// The battle engine: a bit-exact port of the simulation core of Classic dc16.exe (see PORTING.md
// and docs/DC16_BATTLE_ENGINE.md). `Game` owns the game-state Buffer, the map grids, the balance
// tables and the RNG, applies in-game commands and runs `game_tick` (0x419978) so that the
// server can compute the lockstep checksum (sync.c 0x44ABC0) and send 0x08 commands.
//
// Tick order (game_tick 0x419978, read from the disassembly on 11 Sep 2026):
//   1. funky-tower type swap for players with +0xBC0 set (campaign only)
//   2. per-type stats reset, unit counts (stats 6 and per-type stat 1) over objects 152..799
//   3. heroes of players at the unit cap are held at charge 230
//   4. timing sample; research flags for the local player (display only)
//   5. GAME_TIME == 0: clearSeen, vision, minimap
//   6. DN_COUNTER++, GAME_TIME++, UNIT_CAP = unitCap()
//   7. per player: vision mask from the diplomacy matrices; alliance bytes
//   8. day/night phase flip and light level
//   9. path generation stamp += 2 (0x444824)
//  10. TICK % 16 == 0: clearSeen, vision, minimap;  TICK % 8 == 0: stats 10, 0x440100 (generators),
//      0x43E5B0 (triggers), dead heroes removed from the hero table
//  11. TICK % 16 == 0: income for players whose HQ slot is alive
//  12. stat 5 reset, WORD_1948 countdown
//  13. objects 0..MAX_OBJ: loopTrap = 0; allocated -> dispatchObject, else selection mask = 0
//  14. missiles (0x442B50: four update passes, then animation/cleanup)
//  15. record checksum under TICK; (sync sender: done by the server)
//  16. AI turn (0x41AE38) in local-command mode
//  17. timing statistics; TICK % 32 == 0: speed negotiation (network only)
// The pacing loop (0x41E268) increments TICK before calling game_tick and executes the commands
// of a sync frame with `until = u` at TICK == u - 1, before the tick that makes TICK == u.

import { GS, O, P, u8, i16, i32, w8, w16, w32, objAddr, playerAddr, idiv, MAX_OBJECTS, GS_SIZE } from './mem.js';
import * as Grid from './grid.js';
import * as Ticker from './ticker.js';
import * as Missile from './missile.js';
import * as Renat from './renat.js';
import * as Scenario from './scenario.js';
import * as Commands from './commands.js';
import * as Ai from './ai.js';

export const NUM_OBJ_TYPES = 130;
export const NUM_STATS = 12;

export class Game {
  /**
   * @param tables   loaded balance tables (tables.js loadTables)
   * @param consts   data/dc16-tables.json
   * @param opts     { log, assert }
   */
  constructor(tables, consts, opts = {}) {
    this.tables = tables;
    this.consts = consts;
    this.log = opts.log ?? null;
    this.onAssert = opts.assert ?? null;
    this.gs = Buffer.alloc(GS_SIZE);
    this.initGameState();
    this.map = null; // set by Scenario.startGame
    this.scenario = null;
    this.diplo = [new Uint8Array(8), new Uint8Array(8)]; // 0x471C0 / 0x471C4 matrices
    this.stats = new Int32Array(8 * NUM_STATS); // 0x4A5710
    this.typeStats = new Int32Array(8 * NUM_OBJ_TYPES * 4); // 0x4A5890
    this.randTable = consts.rand.values;
    this.randIndex = 0; // 0x489320
    this.globals = { loopTrap: 0, pathGen: 0 }; // 0x488F1C, 0x48AA9C
    this.asserts = [];
    this.started = false;
  }

  /**
   * 0x40C354 (called from the game-state constructor 0x40C470, before the lobby): missile list
   * heads -1, missile count / local player / highest object 0, every player's slot hit points and
   * levels 0, AI type and race 0, every object's life byte and +0x13 0, the four cheat flags 0, the
   * object allocation table -1, every missile's next link -1. The Buffer is zero elsewhere.
   */
  initGameState() {
    const gs = this.gs;
    w16(gs, GS.MISSILE_ACTIVE, -1);
    w16(gs, GS.MISSILE_FREE, -1);
    w32(gs, GS.MISSILE_COUNT, 0);
    w32(gs, GS.LOCAL_PLAYER, 0);
    w32(gs, GS.MAX_OBJ, 0);
    for (let obj = 0; obj < MAX_OBJECTS; obj++) w16(gs, GS.OBJ_ALLOC + obj * 2, -1);
    for (let m = 0; m < 0x7e8; m++) w16(gs, 0x32cc8 + m * 0x28 + 0x14, -1);
  }

  // ---- RNG (mobiles.c 0x4120E0 / 0x4120F0) -----------------------------------------------------

  srand(seed) {
    this.randIndex = seed & 0xff;
  }

  rand() {
    this.randIndex = (this.randIndex + 1) & 0xff;
    return this.randTable[this.randIndex];
  }

  // ---- statistics (results.c 0x41A544 set, 0x41A06C add, 0x41A790 get; 0x41A87C/0x41A154/0x41A634 per type)

  stat(k, p) {
    return this.stats[p * NUM_STATS + k];
  }

  setStat(k, p, v) {
    this.stats[p * NUM_STATS + k] = v | 0;
  }

  addStat(k, p, v) {
    this.stats[p * NUM_STATS + k] = (this.stats[p * NUM_STATS + k] + v) | 0;
  }

  typeStat(k, p, type) {
    return this.typeStats[(p * NUM_OBJ_TYPES + type) * 4 + k];
  }

  setTypeStat(k, p, type, v) {
    this.typeStats[(p * NUM_OBJ_TYPES + type) * 4 + k] = v | 0;
  }

  addTypeStat(k, p, type, v) {
    this.typeStats[(p * NUM_OBJ_TYPES + type) * 4 + k] += v | 0;
  }

  assert(cond, msg) {
    if (cond) return;
    this.asserts.push({ tick: i32(this.gs, GS.TICK), msg });
    if (this.onAssert) this.onAssert(msg, this);
    else if (this.log) this.log.warn('engine assert', { msg, tick: i32(this.gs, GS.TICK) });
  }

  // ---- addresses -------------------------------------------------------------------------------

  obj(i) {
    return objAddr(i);
  }

  player(p) {
    return playerAddr(p);
  }

  /** Object slot allocated? (int16 table at 0x4690C, -1 = free) */
  allocated(obj) {
    return i16(this.gs, GS.OBJ_ALLOC + obj * 2) !== -1;
  }

  get tick() {
    return i32(this.gs, GS.TICK);
  }

  // ---- game start ------------------------------------------------------------------------------

  /**
   * Build the initial state exactly as run_game (0x40122C) does after the lobby: shuffle,
   * player records, scenario objects. `lobby` = { slots: [{ type, race, colour, team, name }] x 8,
   * localSlot, titleDigit }.
   */
  start(mapJson, lobby) {
    Scenario.startGame(this, mapJson, lobby);
    this.started = true;
  }

  // ---- commands --------------------------------------------------------------------------------

  /** Apply one in-game command (raw Buffer with the type byte) at the current tick. */
  applyCommand(raw) {
    Commands.applyCommand(this, raw);
  }

  // ---- one lockstep tick -----------------------------------------------------------------------

  /** The pacing loop's step: TICK++ then game_tick; returns the checksum recorded for the new tick. */
  step() {
    const gs = this.gs;
    w32(gs, GS.TICK, i32(gs, GS.TICK) + 1);
    this.gameTick();
    return this.historyAt(i32(gs, GS.TICK));
  }

  /** game_tick 0x419978 (order in the header comment). */
  gameTick() {
    const gs = this.gs;
    const tick = i32(gs, GS.TICK);

    // 1. campaign funky tower (0x419986): player+0x28 != 0 -> object 15p+5 type 81 becomes 112
    for (let p = 0; p < 8; p++) {
      if (i32(gs, playerAddr(p) + 0x28) !== 0) {
        const a = objAddr(15 * p + 5);
        if (u8(gs, a + O.TYPE) === 0x51) w8(gs, a + O.TYPE, 0x70);
      }
    }
    // 2. statistics: per-type stat 1 reset, stat 6 = 0, then counts over 152..799 (0x4199E4)
    for (let p = 0; p < 8; p++) {
      for (let t = 0; t < NUM_OBJ_TYPES; t++) this.setTypeStat(1, p, t, 0);
      this.setStat(6, p, 0);
    }
    for (let obj = 0x98; obj < MAX_OBJECTS; obj++) {
      if (!this.allocated(obj)) continue;
      const a = objAddr(obj);
      const team = u8(gs, a + O.TEAM);
      if (team < 8) {
        this.addTypeStat(1, team, u8(gs, a + O.TYPE), 1);
        this.addStat(6, team, 1);
      }
    }
    // 3. heroes at the cap are held at charge 230 (0x419A98)
    for (let p = 0; p < 8; p++) {
      if (this.stat(6, p) < i32(gs, GS.UNIT_CAP)) continue;
      for (let k = 0; k < 4; k++) {
        const hero = i16(gs, playerAddr(p) + P.HEROES + 2 * k);
        if (hero !== -1) w8(gs, objAddr(hero) + O.CHARGE, 0xe6);
      }
    }
    // 4. timing sample, research display flags: display only
    // 5. first tick
    if (i32(gs, GS.GAME_TIME) === 0) {
      Grid.clearSeen(this);
      Grid.updateVision(this);
      // minimap 0x43A040: display only
    }
    // 6. counters (0x419B85)
    w32(gs, GS.DN_COUNTER, i32(gs, GS.DN_COUNTER) + 1);
    w32(gs, GS.GAME_TIME, i32(gs, GS.GAME_TIME) + 1);
    w32(gs, GS.UNIT_CAP, Scenario.unitCap(this));
    // 7. vision masks and alliance bytes (0x419BB0)
    for (let p = 0; p < 8; p++) {
      const pa = playerAddr(p);
      let mask = 0;
      for (let q = 0; q < 8; q++) {
        w8(gs, GS.ALLIANCE + p * 10 + q, this.diploGet(0, p, q) ? 1 : 0);
        if (this.diploGet(1, p, q)) mask |= 0x40000000 >> q;
      }
      mask |= 0x40000000 >> p;
      w32(gs, pa + P.VISION, mask);
    }
    // 8. day / night (0x419C7A)
    if (i32(gs, GS.DN_PHASE_LEN) < i32(gs, GS.DN_COUNTER)) {
      w32(gs, GS.DN_COUNTER, 0);
      w32(gs, GS.DN_PHASE, 1 - i32(gs, GS.DN_PHASE));
    }
    if (i32(gs, GS.DN_DAWN_LEN) >= i32(gs, GS.DN_COUNTER)) {
      const q = idiv(i32(gs, GS.DN_COUNTER) << 8, i32(gs, GS.DN_DAWN_LEN));
      w32(gs, GS.DN_LIGHT, i32(gs, GS.DN_PHASE) === 0 ? 0x100 - q : q);
    }
    // 9. path search generation (0x444824)
    this.globals.pathGen += 2;
    // 10. periodic housekeeping (0x419D17)
    if ((tick & 0xf) === 0) {
      Grid.clearSeen(this);
      Grid.updateVision(this);
    }
    if ((tick & 7) === 0) {
      this.setStat(10, 0, 1 - i32(gs, GS.DN_PHASE));
      this.setStat(10, 1, idiv(i32(gs, GS.DN_COUNTER) << 8, i32(gs, GS.DN_PHASE_LEN)));
      Renat.generatorsTick(this); // 0x440100
      Renat.triggersTick(this); // 0x43E5B0
      for (let p = 0; p < 8; p++) {
        for (let k = 0; k < 4; k++) {
          const ha = playerAddr(p) + P.HEROES + 2 * k;
          const hero = i16(gs, ha);
          if (hero === -1) continue;
          const life = u8(gs, objAddr(hero) + O.LIFE);
          if (life === 10 || life === 0) {
            w16(gs, ha, -1);
            this.setStat(11, p, 0);
          }
        }
      }
    }
    // 11. income (0x419E16)
    if ((tick & 0xf) === 0) {
      for (let p = 0; p < 8; p++) {
        const pa = playerAddr(p);
        if (i32(gs, pa + P.SLOT_HP) === 0) continue;
        const income = i32(gs, pa + P.INCOME);
        w32(gs, pa + P.MONEY, i32(gs, pa + P.MONEY) + income);
        this.addStat(1, p, income);
      }
    }
    // 12. (0x419E66)
    for (let p = 0; p < 8; p++) {
      this.setStat(5, p, 0);
      const a = playerAddr(p) + P.WORD_1948;
      const v = i16(gs, a);
      if (v > 1) w16(gs, a, v - 1);
    }
    // 13. objects (0x419EA0)
    const maxObj = i32(gs, GS.MAX_OBJ);
    for (let obj = 0; obj <= maxObj; obj++) {
      this.globals.loopTrap = 0;
      if (this.allocated(obj)) Ticker.dispatchObject(this, obj);
      else w8(gs, objAddr(obj) + O.SELECT, 0);
    }
    // 14. missiles (0x442B50)
    Missile.missilesTick(this);
    // 15. checksum history (0x44AC68)
    this.record(tick);
    // 16. AI (0x41AE38)
    Ai.aiTurn(this);
    // 17. timing statistics and speed negotiation: network/display only
  }

  diploGet(matrix, a, b) {
    // 0x41E970(matrix, a, b): bit b of byte a AND bit a of byte b
    const m = this.diplo[matrix];
    return (m[a] >> b) & 1 && (m[b] >> a) & 1;
  }

  diploSet(matrix, a, b, on) {
    // 0x41E928(matrix, a, b, on)
    const m = this.diplo[matrix];
    if (on === 1) m[a] |= 1 << b;
    else m[a] &= ~(1 << b);
  }

  // ---- sync.c ------------------------------------------------------------------------------------

  /** checksum(gs) 0x44ABC0: plain 16-bit sum, see docs/DC16_BATTLE_ENGINE.md §16. */
  checksum() {
    const gs = this.gs;
    let sum = i16(gs, GS.DN_PHASE_LEN) + i16(gs, GS.DN_DAWN_LEN) + i16(gs, GS.DN_PHASE) + i16(gs, GS.DN_LIGHT);
    sum += i16(gs, GS.TICK) + i16(gs, GS.MAX_OBJ) + i16(gs, GS.MISSILE_COUNT);
    const maxObj = i32(gs, GS.MAX_OBJ);
    for (let obj = 0; obj <= maxObj; obj++) {
      const a = objAddr(obj);
      const life = u8(gs, a + O.LIFE);
      sum += life;
      if (life !== 0) {
        sum += i16(gs, a + O.X) + i16(gs, a + O.Z) + i16(gs, a + O.HP) + u8(gs, a + O.TEAM) + u8(gs, a + O.TYPE);
      }
    }
    return sum & 0xffff;
  }

  /** record 0x44AC68: history[tick % 256] = checksum (stored as int16). */
  record(tick) {
    w16(this.gs, GS.HISTORY + (tick % 256) * 2, this.checksum());
  }

  /** read 0x44AE84: the recorded checksum of `tick` as an unsigned 16-bit value. */
  historyAt(tick) {
    return i16(this.gs, GS.HISTORY + (tick % 256) * 2) & 0xffff;
  }
}

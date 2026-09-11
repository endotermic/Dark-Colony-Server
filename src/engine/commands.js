// commands.js — the in-game command handlers of Classic dc16.exe (handler table 0x48949C, types
// 0x03..0x1B) applied to the game state exactly as a client does when it executes a held frame.
// This is the engine's command APPLICATION; the wire codec (T constants, splitCommands, builders)
// is src/commands.js one directory up.
//
// PORT NOTES
// ==========
// Every handler (gs, &stream, remaining) -> 0 | -1 of the original is ported instruction by
// instruction; `applyCommand(G, raw)` is the body of the frame executor 0x41E06C for ONE command
// (unknown type / NULL table entry -> -1, handler result returned). 0x01 TICK, 0x02 UNTIL and 0x08
// SYNC are the integrator's (no-ops here, return 0).
//
// | type | handler  | state changed |
// | 0x03 | 0x41CF08 | object fields X/HEIGHT/Z/DEST_X/DEST_Z/TYPE/TEAM/HEADING/LIFE/HP/NUDGE/PENDING/ORDER, MAX_OBJ, OBJ_ALLOC |
// | 0x04 | 0x41CE9C | gs->flags[b] toggled (b in 0..3), else -1; `a` unused by the handler (pause is the pacing loop's) |
// | 0x05 | 0x41D018 | obj PENDING=1, ORDER |
// | 0x06 | 0x41D338 | obj DEST_X, DEST_Z |
// | 0x07 | 0x41D574 | WAYPOINTS[0..n), WP_COUNT of each listed object |
// | 0x09 | 0x41CAA4 | player SLOT_HP/SLOT_LEVEL, MONEY (refund) or SPENT, then City.buildSlot |
// | 0x0A | 0x41C9C8 | player QUEUE[class][len..len+count), QUEUE_LEN, SPENT |
// | 0x0B | 0x41D468 | obj +0x32 (target object) |
// | 0x0C | 0x41CBD4 | object_types[type].weaponLevel/armourLevel[player], MONEY or SPENT |
// | 0x0D | 0x41D7AC | diplomacy matrix `which`, bit pb of row pa (G.diploSet) |
// | 0x0E | 0x41DA2C | cheats: MONEY of all players, flags[0]; the message queue is display only |
// | 0x0F | 0x41DBB0 | MONEY[player] += 1000 (see "local player" below) |
// | 0x10 | 0x41DBE0 | player AI_TYPE=3, MONEY -= SPENT, max_speed[player]=0, then a 0x0E message |
// | 0x11 | 0x41DD6C | TICK_MS (ms > 0 else -1) |
// | 0x12 | 0x41DDB4 | max_speed[player] (gs+0x974) |
// | 0x13 | 0x41DE08 | DESIRED_MS |
// | 0x14 | 0x41DEA8 | SELECT bit of the player cleared everywhere, set on the listed objects |
// | 0x15 | 0x41DFE8 | SELECT bit cleared everywhere |
// | 0x16 | 0x41D060 | PENDING/ORDER of selected objects (order 0x12 only if type +0x10C byte != 0) |
// | 0x17 | 0x41D390 | DEST_X/DEST_Z of selected objects |
// | 0x18 | 0x41D4AC | +0x32 of selected objects |
// | 0x19 | 0x41D69C | waypoints of selected objects |
// | 0x1A | 0x41D150 | PENDING=1, ORDER=0x0D on selected objects whose type +0x104 != 0 |
// | 0x1B | 0x41D22C | PENDING=1, ORDER=0x12, WP_COUNT=1, waypoint 0 on selected objects with +0x10C |
//
// No rand() call in any handler.
//
// Local player ("me", gs+0x7D3C): the original checks it in 0x0E (deliver if to_mask has my bit),
// 0x0F (only `player == me` gets the bonus) and after 0x09/0x0C (dep_recompute for my build menu).
// The server engine is nobody: when GS.LOCAL_PLAYER is outside 0..7 (the integrator sets -1),
// 0x0E applies the cheat effects when the mask addresses ANY player (what every addressed client
// does to its copy) and 0x0F credits `player` (what that player's own client does); the
// dep_recompute calls never fire. Money is not part of the checksum, so this only matters for the
// AI's spending decisions.
//
// Disagreements with the docs (the code wins):
//   * DC16_NETWORK_PROTOCOL.md §4.3 0x0C: payload is `u8 which, u8 type, u8 level, u8 player`
//     (handler 0x41CBD4 and sender 0x40C564), not `which, player, value, slot`; cost = 1000*level.
//   * §4.3 0x03: the four int16 after `obj` are x, y(height), z: the 3rd goes to +0x02 (height) and
//     to +0x30, the 4th to +0x04 (z). The doc lists them as x, z, y. MAX_OBJ becomes obj+1 (not obj).
//   * §4.3 / DC16_BATTLE_ENGINE.md §3.2: the target object of 0x0B/0x18 is written to obj+0x32,
//     not +0x30 (+0x30 is DEST_Z, written by 0x06/0x17). mem.js has no name for +0x32 (O_TARGET).
//   * §4.3 0x10: "mark player lost, AI takes over": the handler also zeroes gs+0x974[player] (the
//     0x12 max-speed slot) and generates the chat message THROUGH the 0x0E handler, so the cheat
//     comparison runs on "<name> lost, AI taking over" too. There is no hero handling in it (the
//     dead-hero cleanup is game_tick's).
//   * §4.3 0x09 and DC16_BATTLE_ENGINE.md §15.1: type lookup ignores the race (see city.js notes).
//   * §4.3 0x14: the object id assert is `a <= 800` (inclusive), so object 800 (= missile 0's
//     record) can be selected; reproduced as a plain gs write.
//   * §6.6: money "normalised" = MONEY -= SPENT.
//
// TODO(exact): none. Display-only parts are marked `// display only`.
// TODO(integrator): GS_MAX_SPEED (0x974), O_TARGET (0x32), P_NAME (0x00) belong in mem.js.

import { T } from '../commands.js';
import { GS, O, P, u8, i16, i32, w8, w16, w32, objAddr, playerAddr, MAX_OBJECTS } from './mem.js';
import * as City from './city.js';

// offsets not in mem.js
const GS_MAX_SPEED = 0x974; // int32[8], written by 0x12 and cleared by 0x10
const O_TARGET = 0x32; // i16 target object of the attack order (0x0B / 0x18)
const P_NAME = 0x00; // player name (NUL-terminated) at the start of the player block

const MAX_PLAYERS = 8;

export class CommandError extends Error {}

/** The `&stream` argument of the handlers: a cursor over the raw command (type byte at 0). */
class Stream {
  constructor(raw) {
    this.b = raw;
    this.p = 1;
  }

  get remaining() {
    return this.b.length - this.p;
  }

  need(n) {
    if (this.p + n > this.b.length) throw new CommandError(`command 0x${this.b[0].toString(16)} truncated`);
  }

  u8() {
    this.need(1);
    return this.b[this.p++];
  }

  /** 0x43AD64 returns the 16-bit value zero-extended in eax; callers sign-extend (movsx/cwde). */
  u16() {
    this.need(2);
    const v = this.b.readUInt16LE(this.p);
    this.p += 2;
    return v;
  }

  i16() {
    this.need(2);
    const v = this.b.readInt16LE(this.p);
    this.p += 2;
    return v;
  }

  /** 0x43ADDC */
  i32() {
    this.need(4);
    const v = this.b.readInt32LE(this.p);
    this.p += 4;
    return v;
  }
}

const me = (G) => i32(G.gs, GS.LOCAL_PLAYER);
const isLocal = (G, player) => player === me(G);
/** `mov al,1; shl al,cl`: 8-bit mask, shift count masked to 5 bits by the CPU (player 8 -> 0). */
const pmask = (player) => (1 << (player & 31)) & 0xff;
const selected = (gs, obj, mask) => (u8(gs, objAddr(obj) + O.SELECT) & mask) !== 0;

function assertPlayer(G, player) {
  G.assert(player >= 0 && player <= MAX_PLAYERS, 'player>=0 && player<=MAX_N_PLAYERS');
}

// ---- 0x03 create object 0x41CF08 --------------------------------------------------------------------

function cmdCreate(G, s) {
  const gs = G.gs;
  const obj = s.i16();
  const x = s.i16();
  const y = s.i16(); // height
  const z = s.i16();
  const type = s.u8();
  const owner = s.u8();
  const heading = s.u8();
  const life = s.u8();
  const hp = s.u8();
  const a = objAddr(obj);
  w8(gs, a + O.NUDGE, 0xff);
  w8(gs, a + O.PENDING, 0);
  w8(gs, a + O.ORDER, 0xff);
  w16(gs, a + O.X, x << 5);
  w16(gs, a + O.HEIGHT, y << 5);
  w16(gs, a + O.DEST_X, x << 5);
  w16(gs, a + O.DEST_Z, y << 5);
  w16(gs, a + O.Z, z << 5);
  w8(gs, a + O.TYPE, type);
  w8(gs, a + O.TEAM, owner);
  w8(gs, a + O.HEADING, heading);
  w8(gs, a + O.LIFE, life);
  w32(gs, a + O.HP, hp);
  if (obj > i32(gs, GS.MAX_OBJ)) w32(gs, GS.MAX_OBJ, obj + 1);
  w16(gs, GS.OBJ_ALLOC + obj * 2, obj);
  return 0;
}

// ---- 0x04 cheat flags 0x41CE9C ----------------------------------------------------------------------

function cmdCheat(G, s) {
  const gs = G.gs;
  if (s.remaining < 4) return -1; // 0x421074
  s.i16(); // `a`: the pause codes 1/2 are handled by the pacing loop before the handler runs
  const b = s.i16();
  if (b < 0 || b >= 4) return -1;
  w8(gs, GS.FLAGS + b, u8(gs, GS.FLAGS + b) === 0 ? 1 : 0);
  return 0;
}

// ---- 0x05 order 0x41D018, 0x16 order selected 0x41D060, 0x1A 0x41D150, 0x1B 0x41D22C ----------------

function cmdOrder(G, s) {
  const gs = G.gs;
  const obj = s.i16();
  const order = s.u8();
  const a = objAddr(obj);
  w8(gs, a + O.PENDING, 1);
  w8(gs, a + O.ORDER, order);
  return 0;
}

function cmdOrderSelected(G, s) {
  const gs = G.gs;
  const player = s.u8();
  assertPlayer(G, player);
  const mask = pmask(player);
  const order = s.u8();
  for (let obj = 0; obj < MAX_OBJECTS; obj++) {
    if (!selected(gs, obj, mask)) continue;
    const a = objAddr(obj);
    // order 0x12 (fire at ground / targeted special) only for types with the +0x10C flag
    if (order === 0x12 && !G.tables.types[u8(gs, a + O.TYPE)].hasSpecial) continue;
    w8(gs, a + O.PENDING, 1);
    w8(gs, a + O.ORDER, order);
  }
  return 0;
}

function cmdDeploySelected(G, s) {
  const gs = G.gs;
  const player = s.u8();
  assertPlayer(G, player);
  const mask = pmask(player);
  for (let obj = 0; obj < MAX_OBJECTS; obj++) {
    if (!selected(gs, obj, mask)) continue;
    const a = objAddr(obj);
    if (G.tables.types[u8(gs, a + O.TYPE)].deployCode === 0) continue; // +0x104
    w8(gs, a + O.PENDING, 1);
    w8(gs, a + O.ORDER, 0x0d);
  }
  return 0;
}

function cmdMoveToSelected(G, s) {
  const gs = G.gs;
  const player = s.u8();
  const x = s.i16();
  const z = s.i16();
  assertPlayer(G, player);
  const mask = pmask(player);
  for (let obj = 0; obj < MAX_OBJECTS; obj++) {
    if (!selected(gs, obj, mask)) continue;
    const a = objAddr(obj);
    if (!G.tables.types[u8(gs, a + O.TYPE)].hasSpecial) continue; // +0x10C
    w8(gs, a + O.PENDING, 1);
    w8(gs, a + O.WP_COUNT, 1);
    w8(gs, a + O.ORDER, 0x12);
    w16(gs, a + O.WAYPOINTS + 2, z);
    w16(gs, a + O.WAYPOINTS, x);
  }
  return 0;
}

// ---- 0x06 target position 0x41D338, 0x17 selected 0x41D390 ------------------------------------------

function cmdTargetPos(G, s) {
  const gs = G.gs;
  const obj = s.i16();
  const x = s.i16();
  const z = s.i16();
  const a = objAddr(obj);
  w16(gs, a + O.DEST_X, x);
  w16(gs, a + O.DEST_Z, z);
  return 0;
}

function cmdTargetPosSelected(G, s) {
  const gs = G.gs;
  const player = s.u8();
  assertPlayer(G, player);
  const mask = pmask(player);
  const x = s.i16();
  const z = s.i16();
  for (let obj = 0; obj < MAX_OBJECTS; obj++) {
    if (!selected(gs, obj, mask)) continue;
    const a = objAddr(obj);
    w16(gs, a + O.DEST_Z, z);
    w16(gs, a + O.DEST_X, x);
  }
  return 0;
}

// ---- 0x0B target object 0x41D468, 0x18 selected 0x41D4AC --------------------------------------------

function cmdTargetObj(G, s) {
  const gs = G.gs;
  const obj = s.i16();
  const target = s.i16();
  w16(gs, objAddr(obj) + O_TARGET, target);
  return 0;
}

function cmdTargetObjSelected(G, s) {
  const gs = G.gs;
  const player = s.u8();
  assertPlayer(G, player);
  const target = s.i16();
  const mask = pmask(player);
  for (let obj = 0; obj < MAX_OBJECTS; obj++) {
    if (selected(gs, obj, mask)) w16(gs, objAddr(obj) + O_TARGET, target);
  }
  return 0;
}

// ---- 0x07 waypoints 0x41D574, 0x19 selected 0x41D69C ------------------------------------------------

function readWaypoints(G, s, n) {
  G.assert(n <= 8, 'np <= OBJECT_WAY_SIZE');
  const wp = [];
  for (let i = 0; i < n; i++) wp.push(s.i16(), s.i16());
  return wp;
}

function writeWaypoints(gs, a, wp, n) {
  for (let i = 0; i < n; i++) {
    w16(gs, a + O.WAYPOINTS + 4 * i, wp[2 * i]);
    w16(gs, a + O.WAYPOINTS + 4 * i + 2, wp[2 * i + 1]);
  }
  w8(gs, a + O.WP_COUNT, n);
}

function cmdWaypoints(G, s) {
  const gs = G.gs;
  const n = s.u8();
  const count = s.i16();
  const wp = readWaypoints(G, s, n);
  for (let c = 0; c < count; c++) {
    const obj = s.i16();
    writeWaypoints(gs, objAddr(obj), wp, n);
  }
  return 0;
}

function cmdWaypointsSelected(G, s) {
  const gs = G.gs;
  const n = s.u8();
  const mask = pmask(s.u8()); // no player assert in this handler
  const wp = readWaypoints(G, s, n);
  for (let obj = 0; obj < MAX_OBJECTS; obj++) {
    if (selected(gs, obj, mask)) writeWaypoints(gs, objAddr(obj), wp, n);
  }
  return 0;
}

// ---- 0x09 build building 0x41CAA4 -------------------------------------------------------------------

function cmdBuildBuilding(G, s) {
  const gs = G.gs;
  const slot = s.u8();
  const level = s.u8();
  const player = s.u8();
  const pa = playerAddr(player);
  // 0x444E30(slot, level): row `level` of unitdef, the race is NOT part of this lookup
  const type = City.unitdefRow(G, level, slot);
  const hp = G.tables.types[type].hp;
  if (i32(gs, pa + P.SLOT_HP + slot * 4) === hp && level === i32(gs, pa + P.SLOT_LEVEL + slot * 4)) {
    // already built at that level: refund (race 0 passed to the lookup)
    w32(gs, pa + P.MONEY, i32(gs, pa + P.MONEY) + City.buildingCost(G, slot, level, 0));
    return 0;
  }
  w32(gs, pa + P.SPENT, i32(gs, pa + P.SPENT) + City.buildingCost(G, slot, level, 0));
  w32(gs, pa + P.SLOT_HP + slot * 4, G.tables.types[City.unitdefRow(G, level, slot)].hp);
  w32(gs, pa + P.SLOT_LEVEL + slot * 4, level);
  City.buildSlot(G, player, slot);
  if (isLocal(G, player)) City.depRecompute(G, player); // display only
  return 0;
}

// ---- 0x0A build units 0x41C9C8 ----------------------------------------------------------------------

function cmdBuildUnits(G, s) {
  const gs = G.gs;
  const type = s.u8();
  const player = s.u8();
  const count = s.u8();
  const k = G.tables.types[type].prodClass; // +0xEC
  const pa = playerAddr(player);
  const lenA = pa + P.QUEUE_LEN + 2 * k;
  const len = gs.readUInt16LE(lenA); // `and eax,0FFFFh`
  for (let i = 0; i < count; i++) w8(gs, pa + P.QUEUE + 800 * k + len + i, type);
  w16(gs, lenA, i16(gs, lenA) + count);
  w32(gs, pa + P.SPENT, i32(gs, pa + P.SPENT) + City.troopCost(G, type) * count);
  return 0;
}

// ---- 0x0C setting / upgrade 0x41CBD4 ----------------------------------------------------------------

function cmdSetting(G, s) {
  const gs = G.gs;
  const which = s.u8();
  const type = s.u8();
  const level = s.u8();
  const player = s.u8();
  const cost = level * 1000;
  const pa = playerAddr(player);
  const t = G.tables.types[type];
  const book = (current) => {
    if (level === (current & 0xff)) w32(gs, pa + P.MONEY, i32(gs, pa + P.MONEY) + cost); // same level again: refund
    else w32(gs, pa + P.SPENT, i32(gs, pa + P.SPENT) + cost);
  };
  if (which === 1) {
    book(t.armourLevel[player]); // +0x38
    t.armourLevel[player] = level;
  } else if (which === 0) {
    book(t.weaponLevel[player]); // +0x30
    t.weaponLevel[player] = level;
  }
  if (isLocal(G, player)) City.depRecompute(G, player); // display only
  return 0;
}

// ---- 0x0D diplomacy 0x41D7AC ------------------------------------------------------------------------

function cmdDiplomacy(G, s) {
  const pa = s.u8();
  const pb = s.u8();
  const which = s.u8();
  const on = s.u8();
  G.assert(which >= 0 && which < 2, 'slot>=0 && slot<MAX_MULTI_THINGS');
  G.assert(pa >= 0 && pa < MAX_PLAYERS, 'offering_player>=0 && offering_player<MAX_N_PLAYERS');
  G.assert(pb >= 0 && pb < MAX_PLAYERS, 'offered_to_player>=0 && offered_to_player<MAX_N_PLAYERS');
  if (which > 1) return 0; // the original dereferences a garbage matrix pointer here
  G.diploSet(which, pa, pb, on !== 0 ? 1 : 0); // 0x41E928
  return 0;
}

// ---- 0x0E chat / cheats 0x41DA2C --------------------------------------------------------------------

const CHEAT_MONEY = 'we need equipment'; // 0x483B84
const CHEAT_BROKE = "I'm fighting for that equipment"; // 0x483B98
const CHEAT_FLAG = 'slag net'; // 0x483BB8

/** Is a message with `mask` delivered on this machine? (server: any addressed player) */
function deliveredHere(G, mask) {
  const m = me(G);
  if (m >= 0 && m < MAX_PLAYERS) return (mask & (1 << m)) !== 0;
  return (mask & 0xff) !== 0;
}

/** The body of 0x41DA2C after the payload parse: 0 or -1. */
function chatMessage(G, from, mask, text) {
  const gs = G.gs;
  if (from < 0 || from >= MAX_PLAYERS) return -1;
  if (!deliveredHere(G, mask)) return 0;
  // message queue gs+0x310/0x314/0x318 (6 entries, 0x41D950 scrolls): display only
  // The original scans forward for ':' WITHOUT a bound (a text without one runs past the buffer);
  // here no ':' means no cheat.
  const colon = text.indexOf(':');
  if (colon >= 0) {
    const code = text.slice(colon + 2); // skips ": "
    if (code === CHEAT_MONEY) {
      for (let p = 0; p < MAX_PLAYERS; p++) {
        const ma = playerAddr(p) + P.MONEY;
        w32(gs, ma, i32(gs, ma) + 10000);
      }
    }
    if (code === CHEAT_BROKE) {
      for (let p = 0; p < MAX_PLAYERS; p++) w32(gs, playerAddr(p) + P.MONEY, 0);
    }
    if (code === CHEAT_FLAG) w8(gs, GS.FLAGS, u8(gs, GS.FLAGS) === 0 ? 1 : 0);
  }
  return 0;
}

function cmdChat(G, s) {
  const from = s.u8();
  const mask = s.u8();
  // walk the text: unterminated within the remaining bytes -> -1
  const end = s.b.indexOf(0, s.p);
  if (end < 0) return -1;
  const text = s.b.toString('latin1', s.p, end);
  s.p = end + 1;
  return chatMessage(G, from, mask, text);
}

// ---- 0x0F bonus 0x41DBB0 ----------------------------------------------------------------------------

function cmdBonus(G, s) {
  const gs = G.gs;
  const player = s.u8();
  const m = me(G);
  // original: `if (player == me) money[me] += 1000`; the server mirrors that player's own client
  if (player === m || !(m >= 0 && m < MAX_PLAYERS)) {
    const ma = playerAddr(player) + P.MONEY;
    w32(gs, ma, i32(gs, ma) + 1000);
  }
  return 0;
}

// ---- 0x10 disconnect 0x41DBE0 -----------------------------------------------------------------------

function playerName(gs, pa) {
  const end = gs.indexOf(0, pa + P_NAME);
  return gs.toString('latin1', pa + P_NAME, Math.min(end < 0 ? pa + P.MONEY : end, pa + P.MONEY));
}

function cmdDisconnect(G, s) {
  const gs = G.gs;
  const slot = s.u8();
  let player = -1;
  for (let p = 0; p < MAX_PLAYERS; p++) {
    if (i32(gs, playerAddr(p) + P.NET_ID) === slot) {
      player = p;
      break;
    }
  }
  G.assert(player !== -1, 'lost_player!=-1');
  if (player === -1) return -1; // the original goes on with player -1 (writes before the player array)
  const pa = playerAddr(player);
  w32(gs, pa + P.AI_TYPE, 3);
  w32(gs, pa + P.MONEY, i32(gs, pa + P.MONEY) - i32(gs, pa + P.SPENT));
  w32(gs, GS_MAX_SPEED + player * 4, 0);
  // t2 = { player, 0xFF, sprintf("%s lost, AI taking over", name) }; message(gs, &t2, 128)
  const text = `${playerName(gs, pa)} lost, AI taking over`.slice(0, 0x7f);
  const r = chatMessage(G, player, 0xff, text);
  G.assert(r === 0, 'message(gs, &t2, 128)==0');
  return 0;
}

// ---- 0x11 / 0x12 / 0x13 speeds 0x41DD6C / 0x41DDB4 / 0x41DE08 ---------------------------------------

function cmdTickSpeed(G, s) {
  if (s.remaining < 4) return -1;
  const ms = s.i32();
  if (ms <= 0) return -1;
  w32(G.gs, GS.TICK_MS, ms);
  return 0;
}

function cmdTickMaxSpeed(G, s) {
  if (s.remaining < 5) return -1;
  const player = s.u8();
  const ms = s.i32();
  if (ms <= 0) return -1;
  w32(G.gs, GS_MAX_SPEED + player * 4, ms);
  return 0;
}

function cmdTickDesSpeed(G, s) {
  if (s.remaining < 4) return -1;
  const ms = s.i32();
  if (ms <= 0) return -1;
  w32(G.gs, GS.DESIRED_MS, ms);
  return 0;
}

// ---- 0x14 select 0x41DEA8, 0x15 deselect 0x41DFE8 ---------------------------------------------------

/** 0x41DE50(gs, player): clear the player's selection bit on all 800 objects. */
export function clearSelection(G, player) {
  const gs = G.gs;
  const keep = pmask(player) ^ 0xff;
  for (let obj = 0; obj < MAX_OBJECTS; obj++) {
    const a = objAddr(obj) + O.SELECT;
    w8(gs, a, u8(gs, a) & keep);
  }
}

function cmdSelect(G, s) {
  const gs = G.gs;
  const player = s.u8();
  assertPlayer(G, player);
  const mask = pmask(player);
  clearSelection(G, player);
  for (;;) {
    const v = s.u16();
    if (v === 0xffff) break;
    G.assert(v <= MAX_OBJECTS, 'a<=MAX_OBJECTS');
    const a = objAddr(v) + O.SELECT; // v == 800 hits missile 0's record, as in the original
    w8(gs, a, u8(gs, a) | mask);
  }
  return 0;
}

function cmdDeselect(G, s) {
  const player = s.u8();
  assertPlayer(G, player);
  clearSelection(G, player);
  return 0;
}

// ---- dispatch --------------------------------------------------------------------------------------

const noop = () => 0;

/** Handler table 0x48949C (0x1C entries; NULL entries are `undefined`). */
export const HANDLERS = Object.freeze({
  [T.TICK]: noop, // 0x41CCFC: the integrator's (pacing)
  [T.UNTIL]: noop, // 0x41CD34: the integrator's
  [T.CREATE]: cmdCreate,
  [T.CHEAT]: cmdCheat,
  [T.ORDER]: cmdOrder,
  [T.TARGET_POS]: cmdTargetPos,
  [T.WAYPOINTS_OBJ]: cmdWaypoints,
  [T.SYNC]: noop, // 0x41CE74: the integrator's
  [T.RESEARCH]: cmdBuildBuilding,
  [T.BUILD]: cmdBuildUnits,
  [T.TARGET_OBJ]: cmdTargetObj,
  [T.SETTING]: cmdSetting,
  [T.DIPLOMACY]: cmdDiplomacy,
  [T.CHAT]: cmdChat,
  [T.BONUS]: cmdBonus,
  [T.DISCONNECT]: cmdDisconnect,
  [T.TICK_SPEED]: cmdTickSpeed,
  [T.TICK_MAXSPEED]: cmdTickMaxSpeed,
  [T.TICK_DESSPEED]: cmdTickDesSpeed,
  [T.SELECT]: cmdSelect,
  [T.DESELECT]: cmdDeselect,
  [T.ORDER_SEL]: cmdOrderSelected,
  [T.TARGET_POS_SEL]: cmdTargetPosSelected,
  [T.TARGET_OBJ_SEL]: cmdTargetObjSelected,
  [T.WAYPOINTS_SEL]: cmdWaypointsSelected,
  [T.ORDER0D_SEL]: cmdDeploySelected,
  [T.MOVETO_SEL]: cmdMoveToSelected,
});

/**
 * Apply one raw command (type byte first) to the state: the per-command part of the frame executor
 * 0x41E06C. Returns the handler's result (0, or -1 = the original aborts the frame).
 */
export function applyCommand(G, raw) {
  const type = raw[0];
  const handler = type < 0x1c ? HANDLERS[type] : undefined;
  if (!handler) return -1;
  return handler(G, new Stream(raw));
}

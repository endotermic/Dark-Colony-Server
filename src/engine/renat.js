// renat.c / trigger.c / research.c of Classic dc16.exe: creature generators (wildlife), research
// ("artifact") sites, vents, the trigger script and the state handlers 0xC, 0xF, 0x10, 0x15.
//
// PORT NOTES (read from dc16.asm, 11 Sep 2026; every function below names its VA)
//
// Ported instruction by instruction:
//   * renat.c: 0x43FFB0 registerGenerator, 0x4400C0 generatorCreatureTotal, 0x4400F0 resetGenerators,
//     0x440100 generatorsTick (initial spawn, 1/4 wander order per living creature, 1/128 respawn of
//     ONE dead creature per tick — the function returns after the first respawn), 0x41B818
//     spawnCreature (mobiles.c: free tile via 0x41B68C except for vents, LAST free object below
//     MAX_OBJ, else MAX_OBJ++ with assert, init_object(..., -1, 0, index)).
//   * research.c ("artifact sites" in the docs): 0x440640 resetResearch, 0x440650
//     registerResearchSite, 0x4406F8 addResearchItem, 0x440758 popResearchItem. A site at (x, z)
//     holds up to 10 object TYPES; the scenario loader puts every object line that shares a site's
//     tile into that list instead of creating it; a type 6 / 0xE unit standing on the site pulls
//     them out one every 450 ticks (idleArtifactSite 0x4134D4).
//   * trigger.c: 0x43FD1C loadTriggers (from the map JSON `script[]`, same grammar as the .TRO
//     parser 0x43E6E0/0x43E738), 0x43C674 compileCondition (recursive descent to the 23-opcode
//     stack bytecode), 0x43CFE8 evalCondition (int16 stack machine), 0x43E5B0 triggersTick,
//     0x43E610 triggerEnter (trip triggers, called by move.js when a unit steps on a trigger
//     tile), 0x43D904 runActions with every action of the table 0x43D8A8 (see runActions for the
//     per-action notes), 0x4191E0 reinforce, 0x43D850 the waypoint action helper.
//   * ticker.c / mobiles.c states: 0x413A88 stateHarvest (0xC), 0x4137A0 idleVent, 0x4134D4
//     idleArtifactSite, 0x416DC0 stateF (0xF: object pulled towards a point — the abduction
//     beam / vortex effect), 0x4177AC state10 (0x10: bouncing object that crushes what is under
//     it — artifact effect), 0x418CE0 state15 (0x15: reinforcement drop pod unloading).
//   * states 0x13 / 0x14 / 0x16 (0x418A7C, 0x418978, 0x418650) and their helpers 0x41879C /
//     0x418580 were ported by city.js (stateConstruct, statePodLand, statePodFlight, spawnPod,
//     pushPodFlight) before this module existed; state13/14/16 here delegate to them so that the
//     ticker's STATE_TABLE keeps its Renat.* names. pickup (0x4143D4) delegates to City.pickupsTick.
//
// TODO(exact) stubs: none of the simulation code is stubbed. `die` asserts in the original too
// (trigger.c line 754 `assert(0)`), `msg` and the sound/message calls are display only.
// Ai.triggerAiMsg receives the raw message words (its final shape is ai.js's business).
//
// Disagreements with the docs (the code wins):
//   * DC16_MAP_FILES.md §8: the third header field is not a "flag" but `lifes`, the number of
//     times the trigger may fire (byte at trigger+8, assert `lifes>=0`); 0 = disarmed until a
//     `setlifes` action arms it. Actions are linked LIFO while parsing, so they RUN IN REVERSE
//     FILE ORDER (`setlifes` runs before `setmoney` before `newrate2`).
//   * §8 condition grammar: primitives are `c` (game time >> 4), `r` (rand() as int16), `t`
//     (type of the tripping unit), `S` (its team), `u(k)` (capture counter 0x51D2AC[k]),
//     `b(p,k)` (low word of player p's slot-k hit points), `s(p,k)` (stat k of player p),
//     `s(p,k,type)` (per-type stat), `m(x,z)` (vent alive bit 26 of the load cell), `v(x,z,q)`
//     (cell seen by player q). Operators `+ - * / % < > == != && ||` — but `+`/`-` bind TIGHTER
//     than `*`/`/`/`%` (2*3+4 = 14), each level accepts a single operator without parentheses
//     (`1+2+3` is a parse error), `=` alone is equality, `&&`/`||` are bitwise AND/OR of int16
//     values and `!x` is read as `!=`. All arithmetic is int16 with 32-bit intermediates.
//   * §6.2 / BATTLE_ENGINE §15.2 call type 37 an "artifact site" registered by 0x440650 and
//     `0x4406F8` an "artifact tile" test: the file is research.c (assert strings), 0x4406F8
//     APPENDS the object type to the site's item list and returns whether a site exists there.
//   * BATTLE_ENGINE §4.2 guesses for states 0, 0xF, 0x10, 0x13-0x16 ("artifact pickup, stealing
//     commando, worker/vent handling"): state 0 is a no-op, 0xF/0x10 are artifact effects,
//     0x13-0x16 are the building drop pod and reinforcement pod. The "harvesting" state 0xC
//     transfers the vent's RATE (obj+0x32) every 16th day/night counter value, not its HP.
//   * §15.2: mining income for AI players is rate * player+0x19BC / 256 (idiv), the vent loses
//     the multiplied amount too; a partner unit (type 0x4D/0x4E, info[2]) takes half.
//   * PORTING.md names 0x4400C0 "artifactCount": it sums the generator counts (used by the unit
//     cap 0x41E7FC).
//   * 0x41B930 create_object and 0x41B818 pick the LAST free object index below MAX_OBJ, not the
//     first (§15.2 says "first free object from 152").
//   * mem.js P.SLOT_BLOCKED is written as byte[15] by 0x4184C4 / 0x418A7C (city.js does the same).
//
// Offsets used here that mem.js does not name (original gs / record offsets in the comments).

import {
  GS,
  O,
  P,
  u8,
  i8,
  i16,
  u16,
  i32,
  w8,
  w16,
  w32,
  objAddr,
  playerAddr,
  idiv,
  irem,
  sx16,
  MAX_OBJECTS,
} from './mem.js';
import * as Ticker from './ticker.js';
import * as Move from './move.js';
import * as Anim from './anim.js';
import * as Combat from './combat.js';
import * as Grid from './grid.js';
import * as Scenario from './scenario.js';
import * as Missile from './missile.js';
import * as City from './city.js';
import * as Ai from './ai.js';

// ---- offsets not in mem.js ----------------------------------------------------------------------

const GS_NOUNDEPLOY = 0x948; // byte, set by the `noundeploy` action (0x43C5C0), read by state 0xC
const GS_BAIL = 0x471c9; // byte, `bail` action: mission over
const GS_BAIL_TIME = 0x471cc; // int32, timeGetTime() + 10000 (UI countdown)
const O_RATE = 0x32; // i16, vent: petra per harvest step (also move.js O_TARGET for units)
const O_SOUND = 0xd1; // i8, research site: sound handle (-1 none); display only
const P_NOPICKUP = 0x1c; // gs+0xBB4: `nopickup` action
const P_FUNKY = 0x28; // gs+0xBC0: `funkytower` action (engine.js step 1 reads it)
const P_REINF = 0xe17; // gs+0x19AF byte[8]: reinforcement pod slots (objects team*15+7..14)
const ALIVE_MINE = 1 << 26; // load cell bit 26: a living vent is here
const EMPTY = 0x3ff;
const RESERVED = 0x3fe;

const RNAT_MAX_SOURCES = 25;
const RNAT_MAX_NUMBER = 10;
const MAX_RESEARCH = 10;
const NUM_TRIGGERS = 128; // 0x51AEA8 .. 0x51B6A8, 16 bytes each
const MAX_POOL = 0x1f70; // 0x518F38 .. 0x51AEA8
const NUM_CAPTURES = 8; // 0x51D2AC[0..7]

const T = (G, type) => G.tables.types[type];

// ---- module state (DGROUP / .bss globals of renat.c, research.c, trigger.c) ------------------------

function newGenerator() {
  // 40-byte record at 0x51D2CC + i*40
  return { init: 0, x: 0, z: 0, type: 0, count: 0, ids: new Int16Array(RNAT_MAX_NUMBER) };
  // +0 byte initial spawn done, +4 x, +8 z, +0xC type, +0x10 count, +0x14 int16[10] object ids
}

function newSite() {
  // 52-byte record at 0x51D6B4 + i*52: +0 x, +4 z, +8 n, +0xC int32[10] item types
  return { x: 0, z: 0, n: 0, items: new Int32Array(10) };
}

function newTrigger() {
  // 16-byte record at 0x51AEA8 + i*16: +0 type (0 norm, 1 trip), +4 condition ptr, +8 lives
  // (byte), +0xC first action ptr
  return { type: 0, cond: null, lives: 0, actions: null };
}

/** The renat/trigger globals of a game (created on first use). */
export function renatState(G) {
  if (!G.renat) {
    G.renat = {
      genCount: 0, // 0x4897CC num_registered
      gens: Array.from({ length: RNAT_MAX_SOURCES }, newGenerator),
      siteCount: 0, // 0x4897D0 num_research
      sites: Array.from({ length: MAX_RESEARCH }, newSite),
      triggers: Array.from({ length: NUM_TRIGGERS }, newTrigger),
      actionCount: 0, // 0x51B6A8
      poolUsed: 0, // compiled condition bytes (pool 0x518F38, MAX_POOL)
    };
  }
  return G.renat;
}

/** 0x51D2AC[0..7]: capture counters (`u(k)` in conditions, ++ by the pickup code in city.js). */
function captures(G) {
  if (!G.globals.captures) G.globals.captures = new Array(NUM_CAPTURES).fill(0);
  return G.globals.captures;
}

// ================================================================================================
// renat.c — creature generators
// ================================================================================================

/** 0x4400F0: forget every generator (scenario loader, before the object lines). */
export function resetGenerators(G) {
  renatState(G).genCount = 0;
}

/** 0x43FFB0(x, z, type, count): object line with player == -1. */
export function registerGenerator(G, x, z, type, count) {
  const R = renatState(G);
  G.assert(count < RNAT_MAX_NUMBER, 'number<RNAT_MAX_NUMBER'); // renat.c:33
  G.assert(R.genCount < RNAT_MAX_SOURCES, 'num_registered<RNAT_MAX_SOURCES'); // renat.c:34
  if (!R.gens[R.genCount]) R.gens[R.genCount] = newGenerator(); // the original writes past the table
  const g = R.gens[R.genCount];
  g.x = x;
  g.z = z;
  g.type = type;
  g.init = 0;
  g.count = count;
  R.genCount++;
}

/** 0x4400C0: sum of the generator counts (the unit cap 0x41E7FC subtracts it from the pool). */
export function generatorCreatureTotal(G) {
  const R = renatState(G);
  let n = 0;
  for (let i = 0; i < R.genCount; i++) n += R.gens[i].count;
  return n;
}

/**
 * 0x41B818(gs, x, z, type, team): create a creature / reinforcement at the free tile nearest to
 * (x, z) (vents are placed exactly). Uses the LAST free object index below MAX_OBJ, else grows
 * MAX_OBJ (assert < 800). Returns the object index.
 */
export function spawnCreature(G, x, z, type, team) {
  const gs = G.gs;
  let px = x;
  let pz = z;
  if (type !== 0x28) {
    const fly = (T(G, type).fly << 24) >> 24; // mov eax,[type+0x5D]; sar eax,18h: signed byte +0x60
    const [fx, fz] = Scenario.findFreeCell(G, x, z, fly); // 0x41B68C(gs, x, z, &px, &pz, fly)
    const p = { x: fx, z: fz };
    px = p.x;
    pz = p.z;
  }
  let free = -1;
  const maxObj = i32(gs, GS.MAX_OBJ);
  for (let i = 0x98; i < maxObj; i++) {
    if (u8(gs, objAddr(i) + O.LIFE) === 0) free = i;
  }
  if (free === -1) {
    free = maxObj;
    w32(gs, GS.MAX_OBJ, maxObj + 1);
    G.assert(maxObj + 1 < MAX_OBJECTS, 'gs->number_of_objects < MAX_OBJECTS'); // mobiles.c:291
  }
  Scenario.initObject(G, px, pz, type, team, -1, 0, free); // 0x41B124
  return free;
}

/**
 * 0x440100(gs): every 8th tick (game_tick step 10). Per generator: first call spawns `count`
 * creatures (team 9); then for every creature id: dead -> forget it; alive team-9 creature ->
 * rand() #1: 1/4 chance of a wander order to a random tile within +-7 (rand() pairs until the
 * tile is inside the map; order 7 = assault move, one waypoint); after the ids: rand() #2:
 * 1/128 chance to respawn the FIRST forgotten creature — and return from the whole function.
 */
export function generatorsTick(G) {
  const gs = G.gs;
  const R = renatState(G);
  const { w, h } = G.map;
  G.assert(R.genCount <= RNAT_MAX_SOURCES, 'num_registered<=RNAT_MAX_SOURCES'); // renat.c:64
  for (let gi = 0; gi < R.genCount; gi++) {
    const g = R.gens[gi];
    if (g.init === 0) {
      for (let k = 0; k < g.count; k++) g.ids[k] = spawnCreature(G, g.x, g.z, g.type, 9);
      g.init = 1;
    }
    for (let k = 0; k < g.count; k++) {
      const obj = g.ids[k];
      if (obj === -1) continue;
      const a = objAddr(obj);
      const life = u8(gs, a + O.LIFE);
      if (life === 0 || life === 10) {
        g.ids[k] = -1;
        continue;
      }
      if (u8(gs, a + O.TEAM) !== 9) continue;
      if ((G.rand() & 3) !== 0) continue;
      let x;
      let z;
      do {
        x = g.x + ((G.rand() & 0x0f) - 7);
        z = g.z + ((G.rand() & 0x0f) - 7);
      } while (x < 0 || z < 0 || x >= w || z >= h);
      w8(gs, a + O.WP_COUNT, 1);
      w8(gs, a + O.PENDING, 1);
      w16(gs, a + O.WAYPOINTS, x << 8);
      w8(gs, a + O.ORDER, 7);
      w16(gs, a + O.WAYPOINTS + 2, z << 8);
    }
    if ((G.rand() & 0x7f) === 0) {
      for (let k = 0; k < g.count; k++) {
        if (g.ids[k] === -1) {
          g.ids[k] = spawnCreature(G, g.x, g.z, g.type, 9);
          return; // 0x4403B6: jmp to the epilogue
        }
      }
    }
  }
}

// ================================================================================================
// research.c — "artifact" sites (type 37 objects with a list of object types to hand out)
// ================================================================================================

/** 0x440640: forget every site (scenario loader). */
export function resetResearch(G) {
  renatState(G).siteCount = 0;
}

/** 0x440650(x, z): a type-37 object line registers a site on its tile. */
export function registerResearchSite(G, x, z) {
  const R = renatState(G);
  G.assert(R.siteCount < MAX_RESEARCH, 'num_research<MAX_RESEARCH'); // research.c:33
  if (!R.sites[R.siteCount]) R.sites[R.siteCount] = newSite();
  const s = R.sites[R.siteCount];
  s.x = x;
  s.z = z;
  s.n = 0;
  R.siteCount++;
}

/**
 * 0x4406F8(x, z, item): if a site sits on (x, z), append `item` (an object type) to its list and
 * return true; false otherwise. The list has no bound check in the original (10 slots).
 */
export function addResearchItem(G, x, z, item) {
  const R = renatState(G);
  for (let i = 0; i < R.siteCount; i++) {
    const s = R.sites[i];
    if (s.x !== x || s.z !== z) continue;
    if (s.n >= s.items.length) {
      const grown = new Int32Array(s.n + 1);
      grown.set(s.items);
      s.items = grown;
    }
    s.items[s.n] = item;
    s.n++;
    return true;
  }
  return false;
}

/** 0x440758(x, z): take the first item of the site at (x, z); -1 when empty or no site (assert). */
export function popResearchItem(G, x, z) {
  const R = renatState(G);
  for (let i = 0; i < R.siteCount; i++) {
    const s = R.sites[i];
    if (s.x !== x || s.z !== z) continue;
    if (s.n === 0) return -1;
    const item = s.items[0];
    s.n--;
    for (let k = 0; k < s.n; k++) s.items[k] = s.items[k + 1];
    return item;
  }
  G.assert(false, '0 (research.c:71: no research site at the tile)');
  return -1;
}

// ================================================================================================
// trigger.c — condition compiler (0x43C674) and evaluator (0x43CFE8)
// ================================================================================================

// Opcodes of the condition bytecode (jump table 0x43CF8C, handlers in evalCondition).
const OP_TYPE = 0x00; // t: type of the tripping unit
const OP_TEAM = 0x01; // S: team of the tripping unit
const OP_OR = 0x02; // ||  (bitwise)
const OP_AND = 0x03; // &&  (bitwise)
const OP_EQ = 0x04;
const OP_LT = 0x05;
const OP_GT = 0x06;
const OP_CONST = 0x07; // followed by lo, hi
const OP_CLOCK = 0x08; // c
const OP_MUL = 0x09;
const OP_DIV = 0x0a;
const OP_ADD = 0x0b;
const OP_SUB = 0x0c;
const OP_ARRAY = 0x0d; // u(k)
const OP_SLOT = 0x0e; // b(p, k)
const OP_SEEN = 0x0f; // v(x, z, q)
const OP_STAT = 0x10; // s(p, k)
const OP_TSTAT = 0x11; // s(p, k, type)
const OP_RAND = 0x12; // r
const OP_MOD = 0x13;
const OP_NE = 0x14;
const OP_MINE = 0x15; // m(x, z)
const OP_END = 0x16;

const CH = (c) => c.charCodeAt(0);

/** Character cursor of the compiler: 0x43C5DC next (skips ' '), 0x43C5F4 unget, 0x43C608 error. */
class Src {
  constructor(G, s) {
    this.G = G;
    this.s = s;
    this.i = 0;
  }

  next() {
    for (;;) {
      const c = this.i < this.s.length ? this.s.charCodeAt(this.i) : 0;
      this.i++;
      if (c !== 0x20) return c;
    }
  }

  unget() {
    for (;;) {
      this.i--;
      const c = this.i >= 0 && this.i < this.s.length ? this.s.charCodeAt(this.i) : 0;
      if (c !== 0x20) return;
    }
  }

  error() {
    // printf(" the remaining crap:\n%s\n", ptr); assert(0) — parsing continues afterwards
    this.G.assert(false, `trigger.c:176 parse error, the remaining crap: ${this.s.slice(Math.max(0, this.i))}`);
  }
}

/** 0x43C674(&pool, &str): compile one condition; returns the bytecode (Uint8Array). */
export function compileCondition(G, text) {
  const src = new Src(G, String(text));
  const code = [];
  const emit = (b) => code.push(b & 0xff);

  // 0x43CAF8 primary
  const primary = () => {
    const c = src.next();
    if (c >= CH('0') && c <= CH('9')) {
      // 0x43CF42 number
      emit(OP_CONST);
      let v = c - CH('0');
      for (;;) {
        const d = src.next();
        if (d >= CH('0') && d <= CH('9')) v = (v * 10 + (d - CH('0'))) | 0;
        else break;
      }
      src.unget();
      emit(v);
      emit(sx16(v) >> 8);
      return;
    }
    switch (c) {
      case CH('('):
        subexpr();
        if (src.next() !== CH(')')) src.error();
        return;
      case CH('m'):
        args(2);
        emit(OP_MINE);
        return;
      case CH('t'):
        emit(OP_TYPE);
        return;
      case CH('S'):
        emit(OP_TEAM);
        return;
      case CH('c'):
        emit(OP_CLOCK);
        return;
      case CH('r'):
        emit(OP_RAND);
        return;
      case CH('u'):
        args(1);
        emit(OP_ARRAY);
        return;
      case CH('b'):
        args(2);
        emit(OP_SLOT);
        return;
      case CH('s'): {
        // 0x43CD75: s(p,k) or s(p,k,type)
        if (src.next() !== CH('(')) src.error();
        subexpr();
        if (src.next() !== CH(',')) src.error();
        subexpr();
        const d = src.next();
        if (d === CH(')')) {
          emit(OP_STAT);
          return;
        }
        if (d !== CH(',')) src.error();
        subexpr();
        if (src.next() !== CH(')')) src.error();
        emit(OP_TSTAT);
        return;
      }
      case CH('v'):
        args(3);
        emit(OP_SEEN);
        return;
      default:
        // every other character (including the end of the string) produces nothing
        return;
    }
  };

  // `(` expr {`,` expr} `)` of the m/u/b/v primitives
  const args = (n) => {
    if (src.next() !== CH('(')) src.error();
    for (let k = 0; k < n; k++) {
      if (k > 0 && src.next() !== CH(',')) src.error();
      subexpr();
    }
    if (src.next() !== CH(')')) src.error();
  };

  // 0x43CA44 additive tail: ONE `+` or `-` followed by a primary
  const addTail = () => {
    const c = src.next();
    if (c === CH('+')) {
      primary();
      emit(OP_ADD);
    } else if (c === CH('-')) {
      primary();
      emit(OP_SUB);
    } else if (
      c === 0 ||
      c === CH('!') ||
      c === CH('%') ||
      c === CH('&') ||
      c === CH(')') ||
      c === CH('*') ||
      c === CH(',') ||
      c === CH('/') ||
      c === CH('<') ||
      c === CH('=') ||
      c === CH('>') ||
      c === CH('|')
    ) {
      src.unget();
    } else src.error();
  };

  // 0x43CA28: primary + additive tail
  const additive = () => {
    primary();
    addTail();
  };

  // 0x43C938 multiplicative tail: ONE `*`, `/` or `%` followed by an additive expression
  const mulTail = () => {
    const c = src.next();
    if (c === CH('*') || c === CH('/') || c === CH('%')) {
      additive();
      emit(c === CH('*') ? OP_MUL : c === CH('/') ? OP_DIV : OP_MOD);
    } else if (
      c === 0 ||
      c === CH('!') ||
      c === CH('&') ||
      c === CH(')') ||
      c === CH(',') ||
      c === CH('<') ||
      c === CH('=') ||
      c === CH('>') ||
      c === CH('|')
    ) {
      src.unget();
    } else src.error();
  };

  // 0x43C7E0 comparison tail: ONE `<`, `>`, `=`/`==` or `!=` (any char after `!`)
  const cmpTail = () => {
    const c = src.next();
    if (c === CH('<') || c === CH('>') || c === CH('=') || c === CH('!')) {
      if (c === CH('=')) {
        if (src.next() !== CH('=')) src.unget();
      } else if (c === CH('!')) {
        src.next(); // the second character is consumed unchecked
      }
      additive();
      mulTail();
      emit(c === CH('<') ? OP_LT : c === CH('>') ? OP_GT : c === CH('=') ? OP_EQ : OP_NE);
    } else if (c === 0 || c === CH('&') || c === CH(')') || c === CH(',') || c === CH('|')) {
      src.unget();
    } else src.error();
  };

  // 0x43C6F4 logical tail: `||` or `&&` (second char unchecked) followed by a full expression
  const logTail = () => {
    const c = src.next();
    if (c === CH('|') || c === CH('&')) {
      src.next();
      subexpr();
      emit(c === CH('|') ? OP_OR : OP_AND);
    } else if (c === 0 || c === CH(')') || c === CH(',')) {
      src.unget();
    } else src.error();
  };

  // one full expression: additive, then the three tails
  const subexpr = () => {
    additive();
    mulTail();
    cmpTail();
    logTail();
  };

  subexpr();
  if (src.next() !== 0) src.error(); // trailing characters
  emit(OP_END);
  return Uint8Array.from(code);
}

/**
 * 0x43CFE8(gs, code, unit): run the bytecode on an int16 stack; `unit` is the tripping object
 * (-1 for norm triggers and actions). Returns the single remaining value (assert), sign-extended.
 */
export function evalCondition(G, code, unit) {
  const gs = G.gs;
  G.assert(code != null, 'trigger condition missing (null condition pointer)');
  if (code == null) return 0;
  const st = new Int16Array(0x112);
  st[0] = -1; // guard word at stack-2
  let sp = 1;
  const pop = () => st[--sp];
  const push = (v) => {
    st[sp++] = v;
  };
  const { w, h } = G.map;
  let pc = 0;
  for (;;) {
    const op = code[pc++];
    switch (op) {
      case OP_TYPE:
        G.assert(unit !== -1, 'unit!=-1'); // trigger.c:579
        push(u8(gs, objAddr(unit) + O.TYPE));
        break;
      case OP_TEAM:
        G.assert(unit !== -1, 'unit!=-1'); // trigger.c:583
        push(u8(gs, objAddr(unit) + O.TEAM));
        break;
      case OP_OR: {
        const b = pop();
        const a = pop();
        push(a | b);
        break;
      }
      case OP_AND: {
        const b = pop();
        const a = pop();
        push(a & b);
        break;
      }
      case OP_EQ: {
        const b = pop();
        const a = pop();
        push(a === b ? 1 : 0);
        break;
      }
      case OP_NE: {
        const b = pop();
        const a = pop();
        push(a !== b ? 1 : 0);
        break;
      }
      case OP_LT: {
        const b = pop();
        const a = pop();
        push(a < b ? 1 : 0);
        break;
      }
      case OP_GT: {
        const b = pop();
        const a = pop();
        push(a > b ? 1 : 0);
        break;
      }
      case OP_CONST: {
        const v = code[pc] + (code[pc + 1] << 8);
        pc += 2;
        push(v);
        break;
      }
      case OP_CLOCK:
        push(i32(gs, GS.GAME_TIME) >> 4);
        break;
      case OP_RAND:
        push(G.rand());
        break;
      case OP_MUL: {
        const b = pop();
        const a = pop();
        push(a * b);
        break;
      }
      case OP_DIV: {
        const b = pop();
        const a = pop();
        G.assert(b !== 0, 't1!=0'); // trigger.c:676
        push(idiv(a, b));
        break;
      }
      case OP_MOD: {
        const b = pop();
        const a = pop();
        G.assert(b !== 0, 't1!=0'); // trigger.c:682
        push(irem(a, b));
        break;
      }
      case OP_ADD: {
        const b = pop();
        const a = pop();
        push(a + b);
        break;
      }
      case OP_SUB: {
        const b = pop();
        const a = pop();
        push(a - b);
        break;
      }
      case OP_ARRAY: {
        const k = pop();
        push(captures(G)[k] ?? 0); // low word of 0x51D2AC[k]
        break;
      }
      case OP_SLOT: {
        const k = pop();
        const p = pop();
        push(i16(gs, playerAddr(p) + P.SLOT_HP + 4 * k)); // low word of the int32
        break;
      }
      case OP_SEEN: {
        const q = pop();
        const z = pop();
        const x = pop();
        G.assert(x >= 0 && x < w, 't0>=0 && t0<gs->map->xsize'); // trigger.c:619
        G.assert(z >= 0 && z < h, 't1>=0 && t1<gs->map->ysize'); // trigger.c:620
        G.assert(q >= 0 && q < 8, 't2>=0 && t2<MAX_N_PLAYERS'); // trigger.c:621
        push((Grid.cellGround(G, x, z) & (0x40000000 >> q)) !== 0 ? 1 : 0);
        break;
      }
      case OP_STAT: {
        const k = (pop() << 24) >> 24; // low byte, sign-extended
        const p = pop();
        push(G.stat(k, p)); // 0x41A790(k, p)
        break;
      }
      case OP_TSTAT: {
        const type = pop();
        const k = (pop() << 24) >> 24;
        const p = pop();
        push(G.typeStat(k, p, type)); // 0x41A87C(k, p, type)
        break;
      }
      case OP_MINE: {
        const z = pop();
        const x = pop();
        G.assert(z >= 0 && z < h, 't1>=0 && t1<gs->map->ysize'); // trigger.c:569
        G.assert(x >= 0 && x < w, 't0>=0 && t0<gs->map->xsize'); // trigger.c:570
        push((G.map.load[z * w + x] & ALIVE_MINE) !== 0 ? 1 : 0);
        break;
      }
      case OP_END:
        G.assert(sp === 2, 'sptr==(stack+2)'); // trigger.c:696
        return st[sp - 1];
      default:
        G.assert(false, '0 (trigger.c:699: unknown opcode)');
        break;
    }
  }
}

// ================================================================================================
// trigger.c — loading (0x43FD1C, 0x43E6E0, 0x43E738)
// ================================================================================================

// Action opcodes (byte +0 of the 28-byte record at 0x51B6AC + n*28), keyword table 0x486B90.
const ACTION_OPS = Object.freeze({
  ai: 0x00,
  die: 0x01,
  reinforce: 0x02,
  bail: 0x03,
  aimsg: 0x04,
  newrate: 0x05,
  setarray: 0x06,
  setlifes: 0x07,
  ally: 0x08,
  dfiddle: 0x09,
  waypoint: 0x0a,
  msg: 0x0b,
  exomoney: 0x0c,
  setmoney: 0x0d,
  newrate2: 0x0e,
  reinforce2: 0x0f,
  newtype: 0x10,
  artifact: 0x11,
  noundeploy: 0x12,
  abduct: 0x13,
  vision: 0x14,
  nopickup: 0x15,
  funkytower: 0x16,
});

/** strtol(s, 10) of a JSON argument (numbers as they are, strings like the C runtime). */
function strtol(v) {
  if (typeof v === 'number') return v | 0;
  const n = parseInt(String(v ?? '').trim(), 10);
  return Number.isNaN(n) ? 0 : n | 0;
}

/**
 * Parse one JSON action into its 28-byte record (+0 op, +4.. arguments with the widths of the
 * original stores, +0x18 next). `expr` holds the compiled expression of setarray / setlifes /
 * newrate2 / setmoney (record +8 in the original).
 */
function parseAction(G, R, act) {
  const op = ACTION_OPS[act.op];
  if (op === undefined) {
    G.assert(false, `0 (trigger.c:1502: unknown action (${act.op}) ${String(act.op ?? '').length})`);
    return null;
  }
  const args = act.args ?? [];
  const rec = Buffer.alloc(0x1c);
  const a = { op, rec, expr: null, next: null };
  rec[0] = op;
  R.actionCount++;
  const b8 = (off, v) => {
    rec[off] = strtol(v) & 0xff;
  };
  const w16r = (off, v) => rec.writeInt16LE(sx16(strtol(v)), off);
  const exprArg = (v) => {
    a.expr = compileCondition(G, String(v ?? ''));
    R.poolUsed += a.expr.length;
  };
  switch (op) {
    case ACTION_OPS.ai:
      w16r(4, args[0]);
      w16r(6, args[1]);
      break;
    case ACTION_OPS.aimsg: {
      b8(4, args[0]);
      b8(5, args[1]);
      const n = rec[5];
      for (let i = 0; i < n; i++) {
        G.assert(i < 5, 'i<MAX_AI_MSG_SIZE'); // trigger.c:1248
        if (i < 5) w16r(6 + 2 * i, args[2 + i]);
      }
      break;
    }
    case ACTION_OPS.reinforce:
    case ACTION_OPS.reinforce2:
      b8(4, args[0]);
      b8(5, args[1]);
      b8(6, args[2]);
      for (let i = 0; i < 5; i++) {
        b8(7 + i, args[3 + 2 * i]);
        b8(0x0c + i, args[4 + 2 * i]);
      }
      break;
    case ACTION_OPS.die:
    case ACTION_OPS.noundeploy:
      break;
    case ACTION_OPS.artifact:
    case ACTION_OPS.abduct:
    case ACTION_OPS.exomoney:
    case ACTION_OPS.bail:
      b8(4, args[0]);
      b8(5, args[1]);
      break;
    case ACTION_OPS.newrate:
    case ACTION_OPS.newtype:
    case ACTION_OPS.vision:
    case ACTION_OPS.ally:
    case ACTION_OPS.dfiddle:
      b8(4, args[0]);
      b8(5, args[1]);
      b8(6, args[2]);
      break;
    case ACTION_OPS.newrate2:
    case ACTION_OPS.setmoney:
      b8(4, args[0]);
      b8(5, args[1]);
      exprArg(args[2]);
      break;
    case ACTION_OPS.waypoint: {
      b8(4, args[0]);
      b8(5, args[1]);
      b8(6, args[2]);
      const n = rec[6];
      G.assert(
        n > 0 && n <= 8,
        't.action[a].action.waypoint.npoints>0 && t.action[a].action.waypoint.npoints<=8',
      ); // trigger.c:1381
      for (let i = 0; i < n; i++) {
        b8(7 + 2 * i, args[3 + 2 * i]);
        b8(8 + 2 * i, args[4 + 2 * i]);
      }
      break;
    }
    case ACTION_OPS.setarray:
    case ACTION_OPS.setlifes:
      w16r(4, args[0]);
      exprArg(args[1]);
      break;
    case ACTION_OPS.nopickup:
    case ACTION_OPS.funkytower:
      b8(4, args[0]);
      break;
    case ACTION_OPS.msg:
      b8(4, args[0]);
      b8(5, args[1]);
      b8(6, args[2]);
      b8(7, args[3]);
      b8(8, args[4]);
      break;
    default:
      break;
  }
  return a;
}

/**
 * 0x43FD1C(gs, name): reset the 128 triggers (lives 0, condition null) and the capture counters,
 * then load every block of the map JSON `script[]` ({id, type, flag, condition, actions[]}).
 * Header 0x43E738: id (assert 0..255), norm/trip, lifes (assert >= 0), condition; the actions
 * are linked in front of each other, so the LAST action of a block runs FIRST.
 */
export function loadTriggers(G, script) {
  const R = renatState(G);
  for (const t of R.triggers) {
    t.lives = 0;
    t.cond = null;
  }
  captures(G).fill(0); // 0x51D2A8[1..8] = 0
  R.actionCount = 0;
  R.poolUsed = 0;
  for (const tr of script ?? []) {
    const slot = strtol(tr.id);
    G.assert(slot >= 0 && slot < 256, 'slot>=0 && slot<256'); // trigger.c:1158
    if (slot < 0 || slot >= NUM_TRIGGERS) {
      // ids 128..255 pass the original's assert but overwrite the action table behind the
      // 128-entry trigger table; no shipped file uses them
      G.assert(false, `trigger id ${slot} outside the 128-entry table`);
      continue;
    }
    const t = R.triggers[slot];
    const kind = String(tr.type ?? '');
    if (kind === 'norm' || kind === 'NORM') t.type = 0;
    else if (kind === 'trip' || kind === 'TRIP') t.type = 1;
    else G.assert(false, `0 (trigger.c:1174: Parse Error! Looking for (norm) in trigger ${slot})`);
    const lives = tr.flag == null ? 0 : strtol(tr.flag); // strtol of a non-number is 0
    G.assert(lives >= 0, 'lifes>=0'); // trigger.c:1186
    t.lives = lives & 0xff;
    t.cond = compileCondition(G, String(tr.condition ?? ''));
    R.poolUsed += t.cond.length;
    let head = null;
    for (const act of tr.actions ?? []) {
      const a = parseAction(G, R, act);
      if (!a) continue;
      a.next = head; // 0x43EAD9: new record -> previous head
      head = a;
    }
    t.actions = head; // `end`: 0x43FD0A
    G.assert(R.poolUsed <= MAX_POOL, 'eq<=t.pool+MAX_POOL'); // renat.c:1540
  }
}

// ================================================================================================
// trigger.c — evaluation (0x43E5B0, 0x43E610) and actions (0x43D904)
// ================================================================================================

/** 0x43E5B0(gs): every 8th tick — fire every armed `norm` trigger whose condition holds. */
export function triggersTick(G) {
  const R = renatState(G);
  for (let i = 0; i < NUM_TRIGGERS; i++) {
    const t = R.triggers[i];
    if (t.lives === 0 || t.type !== 0) continue;
    if (evalCondition(G, t.cond, -1) === 0) continue;
    runActions(G, t.actions);
    t.lives = (t.lives - 1) & 0xff;
  }
}

/** 0x43E610(gs, trigger, obj): a unit stepped on trigger tile `trigger` (move.js). */
export function triggerEnter(G, trigger, obj) {
  const R = renatState(G);
  const t = R.triggers[trigger & 0xff];
  if (!t || t.lives === 0 || t.type !== 1) return;
  if (evalCondition(G, t.cond, obj) === 0) return;
  runActions(G, t.actions);
  t.lives = (t.lives - 1) & 0xff;
}

/** First object (0..799, allocated or not) standing on tile (x, z); -1 if none. */
function objectAtTile(gs, x, z, from = 0) {
  for (let obj = from; obj < MAX_OBJECTS; obj++) {
    const a = objAddr(obj);
    if (u16(gs, a + O.X) >> 8 !== x) continue;
    if (u16(gs, a + O.Z) >> 8 !== z) continue;
    return obj;
  }
  return -1;
}

/** 0x43D850(action, obj): patrol waypoints of the `waypoint` action. */
function applyWaypoints(gs, rec, a) {
  const n = rec[6];
  for (let i = 0; i < n; i++) {
    w16(gs, a + O.WAYPOINTS + 4 * i, (rec[7 + 2 * i] << 8) + 0x80);
    w16(gs, a + O.WAYPOINTS + 4 * i + 2, (rec[8 + 2 * i] << 8) + 0x80);
  }
  w8(gs, a + O.PENDING, 1);
  w8(gs, a + O.ORDER, 9);
  w8(gs, a + O.WP_COUNT, n);
}

/**
 * 0x43D904(gs, actions): run an action list (jump table 0x43D8A8). Notes per action:
 *   ai p v            player+0xBBC (AI type) = v                                    0x43D930
 *   die               assert(0) — not implemented in the binary                     0x43DA0A
 *   reinforce t x z.. drop pod with up to 5 (type, count) pairs (reinforce below)    0x43E281
 *   bail a b          gs+0x471C9 = 1, UI timer; stat(0,0) = a, stat(0,7) = b         0x43DA52
 *   aimsg p n w..     0x41AF74(gs, p, n, words) -> Ai.triggerAiMsg                   0x43D94E
 *   newrate r x z     first living vent on the tile: rate = r*stat(0,1)/256           0x43DBFC
 *   setarray k expr   set_type_stat(2, 0, k, eval(expr)) — read back by s(0,2,k)      0x43DBD1
 *   setlifes n expr   trigger n lives = eval(expr) & 0xFF                             0x43DA90
 *   ally a b v        alliance byte + diplomacy matrix 0 both ways                    0x43DAB6
 *   dfiddle p i v     player DEPEND-disabled byte i = v                               0x43DB91
 *   waypoint x z n..  first object on the tile gets n patrol waypoints (order 9)     0x43E183
 *   msg ...           assert(type == 0); message box (display only)                   0x43D967
 *   exomoney p v      player income = v                                              0x43D9EB
 *   setmoney x z expr living vent on the tile: hp = eval*stat(0,2)/256; else a new    0x43DE9D
 *                     dormant vent (rate 0) is created there with that money
 *   newrate2 x z expr living vent on the tile: rate = eval*stat(0,1)/256               0x43DD46
 *   reinforce2 t x z..for every pair: count units of the type; a research site on     0x43E435
 *                     the tile swallows them, else spawnCreature (team t)
 *   newtype x z t     first non-flying object on the tile changes type               0x43E1F4
 *   artifact x z      rand(): item 63 + rand() % 5 added to the research site (assert) 0x43E4B3
 *   noundeploy        gs+0x948 = 1 (mining towers stop obeying undeploy orders)        0x43E549
 *   abduct d s        hero 0 of team d is carried off by a pod of team s               0x43E2A7
 *   vision a b v      diplomacy matrix 1 both ways                                    0x43DB17
 *   nopickup p        player+0xBB4 = 1                                                0x43DB55
 *   funkytower p      player+0xBC0 = 1                                                0x43DB73
 * Object scans run over all 800 records (allocated or not) with unsigned positions >> 8.
 */
export function runActions(G, head) {
  const gs = G.gs;
  const R = renatState(G);
  for (let act = head; act; act = act.next) {
    const rec = act.rec;
    switch (rec[0]) {
      case ACTION_OPS.ai:
        w32(gs, playerAddr(rec.readInt16LE(4)) + P.AI_TYPE, rec.readInt16LE(6));
        break;
      case ACTION_OPS.die:
        G.assert(false, '0 (trigger.c:754: die action)');
        break;
      case ACTION_OPS.reinforce:
        reinforce(G, rec[4], rec[5], rec[6], rec.subarray(7, 12), rec.subarray(12, 17));
        break;
      case ACTION_OPS.bail:
        w8(gs, GS_BAIL, 1);
        w32(gs, GS_BAIL_TIME, 0); // timeGetTime() + 10000: UI countdown, display only
        G.setStat(0, 0, rec[4]);
        G.setStat(0, 7, rec[5]);
        break;
      case ACTION_OPS.aimsg: {
        const n = rec[5];
        const words = new Int16Array(n);
        for (let i = 0; i < n; i++) words[i] = rec.readInt16LE(6 + 2 * i);
        Ai.triggerAiMsg(G, rec[4], n, words); // 0x41AF74(gs, player, n, &words)
        break;
      }
      case ACTION_OPS.newrate: {
        const x = rec[5];
        const z = rec[6];
        let found = false;
        for (let obj = objectAtTile(gs, x, z); obj !== -1; obj = objectAtTile(gs, x, z, obj + 1)) {
          const a = objAddr(obj);
          if (u8(gs, a + O.TYPE) !== 0x28 || u8(gs, a + O.LIFE) === 0) continue;
          // rate 0 -> non-zero: 0x431DB8(1, 7, 0, 0, 0) "vent erupted" message, display only
          w16(gs, a + O_RATE, idiv(rec[4] * G.stat(0, 1), 256));
          found = true;
          break;
        }
        if (!found) G.assert(false, `found (There's no mine at ${x} ${z}, fix your damn triggers)`);
        break;
      }
      case ACTION_OPS.setarray: {
        const v = evalCondition(G, act.expr, -1);
        G.setTypeStat(2, 0, rec.readUInt16LE(4), v); // 0x41A634(2, 0, k, v)
        break;
      }
      case ACTION_OPS.setlifes: {
        const v = evalCondition(G, act.expr, -1);
        const n = rec.readUInt16LE(4);
        if (n < NUM_TRIGGERS) R.triggers[n].lives = v & 0xff;
        else G.assert(false, `setlifes ${n} outside the 128-entry table`);
        break;
      }
      case ACTION_OPS.ally: {
        const a = rec[4];
        const b = rec[5];
        const v = i8(rec, 6);
        w8(gs, GS.ALLIANCE + a * 10 + b, v);
        G.diploSet(0, a, b, v); // 0x41E928(matrix0, a, b, v)
        G.diploSet(0, b, a, v);
        break;
      }
      case ACTION_OPS.dfiddle:
        w8(gs, playerAddr(rec[4]) + P.DISABLED + rec[5], rec[6]);
        // rec[4] == local player: 0x437D00 build-menu refresh, display only
        break;
      case ACTION_OPS.waypoint: {
        const obj = objectAtTile(gs, rec[4], rec[5]);
        if (obj !== -1) applyWaypoints(gs, rec, objAddr(obj));
        break;
      }
      case ACTION_OPS.msg:
        G.assert(rec[5] === 0, 'a->action.message.type==0'); // trigger.c:747
        // 0x44D88C(gs+0x46FBC, rec[4], rec[6], rec[7], rec[8]): message box, display only
        break;
      case ACTION_OPS.exomoney:
        w32(gs, playerAddr(rec[4]) + P.INCOME, rec[5]);
        break;
      case ACTION_OPS.setmoney: {
        const x = rec[4];
        const z = rec[5];
        let found = false;
        for (let obj = objectAtTile(gs, x, z); obj !== -1; obj = objectAtTile(gs, x, z, obj + 1)) {
          const a = objAddr(obj);
          if (u8(gs, a + O.TYPE) !== 0x28 || u8(gs, a + O.LIFE) === 0) continue;
          const v = evalCondition(G, act.expr, -1);
          w32(gs, a + O.HP, idiv(v * G.stat(0, 2), 256));
          found = true;
          break;
        }
        if (found) break;
        // no vent here: create a dormant one (0x43DF54)
        const v = evalCondition(G, act.expr, -1);
        const money = idiv(v * G.stat(0, 2), 256);
        const obj = spawnCreature(G, x, z, 0x28, 8);
        const a = objAddr(obj);
        G.assert(u16(gs, a + O.X) >> 8 === x, 'a->action.setmoney.x == gs->all_objects[troop].x_pos>>8'); // :875
        G.assert(u16(gs, a + O.Z) >> 8 === z, 'a->action.setmoney.z == gs->all_objects[troop].z_pos>>8'); // :876
        w16(gs, a + O_RATE, 0);
        w32(gs, a + O.HP, money);
        G.map.load[z * G.map.w + x] |= ALIVE_MINE;
        break;
      }
      case ACTION_OPS.newrate2: {
        const x = rec[4];
        const z = rec[5];
        let found = false;
        for (let obj = objectAtTile(gs, x, z); obj !== -1; obj = objectAtTile(gs, x, z, obj + 1)) {
          const a = objAddr(obj);
          if (u8(gs, a + O.TYPE) !== 0x28 || u8(gs, a + O.LIFE) === 0) continue;
          const v = evalCondition(G, act.expr, -1);
          // rate 0 and v != 0: 0x431DB8(1, 7, 0, 0, 0) "vent erupted" message, display only
          w16(gs, a + O_RATE, idiv(v * G.stat(0, 1), 256));
          found = true;
          break;
        }
        if (!found) G.assert(false, `found (There's no mine at ${x} ${z}, fix your damn triggers)`);
        break;
      }
      case ACTION_OPS.reinforce2: {
        const team = rec[4];
        const x = rec[5];
        const z = rec[6];
        for (let i = 0; i < 5; i++) {
          const type = rec[7 + i];
          const count = rec[0x0c + i];
          for (let k = 0; k < count; k++) {
            if (!addResearchItem(G, x, z, type)) spawnCreature(G, x, z, type, team);
          }
        }
        break;
      }
      case ACTION_OPS.newtype: {
        for (let obj = objectAtTile(gs, rec[4], rec[5]); obj !== -1; obj = objectAtTile(gs, rec[4], rec[5], obj + 1)) {
          const a = objAddr(obj);
          if (T(G, u8(gs, a + O.TYPE)).fly !== 0) continue;
          w8(gs, a + O.TYPE, rec[6]);
          break;
        }
        break;
      }
      case ACTION_OPS.artifact: {
        const x = rec[4];
        const z = rec[5];
        const item = irem(G.rand(), 5) + 0x3f;
        if (!addResearchItem(G, x, z, item)) {
          G.assert(false, 'research_stuff(x, z, t)'); // trigger.c:1015
          addResearchItem(G, x, z, item); // the assert macro evaluates the condition again
        }
        break;
      }
      case ACTION_OPS.noundeploy:
        w8(gs, GS_NOUNDEPLOY, 1); // 0x43C5C0; 0x513164 / 0x51327C = 0 are UI globals
        break;
      case ACTION_OPS.abduct: {
        const dteam = rec[4];
        const steam = rec[5];
        G.assert(steam >= 0 && steam < 8, 'steam>=0 && steam<MAX_N_PLAYERS'); // trigger.c:953
        G.assert(dteam >= 0 && dteam < 8, 'dteam>=0 && dteam<MAX_N_PLAYERS'); // trigger.c:954
        const hero = i16(gs, playerAddr(dteam) + P.HEROES);
        const ha = objAddr(hero);
        const life = u8(gs, ha + O.LIFE);
        if (life === 0 || life === 10) break;
        const x = u16(gs, ha + O.X) >> 8;
        const z = u16(gs, ha + O.Z) >> 8;
        // types[1..] / counts[2..] beyond the marker are uninitialised stack bytes in the original
        const types = Uint8Array.from([0xff, (hero >> 8) & 0xff, 0, 0, 0]);
        const counts = Uint8Array.from([1, hero & 0xff, 0, 0, 0]);
        reinforce(G, steam, x, z, types, counts);
        break;
      }
      case ACTION_OPS.vision: {
        const a = rec[4];
        const b = rec[5];
        const v = i8(rec, 6);
        G.diploSet(1, a, b, v); // 0x41E928(matrix1, ...)
        G.diploSet(1, b, a, v);
        break;
      }
      case ACTION_OPS.nopickup:
        w32(gs, playerAddr(rec[4]) + P_NOPICKUP, 1);
        break;
      case ACTION_OPS.funkytower:
        w32(gs, playerAddr(rec[4]) + P_FUNKY, 1);
        break;
      default:
        G.assert(false, '0 (trigger.c:1024: unknown action opcode)');
        break;
    }
  }
}

/**
 * 0x4191E0(gs, team, x, z, types[5], counts[5]): a reinforcement drop pod. Takes the first free pod
 * slot k (byte player+0xE17+k, assert), creates the pod object at index team*15+7+k (type 0x5C
 * human / 0x5D alien, team 8), rand() #1/#2 offset its x/z by -256 or +256 around the tile centre,
 * pushes state 0x15 {5 packed (type<<8|count) words, x, z, phase 0, index 0, team, land x, land z,
 * sound}, slides it towards its position (0x4126A0) and pushes the descent (0x418580).
 */
export function reinforce(G, team, x, z, types, counts) {
  const gs = G.gs;
  const pa = playerAddr(team);
  let k = -1;
  for (let i = 0; i < 8; i++) {
    if (u8(gs, pa + P_REINF + i) === 0) {
      k = i;
      break;
    }
  }
  G.assert(k !== -1, 'o!=-1'); // mobiles.c:3776
  if (k === -1) return; // the original goes on with k = -1 (writes below the player block)
  w8(gs, pa + P_REINF + k, 1);
  const pod = k + team * 15 + 7;
  const pd = objAddr(pod);
  const podType = i32(gs, pa + P.RACE) === 1 ? 0x5d : 0x5c;
  Scenario.createObject(G, 0, 0, podType, 8, pod); // 0x41B930(gs, 0, 0, type, 8, index)
  Ticker.resetAndDispatchOrder(G, pod);
  let r = G.rand();
  w16(gs, pd + O.X, (x << 8) + 0x80 + ((r & 1) << 9) - 0x100);
  r = G.rand();
  w16(gs, pd + O.Z, (z << 8) + 0x80 + ((r & 1) << 9) - 0x100);
  const info = Ticker.pushState(G, pod, 0x15, 0x0d);
  for (let i = 0; i < 5; i++) w16(gs, info + 2 * i, (types[i] << 8) | counts[i]);
  w16(gs, info + 0x0a, x);
  w16(gs, info + 0x0c, z);
  w16(gs, info + 0x12, team);
  w16(gs, info + 0x14, (x << 8) + 0x80);
  w16(gs, info + 0x10, 0);
  w16(gs, info + 0x16, (z << 8) + 0x80);
  w16(gs, info + 0x0e, 0);
  w16(gs, info + 0x18, 0); // 0x431A08(...): sound handle, sound only
  const X = u16(gs, pd + O.X);
  const Z = u16(gs, pd + O.Z);
  Move.startStep(G, pod, X >> 8, Z >> 8, X, Z); // 0x4126A0
  City.pushPodFlight(G, pod, 0, 6, podType === 0x5d ? 0x4b0 : 0x258, 0, i16(gs, info + 0x18)); // 0x418580
}

// ================================================================================================
// mobiles.c — vents, research sites, harvesting
// ================================================================================================

/**
 * idle branch of type 40 (0x4137A0, from the idle state for unarmed types): a dormant vent (rate 0)
 * only shows STAND. Otherwise, with a type 6 / 0xE unit on the tile, info[0] counts down from 50;
 * at 0 the unit becomes the mining tower (type 0x2F / 0x30) and gets state 0xC {vent, 1, 0}.
 * Returns 1 in that case so that the vent's own state runs again this tick.
 */
export function idleVent(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = T(G, u8(gs, a + O.TYPE));
  const x = u16(gs, a + O.X) >> 8;
  const z = u16(gs, a + O.Z) >> 8;
  if (i16(gs, a + O_RATE) === 0) {
    Anim.startAnim(G, a + O.ANIM0, t.stand, 2);
    return 0;
  }
  G.assert(
    x >= 0 && z >= 0 && x < G.map.w && z < G.map.h,
    'xd>=0 && yd>=0 && xd<gs->map->xsize && yd<gs->map->ysize',
  ); // mobiles.c:940
  const cell = Grid.cellGround(G, x, z) & 0x3ff;
  const other = cell !== EMPTY && cell !== RESERVED ? cell : -1;
  const oa = other !== -1 ? objAddr(other) : -1;
  const otype = other !== -1 ? u8(gs, oa + O.TYPE) : -1;
  const isDriller = otype === 6 || otype === 0x0e;
  if (u8(gs, a + O.ANIM0_STATUS) === 2) {
    if (other === -1 || isDriller) {
      w8(gs, a + O.HEADING, 0);
      w16(gs, info, 0x32);
      Anim.startAnim(G, a + O.ANIM0, t.stand, 0);
      return 0;
    }
  }
  if (other === -1 || !isDriller) {
    w16(gs, info, 0x32);
    return 0;
  }
  const n = i16(gs, info);
  if (n === 0) return 0;
  w16(gs, info, n - 1);
  if (n - 1 > 0) return 0;
  // the unit deploys into a mining tower (0x4139AA)
  const ot = T(G, otype);
  w8(gs, a + O.HEADING, u8(gs, oa + O.HEADING));
  Anim.startAnim(G, oa + O.ANIM0, ot.deploy, 1);
  Anim.startAnim(G, a + O.ANIM0, t.stand, 2);
  // other's team == local player: 0x431F60(gs, type, 0, 5, 1, x, z) message, display only
  if (otype === 6) w8(gs, oa + O.TYPE, 0x2f);
  else if (otype === 0x0e) w8(gs, oa + O.TYPE, 0x30);
  const hi = Ticker.pushState(G, other, 0x0c, 3);
  w16(gs, hi + 2, 1);
  w16(gs, hi + 4, 0);
  w16(gs, hi, obj);
  return 1;
}

/**
 * idle branch of type 37 (0x4134D4): with a type 6 / 0xE unit of a player who owns building slot 4
 * (research centre) idling on the tile (its level-0 state is 1), the unit plays FUNK and info[0]
 * counts down from 450; at 0 the site hands out its next item type as a unit of that player
 * (spawnCreature). An empty site dies (life 0, slot deallocated — no grid removal).
 */
export function idleArtifactSite(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  const x = u16(gs, a + O.X) >> 8;
  const z = u16(gs, a + O.Z) >> 8;
  G.assert(
    x >= 0 && z >= 0 && x < G.map.w && z < G.map.h,
    'xd>=0 && yd>=0 && xd<gs->map->xsize && yd<gs->map->ysize',
  ); // mobiles.c:871
  const cell = Grid.cellGround(G, x, z) & 0x3ff;
  const other = cell !== EMPTY && cell !== RESERVED ? cell : -1;
  let researching = false;
  let oa = -1;
  let otype = -1;
  if (other !== -1) {
    oa = objAddr(other);
    otype = u8(gs, oa + O.TYPE);
    if (
      (otype === 6 || otype === 0x0e) &&
      i32(gs, playerAddr(u8(gs, oa + O.TEAM)) + P.SLOT_HP + 4 * 4) !== 0 &&
      u8(gs, oa + O.STACK) === 1
    ) {
      researching = true;
    }
  }
  if (!researching) {
    w16(gs, info, 0x1c2);
    if (i8(gs, a + O_SOUND) !== -1) {
      // 0x431C28(0x5F, handle): stop the research sound, sound only
      w8(gs, a + O_SOUND, 0xff);
    }
    return 0;
  }
  Anim.startAnim(G, oa + O.ANIM0, T(G, otype).funk, 0);
  if (i8(gs, a + O_SOUND) === -1) {
    w8(gs, a + O_SOUND, 0); // 0x431A08(0x5F, 1, 0, 0, x, z): sound handle, sound only
  }
  // else 0x431B90(0x5F, handle, 0, 0, x, z): sound position update, sound only
  const n = i16(gs, info) - 1;
  w16(gs, info, n);
  if (n > 0) return 0;
  w16(gs, info, 0x1c2);
  const item = popResearchItem(G, x, z);
  if (item === -1) {
    if (i8(gs, a + O_SOUND) !== -1) w8(gs, a + O_SOUND, 0xff); // 0x431C28: sound only
    Anim.startAnim(G, oa + O.ANIM0, T(G, otype).stand, 0);
    w8(gs, a + O.LIFE, 0);
    w16(gs, GS.OBJ_ALLOC + obj * 2, -1);
    return 0;
  }
  spawnCreature(G, x, z, item, u8(gs, oa + O.TEAM));
  // 0x431DB8(4, 7, 0, 0, 0): message, display only
  return 0;
}

/**
 * state 0xC 0x413A88: mining tower on a vent. info = {vent object, restart-animation flag, partner
 * object (0x4D/0x4E, 0 = none)}. Every 16th day/night counter value the tower's owner receives the
 * vent's rate (times player+0x19BC/256 for AI players; the partner's owner gets half of it) and
 * the vent loses that amount. An exhausted vent (hp - rate <= 0) dies, the tower loses 270 hp and
 * undeploys through state 0xD {50} (dies when it had 270 or less).
 */
export function stateHarvest(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  const vent = i16(gs, info);
  const va = objAddr(vent);
  if (u8(gs, GS_NOUNDEPLOY) === 0 && u8(gs, a + O.ORDER) === 0x0d) {
    Ticker.resetAndDispatchOrder(G, obj);
    return 0;
  }
  const t = T(G, u8(gs, a + O.TYPE));
  if (u8(gs, a + O.ANIM0_STATUS) === 2) {
    w16(gs, info + 2, 0);
    Anim.startAnim(G, a + O.ANIM0, t.stand, 0);
  }
  const rate = i16(gs, va + O_RATE);
  if (i32(gs, va + O.HP) - rate <= 0) {
    // vent exhausted (0x413B47)
    Combat.objectDie(G, vent);
    Ticker.resetAndDispatchOrder(G, obj);
    const vx = u16(gs, va + O.X) >> 8;
    const vz = u16(gs, va + O.Z) >> 8;
    const li = vz * G.map.w + vx;
    G.assert((G.map.load[li] & ALIVE_MINE) !== 0, 'gs->map->load[vent->z_pos>>8][vent->x_pos>>8]&(1<<ALIVE_MINE)'); // :1023
    G.map.load[li] &= ~ALIVE_MINE;
    const di = Ticker.pushState(G, obj, 0x0d, 1);
    w16(gs, di, 0x32);
    const hp = i32(gs, a + O.HP);
    if (hp > 0x10e) {
      w32(gs, a + O.HP, hp - 0x10e);
    } else {
      Combat.objectDie(G, obj);
      Grid.removeObject(G, obj);
      return 0;
    }
    Anim.startAnim(G, a + O.ANIM0, t.deploy, 1);
    // team == local player: 0x431F60(gs, type, 0, 5, 0, x, z) message, display only
    return 0;
  }
  if ((i32(gs, GS.DN_COUNTER) & 0x0f) !== 0) return 0;
  const team = u8(gs, a + O.TEAM);
  const pa = playerAddr(team);
  let amount = rate;
  if (i32(gs, pa + P.AI_TYPE) !== 0) amount = idiv(i32(gs, pa + P.INCOME_MULT) * rate, 256);
  const taken = amount;
  let share = amount;
  if (i16(gs, info + 4) !== 0) {
    const partner = i16(gs, info + 4);
    const pd = objAddr(partner);
    const plife = u8(gs, pd + O.LIFE);
    const ptype = u8(gs, pd + O.TYPE);
    if (plife !== 0 && plife !== 10 && (ptype === 0x4d || ptype === 0x4e)) {
      share = idiv(amount, 2);
      const pteam = u8(gs, pd + O.TEAM);
      const pp = playerAddr(pteam);
      if (i32(gs, pp + P.SLOT_HP) !== 0) {
        w32(gs, pp + P.MONEY, i32(gs, pp + P.MONEY) + share);
        G.addStat(1, pteam, share);
      }
    } else {
      G.assert(
        ptype === 4 || ptype === 0x0c || ptype === 0x4d || ptype === 0x4e,
        'exploiter type check (mobiles.c:1059)',
      );
      w16(gs, info + 4, 0);
    }
  }
  if (i32(gs, pa + P.SLOT_HP) !== 0) {
    w32(gs, pa + P.MONEY, i32(gs, pa + P.MONEY) + share);
    G.addStat(1, team, share);
    G.addStat(5, team, 1);
  }
  w32(gs, va + O.HP, i32(gs, va + O.HP) - taken);
  return 0;
}

// ================================================================================================
// mobiles.c — artifact effects and the reinforcement pod
// ================================================================================================

/** |a| + |b| + |c| (0x416BE8). */
const absSum = (a, b, c) => (a < 0 ? -a : a) + (b < 0 ? -b : b) + (c < 0 ? -c : c);

/**
 * state 0xF 0x416DC0: the object is pulled towards a point. info = {.., .., .., .., distance,
 * dx, dy, dz, heading}: turn towards info[8]; distance < 256 -> object_die; else move by
 * (d * 65536 / min(distance, 1024)) / 512 along each axis and shorten the distance by the sum
 * of the absolute steps.
 */
export function stateF(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  Ticker.turnTowards(G, obj, i16(gs, info + 0x10)); // 0x412414, result unused
  let d = i16(gs, info + 8);
  if (d < 0x100) {
    Combat.objectDie(G, obj);
    return 0;
  }
  if (d > 0x400) d = 0x400;
  const q = idiv(0x10000, d);
  const mx = idiv(i16(gs, info + 0x0a) * q, 512);
  const my = idiv(i16(gs, info + 0x0c) * q, 512);
  const mz = idiv(i16(gs, info + 0x0e) * q, 512);
  w16(gs, info + 8, i16(gs, info + 8) - absSum(mx, my, mz));
  w16(gs, a + O.X, i16(gs, a + O.X) - mx);
  w16(gs, a + O.HEIGHT, i16(gs, a + O.HEIGHT) - my);
  w16(gs, a + O.Z, i16(gs, a + O.Z) - mz);
  return 0;
}

/**
 * state 0x10 0x4177AC: bouncing object (artifact effect). info = {.., countdown, vx, vz, bounces}.
 * Airborne: height -= 2*speed (floor 0). On the ground: the object under it loses 400 hp (city
 * slots only above 600) and dies below 0; the object moves by (vx, vz), turns back at the map
 * edge; when the countdown ran out, a target was hit or the edge was reached: rand() #1 sets the
 * countdown ((r & 8) + 4), more than 32 bounces -> object_die, rand() #2 picks the turn direction,
 * rand() #3 the amount (0x30..0x4F), sincos(heading * 32) gives (vx, vz) = speed * (sin, cos) / 800.
 */
export function state10(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  const speed = T(G, u8(gs, a + O.TYPE)).speed;
  let flag = 0;
  const h = u16(gs, a + O.HEIGHT);
  if (h !== 0) {
    const nh = h - 2 * speed;
    w16(gs, a + O.HEIGHT, nh < 0 ? 0 : nh);
    return 0;
  }
  if (i16(gs, info + 2) === 0) flag = 1;
  else w16(gs, info + 2, i16(gs, info + 2) - 1);
  const cell = Grid.cellGround(G, u16(gs, a + O.X) >> 8, u16(gs, a + O.Z) >> 8) & 0x3ff;
  if (cell !== EMPTY && cell !== RESERVED) {
    const ca = objAddr(cell);
    let hp = i32(gs, ca + O.HP);
    flag = 1;
    if (cell < 120) {
      if (hp > 600) hp -= 400;
    } else hp -= 400;
    if (hp < 0) {
      Combat.objectDie(G, cell);
      Grid.removeObject(G, cell);
    } else w32(gs, ca + O.HP, hp);
  }
  w16(gs, a + O.X, u16(gs, a + O.X) + u16(gs, info + 4));
  w16(gs, a + O.Z, u16(gs, a + O.Z) + u16(gs, info + 6));
  const X = u16(gs, a + O.X);
  const Z = u16(gs, a + O.Z);
  // map+0x9A4B8 / +0x9A4BC: extents in 1/256 tile (xsize << 8, ysize << 8)
  if (X > 0x8000 || X >= G.map.w << 8 || Z > 0x8000 || Z >= G.map.h << 8) {
    w16(gs, a + O.X, X - u16(gs, info + 4));
    w16(gs, a + O.Z, Z - u16(gs, info + 6));
  } else if (flag === 0) return 0;
  // bounce (0x417974)
  const r = G.rand();
  w16(gs, info + 8, i16(gs, info + 8) + 1);
  w16(gs, info + 2, (r & 8) + 4);
  if (i16(gs, info + 8) > 0x20) {
    Combat.objectDie(G, obj);
    return 0;
  }
  const r2 = G.rand();
  const turn = (G.rand() & 0x1f) + 0x30;
  const heading = u8(gs, a + O.HEADING);
  w8(gs, a + O.HEADING, (r2 & 1) === 0 ? heading + turn : heading - turn);
  const sc = Missile.sincos(G, u8(gs, a + O.HEADING) << 5); // 0x441724(angle, &edx, &ebx)
  w16(gs, info + 4, idiv(sx16(sc.sin) * speed, 800));
  w16(gs, info + 6, idiv(sx16(sc.cos) * speed, 800));
  return 0;
}

/**
 * state 0x15 0x418CE0: reinforcement drop pod (object team*15+7+k, pushed by reinforce). info =
 * {5 x (type<<8 | count), x, z, phase, index, team, land x, land z, sound}. Phase 0 -> 1; phase 2:
 * the pod slot is released, STAND, sleep 60 (forever). Unloading: the pod snaps to its landing
 * position; an empty landing tile spawns one unit of the current entry (its count--), an
 * exhausted list (index 5) makes the pod leave; otherwise the pod slides to the free tile nearest
 * to (x, z) (0x41B68C, assert empty). Entry 0 with type byte 0xFF is an abduction: entry 1 is the
 * hero object; the pod slides to it while it is more than a tile away, then crushes it
 * (0x4164BC + grid removal). An all-zero first entry stores 150 into the top-state info[0] of the
 * object encoded in the low bytes of entries 1 and 2 and leaves.
 */
export function state15(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = T(G, u8(gs, a + O.TYPE));
  const phase = i16(gs, info + 0x0e);
  if (phase === 0) {
    w16(gs, info + 0x0e, 1);
  } else if (phase === 2) {
    const team = i16(gs, info + 0x12);
    w8(gs, playerAddr(team) + P_REINF - 7 + (obj - team * 15), 0); // gs+0x19A8+p*0xE34+(obj-15p)
    Anim.startAnim(G, a + O.ANIM0, t.stand, 2);
    Ticker.sleepState(G, obj, 0x3c);
    return 0;
  }
  const leave = () => {
    w16(gs, info + 0x0e, 2);
    City.pushPodFlight(G, obj, 0, 6, u8(gs, a + O.TYPE) === 0x5d ? 0x4b0 : 0x258, 1, i16(gs, info + 0x18));
  };
  w16(gs, a + O.X, i16(gs, info + 0x14));
  w16(gs, a + O.Z, i16(gs, info + 0x16));
  const X = u16(gs, a + O.X);
  const Z = u16(gs, a + O.Z);
  const xt = X >> 8;
  const zt = Z >> 8;
  const idx = i16(gs, info + 0x10);
  const entry = i16(gs, info + 2 * idx);
  let count = u16(gs, info + 2 * idx) & 0xff;
  const type = entry >> 8;
  if (u16(gs, info) >> 8 === 0xff) {
    // abduction (0x418DFC)
    if (idx === 0) {
      w16(gs, info + 0x10, idx + 1);
      return 1;
    }
    const hero = entry;
    const ha = objAddr(hero);
    let dx = X - u16(gs, ha + O.X);
    let dz = Z - u16(gs, ha + O.Z);
    if (dx < 0) dx = -dx;
    if (dz < 0) dz = -dz;
    const life = u8(gs, ha + O.LIFE);
    if (life !== 0 && life !== 10) {
      if (dx + dz > 0x100) {
        Move.startStep(G, obj, xt, zt, u16(gs, ha + O.X), u16(gs, ha + O.Z)); // 0x4126A0
        w16(gs, info + 0x14, i16(gs, ha + O.X));
        w16(gs, info + 0x16, i16(gs, ha + O.Z));
        return 0;
      }
      Combat.makeCorpse(G, hero); // 0x4164BC
      Grid.removeObject(G, hero); // 0x434EB8
    }
    w16(gs, info + 0x10, i16(gs, info + 0x10) + 1);
    if (i16(gs, info + 0x10) !== (u16(gs, info) & 0xff) + 1) return 0;
    leave();
    return 0;
  }
  if (idx === 0 && count === 0 && type === 0) {
    // 0x418F42: wake the object encoded in entries 1 / 2
    const target = ((u16(gs, info + 2) & 0xff) << 8) + (u16(gs, info + 4) & 0xff);
    const ta = objAddr(target);
    const sp = i8(gs, ta + O.SP);
    const off = u8(gs, ta + O.STACK + 1 + 2 * sp);
    w16(gs, ta + O.INFO + 2 * off, 0x96);
    leave();
    return 0;
  }
  if ((Grid.cellGround(G, xt, zt) & 0x3ff) === EMPTY) {
    spawnCreature(G, xt, zt, type, i16(gs, info + 0x12));
    count--;
    w16(gs, info + 2 * idx, (type << 8) | count);
    let i = idx;
    while (count === 0) {
      i++;
      w16(gs, info + 0x10, i);
      count = u16(gs, info + 2 * i) & 0xff;
      if (i === 5) {
        leave();
        return 0;
      }
    }
  }
  // 0x4190C7: slide to the free tile nearest to the reinforcement point
  const [ffx, ffz] = Scenario.findFreeCell(G, i16(gs, info + 0x0a), i16(gs, info + 0x0c), 0); // 0x41B68C
  const f = { x: ffx, z: ffz };
  G.assert((Grid.cellGround(G, f.x, f.z) & 0x3ff) === EMPTY, 'IS_EMPTY(gs->map->run[zm][xm])'); // :3742
  w16(gs, info + 0x14, (f.x << 8) + 0x80);
  w16(gs, info + 0x16, (f.z << 8) + 0x80);
  Move.startStep(G, obj, X >> 8, Z >> 8, u16(gs, info + 0x14), u16(gs, info + 0x16));
  return 0;
}

// ---- states owned by city.js, re-exported under the ticker's Renat.* names -----------------------

/** state 0x13 0x418A7C: building under construction (drop pod). */
export function state13(G, obj, info) {
  return City.stateConstruct(G, obj, info);
}

/** state 0x14 0x418978: the building's drop pod has landed. */
export function state14(G, obj, info) {
  return City.statePodLand(G, obj, info);
}

/** state 0x16 0x418650: drop pod flight (parabolic height). */
export function state16(G, obj, info) {
  return City.statePodFlight(G, obj, info);
}

/** 0x4143D4(gs, obj, kind): pickups (obj+0xCB), ported by city.js; `kind` is re-read from the object. */
export function pickup(G, obj, kind) {
  void kind;
  return City.pickupsTick(G, obj);
}

/** 0x41B818 under the name combat.js uses (HYYK copies). */
export const spawnObject = spawnCreature;

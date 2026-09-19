// Krusty, the computer player of Classic dc16.exe (krusty.c, krusty_general.c, krusty_attack.c,
// krusty_defend.c, krusty_scout.c, krusty_army.c), ported from docs/DC16_AI.md §5-§15 and the
// disassembly (19 Sep 2026). One module for the whole AI; sections are named after the original files.
//
// The port is written once and driven in two ways (DC16_AI.md §17, plan §19.2/§19.6):
//   * EXACT mode (ai.js): inside game_tick after record(t), for players whose P.AI_TYPE is 3
//     (lobby computer slots, DISCONNECT takeovers). rand() is the game RNG, every command is
//     executed at once through Game.applyCommand, exactly as the original's local-command mode.
//   * BOT mode (../krustybot.js): for the server's fake human players. rand() is a private table
//     walk, the commands are collected and go into the next sync frame, chunked to the frame budget.
// Both go through the `ctx` object: { G, p, kai, rand(), emit(rawCommands[]), say(text), exact, fixes }.
// `fixes` (bot mode only, maintainer 19 Sep 2026: "fix the krusty bugs") turns on the repairs listed
// under FIXES below; exact mode never sets it, because there the port must match the original bit for
// bit, bugs included.
//
// FIXES (ctx.fixes):
//  * The armour/weapon upgrade goals (12 and 13, building kinds 7 and 6) never fire in the original:
//    dep_check_building answers 2 for an upgrade item. With fixes on they fire when an upgrade of that
//    kind (weapon 0 / armour 1) is buyable for a unit type of the player's race, and buy the one for the
//    unit type the player fields most of, level by level, with the 0x0C command (upgradeGoal below).
//  * attack_plan indexes the taken-target list B and the parked-group counter C by the GROUP index and
//    tests the contested flag of zone[m] instead of zone[dest]; with fixes on all three use the zone
//    (destination) index, so the two groups no longer march on the same zone and a group standing in a
//    contested destination is not sent home for lack of enemies.

//
// State: `kai` is a Buffer of 0x6C40 bytes with the ORIGINAL offsets (DC16_AI.md §5), so that the
// save-game layout (§19) and every address of the doc map 1:1. The six task callbacks live in code
// (TASK_CALLBACKS); the goal table stores kind indices instead of function pointers (set_goal kinds).
//
// PORT NOTES
//  * Every rand() of the original appears at the same point (defend §12: gate, then one per candidate
//    zone; bomber §14: per unit in the listed order). The census, influence map, goals, attack task and
//    mover draw none.
//  * Original quirks kept (DC16_AI.md §17.1): group_strength uses the class number as an object type
//    and gs+0x40 as a defence class; attack_plan reads zone[m].flags and bumps C[t] with the group /
//    task index; the home-guard reservoir takes the first candidate with certainty; the goal loop has
//    no upper bound; kai+0x6C34 (attack ratio) is never written by krusty_alloc and stays 0 (the pool
//    allocator zero-fills its blocks, 0x40C1B5);
//    the wander loop sums owner ids; the vent reservoir compares rand() & 0xFF with 256 / n.
//  * Guards the original does not have (TODO(exact), each commented): a routing chain that hits zone 0
//    (an unreachable destination would loop forever / overrun route[] in the original) stops; a weapon
//    index -1 in group_strength reads 0 instead of the bytes before the weapon table; a purged unit
//    with life 0 is unlinked after the assert (the original's assert ends the game).
//  * Positions are 1/256 tile; zone centres are `tile << 8` (no half-tile offset), vent targets the
//    vent object's raw x/z, both sign-wrapped to int16 like the original's 16-bit stores.

import { GS, O, P, u8, i8, i16, u16, i32, w8, w16, w32, objAddr, playerAddr, idiv, irem, sx16, MAX_OBJECTS } from './mem.js';
import * as City from './city.js';
import * as Scenario from './scenario.js';
import { build } from '../commands.js';

export const KAI_SIZE = 0x6c40;
export const NZONES = 256;
export const NTASKS = 4;
export const NMINORS = 16;
export const NCLASSES = 9;
export const MAX_GOALS = 32;
const DC_MESSAGE_MAX = 0x400; // hack.c: assert len <= DC_MESSAGE_MAX in send_command
const BOT_CHUNK = 100; // bot mode: objects per 0x07 group (7 + 6n bytes, inside the frame budget)

// ---- kai layout (DC16_AI.md §5) ------------------------------------------------------------------

export const K = Object.freeze({
  FIRST_RUN: 0x0000, // u8 (overlaps zone record 0)
  ZONE: 0x0000, // 256 x 18: record z at 18z
  MEM: 0x1202, // 800 x 4 last-seen memory: +0 tile x, +1 tile z, +2 type i8 (-1 none), +3 team
  TASK: 0x1e84, // 4 x 0x12FC major tasks
  TASK_SIZE: 0x12fc,
  DEFZONES: 0x6a74, // 16 x { u8 used, u8 zone }
  GOALS: 0x6a94, // 32 x { i32 check, i32 action, i32 param } (kinds, see GOAL_KIND)
  SPLIT: 0x6c14, // i32, 0xC0 = 75 % attack
  WEIGHTS: 0x6c18, // 7 x i32 class weights of the unit-building goal
  RATIO: 0x6c34, // i32 attack ratio (aimsg 13), never initialised by krusty_alloc
  SEE_THRU: 0x6c38, // u8[8]
});
/** Zone record fields, relative to kai + 18z. */
export const Z = Object.freeze({ CX: 2, CZ: 3, AA_OWNER: 4, AA_STR: 6, G_OWNER: 8, G_STR: 0xa, AIR_OWNER: 0xc, HOP: 0xd, AIR_STR: 0xe, BUILDINGS: 0x10, FLAGS: 0x12 });
/** Major task fields, relative to TASK(t). */
export const MT = Object.freeze({ HDR: 0x0, MINOR: 0x10, MINOR_SIZE: 0x12c, HAVE: 0x12d0 });
/** Minor task (group) fields, relative to MINOR(t, m). */
export const MN = Object.freeze({ ACTIVE: 0x1, ZONE: 0x8, DEST: 0xc, STATE: 0x10, STEP: 0x12, ROUTE: 0x14, HEAD: 0x116, TAIL: 0x118, COUNT: 0x11a });
/** Object fields the AI owns privately (DC16_AI.md §5; not in the checksum). */
export const OA = Object.freeze({ ZONE: 0x11, STATUS: 0xcc, TILE_X: 0xcd, TILE_Z: 0xce, STUCK: 0xcf, NEXT: 0xd2, PREV: 0xd4, RETARGET: 0xc9, BOTTOM_STATE: 0x39 });
export const NO_GROUP = -2; // obj+0xD2 of a unit in no group (init_object's value)
export const ZF_CONTESTED = 1;
export const ZF_ATTACK_HERE = 2;
export const ZF_MARKED = 4;

export const zoneAddr = (z) => K.ZONE + 18 * z;
export const memAddr = (o) => K.MEM + 4 * o;
export const taskAddr = (t) => K.TASK + K.TASK_SIZE * t;
export const minorAddr = (t, m) => taskAddr(t) + MT.MINOR + MT.MINOR_SIZE * m;
export const haveAddr = (t, c) => taskAddr(t) + MT.HAVE + 2 * c;

// ---- goal table (0x499158, 0x48B404/0x48B41C, 0x499110) -----------------------------------------

/** set_goal kinds (0x456624): 0 workers, 1 building, 2 army, 3 always, 4 nothing. */
export const GOAL_KIND = Object.freeze({ WORKERS: 0, BUILDING: 1, ARMY: 2, ALWAYS: 3, NOTHING: 4 });
/** The 18 default goals of 0x499158 as {check kind, param}; the action kind equals the check kind. */
export const DEFAULT_GOALS = Object.freeze([
  [1, 8], [0, 1], [1, 0], [2, 5], [1, 1], [1, 3], [2, 10], [0, 2], [2, 15], [1, 4], [1, 2], [2, 20],
  [1, 7], [1, 6], [2, 30], [1, 5], [2, 200], [3, 0],
]);
/** 0x499110[param][race]: DEPEND item of a building kind, human / alien. */
export const BUILDING_KIND = Object.freeze([[1, 15], [3, 17], [5, 19], [2, 16], [4, 18], [6, 20], [67, 41], [69, 43], [0, 14]]);
/** Class -> index into the seven weights at kai+0x6C18 (infantry, mech, artillery, cyborg, scout, carry-all/healer, tower builder). */
const WEIGHT_INDEX = Object.freeze({ 0: 0, 2: 1, 3: 2, 4: 3, 5: 4, 7: 5, 1: 6 });
const DEFAULT_WEIGHTS = Object.freeze([1, 1, 2, 4, 2, 4, 2]);

// ---- order bytes and unit types --------------------------------------------------------------------

export const ORDER_MOVE = 2;
export const ORDER_ASSAULT = 7;
export const ORDER_DEPLOY = 0x0d;
const TYPE_VENT = 0x28;
const WORKER_TYPES = [6, 0x0e]; // EXPL, SLUG
const SCOUT_TYPES = [5, 0x0d]; // SCGM, ORTU
const TOWER_BUILDER_TYPES = [1, 9];
const SCOUT_ITEMS = [10, 24]; // DEPEND troop items SCGM / ORTU
const O_VENT_RATE = 0x32; // i16 vent rate (renat.js), same offset as O.TARGET

// ---- small helpers ---------------------------------------------------------------------------------

const alive = (life) => life !== 0 && life !== 10;
const tileOf = (gs, a) => [u16(gs, a + O.X) >> 8, u16(gs, a + O.Z) >> 8];
const famAt = (G, x, z) => G.map.path.familyAt(x, z);
const nextHop = (G, a, b) => G.map.path.routing[((a & 0xff) << 8) + (b & 0xff)];
const adjCount = (G, z) => G.map.path.clist2[z * 32];
const adj = (G, z, k) => G.map.path.clist2[z * 32 + k];
const OT = (G, t) => G.tables.types[t];
const MB = (G, r, c) => G.tables.mbullet.rows[r][c];
/** 0x456A6B etc.: `ground(x, z) & player(q).VISION` - the player's vision MASK (own bit plus shared allies), not the single team bit. */
const seenByPlayer = (G, x, z, q) => (G.map.ground[z * G.map.w + x] & i32(G.gs, playerAddr(q) + P.VISION)) !== 0;
const groundIdAt = (G, x, z) => G.map.ground[z * G.map.w + x] & 0x3ff;
const centreOf = (kai, z) => [u8(kai, zoneAddr(z) + Z.CX), u8(kai, zoneAddr(z) + Z.CZ)];
const money = (G, p) => i32(G.gs, playerAddr(p) + P.MONEY);
const spend = (G, p, cost) => w32(G.gs, playerAddr(p) + P.MONEY, money(G, p) - cost);

/** unit_class 0x456150: types 0..15 -> type mod 8, 49/50 -> 7, 41/42 -> 1, else 8. */
export function unitClass(type) {
  if (type < 16) return type & 7;
  if (type === 49 || type === 50) return 7;
  if (type === 41 || type === 42) return 1;
  return 8;
}

/** Zone of a tile; a family-0 cell (buildings) is replaced by the nearest free ground cell (0x41B68C). */
function zoneOfTile(G, x, z) {
  let f = famAt(G, x, z);
  if (f === 0) {
    const [fx, fz] = Scenario.findFreeCell(G, x, z, 0);
    f = famAt(G, fx, fz);
  }
  return f;
}

// ---- command output (hack.c builders 0x40C7D4, 0x40C50C, 0x40C538; send_command 0x421648) -------

/** `[0x07 nwp nobjs (x,z)* (obj)*][0x05 obj order]*` for the given objects; chunked in bot mode. */
function sendWaypointOrder(ctx, objs, points, order) {
  const pts = points.map(([x, z]) => [sx16(x), sx16(z)]);
  const frame = (ids) => [build.waypointsObjects(pts, ids), ...ids.map((o) => build.order(o, order))];
  if (ctx.exact) {
    // the original builds one buffer and asserts its length (send_command); a bigger group would end the game
    const len = 3 + 4 * pts.length + 2 * objs.length + 4 * objs.length + 2;
    ctx.G.assert(len <= DC_MESSAGE_MAX, 'len<=DC_MESSAGE_MAX');
    ctx.emit(frame(objs));
    return;
  }
  if (objs.length === 0) {
    ctx.emit(frame([])); // the original sends the empty frame too (move_group advance)
    return;
  }
  for (let i = 0; i < objs.length; i += BOT_CHUNK) ctx.emit(frame(objs.slice(i, i + BOT_CHUNK)));
}

function sendOrder(ctx, obj, order) {
  ctx.emit([build.order(obj, order)]);
}

function sendBuildBuilding(ctx, slot, level) {
  ctx.emit([build.buildBuilding(slot, level, ctx.p)]);
}

function sendBuildUnits(ctx, type, count) {
  ctx.emit([build.buildUnits(type, ctx.p, count)]);
}

// ==================================================================================================
// krusty.c
// ==================================================================================================

/** hops 0x44B670(gs, a, b): 0xFF if a or b is 0, 0 if equal, else steps along next[.][b]; 0xFF on a 0 entry, 256 when not reached. */
export function hops(G, a, b) {
  if (a === 0 || b === 0) return 0xff;
  let n = 0;
  let cur = a;
  while (cur !== b) {
    if (cur === 0) return 0xff;
    cur = nextHop(G, cur, b);
    n++;
    if (n >= 256) return n; // post-increment test: the 256th lookup's result is never compared
  }
  return n;
}

/** The object indices of a group's list, head first. */
function listOf(gs, kai, t, m) {
  const out = [];
  let o = i16(kai, minorAddr(t, m) + MN.HEAD);
  let guard = 0;
  while (o !== -1 && guard++ < MAX_OBJECTS) {
    out.push(o);
    o = i16(gs, objAddr(o) + OA.NEXT);
  }
  return out;
}

/** link 0x44BC8C(gs, kai, obj, t, m): insert at the head of the group's list. */
export function link(gs, kai, obj, t, m) {
  const mn = minorAddr(t, m);
  const head = i16(kai, mn + MN.HEAD);
  const a = objAddr(obj);
  w16(gs, a + OA.NEXT, head);
  w16(gs, a + OA.PREV, -1);
  if (head !== -1) w16(gs, objAddr(head) + OA.PREV, obj);
  w16(kai, mn + MN.HEAD, obj);
  if (i16(kai, mn + MN.TAIL) === -1) w16(kai, mn + MN.TAIL, obj);
}

/** unlink 0x44B958(gs, kai, obj, t, m): remove from the list, both links become -2. */
export function unlink(gs, kai, obj, t, m) {
  const mn = minorAddr(t, m);
  const a = objAddr(obj);
  const next = i16(gs, a + OA.NEXT);
  const prev = i16(gs, a + OA.PREV);
  if (prev !== -1) w16(gs, objAddr(prev) + OA.NEXT, next);
  else w16(kai, mn + MN.HEAD, next);
  if (next !== -1) w16(gs, objAddr(next) + OA.PREV, prev);
  else w16(kai, mn + MN.TAIL, prev);
  w16(gs, a + OA.NEXT, NO_GROUP);
  w16(gs, a + OA.PREV, NO_GROUP);
}

/** purge 0x44BA78(gs, kai, t, m): drop corpses (life 10) from a group's list. */
function purge(ctx, t, m) {
  const { G, kai } = ctx;
  const gs = G.gs;
  for (const o of listOf(gs, kai, t, m)) {
    const a = objAddr(o);
    ctx.assert(i16(gs, a + OA.NEXT) !== NO_GROUP, 'obj->ai_next != -2 (krusty.c purge)');
    const life = u8(gs, a + O.LIFE);
    if (life === 0) {
      // "Object is dead": the original asserts (and ends the game); the port drops the stale link. TODO(exact)
      ctx.assert(false, 'Object is dead (krusty.c purge: life == 0)');
      unlink(gs, kai, o, t, m);
    } else if (life === 10) unlink(gs, kai, o, t, m);
  }
}

/** purge_group0 0x44BC00 / purge_all 0x44BC10. */
function purgeGroup0(ctx, t) {
  purge(ctx, t, 0);
}
function purgeAll(ctx, t) {
  for (let m = 0; m < NMINORS; m++) if (u8(ctx.kai, minorAddr(t, m) + MN.ACTIVE)) purge(ctx, t, m);
}

/** group_init_list 0x44B894: head = tail = -1, active = 1 (nothing else). */
function groupInitList(kai, t, m) {
  const mn = minorAddr(t, m);
  w16(kai, mn + MN.HEAD, -1);
  w16(kai, mn + MN.TAIL, -1);
  w8(kai, mn + MN.ACTIVE, 1);
}

/** header_clear 0x44B844 + task_init_groups 0x44B8E8: header zeroed, group 0 an empty active list, groups 1..15 inactive. */
function taskInitGroups(kai, t) {
  const M = taskAddr(t);
  for (let k = 0; k < 4; k++) w32(kai, M + MT.HDR + 4 * k, 0);
  groupInitList(kai, t, 0);
  for (let m = 1; m < NMINORS; m++) w8(kai, minorAddr(t, m) + MN.ACTIVE, 0);
}

/**
 * recount 0x44B6D4(gs, kai, t): have[] cleared once, count[] cleared for ALL 16 groups, then the
 * lists of the active groups counted by unit_class - no life test (corpses still listed count).
 */
function recountByClass(ctx, t) {
  const { G, kai } = ctx;
  const gs = G.gs;
  for (let c = 0; c < NCLASSES; c++) w16(kai, haveAddr(t, c), 0);
  for (let m = 0; m < NMINORS; m++) {
    const mn = minorAddr(t, m);
    for (let c = 0; c < NCLASSES; c++) w16(kai, mn + MN.COUNT + 2 * c, 0);
    if (!u8(kai, mn + MN.ACTIVE)) continue;
    for (const o of listOf(gs, kai, t, m)) {
      const c = unitClass(u8(gs, objAddr(o) + O.TYPE));
      w16(kai, mn + MN.COUNT + 2 * c, i16(kai, mn + MN.COUNT + 2 * c) + 1);
      w16(kai, haveAddr(t, c), i16(kai, haveAddr(t, c)) + 1);
    }
  }
}

/** worker_count 0x459AB8: have[6] = length of group 0's list. */
function workerCount(ctx, t) {
  const n = listOf(ctx.G.gs, ctx.kai, t, 0).length;
  w16(ctx.kai, haveAddr(t, 6), n);
}

// ---- the task callback tables (DC16_AI.md §7) ----------------------------------------------------

function update1Default(ctx, t) {
  w32(ctx.kai, taskAddr(t) + MT.HDR + 8, 0); // 0x4575E4
}
function update1Bomber(ctx, t) {
  // 0x459C44: M+8 += n ? 0 : 1000 (an urgency accumulator nothing reads)
  const n = listOf(ctx.G.gs, ctx.kai, t, 0).length;
  const a = taskAddr(t) + MT.HDR + 8;
  w32(ctx.kai, a, i32(ctx.kai, a) + (n ? 0 : 1000));
}
const noop = () => {};

const TASK_CALLBACKS = [
  { update1: update1Default, update2: purgeGroup0, plan: noop, move: workerUpdate, recount: workerCount, take: () => 0 },
  { update1: update1Default, update2: purgeAll, plan: defendUpdate, move: moveAll, recount: recountByClass, take: defendTake },
  { update1: update1Default, update2: purgeAll, plan: attackPlan, move: moveAll, recount: recountByClass, take: attackTake },
  { update1: update1Bomber, update2: purgeGroup0, plan: noop, move: bomberUpdate, recount: recountByClass, take: () => 0 },
];

// ---- krusty_alloc 0x44BD50 ----------------------------------------------------------------------

/** Allocate and initialise the Krusty state of player p (DC16_AI.md §6). */
export function krustyAlloc(G, p) {
  const kai = Buffer.alloc(KAI_SIZE); // smalloc 0x40C09C zero-fills the block (0x40C1B5), so every unwritten byte is 0
  for (let o = 0; o < MAX_OBJECTS; o++) w8(kai, memAddr(o) + 2, 0xff); // 0x44BD82: kai+0x1200+4i, i = 1..800 = the type byte of records 0..799
  for (let t = 0; t < 8; t++) w8(kai, K.SEE_THRU + t, 0);
  w8(kai, K.SEE_THRU + p, 1);
  initZones(G, p, kai);
  attackInit(kai, 2);
  defendInit(kai, 1);
  taskInitGroups(kai, 0); // worker init 0x459B68
  taskInitGroups(kai, 3); // bomber init 0x45A460
  w32(kai, K.SPLIT, 0xc0);
  for (let k = 0; k < 7; k++) w32(kai, K.WEIGHTS + 4 * k, DEFAULT_WEIGHTS[k]);
  w8(kai, K.FIRST_RUN, 1);
  initGoals(kai);
  return kai;
}

/** init_goals 0x4565D4: the 18 default goals into slots 0..17 (the rest stays as allocated). */
function initGoals(kai) {
  for (let g = 0; g < DEFAULT_GOALS.length; g++) setGoal(kai, g, DEFAULT_GOALS[g][0], DEFAULT_GOALS[g][1]);
}

/** set_goal 0x456624(kai, g, kind, param): asserts g < 32 and kind <= 4. */
export function setGoal(kai, g, kind, param) {
  if (!(g >= 0 && g < MAX_GOALS)) throw new Error('goal<MAX_GOALS');
  if (!(kind >= 0 && kind <= 4)) throw new Error('kind<=4');
  const a = K.GOALS + 12 * g;
  w32(kai, a, kind); // check function index (0x48B404 order: workers, building, army, always, always)
  w32(kai, a + 4, kind); // action function index (0x48B41C order: build worker, build, build unit, nothing, nothing)
  w32(kai, a + 8, param);
}

// ---- krusty_think 0x44BE64 -----------------------------------------------------------------------

/**
 * One think of player p (every 32 ticks in the original): census on the first run, influence map,
 * demand + goals, then the four tasks' update1 / update2 / plan / move.
 */
export function krustyThink(ctx) {
  const { kai } = ctx;
  if (u8(kai, K.FIRST_RUN)) {
    census(ctx);
    w8(kai, K.FIRST_RUN, 0);
  }
  krustyGeneral(ctx);
  krustyDemand(ctx);
  for (let t = 0; t < NTASKS; t++) {
    const cb = TASK_CALLBACKS[t];
    cb.update1(ctx, t);
    cb.update2(ctx, t);
    cb.plan(ctx, t);
    cb.move(ctx, t);
  }
}

// ---- krusty_aimsg 0x44BF78 (trigger `aimsg`, DC16_AI.md §18) -------------------------------------

export function krustyAimsg(ctx, words) {
  const { G, kai, p } = ctx;
  const id = words[0] | 0;
  const v = words[1] | 0;
  const tileZone = () => {
    const z = famAt(G, words[1] | 0, words[2] | 0);
    G.assert(z !== 0, 'zone!=0 (krusty_aimsg)');
    return z;
  };
  switch (id) {
    case 0:
      w32(kai, K.SPLIT, idiv(v * 256, 100));
      break;
    case 1: case 2: case 3: case 4: case 5:
      w32(kai, K.WEIGHTS + 4 * (id - 1), v);
      break;
    case 6:
      addDefendZone(G, kai, tileZone());
      break;
    case 7:
      removeDefendZone(G, kai, tileZone());
      break;
    case 8:
      setGoal(kai, words[1] | 0, words[2] | 0, words[3] | 0);
      break;
    case 9: case 10: {
      const a = zoneAddr(tileZone()) + Z.FLAGS;
      w8(kai, a, id === 9 ? u8(kai, a) | ZF_ATTACK_HERE : u8(kai, a) & ~ZF_ATTACK_HERE);
      break;
    }
    case 11: case 12: {
      const a = zoneAddr(tileZone()) + Z.FLAGS;
      w8(kai, a, id === 11 ? u8(kai, a) | ZF_MARKED : u8(kai, a) & ~ZF_MARKED);
      break;
    }
    case 13:
      w32(kai, K.RATIO, idiv(v * 256, 100));
      break;
    case 14:
      w8(kai, K.SEE_THRU + (v & 7), words[2] | 0);
      break;
    default:
      G.assert(false, 'Unknown AI msg');
  }
  void p;
}

// ==================================================================================================
// krusty_general.c
// ==================================================================================================

/** krusty_init_zones 0x456EF4: hop distances from the home zone and zone centres (DC16_AI.md §6). */
export function initZones(G, p, kai) {
  const gs = G.gs;
  const m = G.map;
  for (let z = 0; z < NZONES; z++) {
    w8(kai, zoneAddr(z) + Z.HOP, 0xff);
    w8(kai, zoneAddr(z) + Z.FLAGS, 0);
  }
  const pa = playerAddr(p);
  let hx = i32(gs, pa + P.CITY_X);
  let hz = i32(gs, pa + P.CITY_Z);
  if (hx === 0 || hz === 0) {
    hx = i32(gs, pa + P.START_X);
    hz = i32(gs, pa + P.START_Z);
  }
  hx = Math.min(Math.max(hx, 0), m.w - 1);
  hz = Math.min(Math.max(hz, 0), m.h - 1);
  const [fx, fz] = Scenario.findFreeCell(G, hx, hz, 0);
  const start = famAt(G, fx, fz);
  G.assert(start !== 0, 'start_zone!=0');
  // breadth first over the zone graph: the open zone with the smallest distance, its next hops relaxed
  const dist = new Int32Array(NZONES).fill(-1);
  const done = new Uint8Array(NZONES);
  dist[start] = 0;
  for (;;) {
    let cur = -1;
    for (let z = 1; z < 255; z++) if (!done[z] && dist[z] >= 0 && (cur < 0 || dist[z] < dist[cur])) cur = z;
    if (cur < 0) break;
    done[cur] = 1;
    w8(kai, zoneAddr(cur) + Z.HOP, dist[cur]);
    for (let d = 1; d < 255; d++) {
      const n = nextHop(G, cur, d);
      if (n === 0 || done[n]) continue;
      if (dist[n] < 0 || dist[n] > dist[cur] + 1) dist[n] = dist[cur] + 1;
    }
  }
  // centres: the mean tile of every family, snapped to the nearest cell of that family
  const sumX = new Int32Array(NZONES);
  const sumZ = new Int32Array(NZONES);
  const cnt = new Uint16Array(NZONES); // u16 in the original: wraps at 65536 tiles per family
  for (let z = 0; z < m.h; z++) {
    for (let x = 0; x < m.w; x++) {
      const f = famAt(G, x, z);
      sumX[f] += x;
      sumZ[f] += z;
      cnt[f]++;
    }
  }
  for (let f = 1; f < 255; f++) {
    if (cnt[f] === 0) continue; // a family without tiles keeps its old centre
    const mx = Math.floor((sumX[f] >>> 0) / (cnt[f] & 0xffff)); // unsigned div (0x45725F), u16 count
    const mz = Math.floor((sumZ[f] >>> 0) / (cnt[f] & 0xffff));
    // growing squares, the whole square rescanned each time, z ascending then x ascending
    let found = false;
    for (let r = 0; r < 100 && !found; r++) {
      for (let z = mz - r; z <= mz + r && !found; z++) {
        if (z < 0 || z >= m.h) continue;
        for (let x = mx - r; x <= mx + r; x++) {
          if (x < 0 || x >= m.w) continue;
          if (famAt(G, x, z) !== f) continue;
          w8(kai, zoneAddr(f) + Z.CX, x);
          w8(kai, zoneAddr(f) + Z.CZ, z);
          found = true;
          break;
        }
      }
    }
  }
}

/** Pool update rule of the influence map: same owner adds, a stronger newcomer takes the zone. */
function poolAdd(kai, za, ownerOff, strOff, team, s) {
  const owner = i8(kai, za + ownerOff);
  const str = u16(kai, za + strOff);
  if (owner === team) {
    w16(kai, za + strOff, str + s);
    return;
  }
  if (owner !== -1) w8(kai, za + Z.FLAGS, u8(kai, za + Z.FLAGS) | ZF_CONTESTED);
  if (s >= str) {
    w16(kai, za + strOff, s - str);
    w8(kai, za + ownerOff, team);
  } else w16(kai, za + strOff, str - s);
}

/** krusty_general 0x456818: the influence map over the zone table (DC16_AI.md §8). No rand(), no commands. */
export function krustyGeneral(ctx) {
  const { G, p, kai } = ctx;
  const gs = G.gs;
  // 1. reset
  for (let z = 0; z < NZONES; z++) {
    const za = zoneAddr(z);
    w8(kai, za + Z.AA_OWNER, -1);
    w16(kai, za + Z.AA_STR, 0);
    w8(kai, za + Z.G_OWNER, -1);
    w16(kai, za + Z.G_STR, 0);
    w8(kai, za + Z.AIR_OWNER, -1);
    w16(kai, za + Z.AIR_STR, 0);
    w16(kai, za + Z.BUILDINGS, 0);
    let flags = u8(kai, za + Z.FLAGS) & ~ZF_CONTESTED;
    if (flags & ZF_MARKED) {
      // 0x456830: for every see_thru slot the test uses player p's OWN mask; the bit goes when p sees
      // the centre, and the zone counts as one building this think either way (0x4568A2)
      let any = false;
      for (let q = 0; q < 8; q++) if (u8(kai, K.SEE_THRU + q)) any = true;
      if (any && seenByPlayer(G, u8(kai, za + Z.CX), u8(kai, za + Z.CZ), p)) flags &= ~ZF_MARKED;
      w16(kai, za + Z.BUILDINGS, 1);
    }
    w8(kai, za + Z.FLAGS, flags);
  }
  // 2. objects 0..799 (0x456E24): the only entry filter is the live team byte < 8 (0x456E57);
  // free slots enter too, that is how the memory is forgotten and remembered objects still count
  for (let o = 0; o < MAX_OBJECTS; o++) {
    const a = objAddr(o);
    const liveTeam = u8(gs, a + O.TEAM);
    if (liveTeam >= 8) continue;
    const life = u8(gs, a + O.LIFE);
    const liveType = u8(gs, a + O.TYPE);
    const [tx, tz] = tileOf(gs, a); // zero-extended words >> 8
    const ma = memAddr(o);
    const hidden = (OT(G, liveType)?.hidden ?? 0) !== 0;
    const detected = u8(gs, a + O.DETECTED);
    let seen = false;
    for (let t = 0; t < 8; t++) {
      if (!u8(kai, K.SEE_THRU + t)) continue;
      if (tx >= G.map.w || tz >= G.map.h || !seenByPlayer(G, tx, tz, t)) continue;
      if (hidden && t !== liveTeam && !(detected & (1 << t))) continue;
      if (life === 0) {
        w8(kai, ma + 2, 0xff); // a free slot in view: forget (0x456958), next team
        continue;
      }
      seen = true;
    }
    let x, z, type, team;
    if (seen) {
      x = tx;
      z = tz;
      type = liveType;
      team = liveTeam;
      if (team !== p) {
        w8(kai, ma, tx);
        w8(kai, ma + 1, tz);
        w8(kai, ma + 2, type);
        w8(kai, ma + 3, team);
      }
    } else {
      type = i8(kai, ma + 2);
      if (type === -1) continue;
      x = u8(kai, ma);
      z = u8(kai, ma + 1);
      if (x >= G.map.w || z >= G.map.h) continue;
      if (seenByPlayer(G, x, z, p)) {
        w8(kai, ma + 2, 0xff); // our own eyes show the remembered tile: forget
        continue;
      }
      team = u8(kai, ma + 3);
    }
    if (team !== p && team < 10 && u8(gs, GS.ALLIANCE + 10 * p + team) !== 0) continue; // allies (0x456B82)
    let zone = famAt(G, x, z);
    if (zone === 0) {
      const [fx, fz] = Scenario.findFreeCell(G, x, z, 0); // 0x41B68C; the result is not re-tested for 0
      zone = famAt(G, fx, fz);
    }
    const za = zoneAddr(zone);
    const t = OT(G, type);
    if (!t) continue;
    if (t.weapon[0] === -1) {
      if (liveTeam === p) continue; // 0x456C32: the LIVE team byte, not the remembered one
      if (t.defenceClass === 8) continue;
      w16(kai, za + Z.BUILDINGS, i16(kai, za + Z.BUILDINGS) + 1);
      continue;
    }
    const wc = G.tables.weapons[t.weapon[0]].weaponClass;
    const dc = t.defenceClass;
    const div = dc === 2 ? 50 : MB(G, 1, dc);
    const gnd = idiv(25 * MB(G, wc, 1), div);
    const aa = MB(G, wc, 2);
    if (t.fly !== 0 && gnd > 0) poolAdd(kai, za, Z.AIR_OWNER, Z.AIR_STR, team, gnd);
    if (gnd > 0) poolAdd(kai, za, Z.G_OWNER, Z.G_STR, team, gnd);
    if (aa > 0) poolAdd(kai, za, Z.AA_OWNER, Z.AA_STR, team, aa);
  }
  // 3. contested: the non-empty owners of a zone disagree
  for (let z = 0; z < NZONES; z++) {
    const za = zoneAddr(z);
    const owners = [i8(kai, za + Z.AA_OWNER), i8(kai, za + Z.G_OWNER), i8(kai, za + Z.AIR_OWNER)].filter((v) => v !== -1);
    if (owners.some((v) => v !== owners[0])) w8(kai, za + Z.FLAGS, u8(kai, za + Z.FLAGS) | ZF_CONTESTED);
  }
}

// ---- goals (DC16_AI.md §10) ----------------------------------------------------------------------

/** The troop items player p may buy now: [{ item, type, cost }] (dep_check_troop == 1), item order. */
function buyableTroops(G, p) {
  const out = [];
  for (let i = 0; i < G.tables.depend.length; i++) {
    const r = City.depCheckTroop(G, p, i);
    if (r.status === 1) out.push({ item: i, type: r.type, cost: r.cost });
  }
  return out;
}

/** Building kinds 6 (weapon) and 7 (armour) are the upgrade goals; `which` of the 0x0C command. */
const UPGRADE_KIND = Object.freeze({ 6: 0, 7: 1 });

/**
 * FIX: the upgrade items (DEPEND kind 2: a = unit type, b = which, c = level) the player may buy now
 * for `which`: active, not disabled, the type's race is the player's, the level not yet reached and
 * every dependency built. Sorted by how many of that unit type the player fields (most first), then
 * by item index. Empty in exact mode.
 */
export function buyableUpgrades(G, p, which) {
  const gs = G.gs;
  const pa = playerAddr(p);
  const race = i32(gs, pa + P.RACE);
  const fielded = new Int32Array(130);
  const maxObj = i32(gs, GS.MAX_OBJ);
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    if (u8(gs, a + O.TEAM) !== p || !alive(u8(gs, a + O.LIFE))) continue;
    fielded[u8(gs, a + O.TYPE)]++;
  }
  const out = [];
  for (let i = 0; i < G.tables.depend.length; i++) {
    const it = G.tables.depend[i];
    if (!it || !it.active || it.kind !== 2 || it.b !== which) continue;
    if (u8(gs, pa + P.DISABLED + i) !== 0) continue;
    const t = OT(G, it.a);
    if (!t || t.race !== race) continue;
    const level = which === 1 ? t.armourLevel[p] & 0xff : t.weaponLevel[p] & 0xff;
    if (level >= it.c) continue;
    let ok = true;
    for (let d = 0; d < 5 && ok; d++) {
      const dp = it.deps[d];
      if (dp === -1) break;
      if (City.depCheckBuilding(G, p, dp).status !== 0) ok = false;
    }
    if (!ok) continue;
    out.push({ item: i, type: it.a, which, level: it.c, cost: it.cost, fielded: fielded[it.a] });
  }
  return out.sort((x, y) => y.fielded - x.fielded || x.item - y.item);
}

const GOAL_CHECK = [
  // 0 workers 0x45618C: fewer than param workers
  (ctx, have, param) => have[6] < param,
  // 1 building 0x456228: buildable, or its slot is blocked for the scenario (then the chain stops)
  (ctx, have, param) => {
    const { G, p } = ctx;
    if (ctx.fixes && param in UPGRADE_KIND) return buyableUpgrades(G, p, UPGRADE_KIND[param]).length > 0;
    const item = BUILDING_KIND[param][i32(G.gs, playerAddr(p) + P.RACE) ? 1 : 0];
    return City.depCheckBuilding(G, p, item).status === 1 || City.slotBlocked(G, p, item);
  },
  // 2 army 0x4563C0: never at the unit cap; fires below param fighting units
  (ctx, have, param) => {
    const { G, p } = ctx;
    if (G.stat(6, p) >= i32(G.gs, GS.UNIT_CAP)) return false;
    return have[0] + have[2] + have[3] + have[4] + have[5] < param;
  },
  // 3 / 4 always
  () => true,
  () => true,
];

const GOAL_ACTION = [
  // 0 build worker 0x4561A8
  (ctx) => {
    const { G, p } = ctx;
    for (const it of buyableTroops(G, p)) {
      if (unitClass(it.type) !== 6) continue;
      if (money(G, p) < it.cost) return;
      spend(G, p, it.cost);
      sendBuildUnits(ctx, it.type, 1);
      ctx.say(`Training a ${OT(G, it.type).name.toLowerCase()} (worker).`);
    }
  },
  // 1 build building 0x4562B0
  (ctx, have, param) => {
    const { G, p } = ctx;
    if (ctx.fixes && param in UPGRADE_KIND) {
      // FIX: buy the upgrade for the unit type the player fields most of (0x0C which type level player)
      const up = buyableUpgrades(G, p, UPGRADE_KIND[param])[0];
      if (!up || money(G, p) < up.cost) return;
      spend(G, p, up.cost);
      ctx.emit([build.upgrade(up.which, up.type, up.level, p)]);
      ctx.say(`${up.which ? 'Armour' : 'Weapon'} upgrade ${up.level} for ${OT(G, up.type).name.toLowerCase()}.`);
      return;
    }
    const item = BUILDING_KIND[param][i32(G.gs, playerAddr(p) + P.RACE) ? 1 : 0];
    const cost = City.itemCost(G, item);
    if (cost > money(G, p)) return;
    if (City.slotBlocked(G, p, item)) return;
    spend(G, p, cost);
    const r = City.depCheckBuilding(G, p, item);
    ctx.assert(r.status === 1, 'dep_check_building(...) == 1 (krusty_general.c build)');
    if (r.status !== 1) return;
    sendBuildBuilding(ctx, r.slot, r.level);
    ctx.say(`Building slot ${r.slot} level ${r.level}.`);
  },
  // 2 build unit 0x456408: the class with the smallest have*weight among the buildable items
  (ctx, have) => {
    const { G, p, kai } = ctx;
    const score = new Array(NCLASSES).fill(0);
    for (let c = 0; c < NCLASSES; c++) {
      if (c === 6) score[c] = 10000;
      else if (c !== 8) score[c] = have[c] * i32(kai, K.WEIGHTS + 4 * WEIGHT_INDEX[c]);
    }
    let best = null;
    let bestScore = 10000; // 0x456414: class 6 (constant 10000) and anything at or above it are never chosen
    for (const it of buyableTroops(G, p)) {
      const c = unitClass(it.type);
      if (c === 8) continue;
      if (bestScore > score[c]) {
        best = it;
        bestScore = score[c];
      }
    }
    if (!best) return;
    ctx.assert(City.depCheckTroop(G, p, best.item).status === 1, 'dep_check_troop(...) == 1 (krusty_general.c army)');
    if (money(G, p) < best.cost) return;
    spend(G, p, best.cost);
    sendBuildUnits(ctx, best.type, 1);
    ctx.say(`Training a ${OT(G, best.type).name.toLowerCase()}.`);
  },
  // 3 / 4 nothing
  () => {},
  () => {},
];

/** run_goals 0x457614: the first goal whose check fires runs its action; no upper bound (quirk kept, guarded at 32). */
function runGoals(ctx, have) {
  const { kai } = ctx;
  for (let g = 0; ; g++) {
    if (g >= MAX_GOALS) {
      // the original would run into the tunables; a scenario that removed the terminator is a bug there
      ctx.assert(false, 'run_goals: no terminating goal (krusty_attack.c)');
      return;
    }
    const a = K.GOALS + 12 * g;
    const check = i32(kai, a);
    const action = i32(kai, a + 4);
    const param = i32(kai, a + 8);
    if (!(check >= 0 && check <= 4)) return; // uninitialised slot (pool memory) - nothing sensible to do
    // 0x457650: a check returning non-zero means "goal satisfied, next"; GOAL_CHECK returns true when the goal FIRES
    if (!GOAL_CHECK[check](ctx, have, param)) continue;
    GOAL_ACTION[action](ctx, have, param);
    return;
  }
}

// ==================================================================================================
// krusty_attack.c: census, demand, attack task
// ==================================================================================================

const FIRST_CENSUS_OBJ = 120; // 0x4572DF / 0x45749B: both census passes start at object 120 (city slots are never units)

/**
 * krusty_census 0x4572AC (DC16_AI.md §9): count the free own units per class, fix the routing
 * (workers -> 0, tower builders -> 1, scouts or stand-in infantry -> 3), split the rest between
 * defend and attack, then hand every free unit to the first task with quota for its class.
 */
export function census(ctx) {
  const { G, p, kai } = ctx;
  const gs = G.gs;
  const count = new Int32Array(NCLASSES);
  const assign = [new Int32Array(NCLASSES), new Int32Array(NCLASSES), new Int32Array(NCLASSES), new Int32Array(NCLASSES)];
  const isFree = (a) => {
    if (u8(gs, a + O.TEAM) !== p) return false;
    if (i16(gs, a + OA.NEXT) !== NO_GROUP) return false;
    const life = u8(gs, a + O.LIFE);
    return life !== 0 && life !== 10;
  };
  // pass 1: free units per class, deployed towers (41/42) left out
  for (let o = FIRST_CENSUS_OBJ; o < MAX_OBJECTS; o++) {
    const a = objAddr(o);
    if (!isFree(a)) continue;
    const type = u8(gs, a + O.TYPE);
    if (type === 41 || type === 42) continue;
    count[unitClass(type)]++;
  }
  // fixed routing
  assign[0][6] = count[6];
  count[6] = 0;
  assign[1][1] = count[1];
  count[1] = 0;
  const have = (t, c) => u16(kai, haveAddr(t, c));
  if (SCOUT_ITEMS.some((it) => City.depCheckTroop(G, p, it).status === 1)) {
    assign[3][5] = count[5];
    count[5] = 0;
  } else if (count[5] > 0) {
    assign[3][5] = count[5];
    count[5] = 0;
  } else if (count[0] > 0) {
    // no scout can be built: the free infantry stands in while 4*have3 <= have1 + have2 (loop-invariant: all or nothing)
    const a = have(1, 0);
    const b = have(2, 0);
    const c = have(3, 0);
    while (4 * c <= a + b) {
      if (count[0] <= 0) break;
      count[0]--;
      assign[3][0]++;
    }
  }
  // split (0x457421): defend while split*have1 <= (256-split)*have2, else attack; have[] bumped provisionally
  const split = i32(kai, K.SPLIT);
  for (let c = 0; c < NCLASSES; c++) {
    for (let n = count[c]; n !== 0; n--) {
      const h1 = haveAddr(1, c);
      const h2 = haveAddr(2, c);
      if (split !== 0x100 && split * u16(kai, h1) <= (0x100 - split) * u16(kai, h2)) {
        assign[1][c]++;
        w16(kai, h1, u16(kai, h1) + 1);
      } else {
        assign[2][c]++;
        w16(kai, h2, u16(kai, h2) + 1);
      }
    }
  }
  // pass 2: hand out (no 41/42 filter here: a free deployed tower takes class-1 quota, as the original)
  for (let o = FIRST_CENSUS_OBJ; o < MAX_OBJECTS; o++) {
    const a = objAddr(o);
    if (!isFree(a)) continue;
    const cls = unitClass(u8(gs, a + O.TYPE));
    for (let t = 0; t < NTASKS; t++) {
      if (assign[t][cls] === 0) continue;
      const m = TASK_CALLBACKS[t].take(ctx, t, cls);
      assign[t][cls]--;
      link(gs, kai, o, t, m);
      break;
    }
  }
}

/** krusty_demand 0x457684: census, have[] over the tasks and the production queues, then the goals. */
export function krustyDemand(ctx) {
  const { G, p, kai } = ctx;
  const gs = G.gs;
  census(ctx);
  const have = new Array(NCLASSES).fill(0);
  for (let t = 0; t < NTASKS; t++) {
    TASK_CALLBACKS[t].recount(ctx, t);
    for (let c = 0; c < NCLASSES; c++) have[c] += i16(kai, haveAddr(t, c));
  }
  const pa = playerAddr(p);
  for (let k = 0; k < 4; k++) {
    const len = gs.readUInt16LE(pa + P.QUEUE_LEN + 2 * k);
    for (let i = 0; i < len; i++) have[unitClass(u8(gs, pa + P.QUEUE + 800 * k + i))]++;
  }
  runGoals(ctx, have);
}

/**
 * route_threat 0x457EA4(gs, kai, from, to, p, route?): A = the zones of the route from -> to (the
 * routing chain, or the given route - then `from` is ignored) plus their neighbours; B = A plus the
 * neighbours of A; the sum of the ground strength of every zone in B whose ground owner is neither
 * -1 nor p. -1 when `from` is 0 or the chain hits a 0 entry.
 */
export function routeThreat(ctx, from, to, route = null, p = ctx.p) {
  const { G, kai } = ctx;
  const A = new Uint8Array(NZONES);
  const mark = (z) => {
    A[z] = 1;
    for (let k = adjCount(G, z); k >= 1; k--) A[adj(G, z, k)] = 1;
  };
  if (!route) {
    let cur = from & 0xff;
    if (cur === 0) return -1;
    for (;;) {
      mark(cur);
      if ((to & 0xff) === cur) break;
      cur = nextHop(G, cur, to & 0xff);
      if (cur === 0) return -1;
    }
  } else {
    for (let i = 0; ; i++) {
      if (i >= NZONES || i >= route.length) {
        ctx.assert(false, 'j<NZONES (krusty_attack.c:263)');
        break;
      }
      const cur = route[i] & 0xff;
      mark(cur);
      if (cur === (to & 0xff)) break;
    }
  }
  const B = Uint8Array.from(A);
  for (let z = 1; z < NZONES; z++) {
    if (!A[z]) continue;
    for (let k = adjCount(G, z); k >= 1; k--) B[adj(G, z, k)] = 1;
  }
  let sum = 0;
  for (let z = 0; z < NZONES; z++) {
    if (!B[z]) continue;
    const owner = i8(kai, zoneAddr(z) + Z.G_OWNER);
    if (owner === -1 || owner === p) continue;
    sum += u16(kai, zoneAddr(z) + Z.G_STR);
  }
  return sum;
}

/**
 * group_strength 0x4583AC: over the arrived units (ai_status 1) of a group, per class c
 * 25 * count[c] * MB[class(weapon0 of TYPE c)][1] / (OT(c).defenceClass == 2 ? 50 : MB[1][OT(c).defenceClass]),
 * a signed division per class - the original's class-number-as-object-type bug kept (the docs'
 * "gs+0x40" was a misreading: the divisor index is OT(c)+0x40).
 */
export function groupStrength(ctx, t, m) {
  const { G, kai } = ctx;
  const gs = G.gs;
  const count = new Int32Array(NCLASSES);
  for (const o of listOf(gs, kai, t, m)) {
    if (u8(gs, objAddr(o) + OA.STATUS) !== 1) continue;
    count[unitClass(u8(gs, objAddr(o) + O.TYPE))]++;
  }
  let total = 0;
  for (let c = 0; c < NCLASSES; c++) {
    const ot = OT(G, c);
    const w = ot.weapon[0];
    if (w === -1) continue;
    const v = MB(G, G.tables.weapons[w].weaponClass, 1);
    const num = 25 * (count[c] * v);
    const den = ot.defenceClass === 2 ? 50 : MB(G, 1, ot.defenceClass);
    total += idiv(num, den);
  }
  return total;
}

/**
 * choose_target 0x458540(gs, kai, excluded, maxh, p, strength, from, route_out): the zone (0..255)
 * with the highest strength * score / threat, strictly better than 0 and than every earlier one.
 * The avoiding-route branch (0x4579F0) is reached only when kai+0x6C34 * threat > strength; the
 * ratio is 0 in every game that does not script `aimsg 13`, so it is not ported (assert, TODO(exact)).
 */
export function chooseTarget(ctx, excluded, maxh, strength, from, routeOut) {
  const { G, kai } = ctx;
  let best = -1;
  let bestScore = 0;
  for (let z = 0; z < NZONES; z++) {
    if (excluded[z]) continue;
    const za = zoneAddr(z);
    const flags = u8(kai, za + Z.FLAGS);
    const hop = u8(kai, za + Z.HOP);
    const e = routeThreat(ctx, z, z);
    let score = 0;
    if (hop <= maxh && (e !== 0 || (flags & ZF_CONTESTED))) score = flags & ZF_ATTACK_HERE ? idiv(maxh, 2) : maxh + 1 - hop;
    if (u16(kai, za + Z.BUILDINGS) !== 0) score += idiv(maxh, 2); // added even when the hop test failed
    if (score === 0) continue;
    let e2 = routeThreat(ctx, from, z);
    if (e2 === 0) e2 = 1; // -1 (no route) stays -1 and makes the final score negative
    if (i32(kai, K.RATIO) * e2 > strength) {
      ctx.assert(false, 'avoiding_route 0x4579F0 not ported (kai+0x6C34 != 0)');
      continue;
    }
    const fin = idiv(strength * score, e2);
    if (fin <= bestScore) continue;
    bestScore = fin;
    best = z;
    routeOut.length = 0;
    let cur = from & 0xff;
    for (let n = 0; n < NZONES; n++) {
      routeOut.push(cur);
      if (cur === z) break;
      cur = nextHop(G, cur, z);
      if (cur === 0) break; // TODO(exact): the original has no bound check here
    }
  }
  return best;
}

/** pick_defend_zone 0x458224(gs, A, C, from): the defend destination with the fewest attack groups (C[z] < 100), then the nearest by hops. */
function pickDefendZone(ctx, A, C, from) {
  let bestC = 100;
  let bestH = 256;
  let best = -1;
  for (let z = 0; z < NZONES; z++) {
    if (!A[z]) continue;
    const h = hops(ctx.G, z, from);
    const c = C[z] & 0xff;
    if (bestC > c || (bestC === c && h < bestH)) {
      bestH = h;
      best = z;
      bestC = c;
    }
  }
  ctx.assert(best !== -1, 'best_slot!=-1 (krusty_attack.c:346)');
  return best;
}

/** attack_plan 0x458864 (DC16_AI.md §13). Deterministic. The three group-index-as-zone-index reads are kept. */
export function attackPlan(ctx, t) {
  const { kai } = ctx;
  const A = new Uint8Array(NZONES);
  let maxh = 0;
  for (let g = 0; g < NMINORS; g++) {
    const md = minorAddr(1, g); // the defend task, hard-coded
    if (!u8(kai, md + MN.ACTIVE)) continue;
    const d = i32(kai, md + MN.DEST) & 0xff;
    A[d] = g === 0 ? 1 : 2;
    const h = u8(kai, zoneAddr(d) + Z.HOP);
    if (h > maxh) maxh = h;
  }
  maxh += 3;
  const B = new Uint8Array(NZONES);
  const C = new Uint8Array(NZONES);
  let active = 0;
  for (let g = 0; g < NMINORS; g++) {
    const mn = minorAddr(t, g);
    if (!u8(kai, mn + MN.ACTIVE)) continue;
    active++;
    const state = u8(kai, mn + MN.STATE);
    const idx = ctx.fixes ? i32(kai, mn + MN.DEST) & 0xff : g; // FIX: the destination zone, not the group index
    if (state === 0) B[idx] = 1; // 0x45897A: the GROUP index in the original (bug)
    if (state === 3 || state === 1) C[idx]++; // 0x45898F: likewise
  }
  for (let m = 0; m < NMINORS; m++) {
    const mn = minorAddr(t, m);
    if (!u8(kai, mn + MN.ACTIVE)) continue;
    const state = u8(kai, mn + MN.STATE);
    const zone = i32(kai, mn + MN.ZONE);
    const dest = i32(kai, mn + MN.DEST);
    if (state === 2 || state === 3) {
      const s = groupStrength(ctx, t, m);
      const route = [];
      const best = chooseTarget(ctx, B, maxh, s, zone, route);
      if (best !== -1) {
        B[best] = 1; // the zone index here
        w8(kai, mn + MN.STATE, 0);
        setRoute(ctx, t, m, best, route);
        ctx.say(`Attack group ${m} marches on zone ${best} (${centreOf(kai, best).join(',')}).`);
      } else if (state === 2) {
        w8(kai, mn + MN.STATE, 3);
        const d = pickDefendZone(ctx, A, C, zone);
        if (d !== -1) setRoute(ctx, t, m, d, null);
        C[i32(kai, mn + MN.DEST) & 0xff]++; // the destination after set_route
      }
    } else if (state === 0) {
      const s = groupStrength(ctx, t, m);
      const step = i16(kai, mn + MN.STEP);
      const route = [];
      for (let i = step; i < NZONES; i++) route.push(u8(kai, mn + MN.ROUTE + i));
      const e = routeThreat(ctx, zone, dest, route);
      const zm = zoneAddr(ctx.fixes ? dest & 0xff : m); // the group index as a zone index in the original (bug); FIX: the destination
      if (e === 0 && !(u8(kai, zm + Z.FLAGS) & ZF_CONTESTED) && u16(kai, zoneAddr(dest & 0xff) + Z.BUILDINGS) === 0) {
        w8(kai, mn + MN.STATE, 2);
      } else if (2 * s <= e) w8(kai, mn + MN.STATE, 2);
    } else if (state === 1) {
      // 0x458B0C: every class must hold at least its share (have[c] / ((4*active)/(m+1))) and at least 3
      let ok = true;
      const share = idiv(4 * active, m + 1);
      for (let c = 0; c < NCLASSES; c++) {
        const h = u16(kai, haveAddr(t, c));
        const q = share === 0 ? 0 : idiv(h, share); // TODO(exact): the original divides by zero here
        const cnt = u16(kai, mn + MN.COUNT + 2 * c);
        if (q > cnt || cnt < 3) ok = false;
      }
      if (ok) w8(kai, mn + MN.STATE, 2);
    }
  }
}

/** attack_take 0x458C5C: score = count[class] (x4 unless state 1) x (m+1); smallest wins, tie -> fewer units. */
function attackTake(ctx, t, cls) {
  const { kai } = ctx;
  let best = -1;
  let bestScore = 0;
  let bestTotal = 0;
  for (let m = 0; m < NMINORS; m++) {
    const mn = minorAddr(t, m);
    if (!u8(kai, mn + MN.ACTIVE)) continue;
    let score = i16(kai, mn + MN.COUNT + 2 * cls);
    if (u8(kai, mn + MN.STATE) !== 1) score *= 4;
    score *= m + 1;
    let total = 0;
    for (let c = 0; c < NCLASSES; c++) total += i16(kai, mn + MN.COUNT + 2 * c);
    if (best < 0 || score < bestScore || (score === bestScore && total < bestTotal)) {
      best = m;
      bestScore = score;
      bestTotal = total;
    }
  }
  if (best < 0) best = 0;
  const mn = minorAddr(t, best);
  w16(kai, mn + MN.COUNT + 2 * cls, i16(kai, mn + MN.COUNT + 2 * cls) + 1);
  w16(kai, haveAddr(t, cls), i16(kai, haveAddr(t, cls)) + 1);
  return best;
}

/** attack init 0x458E54: task_init_common, then groups 0 and 1 active in state 2 (need a target). */
function attackInit(kai, t) {
  taskInitCommon(kai, t);
  for (const m of [0, 1]) {
    newMinor(kai, t, m);
    w8(kai, minorAddr(t, m) + MN.STATE, 2); // attack_minor_start 0x458C0C
  }
}

// ==================================================================================================
// krusty_defend.c
// ==================================================================================================

/** add_defend_zone 0x458EC0 / remove_defend_zone 0x458FC0 (aimsg 6/7). */
function addDefendZone(G, kai, zone) {
  for (let i = 0; i < 16; i++) {
    const a = K.DEFZONES + 2 * i;
    if (u8(kai, a)) continue;
    w8(kai, a, 1);
    w8(kai, a + 1, zone);
    return;
  }
  G.assert(false, 'defend zone list full (krusty_defend.c)');
}
function removeDefendZone(G, kai, zone) {
  for (let i = 0; i < 16; i++) {
    const a = K.DEFZONES + 2 * i;
    if (u8(kai, a) && u8(kai, a + 1) === zone) {
      w8(kai, a, 0);
      return;
    }
  }
  G.assert(false, 'defend zone not found (krusty_defend.c)');
}

/**
 * defend_update 0x4590BC (DC16_AI.md §12). rand(): the 1/16 gate every think, then one per zone
 * with hop < 2; k grows only when a candidate is ACCEPTED (0x4590EC), so a rejected candidate
 * leaves the next one its 1/k chance.
 */
export function defendUpdate(ctx, t) {
  const { G, kai } = ctx;
  if ((ctx.rand() & 0xf) === 0) {
    let k = 1;
    let choice = -1;
    for (let z = 1; z < 255; z++) {
      if (u8(kai, zoneAddr(z) + Z.HOP) >= 2) continue;
      if (irem(ctx.rand(), k) === 0) {
        k++;
        choice = z;
      }
    }
    ctx.assert(choice !== -1, 'found_pos!=-1 (krusty_defend.c:62)');
    if (choice !== -1) {
      setRoute(ctx, t, 0, choice, null);
      G.globals.krustyReissue = 1; // byte 0x49941C, consumed by the first move_group after it
    }
  }
  // the zones worth a group: the vents the worker task's units head for (kai+0x1FAA, task 0 group 0,
  // hard-coded) plus the scripted defend zones
  const set = new Uint8Array(NZONES);
  for (const o of listOf(G.gs, kai, 0, 0)) {
    const z = u8(G.gs, objAddr(o) + OA.ZONE);
    if (z !== 0) set[z] = 1;
  }
  for (let i = 0; i < 16; i++) if (u8(kai, K.DEFZONES + 2 * i)) set[u8(kai, K.DEFZONES + 2 * i + 1)] = 1;
  // groups 1..15: disband those whose destination left the set; every examined destination is
  // cleared, so a second group for the same zone is disbanded too (group 0 is never examined)
  for (let m = 1; m < NMINORS; m++) {
    const mn = minorAddr(t, m);
    if (!u8(kai, mn + MN.ACTIVE)) continue;
    const d = i32(kai, mn + MN.DEST) & 0xff;
    if (!set[d]) disband(ctx, t, m);
    set[d] = 0;
  }
  for (let z = 0; z < NZONES; z++) {
    if (!set[z]) continue;
    let m = -1;
    for (let k = 1; k < NMINORS; k++) {
      if (!u8(kai, minorAddr(t, k) + MN.ACTIVE)) {
        m = k;
        break;
      }
    }
    if (m < 0) continue; // all fifteen slots busy: silently nothing
    newMinor(kai, t, m);
    setRoute(ctx, t, m, z, null);
    ctx.say(`A guard for zone ${z} (${centreOf(kai, z).join(',')}).`);
  }
}

/** defend_take 0x459370: the active group with the smallest count[class] (doubled for groups != 0), tie -> fewer units. */
function defendTake(ctx, t, cls) {
  const { kai } = ctx;
  let best = -1;
  let bestScore = 10000; // 0x459370: a score of 10000 or more is never chosen (then the assert fires)
  for (let m = 0; m < NMINORS; m++) {
    const mn = minorAddr(t, m);
    if (!u8(kai, mn + MN.ACTIVE)) continue;
    let score = u16(kai, mn + MN.COUNT + 2 * cls);
    if (m !== 0) score *= 2;
    if (score < bestScore) {
      best = m;
      bestScore = score;
      continue;
    }
    if (score !== bestScore) continue;
    let sb = 0;
    let sc = 0;
    for (let c = 0; c < NCLASSES; c++) {
      sb += u16(kai, minorAddr(t, best) + MN.COUNT + 2 * c);
      sc += u16(kai, mn + MN.COUNT + 2 * c);
    }
    if (sc < sb) best = m;
  }
  ctx.assert(best !== -1, 'choice!=-1 (krusty_defend.c:171)');
  if (best < 0) best = 0;
  const mn = minorAddr(t, best);
  w16(kai, haveAddr(t, cls), i16(kai, haveAddr(t, cls)) + 1);
  w16(kai, mn + MN.COUNT + 2 * cls, i16(kai, mn + MN.COUNT + 2 * cls) + 1);
  return best;
}

/** defend init 0x459574: task_init_common, then group 0 = the home guard. */
function defendInit(kai, t) {
  taskInitCommon(kai, t);
  newMinor(kai, t, 0);
}

// ==================================================================================================
// krusty_scout.c: workers and bombers
// ==================================================================================================

/** Stuck tracking shared by the tasks: thinks spent on the same tile (saturating when `cap` is set). */
function trackStuck(gs, a, cap) {
  const [tx, tz] = tileOf(gs, a);
  if (u8(gs, a + OA.TILE_X) === tx && u8(gs, a + OA.TILE_Z) === tz) {
    const s = u8(gs, a + OA.STUCK);
    if (cap === 0 || s < cap) w8(gs, a + OA.STUCK, s + 1);
  } else {
    w8(gs, a + OA.STUCK, 0);
    w8(gs, a + OA.TILE_X, tx);
    w8(gs, a + OA.TILE_Z, tz);
  }
}

/**
 * worker_update 0x4595D0 (DC16_AI.md §11): four passes over group 0's list - stuck tracking and
 * recalls, the last listed object without a target, the zones already targeted, the best vent.
 * `from` zones are the raw path family of the unit's tile (no free-cell fallback: a unit on a
 * family-0 cell makes route_threat return -1, which counts as a threat).
 */
export function workerUpdate(ctx, t) {
  const { G, kai } = ctx;
  const gs = G.gs;
  const list = listOf(gs, kai, t, 0);
  // pass 1 (0x4595E3)
  for (const o of list) {
    const a = objAddr(o);
    trackStuck(gs, a, 0); // byte counter, wraps at 256 like the original's inc
    const type = u8(gs, a + O.TYPE);
    if (!WORKER_TYPES.includes(type)) continue;
    const home = i32(kai, minorAddr(1, 0) + MN.DEST) & 0xff; // kai+0x319C: the defend task's home guard destination
    const [tx, tz] = tileOf(gs, a);
    const wfam = famAt(G, tx, tz);
    if (u8(gs, a + OA.STUCK) > 10) {
      w8(gs, a + OA.ZONE, 0); // 0x4596DF: stuck > 10 drops the target WITHOUT a recall order
      continue;
    }
    const target = u8(gs, a + OA.ZONE);
    if (target === 0) continue;
    if (routeThreat(ctx, wfam, target, null, u8(gs, a + O.TEAM)) === 0) continue;
    const [cx, cz] = centreOf(kai, home);
    sendWaypointOrder(ctx, [o], [[cx << 8, cz << 8]], ORDER_MOVE);
    w8(gs, a + OA.ZONE, 0);
    ctx.say(`Worker ${o} recalled: the way to its vent is not safe.`);
  }
  // pass 2 (0x459781): the LAST listed object (any type) without a target
  let last = -1;
  for (const o of list) if (u8(gs, objAddr(o) + OA.ZONE) === 0) last = o;
  if (last < 0) return;
  // pass 3 (0x45980C): zones already targeted (index 0 included)
  const taken = new Uint8Array(NZONES);
  for (const o of list) taken[u8(gs, objAddr(o) + OA.ZONE)] = 1;
  // pass 4 (0x459885): the vent with the smallest hop distance and a threat-free route
  const la = objAddr(last);
  const [wx, wz] = tileOf(gs, la);
  const wfam = famAt(G, wx, wz);
  const team = u8(gs, la + O.TEAM);
  let best = -1;
  let bestZone = -1;
  let bestHop = 0x100; // an unreachable zone (hop 0xFF) is a legal candidate
  for (let o = 0; o < MAX_OBJECTS; o++) {
    const a = objAddr(o);
    if (u8(gs, a + O.TYPE) !== TYPE_VENT) continue;
    if (i16(gs, a + O_VENT_RATE) === 0) continue;
    const life = u8(gs, a + O.LIFE);
    if (life === 10 || life === 0) continue;
    const [vx, vz] = tileOf(gs, a);
    if (groundIdAt(G, vx, vz) !== 0x3ff) continue;
    const vfam = famAt(G, vx, vz);
    if (taken[vfam]) continue;
    const hop = u8(kai, zoneAddr(vfam) + Z.HOP);
    if (hop >= bestHop) continue;
    if (routeThreat(ctx, wfam, vfam, null, team) !== 0) continue;
    bestHop = hop;
    best = o;
    bestZone = vfam;
  }
  if (bestHop === 0x100) return;
  w8(gs, la + OA.ZONE, bestZone);
  const va = objAddr(best);
  sendWaypointOrder(ctx, [last], [[u16(gs, va + O.X), u16(gs, va + O.Z)]], ORDER_MOVE); // the vent's raw position
  const [vx, vz] = tileOf(gs, va);
  ctx.say(`Worker to the vent at ${vx},${vz}.`);
}

/**
 * bomber_update 0x459CA0 (DC16_AI.md §14): scouts (or stand-in infantry) sent one by one. rand()
 * per unit in this order: the 1/64 roll (statuses other than 0/4/5 only), the coin, then either the
 * 1/4 roll plus a pair per tile attempt, or one per candidate vent plus two per wander step.
 */
export function bomberUpdate(ctx, t) {
  const { G, p, kai } = ctx;
  const gs = G.gs;
  const m = G.map;
  for (const o of listOf(gs, kai, t, 0)) {
    const a = objAddr(o);
    const status = u8(gs, a + OA.STATUS);
    const idle = u8(gs, a + OA.BOTTOM_STATE) === 1;
    let act = false;
    if (status === 4 || status === 0) act = idle;
    else if (status === 5) {
      if (idle) w8(gs, a + OA.STATUS, 6);
    } else {
      // 0x459D4D..0x459DD9: dead reads of the unit's zone owners (results discarded)
      ctx.assert(status === 6, 'ai_status == OBJ_BOMBING (krusty_scout.c:67)');
      if ((ctx.rand() & 0x3f) === 0) act = true;
      if (u8(gs, a + OA.RETARGET) !== 0) act = true;
    }
    w8(gs, a + OA.RETARGET, 0);
    if (!act) continue;
    let dx = -1;
    let dz = -1;
    let needRandom = true;
    if ((ctx.rand() & 1) === 0) {
      // a zone with enemy ground strength and no enemy anti-air within two hops (zones 1..255)
      let bestZ = 0;
      let bestScore = 0;
      for (let za = 1; za < NZONES; za++) {
        const visited = new Uint8Array(NZONES);
        visited[za] = 1;
        for (let c = adjCount(G, za); c >= 1; c--) visited[adj(G, za, c)] = 1;
        let reject = false;
        for (let k = 0; k < NZONES && !reject; k++) {
          if (!visited[k]) continue;
          for (let c = adjCount(G, k); c >= 1; c--) {
            const aa = i8(kai, zoneAddr(adj(G, k, c)) + Z.AA_OWNER);
            if (aa === p || aa === -1) continue;
            reject = true;
            break;
          }
        }
        if (reject) continue;
        const zr = zoneAddr(za);
        let score = 0;
        const gOwner = i8(kai, zr + Z.G_OWNER);
        if (gOwner !== p && gOwner !== -1) score = u16(kai, zr + Z.G_STR);
        const airOwner = i8(kai, zr + Z.AIR_OWNER);
        if (airOwner !== p && airOwner !== -1) score -= u16(kai, zr + Z.AIR_STR);
        if (i16(kai, zr + Z.BUILDINGS) !== 0) score++;
        if (score > bestScore) {
          bestZ = za;
          bestScore = score;
        }
      }
      if (bestScore !== 0) {
        [dx, dz] = centreOf(kai, bestZ);
        needRandom = false;
        w8(gs, a + OA.STATUS, 5);
      }
    }
    if (needRandom) {
      if ((ctx.rand() & 3) !== 0) {
        // 3/4: a random passable tile (both draws repeat on every attempt)
        do {
          dx = irem(ctx.rand(), m.w);
          dz = irem(ctx.rand(), m.h - 5);
        } while (famAt(G, dx, dz) === 0);
        w8(gs, a + OA.STATUS, 4);
      } else {
        // 1/4: a live vent (no rate test, objects 0..799), then wander away from enemy air
        let n = 0;
        let vx = -1;
        let vz = -1;
        for (let j = 0; j < MAX_OBJECTS; j++) {
          const va = objAddr(j);
          if (u8(gs, va + O.TYPE) !== TYPE_VENT) continue;
          const life = u8(gs, va + O.LIFE);
          if (life === 10 || life === 0) continue;
          n++;
          const r = ctx.rand() & 0xff;
          if (idiv(0x100, n) <= r) continue;
          [vx, vz] = tileOf(gs, va);
        }
        if (n === 0) {
          do {
            dx = irem(ctx.rand(), m.w);
            dz = irem(ctx.rand(), m.h - 5);
          } while (famAt(G, dx, dz) === 0);
          // ai_status unchanged, no wander
        } else {
          ctx.assert(vx !== -1, '0 (krusty_scout.c:190)'); // cannot fail: the first candidate is always taken
          dx = vx;
          dz = vz;
          w8(gs, a + OA.STATUS, 4);
          let guard = 0;
          for (;;) {
            const f = famAt(G, vx, vz);
            if (f === 0) break; // the previously accepted point is sent
            let sum = 0;
            const ao = i8(kai, zoneAddr(f) + Z.AIR_OWNER);
            if (ao !== p && ao !== -1) sum = ao; // the owner ID is summed, not a count (quirk: player 0 counts as none)
            for (let c = adjCount(G, f); c >= 1; c--) {
              const bo = i8(kai, zoneAddr(adj(G, f, c)) + Z.AIR_OWNER);
              if (bo !== p && bo !== -1) sum += bo;
            }
            if (sum === 0) {
              dx = vx;
              dz = vz;
              break;
            }
            vx += (ctx.rand() & 0x0f) - 8;
            vz += (ctx.rand() & 0x0f) - 8;
            if (vx < 0) vx = 0;
            if (vz < 0) vz = 0;
            if (vx >= m.w) vx = m.w - 1;
            if (vz >= m.h) vz = m.h - 1;
            if (++guard > 100000) break; // TODO(exact): the original has no bound
          }
        }
      }
    }
    const order = SCOUT_TYPES.includes(u8(gs, a + O.TYPE)) ? ORDER_MOVE : ORDER_ASSAULT;
    sendWaypointOrder(ctx, [o], [[dx << 8, dz << 8]], order);
  }
}

// ==================================================================================================
// krusty_army.c: groups and their movement
// ==================================================================================================

/**
 * set_route 0x46B770(gs, kai, t, m, dest, route?): route[0] = the group's zone, then the routing
 * chain up to and including dest (no bound check in the original; an unreachable dest would
 * overrun route[] - stopped here, TODO(exact)); or the given route copied up to dest (assert
 * count < NZONES). step = 0 when dest is the current zone, else 1. The group's zone is not touched.
 */
export function setRoute(ctx, t, m, dest, route) {
  const { G, kai } = ctx;
  const mn = minorAddr(t, m);
  const zone = i32(kai, mn + MN.ZONE);
  if (!route) {
    let i = 0;
    let cur = zone & 0xff;
    for (;;) {
      w8(kai, mn + MN.ROUTE + i, cur);
      i++;
      if (cur === (dest & 0xff)) break;
      cur = nextHop(G, cur, dest & 0xff);
      if (cur === 0 || i >= NZONES) break; // TODO(exact): the original loops on zone 0 / overruns
    }
  } else {
    for (let i = 0; ; ) {
      const v = route[i] ?? 0;
      w8(kai, mn + MN.ROUTE + i, v);
      if ((v & 0xff) === (dest & 0xff)) break;
      if (++i >= NZONES || i >= route.length) {
        ctx.assert(false, 'count < NZONES (krusty_army.c:34)');
        break;
      }
    }
  }
  w16(kai, mn + MN.STEP, dest === zone ? 0 : 1);
  w32(kai, mn + MN.DEST, dest);
}

/** new_minor 0x46BE18(kai, t, m): activate an empty group at the lowest zone with hop < 3. */
export function newMinor(kai, t, m) {
  const mn = minorAddr(t, m);
  if (u8(kai, mn + MN.ACTIVE)) throw new Error('!minor->active (new_minor)');
  let zone = 1;
  for (;;) {
    if (u8(kai, zoneAddr(zone) + Z.HOP) < 3) break;
    if (++zone >= 255) break; // 255 when no zone qualifies
  }
  w16(kai, mn + MN.STEP, 0);
  w8(kai, mn + MN.ACTIVE, 1);
  w16(kai, mn + MN.HEAD, -1);
  w16(kai, mn + MN.TAIL, -1);
  w32(kai, mn + MN.ZONE, zone);
  w32(kai, mn + MN.DEST, zone);
  w8(kai, mn + MN.ROUTE, zone);
  groupInitList(kai, t, m); // 0x46BF8C: head = tail = -1, active = 1 again
  // state, count[] and route[1..] are NOT cleared (recount zeroes count[] before the next take)
}

/** disband 0x46BF98(gs, kai, t, m): inactive; every unit leaves the group (status 0, links -2). */
export function disband(ctx, t, m) {
  const { G, kai } = ctx;
  const gs = G.gs;
  w8(kai, minorAddr(t, m) + MN.ACTIVE, 0);
  for (const o of listOf(gs, kai, t, m)) {
    const a = objAddr(o);
    w8(gs, a + OA.STATUS, 0);
    w16(gs, a + OA.NEXT, NO_GROUP); // prev, +0x11 and the group's head/tail stay as they are
  }
}

/** task_init_common 0x46C074: the sixteen active bytes cleared (header and have[] untouched), default callbacks. */
function taskInitCommon(kai, t) {
  for (let m = 0; m < NMINORS; m++) w8(kai, minorAddr(t, m) + MN.ACTIVE, 0);
}

/** move_all 0x46BDA4: move_group for every active group. */
export function moveAll(ctx, t) {
  for (let m = 0; m < NMINORS; m++) if (u8(ctx.kai, minorAddr(t, m) + MN.ACTIVE)) moveGroup(ctx, t, m);
}

/** move_group 0x46B984 (DC16_AI.md §15): units follow the group zone; the group advances along its route. */
export function moveGroup(ctx, t, m) {
  const { G, kai } = ctx;
  const gs = G.gs;
  const force = G.globals.krustyReissue ? 1 : 0;
  G.globals.krustyReissue = 0;
  const mn = minorAddr(t, m);
  const zone = i32(kai, mn + MN.ZONE) & 0xff;
  const dest = i32(kai, mn + MN.DEST) & 0xff;
  let travelling = false;
  const list = listOf(gs, kai, t, m);
  for (const o of list) {
    const a = objAddr(o);
    if (u8(gs, a + O.LIFE) === 0) {
      ctx.assert(false, 'My god, jim, this object is dead (move_group)');
      return;
    }
    if (u8(gs, a + OA.STATUS) === 0) w8(gs, a + OA.STATUS, 2);
    trackStuck(gs, a, 60);
    // 0x46BA4B: `sar` of the int16 position - a tile >= 128 reads a bogus row in the original; the
    // port uses the unsigned tile (TODO(exact) for maps wider than 128 tiles in exact mode)
    const [ux, uz] = tileOf(gs, a);
    const unitZone = famAt(G, ux, uz); // raw family: a unit on a family-0 cell is 255 hops away
    const type = u8(gs, a + O.TYPE);
    if (TOWER_BUILDER_TYPES.includes(type) && hops(G, unitZone, zone) < 3 && u8(gs, a + OA.STUCK) > 3) {
      sendOrder(ctx, o, ORDER_DEPLOY);
      w8(gs, a + OA.STUCK, 0);
      continue;
    }
    const h = hops(G, unitZone, zone);
    if (h > 3 || force) {
      if (u8(gs, a + OA.STUCK) < 60 && u8(gs, a + OA.STATUS) !== 2) travelling = true;
      if (u8(gs, a + OA.ZONE) !== zone) {
        w8(gs, a + OA.ZONE, zone);
        const [cx, cz] = centreOf(kai, zone);
        sendWaypointOrder(ctx, [o], [[cx << 8, cz << 8]], u8(gs, a + OA.STATUS) === 2 ? ORDER_ASSAULT : ORDER_MOVE);
      }
    } else if (u8(gs, a + OA.STATUS) === 2) w8(gs, a + OA.STATUS, 1);
  }
  if (travelling) return;
  const step = i16(kai, mn + MN.STEP);
  const nz = u8(kai, mn + MN.ROUTE + step);
  if (nz !== dest && step < NZONES - 1) w16(kai, mn + MN.STEP, step + 1);
  w32(kai, mn + MN.ZONE, nz);
  const going = [];
  for (const o of list) {
    const a = objAddr(o);
    if (u8(gs, a + OA.ZONE) !== nz) {
      going.push(o);
      w8(gs, a + OA.ZONE, nz);
    }
  }
  const [cx, cz] = centreOf(kai, nz);
  sendWaypointOrder(ctx, going, [[cx << 8, cz << 8]], ORDER_ASSAULT);
}

// ==================================================================================================
// diagnostics
// ==================================================================================================

/** A readable snapshot of the state for logs and tests. */
export function summarize(G, kai) {
  const gs = G.gs;
  const tasks = [];
  for (let t = 0; t < NTASKS; t++) {
    const groups = [];
    for (let m = 0; m < NMINORS; m++) {
      const mn = minorAddr(t, m);
      if (!u8(kai, mn + MN.ACTIVE)) continue;
      groups.push({ m, zone: i32(kai, mn + MN.ZONE), dest: i32(kai, mn + MN.DEST), state: u8(kai, mn + MN.STATE), units: listOf(gs, kai, t, m).length });
    }
    const have = [];
    for (let c = 0; c < NCLASSES; c++) have.push(i16(kai, haveAddr(t, c)));
    tasks.push({ t, have, groups });
  }
  let zones = 0;
  for (let z = 1; z < 255; z++) if (u8(kai, zoneAddr(z) + Z.HOP) !== 0xff) zones++;
  return { zones, tasks, split: i32(kai, K.SPLIT) };
}

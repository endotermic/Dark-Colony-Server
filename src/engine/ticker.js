// ticker.js — port of ticker.c and the idle/order half of mobiles.c of Classic dc16.exe.
//
// PORT NOTES (11 Sep 2026, from dc16.asm; every function below was ported instruction by
// instruction unless marked TODO(exact)):
//
//   ported exactly
//     0x412114 pushState, 0x4122A4 popState, 0x41233C resetAndDispatchOrder, 0x412414 turnTowards,
//     0x4124B8 dirTo, 0x4124F0 setCooldown, 0x412510 stateCooldown (0xB), 0x41258C sleepState,
//     0x4125E0 stateSleep (3), 0x412650 pushTurn, 0x412670 stateTurn (4), 0x41296C pushIdle,
//     0x4129C0 chooseStep, 0x412B38 chooseStepRandom, 0x412D68 chooseStepNearTarget,
//     0x412EE0 nudgeWalk, 0x413EC4 idleStealer (types 77/78), 0x414B9C stateIdle (1),
//     0x41616C stateMoveDone (2), 0x41617C pushWaypointState, 0x4161A0 orderMove (2),
//     0x416214 stateAssaultDone (7), 0x416224 orderAssault (7), 0x41629C stateAttackDone (0xE),
//     0x4162AC orderAttack (0xE), 0x416330 orderPatrol (9), 0x4163A0 stateWaypoints (8),
//     0x416434 statePatrol (9), 0x4168D8 canDeploy, 0x416A1C orderDeploy (0xD),
//     0x4194CC orderNop, 0x4194D4 stateNop (state 0), 0x4194DC dispatchObject.
//
//   cross-module calls (live bindings, names per PORTING.md; TODO(owner) where the table has no
//   name yet — the integrator wires them):
//     Move.beginMove(G, obj, mode, flag)                  0x414FD0
//     Move.stateStep / Move.stateMove                     states 5 / 6
//     Combat.findTargetInRange(G, obj) -> int             0x435D5C (result taken as int16)
//     Combat.findTarget(G, obj, rings) -> int             0x4356C8
//     Combat.fireWeapon(G, obj, target, tx, tz) -> 0|1    0x413018 via wrapper 0x414B08 (tx = tz = 0)
//     Combat.healNearby(G, obj)                           0x413F24
//     Combat.objectDie(G, obj)                            0x4165A0
//     Combat.stateCorpse / stateWreck / stateFireAtGround / stateDeploy   states 0xA / 0x11 / 0x12 / 0xD
//     Combat.orderFireAtGround(G, obj)                    order 0x12, 0x418160
//     Missile.atan2(dx, dz) -> int (low 16 bits used)     0x4415C0
//     Anim.startAnim(G, slotAddr, anim, flag)             0x42626C
//     Anim.isPlaying(G, slotAddr, facing2) -> bool        0x426294 (TODO(owner anim.js))
//     Anim.advanceAnims(G, obj, facing2)                  0x426428 on ANIM0, ANIM1, ANIM2 in that order
//     Anim.currentAnim(G, slotAddr) -> anim               dword at slot+0 (TODO(owner anim.js))
//     City.stateBuildingIdle(G, obj) -> 0|1               0x41460C (3rd register arg was the object pointer)
//     Renat.pickup(G, obj, kind)                          0x4143D4 (TODO(owner renat.js))
//     Renat.idleVent(G, obj, infoAddr) -> 0|1             0x4137A0, type 40 (TODO(owner renat.js))
//     Renat.idleArtifactSite(G, obj, infoAddr) -> 0|1     0x4134D4, type 37 (TODO(owner renat.js))
//     Renat.stateHarvest / stateF / state10 / state13..state16
//     Grid.cellGround(G, x, z) -> int32, Grid.cellAir(G, x, z) -> int16, Grid.cellSecondary(G, x, z) -> int16
//     Grid.passable(G, x, z) -> bool                      path cell +0xC (family) != 0 (TODO(owner grid.js/path.js))
//     Grid.removeObject(G, obj)                           0x434EB8
//
//   object_types fields are read through TF below (tables.js names; original offsets in comments).
//
//   deliberately reproduced oddities
//     * pushState writes the next level's info offset at +0x3A+2(sp+1); for sp == 5 that byte is
//       +0x46 = the low byte of info[0]. The original does the same (OBJECT_STACK_SIZE = 6).
//     * stateSleep compares info[1] (int16 hp snapshot) with the full int32 hp: units above
//       32767 HP never sleep. Original behaviour.
//     * stateIdle indexes the per-player weapon level byte array (+0x30) with the raw team byte;
//       teams 8/9 read the first two armour-level bytes (+0x38).
//     * chooseStepRandom accepts an occupied cell when the occupant's LIFE byte is exactly 1.
//     * the assert `loop_trap<100` aborts the original program; here the state loop is left
//       after G.assert instead of spinning forever.
//
//   disagreements with docs/DC16_BATTLE_ENGINE.md
//     * §4.2 "every 32 ticks / every 16 ticks": the dispatcher tests gs+0x530 (the day/night
//       counter, reset at each phase flip), not the tick counter.
//     * §4.3 / §13 "0x412650 random heading": 0x412650 only pushes state 4 with the heading in
//       ebx; the idle state draws `rand() & 0xFF` itself (second rand() of the fidget).
//     * §14.4 "diagonal nudge steps require both orthogonal neighbours to be passable": all
//       three step choosers accept the diagonal when EITHER (nx,tz) OR (tx,nz) is passable.
//     * §4.2 table: state 0 is the no-op 0x4194D4 (returns 0), not a renat.c handler; the
//       state-table JSON agrees.
//     * §4.1 "pop_state: sp--": it also asserts the object is alive ("state pop on dead or
//       rotting object.").
//     * §14.1 says immobile types "reset to idle" on move/assault/patrol orders: they call
//       reset_and_dispatch_order (the pending flag is already clear, so it ends in pushIdle);
//       the attack order pushes state 0xE first and only then resets.
//     * §4.3 step 7: the turn state is pushed BEFORE the sleep state, so the unit sleeps first
//       and turns when the sleep ends.
//
// Signatures: state handlers (G, obj, infoAddr) => 0|1, order handlers (G, obj) => void,
// exactly as PORTING.md prescribes. `infoAddr` is the absolute gs offset of info[0].

import { GS, O, P, u8, i8, i16, u16, i32, w8, w16, w32, objAddr, playerAddr, idiv, irem, sx16 } from './mem.js';
import * as Move from './move.js';
import * as Combat from './combat.js';
import * as Missile from './missile.js';
import * as Anim from './anim.js';
import * as City from './city.js';
import * as Renat from './renat.js';
import * as Grid from './grid.js';

// gs offsets not yet in mem.js (integrator: move to mem.js GS)
const GS_BYTE_948 = 0x948; // byte, non-zero disables the deploy order of types 0x2F/0x30 (not identified)

export const NUM_STATES = 23; // LAST_STAT
export const OBJECT_STACK_SIZE = 6;
export const OBJECT_INFO_SIZE = 32;

/** object_types record of `type` (tables.js). */
const T = (G, type) => G.tables.types[type];

/** Field accessors of an object_types record; the original offset of each field is noted. */
export const TF = {
  turn: (t) => t.turnSpeed, // +0x08 turn speed (heading units per tick)
  speed: (t) => t.speed, // +0x0C move speed (0 = immobile)
  weapon: (t, lvl) => t.weapon[lvl], // +0x18 + 4*lvl weapon index for upgrade level lvl (-1 unarmed)
  // +0x30 byte[8] weapon level per player; indexed with the raw team byte in stateIdle, so teams 8/9
  // fall through into the armour-level bytes at +0x38 (exactly what the binary does)
  weaponLevel: (t, team) => (team < 8 ? t.weaponLevel[team] : t.armourLevel[team - 8]),
  flying: (t) => t.fly, // +0x60 byte (1; 7 for DOTT)
  animStand: (t) => t.stand, // +0x80 STAND sprite
  animDeploy: (t) => t.deploy, // +0x94 DEPLOY sprite
  animFunk: (t) => t.funk, // +0x9C FUNK sprite
  animBlood: (t, i) => t.blood[i], // +0xBC + 4*i
  numBlood: (t) => t.numBlood, // +0xD8
  standStill: (t) => t.standStill, // +0xDC column 20: no fidget turn
  chargeRegen: (t) => t.chargeRegen, // +0xF8 special-charge regeneration per 32 counter ticks
  commanderBonus: (t) => t.rallyBonus, // +0xFC
};

const dir8 = (G) => G.consts.dir8.values;

// ---- stack primitives (ticker.c) ----------------------------------------------------------------

/** 0x412114 push_state(gs, obj, state, nwords): returns the absolute gs offset of the new info block. */
export function pushState(G, obj, state, nwords) {
  const gs = G.gs;
  const a = objAddr(obj);
  if (state !== 0x0a) {
    const life = u8(gs, a + O.LIFE);
    G.assert(life !== 0 && life !== 10, 'state push on dead or rotting object.'); // ticker.c:140
  }
  w8(gs, a + O.SP, u8(gs, a + O.SP) + 1);
  const sp = i8(gs, a + O.SP); // mov edi,[esi+35h]; sar edi,18h
  G.assert(sp < OBJECT_STACK_SIZE, 'pos<OBJECT_STACK_SIZE'); // ticker.c:145
  const infoOff = u8(gs, a + O.STACK + 1 + 2 * sp);
  G.assert(infoOff < OBJECT_INFO_SIZE, 'optr->stack[pos].info_stack_ptr<OBJECT_INFO_SIZE'); // ticker.c:146
  w8(gs, a + O.STACK + 2 * sp, state);
  w8(gs, a + O.STACK + 1 + 2 * (sp + 1), infoOff + nwords); // next level starts after this block
  return a + O.INFO + 2 * infoOff;
}

/** 0x4122A4 pop_state(gs, obj) */
export function popState(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  const life = u8(gs, a + O.LIFE);
  G.assert(life !== 0 && life !== 10, 'state pop on dead or rotting object.'); // ticker.c:160
  w8(gs, a + O.SP, u8(gs, a + O.SP) - 1);
}

/** 0x41233C reset_and_dispatch_order(gs, obj): empty the stack, run the pending order or push idle. */
export function resetAndDispatchOrder(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  const life = u8(gs, a + O.LIFE);
  G.assert(life !== 0 && life !== 10, "If you can read this message I'm not paranoid after all. Andy."); // ticker.c:177
  w8(gs, a + O.STACK, 0);
  w8(gs, a + O.STACK + 1, 0);
  const pending = u8(gs, a + O.PENDING);
  w8(gs, a + O.SP, 0xff);
  if (pending !== 0) {
    const code = u8(gs, a + O.ORDER);
    w8(gs, a + O.PENDING, 0);
    const handler = ORDER_TABLE[code];
    G.assert(handler !== undefined, `order code ${code} outside order_table`);
    if (handler) handler(G, obj);
    w8(gs, a + O.ORDER, 0xff);
  } else {
    pushIdle(G, obj);
  }
}

/** 0x41296C push_idle(gs, obj): state 1 with info {target = -1, hp snapshot, fidget = 0}. */
export function pushIdle(G, obj) {
  const gs = G.gs;
  const info = pushState(G, obj, 1, 3);
  w16(gs, info, -1);
  w16(gs, info + 4, 0);
  w16(gs, info + 2, i16(gs, objAddr(obj) + O.HP)); // low word of the int32 hit points
}

/** 0x41258C sleep(gs, obj, n): state 3 with info {n, hp snapshot}. */
export function sleepState(G, obj, n) {
  const gs = G.gs;
  const info = pushState(G, obj, 3, 2);
  w16(gs, info, n);
  w16(gs, info + 2, i16(gs, objAddr(obj) + O.HP));
}

/** 0x4124F0 set_cooldown(gs, obj, n): state 0xB with info {n}. */
export function setCooldown(G, obj, n) {
  const info = pushState(G, obj, 0x0b, 1);
  w16(G.gs, info, n);
}

/**
 * 0x412650 (docs call it "random heading"): pushes state 4 with the target heading in info[0].
 * The heading itself is chosen by the caller (stateIdle draws rand() & 0xFF; start_step 0x4126A0
 * passes the step direction).
 */
export function pushTurn(G, obj, heading) {
  const info = pushState(G, obj, 4, 1);
  w16(G.gs, info, heading);
}

// ---- turning and directions (mobiles.c) --------------------------------------------------------

/** 0x412414 turn(gs, obj, target): rotate by at most type.turn towards `target`; 1 while not aligned. */
export function turnTowards(G, obj, target) {
  const gs = G.gs;
  const a = objAddr(obj);
  const rate = TF.turn(T(G, u8(gs, a + O.TYPE)));
  const heading = u8(gs, a + O.HEADING);
  if (heading !== target) {
    let d = target - heading;
    if (d > 0x80) d -= 0x100;
    if (d < -0x80) d += 0x100;
    if (d < 0) {
      d = -d;
      if (d < rate) w8(gs, a + O.HEADING, target);
      else w8(gs, a + O.HEADING, heading - rate);
    } else if (d < rate) w8(gs, a + O.HEADING, target);
    else w8(gs, a + O.HEADING, heading + rate);
  }
  return u8(gs, a + O.HEADING) !== target ? 1 : 0;
}

/** 0x4124B8 direction(obj*, x, z): heading 0..255 from the object towards (x, z). */
export function dirTo(G, obj, x, z) {
  const gs = G.gs;
  const a = objAddr(obj);
  const dx = x - u16(gs, a + O.X); // xor ecx,ecx; mov cx,[eax]: zero-extended
  const dz = z - u16(gs, a + O.Z);
  const r = sx16(Missile.atan2(dx, dz)); // movsx edx,ax
  return idiv(r, 32) & 0xff; // sar/sbb sequence = idiv by 32
}

// ---- simple states ------------------------------------------------------------------------------

/** state 3, 0x4125E0: sleep info[0] ticks; wakes on a pending order or when the hit points changed. */
export function stateSleep(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  if (u8(gs, a + O.PENDING) !== 0 || i16(gs, info + 2) !== i32(gs, a + O.HP)) {
    popState(G, obj);
    return 1;
  }
  const n = i16(gs, info);
  if (n === 0) {
    popState(G, obj);
    return 0;
  }
  w16(gs, info, n - 1);
  return 0;
}

/** state 4, 0x412670: turn towards info[0]; pop (and rerun) once aligned. */
export function stateTurn(G, obj, info) {
  if (turnTowards(G, obj, i16(G.gs, info)) === 0) {
    popState(G, obj);
    return 1;
  }
  return 0;
}

/** state 0xB, 0x412510: weapon cooldown; STAND once the fire animation ended, count info[0] down. */
export function stateCooldown(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  if (u8(gs, a + O.ANIM0_STATUS) === 2) {
    Anim.startAnim(G, a + O.ANIM0, TF.animStand(T(G, u8(gs, a + O.TYPE))), 0);
  }
  const n = i16(gs, info);
  if (n === 0) {
    popState(G, obj);
    return 0;
  }
  w16(gs, info, n - 1);
  return 0;
}

/** state 0, 0x4194D4: no-op, returns 0. */
export function stateNop() {
  return 0;
}

/** order no-op 0x4194CC */
export function orderNop() {}

// ---- nudging (mobiles.c 0x4129C0..0x413018) ------------------------------------------------------

/**
 * Shared body of 0x4129C0 (offsets = nudgeC, 7 candidates) and 0x412D68 (offsets = nudgeD, 5
 * candidates): first free (ground or air grid, depending on `flying`), passable neighbour of
 * (tx, tz) around the nudge direction; a diagonal needs one passable orthogonal neighbour.
 * Returns the direction 0..7 or -1.
 */
function chooseStepFromTable(G, tx, tz, nd, flying, offsets, count) {
  const c = G.consts;
  const d8 = dir8(G);
  const nudgeA = c.nudgeA.values;
  const nudgeB = c.nudgeB.values;
  for (let i = 0; i < count; i++) {
    const d = nudgeA[(nudgeB[nd] + offsets[i]) & 7];
    const nx = tx + d8[2 * d];
    const nz = tz + d8[2 * d + 1];
    if (nx < 0 || nz < 0) continue;
    if (nx >= G.map.w || nz >= G.map.h) continue;
    if (flying === 0) {
      if ((Grid.cellGround(G, nx, nz) & 0x3ff) !== 0x3ff) continue;
    } else if ((Grid.cellAir(G, nx, nz) & 0x3ff) !== 0x3ff) continue;
    if (!Grid.passable(G, nx, nz)) continue;
    if (d8[2 * d] === 0 || d8[2 * d + 1] === 0) return d;
    if (Grid.passable(G, nx, tz)) return d;
    if (Grid.passable(G, tx, nz)) return d;
  }
  return -1;
}

/** 0x4129C0 step choice for nudge mode 1: perpendicular offsets first (±2, ±1, ±3, 0). */
export function chooseStep(G, tx, tz, nd, flying) {
  return chooseStepFromTable(G, tx, tz, nd, flying, G.consts.nudgeC.values, 7);
}

/** 0x412D68 step choice for nudge mode 2 (idle unit with a target): offsets 0, ±1, ±2. */
export function chooseStepNearTarget(G, tx, tz, nd, flying) {
  return chooseStepFromTable(G, tx, tz, nd, flying, G.consts.nudgeD.values, 5);
}

/**
 * 0x412B38 random step choice (fallback of mode 1): the seven nudgeC offsets in a rand()-driven
 * order (one rand() per candidate); passability first, then the grid cell, which may be occupied
 * by an object whose LIFE byte is 1.
 */
export function chooseStepRandom(G, tx, tz, nd, flying) {
  const gs = G.gs;
  const c = G.consts;
  const d8 = dir8(G);
  const nudgeA = c.nudgeA.values;
  const nudgeB = c.nudgeB.values;
  const nudgeC = c.nudgeC.values;
  const perm = [0, 1, 2, 3, 4, 5, 6];
  for (let k = 0; k < 7; k++) {
    const r = G.rand();
    const j = irem(r, 7 - k) + k;
    const pick = perm[j];
    perm[j] = perm[k];
    perm[k] = pick;
    const d = nudgeA[(nudgeB[nd] + nudgeC[pick]) & 7];
    const nx = tx + d8[2 * d];
    const nz = tz + d8[2 * d + 1];
    if (nx < 0 || nz < 0) continue;
    if (nx >= G.map.w || nz >= G.map.h) continue;
    if (!Grid.passable(G, nx, nz)) continue;
    const v = flying !== 0 ? Grid.cellAir(G, nx, nz) & 0x3ff : Grid.cellGround(G, nx, nz) & 0x3ff;
    if (v !== 0x3ff && v !== 0x3fe) {
      if (u8(gs, objAddr(v) + O.LIFE) !== 1) continue;
    }
    if (d8[2 * d] === 0 || d8[2 * d + 1] === 0) return d;
    if (Grid.passable(G, nx, tz)) return d;
    if (Grid.passable(G, tx, nz)) return d;
  }
  return -1;
}

/**
 * 0x412EE0 nudge walk (from stateIdle while obj+0x35 != 0xFF): mode 1 = no target (0x4129C0,
 * then 0x412B38), mode 2 = target in sight (0x412D68). Clears the nudge, sets the destination to
 * the chosen tile centre and starts begin_move(0). Always returns 1.
 */
export function nudgeWalk(G, obj, mode) {
  const gs = G.gs;
  const a = objAddr(obj);
  const tx = u16(gs, a + O.X) >> 8;
  const tz = u16(gs, a + O.Z) >> 8;
  let r = -1;
  if (mode === 1 || mode === 2) {
    const flying = (TF.flying(T(G, u8(gs, a + O.TYPE))) << 24) >> 24; // dword at +0x5D, sar 24
    const nd = u8(gs, a + O.NUDGE);
    if (mode === 1) {
      r = chooseStep(G, tx, tz, nd, flying);
      if (r === -1) r = chooseStepRandom(G, tx, tz, nd, flying);
    } else {
      r = chooseStepNearTarget(G, tx, tz, nd, flying);
    }
  }
  w8(gs, a + O.NUDGE, 0xff);
  if (r === -1) return 1;
  const d8 = dir8(G);
  w16(gs, a + O.DEST_X, ((tx + d8[2 * r]) << 8) + 0x80);
  w16(gs, a + O.DEST_Z, ((tz + d8[2 * r + 1]) << 8) + 0x80);
  Move.beginMove(G, obj, 0, 0);
  return 1;
}

// ---- idle state (mobiles.c 0x414B9C) -------------------------------------------------------------

/**
 * 0x413EC4 idle branch of the stealing commandos (types 0x4D/0x4E): info[0] holds the object they
 * work on; unless it is alive and of type 0x2F/0x30 the stack is reset and the deploy order runs.
 */
export function idleStealer(G, obj, info) {
  const gs = G.gs;
  const ta = objAddr(i16(gs, info));
  const life = u8(gs, ta + O.LIFE);
  let ok = false;
  if (life !== 0 && life !== 10) {
    const tt = u8(gs, ta + O.TYPE);
    ok = tt === 0x2f || tt === 0x30;
  }
  if (!ok) {
    resetAndDispatchOrder(G, obj);
    orderDeploy(G, obj);
  }
  return 0;
}

/** state 1, 0x414B9C: idle / guard. info = {target, hp snapshot, fidget count}. */
export function stateIdle(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  // 0x414BCE pending pickup / production orders (+0xCB)
  const pickup = u8(gs, a + O.PICKUP);
  if (pickup === 1) {
    Renat.pickup(G, obj, 0);
    return 0;
  }
  if (pickup === 2) {
    Renat.pickup(G, obj, 1);
    return 0;
  }
  if (u8(gs, a + O.PENDING) !== 0) {
    resetAndDispatchOrder(G, obj);
    return 1;
  }
  const type = u8(gs, a + O.TYPE);
  const t = T(G, type);
  const team = u8(gs, a + O.TEAM);
  const weapon = TF.weapon(t, TF.weaponLevel(t, team));
  if (weapon === -1) {
    // unarmed types (0x414C5C)
    if (type === 0x28) return Renat.idleVent(G, obj, info);
    if (type === 0x25) return Renat.idleArtifactSite(G, obj, info);
    if (type === 0x4d || type === 0x4e) return idleStealer(G, obj, info);
    if (u8(gs, a + O.NUDGE) !== 0xff && TF.speed(t) !== 0) return nudgeWalk(G, obj, 1);
    if (obj < 120) return City.stateBuildingIdle(G, obj);
    if (type === 0x31 || type === 0x32) {
      Combat.healNearby(G, obj);
      return 0;
    }
    // types 6 / 0xE keep their FUNK animation, everybody else shows STAND
    if (!((type === 6 || type === 0x0e) && Anim.currentAnim(G, a + O.ANIM0) === TF.animFunk(t))) {
      Anim.startAnim(G, a + O.ANIM0, TF.animStand(t), 0);
    }
    sleepState(G, obj, 7);
    return 0;
  }
  // armed units (0x414D73)
  const target = sx16(Combat.findTargetInRange(G, obj));
  w16(gs, info, target);
  if (u8(gs, a + O.NUDGE) !== 0xff && TF.speed(t) !== 0) return nudgeWalk(G, obj, target === -1 ? 1 : 2);
  if (target !== -1) {
    // 0x414FAA: fire every tick; fire_weapon turns and pushes the cooldown itself
    w16(gs, info + 4, 0);
    return Combat.fireWeapon(G, obj, target, 0, 0);
  }
  // nothing in range (0x414DEB)
  let grew = 0;
  Anim.startAnim(G, a + O.ANIM0, TF.animStand(t), 0);
  const hp = i32(gs, a + O.HP);
  if (i16(gs, info + 2) > hp) {
    w16(gs, info + 4, 0);
    grew = 1;
  }
  w16(gs, info + 2, hp);
  if (TF.speed(t) !== 0) {
    let found;
    if (team >= 8 || i32(gs, playerAddr(team) + P.AI_TYPE) !== 0) found = Combat.findTarget(G, obj, 16);
    else if (TF.flying(t) !== 0) found = -1;
    else found = Combat.findTarget(G, obj, grew !== 0 ? 9 : 4);
    if (found !== -1) {
      w16(gs, info + 4, 0);
      const ta = objAddr(found);
      w16(gs, a + O.DEST_X, i16(gs, ta + O.X));
      w16(gs, a + O.DEST_Z, i16(gs, ta + O.Z));
      Move.beginMove(G, obj, 2, 0);
      return 0;
    }
  }
  // fidget (0x414F15): rand() #1 for the 1/16 chance, rand() #2 for the heading
  if ((G.rand() & 0x0f) === 0 && TF.standStill(t) === 0) {
    pushTurn(G, obj, G.rand() & 0xff);
  }
  const fidget = i16(gs, info + 4);
  let n;
  if (fidget < 3) {
    w16(gs, info + 4, fidget + 1);
    n = 15;
  } else n = 45;
  sleepState(G, obj, n);
  return 0;
}

// ---- orders and waypoint states (mobiles.c 0x41616C..0x4164BC) ----------------------------------

/** state 2, 0x41616C: end of a move order. */
export function stateMoveDone(G, obj) {
  resetAndDispatchOrder(G, obj);
  return 1;
}

/** state 7, 0x416214: end of an assault order. */
export function stateAssaultDone(G, obj) {
  resetAndDispatchOrder(G, obj);
  return 1;
}

/** state 0xE, 0x41629C: attack order reached its target. */
export function stateAttackDone(G, obj) {
  resetAndDispatchOrder(G, obj);
  return 1;
}

/** 0x41617C: push state 8 with info {index = 0, mode}. */
export function pushWaypointState(G, obj, mode) {
  const gs = G.gs;
  const info = pushState(G, obj, 8, 2);
  w16(gs, info, 0);
  w16(gs, info + 2, mode);
}

/** order 2, 0x4161A0: move along the waypoints (marker state 2 + state 8 mode 0). */
export function orderMove(G, obj) {
  const gs = G.gs;
  if (TF.speed(T(G, u8(gs, objAddr(obj) + O.TYPE))) === 0) {
    resetAndDispatchOrder(G, obj);
    return;
  }
  pushState(G, obj, 2, 0);
  pushWaypointState(G, obj, 0);
}

/** order 7, 0x416224: assault move (marker state 7 + state 8 mode 1). */
export function orderAssault(G, obj) {
  const gs = G.gs;
  if (TF.speed(T(G, u8(gs, objAddr(obj) + O.TYPE))) === 0) {
    resetAndDispatchOrder(G, obj);
    return;
  }
  pushState(G, obj, 7, 0);
  pushWaypointState(G, obj, 1);
}

/** order 9, 0x416330: patrol (state 9 with info {index = 0}). */
export function orderPatrol(G, obj) {
  const gs = G.gs;
  if (TF.speed(T(G, u8(gs, objAddr(obj) + O.TYPE))) === 0) {
    resetAndDispatchOrder(G, obj);
    return;
  }
  const info = pushState(G, obj, 9, 1);
  w16(gs, info, 0);
}

/** order 0xE, 0x4162AC: attack the object in obj+0x30 (state 0xE, then begin_move mode 3). */
export function orderAttack(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  pushState(G, obj, 0x0e, 0);
  if (TF.speed(T(G, u8(gs, a + O.TYPE))) === 0) {
    resetAndDispatchOrder(G, obj);
    return;
  }
  Move.beginMove(G, obj, 3, 0);
}

/** state 8, 0x4163A0: waypoint follower. info = {index, mode}. */
export function stateWaypoints(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  if (u8(gs, a + O.PENDING) !== 0) {
    resetAndDispatchOrder(G, obj);
    return 1;
  }
  const idx = i16(gs, info);
  if (idx >= u8(gs, a + O.WP_COUNT)) {
    w8(gs, a + O.WP_COUNT, 0);
    popState(G, obj);
    return 1;
  }
  w16(gs, a + O.DEST_X, i16(gs, a + O.WAYPOINTS + 4 * idx));
  w16(gs, a + O.DEST_Z, i16(gs, a + O.WAYPOINTS + 2 + 4 * idx));
  w16(gs, info, idx + 1);
  Move.beginMove(G, obj, i16(gs, info + 2), 0);
  return 1;
}

/** state 9, 0x416434: patrol — waypoint follower wrapping around, always mode 1. info = {index}. */
export function statePatrol(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  if (u8(gs, a + O.PENDING) !== 0) {
    resetAndDispatchOrder(G, obj);
    return 1;
  }
  if (u8(gs, a + O.WP_COUNT) <= i16(gs, info)) w16(gs, info, 0);
  const idx = i16(gs, info);
  w16(gs, a + O.DEST_X, i16(gs, a + O.WAYPOINTS + 4 * idx));
  w16(gs, a + O.DEST_Z, i16(gs, a + O.WAYPOINTS + 2 + 4 * idx));
  w16(gs, info, idx + 1);
  Move.beginMove(G, obj, 1, 0);
  return 1;
}

// ---- deploy order (mobiles.c 0x4168D8 / 0x416A1C) -----------------------------------------------

/** Types that may always deploy (0x4168D8 list). */
const DEPLOY_ANYWHERE = new Set([
  0x31, 0x32, 0x45, 0x49, 0x46, 0x4a, 0x47, 0x4b, 0x48, 0x4c, 0x04, 0x0c, 0x4d, 0x4e, 0x3f, 0x40, 0x41, 0x44, 0x69,
]);

/** 0x4168D8: deploying is refused within Manhattan distance 7 of any player's city origin. */
export function canDeploy(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  if (DEPLOY_ANYWHERE.has(u8(gs, a + O.TYPE))) return 1;
  const tx = u16(gs, a + O.X) >> 8;
  const tz = u16(gs, a + O.Z) >> 8;
  for (let p = 0; p < 8; p++) {
    const cx = i32(gs, playerAddr(p) + P.CITY_X);
    const cz = i32(gs, playerAddr(p) + P.CITY_Z);
    if (cx === 0 || cz === 0) continue;
    let dx = tx - cx;
    let dz = tz - cz;
    if (dx < 0) dx = -dx;
    if (dz < 0) dz = -dz;
    if (dx + dz < 7) return 0;
  }
  return 1;
}

/** order 0xD, 0x416A1C: deploy / undeploy / transform. */
export function orderDeploy(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  if (canDeploy(G, obj) === 0) {
    pushIdle(G, obj);
    return;
  }
  const type = u8(gs, a + O.TYPE);
  const t = T(G, type);
  let ok = type === 1 || type === 9 || type === 0x2b || type === 0x2c;
  if (!ok && u8(gs, GS_BYTE_948) === 0) ok = type === 0x2f || type === 0x30;
  if (!ok) ok = type === 0x3f || type === 0x40 || type === 0x42 || type === 0x41 || type === 0x43;
  if (!ok) ok = TF.commanderBonus(t) !== 0 && u8(gs, a + O.CHARGE) >= 0x20;
  if (!ok) ok = type === 0x0c || type === 4 || type === 0x4e || type === 0x4d;
  if (!ok) {
    pushIdle(G, obj);
    return;
  }
  // 0x416B04
  if (type === 0x2b || type === 0x2c) {
    // mine layers: refuse when the secondary layer of the own tile is taken
    const sec = Grid.cellSecondary(G, u16(gs, a + O.X) >> 8, u16(gs, a + O.Z) >> 8) & 0x3ff;
    if (sec !== 0x3ff) {
      pushIdle(G, obj);
      return;
    }
  }
  if (type === 0x40) {
    pushState(G, obj, 0x11, 0);
    return;
  }
  Anim.startAnim(G, a + O.ANIM0, TF.animDeploy(t), 1);
  // 0x416B96: message 0x431F60(gs, type, 5, 1, x, z) for the local player: display only
  const info = pushState(G, obj, 0x0d, 1);
  w16(gs, info, 0x32);
}

// ---- tables ---------------------------------------------------------------------------------------

/** state_table 0x48942C[23], index = state id. */
export const STATE_TABLE = [
  stateNop, // 0x00 0x4194D4
  stateIdle, // 0x01 0x414B9C
  stateMoveDone, // 0x02 0x41616C
  stateSleep, // 0x03 0x4125E0
  stateTurn, // 0x04 0x412670
  (G, obj, info) => Move.stateStep(G, obj, info), // 0x05 0x4128D4
  (G, obj, info) => Move.stateMove(G, obj, info), // 0x06 0x415A94
  stateAssaultDone, // 0x07 0x416214
  stateWaypoints, // 0x08 0x4163A0
  statePatrol, // 0x09 0x416434
  (G, obj, info) => Combat.stateCorpse(G, obj, info), // 0x0A 0x4166F8
  stateCooldown, // 0x0B 0x412510
  (G, obj, info) => Renat.stateHarvest(G, obj, info), // 0x0C 0x413A88
  (G, obj, info) => Combat.stateDeploy(G, obj, info), // 0x0D 0x417DA4
  stateAttackDone, // 0x0E 0x41629C
  (G, obj, info) => Renat.stateF(G, obj, info), // 0x0F 0x416DC0
  (G, obj, info) => Renat.state10(G, obj, info), // 0x10 0x4177AC
  (G, obj, info) => Combat.stateWreck(G, obj, info), // 0x11 0x4167E0
  (G, obj, info) => Combat.stateFireAtGround(G, obj, info), // 0x12 0x4182C0
  (G, obj, info) => Renat.state13(G, obj, info), // 0x13 0x418A7C
  (G, obj, info) => Renat.state14(G, obj, info), // 0x14 0x418978
  (G, obj, info) => Renat.state15(G, obj, info), // 0x15 0x418CE0
  (G, obj, info) => Renat.state16(G, obj, info), // 0x16 0x418650
];

/** order_table 0x4893D4[22], index = order byte of commands 0x05 / 0x16. */
export const ORDER_TABLE = [
  orderNop, // 0x00 0x4194CC
  pushIdle, // 0x01 0x41296C
  orderMove, // 0x02 0x4161A0
  orderNop, // 0x03
  orderNop, // 0x04
  orderNop, // 0x05
  orderNop, // 0x06
  orderAssault, // 0x07 0x416224
  orderNop, // 0x08
  orderPatrol, // 0x09 0x416330
  orderNop, // 0x0A
  orderNop, // 0x0B
  orderNop, // 0x0C
  orderDeploy, // 0x0D 0x416A1C
  orderAttack, // 0x0E 0x4162AC
  orderNop, // 0x0F
  orderNop, // 0x10
  orderNop, // 0x11
  (G, obj) => Combat.orderFireAtGround(G, obj), // 0x12 0x418160
  orderNop, // 0x13
  orderNop, // 0x14
  orderNop, // 0x15
];

// ---- per-object dispatcher (ticker.c 0x4194DC) -----------------------------------------------------

/**
 * 0x4194DC: called by game_tick for every allocated object. Order:
 *   1. life 0/10 -> selection mask 0
 *   2. (gs+0x530 & 0x1F) == 0: charge += type regen, saturating at 255
 *   3. (gs+0x530 & 0x0F) == 0: berserk--, link--; game type 1/2: flying units (index >= 120,
 *      alive) of a player without HQ lose 5 HP, die at 0 (object_die + grid remove)
 *   4. blood overlay slot idle and +0xC7 set: rand() % numBlood picks the overlay; +0xC7 = 0
 *   5. advance the three animations with the facing index
 *   6. +0x10 low 5 bits countdown
 *   7. state loop: state_table[stack[sp]](gs, obj, &info) while it returns non-zero
 */
export function dispatchObject(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = T(G, u8(gs, a + O.TYPE));
  const facing2 = ((u8(gs, a + O.HEADING) + 8) & 0xf0) >> 3; // ((heading + 8) & 0xFF) >> 4 << 1
  let loopCount = 0;
  const life = u8(gs, a + O.LIFE);
  if (life === 0 || life === 10) w8(gs, a + O.SELECT, 0);
  const counter = i32(gs, GS.DN_COUNTER);
  if ((counter & 0x1f) === 0) {
    const regen = TF.chargeRegen(t);
    const charge = u8(gs, a + O.CHARGE);
    if (charge < 0xff - regen) w8(gs, a + O.CHARGE, charge + regen);
    else w8(gs, a + O.CHARGE, 0xff);
  }
  if ((counter & 0x0f) === 0) {
    const berserk = u8(gs, a + O.BERSERK);
    if (berserk !== 0) w8(gs, a + O.BERSERK, berserk - 1);
    const link = u8(gs, a + O.LINK_COUNT);
    if (link !== 0) w8(gs, a + O.LINK_COUNT, link - 1);
    const gameType = G.scenario ? G.scenario.gameType : 0; // [gs+0x544]+0x14F0
    if (gameType === 1 || gameType === 2) {
      const team = u8(gs, a + O.TEAM);
      if (
        team < 8 &&
        i32(gs, playerAddr(team) + P.SLOT_HP) === 0 &&
        TF.flying(t) !== 0 &&
        obj >= 120 &&
        life !== 10 &&
        life !== 0
      ) {
        let hp = i32(gs, a + O.HP) - 5;
        if (hp < 0) hp = 0;
        w32(gs, a + O.HP, hp);
        if (hp === 0) {
          Combat.objectDie(G, obj);
          Grid.removeObject(G, obj);
        }
      }
    }
  }
  // 0x41964B blood overlay
  if (!Anim.isPlaying(G, a + O.ANIM1, facing2) && u8(gs, a + O.DAMAGED) !== 0) {
    const numBlood = TF.numBlood(t);
    if (numBlood > 0) {
      const r = G.rand(); // rand() consumed for the blood sprite
      Anim.startAnim(G, a + O.ANIM1, TF.animBlood(t, irem(r, numBlood)), 1);
    }
    w8(gs, a + O.DAMAGED, 0);
  }
  Anim.advanceSlots(G, obj, facing2); // 0x426428 on +0x14, +0x1C, +0x24 (the blood start above is 0x41964B)
  const attacked = u8(gs, a + O.ATTACKED);
  const count = attacked & 0x1f;
  if (count > 0) w8(gs, a + O.ATTACKED, (attacked & 0xe0) | (count - 1));
  // 0x4196E8 state loop
  for (;;) {
    const sp = i8(gs, a + O.SP);
    const state = u8(gs, a + O.STACK + 2 * sp);
    if (state >= NUM_STATES) {
      // printf("Whoa batman, sprite %d do unit out of range %d\n") + assert(0), ticker.c:3971
      G.assert(false, `Whoa batman, sprite ${state} do unit out of range ${obj}`);
      return;
    }
    const infoOff = u8(gs, a + O.STACK + 1 + 2 * sp);
    const r = STATE_TABLE[state](G, obj, a + O.INFO + 2 * infoOff);
    loopCount++;
    if (loopCount >= 100) {
      G.assert(false, 'loop_trap<100'); // ticker.c:3976; the original aborts here
      return;
    }
    if (r === 0) return;
  }
}

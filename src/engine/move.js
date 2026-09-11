// mobiles.c movement of Classic dc16.exe: begin_move, the path-following state 6, the one-tile
// slide state 5, blocking and re-routing.
//
// PORT NOTES (11 Sep 2026, from dc16.asm; instruction by instruction unless marked TODO(exact))
//
// Ported: 0x414FD0 beginMove, 0x415454 reroute, 0x41570C handleBlock, 0x415A94 stateMove (state 6,
// mode table 0x415A80 decoded by hand from the raw bytes: [0]=0x415EDA step, [1]=0x415C58,
// [2]=0x415D8A, [3]=0x415C7F, [4]=0x415DB1), 0x4126A0 startStep, 0x4128D4 stateStep (state 5),
// 0x43508C addPosition.
//
// State 6 info block (8 int16 pushed by begin_move; only 7 are written there):
//   [0] +0x0 cursor (nibble index of the next step, len-1 .. -1)   [1] +0x2 len
//   [2] +0x4 current tile x   [3] +0x6 current tile z              [4] +0x8 mode 0..4
//   [5] +0xA blocked/free cell x (-1 none)   [6] +0xC its z         [7] +0xE nibble offset of the
//   spliced re-route (written by reroute only; begin_move leaves the stale value, as the exe does)
// State 5 info block (5 int16): [0] vx, [1] vz (1/256 tile per tick), [2] remaining ticks,
//   [3] old tile x, [4] old tile z.
// Path nibbles: obj+0x86, 16 bytes = 32 steps, nibble i = byte i>>1, low nibble for even i.
//
// Grid writes (ground int32 / air int16 layers of G.map, index z*w+x) are done inline here exactly
// where the exe does them (state_move 0x416097/0x4160D2, start_step 0x41282E/0x412853, re-route
// 0x41556F/0x415587/0x41560B/0x415637); 0x43508C only adds (vx, vz) to the position, so no
// Grid.* call is needed. Trigger ids live in the upper 6 bits of the air layer word
// (0x4160F8: (u16 >> 10) != 0 -> 0x43E610).
//
// rand() call sites (G.rand(), in order): handleBlock "wander" branch only - two calls,
// destination x jitter then z jitter (0x41589F, 0x4158DB), each (rand() % 3 - 1) * 256.
//
// Cross-module calls (live bindings): Ticker.pushState/popState/resetAndDispatchOrder/sleepState/
// pushTurn/dirTo, Combat.findTargetInRange, Combat.fireWeapon, Anim.startAnim, Missile.sincos,
// Renat.triggerEnter (0x43E610, TODO(owner renat.js)); details in the final report.
//
// Disagreements with docs/DC16_BATTLE_ENGINE.md (the code wins):
//  * §14.2 step 3 / "Start moving many squares..." assert: len is capped to 32 BEFORE the compare
//    with 0x8000 (0x41538B..0x4153B9), so the assert can never fire; when the start was not reached
//    len becomes 32 and extract_path returns 0x8000 unheeded (stale nibbles are walked).
//  * §14.2 step 2: begin_move ALWAYS rewrites obj+0x2E/+0x30 to the (clamped) tile centre, not only
//    when the destination was impassable (0x4151DF is reached on every path).
//  * §14.2 step 4: info[7] is not written by begin_move (see above).
//  * §14.1/§14.2: the attack target of mode 3 is the int16 at obj+0x32 (`mov edx,[edx+30h]; sar
//    edx,10h`), not obj+0x30 (mem.js O.DEST_Z comment) - O_TARGET below, TODO(integrator) -> mem.js.
//  * §14.4 handle_block: the walk along the remaining path stops at the first FREE tile after the
//    blocked run (0x415826 `je` on 0x3FF exits), and the re-route search is seeded there; the docs
//    say "to the first occupied tile".
//  * §14.4 "re-plans in mode 1 (wander)": the wander begin_move keeps the old mode (info[4]) and
//    passes flag = 1 (0x41599A `mov ecx,1`).
//  * §14.4 nudge: the blocker gets obj+0x35 = dir when its nudge byte is 0xFF and
//    ALLIANCE[blocker.team][mover.team] != 0 - no "idle / without orders" test in the code.
//  * §14.4 start_step "heading set to the step direction": it pushes the turn state 4 (0x412650)
//    above state 5, so the unit turns at type.turnSpeed per tick before sliding.
//  * §14.4 "vx = sin·speed >> 11, vz = cos·speed >> 11, ticks = distance / speed": the exe pairs
//    the ebx-pointed sincos output (cos, [ebp-4]) with x and the edx-pointed one (sin, [ebp-8])
//    with z, exactly as fire_weapon does (combat.js): vx = trunc(cos*speed / 2048),
//    vz = trunc(sin*speed / 2048), ticks = trunc((cos*dx + sin*dz) / 2048) / speed (idiv).
//    (angle 0 of atan2 0x4415C0 points along +x, so an east step has heading 0.)
//  * §14.4 seen-by bits are set in the GROUND layer for flyers too (0x41288A/0x4128C5), and only
//    for teams < 8; team 8 skips the old-tile clear as well (0x4127E7).
//  * §14.4 "a pending blocked cell is re-routed around unless it was reached": correct, and the
//    return value of that re-route is ignored (0x415C2D).
//  * Positions are read zero-extended (`xor eax,eax; mov ax,...; sar eax,8`), so a 160-tile map
//    (x up to 0x9FFF) works although the fields are stored as int16; u16 reads are used here.

import { GS, O, u8, i16, u16, w8, w16, objAddr, idiv, irem } from './mem.js';
import * as Ticker from './ticker.js';
import * as Combat from './combat.js';
import * as Anim from './anim.js';
import * as Missile from './missile.js';
import * as Renat from './renat.js';
import { findPath, pathLength, extractPath, importObstacles, clearObstacles, rerouteSearch } from './path.js';

/** obj+0x32: target object of the attack order (mode 3). TODO(integrator): move into mem.js O. */
export const O_TARGET = 0x32;
export const PATH_NIBBLES = 32;

// object_types / weapon_types fields (tables.js names; original offsets in the comments)
const T = (G, type) => G.tables.types[type];
const TF = {
  fly: (t) => t.fly, // +0x60 byte (signed byte pushed as the fly argument)
  speed: (t) => t.speed, // +0x0C
  move: (t) => t.move, // +0x7C MOVE sprite
  stand: (t) => t.stand, // +0x80 STAND sprite
  weapon: (t, lvl) => t.weapon[lvl], // +0x18 + 4*lvl
  // +0x30 byte[8] indexed with the raw team byte; teams 8/9 read the armour-level bytes at +0x38
  weaponLevel: (t, team) => (team < 8 ? t.weaponLevel[team] : t.armourLevel[team - 8]),
  specialWeapon: (t) => t.specialWeapon, // +0x110
};
const weaponRange = (G, w) => G.tables.weapons[w].range; // weapon_types +0x14

const dir8 = (G) => G.consts.dir8.values; // 0x489324 {dx, dz} x 8

/** direction nibble `cursor` (>= 0) of the object's path buffer obj+0x86. */
function nibble(gs, a, cursor) {
  return (u8(gs, a + O.PATH + (cursor >> 1)) >> ((cursor & 1) << 2)) & 0xf;
}

/** x86 `sar` division by 2048 with the sbb fix-up = truncation toward zero. */
const trunc11 = (v) => idiv(v, 2048);

// ---- 0x414FD0 ----------------------------------------------------------------------------------

/**
 * begin_move(gs, obj, mode, flag): push state 6 and plan the path from the unit's tile to the
 * destination obj+0x2E/+0x30 (mode 3 with flag 0: the tile of the target object obj+0x32).
 */
export function beginMove(G, obj, mode, flag) {
  const gs = G.gs;
  const map = G.map;
  const g = map.path;
  const a = objAddr(obj);
  const info = Ticker.pushState(G, obj, 6, 8); // 0x415018, before anything else
  const type = u8(gs, a + O.TYPE);
  const fly = TF.fly(T(G, type)) !== 0;
  let dx;
  let dz;
  if (mode !== 3 || flag !== 0) {
    dx = u16(gs, a + O.DEST_X) >> 8;
    dz = u16(gs, a + O.DEST_Z) >> 8;
  } else {
    const ta = objAddr(i16(gs, a + O_TARGET));
    dx = u16(gs, ta + O.X) >> 8;
    dz = u16(gs, ta + O.Z) >> 8;
  }
  const sx = u16(gs, a + O.X) >> 8;
  const sz = u16(gs, a + O.Z) >> 8;
  if (dx < 0) dx = 0;
  if (dz < 0) dz = 0;
  if (dx >= map.w) dx = map.w - 1;
  if (fly) {
    if (dz >= map.h - 2) dz = map.h - 3;
  } else if (dz >= map.h) dz = map.h - 1;

  // impassable destination of a ground unit: nearest passable tile, squares of growing radius,
  // x-major then z (0x415156..0x4151DD)
  if (g.family[g.idx(dx, dz)] === 0 && !fly) {
    search: for (let r = 0; r < 256; r++) {
      for (let x = dx - r; x <= dx + r; x++) {
        if (x < 0 || x >= map.w) continue;
        for (let z = dz - r; z <= dz + r; z++) {
          if (z < 0 || z >= map.h) continue;
          if (g.family[g.idx(x, z)] !== 0) {
            dz = z;
            dx = x;
            break search;
          }
        }
      }
    }
  }
  w16(gs, a + O.DEST_X, (dx << 8) + 0x80);
  w16(gs, a + O.DEST_Z, (dz << 8) + 0x80);

  if (!fly) {
    G.assert(g.family[g.idx(sx, sz)] !== 0, 'state->map->path.paths[ys][xs].family'); // mobiles.c:1695
    G.assert(g.family[g.idx(dx, dz)] !== 0, 'state->map->path.paths[yd][xd].family'); // mobiles.c:1696
  }
  findPath(G, g, dx, dz, sx, sz, fly);
  let len = pathLength(G, g, sx, sz);
  if (len > PATH_NIBBLES) len = PATH_NIBBLES;
  extractPath(G, g, sx, sz, gs, a + O.PATH, 0, PATH_NIBBLES, len);
  // unreachable: len was capped to 32 above (kept as in the exe)
  if (len === 0x8000) G.assert(false, "Start moving many squares... couldn't get there.  Help.  Bailing"); // mobiles.c:1711
  w16(gs, info + 0xa, -1);
  w16(gs, info + 0xc, -1);
  w16(gs, info + 2, len);
  w16(gs, info, len - 1);
  w16(gs, info + 4, sx);
  w16(gs, info + 6, sz);
  w16(gs, info + 8, mode);
}

// ---- 0x415454 ----------------------------------------------------------------------------------

/**
 * re-route(gs, obj, bx, bz, info, offset): search from the free cell (bx, bz) back to the unit's
 * tile with every occupied tile treated as blocked, splice the result in front of the remaining
 * nibbles at `offset`. Returns 1 when no route exists, else 0.
 */
export function reroute(G, obj, bx, bz, info, offset) {
  const gs = G.gs;
  const map = G.map;
  const g = map.path;
  const a = objAddr(obj);
  const type = u8(gs, a + O.TYPE);
  const fly = TF.fly(T(G, type)) !== 0;
  const ux = u16(gs, a + O.X) >> 8;
  const uz = u16(gs, a + O.Z) >> 8;
  G.assert(ux >= 0 && uz >= 0 && ux < map.w && uz < map.h, 'xs>=0 && zs>=0 && xs<state->map->xsize && zs<state->map->ysize'); // mobiles.c:1733
  const ui = uz * map.w + ux;
  // the unit leaves its tile while searching
  if (fly) map.air[ui] |= 0x3ff;
  else map.ground[ui] |= 0x3ff;
  importObstacles(g, map.ground, map.air, map.w, map.h, fly);
  rerouteSearch(G, g, bx, bz, ux, uz, fly);
  // and re-enters it: cell & (obj | ~0x3FF)
  if (fly) map.air[ui] &= obj | 0xfc00;
  else map.ground[ui] &= obj | ~0x3ff;
  clearObstacles(g);
  const len = pathLength(G, g, ux, uz);
  if (len === 0x8000) return 1;
  if (offset + len < PATH_NIBBLES) {
    extractPath(G, g, ux, uz, gs, a + O.PATH, offset, PATH_NIBBLES, len);
    w16(gs, info + 0xe, offset);
    w16(gs, info, len + offset - 1);
    w16(gs, info + 0xa, bx);
    w16(gs, info + 0xc, bz);
  } else {
    extractPath(G, g, ux, uz, gs, a + O.PATH, 0, PATH_NIBBLES, len);
    w16(gs, info, len - 1);
    w16(gs, info + 0xa, bx);
    w16(gs, info + 0xc, bz);
    w16(gs, info + 0xe, 0);
  }
  return 0;
}

// ---- 0x41570C ----------------------------------------------------------------------------------

/**
 * handle_block(gs, obj, blocker, info, fly): the next tile is occupied. Walk the remaining path
 * over the occupied run to the first free tile and re-route there; if the path ends before a free
 * tile: at the destination -> return 1 (caller resets to idle), else jitter the destination by
 * (rand()%3-1) tiles in x and z and begin_move again (same mode, flag 1). A failed re-route nudges
 * the blocker (obj+0x35 = dir, allied only), shows STAND and sleeps 4 ticks. Returns 0 otherwise.
 */
export function handleBlock(G, obj, blocker, info, fly) {
  const gs = G.gs;
  const map = G.map;
  const a = objAddr(obj);
  const ba = objAddr(blocker);
  const D = dir8(G);
  let exhausted = 0;
  let x = i16(gs, info + 4);
  let z = i16(gs, info + 6);
  let cursor = i16(gs, info);
  let dir = -1;
  for (;;) {
    if (cursor === -1) {
      if (u16(gs, a + O.X) >> 8 === x && u16(gs, a + O.Z) >> 8 === z) return 1;
      exhausted = 1;
      break;
    }
    dir = nibble(gs, a, cursor);
    x += D[dir * 2];
    z += D[dir * 2 + 1];
    cursor--;
    const i = z * map.w + x;
    if (!fly) {
      if ((map.ground[i] & 0x3ff) === 0x3ff) break;
    } else if ((map.air[i] & 0x3ff) === 0x3ff) break;
  }
  cursor++;
  if (exhausted === 0) {
    if (reroute(G, obj, x, z, info, cursor) === 0) return 0;
  } else {
    const mode = i16(gs, info + 8);
    Ticker.popState(G, obj);
    // wander: jitter the destination by -1..+1 tile, two rand() calls (x then z)
    const r1 = G.rand();
    w16(gs, a + O.DEST_X, u16(gs, a + O.DEST_X) + ((irem(r1, 3) << 8) - 0x100));
    const r2 = G.rand();
    w16(gs, a + O.DEST_Z, u16(gs, a + O.DEST_Z) + ((irem(r2, 3) << 8) - 0x100));
    if (u16(gs, a + O.DEST_X) > 0xf000) w8(gs, a + O.DEST_X + 1, u8(gs, a + O.DEST_X + 1) + 1); // inc byte [esi+2Fh]
    if (u16(gs, a + O.DEST_Z) > 0xf000) w8(gs, a + O.DEST_Z + 1, u8(gs, a + O.DEST_Z + 1) + 1);
    if (u16(gs, a + O.DEST_X) >= map.w << 8) w16(gs, a + O.DEST_X, u16(gs, a + O.DEST_X) - 0x100);
    if (u16(gs, a + O.DEST_Z) >= map.h << 8) w16(gs, a + O.DEST_Z, u16(gs, a + O.DEST_Z) - 0x100);
    if (u16(gs, a + O.X) >> 8 === u16(gs, a + O.DEST_X) >> 8 && u16(gs, a + O.Z) >> 8 === u16(gs, a + O.DEST_Z) >> 8) return 0;
    beginMove(G, obj, mode, 1);
    return 0;
  }
  // no re-route: nudge the blocker, stand and wait (0x4159B3)
  G.assert(dir >= -1, 'moveto>=-1'); // mobiles.c:1855
  if (u8(gs, ba + O.NUDGE) === 0xff && u8(gs, GS.ALLIANCE + u8(gs, ba + O.TEAM) * 10 + u8(gs, a + O.TEAM)) !== 0) {
    w8(gs, ba + O.NUDGE, dir);
  }
  Anim.startAnim(G, a + O.ANIM0, TF.stand(T(G, u8(gs, a + O.TYPE))), 0);
  Ticker.sleepState(G, obj, 4);
  return 0;
}

// ---- 0x415A94 ----------------------------------------------------------------------------------

/** state 6, 0x415A94: follow the path one tile per call (mode table 0x415A80). */
export function stateMove(G, obj, info) {
  const gs = G.gs;
  const map = G.map;
  const a = objAddr(obj);
  G.globals.loopTrap++;
  if (G.globals.loopTrap > 10) {
    Ticker.sleepState(G, obj, 7);
    return 0;
  }
  G.assert(G.globals.loopTrap < 100, 'nasty_global_loop_trap<100'); // mobiles.c:1879
  if (u8(gs, a + O.PENDING) !== 0) {
    Ticker.resetAndDispatchOrder(G, obj);
    return 1;
  }
  let cursor = i16(gs, info);
  if (cursor === -1) {
    const mode = i16(gs, info + 8);
    Ticker.popState(G, obj);
    if (!(u16(gs, a + O.X) >> 8 === u16(gs, a + O.DEST_X) >> 8 && u16(gs, a + O.Z) >> 8 === u16(gs, a + O.DEST_Z) >> 8)) {
      beginMove(G, obj, mode, 0);
    }
    return 0;
  }
  if (i16(gs, info + 0xa) !== -1) {
    if (i16(gs, info + 0xa) === i16(gs, info + 4) && i16(gs, info + 0xc) === i16(gs, info + 6)) {
      w16(gs, info + 0xc, -1);
      w16(gs, info + 0xa, -1);
    } else {
      reroute(G, obj, i16(gs, info + 0xa), i16(gs, info + 0xc), info, i16(gs, info + 0xe)); // result ignored
      cursor = i16(gs, info);
    }
  }
  const type = u8(gs, a + O.TYPE);
  const t = T(G, type);
  const mode = u16(gs, info + 8);
  if (mode <= 4) {
    switch (mode) {
      case 1: {
        // 0x415C58: fire at anything in range
        const target = Combat.findTargetInRange(G, obj);
        if (target !== -1) return Combat.fireWeaponAt(G, obj, target); // 0x414B08 wrapper
        break;
      }
      case 2: {
        // 0x415D8A: stop as soon as an enemy is in range
        if (Combat.findTargetInRange(G, obj) !== -1) {
          Ticker.popState(G, obj);
          return 1;
        }
        break;
      }
      case 3: {
        // 0x415C7F: chase the target object; fire when within range (tiles)
        const target = i16(gs, a + O_TARGET);
        const ta = objAddr(target);
        const life = u8(gs, ta + O.LIFE);
        if (life === 0 || life === 10) w16(gs, info + 8, 1);
        const w = TF.weapon(t, TF.weaponLevel(t, u8(gs, a + O.TEAM)));
        if (w === -1) break;
        const ddx = (u16(gs, a + O.X) >> 8) - (u16(gs, ta + O.X) >> 8);
        const ddz = (u16(gs, a + O.Z) >> 8) - (u16(gs, ta + O.Z) >> 8);
        const d2 = ddz * ddz + ddx * ddx;
        const r = weaponRange(G, w);
        if (r * r > d2) return Combat.fireWeaponAt(G, obj, target); // 0x414B08 wrapper
        break;
      }
      case 4: {
        // 0x415DB1: stop within weapon range (position units) of the destination
        let w;
        if (type === 0x0b || type === 3) w = TF.weapon(t, TF.weaponLevel(t, u8(gs, a + O.TEAM)));
        else w = TF.specialWeapon(t);
        G.assert(w !== 0, 'sw!=0'); // mobiles.c:2005
        const r = weaponRange(G, w);
        const r2 = (r * r) << 16;
        const ex = u16(gs, a + O.DEST_X) - u16(gs, a + O.X);
        const ez = u16(gs, a + O.DEST_Z) - u16(gs, a + O.Z);
        const d2 = ez * ez + ex * ex;
        if (d2 > r2) break;
        Ticker.popState(G, obj);
        return 1;
      }
      default:
        break;
    }
  }
  // the step (0x415EDA)
  const D = dir8(G);
  const dir = nibble(gs, a, cursor);
  const nx = i16(gs, info + 4) + D[dir * 2];
  const nz = i16(gs, info + 6) + D[dir * 2 + 1];
  const fly = TF.fly(t) !== 0;
  const i = nz * map.w + nx;
  const blocked = fly ? (map.air[i] & 0x3ff) !== 0x3ff : (map.ground[i] & 0x3ff) !== 0x3ff;
  if (blocked) {
    const blocker = fly ? map.air[i] & 0x3ff : map.ground[i] & 0x3ff;
    if (handleBlock(G, obj, blocker, info, fly) === 0) return 1;
    Ticker.resetAndDispatchOrder(G, obj);
    return 1;
  }
  if (fly) map.air[i] = (map.air[i] & 0xfc00) | obj;
  else map.ground[i] = (map.ground[i] & ~0x3ff) | obj;
  const trig = (map.air[i] & 0xffff) >>> 10;
  if (trig !== 0) Renat.triggerEnter(G, trig, obj); // 0x43E610(gs, trigger, obj)
  const oldZ = i16(gs, info + 6);
  const oldX = i16(gs, info + 4);
  w16(gs, info + 4, nx);
  w16(gs, info + 6, nz);
  w16(gs, info, i16(gs, info) - 1);
  startStep(G, obj, oldX, oldZ, (nx << 8) + 0x80, (nz << 8) + 0x80);
  return 1;
}

// ---- 0x4126A0 ----------------------------------------------------------------------------------

/**
 * start_step(gs, obj, oldX, oldZ, newPosX, newPosZ): MOVE animation, push state 5 {vx, vz, ticks,
 * oldX, oldZ}, push the turn state towards the step, clear the old tile and set the seen-by bits.
 */
export function startStep(G, obj, oldX, oldZ, npx, npz) {
  const gs = G.gs;
  const map = G.map;
  const a = objAddr(obj);
  const type = u8(gs, a + O.TYPE);
  const t = T(G, type);
  Anim.startAnim(G, a + O.ANIM0, TF.move(t), 0); // 0x42626C(&anim0, MOVE, 0)
  const info = Ticker.pushState(G, obj, 5, 5);
  const speed = TF.speed(t);
  const heading = Ticker.dirTo(G, obj, npx, npz); // 0x4124B8
  const ddx = npx - u16(gs, a + O.X);
  const ddz = npz - u16(gs, a + O.Z);
  Ticker.pushTurn(G, obj, heading); // 0x412650: state 4 above state 5
  // 0x441724 sincos(angle, &[ebp-8] = sin, &[ebp-4] = cos), 2048-scaled int16 pair; the code
  // multiplies [ebp-4] (cos) with ddx and [ebp-8] (sin) with ddz (0x412773..0x412789)
  const { sin, cos } = Missile.sincos(G, heading << 5);
  const dist = trunc11(cos * ddx + sin * ddz);
  w16(gs, info + 4, idiv(dist, speed)); // ticks = trunc(dist / 2048) / speed
  w16(gs, info, trunc11(cos * speed)); // vx = [ebp-4] * speed >> 11 (0x4127A6)
  w16(gs, info + 2, trunc11(sin * speed)); // vz = [ebp-8] * speed >> 11 (0x4127BF)
  w16(gs, info + 6, oldX);
  w16(gs, info + 8, oldZ);
  const team = u8(gs, a + O.TEAM);
  if (team === 8) return;
  const oi = oldZ * map.w + oldX;
  if (TF.fly(t) !== 0) map.air[oi] |= 0x3ff;
  else map.ground[oi] |= 0x3ff;
  if (team >= 8) return;
  const seen = 0x40000000 >> team;
  map.ground[oi] |= seen;
  map.ground[(npz >> 8) * map.w + (npx >> 8)] |= seen;
}

// ---- 0x4128D4 ----------------------------------------------------------------------------------

/** state 5, 0x4128D4: slide (vx, vz) per tick; at 0 ticks clear the nudge byte and pop. */
export function stateStep(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  Anim.startAnim(G, a + O.ANIM0, TF.move(T(G, u8(gs, a + O.TYPE))), 0);
  if (i16(gs, info + 4) === 0) {
    w8(gs, a + O.NUDGE, 0xff);
    Ticker.popState(G, obj);
    return 0;
  }
  addPosition(G, obj, i16(gs, info), i16(gs, info + 2));
  w16(gs, info + 4, i16(gs, info + 4) - 1);
  return 0;
}

// ---- 0x43508C ----------------------------------------------------------------------------------

/** collide.c 0x43508C: obj.x += vx, obj.z += vz (16-bit adds); no grid bookkeeping. */
export function addPosition(G, obj, vx, vz) {
  const gs = G.gs;
  const a = objAddr(obj);
  w16(gs, a + O.X, u16(gs, a + O.X) + vx);
  w16(gs, a + O.Z, u16(gs, a + O.Z) + vz);
}

// missile.js — port of missile.c of Classic dc16.exe (projectiles, blasts, burning ground).
//
// PORT NOTES (11 Sep 2026, from dc16.asm; everything below was ported instruction by instruction
// unless marked TODO(exact)):
//
//   ported exactly
//     0x4417F0 allocMissile, 0x4418A8 freeMissile, 0x44192C createMissile, 0x441A9C explosionOnly,
//     0x441E08 detonate, 0x4423D0 burnTick, 0x442610 updateMissiles, 0x442B50 missilesTick
//     (the per-tick wrapper: four update passes, then the animation/cleanup walks),
//     0x441724 sincos, 0x4415C0 atan2.
//
//   rand() call sites, in program order
//     createMissile:  1 (the missile's random byte MS.RANDOM)
//     updateMissiles: single-target hit with explosion sprites -> 1 (random explosion sprite)
//     detonate:       kind 4 with explosion sprites -> 1; regular blast with explosion sprites -> 1
//     (explosionOnly, burnTick, the smoke puff of kind 2 and freeMissile draw none)
//
//   cross-module calls (live bindings; names per PORTING.md, TODO(owner) where the table has none)
//     Anim.startAnim(G, slotAddr, anim, flag)            0x42626C (slotAddr = missileAddr(m) + MS.ANIM)
//     Anim.advance(G, slotAddr, facing2)                 0x426428 (TODO(owner anim.js); ticker.js calls
//                                                        the 3-slot object variant advanceAnims)
//     Grid.cellGround / cellAir / cellSecondary(G, x, z) map layers (reads)
//     Grid.setGround(G, x, z, v)                         write of one ground cell (TODO(owner grid.js);
//                                                        burning-ground markers 0x3FE)
//     Combat.collide(G, x, z, team, canHitAir)           0x4350D4
//     Combat.applyDamage(G, victim, weapon, fraction, attackerPlayer, night, killer)  0x441B4C
//     Combat.abduction(G, team, tx, tz, radius, max, out) 0x4171EC
//     Combat.dropShipEvent(G, team, tx, tz, hiBytes, loBytes) 0x4191E0
//     G.addStat(k, p, v)                                 0x41A06C
//
//   table fields used (tables.js names; original offsets in comments, see Combat's PORT NOTES for
//   the full list): weapon.sound +0x04, weapon.maxFlight +0x18, weapon.blast +0x1C,
//   weapon.bullet +0x2C, weapon.explode[] +0x30, weapon.numExplode +0x40,
//   weapon.weaponClass +0x00; type.race +0x04, type.rallyBonus +0xFC; boom.size +0x10,
//   boom.blast[row][col] +0x12 (int16[7][7]); G.tables.mbullet.rows[wclass][dclass].
//
//   deliberately reproduced oddities
//     * the smoke puff of kind 2 (0x4426E2) MOVES THE MISSILE ITSELF sideways by
//       vz*c/100, -vx*c/100 (c from the 32-entry wobble table 0x48AA1C indexed by
//       (age*4 + random) & 31) before spawning the puff with explosionOnly(weapon 43) — the puff is
//       display only but it allocates a missile record (count/free list) and the wobble changes
//       the collision position, so both stay.
//     * a ballistic missile (kind 1/4) that runs out of flight ticks sets hit = 0; when its blast
//       type has size 1 the code then damages OBJECT 0 (a city slot). Reproduced (never happens
//       with the stock tables: kinds 1/4 all have blast types of size 5/7).
//     * burnTick uses MS.FLIGHT (+0x18) as the burn age, not MS.AGE.
//     * the arc index assert `tl < ASIZE` (17) is a G.assert; the table read then happens anyway.
//     * sound handles (MS.SOUND, burning-ground loop) are display only; the fields are written with
//       the same values the code would write where they are known, otherwise left untouched.
//
//   disagreements with docs/DC16_BATTLE_ENGINE.md
//     * §6 step 6 "vx = sin·speed, vz = cos·speed": fire_weapon stores sincos(dir<<5) as
//       (sin -> [ebp+72], cos -> [ebp+6E]) and computes vx from [ebp+6E] (cos) and vz from [ebp+72]
//       (sin). See Combat.fireWeapon.
//     * §7.4 "runs while age & 3 == 0 ... age & 0x3F": the counter is MS.FLIGHT (+0x18), zeroed by
//       detonate, not MS.AGE (+0x10).
//     * §7.1 "kind 2 spawns a smoke puff every tick": it also displaces the missile (see above).
//     * §7.3: friendly-fire factor 0x40 applies when victim.team == shooter.team; the
//       fraction is idiv(matrix * factor, 256) (Watcom sbb/sar sequence = truncating division).
//     * §3.3 "+0x1F random byte": written from rand() & 0xFF in createMissile only; explosionOnly
//       zeroes the record (memset) and never sets it.

import { GS, O, MS, u8, i16, u16, i32, w8, w16, w32, objAddr, missileAddr, idiv, irem, MAX_MISSILES, MISSILE_SIZE } from './mem.js';
import * as Anim from './anim.js';
import * as Grid from './grid.js';
import * as Combat from './combat.js';

// atan table 0x48A7D6: int16[256], atan(i/256) in 1/8192 turn (the code reads the high half of the
// dword at 0x48A7D4 + 2*i, i.e. the int16 at 0x48A7D6 + 2*i; i is 0..255 because equal legs take
// the 45-degree constants). Extracted 11 Sep 2026 from the Classic exe.
export const ATAN_TABLE = Int16Array.from([
  0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 61, 66, 71, 76, 81, 86, 91, 96, 101, 106, 111, 116, 121, 126, 131, 137, 142, 147, 152, 157,
  162, 167, 172, 177, 182, 187, 192, 197, 202, 207, 212, 216, 221, 226, 231, 236, 241, 246, 251, 256, 261, 266, 271, 275, 280, 285, 290, 295, 300, 304, 309, 314,
  319, 324, 328, 333, 338, 343, 348, 352, 357, 362, 366, 371, 376, 380, 385, 390, 394, 399, 404, 408, 413, 417, 422, 427, 431, 436, 440, 445, 449, 454, 458, 463,
  467, 472, 476, 481, 485, 489, 494, 498, 503, 507, 511, 516, 520, 524, 529, 533, 537, 541, 546, 550, 554, 558, 563, 567, 571, 575, 579, 583, 588, 592, 596, 600,
  604, 608, 612, 616, 620, 624, 628, 632, 636, 640, 644, 648, 652, 656, 660, 664, 668, 671, 675, 679, 683, 687, 691, 694, 698, 702, 706, 709, 713, 717, 720, 724,
  728, 731, 735, 739, 742, 746, 750, 753, 757, 760, 764, 767, 771, 774, 778, 781, 785, 788, 792, 795, 798, 802, 805, 809, 812, 815, 819, 822, 825, 829, 832, 835,
  838, 842, 845, 848, 851, 855, 858, 861, 864, 867, 870, 874, 877, 880, 883, 886, 889, 892, 895, 898, 901, 904, 907, 910, 913, 916, 919, 922, 925, 928, 931, 934,
  937, 940, 942, 945, 948, 951, 954, 957, 959, 962, 965, 968, 971, 973, 976, 979, 981, 984, 987, 990, 992, 995, 998, 1000, 1003, 1005, 1008, 1011, 1013, 1016, 1018, 1021,
]);

// smoke-trail wobble table 0x48AA1C: int32[32], a sine-like wave in percent
export const WOBBLE_TABLE = Int32Array.from([
  0, 19, 38, 55, 70, 83, 92, 98, 99, 98, 92, 83, 70, 55, 38, 19, 0, -19, -38, -55, -70, -83, -92, -98, -99, -98, -92, -83, -70, -55, -38, -19,
]);

const W = (G, i) => G.tables.weapons[i];
const T = (G, t) => G.tables.types[t];
const BOOM = (G, i) => G.tables.booms[i];
const MB = (G, wclass, dclass) => G.tables.mbullet.rows[wclass][dclass];

// ---- trigonometry (missile.c) -------------------------------------------------------------------

/**
 * 0x441724 sincos(angle, &sin, &cos): angle in 1/8192 turn, 2048-scaled results from the
 * quarter-wave table 0x4897D4 (int16[2049], index 0..0x800). Returns { sin, cos } = the values
 * written through the edx and ebx pointers (int16).
 */
export function sincos(G, angle) {
  const tbl = G.consts.sinQuarter.values;
  const q = (angle & 0x1fff) >> 11;
  const a = angle & 0x7ff;
  switch (q) {
    case 0:
      return { sin: tbl[a], cos: tbl[0x800 - a] };
    case 1:
      return { sin: tbl[0x800 - a], cos: -tbl[a] | 0 }; // neg ecx: integer, never -0
    case 2:
      return { sin: -tbl[a] | 0, cos: -tbl[0x800 - a] | 0 };
    default:
      return { sin: -tbl[0x800 - a] | 0, cos: tbl[a] };
  }
}

/**
 * 0x4415C0 atan2(a, b) (eax = a, edx = b): angle 0..0x1FFF in 1/8192 turn. Ported branch by
 * branch; the table lookup uses idiv (truncation) and ATAN_TABLE above.
 */
export function atan2(a, b) {
  if (a === 0 && b === 0) return 0;
  const aa = a < 0 ? -a : a;
  const ab = b < 0 ? -b : b;
  if (ab > aa) {
    // 0x4415FA
    const t = ATAN_TABLE[idiv(aa << 8, ab)];
    if (a < 0 && b >= 0) return t + 0x800;
    if (a < 0 && b < 0) return 0x1800 - t;
    if (a >= 0 && b < 0) return t + 0x1800;
    return 0x800 - t;
  }
  if (ab === aa) {
    // 0x44165B: the four diagonals
    if (a > 0 && b > 0) return 0x400;
    if (a < 0 && b > 0) return 0xc00;
    if (a < 0 && b < 0) return 0x1400;
    return 0x1c00;
  }
  // 0x4416A1
  const t = ATAN_TABLE[idiv(ab << 8, aa)];
  if (a < 0 && b >= 0) return 0x1000 - t;
  if (a < 0 && b < 0) return t + 0x1000;
  if (a > 0) {
    if (b >= 0) return t;
    if (t === 0) return t;
    return 0x2000 - t;
  }
  return t; // a == 0 is impossible here (ab < aa); kept for the jle at 0x4416E4
}

// ---- free list ---------------------------------------------------------------------------------

/** 0x4417F0 alloc_missile(gs): pop the free list, else grow (assert number_of_missiles < 2024). */
export function allocMissile(G) {
  const gs = G.gs;
  const head = i16(gs, GS.MISSILE_FREE);
  if (head !== -1) {
    w16(gs, GS.MISSILE_FREE, i16(gs, missileAddr(head) + MS.NEXT));
    return head;
  }
  const n = i32(gs, GS.MISSILE_COUNT);
  w32(gs, GS.MISSILE_COUNT, n + 1);
  G.assert(n + 1 < MAX_MISSILES, 'game->number_of_missiles<MAX_MISSILES'); // missile.c:50
  return n;
}

/** 0x4418A8 free_missile(gs, m, prev): unlink from the active list (prev == -1: head) and push on the free list. */
export function freeMissile(G, m, prev) {
  const gs = G.gs;
  const ma = missileAddr(m);
  if (prev === -1) w16(gs, GS.MISSILE_ACTIVE, i16(gs, ma + MS.NEXT));
  else w16(gs, missileAddr(prev) + MS.NEXT, i16(gs, ma + MS.NEXT));
  w16(gs, ma + MS.NEXT, i16(gs, GS.MISSILE_FREE));
  w16(gs, GS.MISSILE_FREE, m);
}

// ---- creation ----------------------------------------------------------------------------------

/**
 * 0x44192C create_missile(gs, shooter, vx, vz, vy, weapon, flight, facing, delay, dx, dz, kind).
 * Register args eax..ecx = gs, shooter, vx, vz; the eight stack args in C order.
 */
export function createMissile(G, shooter, vx, vz, vy, weapon, flight, facing, delay, dx, dz, kind) {
  const gs = G.gs;
  const m = allocMissile(G);
  const sa = objAddr(shooter);
  const ma = missileAddr(m);
  const w = W(G, weapon);
  G.addStat(8, u8(gs, sa + O.TEAM), 1); // 0x41A06C shots fired
  w16(gs, ma + MS.X, u16(gs, sa + O.X) + dx);
  w16(gs, ma + MS.Z, u16(gs, sa + O.Z) + dz);
  w16(gs, ma + MS.HEIGHT, u16(gs, sa + O.HEIGHT));
  w8(gs, ma + MS.RANDOM, G.rand()); // rand() #1: the missile's random byte
  w16(gs, ma + MS.SOUND, w.sound); // weapon +0x04
  w8(gs, ma + MS.KIND, kind);
  // 0x431F60(gs, sound, 1, x, z): sound
  w16(gs, ma + MS.AGE, 0);
  w16(gs, ma + MS.VX, vx);
  w16(gs, ma + MS.VZ, vz);
  w16(gs, ma + MS.VY, vy);
  w16(gs, ma + MS.SHOOTER, shooter);
  w16(gs, ma + MS.WEAPON, weapon);
  w16(gs, ma + MS.NEXT, i16(gs, GS.MISSILE_ACTIVE));
  w16(gs, ma + MS.FLIGHT, flight);
  w16(gs, ma + MS.FACING, facing & 0xff); // xor ah,ah: word store of the low byte
  if (delay === 0) w16(gs, ma + MS.STATE, 1);
  else {
    w16(gs, ma + MS.STATE, 0);
    w16(gs, ma + MS.DELAY, delay << 2);
  }
  if (w.bullet) Anim.startAnim(G, ma + MS.ANIM, w.bullet, 0); // weapon +0x2C
  else w32(gs, ma + MS.ANIM, 0);
  w16(gs, GS.MISSILE_ACTIVE, m);
}

/**
 * 0x441A9C explosion_only(gs, x, z, height, weapon, shooter): a record in state 2 that only plays
 * the weapon's first explosion sprite (smoke puffs). No rand().
 */
export function explosionOnly(G, x, z, height, weapon, shooter) {
  const gs = G.gs;
  const m = allocMissile(G);
  const ma = missileAddr(m);
  gs.fill(0, ma, ma + MISSILE_SIZE); // memset(m, 0, 0x28)
  w16(gs, ma + MS.FACING, 0);
  w16(gs, ma + MS.AGE, 0);
  w16(gs, ma + MS.Z, z);
  w16(gs, ma + MS.X, x);
  w16(gs, ma + MS.HEIGHT, height);
  w16(gs, ma + MS.SHOOTER, shooter);
  w16(gs, ma + MS.WEAPON, weapon);
  w16(gs, ma + MS.NEXT, i16(gs, GS.MISSILE_ACTIVE));
  w16(gs, GS.MISSILE_ACTIVE, m);
  Anim.startAnim(G, ma + MS.ANIM, W(G, weapon).explode[0], 1); // weapon +0x30
  w16(gs, ma + MS.STATE, 2);
}

// ---- detonation --------------------------------------------------------------------------------

/** 0x441E08 detonate(gs, m). */
export function detonate(G, m) {
  const gs = G.gs;
  const ma = missileAddr(m);
  const weaponIdx = i16(gs, ma + MS.WEAPON);
  const w = W(G, weaponIdx);
  const boom = BOOM(G, w.blast); // weapon +0x1C -> boom_types
  const half = boom.size >> 1; // byte size, (size - sign) >> 1
  const cx = u16(gs, ma + MS.X) >> 8;
  const cz = u16(gs, ma + MS.Z) >> 8;
  // if (w.blast != 0) 0x431F60(gs, blast, 6, 1, x, z): sound
  const kind = u8(gs, ma + MS.KIND);
  const shooter = i16(gs, ma + MS.SHOOTER);
  const sa = objAddr(shooter);
  if (kind === 4) {
    w16(gs, ma + MS.FLIGHT, 0);
    w16(gs, ma + MS.STATE, 3);
    if (w.numExplode > 0) {
      const r = G.rand(); // rand(): explosion sprite of the burning area
      Anim.startAnim(G, ma + MS.ANIM, w.explode[irem(r, w.numExplode)], 0);
    }
    return;
  }
  if (kind === 8 || kind === 9 || kind === 10) {
    const max = kind === 8 ? 4 : kind === 9 ? 6 : 8; // else assert("0") at 0x441F28, unreachable
    const buf = new Int16Array(8); // int16[8] at ebp-0x64
    const team = u8(gs, sa + O.TEAM);
    let remaining = Combat.abduction(G, team, u16(gs, ma + MS.X) >> 8, u16(gs, ma + MS.Z) >> 8, 9, max, buf);
    let offset = 0;
    let batches = 0;
    if (remaining === 0) return;
    for (;;) {
      const k = remaining < 4 ? remaining : 3;
      // A (ebp-0x4C): [k, low bytes...]; B (ebp-0x54): [0xFF, high bytes...]
      const lo = new Uint8Array(8);
      const hi = new Uint8Array(8);
      lo[0] = k;
      hi[0] = 0xff;
      for (let i = 0; i < k; i++) {
        lo[1 + i] = buf[offset + i] & 0xff;
        hi[1 + i] = (buf[offset + i] & 0xffff) >> 8;
      }
      const fa = objAddr(buf[offset] & 0xffff); // movzx of the first id of the batch
      Combat.dropShipEvent(G, u8(gs, sa + O.TEAM), u16(gs, fa + O.X) >> 8, u16(gs, fa + O.Z) >> 8, hi, lo);
      remaining -= k;
      offset += k;
      batches++;
      if (batches >= 3) {
        G.assert(remaining === 0, 'abduct==0'); // missile.c:291
        return;
      }
      if (remaining === 0) return;
    }
  }
  if (kind === 5 || kind === 6 || kind === 7) {
    // A (ebp-0x44) and B (ebp-0x3C): byte[6] each, [1..5] zeroed, then per kind (fall-through)
    const lo = new Uint8Array(6);
    const hi = new Uint8Array(6);
    if (kind === 7) {
      hi[2] = 3;
      lo[2] = 1;
    }
    if (kind >= 6) {
      hi[1] = 2;
      lo[1] = 1;
    }
    hi[0] = 0;
    lo[0] = 2;
    Combat.dropShipEvent(G, u8(gs, sa + O.TEAM), u16(gs, ma + MS.X) >> 8, u16(gs, ma + MS.Z) >> 8, hi, lo);
    return;
  }
  // regular blast (0x4421DC)
  if (w.numExplode > 0) {
    const r = G.rand(); // rand(): explosion sprite
    Anim.startAnim(G, ma + MS.ANIM, w.explode[irem(r, w.numExplode)], 1);
    w16(gs, ma + MS.STATE, 2);
  }
  const map = G.map;
  const shooterTeam = u8(gs, sa + O.TEAM);
  for (let x = cx - half; x <= cx + half; x++) {
    for (let z = cz - half; z <= cz + half; z++) {
      let fraction = 0x100;
      if (x < 0 || z < 0 || x >= map.w || z >= map.h) continue;
      let id = Grid.cellGround(G, x, z) & 0x3ff;
      if (id === 0x3ff || id === 0x3fe) id = Grid.cellSecondary(G, x, z) & 0x3ff;
      if (id === 0x3ff || id === 0x3fe) continue;
      if (u8(gs, objAddr(id) + O.TEAM) === shooterTeam) fraction = 0x40;
      const f = boom.blast[z - cz + half][x - cx + half]; // int16 at boom+0x12 + row*14 + col*2
      fraction = idiv(f * fraction, 256); // sar/sbb sequence = truncating division
      Combat.applyDamage(G, id, w, fraction, shooterTeam, 0, shooter);
    }
  }
}

/** 0x4423D0 burn_tick(gs, m): burning area of kind 4; MS.FLIGHT is the burn age. */
export function burnTick(G, m) {
  const gs = G.gs;
  const ma = missileAddr(m);
  if ((u8(gs, ma + MS.FLIGHT) & 3) === 0) {
    const w = W(G, i16(gs, ma + MS.WEAPON));
    const boom = BOOM(G, w.blast);
    let finished = 0;
    const half = boom.size >> 1;
    const age = i16(gs, ma + MS.FLIGHT);
    if (age === 0) {
      // MS.SOUND = 0x431A08(63, 1, x, z, 0, ...): looping burning sound, display only
    }
    const cx = u16(gs, ma + MS.X) >> 8;
    const cz = u16(gs, ma + MS.Z) >> 8;
    if (age > 0x348) finished = 1;
    const map = G.map;
    const shooter = i16(gs, ma + MS.SHOOTER);
    const shooterTeam = u8(gs, objAddr(shooter) + O.TEAM);
    for (let x = cx - half; x <= cx + half; x++) {
      for (let z = cz - half; z <= cz + half; z++) {
        if (x < 0 || z < 0 || x >= map.w || z >= map.h) continue;
        const cell = Grid.cellGround(G, x, z);
        const id = cell & 0x3ff;
        if (finished !== 0) {
          if (id === 0x3fe) Grid.setGround(G, x, z, cell | 0x3ff); // or word ptr [cell],3FFh
          continue;
        }
        if (id === 0x3ff) {
          // clear the low 10 bits, then mark the tile burning (0x3FE)
          Grid.setGround(G, x, z, (cell & ~0x3ff) | 0x3fe);
          continue;
        }
        if (id === 0x3fe) continue;
        if ((u8(gs, ma + MS.FLIGHT) & 0x3f) !== 0) continue;
        const f = boom.blast[z - cz + half][x - cx + half];
        Combat.applyDamage(G, id, w, f, shooterTeam, 0, shooter);
      }
    }
    if (finished !== 0) {
      // if (MS.SOUND != -1) 0x431C28(63): stop the sound, display only
      w16(gs, ma + MS.STATE, 4);
    }
  }
  w16(gs, ma + MS.FLIGHT, i16(gs, ma + MS.FLIGHT) + 1);
}

// ---- per-tick update ---------------------------------------------------------------------------

/** 0x442610 update_missiles(gs): one pass over the active list. */
export function updateMissiles(G) {
  const gs = G.gs;
  const arc = G.consts.arc.values;
  let m = i16(gs, GS.MISSILE_ACTIVE);
  while (m !== -1) {
    const ma = missileAddr(m);
    const weaponIdx = i16(gs, ma + MS.WEAPON);
    const w = W(G, weaponIdx);
    const next = i16(gs, ma + MS.NEXT); // read before anything else, as the original does
    if (i16(gs, ma + MS.STATE) === 0) {
      const delay = i16(gs, ma + MS.DELAY);
      if (delay === 0) w16(gs, ma + MS.STATE, 1);
      else w16(gs, ma + MS.DELAY, delay - 1);
    }
    const state = i16(gs, ma + MS.STATE);
    if (state === 3) {
      burnTick(G, m);
      m = next;
      continue;
    }
    if (state !== 1) {
      m = next;
      continue;
    }
    // state 1: move (16-bit wrap)
    w16(gs, ma + MS.X, u16(gs, ma + MS.X) + i16(gs, ma + MS.VX));
    w16(gs, ma + MS.Z, u16(gs, ma + MS.Z) + i16(gs, ma + MS.VZ));
    w16(gs, ma + MS.HEIGHT, u16(gs, ma + MS.HEIGHT) + i16(gs, ma + MS.VY));
    const kind = u8(gs, ma + MS.KIND);
    if (kind === 3) w16(gs, ma + MS.HEIGHT, 0);
    if (kind === 2) {
      // smoke trail: wobble the missile sideways, then spawn a puff (weapon 43) at its position
      const idx = ((i16(gs, ma + MS.AGE) << 2) + u8(gs, ma + MS.RANDOM)) & 0x1f;
      const c = WOBBLE_TABLE[idx];
      w16(gs, ma + MS.X, u16(gs, ma + MS.X) + idiv(i16(gs, ma + MS.VZ) * c, 100));
      w16(gs, ma + MS.Z, u16(gs, ma + MS.Z) + idiv(-i16(gs, ma + MS.VX) * c, 100));
      w16(gs, ma + MS.HEIGHT, u16(gs, ma + MS.HEIGHT) + idiv(i16(gs, ma + MS.VY) * c, 100));
      explosionOnly(G, u16(gs, ma + MS.X), u16(gs, ma + MS.Z), u16(gs, ma + MS.HEIGHT), 0x2b, i16(gs, ma + MS.SHOOTER));
    }
    let hit;
    if (i16(gs, ma + MS.FLIGHT) === -1) {
      // direct fire: collision test every tick
      const shooter = i16(gs, ma + MS.SHOOTER);
      const sa = objAddr(shooter);
      const team = u8(gs, sa + O.BERSERK) !== 0 ? 9 : u8(gs, sa + O.TEAM);
      const canHitAir = MB(G, w.weaponClass, 2) !== 0 ? 1 : 0;
      hit = Combat.collide(G, u16(gs, ma + MS.X), u16(gs, ma + MS.Z), team, canHitAir);
      if (hit !== -1) {
        if (shooter === hit) hit = -1;
        else if (u8(gs, sa + O.BERSERK) === 0) {
          const ht = u8(gs, objAddr(hit) + O.TEAM);
          if (u8(gs, GS.ALLIANCE + u8(gs, sa + O.TEAM) * 10 + ht) !== 0) hit = -1;
        }
      }
    } else {
      if (kind === 1 || kind === 4) {
        const flight = i16(gs, ma + MS.FLIGHT);
        const total = flight + i16(gs, ma + MS.AGE);
        let idx;
        if (total !== 0) {
          idx = idiv(flight << 4, total);
          G.assert(idx < 0x11, 'tl < ASIZE'); // missile.c:484
        } else idx = 0;
        w16(gs, ma + MS.HEIGHT, (arc[idx] * total) >> 6);
      }
      const flight = i16(gs, ma + MS.FLIGHT) - 1;
      w16(gs, ma + MS.FLIGHT, flight);
      hit = ((flight << 16) >> 16) > 0 ? -1 : 0; // test cx,cx on the stored word
    }
    // 0x442963
    if (hit !== -1) {
      const boom = BOOM(G, w.blast);
      if (boom.size === 1) {
        const shooter = i16(gs, ma + MS.SHOOTER);
        const sa = objAddr(shooter);
        const race = T(G, u8(gs, sa + O.TYPE)).race; // type +0x04
        let night = 0;
        if (race === 0) {
          if (i32(gs, GS.DN_PHASE) === 1) night = 1;
        } else if (race === 1 && i32(gs, GS.DN_PHASE) === 0) night = 1;
        let fraction = 0x100;
        if (u8(gs, sa + O.LINK_COUNT) !== 0) {
          const linked = u16(gs, sa + O.LINK_OBJ); // movzx: unsigned
          fraction = T(G, u8(gs, objAddr(linked) + O.TYPE)).rallyBonus; // type +0xFC
        }
        Combat.applyDamage(G, hit, w, fraction, u8(gs, sa + O.TEAM), night, shooter);
        if (w.numExplode > 0) {
          const va = objAddr(hit);
          w16(gs, ma + MS.X, u16(gs, va + O.X));
          w16(gs, ma + MS.Z, u16(gs, va + O.Z));
          w16(gs, ma + MS.HEIGHT, u16(gs, va + O.HEIGHT));
          const r = G.rand(); // rand(): explosion sprite
          Anim.startAnim(G, ma + MS.ANIM, w.explode[irem(r, w.numExplode)], 1);
          w16(gs, ma + MS.STATE, 2);
        }
      } else detonate(G, m);
    }
    // 0x442B12
    w16(gs, ma + MS.AGE, i16(gs, ma + MS.AGE) + 1);
    if (i16(gs, ma + MS.AGE) > w.maxFlight || hit !== -1) {
      if (i16(gs, ma + MS.STATE) === 1) w16(gs, ma + MS.STATE, 4);
    }
    m = next;
  }
}

/**
 * 0x442B50 missiles_tick(gs): four update passes, then advance the animation of every missile
 * (state 2 becomes 4 when its animation finished), then free every state-4 record.
 */
export function missilesTick(G) {
  const gs = G.gs;
  for (let pass = 0; pass < 4; pass++) updateMissiles(G);
  // animation walk
  let m = i16(gs, GS.MISSILE_ACTIVE);
  while (m !== -1) {
    const ma = missileAddr(m);
    const next = i16(gs, ma + MS.NEXT);
    if (i32(gs, ma + MS.ANIM) !== 0) {
      Anim.advance(G, ma + MS.ANIM, i16(gs, ma + MS.FACING) >> 3); // 0x426428(anim, facing >> 3)
      if (i16(gs, ma + MS.STATE) === 2 && u8(gs, ma + MS.ANIM + 6) === 2) w16(gs, ma + MS.STATE, 4);
    }
    m = next;
  }
  // cleanup walk
  let prev = -1;
  m = i16(gs, GS.MISSILE_ACTIVE);
  while (m !== -1) {
    const ma = missileAddr(m);
    const next = i16(gs, ma + MS.NEXT);
    if (i16(gs, ma + MS.STATE) === 4) freeMissile(G, m, prev);
    else prev = m;
    m = next;
  }
}

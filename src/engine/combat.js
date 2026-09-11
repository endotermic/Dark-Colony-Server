// combat.js — port of collide.c and the combat half of mobiles.c of Classic dc16.exe.
//
// PORT NOTES (11 Sep 2026, from dc16.asm; every function below was ported instruction by
// instruction unless marked TODO(exact)):
//
//   ported exactly
//     0x4356C8 findTarget, 0x435D5C findTargetInRange, 0x4350D4 collide (+ 0x43658C inRect,
//     0x4364FC/0x4365BC/0x4365E8 rect helpers inlined), 0x413018 fireWeapon, 0x414B08 fireWeaponAt
//     (wrapper, tx = tz = 0), 0x414B18 fireSpecial (wrapper swapping the special weapon in; no
//     direct call site in the exe, kept for completeness), 0x441B4C applyDamage, 0x4165A0 objectDie,
//     0x4168C0 pushWreck, 0x4166F8 stateCorpse (0xA), 0x4167E0 stateWreck (0x11), 0x413F24
//     healNearby, 0x418160 orderFireAtGround (order 0x12), 0x4182C0 stateFireAtGround (0x12),
//     0x417DA4 stateDeploy (0xD), 0x416ED4 artifactMakt + 0x416C08 maktFling (+ 0x416BE8 dist3),
//     0x416FF4 artifactLuna, 0x417644 artifactTekt, 0x417A6C artifactHyyk, 0x417400 rally,
//     0x417BDC stealingSearch, 0x4171EC abduction, 0x4191E0 dropShipEvent, 0x418580 pushDescend.
//
//   rand() call sites, in program order
//     fireWeapon:     1 (FIRE sprite: irem(rand, numFire)), then per hotspot of an AREA weapon
//                     (weapon.blast != 0) 1 (scatter: rand & 0xFF) — createMissile then draws its own
//     stateCorpse:    1 on the first tick (DIE sprite: irem(rand, numDie); drawn for commanders too,
//                     whose FUNK pose ignores it)
//     artifactLuna:   1 per living player-owned object found (berserk = (rand & 31) + 25)
//     rally:          1 per linked unit (link countdown = (rand & 15) + 20)
//     dropShipEvent:  2 (x and z jitter, bit 0 of each)
//     (applyDamage, objectDie, healNearby, collide, findTarget, stateDeploy, stateWreck,
//      artifactMakt/Tekt/Hyyk, abduction, stealingSearch draw none)
//
//   cross-module calls (live bindings; names per PORTING.md, TODO(owner) where the table has none)
//     Ticker.dirTo(G, obj, x, z) -> 0..255              0x4124B8
//     Ticker.turnTowards(G, obj, dir) -> 0|1             0x412414 (1 while not aligned)
//     Ticker.setCooldown(G, obj, n)                      0x4124F0
//     Ticker.pushState(G, obj, state, nwords) -> infoAddr 0x412114
//     Ticker.resetAndDispatchOrder(G, obj)               0x41233C
//     Ticker.orderDeploy(G, obj)                         0x416A1C (ORDER_TABLE[0xD])
//     Move.startStep(G, obj, tx, tz, destX, destZ)         0x4126A0 (TODO(owner ticker.js): drop-ship
//                                                        arrival flight; starts the MOVE anim)
//     Move.beginMove(G, obj, mode, flag)                 0x414FD0
//     Anim.startAnim(G, slotAddr, anim, flag)            0x42626C
//     Anim.hotspots(G, obj, anim, facing) -> [{x,z,delay}] 0x426338 (muzzle hotspots of the FIRE
//                                                        sprite's current frame; at most 7)
//     Grid.cellGround / cellAir / cellSecondary(G, x, z) map layers (reads)
//     Grid.setSecondary(G, x, z, v)                      write of one secondary cell (TODO(owner grid.js))
//     Grid.removeObject(G, obj)                          0x434EB8
//     City.depRecompute(G)                               0x437D00 (TODO(owner city.js))
//     Missile.createMissile(...) / Missile.sincos(G, angle)
//     Scenario.createObject(G, x, z, type, team, obj)    0x41B930 (drop ship into city slot object)
//     Renat.spawnObject(G, tx, tz, type, team) -> obj    0x41B818 (TODO(owner renat.js); HYYK copies)
//     G.addStat / G.addTypeStat / G.stat                 0x41A06C / 0x41A154 / 0x41A790
//
//   table field names used (tables.js; original offsets)
//     weapon: weaponClass +0x00, sound +0x04, rateOfFire +0x08, damage +0x0C, speed +0x10,
//             range +0x14, maxFlight +0x18, blast +0x1C, shots +0x20, reload +0x24, oneShot +0x28
//             (byte), bullet +0x2C, explode[4] +0x30, numExplode +0x40, kind +0x44 (read as i8, stored u8)
//     type:   scenery +0x00 (byte), race +0x04, speed +0x0C, weapon[3] +0x18, armour[3] +0x24,
//             weaponLevel[8] +0x30 (bytes), armourLevel[8] +0x38 (bytes), defenceClass +0x40,
//             hp +0x44, bbox {minX,minY,maxX,maxY} +0x48..+0x54 (relative to the position),
//             fly +0x60 (byte), signature +0x64 (byte), hidden +0x68, move +0x7C,
//             stand +0x80, deploy +0x94, funk +0x9C, fire[] +0xA0, die[] +0xAC,
//             numFire +0xE4, numDie +0xE8, rallyBonus +0xFC, rallySize +0x100,
//             specialWeapon +0x110
//     boom:   size +0x10 (byte), blast[7][7] +0x12 (int16), scatter[3][3] +0x74 (int16)
//     mbullet: G.tables.mbullet.rows[wclass][dclass] (int16)
//     The bounding box comes from the type table (types[t].bbox, +0x48..+0x57), not from Anim.
//
//   gs offsets not in mem.js (integrator: move to mem.js P)
//     P_WORD_1C = 0x1C (gs+0xBB4, int32): non-zero suppresses the hero-death event
//     P_DROPSHIP_USED = 0xE17 (gs+0x19AF, byte[8]): drop-ship slot in use -> city-slot object 7+i
//
//   "research bytes" 0x510188 (napalm) / 0x510A48 (plasma): these are object_types[4]+0x30 and
//   object_types[12]+0x30, the per-player weapon upgrade level of the cyborg / psy-raider (command
//   0x0C); level 2 enables the special. (Corrected 11 Sep 2026 after the first recorded game; the
//   research() helper below is unused and kept only for the doc trail.)
//
//   TODO(exact)
//     * objectDie's hero-death event passes two 5-byte arrays of which the original only
//       initialises A[0..2] = {0, obj>>8, obj&0xFF} and B[0] = 0; the rest is uninitialised stack.
//       Zeros are passed here. Single player only (gs+0 == 0), so irrelevant for the relay server.
//     * sound handles written into state info (dropShipEvent info[12], burn loop) are 0 here.
//
//   deliberately reproduced oddities
//     * applyDamage's blood byte: `if (damaged + newHp < 255) damaged -= (newHp & 0xFF) else 255`
//       — that is what the binary does (0x441C4A..0x441C6A); the dispatcher only tests != 0.
//     * mines (types 45/46) lose 300 HP per shot; a NEGATIVE result becomes 1, zero stays 0.
//     * the scatter walk can leave the 3x3 matrix with row = col = 3 when the matrix sums to less
//       than 256 (blast type 0: 253): the aim point then shifts by (+2, +2) tiles.
//     * the 5 % guard loop of fireWeapon shrinks |vx|, |vz| and the flight time t, never vx/vz.
//     * findTarget's direct-fire score is `score * ((hp <= 0 ? 0x800 - hp : 0x400) >> 11)` = 0 for
//       every living target, so the first candidate in spiral order wins (best starts at -1).
//     * healNearby zeroes the healer's charge in both branches (the partial-heal branch first
//       computes charge -= missing*64/mb and then overwrites it with 0).
//     * stateCorpse counts in info[0] (info[1] is written by objectDie and never read).
//     * rally/abduction/stealingSearch walk rings as four sides L, R, T, B with k = -2*MAX..2*MAX for
//       every ring (MAX = 10 / radius / 11: the constant maximum, NOT the current ring — corrected 11 Sep
//       2026 after the first send-mode game diverged right after a commander rally) (so the
//       columns overshoot the square) and ring 0 tests the centre tile four times.
//     * dropShipEvent's sound arg = 1 (info[12] receives the handle); drop ships are objects
//       team*15 + 7 + slot, i.e. the unused city-building slots 7..14.
//
//   disagreements with docs/DC16_BATTLE_ENGINE.md
//     * §5: collide tests the layers in the order air (if canHitAir), ground, secondary; score =
//       1 + (same column ? 1) + (same row ? 1) (+3 after a passed bbox test), highest wins.
//     * §5: the hidden-object filter requires `(detected & (1 << team)) != 0` where team may be 9
//       (berserk shooter): 1 << 9 never matches, so berserk shots pass through mines.
//     * §6 step 6: vx uses cos, vz uses sin (see missile.js notes); t = |d|/|v| along the larger
//       velocity component; `vy = t ? dy / t : 0`; t = -1 only when blast == 0.
//     * §6 step 8: "clamped to 1 HP" is only for negative results.
//     * §6.1: for BARR/ATRIL (types 11/3) the state fires the normal weapon; every other type
//       needs specialWeapon != 0 (not -1). The order handler moves closer with begin_move(4, 0)
//       AFTER pushing state 0x12; the deadline is `gameTime - info[2] > 4` with 16-bit operands.
//     * §8.1: stats 2 / per-type 3 are booked BEFORE object_die, stats 3 / per-type 0 AFTER the
//       grid removal; the hero-distance check uses `attackerPlayer < 8` on the int argument.
//     * §8.3: object_die requires player+0x1C == 0 as well for the hero-death event; the death
//       sound plays for building slots only (inside the `obj < 120` branch).
//     * §9: the healer scans rings 0..7 of an expanding square, layers air THEN ground; the heal
//       overlay is the healer's FIRE sprite, the healer's own animation is DEPLOY (+0x94).
//     * §10.2: rally skips its own type via rallyBonus != 0, requires weapons[0] != -1, and
//       first calls reset_and_dispatch_order on the commander and zeroes its charge.
//     * §10.3: state 0xD only maps 1->41, 9->42, 4->77, 12->78, 77->4, 78->12, 47->6, 48->14 and
//       43/44->45/46; 6->47 and 14->48 do not happen here (dead `cmp dl,6 / cmp dl,0Eh` at entry).
//       LENS (63) sets hp = 1 and fires createMissile(kind 3, weapons[0]) at its own position;
//       MAKT/LUNA (64/65) work on a 15x15 square (radius 7); MAKT flings mobile units into state
//       0xF with life = 10; TEKT (67) converts wildlife (team 9) to the player within 128 tiles up
//       to the unit cap; HYYK (66) spawns four copies of itself in state 0x10.
//     * §7.2 "abduction takes up to 4/6/8 enemy units": radius 9, units of non-allied teams that
//       are mobile and not commanders; delivered in batches of 3 through dropShipEvent.
//     * §3.1 / mem.js: the slot-blocked flags at player+0x7C (gs+0xC14) are BYTES (byte[15]),
//       indexed `+0xC14 + slot` in 0x4350D4, 0x4356C8, 0x418559, 0x418C2F; mem.js says int32[15].

import { GS, O, P, u8, i8, i16, u16, i32, w8, w16, w32, objAddr, playerAddr, idiv, irem } from './mem.js';
import * as Ticker from './ticker.js';
import * as Move from './move.js';
import * as Anim from './anim.js';
import * as Grid from './grid.js';
import * as City from './city.js';
import * as Missile from './missile.js';
import * as Scenario from './scenario.js';
import * as Renat from './renat.js';

// player-block offsets not yet in mem.js (integrator: move to mem.js P)
const P_WORD_1C = 0x1c; // gs+0xBB4 int32
const P_DROPSHIP_USED = 0xe17; // gs+0x19AF byte[8]

const W = (G, i) => G.tables.weapons[i];
const T = (G, t) => G.tables.types[t];
const BOOM = (G, i) => G.tables.booms[i];
const MB = (G, wclass, dclass) => G.tables.mbullet.rows[wclass][dclass];
// +0x30 byte[8] indexed with the raw team byte: teams 8/9 read the armour-level bytes (+0x38)
const weaponLevel = (t, team) => (team < 8 ? t.weaponLevel[team] : t.armourLevel[team - 8]);
/** the shooter's current weapon index (weapons[weaponLevel[team]]), -1 if unarmed */
const currentWeapon = (G, a) => {
  const t = T(G, u8(G.gs, a + O.TYPE));
  return t.weapon[weaponLevel(t, u8(G.gs, a + O.TEAM))];
};
const alliance = (G, a, b) => u8(G.gs, GS.ALLIANCE + a * 10 + b);
const inMap = (G, x, z) => x >= 0 && z >= 0 && x < G.map.w && z < G.map.h;

/** research bytes 0x510188 / 0x510A48 (one per player), created on first use. */
export function research(G) {
  if (!G.globals.research) G.globals.research = { napalm: new Uint8Array(8), plasma: new Uint8Array(8) };
  return G.globals.research;
}

// ---- target acquisition (collide.c) -------------------------------------------------------------

/**
 * 0x4356C8 find_target(gs, obj, maxRing): spiral search over the table 0x434200; returns the
 * object index or -1.
 */
export function findTarget(G, obj, maxRing) {
  const gs = G.gs;
  const a = objAddr(obj);
  const team = u8(gs, a + O.TEAM);
  let best = -1;
  let result = -1;
  let mask;
  if (team === 9) {
    mask = 0;
    for (let q = 0; q < 8; q++) mask |= 0x40000000 >> q;
  } else mask = i32(gs, playerAddr(team) + P.VISION);
  G.assert(team !== 8, 'sptr->team_number != 8'); // collide.c:266
  const map = G.map;
  const spiral = G.consts.spiral.values;
  const ox = u16(gs, a + O.X) >> 8;
  const oz = u16(gs, a + O.Z) >> 8;
  const myType = T(G, u8(gs, a + O.TYPE));
  let ring = 0;
  let idx = 0;
  while (ring <= maxRing) {
    const [sdx, sdz] = spiral[idx];
    if (sdx === 99) {
      ring++;
      idx++;
      continue;
    }
    const cx = ox + sdx;
    const cz = oz + sdz;
    if (inMap(G, cx, cz)) {
      const cell = Grid.cellGround(G, cx, cz);
      if ((cell & mask) !== 0) {
        // emptiness pre-check over the three layers
        let id = cell & 0x3ff;
        if (id === 0x3ff || id === 0x3fe) id = Grid.cellAir(G, cx, cz) & 0x3ff;
        if (id === 0x3ff || id === 0x3fe) id = Grid.cellSecondary(G, cx, cz) & 0x3ff;
        if (id !== 0x3ff && id !== 0x3fe) {
          for (let layer = 0; layer < 3; layer++) {
            if (layer === 0) id = cell & 0x3ff;
            else if (layer === 1) id = Grid.cellAir(G, cx, cz) & 0x3ff;
            else id = Grid.cellSecondary(G, cx, cz) & 0x3ff;
            if (id === 0x3ff || id === 0x3fe) continue;
            const o = objAddr(id);
            const ot = T(G, u8(gs, o + O.TYPE));
            const oteam = u8(gs, o + O.TEAM);
            if (ot.hidden !== 0 && team !== oteam && (u8(gs, o + O.DETECTED) & (1 << team)) === 0) continue;
            if (ot.scenery !== 0) continue;
            G.assert(u8(gs, o + O.LIFE) !== 0, 'Collide.c, target dead but still in collision grid'); // :345
            G.assert(u8(gs, o + O.LIFE) !== 10, 'Collide.c, target rotting but still in collision grid'); // :349
            if (id < 120 && u8(gs, playerAddr(idiv(id, 15)) + P.SLOT_BLOCKED + irem(id, 15)) !== 0) continue;
            if (oteam === 8 || oteam === 9) continue;
            if (o === a) continue;
            if (u8(gs, a + O.BERSERK) === 0 && alliance(G, team, oteam) !== 0) continue;
            const wIdx = myType.weapon[weaponLevel(myType, team)];
            const w = W(G, wIdx);
            if (MB(G, w.weaponClass, ot.defenceClass) === 0) continue;
            let score = ot.weapon[0] === -1 ? 50 : 150;
            if (ot.fly !== 0) score += 200;
            if (w.blast !== 0) {
              for (let nx = cx - 1; nx <= cx + 1; nx++) {
                if (nx < 0 || nx >= map.w) continue;
                for (let nz = cz - 1; nz <= cz + 1; nz++) {
                  if (nz < 0 || nz >= map.h) continue;
                  const nid = Grid.cellGround(G, nx, nz) & 0x3ff;
                  if (nid === 0x3ff || nid === 0x3fe) continue;
                  const no = objAddr(nid);
                  if (u8(gs, no + O.LIFE) === 10) continue;
                  if (alliance(G, team, u8(gs, no + O.TEAM)) !== 0) score -= 15;
                  else score += 10;
                }
              }
            } else {
              const hp = i32(gs, o + O.HP);
              let v = 0x400 - hp;
              if (v < 0x400) v = 0;
              score *= (v + 0x400) >> 11;
            }
            if (score > best) {
              best = score;
              result = id;
            }
          }
        }
      }
    }
    idx++;
  }
  return result;
}

/** 0x435D5C find_target_in_range(gs, obj): find_target with rings = weapon.range; -1 if unarmed. */
export function findTargetInRange(G, obj) {
  const wIdx = currentWeapon(G, objAddr(obj));
  if (wIdx === -1) return -1;
  return findTarget(G, obj, W(G, wIdx).range);
}

/** 0x43658C in_rect(x0, z0, x1, z1, px, pz): half-open rectangle test. */
export function inRect(x0, z0, x1, z1, px, pz) {
  return px >= x0 && px < x1 && pz >= z0 && pz < z1 ? 1 : 0;
}

/**
 * 0x4350D4 collide(gs, x, z, team, canHitAir): object hit by a projectile at position (x, z),
 * 3x3 tiles, layers air (if canHitAir) / ground / secondary; -1 for none.
 */
export function collide(G, x, z, team, canHitAir) {
  const gs = G.gs;
  const tx = x >> 8;
  const tz = z >> 8;
  let best = 0;
  let result = -1;
  // rect {0, 0, map.xsize<<8, map.zsize<<8} >> 8 = {0, 0, w, h}
  const w = G.map.w;
  const h = G.map.h;
  if (!inRect(0, 0, w, h, tx, tz)) return -1;
  for (let cx = tx - 1; cx <= tx + 1; cx++) {
    for (let cz = tz - 1; cz <= tz + 1; cz++) {
      if (!inRect(0, 0, w, h, cx, cz)) continue;
      for (let layer = 0; layer < 3; layer++) {
        let id;
        if (layer === 0) {
          if (canHitAir === 0) continue;
          id = Grid.cellAir(G, cx, cz) & 0x3ff;
        } else if (layer === 1) id = Grid.cellGround(G, cx, cz) & 0x3ff;
        else id = Grid.cellSecondary(G, cx, cz) & 0x3ff;
        if (id === 0x3ff || id === 0x3fe) continue;
        let score = 1;
        if (cx === tx) score = 2;
        if (cz === tz) score++;
        const o = objAddr(id);
        const oteam = u8(gs, o + O.TEAM);
        if (oteam === team) continue;
        if (oteam === 8) continue;
        const ot = T(G, u8(gs, o + O.TYPE));
        if (ot.hidden !== 0 && oteam !== team && (u8(gs, o + O.DETECTED) & (1 << team)) === 0) continue;
        if (ot.scenery !== 0) continue;
        if (id < 120) {
          if (u8(gs, playerAddr(idiv(id, 15)) + P.SLOT_BLOCKED + irem(id, 15)) !== 0) continue;
          if (score > best) {
            result = id;
            best = score;
          }
          continue;
        }
        // bounding box of the type, translated to the object's position (0x4364FC)
        const ox = u16(gs, o + O.X);
        const oz = u16(gs, o + O.Z);
        const bb = ot.bbox;
        score += 3;
        if (inRect(bb.minX + ox, bb.minY + oz, bb.maxX + ox, bb.maxY + oz, x, z) && score > best) {
          best = score;
          result = id;
        }
      }
    }
  }
  return result;
}

// ---- firing (mobiles.c) -------------------------------------------------------------------------

/**
 * 0x413018 fire_weapon(gs, obj, obj*, target, tx, tz): turn towards the target and, once aligned,
 * launch one projectile per muzzle hotspot. Always returns 0.
 */
export function fireWeapon(G, obj, target, tx = 0, tz = 0) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = T(G, u8(gs, a + O.TYPE));
  let th = 0;
  if (target !== -1) {
    const ta = objAddr(target);
    if (u8(gs, ta + O.LIFE) === 0) return 0;
    tx = u16(gs, ta + O.X);
    tz = u16(gs, ta + O.Z);
    th = u16(gs, ta + O.HEIGHT);
    const tteam = u8(gs, ta + O.TEAM);
    if (tteam < 8) w8(gs, a + O.ATTACKED, ((tteam << 5) & 0xff) | t.signature);
  }
  const team = u8(gs, a + O.TEAM);
  const wIdx = t.weapon[weaponLevel(t, team)];
  const w = W(G, wIdx);
  const dir = Ticker.dirTo(G, obj, tx, tz);
  if (Ticker.turnTowards(G, obj, dir) !== 0) return 0;
  // firing animation: rand() #1
  const r = G.rand();
  const fireAnim = t.fire[irem(r, t.numFire)];
  Anim.startAnim(G, a + O.ANIM0, fireAnim, 1);
  const facing = (((u8(gs, a + O.HEADING) + 8) & 0xff) >> 4) << 1; // (h+8 & 0xF0) >> 3
  let hot = Anim.hotspotsOfSet(G, fireAnim, facing); // 0x426338 on the slot just started (= fireAnim)
  if (hot.length > 7) hot = hot.slice(0, 7);
  if (hot.length === 0) hot = [{ x: 0, z: 0, delay: 0 }];
  for (let k = 0; k < hot.length; k++) {
    let ax = tx - hot[k].x;
    let az = tz - hot[k].z;
    if (w.blast !== 0) {
      // scatter: rand() per hotspot
      let rr = G.rand() & 0xff;
      let row;
      let col;
      if (u8(gs, a + O.LINK_COUNT) !== 0) {
        row = 1;
        col = 1;
      } else {
        const sc = BOOM(G, w.blast).scatter;
        row = 0;
        col = 0;
        outer: for (;;) {
          for (;;) {
            const v = sc[row][col];
            if (v > rr) break outer;
            rr -= v;
            col++;
            if (col >= 3) break;
          }
          row++;
          if (row >= 3) break; // leaves row = col = 3
          col = 0;
        }
      }
      ax += (col << 8) - 0x100;
      az += (row << 8) - 0x100;
    }
    const dir2 = Ticker.dirTo(G, obj, ax, az);
    const { sin, cos } = Missile.sincos(G, dir2 << 5);
    const vx = idiv(cos * w.speed, 2048); // [ebp+6E] = cos
    const vz = idiv(sin * w.speed, 2048); // [ebp+72] = sin
    const dy = th - u16(gs, a + O.HEIGHT);
    let tflight;
    let vy = 0;
    if (w.blast === 0 && dy === 0) tflight = -1;
    else {
      const dx = ax - u16(gs, a + O.X);
      const dz = az - u16(gs, a + O.Z);
      let avx = vx < 0 ? -vx : vx;
      let avz = vz < 0 ? -vz : vz;
      let num;
      let den;
      if (avz < avx) {
        num = dx;
        den = vx;
      } else {
        num = dz;
        den = vz;
      }
      G.assert(den !== 0, 'fire_weapon: zero velocity component (idiv by zero in the original)');
      tflight = idiv(num, den);
      const r2 = (w.range * w.range) << 16;
      while (avx * avx + avz * avz > r2) {
        avx = idiv(avx * 95, 100);
        avz = idiv(avz * 95, 100);
        tflight = idiv(tflight * 95, 100);
      }
      if (dy !== 0) {
        vy = tflight !== 0 ? idiv(dy, tflight) : 0;
        if (w.blast === 0) tflight = -1;
      }
    }
    // mines lose 300 HP per shot (negative -> 1)
    const type = u8(gs, a + O.TYPE);
    if (type === 0x2d || type === 0x2e) {
      let hp = i32(gs, a + O.HP) - 300;
      if (hp < 0) hp = 1;
      w32(gs, a + O.HP, hp);
    }
    Missile.createMissile(G, obj, vx, vz, vy, wIdx, tflight, dir2, hot[k].delay, hot[k].x, hot[k].z, w.kind);
    if (w.oneShot !== 0) w8(gs, a + O.CHARGE, 0);
  }
  // cooldown
  let cd = w.rateOfFire;
  if (w.shots > 0) {
    const b = (u8(gs, a + O.BURST) + 1) & 0xff;
    w8(gs, a + O.BURST, b);
    if (b >= w.shots) {
      w8(gs, a + O.BURST, 0);
      cd = w.reload;
    }
  }
  Ticker.setCooldown(G, obj, cd);
  return 0;
}

/** 0x414B08 wrapper: fire_weapon(gs, obj, obj*, target, 0, 0). */
export function fireWeaponAt(G, obj, target) {
  return fireWeapon(G, obj, target, 0, 0);
}

/**
 * 0x414B18 wrapper: swap the type's special weapon (+0x110) into the current weapon slot, fire
 * at `target` with tx = tz = 0, restore the slot. No direct call site in the exe.
 */
export function fireSpecial(G, obj, target) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = T(G, u8(gs, a + O.TYPE));
  const lvl = weaponLevel(t, u8(gs, a + O.TEAM));
  const saved = t.weapon[lvl];
  if (t.specialWeapon !== 0) t.weapon[lvl] = t.specialWeapon;
  const r = fireWeapon(G, obj, target, 0, 0);
  t.weapon[lvl] = saved;
  return r;
}

// ---- damage and death -------------------------------------------------------------------------------

/**
 * 0x441B4C apply_damage(gs, victim, weapon*, fraction, attackerPlayer, night, killer).
 * `weapon` is the weapon record.
 */
export function applyDamage(G, victim, weapon, fraction, attackerPlayer, night, killer) {
  const gs = G.gs;
  const va = objAddr(victim);
  const vtype = u8(gs, va + O.TYPE);
  const vteam = u8(gs, va + O.TEAM);
  const vt = T(G, vtype);
  const mb = MB(G, weapon.weaponClass, vt.defenceClass);
  const hp = i32(gs, va + O.HP);
  const mult = vt.armour[vt.armourLevel[vteam]];
  let d = (mb * weapon.damage) >> 8;
  d = (fraction * d) >> 8;
  d = (d * mult) >> 8;
  if (night !== 0) d = (d * 3) >> 2;
  const newHp = hp - d;
  const damaged = u8(gs, va + O.DAMAGED);
  w32(gs, va + O.HP, newHp);
  if (damaged + newHp < 0xff) w8(gs, va + O.DAMAGED, damaged - (newHp & 0xff));
  else w8(gs, va + O.DAMAGED, 0xff);
  w8(gs, va + O.REDRAW1, 1);
  w8(gs, va + O.REDRAW2, 1);
  if (newHp > 0) return;
  if (vteam !== attackerPlayer) {
    G.addStat(2, attackerPlayer, 1);
    G.addTypeStat(3, attackerPlayer, u8(gs, objAddr(killer) + O.TYPE), 1);
  }
  objectDie(G, victim);
  Grid.removeObject(G, victim);
  G.addStat(3, vteam, 1);
  G.addTypeStat(0, vteam, vtype, 1);
  if (attackerPlayer < 8) {
    const hero = i16(gs, playerAddr(attackerPlayer) + P.HEROES);
    if (hero !== -1) {
      const ha = objAddr(hero);
      let dx = (u16(gs, ha + O.X) >> 8) - (u16(gs, va + O.X) >> 8);
      let dz = (u16(gs, ha + O.Z) >> 8) - (u16(gs, va + O.Z) >> 8);
      if (dx < 0) dx = -dx;
      if (dz < 0) dz = -dz;
      if (dx + dz < 20) G.addStat(11, attackerPlayer, 1);
    }
  }
}

/** 0x4165A0 object_die(gs, obj): corpse state, slot bookkeeping, hero-death event. */
export function objectDie(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  w8(gs, a + O.SP, 0xff);
  w8(gs, a + O.STACK, 0);
  w8(gs, a + O.STACK + 1, 0);
  w8(gs, a + O.LIFE, 10);
  const team = u8(gs, a + O.TEAM);
  w8(gs, a + 0x13, 0);
  if (team <= 8) w8(gs, a + O.SELECT, u8(gs, a + O.SELECT) & ~((1 << team) & 0xff));
  if (obj < 120) {
    w32(gs, playerAddr(idiv(obj, 15)) + P.SLOT_HP + irem(obj, 15) * 4, 0);
    City.depRecompute(G); // 0x437D00
    // 0x431F60(gs, type, 3, 1, x, z): death sound
  }
  const t = T(G, u8(gs, a + O.TYPE));
  if (t.rallySize !== 0 && u8(gs, GS.NET_FLAG) === 0 && i32(gs, playerAddr(team) + P_WORD_1C) === 0) {
    // TODO(exact): A[3..4] and B[1..4] are uninitialised stack bytes in the original
    const lo = Uint8Array.from([0, (obj >> 8) & 0xff, obj & 0xff, 0, 0]);
    const hi = Uint8Array.from([0, 0, 0, 0, 0]);
    dropShipEvent(G, team, u16(gs, a + O.X) >> 8, u16(gs, a + O.Z) >> 8, hi, lo);
  }
  const info = Ticker.pushState(G, obj, 0x0a, 2);
  w16(gs, info, 0);
  w16(gs, info + 2, 0);
}

/** 0x4168C0: push the falling-wreck state 0x11 (flyers). */
export function pushWreck(G, obj) {
  Ticker.pushState(G, obj, 0x11, 0);
  return 0;
}

/** state 0xA, 0x4166F8: corpse — DIE animation on the first tick, freed after 150 ticks. */
export function stateCorpse(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = T(G, u8(gs, a + O.TYPE));
  if (i16(gs, info) === 0) {
    const r = G.rand(); // rand(): DIE sprite (also for commanders)
    const idx = irem(r, t.numDie);
    if (t.rallySize !== 0) Anim.startAnim(G, a + O.ANIM0, t.funk, 3);
    else Anim.startAnim(G, a + O.ANIM0, t.die[idx], 1);
  }
  if (t.rallySize !== 0) {
    if (i16(gs, info) === 0) w16(gs, info, 1); // commanders: stuck at 1, never freed
  } else w16(gs, info, i16(gs, info) + 1);
  if (i16(gs, info) === 150) {
    w8(gs, a + O.LIFE, 0);
    w16(gs, GS.OBJ_ALLOC + obj * 2, -1);
  }
  return 0;
}

/** state 0x11, 0x4167E0: falling wreck — height -= 2*speed per tick, then crash animation. */
export function stateWreck(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = T(G, u8(gs, a + O.TYPE));
  const h = u16(gs, a + O.HEIGHT);
  if (h > 0) {
    let nh = h - 2 * t.speed;
    if (nh < 0) nh = 0;
    w16(gs, a + O.HEIGHT, nh);
    return 0;
  }
  Anim.startAnim(G, a + O.ANIM0, t.deploy, 1); // +0x94
  // if (team == gs->local_player) 0x431F60(gs, type, 5, 1, x, z): sound
  const info = Ticker.pushState(G, obj, 0x0d, 1);
  w16(gs, info, 50);
  return 0;
}

// ---- healing ---------------------------------------------------------------------------------------

/** 0x413F24 heal_nearby(gs, obj*, obj): healers (types 49/50) in the idle state. */
export function healNearby(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  const map = G.map;
  const ox = u16(gs, a + O.X) >> 8;
  const oz = u16(gs, a + O.Z) >> 8;
  const t = T(G, u8(gs, a + O.TYPE));
  let healed = 0;
  scan: for (let ring = 0; ring < 8; ring++) {
    for (let cx = ox - ring; cx <= ox + ring; cx++) {
      for (let cz = oz - ring; cz <= oz + ring; cz++) {
        if (cx < 0 || cz < 0 || cx >= map.w || cz >= map.h) continue;
        if (u8(gs, a + O.CHARGE) < 4) break scan;
        for (let layer = 0; layer < 2; layer++) {
          G.assert(inMap(G, cx, cz), 'x>=0 && y>=0 && x<gs->map->xsize && y<gs->map->ysize'); // ticker.c:1131
          const id = (layer === 1 ? Grid.cellGround(G, cx, cz) : Grid.cellAir(G, cx, cz)) & 0x3ff;
          if (id !== 0x3ff && id !== 0x3fe) {
            const o = objAddr(id);
            if (u8(gs, o + O.LIFE) !== 10 && u8(gs, a + O.TEAM) === u8(gs, o + O.TEAM)) {
              const ot = T(G, u8(gs, o + O.TYPE));
              const maxHp = ot.hp;
              const missing = maxHp - i32(gs, o + O.HP);
              const mb = MB(G, 7, ot.defenceClass);
              const rate = idiv(mb * 36, 256);
              G.assert(missing >= 0, 'Maxhits missing hits object'); // ticker.c:1162 (printf + assert(0))
              if (missing !== 0) {
                healed = 1;
                if (rate > missing) {
                  if (u8(gs, o + O.ANIM2_STATUS) !== 1) Anim.startAnim(G, o + O.ANIM2, t.fire[0], 1);
                  w32(gs, o + O.HP, i32(gs, o + O.HP) + missing);
                  G.addStat(9, u8(gs, o + O.TEAM), missing);
                  // charge -= (missing << 8) / 4 / mb, underflow -> 0, then unconditionally 0
                  w8(gs, a + O.CHARGE, 0);
                  G.assert(i32(gs, o + O.HP) <= maxHp, 'hptr->hit_points <=maxhits'); // ticker.c:1206
                } else {
                  w8(gs, a + O.CHARGE, 0);
                  w32(gs, o + O.HP, i32(gs, o + O.HP) + rate);
                  if (u8(gs, o + O.ANIM2_STATUS) !== 1) Anim.startAnim(G, o + O.ANIM2, t.fire[0], 1);
                  G.addStat(9, u8(gs, o + O.TEAM), rate);
                  G.assert(i32(gs, o + O.HP) <= maxHp, 'hptr->hit_points <=maxhits');
                }
              }
            }
          }
          if (layer === 0 && u8(gs, a + O.CHARGE) < 4) break scan;
        }
      }
    }
  }
  if (healed !== 0) {
    Anim.startAnim(G, a + O.ANIM0, t.deploy, 1); // +0x94
    // 0x431F60(gs, type, 5, 1, x, z): sound
    const info = Ticker.pushState(G, obj, 0x0d, 1);
    w16(gs, info, 50);
  } else Ticker.resetAndDispatchOrder(G, obj);
}

// ---- fire at ground / targeted specials ------------------------------------------------------------

/** order 0x12, 0x418160: fire at waypoint 0 (artillery: normal weapon, others: special weapon). */
export function orderFireAtGround(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  const type = u8(gs, a + O.TYPE);
  const t = T(G, type);
  const wIdx = type === 0x0b || type === 3 ? t.weapon[weaponLevel(t, u8(gs, a + O.TEAM))] : t.specialWeapon;
  if (wIdx === 0) {
    Ticker.resetAndDispatchOrder(G, obj);
    return;
  }
  const w = W(G, wIdx);
  const wx = u16(gs, a + O.WAYPOINTS);
  const wz = u16(gs, a + O.WAYPOINTS + 2);
  const dx = wx - u16(gs, a + O.X);
  const dz = wz - u16(gs, a + O.Z);
  const info = Ticker.pushState(G, obj, 0x12, 4);
  const r2 = (w.range * w.range) << 16;
  if (dx * dx + dz * dz > r2) {
    w16(gs, a + O.DEST_X, wx);
    w16(gs, a + O.DEST_Z, wz);
    Move.beginMove(G, obj, 4, 0);
  }
  w16(gs, info, wx);
  w16(gs, info + 6, 0);
  w16(gs, info + 2, wz);
}

/** state 0x12, 0x4182C0: info = { tx, tz, startTime, started }. */
export function stateFireAtGround(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  if (i16(gs, info + 6) === 0) {
    w16(gs, info + 6, 1);
    w16(gs, info + 4, i32(gs, GS.GAME_TIME));
  }
  const type = u8(gs, a + O.TYPE);
  if (type === 0x0b || type === 3) {
    if (u8(gs, a + O.PENDING) !== 0) {
      Ticker.resetAndDispatchOrder(G, obj);
      return 1;
    }
    return fireWeapon(G, obj, -1, u16(gs, info), u16(gs, info + 2));
  }
  const t = T(G, type);
  const team = u8(gs, a + O.TEAM);
  const abort = () => {
    Ticker.resetAndDispatchOrder(G, obj);
    return 1;
  };
  if (t.specialWeapon === 0) return abort();
  // 0x41837C / 0x418390: byte [0x510188 + team] / [0x510A48 + team] == 2. Those addresses are
  // object_types[4] + 0x30 and object_types[12] + 0x30, i.e. the cyborg's / psy-raider's WEAPON
  // UPGRADE LEVEL of that player (set by command 0x0C), not separate research bytes (found with
  // the first recorded game, 11 Sep 2026: the napalm shot at tick 9715 was refused here).
  if (type === 4 && G.tables.types[4].weaponLevel[team] !== 2) return abort();
  if (type === 0x0c && G.tables.types[0x0c].weaponLevel[team] !== 2) return abort();
  if (type >= 0x45 && type <= 0x4c && u8(gs, GS.NET_FLAG) !== 0) return abort();
  if (u16(gs, GS.GAME_TIME) - u16(gs, info + 4) > 4) {
    Ticker.resetAndDispatchOrder(G, obj);
    w8(gs, a + O.CHARGE, 0);
    return 1;
  }
  if (u8(gs, a + O.CHARGE) < 0xff) return abort();
  if (u8(gs, a + O.PENDING) !== 0) return abort();
  w16(gs, info + 4, i32(gs, GS.GAME_TIME));
  const lvl = weaponLevel(t, team);
  const saved = t.weapon[lvl];
  t.weapon[lvl] = t.specialWeapon;
  const r = fireWeapon(G, obj, -1, u16(gs, info), u16(gs, info + 2));
  const t2 = T(G, u8(gs, a + O.TYPE));
  t2.weapon[weaponLevel(t2, u8(gs, a + O.TEAM))] = saved;
  return r;
}

// ---- deploy / artifacts ----------------------------------------------------------------------------

/** state 0xD, 0x417DA4: after the animation finished, transform / trigger the artifact effect. */
export function stateDeploy(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  if (u8(gs, a + O.ANIM0_STATUS) !== 2) return 0;
  const type = u8(gs, a + O.TYPE);
  if (type === 0x3f) {
    // LENS: suicide blast with weapons[0] (kind 3) at its own position
    w32(gs, a + O.HP, 1);
    Missile.createMissile(G, obj, 0, 0, 0, T(G, type).weapon[0], 0, 0, 0, 0, 0, 3);
    objectDie(G, obj);
    Grid.removeObject(G, obj);
    return 0;
  }
  if (type === 0x40) {
    objectDie(G, obj);
    Grid.removeObject(G, obj);
    artifactMakt(G, u16(gs, a + O.X), 0, u16(gs, a + O.Z));
    return 0;
  }
  if (type === 0x41) {
    objectDie(G, obj);
    Grid.removeObject(G, obj);
    artifactLuna(G, u16(gs, a + O.X), u16(gs, a + O.Z));
    return 0;
  }
  if (type === 0x43) {
    objectDie(G, obj);
    Grid.removeObject(G, obj);
    artifactTekt(G, u16(gs, a + O.X), u16(gs, a + O.Z), u8(gs, a + O.TEAM));
    return 0;
  }
  if (type === 0x42) {
    artifactHyyk(G, obj);
    return 0;
  }
  const t = T(G, type);
  if (t.rallyBonus !== 0) {
    rally(G, u16(gs, a + O.X), u16(gs, a + O.Z), obj, t.rallySize);
    return 0;
  }
  let skipMine = false;
  switch (type) {
    case 1:
      w8(gs, a + O.TYPE, 0x29);
      skipMine = true;
      break;
    case 9:
      w8(gs, a + O.TYPE, 0x2a);
      break;
    case 4:
      w8(gs, a + O.TYPE, 0x4d);
      break;
    case 0x0c:
      w8(gs, a + O.TYPE, 0x4e);
      break;
    case 0x4d:
      w8(gs, a + O.TYPE, 4);
      break;
    case 0x4e:
      w8(gs, a + O.TYPE, 0x0c);
      break;
    case 0x2f:
      w8(gs, a + O.TYPE, 6);
      break;
    case 0x30:
      w8(gs, a + O.TYPE, 0x0e);
      break;
    default:
      break;
  }
  if (!skipMine) {
    const nt = u8(gs, a + O.TYPE);
    if (nt === 0x2b || nt === 0x2c) {
      // engineers become mines in the secondary layer
      Grid.removeObject(G, obj);
      w8(gs, a + O.TYPE, nt + 2);
      const tx = u16(gs, a + O.X) >> 8;
      const tz = u16(gs, a + O.Z) >> 8;
      const sec = Grid.cellSecondary(G, tx, tz) & 0xfc00;
      Grid.setSecondary(G, tx, tz, sec | obj);
    }
  }
  const newType = u8(gs, a + O.TYPE);
  Anim.startAnim(G, a + O.ANIM0, T(G, newType).stand, 0);
  Ticker.resetAndDispatchOrder(G, obj);
  if (newType === 0x4d || newType === 0x4e) {
    const target = stealingSearch(G, u16(gs, a + O.X) >> 8, u16(gs, a + O.Z) >> 8, u8(gs, a + O.TEAM));
    if (target === -1) {
      Ticker.resetAndDispatchOrder(G, obj);
      Ticker.orderDeploy(G, obj);
      return 0;
    }
    // idle info[0] of the stealer = target; idle info[2] of the target = stealer (if free)
    const sp = i8(gs, a + O.SP);
    const myInfo = a + O.INFO + u8(gs, a + O.STACK + 1 + 2 * sp) * 2;
    w16(gs, myInfo, target);
    const ta = objAddr(target);
    const tsp = i8(gs, ta + O.SP);
    const tInfo = ta + O.INFO + u8(gs, ta + O.STACK + 1 + 2 * tsp) * 2;
    if (i16(gs, tInfo + 4) !== 0) {
      Ticker.resetAndDispatchOrder(G, obj);
      Ticker.orderDeploy(G, obj);
      return 0;
    }
    w16(gs, tInfo + 4, obj);
  }
  return 0;
}

/** 0x416BE8: |dx| + |dy| + |dz|. */
function dist3(dx, dy, dz) {
  let s = dx < 0 ? -dx : dx;
  s += dy < 0 ? -dy : dy;
  s += dz < 0 ? -dz : dz;
  return s;
}

/**
 * 0x416C08 (MAKT effect on one object): fling a mobile living unit away from (x, y, z) —
 * state 0xF with info { 0, x, y, z, dist, dx*256/dist, dy*256/dist, dz*256/dist, dir+128 },
 * life = 10, MOVE animation.
 */
export function maktFling(G, obj, x, y, z) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = T(G, u8(gs, a + O.TYPE));
  if (t.speed === 0) return;
  const life = u8(gs, a + O.LIFE);
  if (life === 0 || life === 10) return;
  Grid.removeObject(G, obj);
  w8(gs, a + O.SP, 0xff);
  w8(gs, a + O.STACK, 0);
  w8(gs, a + O.STACK + 1, 0);
  w8(gs, a + 0x13, 0);
  const info = Ticker.pushState(G, obj, 0x0f, 9);
  w8(gs, a + O.LIFE, 10);
  w16(gs, info, 0);
  w16(gs, info + 2, x);
  w16(gs, info + 4, y);
  w16(gs, info + 6, z);
  const dx = u16(gs, a + O.X) - x;
  const dy = u16(gs, a + O.HEIGHT) - y;
  const dz = u16(gs, a + O.Z) - z;
  w16(gs, info + 8, dist3(dx, dy, dz));
  const d = i16(gs, info + 8);
  G.assert(d !== 0, 'makt fling: zero distance (idiv by zero in the original)');
  w16(gs, info + 10, idiv(dx << 8, d));
  w16(gs, info + 12, idiv(dy << 8, d));
  w16(gs, info + 14, idiv(dz << 8, d));
  w16(gs, info + 16, (Ticker.dirTo(G, obj, x, z) + 0x80) & 0xff);
  Anim.startAnim(G, a + O.ANIM0, t.move, 0); // +0x7C
}

/** 0x416ED4 MAKT artifact: fling every mobile unit (ground and air layers) within radius 7. */
export function artifactMakt(G, x, y, z) {
  const tx = x >> 8;
  const tz = z >> 8;
  for (let cx = tx - 7; cx <= tx + 7; cx++) {
    for (let cz = tz - 7; cz <= tz + 7; cz++) {
      if (!inMap(G, cx, cz)) continue;
      const id = Grid.cellGround(G, cx, cz) & 0x3ff;
      if (id !== 0x3ff && id !== 0x3fe) maktFling(G, id, x, y, z);
      const id2 = Grid.cellAir(G, cx, cz) & 0x3ff;
      if (id2 !== 0x3ff && id2 !== 0x3fe) maktFling(G, id2, x, y, z);
    }
  }
}

/** 0x416FF4 LUNA artifact: berserk every player-owned unit within radius 7 (pending idle order). */
export function artifactLuna(G, x, z) {
  const gs = G.gs;
  const tx = x >> 8;
  const tz = z >> 8;
  const affect = (id) => {
    const o = objAddr(id);
    const life = u8(gs, o + O.LIFE);
    if (life === 0 || life === 10) return false; // breaks the inner (cz) loop
    if (u8(gs, o + O.TEAM) >= 8) return false;
    w8(gs, o + O.PENDING, 1);
    w8(gs, o + O.ORDER, 1);
    const r = G.rand(); // rand(): berserk duration
    w8(gs, o + O.BERSERK, (r & 0x1f) + 0x19);
    return true;
  };
  for (let cx = tx - 7; cx <= tx + 7; cx++) {
    for (let cz = tz - 7; cz <= tz + 7; cz++) {
      if (!inMap(G, cx, cz)) continue;
      const id = Grid.cellGround(G, cx, cz) & 0x3ff;
      if (id !== 0x3ff && id !== 0x3fe && !affect(id)) break;
      const id2 = Grid.cellAir(G, cx, cz) & 0x3ff;
      if (id2 !== 0x3ff && id2 !== 0x3fe && !affect(id2)) break;
    }
  }
}

/** 0x417644 TEKT artifact: convert wildlife (team 9) within 128 tiles to `team`, up to the unit cap. */
export function artifactTekt(G, x, z, team) {
  const gs = G.gs;
  let room = i32(gs, GS.UNIT_CAP) - G.stat(6, team);
  const tx = x >> 8;
  const tz = z >> 8;
  for (let cx = tx - 0x80; cx <= tx + 0x80; cx++) {
    for (let cz = tz - 0x80; cz <= tz + 0x80; cz++) {
      if (room < 0) return;
      if (!inMap(G, cx, cz)) continue;
      const id = Grid.cellGround(G, cx, cz) & 0x3ff;
      if (id !== 0x3ff && id !== 0x3fe && u8(gs, objAddr(id) + O.TEAM) === 9) {
        room--;
        w8(gs, objAddr(id) + O.TEAM, team);
      }
      const id2 = Grid.cellAir(G, cx, cz) & 0x3ff;
      if (id2 !== 0x3ff && id2 !== 0x3fe && u8(gs, objAddr(id2) + O.TEAM) === 9) {
        room--;
        w8(gs, objAddr(id2) + O.TEAM, team);
      }
    }
  }
}

/** 0x417A6C HYYK artifact: the object and four fresh copies of it enter state 0x10. */
export function artifactHyyk(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = T(G, u8(gs, a + O.TYPE));
  w8(gs, a + O.SP, 0xff);
  w8(gs, a + O.STACK, 0);
  w8(gs, a + O.STACK + 1, 0);
  w8(gs, a + 0x13, 0);
  Anim.startAnim(G, a + O.ANIM0, t.fire[0], 0); // +0xA0
  Grid.removeObject(G, obj);
  for (let i = 0; i < 4; i++) {
    const n = Renat.spawnObject(G, u16(gs, a + O.X) >> 8, u16(gs, a + O.Z) >> 8, u8(gs, a + O.TYPE), u8(gs, a + O.TEAM));
    const na = objAddr(n);
    w16(gs, na + O.X, u16(gs, a + O.X));
    w16(gs, na + O.HEIGHT, u16(gs, a + O.HEIGHT));
    w16(gs, na + O.Z, u16(gs, a + O.Z));
    Grid.removeObject(G, n);
    const info = Ticker.pushState(G, n, 0x10, 5);
    for (let k = 0; k < 5; k++) w16(gs, info + 2 * k, 0);
  }
  const info = Ticker.pushState(G, obj, 0x10, 5);
  for (let k = 0; k < 5; k++) w16(gs, info + 2 * k, 0);
}

/** ring walk shared by rally / abduction / stealingSearch: side 0 left, 1 right, 2 top, 3 bottom. */
function ringSide(tx, tz, ring, k, side) {
  switch (side) {
    case 0:
      return [tx - ring, tz + k];
    case 1:
      return [tx + ring, tz + k];
    case 2:
      return [tx + k, tz - ring];
    default:
      return [tx + k, tz + ring];
  }
}

/** 0x417400 commander rally (gs, x, z, obj, count): link `count` armed same-team units within 10 rings. */
export function rally(G, x, z, obj, count) {
  const gs = G.gs;
  const a = objAddr(obj);
  const tx = x >> 8;
  const tz = z >> 8;
  Ticker.resetAndDispatchOrder(G, obj);
  w8(gs, a + O.CHARGE, 0);
  for (let ring = 0; ring <= 10; ring++) {
    for (let k = -20; k <= 20; k++) { // 0x41746D: k spans -2*10..2*10 for EVERY ring (constant, not the ring)
      for (let side = 0; side < 4; side++) {
        const [cx, cz] = ringSide(tx, tz, ring, k, side);
        if (!inMap(G, cx, cz)) continue;
        for (let layer = 0; layer < 2; layer++) {
          const id = (layer === 0 ? Grid.cellGround(G, cx, cz) : Grid.cellAir(G, cx, cz)) & 0x3ff;
          if (id === 0x3ff || id === 0x3fe) continue;
          const o = objAddr(id);
          if (u8(gs, a + O.TEAM) !== u8(gs, o + O.TEAM)) continue;
          const ot = T(G, u8(gs, o + O.TYPE));
          if (ot.rallyBonus !== 0) continue;
          if (ot.weapon[0] === -1) continue;
          const r = G.rand(); // rand(): link duration
          w8(gs, o + O.LINK_COUNT, (r & 0x0f) + 0x14);
          w16(gs, o + O.LINK_OBJ, obj);
          if (--count === 0) return;
        }
      }
    }
  }
}

/**
 * 0x417BDC stealing search (gs, tx, tz, team): nearest mining tower (types 47/48) of another team
 * within 11 rings that the team currently sees; -1 for none.
 */
export function stealingSearch(G, tx, tz, team) {
  const gs = G.gs;
  for (let ring = 0; ring <= 11; ring++) {
    for (let k = -22; k <= 22; k++) { // 0x417C04: -2*11..2*11 for every ring
      for (let side = 0; side < 4; side++) {
        const [cx, cz] = ringSide(tx, tz, ring, k, side);
        if (!inMap(G, cx, cz)) continue;
        const id = Grid.cellGround(G, cx, cz) & 0x3ff;
        if (id === 0x3ff || id === 0x3fe) continue;
        const o = objAddr(id);
        if (u8(gs, o + O.TEAM) === team) continue;
        const type = u8(gs, o + O.TYPE);
        if (type !== 0x2f && type !== 0x30) continue;
        if (u8(gs, o + O.LIFE) === 0) continue;
        const cell = Grid.cellGround(G, u16(gs, o + O.X) >> 8, u16(gs, o + O.Z) >> 8);
        if ((cell & i32(gs, playerAddr(team) + P.VISION)) === 0) continue;
        const ot = T(G, type);
        if (ot.hidden !== 0 && u8(gs, o + O.TEAM) !== team && (u8(gs, o + O.DETECTED) & (1 << team)) === 0) continue;
        return id;
      }
    }
  }
  return -1;
}

/**
 * 0x4171EC abduction(gs, team, tx, tz, radius, max, out): collect up to `max` mobile, non-commander
 * objects of teams not allied to `team` within `radius` rings into `out` (Int16Array); returns the count.
 */
export function abduction(G, team, tx, tz, radius, max, out) {
  const gs = G.gs;
  let n = 0;
  for (let ring = 0; ring <= radius; ring++) {
    for (let k = -2 * radius; k <= 2 * radius; k++) { // 0x417212: -2*radius..2*radius for every ring
      for (let side = 0; side < 4; side++) {
        const [cx, cz] = ringSide(tx, tz, ring, k, side);
        if (!inMap(G, cx, cz)) continue;
        for (let layer = 0; layer < 2; layer++) {
          const id = (layer === 0 ? Grid.cellGround(G, cx, cz) : Grid.cellAir(G, cx, cz)) & 0x3ff;
          if (id === 0x3ff || id === 0x3fe) continue;
          const o = objAddr(id);
          if (alliance(G, team, u8(gs, o + O.TEAM)) !== 0) continue;
          const ot = T(G, u8(gs, o + O.TYPE));
          if (ot.speed === 0) continue;
          if (ot.rallyBonus !== 0) continue;
          out[n++] = id;
          if (n === max) return n;
        }
      }
    }
  }
  return n;
}

/**
 * 0x418580 (gs, obj, a, b, dur, flag, z): push state 0x16 with info { a, b, dur, flag,
 * flag ? 0 : 50, z } and set the object's height from the descent formula.
 */
export function pushDescend(G, obj, a, b, dur, flag, z) {
  const gs = G.gs;
  const oa = objAddr(obj);
  const info = Ticker.pushState(G, obj, 0x16, 6);
  const step = flag !== 0 ? 0 : 50;
  w16(gs, info, a);
  w16(gs, info + 2, b);
  w16(gs, info + 4, dur);
  w16(gs, info + 6, flag);
  w16(gs, info + 8, step);
  const ia = i16(gs, info);
  const h = idiv(ia * step * step, 2) + step * ia + i16(gs, info + 2);
  w16(gs, oa + O.HEIGHT, h);
  w16(gs, info + 10, z);
}

/**
 * 0x4191E0 drop-ship event (gs, team, tx, tz, hi[], lo[]): create a drop ship (type 92/93) in
 * city-slot object team*15+7+slot, state 0x15 with info[i] = (hi[i] << 8) | lo[i] for i < 5, then
 * the arrival flight and the descent state.
 */
export function dropShipEvent(G, team, tx, tz, hi, lo) {
  const gs = G.gs;
  const pa = playerAddr(team);
  let slot = -1;
  for (let i = 0; i < 8; i++) {
    if (u8(gs, pa + P_DROPSHIP_USED + i) === 0) {
      slot = i;
      break;
    }
  }
  G.assert(slot !== -1, 'o!=-1'); // ticker.c:3776
  w8(gs, pa + P_DROPSHIP_USED + slot, 1);
  const obj = slot + team * 15 + 7;
  const oa = objAddr(obj);
  const dtype = i32(gs, pa + P.RACE) === 1 ? 0x5d : 0x5c;
  Scenario.createObject(G, 0, 0, dtype, 8, obj);
  Ticker.resetAndDispatchOrder(G, obj);
  let r = G.rand(); // rand(): x jitter (bit 0)
  w16(gs, oa + O.X, (tx << 8) + 0x80 + ((r & 1) << 9) - 0x100);
  r = G.rand(); // rand(): z jitter (bit 0)
  w16(gs, oa + O.Z, (tz << 8) + 0x80 + ((r & 1) << 9) - 0x100);
  const info = Ticker.pushState(G, obj, 0x15, 0x0d);
  for (let i = 0; i < 5; i++) w16(gs, info + 2 * i, (hi[i] << 8) | lo[i]);
  w16(gs, info + 10, tx);
  w16(gs, info + 12, tz);
  w16(gs, info + 18, team);
  w16(gs, info + 20, (tx << 8) + 0x80);
  w16(gs, info + 16, 0);
  w16(gs, info + 22, (tz << 8) + 0x80);
  w16(gs, info + 14, 0);
  // info[12] = 0x431A08(dtype == 92 ? 0x2D : 0x52, 1, x, 0x10000, z): looping sound handle, display only
  w16(gs, info + 24, 0);
  Move.startStep(G, obj, u16(gs, oa + O.X) >> 8, u16(gs, oa + O.Z) >> 8, u16(gs, info + 20), u16(gs, info + 22));
  pushDescend(G, obj, 0, 6, dtype === 0x5d ? 0x4b0 : 0x258, 0, i16(gs, info + 22));
}

/**
 * 0x4164BC make_corpse(gs, obj) (renat.js name makeCorpse): empty the state stack, life = 10 (corpse),
 * +0x13 = 0, assert obj >= 120 ("mobiles.c:2282"), STAND animation in mode 1, push state 0xA with
 * info {1, 0}. Used by the trigger `die` path of renat.js.
 */
export function makeCorpse(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  w8(gs, a + O.SP, 0xff);
  w8(gs, a + O.STACK, 0);
  w8(gs, a + O.STACK + 1, 0);
  w8(gs, a + O.LIFE, 10);
  w8(gs, a + 0x13, 0);
  G.assert(obj >= 0x78, 'make_corpse on a city slot (mobiles.c:2282)');
  Anim.startAnim(G, a + O.ANIM0, T(G, u8(gs, a + O.TYPE)).stand, 1);
  const info = Ticker.pushState(G, obj, 0x0a, 2);
  w16(gs, info, 1);
  w16(gs, info + 2, 0);
}

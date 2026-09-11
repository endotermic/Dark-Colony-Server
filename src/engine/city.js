// city.js — city.c, depend.c and the building part of mobiles.c of Classic dc16.exe, ported
// instruction by instruction (see PORTING.md for the rules; addresses are VAs of dc16.asm).
//
// PORT NOTES
// ==========
// Ported instruction by instruction:
//   build_slot            0x4450F4  buildSlot(G, player, slot)
//   start_construction    0x4184C4  startConstruction(G, obj)      (pushes state 0x13)
//   spawn_pod             0x41879C  spawnPod(G, pod, bld, podType, leaving)
//   push_pod_flight       0x418580  pushPodFlight(G, pod, vx, vz, h0, leaving, snd) (pushes state 0x16)
//   state 0x13            0x418A7C  stateConstruct(G, obj, info)   building under construction
//   state 0x14            0x418978  statePodLand(G, obj, info)     drop pod arrived / left
//   state 0x16            0x418650  statePodFlight(G, obj, info)   drop pod descending / ascending
//   building idle         0x41460C  stateBuildingIdle(G, obj)      production (called from the idle
//                                   state 0x414B9C for objects < 120 that are unarmed and immobile)
//   pickups               0x4143D4  pickupsTick(G, obj)            (called from the idle state)
//   slot_to_class         0x41B07C  slotToClass(G, slot)
//   unitdef lookup        0x444E30  unitdefRow(G, row, slot)
//   dep_recompute         0x437D00  depRecompute(G, player)        (player = gs->local_player)
//   dep_check_building    0x43832C  depCheckBuilding(G, player, item) -> { status, slot, level }
//   dep_check_troop       0x4384A8  depCheckTroop(G, player, item)    -> { status, type, cost }
//   costs                 0x438188 itemCost, 0x4381A4 troopCost, 0x4381EC buildingCost
//   item_kind             0x4382D8  itemKind;  slot_blocked 0x4382F4 slotBlocked
//   footprint handles     0x454AA0  registerFootprint, 0x454B28 footprintByHandle
//
// Overlaps with PORTING.md's module table: states 0x13/0x14/0x16 are listed under renat.js and the
// pickups under both renat.js and city.js. They are ported HERE because they are the building
// construction sequence (buildSlot -> state 0x13 -> drop pod -> production) whose timing decides
// when a building starts producing (checksum relevant). The integrator wires STATE_TABLE[0x13],
// [0x14], [0x16] to the exports of this module (state 0x15 0x418CE0 is not ported here).
//
// Disagreements with the docs (the code wins):
//   * DC16_BATTLE_ENGINE.md §15.1: command 0x09 looks the type up as `unitdef[race][level][slot]`.
//     The handler 0x41CAA4 calls 0x444E30(slot, level) = unitdef_flat[level*15 + slot], i.e. row
//     `level` WITHOUT the race, so for an alien player the hit points written to +0xBD8 and the
//     refund test use the HUMAN building of that slot/level. build_slot itself uses row
//     race*2 + level (0x445192). Reproduced.
//   * §15.1 says the refund/cost lookup is by (slot, level); the handler passes race = 0 to
//     0x4381EC (0x41CB2F / 0x41CB55), so only race-0 items match (same costs for both races in
//     DEPEND.TXT, so no visible effect).
//   * §15.2 "damage sprite: STAND above 11/16, SCRCH below that, BURN below 5/16": the code
//     (0x4146B3) shows +0x88 between 5/16 and 11/16 and +0x84 at or below 5/16. With the §2.2
//     labels (+0x84 SCRCH, +0x88 BURN) that is BURN for medium and SCRCH for heavy damage. Display
//     only; the order of the code is reproduced.
//   * §15.2 says the ready flag is cleared and the BUILD animation plays "in the building's third
//     animation slot": confirmed (+0x24, status +0x2A).
//   * mem.js P.SLOT_BLOCKED is documented as int32[15]; every access in the code
//     (0x4184C4, 0x418C2F, 0x437ECB, 0x43831E, 0x438385) is a BYTE at +0xC14 + slot. It is set to 1
//     by start_construction and cleared by state 0x13 when the BUILD animation has ended, so it
//     means "slot under construction" (dep_check_building returns 2 for it).
//   * data/dc16-tables.json `footprints` holds only 48 of the 96 values (slots 0..2 of 6): the
//     table is int32 {dx,dz}[8] per slot, 64 bytes per slot, list terminated by a repeated pair.
//     The full table and the pod landing offsets 0x48AC14 are embedded below.
//   * §3.1 says `+0xCA8[4]` are "unused countdowns": PROD_COUNT is decremented every tick by the
//     idle handler and blocks the production start while non-zero; it is only ever written 0 by
//     the ported code, so it is effectively unused, as the doc says.
//
// Table field names used (tables.js is not written yet; names follow DC16_BATTLE_ENGINE.md §2.2 and
// §15.1, the integrator renames in one place if tables.js differs):
//   object type t:  t.race (+0x04), t.weaponLevel[8] (+0x30), t.armourLevel[8] (+0x38), t.hp (+0x44),
//                   t.flying (+0x60 byte), t.stand (+0x80), t.scrch (+0x84), t.burn (+0x88),
//                   t.build (+0x98, falsy = no BUILD sprite), t.prodClass (+0xEC), t.flyer (+0xF0),
//                   t.deployCode (+0x104), t.hasTargetedSpecial (+0x10C byte)
//   depend item it: it.active (+0x00 byte), it.status (+0x04, mutable), it.cost (+0x08),
//                   it.button (+0x0C), it.kind (+0x10), it.a/it.b/it.c (+0x14/+0x18/+0x1C),
//                   it.deps[5] (+0x20.., -1 terminated). Items are indexed 0..109 (MAX 110).
//
// Cross-module calls (module table names):
//   Ticker.pushState(G, obj, state, nwords) -> infoAddr, Ticker.popState(G, obj),
//   Ticker.resetAndDispatchOrder(G, obj); Anim.startAnim(G, slotAddr, sprite, mode) (0x42626C);
//   Grid.cellGround(G,x,z) / Grid.setCellGround(G,x,z,v) (int32 "run" grid), Grid.cellAir /
//   Grid.setCellAir (int16), Grid.cellLoad(G,x,z) (int32 "load" grid), Grid.removeObject(G, obj)
//   (0x434EB8); Scenario.createObject(G, x, z, type, team, objIndex) (0x41B930, -1 = first free);
//   Combat.objectDie(G, obj) (0x4165A0).
//
// TODO(exact): sound handles (0x431A08 returns one into the pod's state info, 0x431B90/0x431C28
// update/stop it) are stored as 0 — sound only, never enters the simulation.
// TODO(integrator): move P_FUNKY / P_CONSTRUCTING / P_NAME-like offsets into mem.js.

import { GS, O, P, u8, i8, i16, u16, i32, w8, w16, w32, objAddr, playerAddr, idiv, irem, BUILDINGS_PER_SIDE } from './mem.js';
import * as Ticker from './ticker.js';
import * as Anim from './anim.js';
import * as Grid from './grid.js';
import * as Scenario from './scenario.js';
import * as Combat from './combat.js';

// player-block offsets not in mem.js
const P_FUNKY = 0x28; // gs+0xBC0: campaign "funky tower" flag (type 0x51 -> 0x70)
const P_CONSTRUCTING = 0xe16; // gs+0x19AE: byte, one of the player's buildings is under construction

const NUM_DEPEND = 110; // MAX_DEPEND items (0x6E)
const EMPTY = 0x3ff; // low 10 bits of a grid cell: nothing there
const RESERVED = 0x3fe; // spawn tile reserved for a unit being produced
const POD_HUMAN = 0x5c; // object types of the two drop pods
const POD_ALIEN = 0x5d;
const POD_TICKS = 0x32; // 50 ticks descent / ascent

// 0x48AD04: int32 {dx,dz}[8] per building slot 0..5 (64 bytes per slot), tiles relative to the
// city origin. build_slot stops at the first pair equal to the previous one.
const FOOTPRINTS = [
  [-3, 0, -2, 0, -3, 1, -2, 1, -2, 1, -2, 1, -2, 1, -2, 1],
  [-1, -1, 0, -1, -1, -2, 0, -2, 0, -2, 0, -2, 0, -2, 0, -2],
  [0, 2, 1, 2, 0, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3],
  [1, 0, 2, 0, 1, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1],
  [-1, 2, -1, 3, -2, 2, -2, 3, -2, 3, -2, 3, -2, 3, -2, 3],
  [0, 0, -1, 0, -1, -1, 0, -1, 0, 0, 0, 0, 0, 0, 0, 0],
];
// 0x48AC14: int32 {dx,dz} per slot 0..14 in 1/32 tile: where the drop pod lands relative to the
// building (0x444DA0 shifts them left by 3 -> 1/256 tile).
const POD_OFFSETS = [0, 17, 0, -32, 0, 32, 0, 22, 0, 31, 0, -32, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];

function footprint(G, slot, i, k) {
  const v = G.consts?.footprints?.values;
  if (v && v.length >= 96) return v[slot * 16 + i * 2 + k];
  return FOOTPRINTS[slot][i * 2 + k];
}

// ---- tables ---------------------------------------------------------------------------------------

/** 0x41B07C: slot -> production class (table 0x41B040: 2 HQ, 0 barracks, 1 factory, 3 research, 4 none). */
export function slotToClass(G, slot) {
  return G.consts.slotToClass.values[slot];
}

/** 0x444E30(slot, row): unitdef_flat[row*15 + slot] of the [4][15] table 0x48B0C4 (row = race*2+level). */
export function unitdefRow(G, row, slot) {
  const u = G.consts.unitdef.values;
  const flat = row * 15 + slot;
  return u[(flat / 15) | 0]?.[flat % 15];
}

function dependItem(G, i) {
  return G.tables.depend[i];
}

function dependency(it, d) {
  const v = it.deps ? it.deps[d] : undefined;
  return v === undefined ? -1 : v;
}

// ---- costs (depend.c) -----------------------------------------------------------------------------

/** 0x438188: cost of item `item`. */
export function itemCost(G, item) {
  return dependItem(G, item).cost;
}

/** 0x4381A4: cost of the first troop item (kind 1) producing `type`, 0 if none. No `active` test. */
export function troopCost(G, type) {
  for (let i = 0; i < NUM_DEPEND; i++) {
    const it = dependItem(G, i);
    if (it && it.kind === 1 && it.a === type) return it.cost;
  }
  return 0;
}

/** 0x4381EC: cost of the first building item (kind 0) with (slot, level, race), 0 if none. */
export function buildingCost(G, slot, level, race) {
  for (let i = 0; i < NUM_DEPEND; i++) {
    const it = dependItem(G, i);
    if (it && it.kind === 0 && it.a === slot && it.b === level && it.c === race) return it.cost;
  }
  return 0;
}

/** 0x4382D8: kind of item `item`. */
export function itemKind(G, item) {
  return dependItem(G, item).kind;
}

/** 0x4382F4(gs, player, item): the item's slot is under construction (byte +0xC14[slot]). */
export function slotBlocked(G, player, item) {
  return u8(G.gs, playerAddr(player) + P.SLOT_BLOCKED + dependItem(G, item).a) !== 0;
}

// ---- dependency checks ----------------------------------------------------------------------------

/**
 * 0x43832C(gs, player, item, &slot, &level): 0 = already built, 1 = buildable (slot/level filled in),
 * 2 = unavailable (inactive, slot under construction, disabled by a trigger, not a building, wrong
 * race, self-dependency or a dependency not yet built — recursively).
 */
export function depCheckBuilding(G, player, item) {
  const gs = G.gs;
  const pa = playerAddr(player);
  const it = dependItem(G, item);
  if (!it || !it.active) return { status: 2 };
  if (it.kind === 0 && u8(gs, pa + P.SLOT_BLOCKED + it.a) !== 0) return { status: 2 };
  if (u8(gs, pa + P.DISABLED + item) !== 0) return { status: 2 };
  if (it.kind !== 0) return { status: 2 };
  if (i32(gs, pa + P.RACE) !== it.c) return { status: 2 };
  const slot = it.a;
  const level = it.b;
  if (i32(gs, pa + P.SLOT_HP + slot * 4) !== 0 && level <= i32(gs, pa + P.SLOT_LEVEL + slot * 4)) return { status: 0 };
  for (let d = 0; d < 5; d++) {
    const dp = dependency(it, d);
    if (dp < 0) break;
    if (dp === item) return { status: 2 };
    if (depCheckBuilding(G, player, dp).status !== 0) return { status: 2 };
  }
  return { status: 1, slot, level };
}

/**
 * 0x4384A8(gs, player, item, &type, &cost): 1 = the troop item can be bought (type/cost filled in),
 * 2 = not (inactive, disabled, not a troop item, wrong race, or a building dependency not built).
 */
export function depCheckTroop(G, player, item) {
  const gs = G.gs;
  const pa = playerAddr(player);
  const it = dependItem(G, item);
  if (!it || !it.active) return { status: 2 };
  if (u8(gs, pa + P.DISABLED + item) !== 0) return { status: 2 };
  if (it.kind !== 1) return { status: 2 };
  if (G.tables.types[it.a].race !== i32(gs, pa + P.RACE)) return { status: 2 };
  for (let d = 0; d < 5; d++) {
    const dp = dependency(it, d);
    if (dp < 0) break;
    if (depCheckBuilding(G, player, dp).status !== 0) return { status: 2 };
  }
  return { status: 1, type: it.a, cost: it.cost };
}

/**
 * 0x437D00(gs): refresh the status byte (+4) of every active item for the build menu: 0 done,
 * 1 available, 2 blocked. The original always uses gs->local_player; `player` is that player.
 * Display state only (the AI uses depCheckBuilding/depCheckTroop), so the server never needs it;
 * the command handlers call it exactly where the original does (player == local player).
 */
export function depRecompute(G, player) {
  const gs = G.gs;
  const pa = playerAddr(player);
  for (let i = 0; i < NUM_DEPEND; i++) {
    const it = dependItem(G, i);
    if (!it || !it.active) continue;
    let status = 1;
    let done = false;
    switch (it.kind) {
      case 0:
        if (
          i32(gs, pa + P.SLOT_HP + it.a * 4) !== 0 &&
          it.b <= i32(gs, pa + P.SLOT_LEVEL + it.a * 4) &&
          it.c === i32(gs, pa + P.RACE)
        )
          done = true;
        break;
      case 1:
        break;
      case 2: {
        const t = G.tables.types[it.a];
        if (it.b === 1) {
          if ((t.armourLevel[player] & 0xff) >= it.c) done = true;
        } else if (it.b === 0) {
          if ((t.weaponLevel[player] & 0xff) >= it.c) done = true;
        } else G.assert(false, '0 (depend.c:182 unknown upgrade kind)');
        break;
      }
      default:
        G.assert(false, '0 (depend.c:187 unknown item kind)');
    }
    if (done) {
      it.status = 0;
      continue;
    }
    for (let d = 0; d < 5; d++) {
      const dp = dependency(it, d);
      if (dp === -1) break;
      if (dp === i) {
        status = 2;
        continue;
      }
      const di = dependItem(G, dp) ?? { status: 0, kind: -1 };
      if (di.status !== 0) {
        status = 2;
        continue;
      }
      if (di.kind === 0 && u8(gs, pa + P.SLOT_BLOCKED + di.a) !== 0) status = 2;
    }
    if (it.kind === 0 && u8(gs, pa + P.SLOT_BLOCKED + it.a) !== 0) status = 2;
    if (u8(gs, pa + P.DISABLED + i) !== 0) status = 2;
    it.status = status;
  }
  G.globals.dependDirty = 1; // byte 0x4897A0: build menu needs a repaint (display only)
}

// ---- footprint handles (pervasve.c) ---------------------------------------------------------------

/**
 * 0x454AA0(value): register a pervasive structure type; returns the 1-based handle (assert < 16).
 * Called by the GAMESTAT loader (0x43BE0A) for every type whose column 17 is non-zero; the handle
 * is stored in object_types[].+0x78. Ground cells of such structures carry
 * `(handle << 3 | player) << 10` (read back at 0x4388CA with footprintByHandle).
 */
export function registerFootprint(G, value) {
  const g = G.globals;
  g.footprintCount = (g.footprintCount ?? 0) + 1; // 0x48C190
  G.assert(g.footprintCount < 16, 'building_count<MAX_PERVASIVE');
  if (!g.footprints) g.footprints = [];
  g.footprints[g.footprintCount] = value; // 0x5360B4[handle]
  return g.footprintCount;
}

/** 0x454B28(handle). */
export function footprintByHandle(G, handle) {
  return G.globals.footprints ? G.globals.footprints[handle] : undefined;
}

// ---- construction ---------------------------------------------------------------------------------

/**
 * build_slot 0x4450F4(gs, player, slot): (re)initialise building object player*15+slot from the
 * player's slot fields (+0xBD8 hit points, +0xC60 level), write its footprint into the ground grid
 * (not for the tower slot 5), allocate it and reset the production class it feeds.
 */
export function buildSlot(G, player, slot) {
  const gs = G.gs;
  const obj = player * BUILDINGS_PER_SIDE + slot;
  const a = objAddr(obj);
  const pa = playerAddr(player);
  if (i32(gs, pa + P.SLOT_HP + slot * 4) === 0) {
    w8(gs, a + O.LIFE, 0);
    return;
  }
  const level = i32(gs, pa + P.SLOT_LEVEL + slot * 4);
  const race = i32(gs, pa + P.RACE);
  const tableType = unitdefRow(G, race * 2 + level, slot);
  let type = tableType;
  if (type === 0x51 && i32(gs, pa + P_FUNKY) !== 0) type = 0x70;
  // compared with the raw table value, before the funky-tower swap (0x4451E5)
  const changed = u8(gs, a + O.TYPE) !== tableType;
  w8(gs, a + O.TYPE, type);
  const t = G.tables.types[type];
  Anim.startAnim(G, a + O.ANIM0, t.stand, 0);
  Anim.startAnim(G, a + O.ANIM1, t.stand, 2);
  Anim.startAnim(G, a + O.ANIM2, t.stand, 2);
  w32(gs, a + O.HP, i32(gs, pa + P.SLOT_HP + slot * 4));
  const life = u8(gs, a + O.LIFE);
  if (life !== 0 && life !== 10) {
    // alive: an upgrade of a standing building only plays the BUILD animation
    if (!changed) return;
    Anim.startAnim(G, a + O.ANIM0, t.build, 1);
    return;
  }
  // free or corpse: place it. 16-bit loads with stale upper halves in the original; only the low
  // 16 bits reach the stores, so plain arithmetic + w16 is identical.
  const so = G.consts.slotOffsets.values; // 0x48AC8C, 1/32 tile
  w16(gs, a + O.X, (so[slot * 2] << 3) + (i16(gs, pa + P.CITY_X) << 8));
  w16(gs, a + O.HEIGHT, 0);
  w8(gs, a + O.NUDGE, 0xff);
  w8(gs, a + O.HEADING, 0);
  w8(gs, a + O.LIFE, 1);
  w8(gs, a + O.PENDING, 0);
  w8(gs, a + O.WP_COUNT, 0);
  w16(gs, a + O.Z, (so[slot * 2 + 1] << 3) + (i16(gs, pa + P.CITY_Z) << 8));
  w8(gs, a + O.TEAM, player);
  Anim.startAnim(G, a + O.ANIM0, t.stand, 0);
  Anim.startAnim(G, a + O.ANIM1, t.stand, 2);
  Ticker.resetAndDispatchOrder(G, obj); // 0x41233C
  startConstruction(G, obj); // 0x4184C4
  if (slot !== 5) {
    const cx = i32(gs, pa + P.CITY_X);
    const cz = i32(gs, pa + P.CITY_Z);
    let px = -1;
    let pz = -1;
    for (let i = 0; i < 8; i++) {
      const fx = cx + footprint(G, slot, i, 0);
      const fz = cz + footprint(G, slot, i, 1);
      if (fx === px && fz === pz) break;
      px = fx;
      pz = fz;
      G.assert((Grid.cellLoad(G, fx, fz) & 0x80000000) !== 0, 'gs->map->load[gs->map->ysize-1-zs][xs]&0x80000000');
      const cell = Grid.cellGround(G, fx, fz);
      const occ = cell & 0x3ff;
      if (occ !== EMPTY && occ !== RESERVED) {
        // printf("building %d slot %d oobj type %d (%d %d) num %d\n", slot, i, type of occ, fx, fz, occ)
        G.assert(
          occ === obj,
          `(gs->map->run[zs][xs] & OCCUPIED_MASK) == o (building ${slot} slot ${i} oobj type ${u8(gs, objAddr(occ) + O.TYPE)} (${fx} ${fz}) num ${occ})`,
        );
      }
      Grid.setCellGround(G, fx, fz, (cell & ~0x3ff) | obj);
    }
  }
  w16(gs, GS.OBJ_ALLOC + obj * 2, obj);
  if (obj > i32(gs, GS.MAX_OBJ)) w32(gs, GS.MAX_OBJ, obj);
  const k = slotToClass(G, slot);
  if (k === 4) return;
  w16(gs, pa + P.QUEUE_LEN + 2 * k, 0);
  w8(gs, pa + P.PROD_COUNT + k, 0);
  w8(gs, pa + P.PROD_READY + k, 1);
}

/** 0x4184C4(gs, obj): STAND animation, push state 0x13 {0}, mark the slot as under construction. */
export function startConstruction(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = G.tables.types[u8(gs, a + O.TYPE)];
  Anim.startAnim(G, a + O.ANIM0, t.stand, 2);
  const info = Ticker.pushState(G, obj, 0x13, 1);
  w16(gs, info, 0);
  const team = u8(gs, a + O.TEAM);
  w8(gs, playerAddr(team) + P.SLOT_BLOCKED + irem(obj, BUILDINGS_PER_SIDE), 1);
  if (team === i32(gs, GS.LOCAL_PLAYER)) depRecompute(G, team); // display only
}

/**
 * 0x41879C(gs, pod, bld, podType, leaving): create the drop-pod object `pod` (= team*15+6, type
 * 0x5C human / 0x5D alien, team 8) above building `bld` and push states 0x14 {bld, 0, x, z, snd} and
 * 0x16 (flight). leaving = 0: it descends onto the building; 1: it takes off again.
 */
export function spawnPod(G, pod, bld, podType, leaving) {
  const gs = G.gs;
  const ba = objAddr(bld);
  const pa = objAddr(pod);
  // 0x444DA0(slot, &dx, &dz)
  const slot = irem(bld, BUILDINGS_PER_SIDE);
  G.assert(slot >= 0 && slot <= 15, 'building>=0 && building<=BUILDINGS_PER_SIDE');
  const dx = POD_OFFSETS[slot * 2] << 3;
  const dz = POD_OFFSETS[slot * 2 + 1] << 3;
  Scenario.createObject(G, 0, 0, podType, 8, pod); // 0x41B930
  w16(gs, pa + O.HEIGHT, podType === POD_HUMAN ? 0x384 : 0x584);
  const info = Ticker.pushState(G, pod, 0x14, 5);
  if (leaving === 0) {
    w16(gs, pa + O.X, i16(gs, ba + O.X) + dx);
    w16(gs, pa + O.Z, i16(gs, ba + O.Z) + dz);
    // info[4] = 0x431A08(podType == 0x5C ? 0x2D : 0x52, 1, 0x10000, pod.z): sound handle, sound only
    w16(gs, info + 8, 0);
    pushPodFlight(G, pod, 0, 6, u16(gs, pa + O.HEIGHT), 0, i16(gs, info + 8));
    w16(gs, info + 4, i16(gs, ba + O.X) + dx);
    w16(gs, info + 6, i16(gs, ba + O.Z) + dz);
  } else {
    w16(gs, pa + O.X, i16(gs, ba + O.X) + dx);
    w16(gs, pa + O.Z, i16(gs, ba + O.Z) + dz);
    // info[4] is never written on this path: whatever push_state left in the info area is passed on
    pushPodFlight(G, pod, 0, 6, u16(gs, pa + O.HEIGHT), 1, i16(gs, info + 8));
  }
  w16(gs, info + 2, 0);
  w16(gs, info, bld);
}

/**
 * 0x418580(gs, obj, vx, vz, h0, leaving, snd): push state 0x16 {vx, vz, h0, leaving, n, snd} with
 * n = 50 (descending) or 0 (ascending) and set height = vz*n*n/2 + vx*n + h0.
 */
export function pushPodFlight(G, obj, vx, vz, h0, leaving, snd) {
  const gs = G.gs;
  const a = objAddr(obj);
  const t = G.tables.types[u8(gs, a + O.TYPE)];
  const info = Ticker.pushState(G, obj, 0x16, 6);
  Anim.startAnim(G, a + O.ANIM0, t.stand, 0);
  const n = leaving !== 0 ? 0 : POD_TICKS;
  w16(gs, info, vx);
  w16(gs, info + 2, vz);
  const e = i16(gs, info + 2) * n * n;
  w16(gs, info + 4, h0);
  w16(gs, info + 6, leaving);
  w16(gs, info + 8, n);
  w16(gs, a + O.HEIGHT, idiv(e, 2) + i16(gs, info) * n + i16(gs, info + 4));
  w16(gs, info + 10, snd);
}

/** state 0x16 0x418650: drop pod flight, info = {vx, vz, h0, leaving, n, snd}. */
export function statePodFlight(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  const n0 = i16(gs, info + 8);
  // info[5] != -1: 0x431B90(sound id, handle, x, z, height) — sound position update, sound only
  if (i16(gs, info + 6) === 0) {
    const n = i16(gs, info + 8);
    if (n === 0) {
      Ticker.popState(G, obj); // 0x4122A4
      return 0;
    }
    w16(gs, info + 8, n - 1);
  }
  const e = n0 * (i16(gs, info + 2) * n0);
  w16(gs, a + O.HEIGHT, idiv(e, 2) + i16(gs, info) * n0 + i16(gs, info + 4));
  if (i16(gs, info + 6) === 0) return 0;
  const m = i16(gs, info + 8) + 1;
  w16(gs, info + 8, m);
  if (i16(gs, info + 8) !== POD_TICKS) return 0;
  Ticker.popState(G, obj);
  // info[5] != -1: 0x431C28(sound id, handle) — stop the sound, sound only
  return 0;
}

/**
 * state 0x14 0x418978: the pod has finished its flight. info = {bld, phase, x, z, snd}. Looks at the
 * building's current top-of-stack info (its state-0x13 word): 1 (pod descending) -> first pass:
 * snap the pod to (x, z) and re-dispatch; second pass: start the building's BUILD animation and set
 * the building word to 2. Otherwise (pod leaving) set it to 4. Then the pod is freed.
 */
export function statePodLand(G, obj, info) {
  const gs = G.gs;
  const pa = objAddr(obj);
  const bld = i16(gs, info);
  const ba = objAddr(bld);
  const sp = i8(gs, ba + O.SP);
  const off = u8(gs, ba + O.STACK + 1 + 2 * sp); // info offset of the building's top level
  const bInfo = ba + O.INFO + 2 * off;
  if (i16(gs, bInfo) === 1) {
    if (i16(gs, info + 2) === 0) {
      w16(gs, info + 2, 1);
      w16(gs, pa + O.X, i16(gs, info + 4));
      w16(gs, pa + O.Z, i16(gs, info + 6));
      return 1;
    }
    const bt = G.tables.types[u8(gs, ba + O.TYPE)];
    Anim.startAnim(G, ba + O.ANIM0, bt.build, 1);
    w16(gs, bInfo, 2);
  } else {
    w16(gs, bInfo, 4);
  }
  Anim.startAnim(G, pa + O.ANIM0, G.tables.types[u8(gs, pa + O.TYPE)].stand, 2);
  w16(gs, GS.OBJ_ALLOC + obj * 2, -1);
  return 0;
}

/**
 * state 0x13 0x418A7C: building under construction. info[0]: 0 start, 1 pod descending, 2 BUILD
 * animation playing, 3 pod leaving, 4 pod gone, 5 no pod (no BUILD sprite or game tick <= 3).
 * Only one building per player is under construction at a time (byte gs+0x19AE).
 */
export function stateConstruct(G, obj, info) {
  const gs = G.gs;
  const a = objAddr(obj);
  const team = u8(gs, a + O.TEAM);
  const pa = playerAddr(team);
  const t = G.tables.types[u8(gs, a + O.TYPE)];
  if (i16(gs, info) === 0) {
    if (u8(gs, pa + P_CONSTRUCTING) !== 0) return 0;
    w8(gs, pa + P_CONSTRUCTING, 1);
    if (t.build && i32(gs, GS.TICK) > 3) {
      Anim.startAnim(G, a + O.ANIM0, t.build, 2);
      spawnPod(G, team * BUILDINGS_PER_SIDE + 6, obj, i32(gs, pa + P.RACE) === 1 ? POD_ALIEN : POD_HUMAN, 0);
      w16(gs, info, 1);
    } else {
      Anim.startAnim(G, a + O.ANIM0, t.stand, 2);
      w16(gs, info, 5);
    }
  }
  let s = i16(gs, info);
  if (s === 1) return 0;
  if (s === 2 || s === 5) {
    if (u8(gs, a + O.ANIM0_STATUS) !== 2) return 0;
    Anim.startAnim(G, a + O.ANIM0, t.stand, 0);
    w8(gs, pa + P.SLOT_BLOCKED + irem(obj, BUILDINGS_PER_SIDE), 0);
    if (team === i32(gs, GS.LOCAL_PLAYER)) depRecompute(G, team); // display only
    if (i16(gs, info) === 2) {
      spawnPod(G, team * BUILDINGS_PER_SIDE + 6, obj, i32(gs, pa + P.RACE) === 1 ? POD_ALIEN : POD_HUMAN, 1);
      w16(gs, info, 3);
    }
  }
  s = i16(gs, info);
  if (s === 3) return 0;
  if (s === 4 || s === 5) {
    w8(gs, pa + P_CONSTRUCTING, 0);
    Ticker.resetAndDispatchOrder(G, obj);
  }
  return 0;
}

// ---- production -----------------------------------------------------------------------------------

function spawnTile(G, pa, k, ut) {
  const gs = G.gs;
  const idx = k * 3 + ut.flyer; // 0x41AFE0[class*3 + flyer]
  const sp = G.consts.spawnOffsets.values;
  return [i32(gs, pa + P.CITY_X) + sp[idx * 2], i32(gs, pa + P.CITY_Z) + sp[idx * 2 + 1]];
}

/** memmove(queue, queue+1, len) with len = current length (one byte more than needed), then len--. */
function dropQueueHead(gs, qA, qLenA) {
  const len = u16(gs, qLenA);
  gs.copyWithin(qA, qA + 1, qA + 1 + len); // 0x43B0D6
  w16(gs, qLenA, i16(gs, qLenA) - 1);
}

/**
 * 0x41460C(gs, obj, &object): every-tick handler of a standing building (called by the idle state
 * 0x414B9C for objects 0..119 that are unarmed and immobile). Damage sprite, then production of the
 * class the slot feeds. Returns 0 (the idle state returns it).
 */
export function stateBuildingIdle(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  const team = u8(gs, a + O.TEAM);
  const pa = playerAddr(team);
  const slot = obj - team * BUILDINGS_PER_SIDE;
  const k = slotToClass(G, slot);
  const t = G.tables.types[u8(gs, a + O.TYPE)];
  const full = t.hp;
  if (u8(gs, a + O.ANIM0_STATUS) !== 1) {
    // damage sprite (display only, but the animation slot bytes are state)
    let sprite;
    const hp = i32(gs, a + O.HP);
    if (hp > (full * 11) >> 4) sprite = t.stand;
    else if ((full * 5) >> 4 >= hp) sprite = t.scrch; // +0x84
    else sprite = t.burn; // +0x88
    Anim.startAnim(G, a + O.ANIM0, sprite, 0);
  }
  if (k === 4) return 0;
  const cnt = u8(gs, pa + P.PROD_COUNT + k);
  if (cnt !== 0) w8(gs, pa + P.PROD_COUNT + k, cnt - 1);
  const qLenA = pa + P.QUEUE_LEN + 2 * k;
  const qA = pa + P.QUEUE + 800 * k;
  if (u8(gs, pa + P.PROD_READY + k) === 1) {
    if (i16(gs, qLenA) === 0) return 0;
    const utype = u8(gs, qA);
    const ut = G.tables.types[utype];
    const [sx, sz] = spawnTile(G, pa, k, ut);
    G.assert(
      sx >= 0 && sz >= 0 && sx < G.map.w && sz < G.map.h,
      'xloc>=0 && zloc>=0 && xloc<gs->map->xsize && zloc<gs->map->ysize',
    );
    const hidden = ut.fly !== 0; // +0x60: flyers use the air grid
    const cell = (hidden ? Grid.cellAir(G, sx, sz) : Grid.cellGround(G, sx, sz)) & 0x3ff;
    if (cell !== EMPTY) {
      // ask the occupant to step aside and wait. A RESERVED (0x3FE) cell is treated as object 1022:
      // the write lands beyond the object array (inside gs, missile area), as in the original.
      w8(gs, objAddr(cell) + O.NUDGE, 0);
      return 0;
    }
    if (u8(gs, pa + P.PROD_COUNT + k) !== 0) return 0;
    if (G.stat(6, team) >= i32(gs, GS.UNIT_CAP)) {
      // unit cap: refund and drop the queue head, message 0x77 for the player
      w32(gs, pa + P.MONEY, i32(gs, pa + P.MONEY) + troopCost(G, utype));
      w32(gs, pa + P.SPENT, i32(gs, pa + P.SPENT) - troopCost(G, utype));
      dropQueueHead(gs, qA, qLenA);
      w16(gs, pa + P.WORD_1948, 0x77);
      return 0;
    }
    if (ut.build) {
      // reserve the spawn tile and play the unit's BUILD animation in the building's third slot
      if (hidden) Grid.setCellAir(G, sx, sz, (Grid.cellAir(G, sx, sz) & 0xfc00) | RESERVED);
      else Grid.setCellGround(G, sx, sz, (Grid.cellGround(G, sx, sz) & ~0x3ff) | RESERVED);
      w8(gs, pa + P.PROD_READY + k, 0);
      Anim.startAnim(G, a + O.ANIM2, ut.build, 1);
      return 0;
    }
    Scenario.createObject(G, sx, sz, utype, team, -1);
    dropQueueHead(gs, qA, qLenA);
    return 0;
  }
  // ready == 0: wait for the BUILD animation, then create the unit
  if (u8(gs, a + O.ANIM2_STATUS) === 1) return 0;
  const utype = u8(gs, qA);
  const [sx, sz] = spawnTile(G, pa, k, G.tables.types[utype]);
  Scenario.createObject(G, sx, sz, utype, team, -1);
  dropQueueHead(gs, qA, qLenA);
  w8(gs, pa + P.PROD_READY + k, 1);
  return 0;
}

// ---- pickups --------------------------------------------------------------------------------------

/**
 * 0x4143D4(gs, obj): every 4th day/night counter value, scan the 5x5 tiles around `obj` (ground then
 * air layer) for objects with +0xCB == 0. +0xCB == 1: a team-0 finder captures the object (team 0,
 * kind cleared, campaign counter). +0xCB == 2: the finder's owner (0..7) gains `hp` money and the
 * object dies. Called from the idle state 0x414B9C for objects with +0xCB set.
 */
export function pickupsTick(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  if ((u8(gs, GS.DN_COUNTER) & 3) !== 0) return;
  // `xor eax,eax; mov ax,[x]; sar eax,8`: unsigned 16-bit position shifted
  const ox = u16(gs, a + O.X) >> 8;
  const oz = u16(gs, a + O.Z) >> 8;
  const { w, h } = G.map;
  for (let x = ox - 2; x <= ox + 2; x++) {
    if (x < 0 || x >= w) continue;
    for (let z = oz - 2; z <= oz + 2; z++) {
      if (z < 0 || z >= h) continue;
      for (let layer = 0; layer < 2; layer++) {
        const cell = (layer === 0 ? Grid.cellGround(G, x, z) : Grid.cellAir(G, x, z) & 0xffff) & 0x3ff;
        if (cell === EMPTY || cell === RESERVED) continue;
        const oa = objAddr(cell);
        if (u8(gs, oa + O.PICKUP) !== 0) continue;
        if (u8(gs, a + O.PICKUP) === 1 && u8(gs, oa + O.TEAM) === 0) {
          w8(gs, a + O.PICKUP, 0);
          w8(gs, a + O.TEAM, 0);
          // 0x43FE7C(0): campaign counter 0x51D2AC[0]++
          if (!G.globals.captures) G.globals.captures = [0, 0, 0, 0, 0, 0, 0, 0];
          G.globals.captures[0]++;
          // 0x431DB8(3, 7, 0, 0): message / sound, display only
          return;
        }
        if (u8(gs, a + O.PICKUP) === 2) {
          const team = u8(gs, oa + O.TEAM);
          if (team < 8) {
            const ma = playerAddr(team) + P.MONEY;
            w32(gs, ma, i32(gs, ma) + i32(gs, a + O.HP));
            // 0x431DB8(3, 7, 0, 0): display only
            w8(gs, a + O.PICKUP, 0);
            Combat.objectDie(G, obj); // 0x4165A0
            Grid.removeObject(G, obj); // 0x434EB8
          }
        }
      }
    }
  }
}

/**
 * 0x44558C remove_footprint(gs, obj): clear the footprint cells of building object `obj`
 * (player = obj / 15, slot = obj % 15) from the ground layer and reset the slot's production
 * class. The footprint table 0x48AD04 holds 8 {dx, dz} pairs per slot (16 int32); the walk stops at
 * the first pair equal to the previous one or after 8 cells. Every cell must hold `obj`
 * (assert city.c:248, `load & 0x3FF == obj`), the OR with 0x3FF happens either way.
 */
export function removeFootprint(G, obj) {
  const gs = G.gs;
  const map = G.map;
  const player = idiv(obj, 15);
  const slot = irem(obj, 15);
  const fp = G.consts.footprints.values; // 0x48AD04, 96 int32
  const pa = playerAddr(player);
  let px = -100;
  let pz = -100;
  for (let k = 0; k < 8; k++) {
    const x = fp[slot * 16 + k * 2] + i32(gs, pa + P.CITY_X);
    const z = fp[slot * 16 + k * 2 + 1] + i32(gs, pa + P.CITY_Z);
    if (x === px && z === pz) break;
    const i = z * map.w + x;
    G.assert((map.ground[i] & 0x3ff) === obj, 'remove_footprint: cell does not hold the building (city.c:248)');
    map.ground[i] |= 0x3ff;
    px = x;
    pz = z;
  }
  const cls = slotToClass(G, slot); // 0x41B07C
  if (cls !== 4) {
    w16(gs, pa + P.QUEUE_LEN + 2 * cls, 0);
    w8(gs, pa + P.PROD_COUNT + cls, 0);
    w8(gs, pa + P.PROD_READY + cls, 1);
  }
}

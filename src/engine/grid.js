// grid.js — the map as the simulation sees it: the packed `load` word per cell (mapit.c 0x4530C0),
// the three object layers (ground int32 / air int16 / secondary int16), the terrain-class tally,
// layer clearing (0x445740), the seen-by bits (clearSeen 0x4458C0, updateVision 0x44A718 with the
// sixteen writer variants 0x445BF4..0x44A23C) and the collide.c grid helpers (0x434EB8).
//
// PORT NOTES (11 Sep 2026, from dc16.asm; all offsets are Classic dc16.exe VAs)
//
// * Cells are indexed `z * w + x` in z order for every grid. The original keeps TWO row-pointer
//   tables for `load`: `map+4[r]` in FILE order (row r = z h-1-r) and `map+0x404[z]` in z order;
//   the object layers (`map+0x804/0xC04/0x1004`) are z-ordered. Code that indexes `map+4` with h-1-z
//   (the vision writers, city.c) therefore reads the z row; code that indexes `map+4` directly with z
//   reads the MIRRORED row — the only such users are the vent bit (bit 26): set at 0x41C758, tested
//   at 0x413B87 and 0x43D15C, all three with `map+4[z]`. `ventBitSet/ventBitTest` reproduce that
//   (they touch `load[(h-1-z)*w + x]`); everything else uses z-ordered `load`. The JSON of
//   tools/map2json.js is already z-ordered (`tiles.*[z][x]`, `triggerIds[z][x]`).
// * `load` word (0x45337E..0x4534FA): bits 0..10 background slot, 11..21 foreground slot, 22..31 =
//   attribute bits 0..9; attribute bit 7 (load bit 29) forced when bit 9 (load bit 31) is clear;
//   attribute bits 0..3 (load 22..25) cleared when there is no foreground tile. The slots are the
//   tile's POSITION in the .BTS bank (`remap[editor index]`, 0x452F48), which the JSON does not carry:
//   this port assigns dense slots in ascending editor-index order (0 stays 0). Nothing in the
//   simulation reads a slot value except through `terrainClass[slot]`, which is a per-tile property,
//   so every observable result is identical; `mapJson.tileRemap` (editor index -> slot) is used when
//   a future map2json provides it. TODO(exact): carry the .BTS remap in the JSON.
// * Terrain class tally (0x4533A8..0x45356C): word counts per slot x class (class = attribute >> 10,
//   sign-extended 16-bit attribute), incremented for the background AND the foreground tile of every
//   cell (the doc says "per background tile" — the disassembly tallies both, 0x4534D6..0x4534E5);
//   terrainClass[slot] = the first class with the strictly greatest count (0 when all are 0).
// * Ground word: low 10 bits object id, 0x3FF empty, 0x3FE "look in the secondary layer"; bits
//   23..30 seen by player q (`0x40000000 >> q`); bit 31 "explored / seen by the local player" (set by
//   the vision writers when the local player's vision mask covers the object's team, by the reveal
//   cheat 0x4457D0 and for the attacker's cell) — display only, it depends on gs->local_player and
//   so cannot influence the lockstep state; kept because it costs nothing. Bits 10..17 are cleared
//   by the writer variants with the local-sees flag (display fog memory, same remark).
// * Vision (0x44A718): every 16 ticks; per object with life != 0, team < 8, pickup byte not 1/2:
//   radius = (darkness*ObsN + (256-darkness)*ObsD) >> 8 (0x44A7FD..0x44A81E); then one of sixteen
//   compiler-specialised writers selected by flags 1 = local player sees this team, 2 = detector type
//   (object_types+0x6C, commando/engineer), 4 = disc fully inside the map (only removes bounds
//   checks), 8 = flying type (+0x60). The writer is a depth-first walk over a PRECOMPUTED ray tree per
//   radius (`0x4990E4[radius]`, radii 1..10 exist; 11/12 pass the range check but point into other
//   data — an original bug, they never occur with the shipped tables): node = {dx, dz,
//   (children-1)*4, child[8]}; a cell gets the team bit (and, for a cell with attribute bit 8 = load
//   bit 30, only at depth < 2 — deeper such cells get just the explored bit); descent continues
//   through a cell only when it is walkable (load bit 29) or the object flies; detector types also
//   mark the mine in the secondary layer of every visited cell as detected by their team. The trees
//   were extracted to data/dc16-vision.json (1206 nodes). Corpses (life 10) shrink the radius by
//   (150 - info0) / 150 (0x44A502..0x44A53E). The depth table 0x48B1B4 is child depth = parent + 1.
// * clearSeen (0x4458C0): AND every ground word with ~0x7F800000 (unrolled x32); then the reveal
//   cheats: flags[0] -> all team bits + bit 31, else flags[3] -> local bit + bit 31 (0x4457D0).
// * removeFromGrid (0x434EB8): objects < 120 go to city.c 0x44558C (footprint removal, city.js);
//   others: 3x3 search around the tile for the id in ground, then air, then secondary; `or word 0x3FF`
//   frees the cell; not found -> assert "object not found" (0x485FE8). 0x431F60 (dirty rectangle)
//   is display only.
// * Placement (initObject 0x41B5F8.., build_slot): `word &= 0xFC00; dword |= obj` on the ground
//   word (= keep bits 10.. of the low word and all high bits), `word = (word & 0xFC00) | obj` on
//   air/secondary — placeGround/placeAir/placeSec.
// * Attribute helpers 0x448E9C, 0x4492E9, 0x44A1BF, 0x44A65C are not separate functions: they are
//   the `test byte [load+3],20h` (attribute bit 7) inside four of the vision variants. Exposed here
//   as isWalkable/isBlocking/attrBits.
// * Disagreements with the docs: DC16_MAP_FILES.md §3.2 "tallies the class per background tile"
//   (both tiles); DC16_BATTLE_ENGINE.md §3.1 `gs+0x000` "network flag" — the loader writes it as
//   the `atlantis.bts` flag (`gs->underground`, 0x41BC84/0x41BD07); mem.js P.SLOT_BLOCKED int32[15] at
//   +0x7C — the scenario loader clears BYTES +0x7C..+0x8A and int32s +0x8C..+0xC7 (0x41C3C7..0x41C3D6).

import { createRequire } from 'node:module';
import { GS, O, P, u8, u16, i32, w8, objAddr, idiv, sx16 } from './mem.js';
import * as Path from './path.js';
import * as City from './city.js';

const VISION = createRequire(import.meta.url)('../../data/dc16-vision.json');

export const MAXTILE = 1400; // 0x578
export const ID_MASK = 0x3ff;
export const EMPTY = 0x3ff;
export const SECONDARY_MARKER = 0x3fe;
export const SEEN_MASK = 0x7f800000; // bits 23..30
export const EXPLORED = 0x80000000 | 0; // bit 31
export const LOAD_WALKABLE = 0x20000000; // attribute bit 7
export const LOAD_BLOCKING = 0x80000000 | 0; // attribute bit 9
export const LOAD_ATTR8 = 0x40000000; // attribute bit 8 (vision: seen only at depth < 2)
export const LOAD_VENT = 0x04000000; // attribute bit 4, set at run time for vents
export const TERRAIN_CLASSES = 32;

/** `0x40000000 >> q`: the seen-by / vision-mask bit of player q. */
export const teamBit = (q) => 0x40000000 >> q;

// ---- loading (mapit.c 0x4530C0, reached through 0x436080 from the scenario loader) --------------

/**
 * Build G.map from the map JSON (tools/map2json.js) exactly as mapit.c does from .MAP/.MTG and
 * call path.js for the .PTH part (0x442D8C, called at 0x45375F when the path grid is requested).
 */
export function loadMap(G, mapJson) {
  const w = mapJson.width | 0;
  const h = mapJson.height | 0;
  // 0x45312C: assert mapinfo->xsize>0 && xsize<=256 && ysize>0 && ysize<=256
  G.assert(w > 0 && w <= 256 && h > 0 && h <= 256, 'mapinfo->xsize>0 && mapinfo->xsize<=256 && mapinfo->ysize>0 && mapinfo->ysize<=256');
  const n = w * h;
  const bg = mapJson.tiles.background;
  const fg = mapJson.tiles.foreground;
  const attrs = mapJson.tiles.attributes;

  // .BTS remap (0x452F48): editor index -> bank position. Dense stand-in when the JSON has none.
  const remap = mapJson.tileRemap ? new Map(Object.entries(mapJson.tileRemap).map(([k, v]) => [+k, v])) : denseRemap(bg, fg, w, h);

  const load = new Int32Array(n);
  // pass 1 (0x4531D1..0x4533A3): file rows r = 0..h-1 are z = h-1-r; fg << 11 | bg
  for (let r = 0; r < h; r++) {
    const z = h - 1 - r;
    for (let x = 0; x < w; x++) {
      const b = remap.get(bg[z][x]);
      const f = remap.get(fg[z][x]);
      G.assert(b >= 0 && b < MAXTILE, 'backgroundl>=0 && backgroundl<MAXTILE');
      G.assert(f >= 0 && f < MAXTILE, 'forgroundl>=0 && forgroundl<MAXTILE');
      load[z * w + x] = ((f << 11) | b) | 0;
    }
  }
  // pass 2 (0x4533A8..0x453515): attribute word into bits 22..31, forced bit 29, class tally
  const tally = new Uint16Array(MAXTILE * TERRAIN_CLASSES); // "temppool", 0x40 bytes per tile
  for (let r = 0; r < h; r++) {
    const z = h - 1 - r;
    for (let x = 0; x < w; x++) {
      const i = z * w + x;
      let v = load[i];
      const bSlot = v & 0x7ff;
      const fSlot = (v >>> 11) & 0x7ff;
      const attr = sx16(attrs[z][x]); // read as a signed 16-bit word (0x45343C sar eax,10h)
      v = (v | (attr << 22)) | 0;
      if (!(v & LOAD_BLOCKING)) v |= LOAD_WALKABLE; // 0x453451..0x45345C
      G.assert(bSlot < MAXTILE, 'backgroundl<MAXTILE');
      const cls = attr >> 10; // 0x4534CA sar ecx,0Ah
      if (cls < 0 || cls >= TERRAIN_CLASSES) throw new Error(`terrain class ${cls} out of the 32-entry tally at (${x},${z})`);
      tally[bSlot * TERRAIN_CLASSES + cls]++; // inc word ptr (wraps at 65536 like the original)
      if (fSlot !== 0) tally[fSlot * TERRAIN_CLASSES + cls]++;
      if (((v >>> 11) & 0x7ff) === 0) v &= ~0x03c00000; // 0x4534FA: and word ptr [load+2],0FC3Fh
      load[i] = v | 0;
    }
  }
  // 0x45351A..0x453574: most frequent class per slot (first strictly greater, else 0)
  const terrainClass = new Uint8Array(MAXTILE);
  for (let t = 0; t < MAXTILE; t++) {
    let max = 0;
    let idx = 0;
    for (let c = 0; c < TERRAIN_CLASSES; c++) {
      const v = tally[t * TERRAIN_CLASSES + c];
      if (v > max) {
        max = v;
        idx = c;
      }
    }
    terrainClass[t] = idx;
  }

  // .MTG (0x45358F..0x453733): trigger byte << 10 into the air word of row h-1-y; assert (b & 0xC0) == 0
  const air = new Int16Array(n);
  const trig = mapJson.triggerIds;
  if (trig) {
    for (let r = 0; r < h; r++) {
      const z = h - 1 - r;
      for (let x = 0; x < w; x++) {
        const t = trig[z][x] | 0;
        G.assert((t & 0xc0) === 0, '(trigger&0xc0)==0');
        air[z * w + x] = (t << 10) & 0xffff;
      }
    }
  }
  const ground = new Int32Array(n); // contents come from 0x445740 (clearLayers)
  const sec = new Int16Array(n);

  const families = new Uint8Array(n);
  const fam = mapJson.path?.families;
  if (fam) for (let z = 0; z < h; z++) for (let x = 0; x < w; x++) families[z * w + x] = fam[z][x];
  const next = mapJson.path?.next ? new Uint8Array(Buffer.from(mapJson.path.next, 'base64')) : new Uint8Array(65536);

  const map = { w, h, load, ground, air, sec, terrainClass, families, next, path: null, remap };
  G.map = map;
  w8(G.gs, GS.MAP_PTR, 1); // the original stores the map pointer here; non-zero marks "map present"
  Path.initPathGrid(G, mapJson); // 0x442D8C, called from 0x45375F
  return map;
}

/** Dense stand-in for the .BTS `remap[editor index]`: 0 -> 0, other ids in ascending order. */
function denseRemap(bg, fg, w, h) {
  const ids = new Set();
  for (let z = 0; z < h; z++) {
    for (let x = 0; x < w; x++) {
      ids.add(bg[z][x]);
      ids.add(fg[z][x]);
    }
  }
  ids.delete(0);
  const sorted = [...ids].sort((a, b) => a - b);
  const remap = new Map([[0, 0]]);
  sorted.forEach((id, k) => remap.set(id, k + 1));
  return remap;
}

// ---- layer clear 0x445740 ---------------------------------------------------------------------

/** 0x445740: ground words = 0x3FF, air |= 0x3FF (keeps the trigger bits), secondary |= 0x3FF. */
export function clearLayers(G) {
  const { ground, air, sec } = G.map;
  ground.fill(EMPTY);
  for (let i = 0; i < air.length; i++) air[i] |= EMPTY;
  for (let i = 0; i < sec.length; i++) sec[i] |= EMPTY;
}

// ---- accessors ---------------------------------------------------------------------------------

export const cellIndex = (G, x, z) => z * G.map.w + x;
export const cellLoad = (G, x, z) => G.map.load[z * G.map.w + x];
export const cellGround = (G, x, z) => G.map.ground[z * G.map.w + x];
export const cellAir = (G, x, z) => G.map.air[z * G.map.w + x];
export const cellSec = (G, x, z) => G.map.sec[z * G.map.w + x];
/** Object id in the ground layer (0x3FF empty, 0x3FE = look in the secondary layer). */
export const groundId = (G, x, z) => G.map.ground[z * G.map.w + x] & ID_MASK;
export const airId = (G, x, z) => G.map.air[z * G.map.w + x] & ID_MASK;
export const secId = (G, x, z) => G.map.sec[z * G.map.w + x] & ID_MASK;
/** Trigger id of the cell (the .MTG byte, air word bits 10..15). */
export const triggerId = (G, x, z) => (G.map.air[z * G.map.w + x] & 0xffff) >>> 10;
/** Attribute bits 0..9 of the cell (load bits 22..31). */
export const attrBits = (G, x, z) => G.map.load[z * G.map.w + x] >>> 22;
/** Attribute bit 7 (load bit 29): the `test byte ptr [load+3],20h` of 0x448E9C and friends. */
export const isWalkable = (G, x, z) => (G.map.load[z * G.map.w + x] & LOAD_WALKABLE) !== 0;
/** Attribute bit 9 (load bit 31): blocking terrain. */
export const isBlocking = (G, x, z) => (G.map.load[z * G.map.w + x] & LOAD_BLOCKING) !== 0;
export const backgroundSlot = (G, x, z) => G.map.load[z * G.map.w + x] & 0x7ff;
export const foregroundSlot = (G, x, z) => (G.map.load[z * G.map.w + x] >>> 11) & 0x7ff;
/** city.c 0x445B7B: the terrain class of the foreground tile, or of the background when there is none. */
export function terrainClassAt(G, x, z) {
  const f = foregroundSlot(G, x, z);
  return G.map.terrainClass[f !== 0 ? f : backgroundSlot(G, x, z)];
}
/** Seen-by bit of player q on the cell (bits 23..30 of the ground word). */
export const seenBy = (G, x, z, q) => (G.map.ground[z * G.map.w + x] & (0x40000000 >> q)) !== 0;

/**
 * Vent bit (attribute 4 = load bit 26) as the original addresses it: `map+4[z]` is the FILE-order
 * row table, so the bit lands in the z-ordered row h-1-z (0x41C747..0x41C758, 0x413B87, 0x43D15C).
 */
export function ventBitSet(G, x, z) {
  const m = G.map;
  m.load[(m.h - 1 - z) * m.w + x] |= LOAD_VENT;
}
export function ventBitTest(G, x, z) {
  const m = G.map;
  return (m.load[(m.h - 1 - z) * m.w + x] & LOAD_VENT) !== 0;
}

/** Ground placement as 0x41B633/0x41B649: `and word ptr,0FC00h; or dword ptr,obj`. */
export function placeGround(G, x, z, obj) {
  const m = G.map;
  const i = z * m.w + x;
  m.ground[i] = ((m.ground[i] & ~0x3ff) | obj) | 0;
}
/** Air placement (0x41B65B/0x41B673): `word = (word & 0xFC00) | obj`. */
export function placeAir(G, x, z, obj) {
  const m = G.map;
  const i = z * m.w + x;
  m.air[i] = (m.air[i] & 0xfc00) | obj;
}
/** Secondary-layer placement (0x41B605/0x41B673). */
export function placeSec(G, x, z, obj) {
  const m = G.map;
  const i = z * m.w + x;
  m.sec[i] = (m.sec[i] & 0xfc00) | obj;
}

// ---- collide.c 0x434EB8: remove an object from the grids ---------------------------------------

/** 0x434EB8(gs, obj). */
export function removeFromGrid(G, obj) {
  if (obj < 0x78) {
    City.removeFootprint(G, obj); // 0x44558C, city.c: a building's footprint. TODO(owner city.js)
    return;
  }
  const gs = G.gs;
  const a = objAddr(obj);
  const tx = u16(gs, a + O.X) >> 8; // xor edx,edx; mov dx,[obj]; sar ecx,8
  const tz = u16(gs, a + O.Z) >> 8;
  // 0x434F23: 0x431F60(gs, type, x, z, 1, 3) — dirty rectangle, display only
  const m = G.map;
  for (let rx = tx - 1; rx <= tx + 1; rx++) {
    if (rx < 0 || rx >= m.w) continue;
    for (let rz = tz - 1; rz <= tz + 1; rz++) {
      if (rz < 0 || rz >= m.h) continue;
      const i = rz * m.w + rx;
      if ((m.ground[i] & ID_MASK) === obj) {
        m.ground[i] |= EMPTY; // or word ptr [cell],3FFh
        return;
      }
      if ((m.air[i] & ID_MASK) === obj) {
        m.air[i] |= EMPTY;
        return;
      }
      if ((m.sec[i] & ID_MASK) === obj) {
        m.sec[i] |= EMPTY;
        return;
      }
    }
  }
  G.assert(false, 'object not found in the grids (collide.c 0x43502B)');
}

// ---- seen-by bits: clearSeen 0x4458C0, reveal 0x4457D0, updateVision 0x44A718 -----------------

/** 0x4458C0(gs): clear bits 23..30 of every ground word, then the reveal cheats. */
export function clearSeen(G) {
  const ground = G.map.ground;
  const mask = ~SEEN_MASK; // not (sum of 0x40000000 >> q, q = 0..7)
  for (let i = 0; i < ground.length; i++) ground[i] &= mask;
  const gs = G.gs;
  if (u8(gs, GS.FLAGS) !== 0) revealAll(G, 1); // flags[0]
  else if (u8(gs, GS.FLAGS + 3) !== 0) revealAll(G, 0); // flags[3]
}

/** 0x4457D0(gs, all): OR bit 31 plus every team bit (all) or the local player's bit into all cells. */
export function revealAll(G, all) {
  let bits = EXPLORED;
  if (all) {
    for (let q = 0; q < 8; q++) bits |= 0x40000000 >> q;
  } else {
    bits |= 0x40000000 >> i32(G.gs, GS.LOCAL_PLAYER);
  }
  const ground = G.map.ground;
  for (let i = 0; i < ground.length; i++) ground[i] |= bits;
}

/** 0x44A718(gs): per-object vision into the ground layer. */
export function updateVision(G) {
  const gs = G.gs;
  const maxObj = i32(gs, GS.MAX_OBJ);
  // 0x44A75B: detected-by masks of every living object reset (loop is `obj <= max_obj`)
  for (let obj = 0; obj <= maxObj; obj++) {
    const a = objAddr(obj);
    if (u8(gs, a + O.LIFE) !== 0) w8(gs, a + O.DETECTED, 0);
  }
  const dark = i32(gs, GS.DN_LIGHT);
  const light = 0x100 - dark;
  const localVision = i32(gs, G.player(i32(gs, GS.LOCAL_PLAYER)) + P.VISION); // 0x44A8C7
  for (let obj = 0; obj <= maxObj; obj++) {
    const a = objAddr(obj);
    const pickup = u8(gs, a + O.PICKUP);
    if (pickup === 1 || pickup === 2) continue;
    const team = u8(gs, a + O.TEAM);
    if (team > 7) continue;
    if (u8(gs, a + O.LIFE) === 0) continue;
    const t = G.tables.types[u8(gs, a + O.TYPE)];
    const radius = (dark * t.visionNight + light * t.visionDay) >> 8; // 0x44A7FD..0x44A81E (+0x10, +0x14)
    const flyer = t.fly !== 0; // +0x60 -> flag 8
    const detector = t.commando !== 0; // +0x6C -> flag 2
    const localSees = (localVision & (0x40000000 >> team)) !== 0; // flag 1 (display only)
    // flag 4 ("disc inside the map": radius < tx, tz > radius, tx < w-radius, tz < h-radius) only
    // selects a variant without bounds checks; the result is identical, so it is not reproduced.
    visionWrite(G, obj, radius, flyer, detector, localSees);
  }
  // 0x44ABA7: 0x454B34(gs) — minimap refresh from the local player's mask, display only
}

/**
 * The vision writer (0x44A23C and its fifteen variants): DFS over the ray tree of `radius`.
 * Arguments as the original passes them: (gs, obj, radius [ebx], w, h, flyer, detector, localSees).
 */
export function visionWrite(G, obj, radius, flyer, detector, localSees) {
  const gs = G.gs;
  const m = G.map;
  const w = m.w;
  const h = m.h;
  const a = objAddr(obj);
  const team = u8(gs, a + O.TEAM);
  let teamBits = 0x40000000 >> team; // [ebp-14h]
  let exploredBits = 0; // [ebp-18h]
  const tx = u16(gs, a + O.X) >> 8; // 0x44A459: xor edx,edx; mov dx,[obj]; sar edx,8
  const tz = u16(gs, a + O.Z) >> 8;
  // 0x44A46E: an object under attack reveals its own cell to the attacker's team
  const attacked = u8(gs, a + O.ATTACKED);
  if (attacked & 0x1f) {
    const attacker = attacked >> 5;
    let bit = 0x40000000 >> attacker;
    if (attacker === i32(gs, GS.LOCAL_PLAYER)) bit |= EXPLORED; // display only
    m.ground[tz * w + tx] |= bit;
  }
  if (localSees) {
    teamBits |= EXPLORED;
    exploredBits |= EXPLORED;
  }
  if (radius < 1 || radius > 12) return; // 0x44A4D0..0x44A4DC
  if (u8(gs, a + O.LIFE) === 10) {
    // corpse: radius = (150 - info[0]) * radius / 150, at least 1 (0x44A502..0x44A59A)
    radius = idiv((0x96 - u16(gs, a + O.INFO)) * radius, 0x96);
    if (radius === 0) radius = 1;
    else G.assert(radius <= 10, 'radius<=10 (vision.c 0xCC)');
  }
  const tree = VISION.trees[radius];
  if (!tree) throw new Error(`vision radius ${radius}: 0x4990E4 has trees for 1..10 only (original reads garbage)`);
  // the two parallel stacks: node pointer ([ebp-4]) and depth ([ebp-0Ch]); entry 0 is the null sentinel
  const sn = G._visNodes ?? (G._visNodes = new Int32Array(512));
  const sd = G._visDepth ?? (G._visDepth = new Int32Array(512));
  sn[0] = -1;
  sn[1] = 0;
  sd[1] = 0;
  let top = 1;
  const ground = m.ground;
  const load = m.load;
  const sec = m.sec;
  for (;;) {
    const ni = sn[top];
    if (ni < 0) break; // 0x44A5D4: null pointer ends the walk
    const node = tree[ni];
    const depth = sd[top];
    const x = tx + node[0];
    const z = tz + node[1];
    if (x < 0 || x >= w || z < 0 || z >= h) {
      top--; // 0x44A6B8
      continue;
    }
    const i = z * w + x;
    const lv = load[i];
    let g = ground[i];
    if (localSees) g &= 0xfffc03ff; // variant 1 (0x449D94..): clears bits 10..17 first
    if (lv & LOAD_ATTR8) g |= depth < 2 ? teamBits : exploredBits; // 0x44A641..0x44A653
    else g |= teamBits;
    ground[i] = g | 0;
    if (detector) {
      // variant 2 (0x449878..): a mine in the secondary layer becomes detected by this team
      const id = sec[i] & ID_MASK;
      if (id !== EMPTY && id !== SECONDARY_MARKER) {
        const da = objAddr(id) + O.DETECTED;
        w8(gs, da, u8(gs, da) | (1 << team));
      }
    }
    if (flyer || (lv & LOAD_WALKABLE)) {
      // push the children over the current entry; depth table 0x48B1B4[depth][k] = depth + 1
      const ch = node[2];
      for (let k = 0; k < 8; k++) {
        sn[top + k] = k < ch.length ? ch[k] : -1;
        sd[top + k] = depth + 1;
      }
      top += ch.length - 1; // (children-1)*4 bytes; a leaf pops
    } else {
      top--; // 0x44A6A1: blocked cell, children not entered
    }
  }
}

// ---- helpers for other modules -----------------------------------------------------------------

/** Tile coordinates of an object as the vision/collision code derives them (unsigned 16-bit >> 8). */
export function objTile(G, obj) {
  const a = objAddr(obj);
  return [u16(G.gs, a + O.X) >> 8, u16(G.gs, a + O.Z) >> 8];
}

/** Write helpers kept together so that every layer store goes through one place. */
export function setGround(G, x, z, v) {
  G.map.ground[z * G.map.w + x] = v | 0;
}
export function setAir(G, x, z, v) {
  G.map.air[z * G.map.w + x] = v;
}
export function setSec(G, x, z, v) {
  G.map.sec[z * G.map.w + x] = v;
}
/** Free a cell's id bits (`or word ptr,3FFh`) in the given layer: 'ground' | 'air' | 'sec'. */
export function freeCell(G, layer, x, z) {
  const m = G.map;
  const i = z * m.w + x;
  if (layer === 'ground') m.ground[i] |= EMPTY;
  else if (layer === 'air') m.air[i] |= EMPTY;
  else m.sec[i] |= EMPTY;
}

// ---- names other modules use for the functions above (integration, 11 Sep 2026) -----------------

export const setCellGround = setGround;
export const setCellAir = setAir;
export const setSecondary = setSec;
export const cellSecondary = cellSec;
export const removeObject = removeFromGrid;

/** Path cell +0xC (family) != 0: passable terrain for ground units (path grid of path.js). */
export function passable(G, x, z) {
  return G.map.path.familyAt(x, z) !== 0;
}

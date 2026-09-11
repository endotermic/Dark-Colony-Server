// path.c of Classic dc16.exe: the path grid (map+0x1404), the family routing matrix and the
// Dial-bucket search that begin_move (move.js) and the re-route use.
//
// PORT NOTES (11 Sep 2026, from dc16.asm; every function is instruction-by-instruction unless said)
//
// Path struct layout (offsets from `path` = map+0x1404), reproduced as typed arrays of the same
// shape so that pointer arithmetic (cell ± 24 = x±1, cell ± 0xF30 = z±1) maps to index ± 1 / ± 162:
//   +0        row pointer table (0x238 bytes = 142 pointers, rows[-1..140], each pointing at column 1)
//   +4        142*162 cells of 24 bytes: +0 gen stamp, +4 cost, +8 x, +9 z, +0xA parent x,
//             +0xB parent z, +0xC family (0 impassable, 0xFF border), +0xD (always 0), +0x10 next,
//             +0x14 prev (bucket list links)
//   +0x86CA4  256 bucket heads (cost mod 256)
//   +0x870A4  routing matrix next[a][b] (65536 bytes)
//   +0x970A4  CLIST2: 256 x 32 bytes, [fam*32] = count, [fam*32+1..] = distinct next hops
//   +0x990A4  current bucket index, +0x990A8 cost of the current bucket ("head")
// Globals: 0x48AA9C generation stamp (G.globals.pathGen), 0x51D8BC allowed-family set
// (PathGrid.allowed), 0x48AAC8 obstacle flag, 0x48AACC obstacle-fly flag, 0x51D9E4/EC/E0/E8
// obstacle grids + width + height (PathGrid.obstacles/obstacleFly/obsGround/obsAir/obsW/obsH),
// 0x51D9BC[9] the per-relax neighbour flags (module scratch), 0x48AAC4 nibble masks {F0,0F}.
//
// Ported: 0x442C20 resetBuckets, 0x442C60 initPathGridFromTiles (no caller in dc16.exe), 0x442D8C
// initPathGrid incl. the CLIST2 derivation 0x442F28, 0x443068 checkChains (bucket list sanity
// walk, "Whoa, the last pointer in the chain is corrupt"), 0x443130 stepCost, 0x443238 pathLength,
// 0x4432BC extractPath, 0x443458 importObstacles, 0x443490 clearObstacles, 0x4434A0 relax,
// 0x4445CC expandOne, 0x44467C findPathAllowed (search with a caller-supplied allowed set),
// 0x444748 rerouteSearch, 0x444824 bumpGeneration, 0x444830 flyPath, 0x444B34 findPath.
//
// Disagreements with docs/DC16_BATTLE_ENGINE.md §14.3 (the code wins):
//  * "+0xA parent z, +0xB parent x" - it is +0xA parent x, +0xB parent z (0x443277/0x44328F,
//    0x4448BD/0x4448CE, seed writes 0x444D62/0x444D7A).
//  * "Dijkstra ... Step costs are 5 orthogonal and 7 diagonal (0x443130). No heuristic": the search
//    itself adds table 0x48AAD0[dirToStart][neighbour] (1..90) to the popped cost, i.e. a
//    direction-biased greedy cost; 0x443130 is a separate path-cost walk that adds 5 for a z step,
//    +2 for an x step and +2 again when both change (vertical 5, horizontal 2, diagonal 9).
//  * The table 0x48AAD0 is int32[9][9] (81 values); data/dc16-tables.json `neighbourOrders`
//    holds only its first 64 values, so the full table is embedded below (NEIGHBOUR_ORDER).
//  * Ties: a neighbour whose tentative cost is EQUAL to its current cost is unlinked and
//    re-inserted at the head of its bucket (0x443A7D `jge`), which changes the pop order.
//  * Quirk kept: when the left neighbour is not allowed, the down-left neighbour is relaxed with the
//    UP weight (0x4442EB reads [ebp-74h] = tentative[1] instead of tentative[6]).
//  * Quirk kept: flyers with obstacles active lose their three "up" neighbours on row h-2
//    (0x443A0C).
//  * Diagonal neighbours need the diagonal cell allowed AND (when the orthogonal x-neighbour is not
//    allowed) the orthogonal z-neighbour allowed; the docs do not mention this.
//  * "There is no path": after the assert the original keeps walking the chain with family 0 (the
//    assert handler 0x47C02E does not stop the game in the port), so allowed[0] becomes 1; kept.
//  * The re-route search 0x444748 pops at most 256 nodes; not in the docs.
//  * extract_path increments the stamp of every cell it walks and the global stamp once at the end
//    (0x44330D / 0x443446); path_length does not check the stamp after the first cell.
//  * 0x442D8C initialises no cell fields of the border ring except the family byte; typed arrays
//    start zeroed here, which is never observable (border families are never allowed).
// Guard that the original does not have: pathLength/extractPath/stepCost throw when a parent
// pointer is not adjacent (the original would read past the dir3x3 table and loop); marked
// TODO(exact) - it cannot happen on well-formed search results.

import { u8, w8, idiv } from './mem.js';

export const ROW_CELLS = 162; // cells per row incl. the two border columns
export const NUM_ROWS = 142; // row pointers rows[-1..140]
export const CELLS = NUM_ROWS * ROW_CELLS;
export const NONE = -1; // NULL link

/** int32[9][9] at 0x48AAD0: tentative cost added for neighbour k (3x3 order) given the direction
 *  to the search target (3x3 index of sign(dx)+1 + 3*(sign(dz)+1)). */
export const NEIGHBOUR_ORDER = Int32Array.from([
  1, 10, 20, 10, 90, 50, 20, 50, 70,
  10, 1, 10, 20, 90, 20, 70, 50, 70,
  20, 10, 1, 50, 90, 10, 70, 50, 20,
  10, 20, 50, 1, 90, 70, 10, 20, 50,
  80, 80, 80, 80, 90, 80, 80, 80, 80,
  50, 20, 10, 70, 90, 1, 50, 20, 10,
  20, 50, 70, 10, 90, 50, 1, 10, 20,
  70, 50, 70, 20, 90, 20, 10, 1, 10,
  70, 50, 20, 50, 90, 10, 20, 10, 1,
]);
/** 0x48AAA0: direction of neighbour (dx+1)+(dz+1)*3, centre -1. */
export const DIR3X3 = Int32Array.from([0, 1, 2, 3, -1, 4, 5, 6, 7]);
/** 0x48AAC4: byte masks keeping the other nibble, index = step & 1. */
const NIBBLE_MASK = [0xf0, 0x0f];

const neighbourOrder = (G) => {
  const t = G?.consts?.neighbourOrders?.values;
  return t && t.length === 81 ? t : NEIGHBOUR_ORDER;
};

export class PathGrid {
  constructor(w, h) {
    this.w = w;
    this.h = h;
    this.gen = new Int32Array(CELLS); // +0
    this.cost = new Int32Array(CELLS); // +4
    this.cx = new Uint8Array(CELLS); // +8
    this.cz = new Uint8Array(CELLS); // +9
    this.px = new Uint8Array(CELLS); // +0xA
    this.pz = new Uint8Array(CELLS); // +0xB
    this.family = new Uint8Array(CELLS); // +0xC
    this.d = new Uint8Array(CELLS); // +0xD
    this.next = new Int32Array(CELLS).fill(NONE); // +0x10
    this.prev = new Int32Array(CELLS).fill(NONE); // +0x14
    this.buckets = new Int32Array(256).fill(NONE); // path+0x86CA4
    this.routing = new Uint8Array(65536); // path+0x870A4
    this.clist2 = new Uint8Array(256 * 32); // path+0x970A4
    this.cur = 0; // path+0x990A4
    this.head = 0; // path+0x990A8
    this.allowed = new Uint8Array(256); // 0x51D8BC
    this.obstacles = 0; // 0x48AAC8
    this.obstacleFly = 0; // 0x48AACC
    this.obsGround = null; // 0x51D9E4 (ground layer rows)
    this.obsAir = null; // 0x51D9EC (air layer rows)
    this.obsW = 0; // 0x51D9E0
    this.obsH = 0; // 0x51D9E8
  }

  /** Cell index of tile (x, z); the border ring is x = -1 / w and z = -1 / h. */
  idx(x, z) {
    return (z + 1) * ROW_CELLS + (x + 1);
  }

  familyAt(x, z) {
    return this.family[this.idx(x, z)];
  }
}

// ---- 0x442C20 ----------------------------------------------------------------------------------

/** reset the 256 bucket heads and the bucket cursor. */
export function resetBuckets(g) {
  g.buckets.fill(NONE);
  g.head = 0;
  g.cur = 0;
}

// ---- 0x442D8C ----------------------------------------------------------------------------------

function decodeRouting(next) {
  if (next instanceof Uint8Array) return next;
  if (typeof next === 'string') return new Uint8Array(Buffer.from(next, 'base64'));
  return Uint8Array.from(next);
}

/**
 * 0x442D8C(pool, base, &map->path, width, height): build the path grid from the .PTH data.
 * `mapJson` is the map2json document (path.families[z][x], path.next base64); when omitted the
 * families (Uint8Array z*w+x) and routing matrix come from G.map.families / G.map.next.
 * Returns the PathGrid and stores it in G.map.path when G.map exists.
 */
export function initPathGrid(G, mapJson = null) {
  const map = G.map;
  const w = mapJson?.width ?? map.w;
  const h = mapJson?.height ?? map.h;
  const g = new PathGrid(w, h);
  const next = mapJson?.path?.next ?? map.next;
  g.routing.set(decodeRouting(next).subarray(0, 65536));
  const fams = mapJson?.path?.families ?? null;
  const famAt = fams ? (x, z) => fams[z][x] : (x, z) => map.families[z * w + x];

  // border rows z = -1 and z = h, x = -1..w (0x442E21)
  for (let x = -1; x <= w; x++) {
    g.family[g.idx(x, -1)] = 0xff;
    g.family[g.idx(x, h)] = 0xff;
  }
  // border columns x = -1 and x = w, z = -1..h (0x442E51)
  for (let z = -1; z <= h; z++) {
    g.family[g.idx(-1, z)] = 0xff;
    g.family[g.idx(w, z)] = 0xff;
  }
  // cells (0x442E88): z outer, x inner, one family byte per cell (getc order of the file)
  for (let z = 0; z < h; z++) {
    for (let x = 0; x < w; x++) {
      const c = g.idx(x, z);
      g.gen[c] = 0;
      g.cost[c] = -1;
      g.next[c] = NONE;
      g.cx[c] = x;
      g.cz[c] = z;
      g.d[c] = 0;
      g.family[c] = famAt(x, z);
    }
  }
  buildClist2(G, g);
  resetBuckets(g);
  if (map) map.path = g;
  return g;
}

/** 0x442F28: per-family list of the distinct next hops of the routing matrix. */
function buildClist2(G, g) {
  const r = g.routing;
  const c2 = g.clist2;
  for (let fam = 1; fam < 255; fam++) {
    let count = 0;
    for (let b = 1; b < 255; b++) {
      const v = r[(fam << 8) + b];
      if (v === 0) continue;
      let found = false;
      for (let k = 1; k <= count; k++) {
        if (c2[fam * 32 + k] === v) {
          found = true;
          break;
        }
      }
      if (found) continue;
      count++;
      G.assert(count !== 32, 'count!=CLIST2_SIZE');
      c2[fam * 32 + count] = v; // count == 32 spills into the next family's count byte, as the original
    }
    c2[fam * 32] = count;
  }
  c2[0] = 0; // family 0
  c2[255 * 32] = 0; // family 255 (path+0x99084)
}

// ---- 0x442C60 ----------------------------------------------------------------------------------

/**
 * 0x442C60(&path, map, tiles): the same grid built from a 2-byte-per-tile array (`tiles[z][x]`
 * byte 1 = family), covering the border ring too. Not called anywhere in dc16.exe; kept for
 * completeness. `tileFamily(x, z)` supplies the byte for x = -1..w, z = -1..h.
 */
export function initPathGridFromTiles(G, w, h, tileFamily) {
  const g = new PathGrid(w, h);
  for (let x = -1; x <= w; x++) {
    for (let z = -1; z <= h; z++) {
      const c = g.idx(x, z);
      g.gen[c] = 0;
      g.cost[c] = -1;
      g.next[c] = NONE;
      g.cx[c] = x & 0xff;
      g.cz[c] = z & 0xff;
      g.d[c] = 0;
      g.family[c] = tileFamily(x, z) & 0xff;
    }
  }
  resetBuckets(g);
  return g;
}

// ---- 0x443068 ----------------------------------------------------------------------------------

/** walk every bucket list and assert that the prev links are consistent (debug helper). */
export function checkChains(G, g) {
  for (let i = 0; i < 256; i++) {
    let n = g.buckets[(g.cur + i) & 0xff];
    let prev = NONE;
    while (n !== NONE) {
      G.assert(g.prev[n] === prev, 'Whoa, the last pointer in the chain is corrupt');
      prev = n;
      n = g.next[n];
    }
  }
}

// ---- 0x443130 ----------------------------------------------------------------------------------

/** cost of the parent chain from (x, z): +5 per z step, +2 per x step, +2 more when both change. */
export function stepCost(G, g, x, z) {
  const gen = G.globals.pathGen;
  let cost = 0;
  for (;;) {
    const c = g.idx(x, z);
    if (g.gen[c] !== gen) return 0x8000;
    const px = g.px[c];
    const pz = g.pz[c];
    if (x === px && z === pz) return cost;
    if (z !== pz) {
      cost += 5;
      if (x !== px) cost += 2;
    }
    if (x !== px) cost += 2;
    if (Math.abs(px - x) > 1 || Math.abs(pz - z) > 1) throw new Error('TODO(exact): stepCost non-adjacent parent');
    x = px;
    z = pz;
  }
}

// ---- 0x443238 ----------------------------------------------------------------------------------

/** number of steps of the parent chain from (x, z) to the seed; 0x8000 when (x, z) was not reached. */
export function pathLength(G, g, x, z) {
  let c = g.idx(x, z);
  if (g.gen[c] !== G.globals.pathGen) return 0x8000;
  let n = 0;
  for (;;) {
    const pz = g.pz[c];
    const px = g.px[c];
    const d = DIR3X3[px + 1 - x + (pz + 1 - z) * 3];
    if (d === -1) return n;
    if (d === undefined) throw new Error('TODO(exact): pathLength non-adjacent parent');
    x = px;
    z = pz;
    n++;
    c = g.idx(x, z);
  }
}

// ---- 0x4432BC ----------------------------------------------------------------------------------

/**
 * 0x4432BC(path, x, z, dst, pos, size, len): write the parent chain from (x, z) as direction
 * nibbles into gs[dstAddr..] at nibble indices pos+len-1 down to pos (nibble i = byte i>>1, low
 * nibble for even i). Returns len, or 0x8000 when (x, z) was not reached. Every walked cell's stamp
 * is incremented and the global stamp is incremented once on success.
 */
export function extractPath(G, g, x, z, gs, dstAddr, pos, size, len) {
  const gen = G.globals.pathGen;
  let idx = pos + len - 1;
  for (;;) {
    const c = g.idx(x, z);
    if (g.gen[c] !== gen) return 0x8000;
    g.gen[c] = gen + 1;
    const pz = g.pz[c];
    const px = g.px[c];
    const d = DIR3X3[(pz + 1 - z) * 3 + (px + 1 - x)];
    if (d === -1 || idx === -1) break;
    if (d === undefined) throw new Error('TODO(exact): extractPath non-adjacent parent');
    G.assert(idiv(idx, 2) < size, 'pos/2<path_size');
    const a = dstAddr + idiv(idx, 2);
    const b = (u8(gs, a) & NIBBLE_MASK[idx & 1]) | ((d << ((idx & 1) * 4)) & 0xff);
    w8(gs, a, b);
    x = px;
    z = pz;
    idx--;
  }
  G.globals.pathGen++;
  return len;
}

// ---- 0x443458 / 0x443490 -------------------------------------------------------------------------

/** 0x443458(ground rows, air rows, width, height, fly): treat occupied tiles as blocked in relax. */
export function importObstacles(g, ground, air, width, height, fly) {
  g.obsAir = air;
  g.obsGround = ground;
  g.obsH = height;
  g.obsW = width;
  g.obstacles = 1;
  g.obstacleFly = fly ? 1 : 0;
}

/** 0x443490 */
export function clearObstacles(g) {
  g.obstacles = 0;
}

// ---- 0x4434A0 ----------------------------------------------------------------------------------

const TENT = new Int32Array(9); // [ebp-78h..-58h]
const ALLOW = new Int32Array(9); // 0x51D9BC..0x51D9DC (index 4 unused)

/**
 * relax(path, x, z, cost, xs, zs): push the eight neighbours of (x, z) into the buckets with
 * tentative costs cost + NEIGHBOUR_ORDER[dir to (xs, zs)][k], then mark (x, z) final (cost 0).
 */
export function relax(G, g, x, z, cost, xs, zs) {
  const gen = G.globals.pathGen;
  const cell = g.idx(x, z);
  const ddx = xs - x;
  const ddz = zs - z;
  const bias = (ddx > 0 ? 2 : ddx === 0 ? 1 : 0) + (ddz > 0 ? 6 : ddz === 0 ? 3 : 0);
  const table = neighbourOrder(G);
  for (let k = 0; k < 9; k++) TENT[k] = table[bias * 9 + k] + cost;

  const F = g.family;
  const A = g.allowed;
  ALLOW[0] = A[F[cell - ROW_CELLS - 1]];
  ALLOW[1] = A[F[cell - ROW_CELLS]];
  ALLOW[2] = A[F[cell - ROW_CELLS + 1]];
  ALLOW[3] = A[F[cell - 1]];
  ALLOW[5] = A[F[cell + 1]];
  ALLOW[6] = A[F[cell + ROW_CELLS - 1]];
  ALLOW[7] = A[F[cell + ROW_CELLS]];
  ALLOW[8] = A[F[cell + ROW_CELLS + 1]];

  if (g.obstacles !== 0) {
    const W = g.obsW;
    const H = g.obsH;
    const fly = g.obstacleFly !== 0;
    const grid = fly ? g.obsAir : g.obsGround;
    const occ = (xx, zz) => (grid[zz * W + xx] & 0x3ff) !== 0x3ff;
    if (x > 0) {
      if (z > 0 && occ(x - 1, z - 1)) ALLOW[0] = 0;
      if (occ(x - 1, z)) ALLOW[3] = 0;
      if (z < H - 1 && occ(x - 1, z + 1)) ALLOW[6] = 0;
    }
    if (x < W - 1) {
      if (z > 0 && occ(x + 1, z - 1)) ALLOW[2] = 0;
      if (occ(x + 1, z)) ALLOW[5] = 0;
      if (z < H - 1 && occ(x + 1, z + 1)) ALLOW[8] = 0;
    }
    if (z > 0 && occ(x, z - 1)) ALLOW[1] = 0;
    if (z < H - 1 && occ(x, z + 1)) ALLOW[7] = 0;
    if (fly && z === H - 2) {
      // 0x443A0C: flyers on the second-to-last row lose all three "up" neighbours
      ALLOW[1] = 0;
      ALLOW[2] = 0;
      ALLOW[0] = 0;
    }
  }

  // neighbour order of the original: R, DR, UR | L, UL, DL | D | U
  if (ALLOW[5] !== 0) {
    relaxTo(g, gen, cell + 1, TENT[5], x, z);
    if (ALLOW[8] !== 0) relaxTo(g, gen, cell + ROW_CELLS + 1, TENT[8], x, z);
    if (ALLOW[2] !== 0) relaxTo(g, gen, cell - ROW_CELLS + 1, TENT[2], x, z);
  } else {
    if (ALLOW[7] !== 0 && ALLOW[8] !== 0) relaxTo(g, gen, cell + ROW_CELLS + 1, TENT[8], x, z);
    if (ALLOW[1] !== 0 && ALLOW[2] !== 0) relaxTo(g, gen, cell - ROW_CELLS + 1, TENT[2], x, z);
  }
  if (ALLOW[3] !== 0) {
    relaxTo(g, gen, cell - 1, TENT[3], x, z);
    if (ALLOW[0] !== 0) relaxTo(g, gen, cell - ROW_CELLS - 1, TENT[0], x, z);
    if (ALLOW[6] !== 0) relaxTo(g, gen, cell + ROW_CELLS - 1, TENT[6], x, z);
  } else {
    if (ALLOW[0] !== 0 && ALLOW[1] !== 0) relaxTo(g, gen, cell - ROW_CELLS - 1, TENT[0], x, z);
    // 0x4442EB: the original reads tentative[1] (the UP weight) for the down-left neighbour here
    if (ALLOW[6] !== 0 && ALLOW[7] !== 0) relaxTo(g, gen, cell + ROW_CELLS - 1, TENT[1], x, z);
  }
  if (ALLOW[7] !== 0) relaxTo(g, gen, cell + ROW_CELLS, TENT[7], x, z);
  if (ALLOW[1] !== 0) relaxTo(g, gen, cell - ROW_CELLS, TENT[1], x, z);
  g.cost[cell] = 0;
}

/** one neighbour of relax: first visit / better-or-equal cost -> (re)insert at the bucket head. */
function relaxTo(g, gen, n, newCost, x, z) {
  if (g.gen[n] !== gen) {
    g.next[n] = NONE;
    g.cost[n] = -1;
    g.d[n] = 0;
    g.gen[n] = gen;
  } else {
    const c = g.cost[n];
    if (c === 0) return; // already popped
    if (c - g.d[n] < newCost && c !== -1) return; // strictly better already
    if (c !== -1) {
      // unlink from its bucket
      if (g.prev[n] !== NONE) g.next[g.prev[n]] = g.next[n];
      else g.buckets[(c - g.head + g.cur) & 0xff] = g.next[n];
      if (g.next[n] !== NONE) g.prev[g.next[n]] = g.prev[n];
    }
  }
  const b = (newCost - g.head + g.cur) & 0xff;
  g.next[n] = g.buckets[b];
  g.buckets[b] = n;
  g.pz[n] = z;
  g.cost[n] = newCost;
  g.px[n] = x;
  if (g.next[n] !== NONE) g.prev[g.next[n]] = n;
  g.prev[n] = NONE;
}

// ---- 0x4445CC ----------------------------------------------------------------------------------

/**
 * expand_one(path, xs, zs): pop the head of the current bucket (advancing through empty buckets),
 * relax it and unlink it. Returns 0 when (xs, zs) was popped or all 256 buckets are empty, else 1.
 */
export function expandOne(G, g, xs, zs) {
  const cur0 = g.cur;
  for (;;) {
    const n = g.buckets[g.cur];
    if (n === NONE) {
      g.cur = (g.cur + 1) & 0xff;
      g.head++;
      if (g.cur !== cur0) continue;
      return 0;
    }
    relax(G, g, g.cx[n], g.cz[n], g.cost[n], xs, zs);
    if (g.next[n] !== NONE) g.prev[g.next[n]] = NONE;
    g.buckets[g.cur] = g.next[n];
    return g.cx[n] === xs && g.cz[n] === zs ? 0 : 1;
  }
}

function seed(G, g, x, z) {
  const s = g.idx(x, z);
  g.gen[s] = G.globals.pathGen;
  g.cost[s] = 0;
  g.next[s] = NONE;
  g.px[s] = x;
  g.pz[s] = z;
}

// ---- 0x444B34 ----------------------------------------------------------------------------------

/**
 * find_path(path, xFrom, zFrom, xTo, zTo, fly): seed the search at (xFrom, zFrom) - begin_move
 * passes the DESTINATION here - and pop until (xTo, zTo) - the unit's tile - is reached. Ground
 * units may only enter the families of the routing chain fam(from) -> next[fam][fam(to)] -> ...;
 * flyers get the straight line of flyPath. Afterwards pathLength/extractPath from (xTo, zTo).
 */
export function findPath(G, g, xFrom, zFrom, xTo, zTo, fly) {
  if (fly) {
    flyPath(G, g, xFrom, zFrom, xTo, zTo);
    return;
  }
  g.allowed.fill(0);
  let fam = g.family[g.idx(xFrom, zFrom)];
  const famTo = g.family[g.idx(xTo, zTo)];
  G.assert(fam !== 0, 'current_family!=0');
  G.assert(famTo !== 0, 'destination_family!=0');
  let i = 0;
  for (;;) {
    g.allowed[fam] = 1;
    if (fam === famTo) break;
    fam = g.routing[(fam << 8) + famTo];
    if (fam === 0) G.assert(false, 'There is no path');
    if (++i >= 256) break;
  }
  G.globals.pathGen++;
  resetBuckets(g);
  seed(G, g, xFrom, zFrom);
  relax(G, g, xFrom, zFrom, 0, xTo, zTo);
  while (expandOne(G, g, xTo, zTo) !== 0) {
    /* pop */
  }
}

// ---- 0x44467C ----------------------------------------------------------------------------------

/** find_path variant with a caller-supplied 256-byte allowed set (copied into 0x51D8BC). */
export function findPathAllowed(G, g, xFrom, zFrom, xTo, zTo, allowedSet) {
  g.allowed.set(allowedSet.subarray ? allowedSet.subarray(0, 256) : Uint8Array.from(allowedSet).subarray(0, 256));
  G.globals.pathGen++;
  resetBuckets(g);
  seed(G, g, xFrom, zFrom);
  relax(G, g, xFrom, zFrom, 0, xTo, zTo);
  while (expandOne(G, g, xTo, zTo) !== 0) {
    /* pop */
  }
}

// ---- 0x444748 ----------------------------------------------------------------------------------

/**
 * re-route search (path, bx, bz, ux, uz, fly): every family allowed (except 0 for ground units and
 * the border 0xFF), seeded at the first free cell (bx, bz) of the blocked path, popping at most 256
 * nodes or until the unit's tile (ux, uz) is reached. Obstacles are usually active (importObstacles).
 */
export function rerouteSearch(G, g, bx, bz, ux, uz, fly) {
  G.globals.pathGen++;
  g.allowed.fill(0xff);
  if (!fly) g.allowed[0] = 0;
  g.allowed[0xff] = 0;
  resetBuckets(g);
  seed(G, g, bx, bz);
  relax(G, g, bx, bz, 0, ux, uz);
  let i = 0;
  for (;;) {
    if (expandOne(G, g, ux, uz) === 0) break;
    if (++i >= 256) break;
  }
}

// ---- 0x444824 ----------------------------------------------------------------------------------

/** game_tick's stamp bump (engine.js does this inline). */
export function bumpGeneration(G) {
  G.globals.pathGen += 2;
}

// ---- 0x444830 ----------------------------------------------------------------------------------

/**
 * flying path (path, xd, zd, xs, zs): mark a straight parent chain from (xs, zs) to (xd, zd):
 * from the destination first |dx|-|dz| (or |dz|-|dx|) straight steps, then the diagonal ones,
 * so the unit walks the diagonal part first. Ignores terrain.
 */
export function flyPath(G, g, xd, zd, xs, zs) {
  const gen = ++G.globals.pathGen;
  let dx = xd - xs;
  let dz = zd - zs;
  let stepX;
  let stepZ;
  if (dx > 0) stepX = -1;
  else {
    stepX = 1;
    dx = -dx;
  }
  if (dz > 0) stepZ = -1;
  else {
    stepZ = 1;
    dz = -dz;
  }
  const mark = (x, z, px, pz) => {
    const c = g.idx(x, z);
    g.gen[c] = gen;
    g.cost[c] = 0;
    g.px[c] = px;
    g.pz[c] = pz;
  };
  let x = xd;
  let z = zd;
  mark(x, z, x, z);
  if (dx > dz) {
    while (dx > dz) {
      x += stepX;
      mark(x, z, x - stepX, z);
      dx--;
    }
    while (dx > 0) {
      z += stepZ;
      x += stepX;
      mark(x, z, x - stepX, z - stepZ);
      dx--;
    }
  } else {
    while (dz > dx) {
      z += stepZ;
      mark(x, z, x, z - stepZ);
      dz--;
    }
    while (dz > 0) {
      z += stepZ;
      x += stepX;
      mark(x, z, x - stepX, z - stepZ);
      dz--;
    }
  }
}

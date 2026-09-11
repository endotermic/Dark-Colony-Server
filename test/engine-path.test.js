// path.js (path.c of dc16.exe) on a tiny hand-made map: find_path + path_length + extract_path.
// Independent of the other engine modules: `Game` is used when engine.js can be imported, otherwise
// a minimal stand-in with the same fields (gs, map, globals, consts, assert, rand).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { initPathGrid, findPath, pathLength, extractPath, stepCost, rerouteSearch, importObstacles, clearObstacles, PathGrid, NEIGHBOUR_ORDER } from '../src/engine/path.js';
import { O, u8, objAddr } from '../src/engine/mem.js';

const consts = JSON.parse(fs.readFileSync(new URL('../data/dc16-tables.json', import.meta.url), 'utf8'));
const tablesStub = { weapons: [{ range: 3 }], types: [{ fly: 0, speed: 25, move: 'MOVE', stand: 'STAND', weapon: [-1, -1, -1], weaponLevel: new Uint8Array(8), armourLevel: new Uint8Array(8), specialWeapon: 0 }] };

async function newGame() {
  try {
    const { Game } = await import('../src/engine/engine.js');
    return new Game(tablesStub, consts);
  } catch {
    return {
      gs: Buffer.alloc(0x48000),
      tables: tablesStub,
      consts,
      globals: { loopTrap: 0, pathGen: 0 },
      asserts: [],
      randIndex: 0,
      rand() {
        this.randIndex = (this.randIndex + 1) & 0xff;
        return consts.rand.values[this.randIndex];
      },
      assert(cond, msg) {
        if (!cond) this.asserts.push({ msg });
      },
    };
  }
}

/** G.map for a w x h grid: familyAt(x, z) -> family; `links` = [[a, b, next]] routing entries. */
async function makeGame(w, h, familyAt, links = []) {
  const G = await newGame();
  const families = new Uint8Array(w * h);
  for (let z = 0; z < h; z++) for (let x = 0; x < w; x++) families[z * w + x] = familyAt(x, z);
  const next = new Uint8Array(65536);
  for (let a = 1; a < 256; a++) next[a * 256 + a] = a;
  for (const [a, b, n] of links) next[a * 256 + b] = n;
  G.map = {
    w,
    h,
    load: new Int32Array(w * h),
    ground: new Int32Array(w * h).fill(0x3ff),
    air: new Int16Array(w * h).fill(0x3ff),
    sec: new Int16Array(w * h),
    families,
    next,
    path: null,
  };
  initPathGrid(G);
  return G;
}

const PATH_ADDR = 0x1000;

function nibbles(gs, len) {
  const out = [];
  for (let i = len - 1; i >= 0; i--) out.push((u8(gs, PATH_ADDR + (i >> 1)) >> ((i & 1) * 4)) & 0xf);
  return out; // first step first
}

/** run find_path(dest -> start) like begin_move and return the nibbles from the start. */
function plan(G, sx, sz, dx, dz, fly = false) {
  const g = G.map.path;
  findPath(G, g, dx, dz, sx, sz, fly);
  let len = pathLength(G, g, sx, sz);
  if (len > 32) len = 32;
  const r = extractPath(G, g, sx, sz, G.gs, PATH_ADDR, 0, 32, len);
  return { len, r, steps: nibbles(G.gs, len === 0x8000 ? 0 : len) };
}

function walk(G, sx, sz, steps) {
  const D = consts.dir8.values;
  let x = sx;
  let z = sz;
  const cells = [];
  for (const d of steps) {
    x += D[d * 2];
    z += D[d * 2 + 1];
    cells.push([x, z]);
  }
  return cells;
}

test('path grid: border ring is family 0xFF, cells carry their coordinates', async () => {
  const G = await makeGame(4, 3, () => 1);
  const g = G.map.path;
  assert.ok(g instanceof PathGrid);
  assert.equal(g.familyAt(-1, 1), 0xff);
  assert.equal(g.familyAt(4, 1), 0xff);
  assert.equal(g.familyAt(2, -1), 0xff);
  assert.equal(g.familyAt(2, 3), 0xff);
  assert.equal(g.familyAt(2, 1), 1);
  assert.equal(g.cx[g.idx(3, 2)], 3);
  assert.equal(g.cz[g.idx(3, 2)], 2);
  assert.equal(g.cost[g.idx(0, 0)], -1);
  assert.equal(g.clist2[1 * 32], 1); // family 1 has one next hop (itself)
  assert.equal(g.clist2[1 * 32 + 1], 1);
  assert.equal(NEIGHBOUR_ORDER.length, 81);
});

test('straight path: five steps east, generation stamp bumped by extract_path', async () => {
  const G = await makeGame(6, 6, () => 1);
  const gen0 = G.globals.pathGen;
  const p = plan(G, 0, 2, 5, 2);
  assert.equal(p.len, 5);
  assert.equal(p.r, 5);
  assert.deepEqual(p.steps, [4, 4, 4, 4, 4]);
  assert.equal(G.globals.pathGen, gen0 + 2); // +1 find_path, +1 extract_path
  assert.deepEqual(G.asserts, []);
});

test('diagonal path: five steps south-east', async () => {
  const G = await makeGame(6, 6, () => 1);
  const p = plan(G, 0, 0, 5, 5);
  assert.equal(p.len, 5);
  assert.deepEqual(p.steps, [7, 7, 7, 7, 7]);
});

test('around an obstacle: the path goes through the gap and stays on passable tiles', async () => {
  // wall of family 0 at x = 3 for z = 0..3, gap at z = 4
  const G = await makeGame(7, 5, (x, z) => (x === 3 && z < 4 ? 0 : 1));
  const p = plan(G, 0, 0, 6, 0);
  assert.notEqual(p.len, 0x8000);
  const cells = walk(G, 0, 0, p.steps);
  for (const [x, z] of cells) {
    assert.ok(x >= 0 && x < 7 && z >= 0 && z < 5, `inside the map ${x},${z}`);
    assert.notEqual(G.map.families[z * 7 + x], 0, `passable ${x},${z}`);
  }
  assert.deepEqual(cells[cells.length - 1], [6, 0]);
  assert.ok(cells.some(([x, z]) => x === 3 && z === 4), 'passes through the gap');
  assert.equal(p.len, 8); // the shortest possible route
  // regression pin of the exact visiting order (bit-exactness is the point of the port):
  // (0,1) (1,2) (2,3) (3,4) (4,3) (5,2) (6,1) (6,0)
  assert.deepEqual(p.steps, [6, 7, 7, 7, 2, 2, 2, 1]);
});

test('unreachable start inside its own family: path_length and extract_path return 0x8000', async () => {
  const G = await makeGame(7, 5, (x) => (x === 3 ? 0 : 1));
  const p = plan(G, 0, 2, 6, 2);
  assert.equal(p.len, 32); // begin_move caps 0x8000 to 32 before it compares with 0x8000
  assert.equal(p.r, 0x8000);
  assert.deepEqual(G.asserts, []);
});

test('two families without a route: "There is no path" assert', async () => {
  const G = await makeGame(7, 5, (x) => (x === 3 ? 0 : x < 3 ? 1 : 2));
  findPath(G, G.map.path, 6, 2, 0, 2, false);
  assert.ok(G.asserts.some((a) => a.msg === 'There is no path'));
});

test('route through a second family follows the routing chain', async () => {
  // family 1 (x<3), family 2 (x>3), family 3 = the gap cell (3,4): 1 -> 3 -> 2
  const fam = (x, z) => (x === 3 ? (z === 4 ? 3 : 0) : x < 3 ? 1 : 2);
  const G = await makeGame(7, 5, fam, [
    [1, 2, 3],
    [3, 2, 2],
    [2, 1, 3],
    [3, 1, 1],
  ]);
  const p = plan(G, 0, 0, 6, 0);
  const g = G.map.path;
  assert.deepEqual(Array.from(g.allowed.subarray(0, 4)), [0, 1, 1, 1]);
  const cells = walk(G, 0, 0, p.steps);
  assert.deepEqual(cells[cells.length - 1], [6, 0]);
  assert.ok(cells.some(([x, z]) => x === 3 && z === 4));
  assert.deepEqual(G.asserts, []);
});

test('flyer: straight line with the diagonal steps first, nibbles packed two per byte', async () => {
  const G = await makeGame(6, 6, () => 0); // terrain ignored
  const p = plan(G, 0, 0, 5, 3, true);
  assert.equal(p.len, 5);
  assert.deepEqual(p.steps, [7, 7, 7, 4, 4]);
  assert.deepEqual([u8(G.gs, PATH_ADDR), u8(G.gs, PATH_ADDR + 1), u8(G.gs, PATH_ADDR + 2)], [0x44, 0x77, 0x07]);
  assert.equal(stepCost(G, G.map.path, 0, 0), 3 * 9 + 2 * 2); // diagonal 9, horizontal 2
});

test('extract_path with an offset splices nibbles after the existing ones', async () => {
  const G = await makeGame(6, 6, () => 1);
  G.gs.fill(0xa5, PATH_ADDR, PATH_ADDR + 16);
  const g = G.map.path;
  findPath(G, g, 3, 0, 0, 0, false); // 3 steps east
  const len = pathLength(G, g, 0, 0);
  assert.equal(len, 3);
  extractPath(G, g, 0, 0, G.gs, PATH_ADDR, 4, 32, len); // nibbles 6..4
  assert.equal(u8(G.gs, PATH_ADDR), 0xa5); // nibbles 0..1 untouched
  assert.equal(u8(G.gs, PATH_ADDR + 1), 0xa5); // nibbles 2..3 untouched
  assert.equal(u8(G.gs, PATH_ADDR + 2), 0x44); // nibbles 4, 5
  assert.equal(u8(G.gs, PATH_ADDR + 3), 0xa4); // nibble 6 = 4, nibble 7 untouched
});

test('re-route search treats occupied tiles as blocked when obstacles are imported', async () => {
  const G = await makeGame(5, 5, () => 1);
  const g = G.map.path;
  G.map.ground[2 * 5 + 2] = (G.map.ground[2 * 5 + 2] & ~0x3ff) | 7; // object 7 on (2,2)
  // without obstacles the straight line crosses (2,2)
  findPath(G, g, 4, 2, 0, 2, false);
  let cells = walk(G, 0, 2, nibbles((extractPath(G, g, 0, 2, G.gs, PATH_ADDR, 0, 32, pathLength(G, g, 0, 2)), G.gs), 4));
  assert.ok(cells.some(([x, z]) => x === 2 && z === 2));
  // with obstacles the search detours
  importObstacles(g, G.map.ground, G.map.air, 5, 5, false);
  rerouteSearch(G, g, 4, 2, 0, 2, false);
  clearObstacles(g);
  const len = pathLength(G, g, 0, 2);
  assert.notEqual(len, 0x8000);
  extractPath(G, g, 0, 2, G.gs, PATH_ADDR, 0, 32, len);
  cells = walk(G, 0, 2, nibbles(G.gs, len));
  assert.ok(!cells.some(([x, z]) => x === 2 && z === 2), 'avoids the occupied tile');
  assert.deepEqual(cells[cells.length - 1], [4, 2]);
  assert.equal(g.obstacles, 0);
});

test('begin_move snaps the destination and fills the state-6 info block (when move.js loads)', async (t) => {
  let Move;
  try {
    Move = await import('../src/engine/move.js');
  } catch {
    t.skip('move.js depends on engine modules that are not present yet');
    return;
  }
  const G = await makeGame(6, 6, () => 1);
  const gs = G.gs;
  const obj = 200;
  const a = objAddr(obj);
  gs.writeInt16LE(0x0080, a + O.X);
  gs.writeInt16LE(0x0280, a + O.Z);
  gs[a + O.TYPE] = 0;
  gs[a + O.TEAM] = 0;
  gs[a + O.LIFE] = 1;
  gs[a + O.SP] = 0xff;
  gs.writeInt16LE(0x05f0, a + O.DEST_X); // tile 5, off-centre
  gs.writeInt16LE(0x0210, a + O.DEST_Z); // tile 2
  Move.beginMove(G, obj, 0, 0);
  assert.equal(gs.readInt16LE(a + O.DEST_X), 0x0580);
  assert.equal(gs.readInt16LE(a + O.DEST_Z), 0x0280);
  assert.equal(gs[a + O.SP], 0);
  assert.equal(gs[a + O.STACK], 6);
  const info = a + O.INFO;
  assert.deepEqual(
    [0, 2, 4, 6, 8, 10, 12].map((k) => gs.readInt16LE(info + k)),
    [4, 5, 0, 2, 0, -1, -1],
  );
  assert.equal(u8(gs, a + O.PATH), 0x44);
  assert.equal(u8(gs, a + O.PATH + 2), 0x04);
  Move.addPosition(G, obj, 25, -3);
  assert.equal(gs.readInt16LE(a + O.X), 0x0080 + 25);
  assert.equal(gs.readInt16LE(a + O.Z), 0x0280 - 3);
  assert.deepEqual(G.asserts, []);
});

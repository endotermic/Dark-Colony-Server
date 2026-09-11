// Tests of src/engine/missile.js (missile.c port): sincos / atan2 arithmetic and the missile
// free-list bookkeeping on a bare Game.
//
// Engine modules that other agents have not written yet (anim.js, grid.js, ...) are resolved to
// empty stub modules through a loader hook, so this file depends on nothing but mem.js,
// engine.js and missile.js. Every `import * as X` of a missing sibling then yields an empty
// namespace; none of its functions is called by the code under test here.

import { test, before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { register } from 'node:module';
import { fileURLToPath } from 'node:url';
import { GS, MS, GS_SIZE, i16, i32, w16, w32, missileAddr, MAX_MISSILES } from '../src/engine/mem.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, '..');
const consts = JSON.parse(fs.readFileSync(path.join(root, 'data', 'dc16-tables.json'), 'utf8'));

// resolve hook: a relative import of a non-existent src/engine/*.js becomes an empty module
const hook = `
export async function resolve(specifier, context, next) {
  try {
    return await next(specifier, context);
  } catch (e) {
    if (e.code === 'ERR_MODULE_NOT_FOUND' && /^\\.\\/[a-z]+\\.js$/.test(specifier) && /src\\/engine\\//.test(context.parentURL || '')) {
      return { url: 'data:text/javascript,export%20const%20__stub%20=%20true;', shortCircuit: true };
    }
    throw e;
  }
}`;
register(`data:text/javascript,${encodeURIComponent(hook)}`, import.meta.url);

let Missile;
let Game;
before(async () => {
  Missile = await import('../src/engine/missile.js');
  try {
    ({ Game } = await import('../src/engine/engine.js'));
  } catch {
    // minimal stand-in with the surface the missile module uses
    Game = class {
      constructor(tables, c) {
        this.tables = tables;
        this.consts = c;
        this.gs = Buffer.alloc(GS_SIZE);
        this.randTable = c.rand.values;
        this.randIndex = 0;
        this.globals = {};
        this.asserts = [];
      }
      rand() {
        this.randIndex = (this.randIndex + 1) & 0xff;
        return this.randTable[this.randIndex];
      }
      assert(cond, msg) {
        if (!cond) this.asserts.push({ msg });
      }
    };
  }
});

const tablesStub = { weapons: [], types: [], mbullet: { numArmours: 0, numWeapons: 0, rows: [] }, booms: [] };

function bareGame() {
  const G = new Game(tablesStub, consts);
  // the scenario loader initialises both list heads to -1
  w16(G.gs, GS.MISSILE_FREE, -1);
  w16(G.gs, GS.MISSILE_ACTIVE, -1);
  return G;
}

// ---- sincos ------------------------------------------------------------------------------------

test('sincos: quarter-wave table lookups in the four quadrants', () => {
  const G = bareGame();
  const tbl = consts.sinQuarter.values;
  assert.equal(tbl.length, 0x801);
  assert.deepEqual(Missile.sincos(G, 0), { sin: 0, cos: 2048 });
  assert.deepEqual(Missile.sincos(G, 0x800), { sin: 2048, cos: 0 });
  assert.equal(Missile.sincos(G, 0x800).cos, 0);
  assert.equal(Missile.sincos(G, 0x1000).cos, -2048);
  assert.equal(Missile.sincos(G, 0x1000).sin, 0);
  assert.deepEqual(Missile.sincos(G, 0x1800), { sin: -2048, cos: 0 });
  // 45 degrees: sin == cos == tbl[0x400] (1448 = 2048 * sqrt(1/2))
  assert.equal(tbl[0x400], 1448);
  assert.deepEqual(Missile.sincos(G, 0x400), { sin: 1448, cos: 1448 });
  // arbitrary angles read tbl[a] and tbl[0x800 - a] with the quadrant signs
  assert.deepEqual(Missile.sincos(G, 100), { sin: tbl[100], cos: tbl[0x800 - 100] });
  assert.deepEqual(Missile.sincos(G, 0x800 + 100), { sin: tbl[0x800 - 100], cos: -tbl[100] });
  assert.deepEqual(Missile.sincos(G, 0x1000 + 100), { sin: -tbl[100], cos: -tbl[0x800 - 100] });
  assert.deepEqual(Missile.sincos(G, 0x1800 + 100), { sin: -tbl[0x800 - 100], cos: tbl[100] });
  // the angle wraps at 8192 (and 0x1FFF)
  assert.deepEqual(Missile.sincos(G, 0x2000 + 100), Missile.sincos(G, 100));
  // heading 64 of 256 << 5 = a quarter turn
  assert.deepEqual(Missile.sincos(G, 64 << 5), Missile.sincos(G, 0x800));
});

// ---- atan2 -------------------------------------------------------------------------------------

test('atan2: the eight compass directions and the origin', () => {
  // a is the first (eax) argument: +a = 0, +b = quarter turn (0x800), 8192 units per turn
  assert.equal(Missile.atan2(0, 0), 0);
  assert.equal(Missile.atan2(1, 0), 0);
  assert.equal(Missile.atan2(1, 1), 0x400);
  assert.equal(Missile.atan2(0, 1), 0x800);
  assert.equal(Missile.atan2(-1, 1), 0xc00);
  assert.equal(Missile.atan2(-1, 0), 0x1000);
  assert.equal(Missile.atan2(-1, -1), 0x1400);
  assert.equal(Missile.atan2(0, -1), 0x1800);
  assert.equal(Missile.atan2(1, -1), 0x1c00);
  // longer legs give the same directions
  assert.equal(Missile.atan2(7, 7), 0x400);
  assert.equal(Missile.atan2(0, -300), 0x1800);
  assert.equal(Missile.atan2(-300, 0), 0x1000);
});

test('atan2: table lookups between the compass points', () => {
  // atan(1/2) = 26.565 deg = 604.6 units -> table[128] = 604 (truncated ratio (1<<8)/2 = 128)
  assert.equal(Missile.atan2(2, 1), 604);
  assert.equal(Missile.atan2(1, 2), 0x800 - 604);
  assert.equal(Missile.atan2(-2, 1), 0x1000 - 604);
  assert.equal(Missile.atan2(-1, 2), 0x800 + 604);
  assert.equal(Missile.atan2(-2, -1), 0x1000 + 604);
  assert.equal(Missile.atan2(-1, -2), 0x1800 - 604);
  assert.equal(Missile.atan2(2, -1), 0x2000 - 604);
  assert.equal(Missile.atan2(1, -2), 0x1800 + 604);
  // idiv truncates: (3<<8)/7 = 109 -> table[109]
  assert.equal(Missile.atan2(7, 3), Missile.ATAN_TABLE[109]);
  assert.equal(Missile.ATAN_TABLE.length, 256);
});

// ---- free list -----------------------------------------------------------------------------------

test('allocMissile grows the pool while the free list is empty', () => {
  const G = bareGame();
  assert.equal(Missile.allocMissile(G), 0);
  assert.equal(i32(G.gs, GS.MISSILE_COUNT), 1);
  assert.equal(Missile.allocMissile(G), 1);
  assert.equal(Missile.allocMissile(G), 2);
  assert.equal(i32(G.gs, GS.MISSILE_COUNT), 3);
  assert.equal(G.asserts.length, 0);
});

test('freeMissile pushes on the free list; allocMissile pops it without touching the count', () => {
  const G = bareGame();
  const gs = G.gs;
  // three records linked into the active list the way createMissile does: 2 -> 1 -> 0
  for (let k = 0; k < 3; k++) {
    const m = Missile.allocMissile(G);
    w16(gs, missileAddr(m) + MS.NEXT, i16(gs, GS.MISSILE_ACTIVE));
    w16(gs, GS.MISSILE_ACTIVE, m);
  }
  assert.equal(i16(gs, GS.MISSILE_ACTIVE), 2);
  // free the head (prev = -1)
  Missile.freeMissile(G, 2, -1);
  assert.equal(i16(gs, GS.MISSILE_ACTIVE), 1);
  assert.equal(i16(gs, GS.MISSILE_FREE), 2);
  assert.equal(i16(gs, missileAddr(2) + MS.NEXT), -1);
  // free the tail (prev = 1)
  Missile.freeMissile(G, 0, 1);
  assert.equal(i16(gs, missileAddr(1) + MS.NEXT), -1);
  assert.equal(i16(gs, GS.MISSILE_FREE), 0);
  assert.equal(i16(gs, missileAddr(0) + MS.NEXT), 2);
  // LIFO reuse, count unchanged
  assert.equal(Missile.allocMissile(G), 0);
  assert.equal(Missile.allocMissile(G), 2);
  assert.equal(i16(gs, GS.MISSILE_FREE), -1);
  assert.equal(i32(gs, GS.MISSILE_COUNT), 3);
  assert.equal(Missile.allocMissile(G), 3);
  assert.equal(i32(gs, GS.MISSILE_COUNT), 4);
});

test('allocMissile asserts number_of_missiles < MAX_MISSILES but still hands out the slot', () => {
  const G = bareGame();
  w32(G.gs, GS.MISSILE_COUNT, MAX_MISSILES - 1);
  assert.equal(Missile.allocMissile(G), MAX_MISSILES - 1);
  assert.equal(i32(G.gs, GS.MISSILE_COUNT), MAX_MISSILES);
  assert.equal(G.asserts.length, 1);
  assert.match(G.asserts[0].msg, /number_of_missiles<MAX_MISSILES/);
});

test('missilesTick frees every state-4 record and counts launch delays down', () => {
  const G = bareGame();
  const gs = G.gs;
  for (let k = 0; k < 3; k++) {
    const m = Missile.allocMissile(G);
    const ma = missileAddr(m);
    w16(gs, ma + MS.NEXT, i16(gs, GS.MISSILE_ACTIVE));
    w16(gs, GS.MISSILE_ACTIVE, m);
    w16(gs, ma + MS.STATE, k === 1 ? 0 : 4); // waiting record in the middle, finished at both ends
    w16(gs, ma + MS.DELAY, 9);
  }
  Missile.missilesTick(G);
  assert.equal(i16(gs, GS.MISSILE_ACTIVE), 1);
  assert.equal(i16(gs, missileAddr(1) + MS.NEXT), -1);
  assert.equal(i16(gs, GS.MISSILE_FREE), 0);
  assert.equal(i16(gs, missileAddr(0) + MS.NEXT), 2);
  assert.equal(i16(gs, missileAddr(2) + MS.NEXT), -1);
  assert.equal(i16(gs, missileAddr(1) + MS.DELAY), 5); // four update passes
  assert.equal(i16(gs, missileAddr(1) + MS.STATE), 0);
  Missile.missilesTick(G);
  assert.equal(i16(gs, missileAddr(1) + MS.DELAY), 1);
  assert.equal(i16(gs, missileAddr(1) + MS.STATE), 0);
  assert.equal(i32(gs, GS.MISSILE_COUNT), 3);
});

// Tests of src/engine/ticker.js (ticker.c / mobiles.c idle+order port) on a hand-built object.
//
// The module is loaded from a temporary copy next to stub versions of its sibling engine modules
// (move, combat, missile, anim, city, renat, grid), so the test neither depends on nor exercises the
// other agents' code: every cross-module call is recorded in `R.calls` and answered by `R.impl`.
// The Game comes from src/engine/engine.js when that module (and its imports) load; otherwise a
// minimal stand-in with the same surface (gs Buffer, consts, tables, rand/srand, assert) is used.

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { GS, O, P, u8, i16, i32, w8, w16, w32, objAddr, playerAddr, GS_SIZE } from '../src/engine/mem.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, '..');
const consts = JSON.parse(fs.readFileSync(path.join(root, 'data', 'dc16-tables.json'), 'utf8'));

// ---- stub registry -------------------------------------------------------------------------------

const R = {
  calls: [],
  impl: {},
  call(name, args) {
    this.calls.push([name, ...args.slice(1)]);
    const f = this.impl[name];
    return f ? f(...args) : 0;
  },
  reset() {
    this.calls = [];
    this.impl = {};
  },
  named(name) {
    return this.calls.filter((c) => c[0] === name);
  },
};
globalThis.__tickerStubs = R;

const STUBS = {
  move: ['beginMove', 'stateStep', 'stateMove'],
  combat: [
    'findTargetInRange',
    'findTarget',
    'fireWeapon',
    'healNearby',
    'objectDie',
    'stateCorpse',
    'stateWreck',
    'stateFireAtGround',
    'stateDeploy',
    'orderFireAtGround',
  ],
  missile: ['atan2'],
  anim: ['startAnim', 'isPlaying', 'advanceAnims', 'advanceSlots', 'currentAnim'],
  city: ['stateBuildingIdle'],
  renat: [
    'pickup',
    'idleVent',
    'idleArtifactSite',
    'stateHarvest',
    'stateF',
    'state10',
    'state13',
    'state14',
    'state15',
    'state16',
  ],
  grid: ['cellGround', 'cellAir', 'cellSecondary', 'passable', 'removeObject'],
};
const MODULE_PREFIX = {
  move: 'Move',
  combat: 'Combat',
  missile: 'Missile',
  anim: 'Anim',
  city: 'City',
  renat: 'Renat',
  grid: 'Grid',
};

let Ticker;
let Game;
let tmp;

before(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dc-ticker-'));
  for (const f of ['ticker.js', 'mem.js']) fs.copyFileSync(path.join(root, 'src', 'engine', f), path.join(tmp, f));
  for (const [mod, names] of Object.entries(STUBS)) {
    const src =
      'const R = globalThis.__tickerStubs;\n' +
      names.map((n) => `export const ${n} = (...a) => R.call('${MODULE_PREFIX[mod]}.${n}', a);`).join('\n') +
      '\n';
    fs.writeFileSync(path.join(tmp, `${mod}.js`), src);
  }
  Ticker = await import(pathToFileURL(path.join(tmp, 'ticker.js')).href);
  try {
    ({ Game } = await import('../src/engine/engine.js'));
  } catch {
    Game = FakeGame; // sibling modules of engine.js not present yet
  }
});

after(() => {
  if (tmp) fs.rmSync(tmp, { recursive: true, force: true });
});

/** Stand-in for engine.js's Game while the other engine modules are being written. */
class FakeGame {
  constructor(tables, c) {
    this.tables = tables;
    this.consts = c;
    this.gs = Buffer.alloc(GS_SIZE);
    this.map = null;
    this.scenario = null;
    this.randTable = c.rand.values;
    this.randIndex = 0;
    this.globals = { loopTrap: 0, pathGen: 0 };
    this.asserts = [];
  }
  srand(seed) {
    this.randIndex = seed & 0xff;
  }
  rand() {
    this.randIndex = (this.randIndex + 1) & 0xff;
    return this.randTable[this.randIndex];
  }
  assert(cond, msg) {
    if (!cond) this.asserts.push({ tick: i32(this.gs, GS.TICK), msg });
  }
}

// ---- fixtures -------------------------------------------------------------------------------------

const MOBILE = 1; // armed, mobile
const BUILDING = 2; // unarmed, immobile
const FLYER = 3;
const STILL = 4; // armed, mobile, column 20 set (no fidget turn)

function typeRecord(o) {
  return {
    turnSpeed: 16,
    speed: 25,
    weapon: [7, 8, 9],
    weaponLevel: new Uint8Array(8),
    armourLevel: new Uint8Array(8),
    fly: 0,
    stand: 'STAND',
    deploy: 'DEPLOY',
    funk: 'FUNK',
    blood: ['BLOODA', 'BLOODB', 'BLOODC'],
    numBlood: 3,
    standStill: 0,
    chargeRegen: 4,
    rallyBonus: 0,
    ...o,
  };
}

function makeTables() {
  const types = new Array(130).fill(null).map(() => typeRecord({ weapon: [-1, -1, -1], speed: 0 }));
  types[MOBILE] = typeRecord({});
  types[BUILDING] = typeRecord({ weapon: [-1, -1, -1], speed: 0 });
  types[FLYER] = typeRecord({ fly: 1, speed: 47 });
  types[STILL] = typeRecord({ standStill: 1 });
  types[0x2b] = typeRecord({ weapon: [-1, -1, -1] }); // mine layer (deploy test)
  return { weapon: [], types, mbullet: null, booms: [], depend: [], unitid: null };
}

function newGame() {
  R.reset();
  const G = new Game(makeTables(), consts);
  G.map = { w: 10, h: 10 };
  G.srand(0);
  return G;
}

/** A live object of `type` at tile (x, z) with an empty state stack. */
function makeObject(G, obj, type, x = 5, z = 5, team = 0) {
  const gs = G.gs;
  const a = objAddr(obj);
  gs.fill(0, a, a + 0xdc);
  w16(gs, a + O.X, (x << 8) + 0x80);
  w16(gs, a + O.Z, (z << 8) + 0x80);
  w8(gs, a + O.TYPE, type);
  w8(gs, a + O.TEAM, team);
  w8(gs, a + O.LIFE, 1);
  w32(gs, a + O.HP, 300);
  w8(gs, a + O.SP, 0xff);
  w8(gs, a + O.NUDGE, 0xff);
  w8(gs, a + O.ORDER, 0xff);
  w16(gs, GS.OBJ_ALLOC + obj * 2, 0);
  return a;
}

function stack(G, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  const sp = gs.readInt8(a + O.SP);
  const out = [];
  for (let k = 0; k <= sp; k++) out.push(u8(gs, a + O.STACK + 2 * k));
  return out;
}

const infoAt = (G, obj, level) => {
  const a = objAddr(obj);
  return a + O.INFO + 2 * u8(G.gs, a + O.STACK + 1 + 2 * level);
};

/** Position the RNG so that the next rand() returns a value with `pred(value)`. */
function seekRand(G, pred) {
  const t = consts.rand.values;
  for (let i = 0; i < 256; i++) {
    if (pred(t[i])) {
      G.srand((i - 1) & 0xff);
      return t[i];
    }
  }
  throw new Error('no such rand value');
}

// ---- stack primitives -----------------------------------------------------------------------------

test('pushState allocates info blocks after the previous level and popState unwinds', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  const info0 = Ticker.pushState(G, 200, 3, 2);
  assert.equal(info0, a + O.INFO);
  assert.equal(G.gs.readInt8(a + O.SP), 0);
  assert.deepEqual(stack(G, 200), [3]);
  assert.equal(u8(G.gs, a + O.STACK + 1 + 2), 2, 'next level info offset = nwords');
  const info1 = Ticker.pushState(G, 200, 0x0b, 1);
  assert.equal(info1, a + O.INFO + 4);
  assert.deepEqual(stack(G, 200), [3, 0x0b]);
  assert.equal(u8(G.gs, a + O.STACK + 1 + 4), 3);
  Ticker.popState(G, 200);
  Ticker.popState(G, 200);
  assert.equal(u8(G.gs, a + O.SP), 0xff);
  assert.deepEqual(G.asserts, []);
});

test('pushState asserts on dead objects except for the corpse state', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  w8(G.gs, a + O.LIFE, 0);
  Ticker.pushState(G, 200, 0x0a, 0);
  assert.deepEqual(G.asserts, []);
  Ticker.pushState(G, 200, 1, 3);
  assert.equal(G.asserts.length, 1);
  assert.match(G.asserts[0].msg, /state push on dead or rotting object/);
});

test('pushState asserts when the stack overflows or the info area is exhausted', () => {
  const G = newGame();
  makeObject(G, 200, MOBILE);
  for (let k = 0; k < 6; k++) Ticker.pushState(G, 200, 3, 5);
  assert.deepEqual(G.asserts, []);
  Ticker.pushState(G, 200, 3, 1); // sp = 6, info offset 30
  assert.deepEqual(
    G.asserts.map((x) => x.msg),
    ['pos<OBJECT_STACK_SIZE'],
  );
});

test('pushIdle writes {target -1, hp snapshot, fidget 0}', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  w32(G.gs, a + O.HP, 0x12345);
  Ticker.pushIdle(G, 200);
  const info = infoAt(G, 200, 0);
  assert.deepEqual(stack(G, 200), [1]);
  assert.equal(i16(G.gs, info), -1);
  assert.equal(i16(G.gs, info + 2), 0x2345, 'low word of the hit points');
  assert.equal(i16(G.gs, info + 4), 0);
});

test('resetAndDispatchOrder runs the pending order or pushes idle', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  Ticker.pushState(G, 200, 3, 2);
  Ticker.pushState(G, 200, 4, 1);
  w8(G.gs, a + O.PENDING, 1);
  w8(G.gs, a + O.ORDER, 2); // move
  Ticker.resetAndDispatchOrder(G, 200);
  assert.deepEqual(stack(G, 200), [2, 8], 'marker state 2 + waypoint follower');
  assert.equal(u8(G.gs, a + O.PENDING), 0);
  assert.equal(u8(G.gs, a + O.ORDER), 0xff);
  const info = infoAt(G, 200, 1);
  assert.equal(i16(G.gs, info), 0, 'waypoint index');
  assert.equal(i16(G.gs, info + 2), 0, 'mode 0');

  Ticker.resetAndDispatchOrder(G, 200);
  assert.deepEqual(stack(G, 200), [1]);
  assert.deepEqual(G.asserts, []);
});

test('immobile types reset to idle on move / assault / patrol; attack pushes 0xE first', () => {
  const G = newGame();
  makeObject(G, 200, BUILDING);
  Ticker.orderMove(G, 200);
  assert.deepEqual(stack(G, 200), [1]);
  makeObject(G, 200, BUILDING);
  Ticker.orderAssault(G, 200);
  assert.deepEqual(stack(G, 200), [1]);
  makeObject(G, 200, BUILDING);
  Ticker.orderPatrol(G, 200);
  assert.deepEqual(stack(G, 200), [1]);
  makeObject(G, 200, BUILDING);
  Ticker.orderAttack(G, 200);
  assert.deepEqual(stack(G, 200), [1]);
  assert.deepEqual(R.named('Move.beginMove'), []);

  makeObject(G, 200, MOBILE);
  Ticker.orderAttack(G, 200);
  assert.deepEqual(stack(G, 200), [0x0e]);
  assert.deepEqual(R.named('Move.beginMove'), [['Move.beginMove', 200, 3, 0]]);
});

// ---- sleep / turn / cooldown -----------------------------------------------------------------------

test('stateSleep counts down, then pops; wakes early on damage or a pending order', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  Ticker.sleepState(G, 200, 2);
  const info = infoAt(G, 200, 0);
  assert.equal(i16(G.gs, info), 2);
  assert.equal(i16(G.gs, info + 2), 300);
  assert.equal(Ticker.stateSleep(G, 200, info), 0);
  assert.equal(i16(G.gs, info), 1);
  assert.equal(Ticker.stateSleep(G, 200, info), 0);
  assert.equal(i16(G.gs, info), 0);
  assert.equal(Ticker.stateSleep(G, 200, info), 0, 'pop without rerun when the count reaches zero');
  assert.equal(u8(G.gs, a + O.SP), 0xff);

  Ticker.sleepState(G, 200, 10);
  w32(G.gs, a + O.HP, 299);
  assert.equal(Ticker.stateSleep(G, 200, info), 1, 'hit points changed: pop and rerun');
  assert.equal(u8(G.gs, a + O.SP), 0xff);

  Ticker.sleepState(G, 200, 10);
  w8(G.gs, a + O.PENDING, 1);
  assert.equal(Ticker.stateSleep(G, 200, info), 1);
  assert.equal(u8(G.gs, a + O.SP), 0xff);
});

test('turnTowards takes the short way round and snaps within one step', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE); // turn rate 16
  w8(G.gs, a + O.HEADING, 0);
  const seen = [];
  let r;
  do {
    r = Ticker.turnTowards(G, 200, 200);
    seen.push(u8(G.gs, a + O.HEADING));
  } while (r !== 0);
  assert.deepEqual(seen, [240, 224, 208, 200]);
  w8(G.gs, a + O.HEADING, 250);
  assert.equal(Ticker.turnTowards(G, 200, 10), 0, 'wraps through 0 in one step');
  assert.equal(u8(G.gs, a + O.HEADING), 10);
  w8(G.gs, a + O.HEADING, 100);
  assert.equal(Ticker.turnTowards(G, 200, 100), 0);
});

test('stateTurn pops and reruns once aligned', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  w8(G.gs, a + O.HEADING, 0);
  Ticker.pushTurn(G, 200, 40);
  const info = infoAt(G, 200, 0);
  assert.equal(i16(G.gs, info), 40);
  assert.equal(Ticker.stateTurn(G, 200, info), 0);
  assert.equal(u8(G.gs, a + O.HEADING), 16);
  assert.equal(Ticker.stateTurn(G, 200, info), 0);
  assert.equal(Ticker.stateTurn(G, 200, info), 1);
  assert.equal(u8(G.gs, a + O.HEADING), 40);
  assert.equal(u8(G.gs, a + O.SP), 0xff);
});

test('stateCooldown shows STAND when the fire animation ended and counts down', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  Ticker.setCooldown(G, 200, 2);
  const info = infoAt(G, 200, 0);
  assert.deepEqual(stack(G, 200), [0x0b]);
  assert.equal(Ticker.stateCooldown(G, 200, info), 0);
  assert.deepEqual(R.named('Anim.startAnim'), [], 'animation still playing: no STAND');
  w8(G.gs, a + O.ANIM0_STATUS, 2);
  assert.equal(Ticker.stateCooldown(G, 200, info), 0);
  assert.deepEqual(R.named('Anim.startAnim'), [['Anim.startAnim', a + O.ANIM0, 'STAND', 0]]);
  assert.equal(i16(G.gs, info), 0);
  assert.equal(Ticker.stateCooldown(G, 200, info), 0);
  assert.equal(u8(G.gs, a + O.SP), 0xff);
});

// ---- directions ----------------------------------------------------------------------------------------

test('dirTo divides the atan2 result by 32 towards zero and wraps to a byte', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE, 5, 5);
  R.impl['Missile.atan2'] = (dx, dz) => {
    assert.equal(dx, 0x100);
    assert.equal(dz, -0x200);
    return 2048;
  };
  assert.equal(Ticker.dirTo(G, 200, i16(G.gs, a + O.X) + 0x100, i16(G.gs, a + O.Z) - 0x200), 64);
  R.impl['Missile.atan2'] = () => -1;
  assert.equal(Ticker.dirTo(G, 200, 0, 0), 0);
  R.impl['Missile.atan2'] = () => -32;
  assert.equal(Ticker.dirTo(G, 200, 0, 0), 255);
  R.impl['Missile.atan2'] = () => 0x18000 + 64; // only the low 16 bits (ax) are used: -32704
  assert.equal(Ticker.dirTo(G, 200, 0, 0), -1022 & 0xff);
});

// ---- waypoints ----------------------------------------------------------------------------------------

test('orderMove + stateWaypoints walk every waypoint with begin_move mode 0, then pop', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  w16(G.gs, a + O.WAYPOINTS, 0x380);
  w16(G.gs, a + O.WAYPOINTS + 2, 0x480);
  w16(G.gs, a + O.WAYPOINTS + 4, 0x580);
  w16(G.gs, a + O.WAYPOINTS + 6, 0x680);
  w8(G.gs, a + O.WP_COUNT, 2);
  Ticker.orderMove(G, 200);
  assert.deepEqual(stack(G, 200), [2, 8]);
  const info = infoAt(G, 200, 1);
  assert.equal(Ticker.stateWaypoints(G, 200, info), 1);
  assert.equal(i16(G.gs, a + O.DEST_X), 0x380);
  assert.equal(i16(G.gs, a + O.DEST_Z), 0x480);
  assert.equal(Ticker.stateWaypoints(G, 200, info), 1);
  assert.equal(i16(G.gs, a + O.DEST_X), 0x580);
  assert.equal(i16(G.gs, a + O.DEST_Z), 0x680);
  assert.deepEqual(R.named('Move.beginMove'), [
    ['Move.beginMove', 200, 0, 0],
    ['Move.beginMove', 200, 0, 0],
  ]);
  assert.equal(Ticker.stateWaypoints(G, 200, info), 1);
  assert.equal(u8(G.gs, a + O.WP_COUNT), 0);
  assert.deepEqual(stack(G, 200), [2]);
  assert.equal(Ticker.stateMoveDone(G, 200), 1);
  assert.deepEqual(stack(G, 200), [1], 'marker state resets to idle');
});

test('statePatrol wraps the waypoint index and always uses mode 1', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  w16(G.gs, a + O.WAYPOINTS, 0x180);
  w16(G.gs, a + O.WAYPOINTS + 2, 0x280);
  w8(G.gs, a + O.WP_COUNT, 1);
  Ticker.orderPatrol(G, 200);
  const info = infoAt(G, 200, 0);
  assert.equal(Ticker.statePatrol(G, 200, info), 1);
  assert.equal(i16(G.gs, info), 1);
  assert.equal(Ticker.statePatrol(G, 200, info), 1);
  assert.equal(i16(G.gs, info), 1, 'index wrapped to 0 and advanced again');
  assert.equal(i16(G.gs, a + O.DEST_X), 0x180);
  assert.deepEqual(R.named('Move.beginMove'), [
    ['Move.beginMove', 200, 1, 0],
    ['Move.beginMove', 200, 1, 0],
  ]);
});

// ---- idle state -----------------------------------------------------------------------------------------

test('idle fidget: one rand() for the 1/16 chance, a second one for the heading, sleep 15 then 45', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  R.impl['Combat.findTargetInRange'] = () => -1;
  R.impl['Combat.findTarget'] = () => -1;
  Ticker.pushIdle(G, 200);
  const info = infoAt(G, 200, 0);

  // miss the 1/16 chance
  seekRand(G, (v) => (v & 0x0f) !== 0);
  const before = G.randIndex;
  assert.equal(Ticker.stateIdle(G, 200, info), 0);
  assert.equal(G.randIndex, (before + 1) & 0xff, 'exactly one rand()');
  assert.deepEqual(stack(G, 200), [1, 3]);
  assert.equal(i16(G.gs, infoAt(G, 200, 1)), 15);
  assert.equal(i16(G.gs, info + 4), 1, 'fidget count');
  assert.deepEqual(R.named('Combat.findTarget'), [['Combat.findTarget', 200, 4]], 'human, undamaged: 4 rings');
  assert.deepEqual(R.named('Anim.startAnim'), [['Anim.startAnim', a + O.ANIM0, 'STAND', 0]]);

  // hit the 1/16 chance: turn state below the sleep state
  Ticker.popState(G, 200);
  R.reset();
  R.impl['Combat.findTargetInRange'] = () => -1;
  R.impl['Combat.findTarget'] = () => -1;
  seekRand(G, (v) => (v & 0x0f) === 0);
  const heading = consts.rand.values[(G.randIndex + 2) & 0xff] & 0xff;
  const b2 = G.randIndex;
  assert.equal(Ticker.stateIdle(G, 200, info), 0);
  assert.equal(G.randIndex, (b2 + 2) & 0xff, 'two rand() calls');
  assert.deepEqual(stack(G, 200), [1, 4, 3]);
  assert.equal(i16(G.gs, infoAt(G, 200, 1)), heading);
  assert.equal(i16(G.gs, info + 4), 2);

  // fourth round sleeps 45
  Ticker.popState(G, 200);
  Ticker.popState(G, 200);
  w16(G.gs, info + 4, 3);
  seekRand(G, (v) => (v & 0x0f) !== 0);
  Ticker.stateIdle(G, 200, info);
  assert.equal(i16(G.gs, infoAt(G, 200, 1)), 45);
  assert.equal(i16(G.gs, info + 4), 3);
});

test('idle: column 20 types never draw the heading; damage widens the search to 9 rings', () => {
  const G = newGame();
  const a = makeObject(G, 200, STILL);
  R.impl['Combat.findTargetInRange'] = () => -1;
  R.impl['Combat.findTarget'] = () => -1;
  Ticker.pushIdle(G, 200);
  const info = infoAt(G, 200, 0);
  w32(G.gs, a + O.HP, 250); // below the snapshot of 300
  seekRand(G, (v) => (v & 0x0f) === 0);
  const before = G.randIndex;
  Ticker.stateIdle(G, 200, info);
  assert.equal(G.randIndex, (before + 1) & 0xff, 'only the chance rand()');
  assert.deepEqual(stack(G, 200), [1, 3]);
  assert.deepEqual(R.named('Combat.findTarget'), [['Combat.findTarget', 200, 9]]);
  assert.equal(i16(G.gs, info + 2), 250, 'snapshot refreshed');
});

test('idle: AI players and wildlife search 16 rings, flyers never search wide', () => {
  const G = newGame();
  R.impl['Combat.findTargetInRange'] = () => -1;
  R.impl['Combat.findTarget'] = () => -1;
  makeObject(G, 200, MOBILE, 5, 5, 9);
  Ticker.pushIdle(G, 200);
  seekRand(G, (v) => (v & 0x0f) !== 0);
  Ticker.stateIdle(G, 200, infoAt(G, 200, 0));
  assert.deepEqual(R.named('Combat.findTarget'), [['Combat.findTarget', 200, 16]]);

  R.reset();
  R.impl['Combat.findTargetInRange'] = () => -1;
  makeObject(G, 201, MOBILE, 5, 5, 2);
  w32(G.gs, playerAddr(2) + P.AI_TYPE, 3);
  Ticker.pushIdle(G, 201);
  seekRand(G, (v) => (v & 0x0f) !== 0);
  Ticker.stateIdle(G, 201, infoAt(G, 201, 0));
  assert.deepEqual(R.named('Combat.findTarget'), [['Combat.findTarget', 201, 16]]);

  R.reset();
  R.impl['Combat.findTargetInRange'] = () => -1;
  makeObject(G, 202, FLYER);
  Ticker.pushIdle(G, 202);
  seekRand(G, (v) => (v & 0x0f) !== 0);
  Ticker.stateIdle(G, 202, infoAt(G, 202, 0));
  assert.deepEqual(R.named('Combat.findTarget'), []);
});

test('idle: a target in range is fired at, a spotted one is walked to with begin_move mode 2', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  makeObject(G, 300, MOBILE, 8, 2, 1);
  Ticker.pushIdle(G, 200);
  const info = infoAt(G, 200, 0);
  w16(G.gs, info + 4, 2);
  R.impl['Combat.findTargetInRange'] = () => 300;
  R.impl['Combat.fireWeapon'] = () => 0;
  assert.equal(Ticker.stateIdle(G, 200, info), 0);
  assert.equal(i16(G.gs, info), 300);
  assert.equal(i16(G.gs, info + 4), 0);
  assert.deepEqual(R.named('Combat.fireWeapon'), [['Combat.fireWeapon', 200, 300, 0, 0]]);

  R.reset();
  R.impl['Combat.findTargetInRange'] = () => -1;
  R.impl['Combat.findTarget'] = () => 300;
  assert.equal(Ticker.stateIdle(G, 200, info), 0);
  assert.equal(i16(G.gs, a + O.DEST_X), (8 << 8) + 0x80);
  assert.equal(i16(G.gs, a + O.DEST_Z), (2 << 8) + 0x80);
  assert.deepEqual(R.named('Move.beginMove'), [['Move.beginMove', 200, 2, 0]]);
  assert.deepEqual(stack(G, 200), [1]);
});

test('idle: unarmed types branch to their owners, buildings to city.js, everyone else sleeps 7', () => {
  const G = newGame();
  makeObject(G, 5, BUILDING);
  Ticker.pushIdle(G, 5);
  R.impl['City.stateBuildingIdle'] = () => 0;
  assert.equal(Ticker.stateIdle(G, 5, infoAt(G, 5, 0)), 0);
  assert.deepEqual(R.named('City.stateBuildingIdle'), [['City.stateBuildingIdle', 5]]);

  const a = makeObject(G, 200, BUILDING);
  Ticker.pushIdle(G, 200);
  assert.equal(Ticker.stateIdle(G, 200, infoAt(G, 200, 0)), 0);
  assert.deepEqual(R.named('Anim.startAnim'), [['Anim.startAnim', a + O.ANIM0, 'STAND', 0]]);
  assert.deepEqual(stack(G, 200), [1, 3]);
  assert.equal(i16(G.gs, infoAt(G, 200, 1)), 7);

  R.reset();
  makeObject(G, 201, BUILDING);
  w8(G.gs, objAddr(201) + O.PICKUP, 2);
  Ticker.pushIdle(G, 201);
  Ticker.stateIdle(G, 201, infoAt(G, 201, 0));
  assert.deepEqual(R.named('Renat.pickup'), [['Renat.pickup', 201, 1]]);
});

// ---- nudging --------------------------------------------------------------------------------------------

test('chooseStep tries the perpendicular directions first and honours occupancy', () => {
  const G = newGame();
  const blocked = new Set(['6,5']); // east of (5,5)
  R.impl['Grid.cellGround'] = (_G, x, z) => (blocked.has(`${x},${z}`) ? 300 : 0x3ff);
  R.impl['Grid.passable'] = () => true;
  // nudge direction 1 (north): nudgeB[1] = 1, offsets +2 -> dir 4 (east), -2 -> dir 3 (west)
  assert.equal(Ticker.chooseStep(G, 5, 5, 1, 0), 3);
  blocked.add('4,5');
  assert.equal(Ticker.chooseStep(G, 5, 5, 1, 0), 2, 'then the +1 offset: north-east (6,4)');
  // diagonal (6,4): accepted while ONE of (6,5) / (5,4) is passable, skipped when both are not
  R.impl['Grid.passable'] = (_G, x, z) => !(x === 6 && z === 5);
  assert.equal(Ticker.chooseStep(G, 5, 5, 1, 0), 2, 'one passable orthogonal neighbour suffices');
  R.impl['Grid.passable'] = (_G, x, z) => !(x === 6 && z === 5) && !(x === 5 && z === 4);
  assert.equal(Ticker.chooseStep(G, 5, 5, 1, 0), 0, 'both blocked: falls through to the -1 offset (4,4)');
  R.impl['Grid.passable'] = () => false;
  assert.equal(Ticker.chooseStep(G, 5, 5, 1, 0), -1);
  assert.equal(Ticker.chooseStepNearTarget(G, 5, 5, 1, 0), -1);
});

test('chooseStepRandom consumes one rand() per candidate and accepts LIFE == 1 occupants', () => {
  const G = newGame();
  R.impl['Grid.passable'] = () => true;
  R.impl['Grid.cellGround'] = () => 0x3ff;
  const before = G.randIndex;
  const d = Ticker.chooseStepRandom(G, 5, 5, 1, 0);
  assert.ok(d >= 0 && d < 8);
  assert.equal(G.randIndex, (before + 1) & 0xff);

  makeObject(G, 400, MOBILE);
  R.impl['Grid.cellGround'] = () => 400; // every cell occupied by a live unit
  assert.notEqual(Ticker.chooseStepRandom(G, 5, 5, 1, 0), -1);
  w8(G.gs, objAddr(400) + O.LIFE, 2);
  const b2 = G.randIndex;
  assert.equal(Ticker.chooseStepRandom(G, 5, 5, 1, 0), -1);
  assert.equal(G.randIndex, (b2 + 7) & 0xff, 'seven rand() calls for seven rejected candidates');
});

test('nudgeWalk sets the destination to the chosen tile centre and starts begin_move(0)', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE, 5, 5);
  w8(G.gs, a + O.NUDGE, 1);
  R.impl['Grid.cellGround'] = () => 0x3ff;
  R.impl['Grid.passable'] = () => true;
  assert.equal(Ticker.nudgeWalk(G, 200, 1), 1);
  assert.equal(u8(G.gs, a + O.NUDGE), 0xff);
  assert.equal(i16(G.gs, a + O.DEST_X), (6 << 8) + 0x80);
  assert.equal(i16(G.gs, a + O.DEST_Z), (5 << 8) + 0x80);
  assert.deepEqual(R.named('Move.beginMove'), [['Move.beginMove', 200, 0, 0]]);

  R.reset();
  w8(G.gs, a + O.NUDGE, 1);
  R.impl['Grid.passable'] = () => false;
  assert.equal(Ticker.nudgeWalk(G, 200, 1), 1);
  assert.equal(u8(G.gs, a + O.NUDGE), 0xff, 'nudge cleared even when no step fits');
  assert.deepEqual(R.named('Move.beginMove'), []);
});

// ---- deploy order ----------------------------------------------------------------------------------------

test('orderDeploy refuses near a city origin, then plays DEPLOY and pushes state 0xD {50}', () => {
  const G = newGame();
  const a = makeObject(G, 200, 0x2b, 10, 10);
  w32(G.gs, playerAddr(3) + P.CITY_X, 12);
  w32(G.gs, playerAddr(3) + P.CITY_Z, 14);
  assert.equal(Ticker.canDeploy(G, 200), 0);
  Ticker.orderDeploy(G, 200);
  assert.deepEqual(stack(G, 200), [1]);

  makeObject(G, 200, 0x2b, 10, 30);
  R.impl['Grid.cellSecondary'] = () => 0x3ff;
  Ticker.orderDeploy(G, 200);
  assert.deepEqual(stack(G, 200), [0x0d]);
  assert.equal(i16(G.gs, infoAt(G, 200, 0)), 0x32);
  assert.deepEqual(R.named('Anim.startAnim'), [['Anim.startAnim', a + O.ANIM0, 'DEPLOY', 1]]);

  makeObject(G, 200, 0x2b, 10, 30);
  R.impl['Grid.cellSecondary'] = () => 45; // mine already there
  Ticker.orderDeploy(G, 200);
  assert.deepEqual(stack(G, 200), [1]);
});

// ---- dispatcher ------------------------------------------------------------------------------------------

test('dispatchObject: charge regeneration, countdowns, blood rand(), then the state loop', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  w8(G.gs, a + O.CHARGE, 250);
  w8(G.gs, a + O.BERSERK, 3);
  w8(G.gs, a + O.LINK_COUNT, 1);
  w8(G.gs, a + O.ATTACKED, (2 << 5) | 3);
  w8(G.gs, a + O.DAMAGED, 1);
  w8(G.gs, a + O.HEADING, 0x47);
  w32(G.gs, GS.DN_COUNTER, 32);
  Ticker.sleepState(G, 200, 5);
  R.impl['Anim.isPlaying'] = () => false;
  const r = consts.rand.values[(G.randIndex + 1) & 0xff];
  Ticker.dispatchObject(G, 200);
  assert.equal(u8(G.gs, a + O.CHARGE), 254, '250 + 4');
  assert.equal(u8(G.gs, a + O.BERSERK), 2);
  assert.equal(u8(G.gs, a + O.LINK_COUNT), 0);
  assert.equal(u8(G.gs, a + O.ATTACKED), (2 << 5) | 2);
  assert.equal(u8(G.gs, a + O.DAMAGED), 0);
  const facing2 = ((0x47 + 8) & 0xf0) >> 3; // 0x4F -> 0x40 >> 3 = 8
  assert.deepEqual(R.named('Anim.isPlaying'), [['Anim.isPlaying', a + O.ANIM1, facing2]]);
  assert.deepEqual(R.named('Anim.startAnim'), [['Anim.startAnim', a + O.ANIM1, ['BLOODA', 'BLOODB', 'BLOODC'][r % 3], 1]]);
  assert.deepEqual(R.named('Anim.advanceSlots'), [['Anim.advanceSlots', 200, facing2]]);
  assert.equal(i16(G.gs, infoAt(G, 200, 0)), 4, 'sleep state ran once');

  // saturation and the 16-tick guard
  w8(G.gs, a + O.CHARGE, 253);
  w32(G.gs, GS.DN_COUNTER, 64);
  Ticker.dispatchObject(G, 200);
  assert.equal(u8(G.gs, a + O.CHARGE), 255);
  w32(G.gs, GS.DN_COUNTER, 65);
  Ticker.dispatchObject(G, 200);
  assert.equal(u8(G.gs, a + O.BERSERK), 1, 'unchanged at counter 65');
  assert.deepEqual(G.asserts, []);
});

test('dispatchObject: flyers of a player without HQ lose 5 HP in game types 1/2 and die at 0', () => {
  const G = newGame();
  G.scenario = { gameType: 2 };
  const a = makeObject(G, 200, FLYER, 5, 5, 0);
  w32(G.gs, a + O.HP, 7);
  w32(G.gs, GS.DN_COUNTER, 16);
  Ticker.pushState(G, 200, 0, 0); // no-op state: the stubbed object_die leaves the object as it is
  R.impl['Anim.isPlaying'] = () => true;
  Ticker.dispatchObject(G, 200);
  assert.equal(i32(G.gs, a + O.HP), 2);
  Ticker.dispatchObject(G, 200);
  assert.equal(i32(G.gs, a + O.HP), 0);
  assert.deepEqual(R.named('Combat.objectDie'), [['Combat.objectDie', 200]]);
  assert.deepEqual(R.named('Grid.removeObject'), [['Grid.removeObject', 200]]);

  R.reset();
  R.impl['Anim.isPlaying'] = () => true;
  w32(G.gs, playerAddr(0) + P.SLOT_HP, 1000); // HQ alive
  w32(G.gs, a + O.HP, 7);
  Ticker.dispatchObject(G, 200);
  assert.equal(i32(G.gs, a + O.HP), 7);
  G.scenario = { gameType: 0 };
  w32(G.gs, playerAddr(0) + P.SLOT_HP, 0);
  Ticker.dispatchObject(G, 200);
  assert.equal(i32(G.gs, a + O.HP), 7, 'campaign game type: no drain');
});

test('dispatchObject: a handler returning 1 reruns the loop; 100 iterations trip the loop trap', () => {
  const G = newGame();
  const a = makeObject(G, 200, MOBILE);
  w32(G.gs, GS.DN_COUNTER, 1);
  R.impl['Anim.isPlaying'] = () => true;
  R.impl['Combat.findTargetInRange'] = () => -1;
  R.impl['Combat.findTarget'] = () => -1;
  // marker state 2 on top: resets to idle (returns 1), idle then fidgets (returns 0)
  Ticker.pushIdle(G, 200);
  Ticker.pushState(G, 200, 2, 0);
  seekRand(G, (v) => (v & 0x0f) !== 0);
  Ticker.dispatchObject(G, 200);
  assert.deepEqual(stack(G, 200), [1, 3]);
  assert.deepEqual(G.asserts, []);

  makeObject(G, 201, MOBILE);
  Ticker.pushState(G, 201, 0x15, 0); // Renat.state15 stub
  let n = 0;
  R.impl['Renat.state15'] = () => {
    n++;
    return 1;
  };
  Ticker.dispatchObject(G, 201);
  assert.equal(n, 100);
  assert.deepEqual(
    G.asserts.map((x) => x.msg),
    ['loop_trap<100'],
  );

  G.asserts.length = 0;
  w8(G.gs, a + O.STACK + 2 * gsSp(G, 200), 40); // corrupt the top state id
  Ticker.dispatchObject(G, 200);
  assert.match(G.asserts[0].msg, /Whoa batman, sprite 40 do unit out of range 200/);
});

const gsSp = (G, obj) => G.gs.readInt8(objAddr(obj) + O.SP);

test('STATE_TABLE and ORDER_TABLE have the original shapes', () => {
  assert.equal(Ticker.STATE_TABLE.length, 23);
  assert.equal(Ticker.ORDER_TABLE.length, 22);
  assert.equal(Ticker.STATE_TABLE[1], Ticker.stateIdle);
  assert.equal(Ticker.STATE_TABLE[3], Ticker.stateSleep);
  assert.equal(Ticker.STATE_TABLE[0x0b], Ticker.stateCooldown);
  assert.equal(Ticker.ORDER_TABLE[1], Ticker.pushIdle);
  assert.equal(Ticker.ORDER_TABLE[0x0e], Ticker.orderAttack);
  assert.equal(Ticker.ORDER_TABLE[0x0d], Ticker.orderDeploy);
  for (const k of [0, 3, 4, 5, 6, 8, 10, 11, 12, 15, 16, 17, 19, 20, 21]) assert.equal(Ticker.ORDER_TABLE[k], Ticker.orderNop);
});

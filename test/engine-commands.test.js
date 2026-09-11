// Tests of src/engine/commands.js and src/engine/city.js on a bare Game with hand-made objects.
//
// The other engine modules (ticker, anim, grid, scenario, combat, missile, renat, ai) are written by
// other agents and may not exist yet; ESM fails at link time on a missing import, so a module
// resolve hook (node:module register, Node >= 20.6) serves small recording stubs for those eight
// siblings when imported from src/engine/. The stubs call `globalThis.__engineStubImpl[name]` when
// a behaviour is needed (state stack, grid cells, object creation) and log every call.

import { register } from 'node:module';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const STUBS = {
  ticker: ['pushState', 'popState', 'resetAndDispatchOrder', 'dispatchObject', 'sleepState', 'setCooldown', 'pushIdle'],
  anim: ['startAnim', 'advanceAnims'],
  grid: ['cellGround', 'setCellGround', 'cellAir', 'setCellAir', 'cellLoad', 'removeObject', 'clearSeen', 'updateVision'],
  scenario: ['startGame', 'createObject', 'initObject', 'unitCap'],
  combat: ['objectDie', 'applyDamage', 'fireWeapon'],
  missile: ['missilesTick'],
  renat: ['generatorsTick', 'triggersTick'],
  ai: ['aiTurn'],
};

function stubModuleUrl(mod, names) {
  const src = `
const calls = (globalThis.__engineStubCalls ??= []);
const stub = (name) => (...args) => {
  calls.push({ name, args: args.slice(1) });
  const f = globalThis.__engineStubImpl && globalThis.__engineStubImpl[name];
  return f ? f(...args) : undefined;
};
${names.map((n) => `export const ${n} = stub('${mod}.${n}');`).join('\n')}
export const STATE_TABLE = [];
export const ORDER_TABLE = [];
`;
  return 'data:text/javascript,' + encodeURIComponent(src);
}

const urls = Object.fromEntries(Object.entries(STUBS).map(([m, names]) => [m, stubModuleUrl(m, names)]));
const hooks = `
const urls = ${JSON.stringify(urls)};
export async function resolve(specifier, context, next) {
  const m = /^\\.\\/(${Object.keys(STUBS).join('|')})\\.js$/.exec(specifier);
  if (m && context.parentURL && context.parentURL.includes('/src/engine/')) return { url: urls[m[1]], shortCircuit: true };
  return next(specifier, context);
}
`;
register('data:text/javascript,' + encodeURIComponent(hooks));

const { Game } = await import('../src/engine/engine.js');
const Commands = await import('../src/engine/commands.js');
const City = await import('../src/engine/city.js');
const mem = await import('../src/engine/mem.js');
const { build, T } = await import('../src/commands.js');
const { GS, O, P, u8, i8, i16, i32, w8, w16, w32, objAddr, playerAddr } = mem;

const consts = JSON.parse(fs.readFileSync(new URL('../data/dc16-tables.json', import.meta.url), 'utf8'));

// ---- balance-table stub --------------------------------------------------------------------------

function makeTables() {
  const types = [];
  for (let i = 0; i < 130; i++) {
    types.push({
      race: i >= 8 && i < 16 || (i >= 28 && i < 35) ? 1 : 0,
      hp: 100 + i,
      fly: 0,
      flyer: 0,
      prodClass: 0,
      deployCode: 0,
      hasSpecial: 0,
      weaponLevel: [0, 0, 0, 0, 0, 0, 0, 0],
      armourLevel: [0, 0, 0, 0, 0, 0, 0, 0],
      stand: `STAND${i}`,
      scrch: `SCRCH${i}`,
      burn: `BURN${i}`,
      build: null,
    });
  }
  types[1].hasSpecial = 1;
  types[1].deployCode = 2;
  types[2].prodClass = 1;
  types[2].build = 'BUILD2';
  types[17].build = 'BUILD17'; // barracks has a BUILD sprite -> drop pod sequence
  types[0x5c].stand = 'PODSTAND';
  const depend = [];
  const item = (i, rec) => {
    depend[i] = { active: 1, status: 1, button: 0, deps: [-1], ...rec };
  };
  item(0, { cost: 2000, kind: 0, a: 0, b: 0, c: 0 });
  item(1, { cost: 1000, kind: 0, a: 1, b: 0, c: 0, deps: [0, -1] });
  item(3, { cost: 2000, kind: 0, a: 2, b: 0, c: 0, deps: [2, 1, -1] });
  item(2, { cost: 2000, kind: 0, a: 3, b: 0, c: 0, deps: [0, -1] });
  item(7, { cost: 1500, kind: 1, a: 6, deps: [0, -1] });
  item(9, { cost: 350, kind: 1, a: 0, deps: [1, -1] });
  item(11, { cost: 600, kind: 1, a: 2, deps: [3, 2, -1] });
  item(30, { cost: 1000, kind: 2, a: 8, b: 0, c: 1, deps: [-1] });
  return { types, depend, weapons: [], booms: [], mbullet: null, unitid: null };
}

// ---- Game with stub behaviours -------------------------------------------------------------------

function makeGame() {
  const G = new Game(makeTables(), consts);

  const w = 64;
  const h = 64;
  G.map = { w, h, ground: new Int32Array(w * h).fill(0x3ff), air: new Int16Array(w * h).fill(0x3ff) };
  w32(G.gs, GS.LOCAL_PLAYER, -1); // the server is nobody
  w32(G.gs, GS.MAX_OBJ, 0);
  for (let obj = 0; obj < 800; obj++) {
    w16(G.gs, GS.OBJ_ALLOC + obj * 2, -1);
    w8(G.gs, objAddr(obj) + O.SP, 0xff);
  }
  (globalThis.__engineStubCalls ??= []).length = 0; // the stubs hold a reference to this array
  globalThis.__engineStubImpl = {
    'ticker.pushState': (g, obj, state, n) => {
      const a = objAddr(obj);
      const sp = i8(g.gs, a + O.SP) + 1;
      w8(g.gs, a + O.SP, sp);
      w8(g.gs, a + O.STACK + 2 * sp, state);
      const off = sp * 8;
      w8(g.gs, a + O.STACK + 1 + 2 * sp, off);
      const info = a + O.INFO + 2 * off;
      g.gs.fill(0, info, info + 2 * n);
      return info;
    },
    'ticker.popState': (g, obj) => w8(g.gs, objAddr(obj) + O.SP, i8(g.gs, objAddr(obj) + O.SP) - 1),
    'ticker.resetAndDispatchOrder': (g, obj) => {
      w8(g.gs, objAddr(obj) + O.SP, 0xff);
      globalThis.__engineStubImpl['ticker.pushState'](g, obj, 1, 3);
    },
    // 0x42626C: slot = { sprite (4 bytes), frame, x, mode/status }
    'anim.startAnim': (g, slotAddr, sprite, mode) => {
      const id = typeof sprite === 'string' ? sprite.split('').reduce((s, c) => s * 31 + c.charCodeAt(0), 7) | 0 : 0;
      if (i32(g.gs, slotAddr) === id && i8(g.gs, slotAddr + 6) === mode) return;
      w8(g.gs, slotAddr + 4, 0);
      w8(g.gs, slotAddr + 5, 0);
      w8(g.gs, slotAddr + 6, mode);
      w32(g.gs, slotAddr, id);
    },
    'grid.cellGround': (g, x, z) => g.map.ground[z * g.map.w + x],
    'grid.setCellGround': (g, x, z, v) => {
      g.map.ground[z * g.map.w + x] = v;
    },
    'grid.cellAir': (g, x, z) => g.map.air[z * g.map.w + x],
    'grid.setCellAir': (g, x, z, v) => {
      g.map.air[z * g.map.w + x] = v;
    },
    'grid.cellLoad': () => 0x80000000 | 0,
    'scenario.createObject': (g, x, z, type, team, idx) => {
      let obj = idx;
      if (obj === -1) {
        obj = 152;
        while (i16(g.gs, GS.OBJ_ALLOC + obj * 2) !== -1) obj++;
      }
      const a = objAddr(obj);
      w16(g.gs, a + O.X, x << 8);
      w16(g.gs, a + O.Z, z << 8);
      w8(g.gs, a + O.TYPE, type);
      w8(g.gs, a + O.TEAM, team);
      w8(g.gs, a + O.LIFE, 1);
      w8(g.gs, a + O.SP, 0xff);
      w16(g.gs, GS.OBJ_ALLOC + obj * 2, obj);
      if (obj > i32(g.gs, GS.MAX_OBJ)) w32(g.gs, GS.MAX_OBJ, obj);
      return obj;
    },
  };
  return G;
}

function addUnit(G, obj, type, team, x, z) {
  const a = objAddr(obj);
  w8(G.gs, a + O.TYPE, type);
  w8(G.gs, a + O.TEAM, team);
  w8(G.gs, a + O.LIFE, 1);
  w16(G.gs, a + O.X, x << 8);
  w16(G.gs, a + O.Z, z << 8);
  w16(G.gs, GS.OBJ_ALLOC + obj * 2, obj);
}

const fired = (G) => G.asserts.map((a) => a.msg);
const calls = (prefix) => globalThis.__engineStubCalls.filter((c) => c.name.startsWith(prefix));
const raw = (...bytes) => Buffer.from(bytes);
// engine.js's Game.applyCommand does not return the handler result (integrator note); call the module
const apply = (G, cmd) => Commands.applyCommand(G, cmd);

// ---- commands ------------------------------------------------------------------------------------

test('0x14 select / 0x15 deselect / 0x16 order selected (0x12 gated by +0x10C)', () => {
  const G = makeGame();
  addUnit(G, 152, 1, 2, 5, 5);
  addUnit(G, 153, 0, 2, 6, 5);
  addUnit(G, 154, 0, 3, 7, 5);
  w8(G.gs, objAddr(154) + O.SELECT, 0x04); // stale selection of player 2 on someone else's unit
  assert.equal(apply(G, build.select(2, [152, 153])), 0);
  assert.equal(u8(G.gs, objAddr(152) + O.SELECT), 0x04);
  assert.equal(u8(G.gs, objAddr(153) + O.SELECT), 0x04);
  assert.equal(u8(G.gs, objAddr(154) + O.SELECT), 0x00);
  assert.equal(apply(G, build.orderSelected(2, 7)), 0);
  assert.equal(u8(G.gs, objAddr(152) + O.PENDING), 1);
  assert.equal(u8(G.gs, objAddr(152) + O.ORDER), 7);
  assert.equal(u8(G.gs, objAddr(153) + O.ORDER), 7);
  assert.equal(u8(G.gs, objAddr(154) + O.PENDING), 0);
  assert.equal(apply(G, build.orderSelected(2, 0x12)), 0);
  assert.equal(u8(G.gs, objAddr(152) + O.ORDER), 0x12, 'type 1 has the targeted special');
  assert.equal(u8(G.gs, objAddr(153) + O.ORDER), 7, 'type 0 has not');
  assert.equal(apply(G, build.deselect(2)), 0);
  assert.equal(u8(G.gs, objAddr(152) + O.SELECT), 0);
  assert.equal(u8(G.gs, objAddr(153) + O.SELECT), 0);
  assert.deepEqual(fired(G), []);
});

test('0x19 waypoints selected, 0x1B move-to selected, 0x1A deploy selected, 0x17/0x18 targets', () => {
  const G = makeGame();
  addUnit(G, 152, 1, 0, 5, 5);
  addUnit(G, 153, 0, 0, 6, 5);
  apply(G, build.select(0, [152, 153]));
  assert.equal(apply(G, build.waypointsSelected(0, [[10, 20], [30, -40]])), 0);
  for (const obj of [152, 153]) {
    const a = objAddr(obj);
    assert.equal(u8(G.gs, a + O.WP_COUNT), 2);
    assert.equal(i16(G.gs, a + O.WAYPOINTS), 10);
    assert.equal(i16(G.gs, a + O.WAYPOINTS + 2), 20);
    assert.equal(i16(G.gs, a + O.WAYPOINTS + 4), 30);
    assert.equal(i16(G.gs, a + O.WAYPOINTS + 6), -40);
  }
  assert.equal(apply(G, build.moveToSelected(0, 100, 200)), 0);
  assert.equal(u8(G.gs, objAddr(152) + O.ORDER), 0x12);
  assert.equal(u8(G.gs, objAddr(152) + O.WP_COUNT), 1);
  assert.equal(i16(G.gs, objAddr(152) + O.WAYPOINTS), 100);
  assert.equal(i16(G.gs, objAddr(152) + O.WAYPOINTS + 2), 200);
  assert.equal(u8(G.gs, objAddr(153) + O.PENDING), 0, 'type 0 cannot move-to');
  assert.equal(u8(G.gs, objAddr(153) + O.WP_COUNT), 2);
  assert.equal(apply(G, raw(T.ORDER0D_SEL, 0)), 0);
  assert.equal(u8(G.gs, objAddr(152) + O.ORDER), 0x0d);
  assert.equal(u8(G.gs, objAddr(153) + O.ORDER), 0);
  // 0x17 target position, 0x18 target object (+0x32)
  assert.equal(apply(G, Buffer.concat([raw(T.TARGET_POS_SEL, 0), Buffer.from([0x34, 0x12, 0xff, 0xff])])), 0);
  assert.equal(i16(G.gs, objAddr(153) + O.DEST_X), 0x1234);
  assert.equal(i16(G.gs, objAddr(153) + O.DEST_Z), -1);
  assert.equal(apply(G, raw(T.TARGET_OBJ_SEL, 0, 0x99, 0x00)), 0);
  assert.equal(i16(G.gs, objAddr(152) + 0x32), 0x99);
  assert.equal(i16(G.gs, objAddr(152) + O.DEST_Z), -1, '+0x30 untouched by 0x18');
  assert.deepEqual(fired(G), []);
});

test('0x05 order, 0x06 target position, 0x0B target object, 0x07 waypoints by object list', () => {
  const G = makeGame();
  addUnit(G, 160, 0, 1, 1, 1);
  assert.equal(apply(G, raw(T.ORDER, 160, 0, 0x0e)), 0);
  assert.equal(u8(G.gs, objAddr(160) + O.PENDING), 1);
  assert.equal(u8(G.gs, objAddr(160) + O.ORDER), 0x0e);
  assert.equal(apply(G, raw(T.TARGET_POS, 160, 0, 0x10, 0x00, 0x20, 0x00)), 0);
  assert.equal(i16(G.gs, objAddr(160) + O.DEST_X), 0x10);
  assert.equal(i16(G.gs, objAddr(160) + O.DEST_Z), 0x20);
  assert.equal(apply(G, raw(T.TARGET_OBJ, 160, 0, 0x2c, 0x01)), 0);
  assert.equal(i16(G.gs, objAddr(160) + 0x32), 300);
  // 0x07: n=1, count=2, (7,8), objects 160 and 161
  assert.equal(apply(G, raw(T.WAYPOINTS_OBJ, 1, 2, 0, 7, 0, 8, 0, 160, 0, 161, 0)), 0);
  assert.equal(u8(G.gs, objAddr(161) + O.WP_COUNT), 1);
  assert.equal(i16(G.gs, objAddr(161) + O.WAYPOINTS + 2), 8);
  assert.deepEqual(fired(G), []);
});

test('0x0C setting: (which, type, level, player), 1000*level booked, refund on a repeat', () => {
  const G = makeGame();
  const pa = playerAddr(3);
  w32(G.gs, pa + P.MONEY, 5000);
  assert.equal(apply(G, raw(T.SETTING, 0, 5, 1, 3)), 0);
  assert.equal(G.tables.types[5].weaponLevel[3], 1);
  assert.equal(i32(G.gs, pa + P.SPENT), 1000);
  assert.equal(i32(G.gs, pa + P.MONEY), 5000);
  assert.equal(apply(G, raw(T.SETTING, 0, 5, 1, 3)), 0);
  assert.equal(i32(G.gs, pa + P.MONEY), 6000, 'same level again refunds');
  assert.equal(apply(G, raw(T.SETTING, 1, 5, 2, 3)), 0);
  assert.equal(G.tables.types[5].armourLevel[3], 2);
  assert.equal(i32(G.gs, pa + P.SPENT), 3000);
  assert.equal(apply(G, raw(T.SETTING, 2, 5, 2, 3)), 0, 'which 2: nothing');
  assert.equal(i32(G.gs, pa + P.SPENT), 3000);
  assert.deepEqual(fired(G), []);
});

test('0x0D diplomacy sets one direction of the matrix', () => {
  const G = makeGame();
  assert.equal(apply(G, raw(T.DIPLOMACY, 1, 2, 0, 1)), 0);
  assert.equal(G.diplo[0][1], 1 << 2);
  assert.equal(G.diplo[0][2], 0);
  assert.equal(G.diploGet(0, 1, 2), 0, 'needs both directions');
  apply(G, raw(T.DIPLOMACY, 2, 1, 0, 5));
  assert.ok(G.diploGet(0, 1, 2));
  apply(G, raw(T.DIPLOMACY, 1, 2, 1, 1));
  assert.equal(G.diplo[1][1], 1 << 2);
  apply(G, raw(T.DIPLOMACY, 1, 2, 0, 0));
  assert.equal(G.diplo[0][1], 0);
  assert.deepEqual(fired(G), []);
  apply(G, raw(T.DIPLOMACY, 9, 2, 0, 1));
  assert.equal(G.asserts.length, 1);
});

test('0x04 cheat flags, 0x0E chat cheats, 0x0F bonus, speeds', () => {
  const G = makeGame();
  assert.equal(apply(G, build.cheat(0, 1)), 0);
  assert.equal(u8(G.gs, GS.FLAGS + 1), 1);
  assert.equal(apply(G, build.cheat(0, 1)), 0);
  assert.equal(u8(G.gs, GS.FLAGS + 1), 0);
  assert.equal(apply(G, build.cheat(0, 7)), -1);
  assert.equal(apply(G, build.chat(0, 0xff, 'Bob: we need equipment')), 0);
  for (let p = 0; p < 8; p++) assert.equal(i32(G.gs, playerAddr(p) + P.MONEY), 10000);
  assert.equal(apply(G, build.chat(0, 0x00, "Bob: I'm fighting for that equipment")), 0, 'nobody addressed');
  assert.equal(i32(G.gs, playerAddr(0) + P.MONEY), 10000);
  assert.equal(apply(G, build.chat(0, 0x02, 'Bob: slag net')), 0);
  assert.equal(u8(G.gs, GS.FLAGS), 1);
  assert.equal(apply(G, build.chat(9, 0xff, 'x: y')), -1, 'from out of range');
  assert.equal(apply(G, raw(T.CHAT, 0, 0xff, 0x41, 0x42)), -1, 'unterminated');
  assert.equal(apply(G, build.bonus(3)), 0);
  assert.equal(i32(G.gs, playerAddr(3) + P.MONEY), 11000);
  w32(G.gs, GS.LOCAL_PLAYER, 2);
  assert.equal(apply(G, build.bonus(3)), 0);
  assert.equal(i32(G.gs, playerAddr(3) + P.MONEY), 11000, 'as player 2, the bonus for 3 is not mine');
  assert.equal(apply(G, build.tickSpeed(44)), 0);
  assert.equal(i32(G.gs, GS.TICK_MS), 44);
  assert.equal(apply(G, build.tickSpeed(0)), -1);
  assert.equal(apply(G, build.tickDesSpeed(66)), 0);
  assert.equal(i32(G.gs, GS.DESIRED_MS), 66);
  assert.equal(apply(G, build.tickMaxSpeed(5, 120)), 0);
  assert.equal(i32(G.gs, 0x974 + 5 * 4), 120);
  assert.equal(apply(G, raw(0x00)), -1);
  assert.equal(apply(G, raw(0x1c)), -1);
  assert.equal(apply(G, build.tick(3)), 0, "TICK is the integrator's no-op here");
});

test('0x10 disconnect: net slot -> player, AI type 3, money -= spent, max speed 0', () => {
  const G = makeGame();
  const pa = playerAddr(4);
  G.gs.write('Zed\0', pa, 'latin1');
  w32(G.gs, pa + P.NET_ID, 6);
  w32(G.gs, pa + P.MONEY, 2000);
  w32(G.gs, pa + P.SPENT, 500);
  w32(G.gs, 0x974 + 4 * 4, 77);
  assert.equal(apply(G, build.disconnect(6)), 0);
  assert.equal(i32(G.gs, pa + P.AI_TYPE), 3);
  assert.equal(i32(G.gs, pa + P.MONEY), 1500);
  assert.equal(i32(G.gs, 0x974 + 4 * 4), 0);
  assert.deepEqual(fired(G), []);
  assert.equal(apply(G, build.disconnect(7)), -1);
  assert.deepEqual(fired(G), ['lost_player!=-1']);
});

test('0x03 create object writes the record and the allocation table', () => {
  const G = makeGame();
  const cmd = Buffer.concat([raw(T.CREATE), Buffer.from([0xc8, 0x00, 10, 0, 3, 0, 20, 0, 17, 2, 64, 1, 50])]);
  assert.equal(apply(G, cmd), 0);
  const a = objAddr(200);
  assert.equal(i16(G.gs, a + O.X), 10 << 5);
  assert.equal(i16(G.gs, a + O.HEIGHT), 3 << 5);
  assert.equal(i16(G.gs, a + O.Z), 20 << 5);
  assert.equal(i16(G.gs, a + O.DEST_X), 10 << 5);
  assert.equal(i16(G.gs, a + O.DEST_Z), 3 << 5);
  assert.equal(u8(G.gs, a + O.TYPE), 17);
  assert.equal(u8(G.gs, a + O.TEAM), 2);
  assert.equal(u8(G.gs, a + O.HEADING), 64);
  assert.equal(u8(G.gs, a + O.LIFE), 1);
  assert.equal(i32(G.gs, a + O.HP), 50);
  assert.equal(u8(G.gs, a + O.NUDGE), 0xff);
  assert.equal(u8(G.gs, a + O.ORDER), 0xff);
  assert.equal(i16(G.gs, GS.OBJ_ALLOC + 200 * 2), 200);
  assert.equal(i32(G.gs, GS.MAX_OBJ), 201);
});

// ---- city ----------------------------------------------------------------------------------------

test('0x0A build units appends to the class queue and books the cost', () => {
  const G = makeGame();
  const pa = playerAddr(1);
  assert.equal(apply(G, raw(T.BUILD, 0, 1, 3)), 0);
  assert.equal(i16(G.gs, pa + P.QUEUE_LEN), 3);
  assert.equal(u8(G.gs, pa + P.QUEUE + 2), 0);
  assert.equal(i32(G.gs, pa + P.SPENT), 3 * 350);
  assert.equal(apply(G, raw(T.BUILD, 2, 1, 1)), 0, 'type 2 is class 1');
  assert.equal(i16(G.gs, pa + P.QUEUE_LEN + 2), 1);
  assert.equal(u8(G.gs, pa + P.QUEUE + 800), 2);
  assert.equal(i32(G.gs, pa + P.SPENT), 3 * 350 + 600);
});

test('0x09 build building: slot fields, build_slot placement, footprint, production reset, refund', () => {
  const G = makeGame();
  const pa = playerAddr(0);
  w32(G.gs, pa + P.CITY_X, 20);
  w32(G.gs, pa + P.CITY_Z, 20);
  assert.equal(apply(G, raw(T.RESEARCH, 1, 0, 0)), 0);
  assert.equal(i32(G.gs, pa + P.SLOT_HP + 4), 100 + 17, 'hp of unitdef[0][1] = barracks 17');
  assert.equal(i32(G.gs, pa + P.SLOT_LEVEL + 4), 0);
  assert.equal(i32(G.gs, pa + P.SPENT), 1000);
  const a = objAddr(1);
  assert.equal(u8(G.gs, a + O.TYPE), 17);
  assert.equal(u8(G.gs, a + O.TEAM), 0);
  assert.equal(u8(G.gs, a + O.LIFE), 1);
  assert.equal(i32(G.gs, a + O.HP), 117);
  assert.equal(i16(G.gs, a + O.X), 20 << 8);
  assert.equal(i16(G.gs, a + O.Z), 20 << 8);
  assert.equal(i16(G.gs, GS.OBJ_ALLOC + 2), 1);
  assert.equal(i32(G.gs, GS.MAX_OBJ), 1);
  // footprint of slot 1: (-1,-1) (0,-1) (-1,-2) (0,-2) relative to the city origin
  const cell = (x, z) => G.map.ground[z * 64 + x];
  assert.equal(cell(19, 19) & 0x3ff, 1);
  assert.equal(cell(20, 19) & 0x3ff, 1);
  assert.equal(cell(19, 18) & 0x3ff, 1);
  assert.equal(cell(20, 18) & 0x3ff, 1);
  assert.equal(cell(20, 20), 0x3ff);
  assert.equal(u8(G.gs, pa + P.PROD_READY), 1, 'barracks feeds class 0');
  assert.equal(u8(G.gs, pa + P.SLOT_BLOCKED + 1), 1, 'under construction');
  assert.equal(u8(G.gs, a + O.STACK + 2), 0x13, 'construction state pushed above idle');
  assert.equal(u8(G.gs, a + O.SP), 1);
  // same command again: already built at that level -> refund
  w32(G.gs, pa + P.MONEY, 0);
  assert.equal(apply(G, raw(T.RESEARCH, 1, 0, 0)), 0);
  assert.equal(i32(G.gs, pa + P.MONEY), 1000);
  assert.equal(i32(G.gs, pa + P.SPENT), 1000);
  assert.deepEqual(fired(G), []);
});

test('construction sequence: state 0x13 -> drop pod (0x16, 0x14) -> BUILD animation -> pod leaves -> idle', () => {
  const G = makeGame();
  const pa = playerAddr(0);
  w32(G.gs, pa + P.CITY_X, 20);
  w32(G.gs, pa + P.CITY_Z, 20);
  w32(G.gs, GS.TICK, 10);
  apply(G, raw(T.RESEARCH, 1, 0, 0));
  const bld = objAddr(1);
  const info = bld + O.INFO + 2 * u8(G.gs, bld + O.STACK + 1 + 2 * 1);
  assert.equal(City.stateConstruct(G, 1, info), 0);
  assert.equal(i16(G.gs, info), 1, 'pod descending');
  assert.equal(u8(G.gs, pa + 0xe16), 1, 'player is constructing');
  const pod = objAddr(6);
  assert.equal(u8(G.gs, pod + O.TYPE), 0x5c);
  assert.equal(u8(G.gs, pod + O.TEAM), 8);
  assert.equal(i16(G.gs, pod + O.X), 20 << 8, 'pod x = building x + landing offset (0)');
  assert.equal(i16(G.gs, pod + O.Z), (20 << 8) + (-32 << 3), 'pod z = building z + landing offset');
  assert.equal(i16(G.gs, pod + O.HEIGHT), 0x384 + 6 * 50 * 50 / 2, 'height = h0 + vz*n*n/2');
  assert.equal(u8(G.gs, pod + O.STACK + 2), 0x16, 'flight state on top of the landing state');
  const podInfo = pod + O.INFO + 2 * u8(G.gs, pod + O.STACK + 1 + 2);
  const landInfo = pod + O.INFO + 2 * u8(G.gs, pod + O.STACK + 1);
  assert.equal(i16(G.gs, landInfo), 1, 'landing state knows the building');
  // descent: 50 ticks of height change, the 51st pops
  let ticks = 0;
  while (u8(G.gs, pod + O.STACK + 2 * u8(G.gs, pod + O.SP)) === 0x16) {
    City.statePodFlight(G, 6, podInfo);
    ticks++;
    assert.ok(ticks < 100);
  }
  assert.equal(ticks, 51);
  assert.equal(i16(G.gs, pod + O.HEIGHT), 0x384 + 3 * 1 * 1, 'last computed height (n0 = 1)');
  // landing: first pass snaps and re-dispatches, second starts the BUILD animation
  assert.equal(City.statePodLand(G, 6, landInfo), 1);
  assert.equal(City.statePodLand(G, 6, landInfo), 0);
  assert.equal(i16(G.gs, info), 2, 'building word = BUILD animation playing');
  assert.equal(i16(G.gs, GS.OBJ_ALLOC + 6 * 2), -1, 'pod freed');
  assert.equal(u8(G.gs, bld + O.ANIM0_STATUS), 1, 'BUILD animation mode 1');
  assert.equal(City.stateConstruct(G, 1, info), 0);
  assert.equal(i16(G.gs, info), 2, 'waits for the animation');
  w8(G.gs, bld + O.ANIM0_STATUS, 2); // animation finished (Anim's job)
  assert.equal(City.stateConstruct(G, 1, info), 0);
  assert.equal(i16(G.gs, info), 3, 'pod leaving');
  assert.equal(u8(G.gs, pa + P.SLOT_BLOCKED + 1), 0, 'slot no longer under construction');
  assert.equal(i16(G.gs, GS.OBJ_ALLOC + 6 * 2), 6, 'pod re-created');
  const podInfo2 = pod + O.INFO + 2 * u8(G.gs, pod + O.STACK + 1 + 2);
  const landInfo2 = pod + O.INFO + 2 * u8(G.gs, pod + O.STACK + 1);
  assert.equal(i16(G.gs, pod + O.HEIGHT), 0x384, 'starts on the ground');
  ticks = 0;
  while (u8(G.gs, pod + O.STACK + 2 * u8(G.gs, pod + O.SP)) === 0x16) {
    City.statePodFlight(G, 6, podInfo2);
    ticks++;
    assert.ok(ticks < 100);
  }
  assert.equal(ticks, 50);
  assert.equal(City.statePodLand(G, 6, landInfo2), 0);
  assert.equal(i16(G.gs, info), 4);
  const resets = calls('ticker.resetAndDispatchOrder').length;
  assert.equal(City.stateConstruct(G, 1, info), 0);
  assert.equal(u8(G.gs, pa + 0xe16), 0);
  assert.equal(calls('ticker.resetAndDispatchOrder').length, resets + 1, 'back to idle');
  assert.deepEqual(fired(G), []);
});

test('building idle: production, spawn tile, occupant nudge, unit cap refund, BUILD wait', () => {
  const G = makeGame();
  const pa = playerAddr(0);
  w32(G.gs, pa + P.CITY_X, 20);
  w32(G.gs, pa + P.CITY_Z, 20);
  w32(G.gs, GS.UNIT_CAP, 10);
  apply(G, raw(T.RESEARCH, 1, 0, 0)); // barracks, class 0, spawn (0,-3)
  apply(G, raw(T.BUILD, 0, 0, 2)); // two of type 0 (no BUILD sprite)
  const bld = objAddr(1);
  assert.equal(City.stateBuildingIdle(G, 1), 0);
  const created = calls('scenario.createObject');
  assert.equal(created.length, 1);
  assert.deepEqual(created[0].args, [20, 17, 0, 0, -1]);
  assert.equal(i16(G.gs, pa + P.QUEUE_LEN), 1);
  // occupied spawn tile: the occupant is nudged and nothing is produced
  const unit = created[0].args;
  const idx = 152;
  G.map.ground[17 * 64 + 20] = (G.map.ground[17 * 64 + 20] & ~0x3ff) | idx;
  w8(G.gs, objAddr(idx) + O.NUDGE, 0xff);
  assert.equal(City.stateBuildingIdle(G, 1), 0);
  assert.equal(u8(G.gs, objAddr(idx) + O.NUDGE), 0);
  assert.equal(calls('scenario.createObject').length, 1);
  assert.ok(unit);
  G.map.ground[17 * 64 + 20] = 0x3ff;
  // unit cap reached: refund, queue head dropped, message 0x77
  G.setStat(6, 0, 10);
  w32(G.gs, pa + P.MONEY, 0);
  const spent = i32(G.gs, pa + P.SPENT);
  assert.equal(City.stateBuildingIdle(G, 1), 0);
  assert.equal(i32(G.gs, pa + P.MONEY), 350);
  assert.equal(i32(G.gs, pa + P.SPENT), spent - 350);
  assert.equal(i16(G.gs, pa + P.QUEUE_LEN), 0);
  assert.equal(i16(G.gs, pa + P.WORD_1948), 0x77);
  G.setStat(6, 0, 0);
  // a type with a BUILD sprite reserves the tile, plays the animation in slot 3 and waits
  apply(G, raw(T.RESEARCH, 2, 0, 0)); // robot factory slot 2 -> class 1, spawn (2,3)
  apply(G, raw(T.BUILD, 2, 0, 1));
  assert.equal(City.stateBuildingIdle(G, 2), 0);
  assert.equal(G.map.ground[23 * 64 + 22] & 0x3ff, 0x3fe, 'reserved');
  assert.equal(u8(G.gs, pa + P.PROD_READY + 1), 0);
  assert.equal(u8(G.gs, objAddr(2) + O.ANIM2_STATUS), 1);
  assert.equal(calls('scenario.createObject').length, 1);
  assert.equal(City.stateBuildingIdle(G, 2), 0, 'still playing');
  w8(G.gs, objAddr(2) + O.ANIM2_STATUS, 2);
  assert.equal(City.stateBuildingIdle(G, 2), 0);
  assert.equal(calls('scenario.createObject').length, 2);
  assert.deepEqual(calls('scenario.createObject')[1].args, [22, 23, 2, 0, -1]);
  assert.equal(u8(G.gs, pa + P.PROD_READY + 1), 1);
  assert.equal(i16(G.gs, pa + P.QUEUE_LEN + 2), 0);
  // damage sprite choice (display only, but exercised)
  w32(G.gs, bld + O.HP, 10);
  w8(G.gs, bld + O.ANIM0_STATUS, 2);
  City.stateBuildingIdle(G, 1);
  const last = calls('anim.startAnim').at(-1).args;
  assert.equal(last[1], 'SCRCH17');
  assert.deepEqual(fired(G), []);
});

test('dependency checks and costs', () => {
  const G = makeGame();
  const pa = playerAddr(0);
  assert.equal(City.troopCost(G, 0), 350);
  assert.equal(City.troopCost(G, 99), 0);
  assert.equal(City.buildingCost(G, 1, 0, 0), 1000);
  assert.equal(City.buildingCost(G, 1, 0, 1), 0);
  assert.equal(City.itemCost(G, 7), 1500);
  assert.equal(City.itemKind(G, 30), 2);
  assert.deepEqual(City.depCheckBuilding(G, 0, 0), { status: 1, slot: 0, level: 0 });
  assert.deepEqual(City.depCheckBuilding(G, 0, 1), { status: 2 }, 'HQ not built yet');
  assert.deepEqual(City.depCheckTroop(G, 0, 9), { status: 2 });
  w32(G.gs, pa + P.SLOT_HP, 116);
  assert.deepEqual(City.depCheckBuilding(G, 0, 0), { status: 0 });
  assert.deepEqual(City.depCheckBuilding(G, 0, 1), { status: 1, slot: 1, level: 0 });
  assert.deepEqual(City.depCheckTroop(G, 0, 7), { status: 1, type: 6, cost: 1500 });
  w8(G.gs, pa + P.SLOT_BLOCKED + 1, 1);
  assert.deepEqual(City.depCheckBuilding(G, 0, 1), { status: 2 }, 'slot under construction');
  assert.equal(City.slotBlocked(G, 0, 1), true);
  w8(G.gs, pa + P.SLOT_BLOCKED + 1, 0);
  w8(G.gs, pa + P.DISABLED + 1, 1);
  assert.deepEqual(City.depCheckBuilding(G, 0, 1), { status: 2 }, 'disabled by a trigger');
  w8(G.gs, pa + P.DISABLED + 1, 0);
  w32(G.gs, playerAddr(1) + P.RACE, 1);
  assert.deepEqual(City.depCheckBuilding(G, 1, 1), { status: 2 }, 'wrong race');
  assert.deepEqual(City.depCheckTroop(G, 1, 9), { status: 2 });
  City.depRecompute(G, 0);
  assert.equal(G.tables.depend[0].status, 0);
  assert.equal(G.tables.depend[1].status, 1);
  assert.equal(G.tables.depend[3].status, 2, 'needs slot 3 and 1');
  assert.equal(G.tables.depend[9].status, 2, 'needs item 1 done');
  assert.equal(G.tables.depend[30].status, 1);
  assert.equal(G.globals.dependDirty, 1);
});

test('pickups: money crate and capture', () => {
  const G = makeGame();
  w32(G.gs, GS.DN_COUNTER, 4);
  addUnit(G, 300, 0, 2, 10, 10); // finder, team 2
  addUnit(G, 301, 0, 8, 12, 11); // crate
  w8(G.gs, objAddr(301) + O.PICKUP, 2);
  w32(G.gs, objAddr(301) + O.HP, 750);
  G.map.ground[10 * 64 + 10] = 300;
  City.pickupsTick(G, 301);
  assert.equal(i32(G.gs, playerAddr(2) + P.MONEY), 750);
  assert.equal(u8(G.gs, objAddr(301) + O.PICKUP), 0);
  assert.equal(calls('combat.objectDie').length, 1);
  assert.equal(calls('grid.removeObject').length, 1);
  // capture by a team-0 unit only
  addUnit(G, 302, 0, 8, 12, 11);
  w8(G.gs, objAddr(302) + O.PICKUP, 1);
  City.pickupsTick(G, 302);
  assert.equal(u8(G.gs, objAddr(302) + O.TEAM), 8, 'team 2 cannot capture');
  w8(G.gs, objAddr(300) + O.TEAM, 0);
  City.pickupsTick(G, 302);
  assert.equal(u8(G.gs, objAddr(302) + O.TEAM), 0);
  assert.equal(G.globals.captures[0], 1);
  w32(G.gs, GS.DN_COUNTER, 5);
  w8(G.gs, objAddr(302) + O.PICKUP, 1);
  w8(G.gs, objAddr(302) + O.TEAM, 8);
  City.pickupsTick(G, 302);
  assert.equal(u8(G.gs, objAddr(302) + O.TEAM), 8, 'only every 4th counter value');
});

test('handler table covers 0x03..0x1B with the integrator no-ops', () => {
  for (let t = 0x01; t <= 0x1b; t++) assert.equal(typeof Commands.HANDLERS[t], 'function', `type 0x${t.toString(16)}`);
  assert.equal(Commands.HANDLERS[0], undefined);
});

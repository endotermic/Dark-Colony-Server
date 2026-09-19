// The whole battle engine on a real map: start, run, commands, determinism (plan §18). This does
// not prove bit-exactness against the game (that needs recordings, §18.4); it proves that the
// integrated port starts from the room's inputs, runs thousands of ticks without an engine assert
// and gives the same checksums twice.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createGame, loadMapJson, loadEngineData } from '../src/engine/index.js';
import { build } from '../src/commands.js';
import { GS, O, OBJ_SIZE, objAddr, u8, u16, i32, w8, w16, w32 } from '../src/engine/mem.js';
import * as Ticker from '../src/engine/ticker.js';

function lobby(humans = [0, 3]) {
  const slots = [];
  for (let s = 0; s < 8; s++) {
    const h = humans.includes(s);
    slots.push({ type: h ? 2 : 3, race: 0, colour: s, team: s, name: h ? `P${s}` : '' });
  }
  return { slots, localSlot: -1, titleDigit: 8 };
}

function newGame(asserts, humans) {
  return createGame(loadMapJson('D8PLAY01'), lobby(humans), { assert: (msg, g) => asserts.push({ tick: g.tick, msg }) });
}

test('engine data and the room maps load', () => {
  const d = loadEngineData();
  assert.ok(d.consts.rand.values.length === 256);
  assert.ok(d.gamestat);
  assert.ok(d.sprites, 'data/classic/sprites.json is present');
  assert.ok(loadMapJson('D8PLAY01.SCN'));
  assert.equal(loadMapJson('NOSUCH01'), null);
});

test('Armageddon with two humans: start state, 2000 idle ticks without asserts, deterministic checksums', () => {
  const asserts = [];
  const G = newGame(asserts);
  const gs = G.gs;
  assert.deepEqual([...G.scenario.slotToPlayer], [1, -1, -1, 7, -1, -1, -1, -1], 'F41 shuffle with seed 0');
  assert.equal(i32(gs, GS.TICK), 0);
  const maxObj = i32(gs, GS.MAX_OBJ);
  assert.ok(maxObj >= 152, `objects up to ${maxObj}`);
  // one commander (type 69) and one trooper (type 0) per occupied player, 18 vents, no wildlife yet
  let commanders = 0;
  let troopers = 0;
  let vents = 0;
  for (let o = 0; o <= maxObj; o++) {
    const a = objAddr(o);
    if (u8(gs, a + O.LIFE) === 0) continue;
    const type = u8(gs, a + O.TYPE);
    if (type === 69) commanders++;
    else if (type === 0) troopers++;
    else if (type === 40) vents++;
  }
  assert.equal(commanders, 2);
  assert.equal(troopers, 2);
  assert.equal(vents, 18);
  assert.equal(asserts.length, 0, JSON.stringify(asserts.slice(0, 3)));

  const sums = [];
  const t0 = performance.now();
  for (let i = 0; i < 2000; i++) sums.push(G.step());
  const ms = performance.now() - t0;
  assert.equal(i32(gs, GS.TICK), 2000);
  assert.equal(asserts.length, 0, JSON.stringify(asserts.slice(0, 3)));
  assert.ok(ms < 5000, `2000 ticks took ${ms.toFixed(0)} ms`);
  assert.ok(i32(gs, GS.MAX_OBJ) > maxObj, 'the creature generators spawned wildlife');
  assert.equal(G.historyAt(2000), sums[1999]);
  assert.equal(G.historyAt(1745), sums[1744], 'history keeps the last 256 ticks');

  // the same inputs give the same checksums
  const asserts2 = [];
  const G2 = newGame(asserts2);
  for (let i = 0; i < 2000; i++) assert.equal(G2.step(), sums[i], `tick ${i + 1}`);
  assert.equal(G2.randIndex, G.randIndex);
});

test('a move order from the sync stream moves the trooper', () => {
  const asserts = [];
  const G = newGame(asserts);
  const gs = G.gs;
  const maxObj = i32(gs, GS.MAX_OBJ);
  let trooper = -1;
  for (let o = 152; o <= maxObj; o++) {
    const a = objAddr(o);
    if (u8(gs, a + O.LIFE) !== 0 && u8(gs, a + O.TYPE) === 0 && u8(gs, a + O.TEAM) === 1) trooper = o;
  }
  assert.ok(trooper > 0, 'player 1 (lobby slot 0) has a trooper');
  const a = objAddr(trooper);
  const x0 = u16(gs, a + O.X);
  const z0 = u16(gs, a + O.Z);
  for (let i = 0; i < 10; i++) G.step();
  // select it and send it 4 tiles east, as the client of game player 1 would (waypoints are
  // positions in 1/256 tile, stored as they arrive: 0x41D69C)
  G.applyCommand(build.select(1, [trooper]));
  G.applyCommand(build.waypointsSelected(1, [[x0 + 4 * 256, z0]]));
  G.applyCommand(build.orderSelected(1, 2));
  for (let i = 0; i < 200; i++) G.step();
  const x1 = u16(gs, a + O.X);
  assert.ok(x1 >= x0 + 2 * 256, `moved east from tile ${x0 >> 8} to ${x1 >> 8}`); // the target tile itself may be taken
  assert.equal(u8(gs, a + O.LIFE) !== 0, true);
  assert.equal(asserts.length, 0, JSON.stringify(asserts.slice(0, 3)));
});

test('an object created during the object loop with a new highest index runs its first tick in the same tick (0x419EA5 re-reads MAX_OBJ)', () => {
  // The desync of 18 Sep 2026 (plan §16, tick 9469): a unit produced by a building that the loop had
  // already dispatched took a new highest object index; the original dispatches it in the same tick
  // (the loop bound is re-read every iteration), so its idle fidget rand() came one tick earlier
  // than in the engine, which had cached MAX_OBJ before the loop.
  const asserts = [];
  const G = newGame(asserts);
  const gs = G.gs;
  const saved = Ticker.STATE_TABLE.slice();
  let created = -1;
  try {
    for (let s = 0; s < Ticker.STATE_TABLE.length; s++) {
      const f = saved[s];
      Ticker.STATE_TABLE[s] = (g, obj, info) => {
        if (created === -1 && obj >= 152 && u8(gs, objAddr(obj) + O.TYPE) === 0) {
          // a building dispatched earlier in this tick "produces" a copy of this trooper in a new slot
          created = i32(gs, GS.MAX_OBJ) + 1;
          gs.copy(gs, objAddr(created), objAddr(obj), objAddr(obj) + OBJ_SIZE);
          const na = objAddr(created);
          w16(gs, na + O.X, u16(gs, na + O.X) + 0x300);
          w8(gs, na + O.SP, 0); // stack: [idle, info 0]
          w8(gs, na + O.STACK, 1);
          w8(gs, na + O.STACK + 1, 0);
          w8(gs, na + O.NUDGE, 0xff);
          w8(gs, na + O.PENDING, 0);
          w16(gs, GS.OBJ_ALLOC + created * 2, created);
          w32(gs, GS.MAX_OBJ, created);
        }
        return f(g, obj, info);
      };
    }
    G.step();
  } finally {
    for (let s = 0; s < saved.length; s++) Ticker.STATE_TABLE[s] = saved[s];
  }
  assert.ok(created > 152, 'a trooper was dispatched and the newborn created');
  assert.equal(asserts.length, 0, JSON.stringify(asserts.slice(0, 3)));
  // the newborn's idle body ran in its birth tick: the idle fidget pushed its sleep state on top
  assert.equal(u8(gs, objAddr(created) + O.SP), 1, 'newborn dispatched in the tick of its creation');
  assert.equal(u8(gs, objAddr(created) + O.STACK + 2), 3, 'idle pushed the fidget sleep (state 3)');
});

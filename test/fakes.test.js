import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Harness, cmdsOf, startBattle } from './helpers.js';
import { T, build } from '../src/commands.js';
import { STATE } from '../src/constants.js';
import { loadConfig } from '../src/config.js';

test('config: fake player names and the MIN_PLAYERS limit', () => {
  const cfg = loadConfig({}, { FAKE_PLAYERS: 7, MIN_PLAYERS: 1 });
  assert.deepEqual(cfg.FAKE_NAME_LIST, ['AI Mercenary', 'Renegade', 'Outlaw', 'Nomad', 'Drifter', 'Vagabond', 'Marauder']);
  assert.equal(loadConfig({}, { FAKE_PLAYERS: 3, FAKE_NAMES: 'AI Mercenary,X' }).FAKE_NAME_LIST[2], 'AI Mercenary 3');
  assert.throws(() => loadConfig({}, { FAKE_PLAYERS: 7, MIN_PLAYERS: 2 }), /MIN_PLAYERS/);
  assert.throws(() => loadConfig({}, { FAKE_PLAYERS: 8 }), /FAKE_PLAYERS/);
  assert.ok(loadConfig({}, { LOG_LEVEL: 'debug' }).DEBUG);
  assert.ok(loadConfig({}, { DEBUG_MODE: true }).DEBUG);
  assert.ok(!loadConfig({}).DEBUG);
});

test('seven fake humans fill the lobby; the joiner gets the one free slot; a second joiner is refused', () => {
  const h = new Harness({ FAKE_PLAYERS: 7, MIN_PLAYERS: 1 });
  const fakes = h.room.fakeSlots();
  assert.equal(fakes.length, 7);
  assert.equal(fakes[0].slot, 0);
  assert.equal(fakes[0].name, 'AI Mercenary');
  assert.ok(fakes.every((f) => f.type === 2 && f.status === 1 && f.colour === f.slot && f.team === f.slot));
  const free = h.room.freeSlots();
  assert.equal(free.length, 1);

  const a = h.join('A');
  assert.equal(a.slot, free[0].slot);
  const cmds = a.takeCmds();
  const humans = cmds.filter((c) => c.type === T.TYPE && c.value === 2).map((c) => c.player).sort();
  assert.deepEqual(humans, [0, 1, 2, 3, 4, 5, 6, 7], 'all eight slots are humans in the dump');
  const present = cmds.filter((c) => c.type === T.READY && c.status === 1).map((c) => c.player);
  assert.equal(present.length, 8, 'fakes and the joiner are present-not-ready');
  assert.ok(cmds.some((c) => c.type === T.NAME && c.name === 'Renegade'));
  // every slot gets its colour before its type (F25 defaults are colour 0 for everybody)
  const firstL = cmds.findIndex((c) => c.type === T.COLOUR_SET);
  const firstJ = cmds.findIndex((c) => c.type === T.TYPE);
  assert.ok(firstL < firstJ);

  const b = h.join('B');
  assert.ok(b.sock.ended, 'lobby full');
  assert.equal(h.room.clients.size, 1);
});

test('fake slots are placed randomly and re-rolled on reset', () => {
  const h = new Harness({ FAKE_PLAYERS: 4, MIN_PLAYERS: 1 });
  const before = h.room.fakeSlots().map((f) => f.slot);
  assert.equal(before[0], 0);
  const seen = new Set([before.join(',')]);
  for (let i = 0; i < 30; i++) {
    h.room.reset();
    const now = h.room.fakeSlots().map((f) => f.slot);
    assert.equal(now.length, 4);
    assert.equal(now[0], 0);
    assert.equal(new Set(now).size, 4);
    seen.add(now.join(','));
  }
  assert.ok(seen.size > 1, 'placement varies between games');
});

test('the start signal frees every fake slot from status 1, and one real player can start', () => {
  const h = new Harness({ FAKE_PLAYERS: 7, MIN_PLAYERS: 1 });
  const a = h.join('A');
  a.take();
  a.cdReport();
  a.pressReady();
  assert.equal(h.room.state, STATE.STARTING);
  const cmds = a.takeCmds();
  const zeros = cmds.filter((c) => c.type === T.READY && c.status === 0).map((c) => c.player).sort();
  assert.deepEqual(zeros, h.room.fakeSlots().map((f) => f.slot).sort());
  assert.ok(!cmds.some((c) => c.type === T.READY && c.player !== a.slot && c.status === 2), 'no status-2 message for a fake');
  assert.ok(h.room.fakeSlots().every((f) => f.status === 0));
  a.send(build.mready(3, 2));
  assert.equal(h.room.state, STATE.RUNNING);
});

test('debug mode adds the full-map cheat to the first sync frame; normal mode does not', () => {
  let h = new Harness({ DEBUG_MODE: true });
  let [a] = startBattle(h);
  h.stepAfter(33);
  let cmds = cmdsOf(a.take()[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.TICK_SPEED, T.CHEAT]);
  assert.equal(cmds[2].a, 0);
  assert.equal(cmds[2].b, 0);

  h = new Harness();
  [a] = startBattle(h);
  h.stepAfter(33);
  cmds = cmdsOf(a.take()[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.TICK_SPEED]);
});

test('checksums are never forwarded and nothing hands Mercenary to the AI', () => {
  const h = new Harness();
  const [a, b] = startBattle(h);
  a.send(build.sync(1, 2));
  h.stepAfter(33);
  const cmds = cmdsOf(b.take()[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.TICK_SPEED]);
  assert.ok(!cmds.some((c) => c.type === T.DISCONNECT));
  assert.equal(a.client.strikes, 0);
});

// The battle engine beside the relay (plan §18): checksum commands in the sync frames, the tick at
// which frame commands reach the engine, comparison with client checksums, recording, and the
// MERCENARY_SLOT option. The engine itself is faked here (deterministic checksums); the real
// engine has its own tests (test/engine-*.test.js).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Harness, cmdsOf, startBattle, answerSync } from './helpers.js';
import { T, build } from '../src/commands.js';
import { readRecording } from '../src/recorder.js';

/** A stand-in for src/engine: checksum(t) = (t * 7 + 3) & 0xffff, commands recorded with their tick. */
function fakeEngine({ failAt = -1, mapMissing = false, slotToPlayer = null } = {}) {
  const games = [];
  return {
    games,
    loadMapJson: () => (mapMissing ? null : { name: 'fake' }),
    createGame(mapJson, lobby) {
      const gs = Buffer.alloc(0x8000);
      gs.writeInt32LE(41, 0x7d40);
      const g = {
        gs,
        lobby,
        time: 0,
        applied: [],
        history: new Map(),
        slotToPlayer,
        step() {
          this.time++;
          if (this.time === failAt) throw new Error('boom');
          const c = (this.time * 7 + 3) & 0xffff;
          this.history.set(this.time, c);
          return c;
        },
        historyAt(t) {
          return this.history.get(t);
        },
        applyCommand(raw) {
          this.applied.push({ tick: this.time, type: raw[0] });
        },
      };
      games.push(g);
      return g;
    },
  };
}

test('SYNC_CHECK=send: the first frame carries no checksum, every later frame carries the last simulated tick', () => {
  const eng = fakeEngine();
  const h = new Harness({ SYNC_CHECK: 'send' }, { engine: eng });
  const [a, b] = startBattle(h);
  assert.ok(h.room.sync.active, 'engine started with the battle');
  h.stepAfter(33); // frame 1: UNTIL(0, 9) + TICK_SPEED, engine runs ticks 1..9
  let fa = a.take();
  let cmds = cmdsOf(fa[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.TICK_SPEED]);
  assert.equal(eng.games[0].time, 9, 'engine advanced to until');
  answerSync(a, fa);
  answerSync(b, b.take());
  h.stepAfter(33); // frame 2: UNTIL(1, 10) + 0x08(checksum(9), 9)
  fa = a.take();
  const fb = b.take();
  assert.ok(fa[0].equals(fb[0]), 'identical for everybody');
  cmds = cmdsOf(fa[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.SYNC]);
  assert.equal(fa[0].readInt32LE(fa[0].length - 4), 9, 'tick 9');
  assert.equal(fa[0].readInt16LE(fa[0].length - 6) & 0xffff, (9 * 7 + 3) & 0xffff, 'checksum of tick 9');
  assert.equal(eng.games[0].time, 10);
  // the checksum tick is always <= until - 1 of the frame that carries it
  assert.ok(9 <= cmds[0].until - 1);
  assert.equal(h.room.sync.sentTicks, 1);
});

test('a checksum above 32767 goes out as its 16-bit pattern (the first send-mode game crashed here)', () => {
  const eng = fakeEngine();
  const h = new Harness({ SYNC_CHECK: 'send' }, { engine: eng });
  const [a, b] = startBattle(h);
  // checksums with the top bit set, like the 57372 of the first send-mode game
  eng.games[0].step = function step() {
    this.time++;
    const c = (this.time * 7 + 3 + 0x8000) & 0xffff;
    this.history.set(this.time, c);
    return c;
  };
  h.stepAfter(33);
  answerSync(a, a.take());
  answerSync(b, b.take());
  h.stepAfter(33);
  const f = a.take();
  assert.equal(f.length, 1, 'the frame went out');
  const cmds = cmdsOf(f[0]);
  assert.equal(cmds[1].type, T.SYNC);
  assert.equal(cmds[1].checksum & 0xffff, (9 * 7 + 3 + 0x8000) & 0xffff, 'unsigned pattern preserved');
  assert.equal(cmds[1].time, 9);
  assert.ok(h.room.sync.active);
  assert.equal(build.sync(0xc000, 1).readUInt16LE(1), 0xc000);
  assert.equal(build.sync(-1, 1).readUInt16LE(1), 0xffff);
});

test('frame commands reach the engine at until - 1, before the tick that reaches until', () => {
  const eng = fakeEngine();
  const h = new Harness({ SYNC_CHECK: 'shadow' }, { engine: eng });
  const [a, b] = startBattle(h);
  h.stepAfter(33);
  answerSync(a, a.take());
  answerSync(b, b.take());
  a.send(build.orderSelected(1, 2));
  h.stepAfter(33); // frame 2: UNTIL(1, 10) + ORDER_SEL
  const cmds = cmdsOf(a.take()[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.ORDER_SEL], 'shadow mode adds no 0x08');
  const g = eng.games[0];
  const order = g.applied.find((x) => x.type === T.ORDER_SEL);
  assert.ok(order, 'the order was applied');
  assert.equal(order.tick, 9, 'applied when the engine time was until - 1 = 9');
  assert.equal(g.time, 10);
  // TICK_SPEED of frame 1 is applied too (it is part of the simulation input); UNTIL is not
  assert.ok(g.applied.some((x) => x.type === T.TICK_SPEED && x.tick === 8));
  assert.ok(!g.applied.some((x) => x.type === T.UNTIL));
});

test('a client checksum is compared with the engine; a mismatch in send mode stops the checksums', () => {
  const eng = fakeEngine();
  const h = new Harness({ SYNC_CHECK: 'send' }, { engine: eng });
  const [a, b] = startBattle(h);
  h.stepAfter(33);
  answerSync(a, a.take());
  answerSync(b, b.take());
  a.send(build.sync((9 * 7 + 3) & 0xffff, 9));
  assert.equal(h.room.sync.compared, 1);
  assert.equal(h.room.sync.mismatches, 0);
  a.send(build.sync(12, 5)); // wrong
  assert.equal(h.room.sync.mismatches, 1);
  assert.deepEqual(h.room.sync.firstMismatch, { tick: 5, engine: (5 * 7 + 3) & 0xffff, client: 12, slot: a.slot });
  assert.ok(!h.room.sync.active, 'disabled after a mismatch');
  h.stepAfter(33);
  const cmds = cmdsOf(a.take()[0]);
  assert.ok(!cmds.some((c) => c.type === T.SYNC), 'no more 0x08');
  // a checksum for a tick the engine has not reached is ignored, not counted
  b.send(build.sync(1, 5000));
  assert.equal(h.room.sync.compared, 2);
});

test('an engine failure disables the engine and the relay goes on', () => {
  const eng = fakeEngine({ failAt: 12 });
  const h = new Harness({ SYNC_CHECK: 'send' }, { engine: eng });
  const [a, b] = startBattle(h);
  h.stepAfter(33);
  answerSync(a, a.take());
  answerSync(b, b.take());
  h.stepAfter(33); // engine reaches 10
  answerSync(a, a.take());
  answerSync(b, b.take());
  h.stepAfter(33 * 3); // engine would reach 13: fails at 12
  const f = a.take();
  assert.equal(f.length, 1, 'the frame still went out');
  assert.ok(!h.room.sync.active);
  assert.match(h.room.sync.disabledReason, /engine error at tick 11/);
  h.stepAfter(33);
  assert.equal(a.take().length, 1, 'relay continues');
});

test('no map JSON or no engine module: the room runs as a plain relay', () => {
  const h1 = new Harness({ SYNC_CHECK: 'send' }, { engine: fakeEngine({ mapMissing: true }) });
  startBattle(h1);
  assert.ok(!h1.room.sync.active);
  assert.match(h1.room.sync.disabledReason, /no maps\//);
  const h2 = new Harness({ SYNC_CHECK: 'shadow' });
  startBattle(h2);
  assert.ok(!h2.room.sync.active);
  assert.match(h2.room.sync.disabledReason, /not available/);
  const h3 = new Harness({}, { engine: fakeEngine() });
  startBattle(h3);
  assert.ok(!h3.room.sync.active, 'off by default');
  assert.equal(h3.room.sync.mode, 'off');
});

test('MREADY reports the client-side shuffle; a different game player index disables the engine', () => {
  const eng = fakeEngine({ slotToPlayer: [0, 5, 6, 1, 2, 3, 4, 7] });
  const h = new Harness({ SYNC_CHECK: 'shadow' }, { engine: eng });
  const peers = ['A', 'B'].map((n) => h.join(n));
  for (const p of peers) {
    p.take();
    p.cdReport();
  }
  for (const p of peers) p.pressReady();
  peers[0].send(build.mready(eng.createGame({}, {}).slotToPlayer[peers[0].slot], 2)); // matches
  peers[1].send(build.mready(7, 2)); // may or may not match
  assert.equal(h.room.state, 'RUNNING');
  const expected = [0, 5, 6, 1, 2, 3, 4, 7][peers[1].slot];
  assert.equal(h.room.sync.active, expected === 7);
});

test('the 0x08 counts against the command budget and yields to a full-size command group', () => {
  const eng = fakeEngine();
  const h = new Harness({ SYNC_CHECK: 'send' }, { engine: eng });
  const [a, b] = startBattle(h);
  h.stepAfter(33);
  answerSync(a, a.take());
  answerSync(b, b.take());
  const big = build.select(1, new Array(504).fill(7)); // 1 + 1 + 1008 + 2 = 1012 = the whole budget
  assert.equal(big.length, 1012);
  a.send(big);
  h.stepAfter(33);
  let f = a.take();
  b.take();
  let cmds = cmdsOf(f[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.SELECT], 'the group goes out, the checksum waits');
  assert.equal(h.room.sync.sentTicks, 0);
  answerSync(a, f);
  h.stepAfter(33);
  f = a.take();
  cmds = cmdsOf(f[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.SYNC]);
  assert.equal(f[0].readInt32LE(f[0].length - 4), 10, 'the checksum is the newest one, not the skipped one');
  // a 1005-byte group fits next to the 7-byte checksum exactly
  const fits = build.select(1, new Array(500).fill(7)); // 1004 bytes
  a.send(Buffer.concat([fits, build.deselect(1)])); // 1004 + 2 = 1006 > 1005: does not fit
  a.send(build.deselect(1));
  answerSync(a, f);
  h.stepAfter(33);
  cmds = cmdsOf(a.take()[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.SELECT, T.DESELECT, T.DESELECT], 'checksum skipped, both groups fit without it');
});

test('a DISCONNECT hands a base to the AI, which is not ported: send mode stops, shadow mode goes on', () => {
  const h = new Harness({ SYNC_CHECK: 'send', ECHO_TIMEOUT_MS: 100 }, { engine: fakeEngine() });
  const [a, b] = startBattle(h);
  h.stepAfter(33);
  answerSync(a, a.take());
  b.take(); // B never echoes
  h.advance(200);
  h.tick();
  assert.ok(b.gone);
  h.stepAfter(33); // the frame with DISCONNECT(b)
  const cmds = cmdsOf(a.take()[0]);
  assert.ok(cmds.some((c) => c.type === T.DISCONNECT));
  assert.ok(!h.room.sync.active);
  assert.match(h.room.sync.disabledReason, /AI took over/);
  const h2 = new Harness({ SYNC_CHECK: 'send', FILL_EMPTY_WITH_AI: true }, { engine: fakeEngine() });
  startBattle(h2);
  assert.ok(!h2.room.sync.active);
  assert.match(h2.room.sync.disabledReason, /computer players/);
});

test('MERCENARY_SLOT=7 pins the fake host to slot 7 and keeps slot 0 unused', () => {
  const h = new Harness({ MERCENARY_SLOT: 7, FAKE_PLAYERS: 3 });
  const r = h.room;
  assert.ok(r.slots[7].fake);
  assert.equal(r.slots[7].name, 'AI Mercenary');
  assert.ok(!r.slots[0].fake);
  assert.equal(r.fakeSlots().length, 3);
  assert.ok(!r.canJoin(0));
  assert.ok(!r.canJoin(7), 'the fake host is never relocated');
  const peers = ['A', 'B', 'C', 'D'].map((n) => h.join(n));
  for (const p of peers) assert.ok(p.slot >= 1 && p.slot <= 6, `slot ${p.slot}`);
  assert.ok(r.slots[7].fake, 'still there after four joins');
});

test('RECORD_DIR writes a JSON-lines recording of the battle', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dc-rec-'));
  try {
    const eng = fakeEngine();
    const h = new Harness({ SYNC_CHECK: 'shadow', RECORD_DIR: dir }, { engine: eng });
    const [a, b] = startBattle(h);
    h.stepAfter(33);
    answerSync(a, a.take());
    answerSync(b, b.take());
    a.send(build.sync(1234, 3));
    h.stepAfter(33);
    a.take();
    b.take();
    const file = h.room.sync.recorder.file;
    assert.ok(file && fs.existsSync(file));
    h.room.reset();
    const lines = readRecording(file);
    assert.equal(lines[0].type, 'start');
    assert.equal(lines[0].tickMs, 33);
    assert.equal(lines[0].lobby.slots.length, 8);
    assert.equal(lines[0].lobby.slots[0].name, 'AI Mercenary');
    const frames = lines.filter((l) => l.type === 'frame');
    assert.equal(frames.length, 2);
    assert.equal(frames[0].a, 0);
    assert.equal(frames[0].until, 9);
    assert.equal(frames[0].cmds.slice(0, 2), '11', 'TICK_SPEED in frame 1');
    assert.equal(lines.filter((l) => l.type === 'engine').length, 10);
    assert.ok(lines.some((l) => l.type === 'rx08' && l.slot === a.slot && l.tick === 3 && l.checksum === 1234));
    assert.ok(lines.some((l) => l.type === 'mismatch' && l.tick === 3));
    assert.equal(lines.at(-1).type, 'end');
    assert.equal(lines.at(-1).mismatches, 1);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

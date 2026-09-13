import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Harness, cmdsOf } from './helpers.js';
import { T, build } from '../src/commands.js';
import { encodeFrame } from '../src/frame.js';
import { STATE } from '../src/constants.js';

function leaveAnnouncement(cmds, slot) {
  return cmds.some((c) => c.type === T.DISCONNECT && c.player === slot);
}

test('a silent joiner is evicted after JOIN_TIMEOUT and announced to the others', () => {
  const h = new Harness();
  const a = h.join('A');
  const b = h.join('B');
  a.take();
  b.take();
  b.cdReport();
  // B behaves (keep-alives every 700 ms), A stays silent
  for (let t = 0; t < 4900; t += 700) {
    h.advance(700);
    b.keepalive();
    h.tick();
  }
  assert.ok(!a.gone, 'still inside JOIN_TIMEOUT');
  h.advance(200);
  b.keepalive();
  h.tick();
  assert.ok(a.gone);
  assert.ok(!b.gone);
  const cmds = b.takeCmds();
  assert.ok(leaveAnnouncement(cmds, a.slot));
  assert.ok(cmds.some((c) => c.type === T.LOBBY_CHAT && c.text.includes('left the lobby')));
  assert.equal(h.room.slots[a.slot].type, 3);
});

test('keep-alives keep a lobby client alive; their absence evicts', () => {
  const h = new Harness();
  const a = h.join('A');
  a.take();
  a.cdReport();
  for (let i = 0; i < 10; i++) {
    h.advance(700);
    a.keepalive();
    h.tick();
  }
  assert.ok(!a.gone);
  h.advance(3001);
  h.tick();
  assert.ok(a.gone);
  assert.equal(h.room.state, STATE.LOBBY);
});

test('a wrong sequence nibble evicts under STRICT_SEQ and resyncs otherwise; duplicates are skipped', () => {
  let h = new Harness();
  let a = h.join('A');
  a.take();
  a.send(build.keepalive(), 0); // expected 0
  a.send(build.keepalive(), 0); // duplicate: skipped
  assert.ok(!a.gone);
  a.send(build.keepalive(), 5); // expected 1
  assert.ok(a.gone);

  h = new Harness({ STRICT_SEQ: false });
  a = h.join('A');
  a.take();
  a.send(build.keepalive(), 5);
  assert.ok(!a.gone);
  assert.equal(a.client.seqIn, 6);
});

test('garbage and unknown command types evict', () => {
  const h = new Harness();
  const a = h.join('A');
  a.take();
  a.raw([0xff, 0xff, 0x00]); // length 0xfff
  assert.ok(a.gone);

  const b = h.join('B');
  b.take();
  b.send(Buffer.from([0x1c, 0, 0])); // no handler for 0x1c
  assert.ok(b.gone);

  const c = h.join('C');
  c.take();
  c.raw([...encodeFrame(build.keepalive(), 0).subarray(0, 3), 0x42]); // wrong terminator
  assert.ok(c.gone);
});

test('a ready player leaving gets h(0) before DISCONNECT so the colour lock is released', () => {
  const h = new Harness({ MIN_PLAYERS: 3 });
  const a = h.join('A');
  const b = h.join('B');
  a.take();
  b.take();
  a.pressReady();
  b.take();
  a.sock.destroy();
  const cmds = b.takeCmds();
  const i0 = cmds.findIndex((c) => c.type === T.READY && c.player === a.slot && c.status === 0);
  const i1 = cmds.findIndex((c) => c.type === T.DISCONNECT && c.player === a.slot);
  assert.ok(i0 >= 0 && i1 >= 0 && i0 < i1);
  // a non-ready player leaving gets no h(0)
  const c = h.join('C');
  c.take();
  b.take();
  c.sock.destroy();
  const cmds2 = b.takeCmds();
  assert.ok(!cmds2.some((x) => x.type === T.READY && x.player === c.slot));
  assert.ok(leaveAnnouncement(cmds2, c.slot));
});

test('MREADY timeout evicts the loader that never reports and lets the rest start', () => {
  const h = new Harness();
  const a = h.join('A');
  const b = h.join('B');
  const c = h.join('C');
  for (const p of [a, b, c]) {
    p.take();
    p.pressReady();
  }
  assert.equal(h.room.state, STATE.STARTING);
  a.send(build.mready(a.slot, 2));
  b.send(build.mready(b.slot, 2));
  h.advance(30001);
  h.tick();
  assert.ok(c.gone);
  assert.equal(h.room.state, STATE.RUNNING);
  a.take();
  h.stepAfter(33);
  const cmds = cmdsOf(a.take()[0]);
  assert.ok(leaveAnnouncement(cmds, c.slot));
});

test('in-game strikes accumulate to an eviction announced in the sync stream', () => {
  const h = new Harness({ STRIKE_LIMIT: 3 });
  const a = h.join('A');
  const b = h.join('B');
  for (const p of [a, b]) {
    p.take();
    p.pressReady();
  }
  for (const p of [a, b]) p.send(build.mready(p.slot, 2));
  a.take();
  b.take();
  const createRaw = Buffer.from([T.CREATE, ...new Array(13).fill(0)]); // the cheat command without a sender (F13)
  a.send(createRaw);
  a.send(createRaw);
  assert.ok(!a.gone);
  a.send(createRaw);
  assert.ok(a.gone);
  h.stepAfter(33);
  assert.ok(leaveAnnouncement(cmdsOf(b.take()[0]), a.slot));
});

test('a socket error evicts like a close', () => {
  const h = new Harness();
  const a = h.join('A');
  const b = h.join('B');
  a.take();
  b.take();
  a.sock.emit('error', Object.assign(new Error('reset'), { code: 'ECONNRESET' }));
  assert.ok(a.gone);
  assert.ok(leaveAnnouncement(b.takeCmds(), a.slot));
});

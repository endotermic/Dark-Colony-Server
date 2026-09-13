import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Harness, cmdsOf, startBattle, answerSync } from './helpers.js';
import { T, build } from '../src/commands.js';
import { STATE } from '../src/constants.js';
import { COMMAND_BUDGET } from '../src/game.js';

// 0x03 create object: the one in-game command with no legitimate sender (F13)
const CREATE_RAW = Buffer.from([T.CREATE, ...new Array(13).fill(0)]);

test('sync frames: UNTIL first, until strictly increasing, commands in arrival order, identical bytes', () => {
  const h = new Harness();
  const [a, b] = startBattle(h);
  h.stepAfter(33); // frame 1: UNTIL(0, 9) + TICK_SPEED
  let fa = a.take();
  let fb = b.take();
  assert.equal(fa.length, 1);
  assert.ok(fa[0].equals(fb[0]));
  answerSync(a, fa);
  answerSync(b, fb);

  // both send commands between steps; B's frame carries two commands that must stay together
  a.send(build.orderSelected(1, 2));
  b.send(Buffer.concat([build.waypointsSelected(2, [[10, 20]]), build.orderSelected(2, 7)]));
  h.stepAfter(20); // less than a tick: nothing goes out
  assert.equal(a.take().length, 0);
  h.stepAfter(13); // 33 ms since the last frame
  fa = a.take();
  fb = b.take();
  assert.equal(fa.length, 1);
  assert.ok(fa[0].equals(fb[0]));
  const cmds = cmdsOf(fa[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.ORDER_SEL, T.WAYPOINTS_SEL, T.ORDER_SEL]);
  assert.equal(cmds[0].a, 1);
  assert.equal(cmds[0].until, 10);
  assert.equal(fa[0][0], T.UNTIL, 'frame starts with the sync command');

  // a long gap produces one frame with many ticks, as far as MAX_LAG allows
  answerSync(a, fa);
  answerSync(b, fb);
  h.stepAfter(33 * 150);
  const big = cmdsOf(a.take()[0])[0];
  assert.equal(big.a, 2);
  assert.equal(big.until, 2 + 150 + 8);
});

test('a step never covers more than 255 ticks', () => {
  const h = new Harness({ MAX_LAG: 1000 });
  const [a] = startBattle(h);
  h.stepAfter(33 * 300);
  const u = cmdsOf(a.take()[0])[0];
  assert.equal(u.a, 0);
  assert.equal(u.until, 255 + 8);
});

test('filters: sync checks and speed messages vanish silently, cheats strike, cheat chat is dropped', () => {
  const h = new Harness();
  const [a, b] = startBattle(h);
  a.send(build.sync(0x1234, 5));
  a.send(build.tickSpeed(66));
  a.send(build.tickMaxSpeed(1, 100));
  a.send(build.tickDesSpeed(66));
  assert.equal(a.client.strikes, 0);
  a.send(CREATE_RAW);
  a.send(build.cheat(5, 2));
  assert.equal(a.client.strikes, 2);
  a.send(build.chat(1, 0xff, 'Alice: slag net'));
  a.send(build.chat(1, 0xff, "Alice: I'm fighting for that equipment"));
  assert.equal(a.client.strikes, 4);
  a.send(build.chat(1, 0xff, 'Alice: gg'));
  // 0x0F is the diplomacy screen's "give 1000" (F47): relayed, not a cheat; an impossible player strikes
  a.send(build.bonus(b.slot));
  a.send(build.bonus(9));
  assert.equal(a.client.strikes, 5);
  h.stepAfter(33);
  const cmds = cmdsOf(b.take()[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.TICK_SPEED, T.CHAT, T.BONUS]);
  assert.equal(cmds[2].text, 'Alice: gg');
  assert.equal(cmds[3].player, b.slot);
  // ten strikes evict
  for (let i = 0; i < 5; i++) a.send(CREATE_RAW);
  assert.ok(a.gone);
});

test('the command budget is respected and groups carry over intact', () => {
  const h = new Harness();
  const [a, b] = startBattle(h);
  h.stepAfter(33);
  a.take();
  b.take();
  // a 700-byte group, then a 400-byte group: the second does not fit next to the first
  const big1 = build.select(1, new Array(348).fill(7)); // 1 + 1 + 696 + 2 = 700
  const big2 = build.select(2, new Array(198).fill(7)); // 400
  const small = build.orderSelected(1, 2);
  assert.equal(big1.length, 700);
  assert.equal(big2.length, 400);
  a.send(big1);
  b.send(big2);
  a.send(small);
  h.stepAfter(33);
  let cmds = cmdsOf(a.take()[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.SELECT]);
  assert.equal(cmds[1].raw.length, 700);
  h.stepAfter(33);
  cmds = cmdsOf(a.take()[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.SELECT, T.ORDER_SEL]);
  assert.equal(cmds[1].raw.length, 400);
  assert.ok(COMMAND_BUDGET === 1012);
});

test('echo and progress bookkeeping; an echo for a frame never sent evicts', () => {
  const h = new Harness();
  const [a, b] = startBattle(h);
  h.stepAfter(33);
  const fa = a.take();
  b.take();
  assert.equal(a.client.pendingEchoes.size, 1);
  const u = cmdsOf(fa[0])[0];
  a.send(build.until(u.a, u.until));
  assert.equal(a.client.pendingEchoes.size, 0);
  assert.equal(a.client.latencyMs, 0);
  a.send(build.until(-1, u.until));
  assert.equal(a.client.clientTime, u.until);
  // repeated or unknown progress report
  a.send(build.until(-1, u.until));
  assert.ok(a.gone);
  // B echoes something that was never sent
  b.send(build.until(12345, 9));
  assert.ok(b.gone);
  assert.equal(h.room.state, STATE.LOBBY, 'last client gone: room reset');
});

test('stall at MAX_LAG, echo timeout evicts the silent client, the survivor continues', () => {
  const h = new Harness({ MAX_LAG: 20, LAG_DROP_MS: 0 });
  const [a, b] = startBattle(h);
  // nobody reports progress: frames flow until until - 0 >= 20
  let frames = 0;
  for (let i = 0; i < 30; i++) {
    h.stepAfter(33);
    const f = a.take();
    b.take();
    frames += f.length;
    for (const p of f) a.send(build.until(cmdsOf(p)[0].a, cmdsOf(p)[0].until)); // A echoes only
  }
  // until = time + 1 + 8 >= 20  ->  time >= 11  -> frames with time 0..10 = 11 frames
  assert.equal(frames, 11);
  assert.ok(h.room.game.stallSince > 0);
  // B has not echoed anything: after ECHO_TIMEOUT it is evicted first
  h.advance(5001);
  h.tick();
  assert.ok(b.gone, 'B evicted for missing echoes');
  assert.ok(!a.gone);
  // A now reports progress: the stall clears, the next frame carries the DISCONNECT of B
  a.send(build.until(-1, 9));
  h.stepAfter(33); // the long idle gap would jump too far ahead: stalled once, accumulator capped
  assert.equal(a.take().length, 0);
  h.stepAfter(33);
  const cmds = cmdsOf(a.take()[0]);
  assert.equal(cmds[0].type, T.UNTIL);
  assert.ok(cmds.some((c) => c.type === T.DISCONNECT && c.player === b.slot));
  assert.equal(h.room.game.stallSince, 0);
});

test('lag eviction drops the slowest client after LAG_DROP_MS', () => {
  const h = new Harness({ MAX_LAG: 20, LAG_DROP_MS: 1000, ECHO_TIMEOUT_MS: 60000, IDLE_TIMEOUT_MS: 60000 });
  const [a, b] = startBattle(h);
  for (let i = 0; i < 15; i++) {
    h.stepAfter(33);
    const fa = a.take();
    const fb = b.take();
    answerSync(a, fa); // A keeps up
    for (const p of fb) b.send(build.until(cmdsOf(p)[0].a, cmdsOf(p)[0].until)); // B only echoes
  }
  const since = h.room.game.stallSince;
  assert.ok(since > 0, 'stalled because of B');
  h.t = since + 999;
  h.tick();
  assert.ok(!b.gone);
  h.t = since + 1001;
  h.tick();
  assert.ok(b.gone, 'B dropped as the laggard');
  assert.ok(!a.gone);
  h.stepAfter(33); // idle gap: stalled once, accumulator capped
  h.stepAfter(33);
  const cmds = cmdsOf(a.take().at(-1));
  assert.ok(cmds.some((c) => c.type === T.DISCONNECT && c.player === b.slot));
});

test('pause relays out of band and stops the clock; resume re-sends the speed', () => {
  const h = new Harness();
  const [a, b] = startBattle(h);
  h.stepAfter(33);
  answerSync(a, a.take());
  answerSync(b, b.take());
  a.send(build.cheat(1, 0));
  assert.ok(h.room.game.paused);
  let fb = b.take();
  assert.equal(fb.length, 1);
  assert.equal(fb[0][0], T.CHEAT, 'pause is a standalone frame');
  h.stepAfter(500);
  assert.equal(b.take().length, 0, 'no sync frames while paused');
  b.send(build.cheat(2, 0));
  assert.ok(!h.room.game.paused);
  a.take();
  fb = b.take();
  assert.equal(fb.length, 1);
  assert.equal(fb[0][0], T.CHEAT, 'resume is relayed as a standalone frame too');
  h.stepAfter(33);
  fb = b.take();
  const cmds = cmdsOf(fb[0]);
  assert.equal(cmds[0].type, T.UNTIL);
  assert.equal(cmds[0].until, 10, 'the clock did not run while paused');
  assert.ok(cmds.some((c) => c.type === T.TICK_SPEED));
});

test('a closing socket becomes a DISCONNECT in the next frame; the last one leaving resets the room', () => {
  const h = new Harness();
  const [a, b] = startBattle(h);
  h.stepAfter(33);
  answerSync(a, a.take());
  answerSync(b, b.take());
  a.sock.destroy();
  assert.equal(h.room.clients.size, 1);
  h.stepAfter(33);
  const cmds = cmdsOf(b.take()[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.DISCONNECT]);
  assert.equal(cmds[1].player, a.slot);
  b.sock.destroy();
  assert.equal(h.room.state, STATE.LOBBY);
  assert.equal(h.room.clients.size, 0);
  assert.equal(h.room.slots[0].status, 1, 'Mercenary holds the next lobby again');
  // the room is usable again
  const c = h.join('C');
  assert.ok(c.slot >= 1 && c.slot <= 7);
});

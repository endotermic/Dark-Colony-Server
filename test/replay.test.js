// Replay mode (REPLAY_FILE, src/replay.js, plan §18.7): the recorded lobby, the pinned seat, the
// recorded frames byte for byte at the recorded pace, the watcher's orders dropped, checksums compared.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Harness, cmdsOf, answerSync } from './helpers.js';
import { build, T } from '../src/commands.js';
import { Replay, loadReplay, replayConfig } from '../src/replay.js';
import { loadConfig } from '../src/config.js';
import { RoomPool } from '../src/rooms.js';
import { createLogger, silentLogger } from '../src/log.js';
import { SLOT_TYPE, STATE } from '../src/constants.js';

const hex = (...bufs) => Buffer.concat(bufs).toString('hex');

/** A recorded battle: fake host in 0, AI Marauder in 2, the real player Kamyck in 4 (Gray), five frames with a stall. */
function recording() {
  const empty = (s) => ({ type: SLOT_TYPE.EMPTY, race: 0, colour: s, team: s, name: '' });
  const slots = [
    { type: SLOT_TYPE.HUMAN, race: 0, colour: 0, team: 0, name: 'AI Mercenary' },
    empty(1),
    { type: SLOT_TYPE.HUMAN, race: 1, colour: 2, team: 2, name: 'AI Marauder' },
    empty(3),
    { type: SLOT_TYPE.HUMAN, race: 1, colour: 5, team: 4, name: 'Kamyck' },
    empty(5),
    empty(6),
    { type: SLOT_TYPE.AI_EASY, race: 0, colour: 7, team: 7, name: '' },
  ];
  const frames = [
    { a: 0, until: 9, cmds: hex(build.tickSpeed(33)) },
    { a: 1, until: 10, cmds: hex(build.sync(0x1234, 9), build.orderSelected(6, 2)) },
    { a: 2, until: 11, cmds: hex(build.sync(0x2345, 10)) },
    { a: 3, until: 14, cmds: hex(build.sync(0x3456, 11), build.chat(6, 0xff, 'Kamyck: gg')) }, // a stall: the next frame is 3 ticks later
    { a: 6, until: 15, cmds: '' },
  ];
  return [
    {
      type: 'start',
      version: 1,
      map: { file: 'D8PLAY01.SCN', name: 'Armageddon', terrain: 'desert', players: 8 },
      tickMs: 33,
      lookahead: 8,
      syncCheck: 'send',
      mercenarySlot: 0,
      lobby: { slots, localSlot: -1, titleDigit: 8 },
      players: [{ slot: 4, name: 'Kamyck' }],
      t: '2026-09-18T11:13:25.298Z',
    },
    { type: 'mready', slot: 4, gamePlayer: 7 },
    ...frames.map((f) => ({ type: 'frame', ...f })),
    { type: 'rx08', slot: 4, tick: 9, checksum: 0x1234 },
    { type: 'rx08', slot: 4, tick: 10, checksum: 0x2345 },
    { type: 'end', reason: 'room reset', engineTime: 15, mismatches: 0, disabled: null },
  ];
}

const recordedPayloads = () => recording().filter((l) => l.type === 'frame').map((f) => Buffer.concat([build.until(f.a, f.until), Buffer.from(f.cmds, 'hex')]));

test('the room is the recorded lobby, the client gets the recorded seat with race, colour and team pinned', () => {
  const h = new Harness({ MIN_PLAYERS: 1, TICK_MS: 33 }, { replay: new Replay(recording()) });
  const r = h.room;
  assert.equal(r.map.file, 'D8PLAY01.SCN');
  assert.equal(r.map.name, 'Armageddon');
  assert.ok(r.slots[0].fake && r.slots[0].name === 'AI Mercenary');
  assert.ok(r.slots[2].fake && r.slots[2].race === 1 && r.slots[2].name === 'AI Marauder');
  assert.equal(r.slots[4].type, SLOT_TYPE.EMPTY, 'the recorded player\'s seat is free');
  assert.equal(r.slots[7].type, SLOT_TYPE.AI_EASY, 'the AI slot as recorded');
  assert.equal(r.slots[1].type, SLOT_TYPE.EMPTY);
  assert.deepEqual(r.seatableSlots().map((s) => s.slot), [4], 'only that seat can be taken');
  const me = h.join('Me');
  assert.equal(me.slot, 4);
  const dump = me.take().flatMap(cmdsOf);
  assert.ok(dump.some((c) => c.type === T.RACE && c.raw[1] === 1 && c.raw[2] === 4), 'race Gray as recorded');
  assert.ok(dump.some((c) => c.type === T.COLOUR_SET && c.raw[1] === 5 && c.raw[2] === 4), 'colour 5 as recorded');
  assert.ok(dump.some((c) => c.type === T.TEAM_SET && c.raw[1] === 4 && c.raw[2] === 4), 'team as recorded');
  assert.ok(dump.some((c) => c.type === T.LOBBY_CHAT && c.text.includes('Replay')), 'the greeting explains the replay');
  const other = h.join('Other');
  assert.ok(other.sock.ended, 'a second client has no seat');
  // the client may not change what the recording fixed
  me.send(build.race(0, 4));
  let back = me.take().flatMap(cmdsOf);
  assert.ok(back.some((c) => c.type === T.RACE && c.raw[1] === 1 && c.raw[2] === 4), 'the recorded race comes back');
  assert.equal(r.slots[4].race, 1);
  me.send(build.colourCycle(1, 4));
  back = me.take().flatMap(cmdsOf);
  assert.ok(back.some((c) => c.type === T.COLOUR_SET && c.raw[1] === 5 && c.raw[2] === 4));
  assert.equal(r.slots[4].colour, 5);
  me.send(build.teamCycle(1, 4));
  back = me.take().flatMap(cmdsOf);
  assert.ok(back.some((c) => c.type === T.TEAM_SET && c.raw[1] === 4 && c.raw[2] === 4));
  assert.equal(r.slots[4].team, 4);
});

test('the recorded frames go out byte for byte at the recorded pace; the watcher\'s orders are dropped, its checksums compared', () => {
  const h = new Harness({ MIN_PLAYERS: 1, TICK_MS: 33 }, { replay: new Replay(recording()) });
  const rp = h.room.replay;
  const me = h.join('Me');
  me.take();
  me.cdReport();
  me.pressReady();
  assert.equal(h.room.state, STATE.STARTING);
  me.send(build.mready(7, 2));
  assert.equal(h.room.state, STATE.RUNNING);
  me.take();
  const want = recordedPayloads();
  h.stepAfter(33);
  let f = me.take();
  assert.equal(f.length, 1);
  assert.ok(f[0].equals(want[0]), 'frame 0 exactly as recorded (TICK_SPEED, no server additions)');
  answerSync(me, f);
  me.send(build.orderSelected(6, 3)); // the watcher clicks something
  me.send(build.cheat(1, 0)); // presses pause
  me.send(build.chat(6, 0xff, 'Me: hello')); // chats
  me.send(build.cheat(0, 0)); // a cheat flag: no strike in replay
  h.stepAfter(33);
  f = me.take();
  assert.equal(f.length, 1, 'no standalone pause frame came back');
  assert.ok(f[0].equals(want[1]), 'frame 1 as recorded, the watcher\'s order is not in it');
  assert.equal(h.room.game.paused, false, 'the watcher cannot pause the replay');
  assert.equal(rp.dropped, 4);
  assert.deepEqual(rp.ignored, { ORDER_SEL: 1, CHEAT: 2, CHAT: 1 });
  assert.equal(h.room.slots[4].client.strikes, 0, 'nothing the watcher sends is a violation');
  answerSync(me, f);
  me.send(build.sync(0x1234, 9)); // matches the recorded client checksum
  me.send(build.sync(0x9999, 10)); // does not
  assert.deepEqual(rp.divergedAt, { tick: 10, expected: 0x2345, got: 0x9999, slot: 4 });
  assert.equal(rp.compared, 2);
  h.stepAfter(33);
  f = me.take();
  assert.ok(f[0].equals(want[2]));
  answerSync(me, f);
  h.stepAfter(33);
  assert.equal(me.take().length, 0, 'frame 3 stands for three ticks: not yet');
  h.stepAfter(66);
  f = me.take();
  assert.equal(f.length, 1);
  assert.ok(f[0].equals(want[3]), 'frame 3 after three ticks');
  answerSync(me, f);
  h.stepAfter(33);
  f = me.take();
  assert.ok(f[0].equals(want[4]), 'the empty last frame');
  answerSync(me, f);
  h.stepAfter(33);
  assert.equal(me.take().length, 0);
  assert.ok(rp.finished);
  assert.ok(h.room.game.replayDone);
  assert.equal(h.room.game.framesSent, 5);
  h.stepAfter(330);
  assert.equal(me.take().length, 0, 'nothing more after the recording');
  assert.ok(!me.gone, 'the watcher stays connected');
});

test('REPLAY_FULL_MAP sends the map reveal as a standalone frame at battle start, the recorded frames untouched', () => {
  for (const on of [false, true]) {
    const h = new Harness({ MIN_PLAYERS: 1, TICK_MS: 33, REPLAY_FULL_MAP: on }, { replay: new Replay(recording()) });
    const me = h.join('Me');
    me.take();
    me.pressReady();
    me.send(build.mready(7, 2));
    assert.equal(h.room.state, STATE.RUNNING);
    const atStart = me.take().filter((p) => p[0] !== T.LOBBY_CHAT && p[0] !== T.READY && p[0] !== T.TYPE);
    if (on) {
      assert.equal(atStart.length, 1, 'one standalone frame');
      assert.ok(atStart[0].equals(build.cheat(0, 0)), 'CHEAT(0, 0) = full map view (F28)');
    } else {
      assert.equal(atStart.length, 0, 'nothing without the option');
    }
    h.stepAfter(33);
    const f = me.take();
    assert.ok(f[0].equals(recordedPayloads()[0]), 'the first recorded frame is unchanged');
  }
});

test('a start shuffle that differs from the recording is logged', () => {
  const out = [];
  const log = createLogger('info', { write: (s) => out.push(JSON.parse(s)) });
  const h = new Harness({ MIN_PLAYERS: 1, TICK_MS: 33 }, { replay: new Replay(recording()), log });
  const me = h.join('Me');
  me.take();
  me.pressReady();
  me.send(build.mready(3, 2));
  assert.equal(h.room.state, STATE.RUNNING);
  const warn = out.find((o) => o.lvl === 'warn' && o.msg.startsWith('replay: start shuffle differs'));
  assert.ok(warn);
  assert.equal(warn.recorded, 7);
  assert.equal(warn.client, 3);
});

test('loadReplay, seat choice, replayConfig and the single-room pool', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dc-replay-'));
  try {
    const file = path.join(dir, 'game.jsonl');
    fs.writeFileSync(file, `${recording().map((l) => JSON.stringify(l)).join('\n')}\n`);
    const rp = loadReplay(file);
    assert.equal(rp.seat, 4, 'the first recorded real player');
    assert.equal(rp.seatName, 'Kamyck');
    assert.equal(rp.seatWasFake, false);
    const s = rp.summary();
    assert.equal(s.frames, 5);
    assert.equal(s.lastUntil, 15);
    assert.equal(s.clientChecksums, 2);
    assert.equal(s.end.reason, 'room reset');
    assert.throws(() => loadReplay(file, { slot: 1 }), /REPLAY_SLOT 1 was not a human/);
    const fakeSeat = loadReplay(file, { slot: 2 });
    assert.equal(fakeSeat.seatName, 'AI Marauder');
    assert.ok(fakeSeat.seatWasFake, 'a bot\'s seat can be taken too');
    const cfg = replayConfig(loadConfig({}, { SYNC_CHECK: 'send', TICK_MS: 44, HALL: true }), rp);
    assert.equal(cfg.HALL, false);
    assert.equal(cfg.SYNC_CHECK, 'shadow');
    assert.equal(cfg.TICK_MS, 33);
    assert.equal(cfg.ROOM_LIST[0].file, 'D8PLAY01.SCN');
    const pool = new RoomPool(cfg, silentLogger, () => 0, () => 0, { replay: rp });
    assert.equal(pool.rooms.length, 1);
    assert.equal(pool.rooms[0].replay, rp);
    assert.equal(pool.rooms[0].map.name, 'Armageddon');
    // an unknown map keeps the recorded name and terrain
    const lines = recording();
    lines[0].map = { file: 'X8CUSTOM.SCN', name: 'Custom', terrain: 'jungle', players: 8 };
    const custom = new Replay(lines);
    assert.equal(custom.map.name, 'Custom');
    assert.equal(custom.map.terrain, 'jungle');
    assert.ok(custom.map.titleWire.includes('Jungle Map'));
    assert.throws(() => new Replay(recording().filter((l) => l.type !== 'frame')), /no sync frames/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// REPLAY ONLINE GAME (plan §21, protocol doc §4.5): the messages, the recordings index with its
// retention, the hall's RLIST / RPLAY dialogue into a viewer room, refusals, and the bot name prefix.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { HallHarness } from './helpers.js';
import { T, build, splitCommands, decode, MAX_REPLAY_ROW } from '../src/commands.js';
import { Replays, describeRecording, rowText, lengthText, HEADER, COLUMNS, STALE_MS } from '../src/replays.js';
import { loadConfig } from '../src/config.js';
import { SLOT_TYPE, STATE } from '../src/constants.js';

const hex = (...bufs) => Buffer.concat(bufs).toString('hex');

/** A recorded battle on Armageddon: the fake host in 0, a bot in 2, the real players Kamyck (4) and Plink (6), an AI slot 7. */
function recording(t = '2026-10-02T14:54:03.000Z', frames = 5, mapName = 'Armageddon') {
  const empty = (s) => ({ type: SLOT_TYPE.EMPTY, race: 0, colour: s, team: s, name: '' });
  const slots = [
    { type: SLOT_TYPE.HUMAN, race: 0, colour: 0, team: 0, name: 'AI Mercenary' },
    empty(1),
    { type: SLOT_TYPE.HUMAN, race: 1, colour: 2, team: 2, name: 'AI Marauder' },
    empty(3),
    { type: SLOT_TYPE.HUMAN, race: 1, colour: 5, team: 4, name: 'Kamyck' },
    empty(5),
    { type: SLOT_TYPE.HUMAN, race: 0, colour: 6, team: 6, name: 'Plink' },
    { type: SLOT_TYPE.AI_EASY, race: 0, colour: 7, team: 7, name: '' },
  ];
  const lines = [
    {
      type: 'start',
      version: 1,
      map: { file: 'D8PLAY01.SCN', name: mapName, terrain: 'desert', players: 8 },
      tickMs: 44,
      lookahead: 8,
      syncCheck: 'send',
      mercenarySlot: 0,
      lobby: { slots, localSlot: -1, titleDigit: 8 },
      players: [{ slot: 4, name: 'Kamyck' }, { slot: 6, name: 'Plink' }],
      t,
    },
    { type: 'mready', slot: 4, gamePlayer: 7 },
  ];
  for (let k = 0; k < frames; k++) lines.push({ type: 'frame', a: k, until: 9 + k, cmds: k ? hex(build.sync(0x1000 + k, 8 + k)) : hex(build.tickSpeed(44)) });
  lines.push({ type: 'end', reason: 'room reset', engineTime: 9 + frames, mismatches: 0, disabled: null });
  return lines;
}

function writeRecording(dir, name, lines) {
  const file = path.join(dir, name);
  fs.writeFileSync(file, `${lines.map((l) => JSON.stringify(l)).join('\n')}\n`);
  return file;
}

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'dc-replays-'));

test('messages: RLIST, REPLAYS, REPLAY, RPLAY and REPLAYING round-trip through split and decode', () => {
  const e = { id: 7, seats: 8, players: 2, bots: 3, real: 0b01010000, durationS: 754, row: '02.10 14:54 Armageddo Desert 8 2 3 12:34', names: ['AI Mercenary', '', 'AI Marauder', '', 'Kamyck', '', 'Plink', ''] };
  const buf = Buffer.concat([build.rlist(), build.replays(1, HEADER), build.replay(e), build.rplay(7, 4), build.replaying(4), build.keepalive()]);
  const cmds = splitCommands(buf);
  assert.deepEqual(cmds.map((c) => c.type), [T.RLIST, T.REPLAYS, T.REPLAY, T.RPLAY, T.REPLAYING, T.KEEPALIVE]);
  assert.deepEqual(decode(cmds[1]), { count: 1, header: HEADER });
  assert.deepEqual(decode(cmds[2]), e);
  assert.deepEqual(decode(cmds[3]), { id: 7, slot: 4 });
  assert.deepEqual(decode(cmds[4]), { slot: 4 });
  assert.equal(build.replay({ ...e, row: 'x'.repeat(60), names: ['y'.repeat(30), '', '', '', '', '', '', ''] }).length, 1 + 5 + 2 + (MAX_REPLAY_ROW + 1) + (16 + 1) + 7, 'row and names are cut to their limits');
  assert.throws(() => splitCommands(Buffer.from([T.REPLAY, 1, 8, 2, 1, 0, 0, 0, 0x41])), /unterminated|truncated/);
});

test('rowText: 40 monospace columns - date, UTC time, map, terrain, seats, players, AI, length; header in the same columns', () => {
  assert.equal(COLUMNS.reduce((n, [, w]) => n + w, 0) + COLUMNS.length - 1, 40);
  assert.equal(HEADER, 'DATE  UTC   MAP       TERR.  S P A  M:SS');
  const row = rowText({ recordedAt: new Date('2026-10-02T14:54:03Z'), map: 'Armageddon', terrain: 'desert', seats: 8, players: 2, bots: 3, durationS: 754 });
  assert.equal(row, '02.10 14:54 Armageddo Desert 8 2 3 12:34');
  assert.ok(row.length <= MAX_REPLAY_ROW);
  assert.equal(rowText({ recordedAt: new Date('2026-01-09T03:07:59Z'), map: 'Plink - O', terrain: 'jungle', seats: 8, players: 1, bots: 1, durationS: 6005 }), '09.01 03:07 Plink - O Jungle 8 1 1  1h40');
  assert.equal(lengthText(59), ' 0:59');
  assert.equal(lengthText(3600), '60:00');
  assert.equal(lengthText(5999), '99:59');
  assert.equal(lengthText(36000), '10h00');
});

test('describeRecording: participants = the human slots, real players flagged, bots = fakes + AI slots, duration from the last frame', () => {
  const dir = tmp();
  const file = writeRecording(dir, 'a.jsonl', recording());
  const e = describeRecording(file);
  assert.equal(e.map, 'Armageddon');
  assert.equal(e.terrain, 'desert');
  assert.equal(e.seats, 8);
  assert.equal(e.players, 2, 'Kamyck and Plink');
  assert.equal(e.bots, 3, 'two fakes and the AI slot');
  assert.deepEqual(e.names, ['AI Mercenary', '', 'AI Marauder', '', 'Kamyck', '', 'Plink', '']);
  assert.equal(e.real, (1 << 4) | (1 << 6));
  assert.equal(e.frames, 5);
  assert.equal(e.durationS, Math.round((13 * 44) / 1000));
  assert.equal(e.row, '02.10 14:54 Armageddo Desert 8 2 3  0:01');
  // no battle: nothing to watch
  assert.equal(describeRecording(writeRecording(dir, 'b.jsonl', recording('2026-10-02T15:00:00Z', 0))), null);
  fs.writeFileSync(path.join(dir, 'c.jsonl'), 'not json\n');
  assert.equal(describeRecording(path.join(dir, 'c.jsonl')), null);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('Replays: scan indexes newest first with stable ids, onRecorded adds, the retention keeps the newest battles and drops stale non-battles', () => {
  const dir = tmp();
  writeRecording(dir, '2026-10-01T10-00-00-000Z-room1-D8PLAY01.jsonl', recording('2026-10-01T10:00:00Z', 3, 'Old one'));
  writeRecording(dir, '2026-10-02T10-00-00-000Z-room2-J8PLAY01.jsonl', recording('2026-10-02T10:00:00Z', 4, 'Middle'));
  writeRecording(dir, '2026-10-02T12-00-00-000Z-room3-D8PLAY02.jsonl', recording('2026-10-02T12:00:00Z', 5, 'Newest'));
  const aborted = writeRecording(dir, '2026-10-02T13-00-00-000Z-room1-D8PLAY05.jsonl', recording('2026-10-02T13:00:00Z', 0, 'Aborted')); // no frames: not a battle
  const stale = writeRecording(dir, '2026-09-20T13-00-00-000Z-room1-D8PLAY05.jsonl', recording('2026-09-20T13:00:00Z', 0, 'Stale'));
  const old = (Date.now() - STALE_MS - 60000) / 1000;
  fs.utimesSync(stale, old, old);
  const r = new Replays(dir, 2).scan();
  assert.deepEqual(r.list.map((e) => e.map), ['Newest', 'Middle'], 'two battles kept, the oldest battle deleted');
  assert.ok(!fs.existsSync(path.join(dir, '2026-10-01T10-00-00-000Z-room1-D8PLAY01.jsonl')));
  assert.ok(fs.existsSync(aborted), 'a fresh file without frames may be a battle being recorded: kept');
  assert.ok(!fs.existsSync(stale), 'a stale file without frames is deleted');
  assert.equal(fs.readdirSync(dir).length, 3);
  const ids = r.list.map((e) => e.id);
  assert.equal(new Set(ids).size, 2);
  assert.equal(r.get(ids[0]).map, 'Newest');
  assert.equal(r.get(99), null);
  // a battle ends: indexed, newest first, the retention applied again
  const f = writeRecording(dir, '2026-10-02T14-00-00-000Z-room4-D8PLAY03.jsonl', recording('2026-10-02T14:00:00Z', 2, 'Latest'));
  const e = r.onRecorded(f);
  assert.equal(e.map, 'Latest');
  assert.deepEqual(r.list.map((x) => x.map), ['Latest', 'Newest'], 'Middle fell off the end');
  assert.equal(fs.readdirSync(dir).length, 3, 'two battles and the fresh non-battle');
  const cmds = splitCommands(Buffer.concat(r.payloads()));
  assert.equal(cmds[0].type, T.REPLAYS);
  assert.deepEqual(decode(cmds[0]), { count: 2, header: HEADER });
  assert.deepEqual(cmds.slice(1).map((c) => decode(c).row.slice(12, 21).trim()), ['Latest', 'Newest']);
  // a missing directory is no error (the recorder creates it with the first battle)
  assert.equal(new Replays(path.join(dir, 'none'), 50).scan().list.length, 0);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('hall: RLIST lists the recordings (no lobby view, no room table); RPLAY refusals; RPLAY seats the viewer in a viewer room that plays and closes', () => {
  const dir = tmp();
  writeRecording(dir, '2026-10-02T12-00-00-000Z-room3-D8PLAY02.jsonl', recording('2026-10-02T12:00:00Z', 5));
  const h = new HallHarness({ RECORD_DIR: dir, HALL_REFRESH_MS: 100 });
  assert.equal(h.pool.replays.list.length, 1);
  const id = h.pool.replays.list[0].id;
  const p = h.enter('Exe');
  p.take(); // the hall dump the module ignores
  p.send(build.rlist());
  let cmds = p.takeCmds();
  assert.equal(cmds[0].type, T.REPLAYS);
  assert.equal(decode(cmds[0]).count, 1);
  assert.equal(cmds[1].type, T.REPLAY);
  assert.equal(decode(cmds[1]).id, id);
  assert.deepEqual(decode(cmds[1]).names[4], 'Kamyck');
  h.stepAfter(200);
  assert.equal(p.take().length, 0, 'a replay browser gets no room table and no lobby rows');
  // refusals
  p.send(build.rplay(id + 100, 4));
  cmds = p.takeCmds();
  assert.equal(cmds[0].type, T.REFUSED);
  assert.match(decode(cmds[0]).reason, /no longer on the server/);
  p.send(build.rplay(id, 1));
  assert.match(decode(p.takeCmds()[0]).reason, /one of the players/);
  p.send(build.rplay(id, 7));
  assert.match(decode(p.takeCmds()[0]).reason, /one of the players/, 'an AI slot is not a seat');
  assert.equal(h.hall.clients.size, 1, 'still in the hall');
  // the real thing: watch as Kamyck (slot 4)
  p.send(build.rplay(id, 4));
  cmds = p.takeCmds();
  assert.equal(cmds[0].type, T.REPLAYING);
  assert.equal(decode(cmds[0]).slot, 4);
  const v = cmds.find((c) => c.type === T.VERSION);
  assert.ok(v, "the stock join sequence ('d') follows in the same write");
  assert.equal(decode(v).id, 4);
  assert.equal(h.hall.clients.size, 0);
  assert.equal(h.pool.viewers.length, 1);
  const room = h.pool.viewers[0];
  assert.equal(room.id, 101);
  assert.equal(room.replay.seat, 4);
  assert.equal(room.config.SYNC_CHECK, 'off', 'no engine for a viewer');
  assert.equal(room.config.RECORD_DIR, '', 'a viewing is not recorded again');
  assert.equal(room.config.TICK_MS, 44, 'the recorded speed');
  assert.equal(room.slots[4].client.socket, p.sock);
  assert.equal(room.slots[4].race, 1, 'pinned to the recording');
  assert.deepEqual(room.slots.filter((s) => s.fake).map((s) => s.name), ['AI Mercenary', 'AI Marauder', 'Plink'], 'the other humans are fakes');
  assert.ok(h.pool.all().includes(room), 'the pool drives it');
  // READY starts the playback: the first recorded frame goes out byte for byte.  The game's own stream
  // through the exe's proxy starts at sequence 0 (F80), as the relay expects after REPLAYING
  p.seq = 0;
  p.send(build.ready(2, 4));
  assert.equal(room.state, STATE.STARTING);
  p.send(build.mready(7, 2));
  assert.equal(room.state, STATE.RUNNING);
  h.stepAfter(100);
  const frames = p.takeCmds().filter((c) => c.type === T.UNTIL);
  assert.ok(frames.length >= 1, 'recorded frames flow');
  // the recording ends: the relay closes the connection once the viewer has executed the last frame (maintainer,
  // 2 Oct 2026: "when replay ends then relay must close a connection"), and the room is gone
  for (const f of frames) p.send(build.until(decode(f).a, decode(f).until));
  h.stepAfter(2000);
  for (const f of p.takeCmds().filter((c) => c.type === T.UNTIL)) p.send(build.until(decode(f).a, decode(f).until)); // the game echoes everything
  assert.ok(room.game.replayDone, 'all five recorded frames sent');
  h.advance(1000);
  h.tick();
  assert.equal(h.pool.viewers.length, 1, 'the viewer has not reported the last frame yet: still connected');
  p.send(build.until(-1, room.game.lastIssuedUntil)); // the progress report of the last recorded tick
  h.tick();
  assert.ok(p.gone, 'connection closed');
  assert.equal(h.pool.viewers.length, 0, 'the viewer room is gone');
  assert.equal(h.pool.all().length, h.pool.rooms.length);
  // a viewer that stops reporting is closed REPLAY_END_GRACE_MS after the last frame
  const p2 = h.enter('Exe2');
  p2.take();
  p2.send(build.rlist());
  p2.takeCmds();
  p2.send(build.rplay(id, 4));
  p2.takeCmds();
  p2.seq = 0;
  p2.send(build.ready(2, 4));
  p2.send(build.mready(7, 2));
  const room2 = h.pool.viewers[0];
  h.stepAfter(2000);
  for (const f of p2.takeCmds().filter((c) => c.type === T.UNTIL)) p2.send(build.until(decode(f).a, decode(f).until));
  assert.ok(room2.game.replayDone);
  h.advance(h.cfg.REPLAY_END_GRACE_MS - 1000);
  h.tick();
  assert.equal(h.pool.viewers.length, 1, 'within the grace: still connected');
  h.advance(2000);
  h.tick();
  assert.ok(p2.gone, 'closed after the grace');
  assert.equal(h.pool.viewers.length, 0);
  assert.equal(h.pool.all().length, h.pool.rooms.length);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('hall without RECORD_DIR: RLIST answers an empty list', () => {
  const h = new HallHarness();
  assert.equal(h.pool.replays, null);
  const p = h.enter('Exe');
  p.take();
  p.send(build.rlist());
  const cmds = p.takeCmds();
  assert.equal(cmds.length, 1);
  assert.deepEqual(decode(cmds[0]), { count: 0, header: '' });
  p.send(build.rplay(1, 0));
  assert.equal(p.takeCmds()[0].type, T.REFUSED);
});

test('every bot name carries the AI prefix (maintainer, 2 Oct 2026); REPLAY_KEEP is checked', () => {
  const cfg = loadConfig({});
  assert.equal(cfg.MERCENARY_NAME, 'AI Mercenary');
  assert.ok(cfg.FAKE_NAME_POOL.every((n) => n.startsWith('AI ')), cfg.FAKE_NAME_POOL.join(','));
  assert.equal(cfg.REPLAY_KEEP, 50);
  assert.throws(() => loadConfig({}, { REPLAY_KEEP: 0 }), /REPLAY_KEEP/);
});

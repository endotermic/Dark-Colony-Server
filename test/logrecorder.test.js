// RECORD_LOG (src/logrecorder.js, plan §18.6): a battle recording as compact log lines that
// tools/logs2replay.js turns back into a file recording for tools/replay.js.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createLogger } from '../src/log.js';
import { build } from '../src/commands.js';
import { LogRecorder, teeRecorders, parseLogLine, serverLinesOf, decodeLogRecordings, recordingFileName } from '../src/logrecorder.js';
import { sinceMs } from '../tools/logs2replay.js';

function capture() {
  const lines = [];
  const log = createLogger('info', { write: (s) => lines.push(s.trimEnd()) });
  return { lines, log };
}

const HEADER = {
  version: 1,
  map: { file: 'J8PLAY01.SCN', name: 'Jungle', terrain: 'jungle', players: 8 },
  tickMs: 44,
  lookahead: 8,
  syncCheck: 'send',
  mercenarySlot: 0,
  lobby: { slots: [{ type: 2, race: 0, colour: 0, team: 0, name: 'AI Mercenary' }], localSlot: -1, titleDigit: 8 },
  players: [{ slot: 3, name: 'Tester' }],
};

/** A battle as synccheck.js writes it: `frames` frames, a 0x08 in every frame after the first, some orders. */
function battle(frames) {
  const events = [{ type: 'start', ...HEADER }, { type: 'mready', slot: 3, gamePlayer: 5 }];
  let until = 9;
  for (let i = 0; i < frames; i++) {
    let a = until - 9;
    if (i === 0) until = 10; // the first frame of the 18 Sep 2026 recording was UNTIL(0, 10)
    if (i === 700) until += 3; // a stall: until jumps
    if (i === 701) a -= 2; // and an odd a
    const parts = [];
    if (i > 0) parts.push(build.sync((until * 7 + 3) & 0xffff, until - 1));
    if (i === 0) parts.push(build.tickSpeed(44));
    if (i % 37 === 5) parts.push(build.orderSelected(1, 2));
    if (i === 800) parts.push(Buffer.from('0e' + Buffer.from('AI Mercenary: hello', 'latin1').toString('hex') + '00', 'hex'));
    events.push({ type: 'frame', a, until, cmds: Buffer.concat(parts).toString('hex') });
    for (let t = until - 9; t <= until; t++) events.push({ type: 'engine', tick: t, checksum: (t * 7 + 3) & 0xffff });
    if (i > 10) {
      events.push({ type: 'rx08', slot: 3, tick: until - 2, checksum: (until - 2) * 7 + 3 });
      events.push({ type: 'rx08', slot: 5, tick: until - 2, checksum: (until - 2) * 7 + 3 });
    }
    if (i === 900) events.push({ type: 'mismatch', tick: until - 2, engine: 1, client: 2, slot: 3 });
    until++;
  }
  events.push({ type: 'left', slot: 3, reason: 'socket closed', tick: until, state: 'RUNNING' });
  const end = { reason: 'room reset', engineTime: until, sentTicks: frames - 1, compared: frames - 11, mismatches: 1, firstMismatch: null, disabled: null };
  return { events, end };
}

function record(events, end, opts = {}) {
  const { lines, log } = capture();
  const rec = new LogRecorder(log, { now: () => 1789700000000, ...opts });
  rec.open(1, events[0]);
  for (const e of events.slice(1)) rec.write(e);
  rec.close(end);
  return { lines, rec };
}

test('a battle becomes a few dozen short log lines: checksums as hex runs, no per-tick lines', () => {
  const { events, end } = battle(1200);
  const { lines, rec } = record(events, end);
  assert.equal(rec.rec, null, 'closed');
  assert.ok(lines.length <= 16, `${lines.length} lines for 1200 frames`);
  for (const l of lines) assert.ok(l.length < 4000, `line of ${l.length} chars`);
  const objs = lines.map(parseLogLine);
  assert.ok(objs.every((o) => o.msg === 'replay' && o.lvl === 'info'));
  assert.deepEqual(objs.map((o) => o.seq), objs.map((_, i) => i), 'seq 0, 1, 2, ...');
  assert.equal(objs[0].ev, 'start');
  assert.equal(objs[0].map.file, 'J8PLAY01.SCN');
  assert.equal(objs[1].ev, 'mready');
  assert.equal(objs.at(-1).ev, 'end');
  assert.equal(objs.at(-1).reason, 'room reset');
  const chunks = objs.filter((o) => o.ev === 'frames');
  assert.equal(chunks.reduce((n, c) => n + c.n, 0), 1200);
  assert.equal(chunks[0].u0, 10);
  assert.equal(chunks[0].look, 9, 'from the header, not from the odd first frame');
  assert.ok(chunks.every((c) => !JSON.stringify(c.c ?? []).includes('"08')), 'the server checksums are not in the command lists');
  assert.ok(chunks.every((c) => (c.s ?? []).length >= 1), 'but in hex runs');
  assert.equal(chunks[0].s[0][0], 1, 'from frame 1 on (frame 0 carries none)');
  assert.equal(chunks[0].s[0][1].length, 4 * (chunks[0].n - 1), 'one 16-bit value per frame, one run');
  assert.ok(chunks.some((c) => (c.r ?? []).length >= 1), 'client checksums as runs');
  assert.ok(chunks.flatMap((c) => c.r ?? []).every(([, slot]) => slot === 3), 'the first sender only');
  assert.ok(!objs.some((o) => o.ev === 'engine' || o.ev === 'rx08'), 'per-tick events are gone');
  // the mismatch event sits between the chunks in order, the stall is an irregular frame
  const evs = objs.map((o) => o.ev);
  assert.ok(evs.indexOf('mismatch') > evs.indexOf('frames'));
  assert.ok(evs.indexOf('mismatch') < evs.lastIndexOf('frames'));
  assert.deepEqual(chunks[0].x, [[0, 0, 10]], 'the odd first frame is the only irregular one of its chunk');
  assert.ok(chunks.some((c) => (c.x ?? []).length === 2), 'the stall: two irregular frames');
});

test('the log lines decode back into the file recording: frames byte for byte, every client checksum', () => {
  const { events, end } = battle(1200);
  const { lines } = record(events, end);
  const recs = decodeLogRecordings(lines.map(parseLogLine));
  assert.equal(recs.size, 1);
  const r = [...recs.values()][0];
  assert.deepEqual(r.gaps, []);
  assert.equal(r.frames, 1200);
  const start = r.lines[0];
  assert.equal(start.type, 'start');
  assert.equal(start.tickMs, 44);
  assert.deepEqual(start.lobby, HEADER.lobby);
  assert.equal(start.rec, r.rec);
  const want = events.filter((e) => e.type === 'frame');
  const got = r.lines.filter((l) => l.type === 'frame');
  assert.deepEqual(got, want, 'every frame exactly as broadcast: a, until, 0x08 and commands');
  const rx = r.lines.filter((l) => l.type === 'rx08');
  const wantRx = events.filter((e) => e.type === 'rx08' && e.slot === 3).map((e) => ({ ...e, checksum: e.checksum & 0xffff }));
  assert.deepEqual(rx, wantRx, 'every client checksum of the first sender, none of the duplicate sender');
  const mismatch = events.find((e) => e.type === 'mismatch');
  assert.ok(r.lines.some((l) => l.type === 'mismatch' && l.tick === mismatch.tick && l.client === 2));
  assert.ok(r.lines.some((l) => l.type === 'left' && l.reason === 'socket closed'));
  assert.equal(r.lines.at(-1).type, 'end');
  assert.equal(r.lines.at(-1).mismatches, 1);
  assert.equal(recordingFileName(r), `${String(r.startedAt).replace(/[:.]/g, '-')}-roomx-J8PLAY01.jsonl`);
});

test('a dropped log line is reported and its frames are reconstructed empty', () => {
  const { events, end } = battle(1200);
  const { lines } = record(events, end);
  const objs = lines.map(parseLogLine);
  const victim = objs.findIndex((o, i) => o.ev === 'frames' && objs[i - 1]?.ev === 'frames');
  const dropped = objs[victim];
  objs.splice(victim, 1);
  const r = [...decodeLogRecordings(objs).values()][0];
  assert.deepEqual(r.gaps, [[dropped.seq, dropped.seq]]);
  // the dropped chunk held the stall (until jumps by 3), so the empty reconstruction has 3 frames more
  assert.equal(r.frames, 1200 + (dropped.u0 <= 9 + 700 && 9 + 700 < dropped.u0 + dropped.n ? 3 : 0), 'the timeline is complete');
  const frames = r.lines.filter((l) => l.type === 'frame');
  assert.equal(frames.at(-1).until, events.filter((e) => e.type === 'frame').at(-1).until);
  const jumps = frames.slice(1).map((f, i) => f.until - frames[i].until).filter((d) => d !== 1);
  assert.deepEqual(jumps, [4], 'consecutive untils apart from the recorded stall');
  const intact = [...decodeLogRecordings(lines.map(parseLogLine)).values()][0].lines.filter((l) => l.type === 'frame');
  assert.deepEqual(frames.filter((f) => f.until < dropped.u0), intact.filter((f) => f.until < dropped.u0), 'frames before the gap untouched');
  assert.deepEqual(frames.filter((f) => f.until > dropped.u0 + dropped.n + 3), intact.filter((f) => f.until > dropped.u0 + dropped.n + 3), 'frames after the gap untouched');
  assert.ok(frames.filter((f) => f.until >= dropped.u0 && f.until < dropped.u0 + dropped.n).every((f) => f.cmds === ''), 'the lost frames are empty');
  assert.ok(r.lines.filter((l) => l.type === 'note').length >= 2, 'the gap and the reconstruction are noted');
});

test('parseLogLine / serverLinesOf read raw stdout, fly logs text, fly logs --json and Logs API documents', () => {
  const raw = '{"t":"2026-09-18T11:13:06.543Z","lvl":"info","msg":"replay","room":2,"rec":"abc","seq":0,"ev":"start","tickMs":44}';
  const flyText = `[2m2026-09-18T11:13:06Z[0m app[d8927e5c5ee3d8] [32miad[0m [[34minfo[0m]${raw}`;
  assert.deepEqual(parseLogLine(raw), JSON.parse(raw));
  assert.deepEqual(parseLogLine(flyText), JSON.parse(raw));
  assert.equal(parseLogLine('2026-09-18 proxy[abc] iad [info] Connection closed'), null);
  const api = JSON.stringify({ data: [{ id: '1', type: 'logs', attributes: { timestamp: 'x', message: raw, level: 'info' } }, { attributes: { message: 'not json' } }], meta: { next_token: '1' } });
  const flyJson = `{\n  "level": "info",\n  "message": ${JSON.stringify(raw)},\n  "meta": { "Event": { "Provider": "app" } }\n}\n{\n  "message": "plain"\n}\n`;
  for (const text of [`${raw}\n`, `${flyText}\nother line\n`, api, flyJson]) {
    const objs = serverLinesOf(text);
    assert.equal(objs.length, 1, text.slice(0, 30));
    assert.equal(objs[0].rec, 'abc');
  }
  assert.deepEqual(serverLinesOf(`${raw}\n${raw}\n`).length, 2);
  assert.equal(sinceMs('7d', 10 * 86400e3), 3 * 86400e3);
  assert.equal(sinceMs('36h', 2 * 86400e3), 12 * 3600e3);
  assert.equal(sinceMs('2026-09-12T00:00:00Z'), Date.parse('2026-09-12T00:00:00Z'));
});

test('teeRecorders feeds a file recorder and the log recorder alike', () => {
  const seen = [];
  const fake = { active: false, file: '/tmp/x.jsonl', open: (room, h) => (seen.push(['open', room, h.tickMs]), 'f'), write: (o) => seen.push(o.type), close: (e) => seen.push(['close', e?.reason]) };
  const { lines, log } = capture();
  const tee = teeRecorders([null, fake, new LogRecorder(log)]);
  assert.equal(tee.list.length, 2);
  assert.equal(tee.open(3, HEADER), 'f');
  tee.write({ type: 'frame', a: 0, until: 9, cmds: '' });
  tee.close({ reason: 'x' });
  assert.deepEqual(seen, [['open', 3, 44], 'frame', ['close', 'x']]);
  assert.equal(tee.file, '/tmp/x.jsonl');
  assert.equal(lines.length, 3, 'start, frames, end');
  assert.equal(teeRecorders([null]), null);
  assert.equal(teeRecorders([fake]), fake);
});

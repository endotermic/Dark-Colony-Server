// Recording through the log (RECORD_LOG, 18 Sep 2026; plan §18.6, F50).
//
// Fly keeps a machine's stdout for about seven days (Logs API) and its root file system for
// nothing, so a battle that must be diagnosable after a player's report has to leave the log. The
// file recorder's format (src/recorder.js) is far too fat for that - a line per tick for the engine
// checksum, one per tick for the client checksum, one per frame: 1.2-1.7 MB for ten minutes.
// LogRecorder takes the same events and emits compact JSON log lines, `msg: "replay"`, that are
// LOSSLESS for everything the clients received: every sync frame as broadcast (UNTIL(a, until),
// the server's 0x08 checksum, the commands, byte for byte) and every 0x08 a client sent; only the
// engine's own per-tick checksum lines are left out (in `send` mode they are the 0x08 in the next
// frame anyway, otherwise tools/replay.js recomputes them). Each line carries
//   rec   recording id (start time in base 36 + "r" + room): the lines of one battle, and only
//         those, share it; several rooms record at the same time into the same log
//   seq   0, 1, 2, ... per recording; a gap means the log pipeline dropped a line
//   ev    start | frames | rx | mready | left | note | assert | mismatch | pause | resume | end
// `start`, `end` and the rare events carry the file recorder's fields unchanged. `frames` is a
// chunk of consecutive sync frames:
//   u0    until of the first frame, n = frames in the chunk, look = LOOKAHEAD + 1 (from the header)
//   x     [[i, a, until], ...] frames whose until is not the previous + 1 or whose a is not until - look
//   s     [[i0, hex], ...] runs of the server's 0x08: 4 hex characters (16-bit checksum) per frame
//         from frame i0 on, for frames whose 0x08 is the first command and carries tick = the
//         previous frame's until (the rule of synccheck.js); the run breaks at a frame without one
//   sx    [[i, checksum, tick], ...] a first-command 0x08 with any other tick
//   c     [[i, hex], ...] the rest of the frame's commands (everything after that 0x08; a 0x08 that
//         is not the first command stays here untouched)
//   r     [[tick0, slot, hex], ...] runs of client 0x08 checksums, 4 hex characters per tick from
//         tick0 on, first sender per tick; a run breaks at a missing tick or another slot
// A chunk closes after CHUNK_FRAMES frames or CHUNK_CHARS characters of command hex, so a line
// stays a few KB at most. tools/logs2replay.js turns the lines back into a file recording
// (decodeLogRecordings below), whose frames are the broadcast payloads exactly.

import { splitCommands, decode, build, T } from './commands.js';

export const CHUNK_FRAMES = 256;
export const CHUNK_CHARS = 3000;
const LOGGER_KEYS = new Set(['t', 'lvl', 'msg', 'rec', 'seq', 'ev', 'room']);

const hex16 = (v) => (v & 0xffff).toString(16).padStart(4, '0');

export class LogRecorder {
  constructor(log, opts = {}) {
    this.log = log;
    this.chunkFrames = opts.chunkFrames ?? CHUNK_FRAMES;
    this.chunkChars = opts.chunkChars ?? CHUNK_CHARS;
    this.now = opts.now ?? Date.now;
    this.rec = null;
    this.seq = 0;
    this.chunk = null;
    this.rx = []; // runs [tick0, slot, hex] not yet flushed
    this.rxLast = null; // { tick, slot } of the last kept client checksum
    this.lines = 0;
    this.look = null;
    this.prevUntil = null;
    this.file = null; // the file recorder's field, for callers that look at it
  }

  open(room, header) {
    this.close();
    this.rec = `${this.now().toString(36)}r${room}`;
    this.seq = 0;
    this.lines = 0;
    this.prevUntil = null;
    this.rxLast = null;
    // a regular frame has a = until - (LOOKAHEAD + 1); the first frame itself is not always one
    // (a recording of 18 Sep 2026 starts with UNTIL(0, 10)), so the reference comes from the header
    this.look = Number.isInteger(header?.lookahead) ? header.lookahead + 1 : null;
    this.emit('start', header);
    return this.rec;
  }

  get active() {
    return this.rec !== null;
  }

  write(obj) {
    if (this.rec === null) return;
    const { type, ...rest } = obj;
    if (type === 'engine') return; // the 0x08 of the next frame in send mode, recomputed otherwise
    if (type === 'frame') return this.addFrame(rest);
    if (type === 'rx08') return this.addRx(rest);
    this.flush();
    this.emit(type, rest);
  }

  addFrame({ a, until, cmds }) {
    let ch = this.chunk;
    if (!ch) ch = this.chunk = { u0: until, n: 0, look: this.look ?? until - a, x: [], s: [], sx: [], c: [], chars: 0, lastUntil: until - 1, sRun: null };
    const i = ch.n++;
    if (until !== ch.lastUntil + 1 || a !== until - ch.look) ch.x.push([i, a, until]);
    ch.lastUntil = until;
    let rest = cmds ?? '';
    let sync = null;
    if (rest.startsWith('08')) {
      try {
        const list = splitCommands(Buffer.from(rest, 'hex'));
        if (list.length && list[0].type === T.SYNC) {
          const d = decode(list[0]);
          sync = { checksum: d.checksum & 0xffff, tick: d.time };
          rest = rest.slice(list[0].raw.length * 2);
        }
      } catch {
        // unparsable: keep the raw bytes as they are
      }
    }
    if (sync && sync.tick === this.prevUntil) {
      if (ch.sRun && ch.sRun.next === i) {
        ch.sRun.run[1] += hex16(sync.checksum);
        ch.sRun.next++;
      } else {
        const run = [i, hex16(sync.checksum)];
        ch.s.push(run);
        ch.sRun = { run, next: i + 1 };
      }
    } else if (sync) {
      ch.sx.push([i, sync.checksum, sync.tick]);
      ch.sRun = null;
    } else {
      ch.sRun = null;
    }
    this.prevUntil = until;
    if (rest) {
      ch.c.push([i, rest]);
      ch.chars += rest.length;
    }
    if (ch.n >= this.chunkFrames || ch.chars >= this.chunkChars) this.flush();
  }

  addRx({ slot, tick, checksum }) {
    const last = this.rxLast;
    if (last && tick === last.tick) return; // the first sender of a tick counts
    if (last && tick === last.tick + 1 && slot === last.slot && this.rx.length) {
      this.rx.at(-1)[2] += hex16(checksum);
    } else {
      this.rx.push([tick, slot, hex16(checksum)]);
    }
    this.rxLast = { tick, slot };
  }

  /** Emit the open chunk (with the client checksums since the last one) or, without one, the checksums alone. */
  flush() {
    const ch = this.chunk;
    this.chunk = null;
    if (ch) {
      const fields = { u0: ch.u0, n: ch.n, look: ch.look };
      if (ch.x.length) fields.x = ch.x;
      if (ch.s.length) fields.s = ch.s;
      if (ch.sx.length) fields.sx = ch.sx;
      if (ch.c.length) fields.c = ch.c;
      if (this.rx.length) fields.r = this.rx;
      this.rx = [];
      this.emit('frames', fields);
    } else if (this.rx.length) {
      const r = this.rx;
      this.rx = [];
      this.emit('rx', { r });
    }
    // a client run never continues across lines: the decoder treats every run as self-contained
    if (this.rxLast) this.rxLast = { tick: this.rxLast.tick, slot: -1 };
  }

  emit(ev, fields) {
    this.log.info('replay', { rec: this.rec, seq: this.seq++, ev, ...fields });
    this.lines++;
  }

  close(end = null) {
    if (this.rec === null) return;
    this.flush();
    if (end) this.emit('end', end);
    this.rec = null;
  }
}

/** One recorder that feeds several (RECORD_DIR and RECORD_LOG together); null without any. */
export function teeRecorders(recorders) {
  const list = recorders.filter(Boolean);
  if (list.length === 0) return null;
  if (list.length === 1) return list[0];
  return {
    list,
    get active() {
      return list.some((r) => r.active);
    },
    get file() {
      return list.map((r) => r.file).find(Boolean) ?? null;
    },
    open(room, header) {
      return list.map((r) => r.open(room, header)).find((x) => x != null) ?? null;
    },
    write(obj) {
      for (const r of list) r.write(obj);
    },
    close(end = null) {
      for (const r of list) r.close(end);
    },
  };
}

/**
 * The server's JSON object in one log line, whatever wrapped it: the raw stdout line, a `fly logs`
 * text line (timestamp, machine, colour codes in front) or the `message` of a Logs API entry.
 * null for anything else.
 */
export function parseLogLine(line) {
  const s = String(line);
  const i = s.indexOf('{"t":');
  if (i < 0) return null;
  try {
    const o = JSON.parse(s.slice(i));
    return o && typeof o === 'object' && typeof o.msg === 'string' ? o : null;
  } catch {
    return null;
  }
}

/**
 * Every server log object in a text: the server's stdout or a `fly logs` capture (one line each),
 * a Logs API document ({ data: [{ attributes: { message } }] }) or the pretty-printed object
 * stream of `fly logs --json` (each object's `message`).
 */
export function serverLinesOf(text) {
  const s = String(text);
  const trimmed = s.trimStart();
  if (trimmed.startsWith('{') && !trimmed.startsWith('{"t":')) {
    const out = [];
    for (const o of jsonObjectsOf(trimmed)) {
      const entries = Array.isArray(o?.data) ? o.data : [o];
      for (const e of entries) {
        const m = e?.attributes?.message ?? e?.message;
        const parsed = typeof m === 'string' ? parseLogLine(m) : null;
        if (parsed) out.push(parsed);
      }
    }
    if (out.length) return out;
  }
  return s.split('\n').map(parseLogLine).filter(Boolean);
}

/** Concatenated (possibly pretty-printed) JSON objects in a string, in order. */
function jsonObjectsOf(s) {
  const out = [];
  let depth = 0;
  let start = -1;
  let inStr = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (inStr) {
      if (ch === '\\') i++;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '{') {
      if (depth++ === 0) start = i;
    } else if (ch === '}' && --depth === 0) {
      try {
        out.push(JSON.parse(s.slice(start, i + 1)));
      } catch {
        // skip a broken object (a truncated capture)
      }
    }
  }
  return out;
}

/** The 16-bit values of a run's hex string. */
function values16(hex) {
  const out = [];
  for (let i = 0; i + 4 <= hex.length; i += 4) out.push(parseInt(hex.slice(i, i + 4), 16));
  return out;
}

/**
 * Rebuild file recordings from the server's log objects (parseLogLine / serverLinesOf). Returns a
 * Map rec -> { rec, room, startedAt, lines (the file recorder's lines: the frames are the broadcast
 * payloads after UNTIL exactly), gaps ([from, to] of missing seq), frames, lastUntil, events }.
 * Frames lost with a dropped line between two chunks are reconstructed empty (the timeline stays
 * intact, their commands are gone) and a `note` line says so; a lost first chunk cannot be (the
 * first frame's until is not always LOOKAHEAD + 1).
 */
export function decodeLogRecordings(objs) {
  const byRec = new Map();
  for (const o of objs) {
    if (!o || o.msg !== 'replay' || typeof o.rec !== 'string' || !Number.isInteger(o.seq)) continue;
    let m = byRec.get(o.rec);
    if (!m) byRec.set(o.rec, (m = new Map()));
    if (!m.has(o.seq)) m.set(o.seq, o);
  }
  const out = new Map();
  for (const [rec, m] of byRec) {
    const r = { rec, room: null, startedAt: null, lines: [], gaps: [], frames: 0, lastUntil: null, events: {} };
    let expected = 0;
    let look = null;
    for (const seq of [...m.keys()].sort((x, y) => x - y)) {
      const o = m.get(seq);
      r.room ??= o.room ?? null;
      if (seq !== expected) {
        r.gaps.push([expected, seq - 1]);
        r.lines.push({ type: 'note', text: `log lines seq ${expected}..${seq - 1} missing (dropped by the log pipeline)` });
      }
      expected = seq + 1;
      const fields = {};
      for (const [k, v] of Object.entries(o)) if (!LOGGER_KEYS.has(k)) fields[k] = v;
      r.events[o.ev] = (r.events[o.ev] ?? 0) + 1;
      if (o.ev === 'start') {
        r.startedAt = o.t ?? null;
        r.lines.push({ type: 'start', rec, loggedAt: o.t, ...fields });
        look = Number.isInteger(o.lookahead) ? o.lookahead + 1 : null;
      } else if (o.ev === 'frames') {
        const ch = fields;
        look = ch.look;
        if (r.lastUntil !== null && ch.u0 > r.lastUntil + 1) {
          r.lines.push({ type: 'note', text: `frames until ${r.lastUntil + 1}..${ch.u0 - 1} reconstructed empty: their log line is missing` });
          for (let u = r.lastUntil + 1; u < ch.u0; u++) r.lines.push({ type: 'frame', a: u - look, until: u, cmds: '' });
          r.frames += ch.u0 - 1 - r.lastUntil;
        }
        const x = new Map((ch.x ?? []).map(([i, a, u]) => [i, [a, u]]));
        const c = new Map(ch.c ?? []);
        const sync = new Map((ch.sx ?? []).map(([i, checksum, tick]) => [i, { checksum, tick }]));
        for (const [i0, hex] of ch.s ?? []) values16(hex).forEach((checksum, k) => sync.set(i0 + k, { checksum, tick: null }));
        let until = ch.u0 - 1;
        for (let i = 0; i < ch.n; i++) {
          const irr = x.get(i);
          const prevUntil = until;
          until = irr ? irr[1] : until + 1;
          const a = irr ? irr[0] : until - look;
          const s = sync.get(i);
          const head = s ? build.sync(s.checksum, s.tick ?? prevUntil).toString('hex') : '';
          r.lines.push({ type: 'frame', a, until, cmds: head + (c.get(i) ?? '') });
        }
        r.frames += ch.n;
        r.lastUntil = until;
        pushRx(r.lines, ch.r);
      } else if (o.ev === 'rx') {
        pushRx(r.lines, fields.r);
      } else {
        r.lines.push({ type: o.ev, ...fields });
      }
    }
    out.set(rec, r);
  }
  return out;
}

function pushRx(lines, runs) {
  for (const [tick0, slot, hex] of runs ?? []) values16(hex).forEach((checksum, k) => lines.push({ type: 'rx08', slot, tick: tick0 + k, checksum }));
}

/** File name in the file recorder's style for a decoded recording. */
export function recordingFileName(r) {
  const start = r.lines.find((l) => l.type === 'start');
  const stamp = String(r.startedAt ?? new Date(0).toISOString()).replace(/[:.]/g, '-');
  const base = String(start?.map?.file ?? 'map').replace(/\.scn$/i, '');
  return `${stamp}-room${r.room ?? 'x'}-${base}.jsonl`;
}

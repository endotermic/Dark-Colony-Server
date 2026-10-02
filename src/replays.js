// The recorded battles a player can watch (REPLAY ONLINE GAME, 2 Oct 2026; plan §21, protocol doc
// §4.5): an index of the recordings in RECORD_DIR (src/recorder.js files), newest first, with the
// list row the patched Dark Colony Ultimate exe shows, the eight participant names of each battle
// and a retention of the newest REPLAY_KEEP files.  The row is formatted here, like the ONLINE WAR
// room rows, so that the layout can change without an exe rebuild; the screen's list is 40
// monospace columns (MFONTO5, 8 px each = 320 px; the participant pane takes the rest):
//
//   DATE  UTC   MAP       TERR.  S P A  M:SS
//   02.10 14:54 Armageddo Desert 8 2 1 12:34
//
// S = the map's seats, P = real players, A = computer players (the relay's bots and AI slots).
// Times are the relay's clock (UTC on Fly).

import fs from 'node:fs';
import path from 'node:path';
import { readRecording } from './recorder.js';
import { build, MAX_REPLAY_ROW, REPLAY_NAMES } from './commands.js';
import { terrainName } from './online.js';
import { SLOT_TYPE } from './constants.js';

/** [field, width] in row order; single spaces between the columns. */
export const COLUMNS = Object.freeze([
  ['date', 5],
  ['time', 5],
  ['map', 9],
  ['terrain', 6],
  ['seats', 1],
  ['players', 1],
  ['bots', 1],
  ['length', 5],
]);

const HEADINGS = Object.freeze({ date: 'DATE', time: 'UTC', map: 'MAP', terrain: 'TERR.', seats: 'S', players: 'P', bots: 'A', length: ' M:SS' });

/** The lobby slot the game treats as the host: never a viewer's seat (its client controls the lobby settings). */
export const HOST_SLOT = 0;

/** A file that is not a battle (no frames: a lobby that never started, or a foreign file) is deleted once this old; younger ones may still be open. */
export const STALE_MS = 24 * 3600 * 1000;

function cell(text, width) {
  return String(text ?? '').slice(0, width).padEnd(width);
}

/** The header line the screen shows above the list (same columns). */
export const HEADER = COLUMNS.map(([field, width]) => cell(HEADINGS[field], width)).join(' ').trimEnd();

const two = (n) => String(n).padStart(2, '0');

/** "mm:ss" up to 99:59, "1h40" from 100 minutes on; right-aligned in five columns. */
export function lengthText(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s >= 6000) return `${Math.floor(s / 3600)}h${two(Math.floor((s % 3600) / 60))}`.padStart(5);
  return `${Math.floor(s / 60)}:${two(s % 60)}`.padStart(5);
}

/** One list row from an index entry. */
export function rowText(e) {
  const d = e.recordedAt instanceof Date && !Number.isNaN(e.recordedAt.getTime()) ? e.recordedAt : null;
  const values = {
    date: d ? `${two(d.getUTCDate())}.${two(d.getUTCMonth() + 1)}` : '??.??',
    time: d ? `${two(d.getUTCHours())}:${two(d.getUTCMinutes())}` : '??:??',
    map: e.map,
    terrain: terrainName(e.terrain),
    seats: e.seats,
    players: e.players,
    bots: e.bots,
    length: lengthText(e.durationS),
  };
  return COLUMNS.map(([field, width]) => cell(values[field], width)).join(' ').trimEnd().slice(0, MAX_REPLAY_ROW);
}

/**
 * Read one recording into an index entry, or null when it holds no battle (no start header, no lobby,
 * not one sync frame).  `names[k]` is the lobby name of slot k when a human (real player or bot) sat
 * there - the seats a viewer can take - and '' otherwise; `real` has bit k set for the real players.
 */
export function describeRecording(file, stat = null) {
  let lines;
  try {
    lines = readRecording(file);
  } catch {
    return null;
  }
  const start = lines.find((l) => l.type === 'start');
  const slots = start?.lobby?.slots;
  if (!start || !Array.isArray(slots) || slots.length !== REPLAY_NAMES) return null;
  let frames = 0;
  let lastUntil = 0;
  for (const l of lines) {
    if (l.type !== 'frame') continue;
    frames++;
    if (l.until > lastUntil) lastUntil = l.until;
  }
  if (!frames) return null;
  const tickMs = start.tickMs ?? 44;
  const recordedPlayers = Array.isArray(start.players) ? start.players : [];
  const names = slots.map((s, k) => (s.type === SLOT_TYPE.HUMAN ? String(s.name || `Player${k}`).slice(0, 16) : ''));
  const humans = names.filter(Boolean).length;
  const ai = slots.filter((s) => s.type !== SLOT_TYPE.HUMAN && s.type !== SLOT_TYPE.EMPTY).length;
  const players = recordedPlayers.filter((p) => names[p.slot]).length;
  // Slot 0 is the lobby HOST in the game's own logic: a client seated there can change the map and
  // the lobby options, so no viewer may take it (maintainer, 2 Oct 2026). Normally the fake host's
  // seat anyway (MERCENARY_SLOT 0). Its name stays out of the list; the counts above still include it.
  names[HOST_SLOT] = '';
  let real = 0;
  for (const p of recordedPlayers) if (Number.isInteger(p.slot) && p.slot >= 0 && p.slot < REPLAY_NAMES && names[p.slot]) real |= 1 << p.slot;
  const stamp = start.t ?? start.loggedAt ?? null;
  let recordedAt = stamp ? new Date(stamp) : null;
  if (!recordedAt || Number.isNaN(recordedAt.getTime())) recordedAt = stat ? stat.mtime : new Date(0);
  const e = {
    file,
    recordedAt,
    map: start.map?.name ?? start.map?.file ?? 'unknown',
    terrain: start.map?.terrain ?? '',
    seats: start.map?.players ?? REPLAY_NAMES,
    players,
    bots: humans - players + ai,
    real,
    names,
    durationS: Math.round((lastUntil * tickMs) / 1000),
    frames,
  };
  e.row = rowText(e);
  return e;
}

export class Replays {
  /**
   * @param dir   RECORD_DIR
   * @param keep  how many recordings stay on disk (the newest); older files are deleted
   * @param log   logger
   */
  constructor(dir, keep, log = null) {
    this.dir = dir;
    this.keep = keep;
    this.log = log;
    this.list = []; // index entries, newest first; each with a stable wire id 1..250
    this.nextId = 1;
  }

  /** Index every recording of the directory (at start-up), then apply the retention. */
  scan() {
    this.list = [];
    let files;
    try {
      files = fs.readdirSync(this.dir).filter((f) => f.toLowerCase().endsWith('.jsonl'));
    } catch {
      return this; // no directory yet: the recorder creates it with the first battle
    }
    for (const f of files) this.add(path.join(this.dir, f), false);
    this.sort();
    this.prune();
    this.log?.info('replays indexed', { dir: this.dir, recordings: this.list.length, keep: this.keep });
    return this;
  }

  /** A recording was just closed (Recorder.onClose): index it and apply the retention. */
  onRecorded(file) {
    const e = this.add(file, true);
    this.sort();
    this.prune();
    if (e) this.log?.info('replay available', { file: path.basename(file), map: e.map, players: e.players, durationS: e.durationS, recordings: this.list.length });
    return e;
  }

  add(file, replace) {
    let stat = null;
    try {
      stat = fs.statSync(file);
    } catch {
      return null;
    }
    if (replace) this.list = this.list.filter((x) => x.file !== file);
    else if (this.list.some((x) => x.file === file)) return null;
    const e = describeRecording(file, stat);
    if (!e) return null;
    e.id = this.nextId;
    this.nextId = this.nextId >= 250 ? 1 : this.nextId + 1;
    this.list.push(e);
    return e;
  }

  sort() {
    this.list.sort((a, b) => b.recordedAt - a.recordedAt || b.file.localeCompare(a.file));
  }

  /**
   * Keep the newest `keep` battles, delete the older ones; a file that is not a battle (no sync frame)
   * goes once it is STALE_MS old - a younger one may be the recorder's open file of a running battle.
   */
  prune(now = Date.now()) {
    let files;
    try {
      files = fs.readdirSync(this.dir).filter((f) => f.toLowerCase().endsWith('.jsonl')).map((f) => path.join(this.dir, f));
    } catch {
      return;
    }
    const drop = [];
    for (const f of files) {
      if (this.list.some((x) => x.file === f)) continue;
      try {
        if (now - fs.statSync(f).mtimeMs > STALE_MS) drop.push(f);
      } catch {
        // gone meanwhile
      }
    }
    for (const e of this.list.slice(this.keep)) drop.push(e.file); // the list is newest first
    for (const f of drop) {
      try {
        fs.unlinkSync(f);
        this.log?.info('old recording deleted', { file: path.basename(f), keep: this.keep });
      } catch (err) {
        this.log?.warn('cannot delete an old recording', { file: f, err: err.message });
      }
      this.list = this.list.filter((x) => x.file !== f);
    }
  }

  /** Index entry by wire id, or null. */
  get(id) {
    return this.list.find((e) => e.id === id) ?? null;
  }

  /** The 0x56 REPLAYS header + one 0x57 REPLAY per recording, newest first (to be packed into frames). */
  payloads() {
    return [build.replays(this.list.length, HEADER), ...this.list.map((e) => build.replay(e))];
  }
}

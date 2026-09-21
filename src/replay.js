// Replay mode (REPLAY_FILE, 18 Sep 2026; plan §18.7): the server plays a recorded battle back to a
// real dc16.exe. The recording is a file of src/recorder.js (written live with RECORD_DIR, or
// rebuilt from the Fly log with tools/logs2replay.js). The room's lobby is rebuilt from the
// recording's start header - the same map, every recorded human as a fake with its recorded name,
// race, colour and team, AI and empty slots as recorded - except one seat: that of a recorded real
// player (REPLAY_SLOT, default the first one), which the connecting client takes. Its race, colour
// and team are pinned to the recorded values, so the start shuffle and the whole simulation come
// out as in the original battle. In battle the Game broadcasts the recorded sync frames byte for
// byte (UNTIL(a, until), the server's 0x08, the commands) at the recorded pace. Everything the
// watcher sends is ignored (orders, chat, gifts, pause, cheats: never relayed, never a strike)
// except the echoes and progress reports that pace the stream, the keep-alive, and leaving; its
// 0x08 checksums, when it sends any, are only compared with the recorded ones.
//
// Since the frames carry the original server's 0x08 checksums (send mode), the client verifies the
// replay itself: it aborts with "sync error" the moment its simulation leaves the recorded one.

import { readRecording } from './recorder.js';
import { resolveMap } from './maps.js';
import { formatScenarioTitle } from './config.js';
import { SLOT_TYPE, SLOTS } from './constants.js';
import { decode } from './commands.js';

export class Replay {
  /**
   * @param lines  parsed recording (readRecording)
   * @param opts   { file, slot: the recorded real player's slot the client will sit in (-1 = first) }
   */
  constructor(lines, opts = {}) {
    this.file = opts.file ?? null;
    const start = lines.find((l) => l.type === 'start');
    if (!start) throw new Error('recording has no start line');
    this.header = start;
    const slots = start.lobby?.slots;
    if (!Array.isArray(slots) || slots.length !== SLOTS) throw new Error('recording has no lobby slots');
    this.frames = lines.filter((l) => l.type === 'frame').map((l) => ({ a: l.a, until: l.until, cmds: Buffer.from(l.cmds ?? '', 'hex') }));
    if (!this.frames.length) throw new Error('recording has no sync frames');
    this.rx = new Map(); // tick -> { slot, checksum } the first client checksum recorded for that tick
    for (const l of lines) if (l.type === 'rx08' && !this.rx.has(l.tick)) this.rx.set(l.tick, { slot: l.slot, checksum: l.checksum & 0xffff });
    this.mready = new Map(lines.filter((l) => l.type === 'mready').map((l) => [l.slot, l.gamePlayer]));
    this.end = lines.find((l) => l.type === 'end') ?? null;
    this.recordedPlayers = Array.isArray(start.players) ? start.players : [];
    const humans = slots.map((s, i) => (s.type === SLOT_TYPE.HUMAN ? i : -1)).filter((i) => i >= 0);
    const real = this.recordedPlayers.map((p) => p.slot).filter((s) => humans.includes(s));
    let seat = Number.isInteger(opts.slot) && opts.slot >= 0 ? opts.slot : (real[0] ?? humans[0]);
    if (!humans.includes(seat)) {
      throw new Error(`REPLAY_SLOT ${seat} was not a human in the recording (real players in slots ${real.join(', ') || 'none'}, humans in ${humans.join(', ')})`);
    }
    this.seat = seat;
    this.seatWasFake = !real.includes(seat);
    this.map = mapEntry(start.map ?? {});
    this.cursor = 0;
    this.dropped = 0; // watcher commands ignored (all but echoes, progress, keep-alive, 0x08)
    this.ignored = {}; // the same by command name
    this.compared = 0;
    this.divergedAt = null;
    this.reported = false;
  }

  /**
   * Back to the first frame for the next game (21 Sep 2026): the room resets after every game, but
   * the cursor did not, so a second client started mid-stream (missing commands -> the game's sync
   * assert at the first checksum it received) and a third got nothing (the lag guard blocked frames
   * far ahead of a client at time 0). Found while confirming fix `camera` with three runs.
   */
  rewind() {
    this.cursor = 0;
    this.dropped = 0;
    this.ignored = {};
    this.compared = 0;
    this.divergedAt = null;
    this.reported = false;
  }

  get seatSlot() {
    return this.header.lobby.slots[this.seat];
  }

  get seatName() {
    return this.seatSlot.name || `Player${this.seat}`;
  }

  get tickMs() {
    return this.header.tickMs ?? 44;
  }

  get lastUntil() {
    return this.frames.at(-1).until;
  }

  /** The room's eight slots as the recording had them, the client's seat left empty. */
  lobbySlots() {
    return this.header.lobby.slots.map((rec, s) => {
      const base = {
        slot: s,
        name: rec.name ?? '',
        race: rec.race ?? 0,
        colour: rec.colour ?? s,
        team: rec.team ?? s,
        type: rec.type ?? SLOT_TYPE.EMPTY,
        status: 0,
        client: null,
      };
      if (s === this.seat) return { ...base, type: SLOT_TYPE.EMPTY, name: '', reserved: true };
      if (rec.type === SLOT_TYPE.HUMAN) return { ...base, status: 1, fake: true };
      return base;
    });
  }

  peek() {
    return this.frames[this.cursor] ?? null;
  }

  peekNext() {
    return this.frames[this.cursor + 1] ?? null;
  }

  advance() {
    this.cursor++;
  }

  get finished() {
    return this.cursor >= this.frames.length;
  }

  /** Game player index the recording's client in `slot` reported in MREADY, or null. */
  expectedGamePlayer(slot) {
    return this.mready.has(slot) ? this.mready.get(slot) : null;
  }

  /**
   * A 0x08 from the real client. Compared with the checksum a client sent for that tick in the
   * original battle (present when the recorded lowest network id was a real player). Returns the
   * first divergence { tick, expected, got, slot } once found, null while everything matches.
   */
  onClientSync(slot, cmd) {
    const d = decode(cmd);
    const rec = this.rx.get(d.time);
    if (!rec) return null;
    this.compared++;
    const got = d.checksum & 0xffff;
    if (rec.checksum === got) return null;
    if (!this.divergedAt) this.divergedAt = { tick: d.time, expected: rec.checksum, got, slot };
    return this.divergedAt;
  }

  summary() {
    return {
      file: this.file,
      map: this.map.file,
      name: this.map.name,
      recordedAt: this.header.loggedAt ?? this.header.t ?? null,
      seat: this.seat,
      seatName: this.seatName,
      seatWasFake: this.seatWasFake,
      recordedPlayers: this.recordedPlayers,
      frames: this.frames.length,
      lastUntil: this.lastUntil,
      durationS: Math.round((this.lastUntil * this.tickMs) / 1000),
      tickMs: this.tickMs,
      syncCheck: this.header.syncCheck ?? null,
      clientChecksums: this.rx.size,
      end: this.end ? { reason: this.end.reason, mismatches: this.end.mismatches, disabled: this.end.disabled } : null,
    };
  }
}

/** Load a recording file into a Replay. */
export function loadReplay(file, opts = {}) {
  return new Replay(readRecording(file), { ...opts, file });
}

/** The configuration a replay server runs with: one room, no hall, no bots, the recorded speed. */
export function replayConfig(cfg, replay) {
  return {
    ...cfg,
    HALL: false,
    FILL_EMPTY_WITH_AI: false, // the bots are off through Room.replay (Bots.configured)
    // the frames already carry the original server's 0x08; a second set would contradict them
    SYNC_CHECK: cfg.SYNC_CHECK === 'send' ? 'shadow' : cfg.SYNC_CHECK,
    TICK_MS: replay.header.tickMs ?? cfg.TICK_MS,
    LOOKAHEAD: replay.header.lookahead ?? cfg.LOOKAHEAD,
    ROOM_LIST: [replay.map],
  };
}

/** A ROOM_LIST-style entry for the recorded map (the shipped table, or the header when unknown). */
function mapEntry(m) {
  let base = null;
  try {
    base = m.file ? resolveMap(m.name ? `${m.file}:${m.name}:${m.terrain ?? ''}` : m.file) : null;
  } catch {
    base = null;
  }
  if (!base) base = { file: m.file ?? 'UNKNOWN.SCN', name: m.name ?? m.file ?? 'unknown', terrain: m.terrain ?? 'desert', players: m.players ?? SLOTS };
  return { ...base, index: 1, titleWire: formatScenarioTitle(base.name, base.players, base.terrain) };
}

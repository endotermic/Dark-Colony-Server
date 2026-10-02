// The rooms of the server (plan §17): one Room per entry of ROOMS, each with its own map, all
// driven by the same timers. Rooms are fixed; each one resets to LOBBY when its last player leaves.
// Since 2 Oct 2026 (REPLAY ONLINE GAME, plan §21) the pool also holds VIEWER rooms: one per player
// watching a recording, created by the hall at 0x58 RPLAY and dropped when the viewer leaves.

import { Room } from './room.js';
import { childLogger } from './log.js';
import { Replays } from './replays.js';
import { replayConfig } from './replay.js';

export class RoomPool {
  /** `opts.replay` (a Replay, plan §18.7) makes the pool a single room that plays that recording back. */
  constructor(config, log, now, random, opts = {}) {
    this.config = config;
    this.log = log;
    this.replay = opts.replay ?? null;
    // the recorded battles the players can watch (plan §21): indexed from RECORD_DIR, the newest REPLAY_KEEP kept
    this.replays = config.RECORD_DIR && !this.replay ? new Replays(config.RECORD_DIR, config.REPLAY_KEEP, log).scan() : null;
    this.now = now;
    this.random = random;
    this.rooms = this.replay
      ? [new Room(config, childLogger(log, { room: 1 }), now, random, { id: 1, map: this.replay.map, replay: this.replay })]
      : config.ROOM_LIST.map((map) => new Room(config, childLogger(log, { room: map.index }), now, random, { id: map.index, map, replays: this.replays }));
    this.viewers = []; // viewer rooms (plan §21), ids 101, 102, ...
    this.viewerSeq = 0;
    this.engine = null;
  }

  /** Room by 0-based index. */
  get(index) {
    return this.rooms[index] ?? null;
  }

  /** Every room: the fixed ones and the viewer rooms. */
  all() {
    return this.viewers.length ? [...this.rooms, ...this.viewers] : this.rooms;
  }

  /** Hand the loaded battle engine ({ createGame, loadMapJson } or null) to every room. */
  setEngine(engine) {
    this.engine = engine;
    for (const r of this.all()) r.setEngine(engine);
  }

  /**
   * A viewer room for one player (plan §21): the recorded lobby with the viewer's seat free, no
   * recording, no engine, the recorded speed; gone when its client leaves.  Returns the Room.
   */
  openViewerRoom(replay) {
    const id = 101 + (this.viewerSeq++ % 100);
    const cfg = replayConfig(this.config, replay, { viewer: true });
    const room = new Room(cfg, childLogger(this.log, { room: id }), this.now, this.random, {
      id,
      map: replay.map,
      replay,
      onEmpty: (r) => this.closeViewerRoom(r),
    });
    this.viewers.push(room);
    this.log.info('viewer room opened', { room: id, ...replay.summary(), viewers: this.viewers.length });
    return room;
  }

  closeViewerRoom(room) {
    const i = this.viewers.indexOf(room);
    if (i < 0) return;
    this.viewers.splice(i, 1);
    this.log.info('viewer room closed', { room: room.id, viewers: this.viewers.length });
  }

  step(now) {
    for (const r of this.all()) r.step(now);
  }

  watchdogTick(now) {
    for (const r of this.all()) r.watchdogTick(now);
  }

  /** Every client seated in a room. */
  clients() {
    const out = [];
    for (const r of this.all()) for (const c of r.clients) out.push(c);
    return out;
  }

  summaries() {
    return this.rooms.map((r) => r.summary());
  }
}

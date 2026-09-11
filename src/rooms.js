// The rooms of the server (plan §17): one Room per entry of ROOMS, each with its own map, all
// driven by the same timers. Rooms are fixed; each one resets to LOBBY when its last player leaves.

import { Room } from './room.js';
import { childLogger } from './log.js';

export class RoomPool {
  constructor(config, log, now, random) {
    this.config = config;
    this.log = log;
    this.rooms = config.ROOM_LIST.map((map) => new Room(config, childLogger(log, { room: map.index }), now, random, { id: map.index, map }));
  }

  /** Room by 0-based index. */
  get(index) {
    return this.rooms[index] ?? null;
  }

  /** Hand the loaded battle engine ({ createGame, loadMapJson } or null) to every room. */
  setEngine(engine) {
    for (const r of this.rooms) r.setEngine(engine);
  }

  step(now) {
    for (const r of this.rooms) r.step(now);
  }

  watchdogTick(now) {
    for (const r of this.rooms) r.watchdogTick(now);
  }

  /** Every client seated in a room. */
  clients() {
    const out = [];
    for (const r of this.rooms) for (const c of r.clients) out.push(c);
    return out;
  }

  summaries() {
    return this.rooms.map((r) => r.summary());
  }
}

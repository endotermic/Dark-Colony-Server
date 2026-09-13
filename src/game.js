// Strict lockstep (plan §8): one sync frame per server step, [UNTIL][commands...], identical for
// every client. Commands from clients are filtered, grouped and queued for the next frame.

import { T, build, decode, typeName, RELAY_IN_GAME } from './commands.js';
import { MAX_FRAME } from './frame.js';
import { CHEAT_TEXTS } from './constants.js';

export const SYNC_HEADER = 9; // type byte + i32 time + i32 until
export const COMMAND_BUDGET = MAX_FRAME - 3 - SYNC_HEADER; // 1012 bytes of commands per frame

// Commands added to the first sync frame in debug mode. CHEAT(a, b) with a not in {1, 2} toggles
// the client flag gs+0x46F50+b (handler 0x41CE9C); flag 0 is the one the "slag net" chat cheat
// toggles and is tested by the map visibility code (0x445A77): full map view for everybody (F28).
export const DEBUG_START_CHEATS = Object.freeze([build.cheat(0, 0)]);

export class Game {
  constructor(room) {
    this.room = room;
    this.reset();
  }

  get cfg() {
    return this.room.config;
  }

  reset() {
    this.running = false;
    this.time = 0;
    this.acc = 0;
    this.lastStep = 0;
    this.paused = false;
    this.queue = []; // Buffers: each is one group of commands that stays together
    this.issued = new Set(); // until values sent and not yet reached by everybody
    this.lastIssuedUntil = -1;
    this.stallSince = 0;
    this.lastSpeedRefresh = 0;
    this.framesSent = 0;
  }

  start(now) {
    this.running = true;
    this.time = 0;
    this.acc = 0;
    this.lastStep = now;
    this.paused = false;
    this.issued.clear();
    this.lastIssuedUntil = -1;
    this.stallSince = 0;
    this.lastSpeedRefresh = now;
    this.framesSent = 0;
    for (const c of this.room.players()) {
      c.clientTime = 0;
      c.reachedUntil = 0;
      c.pendingEchoes.clear();
    }
    // first sync frame: lock the speed (F11); in debug mode reveal the whole map (F28)
    const head = [build.tickSpeed(this.cfg.TICK_MS)];
    if (this.cfg.DEBUG) head.push(...DEBUG_START_CHEATS);
    this.queue.unshift(...head);
  }

  /** Queue one group of commands (kept together in one frame). */
  queueCommands(buf) {
    if (buf.length === 0) return;
    if (buf.length > COMMAND_BUDGET) {
      this.room.log.warn('command group too large, dropped', { bytes: buf.length });
      return;
    }
    this.queue.push(buf);
  }

  minClientTime() {
    let min = Infinity;
    for (const c of this.room.players()) if (c.clientTime < min) min = c.clientTime;
    return min === Infinity ? 0 : min;
  }

  slowestClient() {
    let best = null;
    for (const c of this.room.players()) if (!best || c.clientTime < best.clientTime) best = c;
    return best;
  }

  // ---- the step loop (plan §8.2) ---------------------------------------------------------

  step(now) {
    if (!this.running) return;
    if (this.paused) {
      this.lastStep = now;
      return;
    }
    const cfg = this.cfg;
    this.acc += now - this.lastStep;
    this.lastStep = now;
    const n = Math.min(255, Math.floor(this.acc / cfg.TICK_MS));
    if (n <= 0) return;
    const until = this.time + n + cfg.LOOKAHEAD;
    if (until - this.minClientTime() >= cfg.MAX_LAG) {
      // somebody is too far behind: stall, but do not build up a burst (§9.5 handles eviction)
      this.acc = Math.min(this.acc, cfg.TICK_MS);
      if (!this.stallSince) {
        this.stallSince = now;
        const slow = this.slowestClient();
        this.room.log.warn('stalled', { until, minClientTime: this.minClientTime(), slowestSlot: slow?.slot });
      }
      return;
    }
    if (this.stallSince) {
      this.room.log.info('stall over', { stalledMs: Math.round(now - this.stallSince) });
      this.stallSince = 0;
    }
    this.acc -= n * cfg.TICK_MS;
    if (now - this.lastSpeedRefresh >= cfg.SPEED_REFRESH_S * 1000) {
      this.queue.push(build.tickSpeed(cfg.TICK_MS));
      this.lastSpeedRefresh = now;
    }
    const parts = [build.until(this.time, until)];
    let used = 0;
    // the engine's checksum of the last simulated tick (SYNC_CHECK=send, plan §18): 0x08 first,
    // unless it would keep a full-size command group from ever going out (it then waits a frame)
    const syncCmd = this.room.sync.syncCommand();
    if (syncCmd && !(this.queue.length && syncCmd.length + this.queue[0].length > COMMAND_BUDGET)) {
      parts.push(syncCmd);
      used += syncCmd.length;
      this.room.sync.markSent();
    }
    while (this.queue.length && used + this.queue[0].length <= COMMAND_BUDGET) {
      const group = this.queue.shift();
      parts.push(group);
      used += group.length;
    }
    const payload = Buffer.concat(parts);
    for (const c of this.room.players()) c.pendingEchoes.set(this.time, now);
    this.room.broadcast(payload);
    this.room.sync.onFrameIssued(this.time, until, parts.slice(1));
    this.issued.add(until);
    this.lastIssuedUntil = until;
    this.time += n;
    this.framesSent++;
    this.pruneIssued();
  }

  pruneIssued() {
    const min = this.minClientTime();
    for (const u of this.issued) if (u < min) this.issued.delete(u);
  }

  // ---- frames from clients (plan §8.3) -----------------------------------------------------

  handle(client, cmds, now) {
    const r = this.room;
    const cfg = this.cfg;
    const group = [];
    for (const cmd of cmds) {
      if (client.gone) return;
      switch (cmd.type) {
        case T.UNTIL: {
          const d = decode(cmd);
          if (d.a !== -1) {
            // echo of one of our sync frames (F10)
            const sentAt = client.pendingEchoes.get(d.a);
            if (sentAt === undefined) return r.evict(client, `echo for unknown frame ${d.a}`);
            client.pendingEchoes.delete(d.a);
            client.latencyMs = now - sentAt;
          } else {
            // "I reached until" (F10)
            if (!this.issued.has(d.until) || d.until <= client.reachedUntil) {
              return r.evict(client, `bad progress report ${d.until} (last ${client.reachedUntil})`);
            }
            client.reachedUntil = d.until;
            client.clientTime = d.until;
          }
          break;
        }

        case T.CHEAT: {
          const d = decode(cmd);
          if (d.a === 1 || d.a === 2) {
            if (!cfg.ALLOW_PAUSE) break;
            const pause = d.a === 1;
            if (pause !== this.paused) {
              this.paused = pause;
              if (!pause) {
                this.lastStep = now;
                this.acc = 0;
                this.lastSpeedRefresh = now;
                this.queue.push(build.tickSpeed(cfg.TICK_MS));
                for (const c of r.players()) for (const a of c.pendingEchoes.keys()) c.pendingEchoes.set(a, now);
              }
              r.log.info(pause ? 'paused' : 'resumed', { by: client.slot });
              r.sync.onPause(pause, client.slot);
            }
            // out-of-band for the client (F6): relayed at once as a standalone frame
            r.broadcast(Buffer.from(cmd.raw));
          } else {
            r.strike(client, `cheat flag ${d.b}`);
          }
          break;
        }

        case T.SYNC:
          // never forwarded (R5); with the fake host in slot 0 nobody sends it anyway (F14). With
          // MERCENARY_SLOT > 0 the lowest real player does, and the engine compares (plan §18)
          r.sync.onClientSync(client, cmd);
          break;

        case T.TICK_SPEED:
        case T.TICK_MAXSPEED:
        case T.TICK_DESSPEED:
          break; // every healthy client sends these; dropped silently (R11)

        case T.CREATE:
          r.strike(client, `cheat command ${typeName(cmd.type)}`);
          break;

        case T.BONUS: {
          // the diplomacy screen's "give 1000" (F47): the giver already deducted locally and only the
          // receiver's machine adds, so it is relayed like an order. Aimed at a bot it buys that bot's
          // alliance (mercenary.js); the engine keeps the bots' ledgers.
          const d = decode(cmd);
          if (d.player > 7) {
            r.strike(client, `bonus for player ${d.player}`);
            break;
          }
          group.push(cmd.raw);
          r.bots.onGift(client, d.player);
          break;
        }

        case T.CHAT: {
          const d = decode(cmd);
          const idx = d.text.indexOf(':');
          const body = (idx >= 0 ? d.text.slice(idx + 1) : d.text).trim().toLowerCase();
          if (CHEAT_TEXTS.has(body)) {
            r.strike(client, 'cheat text');
            break;
          }
          group.push(cmd.raw);
          break;
        }

        case T.TICK:
        case T.DISCONNECT:
          r.strike(client, `server-only command ${typeName(cmd.type)}`);
          break;

        case T.KEEPALIVE:
          break;

        default:
          if (RELAY_IN_GAME.has(cmd.type)) group.push(cmd.raw);
          else if (cmd.type >= 0x64) r.strike(client, `lobby message ${typeName(cmd.type)} in battle`);
          else r.strike(client, `unexpected command ${typeName(cmd.type)}`);
      }
    }
    if (group.length && !client.gone) this.queueCommands(Buffer.concat(group));
  }

  onClientLeft(client) {
    client.pendingEchoes.clear();
    this.queue.push(build.disconnect(client.slot));
  }
}

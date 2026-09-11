// Deadlines per phase (plan §9.1) and lag eviction (§9.5). Runs every 500 ms.

import { STATE } from './constants.js';

export class Watchdog {
  constructor(room) {
    this.room = room;
    this.lastStatsAt = 0;
    this.stalls = 0;
    this.lastStallSeen = 0;
  }

  /** One info line with the numbers that matter for a long-running battle. */
  stats(now) {
    const r = this.room;
    const g = r.game;
    if (g.stallSince && g.stallSince !== this.lastStallSeen) {
      this.stalls++;
      this.lastStallSeen = g.stallSince;
    }
    if (r.state !== STATE.RUNNING || r.config.STATS_INTERVAL_S <= 0) return;
    if (now - this.lastStatsAt < r.config.STATS_INTERVAL_S * 1000) return;
    this.lastStatsAt = now;
    r.log.info('stats', {
      time: g.time,
      until: g.lastIssuedUntil,
      framesSent: g.framesSent,
      queued: g.queue.length,
      paused: g.paused,
      stalls: this.stalls,
      stalledNow: g.stallSince > 0,
      engine: r.sync.mode === 'off' ? undefined : r.sync.summary(),
      players: r.players().map((c) => ({
        slot: c.slot,
        gamePlayer: c.gamePlayer,
        latencyMs: c.latencyMs < 0 ? null : Math.round(c.latencyMs),
        behindTicks: g.lastIssuedUntil - c.clientTime,
        pendingEchoes: c.pendingEchoes.size,
        strikes: c.strikes,
      })),
    });
  }

  tick(now) {
    const r = this.room;
    const cfg = r.config;
    r.lobby.tick(now);
    this.stats(now);
    for (const c of r.players()) {
      switch (r.state) {
        case STATE.LOBBY:
          if (!c.firstMessageAt) {
            if (now - c.joinedAt > cfg.JOIN_TIMEOUT_MS) r.evict(c, 'no message after joining');
          } else if (now - c.lastSeen > cfg.KEEPALIVE_TIMEOUT_MS) {
            r.evict(c, 'keep-alive timeout');
          }
          break;
        case STATE.STARTING:
          if (!c.mready && now - r.startingAt > cfg.MREADY_TIMEOUT_MS) r.evict(c, 'did not finish loading');
          break;
        case STATE.RUNNING: {
          if (r.game.paused) break; // nothing is sent while paused, so nothing can be answered
          let late = null;
          for (const [a, sentAt] of c.pendingEchoes) {
            if (now - sentAt > cfg.ECHO_TIMEOUT_MS) {
              late = a;
              break;
            }
          }
          if (late !== null) {
            r.evict(c, `no echo for frame ${late}`);
            break;
          }
          if (now - c.lastSeen > cfg.IDLE_TIMEOUT_MS) r.evict(c, 'idle');
          break;
        }
        default:
          break;
      }
    }
    if (r.state === STATE.RUNNING && cfg.LAG_DROP_MS > 0 && r.game.stallSince && now - r.game.stallSince >= cfg.LAG_DROP_MS) {
      const victim = r.game.slowestClient();
      if (victim) {
        r.evict(victim, `lagging ${r.game.lastIssuedUntil - victim.clientTime} ticks behind`);
        r.game.stallSince = 0;
      }
    }
  }
}

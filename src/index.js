// Entry point: TCP listener, timers, optional health listener.

import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { loadConfig } from './config.js';
import { createLogger } from './log.js';
import { RoomPool } from './rooms.js';
import { Hall } from './hall.js';
import { VERSION } from './version.js';
import { loadEngine } from './enginebridge.js';
import { loadReplay, replayConfig } from './replay.js';

const STEP_INTERVAL_MS = 5;
const WATCHDOG_INTERVAL_MS = 500;

export function startServer(config, log = createLogger(config.LOG_LEVEL)) {
  // replay mode (plan §18.7): one room that plays a recording back to a real client
  const replay = config.REPLAY_FILE ? loadReplay(config.REPLAY_FILE, { slot: config.REPLAY_SLOT }) : null;
  if (replay) {
    config = replayConfig(config, replay);
    log.info('replay mode', replay.summary());
  }
  const pool = new RoomPool(config, log, undefined, undefined, { replay });
  const hall = new Hall(pool, config, log);
  // the battle engine (plan §18) is loaded in the background; rooms start as plain relays until then
  const engineReady = config.SYNC_CHECK !== 'off' ? loadEngine(log).then((e) => pool.setEngine(e)) : Promise.resolve();
  // HALL=false: the 2.0 behaviour, straight into room 1
  const server = net.createServer((socket) => (config.HALL ? hall.accept(socket) : pool.rooms[0].accept(socket)));
  server.on('error', (err) => log.error('listen error', { err: err.message }));

  const stepTimer = setInterval(() => {
    const now = performance.now();
    pool.step(now);
    hall.step(now);
  }, STEP_INTERVAL_MS);
  const watchdogTimer = setInterval(() => {
    const now = performance.now();
    pool.watchdogTick(now);
    hall.tick(now);
  }, WATCHDOG_INTERVAL_MS);

  let health = null;
  if (config.HEALTH_PORT) {
    health = net.createServer((s) => s.end());
    health.listen(config.HEALTH_PORT, '0.0.0.0');
  }

  const listening = new Promise((resolve) => {
    server.listen(config.PORT, '0.0.0.0', () => resolve(server.address()));
  });

  return {
    pool,
    hall,
    replay,
    config,
    rooms: pool.rooms,
    room: pool.rooms[0],
    server,
    listening,
    engineReady,
    async close() {
      clearInterval(stepTimer);
      clearInterval(watchdogTimer);
      for (const c of [...hall.clients, ...pool.clients()]) c.destroy();
      await new Promise((resolve) => server.close(() => resolve()));
      if (health) await new Promise((resolve) => health.close(() => resolve()));
    },
  };
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMain) {
  const config = loadConfig();
  const log = createLogger(config.LOG_LEVEL);
  const srv = startServer(config, log);
  srv.listening.then((addr) =>
    log.info('listening', {
      ...(srv.config !== config ? { mode: 'replay' } : {}),
      version: VERSION,
      port: addr.port,
      hall: srv.config.HALL,
      rooms: srv.config.ROOM_LIST.map((m) => `${m.index}:${m.file} ${m.name}`),
      marqueeMs: config.MARQUEE_MS,
      tickMs: srv.config.TICK_MS,
      minPlayers: config.MIN_PLAYERS,
      fakePlayers: config.FAKE_PLAYERS,
      debug: config.DEBUG,
      syncCheck: srv.config.SYNC_CHECK,
      recordDir: config.RECORD_DIR || undefined,
      mercenarySlot: config.MERCENARY_SLOT,
      replay: config.REPLAY_FILE || undefined,
    }),
  );
  const shutdown = (signal) => {
    log.info('shutting down', { signal });
    srv.close().then(() => process.exit(0));
    setTimeout(() => process.exit(0), 2000).unref();
  };
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

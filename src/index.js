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

const STEP_INTERVAL_MS = 5;
const WATCHDOG_INTERVAL_MS = 500;

export function startServer(config, log = createLogger(config.LOG_LEVEL)) {
  const pool = new RoomPool(config, log);
  const hall = new Hall(pool, config, log);
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
    rooms: pool.rooms,
    room: pool.rooms[0],
    server,
    listening,
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
      version: VERSION,
      port: addr.port,
      hall: config.HALL,
      rooms: config.ROOM_LIST.map((m) => `${m.index}:${m.file} ${m.name}`),
      marqueeMs: config.MARQUEE_MS,
      tickMs: config.TICK_MS,
      minPlayers: config.MIN_PLAYERS,
      fakePlayers: config.FAKE_PLAYERS,
      debug: config.DEBUG,
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

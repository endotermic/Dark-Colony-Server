// Entry point: TCP listener, timers, optional health listener.

import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { loadConfig } from './config.js';
import { createLogger } from './log.js';
import { Room } from './room.js';
import { VERSION } from './version.js';

const STEP_INTERVAL_MS = 5;
const WATCHDOG_INTERVAL_MS = 500;

export function startServer(config, log = createLogger(config.LOG_LEVEL)) {
  const room = new Room(config, log);
  const server = net.createServer((socket) => room.accept(socket));
  server.on('error', (err) => log.error('listen error', { err: err.message }));

  const stepTimer = setInterval(() => room.step(performance.now()), STEP_INTERVAL_MS);
  const watchdogTimer = setInterval(() => room.watchdogTick(performance.now()), WATCHDOG_INTERVAL_MS);

  let health = null;
  if (config.HEALTH_PORT) {
    health = net.createServer((s) => s.end());
    health.listen(config.HEALTH_PORT, '0.0.0.0');
  }

  const listening = new Promise((resolve) => {
    server.listen(config.PORT, '0.0.0.0', () => resolve(server.address()));
  });

  return {
    room,
    server,
    listening,
    async close() {
      clearInterval(stepTimer);
      clearInterval(watchdogTimer);
      for (const c of [...room.clients]) c.destroy();
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
      map: config.MAP_FILE,
      title: config.MAP_TITLE,
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

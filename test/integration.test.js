// End-to-end: the real TCP server with scripted clients (plan §13.2 / §13.3).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startServer } from '../src/index.js';
import { loadConfig } from '../src/config.js';
import { silentLogger } from '../src/log.js';
import { FakeClient } from '../tools/fakeclient.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(pred, timeoutMs, what) {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > timeoutMs) throw new Error(`timeout waiting for ${what}`);
    await sleep(20);
  }
}

function fastConfig(overrides = {}) {
  return loadConfig({}, {
    PORT: 0,
    START_COUNTDOWN_S: 0,
    MIN_PLAYERS: 2,
    ECHO_TIMEOUT_MS: 400,
    KEEPALIVE_TIMEOUT_MS: 1500,
    JOIN_TIMEOUT_MS: 1000,
    LAG_DROP_MS: 0,
    ...overrides,
  });
}

test('three scripted clients play in lockstep with identical sync frames; one that stops echoing is evicted', async () => {
  const srv = startServer(fastConfig(), silentLogger);
  const { port } = await srv.listening;
  const clients = [0, 1, 2].map((i) => new FakeClient({ port, name: `Bot${i}`, readyAfterMs: 150, loadMs: 50, tickMs: 33 }));
  try {
    await Promise.all(clients.map((c) => c.connect()));
    await waitFor(() => clients.every((c) => c.slot >= 1), 2000, 'handshakes');
    assert.equal(new Set(clients.map((c) => c.slot)).size, 3, 'unique slots');
    await waitFor(() => srv.room.state === 'RUNNING', 4000, 'RUNNING');
    await sleep(700);
    const lists = clients.map((c) => c.syncPayloads.map((b) => b.toString('hex')));
    const n = Math.min(...lists.map((l) => l.length));
    assert.ok(n >= 10, `expected at least 10 sync frames, got ${n}`);
    for (let i = 0; i < n; i++) {
      assert.equal(lists[1][i], lists[0][i], `frame ${i} differs for Bot1`);
      assert.equal(lists[2][i], lists[0][i], `frame ${i} differs for Bot2`);
    }
    assert.ok(clients.every((c) => c.tickSpeed === 33), 'speed locked to 33 ms');
    assert.ok(clients.every((c) => c.reached.length > 0), 'progress reports were made');
    assert.ok(clients.every((c) => c.gameTime > 0));
    assert.ok(srv.room.game.stallSince === 0, 'no stall with healthy clients');

    // Bot2 stops echoing: evicted within ECHO_TIMEOUT, the others learn it inside a sync frame
    clients[2].behave.add('noEcho');
    await waitFor(() => clients[0].disconnects.includes(clients[2].slot), 3000, 'DISCONNECT of Bot2');
    await waitFor(() => clients[2].closed, 1000, 'Bot2 socket closed');
    assert.ok(clients[1].disconnects.includes(clients[2].slot));
    const before = clients[0].syncPayloads.length;
    await sleep(300);
    assert.ok(clients[0].syncPayloads.length > before, 'the game keeps pacing for the survivors');
    assert.equal(srv.room.clients.size, 2);
  } finally {
    for (const c of clients) c.close();
    await srv.close();
  }
});

test('a client that never sends anything after the handshake is dropped from the lobby', async () => {
  const srv = startServer(fastConfig(), silentLogger);
  const { port } = await srv.listening;
  const good = new FakeClient({ port, name: 'Good', readyAfterMs: -1 });
  const mute = new FakeClient({ port, name: 'Mute', readyAfterMs: -1, behave: ['silent'] });
  try {
    await good.connect();
    await mute.connect();
    await waitFor(() => good.slot >= 1 && mute.slot >= 1, 2000, 'handshakes');
    await waitFor(() => mute.closed, 3000, 'mute client dropped');
    assert.ok(!good.closed);
    assert.ok(good.chat.some((t) => t.includes('left the lobby')));
    assert.equal(srv.room.clients.size, 1);
  } finally {
    good.close();
    mute.close();
    await srv.close();
  }
});

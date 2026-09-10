// End-to-end: the real TCP server with scripted clients (plan §13.2 / §13.3).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startServer } from '../src/index.js';
import { loadConfig } from '../src/config.js';
import { silentLogger } from '../src/log.js';
import { FakeClient } from '../tools/fakeclient.js';
import { build } from '../src/commands.js';

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
    HALL: false, // straight into room 1, as in 2.0; the hall has its own test below
    START_COUNTDOWN_S: 0,
    MIN_PLAYERS: 2,
    ECHO_TIMEOUT_MS: 400,
    KEEPALIVE_TIMEOUT_MS: 1500,
    JOIN_TIMEOUT_MS: 1000,
    LAG_DROP_MS: 0,
    ...overrides,
  });
}

test('through the hall: two clients pick room 2 with /2 and READY, then play there', async () => {
  const srv = startServer(fastConfig({ HALL: true, MARQUEE_MS: 100 }), silentLogger);
  const { port } = await srv.listening;
  const clients = [0, 1].map((i) => new FakeClient({ port, name: `Bot${i}`, room: 2, readyAfterMs: 150, loadMs: 50, tickMs: 33 }));
  try {
    await Promise.all(clients.map((c) => c.connect()));
    await waitFor(() => clients.every((c) => c.slot >= 1), 2000, 'handshakes');
    assert.ok(clients.every((c) => c.hallTitle), 'both saw the hall title');
    await waitFor(() => clients.every((c) => c.inRoom), 3000, 'both moved into a room');
    assert.ok(clients.every((c) => c.scenarioTitle.startsWith(srv.rooms[1].map.name)), 'room 2 scenario received');
    assert.equal(srv.hall.clients.size, 0);
    assert.equal(srv.rooms[1].clients.size, 2);
    assert.equal(srv.rooms[0].clients.size, 0);
    await waitFor(() => srv.rooms[1].state === 'RUNNING', 4000, 'room 2 RUNNING');
    await sleep(400);
    const lists = clients.map((c) => c.syncPayloads.map((b) => b.toString('hex')));
    const n = Math.min(...lists.map((l) => l.length));
    assert.ok(n >= 5, `expected sync frames, got ${n}`);
    for (let i = 0; i < n; i++) assert.equal(lists[1][i], lists[0][i], `frame ${i} differs`);
    assert.ok(clients.every((c) => c.marqueeSteps >= 2), 'the room rows scrolled while waiting');
  } finally {
    for (const c of clients) c.close();
    await srv.close();
  }
});

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
    assert.ok(clients.every((c) => c.tickSpeed === 44), 'speed locked to 44 ms (150 %)');
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

test('ready policies (smoke test): hold never starts a room, follow mirrors a real player and the room starts only through them', async () => {
  const srv = startServer(fastConfig({ HALL: true, MIN_PLAYERS: 1, START_COUNTDOWN_S: 1 }), silentLogger);
  const { port } = await srv.listening;
  const peers = new Set();
  const bots = [0, 1].map((i) => new FakeClient({ port, name: `Follow${i}`, room: 3, readyPolicy: 'follow', peerSlots: peers, announceName: true, readyAfterMs: 100, loadMs: 50 }));
  for (const b of bots) b.on('joined', (s) => peers.add(s));
  const holder = new FakeClient({ port, name: 'Hold', room: 4, readyPolicy: 'hold', readyAfterMs: 100 });
  const human = new FakeClient({ port, name: 'Human', room: 3, readyPolicy: 'hold', announceName: true, readyAfterMs: 100, loadMs: 50 });
  const all = [...bots, holder, human];
  try {
    for (const c of all) await c.connect(); // one after the other: distinct slots, as the smoke test seats them
    await waitFor(() => all.every((c) => c.inRoom), 4000, 'everybody in a room');
    assert.equal(srv.rooms[2].clients.size, 3);
    assert.equal(srv.rooms[3].clients.size, 1);
    assert.equal(srv.rooms[2].slots[human.slot].name, 'Human', 'the announced name followed the player into the room');
    await sleep(300);
    assert.equal(srv.rooms[2].state, 'LOBBY', 'follow bots do not start on their own');
    assert.equal(srv.rooms[3].state, 'LOBBY', 'a hold bot never readies');
    assert.ok(bots.every((b) => b.readyWanted === 1));

    human.send(build.ready(2, human.slot));
    await waitFor(() => bots.every((b) => b.readyWanted === 2), 2000, 'bots followed the human');
    await waitFor(() => srv.rooms[2].lobby.countdownEndsAt >= 0, 2000, 'countdown running');
    human.send(build.ready(1, human.slot)); // the human changes their mind: the bots release READY too
    await waitFor(() => bots.every((b) => b.readyWanted === 1), 2000, 'bots released READY');
    await sleep(1200);
    assert.equal(srv.rooms[2].state, 'LOBBY', 'no start without the human');
    assert.ok(human.chat.some((t) => t.includes('start cancelled')));

    human.send(build.ready(2, human.slot));
    await waitFor(() => srv.rooms[2].state === 'RUNNING', 4000, 'room 3 running');
    assert.equal(srv.rooms[3].state, 'LOBBY');
    await sleep(300);
    assert.ok(bots.every((b) => b.syncPayloads.length > 0), 'the bots play along');
  } finally {
    for (const c of all) c.close();
    await srv.close();
  }
});

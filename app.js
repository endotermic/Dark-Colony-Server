'use strict';

// Simple unencrypted TCP game server skeleton.
// Players connect via TCP (e.g. client ephemeral port like 57094) to server port 8888.
// Protocol: newline-delimited JSON messages. Each line must be a complete JSON object.
// Example message from client: {"type":"ping"}\n
// Design goals:
//  - Keep connections list for broadcasting.
//  - Basic heartbeat / idle timeout.
//  - Graceful error handling & logging.
//  - Easily extendable command handlers.

const net = require('net');

const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : 8888;
const HOST = '0.0.0.0';
const IDLE_TIMEOUT_MS = 60_000; // disconnect idle clients after 60s

let nextClientId = 1;
const clients = new Map(); // id -> { socket, buffer, lastActivity }

function log(...args) {
  const ts = new Date().toISOString();
  console.log(`[${ts}]`, ...args);
}

function send(socket, obj) {
  try {
    socket.write(JSON.stringify(obj) + '\n');
  } catch (err) {
    log('Send error', err.message);
  }
}

function broadcast(obj, exceptId = null) {
  const payload = JSON.stringify(obj) + '\n';
  for (const [id, c] of clients) {
    if (id === exceptId) continue;
    c.socket.write(payload);
  }
}

function disconnect(id, reason) {
  const client = clients.get(id);
  if (!client) return;
  try { client.socket.destroy(); } catch (_) { /* ignore */ }
  clients.delete(id);
  log(`Client ${id} disconnected${reason ? ' (' + reason + ')' : ''}. Active: ${clients.size}`);
}

function handleMessage(id, msg) {
  const client = clients.get(id);
  if (!client) return;
  client.lastActivity = Date.now();

  // Basic routing by type field.
  switch (msg.type) {
    case 'ping':
      send(client.socket, { type: 'pong', t: Date.now() });
      break;
    case 'chat': {
      if (typeof msg.text === 'string' && msg.text.length <= 200) {
        broadcast({ type: 'chat', from: id, text: msg.text });
      } else {
        send(client.socket, { type: 'error', error: 'Invalid chat text' });
      }
      break;
    }
    case 'who': {
      send(client.socket, { type: 'who', players: Array.from(clients.keys()) });
      break;
    }
    default:
      send(client.socket, { type: 'error', error: 'Unknown type' });
  }
}

const server = net.createServer((socket) => {
  const id = nextClientId++;
  const remote = `${socket.remoteAddress}:${socket.remotePort}`;
  clients.set(id, { socket, buffer: '', lastActivity: Date.now() });
  log(`Client ${id} connected from ${remote}. Active: ${clients.size}`);

  send(socket, { type: 'welcome', id, message: 'Welcome to Dark Colony (unencrypted TCP).'});
  broadcast({ type: 'join', id }, id);

  socket.setEncoding('utf8');

  socket.on('data', (chunk) => {
    const client = clients.get(id);
    if (!client) return;
    client.buffer += chunk;

    // Process full lines.
    let idx;
    while ((idx = client.buffer.indexOf('\n')) >= 0) {
      const line = client.buffer.slice(0, idx).trim();
      client.buffer = client.buffer.slice(idx + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch (err) {
        send(socket, { type: 'error', error: 'Invalid JSON' });
        continue;
      }
      handleMessage(id, msg);
    }
  });

  socket.on('error', (err) => {
    log(`Client ${id} error:`, err.message);
  });

  socket.on('close', () => {
    disconnect(id, 'closed');
    broadcast({ type: 'leave', id });
  });
});

server.on('error', (err) => {
  log('Server error:', err);
});

server.listen(PORT, HOST, () => {
  log(`Game server listening on ${HOST}:${PORT} (unencrypted TCP)`);
});

// Heartbeat / idle cleanup interval
setInterval(() => {
  const now = Date.now();
  for (const [id, client] of clients) {
    if (now - client.lastActivity > IDLE_TIMEOUT_MS) {
      send(client.socket, { type: 'disconnect', reason: 'idle' });
      disconnect(id, 'idle timeout');
    }
  }
}, 10_000);
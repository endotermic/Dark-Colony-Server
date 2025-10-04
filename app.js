'use strict';

// Simple unencrypted TCP game server skeleton.
// Players connect via TCP (e.g. client ephemeral port like 57094) to server port 8888.
// Protocol: binary packets
// Design goals:
//  - Keep connections list for broadcasting.
//  - Basic heartbeat / idle timeout.
//  - Graceful error handling & logging.

const net = require('net');

const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : 8888;
const HOST = '0.0.0.0';
const IDLE_TIMEOUT_MS = 5_000; // disconnect idle clients after 60s

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

// Optional: helper to send initial binary handshake/message.
function sendInitialBinaryPacket(socket) {
  const packet = Buffer.from([0x08, 0x00, 0x64, 0x0f, 0x00, 0x01, 0x00, 0x00]);
  try {
    socket.write(packet);
    log('Sent initial binary packet to client');
  } catch (err) {
    log('Failed to send initial binary packet:', err.message);
  }
}

// Second initialization binary packet.
function sendSecondBinaryPacket(socket) {
  // Component byte arrays (now using ASCII string prefixes for 'Player')
    const bytesInit = [0x26, 0x11, 0x69, 0x00, 0x00,
        0x6c, 0x00, 0x00,
        0x6c, 0x00, 0x02,
        0x6c, 0x00, 0x03,
        0x6c, 0x00, 0x04,
        0x6c, 0x00, 0x05,
        0x6c, 0x00, 0x06,
        0x6c, 0x00, 0x07,
        0x67, 0x00, 0x00];
  const bytesPlayer0 = [...Buffer.from('Player0\0', 'ascii'), 0x66, 0x00, 0x00, 0x6a, 0x02, 0x00, 0x6e, 0x00, 0x00, 0x68, 0x01, 0x00, 0x67, 0x02, 0x00];
  const bytesPlayer2 = [...Buffer.from('Player2\0', 'ascii'), 0x66, 0x00, 0x02, 0x6a, 0x03, 0x02, 0x6e, 0x02, 0x02, 0x68, 0x00, 0x02, 0x67, 0x03, 0x00];
  const bytesPlayer3 = [...Buffer.from('Player3\0', 'ascii'), 0x66, 0x01, 0x03, 0x6a, 0x03, 0x03, 0x6e, 0x03, 0x03, 0x68, 0x00, 0x03, 0x67, 0x04, 0x00];
  const bytesPlayer4 = [...Buffer.from('Player4\0', 'ascii'), 0x66, 0x00, 0x04, 0x6a, 0x03, 0x04, 0x6e, 0x04, 0x04, 0x68, 0x00, 0x04, 0x67, 0x05, 0x00];
  const bytesPlayer5 = [...Buffer.from('Player5\0', 'ascii'), 0x66, 0x01, 0x05, 0x6a, 0x03, 0x05, 0x6e, 0x05, 0x05, 0x68, 0x00, 0x05, 0x67, 0x06, 0x00];
  const bytesPlayer6 = [...Buffer.from('Player6\0', 'ascii'), 0x66, 0x00, 0x06, 0x6a, 0x03, 0x06, 0x6e, 0x06, 0x06, 0x68, 0x00, 0x06, 0x67, 0x07, 0x00];
  const bytesPlayer7 = [...Buffer.from('Player7\0', 'ascii'), 0x66, 0x01, 0x07, 0x6a, 0x03, 0x07, 0x6e, 0x07, 0x07, 0x68, 0x00, 0x07, 0x67, 0x01, 0x00];
  const bytesPlayer1 = [...Buffer.from('Player1\0', 'ascii'), 0x66, 0x01, 0x01, 0x6a, 0x02, 0x01, 0x6c, 0x01, 0x01, 0x6e, 0x01, 0x01, 0x68, 0x01, 0x01];
  const bytesParams = [
    0x6f, 0x00, 0x00, 0x00, 0x00,
    0x6f, 0x01, 0x00, 0x00, 0x00,
    0x6f, 0x02, 0x00, 0x01, 0x00,
    0x6f, 0x03, 0x00, 0x00, 0x00,
    0x6f, 0x04, 0x00, 0x04, 0x00,
    0x6f, 0x05, 0x00, 0x04, 0x00,
    0x6f, 0x06, 0x00, 0x00, 0x00,
    0x6f, 0x07, 0x00, 0xb8, 0x00,
    0x6f, 0x08, 0x00, 0x01, 0x00,
    0x6f, 0x09, 0x00, 0x00, 0x00,
    0x6f, 0x0a, 0x00, 0x00, 0x00,
    0x6f, 0x0b, 0x00, 0x00, 0x00,
    0x6f, 0x0c, 0x00, 0x00, 0x00,
    0x6f, 0x0d, 0x00, 0x00, 0x00,
    0x6f, 0x0e, 0x00, 0x00, 0x00,
    0x6f, 0x0f, 0x00, 0x00, 0x00,
    0x00
  ];

  // Concatenate all parts
  const allBytes = [
    ...bytesInit,
    ...bytesPlayer0,
    ...bytesPlayer2,
    ...bytesPlayer3,
    ...bytesPlayer4,
    ...bytesPlayer5,
    ...bytesPlayer6,
    ...bytesPlayer7,
    ...bytesPlayer1,
    ...bytesParams
  ];

  try {
    socket.write(Buffer.from(allBytes));
    log('Sent second binary init packet (constructed from component arrays, length=' + allBytes.length + ')');
  } catch (err) {
    log('Failed to send second binary packet:', err.message);
  }
}

// Map selection packet (indicates which map will be played)
function sendMapPacket(socket) {
  const bytes = [
    0x54, 0xe0, 0x69, 0x4a, 0x34, 0x50, 0x4c, 0x41, 0x59, 0x30, 0x31, 0x2e, 0x53, 0x43, 0x4e, 0x00,
    0x34, 0x20, 0x4b, 0x69, 0x6e, 0x67, 0x64, 0x6f, 0x6d, 0x73, 0x0a, 0x20, 0x20, 0x20, 0x20, 0x20,
    0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20,
    0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x28, 0x34, 0x20, 0x50, 0x6c, 0x61, 0x79, 0x65,
    0x72, 0x20, 0x4a, 0x75, 0x6e, 0x67, 0x6c, 0x65, 0x20, 0x4d, 0x61, 0x70, 0x20, 0x29, 0x00, 0x00
  ];
  try {
    socket.write(Buffer.from(bytes));
    log('Sent map packet (length=' + bytes.length + ')');
  } catch (err) {
    log('Failed to send map packet:', err.message);
  }
}

const server = net.createServer((socket) => {
  const id = nextClientId++;
  const remote = `${socket.remoteAddress}:${socket.remotePort}`;
  clients.set(id, { socket, buffer: '', lastActivity: Date.now() });
  log(`Client ${id} connected from ${remote}. Active: ${clients.size}`);

  // Send required binary packets immediately after connection established.
  sendInitialBinaryPacket(socket);
  sendSecondBinaryPacket(socket);
  // sendMapPacket(socket); // commented out per request

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
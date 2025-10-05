'use strict';

// Simple unencrypted TCP game server skeleton.
// Players connect via TCP to server port 8888.
// Protocol: binary packets + optional JSON lines.

const net = require('net');

const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : 8888;
const HOST = '0.0.0.0';
const IDLE_TIMEOUT_MS = process.env.IDLE_TIMEOUT_MS ? parseInt(process.env.IDLE_TIMEOUT_MS, 10) : 5_000; // disconnect idle clients after 5s

let nextClientId = 1;
const clients = new Map(); // id -> { socket, buffer, lastActivity }

function log(...args) {
  const ts = new Date().toISOString();
  console.log(`[${ts}]`, ...args);
}

function safeWrite(socket, data) {
  if (!socket || socket.destroyed) return false;
  try {
    const ok = socket.write(data);
    if (!ok) {
      socket.once('drain', () => log('Socket drain event (backpressure relieved)'));
    }
    return ok;
  } catch (err) {
    log('Write error:', err.message);
    return false;
  }
}

function send(socket, obj) {
  safeWrite(socket, JSON.stringify(obj) + '\n');
}

function disconnect(id, reason) {
  const client = clients.get(id);
  if (!client) return;
  try { client.socket.destroy(); } catch (_) { /* ignore */ }
  clients.delete(id);
  log(`Client ${id} disconnected${reason ? ' (' + reason + ')' : ''}. Active: ${clients.size}`);
}

function sendInitialBinaryPacket(socket) {
  const packet = Buffer.from([0x08, 0x00, 0x64, 0x0f, 0x00, 0x01, 0x00, 0x00]);
  if (!safeWrite(socket, packet)) log('Failed to send initial binary packet');
  else log('Sent initial binary packet to client');
}

function sendSecondBinaryPacket(socket) {
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
  if (!safeWrite(socket, Buffer.from(allBytes))) log('Failed to send second binary init packet');
  else log('Sent second binary init packet (length=' + allBytes.length + ')');
}

function sendMapPacket(socket) {
  const bytes = [
    0x54, 0xe0, 0x69, 0x4a, 0x34, 0x50, 0x4c, 0x41, 0x59, 0x30, 0x31, 0x2e, 0x53, 0x43, 0x4e, 0x00,
    0x34, 0x20, 0x4b, 0x69, 0x6e, 0x67, 0x64, 0x6f, 0x6d, 0x73, 0x0a, 0x20, 0x20, 0x20, 0x20, 0x20,
    0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20,
    0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x28, 0x34, 0x20, 0x50, 0x6c, 0x61, 0x79, 0x65,
    0x72, 0x20, 0x4a, 0x75, 0x6e, 0x67, 0x6c, 0x65, 0x20, 0x4d, 0x61, 0x70, 0x20, 0x29, 0x00, 0x00
  ];
  if (!safeWrite(socket, Buffer.from(bytes))) log('Failed to send map packet');
  else log('Sent map packet (length=' + bytes.length + ')');
}

// Binary parsing helpers per user instructions
const DATA_HEADER = Buffer.from([0xef, 0xbf, 0xbd]);
const IGNORED_SINGLE_BYTES = new Set([0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xa0, 0xb0, 0xc0, 0xd0, 0xe0, 0xf0]);
const COMMANDS = {
  ping: Buffer.from([0x71, 0x00]),
  player_name: Buffer.from([0x67, 0x01, 0x00]),
  player_chat: Buffer.from([0x65, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72]),
  player_race: Buffer.from([0x66]),  // value after command is humans=0x00, aliens=0x01 
  player_color: Buffer.from([0x6b]), // value after command is in range 0x01..0x07
  player_team: Buffer.from([0x6d])   // value after command is in range 0x01..0x07
};

function parseClientBinary(id, buf) {
  if (!Buffer.isBuffer(buf) || buf.length === 0) return;
  let offset = 0;
  // Ignore first byte of chunk
  offset += 1;
  // Skip any subsequent ignored markers (single bytes 0x00..0xf0 or DATA_HEADER sequences)
  while (offset < buf.length) {
    if (buf.length - offset >= DATA_HEADER.length && buf[offset] === DATA_HEADER[0] && buf[offset+1] === DATA_HEADER[1] && buf[offset+2] === DATA_HEADER[2]) {
      offset += DATA_HEADER.length;
      continue;
    }
    if (IGNORED_SINGLE_BYTES.has(buf[offset])) { offset += 1; continue; }
    break;
  }
  if (offset >= buf.length) return;
  const remaining = buf.slice(offset);
  let matched = false;
  for (const [name, pattern] of Object.entries(COMMANDS)) {
    if (remaining.length >= pattern.length && remaining.slice(0, pattern.length).equals(pattern)) {
      matched = true;
      if (name === 'player_name') {
        const after = remaining.slice(pattern.length);
        let end = after.indexOf(0x00);
          if (end === -1) end = after.length; // take all if no terminator
          const playerName = after.slice(0, end).toString('ascii');
        log(`Binary command from Client ${id}: ${name} ${playerName ? '(' + playerName + ')' : ''}`);
      } else if (name === 'player_chat') {
        const after = remaining.slice(pattern.length);
        // Chat message assumed to be null-terminated or rest of buffer
        let end = after.indexOf(0x00);
        if (end === -1) end = after.length;
        const chatMsg = after.slice(0, end).toString('ascii');
        log(`Binary command from Client ${id}: ${name}${chatMsg ? ' ' + chatMsg : ''}`);
      } else {
        log(`Binary command from Client ${id}: ${name}`);
      }
      break;
    }
  }
  if (!matched) log(`Unknown binary data from Client ${id}: ${remaining.toString('hex')}`);
}

const server = net.createServer((socket) => {
  const id = nextClientId++;
  const remote = `${socket.remoteAddress}:${socket.remotePort}`;
  clients.set(id, { socket, buffer: '', lastActivity: Date.now() });
  log(`Client ${id} connected from ${remote}. Active: ${clients.size}`);

  socket.setKeepAlive(true, 30_000);

  sendInitialBinaryPacket(socket);
  sendSecondBinaryPacket(socket);
  // sendMapPacket(socket); // optional

  socket.on('data', (chunk) => {
    const client = clients.get(id); if (!client) return;
    client.lastActivity = Date.now();
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk, 'utf8');
    //log(`Received raw packet from Client ${id}. Hex ${buf.toString('hex')}`);
    parseClientBinary(id, buf);
    const str = buf.toString('ascii');
    client.buffer += str;
  });

  socket.on('end', () => log(`Client ${id} ended connection`));
  socket.on('error', (err) => log(`Client ${id} error:`, err.message));
  socket.on('close', (hadError) => { disconnect(id, hadError ? 'closed with error' : 'closed'); });
});

server.on('error', (err) => { log('Server error:', err); });
server.listen(PORT, HOST, () => { log(`Game server listening on ${HOST}:${PORT} (unencrypted TCP)`); });

setInterval(() => {
  const now = Date.now();
  for (const [id, client] of clients) {
    const idleFor = now - client.lastActivity;
    if (idleFor > IDLE_TIMEOUT_MS) {
      send(client.socket, { type: 'disconnect', reason: 'idle' });
      disconnect(id, `idle timeout ${idleFor}ms`);
    }
  }
}, 10_000);
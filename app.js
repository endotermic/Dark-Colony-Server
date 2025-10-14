'use strict';

// Simple unencrypted TCP game server skeleton.
// Players connect via TCP to server port 8888.
// Protocol: binary packets + optional JSON lines.

const net = require('net');

const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : 8888;
const HOST = '0.0.0.0';
const IDLE_TIMEOUT_MS = process.env.IDLE_TIMEOUT_MS ? parseInt(process.env.IDLE_TIMEOUT_MS, 10) : 5_000; // disconnect idle clients after 5s

let nextClientId = 1;
const clients = new Map(); // id -> { id, socket, buffer, lastActivity }
let packetCounter = 0x00; // increments 0x00,0x10,...,0xf0 then wraps

function log(...args) {
  const ts = new Date().toISOString();
  console.log(`[${ts}]`, ...args);
}

function safeWrite(socket, data) {
  if (!socket || socket.destroyed) return false;
  // Added detailed logging of what is being written
  try {
    const isBuffer = Buffer.isBuffer(data);
    const length = isBuffer ? data.length : Buffer.byteLength(data, 'utf8');
    // Produce a preview (hex for buffers, plain for strings) limited in size
    let preview;
    if (isBuffer) {
      preview = data.toString('hex');
      if (preview.length > 160) preview = preview.slice(0, 160) + '...';
    } else {
      preview = data;
      if (preview.length > 160) preview = preview.slice(0, 160) + '...';
    }
    log(`safeWrite -> ${isBuffer ? 'Buffer' : 'String'} len=${length} preview=${preview}`);

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

/**
 * Sends a framed binary packet.
 * Layout: [ length, counter, <payload bytes>, 0x00 ]
 * - length: total packet length INCLUDING the length byte itself.
 * - counter: cycles 0x00 -> 0x10 -> ... -> 0xF0 -> 0x00 (step 0x10)
 * - last byte always 0x00 terminator
 * @param {net.Socket} socket
 * @param {Buffer|number[]|string} payload - Raw payload bytes (without length / counter / terminator). String will be encoded as ASCII.
 */
function sendFramedPacket(socket, payload) {
  if (!socket || socket.destroyed) return false;
  let payloadBuf;
  if (Buffer.isBuffer(payload)) {
    payloadBuf = payload;
  } else if (Array.isArray(payload)) {
    payloadBuf = Buffer.from(payload);
  } else if (typeof payload === 'string') {
    payloadBuf = Buffer.from(payload, 'ascii');
  } else {
    throw new TypeError('Unsupported payload type for sendFramedPacket');
  }

  // Compute packet
  const totalLen = 1 /*len*/ + 1 /*counter*/ + payloadBuf.length + 1 /*terminator*/;
  if (totalLen > 0xFF) throw new RangeError('Packet too long for single-byte length: ' + totalLen);

  const counter = packetCounter;
  // Prepare next counter value
  packetCounter += 0x10;
  if (packetCounter > 0xF0) packetCounter = 0x00;

  const packet = Buffer.alloc(totalLen);
  let o = 0;
  packet[o++] = totalLen; // length byte (includes itself)
  packet[o++] = counter;  // counter byte
  payloadBuf.copy(packet, o); o += payloadBuf.length;
  packet[o++] = 0x00; // terminator

  log(`sendFramedPacket len=${totalLen} counter=0x${counter.toString(16).padStart(2,'0')} payloadLen=${payloadBuf.length}`);
  return safeWrite(socket, packet);
}

/**
 * New generic packet sender.
 * Rules:
 *  - First byte stores low 8 bits of total packet length (including length/counter and trailing 0x00 terminator).
 *  - Second byte high nibble is the packet counter cycling 0x0..0xF (encoded as 0x00,0x10,...,0xF0).
 *  - Second byte low nibble stores the high 4 bits of the packet length (only used when length > 0xFF).
 *  - Counter increments by 0x10 and wraps after 0xF0 back to 0x00.
 *  - Last byte of every packet is 0x00 terminator.
 * Max encodable length = 0x0FFF (4095). If exceeded an error is thrown.
 * @param {net.Socket} socket
 * @param {Buffer|number[]|string} payload Raw payload bytes (excluding header & terminator)
 */
function sendPacket(socket, payload) {
  if (!socket || socket.destroyed) return false;
  // Initialize per-socket counter if missing
  if (socket.__packetCounter === undefined) socket.__packetCounter = 0x00; // values 0x00,0x10,...,0xF0
  let payloadBuf;
  if (Buffer.isBuffer(payload)) {
    payloadBuf = payload;
  } else if (Array.isArray(payload)) {
    payloadBuf = Buffer.from(payload);
  } else if (typeof payload === 'string') {
    payloadBuf = Buffer.from(payload, 'ascii');
  } else {
    throw new TypeError('Unsupported payload type for sendPacket');
  }

  const totalLen = 2 + payloadBuf.length + 1;
  if (totalLen > 0x0FFF) throw new RangeError('Packet too long (max 4095 bytes): ' + totalLen);

  const lenLow = totalLen & 0xFF;
  const lenHigh = (totalLen >> 8) & 0x0F;

  const counterByteValue = socket.__packetCounter; // 0x00..0xF0
  const counterNibble = (counterByteValue >> 4) & 0x0F;
  const counterByte = (counterNibble << 4) | lenHigh;

  // Advance per-socket counter
  socket.__packetCounter += 0x10;
  if (socket.__packetCounter > 0xF0) socket.__packetCounter = 0x00;

  const packet = Buffer.alloc(totalLen);
  let o = 0;
  packet[o++] = lenLow;
  packet[o++] = counterByte;
  payloadBuf.copy(packet, o); o += payloadBuf.length;
  packet[o++] = 0x00;

  log(`sendPacket [clientCounter=0x${counterNibble.toString(16)}] len=${totalLen} (low=0x${lenLow.toString(16).padStart(2,'0')} high=0x${lenHigh.toString(16)}) payloadLen=${payloadBuf.length}`);
  return safeWrite(socket, packet);
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
  const packet = Buffer.from([0x64, 0x0f, 0x00, 0x01, 0x00]);
  if (!sendPacket(socket, packet)) log('Failed to send initial binary packet');
  else log('Sent initial binary packet to client');
}

function sendSecondBinaryPacket(socket) {
  const bytesInit = [0x69, 0x00, 0x00,
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
    0x6f, 0x0f, 0x00, 0x00, 0x00
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



    const roomWithMap = [0x69, 0x44, 0x38, 0x50, 0x4c, 0x41, 0x59, 0x30, 0x31, 0x2e, 0x53, 0x43, 0x4e, 0x0, 0x41, 0x72, 0x6d, 0x61, 0x67, 0x65, 0x64, 0x64, 0x6f, 0x6e, 0xa, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x28, 0x38, 0x20, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x20, 0x44, 0x65, 0x73, 0x65, 0x72, 0x74, 0x20, 0x4d, 0x61, 0x70, 0x20, 0x29, 0x0, 0x6c, 0x0, 0x0, 0x6c, 0x0, 0x2, 0x6c, 0x0, 0x3, 0x6c, 0x0, 0x4, 0x6c, 0x0, 0x5, 0x6c, 0x0, 0x6, 0x6c, 0x0, 0x7, 0x67, 0x0, 0x0, 0x65, 0x6e, 0x64, 0x6f, 0x74, 0x65, 0x72, 0x6d, 0x69, 0x63, 0x0, 0x66, 0x0, 0x0, 0x6a, 0x2, 0x0, 0x6e, 0x0, 0x0, 0x68, 0x1, 0x0, 0x67, 0x2, 0x0, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x32, 0x0, 0x66, 0x0, 0x2, 0x6a, 0x3, 0x2, 0x6e, 0x2, 0x2, 0x68, 0x0, 0x2, 0x67, 0x3, 0x0, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x33, 0x0, 0x66, 0x1, 0x3, 0x6a, 0x3, 0x3, 0x6e, 0x3, 0x3, 0x68, 0x0, 0x3, 0x67, 0x4, 0x0, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x34, 0x0, 0x66, 0x0, 0x4, 0x6a, 0x3, 0x4, 0x6e, 0x4, 0x4, 0x68, 0x0, 0x4, 0x67, 0x5, 0x0, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x35, 0x0, 0x66, 0x1, 0x5, 0x6a, 0x3, 0x5, 0x6e, 0x5, 0x5, 0x68, 0x0, 0x5, 0x67, 0x6, 0x0, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x36, 0x0, 0x66, 0x0, 0x6, 0x6a, 0x3, 0x6, 0x6e, 0x6, 0x6, 0x68, 0x0, 0x6, 0x67, 0x7, 0x0, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x37, 0x0, 0x66, 0x1, 0x7, 0x6a, 0x3, 0x7, 0x6e, 0x7, 0x7, 0x68, 0x0, 0x7, 0x67, 0x1, 0x0, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x31, 0x0, 0x66, 0x1, 0x1, 0x6a, 0x2, 0x1, 0x6c, 0x1, 0x1, 0x6e, 0x1, 0x1, 0x68, 0x1, 0x1, 0x6f, 0x0, 0x0, 0x0, 0x0, 0x6f, 0x1, 0x0, 0x0, 0x0, 0x6f, 0x2, 0x0, 0x1, 0x0, 0x6f, 0x3, 0x0, 0x0, 0x0, 0x6f, 0x4, 0x0, 0x4, 0x0, 0x6f, 0x5, 0x0, 0x4, 0x0, 0x6f, 0x6, 0x0, 0x0, 0x0, 0x6f, 0x7, 0x0, 0x6c, 0x0, 0x6f, 0x8, 0x0, 0x1, 0x0, 0x6f, 0x9, 0x0, 0x0, 0x0, 0x6f, 0xa, 0x0, 0x0, 0x0, 0x6f, 0xb, 0x0, 0x0, 0x0, 0x6f, 0xc, 0x0, 0x0, 0x0, 0x6f, 0xd, 0x0, 0x0, 0x0, 0x6f, 0xe, 0x0, 0x0, 0x0, 0x6f, 0xf, 0x0, 0x0, 0x0, 0x0, 0x4, 0x20, 0x71];





    if (!sendPacket(socket, Buffer.from(allBytes))) log('Failed to send second binary init packet');
    else log('Sent second binary init packet (length=' + allBytes.length + ')');
}

function sendMapPacket(socket) {
  const armageddon = [0x69, 0x44, 0x38, 0x50, 0x4c, 0x41, 0x59, 0x30, 0x31, 0x2e, 0x53, 0x43, 0x4e, 0x0, 0x41, 0x72, 0x6d, 0x61, 0x67, 0x65, 0x64, 0x64, 0x6f, 0x6e, 0xa, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x28, 0x38, 0x20, 0x50, 0x6c, 0x61, 0x79, 0x65, 0x72, 0x20, 0x44, 0x65, 0x73, 0x65, 0x72, 0x74, 0x20, 0x4d, 0x61, 0x70, 0x20, 0x29, 0x0];

  const bytes = [
    0x69, 0x4a, 0x34, 0x50, 0x4c, 0x41, 0x59, 0x30, 0x31, 0x2e, 0x53, 0x43, 0x4e, 0x00,
    0x34, 0x20, 0x4b, 0x69, 0x6e, 0x67, 0x64, 0x6f, 0x6d, 0x73, 0x0a, 0x20, 0x20, 0x20, 0x20, 0x20,
    0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20,
    0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20, 0x28, 0x34, 0x20, 0x50, 0x6c, 0x61, 0x79, 0x65,
    0x72, 0x20, 0x4a, 0x75, 0x6e, 0x67, 0x6c, 0x65, 0x20, 0x4d, 0x61, 0x70, 0x20, 0x29, 0x00
    ];

    const bytesCounter = [0x54, 0x20];
    const bytesCommand = [0x69]; // room_map command
    const bytesMapType = [0x4a]; // map type J=0x4a or D=0x44
    const bytesPlayersCount = [0x34]; // '4'=0x34 players 
    const bytesMapFilename = [...Buffer.from('PLAY01.SCN\0', 'ascii')]; // null-terminated
    const bytesMapDisplayName = [...Buffer.from('4 Kingdoms\n                    (4 Player Jungle Map )\0', 'ascii')]; // null-terminated

    const allBytes = [
        ...bytesCounter,
        ...bytesCommand,
        ...bytesMapType,
        ...bytesPlayersCount,
        ...bytesMapFilename,
        ...bytesMapDisplayName,
        ...[0x00] // null byte at the end
    ];
    if (!sendPacket(socket, Buffer.from(armageddon))) log('Failed to send map packet');
    else log('Sent map packet (length=' + armageddon.length + ')');
}

// Generic command packet helper: builds [commandBytes...][data bytes][0x00 terminator]
function sendCommandPacket(socket, command, data) {
  if (!socket || socket.destroyed) {
    log('sendCommandPacket: socket invalid/destroyed');
    return; // no value returned
  }
  let commandBuf;
  try {
    if (typeof command === 'number') {
      if (command < 0 || command > 0xFF) throw new TypeError('Single command byte must be 0..255');
      commandBuf = Buffer.from([command]);
    } else if (Array.isArray(command)) {
      if (command.length === 0) throw new TypeError('command array empty');
      commandBuf = Buffer.from(command.map(b => {
        if (typeof b !== 'number' || b < 0 || b > 0xFF) throw new TypeError('command array values must be bytes (0..255)');
        return b;
      }));
    } else if (Buffer.isBuffer(command)) {
      if (command.length === 0) throw new TypeError('command buffer empty');
      commandBuf = command;
    } else {
      throw new TypeError('command must be number | number[] | Buffer');
    }
  } catch (e) {
    log('sendCommandPacket command error:', e.message);
    return; // stop on invalid command
  }

  let dataBuf;
  if (data == null) dataBuf = Buffer.alloc(0);
  else if (Buffer.isBuffer(data)) dataBuf = data;
  else if (Array.isArray(data)) dataBuf = Buffer.from(data);
  else if (typeof data === 'string') dataBuf = Buffer.from(data, 'ascii');
  else dataBuf = Buffer.from(String(data), 'ascii');

  // Do NOT append an internal 0x00 terminator; sendPacket itself will append the single packet terminator.
  const payload = Buffer.concat([commandBuf, dataBuf]);
  const sent = sendPacket(socket, payload);
  const cmdHex = commandBuf.toString('hex');
  log(sent ? `Sent command (${cmdHex}) payloadBytes=${payload.length}` : `Failed to send command (${cmdHex})`);
  // Purposefully no return value
}

// NEW: helper to echo back player name changes (simplistic format 0x67 <name bytes> 0x00)
function sendPlayerName(socket, name) {
  if (!name) name = '';
  // sanitize to ascii and limit length
  const clean = Buffer.from(name.replace(/[^\x20-\x7e]/g,'').slice(0,32),'ascii');
  const payload = Buffer.concat([Buffer.from([0x67, 0x01, 0x00]), clean, Buffer.from([0x00])]);
  if (!sendPacket(socket, payload)) log('Failed to echo player_name');
  else log('Echoed player_name: ' + name);
}

// NEW: helper to echo chat message (format 0x65 <msg bytes> 0x00)
function sendPlayerChat(socket, msg) {
  if (!msg) msg = '';
  const clean = Buffer.from(msg.replace(/\r|\n/g,'').slice(0,120),'ascii');
  const payload = Buffer.concat([Buffer.from([0x65]), clean, Buffer.from([0x00])]);
  if (!sendPacket(socket, payload)) log('Failed to echo player_chat');
  else log('Echoed player_chat: ' + msg);
}

// Binary parsing helpers per user instructions
const DATA_HEADER = Buffer.from([0xef, 0xbf, 0xbd]);
const IGNORED_SINGLE_BYTES = new Set([0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xa0, 0xb0, 0xc0, 0xd0, 0xe0, 0xf0]);
const ROOM_COMMANDS = {
  begin_battle: Buffer.from([0x76]), // [0x76, 0x06, 0x00, 0x02]
  ping: Buffer.from([0x71]),
  player_ready: Buffer.from([0x68]), // plus player index byte in range 0x01..0x08 followed by readyness byte 0x00 or 0x01
  player_name: Buffer.from([0x67, 0x01, 0x00]),
  player_chat: Buffer.from([0x65]),
  player_race: Buffer.from([0x66]),  // value after command is humans=0x00, aliens=0x01 
  player_color: Buffer.from([0x6b]), // value after command is in range 0x01..0x07
  player_team: Buffer.from([0x6d]),  // value after command is in range 0x01..0x07
  room_greeting: Buffer.from([0x6f]),
  room_erupting_vents: Buffer.from([0x6f, 0x02, 0x00]), // erupting vents command following by 0x00 or 0x01 and two zero bytes
  room_renewable_vents: Buffer.from([0x6f, 0x03, 0x00]), // renewable vents command following by 0x00 or 0x01 and two zero bytes
  room_map: Buffer.from([0x69]), // two consecutive null terminated strings: map filename, map display name

  hz1: Buffer.from([0x02]),
  hz2: Buffer.from([0x08]),
};

function parseClientBinary(client, buf) {
  if (!client || !Buffer.isBuffer(buf) || buf.length === 0) return;
  const id = client.id;
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
  for (const [name, pattern] of Object.entries(ROOM_COMMANDS)) {
    if (remaining.length >= pattern.length && remaining.slice(0, pattern.length).equals(pattern)) {
      matched = true;
      if (name === 'player_name') {
        const after = remaining.slice(pattern.length);
        let end = after.indexOf(0x00);
        if (end === -1) end = after.length;
        const playerName = after.slice(0, end).toString('ascii');
        log(`Binary command from Client ${id}: ${name} ${playerName ? '(' + playerName + ')' : ''}`);
        sendPlayerName(client.socket, playerName);
      } else if (name === 'player_chat') {
        const after = remaining.slice(pattern.length);
        let end = after.indexOf(0x00);
        if (end === -1) end = after.length;
        const chatMsg = after.slice(0, end).toString('ascii');
        log(`Binary command from Client ${id}: ${name}${chatMsg ? ' ' + chatMsg : ''}`);
        sendPlayerChat(client.socket, chatMsg);
      } else if (name === 'player_ready') {
          log(`Binary command from Client ${id}: ${name} -> echoing readiness back`);
          sendCommandPacket(client.socket, ROOM_COMMANDS.player_ready, Buffer.from([0x02, 0x01]));
          sendCommandPacket(client.socket, ROOM_COMMANDS.hz1, Buffer.from([0x00, 0x00, 0x00, 0x00, 0x09, 0x00, 0x00, 0x00]));
          sendCommandPacket(client.socket, ROOM_COMMANDS.hz1, Buffer.from([0x00, 0x00, 0x00, 0x00, 0x0a, 0x00, 0x00, 0x00]));
      } else if (name === 'room_greeting') {
        log(`Binary command from Client ${id}: ${name} -> room greeting`);
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
  const clientObj = { id, socket, buffer: '', lastActivity: Date.now() };
  clients.set(id, clientObj);
  socket.__packetCounter = 0x00; // initialize per-client packet counter
  log(`Client ${id} connected from ${remote}. Active: ${clients.size}`);

  socket.setKeepAlive(true, 30_000);

  sendInitialBinaryPacket(socket);
  sendSecondBinaryPacket(socket);
  sendMapPacket(socket);
  sendCommandPacket(socket, ROOM_COMMANDS.player_chat, 'Greetings to the Dark Colony online!');
  sendCommandPacket(socket, ROOM_COMMANDS.player_ready, Buffer.from([0x02, 0x00]));

  socket.on('data', (chunk) => {
    const client = clients.get(id); if (!client) return;
    client.lastActivity = Date.now();
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk, 'utf8');
    log(`Received raw packet from Client ${id}. Hex ${buf.toString('hex')}`);
    parseClientBinary(client, buf);
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
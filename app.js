/*!
 * (c) 2025 Nikolajs Agafonovs
 * Licensed under the AGPL-3.0-or-later license.
 * This server may be used only in open source projects.
 * Source code must remain publicly available under the same license.
 */

'use strict';

// Simple unencrypted TCP game server skeleton.
// Players connect via TCP to server port 8888.
// Protocol: binary packets + optional JSON lines.

const net = require('net');

const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : 8888;
const HOST = '0.0.0.0';
const IDLE_TIMEOUT_MS = process.env.IDLE_TIMEOUT_MS ? parseInt(process.env.IDLE_TIMEOUT_MS, 10) : 5_000; // disconnect idle clients after 5s
const BATTLE_PING_INTERVAL_MS = 66; // battle ping interval in milliseconds
const BATTLE_PING_TIMEOUT_MS = 5000; // timeout if no echo received
const MAX_CLIENTS_PER_ROOM = 8; // maximum clients per room

let nextClientId = 1;
let nextRoomId = 1;
const clients = new Map(); // id -> { id, socket, buffer, lastActivity, battlePingState, roomId, battleInitiated }
const rooms = new Map(); // roomId -> { id, clients: Set<clientId>, inBattle: boolean }

function log(...args) {
  const ts = new Date().toISOString();
  console.log(`[${ts}]`, ...args);
}

// Room management functions
function createRoom() {
  const roomId = nextRoomId++;
  
  // Helper to get random race
  const getRandomRace = () => Math.random() < 0.5 ? 'humans' : 'aliens';
  
  // Initialize 8 player slots
  // Player 0 is always AI Easy and not ready
  // Players 1-7 are empty slots (type: none, ready: true)
  const playerSlots = [
    { index: 0, clientId: null, name: 'battle_bot', race: getRandomRace(), type: 'ai_hard', team: 0, ready: false, color: 0 },
    { index: 1, clientId: null, name: 'Player1', race: getRandomRace(), type: 'none', team: 1, ready: true, color: 1 },
    { index: 2, clientId: null, name: 'Player2', race: getRandomRace(), type: 'none', team: 2, ready: true, color: 2 },
    { index: 3, clientId: null, name: 'Player3', race: getRandomRace(), type: 'none', team: 3, ready: true, color: 3 },
    { index: 4, clientId: null, name: 'Player4', race: getRandomRace(), type: 'none', team: 4, ready: true, color: 4 },
    { index: 5, clientId: null, name: 'Player5', race: getRandomRace(), type: 'none', team: 5, ready: true, color: 5 },
    { index: 6, clientId: null, name: 'Player6', race: getRandomRace(), type: 'none', team: 6, ready: true, color: 6 },
    { index: 7, clientId: null, name: 'Player7', race: getRandomRace(), type: 'none', team: 7, ready: true, color: 7 }
  ];
  
  const room = {
    id: roomId,
    clients: new Set(),
    inBattle: false,
    playerSlots: playerSlots,
    map: {
      type: 'D',
      playerCount: '8',
      filename: 'PLAY01.SCN',
      displayName: 'Armageddon\n                                 (8 Player Desert Map )'
    }
  };
  rooms.set(roomId, room);
  log(`Created Room ${roomId} with 8 initialized player slots`);
  return room;
}

function getAvailableRoom() {
  // Find available rooms (not in battle and have free slots)
  const availableRooms = [];
  for (const room of rooms.values()) {
    if (!room.inBattle) {
      // Check if there are free slots (slots where clientId is null and type is 'none')
      const freeSlots = room.playerSlots.filter(slot => slot.clientId === null && slot.type === 'none');
      if (freeSlots.length > 0) {
        availableRooms.push(room);
      }
    }
  }
  
  // If we have available rooms, return the one with the minimum ID
  if (availableRooms.length > 0) {
    availableRooms.sort((a, b) => a.id - b.id);
    return availableRooms[0];
  }
  
  // No available room found, create a new one
  return createRoom();
}

function getFreeSlotInRoom(room) {
  // Get all free slots (excluding the AI slot at index 0)
  const freeSlots = room.playerSlots.filter(slot => slot.clientId === null && slot.type === 'none');
  if (freeSlots.length === 0) return null;
  
  // Pick a random free slot
  const randomIndex = Math.floor(Math.random() * freeSlots.length);
  return freeSlots[randomIndex];
}

function getAvailableColor(room) {
  // Get all colors currently in use by active players (gamer or ai types)
  const usedColors = new Set();
  for (const slot of room.playerSlots) {
    if (slot.type === 'gamer' || slot.type === 'ai_easy' || slot.type === 'ai_hard') {
      usedColors.add(slot.color);
    }
  }
  
  // Find first available color (0-7)
  for (let color = 0; color <= 7; color++) {
    if (!usedColors.has(color)) {
      return color;
    }
  }
  
  // If all colors are taken, return a random color
  return Math.floor(Math.random() * 8);
}

function broadcastRoomUpdate(room, excludeClientId = null) {
  // Send updated room data and map to all connected clients in the room
  for (const clientId of room.clients) {
    if (clientId === excludeClientId) continue; // Skip the excluded client
    
    const client = clients.get(clientId);
    if (client && client.socket && !client.socket.destroyed) {
      sendRoomData(client.socket, room);
      sendMapPacket(client.socket, room);
      log(`Sent room update (including map) to Client ${clientId} in Room ${room.id}`);
    }
  }
}

function addClientToRoom(clientId, room) {
  // Find a free slot for the client
  const slot = getFreeSlotInRoom(room);
  if (!slot) {
    log(`ERROR: No free slot available in Room ${room.id} for Client ${clientId}`);
    return null;
  }
  
  // Check if there were already clients in the room before adding this one
  const hadExistingClients = room.clients.size > 0;
  
  // Find an available color for the new client
  const availableColor = getAvailableColor(room);
  
  room.clients.add(clientId);
  const client = clients.get(clientId);
  if (client) {
    client.roomId = room.id;
    client.playerSlotIndex = slot.index;
    
    // Update the slot with client info
    slot.clientId = clientId;
    slot.type = 'gamer';
    slot.ready = false;
    slot.color = availableColor;
  }
  log(`Client ${clientId} added to Room ${room.id} at slot ${slot.index} with color ${availableColor}. Room has ${room.clients.size} connected clients`);
  
  // Broadcast room update to existing clients when a new client joins
  if (hadExistingClients) {
    broadcastRoomUpdate(room, clientId);
    log(`Broadcasting room update to existing clients in Room ${room.id}`);
  }
  
  return slot.index;
}

function removeClientFromRoom(clientId) {
  const client = clients.get(clientId);
  if (!client || !client.roomId) return;
  
  const room = rooms.get(client.roomId);
  if (room) {
    room.clients.delete(clientId);
    
    // Reset the player slot
    if (client.playerSlotIndex !== undefined) {
      const slot = room.playerSlots[client.playerSlotIndex];
      if (slot) {
        slot.clientId = null;
        slot.type = 'none';
        slot.ready = true;
        log(`Client ${clientId} removed from Room ${room.id} slot ${client.playerSlotIndex}. Room has ${room.clients.size} connected clients`);
      }
    }
    
    // Broadcast room update to remaining clients when a client leaves
    if (room.clients.size > 0) {
      broadcastRoomUpdate(room);
      log(`Broadcasting room update to remaining clients in Room ${room.id} after client departure`);
    }
    
    // Clean up empty rooms that are not the first room
    if (room.clients.size === 0 && room.id > 1) {
      rooms.delete(room.id);
      log(`Room ${room.id} deleted (empty)`);
    }
  }
}

function checkAllClientsInitiatedBattle(room) {
  if (room.clients.size === 0) return false;
  
  for (const clientId of room.clients) {
    const client = clients.get(clientId);
    if (!client || !client.battleInitiated) {
      return false;
    }
  }
  return true;
}

function startRoomBattle(room) {
  room.inBattle = true;
  log(`Room ${room.id} battle started with ${room.clients.size} players. Room is now locked.`);
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

function send(socket, obj) {
  safeWrite(socket, JSON.stringify(obj) + '\n');
}

function disconnect(id, reason) {
  const client = clients.get(id);
  if (!client) return;
  
  // Remove from room first
  removeClientFromRoom(id);
  
  // Clean up battle ping state if exists
  if (client.battlePingState) {
    if (client.battlePingState.timeoutId) {
      clearTimeout(client.battlePingState.timeoutId);
    }
    client.battlePingState = null;
  }
  try { client.socket.destroy(); } catch (_) { /* ignore */ }
  clients.delete(id);
  log(`Client ${id} disconnected${reason ? ' (' + reason + ')' : ''}. Active: ${clients.size}`);
}

function sendRoomGreeting(socket, playerSlotIndex) {
  const playerIndexBytes = PLAYER_INDEX[`p${playerSlotIndex}`];
  sendCommandPacket(socket, ROOM_COMMANDS.initial_packet, Buffer.from([...PLAYER_INIT_PARAM.player_index, ...NULL_SEPARATOR, ...playerIndexBytes, ...NULL_SEPARATOR]));
  log(`Sent initial binary packet to client (assigned to slot ${playerSlotIndex})`);
}

function sendRoomData(socket, room) {
  const bytesMap = [
    0x00, 0x00 // placeholder for the map
  ];

  // Initialize all 8 player slots
  const bytesInit = [];
  for (let i = 0; i < 8; i++) {
    bytesInit.push(...ROOM_COMMANDS.player_init, ...NULL_SEPARATOR, ...PLAYER_INDEX[`p${i}`]);
  }
  
  // Build player data dynamically based on room state
  const playerBytesArray = [];
  for (const slot of room.playerSlots) {
    const playerIndex = PLAYER_INDEX[`p${slot.index}`];
    const playerRace = slot.race === 'humans' ? PLAYER_RACE.humans : PLAYER_RACE.aliens;
    const playerType = PLAYER_TYPE[slot.type];
    const playerTeam = TEAM_INDEX[`t${slot.team}`];
    const playerReady = slot.ready ? PLAYER_READY.ready : PLAYER_READY.not_ready;
    const playerColor = slot.color;
    
    const playerBytes = [
      ...ROOM_COMMANDS.player_name, ...playerIndex, ...NULL_SEPARATOR, ...Buffer.from(slot.name + '\0', 'ascii'),
      ...ROOM_COMMANDS.player_race, ...playerRace, ...playerIndex,
      ...ROOM_COMMANDS.player_type, ...playerType, ...playerIndex,
      ...ROOM_COMMANDS.player_color, playerColor, ...playerIndex,
      ...ROOM_COMMANDS.player_team2, ...playerTeam, ...playerIndex,
      ...ROOM_COMMANDS.player_ready, ...playerReady, ...playerIndex
    ];
    playerBytesArray.push(playerBytes);
  }
  
  const bytesParams = [
    ...ROOM_COMMANDS.room_param, 0x00, 0x00, 0x00, 0x00,
    ...ROOM_COMMANDS.room_param, 0x01, 0x00, 0x00, 0x00,
    ...ROOM_COMMANDS.room_param, ...ROOM_PARAM.erupting_vents, 0x00, 0x01, 0x00,
    ...ROOM_COMMANDS.room_param, ...ROOM_PARAM.renewable_vents, 0x00, 0x00, 0x00,
    ...ROOM_COMMANDS.room_param, 0x04, 0x00, 0x04, 0x00,
    ...ROOM_COMMANDS.room_param, 0x05, 0x00, 0x04, 0x00,
    ...ROOM_COMMANDS.room_param, 0x06, 0x00, 0x00, 0x00,
    ...ROOM_COMMANDS.room_param, 0x07, 0x00, 0xb8, 0x00,
    ...ROOM_COMMANDS.room_param, 0x08, 0x00, 0x01, 0x00,
    ...ROOM_COMMANDS.room_param, 0x09, 0x00, 0x00, 0x00,
    ...ROOM_COMMANDS.room_param, 0x0a, 0x00, 0x00, 0x00,
    ...ROOM_COMMANDS.room_param, 0x0b, 0x00, 0x00, 0x00,
    ...ROOM_COMMANDS.room_param, 0x0c, 0x00, 0x00, 0x00,
    ...ROOM_COMMANDS.room_param, 0x0d, 0x00, 0x00, 0x00,
    ...ROOM_COMMANDS.room_param, 0x0e, 0x00, 0x00, 0x00,
    ...ROOM_COMMANDS.room_param, 0x0f, 0x00, 0x00, 0x00
  ];
  
  const allBytes = [
    ...bytesMap,
    ...bytesInit,
    ...playerBytesArray.flat(),
    ...bytesParams
  ];

  sendCommandPacket(socket, ROOM_COMMANDS.room_map, Buffer.from(allBytes));
  log('Sent second binary init packet (length=' + allBytes.length + ')');
}

function sendMapPacket(socket, room) {
  // Use map data from room if provided, otherwise use defaults
  const mapType = room?.map?.type || 'D';
  const playerCount = room?.map?.playerCount || '8';
  const filename = room?.map?.filename || 'PLAY01.SCN';
  const displayName = room?.map?.displayName || 'Armageddon\n                                 (8 Player Desert Map )';

  const room_map = Buffer.from([
    ...Buffer.from(mapType, 'ascii'), // map type J=0x4a or D=0x44
    ...Buffer.from(playerCount, 'ascii'), // '8'=0x38 players count
    ...Buffer.from(filename + '\0', 'ascii'), // null-terminated
    ...Buffer.from(displayName, 'ascii'), // display name
  ]);

  sendCommandPacket(socket, ROOM_COMMANDS.room_map, Buffer.from(room_map));
  log('Sent map packet (length=' + room_map.length + ')');
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

  // Build payload from command and data
  const payload = Buffer.concat([commandBuf, dataBuf]);
  
  // Initialize per-socket counter if missing
  if (socket.__packetCounter === undefined) socket.__packetCounter = 0x00;
  
  const totalLen = 2 + payload.length + 1;
  if (totalLen > 0x0FFF) {
    log('sendCommandPacket: packet too long (max 4095 bytes): ' + totalLen);
    return;
  }

  const lenLow = totalLen & 0xFF;
  const lenHigh = (totalLen >> 8) & 0x0F;

  const counterByteValue = socket.__packetCounter;
  const counterNibble = (counterByteValue >> 4) & 0x0F;
  const counterByte = (counterNibble << 4) | lenHigh;

  // Advance per-socket counter
  socket.__packetCounter += 0x10;
  if (socket.__packetCounter > 0xF0) socket.__packetCounter = 0x00;

  const packet = Buffer.alloc(totalLen);
  let o = 0;
  packet[o++] = lenLow;
  packet[o++] = counterByte;
  payload.copy(packet, o); o += payload.length;
  packet[o++] = 0x00;

  const cmdHex = commandBuf.toString('hex');
  log(`sendCommandPacket [clientCounter=0x${counterNibble.toString(16)}] command=${cmdHex} len=${totalLen} (low=0x${lenLow.toString(16).padStart(2,'0')} high=0x${lenHigh.toString(16)}) payloadLen=${payload.length}`);
  safeWrite(socket, packet);
  // Purposefully no return value
}

// Broadcast a command packet to all clients in a room
function broadcastCommandPacket(room, command, data, excludeClientId = null) {
  for (const clientId of room.clients) {
    if (clientId === excludeClientId) continue;
    
    const client = clients.get(clientId);
    if (client && client.socket && !client.socket.destroyed) {
      sendCommandPacket(client.socket, command, data);
    }
  }
}

// NEW: helper to echo back player name changes
// Format: 0x67 [player_ordinal] [0x00] [name_string] [0x00]
function sendPlayerName(socket, playerOrdinal, name) {
  if (!name) name = '';
  // sanitize to ascii and limit length
  const clean = Buffer.from(name.replace(/[^\x20-\x7e]/g,'').slice(0,32),'ascii');
  const data = Buffer.concat([Buffer.from([playerOrdinal, 0x00]), clean, Buffer.from([0x00])]);
  sendCommandPacket(socket, ROOM_COMMANDS.player_name, data);
  log(`Echoed player_name: ordinal=${playerOrdinal} name="${name}"`);
}

// NEW: helper to echo chat message (format 0x65 <msg bytes> 0x00)
function sendPlayerChat(socket, msg) {
  if (!msg) msg = '';
  const clean = Buffer.from(msg.replace(/\r|\n/g,'').slice(0,120),'ascii');
  const data = Buffer.concat([clean, Buffer.from([0x00])]);
  sendCommandPacket(socket, ROOM_COMMANDS.player_chat, data);
  log('Echoed player_chat: ' + msg);
}

// Send next battle ping and set up timeout
function sendNextBattlePing(client) {
  if (!client || !client.battlePingState || client.battlePingState.waitingForEcho) {
    return;
  }

  const buf = Buffer.alloc(8);
  // First 32-bit counter (little-endian) - counts from 0
  buf.writeUInt32LE(client.battlePingState.counter, 0);
  // Second 32-bit counter (little-endian) - starts from initial packet counter
  buf.writeUInt32LE(client.battlePingState.initialPacketCounter + client.battlePingState.counter, 4);
  
  sendCommandPacket(client.socket, ROOM_COMMANDS.battle_ping1, buf);
  client.battlePingState.waitingForEcho = true;
  client.battlePingState.lastPingSentAt = Date.now();
  
  // Set timeout in case echo is never received
  client.battlePingState.timeoutId = setTimeout(() => {
    if (client.battlePingState && client.battlePingState.waitingForEcho) {
      log(`Client ${client.id}: Battle ping echo timeout, sending next ping anyway`);
      client.battlePingState.waitingForEcho = false;
      client.battlePingState.counter++;
      sendNextBattlePing(client);
    }
  }, BATTLE_PING_TIMEOUT_MS);
}

// Handle received battle ping echo
function handleBattlePingEcho(client) {
  if (!client || !client.battlePingState || !client.battlePingState.waitingForEcho) {
    return;
  }

  const echoDelay = Date.now() - client.battlePingState.lastPingSentAt;
  log(`Client ${client.id}: Battle ping echo received after ${echoDelay}ms`);
  
  // Clear timeout
  if (client.battlePingState.timeoutId) {
    clearTimeout(client.battlePingState.timeoutId);
    client.battlePingState.timeoutId = null;
  }

  client.battlePingState.waitingForEcho = false;
  client.battlePingState.counter++;
  
  // Wait the appropriate interval before sending next ping
  setTimeout(() => {
    if (client.battlePingState) {
      sendNextBattlePing(client);
    }
  }, BATTLE_PING_INTERVAL_MS);
}


// Binary parsing helpers per user instructions
const PLAYER_RACE = {
  humans: Buffer.from([0x01]),
  aliens: Buffer.from([0x00])
};
const PLAYER_TYPE = {
  ai_easy: Buffer.from([0x00]),
  ai_hard: Buffer.from([0x01]),
  gamer: Buffer.from([0x02]),
  none: Buffer.from([0x03]),
};
const PLAYER_READY = {
  ready_for_battle: Buffer.from([0x02]),
  not_ready: Buffer.from([0x01]),
  ready: Buffer.from([0x00]),
};
const PLAYER_INDEX = {
  p0: Buffer.from([0x00]),
  p1: Buffer.from([0x01]),
  p2: Buffer.from([0x02]),
  p3: Buffer.from([0x03]),
  p4: Buffer.from([0x04]),
  p5: Buffer.from([0x05]),
  p6: Buffer.from([0x06]),
  p7: Buffer.from([0x07]),
};
const TEAM_INDEX = {
  t0: Buffer.from([0x00]),
  t1: Buffer.from([0x01]),
  t2: Buffer.from([0x02]),
  t3: Buffer.from([0x03]),
  t4: Buffer.from([0x04]),
  t5: Buffer.from([0x05]),
  t6: Buffer.from([0x06]),
  t7: Buffer.from([0x07]),
};
const ROOM_PARAM = {
  erupting_vents: Buffer.from([0x02]),
  renewable_vents: Buffer.from([0x03]),
};
const PLAYER_INIT_PARAM = {
  player_index: Buffer.from([0x0f]),
};
const NULL_SEPARATOR = Buffer.from([0x00]);
const ROOM_COMMANDS = {
  initial_packet: Buffer.from([0x64]), // initial handshake packet
  begin_battle: Buffer.from([0x76]), // [0x76, 0x06, 0x00, 0x02]
  ping: Buffer.from([0x71]),
  player_ready: Buffer.from([0x68]), // plus player index byte in range 0x01..0x08 followed by readyness byte 0x00 or 0x01
  player_name: Buffer.from([0x67]),
  player_chat: Buffer.from([0x65]),
  player_race: Buffer.from([0x66]),  // value after command is humans=0x00, aliens=0x01 
  player_type: Buffer.from([0x6a]),  // value after command is ai_easy=0x00, ai_hard=0x01, gamer=0x02, none=0x03
  player_color: Buffer.from([0x6b]), // value after command is in range 0x01..0x07
  player_init: Buffer.from([0x6c]),  // value after command is [0x00, player index in range 0x00..0x07]
  player_team: Buffer.from([0x6d]),  // value after command is in range 0x01..0x07
  player_team2: Buffer.from([0x6e]), // value after command is in range 0x01..0x07
  room_param: Buffer.from([0x6f]),
  room_erupting_vents: Buffer.from([0x6f, 0x02, 0x00]), // erupting vents command following by 0x00 or 0x01 and two zero bytes
  room_renewable_vents: Buffer.from([0x6f, 0x03, 0x00]), // renewable vents command following by 0x00 or 0x01 and two zero bytes
  room_map: Buffer.from([0x69]), // two consecutive null terminated strings: map filename, map display name

  battle_ping1: Buffer.from([0x02]),
  battle_ping2: Buffer.from([0x08]),
  button_building: Buffer.from([0x09]),
  button_unit: Buffer.from([0x0a]),
  button_upgrade: Buffer.from([0x0c]),
  button_superweapon: Buffer.from([0x0d]), // napalm or virus attack
  battle_chat: Buffer.from([0x0e]), // header [0x06, 0xff] followed by null-terminated ascii string
  unit_select_data: Buffer.from([0x11]),
  unit_select: Buffer.from([0x12]),
  unit_destination_data: Buffer.from([0x14]),
  unit_destination: Buffer.from([0x15]),
  unit_attack: Buffer.from([0x18]),
  unit_move: Buffer.from([0x19]),
  unit_inspire: Buffer.from([0x1a]),
};

function parseClientBinary(client, buf) {
  if (!client || !Buffer.isBuffer(buf) || buf.length === 0) return;
  const id = client.id;
  let offset = 0;

  // STEP 1: Collect all commands from the packet into a list
  const commandList = [];
  
  while (offset < buf.length) {
    // Check if we have at least 2 bytes for length header
    if (offset + 2 > buf.length) {
      log(`Client ${id}: Not enough bytes for command header at offset ${offset}`);
      break;
    }

    // Read length from first 2 bytes (little endian with mask 0xff 0x0f)
    const lenLow = buf[offset];
    const lenHigh = buf[offset + 1] & 0x0f;
    const cmdLength = lenLow | (lenHigh << 8);
    
    log(`Client ${id}: Command at offset ${offset}, length=${cmdLength} (0x${lenLow.toString(16).padStart(2,'0')} 0x${buf[offset+1].toString(16).padStart(2,'0')})`);

    // Check if we have the full command available
    if (offset + cmdLength > buf.length) {
      log(`Client ${id}: Incomplete command at offset ${offset}, need ${cmdLength} bytes but only have ${buf.length - offset}`);
      break;
    }

    // Extract the command payload (skip the 2-byte length header)
    const commandData = buf.slice(offset + 2, offset + cmdLength);
    
    // Add to command list
    commandList.push(commandData);

    // Move to next command
    offset += cmdLength;
  }

  log(`Client ${id}: Collected ${commandList.length} command(s) from packet`);

  // STEP 2: Process commands from the list
  for (const commandData of commandList) {
    let matched = false;
    for (const [name, pattern] of Object.entries(ROOM_COMMANDS)) {
      if (commandData.length >= pattern.length && commandData.slice(0, pattern.length).equals(pattern)) {
        matched = true;
        let remaining = commandData.slice(pattern.length);
        
        // Strip trailing 0x00 from this command's data (each command has its own terminator)
        if (remaining.length > 0 && remaining[remaining.length - 1] === 0x00) {
          remaining = remaining.slice(0, -1);
        }
        
        if (name === 'player_name') {
          // Format: [player_ordinal_byte] [0x00] [name_string] [0x00]
          if (remaining.length >= 2) {
            const playerOrdinal = remaining[0];
            const nameStart = 2;
            let nameEnd = remaining.indexOf(0x00, nameStart);
            if (nameEnd === -1) nameEnd = remaining.length;
            const playerName = remaining.slice(nameStart, nameEnd).toString('ascii');
            log(`Binary command from Client ${id}: ${name} ordinal=${playerOrdinal} ${playerName ? '(' + playerName + ')' : ''}`);
            
            // Update the room's player slot data
            const room = rooms.get(client.roomId);
            if (room) {
              const slot = room.playerSlots[playerOrdinal];
              if (slot) {
                slot.name = playerName.replace(/[^\x20-\x7e]/g,'').slice(0,32) || `Player${playerOrdinal}`;
                log(`Updated slot ${playerOrdinal} name to "${slot.name}" in Room ${room.id}`);
              }
              
              // Broadcast to all clients in the room
              const data = Buffer.concat([Buffer.from([playerOrdinal, 0x00]), Buffer.from(slot.name,'ascii'), Buffer.from([0x00])]);
              broadcastCommandPacket(room, ROOM_COMMANDS.player_name, data);
            }
          }
        } else if (name === 'player_chat') {
          let end = remaining.indexOf(0x00);
          if (end === -1) end = remaining.length;
          const chatMsg = remaining.slice(0, end).toString('ascii');
          log(`Binary command from Client ${id}: ${name}${chatMsg ? ' ' + chatMsg : ''}`);
          
          // Broadcast to all clients in the room
          const room = rooms.get(client.roomId);
          if (room) {
            const clean = Buffer.from(chatMsg.replace(/\r|\n/g,'').slice(0,120),'ascii');
            const data = Buffer.concat([clean, Buffer.from([0x00])]);
            broadcastCommandPacket(room, ROOM_COMMANDS.player_chat, data);
          }
        } else if (name === 'player_ready') {
          // Echo back the player_ready command with the client's actual player slot index
          const playerIndex = PLAYER_INDEX[`p${client.playerSlotIndex}`];
          log(`Binary command from Client ${id}: ${name} -> broadcasting readiness for slot ${client.playerSlotIndex}`);
          
          // Update the room's player slot ready state
          const room = rooms.get(client.roomId);
          if (room) {
            const slot = room.playerSlots[client.playerSlotIndex];
            if (slot) {
              slot.ready = true;
              log(`Updated slot ${client.playerSlotIndex} ready state to true in Room ${room.id}`);
            }
            
            // Broadcast to all clients in the room
            broadcastCommandPacket(room, ROOM_COMMANDS.player_ready, Buffer.from([...PLAYER_READY.ready_for_battle, ...playerIndex]));
            
            // Check if all connected clients (gamers) are ready
            let allClientsReady = true;
            for (const clientId of room.clients) {
              const c = clients.get(clientId);
              if (c && c.playerSlotIndex !== undefined) {
                const s = room.playerSlots[c.playerSlotIndex];
                if (s && !s.ready) {
                  allClientsReady = false;
                  break;
                }
              }
            }
            
            // If all clients are ready, mark the first slot (AI) as ready and broadcast
            if (allClientsReady && room.clients.size > 0) {
              const aiSlot = room.playerSlots[0];
              if (aiSlot && !aiSlot.ready) {
                aiSlot.ready = true;
                log(`All clients ready in Room ${room.id}. Marking AI slot 0 as ready.`);
                
                // Broadcast AI ready status to all clients
                const aiPlayerIndex = PLAYER_INDEX.p0;
                broadcastCommandPacket(room, ROOM_COMMANDS.player_ready, Buffer.from([...PLAYER_READY.ready_for_battle, ...aiPlayerIndex]));
              }
            }
          }
        } else if (name === 'player_race') {
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          
          // Update the room's player slot race
          if (remaining.length >= 2) {
            const raceValue = remaining[0];
            const playerOrdinal = remaining[1];
            const room = rooms.get(client.roomId);
            if (room) {
              const slot = room.playerSlots[playerOrdinal];
              if (slot) {
                slot.race = raceValue === 0x01 ? 'humans' : 'aliens';
                log(`Updated slot ${playerOrdinal} race to "${slot.race}" in Room ${room.id}`);
              }
              
              // Broadcast to all clients in the room
              broadcastCommandPacket(room, ROOM_COMMANDS.player_race, remaining);
            }
          }
        } else if (name === 'player_color') {
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          
          // Update the room's player slot color
          if (remaining.length >= 2) {
            const colorValue = remaining[0];
            const playerOrdinal = remaining[1];
            const room = rooms.get(client.roomId);
            if (room) {
              const slot = room.playerSlots[playerOrdinal];
              if (slot) {
                slot.color = colorValue;
                log(`Updated slot ${playerOrdinal} color to ${slot.color} in Room ${room.id}`);
              }
              
              // Broadcast to all clients in the room
              broadcastCommandPacket(room, ROOM_COMMANDS.player_color, remaining);
            }
          }
        } else if (name === 'player_team') {
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          
          // Update the room's player slot team
          if (remaining.length >= 2) {
            const teamValue = remaining[0];
            const playerOrdinal = remaining[1];
            const room = rooms.get(client.roomId);
            if (room) {
              const slot = room.playerSlots[playerOrdinal];
              if (slot) {
                slot.team = teamValue;
                log(`Updated slot ${playerOrdinal} team to ${slot.team} in Room ${room.id}`);
              }
              
              // Broadcast to all clients in the room
              broadcastCommandPacket(room, ROOM_COMMANDS.player_team, remaining);
            }
          }
        } else if (name === 'room_greeting') {
          log(`Binary command from Client ${id}: ${name} -> room greeting`);
        } else if (name === 'begin_battle') {
          log(`Binary command from Client ${id}: ${name} -> player initiating battle`);
          
          // Mark this client as having initiated battle
          client.battleInitiated = true;
          
          // Check if all clients in the room have initiated battle
          const room = rooms.get(client.roomId);
          if (room && checkAllClientsInitiatedBattle(room)) {
            startRoomBattle(room);
            log(`All ${room.clients.size} clients in Room ${room.id} have initiated battle`);
          }
          
          // Initialize battle ping state
          if (client.battlePingState) {
            if (client.battlePingState.timeoutId) {
              clearTimeout(client.battlePingState.timeoutId);
            }
          }
          client.battlePingState = {
            counter: 0,
            initialPacketCounter: client.socket.__packetCounter || 0,
            waitingForEcho: false,
            timeoutId: null,
            lastPingSentAt: null
          };
          // Send the first ping
          sendNextBattlePing(client);
        } else if (name === 'battle_ping1') {
          log(`Binary command from Client ${id}: ${name} (echo received)`);
          handleBattlePingEcho(client);
        } else if (name === 'battle_ping2') {
          // battle_ping2 does not send echo by protocol definition, just log it
          log(`Binary command from Client ${id}: ${name} (no echo expected)`);
        } else if (name === 'unit_attack') {
          // Broadcast the full unit_attack command with all data bytes
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.unit_attack, remaining);
          }
        } else if (name === 'unit_move') {
          // Broadcast the unit_move command without an optional trailing 0x00 training byte
          let moveData = remaining;
          if (moveData.length > 0 && moveData[moveData.length - 1] === 0x00) {
            log(`Binary command from Client ${id}: ${name} (stripping trailing training 0x00)`);
            moveData = moveData.slice(0, -1);
          } else {
            log(`Binary command from Client ${id}: ${name} (broadcasting ${moveData.length} data bytes)`);
          }
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.unit_move, moveData);
          }
        } else if (name === 'unit_select_data') {
          // Broadcast the full unit_select_data command with all data bytes
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.unit_select_data, remaining);
          }
        } else if (name === 'unit_select') {
          // Broadcast the full unit_select command with all data bytes
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.unit_select, remaining);
          }
        } else if (name === 'unit_destination_data') {
          // Broadcast the full unit_destination_data command with all data bytes
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.unit_destination_data, remaining);
          }
        } else if (name === 'unit_destination') {
          // Broadcast the full unit_destination command with all data bytes
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.unit_destination, remaining);
          }
        } else if (name === 'button_unit') {
          // Broadcast the full button_unit command with all data bytes
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.button_unit, remaining);
          }
        } else if (name === 'button_building') {
          // Broadcast the full button_building command with all data bytes
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.button_building, remaining);
          }
        } else if (name === 'unit_inspire') {
          // Broadcast the full unit_inspire command with all data bytes
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.unit_inspire, remaining);
          }
        } else if (name === 'button_upgrade') {
          // Broadcast the full button_upgrade command with all data bytes
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.button_upgrade, remaining);
          }
        } else if (name === 'button_superweapon') {
          // Broadcast the full button_superweapon command with all data bytes
          log(`Binary command from Client ${id}: ${name} (broadcasting all ${remaining.length} data bytes)`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.button_superweapon, remaining);
          }
        } else if (name === 'battle_chat') {
          // Broadcast the battle chat message (skip first 2 bytes for logging - they are header)
          const messageStart = remaining.length >= 2 ? 2 : 0;
          const messageData = remaining.slice(messageStart);
          let end = messageData.indexOf(0x00);
          if (end === -1) end = messageData.length;
          const chatMsg = messageData.slice(0, end).toString('ascii');
          log(`Binary command from Client ${id}: ${name}${chatMsg ? ' "' + chatMsg + '"' : ' (empty)'}`);
          const room = rooms.get(client.roomId);
          if (room) {
            broadcastCommandPacket(room, ROOM_COMMANDS.battle_chat, remaining);
          }
        } else {
          log(`Binary command from Client ${id}: ${name}`);
        }
        break;
      }
    }
    
    if (!matched) {
      log(`Unknown binary command from Client ${id}: ${commandData.toString('hex')}`);
    }
  }
}

const server = net.createServer((socket) => {
  const id = nextClientId++;
  const remote = `${socket.remoteAddress}:${socket.remotePort}`;
  
  // Find or create an available room
  const room = getAvailableRoom();
  
  const clientObj = { 
    id, 
    socket, 
    buffer: '', 
    lastActivity: Date.now(),
    battlePingState: null,
    roomId: null,
    playerSlotIndex: null,
    battleInitiated: false
  };
  clients.set(id, clientObj);
  socket.__packetCounter = 0x00; // initialize per-client packet counter
  
  // Add client to the room and get assigned slot
  const slotIndex = addClientToRoom(id, room);
  
  if (slotIndex === null) {
    log(`Client ${id} could not be added to any room - no free slots`);
    socket.destroy();
    clients.delete(id);
    return;
  }
  
  log(`Client ${id} connected from ${remote}. Active: ${clients.size}. Assigned to Room ${room.id} slot ${slotIndex}`);

  socket.setKeepAlive(true, 30_000);
  socket.setNoDelay(true); // Disable Nagle's algorithm to send packets immediately without buffering

  // Check if socket is still open before sending initial packets (some automated tools disconnect immediately)
  if (!socket.destroyed && socket.writable) {
    sendRoomGreeting(socket, slotIndex);
    sendRoomData(socket, room);
    sendMapPacket(socket, room);
    sendCommandPacket(socket, ROOM_COMMANDS.player_chat, `Welcome to the world of Dark Colony!`);
    sendCommandPacket(socket, ROOM_COMMANDS.player_chat, `Room: ${room.id}`);
    sendCommandPacket(socket, ROOM_COMMANDS.player_chat, `Random slot assigned: ${slotIndex + 1}`);
  } else {
    log(`Client ${id} socket closed immediately after connection (automated scanner?)`);
    disconnect(id, 'socket closed before initialization');
  }

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
server.listen(PORT, HOST, () => { 
  log(`Game server listening on ${HOST}:${PORT} (unencrypted TCP)`);
  // Initialize the first room when server starts
  createRoom();
});

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
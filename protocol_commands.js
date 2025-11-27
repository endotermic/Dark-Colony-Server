/*!
 * (c) 2025 Nikolajs Agafonovs
 * Licensed under the AGPL-3.0-or-later license.
 * This server may be used only in open source projects.
 * Source code must remain publicly available under the same license.
 */

'use strict';

// Protocol constants for Dark Colony multiplayer game server

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
  battle_ping3_data: Buffer.from([0x11]),
  battle_ping3: Buffer.from([0x12]),
  game_speed: Buffer.from([0x13]), // 4 bytes, only first one is used, speed step 10%, values from 110%=0x3c to 200%=0x21
  unit_destination_data: Buffer.from([0x14]),
  unit_destination: Buffer.from([0x15]),
  unit_attack: Buffer.from([0x18]),
  unit_move: Buffer.from([0x19]),
  unit_inspire: Buffer.from([0x1a]),
};

module.exports = {
  PLAYER_RACE,
  PLAYER_TYPE,
  PLAYER_READY,
  PLAYER_INDEX,
  TEAM_INDEX,
  ROOM_PARAM,
  PLAYER_INIT_PARAM,
  NULL_SEPARATOR,
  ROOM_COMMANDS
};

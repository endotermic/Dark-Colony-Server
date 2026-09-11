// Memory model of the dc16.exe game state ("gs"), reproduced byte for byte.
//
// The server-side battle engine is a port of the simulation core of Classic dc16.exe
// (docs/DC16_BATTLE_ENGINE.md). To keep the port mechanical and the lockstep checksum
// (sync.c 0x44ABC0) identical, the game-state block is a plain Buffer with the ORIGINAL field
// offsets: an object is 220 bytes at gs+0x7D48+obj*220, a player block is 0xE34 bytes at
// gs+0xB98+p*0xE34, and so on. Every function of the port reads and writes through the helpers
// below, so an offset in the disassembly maps 1:1 to a line of JavaScript.
//
// Integer semantics: Buffer.readInt16LE/readInt32LE return the same values the CPU sees; writes
// wrap like the 16- or 32-bit stores they replace. Division in the original is `idiv` (truncation
// toward zero): use idiv() below, never `/` alone. `sar` is `>>`, `shr` is `>>>`.

export const GS_SIZE = 0x48000;

export const MAX_OBJECTS = 800;
export const OBJ_SIZE = 0xdc; // 220
export const OBJ_BASE = 0x7d48;
export const FIRST_UNIT = 0x98; // 152: objects below are city slots (0..119) and reserved (120..151)
export const BUILDINGS_PER_SIDE = 15;
export const MAX_MISSILES = 0x7e8; // 2024
export const MISSILE_SIZE = 0x28;
export const MISSILE_BASE = 0x32cc8;
export const PLAYER_BASE = 0xb98;
export const PLAYER_SIZE = 0xe34;

/** gs-relative offsets of the game-state header (docs/DC16_BATTLE_ENGINE.md §3.1). */
export const GS = Object.freeze({
  NET_FLAG: 0x000, // byte, non-zero disables commander specials (read as "multiplayer" flag)
  TIMING_INDEX: 0x308,
  UNIT_CAP: 0x528,
  GAME_TIME: 0x52c, // incremented inside game_tick
  DN_COUNTER: 0x530,
  DN_PHASE_LEN: 0x534,
  DN_DAWN_LEN: 0x538,
  DN_PHASE: 0x53c, // 0 day, 1 night
  DN_LIGHT: 0x540, // darkness 0..256
  TICK: 0x94c, // incremented by the pacing loop BEFORE game_tick; the sync checksum's time
  UNTIL: 0x954,
  DESIRED_MS: 0x96c,
  TICK_MS: 0x970,
  HISTORY: 0x994, // int16[256], checksum history indexed by tick % 256
  AI_STATE: 0xb94,
  LOCAL_PLAYER: 0x7d3c,
  MAX_OBJ: 0x7d40, // highest object index in use (inclusive)
  MISSILE_COUNT: 0x7d44,
  MISSILE_FREE: 0x46908, // int16 free-list head
  MISSILE_ACTIVE: 0x4690a, // int16 active-list head
  OBJ_ALLOC: 0x4690c, // int16[800], -1 = slot unused
  MAP_PTR: 0x46f4c,
  FLAGS: 0x46f50, // byte[4] cheat flags; [1] = paused
  ALLIANCE: 0x46f54, // byte[10][10]
  DIPLO_MATRIX0: 0x471c0, // pointer in the original; here see Game.diplo
  DIPLO_MATRIX1: 0x471c4,
  BYTE_948: 0x948, // byte, non-zero disables the deploy order of types 0x2F/0x30 (ticker.js)
  MAX_SPEED: 0x974, // int32[8] per-player max-speed reports (TICK_MAXSPEED, cleared by DISCONNECT)
});

/** Object record offsets (§3.2). */
export const O = Object.freeze({
  X: 0x00, // i16, tile = x >> 8
  HEIGHT: 0x02, // i16
  Z: 0x04, // i16
  TYPE: 0x06, // u8
  TEAM: 0x07, // u8: 0..7 players, 8 unowned, 9 wildlife
  HEADING: 0x09, // u8 0..255
  CHARGE: 0x0a, // u8 special charge
  HP: 0x0c, // i32
  ATTACKED: 0x10, // u8 (team << 5) | countdown
  SELECT: 0x12, // u8 selection mask
  ANIM0: 0x14, // 8-byte animation slot (body)
  ANIM0_STATUS: 0x1a,
  ANIM1: 0x1c, // blood overlay
  ANIM1_STATUS: 0x22,
  ANIM2: 0x24, // second overlay (heal beam, BUILD animation of a produced unit)
  ANIM2_STATUS: 0x2a,
  LIFE: 0x2c, // u8: 0 free, 10 corpse, else alive
  DEST_X: 0x2e, // i16
  DEST_Z: 0x30, // i16
  TARGET: 0x32, // i16 target object of the attack order (commands 0x0B/0x18, move mode 3); the docs said +0x30
  BURST: 0x34, // u8 shots fired in the current burst
  NUDGE: 0x35, // u8 nudge direction 0..7, 0xFF none
  PENDING: 0x36, // u8 pending order flag
  ORDER: 0x37, // u8 pending order code
  SP: 0x38, // u8 state-stack pointer, 0xFF empty
  STACK: 0x39, // [state u8, info offset u8] x 6 at +0x39+2k / +0x3A+2k
  INFO: 0x46, // int16[32] state info area
  PATH: 0x86, // 16 bytes = 32 direction nibbles
  WAYPOINTS: 0xa6, // i16 x, i16 z per waypoint, 8 waypoints
  WP_COUNT: 0xc6, // u8
  DAMAGED: 0xc7, // u8 -> blood overlay
  REDRAW1: 0xc8,
  REDRAW2: 0xc9,
  DETECTED: 0xca, // u8 detected-by-team mask (mines)
  PICKUP: 0xcb, // u8 pending build/production order kind
  BERSERK: 0xd0, // u8
  AI_NEXT: 0xd2, // i16
  AI_PREV: 0xd4, // i16
  LINK_COUNT: 0xd6, // u8 commander link countdown
  LINK_OBJ: 0xd8, // i16 linked commander
});

/** Player block offsets, relative to gs+0xB98+p*0xE34 (§3.1 lists them gs-relative for player 0). */
export const P = Object.freeze({
  NAME: 0x00, // char[17] player name (gs+0xB98)
  MONEY: 0x14, // gs+0xBAC
  SPENT: 0x18, // gs+0xBB0
  WORD_1C: 0x1c, // gs+0xBB4, tested by the hero-death event (combat.js)
  RACE: 0x20, // gs+0xBB8
  AI_TYPE: 0x24, // gs+0xBBC: 0 human, 1..4 AI personality
  FUNKY: 0x28, // gs+0xBC0: funky-tower flag (campaign trigger)
  KRUSTY: 0x2c, // gs+0xBC4
  CITY_X: 0x30, // gs+0xBC8, tiles
  CITY_Z: 0x34, // gs+0xBCC
  START_X: 0x38,
  START_Z: 0x3c,
  SLOT_HP: 0x40, // gs+0xBD8 int32[15]: building slot hit points, 0 = not built
  SLOT_BLOCKED: 0x7c, // gs+0xC14 BYTE[15]: slot under construction (city.js 0x4184C4 / state 0x13)
  SLOT_LEVEL: 0xc8, // gs+0xC60 int32[15]
  COLOUR: 0x104, // gs+0xC9C
  NET_ID: 0x108, // gs+0xCA0 lobby slot of the player
  PROD_READY: 0x10c, // gs+0xCA4 byte[4]
  PROD_COUNT: 0x110, // gs+0xCA8 byte[4]
  QUEUE_LEN: 0x114, // gs+0xCAC int16[4]
  QUEUE: 0x11c, // gs+0xCB4 byte[4][800]
  HEROES: 0xda0, // gs+0x1938 int16[4]
  DISABLED: 0xda8, // gs+0x1940 byte[110]
  CONSTRUCTING: 0xe16, // gs+0x19AE byte: a building of this player is under construction (city.js)
  DROPSHIP_USED: 0xe17, // gs+0x19AF byte[8]? drop-ship bookkeeping (combat.js)
  INCOME: 0xe20, // gs+0x19B8
  INCOME_MULT: 0xe24, // gs+0x19BC 0x100 / 0x200
  RANK: 0xe28, // gs+0x19C0 commander rank
  VISION: 0xe2c, // gs+0x19C4 vision mask
  WORD_1948: 0xe30, // gs+0x19C8 int16
});

/** Missile record offsets (§3.3). */
export const MS = Object.freeze({
  X: 0x00,
  Z: 0x02,
  HEIGHT: 0x04,
  VX: 0x06,
  VZ: 0x08,
  VY: 0x0a,
  WEAPON: 0x0c,
  SHOOTER: 0x0e,
  AGE: 0x10,
  DELAY: 0x12,
  NEXT: 0x14,
  SOUND: 0x16,
  FLIGHT: 0x18, // remaining flight ticks, -1 = direct fire
  FACING: 0x1a,
  STATE: 0x1c, // 0 waiting, 1 flying, 2 explosion, 3 burning, 4 finished
  KIND: 0x1e,
  RANDOM: 0x1f,
  ANIM: 0x20, // 8-byte animation slot
  ANIM_STATUS: 0x26,
});

export const objAddr = (obj) => OBJ_BASE + obj * OBJ_SIZE;
export const playerAddr = (p) => PLAYER_BASE + p * PLAYER_SIZE;
export const missileAddr = (m) => MISSILE_BASE + m * MISSILE_SIZE;

// ---- accessors (a = absolute gs offset) --------------------------------------------------------

export const u8 = (gs, a) => gs[a];
export const i8 = (gs, a) => gs.readInt8(a);
export const i16 = (gs, a) => gs.readInt16LE(a);
export const u16 = (gs, a) => gs.readUInt16LE(a);
export const i32 = (gs, a) => gs.readInt32LE(a);
export const w8 = (gs, a, v) => {
  gs[a] = v & 0xff;
};
export const w16 = (gs, a, v) => gs.writeInt16LE(((v & 0xffff) << 16) >> 16, a);
export const w32 = (gs, a, v) => gs.writeInt32LE(v | 0, a);

/** x86 idiv: quotient truncated toward zero. */
export const idiv = (a, b) => (a / b) | 0;
/** x86 idiv remainder (sign of the dividend). */
export const irem = (a, b) => a % b | 0;

/** Sign-extend a 16-bit value read as an unsigned number (movsx). */
export const sx16 = (v) => ((v & 0xffff) << 16) >> 16;

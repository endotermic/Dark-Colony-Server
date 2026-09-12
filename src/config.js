// Configuration from environment variables (plan §11). Types follow the defaults.

import { resolveMap } from './maps.js';

export const DEFAULTS = Object.freeze({
  PORT: 8888,
  HEALTH_PORT: 0, // 0 = no health listener
  // Rooms, one map each (plan §17): SCENARIO/MPLAYER file names, optionally FILE:Name[:terrain].
  // The 2nd character of a file name is the map's player count; it caps the room (F22). At most seven:
  // the rooms are shown in the lobby rows that are not the player's own (F33), numbered 1..7 in
  // place; the map line repeats the selected one (no room is preselected since 12 Sep 2026). Room 1 is a jungle map.
  ROOMS: 'J8PLAY01,D8PLAY01,D8PLAY02,D8PLAY03,D8PLAY05,J8PLAY02,J8PLAY07',
  HALL: true, // false = every connection goes straight into room 1 (the 2.0 behaviour)
  MARQUEE_MS: 200, // hall: one character of scrolling per step in the room rows
  PACK_LOBBY_FRAMES: true, // several commands per lobby frame (F34); false = one command per frame as in 2.0
  PROTOCOL_VERSION: 15,
  TICK_MS: 44, // 150 % (options screen: ms = 6600 / percent), the single-player default since 10 Sep 2026
  LOOKAHEAD: 8,
  MAX_LAG: 200,
  MIN_PLAYERS: 1, // real players needed before the countdown may start (maintainer, 7 Sep 2026: 1)
  START_COUNTDOWN_S: 3,
  MREADY_TIMEOUT_MS: 30000,
  IDLE_TIMEOUT_MS: 10000,
  JOIN_TIMEOUT_MS: 5000,
  KEEPALIVE_TIMEOUT_MS: 3000,
  ECHO_TIMEOUT_MS: 5000,
  LAG_DROP_MS: 10000, // 0 = never drop laggards (original behaviour)
  STRIKE_LIMIT: 10,
  STRICT_SEQ: true,
  MERCENARY_NAME: 'AI Mercenary', // the fake host's display name (was 'Mercenary' until 12 Sep 2026)
  MERCENARY_RACE: 0, // race of every fake player: 0 Human, 1 Gray
  FAKE_PLAYERS: 1, // fake human players including Mercenary (1..7); the rest of the slots are for real players
  FAKE_NAMES: 'AI Mercenary,Renegade,Outlaw,Nomad,Drifter,Vagabond,Marauder,Raider',
  FILL_EMPTY_WITH_AI: false,
  FILL_AI_TYPE: 0, // 0 easy, 1 hard
  ALLOW_PAUSE: true,
  SPEED_REFRESH_S: 30,
  STATS_INTERVAL_S: 30, // in-game stats log line; 0 = off
  DEBUG_MODE: false, // also implied by LOG_LEVEL=debug: applies the full-map-view cheat at game start
  LOG_LEVEL: 'info',
  // The server-side battle engine (plan §18): off = relay only; shadow = the engine runs beside the
  // relay, its checksums are logged/recorded and compared with 0x08 messages from clients; send =
  // shadow plus one 0x08 (checksum, tick) command in every sync frame. A mismatch aborts the CLIENT
  // ("sync error"), so `send` is for verified builds only.
  SYNC_CHECK: 'off',
  // Record every battle (sync frames, client checksums, engine checksums) as JSON lines into this
  // directory for offline replay with tools/replay.js; '' = off. Independent of SYNC_CHECK.
  RECORD_DIR: '',
  // Lobby slot of the fake host. 0 (default) makes it the lowest network id, so no client sends
  // 0x08 (F14). Diagnostic: a higher slot (e.g. 7) lets the lowest real player send checksums every
  // tick, which RECORD_DIR/SYNC_CHECK=shadow compare with the engine. Real players never get slot 0.
  MERCENARY_SLOT: 0,
});

export const SYNC_CHECK_MODES = ['off', 'shadow', 'send'];

const TRUE_WORDS = new Set(['1', 'true', 'yes', 'on']);

export const MAX_ROOMS = 7; // hall: one row per room, numbered 1..7 in place; the eighth row is the player's own (plan §17)

// Terrain descriptions as they appear in dc16.exe next to the title format string (0x483190).
export const TERRAIN_DESCRIPTIONS = Object.freeze({ desert: 'Desert Map ', jungle: 'Jungle Map ' });

/**
 * The scenario title exactly as the game's own host builds it: sprintf("%-43s (%d Player %s)",
 * name + "\n", players, description) (format string at 0x48319C). This is not cosmetic: every
 * lobby client reads the player count from title[45] (0x41141F) and, when that digit is smaller
 * than the number of occupied slots, clears the scenario and un-readies everybody.
 */
export function formatScenarioTitle(name, players, terrain = 'desert') {
  const key = String(terrain).toLowerCase();
  const desc = TERRAIN_DESCRIPTIONS[key] ?? `${key.charAt(0).toUpperCase()}${key.slice(1)} Map `;
  return `${`${name}\n`.padEnd(43)} (${players} Player ${desc})`;
}

export function loadConfig(env = process.env, overrides = {}) {
  const cfg = {};
  for (const [key, def] of Object.entries(DEFAULTS)) {
    const raw = env[key];
    let val = def;
    if (raw !== undefined && raw !== '') {
      if (typeof def === 'number') {
        val = Number(raw);
        if (!Number.isFinite(val)) throw new Error(`${key} must be a number, got "${raw}"`);
      } else if (typeof def === 'boolean') {
        val = TRUE_WORDS.has(String(raw).trim().toLowerCase());
      } else {
        val = String(raw);
      }
    }
    cfg[key] = val;
  }
  Object.assign(cfg, overrides);
  // rooms: { index (1-based), file, name, terrain, players, titleWire }
  cfg.ROOM_LIST = String(cfg.ROOMS)
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((spec, i) => {
      const m = resolveMap(spec);
      return { ...m, index: i + 1, titleWire: formatScenarioTitle(m.name, m.players, m.terrain) };
    });
  validate(cfg);
  cfg.DEBUG = cfg.DEBUG_MODE || cfg.LOG_LEVEL === 'debug';
  // names of the fake players; slot 0 is always MERCENARY_NAME
  const names = String(cfg.FAKE_NAMES).split(',').map((n) => n.trim()).filter(Boolean);
  cfg.FAKE_NAME_LIST = [cfg.MERCENARY_NAME];
  for (let i = 1; i < cfg.FAKE_PLAYERS; i++) {
    const n = names.find((x) => x !== cfg.MERCENARY_NAME && !cfg.FAKE_NAME_LIST.includes(x));
    cfg.FAKE_NAME_LIST.push((n ?? `${cfg.MERCENARY_NAME} ${i + 1}`).slice(0, 16));
  }
  return cfg;
}

function validate(cfg) {
  if (cfg.TICK_MS < 1) throw new Error('TICK_MS must be >= 1');
  if (cfg.ROOM_LIST.length < 1 || cfg.ROOM_LIST.length > MAX_ROOMS) throw new Error(`ROOMS must list 1..${MAX_ROOMS} maps`);
  if (cfg.MARQUEE_MS < 50) throw new Error('MARQUEE_MS must be >= 50');
  if (cfg.FAKE_PLAYERS < 1 || cfg.FAKE_PLAYERS > 7) throw new Error('FAKE_PLAYERS must be 1..7');
  for (const m of cfg.ROOM_LIST) {
    if (cfg.FAKE_PLAYERS >= m.players) {
      throw new Error(`FAKE_PLAYERS=${cfg.FAKE_PLAYERS} leaves no seat on ${m.file} (${m.players} players)`);
    }
  }
  if (cfg.MIN_PLAYERS < 1 || cfg.MIN_PLAYERS > 8 - cfg.FAKE_PLAYERS) {
    throw new Error(`MIN_PLAYERS must be 1..${8 - cfg.FAKE_PLAYERS} with ${cfg.FAKE_PLAYERS} fake players`);
  }
  if (cfg.MERCENARY_NAME.length < 1 || cfg.MERCENARY_NAME.length > 16) throw new Error('MERCENARY_NAME must be 1..16 chars');
  if (cfg.MERCENARY_RACE !== 0 && cfg.MERCENARY_RACE !== 1) throw new Error('MERCENARY_RACE must be 0 or 1');
  if (cfg.FILL_AI_TYPE !== 0 && cfg.FILL_AI_TYPE !== 1) throw new Error('FILL_AI_TYPE must be 0 or 1');
  if (cfg.LOOKAHEAD < 1 || cfg.MAX_LAG <= cfg.LOOKAHEAD) throw new Error('need 1 <= LOOKAHEAD < MAX_LAG');
  if (cfg.STRIKE_LIMIT < 1) throw new Error('STRIKE_LIMIT must be >= 1');
  cfg.SYNC_CHECK = String(cfg.SYNC_CHECK).trim().toLowerCase();
  if (!SYNC_CHECK_MODES.includes(cfg.SYNC_CHECK)) throw new Error(`SYNC_CHECK must be one of ${SYNC_CHECK_MODES.join(', ')}`);
  if (!Number.isInteger(cfg.MERCENARY_SLOT) || cfg.MERCENARY_SLOT < 0 || cfg.MERCENARY_SLOT > 7) throw new Error('MERCENARY_SLOT must be 0..7');
}

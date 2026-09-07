// Configuration from environment variables (plan §11). Types follow the defaults.

export const DEFAULTS = Object.freeze({
  PORT: 8888,
  HEALTH_PORT: 0, // 0 = no health listener
  MAP_FILE: 'D8PLAY01.SCN',
  MAP_TITLE: 'Armageddon', // map name; the wire title is built by formatScenarioTitle()
  MAP_TERRAIN: 'desert', // desert | jungle | ... (description text in the lobby list)
  PROTOCOL_VERSION: 15,
  TICK_MS: 33, // 200 % (options screen: ms = 6600 / percent)
  LOOKAHEAD: 8,
  MAX_LAG: 200,
  MIN_PLAYERS: 2,
  START_COUNTDOWN_S: 3,
  MREADY_TIMEOUT_MS: 30000,
  IDLE_TIMEOUT_MS: 10000,
  JOIN_TIMEOUT_MS: 5000,
  KEEPALIVE_TIMEOUT_MS: 3000,
  ECHO_TIMEOUT_MS: 5000,
  LAG_DROP_MS: 10000, // 0 = never drop laggards (original behaviour)
  STRIKE_LIMIT: 10,
  STRICT_SEQ: true,
  MERCENARY_NAME: 'Mercenary',
  MERCENARY_RACE: 0, // race of every fake player: 0 Human, 1 Gray
  FAKE_PLAYERS: 1, // fake human players including Mercenary (1..7); the rest of the slots are for real players
  FAKE_NAMES: 'Mercenary,Renegade,Outlaw,Nomad,Drifter,Vagabond,Marauder,Raider',
  FILL_EMPTY_WITH_AI: false,
  FILL_AI_TYPE: 0, // 0 easy, 1 hard
  ALLOW_PAUSE: true,
  SPEED_REFRESH_S: 30,
  STATS_INTERVAL_S: 30, // in-game stats log line; 0 = off
  DEBUG_MODE: false, // also implied by LOG_LEVEL=debug: applies the full-map-view cheat at game start
  LOG_LEVEL: 'info',
});

const TRUE_WORDS = new Set(['1', 'true', 'yes', 'on']);

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
  validate(cfg);
  cfg.MAP_PLAYERS = Number(cfg.MAP_FILE[1]);
  cfg.MAP_TITLE_WIRE = formatScenarioTitle(cfg.MAP_TITLE, cfg.MAP_PLAYERS, cfg.MAP_TERRAIN);
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
  if (cfg.MAP_FILE.length < 2 || !/[1-8]/.test(cfg.MAP_FILE[1])) {
    throw new Error(`MAP_FILE "${cfg.MAP_FILE}": the 2nd character must be the player count (1-8)`);
  }
  if (cfg.MAP_TITLE.length < 1 || cfg.MAP_TITLE.length > 42) throw new Error('MAP_TITLE must be 1..42 chars');
  if (cfg.FAKE_PLAYERS < 1 || cfg.FAKE_PLAYERS > 7) throw new Error('FAKE_PLAYERS must be 1..7');
  if (cfg.MIN_PLAYERS < 1 || cfg.MIN_PLAYERS > 8 - cfg.FAKE_PLAYERS) {
    throw new Error(`MIN_PLAYERS must be 1..${8 - cfg.FAKE_PLAYERS} with ${cfg.FAKE_PLAYERS} fake players`);
  }
  if (cfg.MERCENARY_NAME.length < 1 || cfg.MERCENARY_NAME.length > 16) throw new Error('MERCENARY_NAME must be 1..16 chars');
  if (cfg.MERCENARY_RACE !== 0 && cfg.MERCENARY_RACE !== 1) throw new Error('MERCENARY_RACE must be 0 or 1');
  if (cfg.FILL_AI_TYPE !== 0 && cfg.FILL_AI_TYPE !== 1) throw new Error('FILL_AI_TYPE must be 0 or 1');
  if (cfg.LOOKAHEAD < 1 || cfg.MAX_LAG <= cfg.LOOKAHEAD) throw new Error('need 1 <= LOOKAHEAD < MAX_LAG');
  if (cfg.STRIKE_LIMIT < 1) throw new Error('STRIKE_LIMIT must be >= 1');
}

// Entry point of the battle engine for the server: loads the constant data once and builds a
// `Game` for a room's map and lobby. Everything here is synchronous so that a room can start its
// engine inside beginRunning().

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Game } from './engine.js';
import { loadTables } from './tables.js';
import * as Anim from './anim.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
export const DATA_DIR = path.join(ROOT, 'data');
export const MAPS_DIR = path.join(ROOT, 'maps');

let cache = null;

/** The constant inputs of every game: exe tables, balance tables, sprite timing. Loaded once. */
export function loadEngineData(dataDir = DATA_DIR) {
  if (cache && cache.dir === dataDir) return cache;
  const consts = JSON.parse(fs.readFileSync(path.join(dataDir, 'dc16-tables.json'), 'utf8'));
  const gamestat = JSON.parse(fs.readFileSync(path.join(dataDir, 'classic', 'gamestat.json'), 'utf8'));
  const spritesPath = path.join(dataDir, 'classic', 'sprites.json');
  const sprites = fs.existsSync(spritesPath) ? JSON.parse(fs.readFileSync(spritesPath, 'utf8')) : null;
  cache = { dir: dataDir, consts, gamestat, sprites };
  return cache;
}

/** maps/<BASE>.json for a room map file such as "D8PLAY01.SCN", or null when not converted. */
export function loadMapJson(mapFile, mapsDir = MAPS_DIR) {
  const base = String(mapFile).replace(/\.scn$/i, '').toUpperCase();
  const file = path.join(mapsDir, `${base}.json`);
  if (!fs.existsSync(file)) return null;
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

/**
 * Build and start a game. `lobby` = { slots: [{ type, race, colour, team, name }] x 8, localSlot,
 * titleDigit } exactly as the clients saw the lobby when the start signal went out.
 */
export function createGame(mapJson, lobby, opts = {}) {
  const data = loadEngineData(opts.dataDir);
  const tables = loadTables(data.gamestat);
  const sprites = data.sprites ? Anim.loadSprites(data.sprites) : null;
  if (sprites) mergeSprites(tables, sprites);
  const G = new Game(tables, data.consts, { log: opts.log, assert: opts.assert });
  G.sprites = sprites;
  G.start(mapJson, lobby);
  return G;
}

/**
 * The sprite-derived fields of the balance tables (tables.js SPRITE_RULES: animation set ids and
 * the bounding box of every object type, bullet/explosion sets of every weapon, the sets of every
 * blast type) come from data/classic/sprites.json (tools/sprdata2json.js). Set id 0 = NULL.
 */
export function mergeSprites(tables, sprites) {
  for (let t = 0; t < tables.types.length; t++) {
    const rec = tables.types[t];
    const sp = sprites.types[t];
    if (!rec || !sp) continue;
    for (const k of ['move', 'stand', 'scrch', 'burn', 'fig', 'deploy', 'build', 'funk']) rec[k] = sp[k] ?? 0;
    rec.fire = sp.fire.slice();
    rec.numFire = sp.fire.length;
    rec.die = sp.die.slice();
    rec.numDie = sp.die.length;
    rec.blood = sp.blood.slice();
    rec.numBlood = sp.blood.length;
    rec.bbox = { minX: sp.bbox[0], minY: sp.bbox[1], maxX: sp.bbox[2], maxY: sp.bbox[3], hotX: sp.aim?.[0] ?? 0, hotY: sp.aim?.[1] ?? 0 };
  }
  for (let b = 0; b < tables.booms.length; b++) {
    const rec = tables.booms[b];
    const sp = sprites.booms[b];
    if (!rec || !sp) continue;
    rec.sprites = [0, 1, 2, 3].map((i) => sp.sets[i] ?? 0);
  }
  for (let w = 0; w < tables.weapons.length; w++) {
    const rec = tables.weapons[w];
    const sp = sprites.weapons[w];
    if (!rec || !sp) continue;
    rec.bullet = sp.bullet ?? 0;
    rec.explode = [0, 1, 2, 3].map((i) => sp.explode[i] ?? 0);
    rec.numExplode = sp.explode.length;
  }
}

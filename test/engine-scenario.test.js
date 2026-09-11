// Tests of src/engine/scenario.js and src/engine/grid.js (game start, map grids, vision).
//
// The engine modules are being ported in parallel; scenario.js and grid.js import ticker/renat/
// city/anim/ai/path by the names of PORTING.md. To run independently of those modules, this test
// copies mem.js, engine.js, grid.js and scenario.js into a temporary tree next to STUB versions of
// every other engine module (the stubs record their calls), and imports the copies.

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '..');
const consts = JSON.parse(fs.readFileSync(path.join(repo, 'data', 'dc16-tables.json'), 'utf8'));
const vision = JSON.parse(fs.readFileSync(path.join(repo, 'data', 'dc16-vision.json'), 'utf8'));
const armageddon = JSON.parse(fs.readFileSync(path.join(repo, 'maps', 'D8PLAY01.json'), 'utf8'));

const STUBS = {
  'ticker.js': `import { rec } from './_rec.js';
export function dispatchObject() {}
export function pushIdle(G, obj) { rec('pushIdle', obj); }
export function resetAndDispatchOrder(G, obj) { rec('resetAndDispatchOrder', obj); }`,
  'missile.js': 'export function missilesTick() {}',
  'renat.js': `import { rec } from './_rec.js';
export function resetResearch(G) { rec('resetResearch'); }
export function resetGenerators(G) { rec('resetGenerators'); }
export function loadTriggers(G, script) { rec('loadTriggers', script.length); }
export function registerGenerator(G, x, z, type, a) { rec('registerGenerator', x, z, type, a); }
export function registerResearchSite(G, x, z) { rec('registerResearchSite', x, z); }
export function addResearchItem() { return false; }
export function generatorCreatureTotal() { return 0; }
export function generatorsTick() {}
export function triggersTick() {}`,
  'commands.js': 'export function applyCommand() {}',
  'ai.js': `import { rec } from './_rec.js';
export function aiTurn() {}
export function aiInit(G) { rec('aiInit'); }
export function initPlayers(G) { rec('initPlayers'); }`,
  'path.js': `import { rec } from './_rec.js';
export function initPathGrid(G, mapJson) { rec('initPathGrid', mapJson.width, mapJson.height); G.map.path = { stub: true }; }`,
  'anim.js': `import { rec } from './_rec.js';
export function pose(G, type, name) { return 1000 + type + (name === 'STAND' ? 0 : 500); }
export function startAnim(G, addr, sprite, mode) { rec('startAnim', addr, sprite, mode); }`,
  'city.js': `import { rec } from './_rec.js';
export function buildSlot(G, p, slot) { rec('buildSlot', p, slot); }
export function removeFootprint(G, obj) { rec('removeFootprint', obj); }`,
  '_rec.js': `export const calls = [];
export function rec(...a) { calls.push(a); }
export function count(name) { return calls.filter((c) => c[0] === name).length; }`,
};

let tmp;
let Engine;
let Scenario;
let Grid;
let Rec;
let mem;

before(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dc16-engine-'));
  const eng = path.join(tmp, 'src', 'engine');
  fs.mkdirSync(eng, { recursive: true });
  fs.mkdirSync(path.join(tmp, 'data'));
  for (const f of ['mem.js', 'engine.js', 'grid.js', 'scenario.js']) {
    fs.copyFileSync(path.join(repo, 'src', 'engine', f), path.join(eng, f));
  }
  fs.copyFileSync(path.join(repo, 'data', 'dc16-vision.json'), path.join(tmp, 'data', 'dc16-vision.json'));
  for (const [f, src] of Object.entries(STUBS)) fs.writeFileSync(path.join(eng, f), src);
  const url = (f) => pathToFileURL(path.join(eng, f)).href;
  Engine = await import(url('engine.js'));
  Scenario = await import(url('scenario.js'));
  Grid = await import(url('grid.js'));
  Rec = await import(url('_rec.js'));
  mem = await import(url('mem.js'));
});

after(() => {
  if (tmp) fs.rmSync(tmp, { recursive: true, force: true });
});

// ---- fixtures -------------------------------------------------------------------------------------

/** A stand-in for tables.js: 130 object types with the fields scenario.js/grid.js read. */
function makeTables() {
  const types = [];
  for (let t = 0; t < 130; t++) {
    types.push({
      race: -1, counterpart: -1, hp: 100 + t, fly: 0, hidden: 0, commando: 0, stand: 'T' + t + 'STAND',
      colour: 0, visionNight: 2, visionDay: 3, weaponLevel: new Uint8Array(8), armourLevel: new Uint8Array(8),
      specialParam: 5, hasSpecial: 1, specialWeapon: 50,
    });
  }
  Object.assign(types[0], { race: 0, counterpart: 8, hp: 120, colour: 32 });
  Object.assign(types[8], { race: 1, counterpart: 0, hp: 120 });
  Object.assign(types[69], { race: 0, counterpart: 73, hp: 700, visionDay: 6, colour: 96 });
  Object.assign(types[73], { race: 1, counterpart: 69, hp: 700, visionDay: 6 });
  Object.assign(types[16], { race: 0, hp: 2500 }); // human HQ (unitdef[0][0])
  Object.assign(types[28], { race: 1, hp: 2500 });
  Object.assign(types[40], { hp: 1 });
  return { types, weapons: [], mbullet: null, booms: [], depend: [], unitid: null };
}

function newGame() {
  Rec.calls.length = 0;
  const asserts = [];
  const G = new Engine.Game(makeTables(), consts, { assert: (msg) => asserts.push(msg) });
  G.testAsserts = asserts;
  return G;
}

const emptySlot = () => ({ type: 3, race: 0, colour: 0, team: 0, name: '' });

function twoHumansLobby(teamA = 0, teamB = 3) {
  const slots = Array.from({ length: 8 }, emptySlot);
  slots[0] = { type: 2, race: 0, colour: 0, team: teamA, name: 'Alice' };
  slots[3] = { type: 2, race: 0, colour: 3, team: teamB, name: 'Bob' };
  return { slots, localSlot: 0, titleDigit: 8 };
}

function aliveObjects(G) {
  const out = [];
  for (let obj = mem.FIRST_UNIT; obj < mem.MAX_OBJECTS; obj++) {
    const a = mem.objAddr(obj);
    if (mem.u8(G.gs, a + mem.O.LIFE) !== 0) out.push(obj);
  }
  return out;
}

// ---- (a) the start shuffle against RELAY_SERVER_PLAN.md F12 ---------------------------------------

test('shuffle 0x4014F8: hand-computed case, humans in slots 0 and 3, 8-player map, seed 0', () => {
  const G = newGame();
  const lobby = twoHumansLobby();
  const r = Scenario.shuffleStart(G, lobby.slots, 8, 0);
  // rand table (seed 0 -> table[1], table[2], ...): 5758 10113 17515 31051 5627 23010 7419 16212
  // A = [0, 3, -, -, -, -, -, -]
  // k=0: 5758 % 8 = 6 -> swap A[0],A[6]   -> [-, 3, -, -, -, -, 0, -]
  // k=1: 10113 % 7 = 5 -> j = 6           -> [-, 0, -, -, -, -, 3, -]
  // k=2: 17515 % 6 = 1 -> j = 3 (both -1); k=3: 31051 % 5 = 1 -> j = 4; k=4: 5627 % 4 = 3 -> j = 7
  // k=5: 23010 % 3 = 0 -> j = 5; k=6: 7419 % 2 = 1 -> j = 7 -> [-, 0, -, -, -, -, -, 3]; k=7: j = 7
  assert.deepEqual(consts.rand.values.slice(1, 9), [5758, 10113, 17515, 31051, 5627, 23010, 7419, 16212]);
  assert.deepEqual([...r.order], [-1, 0, -1, -1, -1, -1, -1, 3]);
  assert.equal(r.slotToPlayer[0], 1);
  assert.equal(r.slotToPlayer[3], 7);
  assert.equal(G.randIndex, 8, 'exactly N = 8 rand() calls');
  assert.equal(r.records[1].netId, 0);
  assert.equal(r.records[1].name, 'Alice');
  assert.equal(r.records[7].netId, 3);
  assert.equal(r.records[7].team, 3);
  assert.equal(r.records[0].type, 3);
  assert.equal(r.records[0].netId, 8, 'empty records carry net id 8');
});

test('shuffle 0x4014F8: all eight slots occupied is a permutation and is deterministic', () => {
  const slots = Array.from({ length: 8 }, (_, k) => ({ type: k % 3 === 2 ? 2 : k % 2, race: 0, colour: k, team: k, name: `P${k}` }));
  const a = Scenario.shuffleStart(newGame(), slots, 8, 0);
  const b = Scenario.shuffleStart(newGame(), slots, 8, 0);
  assert.deepEqual([...a.order], [...b.order]);
  assert.deepEqual([...a.order].sort(), [0, 1, 2, 3, 4, 5, 6, 7]);
  for (let s = 0; s < 8; s++) assert.equal(a.order[a.slotToPlayer[s]], s);
  // fewer players than the title digit says: only the occupied slots move
  const c = Scenario.shuffleStart(newGame(), twoHumansLobby().slots, 2, 0);
  // k=0: 5758 % 2 = 0 -> j = 0; k=1: 10113 % 1 = 0 -> j = 1  => unchanged
  assert.deepEqual([...c.order], [0, 3, -1, -1, -1, -1, -1, -1]);
});

// ---- (b) a game on Armageddon with two humans ------------------------------------------------------

test('startGame on D8PLAY01 with humans in lobby slots 0 and 3', () => {
  const G = newGame();
  G.start(armageddon, twoHumansLobby());
  const gs = G.gs;
  const { O, P, GS } = mem;
  assert.deepEqual(G.testAsserts, [], 'no engine assert fired');

  // players: slot 0 -> game player 1, slot 3 -> game player 7 (see the shuffle test)
  assert.equal(G.scenario.localPlayer, 1);
  assert.equal(mem.i32(gs, GS.LOCAL_PLAYER), 1);
  assert.deepEqual([...G.scenario.occupied], [0, 1, 0, 0, 0, 0, 0, 1]);
  assert.equal(mem.i32(gs, G.player(1) + P.NET_ID), 0);
  assert.equal(mem.i32(gs, G.player(7) + P.NET_ID), 3);
  assert.equal(mem.i32(gs, G.player(0) + P.NET_ID), 8);
  assert.equal(gs.toString('latin1', G.player(1), G.player(1) + 5), 'Alice');
  assert.equal(mem.i32(gs, G.player(7) + P.COLOUR), 3);
  assert.equal(mem.i32(gs, G.player(1) + P.AI_TYPE), 0);
  for (let p = 0; p < 8; p++) assert.equal(mem.i32(gs, G.player(p) + P.MONEY), 1500, 'money is the file\'s for every team');
  assert.equal(G.stat(1, 1), 1500, 'stat 1 (money earned) starts at the initial money');

  // difficulty factors: stats[p*12 + 0], P7 multipliers 4 << 6
  assert.equal(G.stat(0, 1), 256);
  assert.equal(G.stat(0, 2), 256);
  assert.equal(G.stat(0, 3), 1, 'VAR 2 (erupting vents) default 1');
  assert.equal(G.stat(0, 5), 0);
  assert.equal(G.stat(4, 7), 3, 'stat 4 = colour');

  // day/night header
  assert.equal(mem.i32(gs, GS.DN_PHASE), 0);
  assert.equal(mem.i32(gs, GS.DN_PHASE_LEN), 5400);
  assert.equal(mem.i32(gs, GS.DN_COUNTER), 225);
  assert.equal(mem.i32(gs, GS.DN_DAWN_LEN), 225);
  assert.equal(mem.i32(gs, GS.TICK), 0);
  assert.equal(mem.i32(gs, GS.UNTIL), -1);
  assert.equal(mem.i32(gs, GS.TICK_MS), 0x42);
  assert.equal(gs[0], 0, 'desert is not underground');

  // objects: 8 troopers + 18 vents + 8 commanders consume 34 indices (skipped units of unoccupied
  // slots keep their index), 21 generators none; alive = 2 x 2 units + 18 vents
  assert.equal(mem.i32(gs, GS.MAX_OBJ), 152 + 34);
  const alive = aliveObjects(G);
  assert.equal(alive.length, 22);
  assert.equal(Rec.count('registerGenerator'), 21);
  assert.equal(Rec.count('buildSlot'), 120);
  assert.equal(Rec.count('initPathGrid'), 1);
  assert.deepEqual(Rec.calls.find((c) => c[0] === 'loadTriggers'), ['loadTriggers', 46]);
  assert.equal(Rec.count('resetAndDispatchOrder'), 22);
  assert.equal(Rec.count('startAnim'), 66);
  assert.equal(Rec.count('initPlayers'), 1);
  assert.equal(Rec.count('aiInit'), 1);
  assert.equal(Rec.count('resetResearch'), 1);
  assert.equal(Rec.count('resetGenerators'), 1);
  assert.equal(mem.u8(gs, mem.objAddr(152) + O.LIFE), 0, 'team 0 trooper skipped (slot unoccupied) but index consumed');
  assert.equal(mem.i16(gs, GS.OBJ_ALLOC + 152 * 2), -1, 'skipped object not allocated (the game-state init 0x40C354 sets the table to -1)');

  // commanders on their start positions, in the hero tables
  const byTeamType = (team, type) => alive.find((o) => mem.u8(gs, mem.objAddr(o) + O.TEAM) === team && mem.u8(gs, mem.objAddr(o) + O.TYPE) === type);
  const cmd1 = byTeamType(1, 69);
  const cmd7 = byTeamType(7, 69);
  assert.ok(cmd1 !== undefined && cmd7 !== undefined);
  assert.equal(mem.i16(gs, mem.objAddr(cmd1) + O.X), (67 << 8) + 0x80);
  assert.equal(mem.i16(gs, mem.objAddr(cmd1) + O.Z), (112 << 8) + 0x80);
  assert.equal(mem.i16(gs, mem.objAddr(cmd7) + O.X), (25 << 8) + 0x80);
  assert.equal(mem.i16(gs, mem.objAddr(cmd7) + O.Z), (62 << 8) + 0x80);
  assert.equal(mem.i32(gs, G.player(1) + P.START_X), 67);
  assert.equal(mem.i32(gs, G.player(1) + P.START_Z), 112);
  assert.equal(mem.i16(gs, G.player(1) + P.HEROES), cmd1);
  assert.equal(mem.i16(gs, G.player(1) + P.HEROES + 2), -1);
  assert.equal(mem.i32(gs, mem.objAddr(cmd1) + O.HP), 700, 'hit points from the type table');
  assert.equal(mem.u8(gs, mem.objAddr(cmd1) + O.HEADING), 96);
  assert.equal(mem.u8(gs, mem.objAddr(cmd1) + O.CHARGE), 0x40);
  assert.equal(mem.i16(gs, mem.objAddr(cmd1) + O.AI_NEXT), -2);
  assert.equal(Grid.groundId(G, 67, 112), cmd1, 'placed in the ground layer');
  assert.equal(mem.i16(gs, GS.OBJ_ALLOC + cmd1 * 2), cmd1);
  const trooper1 = byTeamType(1, 0);
  assert.equal(mem.i16(gs, mem.objAddr(trooper1) + O.X), (60 << 8) + 0x80);

  // vents: team 8, hp = money * 256 / 256, rate word, vent bit through the file-order row table
  const vents = alive.filter((o) => mem.u8(gs, mem.objAddr(o) + O.TYPE) === 40);
  assert.equal(vents.length, 18);
  // positions are 1/256 tile in 16 bits: tile 137 overflows int16, the original zero-extends (u16 >> 8)
  const ventAt = (x, z) => vents.find((o) => Grid.objTile(G, o)[0] === x && Grid.objTile(G, o)[1] === z);
  const v = ventAt(28, 124);
  assert.ok(v !== undefined);
  assert.equal(mem.u8(gs, mem.objAddr(v) + O.TEAM), 8);
  assert.equal(mem.i32(gs, mem.objAddr(v) + O.HP), 25000);
  assert.equal(mem.i16(gs, mem.objAddr(v) + 0x32), 25);
  const dormant = ventAt(81, 137);
  assert.equal(mem.i32(gs, mem.objAddr(dormant) + O.HP), 15000);
  assert.equal(mem.i16(gs, mem.objAddr(dormant) + 0x32), 0);
  assert.equal(Grid.groundId(G, 28, 124), 0x3ff, 'team 8 objects are in no layer');
  assert.ok(Grid.ventBitTest(G, 28, 124));
  assert.ok(!Grid.ventBitTest(G, 28, 123));
  assert.ok(Grid.cellLoad(G, 28, 140 - 1 - 124) & 0x04000000, 'the bit physically sits in the mirrored row');

  // city pairs "1 -1": HQ slot pre-built with the HP of unitdef[0][0] = type 16; tower slot HP 1
  assert.equal(mem.i32(gs, G.player(1) + P.SLOT_HP), 2500);
  assert.equal(mem.i32(gs, G.player(1) + P.SLOT_LEVEL), 0);
  assert.equal(mem.i32(gs, G.player(1) + P.SLOT_HP + 4), 0);
  assert.equal(mem.i32(gs, G.player(1) + P.SLOT_HP + 20), 1);
  assert.equal(mem.i32(gs, G.player(0) + P.SLOT_HP), 0, 'unoccupied team: no buildings');
  assert.equal(mem.i32(gs, G.player(0) + P.SLOT_HP + 20), 0);
  assert.equal(G.scenario.pairCounter, 10);
  assert.equal(mem.i32(gs, G.player(1) + P.VISION), 0x40000000 >> 1);
  assert.equal(mem.i32(gs, G.player(1) + P.INCOME), 3);
  assert.equal(mem.u8(gs, G.player(1) + P.PROD_READY), 1);

  // alliances: different lobby teams -> not allied; matrix 0 self bits set by the loader
  assert.equal(G.diploGet(0, 1, 7), 0);
  assert.equal(G.diploGet(0, 1, 1), 1);
  assert.equal(mem.u8(gs, GS.ALLIANCE + 1 * 10 + 9), 1);
  assert.equal(mem.u8(gs, GS.ALLIANCE + 90 + 7), 1);

  // rand(): the shuffle's 8 calls are discarded by the loader's srand(seed); no wildlife with VAR 0 = 0
  assert.equal(G.randIndex, 0);
  // unit cap: (648 - 18 vents - 100) / 2 cities = 265, capped at 150
  assert.equal(Scenario.unitCap(G), 150);
  assert.equal(G.historyAt(0), G.checksum(), 'checksum recorded for tick 0');
});

test('startGame is deterministic and honours lobby teams and VAR 0 (wildlife)', () => {
  const run = (lobby) => {
    const G = newGame();
    G.start(armageddon, lobby);
    return G;
  };
  const a = run(twoHumansLobby());
  const b = run(twoHumansLobby());
  assert.equal(a.checksum(), b.checksum());
  assert.ok(a.gs.equals(b.gs), 'identical game state');
  // equal lobby teams -> allied (matrix 0), humans do not share vision (matrix 1)
  const c = run(twoHumansLobby(5, 5));
  assert.equal(c.diploGet(0, 1, 7), 1);
  assert.equal(c.diploGet(1, 1, 7), 0);
  // VAR 0 (storage cells) = 2 -> 2 random creatures per occupied slot, in rand() order
  const lobby = twoHumansLobby();
  lobby.vars = [2, 0, 1, 0, 4, 4, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1];
  const d = run(lobby);
  assert.deepEqual(d.testAsserts, []);
  const wild = aliveObjects(d).filter((o) => mem.u8(d.gs, mem.objAddr(o) + mem.O.TEAM) === 9);
  assert.equal(wild.length, 4);
  for (const o of wild) {
    const t = mem.u8(d.gs, mem.objAddr(o) + mem.O.TYPE);
    assert.ok(t === 0x55 || t === 0x5a);
    assert.equal(mem.u8(d.gs, mem.objAddr(o) + mem.O.PICKUP), 2);
    const [x, z] = Grid.objTile(d, o);
    assert.notEqual(d.map.families[z * d.map.w + x], 0, 'wildlife stands on a passable cell');
    assert.equal(Grid.groundId(d, x, z), o);
  }
  assert.ok(d.randIndex > 0, 'wildlife consumed rand() after the srand(0) of the loader');
  assert.notEqual(d.checksum(), a.checksum());
  // VAR 1 (artifacts) = 0 and slots occupied -> the 0 type-37 sites of Armageddon: nothing to check
  // beyond the count; a VAR 7 seed changes the shuffle
  const e = twoHumansLobby();
  e.vars = [0, 0, 1, 0, 4, 4, 0, 5, 1, 1, 1, 1, 1, 1, 1, 1];
  const f = run(e);
  assert.equal(f.randIndex, 5, 'srand(var 7) in the loader after the shuffle');
  assert.notEqual(f.scenario.slotToPlayer[0], a.scenario.slotToPlayer[0]);
});

// ---- (c) grid.js: load packing, layers, vision -----------------------------------------------------

function tinyMap(w, h, attrFn, bgFn = () => 7, fgFn = () => 0) {
  const grid = (fn) => Array.from({ length: h }, (_, z) => Array.from({ length: w }, (_, x) => fn(x, z)));
  return {
    width: w, height: h, terrainFile: 'desert.bts', players: 2, header: { startPhase: 0, phaseLength: 5400, phaseCounter: 0, dawnLength: 225 },
    tiles: { background: grid(bgFn), foreground: grid(fgFn), attributes: grid(attrFn) },
    triggerIds: grid((x, z) => (x === 1 && z === 2 ? 5 : 0)),
    path: { families: grid((x, z) => (attrFn(x, z) & 0x200 ? 0 : 1)), maxFamily: 1, next: Buffer.alloc(65536).toString('base64') },
    script: [], objects: [], teams: [],
  };
}

test('grid.loadMap packs the load word like mapit.c and tallies terrain classes', () => {
  const G = newGame();
  // 4x3: cell (2,1) blocking with class 3, (0,0) has a foreground tile with level 2, class 1 elsewhere
  const attr = (x, z) => (x === 2 && z === 1 ? 0x200 | (3 << 10) : (1 << 10) | (x === 0 && z === 0 ? 2 : 0));
  const bg = (x, z) => (x === 2 && z === 1 ? 20 : 7);
  const fg = (x, z) => (x === 0 && z === 0 ? 100 : 0);
  const m = Grid.loadMap(G, tinyMap(4, 3, attr, bg, fg));
  assert.deepEqual(G.testAsserts, []);
  assert.equal(m.w, 4);
  // dense remap: 0 -> 0, 7 -> 1, 20 -> 2, 100 -> 3
  assert.equal(Grid.backgroundSlot(G, 1, 1), 1);
  assert.equal(Grid.backgroundSlot(G, 2, 1), 2);
  assert.equal(Grid.foregroundSlot(G, 0, 0), 3);
  assert.equal(Grid.foregroundSlot(G, 1, 0), 0);
  // attribute bits 22..31; walkable bit 29 forced when bit 9 is clear; levels cleared without foreground
  assert.ok(Grid.isBlocking(G, 2, 1));
  assert.ok(!Grid.isWalkable(G, 2, 1));
  assert.ok(Grid.isWalkable(G, 1, 1));
  // only attribute bits 0..9 fit in load bits 22..31; the class (bits 10..15) lives in the tally only
  assert.equal(Grid.attrBits(G, 1, 1), 0x80);
  assert.equal(Grid.attrBits(G, 0, 0), 0x80 | 2, 'foreground level kept');
  assert.equal(Grid.attrBits(G, 2, 1), 0x200, 'blocking cell: bit 9, no forced bit 7');
  const lvl = (x, z) => (Grid.cellLoad(G, x, z) >>> 22) & 0xf;
  assert.equal(lvl(0, 0), 2);
  assert.equal(lvl(1, 0), 0);
  // terrain classes: slot 1 (tile 7) class 1, slot 2 (tile 20) class 3, slot 3 (fg 100) class 1
  assert.equal(m.terrainClass[1], 1);
  assert.equal(m.terrainClass[2], 3);
  assert.equal(m.terrainClass[3], 1);
  assert.equal(Grid.terrainClassAt(G, 0, 0), 1);
  assert.equal(Grid.terrainClassAt(G, 2, 1), 3);
  // triggers: air word = id << 10; layers after clearLayers
  Grid.clearLayers(G);
  assert.equal(Grid.triggerId(G, 1, 2), 5);
  assert.equal(Grid.cellAir(G, 1, 2), (5 << 10) | 0x3ff);
  assert.equal(Grid.cellGround(G, 1, 2), 0x3ff);
  assert.equal(Grid.cellSec(G, 3, 2), 0x3ff);
  assert.equal(Rec.count('initPathGrid'), 1);
  // placement / removal helpers
  Grid.placeGround(G, 1, 1, 200);
  assert.equal(Grid.groundId(G, 1, 1), 200);
  G.map.ground[1 * 4 + 1] |= 0x40000000;
  Grid.placeGround(G, 1, 1, 201);
  assert.equal(Grid.cellGround(G, 1, 1), 0x40000000 | 201, 'high bits survive a placement');
  Grid.placeAir(G, 1, 2, 202);
  assert.equal(Grid.cellAir(G, 1, 2), (5 << 10) | 202);
  // removeFromGrid searches the 3x3 around the object's tile
  const a = mem.objAddr(201);
  mem.w16(G.gs, a + mem.O.X, (2 << 8) + 0x80);
  mem.w16(G.gs, a + mem.O.Z, (2 << 8) + 0x80);
  Grid.removeFromGrid(G, 201);
  assert.equal(Grid.groundId(G, 1, 1), 0x3ff);
  assert.equal(Grid.cellGround(G, 1, 1), 0x40000000 | 0x3ff);
  Grid.removeFromGrid(G, 5);
  assert.deepEqual(Rec.calls.at(-1), ['removeFootprint', 5]);
});

test('vision 0x44A718: ray-tree disc, terrain shadows, detectors and clearSeen', () => {
  const G = newGame();
  const W = 21;
  const H = 21;
  Grid.loadMap(G, tinyMap(W, H, (x, z) => (x === 10 && z === 9 ? 0x200 : 0)));
  Grid.clearLayers(G);
  const { O, GS } = mem;
  mem.w32(G.gs, GS.LOCAL_PLAYER, 0);
  mem.w32(G.gs, GS.DN_LIGHT, 0); // day: radius = ObsD
  // object 200: team 2, type 0 (obsD 3 in the fixture) at (10,10); make its radius 2 for the check
  G.tables.types[0].visionDay = 2;
  const obj = 200;
  const a = mem.objAddr(obj);
  mem.w8(G.gs, a + O.LIFE, 1);
  mem.w8(G.gs, a + O.TEAM, 2);
  mem.w8(G.gs, a + O.TYPE, 0);
  mem.w16(G.gs, a + O.X, (10 << 8) + 0x80);
  mem.w16(G.gs, a + O.Z, (10 << 8) + 0x80);
  mem.w32(G.gs, GS.MAX_OBJ, obj + 1);
  Grid.clearSeen(G);
  Grid.updateVision(G);
  const bit = 0x40000000 >> 2;
  const seen = [];
  for (let z = 0; z < H; z++) for (let x = 0; x < W; x++) if (G.map.ground[z * W + x] & bit) seen.push(`${x - 10},${z - 10}`);
  const tree = vision.trees[2];
  assert.equal(tree.length, 13, 'radius 2 tree has 13 cells');
  // (0,-2) hangs below (0,-1) in the tree: the blocked cell (10,9) is seen, the cell behind it is not
  const idxOf = (dx, dz) => tree.findIndex((n) => n[0] === dx && n[1] === dz);
  const parentOf = (i) => tree.findIndex((n) => n[2].includes(i));
  assert.equal(parentOf(idxOf(0, -2)), idxOf(0, -1));
  assert.ok(seen.includes('0,-1'));
  assert.ok(!seen.includes('0,-2'));
  assert.equal(seen.length, 12);
  assert.ok(seen.includes('0,0') && seen.includes('2,0') && seen.includes('-1,1'));
  assert.ok(!(G.map.ground[10 * W + 10] & (0x80000000 | 0)), 'local player 0 does not see team 2: no explored bit');
  // clearSeen wipes bits 23..30 only
  G.map.ground[10 * W + 10] |= 0x80000000;
  Grid.clearSeen(G);
  assert.equal(G.map.ground[10 * W + 10], (0x80000000 | 0x3ff) | 0);
  assert.equal(G.map.ground[11 * W + 10], 0x3ff);
  // a flyer sees through the blocking cell
  G.tables.types[0].fly = 1;
  Grid.updateVision(G);
  assert.ok(G.map.ground[8 * W + 10] & bit);
  G.tables.types[0].fly = 0;
  // a detector marks a mine in the secondary layer of a visited cell
  G.tables.types[0].commando = 1;
  const mine = 300;
  mem.w8(G.gs, mem.objAddr(mine) + O.LIFE, 1);
  Grid.placeSec(G, 11, 10, mine);
  Grid.clearSeen(G);
  Grid.updateVision(G);
  assert.equal(mem.u8(G.gs, mem.objAddr(mine) + O.DETECTED), 1 << 2);
  // an object under attack reveals its own cell to the attacker
  Grid.clearSeen(G);
  mem.w8(G.gs, a + O.ATTACKED, (5 << 5) | 3);
  Grid.updateVision(G);
  assert.ok(G.map.ground[10 * W + 10] & (0x40000000 >> 5));
  // the reveal cheat
  Grid.clearSeen(G);
  assert.ok(!(G.map.ground[0] & bit));
  mem.w8(G.gs, GS.FLAGS, 1);
  Grid.clearSeen(G);
  assert.equal(G.map.ground[0] & 0xff800000, (0xff800000 | 0));
});

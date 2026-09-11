// scenario.js — game start: run_game 0x40122C (game type 2, from the end of the lobby to the first
// tick), the start shuffle 0x4014F8, the per-player setup 0x401668, the scenario loader 0x41BAF0
// (from the map JSON instead of the .SCN text), initObject 0x41B124, createObject 0x41B930/0x41B818,
// the nearest-free-cell search 0x41B68C and the unit cap 0x41E7FC.
//
// PORT NOTES (11 Sep 2026, Classic dc16.exe VAs)
//
// * The lobby "VAR" array is `ss+0xA670[16]` = the globals 0x4A4680.. (ss = 0x49A010): var 0
//   Storage Cells -> 0x4A4680, 1 Artifacts -> 84, 2 Erupting -> 88, 3 Renewable -> 8C, 4 P7 quantity
//   -> 90, 5 P7 flow -> 94, 6 Commander rank -> 98, 7 (unused) -> 9C = the RNG seed of the shuffle
//   and of the loader. run_game resets 0..6 at 0x4012A4 (4, 5 to 4) BEFORE the lobby runs, so the
//   values that count are the ones the lobby ends with (the server's 'o' messages): `lobby.vars`.
//   4 and 5 are shifted << 6 at 0x4014C2 and become the difficulty factors stats[p*12 + 0] for
//   p = 1..6 (0x401827..0x40188E: setStat(k=0, p=1..6)). NOTE the getter/setter argument order is
//   (k, p): `0x41A790(eax=k, edx=p)`; the assignment text's "G.stat(1, 0)" is stats[0*12+1], which
//   is a different cell — the port uses G.stat(0, 1) / G.stat(0, 2) etc. as the code does.
// * Lobby slot record (72 bytes at ss+8 = 0x49A018 + slot*0x48, written by the lobby init 0x410DBE
//   and the 'g'/'f'/'j'/'k'/'l'/'m'/'n' handlers): +0x00 i32 slot index ("net id", 8 in the empty
//   records the shuffle creates), +0x04 char[17] name (sprintf "Player %d" default), +0x18 i32 = 0
//   (ready status?), +0x1C i32 race, +0x20 i32 type (0 AI easy, 1 AI hard, 2 human, 3 empty),
//   +0x24 i32 colour, +0x28 i32 team, +0x2C..+0x47 not read by run_game.
// * Shuffle 0x4014F8..0x401653: srand(var 7); N = title[45] - '0' (the lobby's scenario title,
//   = map player count, F15); A[8] = non-empty slots ascending padded with -1; for k in 0..N-1:
//   j = k + rand() % (N-k), swap A[k], A[j]; B[A[k]] = k for A[k] != -1; the 8 records are permuted
//   so that record[B[slot]] = old record[slot], missing ones become {netId 8, type 3}; game player p
//   = position in A; player[p].NET_ID (+0x108) = record[p].netId; local player = B[local slot].
//   Returned as G.scenario.slotToPlayer / playerToSlot.
// * Per player 0x401668..0x401757: occupied flag (campaign+0x1578+p) 0/1, RANK = var 6 for all 8,
//   name (17 bytes strncpy, "AIPLAYER" for AI), RACE, AI_TYPE (human 0, AI 3 = krusty for both easy
//   and hard, empty 4 unreachable), INCOME_MULT 0x200 hard / 0x100 easy — overwritten at 0x40176A
//   for ALL players by (campaign+0x1518[p] << 8) / 100 (idiv). No code writes campaign+0x1518 by
//   literal offset and its allocation was not traced; it is `lobby.incomePercent[8]` here, default 0
//   (the value a zeroed block gives). Used only by 0x413D0D (AI cost scaling). TODO(exact).
// * 0x41AC80: KRUSTY = 0 for all 8, AI state block {tick, 0} -> gs+0xB94 (ai.js owns the state; the
//   later 0x41ACD8 writes gs+0xB94 = 0 anyway). 0x41A040 zeroes both statistics tables.
// * Loader 0x41BAF0 order of operations, each reproduced: srand(seed); 0x440640 (artifact-site count
//   = 0); 0x421630 (global gs pointer); the two diplomacy matrices allocated zeroed; object byte +0x08
//   = slot index (obj % 15 below 120, else 8); 0x43C44C RELOADS ALL BALANCE TABLES (tables.js — the
//   Game must start with fresh tables, TODO(integrator)); timing arrays; TICK/UNTIL/TICK_MS/gs+4 =
//   150 (unit-cap ceiling)/max speeds 33; gs+0 = 0 then = 1 for "atlantis.bts" (this byte is
//   `gs->underground`, NOT a network flag as mem.js/§3.1 say) which also zeroes +0x108/+0x10C/+0x110
//   of types 69..76 (the commanders' targeted special); 0x436080 map load (grid.js); 0x43FD1C triggers
//   (renat.js); 0x44D670 mission texts (display); 0x445740 layers; header numbers; TEAM blocks;
//   120 x build_slot (city.js); MAX_OBJ = 0x98; 0x4400F0 (generator count = 0); object lines; random
//   wildlife; 0x44AC68 record(0); 0x437D00 dep_recompute (local player's build menu, display).
// * TEAM block facts that differ from DC16_MAP_FILES.md §6.1: (1) the five pairs are
//   (count, hp): pair k with first > 0 pre-builds slot k: SLOT_HP[k] = second, or the hit points of
//   unitdef[first-1][k] (0x444E30(k, level) -> 0x48B0C4, race not considered) when second == -1;
//   SLOT_LEVEL[k] = first - 1 — every shipped map's "1 -1" is the pre-built HQ, not "unused"; the
//   JSON names them {type, count} in the wrong order (type = count, count = hp). (2) Allies: only
//   ally(i, j) one way plus ally(i, i) both ways and matrix 1 (vision) (i, i) — the ally line does
//   NOT set (j, i). (3) The building rows index object_types by the value of 0x41AFA0[race*8+j]
//   directly (weaponLevel[i] = byte raw[2], armourLevel[i] = byte raw[3]). (4) +0x7C..+0x8A are 15
//   BYTES cleared and +0x8C..+0xC7 15 int32 = -1 (mem.js has SLOT_BLOCKED int32[15] at +0x7C).
//   (5) The tower slot: SLOT_HP[5] = 1, SLOT_LEVEL[5] = 0 when the team has a city (occupied in
//   multiplayer), else 0/0. (6) alliance[c][9] = alliance[9][c] = 1 for c = 0..7 (0x41C1CB).
// * Object lines 0x41C603..0x41C878: a skipped unit of an UNOCCUPIED slot still consumes its object
//   index (0x41C827 -> inc edi); an artifact-tile skip (0x4406F8) and a skipped type 37 do not; a
//   generator line (player -1) consumes nothing. MAX_OBJ becomes "one past the last index" (the
//   engine's loops use `<=` and so visit one free slot; reproduced). Vents: hp = a * stat(0,2) / 256,
//   rate word (+0x32) = player-column * stat(0,1) / 256 (idiv, the sbb/sar idiom), team 8, vent bit
//   set through the file-order row table (grid.ventBitSet). Type 37 sites are created when
//   stat(0,6) > 0 or the pair counter is 0.
// * Random wildlife 0x41C87D..0x41C994: n = pairCounter / 5 (= occupied slots in multiplayer); for
//   each, stat(0,5) creatures: rand() odd -> type 85 else 90; then rand() % w, rand() % h until the
//   path family byte of the cell is non-zero (G.map.families); 0x41B68C nearest free ground cell;
//   initObject(.., team 9, hp -1, pickup 2). rand() calls in this exact order.
// * initObject 0x41B124 argument order: (gs, x, z, type, team, hp, pickup, obj); the heading, the
//   default hit points, the flyer height and the STAND animations come from the type ARGUMENT, the
//   layer choice (+0x68 hidden -> secondary, +0x60 flying -> air) from the rank-adjusted TYPE byte;
//   team 8 objects are placed in no layer; heroes (types 69..76) are re-typed by the owner's RANK
//   and appended to HEROES (assert < 4). +0xE0 of object_types is the initial heading (§2.2 calls it
//   "not traced; probably palette").
// * unitCap 0x41E7FC: 648 - generatorCreatureTotal (0x4400C0) - (objects of teams 0..8 without a built city slot 0..4;
//   team 8 = vents/sites always counts, team 9 never) - 100, divided by the number of players with a
//   city (idiv), capped by gs+4 (150).
// * Cross-module calls (module table of PORTING.md, TODO(owner) where the name is new):
//   Grid.loadMap/clearLayers/ventBitSet/place*/removeFromGrid; Path.initPathGrid (via Grid.loadMap);
//   Renat.resetResearch (0x440640), Renat.loadTriggers (0x43FD1C), Renat.resetGenerators (0x4400F0),
//   Renat.registerGenerator (0x43FFB0), Renat.registerResearchSite (0x440650), Renat.addResearchItem
//   (0x4406F8, with the type in ebx: the object becomes a research item), Renat.generatorCreatureTotal
//   (0x4400C0 - the assignment called it "artifactCount"; it sums the generator counts); City.buildSlot
//   (0x4450F4); Ticker.resetAndDispatchOrder (0x41233C); Anim.pose(G, type, 'STAND') + Anim.startAnim
//   (0x42626C); Ai.aiInit (0x41AC80), Ai.initPlayers (0x41ACD8, TODO(owner ai.js)).
// * object_types fields used (tables.js names, original offsets): race +0x04, visionNight +0x10,
//   visionDay +0x14 (grid.js), weaponLevel[8] +0x30, armourLevel[8] +0x38, hp +0x44, fly +0x60,
//   hidden +0x68, commando +0x6C, stand +0x80 (through Anim.pose), colour +0xE0 (= the initial
//   HEADING, 0x41B50A; §2.2 "not traced; probably palette", tables.js `colour`), specialParam +0x108,
//   hasSpecial +0x10C, specialWeapon +0x110, counterpart +0x114.

import {
  GS, O, P, u8, i32, w8, w16, w32, objAddr, playerAddr, idiv, irem, MAX_OBJECTS, FIRST_UNIT, BUILDINGS_PER_SIDE,
} from './mem.js';
import * as Grid from './grid.js';
import * as Renat from './renat.js';
import * as City from './city.js';
import * as Ticker from './ticker.js';
import * as Anim from './anim.js';
import * as Ai from './ai.js';

// Offsets not (yet) in mem.js — original offsets in the comments; to be moved there by the integrator.
const OX = Object.freeze({
  SLOT: 0x08, // u8 city slot index of a building object (obj % 15), 8 for units (0x41BB51/0x41BB8F)
  BYTE_11: 0x11, // u8, cleared by initObject
  BYTE_13: 0x13, // u8, cleared by initObject
  VENT_RATE: 0x32, // i16 vent rate (0x41C780)
  BYTE_CC: 0xcc, // u8, cleared by initObject
  TILE_X: 0xcd, // u8 tile x at creation
  TILE_Z: 0xce, // u8 tile z at creation
  BYTE_CF: 0xcf, // u8, cleared by initObject
  BYTE_D1: 0xd1, // u8 = 0xFF at creation
});
const PX = Object.freeze({
  NAME: 0x00, // char[17] (gs+0xB98)
  DWORD_1C: 0x1c, // gs+0xBB4, zeroed by the loader
  FUNKY: 0x28, // gs+0xBC0, campaign funky-tower flag, zeroed by the loader
  SLOT_BYTES: 0x7c, // gs+0xC14 byte[15], zeroed (0x41C3D6)
  SLOT_DWORDS_8C: 0x8c, // gs+0xC24 int32[15] = -1 (0x41C3CA)
  HERO_COUNT: 0xd9c, // gs+0x1934 (num_commanders)
  NO_CITY: 0xdb6, // gs+0x194E byte, set when the city origin is (0, 0) (0x41C276)
  BYTE_E16: 0xe16, // gs+0x19AE
  BYTES_E17: 0xe17, // gs+0x19AF + i (0x41C4B5)
});
const GX = Object.freeze({
  UNDERGROUND: 0x000, // byte: 1 for atlantis.bts (0x41BD07); mem.js calls this NET_FLAG
  UNIT_CAP_MAX: 0x004, // 150 (0x41BC06), the ceiling of unitCap
  TIMING_A: 0x008, // int32[64] zeroed (0x41BBBD: gs+4+edx*4, edx = 1..64)
  TIMING_T: 0x108, // int32[64] timer samples (0x41BBD5), not deterministic
  TIMING_B: 0x208, // int32[64] = 0x42 (0x41BBCA)
  BYTE_30C: 0x30c, // = ss byte 0 (0x40175F)
  DWORD_310: 0x310,
  DWORD_314: 0x314, // = -1
  BYTE_318: 0x318,
  SCENARIO_PTR: 0x544,
  TERRAIN_NAME: 0x548, // char[0x400]
  DWORD_95C: 0x95c,
  DWORD_960: 0x960,
  DWORD_964: 0x964, // = 1
  DWORD_968: 0x968,
  MAX_SPEED: 0x974, // int32[8] = 0x21 (0x41BC10, gs+0x970+eax*4, eax = 1..8)
  BYTE_471C8: 0x471c8,
  BYTE_471C9: 0x471c9,
});

/** The VAR values the relay server sends to every lobby client (src/constants.js VAR_DEFAULTS). */
export const DEFAULT_VARS = Object.freeze([0, 0, 1, 0, 4, 4, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1]);
export const SLOT_EMPTY = 3;
export const SLOT_HUMAN = 2;
const AI_NAME = 'AIPLAYER'; // 0x4820B8

const typeRec = (G, t) => {
  const r = G.tables.types[t];
  if (!r) throw new Error(`object type ${t} missing from G.tables.types`);
  return r;
};

// ---- run_game 0x40122C (0x4014B3 .. 0x401B65 for game type 2) ------------------------------------

/**
 * @param lobby { slots: [{ type, race, colour, team, name }] x 8, localSlot, titleDigit?, vars?,
 *               incomePercent?, ssByte0? }
 */
export function startGame(G, mapJson, lobby) {
  const gs = G.gs;
  const vars = Int32Array.from({ length: 16 }, (_, k) => (lobby.vars ?? DEFAULT_VARS)[k] | 0);
  const titleDigit = lobby.titleDigit ?? mapJson.players ?? 0;
  const incomePercent = Int32Array.from({ length: 8 }, (_, p) => (lobby.incomePercent ?? [])[p] | 0);
  // 0x401349: player[p].RANK = campaign+0x1538[p] for all p — overwritten below (0x401714) for all 8.
  // 0x401381: campaign (type 0/3) copies campaign+0x14E4 into player 0's race — not in multiplayer.
  // 0x4014C2: the P7 multipliers become 8.8 fixed point
  vars[4] <<= 6;
  vars[5] <<= 6;
  // 0x4014E8: campaign+0x1581 (load game) == 0 -> shuffle
  const shuffle = shuffleStart(G, lobby.slots, titleDigit, vars[7]);
  const records = shuffle.records;
  const localPlayer = shuffle.slotToPlayer[lobby.localSlot] ?? -1; // 0x4015C3
  for (let p = 0; p < 8; p++) w32(gs, playerAddr(p) + P.NET_ID, records[p].netId); // 0x40164B
  w32(gs, GS.LOCAL_PLAYER, localPlayer); // 0x40165D

  const occupied = new Uint8Array(8);
  // 0x4016F2..0x401757
  for (let p = 0; p < 8; p++) {
    const pa = playerAddr(p);
    occupied[p] = 0; // campaign+0x1578+p
    w32(gs, pa + P.RANK, vars[6]); // 0x4A4698
    const rec = records[p];
    if (rec.type === SLOT_EMPTY) continue;
    occupied[p] = 1;
    const name = rec.type === SLOT_HUMAN ? rec.name : AI_NAME;
    writeName(gs, pa + PX.NAME, name); // 0x406948(player, src, 0x11)
    w32(gs, pa + P.RACE, rec.race);
    if (rec.type === 3) w32(gs, pa + P.AI_TYPE, 4);
    else if (rec.type === 2) w32(gs, pa + P.AI_TYPE, 0);
    else {
      w32(gs, pa + P.AI_TYPE, 3);
      w32(gs, pa + P.INCOME_MULT, rec.type === 1 ? 0x200 : 0x100);
    }
    w32(gs, pa + P.COLOUR, rec.colour);
  }
  w8(gs, GX.BYTE_30C, lobby.ssByte0 ?? 0); // 0x40175F: gs+0x30C = ss byte 0. TODO(exact): meaning of ss+0
  // 0x401765: 0x41A040 — both statistics tables zeroed
  G.stats.fill(0);
  G.typeStats.fill(0);
  // 0x40176A..0x4017A4: income multiplier from campaign+0x1518[p] (see PORT NOTES)
  for (let p = 0; p < 8; p++) w32(gs, playerAddr(p) + P.INCOME_MULT, idiv(incomePercent[p] << 8, 100));
  // 0x4017A9: 0x41AC80 — krusty pointers cleared, AI state block allocated (Ai.aiInit)
  for (let p = 0; p < 8; p++) w32(gs, playerAddr(p) + P.KRUSTY, 0);
  w32(gs, GS.AI_STATE, 0);
  Ai.aiInit(G);
  // 0x4017B1: campaign+0x1581 != 0 would load a saved game (0x40E194) — not in multiplayer
  // 0x401827..0x40188E: difficulty factors into stats[p*12 + 0], p = 1..6
  G.setStat(0, 1, vars[4]);
  G.setStat(0, 2, vars[5]);
  G.setStat(0, 3, vars[2]);
  G.setStat(0, 4, vars[3]);
  G.setStat(0, 5, vars[0]);
  G.setStat(0, 6, vars[1]);

  G.scenario = {
    gameType: 2,
    occupied,
    vars,
    records,
    slotToPlayer: shuffle.slotToPlayer,
    playerToSlot: shuffle.order,
    localSlot: lobby.localSlot,
    localPlayer,
    titleDigit,
    incomePercent,
    pairCounter: 0,
  };

  // 0x40189F: 0x41BAF0(gs, campaign, seed = var 7)
  loadScenario(G, mapJson, vars[7]);

  // 0x4018BB..0x40195B: alliances from equal lobby teams (post-shuffle records, occupied only)
  for (let a = 0; a < 8; a++) {
    if (!occupied[a]) continue;
    for (let b = 0; b < 8; b++) {
      if (!occupied[b]) continue;
      if (records[a].team !== records[b].team) continue;
      G.diploSet(0, a, b, 1); // 0x41E928(matrix0, a, b, 1)
      if (records[a].type !== SLOT_HUMAN) G.diploSet(1, a, b, 1); // AI players share vision
    }
  }
  // 0x401964: 0x41EA40 network init — not simulation
  // 0x401A78..0x401A8E: stat 4 of every player = colour
  for (let p = 0; p < 8; p++) G.setStat(4, p, i32(gs, playerAddr(p) + P.COLOUR));
  // 0x401AAA..0x401B3C: player names copied into campaign+0x1994 (display / results screen)
  // 0x401B4C: 0x41F0B4 network; 0x401B54: 0x41ACD8 AI personalities initialised
  // 0x41ACD8: per AI player 0x489488[aiType-1]->init(gs, p), then gs+0xB94 = 0. TODO(owner ai.js): not exported yet
  if (typeof Ai.initPlayers === 'function') Ai.initPlayers(G);
  else G.log?.warn?.('engine: Ai.initPlayers (0x41ACD8) missing, AI personalities not initialised');
  // 0x401B65: campaign+0x14F8 = local player (G.scenario.localPlayer)
  return G.scenario;
}

/** strncpy(dst, src, 17) (0x406948): copy up to 17 bytes, zero-fill the rest. */
function writeName(gs, addr, name) {
  const bytes = Buffer.from(String(name ?? ''), 'latin1');
  for (let k = 0; k < 17; k++) gs[addr + k] = k < bytes.length ? bytes[k] : 0;
}

// ---- start shuffle 0x4014F8 .. 0x401653 ----------------------------------------------------------

/**
 * @returns {{ order: Int32Array(8), slotToPlayer: Int32Array(8), records: object[8] }}
 *   order[p] = lobby slot of game player p (-1 none); slotToPlayer[slot] = p (-1 when empty).
 */
export function shuffleStart(G, slots, titleDigit, seed) {
  G.srand(seed); // 0x4014FD: srand(0x4A469C)
  const n = titleDigit | 0; // 0x401504: title[45] - '0'
  const A = new Int32Array(8).fill(-1); // [ebp-12h]
  const B = new Int32Array(8).fill(-1); // [ebp+2Eh]
  let count = 0;
  for (let slot = 0; slot < 8; slot++) if (slots[slot].type !== SLOT_EMPTY) A[count++] = slot; // 0x40153C
  for (let k = 0; k < n; k++) {
    // 0x40155D: j = k + rand() % (n - k)
    const r = G.rand();
    const j = k + irem(r, n - k);
    const t = A[j];
    A[j] = A[k];
    A[k] = t;
  }
  const records = [];
  for (let p = 0; p < 8; p++) {
    if (A[p] !== -1) B[A[p]] = p; // 0x40159B
    records.push({ netId: 8, name: '', race: 0, type: SLOT_EMPTY, colour: 0, team: 0 }); // 0x4015B2/0x4015BA
  }
  // 0x4015DA..0x401611: copy every non-empty slot record to its game position
  for (let slot = 0; slot < 8; slot++) {
    const s = slots[slot];
    if (s.type === SLOT_EMPTY) continue;
    records[B[slot]] = {
      netId: slot, // record +0 = slot index (lobby init 0x410DF1)
      name: s.name ?? '',
      race: s.race | 0,
      type: s.type | 0,
      colour: s.colour | 0,
      team: s.team | 0,
    };
  }
  return { order: A, slotToPlayer: B, records };
}

// ---- scenario loader 0x41BAF0(gs, campaign, seed) -------------------------------------------------

export function loadScenario(G, mapJson, seed) {
  const gs = G.gs;
  const sc = G.scenario;
  const multiplayer = sc.gameType !== 0 && sc.gameType !== 3; // [campaign+0x14F0] tests at 0x41BF79 etc.
  G.srand(seed); // 0x41BB03
  Renat.resetResearch(G); // 0x440640: research (artifact) site count [0x4897D0] = 0
  // 0x41BB11: 0x421630(gs, 0) stores the gs pointer in the global 0x48950C — nothing to do
  G.diplo[0].fill(0); // 0x41BB20..0x41BB33: two zeroed 8-byte matrices at gs+0x471C0/+0x471C4
  G.diplo[1].fill(0);
  for (let obj = 0; obj < MAX_OBJECTS; obj++) {
    // 0x41BB35..0x41BB96: byte +0x08 = slot index for the 120 city objects, 8 otherwise
    w8(gs, objAddr(obj) + OX.SLOT, obj < 8 * BUILDINGS_PER_SIDE ? irem(obj, BUILDINGS_PER_SIDE) : 8);
  }
  // 0x41BB9E: 0x43C44C(pool) reloads WEAPSTAT/GAMESTAT/MBULLET/BOOMSTAT/DEPEND — G.tables must be fresh
  // 0x41BBA5: [0x489498] = gs
  w32(gs, GS.TIMING_INDEX, 0);
  for (let k = 0; k < 64; k++) {
    w32(gs, GX.TIMING_A + 4 * k, 0);
    w32(gs, GX.TIMING_B + 4 * k, 0x42);
    w32(gs, GX.TIMING_T + 4 * k, 0); // original: 0x40B430() timer, not deterministic
  }
  w32(gs, GS.TICK, 0);
  w32(gs, GS.UNTIL, -1);
  w32(gs, GX.DWORD_95C, 0);
  w32(gs, GS.TICK_MS, 0x42);
  w32(gs, GX.UNIT_CAP_MAX, 0x96);
  for (let k = 0; k < 8; k++) w32(gs, GX.MAX_SPEED + 4 * k, 0x21);
  w32(gs, GX.DWORD_960, 0);
  w32(gs, GX.DWORD_964, 1);
  w32(gs, GX.DWORD_968, 0);
  w32(gs, GX.DWORD_310, 0);
  w32(gs, GX.SCENARIO_PTR, 1); // pointer to the campaign object (G.scenario)
  w32(gs, GX.DWORD_314, -1);
  w8(gs, GX.BYTE_318, 0);
  w8(gs, GX.UNDERGROUND, 0); // 0x41BC84
  // 0x41BC87..0x41BCDA: path join, fopen, first line = terrain file into gs+0x548 (1024 bytes)
  const terrain = String(mapJson.terrainFile ?? '');
  gs.fill(0, GX.TERRAIN_NAME, GX.TERRAIN_NAME + 0x400);
  gs.write(terrain.slice(0, 0x3ff), GX.TERRAIN_NAME, 'latin1');
  if (terrain === 'atlantis.bts') {
    // 0x41BD07: gs->underground; types 69..76 lose their targeted special
    w8(gs, GX.UNDERGROUND, 1);
    for (let t = 0x45; t < 0x4d; t++) {
      const r = typeRec(G, t);
      r.specialParam = 0; // +0x108
      r.hasSpecial = 0; // +0x10C (byte)
      r.specialWeapon = 0; // +0x110
    }
  }
  // lines 2 and 3 (base name, display name): file names only
  Grid.loadMap(G, mapJson); // 0x41BDAF: 0x436080(pool, dir, base) -> gs+0x46F4C (+ path init)
  Renat.loadTriggers(G, mapJson.script ?? []); // 0x41BDC6: 0x43FD1C(pool, base)
  // 0x41BDE0: 0x44D670 mission texts — display only
  Grid.clearLayers(G); // 0x41BDEF: 0x445740(gs)
  // line 4 read and dropped (0x41BDFA)
  const hd = mapJson.header;
  w32(gs, GS.DN_PHASE, hd.startPhase | 0); // line 5
  w32(gs, GS.DN_PHASE_LEN, hd.phaseLength | 0); // line 6
  w32(gs, GS.DN_COUNTER, hd.phaseCounter | 0); // line 7
  w32(gs, GS.GAME_TIME, 0);
  w32(gs, GS.DN_DAWN_LEN, hd.dawnLength | 0); // line 8
  w8(gs, GX.BYTE_471C8, 0);
  w8(gs, GX.BYTE_471C9, 0);
  w32(gs, GS.DN_LIGHT, (hd.startPhase | 0) << 8);
  gs.fill(0, GS.ALLIANCE, GS.ALLIANCE + 100); // 0x41BEB8..0x41BEE1

  sc.pairCounter = 0; // [ebp-20h]
  for (let i = 0; i < 8; i++) loadTeam(G, mapJson.teams[i], i, multiplayer);

  // 0x41C5BA..0x41C5F1: every city slot object initialised
  for (let p = 0; p < 8; p++) for (let slot = 0; slot < BUILDINGS_PER_SIDE; slot++) City.buildSlot(G, p, slot); // 0x4450F4(gs, p, slot)
  w32(gs, GS.MAX_OBJ, FIRST_UNIT); // 0x41C5F8
  Renat.resetGenerators(G); // 0x4400F0: generator count [0x4897CC] = 0

  let next = FIRST_UNIT; // edi
  const bumpMax = () => {
    if (next > i32(gs, GS.MAX_OBJ)) w32(gs, GS.MAX_OBJ, next); // 0x41C866..0x41C872
  };
  for (const line of mapJson.objects) {
    w8(gs, objAddr(next) + O.LIFE, 0); // 0x41C641
    const x = line.x | 0;
    const z = line.z | 0;
    let type = line.type | 0;
    const player = line.player | 0;
    const a = line.a | 0;
    const b = line.b | 0; // 6th number, 0 when absent (0x41C66C)
    // 0x41C67A..0x41C6B7: race counterpart
    if (player >= 0 && player < 8) {
      const tr = typeRec(G, type);
      if (i32(gs, playerAddr(player) + P.RACE) !== tr.race && tr.counterpart !== -1) type = tr.counterpart;
    }
    if (player === -1) {
      Renat.registerGenerator(G, x, z, type, a); // 0x43FFB0(x, z, type, a)
      continue; // no object index consumed
    }
    if (type === 40) {
      // vent: money = a * stat(0,2) / 256; team 8; rate = player-column * stat(0,1) / 256
      const money = idiv(a * G.stat(0, 2), 256);
      w8(gs, objAddr(next) + O.LIFE, 0);
      initObject(G, x, z, type, 8, money, 0, next);
      Grid.ventBitSet(G, x, z); // 0x41C758: load byte 3 |= 4 via map+4[z]
      w16(gs, objAddr(next) + OX.VENT_RATE, idiv(player * G.stat(0, 1), 256));
      next++;
      bumpMax();
      continue;
    }
    if (type === 37) {
      if (G.stat(0, 6) > 0 || sc.pairCounter === 0) {
        w8(gs, objAddr(next) + O.LIFE, 0);
        initObject(G, x, z, type, 8, a, 0, next);
        next++;
        Renat.registerResearchSite(G, x, z); // 0x440650(x, z)
        bumpMax();
      }
      continue; // a skipped site consumes no index (0x41C7A2 -> 0x41C866 without inc)
    }
    // 0x41C7FB: 0x4406F8(x, z, type [ebx]) - on a research-site tile the object becomes an item of the
    // site instead of an object; no index consumed
    if (Renat.addResearchItem(G, x, z, type)) continue;
    if (multiplayer) {
      if (player < 8 && sc.occupied[player] === 0) {
        next++; // 0x41C827 -> 0x41C865: the index IS consumed
        bumpMax();
        continue;
      }
    }
    initObject(G, x, z, type, player, a, b, next); // 0x41C841 / 0x41C860
    next++;
    bumpMax();
  }
  // 0x41C87D..0x41C994: random wildlife
  const groups = idiv(sc.pairCounter, 5);
  for (let g = 0; g < groups; g++) {
    for (let j = 0; G.stat(0, 5) > j; j++) {
      const type = irem(G.rand(), 2) !== 0 ? 0x55 : 0x5a; // 0x41C8BA
      let x;
      let z;
      do {
        x = irem(G.rand(), G.map.w); // 0x41C8F4
        z = irem(G.rand(), G.map.h); // 0x41C90C
      } while (G.map.families[z * G.map.w + x] === 0); // path cell +0xC (family) == 0 -> retry
      const [fx, fz] = findFreeCell(G, x, z, 0); // 0x41B68C(gs, x, z, &rx, &rz, 0)
      initObject(G, fx, fz, type, 9, -1, 2, next);
      next++;
      bumpMax();
    }
  }
  // 0x41C9A1: 0x406940 (no-op), fclose
  G.record(0); // 0x41C9B2: 0x44AC68(gs, 0)
  // 0x41C9B9: 0x437D00 dep_recompute — the local player's build-menu status bytes, display only
}

/** One TEAM block (0x41BEED..0x41C4EA), from mapJson.teams[i]. */
function loadTeam(G, team, i, multiplayer) {
  const gs = G.gs;
  const sc = G.scenario;
  const pa = playerAddr(i);
  G.assert(team && team.index === i, 'Whoa, team!=i, error in scenario format');
  if (!multiplayer) w32(gs, pa + P.RACE, team.race | 0); // 0x41BF8D
  w32(gs, pa + P.SPENT, 0); // 0x41BFAE
  w32(gs, pa + PX.DWORD_1C, 0);
  w32(gs, pa + P.MONEY, team.money | 0);
  w32(gs, pa + PX.FUNKY, 0);
  G.addStat(1, i, team.money | 0); // 0x41BFD1: 0x41A06C(k=1, p=i, money)
  if (!multiplayer) w32(gs, pa + P.AI_TYPE, team.ai | 0); // 0x41C006
  if (!multiplayer) {
    // 0x41C039: colour, team index when out of 0..7
    const c = team.colour | 0;
    w32(gs, pa + P.COLOUR, c < 0 || c > 7 ? i : c);
  }
  gs.fill(0, pa + P.DISABLED, pa + P.DISABLED + 110); // 0x41C051..0x41C06E
  for (const d of team.depend ?? []) {
    // 0x41C0A0: assert d>=0 && d<MAX_DEPEND_ITEMS; disabled[d] = 1
    G.assert(d >= 0 && d < 110, 'd>=0 && d<MAX_DEPEND_ITEMS');
    w8(gs, pa + P.DISABLED + d, 1);
  }
  // 0x41C127..0x41C1C6: ally line, 8 values
  for (let j = 0; j < 8; j++) {
    const v = (team.allies ?? [])[j] | 0;
    if (v !== 0) G.diploSet(0, i, j, 1); // 0x41E928(matrix0, i, j, 1)
    if (j === i) {
      G.diploSet(0, i, i, 1);
      G.diploSet(0, i, i, 1);
      G.diploSet(1, i, i, 1);
      G.diploSet(1, i, i, 1);
    }
  }
  // 0x41C1CB..0x41C1F6: alliance[c][9] = 1; alliance[9][c] = alliance[c][9]
  for (let c = 0; c < 8; c++) {
    w8(gs, GS.ALLIANCE + c * 10 + 9, 1);
    w8(gs, GS.ALLIANCE + 90 + c, u8(gs, GS.ALLIANCE + c * 10 + 9));
  }
  // AISlots line: read and ignored (0x41C206)
  const start = team.start ?? { x: 0, z: 0 };
  const city = team.city ?? { x: 0, z: 0 };
  w32(gs, pa + P.START_X, start.x | 0); // 0x41C235: sscanf "%d %d"
  w32(gs, pa + P.START_Z, start.z | 0);
  w32(gs, pa + P.CITY_X, city.x | 0); // 0x41C261
  w32(gs, pa + P.CITY_Z, city.z | 0);
  if (i32(gs, pa + P.CITY_X) === 0 || i32(gs, pa + P.CITY_Z) === 0) {
    w8(gs, pa + PX.NO_CITY, 1); // 0x41C276
    w8(gs, pa + P.DISABLED, 1);
  }
  if (i32(gs, pa + P.START_X) === 0 && i32(gs, pa + P.START_Z) === 0) {
    w32(gs, pa + P.START_X, i32(gs, pa + P.CITY_X)); // 0x41C290
    w32(gs, pa + P.START_Z, i32(gs, pa + P.CITY_Z));
  }
  // 0x41C2AF..0x41C397: five (count, hp) pairs — JSON fields {type: count, count: hp}
  const pairs = team.citySlots ?? [];
  for (let k = 0; k < 5; k++) {
    const first = (pairs[k]?.type ?? 0) | 0; // [ebp-28h]
    const second = (pairs[k]?.count ?? 0) | 0; // eax
    if (i32(gs, pa + P.CITY_X) !== 0 && first > 0) {
      let hp = second;
      if (second === -1) {
        // 0x41C2D4: 0x444E30(k, first-1) = unitdef[first-1][k] (0x48B0C4), its hit points (+0x44)
        const level = first - 1;
        const unitdef = G.consts.unitdef.values;
        const t = unitdef[level][k];
        hp = typeRec(G, t).hp;
      }
      w32(gs, pa + P.SLOT_HP + 4 * k, hp);
      w32(gs, pa + P.SLOT_LEVEL + 4 * k, first - 1);
    } else {
      w32(gs, pa + P.SLOT_HP + 4 * k, 0);
      w32(gs, pa + P.SLOT_LEVEL + 4 * k, 0);
    }
    if (multiplayer) {
      if (sc.occupied[i] === 0) {
        w32(gs, pa + P.SLOT_HP + 4 * k, 0); // 0x41C32D
        w32(gs, pa + P.SLOT_LEVEL + 4 * k, 0);
      } else {
        sc.pairCounter++; // 0x41C342
      }
    }
    // 0x41C345..0x41C393: the tower slot 5
    const hasCity = i32(gs, pa + P.CITY_X) !== 0 && i32(gs, pa + P.CITY_Z) !== 0 && (!multiplayer || sc.occupied[i] !== 0);
    w32(gs, pa + P.SLOT_LEVEL + 4 * 5, 0);
    w32(gs, pa + P.SLOT_HP + 4 * 5, hasCity ? 1 : 0);
  }
  // 0x41C3BE..0x41C3DA
  for (let slot = 0; slot < BUILDINGS_PER_SIDE; slot++) {
    w32(gs, pa + PX.SLOT_DWORDS_8C + 4 * slot, -1);
    w8(gs, pa + PX.SLOT_BYTES + slot, 0);
  }
  // 0x41C3E5..0x41C456: eight building rows -> per-player weapon/armour level of 0x41AFA0[race*8+j]
  const race = i32(gs, pa + P.RACE);
  const buildingList = G.consts.buildingList.values;
  for (let j = 0; j < 8; j++) {
    const raw = team.buildings?.[j]?.raw ?? [0, 0, 0, 0, 0];
    const t = buildingList[race * 8 + j];
    const r = typeRec(G, t);
    r.weaponLevel[i] = raw[2] & 0xff; // byte store at +0x30+i
    r.armourLevel[i] = raw[3] & 0xff; // byte store at +0x38+i
  }
  // 0x41C458..0x41C4E4
  for (let k = 0; k < 4; k++) {
    w16(gs, pa + P.QUEUE_LEN + 2 * k, 0);
    w8(gs, pa + P.PROD_COUNT + k, 0);
    w8(gs, pa + P.PROD_READY + k, 1);
  }
  w32(gs, pa + PX.HERO_COUNT, 0);
  for (let k = 0; k < 4; k++) w16(gs, pa + P.HEROES + 2 * k, -1);
  w8(gs, pa + PX.BYTE_E16, 0);
  w8(gs, pa + PX.BYTES_E17 + i, 0); // written 8 times to the same byte (0x41C4B5)
  w32(gs, pa + P.INCOME, 3);
  w32(gs, pa + P.VISION, 0x40000000 >> i);
}

// ---- initObject 0x41B124(gs, x, z, type, team, hp, pickup, obj) ---------------------------------

export function initObject(G, x, z, typeArg, team, hp, pickup, obj) {
  const gs = G.gs;
  const a = objAddr(obj);
  G.assert(typeArg !== 999, 'type!=999');
  G.assert(obj < MAX_OBJECTS, 'i<MAX_OBJECTS');
  G.assert(x >= 0 && z >= 0, 'xpos>=0 && zpos>=0');
  G.assert(x < G.map.w && z < G.map.h, 'xpos<gs->map->xsize && zpos<gs->map->ysize');
  G.assert(team >= 0 && team < 10, 'team>=0 && team<MAX_N_TEAMS');
  w16(gs, a + O.HEIGHT, 0);
  w8(gs, a + OX.BYTE_CC, 0);
  w16(gs, a + O.X, (x << 8) + 0x80);
  w8(gs, a + OX.BYTE_CF, 0);
  w8(gs, a + O.SELECT, 0);
  w16(gs, a + O.Z, (z << 8) + 0x80);
  w8(gs, a + OX.TILE_X, x);
  w8(gs, a + OX.TILE_Z, z);
  w8(gs, a + O.TYPE, typeArg);
  w8(gs, a + O.TEAM, team);
  w8(gs, a + O.NUDGE, 0xff);
  // 0x41B3CE..0x41B417: commanders re-typed by the owner's rank (byte of player[team].RANK)
  let t = u8(gs, a + O.TYPE);
  if (t >= 0x45 && t <= 0x48) {
    w8(gs, a + O.TYPE, u8(gs, playerAddr(team) + P.RANK) + 0x45);
    t = u8(gs, a + O.TYPE);
  }
  if (t >= 0x49 && t <= 0x4c) {
    w8(gs, a + O.TYPE, u8(gs, playerAddr(team) + P.RANK) + 0x49);
    t = u8(gs, a + O.TYPE);
  }
  if (t >= 0x45 && t <= 0x4c) {
    // 0x41B42F: hero table of the owner
    const pa = playerAddr(team);
    const n = i32(gs, pa + PX.HERO_COUNT);
    G.assert(n < 4, 'gs->player[team].num_commanders<4');
    w16(gs, pa + P.HEROES + 2 * n, obj);
    w32(gs, pa + PX.HERO_COUNT, n + 1);
  }
  const tr = typeRec(G, typeArg); // 0x41B4C1: the type ARGUMENT, not the adjusted byte
  w8(gs, a + O.LIFE, 1);
  w8(gs, a + OX.BYTE_13, 0);
  w8(gs, a + O.PENDING, 0);
  w8(gs, a + O.WP_COUNT, 0);
  w8(gs, a + O.DAMAGED, 0);
  w8(gs, a + O.REDRAW1, 0);
  w8(gs, a + O.REDRAW2, 0);
  w8(gs, a + O.HEADING, tr.colour & 0xff); // +0xE0: tables.js names it `colour`, 0x41B50A stores it as the heading
  w8(gs, a + O.PICKUP, pickup & 0xff);
  w8(gs, a + OX.BYTE_D1, 0xff);
  Ticker.resetAndDispatchOrder(G, obj); // 0x41233C(gs, obj): stack reset, pending order or idle
  w8(gs, a + O.CHARGE, 0x40);
  w32(gs, a + O.HP, hp > -1 ? hp : tr.hp); // 0x41B52B: `cmp ebx,-1; jle default`
  w16(gs, a + O.AI_NEXT, -2);
  w16(gs, a + O.AI_PREV, -2);
  w8(gs, a + OX.BYTE_11, 0);
  w8(gs, a + O.LINK_COUNT, 0);
  w8(gs, a + O.BURST, 0);
  w8(gs, a + O.BERSERK, 0);
  const stand = Anim.pose(G, typeArg, 'STAND'); // type+0x80
  Anim.startAnim(G, a + O.ANIM0, stand, 0); // 0x42626C(&anim0, stand, 0)
  Anim.startAnim(G, a + O.ANIM1, stand, 2);
  Anim.startAnim(G, a + O.ANIM2, stand, 2);
  if (tr.fly !== 0) w16(gs, a + O.HEIGHT, 0x258); // 0x41B5B4: +0x60 of the type argument
  if (team !== 8) {
    // 0x41B5CD: layer from the (rank-adjusted) TYPE byte
    const tt = typeRec(G, u8(gs, a + O.TYPE));
    if (tt.hidden !== 0) Grid.placeSec(G, x, z, obj); // +0x68
    else if (tt.fly === 0) Grid.placeGround(G, x, z, obj);
    else Grid.placeAir(G, x, z, obj);
  }
  w16(gs, GS.OBJ_ALLOC + obj * 2, obj); // 0x41B67C
}

// ---- createObject 0x41B930(gs, x, z, type, team, obj) ------------------------------------------

/** 0x41B930: `obj` -1 = allocate (the LAST free slot in 0x98..MAX_OBJ-1, else MAX_OBJ++). */
export function createObject(G, x, z, type, team, obj = -1) {
  if (obj === -1) obj = allocObject(G, 0x139);
  initObject(G, x, z, type, team, -1, 0, obj);
  return obj;
}

/** 0x41B818(gs, x, z, type, team): like createObject but on the nearest free cell (vents excepted). */
export function createObjectNear(G, x, z, type, team) {
  let rx = x;
  let rz = z;
  if (type !== 0x28) [rx, rz] = findFreeCell(G, x, z, typeRec(G, type).fly); // flag = type+0x60
  const obj = allocObject(G, 0x123);
  initObject(G, rx, rz, type, team, -1, 0, obj);
  return obj;
}

/** The free-slot scan shared by 0x41B818 (line 0x123) and 0x41B930 (line 0x139). */
function allocObject(G, line) {
  const gs = G.gs;
  let obj = -1;
  const max = i32(gs, GS.MAX_OBJ);
  for (let k = FIRST_UNIT; k < max; k++) if (u8(gs, objAddr(k) + O.LIFE) === 0) obj = k; // keeps the last
  if (obj === -1) {
    obj = max;
    w32(gs, GS.MAX_OBJ, max + 1);
    G.assert(max + 1 < MAX_OBJECTS, `gs->number_of_objects<MAX_OBJECTS (mobiles.c ${line})`);
  }
  return obj;
}

/**
 * 0x41B68C(gs, x, z, &rx, &rz, air): growing squares around (x, z), re-scanning the whole square
 * each ring; ground: path family != 0 and ground id == 0x3FF; air: air id == 0x3FF. Asserts when
 * nothing is free within max(w, h) rings.
 */
export function findFreeCell(G, x, z, air) {
  const m = G.map;
  const n = Math.max(m.w, m.h);
  for (let ring = 0; ring < n; ring++) {
    for (let rx = x - ring; rx <= x + ring; rx++) {
      if (rx < 0 || rx >= m.w) continue;
      for (let rz = z - ring; rz <= z + ring; rz++) {
        if (rz < 0 || rz >= m.h) continue;
        const i = rz * m.w + rx;
        if (!air) {
          if (m.families[i] !== 0 && (m.ground[i] & 0x3ff) === 0x3ff) return [rx, rz];
        } else if ((m.air[i] & 0x3ff) === 0x3ff) return [rx, rz];
      }
    }
  }
  G.assert(false, 'no free cell (mobiles.c 0x107)');
  return [x, z];
}

// ---- unit cap 0x41E7FC(gs) ------------------------------------------------------------------------

export function unitCap(G) {
  const gs = G.gs;
  const built = new Uint8Array(10);
  const counts = new Int32Array(10);
  let nBuilt = 0;
  let cap = 0x288 - Renat.generatorCreatureTotal(G); // 0x4400C0: sum of the generator counts
  for (let p = 0; p < 8; p++) {
    const pa = playerAddr(p);
    for (let slot = 0; slot < 5; slot++) if (i32(gs, pa + P.SLOT_HP + 4 * slot) !== 0) built[p] = 1;
    if (built[p]) nBuilt++;
  }
  for (let obj = FIRST_UNIT; obj < MAX_OBJECTS; obj++) {
    const a = objAddr(obj);
    if (u8(gs, a + O.LIFE) !== 0) counts[u8(gs, a + O.TEAM)]++;
  }
  for (let team = 0; team <= 8; team++) if (!built[team]) cap -= counts[team]; // 0x41E8AE: `jg` -> 0..8
  cap -= 100;
  if ((nBuilt << 24) >> 24 > 0) cap = idiv(cap, (nBuilt << 24) >> 24); // signed byte at [ebp-4]
  const max = i32(gs, GX.UNIT_CAP_MAX);
  if (cap > max) cap = max;
  return cap;
}

// ---- small helpers of mobiles.c ------------------------------------------------------------------

/** 0x41B07C: production class of a city slot (table 0x41B040). */
export const slotClass = (G, slot) => G.consts.slotToClass.values[slot];

/** 0x41B088(gs, obj): the object belongs to the local player. */
export function isLocalTeam(G, obj) {
  return u8(G.gs, objAddr(obj) + O.TEAM) === i32(G.gs, GS.LOCAL_PLAYER);
}

/** 0x41B0C4(gs, obj): team >= 8, or allied with the local player (alliance[team][local]). */
export function isAlliedWithLocal(G, obj) {
  const team = u8(G.gs, objAddr(obj) + O.TEAM);
  if (team >= 8) return true;
  return u8(G.gs, GS.ALLIANCE + team * 10 + i32(G.gs, GS.LOCAL_PLAYER)) !== 0;
}

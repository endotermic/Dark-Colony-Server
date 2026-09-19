#!/usr/bin/env node
// Headless self-play of the Krusty port (src/engine/krusty.js) against the server engine: two bots
// on a multiplayer map, their commands applied to the engine at once (a client would execute them one
// frame later), a log of what they build and where they send their troops. Plan §19.10.
//
//   node tools/botmatch.js [MAP=D8PLAY01] [TICKS=12000] [--seats 0,3] [--race 0,1] [--seed N] [--quiet]
//
// Prints the engine asserts (there must be none), the Krusty asserts, the command counts by type,
// every bot's chat lines and a summary of its state (tasks, groups, units) at the end.

import { createGame, loadMapJson } from '../src/engine/index.js';
import { KrustyBot } from '../src/krustybot.js';
import { splitCommands } from '../src/commands.js';
import { GS, O, P, objAddr, playerAddr, u8, i32 } from '../src/engine/mem.js';

function parseArgs(argv) {
  const opts = { map: 'D8PLAY01', ticks: 12000, seats: [0, 3], races: [0, 1], seed: 11, quiet: false };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--seats') opts.seats = argv[++i].split(',').map(Number);
    else if (a === '--race') opts.races = argv[++i].split(',').map(Number);
    else if (a === '--seed') opts.seed = Number(argv[++i]);
    else if (a === '--quiet') opts.quiet = true;
    else rest.push(a);
  }
  if (rest[0]) opts.map = rest[0];
  if (rest[1]) opts.ticks = Number(rest[1]);
  return opts;
}

const opts = parseArgs(process.argv.slice(2));
const mapJson = loadMapJson(opts.map);
if (!mapJson) {
  console.error(`no maps/${opts.map}.json (node tools/map2json.js makes one)`);
  process.exit(2);
}
const slots = [];
for (let s = 0; s < 8; s++) {
  const k = opts.seats.indexOf(s);
  slots.push({ type: k >= 0 ? 2 : 3, race: k >= 0 ? opts.races[k % opts.races.length] | 0 : 0, colour: s, team: s, name: k >= 0 ? `Bot${s}` : '' });
}
const asserts = [];
const G = createGame(mapJson, { slots, localSlot: -1, titleDigit: mapJson.players ?? 8 }, { assert: (msg, g) => asserts.push({ tick: g.tick, msg }) });
const bots = opts.seats.map((s, i) => {
  const p = G.scenario.slotToPlayer[s];
  return { slot: s, p, bot: new KrustyBot(G, p, { seed: (opts.seed + 17 * i) & 0xff }), lines: [] };
});
const byType = {};
const t0 = Date.now();
for (let t = 1; t <= opts.ticks; t++) {
  for (const b of bots) {
    if (t % 32 !== (4 + 4 * b.p) % 32) continue;
    const out = b.bot.think(t);
    for (const buf of out.commands) {
      // one group = one or more commands; apply them in order like the engine feed does
      for (const c of splitCommands(buf)) {
        byType[c.type] = (byType[c.type] ?? 0) + 1;
        G.applyCommand(c.raw);
      }
    }
    for (const l of out.lines) b.lines.push(`${t}: ${l}`);
  }
  G.step();
}
const ms = Date.now() - t0;

console.log(`${opts.map}: ${opts.ticks} ticks in ${ms} ms, engine asserts ${asserts.length}, commands by type ${JSON.stringify(byType)}`);
for (const a of asserts.slice(0, 5)) console.log(`  engine assert at tick ${a.tick}: ${a.msg}`);
for (const b of bots) {
  const pa = playerAddr(b.p);
  const slotsBuilt = [];
  for (let s = 0; s < 15; s++) slotsBuilt.push(i32(G.gs, pa + P.SLOT_HP + 4 * s) ? String(s % 10) : '.');
  const units = {};
  let n = 0;
  const maxObj = i32(G.gs, GS.MAX_OBJ);
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    const life = u8(G.gs, a + O.LIFE);
    if (life === 0 || life === 10 || u8(G.gs, a + O.TEAM) !== b.p) continue;
    n++;
    const name = G.tables.types[u8(G.gs, a + O.TYPE)].name;
    units[name] = (units[name] ?? 0) + 1;
  }
  console.log(`\nslot ${b.slot} = player ${b.p}: money ${i32(G.gs, pa + P.MONEY)}, buildings ${slotsBuilt.join('')}, ${n} units ${JSON.stringify(units)}`);
  console.log(`  ${JSON.stringify(b.bot.summary())}`);
  if (!opts.quiet) for (const l of b.lines) console.log(`  ${l}`);
}
process.exit(asserts.length ? 1 : 0);

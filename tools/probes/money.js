// Money trace of the candidate in one arena job: money, SPENT, income, purchases (0x09/0x0A/0x0C) and units, per period.
// node money.mjs <root> <arena.json> <jobId> [period=250] [from=0] [to=end]
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS, periodS, fromS, toS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands, T } = await imp('src/commands.js');
const { O, objAddr, u8, i32, GS, P, playerAddr } = await imp('src/engine/mem.js');
const job = JSON.parse(fs.readFileSync(jsonFile, 'utf8')).games.find((g) => g.job.id === Number(idS)).job;
const period = Number(periodS ?? 250);
const from = Number(fromS ?? 0);
const to = Number(toS ?? job.ticks);
const mapJson = loadMapJson(job.map);
const specs = [A.parseSpec(job.a), A.parseSpec(job.b)];
const rot = job.swap ? 1 : 0;
const seatOf = specs.map((_, i) => job.seats[(rot + i) % 2]);
const raceOf = specs.map((_, i) => job.races[(rot + i) % 2]);
const slots = [];
for (let s = 0; s < 8; s++) { const side = seatOf.indexOf(s); slots.push({ type: side >= 0 ? 2 : 3, race: side >= 0 ? raceOf[side] : 0, colour: s, team: s, name: side >= 0 ? `Bot${s}` : '' }); }
const G = createGame(mapJson, { slots, localSlot: -1, titleDigit: mapJson.players ?? 8 }, { assert: () => {} });
const sides = specs.map((sp, i) => ({ p: G.scenario.slotToPlayer[seatOf[i]], brain: A.makeBrain(sp, G, G.scenario.slotToPlayer[seatOf[i]], (job.seed + 17 * i) & 0xff) }));
const gs = G.gs;
const pa = playerAddr(sides[0].p);
const name = Object.fromEntries(Object.entries(T).map(([k, v]) => [v, k]));
let buys = [];
let say = [];
for (let t = 1; t <= to; t++) {
  for (const [i, s] of sides.entries()) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    const before = i32(gs, pa + P.MONEY);
    const out = s.brain.think(t);
    if (i === 0) {
      const after = i32(gs, pa + P.MONEY);
      if (after !== before) buys.push(`${t}:brain-${before - after}`);
      for (const l of out.lines) if (/bought|Buy|upgrade|Defence|Turret|train|saving/i.test(l)) say.push(`${t} ${l}`);
    }
    for (const b of out.commands) for (const c of splitCommands(b)) {
      if (i === 0 && [T.BUILD, T.UPGRADE, 0x09, 0x0a, 0x0c].includes(c.type)) buys.push(`${t}:${name[c.type] ?? c.type}(${[...c.raw.subarray(1, 6)].join(',')})`);
      G.applyCommand(c.raw);
    }
  }
  G.step();
  if (t % period || t < from) continue;
  let units = 0;
  const kinds = {};
  for (let o = 120; o <= i32(gs, GS.MAX_OBJ); o++) {
    const a = objAddr(o);
    const l = u8(gs, a + O.LIFE);
    if (!l || l === 10 || u8(gs, a + O.TEAM) !== sides[0].p) continue;
    units++;
    const n = G.tables.types[u8(gs, a + O.TYPE)].name;
    kinds[n] = (kinds[n] ?? 0) + 1;
  }
  const q = [0, 1, 2, 3].map((k) => gs.readUInt16LE(pa + P.QUEUE_LEN + 2 * k)).join('/');
  const slotsB = [0, 1, 2, 3].map((k) => (i32(gs, pa + P.SLOT_HP + 4 * k) ? 'HBFS'[k] : '-')).join('');
  console.log(`@${t} money ${i32(gs, pa + P.MONEY)} spent ${i32(gs, pa + P.SPENT)} income ${G.stat(1, sides[0].p)} ${slotsB} queue ${q} units ${units} ${JSON.stringify(kinds)}`);
  if (buys.length) console.log(`    buys: ${buys.join(' ')}`);
  if (say.length) console.log(`    said: ${say.join(' | ')}`);
  buys = [];
  say = [];
}

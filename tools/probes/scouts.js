import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands } = await imp('src/commands.js');
const { O, objAddr, u8, i32, GS } = await imp('src/engine/mem.js');
const job = JSON.parse(fs.readFileSync(jsonFile, 'utf8')).games.find((g) => g.job.id === Number(idS)).job;
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
let lost = 0, seen = new Set();
for (let t = 1; t <= job.ticks; t++) {
  for (const s of sides) { if (t % 32 !== (4 + 4 * s.p) % 32) continue; for (const b of s.brain.think(t).commands) for (const c of splitCommands(b)) G.applyCommand(c.raw); }
  G.step();
  if (t % 1000) continue;
  const x = sides[0].brain.aux.x;
  const sc = [];
  for (let o = 120; o <= i32(gs, GS.MAX_OBJ); o++) { const a = objAddr(o); const l = u8(gs, a + O.LIFE); if (!l || l === 10 || u8(gs, a + O.TEAM) !== sides[0].p || ![5, 13].includes(u8(gs, a + O.TYPE))) continue; seen.add(o); const j = x.scoutJobs?.get(o); sc.push(`#${o}@${gs.readUInt16LE(a + O.X) >> 8},${gs.readUInt16LE(a + O.Z) >> 8} hp${i32(gs, a + O.HP)} ${j ? j.key : '-'}`); }
  console.log(`@${t} scouts ${sc.length}: ${sc.join(' | ')} | enemy mines known ${x.enemy.mines.length}, buildings ${x.enemy.buildings.length}`);
}
console.log(`scouts ever ${seen.size}, retreats ${sides[0].brain.aux.x.stats.scoutRetreats ?? 0}`);

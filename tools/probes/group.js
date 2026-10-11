// Which Krusty task/minor holds an object at given ticks: node group.mjs <root> <arena.json> <jobId> <obj> <tick,tick,...>
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS, objS, ticksS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const K = await imp('src/engine/krusty.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands } = await imp('src/commands.js');
const { O, objAddr, u8, i32 } = await imp('src/engine/mem.js');
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
const obj = Number(objS);
const at = new Set(ticksS.split(',').map(Number));
const last = Math.max(...at);
for (let t = 1; t <= last; t++) {
  for (const s of sides) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    for (const b of s.brain.think(t).commands) for (const c of splitCommands(b)) G.applyCommand(c.raw);
  }
  G.step();
  if (!at.has(t)) continue;
  const kai = sides[0].brain.kai;
  const found = [];
  for (let tk = 0; tk < 4; tk++) for (let m = 0; m < 16; m++) {
    if (!u8(kai, K.minorAddr(tk, m) + K.MN.ACTIVE)) continue;
    if (K.listOf(G.gs, kai, tk, m).includes(obj)) found.push(`task ${tk} minor ${m} zone ${i32(kai, K.minorAddr(tk, m) + K.MN.ZONE) & 0xff} state ${u8(kai, K.minorAddr(tk, m) + K.MN.STATE)}`);
  }
  const x = sides[0].brain.aux.x;
  console.log(`@${t} #${obj} type ${u8(G.gs, objAddr(obj) + O.TYPE)} OA.ZONE ${u8(G.gs, objAddr(obj) + 0x11)} in: ${found.join('; ') || 'NO GROUP'} | special slots ${[...x.special.entries()].filter(([, v]) => v).map(([k]) => k).join(',')} esc phase ${x.esc.phase}`);
}

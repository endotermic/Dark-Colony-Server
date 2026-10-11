// Everything on and around a tile at a tick, and what the candidate's memory says about it.
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS, sxS, szS, TS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands } = await imp('src/commands.js');
const { O, objAddr, u8, i8, i32, GS } = await imp('src/engine/mem.js');
const K = await imp('src/engine/krusty.js');
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
for (let t = 1; t <= Number(TS); t++) {
  for (const s of sides) { if (t % 32 !== (4 + 4 * s.p) % 32) continue; for (const b of s.brain.think(t).commands) for (const c of splitCommands(b)) G.applyCommand(c.raw); }
  G.step();
}
const sx = Number(sxS), sz = Number(szS);
const x = sides[0].brain.aux.x;
console.log(`tick ${TS}, around ${sx},${sz}; us p${sides[0].p}, rusher p${sides[1].p}`);
for (let o = 0; o <= i32(gs, GS.MAX_OBJ); o++) {
  const a = objAddr(o);
  const l = u8(gs, a + O.LIFE);
  if (!l) continue;
  const tx = gs.readUInt16LE(a + O.X) >> 8, tz = gs.readUInt16LE(a + O.Z) >> 8;
  if (Math.max(Math.abs(tx - sx), Math.abs(tz - sz)) > 3) continue;
  const ty = u8(gs, a + O.TYPE);
  console.log(`  #${o} ${G.tables.types[ty]?.name} type ${ty} team ${u8(gs, a + O.TEAM)} at ${tx},${tz} life ${l} hp ${i32(gs, a + O.HP)} fly ${G.tables.types[ty]?.fly}`);
}
const recs = x.seenFighters.filter((r) => Math.max(Math.abs(r.x - sx), Math.abs(r.z - sz)) <= 3);
console.log('seenFighters there:', JSON.stringify(recs));
for (const r of recs) { const ma = K.memAddr ? K.memAddr(r.o) : null; const a = objAddr(r.o); console.log(`  rec #${r.o}: real object type ${u8(gs, a + O.TYPE)} team ${u8(gs, a + O.TEAM)} life ${u8(gs, a + O.LIFE)} at ${gs.readUInt16LE(a + O.X) >> 8},${gs.readUInt16LE(a + O.Z) >> 8}`); }

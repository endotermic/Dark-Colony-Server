// Bases and vents of an arena job at tick T: HQ positions, every free vent with its hop for us and its distances.
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, jobIdS, T] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands } = await imp('src/commands.js');
const { O, objAddr, u8, i32, i16, P, playerAddr } = await imp('src/engine/mem.js');
const job = JSON.parse(fs.readFileSync(jsonFile, 'utf8')).games.find((g) => g.job.id === Number(jobIdS)).job;
const mapJson = loadMapJson(job.map);
const specs = [A.parseSpec(job.a), A.parseSpec(job.b)];
const rot = job.swap ? 1 : 0;
const seatOf = specs.map((_, i) => job.seats[(rot + i) % 2]);
const raceOf = specs.map((_, i) => job.races[(rot + i) % 2]);
const slots = [];
for (let s = 0; s < 8; s++) { const side = seatOf.indexOf(s); slots.push({ type: side >= 0 ? 2 : 3, race: side >= 0 ? raceOf[side] : 0, colour: s, team: s, name: side >= 0 ? `Bot${s}` : '' }); }
const G = createGame(mapJson, { slots, localSlot: -1, titleDigit: mapJson.players ?? 8 }, { assert: () => {} });
const sides = specs.map((spec, i) => ({ p: G.scenario.slotToPlayer[seatOf[i]], brain: A.makeBrain(spec, G, G.scenario.slotToPlayer[seatOf[i]], (job.seed + 17 * i) & 0xff) }));
for (let t = 1; t <= Number(T); t++) {
  for (const s of sides) { if (t % 32 !== (4 + 4 * s.p) % 32) continue; for (const b of s.brain.think(t).commands) for (const c of splitCommands(b)) G.applyCommand(c.raw); }
  G.step();
}
const hq = sides.map((s) => [i32(G.gs, playerAddr(s.p) + P.CITY_X), i32(G.gs, playerAddr(s.p) + P.CITY_Z)]);
console.log(`map ${G.map.w}x${G.map.h}; us HQ ${hq[0]} (seat ${seatOf[0]}), rusher HQ ${hq[1]} (seat ${seatOf[1]})`);
const d = (a, b) => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]));
for (let o = 0; o < 800; o++) {
  const a = objAddr(o);
  if (u8(G.gs, a + O.TYPE) !== 0x28 || !u8(G.gs, a + O.LIFE)) continue;
  const v = [G.gs.readUInt16LE(a + O.X) >> 8, G.gs.readUInt16LE(a + O.Z) >> 8];
  if (d(v, hq[0]) > 70) continue;
  console.log(`vent ${o} at ${v}: rate ${i16(G.gs, a + 0x32)}  to us ${d(v, hq[0])}  to rusher ${d(v, hq[1])}`);
}

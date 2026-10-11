// The defence episode over time: threats, pushing, and the seen enemy fighters left out of it (distance to our HQ,
// to our nearest mine, to our nearest fighter). node episode.mjs <root> <arena.json> <jobId> [every]
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS, everyS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands } = await imp('src/commands.js');
const { O, objAddr, u8, i32, GS, P, playerAddr } = await imp('src/engine/mem.js');
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
const us = sides[0].p;
const every = Number(everyS ?? 256);
const cheb = (a, b) => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]));
const tile = (o) => [gs.readUInt16LE(objAddr(o) + O.X) >> 8, gs.readUInt16LE(objAddr(o) + O.Z) >> 8];
const rhq = [i32(gs, playerAddr(sides[1].p) + P.CITY_X), i32(gs, playerAddr(sides[1].p) + P.CITY_Z)];
for (let t = 1; t <= job.ticks; t++) {
  for (const s of sides) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    for (const b of s.brain.think(t).commands) for (const c of splitCommands(b)) G.applyCommand(c.raw);
  }
  G.step();
  if (t % every) continue;
  const x = sides[0].brain.aux.x;
  const hq = x.hq;
  const mines = [];
  const ours = [];
  for (let o = 120; o <= i32(gs, GS.MAX_OBJ); o++) {
    const a = objAddr(o);
    const l = u8(gs, a + O.LIFE);
    if (!l || l === 10 || u8(gs, a + O.TEAM) !== us) continue;
    const ty = u8(gs, a + O.TYPE);
    if (ty === 0x2f || ty === 0x30) mines.push(tile(o));
    else if (ty < 16 && G.tables.types[ty].weapon[0] !== -1) ours.push(tile(o));
  }
  const left = (x.seenFighters ?? []).filter((r) => !(x.threats ?? []).some((q) => q.o === r.o) && !(x.pushFront ?? []).some(([fx, fz]) => fx === r.x && fz === r.z));
  const desc = left.slice(0, 6).map((r) => `@${r.x},${r.z} hq${cheb([r.x, r.z], hq)} mine${mines.length ? Math.min(...mines.map((m) => cheb(m, [r.x, r.z]))) : '-'} ours${ours.length ? Math.min(...ours.map((m) => cheb(m, [r.x, r.z]))) : '-'}${r.approaching ? ' appr' : ''}`);
  console.log(`@${t} threats ${x.threats?.length ?? 0} pushing ${x.pushing} front ${x.pushFront?.length ?? 0} | ours ${ours.length} mines ${mines.length} | seen ${x.seenFighters?.length ?? 0}, left out ${left.length}: ${desc.join(' ')} | rusher HQ ${cheb(rhq, hq)} away`);
}

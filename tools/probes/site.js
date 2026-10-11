// Watch one site of the candidate: node siteprobe.mjs <root> <arena.json> <jobId> <x> <z> [from] [to] [every]
// Every `every` ticks: our object on/near the tile (type, hp), enemies within 14 (seen s / threat T / attacking-mark A),
// our nearest armed units and their last orders.
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS, sxS, szS, fromS, toS, everyS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands, T } = await imp('src/commands.js');
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
const us = sides[0].p;
const them = sides[1].p;
const site = [Number(sxS), Number(szS)];
const from = Number(fromS ?? 0);
const to = Number(toS ?? job.ticks);
const every = Number(everyS ?? 64);
const tile = (o) => [gs.readUInt16LE(objAddr(o) + O.X) >> 8, gs.readUInt16LE(objAddr(o) + O.Z) >> 8];
const cheb = (a, b) => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]));
const live = (o) => { const l = u8(gs, objAddr(o) + O.LIFE); return l && l !== 10; };
const dest = new Map();
for (let t = 1; t <= to; t++) {
  for (const [i, s] of sides.entries()) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    const out = s.brain.think(t);
    let wp = null;
    for (const b of out.commands) for (const c of splitCommands(b)) {
      if (i === 0 && c.type === T.WAYPOINTS_OBJ) { const n = c.raw[1]; wp = [c.raw.readInt16LE(4 + 4 * (n - 1)) >> 8, c.raw.readInt16LE(6 + 4 * (n - 1)) >> 8]; }
      if (i === 0 && c.type === T.ORDER && wp) dest.set(c.raw.readInt16LE(1), `${wp}(${c.raw[3]}@${t})`);
      G.applyCommand(c.raw);
    }
  }
  G.step();
  if (t < from || t % every) continue;
  const x = sides[0].brain.aux.x;
  const maxO = i32(gs, GS.MAX_OBJ);
  const mine = [];
  const foes = [];
  const ours = [];
  for (let o = 120; o <= maxO; o++) {
    if (!live(o)) continue;
    const a = objAddr(o);
    const team = u8(gs, a + O.TEAM);
    const ty = u8(gs, a + O.TYPE);
    const p = tile(o);
    const d = cheb(p, site);
    if (team === us && d <= 1) mine.push(`${G.tables.types[ty].name}#${o} hp ${i32(gs, a + O.HP)}`);
    if (team === them && ty < 16 && d <= 14) {
      const att = u8(gs, a + O.ATTACKED);
      foes.push(`${G.tables.types[ty].name}#${o}@${p} d${d}${x.seenFighters?.some((r) => r.o === o) ? 's' : '-'}${x.threats?.some((r) => r.o === o) ? 'T' : '-'}${att & 0x1f ? `A${att >> 5}` : ''}`);
    }
    if (team === us && (ty < 16 || (ty >= 69 && ty <= 76)) && G.tables.types[ty].weapon[0] !== -1) ours.push({ d, s: `${G.tables.types[ty].name}#${o}@${p} d${d} ->${dest.get(o) ?? '-'}` });
  }
  ours.sort((p, q) => p.d - q.d);
  console.log(`@${t} site ${site}: ${mine.join(' ') || 'NOTHING OF OURS'} | danger ${x.danger} threats ${x.threats?.length ?? 0} | foes ${foes.join(' ') || '-'}`);
  console.log(`     nearest ours: ${ours.slice(0, 4).map((q) => q.s).join(' | ')}`);
}

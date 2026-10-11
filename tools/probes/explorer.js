// The candidate's explorers between two ticks: position, hp, nearest enemy fighter (true position, seen by us or not),
// and the bot's escort state. node explorer.mjs <root> <arena.json> <jobId> <from> <to> [every]
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS, fromS, toS, everyS] = process.argv.slice(2);
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
const them = sides[1].p;
const tile = (o) => [gs.readUInt16LE(objAddr(o) + O.X) >> 8, gs.readUInt16LE(objAddr(o) + O.Z) >> 8];
const cheb = (a, b) => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]));
const live = (o) => { const l = u8(gs, objAddr(o) + O.LIFE); return l && l !== 10; };
const every = Number(everyS ?? 32);
console.log(`our HQ ${i32(gs, playerAddr(us) + P.CITY_X)},${i32(gs, playerAddr(us) + P.CITY_Z)}; rusher HQ ${i32(gs, playerAddr(them) + P.CITY_X)},${i32(gs, playerAddr(them) + P.CITY_Z)}`);
for (let t = 1; t <= Number(toS); t++) {
  for (const s of sides) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    for (const b of s.brain.think(t).commands) for (const c of splitCommands(b)) G.applyCommand(c.raw);
  }
  G.step();
  if (t < Number(fromS) || t % every) continue;
  const x = sides[0].brain.aux.x;
  const maxO = i32(gs, GS.MAX_OBJ);
  for (let o = 120; o <= maxO; o++) {
    if (!live(o) || u8(gs, objAddr(o) + O.TEAM) !== us || ![6, 14].includes(u8(gs, objAddr(o) + O.TYPE))) continue;
    const p = tile(o);
    let best = null;
    let bd = 99;
    for (let e = 120; e <= maxO; e++) {
      if (!live(e) || u8(gs, objAddr(e) + O.TEAM) !== them) continue;
      const ty = u8(gs, objAddr(e) + O.TYPE);
      if (!(ty < 16 || (ty >= 69 && ty <= 76))) continue;
      const d = cheb(p, tile(e));
      if (d < bd) { bd = d; best = e; }
    }
    const seen = best !== null && x.seenFighters.some((r) => r.o === best);
    const ev = x.esc.vent >= 0 ? objAddr(x.esc.vent) : 0; const vinfo = ev ? `vent#${x.esc.vent} type ${u8(gs, ev + O.TYPE)} life ${u8(gs, ev + O.LIFE)} rate ${gs.readInt16LE(ev + 0x32)} hp ${i32(gs, ev + O.HP)} occ ${G.map.ground[(gs.readUInt16LE(ev + O.Z) >> 8) * G.map.w + (gs.readUInt16LE(ev + O.X) >> 8)] & 0x3ff}` : "-"; console.log(`@${t} explorer #${o} at ${p} hp ${i32(gs, objAddr(o) + O.HP)} [${vinfo}] state ${u8(gs, objAddr(o) + O.STACK + 2 * u8(gs, objAddr(o) + O.SP))} | nearest enemy ${best !== null ? `${G.tables.types[u8(gs, objAddr(best) + O.TYPE)].name}#${best}@${tile(best)} d${bd} ${seen ? 'seen' : 'NOT seen'}` : '-'} | escort ${x.esc.phase} vent ${x.esc.vx},${x.esc.vz}`);
  }
}

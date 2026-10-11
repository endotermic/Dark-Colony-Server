// When one of the candidate's mines loses hit points: who attacks it, does the bot see them / count them as threats,
// and where are our fighters sent. node mineprobe.mjs <root> <arena.json> <jobId> [maxLines]
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS, maxS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands, T } = await imp('src/commands.js');
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
const pa = playerAddr(us);
const tile = (o) => [gs.readUInt16LE(objAddr(o) + O.X) >> 8, gs.readUInt16LE(objAddr(o) + O.Z) >> 8];
const cheb = (a, b) => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]));
const live = (o) => { const l = u8(gs, objAddr(o) + O.LIFE); return l && l !== 10; };
const hp = new Map();
const dest = new Map();
let lines = 0;
const max = Number(maxS ?? 30);
for (let t = 1; t <= job.ticks && lines < max; t++) {
  for (const [i, s] of sides.entries()) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    const out = s.brain.think(t);
    let wp = null;
    for (const b of out.commands) for (const c of splitCommands(b)) {
      if (i === 0 && c.type === T.WAYPOINTS_OBJ) {
        const n = c.raw[1];
        wp = [c.raw.readInt16LE(4 + 4 * (n - 1)) >> 8, c.raw.readInt16LE(6 + 4 * (n - 1)) >> 8];
      }
      if (i === 0 && c.type === T.ORDER && wp) dest.set(c.raw.readInt16LE(1), [wp, t, c.raw[3]]);
      G.applyCommand(c.raw);
    }
    if (i !== 0) continue;
    const x = s.brain.aux.x;
    const maxO = i32(gs, GS.MAX_OBJ);
    for (let o = 120; o <= maxO; o++) {
      const a = objAddr(o);
      if (!live(o) || u8(gs, a + O.TEAM) !== us || ![0x2f, 0x30].includes(u8(gs, a + O.TYPE))) continue;
      const h = i32(gs, a + O.HP);
      const was = hp.get(o) ?? h;
      hp.set(o, h);
      if (h >= was) continue;
      const anyFoe = (() => { for (let e = 120; e <= maxO; e++) { if (live(e) && u8(gs, objAddr(e) + O.TEAM) === them && u8(gs, objAddr(e) + O.TYPE) < 16 && cheb(tile(e), tile(o)) <= 10) return true; } return false; })();
      if (!anyFoe) continue;
      const m = tile(o);
      const hq = [i32(gs, pa + P.CITY_X), i32(gs, pa + P.CITY_Z)];
      const foes = [];
      for (let e = 120; e <= maxO; e++) {
        if (!live(e) || u8(gs, objAddr(e) + O.TEAM) !== them || u8(gs, objAddr(e) + O.TYPE) >= 16) continue;
        const ep = tile(e);
        if (cheb(ep, m) <= 10) foes.push(`#${e}@${ep}${x.seenFighters?.some((r) => r.o === e) ? 's' : '-'}${x.threats?.some((r) => r.o === e) ? 'T' : '-'}`);
      }
      const ours = [];
      for (let f = 120; f <= maxO; f++) {
        const fa = objAddr(f);
        if (!live(f) || u8(gs, fa + O.TEAM) !== us) continue;
        const ty = u8(gs, fa + O.TYPE);
        if (ty >= 16 && !(ty >= 69 && ty <= 76)) continue;
        if (G.tables.types[ty].weapon[0] === -1) continue;
        const fp = tile(f);
        const d = dest.get(f);
        ours.push({ d: cheb(fp, m), s: `${G.tables.types[ty].name}#${f}@${fp} d${cheb(fp, m)} ->${d ? `${d[0]}(${d[2]}@${d[1]})` : '-'}` });
      }
      ours.sort((p, q) => p.d - q.d);
      console.log(`@${t} MINE #${o} at ${m} (HQ ${hq}, ${cheb(m, hq)} away) hp ${was}->${h} | danger ${x.danger} threats ${x.threats?.length ?? 0} pushing ${x.pushing} | attackers ${foes.join(' ') || 'none seen'}`);
      console.log(`     ours (${ours.length}): ${ours.slice(0, 8).map((q) => q.s).join(' | ')}`);
      lines++;
    }
  }
  G.step();
}

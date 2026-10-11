// Which of our units fire during an arena job: per unit, shots (BURST increments), ticks with an enemy fighter within 6
// tiles, its state, the krusty task/slot it is in and the last order the bot gave it.
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, jobIdS, fromS, toS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands, T } = await imp('src/commands.js');
const { O, objAddr, u8, i32, GS } = await imp('src/engine/mem.js');
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
const us = sides[0].p;
const from = Number(fromS), to = Number(toS);
const stat = new Map();
const lastOrder = new Map();
const gs = G.gs;
const tile = (a) => [gs.readUInt16LE(a + O.X) >> 8, gs.readUInt16LE(a + O.Z) >> 8];
const fighter = (t) => t < 16 || (t >= 69 && t <= 76);
for (let t = 1; t <= to; t++) {
  for (const [i, s] of sides.entries()) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    const out = s.brain.think(t);
    for (const b of out.commands) for (const c of splitCommands(b)) {
      G.applyCommand(c.raw);
      if (i === 0 && c.type === T.ORDER) lastOrder.set(c.raw.readInt16LE(1), { t, code: c.raw[3] });
    }
  }
  G.step();
  if (t < from) continue;
  const max = i32(gs, GS.MAX_OBJ);
  const foes = [];
  for (let o = 120; o <= max; o++) { const a = objAddr(o); const l = u8(gs, a + O.LIFE); if (l && l !== 10 && u8(gs, a + O.TEAM) === sides[1].p && fighter(u8(gs, a + O.TYPE))) foes.push(tile(a)); }
  for (let o = 120; o <= max; o++) {
    const a = objAddr(o);
    const l = u8(gs, a + O.LIFE);
    if (!l || l === 10 || u8(gs, a + O.TEAM) !== us) continue;
    const ty = u8(gs, a + O.TYPE);
    if (!fighter(ty)) continue;
    let s = stat.get(o);
    if (!s) { s = { o, type: G.tables.types[ty].name, born: t, shots: 0, near: 0, burst: 0, states: new Map(), last: null }; stat.set(o, s); }
    const b = u8(gs, a + O.BURST);
    if (b > s.burst) s.shots += b - s.burst;
    s.burst = b;
    const [x, z] = tile(a);
    if (foes.some(([fx, fz]) => Math.max(Math.abs(fx - x), Math.abs(fz - z)) <= 6)) {
      s.near++;
      const sp = u8(gs, a + O.SP);
      const st = sp === 0xff ? -1 : u8(gs, a + O.STACK + 2 * sp);
      s.states.set(st, (s.states.get(st) ?? 0) + 1);
    }
    s.last = [x, z, t, i32(gs, a + O.HP)];
  }
}
for (const s of stat.values()) {
  const lo = lastOrder.get(s.o);
  console.log(`#${s.o} ${s.type.padEnd(6)} from ${s.born} last seen ${s.last[2]} at ${s.last[0]},${s.last[1]} hp ${s.last[3]} | shots ${s.shots}, ticks with a foe within 6: ${s.near}, states then ${JSON.stringify([...s.states])} | last order ${lo ? `${lo.code} @${lo.t}` : '-'}`);
}

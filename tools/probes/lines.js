// The candidate's chat lines matching a regex in arena jobs, plus turret builder positions every 500 ticks.
// node lines.mjs <root> <arena.json> <spec> <regex> <jobId>...
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, spec, re, ...ids] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands } = await imp('src/commands.js');
const { O, objAddr, u8, i32, GS } = await imp('src/engine/mem.js');
const all = JSON.parse(fs.readFileSync(jsonFile, 'utf8')).games;
const rx = new RegExp(re);
for (const id of ids) {
  const job = { ...all.find((g) => g.job.id === Number(id)).job, a: spec };
  const mapJson = loadMapJson(job.map);
  const specs = [A.parseSpec(job.a), A.parseSpec(job.b)];
  const rot = job.swap ? 1 : 0;
  const seatOf = specs.map((_, i) => job.seats[(rot + i) % 2]);
  const raceOf = specs.map((_, i) => job.races[(rot + i) % 2]);
  const slots = [];
  for (let s = 0; s < 8; s++) { const side = seatOf.indexOf(s); slots.push({ type: side >= 0 ? 2 : 3, race: side >= 0 ? raceOf[side] : 0, colour: s, team: s, name: side >= 0 ? `Bot${s}` : '' }); }
  const G = createGame(mapJson, { slots, localSlot: -1, titleDigit: mapJson.players ?? 8 }, { assert: () => {} });
  const sides = specs.map((sp, i) => ({ p: G.scenario.slotToPlayer[seatOf[i]], brain: A.makeBrain(sp, G, G.scenario.slotToPlayer[seatOf[i]], (job.seed + 17 * i) & 0xff) }));
  console.log(`== job ${id} ${job.map} vs ${job.b}`);
  for (let t = 1; t <= job.ticks; t++) {
    for (const [i, s] of sides.entries()) {
      if (t % 32 !== (4 + 4 * s.p) % 32) continue;
      const out = s.brain.think(t);
      if (i === 0) for (const l of out.lines) if (rx.test(l)) console.log(`  ${t} ${l}`);
      for (const b of out.commands) for (const c of splitCommands(b)) G.applyCommand(c.raw);
    }
    G.step();
    if (t % 500) continue;
    const gs = G.gs;
    const b = [];
    for (let o = 120; o <= i32(gs, GS.MAX_OBJ); o++) {
      const a = objAddr(o);
      const l = u8(gs, a + O.LIFE);
      if (!l || l === 10 || u8(gs, a + O.TEAM) !== sides[0].p) continue;
      const ty = u8(gs, a + O.TYPE);
      if ([1, 9, 41, 42].includes(ty)) b.push(`${ty === 1 || ty === 9 ? 'B' : 'T'}#${o}@${gs.readUInt16LE(a + O.X) >> 8},${gs.readUInt16LE(a + O.Z) >> 8}`);
    }
    if (b.length) console.log(`  @${t} turrets: ${b.join(' ')}`);
  }
}

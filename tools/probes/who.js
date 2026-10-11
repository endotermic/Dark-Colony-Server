// Our objects at tick T (type, tile, state) and the bot's order history for chosen objects; krusty task/minor of each.
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, jobIdS, TS, watchS] = process.argv.slice(2);
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
const watch = new Set((watchS ?? '').split(',').filter(Boolean).map(Number));
const gs = G.gs;
const tile = (a) => [gs.readUInt16LE(a + O.X) >> 8, gs.readUInt16LE(a + O.Z) >> 8];
let wp = null;
for (let t = 1; t <= Number(TS); t++) {
  for (const [i, s] of sides.entries()) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    for (const b of s.brain.think(t).commands) for (const c of splitCommands(b)) {
      G.applyCommand(c.raw);
      if (i !== 0) continue;
      if (c.type === T.WAYPOINTS_OBJ) wp = c.raw;
      if (c.type === T.ORDER && watch.has(c.raw.readInt16LE(1))) {
        const o = c.raw.readInt16LE(1);
        const [x, z] = tile(objAddr(o));
        console.log(`  @${t} #${o} at ${x},${z} order ${c.raw[3]} wp ${wp ? wp.toString('hex').slice(0, 40) : '-'}`);
      }
    }
  }
  G.step();
}
const max = i32(gs, GS.MAX_OBJ);
for (let o = 120; o <= max; o++) {
  const a = objAddr(o);
  const l = u8(gs, a + O.LIFE);
  if (!l || l === 10 || u8(gs, a + O.TEAM) !== sides[0].p) continue;
  const ty = u8(gs, a + O.TYPE);
  const sp = u8(gs, a + O.SP);
  console.log(`#${o} type ${ty} ${G.tables.types[ty].name} at ${tile(a)} state ${sp === 0xff ? '-' : u8(gs, a + O.STACK + 2 * sp)} hp ${i32(gs, a + O.HP)}`);
}

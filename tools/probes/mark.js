// At the candidate's think ticks: an enemy object's fire mark, and whether the bot holds it as seen / threat.
// node mark.mjs <root> <arena.json> <jobId> <enemyObj> <from> <to>
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS, objS, fromS, toS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands } = await imp('src/commands.js');
const { O, objAddr, u8 } = await imp('src/engine/mem.js');
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
const e = Number(objS);
for (let t = 1; t <= Number(toS); t++) {
  for (const [i, s] of sides.entries()) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    const before = u8(gs, objAddr(e) + O.ATTACKED);
    const out = s.brain.think(t);
    for (const b of out.commands) for (const c of splitCommands(b)) G.applyCommand(c.raw);
    if (i === 0 && t >= Number(fromS)) {
      const x = s.brain.aux.x;
      console.log(`@${t} #${e} mark ${before.toString(16)} (count ${before & 0x1f}, team ${before >> 5}) | seen ${x.seenFighters.some((r) => r.o === e)} threat ${x.threats.some((r) => r.o === e)} threats ${x.threats.length} danger ${x.danger}`);
    }
  }
  G.step();
}

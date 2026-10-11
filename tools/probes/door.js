// Who stands on the candidate's barracks spawn tile while its queue is not empty: node door.mjs <root> <arena.json> <jobId> [to]
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS, toS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands, T } = await imp('src/commands.js');
const { O, objAddr, u8, i32, P, playerAddr } = await imp('src/engine/mem.js');
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
const pa = playerAddr(sides[0].p);
const sp = G.consts.spawnOffsets.values;
const lastOrder = new Map();
let wp = null;
for (let t = 1; t <= Number(toS ?? 7000); t++) {
  for (const [i, s] of sides.entries()) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    for (const b of s.brain.think(t).commands) for (const c of splitCommands(b)) {
      G.applyCommand(c.raw);
      if (i !== 0) continue;
      if (c.type === T.WAYPOINTS_OBJ) wp = c.raw;
      if (c.type === T.ORDER) {
        const o = c.raw.readInt16LE(1);
        const n = wp ? wp.readUInt8(1) : 0;
        const pts = [];
        // waypoints frame: [type][nObjs?]... print raw head for reference
        lastOrder.set(o, `${c.raw[3]}@${t} wp ${wp ? wp.toString('hex').slice(0, 28) : '-'}${n ? '' : ''}${pts}`);
      }
    }
  }
  G.step();
  if (t % 128) continue;
  const len = gs.readUInt16LE(pa + P.QUEUE_LEN);
  if (!len) continue;
  const sx = i32(gs, pa + P.CITY_X) + sp[0];
  const sz = i32(gs, pa + P.CITY_Z) + sp[1];
  const cell = G.map.ground[sz * G.map.w + sx] & 0x3ff;
  let who = 'empty';
  if (cell === 0x3fe) who = 'RESERVED';
  else if (cell !== 0x3ff) {
    const a = objAddr(cell);
    const sp2 = u8(gs, a + O.SP);
    const stack = [];
    for (let k = 0; k <= sp2 && sp2 !== 0xff; k++) stack.push(u8(gs, a + O.STACK + 2 * k));
    who = `#${cell} ${G.tables.types[u8(gs, a + O.TYPE)].name} team ${u8(gs, a + O.TEAM)} (us ${sides[0].p}) stack [${stack}] nudge ${u8(gs, a + O.NUDGE)} last order ${lastOrder.get(cell) ?? '-'}`;
  }
  console.log(`@${t} queue ${len} spawn ${sx},${sz} (HQ ${i32(gs, pa + P.CITY_X)},${i32(gs, pa + P.CITY_Z)}): ${who}`);
}

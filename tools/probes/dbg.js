// One unit's situation while it sleeps beside an enemy: node dbg.mjs <root> <job.json> <obj> <to> [every]
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, objS, toS, everyS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands, T } = await imp('src/commands.js');
const { O, objAddr, u8, i32, GS, P, playerAddr } = await imp('src/engine/mem.js');
const job = JSON.parse(fs.readFileSync(jsonFile, 'utf8')).games[0].job;
const mapJson = loadMapJson(job.map);
const specs = [A.parseSpec(job.a), A.parseSpec(job.b)];
const rot = job.swap ? 1 : 0;
const seatOf = specs.map((_, i) => job.seats[(rot + i) % 2]);
const raceOf = specs.map((_, i) => job.races[(rot + i) % 2]);
const slots = [];
for (let s = 0; s < 8; s++) { const side = seatOf.indexOf(s); slots.push({ type: side >= 0 ? 2 : 3, race: side >= 0 ? raceOf[side] : 0, colour: s, team: s, name: side >= 0 ? `Bot${s}` : '' }); }
const G = createGame(mapJson, { slots, localSlot: -1, titleDigit: mapJson.players ?? 8 }, { assert: () => {} });
const sides = specs.map((spec, i) => ({ p: G.scenario.slotToPlayer[seatOf[i]], brain: A.makeBrain(spec, G, G.scenario.slotToPlayer[seatOf[i]], (job.seed + 17 * i) & 0xff) }));
const gs = G.gs;
const me = Number(objS);
const tile = (a) => [gs.readUInt16LE(a + O.X) >> 8, gs.readUInt16LE(a + O.Z) >> 8];
const seen = (x, z) => (G.map.ground[z * G.map.w + x] & i32(gs, playerAddr(sides[0].p) + P.VISION)) !== 0;
let last = '-';
let n = 0;
for (let t = 1; t <= Number(toS); t++) {
  for (const [i, s] of sides.entries()) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    for (const b of s.brain.think(t).commands) for (const c of splitCommands(b)) {
      G.applyCommand(c.raw);
      if (i === 0 && c.type === T.ORDER && c.raw.readInt16LE(1) === me) last = `${c.raw[3]}@${t}`;
    }
  }
  G.step();
  if (t % Number(everyS ?? 64)) continue;
  const a = objAddr(me);
  if (!u8(gs, a + O.LIFE) || u8(gs, a + O.TEAM) !== sides[0].p) continue;
  const sp = u8(gs, a + O.SP);
  const top = sp === 0xff ? -1 : u8(gs, a + O.STACK + 2 * sp);
  if (top !== 3 && top !== 1) continue;
  const [x, z] = tile(a);
  let best = null, bd = 99;
  const max = i32(gs, GS.MAX_OBJ);
  for (let o = 120; o <= max; o++) { const b = objAddr(o); const l = u8(gs, b + O.LIFE); if (!l || l === 10 || u8(gs, b + O.TEAM) !== sides[1].p) continue; const ty = u8(gs, b + O.TYPE); if (!(ty < 16 || (ty >= 69 && ty <= 76))) continue; const [fx, fz] = tile(b); const d = Math.max(Math.abs(fx - x), Math.abs(fz - z)); if (d < bd) { bd = d; best = { o, ty, fx, fz, name: G.tables.types[ty].name, fly: G.tables.types[ty].fly }; } }
  if (bd > 6) continue;
  const stack = []; for (let k = 0; k <= sp && sp !== 0xff; k++) stack.push(u8(gs, a + O.STACK + 2 * k));
  console.log(`@${t} #${me} at ${x},${z} stack [${stack}] last order ${last} | foe #${best.o} ${best.name} fly ${best.fly} at ${best.fx},${best.fz} d ${bd} seen ${seen(best.fx, best.fz)} life ${u8(gs, objAddr(best.o) + O.LIFE)}`);
  if (++n > 25) break;
}

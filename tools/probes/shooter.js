// Who damages an object: every tick its HP falls, list objects targeting it (O.TARGET), enemy objects marked as
// attacking our team, and live missiles near it. node shooter.mjs <root> <arena.json> <jobId> <obj> <from> <to>
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, idS, objS, fromS, toS] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands } = await imp('src/commands.js');
const { O, objAddr, u8, i32, i16, GS } = await imp('src/engine/mem.js');
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
const me = Number(objS);
const from = Number(fromS);
const to = Number(toS);
const tile = (o) => [gs.readUInt16LE(objAddr(o) + O.X) >> 8, gs.readUInt16LE(objAddr(o) + O.Z) >> 8];
let hp = null;
let n = 0;
for (let t = 1; t <= to && n < 12; t++) {
  for (const s of sides) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    for (const b of s.brain.think(t).commands) for (const c of splitCommands(b)) G.applyCommand(c.raw);
  }
  G.step();
  const h = i32(gs, objAddr(me) + O.HP);
  const dropped = hp !== null && h < hp;
  hp = h;
  if (t < from || !dropped) continue;
  n++;
  const m = tile(me);
  const who = [];
  for (let o = 0; o <= i32(gs, GS.MAX_OBJ); o++) {
    const a = objAddr(o);
    const l = u8(gs, a + O.LIFE);
    if (!l || l === 10 || o === me) continue;
    const p = tile(o);
    const d = Math.max(Math.abs(p[0] - m[0]), Math.abs(p[1] - m[1]));
    const targeting = i16(gs, a + O.TARGET) === me;
    const mark = u8(gs, a + O.ATTACKED);
    if (targeting || d <= 3 || ((mark & 0x1f) && (mark >> 5) === u8(gs, objAddr(me) + O.TEAM))) who.push(`${G.tables.types[u8(gs, a + O.TYPE)]?.name}#${o} team ${u8(gs, a + O.TEAM)} @${p} d${d}${targeting ? ' TARGETS IT' : ''} att ${u8(gs, a + O.ATTACKED).toString(16)}`);
  }
  console.log(`@${t} #${me} ${G.tables.types[u8(gs, objAddr(me) + O.TYPE)].name} team ${u8(gs, objAddr(me) + O.TEAM)} at ${m} hp -> ${h} | near/targeting: ${who.join(' | ') || '-'}`);
}

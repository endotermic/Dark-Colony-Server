// Orders that send our tanks (types 2, 10) home: target nearer the HQ than the unit by more than 5 tiles, with an enemy
// fighter within 10 tiles of the unit. node retreat.mjs <root> <job.json> [to]
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, toS] = process.argv.slice(2);
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
const sides = specs.map((sp, i) => ({ p: G.scenario.slotToPlayer[seatOf[i]], brain: A.makeBrain(sp, G, G.scenario.slotToPlayer[seatOf[i]], (job.seed + 17 * i) & 0xff) }));
const gs = G.gs;
const pa = playerAddr(sides[0].p);
const hq = () => [i32(gs, pa + P.CITY_X), i32(gs, pa + P.CITY_Z)];
const tile = (o) => [gs.readUInt16LE(objAddr(o) + O.X) >> 8, gs.readUInt16LE(objAddr(o) + O.Z) >> 8];
const cheb = (a, b) => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]));
const byDest = new Map();
let total = 0;
const samples = [];
for (let t = 1; t <= Number(toS ?? job.ticks); t++) {
  for (const [i, s] of sides.entries()) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    const out = s.brain.think(t);
    let wp = null;
    for (const b of out.commands) for (const c of splitCommands(b)) {
      if (i === 0 && c.type === T.WAYPOINTS_OBJ) {
        const n = c.raw[1];
        const no = c.raw.readInt16LE(2);
        const last = [c.raw.readInt16LE(4 + 4 * (n - 1)) >> 8, c.raw.readInt16LE(6 + 4 * (n - 1)) >> 8];
        const ids = [];
        for (let k = 0; k < no; k++) ids.push(c.raw.readInt16LE(4 + 4 * n + 2 * k));
        wp = { last, ids };
      }
      if (i === 0 && c.type === T.ORDER && wp) {
        const o = c.raw.readInt16LE(1);
        const ty = u8(gs, objAddr(o) + O.TYPE);
        if (ty === 10 || ty === 2) {
          const pos = tile(o);
          const h = hq();
          let near = 99;
          for (let e = 120; e <= i32(gs, GS.MAX_OBJ); e++) {
            const ea = objAddr(e);
            const l = u8(gs, ea + O.LIFE);
            if (!l || l === 10 || u8(gs, ea + O.TEAM) !== sides[1].p || u8(gs, ea + O.TYPE) >= 16) continue;
            near = Math.min(near, cheb(pos, tile(e)));
          }
          if (cheb(wp.last, h) < cheb(pos, h) - 10 && near <= 24) {
            total++;
            const key = `${wp.last} (order ${c.raw[3]}, ${wp.ids.length} units)`;
            byDest.set(key, (byDest.get(key) ?? 0) + 1);
            if (samples.length < 12) samples.push(`@${t} #${o} at ${pos} -> ${wp.last} order ${c.raw[3]} with ${wp.ids.length} units; enemy at ${near}; HQ ${h}`);
          }
        }
      }
      G.applyCommand(c.raw);
    }
  }
  G.step();
}
console.log(`orders sending a tank home (>10 tiles home) with an enemy within 24: ${total}`);
console.log([...byDest].sort((a, b) => b[1] - a[1]).slice(0, 10).map(([k, v]) => `  ${v}x to ${k}`).join('\n'));
console.log(samples.join('\n'));

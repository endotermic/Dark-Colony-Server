// Re-run one arena game (a job of a `tools/botarena.js --json` file) and write it as a relay recording (src/recorder.js
// format) that REPLAY ONLINE GAME plays to the patched exe - plan §19.15. Run from the repository root:
//
//   node tools/arena2replay.js . <arena.json> <jobId> <out.jsonl> [tickMs=33] [ourName] [opponentName]
//   node tools/replaycheck.js . <out.jsonl>          (re-runs the frames in the engine: must say 0 mismatches)
//
// then put the file in a folder, start a local relay with `RECORD_DIR=<folder> node src/index.js`, point the game's
// DEFAULT_SERVER.TXT at `address=localhost:8888 plain` and watch it through REPLAY ONLINE GAME (the only way in
// where the exe's viewer money fix runs; never REPLAY_FULL_MAP - the reveal alters the game). tickMs 33 = 200 %.
// The candidate (side a) is the recorded real player, so the replay screen offers its seat - except slot 0, the
// lobby host's seat, which no viewer may take (watch from the opponent's seat then).
// Frame k: UNTIL(k, k+LA+1) [0x08 of tick k+LA] [the commands the arena applied before the step to k+LA+1]; the client
// (like the engine, synccheck.advance) runs to until-1, executes the commands, then steps: the arena's own timing.
// The recording ends 1500 ticks after the candidate's HQ falls, else at the job's tick cap.
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, jobIdS, outFile, tickMsS, ourName, oppName] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const A = await imp('src/arena.js');
const { createGame, loadMapJson } = await imp('src/engine/index.js');
const { splitCommands, build, T } = await imp('src/commands.js');
const { O, objAddr, u8, i32, GS, P, playerAddr } = await imp('src/engine/mem.js');
const LA = 2;
const TICK_MS = Number(tickMsS ?? 33);
const BUDGET = 1012;
const job = JSON.parse(fs.readFileSync(jsonFile, 'utf8')).games.find((g) => g.job.id === Number(jobIdS)).job;
const mapJson = loadMapJson(job.map);
const specs = [A.parseSpec(job.a), A.parseSpec(job.b)];
const rot = job.swap ? 1 : 0;
const seatOf = specs.map((_, i) => job.seats[(rot + i) % 2]);
const raceOf = specs.map((_, i) => job.races[(rot + i) % 2]);
const slots = [];
for (let s = 0; s < 8; s++) {
  const side = seatOf.indexOf(s);
  slots.push({ type: side >= 0 ? 2 : 3, race: side >= 0 ? raceOf[side] : 0, colour: s, team: s, name: side >= 0 ? `Bot${s}` : '' });
}
const lobby = { slots, localSlot: -1, titleDigit: mapJson.players ?? 8 };
const G = createGame(mapJson, lobby, { assert: () => {} });
const sides = specs.map((spec, i) => ({ p: G.scenario.slotToPlayer[seatOf[i]], brain: A.makeBrain(spec, G, G.scenario.slotToPlayer[seatOf[i]], (job.seed + 17 * i) & 0xff) }));
const cmdsAt = new Map(); // t -> [raw...] applied before the step to t
const sums = [0];
let end = job.ticks;
const lines = [];
for (let t = 1; t <= job.ticks; t++) {
  for (const [i, s] of sides.entries()) {
    if (t % 32 !== (4 + 4 * s.p) % 32) continue;
    const out = s.brain.think(t);
    if (i === 0) for (const l of out.lines) if (/Armour|Weapon|Danger|danger is over|Expansion|explorer|mine/i.test(l)) lines.push(`${t} ${l}`);
    for (const buf of out.commands) {
      for (const c of splitCommands(buf)) {
        if (!cmdsAt.has(t)) cmdsAt.set(t, []);
        cmdsAt.get(t).push(c.raw);
        G.applyCommand(c.raw);
      }
    }
  }
  sums.push(G.step());
  if (t % 64 === 0 && i32(G.gs, playerAddr(sides[0].p) + P.SLOT_HP) === 0 && end === job.ticks) {
    end = Math.min(job.ticks, t + 1500); // the HQ fell: half a minute more, then stop
    lines.push(`${t} OUR HQ IS DOWN`);
  }
  if (t >= end) break;
}
if (cmdsAt.has(1) || cmdsAt.has(2)) throw new Error('commands before tick LA+1');
const map = JSON.parse(fs.readFileSync(path.join(root, 'maps/index.json'), 'utf8')).maps.find((m) => m.json === `${job.map}.json`);
const rec = [];
// the names the replay screen shows (display only: the simulation does not read them)
if (ourName) slots[seatOf[0]].name = ourName;
if (oppName) slots[seatOf[1]].name = oppName;
rec.push({ type: 'start', version: 1, t: new Date().toISOString(), map: { file: map.file, name: map.name, terrain: map.terrain, players: map.players }, tickMs: TICK_MS, lookahead: LA, syncCheck: 'send', mercenarySlot: 0, lobby, players: [{ slot: seatOf[0], name: slots[seatOf[0]].name }], note: `arena job ${job.id}: ${job.a} (seat ${seatOf[0]}) vs ${job.b} (seat ${seatOf[1]})` });
let over = 0;
for (let k = 0; k + LA + 1 <= end; k++) {
  const parts = [];
  if (k === 0) parts.push(build.tickSpeed(TICK_MS));
  else parts.push(build.sync(sums[k + LA], k + LA));
  for (const raw of cmdsAt.get(k + LA + 1) ?? []) parts.push(raw);
  const buf = Buffer.concat(parts);
  if (buf.length > BUDGET) over++;
  rec.push({ type: 'frame', a: k, until: k + LA + 1, cmds: buf.toString('hex') });
}
rec.push({ type: 'end', reason: 'arena export', engineTime: end });
fs.writeFileSync(outFile, rec.map((l) => JSON.stringify(l)).join('\n') + '\n');
console.log(`map ${map.file} ${map.name}; us seat ${seatOf[0]} race ${raceOf[0]} p${sides[0].p}, rusher seat ${seatOf[1]} race ${raceOf[1]}; ${rec.length - 2} frames to tick ${end}; frames over budget ${over}`);
console.log(lines.join('\n'));

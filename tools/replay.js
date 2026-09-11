// Replay a recorded battle (RECORD_DIR, src/recorder.js) through the battle engine and compare the
// engine's checksums with the ones the clients sent (0x08, present when MERCENARY_SLOT > 0 made a
// real player the lowest network id). This is the development loop of the engine port: play a
// game with `SYNC_CHECK=shadow RECORD_DIR=logs/replays MERCENARY_SLOT=7`, then
//
//   node tools/replay.js logs/replays/<file>.jsonl [--until TICK] [--maps DIR] [--verbose]
//
// prints the first tick where the engine and the clients disagree, the number of compared ticks and
// the engine's asserts. Exit code 1 on a mismatch or an engine error, 2 when the recording has no
// client checksums to compare with.

import path from 'node:path';
import { readRecording } from '../src/recorder.js';
import { splitCommands, T } from '../src/commands.js';
import { createGame, loadMapJson, MAPS_DIR } from '../src/engine/index.js';

function parseArgs(argv) {
  const opts = { file: null, until: Infinity, maps: MAPS_DIR, verbose: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--until') opts.until = Number(argv[++i]);
    else if (a === '--maps') opts.maps = path.resolve(argv[++i]);
    else if (a === '--verbose') opts.verbose = true;
    else if (!opts.file) opts.file = a;
    else throw new Error(`unexpected argument ${a}`);
  }
  if (!opts.file) throw new Error('usage: node tools/replay.js <recording.jsonl> [--until TICK] [--maps DIR] [--verbose]');
  return opts;
}

/**
 * Run a recording. Returns { engineTime, compared, mismatches, firstMismatch, asserts, error }.
 * `lines` = parsed JSON lines; `opts.log` optional.
 */
export function replay(lines, opts = {}) {
  const start = lines.find((l) => l.type === 'start');
  if (!start) throw new Error('no start line');
  const mapJson = loadMapJson(start.map.file, opts.maps);
  if (!mapJson) throw new Error(`no map JSON for ${start.map.file} in ${opts.maps ?? MAPS_DIR}`);
  const asserts = [];
  const G = createGame(mapJson, start.lobby, { assert: (msg, g) => asserts.push({ tick: g.tick, msg }) });
  const rx = new Map(); // tick -> { checksum, slot } from clients (first one wins)
  for (const l of lines) if (l.type === 'rx08' && !rx.has(l.tick)) rx.set(l.tick, l);
  const result = { engineTime: 0, compared: 0, mismatches: 0, firstMismatch: null, asserts, error: null, frames: 0 };
  const limit = opts.until ?? Infinity;
  let time = 0;
  const check = () => {
    const mine = G.historyAt(time);
    const theirs = rx.get(time);
    if (opts.verbose) opts.log?.(`tick ${time}: engine ${mine}${theirs ? ` client ${theirs.checksum}` : ''}`);
    if (!theirs) return;
    result.compared++;
    if (theirs.checksum !== mine) {
      result.mismatches++;
      if (!result.firstMismatch) result.firstMismatch = { tick: time, engine: mine, client: theirs.checksum, slot: theirs.slot };
    }
  };
  try {
    for (const l of lines) {
      if (l.type !== 'frame') continue;
      result.frames++;
      const cmds = splitCommands(Buffer.from(l.cmds, 'hex')).map((c) => c.raw);
      while (time < l.until - 1 && time < limit) {
        G.step();
        time++;
        check();
      }
      if (time >= limit) break;
      for (const raw of cmds) {
        const t = raw[0];
        if (t === T.UNTIL || t === T.SYNC || t === T.TICK) continue;
        G.applyCommand(raw);
      }
      G.step();
      time++;
      check();
      if (result.firstMismatch && opts.stopAtMismatch) break;
    }
  } catch (err) {
    result.error = { tick: time, message: err.stack ?? String(err) };
  }
  result.engineTime = time;
  return result;
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'));

if (isMain) {
  const opts = parseArgs(process.argv.slice(2));
  const lines = readRecording(opts.file);
  const start = lines.find((l) => l.type === 'start');
  console.log(`map ${start?.map?.file} (${start?.map?.name}), tick ${start?.tickMs} ms, ${lines.filter((l) => l.type === 'frame').length} frames, ${lines.filter((l) => l.type === 'rx08').length} client checksums`);
  const res = replay(lines, { ...opts, log: console.log, stopAtMismatch: true });
  console.log(`engine ran to tick ${res.engineTime} over ${res.frames} frames; compared ${res.compared} checksums, ${res.mismatches} mismatches`);
  if (res.asserts.length) console.log(`engine asserts: ${res.asserts.length}, first: tick ${res.asserts[0].tick} ${res.asserts[0].msg}`);
  if (res.firstMismatch) console.log(`FIRST MISMATCH at tick ${res.firstMismatch.tick}: engine ${res.firstMismatch.engine}, client (slot ${res.firstMismatch.slot}) ${res.firstMismatch.client}`);
  if (res.error) console.log(`ENGINE ERROR at tick ${res.error.tick}: ${res.error.message}`);
  process.exit(res.error || res.mismatches ? 1 : res.compared === 0 ? 2 : 0);
}

#!/usr/bin/env node
// The bot arena on the command line (7 Oct 2026; src/arena.js has the rules; plan §19.12): a candidate
// brain against a pool of opponents, paired games with the seats swapped, spread over worker threads.
//
//   node tools/botarena.js --a krusty+workers --b krusty,rusher [--maps J8PLAY01,D8PLAY01] [--pairs 10]
//                          [--ticks 6818] [--jobs N] [--seed 1] [--ffa N] [--json out.json] [--quiet]
//
// `--pairs N` plays N pairs (2N games) per map per opponent; the default cap is five minutes of game time
// and a capped game is decided on points (src/arena.js); `--ffa N` plays free-for-alls of N sides
// (the candidate and N-1 pool members, every seating rotated) instead. A brain spec is `krusty`, `rusher` or
// `krusty+<switch>+<switch>=<value>` with the krusty.js VARIANTS. The default pool is the server's bot
// (`krusty`) and the rusher; the default maps are every map in maps/index.json that has a JSON.
// Prints progress to stderr, the table to stdout; `--json` keeps every game's result.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Worker, isMainThread, parentPort } from 'node:worker_threads';
import { runGame, makeJobs, aggregate, formatTable, parseSpec, parsePool, DEFAULT_TICKS } from '../src/arena.js';
import { MAPS_DIR } from '../src/engine/index.js';

if (!isMainThread) {
  parentPort.on('message', (job) => {
    try {
      parentPort.postMessage({ job, r: runGame(job) });
    } catch (err) {
      parentPort.postMessage({ job, error: err.stack ?? String(err) });
    }
  });
} else {
  main().catch((err) => {
    console.error(err.stack ?? String(err));
    process.exit(2);
  });
}

function parseArgs(argv) {
  const o = { a: 'krusty+workers', b: 'krusty,rusher', maps: null, pairs: 10, ticks: DEFAULT_TICKS, jobs: Math.max(1, os.cpus().length - 1), seed: 1, ffa: 0, json: null, quiet: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const v = () => argv[++i];
    if (k === '--a') o.a = v();
    else if (k === '--b') o.b = v();
    else if (k === '--maps') o.maps = v().split(',').map((x) => x.trim()).filter(Boolean);
    else if (k === '--pairs') o.pairs = Number(v());
    else if (k === '--ticks') o.ticks = Number(v());
    else if (k === '--jobs') o.jobs = Number(v());
    else if (k === '--seed') o.seed = Number(v());
    else if (k === '--ffa') o.ffa = Number(v());
    else if (k === '--json') o.json = v();
    else if (k === '--quiet') o.quiet = true;
    else throw new Error(`unknown argument ${k}`);
  }
  return o;
}

function allMaps() {
  const index = JSON.parse(fs.readFileSync(path.join(MAPS_DIR, 'index.json'), 'utf8'));
  return index.maps.filter((m) => m.json && fs.existsSync(path.join(MAPS_DIR, m.json))).map((m) => ({ name: path.basename(m.json, '.json'), players: m.players }));
}

async function main() {
  const o = parseArgs(process.argv.slice(2));
  const a = parseSpec(o.a);
  const pool = parsePool(o.b);
  const known = allMaps();
  const maps = o.maps ? o.maps.map((n) => known.find((m) => m.name === n) ?? (() => { throw new Error(`no maps/${n}.json`); })()) : known;
  const jobs = makeJobs({ maps, pool, a, pairs: o.pairs, ticks: o.ticks, seed: o.seed, ffa: o.ffa });
  const workers = Math.min(o.jobs, jobs.length);
  console.error(`${jobs.length} games: ${a.name} vs ${pool.map((p) => p.name).join(', ')} on ${maps.map((m) => m.name).join(', ')}, ${o.ticks} ticks each, ${workers} threads`);
  const t0 = Date.now();
  const results = [];
  let next = 0;
  let done = 0;
  const threads = [];
  await new Promise((resolve, reject) => {
    const hand = (w) => {
      if (next >= jobs.length) return; // idle until everybody is done; terminated below
      w.postMessage(jobs[next++]);
    };
    for (let i = 0; i < workers; i++) {
      const w = new Worker(new URL(import.meta.url));
      threads.push(w);
      w.on('message', (m) => {
        if (m.error) {
          reject(new Error(`game ${m.job.id} (${m.job.map} seats ${m.job.seats} seed ${m.job.seed}): ${m.error}`));
          return;
        }
        results.push(m);
        done++;
        if (!o.quiet) {
          const r = m.r;
          const who = r.winner === 'draw' ? 'draw' : `${r.winner === 'a' ? m.job.a : m.job.b} wins at tick ${r.ticks}`;
          const layout = m.job.rot !== undefined ? ` rot ${m.job.rot}` : m.job.swap ? ' swapped' : '';
          console.error(`[${done}/${jobs.length}] ${m.job.map} seats ${m.job.seats.join('/')}${layout} races ${m.job.races.join('/')}: ${who} (${(r.ms / 1000).toFixed(1)} s)`);
        }
        if (done === jobs.length) resolve();
        else hand(w);
      });
      w.on('error', reject);
      hand(w);
    }
  }).finally(() => {
    for (const w of threads) w.terminate(); // the threads would otherwise keep the process alive
  });
  const agg = aggregate(results);
  console.log(formatTable(agg, a.name));
  console.log(`${results.length} games in ${((Date.now() - t0) / 1000).toFixed(0)} s wall time`);
  if (o.json) {
    fs.writeFileSync(o.json, JSON.stringify({ args: o, candidate: a.name, pool: pool.map((p) => p.name), maps, aggregate: agg, games: results }, null, 1));
    console.log(`results written to ${o.json}`);
  }
}

// The bot arena (7 Oct 2026, maintainer: "build the arena tool and run the economy change"; plan §19.12):
// headless games of one bot brain against a pool of others on the server engine, paired so that every
// game is also played with the seats swapped, and summarised as win rates with confidence intervals.
//
// A game is deterministic in (map, seats, races, seed): the engine's RNG index is the game state's, the
// brains draw from private seeds derived from `seed`, and the brains' commands are applied to the
// engine at once (the relay applies them one sync frame later, a difference of one or two ticks). The
// brains think at the original AI's phase, every 32 ticks at tick 4 + 4p (DC16_AI.md §2).
//
// Win: the other side is dead - its HQ slot is gone and it has no armed mobile unit left (a Krusty without
// an HQ and under 2000 money waits for money forever, so "no objects at all" would never come). A game
// that reaches the tick cap is decided ON POINTS (maintainer, 7 Oct 2026: "5 min of test play is
// enough"): HQ standing 10, each fighter 1, each mine 3; equal points are a draw. The default cap is
// five minutes of game time (DEFAULT_TICKS at 44 ms); `decidedBy` says 'kill' or 'points'.
//
// Brain spec strings: `krusty` (the server's bot: bot mode, FIXES on, no variant), `krusty+workers+vents=zone`
// (krusty.js VARIANTS after `+`), `rusher` (src/rusher.js). A pool is specs separated by commas.
// tools/botarena.js is the command line and spreads the games over worker threads.
//
// Free-for-all (`ffa` >= 3 in makeJobs, `--ffa N`): the candidate and N-1 pool members (the pool cycled)
// in one game, every seating rotated through the N seats (N games per layout). The candidate wins when
// it is the last side alive, loses when it dies, draws at the tick cap. Its opponents are one key in
// the aggregate (names joined with " & "); the `b` metrics are the first opponent's.

import { createGame, loadMapJson } from './engine/index.js';
import { KrustyBot } from './krustybot.js';
import { Rusher } from './rusher.js';
import { splitCommands, T } from './commands.js';
import * as Krusty from './engine/krusty.js';
import { GS, O, P, objAddr, playerAddr, u8, i32 } from './engine/mem.js';

export const DEFAULT_TICKS = 6818; // five minutes of game time at 44 ms (maintainer, 7 Oct 2026)
export const POINTS = Object.freeze({ hq: 10, fighter: 1, mine: 3 }); // the decision at the tick cap
export const CHECK_EVERY = 64; // ticks between death checks and metric samples
const THINK_TICKS = 32;
/** The race pairs a series cycles through: (A's race, B's race). */
export const RACES = Object.freeze([[0, 0], [1, 1], [0, 1], [1, 0]]);

// ---- brain specs -----------------------------------------------------------------------------------

/** `krusty`, `krusty+workers+vents=zone`, `rusher` -> { kind, variant, name }. */
export function parseSpec(spec) {
  const s = String(spec ?? '').trim();
  const [kind, ...opts] = s.split('+').map((x) => x.trim());
  if (kind === 'rusher') {
    if (opts.length) throw new Error(`rusher takes no options: ${s}`);
    return { kind, variant: '', name: 'rusher' };
  }
  if (kind !== 'krusty') throw new Error(`unknown brain ${kind} (krusty or rusher)`);
  const variant = opts.filter(Boolean).join(',');
  Krusty.parseVariant(variant); // throws on an unknown switch
  return { kind, variant, name: variant ? `krusty+${variant.replace(/,/g, '+')}` : 'krusty' };
}

/** A comma-separated list of specs. */
export function parsePool(s) {
  return String(s)
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
    .map(parseSpec);
}

/** The brain object of a spec for game player p; `seed` is the private RNG seed. */
export function makeBrain(spec, G, p, seed) {
  if (spec.kind === 'rusher') return new Rusher(G, p, { isAlly: () => false, nameOf: (q) => `player ${q}` });
  return new KrustyBot(G, p, { seed, variant: spec.variant });
}

// ---- one game --------------------------------------------------------------------------------------

const aliveLife = (life) => life !== 0 && life !== 10;

/** Per-player snapshot: mines, armed mobile units, all units. */
function census(G, p) {
  const gs = G.gs;
  let mines = 0;
  let fighters = 0;
  let units = 0;
  let towers = 0;
  const maxObj = i32(gs, GS.MAX_OBJ);
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    if (!aliveLife(u8(gs, a + O.LIFE)) || u8(gs, a + O.TEAM) !== p) continue;
    const ty = u8(gs, a + O.TYPE);
    units++;
    if (ty === 0x2f || ty === 0x30) mines++;
    else if (ty === 41 || ty === 42) towers++;
    else if (ty < 16 && G.tables.types[ty].weapon[0] !== -1) fighters++;
  }
  return { mines, fighters, units, towers };
}

const hqAlive = (G, p) => i32(G.gs, playerAddr(p) + P.SLOT_HP) !== 0;

/**
 * Run one game. `job` = { map, seats: [sA, sB], races: [rA, rB], seed, a, b, ticks, swap }.
 * `swap` puts brain b into seat sA with race rA and brain a into the other seat (the mirrored game of a pair).
 * Returns { winner: 'a' | 'b' | 'draw', ticks, a: metrics, b: metrics, asserts, botAsserts, ms }.
 */
export function runGame(job) {
  const mapJson = typeof job.map === 'string' ? loadMapJson(job.map) : job.map;
  if (!mapJson) throw new Error(`no map JSON for ${job.map}`);
  const toSpec = (x) => (typeof x === 'string' ? parseSpec(x) : x);
  const opponents = typeof job.b === 'string' ? job.b.split(' & ').map(toSpec) : [toSpec(job.b)];
  const specs = [toSpec(job.a), ...opponents];
  const n = specs.length;
  if (job.seats.length < n) throw new Error(`${n} sides need ${n} seats`);
  const ticks = job.ticks ?? DEFAULT_TICKS;
  // side i sits at seats[(rot + i) % n]: rot 1 of two sides is the seat-swapped twin, a free-for-all rotates 0..n-1
  const rot = job.rot ?? (job.swap ? 1 : 0);
  const seatOf = specs.map((_, i) => job.seats[(rot + i) % n]);
  const raceOf = specs.map((_, i) => job.races[(rot + i) % n]);
  const slots = [];
  for (let s = 0; s < 8; s++) {
    const side = seatOf.indexOf(s);
    slots.push({ type: side >= 0 ? 2 : 3, race: side >= 0 ? raceOf[side] : 0, colour: s, team: s, name: side >= 0 ? `Bot${s}` : '' });
  }
  const asserts = [];
  const G = createGame(mapJson, { slots, localSlot: -1, titleDigit: mapJson.players ?? 8 }, { assert: (msg, g) => asserts.push({ tick: g.tick, msg }) });
  const sides = specs.map((spec, i) => {
    const p = G.scenario.slotToPlayer[seatOf[i]];
    return { side: i === 0 ? 'a' : 'b', i, p, brain: makeBrain(spec, G, p, (job.seed + 17 * i) & 0xff), trained: 0, orders: 0, peakMines: 0, peakFighters: 0, moneySum: 0, moneySamples: 0, dead: false };
  });
  const t0 = Date.now();
  let winner = 'draw';
  let decidedBy = null;
  let endTick = ticks;
  for (let t = 1; t <= ticks; t++) {
    for (const s of sides) {
      if (t % THINK_TICKS !== (4 + 4 * s.p) % THINK_TICKS) continue;
      const out = s.brain.think(t);
      for (const buf of out.commands) {
        for (const c of splitCommands(buf)) {
          if (c.type === T.BUILD) s.trained += c.raw[3];
          else if (c.type === T.WAYPOINTS_OBJ) s.orders++;
          G.applyCommand(c.raw);
        }
      }
    }
    G.step();
    if (t % CHECK_EVERY !== 0) continue;
    for (const s of sides) {
      if (s.dead) continue;
      const c = census(G, s.p);
      s.last = c;
      if (c.mines > s.peakMines) s.peakMines = c.mines;
      if (c.fighters > s.peakFighters) s.peakFighters = c.fighters;
      s.moneySum += i32(G.gs, playerAddr(s.p) + P.MONEY);
      s.moneySamples++;
      if (!hqAlive(G, s.p) && c.fighters === 0) {
        s.dead = true;
        s.diedAt = t;
      }
    }
    const aDead = sides[0].dead;
    const othersDead = sides.slice(1).every((s) => s.dead);
    if (aDead || othersDead) {
      winner = aDead ? (othersDead && n === 2 && sides[1].diedAt === t ? 'draw' : 'b') : 'a';
      decidedBy = 'kill';
      endTick = t;
      break;
    }
  }
  if (!decidedBy) {
    // the cap: on points
    const points = (s) => {
      const c = s.last ?? census(G, s.p);
      return (hqAlive(G, s.p) ? POINTS.hq : 0) + c.fighters * POINTS.fighter + c.mines * POINTS.mine;
    };
    const pa = points(sides[0]);
    const best = Math.max(...sides.slice(1).map(points));
    if (pa !== best) {
      winner = pa > best ? 'a' : 'b';
      decidedBy = 'points';
    }
  }
  const metrics = (s) => {
    const c = s.last ?? census(G, s.p);
    return {
      spec: specs[s.i].name,
      player: s.p,
      seat: seatOf[s.i],
      race: raceOf[s.i],
      dead: s.dead,
      diedAt: s.diedAt ?? null,
      income: G.stat(1, s.p),
      trained: s.trained,
      orders: s.orders,
      peakMines: s.peakMines,
      peakFighters: s.peakFighters,
      mines: c.mines,
      fighters: c.fighters,
      units: c.units,
      towers: c.towers,
      hq: hqAlive(G, s.p),
      money: i32(G.gs, playerAddr(s.p) + P.MONEY),
      meanMoney: s.moneySamples ? Math.round(s.moneySum / s.moneySamples) : 0,
      botAsserts: s.brain.asserts?.length ?? 0,
    };
  };
  const others = sides.slice(1).map(metrics);
  return { winner, decidedBy, ticks: endTick, a: metrics(sides[0]), b: others[0], others, asserts: asserts.slice(0, 5), assertCount: asserts.length, ms: Date.now() - t0 };
}

// ---- series ----------------------------------------------------------------------------------------

/** mulberry32: a small seeded RNG for the series layout (not the game's RNG). */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * The jobs of a series: for every pair index, map and pool member a random seat pair and the cycling
 * race pair, as two games (the second with the seats swapped). `maps` = [{ name, players }].
 */
export function makeJobs({ maps, pool, a, pairs = 10, ticks = DEFAULT_TICKS, seed = 1, ffa = 0 }) {
  const rng = mulberry32(seed);
  const jobs = [];
  let n = 0;
  const pickSeats = (players, k) => {
    const all = [...Array(players).keys()];
    const out = [];
    for (let j = 0; j < k; j++) out.push(all.splice(Math.floor(rng() * all.length), 1)[0]);
    return out;
  };
  for (let i = 0; i < pairs; i++) {
    for (const m of maps) {
      const players = m.players ?? 8;
      if (ffa >= 3) {
        if (ffa > players) throw new Error(`${ffa} sides on a ${players}-player map`);
        const opp = [...Array(ffa - 1).keys()].map((j) => pool[j % pool.length]);
        const seats = pickSeats(players, ffa);
        const races = [...Array(ffa).keys()].map((j) => RACES[i % RACES.length][j % 2]);
        const gameSeed = Math.floor(rng() * 0x7fffffff);
        for (let rot = 0; rot < ffa; rot++) jobs.push({ id: n++, pair: i, map: m.name, seats, races, seed: gameSeed, a: a.name, b: opp.map((o) => o.name).join(' & '), ticks, rot });
        continue;
      }
      for (const b of pool) {
        const [sA, sB] = pickSeats(players, 2);
        const races = RACES[i % RACES.length];
        const gameSeed = Math.floor(rng() * 0x7fffffff);
        for (const swap of [false, true]) jobs.push({ id: n++, pair: i, map: m.name, seats: [sA, sB], races, seed: gameSeed, a: a.name, b: b.name, ticks, swap });
      }
    }
  }
  return jobs;
}

/** Wilson score interval of k successes in n trials (95 %). */
export function wilson(k, n) {
  if (n === 0) return [0, 0];
  const z = 1.96;
  const p = k / n;
  const d = 1 + (z * z) / n;
  const c = p + (z * z) / (2 * n);
  const h = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  return [(c - h) / d, (c + h) / d];
}

/** Aggregate results per opponent spec (and per map inside). */
export function aggregate(results) {
  const by = new Map();
  const mean = (xs) => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : 0);
  const bucket = (key) => {
    if (!by.has(key)) by.set(key, { key, games: 0, wins: 0, losses: 0, draws: 0, winsPoints: 0, lossesPoints: 0, winTicks: [], lossTicks: [], a: [], b: [], asserts: 0, botAsserts: 0, ms: 0, maps: new Map() });
    return by.get(key);
  };
  const add = (bk, r) => {
    bk.games++;
    if (r.winner === 'a') {
      bk.wins++;
      if (r.decidedBy === 'points') bk.winsPoints++;
      else bk.winTicks.push(r.ticks);
    } else if (r.winner === 'b') {
      bk.losses++;
      if (r.decidedBy === 'points') bk.lossesPoints++;
      else bk.lossTicks.push(r.ticks);
    } else bk.draws++;
    bk.a.push(r.a);
    bk.b.push(r.b);
    bk.asserts += r.assertCount;
    bk.botAsserts += r.a.botAsserts + r.b.botAsserts;
    bk.ms += r.ms;
  };
  for (const { job, r } of results) {
    const bk = bucket(job.b);
    add(bk, r);
    if (!bk.maps.has(job.map)) bk.maps.set(job.map, { games: 0, wins: 0, losses: 0, draws: 0 });
    const mb = bk.maps.get(job.map);
    mb.games++;
    if (r.winner === 'a') mb.wins++;
    else if (r.winner === 'b') mb.losses++;
    else mb.draws++;
  }
  const side = (xs) => ({
    income: Math.round(mean(xs.map((x) => x.income))),
    peakMines: +mean(xs.map((x) => x.peakMines)).toFixed(2),
    peakFighters: +mean(xs.map((x) => x.peakFighters)).toFixed(1),
    trained: +mean(xs.map((x) => x.trained)).toFixed(1),
    meanMoney: Math.round(mean(xs.map((x) => x.meanMoney))),
  });
  return [...by.values()].map((bk) => {
    const [lo, hi] = wilson(bk.wins, bk.games);
    const [dlo, dhi] = wilson(bk.wins, bk.wins + bk.losses);
    return {
      opponent: bk.key,
      games: bk.games,
      wins: bk.wins,
      losses: bk.losses,
      draws: bk.draws,
      winsPoints: bk.winsPoints,
      lossesPoints: bk.lossesPoints,
      winRate: bk.games ? bk.wins / bk.games : 0,
      winRateCI: [lo, hi],
      decidedRate: bk.wins + bk.losses ? bk.wins / (bk.wins + bk.losses) : 0,
      decidedCI: [dlo, dhi],
      meanWinTicks: Math.round(mean(bk.winTicks)),
      meanLossTicks: Math.round(mean(bk.lossTicks)),
      a: side(bk.a),
      b: side(bk.b),
      asserts: bk.asserts,
      botAsserts: bk.botAsserts,
      seconds: +(bk.ms / 1000).toFixed(1),
      maps: [...bk.maps.entries()].map(([map, v]) => ({ map, ...v })),
    };
  });
}

const pct = (x) => `${(100 * x).toFixed(0)}%`;

/** A text table of an aggregate for the terminal. */
export function formatTable(agg, aName) {
  const lines = [];
  for (const r of agg) {
    lines.push(`${aName} vs ${r.opponent}: ${r.games} games, ${r.wins} wins (${r.winsPoints} on points) / ${r.losses} losses (${r.lossesPoints} on points) / ${r.draws} draws`);
    lines.push(`  win rate ${pct(r.winRate)} (95% ${pct(r.winRateCI[0])}..${pct(r.winRateCI[1])}), of decided games ${pct(r.decidedRate)} (${pct(r.decidedCI[0])}..${pct(r.decidedCI[1])})`);
    lines.push(`  mean ticks to a kill ${r.meanWinTicks}, to a death ${r.meanLossTicks}; engine asserts ${r.asserts}, bot asserts ${r.botAsserts}; ${r.seconds} s of game time`);
    lines.push(`  ${aName.padEnd(28)} income ${String(r.a.income).padStart(6)}  peak mines ${r.a.peakMines}  peak fighters ${r.a.peakFighters}  trained ${r.a.trained}  mean money ${r.a.meanMoney}`);
    lines.push(`  ${r.opponent.padEnd(28)} income ${String(r.b.income).padStart(6)}  peak mines ${r.b.peakMines}  peak fighters ${r.b.peakFighters}  trained ${r.b.trained}  mean money ${r.b.meanMoney}`);
    for (const m of r.maps) lines.push(`    ${m.map.padEnd(10)} ${m.games} games: ${m.wins} / ${m.losses} / ${m.draws}`);
  }
  return lines.join('\n');
}

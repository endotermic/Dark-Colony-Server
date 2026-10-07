// The bot arena (src/arena.js) and the krusty.js VARIANTS it compares (7 Oct 2026, plan §19.12).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseSpec, parsePool, makeJobs, runGame, aggregate, wilson, RACES } from '../src/arena.js';
import { parseVariant } from '../src/engine/krusty.js';
import { KrustyBot } from '../src/krustybot.js';
import { createGame, loadMapJson } from '../src/engine/index.js';

test('variant specs: krusty.js switches parse, unknown ones throw', () => {
  assert.deepEqual(parseVariant(''), {});
  assert.deepEqual(parseVariant('workers'), { workers: true });
  assert.deepEqual(parseVariant('workers,vents=zone'), { workers: true, vents: 'zone' });
  assert.deepEqual(parseVariant('workers+vents=none'), { workers: true, vents: 'none' });
  assert.deepEqual(parseVariant({ workers: true, vents: 'route' }), { workers: true, vents: 'route' });
  assert.throws(() => parseVariant('towers'), /unknown variant switch/);
  assert.throws(() => parseVariant('vents=sky'), /route, zone or none/);
  assert.deepEqual(parseSpec('krusty'), { kind: 'krusty', variant: '', name: 'krusty' });
  assert.deepEqual(parseSpec('krusty+workers+vents=zone'), { kind: 'krusty', variant: 'workers,vents=zone', name: 'krusty+workers+vents=zone' });
  assert.deepEqual(parseSpec('rusher'), { kind: 'rusher', variant: '', name: 'rusher' });
  assert.throws(() => parseSpec('rusher+workers'), /no options/);
  assert.throws(() => parseSpec('gandalf'), /unknown brain/);
  assert.deepEqual(parsePool('krusty, rusher').map((s) => s.name), ['krusty', 'rusher']);
});

test('the bot carries its variant into the think and the summary', () => {
  const G = createGame(loadMapJson('D8PLAY01'), { slots: [...Array(8)].map((_, s) => ({ type: s === 0 ? 2 : 3, race: 0, colour: s, team: s, name: s === 0 ? 'P0' : '' })), localSlot: -1, titleDigit: 8 }, {});
  const bot = new KrustyBot(G, G.scenario.slotToPlayer[0], { seed: 3, variant: 'workers,vents=zone' });
  assert.deepEqual(bot.variant, { workers: true, vents: 'zone' });
  const out = bot.think(4);
  assert.ok(out.commands.length >= 1, 'the first think buys the worker as before');
  assert.deepEqual(bot.summary().variant, { workers: true, vents: 'zone' });
  assert.equal(bot.asserts.length, 0);
});

test('a series is paired and deterministic: every job comes with its seat-swapped twin', () => {
  const jobs = makeJobs({ maps: [{ name: 'D8PLAY01', players: 8 }, { name: 'J8PLAY01', players: 8 }], pool: parsePool('krusty,rusher'), a: parseSpec('krusty+workers'), pairs: 3, ticks: 100, seed: 7 });
  assert.equal(jobs.length, 3 * 2 * 2 * 2);
  for (let i = 0; i < jobs.length; i += 2) {
    const [x, y] = [jobs[i], jobs[i + 1]];
    assert.equal(x.swap, false);
    assert.equal(y.swap, true);
    assert.deepEqual([x.map, x.seats, x.races, x.seed, x.a, x.b], [y.map, y.seats, y.races, y.seed, y.a, y.b]);
    assert.notEqual(x.seats[0], x.seats[1]);
    assert.ok(RACES.some((r) => r[0] === x.races[0] && r[1] === x.races[1]));
  }
  const again = makeJobs({ maps: [{ name: 'D8PLAY01', players: 8 }, { name: 'J8PLAY01', players: 8 }], pool: parsePool('krusty,rusher'), a: parseSpec('krusty+workers'), pairs: 3, ticks: 100, seed: 7 });
  assert.deepEqual(again, jobs, 'the same seed lays out the same series');
});

test('a short game runs both brains, reports metrics and the swapped twin changes the seats', () => {
  const job = { map: 'D8PLAY01', seats: [0, 3], races: [0, 1], seed: 11, a: 'krusty+workers+vents=zone', b: 'rusher', ticks: 1500, swap: false };
  const r = runGame(job);
  assert.notEqual(r.decidedBy, 'kill', '1500 ticks kill nobody');
  assert.equal(r.ticks, 1500);
  assert.equal(r.assertCount, 0, JSON.stringify(r.asserts));
  assert.equal(r.a.spec, 'krusty+workers+vents=zone');
  assert.equal(r.b.spec, 'rusher');
  assert.deepEqual([r.a.seat, r.a.race, r.b.seat, r.b.race], [0, 0, 3, 1]);
  assert.ok(r.a.trained >= 1, 'Krusty bought its worker');
  assert.ok(r.a.hq && r.b.hq);
  const s = runGame({ ...job, swap: true });
  assert.deepEqual([s.a.seat, s.a.race, s.b.seat, s.b.race], [3, 1, 0, 0]);
  const agg = aggregate([{ job, r }, { job: { ...job, swap: true }, r: s }]);
  assert.equal(agg.length, 1);
  assert.equal(agg[0].games, 2);
  assert.equal(agg[0].wins + agg[0].losses + agg[0].draws, 2);
  assert.equal(agg[0].winsPoints + agg[0].lossesPoints, agg[0].wins + agg[0].losses, 'nothing decided by a kill');
  assert.deepEqual(wilson(0, 0), [0, 0]);
  const [lo, hi] = wilson(5, 10);
  assert.ok(lo > 0.2 && lo < 0.5 && hi > 0.5 && hi < 0.8);
});

test('a free-for-all: three sides, the candidate rotated through the seats, the pool as one opponent key', () => {
  const jobs = makeJobs({ maps: [{ name: 'D8PLAY01', players: 8 }], pool: parsePool('rusher,krusty'), a: parseSpec('krusty+workers'), pairs: 1, ticks: 100, seed: 3, ffa: 3 });
  assert.equal(jobs.length, 3);
  assert.deepEqual(jobs.map((j) => j.rot), [0, 1, 2]);
  assert.equal(new Set(jobs[0].seats).size, 3);
  assert.equal(jobs[0].b, 'rusher & krusty');
  const r = runGame({ ...jobs[1], ticks: 1200 });
  assert.notEqual(r.decidedBy, 'kill');
  assert.equal(r.others.length, 2);
  assert.deepEqual(r.others.map((o) => o.spec), ['rusher', 'krusty']);
  assert.equal(r.a.seat, jobs[1].seats[1], 'rotation 1 puts the candidate in the second seat');
  assert.equal(r.assertCount, 0);
  const agg = aggregate([{ job: jobs[1], r }]);
  assert.equal(agg[0].opponent, 'rusher & krusty');
});

// Krusty's bot-mode extensions (src/engine/krustyx.js, 7 Oct 2026, plan §19.13): the switches parse,
// a game with every switch on plays without an engine or Krusty assert, the extension slots are the
// attack task's 8..15 and the original two groups keep theirs, the economy shows in the metrics, and
// exact mode is untouched (no aux, no switch).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createGame, loadMapJson } from '../src/engine/index.js';
import * as Krusty from '../src/engine/krusty.js';
import * as X from '../src/engine/krustyx.js';
import { KrustyBot } from '../src/krustybot.js';
import { runGame } from '../src/arena.js';
import { splitCommands } from '../src/commands.js';

test('the extension switches parse and `plus` expands to all of them', () => {
  assert.deepEqual(Krusty.parseVariant('ratio=13'), { ratio: 13 });
  assert.deepEqual(Krusty.parseVariant('pressure,react=false,upgrades=experience'), { pressure: true, react: false, upgrades: 'experience' });
  assert.deepEqual(Krusty.parseVariant('plus'), { workers: true, vents: 'zone', ratio: 13, pressure: true, fortify: true, react: true, upgrades: 'experience', mines: true, clear: true, airscout: true, focus: true, hold: true });
  assert.throws(() => Krusty.parseVariant('ratio=0'), /tenths/);
  assert.throws(() => Krusty.parseVariant('upgrades=always'), /experience/);
});

test('a plus bot against the rusher: no asserts, a base and an army within 12000 ticks', () => {
  const r = runGame({ map: 'J8PLAY01', seats: [0, 3], races: [0, 1], seed: 11, a: 'krusty+plus', b: 'rusher', ticks: 12000, swap: false });
  assert.equal(r.assertCount, 0, JSON.stringify(r.asserts));
  assert.equal(r.a.botAsserts, 0);
  assert.ok(r.a.peakMines >= 1, `a mine: ${r.a.peakMines}`);
  assert.ok(r.a.trained >= 10 && r.a.peakFighters >= 8, `an army: ${r.a.trained} trained, ${r.a.peakFighters} fighters at peak`);
});

test('the extension state lives beside the kai, uses slots 8..15 and leaves exact mode alone', () => {
  const G = createGame(loadMapJson('J8PLAY01'), { slots: [...Array(8)].map((_, s) => ({ type: s < 2 ? 2 : 3, race: s, colour: s, team: s, name: s < 2 ? `P${s}` : '' })), localSlot: -1, titleDigit: 8 }, {});
  // hold and react off: under the hold doctrine a lone small bot sends no squad, and react's purchases leave no money for one
  const bot = new KrustyBot(G, G.scenario.slotToPlayer[0], { seed: 3, variant: 'plus,hold=false,react=false' });
  const lines = [];
  for (let t = 1; t <= 12000; t++) {
    if (t % 32 === (4 + 4 * bot.p) % 32) {
      const out = bot.think(t);
      for (const buf of out.commands) for (const c of splitCommands(buf)) G.applyCommand(c.raw);
      lines.push(...out.lines);
    }
    G.step();
  }
  assert.equal(bot.asserts.length, 0, JSON.stringify(bot.asserts));
  const x = bot.aux.x;
  assert.ok(x, 'state created');
  assert.ok(x.home > 0, 'the home zone found');
  const ctx = { aux: bot.aux };
  for (const m of [0, 1]) assert.equal(X.isSpecial(ctx, m), false, `group ${m} is the original army`);
  for (let m = 8; m < 16; m++) if (X.isSpecial(ctx, m)) assert.ok(m >= 8, 'extension slots are 8..15');
  assert.ok(lines.some((l) => l.startsWith('Mine laid') || l.startsWith('Mines:')), 'engineers bought or mines laid');
  assert.ok(lines.some((l) => l.startsWith('Pressure squad')), 'a pressure squad formed');
  assert.ok(x.stats.patrols > 0, 'air patrol flew');
  assert.deepEqual(bot.summary().x, x.stats);
  // exact mode: a ctx without aux gets null state and the hooks return the originals
  const exactCtx = { G, p: bot.p, kai: bot.kai, exact: true, fixes: false, assert: () => {} };
  assert.equal(X.state(exactCtx), null);
  assert.equal(X.take(exactCtx, 150, 0), -1);
  assert.equal(X.production(exactCtx, new Array(9).fill(0)), false);
  assert.equal(X.airPatrol(exactCtx, 150, true), false);
  assert.equal(X.upgradeWanted(exactCtx, 0), null);
  assert.equal(X.isSpecial(exactCtx, 8), false);
  assert.equal(X.focus(exactCtx, 5), 1);
});

test('focus: the most hostile enemy becomes the aggressor, its zones score triple, the squads pick its assets', () => {
  const G = createGame(loadMapJson('J8PLAY01'), { slots: [...Array(8)].map((_, s) => ({ type: s < 3 ? 2 : 3, race: 0, colour: s, team: s, name: s < 3 ? `P${s}` : '' })), localSlot: -1, titleDigit: 8 }, {});
  const bot = new KrustyBot(G, G.scenario.slotToPlayer[0], { seed: 3, variant: 'focus,pressure' });
  bot.think(4);
  const x = bot.aux.x;
  assert.equal(x.aggressor, -1, 'nobody has come near yet');
  const q = G.scenario.slotToPlayer[1];
  const other = G.scenario.slotToPlayer[2];
  x.hostility[q] = 40;
  x.hostility[other] = 5;
  // a think with the hostility planted: decay keeps q above the threshold and makes it the aggressor
  x.mobOwner.fill(-1);
  bot.think(36);
  assert.equal(x.aggressor, q);
  const zoneOfQ = [...Array(255).keys()].find((z) => z > 0 && x.interestTeam[z] === -1);
  x.interestTeam[zoneOfQ] = q;
  assert.equal(X.focus({ aux: bot.aux, kai: bot.kai, variant: bot.variant }, zoneOfQ), 3);
  x.interestTeam[zoneOfQ] = other;
  assert.equal(X.focus({ aux: bot.aux, kai: bot.kai, variant: bot.variant }, zoneOfQ), 1);
});

test('the switches of 8 Oct 2026 parse, `tweak` expands, and the default server variant is workers + lieutenant', async () => {
  assert.deepEqual(Krusty.parseVariant('workers,lieutenant'), { workers: true, lieutenant: true });
  for (const k of ['landmines', 'factory', 'alarm', 'batch', 'safe', 'counter', 'escort', 'patrol', 'second', 'shield', 'gate', 'tech', 'upnow', 'mechfirst', 'noscout']) {
    assert.deepEqual(Krusty.parseVariant(k), { [k]: true });
  }
  assert.deepEqual(Krusty.parseVariant('tweak'), { workers: true, upgrades: 'experience', hold: true, focus: true, landmines: true, alarm: true, safe: true, counter: true, escort: true });
  const { DEFAULTS } = await import('../src/config.js');
  assert.equal(DEFAULTS.BOT_VARIANT, 'workers,lieutenant');
});

test('lieutenant: the commander leaves the groups for its own slot, follows the army and gives the star command', () => {
  const G = createGame(loadMapJson('D8PLAY03'), { slots: [...Array(8)].map((_, s) => ({ type: s === 4 || s === 0 ? 2 : 3, race: s === 0 ? 1 : 0, colour: s, team: s, name: s === 4 || s === 0 ? `P${s}` : '' })), localSlot: -1, titleDigit: 8 }, {});
  const bot = new KrustyBot(G, G.scenario.slotToPlayer[4], { seed: 9, variant: 'workers,lieutenant' });
  const rival = new KrustyBot(G, G.scenario.slotToPlayer[0], { seed: 5, variant: 'workers' });
  for (let t = 1; t <= 12000; t++) {
    for (const b of [bot, rival]) {
      if (t % 32 !== (4 + 4 * b.p) % 32) continue;
      const out = b.think(t);
      for (const buf of out.commands) for (const c of splitCommands(buf)) G.applyCommand(c.raw);
    }
    G.step();
  }
  assert.equal(bot.asserts.length, 0, JSON.stringify(bot.asserts));
  assert.equal(X.isSpecial({ aux: bot.aux }, X.COMMANDER_SLOT), true, 'the commander has its slot');
  assert.ok((bot.aux.x.stats.stars ?? 0) >= 3, `star commands: ${bot.aux.x.stats.stars ?? 0}`); // 8 with these seeds
});

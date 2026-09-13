// AI Mercenary in battle (plan §19.8): the opening offer, the 1000-money alliance (0x0F -> 0x0D
// on both matrices + chat), the refund while an alliance runs, expiry, the ally leaving, and the
// idle fallbacks without the engine or with MERCENARY_AI=off. The engine is faked (the rusher
// has its own tests against the real engine, test/rusher.test.js).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Harness, cmdsOf, startBattle, answerSync } from './helpers.js';
import { T, build } from '../src/commands.js';
import { P, playerAddr } from '../src/engine/mem.js';
import { ALLY_PRICE } from '../src/mercenary.js';

/** A stand-in engine: identity shuffle (slot s -> player s), a gs big enough for the player blocks. */
function fakeEngine() {
  const games = [];
  return {
    games,
    loadMapJson: () => ({ name: 'fake' }),
    createGame(mapJson, lobby) {
      const gs = Buffer.alloc(0x8000);
      gs.writeInt32LE(41, 0x7d40);
      for (let p = 0; p < 8; p++) {
        gs.write(`P${p}`, playerAddr(p) + P.NAME, 'latin1');
        gs.writeInt32LE(1500, playerAddr(p) + P.MONEY);
      }
      const g = {
        gs,
        lobby,
        time: 0,
        applied: [],
        history: new Map(),
        slotToPlayer: [0, 1, 2, 3, 4, 5, 6, 7],
        step() {
          this.time++;
          const c = (this.time * 7 + 3) & 0xffff;
          this.history.set(this.time, c);
          return c;
        },
        historyAt(t) {
          return this.history.get(t);
        },
        applyCommand(raw) {
          this.applied.push({ tick: this.time, type: raw[0], raw: Buffer.from(raw) });
          if (raw[0] === T.BONUS) {
            const a = playerAddr(raw[1]) + P.MONEY;
            gs.writeInt32LE(gs.readInt32LE(a) + 1000, a);
          }
        },
      };
      games.push(g);
      return g;
    },
  };
}

function battle(overrides = {}) {
  const eng = fakeEngine();
  const h = new Harness({ SYNC_CHECK: 'shadow', ...overrides }, { engine: eng });
  const [a, b] = startBattle(h);
  // the peers never send a name, so the room calls them Player<slot>; that is what the Mercenary says
  a.name = h.room.slots[a.slot].name;
  b.name = h.room.slots[b.slot].name;
  return { h, eng, a, b };
}

/** One server step after both peers answered the previous frame; returns the commands of the new frame. */
function frame(h, a, b) {
  h.stepAfter(33);
  const fa = a.take();
  const fb = b.take();
  assert.equal(fa.length, 1);
  assert.ok(fa[0].equals(fb[0]), 'identical frames for everybody');
  answerSync(a, fa);
  answerSync(b, fb);
  return cmdsOf(fa[0]);
}

const chats = (cmds) => cmds.filter((c) => c.type === T.CHAT).map((c) => c.text);
const diplo = (cmds) => cmds.filter((c) => c.type === T.DIPLOMACY).map((c) => [...c.raw.subarray(1)]);
/** The four relations of an alliance between bot `m` and player `q` (both matrices, both directions, F48). */
const relations = (m, q, on) => [[m, q, 0, on], [m, q, 1, on], [q, m, 0, on], [q, m, 1, on]];
/** The diplomacy commands of a frame that concern players p and q only. */
const between = (cmds, p, q) => diplo(cmds).filter((d) => (d[0] === p && d[1] === q) || (d[0] === q && d[1] === p));

test('the first frame carries the offer from the Mercenary player, and the greeting announces the deal', () => {
  const { h, a, b } = battle();
  const m = h.room.mercenary;
  assert.ok(m.active);
  assert.equal(m.player, 0, 'slot 0 -> player 0 in the fake shuffle');
  const cmds = frame(h, a, b);
  assert.equal(cmds[0].type, T.UNTIL);
  const lines = chats(cmds);
  assert.ok(lines.length >= 2);
  assert.ok(lines[0].startsWith('AI Mercenary: I ally with anyone who pays me 1000'));
  assert.ok(lines.some((l) => l.includes('120 seconds')));
  const players = h.room.bots.list.map((b) => b.player);
  assert.equal(players.length, 2, 'AI Mercenary and AI Marauder');
  for (const c of cmds.filter((c) => c.type === T.CHAT)) {
    assert.ok(players.includes(c.from));
    assert.equal(c.mask, 0xff);
  }
  const marauder = h.room.bots.others[0];
  assert.equal(marauder.name, 'AI Marauder');
  assert.ok(lines.some((l) => l.startsWith('AI Marauder: Same deal here: 1000 buys my alliance')), lines.join(' | '));
  assert.ok(h.room.lobby.greeting()[1].includes('AI Marauder and I rush; 1000 in battle buys an alliance for 120 s.'), h.room.lobby.greeting()[1]);
});

test('two bots, two deals: a gift to AI Marauder allies with it alone, the Mercenary keeps its own deal', () => {
  const { h, a, b } = battle();
  frame(h, a, b);
  const merc = h.room.bots.mercenary;
  const mar = h.room.bots.others[0];
  assert.ok(mar.active && mar.player === mar.slot, 'identity shuffle in the fake engine');
  a.send(build.bonus(mar.player));
  let cmds = frame(h, a, b);
  // the deal, and the end of the bots' standing peace: the hired Marauder turns on the Mercenary
  assert.deepEqual(diplo(cmds), [...relations(mar.player, a.slot, 1), ...relations(merc.player, mar.player, 0)], 'AI Marauder <-> A, Mercenary <-/-> Marauder');
  assert.equal(mar.ally.player, a.slot);
  assert.equal(merc.ally, null);
  assert.ok(chats(cmds)[0].startsWith(`AI Marauder: ${a.name} paid 1000`), chats(cmds)[0]);
  // the Mercenary still sells: B buys it; A's second payment to the Marauder comes back
  b.send(build.bonus(merc.player));
  a.send(build.bonus(mar.player));
  cmds = frame(h, a, b);
  assert.deepEqual(diplo(cmds), relations(merc.player, b.slot, 1));
  assert.equal(merc.ally.player, b.slot);
  assert.ok(cmds.filter((c) => c.type === T.BONUS).some((c) => c.player === a.slot), 'refund from the Marauder');
  assert.ok(chats(cmds).some((l) => l.startsWith(`AI Marauder: ${a.name}, I am allied with you`)));
  assert.ok(merc.isAlly(b.slot) && !merc.isAlly(a.slot) && mar.isAlly(a.slot) && !mar.isAlly(b.slot));
});

test('1000 to the Mercenary buys the alliance: 0x0F relayed, alliance + vision set, told in chat', () => {
  const { h, eng, a, b } = battle();
  frame(h, a, b);
  a.send(build.bonus(0));
  const cmds = frame(h, a, b);
  const types = cmds.map((c) => c.type);
  assert.ok(types.includes(T.BONUS), 'the gift itself is relayed');
  const mar = h.room.bots.others[0].player;
  assert.deepEqual(between(cmds, 0, a.slot), relations(0, a.slot, 1), 'Mercenary <-> A: alliance and shared vision, both directions');
  assert.deepEqual(between(cmds, 0, mar), relations(0, mar, 0), 'the hired Mercenary leaves the bots\' peace');
  assert.equal(diplo(cmds).length, 8);
  const lines = chats(cmds);
  assert.ok(lines[0].startsWith(`AI Mercenary: ${a.name} paid ${ALLY_PRICE}. We are allies for 120 seconds, both ways`), lines[0]);
  assert.equal(h.room.mercenary.ally.player, a.slot);
  assert.ok(h.room.mercenary.isAlly(a.slot));
  // the engine credited the Mercenary's ledger when the frame was executed
  assert.equal(eng.games[0].gs.readInt32LE(playerAddr(0) + P.MONEY), 2500);
});

test('a second payment while an alliance runs is returned: 0x0F back, ledger charged, explained', () => {
  const { h, eng, a, b } = battle();
  frame(h, a, b);
  a.send(build.bonus(0));
  frame(h, a, b);
  b.send(build.bonus(0));
  const cmds = frame(h, a, b);
  const bonuses = cmds.filter((c) => c.type === T.BONUS).map((c) => c.player);
  assert.deepEqual(bonuses.sort(), [0, b.slot].sort(), 'the gift and the refund');
  assert.equal(diplo(cmds).length, 0, 'no second alliance');
  const lines = chats(cmds);
  assert.ok(lines[0].startsWith(`AI Mercenary: ${b.name}, I am allied with ${a.name} for`), lines[0]);
  assert.ok(lines[0].endsWith(`Your ${ALLY_PRICE} goes back.`));
  assert.equal(h.room.mercenary.ally.player, a.slot, 'A stays the ally');
  // ledger: 1500 + 1000 (A) + 1000 (B's gift lands) - 1000 (refund) = 2500
  assert.equal(eng.games[0].gs.readInt32LE(playerAddr(0) + P.MONEY), 2500);
  // the ally paying again is returned too
  a.send(build.bonus(0));
  const again = chats(frame(h, a, b));
  assert.ok(again[0].startsWith(`AI Mercenary: ${a.name}, I am allied with you for`), again[0]);
});

test('the alliance ends when the time is up: both relations cleared, announced, a new deal possible', () => {
  const { h, a, b } = battle({ MERCENARY_ALLY_S: 1 }); // 1 s = 31 ticks at 33 ms
  frame(h, a, b);
  a.send(build.bonus(0));
  frame(h, a, b);
  const m = h.room.mercenary;
  assert.equal(m.ally.until, h.room.sync.engineTime - 1 + Math.round(1000 / 33), 'bought at the engine time of the gift');
  let ended = null;
  for (let i = 0; i < 40 && !ended; i++) {
    const cmds = frame(h, a, b);
    if (diplo(cmds).length) ended = cmds;
  }
  assert.ok(ended, 'the alliance ended within the window');
  const mar = h.room.bots.others[0].player;
  assert.deepEqual(diplo(ended), [...relations(0, a.slot, 0), ...relations(0, mar, 1)], 'the deal closed, the peace with the Marauder restored');
  assert.ok(chats(ended)[0].startsWith(`AI Mercenary: The alliance with ${a.name} is over: the time is up.`), chats(ended)[0]);
  assert.equal(m.ally, null);
  b.send(build.bonus(0));
  const next = frame(h, a, b);
  assert.deepEqual(diplo(next), [...relations(0, b.slot, 1), ...relations(0, mar, 0)], 'B can buy the next one');
});

test('the ally leaving the game ends the alliance, and a bot takes its base over instead of DISCONNECT', () => {
  const { h, a, b } = battle();
  frame(h, a, b);
  a.send(build.bonus(0));
  frame(h, a, b);
  a.sock.destroy();
  h.stepAfter(33);
  const cmds = cmdsOf(b.take()[0]);
  assert.ok(!cmds.some((c) => c.type === T.DISCONNECT), 'no DISCONNECT: the player stays a human on the wire (§19.9)');
  const mar = h.room.bots.others[0].player;
  // the deal closes, the peace with the Marauder returns, and the inherited base joins the bots' peace
  assert.deepEqual(between(cmds, 0, a.slot), [...relations(0, a.slot, 0), ...relations(0, a.slot, 1)]);
  assert.deepEqual(between(cmds, 0, mar), relations(0, mar, 1));
  assert.deepEqual(between(cmds, mar, a.slot), relations(mar, a.slot, 1));
  const lines = chats(cmds);
  assert.ok(lines[0].includes(`${a.name} left the game`), lines[0]);
  assert.equal(h.room.mercenary.ally, null);
  // the new bot speaks with A's game player and announces itself
  const bot = h.room.bots.byPlayer(a.slot);
  assert.ok(bot && bot.takeover, 'a takeover bot for the leaver');
  assert.equal(bot.name, a.name);
  assert.equal(h.room.bots.list.length, 3);
  const hello = cmds.find((c) => c.type === T.CHAT && c.from === a.slot);
  assert.ok(hello && hello.text.startsWith(`AI ${a.name}: ${a.name} left the battle. I run this base now: 1000 buys my alliance`), hello?.text);
  // B can buy the inherited base's alliance
  b.send(build.bonus(a.slot));
  h.stepAfter(33);
  const next = cmdsOf(b.take()[0]);
  assert.deepEqual(between(next, a.slot, b.slot), relations(a.slot, b.slot, 1));
  assert.deepEqual(between(next, 0, a.slot), relations(0, a.slot, 0), 'the hired base leaves the bots\' peace');
  assert.deepEqual(between(next, mar, a.slot), relations(mar, a.slot, 0));
  assert.equal(bot.ally.player, b.slot);
});

test('a client leaving while everybody loads gets its bot when the battle starts', () => {
  const eng = fakeEngine();
  const h = new Harness({ SYNC_CHECK: 'shadow' }, { engine: eng });
  const peers = ['A', 'B', 'C'].map((n) => h.join(n));
  for (const p of peers) {
    p.take();
    p.cdReport();
  }
  for (const p of peers) p.pressReady();
  assert.equal(h.room.state, 'STARTING');
  const [a, b, c] = peers;
  c.sock.destroy(); // never loads
  assert.equal(h.room.state, 'STARTING');
  for (const p of [a, b]) p.send(build.mready(p.slot, 2));
  assert.equal(h.room.state, 'RUNNING');
  for (const p of [a, b]) p.take();
  const cmds = frame(h, a, b);
  assert.ok(!cmds.some((x) => x.type === T.DISCONNECT), 'no DISCONNECT for the loader that left');
  const bot = h.room.bots.byPlayer(c.slot);
  assert.ok(bot && bot.takeover);
  assert.ok(chats(cmds).some((l) => l.startsWith(`AI Player${c.slot}: Player${c.slot} left the battle.`)), chats(cmds).join(' | '));
});

test('without bots a leaving client is still handed to the game AI with DISCONNECT', () => {
  const { h, a, b } = battle({ MERCENARY_AI: 'off' });
  frame(h, a, b);
  a.sock.destroy();
  h.stepAfter(33);
  const cmds = cmdsOf(b.take()[0]);
  assert.ok(cmds.some((c) => c.type === T.DISCONNECT && c.player === a.slot));
  assert.equal(h.room.bots.list.filter((x) => x.takeover).length, 0);
});

test('a gift between humans is relayed and does not concern the Mercenary', () => {
  const { h, a, b } = battle();
  frame(h, a, b);
  a.send(build.bonus(b.slot));
  const cmds = frame(h, a, b);
  assert.deepEqual(cmds.filter((c) => c.type === T.BONUS).map((c) => c.player), [b.slot]);
  assert.equal(diplo(cmds).length, 0);
  assert.equal(chats(cmds).length, 0);
  assert.equal(a.client.strikes, 0);
});

test('without the engine the Mercenary stays idle: no offer, gifts relayed, greeting unchanged', () => {
  const h = new Harness({ SYNC_CHECK: 'off' });
  const [a, b] = startBattle(h);
  assert.ok(!h.room.mercenary.active);
  assert.ok(h.room.lobby.greeting()[1].endsWith('My base stays idle.'));
  h.stepAfter(33);
  const cmds = cmdsOf(a.take()[0]);
  assert.deepEqual(cmds.map((c) => c.type), [T.UNTIL, T.TICK_SPEED]);
  answerSync(a, [Buffer.concat([build.until(cmds[0].a, cmds[0].until)])]);
  b.take();
  a.send(build.bonus(0));
  h.stepAfter(33);
  const next = cmdsOf(b.take()[0]);
  assert.ok(next.some((c) => c.type === T.BONUS));
  assert.ok(!next.some((c) => c.type === T.DIPLOMACY));
});

test('MERCENARY_AI=off keeps the engine but no deal and no chat', () => {
  const { h, a, b } = battle({ MERCENARY_AI: 'off' });
  assert.ok(h.room.sync.active);
  assert.ok(!h.room.mercenary.active);
  assert.ok(h.room.lobby.greeting()[1].endsWith('My base stays idle.'));
  const first = frame(h, a, b);
  assert.equal(chats(first).length, 0);
  a.send(build.bonus(0));
  const cmds = frame(h, a, b);
  assert.ok(cmds.some((c) => c.type === T.BONUS));
  assert.equal(diplo(cmds).length, 0);
});

test('the Mercenary stands down when the engine is disabled mid-game', () => {
  const { h, a, b } = battle();
  frame(h, a, b);
  a.send(build.bonus(0));
  frame(h, a, b);
  h.room.sync.disable('test');
  frame(h, a, b); // the Mercenary notices when this frame is issued; its reaction rides the next one
  const cmds = frame(h, a, b);
  assert.ok(!h.room.mercenary.active);
  assert.deepEqual(diplo(cmds), relations(0, a.slot, 0), 'the running alliance is closed properly');
  const lines = chats(cmds);
  assert.ok(lines.some((l) => l.includes('I lost sight of the battle')));
  a.send(build.bonus(0));
  const after = frame(h, a, b);
  assert.equal(diplo(after).length, 0, 'no more deals');
  // with the engine gone a leaving client falls back to DISCONNECT (the game's AI takes over)
  b.sock.destroy();
  h.stepAfter(33);
  assert.ok(cmdsOf(a.take()[0]).some((c) => c.type === T.DISCONNECT && c.player === b.slot));
  assert.equal(h.room.bots.list.filter((x) => x.takeover).length, 0);
});

test('both bots bought by the same player: the bots ally with each other (a pact), and separate when a deal ends', () => {
  const { h, a, b } = battle({ MERCENARY_ALLY_S: 1 }); // 1 s = 30 ticks at 33 ms
  frame(h, a, b);
  const merc = h.room.bots.mercenary;
  const mar = h.room.bots.others[0];
  assert.ok(merc.isAlly(mar.player) && mar.isAlly(merc.player), 'the bots start allied with each other');
  assert.equal(h.room.bots.pacts.size, 1);
  a.send(build.bonus(mar.player));
  let cmds = frame(h, a, b);
  assert.deepEqual(diplo(cmds), [...relations(mar.player, a.slot, 1), ...relations(merc.player, mar.player, 0)], 'one deal: the hired bot leaves the peace');
  assert.ok(!merc.isAlly(mar.player) && !mar.isAlly(merc.player), 'rivals now');
  assert.equal(h.room.bots.pacts.size, 0);
  for (let i = 0; i < 4; i++) frame(h, a, b); // the second deal is bought a few ticks later
  a.send(build.bonus(merc.player));
  cmds = frame(h, a, b);
  // the second deal and, in the same frame, the four relations between the two bots (F49: the game's
  // end check compares every alive player with the first alive one, so the bots must be allied too)
  assert.deepEqual(diplo(cmds), [...relations(merc.player, a.slot, 1), ...relations(merc.player, mar.player, 1)]);
  assert.ok(merc.isAlly(mar.player) && mar.isAlly(merc.player), 'partners while both serve A');
  assert.ok(merc.isAlly(a.slot) && mar.isAlly(a.slot));
  assert.ok(!merc.isAlly(b.slot) && !mar.isAlly(b.slot));
  const pact = cmds.filter((c) => c.type === T.CHAT).find((c) => c.text.includes('both serve you now'));
  assert.ok(pact, 'the pact is told');
  assert.equal(pact.from, merc.player);
  assert.equal(pact.mask, 1 << a.slot, 'to the common ally only');
  assert.equal(h.room.bots.pacts.size, 1);
  // B's payment to the Marauder comes back: the pact stands
  b.send(build.bonus(mar.player));
  cmds = frame(h, a, b);
  assert.equal(diplo(cmds).length, 0);
  assert.equal(h.room.bots.pacts.size, 1);
  // the Marauder's deal (bought first) expires first: the pact ends with it, the Mercenary's deal goes on
  let ended = null;
  for (let i = 0; i < 40 && !ended; i++) {
    cmds = frame(h, a, b);
    if (diplo(cmds).length) ended = cmds;
  }
  assert.ok(ended, 'the first deal ended within the window');
  assert.deepEqual(diplo(ended), [...relations(mar.player, a.slot, 0), ...relations(merc.player, mar.player, 0)]);
  assert.equal(h.room.bots.pacts.size, 0);
  assert.ok(!merc.isAlly(mar.player) && !mar.isAlly(merc.player), 'rivals again');
  assert.ok(merc.ally && merc.ally.player === a.slot, 'the Mercenary still serves A');
  const truce = ended.filter((c) => c.type === T.CHAT).find((c) => c.text.includes('truce'));
  assert.ok(truce && truce.mask === (1 << a.slot), 'told to the ally that remains');
  // the Mercenary's deal ends a few ticks later: the bots' peace returns
  let restored = null;
  for (let i = 0; i < 10 && !restored; i++) {
    cmds = frame(h, a, b);
    if (diplo(cmds).length) restored = cmds;
  }
  assert.ok(restored, 'the second deal ended');
  assert.deepEqual(diplo(restored), [...relations(merc.player, a.slot, 0), ...relations(merc.player, mar.player, 1)]);
  assert.ok(merc.isAlly(mar.player) && mar.isAlly(merc.player), 'at peace again');
  assert.equal(h.room.bots.pacts.size, 1);
});

test('actions reach the ally only; the deal talks to the payer; the offer is public', () => {
  const { h, a, b } = battle();
  let cmds = frame(h, a, b);
  for (const c of cmds.filter((c) => c.type === T.CHAT)) assert.equal(c.mask, 0xff, 'offers to everybody');
  const m = h.room.mercenary;
  m.sayToAlly('Marching.');
  cmds = frame(h, a, b);
  assert.equal(cmds.filter((c) => c.type === T.CHAT).length, 0, 'no ally: an action line is not sent');
  a.send(build.bonus(m.player));
  cmds = frame(h, a, b);
  const deal = cmds.filter((c) => c.type === T.CHAT);
  assert.equal(deal.length, 2, 'the deal and the end of the truce with the Marauder');
  assert.ok(deal[0].text.includes('paid 1000'));
  assert.ok(deal[1].text.includes('truce'), deal[1].text);
  for (const c of deal) assert.equal(c.mask, 1 << a.slot, 'both told to the payer');
  m.sayToAlly('Marching.');
  b.send(build.bonus(m.player));
  cmds = frame(h, a, b);
  const lines = cmds.filter((c) => c.type === T.CHAT);
  assert.equal(lines.find((c) => c.text.endsWith('Marching.')).mask, 1 << a.slot, 'the action goes to the ally');
  assert.equal(lines.find((c) => c.text.includes('goes back')).mask, 1 << b.slot, 'the refund is explained to B alone');
  a.sock.destroy();
  h.stepAfter(33);
  const after = cmdsOf(b.take()[0]).filter((c) => c.type === T.CHAT);
  assert.equal(after.find((c) => c.text.includes('is over')).mask, 1 << a.slot, 'the end is told to the former ally');
  assert.equal(after.find((c) => c.text.includes('left the battle')).mask, 0xff, 'the new bot introduces itself to everybody');
});

test('config: MERCENARY_AI and MERCENARY_ALLY_S are validated', () => {
  assert.throws(() => new Harness({ MERCENARY_AI: 'krusty' }), /MERCENARY_AI/);
  assert.throws(() => new Harness({ MERCENARY_ALLY_S: 0 }), /MERCENARY_ALLY_S/);
  const h = new Harness({ MERCENARY_AI: 'OFF' });
  assert.equal(h.cfg.MERCENARY_AI, 'off');
});

test('end to end with the real engine: the Mercenary speaks and buys in the sync frames', async () => {
  const engine = await import('../src/engine/index.js');
  const h = new Harness({ SYNC_CHECK: 'send' }, { engine: { createGame: engine.createGame, loadMapJson: engine.loadMapJson } });
  const a = h.join('A');
  const b = h.join('B');
  for (const p of [a, b]) {
    p.take();
    p.cdReport();
  }
  for (const p of [a, b]) p.pressReady();
  assert.equal(h.room.state, 'STARTING');
  // MREADY carries the game player index after the shuffle: run the engine's shuffle to know it
  const lobby = { slots: h.room.startSlots.map((s) => ({ ...s })), localSlot: -1, titleDigit: h.room.map.players };
  const probe = engine.createGame(engine.loadMapJson(h.room.map.file), lobby, {});
  for (const p of [a, b]) p.send(build.mready(probe.scenario.slotToPlayer[p.slot], 2));
  assert.equal(h.room.state, 'RUNNING');
  assert.ok(h.room.sync.active, 'real engine running');
  const m = h.room.mercenary;
  assert.ok(m.active && m.rusher, 'Mercenary plays with the rusher');
  assert.equal(m.player, probe.scenario.slotToPlayer[0]);
  for (const p of [a, b]) p.take();
  const players = h.room.bots.list.map((bot) => bot.player);
  const seen = { chat: [], build: [] };
  for (let i = 0; i < 80 && new Set(seen.build.map((c) => c[2])).size < 2; i++) {
    const cmds = frame(h, a, b);
    for (const c of cmds) {
      if (c.type === T.CHAT) seen.chat.push(c.text);
      if (c.type === T.BUILD) seen.build.push([...c.raw]);
    }
  }
  assert.ok(seen.chat[0].startsWith('AI Mercenary: I ally with anyone who pays me 1000'), seen.chat[0]);
  assert.ok(seen.chat.some((l) => l.includes('And I rush.')));
  // actions are told to allies only: without an ally the worker orders go out unannounced
  assert.ok(!seen.chat.some((l) => l.includes('A worker first')), seen.chat.join(' | '));
  for (const c of seen.build) assert.ok(players.includes(c[2]), `worker order for a bot player (${c[2]})`);
  assert.deepEqual(new Set(seen.build.map((c) => c[2])), new Set(players), 'both bots bought their worker');
  assert.deepEqual(seen.build.map((c) => [c[0], c[1], c[3]])[0], [T.BUILD, 6, 1], 'one EXPL worker');
  assert.ok(h.room.sync.active, 'the engine accepted its own bot commands');
  assert.equal(h.room.sync.disabledReason, null);
  // A leaves: its base becomes a bot, no DISCONNECT, the engine keeps running in send mode
  const aPlayer = probe.scenario.slotToPlayer[a.slot];
  const aName = h.room.slots[a.slot].name; // the peers send no name: Player<slot>
  a.sock.destroy();
  const after = [];
  for (let i = 0; i < 80; i++) {
    // the inherited base thinks 32 ticks plus its stagger after the takeover
    h.stepAfter(33);
    const fb = b.take();
    assert.equal(fb.length, 1);
    answerSync(b, fb);
    after.push(...cmdsOf(fb[0]));
  }
  assert.ok(!after.some((c) => c.type === T.DISCONNECT), 'no DISCONNECT');
  const bot = h.room.bots.byPlayer(aPlayer);
  assert.ok(bot && bot.takeover && bot.rusher, 'a rushing bot inherited the base');
  assert.ok(after.some((c) => c.type === T.CHAT && c.from === aPlayer && c.text.startsWith(`AI ${aName}: ${aName} left the battle.`)), 'it introduced itself');
  assert.ok(after.some((c) => c.type === T.BUILD && c.raw[2] === aPlayer), 'and it buys for the inherited base');
  assert.ok(h.room.sync.active && !h.room.sync.aiTakeover, 'the engine is still in step: no AI takeover');
});

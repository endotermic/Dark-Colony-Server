// `/botcount N` in the lobby chat (19 Sep 2026, maintainer: one master bot by default, the players
// set the count): bots are added into random free slots and announced like a joiner's dump, removed
// with DISCONNECT, the master bot stays, the limits follow the map and the players present, the
// pinned chat header follows, and a room reset restores the default.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Harness, HallHarness, startBattle } from './helpers.js';
import { T, build } from '../src/commands.js';

const chatOf = (cmds) => cmds.filter((c) => c.type === T.LOBBY_CHAT).map((c) => c.text);

test('default: one master bot (Mercenary in slot 0), seven seats for players', () => {
  const h = new Harness();
  assert.equal(h.cfg.FAKE_PLAYERS, 1);
  assert.deepEqual(h.room.fakeSlots().map((f) => [f.slot, f.name]), [[0, 'Mercenary']]);
  assert.equal(h.room.seats(), 7);
  assert.equal(h.room.summary().slots, 7);
  assert.equal(h.room.bots.list.length, 1);
  assert.deepEqual(h.cfg.FAKE_NAME_POOL, ['Mercenary', 'Marauder', 'Renegade', 'Outlaw', 'Nomad', 'Drifter', 'Vagabond']);
});

test('/botcount 3 adds two bots: random free slots, dump to everybody (colour before type), announced, bots list follows', () => {
  const h = new Harness({ MIN_PLAYERS: 1 });
  const a = h.join('A');
  const b = h.join('B');
  a.take();
  b.take();
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 3`));
  const fakes = h.room.fakeSlots();
  assert.equal(fakes.length, 3);
  assert.deepEqual(fakes.map((f) => f.name).sort(), ['Marauder', 'Mercenary', 'Renegade']);
  assert.ok(fakes.every((f) => f.slot !== a.slot && f.slot !== b.slot && f.type === 2 && f.status === 1));
  assert.equal(h.room.botCount, 3);
  assert.equal(h.room.bots.list.length, 3);
  assert.equal(h.room.seats(), 5);
  for (const p of [a, b]) {
    const cmds = p.takeCmds();
    for (const f of fakes.filter((x) => x.slot !== 0)) {
      const iColour = cmds.findIndex((c) => c.type === T.COLOUR_SET && c.player === f.slot);
      const iType = cmds.findIndex((c) => c.type === T.TYPE && c.player === f.slot && c.value === 2);
      assert.ok(iColour >= 0 && iType > iColour, `slot ${f.slot}: colour, then type`);
      assert.ok(cmds.some((c) => c.type === T.NAME && c.player === f.slot && c.name === f.name));
      assert.ok(cmds.some((c) => c.type === T.READY && c.player === f.slot && c.status === 1));
    }
    const text = chatOf(cmds).join(' ');
    assert.ok(text.includes(`Player${a.slot} set the bots to 3`), text);
  }
});

test('/botcount 1 removes the extra bots with DISCONNECT; the master bot in slot 0 stays; /botcount shows; /help lists', () => {
  const h = new Harness({ MIN_PLAYERS: 1 });
  const a = h.join('A');
  a.take();
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 4`));
  assert.equal(h.room.fakeSlots().length, 4);
  const extra = h.room.fakeSlots().filter((f) => f.slot !== 0).map((f) => f.slot);
  a.take();
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 1`));
  const cmds = a.takeCmds();
  assert.deepEqual(cmds.filter((c) => c.type === T.DISCONNECT).map((c) => c.player).sort(), extra.sort());
  assert.deepEqual(h.room.fakeSlots().map((f) => f.slot), [0]);
  assert.equal(h.room.bots.list.length, 1);
  for (const s of extra) assert.equal(h.room.slots[s].type, 3, 'the slot is empty again');
  a.send(build.lobbyChat(`Player${a.slot}: /botcount`));
  let text = chatOf(a.takeCmds()).join(' ');
  assert.ok(text.includes('1 bot in this game: Mercenary.'), text);
  a.send(build.lobbyChat(`Player${a.slot}: /help`));
  text = chatOf(a.takeCmds()).join(' ');
  assert.ok(text.includes('/bottype krusty|rusher|random sets their brain') && text.includes('/bothire on|off'), text); // the first help row scrolls out of the 10-row window
  a.send(build.lobbyChat(`Player${a.slot}: /nonsense 3`));
  text = chatOf(a.takeCmds()).join(' ');
  assert.ok(text.includes('Unknown command /nonsense'), text);
});

test('limits: 1..7, never more than the seats the map leaves to the players present; the start rule follows', () => {
  const h = new Harness({ MIN_PLAYERS: 2 });
  const a = h.join('A');
  a.take();
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 0`));
  assert.ok(chatOf(a.takeCmds()).join(' ').includes('the count must be 1..7'));
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 8`));
  assert.ok(chatOf(a.takeCmds()).join(' ').includes('the count must be 1..7'));
  a.send(build.lobbyChat(`Player${a.slot}: /botcount x`));
  assert.ok(chatOf(a.takeCmds()).join(' ').includes('the count must be 1..7'));
  assert.equal(h.room.fakeSlots().length, 1);
  // seven bots + one player fill an 8-player map: the room is full, and one player is enough to start
  assert.equal(h.room.minPlayers, 2);
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 7`));
  a.take();
  assert.equal(h.room.fakeSlots().length, 7);
  assert.equal(h.room.minPlayers, 1, 'MIN_PLAYERS is capped by the seats left');
  assert.ok(h.room.isFull());
  const b = h.join('B');
  assert.ok(b.sock.ended, 'no seat left');
  // with a second real player at most six bots fit
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 2`));
  a.take();
  const c = h.join('C');
  c.take();
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 7`));
  const text = chatOf(a.takeCmds()).join(' ');
  assert.ok(text.includes('at most 6 bots fit on this map with 2 players'), text);
  assert.equal(h.room.fakeSlots().length, 2);
  // plain chat is still relayed
  a.send(build.lobbyChat(`Player${a.slot}: hello`));
  assert.ok(chatOf(c.takeCmds()).some((t) => t === `Player${a.slot}: hello`));
});

test('the pinned header shows the count with the bots configured; a room reset goes back to the default', () => {
  const eng = { loadMapJson: () => ({ name: 'fake' }), createGame: () => ({ gs: Buffer.alloc(0x8000), slotToPlayer: [0, 1, 2, 3, 4, 5, 6, 7], step: () => 0, historyAt: () => 0, applyCommand: () => {} }) };
  const h = new Harness({ SYNC_CHECK: 'shadow', MIN_PLAYERS: 1 }, { engine: eng });
  const a = h.join('A');
  let lines = chatOf(a.takeCmds());
  assert.ok(lines.some((l) => l === 'Bots: 1 krusty, hire off. Type /help.'), lines.join('|'));
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 3`));
  lines = chatOf(a.takeCmds());
  assert.ok(lines.some((l) => l === 'Bots: 3 krusty, hire off. Type /help.'), lines.join('|'));
  const g1 = h.room.lobby.greeting()[1];
  assert.ok(g1.includes('Marauder') && g1.includes('Renegade') && g1.includes('and I play; hiring is off.'), g1);
  h.room.reset();
  assert.equal(h.room.botCount, 1);
  assert.deepEqual(h.room.fakeSlots().map((f) => f.slot), [0]);
});

test('the count set in the lobby is what the battle starts with', () => {
  const h = new Harness({ MIN_PLAYERS: 1 });
  const a = h.join('A');
  a.take();
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 5`));
  a.take();
  a.cdReport();
  a.pressReady();
  assert.equal(h.room.state, 'STARTING');
  assert.equal(h.room.startSlots.filter((s) => s.type === 2).length, 6, 'five bots and the player are humans on the wire');
  a.send(build.mready(a.slot, 2));
  assert.equal(h.room.state, 'RUNNING');
  assert.equal(h.room.bots.list.length, 5);
});

test('hall: the room row shows the seats without the bots and follows /botcount', () => {
  const h = new HallHarness({ MIN_PLAYERS: 1 });
  const p = h.enter('P');
  p.take();
  p.cdReport();
  p.chat('/2');
  p.take();
  p.pressReady();
  p.take();
  const room = p.roomOf(h.pool);
  assert.ok(room);
  assert.equal(room.summary().slots, 7);
  p.send(build.lobbyChat(`Player${p.slot}: /botcount 3`));
  p.take();
  assert.equal(room.summary().slots, 5, 'the map slots without the bots');
  assert.equal(room.summary().seats, 5, 'seats for real players: capacity minus the bots');
  assert.equal(room.freeSlots().length, 4, 'four of them are still free');
  void startBattle;
});

test('/bottype krusty|rusher|random: per room, shown in the header, the brains follow at game start, reset restores BOT_TYPE', async () => {
  const engine = await import('../src/engine/index.js');
  const eng = { createGame: engine.createGame, loadMapJson: engine.loadMapJson };
  const h = new Harness({ SYNC_CHECK: 'shadow', MIN_PLAYERS: 1 }, { engine: eng });
  assert.equal(h.cfg.BOT_TYPE, 'krusty');
  assert.equal(h.room.botType, 'krusty');
  const a = h.join('A');
  a.take();
  a.send(build.lobbyChat(`Player${a.slot}: /bottype`));
  let text = chatOf(a.takeCmds()).join(' ');
  assert.ok(text.includes('The bots play krusty.'), text);
  a.send(build.lobbyChat(`Player${a.slot}: /bottype turtle`));
  text = chatOf(a.takeCmds()).join(' ');
  assert.ok(text.includes('the type must be one of krusty, rusher, random'), text);
  a.send(build.lobbyChat(`Player${a.slot}: /bottype RUSHER`));
  let lines = chatOf(a.takeCmds());
  assert.equal(h.room.botType, 'rusher');
  assert.ok(lines.some((l) => l === 'Bots: 1 rusher, hire off. Type /help.'), lines.join('|'));
  assert.ok(lines.some((l) => l.includes('set the bots to rusher')), lines.join('|'));
  assert.ok(h.room.lobby.greeting()[1].includes('I rush; hiring is off.'), h.room.lobby.greeting()[1]);
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 3`));
  a.send(build.lobbyChat(`Player${a.slot}: /bottype random`));
  lines = chatOf(a.takeCmds());
  assert.ok(lines.some((l) => l === 'Bots: 3 random, hire off. Type /help.'), lines.join('|'));
  // random: the room's random() decides per bot (1 = rusher, 0 = krusty)
  h.randomSeq = [1, 0, 1];
  a.cdReport();
  a.pressReady();
  const lobby = { slots: h.room.startSlots.map((s) => ({ ...s })), localSlot: -1, titleDigit: h.room.map.players };
  const probe = engine.createGame(engine.loadMapJson(h.room.map.file), lobby, {});
  a.send(build.mready(probe.scenario.slotToPlayer[a.slot], 2));
  assert.equal(h.room.state, 'RUNNING');
  assert.ok(h.room.sync.active);
  const kinds = h.room.bots.list.map((b) => b.kind);
  assert.deepEqual(kinds.slice().sort(), ['krusty', 'rusher', 'rusher'], kinds.join());
  assert.ok(h.room.bots.list.every((b) => b.active && b.brain));
  h.room.reset();
  assert.equal(h.room.botType, 'krusty');
});

test('config: BOT_TYPE is validated and lower-cased', () => {
  assert.throws(() => new Harness({ BOT_TYPE: 'turtle' }), /BOT_TYPE/);
  assert.equal(new Harness({ BOT_TYPE: 'Rusher' }).room.botType, 'rusher');
});

test('/bothire on|off: off by default, per room, shown in the header and the greeting, reset restores BOT_HIRE', () => {
  const eng = { loadMapJson: () => ({ name: 'fake' }), createGame: () => ({ gs: Buffer.alloc(0x8000), slotToPlayer: [0, 1, 2, 3, 4, 5, 6, 7], step: () => 0, historyAt: () => 0, applyCommand: () => {} }) };
  const h = new Harness({ SYNC_CHECK: 'shadow', MIN_PLAYERS: 1 }, { engine: eng });
  assert.equal(h.cfg.BOT_HIRE, false);
  assert.equal(h.room.botHire, false);
  const a = h.join('A');
  a.take();
  a.send(build.lobbyChat(`Player${a.slot}: /bothire`));
  let text = chatOf(a.takeCmds()).join(' ');
  assert.ok(text.includes('Hiring of the bots is off.'), text);
  a.send(build.lobbyChat(`Player${a.slot}: /bothire maybe`));
  text = chatOf(a.takeCmds()).join(' ');
  assert.ok(text.includes('say on or off'), text);
  a.send(build.lobbyChat(`Player${a.slot}: /bothire ON`));
  const lines = chatOf(a.takeCmds());
  assert.equal(h.room.botHire, true);
  assert.ok(lines.some((l) => l === 'Bots: 1 krusty, hire on. Type /help.'), lines.join('|'));
  assert.ok(lines.some((l) => l.includes('set hiring of the bots on')), lines.join('|'));
  assert.ok(h.room.lobby.greeting()[1].includes('I play; 1000 in battle buys my alliance for 45 s.'), h.room.lobby.greeting()[1]);
  a.send(build.lobbyChat(`Player${a.slot}: /bothire off`));
  a.take();
  assert.equal(h.room.botHire, false);
  a.send(build.lobbyChat(`Player${a.slot}: /help`));
  text = chatOf(a.takeCmds()).join(' ');
  assert.ok(text.includes('/bothire on|off'), text);
  a.send(build.lobbyChat(`Player${a.slot}: /bothire on`));
  h.room.reset();
  assert.equal(h.room.botHire, false);
  assert.equal(new Harness({ BOT_HIRE: true }).room.botHire, true);
});

test('bots have a random race: drawn per fake slot, MERCENARY_RACE pins it, bad values are refused', () => {
  const h = new Harness({ MIN_PLAYERS: 1 });
  assert.equal(h.cfg.MERCENARY_RACE, 'random');
  const seen = new Set();
  for (let i = 0; i < 40; i++) {
    h.room.reset();
    h.room.setBotCount(3);
    for (const f of h.room.fakeSlots()) {
      assert.ok(f.race === 0 || f.race === 1);
      seen.add(f.race);
    }
  }
  assert.deepEqual([...seen].sort(), [0, 1], 'both races occur');
  assert.ok(new Harness({ MERCENARY_RACE: 1 }).room.fakeSlots().every((f) => f.race === 1));
  assert.ok(new Harness({ MERCENARY_RACE: '0' }).room.fakeSlots().every((f) => f.race === 0));
  assert.throws(() => new Harness({ MERCENARY_RACE: 2 }), /MERCENARY_RACE/);
  assert.equal(h.cfg.MERCENARY_NAME, 'Mercenary');
  assert.deepEqual(h.cfg.FAKE_NAME_POOL.slice(0, 2), ['Mercenary', 'Marauder']);
});

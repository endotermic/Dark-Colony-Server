import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Harness, cmdsOf } from './helpers.js';
import { T, build } from '../src/commands.js';
import { STATE } from '../src/constants.js';
import { formatScenarioTitle } from '../src/config.js';

test('the scenario title follows the game format so that the lobby finds the player count at index 45', () => {
  const t = formatScenarioTitle('Armageddon', 8, 'desert');
  // literal captured from a real host by the previous server
  assert.equal(t, 'Armageddon\n                                 (8 Player Desert Map )');
  assert.equal(t.length, 66);
  assert.equal(t[44], '(');
  assert.equal(t[45], '8');
  assert.equal(formatScenarioTitle('Pond Thing', 2, 'jungle').slice(43), ' (2 Player Jungle Map )');
  assert.equal(formatScenarioTitle('X', 4, 'atlantis')[45], '4');
});

test('a joiner gets the version first, then the host dump, in one write', () => {
  const h = new Harness();
  const p = h.join('A');
  assert.equal(p.sock.out.length, 0); // everything was already read by the Peer constructor
  const cmds = p.takeCmds();
  assert.equal(cmds[0].type, T.VERSION);
  assert.equal(cmds[0].version, 15);
  assert.ok(cmds[0].id >= 1 && cmds[0].id <= 7);
  assert.equal(p.slot, cmds[0].id);
  assert.equal(cmds[1].type, T.SCENARIO);
  assert.equal(cmds[1].file, 'D8PLAY01.SCN');
  assert.ok(cmds[1].title.startsWith('Armageddon\n'));
  assert.equal(cmds[1].title[45], '8', 'player count digit where the lobby reads it');
  assert.equal(cmds[1].title, h.cfg.MAP_TITLE_WIRE);
  // Mercenary: slot 0, human, present-not-ready, colour 0
  assert.ok(cmds.some((c) => c.type === T.NAME && c.player === 0 && c.name === 'Mercenary'));
  assert.ok(cmds.some((c) => c.type === T.TYPE && c.player === 0 && c.value === 2));
  assert.ok(cmds.some((c) => c.type === T.READY && c.player === 0 && c.status === 1));
  assert.ok(cmds.some((c) => c.type === T.COLOUR_SET && c.player === 0 && c.value === 0));
  // the joiner: type 2, status 1, colour = slot
  assert.ok(cmds.some((c) => c.type === T.TYPE && c.player === p.slot && c.value === 2));
  assert.ok(cmds.some((c) => c.type === T.READY && c.player === p.slot && c.status === 1));
  assert.ok(cmds.some((c) => c.type === T.COLOUR_SET && c.player === p.slot && c.value === p.slot));
  // empty slots are type 3 / status 0
  const empty = [1, 2, 3, 4, 5, 6, 7].filter((s) => s !== p.slot);
  for (const s of empty) {
    assert.ok(cmds.some((c) => c.type === T.TYPE && c.player === s && c.value === 3));
    assert.ok(cmds.some((c) => c.type === T.READY && c.player === s && c.status === 0));
  }
  // all 16 VARs, CD flags all 1
  const vars = cmds.filter((c) => c.type === T.VAR);
  assert.equal(vars.length, 16);
  assert.ok(vars.filter((v) => v.index >= 8).every((v) => v.value === 1));
  assert.equal(vars.find((v) => v.index === 2).value, 1);
  // one private welcome line from Mercenary with the server version
  const welcome = cmds.filter((c) => c.type === T.LOBBY_CHAT && c.text.startsWith('Mercenary: Welcome'));
  assert.equal(welcome.length, 1);
  assert.ok(welcome[0].text.includes('Server 2.0') && welcome[0].text.includes('200%') && welcome[0].text.includes('READY'));
  // the first frame of the whole write was the version
  const firstPayload = p.all[0];
  assert.equal(firstPayload[0], T.VERSION);
});

test('slots 1..7 are handed out randomly and uniquely; the eighth connection is rejected', () => {
  const h = new Harness();
  const peers = [];
  for (let i = 0; i < 7; i++) peers.push(h.join(`P${i}`));
  const slots = peers.map((p) => p.slot).sort();
  assert.deepEqual(slots, [1, 2, 3, 4, 5, 6, 7]);
  const extra = h.join('Late');
  assert.equal(extra.slot, 7); // the placeholder slot number of a rejection
  assert.ok(extra.sock.ended);
  assert.equal(h.room.clients.size, 7);
  const cmds = extra.takeCmds();
  assert.equal(cmds[0].type, T.VERSION);
  assert.ok(cmds.some((c) => c.type === T.LOBBY_CHAT && c.text.includes('full')));
});

test('the injected random source picks the slot', () => {
  const h = new Harness();
  h.randomSeq = [3, 0];
  const a = h.join('A');
  assert.equal(a.slot, 4); // free = [1..7], index 3
  const b = h.join('B');
  assert.equal(b.slot, 1); // free = [1,2,3,5,6,7], index 0
});

test('existing players learn about a joiner', () => {
  const h = new Harness();
  const a = h.join('A');
  a.take();
  const b = h.join('B');
  const seenByA = a.takeCmds();
  assert.ok(seenByA.some((c) => c.type === T.TYPE && c.player === b.slot && c.value === 2));
  assert.ok(seenByA.some((c) => c.type === T.READY && c.player === b.slot && c.status === 1));
  assert.ok(!seenByA.some((c) => c.type === T.VERSION));
  assert.ok(!seenByA.some((c) => c.type === T.LOBBY_CHAT), 'the greeting goes to the newcomer only');
});

test('lobby policy: own slot only, host-owned settings dropped, CD flag rewritten', () => {
  const h = new Harness();
  const a = h.join('A');
  const b = h.join('B');
  a.take();
  b.take();

  a.send(build.variable(8 + a.slot, 0));
  let seen = b.takeCmds();
  assert.ok(seen.some((c) => c.type === T.VAR && c.index === 8 + a.slot && c.value === 1));

  a.send(build.name(b.slot, 'Evil'));
  assert.equal(b.takeCmds().length, 0);
  assert.equal(h.room.slots[b.slot].name, `Player${b.slot}`);

  a.send(build.name(a.slot, 'Alice'));
  seen = b.takeCmds();
  assert.ok(seen.some((c) => c.type === T.NAME && c.player === a.slot && c.name === 'Alice'));
  assert.equal(h.room.slots[a.slot].name, 'Alice');

  a.send(build.type(0, b.slot));
  a.send(build.colourSet(3, a.slot));
  a.send(build.scenario('D2PLAY01.SCN', 'x'));
  a.send(build.variable(4, 20));
  assert.equal(b.takeCmds().length, 0);
  assert.equal(h.room.slots[b.slot].type, 2);
  assert.equal(a.client.strikes, 0); // UI clicks are not violations

  a.send(build.teamCycle(1, a.slot));
  seen = b.takeCmds();
  assert.ok(seen.some((c) => c.type === T.TEAM_CYCLE && c.player === a.slot && c.value === 1));
  assert.equal(h.room.slots[a.slot].team, (a.slot + 1) % 8);

  a.send(build.race(1, a.slot));
  assert.equal(h.room.slots[a.slot].race, 1);
  assert.ok(b.takeCmds().some((c) => c.type === T.RACE && c.value === 1 && c.player === a.slot));
});

test('colour lock: cycling skips locked colours, READY on a taken colour is refused, a free colour makes it work', () => {
  const h = new Harness({ MIN_PLAYERS: 3 });
  h.randomSeq = [0, 0]; // A -> slot 1 (colour 1), B -> slot 2 (colour 2)
  const a = h.join('A');
  const b = h.join('B');
  assert.equal(a.slot, 1);
  assert.equal(b.slot, 2);
  a.take();
  b.take();
  // move A onto B's colour (B is not ready, so that is allowed)
  a.send(build.colourCycle(1, 1));
  assert.equal(h.room.slots[1].colour, 2);
  // B presses READY: locks colour 2
  b.send(build.ready(2, 2));
  assert.equal(h.room.slots[2].status, 2);
  // A presses READY: refused (server mirrors the client rule) but the message is still relayed
  a.send(build.ready(2, 1));
  assert.equal(h.room.slots[1].status, 1);
  assert.ok(b.takeCmds().some((c) => c.type === T.READY && c.player === 1 && c.status === 2));
  // A cycles by 7 (= -1) from colour 2: lands on 1
  a.send(build.colourCycle(7, 1));
  assert.equal(h.room.slots[1].colour, 1);
  // A cycles by 1 from colour 1: 2 is locked, so it lands on 3
  a.send(build.colourCycle(1, 1));
  assert.equal(h.room.slots[1].colour, 3);
  // with the free colour 3, READY is accepted and announced
  b.take();
  a.pressReady();
  const seen = b.takeCmds();
  assert.ok(seen.some((c) => c.type === T.READY && c.player === 1 && c.status === 2), 'A is now ready');
  assert.ok(seen.some((c) => c.type === T.LOBBY_CHAT && c.text.includes('is ready (2/2)')));
  assert.equal(h.room.slots[1].status, 2);
  // a ready slot ignores colour/team cycling
  a.send(build.colourCycle(1, 1));
  a.send(build.teamCycle(1, 1));
  assert.equal(b.takeCmds().length, 0);
});

test('READY from everybody starts the game; MREADY leads to RUNNING; first frame locks the speed', () => {
  const h = new Harness();
  const a = h.join('A');
  const b = h.join('B');
  a.take();
  b.take();
  // chat is only relayed, it carries no commands
  a.send(build.lobbyChat('A: ready'));
  let seen = b.takeCmds();
  assert.ok(seen.some((c) => c.type === T.LOBBY_CHAT && c.text === 'A: ready'));
  assert.ok(!seen.some((c) => c.type === T.READY));
  assert.equal(h.room.slots[a.slot].status, 1);

  a.pressReady();
  seen = b.takeCmds();
  assert.ok(seen.some((c) => c.type === T.READY && c.player === a.slot && c.status === 2));
  assert.ok(seen.some((c) => c.type === T.LOBBY_CHAT && c.text.includes('is ready (1/2)')));
  assert.equal(h.room.state, STATE.LOBBY);

  b.pressReady();
  seen = a.takeCmds();
  assert.ok(seen.some((c) => c.type === T.READY && c.player === b.slot && c.status === 2));
  const start = seen.find((c) => c.type === T.READY && c.player === 0 && c.status === 0);
  assert.ok(start, 'Mercenary leaves status 1 = start signal (status 0 bypasses the colour-lock check)');
  assert.ok(!seen.some((c) => c.type === T.READY && c.player === 0 && c.status === 2));
  assert.equal(h.room.state, STATE.STARTING);
  assert.equal(h.room.slots[0].status, 0);

  // the game reports its shuffled game player index, not the lobby slot (seen live: slot 5 -> player 1)
  a.send(build.mready((a.slot + 3) % 8, 2));
  assert.equal(h.room.state, STATE.STARTING);
  assert.equal(a.client.gamePlayer, (a.slot + 3) % 8);
  b.send(build.mready(0, 2));
  assert.equal(h.room.state, STATE.RUNNING);

  a.take();
  b.take();
  h.stepAfter(33);
  const fa = a.take();
  const fb = b.take();
  assert.equal(fa.length, 1);
  assert.ok(fa[0].equals(fb[0]), 'identical sync payload for everyone');
  const cmds = cmdsOf(fa[0]);
  assert.equal(cmds[0].type, T.UNTIL);
  assert.equal(cmds[0].a, 0);
  assert.equal(cmds[0].until, 9);
  assert.equal(cmds[1].type, T.TICK_SPEED);
  assert.equal(cmds[1].ms, 33);
  assert.equal(cmds.length, 2);
});

test('MIN_PLAYERS holds the start; the countdown can be cancelled by un-readying', () => {
  const h = new Harness({ START_COUNTDOWN_S: 2 });
  const peers = [];
  // let time pass while every live peer keeps sending keep-alives, ticking the watchdog
  const pass = (ms) => {
    for (let left = ms; left > 0; left -= 500) {
      h.advance(Math.min(500, left));
      for (const p of peers) if (!p.gone) p.keepalive();
      h.tick();
    }
  };
  const a = h.join('A');
  peers.push(a);
  a.take();
  a.pressReady();
  assert.equal(h.room.state, STATE.LOBBY);
  pass(5000);
  assert.equal(h.room.state, STATE.LOBBY, 'one player cannot start');
  assert.ok(!a.gone);

  const b = h.join('B');
  peers.push(b);
  a.take();
  b.take();
  b.pressReady();
  assert.ok(a.takeCmds().some((c) => c.type === T.LOBBY_CHAT && c.text.includes('starting in 2 s')));
  pass(1000);
  assert.equal(h.room.state, STATE.LOBBY);
  a.send(build.ready(1, a.slot));
  assert.ok(b.takeCmds().some((c) => c.type === T.LOBBY_CHAT && c.text.includes('start cancelled')));
  pass(2000);
  assert.equal(h.room.state, STATE.LOBBY);

  a.send(build.ready(2, a.slot)); // the button counts too
  pass(2000);
  assert.equal(h.room.state, STATE.STARTING);
});

test('a bad MREADY evicts; a second one is a bad MREADY', () => {
  const h = new Harness();
  const a = h.join('A');
  const b = h.join('B');
  a.take();
  b.take();
  a.pressReady();
  b.pressReady();
  assert.equal(h.room.state, STATE.STARTING);
  a.send(build.mready(a.slot, 2));
  a.send(build.mready(a.slot, 2));
  assert.ok(a.gone, 'duplicate MREADY evicts');
  // with A gone, B alone completes the start as soon as it reports
  b.send(build.mready(b.slot, 2));
  assert.equal(h.room.state, STATE.RUNNING);
  b.take();
  h.stepAfter(33);
  const cmds = cmdsOf(b.take()[0]);
  assert.equal(cmds[0].type, T.UNTIL);
  assert.ok(cmds.some((c) => c.type === T.DISCONNECT && c.player === a.slot), 'DISCONNECT rides in the first sync frame');
});

test('INIT_ME re-sends the dump without the version; a second INIT_ME is a strike', () => {
  const h = new Harness();
  const a = h.join('A');
  a.take();
  a.send(build.initMe(a.slot));
  const cmds = a.takeCmds();
  assert.ok(!cmds.some((c) => c.type === T.VERSION));
  assert.equal(cmds[0].type, T.SCENARIO);
  a.send(build.initMe(a.slot));
  assert.equal(a.client.strikes, 1);
});

test('a joiner while a battle runs is told so and dropped', () => {
  const h = new Harness();
  const a = h.join('A');
  const b = h.join('B');
  for (const p of [a, b]) {
    p.take();
    p.pressReady();
  }
  for (const p of [a, b]) p.send(build.mready(p.slot, 2));
  assert.equal(h.room.state, STATE.RUNNING);
  const late = h.join('Late');
  assert.ok(late.sock.ended);
  const cmds = late.takeCmds();
  assert.equal(cmds[0].type, T.VERSION);
  assert.ok(cmds.some((c) => c.type === T.LOBBY_CHAT && c.text.includes('battle is in progress')));
  assert.equal(h.room.clients.size, 2);
});

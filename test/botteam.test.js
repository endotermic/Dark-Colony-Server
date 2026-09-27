// `/botteam N` in the lobby chat (27 Sep 2026, maintainer: "sets for each client N bots which are always
// teamed with client with shared vision until either player looses connection or game ends"; plan §19.11):
// every real player gets N fake humans on its lobby team, announced like a joiner's dump, following its
// team cycles and leaving with it; in battle they are the player's allies with shared vision (four 0x0D in
// the first frame), not for hire, and the bond ends when the player's connection is lost.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Harness, cmdsOf, answerSync } from './helpers.js';
import { T, build } from '../src/commands.js';
import { STATE } from '../src/constants.js';
import { P, playerAddr } from '../src/engine/mem.js';

const chatOf = (cmds) => cmds.filter((c) => c.type === T.LOBBY_CHAT).map((c) => c.text);
const chats = (cmds) => cmds.filter((c) => c.type === T.CHAT).map((c) => c.text);
const diplo = (cmds) => cmds.filter((c) => c.type === T.DIPLOMACY).map((c) => [...c.raw.subarray(1)]);
/** The four 0x0D of an alliance between game players a and b, both matrices, both directions, `on`. */
const relations = (a, b, on) => [[a, b, 0, on], [a, b, 1, on], [b, a, 0, on], [b, a, 1, on]];

/** A faked engine: identity start shuffle (slot = game player), money, a checksum per tick. */
function fakeEngine() {
  return {
    loadMapJson: () => ({ name: 'fake' }),
    createGame(mapJson, lobby) {
      const gs = Buffer.alloc(0x8000);
      gs.writeInt32LE(41, 0x7d40);
      for (let p = 0; p < 8; p++) {
        gs.write(`P${p}`, playerAddr(p) + P.NAME, 'latin1');
        gs.writeInt32LE(1500, playerAddr(p) + P.MONEY);
      }
      return {
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
        },
      };
    },
  };
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

test('/botteam 2 with two players: two team bots each, on the owner\'s team, announced like a dump; /botcount counts the free bots only', () => {
  const h = new Harness({ MIN_PLAYERS: 1 });
  const a = h.join('A');
  const b = h.join('B');
  a.take();
  b.take();
  a.send(build.lobbyChat(`Player${a.slot}: /botteam 2`));
  assert.equal(h.room.botTeam, 2);
  assert.equal(h.room.fakeSlots().length, 5, 'the master bot + 2 + 2');
  assert.equal(h.room.freeBots().length, 1);
  assert.equal(h.room.botCount, 1, '/botcount is untouched');
  for (const p of [a, b]) {
    const mine = h.room.teamBotsOf(p.slot);
    assert.equal(mine.length, 2);
    assert.ok(mine.every((f) => f.team === h.room.slots[p.slot].team && f.type === 2 && f.status === 1 && f.owner === p.slot), `team bots of ${p.slot} share its team`);
  }
  const names = h.room.fakeSlots().map((f) => f.name);
  assert.equal(new Set(names).size, names.length, 'distinct names');
  assert.equal(h.room.bots.list.length, 5);
  assert.deepEqual(h.room.bots.list.filter((x) => x.owner === a.slot).length, 2);
  for (const p of [a, b]) {
    const cmds = p.takeCmds();
    for (const f of h.room.fakeSlots().filter((x) => x.owner >= 0)) {
      const iColour = cmds.findIndex((c) => c.type === T.COLOUR_SET && c.player === f.slot);
      const iType = cmds.findIndex((c) => c.type === T.TYPE && c.player === f.slot && c.value === 2);
      assert.ok(iColour >= 0 && iType > iColour, `slot ${f.slot}: colour, then type`);
      assert.ok(cmds.some((c) => c.type === T.TEAM_SET && c.player === f.slot && c.value === f.team), `slot ${f.slot}: the owner's team`);
      assert.ok(cmds.some((c) => c.type === T.NAME && c.player === f.slot && c.name === f.name));
    }
    assert.ok(chatOf(cmds).join(' ').includes(`Player${a.slot} set the team bots to 2 per player`));
  }
  // /botcount adds free bots beside them, within what is left (8 - 2 players - 4 team bots = 2)
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 3`));
  assert.ok(chatOf(a.takeCmds()).join(' ').includes('at most 2 bots fit'));
  a.send(build.lobbyChat(`Player${a.slot}: /botcount 2`));
  a.take();
  assert.equal(h.room.freeBots().length, 2);
  assert.equal(h.room.fakeSlots().length, 6);
  assert.equal(h.room.teamBotsOf(a.slot).length, 2, 'the team bots stay');
  assert.ok(h.room.isFull());
  // /botteam alone reports; /botteam 0 removes them all with lobby DISCONNECTs
  a.send(build.lobbyChat(`Player${a.slot}: /botteam`));
  assert.ok(chatOf(a.takeCmds()).join(' ').includes('2 team bots per player'));
  const teamSlots = h.room.fakeSlots().filter((f) => f.owner >= 0).map((f) => f.slot).sort();
  a.send(build.lobbyChat(`Player${a.slot}: /botteam 0`));
  const gone = b.takeCmds().filter((c) => c.type === T.DISCONNECT).map((c) => c.player).sort();
  assert.deepEqual(gone, teamSlots);
  assert.equal(h.room.fakeSlots().length, 2, 'the two free bots (the master bot is one of them) stay');
  assert.equal(h.room.bots.list.length, 2);
});

test('a later joiner gets its team bots inside its own dump; the owner\'s team cycle moves them; leaving the lobby takes them along', () => {
  const h = new Harness({ MIN_PLAYERS: 1 });
  const a = h.join('A');
  a.take();
  a.send(build.lobbyChat(`Player${a.slot}: /botteam 1`));
  a.take();
  const c = h.join('C');
  const mine = h.room.teamBotsOf(c.slot);
  assert.equal(mine.length, 1);
  const cmds = c.takeCmds();
  assert.ok(cmds.some((x) => x.type === T.TEAM_SET && x.player === mine[0].slot && x.value === h.room.slots[c.slot].team), 'the joiner\'s dump carries its bot on its team');
  assert.ok(a.takeCmds().some((x) => x.type === T.NAME && x.player === mine[0].slot && x.name === mine[0].name), 'the others see it too');
  assert.equal(h.room.bots.list.length, 3);
  // the owner cycles its team: the bot follows with an absolute 'n'
  c.send(build.teamCycle(1, c.slot));
  const team = h.room.slots[c.slot].team;
  assert.equal(team, (c.slot + 1) % 8);
  assert.equal(h.room.slots[mine[0].slot].team, team);
  assert.ok(a.takeCmds().some((x) => x.type === T.TEAM_SET && x.player === mine[0].slot && x.value === team), 'the bot\'s new team is broadcast');
  // the owner leaves the lobby: its bot leaves with it
  c.sock.destroy();
  const left = a.takeCmds().filter((x) => x.type === T.DISCONNECT).map((x) => x.player).sort();
  assert.deepEqual(left, [c.slot, mine[0].slot].sort());
  assert.equal(h.room.teamBotsOf(c.slot).length, 0);
  assert.equal(h.room.fakeSlots().length, 2, 'the master bot and A\'s bot');
  assert.equal(h.room.bots.list.length, 2);
});

test('limits: 0..6, and no more per player than the map leaves; the setting resets with the room', () => {
  const h = new Harness({ MIN_PLAYERS: 1 });
  const a = h.join('A');
  a.take();
  for (const bad of ['7', '-1', 'x']) {
    a.send(build.lobbyChat(`Player${a.slot}: /botteam ${bad}`));
    assert.ok(chatOf(a.takeCmds()).join(' ').includes('the count must be 0..6'), bad);
  }
  a.send(build.lobbyChat(`Player${a.slot}: /botteam 6`));
  a.take();
  assert.equal(h.room.teamBotsOf(a.slot).length, 6, 'one player + the master bot + 6 fill an 8-player map');
  assert.ok(h.room.isFull());
  assert.equal(h.room.minPlayers, 1);
  a.send(build.lobbyChat(`Player${a.slot}: /botteam 0`));
  a.take();
  const b = h.join('B');
  b.take();
  a.send(build.lobbyChat(`Player${a.slot}: /botteam 3`));
  const text = chatOf(a.takeCmds()).join(' ');
  assert.ok(text.includes('at most 2 team bots per player fit on this map with 2 players and 1 free bot'), text);
  assert.equal(h.room.botTeam, 0);
  a.send(build.lobbyChat(`Player${a.slot}: /botteam 2`));
  a.take();
  assert.equal(h.room.fakeSlots().length, 5);
  a.send(build.lobbyChat(`Player${a.slot}: /help`));
  assert.ok(chatOf(a.takeCmds()).some((l) => l === '/botteam N allied bots per player'));
  h.room.reset();
  assert.equal(h.room.botTeam, 0, 'back to BOT_TEAM');
  assert.equal(h.room.fakeSlots().length, 1);
});

test('in battle a team bot allies its player with shared vision in the first frame, refuses hire, and turns free when the player is gone', () => {
  const h = new Harness({ SYNC_CHECK: 'shadow', BOT_HIRE: true }, { engine: fakeEngine() });
  const a = h.join('A');
  const b = h.join('B');
  for (const p of [a, b]) {
    p.take();
    p.cdReport();
  }
  a.send(build.lobbyChat(`Player${a.slot}: /botteam 1`));
  const botA = h.room.teamBotsOf(a.slot)[0];
  const botB = h.room.teamBotsOf(b.slot)[0];
  for (const p of [a, b]) p.take();
  for (const p of [a, b]) p.pressReady();
  assert.equal(h.room.state, STATE.STARTING);
  for (const p of [a, b]) p.send(build.mready(p.slot, 2)); // identity shuffle: game player = slot
  assert.equal(h.room.state, STATE.RUNNING);
  for (const p of [a, b]) p.take();
  const A = h.room.bots.list.find((x) => x.slot === botA.slot);
  const B = h.room.bots.list.find((x) => x.slot === botB.slot);
  assert.ok(A.active && B.active);
  assert.equal(A.bond.player, a.slot);
  assert.equal(B.bond.player, b.slot);
  assert.ok(A.isAlly(a.slot) && !A.isAlly(b.slot) && !A.isAlly(B.player), 'allied with its player only');
  assert.equal(A.alliedPlayer, a.slot);
  // the first frame: alliance + vision both ways for each team bot, and a word to the player
  const first = frame(h, a, b);
  const d = diplo(first);
  for (const rel of relations(A.player, a.slot, 1)) assert.ok(d.some((x) => x.join() === rel.join()), `relation ${rel} of A's bot`);
  for (const rel of relations(B.player, b.slot, 1)) assert.ok(d.some((x) => x.join() === rel.join()), `relation ${rel} of B's bot`);
  const lines = chats(first);
  assert.ok(lines.some((l) => l.startsWith(`${botA.name}: Player${a.slot}, I fight at your side: allied, shared eyes`)), lines.join('|'));
  assert.ok(!lines.some((l) => l.includes('Same deal here')), 'a team bot makes no offer');
  const toA = first.filter((c) => c.type === T.CHAT && c.text.startsWith(`${botA.name}:`));
  assert.ok(toA.every((c) => c.mask === 1 << a.slot), 'said to its player only');
  // B pays A's bot: the 1000 comes back, no alliance
  b.send(build.bonus(A.player));
  const after = frame(h, a, b); // the payment and the bot's answer ride the same frame (onGift runs on receipt)
  assert.ok(after.some((c) => c.type === T.BONUS && c.player === A.player), 'the payment is relayed');
  assert.ok(after.some((c) => c.type === T.BONUS && c.player === b.slot), 'refund');
  assert.ok(chats(after).some((l) => l.includes(`I am Player${a.slot}'s team bot for this whole battle`)), chats(after).join('|'));
  assert.equal(A.ally, null);
  assert.equal(A.bond.player, a.slot, 'the bond stands');
  // A loses the connection: its bot ends the bond (relations off) and plays free; B's bond is untouched; no DISCONNECT (R14)
  a.sock.destroy();
  h.stepAfter(33);
  const cmds = cmdsOf(b.take()[0]);
  assert.ok(!cmds.some((c) => c.type === T.DISCONNECT), 'no DISCONNECT in battle');
  const off = diplo(cmds);
  for (const rel of relations(A.player, a.slot, 0)) assert.ok(off.some((x) => x.join() === rel.join()), `relation ${rel} ended`);
  assert.ok(chats(cmds).some((l) => l === `${botA.name}: Player${a.slot} is gone. I fight for myself now.`), chats(cmds).join('|'));
  assert.equal(A.bond, null);
  assert.ok(A.active, 'the bot goes on playing');
  assert.ok(!A.isAlly(a.slot));
  assert.equal(B.bond.player, b.slot);
  assert.ok(h.room.bots.list.some((x) => x.takeover && x.slot === a.slot), "A's base got a takeover bot");
});

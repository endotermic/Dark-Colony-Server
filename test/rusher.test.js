// The Mercenary's rusher against the real engine (plan §19.8): headless self-play on Armageddon.
// The rusher's commands are applied to the engine at once (a client would execute them one frame
// later); the engine must accept every one of them without an assert, and the plan must unfold:
// worker -> vent -> barracks -> infantry -> an assault wave at the nearest enemy HQ, with a chat
// line for every decision and no trace left in the engine's RNG or checksum history.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createGame, loadMapJson } from '../src/engine/index.js';
import { Rusher, ORDER_ASSAULT, ORDER_MOVE } from '../src/rusher.js';
import { splitCommands, T, decode } from '../src/commands.js';
import { GS, O, P, objAddr, playerAddr, u8, u16, i32 } from '../src/engine/mem.js';

function lobby(humans = [0, 3]) {
  const slots = [];
  for (let s = 0; s < 8; s++) {
    const h = humans.includes(s);
    slots.push({ type: h ? 2 : 3, race: 0, colour: s, team: s, name: h ? `P${s}` : '' });
  }
  return { slots, localSlot: -1, titleDigit: 8 };
}

function newGame(asserts = []) {
  return createGame(loadMapJson('D8PLAY01'), lobby(), { assert: (msg, g) => asserts.push({ tick: g.tick, msg }) });
}

/** Run `ticks` ticks of self-play; returns every command (decoded), every group and every line with its tick. */
function selfPlay(G, r, ticks, every = 32, from = 0) {
  const log = { commands: [], groups: [], lines: [] };
  for (let t = from; t < from + ticks; t++) {
    if (t > 0 && t % every === 0) {
      const out = r.think(t);
      for (const buf of out.commands) {
        const cmds = splitCommands(buf).map((c) => ({ tick: t, type: c.type, raw: Buffer.from(c.raw), ...decode(c) }));
        log.commands.push(...cmds);
        log.groups.push({ tick: t, cmds });
        for (const c of splitCommands(buf)) G.applyCommand(c.raw); // one command at a time, as the engine feed does
      }
      for (const l of out.lines) log.lines.push({ tick: t, text: l });
    }
    G.step();
  }
  return log;
}

/** The 0x07 waypoint command of every group that carries assault orders. */
const assaultWaypoints = (log) =>
  log.groups.filter((g) => g.cmds.some((c) => c.type === T.ORDER && c.raw[3] === ORDER_ASSAULT)).map((g) => g.cmds.find((c) => c.type === T.WAYPOINTS_OBJ));

const money = (G, p) => i32(G.gs, playerAddr(p) + P.MONEY);
const slotHp = (G, p, s) => i32(G.gs, playerAddr(p) + P.SLOT_HP + 4 * s);

function ownUnits(G, p) {
  const out = [];
  const maxObj = i32(G.gs, GS.MAX_OBJ);
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    const life = u8(G.gs, a + O.LIFE);
    if (life === 0 || life === 10 || u8(G.gs, a + O.TEAM) !== p) continue;
    out.push({ obj: o, type: u8(G.gs, a + O.TYPE), x: u16(G.gs, a + O.X), z: u16(G.gs, a + O.Z) });
  }
  return out;
}

test('opening: a worker first (all the start money), said aloud, engine untouched except the ledger', () => {
  const asserts = [];
  const G = newGame(asserts);
  const p = G.scenario.slotToPlayer[0];
  assert.equal(p, 1);
  const r = new Rusher(G, p);
  assert.equal(money(G, p), 1500);
  for (let t = 0; t < 32; t++) G.step(); // the HQ's drop pod must land first (slot blocked at tick 0)
  const before = money(G, p); // 1500 plus the base income of two 16-tick periods
  const rand0 = G.randIndex;
  const hist0 = Buffer.from(G.gs.subarray(GS.HISTORY, GS.HISTORY + 512));
  const out = r.think(32);
  assert.equal(G.randIndex, rand0, 'a think draws no rand()');
  assert.ok(hist0.equals(G.gs.subarray(GS.HISTORY, GS.HISTORY + 512)), 'a think leaves the checksum history alone');
  const cmds = out.commands.flatMap((b) => splitCommands(b)).map((c) => ({ type: c.type, raw: c.raw }));
  assert.equal(cmds.length, 1);
  assert.equal(cmds[0].type, T.BUILD);
  assert.deepEqual([...cmds[0].raw], [T.BUILD, 6, p, 1], 'one EXPL worker for player 1');
  assert.equal(money(G, p), before - 1500, 'the ledger paid for it');
  assert.ok(out.lines.some((l) => l.startsWith('A worker first')), out.lines.join(' | '));
  assert.ok(out.lines.some((l) => l.startsWith('Gathering a strike force: 2 of 4')), out.lines.join(' | '));
  assert.equal(asserts.length, 0);
});

test('self-play: worker mines, barracks, infantry, then the rush on the nearest enemy HQ', () => {
  const asserts = [];
  const G = newGame(asserts);
  const p = G.scenario.slotToPlayer[0];
  const enemy = G.scenario.slotToPlayer[3];
  const r = new Rusher(G, p);
  const enemyBuildings = r.buildings(enemy).map((b) => b.x); // HQ and tower of the idle enemy
  const log = selfPlay(G, r, 6000);
  assert.equal(asserts.length, 0, JSON.stringify(asserts.slice(0, 3)));

  // the worker walked to a vent (move order) and became a mining tower
  const moves = log.commands.filter((c) => c.type === T.ORDER && c.raw[3] === ORDER_MOVE);
  assert.ok(moves.length >= 1, 'a move order for the worker');
  const towers = ownUnits(G, p).filter((u) => u.type === 0x2f);
  assert.ok(towers.length >= 1, 'a mining tower stands on a vent');
  assert.ok(log.lines.some((l) => l.text.startsWith('Worker to the vent at')));

  // money came in, the barracks went up, infantry was queued
  const barracks = log.commands.find((c) => c.type === T.RESEARCH);
  assert.ok(barracks, 'a 0x09 for the barracks');
  assert.deepEqual([...barracks.raw], [T.RESEARCH, 1, 0, p]);
  assert.notEqual(slotHp(G, p, 1), 0, 'the barracks stands');
  const infantry = log.commands.filter((c) => c.type === T.BUILD && c.raw[1] === 0);
  assert.ok(infantry.length >= 2, `troopers were ordered (${infantry.length} orders)`);
  assert.ok(log.lines.some((l) => /^Building a barracks\./.test(l.text)));
  assert.ok(log.lines.some((l) => /^Training/.test(l.text)));
  assert.ok(money(G, p) >= 0, 'the ledger never goes negative');

  // the first wave: assault orders with the enemy HQ as the waypoint
  const waves = assaultWaypoints(log);
  assert.ok(waves.length >= 1, 'a 0x07 waypoint group with assault orders');
  const hq = objAddr(enemy * 15);
  assert.equal(waves[0].raw.readUInt16LE(4), u16(G.gs, hq + O.X));
  assert.equal(waves[0].raw.readUInt16LE(6), u16(G.gs, hq + O.Z));
  const assaults = log.commands.filter((c) => c.type === T.ORDER && c.raw[3] === ORDER_ASSAULT);
  assert.ok(assaults.length >= r.o.rushSize, `assault orders for the strike force (${assaults.length})`);
  const rushLine = log.lines.find((l) => l.text.startsWith('RUSH!'));
  assert.ok(rushLine, 'the rush is announced');
  assert.ok(rushLine.text.includes(`player ${enemy}'s base`), rushLine.text); // no nameOf given: the default name
  assert.ok(r.waves >= 1);
  // every later wave aims at a building of that enemy (the tower once the HQ has fallen); the second
  // worker follows the first wave
  for (const w of waves) assert.ok(enemyBuildings.includes(w.raw.readUInt16LE(4)), `waypoint x ${w.raw.readUInt16LE(4)}`);
  const workers = log.commands.filter((c) => c.type === T.BUILD && c.raw[1] === 6);
  assert.equal(workers.length, 2, 'two workers bought');
  assert.ok(workers[1].tick > log.lines.find((l) => l.text.startsWith('RUSH!')).tick, 'the second after the first wave');
});

test('allies are never attacked: with the only enemy allied the force gathers but never leaves', () => {
  const asserts = [];
  const G = newGame(asserts);
  const p = G.scenario.slotToPlayer[0];
  const enemy = G.scenario.slotToPlayer[3];
  const r = new Rusher(G, p, { isAlly: (q) => q === enemy, nameOf: (q) => `player ${q}` });
  const log = selfPlay(G, r, 4000);
  assert.equal(asserts.length, 0);
  assert.equal(log.commands.filter((c) => c.type === T.ORDER && c.raw[3] === ORDER_ASSAULT).length, 0, 'no assault order');
  assert.ok(!log.lines.some((l) => l.text.startsWith('RUSH!')));
  assert.equal(r.target, -1);
});

test('when the target becomes an ally the troops are recalled and the Mercenary stands down', () => {
  const asserts = [];
  const G = newGame(asserts);
  const p = G.scenario.slotToPlayer[0];
  const enemy = G.scenario.slotToPlayer[3];
  let allied = false;
  const r = new Rusher(G, p, { isAlly: (q) => allied && q === enemy, nameOf: (q) => `player ${q}` });
  const log = selfPlay(G, r, 6000);
  assert.ok(log.lines.some((l) => l.text.startsWith('RUSH!')), 'a wave went out first');
  allied = true;
  const after = selfPlay(G, r, 64, 32, 6000);
  assert.equal(asserts.length, 0);
  const recall = after.commands.filter((c) => c.type === T.ORDER && c.raw[3] === ORDER_MOVE);
  assert.ok(recall.length >= 1, 'move orders home');
  assert.ok(after.lines.some((l) => l.text.includes('is my ally now')), after.lines.map((l) => l.text).join(' | '));
  assert.equal(r.target, -1);
  assert.equal(r.sent.size, 0);
});

test('defence: two rushers against each other, the attacked base turns its soldiers on the intruders', () => {
  // Both human slots rush; whoever's wave arrives first is met at the other's base. The rush pauses
  // while intruders stand there, the defenders assault the nearest one near the own HQ, and the
  // base returns to the plan once it is clear.
  const asserts = [];
  const G = newGame(asserts);
  const p = G.scenario.slotToPlayer[0];
  const enemy = G.scenario.slotToPlayer[3];
  // the first keeps its soldiers at home (a strike force it never reaches), the second rushes it
  const rushers = [new Rusher(G, p, { nameOf: (q) => `player ${q}`, rushSize: 99 }), new Rusher(G, enemy, { nameOf: (q) => `player ${q}` })];
  const logs = rushers.map(() => ({ groups: [], lines: [] }));
  for (let t = 0; t < 9000; t++) {
    if (t > 0 && t % 32 === 0) {
      rushers.forEach((r, i) => {
        const out = r.think(t + i * 8);
        for (const buf of out.commands) {
          const cmds = splitCommands(buf).map((c) => ({ tick: t, type: c.type, raw: Buffer.from(c.raw), ...decode(c) }));
          logs[i].groups.push({ tick: t, cmds });
          for (const c of splitCommands(buf)) G.applyCommand(c.raw);
        }
        for (const l of out.lines) logs[i].lines.push({ tick: t, text: l });
      });
    }
    G.step();
  }
  assert.equal(asserts.length, 0, JSON.stringify(asserts.slice(0, 3)));
  assert.ok(rushers[0].defenses >= 1, `the home guard defended (waves ${rushers.map((r) => r.waves)})`);
  const defended = rushers.map((r, i) => ({ r, i, log: logs[i] })).filter((x) => x.r.defenses > 0);
  for (const { r, i, log } of defended) {
    const alarm = log.lines.find((l) => l.text.startsWith('Intruders!'));
    assert.ok(alarm, 'the alarm is raised');
    assert.ok(alarm.text.includes(`player ${i === 0 ? enemy : p}'s units at my base`), alarm.text);
    // defence orders: assault groups issued while defending aim within defendRadius of the own home
    const [hx, hz] = r.home();
    const r2 = (r.o.defendRadius * 256 + 2 * 256) ** 2;
    const alarmTick = alarm.tick;
    const clear = log.lines.find((l) => l.tick > alarmTick && l.text.startsWith('My base is clear'));
    const end = clear ? clear.tick : Infinity;
    const groups = log.groups.filter((g) => g.tick >= alarmTick && g.tick < end && g.cmds.some((c) => c.type === T.ORDER && c.raw[3] === ORDER_ASSAULT));
    if (i === 0) assert.ok(groups.length >= 1, 'the home guard was ordered against the intruders');
    for (const g of groups) {
      const wp = g.cmds.find((c) => c.type === T.WAYPOINTS_OBJ);
      const x = wp.raw.readUInt16LE(4);
      const z = wp.raw.readUInt16LE(6);
      assert.ok((x - hx) * (x - hx) + (z - hz) * (z - hz) <= r2, `defence waypoint ${x >> 8},${z >> 8} near home ${hx >> 8},${hz >> 8}`);
    }
    // no wave left the base while it was under attack
    assert.ok(!log.lines.some((l) => l.tick >= alarmTick && l.tick < end && /^(RUSH!|\d+ more)/.test(l.text)), 'the rush paused');
  }
});

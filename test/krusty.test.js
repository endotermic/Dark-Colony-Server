// The Krusty port (src/engine/krusty.js, DC16_AI.md) against the real engine: headless self-play
// on Armageddon in BOT mode (a private RNG, commands applied to the engine one frame later than the
// original would), and the EXACT mode inside game_tick (engine/ai.js) for a computer lobby slot.
// The engine must accept every command without an assert, the game plan must unfold (worker, vent,
// barracks, army, guards, attack groups), and in bot mode the engine's RNG and checksum history
// must stay untouched by a think.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createGame, loadMapJson } from '../src/engine/index.js';
import * as Krusty from '../src/engine/krusty.js';
import * as Ai from '../src/engine/ai.js';
import { KrustyBot } from '../src/krustybot.js';
import { splitCommands, T, decode, build } from '../src/commands.js';
import { GS, O, P, objAddr, playerAddr, u8, i32 } from '../src/engine/mem.js';

function lobby(humans = [0, 3], ai = []) {
  const slots = [];
  for (let s = 0; s < 8; s++) {
    const h = humans.includes(s);
    const a = ai.includes(s);
    slots.push({ type: h ? 2 : a ? 0 : 3, race: s === 3 ? 1 : 0, colour: s, team: s, name: h ? `P${s}` : '' });
  }
  return { slots, localSlot: -1, titleDigit: 8 };
}

function newGame(asserts = [], humans, ai) {
  return createGame(loadMapJson('D8PLAY01'), lobby(humans, ai), { assert: (msg, g) => asserts.push({ tick: g.tick, msg }) });
}

const money = (G, p) => i32(G.gs, playerAddr(p) + P.MONEY);
const slotHp = (G, p, s) => i32(G.gs, playerAddr(p) + P.SLOT_HP + 4 * s);

function ownUnits(G, p) {
  const out = [];
  const maxObj = i32(G.gs, GS.MAX_OBJ);
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    const life = u8(G.gs, a + O.LIFE);
    if (life === 0 || life === 10 || u8(G.gs, a + O.TEAM) !== p) continue;
    out.push({ obj: o, type: u8(G.gs, a + O.TYPE), name: G.tables.types[u8(G.gs, a + O.TYPE)].name });
  }
  return out;
}

/** Self-play: every bot thinks at the original's phase (4 + 4p mod 32); its commands go straight into the engine. */
function selfPlay(G, bots, ticks, from = 1) {
  const log = { commands: [], lines: [], seenTypes: new Map() }; // seenTypes: player -> Set of unit types ever fielded
  for (let t = from; t < from + ticks; t++) {
    for (const b of bots) {
      if (t % 32 !== (4 + 4 * b.p) % 32) continue;
      const seen = log.seenTypes.get(b.p) ?? new Set();
      for (const u of ownUnits(G, b.p)) seen.add(u.type);
      log.seenTypes.set(b.p, seen);
      const out = b.think(t);
      for (const buf of out.commands) {
        for (const c of splitCommands(buf)) {
          log.commands.push({ tick: t, p: b.p, type: c.type, raw: Buffer.from(c.raw), ...decode(c) });
          G.applyCommand(c.raw);
        }
      }
      for (const l of out.lines) log.lines.push({ tick: t, p: b.p, text: l });
    }
    G.step();
  }
  return log;
}

test('krusty_alloc: zone table with hop distances and centres, tasks, goals and tunables as the original', () => {
  const G = newGame();
  const p = G.scenario.slotToPlayer[0];
  const kai = Krusty.krustyAlloc(G, p);
  assert.equal(kai.length, Krusty.KAI_SIZE);
  assert.equal(kai[Krusty.K.FIRST_RUN], 1);
  assert.equal(kai.readInt32LE(Krusty.K.SPLIT), 0xc0);
  assert.deepEqual([...Array(7)].map((_, k) => kai.readInt32LE(Krusty.K.WEIGHTS + 4 * k)), [1, 1, 2, 4, 2, 4, 2]);
  assert.equal(kai.readInt32LE(Krusty.K.RATIO), 0, 'the attack ratio is the zero-filled pool memory');
  assert.equal(kai[Krusty.K.SEE_THRU + p], 1);
  assert.equal([...kai.subarray(Krusty.K.SEE_THRU, Krusty.K.SEE_THRU + 8)].filter(Boolean).length, 1);
  // memory table: every record's type is -1
  for (let o = 0; o < 800; o++) assert.equal(kai.readInt8(Krusty.memAddr(o) + 2), -1);
  // zones: the home zone has hop 0, its neighbours hop 1, every reachable zone a centre inside the map
  const st = Krusty.summarize(G, kai);
  assert.ok(st.zones > 100, `zones reachable: ${st.zones}`);
  let home = -1;
  for (let z = 1; z < 255; z++) {
    const hop = kai[Krusty.zoneAddr(z) + Krusty.Z.HOP];
    if (hop === 0) home = z;
    if (hop === 0xff) continue;
    const cx = kai[Krusty.zoneAddr(z) + Krusty.Z.CX];
    const cz = kai[Krusty.zoneAddr(z) + Krusty.Z.CZ];
    assert.ok(cx < G.map.w && cz < G.map.h, `centre of zone ${z} inside the map`);
    assert.equal(G.map.path.familyAt(cx, cz), z, `centre of zone ${z} lies in its own family`);
  }
  assert.ok(home > 0, 'a home zone');
  // CLIST2 lists the distinct next hops of a zone, the zone itself included (next[z][z] = z)
  const nn = G.map.path.clist2[home * 32];
  for (let k = 1; k <= nn; k++) {
    const n = G.map.path.clist2[home * 32 + k];
    assert.equal(kai[Krusty.zoneAddr(n) + Krusty.Z.HOP], n === home ? 0 : 1, `zone ${n} next to home ${home}`);
  }
  // tasks: the defend home guard and two attack groups in state 2 exist, tasks 0 and 3 have group 0
  assert.deepEqual(st.tasks.map((t) => t.groups.length), [1, 1, 2, 1]);
  assert.deepEqual(st.tasks[2].groups.map((g) => g.state), [2, 2]);
  assert.equal(st.tasks[1].groups[0].zone, st.tasks[1].groups[0].dest);
  // goals: the 18 defaults
  for (let g = 0; g < 18; g++) {
    assert.equal(kai.readInt32LE(Krusty.K.GOALS + 12 * g), Krusty.DEFAULT_GOALS[g][0]);
    assert.equal(kai.readInt32LE(Krusty.K.GOALS + 12 * g + 8), Krusty.DEFAULT_GOALS[g][1]);
  }
  assert.equal(Krusty.hops(G, home, home), 0);
  assert.equal(Krusty.hops(G, 0, home), 0xff);
  assert.equal(Krusty.unitClass(6), 6);
  assert.equal(Krusty.unitClass(14), 6);
  assert.equal(Krusty.unitClass(41), 1);
  assert.equal(Krusty.unitClass(49), 7);
  assert.equal(Krusty.unitClass(40), 8);
});

test('bot mode: a think draws no engine rand(), leaves the checksum history alone and writes only the ledger and AI bytes', () => {
  const asserts = [];
  const G = newGame(asserts);
  const p = G.scenario.slotToPlayer[0];
  for (let t = 0; t < 40; t++) G.step();
  const bot = new KrustyBot(G, p, { seed: 7 });
  const before = money(G, p);
  const rand0 = G.randIndex;
  const hist0 = Buffer.from(G.gs.subarray(GS.HISTORY, GS.HISTORY + 512));
  const out = bot.think(40);
  assert.equal(G.randIndex, rand0, 'the engine RNG is untouched');
  assert.ok(hist0.equals(G.gs.subarray(GS.HISTORY, GS.HISTORY + 512)), 'the checksum history is untouched');
  const cmds = out.commands.flatMap((b) => splitCommands(b));
  const build = cmds.find((c) => c.type === T.BUILD);
  assert.ok(build, 'the first think buys a worker (goal 1: fewer than one worker)');
  assert.equal(build.raw[1], 6, 'EXPL');
  assert.equal(build.raw[2], p);
  assert.equal(build.raw[3], 1);
  assert.equal(before - money(G, p), G.tables.depend.find((d) => d.kind === 1 && d.a === 6).cost, 'the sender deducts (F44)');
  assert.ok(out.lines.some((l) => /worker/i.test(l)), out.lines.join(' | '));
  assert.deepEqual(asserts, []);
  assert.equal(bot.asserts.length, 0);
});

test('self-play on Armageddon: two Krusty bots build the standard base, mine, raise an army and form groups without an engine assert', () => {
  const asserts = [];
  const G = newGame(asserts);
  const players = [G.scenario.slotToPlayer[0], G.scenario.slotToPlayer[3]];
  const bots = players.map((p, i) => new KrustyBot(G, p, { seed: 11 + 17 * i }));
  const log = selfPlay(G, bots, 9600); // about seven minutes of game time
  assert.deepEqual(asserts, [], 'the engine accepted every command');
  for (const b of bots) assert.equal(b.asserts.length, 0, `no Krusty assert: ${JSON.stringify(b.asserts)}`);
  for (const p of players) {
    // the goal chain: HQ (given), a worker, the barracks, army 5, the factory, the science building ...
    assert.notEqual(slotHp(G, p, 1), 0, `player ${p}: barracks built`);
    assert.notEqual(slotHp(G, p, 3), 0, `player ${p}: robot factory / breeding built`);
    const units = ownUnits(G, p);
    assert.ok(units.length >= 8, `player ${p}: an army of ${units.length}`);
    const workers = log.commands.filter((c) => c.type === T.BUILD && c.p === p && (c.raw[1] === 6 || c.raw[1] === 14));
    assert.ok(workers.length >= 2, `player ${p}: two workers bought (${workers.length})`);
    const ventOrders = log.lines.filter((l) => l.p === p && l.text.startsWith('Worker to the vent'));
    assert.ok(ventOrders.length >= 2, `player ${p}: workers sent to vents`);
    const seen = log.seenTypes.get(p);
    assert.ok(seen.has(0x2f) || seen.has(0x30), `player ${p}: a worker became a mining tower at some point (${[...seen].join(',')})`);
    assert.ok(log.lines.some((l) => l.p === p && l.text.startsWith('A guard for zone')), `player ${p}: a defend group for a vent zone`);
    const moves = log.commands.filter((c) => c.type === T.WAYPOINTS_OBJ && c.p === p);
    assert.ok(moves.length > 20, `player ${p}: group orders issued (${moves.length})`);
  }
  // every waypoint order names objects of the issuing player
  for (const c of log.commands.filter((c) => c.type === T.WAYPOINTS_OBJ)) {
    const n = c.raw.readInt16LE(2);
    const nwp = c.raw[1];
    for (let i = 0; i < n; i++) {
      const obj = c.raw.readInt16LE(4 + 4 * nwp + 2 * i);
      assert.ok(obj >= 120 && obj < 800, `object index ${obj}`);
    }
  }
  const s0 = bots[0].summary();
  assert.equal(s0.ai, 'krusty');
  assert.ok(s0.thinks >= 290, `${s0.thinks} thinks`);
  assert.ok(s0.commands > 100);
});

test('exact mode: a computer lobby slot is played by the engine itself inside game_tick (ai.js), at the original schedule', () => {
  const asserts = [];
  const G = newGame(asserts, [0, 3], [5]);
  const ai = G.scenario.slotToPlayer[5];
  assert.equal(i32(G.gs, playerAddr(ai) + P.AI_TYPE), 3, 'lobby type 0 (easy) -> Krusty');
  const humans = [G.scenario.slotToPlayer[0], G.scenario.slotToPlayer[3]];
  for (const p of humans) assert.equal(i32(G.gs, playerAddr(p) + P.AI_TYPE), 0);
  // tick 4: every AI thinks; the first think allocates the state and buys the worker
  for (let t = 0; t < 3; t++) G.step();
  assert.equal(G.ai?.krusty?.[ai] ?? null, null, 'nothing before tick 4');
  G.step(); // TICK 4
  assert.ok(G.ai.krusty[ai], 'krusty_alloc on the first think');
  assert.equal(i32(G.gs, playerAddr(ai) + P.KRUSTY), 1);
  const spent = i32(G.gs, playerAddr(ai) + P.SPENT);
  assert.ok(spent > 0, 'the worker order went through the 0x0A handler (SPENT booked)');
  assert.equal(money(G, ai), 1500 - spent, 'and the AI deducted its money like the original');
  // the round robin: player `ai` thinks again at ticks 4 + 4*ai + 32k
  const rrTick = 4 + 4 * ai + 32;
  const prevThinks = G.ai.rr;
  while (i32(G.gs, GS.TICK) < rrTick - 1) G.step();
  const kaiBefore = Buffer.from(G.ai.krusty[ai]);
  G.step();
  assert.ok(!kaiBefore.equals(G.ai.krusty[ai]), 'the state changed in the think tick (influence map refreshed)');
  void prevThinks;
  // 800 more ticks: the AI plays on, the humans' blocks stay human, nothing asserts
  for (let t = 0; t < 800; t++) G.step();
  assert.deepEqual(asserts, []);
  assert.ok(slotHp(G, ai, 1) !== 0 || i32(G.gs, playerAddr(ai) + P.QUEUE_LEN) !== 0 || ownUnits(G, ai).length >= 1, 'the AI base is alive and producing');
  assert.ok(Ai.hasAiPlayer(G));
  // the trigger `ai` sets the type, nothing else
  Ai.triggerAi(G, humans[0], 4);
  assert.equal(i32(G.gs, playerAddr(humans[0]) + P.AI_TYPE), 4);
});

test('exact mode consumes exactly the original rand() draws: none without AI players, one per think for Krusty', () => {
  const a = newGame();
  const b = newGame();
  for (let t = 0; t < 100; t++) {
    a.step();
    b.step();
  }
  assert.equal(a.randIndex, b.randIndex, 'humans only: ai_turn is a no-op');
  const c = newGame([], [0, 3], [5]);
  const before = c.randIndex;
  for (let t = 0; t < 3; t++) c.step();
  const at3 = c.randIndex;
  void before;
  c.step(); // tick 4: the think draws one rand() in ai_think plus Krusty's own (defend gate at least)
  assert.ok(c.randIndex !== at3, 'the AI think drew from the game RNG');
});

test('set_goal / aimsg: tunables and goals follow the trigger words', () => {
  const G = newGame();
  const p = G.scenario.slotToPlayer[0];
  const ctx = Ai.exactContext(G, p);
  Krusty.krustyAimsg(ctx, [0, 50]);
  assert.equal(ctx.kai.readInt32LE(Krusty.K.SPLIT), 128);
  Krusty.krustyAimsg(ctx, [3, 9]);
  assert.equal(ctx.kai.readInt32LE(Krusty.K.WEIGHTS + 8), 9);
  Krusty.krustyAimsg(ctx, [8, 20, 2, 40]);
  assert.equal(ctx.kai.readInt32LE(Krusty.K.GOALS + 12 * 20), 2);
  assert.equal(ctx.kai.readInt32LE(Krusty.K.GOALS + 12 * 20 + 8), 40);
  Krusty.krustyAimsg(ctx, [13, 25]);
  assert.equal(ctx.kai.readInt32LE(Krusty.K.RATIO), 64);
  Krusty.krustyAimsg(ctx, [14, (p + 1) % 8, 1]);
  assert.equal(ctx.kai[Krusty.K.SEE_THRU + ((p + 1) % 8)], 1);
  assert.throws(() => Krusty.setGoal(ctx.kai, 32, 0, 0));
});

test('FIX (bot mode only): the upgrade goals buy weapon and armour upgrades with 0x0C; exact mode leaves them dead', () => {
  const asserts = [];
  const G = newGame(asserts);
  const p = G.scenario.slotToPlayer[0];
  // barracks, robot factory and science building, the dependencies of the human upgrades
  for (const slot of [1, 3, 2]) G.applyCommand(build.buildBuilding(slot, 0, p));
  for (let t = 0; t < 1500; t++) G.step();
  assert.ok([1, 2, 3].every((s) => slotHp(G, p, s) !== 0), 'buildings up');
  G.gs.writeInt32LE(5000, playerAddr(p) + P.MONEY);
  const ups = Krusty.buyableUpgrades(G, p, 0);
  assert.ok(ups.length > 0, 'weapon upgrades buyable');
  assert.ok(ups.every((u) => u.level === 1 && u.cost === 1000 && G.tables.types[u.type].race === 0), JSON.stringify(ups));
  // a bot whose first goal is the weapon upgrade (kind 1, param 6), then the armour upgrade (param 7)
  const bot = new KrustyBot(G, p, { seed: 3 });
  Krusty.setGoal(bot.kai, 0, 1, 6);
  Krusty.setGoal(bot.kai, 1, 1, 7);
  Krusty.setGoal(bot.kai, 2, 3, 0);
  let out = bot.think(1500);
  let cmds = out.commands.flatMap((b) => splitCommands(b));
  const up = cmds.find((c) => c.type === T.SETTING);
  assert.ok(up, 'a 0x0C upgrade command');
  assert.equal(up.raw[1], 0, 'weapon');
  assert.equal(up.raw[3], 1, 'level 1');
  assert.equal(up.raw[4], p);
  assert.equal(money(G, p), 4000, 'the sender deducts the 1000');
  assert.ok(out.lines.some((l) => l.startsWith('Weapon upgrade 1 for')), out.lines.join(' | '));
  G.applyCommand(up.raw);
  assert.equal(G.tables.types[up.raw[2]].weaponLevel[p], 1);
  // the next think: that weapon item is done, another type or the armour goal comes
  out = bot.think(1532);
  cmds = out.commands.flatMap((b) => splitCommands(b));
  const up2 = cmds.find((c) => c.type === T.SETTING);
  assert.ok(up2 && !(up2.raw[1] === 0 && up2.raw[2] === up.raw[2] && up2.raw[3] === 1), 'not the same upgrade twice');
  // exact mode: the original's dead goals stay dead
  const ctx = Ai.exactContext(G, p);
  Krusty.setGoal(ctx.kai, 0, 1, 6);
  Krusty.setGoal(ctx.kai, 1, 3, 0);
  const emitted = [];
  ctx.emit = (c) => emitted.push(...c);
  Krusty.krustyThink(ctx);
  assert.ok(!emitted.some((c) => c[0] === T.SETTING), 'exact mode: no upgrade command');
  assert.deepEqual(asserts, []);
});

test('FIX (bot mode only): attack_plan excludes taken targets by zone and reads the contested flag of the destination', () => {
  const G = newGame();
  const p = G.scenario.slotToPlayer[0];
  const bot = new KrustyBot(G, p, { seed: 5 });
  const ctx = { G, p, kai: bot.kai, exact: false, fixes: true, rand: () => 0, emit: () => {}, say: () => {}, assert: () => {} };
  // paint an enemy presence in one reachable zone so that both attack groups have exactly one target to want
  const home = [...Array(255).keys()].find((z) => z > 0 && bot.kai[Krusty.zoneAddr(z) + Krusty.Z.HOP] === 0);
  const target = [...Array(255).keys()].find((z) => z > 0 && bot.kai[Krusty.zoneAddr(z) + Krusty.Z.HOP] === 1); // next to home: the best-scoring zone
  assert.ok(home && target);
  const za = Krusty.zoneAddr(target);
  bot.kai.writeInt8((p + 1) % 8, za + Krusty.Z.G_OWNER);
  bot.kai.writeUInt16LE(30, za + Krusty.Z.G_STR);
  bot.kai.writeInt16LE(1, za + Krusty.Z.BUILDINGS);
  // give both groups strength by faking an arrived unit list is complex; instead route group 0 to the target as "state 0"
  const m0 = Krusty.minorAddr(2, 0);
  const m1 = Krusty.minorAddr(2, 1);
  bot.kai.writeInt32LE(home, m0 + Krusty.MN.ZONE);
  bot.kai.writeInt32LE(home, m1 + Krusty.MN.ZONE);
  Krusty.setRoute(ctx, 2, 0, target, null);
  bot.kai[m0 + Krusty.MN.STATE] = 0; // group 0 already marching on the target
  bot.kai[m1 + Krusty.MN.STATE] = 2; // group 1 needs a target
  // chooseTarget for group 1 with the fixed exclusion list must skip the taken zone
  const excluded = new Uint8Array(256);
  excluded[target] = 1; // what attackPlan builds with fixes on (B[dest])
  const route = [];
  const fixed = Krusty.chooseTarget(ctx, excluded, 5, 100, home, route);
  assert.notEqual(fixed, target, 'the taken zone is never chosen with the fix');
  const original = new Uint8Array(256);
  original[0] = 1; // what the original builds (B[group index 0])
  assert.equal(Krusty.chooseTarget(ctx, original, 5, 100, home, route), target, 'the original would send group 1 there too');
  // contested flag: with fixes the state-0 test reads zone[dest]; a contested destination without enemies keeps the group on its way.
  // The group needs strength for that (a group of strength 0 always gives up: 2*0 <= threat 0), so one armed own unit is linked in as arrived
  const unit = ownUnits(G, p).find((u) => G.tables.types[u.type].weapon[0] !== -1);
  assert.ok(unit, 'an armed starting unit');
  Krusty.link(G.gs, bot.kai, unit.obj, 2, 0);
  G.gs[objAddr(unit.obj) + Krusty.OA.STATUS] = 1;
  assert.ok(Krusty.groupStrength(ctx, 2, 0) > 0, 'group 0 has strength');
  bot.kai.writeInt8(-1, za + Krusty.Z.G_OWNER);
  bot.kai.writeUInt16LE(0, za + Krusty.Z.G_STR);
  bot.kai.writeInt16LE(0, za + Krusty.Z.BUILDINGS);
  bot.kai[za + Krusty.Z.FLAGS] |= Krusty.ZF_CONTESTED;
  bot.kai[Krusty.zoneAddr(0) + Krusty.Z.FLAGS] &= ~Krusty.ZF_CONTESTED;
  bot.kai[m1 + Krusty.MN.ACTIVE] = 0; // only group 0 in this check
  Krusty.attackPlan(ctx, 2);
  assert.equal(bot.kai[m0 + Krusty.MN.STATE], 0, 'fixed: the contested destination keeps group 0 marching');
  const exact = { ...ctx, exact: true, fixes: false };
  bot.kai[m0 + Krusty.MN.STATE] = 0;
  Krusty.attackPlan(exact, 2);
  assert.equal(bot.kai[m0 + Krusty.MN.STATE], 2, 'original: zone[0] is not contested, so the group gives its target up');
});

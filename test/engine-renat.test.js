// renat.js: trigger grammar / evaluator / actions against the vent triggers of maps/D8PLAY01.json,
// plus the generator and research-site bookkeeping. Self-contained: engine modules that other
// agents have not written yet resolve to empty stubs through a module hook, and `Game` falls back
// to a minimal stand-in when engine.js itself cannot load.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { register } from 'node:module';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// Missing ./src/engine/<sibling>.js -> empty module (only their live bindings are touched here).
const hook = `
export async function resolve(specifier, context, next) {
  try {
    return await next(specifier, context);
  } catch (e) {
    if (e && e.code === 'ERR_MODULE_NOT_FOUND' && /\\/engine\\/[a-z]+\\.js$/.test(specifier)) {
      return { url: 'data:text/javascript,export%20default%20{}', shortCircuit: true };
    }
    throw e;
  }
}`;
register('data:text/javascript,' + encodeURIComponent(hook), import.meta.url);

const Mem = await import('../src/engine/mem.js');
const Renat = await import('../src/engine/renat.js');
let Anim = null;
try {
  Anim = await import('../src/engine/anim.js');
} catch {
  Anim = null;
}
const { GS, O, objAddr, w8, w16, w32, i16, i32, u8, sx16, idiv, irem, GS_SIZE } = Mem;

const consts = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'dc16-tables.json'), 'utf8'));
const mapJson = JSON.parse(fs.readFileSync(path.join(ROOT, 'maps', 'D8PLAY01.json'), 'utf8'));
const tablesStub = { types: Array.from({ length: 130 }, () => ({ fly: 0, speed: 0, stand: null, deploy: null, funk: null })) };

let Game;
try {
  ({ Game } = await import('../src/engine/engine.js'));
} catch {
  Game = null;
}

/** Minimal stand-in for engine.js's Game (same API surface renat.js uses). */
class FakeGame {
  constructor(tables, c) {
    this.tables = tables;
    this.consts = c;
    this.gs = Buffer.alloc(GS_SIZE);
    this.randTable = c.rand.values;
    this.randIndex = 0;
    this.stats = new Int32Array(8 * 12);
    this.typeStats = new Int32Array(8 * 130 * 4);
    this.diplo = [new Uint8Array(8), new Uint8Array(8)];
    this.globals = { loopTrap: 0, pathGen: 0 };
    this.asserts = [];
    this.map = null;
  }
  srand(s) {
    this.randIndex = s & 0xff;
  }
  rand() {
    this.randIndex = (this.randIndex + 1) & 0xff;
    return this.randTable[this.randIndex];
  }
  stat(k, p) {
    return this.stats[p * 12 + k];
  }
  setStat(k, p, v) {
    this.stats[p * 12 + k] = v | 0;
  }
  addStat(k, p, v) {
    this.stats[p * 12 + k] += v | 0;
  }
  typeStat(k, p, t) {
    return this.typeStats[(p * 130 + t) * 4 + k];
  }
  setTypeStat(k, p, t, v) {
    this.typeStats[(p * 130 + t) * 4 + k] = v | 0;
  }
  diploSet(m, a, b, on) {
    if (on === 1) this.diplo[m][a] |= 1 << b;
    else this.diplo[m][a] &= ~(1 << b);
  }
  assert(cond, msg) {
    if (!cond) this.asserts.push({ msg });
  }
}

function makeGame() {
  const G = Game ? new Game(tablesStub, consts) : new FakeGame(tablesStub, consts);
  // the eruption actions start animations: give the game the sprite data when it is there
  const spritesFile = path.join(ROOT, 'data', 'classic', 'sprites.json');
  if (Anim && fs.existsSync(spritesFile)) G.sprites = Anim.loadSprites(JSON.parse(fs.readFileSync(spritesFile, 'utf8')));
  const w = mapJson.width;
  const h = mapJson.height;
  G.map = { w, h, load: new Int32Array(w * h), ground: new Int32Array(w * h).fill(0x3ff), air: new Int16Array(w * h).fill(0x3ff) };
  return G;
}

const compileEval = (G, text, unit = -1) => Renat.evalCondition(G, Renat.compileCondition(G, text), unit);

test('compileCondition produces the stack bytecode of 0x43C674', () => {
  const G = makeGame();
  // (s(3,0)==1): const 3, const 0, stat, const 1, ==, end
  assert.deepEqual([...Renat.compileCondition(G, '(s(3,0)==1)')], [7, 3, 0, 7, 0, 0, 0x10, 7, 1, 0, 4, 0x16]);
  // ((c+90)+(r%210)): c, 90, +, r, 210, %, +, end
  assert.deepEqual([...Renat.compileCondition(G, '((c+90)+(r%210))')], [8, 7, 90, 0, 0x0b, 0x12, 7, 210, 0, 0x13, 0x0b, 0x16]);
  // m(x,z) and s(p,k,type) and v(x,z,q)
  assert.deepEqual([...Renat.compileCondition(G, 'm(81,137)')], [7, 81, 0, 7, 137, 0, 0x15, 0x16]);
  assert.deepEqual([...Renat.compileCondition(G, 's(0,2,5)')], [7, 0, 0, 7, 2, 0, 7, 5, 0, 0x11, 0x16]);
  assert.deepEqual([...Renat.compileCondition(G, 'v(1,2,3)')], [7, 1, 0, 7, 2, 0, 7, 3, 0, 0x0f, 0x16]);
  // 16-bit constants: 40000 is stored as its low word
  assert.deepEqual([...Renat.compileCondition(G, '40000')], [7, 40000 & 0xff, (sx16(40000) >> 8) & 0xff, 0x16]);
  assert.equal(G.asserts.length, 0);
});

test('evaluator: int16 arithmetic and the inverted +/* precedence of the original', () => {
  const G = makeGame();
  assert.equal(compileEval(G, '2*3+4'), 14); // 2*(3+4)
  assert.equal(compileEval(G, '(2*3)+4'), 10);
  assert.equal(compileEval(G, '10/3'), 3);
  assert.equal(compileEval(G, '7-10'), -3);
  assert.equal(compileEval(G, '5>3'), 1);
  assert.equal(compileEval(G, '3!=3'), 0);
  assert.equal(compileEval(G, '3=3'), 1); // a single = is equality too
  assert.equal(compileEval(G, '(1)||(0)'), 1);
  assert.equal(compileEval(G, '(2)&&(1)'), 0); // bitwise AND of the int16 values
  assert.equal(compileEval(G, '40000'), sx16(40000));
  assert.equal(compileEval(G, '(30000+30000)'), sx16(60000));
  assert.equal(G.asserts.length, 0);
  // 1+2+3 is a parse error in the original (one operator per level without parentheses); the
  // stray `+` is reported by every level that sees it, the parser never consumes it
  Renat.compileCondition(G, '1+2+3');
  assert.ok(G.asserts.length >= 1);
  assert.match(G.asserts[0].msg, /remaining crap: 3$/); // the pointer is already past the `+`
});

test('primitives: c, r, s(p,k), s(p,k,type), m(x,z), u(k), b(p,k)', () => {
  const G = makeGame();
  w32(G.gs, GS.GAME_TIME, 16 * 500 + 15);
  assert.equal(compileEval(G, 'c'), 500);
  G.srand(0);
  const r = consts.rand.values[1];
  assert.equal(compileEval(G, 'r%210'), irem(sx16(r), 210));
  G.setStat(0, 3, 1);
  assert.equal(compileEval(G, '(s(3,0)==1)'), 1);
  G.setStat(0, 3, 0);
  assert.equal(compileEval(G, '(s(3,0)==1)'), 0);
  G.setTypeStat(2, 0, 4, 400);
  assert.equal(compileEval(G, '(c>s(0,2,4))'), 1);
  G.setTypeStat(2, 0, 4, 600);
  assert.equal(compileEval(G, '(c>s(0,2,4))'), 0);
  assert.equal(compileEval(G, '(m(81,137)==0)'), 1);
  G.map.load[137 * G.map.w + 81] |= 1 << 26;
  assert.equal(compileEval(G, '(m(81,137)==0)'), 0);
  G.globals.captures = [0, 3, 0, 0, 0, 0, 0, 0];
  assert.equal(compileEval(G, 'u(1)'), 3);
  w32(G.gs, Mem.playerAddr(2) + Mem.P.SLOT_HP + 4 * 3, 70000);
  assert.equal(compileEval(G, 'b(2,3)'), sx16(70000)); // low word of the int32
  assert.equal(G.asserts.length, 0);
  // t / S need a tripping unit
  compileEval(G, 't');
  assert.equal(G.asserts.at(-1).msg, 'unit!=-1');
  w8(G.gs, objAddr(300) + O.TYPE, 25);
  w8(G.gs, objAddr(300) + O.TEAM, 9);
  assert.equal(compileEval(G, 't', 300), 25);
  assert.equal(compileEval(G, 'S', 300), 9);
});

test('loadTriggers: lives, types, reversed action order (D8PLAY01 vent triggers)', () => {
  const G = makeGame();
  Renat.loadTriggers(G, mapJson.script);
  assert.equal(G.asserts.length, 0);
  const R = Renat.renatState(G);
  const armed = mapJson.script.filter((t) => t.flag === 1).map((t) => t.id);
  for (const t of mapJson.script) {
    const rec = R.triggers[t.id];
    assert.equal(rec.type, t.type === 'trip' ? 1 : 0);
    assert.equal(rec.lives, t.flag);
    assert.ok(rec.cond instanceof Uint8Array);
    // linked LIFO: the last action of the block is the head
    const ops = [];
    for (let a = rec.actions; a; a = a.next) ops.push(a.rec[0]);
    const expected = t.actions.map((a) => ({ setarray: 6, setlifes: 7, newrate2: 0x0e, setmoney: 0x0d })[a.op]).reverse();
    assert.deepEqual(ops, expected);
  }
  assert.ok(armed.length > 0);
  assert.equal(R.triggers[20].lives, 0);
  // byte arguments of newrate2 81 137 15: x, z and the compiled expression
  const t20 = R.triggers[20];
  const acts = [];
  for (let a = t20.actions; a; a = a.next) acts.push(a);
  const nr = acts.find((a) => a.rec[0] === 0x0e);
  assert.equal(nr.rec[4], 81);
  assert.equal(nr.rec[5], 137);
  assert.deepEqual([...nr.expr], [7, 15, 0, 0x16]);
});

test('triggersTick: eruption chain 0 -> 20 -> 40 of D8PLAY01 with the original rand() order', () => {
  const G = makeGame();
  Renat.loadTriggers(G, mapJson.script);
  const R = Renat.renatState(G);
  G.setStat(0, 1, 256); // rate factor 100 %
  G.setStat(0, 2, 256); // money factor 100 %
  G.setStat(0, 3, 1);
  G.setStat(0, 4, 1);
  // the dormant vent of trigger 20/40 at (81, 137) as the scenario loader would create it
  const vent = 200;
  const va = objAddr(vent);
  w8(G.gs, va + O.LIFE, 1);
  w8(G.gs, va + O.TYPE, 40);
  w8(G.gs, va + O.TEAM, 8);
  w16(G.gs, va + O.X, 81 << 8);
  w16(G.gs, va + O.Z, 137 << 8);
  w32(G.gs, va + O.HP, 15000);
  // every vent of the map sets the ALIVE_MINE bit when the scenario loader creates it (0x41C758)
  for (const o of mapJson.objects.filter((o) => o.role === 'vent')) G.map.load[o.z * G.map.w + o.x] |= 1 << 26;
  // tick 1: every "(s(3,0)==1)" trigger fires: setlifes first (reverse order), then setarray k ((c+90)+(r%210))
  w32(G.gs, GS.GAME_TIME, 16 * 100);
  G.srand(0);
  Renat.triggersTick(G);
  assert.equal(G.asserts.length, 0);
  const firstWave = mapJson.script.filter((t) => t.flag === 1 && t.condition === '(s(3,0)==1)');
  assert.ok(firstWave.length >= 2);
  let ri = 0;
  for (const t of firstWave.sort((a, b) => a.id - b.id)) {
    const k = t.actions.find((a) => a.op === 'setarray').args[0];
    const armedId = t.actions.find((a) => a.op === 'setlifes').args[0];
    const rv = consts.rand.values[++ri];
    assert.equal(G.typeStat(2, 0, k), sx16(100 + 90 + irem(sx16(rv), 210)));
    assert.equal(R.triggers[armedId].lives, 1);
    assert.equal(R.triggers[t.id].lives, 0);
  }
  assert.equal(G.randIndex, ri);
  // tick 2: trigger 20 "((c>s(0,2,0))&&(s(3,0)==1))" fires once the clock passed the array value
  const due = G.typeStat(2, 0, 0);
  w32(G.gs, GS.GAME_TIME, 16 * due);
  Renat.triggersTick(G);
  assert.equal(R.triggers[20].lives, 1, 'c == due: not yet');
  w32(G.gs, GS.GAME_TIME, 16 * (due + 1));
  Renat.triggersTick(G);
  assert.equal(R.triggers[20].lives, 0);
  assert.equal(i16(G.gs, va + 0x32), idiv(15 * 256, 256), 'newrate2 81 137 15');
  assert.equal(i32(G.gs, va + O.HP), 15000, 'setmoney 81 137 15000');
  assert.equal(R.triggers[40].lives, 1);
  assert.equal(G.asserts.length, 0);
  // trigger 40 waits for the vent to die (m(81,137)==0)
  const before = G.randIndex;
  Renat.triggersTick(G);
  assert.equal(R.triggers[40].lives, 1);
  G.map.load[137 * G.map.w + 81] &= ~(1 << 26);
  Renat.triggersTick(G);
  assert.equal(R.triggers[40].lives, 0);
  assert.equal(R.triggers[20].lives, 1, 'setlifes 20 1 re-armed');
  assert.equal(G.randIndex, before + 1, 'one rand() for the new eruption time');
  // setmoney on a tile without a vent asks spawnCreature (scenario.js) for a new one: not exercised here
});

test('trip triggers: triggerEnter evaluates only type 1 with the tripping unit', () => {
  const G = makeGame();
  Renat.loadTriggers(G, [
    { id: 3, type: 'trip', flag: 2, condition: '(t==25)&&(S==9)', actions: [{ op: 'setarray', args: [1, '7'] }] },
    { id: 4, type: 'norm', flag: 1, condition: '1', actions: [{ op: 'setarray', args: [2, '1'] }] },
  ]);
  const R = Renat.renatState(G);
  w8(G.gs, objAddr(300) + O.TYPE, 25);
  w8(G.gs, objAddr(300) + O.TEAM, 9);
  Renat.triggerEnter(G, 4, 300); // norm trigger: ignored by trip entry
  assert.equal(R.triggers[4].lives, 1);
  Renat.triggerEnter(G, 3, 300);
  assert.equal(G.typeStat(2, 0, 1), 7);
  assert.equal(R.triggers[3].lives, 1);
  w8(G.gs, objAddr(300) + O.TEAM, 8);
  Renat.triggerEnter(G, 3, 300);
  assert.equal(R.triggers[3].lives, 1, 'condition false: stays armed');
  Renat.triggersTick(G); // norm trigger 4 fires here, trip trigger 3 never does
  assert.equal(R.triggers[4].lives, 0);
  assert.equal(G.typeStat(2, 0, 2), 1);
  assert.equal(R.triggers[3].lives, 1);
  assert.equal(G.asserts.length, 0);
});

test('actions: ally / vision / exomoney / nopickup / funkytower / dfiddle / waypoint / ai', () => {
  const G = makeGame();
  Renat.loadTriggers(G, [
    {
      id: 1,
      type: 'norm',
      flag: 1,
      condition: '1',
      actions: [
        { op: 'ally', args: [2, 5, 1] },
        { op: 'vision', args: [3, 6, 1] },
        { op: 'exomoney', args: [4, 77] },
        { op: 'nopickup', args: [1] },
        { op: 'funkytower', args: [7] },
        { op: 'dfiddle', args: [2, 9, 1] },
        { op: 'waypoint', args: [10, 11, 2, 12, 13, 14, 15] },
        { op: 'ai', args: [6, 3] },
      ],
    },
  ]);
  const unit = 400;
  const ua = objAddr(unit);
  w16(G.gs, ua + O.X, (10 << 8) + 5);
  w16(G.gs, ua + O.Z, (11 << 8) + 9);
  Renat.triggersTick(G);
  assert.equal(G.asserts.length, 0);
  assert.equal(u8(G.gs, GS.ALLIANCE + 2 * 10 + 5), 1);
  assert.equal(G.diplo[0][2] & (1 << 5), 1 << 5);
  assert.equal(G.diplo[0][5] & (1 << 2), 1 << 2);
  assert.equal(G.diplo[1][3] & (1 << 6), 1 << 6);
  assert.equal(G.diplo[1][6] & (1 << 3), 1 << 3);
  assert.equal(i32(G.gs, Mem.playerAddr(4) + Mem.P.INCOME), 77);
  assert.equal(i32(G.gs, Mem.playerAddr(1) + 0x1c), 1);
  assert.equal(i32(G.gs, Mem.playerAddr(7) + 0x28), 1);
  assert.equal(u8(G.gs, Mem.playerAddr(2) + Mem.P.DISABLED + 9), 1);
  assert.equal(i32(G.gs, Mem.playerAddr(6) + Mem.P.AI_TYPE), 3);
  assert.equal(u8(G.gs, ua + O.WP_COUNT), 2);
  assert.equal(u8(G.gs, ua + O.ORDER), 9);
  assert.equal(u8(G.gs, ua + O.PENDING), 1);
  assert.equal(i16(G.gs, ua + O.WAYPOINTS), (12 << 8) + 0x80);
  assert.equal(i16(G.gs, ua + O.WAYPOINTS + 2), (13 << 8) + 0x80);
  assert.equal(i16(G.gs, ua + O.WAYPOINTS + 4), (14 << 8) + 0x80);
  assert.equal(i16(G.gs, ua + O.WAYPOINTS + 6), (15 << 8) + 0x80);
});

test('generators and research sites: registration, limits, item queue', () => {
  const G = makeGame();
  Renat.resetGenerators(G);
  for (const o of mapJson.objects.filter((o) => o.role === 'spawner')) Renat.registerGenerator(G, o.x, o.z, o.type, o.a);
  const R = Renat.renatState(G);
  assert.equal(R.genCount, 21);
  assert.equal(Renat.generatorCreatureTotal(G), 21 * 3);
  assert.equal(R.gens[0].type, 25);
  assert.equal(R.gens[0].init, 0);
  assert.equal(G.asserts.length, 0);
  Renat.registerGenerator(G, 1, 1, 25, 10);
  assert.equal(G.asserts.at(-1).msg, 'number<RNAT_MAX_NUMBER');
  for (let i = R.genCount; i < 25; i++) Renat.registerGenerator(G, 1, 1, 25, 3);
  Renat.registerGenerator(G, 1, 1, 25, 3);
  assert.equal(G.asserts.at(-1).msg, 'num_registered<RNAT_MAX_SOURCES');

  Renat.resetResearch(G);
  Renat.registerResearchSite(G, 30, 40);
  assert.equal(Renat.addResearchItem(G, 30, 40, 63), true);
  assert.equal(Renat.addResearchItem(G, 30, 40, 66), true);
  assert.equal(Renat.addResearchItem(G, 31, 40, 63), false);
  assert.equal(Renat.popResearchItem(G, 30, 40), 63);
  assert.equal(Renat.popResearchItem(G, 30, 40), 66);
  assert.equal(Renat.popResearchItem(G, 30, 40), -1);
  const n = G.asserts.length;
  assert.equal(Renat.popResearchItem(G, 99, 99), -1);
  assert.equal(G.asserts.length, n + 1);
});

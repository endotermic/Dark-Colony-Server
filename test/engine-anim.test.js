import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as Anim from '../src/engine/anim.js';
import { GS_SIZE, O, objAddr, w8, u8 } from '../src/engine/mem.js';
import { extract, parseFin, FACING_PROBES } from '../tools/sprdata2json.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const jsonPath = path.join(here, '..', 'data', 'classic', 'sprites.json');
const json = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
const sprites = Anim.loadSprites(json);
const gameDir = process.env.DC_CLASSIC_DIR ?? path.join(here, '..', '..', 'Dark-Colony', 'DC - Classic');
const haveGame = fs.existsSync(path.join(gameDir, 'ANIM.DAT'));

const typeIndex = (name) => json.types.findIndex((t) => t.name === name);
const setName = (id) => json.sets[id].n;
const animAt = (setId, facing) => sprites.anims[sprites.sets[setId].anims[facing]];

function fakeG(randValues = [0]) {
  return {
    gs: Buffer.alloc(GS_SIZE),
    sprites,
    randCalls: 0,
    rand() {
      return randValues[this.randCalls++ % randValues.length];
    },
  };
}

test('TRSC: pose sets, frame counts and durations', () => {
  const G = fakeG();
  const trsc = typeIndex('TRSC');
  assert.equal(trsc, 0);
  const stand = Anim.pose(G, trsc, 'STAND');
  assert.equal(setName(stand), 'TRSCSTAND');
  assert.equal(animAt(stand, 0).name, 'TRSCSTAND12'); // facing 0 = file facing 12
  assert.deepEqual(animAt(stand, 0).dur, [2]);
  assert.equal(Anim.frameCount(G, stand, 0), 1);
  // 16 file facings -> 32 game facings, odd file facings missing: nearest existing wins (probe table)
  assert.deepEqual([0, 1, 2, 3, 4, 5, 6, 7, 30, 31].map((f) => animAt(stand, f).name), [
    'TRSCSTAND12', 'TRSCSTAND12', 'TRSCSTAND12', 'TRSCSTAND10', 'TRSCSTAND10', 'TRSCSTAND10', 'TRSCSTAND10', 'TRSCSTAND8',
    'TRSCSTAND14', 'TRSCSTAND12',
  ]);
  assert.equal(Anim.fireCount(G, trsc), 2);
  assert.equal(setName(Anim.pose(G, trsc, 'FIRE', 0)), 'TRSCFIREA'); // FIREA0 replaces FIRE0 in slot 0
  assert.equal(setName(Anim.pose(G, trsc, 'FIRE', 1)), 'TRSCFIREB');
  assert.deepEqual(animAt(Anim.pose(G, trsc, 'FIRE', 0), 0).dur, [1, 1, 1, 1, 1, 1, 1, 1]);
  assert.deepEqual(animAt(Anim.pose(G, trsc, 'FIRE', 1), 0).dur, [2, 2, 2, 2, 2, 2, 2, 2]);
  assert.equal(Anim.dieCount(G, trsc), 3);
  assert.deepEqual(json.types[trsc].die.map((s) => animAt(s, 0).count), [11, 21, 11]);
  assert.equal(Anim.bloodCount(G, trsc), 7);
  // fallbacks share the STAND set id (pointer identity in the original)
  assert.equal(Anim.pose(G, trsc, 'DEPLOY'), stand);
  assert.equal(Anim.pose(G, trsc, 'FIG'), stand);
  assert.equal(Anim.pose(G, trsc, 'SCRCH'), stand);
  assert.equal(Anim.pose(G, trsc, 'BURN'), stand);
  assert.equal(setName(Anim.pose(G, trsc, 'FUNK')), 'TRSCFUNK');
  assert.equal(setName(Anim.pose(G, trsc, 'BUILD')), 'TRSCBUILD');
  assert.equal(setName(Anim.pose(G, trsc, 'MOVE')), 'TRSCMOVE');
  assert.equal(Anim.resolveSprite(G, 'TRSCSTAND'), stand);
  assert.equal(Anim.resolveSprite(G, 'NOSUCHSET'), Anim.NULL_SET);
});

test('set ids: NULL where the original holds NULL, distinct allocation per call', () => {
  const G = fakeG();
  const immobile = json.types.findIndex((t) => t.move === 0);
  assert.ok(immobile >= 0);
  assert.equal(Anim.pose(G, immobile, 'MOVE'), Anim.NULL_SET);
  const ids = new Set();
  for (const t of json.types) for (const k of ['move', 'stand', 'fire', 'die', 'blood']) for (const id of [].concat(t[k])) if (id) ids.add(id);
  // every set holds 32 animations (0x426014 asserts none is NULL)
  for (let i = 1; i < json.sets.length; i++) assert.equal(json.sets[i].a.filter((a) => a >= 0).length, 32, json.sets[i].n);
  assert.equal(FACING_PROBES.length, 32);
  assert.equal(json.sets.length - 1, 755);
});

test('weapons and booms: bullet set, blast-type explosion sets replace the own set', () => {
  assert.equal(json.weapons[1].bullet, Anim.NULL_SET); // "weapons" has no weaponsBULLET0
  assert.equal(setName(json.weapons[10].bullet), 'BARRBULLET');
  assert.deepEqual(json.weapons[10].explode, json.booms[1].sets); // Arty blast type -> NUKE, GASY by identity
  assert.deepEqual(json.booms[1].sets.map(setName), ['NUKE', 'GASY']);
  assert.equal(animAt(json.booms[1].sets[0], 0).name, 'NUKE');
  assert.equal(animAt(json.booms[1].sets[0], 17).name, 'NUKE'); // one animation copied to all 32 facings
  assert.deepEqual(json.weapons[5].explode.map(setName), ['SMOKEXPLODE']); // blast type 0 keeps the own set
  assert.equal(json.booms[0].sets.length, 0);
});

test('hotspots: TURR and ATRIL have one muzzle point, TRSC none (dummy)', () => {
  const G = fakeG();
  const a = objAddr(5);
  const turr = typeIndex('TURR');
  Anim.startAnim(G, a + O.ANIM0, Anim.pose(G, turr, 'FIRE', 0), Anim.MODE_ONCE);
  assert.deepEqual(Anim.hotspots(G, a + O.ANIM0, Anim.objFacing(0)), [{ x: 56, z: 216, delay: 0 }]);
  const atril = typeIndex('ATRIL');
  Anim.startAnim(G, a + O.ANIM0, Anim.pose(G, atril, 'FIRE', 0), Anim.MODE_ONCE);
  // durations 3,3,2,2 before the frame that carries the part: delay 10 ticks
  assert.deepEqual(animAt(Anim.pose(G, atril, 'FIRE', 0), 0).dur, [3, 3, 2, 2, 2, 2]);
  assert.deepEqual(Anim.hotspots(G, a + O.ANIM0, 0), [{ x: 120, z: 144, delay: 10 }]);
  Anim.startAnim(G, a + O.ANIM0, Anim.pose(G, 0, 'FIRE', 1), Anim.MODE_ONCE);
  assert.deepEqual(Anim.hotspots(G, a + O.ANIM0, 0), []);
  assert.deepEqual(Anim.fireHotspots(G, a + O.ANIM0, 0), [{ x: 0, z: 0, delay: 0 }]);
  // Classic data never has more than one hotspot per animation
  for (const an of sprites.anims) assert.ok(an.hot.length <= 1, an.name);
});

test('advance: a play-once animation finishes on tick 1 + d[1] + ... + d[n-1]', () => {
  const G = fakeG();
  const slot = objAddr(1) + O.ANIM0;
  const fireB = Anim.pose(G, 0, 'FIRE', 1); // 8 frames of 2 ticks
  Anim.startAnim(G, slot, fireB, Anim.MODE_ONCE);
  assert.equal(Anim.slotFrame(G, slot), 0);
  let ticks = 0;
  while (!Anim.isFinished(G, slot)) {
    Anim.advance(G, slot, 0);
    ticks++;
    assert.ok(ticks < 100);
  }
  assert.equal(ticks, 1 + 7 * 2);
  assert.equal(u8(G.gs, objAddr(1) + O.ANIM0_STATUS), Anim.STATUS_FINISHED);
  // 1-frame animation: finished on the first advance
  Anim.startAnim(G, slot, Anim.pose(G, 0, 'STAND'), Anim.MODE_ONCE);
  Anim.advance(G, slot, 0);
  assert.ok(Anim.isFinished(G, slot));
  // loop: never finishes, frame cycles
  Anim.startAnim(G, slot, fireB, Anim.MODE_LOOP);
  const frames = [];
  for (let i = 0; i < 20; i++) {
    Anim.advance(G, slot, 0);
    frames.push(Anim.slotFrame(G, slot));
  }
  assert.deepEqual(frames, [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 0, 0, 1, 1, 2, 2]);
  assert.equal(Anim.running(G, slot, 0), 1);
  // hold: stays on the last frame
  Anim.startAnim(G, slot, fireB, Anim.MODE_HOLD);
  for (let i = 0; i < 40; i++) Anim.advance(G, slot, 0);
  assert.equal(Anim.slotFrame(G, slot), 7);
  assert.equal(Anim.status(G, slot), Anim.MODE_HOLD);
  // startAnim is a no-op for the same set and mode (keeps the frame)
  Anim.startAnim(G, slot, fireB, Anim.MODE_HOLD);
  assert.equal(Anim.slotFrame(G, slot), 7);
  Anim.startAnim(G, slot, fireB, Anim.MODE_ONCE);
  assert.equal(Anim.slotFrame(G, slot), 0);
  // advancing a NULL set is a bug (NULL dereference in the original)
  assert.throws(() => Anim.advance(G, objAddr(2) + O.ANIM0, 0));
});

test('advanceAnims: blood overlay consumes rand() only when the slot is idle and the type has blood', () => {
  const G = fakeG([16838, 5758]);
  const obj = 200;
  const a = objAddr(obj);
  w8(G.gs, a + O.TYPE, 0); // TRSC, 7 blood sets
  w8(G.gs, a + O.HEADING, 0);
  const stand = Anim.pose(G, 0, 'STAND');
  Anim.startAnim(G, a + O.ANIM0, stand, Anim.MODE_LOOP); // initObject 0x41B124
  Anim.startAnim(G, a + O.ANIM1, stand, Anim.MODE_DONE);
  Anim.startAnim(G, a + O.ANIM2, stand, Anim.MODE_DONE);
  Anim.advanceAnims(G, obj);
  assert.equal(G.randCalls, 0);
  w8(G.gs, a + O.DAMAGED, 1);
  Anim.advanceAnims(G, obj);
  assert.equal(G.randCalls, 1);
  assert.equal(Anim.slotSet(G, a + O.ANIM1), json.types[0].blood[16838 % 7]);
  assert.equal(Anim.status(G, a + O.ANIM1), Anim.MODE_ONCE);
  assert.equal(u8(G.gs, a + O.DAMAGED), 0);
  // while the blood animation runs a new hit is kept pending, no rand()
  w8(G.gs, a + O.DAMAGED, 1);
  Anim.advanceAnims(G, obj);
  assert.equal(G.randCalls, 1);
  assert.equal(u8(G.gs, a + O.DAMAGED), 1);
  while (!Anim.isFinished(G, a + O.ANIM1)) Anim.advanceAnims(G, obj);
  assert.equal(G.randCalls, 1);
  Anim.advanceAnims(G, obj);
  assert.equal(G.randCalls, 2);
  assert.equal(Anim.slotSet(G, a + O.ANIM1), json.types[0].blood[5758 % 7]);
  // a type without blood sets clears the flag without rand()
  const noBlood = json.types.findIndex((t) => t.blood.length === 0);
  assert.ok(noBlood >= 0);
  const b = objAddr(201);
  w8(G.gs, b + O.TYPE, noBlood);
  const s2 = Anim.pose(G, noBlood, 'STAND');
  Anim.startAnim(G, b + O.ANIM0, s2, Anim.MODE_LOOP);
  Anim.startAnim(G, b + O.ANIM1, s2, Anim.MODE_DONE);
  Anim.startAnim(G, b + O.ANIM2, s2, Anim.MODE_DONE);
  w8(G.gs, b + O.DAMAGED, 1);
  Anim.advanceAnims(G, 201);
  assert.equal(G.randCalls, 2);
  assert.equal(u8(G.gs, b + O.DAMAGED), 0);
});

test('bounding boxes and facing helpers', () => {
  const G = fakeG();
  assert.deepEqual(Anim.bbox(G, typeIndex('TRSC')), [-128, -96, 96, 344]); // z0 and x1 widened to +-96
  assert.deepEqual(Anim.aimPoint(G, typeIndex('TRSC')), [0, -43]); // -(344 >> 3)
  assert.deepEqual(Anim.bbox(G, typeIndex('TURR')), [-160, -136, 160, 184]);
  const scgm = typeIndex('SCGM');
  assert.equal(json.types[scgm].fly, 1);
  assert.deepEqual(Anim.bbox(G, scgm), [-312, -296, 152, 232]);
  const box = Anim.bbox(G, typeIndex('TRSC'));
  assert.equal(Anim.pointInBox(box, -128, -96), 1);
  assert.equal(Anim.pointInBox(box, 96, 0), 0); // half-open
  assert.equal(Anim.pointInBox(box, 0, 343), 1);
  assert.equal(Anim.pointInBox(box, 0, 344), 0);
  assert.equal(Anim.objFacing(0), 0);
  assert.equal(Anim.objFacing(7), 0);
  assert.equal(Anim.objFacing(8), 2);
  assert.equal(Anim.objFacing(247), 30);
  assert.equal(Anim.objFacing(248), 0);
  assert.equal(Anim.missileFacing(255), 31);
  assert.equal(Anim.missileFacing(8), 1);
});

test('tool: the committed JSON is what the Classic folder produces', { skip: !haveGame && 'game folder not found' }, () => {
  const fin = parseFin(fs.readFileSync(path.join(gameDir, 'ANIMATE', 'TRSC.FIN')));
  assert.deepEqual(fin.banks, ['trsc', 'smsp', 'bloo', 'blaz', 'ssss']);
  assert.equal(fin.anims.length, 77);
  assert.equal(fin.frames.length, 472);
  assert.equal(fin.trailing, 0);
  assert.deepEqual(fin.anims[0], { name: 'TRSCSTAND0', first: 0, last: 0 });
  const { json: fresh } = extract(gameDir);
  assert.deepEqual(fresh, json);
});

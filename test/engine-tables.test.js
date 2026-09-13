import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  convertGamestat, parseWeapstat, parseGamestat, parseDepend, parseUnitid, parseMbullet, parseBoomstat,
  LineReader, sscanfNameInts, strtol, Cursor, LoaderError,
} from '../tools/gamestat2json.js';
import {
  loadTables, lookupUnitId, mbulletEntry, NUMWEAPTYPES, NUMOBJTYPES, NUMBLASTTYPES, MAX_DEPEND_ITEMS, MAX_DEPEND,
  MAX_LOOKUP,
} from '../src/engine/tables.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '..');
const jsonFile = path.join(repo, 'data', 'classic', 'gamestat.json');
const gameDir = path.resolve(repo, '..', 'Dark-Colony', 'DC - Classic');
const haveGame = fs.existsSync(path.join(gameDir, 'GAMESTAT'));

const committed = JSON.parse(fs.readFileSync(jsonFile, 'utf8'));

// ---- the shipped Classic files ---------------------------------------------------------------------

test('gamestat2json: the committed data/classic/gamestat.json is what the Classic folder produces', { skip: !haveGame && 'game folder absent' }, () => {
  const fresh = convertGamestat(gameDir);
  assert.deepEqual(fresh, committed);
});

test('gamestat2json: counts of the Classic tables', () => {
  assert.equal(committed.weapons.count, 64);
  assert.equal(committed.weapons.records.length, 64);
  const numbers = committed.weapons.records.map((r) => r.index).sort((a, b) => a - b);
  assert.deepEqual(numbers, Array.from({ length: 64 }, (_, i) => i + 1)); // weapon 0 is not in the file
  assert.equal(committed.types.count, 106);
  assert.equal(committed.types.records.length, 106);
  assert.ok(committed.types.records.every((r) => r.cols.length === 32));
  assert.equal(committed.mbullet.numWeapons, 9);
  assert.equal(committed.mbullet.numArmours, 10);
  assert.equal(committed.mbullet.rows.length, 9);
  assert.ok(committed.mbullet.rows.every((r) => r.length === 10));
  assert.equal(committed.booms.count, 13);
  assert.deepEqual(committed.booms.records.map((b) => b.index), Array.from({ length: 13 }, (_, i) => i));
  assert.equal(committed.depend.count, 80); // 0x506048 = the count line, although ids reach 84
  assert.equal(committed.depend.records.length, 80);
  assert.equal(committed.unitid.rows.length, 57);
});

test('gamestat2json: raw records are the file columns', () => {
  const w1 = committed.weapons.records.find((r) => r.index === 1);
  assert.deepEqual(w1, { index: 1, name: 'weapons', cols: [0, 1, 15, 100, 60, 4, 0, -1, -1, 0, 0] });
  const t0 = committed.types.records[0];
  assert.equal(t0.name, 'TRSC');
  assert.deepEqual(t0.cols.slice(0, 12), [0, 10, 25, 7, 4, 1, 2, 3, 125, 150, 0, 800]);
  assert.deepEqual(committed.mbullet.rows[0], [25, 12, 25, 18, 25, 90, 5, 50, 0, 5]);
  const b1 = committed.booms.records[1];
  assert.deepEqual(b1.sprites, ['NUKE', 'GASY']);
  assert.equal(b1.size, 5);
  assert.deepEqual(b1.blast[2], [50, 75, 100, 75, 50]);
  assert.deepEqual(b1.scatter, [[3, 10, 3], [10, 48, 10], [3, 10, 3]]);
  assert.deepEqual(committed.depend.records[0], { index: 0, cost: 2000, button: 206, kind: 0, params: [0, 0, 0], deps: [-1] });
  const troop = committed.depend.records.find((r) => r.index === 7);
  assert.deepEqual(troop, { index: 7, cost: 1500, button: 87, kind: 1, params: [6], deps: [0, -1] });
  assert.deepEqual(committed.unitid.rows[0], [1, 0, 73, 234]);
  assert.deepEqual(committed.unitid.rows[56], [1, 0, 78, 284]);
});

// ---- loadTables ----------------------------------------------------------------------------------------

const T = loadTables(committed);

test('tables: array sizes equal the original static arrays', () => {
  assert.equal(NUMWEAPTYPES, 80);
  assert.equal(NUMOBJTYPES, 130);
  assert.equal(NUMBLASTTYPES, 15);
  assert.equal(MAX_DEPEND_ITEMS, 110);
  assert.equal(MAX_DEPEND, 5);
  assert.equal(MAX_LOOKUP, 80);
  assert.equal(T.weapons.length, 80);
  assert.equal(T.types.length, 130);
  assert.equal(T.booms.length, 15);
  assert.equal(T.depend.length, 110);
  assert.equal(T.unitid.rows.length, 80);
  assert.equal(T.numTypes, 106);
  assert.equal(T.numDepend, 80);
  assert.equal(T.weapons[0].loaded, false); // weapon 0 stays zeroed
  assert.equal(T.weapons[0].damage, 0);
  assert.equal(T.types[106].loaded, false);
  assert.equal(T.types[106].hp, 0);
  assert.equal(T.booms[13].size, 0);
});

test('tables: weapons (docs §2.1 / §8.2)', () => {
  const w1 = T.weapons[1];
  assert.equal(w1.weaponClass, 0);
  assert.equal(w1.damage, 100);
  assert.equal(w1.rateOfFire, 15);
  assert.equal(w1.range, 4);
  assert.equal(w1.speed, 60);
  assert.equal(w1.maxFlight, ((((4 << 8) + 0x400) * 2 + 1) / 120 | 0) + 1); // 35
  assert.equal(w1.maxFlight, 35);
  assert.equal(w1.blast, 0);
  assert.equal(w1.shots, -1);
  assert.equal(w1.reload, -1);
  assert.equal(w1.kind, 0);
  assert.equal(w1.oneShot, 0);
  assert.equal(w1.numExplode, 0); // boom 0 has no sprites: EXPLODE0 / EXPL0 decide (sprite agent)
  assert.equal(w1.bullet, null);
  const w10 = T.weapons[10]; // Thunderbolt mortar: class 3, damage 250, rate 75, blast 1
  assert.equal(w10.weaponClass, 3);
  assert.equal(w10.damage, 250);
  assert.equal(w10.rateOfFire, 75);
  assert.equal(w10.blast, 1);
  assert.deepEqual(w10.explode, ['NUKE', 'GASY', null, null]);
  assert.equal(w10.numExplode, 2);
  const w21 = T.weapons[21]; // Scythe demon: class 1, damage 100, range 1
  assert.equal(w21.weaponClass, 1);
  assert.equal(w21.damage, 100);
  assert.equal(w21.range, 1);
  const w38 = T.weapons[38]; // mine: class 6, damage 1300, blast 2
  assert.equal(w38.weaponClass, 6);
  assert.equal(w38.damage, 1300);
  assert.equal(w38.blast, 2);
  assert.deepEqual(w38.explode, ['NUKE', null, null, null]);
  assert.equal(w38.numExplode, 1);
});

test('tables: magic-bullet matrix is 8.8 fixed point, truncated toward zero', () => {
  const MB = T.mbullet.rows;
  assert.equal(MB[0][0], 64); // 25 %
  assert.equal(MB[3][0], 256); // 100 %
  assert.equal(MB[1][0], 256);
  assert.equal(MB[6][0], 419); // 164 % -> 419.84 truncated
  assert.equal(MB[6][9], 5); // 2 % -> 5.12
  assert.equal(MB[2][0], 17); // 7 % -> 17.92 truncated (the doc's "round" would give 18)
  assert.equal(MB[7][9], 512); // 200 %
  assert.equal(MB[0][8], 0); // invulnerable column
  assert.equal(mbulletEntry(33), 84);
  assert.equal(mbulletEntry(12), 30);
  assert.equal(mbulletEntry(48), 122);
  // §8.2: trooper vs warrior 25 HP per shot; armour upgrade 1 -> 19
  assert.equal((100 * MB[0][0]) >> 8, 25);
  assert.equal((25 * T.types[8].armour[1]) >> 8, 19);
});

test('tables: object types (docs §2.2)', () => {
  const t0 = T.types[0];
  assert.equal(t0.name, 'TRSC');
  assert.equal(t0.race, 0);
  assert.equal(t0.turnSpeed, 10);
  assert.equal(t0.speed, 25);
  assert.equal(t0.visionDay, 7);
  assert.equal(t0.visionNight, 4);
  assert.deepEqual(t0.weapon, [1, 2, 3]);
  assert.deepEqual(t0.armour, [256, 204, 170]);
  assert.equal(t0.defenceClass, 0);
  assert.equal(t0.hp, 800);
  assert.equal(t0.fly, 0);
  assert.equal(t0.signature, 31);
  assert.equal(t0.scenery, 0);
  assert.equal(t0.counterpart, 8);
  assert.ok(t0.weaponLevel instanceof Uint8Array && t0.weaponLevel.length === 8);
  assert.ok(t0.armourLevel instanceof Uint8Array && t0.armourLevel.length === 8);
  assert.equal(t0.move, 'TRSCMOVE');
  assert.equal(t0.stand, 'TRSCSTAND');
  assert.deepEqual(t0.fire, ['TRSCSTAND', null, null]); // fallback until the sprite agent applies SPRITE_RULES
  assert.equal(t0.numFire, 1);
  assert.equal(t0.numDie, 1);
  assert.equal(t0.build, null);
  const hq = T.types[16]; // EXCOPOD: 120 / 140 -> 213 / 182, immobile, scenery 0
  assert.deepEqual(hq.armour, [256, 213, 182]);
  assert.equal(hq.speed, 0);
  assert.equal(hq.move, null);
  assert.equal(hq.defenceClass, 9);
  assert.equal(hq.hp, 4800);
  assert.equal(hq.fly, 1);
  const sarg = T.types[4]; // cyborg: special 129, special weapon 50, deploy code 5
  assert.equal(sarg.specialParam, 129);
  assert.equal(sarg.hasSpecial, 1);
  assert.equal(sarg.specialWeapon, 50);
  assert.equal(sarg.deployCode, 5);
  assert.equal(sarg.prodClass, 3);
  assert.equal(sarg.chargeRegen, 4);
  assert.equal(T.types[0].hasSpecial, 0);
  const lt = T.types[69]; // human lieutenant: rally 130 % -> 332, size 6, rank 12
  assert.equal(lt.rallyBonus, (130 << 8) / 100 | 0);
  assert.equal(lt.rallyBonus, 332);
  assert.equal(lt.rallySize, 6);
  assert.equal(lt.rankCode, 12);
  assert.equal(lt.deployCode, 196);
  assert.equal(T.types[94].fly, 7); // DOTT
  assert.equal(T.types[94].scenery, 1);
  assert.equal(T.types[51].scenery, 1); // ONEF
  assert.equal(T.types[45].hidden, 1); // HMINE
  assert.equal(T.types[49].healer, 1); // BEON
});

test('tables: pervasive slots (0x454AA0) are allocated in GAMESTAT order', () => {
  assert.equal(T.types[37].pervasive, 1); // POOP
  assert.equal(T.types[40].pervasive, 2); // VENT
  assert.equal(T.types[41].pervasive, 3); // T
  assert.equal(T.types[42].pervasive, 4); // XDEPLOY
  assert.equal(T.types[47].pervasive, 5); // EDPLY
  assert.equal(T.types[48].pervasive, 6); // SDPL
  assert.equal(T.types[0].pervasive, 0);
  assert.equal(T.pervasive.count, 6);
  assert.deepEqual(Array.from(T.pervasive.types.slice(0, 7)), [0, 37, 40, 41, 42, 47, 48]);
});

test('tables: boom types (docs §2.4)', () => {
  const b0 = T.booms[0];
  assert.equal(b0.size, 1);
  assert.equal(b0.blast[0][0], 256);
  assert.deepEqual(b0.sprites, [null, null, null, null]);
  assert.deepEqual(b0.scatter, [[7, 25, 7], [25, 122, 25], [7, 25, 7]]); // 3 % -> 7, 10 % -> 25, 48 % -> 122
  const b1 = T.booms[1];
  assert.equal(b1.size, 5);
  assert.deepEqual(b1.sprites, ['NUKE', 'GASY', null, null]);
  assert.equal(b1.blast[2][2], 256);
  assert.equal(b1.blast[0][0], 25); // 10 %
  assert.equal(b1.blast[1][1], 128); // 50 %
  assert.equal(b1.blast[2][1], 192); // 75 %
  assert.equal(b1.blast[5][0], 0); // outside size
  assert.deepEqual(b1.scatter, b0.scatter);
  const b2 = T.booms[2];
  assert.equal(b2.size, 7);
  assert.equal(b2.blast[3][3], 256);
  assert.equal(b2.blast[0][0], 12); // 5 %
  assert.equal(b2.blast[2][3], 230); // 90 %
  assert.deepEqual(b2.scatter, [[0, 0, 0], [0, 256, 0], [0, 0, 0]]);
  assert.deepEqual(T.booms[6].sprites, ['TURREXPLODE0', null, null, null]);
});

test('tables: depend items (docs §15.1)', () => {
  const d0 = T.depend[0];
  assert.deepEqual(d0, { index: 0, defined: 1, active: 1, status: 0, cost: 2000, button: 206, kind: 0, params: [0, 0, 0], a: 0, b: 0, c: 0, deps: [-1, 0, 0, 0, 0] });
  assert.deepEqual([T.depend[7].a, T.depend[7].b, T.depend[7].c], [6, 0, 0], 'the names city.js reads (depend.c +0x14/+0x18/+0x1C)');
  assert.equal(T.depend[34].active, 0);
  assert.deepEqual(T.depend[3].deps, [2, 1, -1, 0, 0]);
  const d7 = T.depend[7]; // troop item: one param, +0x18/+0x1C stay 0
  assert.equal(d7.kind, 1);
  assert.deepEqual(d7.params, [6, 0, 0]);
  assert.deepEqual(d7.deps, [0, -1, 0, 0, 0]);
  const d30 = T.depend[30]; // alien infantry weapon upgrade 1
  assert.equal(d30.kind, 2);
  assert.deepEqual(d30.params, [8, 0, 1]);
  assert.equal(d30.cost, 1000);
  assert.equal(T.depend[83].defined, 1);
  assert.equal(T.depend[83].params[0], 49);
  assert.equal(T.depend[34].defined, 0); // commented out in the file
  assert.equal(T.depend[34].cost, 0);
  assert.equal(T.depend[109].defined, 0);
});

test('tables: unitid lookup (0x43864C)', () => {
  assert.deepEqual(T.unitid.rows[0], { isUnit: 1, raceSel: 0, type: 73, id: 234 });
  assert.deepEqual(T.unitid.rows[57], { isUnit: -1, raceSel: -1, type: -1, id: -1 });
  assert.equal(T.unitid.count, 57);
  assert.equal(lookupUnitId(T.unitid, 16, 5, 0), 268); // human HQ building slot, human player
  assert.equal(lookupUnitId(T.unitid, 16, 5, 1), -1); // wrong race
  assert.equal(lookupUnitId(T.unitid, 28, 0, 1), 273); // alien HQ, alien player
  assert.equal(lookupUnitId(T.unitid, 82, 200, 0), 238); // raceSel -1 matches any race
  assert.equal(lookupUnitId(T.unitid, 82, 200, 1), 238);
  assert.equal(lookupUnitId(T.unitid, 73, 200, 1), 234); // alien lieutenant, alien player (sel 0)
  assert.equal(lookupUnitId(T.unitid, 73, 200, 0), -1);
  assert.equal(lookupUnitId(T.unitid, 73, 5, 1), -1); // isUnit mismatch
  assert.equal(lookupUnitId(T.unitid, 127, 200, 0), -1);
});

test('tables: records are fresh per loadTables call (mutable per game)', () => {
  const A = loadTables(committed);
  const B = loadTables(committed);
  A.types[0].weaponLevel[3] = 2;
  A.types[0].weapon[0] = 50;
  A.weapons[1].damage = 1;
  assert.equal(B.types[0].weaponLevel[3], 0);
  assert.equal(B.types[0].weapon[0], 1);
  assert.equal(B.weapons[1].damage, 100);
  assert.equal(T.types[0].weaponLevel[3], 0);
});

// ---- parser rules ------------------------------------------------------------------------------------

const crlf = (lines) => Buffer.from(lines.join('\r\n') + '\r\n', 'latin1');

test('parser: the line reader counts "\\r\\n" in strlen and skips % lines', () => {
  const rd = new LineReader(crlf(['1', '% c', '', '12', 'abc']), 'x');
  assert.equal(rd.fgets(), '1\r\n');
  assert.equal(rd.dataLine(4), '12\r\n'); // "% c", "" skipped; "1\r\n" would have been skipped too
  assert.equal(rd.dataLine(4), 'abc\r\n');
  assert.throws(() => rd.dataLine(4), LoaderError); // EOF is fatal
  const rd3 = new LineReader(crlf(['0 1', 'x']), 'x');
  assert.equal(rd3.dataLine(3), '0 1\r\n');
  assert.equal(rd3.dataLine(3), 'x\r\n'); // minLen 3 lets one visible character through
});

test('parser: fgets splits lines longer than 255 bytes', () => {
  const long = 'a'.repeat(300);
  const rd = new LineReader(Buffer.from(long + '\n', 'latin1'), 'x');
  assert.equal(rd.fgets().length, 255);
  assert.equal(rd.fgets(), 'a'.repeat(45) + '\n');
});

test('parser: sscanf %d stops at the first non-number, strtol does not advance without digits', () => {
  assert.deepEqual(sscanfNameInts('NAME 1 -2 x 4\r\n', 4), { name: 'NAME', ints: [1, -2] });
  assert.deepEqual(sscanfNameInts('\r\n', 4), { name: null, ints: [] });
  const cur = new Cursor('  abc');
  assert.deepEqual(strtol(cur), { value: 0, parsed: false });
  assert.equal(cur.i, 0);
  const c2 = new Cursor(' -17rest');
  assert.deepEqual(strtol(c2), { value: -17, parsed: true });
  assert.equal(c2.i, 4);
});

test('parser: weapstat splits at the first space and indexes by the weapon number', () => {
  const buf = crlf(['2', '% comment', '7 BARR 3 10 75 250 60 12 1 -1 -1 1 0', '', '3 weapons 0 3 15 150 60 4 0 -1 -1 0 0']);
  const w = parseWeapstat(buf);
  assert.equal(w.count, 2);
  assert.deepEqual(w.records[0], { index: 7, name: 'BARR', cols: [3, 10, 75, 250, 60, 12, 1, -1, -1, 1, 0] });
  assert.equal(w.records[1].index, 3);
  assert.throws(() => parseWeapstat(crlf(['0'])), /weapons < NUMWEAPTYPES/);
  assert.throws(() => parseWeapstat(crlf(['1', '80 X 0 0 0 0 0 0 0 0 0 0 0'])), /NUMWEAPTYPES/);
  assert.throws(() => parseWeapstat(crlf(['2', '1 X 0 0 0 0 0 0 0 0 0 0 0'])), /end of file/);
});

test('parser: gamestat indexes by line order and tolerates short lines', () => {
  const g = parseGamestat(crlf(['2', '% x', 'A 1 2 3', '', 'B -1 0']));
  assert.deepEqual(g.records, [{ index: 0, name: 'A', cols: [1, 2, 3] }, { index: 1, name: 'B', cols: [-1, 0] }]);
  assert.throws(() => parseGamestat(crlf(['131', 'A 1'])), /NUMOBJTYPES/);
});

test('parser: depend kinds and dependency terminator', () => {
  const d = parseDepend(crlf(['3', '5 100 7 1 12 3 -1', '9 200 8 2 1 2 3 -1', '10 300 9 0 1 2 3 5 9 -1']));
  assert.deepEqual(d.records[0], { index: 5, cost: 100, button: 7, kind: 1, params: [12], deps: [3, -1] });
  assert.deepEqual(d.records[1].params, [1, 2, 3]);
  assert.deepEqual(d.records[2].deps, [5, 9, -1]);
  assert.throws(() => parseDepend(crlf(['1', '5 100 7 3 1 -1'])), /kind 3/);
  assert.throws(() => parseDepend(crlf(['1', '5 100 7 1 1 1 2 3 4 5 -1'])), /MAX_DEPEND/);
  assert.throws(() => parseDepend(crlf(['1', '110 100 7 1 1 -1'])), /MAX_DEPEND_ITEMS/);
  // a missing number yields 0 without advancing: "5 100" -> button 0, kind 0, params 0,0,0, deps [0,0,0,0,0] -> assert
  assert.throws(() => parseDepend(crlf(['1', '5 100'])), /MAX_DEPEND/);
});

test('parser: unitid ends at an all-negative row', () => {
  const u = parseUnitid(crlf(['% h', '1 0 73 234', '0 1 16 268', '-1 -1 -1 -1', '1 1 1 1']));
  assert.deepEqual(u.rows, [[1, 0, 73, 234], [0, 1, 16, 268]]);
});

test('parser: mbullet reads two count lines then strtod rows', () => {
  const m = parseMbullet(crlf(['2', '3', '% c', '25 12.5', '100 0', '7 -3']));
  assert.deepEqual(m, { numArmours: 2, numWeapons: 3, rows: [[25, 12.5], [100, 0], [7, -3]] });
  assert.throws(() => parseMbullet(crlf(['2', '1', '25'])), /lptr!=ptr/);
  assert.throws(() => parseMbullet(crlf(['0', '1', '25 1'])), /num_armours>0/);
});

test('parser: boomstat sprite list, matrices and asserts', () => {
  const b = parseBoomstat(crlf(['1', '% x', '4 3', '', 'NUKE', 'NONE', '1 2 3', '4 5 6', '7 8 9', '0 0 0', '0 100 0', '0 0 0']));
  assert.deepEqual(b.records[0], { index: 4, size: 3, sprites: ['NUKE'], blast: [[1, 2, 3], [4, 5, 6], [7, 8, 9]], scatter: [[0, 0, 0], [0, 100, 0], [0, 0, 0]] });
  assert.throws(() => parseBoomstat(crlf(['1', '0 0', 'NONE', '1', '0 0 0', '0 0 0', '0 0 0'])), /size >= 1/);
  assert.throws(() => parseBoomstat(crlf(['15'])), /NUMBLASTTYPES/);
});

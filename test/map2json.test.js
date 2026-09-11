import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  parseMap, parseMtg, parsePth, parseScn, parseTro, parsePop, convertScenario, stringifyCompact, playersFromFileName,
  routingTable, defaultRoomBases,
} from '../tools/map2json.js';
import { DEFAULTS } from '../src/config.js';

// A 4x3 scenario built by hand. Rows in .MAP/.MTG are stored with z = height-1 first; the
// converter must flip them, while .PTH rows are already in z order.
const W = 4;
const H = 3;

function makeMap() {
  const b = Buffer.alloc(8 + 6 * W * H);
  b.writeUInt32LE(W, 0);
  b.writeUInt32LE(H, 4);
  for (let r = 0; r < H; r++) {
    for (let x = 0; x < W; x++) {
      const i = r * W + x;
      b.writeUInt16LE(1000 + r * 10 + x, 8 + i * 4);          // background: row and column readable
      b.writeUInt16LE(r === 0 && x === 1 ? 77 : 0, 8 + i * 4 + 2); // one foreground tile in file row 0
      b.writeUInt16LE(r === 2 ? 0x280 : 0x480, 8 + 4 * W * H + i * 2); // file row 2 blocking
    }
  }
  return b;
}

function makeMtg() {
  const b = Buffer.alloc(2 + W * H);
  b[0] = W; b[1] = H;
  b[2 + 0 * W + 3] = 5; // file row 0, x = 3
  return b;
}

function makePth() {
  const b = Buffer.alloc(65536 + W * H);
  b[1 * 256 + 2] = 2; b[2 * 256 + 1] = 1; b[1 * 256 + 1] = 1; b[2 * 256 + 2] = 2;
  for (let z = 0; z < H; z++) for (let x = 0; x < W; x++) b[65536 + z * W + x] = z === 0 ? 0 : (x < 2 ? 1 : 2);
  return b;
}

function teamBlock(i, active, city) {
  return [
    `TEAM ${i} ${active ? 1 : 0}`, `${i % 2}`, '%Race', active ? '1500' : '0', '%Money', '0', '%AI', '-1', '%TeamColour',
    active ? '3 7 -1' : '-1', '%Depend', '0 1 0 0 0 0 0 0 0 0 0 0 0 0 0 -1', '%TeamAllies', '5 6 -1', '%AISlots',
    city ? '1 2' : '38 45', city ? '2 1' : '0 0', '%City', '1 -1 0 -1 0 -1 0 -1 0 -1 ',
    ...Array.from({ length: 8 }, (_, j) => `0 0 ${j === 1 ? 2 : 0} ${j === 1 ? 1 : 0} 0`), '', '',
  ].join('\r\n');
}

const SCN = [
  'desert.bts', 'tiny01', "Tiny Test's Map", '0 2', '1', '5400', '225', '90', '',
  teamBlock(0, true, true), teamBlock(1, true, true),
  ...[2, 3, 4, 5, 6, 7].map((i) => teamBlock(i, false, false)),
  '', '1 2 0 0 -1 0', '', '2 1 0 1 -1 0', '', '', '3 0 40 15 15000', '0 2 40 0 25000', '2 2 25 -1 3 4', '1 1 37 0 -1 0', '',
  '1 2 69 0 -1 0', '2 1 69 1 -1 0', '',
].join('\r\n');

const TRO = [
  '20 norm 0 ((c>s(0,2,0))&&(s(3,0)==1))', 'newrate2 3 0 15', 'setmoney 3 0 15000', 'setlifes 40 1', 'end', '',
  '4 trip 1 (S==0)', 'setarray 0 ((c+90)+(r%210))', 'waypoint 63 54 2 67 53 63 54', 'msg 2 0 1 3 6', 'end', '',
].join('\r\n');

const POP = ['90', '90', '210', '900', '0 2 40 15 25000', ''].join('\r\n');

test('parseMap flips file rows into game z order and reads the three planes', () => {
  const m = parseMap(makeMap());
  assert.equal(m.width, W);
  assert.equal(m.height, H);
  assert.deepEqual(m.background[0], [1020, 1021, 1022, 1023]); // z = 0 is the last file row
  assert.deepEqual(m.background[2], [1000, 1001, 1002, 1003]);
  assert.equal(m.foreground[2][1], 77);
  assert.equal(m.attributes[0][0], 0x280); // blocking row (file row 2) is z = 0
  assert.equal(m.attributes[2][0], 0x480);
  assert.throws(() => parseMap(makeMap().subarray(0, 20)), /length/);
});

test('parseMtg and parsePth agree on the coordinate system', () => {
  const t = parseMtg(makeMtg(), W, H);
  assert.equal(t.grid[2][3], 5); // file row 0 -> z = 2
  assert.equal(t.maxTrigger, 5);
  assert.throws(() => parseMtg(Buffer.from([W, H, 0xc0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]), W, H), /bits 6\/7/);
  const p = parsePth(makePth(), W, H);
  assert.deepEqual(p.families[0], [0, 0, 0, 0]); // z = 0 is the first row of the file
  assert.deepEqual(p.families[1], [1, 1, 2, 2]);
  assert.equal(p.maxFamily, 2);
  const next = routingTable(p.next);
  assert.equal(next(1, 2), 2);
  assert.equal(next(2, 1), 1);
  assert.equal(next(0, 1), 0);
  assert.throws(() => routingTable('AAAA'), /65536/);
});

test('parseScn reads the header, the eight TEAM blocks and the object list like mobiles.c', () => {
  const s = parseScn(SCN);
  assert.equal(s.terrainFile, 'desert.bts');
  assert.equal(s.baseName, 'tiny01');
  assert.equal(s.name, "Tiny Test's Map");
  assert.deepEqual(s.header, { scenarioType: 0, teamCount: 2, startPhase: 1, phaseLength: 5400, phaseCounter: 225, dawnLength: 90 });
  assert.equal(s.teams.length, 8);
  const t0 = s.teams[0];
  assert.equal(t0.active, true);
  assert.equal(t0.race, 0);
  assert.equal(t0.money, 1500);
  assert.equal(t0.colour, -1);
  assert.deepEqual(t0.depend, [3, 7]);
  assert.deepEqual(t0.allies, [0, 1, 0, 0, 0, 0, 0, 0]);
  assert.deepEqual(t0.aiSlots, [5, 6]);
  assert.deepEqual(t0.start, { x: 1, z: 2 });
  assert.deepEqual(t0.city, { x: 2, z: 1 });
  assert.deepEqual(t0.citySlots[0], { type: 1, count: -1 });
  assert.equal(t0.buildings.length, 8);
  assert.equal(t0.buildings[1].weaponLevel, 2);
  assert.equal(t0.buildings[1].armourLevel, 1);
  assert.deepEqual(s.teams[2].city, { x: 0, z: 0 });
  assert.equal(s.teams[2].active, false);

  assert.equal(s.objects.length, 8);
  const vent = s.objects.find((o) => o.x === 3 && o.z === 0);
  assert.equal(vent.role, 'vent');
  assert.equal(vent.rate, 15);
  assert.equal(vent.money, 15000);
  assert.equal(s.objects.find((o) => o.type === 25).role, 'spawner');
  assert.equal(s.objects.find((o) => o.type === 37).role, 'artifactSite');
  const cmd = s.objects.find((o) => o.type === 69 && o.player === 1);
  assert.equal(cmd.role, 'unit');
  assert.equal(cmd.b, 0);
  assert.match(cmd.typeName, /lieutenant/);
});

test('parseTro splits trigger blocks, flags, conditions and actions', () => {
  const t = parseTro(TRO);
  assert.equal(t.length, 2);
  assert.deepEqual(t[0], {
    id: 20, type: 'norm', flag: 0, condition: '((c>s(0,2,0))&&(s(3,0)==1))',
    actions: [{ op: 'newrate2', args: [3, 0, 15] }, { op: 'setmoney', args: [3, 0, 15000] }, { op: 'setlifes', args: [40, 1] }],
  });
  assert.equal(t[1].type, 'trip');
  assert.equal(t[1].flag, 1);
  assert.equal(t[1].condition, '(S==0)');
  assert.deepEqual(t[1].actions[0], { op: 'setarray', args: [0, '((c+90)+(r%210))'] });
  assert.deepEqual(t[1].actions[1].args, [63, 54, 2, 67, 53, 63, 54]);
  assert.deepEqual(parseTro(''), []);
});

test('parsePop reads the eruption timing and the erupting vents', () => {
  assert.deepEqual(parsePop(POP), { timing: [90, 90, 210, 900], vents: [{ x: 0, z: 2, type: 40, rate: 15, money: 25000 }] });
});

test('convertScenario finds the sibling files case-insensitively and stringifyCompact keeps rows on one line', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dc-map2json-'));
  try {
    fs.writeFileSync(path.join(dir, 'T2INY01.scn'), SCN);
    fs.writeFileSync(path.join(dir, 'T2INY01.MAP'), makeMap());
    fs.writeFileSync(path.join(dir, 'T2INY01.mtg'), makeMtg());
    fs.writeFileSync(path.join(dir, 'T2INY01.PTH'), makePth());
    fs.writeFileSync(path.join(dir, 'T2INY01.tro'), TRO);
    fs.writeFileSync(path.join(dir, 'T2INY01.POP'), POP);
    const d = convertScenario(path.join(dir, 'T2INY01.scn'));
    assert.equal(d.format, 'dc16-scenario');
    assert.equal(d.players, 2);
    assert.equal(d.terrain, 'desert');
    assert.equal(d.width, W);
    assert.deepEqual(d.source.files, { scn: 'T2INY01.scn', map: 'T2INY01.MAP', mtg: 'T2INY01.mtg', pth: 'T2INY01.PTH', tro: 'T2INY01.tro', pop: 'T2INY01.POP' });
    assert.equal(d.tiles.background[0][0], 1020);
    assert.equal(d.path.families[0][0], 0);
    assert.equal(d.triggerIds[2][3], 5);
    assert.equal(d.script.length, 2);
    assert.equal(d.eruptions.vents.length, 1);
    assert.equal(d.overview, undefined);
    const text = stringifyCompact(d);
    assert.deepEqual(JSON.parse(text), d);
    assert.match(text, /"background": \[\n\s+\[1020,1021,1022,1023\],/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('playersFromFileName reads the 2nd character of multiplayer names only', () => {
  assert.equal(playersFromFileName('D8PLAY01.SCN'), 8);
  assert.equal(playersFromFileName('j2play05.scn'), 2);
  assert.equal(playersFromFileName('HUMAN01.SCN'), null);
});

test('defaultRoomBases lists the seven default rooms, and maps/ holds exactly those', () => {
  const rooms = defaultRoomBases();
  assert.equal(rooms.length, DEFAULTS.ROOMS.split(',').length);
  assert.deepEqual(rooms.slice(0, 2), ['J8PLAY01', 'D8PLAY01']);
  const mapsDir = path.resolve('maps');
  if (fs.existsSync(mapsDir)) {
    const files = fs.readdirSync(mapsDir).filter((f) => f !== 'index.json').map((f) => f.replace(/\.json$/, '')).sort();
    assert.deepEqual(files, [...rooms].sort(), 'maps/ must contain the default ROOMS only (regenerate with --rooms)');
    const index = JSON.parse(fs.readFileSync(path.join(mapsDir, 'index.json'), 'utf8'));
    assert.deepEqual(index.maps.map((m) => m.json.replace(/\.json$/, '')).sort(), files);
  }
});

// The real files, when the game repository sits beside this one (as on the maintainer's machine).
const REAL = path.resolve('..', 'Dark-Colony', 'DC - Classic', 'SCENARIO', 'MPLAYER', 'D8PLAY01.SCN');
test('Armageddon converts as documented', { skip: !fs.existsSync(REAL) }, () => {
  const d = convertScenario(REAL);
  assert.equal(d.name, 'Armageddon');
  assert.equal(d.terrain, 'desert');
  assert.equal(d.players, 8);
  assert.equal(d.width, 160);
  assert.equal(d.height, 140);
  assert.equal(d.teams.filter((t) => t.active).length, 8);
  const vents = d.objects.filter((o) => o.role === 'vent');
  assert.equal(vents.length, 18);
  assert.equal(d.eruptions.vents.length, 10);
  // every erupting vent of the .POP is a dormant (rate 0) vent of the .SCN
  for (const v of d.eruptions.vents) assert.equal(vents.find((o) => o.x === v.x && o.z === v.z)?.rate, 0);
  // the routing matrix is symmetric in reachability and the diagonal names the family itself
  const next = routingTable(d.path.next);
  for (let a = 1; a <= d.path.maxFamily; a++) assert.equal(next(a, a), a, `family ${a}`);
  // bit 9 of the attribute word marks blocking terrain: such cells are always path family 0
  for (let z = 0; z < d.height; z++) {
    for (let x = 0; x < d.width; x++) {
      if (d.tiles.attributes[z][x] & 0x200) assert.equal(d.path.families[z][x], 0, `x=${x} z=${z}`);
    }
  }
  // the commanders stand on the teams' start positions
  for (const t of d.teams) {
    assert.ok(d.objects.some((o) => o.type === 69 && o.player === t.index && o.x === t.start.x && o.z === t.start.z), `team ${t.index}`);
  }
});

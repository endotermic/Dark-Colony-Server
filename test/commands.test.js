import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ProtocolError } from '../src/frame.js';
import { T, build, splitCommands, decode, commandLength, sanitizeName, sanitizeText } from '../src/commands.js';

function one(buf) {
  const cmds = splitCommands(buf);
  assert.equal(cmds.length, 1, `expected one command in ${buf.toString('hex')}`);
  return cmds[0];
}

test('fixed-size builders round-trip through split and decode', () => {
  const cases = [
    [build.version(15, 3), T.VERSION, 5, { version: 15, id: 3 }],
    [build.until(7, 15), T.UNTIL, 9, { a: 7, until: 15 }],
    [build.until(-1, 15), T.UNTIL, 9, { a: -1, until: 15 }],
    [build.cheat(1, 0), T.CHEAT, 5, { a: 1, b: 0 }],
    [build.mready(4, 2), T.MREADY, 4, { player: 4, state: 2 }],
    [build.ready(2, 5), T.READY, 3, { status: 2, player: 5 }],
    [build.race(1, 5), T.RACE, 3, { value: 1, player: 5 }],
    [build.colourCycle(7, 2), T.COLOUR_CYCLE, 3, { value: 7, player: 2 }],
    [build.variable(9, 1), T.VAR, 5, { index: 9, value: 1 }],
    [build.disconnect(6), T.DISCONNECT, 2, { player: 6 }],
    [build.tickSpeed(33), T.TICK_SPEED, 5, { ms: 33 }],
    [build.tickMaxSpeed(2, 66), T.TICK_MAXSPEED, 6, { player: 2, ms: 66 }],
    [build.sync(0x1234, 99), T.SYNC, 7, {}],
    [build.keepalive(), T.KEEPALIVE, 1, {}],
    [build.initMe(3), T.INIT_ME, 2, { player: 3 }],
    [build.orderSelected(1, 0x12), T.ORDER_SEL, 3, {}],
    [build.moveToSelected(1, 100, 200), T.MOVETO_SEL, 6, {}],
    [build.deselect(2), T.DESELECT, 2, {}],
  ];
  for (const [buf, type, len, fields] of cases) {
    const c = one(buf);
    assert.equal(c.type, type);
    assert.equal(c.raw.length, len, `length of ${buf.toString('hex')}`);
    assert.deepEqual(decode(c), fields);
  }
});

test('variable-length commands', () => {
  let c = one(build.name(2, 'Nika'));
  assert.equal(c.raw.length, 8);
  assert.deepEqual(decode(c), { player: 2, name: 'Nika' });

  c = one(build.scenario('D8PLAY01.SCN', 'Armageddon'));
  assert.equal(c.raw.length, 1 + 13 + 11);
  assert.deepEqual(decode(c), { file: 'D8PLAY01.SCN', title: 'Armageddon' });

  c = one(build.lobbyChat('A: hi'));
  assert.equal(c.raw.length, 7);
  assert.deepEqual(decode(c), { text: 'A: hi' });

  c = one(build.chat(1, 0xff, 'x: y'));
  assert.equal(c.raw.length, 8);
  assert.deepEqual(decode(c), { from: 1, mask: 0xff, text: 'x: y' });

  // 0x07: u8 n=2, i16 count=3, 2 waypoints, 3 objects
  const wp = Buffer.from([0x07, 2, 3, 0, 1, 0, 2, 0, 3, 0, 4, 0, 10, 0, 11, 0, 12, 0]);
  assert.equal(one(wp).raw.length, 18);

  c = one(build.select(1, [5, 6]));
  assert.equal(c.raw.length, 8);
  assert.deepEqual([...c.raw], [0x14, 1, 5, 0, 6, 0, 0xff, 0xff]);

  c = one(build.waypointsSelected(3, [[1, 2]]));
  assert.equal(c.raw.length, 7);
  assert.deepEqual([...c.raw], [0x19, 1, 3, 1, 0, 2, 0]);
});

test('a payload with several commands splits in order', () => {
  const payload = Buffer.concat([build.waypointsSelected(3, [[1, 2]]), build.orderSelected(3, 2), build.sync(1, 2)]);
  const cmds = splitCommands(payload);
  assert.deepEqual(cmds.map((c) => c.type), [T.WAYPOINTS_SEL, T.ORDER_SEL, T.SYNC]);
  assert.equal(cmds.reduce((n, c) => n + c.raw.length, 0), payload.length);
});

test('malformed payloads throw ProtocolError', () => {
  assert.throws(() => splitCommands(Buffer.from([0x1c, 0, 0])), ProtocolError); // no handler
  assert.throws(() => splitCommands(Buffer.from([0x00])), ProtocolError); // stray terminator
  assert.throws(() => splitCommands(Buffer.from([0x02, 1, 2, 3])), ProtocolError); // short UNTIL
  assert.throws(() => splitCommands(Buffer.from([0x65, 65, 66])), ProtocolError); // unterminated string
  assert.throws(() => splitCommands(Buffer.from([0x14, 1, 5, 0])), ProtocolError); // select w/o 0xFFFF
  assert.throws(() => splitCommands(Buffer.from([0x19, 9, 1])), ProtocolError); // 9 waypoints
  assert.throws(() => splitCommands(Buffer.from([0x67, 1, 0])), ProtocolError); // name w/o string
  assert.throws(() => commandLength(Buffer.from([0x71, 0x71, 0x99]), 2), ProtocolError);
});

test('sanitizers keep printable Latin-1 and cut names to 16', () => {
  assert.equal(sanitizeName('  Nikolajs Agafonovs Rules  '), 'Nikolajs Agafono');
  assert.equal(sanitizeName('\x01\x02', 'Player3'), 'Player3');
  assert.equal(sanitizeText('a\nb\tc\x7f'), 'abc');
  assert.equal(sanitizeText('x'.repeat(300)).length, 200);
});

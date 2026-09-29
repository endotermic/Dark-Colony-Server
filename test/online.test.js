// ONLINE WAR (plan §20, protocol doc §4.4): the room table for the patched Ultimate exe, ENTER with a
// relay-chosen slot, refusals, sequence reset, and a stock hall client unaffected.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { HallHarness, cmdsOf } from './helpers.js';
import { T, build, splitCommands, decode, ROOM_STATE, ROOM_STATE_TEXT, MAX_ROOM_ROW } from '../src/commands.js';
import { STATE } from '../src/constants.js';
import { rowText, HEADER, COLUMNS, roomEntry, roomsPayload, terrainName } from '../src/online.js';
import { loadConfig } from '../src/config.js';

const roomsOf = (cmds) => cmds.filter((c) => c.type === T.ROOMS).map((c) => c.rooms);

test('messages: LIST, ENTER, ENTERING, REFUSED and ROOMS round-trip through split and decode', () => {
  for (const [buf, type, len, fields] of [
    [build.list(), T.LIST, 1, {}],
    [build.enter(3), T.ENTER, 2, { id: 3 }],
    [build.entering(5), T.ENTERING, 2, { id: 5 }],
    [build.refused('Room 2: it is full.'), T.REFUSED, 21, { reason: 'Room 2: it is full.' }],
  ]) {
    const cmds = splitCommands(buf);
    assert.equal(cmds.length, 1);
    assert.equal(cmds[0].type, type);
    assert.equal(buf.length, len);
    assert.deepEqual(decode(cmds[0]), fields);
  }
  const rooms = [
    { id: 1, state: ROOM_STATE.OPEN, seats: 8, players: 2, bots: 1, terrain: 'Jungle', name: 'Plink - O', row: 'x' },
    { id: 7, state: ROOM_STATE.IN_BATTLE, seats: 8, players: 3, bots: 2, terrain: 'Desert', name: 'Rings of fire', row: 'y' },
  ];
  const p = build.rooms(rooms);
  const cmds = splitCommands(Buffer.concat([p, build.keepalive()]));
  assert.equal(cmds.length, 2, 'the variable-length ROOMS is measured correctly');
  assert.deepEqual(decode(cmds[0]).rooms, rooms);
  assert.equal(cmds[1].type, T.KEEPALIVE);
  assert.throws(() => splitCommands(Buffer.from([T.ROOMS, 1, 1, 0, 8, 0, 0, 0x41])), /unterminated|truncated/);
});

test('rowText: fixed monospace columns within 64 characters, header with the same columns', () => {
  const e = { id: 2, state: ROOM_STATE.OPEN, seats: 8, players: 2, bots: 1, terrain: 'Desert', name: 'Armageddon' };
  const row = rowText(e);
  assert.equal(row, 'Armageddon         Desert   8     2       1    open');
  assert.equal(HEADER, 'MAP                TERRAIN  SEATS PLAYERS BOTS STATUS');
  assert.equal(COLUMNS.reduce((n, [, w]) => n + w, 0) + COLUMNS.length - 1, 56);
  const long = rowText({ ...e, name: 'Monkey in the Middle of Nowhere', state: ROOM_STATE.IN_BATTLE, terrain: 'Atlantis' });
  assert.ok(long.length <= MAX_ROOM_ROW);
  assert.equal(long, 'Monkey in the Midd Atlantis 8     2       1    in battle');
  assert.equal(ROOM_STATE_TEXT[ROOM_STATE.STARTING], 'starting');
  assert.equal(terrainName('jungle'), 'Jungle');
});

test('roomEntry: seats = the map, players = real people, bots = the fakes; the state follows the room', () => {
  const h = new HallHarness();
  const e = roomEntry(h.room);
  assert.equal(e.id, 1);
  assert.equal(e.seats, 8);
  assert.equal(e.players, 0);
  assert.equal(e.bots, 1, 'Mercenary');
  assert.equal(e.terrain, 'Jungle');
  assert.equal(e.name, 'Plink - O');
  assert.equal(e.state, ROOM_STATE.OPEN);
  assert.equal(e.row, rowText(e));
  const cmds = splitCommands(roomsPayload(h.pool.rooms));
  assert.equal(cmds.length, 1);
  assert.equal(decode(cmds[0]).rooms.length, 7);
});

test('LIST marks the client: it gets ROOMS at once and again only when the table changes; no lobby rows any more', () => {
  const h = new HallHarness({ HALL_REFRESH_MS: 100 });
  const p = h.enter('Exe');
  p.take(); // the hall dump the module ignores
  p.send(build.list());
  let rooms = roomsOf(p.takeCmds());
  assert.equal(rooms.length, 1);
  assert.equal(rooms[0].length, 7);
  assert.equal(rooms[0][0].players, 0);
  h.stepAfter(200);
  assert.equal(p.take().length, 0, 'nothing changed: no refresh traffic');
  // a stock client joins room 1: the table changes and the online client gets it once
  const q = h.enter('Stock');
  q.take();
  q.cdReport();
  q.chat('/1');
  q.pressReady();
  assert.equal(q.roomOf(h.pool), h.room);
  h.stepAfter(200);
  const cmds = p.takeCmds();
  rooms = roomsOf(cmds);
  assert.equal(rooms.length, 1);
  assert.equal(rooms[0][0].players, 1);
  assert.ok(!cmds.some((c) => c.type === T.NAME || c.type === T.SCENARIO), 'no lobby rows for an online client');
  h.stepAfter(200);
  assert.equal(p.take().length, 0);
  // keep-alives keep it alive like any hall client
  h.advance(2500);
  p.keepalive();
  h.tick();
  assert.ok(!p.gone);
});

test('ENTER seats the client in a random seatable slot of that room, sends ENTERING, resets the sequence and joins with the d handshake', () => {
  const h = new HallHarness();
  const p = h.enter('Exe');
  p.take();
  p.send(build.list());
  p.take();
  p.keepalive();
  const hallSlot = p.slot;
  h.randomSeq = [3]; // the 4th seatable slot
  p.send(build.enter(2));
  const cmds = p.takeCmds();
  const entering = cmds.find((c) => c.type === T.ENTERING);
  assert.ok(entering, 'ENTERING first');
  const d = cmds.find((c) => c.type === T.VERSION);
  assert.ok(d, "then the 'd' handshake");
  assert.equal(d.id, entering.id, 'same slot in both');
  const room = h.pool.rooms[1];
  assert.equal(room.slots[entering.id].client?.socket, p.sock, 'seated in room 2');
  assert.equal(entering.id, room.seatableSlots().length ? 4 : 4, 'the 4th seatable slot (1..7 without the fake in 0) is 4');
  assert.notEqual(entering.id, 0, 'never Mercenary\'s slot');
  assert.equal(h.hall.clients.size, 0);
  // the frames after ENTERING are numbered from 0 again: the 'd' frame is sequence 0
  const frames = p.all.length; // payloads only; check the counters directly
  const client = room.slots[entering.id].client;
  assert.ok(frames > 0);
  assert.equal(client.seqIn, 0, 'the game\'s first frame will be sequence 0');
  assert.equal(client.firstMessageAt, 0, 'the join grace starts over for the game\'s own connection (F71)');
  assert.equal(client.joinedAt, h.t, 'the join clock restarts at ENTER (15 s grace for the game itself)');
  // the game (through the exe's proxy) now sends its first frame with sequence 0
  p.seq = 0;
  p.cdReport();
  assert.ok(!p.gone, 'sequence 0 accepted after the reset');
  assert.ok(cmds.some((c) => c.type === T.SCENARIO), 'the room dump followed');
  assert.notEqual(hallSlot, -1);
});

test('ENTER is refused with a reason (unknown room, full, in battle) and a fresh ROOMS follows; a stock client is unaffected', () => {
  const h = new HallHarness({ FAKE_PLAYERS: 1 });
  const p = h.enter('Exe');
  p.take();
  p.send(build.list());
  p.take();
  p.send(build.enter(9));
  let cmds = p.takeCmds();
  assert.equal(cmds.find((c) => c.type === T.REFUSED)?.reason, 'There is no room 9; rooms are 1..7.');
  assert.equal(roomsOf(cmds).length, 1, 'a fresh table after the refusal');
  // room 1 in battle
  h.room.state = STATE.RUNNING;
  p.send(build.enter(1));
  cmds = p.takeCmds();
  assert.match(cmds.find((c) => c.type === T.REFUSED).reason, /battle is in progress/);
  assert.equal(roomsOf(cmds)[0][0].state, ROOM_STATE.IN_BATTLE);
  h.room.state = STATE.LOBBY;
  // a full room: fill room 3 (8 seats, 1 fake) with 7 stock clients
  const room = h.pool.rooms[2];
  const stock = [];
  for (let i = 0; i < 7; i++) {
    const q = h.enter(`S${i}`);
    q.take();
    q.cdReport();
    q.chat('/3');
    q.pressReady();
    if (q.roomOf(h.pool) === room) stock.push(q);
  }
  assert.ok(room.isFull() || room.seatableSlots().length === 0, `room 3 is full (${room.clients.size} clients)`);
  p.send(build.enter(3));
  cmds = p.takeCmds();
  assert.match(cmds.find((c) => c.type === T.REFUSED).reason, /full|no free slot/);
  assert.equal(roomsOf(cmds)[0][2].state, ROOM_STATE.FULL);
  assert.ok(!p.gone, 'refusals are not violations');
  // the stock clients in room 3 saw no ONLINE WAR message
  for (const q of stock) assert.ok(!q.takeCmds().some((c) => c.type >= 0x50 && c.type <= 0x54));
});

test('config: TLS_PORT needs the certificate and key files', () => {
  assert.throws(() => loadConfig({}, { TLS_PORT: 8889 }), /TLS_CERT/);
  const cfg = loadConfig({}, { TLS_PORT: 8889, TLS_CERT: 'a.pem', TLS_KEY: 'b.pem' });
  assert.equal(cfg.TLS_PORT, 8889);
  assert.equal(loadConfig({}).TLS_PORT, 0);
});

test('cmdsOf decodes ROOMS entries with their rows', () => {
  const h = new HallHarness();
  const cmds = cmdsOf(roomsPayload(h.pool.rooms));
  assert.equal(cmds[0].rooms[1].name, 'Armageddon');
  assert.ok(cmds[0].rooms[1].row.startsWith('Armageddon'));
});

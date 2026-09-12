// The room-selection lobby (plan §17): rows, marquee, chat commands, READY as "join", per-room maps.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { HallHarness, cmdsOf } from './helpers.js';
import { T, build, MAX_NAME } from '../src/commands.js';
import { STATE } from '../src/constants.js';
import { loadConfig } from '../src/config.js';
import { packPayloads } from '../src/client.js';
import { CHAT_ROWS } from '../src/chat.js';
import { HALL_TITLE_PREFIX, marquee } from '../src/hall.js';

// default rooms: 1 Plink - O (jungle), 2 Armageddon, 3 Black Widow, 4 Circle of Friends, 5 Olympus Mons (desert),
// 6 Hoops of Fury, 7 Rings of fire (jungle)

/** The hall's Client object of a peer. */
const hallClient = (h, p) => [...h.hall.clients].find((c) => c.slot === p.slot);
/** Chat lines in a command list (the last ten are the window as the client shows it). */
const chatOf = (cmds) => cmds.filter((c) => c.type === T.LOBBY_CHAT).map((c) => c.text);
const windowOf = (cmds) => chatOf(cmds).slice(-CHAT_ROWS);
/** All chat text joined: lines are wrapped at spaces, so a phrase can be searched across lines. */
const chatText = (cmds) => chatOf(cmds).join(' ').replace(/\s+/g, ' ');

test('marquee: short texts are static, long texts scroll one character per step and wrap around', () => {
  assert.equal(marquee('Room', 16, 5), 'Room');
  const t = 'Armageddon desert (0/7) open';
  assert.equal(marquee(t, 14, 0), 'Armageddon des');
  assert.equal(marquee(t, 14, 1), 'rmageddon dese');
  const period = t.length + 3; // text + separator
  assert.equal(marquee(t, 14, period), marquee(t, 14, 0));
  for (let i = 0; i < period; i++) assert.equal(marquee(t, 14, i).length, 14);
  assert.equal(marquee(t, 14, t.length + 1), `  ${t.slice(0, 12)}`, 'the separator scrolls through before the text repeats');
});

test('packPayloads: commands are concatenated into frames of at most 1021 bytes', () => {
  const small = [build.keepalive(), build.ready(1, 2), build.name(3, 'Nika')];
  const packed = packPayloads(small);
  assert.equal(packed.length, 1);
  assert.deepEqual(cmdsOf(packed[0]).map((c) => c.type), [T.KEEPALIVE, T.READY, T.NAME]);
  const many = Array.from({ length: 60 }, (_, i) => build.name(i % 8, 'x'.repeat(16))); // 20 bytes each
  const frames = packPayloads(many);
  assert.equal(frames.length, 2);
  assert.ok(frames.every((f) => f.length <= 1021));
  assert.equal(frames.reduce((n, f) => n + cmdsOf(f).length, 0), 60);
  assert.throws(() => packPayloads([Buffer.alloc(1022)]), /does not fit/);
});

test('config: ROOMS entries resolve against the map table, custom maps need a name, capacity is checked', () => {
  assert.throws(() => loadConfig({}, { ROOMS: '' }), /ROOMS/);
  assert.throws(() => loadConfig({}, { ROOMS: 'D8PLAY01,D8PLAY02,D8PLAY03,D8PLAY04,D8PLAY05,D8PLAY06,D8PLAY07,D8PLAY08' }), /ROOMS/, 'eight rooms do not fit');
  assert.throws(() => loadConfig({}, { ROOMS: 'X9BAD' }), /2nd character/);
  assert.throws(() => loadConfig({}, { ROOMS: 'X8UNKNOWN' }), /unknown map/);
  assert.throws(() => loadConfig({}, { ROOMS: 'D4PLAY01', FAKE_PLAYERS: 4 }), /leaves no seat/);
  const cfg = loadConfig({}, { ROOMS: 'd8play01, J4PLAY01:My Jungle:jungle ,X8CUSTOM.SCN:Custom' });
  assert.deepEqual(cfg.ROOM_LIST.map((m) => m.file), ['D8PLAY01.SCN', 'J4PLAY01.SCN', 'X8CUSTOM.SCN']);
  assert.deepEqual(cfg.ROOM_LIST.map((m) => m.index), [1, 2, 3]);
  assert.equal(cfg.ROOM_LIST[0].name, 'Armageddon');
  assert.equal(cfg.ROOM_LIST[1].name, 'My Jungle');
  assert.equal(cfg.ROOM_LIST[1].players, 4);
  assert.equal(cfg.ROOM_LIST[1].titleWire[45], '4');
  assert.equal(cfg.ROOM_LIST[1].titleWire.slice(43), ' (4 Player Jungle Map )');
  assert.equal(cfg.ROOM_LIST[2].terrain, 'desert');
  const dflt = loadConfig({});
  assert.equal(dflt.ROOM_LIST.length, 7, 'the default is seven rooms');
  assert.equal(dflt.ROOM_LIST[0].name, 'Plink - O', 'the default room 1 is a jungle map');
  assert.equal(dflt.ROOM_LIST[0].terrain, 'jungle');
  assert.equal(dflt.ROOM_LIST[1].name, 'Armageddon');
  assert.equal(dflt.MARQUEE_MS, 200);
  assert.ok(dflt.PACK_LOBBY_FRAMES);
  // defaults since 7 Sep 2026: one fake (Mercenary) and one real player is enough to start
  assert.equal(dflt.FAKE_PLAYERS, 1);
  assert.equal(dflt.MIN_PLAYERS, 1);
  assert.equal(loadConfig({}, { FAKE_PLAYERS: 7, LOG_LEVEL: 'debug' }).FAKE_PLAYERS, 7, 'debug mode does not touch the fakes');
});

test("a newcomer gets 'd', then one packed frame: rooms 1..7 in the rows in place, its own name row, an empty map line, the chat window", () => {
  const h = new HallHarness();
  const p = h.enter('A');
  const c = hallClient(h, p);
  const payloads = p.take();
  assert.equal(payloads.length, 2, "'d' alone, then everything else in one frame (F34)");
  assert.equal(payloads[0][0], T.VERSION);
  const cmds = payloads.flatMap(cmdsOf);
  assert.equal(cmds[0].id, p.slot);
  assert.ok(p.slot >= 1 && p.slot <= 7);
  const scen = cmds.find((cmd) => cmd.type === T.SCENARIO);
  assert.equal(scen.title, h.hall.titleFor(c));
  assert.equal(c.selected, -1, 'no room is preselected (maintainer, 12 Sep 2026)');
  assert.equal(scen.file, '', 'no file: the client disables READY while the scenario file name is empty (F42)');
  assert.equal(scen.title, '', 'the map line is empty while no room is selected (maintainer, 12 Sep 2026)');
  for (let q = 0; q < 8; q++) {
    assert.ok(cmds.some((cmd) => cmd.type === T.TYPE && cmd.player === q && cmd.value === 2), `row ${q} human`);
    assert.ok(cmds.some((cmd) => cmd.type === T.READY && cmd.player === q && cmd.status === 1), `row ${q} present, not ready (F3)`);
    assert.ok(cmds.some((cmd) => cmd.type === T.COLOUR_SET && cmd.player === q && cmd.value === q), `row ${q} colour`);
    assert.ok(cmds.some((cmd) => cmd.type === T.VAR && cmd.index === 8 + q && cmd.value === 1), `row ${q} joinable icon`);
  }
  assert.ok(!cmds.some((cmd) => cmd.type === T.READY && cmd.status === 2), 'no colour locks (F20)');
  const names = cmds.filter((cmd) => cmd.type === T.NAME);
  assert.equal(names.length, 8);
  for (const n of names) {
    assert.ok(n.name.length <= MAX_NAME);
    assert.ok(!n.name.includes(':'), 'row texts never contain a colon (chat prefix)');
  }
  const nameAt = (q) => names.find((n) => n.player === q).name;
  assert.equal(nameAt(p.slot), `Player${p.slot}`, 'the own row shows the player name (F33)');
  // rooms 1..7 fill the rows in order, skipping the own row; every row starts with "<n> "
  const roomRows = [0, 1, 2, 3, 4, 5, 6].map((i) => h.hall.rowOf(c, i));
  assert.deepEqual(roomRows, [0, 1, 2, 3, 4, 5, 6, 7].filter((q) => q !== p.slot));
  for (let i = 0; i < 7; i++) {
    assert.equal(h.hall.roomAt(c, roomRows[i]).id, i + 1);
    assert.ok(nameAt(roomRows[i]).startsWith(`${i + 1} `), `row of room ${i + 1} starts with its number`);
    assert.equal(nameAt(roomRows[i]).length, 16);
  }
  assert.equal(h.hall.roomAt(c, p.slot), null);
  assert.equal(nameAt(0), '1 Plink - O jung', "room 1 is always row 0 (Mercenary's slot); the terrain follows the map name");
  assert.equal(nameAt(roomRows[1]), '2 Armageddon des');
  assert.equal(nameAt(roomRows[6]), '7 Rings of fire ');
  // the chat window: ten lines, static header on top, no name in front of relay lines (§17.8)
  const chat = chatOf(cmds);
  assert.equal(chat.length, CHAT_ROWS);
  assert.ok(chat.every((t) => t.length >= 1 && t.length <= 40), 'no line wraps on the client');
  assert.deepEqual(chat.slice(0, 6), [
    'Welcome to Dark Colony server 2.1.',
    'Type /1../7 + ENTER to select a room,',
    'then press READY to join it.',
    'The map line shows the selected room.',
    'You may type your name in your row.',
    'No room selected. Type /1../7 + ENTER.',
  ]);
  assert.ok(chat.slice(6).every((t) => t === ' '));
  assert.deepEqual(hallClient(h, p).chat.header.length, 6, 'all six greeting lines are static');
  assert.ok(!chat.some((t) => t.startsWith('Mercenary:')));
  assert.equal(h.hall.clients.size, 1);
  assert.equal(h.room.clients.size, 0);
});

test('READY without a selection is refused: nothing is preselected, the client stays in the hall until it types /N', () => {
  const h = new HallHarness();
  const p = h.enter('A');
  p.take();
  p.cdReport();
  p.pressReady();
  const cmds = p.takeCmds();
  assert.equal(h.hall.clients.size, 1, 'still in the hall');
  assert.equal(h.room.clients.size, 0, 'room 1 did not get the client');
  assert.ok(!cmds.some((cmd) => cmd.type === T.SCENARIO), 'no room dump');
  const win = windowOf(cmds);
  assert.equal(win[5], 'No room selected. Type /1../7 + ENTER.', 'the header still says so');
  assert.equal(win[6], 'Select a room first: /1../7 + ENTER.', 'the refusal, below the header');
  p.chat('/1');
  p.pressReady();
  assert.equal(p.roomOf(h.pool), h.room, 'after typing the number READY joins');
});

test('room rows: the number stays in place, the rest is padded to one length so all rows scroll with the same period', () => {
  const h = new HallHarness({ MARQUEE_MS: 100 });
  const p = h.enter('A');
  const c = hallClient(h, p);
  p.take();
  p.cdReport();
  const start = h.hall.rowsFor(c).map((r) => r.text);
  // the longest detail is "Circle of Friends desert (0/7) open" (35 characters); period = 35 + 3
  const longest = Math.max(...h.pool.rooms.map((r) => h.hall.detail(r, c.slot).length));
  assert.equal(longest, 35);
  const period = longest + 3;
  for (let i = 0; i < period; i++) {
    h.advance(100);
    h.step();
    const now = h.hall.rowsFor(c).map((r) => r.text);
    if (i < period - 1) assert.notDeepEqual(now, start);
    for (let q = 0; q < 8; q++) {
      const room = h.hall.roomAt(c, q);
      if (room) assert.ok(now[q].startsWith(`${room.id} `), `row ${q} keeps "${room.id} " at offset ${i + 1}`);
    }
  }
  assert.deepEqual(h.hall.rowsFor(c).map((r) => r.text), start, 'every room row is back at its start after one common period');
  // one step in: the number stands, the detail moved by one character
  h.advance(100);
  h.step();
  const rows = h.hall.rowsFor(c);
  assert.equal(rows[h.hall.rowOf(c, 6)].text, '7 ings of fire j');
  assert.equal(rows[0].text, '1 link - O jungl');
  // offset 30 of a 31-character detail padded to 35: the last letter, four padding spaces, the separator, the wrap
  assert.equal(marquee('Rings of fire jungle (0/7) open'.padEnd(35), 14, 30), 'n       Rings ', 'padding then the wrap');
});

test('the name may be typed in the hall and follows into the room; race, colour and team changes are dropped', () => {
  const h = new HallHarness();
  const p = h.enter('A');
  p.take();
  p.cdReport();
  p.send(build.race(1, p.slot));
  p.send(build.colourCycle(1, p.slot));
  p.send(build.teamCycle(1, p.slot));
  assert.equal(p.takeCmds().length, 0, 'nothing echoed');
  p.send(build.name(p.slot, 'Nika'));
  const echo = p.takeCmds();
  assert.deepEqual(echo.map((c) => [c.type, c.player, c.name]), [[T.NAME, p.slot, 'Nika']]);
  assert.equal(hallClient(h, p).name, 'Nika');
  p.send(build.name((p.slot % 7) + 1, 'Hacked'));
  assert.equal(p.takeCmds().length, 0, 'foreign slot dropped');
  h.advance(200);
  h.step();
  assert.ok(!p.takeCmds().some((c) => c.type === T.NAME && c.player === p.slot), 'the own row is not re-sent by the marquee');
  p.chat('/1');
  p.take();
  p.pressReady();
  const cmds = p.takeCmds();
  assert.equal(h.room.slots[p.slot].name, 'Nika');
  assert.ok(cmds.some((c) => c.type === T.NAME && c.player === p.slot && c.name === 'Nika'));
  const win = windowOf(cmds);
  assert.equal(win[0], 'Room 1: Plink - O, jungle, 8 players.', 'the one-line room header; no name, not even a typed one');
  assert.equal(win[1], ' ', 'present-not-ready: nothing below the header');
  assert.ok(!p.gone);
});

test('/N selects a room: the map line shows it, the rows stay; READY moves the client in with its slot, slots cleared, a fresh chat window', () => {
  const h = new HallHarness();
  const p = h.enter('A');
  const c = hallClient(h, p);
  p.take();
  p.cdReport();
  p.chat('/3');
  let payloads = p.take();
  assert.equal(payloads.length, 2, 'one chat window frame and one packed row/title update');
  let cmds = payloads.flatMap(cmdsOf);
  const win = windowOf(cmds);
  assert.equal(win.length, CHAT_ROWS);
  assert.equal(win[0], 'Welcome to Dark Colony server 2.1.', 'the header stays on top');
  assert.equal(win[5], 'Room 3 (Black Widow) is selected.', 'the selection line of the header changed in place');
  assert.ok(win.slice(6).every((t) => t === ' '), 'no message was added');
  assert.ok(win.every((t) => t.length <= 40));
  const scen = cmds.find((cmd) => cmd.type === T.SCENARIO);
  assert.ok(scen.title.startsWith('>3 Black Widow desert (0/7) open\n'), scen.title);
  assert.equal(scen.title[45], '8');
  assert.ok(scen.title.endsWith('(8 Player Desert Map )'));
  assert.equal(cmds.filter((cmd) => cmd.type === T.NAME).length, 0, 'the rows do not change on selection');
  assert.equal(h.hall.roomAt(c, h.hall.rowOf(c, 2)).id, 3, 'room 3 keeps its row');

  p.pressReady();
  payloads = p.take();
  assert.equal(payloads.length, 1, 'the slot clear, room dump and chat window arrive as one frame');
  cmds = cmdsOf(payloads[0]);
  const room = h.pool.rooms[2];
  assert.equal(h.hall.clients.size, 0);
  assert.equal(room.clients.size, 1);
  assert.equal(p.roomOf(h.pool), room);
  assert.equal(room.slots[p.slot].client.slot, p.slot, 'slot number unchanged (F30)');
  assert.ok(!cmds.some((cmd) => cmd.type === T.VERSION), "no second 'd' (F30)");
  const roomScen = cmds.find((cmd) => cmd.type === T.SCENARIO);
  assert.equal(roomScen.file, 'D8PLAY02.SCN');
  assert.equal(roomScen.title, room.map.titleWire);
  assert.ok(cmds.some((cmd) => cmd.type === T.NAME && cmd.player === 0 && cmd.name === 'Mercenary'), 'rows are real names again');
  assert.ok(cmds.some((cmd) => cmd.type === T.NAME && cmd.player === p.slot && cmd.name === `Player${p.slot}`));
  assert.ok(cmds.some((cmd) => cmd.type === T.READY && cmd.player === p.slot && cmd.status === 1), 'present, not ready (no automatic start)');
  for (const s of [1, 2, 3, 4, 5, 6, 7].filter((x) => x !== p.slot)) {
    assert.ok(cmds.some((cmd) => cmd.type === T.TYPE && cmd.player === s && cmd.value === 3), `slot ${s} empty in the room`);
  }
  // the hall's seven occupied rows are emptied first: DISCONNECT for every slot but the own one and Mercenary's
  const dcs = cmds.filter((cmd) => cmd.type === T.DISCONNECT).map((cmd) => cmd.player).sort();
  assert.deepEqual(dcs, [1, 2, 3, 4, 5, 6, 7].filter((s) => s !== p.slot));
  assert.ok(cmds.findIndex((cmd) => cmd.type === T.DISCONNECT) < cmds.findIndex((cmd) => cmd.type === T.SCENARIO), 'DISCONNECTs, then the dump');
  assert.ok(cmds.findIndex((cmd) => cmd.type === T.SCENARIO) < cmds.findIndex((cmd) => cmd.type === T.LOBBY_CHAT), 'then the chat window');
  const lines = windowOf(cmds);
  assert.equal(lines.length, CHAT_ROWS, 'the whole window is repainted: the hall chat is gone');
  assert.equal(lines[0], 'Room 3: Black Widow, desert, 8 players.');
  assert.ok(lines.slice(1).every((t) => t === ' '), 'one header line, nothing else');

  // the hall no longer writes to this client
  h.advance(1000);
  h.step();
  assert.equal(p.takeCmds().filter((cmd) => cmd.type === T.NAME).length, 0);

  // a second player types the bare number, joins the same room, and the room starts as before
  const q = h.enter('B');
  q.take();
  q.cdReport();
  q.chat('3');
  q.pressReady();
  assert.equal(room.clients.size, 2);
  assert.notEqual(q.slot, p.slot);
  assert.equal(windowOf(q.takeCmds())[0], 'Room 3: Black Widow, desert, 8 players.');
  assert.equal(room.state, STATE.LOBBY, 'nobody is ready yet: no automatic start');
  // the stale READY button (F36): the first click sends status 1, a no-op; the second readies
  p.take();
  p.send(build.ready(1, p.slot));
  assert.equal(room.slots[p.slot].status, 1);
  assert.equal(room.state, STATE.LOBBY);
  p.pressReady();
  const afterReady = windowOf(p.takeCmds());
  assert.equal(afterReady[0], 'Room 3: Black Widow, desert, 8 players.', 'the header stays while messages arrive');
  assert.ok(afterReady.includes(`Player${p.slot} is ready (1/2)`), afterReady.join('|'));
  q.pressReady();
  assert.equal(room.state, STATE.STARTING);
  assert.equal(h.pool.rooms[0].state, STATE.LOBBY, 'the other rooms are untouched');
});

test('the rows scroll: after MARQUEE_MS all seven room rows shift by one character in ONE frame; the own row and map line stay', () => {
  const h = new HallHarness({ MARQUEE_MS: 300 });
  const p = h.enter('A');
  const c = hallClient(h, p);
  p.take();
  p.cdReport();
  h.advance(299);
  h.step();
  assert.equal(p.takeCmds().length, 0);
  h.advance(1);
  h.step();
  const payloads = p.take();
  assert.equal(payloads.length, 1, 'one frame per step (F34)');
  const cmds = cmdsOf(payloads[0]);
  const names = cmds.filter((cmd) => cmd.type === T.NAME);
  assert.equal(names.length, 7, 'the seven room rows scroll, the own row does not');
  assert.ok(!names.some((n) => n.player === p.slot));
  assert.equal(names.find((n) => n.player === h.hall.rowOf(c, 1)).name, '2 rmageddon dese');
  assert.equal(cmds.filter((cmd) => cmd.type === T.VAR).length, 0, 'icons unchanged');
  assert.equal(cmds.filter((cmd) => cmd.type === T.SCENARIO).length, 0, 'the map line does not scroll');
  assert.equal(cmds.filter((cmd) => cmd.type === T.LOBBY_CHAT).length, 0, 'the chat is not repainted by the marquee');
  h.advance(300);
  h.step();
  assert.equal(p.takeCmds().find((cmd) => cmd.type === T.NAME && cmd.player === h.hall.rowOf(c, 1)).name, '2 mageddon deser');
});

test('a room in battle shows the icon off; nothing is preselected; the map line follows the selected room; READY there is refused', () => {
  const h = new HallHarness({ MIN_PLAYERS: 1 });
  const a = h.join('A'); // direct join into room 1
  a.take();
  a.cdReport();
  a.pressReady();
  assert.equal(h.room.state, STATE.STARTING);
  const p = h.enter('P');
  const c = hallClient(h, p);
  let cmds = p.takeCmds();
  p.cdReport();
  assert.equal(c.selected, -1, 'no room is preselected, not even an open one');
  assert.equal(cmds.find((cmd) => cmd.type === T.SCENARIO).title, '', 'empty map line');
  assert.ok(cmds.some((cmd) => cmd.type === T.VAR && cmd.index === 8 && cmd.value === 0), 'row 0 = room 1: icon off');
  assert.ok(cmds.find((cmd) => cmd.type === T.NAME && cmd.player === 0).name.startsWith('1 Plink - O'));
  p.chat('/1');
  p.pressReady();
  cmds = p.takeCmds();
  const text = chatText(cmds);
  assert.ok(text.includes('Room 1 (Plink - O) is selected.'), text);
  assert.ok(text.includes('Room 1: a battle is in progress there'), text);
  assert.ok(text.includes('Cannot join room 1: a battle is in progress'));
  assert.ok(cmds.find((cmd) => cmd.type === T.SCENARIO).title.startsWith('>1 Plink - O jungle (1/7) in battle'), 'map line shows the state');
  assert.equal(h.hall.clients.size, 1);
  assert.equal(h.room.clients.size, 1);
  // when the battle ends the map line and icon update within one step
  a.sock.destroy();
  assert.equal(h.room.state, STATE.LOBBY);
  h.advance(200);
  h.step();
  cmds = p.takeCmds();
  assert.ok(cmds.find((cmd) => cmd.type === T.SCENARIO).title.startsWith('>1 Plink - O jungle (0/7) open'));
});

test('slot conflicts: a slot taken by a real player is reported, and newcomers avoid it', () => {
  const h = new HallHarness();
  const p = h.enter('P');
  p.take();
  p.cdReport();
  p.chat('/1');
  p.take();
  // a direct joiner takes exactly p's slot in room 1 (random index p.slot-1 of the free list 1..7)
  h.randomSeq = [p.slot - 1];
  const a = h.join('A');
  assert.equal(a.slot, p.slot);
  a.take();
  a.cdReport();
  h.advance(200);
  h.step();
  const cmds = p.takeCmds();
  assert.ok(cmds.find((cmd) => cmd.type === T.SCENARIO).title.startsWith('>1 Plink - O jungle (1/7) slot taken'), 'room 1 is selected: the map line says why');
  assert.ok(cmds.some((cmd) => cmd.type === T.VAR && cmd.index === 8 && cmd.value === 0), 'row 0 = room 1: icon off');
  p.pressReady();
  const text = chatText(p.takeCmds());
  assert.ok(text.includes(`your slot ${p.slot} is taken there`), text);
  assert.equal(h.hall.clients.size, 1);
  // a newcomer gets a slot that is free everywhere
  const q = h.enter('Q');
  assert.notEqual(q.slot, p.slot);
  // p can still join room 2
  p.chat('/2');
  p.pressReady();
  assert.equal(p.roomOf(h.pool), h.pool.rooms[1]);
});

test('seven fakes: rooms show (0/7), stay joinable, the fake in the way moves, all fake names are in the dump', () => {
  const h = new HallHarness({ FAKE_PLAYERS: 7, MIN_PLAYERS: 1 });
  const p = h.enter('P');
  const c = hallClient(h, p);
  let cmds = p.takeCmds();
  p.cdReport();
  for (let q = 0; q < 8; q++) assert.ok(cmds.some((cmd) => cmd.type === T.VAR && cmd.index === 8 + q && cmd.value === 1), `row ${q} joinable`);
  assert.ok(cmds.find((cmd) => cmd.type === T.NAME && cmd.player === h.hall.rowOf(c, 3)).name.startsWith('4 Circle of Fri'));
  const room = h.pool.rooms[3];
  assert.equal(room.seats(), 1, 'one real seat in truth');
  p.chat('/4');
  cmds = p.takeCmds(); // the selection update
  assert.ok(cmds.find((cmd) => cmd.type === T.SCENARIO).title.startsWith('>4 Circle of Friends desert (0/7) open'), 'the size shown is the map slots without Mercenary');
  p.pressReady();
  cmds = p.takeCmds();
  assert.equal(p.roomOf(h.pool), room);
  assert.equal(room.slots[p.slot].client.slot, p.slot);
  assert.equal(room.fakeSlots().length, 7, 'no fake was lost');
  assert.ok(!room.fakeSlots().some((f) => f.slot === p.slot));
  const names = cmds.filter((cmd) => cmd.type === T.NAME);
  for (const f of room.fakeSlots()) assert.ok(names.some((n) => n.player === f.slot && n.name === f.name), `${f.name} named in the dump`);
  const fakeNames = names.filter((n) => n.player !== p.slot).map((n) => n.name).sort();
  assert.deepEqual(fakeNames, ['Drifter', 'Marauder', 'Mercenary', 'Nomad', 'Outlaw', 'Renegade', 'Vagabond']);
  assert.ok(cmds.filter((cmd) => cmd.type === T.TYPE).every((cmd) => cmd.value === 2), 'all eight slots are humans');
  assert.equal(windowOf(cmds)[0], 'Room 4: Circle of Friends, desert, 8', 'a long header line wraps at 40 on the server side');
  p.pressReady();
  assert.equal(room.state, STATE.STARTING, 'one real player is enough with MIN_PLAYERS=1');
  // a second solo player goes to another room, whatever its slot
  const q = h.enter('Q');
  q.take();
  q.cdReport();
  q.chat('/2');
  q.pressReady();
  assert.equal(q.roomOf(h.pool), h.pool.rooms[1]);
});

test('hall deadlines: silent after the handshake is dropped, keep-alives keep a client, then their absence drops it', () => {
  const h = new HallHarness();
  const p = h.enter('P');
  p.take();
  h.advance(h.cfg.JOIN_TIMEOUT_MS + 1);
  h.tick();
  assert.ok(p.gone);
  assert.equal(h.hall.clients.size, 0);
  const q = h.enter('Q');
  q.take();
  q.cdReport();
  for (let i = 0; i < 10; i++) {
    h.advance(700);
    q.keepalive();
    h.tick();
  }
  assert.ok(!q.gone);
  h.advance(h.cfg.KEEPALIVE_TIMEOUT_MS + 1);
  h.tick();
  assert.ok(q.gone);
});

test("hall chat goes to the other waiting clients under the sender's name; /rooms and /help answer privately; the header stays", () => {
  const h = new HallHarness();
  const p = h.enter('P');
  p.take();
  p.cdReport();
  const q = h.enter('Q');
  q.take();
  q.cdReport();
  p.send(build.name(p.slot, 'Nika'));
  p.take();
  // whatever prefix the client puts in front, the server uses the name it knows
  p.send(build.lobbyChat('Player9: hello all'));
  const qWin = windowOf(q.takeCmds());
  assert.equal(qWin.length, CHAT_ROWS);
  assert.equal(qWin[0], 'Welcome to Dark Colony server 2.1.', "the other client's own header stays on top");
  assert.ok(qWin.includes('Nika: hello all'), qWin.join('|'));
  assert.ok(windowOf(p.takeCmds()).includes('Nika: hello all'));
  p.chat('/rooms');
  const list = windowOf(p.takeCmds());
  assert.equal(list.length, CHAT_ROWS);
  assert.deepEqual(list.slice(6), [
    '4 Circle of Friends desert (0/7) open',
    '5 Olympus Mons desert (0/7) open',
    '6 Hoops of Fury jungle (0/7) open',
    '7 Rings of fire jungle (0/7) open',
  ], 'six header rows leave four rows for messages');
  assert.equal(list[1], 'Type /1../7 + ENTER to select a room,');
  assert.equal(list[5], 'No room selected. Type /1../7 + ENTER.');
  assert.equal(q.takeCmds().length, 0, 'the list is private');
  // a flood of comments never eats a greeting line
  for (let i = 0; i < 20; i++) p.chat(`comment ${i}`);
  const flood = windowOf(p.takeCmds());
  assert.deepEqual(flood.slice(0, 6), hallClient(h, p).chat.header);
  assert.deepEqual(flood.slice(6), ['Nika: comment 16', 'Nika: comment 17', 'Nika: comment 18', 'Nika: comment 19']);
  const qFlood = windowOf(q.takeCmds());
  assert.deepEqual(qFlood.slice(0, 6), hallClient(h, q).chat.header, 'the other client keeps its own header too');
  assert.equal(qFlood[9], 'Nika: comment 19');
  p.chat('/help');
  const help = windowOf(p.takeCmds());
  assert.deepEqual(help.slice(7), ['/1../7 + ENTER selects a room.', '/rooms lists the rooms.', 'READY joins the selected room.'], 'three short help lines fit under the header');
  p.chat('/9');
  assert.ok(chatText(p.takeCmds()).includes('There is no room 9'));
  p.chat('/dance');
  assert.ok(chatText(p.takeCmds()).includes('Unknown command /dance'));
});

test('a 4-player room shows (0/3), seats three real players, caps MIN_PLAYERS, and unused rows are empty', () => {
  const h = new HallHarness({ ROOMS: 'D4PLAY01,D8PLAY01', MIN_PLAYERS: 5 });
  const room = h.room;
  assert.equal(room.seats(), 3);
  assert.equal(room.minPlayers, 3);
  for (const n of ['A', 'B', 'C']) {
    const p = h.enter(n);
    p.take();
    p.cdReport();
    p.chat('/1');
    p.pressReady(); // enters the room, present-not-ready
  }
  assert.equal(room.clients.size, 3);
  assert.equal(room.state, STATE.LOBBY);
  const d = h.enter('D');
  const c = hallClient(h, d);
  const cmds = d.takeCmds();
  assert.equal(c.selected, -1, 'nothing is preselected, full or not');
  assert.equal(cmds.find((cmd) => cmd.type === T.SCENARIO).title, '', 'empty map line');
  assert.equal(h.hall.rowOf(c, 0), 0);
  assert.equal(cmds.find((cmd) => cmd.type === T.NAME && cmd.player === 0).name, '1 Four Corners d');
  assert.ok(cmds.some((cmd) => cmd.type === T.VAR && cmd.index === 8 && cmd.value === 0), 'full: icon off');
  // rows without a room are empty, except the newcomer's own row, which shows its name
  for (let q = 2; q < 8; q++) {
    if (q === d.slot) {
      assert.ok(cmds.some((cmd) => cmd.type === T.TYPE && cmd.player === q && cmd.value === 2));
      assert.ok(cmds.some((cmd) => cmd.type === T.NAME && cmd.player === q && cmd.name === `Player${q}`));
    } else if (h.hall.roomAt(c, q) === null) {
      assert.ok(cmds.some((cmd) => cmd.type === T.TYPE && cmd.player === q && cmd.value === 3));
      assert.ok(cmds.some((cmd) => cmd.type === T.READY && cmd.player === q && cmd.status === 0));
    }
  }
  d.cdReport();
  d.chat('/1');
  d.pressReady();
  const text = chatText(d.takeCmds());
  assert.ok(text.includes('Room 1 (Four Corners) is selected.'), text);
  assert.ok(text.includes('Room 1: it is full'), text);
  assert.ok(text.includes('Cannot join room 1: it is full'));
  assert.equal(h.hall.clients.size, 1);
  // the three inside start when all are ready (minPlayers 3)
  for (const p of h.peers.slice(0, 3)) {
    p.take();
    p.pressReady();
  }
  assert.equal(room.state, STATE.STARTING);
});

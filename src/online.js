// The ONLINE WAR room table (plan §20, protocol doc §4.4): what the patched Dark Colony Ultimate
// exe shows in the room list of its ONLINE WAR screen. The exe's module sends 0x50 LIST right after
// connecting; the hall answers with 0x51 ROOMS - one entry per room with the structured fields and
// the ready-made list row - and repeats it whenever the table changes. The row is formatted here so
// that the layout can change without an exe rebuild: the screen's list is 56 monospace columns
// (MFONTO5: 7 px glyphs on an 8 px advance, 448 px), the columns below use all 56.
//
//   MAP                TERRAIN  SEATS PLAYERS BOTS STATUS
//   Armageddon         Desert   8     2       1    open

import { build, ROOM_STATE, ROOM_STATE_TEXT, MAX_ROOM_ROW } from './commands.js';
import { STATE } from './constants.js';

/** [field, width] in row order; single spaces between the columns. */
export const COLUMNS = Object.freeze([
  ['name', 18],
  ['terrain', 8],
  ['seats', 5],
  ['players', 7],
  ['bots', 4],
  ['status', 9],
]);

const HEADINGS = Object.freeze({ name: 'MAP', terrain: 'TERRAIN', seats: 'SEATS', players: 'PLAYERS', bots: 'BOTS', status: 'STATUS' });

function cell(text, width) {
  return String(text ?? '').slice(0, width).padEnd(width);
}

/** One list row from the fields of a ROOMS entry (`status` derived from `state` when absent). */
export function rowText(e) {
  const status = e.status ?? ROOM_STATE_TEXT[e.state] ?? '';
  const values = { ...e, status };
  return COLUMNS.map(([field, width]) => cell(values[field], width)).join(' ').trimEnd().slice(0, MAX_ROOM_ROW);
}

/** The header line the screen shows above the list (same columns). */
export const HEADER = COLUMNS.map(([field, width]) => cell(HEADINGS[field], width)).join(' ').trimEnd();

/** "desert" -> "Desert". */
export function terrainName(terrain) {
  const t = String(terrain ?? '').toLowerCase();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : '';
}

/** The wire state of a room: open, full, starting, in battle. */
export function roomState(room) {
  if (room.state === STATE.RUNNING) return ROOM_STATE.IN_BATTLE;
  if (room.state === STATE.STARTING) return ROOM_STATE.STARTING;
  if (room.isFull() || room.seatableSlots().length === 0) return ROOM_STATE.FULL;
  return ROOM_STATE.OPEN;
}

/** Why an ONLINE WAR client cannot enter `room` now, or null. */
export function enterBlocker(room) {
  if (room.state !== STATE.LOBBY) return 'a battle is in progress there, wait for it to end';
  if (room.isFull()) return 'it is full';
  if (room.seatableSlots().length === 0) return 'no free slot';
  return null;
}

/** The ROOMS entry of one room: seats = the map's player count, players = real people, bots = the relay's fakes. */
export function roomEntry(room) {
  const s = room.summary();
  const e = {
    id: room.id,
    state: roomState(room),
    seats: room.capacity,
    players: s.players,
    bots: room.fakeSlots().length,
    terrain: terrainName(s.map.terrain),
    name: s.map.name,
  };
  e.row = rowText(e);
  return e;
}

/** The 0x51 ROOMS payload for a room pool. */
export function roomsPayload(rooms) {
  return build.rooms(rooms.map(roomEntry));
}

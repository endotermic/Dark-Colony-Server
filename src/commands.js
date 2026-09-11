// Message catalogue of dc16.exe (protocol doc §4): sizes, parsers and builders.
// All integers are little-endian; strings are NUL-terminated without a length prefix.

import { ProtocolError } from './frame.js';

export const T = Object.freeze({
  // in-game commands (handler table 0x48949C)
  TICK: 0x01,
  UNTIL: 0x02,
  CREATE: 0x03,
  CHEAT: 0x04,
  ORDER: 0x05,
  TARGET_POS: 0x06,
  WAYPOINTS_OBJ: 0x07,
  SYNC: 0x08,
  RESEARCH: 0x09,
  BUILD: 0x0a,
  TARGET_OBJ: 0x0b,
  SETTING: 0x0c,
  DIPLOMACY: 0x0d,
  CHAT: 0x0e,
  BONUS: 0x0f,
  DISCONNECT: 0x10,
  TICK_SPEED: 0x11,
  TICK_MAXSPEED: 0x12,
  TICK_DESSPEED: 0x13,
  SELECT: 0x14,
  DESELECT: 0x15,
  ORDER_SEL: 0x16,
  TARGET_POS_SEL: 0x17,
  TARGET_OBJ_SEL: 0x18,
  WAYPOINTS_SEL: 0x19,
  ORDER0D_SEL: 0x1a,
  MOVETO_SEL: 0x1b,
  // lobby / meta messages (letters)
  VERSION: 0x64, // 'd'
  LOBBY_CHAT: 0x65, // 'e'
  RACE: 0x66, // 'f'
  NAME: 0x67, // 'g'
  READY: 0x68, // 'h'
  SCENARIO: 0x69, // 'i'
  TYPE: 0x6a, // 'j'
  COLOUR_CYCLE: 0x6b, // 'k'
  COLOUR_SET: 0x6c, // 'l'
  TEAM_CYCLE: 0x6d, // 'm'
  TEAM_SET: 0x6e, // 'n'
  VAR: 0x6f, // 'o'
  NUKE: 0x70, // 'p'
  KEEPALIVE: 0x71, // 'q'
  META_VERSION: 0x72, // 'r'
  INTRO: 0x73, // 's'
  OUTRO: 0x74, // 't'
  GROUP: 0x75, // 'u'
  MREADY: 0x76, // 'v'
  GVERSION: 0x77, // 'w'
  INIT_ME: 0x79, // 'y'
});

export const TYPE_NAME = Object.freeze(Object.fromEntries(Object.entries(T).map(([k, v]) => [v, k])));

export const MAX_NAME = 16;
export const MAX_TEXT = 200;
const MAX_WAYPOINTS = 8;
const MAX_OBJECTS = 800;

/** In-game commands the relay forwards (inside sync frames). Everything else is consumed or dropped. */
export const RELAY_IN_GAME = new Set([
  T.ORDER, T.TARGET_POS, T.WAYPOINTS_OBJ, T.RESEARCH, T.BUILD, T.TARGET_OBJ, T.SETTING, T.DIPLOMACY,
  T.SELECT, T.DESELECT, T.ORDER_SEL, T.TARGET_POS_SEL, T.TARGET_OBJ_SEL, T.WAYPOINTS_SEL, T.ORDER0D_SEL,
  T.MOVETO_SEL,
]);

// payload size after the type byte for fixed-size messages
const FIXED = new Map([
  [T.TICK, 1], [T.UNTIL, 8], [T.CREATE, 13], [T.CHEAT, 4], [T.ORDER, 3], [T.TARGET_POS, 6], [T.SYNC, 6],
  [T.RESEARCH, 3], [T.BUILD, 3], [T.TARGET_OBJ, 4], [T.SETTING, 4], [T.DIPLOMACY, 4], [T.BONUS, 1],
  [T.DISCONNECT, 1], [T.TICK_SPEED, 4], [T.TICK_MAXSPEED, 5], [T.TICK_DESSPEED, 4], [T.DESELECT, 1],
  [T.ORDER_SEL, 2], [T.TARGET_POS_SEL, 5], [T.TARGET_OBJ_SEL, 3], [T.ORDER0D_SEL, 1], [T.MOVETO_SEL, 5],
  [T.VERSION, 4], [T.META_VERSION, 4], [T.MREADY, 3], [T.GROUP, 4], [T.GVERSION, 4], [T.VAR, 4],
  [T.INTRO, 2], [T.OUTRO, 2], [T.READY, 2], [T.RACE, 2], [T.TYPE, 2], [T.COLOUR_CYCLE, 2],
  [T.COLOUR_SET, 2], [T.TEAM_CYCLE, 2], [T.TEAM_SET, 2], [T.INIT_ME, 1], [T.NUKE, 1], [T.KEEPALIVE, 0],
]);

export function typeName(type) {
  return TYPE_NAME[type] ?? `0x${type.toString(16).padStart(2, '0')}`;
}

function need(ok, what) {
  if (!ok) throw new ProtocolError(`truncated ${what}`);
}

function cstrEnd(buf, from, what) {
  const end = buf.indexOf(0, from);
  if (end < 0) throw new ProtocolError(`unterminated string in ${what}`);
  return end;
}

/** Total length (type byte included) of the command starting at `off`, or a ProtocolError. */
export function commandLength(buf, off) {
  const type = buf[off];
  const rest = buf.length - off - 1;
  const name = typeName(type);
  const fixed = FIXED.get(type);
  if (fixed !== undefined) {
    need(rest >= fixed, name);
    return 1 + fixed;
  }
  switch (type) {
    case T.WAYPOINTS_OBJ: {
      need(rest >= 3, name);
      const n = buf[off + 1];
      if (n > MAX_WAYPOINTS) throw new ProtocolError(`${name}: ${n} waypoints`);
      const count = buf.readInt16LE(off + 2);
      if (count < 0 || count > MAX_OBJECTS) throw new ProtocolError(`${name}: ${count} objects`);
      const len = 3 + 4 * n + 2 * count;
      need(rest >= len, name);
      return 1 + len;
    }
    case T.WAYPOINTS_SEL: {
      need(rest >= 2, name);
      const n = buf[off + 1];
      if (n > MAX_WAYPOINTS) throw new ProtocolError(`${name}: ${n} waypoints`);
      const len = 2 + 4 * n;
      need(rest >= len, name);
      return 1 + len;
    }
    case T.SELECT: {
      need(rest >= 3, name);
      let p = off + 2;
      for (;;) {
        need(p + 2 <= buf.length, name);
        const v = buf.readUInt16LE(p);
        p += 2;
        if (v === 0xffff) break;
      }
      return p - off;
    }
    case T.CHAT:
      need(rest >= 3, name);
      return cstrEnd(buf, off + 3, name) - off + 1;
    case T.NAME:
      need(rest >= 3, name);
      return cstrEnd(buf, off + 3, name) - off + 1;
    case T.LOBBY_CHAT:
      need(rest >= 1, name);
      return cstrEnd(buf, off + 1, name) - off + 1;
    case T.SCENARIO: {
      need(rest >= 2, name);
      const e1 = cstrEnd(buf, off + 1, name);
      const e2 = cstrEnd(buf, e1 + 1, name);
      return e2 - off + 1;
    }
    default:
      throw new ProtocolError(`unknown command type ${name}`);
  }
}

/** Split a frame payload into commands `{ type, raw }` (raw includes the type byte). */
export function splitCommands(payload) {
  const cmds = [];
  let off = 0;
  while (off < payload.length) {
    const len = commandLength(payload, off);
    cmds.push({ type: payload[off], raw: payload.subarray(off, off + len) });
    off += len;
  }
  return cmds;
}

function readCstr(buf, from) {
  const end = buf.indexOf(0, from);
  return buf.toString('latin1', from, end < 0 ? buf.length : end);
}

/** Decode the fields of the messages the relay needs to understand. */
export function decode(cmd) {
  const b = cmd.raw;
  switch (cmd.type) {
    case T.TICK:
      return { n: b[1] };
    case T.UNTIL:
      return { a: b.readInt32LE(1), until: b.readInt32LE(5) };
    case T.CHEAT:
      return { a: b.readInt16LE(1), b: b.readInt16LE(3) };
    case T.SYNC:
      return { checksum: b.readInt16LE(1), time: b.readInt32LE(3) };
    case T.CHAT:
      return { from: b[1], mask: b[2], text: readCstr(b, 3) };
    case T.BONUS:
    case T.DISCONNECT:
    case T.INIT_ME:
    case T.NUKE:
      return { player: b[1] };
    case T.TICK_SPEED:
    case T.TICK_DESSPEED:
      return { ms: b.readInt32LE(1) };
    case T.TICK_MAXSPEED:
      return { player: b[1], ms: b.readInt32LE(2) };
    case T.VERSION:
    case T.META_VERSION:
      return { version: b.readInt16LE(1), id: b.readInt16LE(3) };
    case T.MREADY:
      return { player: b.readInt16LE(1), state: b[3] };
    case T.GROUP:
    case T.GVERSION:
      return { id: b.readInt16LE(1), value: b.readInt16LE(3) };
    case T.VAR:
      return { index: b.readInt16LE(1), value: b.readInt16LE(3) };
    case T.INTRO:
    case T.OUTRO:
      return { id: b.readInt16LE(1) };
    case T.NAME:
      return { player: b.readInt16LE(1), name: readCstr(b, 3) };
    case T.READY:
      return { status: b[1], player: b[2] };
    case T.RACE:
    case T.TYPE:
    case T.COLOUR_CYCLE:
    case T.COLOUR_SET:
    case T.TEAM_CYCLE:
    case T.TEAM_SET:
      return { value: b[1], player: b[2] };
    case T.LOBBY_CHAT:
      return { text: readCstr(b, 1) };
    case T.SCENARIO: {
      const e1 = b.indexOf(0, 1);
      return { file: b.toString('latin1', 1, e1), title: readCstr(b, e1 + 1) };
    }
    default:
      return {};
  }
}

// ---- builders -------------------------------------------------------------------------------

const u8 = (...v) => Buffer.from(v.map((x) => x & 0xff));
const i16 = (v) => {
  const b = Buffer.alloc(2);
  b.writeInt16LE(((v & 0xffff) << 16) >> 16); // wraps: the 0x08 checksum is an unsigned 16-bit sum
  return b;
};
const i32 = (v) => {
  const b = Buffer.alloc(4);
  b.writeInt32LE(v);
  return b;
};
const cstr = (s, max) => Buffer.concat([Buffer.from(String(s).slice(0, max), 'latin1'), Buffer.from([0])]);

/** Keep printable Latin-1 only, cut to `max` characters. */
export function sanitizeText(s, max = MAX_TEXT) {
  return String(s ?? '').replace(/[^\x20-\x7e\xa0-\xff]/g, '').slice(0, max);
}

export function sanitizeName(s, fallback = 'Player') {
  const t = sanitizeText(s, 1000).trim().slice(0, MAX_NAME);
  return t || fallback;
}

export const build = Object.freeze({
  // lobby
  version: (ver, slot) => Buffer.concat([u8(T.VERSION), i16(ver), i16(slot)]),
  scenario: (file, title) => Buffer.concat([u8(T.SCENARIO), cstr(file, 64), cstr(title, MAX_TEXT)]),
  colourSet: (colour, slot) => u8(T.COLOUR_SET, colour, slot),
  colourCycle: (delta, slot) => u8(T.COLOUR_CYCLE, delta, slot),
  teamSet: (team, slot) => u8(T.TEAM_SET, team, slot),
  teamCycle: (delta, slot) => u8(T.TEAM_CYCLE, delta, slot),
  name: (slot, name) => Buffer.concat([u8(T.NAME), i16(slot), cstr(name, MAX_NAME)]),
  race: (race, slot) => u8(T.RACE, race, slot),
  type: (type, slot) => u8(T.TYPE, type, slot),
  ready: (status, slot) => u8(T.READY, status, slot),
  variable: (index, value) => Buffer.concat([u8(T.VAR), i16(index), i16(value)]),
  lobbyChat: (text) => Buffer.concat([u8(T.LOBBY_CHAT), cstr(text, MAX_TEXT)]),
  keepalive: () => u8(T.KEEPALIVE),
  mready: (player, state) => Buffer.concat([u8(T.MREADY), i16(player), u8(state)]),
  initMe: (player) => u8(T.INIT_ME, player),
  nuke: (player) => u8(T.NUKE, player),
  // in-game
  tick: (n) => u8(T.TICK, n),
  until: (a, until) => Buffer.concat([u8(T.UNTIL), i32(a), i32(until)]),
  cheat: (a, b) => Buffer.concat([u8(T.CHEAT), i16(a), i16(b)]),
  chat: (from, mask, text) => Buffer.concat([u8(T.CHAT), u8(from, mask), cstr(text, MAX_TEXT)]),
  bonus: (player) => u8(T.BONUS, player),
  disconnect: (slot) => u8(T.DISCONNECT, slot),
  tickSpeed: (ms) => Buffer.concat([u8(T.TICK_SPEED), i32(ms)]),
  tickMaxSpeed: (player, ms) => Buffer.concat([u8(T.TICK_MAXSPEED), u8(player), i32(ms)]),
  tickDesSpeed: (ms) => Buffer.concat([u8(T.TICK_DESSPEED), i32(ms)]),
  sync: (checksum, time) => Buffer.concat([u8(T.SYNC), i16(checksum), i32(time)]),
  orderSelected: (player, order) => u8(T.ORDER_SEL, player, order),
  deselect: (player) => u8(T.DESELECT, player),
  moveToSelected: (player, x, z) => Buffer.concat([u8(T.MOVETO_SEL, player), i16(x), i16(z)]),
  waypointsSelected: (player, points) =>
    Buffer.concat([u8(T.WAYPOINTS_SEL, points.length, player), ...points.flatMap(([x, z]) => [i16(x), i16(z)])]),
  select: (player, objs) => Buffer.concat([u8(T.SELECT, player), ...objs.map((o) => i16(o)), Buffer.from([0xff, 0xff])]),
});

// The room-selection lobby ("hall", plan §17). A fresh connection lands here: it sees the normal
// lobby screen used as a room browser. The player rows other than the client's own show the rooms
// (up to seven), numbered 1..7 in place: the number and a space stay fixed, the map name, the
// player count and the availability scroll through the remaining 14 characters of the name field.
// The map line repeats the selected room in full. The client's own row keeps showing the player's
// name: the client never repaints its own name field from an incoming 'g' (F33), so the server
// cannot use that row. Chat commands select a room; the READY button joins it. The name may be
// typed here and follows the player into the room; race, colour and team cannot be changed here.
// The view is private to each client.
//
// Every update goes out as one frame: the client's lobby loop handles one frame per iteration and
// its dispatcher runs all commands of a frame (F34), so one frame per step keeps it in step.

import { randomInt } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { T, build, decode, typeName, sanitizeText, sanitizeName, MAX_NAME } from './commands.js';
import { STATE, SLOT_TYPE, SLOTS, VAR_DEFAULTS } from './constants.js';
import { Client, readCommands, packPayloads } from './client.js';
import { formatScenarioTitle } from './config.js';
import { VERSION_SHORT } from './version.js';

export const HALL_TITLE_PREFIX = '>'; // the map line shows the selected room; a room's own title never starts with it
export const HALL_FILE = 'D8PLAY01.SCN'; // never loaded: no game starts from the hall
const MAX_TITLE_NAME = 42; // F15: the name part of the title
const SEPARATOR = '   ';
const LABEL_WIDTH = 2; // "<n> " stays in place at the start of a room row
const SCROLL_WIDTH = MAX_NAME - LABEL_WIDTH; // the 14 characters that scroll

/** The `width` characters of `text` visible at scroll position `offset`; short texts do not scroll. */
export function marquee(text, width, offset) {
  if (text.length <= width) return text;
  const s = text + SEPARATOR;
  const i = offset % s.length;
  return (s + s).slice(i, i + width);
}

export class Hall {
  constructor(pool, config, log, now = () => performance.now(), random = (n) => randomInt(n)) {
    this.pool = pool;
    this.config = config;
    this.log = log;
    this.now = now;
    this.random = random;
    this.clients = new Set();
    this.offset = 0;
    this.lastShift = 0;
  }

  get cfg() {
    return this.config;
  }

  players() {
    const out = [];
    for (const c of this.clients) if (!c.gone) out.push(c);
    return out;
  }

  pack(payloads) {
    return this.cfg.PACK_LOBBY_FRAMES ? packPayloads(payloads) : payloads;
  }

  // ---- connections --------------------------------------------------------------------------

  accept(socket) {
    const now = this.now();
    const client = new Client(socket, undefined, now);
    client.owner = this;
    client.wire();
    if (this.clients.size === 0) this.lastShift = now; // the marquee clock starts with the first visitor
    const slot = this.pickSlot();
    client.slot = slot;
    client.name = `Player${slot}`;
    client.selected = this.defaultRoom(slot);
    this.clients.add(client);
    const rows = this.rowsFor(client);
    // the chat window: the whole greeting is a static header (§17.8), messages go below it
    client.chat.setHeader(this.headerFor(client));
    client.sendBatch([build.version(this.cfg.PROTOCOL_VERSION, slot), ...this.pack([...this.dumpPayloads(client, rows), ...client.chat.payloads()])]);
    this.remember(client, rows);
    this.log.info('hall joined', { id: client.id, slot, address: client.address, waiting: this.clients.size, selected: client.selected + 1 });
  }

  /**
   * The six static lines at the top of a hall client's chat (maintainer, 7 Sep 2026: none of them
   * may scroll away). No player name: "PlayerN" is generated and means nothing. The last line names
   * the selected room and is rewritten in place on every selection.
   */
  headerFor(client) {
    const n = this.pool.rooms.length;
    const sel = this.pool.rooms[client.selected];
    return [
      `Welcome to Dark Colony server ${VERSION_SHORT}.`,
      `Type /1../${n} + ENTER to select a room,`,
      'then press READY to join it.',
      'The map line shows the selected room.',
      'You may type your name in your row.',
      `Room ${sel.id} (${sel.map.name}) is selected.`,
    ];
  }

  /**
   * The slot number a newcomer keeps for the whole connection (F30). Prefer a slot that is free in
   * as many rooms as possible and that no other waiting client holds; random among equals.
   */
  pickSlot() {
    const waiting = this.players();
    let best = [];
    let bestScore = -Infinity;
    for (let s = 1; s < SLOTS; s++) {
      const open = this.pool.rooms.filter((r) => r.canJoin(s)).length;
      const held = waiting.filter((c) => c.slot === s).length;
      const score = open * SLOTS - held;
      if (score > bestScore) {
        bestScore = score;
        best = [s];
      } else if (score === bestScore) {
        best.push(s);
      }
    }
    return best[this.random(best.length)];
  }

  /** Index of the first room the client could join right now, else room 1. */
  defaultRoom(slot) {
    const i = this.pool.rooms.findIndex((r) => r.canJoin(slot));
    return i < 0 ? 0 : i;
  }

  // ---- the private lobby view ---------------------------------------------------------------

  /** Availability of `room` for a client in `slot`: open, full, in battle, slot taken. */
  stateOf(room, slot) {
    const s = room.summary();
    if (s.state !== STATE.LOBBY) return 'in battle';
    if (s.players >= s.seats) return 'full';
    if (!room.canJoin(slot)) return 'slot taken';
    return 'open';
  }

  /** "<map> <terrain> (<players>/<slots>) <state>" without a ':' (the chat prefix is split at the first colon). */
  detail(room, slot) {
    const s = room.summary();
    return `${s.map.name} ${s.map.terrain} (${s.players}/${s.slots}) ${this.stateOf(room, slot)}`.replace(/:/g, ' ');
  }

  /** "<n> <map> (<players>/<slots>) <state>": the full description, as in the map line and /rooms. */
  describe(room, slot) {
    return `${room.id} ${this.detail(room, slot)}`;
  }

  /** Row that shows room `index` (0-based) for this client: rooms fill the rows in order, skipping the own row. */
  rowOf(client, index) {
    return index < client.slot ? index : index + 1;
  }

  /** Room shown in row `q` for this client, or null (the own row, or no room for that row). */
  roomAt(client, q) {
    if (q === client.slot) return null;
    return this.pool.rooms[q < client.slot ? q : q - 1] ?? null;
  }

  /** The map line: the selected room in full, in the game's title format with the digit 8 (F22). */
  titleFor(client) {
    const room = this.pool.rooms[client.selected];
    const text = `${HALL_TITLE_PREFIX}${this.describe(room, client.slot)}`.slice(0, MAX_TITLE_NAME);
    return formatScenarioTitle(text, SLOTS, room.map.terrain);
  }

  /** One entry per row: what to show in the name field, the CD icon ("joinable"), type and status. */
  rowsFor(client) {
    // "<n> " stays in place; the rest scrolls. The details are padded to a common length so that all
    // rows scroll with the same period and wrap around together (maintainer, 7 Sep 2026)
    const details = new Array(SLOTS).fill(null);
    let width = 0;
    for (let q = 0; q < SLOTS; q++) {
      const room = this.roomAt(client, q);
      if (!room) continue;
      details[q] = this.detail(room, client.slot);
      width = Math.max(width, details[q].length);
    }
    const rows = [];
    for (let q = 0; q < SLOTS; q++) {
      if (q === client.slot) {
        rows.push({ text: client.name, flag: 1, type: SLOT_TYPE.HUMAN, status: 1 });
        continue;
      }
      if (details[q] === null) {
        rows.push({ text: '', flag: 0, type: SLOT_TYPE.EMPTY, status: 0 });
        continue;
      }
      const room = this.roomAt(client, q);
      // every room row is a present-not-ready human: the client stays in the lobby (F3), no colour locks (F20)
      rows.push({
        text: `${room.id} `.padEnd(LABEL_WIDTH) + marquee(details[q].padEnd(width), SCROLL_WIDTH, this.offset),
        flag: room.canJoin(client.slot) ? 1 : 0,
        type: SLOT_TYPE.HUMAN,
        status: 1,
      });
    }
    return rows;
  }

  /** The hall's lobby dump for `client` (like the room dump, §6.1, without the 'd'). */
  dumpPayloads(client, rows) {
    const s = client.slot;
    const out = [build.scenario(HALL_FILE, this.titleFor(client))];
    for (let q = 0; q < SLOTS; q++) if (q !== s) out.push(build.colourSet(q, q));
    for (let q = 0; q < SLOTS; q++) {
      if (q === s) continue;
      out.push(build.name(q, rows[q].text), build.race(0, q), build.type(rows[q].type, q), build.teamSet(q, q), build.ready(rows[q].status, q));
    }
    out.push(build.name(s, rows[s].text), build.race(0, s), build.type(SLOT_TYPE.HUMAN, s), build.colourSet(s, s), build.teamSet(s, s), build.ready(1, s));
    for (let v = 0; v < 8; v++) out.push(build.variable(v, VAR_DEFAULTS[v]));
    for (let q = 0; q < SLOTS; q++) out.push(build.variable(8 + q, rows[q].flag)); // CD icon = joinable (F5)
    return out;
  }

  remember(client, rows) {
    client.rows = rows.map((r) => r.text);
    client.flags = rows.map((r) => r.flag);
    client.title = this.titleFor(client);
  }

  /** Send the map line, rows and icons that changed, all in one frame, one write per client. */
  refresh(only = null) {
    for (const client of this.players()) {
      if (only && client !== only) continue;
      const rows = this.rowsFor(client);
      const title = this.titleFor(client);
      const payloads = [];
      if (title !== client.title) payloads.push(build.scenario(HALL_FILE, title));
      for (let q = 0; q < SLOTS; q++) {
        if (rows[q].text !== client.rows[q]) payloads.push(build.name(q, rows[q].text));
        if (rows[q].flag !== client.flags[q]) payloads.push(build.variable(8 + q, rows[q].flag));
      }
      if (payloads.length) client.sendBatch(this.pack(payloads));
      this.remember(client, rows);
    }
  }

  /** Driven by the step timer: scroll the rows every MARQUEE_MS (this also picks up room changes). */
  step(now) {
    if (this.clients.size === 0) {
      this.lastShift = now;
      return;
    }
    if (now - this.lastShift < this.cfg.MARQUEE_MS) return;
    this.lastShift = now;
    this.offset++;
    this.refresh();
  }

  // ---- input --------------------------------------------------------------------------------

  onSocketClose(client) {
    this.evict(client, 'connection closed');
  }

  onSocketError(client, err) {
    this.evict(client, `socket error: ${err.code || err.message}`);
  }

  onData(client, chunk) {
    if (client.gone) return;
    const now = this.now();
    client.lastSeen = now;
    const { batches, resyncs, error } = readCommands(client, chunk, this.cfg.STRICT_SEQ);
    for (const r of resyncs) this.log.warn('sequence resync', { id: client.id, ...r });
    for (const b of batches) {
      if (client.gone) return;
      if (!client.firstMessageAt) client.firstMessageAt = now;
      if (client.owner !== this) {
        // moved into a room by an earlier frame of the same chunk
        client.owner.dispatch(client, b.cmds, now);
        continue;
      }
      if (this.log.level === 'debug') this.trace(client, b.seq, b.cmds);
      this.handle(client, b.cmds, now);
    }
    if (error) (client.owner ?? this).evict(client, error);
  }

  trace(client, seq, cmds) {
    const shown = cmds.filter((c) => c.type !== T.KEEPALIVE);
    if (shown.length === 0) return;
    this.log.debug('rx', { id: client.id, slot: client.slot, seq, state: 'HALL', cmds: shown.map((c) => ({ t: typeName(c.type), ...decode(c) })) });
  }

  handle(client, cmds, now) {
    for (let i = 0; i < cmds.length; i++) {
      const cmd = cmds[i];
      if (client.gone) return;
      if (client.owner !== this) return client.owner.dispatch(client, cmds.slice(i), now); // joined a room
      switch (cmd.type) {
        case T.KEEPALIVE:
          break;

        case T.NAME: {
          // the own name field is the client's (F33): keep what it types, it follows the player into the room
          const d = decode(cmd);
          if (d.player !== client.slot) {
            this.drop(client, cmd, 'foreign slot');
            break;
          }
          client.name = sanitizeName(d.name, `Player${client.slot}`);
          client.send(build.name(client.slot, client.name)); // as a room would: the client stores the echoed name
          if (client.rows) client.rows[client.slot] = client.name;
          break;
        }

        case T.VAR: // the client's CD report: the icons here mean "joinable", never relay it
        case T.RACE: // race, colour and team cannot be changed in the hall (R13)
        case T.COLOUR_CYCLE:
        case T.TEAM_CYCLE:
        case T.TYPE:
        case T.COLOUR_SET:
        case T.TEAM_SET:
        case T.SCENARIO:
        case T.NUKE:
          this.drop(client, cmd, 'not allowed in the hall');
          break;

        case T.READY: {
          const d = decode(cmd);
          if (d.player === client.slot && d.status === 2) this.join(client);
          break;
        }

        case T.LOBBY_CHAT: {
          const text = decode(cmd).text;
          const colon = text.indexOf(':');
          this.command(client, sanitizeText(colon < 0 ? text : text.slice(colon + 1)).trim());
          break;
        }

        case T.INIT_ME: {
          client.initMeCount++;
          if (client.initMeCount > 1) {
            this.strike(client, 'repeated INIT_ME');
            break;
          }
          const rows = this.rowsFor(client);
          client.sendBatch(this.pack(this.dumpPayloads(client, rows)));
          this.remember(client, rows);
          break;
        }

        default:
          if (cmd.type < 0x64) this.strike(client, `in-game command ${typeName(cmd.type)} in the hall`);
          else this.strike(client, `unexpected lobby message ${typeName(cmd.type)}`);
      }
    }
    return undefined;
  }

  drop(client, cmd, why) {
    this.log.debug('dropped', { id: client.id, slot: client.slot, type: typeName(cmd.type), why });
  }

  // ---- chat commands ------------------------------------------------------------------------

  command(client, body) {
    const n = this.pool.rooms.length;
    const pick = /^\/?\s*(?:join\s+)?([0-9])$/i.exec(body);
    if (pick) return this.select(client, Number(pick[1]) - 1);
    if (/^\/(rooms|list)$/i.test(body)) return this.listRooms(client);
    if (/^\/help$/i.test(body)) {
      // three lines of at most 40 characters: the hall leaves four rows for messages (§17.8)
      return this.sayLines(client, [`/1../${n} + ENTER selects a room.`, '/rooms lists the rooms.', 'READY joins the selected room.']);
    }
    if (body.startsWith('/')) return this.say(client, `Unknown command ${body.split(/\s+/)[0]}, try /help.`);
    if (!body) return undefined;
    // plain chat between the people waiting in the hall, under the sender's real name
    for (const c of this.players()) this.say(c, `${client.name}: ${body}`);
    return undefined;
  }

  /** Why `client` cannot join `room` right now, or null if it can. */
  blocker(client, room) {
    if (room.state !== STATE.LOBBY) return 'a battle is in progress there, wait for it to end';
    if (room.isFull()) return 'it is full';
    if (!room.canJoin(client.slot)) return `your slot ${client.slot} is taken there; reconnect to get another slot`;
    return null;
  }

  select(client, index) {
    const room = this.pool.rooms[index];
    if (!room) return this.say(client, `There is no room ${index + 1}; rooms are 1..${this.pool.rooms.length}.`);
    client.selected = index;
    client.chat.setHeader(this.headerFor(client)); // "Room N (<map>) is selected." changes in place
    const why = this.blocker(client, room);
    if (why) this.say(client, `Room ${room.id}: ${why}.`);
    else client.sendBatch(this.pack(client.chat.payloads()));
    this.refresh(client);
    return undefined;
  }

  listRooms(client) {
    this.sayLines(client, this.pool.rooms.map((room) => this.describe(room, client.slot)));
  }

  /** The READY button in the hall: move the client into the selected room. */
  join(client) {
    const room = this.pool.rooms[client.selected];
    const why = this.blocker(client, room);
    if (why) return this.say(client, `Cannot join room ${room.id}: ${why}.`);
    this.clients.delete(client);
    this.log.info('hall -> room', { id: client.id, slot: client.slot, name: client.name, room: room.id, waiting: this.clients.size });
    // the room dump replaces the rows with real players; the client is present-not-ready there
    // (its READY button stays pressed, F36, so its first click in the room is a no-op)
    room.adopt(client, client.slot, false);
    return undefined;
  }

  // ---- liveness -----------------------------------------------------------------------------

  /** Driven by the watchdog timer: the lobby deadlines of §9.1 apply here too. */
  tick(now) {
    const cfg = this.cfg;
    for (const c of this.players()) {
      if (!c.firstMessageAt) {
        if (now - c.joinedAt > cfg.JOIN_TIMEOUT_MS) this.evict(c, 'no message after joining');
      } else if (now - c.lastSeen > cfg.KEEPALIVE_TIMEOUT_MS) {
        this.evict(c, 'keep-alive timeout');
      }
    }
  }

  /** A line in the client's chat window, below the static header; no name in front of it (§17.8). */
  say(client, text) {
    this.sayLines(client, [text]);
  }

  sayLines(client, texts) {
    for (const t of texts) client.chat.push(t);
    client.sendBatch(this.pack(client.chat.payloads()));
  }

  strike(client, reason) {
    if (client.gone) return;
    client.strikes++;
    this.log.debug('strike', { id: client.id, slot: client.slot, strikes: client.strikes, reason });
    if (client.strikes >= this.cfg.STRIKE_LIMIT) this.evict(client, `too many violations (${reason})`);
  }

  evict(client, reason) {
    if (client.gone) return;
    client.gone = true;
    this.clients.delete(client);
    this.log.info('hall left', { id: client.id, slot: client.slot, reason, waiting: this.clients.size });
    client.destroy();
  }
}

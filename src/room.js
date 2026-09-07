// The single game room: slots, clients, state machine LOBBY -> STARTING -> RUNNING -> reset.
// Routes frames to Lobby/Game, owns broadcasting and eviction (plan §5, §9.4).

import { randomInt } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { checkSeq } from './frame.js';
import { splitCommands, build, decode, typeName, T } from './commands.js';
import { STATE, SLOT_TYPE, SLOTS } from './constants.js';
import { Client } from './client.js';
import { Lobby } from './lobby.js';
import { Game } from './game.js';
import { Watchdog } from './watchdog.js';

export { STATE, SLOT_TYPE, SLOTS };

// every healthy client sends these all the time; they only clutter a debug trace
const TRACE_NOISE = new Set([T.KEEPALIVE, T.TICK_SPEED, T.TICK_MAXSPEED, T.TICK_DESSPEED]);

export class Room {
  /**
   * @param config   see config.js
   * @param log      logger (log.js)
   * @param now      monotonic clock in ms (injectable for tests)
   * @param random   random integer in [0, n) (injectable for tests)
   */
  constructor(config, log, now = () => performance.now(), random = (n) => randomInt(n)) {
    this.config = config;
    this.log = log;
    this.now = now;
    this.random = random;
    this.state = STATE.LOBBY;
    this.clients = new Set();
    this.nextClientId = 1;
    this.startingAt = 0;
    this.gamesPlayed = 0;
    this.slots = new Array(SLOTS);
    this.resetSlots();
    this.lobby = new Lobby(this);
    this.game = new Game(this);
    this.watchdog = new Watchdog(this);
  }

  // ---- slots --------------------------------------------------------------------------------

  emptySlot(s) {
    const cfg = this.config;
    return {
      slot: s,
      name: '',
      race: 0,
      colour: s,
      team: s,
      type: cfg.FILL_EMPTY_WITH_AI ? cfg.FILL_AI_TYPE : SLOT_TYPE.EMPTY,
      status: 0,
      client: null,
    };
  }

  fakeSlot(s, name) {
    return {
      slot: s,
      name,
      race: this.config.MERCENARY_RACE,
      colour: s,
      team: s,
      type: SLOT_TYPE.HUMAN,
      status: 1, // present but not ready: holds the lobby until the start signal (F3)
      client: null,
      fake: true,
    };
  }

  resetSlots() {
    const cfg = this.config;
    for (let s = 0; s < SLOTS; s++) this.slots[s] = this.emptySlot(s);
    // slot 0 is always the fake host "Mercenary"
    this.slots[0] = this.fakeSlot(0, cfg.FAKE_NAME_LIST[0]);
    // further fake humans take random slots, so that the real players' slots (and with them
    // their start positions, F12) differ from game to game
    if (cfg.FAKE_PLAYERS > 1) {
      const pool = [1, 2, 3, 4, 5, 6, 7];
      for (let i = pool.length - 1; i > 0; i--) {
        const j = this.random(i + 1);
        [pool[i], pool[j]] = [pool[j], pool[i]];
      }
      for (let i = 1; i < cfg.FAKE_PLAYERS; i++) this.slots[pool[i - 1]] = this.fakeSlot(pool[i - 1], cfg.FAKE_NAME_LIST[i]);
    }
  }

  fakeSlots() {
    return this.slots.filter((sl) => sl.fake);
  }

  freeSlots() {
    return this.slots.filter((sl) => !sl.fake && !sl.client);
  }

  /** Connected, not evicted clients. */
  players() {
    const out = [];
    for (const c of this.clients) if (!c.gone) out.push(c);
    return out;
  }

  // ---- connections --------------------------------------------------------------------------

  accept(socket) {
    const now = this.now();
    const client = new Client(socket, this.nextClientId++, now);
    if (this.log.level === 'debug') client.onSend = (c, payloads) => this.traceTx(c, payloads);
    socket.on('error', (err) => this.evict(client, `socket error: ${err.code || err.message}`));
    try {
      socket.setNoDelay(true);
      socket.setKeepAlive(true, 15000);
    } catch {
      // fake sockets in tests
    }
    if (this.state !== STATE.LOBBY) return this.reject(client, 'a battle is in progress, try again later');
    const free = this.freeSlots();
    if (free.length === 0) return this.reject(client, 'the lobby is full');

    // random free slot in 1..7: this is what randomises the start positions (plan §7)
    const slot = free[this.random(free.length)];
    slot.client = client;
    slot.type = SLOT_TYPE.HUMAN;
    slot.status = 1;
    slot.name = `Player${slot.slot}`;
    slot.race = 0;
    slot.colour = slot.slot;
    slot.team = slot.slot;
    client.slot = slot.slot;
    this.clients.add(client);
    socket.on('data', (chunk) => this.onData(client, chunk));
    socket.on('close', () => this.evict(client, 'connection closed'));
    this.log.info('client joined', {
      id: client.id,
      slot: slot.slot,
      address: client.address,
      players: this.clients.size,
      fakeSlots: this.fakeSlots().map((f) => f.slot),
    });
    this.lobby.onJoin(client);
  }

  reject(client, text) {
    this.log.info('connection rejected', { address: client.address, reason: text, state: this.state });
    client.sendBatch([
      build.version(this.config.PROTOCOL_VERSION, SLOTS - 1),
      build.lobbyChat(`${this.config.MERCENARY_NAME}: ${text}`),
    ]);
    client.gone = true;
    try {
      client.socket.end();
    } catch {
      // ignore
    }
  }

  onData(client, chunk) {
    if (client.gone) return;
    const now = this.now();
    client.lastSeen = now;
    let frames;
    try {
      frames = client.decoder.feed(chunk);
    } catch (err) {
      return this.evict(client, `bad frame: ${err.message}`);
    }
    for (const frame of frames) {
      if (client.gone) return;
      const verdict = checkSeq(client.seqIn, frame.seq);
      if (verdict === 'duplicate') continue;
      if (verdict === 'mismatch') {
        if (this.config.STRICT_SEQ) return this.evict(client, `sequence ${frame.seq}, expected ${client.seqIn}`);
        this.log.warn('sequence resync', { id: client.id, got: frame.seq, expected: client.seqIn });
      }
      client.seqIn = (frame.seq + 1) & 15;
      let cmds;
      try {
        cmds = splitCommands(frame.payload);
      } catch (err) {
        return this.evict(client, `bad command: ${err.message}`);
      }
      if (!client.firstMessageAt) client.firstMessageAt = now;
      if (this.log.level === 'debug') this.trace(client, frame.seq, cmds);
      this.dispatch(client, cmds, now);
    }
  }

  /** Debug trace of frames sent to a client outside the battle (the battle stream is uniform anyway). */
  traceTx(client, payloads) {
    if (this.state === STATE.RUNNING) return;
    const cmds = [];
    for (const p of payloads) {
      for (const c of splitCommands(p)) {
        const d = decode(c);
        const short = { t: typeName(c.type) };
        for (const k of ['player', 'status', 'value', 'index', 'id', 'version']) if (k in d) short[k] = d[k];
        if ('name' in d) short.name = d.name;
        if ('text' in d) short.text = d.text.slice(0, 40);
        if ('file' in d) short.file = d.file;
        cmds.push(short);
      }
    }
    this.log.debug('tx', { id: client.id, slot: client.slot, seqFrom: client.seqOut, frames: payloads.length, cmds });
  }

  /** Debug trace of received commands; keep-alives, speed reports and in-game UNTIL traffic are left out. */
  trace(client, seq, cmds) {
    const shown = cmds.filter((c) => !TRACE_NOISE.has(c.type) && (this.state !== STATE.RUNNING || c.type !== T.UNTIL));
    if (shown.length === 0) return;
    this.log.debug('rx', {
      id: client.id,
      slot: client.slot,
      seq,
      state: this.state,
      cmds: shown.map((c) => ({ t: typeName(c.type), ...decode(c) })),
    });
  }

  dispatch(client, cmds, now) {
    switch (this.state) {
      case STATE.LOBBY:
        return this.lobby.handle(client, cmds, now);
      case STATE.STARTING:
        return this.lobby.handleStarting(client, cmds, now);
      case STATE.RUNNING:
        return this.game.handle(client, cmds, now);
      default:
        return undefined;
    }
  }

  // ---- output -------------------------------------------------------------------------------

  broadcast(payload, except = null) {
    for (const c of this.clients) if (c !== except && !c.gone) c.send(payload);
  }

  /** Lobby chat line from Mercenary. Ignored outside the lobby (the game discards letters). */
  say(text) {
    if (this.state !== STATE.LOBBY) return;
    this.broadcast(build.lobbyChat(`${this.config.MERCENARY_NAME}: ${text}`));
  }

  // ---- violations & eviction ----------------------------------------------------------------

  strike(client, reason) {
    if (client.gone) return;
    client.strikes++;
    this.log.debug('strike', { id: client.id, slot: client.slot, strikes: client.strikes, reason });
    if (client.strikes >= this.config.STRIKE_LIMIT) this.evict(client, `too many violations (${reason})`);
  }

  evict(client, reason) {
    if (client.gone) return;
    client.gone = true;
    this.clients.delete(client);
    const s = client.slot;
    const slot = s >= 0 ? this.slots[s] : null;
    const name = slot?.name || `Player${s}`;
    const wasReady = slot?.status === 2;
    this.log.info('client left', {
      id: client.id,
      slot: s,
      name,
      reason,
      state: this.state,
      players: this.clients.size,
    });
    if (slot && slot.client === client) this.slots[s] = this.emptySlot(s);
    switch (this.state) {
      case STATE.LOBBY:
        if (wasReady) this.broadcast(build.ready(0, s)); // release the colour lock first (F20)
        this.broadcast(build.disconnect(s));
        this.say(`${name} left the lobby (${reason})`);
        break;
      case STATE.STARTING:
      case STATE.RUNNING:
        this.game.onClientLeft(client); // DISCONNECT inside the next sync frame (F19)
        break;
      default:
        break;
    }
    client.destroy();
    if (this.clients.size === 0) {
      this.reset();
      return;
    }
    if (this.state === STATE.LOBBY) this.lobby.onLeave(client);
    else if (this.state === STATE.STARTING) this.lobby.checkAllLoaded(this.now());
  }

  // ---- state transitions --------------------------------------------------------------------

  reset() {
    this.log.info('room reset', { gamesPlayed: this.gamesPlayed });
    this.state = STATE.LOBBY;
    this.startingAt = 0;
    for (const c of this.clients) c.destroy();
    this.clients.clear();
    this.resetSlots();
    this.lobby.reset();
    this.game.reset();
  }

  beginStarting(now) {
    this.state = STATE.STARTING;
    this.startingAt = now;
    for (const c of this.clients) c.mready = false;
    // The fake players were the last status-1 slots; once none is 1 any more every client leaves
    // the lobby (F3). Status 0 is used rather than 2: a status-2 message goes through the client's
    // colour-lock check and was refused on some clients (live test, 6 Sep 2026), status 0 is
    // applied unconditionally. The game start only looks at slot types, not statuses.
    for (const f of this.fakeSlots()) {
      f.status = 0;
      this.broadcast(build.ready(0, f.slot));
    }
    this.log.info('starting', {
      players: this.players().map((c) => ({ slot: c.slot, name: this.slots[c.slot].name, colour: this.slots[c.slot].colour })),
    });
  }

  beginRunning(now) {
    this.state = STATE.RUNNING;
    this.gamesPlayed++;
    this.game.start(now);
    this.log.info('running', { game: this.gamesPlayed, players: this.players().length, tickMs: this.config.TICK_MS });
  }

  // ---- timers (driven by index.js or by tests) ----------------------------------------------

  step(now) {
    if (this.state === STATE.RUNNING) this.game.step(now);
  }

  watchdogTick(now) {
    this.watchdog.tick(now);
  }
}

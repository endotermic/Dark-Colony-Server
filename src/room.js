// One game room with its own map: slots, clients, state machine LOBBY -> STARTING -> RUNNING -> reset.
// Routes frames to Lobby/Game, owns broadcasting and eviction (plan §5, §9.4). Several rooms live
// in a RoomPool (rooms.js); connections arrive through the Hall (hall.js) or, with HALL=false,
// directly through accept().

import { randomInt } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { splitCommands, build, decode, typeName, T } from './commands.js';
import { STATE, SLOT_TYPE, SLOTS } from './constants.js';
import { Client, readCommands } from './client.js';
import { Lobby } from './lobby.js';
import { Game } from './game.js';
import { Watchdog } from './watchdog.js';
import { SyncCheck } from './synccheck.js';
import { Recorder } from './recorder.js';
import { LogRecorder, teeRecorders } from './logrecorder.js';
import { Bots } from './mercenary.js';
import { BOT_TYPES } from './config.js';

export { STATE, SLOT_TYPE, SLOTS };

// every healthy client sends these all the time; they only clutter a debug trace
const TRACE_NOISE = new Set([T.KEEPALIVE, T.TICK_SPEED, T.TICK_MAXSPEED, T.TICK_DESSPEED]);

export class Room {
  /**
   * @param config   see config.js
   * @param log      logger (log.js)
   * @param now      monotonic clock in ms (injectable for tests)
   * @param random   random integer in [0, n) (injectable for tests)
   * @param opts     { id: 1-based room number, map: entry of config.ROOM_LIST, engine, replay: a Replay (replay.js) }
   */
  constructor(config, log, now = () => performance.now(), random = (n) => randomInt(n), opts = {}) {
    this.config = config;
    this.log = log;
    this.now = now;
    this.random = random;
    this.id = opts.id ?? 1;
    this.replay = opts.replay ?? null; // replay mode (plan §18.7): the lobby and the frames come from a recording
    this.map = opts.map ?? this.replay?.map ?? config.ROOM_LIST[0];
    // occupied slots (fakes, AI and real players) may not exceed the map's player count (F22)
    this.capacity = this.map.players;
    // bots in this room's next game: FAKE_PLAYERS (1 = the master bot) until the players change it
    // with `/botcount N` in the lobby chat (19 Sep 2026, §19.10); back to the default at every reset
    this.botCount = config.FAKE_PLAYERS;
    // the bots' brain for the next game: BOT_TYPE until the players change it with `/bottype T`
    this.botType = config.BOT_TYPE;
    // may the bots be hired (1000 buys an alliance)? BOT_HIRE until the players change it with `/bothire`
    this.botHire = config.BOT_HIRE;
    this.minPlayers = this.minPlayersFor(this.botCount);
    if (this.replay) this.minPlayers = 1; // the one client in the recorded player's seat
    this.state = STATE.LOBBY;
    this.clients = new Set();
    this.startingAt = 0;
    this.gamesPlayed = 0;
    this.slots = new Array(SLOTS);
    this.resetSlots();
    this.lobby = new Lobby(this);
    this.game = new Game(this);
    this.watchdog = new Watchdog(this);
    // the battle engine beside the relay (plan §18). The engine module is loaded asynchronously by
    // index.js (enginebridge.js) and handed in through setEngine(); tests inject a fake one.
    this.sync = new SyncCheck(this, {
      mode: config.SYNC_CHECK,
      recorder: teeRecorders([
        config.RECORD_DIR ? new Recorder(config.RECORD_DIR, this.log) : null,
        config.RECORD_LOG ? new LogRecorder(this.log) : null,
      ]),
      createGame: opts.engine?.createGame ?? null,
      loadMapJson: opts.engine?.loadMapJson ?? null,
    });
    // the fake players in battle: rush + the 1000-money alliance (plan §19.8); need the engine
    this.bots = new Bots(this);
  }

  /** The fake host's bot (AI Mercenary), for callers that only know about it. */
  get mercenary() {
    return this.bots.mercenary;
  }

  /** The engine factory ({ createGame, loadMapJson }) once it is loaded, or null when it is not available. */
  setEngine(engine) {
    this.sync.createGame = engine?.createGame ?? null;
    this.sync.loadMapJson = engine?.loadMapJson ?? null;
  }

  // ---- slots --------------------------------------------------------------------------------

  emptySlot(s) {
    const cfg = this.config;
    // AI fill needs all eight slots to count as occupied, which only an 8-player map allows (F22)
    const ai = cfg.FILL_EMPTY_WITH_AI && this.capacity === SLOTS;
    return {
      slot: s,
      name: '',
      race: 0,
      colour: s,
      team: s,
      type: ai ? cfg.FILL_AI_TYPE : SLOT_TYPE.EMPTY,
      status: 0,
      client: null,
    };
  }

  fakeSlot(s, name) {
    const r = this.config.MERCENARY_RACE;
    return {
      slot: s,
      name,
      race: r === 'random' ? this.random(2) : r, // a random race per bot (maintainer, 19 Sep 2026)
      colour: s,
      team: s,
      type: SLOT_TYPE.HUMAN,
      status: 1, // present but not ready: holds the lobby until the start signal (F3)
      client: null,
      fake: true,
    };
  }

  resetSlots() {
    if (this.replay) {
      // the recorded lobby: every recorded human a fake with its recorded name, race, colour and
      // team, AI and empty slots as recorded, the replayed player's seat left free (§18.7)
      const slots = this.replay.lobbySlots();
      for (let s = 0; s < SLOTS; s++) this.slots[s] = slots[s];
      return;
    }
    const cfg = this.config;
    for (let s = 0; s < SLOTS; s++) this.slots[s] = this.emptySlot(s);
    // the fake host "Mercenary" sits in MERCENARY_SLOT (0 unless a diagnostic session moves it so
    // that a real player becomes the lowest network id and sends 0x08 checksums, plan §18)
    const m = cfg.MERCENARY_SLOT;
    this.slots[m] = this.fakeSlot(m, cfg.FAKE_NAME_POOL[0]);
    // further fake humans take random slots, so that the real players' slots (and with them
    // their start positions, F12) differ from game to game
    if (this.botCount > 1) {
      const pool = [1, 2, 3, 4, 5, 6, 7].filter((s) => s !== m);
      for (let i = pool.length - 1; i > 0; i--) {
        const j = this.random(i + 1);
        [pool[i], pool[j]] = [pool[j], pool[i]];
      }
      for (let i = 1; i < this.botCount; i++) this.slots[pool[i - 1]] = this.fakeSlot(pool[i - 1], cfg.FAKE_NAME_POOL[i]);
    }
  }

  fakeSlots() {
    return this.slots.filter((sl) => sl.fake);
  }

  /** Real players needed to start with `bots` bots on this map (MIN_PLAYERS, capped by the seats left). */
  minPlayersFor(bots) {
    return Math.max(1, Math.min(this.config.MIN_PLAYERS, this.capacity - bots));
  }

  /** The most bots this room can hold right now: the map's slots minus the real players present. */
  maxBots() {
    return Math.max(1, Math.min(7, this.capacity - this.clients.size));
  }

  /**
   * `/bottype T` (19 Sep 2026, maintainer: "so there is a possibility to apply rusher too"): the brain
   * of every bot of the next game in this room, `krusty`, `rusher` or `random` (each bot draws one at
   * game start). Returns null when done, else the reason why not.
   */
  /** `/bothire on|off` (19 Sep 2026): whether the bots of the next game sell their alliance. */
  setBotHire(v) {
    if (this.state !== STATE.LOBBY || this.replay) return 'only in the lobby';
    const w = String(v ?? '').trim().toLowerCase();
    if (!['on', 'off'].includes(w)) return 'say on or off';
    this.botHire = w === 'on';
    this.lobby.onBotsChanged();
    return null;
  }

  setBotType(t) {
    if (this.state !== STATE.LOBBY || this.replay) return 'only in the lobby';
    const type = String(t ?? '').trim().toLowerCase();
    if (!BOT_TYPES.includes(type)) return `the type must be one of ${BOT_TYPES.join(', ')}`;
    this.botType = type;
    this.lobby.onBotsChanged();
    return null;
  }

  /**
   * `/botcount N` (19 Sep 2026, maintainer: the bot count is set by the players, the default is the
   * one master bot): change the number of fake humans in the LOBBY. Extra bots take random free
   * slots and are announced to every client like a joiner's dump (colour before type, F25); removed
   * bots leave with a DISCONNECT, the master bot in MERCENARY_SLOT never goes. Returns null when
   * done, else the reason why not.
   */
  setBotCount(n) {
    if (this.state !== STATE.LOBBY || this.replay) return 'only in the lobby';
    if (!Number.isInteger(n) || n < 1 || n > 7) return 'the count must be 1..7';
    const max = this.maxBots();
    if (n > max) return `at most ${max} bot${max === 1 ? '' : 's'} fit${max === 1 ? 's' : ''} on this map with ${this.clients.size} player${this.clients.size === 1 ? '' : 's'} in the room`;
    const cfg = this.config;
    const fakes = this.fakeSlots();
    if (n < fakes.length) {
      // the last names of the pool go first; AI Mercenary (slot MERCENARY_SLOT) stays
      const removable = fakes.filter((f) => f.slot !== cfg.MERCENARY_SLOT).sort((a, b) => cfg.FAKE_NAME_POOL.indexOf(b.name) - cfg.FAKE_NAME_POOL.indexOf(a.name));
      for (const f of removable.slice(0, fakes.length - n)) {
        this.slots[f.slot] = this.emptySlot(f.slot);
        this.broadcast(build.disconnect(f.slot));
        this.log.info('bot removed', { name: f.name, slot: f.slot });
      }
    } else if (n > fakes.length) {
      const used = new Set(fakes.map((f) => f.name));
      for (let i = fakes.length; i < n; i++) {
        const free = this.seatableSlots();
        if (free.length === 0) break;
        const s = free[this.random(free.length)].slot;
        const name = cfg.FAKE_NAME_POOL.find((x) => !used.has(x)) ?? `${cfg.MERCENARY_NAME} ${i + 1}`;
        used.add(name);
        const f = this.fakeSlot(s, name);
        f.colour = this.freeColour(s);
        this.slots[s] = f;
        this.broadcast(Buffer.concat([build.colourSet(f.colour, s), build.name(s, f.name), build.race(f.race, s), build.type(f.type, s), build.teamSet(f.team, s), build.ready(f.status, s)]));
        this.log.info('bot added', { name, slot: s });
      }
    }
    this.botCount = this.fakeSlots().length;
    this.minPlayers = this.minPlayersFor(this.botCount);
    this.bots.reset(); // one AiPlayer per fake slot
    this.lobby.onBotsChanged();
    return null;
  }

  freeSlots() {
    return this.slots.filter((sl) => !sl.fake && !sl.client);
  }

  /** Free slots a real player or a relocated fake may take: never slot 0 (the lowest network id, F14). */
  seatableSlots() {
    if (this.replay) return this.freeSlots().filter((sl) => sl.slot === this.replay.seat); // the recorded player's seat only
    return this.freeSlots().filter((sl) => sl.slot > 0);
  }

  /** Seats for real players on this map. */
  seats() {
    return this.capacity - this.fakeSlots().length;
  }

  isFull() {
    return this.clients.size >= this.seats();
  }

  /**
   * Can a client whose slot number is `s` join now? Needs LOBBY, a free seat and that slot not held
   * by a real player; a fake sitting in `s` is moved to a free slot when the client arrives (§17.5).
   */
  canJoin(s) {
    if (this.state !== STATE.LOBBY || this.isFull()) return false;
    const slot = this.slots[s];
    if (!slot || s <= 0 || slot.client) return false;
    if (s === this.config.MERCENARY_SLOT) return false; // the fake host is never moved
    return !slot.fake || this.seatableSlots().length > 0;
  }

  /** Move the fake in slot `s` to a random free slot (fakes have no client, so nothing else notices). */
  relocateFake(s) {
    const free = this.seatableSlots();
    if (free.length === 0) return false;
    const t = free[this.random(free.length)].slot;
    const f = this.slots[s];
    this.slots[t] = { ...f, slot: t, colour: t, team: t };
    this.slots[s] = this.emptySlot(s);
    this.log.debug('fake moved', { name: f.name, from: s, to: t });
    return true;
  }

  /** Connected, not evicted clients. */
  players() {
    const out = [];
    for (const c of this.clients) if (!c.gone) out.push(c);
    return out;
  }

  /**
   * What the hall shows about this room. `slots` is the number shown as the room's size: the map's
   * player slots without the fake players (they are the server's, not seats); `seats` is what is
   * really left for real players right now.
   */
  summary() {
    return { id: this.id, map: this.map, state: this.state, players: this.clients.size, seats: this.seats(), slots: this.capacity - this.fakeSlots().length };
  }

  // ---- connections --------------------------------------------------------------------------

  /** Direct join (HALL=false or tests): a new socket gets a random free slot in this room. */
  accept(socket) {
    const now = this.now();
    const client = new Client(socket, undefined, now);
    client.owner = this;
    client.wire();
    if (this.log.level === 'debug') client.onSend = (c, payloads) => this.traceTx(c, payloads);
    if (this.state !== STATE.LOBBY) return this.reject(client, 'a battle is in progress, try again later');
    const free = this.seatableSlots();
    if (free.length === 0 || this.isFull()) return this.reject(client, 'the lobby is full');
    // random free slot in 1..7: this is what randomises the start positions (plan §7)
    return this.adopt(client, free[this.random(free.length)].slot, true);
  }

  /**
   * Seat `client` in slot `s` (its slot number never changes, F30) and run the join sequence.
   * `handshake` = send 'd' first (a fresh connection); false for a client coming from the hall.
   */
  adopt(client, s, handshake) {
    if (this.slots[s].fake && !this.relocateFake(s)) throw new Error(`slot ${s} is held by a fake and no slot is free`);
    const slot = this.slots[s];
    slot.client = client;
    slot.type = SLOT_TYPE.HUMAN;
    slot.name = client.name || `Player${s}`;
    slot.race = 0;
    slot.colour = this.freeColour(s);
    slot.team = s;
    if (this.replay) {
      // pinned to the recording: the start shuffle and the simulation depend on them
      const rec = this.replay.seatSlot;
      slot.race = rec.race;
      slot.colour = rec.colour;
      slot.team = rec.team;
    }
    // Present, not ready. A client from the hall pressed READY to get here and its READY button stays
    // pressed (no lobby message can release it, F36), so its first click in the room sends status 1,
    // a no-op, and the second one readies it. The maintainer prefers that to an automatic start.
    slot.status = 1;
    client.slot = s;
    client.name = slot.name;
    client.owner = this;
    if (this.log.level === 'debug') client.onSend = (c, payloads) => this.traceTx(c, payloads);
    this.clients.add(client);
    this.log.info('client joined', {
      id: client.id,
      slot: s,
      address: client.address,
      players: this.clients.size,
      fakeSlots: this.fakeSlots().map((f) => f.slot),
    });
    this.lobby.onJoin(client, handshake);
  }

  /** Colour for a newcomer in slot `s`: its slot number unless an occupied slot already shows it (F4). */
  freeColour(s) {
    const used = new Set(this.slots.filter((q) => q.slot !== s && (q.fake || q.client)).map((q) => q.colour));
    if (!used.has(s)) return s;
    for (let c = 0; c < SLOTS; c++) if (!used.has(c)) return c;
    return s;
  }

  reject(client, text) {
    this.log.info('connection rejected', { address: client.address, reason: text, state: this.state });
    client.sendBatch([build.version(this.config.PROTOCOL_VERSION, SLOTS - 1), build.lobbyChat(text)]);
    client.gone = true;
    try {
      client.socket.end();
    } catch {
      // ignore
    }
  }

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
    const { batches, resyncs, error } = readCommands(client, chunk, this.config.STRICT_SEQ);
    for (const r of resyncs) this.log.warn('sequence resync', { id: client.id, ...r });
    for (const b of batches) {
      if (client.gone) return;
      if (!client.firstMessageAt) client.firstMessageAt = now;
      if (this.log.level === 'debug') this.trace(client, b.seq, b.cmds);
      this.dispatch(client, b.cmds, now);
    }
    if (error) this.evict(client, error);
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

  /** A line from the relay itself in every player's chat window; no name in front of it (maintainer, 7 Sep 2026). Ignored outside the lobby. */
  say(text) {
    if (this.state !== STATE.LOBBY) return;
    this.chat(text);
  }

  /** Append a line to every player's chat window and repaint the windows (chat.js, §17.8). */
  chat(text, except = null) {
    for (const c of this.clients) {
      if (c === except || c.gone) continue;
      c.chat.push(text);
      c.sendBatch(this.lobby.pack(c.chat.payloads()));
    }
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
    // The battle numbers tell a silent client apart from a lagging one (25 Sep 2026, plan §16):
    // sinceLastMessageMs = how long nothing at all arrived (echoes, progress reports, commands);
    // clientTime/serverUntil = where its simulation stood against the frames issued.
    const now = this.now();
    const battle = this.state === STATE.RUNNING || this.state === STATE.STARTING
      ? {
        sinceLastMessageMs: now - client.lastSeen,
        clientTime: client.clientTime,
        serverUntil: this.game.lastIssuedUntil,
        pendingEchoes: client.pendingEchoes.size,
        latencyMs: client.latencyMs < 0 ? null : Math.round(client.latencyMs),
      }
      : {};
    this.log.info('client left', {
      id: client.id,
      slot: s,
      name,
      reason,
      state: this.state,
      players: this.clients.size,
      ...battle,
    });
    if (slot && slot.client === client) this.slots[s] = this.emptySlot(s);
    switch (this.state) {
      case STATE.LOBBY:
        if (wasReady) this.broadcast(build.ready(0, s)); // release the colour lock first (F20)
        this.broadcast(build.disconnect(s));
        this.say(`${name} left the lobby (${reason})`);
        break;
      case STATE.STARTING:
      case STATE.RUNNING: {
        this.bots.onClientLeft(client); // an alliance bought from a bot ends with the buyer
        // the base becomes a server bot when the engine plays (§19.9); otherwise DISCONNECT hands it
        // to the game's own AI on every client (F19)
        const takenOver = this.bots.takeOver(client);
        if (takenOver) client.pendingEchoes.clear();
        else this.game.onClientLeft(client);
        this.sync.onClientLeft(client, takenOver ? `${reason}; a bot took the base over` : reason);
        break;
      }
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
    if (this.state !== STATE.LOBBY) {
      if (this.bots.active) this.log.info('bots summary', { bots: this.bots.summary() });
      this.sync.stop('room reset');
    }
    this.state = STATE.LOBBY;
    this.startingAt = 0;
    for (const c of this.clients) c.destroy();
    this.clients.clear();
    this.botCount = this.config.FAKE_PLAYERS; // a fresh lobby starts with the default (one master bot)
    this.botType = this.config.BOT_TYPE;
    this.botHire = this.config.BOT_HIRE;
    this.minPlayers = this.replay ? 1 : this.minPlayersFor(this.botCount);
    this.replay?.rewind(); // every game of a replay server starts at the first recorded frame
    this.resetSlots();
    this.bots.reset(); // one bot per (freshly placed) fake slot
    this.lobby.reset();
    this.game.reset();
  }

  beginStarting(now) {
    this.state = STATE.STARTING;
    this.startingAt = now;
    for (const c of this.clients) c.mready = false;
    // the lobby as every client sees it when the start signal goes out: the engine's input (§18)
    this.startSlots = this.slots.map((q) => ({ type: q.type, race: q.race, colour: q.colour, team: q.team, name: q.name }));
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
    this.sync.start(this.startSlots ?? this.slots);
    for (const c of this.players()) if (c.gamePlayer >= 0) this.sync.onMready(c, c.gamePlayer);
    this.game.start(now);
    this.bots.onRunning();
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

// The server-side battle engine at work in a room (plan §18): runs the same simulation the
// clients run, from the same lobby state and the same sync frames, and produces the lockstep
// checksum (sync.c 0x44ABC0) the game's own host never produces (F14: with Mercenary in slot 0
// nobody sends 0x08).
//
// Timing (read from the client's pacing loop 0x41E268 / 0x41E0D8, 11 Sep 2026):
//   * a sync frame UNTIL(a, u) is executed as a whole (marker + commands) when the client's
//     game_time == u - 1, then game_time becomes u and game_tick runs;
//   * the checksum of tick t is recorded after that tick under history[t % 256];
//   * a 0x08 (checksum, t) is checked when the frame that carries it is executed, which needs
//     t <= game_time (= u - 1 of that frame) and game_time - t < 256.
// So after the server has issued frame k with until u_k the engine can run up to tick u_k (all
// commands that apply before it are known), and frame k+1 may carry 0x08(checksum(u_k), u_k).
//
// Modes (config SYNC_CHECK): off = nothing; shadow = the engine runs, checksums are logged and
// recorded and compared with 0x08 messages received from clients; send = shadow plus one
// 0x08 command in every sync frame. Any exception inside the engine disables it for the rest of
// the game (the relay itself is never affected).

import { T, build, splitCommands, decode } from './commands.js';
import { STATE } from './constants.js';

export const SYNC_MODES = Object.freeze(['off', 'shadow', 'send']);

export class SyncCheck {
  /**
   * @param room     the Room
   * @param opts     { mode, recorder (Recorder | null), createGame(mapJson, lobby, opts) -> engine,
   *                   loadMapJson(file) -> json | null }
   */
  constructor(room, opts) {
    this.room = room;
    this.mode = opts.mode ?? 'off';
    this.recorder = opts.recorder ?? null;
    this.createGame = opts.createGame;
    this.loadMapJson = opts.loadMapJson;
    this.log = room.log;
    this.reset();
  }

  reset() {
    this.engine = null;
    this.enabled = false;
    this.disabledReason = null;
    this.engineTime = 0; // ticks simulated
    this.pending = []; // issued frames not yet simulated: { until, cmds: Buffer[] }
    this.lastChecksum = null; // { tick, checksum } of the last simulated tick, sent once
    this.sentTicks = 0;
    this.mismatches = 0;
    this.compared = 0;
    this.firstMismatch = null;
    this.rx08 = 0;
    this.slotToPlayer = null;
    this.aiTakeover = false;
    this.recorder?.close();
  }

  get active() {
    return this.mode !== 'off' && this.enabled && this.engine !== null;
  }

  // ---- lifecycle -------------------------------------------------------------------------------

  /** Called from Room.beginRunning() with the slots as every client saw them at the start signal. */
  start(lobbySlots) {
    this.reset();
    if (this.mode === 'off' && !this.recorder) return;
    const r = this.room;
    const lobby = {
      slots: lobbySlots.map((s) => ({ type: s.type, race: s.race, colour: s.colour, team: s.team, name: s.name })),
      localSlot: -1,
      titleDigit: r.map.players,
    };
    if (this.recorder) {
      this.recorder.open(r.id, {
        version: 1,
        map: { file: r.map.file, name: r.map.name, terrain: r.map.terrain, players: r.map.players },
        tickMs: r.config.TICK_MS,
        lookahead: r.config.LOOKAHEAD,
        syncCheck: this.mode,
        mercenarySlot: r.config.MERCENARY_SLOT,
        lobby,
        players: r.players().map((c) => ({ slot: c.slot, name: c.name })),
      });
    }
    if (this.mode === 'off') return;
    if (!this.createGame || !this.loadMapJson) {
      this.disable('engine module not available');
      return;
    }
    if (lobby.slots.some((s) => s.type === 0 || s.type === 1)) {
      this.disable('computer players in the lobby: the AI is not ported (engine/ai.js)');
      return;
    }
    const mapJson = this.loadMapJson(r.map.file);
    if (!mapJson) {
      this.disable(`no maps/${r.map.file.replace(/\.scn$/i, '')}.json`);
      return;
    }
    try {
      this.engine = this.createGame(mapJson, lobby, { log: this.log, assert: (msg) => this.onAssert(msg) });
      this.slotToPlayer = this.engine.scenario?.slotToPlayer ?? this.engine.slotToPlayer ?? null;
      this.enabled = true;
      this.log.info('engine started', { mode: this.mode, map: r.map.file, objects: this.engine.gs.readInt32LE(0x7d40) + 1 });
    } catch (err) {
      this.disable(`engine start failed: ${err.message}`);
      this.log.warn('engine start failed', { err: err.stack ?? String(err) });
    }
  }

  disable(reason) {
    if (this.disabledReason) return;
    this.enabled = false;
    this.disabledReason = reason;
    this.log.warn('engine disabled', { reason, tick: this.engineTime });
    this.recorder?.write({ type: 'note', text: `engine disabled: ${reason}`, tick: this.engineTime });
  }

  onAssert(msg) {
    // an assertion of the original would end the game there; the server keeps relaying
    this.log.warn('engine assert', { msg, tick: this.engineTime });
    this.recorder?.write({ type: 'assert', tick: this.engineTime, msg });
  }

  stop(reason) {
    this.recorder?.close({
      reason,
      engineTime: this.engineTime,
      sentTicks: this.sentTicks,
      compared: this.compared,
      mismatches: this.mismatches,
      firstMismatch: this.firstMismatch,
      disabled: this.disabledReason,
    });
    if (this.mode !== 'off') {
      this.log.info('engine stopped', {
        reason,
        engineTime: this.engineTime,
        compared: this.compared,
        mismatches: this.mismatches,
        firstMismatch: this.firstMismatch,
        disabled: this.disabledReason,
      });
    }
    this.engine = null;
    this.enabled = false;
  }

  // ---- frames ----------------------------------------------------------------------------------

  /**
   * The 0x08 command for the frame about to be built (mode send only): the checksum of the last
   * simulated tick, once. Must be called BEFORE onFrameIssued of the same frame.
   */
  syncCommand() {
    if (this.mode !== 'send' || !this.active || !this.lastChecksum) return null;
    const { tick, checksum } = this.lastChecksum;
    try {
      return build.sync(checksum, tick);
    } catch (err) {
      // never let the engine side take the relay down (11 Sep 2026: a signed 16-bit write of a
      // checksum above 32767 crashed the whole server in the first send-mode game)
      this.disable(`cannot build the 0x08 command: ${err.message}`);
      return null;
    }
  }

  /** The command returned by syncCommand() went into the frame. */
  markSent() {
    this.lastChecksum = null;
    this.sentTicks++;
  }

  /**
   * A sync frame UNTIL(a, until) with `groups` (the command buffers after the UNTIL, our own 0x08
   * included) was broadcast: record it and advance the engine to `until`.
   */
  onFrameIssued(a, until, groups) {
    const cmds = groups.length ? Buffer.concat(groups) : Buffer.alloc(0);
    this.recorder?.write({ type: 'frame', a, until, cmds: cmds.toString('hex') });
    if (this.active) this.feed(until, cmds);
    // the bots think on the freshly simulated state (or notice that the engine is gone); their
    // commands go into the next frame
    this.room.bots?.onAdvanced(this.engineTime);
  }

  feed(until, cmds) {
    const list = [];
    try {
      for (const c of splitCommands(cmds)) list.push(c.raw);
    } catch (err) {
      this.disable(`bad frame in engine feed: ${err.message}`);
      return;
    }
    this.pending.push({ until, cmds: list });
    this.advance();
  }

  /** Simulate every tick whose commands are known (up to the last issued until). */
  advance() {
    const eng = this.engine;
    try {
      while (this.pending.length) {
        const frame = this.pending[0];
        if (frame.until <= this.engineTime) {
          // cannot happen with strictly increasing untils; keep the engine honest
          this.pending.shift();
          continue;
        }
        while (this.engineTime < frame.until - 1) this.tickOnce();
        // game_time == until - 1: execute the frame's commands, then the tick that reaches `until`
        for (const raw of frame.cmds) this.applyCommand(raw);
        this.pending.shift();
        this.tickOnce();
      }
    } catch (err) {
      this.disable(`engine error at tick ${this.engineTime}: ${err.message}`);
      this.log.warn('engine error', { err: err.stack ?? String(err), tick: this.engineTime });
    }
    void eng;
  }

  applyCommand(raw) {
    const type = raw[0];
    // the marker itself, our own checksums and the pacing/speed traffic are not simulation input
    if (type === T.UNTIL || type === T.SYNC || type === T.TICK) return;
    this.engine.applyCommand(raw);
    if (type === T.DISCONNECT) {
      // the lost player's base goes to the AI (0x41DBE0), which is not ported: from its first think
      // (within 32 ticks) the engine's state is no longer the clients' state
      this.aiTakeover = true;
      if (this.mode === 'send') this.disable(`AI took over slot ${raw[1]}: the AI is not ported (engine/ai.js)`);
      else this.log.warn('AI took over a base: engine checksums are no longer comparable', { slot: raw[1], tick: this.engineTime });
    }
  }

  tickOnce() {
    const checksum = this.engine.step();
    this.engineTime++;
    this.lastChecksum = { tick: this.engineTime, checksum };
    this.recorder?.write({ type: 'engine', tick: this.engineTime, checksum });
  }

  // ---- messages from clients -------------------------------------------------------------------

  /** A 0x08 (checksum, tick) from a real client: compare with the engine when we have that tick. */
  onClientSync(client, cmd) {
    const d = decode(cmd);
    this.rx08++;
    this.recorder?.write({ type: 'rx08', slot: client.slot, tick: d.time, checksum: d.checksum & 0xffff });
    if (!this.active) return;
    if (d.time > this.engineTime || this.engineTime - d.time >= 256) return;
    const mine = this.engine.historyAt(d.time);
    const theirs = d.checksum & 0xffff;
    this.compared++;
    if (mine === theirs) return;
    this.mismatches++;
    if (!this.firstMismatch) {
      this.firstMismatch = { tick: d.time, engine: mine, client: theirs, slot: client.slot };
      this.log.warn('checksum mismatch', this.firstMismatch);
      this.recorder?.write({ type: 'mismatch', ...this.firstMismatch });
      if (this.mode === 'send') this.disable('checksum mismatch: the engine diverged from the clients');
    }
  }

  /** MREADY(gamePlayer) from a client: the engine's shuffle must give the same game player index. */
  onMready(client, gamePlayer) {
    this.recorder?.write({ type: 'mready', slot: client.slot, gamePlayer });
    if (!this.active || !this.slotToPlayer) return;
    const mine = this.slotToPlayer[client.slot];
    if (mine !== gamePlayer) {
      this.log.warn('shuffle mismatch', { slot: client.slot, engine: mine, client: gamePlayer });
      this.recorder?.write({ type: 'note', text: `shuffle mismatch slot ${client.slot}: engine ${mine}, client ${gamePlayer}` });
      this.disable('start shuffle differs from the client');
    }
  }

  onClientLeft(client, reason) {
    this.recorder?.write({ type: 'left', slot: client.slot, reason, tick: this.engineTime, state: this.room.state });
  }

  onPause(paused, slot) {
    this.recorder?.write({ type: paused ? 'pause' : 'resume', slot, tick: this.engineTime });
  }

  /** Room state at the start signal, for the recording: only meaningful while RUNNING. */
  running() {
    return this.room.state === STATE.RUNNING;
  }

  summary() {
    return {
      mode: this.mode,
      active: this.active,
      engineTime: this.engineTime,
      sentTicks: this.sentTicks,
      rx08: this.rx08,
      compared: this.compared,
      mismatches: this.mismatches,
      disabled: this.disabledReason,
    };
  }
}

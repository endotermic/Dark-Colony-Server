// The server as fake host of the lobby (plan §6): join dump, message policy, the "ready" word,
// countdown, and the STARTING phase (waiting for MREADY).

import { T, build, decode, sanitizeName, sanitizeText, typeName } from './commands.js';
import { STATE, SLOT_TYPE, SLOTS, VAR_DEFAULTS } from './constants.js';
import { packPayloads } from './client.js';
import { ChatView } from './chat.js';

export class Lobby {
  constructor(room) {
    this.room = room;
    this.countdownEndsAt = -1;
  }

  get cfg() {
    return this.room.config;
  }

  reset() {
    this.countdownEndsAt = -1;
  }

  // ---- join ---------------------------------------------------------------------------------

  /**
   * Join sequence for a client seated in its slot. `handshake` = a fresh connection that still
   * needs the 'd'; a client coming from the hall (plan §17) already had it and only gets the dump,
   * exactly like the game's own meta-server join path (protocol doc §6.2).
   */
  onJoin(client, handshake = true) {
    const r = this.room;
    const s = client.slot;
    const dump = this.dumpPayloads(s);
    const mine = [];
    if (!handshake) {
      // coming from the hall: its seven room rows were occupied humans. Empty every slot except the
      // client's own and Mercenary's first (the DISCONNECT handler resets type, status and CD flag
      // and keeps the client's player count right; Mercenary's row stays status 1 so that the
      // client does not leave the lobby, F3)
      for (let q = 0; q < SLOTS; q++) if (q !== s && q !== this.cfg.MERCENARY_SLOT) mine.push(build.disconnect(q));
    }
    // a fresh chat window with the room greeting pinned at the top (§17.8); the hall's lines are gone
    client.chat = new ChatView(this.greeting());
    mine.push(...dump, ...client.chat.payloads());
    // 'd' first (its own frame), then everything else, in one TCP write so the client sees it all at once
    client.sendBatch(handshake ? [build.version(this.cfg.PROTOCOL_VERSION, s), ...this.pack(mine)] : this.pack(mine));
    for (const c of r.players()) if (c !== client) c.sendBatch(this.pack(dump));
    this.cancelCountdown('a player joined');
  }

  /** Several commands per frame when allowed (F34), else one command per frame as the original host. */
  pack(payloads) {
    return this.cfg.PACK_LOBBY_FRAMES ? packPayloads(payloads) : payloads;
  }

  /** The static header of a player's chat window in this room: the room line only (maintainer, 7 Sep 2026). */
  greeting() {
    const r = this.room;
    return [`Room ${r.id}: ${r.map.name}, ${r.map.terrain}, ${r.map.players} players.`];
  }

  /** The host's lobby state dump for a client in slot `s` (protocol doc §6.1), without the 'd'. */
  dumpPayloads(s) {
    const r = this.room;
    const out = [build.scenario(r.map.file, r.map.titleWire)];
    for (const q of r.slots) if (q.slot !== s) out.push(build.colourSet(q.colour, q.slot));
    for (const q of r.slots) {
      if (q.slot === s) continue;
      out.push(
        build.name(q.slot, q.name),
        build.race(q.race, q.slot),
        build.type(q.type, q.slot),
        build.teamSet(q.team, q.slot),
        build.ready(q.status, q.slot),
      );
    }
    const me = r.slots[s];
    out.push(
      build.name(s, me.name),
      build.race(me.race, s),
      build.type(SLOT_TYPE.HUMAN, s),
      build.colourSet(me.colour, s),
      build.teamSet(me.team, s),
      build.ready(me.status, s),
    );
    for (let v = 0; v < VAR_DEFAULTS.length; v++) out.push(build.variable(v, VAR_DEFAULTS[v]));
    return out;
  }

  // ---- LOBBY message policy (plan §6.2) --------------------------------------------------

  handle(client, cmds, now) {
    const r = this.room;
    for (const cmd of cmds) {
      if (client.gone || r.state !== STATE.LOBBY) return;
      const s = client.slot;
      const slot = r.slots[s];
      this.currentSlot = slot;
      switch (cmd.type) {
        case T.KEEPALIVE:
          break;

        case T.VAR: {
          const d = decode(cmd);
          // the client's own CD report: never relay it, everybody always "has the disc"
          if (d.index >= 8 && d.index < 16) r.broadcast(build.variable(8 + s, 1));
          else this.drop(client, cmd, 'host-owned VAR');
          break;
        }

        case T.NAME: {
          const d = decode(cmd);
          if (d.player !== s) {
            this.drop(client, cmd, 'foreign slot');
            break;
          }
          slot.name = sanitizeName(d.name, `Player${s}`);
          client.name = slot.name;
          r.broadcast(build.name(s, slot.name));
          break;
        }

        case T.RACE: {
          const d = decode(cmd);
          if (d.player !== s || d.value > 1) {
            this.drop(client, cmd, 'foreign slot or bad race');
            break;
          }
          if (slot.status === 2) break; // the game ignores it for ready slots
          slot.race = d.value;
          r.broadcast(build.race(d.value, s));
          break;
        }

        case T.COLOUR_CYCLE: {
          const d = decode(cmd);
          if (d.player !== s) {
            this.drop(client, cmd, 'foreign slot');
            break;
          }
          if (slot.status === 2) break;
          // mirror of the client's handler: add delta until a colour that no ready slot holds
          let c = slot.colour;
          for (let i = 0; i < 8; i++) {
            c = (c + d.value) % 8;
            if (!this.colourLocked(c, s)) break;
          }
          slot.colour = c;
          r.broadcast(build.colourCycle(d.value, s));
          break;
        }

        case T.TEAM_CYCLE: {
          const d = decode(cmd);
          if (d.player !== s) {
            this.drop(client, cmd, 'foreign slot');
            break;
          }
          if (slot.status === 2) break;
          slot.team = (slot.team + d.value) % 8;
          r.broadcast(build.teamCycle(d.value, s));
          break;
        }

        case T.READY: {
          // the READY button; it works for every joiner, the eighth slot included (verified live)
          const d = decode(cmd);
          if (d.player !== s || (d.status !== 1 && d.status !== 2)) {
            this.drop(client, cmd, 'foreign slot or bad status');
            break;
          }
          const before = slot.status;
          const after = this.applyReady(d.status);
          r.broadcast(build.ready(d.status, s)); // every client applies the same colour rule (F4)
          if (after === 2 && before !== 2) {
            const { ready, total } = this.readyCount();
            r.say(`${slot.name} is ready (${ready}/${total})`);
          }
          this.checkStart(now);
          break;
        }

        case T.LOBBY_CHAT: {
          // plain relay into every window (the player's own "Name: text" prefix stays); chat has no commands
          r.chat(sanitizeText(decode(cmd).text));
          break;
        }

        case T.INIT_ME: {
          client.initMeCount++;
          if (client.initMeCount > 1) {
            r.strike(client, 'repeated INIT_ME');
            break;
          }
          const dump = this.pack(this.dumpPayloads(s));
          for (const c of r.players()) c.sendBatch(dump);
          break;
        }

        case T.TYPE:
        case T.COLOUR_SET:
        case T.TEAM_SET:
        case T.SCENARIO:
        case T.NUKE:
          this.drop(client, cmd, 'host-owned setting');
          break;

        default:
          if (cmd.type < 0x64) r.strike(client, `in-game command ${typeName(cmd.type)} in the lobby`);
          else r.strike(client, `unexpected lobby message ${typeName(cmd.type)}`);
      }
    }
  }

  drop(client, cmd, why) {
    this.room.log.debug('dropped', { id: client.id, slot: client.slot, type: typeName(cmd.type), why });
  }

  /** Is `colour` locked by a ready slot other than `exceptSlot`? (client table ss+0x248) */
  colourLocked(colour, exceptSlot) {
    return this.room.slots.some((q) => q.slot !== exceptSlot && q.status === 2 && q.colour === colour);
  }

  /** Mirror of the client's 'h' handler (0x40F23C) for the sender's own slot. Returns the resulting status. */
  applyReady(status) {
    // note: called with the slot of the sending client (see handle())
    const slot = this.currentSlot;
    if (status === 2) {
      if (slot.status === 2) return 2;
      if (this.colourLocked(slot.colour, slot.slot)) {
        slot.status = 1; // refused on every client: colour already taken by a ready player
        return 1;
      }
      slot.status = 2;
      return 2;
    }
    slot.status = status;
    return status;
  }

  // ---- start condition (plan §6.3) ---------------------------------------------------------

  isReady(client) {
    return this.room.slots[client.slot].status === 2;
  }

  readyCount() {
    const players = this.room.players();
    return { ready: players.filter((c) => this.isReady(c)).length, total: players.length };
  }

  checkStart(now) {
    const r = this.room;
    if (r.state !== STATE.LOBBY) return;
    const { ready, total } = this.readyCount();
    const allReady = total >= r.minPlayers && ready === total;
    if (!allReady) {
      this.cancelCountdown('not everyone is ready');
      return;
    }
    if (this.countdownEndsAt >= 0) return;
    const secs = this.cfg.START_COUNTDOWN_S;
    if (secs <= 0) {
      r.say('all players ready, starting now');
      r.beginStarting(now);
      return;
    }
    this.countdownEndsAt = now + secs * 1000;
    r.say(`all players ready, starting in ${secs} s`);
  }

  cancelCountdown(why) {
    if (this.countdownEndsAt < 0) return;
    this.countdownEndsAt = -1;
    this.room.say(`start cancelled: ${why}`);
  }

  /** Called by the watchdog: fires the countdown. */
  tick(now) {
    const r = this.room;
    if (r.state !== STATE.LOBBY || this.countdownEndsAt < 0 || now < this.countdownEndsAt) return;
    this.countdownEndsAt = -1;
    const { ready, total } = this.readyCount();
    if (total >= r.minPlayers && ready === total) {
      r.say('starting now');
      r.beginStarting(now);
    }
  }

  onLeave() {
    this.cancelCountdown('a player left');
    this.checkStart(this.room.now());
  }

  // ---- STARTING: waiting for MREADY (plan §6.4) ------------------------------------------

  handleStarting(client, cmds, now) {
    const r = this.room;
    const inGame = [];
    for (const cmd of cmds) {
      if (client.gone || r.state !== STATE.STARTING) return;
      switch (cmd.type) {
        case T.MREADY: {
          // `player` is the game player index after the client-side start-position shuffle,
          // not the lobby slot; the original server ignores it and only checks the state byte.
          const d = decode(cmd);
          if (d.state !== 2 || d.player < 0 || d.player > 7 || client.mready) {
            return r.evict(client, `bad MREADY (player ${d.player}, state ${d.state}${client.mready ? ', duplicate' : ''})`);
          }
          client.mready = true;
          client.gamePlayer = d.player;
          r.log.info('client loaded', { id: client.id, slot: client.slot, gamePlayer: d.player });
          this.checkAllLoaded(now);
          break;
        }
        case T.KEEPALIVE:
        case T.VAR:
          break; // lobby stragglers sent just before the client left the lobby loop
        default:
          if (cmd.type < 0x64) inGame.push(cmd); // early in-game traffic: filter/queue as usual
          else this.drop(client, cmd, 'lobby message while starting');
      }
    }
    if (inGame.length && !client.gone) r.game.handle(client, inGame, now);
  }

  checkAllLoaded(now) {
    const r = this.room;
    if (r.state !== STATE.STARTING) return;
    const players = r.players();
    if (players.length > 0 && players.every((c) => c.mready)) r.beginRunning(now);
  }
}

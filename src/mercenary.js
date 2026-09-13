// The fake players in battle (13 Sep 2026, maintainer request; plan §19.8, §19.9).
//
// Every fake human of the room (AI Mercenary in MERCENARY_SLOT and, since the same day, AI Marauder
// in a random slot; FAKE_PLAYERS decides how many) is an AiPlayer: it rushes (rusher.js) and it
// sells an alliance. Whoever gives it 1000 with the diplomacy screen's "give 1000" button (command
// 0x0F, F47) becomes its ally with shared vision for MERCENARY_ALLY_S seconds; while an alliance
// runs, further payments are returned (0x0F back to the payer, minus 1000 on the bot's ledger).
// The bots talk in the battlefield chat (0x0E from the bot's game player): the offer, the deal
// and its end go to the players concerned (the offer to everybody), but a bot's ACTIONS (the
// rusher's decisions) reach only its current ally through the message's player mask (maintainer,
// 13 Sep 2026: "AI bots must send their actions only to the allied client"); without an ally they
// are not sent at all. The bots keep the peace among themselves by default (maintainer, 13 Sep
// 2026: "bots must ally each other by default so there is no war between bots when not hired by
// anyone"): a lone player fights both, and a bot it buys turns on the bots that do not serve it.
//
// The alliance is set in BOTH directions (bot -> payer and payer -> bot, alliance and vision): the
// game evaluates a relation only when both bits match (F48), and its end-of-game check (F49:
// 0x40E260) declares the battle over, with Victory for every player still alive, as soon as every
// alive player is mutually allied with the FIRST alive one. That check is a star around the first
// alive player, so two rival bots bought by the same player did not end the battle in the first live
// test (13 Sep 2026, plan §16): the human was allied with both, but the bots were not allied with
// each other. Bots that share a paying ally therefore also ally with each other (a "pact", four more
// 0x0D, Bots.syncPacts) for as long as both deals hold; the pact is cleared when either deal ends.
// Buying the alliance of every remaining bot then wins the game for a lone player.
//
// A real client that leaves a running battle is taken over by a new AiPlayer (§19.9) instead of
// being handed to the game's own AI with DISCONNECT: the player stays a human on the wire, the
// engine stays in step (the AI is not ported), and the base plays and sells alliances like the
// fakes. Its money is normalised as the original DISCONNECT does (money -= spent, F44).
//
// The bots need the server engine (plan §18): their game player indices come from the engine's
// start shuffle, their money exists only there (F44) and the rusher reads the battle from it.
// Without an active engine (SYNC_CHECK=off, missing map JSON, a divergence) the bases stay idle as
// before, the deal is off, and a leaving client is handed to the game's AI as in 2.x.

import { build, sanitizeText } from './commands.js';
import { STATE } from './constants.js';
import { P, playerAddr, i32, w32 } from './engine/mem.js';
import { Rusher } from './rusher.js';

export const ALLY_PRICE = 1000; // fixed by the game's button (0x433564: money > 1000, money -= 1000)
export const MERCENARY_AI_MODES = Object.freeze(['off', 'rusher']);
const MAX_LINES_PER_THINK = 2; // the client shows a queue of six messages
const PHASE_STEP = 8; // ticks between the thinks of two bots (spreads their chat and their orders)

/** All bots of a room: one per fake slot, plus one per real player that left the battle. */
export class Bots {
  constructor(room) {
    this.room = room;
    this.list = [];
    this.pendingTakeovers = [];
    this.reset();
  }

  get cfg() {
    return this.room.config;
  }

  /** The bots play when MERCENARY_AI is not off and the engine will run (SYNC_CHECK). */
  get configured() {
    return this.cfg.MERCENARY_AI !== 'off' && this.cfg.SYNC_CHECK !== 'off';
  }

  /** One AiPlayer per fake slot; call after Room.resetSlots(). */
  reset() {
    this.list = this.room.fakeSlots().map((f) => new AiPlayer(this.room, f.slot, f.name));
    this.pendingTakeovers = [];
    this.pacts = new Map(); // "p:q" -> [botP, botQ], the alliances between bots that share an ally
  }

  /** The fake host (MERCENARY_SLOT). */
  get mercenary() {
    return this.list.find((b) => b.slot === this.cfg.MERCENARY_SLOT) ?? this.list[0] ?? null;
  }

  /** Bots other than the fake host, in creation order. */
  get others() {
    return this.list.filter((b) => b !== this.mercenary);
  }

  get active() {
    return this.list.some((b) => b.active);
  }

  byPlayer(p) {
    return this.list.find((b) => b.active && b.player === p) ?? null;
  }

  onRunning() {
    this.pacts = new Map();
    const m = this.mercenary;
    let i = 0;
    for (const b of [m, ...this.others].filter(Boolean)) b.onRunning(i++);
    // players who left while everybody was loading (STARTING) get their bot now
    for (const t of this.pendingTakeovers) this.startTakeover(t.slot, t.name);
    this.pendingTakeovers = [];
    this.syncPacts(); // the bots start allied with each other (first frame)
  }

  onAdvanced(tick) {
    for (const b of this.list) b.onAdvanced(tick);
    this.syncPacts();
  }

  onGift(client, target) {
    this.byPlayer(target)?.onGift(client);
    this.syncPacts();
  }

  onClientLeft(client) {
    for (const b of this.list) b.onClientLeft(client);
    this.syncPacts();
  }

  // ---- pacts: bots that share a paying ally are allied with each other (F49) --------------------

  static pactKey(a, b) {
    return a.player < b.player ? `${a.player}:${b.player}` : `${b.player}:${a.player}`;
  }

  /** Is `bot` in a pact with the bot that plays game player q? */
  arePartners(bot, q) {
    for (const [x, y] of this.pacts.values()) {
      if (x === bot && y.player === q) return true;
      if (y === bot && x.player === q) return true;
    }
    return false;
  }

  /**
   * Bring the pacts in line with the deals. Two active bots are allied with each other (both
   * matrices, both directions) exactly when they serve the same master: both unhired (the default,
   * maintainer 13 Sep 2026: "bots must ally each other by default so there is no war between bots
   * when not hired by anyone") or both bought by the same player (F49: the game's end check
   * compares every alive player with the first alive one, so a player allied with both bots wins
   * only when the bots are allied too). A hired bot therefore turns on the bots that are not
   * serving its ally, and the peace returns when its deal ends.
   */
  syncPacts() {
    const want = new Map();
    const active = this.list.filter((b) => b.active);
    for (let i = 0; i < active.length; i++) {
      for (let j = i + 1; j < active.length; j++) {
        const x = active[i];
        const y = active[j];
        if ((x.ally?.player ?? -1) === (y.ally?.player ?? -1)) want.set(Bots.pactKey(x, y), [x, y]);
      }
    }
    for (const [key, [x, y]] of this.pacts) {
      if (want.has(key)) continue;
      this.pacts.delete(key);
      this.room.game.queueCommands(x.relations(y.player, 0));
      // told to whoever of the two has an ally (actions go to allies only)
      if (x.ally) x.say(`My truce with ${y.chatName} is over. I turn on it for you.`, 1 << x.ally.player);
      if (y.ally) y.say(`My truce with ${x.chatName} is over. I turn on it for you.`, 1 << y.ally.player);
      this.room.log.info('bot: pact ended', { between: [x.name, y.name] });
    }
    for (const [key, [x, y]] of want) {
      if (this.pacts.has(key)) continue;
      this.pacts.set(key, [x, y]);
      this.room.game.queueCommands(x.relations(y.player, 1));
      if (x.ally) x.say(`${y.chatName} and I both serve you now. We hold our fire on each other.`, 1 << x.ally.player);
      this.room.log.info('bot: pact', { between: [x.name, y.name], ally: x.ally?.player ?? -1 });
    }
  }

  /**
   * A real client left the battle (§19.9): make its base a bot. Returns false when that is not
   * possible (bots off, engine off or gone) and the room must fall back to DISCONNECT (the game's
   * own AI takes over on every client).
   */
  takeOver(client) {
    if (!this.configured) return false;
    const slot = client.slot;
    if (!(slot >= 0)) return false;
    const name = this.room.slots[slot]?.name || client.name || `Player${slot}`;
    if (this.room.state === STATE.STARTING) {
      this.pendingTakeovers.push({ slot, name });
      return true;
    }
    if (this.room.state !== STATE.RUNNING || !this.room.sync.active) return false;
    return this.startTakeover(slot, name);
  }

  startTakeover(slot, name) {
    const bot = new AiPlayer(this.room, slot, name, { takeover: true });
    this.list.push(bot);
    bot.onRunning(this.list.length - 1);
    if (!bot.active) {
      this.list.pop();
      return false;
    }
    this.syncPacts(); // the inherited base joins the bots' peace
    return true;
  }

  summary() {
    return this.list.map((b) => b.summary());
  }
}

export class AiPlayer {
  /**
   * @param room  the Room
   * @param slot  lobby slot of the player this bot plays
   * @param name  the player's lobby name (the fake's, or the name of the client that left)
   * @param opts  { takeover: true } for a base inherited from a real player
   */
  constructor(room, slot, name, opts = {}) {
    this.room = room;
    this.slot = slot;
    this.name = name;
    this.takeover = opts.takeover === true;
    // a taken-over base keeps the player's name on every screen; its chat lines say who speaks
    this.chatName = this.takeover ? `AI ${name}` : name;
    this.reset();
  }

  get cfg() {
    return this.room.config;
  }

  get log() {
    return this.room.log;
  }

  reset() {
    this.active = false;
    this.player = -1;
    this.rusher = null;
    this.ally = null; // { player, until (engine tick), client, name }
    this.nextThink = 0;
    this.allyTicks = 0;
    this.thinkTicks = 32;
    this.said = 0;
    this.refunds = 0;
    this.deals = 0;
  }

  /** Allied with game player q: the deal, or a mutual alliance the game already has (lobby team, an inherited one). */
  isAlly(q) {
    if (this.ally !== null && this.ally.player === q) return true;
    if (this.room.bots?.arePartners(this, q)) return true;
    const G = this.room.sync.engine;
    return typeof G?.diploGet === 'function' && this.player >= 0 && q !== this.player && G.diploGet(0, this.player, q) !== 0;
  }

  /** Display name of game player q: the real client's lobby name, else the engine's player block. */
  nameOf(q) {
    for (const c of this.room.players()) if (c.gamePlayer === q) return this.room.slots[c.slot]?.name || c.name || `player ${q}`;
    const gs = this.room.sync.engine?.gs;
    if (gs) {
      const a = playerAddr(q) + P.NAME;
      const end = gs.indexOf(0, a);
      const name = gs.toString('latin1', a, Math.min(end < 0 ? a + 17 : end, a + 17)).trim();
      if (name) return name;
    }
    return `player ${q}`;
  }

  // ---- lifecycle ------------------------------------------------------------------------------

  /**
   * Called from Bots after the engine and the game loop have started. `index` 0 is the fake host,
   * which explains the deal; the others only add their names to it.
   */
  onRunning(index = 0) {
    this.reset();
    const cfg = this.cfg;
    if (cfg.MERCENARY_AI === 'off') return;
    const sync = this.room.sync;
    if (!sync.active || !sync.slotToPlayer) {
      if (index === 0) this.log.info('bots idle', { reason: sync.disabledReason ?? `engine ${sync.mode}` });
      return;
    }
    const player = sync.slotToPlayer[this.slot];
    if (!(player >= 0 && player < 8)) {
      this.log.warn('bot idle', { name: this.name, reason: `no game player for slot ${this.slot}` });
      return;
    }
    this.player = player;
    this.active = true;
    this.thinkTicks = Math.max(1, cfg.MERCENARY_THINK_TICKS | 0);
    this.allyTicks = Math.max(1, Math.round((cfg.MERCENARY_ALLY_S * 1000) / cfg.TICK_MS));
    this.nextThink = sync.engineTime + this.thinkTicks + ((index * PHASE_STEP) % this.thinkTicks);
    const G = sync.engine;
    if (this.takeover) {
      // the human's spending was booked but never deducted on our ledger (F44); DISCONNECT does the same
      const a = playerAddr(player) + P.MONEY;
      w32(G.gs, a, i32(G.gs, a) - i32(G.gs, playerAddr(player) + P.SPENT));
    }
    if (cfg.MERCENARY_AI === 'rusher' && G.tables && G.scenario) {
      try {
        this.rusher = new Rusher(G, player, { isAlly: (q) => this.isAlly(q), nameOf: (q) => this.nameOf(q) });
      } catch (err) {
        this.log.warn('bot: rusher failed to start', { name: this.name, err: err.message });
      }
    }
    if (this.takeover) {
      this.say(`${this.name} left the battle. I run this base now: ${ALLY_PRICE} buys my alliance for ${cfg.MERCENARY_ALLY_S} seconds.${this.rusher ? ' And I rush.' : ''}`);
    } else if (index === 0) {
      this.say(`I ally with anyone who pays me ${ALLY_PRICE}: Diplomacy screen, give ${ALLY_PRICE}.`);
      this.say(`The deal: alliance and shared vision both ways for ${cfg.MERCENARY_ALLY_S} seconds, one ally at a time.`);
      if (this.rusher) this.say('And I rush. Guard your base.');
    } else {
      this.say(`Same deal here: ${ALLY_PRICE} buys my alliance for ${cfg.MERCENARY_ALLY_S} seconds.${this.rusher ? ' And I rush too. Pick your side.' : ''}`);
    }
    this.log.info('bot playing', {
      name: this.name,
      slot: this.slot,
      player,
      takeover: this.takeover,
      ai: this.rusher ? cfg.MERCENARY_AI : 'deal only',
      allyTicks: this.allyTicks,
    });
  }

  /** The engine has simulated up to `tick` (SyncCheck.onFrameIssued): timers and the next think. */
  onAdvanced(tick) {
    if (!this.active) return;
    const sync = this.room.sync;
    if (!sync.active) {
      this.stop(sync.disabledReason ?? 'engine stopped');
      return;
    }
    if (this.ally && tick >= this.ally.until) this.endAlliance('the time is up');
    if (!this.rusher || tick < this.nextThink) return;
    while (this.nextThink <= tick) this.nextThink += this.thinkTicks;
    try {
      const r = this.rusher.think(tick);
      for (const c of r.commands) this.room.game.queueCommands(c);
      r.lines.slice(0, MAX_LINES_PER_THINK).forEach((l) => this.sayToAlly(l));
      if (r.lines.length > MAX_LINES_PER_THINK) this.log.debug('bot: lines dropped', { name: this.name, lines: r.lines.slice(MAX_LINES_PER_THINK) });
    } catch (err) {
      // failure isolation (plan §19.3): this bot's rusher stops, the relay and the engine go on
      this.log.warn('bot: think failed, rusher disabled', { name: this.name, err: err.stack ?? String(err), tick });
      this.rusher = null;
      this.say('My officers are confused. I hold what I have.');
    }
  }

  stop(reason) {
    if (!this.active) return;
    this.active = false;
    this.log.info('bot stopped', { reason, ...this.summary() });
    if (this.ally) this.endAlliance('I lost sight of the battle');
    this.say('I lost sight of the battle. My base is idle now.');
  }

  /** A real client left the battle. */
  onClientLeft(client) {
    if (this.active && this.ally && this.ally.client === client) this.endAlliance(`${this.ally.name} left the game`);
  }

  // ---- the deal -------------------------------------------------------------------------------

  /** The four 0x0D commands of an alliance with `q`: both matrices, both directions (F48). */
  relations(q, on) {
    return Buffer.concat([
      build.diplomacy(this.player, q, 0, on),
      build.diplomacy(this.player, q, 1, on),
      build.diplomacy(q, this.player, 0, on),
      build.diplomacy(q, this.player, 1, on),
    ]);
  }
  /** A client sent 0x0F to this bot's player (the relay forwards it in any case, F47): buy or return. */
  onGift(client) {
    if (!this.active) return;
    const giver = client.gamePlayer;
    if (!(giver >= 0 && giver < 8) || giver === this.player) return;
    const tick = this.room.sync.engineTime;
    const name = this.nameOf(giver);
    if (this.ally) {
      const left = Math.max(1, Math.ceil(((this.ally.until - tick) * this.cfg.TICK_MS) / 1000));
      this.refund(giver);
      const who = this.ally.player === giver ? 'you' : this.nameOf(this.ally.player);
      this.say(`${name}, I am allied with ${who} for ${left} more seconds. Your ${ALLY_PRICE} goes back.`, 1 << giver);
      this.log.info('bot: payment returned', { name: this.name, from: giver, slot: client.slot, ally: this.ally.player, tick });
      return;
    }
    this.ally = { player: giver, until: tick + this.allyTicks, client, name };
    this.deals++;
    this.room.game.queueCommands(this.relations(giver, 1));
    this.say(`${name} paid ${ALLY_PRICE}. We are allies for ${this.cfg.MERCENARY_ALLY_S} seconds, both ways, and we share our eyes.`, 1 << giver);
    this.log.info('bot: alliance', { name: this.name, with: giver, slot: client.slot, until: this.ally.until, tick });
  }

  /** 0x0F back to the payer; the bot's ledger pays for it (the gift itself lands with the frame). */
  refund(giver) {
    this.refunds++;
    this.room.game.queueCommands(build.bonus(giver));
    const gs = this.room.sync.engine?.gs;
    if (gs) {
      const a = playerAddr(this.player) + P.MONEY;
      w32(gs, a, i32(gs, a) - ALLY_PRICE);
    }
  }

  endAlliance(why) {
    const a = this.ally;
    if (!a) return;
    this.ally = null;
    this.room.game.queueCommands(this.relations(a.player, 0));
    this.say(`The alliance with ${a.name} is over: ${why}. ${ALLY_PRICE} buys the next one.`, 1 << a.player);
    this.log.info('bot: alliance ended', { name: this.name, with: a.player, why });
  }

  // ---- chat -----------------------------------------------------------------------------------

  /**
   * One battlefield chat line from this bot in the next sync frame. `mask` = the players who see
   * it (bit i = game player i, the client's own filter); 0xFF = everybody, the default for offers.
   */
  say(text, mask = 0xff) {
    if (this.player < 0 || (mask & 0xff) === 0) return;
    this.said++;
    this.room.game.queueCommands(build.chat(this.player, mask & 0xff, sanitizeText(`${this.chatName}: ${text}`)));
  }

  /** An action line: only the current ally hears it; without an ally it stays in the debug log. */
  sayToAlly(text) {
    if (this.ally === null) {
      this.log.debug('bot: unheard', { name: this.name, text });
      return;
    }
    this.say(text, 1 << this.ally.player);
  }

  summary() {
    return {
      name: this.name,
      slot: this.slot,
      player: this.player,
      takeover: this.takeover,
      ally: this.ally?.player ?? -1,
      deals: this.deals,
      refunds: this.refunds,
      said: this.said,
      rusher: this.rusher?.summary() ?? null,
    };
  }
}

// The fake host under its old name, for callers that only know about the Mercenary.
export { AiPlayer as Mercenary };

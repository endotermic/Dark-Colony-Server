// The game's own computer player, Krusty, as a server bot (19 Sep 2026, maintainer request: "the
// main AI bot, reverse engineered, built into the server"; plan §19.2, §19.10).
//
// A KrustyBot drives the bit-exact port of krusty.c (src/engine/krusty.js) in BOT mode for one
// fake human player against the room's engine (plan §18): same zone table, influence map, census,
// production goals, worker/defend/attack/scout tasks and group mover as the original, with the two
// substitutions of DC16_AI.md §17.2 - a private RNG (the engine's is part of the lockstep) and
// commands collected for the next sync frame instead of executed on the spot. The only engine
// state it writes is its player's money (F44: the sender deducts, the handlers only book) and the
// AI-private object bytes (+0x11, +0xCC..+0xD4), none of which is in the checksum. It never calls
// G.rand() and never touches the checksum history.
//
// Interface: think(tick) -> { commands: Buffer[], lines: string[] } (mercenary.js AiPlayer drives it).

import * as Krusty from './engine/krusty.js';

export class KrustyBot {
  /**
   * @param G       the engine Game
   * @param player  game player index of the fake human
   * @param opts    { seed: RNG seed (0..255 used), thinkTicks }
   */
  constructor(G, player, opts = {}) {
    this.G = G;
    this.p = player;
    this.kai = Krusty.krustyAlloc(G, player);
    this.randIndex = (opts.seed ?? (Math.random() * 256) | 0) & 0xff;
    this.table = G.randTable;
    this.thinks = 0;
    this.commands = 0;
    this.groups = 0;
    this.asserts = [];
    this.lastAssert = null;
  }

  /** The game's table RNG (0x4120F0) on a private index: same value domain, no effect on the lockstep. */
  rand() {
    this.randIndex = (this.randIndex + 1) & 0xff;
    return this.table[this.randIndex];
  }

  /** One think (krusty_think 0x44BE64) at engine tick `tick`. */
  think(tick) {
    const out = { commands: [], lines: [] };
    const ctx = {
      G: this.G,
      p: this.p,
      kai: this.kai,
      exact: false,
      fixes: true, // the bot plays the repaired Krusty (krusty.js FIXES); exact mode keeps the original's bugs
      rand: () => this.rand(),
      emit: (cmds) => {
        out.commands.push(Buffer.concat(cmds));
        this.groups++;
        this.commands += cmds.length;
      },
      say: (text) => out.lines.push(text),
      assert: (cond, msg) => {
        if (cond) return;
        // the original would end the game here; the bot notes it and plays on
        this.lastAssert = { tick, msg };
        if (this.asserts.length < 20) this.asserts.push(this.lastAssert);
      },
    };
    Krusty.krustyThink(ctx);
    this.thinks++;
    return out;
  }

  summary() {
    const st = Krusty.summarize(this.G, this.kai);
    return {
      ai: 'krusty',
      thinks: this.thinks,
      commands: this.commands,
      groups: this.groups,
      zones: st.zones,
      tasks: st.tasks.map((t) => ({ t: t.t, have: t.have.join(''), groups: t.groups.length })),
      asserts: this.asserts.length,
      lastAssert: this.lastAssert,
    };
  }
}

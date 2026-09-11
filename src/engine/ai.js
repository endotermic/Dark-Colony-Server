// Computer players (ai.c 0x41AE38 / 0x41AD30, krusty_*.c) — NOT PORTED YET.
//
// In a relay game every occupied slot is a human (fakes included, maintainer decision), so the AI
// only runs after a DISCONNECT (the lost player's base is handed to AI type 3, "Krusty"). Until
// the AI is ported the engine's state diverges from the clients' from the first AI think after a
// disconnect (32 ticks later at the latest); the server stops sending checksums for that game as
// soon as an AI player exists (see src/synccheck.js).

import { i32, playerAddr, P } from './mem.js';

/** ai_turn 0x41AE38: called from game_tick in local-command mode. */
export function aiTurn(G) {
  // TODO(exact): port ai.c / krusty. For now only report whether an AI player exists.
  return false;
}

/** Any player controlled by the computer (player +0x24 != 0)? */
export function hasAiPlayer(G) {
  for (let p = 0; p < 8; p++) if (i32(G.gs, playerAddr(p) + P.AI_TYPE) !== 0 && G.scenario?.occupied?.[p]) return true;
  return false;
}

/** ai_init 0x41AC80: allocate the AI game state (start tick, round-robin counter). */
export function aiInit(G) {
  G.ai = { startTick: 0, roundRobin: 0 };
}

/** The `ai` / `aimsg` trigger actions (0x43D930, 0x44BF78). */
export function triggerAi(G, player, type) {
  // TODO(exact)
  void G;
  void player;
  void type;
}

export function triggerAiMsg(G, player, id, value) {
  // TODO(exact)
  void G;
  void player;
  void id;
  void value;
}

/**
 * 0x41ACD8: for every player whose AI type is >= 1 call the personality's init callback
 * (0x489488[type-1] +0xC) and clear the krusty state pointer (player +0x2C); then gs+0xB94 = 0.
 * The personality callbacks are part of the unported ai.c; with humans only nothing happens.
 */
export function initPlayers(G) {
  for (let p = 0; p < 8; p++) {
    const pa = playerAddr(p);
    if (i32(G.gs, pa + P.AI_TYPE) - 1 >= 0) {
      // TODO(exact): personality init 0x489488[type-1]->init(gs, p)
      if (i32(G.gs, pa + P.KRUSTY) !== 0) G.gs.writeInt32LE(0, pa + P.KRUSTY);
    }
  }
  G.gs.writeInt32LE(0, 0xb94); // GS.AI_STATE
}

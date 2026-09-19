// ai.c of Classic dc16.exe (0x41AC80..0x41AF97): the computer players' schedule and the weighted
// choice, driving the Krusty port (krusty.js) in EXACT mode - inside game_tick after record(t),
// with the game RNG and immediate command execution (local-command mode, DC16_AI.md §2, §3, §17.1).
//
// PORT NOTES (19 Sep 2026, from dc16.asm)
//  * ai_turn 0x41AE38: `test byte [gs+0x94C],3; jne ret` - only ticks with TICK & 3 == 0. TICK == 4
//    (the literal value, 0x41AE56 `cmp edx,4`): every player with ai_type != 0 thinks, in slot order,
//    the round-robin counter untouched. Otherwise rr = (rr + 1) % 8 (idiv) and that ONE slot thinks
//    if its ai_type != 0 (humans waste the call).
//  * ai_think 0x41AD30: t = ai_type - 1, assert t < 4 ("ai < MAX_AI"); over the personality's pairs
//    until a NULL weight function: w = weight(gs, p); r = rand() (drawn for EVERY pair); the pair
//    is chosen iff w > r * (cum + w) * C in double arithmetic (fcompp/jbe: skip when w <= product),
//    C = [0x48385C] = 0x3F00002000400080 = 1/32767 exactly (checked: the double 1/32767 has these
//    bits); cum += w. After the loop the chosen pair's action runs.
//  * Personalities 0x489488: type 1 (five pairs) and type 2 (two pairs) are campaign scripts that use
//    the C run-time rand() (not lock-stepped, DC16_AI.md §4) and are only ever set by the trigger
//    `ai`; they are stubs here (weights 0, so the rand() draws happen and no action runs) with a
//    one-time warning. Type 3 = Krusty (weight 1 -> always chosen: no table entry is 32767). Type 4
//    (closed slots) = weight 0, empty action: one rand() per think and nothing else.
//  * ai_init 0x41AC80 is called by run_game before a save is loaded: P.KRUSTY = 0 for all players,
//    the 8-byte AI state { start_tick = TICK, rr = 0 }. ai_destroy 0x41ACD8 at game end. The state
//    lives in G.ai; P.KRUSTY holds 1 as "allocated" marker (a pool pointer in the original; not in
//    the checksum).
//  * The trigger actions `ai` (0x43D930) and `aimsg` (0x43D94E -> 0x41AF74 -> personality->message)
//    are exported for renat.js.
//  * Verification status: the port follows DC16_AI.md instruction for instruction where the doc is
//    explicit, but it has NOT yet been checked against a recording of a real game in which the AI
//    played (a DISCONNECT takeover or computer lobby slots). synccheck.js therefore still stops
//    `send` mode when an AI player appears unless AI_SEND is set; `shadow` mode compares.

import { GS, P, i32, w32, playerAddr } from './mem.js';
import * as Krusty from './krusty.js';

/** [0x48385C]: 1/32767 as the exe stores it (0x3F00002000400080). */
export const CHOICE_C = 1 / 32767;

const noop = () => {};
const warnedStub = new Set();
function stubWeight(type) {
  return (G) => {
    if (!warnedStub.has(type)) {
      warnedStub.add(type);
      G.log?.warn?.('ai: campaign personality not ported', { type });
    }
    return 0; // TODO(exact): types 1/2 use the unseeded CRT rand(); campaign only
  };
}

/** The personality table 0x489488: {weight, action} pairs per ai_type 1..4. */
export const PERSONALITIES = [
  [1, 2, 3, 4, 5].map(() => ({ weight: stubWeight(1), action: noop })),
  [1, 2].map(() => ({ weight: stubWeight(2), action: noop })),
  [{ weight: () => 1, action: krustyExact }],
  [{ weight: () => 0, action: noop }],
];

const aiType = (G, p) => i32(G.gs, playerAddr(p) + P.AI_TYPE);

/** ai_init 0x41AC80: P.KRUSTY = 0 for all players; the AI state { start_tick, rr }. */
export function aiInit(G) {
  for (let p = 0; p < 8; p++) w32(G.gs, playerAddr(p) + P.KRUSTY, 0);
  G.ai = { startTick: i32(G.gs, GS.TICK), rr: 0, krusty: new Array(8).fill(null) };
}

function state(G) {
  if (!G.ai) aiInit(G);
  return G.ai;
}

/** ai_turn 0x41AE38: called from game_tick in local-command mode. Returns true when somebody thought. */
export function aiTurn(G) {
  const tick = i32(G.gs, GS.TICK);
  if (tick & 3) return false;
  const st = state(G);
  let thought = false;
  if (tick === 4) {
    for (let p = 0; p < 8; p++) {
      if (aiType(G, p) !== 0) {
        aiThink(G, p);
        thought = true;
      }
    }
    return thought;
  }
  st.rr = (st.rr + 1) % 8;
  if (aiType(G, st.rr) !== 0) {
    aiThink(G, st.rr);
    thought = true;
  }
  return thought;
}

/** ai_think 0x41AD30(gs, p): the weighted choice, one rand() per pair, then the chosen action. */
export function aiThink(G, p) {
  const t = aiType(G, p) - 1;
  G.assert(t >= 0 && t < 4, 'ai < MAX_AI');
  const pairs = PERSONALITIES[t];
  if (!pairs) return;
  let cum = 0;
  let chosen = -1;
  for (let i = 0; i < pairs.length; i++) {
    const w = pairs[i].weight(G, p);
    const r = G.rand();
    if (w > r * (cum + w) * CHOICE_C) chosen = i;
    cum += w;
  }
  if (chosen >= 0) pairs[chosen].action(G, p);
}

/** The Krusty state of player p, allocated on first use (krusty_alloc 0x44BD50 from the think / aimsg / load). */
export function krustyState(G, p) {
  const st = state(G);
  if (!st.krusty[p]) {
    st.krusty[p] = Krusty.krustyAlloc(G, p);
    w32(G.gs, playerAddr(p) + P.KRUSTY, 1);
  }
  return st.krusty[p];
}

/** The exact-mode context: game RNG, immediate execution through the command handlers. */
export function exactContext(G, p) {
  return {
    G,
    p,
    kai: krustyState(G, p),
    exact: true,
    rand: () => G.rand(),
    emit: (cmds) => {
      for (const c of cmds) G.applyCommand(c); // execute_commands 0x41E06C: one handler call per command
    },
    say: noop,
    assert: (cond, msg) => G.assert(cond, msg),
  };
}

/** Krusty's think (0x44BE64) in exact mode. */
export function krustyExact(G, p) {
  Krusty.krustyThink(exactContext(G, p));
}

/** Any player controlled by the computer (player +0x24 != 0)? */
export function hasAiPlayer(G) {
  for (let p = 0; p < 8; p++) if (aiType(G, p) !== 0 && G.scenario?.occupied?.[p]) return true;
  return false;
}

/** Trigger action `ai <player> <type>` (0x43D930): P.AI_TYPE = type, nothing else. */
export function triggerAi(G, player, type) {
  if (!(player >= 0 && player < 8)) return;
  w32(G.gs, playerAddr(player) + P.AI_TYPE, type | 0);
}

/** Trigger action `aimsg <player> <n> <words...>` (0x43D94E -> ai_message 0x41AF74): Krusty only. */
export function triggerAiMsg(G, player, words) {
  if (!(player >= 0 && player < 8)) return;
  if (aiType(G, player) !== 3) return; // the other personalities' message callback is a no-op
  Krusty.krustyAimsg(exactContext(G, player), Array.isArray(words) ? words : [words]);
}

/**
 * ai_destroy 0x41ACD8 (game end / load failure): for every AI player the personality's destroy
 * callback (no-ops for all four types), P.KRUSTY = 0; gs+0xB94 = 0. Never called at game start.
 */
export function initPlayers(G) {
  for (let p = 0; p < 8; p++) {
    const pa = playerAddr(p);
    if (aiType(G, p) - 1 >= 0 && i32(G.gs, pa + P.KRUSTY) !== 0) w32(G.gs, pa + P.KRUSTY, 0);
  }
  G.gs.writeInt32LE(0, GS.AI_STATE);
  G.ai = null;
}

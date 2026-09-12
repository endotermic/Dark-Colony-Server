# Dark Colony `dc16.exe`: the computer player ("Krusty")

Reverse-engineering notes on the AI of Classic `dc16.exe` (1997), written 12 Sep 2026 from the
`dumpbin -ALL -DISASM` disassembly (`dc16.asm`, virtual addresses, image base `0x400000`; the AI code
is untouched by the resolution, cursor, pool and speed patches). Purpose: (1) describe the AI exactly
enough that the relay server can run the same logic for its fake players ("alive bots",
`RELAY_SERVER_PLAN.md` §19) and (2) give a later bit-exact port into `src/engine/ai.js` its
specification. Everything not marked *(inferred)* was read instruction by instruction. `§17` of
`DC16_BATTLE_ENGINE.md` was the first, partly wrong sketch of this material; §21 lists what it got
wrong.

Conventions: Watcom register calls `(eax, edx, ebx, ecx, then stack)`. `gs` = game state
(`src/engine/mem.js` offsets); `obj(i) = gs+0x7D48+i·0xDC`; `player(p) = gs+0xB98+p·0xE34`
(`P.MONEY +0x14`, `P.RACE +0x20`, `P.AI_TYPE +0x24`, `P.KRUSTY +0x2C`, `P.CITY_X/Z +0x30/+0x34`,
`P.NET_ID +0x108`, `P.VISION +0xE2C`); `map = [gs+0x46F4C]`; `OT(t) = 0x50FCF8 + t·0x118`
(object types), `WT(w) = 0x50E678 + w·0x48` (weapons), `MB[r][c]` = magic-bullet matrix
(`[0x518B30]` row pointers, int16 entries, 8.8 fixed point). Positions are 1/256 tile (`tile = x >> 8`).
`rand()` = the game's table RNG `0x4120F0` unless stated otherwise.

---

## 1. Overview

* Every computer player is a **personality** (`ai_type` 1..4 in `P.AI_TYPE`; 0 = human). Type 3 is
  the real AI, "Krusty" (`krusty.c`, `krusty_general.c`, `krusty_attack.c`, `krusty_defend.c`,
  `krusty_scout.c`, `krusty_army.c`). Types 1 and 2 are two simple scripted personalities used by
  campaign triggers; type 4 does nothing (closed lobby slots).
* Multiplayer lobby types map to `ai_type`: human → 0, closed → 4, computer easy/hard → **3 for
  both** (they differ only in the harvesting multiplier `player+0x19BC`, `DC16_BATTLE_ENGINE.md`
  §15.2). A human who disconnects in a network game becomes type 3 (§19).
* The AI runs **inside `game_tick` on every machine**, deterministically, and expresses every decision
  as ordinary in-game commands (`0x09`, `0x0A`, `0x07`+`0x05`, `0x05 0x0D`, and for type 2 `0x0B`+`0x05
  0x0E`) that are executed on the spot through the same handler table as network commands (§3). It
  never sends anything over the network.
* Krusty's world model is the **zone graph**: a zone is a path family of the `.PTH` file
  (`DC16_MAP_FILES.md`, `DC16_BATTLE_ENGINE.md` §14.3), the routing matrix `next[a][b]` gives the
  next zone on the way from `a` to `b`, and an adjacency list per zone is derived at map load. On the
  zone graph it keeps an **influence map** (who owns each zone with how much anti-air, ground and air
  strength), a memory of enemy objects it has seen, four **major tasks** (workers → vents, defend,
  attack, scouting) each owning up to sixteen **unit groups** ("minor tasks"), and a list of 18
  **production goals** run as a priority chain.

---

## 2. Framework (`ai.c`, `0x41AC80..0x41AF97`)

| VA | function | what it does |
|---|---|---|
| `0x41AC80` | `ai_init(gs)` | called from `run_game` `0x4017A9` before a save is loaded: `P.KRUSTY = 0` for all 8 players; allocates the 8-byte **AI game state** `gs+0xB94 = {i32 start_tick = TICK, i32 rr = 0}` from the local pool (`0x40C09C`, name "AI Game State") |
| `0x41ACD8` | `ai_destroy(gs)` | game end (`0x401B54`) and load failure (`0x4017E2`): for every player with `ai_type ≥ 1` call `personality->destroy(gs, p)` and clear `P.KRUSTY`; `gs+0xB94 = 0`. **Never called at game start** (the engine port's comment "personality init" is wrong) |
| `0x41AE38` | `ai_turn(gs)` | from `game_tick` `0x419FB6` only. `if (TICK & 3) return`. **Tick 4**: `ai_think` for every player with `ai_type ≠ 0`, in slot order, `rr` untouched. Otherwise `rr = (rr + 1) % 8` and `ai_think(gs, rr)` **if that slot is an AI** — one *slot* per call, human slots waste the call. So player `p ≥ 1` thinks at ticks `4 + 4p + 32k`, player 0 at `36 + 32k`, everybody once more at tick 4; each AI thinks every 32 ticks at a fixed phase |
| `0x41AD30` | `ai_think(gs, p)` | `t = ai_type − 1`, assert `t < 4` ("ai < MAX_AI"). Over the personality's NULL-terminated `{weight_fn, action_fn}` pairs: `w = weight(gs, p)`; **`r = rand()` once per pair, unconditionally**; the pair becomes the choice iff `w > r · (cum + w) · C` in double arithmetic with `C = [0x48385C] = 0x3F00002000400080 = 1/32767` exactly (strict `>`); `cum += w`. After the loop the chosen pair's `action(gs, p)` runs (nothing if none was chosen). Reservoir sampling giving `w_i / Σw`; the edge case `r = 32767` (never chosen) cannot occur: the 256-entry table `0x488F20` holds no 32767 |
| `0x41AEB4` / `0x41AF0C` | `ai_save` / `ai_load` | `fputc(rr)`; per AI player `personality->save(kai, file)` / `load(gs, p, file) → bool` (a 0 aborts the load). `start_tick` is not saved |
| `0x41AF74` | `ai_message(gs, p, n, words)` | the `aimsg` trigger → `personality->message` (§18) |

**Personality table** `0x489488 → i32[4]`, records `{pairs, save, load, destroy, message}`:

| type | record | pairs (weight → action) | save / load / destroy / message |
|---|---|---|---|
| 1 | `0x48B3C4` | `0x455E30→0x455E70`, `0x44AEB0→0x44AF10`, `0x44B004→0x44B058`, `0x455EC0→0x455F3C`, `0x44B2C0→0x44B360` | no-ops `0x456118 / 0x456120 (returns 1) / 0x456128 / 0x456130` |
| 2 | `0x48B3F0` | `0x455EC0→0x455F3C`, `0x44B500→0x44B50C` | same no-ops |
| 3 | `0x48B444` | `0x44BC80 (returns 1) → 0x44BE64` (Krusty think) | `0x44C750 / 0x44CF48 / 0x44D654 (no-op) / 0x44BF78` |
| 4 | `0x48B468` | `0x44D660 (returns 0) → 0x44D668` | same no-ops as 1 |

`rand()` consumption of `ai_think` itself: type 1 five calls, type 2 two, type 3 one, type 4 one
(its weight is 0, so its action never runs). `0x44D660` and `0x421630` are mis-decoded by dumpbin
(misaligned after padding); read them from the raw bytes.

---

## 3. How AI commands reach the simulation, and when (`hack.c`, `mobiles.c`)

* Every command builder ends in **`send_command 0x421648(buf, len, client)`** (`hack.c`; asserts
  `len <= DC_MESSAGE_MAX` = 0x400 and a 12-bit length). `buf[0..1]` receive the frame header. Then:
  **if the global byte `[0x4AF090]` is set ("local command mode")** the payload is executed
  immediately, in the caller's stack frame, by `execute_commands 0x41E06C(gs = [0x48950C], buf+2,
  len−2)` through the handler table `0x48949C` (28 entries, id 0 and ≥ 0x1C invalid); the `client` is
  ignored, no sequence nibble, nothing queued. Otherwise the sequence nibble is added and
  `client->send (+0x5C)` transmits the frame to the server. `[0x48950C]` is set once by `0x421630`
  from the game-state initialiser (`0x41BB11`); the AI's `client` argument is `[gs+0x950]`, the local
  client record.
* `game_tick 0x419978` ends with (`0x419F1B..0x419FC5`): choose the checksum sender and send `0x08`
  → **`0x42163C(1)`; `ai_turn(gs)`; `0x42163C(0)`** → timing statistics. There is no flag test around
  the call: local mode is simply on for the duration of `ai_turn`. Consequently, within tick `t`:
  1. the pacing loop executes the sync frame `UNTIL(a, t)` (network commands) *before* `game_tick(t)`;
  2. `game_tick(t)`: counters, vision, triggers (every 8 ticks, where `ai`/`aimsg` actions fire),
     income, object loop, missiles, `record(t)` (checksum);
  3. **AI commands of tick `t`** (ticks with `t % 4 == 0`) are applied here, *after* `record(t)`;
  4. tick `t+1`: its sync frame, then `game_tick(t+1)`.
  AI-generated commands therefore take effect from the object loop of `t+1`, like the network commands
  of frame `t+1` but applied *before* them, and they are part of checksum `t+1`, not `t`. A bit-exact
  port must run the AI exactly there. `0x41E0D8` (held-frame executor) is only for network frames.
* **Money model.** The `0x09`/`0x0A`/`0x0C` handlers book `P.SPENT` and never deduct `P.MONEY`
  (`DC16_BATTLE_ENGINE.md` §15.2). The *sender* deducts: the build menu locally (`0x43332F`), Krusty's
  goal actions on every machine identically (§10). Money is not in the checksum. A player that no
  machine controls (the relay's fake humans) has money nowhere but in the server engine.

Command builders the AI uses (register order; all through `0x421648` unless noted):

| VA | call | frame |
|---|---|---|
| `0x40C50C` | `(client, player, slot, level)` | `[0x09 slot level player][0]` build building |
| `0x40C538` | `(client, player, type, count)` | `[0x0A type player count][0]` build units |
| `0x40C564` | `(client, which, type, level, player)` | `[0x0C which type level player][0]` upgrade (not used by the AI) |
| `0x40C7D4` | `(client, objs*, nobjs, wps*, [stack] nwp, [stack] order)` | `[0x07 nwp i16 nobjs (i16 x, i16 z)×nwp (i16 obj)×nobjs][0x05 obj order]×nobjs[0]` — waypoint list plus one order per object, one frame |
| `0x40C978` | `(obj, target, cursor**)` | appends `[0x0B obj target][0x05 obj 0x0E]` to a caller buffer (type-2 personality only) |
| inline | `0x46BAxx` | `[0x05 obj 0x0D][0]` deploy (tower builders) |

Order bytes: `2` move, `7` assault move (`DC16_BATTLE_ENGINE.md` §4.2), `0x0D` deploy, `0x0E` attack
target (with `0x0B`). Waypoints are absolute 1/256-tile positions; Krusty uses zone centres as
`tile << 8` (no half-tile offset) and vent positions as the vent object's raw `x, z`.

---

## 4. The simple personalities (types 1, 2, 4)

Type 1 is a base builder and rusher, type 2 an attack-only script; both are only ever set by the
campaign trigger `ai <player> <type>` (§18). Their random choices partly use the **C run-time `rand()`
`0x44E177`** (LCG `seed·0x343FD + 0x269EC3`, `>> 16 & 0x7FFF`, never seeded, shared with `sound.c`
`0x431DBA`/`0x43212C` and UI code) — a stream that is *not* lock-stepped between machines, so these
types are unsafe in a network game (they are never used there).

| weight → action | behaviour |
|---|---|
| `0x455E30 → 0x455E70` | weight 1 if `dep_check_building(gs, p, item) == 1` for any DEPEND item 0..109; action sends `0x09` for the first such item. **No money check** (the handler does not check either) |
| `0x44AEB0 → 0x44AF10` | weight 1 if a troop item is buildable (`dep_check_troop == 1`) and `money / cost > 0`; action: reservoir over the buildable affordable items with CRT `rand()`, **keyed by the item index, not the candidate count** (`1 > r·(item+1)/32767`) — biased; then `count = max(1, (money / candidates) / cost)`; `0x0A type player count`. Money not deducted |
| `0x44B004 → 0x44B058` | weight 1 if `own_unit` (§5) matches any object; action: collect all own units; **skip everything if the own HQ object `obj(15p)` is dead**; pick a random human player (`ai_type == 0`, CRT `rand()`, uniform); `R = W + 0.05·H` with `W, H = [map+0x9A4B8/+0x9A4BC]` = map size × 256; up to 1000 tries: `dx, dz = trunc(rand()·2R/32767 − R)` (game RNG), accept a point inside the map with `dx² + dz² > R²/4` — a point farther than `R/2` from the HQ, which rarely exists, so usually all tries fail; success → `0x07` with **two waypoints** `(point, HQ)`, order 7; failure → one waypoint (the HQ), order 7 |
| `0x455EC0 → 0x455F3C` | weight 1 if an own flying unit is alive; action: reservoir-pick a flyer (CRT `rand()`), then `x = trunc(rand()·W/32767)`, `z = trunc(rand()·H/32767)` (game RNG, 1/256-tile units, FPU truncation via `0x42B5B0`) and `0x07`+`0x05` order 7 |
| `0x44B2C0 → 0x44B360` | weight 1 if a vent (type 0x28) exists and an own object of type 6/0x0E (worker) with **`life ≠ 1`** exists (an inverted test in the original: alive units have `life == 1`, so this matches free/corpse slots); action: reservoir over vents (every vent participates, a free vent resets the counter) and over "workers", CRT `rand()`; `0x07`+`0x05` order 2 to the vent position |
| `0x44B500 → 0x44B50C` (type 2) | constant weight 1; action: own units (`own_unit`) vs targets (`enemy_target` §5, no liveness check); deal targets without replacement by a partial Fisher–Yates with the game RNG, restarting the deck when exhausted; one frame of `[0x0B own target][0x05 own 0x0E]` per own unit |
| `0x44D660 → 0x44D668` (type 4) | weight 0, action empty |

Helpers (`krusty_general.c` neighbourhood): `own_unit 0x456078(obj*, p)` = `life == 1 && team == p &&
!OT.flying(+0x60) && OT.mobile(+0x0C) && OT.weapon0(+0x18) != −1`; `enemy_target 0x4560CC(gs, obj, p)` =
`team != p && player(team).ai_type == 0` (a *human* enemy; reads outside the player array for teams
8/9); `unit_class 0x456150(type)`: types 0..15 → `type mod 8` (0 infantry, 1 tower builder, 2
mech/scythe, 3 artillery, 4 cyborg/psy-raider, 5 scout, 6 worker, 7 carry-all), 49/50 (healers) → 7,
41/42 (deployed towers) → 1, else 8.

---

## 5. Krusty's state block (`kai`, 0x6C40 bytes, `P.KRUSTY`)

Allocated by `krusty_alloc 0x44BD50(gs, p)` (pool name "Krusty AI") on the first think, by `aimsg`
or by the loader. Layout (verified from the code's address arithmetic and from `krusty_save`
`0x44C750` / `krusty_load 0x44CF48`, which write the sections `KRUSTY_SAVE_TOP/ZONE/GHOST/MTASK/
TASK/DEFENSE/GOAL`):

| offset | size | content |
|---|---|---|
| `+0x0000` | 1 | **first-run flag** (1 after alloc → the census runs once before the first influence map; 0 after a load) |
| `+0x0000` | 256 × 18 | **zone table**, record `z` at `kai + 18z` (zone = path family 1..255; zone 0's record overlaps the flag and is never a real zone; the C struct evidently starts at `+2`, which is how the save code addresses it). Fields relative to `kai + 18z`: `+2` u8 centre tile x, `+3` u8 centre tile z, `+4` i8 **anti-air owner** (−1 none), `+6` u16 anti-air strength, `+8` i8 **ground owner**, `+0xA` u16 ground strength, `+0xC` i8 **air owner**, `+0xD` u8 **hop distance from the home zone** (0xFF unreachable), `+0xE` u16 air strength, `+0x10` i16 **enemy building count**, `+0x12` u8 **flags**: bit 0 contested, bit 1 "attack here" (`aimsg 9/10`), bit 2 "unknown/marked zone" (`aimsg 11/12`, cleared when the AI sees the centre; presets the building count to 1). Bytes `+5, +9, +0x11` padding |
| `+0x1200` | 2 | padding |
| `+0x1202` | 800 × 4 | **last-seen memory** per object: `+0` u8 tile x, `+1` u8 tile z, `+2` i8 type (−1 none), `+3` u8 team (§8). `krusty_alloc` writes 0xFF into byte `+2` of entries 1..800 (`kai+0x1204+4i`); nothing else reads `kai+0x1200`. (The earlier doc's "task assignment bytes" do not exist: membership is the `obj+0xD2 == −2` test) |
| `+0x1E84` | 4 × 0x12FC | **major tasks** `M(t) = kai + 0x1E84 + 0x12FC·t` (0x1E84, 0x3180, 0x447C, 0x5778): `+0..+0xC` four i32 header words (`+8` = an accumulator only task 3 writes, no reader found); `+0x10` **16 minor tasks × 0x12C**; `+0x12D0` **`i16 have[9]`** = units currently held per class (task 0's is the well-known `kai+0x3154`; this is a *count*, not a demand); `+0x12E4..+0x12F8` six callbacks (§7), not saved |
| minor `m` | 0x12C at `M + 0x10 + 0x12C·m` | `+1` u8 active; `+8` i32 **current zone** of the group; `+0xC` i32 **destination zone**; `+0x10` u8 **state** (attack task: 0 en route, 1 unused, 2 needs a target, 3 parked in a defend zone); `+0x12` i16 **route step**; `+0x14` u8[256] **route** (zone ids from the start zone to the destination); `+0x116` i16 **list head**, `+0x118` i16 **list tail** (object indices, −1 empty); `+0x11A` i16[9] **count per class** |
| `+0x6A74` | 16 × 2 | **defend-zone list** (`aimsg 6/7`): `+0` u8 in use, `+1` u8 zone |
| `+0x6A94` | 32 × 12 | **goals** `{i32 check_fn, i32 action_fn, i32 param}`; slots 0..17 copied from `0x499158` (§10), `MAX_GOALS = 32` |
| `+0x6C14` | i32 | **split** (0xC0 = 75 %, `aimsg 0` as `v·256/100`) |
| `+0x6C18..+0x6C30` | 7 × i32 | class weights of the unit-building goal: infantry 1, mech 1, artillery 2, cyborg 4, scout 2, carry-all/healer 4, tower builder 2 (`aimsg 1..5` set the first five) |
| `+0x6C34` | i32 | **attack ratio** (`aimsg 13`, `v·256/100`); **never initialised by `krusty_alloc`** (pool memory, presumably 0) |
| `+0x6C38` | u8[8] | **`see_thru[team]`**: the AI may use team `t`'s vision. Own team = 1 at alloc, `aimsg 14` sets others (allies). Not "enemy flags" |

Object fields used privately by the AI (all objects, not in the checksum's covered set except through
their effects): `obj+0x11` u8 **AI target zone** the unit was last ordered to (0 none); `obj+0xCC` u8
**`ai_status`**: 0 none, 1 arrived at the group zone, 2 travelling with the group, 4 scouting a random
point, 5 approaching a bombing zone, **6 = `OBJ_BOMBING`**; `obj+0xCD/+0xCE` tile at the previous
think, `obj+0xCF` **stuck counter** (thinks on the same tile; the mover saturates it at 60);
`obj+0xD2` i16 **next**, `obj+0xD4` i16 **prev** in a group list, −1 = list end, **−2 = in no group**
(the initial value from `init_object`); `obj+0xC9` is tested by the bomber as a re-target request but
nothing sets it at run time (dead); `obj+0x39` (bottom state of the state stack) `== 1` means "idle".
`+0xD0` is the berserk counter, not the AI status.

---

## 6. Initialisation (`krusty_alloc 0x44BD50`)

1. Allocate `kai`; memory table types = 0xFF; `see_thru[·] = 0`, `see_thru[p] = 1`.
2. `krusty_init_zones 0x456EF4(gs, p, kai)` (`krusty_general.c`): every zone `+0xD = 0xFF`, `+0x12 = 0`;
   **home tile** = `P.CITY_X/CITY_Z`, or `P+0x38/+0x3C` when either is 0, snapped to the nearest
   free ground cell (`0x41B68C`); `start = family(home)`, assert `start_zone!=0`; **hop distances**
   by breadth-first search on the zone graph (`dist[start] = 0`; repeatedly take the open zone with the
   smallest distance, store it in `+0xD`, relax every `next[cur][d]` for `d = 1..254` to `dist+1`;
   zones 0 and 255 never visited); **centres** = per family the mean tile (sums over the whole map),
   snapped to the nearest cell of that family by a growing square scan `r = 0..99` (lexicographically
   first hit in the smallest square), stored in `+2/+3`.
3. Task inits: `0x458E54(kai, 2)` attack, `0x459574(kai, 1)` defend, `0x459B68(kai, 0)` workers,
   `0x45A460(kai, 3)` scouting (§7).
4. Tunables `+0x6C14 = 0xC0`, `+0x6C18.. = 1, 1, 2, 4, 2, 4, 2`; first-run flag = 1;
   `init_goals 0x4565D4` copies the 18 goals from `0x499158` to `+0x6A94`.

Map-side data every part relies on (`map = [gs+0x46F4C]`): `[map+0x1404]` row pointers to 24-byte
path cells, family byte at `cell+0xC` (`family(x, z)`); `[map+0x804]` row pointers to the int32 ground
layer, bits 23..30 = "seen by player q" (`0x40000000 >> q`, `grid.js teamBit`), low 10 bits the
occupying object (0x3FF empty); `map+0x884A8 + 256a + b` = routing matrix `next[a][b]`;
`map+0x984A8 + 32z` = adjacency list of zone `z` (`[0]` count, `[1..]` neighbours, derived at load,
`DC16_BATTLE_ENGINE.md` §14.3 `CLIST2`); `map+0x9A4B0/+0x9A4B4` width/height in tiles,
`+0x9A4B8/+0x9A4BC` in 1/256 tiles.

---

## 7. The think (`0x44BE64(gs, p)`, every 32 ticks) and the task callbacks

```
kai = P.KRUSTY or krusty_alloc
if kai.first_run: krusty_census(gs, p, kai) (§9); first_run = 0
krusty_general(gs, p, kai)                       influence map (§8)
krusty_demand(gs, kai, p)                        census + production goals (§9, §10)
for t in 0..3:  M = kai + 0x1E84 + 0x12FC·t
    M.update1(gs, kai, t, M)      +0x12E4   (abs +0x3168 for task 0)
    M.update2(gs, kai, t, M)      +0x12F0   (+0x3174)
    M.plan(gs, kai, t, p)         +0x12E8   (+0x316C)
    M.move(gs, kai, t, p)         +0x12EC   (+0x3170)
```

The other two callbacks are called from the census: `M.recount(gs, kai, t)` (+0x12F4, `+0x3178`)
rebuilds `have[]` and every group's `count[]` from the lists, and `M.take(gs, kai, t, class) → minor`
(+0x12F8, `+0x317C`) picks the group for a unit being handed to the task. Callbacks per task:

| task | role (file) | update1 | update2 | plan | move | recount | take |
|---|---|---|---|---|---|---|---|
| 0 | workers → vents (`krusty_scout.c`) | `0x4575E4` zero `M+8` | `0x44BC00` purge corpses of group 0 | `0x44B950` no-op | `0x4595D0` worker logic (§11) | `0x459AB8` `have[6]` = list length | `0x459B60` → 0 |
| 1 | defend (`krusty_defend.c`) | `0x4575E4` | `0x44BC10` purge all groups | `0x4590BC` (§12) | `0x46BDA4` group mover (§15) | `0x44B6D4` recount by `unit_class` | `0x459370` |
| 2 | attack (`krusty_attack.c`) | `0x4575E4` | `0x44BC10` | `0x458864` (§13) | `0x46BDA4` | `0x44B6D4` | `0x458C5C` |
| 3 | scouting / bombing (`krusty_scout.c`) | `0x459C44` `M+8 += n ? {40,30,20,0}[3]/n = 0 : 1000` (unread) | `0x44BC00` | `0x44B950` | `0x459CA0` (§14) | `0x44B6D4` | `0x45A458` → 0 |

Tasks 1 and 2 are initialised by `task_init_common 0x46C074` (all groups inactive, default callbacks
incl. `take = 0x46C024` which asserts) plus overrides; the defend init activates group 0 (`new_minor`
§15), the attack init activates groups 0 and 1 in state 2. Tasks 0 and 3 use `0x44B8E8` (header
zeroed, group 0 active with an empty list) and have no mover: their units are ordered individually.

Purge (`0x44BA78`): walks a group's list, asserts `next != −2`, prints "Object is dead" and asserts on
`life == 0`, unlinks corpses (`life == 10`) with `0x44B958` (fixes head/tail, sets both links to −2).
`link 0x44BC8C(gs, kai, obj, t, m)` inserts at the head.

---

## 8. The influence map (`krusty_general 0x456818`, `krusty_general.c`)

Three passes, no `rand()`, no commands.

1. **Reset**: every zone `+4/+8/+0xC = −1`, `+6/+0xA/+0xE = 0`, `+0x10 = 0`, `+0x12 &= ~1`. A zone with
   bit 2 (marked) loses the bit when the AI's own vision bit is set on its centre tile, and then gets
   `+0x10 = 1` (a marked zone counts as one enemy building until seen).
2. **Objects** `o = 0..799` with live `team < 8`:
   * *visible* iff for some team `t` with `see_thru[t]`: the ground-layer bit `teamBit(t)` is set on the
     object's tile and the object is not a hidden mine undetected by `t` (`OT.hidden(+0x68) && team != t
     && !(obj+0xCA & (1 << t))`).
   * alive/corpse (`life != 0`) and visible → use the live `(x, z, type, team)` and, **if `team != p`**,
     refresh the memory record `kai+0x1202+4o` (own objects are never memorised). Free slot and visible
     → forget the record.
   * not visible → use the memory record if it has a type; if the AI's own vision covers the remembered
     tile (nobody flagged sees an object there) the record is forgotten and the object skipped.
   * skip allies (`gs+0x46F54[10p + team] != 0`, `GS.ALLIANCE`) except own objects.
   * `zone = family(x, z)`; on a family-0 cell (buildings) use the nearest free cell (`0x41B68C`).
   * **unarmed** (`OT.weapon0(+0x18) == −1`): skip own and teams ≥ 8; `OT.defenceClass(+0x40) == 8`
     (invulnerable class) → skip; else `zone.+0x10 += 1` (workers, healers, buildings count).
   * **armed**: `wc = WT(w).class(+0)`, `a = OT.defenceClass`;
     `gnd = MB[wc][1]·25 / MB[1][a]` (`a == 2` → `/ 50` to avoid `MB[1][2] = 0`), `aa = MB[wc][2]`
     (raw 8.8 values; Classic: TRSC gnd 3 / aa 64, SCYT 25 / 0, BARR 35 / 0, SCGM/ORTU 32 / 0, deployed
     towers 80 / 128). Pool update rule `add(owner, strength, team, s)`: same owner → `strength += s`;
     else `{ if owner != −1: flags |= 1; if s >= strength: strength = s − strength, owner = team else
     strength −= s }`. Flying units with `gnd > 0` go into the **air** pool (`+0xC/+0xE`) **and** the
     ground pool; every armed unit with `gnd > 0` into the **ground** pool (`+8/+0xA`); `aa > 0` into
     the **anti-air** pool (`+4/+6`). The strength uses the *base* weapon slot, never the player's
     upgrade level.
3. **Contested**: a zone whose three non-empty owners disagree gets bit 0.

---

## 9. Census: which units go to which task (`krusty_census 0x4572AC`, `krusty_attack.c`)

Runs on the first think and inside `krusty_demand` every think.

1. Count **free** own units per class: `team == p`, `obj+0xD2 == −2`, `life ∉ {0, 10}`, type ∉ {41, 42}
   (deployed towers).
2. Fixed routing: all **workers (class 6) → task 0**; all **tower builders (class 1) → task 1**;
   **scouts (class 5) → task 3** when a scout item is buildable (`dep_check_troop` items 10 SCGM / 24
   ORTU) or any scout is free; otherwise, if no scout can be built, **all free infantry → task 3** while
   `4·have3[0] <= have1[0] + have2[0]` (evaluated once).
3. Every remaining unit of every class: `split == 0x100` → task 2; else task 1 while
   `split·have1[c] <= (256 − split)·have2[c]`, else task 2 (counts bumped provisionally). Default
   0xC0: **defend ≤ 25 %, attack ≥ 75 %** of the combat units.
4. For each free unit, the first task with a remaining quota for its class: `m = M.take(gs, kai, t, c)`,
   `link(obj, t, m)`.

`take` callbacks: task 0/3 → group 0. **Defend** `0x459370`: active group with the smallest
`count[class]` (doubled for groups ≠ 0), tie → fewer units in total, then `have[c]++, count[c]++`.
**Attack** `0x458C5C`: score `c = count[class]`, `×4` unless `state == 1`, `×(m+1)`; smallest wins,
tie → fewer units; with the two initial groups this yields 0, 1, 0, 1, 0, 0, 1, … (group 0 ends up about
twice as strong).

---

## 10. Production: demand and goals (`krusty_demand 0x457684`, `run_goals 0x457614`, `krusty_general.c` actions)

`krusty_demand`: census; `have[9] = 0`; for each task `M.recount` then `have[c] += M.have[c]`; plus every
unit in the four production queues (`P.QUEUE_LEN/QUEUE`, class by `unit_class` of the queued type).
Then `run_goals(gs, kai, p, have)`: for `g = 0, 1, 2, …` **without upper bound** — `check(gs, kai, p,
have, param) == 0` → run `action(...)` and stop. Goal 17 ("always") terminates the chain; a scenario
that overwrote it with `aimsg 8` would let the loop run into the tunables.

| g | check | action | param | meaning (`0x499110[param][race]` = human/alien DEPEND item) |
|---|---|---|---|---|
| 0 | building | build | 8 | HQ / mind hive (items 0 / 14) |
| 1 | workers | build worker | 1 | fewer than 1 worker |
| 2 | building | build | 0 | barracks / warrior hive (1 / 15) |
| 3 | army | build unit | 5 | army < 5 |
| 4 | building | build | 1 | robot factory 1 / breeding 1 (3 / 17) |
| 5 | building | build | 3 | science 1 (2 / 16) |
| 6 | army | build unit | 10 | |
| 7 | workers | build worker | 2 | |
| 8 | army | build unit | 15 | |
| 9 | building | build | 4 | science 2 (4 / 18) |
| 10 | building | build | 2 | robot factory 2 / breeding 2 (5 / 19) |
| 11 | army | build unit | 20 | |
| 12 | building | build | 7 | armour upgrade items (69 / 43) — `dep_check_building` is 2 for upgrade items, dead unless the slot-blocked test fires |
| 13 | building | build | 6 | weapon upgrade items (67 / 41) — dead likewise |
| 14 | army | build unit | 30 | |
| 15 | building | build | 5 | research centre (6 / 20) |
| 16 | army | build unit | 200 | |
| 17 | always | nothing | 0 | terminator |

Goal functions (all `(gs, kai, p, have, param)`, `krusty_general.c`):

* **workers** `0x45618C`: fires iff `have[6] < param`. Action `0x4561A8`: for every troop item 0..109
  with `dep_check_troop == 1` and `unit_class == 6`: `if money < cost return; money -= cost;` send
  `0x0A type p 1` — and continue the loop (one worker item per race in practice).
* **building** `0x456228`: `item = 0x499110[param][race]`; fires iff `dep_check_building(gs, p, item) ==
  1` **or** the item's slot is blocked for the scenario (`0x4382F4`; the action then does nothing but
  the chain stops). Action `0x4562B0`: `cost = 0x438188(item)`; `if cost > money return`; blocked →
  return; `money -= cost`; assert `dep_check_building == 1`; send `0x09 slot level p`.
* **army** `0x4563C0`: never fires at the unit cap (`stat 6 (0x41A790) >= gs+0x528`); fires iff
  `have[0] + have[2] + have[3] + have[4] + have[5] < param` (infantry, mech, artillery, cyborg, scout).
  Action `0x456408`: `score[c] = have[c]·weight[c]` with the tunables (`score[6] = 10000`, class 8
  skipped); over the buildable troop items (`dep_check_troop == 1`) take the class with the smallest
  score (first wins ties); assert it is still buildable; `if money < cost return; money -= cost;` send
  `0x0A type p 1`. No `rand()`.
* **always** `0x4565C0` / **nothing** `0x4565CC`.

`set_goal 0x456624(kai, g, kind, param)` (`aimsg 8`, asserts `g < 32`, `kind <= 4`) installs the pair
by kind: 0 workers, 1 building, 2 army, 3 and 4 always/nothing.

---

## 11. Task 0: workers to vents (`worker_update 0x4595D0`, `krusty_scout.c`)

Group 0 only; no `rand()`. For every listed object: stuck tracking (`+0xCD/+0xCE/+0xCF`, unbounded
counter). For workers (types 6 EXPL, 0x0E SLUG): `home` = destination zone of the **defend task's
group 0**; a worker stuck for more than 10 thinks loses its target (`+0x11 = 0`); a worker with a target
zone whose `route_threat(myzone, +0x11) != 0` (§13, enemy ground strength within one hop of the route,
or no route) is **recalled** with a plain move (`0x07`+`0x05` order 2) to `home`'s centre and
`+0x11 = 0`. Then the *last* worker with `+0x11 == 0` is sent to a vent: over all objects of type 0x28
(vent) with `i16 obj+0x32 != 0` (vent rate) and `life ∉ {0, 10}`, whose tile is empty in the ground
layer (`& 0x3FF == 0x3FF`), whose zone no other worker targets, with the smallest **hop distance**
`+0xD` and a threat-free route from the worker's zone: `+0x11 = vent zone`, `0x07`+`0x05` order 2 to
the vent's raw position. One dispatch per think.

---

## 12. Task 1: defend (`defend_update 0x4590BC`, `krusty_defend.c`)

* **Home guard** = group 0 (created at init at the lowest zone id with hop < 3). With probability 1/16
  per think (`rand() & 0xF == 0`) it is re-routed to a random zone with hop distance < 2: over zones
  1..254 with `+0xD < 2`, `rand() % k == 0` selects the zone and **then** `k++` (so the first candidate
  is always taken, the next replaces it with 1/2, then 1/3, …; a biased reservoir); assert a choice;
  `set_route(group 0, choice)` and the global byte `[0x49941C] = 1` ("re-issue orders", consumed by the
  mover).
* **Zone groups** (every think): `zoneset` = the vent zones the worker task's units are heading to
  (`obj+0x11` of task 0's list) ∪ the scripted defend zones (`kai+0x6A74`). Groups 1..15 whose
  destination is not in the set are **disbanded** (`0x46BF98`: inactive, every unit `ai_status = 0`,
  `+0xD2 = −2`, so the census re-offers them); a zone with two groups loses the second. For every set
  zone without a group: `new_minor` at the lowest inactive slot and `set_route(zone)`.
* The defend task never reads strengths and never recalls units itself; all its orders come from the
  mover (§15): assault moves to zone centres and deploy orders for tower builders.

---

## 13. Task 2: attack (`attack_plan 0x458864`, `krusty_attack.c`)

No `rand()` in the whole file; the choice is deterministic given the influence map.

Helpers:

* `hops 0x44B670(gs, a, b)`: 0xFF if `a == 0 || b == 0`, 0 if equal, else steps along `next[·][b]`
  (0xFF on a 0 entry, 256 when not reached within 256 steps).
* **`route_threat 0x457EA4(gs, kai, from, to, p, route?)`**: `A` = zones on the route `from → to`
  (routing matrix when `route == NULL`; `from == 0` or a 0 hop → **return −1**) plus their neighbours;
  `B = A ∪ neighbours(A)`; returns `Σ ground strength` of zones in `B` whose ground owner is neither −1
  nor `p`. With `from == to` it is the enemy ground strength within two hops of that zone.
* **`avoiding_route 0x4579F0`** (Dijkstra on zones, cost of entering `z` = `1 + 10·Σ enemy ground
  strength of z's neighbours / strength`; asserts "AI Path not found (hsm)" when unreachable). Only
  reached when `kai+0x6C34 · e > strength`; with the uninitialised (0) ratio it never runs.
* **`group_strength 0x4583AC(gs, kai, t, m)`**: over the group's units with `ai_status == 1` (arrived),
  count per class; `Σ 25·count[c]·MB[WT(OT(c).weapon0).class][1] / MB[1][gs+0x40]` — **uses the class
  number as an object type and `gs+0x40` as a defence class** (original bugs; `gs+0x40 != 2`, else `/ 50`).
* **`choose_target 0x458540(gs, kai, excluded, maxh, p, strength, from, route_out)`**: for every zone
  `z` not excluded: `e = route_threat(z, z)`; `score = 0`; if `hops(z) <= maxh` and (`e != 0` or
  contested): `score = flags & 2 ? maxh/2 : maxh + 1 − hops(z)`; if `buildings > 0`: `score += maxh/2`;
  `score == 0` → next. `e2 = max(1, route_threat(from, z))`; (avoiding-route branch as above, dropping
  the zone when even the safe route's threat exceeds `strength`); **`final = strength·score / e2`**,
  highest wins; `route_out` = the routing-matrix chain `from → z` (or the safe route). Returns −1 when
  nothing scores.
* `pick_defend_zone 0x458224`: among the defend task's group destinations the one with the fewest
  attack groups (array `C`), then the nearest by hops.

`attack_plan` each think: `A[z]` = destinations of the defend task's active groups (1 for group 0, 2
otherwise), `maxh` = their largest hop distance + 3; `B[dest] = 1` for own groups in state 0 (targets
already taken); `C[m]++` for states 1/3 (**indexed by the group index instead of the zone — original
bug**). Then per active group by state:

* **2 or 3** (needs a target): `s = group_strength`; `best = choose_target(B, maxh, p, s, group.zone)`;
  found → `B[best] = 1`, `set_route(best, route)`, state 0. Not found and state 2 → state 3, route to
  `pick_defend_zone(A, C, group.zone)`, `C[t]++`.
* **0** (en route): `s = group_strength`; `e = route_threat(group.zone, dest, &route[step])`; if `e ==
  0` and `zone[m].flags & 1 == 0` (**group index as zone index, bug**) and `zone[dest].buildings == 0`
  → state 2 (target cleared); else if `2·s <= e` → state 2 (too weak; the group keeps its orders until
  re-targeted next think).
* **1**: unreachable (the block that would set it never runs).

The attack task sends no `0x0B`/`0x0E`: it moves groups zone by zone with assault orders; units engage
what they meet (`DC16_BATTLE_ENGINE.md` §14.1 mode 1).

---

## 14. Task 3: scouting and bombing (`bomber_update 0x459CA0`, `krusty_scout.c`)

Group 0; scouts (types 5 SCGM, 0x0D ORTU) or infantry as stand-ins (§9); units are targeted
individually. Per unit `o`, on `ai_status = o+0xCC`:

* 0 or 4: needs a target iff idle (`o+0x39 == 1`). 5: idle → status 6 (`OBJ_BOMBING`, sits and shoots).
  6: assert status 6; needs a target iff `rand() & 0x3F == 0` (1/64) or `o+0xC9 != 0`. Always `o+0xC9 = 0`.
* New target: `rand() & 1 == 0` (50 %) → **zone pick**: for `a = 1..255`, visited = `a` ∪ neighbours;
  if any neighbour of a visited zone has an **anti-air owner** ∉ {p, −1} → skip `a` (enemy AA within two
  hops); `score = enemy ground strength(a) − enemy air strength(a) + (buildings ? 1 : 0)`, best `> 0`
  wins → `wp = centre(a)`, status 5. Otherwise (or no zone scored): `rand() & 3 != 0` (75 %) → random
  tile `x = rand() % W`, `z = rand() % (H − 5)` until `family != 0`, status 4; else (25 %) → reservoir
  over live vents (`rand() & 0xFF < 256 / n`), status 4, then **wander away from enemy air**: while the
  zone of the point or one of its neighbours has an air owner ∉ {p, −1} (the owner *ids* are summed, so an
  enemy player 0 counts as none — quirk): `x += (rand() & 15) − 8`, `z += (rand() & 15) − 8`, clamped
  to the map, no iteration limit; a zone-0 point is sent as is. No vents → random tile without a status
  change.
* Send `0x07`+`0x05` with one waypoint `(x << 8, z << 8)`, order **2 for scouts, 7 for anything else**.

`rand()` sites: `0x459E3A` (1/64), `0x459E68` (coin), `0x45A08D` (tile vs vent), `0x45A0A0/0x45A0C2`
(tile, per attempt), `0x45A130` (per vent), `0x45A1B0/0x45A1D2` (tile when no vent), `0x45A35D/0x45A36D`
(wander). The dead code before the assert reads the unit's zone owners and discards them.

---

## 15. Group movement (`krusty_army.c`)

* **`set_route 0x46B770(gs, kai, t, m, dest, route?)`**: `route[0] = group.zone`, then `route[i] =
  next[route[i−1]][dest]` until `dest` (no bound check; an unreachable `dest` loops on zone 0), or copy
  a given route until `dest` (assert `count < NZONES`); `step = (dest == zone) ? 0 : 1`; `dest` stored.
* **`new_minor 0x46BE18(kai, t, m)`**: assert inactive; `zone = dest = route[0]` = lowest zone id in
  1..254 with hop < 3 (0xFF if none); `step = 0`, active, empty list.
* **`disband 0x46BF98`**: see §12. **`move_all 0x46BDA4`**: `move_group` for every active group.
* **`move_group 0x46B984(gs, kai, t, m)`**: `force = [0x49941C]; [0x49941C] = 0`. For every unit
  (a `life == 0` unit prints "My god, jim, this object is dead" and exits): `ai_status 0 → 2`; stuck
  tracking (saturating at 60); **tower builders (types 1, 9)** within `hops < 3` of the group zone and
  stuck > 3 thinks get `[0x05 obj 0x0D]` (deploy) and a reset counter. Others: `h = hops(unit zone,
  group zone)`; if `h > 3 || force`: a unit with `stuck < 60 && ai_status != 2` marks the group as still
  travelling; a unit whose `+0x11 != group zone` gets `+0x11 = zone` and its own `0x07`+`0x05` to the
  zone centre with order **7 if `ai_status == 2` else 2**; if `h <= 3` and not forced: `ai_status 2 → 1`
  (arrived). When nobody is travelling: **advance** `nz = route[step]` (`step++` unless `nz == dest`),
  `group.zone = nz`, and send one `0x07`+`0x05` **order 7** frame to `centre(nz)` for every unit whose
  `+0x11 != nz` (sent even with zero units).

Units move *between* groups only by being disbanded (or dying) and re-offered by the census.

---

## 16. Vision, memory and cheating

Krusty sees exactly what its own team sees (`see_thru` = own team), plus allied teams when a scenario
says so (`aimsg 14`). What it cannot see it remembers from the last sighting (the memory table, §8) and
forgets when its own vision shows the tile empty. It never reads enemy money, queues or hidden units,
and it does not path-find below the zone level (it orders assault moves and lets the units' own
pathing do the rest). The only asymmetry with a human is the harvesting multiplier of lobby type 1.

---

## 17. Determinism and what a port must reproduce

### 17.1 Bit-exact mode (inside the engine, for AI-typed players and disconnect takeovers)

* Schedule: `TICK & 3 == 0`; tick 4 all AIs by slot; otherwise `rr = (rr + 1) % 8`, one slot.
* Position in the tick: after `record(t)`, before the next frame's commands (§3); the AI's commands go
  through the same handlers immediately; goal actions deduct `P.MONEY` before sending.
* `ai_think`: one `rand()` per personality pair, strict `w > r·(cum+w)/32767` in double arithmetic.
* Krusty's `rand()` sites, in order within a think: defend §12 (gate `& 0xF`, then one per candidate
  zone with `% k`), bomber §14 (per unit as listed). Nothing else in Krusty calls `rand()`; the census,
  influence map, goals, attack task and mover are deterministic.
* Quirks to keep: `group_strength`'s class-as-type and `gs+0x40`; the two group-index-as-zone-index
  reads in `attack_plan`; the biased reservoir of the home-guard re-route; the unbounded goal loop; the
  uninitialised `kai+0x6C34`; the wander loop summing owner ids; `zoneset` of §14 not clearing index 255.
* The type-1/2 personalities use the unseeded CRT `rand()` (§4) and cannot be lock-stepped; they are
  campaign-only.

### 17.2 Logical mode (server bots for fake human players)

Same decisions, same command bytes, but: a private RNG (the engine's is part of the checksum), the
commands go into the next sync frame instead of being applied immediately (one frame ≈ 1–2 ticks of
latency), money is deducted on the server engine's player block (the only ledger of a fake player),
and vision is the fake player's own vision bits from the engine's grid. `RELAY_SERVER_PLAN.md` §19.

---

## 18. Scenario control: `ai`, `aimsg` and the DISCONNECT takeover

* Trigger action 0 **`ai <player> <type>`** (`0x43D930`): `P.AI_TYPE = type`. Nothing else (no init,
  no money change; an existing Krusty block is reused).
* Trigger action 4 **`aimsg <player> <n> <words…>`** (`0x43D94E` → `0x41AF74` → `krusty_aimsg
  0x44BF78` for type 3; a no-op for other types; allocates the state if needed). `id = words[0]`, ids
  > 14 → "Unknown AI msg" + assert:

| id | words | effect |
|---|---|---|
| 0 | v | split `+0x6C14 = v·256/100` |
| 1–5 | v | class weights `+0x6C18..+0x6C28` (infantry, mech, artillery, cyborg, scout) |
| 6 / 7 | x, z | add / remove the zone of tile (x, z) in the defend list `+0x6A74` (assert `zone!=0`; 16 slots; assert when full / not found) |
| 8 | g, kind, param | `set_goal(g, kind, param)` (§10) |
| 9 / 10 | x, z | zone flag bit 1 set / clear ("attack here": constant score `maxh/2` in §13) |
| 11 / 12 | x, z | zone flag bit 2 set / clear ("unknown zone": counts as one enemy building until seen) |
| 13 | v | attack ratio `+0x6C34 = v·256/100` |
| 14 | team, on | `see_thru[team] = on` |

* **DISCONNECT** handler `0x41DBE0` (command `0x10 net_id`): find the player with that `P.NET_ID`
  (assert found); `P.AI_TYPE = 3`; **`P.MONEY −= P.SPENT`** (converts the human's "money minus booked
  spending" into the AI's convention); `gs+0x974[p] = 0` (max-speed slot); injects the chat
  "`%s lost, AI taking over`" through the `0x0E` handler. Nothing else is reset; the first think
  (§2 schedule) allocates the Krusty state for the existing base.

---

## 19. Save and load (`krusty_save 0x44C750`, `krusty_load 0x44CF48`)

Sections, each asserting its element count: TOP (9 i32 tunables `+0x6C14..+0x6C34`, 8 bytes
`see_thru`), ZONE (256 × 11 i16 from `kai+2+18z`: `u8 +0, u8 +1, i8 +2, i16 +4, i8 +6, i16 +8, i8 +0xA,
u8 +0xB, i16 +0xC, i16 +0xE, u8 +0x10` — the fields of §5 shifted by the struct base), GHOST (800 ×
4 i16 memory records), MTASK (per task 4 header i32, then per group 9 i32 `{u8 +0, +1 != 0, +4, +8,
+0xC, u8 +0x10, i16 +0x12, i16 +0x116, i16 +0x118}`, 9 i16 from `+0x11A`, 257 bytes from `+0x14`,
then the task's 9 `have` i16), DEFENSE (16 × 2), GOAL (32 × 3 i32: indices of the check/action
functions in `0x48B404`/`0x48B41C`, param). Not saved: callbacks, the first-run flag, padding. Load
re-runs `krusty_alloc` (callbacks and defaults from code), overwrites with the file and **clears the
first-run flag**.

---

## 20. Address index

| VA | name | file |
|---|---|---|
| `0x41AC80` `0x41ACD8` `0x41AD30` `0x41AE38` `0x41AEB4` `0x41AF0C` `0x41AF74` | ai_init, ai_destroy, ai_think, ai_turn, ai_save, ai_load, ai_message | ai.c |
| `0x421630` `0x42163C` `0x421648` `0x41E06C` | set_local_gs, set_local_command_mode, send_command, execute_commands | hack.c / mobiles.c |
| `0x40C50C` `0x40C538` `0x40C564` `0x40C7D4` `0x40C978` | command builders | hack.c |
| `0x455E30` `0x455E70` `0x455EC0` `0x455F3C` `0x44AEB0` `0x44AF10` `0x44B004` `0x44B058` `0x44B2C0` `0x44B360` `0x44B500` `0x44B50C` | type-1/2 weights and actions | ai.c region |
| `0x456078` `0x4560CC` `0x456150` | own_unit, enemy_target, unit_class | krusty_general.c |
| `0x45618C` `0x4561A8` `0x456228` `0x4562B0` `0x4563C0` `0x456408` `0x4565C0` `0x4565CC` `0x4565D4` `0x456624` | goal checks/actions, init_goals, set_goal | krusty_general.c |
| `0x456818` `0x456EF4` | krusty_general (influence map), krusty_init_zones | krusty_general.c |
| `0x4572AC` `0x457614` `0x457684` `0x4575E4` | census, run_goals, krusty_demand, update1 default | krusty_attack.c |
| `0x4577A0` `0x457834` `0x4578C8` `0x45795C` | zone flag set/clear (aimsg 9–12) | krusty_attack.c |
| `0x4579F0` `0x457EA4` `0x458224` `0x4582FC` `0x4583AC` `0x458540` `0x458864` `0x458C0C` `0x458C5C` `0x458E54` | avoiding_route, route_threat, pick_defend_zone, nearest_marked_zone, group_strength, choose_target, attack_plan, attack_minor_start, attack_take, attack init | krusty_attack.c |
| `0x458EC0` `0x458FC0` `0x4590BC` `0x459370` `0x459574` | add/remove defend zone, defend_update, defend_take, defend init | krusty_defend.c |
| `0x4595D0` `0x459AB8` `0x459B60` `0x459B68` `0x459BE0` `0x459C44` `0x459CA0` `0x45A458` `0x45A460` | worker_update, worker_count, worker_take, worker init, count_group0, bomber_urgency, bomber_update, bomber_take, bomber init | krusty_scout.c |
| `0x44B670` `0x44B6D4` `0x44B844` `0x44B894` `0x44B8E8` `0x44B950` `0x44B958` `0x44BA78` `0x44BC00` `0x44BC10` `0x44BC8C` `0x44BD50` `0x44BE64` `0x44BF78` `0x44C750` `0x44CF48` | hops, recount, header_clear, group_init_list, task_init_groups, noop, unlink, purge, purge_group0, purge_all, link, krusty_alloc, krusty_think, krusty_aimsg, krusty_save, krusty_load | krusty.c |
| `0x46B770` `0x46B984` `0x46BDA4` `0x46BE18` `0x46BF98` `0x46C024` `0x46C074` | set_route, move_group, move_all, new_minor, disband, take_unexpected, task_init_common | krusty_army.c |
| `0x41DBE0` `0x43D930` `0x43D94E` | DISCONNECT handler, trigger `ai`, trigger `aimsg` | mobiles.c / trigger.c |
| `0x489488` `0x48B3C4..0x48B468` `0x499158` `0x499110` `0x48385C` `0x4AF090` `0x48950C` `0x49941C` | personality table and records, goal table, building-kind map, 1/32767, local-mode byte, local gs, re-issue flag | DGROUP / .bss |

`0x45A4CE..0x45AB88` and `0x46C1xx..0x46CFxx` are the Watcom C run-time (start-up, errno, getcwd,
strdup, time), not AI code, despite sitting next to it.

---

## 21. Corrections to `DC16_BATTLE_ENGINE.md` §17 (12 Sep 2026)

* Task roles: 0 = workers → vents, **1 = defend**, **2 = attack**, 3 = scouting/bombing (§17.2 had 1
  and 2 swapped and called 3 "unknown"). `0x459574` is the defend init, `0x458E54` the attack init.
* The state layout: zone records at `kai + 18z` with the fields of §5 (owners are i8, `+0xD` is the
  hop distance, `+0x10` the building count, `+0x12` the flags); `kai+0x1202` is the last-seen memory,
  not a task assignment; the major tasks start at `kai+0x1E84` and `+0x3154` is task 0's `have[]`
  (a count, not a demand); groups are 0x12C bytes with head/tail at `+0x116/+0x118`; there are 32
  goal slots and nine tunables; `+0x6C38` is the vision-sharing mask, not "enemy flags".
* The `+0x3178` callback takes no class and recounts; class → task routing is fixed in the census.
* The weighted choice constant is `1/32767`, the comparison strict, `rand()` is drawn once per pair;
  `ai_turn` rotates over *slots*, so a think happens every 32 ticks at phase `4 + 4p`.
* The AI's client is `[gs+0x950]`; local mode is the byte `0x4AF090` toggled around `ai_turn`, with
  immediate execution through `0x41E06C` after `record(t)`.
* The personality `+0xC` callback is `destroy` (game end), never called at start; `0x457EA4` is the
  route-threat sum, the "AI Path not found (hsm)" assert belongs to the Dijkstra `0x4579F0`; `ai_status`
  is `obj+0xCC` (not `+0xD0`); `0x40C978` takes `(obj, target)`; `0x40C538` takes `(client, player,
  type, count)`; the type-1 "assault-move to a random point" is two waypoints then the HQ, order 7,
  usually degenerating to the HQ alone; the type-2 weight is a constant 1.
* DISCONNECT also does `MONEY −= SPENT` and clears the max-speed slot; `aimsg` ids 6–14 are as in §18.

## 22. Open points

* `gs+0x40` in `group_strength` and the intended meaning of that formula (probably a bug).
* Who was meant to set `obj+0xC9` and read `M+8`; both look like dead remains.
* `kai+0x6C34`'s effective default (pool memory; the pool is not known to zero).
* The exact strength formula was read from the code; its numeric consequences per unit type were only
  spot-checked (§8 examples).
* Nothing here was re-checked against `DCEXP16.EXE` (the code is the same build; offsets differ
  slightly, `DC16_SINGLE_EXE_MERGE.md`).

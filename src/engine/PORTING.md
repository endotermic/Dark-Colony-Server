# Porting dc16.exe's simulation to `src/engine/` — conventions

The engine is a **bit-exact port** of the simulation core of Classic `dc16.exe` so that the server
can compute the lockstep checksum (`sync.c` `0x44ABC0`) and put `0x08 (checksum, time)` commands
into its sync frames. A wrong checksum makes every real client abort with "sync error", so the
port must reproduce every integer operation, every `rand()` call and their order. Nothing is
"good enough": when the original does something odd, do the same odd thing and comment it.

Sources of truth, in this order:

1. the disassembly `C:\Users\nika\Documents\Dark-Colony-development\dc16.asm` (`dumpbin -ALL
   -DISASM` of Classic `dc16.exe`; virtual addresses; `DGROUP` string VA = file offset + 0x402800);
2. `docs/DC16_BATTLE_ENGINE.md` (structures, addresses, what each function does — descriptive,
   not instruction-exact; trust the disassembly when they differ and note the difference);
3. `docs/DC16_NETWORK_PROTOCOL.md` §4.3 (command payloads), `docs/DC16_MAP_FILES.md` (map data).

Extract a function from the disassembly with (Git Bash, in the development folder):

```bash
awk -v s=$((16#414FD0)) -v e=$((16#415454)) '/^  [0-9A-F]{8}: / { a=strtonum("0x" substr($1,1,8)); if (a>=s && a<e) {print; p=1} else if (p) exit }' dc16.asm
```

Regions that `dumpbin` shows as raw hex (after inline jump tables) can be re-disassembled by
wrapping the bytes in a minimal COFF object (see DC16_BATTLE_ENGINE.md §15 "How this was produced").
Constant tables of the exe are already extracted to `data/dc16-tables.json` (rand table, sin
quarter wave, arc, dir8, nudge tables, dir3x3, neighbour orders, slot offsets, footprints, unitdef,
spiral, building list, spawn offsets, slot→class); read raw bytes of the exe with
`node C:/Users/nika/Documents/Dark-Colony-development/scratch/dump.mjs <VA hex> <len> [x|i8|u8|i16|u16|i32] [cols]`
when you need another table.

## Calling convention and integer rules

* Watcom register convention: arguments in `eax, edx, ebx, ecx`, then the stack (pushed right to
  left, so the first `push` before a call is the LAST stack argument... check each call site: the
  pushes appear in reverse order of the C parameter list). Return value in `eax`/`al`.
* `gs` (the game state, `eax` of every simulation function) is a Buffer; read and write it ONLY
  through the helpers of `mem.js` (`u8/i8/i16/u16/i32`, `w8/w16/w32`) with the original offsets
  (`objAddr(obj) + O.X`, `playerAddr(p) + P.MONEY`, `missileAddr(m) + MS.X`, `GS.TICK`, ...).
  Add missing offsets to `mem.js` as named constants (comment the original offset) rather than
  writing magic numbers twice.
* Signed/unsigned: `movsx` → `i16`/`i8`, `movzx` → `u16`/`u8`. A `mov ax, word ptr [...]` followed
  by 32-bit arithmetic in `eax` whose upper half is stale is *undefined* in C but deterministic in
  the binary — reproduce what the code does with the low 16 bits and comment it.
* `idiv` → `idiv(a, b)` (truncate toward zero) and `irem(a, b)`; `sar` → `>>`; `shr` → `>>>`;
  `imul` → `*` then `| 0` when it can exceed 2^31 (positions and hit points never do, but check).
* 16-bit fields wrap: writing through `w16` wraps like the original store. Positions (`O.X`, `O.Z`)
  are int16 in 1/256 tile.
* `rand()` (`0x4120F0`) is `G.rand()`: `index = (index + 1) & 0xFF; return table[index]` with the
  int32 table `0x488F20`; `srand(s)` (`0x4120E0`) is `G.srand(s)`. **Every `rand()` call of the
  original must appear at the same point in the same order**, including calls whose result is
  discarded or only used for graphics (comment those: `// rand() consumed for the blood sprite`).
* Assertions (`0x47BE46` printf + `0x47BE67` + `0x47BE77` + `0x47C02E` pattern) become
  `G.assert(cond, 'message')`; they are real bugs when they fire, do not silence them.
* Sounds, animations shown on screen, redraw flags and statistics do not enter the checksum, but
  animation *timing* does (states wait for animations to end), and stats are cheap: call
  `Anim.*` / `G.stat*` where the original does, and comment `// sound` where a sound is played.
* Graphics-only calls (draw, minimap `0x43A040`, message boxes) are `// display only` comments.

## Layout of the port

One module per original source file group. Export functions named after the original with the
VA in a comment on the line above; keep helper names close to the docs.

| Module | Content (original source file) | Key functions |
|---|---|---|
| `mem.js` | offsets and accessors (this is done) | |
| `engine.js` | `Game` class: `gs`, `map`, tables, RNG, stats, `tick()` = `game_tick` `0x419978`, checksum `0x44ABC0`, history; command dispatch entry | done by the integrator |
| `tables.js` | balance tables from `data/classic/gamestat.json` into per-game mutable records (`loader.c`) | `loadTables(json)` → `{ weapons, types, mbullet, booms, depend, unitid }` |
| `scenario.js` | game start: shuffle `0x4014F8`, player setup `0x401668`, scenario objects `0x41BAF0`, `initObject` `0x41B124`, `createObject` `0x41B930`, unit cap `0x41E7FC` | `startGame(G, mapJson, lobby)`, `initObject(...)`, `createObject(...)`, `unitCap(G)` |
| `grid.js` | map grids (`load`, ground/air/secondary layers), `clearSeen` `0x4458C0`, vision `0x44A718`, grid write/remove (`collide.c` `0x434EB8` and friends) | `cellGround(G,x,z)`, ... |
| `ticker.js` | `ticker.c` + idle logic of `mobiles.c`: stack primitives `0x412114..0x41296C`, dispatcher `0x4194DC`, state table, idle state `0x414B9C`, turn/dir/fidget `0x412414/0x4124B8/0x412650`, order handlers, waypoint states, nudge walk | `dispatchObject(G,obj)`, `pushState`, `popState`, `resetAndDispatchOrder`, `sleepState`, `setCooldown`, `pushIdle`, `turnTowards`, `dirTo`, `STATE_TABLE`, `ORDER_TABLE` |
| `move.js`, `path.js` | `begin_move` `0x414FD0`, move state `0x415A94`, step state `0x4128D4`, `handle_block`, re-route, `start_step`, position add `0x43508C`; `path.c` grid init `0x442D8C`, find_path `0x444B34`, ... | `beginMove(G,obj,mode,flag)`, `stateMove`, `stateStep`, `PathGrid`, `findPath`, `pathLength`, `extractPath` |
| `combat.js`, `missile.js` | `collide.c` targeting `0x4356C8/0x435D5C/0x4350D4`, `fire_weapon` `0x413018`, `apply_damage` `0x441B4C`, `object_die` `0x4165A0`, corpse/wreck states, healing `0x413F24`, fire-at-ground `0x418160/0x4182C0`, deploy `0x417DA4`, rally `0x417400`; `missile.c` alloc/create/update/detonate/burn, `sincos` `0x441724`, `atan2` `0x4415C0` | `findTarget`, `findTargetInRange`, `collide`, `fireWeapon`, `applyDamage`, `objectDie`, `updateMissiles` (`0x442B50` wrapper included) |
| `commands.js` | in-game command handlers `0x41C9C8..0x41DFE8` (types 0x03..0x1B) applied to the state | `applyCommand(G, raw)` |
| `city.js` | `city.c`/`depend.c`: `build_slot` `0x4450F4`, building idle `0x41460C`, production, dependency checks | `buildSlot`, `stateBuildingIdle`, `depCheckBuilding`, ... |
| `renat.js` | `renat.c` generators `0x440100/0x43FFB0/0x41B818`, vents, artifacts `0x440650/0x4406F8/0x4400C0`, triggers `0x43E5B0` (+ `0x43CFE8`, `0x43D904`), states 0, 0xC, 0xF, 0x10, 0x13–0x16, pickups `0x4143D4` | `generatorsTick(G)`, `triggersTick(G)`, state handlers |
| `anim.js` | animation instances (8-byte slots at `O.ANIM0/1/2`, status bytes), FIN frame timing, sprite-derived data (bounding boxes, muzzle hotspots) from `data/classic/sprites.json` | `startAnim`, `advanceAnims`, `hotspots`, `bbox` |
| `ai.js` | `ai.c` / krusty (later) | |

Cross-module calls: `import * as Ticker from './ticker.js'` and call `Ticker.pushState(...)` at run
time (ESM live bindings make the cycles harmless). Use the names in the table; if you need a
function another module owns and it is not listed, add it to this table with a `TODO(owner)` and
call it by that name — the integrator wires the rest.

Per-object state handlers have the signature `(G, obj, infoAddr) => 0|1` (`infoAddr` = absolute gs
offset of the state's info block, the `&info` argument of the original); order handlers
`(G, obj) => void`. Every handler starts with a comment naming its VA and state/order id.

## What `Game` (engine.js) provides

```js
G.gs            // Buffer, the game state (mem.js offsets)
G.map           // { w, h, load: Int32Array(w*h), ground: Int32Array, air: Int16Array, sec: Int16Array,
                //   terrainClass: Uint8Array, path: PathGrid, families/routing from the map JSON }; index z*w+x
G.tables        // { weapons[], types[], mbullet: {numArmours, numWeapons, rows[][]}, booms[], depend[], unitid }
G.scenario      // { gameType (2 = multiplayer), occupied[8], vars[16], ... }
G.diplo         // [Uint8Array(8), Uint8Array(8)]: the two diplomacy bit matrices (0x471C0/0x471C4)
G.stats         // Int32Array(8*12): 0x4A5710 per-player statistics, G.stat(k, p) / G.setStat(k,p,v) / G.addStat(k,p,v)
G.typeStats     // Int32Array(8*130*4): 0x4A5890
G.rand(), G.srand(s), G.randIndex
G.globals       // { loopTrap (0x488F1C), pathGen (0x48AA9C), ... } plain object for DGROUP scalars
G.assert(cond, msg)
G.log           // optional logger
```

Balance-table records (`tables.js`) expose the original fields under readable names AND keep the
per-player mutable arrays (`weaponLevel[8]`, `armourLevel[8]` of an object type, the weapon slot
swap of specials). Document the offset ↔ name mapping in `tables.js`.

## Deliverable of every port module

* The module itself, ESLint-clean (`npx eslint .`), no dependencies.
* A `PORT NOTES` block at the top: what was ported instruction by instruction, what was left as a
  `TODO(exact)` stub (throwing or logging), and every place where the disassembly and the docs
  disagree (the docs get fixed by the integrator).
* A small `test/engine-<module>.test.js` where a self-contained check is possible (table lookups,
  pure arithmetic such as `sincos`/`atan2`, path search on a tiny grid).

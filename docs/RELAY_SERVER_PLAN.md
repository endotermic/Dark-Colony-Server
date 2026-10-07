# Dark Colony relay server — implementation plan

A stand-alone Node.js relay server for `dc16.exe` multiplayer, written from scratch, hosted on Fly.io.
It replaces the in-game "ACT AS SERVER" host: players use **MULTI PLAYER WAR → CONNECT TO SERVER** and
type the server address. Nothing in the game binaries has to change.

Everything below that names a `0x4.....` address refers to `DC - Classic/dc16.exe` and is backed by
`docs/DC16_NETWORK_PROTOCOL.md` (the protocol reference, next to this file) or by the disassembly
checks done while writing this plan. The game folder, the full disassembly (`dc16.asm`, produced with
`dumpbin -ALL -DISASM`) and the Ghidra project live in the sister repository
[Dark-Colony](https://github.com/endotermic/Dark-Colony); this repository holds the server.

---

## 1. Requirements (as agreed)

| # | Requirement | How the plan meets it |
|---|---|---|
| R1 | Node.js, from scratch, no game code changes | Single process, zero npm dependencies (`node:net`, `node:crypto`, `node:test`) |
| R2 | Hosted on Fly.io, TCP | One always-on machine, dedicated IPv4, ports 8888 + 8889 |
| R3 | 8 players | Slots 0..7; slot 0 is the fake host, slots 1..7 are real TCP clients |
| R4 | Slot 0 = fake human player **"Mercenary"**, always a fake human (never handed to the AI) | Server emulates the host client for slot 0 (name, race, colour, team, status). `FAKE_PLAYERS` (1..7; default 2 from 13 to 19 Sep 2026 = AI Mercenary plus **AI Marauder**; **default 1 since 19 Sep 2026** = the one master bot, maintainer: "by default only the bare minimum") adds further fake humans in random slots; since 19 Sep 2026 the players raise the count per room with the lobby chat command **`/botcount N`** (§19.10). Since 12 Sep 2026 the maintainer wants the fakes to **play** ("alive bots", §19): they stay human slots on the wire, the server issues their commands; since 13 Sep 2026 every fake is a bot (§19.8) |
| R5 | Ignore command `0x08` (checksum) | Dropped, never queued |
| R6 | Very strict sync: commands only travel inside the frame that carries the `0x02` sync command; identical bytes, identical order for everyone | One sync frame per server step: `[UNTIL][cmd…][0x00]`, broadcast byte-for-byte to all clients (only the per-connection sequence nibble differs) |
| R7 | Start the game with the READY button (the chat word `ready` of the first design was dropped on 7 Sep 2026: the button works for every joiner, the eighth slot included) | When every real player has status 2 and there are at least `MIN_PLAYERS`, a countdown runs and the server frees the fake slots from status 1 (`'h'(0, q)`), which makes every client leave the lobby (§6.3) |
| R8 | Default map **ARMAGEDDON** (8-player desert) | `'i' "D8PLAY01.SCN", "Armageddon"` |
| R9 | Random placement for every new game | Joiners get a random free lobby slot; see §7 for why this is the only lever |
| R10 | Disable cheats by not broadcasting | `0x0E` cheat texts, `0x04` flag toggles, `0x03` are dropped. `0x0F` is **not** a cheat: it is the diplomacy screen's "give 1000" (F47) and is relayed since 13 Sep 2026 |
| R11 | 150 % game speed (200 % until 10 Sep 2026), clients cannot change it | Server sends `TICK_SPEED(44)` itself and drops `0x11/0x12/0x13` from clients |
| R12 | Clients may drop out or misbehave; a client that does not answer every message correctly is removed and everybody is told it left the lobby or the battle | Per-phase expected answers, deadlines and violation rules (§9); the eviction broadcasts `'h' 0` + `DISCONNECT` in the lobby and a `DISCONNECT` inside the next sync frame in battle |
| R14 | **Bots run only on the relay, never on a player's machine** (maintainer, 27 Sep 2026: "bots must always run on relay! never allow to run them on customer machine!") | No lobby slot is ever typed computer (`FILL_EMPTY_WITH_AI` removed; clients' `'j'` type messages are dropped), and no `DISCONNECT` is sent during a battle: a leaving player's base becomes a server bot (§19.9) or, without the engine, stands idle (§8.4). The game's own AI (`0x41DBE0` takeover, `FILL_AI_TYPE` slots) is reached only by replayed recordings |
| R13 | Seven rooms, each with its own map, chosen by the player inside the game's own lobby screen (added 7 Sep 2026, version 2.1) | A room-selection lobby ("hall", §17): the seven player rows that are not the player's own show the rooms, numbered 1..7 in place (F33), with the map name, player count and availability scrolling after the fixed number; the map line repeats the selected room (nothing is preselected since 12 Sep 2026: it asks for a room number until one is typed); chat commands select a room, READY joins it. The name may be typed in the hall and follows the player; race, colour and team cannot be changed there |

---

## 2. Facts from the binary that shape the design

| # | Fact | Evidence | Consequence for the server |
|---|---|---|---|
| F1 | Frame = 2 header bytes (12-bit length incl. header and terminator, 4-bit sequence) + commands + `0x00`. Max 1024 bytes. Client reads with `strict=1`: a wrong sequence is a fatal error, `seq == previous` is silently skipped. | `0x43AE8C`, `0x421648` | Per-connection outgoing sequence counter, incremented on every frame, including broadcasts |
| F2 | The first frame a client accepts is `'d'(i16 15, i16 slot)` within 25 s. | `0x4108DB` | Send it right after `accept` |
| F3 | Every client leaves the lobby and starts the game the moment **no slot has status 1** (present-but-not-ready). There is no start message. The lobby init sets slot 0 to status 1 locally (`0x410E05`), which is why a fresh joiner does not exit before the dump arrives. | `0x411CF1`–`0x411D23`, `0x412015` returns 1 | Mercenary in slot 0 with status 1 holds everyone in the lobby. The start signal is **`'h'(0, 0)`**, not `'h'(2, 0)`: a status-2 message goes through the colour-lock check (F4) and was refused on one of two clients in the live test of 6 Sep 2026 (both humans showed ready, the client stayed in the lobby), a status-0 message is applied unconditionally. The game start reads only slot types, so status 0 on slot 0 is harmless |
| F4 | The READY button is refused client-side only when the slot's colour is locked by another ready slot (table `ss+0x248[colour]`). | `0x4115EB`–`0x41161B`, `0x40F2B8`, `0x40F315` | Keep colours unique per slot; nothing else blocks readiness |
| F5 | The lobby CD flags (`'o' 8+p`) are used for exactly two things: (a) a per-slot CD icon in the lobby (controls `0xB4+p`, refresh routine `0x40F8CC`) and (b) the CD rule (un-ready all while `#CDs < ceil(#humans/2)`), which runs **only on player 0** (`ss+0xA254` is the own player number, set from `'d'` at `0x410A01`). Neither the READY button handler nor the `'h'` handler read them. | `0x410801`, `0x4115EB`–`0x41161B`, `0x40F23C` | With the server in slot 0 the CD flags cannot block anybody. Broadcasting `'o'(8+p, 1)` for every slot is harmless and gives everyone a CD icon, so the server does it (§6.2), but it is cosmetic and not a fix for a stuck READY (§6.5) |
| F6 | In-game receive loop classifies every frame by its **first payload byte only**: `0x01` execute all held now, `0x02` echo + arm `until`, `0x04` a∈{1,2} pause flag and frame discarded, anything else = stored as a held command frame. | `0x41E752`–`0x41E7BE` | `UNTIL` must be the **first** command of the sync frame |
| F7 | The dispatcher runs all commands of a held frame back-to-back; `0x00` must be the last byte; type ≥ `0x1C` or unknown = frame error. | `0x41E06C` | Commands placed after `UNTIL` execute in the same tick as the marker |
| F8 | The `UNTIL` reader needs ≥ 8 bytes and asserts it consumed exactly 8; trailing bytes are ignored. | `0x420F34` | Trailing commands in the same frame are safe |
| F9 | Executor runs whole frames and stops after the frame whose `UNTIL` handler cleared `gs->until` (that handler asserts `until == gs->until`, sends `UNTIL(-1, until)`, sets `gs->until = -1`). Pacing then re-arms from the next held frame whose first byte is `0x02`. | `0x41E0D8`, `0x41CD34`, `0x41E36E`–`0x41E3E3` | Exactly one `UNTIL` per frame; `until` values strictly increasing; several sync frames may be in flight |
| F10 | On receipt of `UNTIL(a, until)` the client sends back a **rebuilt** 9-byte `UNTIL(a, until)` frame (echo); when it reaches `until` it sends `UNTIL(-1, until)`. | `0x41E4B4`–`0x41E544`, `0x41CDC7` | Consume both; `a ≠ -1` = latency sample, `a = -1` = `clientTime[slot] = until` |
| F11 | Options screen speed percent → `TICK_DESSPEED(6600 / percent)`; only `0x11 TICK_SPEED` changes `gs->tick_ms`; clients compute `TICK_SPEED` from the `TICK_MAXSPEED` reports they receive. | `0x432CD3`, `0x41DD6C`, `0x419830` | **150 % = 44 ms** per tick since 10 Sep 2026 (the single-player default of the patched exes; 200 % = 33 ms, the game's own minimum `0x419883`, until then; the patched exes were back at the stock 66 ms from 21 Sep to 1 Oct 2026, when the maintainer asked for the `speed` fix again - display doc §10.58). Server sends `TICK_SPEED(TICK_MS)`; client `0x11/0x12/0x13` are dropped |
| F12 | Start positions: at game start each client seeds the game RNG from a global that is never written (always 0), lists lobby slots with type ≠ 3 in ascending order into 8 entries padded with −1, Fisher–Yates-shuffles `N = filename[1] - '0'` entries, and the position of a slot in the shuffled list is its game player index (= start location). | `0x4014F8`–`0x40159F`; RNG `0x4120E0/0x4120F0`, table `0x488F20` | The shuffle is the same every game. Randomness can only come from **which lobby slots are occupied**. With `k` occupied slots the same `k` start locations are always used and only the assignment of players to them varies, unless all 8 slots are occupied (§7) |
| F13 | Cheats: in-game chat `0x0E` text after `':'` equal to `we need equipment`, `I'm fighting for that equipment`, `slag net`; `0x04` with `a ∉ {1,2}` toggles debug flags; `0x03` spawns objects and has no legitimate sender. (`0x0F` = +1000 P7 was listed here as a cheat until 13 Sep 2026; it is the money gift of the diplomacy screen, F47.) | `0x41DA2C`, `0x41CE9C`, `0x41DBB0`, `0x41CF08` | Drop them |
| F14 | `0x08` sync check; sent every tick by the connected human with the **lowest network slot that is not marked lost** (`0x419F1B`–`0x419F54`). With Mercenary in slot 0 that is always Mercenary, which has no client, so **nobody sends checksums** and the game's own desync detection is inert. A receiver that detects a mismatch prints "sync error" and fails an assertion, i.e. terminates (`0x44AC94`). | `0x41CE74`, `0x419F4B` | Drop (R5), always. Decision of 7 Sep 2026: checksums are neither generated (fake human in slot 0) nor forwarded, the game runs fine without them; the temporary diagnostic flags used for the two-player sync test were removed. Since 11 Sep 2026 the server can compute the checksum itself (§18, F38–F41) and `MERCENARY_SLOT` can make a real player the sender for verification (F40) |
| F15 | `'i'` carries the scenario file name relative to `scenario/mplayer/` (client formats `"scenario/mplayer/%s"`) and the title. **The title is not free text**: the host builds it with `sprintf("%-43s (%d Player %s)", name + "\n", players, "Desert Map ")` (format string `0x48319C`, terrain strings `0x483190`), and every lobby client reads `title[45]` as the map's player count (`0x41141F`): when that digit is smaller than the number of occupied slots the client clears the scenario and un-readies everybody (`0x41143B`–`0x41144C`). The file name's 2nd character is read as the player count at game start (`0x401504`). | `0x40FB5C`, `0x410733`, `0x41141F` | File `"D8PLAY01.SCN"`, title `"Armageddon\n" + 32 spaces + " (8 Player Desert Map )"` (66 chars, `(` at index 44). A plain "Armageddon" made every real client send `'i' "", ""` plus un-ready messages a second after joining (live test) |
| F16 | Names: 16 chars + NUL. Buffers: lobby receive 1416, in-game receive 1024, held commands 7168. | `0x41F701`, `0x41E65B` | Frame budget in §8 |
| F17 | Original server constants: tick 66 ms, look-ahead 8 ticks, stall when `until − min(clientTime) ≥ 200`, ≤ 255 ticks per step, `DISCONNECT(slot)` in-game → AI takes over. | `0x40B7CC` | Reused, with tick 44 ms (33 ms until 10 Sep 2026) |
| F18 | Client sockets are blocking with Nagle on; lobby clients send `'q'` every 700 ms; in-game they echo every `UNTIL`. | §2.1/§6.1 of the protocol doc | `setNoDelay(true)` on the server; liveness = "bytes seen recently" |
| F19 | The in-game `DISCONNECT`, `TICK_SPEED` etc. are ordinary held commands (first byte ≠ 1/2/4), i.e. they execute at the next `UNTIL` boundary. | F6 | Put them inside sync frames like any other command |
| F20 | Leaving the lobby does not release the colour lock: the lobby `DISCONNECT` (`0x40F5B0`) and `NUKE` (`0x40EF84`) handlers reset type, status and CD flag but never touch the lock table `ss+0x248`. Only an `'h'` with status 0/1 for a slot that was ready clears its colour's lock (`0x40F315`); the whole table is cleared only when the lobby screen is entered (`0x410D4A`). | all writes to `+0x248` in setup.c | If a ready player disconnects, its colour stays locked on every remaining client and nobody with that colour can become ready. The server must broadcast `'h'(0, slot)` **before** `DISCONNECT(slot)` |
| F21 | An unknown message type in the lobby lands on the dispatch table's default entry, an assertion (`0x40F560`, setup.c:915) that terminates the game (`0x47C02E` → `0x47BEB7`). | table `0x488E54` | Send only the 17 known lobby types to a lobby client. In-game, unknown types are merely dropped (`0x41E6CB`) |
| F22 | The lobby loop also clears the scenario and un-readies everyone when more slots are occupied than the scenario's player-count digit allows. | `0x41141F`–`0x41144C` | Cannot fire with an 8-player map and 8 slots; if `MAP_FILE` is ever changed, its 2nd character must be ≥ the number of occupied slots |
| F23 | What a healthy client sends on its own: `'o'(8+p, cd)` right after it accepted `'d'`; `'q'` every 700 ms, but only from the lobby loop, so nothing while it loads the map; `'v' MREADY` once after leaving the lobby; in-game an echo of every `UNTIL` before executing anything, and `UNTIL(-1, u)` when it reaches `u`. Verified live: the first message is indeed the CD report, the outgoing sequence starts at 0 and increments per frame (including keep-alives), several commands are packed into one lobby frame, and in battle the client sends `TICK_MAXSPEED` and `TICK_SPEED(66)` about once a second. | `0x410A10`, `0x411C2A`, `0x41E544`, `0x41CDC7`, live log | These are the answers §9 waits for, phase by phase |
| F24 | `MREADY` carries the **game player index** after the client-side start-position shuffle, not the lobby slot (`gs+0x7D3C`, `0x401986`); the original server only checks the state byte. | `0x401992`, live: slot 5 reported player 1, slot 2 reported player 7 | Accept any player 0..7, require state 2 and no duplicate |
| F25 | Lobby init on every entry (`0x410C48`–`0x410DA0`): all 8 slots get type 3, colour 0, race 0, team = index, lock flag 0, and the colour-lock table entry is cleared; then slot 0 status := 1. Colours are set only by `'l'`, which applies unconditionally (`0x40F030`), as do `'n'` team (`0x40F0A0`) and `'f'` race (`0x40EE10`). Wire order of these two-byte messages is (value, player). | disassembly | The join dump must carry `'l'` for every slot (it does); default colours are all 0 until then |
| F26 | The lobby chat handler appends a line to a 2 KiB buffer and word-wraps at the 41-column chat window (`0x40ECE4`); long lines are safe. The chat input allows 255 characters (`intrface/MULTIE`). | `0x40ECE4` | Mercenary's lines may exceed 41 characters |
| F27 | ~~Only Classic `dc16.exe` plays over the network; the Council Wars executable has no working network play.~~ **Revised 25 Sep 2026 (F65):** the Council Wars executable is the same network program (code, protocol, lockstep, checksum, RNG and game-state layout identical to Classic's, verified by a relocation-aware byte comparison of the untouched exes) and its patched build "Dark Colony Ultimate.exe" reaches it through MULTI PLAYER WAR. Two data differences made it desynchronise against Classic clients and against this server's checksums; both are fixed in the patched build (F65). Never played over the network yet. | maintainer (11 Sep); `dc16.asm`/`dcexp16.asm`, `DC16_DISPLAY_AND_RESOLUTION.md` §10.39 | Mixed rooms (Dark Colony + Dark Colony Ultimate) are expected to work with the patched builds of 25 Sep 2026 or later; the untouched `ENGEXP16.EXE` still differs (Gray commander rally, F65) |
| F28 | `CHEAT(a, b)` with `a ∉ {1,2}` toggles the client flag `gs+0x46F50+b` (`b` 0..3, handler `0x41CE9C`); the chat cheat "slag net" toggles flag 0 the same way (`0x41DB9E`). Flag 1 is the pause flag, flag 2 has no readers, flags 0 and 3 are tested at the end of the fog-of-war mask routine (`0x445A77`, `0x445A89`), which then reveals everything (`0x4457D0`): **flag 0 = full map view**. All four flags are cleared at game init (`0x40C40F`) and stored in save games. | disassembly, confirmed live 7 Sep 2026 | Debug mode puts `CHEAT(0, 0)` into the first sync frame, so every client sees the whole map from the first tick (the maintainer confirmed the full map was visible from the start). **The flag is not display-only** (replay test, 18 Sep 2026, §18.7): a client that alone had it desynced from a recorded battle at tick 3944 (`sync error: time 3944, net 14336, me 14360`), the same replay without the flag ran to the end. Whatever reads the revealed fog mask (targeting of the revealed units, most likely) changes the simulation, so the flag must be set on every machine or none - as debug mode does |
| F29 | The lobby screen (`INTRFACE/MULTIE`): the eight player-name fields are `in_text` controls of **16 characters** (`x=247`, rows 19 px apart) with the `immediate` flag (the own field is editable, every keystroke goes out as `'g'`); the type and race columns are 6-character read-only fields derived from the slot's type and race values, not free text; the map line (`in_text 26`) is 55 characters wide; the chat window is 41 columns × 10 lines, the chat input holds 255 characters. | `INTRFACE/MULTIE` lines 35–51, 113–123, 181–217 | The only free text per row is the 16-character name. Longer texts scroll through it (marquee, §17.2). In the hall the own row is a room row too, so name edits are dropped there (R13) |
| F30 | `'d'` is **not** in the lobby dispatch table (`0x488E54`: `0x01 'e' 'f' 'h' 'g' 'i' 'j' 'k' 'l' 'm' 'n' 'o' 'p' 'y' 0x10 'q'`); it is read only by the join wait (`0x4108DB`). An unknown type in the lobby terminates the game (F21). The meta-server join path receives the state dump **without** `'d'` after `'y' INIT_ME` (protocol doc §6.2), and every existing player receives the full dump again on each join (live, 6–7 Sep 2026). | table `0x488E54`, protocol doc §4.2/§6.2, live logs | A client's slot number is fixed for the whole connection: it can only join a room where that slot is free (§17.5). A second full dump (new `'i'`, new rows) in the middle of the lobby is exactly what the game was built to accept |
| F31 | Every multiplayer map file `SCENARIO/MPLAYER/*.SCN` starts with three strings: the terrain file (`desert.bts`, `jungle.bts`, `atlantis.bts`), the base name and the **display name** the game's own host puts into the `'i'` title. Classic ships 56 of them: 10 desert and 9 jungle 2-player, 10 desert and 7 jungle 4-player, 2 jungle 6-player, 10 desert and 7 jungle 8-player, plus one 2-player Atlantis map. | the game folder, 7 Sep 2026 | `src/maps.js` holds the table (generated from the files); `ROOMS` lists maps by file name and the server builds the title from the table (F15) |
| F32 | The client has a dormant meta-lobby: when the first frame is `'r'(9, id)` instead of `'d'` it enters a screen defined by `INTRFACE/METAE` (a 392×258 scrollable list, an 11-character name field, MENU and READY buttons) and speaks the `'r' 's' 't' 'g' 'u' 'v' 'w'` protocol (protocol doc §4.2); the server half never existed in the game. | `INTRFACE/METAE`, protocol doc §4.2 | Considered as a native room browser and **not used**: never exercised, unknown UI state. The hall (§17) reuses the normal lobby screen instead |
| F33 | The lobby `'g'` NAME handler (`0x40F398`, dispatch table `0x488E54`) copies the name into the slot record (17 bytes, `0x406948`) and then repaints the row's text field (`0x423E74`) **only if the player is not the client itself** (`ss+0xA254`). The own name field is an editable `in_text` with the `immediate` flag (F29): every keystroke goes out as `'g'` and the field shows what was typed regardless of what the server answers. | disassembly `0x40F3FC`–`0x40F415`; live 7 Sep 2026: the own row never showed the hall's room text, typing changed it | The server cannot paint the client's own row and cannot stop the player from typing there. The hall therefore shows the player's name in that row and accepts name changes (they follow the player into the room); seven rows remain for rooms, hence `MAX_ROOMS = 7` |
| F34 | The lobby loop reads **at most one frame per iteration** (`0x411132`: one call of the frame reader, not a loop; on data it calls the dispatcher `0x40F918` and goes on with the iteration), and the dispatcher runs **every command of the frame** (loop at `0x40F929`–`0x40F975`: type byte → table `0x488E54` → handler, until the `0x00` terminator; an unknown type is "SETUP_COMMANDS BAD"), then calls `0x40F6AC` once per frame. The iteration rate is that of the UI loop. | disassembly; live 7 Sep 2026: seven one-command frames per 200 ms scrolled fast for a moment and then slower and slower (the client fell behind and queued frames) | Send one **frame** per update with all its commands inside: the marquee step, the hall dump, the room dump. Multi-command lobby frames are also what the original server relays when a client packs `'o'`+`'q'` (F23). `PACK_LOBBY_FRAMES=false` restores one command per frame |
| F35 | The lobby chat handler (`'e'`, `0x40ECE4`) copies the chat control's current text (control 24, up to `0x800` bytes) into a local buffer, appends `"\n"` + the received string, word-wraps by inserting `'\n'` at the last space once a line reaches **width − 1 = 40** columns (control width from `0x423F24`), and then, while the text does not fit the control (`0x424608`), **drops the first line**. The control shows the last ten visual lines; nothing else is kept. The received string is appended as it is: the `"Name: "` in front of a player's line is the sending client's own convention, not something the handler needs. | disassembly `0x40ED15`–`0x40EDE5`; F26, F29 | The server can paint the whole window: ten lines of at most 40 characters replace what is shown. Lines from the relay carry no name (maintainer, 7 Sep 2026). The greeting is kept at the top by repainting the window on every chat event (§17.8) |
| F36 | The big READY button is `checkb 133` (`INTRFACE/MULTIE` line 351), a checkbox with its own pressed state. Its click handler (`0x4115A9`–`0x411636`) sends `'h'(2, own)` on the "checked" event and `'h'(1, own)` on any other event; the only code that changes the button's state is the click itself and the client-side refusal when the own colour is locked (`0x4272A8(ui, 0x85, 0)` at `0x411608`). The `'h'` message handler drives the **row** checkbox `16 + player` only (`0x4272A8(ui, 0x10 + player, status == 2)` at `0x40F38B`); the lobby refresh only enables or disables control 133 (`0x424514` at `0x40FF96`/`0x40FFC5`). No lobby message reaches the button's state. | disassembly; all five logged room entries of 7 Sep 2026: the first READY press inside the room sent status 1, the second status 2 | After READY in the hall the button stays pressed and the server cannot release it. The room therefore seats a client from the hall **ready** (status 2, F20 lock check applied), so that the pressed button is true; one click un-readies for colour or race changes. A direct join (`HALL=false`) is present-not-ready as before |
| F38 | **When a sync frame is executed.** The client's pacing loop (`0x41E268`) runs, per elapsed `tick_ms` while `until >= 0`: if `game_time + 1 == until` it calls the held-frame executor (`0x41E0D8`, mode 1), which dispatches whole held frames in order (every command of a frame, the `UNTIL` marker included) and stops after the frame whose `UNTIL` marker cleared `until`; then `game_time++` (`gs+0x94C`) and `game_tick` (`0x419978`). Afterwards, if `until < 0`, the next held frame starting with `0x02` supplies the new `until`. | `0x41E268`–`0x41E478`, `0x41E0D8`–`0x41E25C` | The commands of frame `UNTIL(a, u)` take effect at game time `u − 1`, before the tick that makes it `u`; ticks between two frames run without commands. The server engine mirrors exactly that (`src/synccheck.js`) |
| F39 | **Checksum time.** `game_tick` ends with `record(gs, gs+0x94C)`: the checksum of the state *after* the tick is stored under the tick number that was incremented *before* the tick. The sender (`0x419F1B`: the human player — AI type 0 — with the smallest non-negative network id, if it is the local player) sends `0x08 (history[time], time)` with that same `time`. The receiver (`0x44ACF8`) asserts `time <= game_time` ("AUGH check sync time %ld > game time %ld") and `game_time − time < 256` ("records start at %ld"), then compares `history[time % 256]`; a mismatch prints "sync error: time %ld, net %d, me %d" and aborts. | `0x419F06`–`0x419FA9`, `0x44AC68`, `0x44ACF8`–`0x44AE7D` | A `0x08` inside frame `UNTIL(a, u)` is checked at game time `u − 1`, so it may carry any tick `t` with `u − 256 <= t <= u − 1`. The server sends, in frame k+1, the checksum of `until_k`, which it has just simulated; never tick 0 (no history entry) |
| F40 | The lowest network id decides who sends checksums (F14, F39): with the fake host in slot 0 nobody does, and a real player in slot 0 would be the game's "host" in the clients' eyes (`'d'` player 0). | F14, `0x419F1B` | `MERCENARY_SLOT` moves the fake host for diagnostic games so that a real player becomes the sender; slot 0 then stays empty (never seatable), the fake host is never relocated by a joiner |
| F41 | **Start shuffle RNG.** `run_game` seeds the game RNG with `srand([0x4A469C])` (`0x4120E0`: index = seed & 0xFF; the global is lobby `VAR` 7 — the `'o'` array `ss+0xA670` lives at `0x4A4680` — which the UI never changes and the server sends as 0, so index 0; the scenario loader re-seeds with the same value), then for `k = 0 .. N−1` (N = the title's player digit) swaps entry `k` of the 8-entry slot list (occupied slots ascending, padded with −1) with entry `k + rand() % (N − k)`, where `rand()` (`0x4120F0`) is `index = (index + 1) & 0xFF; return table[index]` over the 256 int32 values at `0x488F20`. The same RNG drives the whole simulation afterwards, so the shuffle's `rand()` calls are part of the deterministic sequence. | `0x4014F8`–`0x401582`, `0x4120E0`, `0x4120F0`, `0x488F20` (`data/dc16-tables.json`) | The engine starts from RNG index 0 and performs the shuffle itself; MREADY's game player index (F24) is a free check of it (`SyncCheck.onMready`) |
| F37 | A scenario is `.SCN` (text: terrain, base name, display name, day/night, eight TEAM blocks with start position and city origin, object list `x z type player a b`), `.MAP` (u32 w, u32 h, w×h × {u16 bg, u16 fg}, w×h × u16 attribute; bit 9 = blocking terrain, rows stored `z = h-1-r`), `.MTG` (trigger ids), `.PTH` (256×256 family routing matrix + w×h family bytes in z order, 0 = impassable), `.TRO` (trigger script), `.POP` (editor-only eruption data), `.OVH`/`.O16` (96×84 minimap cache the game regenerates). Tile numbers are editor indices remapped through the terrain's `.BTS`. The lobby only ever needs lines 1–3 of the `.SCN` (F31). | `mapit.c 0x4530C0`, `path.c 0x442D8C`, `mobiles.c 0x41BAF0`, `renat.c 0x43FD1C`; all 198 shipped scenarios of both games parse; `docs/DC16_MAP_FILES.md` | `tools/map2json.js` converts scenarios to JSON, `maps/*.json` + `maps/index.json` hold the seven maps of the default `ROOMS` (others on request; grids indexed `[z][x]`, same coordinates as the game's commands), so a later server feature (position validation, start-location logic §7, a map browser) reads JSON instead of the binaries |
| F42 | **The READY button is disabled while the scenario is empty.** The lobby refresh (`0x40FF80`) enables control 133 (F36) only when the scenario file name (`gs+0xA264`, set by `'i'`) is non-empty and the word at `gs+4` is 0; otherwise `0x424514(ui, 0x85, 1)` greys it out (`0x40FF96`; enable at `0x40FFC5`). The `'i'` handler stores the title at `gs+0xA268`; the player-count check of F15 (`title[45]`, `0x41141F`) is skipped when the title is empty (`0x411416`), so an empty title causes no un-ready storm. `'i' "", ""` is the client's own "no map" state (F15 example). | disassembly `0x40FF80`–`0x40FFD0`, `0x411416`; real client 12 Sep 2026: empty map line, READY greyed | The hall sends `'i' "", ""` while no room is selected (maintainer, 12 Sep 2026): the map line is empty and the client itself disables READY until `/N` puts the room title there; the server-side refusal of READY without a selection stays as a guard |
| F43 | **The AI is a command generator.** `ai_think` (`0x41AD30`) decides and then calls the ordinary command builders (`0x40C50C` → `0x09`, `0x40C538` → `0x0A`, `0x40C7D4` → `0x07`+`0x05`, `0x05 0x0D` deploy); every builder ends in `0x421648`, which during `ai_turn` runs in "local command mode" (byte `0x4AF090`, set by `game_tick` around the call) and executes the bytes at once through the same handler table `0x48949C` the sync frames use. The AI never sends anything; nothing in a client distinguishes a human's command from an AI's. | `0x419FA9`–`0x419FC0`, `0x421648`, `0x41E06C`; `docs/DC16_AI.md` §3 | A server may generate the same bytes for a fake human and put them into its next sync frame (§19) |
| F44 | **Money is local.** The `0x09`/`0x0A`/`0x0C` handlers add to `P.SPENT` and never touch `P.MONEY`; the sender deducts before sending (build menu `0x43332F`, Krusty goal actions `0x4562B0`/`0x456408`). Money is not in the checksum. | `0x41CAA4`, `0x41C9C8`, `0x41CBD4`; `DC16_AI.md` §3 | A fake player's money exists only in the server engine; the bot deducts there (§19.1) |
| F45 | **AI schedule.** `ai_turn` runs on ticks with `TICK & 3 == 0`: tick 4 → every AI player; afterwards one *slot* per call (`rr = (rr+1) % 8`, humans waste the call), so player `p ≥ 1` thinks at ticks `4 + 4p + 32k`, player 0 at `36 + 32k`. AI commands of tick `t` are applied after `record(t)` and enter checksum `t+1`. | `0x41AE38`, `0x419FB6`; `DC16_AI.md` §2–§3 | Bots think every 32 ticks at the same phases; a bit-exact `ai.js` must run at that point of the tick (§19.6) |
| F47 | **`0x0F` has a legitimate sender: the diplomacy screen's "give 1000" button.** The in-game diplomacy panel (`0x4334DD`: button ids 154..195, `(id - 154) / 6` = row = the other players in order, `% 6` = column) sends, for column 4, `0x0F(target)` through `0x40C730` **after** checking `money > 1000` and deducting 1000 locally (`0x43352B`/`0x43354E`); the handler `0x41DBB0` adds 1000 only on the machine whose local player is `target`. Columns 0 and 1 toggle the alliance / shared-vision bit towards that player (`0x41E94C` get, `0x40C598` -> `0x0D(me, target, column, !current)`). | `0x4334DD`-`0x4335E7`, `0x40C730`, `0x41DBB0` | The relay must forward `0x0F` (until 13 Sep 2026 it struck it as a cheat, so a gift between humans vanished and the giver still lost the 1000). For the Mercenary the engine's ledger receives it (local player -1: `cmdBonus` adds for any target) |
| F48 | **Alliance and vision are per-direction bits that must match.** `0x0D(pa, pb, which, on)` sets bit `pb` of byte `pa` in matrix `which` (`0x41E928`); `game_tick` derives the alliance byte `gs+0x46F54[10a+b]` and the vision mask bit from `0x41E970(matrix, a, b)` = bit `b` of byte `a` **and** bit `a` of byte `b`. Targeting (`collide.c`) and the shared-vision mask read those derived values. A one-sided "alliance" therefore changes nothing until the other side sets its bit too. | `0x41D7AC`, `0x41E928`, `0x41E970`, `0x419BB0` | The Mercenary sets its two bits towards its ally and tells the ally in chat to set theirs; its own rusher never targets the ally regardless (`isAlly`), but the game's auto-fire only stops once the bits match |
| F49 | **End of a multiplayer battle.** Every UI frame the client (`0x40ACD5`, game types 1/2) calls `game_over 0x40E260(gs)`: walk players 0..7, `first` = the first one for which `player_alive 0x40E1D4(gs, p)` holds; every further alive player must be **mutually allied** with `first` (`0x41E970(matrix 0, first, p)`, both bits) or the game goes on; with no unallied alive pair it returns 1. `player_alive` = any living object (`life ∉ {0, 10}`) of team `p` other than mines (types 45/46), deployed towers (41/42) and the city's tower slot (`obj < 120 && obj % 15 == 5`): buildings, workers and mining towers all count. When the check fires the client sets `gs+0x471C8 = 1`, and `stat(0, 0)` = its own player if it is still alive (`ui+0x13B == 0`, set earlier when `player_alive(me)` first failed, `0x40AC2A`) else 8; `run_game` then shows the results screen (`0x404964`), which prints **Victory** (`0x482430`) when `stat(0, 0) == local player`, **Defeat** otherwise (`0x404A83`). Alliances therefore end the game: once all alive players are mutually allied, every one of them has won. | `0x40AB40`-`0x40AD3C`, `0x40E1D4`, `0x40E260`, `0x404964` | **The check is a star, not a set of pairs the player is in**: every alive player is compared with `first` (the lowest alive game player index), so with two rival bots alive a lone player allied with both does NOT win — the two bots are not allied with each other (first live test 13 Sep 2026, §16: matrix rows 1↔6 and 6↔7 set, 1↔7 clear, `game_over` false while both deals overlapped). Victory by alliance therefore needs the bots that share a paying ally to ally with each other too (or one bot left); done the same day as the "pacts" of `Bots.syncPacts` (§19.9). A lone player against a single remaining bot wins for 1000. The server does not need an end detection of its own: the clients leave when they show the results screen and the room resets |
| F50 | **What survives on Fly.** The machine (`d8927e5c5ee3d8`, no volume) keeps nothing on disk across a restart or deploy, and `fly logs` shows only the last 100 lines. But Fly's Logs API (`GET https://api.fly.io/api/v1/apps/<app>/logs?next_token=<ns>`, header `Authorization: FlyV1 <flyctl token>`, 100 entries a page, paging forward from `next_token` = a nanosecond Unix time) holds about **seven days** of the app's stdout: on 18 Sep 2026 it paged back to 12 Sep 01:18 UTC. The server wrote 602 lines / 119 KB in the 24 h before (10 battles started; mostly hall joins/leaves by port scanners and the 30 s stats lines). No line-size or rate limit is documented; the recorder keeps its lines below a few KB anyway. | Fly Logs API, checked 18 Sep 2026 | A battle recording that must be recoverable after a player's report has to be in the log and small: `RECORD_LOG` (§18.6). Recover within a week with `tools/logs2replay.js --fetch`; for longer keeping add a log shipper or a volume |
| F51 | **The object loop of `game_tick` re-reads its bound every iteration** (`0x419EA2..0x419EAB`: `cmp esi, [gs+7D40h]` = `MAX_OBJ`). An object allocated during the loop with a new highest index - a unit produced by a building the loop has already dispatched, a wildlife spawn, a drop - is dispatched in the tick of its creation; a recycled lower slot is dispatched the next tick. The engine cached the bound until 19 Sep 2026 and ran such newborns one tick late, which reordered their idle fidget `rand()` against the next tick's draws (desync of 18 Sep 2026 at tick 9469, §16). | disassembly + replay into the real game, 19 Sep 2026 | `engine.js` step 13 reads `MAX_OBJ` in the loop condition; regression test in `test/engine-game.test.js` |
| F52 | **The local pool zero-fills.** `smalloc 0x40C09C` clears every block it returns (`0x40C1B5..0x40C1BC`: `memset(block+0x10, 0, size)`), so Krusty's state (`krusty_alloc 0x44BD50`, 0x6C40 bytes, "Krusty AI") starts as all zeros except what the allocator writes; the attack ratio `kai+0x6C34`, which nothing initialises, is therefore 0 and the enemy-avoiding zone route `0x4579F0` never runs in an unscripted game. | `0x40C09C`, `0x44BD50`; `DC16_AI.md` §23 | The port allocates a zeroed Buffer and leaves `+0x6C34` alone; `avoiding_route` is not ported (assert if ever reached) |
| F53 | **The vent bit lives in the file-order row.** The loader sets attribute bit 4 (load bit 26, "a living vent here") through `map+4[z]` (`0x41C758`), and BOTH readers use the same table: the trigger primitive `m(x, z)` (`0x43D15C`) and the exhausted-vent assert/clear of `stateHarvest` (`0x413B87`, `0x413C28`). So `m(x, z)` is simply "vent alive at (x, z)". The port read the two sites z-ordered until 19 Sep 2026: the first exhausted vent of a game asserted (`gs->map->load[...] & (1<<ALIVE_MINE)`) and cleared the bit of a cell in the mirrored row, and `m()` looked at the wrong cell. Load bits are not in the checksum; the trigger result can be (a re-armed eruption draws `rand()`). Found by the Krusty bots' long self-play (vents ran dry after ~16 000 ticks). | `0x41C747..0x41C758`, `0x43D152..0x43D15C`, `0x413B61..0x413B87`, `0x413BFF..0x413C2C` | `grid.ventBitTest/ventBitClear` at both sites; `test/engine-renat.test.js` sets the bit as the loader does. The seven D8PLAY01 recordings of 11 Sep replay unchanged (their eruption trigger 40 also needs `s(4,0)==1`) |
| F54 | **AI schedule details.** `ai_turn` tests the literal `TICK == 4` (`0x41AE56`), not a start-relative tick; `ai_think` draws one `rand()` per personality pair whatever the weight, chosen iff `w > r·(cum+w)·C` with `C = 0x3F00002000400080` = exactly the double `1/32767`; a lobby slot of type 3 never becomes a type-4 player in multiplayer (the start-up loop skips empty records before the type test, `0x4016F2`), so a humans-only game draws nothing in `ai_turn` - which is why the engine matched real games without any AI code. | `0x41AE38`, `0x41AD30`, `0x48385C`, scenario.js `0x4016F2..0x401757` | `engine/ai.js` reproduces the schedule and the choice; the personality stubs for types 1/2 draw their `rand()`s and do nothing |
| F55 | **The battle-start camera is never clamped, and the 1024x768 build's first frame reads past the map for start rows near the far edge.** `proto.c` init sets `cam = start_tile << 8` from the local player's record (`player+0xBD0/0xBD4`, code `0x41ED11..0x41ED48`, into `ui 0x4AA9D0` +0x108/+0x110), then stores the bounds `[half, map_world − half]` into `ui+0x114..0x120` (`0x41EE66..0x41EEA9`; half = 0xE00/0xB80 after fix `resolution`, 0x800/0x700 stock) without applying them; only the render path clamps (`clamp2d 0x436668` from `0x40AF16`), and it runs after the client step. The first client step `0x40AAFC` computes `view origin = (cam − half) >> 8` (`0x40AB1C/0x40AB2B`) and calls the ambience picker `0x432040` → `0x445AA4(gs, x0, tiles_x, z0, tiles_y)`, which checks only `x0 ≤ W`, `z0 ≤ H` and then loops `z0..z0+tiles_y` through the vision-plane row table `map+0x804[z]`; rows ≥ H hold NULL → `test [edx],ecx` at `0x445B52` faults. Stock (16x14) needed a start within 7 rows of the far edge, which no shipped map has; at 28x23 every start row within 11 rows does: J8PLAY01 teams 0 (18,131) and 1 (120,132), D8PLAY01 3 (148,128), D8PLAY03 1 (80,130) and 2 (131,130), D8PLAY05 0 (43,128) and 1 (129,129), J8PLAY07 3 (142,126) … (map height 140; the low edge is protected by the `z0 ≥ 0` early return). A crashed client that Windows Error Reporting holds keeps its socket open: the relay sees `no echo for frame 0` after `ECHO_TIMEOUT_MS`, exactly like the sync assert. | `0x41ED11`–`0x41EEB8`, `0x40AAFC`–`0x40AB37`, `0x432040`, `0x445AA4`–`0x445BF0`, `0x436668`, `0x40AF16`; Fly log 19 Sep 2026 18:26 UTC + Windows Application Event 1000 (`dc16new.exe`, `0xC0000005`, fault offset `0x45B52`) | Fix `camera` (`tools/patch_camera.py`, 21 Sep 2026, both exes): the `call load_ambience` after the bounds stores (`0x41EEB8` / CW `0x41EF18`) goes through a 33-byte stub in the AUTO zero tail (`0x47F1DC` / `0x47F310`) that calls `clamp2d` on the camera with the freshly stored bounds and jumps on. The server cannot work around it (the client picks the seat→player shuffle and the start position); an evicted-at-frame-0 client is the signature to look for |
| F56 | **The game has no message loop; posted `WM_SYSCOMMAND`s are swallowed.** `frame_end` (`0x42F1C8`) pulls posted messages with five range-filtered `PeekMessageA(PM_REMOVE)` calls (mouse, keys, `WM_SYSCOMMAND` only to drop `SC_SCREENSAVE`, and the never-posted `WM_SETCURSOR`/`WM_DESTROY`) and dispatches none (`DispatchMessage`/`GetMessage` are not imported). Windows restores a minimised window by posting `SC_RESTORE`, so after Alt+Tab away/back the game is active but stays iconic; DirectDraw's exclusive-mode hook re-sets the display mode only during an activation of a non-iconic window, and an application-side `SetDisplayMode` afterwards makes every old surface unrestorable (`Restore` → `DDERR_WRONGMODE` 0x8876024B). Not a server matter, but "player vanished / black screen after Alt+Tab" reports are this. | `0x42F1C8`–`0x42F2AE`, `0x42E340` (wndproc), `0x42E84D` (`SetCooperativeLevel 0x51`), `0x42E060` (`restore_surfaces`) | Fix `restore` (`tools/patch_restore.py`, 21 Sep 2026, both exes): the `WM_SYSCOMMAND` peek hands everything but `SC_SCREENSAVE` to `DefWindowProcA` and after an `SC_RESTORE` runs `ShowWindow(SW_MINIMIZE)`+`ShowWindow(SW_RESTORE)`, the activation cycle the hook handles; `DC16_DISPLAY_AND_RESOLUTION.md` §10.28 |
| F57 | **A missing required file is an endless "insert CD" wait, not an error.** The file-open helper `0x4061DC` (`safefunc.c`) calls the display object's CD-prompt method (slot `+0x4C` = `0x42C0BC`, set at `0x42C3E5`; only caller `0x40630D`) when a *required* file is missing and the display is up; the method draws the sprite `intrface/insee` ("Please insert Dark Colony CD", a picture, not a string) at (235,220) and loops on `fopen(name)` until the file appears — with the CD path removed (`nocd`), forever. A player saw it because `ozi_ns/intrf_hd/` (the five OZI scripts and scene lists) had never been committed: OZI MISSIONS → NEXT on the story screen asked for the disc for `intrf_hd/hxscene.txt`. Not a server matter, but the signature "Please insert Dark Colony CD" on a CD-free build = a missing file. | `0x4061DC`–`0x406403` (`0x4062F2..0x406310`), `0x42C0BC`–`0x42C26D`, `0x42C3E5`, `INTRFACE/INSEE.SPR`; two player screenshots 21 Sep 2026 | `nocd` edit 9 (`tools/patch_nocd.py`, 21 Sep 2026, both exes): the method becomes the wave loader's error exit — error.log line `unable to open file <name>`, box "FILE NOT FOUND / <name>", exit; the five files committed; `DC16_DISPLAY_AND_RESOLUTION.md` §10.19 |
| F58 | **Sound files fail from a deep game folder.** The wave loader (`0x452A50`, CW `0x452AB0`; sound banks, briefings, ambience) is the only code that opens files through the Win16-era `OpenFile(name, &OFSTRUCT, OF_READ)` (IAT slot `0x4804C8`, four call sites in that function), whose `OFSTRUCT.szPathName` holds 128 characters: once `<game folder>\sound\xxxxxxxx.wav` exceeds that, every WAV open returns -1, the `nocd` error exit fires about 5 s after start ("FILE NOT FOUND / A sound file is missing - see error.log", exit) and `error.log` says `unable to open file ` with an empty name (the exit printed the CD buffer that `nocd` no longer fills). Every other loader is `fopen` → `CreateFileA` (MAX_PATH). Not a server matter, but a player whose install sits deep under `Downloads` never reaches the lobby. | `0x452A50`–`0x452C70` (Classic; opens `0x452AC4`/`0x452ADD`, error exit `0x452BCB`), IAT `0x4804C8` OpenFile / `0x4803F8` CreateFileA, thunk `0x47EF3C`; reproduced 22 Sep 2026 from a 161-character path, control from `subst Q:` clean | Fix `longpath` (`tools/patch_longpath.py`, 22 Sep 2026, both exes, requires `nocd`): the two live opens call a 22-byte `open_read` stub written over the dead first CD attempt (`CreateFileA(name, GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING)`, returns -1 on failure like `OpenFile`), the error exit names the file tried; `DC16_DISPLAY_AND_RESOLUTION.md` §10.29 |
| F59 | **The soundtrack is CD audio and the game has been silent without a drive.** Both discs are mixed-mode (data + audio tracks 2-5); the exe's `cdaudio` MCI module (`0x4510D0..0x451820`, CW +0x60, the only `mciSendCommandA` user) seeks to track 2 at battle start and plays to the disc's end, a 5-s poll restarts at track 2, `stop` at battle end; `MCI_OPEN cdaudio` fails without a CD-ROM drive -> the layer's "no CD audio" flag, no message. The music slider drives an aux CD-line volume that modern Windows lacks (`auxGetNumDevs() == 0`). The scene lists' per-mission playlists (`2 3 5 -1`) are parsed (`scenario.c 0x429BC0` -> `0x4A46C0`) but never read. Not a server matter. | `0x42F9F8`/`0x42FA9C`/`0x42FAC0`/`0x42FA80` (ddex4.c layer, display slots +0xB8..+0xCC), callers `0x404E60`, `0x41F09F`, `0x4320DB`, `0x401A41`; `0x4527F8`/`0x452870` volume; CD images scanned 22 Sep 2026 | Fix `music` (`tools/patch_music.py`, 22 Sep 2026, both exes): the module is rewritten in place as an MP3 player on MCI `mpegvideo` reading `MUSIC\TRACK02-05.MP3` (Classic) / `exp\music\track02-05.mp3` (CW), the eight tracks encoded from the CD images at 192 kbit/s ship in the repository, the slider sets the element's volume; `DC16_DISPLAY_AND_RESOLUTION.md` §10.31 |
| F60 | **The first published `widemap` build crashed on the first battlefield click (22 Sep 2026).** The spot-order stub read the map through `[eax+0x46F4C]`, but the order builder `0x40968C` receives the client object, whose game-state pointer is at `+0xC` (the pick `0x409850` reads `mov eax,[eax+0Ch]; mov esi,[eax+46F4Ch]`); dereferencing the garbage pointer raised an access violation (Windows Application log Event 1000, fault offset `0x5F58` Classic / `0x5F38` CW = stub +13; twice `0x2BF32`, the click marker's rect fill, when the garbage happened to be readable). No `error.log` entry - the WER box behind the full-screen surface looked like a hang. Not a server matter, but every player with the ZIP of `91e3f06` crashed at the first order. | `0x40968C` builder, `0x4098D4` / `0x40A1BE` callers, `0x409850` pick, stub `0x405F4B` (Classic; CW `0x405F2B`); Windows Application log 22 Sep 2026 17:11-17:36 | Fixed the same evening: `mov ecx,[eax+0Ch]` first (stub 49 bytes), patcher regenerated, published exes `a71d038b…` / `bb6a1e77…`, confirmed by the maintainer and by scripted clicks in both games; `DC16_DISPLAY_AND_RESOLUTION.md` §10.33 |
| F61 | **The untouched Council Wars exe cannot reach its main menu without a disc.** `main.c`'s menu init greys six widgets when the CD flag is 0 - ids 0, 1, 16, 4, 2, 5 through `set_greyed` (CW `0x424574`, Classic `0x424514`), which writes the greyed byte at `ip+0x88+0x34*id` without checking the type and then calls `widget_get` (CW `0x421CF8`), whose widget.c line 152 assert (`objects[i].type != unknown_obj`) fires because the shipped `exp/intrface/bintroe` has buttons 1, 3, 4, 5 `%`-commented out: `error.log` gets the assert line and the message box hides behind the exclusive-mode surface = an apparent hang. Classic's script defines all nineteen widgets, so the Classic original only comes up grey (ids 3 and 12 stay usable). Not a server matter: the patched exes clear the branch with `nocd`, and only they talk to the relay. | `0x404F18` call, `0x404F1F` `jne` (file `0x431F`), the six calls at `0x404F2B/3A/4C/5E/70/82`; asserts at `0x421D14` (line 151) and `0x421D98` (line 152); stock bytes of both exes read 23 Sep 2026 | Fixed in the data on the maintainer's instruction: `exp/intrface/bintroe` is now Classic's `INTRFACE/BINTROE` with the eight button rows 16 px lower (330/356/382/408, for the code-positioned credits box the untouched exe cannot move), so every greyed id exists; the PATCHED builds' copies get a five-row order on top of it (ACADEMY / COUNCIL WARS / LOAD CW GAME / OZI MISSIONS / LOAD OZI GAME | MULTI PLAYER WAR, ENCYCLOPEDIA, QUIT in rows 1/3/5, +12 px after rows 1 and 3 and between the columns, block re-centred) with the credits box shortened and moved above it; the OZI mode is two label renames (`OZI MISSIONS` in the PLAY INTRO slot, `OZI LOAD` in the SINGLE PLAYER WAR slot) in `build_ozi_overlay.py` and the patcher's `Edit-OziMenu`; `DC16_DISPLAY_AND_RESOLUTION.md` §10.35 |
| F62 | **The Council Wars executable can play the original Dark Colony campaign, and the two buttons for it cost one byte of code plus a fourth prefix mode.** The expansion build is the same program as `dc16.exe` (`DC16_SINGLE_EXE_MERGE.md` §2: the only code differences are the `exp/` overlay helper and the four immediates that decide which missions award the last two campaign medals, §5.3 - cosmetic), the scene-list chooser picks the Classic lists when `gs+0x14F4 = 0` and `gs+0x14F0 = 0` - which the menu already writes for every button at `0x405018` - and the shared game folder holds the complete Classic data set. The menu's accepted-id filter (`cmp edx,5 ; jle accept`, plus `0Ch` and `10h`) is what kept new buttons out: raising the 5 to 7 admits ids 6 and 7, the first free ones, and the old PLAY INTRO body leaves 59 NOP bytes for their handlers. Not a server matter. | filter `0x404F9E` (file `0x439E`), chain end `0x4050DB`, handler space `0x405102..0x40513C`, tail `0x47F340..0x47F3AA`, slots `0x4826D0`/`0x487DC8`/`0x482344`/`0x485E5C`, chooser `0x402FED`/`0x403C9D`, runner `0x401C08`, load screen `0x403AA4` | Fix `ozi` extended (`tools/patch_ozi_menu.py`, 23 Sep 2026): DARK COLONY (id 6) and LOAD DC GAME (id 7) write `dc/` / `dc/` / `save` / `save` into the four mode slots, so every file falls through to the Classic data in the game root and the load button lists the same `SAVE/` folder the Classic exe uses; `dc/intrf_hd/bintroe` is the only file in that overlay. `DC16_DISPLAY_AND_RESOLUTION.md` §10.36 |
| F63 | **Removing a screen's TTY without removing its destroy call underflows the game's single-instance TTY counter.** `main.c bintro` creates exactly one text window (the scrolling credits) and frees it on every menu click with `mov edx,1 ; call 0x429684`, which decrements the global count at `0x4D61C0`. The seven-row menu needed the credits box gone; with only the create removed the count went to -1, the next screen's TTY (the race overview's text) was built at index -1, that screen came up empty, and the corrupted neighbourhood took the interface-level teardown down with an access violation at `0x44DE14` (`mov [ebx+eax*4],0` over a bad base). The module allows one TTY at a time - its create asserts when the count reaches 2 - so every screen that makes one frees it the same way. Not a server matter. | create `0x4284A8` (counter at `0x4284BF`), destroy `0x429684` (`0x429694`), count `0x4D61C0`, per-frame walk `0x427BB7`, crash `0x44DE14` reached from the level pop `0x425EAC`; Windows Application log 23 Sep 2026 16:35, fault offset `0x4DE14` | Fixed the same evening: the destroy count becomes 0 (`ba 01 00 00 00` -> `ba 00 00 00 00` at `0x405008`), `ozi` edit 8 - in the 640x480 form only since 24 Sep 2026, when the HD sizes got the box back (create and destroy both run there); `DC16_DISPLAY_AND_RESOLUTION.md` §10.36 |
| F64 | **The Jupiter mod 0.3 (MaraProject, 2008) is a unit / balance mod over the 106-type Classic table, not a mission pack, and fits the Council Wars build as a fifth prefix mode - once the prefix slots are moved.** Its installer is a Council Wars-in-root install plus 112-type `gamestat.txt`, 72-row `weapstat.txt`, 86-row `depend.txt`, `unitid.txt`, `slist.dat`, a HUD with the new build buttons and one extra sound; its own menu has no campaign button. Two constraints of the exe decide the shape: (1) the four mode slots are 8 bytes with the next string right behind them, so an 8-letter prefix (`jupiter/`) needs new slots - the two prefix reads are single `mov esi,imm32` instructions and are re-pointed at the dead 24-byte `intrface/credits.txt` string (two 12-byte slots); (2) the start-up sound table holds exactly 200 entries whose WAVs are all opened at start-up under the Council Wars prefix, so a mod-only sound cannot exist (the mod's own entry 200 overflowed the array). Not a server matter. | `0x4063F9` / `0x452AC8` prefix reads, `0x48246C` credits string (pointer scan of all HIGHLOW entries), `0x4309C8` table loader (clear loop `0..0xC7`, entries `0x4DFF30` x 116), `0x430DD3` load-all pass, `0x431390` list loader (`i < NUM_SOUNDS` at `0x431430`) from `0x41EB40`; the mod's files diffed against the game folder | Fix `ozi` v4 (`tools/patch_ozi_menu.py`): buttons 8/9 in the 45 bytes of the removed credits create (live code - the menu init falls through them, so the block opens with a jump over itself; the first build without it crashed at menu init, fault offset `0x4E80`), `stub_jup_set` at `0x47F3B0`, filter `cmp edx,9`; overlay `jupiter/` from `tools/build_jupiter_overlay.py`; nine-row menu anchored at `H-72`; confirmed in game 23 Sep 2026 (menu, race screen, mission 1, load screen) - and **dropped by the maintainer on 24 Sep 2026** once it was clear that the "missions" were the Dark Colony ones: everything reverted, findings kept in `DC16_DISPLAY_AND_RESOLUTION.md` §10.37 |
| F65 | **Dark Colony Ultimate (patched Council Wars) and Dark Colony simulate identically only since the fixes of 25 Sep 2026.** (1) Council Wars' start-up animation list loads `troo.fin` / `grrr.fin` before the Classic list, which give the Security Trooper, the Gray and the eight commanders (types 0, 8, 69-76) a `DEPLOY` animation; Classic falls back to STAND. State 0xD waits for it before the commander rally links units: Gray commanders 28 ticks (14 frames) in Council Wars against 2 in Classic, Human commanders 2 against 2. (2) MULTI PLAYER WAR entered the network screen in whatever menu mode was last, so after OZI MISSIONS a network game loaded `ozi_ns/gamestat` (126 types, other weapon/blast/bullet tables). Harmless: the 12 extra Council Wars rows of `exp/gamestat/gamestat.txt` (the type count is read only by a sound assert and by save/load), sounds and sound tables (no `rand()` consumer outside the simulation, the AI and the triggers). | engine replays of the 19 recordings in `logs/replays` with Classic vs Council Wars sprite data: all 6 with a Gray commander rally diverge 3-10 ticks after the first rally, the other 13 and all 19 with the fixed data stay identical; `patch_ozi_menu.py` item 8, `build_ozi_overlay.py` `NOT_IN_OZI_ANIM`; `DC16_DISPLAY_AND_RESOLUTION.md` §10.39 | Patched Ultimate: `exp/animozi.dat` without the two FINs, MULTI PLAYER WAR through `tramp_dc_net` (Dark Colony mode = the root's Classic tables, the same as `data/classic/*.json`). The untouched `ENGEXP16.EXE` keeps difference (1): a room with it and a Gray commander rally desynchronises (with `SYNC_CHECK=send` the server's checksum aborts it) |
| F66 | **"Connection Lost" on the client = the client was alive and the server closed the socket.** The battle loop `0x40115C` runs the client step `0x40AAFC` on every UI pass; the step's first action is the receive loop `0x41E648` (`0x40ABD7`), and a negative return sets `gs+0x471C8 = 1` and shows `intrface/loste` text 2 via `0x410400(net, 2, 8)`. Nothing else shows that box. It is therefore the signature of an eviction of a live client (`no echo for frame N` after `ECHO_TIMEOUT_MS` = 5 s, `lagging`, `idle`), whereas a crashed client shows Windows Error Reporting (socket kept open) and a sync assert shows `error.log` + a message box. The relay cannot tell from its own log whether the 5 s of missing echoes were a network interruption or a client whose loop stopped and resumed; since 25 Sep 2026 the `client left` line carries `sinceLastMessageMs`, `clientTime`, `serverUntil`, `pendingEchoes`, `latencyMs` so the next case says at least whether anything at all arrived. | `0x40115C`–`0x401221`, `0x40AAFC`–`0x40ABF7`, `0x41E648`; Fly log 25 Sep 2026 14:22:59 UTC | `Room.evict` (`src/room.js`) logs the battle numbers; `ECHO_TIMEOUT_MS` unchanged (maintainer decision pending, see §16 of 25 Sep) |
| F67 | **The main menu's opening plate wave is the `banim` widget, and its pair order is the order of the wave.** `create_banim` `0x427854` stores n plate ids and m button ids (`widget+0x28`); the update `0x4279EC` (a blocking loop run from `0x427AE4` for every type-`0x0C` widget when the screen opens) starts plate k+1 when plate k's one-shot has finished and reveals button k when plate k settles, so the first listed plate must carry `anim_oneoff` (that is what the flag does). The Ultimate menu generators had kept the stock pair order while moving the buttons into the seven-row block, so the wave hopped about the block (maintainer report 25 Sep 2026 "initial animation of buttons are in wrong places"); now the order follows the layout, column by column, top to bottom, and `anim_oneoff` sits on the first plate of that order. Confirmed in game (20 Hz capture, plates settle 60 ms apart in the intended order). | `0x427854`, `0x4279EC`, `0x427AE4`, keyword table `0x489588` (`{name, creator}` pairs) | Data only: `build_ozi_overlay.menu_order`, patcher `Edit-OziMenu`; `DC16_DISPLAY_AND_RESOLUTION.md` §10.40 |
| F68 | **The battlefield options handler reaches its tail for every event kind, and its button ids are plain data.** interface.c's options handler `0x432CE4` (CW) reads the event with `0x42417C(ip, &id)`, runs its `cmp [ebp-8], id` chain only for kind 1 (a press) and jumps to the tail `test bl,bl / call close` for everything else (`bl` is set by press events alone, so that tail is inert for the release). Code hooked into that tail therefore sees the release too - the first build of the MUSIC row stepped twice per click - and must be reached from the press path only (the `jne` at `0x432D02` now targets the epilogue). New buttons are ordinary `pushb` ids in the dialog script (71 / 72), value texts an `in_text` (73) set with `0x423ED4(ip, id, str)`, which asserts on a missing widget (`objects[id].type != 4`), so a guard on the type byte (`ip+0x88+0x34*id+1`) keeps a stock script safe; `textmsg(ip, n)` `0x422718` returns "" for a missing number. | `0x432CE4`, `0x432D02`, `0x432F20`, `0x432C28` (refresh), `0x423ED4`, `0x422718` | Ultimate `music` fix v2 (`patch_music.py` + `music_asm.py`): source DC / CW / ALL, defaults from the `ozi` stubs; `DC16_DISPLAY_AND_RESOLUTION.md` §10.41 |
| F69 | **A missing start-up file is an assert box, not the FILE NOT FOUND box, and `error num` is Watcom `errno` (1 = `ENOENT`).** The generic open helper (CW `0x4061BC`) takes the assert path (`fprintf` "FILE Error opening file %s with error num %d" + `assert(0)` safefunc.c 290) whenever the display object `0x488E20` is still NULL; the sound table `sound\sound2.dat` (`0x4309C8`, display slot `+0xFC`) is loaded inside the display constructor, after `exp/animozi.dat` and its ~180 banks, before `set_display 0x405E00` stores the object. `sound\sound2.dat` is the only path string in either exe with a backslash separator. Reproduced on Windows 25 Sep 2026 by renaming both `sound2.dat` copies away (`DC16_DISPLAY_AND_RESOLUTION.md` §10.42); since the same day fix `longpath` writes that separator as `/` (§10.29). | `dcexp16.asm` 25 Sep 2026 |
| F70 | **The DARK COLONY mode of Dark Colony Ultimate plays Classic's own balance tables.** The six tables (gamestat, weapstat, boomstat, mbullet, unitid, depend) are loaded per battle and per save load by the game-state initialiser `0x41BB50` → `load_tables 0x43C4AC` (its only caller) through the prefix helper `0x4063E4` (`<prefix>` + name, then the bare root name); the `dc/` overlay has no `gamestat/`, so every table is root `GAMESTAT/` (106 types) = the files `Dark Colony.exe` reads. The COUNCIL WARS mode gives the same numbers for the shared 106 types (`exp/gamestat/gamestat.txt` = root + 12 appended rows, no other expansion table exists; `exp/gamestat/gxmestat.txt` is dead), only `ozi_ns/gamestat/` is re-balanced and only under `ozi_ns/`. A save is refused on a type-count mismatch (`0x43C4E4` writes count + 16 bytes per type, `0x43C558` returns -1), so DC saves load only in DC mode. Start-up loads under `exp/` are shared by every mode: `exp/sound/sound2.dat` (12 ambience entries differ from root, audio only), `exp/animozi.dat` (Classic-identical since F65), the banks. | `dcexp16.asm` 25 Sep 2026: loaders `0x43BBE0`/`0x43B74C`/`0x43B484`/`0x43B1B0`/`0x438778`/`0x437A20`, `0x43C4AC`, `0x41BBFE`, `0x40DD0E`, `0x406474`; file diffs in the game folder | Investigation only, nothing changed; `DC16_DISPLAY_AND_RESOLUTION.md` §10.36, `DC16_SINGLE_EXE_MERGE.md` §5.1. For the relay: an Ultimate client in DARK COLONY or MULTI PLAYER WAR mode simulates on the same tables as `data/classic/*.json` |
| F71 | **The game's CD report is sent by the connect code, before the lobby screen exists; keep-alives start with the lobby loop, which at 1920x1200 comes 3-4.5 s after the connect.** Observed 27/28 Sep 2026 on a local relay with `LOG_LEVEL=debug`: `hall joined` at the `connect`, the `VAR (8+slot, cd)` 0.08 s later, then silence until the `intrf_hd/multie` screen had loaded (per-second screen captures: black for 3.0-4.5 s at 1920x1200, 1.5-2.8 s at 1280x800), then `NAME`/keep-alives. A relay that starts its keep-alive clock at that `VAR` evicts every 1920-wide client before its hall appears (black screen, then back to the main menu). | relay logs + captures, 27/28 Sep 2026 | `Hall.onData` / `Room.onData` ignore `VAR`-only batches for `firstMessageAt`; `JOIN_TIMEOUT_MS` 15 s; §16 entry of 28 Sep 2026 |
| F72 | **Every screen load converts its palette through 512 full-surface transfers, so the load time is proportional to the pixel count.** `set_palette` (ddex4.c, CW `0x42F380`) converts each of the 256 palette entries with `GetDC(back buffer)` → `SetPixel` → `ReleaseDC` → `Lock(whole surface)` → read → `Unlock`; on Windows 11's D3D9-emulated DirectDraw `ReleaseDC` and `Unlock` each write the whole surface back (2.8 ms each at 1024×768, 6.2 ms at 1920×1200 per entry), i.e. 1.4-1.5 s resp. 3.2-3.5 s per screen change and twice that at start-up; the value read back is plain truncation (`(r>>3)<<11 | (g>>2)<<5 | b>>3`, 256/256 verified in the running game). This is the black screen of F71 and the slow menus at 1920-wide sizes. | main-thread stack sampler + `ReadProcessMemory` of the LUT, 28 Sep 2026; `DC16_DISPLAY_AND_RESOLUTION.md` §10.46 | Fixed the same day: fix `palette` (`tools/patch_palette.py`, both exes, patcher step after `ddraw`): the loop body computes the value in place, the Unlock is skipped; screen change 0.5 s instead of 5 s at 1920×1200, confirmed in game; §10.46 end |
| F73 | **The main menu's opening is staged by code, not data: `bintro` runs the blocking button wave first, then the logo, then the title at logo frame 10, and the credits TTY from the loop's first pass; the animation modes are 0 loop / 1 one-shot / 2 stopped = finished.** Reordering it needs the init rewritten; the shipped scripts' `anim_oneoff` first plate has to be parked (mode 2) and restarted before the wave or `run_banims` never advances. | `dc16.asm` 28 Sep 2026: `0x00404E80..0x00405060`, `0x0042626C`, `0x00426294`, `0x0042670C`; captures of both 1920x1200 builds | fix `menuorder` (`tools/patch_menu_order.py`), `DC16_DISPLAY_AND_RESOLUTION.md` §10.47; relay unaffected (menu only) |
| F75 | **The battlefield's look is three data files, and the stock data already holds a console-style button bank nobody uses.** `INTRFACE.GIF` (frame), `MAINBUT.SPR` (133 cells) and `POPP.SPR` (14 dialog plates) are the only metal-styled art; `INTRFACE/BUTTON.SPR` (114 cells, referenced by no script or exe string) is the same button set in the menus' style, index for index. A `pictures intrf_hd/<bank>` script line is resolved like `background`, so the patched exes take new banks from `INTRF_HD/` without a code change; the terrain palettes differ from `PALETTE.GIF` by at most 3 of 255 per index, and the cyan ramp 128..143 is remapped to the player's team colour in widget-drawn cells. | scripts + exe strings + palettes measured 28 Sep 2026; game test at 1920×1200; `DC16_DISPLAY_AND_RESOLUTION.md` §10.49 | `tools/hud_console.py` (frame / bank / popp / apply), patcher rules `$TAB_STRIP`, `$PIC_RETARGET`, banks as `hd_data`; relay unaffected |
| F74 | **The in-game chat has no sound and shows two lines; the single-player mission message is a different module with a sound.** Chat `0x0E` → handler `0x41DA2C` → six-entry ring in the net object (`+0x310` count, `+0x314` index, `+0x318` 88-byte entries) → client display `0x40B10D` shows entries `index`, `index-1` in `MAINE` `in_text 203/204` (ids `0xCB+i`), drops the oldest after 7.5 s (2.5 s while more than two queued), plays nothing. Mission messages (trigger `msg`, action 11 of `0x43D904` → `0x44D88C`, object `gs+0x46FBC`) are drawn by `0x433C44` into bar widget 148 only in campaign modes (`gs+0x14F0` 0/3) and play sound table entry 187 = `SOUND\MSG.WAV` through `[display+0x7C](187, 1)`. Every `0x0E` whose mask includes the local player counts as "sent comment", the relay's bot lines included. | `dc16.asm` 28 Sep 2026: `0x0040B10D..0x0040B221`, `0x0041DA2C`, `0x0043D967`, `0x0044D88C`, `0x00433C44`, `0x00433D4E`; `DC16_DISPLAY_AND_RESOLUTION.md` §10.48 | fix `chat` (`tools/patch_chat.py`, both games, `Requires palette`): six lines (widgets 203, 204, 207..210, drawn only if the widget exists), sound 187 on every arriving line via a marker in `+0x314`; `MAINE` gets `in_text 207..210`; relay unchanged - its bot chat lines now beep on the client like the players' |
| F76 | **A mode switch changes the monitor's DPI, so a SYSTEM-DPI-aware full-screen game is bitmap-scaled by the desktop window manager in every mode whose scale step differs from the desktop's.** Windows allows only certain scale steps per resolution: on a 1920×1200 panel at 150 % the game window's `GetDpiForWindow` reads 96 at 1024×768, 1280×720 and 1280×800 (100 %), 120 at 1280×1024, 144 at 1920×1080 and 1920×1200. The 27 Sep `<dpiAware>true</dpiAware>` manifest (system-aware, laid out for 144) therefore showed those modes as a two-thirds picture in the top-left corner (683×512 at 1024×768); an unaware process is scaled by `monitor/96` instead, i.e. 1.5× exactly where the DPI stays 144 (§10.44's 1920-wide symptom). Only a per-monitor-aware process is never scaled. | measured 28 Sep 2026 with a per-monitor-aware probe (`EnumDisplaySettings`, window rect, `GetDpiForWindow`, screenshots) on nine builds; `DC16_DISPLAY_AND_RESOLUTION.md` §10.50 | `patch_icon.MANIFEST` = `dpiAwareness PerMonitorV2, PerMonitor` + `dpiAware true/pm`; every game build's hash changed; relay unaffected |
| F77 | **The lobby client's slot comes from the `'d'` handshake and nothing else, so a client that has not yet shown a lobby screen can be seated anywhere.** The hall pre-assigns a slot at connect (F30) only because the hall IS a lobby screen; a room browser that runs before the lobby code (the ONLINE WAR screen, §20) lets the relay pick the slot at join time. | `0x4108DB` (join wait reads `'d'`), §6.8 of the protocol doc | `0x52 ENTER` seats the client in a random seatable slot of the chosen room and then sends `'d'` with that slot (§20.4) |
| F78 | **The game opens its own socket inside the network entry `0x40122C`** (`connect` through the net object's vtable slot `+0x64`, the 16 KiB send buffer allocated there), so a connection made by any other code cannot be handed to the lobby client. The address argument is `{u16 port; u32 pad; char *host}` (CONNECT handler `0x405C0A`: port 8888, `[ebp-0x18]`). | `0x40139D`, `0x405BEB..0x405C16` | The ONLINE WAR module keeps its relay connection and lets the game connect to a loopback proxy thread inside the process that pipes the bytes (and does the TLS) - §20.5 |
| F79 | **The relay has no way to tell an ONLINE WAR connection from a stock one until the client speaks**: the stock client sends nothing before `'d'` (the CD report `'o'` follows the `'d'`), and the hall's dump goes out at accept. | `hall.accept`, F23 | The module ignores every frame until `ROOMS`/`ENTERING`; the relay marks the connection at `0x50 LIST` and stops sending it hall rows |
| F80 | **The game's own frame reader is strict about the sequence nibble and the game's stream through the proxy starts at 0** (F1); the frames the module exchanged before the hand-over count on the relay's counters. | `0x43AE8C` strict=1 on the client | The relay resets `seqOut`/`seqIn` to 0 at `ENTERING`, so `'d'` goes out as sequence 0 and the game's first frame is expected as 0 |
| F81 | **The ONLINE WAR module's keep-alives outlive its `ENTER`: until it has READ `ENTERING` (one relay round trip later) it keeps sending `'q'` every 700 ms, and the relay resets `seqIn` at `ENTER`** - so a keep-alive in flight arrived as the "game's first frame" with the module's number and the strict check evicted the player (`sequence 12, expected 0` 60 ms after `online -> room`; two of four joins in the Fly log of 29 Sep 2026, the maintainer's "often connection lost"). A fake relay holding `ENTERING` for 3 s received 4 keep-alives from the old exe after `ENTER`, none from the fixed one. | Fly log 29 Sep 2026 13:02 / 13:05 UTC; `online.c` `room_screen` loop; `fakerelay.py` control run | Module: silent from `ENTER` to the answer (`entering` flag, second ENTER ignored, `REFUSED` clears it). Relay: `client.handover` set at `enter()`; `readCommands` drops module-only frames (keep-alive, LIST, ENTER) without a sequence check until the game's first frame, `hall.onData` drops same-chunk stragglers instead of forwarding them and ending the join grace. Two tests (261). |
| F82 | **A `checkb` widget (the lobby's READY boxes) toggles itself on a mouse press and reports the new state.** Its creator is `0x4271AC` (Ultimate; keyword table at `0x4895B4`, `{name, creator}` pairs: pushb `0x426F48`, checkb `0x4271AC`, picture `0x4227D4`, in_text `0x42283C`, count, scount, group, list `0x42A3EC`, scroll `0x42ABBC`, colour, font, animation, textmsg, gadget `0x424AA0`, label `0x426C34`, banim `0x4278B4`); its state struct sits at `objects[id]+0x28` (+0 state byte, +4/+8 the two picture cells); its vtable `0x4896E4` = {redraw `0x4270D0`, mouse handler `0x42711C`}: on event 4 (press) the handler stores the id at `ip+0x431C`, **inverts the state byte**, redraws, plays sound `0x61` and returns **2 = now ticked / 3 = now cleared**; event `0x104` (release) clears `ip+0x431C`. The hit dispatcher `0x423B30` stores the widget index in the pump's out-parameter and returns the handler's value, so `pump(ip, &arg)` yields kind 2 / 3 with `arg` = the box's id. (`0x4273B0`, first taken for the handler, is the keyboard entry at vtable +0xC and returns 4 / 5.) The box is set from code by `0x427308(ip, id; bl = state)` (40 callers in the lobby code), which asserts the widget's type. | `dcexp16.asm` `0x42711C..0x4271AA`, `0x423B30`, `0x4896E4`; game test 2 Oct 2026 (the first build listened for 4 / 5 and both boxes stayed ticked) | The REPLAY ONLINE GAME module makes eight checkb widgets behave as radio buttons: on kind 2 / 3 it takes `arg`, remembers the choice and sets all eight boxes with `0x427308` - exactly one ticked |
| F83 | **A purchase is deducted from `MONEY` (`gs+0xBAC+p*0xE34`) only on the issuing machine; every machine books the price into `SPENT` (`gs+0xBB0+p*0xE34`, handlers 0x09 / 0x0A / 0x0C); money is not in the checksum.** A viewer in a recorded seat never issues anything, so its money only grew (maintainer, 2 Oct 2026: "fix money count increase problem in replay mode, when money are not shrinking on purchase"). The game's own `DISCONNECT` handler normalises a base with `MONEY -= SPENT`. | `DC16_BATTLE_ENGINE.md` §7 / §18, `commands.js` port | The exe module's proxy thread, every 100 ms while a replay runs, takes every increase of the local seat's `SPENT` off its `MONEY` (`viewer_money_fix`, `online.c`); a drop of `SPENT` (a new battle) resets the bookkeeping. **The simulation state is NOT the main menu's `gs`**: the menu passes proto.c's campaign object (`+0x14F0`/`+0x14F4` flags, `+0x1984` settings), the battle's state is another allocation whose pointer the game start `0x41EAA0` stores at **`.bss 0x4AA9DC`** (the first build read local player 0 / money 0 from the campaign object and did nothing) |
| F84 | **A network battle could be saved, and the file resumed as a solo host game, never as a rejoin.** The battlefield save dialog (`0x432708`, CW `0x432768`; F11 via the client's event table `0x40A63B`, the Game Option cell via the dialog handler's `cmp edx,3Fh` - the `pushb` id is the key code) has no game-type check and sends nothing; the file lands in `save\<name>.dcg` with game type 2 at offset 14. The main menu's load routine `0x403AA4` takes the type-2 branch at `0x403B30` = `0x40122C(ui, 0, 0, gs; loaded)`: a null net builds the in-process mailbox network (`0x40BFB0`), the flag makes `0x40B4B0` create the host's server object on it, the saved path in `gs+0x1581` skips the lobby and the shuffle, the battle is re-read (`0x4017C5`) with every seat as saved - the other humans unconnected and idle, the relay's bots absent; no socket, no CONNECT screen; the protocol has no save or resume message. | `dcexp16.asm` 3 Oct 2026, `DC16_DISPLAY_AND_RESOLUTION.md` §10.68 | Fix `netsave` (`tools/patch_netsave.py`, both games, patcher 1.4): the Save cell disabled through the widget's `+3` flag at battle start and the dialog's entry guarded while the game type is 2. Relay unchanged; a true resume would need a save upload and a rebuilt lobby (not planned). |
| F85 | **The game has no frame limiter; the DirectDraw `Flip` is its only pacing.** `present` `0x42E0FC` (Ultimate +0x60) ends in `Flip(NULL, 0)` retried on every error but SURFACELOST; Windows completes it at the vertical blank (60 frames per second), Wine returns at once (350-380 frames per second measured in the Xvfb rig). The battlefield scroll (one tile per frame after a 100 ms dwell, `0x40AE6A`) and the cursor animation (`0x40B309` -> `0x422C7C`) advance per frame with no time gate; the menus' widget pump (`0x424294`, 16 ms) and interface loop (`0x42412A`, 33 ms) are clock-gated; ticks are clock-driven, so the simulation and the relay echo were never affected. | `dc16.asm`, Wine rig 3 Oct 2026, `DC16_DISPLAY_AND_RESOLUTION.md` §10.69 | Fix `fps` (`tools/patch_fps.py`, both games, patcher 1.5): present's epilogue waits until 16 ms have passed since the previous frame (`timeGetTime` + `Sleep(1)` under `timeBeginPeriod(1)`), stub in the dead `remap` assert bodies. Relay unchanged. |
| F86 | **The battlefield pointer animation is advanced once per frame, the menus' once per 33 ms.** Client display `0x40B309` calls the cursor advance `0x422C7C` every frame (step routine `0x426428`: per-frame hold count from the FIN, no clock) = 30 cursor changes per second at 60 Hz; the interface loop `0x42412A` gates the same call at 33 ms (`last = now`, the elapsed/33 loop after it only redraws widgets) = 11.5 per second with the menu arrow. | `dc16.asm`, `smoke_rig/cursor_rate.py` 3 Oct 2026, `DC16_DISPLAY_AND_RESOLUTION.md` §10.70 | Fix `pointer` (`tools/patch_pointer.py`, both games, patcher 1.6): the battlefield call goes through the menus' 33 ms gate (15 changes per second). Relay unchanged. |

| F46 | **Krusty's inputs** are all in the engine's state: objects (position, type, team, life, weapon/defence class), the player's own vision bits of the ground layer (`0x40000000 >> p`), `GS.ALLIANCE`, the path families and the routing matrix, the production queues, `dep_check_building/troop`, the unit cap. It uses its own `rand()` draws from the shared game RNG (defend re-route, bomber targets), everything else is deterministic. | `DC16_AI.md` §5–§15 | The bot reads `room.sync.engine` through the engine's accessors and uses a private RNG (§19.3) |

---

## 3. Architecture

```
Dark-Colony-Server/     (repository root)
  package.json          "type": "module", "engines": { "node": ">=20" }, scripts: start, test, fakeclient, smoketest, lint
  src/
    index.js            entry: config → RoomPool + Hall → net.createServer, step/watchdog timers, optional health listener
    config.js           env-var parsing with defaults (§11), ROOMS → ROOM_LIST
    maps.js             the 56 multiplayer maps of Classic (file → name, terrain, players), ROOMS entry resolver (F31)
    constants.js        STATE, SLOT_TYPE, VAR defaults, cheat texts
    frame.js            encode/decode frames, sequence check (§4)
    commands.js         command size table, parsers and builders for every message (§10)
    client.js           one TCP connection: socket wired to its current owner (Hall or Room), sequence counters, watchdog bookkeeping, send()
    hall.js             the room-selection lobby: private per-client view, room rows with marquee, chat commands, READY = join (§17)
    chat.js             the server-painted 10-line chat window: static header, recent lines, wrap at 40 columns (§17.8, F35)
    rooms.js            RoomPool: one Room per ROOMS entry, shared timers
    room.js             one game room with its own map: slots, state machine LOBBY→STARTING→RUNNING→reset, eviction, adopt()
    lobby.js            fake-host behaviour: join dump, message policy, READY handling, MREADY wait (§6)
    game.js             lockstep: command queue, step loop, sync frame builder, filters (§8)
    watchdog.js         per-client deadlines and lag eviction (§9)
    log.js              one-line JSON logs to stdout (fly logs); childLogger stamps the room number
  test/
    helpers.js          Room / RoomPool + Hall with a fake clock, fake sockets, scripted peers
    frame.test.js, commands.test.js, lobby.test.js, game.test.js, eviction.test.js, fakes.test.js, hall.test.js, chat.test.js, integration.test.js
  tools/
    fakeclient.js       scripted dc16 client: library for the integration test and a CLI (§13); READY policies auto/hold/follow
    smoketest.js        seats scripted clients in rooms of a running server (default: 7 in room 1, 3 in room 2) for tests with the real game (§13.7)
  docs/
    RELAY_SERVER_PLAN.md, DC16_NETWORK_PROTOCOL.md   (this plan and the protocol reference)
  logs/                 live-test logs, kept out of git
  Dockerfile, fly.toml, .dockerignore, eslint.config.js, README.md, CHANGELOG.md, LICENSE
```

Node 20 is enough to run it; the Docker image uses Node 22. (The code was developed as `dc-relay/`
inside the Dark-Colony repository and moved here as version 2.0 on 7 Sep 2026, §16.)

Design rules:

- One `Room` = one map = one game at a time; up to eight rooms run side by side (`RoomPool`, §17.6),
  each with the state machine below. A connection reaches a room through the room-selection lobby
  (`Hall`, §17), or directly into room 1 with `HALL=false` (the 2.0 behaviour). Version 2.0 had a
  single room; its design rule said multi-room would be added by instantiating `Room` per group,
  which is what 2.1 does.
- Everything is single-threaded and event-driven; the only timer that matters is the game step.
- The relay never depends on a simulation: it only relays, orders and paces. Since 11 Sep 2026 a
  **battle engine** (`src/engine/`, a port of the game's simulation core) can run *beside* it to
  produce the lockstep checksum (§18); it is off by default and can never stall or alter the relay.
- Every byte sent to a client goes through `Client.send(payloadBuffer)`, which stamps that client's
  sequence nibble. A broadcast is the same payload sent through each client's `send`.

State machine of `Room`:

```
LOBBY ──(all ready + min players, countdown done; send 'h' 2,0)──► STARTING
STARTING ──(MREADY from every connected client, or MREADY timeout)──► RUNNING
RUNNING ──(last real client gone)──► reset() ──► LOBBY (fresh random slot assignment)
STARTING ──(all clients gone)──► reset()
```

---

## 4. Wire codec (`frame.js`)

```js
export const MAX_FRAME = 1024;

export function encodeFrame(payload, seq) {        // payload = commands, no terminator
  const len = payload.length + 3;                  // header(2) + payload + 0x00
  if (len > MAX_FRAME) throw new RangeError(`frame ${len} > ${MAX_FRAME}`);
  const f = Buffer.allocUnsafe(len);
  f[0] = len & 0xff;
  f[1] = ((len >> 8) & 0x0f) | ((seq & 0x0f) << 4);
  payload.copy(f, 2);
  f[len - 1] = 0;
  return f;
}

// Decoder: feed(chunk) appends to a per-connection accumulator and yields payloads.
//   while (acc.length >= 2) {
//     const len = acc[0] | ((acc[1] & 0x0f) << 8), seq = acc[1] >> 4;
//     if (len < 3 || len > MAX_FRAME) -> protocol error, drop connection
//     if (acc.length < len) break;                       // wait for more bytes
//     if (acc[len - 1] !== 0) -> protocol error
//     yield { seq, payload: acc.subarray(2, len - 1) }; acc = acc.subarray(len);
//   }
```

Sequence handling (mirrors `0x43AE8C`):

- Outgoing: `client.seqOut` starts at 0, `send()` stamps it and increments mod 16.
- Incoming: `client.seqIn` = next expected. `seq == (seqIn − 1) & 15` → duplicate, skip frame.
  Otherwise accept; if `seq != seqIn` log a warning and resynchronise (the original server uses
  `strict = 0`). `seqIn = (seq + 1) & 15`.

Primitive helpers: `u8`, `i16LE`, `i32LE`, `cstr` (NUL-terminated, no length prefix, max 16 for
names, max 1000 otherwise).

Payload splitting: a payload is parsed command-by-command with the size table of §10. An unknown type
or a truncated command means the **whole frame** from that client is dropped and logged (the game
would reject it too, F7).

---

## 5. Connection lifecycle (`client.js`, `hall.js`, `room.js`)

With `HALL=true` (default since 2.1) a new connection is first handled by the room-selection lobby
(§17): it gets its `'d'`, its slot number and the private room view there, and enters a `Room` only
when it presses READY on a joinable room (`Room.adopt`, §17.5). The socket events are wired once
(`Client.wire`) and dispatched to whatever object currently owns the connection. The steps below
describe the room side; with `HALL=false` they apply to the connection directly.

1. `accept` → `socket.setNoDelay(true)`, `socket.setKeepAlive(true, 15000)`.
2. If `room.state !== LOBBY` or no free slot: send `'d'(15, 7)`, one chat line
   `'e' "Mercenary: game in progress, try again later"`, then `socket.end()` after 1 s. (The client
   shows the lobby for a moment and then "SERVER LOST". Closing without `'d'` would make it wait 25 s
   for "no init msg".) Through the hall this cannot happen: the hall only hands over clients the room
   can seat, and everybody else waits in the hall.
3. Otherwise pick a **random free slot in 1..7** (`crypto.randomInt`), create the player record
   `{ slot, name: "Player"+slot, race 0, colour slot, team slot, type 2, status 1, ready false,
   lastSeen, seqIn 0, seqOut 0 }`, send the join dump (§6.1) and one private greeting line to the
   newcomer (nobody else is greeted or notified, the slot fills in by itself). A client coming from
   the hall keeps the slot it already has (F30) and gets the dump without the `'d'`. Its colour is
   the slot number unless an occupied slot already shows that colour, then the first unused one (F4).
4. Data → decoder → frames → commands → policy (§6 in LOBBY/STARTING, §8 in RUNNING).
5. Liveness and correctness: every phase has answers the client must produce (`'q'` every 700 ms in
   the lobby, `MREADY` while starting, an echo for every `UNTIL` in battle) and deadlines for them; a
   client that misses one, or violates the protocol, is evicted and announced to everybody else. The
   rules are in §9. `IDLE_TIMEOUT_MS` (10 s without any byte) is the phase-independent fallback.
6. Close/error/timeout → free the slot. In the lobby, if the player was ready, broadcast `'h'(0, slot)`
   first so every client releases its colour lock (F20), then `DISCONNECT(slot)` as a standalone frame.
   While STARTING or RUNNING **no `DISCONNECT` is sent** (R14, since 27 Sep 2026; until then it was
   queued into the next sync frame, F19, and handed the base to the game's AI on every client): the base
   becomes a server bot (§19.9) or stands idle. Exclude the slot from `min(clientTime)`. If no real
   client remains → `room.reset()`.

---

## 6. Lobby: the server as fake host (`lobby.js`)

### 6.1 Join dump

One command per frame (the original host sends them one by one; the lobby parser was not verified
for multi-command frames). Order as the original host (§6.1 of the protocol doc):

| Step | To | Message |
|---|---|---|
| 1 | newcomer only | `'d' 15, p` |
| 2 | everyone | `'i' "D8PLAY01.SCN", <wire title>` where the wire title is the game's own format (F15): `formatScenarioTitle("Armageddon", 8, "desert")` |
| 3 | everyone | for every other slot q: `'l' colour_q, q` |
| 4 | everyone | for every other slot q: `'g' q,name_q` · `'f' race_q,q` · `'j' type_q,q` · `'n' team_q,q` · `'h' status_q,q` |
| 5 | everyone | for p: `'g' p,name_p` · `'f' 0,p` · `'j' 2,p` · `'l' colour_p,p` · `'n' team_p,p` · `'h' 1,p` |
| 6 | everyone | `'o' v,value` for v = 0..15 with `[0,0,1,0,4,4,0,0, 1,1,1,1,1,1,1,1]` (VAR 8+p = "has CD" = 1 for all) |
| 7 | newcomer only | the chat window (§17.8): ten `'e'` lines, the room header at the top (one line: `Room <n>: <map>, <terrain>, <k> players.`), blanks below; no name in front of relay lines |

Slot 0 is constant: name `Mercenary`, type 2 (human), race `MERCENARY_RACE` (default 0), colour 0,
team 0, status 1. With `FAKE_PLAYERS > 1` further fake humans (names from `FAKE_NAMES`, same race,
colour = team = slot, status 1, no client) occupy random slots chosen at every reset; they look and
behave exactly like Mercenary. Empty slots are type 3, status 0, colour = slot, team = slot (or AI
type 0/1 when `FILL_EMPTY_WITH_AI` is on, §7).

Existing clients receive the dump again on every join; that is what the original host does and it is
harmless (the messages are idempotent).

### 6.2 Message policy in LOBBY (sender = slot `s`)

| Message | Policy |
|---|---|
| `'g'` name | only if `player == s`; truncate to 16 chars; store; relay to all |
| `'f'` race | only own slot, only while status ≠ 2; store; relay |
| `'k'` colour cycle | only own slot, only while status ≠ 2; apply the same rule as the client (`(colour+delta) mod 8`, skipping colours locked by ready slots); relay |
| `'m'` team cycle | only own slot, only while status ≠ 2; `(team+delta) mod 8`; relay |
| `'h'` status | the READY button; only own slot, values 1/2; mirror the client rule (2 is refused if the colour is locked by another ready slot); relay; when the player becomes ready post `"<name> is ready (k/n)"` and evaluate the start condition (§6.3). The button works for every joiner, the eighth slot included (verified live, 7 Sep 2026) |
| `'e'` chat | relay the line (sanitised); chat carries no commands |
| `'q'` keep-alive | consume (refreshes liveness) |
| `'y'` INIT_ME | reply with the dump minus `'d'` (meta-lobby join path; cheap to support) |
| `'v'` MREADY | ignored in LOBBY, handled in STARTING (§6.4) |
| `'o'` VAR | the client reports its own CD state as `'o'(8+s, 0/1)` at join and whenever it changes. Never relay it; answer with a broadcast `'o'(8+s, 1)` so every lobby shows a CD icon for every player (F5). Other VAR indices from clients are dropped |
| `'j'` type, `'l'`/`'n'` absolute, `'i'` scenario, `'p'` NUKE | **drop** — host-owned settings; the server is the host |
| any numeric type (`0x01`, `0x02`, `0x04`, `0x10`, `0x11`…) | drop in lobby |
| unknown | drop frame, log |

The server keeps its own copy of the 8 slot records so that the dump for the next joiner is correct
and so that "everyone ready" can be evaluated.

### 6.3 READY and game start

- Each real player presses READY; the client sends `'h'(2, s)` itself (after its own colour check,
  F4), the server relays it, every client applies the same colour rule, and the server posts
  `'e' "<name> is ready (k/n)"` (no name in front of relay lines since 7 Sep 2026, §17.8). A player whose colour is locked by a ready player must
  pick another colour first; the client refuses to send otherwise.
- Start condition: every occupied slot 1..7 has status 2, count ≥ `MIN_PLAYERS` (default 1 since 7 Sep 2026, 2 before).
- Countdown `START_COUNTDOWN_S` (default 3) with chat lines; any join, leave or `'h' 1` cancels it.
- Then: `state = STARTING`, broadcast `'h'(0, q)` for every fake slot `q` (slot 0 and the other
  fakes). Because those were the only status-1 slots left, every client exits the lobby at once (F3)
  and starts loading. Status 0 rather than 2 because a
  status-2 message is subject to the client's colour-lock check and was refused by one client in
  the live test; status 0 is applied unconditionally and the game start ignores statuses.
- From this moment the server drops all lobby messages except `'v'` and `'q'`.

History: the first design used the chat word `ready` as the start trigger because it was unclear
whether a non-host client could press READY at all. The live tests of 6–7 Sep 2026 showed that the
button works for every joiner (also in the eighth slot with seven fakes), so the chat word, the
server-side colour fix-up and the `READY_WORD`/`READY_BUTTON_COUNTS` options were removed.

### 6.4 STARTING → RUNNING

- Wait for `'v' MREADY(player, 2)` from every connected real client; `player` is the client's game
  player index after its start-position shuffle (F24), so it is only range-checked (0..7); `state`
  must be 2, exactly once. Anything else is a hard violation and evicts the client (§9), as the
  original server does.
- `MREADY_TIMEOUT_MS` (default 30 s): clients that never report are evicted (no `DISCONNECT`, R14:
  their base becomes a server bot when the battle starts, or stands idle). Evicting the last missing
  client completes the transition immediately.
- When all reported: `time = 0`, `clientTime[s] = 0`, `lastStep = now`, `state = RUNNING`. The first
  sync frame is `[UNTIL(0, 8)][TICK_SPEED 33][CHEAT(0, 0) in debug mode (F28)][0x00]`.

Mercenary and the other fake players are human slots without a client, so their units stand idle;
that is intended (decision of 7 Sep 2026: fakes always stay fake humans, they are never handed to
the AI with a `DISCONNECT`).

### 6.5 The "ready limit": everything that can block READY on a client

All lobby code paths that can prevent a player from reaching status 2, or throw it back to 1:

| Cause | Where | Possible with this server? | Countermeasure |
|---|---|---|---|
| **Colour conflict**: `'h' 2` is refused (button flashes, status forced back to 1) when another *ready* slot has the same colour. Both the local button handler and the `'h'` handler that processes the relayed message apply this. | `0x4115EB`, `0x40F2B8`–`0x40F2E1` | Only if the server hands out duplicate colours. A server that never sends `'l'` or gives everyone colour 0 shows exactly this symptom: the first player can get ready, nobody else can | Colour = slot index in the join dump; `'k'` mirrored server-side; on `ready` the server first moves the player to a free colour (§6.3) |
| **Stale colour lock** after a ready player leaves | F20 | Yes, unless prevented | `'h'(0, slot)` before `DISCONNECT(slot)` (§5.6) |
| **Slot 0 never ready**: the lobby only ends when no slot has status 1 | F3 | By design, Mercenary (and any other fake) holds the lobby | `'h'(0, q)` for every fake slot once all real players pressed READY and the countdown ran (§6.3) |
| **Host un-readies everyone**: CD rule (F5) or scenario too small (F22) | `0x41081D`, `0x41144C` | No. The CD rule runs only on player 0, which is the server; the scenario check cannot fire with 8 slots | `'o'(8+p, 1)` for all p is broadcast anyway (cosmetic) |
| **No CD in the client's drive** | — | No. The READY path never reads the CD flags (F5) | — |

Answer to "broadcast every player as having the disc": possible, free, and already part of the join
dump and of the `'o'` policy (§6.2); every lobby then shows a CD icon for every player. It does **not**
add a way past a stuck READY, because no client-side READY check reads the CD flags; the only host-side
CD consequence (un-ready all) can no longer happen when the host is the server. The two mechanisms
that really block READY are the colour lock (including the stale-lock case) and the host-slot rule,
and both are covered above. The start trigger is the READY button (`'h' 2` from each real player); the
chat-word alternative of the first design was dropped on 7 Sep 2026 (§6.3).

---

## 7. Random placement (R9) and what the server can really control

Because of F12 the mapping "lobby slot → start location" is a fixed function of the set of occupied
slots. The server therefore:

1. Assigns every joiner a **random free slot in 1..7** (never the lowest free one). With the same
   set of players this alone gives a random permutation of the players over the start locations.
2. Re-randomises on every `reset()`, so each new game gets a fresh assignment.
3. Optionally (`FILL_EMPTY_WITH_AI`, default off) declares the remaining slots as AI players (type 0
   easy / 1 hard, `'j'`). With all 8 slots occupied the shuffle spreads the players over all 8
   locations instead of the same `k` locations every game. This changes the game (AI opponents),
   so it is the user's choice.
4. `FAKE_PLAYERS=n` (default 1) puts `n − 1` further fake humans, like Mercenary, into **random**
   slots at every reset. With `FAKE_PLAYERS=7` all eight slots are occupied, the one real player's
   slot changes from game to game, and so does its start position over all eight locations, without
   adding AI opponents (the fakes' bases stand idle). This is the mode of the solo live test.

Optional later step: reproduce the shuffle in the server (the RNG is a 256-entry table at
`0x488F20` indexed by `(seed + 1) & 0xFF` per call, seed 0) to log which start location each player
got and to validate the `player` byte of in-game commands against the sender (anti-cheat hardening).

---

## 8. In-game strict lockstep (`game.js`)

### 8.1 Frame layout

```
[0x02][i32 time][i32 until]  [cmd][cmd]…  [0x00]
 ^ first byte = UNTIL (F6)   ^ commands received since the previous step, in arrival order (F7)
```

Every client receives exactly this payload; only header byte 1 differs (sequence nibble, F1).
Budget: 1024 − 3 (header, terminator) − 9 (`UNTIL`) = **1012 bytes of commands per frame**.
Commands are queued as **groups** (all commands of one client frame stay together, e.g.
`[0x19 waypoints][0x16 order]`). If the next group does not fit, it and everything behind it waits
for the next step (a delay of one step, still identical for everyone). A single group larger than
1012 bytes cannot happen (client frames are ≤ 1024).

Why not `[cmds][UNTIL]`: the receive loop looks only at the first byte (F6); a frame starting with
a command would be held but never arm `until`, and the game would stall.

### 8.2 Step loop

```js
// setInterval(step, 5) — drift-free because it integrates real elapsed time
function step(now) {
  if (state !== RUNNING || paused) { lastStep = now; return; }
  acc += now - lastStep; lastStep = now;
  const n = Math.min(255, Math.floor(acc / TICK_MS));
  if (n === 0) return;
  const until = time + n + LOOKAHEAD;                 // LOOKAHEAD = 8 (F17)
  if (until - minClientTime() >= MAX_LAG) {           // MAX_LAG = 200: someone is far behind
    acc = Math.min(acc, TICK_MS);                     // do not build up a burst
    noteStall(now);                                   // §9.5: evicts the laggard after LAG_DROP_MS
    return;
  }
  clearStall();
  acc -= n * TICK_MS;
  const cmds = takeGroups(queue, 1012);
  broadcastSync(time, until, cmds);
  time += n;
}
```

- `until` is strictly increasing (n ≥ 1), satisfying F9's assertion `until > game_time` on re-arm.
- `minClientTime()` is over connected real clients only. With no client left the room resets.
- `TICK_SPEED(TICK_MS)` is queued again every `SPEED_REFRESH_S` (default 30 s) and after every resume;
  it is idempotent for the client (F11) and guards against a missed first frame.
- Every broadcast sync frame registers a pending echo for each client (`pendingEchoes.set(time, now)`);
  the echo deadline and the stall handling are described in §9.

### 8.3 Handling of frames received from a client while RUNNING

| Type | Policy |
|---|---|
| `0x02 UNTIL(a, u)` | consume. `a ≠ -1`: echo of our frame; `a` must be in the client's `pendingEchoes` (else hard violation, §9), remove it and keep `now − sentAt` as the latency sample. `a == -1`: the client reached `u`; `u` must be an issued `until` and greater than its previous report (else hard violation); set `clientTime[s] = u` |
| `0x04 CHEAT(1,·)` / `(2,·)` pause / resume | if `ALLOW_PAUSE` (default true): set `paused`, relay **as a standalone frame immediately** (the client handles pause out-of-band and discards the frame, F6). Otherwise drop |
| `0x04` other, `0x03`, `0x08` | **drop**, log (R5, R10) |
| `0x0F bonus(player)` | **queue** (F47: the diplomacy screen's "give 1000"; the giver deducted locally, only the receiver's machine adds); `player > 7` strikes. Aimed at the Mercenary it buys the alliance (§19.8) |
| `0x11`, `0x12`, `0x13` speed | **drop** (R11) |
| `0x0E chat(from, mask, text)` | drop if the text after the first `':'` (trimmed) equals one of the three cheat strings (F13); otherwise queue |
| `0x10 DISCONNECT` | drop (server-originated only) |
| `0x01 TICK` | drop (server-originated only) |
| letters | drop (ignored by the game in-game anyway) |
| `0x05 0x06 0x07 0x09 0x0A 0x0B 0x0C 0x0D 0x14 0x15 0x16 0x17 0x18 0x19 0x1A 0x1B` | **queue** (as a group with the other commands of the same client frame) |
| unknown / malformed | drop the frame, log |

Everything that is queued goes out only inside the next sync frame (R6). The only bytes ever sent
outside a sync frame while RUNNING are the pause/resume relays (which the client never treats as
commands) and nothing else.

### 8.4 Disconnects and end of game

- Client lost → its `clientTime` no longer gates pacing and its pending echoes are dropped. When the
  bots are on and the engine is active the base becomes a server bot (§19.9, since 13 Sep 2026);
  otherwise it **stands idle**: units and buildings stay, the survivors must destroy them to win (F49).
  **No `DISCONNECT` is ever sent in battle** since 27 Sep 2026 (R14): the game's handler `0x41DBE0` would
  hand the base to Krusty on every client's machine. Until then `DISCONNECT(slot)` was queued (F19) as
  the fallback.
- Last real client gone → `reset()`: state LOBBY, all slots 1..7 empty, queue cleared, new random
  assignment for the next joiners. There is no in-game end detection (the server does not simulate);
  the game ends for the server when everybody has left.
- New connections while STARTING/RUNNING are refused (§5.2). No late joins, as in the original.

---

## 9. Eviction: clients that stop answering or answer wrongly

The original server only reacts to a broken socket. This server also treats a client that does not
produce the answers the protocol expects, or that violates the protocol, as gone: its slot is freed and
every other client is told that the player left the lobby or the battle, so nobody waits for a dead
peer. Mercenary (slot 0) never answers and is exempt.

### 9.1 Expected answers per phase

| Phase | After the server sent | The client must send | Deadline (config) | Evidence |
|---|---|---|---|---|
| LOBBY, joining | `'d'` + join dump | its CD report `'o'(8+p, cd)`, the first message of every joiner - sent by the connect code before the lobby screen exists, so it does not start the keep-alive clock (F71) | `JOIN_TIMEOUT_MS` = 15 s until the first non-`VAR` command | F23, F71 |
| LOBBY | — | `'q'` keep-alive every 700 ms | `KEEPALIVE_TIMEOUT_MS` = 3 s (≈ 4 missed) | F23 |
| STARTING | `'h'(0, q)` for the fake slots | `'v' MREADY(game player index, 2)`, exactly once | `MREADY_TIMEOUT_MS` = 30 s | §6.4, F24 |
| RUNNING | each sync frame `UNTIL(a, u)` | echo `UNTIL(a, u)` | `ECHO_TIMEOUT_MS` = 5 s per frame | F10, F23 |
| RUNNING | each sync frame `UNTIL(a, u)` | `UNTIL(-1, u)` once its simulation reaches `u` | `LAG_DROP_MS` = 10 s of stalling the game (§9.5) | F10, F17 |
| any | anything | well-formed frames only: length 3..1024, `0x00` terminator, expected sequence nibble, known types, sizes as in §10 | immediate | F1, F7, F21 |
| any | — | at least one byte | `IDLE_TIMEOUT_MS` = 10 s (fallback) | — |

`'q'` is produced by the lobby loop only, so it stops while the client loads the map. That is why
STARTING is covered by the `MREADY` deadline and RUNNING by echoes, not by keep-alives.

### 9.2 Bookkeeping per client

```
lastSeen        ms of the last received byte
joinedAt        set when 'd' was sent; cleared by the first 'o'
pendingEchoes   Map<a, sentAt>: one entry per sync frame sent, removed by the echo
reachedUntil    last u reported with UNTIL(-1, u); must be an issued until and increasing
strikes         soft-violation counter
```

A 500 ms watchdog timer checks the deadlines of every client; the in-game step loop additionally
tracks the stall condition (§9.5).

### 9.3 Violations

Hard violations evict immediately:

- frame decode error: length outside 3..1024, missing `0x00` terminator, truncated command, unknown
  command type, command size not matching §10 (F1, F7);
- wrong sequence nibble while `STRICT_SEQ` is on (duplicates, `seq == previous`, are skipped as the
  game does; anything else is an error). With `STRICT_SEQ=false` the server resynchronises like the
  original server and only logs;
- unknown lobby message type (the game itself would die on it, F21);
- `MREADY` with a player index outside 0..7, `state ≠ 2`, or a second `MREADY` (the index is the
  game player index, F24, so it cannot be checked against the slot);
- echo `UNTIL(a, ·)` with an `a` the server never sent to this client, or `UNTIL(-1, u)` with a `u`
  that was never issued or is not greater than the previous report.

Soft violations add a strike; `STRIKE_LIMIT` (10) strikes evict:

- cheats: `0x04` with `a ∉ {1,2}`, `0x0F`, `0x03`, chat with a cheat text (F13);
- messages of the wrong phase: lobby letters other than `'q'` while RUNNING, numeric in-game types
  while in LOBBY, a second `'y' INIT_ME`.

Not violations, dropped silently: `0x08` sync checks, `0x11/0x12/0x13` speed messages and the client's
own `'o'` CD reports, because every healthy client sends them (F11, F14, F23); lobby messages for
another player's slot (`'g' 'f' 'k' 'm' 'h'` with a foreign slot) and host-owned settings (`'j' 'l'
'n' 'i' 'p'`, VAR indices other than the CD flag), because the lobby UI lets any player click another
slot's buttons (the READY handler accepts the control ids of all eight slots, `0x4115A9`), so these are
ordinary clicks rather than misbehaviour.

### 9.4 Eviction procedure

1. Log `{slot, name, address, phase, reason}`.
2. `socket.destroy()`; anything still buffered from that client is ignored.
3. Announce, depending on the phase:
   - **LOBBY**: if the player was ready, `'h'(0, slot)` first so every client releases its colour lock
     (F20); then `DISCONNECT(slot)` as a standalone frame (clients empty the slot: type 3, status 0);
     then `'e' "<name> left the lobby (<reason>)"` (no name in front of relay lines, §17.8). A running start countdown is cancelled
     and the ready check is re-evaluated, because the remaining players may now all be ready.
   - **STARTING**: nothing is sent (R14, since 27 Sep 2026; before: `DISCONNECT(slot)` queued for the
     first sync frame). The slot is removed from the `MREADY` wait list, which may complete the
     transition at once; the base gets a server bot when the battle starts (§19.9) or stands idle.
   - **RUNNING**: nothing is sent either (before 27 Sep 2026: `DISCONNECT(slot)` in the next sync frame,
     which made every client print "<name> lost, AI taking over" and run the game's AI for that base).
     The base becomes a server bot or stands idle; the slot leaves `minClientTime()` and its
     `pendingEchoes` are dropped.
4. If no real client remains → `reset()`.

There is no in-game chat from the server, so the other players see nothing when somebody leaves a
battle: the base simply stops acting (or is run by a server bot). Evicted players cannot rejoin a
running game (no late joins, F17); they can reconnect once the room is back in LOBBY.

### 9.5 Lag eviction

The lockstep stalls when `until − minClientTime() ≥ MAX_LAG` (200 ticks, 8.8 s at 44 ms; 6.6 s at 33 ms). The
original server stalls forever. Here the step loop records when a stall began and which slot has the
smallest `clientTime`. If the stall persists for `LAG_DROP_MS` (10 s) that slot is evicted (one per
check, then re-evaluate); the record is cleared as soon as the condition clears. `LAG_DROP_MS=0`
disables lag eviction and restores the original behaviour. The echo deadline is independent: a client
whose network is fine but whose simulation is frozen keeps echoing and is caught by lag eviction; a
client whose network is gone stops echoing and is caught by `ECHO_TIMEOUT_MS`.

---

## 10. Command size table (`commands.js`)

Needed to split client payloads into commands. All integers little-endian; `str` = NUL-terminated.

| Type | Name | Payload after the type byte | Size |
|---|---|---|---|
| `0x01` | TICK | u8 n | 1 |
| `0x02` | UNTIL | i32 a, i32 until | 8 |
| `0x03` | create object | i16,i16,i16,i16,u8,u8,u8,u8,u8 | 13 |
| `0x04` | CHEAT | i16 a, i16 b | 4 |
| `0x05` | order object | i16 obj, u8 order | 3 |
| `0x06` | target position | i16 obj, i16 x, i16 z | 6 |
| `0x07` | waypoints, objects | u8 n, i16 count, n×(i16,i16), count×i16 | 3 + 4n + 2·count |
| `0x08` | sync check | i16 checksum, i32 time | 6 |
| `0x09` | research | u8 item, u8 level, u8 player | 3 |
| `0x0A` | build | u8 type, u8 player, u8 count | 3 |
| `0x0B` | target object | i16 obj, i16 target | 4 |
| `0x0C` | setting | u8,u8,u8,u8 | 4 |
| `0x0D` | diplomacy | u8 pa, u8 pb, u8 which, u8 on | 4 |
| `0x0E` | chat | u8 from, u8 to_mask, str text | 2 + len + 1 |
| `0x0F` | bonus | u8 player | 1 |
| `0x10` | DISCONNECT | u8 slot | 1 |
| `0x11` | TICK_SPEED | i32 ms | 4 |
| `0x12` | TICK_MAXSPEED | u8 player, i32 ms | 5 |
| `0x13` | TICK_DESSPEED | i32 ms | 4 |
| `0x14` | select | u8 player, i16 obj…, i16 0xFFFF | 1 + 2k + 2 |
| `0x15` | deselect | u8 player | 1 |
| `0x16` | order selected | u8 player, u8 order | 2 |
| `0x17` | target pos selected | u8 player, i16 x, i16 z | 5 |
| `0x18` | target obj selected | u8 player, i16 target | 3 |
| `0x19` | waypoints selected | u8 n, u8 player, n×(i16,i16) | 2 + 4n |
| `0x1A` | order 0x0D selected | u8 player | 1 |
| `0x1B` | move-to selected | u8 player, i16 x, i16 z | 5 |
| `'d' 'r'` | VERSION | i16 version, i16 id | 4 |
| `'v'` | MREADY | i16 player, u8 state | 3 |
| `'u' 'w' 'o'` | GROUP / GVERSION / VAR | i16, i16 | 4 |
| `'s' 't'` | INTRO / OUTRO | i16 id | 2 |
| `'g'` | NAME | i16 player, str name | 2 + len + 1 |
| `'h' 'f' 'j' 'k' 'l' 'm' 'n'` | status / race / type / colour± / colour / team± / team | u8 value, u8 player | 2 |
| `'y' 'p'` | INIT_ME / NUKE | u8 player | 1 |
| `'e'` | chat | str "Name: text" | len + 1 |
| `'i'` | scenario | str file, str title | 2 strings |
| `'q'` | keep-alive | — | 0 |

Builders are needed for: `'d' 'i' 'l' 'g' 'f' 'j' 'n' 'h' 'o' 'e'`, `0x02`, `0x10`, `0x11`.

---

## 11. Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8888` | listen port (Fly maps 8888 and 8889 to it) |
| `HEALTH_PORT` | unset | optional bare TCP liveness port for a Fly check; the game never talks to it |
| `ROOMS` | `J8PLAY01,D8PLAY01,D8PLAY02,D8PLAY03,D8PLAY05,J8PLAY02,J8PLAY07` | 1..7 rooms, one map each (§17.6): `SCENARIO/MPLAYER` file names, looked up in `src/maps.js` (F31); a map not in the table is written `FILE:Name[:terrain]`, name ≤ 42 chars. The 2nd character of the file is the player count and caps the room (F22). Seven at most: one lobby row per room, the eighth row is the player's own (F33). Room 1 is a jungle map (maintainer, 7 Sep 2026); no room is preselected since 12 Sep 2026. Replaces `MAP_FILE`/`MAP_TITLE`/`MAP_TERRAIN` of 2.0 |
| `HALL` | `true` | the room-selection lobby (§17); `false` = every connection goes straight into room 1 as in 2.0 |
| `HALL_REFRESH_MS` | `200` | hall: rows, icons and map line are recomputed from the live room states every this many ms (≥ 50) and only changes are sent. Called `MARQUEE_MS` until 26 Sep 2026, when the rows scrolled one character per step (300 was too slow for the maintainer, 7 Sep 2026); the old name is still read |
| `PACK_LOBBY_FRAMES` | `true` | several commands per lobby frame (F34): one frame per refresh and per dump; `false` = one command per frame as the original host and 2.0 |
| `STATS_INTERVAL_S` | `30` | in-battle stats log line (latency, ticks behind, pending echoes, stalls); `0` = off |
| `TICK_MS` | `44` | 150 % speed (F11); `33` = 200 % until 10 Sep 2026 |
| `LOOKAHEAD` / `MAX_LAG` | `8` / `200` | lockstep constants (F17) |
| `MIN_PLAYERS` | `1` | real players needed before the countdown may start (default 2 until 7 Sep 2026; the maintainer set it to 1, so a lone player can start against the idle Mercenary) |
| `START_COUNTDOWN_S` | `3` | seconds between "everyone ready" and `'h' 2,0` |
| `MREADY_TIMEOUT_MS` | `30000` | drop clients that never finish loading |
| `IDLE_TIMEOUT_MS` | `10000` | phase-independent fallback: no byte at all for this long → evict |
| `JOIN_TIMEOUT_MS` | `15000` | a joiner must send its first lobby-loop message (anything but the connect-time `'o'` CD report) this soon after the dump; 5 s until 28 Sep 2026, when 1920x1200 clients (hall screen up after 3-4.5 s) were evicted by the keep-alive clock the CD report had started (F71) |
| `KEEPALIVE_TIMEOUT_MS` | `3000` | lobby: no `'q'` for this long (≈ 4 missed) → evict |
| `ECHO_TIMEOUT_MS` | `5000` | battle: the echo of a sync frame did not arrive → evict |
| `LAG_DROP_MS` | `10000` | battle: the same client has stalled the game for this long → evict it; `0` = never (original behaviour) |
| `STRIKE_LIMIT` | `10` | soft violations before eviction |
| `STRICT_SEQ` | `true` | a wrong sequence nibble is a hard violation (`false` = resync like the original server) |
| `MERCENARY_RACE` | `random` (0 until 19 Sep 2026) | race of a fake player: `0` Human, `1` Gray, `random` = drawn per bot when its slot is made (`Room.fakeSlot`, `Room.random`; maintainer, 19 Sep 2026: "bots must have random race") |
| `FAKE_PLAYERS` | `1` (2 from 13 to 19 Sep 2026, 1 before) | fake humans including the master bot AI Mercenary (1..7) at the start of every game, the others in random slots (`FAKE_NAMES`: AI Marauder, Renegade, ...); every one of them is a bot when `MERCENARY_AI` is on (§19.8). Since 19 Sep 2026 the count is the players' choice per room: **`/botcount N`** in the lobby chat (§19.10), the default is the bare minimum (maintainer). `MIN_PLAYERS ≤ 8 − FAKE_PLAYERS` |
| `MERCENARY_NAME` | `Mercenary` | display name of the fake host in slot 0 (`AI Mercenary` from 12 to 19 Sep 2026; maintainer, 19 Sep: "remove AI label"); at most 16 characters (F33) |
| `FAKE_NAMES` | `Mercenary,Marauder,Renegade,Outlaw,Nomad,Drifter,Vagabond,Raider` | names for the fakes, slot 0 always `MERCENARY_NAME` (the `AI ` prefixes went on 19 Sep 2026) |
| `DEBUG_MODE` | `false` | debug mode (also implied by `LOG_LEVEL=debug`): full map view for everybody at game start (F28) |
| `FILL_EMPTY_WITH_AI` | removed 27 Sep 2026 | empty slots became AI-typed (`0` easy / `1` hard via `FILL_AI_TYPE`), i.e. the game's own AI ran on every client; R14 rules that out, the relay's bots (`/botcount`) are the computer players |
| `ALLOW_PAUSE` | `true` | relay pause/resume |
| `SPEED_REFRESH_S` | `30` | re-send `TICK_SPEED` |
| `LOG_LEVEL` | `info` | `debug` logs every frame |
| `SYNC_CHECK` | `send` (since 27 Sep 2026 - the bots need the engine; `off` until then, `send` in `fly.toml` since 11 Sep 2026; the test harnesses pin `off`) | the battle engine (§18): `off` relay only; `shadow` the engine runs beside the relay, its checksums are logged, recorded and compared with `0x08` messages from clients; `send` = shadow plus one `0x08 (checksum, tick)` in every sync frame. A wrong checksum aborts the *client* ("sync error"), so `send` only with a verified engine |
| `RECORD_DIR` | unset | record every battle as JSON lines (sync frames, client checksums, engine checksums, MREADY, disconnects) for `tools/replay.js` (§18.4); independent of `SYNC_CHECK` |
| `RECORD_LOG` | `false` (`on` in `fly.toml` since 18 Sep 2026) | record every battle into the **log** as compact `msg: "replay"` lines (§18.6), lossless for what the clients received: every sync frame byte for byte (UNTIL, the server's `0x08`, the commands) in chunks of 256 frames / 3000 hex characters, every client `0x08`, the events; only the engine's per-tick checksum lines are left out. 4-11 KB per game-minute (about 10 in `send` mode), lines of at most a few KB. `node tools/logs2replay.js --fetch dark-colony-server` rebuilds the recordings from Fly's Logs API (about seven days of history, F50) for `tools/replay.js`. Works beside `RECORD_DIR` |
| `REPLAY_FILE` | unset | **replay mode** (§18.7): play this recording (a `RECORD_DIR` file or one rebuilt by `tools/logs2replay.js`) back to a real client. One room, no hall, no bots, the recorded speed; the lobby is the recorded one and the recorded sync frames are broadcast byte for byte. `SYNC_CHECK=send` becomes `shadow` (the frames already carry the original checksums) |
| `REPLAY_SLOT` | `-1` | replay mode: the recorded human whose seat the connecting client takes (`-1` = the first recorded real player); race, colour and team of that seat are pinned to the recording |
| `REPLAY_FULL_MAP` | `false` | replay mode: reveal the whole map to the watcher. `CHEAT(0, 0)` (the game's own full-map flag, F28) goes out as a standalone frame at battle start; the recorded frames stay untouched. **Desyncs the viewer** (confirmed 18 Sep 2026, §18.7): the flag reaches the simulation, the replay aborted with a sync error at tick 3944 (about 3 minutes in) and ran to the end without it. Only for a short look at the opening; a full viewing needs it off |
| `MERCENARY_SLOT` | `0` | lobby slot of the fake host. With 0 nobody sends `0x08` (F14). A higher slot (7) makes the lowest real player the checksum sender, which `shadow`/`RECORD_DIR` need for verification; slot 0 is then never given to a real player (F40) |
| `BOT_TYPE` | `krusty` | the bots' brain in a fresh room: `krusty` = the port of the game's own computer player (§19.10, `src/engine/krusty.js`), `rusher` = the purpose-built rusher of §19.8 (`src/rusher.js`), `random` = every bot draws one at game start. The players change it per room with **`/bottype T`** (maintainer, 19 Sep 2026: "so there is a possibility to apply rusher too"); a room reset restores the default. Replaces `MERCENARY_AI` (`rusher`/`off` 13-19 Sep 2026): there is no `off` any more - the bots idle only without the engine (`SYNC_CHECK=off`) or in replay mode |
| `BOT_HIRE` | `false` | may the bots be hired in a fresh room (the 1000-money alliance of §19.8)? Off since 19 Sep 2026 (maintainer: "switch off hiring of bots"); the players turn it on per room with **`/bothire on`**. Off = no offer at the start, a `0x0F` to a bot is returned with a word, the bots stay at peace with each other and nobody's allies |
| `BOT_TEAM` | `0` | team bots per real player in a fresh room (`/botteam N`, 0..6, §19.11): fake humans on the player's lobby team, in battle its allies with shared vision until the player loses the connection or the game ends; not for hire |
| `MERCENARY_ALLY_S` | `45` (120 from 13 to 19 Sep 2026) | seconds an alliance bought for 1000 lasts (maintainer, 19 Sep 2026: "hiring of the bot must remain for 45 sec"); payments arriving while one runs are returned (§19.8) |
| `MERCENARY_THINK_TICKS` | `32` | decision interval of a bot in game ticks (the original AI's 32, F45) |
| `BOT_SEED` | `0` | seed of the bots' private RNG (the krusty bot walks the game's `rand()` table on its own index); `0` = random per game, else bot *i* starts at `(BOT_SEED + 17 i) & 0xFF`, which makes a recorded game's bot decisions reproducible (§19.10) |
| `AI_SEND` | `false` | the engine runs the game's own AI (`engine/ai.js`) for computer lobby slots and `DISCONNECT` takeovers since 19 Sep 2026, but that exact mode is unverified against a real client: in `send` mode such a game stops sending checksums unless this is `true` (a wrong checksum kicks every client); `shadow` compares and is the verification path (§19.10) |

---

## 12. Fly.io deployment

Base: the `Dockerfile` and `fly.toml` of [Dark-Colony-Server](https://github.com/endotermic/Dark-Colony-Server)
are proven to work with `dc16.exe` on Fly (app `dark-colony-server`, region `iad`, port 8888, raw TCP).
Both files are copied as they are and only the changes listed below are made.

Facts that matter:

- **IPv4.** Fly's shared IPv4 only routes HTTP/TLS (it needs SNI or a Host header); raw TCP on 8888
  needs a **dedicated IPv4** (`fly ips allocate-v4`, about 2 USD/month). Check what the working app
  uses with `fly ips list -a dark-colony-server` and do the same for the new app, or deploy the new
  code under the old app name to keep its address and hostname. IPv6 is useless here: the game uses
  Winsock 1.1 `inet_addr`/`gethostbyname`, IPv4 only.
- **Port 8889.** The game retries it when 8888 fails (`0x42DC2A`); the working config exposes only
  8888, so add a second `[[services.ports]]` entry mapped to the same internal port.
- **No health checks in the working config**, and none are needed: Fly restarts a crashed process
  anyway. If a check is added later it must never target 8888 (a probe would be handed a lobby slot and
  then vanish); use a top-level `[checks]` TCP check on a separate `HEALTH_PORT`.
- **One machine, always on.** The working config omits `auto_stop_machines`/`auto_start_machines`,
  which leaves the machine running; keep that (`fly scale count 1`). Lockstep state lives in memory,
  so two machines would be two different servers.
- **Concurrency limits** (`hard_limit 50`, `soft_limit 30`) from the working config are more than the
  8 connections a game needs; keep them.
- **Size.** Traffic is tiny (keep-alives every 700 ms, sync frames every 44 ms of at most 1 KiB);
  the default `shared-cpu-1x` / 256 MB machine is enough. Fly proxy idle timeouts are not a concern
  because the game never goes silent.

```toml
# fly.toml — the 1.x configuration of this repository, unchanged except for the second port.
app = "dark-colony-server"       # the existing app: same IP and hostname as before
primary_region = "iad"           # as in the working config; move closer to the players if wanted

[build]
  dockerfile = "Dockerfile"

[env]
  PORT = "8888"

[[services]]
  internal_port = 8888
  processes = ["app"]
  protocol = "tcp"

  [services.concurrency]
    hard_limit = 50
    soft_limit = 30

  [[services.ports]]
    port = 8888
  [[services.ports]]
    port = 8889                  # dc16.exe retries this port when 8888 fails

# Optional, only once the basics work. Never a check on 8888.
# [checks.health]
#   type = "tcp"
#   port = 8080                  # HEALTH_PORT, a bare listener the game never talks to
#   interval = "15s"
#   timeout = "5s"
#   grace_period = "10s"
```

```dockerfile
# Dockerfile — copied from Dark-Colony-Server. Changes: Node 22 (Node 20 is end-of-life), entry point.
FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev       # no runtime dependencies; kept for parity with the working image
COPY . .
EXPOSE 8888
CMD ["npm", "start"]             # package.json: "start": "node src/index.js"
```

`.dockerignore` as in the working repo (`node_modules`, `npm-debug.log`, `.git`, `.gitignore`,
`Dockerfile`, `fly.toml`) plus `test/`. `package.json`: `"engines": { "node": ">=22" }`,
`"start": "node src/index.js"`, `"test": "node --test"`, no dependencies.

Commands: `fly launch --copy-config --no-deploy` (keeps this fly.toml), `fly ips allocate-v4` unless
the old app's address is reused, `fly deploy`, `fly scale count 1`, `fly logs`, `fly ssh console`.
Players connect with the app hostname (`<app>.fly.dev`) exactly as with the working server.

---

## 13. Testing

1. **Unit tests** (`node --test`): frame encode/decode round trips, sequence duplicate/resync logic,
   every entry of the size table against hand-built buffers, the lobby policy table (own-slot
   enforcement, colour lock mirroring, `ready` parsing), the sync frame builder (budget, group
   integrity, `UNTIL` first), the step loop with a fake clock (n, until monotonic, stall at 200), the
   eviction deadlines and the strike counter with a fake clock (§9).
2. **Scripted clients** (`test/fakeclient.js`): a minimal dc16 client that connects, validates `'d'`,
   records the dump, sends `'o'` and `'q'`, presses READY, sends `MREADY`, echoes every `UNTIL`, reports
   `UNTIL(-1, u)` at a configurable simulated tick rate, and injects commands (including cheats and
   `0x08`). Run 2–7 of them and assert that all received sync payloads are byte-identical and that
   forbidden commands never appear.
3. **Eviction scenarios** with the same script misbehaving on demand: silent after joining, no
   keep-alives, no echoes, slow simulation (laggard), wrong sequence nibble, garbage bytes, no `MREADY`,
   duplicate `MREADY`, cheat spam. Assert that the offender is evicted within the configured deadline
   with the right announcement (`'h' 0` + `DISCONNECT` in the lobby, `DISCONNECT` inside the next sync
   frame in battle), and that the remaining clients keep receiving identical sync payloads and the game
   keeps pacing.
4. **Real game on LAN**: run the server locally, two PCs (or one PC + one VM) with
   `DC - Classic/dc16.exe` connecting to the LAN IP. Check: lobby shows Mercenary + players, colours
   unique, `ready` works, both machines start together, speed is 200 %, options-screen speed change
   has no effect, cheat texts do nothing, pause works, disconnecting one machine hands its base to AI.
5. **Fly**: same with the public address, watch `fly logs` for latency samples, stalls and evictions.
6. **Reference capture (optional)**: Wireshark a genuine host session (host + one client on LAN) to
   confirm the exact join dump order and the `'i'` file-name casing before finalising §6.1.
7. **Smoke test with the real game** (`tools/smoketest.js`, 8 Sep 2026): scripted clients enter the
   rooms of a running server through the hall (`/N` + READY) and stay there while a person connects
   with `dc16.exe`. The plan `room:count:policy` (default `1:7:hold,2:3:follow`) fills room 1 to
   `(7/7) full` with bots that never press READY and seats three bots in room 2 that press READY only
   when a real player in the room does (and release it when that player does), so the person can start
   a battle with them; `auto` bots ready up at once and put a room `in battle`. An observer stays in
   the hall, selects every room in turn to read the map line the server paints (`N <map> <terrain>
   (k/s) <state>`) and once presses READY on every room that is not open, expecting `Cannot join room
   N: ...`. Status lines every 30 s; when the last real player leaves a battle the bots leave too (the
   room resets) and enter the room again for the next round. Exit code 1 if a bot could not be seated
   or was dropped in the lobby, or a refusal did not come. The bots tick at 20 ms so they are never
   the laggards of a battle. The READY policies of the scripted client are pinned by an integration test.

---

## 14. Implementation order

| Step | Deliverable | Done when |
|---|---|---|
| 1 | `frame.js`, `commands.js` + tests | all size-table entries round-trip |
| 2 | `client.js`, `room.js` skeleton, `'d'` handshake, idle timeout, logging | real `dc16.exe` reaches the lobby screen and stays (keep-alives consumed) |
| 3 | `lobby.js`: join dump, policy table, Mercenary chat | two real clients see each other, Mercenary, map name, unique colours |
| 4 | READY handling, countdown, start signal `'h'(0, q)`, STARTING/MREADY | both clients leave the lobby together and load the map |
| 5 | `game.js`: step loop, sync frames, echo/reached handling, `TICK_SPEED(33)` | game runs at 200 %, units move on both machines identically |
| 6 | filters (cheats, `0x08`, speed), pause, eviction rules (§9), DISCONNECT, reset | cheat texts inert, speed locked, a silent or misbehaving client is announced as gone within its deadline, leaving player becomes AI, room reusable |
| 7 | `fakeclient.js` + integration tests | 7 scripted clients receive identical sync payloads |
| 8 | Dockerfile, fly.toml, dedicated IPv4, deploy | public game with ≥ 2 remote players |
| 9 | Optional: `FILL_EMPTY_WITH_AI`, `FAKE_PLAYERS`, debug full-map view, shuffle replication for logs/validation | as configured |

Status (end of 6 Sep 2026): steps 1–7 are implemented (now this repository) and covered by 42 passing tests
(`node --test test/`, including two end-to-end tests over real TCP with scripted clients). The LAN
tests of §13.4 are done with real `dc16.exe` clients: one player to victory, then two players on two
PCs with perfect sync for a full battle (§16). Step 8 is done: version 2.0 was deployed to Fly.io on
7 Sep 2026 (§16). Of step 9, `FILL_EMPTY_WITH_AI`, `FAKE_PLAYERS` and the debug full-map view exist;
the shuffle replication does not. Still open: a test with three or more real players over the
internet, and §13.5 (a capture of a genuine host) which is now optional.

---

## 15. Open points and risks

- **Join dump details** not verified against a live host: whether the joining client sends its own
  `'g'` name automatically, and the exact `'i'` file-name casing (`D8PLAY01.SCN` is what the previous
  server used; the game opens files case-insensitively on Windows anyway). A Wireshark capture
  (§13.6) settles both.
- **Old "ready limit" root cause** is inferred, not observed: §6.5 predicts that duplicate or stale
  colour locks are what stopped players from getting ready on the previous server. Checking which
  colours (`'l'`) that server assigns, or reproducing with two clients, would confirm it.
- **Eviction is deliberately strict**: a player whose machine stops simulating (a modal in-game screen,
  a frozen or minimised window, a laptop going to sleep) first stalls everyone for 200 ticks (6.6 s at
  33 ms) and is then dropped after `LAG_DROP_MS`; their base becomes AI and they cannot rejoin the
  running game. Players should use pause (`CHEAT 1`) for breaks. `LAG_DROP_MS=0` restores the original
  "stall forever" behaviour.
- **Sequence strictness**: verified live, the client's outgoing sequence starts at 0 and increments
  by one per frame; `STRICT_SEQ=true` is safe. `STRICT_SEQ=false` remains available as the original
  server's resync behaviour.
- **No checksums**: with a fake human in slot 0 nobody sends `0x08` (F14), so a desync would not be
  detected by the game itself. The maintainer decided (7 Sep 2026) that checksums are neither
  generated nor forwarded; the two-player test showed perfect sync without them. Since 11 Sep 2026
  the server can generate them itself from its own simulation (§18, `SYNC_CHECK=send`), once the
  engine port is verified bit-exact against recorded games; until then the option stays `off`.
- **Colour-0 lock on the second joiner** is fixed by the `'h'(0, 0)` start signal but not explained
  (F3, §16). If a client ever refuses a human player's ready message the same way, the `tx` trace
  will show what it received.
- **Short ticks on slow machines**: the lockstep stalls when a client is 200 ticks behind, which at
  44 ms is 8.8 s of lag (6.6 s at the earlier 33 ms). A very slow PC will make the game stutter for everyone. Keep `TICK_MS`
  configurable; an adaptive fallback (accept `TICK_MAXSPEED` reports and lower the speed when a
  client cannot keep up) is a possible later addition, deliberately not in the default.
- **Idle fakes**: Mercenary and the other fake humans are slots without a client and just sit there.
  That is the maintainer's decision (7 Sep 2026); they are never handed to the AI.
- **Placement**: with fewer than 8 occupied slots the same subset of start locations is used every
  game (F12). Map-wide randomness needs all slots occupied: `FAKE_PLAYERS=7` (idle fakes) or
  `FILL_EMPTY_WITH_AI` (AI opponents).
- **Mixed builds**: Dark Colony and the patched Dark Colony Ultimate of 25 Sep 2026 or later simulate
  identically (F27 revised, F65); older Ultimate builds and the untouched `ENGEXP16.EXE` desynchronise
  at a Gray commander rally, and older Ultimate builds also after an OZI session. The server cannot
  tell the builds apart (same protocol, no version field), so this rests on players using the current
  patcher. Not yet seen in a real network game.
- **Anti-cheat depth**: the server drops known cheat messages but does not validate the `player`
  byte inside commands against the sender (needs the shuffle replication of §7).
- **Room selection (§17), seen on a real client twice on 7 Sep 2026**: the hall, the scrolling
  rows, `/N`, READY into a room and the battle all worked. Corrections from the two tests (§16): the
  own row cannot be painted by the server (F33), so it shows the player's name and the selected room
  moved into the map line so that eight rooms fit; the client handles one frame per loop iteration
  (F34), so every update is one packed frame; the room size shown is the map's slots without
  Mercenary; the chat is scrolled clean with a fresh greeting when a room is entered. Still to be
  seen live: the packed frames and the map-line display (third test pending), two real players
  entering the same room through the hall, and a client whose slot is 7.
- **Fixed slot per connection** (F30): a player can only join a room where the slot picked at
  connect time is not held by another real player. The hall picks the slot free in the most rooms,
  and a fake sitting in that slot is moved aside (§17.5), so with few players this never bites; when
  it does, the row says `slot taken`, Mercenary explains, and reconnecting gives another slot. A
  room-side fix would need the game to accept a second `'d'`, which it does not.

---

## 16. Change log from live tests

Every fix found while testing against real clients is recorded here as well as in the sections
above, so that the plan can be followed from scratch without repeating the discoveries.

**6 Sep 2026, single client (Classic `dc16.exe`, same PC as the server)**

- Client was kicked with "connection lost" between lobby and battle: the server required the MREADY
  player field to equal the lobby slot. It is the game player index after the shuffle (F24).
  Fix: range-check only.
- One second after joining, the client sent `'i' "", ""` and `'h'(1, ·)` three times: the lobby reads
  the map's player count from `title[45]`, and a plain "Armageddon" title has no digit there (F15).
  Fix: build the title with the game's `%-43s (%d Player %s)` format (`formatScenarioTitle`).
- Confirmed live: first client message is the CD report, sequence numbers start at 0 and increment
  per frame, lobby frames may carry several commands, the client asks for 66 ms every second in
  battle and is ignored (F23). A full 7-minute game to victory ran at 200 % without stalls.

**6 Sep 2026, two clients (second PC on the LAN)**

- The client that joined second stayed in the lobby after the countdown while the first joiner
  entered the battle and waited; both humans showed ready on the stuck client, and it left the lobby
  only via its LEAVE button (after which the room evicted it and the other player continued alone).
  The only slot that can still have had status 1 in that client's view is slot 0, i.e. it refused
  Mercenary's `'h'(2, 0)` in the colour-lock check (F4). Fix: the start signal is `'h'(0, 0)`,
  which the client applies without any check (F3). Why colour 0 was locked on that client is not
  understood; the lobby init clears the lock table on every entry (F25).
- Added for diagnosis: a debug `tx` trace of every frame sent in the lobby, an `rx` trace of every
  command received (keep-alives, speed reports and battle `UNTIL`s left out), a 30-second `stats`
  line in battle, and the `RELAY_SYNC_CHECK` flag (F14).
- Learned: with Mercenary in slot 0 nobody sends the `0x08` checksum, so the game's own desync
  detection is inert unless Mercenary is marked lost (F14).

**6 Sep 2026, two clients again, with the `'h'(0, 0)` start signal: success**

- Both clients left the lobby together and loaded (MREADY 0.5 s and 1.0 s after the start signal),
  then played a full battle; the maintainer reports "sync is perfect".
- Server settings: `MIN_PLAYERS=2 MERCENARY_AI_TAKEOVER=true RELAY_SYNC_CHECK=true
  STATS_INTERVAL_S=30 LOG_LEVEL=debug STRICT_SEQ=false LAG_DROP_MS=0 START_COUNTDOWN_S=3` with
  15–30 s timeouts.
- Numbers from the stats lines (log kept outside git as `logs/2026-09-06-two-players-lan.log`):

  | Measure | Value |
  |---|---|
  | Battle length | 4 min 42 s, 8 258 ticks at 33 ms |
  | Sync frames sent | 8 237 (about 30 per second, one tick each) |
  | Stalls | 0 |
  | Latency, LAN client / local client | 35–51 ms / 2–17 ms |
  | Ticks behind the last issued `until` | 18–21 for both (look-ahead 8 plus one round trip) |
  | Pending echoes | at most 2 |
  | Strikes or dropped messages | 0 |
  | Checksums (`0x08`) relayed | 8 652, from the lowest real player; no client reported a sync error |
  | Relayed player commands | about 210 (selection, waypoints, orders, build, research) |

- Verified in passing: the client's sequence numbers never needed a resync, so `STRICT_SEQ=true`
  (the default) is safe; the `TICK_SPEED(66)` requests every second were ignored and the game ran
  at 200 %.

**7 Sep 2026, decisions and the fake-players mode**

- Decisions: Mercenary (and every other fake) always stays a fake human, never an AI; `0x08`
  checksums are never forwarded. The diagnostic options `MERCENARY_AI_TAKEOVER` and
  `RELAY_SYNC_CHECK` were removed from the code (F14, §6.4).
- New `FAKE_PLAYERS` (1..7) with `FAKE_NAMES`: further fake humans in random slots, re-rolled at
  every reset; the start signal sends `'h'(0, q)` for every fake slot (§6.3, §7). `MIN_PLAYERS` is
  limited to the remaining free slots.
- New debug mode (`DEBUG_MODE=true` or `LOG_LEVEL=debug`): the first sync frame carries
  `CHEAT(0, 0)`, the full-map-view flag (F28), so the tester sees the whole map.
- Live test with `FAKE_PLAYERS=7 MIN_PLAYERS=1 LOG_LEVEL=debug` (log
  `logs/2026-09-07-seven-fakes.log`, outside git): the client took the one free slot (1; fakes on
  0, 2–7), pressed the **READY button**, the countdown ran, MREADY came as game player 7, and a
  40-second battle ran with 8 ms latency and no stalls. The maintainer confirmed the READY button is
  enabled for a client that joins as the eighth player, and that the full map was visible from the
  start, which validates `CHEAT(0, 0)` as the map-reveal command (F28).
- Consequences (7 Sep 2026): the chat word `ready`, the `unready` chat, the server-side colour fix-up
  on `ready` and the `READY_WORD`/`READY_BUTTON_COUNTS` options were removed; READY presses are
  announced as `"<name> is ready (k/n)"`. The lobby greeting is now one private chat line to the
  newcomer only, instead of a broadcast on every join.

**7 Sep 2026, version 2.0 and the move to the Dark-Colony-Server repository**

- This implementation is **Dark Colony Server 2.0** (`package.json` version `2.0.0`, `src/version.js`);
  the greeting reads `"Mercenary: Welcome, <name>. Server 2.0, map Armageddon at 200%. Press READY to
  start."` and the start-up log line carries the full version.
- The code replaced the 1.x implementation in `github.com/endotermic/Dark-Colony-Server` (local clone
  `C:\Users\nika\Documents\Dark-Colony-Server`, commit "Version 2.0: rewrite as a fake-host lockstep
  relay"): `app.js`, `server.js`, `protocol_commands.js` removed; `src/`, `test/`, `tools/`,
  `Dockerfile`, `.dockerignore`, `package.json`, `eslint.config.js` (ESM) and `README.md` replaced;
  `fly.toml` keeps the app name `dark-colony-server` and region `iad` and adds port 8889; `LICENSE`
  (AGPL-3.0) kept; the Visual Studio project files (`.esproj`, `.sln`) were removed as no longer
  relevant. The README's methodology section was rewritten to state truthfully that 2.0 is based on
  a study of the executable's network code, not only on captured traffic.
- This plan and `DC16_NETWORK_PROTOCOL.md` moved into this repository's `docs/` the same day, and the
  live-test logs into `logs/` (gitignored). The Dark-Colony repository keeps the game files, the
  disassembly and the Ghidra project; its `dc-relay/` folder (the pre-move copy of the code) was
  deleted after the push on 7 Sep 2026. From then on all implementation work happens in this
  repository.

**7 Sep 2026, pushed and deployed**

- `main` pushed to `github.com/endotermic/Dark-Colony-Server` (commits `dae1320`, `45ac826`).
- `fly deploy` from this repository replaced the 1.x machine `d8927e5c5ee3d8` in `iad` (rolling update,
  image about 53 MB, Node 22); the app keeps its dedicated IPv4 `213.188.222.154` (allocated Oct 2025)
  and hostname `dark-colony-server.fly.dev`. Production runs the defaults: `MIN_PLAYERS=2`,
  `FAKE_PLAYERS=1`, `LOG_LEVEL=info`, strict sequence check, standard timeouts.
- Verified from outside: the server logged `listening` with version 2.0.0, and a scripted client
  (`node tools/fakeclient.js --host dark-colony-server.fly.dev --ready-after -1`) joined slot 3 and
  received `"Mercenary: Welcome, Player3. Server 2.0, map Armageddon at 200%. Press READY to start."`.
  Real-game test over the internet still to be done by players.

**7 Sep 2026, multi-room: eight rooms and the room-selection lobby (version 2.1, scripted clients only)**

- Design discussion. Three ways to give players several rooms were weighed: (a) a pool of rooms with
  automatic placement (no choice, only removes "battle in progress, try again later"); (b) chat
  commands that move a player between live rooms (needs the colour-lock release order of F20 and a
  live test); (c) the game's dormant meta-lobby as a native room browser (F32, never exercised). The
  maintainer proposed a fourth: **the lobby screen itself is the room browser**. Every row is a room,
  the row's name field carries the room description, chat commands select, READY joins, and the
  room's own dump then rewrites the rows to the real players. Refinements agreed the same day: all
  eight rows are rooms (the client's own row and Mercenary's row included; only names are
  manipulated), every room has its own map, texts longer than the 16-character field scroll
  ("marquee"), and **name, race, colour and team cannot be changed in the hall**. The design is §17.
- Facts established for it: the lobby screen layout (F29), the fixed slot number and the
  dump-without-`'d'` join path (F30), the map names inside the `.SCN` files (F31), the meta-lobby
  screen (F32).
- Implementation: `src/hall.js` (hall), `src/rooms.js` (RoomPool), `src/maps.js` (56 maps),
  `Room.adopt()` (seat a client that already has its slot, no `'d'`), `Client.wire()`/`owner`
  (socket events follow the connection from hall to room), `childLogger` (every room log line
  carries `room: n`), config `ROOMS`/`HALL`/`MARQUEE_MS` (the 2.0 `MAP_*` variables are gone),
  version 2.1.0, greeting `"Welcome, <name>. Server 2.1, room <n>: <map> at 200%. Press READY to
  start."`. Per-room capacity follows the map's player digit (F22): seats = players − fakes,
  `MIN_PLAYERS` is capped per room, `FILL_EMPTY_WITH_AI` acts only on 8-player maps, `FAKE_PLAYERS`
  must leave a seat on every map.
- Tests: 60 (48 before): `test/hall.test.js` covers the marquee, the hall dump, dropped edits,
  `/N` + READY into a room with the slot unchanged and no second `'d'`, scrolling, rooms in battle,
  slot conflicts, deadlines, hall chat and commands, a 4-player room; the integration test drives two
  scripted clients through the hall into room 2 and a lockstep battle there; the older end-to-end
  tests run with `HALL=false`. `tools/fakeclient.js --room N` types `/N` and presses READY twice.
- Smoke test on this PC (`logs/2026-09-07-hall-smoke.log`, outside git): two scripted clients
  connected (slots 7 and 2), saw the hall title, typed `/2`, landed in room 2 (Black Widow) with
  their slots unchanged, both READY, countdown, MREADY, a battle with 68 identical sync frames each.
  Not yet tried with the real game; the items to look at are in §15 and §17.7.

**7 Sep 2026, the hall on a real client: works; three corrections**

- The maintainer connected Classic `dc16.exe` to the local 2.1 server twice (log
  `logs/2026-09-07-hall-live.log`, outside git): the hall appeared with the scrolling room rows,
  READY took the first session into room 1 (Armageddon) and the second, after `/7`, into room 7
  (Hoops of Fury); both played a battle. Verdict: "it works". No flicker complaint.
- **Scroll speed**: 300 ms per character was too slow; `MARQUEE_MS` default is now 200 (+50 %).
- **The own row**: it never showed the hall's room text, and typing into it changed it on screen
  although the server dropped every `'g'` (the log shows the client's keystrokes `Player` →
  `Playsdsds` → `fdsf`). The disassembly explains it (F33): the client's `'g'` handler stores the
  name but repaints the row only for other players, and the own field is a local edit control. The
  server cannot own that row. Design change: the own row shows the player's name, name changes in
  the hall are accepted and echoed (as in a room) and follow the player into the room; race,
  colour and team stay blocked. Rooms occupy the other seven rows in order, skipping the own row,
  so `MAX_ROOMS = 7` and the default list lost Big Crater. The hall title says `/1../<n>`.
- **Fake players' names**: the maintainer saw only Mercenary in the room. That instance ran with
  `FAKE_PLAYERS=1` (the log's `fakeSlots:[0]`), so no other fake existed; the scripted check with
  `FAKE_PLAYERS=7` shows all seven names in the room dump. Found while checking: with seven fakes
  each room had a single free slot and the hall client's fixed slot matched it in about one room in
  seven, the rest said `slot taken`. Fix: a fake sitting in the joiner's slot is moved to a free
  slot when the client arrives (`Room.relocateFake`, §17.5); fakes have no client, so nothing else
  notices, and the dump that follows repaints every row for everybody. The local server was
  restarted with `FAKE_PLAYERS=7 MIN_PLAYERS=1 LOG_LEVEL=debug` for the next look.
- Tests: 61, all passing; the hall tests now cover the own-row name, the seven-row mapping and the
  seven-fakes case.

**7 Sep 2026, second real-client test of the hall: four more corrections**

- The maintainer's report: "8 rooms needed"; the room size read `(0/1)` with seven fakes but must
  read `(0/7)`; the chat should be cleared and a new greeting shown when entering a room; the scroll
  was fast right after entering the hall and then dropped to a slow roll.
- **Slow roll**: the server sent seven one-command frames per step (35 frames/s at 200 ms), measured
  steady with a raw client. The disassembly shows why the client slowed down (F34): its lobby loop
  reads one frame per iteration and its dispatcher runs all commands of a frame. The frames queued up
  on the client; the visible speed was the client's iteration rate divided by seven, and `MARQUEE_MS`
  had no visible effect. Fix: every update is one frame with all its commands (`packPayloads`,
  `PACK_LOBBY_FRAMES`), for the marquee step (one frame of about 150 bytes per 200 ms), the hall dump
  and the room dump (one frame of about 450 bytes instead of about 65 frames). The dispatcher loop
  and the original server's verbatim relay of packed client frames (F23) are the evidence that
  multi-command lobby frames are safe.
- **Eight rooms**: the own row is out of reach (F33), so the selected room now lives in the **map
  line** (`'i'` title, 42 free characters, F15): `>3 Circle of Friends (0/7) open`, re-sent whenever
  the selection or that room's state changes; the seven other rooms fill the rows that are not the
  player's own, in order. `MAX_ROOMS` is 8 again and Big Crater is back in the default list. The
  hall's instruction text moved to the chat greeting; scripted clients recognise the hall by the
  leading `>` of the title (or an empty title, since 12 Sep 2026).
- **Room size**: the rows and the map line show `players / (map players − 1)`, i.e. the map's slots
  without Mercenary; fakes are idle bases, not participants. Whether a seat is really free is still
  decided by the real seat count (`full`).
- **Chat and greeting on entering a room**: the room dump for a client coming from the hall is
  preceded by ten blank chat lines, which scroll the hall's chat out of the 10-line window (F26), and
  followed by two new lines: `"Welcome, <name>, to room <n>: <map> (<terrain>, <k>-player map).
  Server 2.1."` and `"Here: <names>. Press READY when you want to fight; the battle starts at 200%
  when everybody is ready."` (or `"You are the first one here."`). Direct joins get the same two
  lines without the blank ones. All of it travels in the one packed frame with the dump.
- Tests: 62, all passing (`packPayloads`, the map-line title, the seven-row mapping without the
  selected room, the chat clear and greeting, `(0/7)` with seven fakes). The local server was
  restarted with `FAKE_PLAYERS=7 MIN_PLAYERS=1 LOG_LEVEL=debug`.
- Later the same evening: a rule "debug mode keeps only Mercenary as a fake" was added and, on the
  maintainer's request, **reverted** within the hour. Instead the defaults changed: `FAKE_PLAYERS=1`
  (unchanged) and `MIN_PLAYERS=1` (was 2), so a lone player can start a battle without extra
  settings. Local server restarted with `LOG_LEVEL=debug` only.

**7 Sep 2026, third real-client test of the hall: packed frames and the map line work; three tweaks**

- The maintainer tested the packed frames, the map-line room display, the new greeting and the
  cleared chat; no complaint about any of them. Three tweaks asked for and done the same evening:
  (1) the client's player count was still off after entering a room, so the room dump is now
  preceded by `DISCONNECT(q)` for every slot except the client's own and Mercenary's (§17.5);
  (2) the hall greeting and `/help` say that a room number typed in chat must be sent with ENTER;
  (3) the room texts are padded to a common length so that all rows scroll with the same period and
  wrap together (§17.2). Tests: 63, all passing.
- Fourth round the same evening: with the selected room moved into the map line, room 1 disappeared
  from the rows and the maintainer reported it "lost". Decision: **seven rooms, rows 1..7 stay in
  place**, the number and the space after it never scroll, only the 14 characters behind them do;
  the map line keeps repeating the selected room. `MAX_ROOMS` is 7 again (default list without Big
  Crater). Tests: 63, all passing.
- Fifth round: the default room 1 (the preselected one) is now the jungle map Plink - O, with
  Armageddon as room 2, and every room text names its terrain (`1 Plink - O jungle (0/7) open` in the
  rows, the map line and `/rooms`; `Room 3 (Black Widow, desert, 0/7) selected` in chat). Tests: 63.
- Sixth round: **no name in front of relay lines** and **the greetings pinned at the top of the chat**
  (§17.8). The chat handler was read (F35): a ten-line log, wrap at column 40, oldest line dropped
  first. `src/chat.js` paints the window (header + recent lines, ten lines per event); the blank-line
  chat clear is gone since a repaint replaces the window. Greetings rewritten as short header lines
  (hall: welcome/version/room count, `/1../7 + ENTER`, READY; room: room/map/terrain/players,
  welcome/version/speed, READY, battle start). Tests: 65, all passing.
- Seventh round: **no player name in the greetings** (`Welcome. Server 2.1, 7 rooms.` / `Welcome.
  Server 2.1 at 200%.`): `Player<n>` is generated and means nothing (maintainer). Names still appear
  in event lines (`<name> is ready`, `<name> left the lobby`) and in the rows.
- Eighth round: the room header starts with the welcome line (`Welcome. Server 2.1.`), the room line
  comes second, and the speed is no longer mentioned.
- Ninth round: the room header is the single line `Room <n>: <map>, <terrain>, <k> players.`, and
  the hall header opens with `Welcome to Dark Colony server 2.1.`.
- Tenth round (live): under a flood of comments the three instruction lines below the hall header
  scrolled away while the header stayed. Fix: the whole six-line hall greeting is the static header,
  and its last line (`Room <n> (<map>) is selected.`) is rewritten in place on selection instead of a
  "selected" message; a blocked selection still gets a `Room N: <reason>.` message. Four rows remain
  for messages in the hall; `/help` was shortened to three lines that fit there.
- Eleventh round: the maintainer asked to un-ready the client when it enters a room, since its READY
  button stayed pressed. The disassembly shows that no lobby message can do that (F36): the button is
  a checkbox that only the click or a client-side colour-lock refusal changes, while `'h'` only drives
  the small row checkbox. All five logged room entries confirm it (first press inside the room sent
  status 1). Chosen fix: a client from the hall is seated **ready**, so the button state is true, with
  the `<name> is ready (k/n)` line and the start check right away (§17.5). Tests: 65, all passing.

**8 Sep 2026, twelfth round: no automatic start**

- The join-ready seating of the eleventh round was undone on the maintainer's request ("we don't need
  autostart"): a client from the hall is present-not-ready again, and the stale READY button costs one
  extra click (F36). The maintainer also asked that a READY toggle stop and reset the "begin battle"
  countdown; the lobby already did that (`checkStart` cancels when not everyone is ready, a new READY
  starts a fresh countdown), now pinned by a test. Tests: 66, all passing.

**8 Sep 2026, version 2.1 committed, pushed and deployed**

- Commit a411023 "Version 2.1: seven rooms and a room-selection lobby" on main, pushed to
  github.com/endotermic/Dark-Colony-Server (the logs folder was added to .dockerignore first).
- fly deploy replaced the machine d8927e5c5ee3d8 in iad in place (image 53 MB, rolling update, health
  good). The live server logged listening with version 2.1.0, hall on, seven rooms starting with
  J8PLAY01 Plink - O; a scripted client from this PC got slot 5, the map line
  >1 Plink - O jungle (0/7) open, the scrolling row for room 1 and the header line
  Welcome to Dark Colony server 2.1. Production runs the defaults (MIN_PLAYERS=1, FAKE_PLAYERS=1,
  MARQUEE_MS=200, LOG_LEVEL=info). Real-game test over the internet by players still to come.

**8 Sep 2026, smoke test of the live server with scripted clients (`tools/smoketest.js`, §13.7)**

- Purpose: let the maintainer check with the real game, against production, that the hall counts
  players per room, marks a room that cannot be joined, refuses the join, and that a battle with
  other players starts. Ten scripted clients were seated one after the other through the hall
  (`/N` + READY, ~0.3 s each over the internet): seven in room 1 with the `hold` policy (never READY),
  three in room 2 with the `follow` policy (READY exactly when a real player in the room is ready,
  released when that player releases it), plus an observer in the hall.
- Observed on `dark-colony-server.fly.dev` at 13:00 local time: the seven bots got the distinct slots
  6, 1, 7, 5, 3, 4, 2 in room 1 (`Hall.pickSlot` prefers slots still free in most rooms, so no
  `slot taken` occurred), the three in room 2 got 6, 4, 2; the observer (slot 3) read the map lines
  `1 Plink - O jungle (7/7) full` and `2 Armageddon desert (3/7) open`, rooms 3–7 `(0/7) open`;
  selecting room 1 answered `Room 1: it is full.` and READY on it `Cannot join room 1: it is full.`
  The bots' names typed in the hall (`Smoke1`..`Smoke10`) arrived in the rooms.
- Rehearsed locally beforehand with a scripted "human" in room 2: its READY made the three bots
  ready (`Nika is ready (1/4)` .. `Smoke3 is ready (4/4)`, `all players ready, starting in 3 s`),
  releasing READY gave `start cancelled: not everyone is ready` and the bots released too, READY
  again started the battle (4 players RUNNING, 120 sync frames in 4 s), and when the human quit the
  bots left as well, the room reset and three fresh bots re-entered it within 2 s. So a room of
  scripted clients can never start without the person, and a finished round leaves the room ready
  for the next one.
- The scripted client (`tools/fakeclient.js`) got the READY policies `auto`/`hold`/`follow`,
  `announceName`, and the events `title`, `lobby`, `chatWindow`; one integration test pins the
  policies (67 tests, all passing). Server code unchanged. Manual results with `dc16.exe` to be
  recorded here.

---

**10 Sep 2026, game speed 150 % (maintainer decision)**

- `TICK_MS` default `33` → `44` ms (150 %), matching the new single-player default of both patched
  exes (`tools/patch_speed.py`, display doc §10.14). The game's options slider shows the tick as
  `6600 / ms` rounded down to tens, so 44 ms reads 150 %. R11, F11, F17 and §13 updated; the tick
  unit tests keep stepping in 33 ms (`test/helpers.js` pins `TICK_MS: 33`), the end-to-end test
  asserts the new default. Not yet deployed to Fly at the time of writing.

**11 Sep 2026, scenario files documented and converted to JSON (F37)**

- New investigation, no live test: the loaders of `dc16.exe` for `.MAP` (`mapit.c` `0x4530C0`), `.MTG`,
  `.PTH` (`path.c` `0x442D8C`), `.SCN` (`mobiles.c` `0x41BAF0`) and `.TRO` (`renat.c` `0x43FD1C`) were
  read and every shipped file of both games checked against them (`docs/DC16_MAP_FILES.md`). Findings
  that matter for the server: the `.MAP`/`.MTG` rows are stored in the opposite order of the game's
  `z` axis while `.PTH`, `.SCN`, `.TRO`, `.POP` use `z` directly (verified with the vent tiles and the
  blocking bit); attribute bit 9 of a `.MAP` cell is blocking terrain and always path family 0; the
  `.POP` is never read by the game (the editor compiles it into `.TRO` vent triggers); the overview
  caches are two 96×84 planes the game rewrites when missing; the object list's 4th column is the
  vent rate for type 40, the player otherwise.
- `tools/map2json.js` (Node, library + CLI) converts a scenario or a folder; `maps/` has the **seven
  maps of the default `ROOMS`** (`--rooms`, 3.6 MB, grids one row per line, the routing matrix as
  base64) and `maps/index.json`. The first version converted all 56 multiplayer maps (20.6 MB); the
  maintainer decided the same day to keep only the rooms' maps in the repository and generate any
  other map on request. `test/map2json.test.js`: a hand-built 4×3 scenario, the `maps/` contents
  against `ROOMS`, and checks on Armageddon when the game repository is present. Tests: 76, all
  passing; server code unchanged.

**11 Sep 2026, the battle engine beside the relay (§18; F38–F41)**

- Maintainer's request: run the battle engine on the server and put checksum commands (`0x08`)
  into the sync frames. Design and integration done the same day; the engine itself is a port of
  the simulation core of `dc16.exe` into `src/engine/` (in progress, see §18.5 for the state).
- Facts read from the client's pacing code for it: a sync frame is executed as a whole when
  `game_time + 1 == until` (F38), the checksum of a tick is recorded after that tick under the new
  time and the sender puts that time into `0x08` (F39), the start shuffle seeds the game RNG with 0
  and consumes one `rand()` per shuffled entry (F41). The `0x08` a frame carries must satisfy
  `tick <= until - 1` and `until - 1 - tick < 256` on the receiving client, which the server
  guarantees by sending, in frame k+1, the checksum of the tick `until_k` it has just simulated.
- Server: `SYNC_CHECK=off|shadow|send`, `RECORD_DIR`, `MERCENARY_SLOT` (§11); `src/synccheck.js`
  (engine driver: frames → engine ticks, `0x08` out, `0x08` in → comparison, MREADY → shuffle
  check), `src/recorder.js` (JSON-lines recordings), `src/enginebridge.js` (the engine is loaded
  asynchronously and optional), `tools/replay.js` (offline replay and comparison). The `0x08` sits
  right after the `UNTIL`, counts against the command budget and yields to a full-size command
  group (it goes out one frame later with the newer tick). Any engine error disables the engine for
  the rest of the game; the relay never waits for it. With `MERCENARY_SLOT > 0` slot 0 is never
  given to a real player (`Room.seatableSlots`), the fake host is never relocated, and the
  hall/room DISCONNECT list on entry skips the fake host's slot instead of slot 0.
- Tests: 85 (`test/synccheck.test.js` with a fake engine: checksum placement and tick, command
  timing at `until - 1`, mismatch handling, engine failure, missing map/engine, MREADY shuffle
  check, budget rule, `MERCENARY_SLOT=7`, recording); ESLint clean. Not yet tried with the real
  game; the verification path is §18.4.

**11 Sep 2026, first real-client comparison of the battle engine: 2005 checksums, 0 mismatches**

- Setup (§18.4 stage 1): local server `SYNC_CHECK=shadow RECORD_DIR=logs/replays MERCENARY_SLOT=7
  MIN_PLAYERS=1 LOG_LEVEL=debug`; the maintainer's `dc16.exe` joined room 2 (Armageddon) as slot 2,
  the fake host sat in slot 7, so the client was the lowest network id and sent `0x08` every tick
  (F40). The first attempt found no server: the start command with a trailing `&` had not survived
  the maintainer's shell (nothing on 8888, no log); started from here instead.
- Result (log `logs/2026-09-11-engine-shadow.log`, recording
  `logs/replays/2026-09-11T11-32-52-621Z-room2-D8PLAY01.jsonl`, both outside git): MREADY reported
  game player 1 for slot 2, the engine's shuffle agreed (F41 holds). Over 1452 ticks with a build
  order, selections, waypoint/assault orders and wildlife wandering the engine's checksum matched the
  client's on every one of 1442 compared ticks. At tick 1452 the engine threw and switched itself
  off: `fireWeapon` passed the object index where `Anim.hotspots` expects a slot address (an
  integration mismatch, not a simulation error), so the muzzle-hotspot lookup hit a NULL set the
  first time a unit fired. Fixed (`Anim.hotspotsOfSet(fireAnim, facing)`); the offline replay of the
  recording then ran all 2016 ticks: **2005 client checksums compared, 0 mismatches**, i.e. the port
  reproduced the game's state bit-exactly for a 90-second game with movement, production and combat.
- Consequence: §18.5 status is "bit-exact on the first recorded game"; the remaining gaps are longer
  games with heavier combat, buildings and upgrades, several players, and the unported AI. `send`
  mode stays a maintainer decision until a few more recordings replay clean.

**11 Sep 2026, second real-client game: 8.5 minutes with fighting, 11512 checksums, 0 mismatches after one fix**

- Same setup, longer game (log and `logs/replays/2026-09-11T11-39-44-629Z-room2-D8PLAY01.jsonl`,
  outside git): a full base build-up — every building and every unit type of the race built
  (maintainer's account), i.e. the construction drop pods and production from every building class
  — six research purchases, four upgrades (`0x0C`), assault moves, fights with the wildlife, a
  cyborg napalm strike. Live: 9714 ticks matched, then `client − engine = 1`
  for six ticks and divergence from tick 9721 on.
- Diagnosis from the recording alone: the only per-tick changes at 9715 were creature moves plus a
  cyborg leaving its special-attack state (0x12) without firing; a constant +1 in the plain sum is
  one extra missile (`gs+0x7D44`), and six ticks later napalm lands. The port gated the special on
  "research bytes" `0x510188/0x510A48` that nothing writes; those addresses are
  `object_types[4]+0x30` / `object_types[12]+0x30`, the cyborg's and psy-raider's **weapon upgrade
  level** (command `0x0C`, level 2), which the player had bought at tick 8544. Fixed in `combat.js`;
  the replay then matches all **11512 client checksums with 0 mismatches**, the first game too.
- Both recordings are now the regression set of the engine: `node tools/replay.js <file>` must
  print 0 mismatches after any engine change.

**11 Sep 2026, first `send`-mode games: the client accepts 8952 server checksums, then dies at a commander rally**

- Stage 3 of §18.4 locally: `SYNC_CHECK=send RECORD_DIR=logs/replays MIN_PLAYERS=1`, fake host back
  in slot 0. First attempt: the whole server process crashed at the first frame with a checksum
  above 32767 (57372): `build.sync` wrote it with a signed 16-bit write. Fixed: the 16-bit encoder
  wraps to the bit pattern, and `SyncCheck.syncCommand` is guarded so that an exception there
  disables the engine instead of killing the relay; a test pins both.
- Second attempt (`logs/replays/2026-09-11T12-16-15-643Z-room2-D8PLAY01.jsonl`): 6.5 minutes,
  **8952 server checksums sent and accepted by the client** (every one is verified by the game
  against its own history, so this is the strongest confirmation so far). Then the maintainer
  pressed the commander's morale star with units next to him; the client stopped echoing about ten
  ticks after the rally tick and, after the 5-second echo deadline, was evicted and saw "connection
  lost" (the game's assertion box is not shown; `error.log` is truncated at every start).
- Diagnosis from the engine's own trace: the rally itself matched the disassembly, but the ring walk
  shared by rally (`0x417400`), stealing search (`0x417BDC`) and abduction (`0x4171EC`) runs the side
  offset `k` from `−2·MAX` to `+2·MAX` with the **constant** maximum ring, not `−2r..2r`; the port
  visited fewer cells, linked other units and consumed `rand()` a different number of times, which
  the wildlife's wandering turned into a checksum difference within a few ticks. Fixed in
  `combat.js` (battle-engine doc §20). The three shadow recordings still replay with 0 mismatches,
  and a shadow-mode repro of a real rally (`logs/replays/2026-09-11T12-44-38-906Z-room2-D8PLAY01.jsonl`:
  charge 245, two troopers linked at tick 505, 200 more ticks played) matches all **704 client
  checksums with 0 mismatches**. Back to `send` mode for the next games.
- An earlier, earlier star press in the same game (ticks 4959–5066) had no effect on either side:
  the commander was asleep with too little charge and the order was refused.

**11 Sep 2026, `send` mode with two real clients on the LAN: a full game, no problems**

- `SYNC_CHECK=send RECORD_DIR=logs/replays MIN_PLAYERS=1`, fake host in slot 0, two PCs
  (`192.168.8.49` slot 5, `192.168.8.37` slot 7) in room 2 (Armageddon). The engine sent **2775
  checksums, one per sync frame, and both clients accepted every one** (each client checks each
  `0x08` against its own history and aborts on a difference). The maintainer's verdict: "no
  problems". When the first player quit at tick 2788 the `DISCONNECT` handed that base to the AI
  and the engine switched itself off for the rest of the game as designed (`ai.js` is not ported);
  the second player played on as a plain relay game. Recording
  `logs/replays/2026-09-11T12-58-17-957Z-room2-D8PLAY01.jsonl` (outside git).
- Committed the same day (`SYNC_CHECK` default still `off`; enabling it in production is one
  environment variable on Fly).

**11 Sep 2026, pushed and deployed: the live server sends Mercenary's checksums**

- Commits `4f510ad` (the engine, its data, the server integration, recorder, replay tool, docs) and
  `8d3c8e2` (`SYNC_CHECK = "send"` in `fly.toml`) pushed to `github.com/endotermic/Dark-Colony-Server`
  (the first push attempt hung on a credential prompt for minutes; killed and retried with
  `GIT_TERMINAL_PROMPT=0`). `fly deploy --remote-only` rebuilt the image (54 MB) and updated machine
  `d8927e5c5ee3d8` in place; the live start-up line at 13:13 UTC reads `"syncCheck":"send",
  "mercenarySlot":0`, version still 2.1.0. Recording is off in production (no persistent disk);
  `fly logs` shows `engine started` per battle and the `stats` line carries `engine.sentTicks`.
- Maintainer decision (11 Sep 2026): Mercenary is the only party that sends `0x08`; client
  checksums are never relayed (they only exist in diagnostic `MERCENARY_SLOT>0` sessions and are
  compared, not forwarded).

**12 Sep 2026, no preselected room (maintainer decision)**

- The hall no longer preselects a room. Until now a newcomer had the lowest-numbered joinable room
  selected (else room 1), so READY without typing anything joined that room. `Client.selected` now
  starts at −1: the map line is **empty** (`'i' "", ""`, F42), the sixth header line is `No room
  selected. Type /1../7 + ENTER.` until the first `/N` turns it into `Room N (<map>) is selected.`,
  and a READY before a selection answers `Select a room first: /1../7 + ENTER.` and leaves the
  client in the hall. The first version of the day put a prompt `>Type /1../7 + ENTER to select a
  room` into the map line; the maintainer's live test (Classic `dc16.exe` against a local server)
  asked for the field to be empty instead, because the game then greys out its READY button by
  itself (the lobby refresh disables control 133 while the scenario file name is empty, F42), which
  is the stock appearance before a host picks a map. Empty file and title cause no un-ready storm:
  the F15 player-count check is skipped for an empty title. Scripted clients recognise the hall by
  an empty title or the leading `>`. `/N`, `/rooms`, `/help`, the rows and the icons are
  unchanged. `tools/fakeclient.js` without `--room` stays in the hall instead of pressing READY on
  the server's choice; the smoke test's observer never pressed READY by itself and parses only map
  lines that start with a room number, so it is unaffected. §11 (`ROOMS`), §17.1–17.4, §17.6,
  §17.8, R13, README and CHANGELOG updated; one new test (READY refused without a selection), six
  adjusted, 193 in all. The prompt variant was seen on a real client on 12 Sep 2026 (and rejected, see above); the empty-map-line variant awaits its live test.

**12 Sep 2026, AI Mercenary (maintainer decision)**

- The fake host is called **AI Mercenary** (`MERCENARY_NAME` default; `FAKE_NAMES` starts with it
  too so that the second fake is still Renegade), and the room greeting (§17.8) has a second header
  line from it: `AI Mercenary: Hi! I am an AI bot and the host of this game. My base stays idle.`,
  wrapped at 40 columns under the room line. Asked for after the live test of the empty map line
  (which the maintainer confirmed the same day): a newcomer should be told what the player in slot 0
  is. It is the one relay line with a name in front; the rule of 7 Sep 2026 (§17.8) holds for every
  other line. The hall has no such line (no Mercenary there). The name is 12 characters, under the
  16 of the name field (F33). Tests adjusted (lobby header, fake names, recordings); README,
  CHANGELOG, §11 updated. Confirmed on a real client the same day (local server, Classic `dc16.exe`).

**12 Sep 2026, release 2.2**

- `package.json` 2.1.0 → 2.2.0, tag `v2.2.0`; CHANGELOG's Unreleased section became the 2.2 entry
  (battle engine + checksums, no preselected room, AI Mercenary, 150 % speed, map files to JSON,
  smoke test). The lobby greeting reads `Welcome to Dark Colony server 2.2.` (`VERSION_SHORT` from
  `package.json`; the hall tests now derive that string instead of pinning `2.1`). Deployed to Fly.

**12 Sep 2026, alive bots: the game's AI documented, server bots planned (§19, F43–F46)**

- Maintainer's request: "make our server bots alive" — the fake players should play. No code yet.
- The computer player of `dc16.exe` ("Krusty", `ai.c` + six `krusty_*.c` files) was read instruction
  by instruction from `dc16.asm` and written up in `docs/DC16_AI.md`: framework and think schedule,
  the local-command path that executes AI commands on every machine, the full 0x6C40-byte state
  layout (zone table, memory of seen objects, four major tasks × sixteen groups, 32 goals, tunables),
  influence map formula, census (workers → task 0, tower builders → defend, scouts → scouting, the
  rest 25 % defend / 75 % attack), the 18 production goals, the four tasks (workers to vents, defend,
  attack with its deterministic target scoring, flyer scouting/bombing), the group mover, every
  `rand()` site, the `aimsg` trigger ids, DISCONNECT and save/load. Several statements of
  `DC16_BATTLE_ENGINE.md` §17 turned out wrong (task roles swapped, state offsets, "demand" array,
  RNG constant); §17 now points to the new document and lists the corrections.
- Design decision recorded in §19: bots are Krusty instances run by the server against the engine's
  state for the fake human players; their decisions become ordinary commands in the next sync frame
  (F43), money is kept on the engine's player block (F44), the think phase follows the original
  (F45), a private RNG replaces the game RNG, vision is the fake player's own (F46). The same module
  is to be promoted later to a bit-exact `src/engine/ai.js` so that the engine survives a
  `DISCONNECT` takeover and AI-typed lobby slots (§19.6). Steps, configuration and risks in §19.

**13 Sep 2026, AI Mercenary plays: a rusher and the 1000-money alliance (§19.8, F47, F48)**

- Maintainer's request: the relay's Mercenary AI "must have rushing character", must ally with a
  client (including visibility) for 2 minutes when the client sends it 1000 money, must return money
  sent while somebody is already allied, must spell every decision aloud in the battlefield chat, and
  must announce the offer at the start of the battle.
- The money gift: the in-game diplomacy screen's "give 1000" button sends `0x0F(target)` after
  deducting 1000 locally (`0x4334DD`-`0x433569`, F47). The relay had struck `0x0F` as a cheat since
  version 2.0 (R10/F13 said it had no sender), which also made gifts between humans vanish while the
  giver lost the money. `0x0F` is now queued like an order; `player > 7` strikes.
- Alliance and shared vision are per-direction bits and take effect only when both sides set them
  (F48). The Mercenary sets its side (`0x0D` twice) and asks the ally in chat to set theirs.
- Implementation: `src/rusher.js` (strategy against the engine state), `src/mercenary.js` (deal,
  chat, timers, wiring), settings `MERCENARY_AI` (`rusher`/`off`), `MERCENARY_ALLY_S` (120),
  `MERCENARY_THINK_TICKS` (32); new command builders in `commands.js`; the lobby greeting announces
  the deal when the Mercenary can play (`SYNC_CHECK` not off). 15 new tests (`test/mercenary.test.js`
  with a fake engine, `test/rusher.test.js` in headless self-play on Armageddon: worker -> vent ->
  barracks -> infantry -> a four-unit wave at the enemy HQ within 6000 ticks, allies never attacked,
  recall when the target becomes an ally, no `rand()` and no history writes by a think, and one end-to-end run of the real engine inside a room); 208 tests.
- Two engine bugs surfaced (§19.8): the DEPEND loader's field names did not match depend.c's readers
  (every dependency check answered "unavailable"), and `objectDie` called `depRecompute` without the
  player. Both fixed; neither touches a checksum.
- Same day, third request: victory by allying with all remaining bots, and a leaving client's base
  becoming a bot. Investigation (F49): the client ends a multiplayer battle when all alive players
  are mutually allied (`0x40E260`; alive = any object but mines/towers, `0x40E1D4`), Victory for the
  alive ones. Implemented (§19.9): the deal sets alliance and vision in both directions, so buying
  every remaining bot wins; a client leaving a running (or loading) battle is taken over by a new
  bot instead of `DISCONNECT` when the engine plays (money normalised `money -= spent`); a bot's
  `isAlly` honours the game's own mutual alliances. 211 tests.
- Same day, second request: "a permanent second rusher AI, so single client can play against, ally
  or combine tactics". `FAKE_PLAYERS` now defaults to 2, the second fake is **AI Marauder**, and every
  fake is a bot with its own rusher and its own alliance for sale (`Bots`/`AiPlayer` in
  `src/mercenary.js`, `Room.bots`). Hall room sizes are shown without the fakes ("(0/6)"); tests of
  the slot mechanics pin `FAKE_PLAYERS=1`. 209 tests.
- **First live test of the bots, 13 Sep 2026** (local relay, `SYNC_CHECK=send RECORD_DIR=logs/replays
  MERCENARY_AI=rusher`, one Classic client in room 1 Plink - O, 11 504 ticks, recording
  `2026-09-13T12-11-09-941Z-room1-J8PLAY01.jsonl`): both rushers built and attacked (359 thinks each,
  ~50 waves, 59 troops), 5 deals were bought and every one expired or ended correctly, no engine
  assert, no stall. **Bug: allying with both bots did not end the battle.** The player (game player
  6) held both alliances during ticks 8174..9157 and again from 11046 until leaving. Replaying the
  recording through the engine (`scratch/probe_alliance.mjs`) shows matrix 0 rows 1 = {1,6}, 6 =
  {1,6,7}, 7 = {6,7}: the human is mutually allied with both bots but the bots are not allied with
  each other, and `game_over 0x40E260` compares every alive player with the *first* alive one (game
  player 1 = AI Mercenary), which is not allied with 7 = AI Marauder. F49 and §19.9 corrected; fixed
  the same day: bots sharing a paying ally ally with each other while both deals hold (pacts,
  `Bots.syncPacts`), and, on the maintainer's request, a bot's action lines now go to its ally only
  (chat mask). 2 new tests (213). Also seen: the
  client sent no `0x08` because `MERCENARY_SLOT=0` kept the fake host as the lowest id (0 checksums
  compared) — the next live test should run `MERCENARY_SLOT=7` to verify the engine as well.
- **Second live test, 13 Sep 2026** (same setup, `MERCENARY_SLOT=0`, room 1 Plink - O, 11 028
  ticks, recording `2026-09-13T12-56-01-427Z-room1-J8PLAY01.jsonl`): pacts, private action lines
  and the defence in the loop. The client accepted the server's `0x08` checksums for the whole game
  in `send` mode with two rushers' orders and the defence orders in the frames (a mismatch aborts
  the client at once), no engine assert, no stall. Action lines were unheard without an ally and
  went to the payer during the one deal (AI Marauder, ticks 2537..5264); only one bot was bought, so
  the pact did not fire (matrices confirm 6↔7 only). The Mercenary defended six times against the
  Marauder's trickle of single units; **weakness seen**: with nobody at home every alarm recalls the
  entire sent army ("Only 0 at home against 1. Everybody back", then "32 more march" once clear), a
  ping-pong that keeps the whole force walking; fixed right after: only the nearest units on their
  way come back, three per intruder and at least four (`defendPerIntruder`, `defendMin`). The client
  left by quitting (no echo for 5 s, player
  7 alive with 17 objects at the end), so the takeover bot was created and the room reset.
- **Third live test, 13 Sep 2026** (same setup, 12 812 ticks, recording
  `2026-09-13T13-14-04-308Z-room1-J8PLAY01.jsonl`): **victory by alliance confirmed.** The player
  bought AI Marauder at tick 12755 and AI Mercenary at 12800; the pact's four relations went out in
  the same frame as the second deal, the replayed matrices show 1↔6, 1↔7 and 6↔7 all set at tick
  12805 and `game_over` true, and the client closed its connection cleanly half a second later (the
  results screen). Send-mode checksums accepted for the whole game, no assert, no stall. The bounded
  recall worked ("1 at home against 1. 3 come back to defend the base.", 8 defences, no more
  whole-army walks). The two bots fought each other for most of the game (38 "march on AI
  Mercenary's base" lines), which prompted the maintainer's next request: bots allied by default
  (§19.9, implemented right after).
- **Fourth live test, 13 Sep 2026** (same setup, 8 355 ticks, recording
  `2026-09-13T13-32-38-799Z-room1-J8PLAY01.jsonl`): **the standing peace works end to end.** Replayed
  matrices: 1↔7 set from tick 9 (first frame); the player (6) bought AI Marauder at 3036 -> 7↔6 on
  and 1↔7 off in the same frame, the Marauder marched on the Mercenary and the Mercenary defended
  twice; the deal ran out at 5763 -> 7↔6 off, 1↔7 back on; AI Mercenary bought at 6165 -> 1↔6 on,
  1↔7 off; AI Marauder bought again at 8345 while the Mercenary's deal still ran -> 7↔6 on and the
  pact 1↔7 on in the same frame, `game_over` true at 8350, and the client closed cleanly half a
  second later (Victory). Send-mode checksums accepted throughout, no assert, no stall, no bot line
  reached the player without a deal. Nothing left open from the day's requests.

**13 Sep 2026, release 2.3**

- `package.json` 2.2.0 → 2.3.0, tag `v2.3.0`; CHANGELOG's Unreleased section became the 2.3 entry
  (AI Mercenary and AI Marauder play: rush, alliance for sale, pacts, base defence, takeover of
  leavers, victory by alliance; money gifts relayed again; DEPEND loader fixed; `DC16_AI.md`; the
  two-monitor start-up fix of the game exes, `tools/patch_ddraw_lost.py`, display doc §10.16). The
  lobby greeting reads `Welcome to Dark Colony server 2.3.` (`VERSION_SHORT`). Deployed to Fly.

**18 Sep 2026, player reports: "sync error" kicked everybody; aliens "cannot build" some things**

- Reports reached the maintainer second-hand (no log, no screenshot): a battle ended for every player
  with the game's own "sync error" abort, and players of the Gray race saw odd limits on what they
  could build. Investigation of the same day, nothing changed on the server yet:
- **Fly kept no evidence of the battle.** `fly logs` shows only the last ~100 lines (18 Sep: all of
  them hall joins by port scanners, `bad frame: invalid frame length 1351/1869/3907`, and Fly proxy
  EOFs); the Logs API does hold about seven days of the app's log (F50, found later the same day),
  but the machine `d8927e5c5ee3d8` (image of 15 Sep 2026, v182) has no volume and `RECORD_DIR` was
  unset, so no recording of any live battle exists. The incident itself cannot be reconstructed.
- **Mechanism of the kick (F14, F39, §18):** `fly.toml` runs `SYNC_CHECK=send`: every sync frame
  carries the server engine's `0x08 (checksum, tick)`, each client compares it with its own history
  and aborts on the first difference. With the fake host in slot 0 no client sends `0x08`, so the
  server cannot notice that its engine diverged (`SyncCheck.onClientSync` never runs) and keeps
  sending wrong checksums until every client is gone. A single divergence anywhere in the port ends
  the battle for all players; the relay itself is unaffected.
- **The engine was never verified with the Gray race.** All eleven recordings in `logs/replays/`
  (11 and 13 Sep 2026) have every slot at race 0; the "every building and every unit type" game of
  11 Sep was Human only; the engine tests mention race 1 three times (scenario loading only). Alien
  buildings (`unitdef` rows 2/3, pod type `0x5D`, alien build animations), alien units and their
  specials (abduction/stealing/cloak, night rule in `missile.js`) are ported but unconfirmed.
  Checked again today against `dc16.asm`: the `0x09` handler `0x41CAA4` (type lookup by `level`
  without race, refund with race 0) and `build_slot` `0x4450F4` (row `race*2+level`, funky tower
  `0x51`->`0x70`) match `engine/commands.js` / `city.js` line by line, so the *building command*
  itself is not the divergence; it must be somewhere in the alien game that follows.
- **No server-side cause for the build limits was found.** The relay forwards `0x09`/`0x0A`/`0x0C`
  untouched (`RELAY_IN_GAME`), the seven room maps have `%Depend -1` in every TEAM block, the
  shared game folder's `GAMESTAT/*.TXT` and `INTRFACE`/`INTRF_HD` scripts are identical to the
  retired `DC - Classic` copies apart from CRLF line endings and the `movies` rename in
  `?SCENE.TXT`, and the lobby `'f'` handler `0x40EE10` applies whatever race comes back. Either the
  players describe the kick that followed their build, or it is the game's own DEPEND rules.
- **Recommended, maintainer's decision:** (1) `SYNC_CHECK=shadow` on Fly (edit `fly.toml`, `fly
  deploy`): the engine keeps running for the bots, no `0x08` is sent, the game's own desync check
  is inert again as before 11 Sep and nobody is kicked; (2) one recorded Gray-race game with
  `MERCENARY_SLOT=7 SYNC_CHECK=shadow RECORD_DIR=logs/replays` (locally, as on 11 Sep) so that
  `tools/replay.js` shows the first mismatching tick; (3) `send` again only when it replays clean.
  Longer term a Fly volume for `RECORD_DIR` (or log shipping), otherwise the next report is as blind
  as this one.
- **Same day, maintainer requirement: recordings must be recoverable from the Fly log**, without
  swamping it. Done as `RECORD_LOG` (§18.6, `src/logrecorder.js`, `tools/logs2replay.js`, F50):
  the file recorder's events become compact `msg: "replay"` log lines - the start header, every
  sync frame as broadcast (UNTIL, the server's `0x08` as 4-hex-character runs, the commands), every
  client `0x08` (runs, first sender per tick), the events (`mready`, `mismatch`, `left`, `end`,
  ...); only the per-tick engine checksum lines are left out (`tools/replay.js` recomputes them, and
  in `send` mode they are the `0x08` of the next frame). A first version stripped the `0x08` and
  sampled the client checksums; the maintainer asked for the full broadcast package, so that a
  battle replays locally exactly - the decoded frames are now byte-identical to the payloads sent.
  Every line carries the recording id `rec` (start time + room), so simultaneous battles in several
  rooms do not mix; the room's own `recording` line names the id. Measured on the twelve local
  recordings (1.5-9.4 min each): 8.8 MB of files become 487 KB of log, 4-11 KB per game-minute (about 10 in `send` mode with two
  bots playing, 7 with a real client's checksums in `shadow` mode), 30-55 lines for an 8-10 minute
  battle, the longest line 3.2 KB; all twelve decode back to byte-identical frames and identical
  client checksums, and the five with client checksums replay to the same verdict with the same
  number of compared ticks. `node tools/logs2replay.js
  --fetch dark-colony-server --since 7d --replay` pages the Logs API (24 h = 602 lines in 8 pages,
  3 s), writes `logs/replays/<start>-room<n>-<map>.jsonl` per battle and replays each. Dropped log
  lines are detected through `seq` and the lost frames are reconstructed empty with a note.
  `fly.toml` sets `RECORD_LOG = "on"`; not deployed yet, `SYNC_CHECK` unchanged (the decision above
  is still open). 220 tests.
- **Same day, third requirement: a recording must play back into a real dc16.exe.** "Save the
  replay to a file, run the local relay server, connect with the real game, and the replay happens
  as one of the clients." Done as **replay mode** (§18.7, `src/replay.js`, `REPLAY_FILE` /
  `REPLAY_SLOT`): the server rebuilds the recorded lobby (the same map, every recorded human as a
  fake with its recorded name, race, colour and team, AI and empty slots as recorded), seats the
  connecting client in a recorded real player's chair with race, colour and team pinned, and in
  battle broadcasts the recorded sync frames byte for byte at the recorded pace instead of building
  its own; the watcher's orders are dropped, its echoes and progress reports pace the stream as
  usual, its `0x08` checksums (when it is the lowest network id) are compared with the recorded
  client checksums and the first divergence is logged; a differing MREADY game player index is
  logged too. The frames carry the original server's `0x08`, so the client itself aborts with "sync
  error" the moment its simulation leaves the recorded one - which is the point: a battle that
  desynced on Fly can be watched locally, and the tick where the real game disagrees with the
  server engine is found by `tools/replay.js` on the same file. Not yet tried with the real game;
  the unit tests cover the lobby, the pinned seat, the byte-exact frames, the pacing of a recorded
  stall, the dropped orders and the checksum comparison (224 tests).
- **Committed and deployed the same day** (Server `9e94196`, pushed; `fly deploy` at 12:46 UTC,
  image `deployment-01M2T8SVT9A62HFSDJAQEZ4KHG`, machine version 184). The rolling update left the
  machine in state **stopped** (event `stopped/update` right after `created/launch`; the old
  process had exited cleanly on SIGINT) and it did not come back by itself; `fly machine start
  d8927e5c5ee3d8` brought it up at 12:47 UTC, `listening` logged, `RECORD_LOG=on` in the
  machine's environment. Check `fly status` after every deploy. `SYNC_CHECK` stays `send`
  (maintainer's call); from now on every battle on Fly leaves a recording in the log.
- **First battle recovered from the Fly log and replayed in the real game, same day.** The
  maintainer played on Fly (room 1, Plink - O, start 13:05 UTC, 9976 frames, 7 min 20 s at 44 ms,
  ended by room reset with 0 mismatches). `node tools/logs2replay.js --fetch dark-colony-server
  --since 3h` fetched 282 log lines in 4 pages and rebuilt the recording (`tools/replay.js` runs
  the engine through all 9985 ticks without an assert; no client checksums, the fake host held
  slot 0). Local `REPLAY_FILE=... REPLAY_FULL_MAP=on node src/index.js`, dc16.exe joined
  127.0.0.1:8888, took slot 4 with the recorded race/colour/team, MREADY game player 7 as recorded,
  the battle played with the whole map visible - and stopped at frame 3947: `no echo for frame
  3947`, the game's `error.log`: `sync error: time 3944, net 14336, me 14360`. The same replay
  **without `REPLAY_FULL_MAP` ran to the end without an error** ("replay finished without errors,
  map cheat is the cause", maintainer). Two results: replay mode works with the real game, from a
  Fly log recording, byte for byte over 9976 frames; and the full-map flag reaches the simulation
  (F28 amended). Meanwhile the viewer's signals were made inert (`e47c478`, above).

**18 Sep 2026, maintainer report: "all Dark Colony executables request the CD and hang on multi hard drive systems"**

- Not a server matter, recorded here like the two-monitor fix: the `cdcheck` bytes only ignored the
  answer of the CD test; the probe itself (`cd_probe` `0x405EAC` / CW `0x405E8C`, `safefunc.c`) still
  opened `D:\dc\anim.dat` — drive letter from `HBNFUFL.A01`/`.A02` — at start-up, at every menu
  screen (`load_interface`) and periodically in game, and the game never calls `SetErrorMode`, so a
  not-ready `D:` (card reader / empty USB or optical drive / sleeping second disk) meant Windows'
  "No Disk" box behind the full-screen surface or a spin-up stall. New `tools/patch_nocd.py`
  (patcher fix `cddrive`, applied after `resolution` because `patch_resolution.py` checks its input
  by MD5). A first two-byte version (probe → `ret`, format string emptied) was rejected the same day
  — "the game should not try to touch the CD path at all" — and became nine edits + two `.reloc`
  entries per exe: the start-up block that opens HBNFUFL and builds the path is jumped over (near
  `jmp`; a short `EB 9F` for the +0x9C hop crashed both exes in the smoke test and was caught before
  commit), the probe call NOPped and the probe itself `ret`, the open helper / wave loader / movie
  opener jump past their CD attempts, the dead format string zeroed, the "insert the CD" box
  replaced by "FILE NOT FOUND / A sound file is missing - see error.log". The patched exes no longer
  read `HBNFUFL.A0x`. Both repository exes re-patched, `Apply-DarkColonyPatches.ps1` regenerated;
  tested on `subst` drives D/E/F/G with the game on G: and `anim.dat` in `D:\dc\` (stock exe writes
  two probe files there in 14 s, patched exes none, and they start without the HBNFUFL files);
  display doc §10.19. A not-ready (no-medium) drive cannot be simulated with `subst`. Then, same
  day, "the patcher should contain only one CD fix": `cdcheck` (the three hand-patched 2025 bytes)
  and `cddrive` merged into **`nocd`**, all produced by `patch_nocd.py`, first in the order;
  `patch_resolution.py` identifies its input by size when the MD5 is not the 2025 one. Exe bytes
  unchanged. HBNFUFL.A0x answer for the originals: the first character is the letter of the drive
  holding the CD (or the mounted CD image), `D:` in the repository.

**19 Sep 2026, the two two-player battles of 18 Sep: one "crash" when a player's last unit died = the game's sync assert; root cause found in the engine's object loop**

- Maintainer report: two battles on Fly on 18 Sep with two real clients (endotermic, Gray, vs
  Delaro, Human; Plink - O, AI Mercenary and AI Marauder playing). The first (15:29 UTC, 9604
  ticks) "crashed when all units of one player were destroyed", the second (15:38 UTC, 16807
  ticks) ended correctly with Defeat/Victory. Both recordings came back from the Fly log with
  `tools/logs2replay.js --fetch` (F50).
- **What the log shows.** Game 1: endotermic's client stopped echoing at frame 9473 and was evicted
  after 5 s, Delaro's connection closed 1.9 s earlier at tick 9515. That is the pattern of the
  game's own **sync assert** (`sync.c` line 125): `MessageBoxA` behind the full-screen surface,
  black screen, the socket stays open until the player presses a key. Game 2 ended cleanly because
  endotermic's last unit died while every survivor was mutually allied, so the battle-over check
  (F49) fired at once.
- **Reproduced with replay mode (§18.7):** the recording played back into the real `dc16new.exe`
  aborted at the same place; `error.log`: `sync error: time 9469, net 13454, me 13430`. Server
  engine and game differ by 24 at tick 9469 = one Trooper step. Endotermic's last unit (a Gray
  worker, #176) was not involved at all: the difference is in the crowd of ~40 allied bot and Delaro
  units jammed on endotermic's destroyed base, where blocked units re-plan ("wander") with two
  `rand()` jitters every few ticks - the game's `rand()` is a 256-entry table walk, so a divergence
  in the *number* of draws stays invisible until a jitter decides a step.
- **Localisation without a second observable.** The checksum of tick 9469 is the only game-side
  value, and about half of all random-index shifts reproduce it. The decisive trick: rewrite the
  recording's `0x08` values from tick 9469 on with a *candidate* engine's history and replay it into
  the real game; the game then runs until the next disagreement and names the tick and its own
  checksum in `error.log`. Shifting the engine's random index by -1 at tick 9448 reproduced the
  game through 9474 and failed at 9475 (`me -14209`, off by 23); every shift at 9470 (+-1, +-2)
  fixed that one too, so the game had one draw fewer than the engine before tick 9469 and the
  difference lay in the *order* of draws around a unit born at tick 9463.
- **Root cause (engine.js, `game_tick` step 13).** The original's object loop re-reads the bound
  every iteration - `cmp esi, [gs+7D40h]` at `0x419EA5` - so an object created *during* the loop
  with a **new highest index** (a unit produced by a building that the loop had already dispatched;
  here `#291`, a Sergeant from Delaro's research pod, tick 9463) runs its first tick immediately:
  its idle body draws the fidget `rand()` in the birth tick. The port cached `MAX_OBJ` before the
  loop and dispatched such a unit one tick later, so its draw fell *after* the draws of the next
  tick's lower-indexed units - same count, different order, and 1/16 of the re-ordered fidget draws
  gain or lose their second draw. Recycled slots (index below the cached bound) were unaffected,
  which is why the 11-13 Sep recordings, the two other battles of 18 Sep and game 2 (16807 ticks,
  two new-maximum births in its last 800 ticks) stayed in sync. Fix: `for (obj = 0; obj <=
  i32(gs, GS.MAX_OBJ); obj++)`. Verification: the fixed engine reproduces all seven game-confirmed
  checksums of game 1 (9469-9475) with no artificial shift and leaves the three in-sync recordings
  byte-identical (0 mismatches over 16807, 9985 and 20287 ticks); regression test in
  `test/engine-game.test.js` (fails on the old loop). **Confirmed live the same day:** the recording with the fixed engine's checksums for ticks
  9469-9604 replayed into the real `dc16new.exe` ran past the old abort to the end of the recording
  (`replay finished: all recorded frames sent`, tick 9604, `error.log` empty). The server on Fly
  ran the old engine in `send` mode until the deploy of 19 Sep 2026 (maintainer, after his own replay
  of the fixed recording: "tested, working as expected - ended and disconnected correctly").
- **Not the cause** (all checked against the asm): the movement loop trap (reset per object in
  both, `0x419EC0` sits inside the loop; fired 230 times in game 2), `reroute`/`relax`/`handle_block`,
  the post-block sleep of 4, `turn`, `state_sleep`, the random side-step, the fidget, the random
  table (byte-identical to `0x488F20`), the day/night vision radius, target acquisition.
- **Replay mode shows too much money** (maintainer, same day): money is local - the sender deducts
  it when it issues a build command, receivers only book "spent" - and the watcher never issued the
  recorded player's commands, so nothing was ever deducted while income accrued. Display only;
  money is not in the checksum. Noted in §18.7.
- Tools of the day, all in the scratch directory, not committed: `logs2replay.js --fetch` for the
  recordings; a Python `SendInput`/`PrintWindow` driver to launch and screenshot the game (mouse
  clicks reach the game, the in-game cursor follows relative motion only; the maintainer joined the
  replays by hand); rewriting `0x08` values in a recording for a candidate engine.

**19 Sep 2026, the game's own AI plays the bots; one bot by default, `/botcount N` for more (§19.10, F52-F54)**

- Maintainer request: "reverse engineered main AI bot which later must be built into server and count
  of bots must be updated by `/botcount`; by default must be only bare minimum one master bot."
- Built: `src/engine/krusty.js` (Krusty, bit-exact structure, DC16_AI.md §5-§15 checked against the
  asm by five parallel readings, §23 of that doc lists the ~30 corrections), `src/engine/ai.js` (the
  `ai.c` schedule, exact mode after `record(t)`), `src/krustybot.js` (bot mode: private RNG, commands
  into the sync frame), `MERCENARY_AI=krusty` default, `FAKE_PLAYERS=1` default, lobby chat commands
  `/botcount N`, `/botcount`, `/help` (`Room.setBotCount`), settings `BOT_SEED`, `AI_SEND`.
- Headless self-play on Armageddon and Plink - O: the standard base (worker, vent, barracks, factory,
  science, second worker), guards at the vents, two attack groups moving, ~30 units by tick 24 000, no
  asserts in 30 000 ticks. The first 6000-tick run bought nothing: the goal chain fires when a check
  returns 0 (`0x457650`), the port had it inverted - fixed before anything else.
- Engine fix on the way (F53): the vent bit is set through the file-order row table and was read
  z-ordered by the exhausted-vent path and by the trigger primitive `m(x, z)`; the first exhausted vent
  asserted in the long self-play. Both sites fixed; the D8PLAY01 recordings of 11 Sep replay unchanged.
- Verification against a real client pending (bot mode: a live game; exact mode: a shadow recording
  with an AI takeover). `send` on Fly keeps stopping on AI takeovers until `AI_SEND=true`.
- 239 tests; the two-bot scenarios of `test/mercenary.test.js` pin `FAKE_PLAYERS: 2`; hall rows show
  `(0/7)` with one bot.
- Later the same day, maintainer: "always apply krusty as a bot AI for relay fakeclients" - the
  `MERCENARY_AI` switch (`off`/`rusher`/`krusty`) and the rusher (`src/rusher.js`, its test) removed;
  the bots are Krusty whenever the engine runs, idle only without it or in replay mode. 233 tests.
- Also the same day: "hiring of the bot must remain for 45 sec" - `MERCENARY_ALLY_S` defaults to 45 (was 120).
- And: "add a lobby option `/bottype` with available variants krusty, rusher and random so there is a
  possibility to apply rusher too" - the rusher is back (`src/rusher.js`, its test), `Room.botType`
  (`BOT_TYPE`, default `krusty`) is set per room with `/bottype krusty|rusher|random`; `random` draws
  per bot at game start (`Room.random`); the header row reads "Bots: 3 random. /botcount N, /bottype T.";
  the greeting says "rush" for rusher rooms.
- Then: "on the battlefield bots must not write its battling actions to chat" - the brains' action
  lines are no longer said (not even to the ally; debug log only); the deal lines stay. And "fix the
  krusty bugs": in **bot mode only** (`ctx.fixes`, `krusty.js` FIXES) the upgrade goals 12/13 buy weapon
  and armour upgrades (`0x0C`, the unit type fielded most, level by level) and `attack_plan` indexes its
  taken-target list and parked-group counter by zone and tests the contested flag of the destination.
  Exact mode keeps the original's behaviour bit for bit. 242 tests. Committed `eb533ca`, pushed,
  deployed to Fly.
- Then: "switch off hiring of bots and make a switch in lobby `/bothire` with on and off" -
  `Room.botHire` (`BOT_HIRE`, default **off**), `/bothire on|off` per room; off = no offer at the
  start ("Hiring is off in this game: the bots are nobody's allies."), a gift to a bot is returned
  with a word, the header row reads "Bots: 1 krusty, hire off. Type /help." (the commands moved to
  `/help` to keep one row). 244 tests.
- Then: "bots must not ally each other by default" - the standing peace of 13 Sep 2026 (§19.9) is
  gone: `Bots.syncPacts` allies two bots only while the same player has hired both; unhired bots and
  inherited bases are rivals of everybody. 244 tests. Committed `ec1dcc7`, deployed.
- Then: "remove AI label from mercenary and marauder; bots must have random race" - `MERCENARY_NAME`
  `Mercenary`, `FAKE_NAMES` `Mercenary,Marauder,...`; `MERCENARY_RACE` defaults to `random`: every
  fake slot draws Human or Gray when it is made (`Room.fakeSlot`), so the bots' races differ from
  game to game and bot to bot. A takeover bot still speaks as `AI <name>` (it is not one of the fakes).
  Committed `c09ce00`, deployed.
- Then: "update the initial greeting in the lobby, mention /botcount, shorten the greeting from the
  mercenary bot" - the room header is now four rows: the room line, "Mercenary: Hi! I am the AI
  host." (plus " 1000 in battle hires me for 45 s." when hiring is on; no bot names, they are the
  player rows anyway), "Bots: 1 krusty, hire off. /botcount N", "/bottype, /bothire, /help for more."
  Without the engine: "Mercenary: Hi! I am the AI host. My base stays idle." 245 tests.

**21 Sep 2026, maintainer report: "when endotermic entered the battlefield his client hanged and was thrown out, the game continued as bots vs the second client" = a client crash at battle start for start positions near the map edge (F55; fix `camera`, `tools/patch_camera.py`, `DC16_DISPLAY_AND_RESOLUTION.md` §10.22)**

- **The log** (`tools/logs2replay.js --fetch dark-colony-server --since 3d --raw ...`): the last game
  with endotermic is 19 Sep 18:26:55 UTC, room 1 (Plink - O), endotermic (slot 6) and Delaro (slot 3)
  plus six Krusty bots (`/botcount 6`, hire toggled on and off in the lobby). Both clients sent
  MREADY (Delaro game player 2, endotermic **game player 0**); at "running" + 0.25 s the stats line
  shows endotermic with `latencyMs null, pendingEchoes 6` while Delaro had echoed frame 0 already
  (247 ms). After `ECHO_TIMEOUT_MS` the watchdog evicted endotermic: `client left ... reason "no echo
  for frame 0"`, the Krusty takeover took the base, Delaro played 25 575 ticks against seven bots
  and the recording (`mu8pynofr1`) replays with 0 mismatches. The frames were fine: frame 0 is the
  usual `TICK_SPEED(44)` + two greeting chat lines, identical in structure to the three other
  Krusty-era games that ran to the end. Nothing server-side differed for endotermic except the
  seat: no human client had ever been game player 0 in any of the 19 recordings before (humans
  were 1, 2, 4, 6, 7).
- **The client side** (endotermic is the maintainer's own PC): the game's `error.log` stayed empty,
  but the Windows Application log has `Application Error` 1000 for `dc16new.exe` at 21:26:57 local
  = 18:26:57 UTC, the very second of "running": exception `0xC0000005`, fault offset `0x45B52` =
  VA `0x445B52`, `test dword ptr [edx],ecx` in the dominant-terrain-class scan of the ambience
  picker (`0x445AA4`, called from `0x432040` out of the first client step `0x40AAFC`). The WER
  dialog sat behind the full-screen surface (the "hang"); the process was held by WER until
  21:27:12 (the `error.log` change time), which is why the socket stayed open through the 5 s echo
  timeout. Root cause and fix: F55. A second crash of the relaunched game (21:29 local, room 2,
  `Lock when already locked` -> `assert(0)` ddex4.c line 1197 -> `Lock` on a NULL surface at
  `0x42F8B8`, after the player had left the battle) is a different, quit-time display bug; noted in
  §10.22, not fixed.
- **Why the relay saw only "no echo"**: a crashed client that WER holds keeps its TCP connection
  open, so the first sign is the missing echo, not a closed socket; the same signature as the sync
  assert of 19 Sep. Both `error.log` of the client (asserts) and the Windows Application log
  (access violations) tell them apart.
- Fix applied to both repository exes (`dc16new.exe`, `engexp16new.exe`), patcher fix `camera`
  (after `ddraw`, before `movies`/`sounds`/`ozi`), docs.
- **Confirmed in game the same day with replay mode** (`REPLAY_FILE=logs/replays/2026-09-19T18-26-56-991Z-room1-J8PLAY01.jsonl REPLAY_SLOT=6`,
  the viewer becomes game player 0 at start position (18,131)): the exe before the fix (extracted
  from git as `dc16old.exe`, deleted afterwards) died at the first frame exactly as on Fly (Event
  1000, fault offset `0x45B52`, "no echo for frame 0"); the fixed `dc16new.exe` showed the
  battlefield and followed the whole 1125 s recording to the victory screen (19 minutes, connection
  closed by the client at the end, no assert, `error.log` empty).
- **Replay-mode bug found on the way, fixed:** the room reset after every game but the `Replay`
  cursor did not, so the second client of a replay server started mid-stream (the first game had
  consumed ~127 frames before its eviction; the client then simulated ticks 0..134 without the
  recorded commands and hit the game's sync assert at the first checksum it received, `sync error:
  time 135`) and a third got nothing at all (`stalled until 322, minClientTime 0`: the lag guard
  blocked frames 200+ ticks ahead of a client at time 0, eviction "idle"). `Replay.rewind()` is
  called from `Room.reset()`; every game of a replay server now starts at frame 0.

**21 Sep 2026, maintainer report: "window restore after minimization is not working" = Alt+Tab / taskbar do not bring the minimised game back (F56; fix `restore`, `tools/patch_restore.py`, `DC16_DISPLAY_AND_RESOLUTION.md` §10.28)**

- Reproduced on the maintainer's PC with a window-state probe (`IsIconic`, display mode, foreground
  window, screen capture every 0.5 s) and a `Wow64GetThreadContext` thread sampler: after Alt+Tab
  back the game is the foreground window but stays iconic; a restore from outside then shows a black
  1280x800 window on the 1920x1200 desktop, `error.log` empty, the process busy in DirectDraw's
  software blit and `restore_surfaces` (~4 400 calls/s). The first probe run also caught the real
  user route by accident: the Start menu opened (Win key) while the game ran, DirectDraw minimised
  the game, the foreground went back to the iconic game when Start closed.
- Cause (F56): `frame_end` swallows every posted `WM_SYSCOMMAND` (it only looks for
  `SC_SCREENSAVE`), so the shell's `SC_RESTORE` is dropped; being activated while iconic defeats
  DirectDraw's own mode re-set, and the game re-setting the mode itself leaves the old surfaces in
  `DDERR_WRONGMODE` for good (measured with an instrumented test build that recorded the HRESULTs).
- Fix: the 107-byte peek block of `frame_end` is rewritten in place (86 bytes code + NOP; calls via
  the import thunks, no `.reloc` change): posted system commands go to `DefWindowProcA`, and an
  `SC_RESTORE` is followed by `ShowWindow(SW_MINIMIZE)`+`ShowWindow(SW_RESTORE)`, the
  deactivate/activate cycle that makes the hook re-set the mode. Four routes confirmed in game on
  both exes the same day (Alt+Tab, taskbar button, Start menu + taskbar, taskbar minimise + Alt+Tab).
- Patcher fix `restore` (after `camera`), `Apply-DarkColonyPatches.ps1` regenerated; published
  1024x768 builds are now SHA-256 Classic `9f6b1f7b…`, Council Wars `89c66244…`.
- **Second part, same day (maintainer: "fix cpu burn without stopping the game when minimized")**: a
  minimised game spun at 100 % of a core (its `present` fails BltFast + Restore and returns before
  the pacing `Flip`); the failed-restore `jne` of `present` now goes to a 12-byte `Sleep(1)` stub in
  the spare tail of the rewritten block. Ticks and echoes keep running, 5.9 % of a core minimised
  vs 27.8 % visible at the menu (1280x800 Classic). Reference 1024x768 builds with the stub:
  Classic `ca488306…`, Council Wars `2ae4e2e9…`; the game folder holds the 1280x800 builds `98a24204…` / `ce962147…`.
  Smoke test on the Fly relay passed the same evening (maintainer); committed, the repository exes
  are the reference builds above.

**21 Sep 2026, maintainer report: a player "still sees Please insert Dark Colony CD" (two screenshots of the OZI campaign introduction) = a missing file shown as a CD request (F57; `nocd` edit 9, `tools/patch_nocd.py`, `DC16_DISPLAY_AND_RESOLUTION.md` §10.19)**

- The text is the sprite `intrface/insee`, drawn by the display's CD-prompt method when the file-open
  helper misses a *required* file; the method then loops on `fopen` — the stock "insert the disc"
  wait, endless since `nocd` removed the CD path. The file was `intrf_hd/hxscene.txt`: the five
  `ozi_ns/intrf_hd/` files (OZI scripts + scene lists, §10.17 of the display doc) had never been
  committed — the unanchored `.gitignore` rule hid the folder until 21 Sep and the anchoring commit
  did not add it — so every clone running the published Council Wars exe hit it at OZI MISSIONS →
  (race, name) → NEXT. The maintainer noticed the untracked folder independently the same evening.
- Fixes: the five files committed (1024x768, byte-identical to what the patcher's
  `Write-InterfaceSet` writes; staged as blobs so the game folder's 1280x800 set stayed in place);
  `nocd` edit 9 rewrites the 68-byte head of the CD-prompt method into the wave loader's error exit
  (error.log line `unable to open file <name>`, desktop mode back, box "FILE NOT FOUND / <name>",
  exit 0), four `.reloc` entries re-pointed, nothing moves. Patcher regenerated (`nocd` 18 / 19
  edits); a rebuild from the originals in a clean checkout of the staged tree is byte-identical:
  published 1024x768 builds Classic `49abd4e3…`, Council Wars `97eaaf01…` (before: `ca488306…` /
  `2ae4e2e9…`); the game folder's 1280x800 builds are `14ed298e…` / `6fa9a043…`.
- Confirmed in game the same evening (1280x800 `engexp16new.exe`, `ozi_ns/intrf_hd` renamed away,
  a ctypes probe posting the clicks OZI MISSIONS → HUMAN → START CAMPAIGN → NEXT): the box named the
  file 2 s after the click, OK → exit code 0, error.log had the line. `dc16.asm` / `dcexp16.asm`
  regenerated from the new 1024x768 exes.

**21 Sep 2026, maintainer: "remove 150% speed patch from patcher"** — fix `speed` (`patch_speed.py`,
§10.14 of the display doc, 10 Sep 2026) dropped from `gen_apply_script.py` and the regenerated
`Apply-DarkColonyPatches.ps1`; the published exes and the game folder's 1280x800 builds are back to
the stock 66 ms in both tick dwords (default 100 %). Published 1024x768 builds: Classic `36d3bada…`,
Council Wars `57b28dbf…`. The tool itself stays in `tools/`. Multiplayer speed was never affected (the
relay sets it, F11).

**21 Sep 2026, player report via the maintainer: a fresh download of the repository and the patcher
shows "Check failed - nothing written: fix 'nocd': the bytes at file offset 0x478 are already patched"**
— the player browsed to the game exe they play, `dc16new.exe` (already fully patched; 0x478 is the
movie-opener edit, the lowest offset of `nocd`), as the "original". The window accepted any known
build with a note and failed only at Apply. Now (`gen_apply_script.py`, patcher regenerated): a
browsed exe whose first fix is already applied is recognised as a patched build; if the untouched
original (`dc16.exe` / `ENGEXP16.EXE` / `maped.exe`, checked by SHA-256) sits beside it, the window
switches the input to that file and makes the browsed file the output ("the result replaces
dc16new.exe"); otherwise it refuses with the name of the file to browse to and disables Apply. The
per-edit check's message names the originals too. Tested headlessly (pwsh and 5.1): redirect,
refusal for both games, untouched original unchanged.

**22 Sep 2026, maintainer report "when patching to widescreen engexp16new.exe isn't widescreen ...
in the fresh copy of repo"** — the fresh copy was the GitHub ZIP extracted to `Downloads\Dark-Colony-main`;
a blob-hash comparison against the repository showed that nothing in it had been patched (its exes and
`INTRF_HD` were still the published 1024x768 set) while the widescreen outputs had been written into the
`Documents` game folder; the games launched from the ZIP copy (three fresh `.o16` caches) were the
1024x768 exes. The patcher itself produced the reference Council Wars exe for 1280x800 and 1280x720 in
every configuration tried (CLI, window, PowerShell 5.1 and 7, CRLF and LF clones, the ZIP copy's own
script). **Found on the way (F58; fix `longpath`, `tools/patch_longpath.py`, `DC16_DISPLAY_AND_RESOLUTION.md`
§10.29):** run from the Claude scratchpad (161-character game folder path) either exe dies 5 s after
start with "FILE NOT FOUND / A sound file is missing" and an empty file name in `error.log` — the wave
loader is the one place that opens files with the Win16 `OpenFile`, whose `OFSTRUCT` path field holds
128 characters. The two live opens now call a `CreateFileA` stub placed over the dead first CD attempt
(dead since `nocd`, which the fix therefore requires) and the error exit names the file it tried; 4 edits,
57 bytes per exe, no `.reloc` change, Council Wars = Classic + 0x60. Verified from the long path (unfixed:
box at 5.1 s; fixed: plays on, `error.log` empty). Patcher regenerated (fix `longpath` after `restore`,
`Requires nocd`); published 1024x768 exes now Classic `71b570fa…`, CW `d4ca8555…` (old published + tool =
the same bytes); the game folder's 1280x800 Classic / 1280x720 CW builds got the fix in place
(`371cf781…` / `de9b447c…` = the patcher's references for those sizes); `dc16.asm`/`dcexp16.asm` regenerated.

**22 Sep 2026, maintainer: "investigate how to add original music from CDs", then "extract the tracks and encode them to mp3 at 192 kbit/s and update patcher to use original tracks in corresponding executables"** - F59. The soundtrack turned out to be Red Book audio (4 tracks per disc, 10.2 / 13.0 min) played through MCI `cdaudio`, dead on every PC without a CD-ROM drive. Tracks sliced from the `.bin` images (silence-gap boundaries, no `.cue`) and encoded with LAME to `MUSIC/TRACK02-05.MP3` + `exp/music/track02-05.mp3` (32 MB, committed); `tools/patch_music.py` = patcher fix `music` (both games, `Data` = the four tracks) rewrites the cdaudio module as an MCI `mpegvideo` player with the disc's own "track 2 to the end, repeat" logic and wires the music slider to `MCI_SETAUDIO`. A first build stored the track number after the stdcall (edx clobbered) - caught by reading the state block of the running game. Verified on the 1280x800 Classic build (MP3 modules loaded at the menu, state block and audio session live in the demo battle); the Council Wars run and the track-3 transition were not watched yet. Published exes now `5a2e10b7...` / `d7b30c1a...`. `DC16_DISPLAY_AND_RESOLUTION.md` §10.31.

**22 Sep 2026 evening, maintainer: "your last changes broke patched executables. on the battlefield first click hangs the game"** - F60. The Windows Application log answered within a minute: four Event 1000 access violations of the Downloads-ZIP exes (`b0551fc0…` / `f00fc454…` = commit `91e3f06`), fault offset `0x5F58` / `0x5F38` = byte 13 of the `widemap` spot-order stub in the dead `cd_probe` body, which read the map pointer from the wrong object (the order builder receives the client object, not the game state; `cl+0xC` holds the game state, exactly as the pick `0x409850` reads it). Three-byte fix in `patch_widemap.py` (`mov ecx,[eax+0Ch]` first; stub 46 -> 49 bytes, body 207 of 219), generator length table updated, `Apply-DarkColonyPatches.ps1` regenerated, both exes rebuilt (`a71d038b…` / `bb6a1e77…`; the diff to the broken exes is the 49-byte stub only), `dc16.asm` / `dcexp16.asm` regenerated. Confirmed by the maintainer in game and by scripted `SendInput` clicks from a `subst V:` copy (Esc skips the movie, NEW CAMPAIGN -> leader name -> START CAMPAIGN -> NEXT -> TO BATTLE, then select a unit and give three spot orders, one into unexplored ground) in Classic mission 1 and Council Wars council mission 1: units move, process alive, no event, `error.log` empty. The three earlier "mystery exits" with code 0 during that test were the maintainer closing the full-screen game by hand. Rule from this: nothing that touches the click path is published without a ground click in game. Found on the way (TODO, documented in `DC16_DISPLAY_AND_RESOLUTION.md` before 10.31): the patcher resolves a relative `-Output` with .NET `GetFullPath`/`WriteAllBytes` against the process working directory, not PowerShell's `Set-Location` - the resource check then looks in the wrong folder (fixes greyed out as RESOURCES NOT FOUND) and the write fails with `Could not find a part of the path`; nothing is written. Fix = `GetUnresolvedProviderPathFromPSPath` on `$Output` once at the CLI entry; until then use absolute paths from tools.

**23 Sep 2026, maintainer report "engexp16.exe is hanging when entering main menu", then "add missing widgets which are the cause of the problem" and "do the same widgets layout as in dc16.exe where ozi missions replaces intro and ozi load replaces single player war"** - F61. Not the patched build: the game folder's 1280x800 `engexp16new.exe` verified as all thirteen fixes with a matching interface set, `error.log` empty (the game truncates it at every start, `0x40529F`), no Application-log event since the `widemap` clicks of 22 Sep. The stock bytes named the cause instead - the CD-less greying of six widget ids against a script that defines three of them, the widget.c:152 assert of 14 Sep 2026 (§10.17) that nobody had fixed because "the retail game does it too". Moving the script cannot help (the exe tries `exp/intrface/bintroe`, then the bare root name, and nothing else), so the maintainer chose the layout fix: `exp/intrface/bintroe` = Classic's script, the full 2x4 button grid, and the Council Wars menu becomes Classic's with OZI MISSIONS in the PLAY INTRO slot (button 16, bottom left) and OZI LOAD in the SINGLE PLAYER WAR slot (button 4, middle right). Because every derived script is letterboxed from that one file, `build_ozi_overlay.menu_rows`/`menu_script` and the patcher's `Edit-OziMenu` shrink to those two renames plus a grid check, and `Edit-OziMenu640` (the two-column plan of 21 Sep, §10.27) is gone. Regenerated and cross-checked: the Council Wars HD menu is the same size's Classic `INTRF_HD/BINTROE` plus the two renames for all four sizes (`hd_sets/` fixtures, the game folder's live 1280x800 pair and the `ozi_ns` copy), the Python tool reports `0 edits` and refuses the old five-row file, and a full `-All -Resolution 1280x800` patcher run on a scratch copy rebuilt the maintainer's exe byte for byte with an identical interface set. This is the first deliberate exception to "everything the original exe reads is byte-identical to the CD" (§10.17). The code-drawn credits box then decided the vertical position (maintainer: "for unpatched engexp16.exe put credits a bit higher and buttons a bit lower so they don't overlap each other"): its y is an exe immediate (Council Wars 230, box rows 230..329) that the untouched exe cannot move, and Classic's grid starts at 314, so the grid went down 16 px to 330 - one row below the box, bottom row ending at 432 against backdrop artwork that starts at 436, i.e. the whole remaining budget. Wherever the exe IS patched the box goes up instead, and it has to go one row further because the maintainer's third instruction of the day made the patched menu five rows deep (ACADEMY first, then the two Council Wars entries, then OZI MISSIONS and LOAD OZI GAME; MULTI PLAYER WAR, ENCYCLOPEDIA and QUIT in the second column's rows 1, 3 and 5): and it also has to get shorter, because the fourth and fifth instructions ("add spacing between the rows of menu buttons ... between 1 and 2 row, between 3 and 4 rows", then "change to 50 % spacing and add 50 % spacing between columns") added 27 px of block that neither the 3 px above the 640x480 artwork nor the 1 px under the title could pay for: the TTY is created with four immediates (width 280, y, height 100, x) at `main.c bintro`, so the patched builds write **68 rows at y = 203** at HD sizes (`patch_resolution.py`: `credits_y(203, 296)` plus the first Council Wars-only *site* for the height, `push 64h` -> `push 44h` at file `0x429E`; Classic keeps its four rows and its 100-row box) and **52 rows at y = 219** at 640x480, where `resolution` never runs and the stock backdrop draws the planet's crescent across rows 198..218 (`ozi` edits 15 -> 18).  Both leave the box 9 px above the first button row. `build_ozi_overlay.menu_layout` and the patcher's `Edit-OziMenu` place the rows from the script's own geometry - columns, pitch and button height read off it, anchored on the bottom row - so they are idempotent, and rewrite five labels. Published CW builds: 1024x768 `4783eb2d…`, 1280x800 `772672fc…`, 640x480 `45399ed3…`; Classic unchanged `a71d038b…`. **Confirmed in game the same day** on the 1280x800 build: the five-row menu as designed, hover +93 % on the hovered plate and 0 % on the controls (hit rects where the script says), one click opened ENCYCLOPEDIA (never reachable in any shipped Council Wars build), one click on QUIT left with exit code 0, and `error.log` empty throughout. **The plate animation is NOT verified:** the maintainer reports that nothing animates, and the one change made to it - moving `anim_oneoff` from gadget 6 to the first row's gadget - was reverted on request the same day (this entry's predecessor claimed it confirmed; what was actually sampled was a single static frame per state, so a plate cycling while hovered or pressed would have looked identical to a static one). What LARGEBUTTON even is stays open: it is frames 0..11 of the `knobe` bank, and those cells are differently sized plates (180x26, 180x26, 90x26, 90x26, 60x26 ...), not one plate in twelve poses. Test rig: `subst V:` on the game folder, SPACE (not ESC - the menu quits on ESC) to abort the intro movie, captures from a DPI-*aware* process and input from a DPI-*unaware* one, since the game is unaware and a DPI-aware `SetCursorPos` is off by the 150 % factor. Still open: the untouched exe without a disc, MULTI PLAYER WAR from Council Wars (never played over the network), and the other resolutions of this menu. `DC16_DISPLAY_AND_RESOLUTION.md` §10.35.

**23 Sep 2026, maintainer instruction "it's time to add classic missions to engexp16 by adding paired buttons DARK COLONY + LOAD DC GAME between ACADEMY and COUNCIL WARS in main menu. menu grows in height so in 640x480 resolution we'll have to remove credentials [credits]"** - F62, F63. The expansion build has carried the whole Classic campaign since it shipped (`DC16_SINGLE_EXE_MERGE.md`, 11 Sep 2026) and the shared folder has carried the Classic data since 15 Sep, so this is the mode mechanism of §10.13 with a fourth mode: `dc/` in the two prefix slots and `save` in the two save-folder slots, which makes every open fall through to the Classic root - 106-type `GAMESTAT/GAMESTAT.TXT`, `MISSION/` briefings, `SCENARIO/HUMAN|ALIEN`, `INTRF_HD/HSCENE|GSCENE.TXT`, and the `SAVE/` folder `dc16new.exe` itself uses (the same two slot values, checked). The overlay holds one file, the patched menu script, because the mode is sticky and the game root has Classic's own menu. Four code edits beyond the stub and its two trampolines: the id filter 5 -> 7 (one byte), the end of the id chain into the NOP tail, the two handlers in those 59 bytes, and the credits box. **The credits box had to go at every size, not only at 640x480:** the seven-row block is 217 rows tall, the 640x480 backdrop's black band between the crescent and the artwork is exactly 217 rows (218..434, measured), and at the HD sizes the block sits in the same place relative to the title because the whole cluster scales with `credits_y`. **The first game test (maintainer) found F63** - "race overview is empty and clicking button hangs the game": removing the create left the matching destroy running, the single-instance TTY counter went to -1 and the next screen's text window was built at index -1. Fixed by setting that destroy's count to 0. **Confirmed in game after the fix** (maintainer: "dark colony missions work now"), and one
follow-up the same evening ("ACADEMY saves goes to COUNCIL WARS saves. save them in DARK COLONY
instead"): TRAINING plays `SCENARIO/TEST` off the Classic training scene lists, which nothing in
`exp/` shadows, but its handler still went through `tramp_cw_campaign`, so it saved to `esave` and
played the Council Wars briefings for `mission/h1..h7.wav`; its call at `0x00405083` now goes to
`tramp_dc_campaign`, five bytes, and both follow the Classic data. Cross-checks that did hold: the Python and PowerShell menu generators produce byte-identical scripts at all five sizes (640x480 rows 219..408 with the group gap shrunk by one pixel so the first row clears the crescent, 1024x768 414..606, 1280x720 384..576, 1280x800 433..625, 1280x1024 571..763), both are idempotent and both accept the stock grid as well as their own output, and the patcher rebuilds the tool chain's exe byte for byte (1024x768 `a19972fb…`; Classic untouched at `a71d038b…`). `DC16_DISPLAY_AND_RESOLUTION.md` §10.36.

**23 Sep 2026, maintainer instruction "mod specific files must go under 'jupiter' folder next to 'exp' folder of 'Council wars' and must be added to main menu as 'JUPITER MISSIONS' and 'LOAD JUP GAME' as last in the left column. You must get sure that all specific sounds, savefiles, balance tables, animations and other specific to this mod files are applied only to these Jupiter missions!"** - F64. The mod (`DCJM_setup.exe`, Inno Setup, extracted with innoextract into `Dark-Colony-development/DC - Jupiter mod`) turned out to be a Council Wars-in-root install plus a unit / balance mod over the 106-type Classic table with no missions of its own, so JUPITER MISSIONS is the original campaign under the mod's tables: the fifth prefix mode `jupiter/` + `jupsave`, built like DARK COLONY (§10.36). Two exe facts shaped it: `jupiter/` is eight letters and the stock 8-byte slots are followed by the next string, so the two prefix reads (single `mov esi,imm32` each) now point into the dead `intrface/credits.txt` string as two 12-byte slots; and the start-up sound table has exactly 200 entries, all loaded at start-up under the CW prefix, so the mod's one sound (`ion.wav`, entry 200 - an array overflow in the mod's own install) cannot be made mode-specific and was left out (documented; a shared entry could be traded for it). Code: the two handlers fill the 45 NOPs of the removed credits create exactly (flag, `stub_jup_set` at `0x47F3B0`, `cmp edi,9`, runner or load screen), reached through a `jmp` in the dispatch pad; the id filter goes to `cmp edx,9`; the `.reloc` insert grows to 16 entries; 7 tail bytes left. Menu: nine rows in the left column with the group gap after rows 1, 3, 5, 7, now anchored at `H-72` at every size (the stock 640x480 bottom row; every painted backdrop's artwork starts at `H-45`), because the old bottom-row anchor would have put row 1 under the title at 1024x768; at 640x480 nine rows cannot fit the 217-row band, so the two Jupiter buttons sit in rows 4/5 of the second column there. Overlay by the new `tools/build_jupiter_overlay.py` (tables, `slist.dat` minus the sound-200 line, the HUD verbatim + laid out for HD by the hud_layout rule, save marker); `build_ozi_overlay.py` writes the menu copy; the patcher generator gained the PowerShell twins (nine rows, `jupiter\` copies of menu and HUD, the 640x480 `bintoze`, the data list). Cross-checked: PowerShell = Python for every size, patcher rebuilds the tool-chain exe byte for byte, `dcexp16.asm` regenerated. **Two lessons from the game test:** a scripted run on a LOCKED desktop (black captures, driver access violation on the first click, DARK COLONY control included) proves nothing - check `LogonUI` first; and the 45 bytes of the removed credits create are LIVE code the menu init runs through every time, so the first build (CW `671c58d2…`) crashed at menu init with fault offset `0x4E80` = the handler's first byte - the block now opens with a jump over itself (44 bytes + NOP). **Confirmed in game the same night** with the fixed build (CW 1024x768 **`c9a8b838…`**, 640x480 `0e49c9f8…`; Classic untouched): nine-row menu, JUPITER MISSIONS -> race screen -> briefing -> mission 1 battlefield, LOAD JUP GAME -> load screen, `error.log` empty. **Dropped the next day** (maintainer, 24 Sep 2026: "jupiter missions are the same as DARK COLONY missions. is that right?" - yes, the mod has no missions of its own - "drop it but save findings in documentation"): both repositories reverted to their committed state (CW exe back at `b12a0b14…`), the overlay, save folder, new tool and extracted installer removed; the findings (movable prefix slots, live credits bytes, 200-entry sound table, `H-72` menu anchor, the two test-rig rules) stay in `DC16_DISPLAY_AND_RESOLUTION.md` §10.37. Lesson for the next request of this kind: when the input turns out to be something other than what the request assumed (a balance mod, not a mission pack), say so and ask before building the mode.

**24 Sep 2026, maintainer instruction "return back credentials [credits] for higher that 640x480 resolutions!"** - amends the fix of F62/F63. The 23 Sep removal of the main menu's credits box rested on a 640x480 measurement (217-row black band = 217-row block) applied to every size; the painted HD backdrops are black from the planet's crescent down to the bottom artwork at H-45, and the scaled block had left 90 unused rows under itself at 1024x768. The HD block now hangs from the `DCUT` title instead of the stock bottom row: first row 120 rows under it (11 px, the stock 100-row box, 9 px) unless the bottom row would pass H-72 (the stock bottom row 408, the anchor F64's nine-row menu used), where the block stops and the box shortens - box 94 rows at 1024x768 (buttons 504..696), 76 at 1280x720 (456..648), the stock 100 from 1280x800 up (529..721, 667..859, 702..894); 640x480 unchanged (box removed, rows 219..408). Exe: `ozi` carries the create/destroy edits and the page-0x4000 `.reloc` change in its 640x480 form only (`patch_ozi_menu.build(stock_mode)`), `resolution`'s Council Wars height site is per size (`patch_resolution.cw_credits_height`, and `resolve` skips a site whose replacement equals the expected bytes, so the no-op at 100 never reaches the generator). Layout in `build_ozi_overlay.menu_layout` and the patcher's `Edit-OziMenu` from the title gadget and the `size` line only (idempotent); PowerShell = Python byte for byte for the five HD sizes, the four `hd_sets` fixtures and the game folder's three 1024x768 copies regenerated, the patcher regenerated and every Council Wars mode rebuilt against its reference. Council Wars 1024x768 exe `ec7ab499…` (was `b12a0b14…`; staged from a scratch build, the working copy keeps the maintainer's 640x480 build `5d8202f8…`, which is unchanged); Classic untouched. Smoke test on a `subst` copy: the 1024x768 build reached the menu with the credits text scrolling in the box columns (rows 390..474), black between box and first row, buttons on 504..720, `error.log` empty (a synthetic Esc did not quit the game this time; terminated). Not run: the other sizes, a click through the menu. Lesson: a removal justified by one size's measurement must be re-measured at every size it is applied to. `DC16_DISPLAY_AND_RESOLUTION.md` §10.36.

**24 Sep 2026, maintainer instruction "endexp16. move "DC" logo, "DARK CLOLONY" logo, credentials and buttons block 15 points higher for resolutions except 640x480"** - Council Wars only, on top of the entry above. The letterbox rule for the `exp/` override scripts (`paint_intro.layout_for`) takes `paint_intro.cw_menu_lift(H)` rows off its shift, and the same amount comes off the credits y (`patch_resolution`) and the button block's H-72 cap (`build_ozi_overlay.menu_layout`, patcher `Edit-OziMenu`), so logo, title, box and buttons move together and the box heights stay 94 / 76 / 100. The lift is 15 wherever the opaque DC logo stays under the planet's crescent, whose painted fade ends at row 112 of the 480-row design over the logo's columns (measured: 55/255 right above the logo at 1280x720, 7/255 two rows above it at 1024x768, nothing at 1280x800), and therefore **0 at 1280x720**, where the logo already touches the tail and a lift would cut a black notch into the planet; a pure function of H (`max(0, min(15, logo_top − round(112·H/480) − 1))`, `Get-MenuLift` in the patcher), so no tool measures anything at patch time. Results: 1024x768 logo 183, title to 374, box 386..479, buttons 489..681 (all 15 up); 1280x800 514..706; 1280x1024 652..844; 3840x1080 687..879; 1280x720 and 640x480 unchanged. Patcher regenerated, every Council Wars mode rebuilt against its reference (1280x720 `1f3e3c8b…` and 640x480 `5d8202f8…` byte-identical to before), PowerShell = Python for the five HD menu scripts (background line aside), fixtures and the game folder's 1024x768 copies regenerated; CW 1024x768 exe `39a49a3b…` (staged, working copy keeps the maintainer's 640x480 build), `dcexp16.asm` regenerated. Game test at 1024x768 on the `subst` copy: logo 183..302 with the crescent above it untouched, title 342..374, credits scrolling from 386, buttons 489..705, `error.log` empty, Esc quit with exit 0. `DC16_DISPLAY_AND_RESOLUTION.md` §10.36 (end).

**25 Sep 2026, maintainer question "is it worth to convert whole patching script to c# with classical cmd script which acts as an builder and starter of compiled c#", then "add the launcher and update the howto and fix -Output"** - patcher only, no exe byte changed. Assessment (answer: no rewrite): the script is ~8 700 lines of generated edit tables, ~1 700 lines of logic (mostly the byte-exact interface-set port) and ~440 lines of window; `-List` takes 3.0 s under 5.1 / 1.1 s under 7, a six-fix run 1.4 / 1.0 s, so speed is no argument. A single-file cmd/C# hybrid works with the in-box `csc.exe` 4.8 (C# 5 only; first line `/* 2>nul`, batch lines compile the file itself, `*/`, C# follows; 1.2 MB with the tables as object initialisers 9.9 s compile, as one text block parsed at start 3.8 s), but it would mean re-porting and re-verifying everything, and cmd -> csc -> unsigned exe -> run is the "compile after delivery" pattern that security products watch, the opposite of the patcher's purpose. The real friction was the start: on this Windows 11 build *Run with PowerShell* is `powershell.exe -file "%1"` without Bypass, so under `RemoteSigned` a script from the GitHub ZIP (Mark of the Web) is refused and the console closes at once, and under the client default `Restricted` every script is. **Done:** the starter in the Dark-Colony root, first `Apply-DarkColonyPatches.cmd`, the same day renamed **`INSTALL.CMD`** (maintainer: "rename cmd script to INSTALL.SH" - `.SH` has no Windows handler, on this PC it opens Git Bash / VS Code, so `.CMD` was agreed; the `install` UAC heuristic of 15 Sep only hits manifest-less exes, `cmd.exe` has a manifest) (hand-written, not generated, 30 lines incl. comments): `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Apply-DarkColonyPatches.ps1" %*`, exit code passed on, and on an error it pauses only when started without arguments (= double-click). Two batch pitfalls hit: `setlocal` resets ERRORLEVEL to 0 (the code must be taken before it), and a `!cmdcmdline:name=!` comparison inside `if` never matched because `cmdcmdline` carries quotes - hence the no-arguments rule. `.gitattributes` `*.cmd -text` because `core.autocrlf=true` would store the batch file with LF and GitHub's ZIP would ship it that way (the launcher has no labels, so it would survive LF anyway). **The relative `-Output` wart of 22 Sep is fixed** (see before §10.31): new `Get-AbsolutePath` (`$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath`) normalises `-Output` right after its default is chosen and the path the window loads, so .NET calls and `Test-Path` agree. Tests: old script from a session whose process directory is `C:\Windows` and `Set-Location` elsewhere -> `Could not find a part of the path 'C:\Windows\rel\...'`, new script writes (5.1 and 7); a full `-All -Resolution 1024x768` run with a relative `-Output` into a scratch copy of the game folder skipped no fix and gave `a71d038b…` = the published `dc16new.exe` (both shells); headless window `& $gui.Load 'DC - Council wars\dc16.exe'` shows absolute input/output paths; the launcher from a folder named `Dark Colony (copy) & more!` with a Mark-of-the-Web `.ps1` ran it (plain `powershell -file` refused the same copy once the tool session's inherited `PSExecutionPolicyPreference=Bypass` was cleared - a pitfall for every policy test from this session), passed quoted arguments, returned 1 / 0, paused after a start-up error without arguments and not with them, and a ShellExecute start opened the patcher window. Not tested: whether SmartScreen / the Attachment Manager prompts for the downloaded `.cmd` itself (the HOWTO covers both prompts). `PATCH_HOWTO.TXT` (start = double-click the `.cmd`, warnings, a new "IF IT DOES NOT START" part, command line through the starter, current patch ids, stale 150 % speed removed) and `README.md` updated; generator help text names the starter; patcher regenerated (only the three code lines and comments differ). **Desktop shortcut** (same day, maintainer: "add to ps1 script possibility to put shortcut on the desktop"; target = the patched game, chosen by the maintainer over a shortcut to the patcher): checkbox "Desktop shortcut" right of the "Written to" box (ticked by default; fixed size - an auto-sized checkbox anchored right grows to the left and overlapped the box), CLI switch `-DesktopShortcut`; after a successful write `New-GameShortcut` creates `Dark Colony.lnk` / `Dark Colony - Council Wars.lnk` / `Dark Colony map editor.lnk` through the WScript.Shell COM object on `[Environment]::GetFolderPath('Desktop')` (OneDrive-redirected desktops included): target = the written exe, start folder = the game folder (the game opens its data relative to it), icon = the exe's own (both game exes carry an icon group, the editor none), an existing shortcut of that name replaced; skipped with a note when the interface set was not written; a failure is reported in the result box, the exe stays written. Tests with `$script:DesktopFolder` pointed at a scratch folder (headless window: Council Wars, all fixes at 1280x800 = its reference, shortcut read back with the right target/start folder/icon, none when unticked; CLI through `INSTALL.CMD` from a test copy: Classic `-All -DesktopShortcut` = `a71d038b…` + `Dark Colony.lnk`), 5.1 and 7; the real desktop was never written. Not run: starting the game from a shortcut. **Then, same day, the one-shot installer** (maintainer: "all three files for patching must be selected by default so installer patches all in one shot. resulting files and shortcuts names must be: 'Dark Colony map editor 1.2', 'Dark Colony', 'Dark Colony Ultimate'. Based on DC.ICO create a high resolution icon and apply it to all three patched files"; first asked for `INSTALL.SH`, agreed as `INSTALL.CMD` because Windows would not run `.SH` as a batch file): the patched builds are renamed `Dark Colony.exe`, `Dark Colony Ultimate.exe`, `Dark Colony map editor 1.2.exe` (`git mv`; shortcuts = the same names; the editor became **`Dark Colony Map Editor.exe`** later that day, maintainer: "rename 'Dark Colony map editor 1.2' to 'Dark Colony Map Editor'" - `git mv`, generator `BUILDS` exe/product, README, HOWTO, INSTALL.CMD, patcher regenerated, bytes unchanged), the window lists all three originals found beside the script, ticked, and one button patches them all with one result box, `-All` without `-Original` does the same on the command line, and the new last fix `icon` (`tools/patch_icon.py`) appends a `.dcicon` resource section with the images of `DC - Council wars/DC_HD.ICO`, rendered by the new `tools/make_dc_icon.py` from `DC.ICO`'s geometry (3-D letters and a thin pixel-snapped frame after two further requests). New patcher edit kind `Append` (Base64 + SHA-256). Published 1024x768 builds `e9cc0561…` / `dcd973eb…` / `c72dd205…`; without `icon` byte-identical to the previous ones. All three started under their new names from a `subst` drive; the running game's window now has a class icon (group id 101, which `create_window` asks for and the stock exes lack). Test-rig slip: `V:` was already a `subst` drive (not mine), my test's cleanup removed that mapping - check `subst` first and remove only mappings the test itself made. `DC16_DISPLAY_AND_RESOLUTION.md` §10.38. Follow-up the same day ("there must be three browse buttons. One button for each executable in installer"): the executables list became three rows of controls, each with its own Browse button; tested headless under 5.1 and 7 (rows ticked by default, row click switches the fix list, an unticked row is left out, all three byte-identical). Then ("create a tabs for checklists of these three files"): the fix checklist became a tab control with one tab per executable, each with its own Select-all box, synced with the rows; tested the same way (tabs 15/14/5 fixes ticked, a fix unticked on one tab survives a resolution change, a skipped row's tab is titled "(skipped)", apply byte-identical). `DrawToBitmap` draws stray lines around a `TabControl`; a screen capture of the shown window has none. Finally ("let's do the classical installer way for patch region instead of tabs ... greeting message and button forward. second screen ... DC, third ... CW and fourth ... maped"): the window is a wizard - Welcome, Step 1..3 of 3 (one page per executable: include box, original + Browse, resolution on the game pages kept equal, checklist, description, output), Patch, Finished page with the results; tested headless under 5.1 and 7 (navigation and button states, resolution sync, an unticked fix and an unticked executable left out, results on the Finished page, Close). `DC16_DISPLAY_AND_RESOLUTION.md` §10.38 (Window). And ("if original of one of files are not originals, then bring in front a big red error and offer to select a correct file or to download it from original discs or our repo"): a missing, unreadable, unknown, already patched or modified original puts its page into an error state - locked include box, a big red panel with found vs. expected SHA-256, Select the correct file..., Download from our repository (opens the file's GitHub page) and the original-disc hint (ENGEXP16.EXE and MAPED.EXE are on the CDs byte-identical; dc16.exe only in the repository, the CD has the 1997 build) - and a red banner on the welcome page; a modified copy is no longer accepted in the window (`-Force` on the command line still). Tested headless with four bad inputs. §10.38.

**25 Sep 2026, maintainer question "check carefully, are classic and ultimate network games 100% compatible?", then "do both fixes and update the docs. get sure that ozi missions will not be broken without this animation" and "skip the game test, rebuild the exe and update docs"** - F27 revised, F65. Answer: not 100 %, two differences. Method: a relocation-aware byte comparison of the untouched `dc16.exe` / `ENGEXP16.EXE` (identical outside the menu/start-up block `0x405000..0x406600`, the medal constants and the credits y; `.bss` delta 0; DGROUP constants incl. the rand table identical), the 32 `rand()` call sites and every inline use of its index (simulation, triggers, missiles, AI only), and `tools/sprdata2json.js` run on a merged Council Wars file view (root + `exp/animate`, `exp/sprites`, `exp/animozi.dat`, with the CW and the OZI table sets). The first difference is data: `troo.fin` / `grrr.fin` give ten Classic types a DEPLOY animation, and the Gray commander's rally waits 28 ticks for it against Classic's 2. To prove it matters, the 19 recordings of `logs/replays` were run through three engine instances in lockstep (Classic sprite data, Council Wars before, after): the six with a Gray commander rally (all from 18-20 Sep, Plink - O) diverge 3-10 ticks after the first rally, the thirteen without one never do, and the fixed data never diverges. The second is the sticky menu mode (MULTI PLAYER WAR after OZI MISSIONS = the pack's tables). Fixes, both in the patched Council Wars build only: `exp/animozi.dat` without the two FINs (`build_ozi_overlay.py` `NOT_IN_OZI_ANIM`; OZI and Council Wars checked: nothing else names those animations, the affected deploy slots fall back to STAND as in Classic, the pack's own units unchanged), and `patch_ozi_menu.py` item 8 = `tramp_dc_net` (`0x47F3B0`, `call stub_dc_set; jmp 0x405C20`, 13 bytes, no `.reloc`), so a network game always runs on the root's Classic tables. Patcher regenerated; PowerShell 5.1 run = tool chain byte for byte. Published Ultimate 1024x768 `7f5e5322…` (staged via `git hash-object`/`update-index`), the maintainer's 1280x800 working copy rebuilt to `2b430a8f…` with only the exe copied back; Classic `e9cc0561…` unchanged; `dcexp16.asm` regenerated. **Not run in the game** (maintainer: skip the game test): the MULTI PLAYER WAR click after OZI mode, a network battle with an Ultimate client (mixed or not), an OZI mission with the new list. Replay-mode idea for that test: `REPLAY_FILE=<one of the six recordings>` into the fixed Ultimate build should play to the end, the pre-fix build should hit a sync error a few ticks after the first Gray rally. `DC16_DISPLAY_AND_RESOLUTION.md` §10.39, `DC16_SINGLE_EXE_MERGE.md` §4.2 correction.

**25 Sep 2026, maintainer report "endotermic played against 7 bots. at some point in the middle of the game client got 'lost connection'. Client used latest stock Dark Colony Ultimate.exe"** - F66, the first Dark Colony Ultimate game on the wire (`7f5e5322…`, the build with the deploy-animation and sticky-mode fixes of F65).

- Recording fetched with `node tools/logs2replay.js --fetch dark-colony-server --since 3d` (1101 log
  lines, 13 pages; raw copy `logs/fly/fly-2026-09-25.log`, gitignored) →
  `logs/replays/2026-09-25T14-08-09-018Z-room1-J8PLAY01.jsonl`: room 1 (Plink - O), endotermic slot 5
  / game player 5, Gray, seven Krusty bots (`/botcount 7`), 20 231 frames to tick 20 241 (14 min 50 s at
  44 ms), 41 210 commands, `SYNC_CHECK=send`, no client checksums (the fake host holds slot 0).
  `tools/replay.js` runs the engine through it without an assert.
- What the log shows: the server never stalled - the 30 s `stats` lines put tick 19 790 at 14:22:39.8
  and the eviction at server time 20 241 at 14:22:59.3, 44.0 ms per tick throughout; the client was
  steady for the whole game (latency 170-235 ms, 20-23 ticks behind, 4-6 echoes pending, 0 strikes) and
  active until frame 20 053 (its last DESELECT, 2.8 s before the silence). Frame 20 116 went out at
  ≈14:22:54.1 and was never echoed, nor were the 116 frames after it; the watchdog evicted the client
  5 s later (`no echo for frame 20116`, `ECHO_TIMEOUT_MS`), a bot took the base over and the room reset.
  No sync error (the client would have asserted, not shown "Connection Lost"), no crash (WER would have
  kept the socket open and the client would have seen nothing), no bot assert (`bots summary` clean).
- "Connection Lost" is the client's reaction to the relay closing the socket (F66, read from the
  battle loop), so the client was alive at 14:22:59 but had sent nothing the relay received for 5 s.
  From the server side the two remaining causes cannot be told apart: a network interruption of ≥5 s
  between the player's PC and Fly (the 5 s echo deadline is strict; the game itself never gives up
  on a silent peer), or the game's loop standing still for ≥5 s on the PC and resuming (the in-game
  dialogs are interface sub-windows drawn by the same loop, so they are not it; a minimised client
  kept echoing in the maintainer's smoke test of 21 Sep, §10.28). The `client left` line now
  carries `sinceLastMessageMs`, `clientTime`, `serverUntil`, `pendingEchoes`, `latencyMs` so the next
  case shows whether progress reports or commands still arrived while the echoes were missing.
- Not changed, for the maintainer to decide: `ECHO_TIMEOUT_MS` (5 s) is far below the game's own
  tolerance (a lockstep stall begins only at 200 ticks = 8.8 s, `LAG_DROP_MS` 10 s later). Raising it
  to 15-20 s would let a short network hiccup pass: the client's held buffer takes the burst (at most
  200 frames, `0x1C00` bytes), the client then plays on ~2 s + the gap behind the server (its timing
  accumulator is clamped to 2000 ms, F38), and a client that is really gone is still caught by the lag
  eviction. Cost: a genuinely dead client holds its seat 10-15 s longer before the bot takes over.
- Same log, unrelated: 12 TCP connections from 14:11:36 to 14:11:56 that closed within a second
  without sending a byte (`hall joined` / `hall left connection closed`), and one at 14:03 that sent a
  non-protocol frame (`invalid frame length 1351`) - port probes, not players.

**25 Sep 2026, maintainer report "ultimate executable main screen. initial animation of buttons are in wrong places. animation must go from top buttons to bottom buttons"** - F67, data + tools only, no exe byte changed. The opening wave of the main menu is the `banim` widget (button.c `create_banim` `0x427854`, update `0x4279EC`): its pair list is walked in order - plate k+1 starts when plate k's one-shot has finished, button k is revealed when plate k settles, and the first listed plate must carry `anim_oneoff` (the flag whose purpose §10.35 had left open; the 23 Sep move of it was reverted for that reason). `build_ozi_overlay.menu_script` and the patcher's `Edit-OziMenu` had kept the stock pair order `0 1 2 3 4 5 16 12 + 6 7` while the seven-row layout moved the buttons, so the wave ran row 4, 1, 5, right 1, 7, right 2, 6, right 7, 2, 3 with the one-shot on COUNCIL WARS (row 4). Now `menu_order()` (and the same sort in PowerShell, key `x·100000 + y`) lists the buttons column by column, left column first, each from top to bottom - the stock 2×4 grid's own sequence - and the plate of the first one gets `anim_oneoff`, the other nine `anim_stopped` (gadget-line token 9, cloned plates included): ACADEMY, DARK COLONY, LOAD DC GAME, COUNCIL WARS, LOAD CW GAME, OZI MISSIONS, LOAD OZI GAME, MULTI PLAYER WAR, ENCYCLOPEDIA, QUIT. Regenerated: the three identical 1024×768 copies in the game folder (`exp/`, `ozi_ns/`, `dc/` `intrf_hd/bintroe`), the four `hd_sets` fixtures, the patcher (`Apply-DarkColonyPatches.ps1`; Python and PowerShell 5.1 / 7 byte-identical on six inputs, idempotent). Game test on a `subst W:` scratch copy with `avi\INTRO.AVI` renamed away: 20 Hz screen-DC capture from a DPI-aware ctypes process, per-plate MD5 - the plates settle ACADEMY 11.57 s → … → QUIT 12.19 s in exactly that order, labels appearing plate by plate, `error.log` empty; a posted ESC did not quit (known), terminated. Alternative noted in §10.40: strict row-major is a one-line sort-key change. Also corrected on the way: the widget keyword table is `0x489588` with `{name, creator}` pairs (`gadget` `0x424A40`, `label` `0x426BD4`, `banim` `0x427854`), not `label` = `0x424A40` as §10.15 had it. `DC16_DISPLAY_AND_RESOLUTION.md` §10.40. Not run: the other resolutions and the 640×480 build.

**25 Sep 2026, maintainer: "ultimate version. now let's update musical selection. battlefield menu for sounds. add new entry for music selection with possibility to select: original game, expansion pack, shuffle them all. entries must be named: DC, CW, ALL. ACADEMY and DARK COLONY must play DC music by default, COUNCIL WARS must play CW by default, OZI MISSIONS must play shuffled all. But in sounds menu client can select any setting in any time", then "640x480 version must contain the same musical menu"** - F68, Dark Colony Ultimate only, `DC16_DISPLAY_AND_RESOLUTION.md` §10.41. The MP3 module of the `music` fix (§10.31) is re-assembled for the Ultimate build (`tools/music_asm.py`, keystone at development time, bytes + fixup table pasted into `patch_music.py`): a source byte (0 DC = `MUSIC\`, 1 CW = `exp\music\`, 2 ALL = the eight tracks in a Fisher-Yates order over a private `rdtsc`-seeded LCG, reshuffled after eight, never the same track twice in a row), a track index 0..7 instead of a track number, two path templates, `start_source` for the battle start and for a source change. The battlefield options dialog gets a MUSIC row (pushb 71 "-" / 72 "+", in_text 73 = textmsg 20/21/22 DC / CW / ALL) through three in-place edits in interface.c: the handler tail -> `opt_tail` (step modulo 3, restart the music if the layer is live, refresh, then the original close), the refresh epilogue -> `opt_refresh_tail` (writes widget 73 only when the script has it), and the handler's `jne` for non-press events -> the epilogue (F68: the first build stepped twice per click). The row is data - `patch_music.music_row()` / patcher `Edit-MusicDialog`, byte-identical under PowerShell 5.1 and 7 - written as `exp/`, `dc/`, `ozi_ns/` `intrf_hd/lopte` from the set's `INTRF_HD/LOPTE`; at 640x480 the DGROUP name `intrface/lopt` -> `intrface/lopm` and the copies are `intrface/lopme` from the stock dialog (the original exe's `exp/intrface/lopte` is never created). Defaults: the `ozi` fix's three mode stubs end with `mov byte [0x5327F4], N` (v5; 80 bytes = their slots, 3 more `.reloc` entries), so ACADEMY / DARK COLONY / LOAD DC GAME -> DC, COUNCIL WARS / LOAD CW GAME -> CW, OZI MISSIONS / LOAD OZI GAME -> ALL; MULTI PLAYER WAR -> ALL as well (maintainer, same evening: "network game must be with shuffle all music set"; `tramp_dc_net` = `call stub_dc_set; mov byte [0x5327F4], 2; jmp 0x405C20`, 17 bytes, one more `.reloc` entry). Same evening, "ultimate executable main menu must always show CW credentials": the credits box reads `intrface/credits.txt` through the mode prefix and showed Classic's text in the DARK COLONY mode and the pack's in the OZI mode - `build_ozi_overlay.py` now copies `exp/intrface/credits.txt` into `ozi_ns/intrface/` and `dc/intrface/` (data only, root stays Classic's). `music` Data for Ultimate = all eight tracks; `music` is a per-mode step now (the 640x480 variant has the name site). Confirmed in game on a `subst` copy (1024x768 patcher build `ce0acfe0…`, final `d1a9b518…`): the dialog shows the row, `+`/`-` step by one through DC / CW / ALL with the state block (`ReadProcessMemory` at `0x5327D0`) switching file, index and shuffle order at once, the stock rows unaffected, the four menu buttons set their defaults, `error.log` empty. Not run in the game: the 640x480 build (`55127ec0…` on the scratch copy: name string renamed, three `lopme` copies written), end-of-track transitions, the volume slider on the new module. Published Ultimate 1024x768 exe `d1a9b518…` (`ce0acfe0…` before the network default and the credits copies); Classic unchanged (`e9cc0561…`); `dcexp16.asm` regenerated. Lessons: a dialog handler in this engine runs for EVERY event kind and only its press branch sets state - hook the press path, not the tail; `rep movsd` needs its explicit operands in keystone; edit scripts with backslashes go through the Write tool, never a heredoc (the `0F 85` bytes of a `b'\x0F\x85'` literal were mangled once more).

**25 Sep 2026, player report forwarded by the maintainer ("I had some issues to overcome. There was 0, file safefunc.c, line 290 when I tried to run it in 32:9. I temporary fixed it by throwing ddraw.dll into game folder. And music does not work for me... Possibly issues in wine and gamescope"; `error.log`: `FILE Error opening file sound\sound2.dat with error num 1` + `assert failure, file safefunc.c line 290 (0)`) - "Investigate this. Linux + wine = several errors". Investigation only (`DC16_DISPLAY_AND_RESOLUTION.md` §10.42, fact F69): the two lines mean the game could not open `sound2.dat` (neither `exp/sound/` nor root `SOUND/`) at start-up while the display object did not exist yet, so the assert box appears instead of the FILE NOT FOUND box; errno 1 is Watcom `ENOENT`; reproduced byte for byte on Windows by renaming both files away. Everything before it (`exp/animozi.dat`, ~180 banks from the uppercase root folders via lowercase forward-slash names) had loaded, so the working directory and case-insensitivity were fine; `sound\sound2.dat` is the only backslash path in the exes - under a stock Wine harmless, so the cause on that machine stays open (asked: `ls -la` of the two files and a `WINEDEBUG=+file` trace); the ddraw.dll "cure" has no mechanism. Music: Wine's `mciqtz32` supports every MCI command of the `music` module (open by element, async play, status mode after EC_COMPLETE, setaudio volume 0..1000), the failing step is DirectShow `RenderFile` without a GStreamer MP3 decoder in the prefix (32-bit `gst-plugins-good`/`gst-libav`); one failed open silences the session (no retry by design). 32:9: a wrapper ddraw is the expected route on Wine too (§10.32), loaded only with `WINEDLLOVERRIDES=ddraw=n,b`. Then, maintainer: "apply the backslash fix to both exes and update patcher" - the one `\` of `sound\sound2.dat` becomes `/` as the fifth edit of fix `longpath` (`patch_longpath.py`: string located in DGROUP in either form, build identified by the AUTO section size so the `.dcicon` builds are accepted, `apply` upgrades the 22-25 Sep builds by that byte; generator `blocks_longpath` = 5 edits; patcher regenerated, 1.26 MB). Published 1024x768 exes `89744c45…` (Classic) / `2da5c86a…` (Ultimate), reproduced byte for byte by the patcher's `-All -Resolution 1024x768` under PowerShell 5.1 and 7 on a scratch copy; `dc16.asm`/`dcexp16.asm` regenerated. Not run in the game, not tested under Wine.**

**25 Sep 2026, maintainer question "investigate if 'dark colony' missions in ultimate version use calibration data for that set of missions?"** - F70, investigation only, nothing changed. Answer: yes, DARK COLONY, LOAD DC GAME and ACADEMY in Dark Colony Ultimate play with Classic's own balance tables, the same 106-type root `GAMESTAT/` set that `Dark Colony.exe` reads. Trace in `dcexp16.asm`: the six table loaders are called in a row by `load_tables 0x43C4AC`, whose only caller is the game-state initialiser `0x41BB50` - run at every battle start (battle function `0x40122C`, all eight game-start sites) and on every save load (`0x40DD0E`) - and every loader opens through the prefix helper `0x4063E4` (`<prefix>` + name, then the bare root name). The `dc/` overlay holds no `gamestat/`, so every table falls through to the root; the tables are rewritten into the global arrays each time, nothing is cached from a previous mode. COUNCIL WARS mode would give the same numbers for the shared units (`exp/gamestat/gamestat.txt` = root + 12 appended types, no other expansion table; `exp/gamestat/gxmestat.txt` is unreferenced), only the OZI set is re-balanced and only under `ozi_ns/`. A save stores the type count and is refused on a mismatch, consistent with LOAD DC GAME setting `dc/` first. Shared by every mode because loaded once at start-up under `exp/`: the sound table (`exp/sound/sound2.dat`, 12 ambience entries differ from root - audio only) and the animation list (`exp/animozi.dat`, Classic-identical in the simulation since F65). `DC16_DISPLAY_AND_RESOLUTION.md` §10.36, `DC16_SINGLE_EXE_MERGE.md` §5.1 corrected (it had assumed the expansion's gamestat would be used).

**26 Sep 2026, maintainer: "relay server. map selection lobby. disable scroll of maps. these fields must be static. format must be this: [number of the room]+[space]+[first word of map name]+[players on the map in format as it is now (X/Y)]. If room is in battle then instead of map name show info about that. Players in map indicator must always occupy last four character places in slot."** - §17.2, server only. The hall's room rows no longer scroll: `Hall.rowText` builds a static 16-character text `"<n> "` + the first word of the map name padded to nine places + `"(<players>/<slots>)"` in the last five (`1 Plink    (0/6)`; a word longer than nine characters is cut, `2 Armageddo(0/6)`; `2 in battle(2/6)` while the room is not in its lobby). The `(X/Y)` indicator is five characters, so it fills the last five places rather than four - the request's "as it is now" (parentheses included) was kept over its "four"; a four-place variant would have to drop a parenthesis. `full` / `slot taken` left the row (the CD icon, the map line and `/rooms` keep them). The step timer still recomputes rows, icons and map line from the live room states every `HALL_REFRESH_MS` (the setting was `MARQUEE_MS`; the old name is still read, `src/config.js`) and sends only changes in one frame (F34), so a quiet hall sends nothing (the scrolling cost about 0.7 KB/s per waiting client). Tests rewritten (`test/hall.test.js`: row format, no traffic without a change, one frame per change, the in-battle row; `test/integration.test.js` checks the format of the rows the fake clients saw); `tools/fakeclient.js` records `hallRows` instead of counting scroll steps. Confirmed by the maintainer against a local server on 27 Sep 2026 ("works").

**27 Sep 2026, maintainer (after testing the static rows locally): "in room lobby shorten Mercenary greeting to first phrase. add "/help" as third line where commands will be described."** - `src/lobby.js` `greeting()` / `helpLines()`. The room header is three rows again: the room line, `Mercenary: Hi! I am the AI host.` (the ` 1000 in battle hires me for 45 s.` suffix while hiring is on and the ` My base stays idle.` of the engine-less server are gone) and `/help lists the commands.` (`HELP_LINE`). The four-row header of 19 Sep (bots row `Bots: 1 krusty, hire off. /botcount N`, commands row `/bottype, /bothire, /help for more.`) is history; `/botcount`, `/bottype` and `/bothire` no longer repaint the header (nothing in it changes), the players see their broadcast line instead. `/help` answers with the current settings first - `Bots: N <type>, hire on|off.` or `The bots stay idle (no battle engine).` - then `/botcount N sets the number of bots (1..7).`, `/bottype krusty|rusher|random sets their brain.`, `/bothire on|off: 1000 hires a bot 45 s.` (`MERCENARY_ALLY_S`); every row is at most 40 columns, so with the three header rows the answer fits the ten-row window (F35). Two follow-ups minutes later: "remove this line 'All here? READY starts the game.'" (gone), and - the maintainer's local test server had run with the code default `SYNC_CHECK=off`, so `/help` opened with "The bots stay idle (no battle engine)." - "remove line that bots stay idle! Bots must use krusty by default! don't write about default state of the bots brain!": the state row is gone (`/help` = the three command rows, `/botcount` / `/bottype` / `/bothire` without an argument still report the settings), and **`SYNC_CHECK` defaults to `send`** (§11), the value Fly has run since 11 Sep 2026, so a plain `node src/index.js` loads the engine and the bots play Krusty; `test/helpers.js` and the integration tests pin `off` so no test changed behaviour. Confirmed locally by the maintainer the same day. Replay rooms keep their own greeting. Tests updated (`botcount`, `mercenary`, `hall`, `lobby`), 245 tests.

**27 Sep 2026, maintainer: "bots must always run on relay! never allow to run them on customer machine!"** - R14. Audit of every path by which a client's own game could run Krusty: (1) lobby slot types - the server never typed a slot computer except through `FILL_EMPTY_WITH_AI`, and clients' `'j'` TYPE messages are dropped as host-owned; (2) the in-battle `DISCONNECT`, whose handler `0x41DBE0` switches the lost player to AI control on every client (protocol doc §6.6) - sent by `Game.onClientLeft` whenever `Bots.takeOver` could not make a server bot (bots off, engine off, engine disabled by a divergence or a start-shuffle mismatch, `AiPlayer` not active), also for a loader evicted during STARTING; (3) the engine's own `ai.js` runs only inside the server. Closed both: `FILL_EMPTY_WITH_AI` / `FILL_AI_TYPE` are gone (`Room.emptySlot` is always type 3; an env value is ignored), and `Game.onClientLeft` clears the pending echoes without queuing anything - a leaving player's base stands idle when no bot takes it over (`room.leave` logs "...; the base stands idle"). Consequences: the survivors get no "lost, AI taking over" line (the server has no in-game chat), the idle base keeps its units and buildings and must be destroyed for the victory check (F49), and `send` mode keeps running because the engine sees no AI takeover; `SyncCheck`'s `aiTakeover` / `AI_SEND` path is reached only by replayed recordings that carry a `DISCONNECT`. Lobby `DISCONNECT`s (leaving the lobby, `/botcount` removing fakes, the hall-to-room dump) are unchanged: in the lobby the message only empties a slot. Tests: `game`, `eviction`, `lobby`, `mercenary`, `synccheck` and the integration test now assert the absence of the message; 245 tests. Not yet seen with a real client leaving a battle.

**27 Sep 2026, maintainer: "add one more command for lobby. '/botteam N' sets for each client N bots which are always teamed with client with shared vision until either player looses connection or game ends."** - §19.11, `BOT_TEAM`. Every real player gets N team bots: fake humans on the player's lobby team (allied by the game at the start), which in the first sync frame add the shared-vision relation both ways (the game shares vision for equal teams only between AI-typed slots) and keep the bond until the player's connection is lost (then `relations(0)`, "X is gone. I fight for myself now.") or the game ends; not for hire (payments returned), allied with each other and with a bot their player hires (`syncPacts` on `alliedPlayer`). Lobby side: random free slots, pool names, `'n'` follows the owner's team cycles, removed with the owner or by `/botteam 0`; `/botcount` counts the free bots only. `/help` gained the row `/botteam N: N allied bots per player.` (`/botteam N allied bots per player` since the same evening). New `test/botteam.test.js`. Deployed as Server `1d9052e`; **confirmed by the maintainer on the live Fly server the same day ("tested /botteam on the live server. working well.")**.

**27 Sep 2026, maintainer: "in /help commands must be one liners without dot at the end. 'sets the number of bots', 'bots brain'. and in maps selection lobby there must be only two lines in chat: greeting and map selected with the hint as it appears now 'Type /1../7 + ENTER'."** - `Lobby.helpLines` = `/botcount N sets the number of bots` · `/botteam N allied bots per player` · `/bottype krusty|rusher|random bots brain` · `/bothire on|off hire a bot for 1000` (the hall's `/help` rows lost their full stops too); `Hall.headerFor` = two rows, `Welcome to Dark Colony server 2.3.` and `No room selected. Type /1../7 + ENTER.` / `Room <n> (<map>) is selected.` (§17.2 step 7, §17.8), which leaves eight rows for messages (`/rooms` shows all seven rooms). Tests adjusted (`hall`, `botcount`, `botteam`). Not yet seen in the real game.

**27 Sep 2026, maintainer: "Add 1920x1080 resolution to patcher", then "add to documentation that we are adding multiple resolutions for aspect ratios. add 1920x1200 too"** - `gen_apply_script.py` / `Apply-DarkColonyPatches.ps1`, data only otherwise. **Rule change:** the patcher's list may hold several sizes per aspect ratio from now on (22-27 Sep: one per ratio). `HD_MODES` = `1024x768, 1280x1024, 1280x720, 1280x800, 1920x1080, 1920x1200, 3840x1080` - within a ratio the larger size after the smaller, because the window preselects the last recommended entry. The exe patch existed already for both (`patch_resolution.Geometry`: 1792-wide view = 56 tiles, 32 / 36 tile rows with 24 / 16 spare rows to the HUD bottom bar, imul strides, lightmap stride 256, 178 edits per exe); new are the shipped pictures `DC - Council wars/INTRF_HD/1920x1080/` and `1920x1200/` (`INTRG`, `INTRO`, `INTRFACE.GIF` from the Python pipeline) and the fixtures `Dark-Colony-development/hd_sets/1920x1080/`, `1920x1200/`. Patcher regenerated (8 modes): every existing reference hash unchanged; new references Classic `9a24896b…` / Ultimate `2b92968c…` (1920x1080), Classic `dac590be…` / Ultimate `2259f106…` (1920x1200); clean-checkout runs under PowerShell 7 and 5.1 reproduced all four and wrote interface sets identical to the fixtures (64 files compared per size). Not run in the game (the maintainer's own Ultimate was running from the game folder during the session, see the 1920×1080 attempt above: the test exe quit at once as a second exclusive-mode client). `DC16_DISPLAY_AND_RESOLUTION.md` §10.43.

**27 Sep 2026, maintainer: "verify that these resolutions are applied correctly" → "nothing is fine! all screen is screwed up" → "make the game DPI-aware via manifest and test again" → "embed manifest the sme way as icon"** - `tools/patch_icon.py`, fix `icon`, both games. The 1920-wide builds ran correctly (mode set, menu, battlefield, empty `error.log`) but Windows showed the DPI-unaware game's emulated 16-bit surface scaled by the desktop's 150 % and cropped; 1280×800 had only ever fitted because it equals this panel's scaled desktop. Fix: an `RT_MANIFEST` (type 24, id 1, `<dpiAware>true</dpiAware>`, 463 bytes) in the `.dcicon` resource directory that the `icon` fix appends - games only (`--manifest auto`; the map editor's dialogs would shrink), so every game build's reference hash changed (1024×768 Classic `866ad0f5…`, Ultimate `7abb952a…`; 1920×1080 `58522e46…` / `5e296d04…`; 1920×1200 `4b6b3731…` / `a2d7c6bd…`; editor `c72dd205…` unchanged); patcher regenerated, clean-copy rebuilds under PowerShell 7 and 5.1 byte-identical; the 1024×768 exes staged into the game repository's index. Confirmed in game: both games at both new sizes through menu → training → battlefield → click, frame 1:1, `error.log` empty. Rig lessons: a copy on a new path fails its first launch (`Setting to 16 bit mode Failure`, ddex4.c 586) until Windows' `DWM8And16BitMitigation` layer exists for it; the maintainer's own running game makes a second exclusive-mode client quit at once; after the manifest, drive the game with real `SendInput` from a DPI-aware process, not posted messages; a copy with `DCINTRO.AVI` renamed away gets no `movies` fix and no reference hash. `DC16_DISPLAY_AND_RESOLUTION.md` §10.44.

**28 Sep 2026 (night of 27 Sep), maintainer: "comments are messed up in network war"** - investigation on a local relay (`node src/index.js`, defaults) with the 1920x1200 Ultimate build driven from a `subst` copy through MULTI PLAYER WAR → TCP/IP → CONNECT TO SERVER → 127.0.0.1 → CONNECT; nothing changed yet. **Finding: a 1920x1200 client is evicted from the hall before its hall screen has appeared.** The game sends one `VAR` the moment the socket connects (`hall joined` +0.08 s; `dropped ... not allowed in the hall`), which the hall counts as the client's first message (`Hall.tick`: `firstMessageAt` set, so the 5 s `JOIN_TIMEOUT_MS` grace no longer applies and the 3 s `KEEPALIVE_TIMEOUT_MS` clock runs from that message), then sends nothing until its lobby screen (`intrf_hd/multie`) has loaded and its lobby loop starts. That load took **3.0-4.5 s at 1920x1200** (screen black in the per-second captures until then) against **1.5-2.8 s at 1280x800**, so at 1920x1200 the relay's `hall left ... keep-alive timeout` came 3.3 s after the join, before the screen was up: the player sees a black screen for ~4 s and is then back at the main menu (the socket close ends the lobby); with `KEEPALIVE_TIMEOUT_MS=60000` the same client showed the hall (rows `1 Plink (0/7)`..., static header, map list) and stayed. Every other screen transition is slower at 1920x1200 as well (the load appears to scale with the pixel count, 2.25x that of 1280x800); the cause inside the exe is not traced. Fly runs the defaults, so 1920-wide players hit this there. **Fix (28 Sep 2026, maintainer: "fix the relay keep-alive and test"):** in `Hall.onData` and `Room.onData` a batch sets `firstMessageAt` only when it holds a command that is not a `VAR`, so the connect-time CD report leaves the client inside the join grace, and `JOIN_TIMEOUT_MS` is 15 s (was 5 s; a dead socket therefore holds a hall slot or a room seat up to 15 s instead of 5). The room lobby has the same two-phase rule (`Watchdog.tick`), so a direct room join without the hall is covered too; STARTING/RUNNING deadlines unchanged. Tests: `eviction.test.js` (join grace read from the config; new: CD report alone survives `KEEPALIVE_TIMEOUT`+1.5 s, the first keep-alive starts the clock, never past the CD report = dropped at `JOIN_TIMEOUT`), `hall.test.js` (same for the hall). **Game test (28 Sep 2026, local relay with the DEFAULT settings, 1920x1200 Ultimate build from a `subst` copy):** `hall joined` +0.09 s the CD report, hall screen visible 3.6 s after CONNECT, no eviction (the client stayed until the test killed it 39 s later, `socket error: ECONNRESET`), `/1` answered with `Room 1 (Plink - O) is selected.` and the map line `>1 Plink - O jungle (0/7) o`, a chat line `Player3: hello from the test rig` shown in the window, `error.log` empty. 251 tests, lint clean. Still open: the client-side cause of the 3-4.5 s screen load at 1920x1200 (every screen change is that slow there), the maintainer then clarified that the "messed up comments" are the BATTLEFIELD chat at 1920x1200, not the lobby - a separate issue (see the next entry); this eviction is a second, independent defect found on the way. **Committed as Server `8fe92b0`, pushed and deployed to Fly on 28 Sep 2026** (game repo `1a145cc`: patcher, pictures, rebuilt 1024x768 exes). Whether "comments messed up" is this eviction (the chat window is left half-painted when the socket closes mid-dump) or something else in the chat text is not yet confirmed by the maintainer; the hall's chat text itself rendered correctly at 1920x1200 in the captures. Rig: the relay's `LOG_LEVEL=debug` `rx` lines showed the driver's typing landing in the NAME field (`Player2/1hello`) - the chat line `in_text 25` needs a click first; `DC16_DISPLAY_AND_RESOLUTION.md` §10.44 has the DPI notes of the same evening.

**28 Sep 2026, maintainer: "broken is not a lobby chat but battlefield chat!"** (the actual report behind "comments are messed up in network war") - data only: `tools/hud_layout.py` `shift` and the patcher's `Edit-HudScript`. The battlefield chat is two `MAINE` widgets (`in_text 204` at (10,425), `203` at (10,440), filled by the client display `0x40B10D` with ids 0xCB/0xCC) on the last rows of the map view; the HUD tool moved them with the bottom-bar furniture by the whole growth `H − 480`, so at the sizes whose bar absorbs spare rows (1280x720, 1920x1080, 1920x1200) the lower line landed inside the enlarged bar. Now the band 420..453 moves by `dy − slack_y` (1920x1200: 1129 / 1144, view ends 1158). Fixtures for the three sizes, the patcher and the maintainer's game-folder `MAINE` updated; confirmed in a relay battle at 1920x1200 (`SYNC_CHECK=shadow BOT_HIRE=true`: the Mercenary's offer lines on the view's last rows above the bar). READY twice in the room = F36 (client checkbox state), not a relay bug. `DC16_DISPLAY_AND_RESOLUTION.md` §10.45.

**28 Sep 2026, maintainer: "there is a black line right over comment creation line!" → "why the battlefield can't occupy all the space? try option 1"** - experiment, nothing shipped. The black band at 1920×1200 (and 1920×1080, 1280×720) is the bottom bar's filler: the spare rows the view cannot use are given to the bar by repeating its first two rows, which are black. A build with the view height 1168 (36.5 tiles; three exe sites) and a matching HUD hole drew terrain down to row 1157 and left 1158-1177 black: the tile drawer floors the row count from the view height (`>> 5`) and the camera moves in whole tiles, so the partial row is never drawn. Filling it needs a clipped 37th row in the drawer (several hours, scratch buffer + mask); the no-exe alternative is a bar-textured filler or spare rows at the top border. `DC16_DISPLAY_AND_RESOLUTION.md` §10.45 (end).

**28 Sep 2026, maintainer: "fill the bar rows with bevel texture and test"** - `tools/hud_layout.py` `_bevel_plate`: the spare rows above the bottom bar (16 at 1280x720 and 1920x1200, 24 at 1920x1080) are a bevelled plate of the bar's own frame rows (highlight, brushed body, shadow; one colour per row across the bar) instead of the repeated black top rows. Three frames rebuilt (`hd_sets`, the shipped `INTRF_HD\<WxH>\INTRFACE.GIF`, the game folder's active 1920x1200 frame); confirmed in a 1920x1200 battle. Then, on "check other resolutions if they need the same fix": only 3840x1080 shares the spare rows (24) - its shipped frame rebuilt too (rows 1030-1053), its patcher-written `MAINE` already right; 640x480, 1024x768, 1280x800, 1280x1024, 5120x1440 have none. `DC16_DISPLAY_AND_RESOLUTION.md` §10.45 (end).
**28 Sep 2026, maintainer: "investigate, why higher resolutions leads to longer load of every menu?"** - investigation only, nothing changed (F72, `DC16_DISPLAY_AND_RESOLUTION.md` §10.46). The open point of the 28 Sep eviction entry above (the 3-4.5 s screen load at 1920x1200) is traced: every screen load ends in `set_palette` (ddex4.c, CW `0x42F380`), which converts the 256 palette entries to 16-bit pixels by drawing each one into the back buffer with GDI and reading it back through a whole-surface `Lock` - `GetDC` → `SetPixel` → `ReleaseDC` → `Lock` → read → `Unlock` per entry. Measured with a main-thread stack sampler on the Ultimate build (three screen changes per size): `set_palette` 1.41-1.53 s per screen at 1024x768, 3.19-3.54 s at 1920x1200, all of it in `ReleaseDC` (2.8 / 6.2 ms per entry) and `Unlock` (2.8 / 6.1 ms), which on Windows 11's D3D9-emulated DirectDraw each write the whole W×H×2 surface back; the rest of a screen change (script, GIF, files) is under 0.2 s. The value it reads back is plain truncation of the RGB bytes (all 256 LUT words of the running game match `(r>>3)<<11 | (g>>2)<<5 | b>>3` and the function's own `make_colour` tables), so a ~40-byte arithmetic rewrite of the loop body (Classic `0x42F4C7..0x42F538`, Ultimate +0x60, 4 `.reloc` entries to type 0, after `ddraw`) would remove 1.4 s (1024x768) to 3.3 s (1920x1200) from every screen change and twice that from start-up - proposed, not built. Rig: `subst V:` on the game folder with a per-path `DWM8And16BitMitigation` layer entry, a 1024x768 patcher build in a scratch copy without `AVI\`, `SendInput` from a DPI-aware process, `Wow64GetThreadContext` sampling with return-address-slot attribution (details in §10.46). **Then, the same day, maintainer: "implement the fix for both exes and update the patcher"** - fix `palette`, `tools/patch_palette.py` (verify / plan / apply, pattern-located, both exes): the 135-byte loop body becomes 58 bytes of shifts and ors that build the 5-6-5 (or 5-5-5) word from the RGB bytes and store it, the 24-byte Unlock block a jump to the next entry, 5 `.reloc` entries type 0; generator + `Apply-DarkColonyPatches.ps1` regenerated (step after `ddraw`, 7 edits per exe), 1024x768 references now Classic `2357ca77…` / Ultimate `fa03709d…` (= the previously committed exes + exactly this tool's bytes; reproduced under PowerShell 7 and 5.1), the 1024x768 exes staged from the scratch build, the game folder's 1920x1200 builds given the fix with the tool (`3367fb53…` / `65ce242f…`). Confirmed in game at 1920x1200, both games: the 256 LUT words of the patched game are identical to the unpatched read-back, the NEXT → briefing screen change takes 0.4-0.6 s instead of 5.1 s (ACADEMY 0.5 s instead of 1.7 s), `error.log` empty. For the relay this means the 1920-wide client's black screen before the hall (F71) shrinks from 3-4.5 s to under a second; the 15 s join grace stays. Not run: 640x480, the battle load, RGB555 displays.

**28 Sep 2026, maintainer: "main menu items must initiate in different order. first must be 'DC' logo, second 'DARK COLONY' logo, then buttons, then credentials. suggest use best practices for initiating game main screen"** - new exe fix **`menuorder`** (`tools/patch_menu_order.py`, both games, `Requires nocd`, patcher order after `music`; F73; `DC16_DISPLAY_AND_RESOLUTION.md` §10.47). The stock menu init ran the blocking button wave, then the logo, then the title (at logo frame 10) and painted the credits from the loop; now three in-place blocks of main.c `bintro` (194 bytes: the dead CD-less greying calls and the loop's frame-10 check give the room, no `.reloc` change) run logo → title (when the logo has finished) → wave (at the title's second frame; plates top to bottom, labels as they settle) → credits (loop's first pass). The script's `anim_oneoff` first plate is parked (mode 2) before the first pump and started before `run_banims`, so no interface file changes and the fixtures are untouched. First build parked it with mode 0 = `anim_loop` and the plate looped under the logo (the anim modes are 0 loop / 1 one-shot / 2 stopped=finished, not 0 stopped as §10.40 had read them) - found with a 17-19 Hz per-region capture (`menuseq.py`), fixed, re-measured: Ultimate 1920x1200 logo +0.44..+0.97 s after the menu background, title +1.02..+1.14, wave +1.09..+2.49, credits from +2.49; Classic the same shape; `error.log` empty. Patcher regenerated (`blocks_menuorder`), published 1024x768 builds Classic `86a06e69…`, Ultimate `95da6d53…` (tool chain = patcher byte for byte, PowerShell 5.1 = 7); the maintainer's game-folder 1920x1200 builds rebuilt; `dc16.asm` / `dcexp16.asm` regenerated. Not done: click-to-skip during the cascade; not run at 640x480 or the other HD sizes (size-independent code). **Committed and pushed 28 Sep 2026: Dark-Colony `34dfee6`, Server `8bbfe8c`.**


**28 Sep 2026, maintainer: "comments on the multiplayer battlefield must play the same sound as is playing for comments in single mode missions. on the battlefield we must have six lines of sent comments"** - new exe fix **`chat`** (`tools/patch_chat.py`, both games, `Requires palette`, patcher order after `menuorder`; F74; `DC16_DISPLAY_AND_RESOLUTION.md` §10.48). The client's chat display (`0x40B10D`, 276 bytes) is rewritten in place: up to six lines (`MAINE` `in_text 203`, `204`, `207..210`, each drawn only if the widget exists), the newest index capped at 5, and a helper that plays sound 187 (`SOUND\MSG.WAV`, the mission-message sound) whenever the chat handler has left a "new line" marker (`0x7FFFFFFF` in the ring index, written by a 17-byte stub the handler's `inc` now calls); stub and helper use 51 of the 75 NOP bytes fix `palette` left in `set_palette`. Identical bytes in both builds, no `.reloc` change. Data: `hud_layout.py maine` and the patcher's `Add-ChatLines` add `in_text 207..210` (15 rows apart above the two stock lines) to `INTRF_HD/MAINE`; six fixtures and the game folder's 1920x1200 `MAINE` updated; the stock 640x480 `MAINE` keeps two lines (the exe then shows two, with the sound). Patcher regenerated: 1024x768 references Classic `bd86e63e…` / Ultimate `5acee874…` (= the committed `palette` exes + the tool's bytes; pwsh 7 = PowerShell 5.1 = tool chain on a clean checkout, `MAINE` = fixtures), staged; the game folder's 1920x1200 builds `9d2c60a5…` / `2dec194f…`. **Game test on a local relay** (`BOT_HIRE=true SYNC_CHECK=shadow LOG_LEVEL=debug`, Ultimate 1920x1200 from `subst W:`): rig `battle_chat.py` + `chatter.mjs` (a `FakeClient` in room 1, `readyPolicy follow`, eight chat lines 2.5 s apart in battle) - captures show six lines at once (lines 3-8), oldest on top, one leaving every 7.5 s; a music-free copy with a pycaw peak meter shows eight 0.33 s bursts 0.2-0.7 s after the eight sends; `error.log` empty; **the maintainer confirmed: "audio of chat message is ok. all 6 lines of chat are visible ok"**. Relay unchanged. Rig notes: the hall's `/N` join is also logged as `READY status 2` (match `"state":"LOBBY"` for the in-room READY); the fake client's `mready` reports game player 7, so shadow mode logs `shuffle mismatch … engine disabled, bots idle` when a `FakeClient` sits in a real client's battle (rig artefact). `dc16.asm` / `dcexp16.asm` regenerated. **Committed and pushed 28 Sep 2026: Dark-Colony `6bad173` (patcher, both 1024x768 exes, `INTRF_HD/MAINE`, README, HOWTO), Server `27ace43`.**

**28 Sep 2026, maintainer: "battlefield interface and menus styles are bad. You must create the same style as in race selection, network lobby and other menus", then "use original sprites for buildings, units and upgrades", then "keep the icons and portraits, red-outlined button icons which mimics lobby button style everywhere"** - data only, no exe byte (F75; `DC16_DISPLAY_AND_RESOLUTION.md` §10.49). Then, on the first in-game result: "third tab icons are absolutely unacceptable. first of all they are messy. secondly, they do not obey lobby style. and frames must be in lobby style. lobby is not black, it is in different intensities of gray." New tool `tools/hud_console.py`, final form: the HUD frame rendered from geometry at every patcher size as the lobby's grey pipework (seeded generator: 11/23/35/43-grey compartments, vents, rounded tubes, light 107-grey edge lines over the panel column, the bar and the borders) with black read-out screens (minimap, the tab row + button grid, status, DAYS, money, dial, message box, DARK COLONY strip) and the BUILD lettering from `BUTTON.SPR`; a 133-cell `INTRF_HD/MAINBUT.SPR` with every button on the lobby's `KNOBE.SPR` red ring - the unit / building / upgrade portraits of `MAINBUT.SPR` kept pixel for pixel, 21 clean red-outline icons drawn as vector shapes for the order / option buttons (quit, save, options, allies, pause, objectives, stop, move, attack, waypoints, deploys, napalm, disease, steal, inspire, drop ship, saucer), `KNOBE`'s own arrow glyphs, tab strips with the lit tab in the lobby's green grid, PAUSED panel). Third round ("up/down buttons of battlefield chat are skewed. day/night clock is old on top of new one ... day and night parts are indistinguishable. previous style of first tab was ok, return them back and create the same style buttons for third tab. battlefield menus are still black"): `BUTTON.SPR`'s neon icons are the approved style - back on the twelve cells it covers, the nine others (save, allies, pause, objectives, crosshair, inspire, drop ship, saucer, shovel) drawn in its idiom; symmetric outline triangles centred in the button rects for every arrow; the 36 hand cells of `sprites/cloc` turned out to be the whole metal dial blitted over the frame, so **fix `clock` gained a third edit** (DGROUP `sprites/cloc` -> `sprites/clock`, `patch_clock.py`) and the patched exes read the redrawn `SPRITES/CLOCK.SPR` (day = light right half with a sun, night = dark left half with a moon, red hand) - every game build's hash changed (1024x768 Classic `88f59c1d…`, Ultimate `63ccbe88…`; 1920x1200 `c7505258…` / `cdac7ca1…`); dialog rows filled with the grey pipework. Fourth round ("inactive tab buttons must be gray. active button must be red. build button must have red text. third tab buttons must be the same style as action buttons for units!"): grey inactive / red active tab plates, red BUILD lettering, and the Game Option tab (plus crosshair, inspire, drop ship, saucer) built in BUTTON's unit-action layout - the common pixels of its ten action cells (hot-key box + circuit line) on the lobby plate, the shipped hot-keys (O Q D F11 J ESC, return arrow) in light grey, the cyan neon icon beside. Fifth round ("BUILD button font must be the same as in lobby. day/night clock is not appearing instantly on game start"): BUILD in `MFONTO5` with the lobby's caption remap (86/221/99/100/244), the dial's first cell painted into the frame under the bezel because `clock_draw` blits only on an index change. Sixth round ("BUILD button font must be just red! third tab, images on the buttons must be in different colors similar to unit command buttons"): BUILD plain red (`CAPTION_RED`), one neon hue per drawn icon (`NEON_RAMPS`: quit orange, save green, options cyan, allies yellow, pause red, objectives blue). Verified clean-copy under pwsh 7 and 5.1 (exes = references, sets = fixtures); confirmed in game. **Committed and pushed 28 Sep 2026: Dark-Colony `a7be8d3`, Server `ae16ab8`** (later rounds - grey frames everywhere, hot-key boxes, the diplomacy header and row plates - are in the same commits) and `INTRF_HD/POPP.SPR` (the options / quit / save / objectives plates as conduit rows); `INTRF_HD/MAINE` says `pictures intrf_hd/mainbut` with the tab strips as 124×16 pictures at the panel's left edge, the four dialogs and the three `lopte` copies say `pictures intrf_hd/popp`. Patcher: `Edit-HudScript` tab rule, `Set-BackgroundHd` also retargets `pictures intrface/(mainbut|popp)` (and is applied to the sub-window dialogs, which it had skipped), the two banks are required data, the seven shipped `INTRF_HD\<WxH>\INTRFACE.GIF` rebuilt; six `hd_sets` fixtures updated (and the stale 1024x768 `exp/intrf_hd/introe` refreshed); verified on a clean copy under pwsh 7 (1024x768) and PowerShell 5.1 (1920x1200): exe hashes unchanged, sets = fixtures. Confirmed in a 1920x1200 training battle (Classic build): HUD, Game Option tab, Options and Objectives dialogs, `error.log` empty; the cyan glyphs show in the team colour (the sprite blitter's team remap of ramp 128..143). Relay unaffected. 640x480 builds keep the metal HUD (stock scripts).

**28 Sep 2026, maintainer: "patcher is broken. all graphical modes (except 1920x1200) are showing picture in left upper corner"** - F76, `tools/patch_icon.py` (fix `icon`, both games), `DC16_DISPLAY_AND_RESOLUTION.md` §10.50. Not the patcher's edits or data: the maintainer's game folder held the 1280x800 patcher builds, byte-identical to the references, with a matching set, and a 1024x768 build without the `icon` fix filled the screen. The 27 Sep manifest (`<dpiAware>true</dpiAware>`) makes the process SYSTEM-DPI-aware, and the mode switch changes the monitor's DPI (96 at 1024x768 / 1280x720 / 1280x800, 120 at 1280x1024, 144 at the 1920-wide modes on this 150 % panel), so the DWM bitmap-scaled the game window - and the 16-bit surface the mitigation layer presents through it - by 96/144: a 683x512 picture in the corner at 1024x768, 854x533 at 1280x800; the two 1920-wide modes the 27 Sep test used keep 144 and were never affected. Fix: the manifest declares per-monitor awareness (`dpiAwareness PerMonitorV2, PerMonitor`, `dpiAware true/pm` fallback; 589 bytes, exe sizes unchanged) - a per-monitor-aware process is never scaled by Windows; the game receives `WM_DPICHANGED` it never reads. Generator/README/HOWTO texts updated, patcher regenerated: every game reference changed (1024x768 Classic `928bb8d0…` / Ultimate `98fe5e95…`, 1280x800 `bc4c0537…` / `e7392a4d…`, 1920x1200 `7abeb48b…` / `58265655…`, full list in §10.50; editor unchanged), PowerShell 7 = 5.1, the 1024x768 exes staged from a scratch build, the maintainer's game folder rebuilt at 1280x800 with the new patcher, `dc16.asm` / `dcexp16.asm` regenerated. **Confirmed in game** from a `subst X:` copy (probe: display mode, window rect, `GetDpiForWindow`, DPI-aware screenshots, real click): the per-monitor builds fill the screen at 1024x768, 1280x800, 1920x1080 and 1920x1200 (Classic) and 1280x800 (Ultimate), TRAINING / ACADEMY clicks open the name screen; the final patcher outputs re-run at 1024x768 (scratch) and 1280x800 (game folder), `error.log` empty. Not run: 640x480, 1280x720, 1280x1024, 3840x1080, a battle. Relay unaffected. Lesson: a display fix tested only at the desktop's own size proves nothing about the switched modes; and the three DPI-awareness levels are not interchangeable for a program that switches modes.

**29 Sep 2026, maintainer: "MULTI PLAYER WAR set to third button from the top right column. ENCYCLOPEDIA set as fourth from the top on right column. first button on the right column add ONLINE WAR, it must lead to the form of room selection based on LOAD GAME form and it must contain maps from relay server. relay server name must be located in separate file DEFAULT_SERVER.TXT ... Connection must happen to 8889 port which must use standard ssh encryption. When map is selected and entering the room, relay must select free slot for the client" → "use TLS, ultimate only, row 2 empty (reserved for future implementation of replay), one row per room ... [map name]+[terrain]+[slot count]+[clients count]+[bot count]+[status]"** - §20, F77-F80, protocol doc §4.4 / §6.9, `DC16_DISPLAY_AND_RESOLUTION.md` §10.51. Relay: five messages `0x50 LIST` / `0x51 ROOMS` / `0x52 ENTER` / `0x53 REFUSED` / `0x54 ENTERING` (`commands.js`, `online.js` formats the 56-column rows), the hall answers LIST with the table and re-sends it on every change instead of lobby rows, ENTER seats the client in a random seatable slot chosen at that moment, sends ENTERING, resets both sequence counters and both join clocks (`firstMessageAt`, `joinedAt` - the first build evicted the game 3 s after joining) and runs the stock `'d'` join; `TLS_PORT`/`TLS_CERT`/`TLS_KEY` for a self-hosted relay's own TLS listener; on Fly port 8889 is now `handlers = ["tls"]` (the proxy terminates TLS with the `*.fly.dev` Let's Encrypt certificate and forwards to 8888 - `openssl s_client` TLS 1.3, verify ok; the stock game's plain fallback to 8889 no longer works there, 8888 is unchanged). `fakeclient.js --online [--tls]` speaks the dialogue; 8 new tests (259). **Deployed to Fly 29 Sep 2026** (machine version 243). Exe: fix `online` (`patch_online.py` + the C module `tools/online/online.c`, Ultimate only, last in the build after `icon`), menu layout `OZI_COLUMNS` right column `8, -, 3, 5, -, -, 12` with plate 8 renumbered to 24; details, hashes (Ultimate 1024x768 `d55b1689…`, 640x480 `ba41dc2c…`; `5b9efed6…` / `1ddb37b0…` were the first form of the same day, before the maintainer's screen corrections: title centred, a "Server: host:port" line above the state line, both in a grey frame drawn into `ONLINEBG.GIF`) and the game tests in §10.51 - confirmed against a local relay (lobby, READY, battle through the proxy) and against Fly over TLS (room 1, slot 1). Lessons for the relay: the game's stream through the exe's proxy is a fresh connection for the game but not for the relay (counters and clocks must restart at ENTERING); a `LIST` client must never get lobby rows (the module ignores them, but the refresh would waste frames). Known limitation: with the Fly proxy terminating TLS, the relay logs the proxy's internal address (`172.16.x.x`) for 8889 clients, not the player's - Fly's `proxy_proto` handler would carry it, but the relay would have to strip a PROXY header first (not done).

**29 Sep 2026 (evening), maintainer: "when using 'online war' connecting to a selected lobby, often I have 'connection lost'"** - F81, protocol doc §4.4 / §6.9, `DC16_DISPLAY_AND_RESOLUTION.md` §10.51. The Fly log had it: `client left ... reason: "sequence 12, expected 0"` (and `sequence 4`) 60-150 ms after `online -> room`, in two of the day's four ONLINE WAR joins. The module kept its 700 ms keep-alives going until it had read `ENTERING`, one round trip after `ENTER`, while `enter()` had already reset the counter for the game's stream (F80) - the keep-alive arrived as the game's first frame with the module's sequence number. Fixed on both sides: `online.c` sends nothing between `ENTER` and the answer (`entering` flag; a second ENTER click is ignored; `REFUSED` re-arms the keep-alives), rebuilt with `build.cmd --embed` (11 810 bytes of code and data, section size unchanged; `patch_online.py` re-embedded, patcher regenerated, **Ultimate 1024x768 now `d970597f…`**, pwsh 7 = 5.1 = generator reference; Dark Colony `928bb8d0…` and the editor unchanged; `dcexp16.asm` regenerated); the relay marks the client at `enter()` (`client.handover`) and `readCommands` drops module-only frames (keep-alive, `LIST`, `ENTER`) without a sequence check until the game's first frame - also a straggler that happens to carry sequence 0, which would have made the game's real frame 0 a "duplicate" - and `hall.onData` drops stragglers that sit behind `ENTER` in the same chunk instead of forwarding them to the room and restarting the keep-alive clock (a second, unseen failure mode: the 3 s deadline instead of the 15 s join grace). Two tests (261), `isModuleFrame` exported from `client.js`. **Confirmed in game** from a `subst X:` scratch copy (DPI-aware `online_test.py` driver): a fake relay (`fakerelay.py`, plain 8888) that holds `ENTERING` for 3 s logged 4 keep-alives after `ENTER` from the old exe (`d55b1689…`, seq 12..15) and none from the rebuilt one; the rebuilt exe against the new local relay: `online -> room`, `client joined` room 1 slot 2, 18 s in the lobby, no eviction, `error.log` empty. The relay fix alone repairs the deployed situation for players with the old exe (their stragglers are dropped); **committed and pushed 29 Sep 2026: Dark-Colony `4c85153`, Server `5e2eaeb`; deployed to Fly the same evening; the maintainer then joined a room on Fly with the rebuilt exe: "tested on fly, works now"**. Lesson: a hand-over between two protocols on one stream needs a quiet period on the sending side AND tolerance on the receiving side - the round trip between "I sent ENTER" and "I read ENTERING" is exactly where the old side's periodic traffic keeps arriving.

**29 Sep 2026 (evening), maintainer: "music is lagging periodically. something wrong with playing music", then "i see where is the problem. music rip is in very bad condition! redo rip and validate that it has no glitches!"** - data only, no exe or relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.52; new tool `tools/rip_music.py` (`scan` / `rip` / `check`). The MP3 player in the exes was cleared first: a replica of its MCI calls recorded through a WASAPI loopback capture for 40 s had no gap of 20 ms or more and 0.1 ms status polls. The shipped MP3s decode to the image PCM within normal coding noise (SNR 17-30 dB), so the 22 Sep encode was right and the material wrong: **every Dark Colony track has runs of 8-12 sectors (100-160 ms) that are exact copies of the audio 92 sectors (1.23 s) later - 8 % of each track, one run every 1-3 s**; the ripping drive served its read-ahead cache for those addresses and the original audio is not in `Dark Colony.bin` (the early copy is the stray one, its junctions break the waveform, the late copy is seamless; deleting it does not restore continuity). The Council Wars image has no duplicated sector and no fixed-phase splice. Also fixed: pregap junk at the old slice starts (Dark Colony track 2 began with 50 ms of full-scale noise, track 3 with a 180 ms fragment, Council Wars track 2 with a click - all followed by digital silence, all heard at every loop start). The tool trims each track to the end of the last >= 20 ms silence inside its first second, encodes with the same LAME settings and verifies every file by decoding it against its PCM. Game folder: `exp\music\track02-05.mp3` re-ripped and clean (all checks pass; `track04.mp3` byte-identical to the old file), `MUSIC\TRACK02-05.MP3` re-ripped with the junk gone but the stray blocks still in (the tool exits 1 for that image). The patcher lists the files by name, no regeneration. **Open: the Dark Colony disc must be ripped again with a secure ripper (EAC secure mode, cdparanoia, `cdrdao --paranoia-mode 3`); `rip_music.py scan` must report no stray blocks and no splices before the set is rebuilt.** Lesson: align the encoded files with the raw PCM first - it decides encoder vs. material in one pass - and treat a raw `.bin` as unvalidated audio until a duplicated-sector scan has run. **Later the same evening:** the maintainer downloaded both soundtracks (eight VBR 48 kHz MP3s, YouTube uploads) into `Documents\DC - soundtracks` and asked for them to be copied without re-encoding; matched to the CD tracks by cross-correlation (uploader's DC "Track 1-4" = CD 2, 3, 5, 4; CW "Track 5-8" = CD 4, 2, 3, 5) and copied under the game names - these are the shipped `MUSIC\` and `exp\music\` files now (frame streams intact, no clipping; confirmed by the maintainer: "music works now"). The re-ripped image sets are superseded. Committed and pushed 29 Sep 2026.

**30 Sep 2026, maintainer: "battlefield skin of forms are not good. text boxes must have gray frame. scroll bar must have appropriate frame. additionally write a clear and descriptive guidelines for drawing an interface elements"** - data only, no exe or relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.53; new **`docs/DC16_INTERFACE_STYLE_GUIDE.md`** (palette, the frame tube 11 | 35 | 107 | 35, plates, pipework, dialog anatomy with the fixed 304-px row layout, text boxes, scroll channels, text, icons, process, don'ts). `hud_console.build_popp` redraws the dialog rows: the panel border is the lobby tube, the list rows carry the list window in its frame (x 6..269) and a framed scroll channel (x 272..297) for UP / bar / DOWN, and two new cells are framed text boxes (14 = value box 78x24, 15 = name box 264x24). `hud_console.console_dialog` lays the four dialog scripts out on them from the row positions (list x row + 10 / y top + 4, arrows x row + 277 at top + 5 / bottom - 5, bar x row + 280; a value box picture over every "-" / "+" pair, a name box over the save-name field), regenerating the box pictures on every pass so the tool chain and the patcher (`Edit-DialogConsole`, byte-identical under pwsh 7 and 5.1, run after the letterbox shift and after `Edit-MusicDialog`) agree in any order; `patch_music.write_dialogs` runs the pass after `music_row`. Regenerated: `INTRF_HD/POPP.SPR`, the three 1024x768 dialog scripts + the three `lopte` copies, the six fixtures, the patcher; exe hashes unchanged (`928bb8d0…` / `d970597f…`). **Confirmed in game** at 1024x768 (Classic training battle: save, options, objectives and quit dialogs; `error.log` empty). Not run: the Ultimate copy with the MUSIC row, other sizes.

**30 Sep 2026, maintainer: "refactor style of battlefield 'options' menu using best practices taken from pre battle menus"** - data only, no exe or relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.54, style guide §4 / §6 / §7 / §9 amended. The OPTIONS dialog now follows `MULTIE` / `LOADGE` / `NEWGAMEE`: rows 3..last are one framed panel (POPP cells 16 / 17 / 18), the title a `MFONTO2` label centred in a header box (cell 19), every option a plain `remap 4` label with the value between KNOBE's ringed 14x14 arrows (cells 21 / 22 = KNOBE 14 / 16), CANCEL / OK as 90x26 text buttons (cell 20 = KNOBE 2, `label centre`, CANCEL left / OK right), `bright_pushed 8` / `bright_highlight 4`, `MFONTO5` body font; the red title and label plates and the §10.53 value boxes are gone. `hud_console.console_dialog` picks the form when the script has "-" / "+" pairs, derives every position from the rows and stays order-independent with `music_row` (the Ultimate copies get five options); the patcher's `Edit-DialogConsole` is the byte-identical port (pwsh 7 = 5.1 on eight inputs). Pitfall: a new constant shadowed the HUD geometry's `PANEL_X` and the frame renderer drew the panel column at x 6 - caught by the fixtures' `INTRFACE.GIF` hashes; renamed. Regenerated: `INTRF_HD/POPP.SPR` (23 cells), `INTRF_HD/LOPTE` + the three `lopte` copies, the six fixtures (frames unchanged), the patcher; exe hashes unchanged. **Confirmed in game** at 1024x768 (Classic training battle: layout, hover brightness on CANCEL, `>` stepped GAME SPEED to 110 %, CANCEL and OK closed the dialog, `error.log` empty). Not run: the Ultimate copy, other sizes. Follow-up: the same form for the save / objectives / quit dialogs. **Third round the same day (maintainer: "every element must have gray frame. all menus have some gray unclickable elements and lines between clickable elements"):** every option is a capsule strip (POPP cell 23, 282x24) - the lobby's 3-px `23 | 65 | 23` element frames with rounded outer ends around label, "<", value and ">", joined by grey bars - `capsule()` / `option_strip()`, positions x 27 / 161 / 188 / 265 on y0 + 63 + 32 k; second name collision (`STRIP_CELL` = MAINBUT's diplomacy strip) caught by the `MAINBUT.SPR` hash and renamed; confirmed in game (fresh battle), port equal under both shells, fixtures and patcher regenerated. **Fourth round ("too early to push anything! ... some frames are not closed! check battlefield main interface and your 'options' interface"):** the HUD's pipework compartments ran under the border tubes and the screens' rings (open sides), and the capsules' rounded ends were loose arcs - `Pipework.region(..., holes)` now lays the compartments around every screen, plate and border tube, borders are drawn last, the DAYS label got its own screen, `capsule()` bends the outline itself; every size's `INTRFACE.GIF` regenerated (shipped pictures, fixtures, game folder); confirmed in game at 1024x768; not pushed pending review. **Fifth round ("battle interface tab buttons are braking interface frame? 'OPTIONS' text does not have a frame at all"):** tab strips 120 px (plates to x 634 stock, off the border tube; `MAINE` `120 16`, old `124 16` rewritten), the title label's rect had erased its own box (`bg textbg` paints the rect black) - header box 200x32 centred at row + 52, label rect = the box interior; confirmed in game; uncommitted. **Sixth round ("increase height and width of 'OPTIONS' frame ... fill all space between form border and actual options frame"; "tab button '1' are braking interface frame. chat entering frame is broken too. chat scrolling buttons must be centered"):** header box 292x44 at row + 6 / y0 + 4 over blank rows (cells 25 / 24); tab plates 36x14 inset 2 px; message screen interior H-20..H-8 with the bar texts 2 px higher (`CHAT_LINE_Y`); the bar arrows are transparent triangle cells centred in one capsule channel drawn into the frame; `ROW_KINDS` bug (rows with cells 16..25 were not rows to the pass) fixed; confirmed in game; uncommitted. **Seventh round ("you didn't fix the problem with '1' tab frame! and now 'BUILD' button lost left frame!"; "'DAYS' frame is too tight and days count is not horizontally centred"):** the panel content stands on the map's edge tube (BUILD at x 900, screens at 902, tube 900..902 at 1024x768) - the view walls are drawn before the panel again as BAND | LT | dark so plates keep their rings and screen rings coincide; the tab strip is transparent outside its plates; BUILD plate 81 px in its 86-px rect, DAYS screen 601..632 x 420..441 above the dial's bezel, count at stock (607, 429); confirmed in game; uncommitted. **Eighth round ("'DAYS' and days count must not overlap each other"):** the MFONTO7 caption (10 rows) + the count (10 rows, a 12-row widget rect) cannot share the 22-row interior - the caption is now the frame's 5x7 pixel lettering (`caption_5x7`) on rows 420..426, the count at (607, 430) with its rect ending on the interior's last row, the bottom ring intact; confirmed in game; uncommitted. **Ninth round ("apply the same style to save, objectives and quit dialogs"):** every dialog is a form - header box + MFONTO2 title over blank rows, red plates dropped, text buttons (captions in a new font 2 = MFONTO5; OK x 158 / CANCEL x 56, a lone OK x 107, the quit dialog's two 180x26 plates = POPP cell 26 at x 62), the list frames kept; font 0 switched only for the options form (the objectives text overflowed otherwise); the header block re-inserted canonically; confirmed in game; uncommitted.

**30 Sep 2026, maintainer: '"save messages", "objectives", "really quit" must have black background'** - data only, no exe or relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.55, style guide §6 / §7 / §8 / §13. The three list dialogs still had pipework rows between header, list and buttons; now rows 3..last of EVERY dialog are the options form's black panel (POPP cells 16 / 17 / 18) and the list block is a compartment of it - the list and scroll-channel frames share the panel's side tubes, 2 px of black between them, full-width dividers top and bottom (cell 3 = list top at the panel's top, new cell 27 = list top inside the panel, 4, 5); the save name's box (cell 15) is 280x24 inside the panel; POPP.SPR 28 cells. `hud_console._list_form` and the patcher's `Edit-DialogConsole` derive the list rows from the `list` widget's y range - the STOCK save dialog frames its name field with two list cells, which the first form took for the list's top; found by the clean-copy patcher run (the Python fixtures and the harness inputs were all already laid out), the harness now runs the four stock scripts too (18 inputs, pwsh 7 = 5.1 = Python). Regenerated: the game folder's 1920x1200 `POPP.SPR` + 3 dialogs (frame, banks, MAINE, `lopte` copies byte-identical), the six fixtures (3 dialogs each), the patcher; exe hashes unchanged. **Confirmed in game** at 1920x1200 (Classic training battle: F11 save, objectives, quit, options - all on black, every button closes its dialog, `error.log` empty). Not run: Ultimate, other sizes. Committed and pushed: Dark-Colony `5d35735`, Server `410ecb2`. **1 Oct 2026 ("'really quit?' dialog buttons must be better centered in frame"):** the two 180x26 plates are centred vertically in the panel's interior (row 3 + 23 and + 71, 22 px apart, 19 px of black above and below) in both ports; `LQCE` regenerated (game folder, fixtures, patcher); measured in game at 1920x1200. Then "do the same for save and objectives dialogs": OK / CANCEL centred between the list block's bottom divider and the panel's bottom tube (bottom row + 29, 13 px of black above and below); `LSGE` / `LOBJE` regenerated everywhere; measured in game. Then "'objectives' is missing bottom part frame of form. '1' tab when clicking tab '2' or '3' is deleting left part of interface frame": the stock LOBJE `size` (272 for 18 rows) hid its last row - the pass grows `size` to the last row; the tab-strip cells carry the view's two wall columns because the engine erases a picture's rect before a repaint; `MAINBUT.SPR` + `LOBJE` regenerated (game folder, fixtures, patcher), confirmed in game at 1920x1200.

**1 Oct 2026, maintainer: "pre battle dialogs on higher resolutions than original are on black background. I need you to create realistic picture based on main menu background which can be used as a background for all prebattle forms including main menu"** - data only, no exe or relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.56, style guide §11. `paint_intro.paint` renders the main-menu scene realistically on the stock geometry (same circle, lit depth, glow extent, star density, so every layout rule measured on it still holds): a sun-lit sphere with a bump-mapped, cratered Mars surface, a terminator envelope that keeps the DC logo rows black (`crescent_tail_row` checks it per size), dust haze on the lit rim and outside the limb, magnitude-distributed tinted stars, ordered dither over the planet only. The scene is written three times per size - INTRG.GIF / INTRO.GIF with their bottom bands and the new **BACKDROP.GIF** without one - and `pad_background.pad_gif` lays every letterboxed pre-battle screen on BACKDROP.GIF in a grey panel frame (`35 | 107 | 35`, 2 px ground), remapped into the screen's own palette through a 256-entry nearest-colour table. Four shipped pictures per size now (`INTRF_HD/<WxH>/BACKDROP.GIF` added for all seven sizes); the patcher's `DcGif.Pad` gained the backdrop form (byte-identical to the Python tool under pwsh 7 and 5.1), `Write-InterfaceSet` requires and passes it. Regenerated: shipped pictures, the six fixtures' GIFs, the game folder's 1920x1200 set, the patcher. **Confirmed in game** at 1920x1200 in both builds (main menu, LOAD GAME, race selection, network screen; `error.log` empty). **Patcher checked on a clean `git archive HEAD` copy (plus the working tree's patcher and shipped pictures) under pwsh 7 and PowerShell 5.1 at 1024x768 and 1920x1200:** every GIF and script of INTRF_HD / exp\intrf_hd equals the fixture (57 of 57 comparable files; HSCENE/GSCENE differ only by the DC ending names, as the fixtures README says), exe hashes unchanged and byte-identical to the published builds (Classic `928bb8d0…`, Ultimate `d970597f…`, editor `c72dd205…`). Not run: other sizes in game, the lobby / story / victory screens, 640x480 (stock files). **Same day, the maintainer: "planet is looking like an asteroid. Can you please take a real Martian topology and wrap on the sphere?"** - `DC16_DISPLAY_AND_RESOLUTION.md` §10.57: the surface is now the real one - MOLA MEGDR heights (16 ppd) and the USGS Viking colourised mosaic, downloaded with permission and derived by the new `tools/mars_maps.py` into `tools/mars/` (4096x2048 16-bit height PNG + colour JPEG, 13.7 MB, public domain), wrapped on the sphere oriented (`MARS_ROLL` −83, `MARS_TILT` 174, `MARS_LON0` 179, found by a brute-force search) so that Valles Marineris climbs from the night side across the lit band and over the horizon at ~50° to it (maintainer: "show side of mars containing Mariner valley", then "... 75 degrees angle to horizon", then "50 degrees instead of 75"), relief ×2.5 and `BODY_GAIN` 2.6 instead of ×6 / 3.2 ("glows as a mirror. lights and shading right now are too aggressive"), then sun-facing slopes capped at +0.10 n·L over flat ground and the slope maps blurred (`SLOPE_LIGHT_CAP`, `SLOPE_SMOOTH`; "edges of obstacles are too bright"); the Lambert steepening eased (`LAMBERT_POWER` 1.6 → 1.3) so the inner band reads - the envelope stays, a softer one put lit canyon walls past the DC logo limit at 1024x768, relief exaggerated 6x for the bump shading, the mosaic's brightness tinted with the stock orange (`MARS_TINT`; maintainer: "make it warmer, more orange like before"); everything else of §10.56 unchanged; shipped pictures, fixtures and the game folder regenerated, patcher untouched; confirmed in game at 1920x1200.

**1 Oct 2026, maintainer: "let's update patcher. use best approaches for installer building. combine all patches related to resolution change into one single patch. return back patch for 150% game speed by default. don't select any resolution by default. force the client to select resolution once at the beginning, mark 640x480 as (original). mark patching 'Dark Colony' executable as deprecated and disabled and skipped by default"** - patcher only (`gen_apply_script.py` → `Apply-DarkColonyPatches.ps1`), no relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.58. The three display fixes `resolution` / `hdpaths` / `clock` are ONE fix `resolution` per HD size (the generator replays the three tools consecutively and attributes one diff; every fix but `speed` still reproduces the previous published exes byte for byte, so the merge changed nothing), fix `speed` (`patch_speed.py --percent 150`, F11) is back after `pool`, the resolution has no default any more (`-Resolution` is required for the games, the window asks on its first page with radio buttons and nothing preselected, `640x480 (original)`), and the Classic build is deprecated: listed last, unticked and greyed in the window, skipped by `-All` unless `-IncludeDeprecated`, still shipped. The window is a classical installer: Welcome → Screen resolution → one page per executable → Ready to patch (summary) → Finished. Published 1024x768 builds: Ultimate **`bc751e3b…`**, Classic **`5f85cee6…`**, editor `de8076dc…` unchanged; `dc16.asm` / `dcexp16.asm` regenerated. Tested headlessly under pwsh 7 and 5.1 and on the command line on a clean copy (the written 1024x768 set = the fixture); not run in the game; uncommitted at the time of writing. **Second round the same day** (maintainer: "dark mode must be optional but not preselected, customer must be forced to select light mode (classic) or dark mode of battlefield interface. resolution selection must be a dropdown, then under it dark/light theme selection and under it checkbox about patching deprecated executable (if not selected then options screen for dc16.exe must not appear at all)"; "remove already patched 'Dark Colony.exe' from repo by default"): the console-style battlefield HUD is the DARK theme = data + the one-edit fix `console` (`patch_clock.py --part bank`), the LIGHT (classic) theme = the stock metal interface with a fifth shipped picture per size `INTRF_HD\<WxH>\INTRFACE_LIGHT.GIF`; `-Theme light|dark` required at an HD size, the window's options page = resolution drop-down, theme radios, deprecated checkbox (the Dark Colony page only while ticked); light 1024x768 references Ultimate `56ead72e…`, Classic `31c215a3…`, dark unchanged; the patched `Dark Colony.exe` is removed from the repository (`.gitignore`) and the patcher writes it on request only. Display doc §10.58, second round.

**2 Oct 2026, maintainer: "looks like we have to absolutely isolate files for different resolutions to they own folders, so resources are never mixed ... go ahead, build it. one more rule - when rebuilding for another resolution, delete all other resolution folders and resources. We must be sure that no there are no leftover anywhere from other resolutions resources."** - game data layout, exe path strings, the ONLINE module, the patcher and the data tools; no relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.61. Every resolution's interface set lives in its own 8-character folder `HD_<height>P` (`UW_` for the ultra-wide sizes), the exe's 30 path strings point at it (`hd_1080p/bintro`), the patcher's inputs moved to `HD_SRC\`, and a patcher run deletes every other size's folder, the pre-October `INTRF_HD` and the 640x480 copies before it writes - so a HUD script of one size can no longer sit under the frame of another, and a repository ZIP unpacked over an install of another size no longer overwrites that install's set. The ONLINE WAR module reads the folder from the exe's own `loadg` string (DGROUP `0x48234C`) instead of a hard-coded `intrf_hd`. Published Ultimate 1024x768 dark build `61f6a72a…`; the repository ships `HD_0768P\` and `HD_SRC\`. Tested with six consecutive patcher runs on a clean copy (sets = fixtures, every deletion as specified, HD_SRC untouched), the headless options test under both shells, and in game at 1920x1080 (battle, dialogs, the ONLINE WAR screen from the new folder).

**2 Oct 2026, maintainer: "smoke test every resolution with light/dark modes and select Lieutenant to check placement of his commands on the interface. use ozi first human mission."** - test only, no exe, data or relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.62. Dark Colony Ultimate patched from PowerShell 5.1 and driven through the OZI pack's `globo01` ("Return to Mars") at 1024x768, 1280x720, 1280x800, 1280x1024, 1920x1080, 1920x1200 (dark and light), 3840x1080 (dark and light through dgVoodoo) and 640x480 - fifteen runs: the Lieutenant (type 69, found through the object table of the running game, `gsread.py`) selected alone shows his five order cells (Stop, Move, Move & Attack, Waypoints, Inspire Troops) in the left grid column and `LIEUTENANT` in the status screen at every size, every exe byte-identical to the patcher's reference, `error.log` empty. One finding: the dark theme's Move & Attack cell carries the hot-key badge `P` of the unused `BUTTON.SPR` bank while the live key is `A` (measured: `A` lights the cell, `P` does nothing); Napalm / Disease show the return arrow where the stock bank says `D`. Fixed the same day at the maintainer's word ("fix the hot-key badges and update patcher"): `hud_console.py` `STOCK_BADGE` draws the stock letters A / D / D in BUTTON's key greys on cells 65 / 72 / 73, `HD_SRC\MAINBUT.SPR` regenerated (only those three cells differ), the patcher regenerated byte-identical (the bank is a resource, not script content), the A badge confirmed in game at 1024x768; committed and pushed 2 Oct 2026 (Dark-Colony `98c1f43`, Server `4583452`). Rig kept in `Dark-Colony-development/smoke_rig/`.

**2 Oct 2026, maintainer: "why lieutenant mark and units life indicator are too high on 1024 resolution above the unit itself? compare with 1920 resolution"** - exe fix in the `resolution` display fix, no data or relay change; `DC16_DISPLAY_AND_RESOLUTION.md` section 10.63. At the sizes whose view is an odd number of tile rows (1024x768: 23, 1280x720: 21, 1280x1024: 31) the camera's tile snap (`mov byte ptr [ui+110h],0`, `0x40AF2B`) put the renderer's origin `camera - 11.5 tiles` on a half tile; the tile renderer floored it and drew terrain and sprites 16 px lower than the pixel-exact frame of the selection marker and the mouse pick - the marker 34 px above the helmet instead of the stock 18, ground orders half a tile north of the click. `patch_resolution.py` now snaps the camera to `.80` on an odd axis (one byte per exe at those three sizes, nothing at even sizes), measured in game at all three: gap 18 px, camera 11.5 .. 72.5 tiles at the edges, `error.log` empty. Patcher regenerated: Ultimate 1024x768 dark `b1725e95...` (published), 1280x720 `48ecca5e...`, 1280x1024 `2c5df19d...`; even sizes unchanged. Committed and pushed 2 Oct 2026 (Dark-Colony `20904d3`, Server `4d4c82c`).

**2 Oct 2026, maintainer: "multiple units are throwing a projectile. investigate how much effort would be to add tracer bullets with light tails for human and alien troopers" -> "ok, fix alien trooper weapon upgrade sprite problem. and implement tier 1 for human trooper only" -> "i see that lieutenant dont have a tracer"** - data only, no exe byte, no relay change; `DC16_DISPLAY_AND_RESOLUTION.md` section 10.64; `tools/tracer.py`. The human trooper's weapons 1-3 and the Gray trooper's upgraded weapons 16/17 named a sprite (`weapons`) without a `BULLET0` animation, so those shots were never drawn and the Gray bolt disappeared with the first weapon upgrade. Now `SPRITES/TRAC.SPR` + `ANIMATE/TRAC.FIN` (16 facing streaks on the Gray bolt's own glow and ground-glow cells; `TRACBULLET<n>` and the alias `SMOKBULLET<n>` for the Lieutenant's pistol; same day, after "go with option 2, gray bolt for alien leader": the Gray commander's weapon 62 - the same `SMOK` name - is renamed `SMOG` in the overlays and the FIN gives it `SMOGBULLET<n>` = the bare Gray bolt and `SMOGEXPLODE0` = its pistol hit animation copied byte for byte, so the explosion count, a rand() consumer, stays 1; verified with a weapon-table probe of the running game; then "human lieutenant projectile have wrong offset" / "wrong offset is visible the most when shooting horizontally": a missile flies at the shooter's ground point, so the streak ran along the feet - now lifted 22 px to rifle height (`GUN_LIFT`), the ground glow stays on the soil; measured in game with the offline recorder `smoke_rig/record_run.py`), weapon-table overlays `dc/gamestat/weapstat.txt`, `exp/gamestat/weapstat.txt` and the pack's `ozi_ns/gamestat/weapstat.txt` (1-3 -> TRAC, 16/17 -> GRAY, 62 -> SMOG; the root table stays stock for the original exes), `trac.fin` in `exp/animozi.dat`; the four files are `Data` of the `ozi` fix, patcher regenerated, exe hashes unchanged. **Network-safe**: a bullet sprite only sets the missile's animation instance; the checksum covers the missile count and the objects, and no rand() depends on it, so patched and unpatched players stay in sync and the engine port is unchanged (the explosion count does feed rand(), so no weapon's EXPLODE lookup was touched). Confirmed in game at 1024x768 (OZI globo01: missiles of weapons 1, 5 and 17 carry the animation, streaks and bolts sit on the bullets within the rig's one-tick jitter). Uncommitted at the time of writing.

**2 Oct 2026, maintainer: "it's time to create a replay functionality for server and Ultimate executable. Ultimate must contain 'REPLAY ONLINE GAME' in main menu right under 'ONLINE WAR'. it must bring up the similar form as 'ONLINE WAR' does but with correct headers and modified. it must be possible to select from client list of participants (8 radio buttons with client names on the right pane of form). By the way, all bots on the server must have 'AI ' prefix for their names" -> "create the volume and go ahead with the replay design. List row format: [date time] [map name] [map type] [map slots count] [players] [AI count] [duration]. keep last 50 replays."** - relay, exe module, screen, menu, patcher; §21, protocol doc §4.5 / §6.10, `DC16_DISPLAY_AND_RESOLUTION.md` §10.65, F82. Relay: every battle is recorded as a file again (`RECORD_DIR=/data/replays` on a new 1 GB Fly volume `dc_replays`, `fly.toml` `[mounts]`; `REPLAY_KEEP` 50 - the newest battles stay, older files go when a battle ends, files without a sync frame once a day old), `src/replays.js` indexes them (newest first, the 40-column row, the eight participant names, the real-player mask), five message types `0x55..0x59` (`commands.js`), the hall answers `RLIST` with the list and `RPLAY` with a private **viewer room** (`RoomPool.openViewerRoom`: the §18.7 replay room per viewer - the recorded lobby, the seat pinned, the recorded speed, no engine, no recording - dropped when the viewer leaves, `Room.onEmpty`), `tools/fakeclient.js --replay [ID] --slot S`; `MERCENARY_NAME` / `FAKE_NAMES` carry the `AI ` prefix again (`AI Mercenary`, `AI Marauder`, ...; a taken-over base was `AI <name>` already). Exe: the module's second entry point `replay_game` (button id 9, `cmp edx,9`, dispatch for 8 and 9), the screen REPLAYE (ONLINE with a 40-column list and, right of the scroll bar, eight `checkb` boxes - radio buttons by the module's own exclusivity, F82 - with the participants' names and the heading WATCH AS; `REPLAYBG.GIF` with a fourth frame around the pane), the menu's right column row 2 (`OZI_COLUMNS`, plate 25 for MULTI PLAYER WAR's gadget 9 that the button id displaced, plate 26 for the new button, 12 `banim` pairs, `textmsg 12 REPLAY ONLINE GAME`), the patcher's `Edit-ReplayScript` / `Write-OnlineScreen`. Tests: 7 new (`replays.test.js`), 268 in all. **Confirmed in game at 1024x768 against a local relay** (a scripted battle recorded first): the button, the screen with the list row `02.10 15:30 Plink - O Jungle 8 1 1  0:39`, the pane with `AI Mercenary` and `Player3` (the real player preselected), the radio behaviour (one box ticked at a time, an empty seat ignored), WATCH -> the viewer room's lobby with the replay greeting -> READY -> the battle from Player3's seat, the room dropped when the game closed, `error.log` empty. Two findings fixed on the way: the module reads one command per frame, so the relay sends the list unpacked; a `checkb` toggles itself and reports kind 2 / 3 (F82), not the 4 / 5 of the keyboard entry the first build listened for. **Same day, three more maintainer requests:** "'REPLAY' button must be disabled until user selects a client to watch for. and client radio buttons must be empty instead of question mark" (the button is REPLAY, greyed with `set_greyed 0x424574` until a seat is ticked, nothing preselected; the unticked box is cell 149 of the new bank `HD_SRC\KNOBR.SPR` = KNOBE + an empty box, `patch_online.py bank`, the fix's `Data`), "add version number and build number to the installer" (`PATCHER_VERSION` 1.0 in the generator, build = the UTC time of the generation `YYYYMMDD.HHMM` + the two repositories' commits, shown in the window title, the welcome page, the result box, the CLI banner and the file header; `Set-StrictMode` lesson: declare `$script:BannerShown` before reading it, or every CLI build dies), and "fix money count increase problem in replay mode, when money are not shrinking on purchase" (F83: `viewer_money_fix` in the proxy thread takes every increase of the local seat's `SPENT` off its `MONEY`, reading the simulation state through `.bss 0x4AA9DC` - the menu's `gs` is the campaign object; verified with an injected `0x0A` purchase in a copy of the recording: money 1500 + income - 350 = 1315 with spent 350 in the live state). Published Ultimate 1024x768 dark **`4c7da630…`** (= patcher under pwsh 7 and 5.1 = generator reference; intermediate builds `c7a7c1b1…`, `8208da56…`, `f8d6f5e3…`, `fe4ead95…` that day); `dcexp16.asm` regenerated. Committed and pushed 2 Oct 2026: Dark-Colony `ff69aad`, Server `5c9935c`. **Maintainer after the Fly test: "tested on fly, replay works. when replay ends then relay must close a connection"** - the watchdog evicts the viewer (`replay ended`) once its progress report reaches the last recorded tick, or `REPLAY_END_GRACE_MS` (10 s) after the last frame; the game then returns to its menu (relay only, no exe change). Then "disable possibility to watch replay for slot 0 because that would give a control to the customer over settings in lobby": the relay refuses an `RPLAY` for slot 0 (`HOST_SLOT`; the game's lobby host is slot 0, its client sets map and options). Then "slot 0 must contain correct name of ai master as before, but its radio button must be grayed out and unclickable": the name is listed again, the exe shows slot 0's box as KNOBR cell 150 (the empty box in greys) for both states and never ticks it - a new exe build: published Ultimate 1024x768 dark **`cd297478…`** (pwsh 7 = 5.1 = generator reference), confirmed in game (the grey box beside `AI Mercenary`, a click on it leaves REPLAY greyed, ticking another seat enables it), `dcexp16.asm` regenerated; Dark-Colony `e7d2eff`, Server `a4feb03`. **Maintainer on Fly with that build: "tested on fly, works"** (the end-of-replay close, the host seat's grey box, the money correction).

**1 Oct 2026, maintainer: "do a smoke test for all resolutions. clients are complaining that some interface elements are not in place"** - test only, no exe, data or relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.59. The Ultimate build was patched (PowerShell 5.1, the INSTALL.CMD shell) and driven through menu, ACADEMY, briefing, battle, unit selection, the three panel tabs and the four battlefield dialogs at 640x480, 1024x768, 1280x720, 1280x800, 1280x1024, 1920x1080 and 1920x1200 (dark), 1024x768 and 1920x1080 (light), the deprecated Classic build at 1920x1080 (dark): every element in place, `error.log` empty, every written set equal to the `hd_sets` fixture (two fixtures' `exp/intrf_hd/introe` were stale and refreshed). The player's screenshot (1920x1080, empty panel, a cyan "000" on the map) was reproduced by pairing the 1920x1080 frame with a 1024x768 `MAINE`: the HUD widgets land inside the map view and are overdrawn; the player's "000" position says their `MAINE` is a 1280-wide one from a pre-30 Sep patcher under a 1 Oct frame - a mixed interface set, cured by re-running the patcher at their size. Rig: `smoke.py` / `runall.sh` / `cmpset.py` in the 1 Oct session scratchpad. **An installer integrity check built on this finding (1 Oct 2026: a manifest of every shipped file in the patcher, a scrollable error popup with a download link, a consistency check of the installed set) was reverted on 2 Oct 2026 at the maintainer's word - "it doesn't solve anything but adds too much complexity"; the mixed-set diagnosis above stays.**

**2 Oct 2026, maintainer: "Customer is complaining that '1', '2', '3' tabs and days count on the battlefield are lost after patching. investigate why." -> "can we do a culture invariant patcher?"** - patcher only, no exe byte, no data, no relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.66. The player's folder (a ZIP, patched at 1920x1080 dark by the patcher of `cc633d5`) had the reference exe and a freshly written `HD_1080P\` whose `MAINE`, `BINTROE`, `DINTROE` and `INTROE` had been letterboxed like menu screens instead of laid out as the HUD / intro scripts - every file name with an I. Cause: `Write-InterfaceSet` picks its branches with `.ToLower()` comparisons, and under a Turkish or Azerbaijani Windows regional format .NET lowercases `I` to the dotless `ı`; reproduced byte for byte by running the same patcher with the thread culture set to `tr-TR`. Fix: patcher **1.1** is culture-invariant (`ToLowerInvariant` / `ToUpperInvariant` everywhere, thread culture = InvariantCulture from the first line, window and command line); reference hashes unchanged; verified on a clean copy under `tr-TR` in PowerShell 5.1 and 7 (sets = fixtures, exes = references) and the headless window test. Rule: every patcher change is checked once under `tr-TR`. Committed and pushed 2 Oct 2026: Dark-Colony `37542ea`, Server `df3d7f6`.

**3 Oct 2026, maintainer: '"DEFAULT_SERVER.TXT" upgrade. it must contain field "name=https://github.com/endotermic/Dark-Colony-Server" and "address=dark-colony-server.fly.dev". add a line with "Name:" above the "Server:" line on the "ONNLINE WAR" and "REPLAY ONLINE GAME" forms'** - exe module, screens, patcher; no relay change; §20.2, protocol doc §4.4 / §4.5, `DC16_DISPLAY_AND_RESOLUTION.md` §10.51 (third round). `DEFAULT_SERVER.TXT` is `key=value` lines now: `name=<text>` (the rest of the line), `address=host[:port]`, the word `plain` as before; the bare `host[:port]` file of 29 Sep is still read (it shows "Name: -"), `address=` wins over a bare address, keys match without case, unknown keys are ignored, and `//` opens a comment only at a line start or after white space - the first stripper would have cut `name=https://...` at the colon. Both screens have a third text line: `in_text 49` "Name: <name>" at list bottom + 14, the server line moved to + 30, the state line to + 46, the grey text frame B+8 .. B+68 (the buttons sit at B+87; at 640x480 the frame ends at 428, the CHOB plate starts at 436); the module (`online.c`, 15 128 bytes, was 14 616) sets the name line only when the loaded script has widget 49 (`ip->objects[49].type == 4`: a screen written by an older patcher lacks it and `0x423ED4` asserts on a missing widget) and clips both lines to the widgets' 56 columns. Patcher **1.2**: `Edit-OnlineScript` emits the three lines, both frames grow, `DEFAULT_SERVER.TXT` is written when missing and upgraded when it is the bare shipped address alone (`patch_online.bare_shipped_config` ported to `Write-OnlineScreen`) - a player's own address stays. Published Ultimate 1024x768 dark **`a309a2f5…`** (light `2da31ce3…`, 1280x800 dark `04c4949b…` = the maintainer's game folder, rebuilt from a scratch copy); Dark Colony `24de980e…` and the editor `de8076dc…` unchanged. Verified: the parser compiled on the host against ten inputs (new form, old form, comments, `address=` over a bare address, a bad port, a missing file); a clean copy patched under pwsh 7 and under PowerShell 5.1 with the `tr-TR` culture = the reference exe and the fixture set (HSCENE/GSCENE = the DC ending names, the copy has the movies), the old shipped file upgraded, a `127.0.0.1 plain` file left alone; the six `hd_sets` fixtures and the game folder's `HD_0800P` screens regenerated. **In game (3 Oct 2026, 1024x768 dark from a `subst X:` clean copy against a local plain relay, `DEFAULT_SERVER.TXT` = `name=Local test relay (plain) // trailing comment`, `address=127.0.0.1`, `plain`):** ONLINE WAR and REPLAY ONLINE GAME both show "Name: Local test relay (plain)" over "Server: 127.0.0.1:8888" over the state line inside the frame, the comment stripped from the name (`ONLINE.LOG`: `config: name Local test relay (plain)`), both connected, BACK returned to the menu, `error.log` empty. Rig lesson repeated: a driver that imports `drive.py` before the game's mode switch keeps the desktop's screen size - re-read it after the switch (`drive.SW, drive.SH = drive._screen()`), or every click lands at 1024/1920 of the intended point. **Committed and pushed 3 Oct 2026: Dark-Colony `23a7c7b`, Server `4121233`.**

**3 Oct 2026, maintainer: "error on game startup"** - exe fix, fifth edit of `ddraw` (`tools/patch_ddraw_lost.py`), both games, no data or relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.16 (addendum). The Windows Application log held two access violations of `Dark Colony Ultimate.exe` 35 s apart, fault offset `0x2EE50` = `0x42EE50`, right after the maintainer's exe had been replaced by the day's 1280x800 build; the game's `error.log` was empty. The site is the cursor set-up of `ddex4.c` (lines 805-858): the 32 cursor bitmaps are loaded into surfaces, then cursor surface 0 is LOCKED to read its top-left pixel as the blit colour key (line 836, "Couldn't lock mouse"); the failure is printed and "asserted" through the non-fatal `0x47C08E(0)`, and the code then reads the pixel through the `DDSURFACEDESC`'s `lpSurface`, which a failed Lock leaves uninitialised - NULL or garbage. A lost surface (the §10.16 two-monitor loss, the desktop had two monitors again) fails that Lock; the `ddraw` fix of 13 Sep covered the palette remap and the loading screen's Flip but not this earlier site. Reproduced once: a focus loss (Alt+Tab from a driver) 0.5 s after launch gave exactly `Couldn't lock mouse / assert failure, file ddex4.c line 836 (0)` in `error.log` on the unfixed exe (that run survived because the stale pointer happened to be readable - the maintainer's two were not); fourteen more timed attempts did not hit the window again (the first, cold-cache launch loads the bitmaps slowest). The empty `error.log` = the game truncates it at every start, so a later successful start erased the crash runs' lines. **Fix:** the 92-byte failure block (Classic `0x42ED8C`, Ultimate `0x42EDEC`) becomes 51 bytes: `GetPixelFormat` on cursor surface 0 (answered for a lost surface too) into the desc's own `ddpfPixelFormat`, key = `dwGBitMask == 0x7E0 ? 0x69E0 : 0x35E0` (the bitmaps' corner colour, palette entry 0 = RGB 109/60/0, truncated into 565 / 555 as GDI's blit does), stored where the pixel read would have put it, then a jump past the read and the Unlock; the per-frame restore reloads the cursor bitmaps and the colour key stays with the surface objects. Five of the block's six HIGHLOW `.reloc` entries become type 0, the first stays (the new `mov eax,[imm32]` operand sits at the same offset); `ddraw` = 5 code sites + 8 `.reloc` entries, anchored on the unchanged Lock call before the block. Every game build's reference changed: Ultimate 1024x768 dark **`5f8655ea…`** (light `27177b98…`), 1280x800 dark `58282faf…` (= the maintainer's game folder, rebuilt from a scratch copy), Classic 1024x768 dark `ae34e1d5…` (not shipped); editor unchanged. Verified: the block disassembled from a patched copy (`dumpbin`), `plan` / `verify` on both stock exes and on the patched output, a clean copy under pwsh 7 = the references, the fixed 1280x800 build started seven times (three with the 0.5 s focus loss, one with a cursor close-up afterwards: the pointer draws cleanly), `error.log` empty. Not proven: a run in which the fallback path itself executes (the loss window is not reproducible on demand); if the key were wrong the pointer would show a brown box, not crash. `dc16.asm` / `dcexp16.asm` regenerated. **Committed and pushed 3 Oct 2026: Dark-Colony `23a7c7b`, Server `4121233`.**

**3 Oct 2026, maintainer: "'Reply online game' header does not fit in frame"** - data only (`REPLAYE` scripts), no exe byte, no relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.65. The replay screen's title "Replay Online Game" is 18 characters of `MFONTO2` = 302 px (measured on the 1024x768 capture of the morning's test), while the rounded title panel of LOADER.GIF has about 290 px of flat interior, so the text ran onto the rounded ends at every size; the title is now **"Online Replay"** (13 characters, the longest that fits; pairs with "Online War"). `patch_online.TEXTS_R`, the patcher's `Edit-ReplayScript`; fixtures, the repository's `HD_0768P/REPLAYE` and the game folder's `HD_0800P/REPLAYE` regenerated. The same capture (the maintainer's 1280x800 folder, connected to Fly) showed the replay screen's TLS status line cut at the widget's 56 columns ("... tick a player, press REP"): the module's string was 61 characters, now "Connected (TLS). Pick a battle, tick a player, REPLAY." (54; the other three state lines were 41-54 already) - a module rebuild, so the Ultimate references moved once more: 1024x768 dark **`05c43005…`** (light `2a73d0c7…`), 1280x800 dark `250578db…` (the maintainer's game folder); Classic `ae34e1d5…` unchanged. Confirmed in the maintainer's folder at 1280x800: "Online Replay" inside the panel, the Fly list with one recording. **Committed and pushed 3 Oct 2026: Dark-Colony `23a7c7b`, Server `4121233`.**

**3 Oct 2026, found while testing the title change: the deprecated Dark Colony build, patched after Dark Colony Ultimate, dropped Ultimate's interface files** - patcher only (patching order), no exe byte, no data, no relay change. `Write-InterfaceSet` rebuilds the resolution's folder from scratch (the 2 Oct rule), and only the Ultimate step adds the ONLINE / REPLAYE screens with their backgrounds (`Write-OnlineScreen`) and the three option-dialog copies with the MUSIC row (`Write-MusicDialogs`); `BUILDS` runs CouncilWars, MapEditor, Classic, so `-All -IncludeDeprecated` (and the window with the deprecated page ticked) ended with a set that lacked them - ONLINE WAR would have asserted on the missing screen. Seen as "No such file" for `HD_0768P\ONLINE` in a clean-copy check; the plain `-All` run (Classic skipped) was never affected. Fix: `Get-PatchOrder` / `Sort-ForPatching` - the executables are patched Classic first, Ultimate last, in the window's "Patch selected executables" and the CLI's `-All`; the pages and the summary keep their order. Verified on a clean copy: `-All -Resolution 1024x768 -Theme dark -IncludeDeprecated` under pwsh 7 and PowerShell 5.1 (`tr-TR`) = the three references, and the written `HD_0768P` set holds ONLINE, REPLAYE, both backgrounds and the `exp|dc|ozi_ns` `lopte` copies with the MUSIC row, all equal to the fixtures. **Rule: a step that adds files to a shared, rebuilt-from-scratch folder must run after every step that rebuilds it.** **Committed and pushed 3 Oct 2026: Dark-Colony `23a7c7b`, Server `4121233`.**

**3 Oct 2026, maintainer: "main menu. we must have only one 'LOAD GAME' button which will serve for saves for all campaigns. place it as last button in left column. Game loading form use the same as 'online war' must have a format [date]+[time]+[name]+[campaign name('Academy','Dark Colony','Council wars','Ozi missions')]. savefiles must remain in they folders."** - exe module (one code edit), menu script, patcher 1.3; no relay change; `DC16_DISPLAY_AND_RESOLUTION.md` §10.67. The three load buttons of the Ultimate menu (LOAD DC GAME 7, LOAD CW GAME 2, LOAD OZI GAME 4) differed only in the mode stub run before the stock load routine `0x403AA4`, which calls its one-folder picker screen `0x40388C(ui, buf, cap; cl = 0)` and then opens, reads and resumes whatever path the picker left in `buf` - through the prefix helper that falls back to the bare path, so any mode can open any folder's file, and the save header carries the campaign record (`gs+0x14F0` = the game type: 0 campaign, 2 network, 3 training, at file offset 14; `0x14F4` the pack flag). So: **one button (id 2, the stock LOAD GAME text, last in the left column), one code edit** - the rel32 of that `call 0x40388C` at `0x403ABC` now reaches `load_game_picker` in the `online` module (`patch_online.py` edit 6; a naked thunk, the save form `cl = 1` still gets the stock picker), which lists `save\`, `esave\`, `ozisave\` with `FindFirstFileA`, reads every `.dcg` header for its type, sorts newest first and shows `dd.mm.yy hh:mm  name(26)  campaign` (Academy = save + type 3, Dark Colony = save, Council wars = esave, Ozi missions = ozisave; a network save says Multiplayer) in **`LOADALLE`** = the ONLINE script with the title Load Game and the button LOAD (ONLINEBG.GIF shared; `patch_online.loadall_script`, patcher `Edit-LoadAllScript`, `Write-OnlineScreen`), the three text lines = counts per folder, the selected file, the state; LOAD runs the folder's stub (`stub_dc_set` / `stub_cw_set` / `stub_pack` - prefix, save folder, music), returns the path and the stock code does the rest; the saves stay where the game wrote them. Menu: `OZI_COLUMNS = ((1, 6, 0, 16, 2), (8, 9, 3, 5, 12))`, one gap after row 4, buttons 4 and 7 with their plates and `textmsg 10` dropped from the script (handlers stay, unreachable), ten `banim` pairs; the block rises 116 rows instead of 192, so the credits box keeps its stock 100 rows at every size (`patch_resolution.OZI_BLOCK_RISE`; the `push 64h` site is a no-op everywhere now - the `resolution` fix of Ultimate lost one edit at 1024x768 and 1280x720). Patcher **1.3**; `.gitignore` of the game repo gained `INTRFACE\LOADALLE` (+ the forgotten `REPLAYE` / `REPLAYBG.GIF` 640x480 copies). References: Ultimate 1024x768 dark **`35346be9…`** (light `6accd206…`), the other sizes in the script; Dark Colony `ae34e1d5…` and the editor unchanged. Verified: a clean `git archive HEAD` copy under pwsh 7 and under PowerShell 5.1 `tr-TR` = the reference exe and identical sets, the PowerShell menu port = `menu_script` on the stock script and on the previous layout's output, `loadall_script` = `Edit-LoadAllScript`; the six fixtures' `bintroe` / `LOADALLE` refreshed from patcher runs at every size (hashes = the references). **In game (1024x768 dark, clean copy on `subst X:`, `smoke_rig/saveload_test.py`):** one F11 save per campaign (`save/acad1` type 3, `save/dc1`, `esave/cw1`, `ozisave/ozi1`), the list newest first with the four labels, each row loaded in turn and its battle back as saved, a save after each load in the loaded save's folder (the mode followed the pick), `error.log` empty. `dcexp16.asm` regenerated. **Committed and pushed 3 Oct 2026: Dark-Colony `4138b49`, Server `3c5723c`.** **Same day, maintainer: "rename 'multiplayer war' to 'CUSTOM NET WAR'"** - the menu label of button 3 (`textmsg 4`) in `build_ozi_overlay.OZI_LABELS` and the patcher's `Edit-OziMenu`; data only, the handler and the relay unchanged; the stock script of the untouched exe keeps MULTI PLAYER WAR. Docs written before say MULTI PLAYER WAR for the same button.


**3 Oct 2026, maintainer: "investigate - when playing network game there is a 'save' button available. is this saved mission visible in main menu 'LOAD GAME' form?" -> "hide the save button in network battles and update patcher"** - exe fix **`netsave`** (`tools/patch_netsave.py`, both games, `Requires nocd`, after `chat`; F84; `DC16_DISPLAY_AND_RESOLUTION.md` §10.68), patcher **1.4**, no relay change. Answer first: yes - the in-battle save dialog had no game-type check, the file went to `save\` with game type 2 and the LOAD GAME picker showed it as `Multiplayer`; loading it ran the stock network-resume branch (`0x403B30` -> `0x40122C` with no address and no network object = this machine as the host of the in-process mailbox network, lobby skipped, every other seat present but unconnected), a solo game against frozen opponents and never a rejoin. Fix: at battle start the game start's network branch calls `widget_enable(ip, 63, 0)` (`0x424488` / Classic `0x424428`, the per-widget "enabled" byte `+3` the game itself uses to hide the Allies cell in campaign battles; the tab switch only touches the "shown" byte `+2`) when the game type is 2, and the save dialog's entry returns at once for game type 2 (F11, the `?` key and the cell); two 5-byte hooks, two stubs (55 + 34 bytes) in the wave loader's dead CD attempt behind `longpath`'s `open_read` (`0x452B05` Classic / `0x452B65` Ultimate), two `.reloc` words. References: Ultimate 1024x768 dark `b3d3ead8…`, light `eb79fba7…`, Classic `739e1219…`; the tool on the published `35346be9…` = the patcher's output; pwsh 7 = PowerShell 5.1 `tr-TR` on a `subst X:` copy. In game at 1024x768 dark against a local relay (ONLINE WAR, room 1, READY, battle): the Save cell's rect pure black, F11 and a click on it inert, still black after tabs 1/2/3; ACADEMY: cell drawn, F11 opens Save Game; `error.log` empty (`smoke_rig/netsave_test.py`; rig lesson: a copy with `AVI\INTRO.AVI` plays the intro and the first clicks land in the movie - rename it away). Game folder: the new Ultimate exe copied in by name. **Second round (maintainer: "run: other resolutions, 640x480, the Dark Colony build in game, the replay viewer"):** Ultimate at 1280x720 / 1280x800 / 1280x1024 / 1920x1200 dark, 1920x1080 light, 640x480 and 3840x1080 (dgVoodoo, letterboxed clicks), the Dark Colony build at 1024x768 through CUSTOM NET WAR (hall, `/1`, two READYs) and TRAINING, and the replay viewer on a 76 s recording - the cell blank and F11 inert in every network battle and in the viewer, drawn and working in every campaign battle, `error.log` empty throughout (`smoke_rig/netsave_all.py`; the viewer room is an ordinary lobby: READY starts it, an empty seat's box is ignored). **Committed and pushed 3 Oct 2026: Dark-Colony `c77d671`, Server `7949bf8`.**

**3 Oct 2026, maintainer: "in linux+wine speed of cursor animation and map scroll on the battlefield are ridiculously fast. investigate" -> "go ahead with option 1 and update patcher"** - exe fix **`fps`** (`tools/patch_fps.py`, both games, `Requires ddraw`, after `netsave`; F85; `DC16_DISPLAY_AND_RESOLUTION.md` §10.69), patcher **1.5**, no relay change. The main loop's only pacing is the DirectDraw `Flip(NULL, 0)` at the end of `present` (`0x42E0FC` / Ultimate `0x42E15C`), which Windows completes at the vertical blank and Wine at once: 350-380 frames per second measured in the WSL rig (`WINEDEBUG=+timestamp,+ddraw`, `_Flip` lines per second), and the battlefield's edge/key scroll (one tile per frame, `0x40AE6A..0x40AEB6`) and cursor animation (`call 0x422C7C` at `0x40B309`) are per-frame with no time gate, while the menus are clock-gated (`0x424294` 16 ms, `0x42412A` 33 ms) and the ticks clock-driven. Fix: `present`'s epilogue `lea esp,[ebp+82h]` (`0x42E2A1` / `0x42E301`) jumps to a 132-byte position-independent stub (`call $+5; pop esi`, no absolute operand) in the three dead `remap` assert bodies behind the `ddraw` jumps (`0x42F399` / `0x42F3FA` / `0x42F443`, +0x60): `timeGetTime`, and if less than 16 ms passed since the previous frame `GetModuleHandleA("winmm.dll")` + `GetProcAddress("timeBeginPeriod")(1)`, `Sleep(1)` until 16 ms, `timeEndPeriod(1)` - a Windows 11 process without `timeBeginPeriod` sleeps 15.6 ms per `Sleep(1)` (measured), which would have made 144 Hz monitors 43 frames per second; the pair around the wait keeps fix `restore`'s minimised `Sleep(1)` at one tick. Timestamp at `0x481FF0` (the `.idata` page slack, unreferenced), 20 HIGHLOW `.reloc` entries of the dead bodies -> type 0, `patch_ddraw_lost.py` re-anchored on the loop tail (its old anchors were the dead bodies' first bytes). Verified: Wine rig 114 Flip calls per second in 16-18 ms + 1-3 ms pairs = 57-60 frames; Windows `smoke_rig/fps_test.py` 60.2 frames per second (median 17 ms), the timestamp advancing every frame, RIGHT arrow held = 18 tiles in 18 frames (60.1 tiles per second, stock), `error.log` empty; clean copy pwsh 7 = PowerShell 5.1 `tr-TR` = references **Ultimate `308ac5b7…`** (light `5bb3fdd4…`), **Dark Colony `32432f5c…`** (light `e2bd1fc3…`), editor unchanged, sets = fixtures. Game folder: the new Ultimate exe copied in by name. **Rule: every per-frame effect assumes a 60 Hz frame - rely on the limiter or gate by the clock; a tool anchored on dead code breaks when the dead code is reused.** **Second round (same day, maintainer: "test these things on this machine using virtual monitor with refresh rate 120hz" / "pointer animation is too fast"):** six sizes in game (1024x768 .. 1920x1200 dark, `sizes.sh` + `fps_test.py`) all 60.0-60.2 frames per second, limiter active, one tile per frame, exes = references, `error.log` empty; the pointer-edge oddity = the in-battle pointer follows relative movement (DirectInput path `0x450E80`), so a synthetic jump lands short of the 3-px zone (readback `0x4DFF14`: 1197 for a move to 1277) - hold an arrow key; pointer animation measured 30.2 cursor-index changes per second in battle (stock 60 Hz rate, `0x426428` has no clock) vs 11.5 at the menu (33 ms gate) - slowing it means gating `0x40B309` like the menus (built the same evening as fix `pointer`, below); 120 Hz not reproducible here: the panel is 60 Hz only and dgVoodoo (fake and real fullscreen, `ForceVerticalSync = false`) still presents at 60 (old exe 13-18 % CPU at the menu), a virtual display driver (IddCx) would be a system install left to the maintainer; the Wine rig stays the one unbounded presenter and there the cap holds. **Third round (same day, maintainer: "ok, let's try battle cursor at the rate of gated the way the menus are"):** fix **`pointer`** (`tools/patch_pointer.py`, both games, `Requires ddraw`, after `fps`; F86; `DC16_DISPLAY_AND_RESOLUTION.md` §10.70), patcher **1.6**: the client's cursor-advance call `0x40B309` -> a 35-byte gate in the tails of the `remap` dead bodies (`0x42F424` 21 B + `0x42F46B` 14 B, +0x60) that lets the advance through only when 33 ms have passed (`timeGetTime`, `last` at `0x481FF4`, three tail `.reloc` entries re-pointed / neutralised); `fps` narrowed to 92/42/40 bytes and 17 entries so the tails stay free. Measured 14.2 cursor changes per second in battle (30.2 before), frame rate and scroll unchanged, `error.log` empty, Wine 640x480 battle fine; clean copy pwsh 7 = 5.1 `tr-TR` = references **Ultimate `3cba8b55…`** (light `7ad1744c…`), **Dark Colony `d2424406…`** (light `3a53818b…`), editor unchanged. **Committed and pushed 3 Oct 2026: Dark-Colony `ef41557`, Server `98f5e88`.**

**5 Oct 2026, maintainer: "installer must have green plus yellow plus the ozi_ns pack. put all patcher's resources and scripts (except installer.cmd and PATCH_HOWTO.TXT) into a separate folder. installer - add a checkbox that adds a form to select both discs and installation directory ('documents' by default). So installer would be ready for deployment on ModDB"** (after "create an advice of what to put in when registering on ModDB" -> "is it worth to include all game including proprietary parts? how can we overcome this shit?" -> "can my installer download iso for darkcolony.pl and work with it as a base?" -> "what content from repo can be safely included in installer?") - patcher **2.0**, repository restructure, no exe byte, no relay change; `DC16_DISPLAY_AND_RESOLUTION.md` section 10.71. ModDB removes full-game uploads (the game is not sold anywhere, the rights holder unclear - Take-Two or Ubisoft through SSI), an installer fetching the fan-site packages would make the patcher the distributor (and darkcolony.pl has no English Council Wars disc anyway), so: the **installer package = `INSTALL.CMD` + `PATCH_HOWTO.TXT` + `patcher/`** with nothing of the game in it, and the game comes from the player's own two discs. Every tracked file of the game folders was classified against the two CD images (`tools/discs.py manifest` -> `tools/disc_manifest.json`: 2263 disc files - 1958 from the Dark Colony disc, 305 from the Council Wars disc, 80 under another name on the disc, 12 game-written `.OVH` taken as the disc has them -, 75 patcher outputs, 17 repository-only files (the MP3 soundtrack, the 1998 `dc16.exe`, InstallShield logs), 1 derived (`human09.tro`, the 1998 update's typo fix), **366 resources** = the project's own files and the ozi_ns pack); the resources are COPIED into **`patcher/game/`** and **`patcher/editor/`** (a first form had moved them with `git mv`; maintainer, same day: "don't delete files from where they was! You must use 'patcher' directory as a source from where you take resources and copy to the places where they must reside" - the game folders keep every file, the repository plays as checked out; `tools/resources.py sync|check` keep the two copies equal), the patcher script into `patcher/`. The generated script copies the resources into the folder it patches (`Copy-Resources`) and counts a resource as a present data file (`Test-DataFile`); the welcome page's checkbox adds a **Game discs** page (Council Wars disc, Dark Colony disc - image `.iso` / `.bin` / `.cue` or drive - and the install folder, default `Documents\Dark Colony`), whose Next extracts and SHA-checks the two originals and whose Patch copies the whole game by the manifest (`DcDisc`, a C# ISO 9660 reader compiled by Add-Type like the GIF codec; resumable; ~10 s for 400 MB from a `.bin`); CLI `-InstallDir -CouncilWarsDisc -DarkColonyDisc -All`. A disc install cannot have the MP3 soundtrack: Ultimate = 20 of 21 fixes there (`b21baac1…` at 1024x768 dark), said on the page and in the HOWTO. Tested on a clean copy under pwsh 7 and PowerShell 5.1 (`tr-TR`): repository layout = the references and the fixture set, the resource copy a no-op there; disc installs from `.bin`, from `.iso` and from the Dark Colony disc as a folder = 2263 files identical to the repository, set = fixture; headless window; `tools/make_installer_zip.py` builds the package. Pitfalls: ISO root record extent at PVD byte 158 (not 162); PowerShell's case-insensitive variables (`$dir` clobbered the `$Dir` parameter); `` `u001a `` parses in neither shell the same way (use `[char] 0x1A`). **Same day, maintainer: "music is on the original discs as real music disc tracks. installer must rip them, and if possible convert to mp3"** - patcher **2.1**: `DcDisc` scans the audio tracks of a raw `.bin` (silence-separated pieces after the sync-carrying data track) or reads a drive's TOC and raw CDDA sectors, writes each track as a trimmed WAV and `ConvertTo-Mp3` encodes it at 192 kbit/s with Windows' MP3 encoder through the WinRT `MediaTranscoder` (Windows PowerShell 5.1 in-process, a `powershell.exe` child under pwsh 7; probed once by `Test-Mp3Encoder`), into `MUSIC\` and `exp\music\` - so a disc install from `.bin` images or real CDs reaches the full reference (`3cba8b55…`); an `.iso` has no audio and the music is left out (MCI `mpegvideo` refuses a WAV named `.mp3`, so there is no WAV fallback). Tested on the two `.bin` images (8 tracks, MPEG-1 layer 3 192 kbps 44.1 kHz, the game's MCI device plays them); the real-drive path is written from the IOCTL documentation and untested (no optical drive on this PC). Smoke test as a player: the package unpacked into `Documents\Dark Colony patcher`, `INSTALL.CMD` from there built `Documents\Dark Colony Ultimate` from both `.bin` images - 2696 tracked files identical to the repository, 8 ripped MP3s, 12 disc `.OVH` caches, nothing missing, one untracked `HD_0768P\BACKDROP.GIF`; the game ran from that folder into the first OZI battle with an empty `error.log`. **The package is also its own public repository since 5 Oct 2026 (maintainer: "create github repo 'Dark-Colony-Ultimate' and put there installer with it's resources as we decided earlier for ModDB"): `https://github.com/endotermic/Dark-Colony-Ultimate`, cloned to `../Dark-Colony-Ultimate` - exactly the tracked `INSTALL.CMD`, `PATCH_HOWTO.TXT` and `patcher/` of Dark-Colony plus its own `README.md` and `.gitattributes` (`*.cmd -text`); `tools/publish_installer.py GAME_REPO ULTIMATE_REPO` copies the package over (removes stale files under `patcher/`, touches nothing else, stages nothing) - run it after every patcher change, then commit and push there too; no LICENSE yet in either game repository (the maintainer's call).** **Committed and pushed 5 Oct 2026: Dark-Colony `26483c0`, Server `58488c8`** (the maintainer's unstaged 1280x800 run in the game folder - `HD_0800P`, the deprecated `Dark Colony.exe`, the `HD_0768P` deletions - left in the working tree).

**`ozisave` is not carried; `ozisave.txt` is created from scratch (5 Oct 2026, same day, maintainer: "patcher must not carry 'ozisave'. ozisave.txt must be created from scratch"; patcher 2.2; `DC16_DISPLAY_AND_RESOLUTION.md` section 10.71).** `patcher/game/ozisave/ozisave.txt` is removed from the repository (`git rm`) and `tools/discs.py` classes `ozisave/ozisave.txt` as an OUTPUT (`OUTPUT_RE`), so it left the manifest's resource list (365 resources) and the installer zip (368 entries); `gen_apply_script.ozi_data` no longer lists it as a `Data` file. The generated script's `Write-OziSaveFolder` runs after every Dark Colony Ultimate build with the `ozi` fix (any resolution, the disc install too): it creates `ozisave\` when missing (stub_pack points both save-folder slots at it and the game writes `ozisave\<name>.dcg` without creating the folder) and writes `ozisave\ozisave.txt` with the one line the game folder's marker carries when no such file exists; nothing is overwritten (a second run writes nothing, saves beside it stay). Verified on a clean copy of the index with `ozisave\` deleted before each run: pwsh 7 and PowerShell 5.1 `tr-TR` at 1024x768 dark = `3cba8b55…` / `de8076dc…`, set = fixture, marker byte-identical to the repository's; 640x480 = `be1b3fa0…`; the disc install from `.iso` + folder = `b21baac1…`, 2263 disc files identical, `ozisave\ozisave.txt` present. The game folder's own `DC - Council wars/ozisave/ozisave.txt` stays tracked (an output now, like the HD sets).

**5 Oct 2026, maintainer: "remove deprecated 'Dark colony' option from installer completely as won't be needed anymore"** - patcher **2.3**, `DC16_DISPLAY_AND_RESOLUTION.md` section 10.72. The Classic build (`dc16.exe` -> `Dark Colony.exe`, deprecated 1 Oct 2026) leaves the generator's `BUILDS` together with its two Classic-only fixes `movies` and `sounds`, the `Deprecated` / `Shipped` fields, `-IncludeDeprecated`, the options-page checkbox, `Write-StockEndingLists` and the "Ultimate last" patch order; the installer knows Dark Colony Ultimate and the map editor (Welcome -> Options -> two pages -> Ready, 4 steps). References unchanged (`3cba8b55…` / `de8076dc…`), set = fixture, disc install from `.iso` = `b21baac1…`; clean-copy CLI under pwsh 7 and 5.1 `tr-TR`, headless window under both shells. Script 1.98 MB -> 1.33 MB. The untouched `dc16.exe` stays in the repository for the research tools.

**5 Oct 2026, maintainer: "remove movie from startup. respective movies must play when selecting mission pack from main menu. 'dark colony' intro for 'DARK COLONY'. 'Council wars' intro for 'COUNCIL WARS'"** - fix **`intro`** (`tools/patch_intro.py`, Dark Colony Ultimate only, `Requires ozi`, after ozi in the patcher; patcher **2.4**; `DC16_DISPLAY_AND_RESOLUTION.md` section 10.74): the 95-byte start-up block of `main` that built `avi/intro.avi` and called `play_movie 0x401028` becomes a jump to the menu loop and hosts two trampolines (push edx; call the button's mode stub; call common with the movie path inline) and a common tail (pop the path, save eax, play, restore, jmp the campaign runner); COUNCIL WARS and DARK COLONY call them instead of fix ozi's plain trampolines, ACADEMY / OZI / LOAD GAME play nothing; two `.reloc` entries -> type 0, no new absolute operand. Ultimate 1024x768 dark **`165b609a…`** (light `b5986b07…`, 640x480 `01fc077c…`), editor unchanged; tool chain = patcher, clean copy pwsh 7 + 5.1 `tr-TR`, set = fixture; confirmed in game at 1024x768 (menu at once, the Mars intro after DARK COLONY, the jungle intro after COUNCIL WARS, ACADEMY straight to its screen, `error.log` empty).

**5 Oct 2026, maintainer: "wipe out a patched files from repo. customer must generate all of them by themselves!"** - patcher **2.5**, `DC16_DISPLAY_AND_RESOLUTION.md` section 10.75: every patcher output left the Dark-Colony index (`Dark Colony Ultimate.exe`, `Dark Colony Map Editor.exe`, the `HD_0768P` set with its three copies, `ozisave/ozisave.txt`; `git rm --cached`, `.gitignore` covers them all), the generator derives the set's stock inputs from the frozen `HD_SET_FILES` instead of the index and says "the generator's reference build" instead of "published in the repository"; the manifest's `outputs` class is empty. Clean copy without any output: `-All` builds `165b609a…` / `de8076dc…`, set = fixture, 5.1 `tr-TR` light `b5986b07…`, `.iso` disc install `b21baac1…`, headless window both shells. The hd_sets fixtures are the only reference copy of the sets now.

**7 Oct 2026, a player's report through the maintainer: "when you select Dark Colony or cw during the intro there is a sound of the menu" -> "stop that sound right before video playback and play that sound again after video playback ends"** - fix **`intro`** amended (`tools/patch_intro.py`; patcher **2.7**; `DC16_DISPLAY_AND_RESOLUTION.md` section 10.77): the main menu's looping hum (sample 0x86 `SOUND/HUM.WAV`, started by `bintro`, stopped by nothing before the pre-battle screens) went on under the campaign intros. The common tail of the two trampolines now stops every sample through the ui's `+0B4h` slot (the battle end's stop-all `0x4310CC`) before `play_movie` and starts the hum again through `+7Ch` after it - 39 bytes instead of 14 in the same freed block, no new absolute operand, `.reloc` as before; the tool checks `bintro`'s hum start in the exe and refuses the 5 Oct block. Ultimate 1024x768 dark `ac3a2463…`, light `720ca97b…`, 640x480 `7335ea8c…`; editor unchanged; byte-identical rebuild into the X: rig copy, confirmed in game (three launches, both movies, `error.log` empty).

## 17. Multi-room: seven rooms and the room-selection lobby (version 2.1)

Added 7 Sep 2026 from the maintainer's proposal (§16). The game gives a player no way to pick a
room: the connect dialog takes a host name only, the port is fixed (8888, then 8889), plain TCP
carries no host name, and the slot number arrives once in `'d'` (F30). Inside the lobby the player
has two input channels the server sees, chat text and the own name field, and the server controls
everything that is displayed. So the lobby itself becomes the room browser.

### 17.1 Overview

```
connect ──'d'(15,p) + hall dump──► HALL (private view: 7 rows = rooms 1..7 in place, own name row, map line = selected room, or empty until one is picked)
   │  '/N' selects a room (row N gets the '>' marker, Mercenary confirms)
   │  READY ('h' 2,p) on a joinable room
   ▼
ROOM N ──room dump without 'd' (rows become the real players, 'i' = the room's map)──► normal §6 lobby
   │  READY again = ready to fight, as before
   ▼
STARTING → RUNNING → reset (§3), independently per room
```

- One process, `RoomPool` with up to seven `Room`s (`ROOMS`, §11), each with its own map and the
  §3 state machine, all driven by the same 5 ms step timer and 500 ms watchdog. Rooms are fixed;
  a room resets to LOBBY when its last player leaves.
- The `Hall` handles a connection until it joins a room. Its view is private per client: the other
  waiting clients are not visible as rows (their chat is relayed, §17.4).
- `HALL=false` restores the 2.0 behaviour: every connection goes straight into room 1.

### 17.2 The hall view

Sent right after `accept`, in one write: the `'d'` as its own frame, then everything else in one
packed frame (F34; `PACK_LOBBY_FRAMES=false` gives one command per frame as in §6.1):

| Step | Message | Notes |
|---|---|---|
| 1 | `'d' 15, p` | `p` = the slot the client keeps for the whole connection (§17.5) |
| 2 | `'i' "D8PLAY01.SCN", title` | the **selected room** in the map line: title = `formatScenarioTitle(">3 Circle of Friends (0/7) open", 8, terrain)`. The digit at index 45 must be 8 because eight rows are occupied (F22); the file is never loaded, no game starts from the hall. Re-sent whenever the selection or that room's count or state changes. **Nothing is preselected** (maintainer, 12 Sep 2026): until the first `/N` the message is `'i' "", ""` (empty file and title), which makes the client grey out READY (F42) and show an empty map line |
| 3 | for every row q ≠ p: `'l' q,q` | colours = row |
| 4 | for every row q ≠ p: `'g' q,text_q` · `'f' 0,q` · `'j' 2,q` · `'n' q,q` · `'h' 1,q` | every row is a present-not-ready human, so the client stays in the lobby (F3) and no colour is ever locked (F20) |
| 5 | for p: `'g' p,<player name>` · `'f' 0,p` · `'j' 2,p` · `'l' p,p` · `'n' p,p` · `'h' 1,p` | the own row shows the player's name: the client never repaints its own field from an incoming `'g'` (F33), so this row cannot carry room text |
| 6 | `'o' v,default` for v = 0..7, then `'o' 8+q, joinable_q` for q = 0..7 | the per-row CD icon (cosmetic, F5) marks the rooms this client can join right now |
| 7 | the chat window | ten `'e'` lines (§17.8): the two-row header `Welcome to Dark Colony server 2.3.` · `No room selected. Type /1../7 + ENTER.` (becomes `Room <n> (<map>) is selected.` once a room is chosen), then blanks. Two rows since 27 Sep 2026 (maintainer: "only two lines in chat: greeting and map selected" with the hint); from 7 to 27 Sep 2026 four more rows sat between them (`Type /1../7 + ENTER to select a room,` · `then press READY to join it.` · `The map line shows the selected room.` · `You may type your name in your row.`) |

Rows and map line: the rooms fill the rows in order, skipping the client's own row `p`, so row 0
(Mercenary's slot) is always room 1 and the rooms after the own row sit one row lower than their
number. Every room row is a static 16-character text (below; until 26 Sep 2026 the 14 characters after `"<n> "` scrolled).
The map line repeats the selected room in full (42 characters are free, F15, so nothing scrolls
there) and is re-sent when the selection or that room's state changes; selecting does not touch the
rows. With fewer than seven rooms the remaining rows are empty (type 3, status 0). (An interim
version of 7 Sep 2026 showed eight rooms by moving the selected one out of the rows into the map
line; the maintainer found room 1 "lost" that way and asked for rows 1..7 that stay in place.)

Row text (maintainer, 26 Sep 2026: "disable scroll of maps. these fields must be static"; `Hall.rowText`):
`"<n> "` + the **first word of the map name** padded to nine places + `"(<players>/<slots>)"` in the
**last five places**, always 16 characters: `1 Plink    (0/6)`, `4 Circle   (0/6)`; a first word longer
than nine characters is cut (`2 Armageddo(0/6)`), and while the room is not in its lobby the word is
replaced by `in battle` (`2 in battle(2/6)`). `slots` = the map's player count minus the fake players
(bots are not seats). The `(X/Y)` indicator is five characters wide, so it fills the last five places
(the request said "four"; the indicator "as it is now", parentheses included, was kept). The states
`full` and `slot taken` are no longer in the row: the CD icon (joinable), the map line (unchanged
format `>2 Armageddon desert (2/6) in battle`) and `/rooms` carry them; never a `':'` in a row (the
chat prefix is split at the first colon). Every `HALL_REFRESH_MS` (200 ms) the server recomputes the
map line, the rows and the icons from the live room states and sends only what changed, **in one
frame** (F34), so a state change (a room starts, fills or empties) shows up within one step and an
unchanged hall sends nothing. History: from 7 to 26 Sep 2026 the 14 characters after the number
scrolled `"<map name> <terrain> (<players>/<slots>) <state>"` one character per `MARQUEE_MS`, padded
to a common length so that all rows wrapped together (third live test: unequal lengths made the rows
drift apart); the terrain was asked for after the fourth live test. No default selection (maintainer, 12 Sep 2026; until then the
lowest-numbered joinable room, else room 1, was preselected): a room is selected only by typing its
number, the map line is empty until then (which disables the client's READY button, F42), and a
stray READY before a selection is refused (§17.3).

Cost: nothing while the rooms do not change; a change is one frame of a few dozen bytes per waiting
client (the scrolling of 7-26 Sep 2026 cost about 0.7 KB/s per waiting client).

### 17.3 Messages from a client in the hall

| Message | Policy |
|---|---|
| `'q'` keep-alive | consume (liveness) |
| `'o'` CD report | drop: the icons mean "joinable" here |
| `'g'` name | own slot only: sanitised, stored as the player's name and echoed like a room does; it follows the player into the room. The own field is the client's anyway (F33) |
| `'f' 'k' 'm'` race, colour, team | **drop, no echo** (R13). The client applies changes only when they come back, so nothing changes on screen |
| `'j' 'l' 'n' 'i' 'p'` | drop (host-owned, as in §6.2) |
| `'h' 2,p` READY | join the selected room (§17.5); with no selection (the button is disabled on the client then, F42) a stray READY is told `Select a room first: /1../7 + ENTER.` and stays; `'h' 1,p` is dropped |
| `'e'` chat | text after the first `':'` (the client's prefix is its row text): a command (§17.4) or hall chat |
| `'y'` INIT_ME | re-send the hall dump; a second one is a strike |
| numeric in-game types, unknown letters | strike (§9.3) |
| deadlines | `JOIN_TIMEOUT_MS` for the first lobby-loop message (the connect-time CD report does not count, F71), `KEEPALIVE_TIMEOUT_MS` afterwards (§9.1) |

### 17.4 Chat commands

| Typed | Effect |
|---|---|
| `/1` … `/7`, a bare digit, `/join N` | select room N: the map line shows it, the rows stay, the header's last line becomes `Room N (<map>) is selected.`; if the room cannot be joined right now a message `Room N: <reason>.` follows |
| `/rooms`, `/list` | one line per room: `"N <map> (k/s) <state>"` |
| `/help` | three rows of at most 40 characters, no full stop (maintainer, 27 Sep 2026): `/1../7 + ENTER selects a room` · `/rooms lists the rooms` · `READY joins the selected room` |
| other `/word` | `"Unknown command /word, try /help."` |
| anything else | relayed to every client waiting in the hall as `"Player<p>: text"` (the sender's real name replaces the row-text prefix) |

### 17.5 Joining a room

Conditions: the room is in LOBBY, has a free seat (`players < map players − fakes`) and the
client's slot `p` is not held by a real player there. A fake sitting in `p` is no obstacle: fakes
have no client, so `Room.relocateFake` moves it to a random free slot (colour and team follow the
new slot) before the client is seated, and the dump sent to everybody afterwards repaints the rows.
Without this, `FAKE_PLAYERS=7` left exactly one free slot per room and a client could join only the
rooms whose free slot happened to be its own. Otherwise Mercenary says why (`a battle is in
progress there`, `it is full`, `your slot p is taken there; reconnect to get another slot`) and the
client stays.

Procedure (`Hall.join` → `Room.adopt(client, p, false)`): the hall forgets the client; the room
fills slot `p` (name `Player<p>`, race 0, colour `p` or the first unused colour, team `p`, status 1),
sends the client the room dump **without** `'d'` (the game's meta-server join path, F30), sends the
dump to the other players as at any join, greets the newcomer, and cancels a running countdown. The
frame decoder and both sequence counters live in the `Client` and continue unchanged; commands that
followed the READY in the same TCP chunk are handed to the room. The player is present-not-ready in
the room present-not-ready, so READY has to be pressed again. Its READY button is still pressed from
the hall and no message can release it (F36), so the first click inside the room sends `'h'(1)`, a
no-op for the server, and the second one readies the player. An interim version (eleventh round)
seated the client ready to make the button state true; the maintainer does not want the automatic
start that comes with it (`MIN_PLAYERS=1`), so it was undone the next day. A READY toggle during the
countdown cancels it (`start cancelled: not everyone is ready`) and readying again restarts a full
countdown; both are covered by a test.

The room dump for a client coming from the hall is one packed frame (F34) that starts with a
`DISCONNECT(q)` for every slot except the client's own and Mercenary's (the hall's seven room rows were
occupied humans; the lobby DISCONNECT handler resets type, status and CD flag and keeps the client's
player count right, while Mercenary's row keeps status 1 so that the client stays in the lobby, F3;
asked for after the third live test, where the count was off), continues with the dump, and ends with
the ten lines of a **fresh chat window** (§17.8) whose header is the room greeting; the hall's chat
is gone with it. A direct join (`HALL=false`) gets the same after its `'d'`.

Slot choice at connect time (`Hall.pickSlot`): for every slot 1..7 count the rooms where it could
join now, subtract the waiting clients that already hold it, and pick randomly among the best. With
the current player numbers a conflict is rare; it is reported as `slot taken`.

### 17.6 Rooms and maps

- `ROOMS` names 1..8 maps (`src/maps.js`, F31). Each room has `map = { file, name, terrain,
  players, titleWire }`; the `'i'` title is built per room in the game's format (F15).
- Capacity: `players` occupied slots at most (F22). Seats for real players = players − `FAKE_PLAYERS`;
  `MIN_PLAYERS` is capped per room at the seat count; `FILL_EMPTY_WITH_AI` acts only on 8-player maps
  (with a smaller map, eight occupied slots would make every client clear the scenario); the config
  refuses a `FAKE_PLAYERS` that leaves no seat on any map. Slot numbers of a 4-player room may still
  be any of 1..7: the game counts occupied slots, it does not require low numbers (F12, F22).
- Size shown: `(players/slots)` with `slots = map players − 1`, the seats a real host would offer
  next to itself. With `FAKE_PLAYERS=7` a room still reads `(0/7)` although one real player fits;
  the second one is told `it is full`.
- Defaults, in room order: Plink - O (jungle), Armageddon, Black Widow, Circle of Friends, Olympus
  Mons (desert), Hoops of Fury, Rings of fire (jungle), all 8-player maps; the jungle map comes
  first (it was the preselected room until 12 Sep 2026; nothing is preselected since).
  fake host in slot 0.
- Every room logs with `room: n`; the hall logs `hall joined`, `hall -> room`, `hall left`.

### 17.7 Real-client tests (7 Sep 2026)

### 17.8 The chat window: no name for the relay, a header that stays

Two rules from the maintainer (7 Sep 2026, after the fifth round): a line the relay itself writes
carries **no name** in front (no `"Mercenary: "`), and the hall and room greetings sit **at the top of
the chat and stay there** while messages arrive. One exception since 12 Sep 2026 (maintainer): the
room header's second line is a greeting **from the fake host itself**, `AI Mercenary: Hi! I am an AI
bot and the host of this game. My base stays idle.` (wrapped at 40 columns by `ChatView`), so that
a newcomer understands what the player in slot 0 is. The fake host's display name is `AI Mercenary`
(`MERCENARY_NAME`) since the same day; "Mercenary" elsewhere in this document means that slot-0 fake.

The client's chat control is a plain ten-line log that wraps at 40 columns and drops lines from the
top (F35), so the server paints it: `ChatView` (`src/chat.js`) keeps, per client, a static header
and the most recent messages, wraps every text server-side at 40 columns (so the client never wraps
anything itself) and renders exactly ten lines, header first, messages below, blanks at the end. Every
chat event (a relay line, a player's line, a room's announcement, `/rooms`) appends to the affected
clients' views and sends each of them its ten lines in one packed frame. What the client shows is
therefore always the render, with the header on top. Entering a room replaces the view (new header,
no old lines). Headers: hall **two rows** since 27 Sep 2026 (`Welcome to Dark Colony server 2.3.` and
`Room <n> (<map>) is selected.`, which reads `No room selected. Type /1../7 + ENTER.` before the
first selection and is rewritten in place on every selection; maintainer: "only two lines in chat:
greeting and map selected" with the hint), room **three rows** (`Room <n>: <map>, <terrain>, <k>
players.`, `Mercenary: Hi! I am the AI host.`, `/help lists the commands.`); the rest is for
messages: eight rows in the hall (`/rooms` shows all seven rooms, `/help` three short rows), seven in a
room. History: from 7 to 27 Sep 2026 the hall header had six rows (the two command lines, `The map
line shows the selected room.`, `You may type your name in your row.` between the two that remain) -
the tenth live test had shown the instruction lines scrolling away under a flood of comments while
the three header lines stayed, and the maintainer then wanted all six to stay; on 27 Sep 2026 the
static room rows made most of them redundant.
`"Name: text"` form, since that is what the player typed; the hall replaces that prefix with the
name the server knows.

Two the same day (§16). First: the hall, the scrolling rows, `/N`, READY into a room and the battle
worked; the own row behaved as F33 predicts (kept the player's name, accepted typing), which became
the design; the speed went to 200 ms. Second: eight rooms wanted, so the selected room moved into the
map line; the size shown became the map's slots without Mercenary; the chat is scrolled clean with a
new greeting on entering a room; and the "fast, then slow" roll turned out to be the client queuing
one-command frames (F34), fixed by one packed frame per update. Still to be seen with real clients:
the packed frames and the map-line display (third test), two players entering the same room through
the hall, and a client whose slot is 7 (its own row is the last one).

## 18. The battle engine beside the relay: checksums from the server (11 Sep 2026)

The maintainer asked for the server to run the battle engine itself and to put checksum commands
into the sync frames. The game's own desync detection is inert on this server (F14: the fake host
in slot 0 is the lowest network id, so no client sends `0x08`), and the relay has no idea what
happens in a battle (no end detection, no position validation). A server-side simulation solves
both, but only if it is **bit-exact**: every client compares a received `0x08` with its own history
and aborts on the first difference (F39), so a wrong checksum ends the game for everybody.

### 18.1 What runs where

```
Room ── Game (relay, unchanged) ──► sync frames ──► clients
  │                                   │
  └── SyncCheck (src/synccheck.js) ◄──┘  feeds every issued frame to the engine, asks it for the
        │                                checksum of the last simulated tick, compares 0x08 from clients
        └── engine.Game (src/engine/)    the port of dc16.exe's simulation core (mem.js memory model,
                                         engine.js game_tick, one module per original source file)
```

- `SYNC_CHECK=off` (default): nothing of this runs. `shadow`: the engine runs beside the relay, its
  checksums are logged/recorded and compared with `0x08` messages received from clients (which
  exist only with `MERCENARY_SLOT > 0`, F40); nothing is sent. `send`: shadow plus one `0x08` per
  sync frame.
- The engine is loaded asynchronously at start-up (`src/enginebridge.js`) and is optional: when the
  module or its data files are missing, or the room's map has no `maps/<BASE>.json`, or the engine
  throws, the room logs `engine disabled` with the reason and goes on as a plain relay. The relay
  never waits for the engine; an exception in a tick disables the engine for that game only.
- Inputs at game start: the lobby slots as every client saw them at the start signal (`Room.startSlots`,
  taken in `beginStarting`), the map JSON (`maps/`), the balance tables (`data/classic/gamestat.json`,
  generated from `GAMESTAT/*.TXT` by `tools/gamestat2json.js`), the exe's constant tables
  (`data/dc16-tables.json`: RNG table, sine quarter wave, spiral, direction tables, city layout) and
  the sprite timing data (`data/classic/sprites.json`, `tools/sprdata2json.js`).

### 18.2 Timing of ticks, commands and checksums (F38, F39)

The client executes the whole frame `UNTIL(a, u)` (marker and commands) at game time `u − 1`, then
ticks to `u`; ticks between two frames have no commands. `SyncCheck` keeps the issued frames in
order and, after frame k is broadcast, runs the engine: ticks up to `u_k − 1` without input, the
frame's commands (everything except `UNTIL`, `0x08` and `TICK`), then the tick that reaches `u_k`.
Frame k+1 may then carry `0x08 (history[u_k], u_k)`: on the receiving client that frame is executed
at game time `u_{k+1} − 1 >= u_k`, and `u_{k+1} − 1 − u_k = n_{k+1} − 1 <= 254 < 256`, so both
assertions of `check` hold. The first frame never carries a checksum (the engine has no tick yet;
tick 0 has no history entry on the clients either).

The `0x08` sits right after the `UNTIL`, before the players' commands (order inside a frame does
not matter to the check), counts against the 1012-byte command budget, and is left out of a frame
whose first queued command group would not fit next to it (the group cannot wait, the checksum can:
the next frame carries the newer tick). Pause: no frames, no ticks. `DISCONNECT(slot)` travels in a
frame like any command and reaches the engine's `0x10` handler at the right tick.

### 18.3 What the engine must reproduce

Everything that can change a position, a hit-point value, an object's existence/type/team, the
missile count or the day/night fields, and every `rand()` call in the original order (the RNG state
is shared by all of it). `docs/DC16_BATTLE_ENGINE.md` describes the code; `src/engine/PORTING.md`
sets the porting rules (memory model with the original offsets, integer semantics, one module per
original source file, `PORT NOTES` per module). The start sequence is part of it: the shuffle (F41),
the player records, the scenario objects, the wildlife placed with `rand()` after the object list.

### 18.4 Verification path

1. Play a game with `SYNC_CHECK=shadow RECORD_DIR=logs/replays MERCENARY_SLOT=7` (the lowest
   real player then sends `0x08` every tick, the server records frames, engine checksums and the
   client's checksums and logs the first mismatch at once).
2. `node tools/replay.js logs/replays/<file>.jsonl` re-runs the engine offline over the recording
   and prints the first tick where it disagrees with the client — the loop for fixing the port
   without a live game. `MREADY`'s game player index is a free check of the start shuffle.
3. Only when recorded games replay without a mismatch: `SYNC_CHECK=send`. A mismatch reported by a
   client during `send` disables the checksums for that game (the client that reported it has
   already aborted; the others survive).

### 18.5 State of the port (11 Sep 2026, night)

All simulation modules are ported and integrated (see the §16 entries of 11 Sep 2026): tables,
scenario/grid, ticker, move/path, combat/missile, commands/city, renat, anim; only sound handles,
the hero-death event's uninitialised bytes and the whole of `ai.js` are stubs. **Verified against
the real game**: three shadow recordings (90 s; 8.5 min with a full base build-up, research,
upgrades, wildlife fights and napalm; a commander rally) replay with 14221 client checksums and 0
mismatches, and in `send` mode two real clients played a full game accepting 2775 server checksums.
Three port bugs were found and fixed on the way (a hotspot call, the cyborg/psy-raider "research
bytes", the ring walk of the rally). Still to be exercised: player-versus-player destruction of
units and buildings, artifacts, more than two players. After a `DISCONNECT` the AI takes over a
base and the engine stops sending for that game (`ai.js` is not ported). `SYNC_CHECK` stays `off`
by default; the verification loop for further work is §18.4.

### 18.6 Recordings that survive on Fly: `RECORD_LOG` (18 Sep 2026)

The first player report of a "sync error" (§16, 18 Sep 2026) could not be examined: the Fly
machine has no volume, `RECORD_DIR` was unset, and `fly logs` shows only the last 100 lines. Fly
does keep the app's stdout for about seven days in its Logs API (F50), so the maintainer's
requirement is that a battle can be rebuilt from the log alone, without flooding the log.

- **What must be kept** (maintainer, 18 Sep 2026: "the full package which has been broadcasted
  including checksums, so we can precisely replay the game locally"): the start header (map, tick
  length, lobby slots, ~800 bytes), every sync frame byte for byte - `UNTIL(a, until)`, the server's
  `0x08 (checksum, tick)`, the commands - and every `0x08` a client sent. Only the file recorder's
  per-tick `engine` lines are dropped: in `send` mode the same values are the `0x08` of the next
  frame, otherwise `tools/replay.js` recomputes them. The compression is in the encoding, not in
  leaving data out: regular frames (until = previous + 1, a = until - LOOKAHEAD - 1) cost nothing,
  a checksum costs 4 hex characters, and the frames' commands are stored as they are.
- **Format** (`src/logrecorder.js`): JSON log lines `msg: "replay"` with `rec` (recording id =
  start time in base 36 + `r` + room; every line of a battle carries it, so several rooms can
  record into the same log at once, and the room's `recording` log line names it), `seq` (0, 1, 2,
  ...; a gap = a dropped line) and `ev`: `start`, `frames` (a chunk: `u0` = until of the first
  frame, `n` frames, `look` = LOOKAHEAD + 1, `x` = `[i, a, until]` of the frames that are not
  "previous until + 1, a = until - look", `s` = `[i0, hex]` runs of the server's `0x08` checksums,
  4 hex characters per frame, for a `0x08` that is the first command and carries tick = the previous
  until, `sx` = `[i, checksum, tick]` for any other first-command `0x08`, `c` = `[i, hex]` the
  frames' remaining commands untouched, `r` = `[tick0, slot, hex]` runs of client checksums, 4 hex
  characters per tick, first sender per tick), `rx` (client runs without a chunk), and the file
  recorder's `mready`, `left`, `note`, `assert`, `mismatch`, `pause`, `resume`, `end` unchanged. A
  chunk closes after 256 frames or 3000 hex characters, so no line exceeds a few KB. `LOOKAHEAD + 1`
  comes from the header, not from the first frame: a recording of 18 Sep starts with `UNTIL(0, 10)`.
  Decoding rebuilds each frame's payload byte for byte (`build.sync` for the checksum head).
- **Cost**: 4-11 KB per game-minute (measured; about 10 in `send` mode), 30-55 lines for a
  ten-minute battle, so an hour-long eight-player battle is roughly 0.6-1 MB and ten of them a day
  about 10 MB, against 602 lines / 119 KB the server logged in the 24 h before (F50).
  `fly logs` (the 100-line tail) gets noisier; filter with `grep -v '"replay"'`.
- **Recovery**: `node tools/logs2replay.js --fetch dark-colony-server [--since 7d] [--raw FILE]
  [--out logs/replays] [--list] [--replay]` pages the Logs API (token from `--token`,
  `FLY_API_TOKEN` or `fly auth token`), or takes files: a `fly logs` capture (text or `--json`), a
  Logs API document or the server's stdout. Each recording becomes `<start>-room<n>-<map>.jsonl`
  in the file recorder's format; `--replay` runs `tools/replay.js` on it. Missing `seq` values are
  reported, the frames of a lost chunk between two chunks are reconstructed empty (timeline intact,
  commands lost) with a `note`; a lost first chunk or start line cannot be repaired.
- **Verified** against the twelve local file recordings: every one round-trips to byte-identical
  frames and identical client checksums (8.8 MB -> 487 KB), and the ones with client checksums
  replay to the same verdict; a 24 h fetch of the live app ran (8 pages, 3 s, no recordings yet
  because the build is not deployed). Still to do: deploy, then fetch a real battle back and replay
  it.

### 18.7 Replay mode: a recording played back into a real dc16.exe (18 Sep 2026)

Maintainer requirement of 18 Sep 2026: save a recording to a file, run the relay server locally,
connect with the real game, and the recorded battle plays as if one were one of its players.
`REPLAY_FILE=<recording.jsonl> node src/index.js` (`src/replay.js`) does that; `REPLAY_SLOT` picks
the seat.

- **Lobby.** The room is built from the recording's `start` header, not from `ROOMS`: the same
  map (the shipped table, or the recorded name and terrain for an unknown file), every recorded
  human as a fake with its recorded name, race, colour and team, AI and empty slots as recorded.
  One seat stays free: that of the recorded real player (`REPLAY_SLOT`, default the first one in
  the header's `players`; a bot's seat is allowed too). The connecting client gets exactly that
  slot, with the recorded race, colour and team; changing them in the lobby is answered with the
  recorded values (`'f'` race, `COLOUR_SET`, `TEAM_SET`) and a chat line. Nobody else can join.
  `MIN_PLAYERS` is 1, no hall, no bots (`MERCENARY_AI=off`), `TICK_MS`/`LOOKAHEAD` from the
  recording, `SYNC_CHECK=send` becomes `shadow`.
- **Start.** READY starts the countdown as usual. The client's MREADY game player index is compared
  with the recorded one for that slot; a difference is logged (the lobby differed, the battle
  will diverge).
- **Battle.** `Game.stepReplay` replaces the frame builder: recorded frame k (`UNTIL(a_k, u_k)` +
  its commands, the original server's `0x08` included) is broadcast byte for byte once
  `a_{k+1} - a_k` ticks of real time have accumulated (normally one; a recorded stall is
  reproduced as a pause of the same length) and nobody is `MAX_LAG` behind. The client's echoes
  and progress reports work as in a live game, so lag and eviction behave normally. Everything
  else the watcher sends is ignored (maintainer, 18 Sep 2026: "completely ignored except exit"):
  orders, chat, gifts, pause/resume and cheat flags are counted per command name and never
  relayed, and none of them is a strike; only leaving (the socket closes) ends the viewing.
  Its `0x08`, sent when it is the lowest network id (recordings made with `MERCENARY_SLOT > 0`),
  is compared with the recorded client checksum of that tick and the first divergence is logged.
  After the last frame nothing more is sent; the watcher quits the game. The room resets when the
  client leaves, ready for the next viewer.
- **Verification is the client's own.** In a `send`-mode recording every frame carries the
  original server engine's checksum; the real game compares it with its own history and aborts
  with "sync error" at the first difference. A replay that runs to the end therefore proves that
  the real game reproduces the recorded battle from the recorded lobby and frames - and a replay
  that aborts shows the tick. Against the *engine*, `node tools/replay.js <file>` on the same
  recording gives the corresponding verdict offline (§18.4).
- **Whole map.** By default the watcher sees the recorded player's fog of war. `REPLAY_FULL_MAP=on`
  sends `CHEAT(0, 0)` (flag 0 = full map view, F28) to the watcher as a standalone frame when the
  battle starts; the client holds a non-UNTIL frame and executes it with the next sync frame (F6),
  so the map is open from the first recorded tick and no recorded frame changes. **Tested 18 Sep
  2026 with the real game: the flag reaches the simulation.** With the option on the replay of the
  maintainer's own battle of 13:05 UTC (Plink - O, 9976 frames) aborted at tick 3944, about three
  minutes in - the game's `error.log` says `sync error: time 3944, net 14336, me 14360` (its
  checksum 14360 against the recorded server checksum 14336) - and the same replay without the
  option ran to the end without an error. So the revealed fog mask is read by something in the
  simulation (the targeting of units that are normally unseen, most likely), which is also why
  debug mode has to give the flag to every client (F28). The option stays available for a short
  look at an opening; a full viewing needs it off.
- **Limits.** The recording must hold every frame from the start (a lost first chunk in a log
  recording cannot be repaired, §18.6). The watcher sees the battle from the recorded player's
  seat and cannot act. Names are not pinned (the client keeps its own; nothing in the simulation
  reads them). Covered by `test/replay.test.js`; **confirmed with the real game on 18 Sep 2026**
  (below, §16).
- **Money is not replayed.** Money is local to each client (the sender deducts it when it issues a
  build command, receivers only book "spent", plan §19), and the watcher never issued the recorded
  player's commands, so its money is never deducted while the income keeps coming: the replay shows
  far more money than the live game had (maintainer, 19 Sep 2026). Display only, money is not part
  of the checksum, and there is no command that could set it.

---

## 19. Alive bots: the fake players play (plan of 12 Sep 2026; §19.8 implemented 13 Sep 2026)

The maintainer asked on 12 Sep 2026 for the server's bots to come alive: AI Mercenary and the other
fake humans (`FAKE_PLAYERS`) should build, harvest, defend and attack instead of sitting idle. This
section is the design; the game's own computer player it is based on is documented in
`docs/DC16_AI.md` (written the same day from the disassembly).

### 19.1 Why the server can do this at all

* **The original AI is nothing but a command generator.** `ai_think` decides and then calls the very
  same command builders the mouse and the build menu use (`0x09` build building, `0x0A` build units,
  `0x07`+`0x05` waypoint orders, `0x05` order `0x0D` deploy). In the original those bytes are executed
  on the spot on every machine (local-command mode, `DC16_AI.md` §3); a server can instead put the
  identical bytes into its next sync frame, and every client executes them for the fake's game player
  exactly as it executes a human's orders. Nothing in the client distinguishes a human's command from
  a bot's (F43).
* **The server already knows the whole game.** Since 11 Sep 2026 the battle engine (§18) runs beside
  the relay in `send` mode and is bit-exact with the clients: objects, hit points, money, vision bits,
  production queues, the path families ("zones") and the routing matrix are all in `room.sync.engine`.
  That is precisely the input Krusty reads (`DC16_AI.md` §8–§13). No new data source is needed.
* **Money is local.** The `0x09`/`0x0A`/`0x0C` handlers book spending but never deduct money; the
  *sender* deducts before sending (build menu `0x43332F`, Krusty `0x4562B0`/`0x456408`). A fake
  player's money therefore exists on no client at all; the engine's copy (`P.MONEY`, fed by the
  income step of every 16th tick and by refunds) is its only ledger, and the bot deducts there (F44).
* **The fakes stay humans on the wire.** No lobby type change, no `DISCONNECT`, no "lost, AI taking
  over": R4 holds. The clients see eight named human players, some of which happen to be played by the
  server. This also keeps the checksum sender rule (F14/F40) and the hall unchanged.

### 19.2 What a bot is

One **Krusty instance per fake player**, running on the server against the engine's game state, with
two substitutions relative to the original (`DC16_AI.md` §17.2):

| Original Krusty | Server bot |
|---|---|
| runs inside `game_tick` on every machine, every 32 ticks per player (`ai_turn`, F45) | runs on the server after `SyncCheck.advance()` has simulated up to `until_k`, when `until_k % 32 == phase(bot)` (the original phase `4 + 4·slot`, so the bots are spread over the window) |
| commands executed immediately via `0x421648` in local mode | commands appended as one group to `Game.queue` → next sync frame → executed by clients and engine at `until_{k+1} − 1` (one frame ≈ 1–2 ticks later than the original) |
| `rand()` = the shared game RNG `0x4120F0` (part of the lockstep) | a private RNG per bot (the engine's RNG must not be touched: it is part of the checksum) |
| `money -= cost` on the local player block | the same on the engine's player block (the only ledger, F44) |
| vision = the AI player's own vision bits (`see_thru` = own team) | the same bits from the engine's load grid (`grid.teamBit(p)`); the bot does **not** see the whole map |

Everything else — zone table, influence map, census, the 18 production goals, the four tasks (workers
to vents, defend, attack, flyer scouting), the group mover — is ported **logically** (same decisions,
same thresholds, same command payloads), not instruction-exact. Bit-exactness is not needed for a bot:
its output is commands, and commands are applied identically everywhere by construction (R6). The
module is written so that the same code can later run in bit-exact mode inside the engine (§19.6).

### 19.3 Placement in the code

```
src/bot/
  krusty.js     one Krusty: state (zones, 4 major tasks × 16 groups, goals, tunables), think()
  influence.js  influence map + zone init (krusty_general.c)
  tasks.js      census, goals/production, worker task, defend task, attack task, bomber task
  mover.js      set_route, new/disband group, group mover (krusty_army.c), hop count
  sink.js       command builders for the bot: 0x07+0x05 (chunked to the frame budget), 0x05 0x0D,
                0x09, 0x0A, 0x0C with the game player index; the "client" of the original
  manager.js    BotManager per room: creates one Krusty per fake slot at game start, drives think()
                from SyncCheck, hands command groups to Game.queueCommands, logs a stats line
```

* `SyncCheck` gets a hook `onAdvanced(tick)` called at the end of `advance()`; `BotManager` thinks
  there for every bot whose phase matches (F45) and only while `sync.active` (engine present, no
  divergence). Commands are queued with `room.game.queueCommands(group)` and go out with the next
  frame like a client's group; the `0x08` still comes first (§18.2).
* Player indices: `sync.slotToPlayer[fake.slot]` (F41). Object ids and coordinates come straight from
  the engine (`objAddr`, `O.X/O.Z` in 1/256 tile; zone centres `tile << 8` as the original, no
  half-tile offset).
* Frame budget: a `0x07` frame with `n` objects and one waypoint is `3 + 4 + 2n` bytes plus `4n` for
  the `0x05` orders; the sink splits a group order into chunks of at most 100 objects so that no group
  exceeds `COMMAND_BUDGET` (1012 bytes); a chunk that does not fit waits for the next frame
  (`Game.step` already keeps groups whole).
* The bots must never read or write anything of the engine except through documented accessors
  (`mem.js`, `grid.js`, `path.js` families/routing, `tables.js`); they never call `G.rand()`. A guard
  in tests asserts that the engine's RNG index and checksum history are unchanged by a think.
* Failure isolation: any exception in a think disables **that bot** for the rest of the game and logs
  it; the engine and the relay are unaffected (same policy as §18).

### 19.4 Behaviour and configuration

Default behaviour = the original Krusty on "easy" (the lobby's two computer types differ only by the
income multiplier `+0x19BC`, which is applied by the harvesting code of every client for AI-typed
players only; a fake human harvests at 1×, so bots get the human income). Settings:

| Variable | Default | Meaning |
|---|---|---|
| `BOTS` | `krusty` | `off` = idle fakes as before; `krusty` = every fake plays |
| `BOT_THINK_TICKS` | `32` | think interval per bot (the original's 32) |
| `BOT_SPLIT_PERCENT` | `75` | share of combat units given to the attack task (Krusty tunable `+0x6C14`) |
| `BOT_CLASS_WEIGHTS` | `1,1,2,4,2,4,2` | the seven class weights of the unit-building goal (`+0x6C18..+0x6C30`) |
| `BOT_SEED` | random | seed of the bots' private RNG (a fixed seed makes a recorded game reproducible) |

Optional later knobs, deliberately not in the first version: a `BOT_HANDICAP` that skips thinks, a
`BOT_MONEY_BONUS` (the bot could grant itself money by simply not deducting — but a fake player's
money exists only on the server, so this would be invisible and unfair rather than a difficulty
setting; leave it out), team play (bots allied with each other through the lobby's team numbers —
possible today with `TEAM_SET`, needs a lobby rule).

Chat: the greeting line of AI Mercenary changes from "My base stays idle." to "I play too." when
`BOTS=krusty`. (The 7 Sep 2026 rule that the relay is nameless in battle was lifted for the
Mercenary on 13 Sep 2026: the maintainer wants every decision of its AI said aloud in the
battlefield chat, §19.8. The relay's own lines stay nameless; the Mercenary speaks as a player.)

### 19.5 Steps

| Step | Deliverable | Done when |
|---|---|---|
| 1 | `docs/DC16_AI.md` (this analysis), plan §19, facts F43–F46 | reviewed by the maintainer |
| 2 | `src/bot/sink.js` + tests: exact payloads of `0x07/0x05/0x09/0x0A/0x0C`, chunking, budget | round-trips through `splitCommands` and `engine.applyCommand` without asserts |
| 3 | `src/bot/influence.js`: zone init (home zone, hop BFS, centres) and influence map over the engine state; `tools/botmap.js` prints the zone table of a room map | zone table of Armageddon matches a hand check (home zone, hop distances) |
| 4 | `src/bot/tasks.js` goals + census + worker task; `BotManager` wired to `SyncCheck` behind `BOTS` | headless self-play (`tools/botmatch.js`: engine + N bots, no clients, commands looped back one tick later) shows HQ, worker, barracks built and workers on vents within the first minutes |
| 5 | defend, attack, bomber tasks and the mover | in self-play 8 bots fight; no engine assert, no dropped command group, think time < 5 ms |
| 6 | live test on the LAN with one real player against 7 bots, `SYNC_CHECK=send RECORD_DIR=logs/replays` | the real client accepts every checksum for a whole game (the bots' commands are ordinary commands, so a mismatch would mean an engine bug, not a bot bug); the recording replays clean with `tools/replay.js` |
| 7 | deploy (`fly deploy`), greeting text, CHANGELOG 2.3 | public game with living bots |

Order of the tasks inside step 4/5 follows the original's dependency: nothing moves without the
census, nothing attacks without the influence map.

### 19.6 Second stage: the same code bit-exact inside the engine

**Done on 19 Sep 2026** as far as the code goes (§19.10): `src/engine/ai.js` runs `src/engine/krusty.js`
in exact mode; what is missing is the verification against a recording of a real game in which the AI
played, so `send` mode still stops on a takeover unless `AI_SEND=true`. The text below is the plan as
written on 12 Sep 2026. Two situations still break the server's picture of the game, both because
`src/engine/ai.js` is a stub (§18.5): a real player's `DISCONNECT` hands their base to Krusty on every client, and a lobby
with computer slots (`FILL_EMPTY_WITH_AI`) runs Krusty on every client. From the first think the
engine diverges and `send` stops — and with it the bots, which need `sync.active`.

The bot module is therefore written with two seams so that it can be promoted to the engine's
`ai.js`: the RNG (`G.rand()` in exact mode, private in bot mode) and the command sink (immediate
`applyCommand` in exact mode, frame queue in bot mode). In exact mode the port must additionally
follow `DC16_AI.md` §17.1: the think runs after `record(t)` and before the next frame's commands;
`ai_think` draws one `rand()` per personality pair (strict `w > r·(cum+w)/32767`); the schedule is
`TICK & 3 == 0`, all AIs at tick 4, then one *slot* per call; every `rand()` of the defend and bomber
tasks in the listed order; the two "minor index used as zone index" bugs of `attack_plan` reproduced;
the type-1/2 personalities' libc `rand()` (`0x44E177`) is not lock-stepped and is only reached from
campaign triggers, so it is out of scope for multiplayer. Verification is the §18.4 loop: a shadow
recording of a game in which a player disconnects must replay clean. This stage is optional for
"alive bots" but it is what makes them survive a disconnect and lets AI-typed slots coexist with
`send`.

### 19.8 The first living bot: AI Mercenary rushes and sells alliances (13 Sep 2026)

The maintainer's request of 13 Sep 2026 shaped the first implementation, which is not the Krusty
port of §19.2-§19.5 but a purpose-built character for the fake host alone:

* **A rusher** (`src/rusher.js`, `MERCENARY_AI=rusher`): a worker first (base income is 3 per 16
  ticks, `P.INCOME`; the economy is the vents: a worker standing on a vent tile for 50 ticks becomes
  a mining tower, `renat.js idleVent`), then the barracks (DEPEND item slot 1 level 0 of the race),
  then the cheapest buildable infantry non-stop (class-0 troop items, at most 3 in the queue), a
  second worker once the first wave is out. The first wave leaves when four armed mobile units stand
  at home; afterwards every pair follows; units are re-ordered every 400 ticks. Orders are the
  original's `0x07` (one waypoint, the object list) + `0x05` order 7 (assault) per unit, `0x05` order
  2 for workers and recalls, `0x09`/`0x0A` for purchases, all with the Mercenary's game player index
  (`sync.slotToPlayer[MERCENARY_SLOT]`). Target: the nearest enemy base (HQ object `15q` while it
  stands, then any standing building `15q+slot`, positions from the objects' raw x/z), else enemy
  units the Mercenary's own vision bit shows (`grid.seenBy`), else nothing (troops go home).
  Allies (lobby team or the deal below) are never targeted; when the target becomes an ally the
  army is recalled. The rusher writes only `P.MONEY` of its player (F44), never calls `G.rand()` and
  a test asserts that a think leaves the RNG index and the checksum history untouched. On Armageddon
  in headless self-play (44 ms ticks): worker at tick 32, mining by ~500, barracks at ~900, the first
  four-unit wave at ~1350 (about a minute), then a soldier every ~200 ticks.
* **The deal** (`src/mercenary.js`): the Mercenary's first sync frame carries its offer in chat; a
  `0x0F(mercenary)` from a client (F47) makes that client's game player its ally: `0x0D(m, c, 0, 1)`
  and `0x0D(m, c, 1, 1)` go into the next frame, the alliance lasts `MERCENARY_ALLY_S` seconds of game
  ticks (`round(s * 1000 / TICK_MS)`), then both bits are cleared and announced. While an alliance
  runs, any `0x0F(mercenary)`, from a third player or from the ally itself, is **returned**:
  `0x0F(payer)` in the next frame and -1000 on the Mercenary's ledger (the gift's +1000 lands when
  its frame is executed, so the ledger nets to zero). The ally leaving the game or the engine stopping
  ends the alliance. Because the bits are per direction (F48), the chat tells the ally to set its own
  alliance and vision towards the Mercenary; the rusher itself stops targeting the ally at once.
* **Chat** (`0x0E from=mercenary, mask 0xFF, "AI Mercenary: ..."`): the opening offer (two lines,
  plus "And I rush." when the rusher runs), every purchase, every worker dispatch, the gathering
  count, each wave and reinforcement, each retarget, each alliance event, and a farewell when the
  engine is disabled mid-game ("I lost sight of the battle"). At most two lines per think (the client
  keeps a queue of six messages); the relay's own lines stay nameless.
* **Wiring**: `Room.mercenary`; `Room.beginRunning` -> `Mercenary.onRunning()` after `sync.start` and
  `game.start`; `SyncCheck.onFrameIssued` -> `onAdvanced(engineTime)` after every frame (also when the
  engine is inactive, so the Mercenary notices a disable); `Game.handle` forwards `0x0F` and calls
  `onGift`; `Room.evict` -> `onClientLeft`. Any exception in a think disables the rusher for that game
  and is logged; the deal and the relay go on. Without an active engine the Mercenary is idle and the
  lobby greeting says so ("My base stays idle."); with it the greeting reads "I rush. Pay me 1000 in
  battle and I am your ally for 120 s."
* **Engine fixes found on the way**: `tables.js loadDepend` wrote `defined`/`params` while `city.js`
  (depend.c) reads `active`/`a`/`b`/`c`, so every `dep_check_*` on real tables answered 2, invisible
  to the checksum (money and the build menu are not in it), fatal for a bot; the loader now writes
  both names. `combat.js objectDie` called `depRecompute(G)` without the player (`0x437D00` uses
  `gs->local_player`, -1 on the server); it now passes it and `depRecompute` returns for a player
  outside 0..7. Neither changes any checksum.
* **Two bots by default** (same day, maintainer: "a permanent second rusher AI, so a single client
  can play against, ally or combine tactics"): `FAKE_PLAYERS` defaults to 2 and every fake human is an
  `AiPlayer` (`src/mercenary.js`: `Bots` = one `AiPlayer` per fake slot; `Room.bots`,
  `Room.mercenary` = the fake host's bot). The second is **AI Marauder** (`FAKE_NAMES`), in a random
  slot like any extra fake. Both run the same rusher and the same deal with their own alliance each;
  they are rivals of each other like of everybody else (the nearest base is the target, so on a small
  map they may well fight each other first). The fake host explains the deal in the first frame; the
  others add "Same deal here: 1000 buys my alliance for 120 seconds. And I rush too. Pick your side."
  Thinks are staggered by 8 ticks per bot. A `0x0F` is routed to the bot whose game player it names.
  The lobby greeting reads "AI Marauder and I rush; 1000 in battle buys an alliance for 120 s." and
  the hall shows the room size without the fakes ("(0/6)" on an 8-player map).
* Not done: the Krusty port (§19.2-§19.6) remains the plan for a smarter bot. Real-client test
  pending (the deal's chat texts, the visibility of a bot's vision on the ally's screen, and whether
  two rushers leave the human enough room are what to watch).

### 19.9 Alliances both ways, victory by alliance, and bots for players who leave (13 Sep 2026)

Third request of the day: "if no other client is in battle anymore, then alliance with all remaining
bots will bring a victory of the battle and end the game; when a client leaves a game, it must be
switched to be the same fake human bot that can be allied with."

* **Victory by alliance is the game's own rule** (F49): the client ends the battle, with Victory for
  everybody alive, once all alive players are mutually allied. For that to work with the deal the
  bots now set **both directions** of both matrices (`0x0D(bot, payer, ·, 1)` and `0x0D(payer, bot,
  ·, 1)`, four commands in one group; the handler has no sender check) and clear all four at the
  end. The payer no longer has to touch the diplomacy screen, the bot's troops stop shooting at once
  (targeting reads the mutual byte), and buying the last remaining bot's alliance while the others
  are still running ends the game. With two bots that is 2000 within two minutes; with one bot left,
  1000. The opening chat says "both ways".
  **Live test 13 Sep 2026 (§16): wrong with two bots.** `game_over` compares every alive player with
  the *first* alive one only. Player 6 bought both AI Mercenary (game player 1) and AI Marauder (7);
  the matrices held 1↔6 and 6↔7 but not 1↔7, and `first` = 1 was not allied with 7, so the battle went
  on. For "ally with all remaining bots = Victory" the bots that share a paying ally must also set
  the alliance between themselves for as long as both deals hold (four more `0x0D` per bot pair, and
  the rushers must treat the other bot as an ally meanwhile). **Implemented the same day as
  "pacts"** (`Bots.syncPacts`, run after every gift, every engine advance and every client leaving):
  two active bots whose current allies are the same player get the four relations between their
  players in the same frame as the second deal; when either deal ends (time, refund never, the ally
  leaving) the pact's four relations are cleared with it. `AiPlayer.isAlly` honours the pact, so the
  rushers stand down against each other and recall their waves. The pact is told to the common ally
  ("AI Marauder and I both serve you now. We hold our fire on each other."), its end to whichever
  ally remains ("My truce with ... is over."). A lone player who buys both bots within the two
  minutes now makes {player, bot, bot} a full clique and the client's check fires.
* **Actions go to the ally only** (maintainer, 13 Sep 2026, after the first live test: "AI bots must
  send their actions only to the allied client"): the in-game chat command carries a player mask
  (`0x0E from, to_mask, text`, bit *i* = game player *i*), so a bot's rusher lines are sent with the
  mask of its current ally and are not sent at all while it has none (`AiPlayer.sayToAlly`, debug
  log "bot: unheard"). The offer in the first frame and a takeover bot's introduction stay public
  (`0xFF`); "X paid 1000", a refund and "the alliance is over" go to the player concerned.
* **The bots keep the peace among themselves** (maintainer, 13 Sep 2026, after the third live test:
  "bots must ally each other by default so there is no war between bots when not hired by anyone").
  **Reversed on 19 Sep 2026** (maintainer: "bots must not ally each other by default"): unhired bots
  are rivals again; the pact below exists only while the same player has hired both bots.
  `Bots.syncPacts` now allies two active bots exactly when they serve the same master: both unhired
  (the default, set in the first frame and whenever a takeover bot appears) or both bought by the
  same player. A hired bot therefore turns on every bot that does not serve its ally ("My truce with
  AI Marauder is over. I turn on it for you.", to the payer), and the peace returns when its deal
  ends. A lone player fights two allied bots; buying one gives an ally against the other; buying
  both wins (F49). The rushers honour the peace through `isAlly`, so no bot ever marches on another
  unhired bot.
* **The rusher defends its base** (maintainer, 13 Sep 2026, same conversation: the bots stay
  rushers, "just tweak it to defend its base when it is attacked"): `Rusher.defend` runs before
  `attack` in every think. Enemy units the own vision shows within `defendRadius` (10) tiles of an
  own building are intruders; while any stand there the rush pauses (no wave, no re-orders), the
  soldiers at home assault the intruder nearest to the HQ, and when they are fewer than
  `defendPerIntruder` (3) per intruder, at least `defendMin` (4), the nearest units on their way are
  recalled to make the number (dropped from `sent`; until the second live test the whole army came
  back for a single scout, §16). Orders are refreshed every `defendReorderTicks`
  (96) or when the nearest intruder moved more than two tiles; a base without soldiers says so once
  per episode and keeps training. When the base is clear ("My base is clear. Back to the plan.") the
  survivors count as fresh and the next think sends them as a wave. Test: two rushers on Armageddon,
  the home guard (rushSize 99) meets the other's rush at its own HQ. 214 tests.
* **A leaving client's base becomes a bot** (`Bots.takeOver`, `AiPlayer` with `takeover: true`)
  instead of `DISCONNECT` (F19) when the bots are configured and the engine is active: the player
  stays a human on the wire (no "lost, AI taking over" on the clients, no Krusty on any machine, so
  the engine stays in step and `send` goes on), its money is normalised like the original's
  DISCONNECT does (`money -= spent`, F44), and the new bot rushes and sells its alliance like the
  fakes. It keeps the player's name on every screen and speaks as `AI <name>`; its first line is
  "<name> left the battle. I run this base now: 1000 buys my alliance for 120 seconds. And I rush."
  A client that leaves while everybody is loading (STARTING) is remembered and gets its bot when the
  battle starts. Without the engine (or with `MERCENARY_AI=off`, or after a divergence) the room
  fell back to `DISCONNECT` until 27 Sep 2026; since then the base stands idle (R14). A bot's `isAlly` also honours a mutual alliance the game
  already has (lobby team, an alliance the leaver had made), so an inherited base does not turn on
  its former allies.
* Not done: the server has no end detection of its own (F49: the clients end the game and leave).
  Real-client test pending.

### 19.10 The Krusty port: the game's own AI plays the bots, one by default, `/botcount N` for more (19 Sep 2026)

Maintainer request of 19 Sep 2026: "the reverse-engineered main AI bot, built into the server; the
count of bots is set with `/botcount`; by default only the bare minimum, one master bot."

* **The port** (`src/engine/krusty.js`, ~1300 lines, one module for `krusty.c`, `krusty_general.c`,
  `krusty_attack.c`, `krusty_defend.c`, `krusty_scout.c`, `krusty_army.c`): the state is a 0x6C40-byte
  Buffer with the original offsets (`DC16_AI.md` §5), the zone table with hop distances and centres,
  the influence map, the census, the 18 production goals, the four tasks (workers to vents, defend,
  attack, scouting) and the group mover, written from `DC16_AI.md` and then checked function by
  function against `dc16.asm` by five parallel instruction-level readings (their findings: `DC16_AI.md`
  §23 - about thirty details the doc had wrong or left out, none of them structural). The original's
  quirks are kept (the group-index-as-zone-index reads of `attack_plan`, the class-as-type formula of
  `group_strength`, the biased reservoirs, the un-cleared fields of `new_minor`, the unbounded goal
  chain); the few places where the original would crash or loop forever are stopped and marked
  `TODO(exact)`. One `ctx` object carries the two seams of §19.2/§19.6: `rand()` and the command sink.
* **Exact mode** (`src/engine/ai.js`, F54): `ai_turn` runs inside `game_tick` after `record(t)` for
  every player with `P.AI_TYPE != 0` - computer lobby slots (`FILL_EMPTY_WITH_AI`) and `DISCONNECT`
  takeovers when the bots are off - with the game RNG and immediate execution through the command
  handlers, exactly the local-command mode of the original (F43, F45). `SyncCheck` no longer disables
  the engine for such games in `shadow` mode (the comparison with client checksums is the verification
  loop of §18.4); in `send` mode it still stops unless `AI_SEND=true`, because nothing has verified the
  port bit for bit yet. The campaign personalities 1/2 are stubs (unseeded CRT `rand()`, campaign only).
* **Bot mode** (`src/krustybot.js`, `MERCENARY_AI=krusty`, the new default): every fake human is a
  `KrustyBot` - the same code with a private walk of the game's `rand()` table (`BOT_SEED`) and the
  commands collected into groups for the next sync frame (0x07 orders chunked to 100 objects, the frame
  budget). It writes only its player's money (F44) and the AI-private object bytes, never the engine
  RNG or the checksum history (asserted in `test/krusty.test.js`). Alliances bought with the deal and
  the bots' pacts are honoured through the game's own alliance bytes (`gs+0x46F54`), which the
  influence map skips - one frame after the `0x0D` lands. The brain is chosen **per room** with
  `/bottype krusty|rusher|random` (`Room.botType`, default `BOT_TYPE=krusty`; maintainer, later the
  same day, after first asking for Krusty always: "so there is a possibility to apply rusher too");
  `random` makes every bot draw krusty or rusher at game start. The `MERCENARY_AI` switch is gone;
  `Bots.configured` is "engine on and not a replay". **Hiring is a per-room switch** (`/bothire
  on|off`, `Room.botHire`, `BOT_HIRE` default off since later the same day): off = no offer, payments
  returned, the bots stay allied with each other and with nobody else. **Repairs in bot mode** (`ctx.fixes`, maintainer
  19 Sep 2026): the upgrade goals work (`0x0C` for the unit type fielded most, level by level) and the
  three group-index-as-zone-index reads of `attack_plan` use the destination zone; exact mode stays
  faithful to the original. Chat: the offer says "And I play the game: base, workers, army, war."; the
  actions ("Training a trsc.", "Worker to the vent at 57,104.", "Attack group 0 marches on zone 68
  (...)", "A guard for zone 83") are not said in battle since later the same day (maintainer: "bots
  must not write its battling actions to chat"); they stay in the debug log.
* **Headless self-play** (Armageddon, two bots, 44 ms ticks): worker at tick 8, first vent at 40,
  barracks at ~870, infantry from ~1100, robot factory at ~2800, science at ~4000, second worker
  and second guard group at ~6500, ~30 units and two attack groups on the move by tick 24 000; 30 000
  ticks in 4.6 s wall time, no engine assert, no Krusty assert. Plink - O likewise. Tool:
  `node tools/botmatch.js [MAP] [TICKS]`.
* **`/botcount N`** (`src/lobby.js command()`, `src/room.js setBotCount()`): a lobby chat line whose
  text after the name starts with `/` is a server command, not relayed. `/botcount N` (1..7, never more
  than the map's slots minus the real players present) adds bots into random free slots - announced to
  every client like a joiner's dump (colour set, name, race, type, team, present-not-ready, F25) - or
  removes the last-named ones with `DISCONNECT`; the master bot in `MERCENARY_SLOT` never goes.
  `Bots` is rebuilt, `minPlayers` recomputed (F22), the pinned chat header repainted ("Bots: 3. /botcount
  N (1..7) sets it."), the start condition re-checked, and a room reset restores `FAKE_PLAYERS`.
  `/botcount` alone lists the bots, `/help` the commands; an unknown `/x` is answered privately.
  `FAKE_PLAYERS` defaults to 1 (F22's cap `MIN_PLAYERS ≤ 8 − FAKE_PLAYERS` still validated at start).
* **Engine fix found on the way** (F53): the exhausted-vent assert and the trigger primitive `m(x, z)`
  read the vent bit z-ordered while the loader sets it through the file-order row table. Both now use
  `grid.ventBitTest/ventBitClear`; the seven D8PLAY01 recordings of 11 Sep replay unchanged.
* **Tests**: `test/krusty.test.js` (zone table, ledger-only writes, 9600-tick self-play, exact mode
  schedule and `rand()` consumption, `aimsg`), `test/botcount.test.js` (add/remove, limits, header,
  reset, hall row, start with the chosen count); 239 tests.
* **Open**: a live game against a real client (the bots' orders are ordinary commands, so a checksum
  mismatch there would be an engine bug, not a bot bug); the exact-mode verification (§18.4 loop with a
  game in which somebody disconnects with the bots off, or with `FILL_EMPTY_WITH_AI`); whether Krusty on
  the multiplayer maps is fun against one human - it builds the standard base and sends 75 % of its army
  at the nearest contested zone, tunable through `aimsg`-equivalent settings if the maintainer wants
  them exposed (`BOT_SPLIT_PERCENT`, `BOT_CLASS_WEIGHTS` of §19.4 are not implemented).

### 19.11 Team bots: `/botteam N` (27 Sep 2026)

Maintainer: "add one more command for lobby. '/botteam N' sets for each client N bots which are
always teamed with client with shared vision until either player looses connection or game ends."

* **Lobby.** `Room.botTeam` (default `BOT_TEAM` = 0, back to it at every reset like the other
  settings) is set with `/botteam N` (0..6) in the room chat; `/botteam` alone reports it. Every real
  player in the room, and every later joiner, gets N **team bots**: fake humans (`fakeSlot(s, name,
  owner)`, `owner` = the player's lobby slot) in random free slots, named from the pool after the free
  bots (else `Bot <slot>`), **on the player's lobby team** (`'n'` = the owner's team; `Room.followTeam`
  re-sends it when the owner cycles its team, `relocateFake` keeps it). They are announced like a
  joiner's dump (a joiner's own dump already carries them) and leave with a lobby `DISCONNECT` when
  their player leaves the lobby or `/botteam` shrinks. `/botcount` counts the **free** bots only
  (`Room.freeBots`); its ceiling `maxBots` subtracts the team bots. Limit: N ≤ ⌊(capacity − free bots −
  players) / players⌋ for the players present; a later joiner gets as many as still fit and is told so.
* **Battle.** The game allies equal lobby teams at the start (`0x4018BB`; matrix 0 = alliance) but sets
  the vision matrix 1 only for AI-typed slots (`scenario.js`, `records[a].type !== SLOT_HUMAN`), so an
  `AiPlayer` with an `owner` builds its **bond** in `onRunning` - `{ player: client.gamePlayer, client,
  name }` - and queues `relations(player, 1)` (the four `0x0D`: alliance and vision, both directions;
  the alliance half is repeated, harmless) into the first sync frame, then tells its player "I fight at
  your side: allied, shared eyes, all battle long." `isAlly` and `alliedPlayer` honour the bond (the
  rusher spares the player, Krusty reads the engine's alliance bytes), `onGift` returns any 1000 with
  "I am X's team bot for this whole battle" (a team bot is not for hire), and `Bots.syncPacts` treats the
  bond like a deal, so the team bots of one player are allied with each other too (equal team anyway)
  and with a bot that player hires. **End of the bond:** the player loses the connection - `onClientLeft`
  queues `relations(player, 0)`, says "X is gone. I fight for myself now." and the bot is an ordinary
  rival from then on (also to the takeover bot or idle base that inherits X's units, R14) - or the game
  ends. A team bot whose player left while everybody was loading starts free.
* **Wire.** Nothing new: `'n'` team values in the lobby, `0x0D` diplomacy and `0x0E` chat in battle,
  all of them commands the original host relays. Tests: `test/botteam.test.js` (lobby allocation,
  joiner, team cycle, leaving, limits, `/botcount` interplay, the bond's commands and its end).
  **Confirmed by the maintainer on the live server, 27 Sep 2026** (deployed as `1d9052e`).

### 19.7 Risks and open points

* **One-frame latency.** A bot's order may reach a unit that died or a slot that was re-allocated in
  the meantime; the handlers write blindly (`cmdOrder` sets pending/order on any slot). A human's late
  click has the same effect and the game tolerates it; the bot additionally re-checks liveness at the
  next think. Not a sync risk: everybody applies the same bytes.
* **Engine dependency.** Bots freeze when the engine is disabled for a room (missing map JSON, a
  detected divergence, AI takeover after a disconnect until §19.6 is done). The room keeps relaying;
  the log says why. `BOTS=krusty` therefore requires `SYNC_CHECK=shadow` or `send`.
* **Original bugs.** `attack_plan` indexes the zone table with the minor index twice, `group_strength`
  uses `gs+0x40` as a defence-class column and the class number as a unit type, `kai+0x6C34` is never
  initialised (so the enemy-avoiding zone route never runs). The bot port keeps the observable
  behaviour where it is harmless and documents each deviation in a `PORT NOTES` block, as
  `src/engine/PORTING.md` prescribes; the exact-mode promotion (§19.6) must reproduce them.
* **Strength of play.** Krusty was tuned for 640×480 single-player maps with campaign triggers
  (`aimsg`) helping it; on the multiplayer maps it will build the standard base and send 75 % of its
  army at the nearest contested zone. Whether that is fun against one human is a live-test question;
  the tunables of §19.4 exist to adjust it, and a smarter successor can reuse the influence map and
  mover unchanged.
* **Performance.** A think is ~800 objects × 8 vision lookups plus zone scans of 256×(neighbours),
  well under a millisecond in Node for one bot; seven bots spread over the 32-tick window add at most
  one think per server step.


## 20. ONLINE WAR: room selection in the game, TLS on 8889, slot chosen at join (29 Sep 2026)

Maintainer, 29 Sep 2026: "MULTI PLAYER WAR set to third button from the top right column.
ENCYCLOPEDIA set as fourth from the top on right column. first button on the right column add
ONLINE WAR, it must lead to the form of room selection based on LOAD GAME form and it must contain
maps from relay server. relay server name must be located in separate file DEFAULT_SERVER.TXT
which supports standard c++ comments and contains dark-colony-server.fly.dev as a server address
and a comment about content and usage. Connection must happen to 8889 port which must use
standard ssh encryption. When map is selected and entering the room, relay must select free slot
for the client, so client slot is not predefined." Decisions the same day: TLS (Windows Schannel
in the exe, the Fly proxy's TLS handler on the server) instead of SSH; Dark Colony Ultimate only;
row 2 of the right column stays empty (reserved for a replay button); one list row per room with
`[map name] [terrain] [seats] [players] [bots] [status]`.

### 20.1 What changes where

| Part | Change |
|---|---|
| Relay `commands.js` | five message types `0x50 LIST`, `0x51 ROOMS`, `0x52 ENTER`, `0x53 REFUSED`, `0x54 ENTERING` (protocol doc §4.4) |
| Relay `hall.js` | `LIST` marks the client (`client.online = true`), answers `ROOMS`, re-sends `ROOMS` on every change of the table (the step timer, `HALL_REFRESH_MS`) instead of lobby rows; `ENTER` joins with a slot chosen now (§20.4); the classic hall (chat commands, READY) is unchanged for stock clients |
| Relay `index.js` / `config.js` | an optional TLS listener `TLS_PORT` (default 0 = none) with `TLS_CERT` / `TLS_KEY` (PEM) for self-hosted relays; on Fly the proxy terminates TLS: `fly.toml` port 8889 gets `handlers = ["tls"]` and forwards plaintext to 8888 (the app's `dark-colony-server.fly.dev` certificate) |
| Relay `tools/fakeclient.js` | `--online [--tls]`: the scripted client speaks the ONLINE WAR dialogue (LIST, pick a room, ENTER) and continues as a stock lobby client |
| Game data | `DC - Council wars/DEFAULT_SERVER.TXT` (§20.2); the room screen script `INTRF_HD/ONLINE` (HD) / `INTRFACE/ONLINE` (640x480), derived from the LOAD GAME picker `LOADGE` by the patcher and by `tools/patch_online.py` |
| Exe (Ultimate) | fix `online` = `tools/patch_online.py`: an appended code section `.dccode` with the compiled C module `tools/online/online.c` (Schannel TLS client, DEFAULT_SERVER.TXT parser, room list screen on the game's own interface engine, loopback proxy thread), plus two edits in `AUTO` (menu id filter 7 -> 8, the 7 NOP bytes at the end of the id chain -> `jmp` into the section); `DC16_DISPLAY_AND_RESOLUTION.md` §10.51 |
| Menu layout | `build_ozi_overlay.py` / patcher `Edit-OziMenu`: right column = ONLINE WAR (8), empty, MULTI PLAYER WAR (3), ENCYCLOPEDIA (5), empty, empty, QUIT (12); new plate gadget 23 and `textmsg 11 ONLINE WAR`; 11 `banim` pairs |

### 20.2 `DEFAULT_SERVER.TXT`

A text file beside the exe, read when ONLINE WAR or REPLAY ONLINE GAME is pressed. `//` line
comments and `/* ... */` block comments are stripped (`//` counts only at the start of a line or
after white space, so `name=https://...` keeps its value), then one field per line (since 3 Oct
2026, maintainer: "it must contain field name=https://github.com/endotermic/Dark-Colony-Server and
address=dark-colony-server.fly.dev"): `name=<text>` (the rest of the line - what both screens show
as "Name:"), `address=host[:port]` (shown as "Server: host:port"; the port defaults to 8889 with
TLS), and the word `plain` on a line of its own (or after the address) switches TLS off for a LAN
relay without a certificate, port 8888 then. Keys match without case, an unknown key is ignored,
`address=` wins over a bare address. The first form (29 Sep 2026: whitespace-separated tokens, the
first = `host[:port]`, optional `plain`) is still read - such a file shows "Name: -". Shipped
content: the two fields above and a comment explaining the file. The patcher writes the file when
it is missing, and replaces an existing file only when it is the first form holding nothing but the
shipped address (comments off, exactly one token, `dark-colony-server.fly.dev`:
`patch_online.bare_shipped_config`, the same test in `Write-OnlineScreen`) - a player's own relay
address is never overwritten; the repo carries the file as game content.

### 20.3 The room screen

The LOAD GAME picker (`intrf_hd/loadg` + `e` = `LOADGE`, code `0x40388C`) is the template: a `list`
widget (monospace `MFONTO5`, 7 px per character), scroll bar, UP/DOWN, two buttons, a title. The
ONLINE WAR screen `ONLINE` widens the list to 448 px = 64 columns, adds a read-only header line
and, under the list, three text lines (`in_text` 49 "Name:", 31 "Server:", 17 the connection state;
two until 3 Oct 2026), titles it "Online War" and labels the buttons ENTER and BACK. One
list row per room, formatted by the relay (so the format can change without an exe rebuild):
`name (24) terrain (9) seats (5) players (7) bots (4) status (9)` with single spaces = 63 columns;
status = `open`, `full`, `starting`, `in battle`. A row is entered with ENTER; a room that is not
`open` is shown and refused by the relay with a reason that appears in the status line. The list is
refreshed from every `ROOMS` frame.

### 20.4 Joining: the slot is chosen at ENTER

`Hall.enter(client, id)`: refuse when the room is not in LOBBY, is full or has no seatable slot;
otherwise `slot = seatable[random]` (never slot 0 = Mercenary, F14; a fake in that slot is moved,
§17.5), send `ENTERING slot`, reset `client.seqOut = client.seqIn = 0`, `room.adopt(client, slot,
true)` (the `'d'` handshake + dump of §6.1). The hall's own `pickSlot()` at accept still runs (the
stock hall needs it) but is irrelevant for an online client.

### 20.5 The exe side in one paragraph

`online_war(ui, gs)` (C, cdecl; the `.dccode` dispatch converts the game's register call): set the
Dark Colony prefix mode and music source ALL exactly as MULTI PLAYER WAR does (`stub_dc_set`
`0x47F340`, `[0x5327F4] = 2`, `gs+0x14F0 = 2`); read the config; `WSAStartup`; resolve and connect;
TLS handshake through `secur32` (`AcquireCredentialsHandleA`, `InitializeSecurityContextA` loop,
`EncryptMessage`/`DecryptMessage`, automatic chain validation against the host name, TLS 1.2+);
send `LIST`; run the room screen with the game's interface calls (`load_interface 0x423248`,
`0x427B44`, `list_set 0x42A5B8`, `list_selected 0x42A828`, `set_text 0x423ED4`, the event pump
`0x42417C`: kind 1 = button, 7 = list selection; `unload 0x423210`; pool bookmark `0x40C0FC` /
`0x40C26C` around it like the picker); on ENTER send `0x52`, read until `ENTERING`; open a
listening socket on `127.0.0.1:0`, start the proxy thread (`CreateThread`), call `0x40122C(ui,
{port, "127.0.0.1"}, gs, 0, 0)` - the stock network entry that connects, runs the lobby and the
battle - and, when it returns, close the listening socket (the thread ends when either side
closes). Errors show the game's own LOST dialog (`0x410460`, e.g. 11 UNABLE TO CONNECT) or a
module message in the status line. Every Windows function but `LoadLibraryA`/`GetProcAddress`
(IAT `0x4804B0` / `0x480480`) is resolved at run time, so the exe imports nothing new.

### 20.6 Test plan (done 29 Sep 2026 except the last item)

Unit tests for the messages and the hall (`LIST` -> `ROOMS`, refresh on change, `ENTER` -> random
seatable slot, refusal reasons, sequence reset, a stock client unaffected). `fakeclient.js
--online` against a local relay and against Fly over TLS. The game: a local relay (`plain` in
`DEFAULT_SERVER.TXT`) for the screen and the join; the Fly relay for the TLS path; a stock
Ultimate client joining the same room to see both slot assignments.

**Hand-over race fixed the same evening (F81, §16 entry of 29 Sep evening):** the module is silent from `ENTER` to the answer and the relay drops its stragglers; Ultimate 1024x768 is **`d970597f…`** since then. Proof: a fake relay that holds `ENTERING` for 3 s (`fakerelay.py`) - old exe 4 keep-alives after `ENTER`, rebuilt exe none - and the rebuilt exe in the lobby of the new local relay for 18 s.

### 20.7 The module's third entry point: LOAD GAME (3 Oct 2026)

Not a relay feature, but the same appended `.dccode` section: `load_game_picker` replaces the stock LOAD GAME
routine's one-folder picker call (`0x403ABC`) and lists the saves of `save\`, `esave\` and `ozisave\` together -
the one LOAD GAME button of the five-row menu; LOAD switches the mode to the chosen save's folder and the stock
code resumes the campaign. Details and the save header layout: `DC16_DISPLAY_AND_RESOLUTION.md` §10.67, §16 entry
of 3 Oct 2026.

## 21. REPLAY ONLINE GAME: recorded battles watched from the game, a participant's seat chosen in the exe (2 Oct 2026)

Maintainer, 2 Oct 2026: "it's time to create a replay functionality for server and Ultimate executable.
Ultimate must contain 'REPLAY ONLINE GAME' in main menu right under 'ONLINE WAR'. it must bring up the
similar form as 'ONLINE WAR' does but with correct headers and modified. it must be possible to select
from client list of participants (8 radio buttons with client names on the right pane of form). By the
way, all bots on the server must have 'AI ' prefix for their names"; then "create the volume and go
ahead with the replay design. List row format: [date time] [map name] [map type] [map slots count]
[players] [AI count] [duration]. keep last 50 replays." Decisions: a 1 GB Fly volume for the
recordings (the machine's root file system is lost on every deploy); every battle with at least one
sync frame is offered; the viewer takes a participant's seat (real player or bot) and sees that
player's fog of war; playback at the recorded speed, no pause or fast-forward (that would need more exe
code); the list is 40 columns because the participant pane needs the right 150 px of the form.

### 21.1 What changes where

| Part | Change |
|---|---|
| Fly | volume `dc_replays` (1 GB, `iad`, encrypted, daily snapshots) mounted at `/data`; `fly.toml` `[env] RECORD_DIR = "/data/replays"`, `REPLAY_KEEP = "50"` |
| Relay `recorder.js` | `Recorder.onClose(file)` hook |
| Relay `replays.js` (new) | `Replays`: scan `RECORD_DIR` at start-up, `onRecorded(file)` when a battle ends, `prune()` = keep the newest `REPLAY_KEEP` battles, delete older ones and files without a battle once they are a day old (a younger one may be the recorder's open file); `describeRecording` = map, terrain, seats, real players, bots (fakes + AI slots), the eight names, the real-player mask, the length from the last frame's `until` × `tickMs`; `rowText` / `HEADER` = the 40-column row |
| Relay `commands.js` | `0x55 RLIST`, `0x56 REPLAYS`, `0x57 REPLAY`, `0x58 RPLAY`, `0x59 REPLAYING` (protocol doc §4.5); `MAX_REPLAY_ROW` 40 |
| Relay `hall.js` | `RLIST` marks the client (`online` + `replays`: no lobby view, no room table) and sends the list in packed frames; `RPLAY` → `watch()`: `loadReplay(file, { slot })`, `pool.openViewerRoom`, `REPLAYING`, the ENTER hand-over (counters reset, `handover`, join grace), `room.adopt`; refusals for an unknown id, a non-human slot, an unloadable file |
| Relay `rooms.js` / `room.js` / `replay.js` | `RoomPool.viewers` beside the fixed rooms (`all()` drives step, watchdog, engine, clients), `openViewerRoom` / `closeViewerRoom` (ids 101..200), `Room.onEmpty` after the reset of an emptied room, `replayConfig(cfg, replay, { viewer: true })` = `SYNC_CHECK off`, no `RECORD_DIR` / `RECORD_LOG`, the recorded `TICK_MS` / `LOOKAHEAD` |
| Relay `config.js` | `REPLAY_KEEP` (1..250, default 50); `MERCENARY_NAME` `AI Mercenary`, `FAKE_NAMES` with the `AI ` prefix |
| Relay `client.js` | `client.replays`; `isModuleFrame` also `RLIST` / `RPLAY` |
| Relay `tools/fakeclient.js` | `--replay [ID] --slot S`: the dialogue, then a stock lobby client of the viewer room |
| Game data | `HD_<height>P\REPLAYE` + `REPLAYBG.GIF` (`INTRFACE\` at 640x480), derived from the ONLINE script / LOADER.GIF by `patch_online.py` and the patcher (`Edit-ReplayScript`, `Write-OnlineScreen`); `HD_SRC\KNOBR.SPR` (the radio-box bank, `patch_online.py bank`, the fix's `Data`); the menu script's right column row 2 |
| Exe (Ultimate) | fix `online`: `cmp edx,9`, `online_dispatch` for ids 8 and 9, `replay_game` = `run(ui, gs, 1)`: the REPLAYE screen, `RLIST`, the list + pane, `RPLAY`, the `REPLAYING` hand-over; `checkb` radio logic (F82) |

### 21.2 The viewer room

`RoomPool.openViewerRoom(replay)` is the §18.7 replay room made per viewer inside a live relay: the
recorded lobby (every other recorded human a fake with its name, race, colour and team; AI and empty
slots as recorded), the viewer's seat free and pinned, `MIN_PLAYERS` 1, the recorded tick length, the
engine off (the frames carry the original server's `0x08`, and a viewer must not cost an engine), no
recording of the viewing, bots idle. READY starts the countdown, the recorded frames go out byte for
byte at the recorded pace (`Game.stepReplay`), everything the viewer sends but echoes, progress
reports and keep-alives is dropped. **When the recording ends the relay closes the connection**
(maintainer, 2 Oct 2026, after the first Fly test: "when replay ends then relay must close a
connection"): once the viewer's progress report has reached the last recorded tick, or
`REPLAY_END_GRACE_MS` (10 s) after the last frame went out, the watchdog evicts it with `replay ended`
and the game returns to its menu. When the viewer's socket closes the room resets and `onEmpty`
removes it from the pool. **Slot 0 is never a seat** (`HOST_SLOT`: `hall.watch` refuses it, the real-player mask never has
bit 0, the exe never ticks its box): the game treats slot 0 as the lobby host, whose client controls
the map and the lobby options - a viewer there could change the settings of the viewer room
(maintainer, 2 Oct 2026: "disable possibility to watch replay for slot 0 because that would give a
control to the customer over settings in lobby"). Its name is listed - the master bot's - beside a
greyed, dead box: KNOBR cell 150 = the empty box in the screen palette's greys, used for both states
of slot 0's `checkb`, so the engine's toggle on a click shows nothing and the module undoes it
(maintainer, same day: "slot 0 must contain correct name of ai master as before, but its radio button
must be grayed out and unclickable"). Several viewers can watch at once, each
in a room of their own; the fixed rooms are untouched (the hall's rows and `ROOMS` list only them).

### 21.3 The screen

REPLAYE = ONLINE (§20.3) with the list narrowed to 40 columns (320 px; the relay formats the rows and
the header), the scroll bar following it, and right of the scroll bar the participant pane in its own
grey frame: eight `checkb` rows (`HD_SRC\KNOBR.SPR` = KNOBE + an empty box as cell 149, so an unticked
seat shows an empty box, not the lobby's "?"; ticked = KNOBE's green cross 8) 30 px apart with a
13-column read-only name field each and the heading WATCH AS. A checkb toggles itself on a press and
the pump reports kind 2 / 3 with the box's id (F82): the module then sets all eight boxes to its own
choice, so they are radio buttons; selecting a list entry fills the names and clears the boxes, and the
REPLAY button is greyed (`set_greyed 0x424574`) until a seat is ticked (maintainer, same day). REPLAY
sends `RPLAY` for the selected battle and the ticked seat.

**Money in the viewer's seat (F83).** Purchases are deducted from `MONEY` only on the issuing machine
(the handlers book `SPENT` everywhere), so a viewer's balance would only grow. The module's proxy
thread polls every 100 ms while a replay runs and subtracts every increase of the local seat's
`SPENT` from its `MONEY` - the same normalisation the game's `DISCONNECT` handler applies to a left
player's base. Only the module's replay path has it; `REPLAY_FILE` through MULTI PLAYER WAR does not.

### 21.4 Test plan

Unit tests (`replays.test.js`): the messages, the index (names, mask, duration, newest first, stable
ids), the retention (battles beyond 50 deleted, stale non-battles deleted, fresh ones kept), the hall
dialogue into a viewer room that plays and closes, an empty list without `RECORD_DIR`, the bot names.
Patcher: clean-copy runs under pwsh 7 and 5.1 = fixtures. Game: a local relay with a recording, the
REPLAY ONLINE GAME screen, a seat chosen by radio box, WATCH, the battle plays; Fly after the deploy.

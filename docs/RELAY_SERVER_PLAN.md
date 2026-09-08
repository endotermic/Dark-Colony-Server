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
| R4 | Slot 0 = fake human player **"Mercenary"**, always a fake human (never handed to the AI) | Server emulates the host client for slot 0 (name, race, colour, team, status). `FAKE_PLAYERS` (1..7) adds further fake humans in random slots, e.g. 7 fakes + 1 real player |
| R5 | Ignore command `0x08` (checksum) | Dropped, never queued |
| R6 | Very strict sync: commands only travel inside the frame that carries the `0x02` sync command; identical bytes, identical order for everyone | One sync frame per server step: `[UNTIL][cmd…][0x00]`, broadcast byte-for-byte to all clients (only the per-connection sequence nibble differs) |
| R7 | Start the game with the READY button (the chat word `ready` of the first design was dropped on 7 Sep 2026: the button works for every joiner, the eighth slot included) | When every real player has status 2 and there are at least `MIN_PLAYERS`, a countdown runs and the server frees the fake slots from status 1 (`'h'(0, q)`), which makes every client leave the lobby (§6.3) |
| R8 | Default map **ARMAGEDDON** (8-player desert) | `'i' "D8PLAY01.SCN", "Armageddon"` |
| R9 | Random placement for every new game | Joiners get a random free lobby slot; see §7 for why this is the only lever |
| R10 | Disable cheats by not broadcasting | `0x0E` cheat texts, `0x04` flag toggles, `0x0F`, `0x03` are dropped |
| R11 | 200 % game speed, clients cannot change it | Server sends `TICK_SPEED(33)` itself and drops `0x11/0x12/0x13` from clients |
| R12 | Clients may drop out or misbehave; a client that does not answer every message correctly is removed and everybody is told it left the lobby or the battle | Per-phase expected answers, deadlines and violation rules (§9); the eviction broadcasts `'h' 0` + `DISCONNECT` in the lobby and a `DISCONNECT` inside the next sync frame in battle |
| R13 | Seven rooms, each with its own map, chosen by the player inside the game's own lobby screen (added 7 Sep 2026, version 2.1) | A room-selection lobby ("hall", §17): the seven player rows that are not the player's own show the rooms, numbered 1..7 in place (F33), with the map name, player count and availability scrolling after the fixed number; the map line repeats the selected room; chat commands select a room, READY joins it. The name may be typed in the hall and follows the player; race, colour and team cannot be changed there |

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
| F11 | Options screen speed percent → `TICK_DESSPEED(6600 / percent)`; only `0x11 TICK_SPEED` changes `gs->tick_ms`; clients compute `TICK_SPEED` from the `TICK_MAXSPEED` reports they receive. | `0x432CD3`, `0x41DD6C`, `0x419830` | **200 % = 33 ms** per tick (the game's own minimum, `0x419883`). Server sends `TICK_SPEED(33)`; client `0x11/0x12/0x13` are dropped |
| F12 | Start positions: at game start each client seeds the game RNG from a global that is never written (always 0), lists lobby slots with type ≠ 3 in ascending order into 8 entries padded with −1, Fisher–Yates-shuffles `N = filename[1] - '0'` entries, and the position of a slot in the shuffled list is its game player index (= start location). | `0x4014F8`–`0x40159F`; RNG `0x4120E0/0x4120F0`, table `0x488F20` | The shuffle is the same every game. Randomness can only come from **which lobby slots are occupied**. With `k` occupied slots the same `k` start locations are always used and only the assignment of players to them varies, unless all 8 slots are occupied (§7) |
| F13 | Cheats: in-game chat `0x0E` text after `':'` equal to `we need equipment`, `I'm fighting for that equipment`, `slag net`; `0x04` with `a ∉ {1,2}` toggles debug flags; `0x0F` = +1000 P7; `0x03` spawns objects and has no legitimate sender. | `0x41DA2C`, `0x41CE9C`, `0x41DBB0`, `0x41CF08` | Drop them |
| F14 | `0x08` sync check; sent every tick by the connected human with the **lowest network slot that is not marked lost** (`0x419F1B`–`0x419F54`). With Mercenary in slot 0 that is always Mercenary, which has no client, so **nobody sends checksums** and the game's own desync detection is inert. A receiver that detects a mismatch prints "sync error" and fails an assertion, i.e. terminates (`0x44AC94`). | `0x41CE74`, `0x419F4B` | Drop (R5), always. Decision of 7 Sep 2026: checksums are neither generated (fake human in slot 0) nor forwarded, the game runs fine without them; the temporary diagnostic flags used for the two-player sync test were removed |
| F15 | `'i'` carries the scenario file name relative to `scenario/mplayer/` (client formats `"scenario/mplayer/%s"`) and the title. **The title is not free text**: the host builds it with `sprintf("%-43s (%d Player %s)", name + "\n", players, "Desert Map ")` (format string `0x48319C`, terrain strings `0x483190`), and every lobby client reads `title[45]` as the map's player count (`0x41141F`): when that digit is smaller than the number of occupied slots the client clears the scenario and un-readies everybody (`0x41143B`–`0x41144C`). The file name's 2nd character is read as the player count at game start (`0x401504`). | `0x40FB5C`, `0x410733`, `0x41141F` | File `"D8PLAY01.SCN"`, title `"Armageddon\n" + 32 spaces + " (8 Player Desert Map )"` (66 chars, `(` at index 44). A plain "Armageddon" made every real client send `'i' "", ""` plus un-ready messages a second after joining (live test) |
| F16 | Names: 16 chars + NUL. Buffers: lobby receive 1416, in-game receive 1024, held commands 7168. | `0x41F701`, `0x41E65B` | Frame budget in §8 |
| F17 | Original server constants: tick 66 ms, look-ahead 8 ticks, stall when `until − min(clientTime) ≥ 200`, ≤ 255 ticks per step, `DISCONNECT(slot)` in-game → AI takes over. | `0x40B7CC` | Reused, with tick 33 ms |
| F18 | Client sockets are blocking with Nagle on; lobby clients send `'q'` every 700 ms; in-game they echo every `UNTIL`. | §2.1/§6.1 of the protocol doc | `setNoDelay(true)` on the server; liveness = "bytes seen recently" |
| F19 | The in-game `DISCONNECT`, `TICK_SPEED` etc. are ordinary held commands (first byte ≠ 1/2/4), i.e. they execute at the next `UNTIL` boundary. | F6 | Put them inside sync frames like any other command |
| F20 | Leaving the lobby does not release the colour lock: the lobby `DISCONNECT` (`0x40F5B0`) and `NUKE` (`0x40EF84`) handlers reset type, status and CD flag but never touch the lock table `ss+0x248`. Only an `'h'` with status 0/1 for a slot that was ready clears its colour's lock (`0x40F315`); the whole table is cleared only when the lobby screen is entered (`0x410D4A`). | all writes to `+0x248` in setup.c | If a ready player disconnects, its colour stays locked on every remaining client and nobody with that colour can become ready. The server must broadcast `'h'(0, slot)` **before** `DISCONNECT(slot)` |
| F21 | An unknown message type in the lobby lands on the dispatch table's default entry, an assertion (`0x40F560`, setup.c:915) that terminates the game (`0x47C02E` → `0x47BEB7`). | table `0x488E54` | Send only the 17 known lobby types to a lobby client. In-game, unknown types are merely dropped (`0x41E6CB`) |
| F22 | The lobby loop also clears the scenario and un-readies everyone when more slots are occupied than the scenario's player-count digit allows. | `0x41141F`–`0x41144C` | Cannot fire with an 8-player map and 8 slots; if `MAP_FILE` is ever changed, its 2nd character must be ≥ the number of occupied slots |
| F23 | What a healthy client sends on its own: `'o'(8+p, cd)` right after it accepted `'d'`; `'q'` every 700 ms, but only from the lobby loop, so nothing while it loads the map; `'v' MREADY` once after leaving the lobby; in-game an echo of every `UNTIL` before executing anything, and `UNTIL(-1, u)` when it reaches `u`. Verified live: the first message is indeed the CD report, the outgoing sequence starts at 0 and increments per frame (including keep-alives), several commands are packed into one lobby frame, and in battle the client sends `TICK_MAXSPEED` and `TICK_SPEED(66)` about once a second. | `0x410A10`, `0x411C2A`, `0x41E544`, `0x41CDC7`, live log | These are the answers §9 waits for, phase by phase |
| F24 | `MREADY` carries the **game player index** after the client-side start-position shuffle, not the lobby slot (`gs+0x7D3C`, `0x401986`); the original server only checks the state byte. | `0x401992`, live: slot 5 reported player 1, slot 2 reported player 7 | Accept any player 0..7, require state 2 and no duplicate |
| F25 | Lobby init on every entry (`0x410C48`–`0x410DA0`): all 8 slots get type 3, colour 0, race 0, team = index, lock flag 0, and the colour-lock table entry is cleared; then slot 0 status := 1. Colours are set only by `'l'`, which applies unconditionally (`0x40F030`), as do `'n'` team (`0x40F0A0`) and `'f'` race (`0x40EE10`). Wire order of these two-byte messages is (value, player). | disassembly | The join dump must carry `'l'` for every slot (it does); default colours are all 0 until then |
| F26 | The lobby chat handler appends a line to a 2 KiB buffer and word-wraps at the 41-column chat window (`0x40ECE4`); long lines are safe. The chat input allows 255 characters (`intrface/MULTIE`). | `0x40ECE4` | Mercenary's lines may exceed 41 characters |
| F27 | Only Classic `dc16.exe` plays over the network; the Council Wars executable has no working network play. | maintainer | All clients are the same build; the "mixed builds" risk does not arise |
| F28 | `CHEAT(a, b)` with `a ∉ {1,2}` toggles the client flag `gs+0x46F50+b` (`b` 0..3, handler `0x41CE9C`); the chat cheat "slag net" toggles flag 0 the same way (`0x41DB9E`). Flag 1 is the pause flag, flag 2 has no readers, flags 0 and 3 are tested at the end of the fog-of-war mask routine (`0x445A77`, `0x445A89`), which then reveals everything (`0x4457D0`): **flag 0 = full map view**. All four flags are cleared at game init (`0x40C40F`) and stored in save games. | disassembly, confirmed live 7 Sep 2026 | Debug mode puts `CHEAT(0, 0)` into the first sync frame, so every client sees the whole map from the first tick (the maintainer confirmed the full map was visible from the start) |
| F29 | The lobby screen (`INTRFACE/MULTIE`): the eight player-name fields are `in_text` controls of **16 characters** (`x=247`, rows 19 px apart) with the `immediate` flag (the own field is editable, every keystroke goes out as `'g'`); the type and race columns are 6-character read-only fields derived from the slot's type and race values, not free text; the map line (`in_text 26`) is 55 characters wide; the chat window is 41 columns × 10 lines, the chat input holds 255 characters. | `INTRFACE/MULTIE` lines 35–51, 113–123, 181–217 | The only free text per row is the 16-character name. Longer texts scroll through it (marquee, §17.2). In the hall the own row is a room row too, so name edits are dropped there (R13) |
| F30 | `'d'` is **not** in the lobby dispatch table (`0x488E54`: `0x01 'e' 'f' 'h' 'g' 'i' 'j' 'k' 'l' 'm' 'n' 'o' 'p' 'y' 0x10 'q'`); it is read only by the join wait (`0x4108DB`). An unknown type in the lobby terminates the game (F21). The meta-server join path receives the state dump **without** `'d'` after `'y' INIT_ME` (protocol doc §6.2), and every existing player receives the full dump again on each join (live, 6–7 Sep 2026). | table `0x488E54`, protocol doc §4.2/§6.2, live logs | A client's slot number is fixed for the whole connection: it can only join a room where that slot is free (§17.5). A second full dump (new `'i'`, new rows) in the middle of the lobby is exactly what the game was built to accept |
| F31 | Every multiplayer map file `SCENARIO/MPLAYER/*.SCN` starts with three strings: the terrain file (`desert.bts`, `jungle.bts`, `atlantis.bts`), the base name and the **display name** the game's own host puts into the `'i'` title. Classic ships 56 of them: 10 desert and 9 jungle 2-player, 10 desert and 7 jungle 4-player, 2 jungle 6-player, 10 desert and 7 jungle 8-player, plus one 2-player Atlantis map. | the game folder, 7 Sep 2026 | `src/maps.js` holds the table (generated from the files); `ROOMS` lists maps by file name and the server builds the title from the table (F15) |
| F32 | The client has a dormant meta-lobby: when the first frame is `'r'(9, id)` instead of `'d'` it enters a screen defined by `INTRFACE/METAE` (a 392×258 scrollable list, an 11-character name field, MENU and READY buttons) and speaks the `'r' 's' 't' 'g' 'u' 'v' 'w'` protocol (protocol doc §4.2); the server half never existed in the game. | `INTRFACE/METAE`, protocol doc §4.2 | Considered as a native room browser and **not used**: never exercised, unknown UI state. The hall (§17) reuses the normal lobby screen instead |
| F33 | The lobby `'g'` NAME handler (`0x40F398`, dispatch table `0x488E54`) copies the name into the slot record (17 bytes, `0x406948`) and then repaints the row's text field (`0x423E74`) **only if the player is not the client itself** (`ss+0xA254`). The own name field is an editable `in_text` with the `immediate` flag (F29): every keystroke goes out as `'g'` and the field shows what was typed regardless of what the server answers. | disassembly `0x40F3FC`–`0x40F415`; live 7 Sep 2026: the own row never showed the hall's room text, typing changed it | The server cannot paint the client's own row and cannot stop the player from typing there. The hall therefore shows the player's name in that row and accepts name changes (they follow the player into the room); seven rows remain for rooms, hence `MAX_ROOMS = 7` |
| F34 | The lobby loop reads **at most one frame per iteration** (`0x411132`: one call of the frame reader, not a loop; on data it calls the dispatcher `0x40F918` and goes on with the iteration), and the dispatcher runs **every command of the frame** (loop at `0x40F929`–`0x40F975`: type byte → table `0x488E54` → handler, until the `0x00` terminator; an unknown type is "SETUP_COMMANDS BAD"), then calls `0x40F6AC` once per frame. The iteration rate is that of the UI loop. | disassembly; live 7 Sep 2026: seven one-command frames per 200 ms scrolled fast for a moment and then slower and slower (the client fell behind and queued frames) | Send one **frame** per update with all its commands inside: the marquee step, the hall dump, the room dump. Multi-command lobby frames are also what the original server relays when a client packs `'o'`+`'q'` (F23). `PACK_LOBBY_FRAMES=false` restores one command per frame |
| F35 | The lobby chat handler (`'e'`, `0x40ECE4`) copies the chat control's current text (control 24, up to `0x800` bytes) into a local buffer, appends `"\n"` + the received string, word-wraps by inserting `'\n'` at the last space once a line reaches **width − 1 = 40** columns (control width from `0x423F24`), and then, while the text does not fit the control (`0x424608`), **drops the first line**. The control shows the last ten visual lines; nothing else is kept. The received string is appended as it is: the `"Name: "` in front of a player's line is the sending client's own convention, not something the handler needs. | disassembly `0x40ED15`–`0x40EDE5`; F26, F29 | The server can paint the whole window: ten lines of at most 40 characters replace what is shown. Lines from the relay carry no name (maintainer, 7 Sep 2026). The greeting is kept at the top by repainting the window on every chat event (§17.8) |
| F36 | The big READY button is `checkb 133` (`INTRFACE/MULTIE` line 351), a checkbox with its own pressed state. Its click handler (`0x4115A9`–`0x411636`) sends `'h'(2, own)` on the "checked" event and `'h'(1, own)` on any other event; the only code that changes the button's state is the click itself and the client-side refusal when the own colour is locked (`0x4272A8(ui, 0x85, 0)` at `0x411608`). The `'h'` message handler drives the **row** checkbox `16 + player` only (`0x4272A8(ui, 0x10 + player, status == 2)` at `0x40F38B`); the lobby refresh only enables or disables control 133 (`0x424514` at `0x40FF96`/`0x40FFC5`). No lobby message reaches the button's state. | disassembly; all five logged room entries of 7 Sep 2026: the first READY press inside the room sent status 1, the second status 2 | After READY in the hall the button stays pressed and the server cannot release it. The room therefore seats a client from the hall **ready** (status 2, F20 lock check applied), so that the pressed button is true; one click un-readies for colour or race changes. A direct join (`HALL=false`) is present-not-ready as before |

---

## 3. Architecture

```
Dark-Colony-Server/     (repository root)
  package.json          "type": "module", "engines": { "node": ">=20" }, scripts: start, test, fakeclient, lint
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
    fakeclient.js       scripted dc16 client: library for the integration test and a CLI (§13)
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
- The server never simulates the game. It only relays, orders and paces.
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
6. Close/error/timeout → free the slot and broadcast `DISCONNECT(slot)`. In the lobby, if the player
   was ready, broadcast `'h'(0, slot)` first so every client releases its colour lock (F20), then
   `DISCONNECT(slot)` as a standalone frame. While RUNNING, `DISCONNECT(slot)` is queued into the next
   sync frame (F19). Exclude the slot from `min(clientTime)`. If no real client remains → `room.reset()`.

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
- `MREADY_TIMEOUT_MS` (default 30 s): clients that never report are evicted (`DISCONNECT` queued for
  the first sync frame, their base becomes AI on everybody else's machine). Evicting the last missing
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
- `TICK_SPEED(33)` is queued again every `SPEED_REFRESH_S` (default 30 s) and after every resume;
  it is idempotent for the client (F11) and guards against a missed first frame.
- Every broadcast sync frame registers a pending echo for each client (`pendingEchoes.set(time, now)`);
  the echo deadline and the stall handling are described in §9.

### 8.3 Handling of frames received from a client while RUNNING

| Type | Policy |
|---|---|
| `0x02 UNTIL(a, u)` | consume. `a ≠ -1`: echo of our frame; `a` must be in the client's `pendingEchoes` (else hard violation, §9), remove it and keep `now − sentAt` as the latency sample. `a == -1`: the client reached `u`; `u` must be an issued `until` and greater than its previous report (else hard violation); set `clientTime[s] = u` |
| `0x04 CHEAT(1,·)` / `(2,·)` pause / resume | if `ALLOW_PAUSE` (default true): set `paused`, relay **as a standalone frame immediately** (the client handles pause out-of-band and discards the frame, F6). Otherwise drop |
| `0x04` other, `0x03`, `0x08`, `0x0F` | **drop**, log (R5, R10) |
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

- Client lost → `DISCONNECT(slot)` queued (F19); its `clientTime` no longer gates pacing.
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
| LOBBY, joining | `'d'` + join dump | its CD report `'o'(8+p, cd)`, the first message of every joiner | `JOIN_TIMEOUT_MS` = 5 s | F23 |
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
   - **STARTING**: `DISCONNECT(slot)` is queued for the first sync frame. The others are loading and
     no longer read lobby messages, and the in-game loop would hold a `0x10` frame until the first
     `UNTIL` anyway (F19). The slot is removed from the `MREADY` wait list, which may complete the
     transition at once.
   - **RUNNING**: `DISCONNECT(slot)` is queued into the next sync frame, so every client processes it
     at the same tick and prints "<name> lost, AI taking over" (F17). The slot leaves
     `minClientTime()` and its `pendingEchoes` are dropped.
4. If no real client remains → `reset()`.

There is no in-game chat from the server; the `DISCONNECT` message itself produces the "lost, AI
taking over" line on every client. Evicted players cannot rejoin a running game (no late joins, F17);
they can reconnect once the room is back in LOBBY.

### 9.5 Lag eviction

The lockstep stalls when `until − minClientTime() ≥ MAX_LAG` (200 ticks, 6.6 s at 33 ms). The
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
| `ROOMS` | `J8PLAY01,D8PLAY01,D8PLAY02,D8PLAY03,D8PLAY05,J8PLAY02,J8PLAY07` | 1..7 rooms, one map each (§17.6): `SCENARIO/MPLAYER` file names, looked up in `src/maps.js` (F31); a map not in the table is written `FILE:Name[:terrain]`, name ≤ 42 chars. The 2nd character of the file is the player count and caps the room (F22). Seven at most: one lobby row per room, the eighth row is the player's own (F33). Room 1, the default selection, is a jungle map (maintainer, 7 Sep 2026). Replaces `MAP_FILE`/`MAP_TITLE`/`MAP_TERRAIN` of 2.0 |
| `HALL` | `true` | the room-selection lobby (§17); `false` = every connection goes straight into room 1 as in 2.0 |
| `MARQUEE_MS` | `200` | hall: the room rows scroll one character per this many ms (≥ 50); 300 was too slow for the maintainer (7 Sep 2026) |
| `PACK_LOBBY_FRAMES` | `true` | several commands per lobby frame (F34): one frame per marquee step and per dump; `false` = one command per frame as the original host and 2.0 |
| `STATS_INTERVAL_S` | `30` | in-battle stats log line (latency, ticks behind, pending echoes, stalls); `0` = off |
| `TICK_MS` | `33` | 200 % speed (F11) |
| `LOOKAHEAD` / `MAX_LAG` | `8` / `200` | lockstep constants (F17) |
| `MIN_PLAYERS` | `1` | real players needed before the countdown may start (default 2 until 7 Sep 2026; the maintainer set it to 1, so a lone player can start against the idle Mercenary) |
| `START_COUNTDOWN_S` | `3` | seconds between "everyone ready" and `'h' 2,0` |
| `MREADY_TIMEOUT_MS` | `30000` | drop clients that never finish loading |
| `IDLE_TIMEOUT_MS` | `10000` | phase-independent fallback: no byte at all for this long → evict |
| `JOIN_TIMEOUT_MS` | `5000` | a joiner must send its first message (`'o'`) this soon after the dump |
| `KEEPALIVE_TIMEOUT_MS` | `3000` | lobby: no `'q'` for this long (≈ 4 missed) → evict |
| `ECHO_TIMEOUT_MS` | `5000` | battle: the echo of a sync frame did not arrive → evict |
| `LAG_DROP_MS` | `10000` | battle: the same client has stalled the game for this long → evict it; `0` = never (original behaviour) |
| `STRIKE_LIMIT` | `10` | soft violations before eviction |
| `STRICT_SEQ` | `true` | a wrong sequence nibble is a hard violation (`false` = resync like the original server) |
| `MERCENARY_RACE` | `0` | race of every fake player: 0 Human, 1 Gray |
| `FAKE_PLAYERS` | `1` | fake humans including Mercenary (1..7), placed in random slots; `MIN_PLAYERS ≤ 8 − FAKE_PLAYERS` |
| `FAKE_NAMES` | `Mercenary,Renegade,Outlaw,Nomad,Drifter,Vagabond,Marauder,Raider` | names for the fakes, slot 0 always `MERCENARY_NAME` |
| `DEBUG_MODE` | `false` | debug mode (also implied by `LOG_LEVEL=debug`): full map view for everybody at game start (F28) |
| `FILL_EMPTY_WITH_AI` | `false` | empty slots become AI (`0` easy / `1` hard via `FILL_AI_TYPE`) |
| `ALLOW_PAUSE` | `true` | relay pause/resume |
| `SPEED_REFRESH_S` | `30` | re-send `TICK_SPEED` |
| `LOG_LEVEL` | `info` | `debug` logs every frame |

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
- **Size.** Traffic is tiny (keep-alives every 700 ms, sync frames every 33 ms of at most 1 KiB);
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
  generated nor forwarded; the two-player test showed perfect sync without them. If desync
  detection is ever wanted again, the server would have to forward `0x08` and a real player would
  have to be the lowest non-lost slot, which contradicts the fake host design.
- **Colour-0 lock on the second joiner** is fixed by the `'h'(0, 0)` start signal but not explained
  (F3, §16). If a client ever refuses a human player's ready message the same way, the `tx` trace
  will show what it received.
- **33 ms ticks on slow machines**: the lockstep stalls when a client is 200 ticks behind, which at
  33 ms is only 6.6 s of lag. A very slow PC will make the game stutter for everyone. Keep `TICK_MS`
  configurable; an adaptive fallback (accept `TICK_MAXSPEED` reports and lower the speed when a
  client cannot keep up) is a possible later addition, deliberately not in the default.
- **Idle fakes**: Mercenary and the other fake humans are slots without a client and just sit there.
  That is the maintainer's decision (7 Sep 2026); they are never handed to the AI.
- **Placement**: with fewer than 8 occupied slots the same subset of start locations is used every
  game (F12). Map-wide randomness needs all slots occupied: `FAKE_PLAYERS=7` (idle fakes) or
  `FILL_EMPTY_WITH_AI` (AI opponents).
- **Mixed builds**: not an issue in practice, only Classic `dc16.exe` plays over the network (F27).
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
  leading `>` of the title.
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

---

## 17. Multi-room: seven rooms and the room-selection lobby (version 2.1)

Added 7 Sep 2026 from the maintainer's proposal (§16). The game gives a player no way to pick a
room: the connect dialog takes a host name only, the port is fixed (8888, then 8889), plain TCP
carries no host name, and the slot number arrives once in `'d'` (F30). Inside the lobby the player
has two input channels the server sees, chat text and the own name field, and the server controls
everything that is displayed. So the lobby itself becomes the room browser.

### 17.1 Overview

```
connect ──'d'(15,p) + hall dump──► HALL (private view: 7 rows = rooms 1..7 in place, own name row, map line = selected room)
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
| 2 | `'i' "D8PLAY01.SCN", title` | the **selected room** in the map line: title = `formatScenarioTitle(">3 Circle of Friends (0/7) open", 8, terrain)`. The digit at index 45 must be 8 because eight rows are occupied (F22); the file is never loaded, no game starts from the hall. Re-sent whenever the selection or that room's count or state changes |
| 3 | for every row q ≠ p: `'l' q,q` | colours = row |
| 4 | for every row q ≠ p: `'g' q,text_q` · `'f' 0,q` · `'j' 2,q` · `'n' q,q` · `'h' 1,q` | every row is a present-not-ready human, so the client stays in the lobby (F3) and no colour is ever locked (F20) |
| 5 | for p: `'g' p,<player name>` · `'f' 0,p` · `'j' 2,p` · `'l' p,p` · `'n' p,p` · `'h' 1,p` | the own row shows the player's name: the client never repaints its own field from an incoming `'g'` (F33), so this row cannot carry room text |
| 6 | `'o' v,default` for v = 0..7, then `'o' 8+q, joinable_q` for q = 0..7 | the per-row CD icon (cosmetic, F5) marks the rooms this client can join right now |
| 7 | the chat window | ten `'e'` lines (§17.8): the six-row header `Welcome to Dark Colony server 2.1.` · `Type /1../7 + ENTER to select a room,` · `then press READY to join it.` · `The map line shows the selected room.` · `You may type your name in your row.` · `Room <n> (<map>) is selected.`, then blanks |

Rows and map line: the rooms fill the rows in order, skipping the client's own row `p`, so row 0
(Mercenary's slot) is always room 1 and the rooms after the own row sit one row lower than their
number. Every room row starts with `"<n> "`, which never moves; the remaining 14 characters scroll.
The map line repeats the selected room in full (42 characters are free, F15, so nothing scrolls
there) and is re-sent when the selection or that room's state changes; selecting does not touch the
rows. With fewer than seven rooms the remaining rows are empty (type 3, status 0). (An interim
version of 7 Sep 2026 showed eight rooms by moving the selected one out of the rows into the map
line; the maintainer found room 1 "lost" that way and asked for rows 1..7 that stay in place.)

Row text: `"<n> "` + `"<map name> <terrain> (<players>/<slots>) <state>"` (e.g. `1 Plink - O jungle (0/7) open`; the terrain was asked for after the fourth live test) with `slots` = the map's player count
minus one (Mercenary's slot; fakes are idle bases, not participants), state ∈ `open`, `full`,
`in battle`, `slot taken` (this client's slot is held by a real player there); never a `':'` (the
chat prefix is split at the first colon). The scrolling parts are padded with spaces to the length
of the longest one, so that all rows scroll with the same period and wrap around together (third
live test: unequal lengths made the rows drift apart). A part longer than the 14 characters scrolls
one character per `MARQUEE_MS` (200 ms) with three spaces between the end and the wrap-around; a
short one stands still. Every step recomputes the map line, the rows and the icons from the live room
states and sends only what changed, **in one frame** (F34), so state changes (a room starts, fills
or empties) show up within one step. Default selection: the lowest-numbered room the client can
join, else room 1.

Cost: one frame of at most about 150 bytes per step per waiting client, roughly 0.7 KB/s at
200 ms, far below the battle stream.

### 17.3 Messages from a client in the hall

| Message | Policy |
|---|---|
| `'q'` keep-alive | consume (liveness) |
| `'o'` CD report | drop: the icons mean "joinable" here |
| `'g'` name | own slot only: sanitised, stored as the player's name and echoed like a room does; it follows the player into the room. The own field is the client's anyway (F33) |
| `'f' 'k' 'm'` race, colour, team | **drop, no echo** (R13). The client applies changes only when they come back, so nothing changes on screen |
| `'j' 'l' 'n' 'i' 'p'` | drop (host-owned, as in §6.2) |
| `'h' 2,p` READY | join the selected room (§17.5); `'h' 1,p` is dropped |
| `'e'` chat | text after the first `':'` (the client's prefix is its row text): a command (§17.4) or hall chat |
| `'y'` INIT_ME | re-send the hall dump; a second one is a strike |
| numeric in-game types, unknown letters | strike (§9.3) |
| deadlines | `JOIN_TIMEOUT_MS` for the first message, `KEEPALIVE_TIMEOUT_MS` afterwards (§9.1) |

### 17.4 Chat commands

| Typed | Effect |
|---|---|
| `/1` … `/7`, a bare digit, `/join N` | select room N: the map line shows it, the rows stay, the header's last line becomes `Room N (<map>) is selected.`; if the room cannot be joined right now a message `Room N: <reason>.` follows |
| `/rooms`, `/list` | one line per room: `"N <map> (k/s) <state>"` |
| `/help` | three lines of at most 40 characters: `/1../7 + ENTER selects a room.` · `/rooms lists the rooms.` · `READY joins the selected room.` |
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
  first so that the default selection is a jungle map.
  fake host in slot 0.
- Every room logs with `room: n`; the hall logs `hall joined`, `hall -> room`, `hall left`.

### 17.7 Real-client tests (7 Sep 2026)

### 17.8 The chat window: no name for the relay, a header that stays

Two rules from the maintainer (7 Sep 2026, after the fifth round): a line the relay itself writes
carries **no name** in front (no `"Mercenary: "`), and the hall and room greetings sit **at the top of
the chat and stay there** while messages arrive.

The client's chat control is a plain ten-line log that wraps at 40 columns and drops lines from the
top (F35), so the server paints it: `ChatView` (`src/chat.js`) keeps, per client, a static header
and the most recent messages, wraps every text server-side at 40 columns (so the client never wraps
anything itself) and renders exactly ten lines, header first, messages below, blanks at the end. Every
chat event (a relay line, a player's line, a room's announcement, `/rooms`) appends to the affected
clients' views and sends each of them its ten lines in one packed frame. What the client shows is
therefore always the render, with the header on top. Entering a room replaces the view (new header,
no old lines). Headers: hall **six rows** (`Welcome to Dark Colony server 2.1.`, the two command
lines, `The map line shows the selected room.`, `You may type your name in your row.`, `Room <n>
(<map>) is selected.`; the last one is rewritten in place on every selection), room one row (`Room
<n>: <map>, <terrain>, <k> players.`); the rest is for messages: four rows in the hall (so `/rooms`
shows its last four lines and `/help` is three short lines), nine in a room. The tenth live test
showed the three instruction lines scrolling away under a flood of comments while the three header
lines stayed; the maintainer wanted all six to stay, hence the six-row header.
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

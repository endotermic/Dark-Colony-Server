# Dark Colony `dc16.exe` — TCP/IP Network Protocol

Reverse-engineered from the disassembly of `DC - Classic/dc16.exe` (Watcom-linked PE32, image base `0x400000`, code section `AUTO` at `0x401000`). All addresses below are virtual addresses in that binary; file offset = VA − `0x400C00`. Assertion strings in the binary preserve the original source file names (`net.c`, `hack.c`, `proto.c`, `server.c`, `client.c`, `setup.c`, `sync.c`, `dplay.c`, `local.c`) and line numbers, which are quoted where useful.

Names written in `UPPER_CASE` come from the binary's own `MSIZE_*` assertion strings. Names marked *(inferred)* are mine, derived from behaviour. Everything else is stated as observed in code.

The game folder `DC - Classic/`, the full disassembly `dc16.asm` and the Ghidra project referred to here are in the sister repository [Dark-Colony](https://github.com/endotermic/Dark-Colony); this document moved to the server repository on 7 Sep 2026 together with `RELAY_SERVER_PLAN.md`, which records everything learned after this protocol description was written (title format, MREADY semantics, colour-lock behaviour, cheat flags).

---

## 1. Architecture overview

| Role | Description |
|---|---|
| **Server** (`server.c`, `Initialize_Server` @ `0x40B450`, loop @ `0x40B7CC`) | Owns up to 8 connection slots. Relays every message it receives to *all* connected clients (including the sender) unless it consumes it itself. Drives game time with `UNTIL`/`TICK` messages. Never simulates the game. |
| **Client** (`setup.c` lobby @ `0x410830`, in-game receive @ `0x41E648`) | Every participant, including the host machine. Sends commands to the server, executes commands only when they come back from the server (lockstep relay model). |
| **Host** | Runs a server and a client in one process. The host's client connects to its own server through an in-process mailbox network (`local.c`) that the TCP listener creates as a sub-network; it always occupies **server slot 0**. |
| **Meta server** *(external)* | A stand-alone lobby/relay program not contained in `dc16.exe`. Speaks the "version 9" protocol (§4.2). `dc16.exe` only implements the client side. |

Three interchangeable transports implement one virtual interface (§2.4): **TCP/IP** (`WSOCK32.dll`, Winsock 1.1), **DirectPlay** (`DPLAYX.dll`: IPX, modem, serial) and **local mailboxes** (single machine). The framing (§3) and every message (§4) are transport-independent. This document covers TCP; the others are mentioned only where the difference matters.

Player limit over TCP: the TCP network object reports `max_connections = 4` (`[net+0x54]`, set in `get_tcp_network` @ `0x42E04A`), i.e. **host + 3 TCP clients**. DirectPlay reports 8, local 1.

---

## 2. Transport layer (TCP)

### 2.1 Winsock usage
Imported by ordinal from `WSOCK32.dll` (IAT `0x4805A0`–`0x4805D4`, thunks `0x47F122`–`0x47F170`): `WSAStartup`, `accept`, `listen`, `bind`, `closesocket`, `gethostbyname`, `inet_addr`, `socket`, `htons`, `connect`, `recv`, `send`, `__WSAFDIsSet`, `select`. No `sendto`/`recvfrom`: **TCP stream sockets only**. `WSAStartup(0x0101)` is called once (`0x42DFC4`, flag byte `0x489700`). `WSACleanup` is never called. `setsockopt` and `ioctlsocket` are not imported either: all sockets stay **blocking**, Nagle's algorithm stays enabled and no TCP keep-alive is set; readiness is always probed with `select` and a zero timeout.

### 2.2 Server side (`0x42DDA8`, vtable slot `listen`)
```
s = socket(AF_INET, SOCK_STREAM, 0)
bind(s, {AF_INET, port = htons(8888), addr = INADDR_ANY}, 16)
listen(s, 5)
```
Before that it creates the in-process mailbox sub-network for the host's own connection (`0x40BFC0`), stored at `priv[4]`.

Accept (`0x42DE88`, vtable `accept`): `select(readfds={s}, timeout 0)`; if readable, `accept(s, NULL, NULL)`. The new connection record is initialised as `{net, seq_out=0, seq_in=0, socket, pending=0}`. Return codes of `bind`/`listen` are **not checked**.

### 2.3 Client side (`0x42DC8C`, vtable `connect_to_server`)
Address argument: `struct { uint16 port; uint32 pad; char *host; }`.
1. If the network object has a mailbox sub-network (`priv[4] != NULL`, i.e. we are the host) → connect through it instead of TCP.
2. `socket(AF_INET, SOCK_STREAM, 0)`; on failure prints `Died in socket`.
3. `sin_port = htons(port)`; `sin_addr = inet_addr(host)`. If `inet_addr` fails, `gethostbyname(host)` and every address in `h_addr_list` is tried in turn.
4. Connect attempt (`0x42DC10`): `connect(s, addr, 16)`; **if it fails, the port is changed to `htons(8889)` and `connect` is retried once**; afterwards the port field is reset to `htons(8888)`.
5. On success a 16 KiB (`0x4000`) send buffer is allocated (`generic_connect_to_server`).

The UI (`0x405B40`) fills the address with the host string typed in the "CONNECT TO SERVER" dialog and **port 8888** (`0x405C0B`). The "ACT AS SERVER" button (`0x405AD0`) starts the server with an empty address, so the host connects to itself via the mailbox path.

### 2.4 Network object interface (vtable)
Every transport fills the same slots of the network object (`get_tcp_network` @ `0x42DFC4`):

| Offset | TCP implementation | Purpose |
|---|---|---|
| `+0x54` | `4` | max connection slots |
| `+0x58` | `{SOCKET listen_sock = -1; net *local_subnet = NULL}` | private data |
| `+0x5C` | `0x42DB14` | `write(conn, buf, len)` – buffered send |
| `+0x60` | `0x42DBC8` | `read_exact(conn, buf, n)` – blocking, returns 1/0 |
| `+0x64` | `0x42DC8C` | `connect(conn, addr)` |
| `+0x68` | `0x42DDA8` | `listen(&conn0)` |
| `+0x6C` | `0x42DE0C` | `poll_readable(conn)` – `select` with zero timeout |
| `+0x70` | `0x42DE88` | `accept(conn)` |
| `+0x74` | `0x43AE28` (shared by all transports) | `send_to_many(conns, count, buf, len)` |
| `+0x78` | `0x42DF40` | `close(conn)` – `closesocket`, free send buffer |
| `+0x7C` | `0x42DF68` | `free()` – close listening socket, free object |

Connection record (60 bytes per slot in the server's array at `server+4`):

| Offset | Field |
|---|---|
| `+0x00` | `net *` (NULL = slot free) |
| `+0x04` | `uint8 seq_out` – 4-bit sequence counter for outgoing frames |
| `+0x05` | `uint8 seq_in` – next expected incoming sequence |
| `+0x08` | `SOCKET` |
| `+0x0C` | `int pending` – bytes waiting in the send buffer |
| `+0x10` | `char *sendbuf` – 16 KiB |

### 2.5 Send path (`0x42DB14`)
1. Flush pending bytes if `select(writefds)` says the socket is writable (`0x42DA9C`); a `send` error prints `Died in write` and returns −1.
2. If nothing is pending and the socket is writable, `send(s, buf, len, 0)` directly; any unsent remainder is appended to the buffer.
3. Otherwise append to the buffer. **If `pending + len > 0x4000` the whole buffer is discarded** (`pending = 0`) – there is no back-pressure.

### 2.6 Receive path (`0x42DBC8`)
Before reading, pending output is flushed. Then `recv` is called in a loop until exactly `n` bytes have arrived (**blocking**). Callers only invoke it after `poll_readable` reported data, but a partial frame can still block the game loop. Return 0 on error or EOF; the caller treats that as connection loss.

---

## 3. Framing layer (`net.c`, `hack.c`)

Every unit on the wire is a **frame**:

```
offset  size  field
0       1     length low byte
1       1     bits 0-3: length high nibble (12-bit length, max 4095)
              bits 4-7: sequence number (0..15)
2       n     payload = one or more commands (§4), each starting with a type byte
2+n     1     0x00 terminator (counted in length)
```

* `length` counts the two header bytes, the payload and the terminator. Senders assert `length <= 1024` (`DC_MESSAGE_MAX`, `hack.c:30`) and `high nibble <= 15` (`hack.c:34`, `net.c:157`).
* Writer: `0x421764(buf, len, conn)` appends the `0x00` terminator, then `0x421648` stores the length, ORs `conn->seq_out << 4` into byte 1, increments `seq_out` mod 16 and calls the transport `write`. If the global "local mode" flag (`0x4AF090`) is set, the frame is instead fed straight into the local game state (`0x41E06C`) – used for single-player and for AI-generated commands.
* Server relay: `0x43AF78(net, conns, count, buf, len)` writes the header, then `0x43AE28` re-stamps the sequence nibble **per destination** (each connection has its own `seq_out`) and calls `write` for every non-empty slot.
* Reader `0x43AE8C(conn, buf, maxlen, &outlen, strict)`:
  1. return 0 if `poll_readable` is false;
  2. read 2 header bytes (−1 on failure); `seq = hdr[1] >> 4; len = (hdr & 0x0FFF) - 2`;
  3. `len > maxlen` → −1; read `len` bytes into `buf` (payload including terminator);
  4. sequence check: `seq == (seq_in-1) & 15` → **duplicate, silently skipped** (reads the next frame); `seq == seq_in` → accept, `seq_in++`; otherwise if `strict == 0` accept and resynchronise, if `strict == 1` return −1.

  `strict` is 0 in the server loop and 1 everywhere on the client.
* Receive buffer sizes (`maxlen`): server 1022, in-game client 1024, lobby 1416 (`0x588`), meta lobby 8192.
* Payload parsing (`0x41E06C`): commands are read back-to-back; a type byte of `0x00` must be the last byte, otherwise the frame is rejected. Types `≥ 0x1C` have no in-game handler; in the in-game receive loop any frame whose first payload byte is `≥ 0x64 ('d')` is dropped without processing (`0x41E6CB`).

### 3.1 Primitive encodings (`net.c` helpers)
| Type | Encoding | Helpers |
|---|---|---|
| `u8` | 1 byte | inline |
| `i16` | 2 bytes **little-endian** | put `0x43AD40`, get `0x43AD64` |
| `i32` | 4 bytes little-endian | put `0x43AD88`, get `0x43ADDC` |
| `string` | ASCII, `0x00`-terminated, no length prefix | `strcpy`; reader `0x420E6C` scans for NUL within the remaining length |

Names are limited to 16 characters + NUL (17-byte buffers).

---

## 4. Message catalogue

Type bytes fall in two families. **Letters** (`'d'`..`'y'`, plus `'e'`,`'i'`,`'q'`) are setup/lobby/meta messages and are ignored in-game. **Small numbers** (`0x01`..`0x1B`) are in-game commands dispatched through a 28-entry function-pointer table at `0x48949C` (index = type byte).

Field order is exactly the wire order. Serialisers live in `proto.c` (`0x41F1B0`–`0x4215E8`); each has a writer asserting `MSIZE_X + 1` (with type byte) and a reader asserting `MSIZE_X`.

### 4.1 Setup / lobby messages (letters)

| Type | Name | Payload | Sent by | Meaning |
|---|---|---|---|---|
| `'d'` 0x64 | `VERSION` | `i16 15`, `i16 player` | server → new client | Handshake. Constant **15** is the game protocol version; reader rejects anything else. `player` = the connection slot assigned to the client (0..7). |
| `'r'` 0x72 | `VERSION` (meta) | `i16 9`, `i16 id` | meta server → client | Meta-lobby handshake, version **9**; `id` = client id in the meta lobby. |
| `'v'` 0x76 | `MREADY` | `i16 id/player`, `u8 state` | client → server | `state 2` = "loaded, ready to start the game" (server waits for it from every connection). In the meta lobby `state 0/1` = ready toggle. |
| `'u'` 0x75 | `GROUP` | `i16 id`, `i16 group` | meta | Join meta group `group`. |
| `'g'` 0x67 | `NAME` | `i16 player`, `string name` | any ↔ | Player name (≤16 chars). |
| `'w'` 0x77 | `GVERSION` | `i16 id`, `i16 version` | client → meta | Announces game version (**15**) to the meta server. |
| `'s'` 0x73 | `INTRO` | `i16 id` | meta → client | A client with `id` appeared in the meta lobby. |
| `'t'` 0x74 | `OUTRO` | `i16 id` | meta → client | Client `id` left. |
| `0x01` | `TICK` | `u8 n` | server → client | Advance `n` game ticks (only used when the server has a single connection). Ignored in the lobby. |
| `'h'` 0x68 | `READY` | `u8 status`, `u8 player` | any ↔ | Slot status: `0` empty/disconnected, `1` present, `2` ready (locks the colour). |
| `'y'` 0x79 | `INIT_ME` | `u8 player` | client → host | Sent by a client that joined through the meta lobby; the host answers with the full lobby state (§6.2). |
| `'f'` 0x66 | `RACE` | `u8 race`, `u8 player` | any ↔ | `race` 0/1 (Human / Gray). |
| `'j'` 0x6A | `TYPE` | `u8 type`, `u8 player` | any ↔ | Slot type: `0` AI (easy), `1` AI (hard), `2` human network player, `3` empty. UI cycles 0→1→3→0; 2 is assigned only to joiners. |
| `'k'` 0x6B | *(colour cycle)* | `u8 delta`, `u8 player` | any ↔ | `colour = (colour + delta) mod 8`, skipping colours already taken. UI sends 1 or 7. Ignored if slot is ready. |
| `'l'` 0x6C | *(colour set)* | `u8 colour`, `u8 player` | host → | Absolute colour 0..7. |
| `'m'` 0x6D | *(team cycle)* | `u8 delta`, `u8 player` | any ↔ | `team = (team + delta) mod 8`. Ignored if slot is ready. |
| `'n'` 0x6E | *(team set)* | `u8 team`, `u8 player` | host → | Absolute team 0..7. Equal team ⇒ allied at game start. |
| `'p'` 0x70 | `NUKE` | `u8 player` | — | Remove player from slot (type→3). Handler exists, **no sender in dc16.exe**. |
| `0x10` | `DISCONNECT` | `u8 slot` | server → clients | Connection `slot` was lost. Lobby: slot emptied. In-game: AI takes over (§6.6). |
| `'o'` 0x6F | `VAR` | `i16 var`, `i16 value` | any ↔ | Lobby option (§4.1.1). |
| `'e'` 0x65 | *(chat)* | `string "Name: text"` | any ↔ | Lobby chat line, appended to the chat window. |
| `'i'` 0x69 | *(scenario)* | `string file`, `string title` | host → | Selected scenario: file name without directory (game prefixes `scenario/mplayer/`) and display title. |
| `'q'` 0x71 | *(keep-alive)* | — | any → server | Sent every 700 ms from the lobby loop; receivers ignore it. |
| `0x04` | `CHEAT` | `i16 a`, `i16 b` | any ↔ | In-game: `a=1` pause, `a=2` resume (server enters/leaves paused state); other values toggle flag `b` (0..3). |
| `0x02` | `UNTIL` | `i32 a`, `i32 b` | both | Lockstep pacing, see §6.4. |
| `0x11` | `TICK_SPEED` | `i32 ms` | client → server → all | Tick length in ms (>0). Server adopts it and relays. |
| `0x12` | `TICK_MAXSPEED` | `u8 player`, `i32 ms` | client → all | Slowest tick a machine can sustain. |
| `0x13` | `TICK_DESSPEED` | `i32 ms` | client → all | Desired tick length. |

Lobby dispatch table (`setup.c`, `0x488E54`, 17 entries `{type, handler}`): `0x01, 'e','f','h','g','i','j','k','l','m','n','o','p','y', 0x10, 'q'`, default. Meta dispatch table (`0x488E1C`, 7 entries): `'r','s','t','g','u','v','w'`.

#### 4.1.1 `VAR` indices
Stored in `ss+0xA670[16]`; defaults from table `0x488EDC`.

| var | Lobby control (`intrface/multie`) | Values | Default |
|---|---|---|---|
| 0 | Storage Cells | 0 OFF, 1 LOW, 2 MED, 3 HIGH | 0 |
| 1 | Artifacts | 0 OFF, 1 LOW, 2 MED, 3 HIGH | 0 |
| 2 | Erupting Vents | 0 = ON button, 1 = OFF button | 1 |
| 3 | Renewable Vents | 0 = ON button, 1 = OFF button | 0 |
| 4 | P7 Quantity Multiplier | 1..20 | 4 |
| 5 | P7 Flow Multiplier | 1..20 | 4 |
| 6 | Commander Rank | 0..3 (LEUT. …) | 0 |
| 7 | unused | | 0 |
| 8+p | player *p* has the CD in the drive | 0/1 | var 8 = 1 |

### 4.2 Meta-lobby ("version 9") protocol — client side only
Entered when the first frame after connecting starts with `'r'`. The client then (`0x40EB54`):
1. records its `id`, sends `'w'(id, 15)`;
2. maintains a list of entries `{id, group, version, ready, name}` from `'s'`,`'t'`,`'g'`,`'u'`,`'v'`,`'w'`;
3. UI: name edit → `'g'(id, name)`; ready checkbox → `'v'(id, 0|1)`; clicking a list entry → `'u'(id, that entry's group)`;
4. on a second `'r'(9, x)` while already joined the routine returns `x` as the assigned game player number (negative → "bad pnum" error) and the same connection continues with the normal lobby protocol (§6.2). A first `'r'` is answered with `'v'(id, 2)`.

The server half (matching of groups into games, choice of player numbers) is outside `dc16.exe`.

### 4.3 In-game commands (handler table `0x48949C`)

Each command is executed by every client at the same game tick (§6.4). Object ids index the game-state object array (max 800 = `0x320`, 220-byte records). "selected" means the object's selection-mask bit for `player` is set (byte `obj+0x12`). Positions are map coordinates in 1/32-tile units as stored (`value << 5`).

| Type | Handler | Payload | Effect |
|---|---|---|---|
| `0x01` `TICK` | `0x41CCFC` | `u8 n` | run `n` simulation ticks |
| `0x02` `UNTIL` | `0x41CD34` | `i32 a`, `i32 until` | in the command stream: asserts `until == gs->until`, sends `UNTIL(-1, until)` to the server, clears `gs->until` |
| `0x03` *(create object)* | `0x41CF08` | `i16 obj`, `i16 x`, `i16 z`, `i16 y`, `u8 type`, `u8 owner`, `u8 f9`, `u8 f2C`, `u8 f0C` | instantiate object `obj` at (x,z,y) with the given type/owner fields; no sender found in the binary |
| `0x04` `CHEAT` | `0x41CE9C` | `i16 a`, `i16 b` (b<4) | toggle flag `gs->flags[b]` (pause handled separately, §6.4) |
| `0x05` *(order object)* | `0x41D018` | `i16 obj`, `u8 order` | `obj->pending=1; obj->order=order` |
| `0x06` *(target position)* | `0x41D338` | `i16 obj`, `i16 x`, `i16 z` | set object target x,z |
| `0x07` *(waypoints, objects)* | `0x41D574` | `u8 n (≤8)`, `i16 count`, `n×(i16 x,i16 z)`, `count×i16 obj` | copy `n` waypoints into each listed object |
| `0x08` *(sync check)* | `0x41CE74` | `i16 checksum`, `i32 time` | compare with local checksum history (§6.5) |
| `0x09` *(research)* | `0x41CAA4` | `u8 item`, `u8 level`, `u8 player` | set research item/level for player, adjust money |
| `0x0A` *(build)* | `0x41C9C8` | `u8 unit_type`, `u8 player`, `u8 count` | enqueue `count` units in the player's build queue, charge cost |
| `0x0B` *(target object)* | `0x41D468` | `i16 obj`, `i16 target` | set object target-object field |
| `0x0C` *(setting)* | `0x41CBD4` | `u8 which (0/1)`, `u8 player`, `u8 value`, `u8 slot` | per-player table setting, costs 1000×value |
| `0x0D` *(diplomacy)* | `0x41D7AC` | `u8 pa (<8)`, `u8 pb (<8)`, `u8 which (0/1)`, `u8 on` | set relation between players `pa`,`pb` in matrix 0 (alliance) or 1 (shared vision) |
| `0x0E` *(chat / cheat)* | `0x41DA2C` | `u8 from`, `u8 to_mask`, `string text` | if `to_mask & (1<<me)`: show message (queue of 6). Text after `':'` is compared with cheat codes `we need equipment` (+10000 P7 for all), `I'm fighting for that equipment` (P7 = 0), `slag net` (toggle flag) |
| `0x0F` *(bonus)* | `0x41DBB0` | `u8 player` | if `player == me`: +1000 P7 |
| `0x10` `DISCONNECT` | `0x41DBE0` | `u8 slot` | map network slot → player; mark player lost, AI takes over, post message "%s lost, AI taking over" |
| `0x11` `TICK_SPEED` | `0x41DD6C` | `i32 ms` | `gs->tick_ms = ms` |
| `0x12` `TICK_MAXSPEED` | `0x41DDB4` | `u8 player`, `i32 ms` | `gs->max_speed[player] = ms` |
| `0x13` `TICK_DESSPEED` | `0x41DE08` | `i32 ms` | `gs->desired_ms = ms` |
| `0x14` *(select)* | `0x41DEA8` | `u8 player`, `i16 obj…`, `i16 0xFFFF` | clear player's selection, then select each listed object |
| `0x15` *(deselect)* | `0x41DFE8` | `u8 player` | clear player's selection |
| `0x16` *(order selected)* | `0x41D060` | `u8 player`, `u8 order` | give `order` to all selected objects (order `0x12` only to types allowed by table `0x50FE04`) |
| `0x17` *(target pos, selected)* | `0x41D390` | `u8 player`, `i16 x`, `i16 z` | set target x,z on selected objects |
| `0x18` *(target obj, selected)* | `0x41D4AC` | `u8 player`, `i16 target` | set target object on selected objects |
| `0x19` *(waypoints, selected)* | `0x41D69C` | `u8 n (≤8)`, `u8 player`, `n×(i16 x,i16 z)` | copy waypoints to selected objects |
| `0x1A` *(order 0x0D selected)* | `0x41D150` | `u8 player` | order `0x0D` to selected objects whose type allows it |
| `0x1B` *(move-to selected)* | `0x41D22C` | `u8 player`, `i16 x`, `i16 z` | order `0x12` with a single waypoint (x,z) to selected objects whose type allows it |

Command builders on the sending side (`client.c`, `0x40C50C`–`0x40C978`; further inline builders at `0x409513`/`0x409748`/`0x409818` for `0x1B`/`0x18`/`0x1A`, `0x43C8AA`/`0x43CC56` for `0x14`/`0x15`, `0x40978B` for `0x0E`) confirm the layouts and show that several commands are packed into one frame, e.g. `[0x19 waypoints][0x16 order][0x00]` or `[0x07 …][0x05 obj order]…[0x00]`.

Order codes observed at the call sites (the `order` byte of `0x05`/`0x16`):

| Order | Where sent | Meaning |
|---|---|---|
| `2` / `7` | `0x40C7D4` callers (`0x44B296`, `0x45A407`, `0x46BBE7`, `0x459730`) choose one of the two by a mode flag | movement along the waypoint list in the two movement modes the UI offers (move / assault) |
| `0x0D` | `0x46BB17`, handler `0x1A` | order for units whose type has the flag in table `0x50FDFC` (deploy) |
| `0x0E` | `0x40C978`, always paired with `0x0B target` | attack the target object |
| `0x12` | handler `0x1B` | move to a single position, for unit types flagged in `0x50FE04` |

Selection is replicated over the network (`0x14`/`0x15`) so that later "selected" commands are unambiguous on every machine. In-game `player` values are **game player indices** (0..7, assigned at game start, §6.3), not lobby slots; `DISCONNECT` carries the network slot and is translated through `gs->net_id[player]` (`gs+0xCA0+player*0xE34`).

---

## 5. Server behaviour (`server.c`)

State record (`0x24C` bytes) created by `Initialize_Server(net)`:

| Offset | Field | Initial |
|---|---|---|
| `+0x004` | `conn[8]` (60 bytes each) | slot 0 reserved for the host's mailbox |
| `+0x1E4+8i` | `ready[i]` (byte) | 0 |
| `+0x1E8+8i` | `client_time[i]` | 0 |
| `+0x224` | connection count (upper bound for broadcasts) | 1 |
| `+0x228` | server game time (ticks) | 0 |
| `+0x22C` | last tick timestamp (ms) | −1 |
| `+0x230` | state: 0 lobby, 1 waiting for `MREADY`, 2 running, 3 paused | 0 |
| `+0x234` | tick length ms | 66 (`0x42`) |
| `+0x238` | look-ahead ticks | 8 |
| `+0x23C/+0x240/+0x244` | min/max latency, sample count | −1/−1/0 |
| `+0x248` | min client time | 0 |

Per iteration of `0x40B7CC` (called every frame by the host):
1. For each slot with a connection and readable data, read frames (`strict=0`). On read error: close the connection and remember it as lost. Each frame is passed to `0x40B6A4`:
   * `UNTIL` → consumed (§6.4).
   * `TICK_SPEED` → set tick length, **and relay**.
   * `'v'` `MREADY` (only in state 1) → `state` byte must be 2, slot not already ready, else the client is dropped; consumed.
   * `CHEAT(1,·)` → state 3 (pause); `CHEAT(2,·)` → state 2; relayed.
   * everything else → relayed verbatim to all slots (sender included).
2. For every lost slot: broadcast `DISCONNECT(slot)`.
3. State 1 → 2 when every connected slot has sent `MREADY(·,2)`.
4. State 2: compute elapsed ticks `n = (now − last)/tick_ms` (max 255). If `n>0`: with one connection send `TICK(n)`; otherwise compute `until = time + n + 8`; if `until − min(client_time) ≥ 200` do nothing (clients too far behind), else broadcast `UNTIL(time, until)` and `time += n`.

New TCP connections are accepted only from the lobby loop (`0x4110FF` → `0x40BB20`): first free slot `i < max_connections`, then the host sends the lobby state to it (§6.1). The in-game server loop never calls `accept`, so **there are no late joins**; a socket that connects during play is left in the listen backlog.

---

## 6. Protocol flows

### 6.1 Direct TCP join and lobby (`setup.c` @ `0x410830`)

```
Client                                  Host (server + host client)
  |-- TCP connect :8888 (retry :8889) -->|
  |                                      | accept → slot p; sent directly to slot p:
  |<-------- 'd' 15, p ------------------|  (VERSION, player number)
  |                                      | host client → server → relayed to ALL:
  |<-- 'i' file,title   'l' colour,q for every other slot q
  |    for each other slot q: 'g' q,name  'f' race,q  'j' type,q  'n' team,q  'h' status,q
  |    for p:                'g' p,name  'f' race,p  'j' 2,p  'l' colour,p  'n' team,p  'h' 1,p
  |    'o' 0..15 (all VARs) -------------|
  |-- 'o' 8+p, cd_present -------------->| (relayed)
  |   ... 'q' every 700 ms; UI actions → 'g','f','j','k','m','h','o','e' → relayed to all
```
* The client waits up to **25 s** (polling every 100 ms) for the first frame; timeout → "no init msg". A first byte other than `'r'`/`'d'` → "unknown"; `'d'` with version ≠ 15 → "wrong version".
* The client's own player number is the slot from `'d'`. Player 0 is always the host.
* All lobby state changes are applied only when the message comes back from the server; the host is not special except for accepting and for answering `INIT_ME`.
* Ready handling: `'h'(2,p)` locks colour `p`; any settings change by the host re-sends `'h'(1,q)` for every human slot (un-ready all). The game starts when every non-empty human slot has status 2 (`0x411CF1`).
* **CD rule**: each client reports `'o'(8+p, cd)` at join and re-checks the drive every 5 s. The host requires `#CDs ≥ ceil(#humans / 2)`; while unmet it keeps un-readying everyone (`0x4107A0`). (This uses the same CD-presence routine `0x405E8C` that the no-CD patches bypass at start-up.)
* Every 5 s the CD state is re-sent if it changed. `'q'` keep-alives are sent every 700 ms.
* Error dialog texts (`intrface/loste`): 2 "Connection Lost", 3 "no init msg", 4 "wrong mversion", 5 "bad pnum", 6 "wrong version", 9 "SERVER LOST!?!", 10 "SETUP_COMMANDS BAD", 11 "UNABLE TO CONNECT".

### 6.2 Join through the meta server
Identical socket handling, but the first frame is `'r'(9,id)`. After the meta lobby (§4.2) returns player number `p`, the client sends `'o'(8+p,cd)` and then **`'y' INIT_ME(p)`**; the host answers with the same state dump as in §6.1 minus the `'d'`. The rest is identical, with the meta server acting as the relay server.

### 6.3 Game start (`0x40122C`)
1. Lobby exits with success. Lobby slots with type ≠ 3 are collected, the first *N* (N = scenario player count) are shuffled, giving the **game player index** for each lobby slot; `gs->net_id[player] = slot`.
2. Types map to control: 3 → none, 2 → human, 0 → AI (difficulty 0x100), 1 → AI (0x200). Equal `'n'` team values become alliances (matrix 0, plus matrix 1 for humans).
3. Host: server state := 1.
4. Every client sends `'v' MREADY(my_player, 2)`. When all arrived the server enters state 2 and starts issuing `UNTIL`.

### 6.4 In-game lockstep (`0x41E648`, `0x41E268`, `0x41E0D8`)
Client receive loop (each frame):
1. Read frames (`strict=1`) into the **held buffer** (`gs+0x958`, up to `0x1C00` bytes), each stored as `[i16 payload_len][payload]`. Frames starting with a letter are discarded.
2. On `UNTIL(a, until)` (`0x41E484`): **echo the identical frame back** to the server (this is how the server measures round-trip latency: `latency = server_time_now − a`), and if `gs->until < 0` set `gs->until = until`; otherwise adjust the timing accumulator (clamped to 2000 ms).
3. On `CHEAT(1/2,·)`: set/clear the paused flag immediately.
4. Pacing (`0x41E268`): while `gs->until ≥ 0` and `game_time ≤ until`: for every `tick_ms` elapsed, if `game_time + 1 == until` first execute the held commands up to and including the next `UNTIL` marker (`0x41E0D8`), then `game_time++` and simulate one tick (`0x419978`). Executing the `UNTIL` marker sends `UNTIL(-1, until)` ("I reached `until`") and clears `gs->until`, so the client stalls until the server's next `UNTIL`.
5. Server side, `UNTIL(-1, t)` records `client_time[slot] = t` and refreshes `min(client_time)`, which gates further `UNTIL`s (200-tick lag limit). `UNTIL(a≠-1, ·)` is treated as an echo and only updates latency statistics.
6. With a single connection the server sends `TICK(n)` instead and the client executes everything immediately.

Speed negotiation (`0x419830`): each client periodically measures how long its own ticks take, clamps the result to **33..660 ms**, and sends `TICK_MAXSPEED(my_player, ms)`. It then takes the largest `TICK_MAXSPEED` value reported by any player, derives the UI speed setting from it (thresholds 66/100/150/200/250 ms) and sends `TICK_SPEED(max(desired_or_current_tick_ms, slowest_player_ms))`. The server adopts the last `TICK_SPEED` it receives as its tick length and relays it, so the game runs at the pace of the slowest machine. `TICK_DESSPEED(ms)` carries the speed chosen in the options screen.

Commands generated by AI players are executed locally on every machine (local-mode flag around `0x41AE38`), never sent.

### 6.5 Sync checking (`sync.c`)
After every tick each client stores a 16-bit checksum of the game state (`0x44ABC0`: sums of player money, object count, and per-object position/type/owner fields) in a 256-entry history indexed by `time mod 256`. The active player with the **lowest network id** broadcasts `0x08 (checksum, time)` every tick. Receivers compare against their history entry for that tick; mismatch prints `sync error: time %ld, net %d, me %d` and calls the desync handler (`0x44AC94`). Checks for a tick older than 256 ticks or in the future are reported as `AUGH check sync time …`.

### 6.6 Disconnects
A read error on a server slot closes it and broadcasts `DISCONNECT(slot)`. In the lobby the slot becomes type 3 / status 0. In-game the player's control changes to AI (`+0xBBC = 3`), its money is normalised and a system chat message "*name* lost, AI taking over" is generated locally. A client whose own read fails leaves the game with "Connection Lost"/"SERVER LOST!?!".

### 6.7 Chat and cheats
Lobby chat: `'e' "Name: text"`. In-game chat: `0x0E from, to_mask, text` (bit *i* of `to_mask` = deliver to player *i*); the text after `':'` is compared against the three cheat strings listed in §4.3.

---

## 7. Constants

| Constant | Value | Where |
|---|---|---|
| Protocol/game version (`'d'`, `'w'`) | 15 | `0x41F1C4`, `0x40EB97` |
| Meta protocol version (`'r'`) | 9 | `0x41F318` |
| TCP port | 8888, fallback 8889 | `0x42DDCA`, `0x42DC2A` |
| `listen` backlog | 5 | `0x42DDF4` |
| Winsock version | 1.1 (`0x0101`) | `0x42DFD7` |
| Max connections TCP / DirectPlay / local | 4 / 8 / 1 | `[net+0x54]` |
| Send buffer per connection | 16384 | `0x42DC74` |
| `DC_MESSAGE_MAX` (frame) | 1024 | `hack.c:30` |
| Frame length field | 12 bits; sequence 4 bits | `net.c:157` |
| Join timeout / poll | 25000 ms / 100 ms | `0x4108EC`, `0x410929` |
| Keep-alive `'q'` | every 700 ms | `0x411C2A` |
| CD re-check | every 5000 ms | `0x41137F` |
| Default tick length | 66 ms | `0x40B4DD` |
| Server look-ahead | 8 ticks | `0x40B4E7` |
| Max ticks per server iteration | 255 | `0x40B98A` |
| Lag stall threshold | 200 ticks | `0x40B950` |
| Held-command buffer | 7168 bytes | `0x41E65B` |
| Timing accumulator clamp | 2000 ms | `0x41E59B` |
| Sync history | 256 ticks | `0x44AC75` |
| Max objects / waypoints | 800 / 8 | `0x41D100`, `0x41D5A1` |
| Name length | 16 + NUL | `0x41F701` |

---

## 8. Address index

| Function | Address | Source file |
|---|---|---|
| `get_tcp_network` | `0x42DFC4` | dplay.c |
| TCP write / read_exact / connect / listen / poll / accept / close / free | `0x42DB14` `0x42DBC8` `0x42DC8C` `0x42DDA8` `0x42DE0C` `0x42DE88` `0x42DF40` `0x42DF68` | dplay.c |
| `get_dplay_network` / `create_network` (base object) / local network | `0x42D7F4` / `0x43B070` / `0x40BF50` | dplay.c / net.c / local.c |
| put/get i16, put/get i32 | `0x43AD40` `0x43AD64` `0x43AD88` `0x43ADDC` | net.c |
| frame read `0x43AE8C`, broadcast `0x43AF78`, send-to-many `0x43AE28` | | net.c |
| frame write `0x421648`, write+terminator `0x421764`, `'q'` writer `0x421614`, local-mode flag setter `0x42163C` | | hack.c |
| `proto.c` writers/readers | `'d'` `0x41F1B0`/`0x41F248`, `'r'` `0x41F304`/`0x41F39C`, `'v'` `0x41F458`/`0x41F4F0`, `'u'` `0x41F5A4`/`0x41F638`, `'g'` `0x41F6E8`/`0x41F728`, `'w'` `0x41F84C`/`0x41F8E0`, `'s'` `0x41F994`/`0x41FA20`, `'t'` `0x41FAC8`/`0x41FB54`, `TICK` `0x41FBFC`/`0x41FCF0`, `'h'` `0x41FD8C`/`0x42002C`, `'y'` `0x41FE9C`/`0x41FF90`, `'f'` `0x4200DC`/`0x4201EC`, `'j'` `0x42029C`/`0x4203AC`, `'p'` `0x42045C`/`0x420548`, `DISCONNECT` `0x4205E4`/`0x4206D0`, `'k'` `0x42076C`/`0x42087C`, `'m'` `0x42092C`/`0x420A3C`, `'l'` `0x420AEC`/`0x420BFC`, `'n'` `0x420CAC`/`0x420DBC`, string reader `0x420E6C`, `UNTIL` `0x420EA4`/`0x420F34`, `CHEAT` `0x420FE0`/`0x421074`, `'o'` `0x421124`/`0x4211B8`, `TICK_SPEED` `0x421268`/`0x4212F4`, `TICK_MAXSPEED` `0x42139C`/`0x421438`, `TICK_DESSPEED` `0x4214E0`/`0x42156C` | proto.c |
| `Initialize_Server` `0x40B450`, server loop `0x40B7CC`, message handler `0x40B6A4`, `UNTIL` handler `0x40B550`, accept `0x40BB20`, set state 1 `0x40BBA4`, `Shutdown_Server` `0x40BBB4` | | server.c |
| lobby loop `0x410830`, client join handshake `0x4108DB`, lobby dispatcher `0x40F918`, state dump `0x40FAE8`, scenario writer `0x40F9B0`, CD rule `0x4107A0`, meta lobby `0x40EB54` / dispatcher `0x40EA44` | | setup.c |
| run game `0x40122C`, main loop `0x40115C` | | main |
| command dispatcher `0x41E06C`, held-command executor `0x41E0D8`, pacing `0x41E268`, `UNTIL` receive `0x41E484`, `CHEAT` receive `0x41E5D4`, in-game receive loop `0x41E648` | | client/game |
| command builders `0x40C50C`–`0x40C978`, sync sender `0x419F4B` | | client.c / game |
| checksum `0x44ABC0`, record `0x44AC68`, check `0x44ACF8` | | sync.c |

Pointer tables in `DGROUP`: in-game handler table `0x48949C` (28 × `void*`), lobby dispatch `0x488E54`, meta dispatch `0x488E1C`, `VAR` defaults `0x488EDC`.

---

## 9. Notes and open points

* Two structurally identical "version" messages exist (`'d'`=15 and `'r'`=9); the binary uses one `MSIZE_VERSION` assertion for both.
* The `'p' NUKE` writer is never called; kicking is not implemented in this build.
* Frame loss: the send path drops the entire buffer on overflow and the read path skips duplicates by sequence; there is no retransmission — correctness relies on TCP.
* `ENGEXP16.EXE` (Council Wars) is the same code base with slightly different addresses; the message formats were not separately verified there.
* Semantics of command `0x0C` and of the trailing bytes of `0x03` were not traced into the simulation code; order codes were identified only from their call sites (§4.3), not from the unit state machine.
* `0x03` (create object) has a handler but no sender in this binary; it may belong to a debug build or to scripted spawning that is compiled out.

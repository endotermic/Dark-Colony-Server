# Changelog

Dates are the commit dates on `main`. Details of every finding and decision are in
[`docs/RELAY_SERVER_PLAN.md`](docs/RELAY_SERVER_PLAN.md) (section 16 is the dated change log of the
live tests); the wire protocol is in [`docs/DC16_NETWORK_PROTOCOL.md`](docs/DC16_NETWORK_PROTOCOL.md).

## 2.1 — 8 September 2026 (commit `a411023`)

Rooms, and a room browser inside the game's own lobby screen.

- **Seven rooms**, each with its own map (`ROOMS`, any of the 56 multiplayer maps of Classic, table
  in `src/maps.js`). Default list: Plink - O (jungle, the preselected room), Armageddon, Black Widow,
  Circle of Friends, Olympus Mons, Hoops of Fury, Rings of fire.
- **Room-selection lobby ("hall")**: a newcomer sees rooms 1..7 in the player rows that are not its
  own (fixed number, then the map name, terrain, player count and state scrolling in step), the
  selected room in the map line, and a six-line greeting pinned at the top of the chat. `/1`..`/7`
  plus ENTER selects, READY joins; the room's state dump then repaints the rows with the real
  players. The name may be typed in the hall; race, colour and team only inside a room. A room a
  player cannot join right now says why (in battle, full, slot taken).
- **Chat window painted by the server**: a static header (hall: welcome and instructions; room: the
  room line), recent messages below, ten lines per update; the server's own lines carry no name.
- **One packed frame per lobby update** (dumps, marquee steps, chat repaints): the client handles one
  frame per loop iteration, so many small frames made it fall behind.
- Fake players sitting in a joiner's slot are moved aside; every room counts its players over the
  map's slots without Mercenary.
- Settings: `ROOMS`, `HALL` (false = the 2.0 single room), `MARQUEE_MS` (200), `PACK_LOBBY_FRAMES`;
  `MAP_FILE`/`MAP_TITLE`/`MAP_TERRAIN` removed; defaults `MIN_PLAYERS=1`, `FAKE_PLAYERS=1`.
- Facts from the disassembly recorded as F29–F36 (lobby screen layout, fixed slot per connection,
  one frame per iteration, the chat control, the READY button). 66 tests; the scripted client learnt
  `--room N`.

## 2.0 — 7 September 2026 (commit `dae1320`)

Complete rewrite as a fake-host lockstep relay, based on a study of the executable's network code.

- The server plays the host: **Mercenary** in slot 0 (a fake human, never handed to the AI), real
  players in random free slots, the READY button starts the game after a countdown.
- **Strict lockstep**: one sync frame per server step, `[UNTIL][commands…]`, byte-identical for every
  client; commands only travel inside sync frames. Speed locked at 200 % (33 ms per tick).
- Cheats and the `0x08` checksum command are never forwarded.
- Clients that stop answering (keep-alives, load report, frame echoes) or violate the protocol are
  evicted within deadlines and announced to the others; lag eviction after a configurable stall.
- `FAKE_PLAYERS` (idle fake humans), debug mode with full map view, one-line JSON logs, environment
  configuration, 48 tests with a scripted `dc16.exe` client.
- Protocol reference and design plan moved into `docs/`; Fly.io deployment kept (same app, IPv4 and
  hostname), port 8889 added because the game retries it.

## 1.x — September 2025 to June 2026

The first server, written from captured traffic; it lives in the git history before `dae1320`.

- **1.0, Sep–Oct 2025**: Node.js server on port 8888 deployed to Fly.io; packet framing with length
  and per-client counter; lobby commands (name, chat, ready, race, vents, map), Armageddon
  preselected; entering the battle, unit selection and move echo, battle ping, in-game chat and
  superweapon.
- **Nov 2025**: room init with random placement, several clients per room, a hard-AI bot, full room
  reset when everybody leaves, 2× speed with a 33 ms ping, protocol definitions in their own file.
- **Feb 2026**: Fly TCP health checks disabled (the server took them for players); start-up delay
  removed.
- **May–Jun 2026**: room init made identical to the original game server, map packet fixes, colour
  switching fixed, readiness fix, two and more players through the `ready` chat word.

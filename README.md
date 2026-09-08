# Dark Colony Server 2.1
A standalone multiplayer server for the classic RTS game *Dark Colony* (1997), designed for
interoperability with the original, unmodified game.

Come and play with friends!  
HOWTO connect to the **online server**:  
Launch *Dark Colony* → MULTI PLAYER WAR → CONNECT TO SERVER → **dark-colony-server.fly.dev**

Then, in the lobby: the player rows other than your own are the **rooms** 1 to 7, each with its
own map; after the fixed number the row text scrolls the map name, its terrain (jungle or desert),
the player count and whether the room is open, and the map line repeats the selected room. Type `/1` … `/7` in the chat, hit
ENTER to select the room, and press **READY** to enter it. Inside the room press **READY** again when you want to fight. Your own
row shows your name; you can type it there.

---

## What the server does

- It replaces the in-game host. Slot 0 is a fake human player, **Mercenary**; real players get random
  free slots, so their start positions differ from game to game.
- **Seven rooms**, each with its own map (default: Plink - O, Armageddon, Black Widow, Circle of
  Friends, Olympus Mons, Hoops of Fury, Rings of fire; configurable with `ROOMS`). A newcomer
  first sees the room list in the lobby screen and picks a room with a chat command and READY; the
  name can be typed there, race, colour and team can be changed only inside a room.
- Everybody presses **READY**; when every real player is ready the game starts after a 3-second
  countdown. The chat window keeps a header pinned at the top (the server name and version in the
  room list, the room and its map inside a room); the server's own lines carry no name.
- Strict lockstep: one frame per server step, `[UNTIL][commands...]`, byte-identical for all clients,
  so every game runs exactly the same commands in exactly the same order.
- Game speed is fixed at 200 % (33 ms per tick); clients cannot change it.
- Cheats and the `0x08` checksum command are never forwarded.
- Clients that stop answering (keep-alives, load report, frame echoes) or violate the protocol are
  removed and announced to everybody else.
- `FAKE_PLAYERS=7` fills the lobby with fake humans for a solo game against idle bases.

Version 2.0 (September 2026) is a rewrite; version 1.x lives in the git history. Version 2.1 adds
the rooms and the room-selection lobby.

---

## Features / Goals
- Public internet server for players worldwide
- Up to seven rooms of up to 8 players each, one map per room, chosen inside the game's own lobby
  screen (fake players fill the rest on request)
- (TODO) Tournaments (fans vote online for players)
- (TODO) Admin commands (options)
- (TODO) Replays
- (TODO) Leaderboard
- (TODO) Missions with incremental complexity (aka "open world")

---

## Disclaimer

This project is a fan-made, open-source recreation of the original *Dark Colony* network server.  
It was developed through **reverse engineering** for the sole purpose of **interoperability** and **preservation** of the original multiplayer experience.  

This project **does not include or distribute any original game assets, binaries, or copyrighted material** from *Dark Colony*.  
You must own a legitimate copy of the game to use this software.

All trademarks and copyrights are the property of their respective owners.

---

## Reverse Engineering Methodology
- The server is an independent implementation of the game's network protocol, written from scratch;
  it contains no code taken from the game.
- The protocol was documented by analysing the game's network behaviour: captured network traffic
  (Wireshark) and, for version 2.0, a study of the game executable's network code. The results are in
  this repository: [`docs/DC16_NETWORK_PROTOCOL.md`](docs/DC16_NETWORK_PROTOCOL.md) (wire protocol) and
  [`docs/RELAY_SERVER_PLAN.md`](docs/RELAY_SERVER_PLAN.md) (design of this server, with a log of every
  finding from the live tests). The game files and the disassembly they cite are kept in the sister
  project [Dark-Colony](https://github.com/endotermic/Dark-Colony).
- Only interoperability information (message formats, timing, lobby rules) was used, for the purpose
  of letting the original game talk to a new server.

---

## For developers

### Run locally
1. Install [Node.js](https://nodejs.org) 20 or newer (no dependencies to install)
2. Download or clone this repo
3. Open a terminal in the project folder
4. Run:
   ```bash
   node src/index.js                                     # listens on 8888, seven rooms; one player may start alone
   LOG_LEVEL=debug node src/index.js                     # debug: every frame logged, full map view in battle
   FAKE_PLAYERS=7 node src/index.js                      # solo game against seven idle fakes
   MIN_PLAYERS=2 node src/index.js                       # a battle needs at least two real players
   HALL=false node src/index.js                          # no room selection: straight into room 1
   ROOMS=D8PLAY01,J8PLAY02,D4PLAY01 node src/index.js    # three rooms with these maps
   ```
5. Keep the terminal open while it runs
6. Launch *Dark Colony* → **MULTI PLAYER WAR** → **CONNECT TO SERVER**
7. Enter `localhost` as the IP address

### Tests
```bash
node --test test/                                         # unit and end-to-end tests with scripted clients
node tools/fakeclient.js --count 3 --duration 10000       # scripted clients against a running server
node tools/fakeclient.js --count 3 --behave noEcho        # the last one misbehaves
node tools/fakeclient.js --count 2 --room 2               # both pick room 2 in the hall and play there
```

### Configuration
Environment variables, see `src/config.js` for the full list and defaults: `PORT`, `ROOMS`, `HALL`,
`MARQUEE_MS`, `TICK_MS`, `MIN_PLAYERS`, `START_COUNTDOWN_S`, `FAKE_PLAYERS`, `FAKE_NAMES`,
`ALLOW_PAUSE`, `LAG_DROP_MS`, `STRICT_SEQ`, `DEBUG_MODE`, `LOG_LEVEL`, ... The map names for `ROOMS`
are the `SCENARIO/MPLAYER` file names (`D8PLAY01` = Armageddon); `src/maps.js` lists all 56.

### Deploy on Fly.io
```bash
fly deploy
fly logs
```
Raw TCP on port 8888 needs a dedicated IPv4 (`fly ips list`, `fly ips allocate-v4`).

---

## License
Licensed under the **GNU Affero General Public License v3 (AGPLv3)**.  
You can use, modify, and share this project — **as long as your version stays open source** under the same license.  

[Read full license →](./LICENSE)

---

(c) 2026 Nikolajs Agafonovs

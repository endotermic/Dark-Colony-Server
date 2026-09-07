# Dark Colony Server 2.0
A standalone multiplayer server for the classic RTS game *Dark Colony* (1997), designed for
interoperability with the original, unmodified game.

Come and play with friends!  
HOWTO connect to the **online server**:  
Launch *Dark Colony* → MULTI PLAYER WAR → CONNECT TO SERVER → **dark-colony-server.fly.dev**

---

## What the server does

- It replaces the in-game host. Slot 0 is a fake human player, **Mercenary**; real players get random
  free slots, so their start positions differ from game to game.
- The map **Armageddon** (8-player desert) is preselected.
- Everybody presses **READY**; when every real player is ready the game starts after a 3-second
  countdown. Each joiner gets one private greeting line from Mercenary with the server version.
- Strict lockstep: one frame per server step, `[UNTIL][commands...]`, byte-identical for all clients,
  so every game runs exactly the same commands in exactly the same order.
- Game speed is fixed at 200 % (33 ms per tick); clients cannot change it.
- Cheats and the `0x08` checksum command are never forwarded.
- Clients that stop answering (keep-alives, load report, frame echoes) or violate the protocol are
  removed and announced to everybody else.
- `FAKE_PLAYERS=7` fills the lobby with fake humans for a solo game against idle bases.

Version 2.0 (September 2026) is a rewrite; version 1.x lives in the git history.

---

## Features / Goals
- Public internet server for players worldwide
- One room of up to 8 players (fake players fill the rest on request)
- (TODO) Several rooms
- (TODO) Tournaments (fans vote online for players)
- (TODO) Admin commands (switch maps, options)
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
   node src/index.js                                     # listens on 8888
   MIN_PLAYERS=1 LOG_LEVEL=debug node src/index.js       # solo test, full map view
   FAKE_PLAYERS=7 MIN_PLAYERS=1 node src/index.js        # solo game against seven idle fakes
   ```
5. Keep the terminal open while it runs
6. Launch *Dark Colony* → **MULTI PLAYER WAR** → **CONNECT TO SERVER**
7. Enter `localhost` as the IP address

### Tests
```bash
node --test test/                                         # unit and end-to-end tests with scripted clients
node tools/fakeclient.js --count 3 --duration 10000       # scripted clients against a running server
node tools/fakeclient.js --count 3 --behave noEcho        # the last one misbehaves
```

### Configuration
Environment variables, see `src/config.js` for the full list and defaults: `PORT`, `MAP_FILE`,
`MAP_TITLE`, `MAP_TERRAIN`, `TICK_MS`, `MIN_PLAYERS`, `START_COUNTDOWN_S`, `FAKE_PLAYERS`, `FAKE_NAMES`,
`ALLOW_PAUSE`, `LAG_DROP_MS`, `STRICT_SEQ`, `DEBUG_MODE`, `LOG_LEVEL`, ...

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

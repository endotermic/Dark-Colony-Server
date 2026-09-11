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
- Game speed is fixed at 150 % (44 ms per tick, the single-player default of the patched exes); clients cannot change it.
- Cheats and the `0x08` checksum command are never forwarded.
- Clients that stop answering (keep-alives, load report, frame echoes) or violate the protocol are
  removed and announced to everybody else.
- `FAKE_PLAYERS=7` fills the lobby with fake humans for a solo game against idle bases.

Version 2.0 (September 2026) is a rewrite; version 1.x lives in the git history. Version 2.1 adds
the rooms and the room-selection lobby. The history of releases is in [`CHANGELOG.md`](CHANGELOG.md).

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

## Game patches and reverse-engineering notes

Since 10 Sep 2026 this repository also holds the single-player side of the project, moved here from
[Dark-Colony](https://github.com/endotermic/Dark-Colony) so that every note and tool about the game
executable is in one place. The game files themselves (`DC - Classic/`, `DC - Council wars/`, the map
editor) stay in Dark-Colony; the tools take a game directory or executable as an argument and need
only a stock Python 3 (no third-party packages).

- `docs/DC16_BATTLE_ENGINE.md` - combat core of `dc16.exe`: balance tables, targeting, firing,
  projectiles, damage, upgrades, day/night.
- `docs/DC16_DISPLAY_AND_RESOLUTION.md` - display pipeline (DirectDraw, software blitters, HUD
  geometry, mouse, movies), every resolution-dependent code site, the staged 1024x768 upgrade
  (sections 8-10), the Windows-pointer fix (section 10.12) and the OZI MISSIONS campaign mode
  (section 10.13).
- `docs/DC16_MAP_FILES.md` - the scenario file set (`.SCN`, `.MAP`, `.MTG`, `.PTH`, `.TRO`, `.POP`,
  `.OVH`/`.O16`, the `.BTS` tile sets): binary layouts, the text grammars, the coordinate system,
  what the game does with every field, and the JSON the server uses instead (section 12).
- `tools/map2json.js` - converts scenarios to that JSON (`node tools/map2json.js <folder | .SCN>
  [--out DIR] [--rooms] [--images] [--pretty]`; library + CLI, no dependencies). `maps/` holds the
  output for the seven maps of the default `ROOMS` only (plus `maps/index.json`); any other map is
  generated on request with the same command (maintainer decision, 11 Sep 2026).
- `tools/patch_resolution.py` - raises the screen resolution of `dc16.exe` / `DCEXP16.EXE`
  (verify / plan / apply, staged, byte-checked, keeps a `.bak`).
- `tools/patch_cursor.py` - keeps the Windows mouse pointer hidden over the game window (the stock
  exe registers its window class with an uninitialised cursor handle and lets `DefWindowProc`
  restore it on every `WM_SETCURSOR`); verify / plan / apply, keeps a `.cursor.bak`.
- `tools/patch_ozi_menu.py` - adds the "OZI MISSIONS" campaign mode to Council Wars' `DCEXP16.EXE`
  (the ozi_ns mission pack, 22 missions, selectable from the main menu in place of PLAY INTRO;
  verify / plan / apply, keeps a `.ozi.bak`); `tools/build_ozi_overlay.py` installs the pack's data
  (dry run / `--apply`). Section 10.13 of the display document explains both.
- `tools/patch_pool.py` - enlarges the game's single memory arena (`smalloc.c` local pool) from
  11.5 MB to 32 MB in both exes; the 1024x768 screens and the pack's extra unit banks had used up
  the stock headroom (verify / plan / apply, keeps a `.pool.bak`).
- `tools/patch_speed.py` - sets the default game speed (the tick length the game-state initialiser
  writes; 100 % -> 150 %) in both exes; the options slider still works (verify / plan / apply).
- `tools/hud_layout.py` - redraws the in-game HUD frame for the new resolution (region geometry,
  tracing layers, the `MAINE` widget transform).
- `tools/pad_background.py` - letterboxes the interface screens into a larger framebuffer (plan /
  apply / revert).
- `tools/paint_intro.py` - repaints the main-menu backdrops at any framebuffer size and re-bakes
  them into the logo animations; `tools/logo_art.py` re-sets the title; `tools/spr.py` is the
  `.SPR` sprite codec they share.

```bash
python tools/patch_cursor.py verify "../Dark-Colony/DC - Classic/dc16.exe"
python tools/patch_resolution.py verify "../Dark-Colony/DC - Council wars/DCEXP16.EXE"
python tools/patch_ozi_menu.py verify "../Dark-Colony/DC - Council wars/DCEXP16.EXE"
python tools/patch_pool.py verify "../Dark-Colony/DC - Classic/dc16.exe"
python tools/patch_speed.py verify "../Dark-Colony/DC - Council wars/DCEXP16.EXE"
python tools/build_ozi_overlay.py "../Dark-Colony/DC - Council wars"          # dry run
node tools/map2json.js "../Dark-Colony/DC - Classic/SCENARIO/MPLAYER" --rooms --out maps   # regenerate maps/ (default rooms)
node tools/map2json.js "../Dark-Colony/DC - Classic/SCENARIO/MPLAYER/D4PLAY01.SCN" > four_corners.json   # any other map, on request
```

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
node tools/smoketest.js                                   # smoke test of the live server: 7 bots fill room 1, 3 wait in room 2 for you
node tools/smoketest.js --host 127.0.0.1 --plan 1:7:hold,2:3:follow,3:2:auto   # local server; room 3 gets a battle among bots
```
The smoke test keeps its bots connected until Ctrl+C: connect with the game meanwhile and check the
room list (`1 ... (7/7) full`, `2 ... (3/7) open`), that `/1` + READY is refused, and that READY in
room 2 starts a battle with the three bots (they press READY when you do).

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

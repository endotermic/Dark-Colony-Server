# Changelog

Dates are the commit dates on `main`. Details of every finding and decision are in
[`docs/RELAY_SERVER_PLAN.md`](docs/RELAY_SERVER_PLAN.md) (section 16 is the dated change log of the
live tests); the wire protocol is in [`docs/DC16_NETWORK_PROTOCOL.md`](docs/DC16_NETWORK_PROTOCOL.md).

## Unreleased

- **One game folder: Classic moves into `DC - Council wars/`, `DC - Classic/` removed** (15 Sep 2026, maintainer decision;
  `tools/gen_apply_script.py`, the three tests that read the game folder, README). The untouched
  Classic exe of 7 Jan 1998 is now `DC - Council wars/dc16.exe` (restored from the game repository's
  first commit, byte-identical to the former `DC - Classic/dc16original1998.exe`) and the patcher
  writes its patched build as `dc16new.exe` beside it (`OutputName`; all eight Classic fixes
  still reproduce the published exe byte for byte, checked under pwsh and PowerShell 5.1). Everything
  the Classic folder held that the Council Wars folder lacked was copied over (`MISSION/` briefings,
  `ENCYCLO/`, `WALLPAPR/`, `SCENARIO/MULTI-~1/`, the `PMAP.EXE` map tool and its palette files, the
  editor `.SET`/`.JUS` files, icon sources; the Classic movies that clash with Council Wars' own names
  as `AVI/DCINTRO.AVI`, `DCAENDING.AVI`, `DCHENDING.AVI` for a future exe patch), and the Council
  Wars copy of `SCENARIO/HUMAN/HUMAN09.TRO`, whose trigger condition carried a `&&==` typo, was
  replaced by the Classic one. `test/engine-anim.test.js`, `test/engine-tables.test.js` and
  `test/map2json.test.js` read `DC - Council wars` (override `DC_CLASSIC_DIR`); `data/classic/*.json`
  regenerated from that folder (only the recorded source folder name changed). The untouched Classic
  exe still needs a CD image with a `/DC/` tree on the drive named in `HBNFUFL.A01`.

- **Classic movies under their own names** (15 Sep 2026; `tools/patch_movies.py`, patch id `movies`,
  Dark Colony only, doc §10.18): `dc16new.exe` plays `AVI/DCINTRO.AVI` (the DGROUP string
  `intro.avi` plus its two alignment padding zeros is exactly `dcintro.avi`, rewritten in place at
  file `0x7FCA8`, no code or `.reloc` change) and the campaign lists `INTRF_HD/HSCENE.TXT` /
  `GSCENE.TXT` (line 154) name `avi/dchending.avi` / `avi/dcaending.avi`; the stock `GAMESTAT/` lists
  and Council Wars' own three movies are untouched. The tool refuses the Council Wars exe. Patcher
  regenerated (Classic: nine fixes; `Data` = the three movies). Smoke test: the exe starts at 1024x768
  with an empty `error.log`; the movies themselves are not yet checked in game. The patched build was
  first named `dc16patched.exe`, which Windows' installer heuristic (manifest-less exe named `*patch*`)
  takes for a setup program and runs elevated after a UAC prompt; the maintainer renamed it
  **`dc16new.exe`** (patcher `OutputName`, docs and tests follow). The Council Wars pair follows the
  same scheme: the untouched exe is back to its CD name **`ENGEXP16.EXE`** (was `engexp16original.exe`),
  the patched build is **`engexp16new.exe`** (was `DCEXP16.EXE` since 10 Sep 2026); `git mv` in the game
  repository, patcher regenerated, byte-identical rebuilds re-checked. The map editor files copied
  into the Council Wars folder the same day were removed again at the maintainer's request; the editor
  stays in `Dark Colony - Map editor/` (the Council Wars CD has no editor, the Classic CD's is
  byte-identical to that folder's `maped.exe`).

- **Map editor build in the patcher** (15 Sep 2026; `tools/patch_maped.py`, `gen_apply_script.py` third
  build `MapEditor`): a section-by-section diff showed that the ozi_ns editor
  `maped_by_ozy_ns_v1.2PL.exe` has byte-identical CODE/`.idata`/`.edata`/`.reloc`/`.debug` sections -
  no code fix; besides Polish DIALOG/MENU texts, an icon and retitled strings its only functional change
  is the `WS_DISABLED` bit (0x08000000) cleared on controls the original greyed out. The patcher now
  rebuilds exactly that, English, from `Dark Colony - Map editor\maped.exe` into
  `maped_ozi_ns_v1.2.exe`: fixes `blocksets` (New Map: Atlantis / Training Set / Special Set buttons,
  routed by the WM_COMMAND table at `0x4122A5` to `atlantis/htrain/special.bts`), `teams` (Team Colour +
  Allies radio groups, read by the RACE procedure `0x41E7B9`), `healer` (Troop Attributes Healer row,
  edit read at `0x41FE16`), `troopsframe` (THICKFRAME -> SYSMENU); 24 one-byte edits located by walking
  the resource tree and the DLGTEMPLATEs. The generator learned builds without a CD-check step and
  per-step tool arguments. The unlocked block sets need `scenario\atlantis.set`, `trainh.set`,
  `special.set`, `special.bts` from the ozi_ns pack (not in the repository).

- **Patcher: fixes without their resources are greyed out** (15 Sep 2026, maintainer request;
  `tools/gen_apply_script.py` → `Get-UnavailableFixes`): the window checks every fix's `Data` files
  against the "Write to" folder (re-checked when the path changes); a fix whose files are missing, or
  which requires such a fix, is labelled `[RESOURCES NOT FOUND]`, cannot be ticked, is skipped by
  "Select all" and explains itself in the detail panel. `-All` applies the available fixes and prints
  the skipped ones; an explicit `-Patches` naming one is still refused unless `-IgnoreMissingData`.
  Resources are checked as separate groups: interface files (`INTRF_HD\`, `*_HD` sprites/FINs) on
  `resolution`/`hdpaths`, the `AVI\DC*.AVI` movies on `movies`, the `ozi_ns` overlay on `ozi`. Also copied into the Council Wars folder: the map editor `MAPED.EXE` (byte-identical
  to `DC\MAPED.EXE` on the Dark Colony CD; the Council Wars CD has no editor, the Classic CD's
  `EDITOR\` folder is only its InstallShield kit), `BWCC.DLL`, `BWCC32.DLL`, `CW3215MT.DLL`,
  `readme.doc` and `maped_by_ozy_ns_v1.2PL.exe`.

- **Patcher safeguard** (14 Sep 2026, maintainer request; `tools/gen_apply_script.py`): every fix in
  `Apply-DarkColonyPatches.ps1` now lists the fixes it only works together with (`Requires`) and the
  data files it needs next to the exe (`Data`, enumerated from the repository with `git ls-files`:
  60/65 files for 1024x768, 407 for OZI MISSIONS). Before writing, window and command line check both
  and refuse with the list of what is missing, because an exe patched into a folder without
  `INTRF_HD/` fails at start-up and looks like a bug of the patch. `-IgnoreMissingData` overrides on
  the command line; `-List` shows the requirements.

- **Game data: stock 640x480 and 1024x768 files side by side** (14 Sep 2026, maintainer request;
  `tools/split_hd_data.py`, `tools/patch_hd_paths.py`, doc `DC16_DISPLAY_AND_RESOLUTION.md` §10.17):
  the rebuilt menus, HUD frame, loading screens, briefing lists and re-baked logo sprites moved from
  the stock names into `INTRF_HD/` (Council Wars also `exp/intrf_hd/`, `ozi_ns/intrf_hd/`;
  `SPRITES/*_HD.SPR` + `ANIMATE/*_HD.FIN`), the stock files are back under their original names, and
  the patched exes read `intrf_hd/...` through 30 rewritten path strings (patch id `hdpaths` in
  `Apply-DarkColonyPatches.ps1`, regenerated). The untouched original exes now run from the same
  folder. `build_ozi_overlay.py` writes the pack's screens and lists to `ozi_ns/intrf_hd/`;
  `pad_background.py` accepts an `INTRF_HD` folder. First test: the Classic original runs; the
  Council Wars original without its CD asserts on its own menu script (`widget.c` 152: the no-CD
  path greys buttons the shipped `exp/intrface/bintroe` comments out - retail behaviour), a
  CD-free 640x480 build (`-Patches cdcheck`, confirmed running) is left to the patcher and not
  committed (maintainer decision), and Council Wars' own menu backdrops `exp/intrface/intrg.gif` /
  `intro.gif` came from the CD. Both untouched originals run with the Council Wars CD image mounted
  as `D:`. Rule since then: every file the original exes
  read is byte-identical to the CD (inventory against the whole `/EXPENG/` tree). The OZI pack's base
  set therefore moved off the stock names: `exp/anim.dat`, `tran.fin`, `tran.spr` are stock again,
  the patched exe opens `exp/animozi.dat` (one more string edit in the `ozi` patch,
  `patch_ozi_menu.py DGROUP_SITES`) with `tranozi.fin` / `tranozi.spr`, all written by
  `build_ozi_overlay.py`. The original's CD test (`0x405E8C`) is documented: it needs
  `<hbnfufl.a02 drive>:\dc\anim.dat` readable and the drive write-protected, which a mounted `.iso`
  of the CD satisfies. OZI mode after the rename untested.

## 2.3 — 13 September 2026 (tag `v2.3.0`)

The fake players play: two rusher bots (AI Mercenary, AI Marauder) that sell a two-minute alliance
for 1000 money, keep the peace among themselves, defend their bases and take over the seat of a
player who leaves; victory by alliance works in the real game. The game's own AI is documented, money
gifts between humans work again, and the game exes no longer hang at start-up on two-monitor PCs.
Everything below was deployed to Fly as it landed (12–13 Sep 2026); the lobby greeting now says
`Dark Colony server 2.3`.

- **Game exes: no more start-up hang on two-monitor PCs** (13 Sep 2026, `tools/patch_ddraw_lost.py`,
  doc `DC16_DISPLAY_AND_RESOLUTION.md` §10.16): the 1024x768 mode switch makes Windows move the second
  monitor, DirectDraw marks the game's surfaces lost, and the stock start-up code asserted into a
  message box hidden behind the full-screen surface. Four failure branches (three in the palette
  remap, one in the loading screen) are now non-fatal; the game's own per-frame restore repairs the
  surfaces. Both `dc16.exe` and `DCEXP16.EXE` in the Dark-Colony repository carry it.

- **AI Mercenary plays** (13 Sep 2026, maintainer request; plan §19.8, facts F47-F48): the fake host
  has a rushing character (a worker to a vent, a barracks, cheap infantry non-stop, a four-unit wave
  at the nearest enemy base and reinforcements in pairs) and sells an alliance: whoever gives it
  1000 with the diplomacy screen's "give 1000" button becomes its ally with shared vision for
  `MERCENARY_ALLY_S` (120) seconds; payments arriving while an alliance runs are returned. Every
  decision is said aloud in the battlefield chat, starting with the offer in the first frame. New
  settings `MERCENARY_AI` (`rusher`, or `off` for the idle base), `MERCENARY_ALLY_S`,
  `MERCENARY_THINK_TICKS`; needs the engine (`SYNC_CHECK` `shadow`/`send`). The lobby greeting
  announces the deal. New modules `src/rusher.js`, `src/mercenary.js`.
- **A second bot, AI Marauder** (13 Sep 2026, maintainer request): `FAKE_PLAYERS` defaults to 2 and
  every fake human is a bot with the same rush and its own alliance for sale, so a lone player can
  fight both, buy one of them, or set them against each other. The hall shows room sizes without the
  fakes ("(0/6)" on an 8-player map). 16 new tests (209).
- **Alliances both ways, victory by alliance, bots for players who leave** (13 Sep 2026, maintainer
  request; plan §19.9, fact F49): the bought alliance is set in both directions (alliance and vision),
  so the payer need not touch the diplomacy screen and the bot's troops stop at once. The game ends
  a battle, with Victory for everyone alive, as soon as all alive players are mutually allied
  (`0x40E260`), so a lone player wins by buying the alliance of every remaining bot within the two
  minutes. A client that leaves a running or loading battle is taken over by a new bot (speaking as
  `AI <name>`, money normalised) instead of being handed to the game's own AI with `DISCONNECT`; the
  engine stays in step. Without the engine the old `DISCONNECT` path remains.
- **Pacts between bots, actions told to the ally only** (13 Sep 2026, after the first live test of
  the bots; plan §16, §19.9, F49 corrected): the game's end check compares every alive player with
  the *first* alive one, so a player allied with two rival bots did not win. Bots whose current
  allies are the same player now ally with each other for as long as both deals hold, which makes
  the victory by alliance work. A bot's decisions are now chat lines to its ally alone (the in-game
  chat's player mask) and are not sent while it has no ally; the offer stays public, the deal and
  its end go to the player concerned. 2 new tests (213).
- **The bots keep the peace among themselves** (13 Sep 2026, maintainer request after the third
  live test, which confirmed the victory by alliance in the real game): unhired bots are allied with
  each other from the first frame; a bot that is bought turns on the bots that do not serve its
  ally, and the peace returns when its deal ends. A lone player faces two allied bots instead of
  two bots at war with each other.
- **The rusher defends its base** (13 Sep 2026, maintainer request): enemy units within 10 tiles of
  an own building pause the rush; the soldiers at home assault the nearest intruder, the nearest
  units on their way come back when the home guard is short (three per intruder, at least four; a
  live test had shown the whole army walking home for one scout), and the troops march on again
  once the base is clear. New rusher settings `defendRadius`, `defendReorderTicks`,
  `defendPerIntruder`, `defendMin`. 1 new test (214).
- **Money gifts work again** (13 Sep 2026): the in-game command `0x0F` is the diplomacy screen's
  "give 1000", not a cheat; the relay had dropped it since 2.0, so a gift between humans vanished
  while the giver still paid. It is now relayed (F47).
- **Engine fixes** (13 Sep 2026): the DEPEND loader now fills the field names `depend.c` reads (every
  dependency check on real tables had answered "unavailable"), and a building's death no longer
  calls `depRecompute` without a player. Neither affects a checksum.
- **The game's AI documented** (12 Sep 2026): [`docs/DC16_AI.md`](docs/DC16_AI.md) describes the
  computer player of `dc16.exe` ("Krusty") from the disassembly — schedule, command path, state
  layout, influence map, census, production goals, the four tasks, group movement, `aimsg`, save/load.
  `DC16_BATTLE_ENGINE.md` §17 now points there and lists what it had wrong.
- **Alive bots planned** (12 Sep 2026, maintainer request; nothing implemented yet): plan §19 designs
  Krusty instances run by the server for the fake human players, their decisions travelling as
  ordinary commands in the sync frames (facts F43–F46). Settings foreseen: `BOTS`, `BOT_THINK_TICKS`,
  `BOT_SPLIT_PERCENT`, `BOT_CLASS_WEIGHTS`, `BOT_SEED`.

## 2.2 — 12 September 2026 (tag `v2.2.0`)

The battle engine runs beside the relay and puts lockstep checksums into the sync frames, the hall
preselects no room, the fake host is called AI Mercenary and introduces itself, the default speed is
150 %, and the scenario files are documented and converted to JSON. Everything below was deployed to
Fly as it landed (8–12 Sep 2026); the lobby greeting now says `Dark Colony server 2.2`.

- **AI Mercenary** (12 Sep 2026, maintainer decision): the fake host in slot 0 is called
  `AI Mercenary` (`MERCENARY_NAME` default, was `Mercenary`), and the room greeting gets a second
  header line from it: `AI Mercenary: Hi! I am an AI bot and the host of this game. My base stays
  idle.` (wrapped at 40 columns, pinned under the room line). It is the one relay line that carries a
  name; everything else the server writes stays nameless.
- **No preselected room in the hall** (12 Sep 2026, maintainer decision): a newcomer has no room
  selected. The map line is **empty** (`'i' "", ""`), which makes the game grey out its READY
  button by itself until a room is chosen (the lobby refresh disables it while the scenario file
  name is empty, plan F42); the pinned header ends with `No room selected. Type /1../7 + ENTER.`.
  Should a READY arrive anyway it is refused with `Select a room first: /1../7 + ENTER.`. Until now
  the lowest-numbered joinable room was preselected, so READY alone joined it. `tools/fakeclient.js`
  without `--room` stays in the hall. One new test, six adjusted (193).
- **Battle engine beside the relay** (11 Sep 2026, plan §18; deployed to Fly with `SYNC_CHECK=send` the same day): the server can run a port of the
  game's simulation core (`src/engine/`, bit-exact memory layout of `dc16.exe`'s game state) and
  put the lockstep checksum command `0x08 (checksum, tick)` into its sync frames, which no client
  ever sends on this server (the fake host is the lowest network id). New settings: `SYNC_CHECK`
  (`off` default, `shadow` = run and compare only, `send` = checksums in every frame), `RECORD_DIR`
  (JSON-lines recording of every battle for `tools/replay.js`), `MERCENARY_SLOT` (move the fake
  host so that a real player sends checksums for verification; slot 0 is then never seated). The
  engine is optional and loaded in the background; any failure leaves the room a plain relay. Facts
  F38–F41 (when a frame's commands execute, which tick a checksum names, the RNG seed of the start
  shuffle) were read from the client's pacing code. The simulation modules are ported
  (`src/engine/PORTING.md`, `data/classic/*.json`, `tools/gamestat2json.js`, `tools/sprdata2json.js`);
  the AI is not. Two real-client games recorded the same day replay bit-exactly: 90 seconds (2005 checksums)
  and 8.5 minutes with fighting, upgrades and napalm (11512 checksums), 0 mismatches; in `send` mode
  two real clients played a full LAN game accepting 2775 server checksums (plan §16). Three port
  bugs found by the recordings were fixed on the way. 106 new tests (191).
- Scenario files documented and converted to JSON (11 Sep 2026): `docs/DC16_MAP_FILES.md` describes
  the `.SCN`/`.MAP`/`.MTG`/`.PTH`/`.TRO`/`.POP`/`.OVH` formats from the game's loaders (row order,
  tile numbering through the `.BTS` remap, the attribute word's blocking bit, the TEAM block, the
  object list, the trigger grammar); `tools/map2json.js` converts any scenario (Classic, Council
  Wars, OZI pack all parse) and `maps/` holds the seven maps of the default `ROOMS` as JSON with an
  `index.json` (`--rooms`; every other map is generated on request, maintainer decision). Nine new
  tests (76). Server code unchanged.
- Game speed 150 % (`TICK_MS` 44 ms) instead of 200 %, matching the single-player default of the
  patched exes (maintainer decision, 10 Sep 2026); tests pin the tick tests to 33 ms.
- `tools/smoketest.js`: smoke test of a running server with the real game. Scripted clients enter the
  rooms through the hall by a plan (`room:count:policy`, default seven bots that fill room 1 and three
  in room 2 that press READY only when a real player does) and stay until Ctrl+C; an observer in the
  hall reads the map line for every room and checks that READY on a full room is refused. The
  scripted client (`tools/fakeclient.js`) learnt the READY policies `auto`, `hold`, `follow` and
  announces its name; one more integration test pins them (67 tests). Server code unchanged.

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

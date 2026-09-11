# Merging Classic into Council Wars: one executable, one game folder

Feasibility study, written 11 Sep 2026 by comparing the two shipped executables and game folders.
The question was whether `DCEXP16.EXE` (Council Wars) can take over the Classic-only features -
the Classic campaign and training missions, the Classic videos and, above all, TCP/IP multiplayer -
so that the project maintains **one** executable and **one** game folder instead of two "lifelines".

Sources: `dc16.asm` and `dcexp16.asm` (`dumpbin -ALL -DISASM` of the current patched
`DC - Classic/dc16.exe` and `DC - Council wars/DCEXP16.EXE`, both 1024x768 + cursor + pool + speed
patched; DCEXP16 also carries the OZI MISSIONS patch), the printable strings of both binaries, and
a file-by-file comparison of the two game folders in the `Dark-Colony` repository. Addresses are
virtual and refer to **DCEXP16.EXE** unless marked "Classic"; the Council Wars code sits 0x60 bytes
above the Classic code from `0x4063E4` onwards (§2). Facts marked **(verified)** were read from the
code or from byte comparisons; **(inferred)** facts are conclusions from those; **(untested)** means
nobody has run it in the game yet.

Status: **assessment only, nothing changed.** Neither executable, nor any data file, nor the relay
server plan was modified for this document.

---

## 1. Short answer

**The merge is feasible and mostly a data job.** `DCEXP16.EXE` is the same program as `dc16.exe`:
the network code, the Classic campaign selection, the training campaign and the encyclopedia are
all compiled in and reachable from the main-menu button handler. Council Wars hides them only by
commenting out four rows of its menu script (`exp/intrface/bintroe`). The Council Wars folder already
contains every Classic scenario, every balance table, every network UI script and 58 of the 59
Classic videos.

What is genuinely missing is small: the Classic briefing sounds (`MISSION/`, 3.1 MB), the
encyclopedia assets (`ENCYCLO/`, 25 MB), the Classic intro video (28 MB, name clash), and one exe
detail - four artifact-unlock constants tuned per campaign - that needs a small mode-aware stub.
Everything else can reuse the mode mechanism (writable prefix slots + zero-tail stubs) built for the
OZI MISSIONS pack (`DC16_DISPLAY_AND_RESOLUTION.md` §10.13).

Two things are **untested**: that the expansion build really completes a game over TCP against the
relay server, and that a Classic campaign started from the expansion build plays through. Both are
expected to work because the code is byte-for-byte the same, but §7 lists the checks.

---

## 2. How different are the two executables?

### 2.1 Method

Both disassemblies were parsed into instruction lists, every absolute operand in the image range
(`0x40xxxx`..`0x5xxxxx`, with or without the `h` suffix) was replaced by a placeholder, and the two
lists were aligned with a longest-common-subsequence diff. Anything left is a real difference in
code or in immediate constants; pure address shifts disappear.

### 2.2 Result **(verified)**

| Where (DCEXP16 / Classic) | Difference | Meaning |
|---|---|---|
| `0x403FC7`, `0x403FE8`, `0x4040B3`, `0x4040D4` / same | `cmp byte ptr [eax],imm8`: Classic 5, 0Eh, 4, 0Eh; CW 0Eh, 7, 0Eh, 7 | Artifact-unlock schedule of the campaign (§5.3) |
| `0x404E99` / same | `mov ebx,1AFh` vs `189h` | Not a build difference: the credits-box y that `patch_resolution.py` moves (fixup `0x4299`) |
| `0x405050..0x4050FB` / `0x405050..0x405134` | Main-menu handler | Council Wars sets `gs+0x14F4=1` for NEW CAMPAIGN (Classic: 0); the rest of the hunk is our OZI patch |
| `0x405271` / `0x405277` | `call 0x40B490` vs `call 0x40B430` + a string copy | Start-up: order of the CD-root string copy differs; same effect (§6.3) |
| `0x4063E4..0x406472` / - | **inserted function, 0x8E bytes, padded to 0x60 shift** | The `exp/` overlay open helper: prefix at `0x4826D0`, fallback to bare name via `0x4061BC`. Classic has no overlay mechanism at all |
| everywhere after | none | All remaining hunks are inline jump tables decoded as garbage (`0x408D08`, `0x40A43C`, `0x415AE0`, `0x41723D`, `0x417450`, ...) or DGROUP address shifts |

Imports are identical (both load `WSOCK32.dll`, both contain `get_tcp_network`, `create_network`,
`init_dplay_network`, the lobby scripts and the sync code). The TCP port constant `22B8h` (8888) is
at `0x405BEB`, `0x42DCA1`, `0x42DE2A` (Classic `0x405C0B`, `0x42DC41`, `0x42DDCA`).

### 2.3 Strings **(verified)**

The printable-string sets of the two binaries differ only in the CD-check texts ("Please insert The
Dark Colony CD" vs "... Expansion Pak CD - The Council Wars") and the drive-letter file name
(`hbnfufl.a01` vs `hbnfufl.a02`). Every campaign, lobby, network, video and encyclopedia string is in
both.

### 2.4 Consequence

`DC16_NETWORK_PROTOCOL.md` says the message formats "were not separately verified" on the expansion
build, and `RELAY_SERVER_PLAN.md` fact **F27** states that "the Council Wars executable has no working
network play". F27 is a maintainer assumption, not a binary fact: the network code of the two
builds is identical, it is only unreachable from the expansion's menu. F27 should be reworded once
§7.1 has been run.

---

## 3. The main menu is the whole difference

### 3.1 Button handler `0x405000` **(verified)**

The main-menu script `intrface/bintroe` (opened through the prefix helper, so `exp/intrface/bintroe`
in Council Wars) defines `pushb <id> ...` rows; the handler switches on the id in `edi`:

| id | Label (`textmsg`) | Code | What it does |
|---|---|---|---|
| 0 | NEW CAMPAIGN | `0x405050` | `gs+0x14F4 = 1` (Classic: 0), `gs+0x14F0 = 0`, `call 0x401C08` (now through `tramp_cw_campaign 0x47F2E0`) |
| 1 | TRAINING | `0x405076` | `gs+0x14F0 = 3`, `call 0x401C08` (same trampoline) |
| 2 | LOAD GAME | `0x4050BF` | `call 0x403AA4` (now through `tramp_cw_load 0x47F2F0`) |
| 3 | MULTI PLAYER WAR | `0x405097` | `call 0x405C20` - the **netopt** screen (§4.1) |
| 4 | SINGLE PLAYER WAR | `0x4050AB` | `call 0x405AE4` - reads `intrface/server.dat` + `getsvr`, calls `get_tcp_network 0x42E024` and the game start `0x40122C`; **now OZI LOAD** (`tramp_pack_load 0x47F300`) |
| 5 | ENCYCLOPEDIA | `0x4050D1` | `call 0x402614` - fonts `mfonto5`, `intrface/ency.dat`, `intrface/encyclo`, `%s.txt` per unit (§5.5) |
| 0x10 | PLAY INTRO | `0x4050DD` | Classic: CD-root + `intro.avi` -> `0x401028`; **now OZI MISSIONS** |
| 0xC | QUIT | `0x405022` | shutdown |

Classic addresses: 3 -> `0x405C40`, 4 -> `0x405B04`, all others identical.

### 3.2 Menu scripts **(verified)**

| File | Rows present |
|---|---|
| `DC - Classic/INTRFACE/bintroe` | 0, 1, 2 at x=332; 3, 4, 5, 12 at x=512; 16 at x=332 - two columns, all eight buttons |
| `DC - Council wars/INTRFACE/bintroe` (root copy, unused) | byte-identical to Classic's |
| `DC - Council wars/exp/intrface/bintroe`, stock (`0307feb`) | rows **1, 3, 4, 5 commented out with `%`**; 0, 2, 12, 16 in one column at x=228 (640x480) |
| same file now | one column at x=422: NEW CAMPAIGN 541, LOAD GAME 567, OZI MISSIONS (16) 593, OZI LOAD (4) 619, QUIT 645; rows 1, 3, 5 still commented |

The `textmsg` labels 4 "MULTI PLAYER WAR" and 6 "ENCYCLOPEDIA" are still in the expansion script.
Restoring a button is therefore one un-commented row (plus layout).

### 3.3 The lobby and network screens exist in the Council Wars root **(verified)**

Byte comparison Classic `INTRFACE/` vs `DC - Council wars/INTRFACE/` (root, reached by the prefix
helper's fallback): `netopte`, `getsvre`, `multiwne`, `multie`, `dplayse`, `ipxnamee`, `encycloe`,
`shumane`, `lopte`, `bintroe`, `net.dat`, `server.dat`, `multiwin.dat`, `tcpwait.dat`, `ency.dat`,
`shuman.dat`, `BLEW.SPR`, `MFONTO5.SPR` are **all identical** to Classic's current files, i.e. they
are already the 1024x768 versions produced during the resolution work.

---

## 4. Networking

### 4.1 Path through the code **(verified, untested in game)**

MULTI PLAYER WAR (`0x405C20`) draws `intrface/netopt` + `net.dat` (background `blew`). Its
sub-handlers: `0x4056CC` (DirectPlay: `intrface/dplays`, `ipxname`, `do_dplay_net`, "(No
Sessions)"), `0x405AB0` (ACT AS SERVER), `0x405B20` (CONNECT TO SERVER: `intrface/getsvr` +
`server.dat`, then `get_tcp_network 0x42E024` with port 8888 at `0x405BEB`, then the game start
`0x40122C`). This is exactly the route `RELAY_SERVER_PLAN.md` §1 describes for Classic ("MULTI PLAYER
WAR -> CONNECT TO SERVER"), 0x60 bytes higher.

The CD gate inside this screen was already neutralised in both builds (Classic file offset `0x509F`,
DCEXP16 `0x507F` = `0x405C7F`: `75 -> EB`).

### 4.2 Sync compatibility with Classic clients and the server engine **(verified data, inferred conclusion)**

A multiplayer game loads its balance tables per game start (`0x41BB50 -> 0x43C4AC`, see the
11 Sep note in `DC16_BATTLE_ENGINE.md`) through the prefix:

| Table | Council Wars loads | Relation to Classic |
|---|---|---|
| `gamestat/gamestat.txt` (object types) | `exp/gamestat/gamestat.txt` | Classic's 106 rows **unchanged**, 12 rows appended (types 106..117: VATO and the other expansion units); count 118 |
| `weapstat`, `boomstat`, `mbullet`, `unitid` | root `GAMESTAT/*.TXT` (no `exp/` copy) | **byte-identical** to Classic |
| `anim.dat`, sprite banks, `sound2.dat` | `exp/` copies | superset / different sounds - not part of the simulation |

The 56 multiplayer maps place only Classic object types, and the relay server never hands a slot to
the AI, so an expansion client should produce the same `0x08` checksums as a Classic client and as
the server's engine port (`data/classic/gamestat.json`). The residual risk is the AI after a
`DISCONNECT`: with 118 types its production choices could differ from a Classic client's. The clean
fix is to enter multiplayer in a "Classic mode" (§5.2) so the tables come from the root, which makes
the expansion client indistinguishable from `dc16.exe`.

### 4.3 What to do

1. Un-comment `pushb 3` in `exp/intrface/bintroe` (and re-layout, §6.1). Nothing else is needed on
   paper.
2. Optionally route button 3 through a `stub_classic_set` (§5.2) before `0x405C20`.
3. Test §7.1, then fix F27 and the protocol doc's caveat.

---

## 5. Classic campaign, training and encyclopedia

### 5.1 How the campaign is selected **(verified)**

Scene-list choice (`0x402FED`, repeated at `0x403C9D` for the load path):

```
if (gs+0x14F0 != 0)      name = race ? "gamestat/gtscene" : "gamestat/htscene"   // training
else                     name = race ? "gamestat/gscene"  : "gamestat/hscene"    // Classic
if (gs+0x14F4 != 0)      name = race ? "gamestat/gxscene" : "gamestat/hxscene"   // Council Wars
```

(`gs+0x14E4` = race, `.txt` appended, opened through the prefix helper.) So a Classic campaign in
the expansion build is nothing more than `gs+0x14F4 = 0`, `gs+0x14F0 = 0` before `call 0x401C08`,
which is what Classic's own NEW CAMPAIGN handler does. With the `exp/` prefix active the helper
looks for `exp/gamestat/hscene.txt`, does not find it, and falls back to root `GAMESTAT/HSCENE.TXT` -
which is present and identical to Classic's (apart from the 1024x768 `%label` shifts applied to
both). The mission files it names (`SCENARIO/HUMAN`, `SCENARIO/ALIEN`, training `SCENARIO/TEST`,
`HTRAIN.BTS`) are in the Council Wars root and identical to Classic's, with one exception (§5.4).

Balance in that campaign would come from `exp/gamestat/gamestat.txt` (118 types, first 106 identical),
which only matters for the AI's unit choice; a Classic-mode prefix (§5.2) avoids even that.

### 5.2 A "Classic mode" via the existing slot mechanism **(inferred)**

The OZI patch established that a campaign *mode* is the content of four writable DGROUP slots: the
file prefix `0x4826D0` (`exp/`), the wave-loader prefix `0x487DC8`, and the two save-folder names
`0x482344` / `0x485E5C` (`esave`). `stub_pack 0x47F240` and `stub_cw_set 0x47F290` write them. A
third stub, `stub_classic_set`, would write a prefix that matches nothing (e.g. `dc/`, so every
open falls through to the root) or a real overlay folder `classic/` for Classic-only files, and
`save` as the save folder. Classic saves would then live in `save/` as before.

Handlers needed (all in the zero tail like the OZI ones, 8-byte relocs in block `0x7F000`):

| Menu button | Sets | Then |
|---|---|---|
| CLASSIC CAMPAIGN (new id, e.g. reuse 1 or a free id) | `gs+0x14F4=0`, `gs+0x14F0=0`, `stub_classic_set` | `call 0x401C08` |
| TRAINING (id 1, un-commented) | `gs+0x14F0=3`, `stub_classic_set` | `call 0x401C08` (replace the `tramp_cw_campaign` call at `0x405083`) |
| CLASSIC LOAD | `stub_classic_set` | `jmp 0x403AA4` (a third load trampoline, deterministic like OZI LOAD) |
| MULTI PLAYER WAR (id 3) | `stub_classic_set` (optional, §4.2) | `call 0x405C20` |

The mode is sticky for everything else, exactly as today. The main-menu script has room for ids
that the handler already knows; a brand-new id would need a new `cmp edi,imm` branch, so reusing
1 (TRAINING) and 4/5 where possible keeps the code patch small.

### 5.3 The four artifact-unlock constants **(verified data, inferred meaning)**

`0x403F60..0x40414D` is the campaign-progress routine that fills the six "Alien Artifacts" slots
`gs+0x1500..gs+0x1514` (values 1..6) from the current mission number (`byte [gs+0]`) and elapsed
days (`gs+0x14FC >= 0x50`). Two of the six triggers are mission-number equalities whose immediates
differ per build:

| Site | Classic | Council Wars | Slot |
|---|---|---|---|
| `0x403FC7` | mission 5 | mission 14 | `gs+0x1510 = 4` |
| `0x403FE8` | mission 14 | mission 7 | `gs+0x1514 = 5` |
| `0x4040B3` | mission 4 | mission 14 | second copy of the same test (other branch) |
| `0x4040D4` | mission 14 | mission 7 | second copy |

A Classic campaign played in the expansion build would unlock artifacts on the expansion schedule.
Fix: make the four `cmp` sites read their immediate from a mode-dependent byte (e.g. `cmp al,[mode_tab+n]`
via a short detour to the zero tail; each site is a 3-byte `80 38 imm8` followed by a 2- or 6-byte
`jne`, so a `call stub` (5 bytes) fits when the `jne` is re-encoded in the stub), and let the mode
stubs fill `mode_tab`. Alternatively accept the discrepancy for a first test.

### 5.4 Data already in the Council Wars folder **(verified)**

| Content | Classic | Council Wars root | Status |
|---|---|---|---|
| `SCENARIO/HUMAN` (177 files) | | identical | except `human09.tro`: the CW copy (from the real install, `e476eb4`) contains the typo `(b(1,3)&&==0)` in trigger line 5; Classic's `(b(1,3)==0)` is correct - **take the Classic file** |
| `SCENARIO/ALIEN` (169), `SCENARIO/TEST` (174) | | identical | training uses `TEST` + `HTRAIN.BTS`, both present |
| `SCENARIO/MPLAYER` (454) | | identical | CW lacks only `pmap.exe`, `palette.*`, `primes.dat`, one `.med` - editor/tool files the game never opens |
| `GAMESTAT/*.TXT` | | identical | scene lists carry the same 1024x768 label shifts |
| `INTRFACE/` scripts, `.dat`, `BLEW.SPR`, `MFONTO5.SPR` | | identical | §3.3 |
| `AVI/` | 59 files | 63 files | 58 shared; CW adds `ascrubb`, `council1/2`, `electric` and its own `intro.avi`; **Classic `INTRO.AVI` (28 MB) is the only Classic video missing** |
| `SCENARIO/ALL.JUS`, `VENT.JUS`, `DESERT.SET`, `JUNGLE.SET` | present | absent (also absent from `exp/`) | never opened by the game (editor leftovers) |
| `scenario/multi-~1/` (jungle j2/j4/j6/j8 maps) | present | absent | duplicate of MPLAYER maps, the exe only opens `scenario/mplayer/%s` |

### 5.5 Data that has to be added **(verified absence)**

| Folder | Size | Needed by | Note |
|---|---|---|---|
| `MISSION/` (`h1..h15.wav`, `g1..g15.wav`, 30 files) | 3.1 MB | Classic campaign briefings | **Blocking**: the wave loader `0x452AB0` tries prefix, root, then the CD path and exits with "Please insert ... CD" when all fail (seen with the OZI pack on 10 Sep) |
| `ENCYCLO/` (`*.SPR/.TXT/.WAV` per unit, ~60 files) | 25 MB | ENCYCLOPEDIA button | The expansion never shipped an encyclopedia; the Classic one knows no expansion units |
| `AVI/INTRO.AVI` (Classic) | 28 MB | Classic intro | Name clash with the expansion intro (17 MB); see §6.3 |
| `WALLPAPR/*.bmp` (13 files) | 3.9 MB | nothing in game | cosmetic extra of the Classic install, optional |

Everything is committed as plain binaries; ~60 MB more in the Council Wars folder is acceptable
only if the Classic folder is eventually retired (that is the point of the merge). `sound/beat.wav`
and `intrface/multie~1.txt` are Classic leftovers with no reference in either exe.

---

## 6. Details and pitfalls

### 6.1 Menu layout

The single column of the current expansion menu holds five rows (541..645). Restoring MULTI PLAYER
WAR, TRAINING, ENCYCLOPEDIA and adding CLASSIC CAMPAIGN / CLASSIC LOAD means going back to two
columns as in Classic (x=332 and x=512 at 1024x768, rows 507..585) or a wider single column. The
label strip of `bintroe` is repainted by `paint_intro.py` and the rows are rewritten by
`build_ozi_overlay.py` (`MENU_ROWS`, `menu_script()`); both need updating, and
`build_ozi_overlay.py` must keep working idempotently after the change. A `textmsg` for the new
labels ("CLASSIC CAMPAIGN", "CLASSIC LOAD") has to be added; the ids 1..8 are used, the parser
accepts more.

### 6.2 Load buttons

Save games do not record their mode. The OZI work made the load buttons deterministic (OZI LOAD =
`ozisave`, LOAD GAME = `esave`); a Classic load button would add `save`. Three load buttons is
clumsy; an alternative is one LOAD GAME that lists all three folders, but that is a code change in
`0x403AA4`'s directory scan, not a slot write - out of scope for a first version.

### 6.3 Videos are not opened through the prefix helper **(verified)**

AVI paths are built from the CD-root buffer `0x4A46F2` (`.bss`, filled at start-up `0x405276..`
from the string at `0x482464`, i.e. the game directory) + `avi/` + name (`0x4050D9` in Classic for
the intro, `0x404964` for the `avhd1.avi`/`hvad1.avi` victory/defeat clips, mission briefings from
the scenario files). The prefix slots therefore do not redirect videos. The intro name is one
DGROUP string `intro.avi` at `0x4824A8` (file offset `0x7FEA8`): 9 chars + NUL + 2 padding bytes
before the next string at `0x4824B4`, so a name of up to 11 characters fits. It is used by the
start-up intro (`0x40538C`) and, in Classic, by PLAY INTRO (`0x4050FE`). Option: ship the Classic
video as `AVI/CINTRO.AVI` (10 chars) and let `stub_classic_set` write `cintro.avi` into the slot
while `stub_cw_set` / `stub_pack` restore `intro.avi` - a fifth slot in the mode mechanism. The
start-up intro is unaffected because start-up runs before any mode stub. Whether the Classic intro
(28 MB) is worth carrying at all is a maintainer decision; PLAY INTRO is currently OZI MISSIONS
anyway, so a new button would be needed to play it.

### 6.4 Start-up loads with the expansion prefix

`anim.dat`, all FIN/SPR banks and `sound2.dat` load once at start-up through `exp/` (the reason the
OZI units live in `exp/`). In Classic mode the game therefore still uses the expansion's sound table
(different gun sounds for some weapons) and the superset sprite banks. Cosmetic; the simulation is
unaffected.

### 6.5 Things that are already fine

- CD checks: both CD gates (`0x431F`, `0x507F`) and the second check (`0x781D9`) are patched in
  DCEXP16; `hbnfufl.a02` is present in the Council Wars folder.
- Resolution, cursor, pool, speed patches: applied to DCEXP16 already; the network screens are the
  1024x768 versions (§3.3).
- Relay server: no change needed for a first test; the client is protocol-identical (§2.2).
- The map editor is independent of both executables.

---

## 7. Test plan (nothing here has been run)

1. **Network smoke test**: un-comment `pushb 3` in a scratch copy of `exp/intrface/bintroe`, start
   DCEXP16, MULTI PLAYER WAR -> CONNECT TO SERVER -> `dark-colony-server.fly.dev` (or a local
   `PORT=8888 FAKE_PLAYERS=7 MIN_PLAYERS=1` server), READY, play a few minutes with `SYNC_CHECK=shadow
   RECORD_DIR=logs/replays`, then `node tools/replay.js` must report 0 mismatches. Then a mixed game:
   one `dc16.exe` client + one `DCEXP16.EXE` client.
2. **Classic campaign**: with `MISSION/` copied and a CLASSIC CAMPAIGN handler in place, play
   HUMAN01 to the first briefing and save/load once from `save/`. Check artifact unlocks against a
   Classic run if §5.3 is not yet patched.
3. **Training**: TRAINING from the expansion menu, first mission loads (`TEST` scenarios).
4. **Encyclopedia**: opens, browses a Human and a Gray unit, plays the unit WAV.
5. **Regression**: OZI MISSIONS, OZI LOAD, NEW CAMPAIGN (Council Wars), LOAD GAME still behave as
   documented in `DC16_DISPLAY_AND_RESOLUTION.md` §10.13, and `build_ozi_overlay.py` dry run reports
   no changes on the final data.

---

## 8. Effort estimate

| Step | Kind | Size |
|---|---|---|
| MULTI PLAYER WAR button back | data (1 row + layout) | hours, plus the test in 7.1 |
| `MISSION/`, `ENCYCLO/`, wallpapers, `human09.tro` | data copy | minutes; +32 MB in git |
| `stub_classic_set` + CLASSIC CAMPAIGN / TRAINING / CLASSIC LOAD handlers | exe patch (one stub, 2-3 trampolines, ~6 relocs), new `patch_classic_menu.py` in `tools/` | a day incl. mirroring into the docs |
| Artifact constants mode-aware | exe patch (4 sites -> stub) | half a day |
| Classic intro | decision + data | small once decided |
| Menu repaint (`paint_intro.py`, `build_ozi_overlay.py`) | tools | half a day |
| Plan/protocol doc corrections (F27, "not verified on ENGEXP16") | docs | after 7.1 |

After all of that the Classic folder would differ from the Council Wars folder only by its
executable and its menu, and could be retired from the repository (history keeps it).

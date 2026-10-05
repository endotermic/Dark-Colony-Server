# Dark Colony `dc16.exe` — Display Pipeline and the 1024×768 Upgrade

Reverse-engineering notes on how Classic `dc16.exe` (`DC - Classic/dc16.exe`, MD5
`aa0a646b1234d1d9815a2b7480fd080b`) puts pixels on the screen — DirectDraw setup, the internal
"screen" and graphics-context objects, the sprite blitters, the in-game HUD geometry, the mouse and
the movie player (§1–§7) — followed by the complete inventory of resolution-dependent code sites
(§8) and a staged plan to raise the game from 640×480 to 1024×768 (§9–§12).

All addresses are virtual addresses in Classic `dc16.exe` (`VA = file_offset + 0x400C00` for the
`AUTO` code section, `VA = file_offset + 0x402800` for `DGROUP`). Council Wars `ENGEXP16.EXE`
(MD5 `50419d438427d31341057e9723724f66`) is the same code base; §8 lists its file offsets too, all
byte-verified, and §10.10 has the three places where it is not a plain shift of Classic and how
the patch was carried over. Council Wars `dc16.exe` (MD5 `4180f6e9d01925b23eac0eb335e0b95e`) is a
different build and is *not* covered except for the two globals in §3.

**Names, 10 Sep 2026:** the expansion executable was renamed `ENGEXP16.EXE` → **`DCEXP16.EXE`**
(same bytes; the tools identify builds by MD5, not by name), the unrelated Council Wars `dc16.exe`
was removed from the repository, and so was the Ghidra project (`DecompiledWithGhidra/`, whose
databases were never tracked). The text below keeps the old names; both files are in the git
history before that commit.

Calling convention is Watcom register-based: the first four arguments in `eax, edx, ebx, ecx`.
Facts marked **(verified)** were read directly from the disassembly; facts marked *(inferred)* are
consistent with the code but were not traced to the end.

---

## 1. Overview **(verified)**

```
                       ┌─ 8-bit sprite cells (.SPR / .FIN, juicel.c)
                       │  8-bit background pages (.GIF, gifload.c, W*H bytes)
                       ▼
        shade LUT ──► software blitters ──► "screen" 16-bit framebuffer
     (0x48C188,            (juicel.c,           (DirectDraw offscreen
      level*512)            engmain.c,           SYSTEMMEMORY surface,
                            lighting.c,          640×480×16, 0x489720)
                            sprite.c)                   │
                                                        │ ddex4.c
                                                        ▼
                                        primary + 1 back buffer (flip chain)
                                        640×480×16 EXCLUSIVE FULLSCREEN
```

* The game is **16 bits per pixel**, RGB565 or RGB555 (whichever the card reports), *not* 8-bit
  paletted. All art on disk is 8-bit; it is expanded to 16-bit through a shading lookup table at
  draw time.
* All drawing is done by **CPU software blitters writing into one linear 16-bit buffer**. That
  buffer is a DirectDraw `DDSCAPS_OFFSCREENPLAIN | DDSCAPS_SYSTEMMEMORY` surface, locked once per
  frame; the code uses `lpSurface` only and **ignores `lPitch`**, assuming `pitch == width * 2`.
* DirectDraw is used only to get the buffer, to blit/flip it, to hold the mouse cursors, and to
  set the display mode. There is no hardware 2-D acceleration path and no scaling anywhere.
* **Every screen, the in-game HUD included, is a plain-text script** in `INTRFACE/` (§6). Only two
  things about the in-game screen are compiled in: where the map view is rendered and where the
  minimap is plotted (§5). Everything else — the panel frame art, all 163 HUD widgets and their
  coordinates — is data.

Modules involved (link order, boundaries from assert strings): `main.c`, `avi.c`, `proto.c`,
`widget.c`, `gadget.c`, `animate.c`, `button.c`, `driver.c`, `ddex4.c`, `interface.c`, `marker.c`,
`engmain.c`, `sprites.c`, `text.c`, `gifload.c`, `juicel.c`, `image.c`, `mouse.c`, `tile.c`,
`mapit.c`, `lighting.c`, `sprite.c`.

---

## 2. DirectDraw layer (`ddex4.c`) **(verified)**

Imports used: `DDRAW.dll!DirectDrawCreate` only (thunk `0x0047F11C`); everything else goes through
the COM vtables. `GDI32` `BitBlt`/`CreateCompatibleDC` and `USER32` `LoadImageA` are used for the
two loading-screen bitmaps and nothing else.

| Function | Address | What it does |
|---|---|---|
| `win_init` | `0x0042E7B4` | `GetVersionExA`; sets mouse to screen centre (320,240); `DirectDrawCreate`; `SetCooperativeLevel(hwnd, DDSCL_ALLOWMODEX \| DDSCL_EXCLUSIVE \| DDSCL_FULLSCREEN)`; `set_mode_16`; `create_surfaces`; sound init |
| `create_window` | `0x0042E688` | `LoadIconA`, `RegisterClassA`, `CreateWindowExA(0, cls, title, WS_POPUP, 0, 0, W, H, …)` — **reads the globals of §3**, so no patch needed |
| `set_mode_8` | `0x0042E890` | `IDirectDraw::SetDisplayMode(640, 480, 8)` — live: called by `avi_end` |
| `set_mode_16` | `0x0042E914` | `IDirectDraw::SetDisplayMode(640, 480, 16)`; on failure prints "Setting to 16 bit mode Failure" and aborts |
| `create_surfaces` | `0x0042E998` | primary + 1 back buffer (`COMPLEX\|FLIP\|PRIMARYSURFACE`, video memory, falling back to any memory, 10 retries 500 ms apart, then error exit); `GetAttachedSurface(BACKBUFFER)`; a **640×480 `OFFSCREENPLAIN\|SYSTEMMEMORY`** surface; `GetSurfaceDesc` to detect RGB565 / RGB555; `CreatePalette`; 32 mouse cursors from `cursor/cursor%d.bmp` |
| `lock_screen` | `0x0042F828` | `Lock(offscreen)` → `screen->pixels = desc.lpSurface`. `lPitch` is discarded |
| `unlock_screen` | `0x0042F8D8` | `Unlock`; `screen->pixels = NULL` |
| `clear_screen` | `0x0042F774` | zeroes `0x4B000` (= 640·480) 16-bit words linearly, then `Flip` |
| `make_colour` | `0x0042F2D0` | `(r,g,b) → 16-bit` via three interleaved LUTs at `0x004DEC90/92/94` |
| `set_palette` | `0x0042F320` | rebuilds those LUTs and the shade table from a 768-byte palette |
| loading screen | `0x0042EEF6`…`0x0042F031` | `LoadImageA(0, "intrface/load.bmp"/"load2.bmp", IMAGE_BITMAP, 640, 480, LR_LOADFROMFILE\|LR_CREATEDIBSECTION)` → `GetDC(backbuffer)`, `BitBlt(0,0,640,480,SRCCOPY)`, `Flip` (function start not determined) |

Surface handles: `0x00489714` `IDirectDraw`, `0x00489718` primary, `0x0048971C` back buffer,
`0x00489720` the 640×480×16 offscreen work surface, `0x00489724` palette, `0x00489730` `HWND`,
`0x004DFE90[32]` cursor surfaces, `0x00489734` "screen is locked" flag.

Pixel-format detection stores the masks at `0x004DFF18` (R), `0x004DFF28` (G), `0x004DFF24` (B),
the format id at `screen+0x14` (`0x235` = RGB565, `0x22B` = RGB555) and a 50 %-brightness mask at
`screen+0x10` (`0x7BEF` / `0x3DEF`).

---

## 3. The two master dimension globals **(verified)**

```
DGROUP  0x00488DB4  dword = 640     screen width
DGROUP  0x00488DB8  dword = 480     screen height
```

These are **initialised data, never written at runtime**, and each is read at exactly four places:

| Reader | Meaning |
|---|---|
| `0x0042E6F4` / `0x0042E6ED` | `CreateWindowExA` width / height |
| `0x0042E82E` / `0x0042E83A` | `screen->width = W; screen->height = H` in `win_init` |
| `0x0044FFC7` / `0x0044FFCC` | `image.c` full-screen page: `malloc(W * H)` (8-bit) |
| `0x0044FFE0` / `0x0044FFE7` | same function: `image->width = W; image->height = H` |

They are preceded in `DGROUP` by the dword pair `0x14F, 0x16E` (335, 366 — purpose not
identified), which makes the 12-byte sequence

```
4F 01 6E 01 80 02 00 00 E0 01 00 00
```

a **unique anchor in all three binaries** — the reliable way to find these globals in any build:

| Binary | W offset | H offset |
|---|---|---|
| Classic `dc16.exe` | `0x865B4` | `0x865B8` |
| `ENGEXP16.EXE` | `0x867DC` | `0x867E0` |
| Council Wars `dc16.exe` | `0x868E0` | `0x868E4` |

Because `screen->width` doubles as the framebuffer **stride** for the main sprite blitter (§4),
changing these two dwords alone already moves a large part of the renderer to a new resolution.
It is *not* sufficient — §8 lists the ~60 places that hardcode the numbers independently.

---

## 4. The `screen` and graphics-context objects **(verified)**

`driver.c` `0x0042C29C` builds both:

```c
ctx    = malloc(0xEC);              /* "Screen" */
screen = new_screen();              /* 0x1A4 bytes, driver.c 0x0042BD78 */
ctx[0x20] = screen;
ctx[0x24] = gfx_descriptor;
screen[0xFC] = 0; install_ddraw_driver(screen);   /* ddex4.c 0x0042FBF0 */
assert(screen[0xFC] != 0);
ctx->clip   = make_rect(0, 0, 640, 480);    /* 0x0042C347 / 0x0042C363 */
ctx->bounds = make_rect(0, 0, 640, 480);
```

**`screen`** (0x1A4 bytes) is an *image* with the graphics driver's method table appended:

| Offset | Field |
|---|---|
| `+0x00` | width **= stride in pixels** |
| `+0x04` | height |
| `+0x08` | pixel pointer (16-bit for the screen, 8-bit for `image.c` pages) |
| `+0x10` | u16 50 %-brightness mask |
| `+0x14` | pixel format id |
| `+0x18` | → shade / palette LUT block (`0x1802` bytes) |
| `+0xFC` | `win_init` |
| `+0x100`…`+0x1A0` | driver methods (graphics **and** sound — `driver.c` is the whole platform layer) |

**`ctx`** (0xEC bytes) is the drawing context every blitter receives in `eax`:

| Offset | Field |
|---|---|
| `+0x00`…`+0x0C` | clip rect `x0, y0, x1, y1` |
| `+0x10`…`+0x1C` | screen bounds rect |
| `+0x20` | → `screen` |
| `+0x24` | → gfx descriptor |
| `+0x29` | u8 "use shadow table" flag |
| `+0x30`…`+0x98` | method slots copied from `screen+0x100`… by `0x0042C405`ff; notably `+0x8C` `lock_screen`, `+0x90` `unlock_screen`, `+0x94` `set_origin` |

`make_rect(x, y, w, h)` is `0x004365BC` (`engmain.c`); it returns `{x, y, x+w, y+h}` by value
through `esi`. It is the single place rectangles are built, which makes it a useful breakpoint.

### The sprite-cell blitter `0x0044FAC0` (`juicel.c`) **(verified)**

The hot path, and the reason a resolution change is tractable at all:

```
screen = ctx[0x20]
stride = screen[0]                  <-- NOT a constant
base   = screen[8]
clip against ctx[0x00..0x0C]
dst    = base + (y*stride + x) * 2
src    = 8-bit cell data; each byte -> u16 through  [0x48C188] + level*512 + idx*2
```

It is fully resolution-independent. The same is true of the text (`text.c`), gadget and widget
drawing paths, which all go through it. There are exactly eleven `ctx+0x8C`/`ctx+0x90`
(`lock_screen`/`unlock_screen`) call sites, i.e. eleven **entry points** into framebuffer access —
everything else draws inside one of these brackets: `0x0042BCCE`, `0x0042BEFE`, `0x0042BFC1`,
`0x0042C146`, `0x0042C203` (`driver.c` generic rect/pixel ops), `0x00436399` (main map render, which
calls `draw_terrain` and `draw_objects` while holding the lock), `0x0043A095` and `0x0043A44F`
(minimap), `0x0044EA8C` (`gifload.c`), `0x0044FAED` and `0x0044FCCB` (`juicel.c`). That list is the
audit surface: any function that touches the framebuffer is reachable from one of them.

---

## 5. In-game screen layout **(verified)**

The whole HUD geometry is four numbers plus one byte offset:

```
screen                    640 x 480          (§3)
map viewport              512 x 448  at (4, 6)
   size   engmain.c  0x00435F46 (512) / 0x00435F61 (448)
   rect   proto.c    0x0041ED63 (x=4) / 0x0041ED6E (y=6) / 0x0041ED23 (512) / 0x0041ED1E (448)
   tiles  engmain.c  0x00435E4F (16 across) / 0x00435E47 (14 down)      <- 512/32, 448/32
minimap                    96 x  84  at (519, 6)
   size   engmain.c  0x0043A083 (96 cols) / 0x0043A07E (84 rows)
   steps  engmain.c  0x0043A463 (96<<8) / 0x0043A478 (84<<8)
   origin engmain.c  0x0043A0AF / 0x0043A484:  +0x220E bytes = (6*640 + 519) * 2
```

So the panel chrome is the right-hand strip `x = 516…639` (124 px) plus the bottom strip
`y = 454…479` (26 px). Tiles are **32×32 pixels**; world coordinates are `tile << 8` (8 fractional
bits), and the terrain renderer converts with `>> 5` (`0x004539B3`ff), i.e. the view origin
`0x005044AC`/`0x005044B0` is in **pixels**.

Every one of these numbers is independently confirmed by the HUD artwork and its layout script:
`INTRFACE.GIF` is opaque exactly on columns 0–3, rows 0–5 and columns 515–517, and all 75 panel
widgets in `MAINE` start at x ≥ 516 — see §6.1.

### The render view struct at `0x005044AC` **(verified)**

Set up by `engmain.c` `0x00435E7C`:

| Offset | Address | Value |
|---|---|---|
| `+0x00` | `0x005044AC` | i32 view x (pixels) |
| `+0x04` | `0x005044B0` | i32 view y (pixels) |
| `+0x08` | `0x005044B4` | destination pixel pointer |
| `+0x0C` | `0x005044B8` | u16 viewport width  = 512 |
| `+0x0E` | `0x005044BA` | u16 viewport height = 448 |
| `+0x10` | `0x005044BC` | u16 **destination stride in pixels = 640** (`0x00435F9F`) |
| `+0x14` | `0x005044C0` | → map (`0x9AA38` bytes, "kev: mapinfo") |
| `+0x18` | `0x005044C4` | → occlusion mask, `0x7000` bytes = 512·448/8 ("kev: maskbuffer", `0x00435F88`) |
| `+0x34` | `0x005044E0` | → tile-type table, `0x4260` bytes = 1416 × 12 ("kev: tilememory") — indexed by **tile type** (`0x00435FEF`), so resolution-independent |

Per-frame path (`engmain.c` `0x00436080` → `0x00435E7C`, and `0x00436380`ff):

```
lock_screen(ctx)
draw_ptr = screen[8] + (clip.y0 * 640 + clip.x0) * 2   <- 0x004363B6 (*5<<7), 0x004360B6 (*5<<8)
draw_terrain(view)          lighting.c 0x00453910
draw_objects(view)          sprite.c   0x004543FC
unlock_screen(ctx)
draw_minimap(ctx, ...)      engmain.c  0x0043A064 / 0x0043A43C
```

### Buffers that size themselves from the view struct **(verified)**

`lighting.c` `lighting_init` `0x00453770` is the model to imitate: it takes the view struct, reads
the viewport width and height back out of it (`[view+0x0A]>>16`, `[view+0x0C]>>16`), stores the
width in `0x0049931C` and the pixel count in `0x005360B0`, and allocates a 1-byte-per-pixel
"lightplane" of exactly that size into `view+0x1C`. `0x0049931C` is then used as the light-plane
row stride by the object renderer (`0x004542E8`, `0x00465051`, `0x00465401`, `0x004657B1`,
`0x00465B61`, `0x00465E6A`). **The allocation and the tile-to-tile addressing follow the viewport
size automatically** — changing the two immediates at `0x00435F46`/`0x00435F61` is enough for
them. Two things do not follow: the occlusion mask (`0x00435F88`) was written as a literal
`0x7000` and has to be patched by hand, and **`draw_terrain`'s fill of the lightplane advances
from one pixel row of a tile to the next by a literal `0x200` = 512 bytes**, 31 times
(`0x00453CCB…0x004542BC`), which is the stock viewport width in disguise — see §10.6.

### Buffers that are **not** resolution-dependent **(verified)**

Worth stating because it removes the hardest class of problem: **no static `.bss` buffer scales
with the screen size.** `0x00533C90` (`0x2420` bytes) is the fade/shading table read verbatim from
`FADE.DAT` (9248 bytes, `lighting.c` `0x004537AD`); the 289×32-byte loop at `0x00453970` only
rewrites its low 3 bits with the current light level. Everything that does scale — the map, the
flat maps, the occlusion mask, the `image.c` full-screen pages, the DirectDraw surfaces — is heap
or DirectDraw allocated. `.bss` runs `0x0049A000…0x00536E00` with `.reloc` immediately after at
`0x00537000`, so growing it in place would mean moving `.reloc` and `.rsrc`; that is not needed.

---

## 6. What is data, not code **(verified)**

**Every screen in the game — including the in-game HUD — is a plain-text script in `INTRFACE/`.**
There are **30** of them in Classic, parsed by `widget.c` `0x004232E2`ff and loaded by
`load_interface(app, name, flags)` `0x004231E8` (freed by `0x004231B0`):

| Script | `size` line | Script | `size` line |
|---|---|---|---|
| `MAINE` — **in-game HUD** | `size 640 480` | `LOGOE` | `size 640 480` |
| `NEWGAMEE` — main menu | `size 640 480` | `LOSTE` | `size 640 480` |
| `MULTIE` — lobby | `size 640 480` | `METAE` | `size 640 480` |
| `MULTIWNE` | `size 640 480` | `NETOPTE` | `size 640 480` |
| `GENERALE` | `size 640 480` | `STORYE` | `size 640 480` |
| `BUTTONSE` | `size 640 480` | `ENCYCLOE` | `size 640 480` |
| `DEMOWINE` | `size 640 480` | `WINE` | `size 640 480` |
| `DINTROE` | `size 640 480` | `WINGAMEE` | `size 640 480` |
| `DPBLANKE` | `size 640 480` | `WINUKE` | `size 640 480` |
| `DPLAYSE` | `size 640 480` | `bintroe` | `size 640 480` |
| `GETSVRE` | `size 640 480` | `introe` | `size 640 480` |
| `IPXNAMEE` | `size 640 480` | `shumane` | `size 640 480` |
| `LOADGE` | `size 640 480` | `LOBJE` | `size 112 96 304 272` |
| `LOPTE` | `size 112 128 308 240` | `LQCE` | `size 112 160 304 176` |
| `LSGE` | `size 112 48 304 386` | `MULTIE~1.TXT` (readable copy of a lobby script) | `size 640 480` |

Note the lowercase `bintroe`, `introe`, `shumane` — easy to miss on a case-insensitive listing.
Council Wars has the same set (uppercase `BINTROE`, `INTROE`, `SHUMANE`) plus three more under
`exp/intrface/`.

Keywords: `size`, `pictures`, `background`, `palette`, `text`, `font`, `font_offset`, `colour`,
`bright_pushed`, `bright_highlight`, `end`, plus per-object `pushb`, `checkb`, `in_text`,
`picture`, `list`, `scroll`, `group`, `textmsg`, each with explicit `x y w h`.

The `size` handler is `widget.c` `0x004223A8` and it accepts **two or four** numbers:

* `size W H` → rect `(0, 0, W, H)` — what the 26 full-screen scripts use
* `size X Y W H` → rect `(X, Y, W, H)` — used by the four sub-window scripts above

**Consequence: every screen can be repositioned or resized by editing one line of text, with no
binary patching at all.** ~~Centring an existing 640×480 screen inside 1024×768 is
`size 192 144 640 480`.~~ — **Retracted twice over**: the rect does not move the widgets (§10.1),
and it is the area `window_draw` erases when the screen opens, so a full-screen script must keep
`size 0 0 1024 768` (§10.15).

### 6.1 The in-game HUD is three data files **(verified)**

This was the one part of the display assumed to be compiled in. It is not. `proto.c` loads it in
the same function that sets the map view rect (§5): `mov edx, "intrface/main"` at `0x0041EB34`,
`call load_interface` at `0x0041EB63`, handle stored in `0x004AB1C4`. `MAINE`'s own header names
the other two files:

```
size 640 480
pictures   intrface/mainbut     -> INTRFACE/MAINBUT.SPR   242 231 B, the HUD widget cell bank
font 0     intrface/mfonto7
background intrface/intrface    -> INTRFACE/INTRFACE.GIF   640x480, the HUD frame / panel art
palette    palette
colour erase 0 0 0
bright_pushed 4
bright_highlight 4
```

`INTRFACE.GIF` is a **frame with a hole**, and the hole is exactly the map viewport derived from
the code in §5. Decoded, it is only **8.9 % opaque** (27 230 of 307 200 px); index 254 (RGB 0,0,0,
the `erase` colour) fills the rest. Over the rect `(4,6)-(515,453)` — the 512×448 viewport — it is
**99.8 % index 254** with 4 distinct colours, against 81 distinct colours outside it. The fully
opaque runs pin the frame down to the pixel:

| Opaque run | Meaning |
|---|---|
| columns `0…3` (100 %) | left border — map view starts at x = 4 |
| rows `0…5` (100 %) | top border — map view starts at y = 6 |
| columns `515…517` (100 %) | map view right edge (4 + 512 − 1 = 515) |
| columns `636…639` (100 %) | right screen border |
| rows `457…462`, `473…479` (100 %) | bottom bar detail |
| `x 516…639` overall | 24.6 % opaque — the right panel, rest filled by `MAINBUT.SPR` widgets and the engine-drawn minimap |
| `x 0…639, y 454…479` | 55.1 % opaque — the bottom bar |

`MAINE` places **163 positioned widgets** — 82 of the generic kinds (47 `pushb`, 13 `picture`,
13 `in_text`, 9 `checkb`) plus **80 `count`** (a button with a price counter: every building,
troop and upgrade in the build panel, at x = 518 / 577, y `112…358`) and **1 `scount`** (the money
counter, `#75` at (524,456)) — and 9 `group`s. The first version of this paragraph missed the two
`count` kinds, and so did `tools/hud_layout.py`; see §10.6 for what that looked like in the game.

* **156 widgets at x ≥ 516**, spanning x `516…638`, y `92…456` — the right panel. Tabs at y = 92,
  unit-order buttons from y = 153, Build button at (516, 422), money counter at (524, 456) on the
  panel's bottom corner.
* **4 widgets in the bottom bar**: `pushb #147` (4,460) 20×19, `pushb #149` (24,460) 20×19,
  `in_text #148` (50,462) 61×1, `in_text #200` (480,463) 3×1.
* **3 widgets over the map**: `picture #199` (200,160) 5×5, `in_text #203` (10,440) 72×1,
  `in_text #204` (10,425) 72×1 — the message lines.
* **y 6…90 at x 519…615 is deliberately left empty** — that is the minimap, painted by
  `engmain.c` `0x0043A064`/`0x0043A43C`, not by a widget.

So the division of labour is: the **code** fixes where the map view and minimap are rendered; the
**data** positions everything else around them.

### 6.2 The art

Fixed-size and never scaled by the engine: 19 × 640×480 `.GIF` backgrounds in `INTRFACE/`
(including the HUD frame above), the terrain `.GIF` palettes at the game root, `LOAD.BMP` and
`LOAD2.BMP` (640×480), 32 `CURSOR/cursor%d.bmp`, and the `.SPR` cell banks (§6.3).

### 6.3 The `.SPR` container format **(verified)**

Decoded from `juicel.c`. Entry points, all reached through the graphics context:

| Address | Role |
|---|---|
| `0x0042532C` | `load_sprite_bank(name, ctx)` in `animate.c` — builds `"sprites/%s"`, allocates a 20-byte bank record ("animation cell header") and calls the two below through `ctx+0x44` then `ctx+0x40` |
| `0x0044F790` | `ctx+0x44` — reads the header only, to compute the allocation size |
| `0x0044F818` | `ctx+0x40` — the parser |
| `0x0044FAC0` | raw-cell blitter |
| `0x0044FC44` | RLE-cell blitter (decode loop at `0x0044FD4F`) |
| `0x0044FE81`ff | per-cell draw dispatcher: applies the cell offsets, clips, then picks the blitter from `bank+0x0C` |

**File layout.** All integers little-endian — the loader uses plain `fread`
(`0x00406A44` = `fread(dst,2,1,f)`, `0x00406A5C` = `fread(dst,4,1,f)`), with no byte swapping.

| Offset | Size | Field |
|---|---|---|
| `0x000` | `u16` | `flags` — `flags & 0x180` means the cell bodies are RLE compressed. Only two values occur in the shipped data: `0x0001` (raw, 84 files) and `0x0081` (RLE, 468 files) |
| `0x002` | `u16` | `ncells` |
| `0x004` | `u32` | `datasize` — an allocation hint, **not** the body size (see below) |
| `0x008` | 768 | palette, 256 × (r,g,b), 6-bit VGA components (0…63) |
| `0x308` | `8·n` | directory, per cell: `u16 width`, `u16 height`, `u16 xoffset`, `u16 yoffset` |
| `0x308+8n` | — | cell bodies in order: raw = `width·height` bytes with no prefix; RLE = `u32 length` then that many bytes |

The directory therefore starts at **776**, not 779 — see the dead end recorded in §13.

**RLE scheme** (`0x0044FD4F`). A signed control byte, with the write cursor running in row-major
order and wrapping at `width`, so a transparent run may cross rows:

* `c >= 0` → the next `c + 1` bytes are literal palette indices (max run 128)
* `c < 0` → skip `-c` pixels, leaving them transparent (max skip 128)

**Palette index 0 is transparent** in both blitters (`0x0044FBEF`, `0x0044FDA0`) — including inside
a literal run, so a literal may legally carry zeros and they still read through.

**Cell `xoffset`/`yoffset`** are draw offsets added to the requested position by the dispatcher at
`0x0044FE9A` and `0x0044FEA2`. Every cell in the shipped data is tightly cropped (no empty
margins), so these are the trim offsets that put a cropped cell back where it belongs in its
logical frame. `SPRITES/ACAR.SPR` shows it plainly: constant height 227, widths 15/32/33/66/82…
with offsets 54/36/36/36/20… so that `width + xoffset` is constant within a facing group.

**The `datasize` quirk.** For RLE files it is the sum of the per-cell stream lengths. For raw files
the stored value counts an extra 12 bytes per cell — the 8-byte directory entry plus a notional
4-byte length prefix that raw files do not actually store — which is why the loader does
`datasize -= ncells*4` at `0x0044F88B` and then over-allocates by `ncells*24` (24 = the in-memory
cell header size, from `0x0044F7FF`). Nothing else depends on the value, so a writer can simply
recompute it.

**Validation.** All **552** unique `.SPR` files in `DC - Classic`, `DC - Council wars` (including
`exp/`) and the map editor parse byte-exact, with no trailing bytes: 17 958 cells, of which 52 are
`0x0` placeholders and 15 088 are non-empty compressed cells. Every one of those 15 088 decodes to
exactly `width·height` pixels while consuming exactly its declared stream length. Re-encoding and
decoding again reproduces the pixels for **15 088 / 15 088** cells; the re-encoded stream is
byte-identical to the original for 55.2 % of cells and never larger (0.4 % smaller in total, so the
original packer was marginally suboptimal). **337 of the 552 files (61.1 %) rebuild
byte-identically** from the parsed representation — `INTRFACE/MAINBUT.SPR` among them, same MD5.

**Tooling.** `tools/spr.py` implements the codec: `info`, `extract` (one PNG per cell plus a
`cells.json` carrying flags, palette and the per-cell offsets), `build` (rebuild from that
directory) and `check` (parse plus round-trip self-test). This is what unblocks re-importing traced
artwork in §10 stage 5.

**Cell size limits.** `engmain.c` asserts `sprite->xsize > 0 && xsize < 512 && ysize > 0 &&
ysize < 512` (string at `DGROUP 0x00486274`), but the shipped data contains **60 cells with a
dimension ≥ 512** (largest 640×257), so that path clearly is not reached for every cell. Treat
512 as the safe ceiling for anything drawn through `engmain.c`.

`ANIMATE/*.FIN` is a **different container** — loaded via `"animate/%s"` (`animate.c`
`0x00425613`), and its header does not fit the layout above (its `flags` word reads `0x001D`).
It is not covered here.

---

## 7. Mouse and movies **(verified)**

**Mouse** (`mouse.c`). Two paths, both clamping to the screen:

* `0x00450E20`ff (absolute): clamps to `[0, 639] × [0, 479]`, stores to `0x0048C164/68`.
* `0x00450E80` (DirectInput `GetDeviceState`, relative): accumulates into
  `0x005327C0`/`0x005327C4`, clamps to `[0, 639] × [0, 479]`, mirrors into `0x004DFF14` /
  `0x004DFF1C`, which is the position the cursor blit and every hit test use.
* `win_init` seeds `0x004DFF14/1C` with (320, 240) — the screen centre.
* The cursor sprite is blitted with `IDirectDrawSurface::BltFast` from one of the 32 cursor
  surfaces and clipped by hand against 640×480 at `0x0042E1D2`…`0x0042E219`.

**Movies** (`avi.c`, `AVIFIL32` + `MSVFW32`). Source frames are **320×240** (`0x0040722B`,
`0x004074B1`, `0x0040762B`, `0x00407E14`) written into their own DirectDraw surface
(`0x00488E00`) with a hardcoded **320-pixel** stride (`add edx, 0x280` = 640 bytes,
`0x004074C2`) and a 180-row clear (`0x004074C8`) — i.e. some clips are letterboxed 320×180.
`avi_begin` `0x00406FD0` releases the game surfaces; `avi_end` `0x00406FF0` does
`SetDisplayMode(640,480,8)` → `SetDisplayMode(640,480,16)` → `create_surfaces`, a deliberate mode
cycle, so **both** mode setters are live. The back buffer is cleared directly at `0x004073C4`
with a hardcoded 640×480 extent and a **1280-byte row pitch** (`add ecx, 0x500`, `0x00407405`).

---

## 8. Complete inventory of resolution-dependent sites

Verified by disassembly and byte-checked in the file. `ENGEXP16.EXE` offsets were located by
relocation-tolerant byte matching and, for the sites of these tables, resolve to a single shift:
**`ENGEXP16 = Classic + 0x60` in `AUTO` from `0x6000` onward, `+0` below it, `+0x228` in
`DGROUP` *file* offsets** (98.3 % of `AUTO 0x7000…0x50000` is byte-identical at that shift).
The rule is not exact everywhere: §10.10 has the complete delta map of the code section (a
`−0x20` stretch in `main.c` that holds two menu-furniture sites), the distinction between the
`+0x228` file shift and the `+0x28` *virtual-address* shift of the DGROUP data the code names, and
the one site whose stock value differs. `tools/patch_resolution.py` carries all of that in its
`BUILDS` table.

Council Wars `dc16.exe` is a different build; only the §3 anchor is given for it.

### 8.1 Plain immediates

| Site | Classic VA | Classic file | Bytes | ENGEXP16 file |
|---|---|---|---|---|
| W global (640) | `0x00488DB4` | `0x865B4` | `80 02 00 00` | `0x867DC` |
| H global (480) | `0x00488DB8` | `0x865B8` | `E0 01 00 00` | `0x867E0` |
| `main.c` full-screen rect 639 | `0x004010E5` | `0x4E5` | `BF 7F 02 00 00` | `0x4E5` |
| `main.c` full-screen rect 479 | `0x004010EA` | `0x4EA` | `B8 DF 01 00 00` | `0x4EA` |
| avi back-buffer clear width 640 | `0x004073F4` | `0x67F4` | `3D 80 02 00 00` | `0x6854` |
| avi back-buffer row pitch 1280 | `0x00407405` | `0x6805` | `81 C1 00 05 00 00` | `0x6865` |
| avi back-buffer clear height 480 | `0x0040740B` | `0x680B` | `81 FA E0 01 00 00` | `0x686B` |
| avi movie-surface width 320 | `0x004074B1` | `0x68B1` | `3D 40 01 00 00` | `0x6911` |
| avi movie-surface pitch 640 B | `0x004074C2` | `0x68C2` | `81 C2 80 02 00 00` | `0x6922` |
| avi movie-surface rows 180 | `0x004074C8` | `0x68C8` | `81 F9 B4 00 00 00` | `0x6928` |
| map view rect h 448 | `0x0041ED1E` | `0x1E11E` | `B9 C0 01 00 00` | `0x1E17E` |
| map view rect w 512 | `0x0041ED23` | `0x1E123` | `BB 00 02 00 00` | `0x1E183` |
| map view rect x 4 | `0x0041ED63` | `0x1E163` | `B8 04 00 00 00` | `0x1E1C3` |
| map view rect y 6 | `0x0041ED6E` | `0x1E16E` | `BA 06 00 00 00` | `0x1E1CE` |
| `driver.c` row advance 640 (a) | `0x0042C194` | `0x2B594` | `BB 80 02 00 00` | `0x2B5F4` |
| `driver.c` row advance 640 (b) | `0x0042C261` | `0x2B661` | `BA 80 02 00 00` | `0x2B6C1` |
| `driver.c` clip rect w 640 | `0x0042C347` | `0x2B747` | `BB 80 02 00 00` | `0x2B7A7` |
| `driver.c` clip rect h 480 | `0x0042C363` | `0x2B763` | `B9 E0 01 00 00` | `0x2B7C3` |
| cursor clip w 640 (a) | `0x0042E1D2` | `0x2D5D2` | `B8 80 02 00 00` | `0x2D632` |
| cursor clip w 640 (b) | `0x0042E1FF` | `0x2D5FF` | `B8 80 02 00 00` | `0x2D65F` |
| cursor clip h 480 (a) | `0x0042E20E` | `0x2D60E` | `B8 E0 01 00 00` | `0x2D66E` |
| cursor clip h 480 (b) | `0x0042E219` | `0x2D619` | `B8 E0 01 00 00` | `0x2D679` |
| mouse init X 320 | `0x0042E7C4` | `0x2DBC4` | `BA 40 01 00 00` | `0x2DC24` |
| mouse init Y 240 | `0x0042E7C9` | `0x2DBC9` | `B9 F0 00 00 00` | `0x2DC29` |
| `SetDisplayMode` 8-bit h 480 | `0x0042E898` | `0x2DC98` | `68 E0 01 00 00` | `0x2DCF8` |
| `SetDisplayMode` 8-bit w 640 | `0x0042E8A2` | `0x2DCA2` | `68 80 02 00 00` | `0x2DD02` |
| `SetDisplayMode` 16-bit h 480 | `0x0042E91C` | `0x2DD1C` | `68 E0 01 00 00` | `0x2DD7C` |
| `SetDisplayMode` 16-bit w 640 | `0x0042E926` | `0x2DD26` | `68 80 02 00 00` | `0x2DD86` |
| offscreen surface w 640 | `0x0042EA89` | `0x2DE89` | `B8 80 02 00 00` | `0x2DEE9` |
| offscreen surface h 480 | `0x0042EA8E` | `0x2DE8E` | `BA E0 01 00 00` | `0x2DEEE` |
| `load.bmp` `LoadImageA` h | `0x0042EF07` | `0x2E307` | `68 E0 01 00 00` | `0x2E367` |
| `load.bmp` `LoadImageA` w | `0x0042EF0C` | `0x2E30C` | `68 80 02 00 00` | `0x2E36C` |
| `load2.bmp` `LoadImageA` h | `0x0042EF38` | `0x2E338` | `68 E0 01 00 00` | `0x2E398` |
| `load2.bmp` `LoadImageA` w | `0x0042EF3D` | `0x2E33D` | `68 80 02 00 00` | `0x2E39D` |
| splash `BitBlt` h 480 | `0x0042EFED` | `0x2E3ED` | `68 E0 01 00 00` | `0x2E44D` |
| splash `BitBlt` w 640 | `0x0042EFF2` | `0x2E3F2` | `68 80 02 00 00` | `0x2E452` |
| clear loop `W*H` (a) | `0x0042F7AD` | `0x2EBAD` | `3D 00 B0 04 00` | `0x2EC0D` |
| clear loop `W*H` (b) | `0x0042F7E2` | `0x2EBE2` | `3D 00 B0 04 00` | `0x2EC42` |
| viewport tiles down 14 | `0x00435E47` | `0x35247` | `B9 0E 00 00 00` | `0x352A7` |
| viewport tiles across 16 | `0x00435E4F` | `0x3524F` | `BB 10 00 00 00` | `0x352AF` |
| viewport width 512 | `0x00435F46` | `0x35346` | `BA 00 02 00 00` | `0x353A6` |
| viewport height 448 | `0x00435F61` | `0x35361` | `BB C0 01 00 00` | `0x353C1` |
| occlusion mask size `0x7000` | `0x00435F88` | `0x35388` | `BA 00 70 00 00` | `0x353E8` |
| render dest stride 640 | `0x00435F9F` | `0x3539F` | `B8 80 02 00 00` | `0x353FF` |
| minimap rows 84 | `0x0043A07E` | `0x3947E` | `BB 54 00 00 00` | `0x394DE` |
| minimap cols 96 | `0x0043A083` | `0x39483` | `BF 60 00 00 00` | `0x394E3` |
| minimap stride 640 (a) | `0x0043A09B` | `0x3949B` | `B8 80 02 00 00` | `0x394FB` |
| minimap origin `0x220E` (a) | `0x0043A0AF` | `0x394AF` | `81 C2 0E 22 00 00` | `0x3950F` |
| minimap stride 640 (b) | `0x0043A45B` | `0x3985B` | `BA 80 02 00 00` | `0x398BB` |
| minimap x step `96<<8` | `0x0043A463` | `0x39863` | `B8 00 60 00 00` | `0x398C3` |
| minimap y step `84<<8` | `0x0043A478` | `0x39878` | `B8 00 54 00 00` | `0x398D8` |
| minimap origin `0x220E` (b) | `0x0043A484` | `0x39884` | `81 45 F0 0E 22 00 00` | `0x398E4` |
| mouse clamp `cmp 640` | `0x00450E47` | `0x50247` | `81 FE 80 02 00 00` | `0x502A7` |
| mouse clamp `set 639` | `0x00450E4F` | `0x5024F` | `BE 7F 02 00 00` | `0x502AF` |
| mouse clamp `cmp 480` | `0x00450E5C` | `0x5025C` | `81 FF E0 01 00 00` | `0x502BC` |
| mouse clamp `set 479` | `0x00450E64` | `0x50264` | `BF DF 01 00 00` | `0x502C4` |
| DI clamp `cmp 639` | `0x00450F46` | `0x50346` | `3D 7F 02 00 00` | `0x503A6` |
| DI clamp `set 639` | `0x00450F4D` | `0x5034D` | `C7 05 C0 27 53 00 7F 02 00 00` | `0x503AD` |
| DI clamp `cmp 479` | `0x00450F6B` | `0x5036B` | `81 FE DF 01 00 00` | `0x503CB` |
| DI clamp `set 479` | `0x00450F73` | `0x50373` | `C7 05 C4 27 53 00 DF 01 00 00` | `0x503D3` |
| minimap hit rect x 519 | `0x0041EE43` | `0x1E243` | `B8 07 02 00 00` | `0x1E2A3` |
| minimap click `x − 519` | `0x0040A084` | `0x9484` | `2D 07 02 00 00` | `0x94E4` |
| minimap plot clip rect x 519 | `0x0043A1E4` | `0x395E4` | `B8 07 02 00 00` | `0x39644` |
| minimap view-box x `+519` | `0x0043A27A` | `0x3967A` | `05 07 02 00 00` | `0x396DA` |
| lightplane row advance 512 (×30, `add eax`) | `0x00453CCB … 0x00454276` | `0x530CB … 0x53676` | `05 00 02 00 00` | `+0x60` each |
| lightplane row advance 512 (`lea edi`) | `0x004542BC` | `0x536BC` | `8D B8 00 02 00 00` | `0x5371C` |
| day/night clock hand anchor x 608 / y 450 (`clock.c`, §10.15) | `0x0043ACB5` / `0x0043ACA2` | `0x3A0B5` / `0x3A0A2` | `B8 60 02 00 00` / `BA C2 01 00 00` | `0x3A115` / `0x3A102` |

The last two rows are a *viewport* constant (512 = the stock map view width), not a screen one;
they were found by symptom in the first visual test (§10.6), not by the sweep. Two further
families are tabulated in their own sections: the 44 menu-furniture coordinates (`main.c`,
§10.7 — 640×480 *positions*, moved with the letterboxed scripts) and the movie path (`avi.c`,
§10.8 — ten sites, three of them code rewrites rather than constants).

### 8.2 Hidden multiplies — the trap

Watcom strength-reduced `× 640` and `× 1280` into `(y*4 + y) << n`, so **searching the
disassembly for `280h` finds only 17 sites and misses these three entirely**:

| Site | Classic VA | Classic file | Bytes | ENGEXP16 file | Effect |
|---|---|---|---|---|---|
| `driver.c` `y*640` | `0x0042C213` + `0x0042C218` | `0x2B613`, `0x2B618` | `01 CA` … `C1 E2 07` | `0x2B673`, `0x2B678` | `edx = (y*4 + y) << 7` |
| `engmain.c` `y*1280` (bytes) | `0x004360B9` + `0x004360BE` | `0x354B9`, `0x354BE` | `01 F8` … `C1 E0 08` | `0x35519`, `0x3551E` | `eax = (y*4 + y) << 8` |
| `engmain.c` `y*640` | `0x004363BD` + `0x004363C5` | `0x357BD`, `0x357C5` | `01 C8` … `C1 E0 07` | `0x3581D`, `0x35825` | `eax = (y*4 + y) << 7` |

A full sweep for `shl reg, 4…12` preceded by a `×3`/`×5` idiom found no other framebuffer strides;
the remaining hits are struct strides (`×96`, `×160`, `×192`, `×384`, `×1600`), the minimap grid
row stride (`×384` = 96 × 4), signed-divide idioms and percentage scaling.

### 8.3 Not verified / still open

* `0x0044EF15` (`gifload.c`): an 8-bit writer with a hardcoded **320**-pixel stride over a
  16-iteration loop, using the shade LUT at `0x0048C188`. Purpose not identified. Must be read
  before trusting a non-640 mode.
* `driver.c` `0x0042BCCE`, `0x0042BEFE`, `0x0042BFC1`, `0x0042C146` were not decoded individually;
  `0x0042BCEE` loads `0x4B000` (= 640·480) into `esi`, so at least one of them is a full-screen
  pass that needs the same treatment as `clear_screen`.
* The two dwords `0x14F`, `0x16E` at `DGROUP 0x00488DAC/B0` were not identified.
* Nothing here was re-verified in Council Wars `dc16.exe`.

---

## 9. Target geometry for 1024×768

**Why 1024 and not 800.** `1024 = 2^10`, so every `y * width` becomes a single shift. The three
hidden multiplies of §8.2 then need only a *length-preserving* two-instruction edit — neutralise
the `+ y` and bump the shift by one:

| Site | From | To | Result |
|---|---|---|---|
| `0x0042C213` / `0x0042C218` | `01 CA` / `C1 E2 07` | `89 D2` / `C1 E2 08` | `(y*4) << 8 = y*1024` |
| `0x004363BD` / `0x004363C5` | `01 C8` / `C1 E0 07` | `89 C0` / `C1 E0 08` | `(y*4) << 8 = y*1024` |
| `0x004360B9` / `0x004360BE` | `01 F8` / `C1 E0 08` | `89 C0` / `C1 E0 09` | `(y*4) << 9 = y*2048` B |

(`89 D2` = `mov edx,edx`, `89 C0` = `mov eax,eax` — two-byte no-ops, so no NOP sleds, no shifted
code, and no risk of landing inside a jump target.) With 800 pixels each of these would need a
real `imul` or a longer sequence and therefore code relocation. Recommend **1024×768** and treat
800×600 as out of scope.

**Layout.** Keep the HUD chrome at its native pixel size and spend all the new space on the map
view. Current chrome is 124 px on the right and 26 px at the bottom, with a 4/6 px inset:

| | 640×480 (now) | 1024×768 (target) |
|---|---|---|
| screen | 640 × 480 | 1024 × 768 |
| map viewport | 512 × 448 = 16 × 14 tiles | **896 × 736 = 28 × 23 tiles** |
| map view rect | (4, 6, 512, 448) | (4, 6, 896, 736) |
| occlusion mask | `0x7000` (512·448/8) | **`0x14200`** (896·736/8 = 82 432) |
| minimap | 96 × 84 at (519, 6) | 96 × 84 at (903, 6), origin byte `0x370E` |
| clear loop count | `0x4B000` | **`0xC0000`** (1024·768) |
| avi row pitch | `0x500` | **`0x800`** |
| mouse clamp | 639 / 479 | **1023 / 767** (`0x3FF` / `0x2FF`) |
| mouse centre | 320 / 240 | **512 / 384** (`0x200` / `0x180`) |

896 and 736 are both exact multiples of 32, so no partial tile row or column is introduced. The
minimap origin is `(6 * 1024 + 903) * 2 = 0x370E`.

**What this does *not* do.** Nothing is scaled. Sprites, fonts, buttons, the HUD and every menu
background stay at their 640×480 pixel size, so at 1024×768 they occupy proportionally less of the
screen and the panel art has 288 px of empty space below it. Fixing that is art work (§10 stage
5), not patching.

---

## 10. Staged plan

Each stage ends in a runnable binary, so a regression can be bisected to one stage. Every stage
is mirrored in `ENGEXP16.EXE` by `patch_resolution.py`'s `BUILDS` table (the `+0x60` rule of §8
plus the fixups of §10.10), and the expected-byte check re-verifies every site before writing.

### Stage 0 — reproducible patching (done)

The two CD patches so far were single-byte edits recorded only in the commit history. Sixty-odd
multi-byte edits across two binaries need a script, so `tools/patch_resolution.py` is that script
and **it, not §8, is now the authoritative copy of the site table**. It

* takes `--width`/`--height` and a `--stage` (1…4, cumulative, matching the stages below), plus
  `--viewport WxH` to force the map view smaller than the screen allows and `--exclude SUBSTR` to
  drop individual sites — the two flags that made the §10.3 bisection possible;
* locates the globals by the **4-byte** prefix `4F 01 6E 01` of the §3 anchor — deliberately not
  the full 12-byte sequence, because that contains the dimensions and so stops matching the moment
  the file is patched, which would break `verify`. The 4-byte prefix is unique in all three stock
  builds *and* in a patched one;
* identifies the build by MD5, and derives `ENGEXP16` offsets with the `+0x60` rule and the
  per-build fixups of §10.10 (`BUILDS`);
* applies each site as `(file_offset, expected_bytes, new_bytes)` and **refuses to write unless
  every selected site still holds its expected bytes** — that check is what makes relying on the
  shift rule safe rather than a guess, and it makes a double-apply or a wrong build fail loudly;
* keeps instruction lengths identical at every site, so no code moves and no jump target shifts;
* writes a `.bak` (and never overwrites an existing one, which is assumed to be pristine).

Three commands: `verify` reports a binary's state — for a patched file it reads the geometry back
out of the globals and tallies, per stage, how many sites are patched / stock / unrecognised, which
is what makes the staged bisection below usable. `plan` prints every edit and writes nothing.
`apply` patches, then prints the data and art work that is left.

Verified end to end: **66** edits on Classic and 66 on `ENGEXP16` (64 code sites plus the two
globals), cumulatively 20 / 42 / 63 / 66 at stages 1 / 2 / 3 / 4; `--stage 1` applies 20 and
`verify` then correctly reports stages 2–4 as untouched;
re-applying is refused; the `.bak` is byte-identical to the original; and the three rewritten
multiply sequences were re-disassembled out of the patched binary to confirm they read

```
0042C213: 89 D2   mov edx,edx        0042C218: C1 E2 08   shl edx,8     ; y*1024
004363BD: 89 C0   mov eax,eax        004363C5: C1 E0 08   shl eax,8     ; y*1024
004360B9: 89 C0   mov eax,eax        004360BE: C1 E0 09   shl eax,9     ; y*1024*2 bytes
```

with every following instruction still at its original address. The optional-header `CheckSum` of
these binaries is `0`, so there is nothing to recompute after patching.

### Stage 1 — prove the display mode (letterbox)

Patch only: the two globals (§3), both `SetDisplayMode` calls, the offscreen surface size, the
clear-loop count, the `driver.c` clip rect, and the three hidden multiplies of §9.

Leave the map viewport, the minimap, the HUD rect, the mouse clamps and all art alone. Expected
result: a 1024×768 framebuffer with the game drawn in its top-left 640×480 corner and the rest
black or garbage. What this proves: that DirectDraw gives us a 1024×768×16 exclusive-fullscreen
flip chain on the target machines, that `pitch == width * 2` still holds for the system-memory
offscreen surface, and that the blitters follow `screen->width` as advertised.

**Watch for:** neither `set_mode_16` nor `create_surfaces` has a fallback if 16-bit at the new size
is refused — they print an error and abort. If a machine refuses RGB565 at 1024×768 there is no
second chance in the code.

**Result, run 9 Sep 2026 on Windows 11 (1280×800 desktop).** Stage 1 applied to a copy of
Classic `dc16.exe` (20 edits), launched from `DC - Classic/`:

* the mode was granted — `PrimaryScreen.Bounds` read **1024×768** while the game ran, and the
  desktop was restored on exit;
* the process ran stably for the whole 20 s observation, no assert, `error.log` stayed empty;
* `pitch == width * 2` holds and the blitters do follow `screen->width`: the loading screen, the
  intro movie, the `DC` logo, the title, the credits and the whole button grid rendered sharp and
  at their correct coordinates in the top-left 640×480. A wrong stride would have sheared every
  one of them diagonally;
* the 640×480 loading screen and the letterboxed intro AVI sat in the top-left corner on black,
  exactly as intended for this stage.

One real defect surfaced, which is what the stage exists for — §10.1 below.

#### 10.1 Full-screen `.GIF` backgrounds skew, and centring is three edits **(verified)**

The main menu came up with its logo, title and buttons correct but its **background** wrecked: the
Mars limb gone, replaced by diagonal red streaks, and the starfield smeared across the full
1024 px width. Captured side by side with the stock binary at 640×480, which is clean, so this is
new and not the menu's own animated interference.

Cause, read out of `gifload.c`: the GIF LZW decoder `0x0044EB7C` writes **straight into the locked
16-bit framebuffer** (`esi = screen->pixels`, colours through `screen->LUT + 0x602`), and its
output loop has **no row-stride advance at all** — `add esi,2` per pixel at
`0x0044ECC5`/`0x0044ECCE`, and `add esi,length*2` for a multi-pixel LZW string at `0x0044ECF4`. It
streams the whole image as one linear run, which is correct only while
`image width == framebuffer stride`. Give a 640-wide GIF a 1024-stride framebuffer and every row
lands 384 px early, so the picture shears left and compresses vertically by 640/1024.

**There is no constant to patch here.** The stride is not wrong, it is absent. And the blit takes
no destination origin either — `esi = screen->pixels` at `0x0044EB89`, nothing added — so a
background always lands at framebuffer (0,0) whatever the script's `size` line says.

Those two facts together give one hard rule: **a full-screen background must be a file whose
dimensions are exactly the framebuffer size.** Nothing else can be made to work without changing
code.

This matters for every screen, because all **26** full-screen scripts have a `background` line —
checked, not sampled — drawing one of **19 distinct 640×480 `.GIF`s** (`choo`, `ency`, `gtsux`,
`intrface`, `intrg`, `intro`, `loader`, `lost`, `multiwin`, `name`, `net`, `server`, `shuman`,
`story`, `tcpwait`, `victorg`, `victory`, `wingame`, plus `general`, which has no `.GIF` and is
drawn from sprites). The only scripts *without* a background are the four sub-window dialogs
`LOBJE`, `LOPTE`, `LQCE`, `LSGE`, and those already use the `size X Y W H` form and are correctly
placed as they stand.

##### `size X Y W H` does not move the widgets **(verified)**

A second fact, learned the hard way by padding one GIF and launching: the four-argument `size`
form sets the window's **bounds rect only**. Widget `x`/`y` are **absolute screen coordinates** and
are not offset by it. `NEWGAMEE`'s `checkb 0` sits at (193,23) in the script and renders at
(193,23) whatever the `size` line says. The shipped four-argument scripts mislead on this point:
`LOPTE` declares `size 112 128 308 240` and its first `picture` is at (112,128), which looks like
rect-relative addressing but is simply a coordinate that already agrees with the rect.

So letterboxing a screen is **three coupled edits**, which is why they belong in one tool:

1. repack the background GIF onto a framebuffer-sized canvas with the original content centred;
2. set the script's `size` to `X Y w h` so the bounds rect covers where the content now is;
3. add the same `(X, Y)` to **every** positioned widget's `x` and `y`.

`tools/pad_background.py` does all three (`plan` / `apply` / `revert`, `.bak` per file, idempotent,
and it refuses to write a GIF whose byte layout would desynchronise the game's parser — no
extension blocks, GIF87a and the 256-entry global colour table preserved). It deliberately skips
`MAINE`: padding the HUD would keep the map viewport at 512×448, which is the opposite of the
point.

##### Two routes, neither needing a code patch

**Route A — pad.** Letterbox the 17 menu backgrounds that have a `.GIF`, and shift their widgets.
Pure data, no artwork, fully reversible. Gives a pixel-correct 640×480 UI inside a 1024×768
screen. **Tested 9 Sep 2026**: applied to all 24 menu scripts, then launched a stage-2 build. The
network-options screen (`NETOPTE`) rendered exactly as intended — background sharp, unskewed and
centred at (192,144), every widget aligned with the artwork, clean black border, cursor tracking.

**Route B — repaint.** Redraw the backgrounds at 1024×768 and set each script to `size 1024 768`
with its widgets repositioned for the larger canvas. More work, and it is **required regardless**
for `INTRFACE.GIF`, because only a genuinely redrawn HUD frame can carry the 896×736 hole that
buys the bigger battlefield (stage 5).

An earlier draft of this section proposed injecting a row stride and a destination origin into the
decoder via a code cave. **That is not needed.** Both routes above satisfy the hard rule by making
the file the right size, and the existing linear write is then correct as it stands.

##### Caveat: code-positioned elements do not move

Route A shifts widgets, but some screen furniture is placed by code and stays where it is. On the
network screen the spinning globe (`intrface/blew`; `NETOPTE` never mentions it — the
`globeg`/`globes` pair loaded at `0x0040323A`/`0x0040325D` is the *briefing* screen's globe)
keeps its hardcoded position, and once the buttons move +192 it overlaps them — its opaque
bounding box blanks the right half of the `TCP/IP` and `IPX NETWORK` labels. The intro AVI is the
same class of thing, still letterboxed at the top left until stage 4. **Enumerated and fixed in
§10.7 (44 immediates, two primitives) and §10.8 (movies).**

### Stage 2 — full-screen chrome, menus centred

* Mouse clamps and centre → 1023/767, 512/384.
* Cursor clip → 1024/768.
* `main.c` full-screen rect → 1023/767.
* Loading screens: the `LoadImageA` and `BitBlt` calls take the new numbers (stage 2 sites), and
  `INTRFACE/LOAD.BMP` / `LOAD2.BMP` are **padded** to 1024×768 with the picture centred
  (`pad_background.py apply` does it alongside the GIFs). Two things ruled out the alternatives,
  found when the tester reported the stretched result "must be the original image": `LoadImageA`
  with a non-zero `cxDesired`/`cyDesired` *scales* a 640×480 file to 1024×768, so leaving the art
  alone gives a blocky stretch; and the `BitBlt` destination at `0x0042EFF7`/`0x0042EFF9` is two
  `push 0` **imm8** bytes (`6A 00`), so it cannot be moved to (192,144) with a same-length edit —
  192 and 144 do not fit a signed byte, and neither do −192/−144 as a source offset.
* Menus: edit one line per `INTRFACE/*E` script (§6) from `size 640 480` to
  `size 192 144 640 480`. This centres every menu, lobby and dialog with **no patching**. The
  in-game map view is positioned separately (`proto.c`, stage 3) and is unaffected.
  **Not a one-line edit** — the rect does not move the widgets and the background ignores it
  entirely (§10.1). Use `tools/pad_background.py`, which pads the GIF, sets the rect and shifts
  every widget together; or repaint at 1024×768 and use `size 1024 768` with repositioned
  widgets.

At the end of stage 2 the game is a genuine 1024×768 application: under route A a pixel-correct
640×480 UI boxed in the middle, under route B a native 1024×768 one. Either is a usable state and
a sensible place to stop if the rest stalls, bar the code-positioned elements noted in §10.1.

#### 10.2 A tile count encoded twice, the second time as an `lea` displacement **(verified)**

Stage 3 as first shipped crashed the moment a battle started: access violation, `0xC0000005`,
fault RVA `0x39D5E` = VA **`0x00439D5E`**, in the per-tile vision scan in `engmain.c`. That
instruction is `mov edx,[eax]`, dereferencing a ground-layer row pointer taken from
`[map+0x804 + i*4]`.

`clip_view_to_map` `0x00435E24` builds the scanned rect from the map's **bottom** edge:

```
00435E3F  mov  eax,[map+0x9A4B4]   ; map height in tiles
00435E45  sub  eax,edx             ;   - view_tile_y
00435E47  mov  ecx,0Eh             ; tiles_down = 14      <- patched to 23
00435E4C  lea  edx,[eax-0Eh]       ; y origin, 14 AGAIN   <- MISSED
00435E4F  mov  ebx,10h             ; tiles_across = 16    <- patched to 28
00435E62  call make_rect(x, y, w, h)
```

The count appears **twice**: once as `mov r32, imm32` and once as a **signed 8-bit `lea`
displacement**, `8D 50 F2`. Patching only the first left the rect 9 tiles too tall, so the scan
walked past the 256-entry row-pointer table, picked up a word of path-grid data as a pointer, and
faulted. Fix: `8D 50 F2` -> `8D 50 E9`, one byte, now site `0x3524C` in `patch_resolution.py`,
the first of nine sites the crash hunt added (57 → 66).

**The lesson generalises.** §8.2 records that Watcom hides `*640` in strength-reduced form; this is
a second hiding place — *small* constants encoded as `imm8` or `disp8`, which no search for the
`imm32` form can find. A targeted sweep of the render modules for 14 as an `imm8`/`disp8` afterwards
turned up no other instance (the other `[ebp-0Eh]` hits are stack locals), and the width has no
such partner because the x origin is not measured from the far edge. But the same check is owed to
any future constant: **look for the small encodings too, and prefer a diagnostic over a sweep** —
the Windows Error Reporting fault offset named the faulting instruction exactly and cost one
lookup, where auditing the 80-odd map-dimension references would have cost an afternoon.

`Geometry` now refuses a viewport taller than 127 tiles, since that displacement is a signed byte.

### Stage 3 — enlarge the map viewport **(done — 28×23 tiles run; §10.5 grows the lightmap frame)**

* `engmain.c` viewport 512 → 896, 448 → 736; tiles 16 → 28, 14 → 23.
* Occlusion mask `0x7000` → `0x14200`.
* Render dest stride 640 → 1024.
* `proto.c` map view rect 512 → 896, 448 → 736 (keep x=4, y=6).
* Minimap origin `0x220E` → `0x370E`, both strides 640 → 1024.
* `lighting.c` lightplane fill: the 31 per-row advances `0x200` → 896 (§10.6).

The lightplane *allocation* and its stride variable follow automatically (§5); the fill loop's
row advance does not, and was the "repetitive black lines" of the first visual test (§10.6).

Then re-derive, from the disassembly, everything the terrain and object renderers clamp against.
`0x00435E24` (clip view to map) builds `make_rect(view_x>>5, map_h - (view_y>>5) - 14, 16, 14)`
from the same two tile counts, so those two immediates look like they cover the scroll clamp as
well — but that must be confirmed, together with `lighting.c` `0x00453910` and `sprite.c`
`0x004543FC`, before declaring the stage done.

**Consequence to accept:** a 28×23-tile view on a 256×256-tile map shows 2.9× the area. This is
render-side only — the lockstep checksum (`sync.c`, `DC16_BATTLE_ENGINE.md` §16) covers simulation
state and not the camera, and the relay server (`Dark-Colony-Server`) never sees viewport data — so
it does not desync. It *is* a competitive change if a patched and an unpatched client meet in the
same game.

#### 10.3 Stage 3 does **not** work above ~20×16 tiles — how the cause was narrowed **(verified)**

> **Resolved in §10.4.** The buffer is `draw_terrain`'s own stack lightmap, and there are two caps,
> not one. Read this section for the method; §10.4 has the answer and corrects the 640×512
> recommendation made below.

With the §10.2 `lea` fix in place, a full-size 896×736 viewport (28×23 tiles) still dies, now in a
different place: access violation reading address 0, faulting instruction

```
00453ad4  8b36    mov esi,dword ptr [esi]
```

inside `draw_terrain` `0x00453910` (`lighting.c`). It reproduces **without entering a battle** —
the attract-mode demo starts itself after ~20 s at the main menu and renders terrain — which is
what made it cheap to bisect.

##### It is a capacity threshold, not arithmetic

`patch_resolution.py` grew a `--viewport WxH` flag for this: it forces the map view smaller than
the screen allows, so the viewport can be swept independently of the 1024×768 framebuffer. (The
HUD frame then does not match the hole, which is irrelevant to a crash test.) Sweeping it:

| `--viewport` | tiles | occlusion mask | result |
|---|---|---|---|
| 544×480 | 17×15 | `0x7F80` | runs |
| 640×512 | 20×16 | `0xA000` | runs |
| 768×608 | 24×19 | `0xE400` | **crashes** |
| 896×736 | 28×23 | `0x14200` | **crashes** |

Every one of those builds computes its constants through the same `Geometry` code, so a wrong
formula would fail at 17×15 too. Controls: stage 2 alone ran 45 s clean; stage 3 with the
enlargement sites excluded ran 50 s clean. So the arithmetic is right and something has a fixed
capacity that 20×16 fits and 24×19 does not.

##### The register capture names a single corrupted local

Three runs under `cdb` (`x86\cdb.exe` out of the WinDbg store package) at 896×736 gave consistent
registers at the fault:

* run 1: `eax = 0x11` (17), `esi = 0x44`
* run 2: `eax = 0x49` (73), `esi = 0x124`

The address being dereferenced is built as `esi = (index << 2) + [ebp+0x56]`, and `17 << 2 = 0x44`,
`73 << 2 = 0x124`. **`[ebp+0x56]` reads 0 in both runs.** It should hold the ground-layer
row-pointer table base, `[view+0x14] + 0x804` — the same table §10.2 was walking off the end of.

Everything around it is intact, checked in the same runs:

| Location | Value | Meaning |
|---|---|---|
| `[ebp+0x56]` | **`0`** | row-pointer table base — should be `0x0356CC12` |
| `[ebp+0x5A]` | `0x10` | light level |
| `[ebp+0x5E]` | `0` | view tile y |
| `[ebp+0x62]` | `97` | view tile x |
| `[ebp+0x6A]` | `23` | tiles down |
| `[ebp+0x6E]` | `28` | tiles across |
| `[ebp+0x72]` | `13` | — |
| `0x005044B8` | `0x02E00380` | viewport `896×736` |
| `0x005044BC` | `0x400` | dest stride 1024 |
| `0x005044C0` | `0x0356C40E` | map pointer, valid; map is **128×112** tiles |

So the tile rect is legal on this map — `97 + 28 = 125 ≤ 128`, `0 + 23 = 23 ≤ 112` — and the view
struct §5 describes is correct in every field. `ebp = 0x000EF72E`, `esp = 0x000EE2DC`,
`ebp - esp = 0x1452`, exactly the frame size the prologue reserves, so the stack pointer is not
blown either. **Exactly one local is zeroed while its immediate neighbours survive**, which is the
signature of a bounded write into a fixed-size buffer that happens to end at `ebp+0x56`.

##### Buffers ruled out as the overflowing one

Each of these was read out of the disassembly and either scales with the viewport or is indexed by
something unrelated to it:

* `draw_objects` `0x004543FC` sort array at `ebp-0xCB4` — 804 dwords, sized for the 800-object
  pool, not for screen area;
* the static render-item list at `0x004F6F2C` — capacity 800, and it is *guarded*:
  `cmp dword ptr [0x005044E4],320h` at `0x0043613E` refuses to add beyond 800;
* `tilememory` at `0x4260` — 1416 × 12 bytes, indexed by tile *type*;
* the occlusion mask and the lightplane — both allocated from the viewport size (§5), and neither
  has a hardcoded row stride to miss;
* `draw_terrain`'s own frame — no viewport-indexed array found in it.

The camera path was also verified end to end and is **not** the cause: bounds are written at
`0x0041EE66` / `0x0041EE6F` into `ui+0x114…0x120` (`ui` base `0x004AA9D0`), enforced by the
generic `clamp2d` `0x00436668(min_x, min_y, max_x, max_y, &x, &y)` called from `0x0040AF16`, and
the view origin is derived with the same half-viewport constants the patcher already rewrites at
`0x0040AB1C` / `0x0040AB2B` and `0x0040B0BC` / `0x0040B0E0`. An earlier draft called those camera
globals dead stores; that was a bad grep (the pattern needed the trailing `h` of `[004AAAE4h]`) and
is retracted — they are read.

##### What was left — and how it was closed

One question remained: **what writes 0 to `[ebp+0x56]`, and why only above 20×16 tiles.** §10.4
answers it. The step that got there was a hardware watchpoint on that dword — under `cdb`, armed *from the breakpoint's own
command list* so that it is set with the frame established:

```
bp dc16+0x5393d "r ebp;dd ebp+0x56 L1;ba w2 ebp+0x56;ba w2 ebp+0x58;bc 0;g"
g
```

Two invocation details cost a run each and are worth recording: queued `-c` commands are mangled
by shell quoting, so use `-cf <file>`; and `cdb` needs the full path to the target plus
`-WorkingDirectory`, or it cannot find it. A third: `ba w4` is rejected with *"Data breakpoint
must be aligned"* whenever the computed address is not 4-byte aligned, which `ebp+0x56` is not for
every `ebp` — two `ba w2` breakpoints covering the dword avoid the problem entirely.

##### ~~Meanwhile: 640×512 is a shippable middle~~ — **retracted, see §10.4**

This section recommended `--stage 3 --viewport 640x512` (20×16 tiles, 1.46× the stock area) as a
releasable state. **It is not one.** §10.4 shows a second, *silent* cap at 17 tiles across: 20×16
does not crash, but it lights part of the view from the wrong neighbours. The largest viewport
correct on both caps is **17×16 = 544×512**, 1.24× stock. The map-dependence caveat still stands —
nothing here has been exercised outside the attract-mode demo map (128×112 tiles).

#### 10.4 The buffer, found: `draw_terrain`'s stack lightmap **(verified, watchpoint)**

A hardware watchpoint on `[ebp+0x56]` named the writer in one run. It is **`draw_terrain` itself**,
at the back-edge of its own first loop:

```
00453B01  lea eax,[ecx+ecx]              ; 2*ecx
00453B04  lea esi,[eax+2]                ; 2*ecx + 2
00453B07  lea eax,[esi*8]                ;  \
00453B0E  add eax,esi                    ;   >  eax = 144 * (2*ecx + 2)
00453B10  shl eax,4                      ;  /
00453B13  lea esi,[edx*8]
00453B1A  add eax,esi                    ; + 8*edx
00453B1C  mov esi,[ebp+5Ah]              ; the light value
00453B20  mov [eax+ebp-144Ah],esi        ; <-- the overflowing store
00453B27  jmp 00453A5C
```

The capture: `ecx = 0x11` (17), `edx = 0x0D` after its `inc`, so `eax = 144*36 + 8*12 = 0x14A0`,
and `ebp - 0x144A + 0x14A0` = **`ebp + 0x56`** — the corrupted local, to the byte. `[ebp+0x56]` read
`0x0352CC12` at the top of the function and `0` at the fault, with the store at `0x00453B20` the
only write in between.

##### What the array is

A **half-tile-resolution lightmap on the stack**: dwords, **4 bytes per half-tile column, 144 bytes
per half-tile row**, based at `ebp-0x1456`. Loop 1 (`0x00453A40`) fills the even half-rows from the
per-tile light values; loop 2 (`0x00453B32`) interpolates the odd half-rows from its two neighbours
(`0x00453B7A`…`0x00453B91` sum four corners, `sar edi,2` averages, `0x00453BB3` stores). Later
passes read it through the same four bases. Ten sites in all, at four displacements — `-0x1456`,
`-0x1452`, `-0x144E`, `-0x144A`, which are columns −1, 0, +1, +2 from the base `[ebp-0x1452]`
(= `esp` after the prologue) — and the ×144 row-stride idiom (`lea r,[s*8]; add r,s; shl r,4`)
appears at **6** sites, all between `0x00453B07` and `0x00453C25`. (~~42~~ — that earlier count
took every `shl reg,4` in the function; the other 34 are the `shl 4; add; shl 5` = ×544
lighting-palette index and never touch the array. Corrected in §10.5.)

The frame is `sub esp,0x14CC` with `sub ebp,7Ah`, so `esp = ebp-0x1452` and the array runs
**upward** from just above `esp` toward the locals, the lowest of which is `[ebp+0x2E]`. That gives
`0x144A + 0x2E = 5240` bytes of room. Hence two independent caps:

| Cap | Comes from | Limit | Symptom when exceeded |
|---|---|---|---|
| ~~**Width**~~ | ~~the 144-byte row stride: `4*(2*ta+2) <= 144`~~ | ~~**`tiles_across <= 17`**~~ | ~~rows bleed into each other — wrong lighting, silently~~ **Retracted in §10.5: the overflow lands only on cells of the opposite parity, which nothing uses. The stride is correct up to 34 across.** |
| **Height** | total room: `144*(2*td+2) + 8*ta <= 5240` | **`tiles_down <= 16`** | the store walks past the array into the locals — **crash** |

Stock 16×14 fits with almost nothing to spare (`34 <= 36` half-columns), which is the signature
of an array sized for exactly one screen size. The "height" cap is really a *room* cap — it is
the total footprint, `144*(2*td+2) + 4*(2*ta+2) + 4` bytes, that must stay below the locals.

The height cap reproduces the §10.3 bisection exactly, and is the whole explanation of it:

| viewport | tiles | `144*(2*td+2) + 8*ta` | vs 5240 | observed |
|---|---|---|---|---|
| 544×480 | 17×15 | 4744 | fits | runs |
| 640×512 | 20×16 | 5056 | fits | runs |
| 768×608 | 24×19 | 5952 | **over** | crashes |
| 896×736 | 28×23 | 7136 | **over** | crashes |

##### ~~Correction to §10.3 and to the README~~ — itself retracted, see §10.5

§10.3 offered 640×512 (20×16) as a clean "shippable middle". This section then called that wrong
because 20 across exceeds the supposed *width* cap of 17 and would light part of the view from the
wrong neighbours. **§10.5 shows the width cap does not exist**: the overflowing columns alias cells
that the algorithm never uses. 20×16 was fine after all, and so is anything up to 34 across whose
total footprint fits. The 17×16 figure below is superseded.

##### The fix, and its real cost — **as first estimated; §10.5 has what was actually needed**

Two independent edits, both mechanical, both length-preserving:

1. **Height** — move the array base down and grow the frame: `sub esp,14CCh` → a larger `imm32`
   (still 6 bytes), and subtract the same delta from all **10** disp32 array bases (each already a
   7-byte `mov [reg+ebp+disp32]`). For 28×23 the store needs `144*48 + 224 = 7136` bytes, so the
   delta is `0x780` and the frame becomes `0x1C4C` — about 7 KB on a 1 MB stack.
2. **Width** — raise the row stride from 144 to 256 bytes, which turns the ×144 idiom into a single
   `shl reg,8`. The three-instruction sequence is 12 bytes and `shl reg,8` is 3, so it fits with
   `nop` padding and nothing moves. 256 B/row = 64 half-columns = **30 tiles across**. This is the
   expensive half: **42 sites**, and each must be confirmed to be the row stride and not some other
   ×16.

With both applied the array is `144→256` × `(2*td+3)` rows; for 28×23 that is 12 544 bytes, so the
frame goes to roughly `0x3200`. Worth doing as its own stage with its own bisection, since 42 hand-
checked sites is exactly the kind of sweep §10.2 warns about.

##### Method note

The three earlier "prologue breakpoint" runs did nothing: **`bp dc16+0x5393d` makes cdb evaluate
`dc16` as the hex number `0xDC16`**, so the breakpoint went to `0xDC16 + 0x5393D = 0x61553` and
failed to insert with `Win32 error 0n998`, while `g` returned immediately. Their fault registers
were still valid — the process simply ran unbroken to the crash. Use the absolute VA (`bp 0045393d`;
the image loads at its preferred `0x00400000`, confirmed with `lm m dc16`). To keep the log readable,
filter the legitimate writer inside the watchpoint command, remembering that a data breakpoint
reports the instruction **after** the write:

```
bp 0045393d
g
bc 0
ba w2 ebp+0x56 ".if (@eip != 0x0045393d) { .echo CULPRIT; r; kb }; gc"
g
```

#### 10.5 The lightmap frame grown: 28×23 tiles run **(verified, breakpoint)**

Applied and launched 9 Sep 2026. The full 896×736 viewport (28×23 tiles) renders terrain and the
game runs until killed; the crash of §10.3 is gone. Two runs, 60 s and 77 s, on the attract-mode
demo map, both clean. The second run carried a one-shot breakpoint at `0x004539DD` (just after
the tile counts are stored) which logged:

```
esp=001adb6c ebp=001af72e        ebp-esp = 0x1BC2  (= 0x1C3C - 0x7A: the grown frame)
[ebp+0x6A] = 0x17 (23 down)   [ebp+0x6E] = 0x1C (28 across)
[ebp+0x56] = 03afcc12          (row-pointer table base — the local that used to read 0)
```

##### What the array actually is — and why there is no width cap

Reading the three loops as index sets, rather than as byte ranges, changes the picture:

| pass | writes | reads |
|---|---|---|
| loop 1 `0x00453A40` | (even row, even col): rows `0..2*td+2`, cols `0..2*ta+2` | the map |
| loop 2 `0x00453B32` | (odd row, odd col): `(r+1, c+1)` for even `r, c` | the four loop-1 corners `(r|r+2, c|c+2)` |
| loop 3 `0x00453BC4` | — | (odd row, odd col): `(2i+1|2i+3, 2j+1|2j+3)` — note **`2j+3`**, since `-0x144E` is column +1 |

Half the cells — (even, odd) and (odd, even) — are never written or read. Now let a row spill past
byte 144: cell `(r, c)` with `c >= 36` lands on `(r+1, c-36)`, the same byte with the **opposite row
parity and the same column parity**, i.e. exactly one of the unused cells. A second wrap (`c >= 72`)
would land on `(r+2, c-72)` with matching parity and collide. So the 144-byte stride is correct for
`2*ta+2 < 72`, **34 tiles across**, and the only real limit is the room below the locals: the last
write is `(2*td+2, 2*ta+2)`, so the array needs

```
144*(2*td+2) + 4*(2*ta+2) + 4  bytes   <=   0x1452 + 0x2E = 5248
```

which is `5044` for 17×16 (fits), `5068` for 20×16 (fits — the §10.4 retraction of 640×512 was
unfounded), `5964` for 24×19 and `7148` for 28×23 (both over: the crashes). The same table as
§10.4's, one column further right.

##### What was patched — 13 sites, no stride change

The ×144 idiom is left alone. `patch_resolution.py` grows the frame whenever the footprint above
exceeds the stock room, by `delta = roundup16(footprint - 5248)`:

| site | stock | 28×23 (`delta = 0x770`) |
|---|---|---|
| `0x00453915` `sub esp,imm32` | `0x14CC` | `0x1C3C` |
| 10 × `[reg+ebp+disp32]` at `0x00453B20 B7A B81 B88 B91 BB3 C05 C2D C3C C5D` | `-0x1456/-0x1452/-0x144E/-0x144A` | each `- delta` |
| PE optional header `+0x48` SizeOfStackReserve | `0x13880` (80 000) | `0x100000` |
| PE optional header `+0x4C` SizeOfStackCommit | `0x10000` (64 KB) | `0x40000` |

Nothing else moves: `ebp` is set *before* the `sub esp` (`mov ebp,esp; sub esp,14CCh; sub ebp,7Ah`),
so every local `[ebp+0x2E…0x8E]` and the arguments stay put; the epilogue is `lea esp,[ebp+7Ah]`
at `0x00454303`, independent of the frame size; and no pointer to the array ever leaves the frame
(there is no `lea reg,[ebp-14xx]` in the function). The ten `disp32` fields are already 7-byte
instructions, so the edit is a value change only.

The header rows are there because **Watcom emits no stack probe**. A frame that grows by two pages
in one `sub esp` is only safe if that stack is already committed; the stock header commits 64 KB,
and touching past the guard page without touching the guard page is an access violation, not a
stack growth. Raising the commit (and the 80 000-byte reserve, which was tight for a 1 MB-era
default) costs nothing and removes the question. The checksum field is 0 in both builds, so the
header edit needs no fix-up.

Both builds take the patch at the same sites: ENGEXP16 is `+0x60` for all 11 code sites and the
header offsets are identical (`0xE0`/`0xE4`).

##### What this run does not show

Correct *lighting* at 28 across is argued from the index sets above, not observed — the run was a
crash test under `cdb`, and exclusive-mode DirectDraw gives no screenshot. The parity argument is
tight, but a look at the right-hand third of the view during a night phase would close it. Also
still only exercised on the attract-mode demo map (128×112 tiles).

#### 10.6 First look at the picture: the lightplane's hidden 512, and the `count` widgets **(verified)**

The first time a person looked at the 28×23-tile battlefield (9 Sep 2026, evening; stage 3 exe,
`hud_layout.py build` frame, `hud_layout.py maine apply` script) the report was: the whole screen
is used, nothing hangs, the unit-order icons are where they should be — but **the build buttons
are not visible at all**, and **the screen is full of repetitive black lines**. Both had one-line
causes, and both were found in the disassembly without another launch.

##### The black lines: `draw_terrain` fills the lightplane with a hardcoded 512-byte row stride

`draw_terrain` `0x00453910` does not draw terrain pixels at all. Its first half is the stack
lightmap of §10.4/10.5; its second half (`0x00453BC4…0x004542E0`, the per-tile loop) **fills the
lightplane** — the 1-byte-per-pixel buffer at `view+0x1C` that `lighting_init` allocates at the
viewport size — one 32×32 tile at a time. Per tile it copies 32 rows of 8 dwords from the fade
table `0x00533C90` (`rep movsd`, `ecx = 8`), and between one row and the next it does

```
00453CCB  05 00 02 00 00        add  eax,200h        ; next lightplane row = +512 bytes
...                                                  ; 30 of these
004542BC  8D B8 00 02 00 00     lea  edi,[eax+200h]  ; and the 32nd row
```

The advance from one **tile** to the next (`[ebp+0x52] = 0x20`, `0x004542D9`) and from one
**tile row** to the next (`imul eax,[0x0049931C]`, `0x004542E8`) both use the real width; only the
31 pixel-row steps inside a tile are the literal `0x200` = 512, i.e. the stock viewport width
written as a constant. With an 896-wide plane, rows 1…31 of every tile land at byte offsets
`k*512` instead of `k*896`: row `k` of the tile ends up on plane row `⌊k·4/7⌋` at a column offset
of `(k·512) mod 896`, so four out of every seven plane rows get a 32-byte slice of *some* tile's
light while the other three keep whatever the allocator left (zero; *inferred* that this reads as
the darkest level — the tester saw black). Every tile repeats the same pattern relative to its own
origin, hence a perfectly regular grid of unlit lines over the whole battlefield. The tile *pixels* underneath are correct:
they are drawn by `0x0045011C` (`tile.c`/`juicel.c`, see below), which takes its destination
stride from `view+0x10` and reads the light back through `0x004538C0` = `plane + y*[0x0049931C] +
x`, the right formula — it was only ever fed a plane filled at the wrong stride.

This is the §8.2 trap in a third form. Not a 640 and not a strength-reduced multiply, but a
*viewport* constant, 512, encoded as a plain `imm32`; a search for `280h`/`500h` cannot see it,
and the "sweep for `shl` after a ×5 idiom" cannot either. What found it was reading the function
that owns the buffer end to end after the symptom was known. A grep for `,200h` over
`AUTO 0x430000…0x46FFFF` afterwards shows the 31 sites and nothing else resolution-shaped: the
other hits are the `sprite->xsize < 512` asserts at `0x0043623E`ff (`engmain.c` line 236), a
`mov ebx,200h` buffer length at `0x00457A18`, and struct offsets.

**Fix:** `patch_resolution.py` stage 3 now rewrites all 31 to the viewport width
(`05 80 03 00 00` / `8D B8 80 03 00 00` for 896). Same length, no code motion; ENGEXP16 takes them
at `+0x60`, byte-verified in the stock file. Stage 3 is now 52 sites, plus the 11 lightmap-frame
sites and the 2 PE header fields when the frame is grown: **107 edits** in total for 1024×768
(was 76).

##### The tile drawer, for the record

The function that does put terrain pixels on screen is `0x0045011C` (between `juicel.c` and
`lighting.c` in link order, so `tile.c`/`mapit.c`). It takes the view struct in `eax` and:

* reads the tile counts back out of the viewport size (`[view+0x0A]>>16>>5`, `[view+0x0C]>>16>>5`),
  the destination stride from `[view+0x0E]>>16` (= the u16 at `+0x10`) and a **zoom shift** from
  the u16 at `+0x12` (`edx = 5 - zoom`, tile pixel size `32 >> zoom`);
* computes each tile's destination as `dest + ((row<<zoom)*stride + (col<<zoom))*2`
  (`0x004501F0…0x00450218`, into `0x00499314`), so it is resolution-independent;
* dispatches the actual 32×32 blit through two function tables indexed by zoom and by two tile
  flags: `0x0048C14C` (`call [edx+eax*4+0048C14Ch]`, `0x004503E1`) and `0x0048C15C`
  (`0x004502E7`) — another pair of `DGROUP → AUTO` pointer tables that no call graph shows;
* builds the **occlusion mask** (`view+0x18`, "kev: maskbuffer") as it goes: for each tile it
  writes `32 >> zoom` dwords, one per pixel row, each dword being the 32 pixels of that row of the
  tile passed through the bit table at `0x005326A4` (built by `0x004500B0`); the mask row stride
  is `tiles_across * 4` bytes (`0x00450384`, `0x004503AF`) and the tile-row advance
  `tiles_across * (32>>zoom) * 4` (`0x004503FA`). So the mask is `tiles_across*4 × tiles_down*32`
  bytes = `0x7000` at 16×14 and `0x14200` at 28×23 — the layout derives from the view struct and
  only the allocation size (`0x00435F88`) is literal, as stage 3 already assumed.

##### The build buttons: two widget kinds the HUD tool did not know

`MAINE` has **80 `count` widgets** — every building, troop and upgrade button in the build panel,
a `pushb` with a price counter — and one `scount`, the money counter. Neither kind was in
`hud_layout.py`'s `KINDS`, so `maine apply` moved the 75 generic panel widgets to x ≥ 900 and left
all 81 of these at x = 518 / 577 / 524, which at 1024×768 is the middle of the map view. The
widget layer paints them, the terrain paints over them next frame: "not visible at all". The unit
order buttons (`pushb`/`checkb`) had moved, which is why those looked right.

Also visible in the same test data: §6.1 above previously counted "82 widgets" for the same
reason. Corrected there. `hud_layout.py maine` now shifts 156 panel widgets right.

**Second visual test (same evening): both fixed** — no lines, build panel present.

##### Where the panel's extra 288 rows go

The maintainer's next note was that the Build button, its status box and the money counter must
sit on the bottom edge of the screen as in the original. The first `build` had spliced the
panel's new rows in at y = 450, *below* the Build button, which left the button mid-screen with
blank rail under it. Measuring the panel column (`x 516…639`) row by row settles where the seam
belongs: rows `94…398` carry only the 6-px side rail (the button grid is drawn entirely by the
`count` widgets), and row **399** is the first full-width bar of the bottom cluster — status box
`399…415`, BUILD `416…432`, DAYS `433…444`, money dial `445…472`, bottom frame `473…479`. So the
splice is at **399**: minimap, tabs and grid stay at the top, everything from 399 down moves by
288 to `687…767`, and the four widgets on it move with it — `in_text #79` (520,404) → (904,692),
`pushb #19` BUILD (516,422) → (900,710), `in_text #234` DAYS (613,433) → (997,721), `scount #75`
(524,456) → (908,744). `hud_layout.py` has `PANEL_INSERT = 399` for the script side and
`insert=399` on the `right_panel` region for the art side; they must agree. `plan` output now:
156 × `x += 384`, 10 × `y += 288` (4 bottom-bar widgets, 2 message lines, the 4-widget cluster),
1 left alone (`picture #199`).

**Third visual test:** the bottom cluster is in place. Remaining complaint: the bottom bar's
message box had "an ugly break in the middle".

##### The bottom bar: splice inside the message box, and repeat a stretch, not a line

The first `build` spliced the bar's 384 new columns in at x = 520 — *after* the message box's
right frame (`509…515`) and into the panel corner — and filled them with the single most common
column, so the box was cut in two with a bar down the middle. Measured column by column, the bar
is: arrow buttons `4…44`, the message box frame `46…52`, the **box interior `53…508` with only four
distinct columns** — black between two bevel lines whose rows 457/461/474/478 alternate at random
between two greys (`44/40`, `60/62`) — the right frame `509…515`, then the panel. The §5.1 note
"no period beyond ~49 px, needs real artwork" was measuring that random dither and drawing the
wrong conclusion: there is no period because it is noise, and noise repeats invisibly.

So `build` now splices the bar at **x = 300**, inside the interior, and fills — for every
region — by repeating the **128 lines just before the splice** rather than one line. The top and
left borders are the same case (two lines in random alternation), the panel and the map edge are
constant over their splice, so the 128-line filler is exact everywhere and the whole frame extends
without new artwork. The one bottom-bar widget right of the splice, `in_text #200` at (480,463),
the 3-character field at the box's right end, moves with the box's end: → (864,751)
(`BOTTOM_INSERT = 300` in `hud_layout.py`). `plan`: 156 × `x += 384` in the panel plus that one,
10 × `y += 288`, 1 left alone.

**Still unverified:** the lighting at the right-hand edge during a night phase (§10.5's open
question), and the new bottom bar in the game.

#### 10.7 The menu furniture that code draws: two primitives, 44 immediates, and two data omissions **(verified)**

After the battlefield was right, a full pass through the menus at 1024×768 produced the list
§10.1 had warned about: the campaign overview text, the mission-briefing globe and description,
the network screen's globe, the encyclopedia's text and turning models, the victory screen's
debrief text and medal — all still at 640×480 coordinates while the letterboxed scripts around
them had moved by (+192,+144). Plus two captions on the main menu ("Choose race", "Type in a name
for your leader") and the "Rank" caption on the briefing screen.

##### Everything code-positioned goes through two `scenario.c` primitives

Reading the seven screen functions in `main.c` (they are the ones containing the
`load_interface` calls listed in §6) shows that they draw nothing themselves. Every
code-positioned element is created by one of two calls, both taking **x in `edx` and y in `ebx`**
as `mov reg, imm32` at the call site and storing them once:

| Primitive | Args | Stores | Drawn by |
|---|---|---|---|
| `0x00428448` **text-file box** ("TTY", asserts "Too many TTYs" `0x48501C`) | `(app, x, y, w; [esp] h, filename, font, …)`, `ret 20h` | slot record, stride `0x3EE8`: x `0x4D61C8`, y `0x4D61CC`, w `0x4D61D0`, h `0x4D61D4` (`0x004284F0…0x00428517`) | `0x00428968`: glyph at `x + col·cellw + col`, `y + row·cellh + row`, `call [ctx+5Ch]` |
| `0x004289D0` **animated picture window** ("PIC", asserts "Too many PIC Windows" `0x485078`) | `(app, x, y, spritename; [esp] period, …, erase_bg)`, `ret 1Ch` | slot record, stride `0x60`: x `0x4DE008`, y `0x4DE00C` (`0x00428A65/74`) | `0x00428B8C`: erase rect `call [ctx+60h]`, cell blit `call [ctx+58h]` with `edx=x, ebx=y` |

So the whole fix is the 44 immediates at the call sites, all `imm32` (no `imm8` overflow
anywhere), all `+192` / `+144`. Widths and heights stay. The `push 32h/21h/42h/2Dh` next to the
PIC calls are frame periods, not coordinates. `patch_resolution.py` carries them as
`MENU_FURNITURE` in **stage 2**, since they belong with the letterboxing they compensate for:

| Screen (function) | Element | Sites | Stock (x, y) |
|---|---|---|---|
| campaign overview `story` (`0x004023F4`) | `hstory.txt`/`astory.txt` TTY 579×420 | `0x0040247B` / `0x00402471` | (10, 13) |
| encyclopedia `encyclo` (`0x00402614`) | unit text TTY 238×356, six code paths | `0x00402707 …0x00402E40` (x), `0x00402702 … 0x00402E4A` (y) | (20, 106) |
| | unit model PIC, six code paths | `0x0040272F … 0x00402E8B` (x), `0x00402728 … 0x00402E84` (y) | (303, 13) |
| mission briefing `shuman` (`0x00402F90`) | description TTY 294×225 | `0x0040320A` / `0x00403200` | (310, 212) |
| | globe PICs `globeg`+`globes` (human) / `earthg`+`earths` (alien), three call sites sharing a tail | `0x0040323F/7C/9F` (x), `0x00403249/75/9A` (y) | (34, 26) |
| network `netopt` (`0x00405C40`) | globe PIC `intrface/blew` | `0x00405C71` / `0x00405C60` | (336, 24) |
| victory `wingame` | debrief TTY 445×112 (`"%s.%3.3d"`) | `0x00404281` / `0x0040427A` | (29, 190) |
| | medal PIC `intrface/mdl%c`, created and re-created on click | `0x00404474/0x004047F3` (x), `0x0040446D/0x004047EC` (y) | (541, 169) |
| intro `bintro` (`0x00404DC8`) | scrolling `credits.txt` TTY 280×100 | `0x00404EA0` / `0x00404E99` | (178, 200) | — since §10.11 not +192/+144 but the full-frame menu's cluster position (fixups) |

§10.1's guess that the network screen shows `globeg/globes` was wrong: that pair (and
`earthg/earths`) is the *briefing* globe; the network screen's is `blew`. The main menu draws
nothing by code, and there is no separate "name your leader" screen: that prompt is `in_text 5`
plus `label 18` on `NEWGAMEE`. `meta`, `multi`, `lost`, `loadg`, `dpblank`, `dplays`, `getsvr`,
`ipxname` and the `interface.c` dialogs call neither primitive.

##### Two things that were data after all

1. **`label` was missing from `pad_background.py`'s widget-kind list.** The keyword table at
   `0x00489588` pairs `"label"` with handler `0x00426BD4` (button.c's label button; the table is
   `{name, creator}` pairs and the neighbouring `0x00424A40` is the `gadget` creator - corrected in
   §10.40, the conclusion below is unchanged), which parses `x y w h` through the
   same field parser (`0x00422414`) as every other kind. So the 44 `label` lines across the
   scripts — including the four the tester saw — had never moved. `POSITIONED` now has `label`
   (and, for completeness, `count`/`scount`). A census of every keyword in the 30 scripts found
   no other positioned kind: `banim` carries frame indices, `animation` and `text` file names.
2. **The mission marker on the briefing globe is placed by `GAMESTAT/*SCENE.TXT`.** `main.c`
   `0x00403732` creates `intrface/epic` at `(gs+0x14E0, gs+0x14DC)` when the globe reaches frame
   `gs+0x14D4`; `scenario.c 0x00429B67`ff fills those three with `sscanf("%d %d %d")` from the
   `frame x y` line of each mission block (the line after the two `.avi` lines, e.g. `25 110 30`).
   `pad_background.py apply` now adds (192,144) to the second and third numbers in `HSCENE.TXT`,
   `GSCENE.TXT` (15 blocks each), `HTSCENE.TXT`, `GTSCENE.TXT` (7 each), with `.bak`s, and
   `revert` restores them. Which of the two numbers is x is *inferred* from the two `mov` at
   `0x00403726/2C`; a wrong guess would show as a marker off the globe.

`pad_background.py` also re-derives from `.bak` on a re-run now, so adding a kind and running
`apply` again is enough (previously a padded script was mistaken for a sub-window dialog and
skipped).

**ENGEXP16 note.** The two `netopt` sites sit at `−0x20`, not `+0x60`, and the credits' stock y
differs (`0xE6` = 230, not 200): `main.c` is not a uniform shift there. The expected-byte check
caught it; §10.10 re-derived those three and the patcher carries them as build fixups.

#### 10.8 Movies: the frame path, and why stretching needed a re-route **(verified in code; game test pending)**

Same test pass: at 1024×768 the FMVs played letterboxed in the top-left instead of stretched
across the screen, and with periodic black lines. All 59 `AVI/*.avi` are **320×180 Cinepak**
(`strf`), so the source is 16:9, not 320×240.

##### How a frame reaches the screen (`avi.c`)

```
main.c 0x00401137  avi_begin 0x00406FD0 → avi_create_surfaces 0x00407068
main.c 0x00401149  [vtbl+3Ch] → 0x004073B8 → play_avi 0x00408CB8
main.c 0x0040114E  avi_end   0x00406FF0 → clear_and_flip 0x00407440, release_surfaces 0x00407018, mode cycle

play_avi: open_video 0x0040818C (AVIStream + ICLocate, BITMAPINFO forced to 24 bpp at 0x004083DA),
          open_audio 0x00407E60, build_luts 0x0040755C (R/G/B 8→16 LUTs 0x4A49C0/4DC0/51C0, then the
          geometry below), four threads (0x00408784 audio feed, 0x00408BB0 decode, 0x00408AC8 display,
          0x00408558 sync)
display thread 0x00408AC8: cmp [0x488E0C],1 ; jne → draw_offscreen 0x00407898
                                            ;  else  draw_flip      0x00407674
```

`avi_create_surfaces` makes a primary + one back buffer (`DDSCAPS 0x4218`, `0x489718`/`0x48971C`,
flag **`0x488E0C = 1`**, "flip mode"). Only if that fails does it fall back to a plain primary
and create the **320×180 offscreen movie surface `0x488E00`** (`0x004071F7…`, `dwWidth` at
`0x0040722B`, `dwHeight` at `0x00407230`, flag = 0).

**Flip mode, `draw_flip 0x00407674`, is a software 2× doubler into the back buffer.** It Locks
the back buffer and *does* take `lPitch` from the description (`0x004076C9`), converts each 24-bpp
source pixel through the LUTs into a DWORD holding two identical 16-bit pixels
(`mov [edx+ebx*4-4],edi`, `0x00407760` — the horizontal 2×), then per source row advances the
destination by **two** rows (`0x0040776F/0x00407775`) having written **one**. So the stock game
paints 640 × 2(H−1) = 640×358 at `(0, yoff)` with **every odd row untouched** — the "periodic
black lines" are the design of the stock doubler, filled black by the clear at 640×480 and
presumably invisible on a CRT. `yoff = 241 − H` (`0x0040762B`, `mov edx,0F0h; sub edx,H−1`)
centres the doubled picture in 480 rows: rows 61…418, 61-px bars. The letterbox is the stock
proportion.

Two things break at 1024×768: the doubler is fixed 2× anchored at x = 0 (no x term anywhere in
`0x004076F0…0x00407703`), so it fills 640×358 of the 1024×768 back buffer; and the clear
`0x004073C4` Locks the back buffer but then **ignores the description** — 640 pixels per row, a
1280-byte pitch and 480 rows are literals (`0x004073F4/05/0B`) — so with a 2048-byte pitch it
clears exactly the top 300 rows and leaves the rest as stale VRAM, which is what shows through the
skipped rows below row 300.

**Fallback mode, `draw_offscreen 0x00407898`,** writes the frame **1:1** into the 320×180 surface
(pitch from its own Lock, `0x004078ED`), then `BltFast`s it to the primary at `(160, 2·yoff)`
(`0x00407E14…0x00407E4F`). `BltFast` cannot stretch, so nobody ever saw this path at its best.

##### The fix: use the fallback surface in flip mode and stretch with `Blt`

A same-length edit cannot make the doubler fill 1024×768 (3.2× is not integer, 3× needs three
stores and three row writes — a loop rewrite). The 1:1 path plus a **stretching
`IDirectDrawSurface::Blt`** does everything at once — full width, every row written, dest rect a
tunable immediate — and every edit is in place. Stage 4 of `patch_resolution.py`:

| Site | Stock | New | Effect |
|---|---|---|---|
| `0x004073F4` / `05` / `0B` | `cmp eax,280h` / `add ecx,500h` / `cmp edx,1E0h` | `cmp eax,[ebp-60h]` / `add ecx,[ebp-5Ch]` / `cmp edx,[ebp-64h]` (+ nops) | clear reads dwWidth / lPitch / dwHeight from its own Lock description (`DDSURFACEDESC` at `[ebp-6Ch]`: +8 height, +0xC width, +0x10 pitch, +0x24 `lpSurface` — the last was already read) |
| `0x0040712E` (13 B) | `test eax,eax; je 40728B; jmp 4073AC` | `mov edx,eax; jmp 4071E0; nop×6` | after `GetAttachedSurface`: on success `edx=0` → `0x4071E0` `test edx,edx; je 4071F7` → create the 320×180 surface (the `memset(desc, edx, 6Ch)` at `0x00407204` takes its fill byte from `edx`, hence the `mov`); on failure (`eax≠0`) the same path returns 0 via `0x4071E4` |
| `0x0040723D` | `mov [488E0C],ebx` (=0) | nops | keep the flip flag when the movie surface is created in flip mode |
| `0x00408B3E` | `jne 408B47` | `jmp` | display thread always takes `draw_offscreen` |
| `0x00407E14` (62 B) | `BltFast(primary, 160, 2·yoff, movie, &(0,0,320,180), WAIT)` | `Blt(primary, &(0,96,1024,672), movie, NULL, DDBLT_WAIT, NULL)` | the stretch. Dest rect built in `[ebp+62h…71h]` (loop temporaries, dead after the copy); 16:9 across the full width, centred — the stock proportion |
| `0x00407479` | `jmp 407552` | nops | `clear_and_flip` falls through into the movie-surface clear (null-checked) in flip mode too |
| `0x00407030` | `jne 40704A` | nops | `release_surfaces` releases the movie surface whenever it exists (else one surface leaks per movie) |
| `0x0040791B` | `jae` | `ja` | `draw_offscreen` writes all H rows; stock wrote H−1 and left the bottom row stale (an off-by-one the doubler shares) |

The first draft of the `0x0040712E` rewrite used `je 4071F7; jmp rel8 4071EC` — the `jmp rel8`
is 178 bytes forward and does not fit a signed byte (it would have landed at `0x4070EC`); the
`mov edx,eax; jmp rel32 4071E0` form above avoids a second jump entirely. The Blt block carries
three absolute data addresses (`0x489718`, `0x488E00`, `0x4A5680`), so `patch_resolution.py` builds
both its expected and its replacement bytes from the build's DGROUP and `.bss` shifts; ENGEXP16
shifts `0x488Exx`/`0x4897xx` by `+0x28` and the `.bss` address not at all (§10.10), and the block
matched byte for byte at `0x7274` once the table said so.

**Risks.** The movie surface is requested as `VIDEOMEMORY` (`0x4040`, from `[ebp-4]=1` in flip
mode); if all 10 attempts fail, `avi_create_surfaces` returns 0 and `main.c` skips the movie
silently — the fallback edit is `0x0040721B` `C7 45 F4 40 40 00 00` → `… 40 08 …`
(`SYSTEMMEMORY`). `Blt` to the primary without `Flip` can tear (the stock fallback did the same;
blitting to the back buffer and flipping needs 15 bytes more than the block has). Dest-rect
alternatives are the four immediates in the block: `(0,0,1024,768)` full screen (distorts
16:9→4:3), or 3× integer `(32,114,992,654)`.

#### 10.9 The minimap's input side **(verified)**

Fifth test pass: clicking the minimap to send troops did nothing. Stage 3 had moved the
minimap's *rendering* (the two framebuffer origins and strides, §8.1) but not the three other
places that know where it is, all of them the plain immediate **519** (`0x207`):

| VA | Code | Role |
|---|---|---|
| `0x0041EE43` | `proto.c`: `make_rect(519, 6, 96, 84)` → `ui+0x7B4` (`0x004AB184`) at mission start | the **hit rectangle**. The click dispatcher `0x0040A484`ff tests the map view rect first (`[ui+8]`), then this one (`point_in_rect 0x0043658C` on `ui+0x7B4…0x7C0`, `0x0040A517`) before calling the minimap handler `0x0040A070` |
| `0x0040A084` | minimap handler: `sub eax,207h` | mouse x → minimap column: `tile_x = ((x−519)·2+1)·map_w / 96 / 2`; the y side is `90 − y` (`0x0040A0B5`, the minimap's bottom edge, unchanged) |
| `0x0043A1E4` | `engmain.c` minimap plot: `make_rect(519, 6, 96, 84)` | the clip rect for the plot |
| `0x0043A27A` | `add eax,207h` | the **view-box indicator**: `view_x·96/map_w + 519` (y side `+6` at `0x0043A25F`) |

All four → the new minimap x (903), stage 3 of `patch_resolution.py`. ENGEXP16 has them at
`+0x60`. The other `0x207`/`0x208` hits in the binary are assert line numbers and buffer sizes.
So the minimap's x is written **seven** times in three ways — two framebuffer byte offsets
`(6·640+519)·2`, one rect built at init and reused, four bare immediates — and the y (6) twice
as a rect argument, once as its bottom edge 90, and inside the two byte offsets.

#### 10.10 Stage 6: the same patch on Council Wars `ENGEXP16.EXE` **(verified; confirmed in game)**

With the Classic build confirmed in five test passes, the whole patch was carried over to the
expansion's executable. Running the patcher against stock `ENGEXP16.EXE` refused to write on
exactly **five** of the 165 edits, all three of them kinds of deviation that the `+0x60`/`+0x228`
rule of §8 does not describe:

| Site(s) | Rule said | Found | Cause |
|---|---|---|---|
| `netopt` globe PIC x, y (`0x00405C71/60` Classic) | `+0x60` → `0x50D1/0x50C0` | at **`0x5051/0x5040`** (`−0x20`); the stock values 336/24 unchanged | `main.c` is `0x20` bytes shorter from just after the intro screens up to `avi.c` — the same stretch that put the CD-button patch at `0x507F` instead of `0x509F` (CLAUDE.md) |
| `bintro` credits TTY y (`0x00404E99`) | `bb c8 00 00 00` (200) at `+0` | `bb e6 00 00 00` (**230**) at `+0`; the x (178) matches | a different stock layout for the expansion's own `exp/intrface/credits.txt`; the target is still stock + 144 |
| flip-flag store `mov [0x488E0C],ebx` (`0x0663D`) | `+0x228` in DGROUP | `mov [0x488E34],ebx` (**`+0x28`**) | see below |
| the 62-byte `BltFast` block (`0x07214`) | `0x489718`, `0x488E00`, `0x4A5680` `+0x228` | `0x489740`, `0x488E28` (**`+0x28`**), `0x4A5680` (**`+0`**) | see below |

**Why `+0x228` and `+0x28` are both right.** ENGEXP16's `AUTO` section has a raw size of
`0x7E400` against Classic's `0x7E200`, so the *file pointer* of every later section (`.idata`,
`DGROUP`, `.reloc`, `.rsrc`) is `+0x200`, while the sections' *virtual addresses* are identical
(`DGROUP` at `0x482000`, `.bss` at `0x49A000` in both). Inside `DGROUP` the data the patched code
names has moved by `+0x28` (the dimension anchor: VA `0x488DB4` → `0x488DDC`, i.e. file `0x865B4`
→ `0x867DC` = `+0x200 +0x28`; the movie surface, its flag and the primary likewise). The `.bss`
variables the patch names — `yoff 0x4A5680` and the DirectInput mouse position `0x5327C0/C4` —
sit at the same VA in both builds. So a site's *file offset* moves by the AUTO rule, a DGROUP
*address in its bytes* by `+0x28`, and a `.bss` address by `+0`; the earlier `+0x228` was the
anchor's file shift misread as a VA shift.

**The code-section delta map.** Matching 32-byte windows of Classic at every `0x40` step against
ENGEXP16 (±`0x200`) gives, ignoring one-window blips at jump tables: `+0` from `0x400` to about
`0x4500`; `−0x20` from about `0x4800` to `0x5880` (`main.c`'s network screen and everything up to
`avi.c`); `+0x60` from `0x5880` (VA `0x406480`) to the end. The patcher's threshold of `0x5000`
therefore mis-derives only sites in `0x4800…0x5880`, of which the site table has exactly the two
`netopt` ones. The 23 `TTY`/`PIC` call sites of §10.7 are one-to-one between the builds with the
same 44 immediates except the two moved and the one changed above, so the expansion adds no
code-positioned menu furniture of its own.

**What the patcher does now.** `BUILDS` is a table per build of `(AUTO shift, DGROUP VA shift,
.bss VA shift, fixups)`; a fixup overrides a site's file offset, its expected bytes and/or its
value function, and everything else still goes through the same expected-byte check. The two
sites that name a `.bss` address (`DirectInput clamp: X/Y maximum`) and the flip-flag store now
build their expected bytes from the shifts too, so the table has no hidden absolute address left.
Result: `plan` prints **165 edits** for both builds with no mismatch; the 24 bytes of code before
every ENGEXP16 site differ from Classic in at most four bytes (relocated operands), so each match
is the same instruction in the same function, not a coincidence. `verify` after `apply` reports
18/66/67/10 patched per stage and the two PE stack fields patched, i.e. the same tallies as
Classic.

**What `exp/` is.** `ENGEXP16` opens every data file through a wrapper (`0x004063E4`, string
`"exp/"` at `0x4826D0`) that first tries `exp/<path>` and, if that open fails, `<path>`. So the
expansion overrides individual files without touching the base install: `exp/intrface/bintroe`,
`introe`, `shumane` (the intro screens with the campaign buttons rearranged into one column at
x = 228, and the briefing screen with one button commented out) — their `background` GIFs are the
base `INTRFACE/INTRG.GIF`, `INTRO.GIF`, `SHUMAN.GIF` — and `exp/gamestat/hxscene.txt`,
`gxscene.txt` (the Aerogen and Council campaigns, eight mission blocks each, with the same
`frame x y` globe-marker line as §10.7). `pad_background.py` therefore learnt to look for a
script's GIF in the game root's `INTRFACE` when the script lives in `exp/intrface`, and to treat
`HXSCENE`/`GXSCENE` as scene files; the Council Wars data build is two runs, `INTRFACE` first
and `exp/intrface` second.

**Test build** `C:\Users\nika\Documents\Dark-Colony-CW-1024` (a copy of `DC - Council wars`):
`patch_resolution.py apply` (165 edits, `.bak` kept), `pad_background.py apply` on `INTRFACE`
(29 scripts, 18 GIFs, both loading bitmaps, four scene files) and on `exp/intrface` (3 scripts,
2 scene files), `hud_layout.py build` + `maine apply`. Because the expansion ships the Classic
`INTRFACE.GIF`, `MAINE`, `MAINBUT.SPR`, menu scripts and scene files unchanged, all 54 base data
outputs are **byte-identical** to the confirmed Classic test build; only the three `exp/` scripts
and the two `exp/` scene files are new. **The maintainer played the build on 9 Sep 2026 and
reported everything working.**

##### Release: the repository binaries are patched

With both test builds confirmed, the same three tools were run on the repository copies
(`DC - Classic` and `DC - Council wars`) on 9 Sep 2026, and every changed file was checked
byte for byte against the corresponding test build before committing: 165 exe edits per binary,
the letterboxed menus, loading screens and scene files, the rebuilt `INTRFACE.GIF` and `MAINE`,
and for Council Wars the `exp/` overrides. The `.bak` files the tools leave beside the originals
are gitignored; the stock executables and data are the versions before that commit (last stock
state: `0307feb`), and `patch_resolution.py verify` distinguishes a patched binary from a stock
one by reading the dimension globals back. Because `pad_background.py` and `hud_layout.py maine`
recognise an already-transformed script (a four-argument `size` line), re-running the tools on
the repository is a no-op rather than a double shift.

Still open under stage 6: Council Wars `dc16.exe` (a different build: only the §3 anchor
transfers, so the whole site table would have to be re-derived in Ghidra), and the map editor.

#### 10.11 The main menu repainted full-frame: a procedural backdrop and re-baked logos **(verified by measurement; game test pending)**

The first screen after the intro movie (NEW CAMPAIGN, TRAINING, LOAD GAME, …) was the most
visible letterboxed screen: a 640×480 picture with black borders in a 1024×768 frame. On
10 Sep 2026 it became the first screen painted at the new size (Route B of §10.1). Four things
about it were data that had to be measured before anything could be drawn — and one of them was
learned from a failed game test.

**Which script is the menu.** The retail exe names only `intrface/bintro` (`main.c` `bintro`
`0x00404DC8`, the function that also creates the scrolling credits box of §10.7), so the live menu
is **`bintroe`** with background **`INTRFACE/INTRG.GIF`**, the `DCUK` logo animation and the `DCUT`
title. `introe` (background `INTRO.GIF`, `DCSS` logo) is the same menu in an older dress and is
not reached; `BUTTONSE` and `DINTROE` are demo leftovers on `INTRO.GIF`. The first pass of this
work repainted `INTRO.GIF` and `introe` only, and the maintainer saw no change in either game —
that test is what pinned the screen down. Council Wars adds `exp/intrface/BINTROE` / `INTROE`
overrides (one column of buttons, the same sprites at the same places). `INTRG.GIF` and
`INTRO.GIF` are the same picture except for the bottom band (Take 2 logo and copyright in the one,
SSI logo, red rule and copyright in the other); both now exist full-frame and all six scripts say
`size 1024 768`.

**The palette.** Both GIFs' colour table is **the game's master palette, byte-identical to
`PALETTE.GIF`** (`CHOO`, `LOADER`, `LOST`, `STORY`, `NET` have their own). `INTRO.RGB` (32 768 B,
the 15-bit inverse lookup) and `INTRO.RMP` (196 608 B) are derived from that table, so the repaint
keeps all 256 entries as they are and only produces new indices — the constraint §5.1 states for
the HUD frame. The sprites' embedded palettes differ from it in one or two entries and are not
what the game draws with (viewing `DCUK` through its own table gives a green sky); always decode
sprites through the screen palette.

**Geometry of the stock picture, measured on the indices.** Fitting circles to the limb
(outermost limb-coloured pixel per row, both sides, plus the top edge) gives the planet as a disc
centred at **(320, 351) with radius 291** in the 640×480 frame (left edge alone fits with σ =
0.95 px), i.e. centre (0.500 W, 0.731 H), radius 0.607 H. Outside the limb a **10 px glow**
(luminance 7 → 50 over rows 48…60 at the centre column). Inside, a **lit crescent 45 px deep at
the top**, whose brightness along the centre column falls as (1 − d/45)^1.3 (luminance 115, 95,
57, 33, 9 at d = 0, 10, 20, 30, 40) and whose depth and peak fall with the angle θ from the top
as ≈ 45·cos^2.2 θ and ≈ cos^1.6 θ — 30 px deep at 30°, 22 at 45°, 10 at 60°, nothing beyond
±75°. The peak colour is index 204 (239, 95, 0); the whole crescent lives on the orange ramp
201…251, and it already carries faint darker streaks (surface texture). The sky holds **1 244
stars over 251 198 px** (4.95 × 10⁻³ per pixel): 55 % single pixels, 67 % with a peak below 40
(indices 67/66/62), 6.5 % saturated white, mostly neutral with a few blue (107), violet (119) and
pink (144…159) pixels — the violet/blue ones are the halos of the bright stars. Everything below
row 421 is the band: in `INTRO.GIF` the SSI logo (rows 423…447, x 274…359), a full-width rule
(rows 448…458, one solid colour per row: indices 56, 4, 1, 97, 84 ×4, 194, 1, 44) and the
copyright line (rows 462…475, x 48…587); in `INTRG.GIF` the Take 2 logo and copyright (rows
435…478, x 97…516), no rule.

**The picture is not resampled; it is re-rendered from those numbers** (`tools/paint_intro.py`).
Stars and planet are procedural — the master is the generator and its measured parameters, so
any resolution is a re-render, which is the point of the vector-first rule of §5.1 for art that
has no vector form. Stars keep their pixel size (a point of light does not grow with the canvas)
and their density per pixel, with the stock brightness distribution (2/3 faint, ~5 % saturated),
the nearest pixel always carrying the full brightness so single-pixel stars stay crisp, and the
bright ones get the stock's cold blue-violet halo and a faint cross. The planet keeps its
proportions (centre and radius scale with the height), the measured crescent profile, and gains
what "more robust" was asked for: two scales of noise in a longitude/latitude parametrisation of
the sphere (so features foreshorten towards the limb), 0.55…1.35 albedo, dark terrain leaning
browner; the thin atmospheric rim at the edge is not modulated by terrain. The render is
quantised to the nearest colour of the fixed palette, with ±2 random dither only inside the disc
to break the ramp's 4-step banding; index 0 is never used (black is 254 as in the stock files).
The bands are UI-scale pixel art like the buttons and are **copied 1:1** (a row that is non-black
across the stock width is a rule and is extended to the full width; the band keeps its distance
from the bottom edge and is centred). Statistics of the result: 259 star pixels per 10 k px
against the stock's 324 at 640×480; 121 distinct indices against 135.

**The logos have the stock backdrop baked into every frame — and live in `SPRITES/`.**
`gadget 14 … DCUK anim_stopped unmask - bg erase` names an *animation*, not a sprite file: the
screen's `INTRG.DAT` lists `knobe.fin`, `dcuk.fin`, `dcut.fin`, and `ANIMATE/DCUK.FIN` (2 276 B;
header `1D 00 0C 00 01 00 02 00`, bank names `dcuk`/`dcut`, animation name `DCUK`, then 18-byte
frame records `bank[8] cell:u16 flags:u16 duration:u32 …` for cells 2…13) is loaded through
`"animate/%s"` (`animate.c` `0x00425613`) and opens its sprite banks through **`"sprites/%s"`**
(`0x00425356`). So the frames the game draws come from **`SPRITES/DCUK.SPR`**, a **14-cell 310×120
assembly animation** (cell 0 a solid flash, index 133); `INTRFACE/DCUK.SPR` is a byte-identical
copy that nothing on this screen reads — the third game test, which still showed the stock stars
after only the `INTRFACE` copy had been re-baked, is what proved it. (`SPRITES/DCSS.SPR`, 29 cells,
is a longer cut of the `introe` logo than the 15-cell `INTRFACE` copy.) Each cell contains sky,
stars and the top of the limb — the gadgets are `unmask` (keyword table `0x004849A8`, handler
`0x00424BCB`), so the sprite carries its own background. `paint_intro.py apply` re-bakes every
copy it finds in `INTRFACE/` and `SPRITES/`. Black in the
sprites is index 0 (index 254 never occurs). That backdrop is *not* a byte copy of the GIF (only
20 % of the arc pixels agree; it is another quantisation of the same scene using browns 184…186
and red 101 the GIF does not), and the letters shimmer (consecutive frames are only 50–60 %
identical; `DCSS`'s last five frames agree on just 24 pixels), so neither "equals the GIF" nor
"static across frames" identifies it alone. With the planet 1.6× larger the baked arc showed as
a second, smaller limb through the letters. `paint_intro.py apply` therefore **re-bakes** the
cells (first against the new render, since the second game test with plain black — see Layout
below): a pixel is backdrop if it agrees with the per-pixel mode over the frames where that mode
has ≥ 5 supporters, or has a limb colour (r > g+8, r > b+8, g ≤ 0.6 r — 87 indices; the letters
above the limb contain 0.5 % such pixels), or is a non-limb speck of ≤ 12 connected pixels (a
star), or is black sky more than 1 px from a letter (the letters' black shadows stay 0). Those
pixels take the new render at the logo's new position; the letters, their shadows and the flying
pieces are untouched. **Fourth game test:** most stars gone, but light pixels still flickered on
the logo animation. Measured on the re-baked frames: a 179-px streak of the limb's darkest glow
colour, index 252 (7,0,0), which the colour family `r > g+8` had missed, with white and grey star
pixels glued into it (frames 1…3), plus stars touching a flying piece and therefore merged into
its component. Since the backdrop is now plain black anyway, the `black` mode is stricter than
`full`: the colour family is `g, b ≤ 0.65 r` (87 → includes 252 and 251), a pixel equal to the
*reference frame* (the frame with the least logo in it — the static baked backdrop, minus its own
pieces) is backdrop, and afterwards **everything that is not part of a piece of ≥ 100 connected
pixels is dropped**; a fragment between 12 and 100 px survives only if at least half its pixels
have logo colours (colours present in the big pieces but absent from the reference backdrop),
which keeps the tan sparks of frame 9 (three 13–14 px fragments) and removes star halos. Result:
0…4 surviving stock-backdrop pixels per frame, all dark (`DCUK`: up to 35 502 of 37 200 pixels per
cell replaced, 1 in the flash frame; `DCSS`: 22 311…45 563). `DCUT.SPR` (the 398×33 title, 5 cells: a solid flash, a
dark-red fade-in, a **red-white glow** frame, and the tan text) sits over the black disc and has
only 88 baked star pixels; its glow frame is all limb colours, so it gets the minimal rule —
only pixels equal to a non-black stock pixel underneath are replaced. `spr.py check` passes on all
three.

**The title re-set, the mark kept (fifth to eighth game tests).** With the menu accepted, the
maintainer asked for the DC mark and the "DARK COLONY" title themselves to be redone: clear,
robust and appealing shape, colour and surface — the stock sprites are 1997 pixel art with a
dithered fill and staircase edges. `tools/logo_art.py` re-sets the **title** from a typeface,
Impact stretched to the 398×33 cell (the stock's face is an extended stencil; no stencil font
exists on the machine, and a traced 33 px stencil came out lumpy): 8× supersampling, an erosion
distance field for a 2 px bevel lit from the top left with a specular highlight, a tan gradient
(232,170,112) → (172,112,60), horizontal brushed streaks and fine grain for the surface, a dark
1 px rim so the letters read on black, and nearest-colour quantisation with ±3 dither into the
palette indices with r ≥ g ≥ b (tans, browns, greys — no blue/green speckle); black is index 0;
five 398×33 cells — flash, dark-red fade-in, red-white glow, final with a hot highlight, final;
`DCUT.FIN` unchanged. **Approved in the sixth test.** For the **DC mark** two re-renders were
tried and both rejected: first a parametric vector redesign (a stencil-cut D, a C from two
ellipses with the slit and V-notches, a new seven-band sliding assembly) — "differs from the
original too much"; then the stock frames themselves as masters, backdrop removed, silhouettes
smoothed 0.8 px and re-lit with the same bevel, the frame colour following the stock's
grey-steel-to-tan progression — closer, but after a frame-9 extraction bug (its reddish-brown
pieces are limb-coloured and the colour rule ate a third of them) was fixed, still "not good".
The maintainer's decision (eighth test): **leave the DC logo alone** — `DCUK.SPR` is the original
1997 pixel art, re-baked onto black by `paint_intro.py` (mode `black`, the version approved in
the fifth test), and `logo_art.py` touches the title only. Lesson recorded for future art work:
the original shapes and animations are to be kept; only surface quality may change, and even
that is judged frame by frame in the game. Both `SPRITES/` and `INTRFACE/` copies of `DCUT.SPR`
are written; `paint_intro.py` marks that bank `art` and leaves it alone. `spr.py check` passes.

**Layout — second game test.** The maintainer's second test (background right) asked for two
things: the DC logo on **black**, and **right above the "DARK COLONY" title as in the original**,
not pinned to the top edge over the limb. So the title, credits and buttons form one cluster that
keeps its stock vertical centre as a fraction of the height and is centred horizontally (the
button grid as a unit, the title on its own), and the logo moves by the same amount, centred, so
the stock 39 px gap logo→title is preserved. The logo is an opaque 310×120 rectangle: its
backdrop is now plain black (rebake mode `black`, same pixel classification, index 0 instead of
the render), and because the proportional shift alone (+173) would have put its top rows over
the last (27,7,0) pixels of the crescent's tail (rows 173…190), every screen with a logo shifts
**20 px further** (`LOGO_CLEARANCE`), onto rows that are entirely black under the rectangle
(measured: 0 non-black pixels under (357…667, 193…313)). In `bintroe` the cluster is rows
159…417 (centre 288, i.e. 0.600 H), so everything moves by (+194, +193): logo at (357, 193),
title at (313, 352), the credits box at **(372, 393)**, the button grid at x 332/512, rows
507…610. In the Council Wars override (rows 159…443, centre 301) the shift is (+194, +201) and
the credits, which start at y 230 there, land at **(372, 431)**. All positions are absolute screen
coordinates (§10.1). The credits box is the one code-positioned element of this screen, so the
two `intro credits text` sites of §10.7 (`0x00404EA0` x, `0x00404E99` y; file `0x42A0`/`0x4299`)
no longer follow the (+192, +144) letterbox rule: `patch_resolution.py` carries them as per-build
fixups computed from the same numbers (Classic 200 + round(288·(H/480 − 1)) + 20, ENGEXP16 230 +
round(301·(H/480 − 1)) + 20, x = (W − 280)/2), and `paint_intro.py plan` prints where they must
be. Both exes were re-patched from their `.bak` on 10 Sep 2026: still 165 edits, exactly two
dwords different from the release build (x 370 → 372, y 344 → 393 / 374 → 431). `pad_background.py`
now recognises a script that already says `size W H` for the target framebuffer and leaves it
and its GIF alone, so re-running it on the repository is still a no-op, and its `revert` restores
the stock 640×480 set. Applied to `DC - Classic`, `DC - Council wars` and both test builds
(identical output, seed 7); `paint_intro.py preview` composites the settled logo frames, the
button cells and the credits rectangle over the new background for checking without launching
the game. **Not yet seen in the game.** The `unmask` blit path was inferred from the baked
backdrop, not read; if the game masks index 0 after all, the rebaked sprites still work (their
stars then coincide with the background's).

#### 10.12 The Windows pointer: an uninitialised class cursor and a `WM_SETCURSOR` that falls through **(verified; confirmed in game 10 Sep 2026)**

Symptom on a modern Windows: the system pointer shows over the game — a plain arrow on the first
loading screen, and later, on the main menu and in battle, an icon-sized block that flickers
against the game's own cursor. The game cursor itself is fine: it is a `BltFast` from one of the
32 `cursor/cursor%d.bmp` surfaces (§7), positioned from `0x004DFF14/1C`. What flickers is the
*Windows* pointer, which the stock code hides with `SetCursor(NULL)` (`0x00408CD0` once after
the initial load, `0x0042F2B8` once per frame) but lets the system bring back through two holes:

* **`create_window` (`0x0042E688`) never writes `WNDCLASSA.hCursor`.** The structure is built in
  the stack frame at `[ebp-0x28]`; every field is stored except `[ebp-0x10]` (`hCursor`), so the
  class is registered with whatever the previous callee left in that slot. Windows restores the
  class cursor every time the mouse moves (the `SetCursor` contract: a self-drawn cursor needs a
  NULL class cursor). A stray handle that happens to be a valid icon draws as a cursor — icons
  and cursors are one object type — which is the "block".
* **The window procedure (`0x0042E340`) answers `WM_SETCURSOR` and then calls `DefWindowProcA`
  anyway.** `0x0042E36E: push 0 / call SetCursor / jmp 0x0042E3ED` — and `0x0042E3ED` is the
  common `DefWindowProcA` tail, whose `WM_SETCURSOR` handling sets the class cursor straight
  back. The handler has to return TRUE (halt processing) instead. For the record, the same
  procedure only knows three messages: `WM_DESTROY` (2) → `0x0042E2B0` + `PostQuitMessage`,
  `WM_SETCURSOR` (0x20), and `WM_SYSCOMMAND` (0x112) with `SC_SCREENSAVE` (0xF140) → the assert
  path (`sysfile`, line 0x149, exit).

The message pump at `0x0042F1C8` also peeks `WM_SETCURSOR` (`PeekMessageA(…, 0x20, 0x20,
PM_REMOVE)`) and calls `SetCursor(NULL)` when it finds one, but `WM_SETCURSOR` is a *sent*
message and never sits in the posted queue, so that branch is dead code.

**The fix (`tools/patch_cursor.py`, 4 code sites + 7 `.reloc` entries per binary):**

| Site | Classic VA (file) | Change |
|---|---|---|
| window proc `WM_SETCURSOR` | `0x0042E351` (`0x2D751`), 40 bytes | `SetCursor(NULL); mov eax,1; jmp` epilogue `0x0042E3FE` — no `DefWindowProcA` |
| `create_window` class | `0x0042E6AF` (`0x2DAAF`), 52 bytes | `mov [ebp-10h],ebx` (ebx = 0) before `RegisterClassA` → `hCursor = NULL` |
| `create_window` show | `0x0042E794` (`0x2DB94`), 7 bytes | `call cs:[UpdateWindow]` → `call stub` |
| stub | `0x0047F1D0` (`0x7E5D0`), 12 bytes | `push 0; call SetCursor; jmp UpdateWindow` — hides the pointer before the loading screen, tail-calls the original |

None of this moves other code. The room comes from rewriting Watcom's 7-byte
`2E FF 15 imm32` (`call cs:[import]`) as 5-byte relative calls to the import thunks the linker
already emitted at the end of `AUTO` (`jmp dword ptr [import]`: `SetCursor 0x0047EFF0`,
`UpdateWindow 0x0047EF8A`, `RegisterClassA 0x0047EF9C`, `GetStockObject 0x0047EFA2`,
`LoadIconA 0x0047EFA8`), which behave identically and carry no absolute pointer; `je rel32` →
`je rel8` and `push 1 / pop eax` for `mov eax,1` save the rest. The stub sits in the zero tail of
`AUTO` (raw data runs to `0x0047F1FF`, last real byte `0x0047F1C9`), inside the section's mapped
size. The `.reloc` block for page `0x2E000` is kept exact: the two absolute operands that move
(`hInstance` load, class-name immediate) get their new offsets, the five that vanished (four
IAT operands and the `UpdateWindow` one) become `IMAGE_REL_BASED_ABSOLUTE` padding.

Council Wars `DCEXP16.EXE`: identical bytes at +0x60 (`0x0042E3A0`, `0x0042E6E8`, `0x0042E7F4`,
thunks `0x0047F050/0x0047EFEA/0x0047EFFC/0x0047F002/0x0047F008`, stub `0x0047F230`; its `AUTO`
tail is 471 zero bytes), only the class-name pointer differs (`0x0048591C` vs `0x00485914`). All
relative displacements are therefore the same in both builds. Applied to both repository exes on
10 Sep 2026 on top of the 165 resolution edits; `patch_cursor.py verify` tells stock from patched
by the window-procedure bytes, so it does not depend on MD5s. Both patched binaries were
re-disassembled and the eight regions decode as intended. **Confirmed in the game by the maintainer
on 10 Sep 2026: no system pointer on the loading screen, the main menu or in battle.** Had the
arrow still shown on the loading screen, the remaining suspect would have been window ghosting
(the thread does not pump messages while loading; the ghost window Windows substitutes after
~5 s has an arrow class cursor) — the remedy would have been `DisableProcessWindowsGhosting` via
`LoadLibraryA`/`GetProcAddress` at start-up, not another cursor call; it was not needed.

#### 10.13 A second campaign in the main menu: the ozi_ns mission pack as a *mode* of four strings **(verified by re-disassembly; game test pending)**

The ozi_ns mission pack (2010, 22 missions in two campaigns: Globo `hxscene` and Taar Council
`gxscene`) was shipped as a hex-edited Polish Council Wars exe plus an overlay folder. What its exe
changed shows how Council Wars finds its data, and that is the whole basis of the integration:

* **One overlay helper opens every data file.** `0x004063E4` copies the prefix string stored at
  `0x004826D0` (stock `exp/`, an 8-byte slot in `DGROUP`), appends the name, tries to open it,
  and on failure opens the bare name — i.e. the file in the game root. `exp/` is therefore not a
  folder the code knows about, it is a string. The wave loader `0x00452AB0` (`OpenFile` based,
  used for the `sound2.dat` table, ambience and briefings) has its own copy of the prefix at
  `0x00487DC8`, and the save folder `esave` sits in two more 8-byte slots: `0x00482344` (the LOAD
  GAME screen `0x0040388C`) and `0x00485E5C` (the in-game save/load dialog). All four are writable.
  ozi_ns's exe simply held `M1PACK/` and `msave` there (plus `missiee/` for briefings and `.kor`
  for scene lists — the Polish edition's own extension); no code was changed.
* **What is loaded when.** Balance tables (`0x0043C4AC` ← `0x0041BB50` ← the game start
  `0x0040122C`; also from the save loader `0x0040E1F4`), `sound/slist.dat`, scene lists, missions,
  terrains and the interface scripts are read per game or per screen. But `anim.dat` is read once
  at start-up (`0x00405264` → `0x004051CC`), and for every line `0x0042565C` loads the FIN
  (`animate/%s`) and each sprite bank it names (`sprites/%s`, `0x0042538C`); `sound2.dat`
  (`0x004309C8`, a fixed 200-entry table — `cmp edx,0C8h` at `0x00430A62`) is read at start-up too.
  Whatever a campaign mode needs from those has to be in the base set.

**Design (maintainer decisions, 10 Sep 2026; the single column was replaced by Classic's 2x4 grid
on 23 Sep 2026, §10.35 — the slots and their handlers are unchanged):** Council Wars keeps its
single-column menu; the
useless PLAY INTRO button becomes **OZI MISSIONS** and the unused SINGLE PLAYER WAR button (id 4)
comes back as **OZI LOAD** below it (column x=422: NEW CAMPAIGN 541, LOAD GAME 567, OZI MISSIONS
593, OZI LOAD 619, QUIT 645 — the backdrop there is plain erase colour); the pack's new models go
into the shared `exp/` so both campaigns see them; the pack's briefing speech is included after all
(the first decision against it was reversed once the missing files proved fatal, see below). A
*mode* is the content of the four slots:

| slot | VA | Council Wars | OZI missions |
|---|---|---|---|
| overlay prefix (`0x004063E4`) | `0x004826D0` | `exp/` | `ozi_ns/` |
| wave-loader prefix (`0x00452AB0`) | `0x00487DC8` | `exp/` | `ozi_ns/` |
| save folder, LOAD GAME screen | `0x00482344` | `esave` | `ozisave` |
| save folder, in-game dialog | `0x00485E5C` | `esave` | `ozisave` |

**Code (`tools/patch_ozi_menu.py`, `DCEXP16.EXE` only — the pack is Council Wars content):**

| Site | VA (file) | Change |
|---|---|---|
| PLAY INTRO handler, button id 0x10 | `0x004050DD` (`0x44DD`), 96 bytes | now the OZI handler: `gs+0x14F4 = 1` (expansion scenes), `gs+0x14F0 = 0` (campaign) exactly as NEW CAMPAIGN sets them, `call stub_pack`, `call 0x00401C08` (campaign runner), `jmp 0x0040513D` (the shared between-mission loop); rest NOP |
| NEW CAMPAIGN / TRAINING → campaign runner | `0x00405065`, `0x00405083` | `call 0x00401C08` → `call tramp_cw_campaign` |
| LOAD GAME (button id 2) | `0x004050BF` | `call 0x00403AA4` → `call tramp_cw_load`: always the Council Wars saves |
| SINGLE PLAYER WAR (button id 4) → **OZI LOAD** | `0x004050AB` | `call 0x00405AE4` (its only caller) → `call tramp_pack_load`: always the pack's saves |
| `stub_pack` | `0x0047F240` (`0x7E640`), 73 bytes | `push eax/edi`; four × (`mov edi,slot; mov eax,imm; stosd; mov eax,imm; stosd`) writing `ozi_ns/` / `ozisave`; `pop; ret` |
| `stub_cw_set` | `0x0047F290` (`0x7E690`), 73 bytes | same with `exp/` / `esave`; `pop; ret` (v1 of the patch tail-jumped into `0x00401C08` from here) |
| `tramp_cw_campaign` / `tramp_cw_load` / `tramp_pack_load` | `0x0047F2E0` / `0x0047F2F0` / `0x0047F300`, 10 bytes each | `call stub; jmp target` — targets `0x00401C08`, `0x00403AA4`, `0x00403AA4`; relative only, no relocations |
| `.reloc` block `0x5000` | | the two operands of the old handler (`0x004050DE` → `.bss`, `0x00405103` → `DGROUP`) become `IMAGE_REL_BASED_ABSOLUTE` padding |
| `.reloc` block `0x7F000` | file `0xA002C` | eight new HIGHLOW entries for the `mov edi,imm32` operands (`0x243 0x254 0x265 0x276 0x293 0x2A4 0x2B5 0x2C6`); the block grows `0xC0` → `0xD0`, every later block shifts by 16 bytes (52 bytes of slack in the section), the base-relocation directory size becomes `0x93DC` |

The other two callers of the campaign runner — mission continuation at `0x00405165` and the
post-load call at `0x00403B29` — stay direct, so the mode is *sticky* for everything that is not
a menu button: after OZI MISSIONS or OZI LOAD the game stays in pack mode (main menu included —
the overlay carries a copy of the 1024×768 `bintroe`) until NEW CAMPAIGN, TRAINING or LOAD GAME
is pressed. The two load buttons are deterministic: LOAD GAME lists `esave/` and continues the
Council Wars campaign, OZI LOAD lists `ozisave/` and continues the pack's; in-game saving goes to
the folder of the current mode, so a save is always listed by the button that can load it. At
start-up the slots hold their stock strings, i.e. Council Wars. Both stubs preserve `eax` (screen
object) and `edx` (game state), the arguments of `0x00401C08` and `0x00403AA4`; the handlers of
buttons 2 and 4 load them identically (`mov edx,eax; mov eax,[ebp-4]`).
Patched exe re-disassembled: the handler, both stubs and the two calls decode as intended, the
relocation table parses with 142 blocks and no other byte differs from the input.

**Data (`tools/build_ozi_overlay.py`, from the pack folder `OZI_NS/M1PACK` kept beside the game repository in `Documents` — local material, never committed (the game repo's `.gitignore` also blocks a copy inside it)):**

* base set `exp/`: `dalg`/`spyo`/`reae` FIN + SPR (the pack's new units — Reaper II also uses ten
  effect banks that Council Wars already has), three lines appended to `anim.dat`, the pack's
  `tran.fin` + `tran.spr` replacing the stock transport (a smoke animation and a real 42 KB sprite
  for the pack's "transmitter"/"Generator"; no Council Wars or Classic balance table has a TRAN
  row), and `textmsg 8` in `exp/intrface/bintroe` = `OZI MISSIONS`;
* overlay `ozi_ns/` (363 files, 23 MB): both campaigns' 22 mission sets, the pack's `gamestat/`
  (unit types 118–125, `unitid`, `mbullet` column 13, weapon tweaks; scene lists renamed
  `.kor` → `.txt` because our exe appends `.txt`, `0x00482204`, and their `frame x y` globe-marker
  lines shifted by (192,144) like the stock lists — third game test: the location circle sat at
  its 640×480 place), `sound/` (its `slist.dat`,
  ambience, the new WAVs), terrains `gatlan`/`gjungle`/`special` and the Council Wars terrains
  the pack copied (the fallback is the *root*, not `exp/`, so the overlay must be complete relative
  to the root — it is: the only `exp/` files invisible in pack mode are Council Wars' own missions,
  briefings and scene lists), the English story/credits texts, and copies of our 1024×768
  `bintroe`/`shumane`/`introe` in place of the pack's 640×480 screens; `ozisave/` is created.
* **Not merged:** `sound2.dat`. The pack overwrote slots 6, 15, 16, 17 with Dalgar voices and a
  commando weapon; 15 and 17 are the gun sounds of Council Wars units 39/55/56 and the table has
  no free slot (all 200 entries point at existing files), so in pack mode the Dalgar and commandos
  borrow gray-alien sounds. A later patch of the table size would lift this. The pack's
  `horn`/`pimp`/`snak` FIN variants turned out to be the Polish CW originals, not edits, so our
  English ones stay. Briefings: the pack's Polish speech stays out (decision), but the WAVs cannot
  simply be absent — the first game test (10 Sep 2026) ended on the story screen with "Please
  insert The Dark Colony Expansion Pak CD" and an exit. That is the wave loader `0x00452AB0`:
  after the prefixed and the bare name fail it builds the CD path (`0x00405E80`, `%c:\dc\`), and
  when that `OpenFile` fails too it logs `unable to open file`, tears the display down
  (`0x0042E310`, `0x00430928`), shows the prompt (`MessageBoxA`, string `0x00487DF8`) and exits
  (`0x0047C157`) — the CD-check patches never touched this path. So the overlay tool writes 22
  silent 0.1 s WAVs (`ozi_ns/mission/h1..h11.wav`, `g1..g11.wav`, stock briefing format 44.1 kHz
  stereo 16 bit), one per scene-list entry. The same rule holds for any other WAV a pack mission
  might name: missing in overlay *and* root means the CD prompt, not silence.
* **The local pool (second game test, 10 Sep 2026).** With the briefing WAVs in place the run
  ended one screen later in `error.log`: `SMalloc: Out of memory in local pool` / `assert failure,
  file smalloc.c line 97`. `smalloc.c` is a single arena created once at start-up —
  `mov eax,0AF79E0h; call 0x0040C0BC` at `0x00405319` (Classic `0x00405334`), 11 500 000 bytes —
  from which `SMalloc(pool, size, name)` (`0x0040C0FC`) hands out stack-like blocks (16-byte header,
  magic `1234ABCDh`, released back to a named mark by `0x0040C22C`/`0x0040C26C`). Its tenants,
  from the 96 call sites: every sprite bank the start-up animation list names (`0x0042538C`, ~7.2 MB
  of SPR cells in Council Wars, +0.74 MB with the pack's `dalg`/`spyo`/`reae`/`tran`), one
  framebuffer per interface screen ("Background memory" `0x0042C199` — 786 KB at 1024×768 instead
  of 307 KB), the mission's lightplane (`0x00453800`), tiles and remappings (`0x0045306B`),
  "kev: mapinfo" 633 KB, "kev/marc: coloursetup" 393 KB, "gifbuffer" 256 KB, "Gamestate" 291 KB,
  the flat maps, "Krusty AI", the widgets. So the 1024×768 build had already used most of the
  stock headroom and the pack's extra banks pushed the briefing screen over the edge. WAV data is
  *not* a tenant (`0x004529C0`: `GlobalAlloc`), so neither the 17 KB placeholders nor the pack's
  4.4 MB briefings mattered. Fix: `tools/patch_pool.py` sets the constant to 32 MiB
  (`0x02000000`) in **both** exes — one `imm32`, found by its byte pattern, no relocation involved;
  the size check is unsigned and block offsets are 32-bit, so nothing else depends on the value.

#### 10.14 Default game speed 150 % **(verified by disassembly; first game test showed the second site was needed)**

The simulation runs one tick every `gs->tick_ms` milliseconds (game state `+0x970`); the frame
loop at `0x0041E2FC` compares the elapsed time with it. Two values feed that field:

* the game-state initialiser (`0x0041BB50`, run at the start of every mission) sets it to
  **66 ms** at `0x0041BBFC` (`mov dword ptr [esi+970h],42h`; Council Wars `0x0041BC5C`), followed
  by the eight per-player `max_speed` slots at 33 ms (the engine's floor, 200 %);
* four **persistent settings** live in `DGROUP` globals — Classic `0x00488DE0..0x00488DEC`,
  Council Wars `0x00488E08..0x00488E14`, stock values `2,5,5,66` in both — which the main
  menu copies into the campaign object at `+0x1984..+0x1990` on entry (`0x00404E27`ff) and back on
  exit (`0x0040517E`ff; the options screen writes them there via `0x0040B38D`ff). The fourth is the
  desired tick length. The per-game start-up `0x0041EA43` copies it into `gs->desired_ms`
  (`+0x96C`, `0x0041EB29`), and the speed negotiation `0x00419830` — which runs in single player
  too — sends `TICK_SPEED(max(desired, slowest player))`, whose handler `0x0041DD6C` writes
  `tick_ms`. Patching only the initialiser is therefore undone within the first second of a
  mission: the first game test still showed 100 %.

The options screen (`intrface/lopt`, constructor `0x00432ECB`) shows `percent = 6600 / tick_ms`
rounded down to tens (`0x00432F10`; the slider runs 100..200 in steps of 10) and writes a change
as `TICK_DESSPEED(6600 / percent)` (`0x00432CD3`, plan F11). There is no settings file.

`tools/patch_speed.py` sets both dwords to **44 ms = 150 %** in both exes (sites found by byte
pattern: `C7 86 70 09 00 00 imm32`, and `A1 <global> 89 82 90 19 00 00` for the settings copy,
each unique in both builds). The slider keeps working, multiplayer is unaffected because the relay
server dictates `TICK_SPEED(33)` (plan R11), and a save game carries the speed it was saved with.
Applied to both repository exes on 10 Sep 2026.

* **Removed from the patcher and the published exes on 21 Sep 2026 (maintainer request "remove 150%
  speed patch from patcher"):** `dc16new.exe` / `engexp16new.exe` carry the stock 66 ms in both dwords
  again (default 100 %), `Apply-DarkColonyPatches.ps1` no longer offers `speed` (order now `nocd,
  resolution, hdpaths, cursor, pool, clock, ddraw, camera, restore` + `movies`/`sounds` or `ozi`), the
  game folder's 1280x800 builds were reverted in place with `patch_speed.py --percent 100`. The tool
  stays for anyone who wants the faster default. Published 1024x768 builds: Classic `36d3bada…`,
  Council Wars `57b28dbf…` (byte-identical rebuild from the originals).

#### 10.15 Three leftovers found in play: the battlefield dialogs, a black box at every screen change, and the clock hand **(verified by disassembly and pixel comparison; game test pending)**

Reported 13 Sep 2026 after playing the 1024×768 build: (1) the in-game pop-up dialogs (options,
objectives, quit, save/load) still sat at their 640×480 places, in the upper left of the enlarged
map view; (2) pressing a main-menu button (new campaign, load game, multiplayer) flashed a
**640×480 black box in the middle of the screen** before the next screen came up; (3) the
day/night dial on the panel showed its face, but the hand never moved although day and night
themselves changed.

##### The dialogs: the one class of script `pad_background.py` skipped

The four sub-window scripts `LOBJE`, `LOPTE`, `LQCE`, `LSGE` have no `background` and already
used the four-argument `size` (§10.1), so the tool left them alone. Their widget coordinates are
absolute like everyone else's, the `interface.c` dialog module positions nothing by code (§10.7),
and in stock they were centred on the *map view* (LOPTE spans x 112..420 around the view centre
260). The view grows symmetrically by (+384, +288), so shifting rect and widgets by half of that,
**(+192, +144)**, keeps them centred on it. `pad_background.py apply` now does this for every
script whose pristine copy has a four-argument `size` and no background (`scan_dialogs`, printed
as `dialog` lines, `.bak` and re-derivation as for the menus): 116 widgets per game, both games.

##### The black box: the `size` rect is erased when a window opens

`load_interface` (`widget.c`, keyword loop `0x004232E2`) ends by calling `window_draw`
`0x00422D84` (call at `0x00423A46`) with the background and palette names. `window_draw` copies
the window's bounds rect — the four dwords the `size` handler `0x004223A8` stored at window+0 —
over the context's clip rect, fills it with the `colour erase` RGB (`0x489530/34/38`) through
`[ctx+60h]`, restores the clip, flips (`[ctx+54h]`) **and only then** decodes the background GIF
(`[ctx+34h]`). At 640×480 the rect was the whole framebuffer, so this was the familiar black
screen between menus. The padded scripts said `size 192 144 640 480` (§10.1 step 2, §6), so the
erase became a 640×480 black box over the still-displayed previous screen, visible for as long as
the new screen took to load (the main menu behind it is a full-frame painting since §10.11, which
is why it showed).

Fix: **the rect of a full-screen script must stay the whole framebuffer.** `pad_background.py`
writes `size 0 0 1024 768` now (the four-argument form so its own re-derivation from `.bak` keeps
working; the game builds the same rect from either form) and only the widgets carry the (192,144)
offset. §6's "centring an existing screen is `size 192 144 640 480`" is thereby retracted: the
rect never positioned anything (§10.1), and it is not harmless either. 41 scripts changed
(20 Classic, 19 Council Wars root, `exp/intrface/shumane`, copied to `ozi_ns/intrface/shumane`
exactly as `build_ozi_overlay.py` copies it); the GIFs, bitmaps and scene files re-derived
byte-identically.

##### The clock hand: a code-drawn sprite anchored at (608, 450)

The dial is two things. The face is panel art in `INTRFACE.GIF`; the hand is a cell of
`sprites/cloc` (36 cells of 28×28: 18 for the day half, 18 for the night half) drawn by `clock.c`
(assert string `(cs.frame>=0)||(cs.frame<cs.clock.number)` at `0x48648C`), not by `MAINE`:

* `clock_init` `0x0043AAC0` (Council Wars +0x60) loads the bank (`0x50DE7C`), keeps the cell
  count (`0x50DE60`) and its half (`0x50DE78` = 18), and stores
  `ticks_per_cell = phase_length (gs+0x534) / 18` as a float at `0x50DE74`.
* `clock_draw` `0x0043AB38` (CW `0x0043AB98`) computes `cell = counter (gs+0x530) / ticks_per_cell`,
  `+18` at night (`gs+0x53C`), pulls the index back by one when the counter reaches the half, and
  whenever the cell differs from the last drawn one (`0x50DE72`) blits it through `[ctx+58h]` at
  `x = 0x260 − cell_w`, `y = 0x1C2 − cell_h`: the cell's **bottom-right corner is anchored at
  (608, 450)** — `0x0043ACB5` `B8 60 02 00 00`, `0x0043ACA2` `BA C2 01 00 00` (CW
  `0x0043AD15` / `0x0043AD02`). Neither number is 640 or 480, so the §8 sweep did not see them.

`hud_layout.py` slides the panel's bottom cluster by (+384, +288) (right_panel `insert=399`), and
a pixel comparison confirms the stock face at (580..608, 422..450) sits at **(964..992, 710..738)**
in both rebuilt `INTRFACE.GIF`s. So the hand had been drawn inside the map view all along and the
terrain painted over it every frame. `tools/patch_clock.py` (verify / plan / apply, `.clock.bak`;
pattern `BA imm32 66 8B 58 06 29 DA 89 D3 31 D2 66 8B 50 04 B8 imm32 29 D0`, unique in both
builds) sets the anchor to `(608 + W − 640, 450 + H − 480)` = **(992, 738)**: two dwords per exe
(Classic file `0x3A0A3` / `0x3A0B6`, DCEXP16 `0x3A103` / `0x3A116`), plain constants without
`.reloc` entries. Applied to both repository exes on 13 Sep 2026.

##### Follow-up from the game test: the PAUSED picture

All three confirmed in game the same day; the tester then noticed that the ESC/pause overlay was
still in the upper left of the view. It is not code-drawn: it is `MAINE`'s `picture 199` (cell
132 of `MAINBUT.SPR`, 123×137, declared `5 5` but the cell is drawn whole) at (200,160), i.e.
centred on the stock map view, toggled by the pause handler (`0x0040AF93` hides it while
`gs+0x46F51` is 0, `0x0040B33C` shows and redraws it when the flag is set) as the only member of
`group 201`. `hud_layout.py`'s `shift()` had a rule for the panel and one for the bottom bar and
left everything inside the view where it was; it now moves an in-view widget by half the growth,
so `maine apply` puts the picture at **(392, 304)** in both games. Game test pending.

### Stage 4 — cursors and movies

* Cursors are `IDirectDrawSurface` blits at 1:1, so they simply look small. Redrawing
  `CURSOR/cursor%d.bmp` at 1.6× is optional and independent.
* The flickering *Windows* pointer over the game (uninitialised class cursor, `WM_SETCURSOR`
  falling into `DefWindowProcA`) is fixed by `tools/patch_cursor.py` — §10.12.
* Movies: **done in §10.8** — the source is 320×180, the stock path is a fixed 2× software
  doubler that skips every other row, and the fix re-routes the frames through the existing
  320×180 movie surface and a stretching `Blt` to `(0,96)-(1024,672)`; the back-buffer clear
  now reads width/pitch/height from its own Lock. Ten sites. README goal 3 ("increase quality of
  movies") becomes easier after this: a higher-resolution re-encode would only need the movie
  surface size (`0x0040722B/30`) and the 1:1 writer's row length to follow `biWidth`/`biHeight`,
  which `draw_offscreen` already reads from the `BITMAPINFO`.

### Stage 5 — HUD reflow (data only)

No patching and no reverse engineering here: the HUD is `MAINE` + `INTRFACE.GIF` +
`MAINBUT.SPR` (§6.1). `MAINBUT.SPR` needs no change at all — the buttons are the same buttons.

`tools/hud_layout.py` is the scaffolding: `spec` prints the region geometry below, `extract` cuts
the frame into per-region layers plus tracing masks, `template` renders a target-resolution guide
to paint into, and `maine plan|apply|revert` does the script transform (75 right-panel widgets
`x += 384`, 6 bottom-bar widgets `y += 288`, `size 1024 768`; the one mid-map widget,
`picture #199` at (200,160), is reported and left alone). Verified idempotent, and `revert`
restores `MAINE` byte-for-byte.

#### 5.1 What actually has to be drawn **(verified by measurement)**

The frame is 8.9 % opaque and splits into five regions. Four of the five extend with no new
artwork, which was not obvious before measuring:

| Region | Source | Target | Grows | Extends how |
|---|---|---|---|---|
| `left_border` | (0,0) 4×480 | (0,0) 4×768 | +288 h | detail; only ~78 px periodic — pick a repeat segment or draw |
| `top_border` | (0,0) 640×6 | (0,0) 1024×6 | +384 w | detail; only ~77 px periodic — same |
| `map_edge` | (515,0) 3×480 | (899,0) 3×768 | +288 h | **constant** over y=94…399: tile one column, free |
| `right_panel` | (516,0) 124×480 | (900,0) 124×768 | +288 h | **287 rows carry only 6 px** of side rail (x=516,517 and 636…639): tile that row, free |
| `bottom_bar` | (0,454) 640×26 | (0,742) 1024×26 | +384 w | the message box interior (x 53…508) is 4 columns in random alternation: tile a stretch of it, free (§10.6 — the first reading, "needs real artwork", was wrong) |

So the drawing work is the bottom bar's extra 384 px, plus a chosen repeat or hand-work for the two
outer borders. The right panel's 288 px of new height and the map edge's come free — the panel
interior over that range is transparent, because the `MAINBUT.SPR` widgets are drawn over it.

1. **Vectorise before repainting.** Do not resample the 640×480 art up to 1024×768. Trace each
   element to vector (SVG) first, keep the vector as the master, and render the raster from it at
   the target size. Rendering from geometry is what keeps edges sharp — a 1.6× resample can only
   blur or blockify what is already there, whereas a traced bevel or border re-rendered at 1024×768
   is exactly as crisp as the original was at 640×480, and gradients and highlights come out
   smoother than the 8-bit source. It also turns this from a one-off repaint into an asset
   pipeline: commit the SVGs and any future resolution (1440p, 4K, or the 800×600 that §9 rejects
   for code reasons) is a re-render, not another manual repaint.

   **Which elements this pays off on.** `INTRFACE.GIF` is not uniform material — measured over its
   27 230 opaque pixels (80 distinct palette indices):

   | Measure | Value | Reading |
   |---|---|---|
   | mean horizontal run | 1.93 px (median 1, max 634) | mixed: long structural runs plus fine noise |
   | opaque px in runs ≥ 4 px | 43.4 % | the structural half — borders, bevels, panel outlines |
   | opaque px in runs of 1 px | 44.7 % | per-pixel shading detail |
   | pixels matching the one below | 32.5 % | little vertical coherence |
   | dither-pattern pixels | 6.1 % | deliberate 2-colour dithering |

   So split the work: **trace the structural 43 %** — the frame borders, the bevels, the rectangular
   panel outlines, and above all the map-view hole boundary, which is mostly long axis-aligned runs
   and vectorises essentially losslessly. Do **not** blindly trace the noisy interior fill; a
   general-purpose tracer turns 44.7 % single-pixel detail and 6.1 % dither into thousands of
   micro-paths that render worse than the original. Handle those regions as tiled/procedural
   texture, or with a pixel-art-aware upscaler, or by hand.

   **Tools.** Inkscape's *Trace Bitmap* with multi-colour scans, or its *Pixel art* mode
   (Kopf–Lischinski depixelization, `libdepixelize`) which is designed for exactly this kind of
   source; `potrace`/`autotrace` per colour plane for scripted runs. Snap traced geometry to the
   pixel grid so borders stay axis-aligned.

   **Two hard constraints on the render-back step.** (a) The output must be **8-bit indexed in the
   game's existing palette** (`MAINE` says `palette palette`, i.e. `PALETTE.GIF`/`.RGB`/`.RMP`) —
   `gifload.c` consumes indices directly and does no re-quantisation. (b) The erase colour must
   stay **exactly index 254** across the whole map-view hole: render the hole as a hard mask, with
   anti-aliasing and dithering **off** at its boundary. Any interpolated near-black pixel that is
   not index 254 stops being transparent and shows as a halo along the edge of the map view.

   **Sequencing.** Nothing is blocked. `INTRFACE.GIF` is a plain GIF, and `.SPR` is decoded with a
   working codec in `tools/spr.py` (§6.3), so traced cells can be re-imported into `MAINBUT.SPR` and
   the fonts as soon as the SVGs exist. Use `spr.py extract` → trace → render → `spr.py build`, and
   `spr.py check` to verify. Fonts are the strongest vectorisation candidate of all — glyphs are
   shapes, and a vector font makes every future resolution free.

2. **`INTRFACE/INTRFACE.GIF`** — render at 1024×768 from the vector master. The 8.9 % of it that is
   opaque has to move:
   the transparent hole becomes `(4,6)-(899,741)` (896×736), the left border stays at columns 0–3,
   the top border at rows 0–5, the map-view right edge moves from columns 515–517 to 899–901, the
   right screen border from 636–639 to 1020–1023, and the bottom bar from rows 454–479 to 742–767.
   The 124 px-wide panel column and the 26 px-tall bottom bar keep their pixel dimensions, so most
   of the existing artwork can be translated rather than redrawn — except that the panel column is
   now 768 px tall instead of 480 and needs 288 px of new filler between the widget groups.
3. **`INTRFACE/MAINE`** — shift the widget coordinates:
   * the 75 right-panel widgets (x `516…638`) → `x += 384`, giving x `900…1022`;
   * the 4 bottom-bar widgets (y `460…463`) → `y += 288`, giving y `748…751`;
   * the 3 message-line widgets over the map (`in_text #203`/`#204` at y 425/440, `picture #199`)
     → `y += 288` to stay the same distance above the bottom bar;
   * leave `size 640 480` → `size 1024 768`.
   That is a scripted edit over one text file, easy to verify by re-parsing and re-checking the
   zone counts.
4. The minimap is **not** in `MAINE` — it is the code patch in stage 3.
5. Optionally re-render the other 18 × 640×480 `INTRFACE/*.GIF` backgrounds at 1024×768 — same
   vector-first route as step 1, and easier there because they are full images with no transparency
   hole to preserve — then drop the `size 192 144 640 480` centring from stage 2.
6. Optionally redraw the fonts (`INTRFACE/FONT.SPR`, `MFONT*`) larger, from vector outlines per
   step 1 and via `tools/spr.py` (§6.3). `FONT.SPR` is 122 raw cells, 1…38 px wide by 28…29 tall —
   variable-width glyphs starting at `!`, with an alien head in place of `*`. The widget scripts
   carry per-font metrics, so a larger font is a data change plus a per-script `x y w h`
   adjustment.

Because stages 2 and 5 are pure data, they can be developed and tested against the **unpatched**
exe first (a 640×480 `MAINE` with shifted coordinates will simply look wrong, but it proves the
parse), which de-risks them completely.

### Stage 6 — parity and release

* ~~Apply every stage to `ENGEXP16.EXE` and diff-verify.~~ **Done in §10.10** (165 edits, three
  build fixups, `exp/` overrides handled by `pad_background.py`); game test by the maintainer
  pending.
* Council Wars `dc16.exe`: re-derive all offsets in Ghidra (only the §3 anchor transfers).
* The map editor (`maped_by_ozy_ns_v1.2PL.exe`) is a separate binary with its own 640×480
  assumptions and is **not** covered here.
* Commit each stage separately with a message describing the behaviour change, per the repo
  convention.

---

#### 10.16 Two monitors: the surfaces are lost right after the mode switch **(verified in game, 13 Sep 2026)**

Reported 13 Sep 2026: on a PC with two monitors the game "hangs" at start-up, black screen,
right before the intro movie. Reproduced on the maintainer's machine (primary 1920×1200 at (0,0),
secondary 1920×1080 at x=1920) and traced with a thread-stack scanner and a monitor-layout
watcher (both throw-away Python/ctypes scripts; no debugger was needed):

```
 0.00  start
 1.41  primary now 1024×768 — and the secondary has moved from x=1920 to x=1024
 3.43  foreground window = "Assertion Failed!" (#32770), error.log = 71 bytes
```

* `SetDisplayMode(1024,768,16)` in `win_init` makes Windows **re-lay out the desktop**: the
  secondary monitor is pushed left to the new right edge of the primary. That re-layout is a
  display change the game did not ask for, and DirectDraw answers it by marking every
  exclusive-mode surface **lost**. The loss arrives asynchronously, 0.5–2 s after the switch
  (it hit the loading screen's `Flip` in one run and the palette remap in the others). With a
  single monitor nothing moves, so nothing is lost — which is why nobody saw it in 1997 or in the
  eleven days of 1024×768 testing.
* The start-up path never restores surfaces. `remap` (`ddex4.c`, `0x0042F320`, ~line 1000–1036)
  converts the 256 palette entries to 16-bit pixels by `GetDC(back buffer)` → `SetPixel(0,0)` →
  `ReleaseDC` → `Lock` → read the pixel → `Unlock`, once per entry, and asserts on the first
  failure: `"POO can't unlock in remap"` line 1029 (`0x0042F394`), Lock line 1033 (`0x0042F3F5`),
  GetDC line 1036 (`0x0042F43E`). An instrumented build recorded the code: `Unlock` returned
  **`DDERR_SURFACELOST` 0x887601C2** while `GetDC` and `Lock` had still succeeded. The loading
  screen (`0x0042EEF6`) asserts `"Flip Problem"` (line 893, `0x0042F031`) when its `Flip` fails.
* The assert handler (`0x0047C02E` → `0x0047BEB7`) writes `error.log` and calls
  `MessageBoxA(NULL, msg, "Assertion Failed!", MB_TASKMODAL)` — the box is the foreground window
  but sits **behind the exclusive-mode primary surface**, so the player sees a black screen that
  reacts to nothing but Enter. That is the "hang"; the process exits with 255 when the box is
  dismissed.
* The game does restore lost surfaces, but only on its per-frame flip path: `0x0042E141`
  (`BltFast` offscreen → back buffer) and `0x0042E291` (`Flip`) test for `DDERR_SURFACELOST`
  and call `restore_surfaces` `0x0042E060` (Restore primary, offscreen, the 32 cursor surfaces
  with their bitmaps reloaded through `0x00450530`). `clear_screen` `0x0042F774` does the same.
  `IsLost` is never called anywhere; the window procedure does not track activation.

**Fix — `patch_ddraw_lost.py` (verify / plan / apply, `.ddraw.bak`, pattern-located, both
exes):** make the four start-up failures non-fatal and let the first in-game frame repair
everything, which is exactly what the game already does after an alt-tab. Four edits per exe
(Council Wars at +0x60), plus three `.reloc` entries turned into ABSOLUTE padding for the
overwritten `push imm32` operands:

| Site (Classic) | Stock | New | Effect |
|---|---|---|---|
| `0x0042F394` | `push "POO can't unlock in remap"` | `jmp 0x0042F486` | Unlock failed → next palette index |
| `0x0042F3F5` | `push fmt` (Lock assert) | `jmp 0x0042F486` | Lock failed → next index |
| `0x0042F43E` | `push fmt` (GetDC assert) | `jmp 0x0042F486` | GetDC failed → next index |
| `0x0042F033` | `je 0x0042F090` | `jmp 0x0042F090` | a failed loading-screen `Flip` is not fatal |

What is lost: the 16-bit LUT entries (`screen->palette + 0x602 + 2·i`) of the indices `remap`
could not read while the surface was gone — and `remap` runs again at every palette change, the
first one before the main menu is drawn, so nothing of it is ever visible. Confirmed in game by
the maintainer on the two-monitor machine: intro, menu and play as on one monitor.

**What did not work.** A first version restored the surfaces *inside* `remap` and retried the
index (a `try_restore` helper in the cave of the two dead assert blocks, calling
`restore_surfaces` up to 300 times with `PeekMessageA`/`Sleep(100)` between attempts, and the
loading screen redrawn from its `GetDC`). It got past the asserts, but the process then died
silently with exit code −1 four to nine seconds later, main thread inside `DDRAW.dll` in a
`WaitForMultipleObjects`; `error.log` stayed empty. Restoring in the middle of the loading
sequence upsets the modern DirectDraw emulation layer in a way the stock per-frame restore does
not, and the skip variant was confirmed working, so the restore variant was dropped. A fallback
for the unrecoverable case (Restore keeps failing) therefore does not exist either: those
asserts now simply do not fire at start-up.

The instrumented builds that pinned the error code and the stack scanner (Wow64 thread
contexts read with `ReadProcessMemory`, return addresses resolved against the DLL export tables)
are not kept; the technique is described in `CLAUDE.md`.

---

**Addendum, 3 Oct 2026 - a fifth site: the cursor colour-key Lock (maintainer: "error on game startup").** Two access violations at Ultimate `0x42EE50` (fault offset `0x2EE50`, Windows Application log) on a two-monitor desktop, `error.log` empty (the game truncates it at every start, a later good start erased the lines). The cursor set-up (`ddex4.c` 805-858) creates the 32 cursor surfaces (`cursor/cursor%d.bmp`, array `.bss 0x4DFE90`), sets their palette, then **Locks cursor surface 0** (`[vtable+0x64]`, `DDLOCK_WAIT`) to read its top-left pixel as the colour key for all 32 (`SetColorKey` `[vtable+0x74]`, `DDCKEY_SRCBLT`); a failure prints "Couldn't lock mouse" + the line-836 assert through the non-fatal `0x47C08E(eax=0)` and **continues into `mov ax,[lpSurface]`** with the `DDSURFACEDESC`'s `lpSurface` uninitialised - NULL or stale. A lost surface fails that Lock; the 13 Sep edits covered `remap` and the loading screen's Flip, both later in the start-up, not this one. Reproduced once with a driver's Alt+Tab 0.5 s after launch (the exact `error.log` text; that run survived on a readable stale pointer), not again in fourteen timed attempts - the window is the cold-cache first launch. **Fix = `ddraw` edit 5** (`patch_ddraw_lost.py`, Classic block `0x42ED8C`, Ultimate `0x42EDEC`, 92 bytes -> 51 + NOPs): `mov eax,[surf0]; lea edx,[ebp-5Eh]` (the desc's `ddpfPixelFormat`); `mov dword [edx],20h`; `GetPixelFormat` (`[vtable+0x54]`, answered for a lost surface - no SURFACE_LOST check in that method); `eax = dwGBitMask == 7E0h ? 69E0h : 35E0h` (the bitmaps' corner colour, palette entry 0 = RGB 109/60/0, truncated to 565 / 555 as GDI's DIB blit does - the same truncation §10.46 measured); `mov [ebp+72h],eax; mov [ebp+76h],eax; jmp` past the pixel read and the Unlock to the `SetColorKey` loop. The per-frame restore (`0x42E060` / Ultimate `0x42E0C0`) restores all 32 cursor surfaces and reloads their bitmaps, and a colour key belongs to the surface object, so nothing else is needed. `.reloc`: five of the block's six HIGHLOW entries -> type 0, the first (block +1) stays for the new `mov eax,[imm32]`; the tool anchors on the unchanged Lock call before the block (`6A 00 6A 01 8D 95 5A FF FF FF 52 A1 .. 6A 00 8B 18 50 FF 53 64 85 C0 74 5C`) so stock and patched exes both verify. References: Ultimate 1024x768 dark `5f8655ea…`, 1280x800 dark `58282faf…`, Classic 1024x768 dark `ae34e1d5…`. Checked: block disassembly, both stock exes, clean-copy patcher = references, seven starts of the fixed 1280x800 build (three under the provoked focus loss; the pointer draws cleanly afterwards), `error.log` empty. Open: a run in which the fallback itself executes. **Rule: every site that reads a surface after a Lock must handle the Lock's failure; the loss can come before ANY start-up step, not only the ones seen so far.** Rig: `loss_probe.py` (3 Oct scratchpad) launches an exe, Alt+Tabs away and back after D seconds and reports exit code, `error.log` and the Application log's fault offsets; another start-up site that tolerates the loss on its own showed up at 2.5 s (`avi.c` 775, "Restore AVI Surfaces ERROR", survived).

#### 10.17 Stock and 1024×768 data side by side: `INTRF_HD` and 30 path strings **(verified by disassembly and a data cross-check; game test pending)**

Maintainer request, 14 Sep 2026: the untouched originals `dc16original1998.exe` /
`engexp16original.exe` (committed 14 Sep, §"Two Classic builds" in `CLAUDE.md`) must run *out of
the box* from the same folder as the patched exes. Until now they could not: the 1024×768 menus,
the rebuilt HUD frame, the two loading bitmaps, the letterboxed briefing lists and the re-baked
logo animations had **replaced** the stock files under their stock names, so the original exe drew
1024×768 pictures into a 640×480 frame (top-left quarter, skewed GIF rows, §10.1).

**How the game names its interface files.** There is no `intrface/%s` format string. Every
screen, bitmap and list is opened through a *literal* path in DGROUP, 45 of them beginning with
`intrface/` and six with `gamestat/`:

* interface scripts: `load_interface` `0x004231E8` receives e.g. `"intrface/bintro"` (DGROUP
  `0x00482498`) and appends the language letter (`bintroe`); the `.DAT` beside a screen
  (`"intrface/intrg.dat"` `0x00482484`) lists the `ANIMATE/*.FIN` files of its gadgets;
* the loading screens `"intrface/load.bmp"` / `"intrface/load2.bmp"` (`0x004859CC` / `0x00485A74`,
  `driver.c`, `LoadImageA`, §10 stage 2);
* the briefing lists: `0x00403002`ff copies `"gamestat/hscene"` (`0x00482210`; `gscene`, `htscene`,
  `gtscene`, `hxscene`, `gxscene` follow at 16-byte steps) into a stack buffer and appends `.txt`;
* the background of a script is data in the script (`background intrface/intrg`), the `pictures`
  sheet too (`pictures intrface/knobe`);
* the logo animations: `INTRG.DAT` → `animate/dcuk.fin` (`0x00425613`) → the FIN's bank fields
  (`dcuk`, `dcut`: an 8-byte field in the header and in every 20-byte frame record, used as a **C
  string**) → `load_sprite_bank` `0x0042532C` formats `"sprites/%s"` (`0x00425356`) → `SPRITES/DCUK.SPR`.

**The rename (split_hd_data.py, both games).** Every file that differs from the stock revision
`0307feb` moved, under its stock name, into a new folder **`INTRF_HD/`** beside `INTRFACE/`; the
stock file came back in place (byte-identical to `0307feb`, checked with `git diff`):

| moved to `INTRF_HD/` | count | note |
|---|---|---|
| interface scripts | 28 | `BINTROE MAINE MULTIE LOPTE …`; their `background intrface/<x>` line became `background intrf_hd/<x>` when `<x>.GIF` moved too (all of them did); `pictures intrface/knobe|mainbut|popp` unchanged (those sheets did not change) |
| backgrounds | 18 GIF + `LOAD.BMP`, `LOAD2.BMP` | the only 1024×768 pictures |
| briefing lists | `HSCENE GSCENE HTSCENE GTSCENE .TXT` | from `GAMESTAT/`; markers +192/+144 (§10.7) |
| FIN lists | `INTRG.DAT`, `INTRO.DAT` | new copies that say `dcuk_hd.fin` / `dcut_hd.fin` / `dcss_hd.fin`; the other ten `.DAT` name no re-baked bank and stay single |
| Council Wars | `exp/intrf_hd/bintroe introe shumane hxscene.txt gxscene.txt` | the `exp/` overrides and the expansion's two lists; `ozi_ns/intrf_hd/` (same five, generated by `build_ozi_overlay.py`) |

The re-baked logo banks became **`SPRITES/DCSS_HD.SPR`, `DCUK_HD.SPR`, `DCUT_HD.SPR`** with
**`ANIMATE/DCSS_HD.FIN`, `DCUK_HD.FIN`, `DCUT_HD.FIN`** = the stock FINs with every bank field
`dcuk\0\0\0\0` → `dcuk_hd\0` (7 characters + NUL: the field is formatted as a C string, so 8
characters would overrun; 14 fields × 3 bytes in `DCUK.FIN`). The *animation* name (`DCUK`,
what `gadget 14 … DCUK` refers to) is unchanged, and only one of the two FIN sets is loaded per
process (`INTRG.DAT` is read per screen, not from `anim.dat`), so there is no "already have
animation" clash. Not carried over: `INTRFACE/DCSS.SPR DCUK.SPR DCUT.SPR` (byte copies nothing
reads, §10.11) and `INTRFACE/MULTIE~1.TXT` (a stray duplicate) — restored to stock only.
Seven of the moved scripts (`BUTTONSE DEMOWINE DINTROE INTROE LOGOE WINE WINUKE`) are never
named by the exe (`introe` was known dead since §10.11); they moved for consistency and cost
nothing.

**The exe patch (patch_hd_paths.py, id `hdpaths` in `Apply-DarkColonyPatches.ps1`).** Exactly the
30 strings whose files moved get their 8-byte directory part rewritten in place, `intrface` /
`gamestat` → **`intrf_hd`** (same length; `intrface`→`intrf_hd` differs in 3 bytes, `gamestat` in
8: 120 bytes per exe, no code change, no `.reloc` involvement — the strings' addresses do not move).
Council Wars' offsets are Classic +0x200 (DGROUP); the tool locates the strings by content.

| Classic file offset (DGROUP VA) | string | opens |
|---|---|---|
| `0x7F930` (`0x482130`) `0x7F94C` `0x7F9AC` | `intrface/newgame` `story` `encyclo` | `NEWGAMEE STORYE ENCYCLOE` |
| `0x7FA10`…`0x7FA6C` (`0x482210`…`0x48226C`) | `gamestat/hscene gscene htscene gtscene hxscene gxscene` | `INTRF_HD/*SCENE.TXT` (CW: `exp/intrf_hd/`) |
| `0x7FB08` `0x7FB4C` `0x7FB84` `0x7FC1C` | `intrface/shuman loadg wingame multiwn` | `SHUMANE LOADGE WINGAMEE MULTIWNE` |
| `0x7FC84` `0x7FC98` | `intrface/intrg.dat` `intrface/bintro` | main menu FIN list, `BINTROE` |
| `0x7FD38` `0x7FD4C` `0x7FD60` `0x7FD9C` `0x7FDD0` | `intrface/dpblank ipxname dplays getsvr netopt` | network screens |
| `0x80830` `0x80940` `0x809E0` `0x814FC` | `intrface/meta lost multi main` | lobby, defeat, lobby, **HUD `MAINE`** |
| `0x831CC` `0x83274` | `intrface/load.bmp` `load2.bmp` | loading screens |
| `0x83678` `0x836E8` `0x836F8` `0x83734` | `intrface/lsg lobj lqc lopt` | the four battlefield dialogs (§10.15) |

Everything else keeps its stock path and its single copy: fonts (`mfont*`), `hstory.txt` /
`astory.txt` / `credits.txt` / `encyclo.txt`, `mdl%c`, `epic`, the globe pictures, the other
`.DAT` lists, `sprites/%s` and `animate/%s` themselves, cursors, `MAINBUT.SPR`, `KNOBE.SPR`.
`intrface/encyclo` is only the script (`0x00402674` → `load_interface`; the `%s.txt` at
`0x004026DD` is applied to the unit name, not to this string), so `ENCYCLO.TXT` did not move.

**Council Wars.** The overlay helper `0x004063E4` (§10.13) prefixes the current mode string and
falls back to the game root, so `intrf_hd/bintro`+`e` resolves to `exp/intrf_hd/bintroe`
(Council Wars) or `ozi_ns/intrf_hd/bintroe` (pack mode) and its `background intrf_hd/intrg` to
the root `INTRF_HD/INTRG.GIF` — the same mechanism that served `exp/intrface` + root `INTRFACE`
before. The OZI menu rows (`build_ozi_overlay.py`) are applied to `exp/intrf_hd/bintroe` only;
the stock `exp/intrface/bintroe` keeps `PLAY INTRO`, which is right for the original exe (no OZI
mode). Pack-mode briefing lists are written to `ozi_ns/intrf_hd/hxscene.txt` / `gxscene.txt`.

**Result.** From one folder, `dc16original1998.exe` (CD checks intact; or an exe built with
`Apply-DarkColonyPatches.ps1 -Patches cdcheck` for a CD-less 640×480 game) reads `INTRFACE/`,
`GAMESTAT/`, `SPRITES/DCUK.SPR` and runs the stock 640×480 game; `dc16.exe` reads `INTRF_HD/`,
`SPRITES/DCUK_HD.SPR` and runs at 1024×768. Checks made: the 24 restored `INTRFACE` files, 4
lists and 3 banks per game are byte-identical to `0307feb`; every redirected string has its
target in `INTRF_HD` (or `exp/intrf_hd`); every `background` / `pictures` line of every HD script
resolves; no stock script mentions `intrf_hd`; every FIN named by the HD `.DAT` lists exists and
every bank it names has its `.SPR`; all HD GIFs are 1024×768, all stock GIFs ≤ 640×480; the
patcher rebuilds both exes byte-identically (pwsh and PowerShell 5.1) and `-Verify` reports
`hdpaths APPLIED`. **Open:** game test of both exes side by side (menus, HUD, briefing globe,
main-menu logos, OZI mode).

**First test (14 Sep 2026).** `dc16original1998.exe` ran; `engexp16original.exe` appeared to
hang when the main menu came up. `error.log`: `assert failure, file widget.c line 152
(ip->objects[i].type != unknown_obj)` — `widget_get` (DCEXP16 `0x00421CF8`: asserts `0 <= i < 300`
at line 151 and that widget `i` exists at line 152, message box hidden behind the full-screen
surface as in §10.16). The caller is the stock CD logic: the first CD check `0x00404F18` (the
`jne` at `0x00404F1F` = file `0x431F` of the `cdcheck` patch) greys widgets 0, 1, 0x10, 4, … through
`0x00424574` when the disc is missing, and the shipped Council Wars `exp/intrface/bintroe`
(byte-identical to the CD's `/EXPENG/EXP/INTRFACE/BINTROE`, and to `shumane`/`introe`) has
buttons 1, 3, 4, 5 `%`-commented out. Classic's script defines all of them, which is why the
Classic original runs without its disc and the Council Wars original does not — the retail game
behaves the same. (Reported again on 23 Sep 2026 and fixed in the data: the Council Wars script is
Classic's script now, **§10.35**.) A **CD-free 640×480 build** = original + `cdcheck` only (patcher `-Patches cdcheck`, 2 resp. 3
bytes; `-Verify` lists just `cdcheck`, `patch_hd_paths.py verify` says stock paths) was built and
ran, but is **not committed** — maintainer decision 14 Sep 2026: special builds come from the
patcher, the repository ships the originals and the fully patched exes only. Found on the way: the CD's `EXP/INTRFACE` also holds Council Wars' own 640×480
`INTRG.GIF` (23 866 B) and `INTRO.GIF` (26 061 B); the repository's `exp/intrface` (copied from an
installation, commit `e476eb4`) never had them, so the stock CW menu drew the Classic backdrop
from the root `INTRFACE`. Both were extracted into `exp/intrface/` (GIF layout checked); only the
640×480 exes see them (`intrf_hd/intrg` resolves to the root `INTRF_HD`). The `cdcheck`-only
Council Wars build confirmed running in game (14 Sep 2026); later the same day both untouched
originals ran with the Council Wars CD image mounted as `D:` (below).

**Second test and the rule "everything the original reads is original".** The maintainer wants
the untouched originals themselves to run, so two more things were settled:

* *The CD test, read out exactly* (`0x00405E8C`, result byte `0x004A49B8`, both builds): it reads
  the drive letter from `hbnfufl.a02` (`"D:"`), builds `%c:\dc\` (`0x00482654`), opens
  `D:\dc\anim.dat` (`"%sanim.dat"` `0x00482624`, mode `"r"`) and then tries to *create*
  `D:\dc\a<random>` (`"%sa%ld"`, mode `"w"`): read succeeds and write is refused → CD present.
  `widget_set_greyed` (`0x00424574`) ends in `widget_get` (`0x00424614` → `0x00421CF8`), which is
  the line-152 assert for the undefined button 1. The Council Wars CD image
  (`Documents/Dark Colony - The Council Wars.bin`, raw 2352-byte MODE1 sectors + audio tracks)
  was converted to `Dark Colony - The Council Wars.iso` (data track only, 251 486 sectors; the
  Classic image likewise to `Dark Colony.iso`) and mounted with `Mount-DiskImage`; it took `D:`,
  `D:\dc\anim.dat` exists, writes are refused — the original exe's test passes with it mounted.
* *A full inventory against the CD*: every file of the CD's `/EXPENG/` tree (246 files, the
  expansion set the installer lays over the Classic root) is byte-identical in the repository
  except `HBNFUFL.A02` (installer-written drive letter) and three files the OZI base set had
  replaced: `exp/anim.dat` (+3 lines), `exp/animate/tran.fin`, `exp/sprites/tran.spr` (the pack's
  transport). The CD's `/DC/` tree differs only where the expansion installer overwrites Classic
  files (`MAINE`, fonts, `CURS.SPR`, `LOAD*.BMP` — all identical to `/EXPENG/`'s copies) and in
  game-written `.OVH`/`ERROR.LOG`; the Classic folder differs from the Classic CD only in
  game-written `.OVH` caches and one `.TRO`. So the three `exp/` files were restored from the CD
  and the OZI base set moved to new names: **`exp/animozi.dat`** (stock list, `tran.fin` →
  `tranozi.fin`, plus `dalg.fin spyo.fin reae.fin`), **`exp/animate/tranozi.fin`** (pack FIN, 42
  bank fields `tran` → `tranozi`, 7 chars; banks `glat glit ssss smsp` unchanged),
  **`exp/sprites/tranozi.spr`**. The start-up reader `0x004051CC` opens the list through the
  overlay helper as the literal `anim.dat` (DGROUP `0x004824B8`, mode `"rt"` at `0x004824B4`; 9
  bytes + 3 slack before `"w"` at `0x004824C4`), so `patch_ozi_menu.py` now also writes
  `animozi.dat\0` there (`DGROUP_SITES`; 12 bytes, the `ozi` patch grows to 14 edits) and
  `build_ozi_overlay.py` produces the three files and warns when the stock trio is the pack
  version. Checked: every FIN named by both lists exists and every bank has its `.SPR`; the CD
  `/EXPENG/` comparison now lists only `HBNFUFL.A02`; the patcher rebuilds `DCEXP16.EXE`
  byte-identically. Game test of the OZI mode after the rename pending.

Tools: `tools/split_hd_data.py plan|apply GAME --stock STOCK` (the stock tree from
`git archive 0307feb …`; idempotent), `tools/patch_hd_paths.py verify|plan|apply EXE`,
`build_ozi_overlay.py` (writes `ozi_ns/intrf_hd/`), `pad_background.py` accepts an `INTRF_HD`
folder (GIFs looked up in the sibling `INTRFACE` as a fallback, lists inside the folder). Rebuild
workflow from now on: pad/paint a **copy** of the stock game folder in place as before, then
`split_hd_data.py` moves what differs into `INTRF_HD` and retargets the `background` lines.

#### 10.18 One game folder: Classic lives in `DC - Council wars/` **(15 Sep 2026, maintainer decision; Classic original confirmed running there)**

The Council Wars root has always been the complete Classic data set (the expansion is the `exp/`
overlay), so the maintainer decided to retire the `DC - Classic/` folder. What changed:

* **The untouched Classic exe is `DC - Council wars/dc16.exe`** — the 7 Jan 1998 build restored from
  the game repository's first content commit (`02d62b2`, which shipped this very file in the Council
  Wars folder as well; md5 `8fc93346…`, byte-identical to the former `DC - Classic/dc16original1998.exe`).
  Its patched build is **`dc16new.exe`** beside it (`OutputName` of the Classic build in
  `gen_apply_script.py`; the patcher rebuilds it byte-identically from `dc16.exe`, SHA-256 `0e9297c2…`,
  checked under pwsh and PowerShell 5.1). The Council Wars pair was renamed the same day, the same
  way: the untouched exe is back to its CD name **`ENGEXP16.EXE`** (was `engexp16original.exe`) and the
  patched build is **`engexp16new.exe`** (was `DCEXP16.EXE` since 10 Sep 2026; earlier sections say
  `DCEXP16` for it and its `+0x60` addresses). `Requires`/`Data` of the Classic fixes are now enumerated from the Council Wars folder
  (60 files: `INTRF_HD\` 54, `SPRITES\` 3, `ANIMATE\` 3).
* **Launched from that folder, the untouched Classic exe runs** (mode switch to 640×480, `error.log`
  empty, exit 0 on quit) as long as a CD image with a `/DC/` tree is mounted on the drive named in
  `HBNFUFL.A01` (`D:`; the Council Wars image has `/DC/ANIM.DAT`). Without it: "Please insert The
  Dark Colony CD and Restart". The CD test needs `D:\dc\anim.dat` to open and `D:\dc\a<n>` to be
  uncreatable (§10.17).
* **Data copied from Classic** (every Classic file now has a byte-identical copy in the Council Wars
  folder, checked by hashing both trees): `MISSION/` (30 briefing WAVs `H1..H15`, `G1..G15`, opened as
  `mission/h%d` — without them a campaign mission made the wave loader fall back to the CD path and
  quit), `ENCYCLO/` (75 files), `WALLPAPR/` (13 BMPs, not read by the exe), `SCENARIO/MULTI-~1/` (30
  files: an older revision of seven jungle multiplayer maps, every `.MAP`/`.PTH`/`.SCN` differs from
  `MPLAYER/`; never opened by the game, kept for the record), `SCENARIO/MPLAYER/PMAP.EXE` +
  `PALETTE.GIF/RGB/RMP` + `PRIMES.DAT` + `J8PLAY07.MED`, `SCENARIO/ALL.JUS`, `VENT.JUS`, `DESERT.SET`,
  `JUNGLE.SET` (map-editor files, no string for them in the exe), `DC16.ICO`, `ICON*.RC/RES`,
  `SOUND/BEAT.WAV` (unreferenced; `SOUND2.DAT` names `BEAT2.WAV`), `INTRFACE/MULTIE~1.TXT`.
* **Movies whose names clash**: Council Wars has its own `INTRO.AVI`, `AENDING.AVI`, `HENDING.AVI`,
  so the Classic ones were added as **`AVI/DCINTRO.AVI`** (29.3 MB), **`DCAENDING.AVI`** (18.6 MB),
  **`DCHENDING.AVI`** (20.5 MB). **Patch `movies`** (`patch_movies.py`, same day, Dark Colony only)
  makes `dc16new.exe` play them. Only the intro is named in the exe: DGROUP `intro.avi` at
  `0x004824A8` (file `0x7FCA8`), used by the start-up path `0x004053A7` and the PLAY INTRO handler
  `0x004050FE` (`mov esi,4824A8h`), appended to `avi/` at `0x00482464`. Watcom aligned the next string
  (`rt`, the fopen mode, at `0x004824B4`) to 4 bytes, so `intro.avi\0` + two padding zeros = 12 bytes
  = `dcintro.avi\0`: rewritten in place, same address, no `.reloc` change (one 12-byte edit). The
  endings are **data**: line 154 of the campaign lists `HSCENE.TXT` / `GSCENE.TXT` (`avi/hending.avi`
  / `avi/aending.avi`, right after the last mission's `scenario/human/human15`), and the patched exe
  reads those lists from `INTRF_HD/` (§10.17), where they now say `avi/dchending.avi` /
  `avi/dcaending.avi`; the stock `GAMESTAT/` lists (read by the untouched exe, which therefore plays
  Council Wars' movies in this folder) are unchanged. The tool refuses `DCEXP16.EXE` (its
  `intro.avi` is the Council Wars intro) and edits the two lists only when `INTRF_HD/` exists beside
  the exe, so the patcher generator's exe-only replay is unaffected. `dc16new.exe` SHA-256
  `572646e4…` since. **Game test pending.** `split_hd_data.py` regenerates the INTRF_HD lists from
  stock, so re-run `patch_movies.py apply` after any HD-data rebuild.
* **Map editor** (15 Sep 2026): the Council Wars CD carries no editor at all; the Classic CD (`DCUK`)
  has `DC\MAPED.EXE`, byte-identical to the repository's `Dark Colony - Map editor/maped.exe`, and an
  `EDITOR\` folder that is just its InstallShield kit (`DATA.Z` 2.5 MB; the repository folder is the
  installed result: `BWCC.DLL`, `BWCC32.DLL`, `CW3215MT.DLL`, `readme.doc`). The editor's own
  `scenario/` mirror is content-identical to the game's `SCENARIO/`. The files were copied into the
  Council Wars folder and removed again the same day at the maintainer's request: the editor stays in
  `Dark Colony - Map editor/`. Both CDs, for the record, carry `CVS/` folders in every data directory
  (developer leftovers).
* **Name caveat (found in the smoke test, 15 Sep 2026):** the patched build was first called
  `dc16patched.exe`. It starts (mode switch to 1024×768, `error.log` empty), but Windows'
  installer-detection heuristic treats a manifest-less 32-bit exe whose file name contains "patch"
  (also "setup", "update", "install") as a setup program: every launch showed a UAC prompt and the game
  ran elevated (`Start-Process` reported "The operation was canceled by the user" when the prompt was
  declined, and the running game could not be ended from a non-elevated process). The exe has no
  manifest resource (`.rsrc` holds only the icon). The maintainer renamed it **`dc16new.exe`** the same
  day; never use one of those words in a game exe name.
* **Patcher resource check (15 Sep 2026, maintainer request):** `Apply-DarkColonyPatches.ps1` checks
  every fix's `Data` files against the folder the exe is written to (`Get-UnavailableFixes`). A fix
  whose files are missing, or which `Requires` such a fix (propagated until stable — `resolution` and
  `hdpaths` require each other), is labelled `[RESOURCES NOT FOUND]` in the window, cannot be ticked
  (the `ItemCheck` handler cancels the tick and the log line names the missing group), is skipped by
  "Select all", and its panel opens with the reason; the check re-runs whenever the "Write to" path
  changes. On the command line `-All` applies the available fixes and prints `skipping […] RESOURCES
  NOT FOUND`; an explicit `-Patches` list naming an unavailable fix is still refused unless
  `-IgnoreMissingData`. The resources are checked as separate groups: the interface files (60:
  `INTRF_HD\` 54, `SPRITES\*_HD.SPR` 3, `ANIMATE\*_HD.FIN` 3) belong to `resolution`/`hdpaths`, the
  three `AVI\DC*.AVI` to `movies` (the two INTRF_HD campaign lists it relies on are interface files),
  the `ozi_ns` overlay + `exp/animozi.dat` etc. to `ozi`.
* **`SCENARIO/HUMAN/HUMAN09.TRO` fixed**: the Council Wars copy (from the "real installation" commit
  `e476eb4`) had `(b(1,3)&&==0)` in mission 9's trigger condition; the Classic copy has `(b(1,3)==0)`
  and replaced it.
* **Tools and tests**: `test/engine-anim.test.js`, `test/engine-tables.test.js`, `test/map2json.test.js`
  read `DC - Council wars` (env `DC_CLASSIC_DIR` overrides); `data/classic/gamestat.json` /
  `sprites.json` regenerated from it (only the recorded `source` folder name changed). Not copied:
  the Classic `HBNFUFL.A01` (`d`; the Council Wars one says `D:`) and the untracked `.bak`
  intermediates. `data/dc16-tables.json` / `dc16-vision.json` keep their historical "read from
  `DC - Classic/dc16.exe`" note (the exe bytes are those of `dc16new.exe`).

#### 10.19 No CD-drive access: the probe behind "requests the CD and hangs" **(18 Sep 2026, maintainer report; traced in both exes from the disassembly; patched exes smoke-tested; the not-ready-drive case itself could not be reproduced here — the development PC has no drive letter D:)**

* **Report (18 Sep 2026):** "all Dark Colony executables are requesting CD and hanging on multi
  hard drive systems" — the patched builds included.
* **What `cdcheck` did and did not do.** The three `cdcheck` bytes (§"Patches applied so far" in
  `CLAUDE.md`) flip the conditional jumps *after* the CD test at the two menu sites
  (`0x404F18`/`0x405C98` Classic, `0x404F18`/`0x405C78` Council Wars) and invert the CRT-level
  `jne` at Council Wars file `0x781D9`. The test itself was untouched and every build still ran it:
  - **Start-up** (`safefunc.c`, `0x405F88` Classic / `0x405F68` Council Wars, called from the
    initialiser `0x405311`): clears the 16 remap slots `0x4A4730`, `fopen("hbnfufl.a01")`
    (Classic; Council Wars `hbnfufl.a02`, string `0x482644` in both) — a missing file is
    `assert failure, file safefunc.c line 137` into `error.log` and `exit(0)` — reads the first
    character (`fgetc`), `sprintf(cdpath 0x4A48B0, "%c:\dc\" 0x482654, c)`, then probes the
    game folder for `anim.dat` (`0x48265C`) and for a file named `full` (`0x482668`: present →
    byte `0x488DF5` / `0x488E1D` "not a full install" is cleared; the repository folder has
    `FULL`), and finally `call cd_probe` (`0x406074` / `0x406054`).
  - **`cd_probe`** (`0x405EAC` Classic / `0x405E8C` Council Wars): `fopen("%sanim.dat", cdpath)`;
    failure → flag `0x4A49B8` := `[0x488DF4]`/`[0x488E1C]` (0); success → `fclose`,
    `rand() % 100000`, `fopen("%sa%ld", "w")`; failure to create → flag 1 (a medium that refuses
    writes = the CD); success → `fwrite("foo",1,1)`, 0 written → flag 1, else flag 0; the temp
    file `D:\dc\a<n>` is never deleted (a writable `D:\dc\` collects them). The flag is read
    through the getter `0x405E8C` / `0x405E6C` by the menu tests, by the `"rb"` opener `0x401028`
    (`0x401071`: falls back to `cdpath+name` only when the flag is set), by `0x410A10` / `0x410A70`
    and by the **in-game check** `0x411386..0x4113A1` / `0x4113E6..0x411401`, which calls the probe
    again (`0x41138D` / `0x4113ED`) and compares the flag before and after (the stock "CD removed
    during play" test).
  - **`load_interface`** (`0x4231E8` / `0x423248`) calls the probe at `0x423223` / `0x423283` —
    **every menu screen** re-opens `D:\dc\anim.dat`.
  - **Missing-file fallbacks:** the generic open helper (`0x406253`, when the flag or the "not
    full" byte is set: `strcpy(cdpath); strcat(name)`) and the wave loader (`0x452AEF` /
    `0x452B5B`, through the cdpath getter `0x405EA0` / `0x405E80`, unconditionally when both
    local names fail; then `unable to open file %s` into `error.log`, DirectDraw released
    (`0x42E2B0`), `Sleep(2000)`, `MessageBoxA("Please insert The Dark Colony CD and Restart"` /
    Council Wars `"…Expansion Pak CD - The Council Wars…"`, `"CDROM NOT FOUND")`, `exit`).
  - The import table has **no `SetErrorMode`** and no `GetDriveTypeA`: the game trusts the
    installer's letter blindly and never asks Windows to fail a not-ready drive quietly.
* **Why that is a hang on "multi hard drive systems" (inferred, not reproduced):** `HBNFUFL.A0x`
  says `D:`. When `D:` is a hard disk with no `\dc\anim.dat` the `fopen` fails at once and nothing
  is visible — the single-drive case everyone tested. When `D:` is a **drive that is not ready** —
  a card reader or a USB/optical drive without a medium (very common on multi-drive PCs), an
  unplugged removable disk — `CreateFile` raises Windows' hard error, and because the process
  error mode is the default the OS shows *"Windows - No Disk: There is no disk in the drive.
  Please insert a disk into drive \Device\Harddisk1\DR1"* — modal, behind the exclusive
  full-screen surface: black screen, a "hang", and a message that reads like a CD request. When
  `D:` is a **second hard disk that has spun down** (Windows' default power plan parks idle disks
  after 20 min) every probe waits for the spin-up: several seconds of freeze at start-up, again at
  every menu screen, again in game. All three go through the same `fopen("D:\dc\…")`.
* **Fix = `tools/patch_nocd.py`** (verify / plan / apply, `.nocd.bak`, pattern-located, both
  exes), patcher fix **`cddrive`**. A first version (same morning, two bytes: probe → `ret`,
  first byte of the format string → NUL) stopped the drive access but left the CD logic in place
  (HBNFUFL still read, the path still built, the fallbacks still "trying" an empty CD path, the
  disc still requested); the maintainer rejected that the same day — **"the game should not try
  to touch the CD path at all"** — and the fix became **nine edits + two `.reloc` entries per exe**
  (92 / 124 bytes), all inside existing instructions and strings, nothing moves:
  1. start-up `0x405FB3` / `0x405F93` (file `0x53B3` / `0x5393`): `mov edx,"r"; mov eax,"hbnfufl.a0x"`
     (10 bytes) → `jmp 0x406054` / `0x406034` (`E9 9C 00 00 00` + 5 NOP) straight to the `full`
     marker check: HBNFUFL is never opened, no letter read, no `sprintf`, no probe. **The hop is
     +0x9C, too far for a short `jmp`** — the first attempt used `EB 9F`, which is a *signed* short
     jump of −97 into `cd_probe`; both patched exes died with exit −1 in the smoke test and the
     tool was corrected. The two absolute operands vanished, so their HIGHLOW `.reloc` entries
     (file `0x97A8A`/`0x97A8C` = `3FB4`/`3FB9`; CW `0x97C88`/`0x97C8A` = `3F94`/`3F99`) became type 0.
  2. start-up `call cd_probe` `0x406074` / `0x406054` → 5 NOP.
  3. `cd_probe` entry `0x405EAC` / `0x405E8C`: `push ebx` → `ret`, for the two callers that remain
     (`load_interface`, in-game check). The flag `0x4A49B8` is `.bss`, stays 0.
  4. DGROUP `"%c:\dc\"` `0x482654` (file `0x7FE54` / `0x80054`): 8 zero bytes (dead data).
  5. open helper `0x4063DF` / `0x4063BF`: `je <try cdpath+name>` (`0F 84 6E FE FF FF`) → `jmp
     0x4062DD` (`E9 F9 FE FF FF 90`), the ordinary "missing file" exit (quiet NULL or the
     `FILE Error opening file` assert, as before).
  6. wave loader `0x452AE9` / `0x452B49`: `jne 0x452BC6` (`0F 85 D7 00 00 00`) → `jmp` (`E9 D8 00 00
     00 90`): after the two local `OpenFile`s the loader goes to its error exit; `esi` already
     holds the handle or −1, so the found case is unchanged.
  7. movie opener `0x401078`: `je 0x4010DD` (`74 63`) → `jmp` (`EB 63`): never the CD branch.
  8. DGROUP `"CDROM NOT FOUND"` → `"FILE NOT FOUND"` (16 bytes) and `"Please insert The Dark
     Colony CD and Restart"` (CW: `"…Expansion Pak CD - The Council Wars and Restart"`) → `"A sound
     file is missing - see error.log"`, NUL-padded to 45 / 78 bytes: the box the wave loader
     shows for a missing WAV tells the truth.
  The patched exes **no longer read `HBNFUFL.A01`/`.A02`** (the originals still do; the files stay
  in the repo for them). In the patcher the fix is applied **right after `resolution`**
  (`cdcheck, resolution, cddrive, hdpaths, …`), because `patch_resolution.py` identifies its input
  by MD5 and expects exactly the `cdcheck`-fixed exe. Both repository exes re-patched (SHA-256
  `dc16new.exe` `c54f434f…`, `engexp16new.exe` `13c95489…`), `Apply-DarkColonyPatches.ps1`
  regenerated (`-Verify` reports both as the fully patched executables), `dc16.asm` /
  `dcexp16.asm` regenerated (all four jumps land on their targets).
* **Tests (18 Sep 2026):** `subst` drives `D:` `E:` `F:` (empty folders) and `G:` = the Dark-Colony
  repository, game run from `G:\DC - Council wars`, a copy of `anim.dat` in `D:\dc\` so the probe's
  write test becomes visible. Stock `dc16.exe`: alive after 14 s and **two files `D:\dc\a<n>`
  (1 byte each) appeared** — the probe ran at start-up and again at the first menu screen and
  wrote to the "hard disk" `D:`. `dc16new.exe` and `engexp16new.exe`: alive after 14 s, `ERROR.LOG`
  empty, **no file on `D:`**; the same two **with `HBNFUFL.A01`/`.A02` renamed away: alive after
  14 s, `ERROR.LOG` empty** (the stock build exits through the `safefunc.c` line-137 assert without
  them). A not-ready drive (no medium) cannot be built with `subst`; that case rests on the trace.
* **Left as it was:** the in-game check `0x411386`ff and `0x410A10` still *read* the flag (always
  0, no file access); the stock leak of `D:\dc\a<n>` temp files on a writable `D:\dc\` is moot.
* **One CD fix (same day, maintainer requirement "the patcher should contain only one CD fix"):**
  the patcher's `cdcheck` (the three hand-patched 2025 bytes — `0x404F1F`, `0x405C9F` / `0x405C7F`
  jne→jmp, Council Wars `0x478DD9` jne→je — which the generator used to apply itself) and `cddrive`
  are merged into **`nocd`**, produced entirely by `patch_nocd.py` (the 2025 bytes are
  pattern-located sites of the tool; 13 / 14 edits incl. the two `.reloc` entries), first in the
  order. `patch_resolution.py identify()` accepts the input by size (659456 / 659968) when the MD5
  is not the 2025 CD-fixed one. Exe bytes unchanged (same SHA-256); `-Patches nocd` alone builds the
  CD-free 640x480 exe (SHA-256 `e64c215d…` for Classic).
* **`HBNFUFL.A01` / `.A02` for the untouched originals:** the file is what the installer wrote — the
  first character is the **letter of the drive that holds the CD** (or a mounted CD image with a
  `/DC/` tree: `D:\dc\anim.dat` must exist and the drive must refuse to create `D:\dc\a<n>`), the
  rest (`:\r\n\x1a`) is ignored; `fgetc` reads one byte, case does not matter. The repository keeps
  `D:` because the maintainer's CD image mounts as `D:`. If the CD drive has another letter, edit
  the first byte; a letter that does not exist makes the probe fail instantly and the original then
  behaves as "no CD" (greyed menu, Council Wars asserts in `widget.c`) — the patched exes ignore the
  file entirely.

* **The "Please insert Dark Colony CD" box (21 Sep 2026, maintainer report: a player "still sees
  Please insert Dark Colony CD" — two screenshots of the OZI human campaign introduction with a cyan
  box over the story text):** that text is not a string but the sprite **`intrface/insee`**
  (`INTRFACE/INSEE.SPR`, the "insert CD" picture). The file-open helper `0x4061DC` (`safefunc.c`)
  does not fail when a *required* file (`bl` = 1) is missing: with the display up (`0x488DF8` ≠ 0 and
  byte `0x488DF4` = 0) it calls the display object's **CD-prompt method, slot `+0x4C` = `0x42C0BC` /
  CW `0x42C11C`** (`0x40630D` / `0x4062ED` is the only caller — the other `call [reg+4Ch]` sites go
  through C++-style vtables of other classes; the slot is set at `0x42C3E5`), which allocates "CD
  Prompt Sprite Memory", saves the background, draws the sprite at (235,220) and **loops on
  `fopen(name,"r")` until the file appears** (`0x42C1EA`) — the stock game's "insert the disc" wait.
  With edit 5 the loop no longer tries the CD path, so a missing required file was a hang behind a CD
  request that named no file. The player's file was **`intrf_hd/hxscene.txt`**: `ozi_ns/intrf_hd/`
  (the five OZI files of §10.17 — `bintroe`, `introe`, `shumane`, `hxscene.txt`, `gxscene.txt`) had
  **never been committed** (the unanchored `.gitignore` rule `OZI_NS/` hid the folder until 21 Sep
  2026, and the anchoring commit did not add it), so a clone running the published `engexp16new.exe`
  in OZI MISSIONS mode found neither `ozi_ns/intrf_hd/hxscene.txt` nor a root `intrf_hd/hxscene.txt`
  when NEXT on the story screen loaded the scene list. Two fixes: (1) the five files are committed
  (1024x768: the three scripts = `exp/intrf_hd/*`, the two lists = `ozi_ns/gamestat/*` with the globe
  markers +192,+144 — byte-identical to what the patcher's `Write-InterfaceSet` writes); (2) **`nocd`
  edit 9** (`patch_nocd.py`, both exes; patcher fix `nocd` now 18 / 19 edits): the first 68 bytes of
  the CD-prompt method (up to and including its second `mov eax,"intrface/insee"`) become the wave
  loader's error exit (`0x452BD1` / `0x452C31`): `fprintf(error.log, "unable to open file %s\n",
  name)`, flush, display shutdown `0x42E2B0`, `Sleep(2000)`, `MessageBoxA(hwnd [0x489730], name,
  "FILE NOT FOUND", MB_OK)` through the import thunk `0x47EFB4`, `exit(0)` — the rest of the old body
  is dead, nothing moves; the four absolute operands take over the `.reloc` entries `0x0D8 / 0x0DD /
  0x0FC / 0x132` of page `0x42C000` (CW `0x138 / 0x13D / 0x15C / 0x192`, page `0x42C000`), the fifth
  (`0x1EB` / `0x24B`, an operand in the dead rest) stays. Every target is read from the wave loader's
  own sequence, so one pattern serves both builds; the tool upgrades the 18 Sep form in place.
  **Confirmed in game 21 Sep 2026** (1280x800 `engexp16new.exe`, `ozi_ns/intrf_hd` renamed away,
  driven by a ctypes probe posting the clicks: OZI MISSIONS → HUMAN → START CAMPAIGN → NEXT): the box
  "FILE NOT FOUND / intrf_hd/hxscene.txt" 2 s after the click, `error.log` = `unable to open file
  intrf_hd/hxscene.txt`, OK → exit code 0. Published 1024x768 builds now SHA-256 Classic
  `49abd4e3…`, Council Wars `97eaaf01…` (patcher regenerated; rebuilt from the originals in a clean
  checkout incl. the five OZI files, byte-identical; `-Verify` reports the 18 Sep exes as `nocd`
  MIXED 13/14 of 18/19); the game folder's 1280x800 builds are `14ed298e…` / `6fa9a043…`.
  Rule for reports: **"Please insert Dark Colony CD" on a CD-free build meant a missing file**; since
  edit 9 the box names it.

#### 10.20 "Units miss the spot I clicked" on the 1024×768 build: the click chain audited **(19 Sep 2026, player report via the maintainer; audited in the disassembly only — no defect found, not reproduced)**

A player reported (more than ten times in one online game) that orders sent to one spot made
units go elsewhere, once losing an Exploiter ordered onto a Petra-7 vent, and believed the
1024×768 executable to be the cause. Every step from the mouse to the order was read in the
patched Classic exe; all of it is consistent with the 896×736 map view. Nothing here is a fix —
it is the list of what is **not** wrong, so the next report can be narrowed further.

| Step | Where | What it does in the patched exe |
|---|---|---|
| mouse position | `mouse.c 0x00450E80` (the only live path; the absolute path `0x00450DA0` has no caller) | DirectInput relative deltas accumulated into `0x005327C0/C4`, clamped to 0…1023 / 0…767, mirrored to `0x004DFF14/1C` |
| button events | `0x00450FA0…0x004510B9` → `push_event 0x0042F110` | polled once per frame from the button *state*: type 4 = left press (flags 2/4/8 = Shift/Ctrl/Alt in byte 1), `0x104` = left release, 3 / `0x103` = right press / release, 5 = move; queue `0x004DF290`, 256 × {type, x, y} dwords — nothing is packed into 640×480-sized fields |
| dispatch | `0x0040A484` | a captured HUD widget (`0x004245C0`, `[intf+0x431C] ≥ 0`) first, then `point_in_rect` on the map view `[ui+8]` = (4, 6, 896, 736) → `0x00409AC4`, then the minimap rect `ui+0x7B4` = (903, 6, 96, 84) → `0x0040A070` |
| left click with a selection | `0x00409AC4`: press `0x00409E72`, release `0x00409EAC`, move `0x00409F5F` | the press is **deferred**: its event is stored at `ui+0x4680…0x4688` with the time at `ui+0x4678`; a move of more than 45 px (`0x00409F9A`) or 1.5 s (`0x00409FAD`) turns it into a box select; the release calls `0x004098D4`, which converts the **stored press position** with the **camera of the release moment** |
| screen → world | `0x00409574` | `world = camera ± (2·(m − rect.xy) − rect.wh)·8·scale / 4096 / 2` with `rect = [ui+8]`, `scale = [ui+0x10C]` (clamped 0x1000…0x4000 at `0x0040AEBF`); x adds, y subtracts (the world y axis points up). At scale 0x1000 this is exactly `camera − half·8 + (m − rect.xy)·8`, i.e. the inverse of the renderer's origin `camera − 0xE00 / 0xB80` (`0x0040B0BC/E0`, §Stage 3), pixel-exact because 896 and 736 are even |
| camera | `0x0040AE6A…0x0040AF2B` | scrolls in whole tiles (`± 0x100`), is clamped to `[half, map − half]` (`0x0041EE66/6F` = 0xE00 / 0xB80, `clamp2d 0x00436668`) and then has its low byte zeroed — always tile-aligned. Because 0xB80 is 11.5 tiles the bottom limit rounds down to 0xB00, so at the map's bottom edge the view shows half a tile beyond the map (world y −0x80…0), where a click yields a negative world y and the pick refuses it. Edge-scroll zones `ui+0x7D0…0x7DC` = the view rect shrunk by 3 px (`0x00432F80`), so they moved with the view |
| pick | `0x00409850` → `0x004350D4` | tile = world >> 8, bounds against the map, the tile dword's high bits must contain the player's vision mask `[player+0x19C4]` (own bit `0x40000000 >> p` plus allies with shared vision, rebuilt at `0x00419C1E`; no code clears these bits — explored means seen), then a 3×3-tile search of the three occupancy planes (`map+0x804` dwords, `map+0xC04` / `+0x1004` words, ids masked 0x3FF). Map-based, not render-list based |
| order | `0x0040968C` (spot) / `0x0040999E` (target) → `0x004092D0` → `0x00421764` | 16-bit world coordinates in the command bytes — a 256-tile map fits |
| minimap | `0x0040A070` | `world_x = ((x − 903)·2 + 1)·map_w / 96 / 2`, `world_y = ((90 − y)·2 + 1)·map_h / 84 / 2`, drawn at (903, 6) (`0x370E`) — consistent |

Also checked: no stock `0x800` / `0x700` half-viewport immediates remain in code (the three
`0x40ED20`ff and `0x441618`ff hits are a buffer size and an angle quadrant); the HD `MAINE`
script has no widget over the map view that the stock one did not have (only the two message
lines moved to y = 713 / 728 and the pause picture to (392, 304)); the cursor blit adds the same
hotspot offsets `0x00489708/0C` as stock and clips against 1024×768.

What the code does say, and what a reproduction should test: (1) the order is computed at
**release** time, so a camera step between press and release (edge scroll after a 100 ms dwell,
one tile per frame at `0x0040AE43`; arrow keys; a minimap drag; a jump-to-event key) moves the
target by whole tiles — stock behaviour, but the bigger view makes long clicks near the edges
more common; (2) with 2.9× the area on screen, far more of what the player sees is out of every
unit's sight, and the pick refuses a target whose tile lacks the vision bit even though the
terrain and site are drawn — an Exploiter sent at an unseen vent gets a plain move to the vent's
world point and stops beside it undeployed; (3) the half-tile overhang at the map's bottom edge
above. To separate these from a real defect the report needs: whether the same happens in the
untouched `dc16.exe`; whether the unit goes to a *wrong place* or reaches the spot and fails to
*deploy*; whether the view was scrolling; where on screen the click was (inside or outside the
old 512×448 area); minimap or map view. A scripted reproduction (drive `dc16new.exe` with
synthetic input, compare the ordered world point against the unit's position in the game state)
was not attempted.

#### 10.21 The Classic wave loader's leftover `exp/` prefix: Council Wars briefings in the Classic campaign **(19 Sep 2026, maintainer report "dc16new.exe must point at the correct sound files for mission briefings"; traced in both exes; patched; confirmed in game 19 Sep 2026: mission 1 plays the Classic briefing)**

Classic `dc16.exe` and `ENGEXP16.EXE` are one code base (§10.13, `DC16_SINGLE_EXE_MERGE.md`).
Council Wars opens every data file through the overlay helper `0x004063E4`, whose prefix slot
`0x004826D0` says `exp/`; in the Classic build that slot holds unrelated text (an assert string),
so the helper opens bare names. The **wave loader** is the exception: `wave.c 0x00452A50` has its
own prefix copy, the 8-byte DGROUP slot **`0x00487DC0`** (Classic file `0x855C0`; Council Wars
`0x00487DC8`, the slot the OZI MISSIONS mode swaps, §10.13), and **in the Classic build it still
says `exp/`**. The loader (`mov esi,0x487DC0` at `0x00452A68`, then two word-copy loops) builds
prefix+name into `[ebp-0x80E]`, opens it (`0x00452AC4`), on failure opens the bare name
(`0x00452ADD`), and on a second failure took the CD path (`0x00452AEF`ff, jumped over since §10.19).
Every WAV goes through it: the briefings `mission/h%d` / `mission/g%d` + `.wav` (the only
`mission/` strings in the exe), the start-up sound table, the ambience.

In the old `DC - Classic/` folder no `exp/` tree existed, so the first attempt always failed and
the leftover was invisible. Since the two games share `DC - Council wars/` (§10.18), the first
attempt **succeeds** wherever a Council Wars file of the same name sits under `exp/`: the
Council Wars briefings `exp/mission/h1…h8.wav`, `g1…g8.wav` (its two eight-mission campaigns) and
`exp/sound/water.wav` (which differs from `SOUND/WATER.WAV`). `dc16new.exe` therefore played the
Council Wars briefing for Classic missions 1-8 of both campaigns and the Council Wars water
ambience. No other WAV name of the Classic game has an `exp/` twin (`exp/sound/` holds 21 files;
the other names present in both trees, `slist.dat` and `sound2.dat`, are opened by the generic
helper, which has no prefix in Classic). The root `MISSION/*.WAV` are byte-identical to the
Classic originals (checked against `d660514^`), so the data was never wrong.

**Fix = `tools/patch_wavprefix.py`, patcher fix `sounds`, Dark Colony only:** the four letters
`exp/` at file `0x855C0` become NUL (`65 78 70 2F` → `00 00 00 00`), so prefix+name is the bare
name and the first open already hits `MISSION/` and `SOUND/`. Data only, in place, no code, no
`.reloc` entry (the slot has one referencing instruction). Located by pattern: the unique
`mov esi,imm32` into DGROUP that is followed by `lea edi,[ebp-80Eh]; push edi; mov al,[esi];
mov [edi],al`. Council Wars builds are refused by size: their loader must keep `exp/`. Applied to
`dc16new.exe` (SHA-256 `89894d73…`, before: `c54f434f…`); `Apply-DarkColonyPatches.ps1`
regenerated, canonical order now `nocd, resolution, hdpaths, cursor, pool, speed, clock, ddraw,
movies, sounds` for Dark Colony; `-All` from `dc16.exe` reproduces the repo exe byte for byte,
the Council Wars build is unchanged (`13c95489…`), `-Patches sounds` alone under PowerShell 5.1
writes exactly the four bytes. **Confirmed in game 19 Sep 2026** (maintainer: Classic mission 1 plays
the Classic briefing).

#### 10.22 Crash the moment the battlefield appears when the start position is near the far map edge: the unclamped start camera **(21 Sep 2026, maintainer report "when endotermic entered the battlefield his client hanged and was thrown out, the game continued as bots vs the second client"; traced in the Fly log, the Windows Application log and both exes; patched — fix `camera`, `tools/patch_camera.py`; confirmed in game the same day: the pre-fix exe reproduces the crash in replay mode, the fixed one plays the recording to the victory screen)**

**Evidence.** Fly log, 19 Sep 2026 18:26:55 UTC, room 1 (Plink - O, J8PLAY01, 160×140 tiles):
endotermic (slot 6) and Delaro (slot 3) with six Krusty bots. Both clients loaded (MREADY: Delaro
game player 2, endotermic **game player 0**), the server issued frame 0 at 18:26:57.16, Delaro
echoed it within 247 ms, endotermic never echoed anything (`latencyMs null, pendingEchoes 6`) and
was evicted after `ECHO_TIMEOUT_MS`: `client left … reason "no echo for frame 0"`; a Krusty
takeover played the base and Delaro's game ran 25 575 ticks with 0 checksum mismatches. Frame 0 was
the usual `TICK_SPEED(44)` plus two greeting chat lines, identical in structure to the other
Krusty-era games; the only thing peculiar to endotermic was the seat — in the 19 recordings before,
human clients had been game players 1, 2, 4, 6 and 7, never 0. On the same PC the game's
`error.log` stayed empty (only asserts write it), but the **Windows Application log** has an
`Application Error` (Event 1000) for `dc16new.exe` at 21:26:57 local = 18:26:57 UTC, the second
of "running": exception `0xC0000005`, fault offset `0x00045B52`, i.e. VA **`0x00445B52`**. Windows Error Reporting held the dying process (its dialog behind the exclusive
full-screen surface = the "hang"), so the TCP connection stayed open through the 5 s echo timeout;
`error.log`'s change time 21:27:12 is the moment the process finally went away.

**The faulting code.** `0x00445B52` is `test dword ptr [edx],ecx` inside `0x00445AA4`
(`gs, x0, tiles_x, z0, tiles_y`): the routine that counts the terrain classes of the tiles on
screen that the local player can see (`ecx` = `player(me).VISION` mask from `+0x19C4`) and returns
the most frequent class — the ambience picker (`0x00432040`, day/night `JUNGLE.AMB` etc.) calls
it from the client step `0x0040AAFC` every 5 s / 7 s, and at once on the first frame (its
timestamps start at 0). It checks only the origin (`x0 ≤ W`, `z0 ≤ H`, negatives return early)
and then loops `z0 … z0+tiles_y` × `x0 … x0+tiles_x` through the vision-plane row table
`map+0x804[z]` (`edx = row[z] + x*4`). Rows at or beyond the map height hold NULL: the fault.

**Why the camera was there.** The client step derives the rectangle from the camera:
`origin = (cam − half) >> 8` (`0x0040AB1C` / `0x0040AB2B`, half = `0xB80` / `0xE00` since the
stage-3 viewport edits of `patch_resolution.py`). The camera itself is set at game start by `proto.c`'s init: `cam_x = start_x << 8`,
`cam_z = start_z << 8` from the local player's record (`player+0xBD0/0xBD4`, `0x0041ED11..0x0041ED48`,
into `ui 0x004AA9D0` `+0x108` / `+0x110`), **then** the bounds `[half, map − half]` are computed and
stored in `ui+0x114..0x120` (`0x0041EE66..0x0041EEA9`) — and never applied to that first position.
The only clamp is in the render path (`clamp2d 0x00436668`, called at `0x0040AF16` with the six
bounds/camera pointers, then the low bytes are zeroed = tile snap), which runs **after** the
client step. So the very first frame scans `start − 11.5 … start + 11.5` rows. Team 0 of Plink - O
starts at (18,131) on a 140-row map: rows 119…141 → NULL row pointers → crash. Stock 16×14 (half
8/7) would have needed a start within 7 rows of the far edge, which no shipped map has (the
nearest is 8); at 28×23 every start row within 11 of the far edge crashes: J8PLAY01 teams 0 and 1,
D8PLAY01 team 3, D8PLAY03 teams 1 and 2, D8PLAY05 teams 0 and 1, J8PLAY07 team 3 … The low edge is
safe (`z0 < 0` returns early), the x direction only reads garbage inside the plane. The clamped
camera is also what draw_terrain renders, which is why nothing else complained; and once the player
scrolls, the render clamp keeps the camera inside, so the crash is a first-frame (and single-tick
"camera parked at the very top" ambience-tick) affair — Delaro, Kamyck and endotermic's own earlier
games at players 2/6/7 never hit it.

**Fix (`tools/patch_camera.py`, both exes, pattern-located, `.camera.bak`, patcher fix `camera`
after `ddraw`).** Right after the bounds are stored the init does `mov eax,ui; mov edx,name;
call load_ambience` (`0x0041EEB8` → `0x00432F80`; CW `0x0041EF18` → `0x00432FE0`). That call is
redirected to a 33-byte stub in the AUTO zero tail — Classic `0x0047F1DC` (right after the
`cursor` stub), Council Wars `0x0047F310` (after the `ozi` stubs):

```
8D B0 08 01 00 00   lea  esi,[eax+108h]        ; esi = &cam_x (ui in eax)
8D 4E 08            lea  ecx,[esi+8]            ; &cam_z
51 56               push ecx ; push esi         ; &cam_z, &cam_x
FF 76 18            push [esi+18h]              ; max_z   ui+0x120
FF 76 14            push [esi+14h]              ; max_x   ui+0x11C
FF 76 10            push [esi+10h]              ; min_z   ui+0x118
FF 76 0C            push [esi+0Ch]              ; min_x   ui+0x114
E8 rel32            call clamp2d                ; 0x00436668 / CW 0x004366C8, ret 18h, preserves eax and edx
E9 rel32            jmp  load_ambience          ; returns to the init as before
```

Register-relative only, so no `.reloc` entries; `load_ambience` reads only `eax` (and `edx`), which
the stub leaves untouched (`clamp2d` saves everything it uses). Two edits per exe: the rel32 of the
call and the 33 zero bytes. `verify` re-derives every address (bounds site, `clamp2d` also
cross-checked against the render path's call at `0x0040AF16`, the camera init two dozen
instructions earlier) and refuses anything unexpected. Harmless without `resolution` and in
single-player (the clamp can only move the camera inward). Both repository exes carry it; the
patcher rebuilds them byte-identically (SHA-256 Classic `09e9c007…`, CW `c098d3dc…`).

**Reproduction / confirmation (21 Sep 2026):** `REPLAY_FILE=logs/replays/2026-09-19T18-26-56-991Z-room1-J8PLAY01.jsonl REPLAY_SLOT=6 node src/index.js`
seats the viewer in endotermic's slot, so the real client becomes game player 0 at (18,131). The
exe before the fix (git `HEAD` copy, run as `dc16old.exe`) died at the first frame exactly as on Fly
(Event 1000, offset `0x45B52`, server: "no echo for frame 0"); the fixed `dc16new.exe` showed the
battlefield and followed the whole 1125 s recording to the victory screen. (The replay server
needed a fix of its own on the way: its frame cursor did not rewind between games, plan §16.)

**A second, unrelated crash in the same log**: the relaunched game (21:27:12 local, room 2,
Armageddon, seven bots, game player 4) ran 50 s, the player left the battle (`connection closed`
18:28:56 UTC) and the process died at 21:29:17 with fault offset `0x2F8B8` = `0x0042F8B8`: the
surface `Lock` wrapper `0x0042F828` found `[0x489734] == 1` ("Lock when already locked" printed to
the buffered `error.log`, lost in the crash), ran `assert(0)` at `ddex4.c` line 1197 through the
soft assert helper and then called `Lock` on the surface pointer `[0x489720]`, which was already
NULL — a quit-time display shutdown ordering bug. Not fixed; noted here so it is not mistaken for
§10.16 or for this section.

#### 10.23 A screen-resolution drop-down in the patcher: feasibility **(21 Sep 2026, maintainer request "investigate screen resolution dropdown in patcher creation possibility; both existing resolutions, widescreen, keep the primary monitor's aspect ratio"; assessment only — nothing implemented, nothing patched)**

**Verdict: feasible, with three exe-side changes and one data-side decision.** Every one of the
165 `resolution` edits is already a function of a `Geometry(width, height)` object
(`patch_resolution.py`), and the HUD frame (`hud_layout.py`), the letterboxed menus
(`pad_background.py`), the full-frame main menu (`paint_intro.py`, which already centres the planet
for non-4:3 aspects) and the clock anchor (`patch_clock.py`) all take `--width/--height`. What
stops the tool today at anything but 1024×768 are three checks in `Geometry.__init__`; each one is a
limitation of the *tool*, not of the binary:

| Check in the tool | Why it exists | Binary reality (21 Sep 2026, `dc16.asm`) | Change |
|---|---|---|---|
| `width` must be a power of two | the three strength-reduced `y*640` / `y*1280` strides (§8.2) were rewritten as `(y*4) << n` | each idiom starts with a **7-byte** `lea r,[s*4+0]` (`0x0042C209` `8D 14 8D 00000000`, `0x004363B6` `8D 04 8D 00000000`) or is the 11-byte window `shl eax,2; add eax,edi; mov ebx,[ebx+8]; shl eax,8` (`0x004360B6..C1`). A 6-byte `imul r,s,imm32` (`69 D1` / `69 C1` / `69 C0` + imm32) plus one `90` replaces the `lea`, and the `add`/`shl` become `mov r,r` / `shl r,0` as before; the 11-byte window becomes `imul eax,eax,W*2` + `mov ebx,[ebx+8]` + `90 90` (`edi` is loaded at `0x004360B3` and stays). No jump lands inside the three windows, the zero displacements carry no `.reloc` entry (checked), and no flag consumer follows before the next flag writer. | length-preserving, any width |
| `tiles_x <= 34` | `draw_terrain`'s stack lightmap keeps a hard-coded **144-byte** row stride (§10.4/10.5): a second wrap collides | the stride is the idiom `lea r,[s*8]; add r,s; shl r,4` at six sites inside `0x00453B07..0x00453C25` (`add`+`shl` at `0x453B0E/B10`, `B5C/B5E`, `B72/B74`, `BA6/BA8`, `BFB/C00`, `C23/C25`; the two other `shl …,4` in the range, `0x453C0E` and `0x453C50`, are the ×544 stride of the light table at `0x533C90`). Neutralising the `add` and making the shift 5 (or 6) turns it into a stride of **256 (512)** bytes — the option §10.5 named and never needed. The parity argument of §10.5 holds for any even column count, so the single-wrap limit becomes `tiles_x < stride/4 − 1`: **62 across at 256, 126 at 512**. The frame growth and the ten disp32 rebases already exist; the footprint for 1920×1080 is 17 KB, for 2560×1440 46 KB, against the 256 KB commit the patch already sets | 12 more edits, stride chosen from the geometry |
| view must be a multiple of 32 in both axes | the stock 512×448 and 896×736 are exact; a partial tile row was never tested | most widescreen modes leave a remainder (1920×1080: 1048 rows = 32 tiles + 24 px). Rather than test partial tiles, keep the rule and give the remainder to the HUD frame: `Geometry` floors the view to whole tiles and `hud_layout.target()` grows the bottom bar / right panel by the slack (its splice already adds any number of rows or columns) | tool change only |

Candidate modes under those rules (view = `(W−128, H−32)` floored to tiles; slack = HUD growth
beyond the stock 124/26 px; lightmap = stride and frame the patch would choose):

| mode | aspect | map view (tiles) | slack x,y | lightmap | notes |
|---|---|---|---|---|---|
| 640×480 | 4:3 | 512×448 (16×14) | 0,0 | 144 / 4.4 KB | stock — drop-down entry = no `resolution`/`hdpaths`/`clock` fix |
| 800×600 | 4:3 | 672×544 (21×17) | 0,24 | 144 / 5.2 KB | needs the `imul` strides |
| 1024×768 | 4:3 | 896×736 (28×23) | 0,0 | 144 / 7.0 KB | today's build, bytes unchanged |
| 1152×864 | 4:3 | 1024×832 (32×26) | 0,0 | 144 / 7.9 KB | |
| 1280×960 / 1280×1024 | 4:3 / 5:4 | 1152×928 / 1152×992 (36×29 / 36×31) | 0,0 | 256 / 15–16 KB | first modes past 34 tiles |
| 1600×1200 | 4:3 | 1472×1152 (46×36) | 0,16 | 256 / 19 KB | |
| 1280×720 | 16:9 | 1152×672 (36×21) | 0,16 | 256 / 11 KB | |
| 1366×768 | ~16:9 | 1216×736 (38×23) | 22,0 | 256 / 12 KB | odd width is fine with `imul` |
| 1600×900 | 16:9 | 1472×864 (46×27) | 0,4 | 256 / 14 KB | |
| 1920×1080 | 16:9 | 1792×1024 (56×32) | 0,24 | 256 / 17 KB | |
| 2560×1440 | 16:9 | 2432×1408 (76×44) | 0,0 | 512 / 46 KB | stride 512 |
| 1280×800 / 1440×900 / 1680×1050 / 1920×1200 | 16:10 | 36×24 / 41×27 / 48×31 / 56×36 | 0,0 / 0,4 / 16,26 / 0,16 | 256 | |
| 2560×1600 | 16:10 | 2432×1568 (76×49) | 0,0 | 512 / 51 KB | |

Everything else in the exe is already generic: the 44 menu-furniture sites move by
`((W−640)/2, (H−480)/2)`, the credits box by the tool's fixups, the minimap sits at `W−121`, the
movie `Blt` stretches to the full width at 16:9 (a 16:9 screen is then filled edge to edge), mouse
clamps, clear loops and surface sizes are dwords, `tiles_y` fits its signed byte up to 127 rows.
`patch_camera.py` reads the bounds at run time; `ddraw`, `nocd`, `cursor`, `speed`, `movies`,
`sounds` and `ozi` do not depend on the mode. Two things to re-check per new mode, not blockers:
the **local pool** (`patch_pool.py`, 32 MiB) was sized for 1024×768 backgrounds and banks — a
1920×1080 8-bit page is 2 MB and every full-frame menu GIF grows 2.6×, so the constant should scale
with `W×H` (or go to 64 MiB once); and the **DirectDraw mode** must be one the display lists (the
risk table in §11 still holds: a refused `SetDisplayMode` aborts with no fallback), which is exactly
why the drop-down should offer only modes read from the monitor (below).

**Nothing is scaled.** Sprites, fonts and HUD art keep their 640×480 pixel size, so at 1920×1080
the battlefield shows 5.6× the stock area and a unit is about a third of the apparent size it has at
1024×768 on the same monitor. That is the maintainer's call, not a defect; §9 has said so from the
start.

**Data side — the real cost.** Each resolution needs its own generated interface set: the padded
scripts and GIFs, `LOAD*.BMP`, the `*SCENE.TXT` lists, the rebuilt HUD frame `INTRFACE.GIF` +
`MAINE`, the full-frame `INTRG.GIF`/`INTRO.GIF`, the re-baked logo banks `SPRITES/*_HD.SPR` — 59 +
6 + 5 files, 2.2 MB `INTRF_HD` + 0.7 MB sprites at 1024×768, roughly 7 MB at 1920×1080, so a set of
eight modes is 30–40 MB of repository data (or downloadable zips: the patcher's resource check
already greys out a fix whose `Data` files are absent). The tools cannot run on a player's PC
(Python + numpy + Pillow; the patcher is pure .NET), so the sets are generated here and shipped.
Two ways to address them from the exe: (a) one folder per mode — the `hdpaths` strings are
rewritten **in place**, so a folder name must be **exactly 8 characters** like `intrf_hd` (a naming
scheme is needed, e.g. a documented code per mode; `1920x1080` is nine), which lets a 1024×768 and
a widescreen exe coexist in the same game folder; (b) the patcher copies the chosen set into
`INTRF_HD/` and the exe strings stay as they are — simpler, but one mode at a time and the
committed folder gets overwritten. Recommend (a).

**Patcher side.** `gen_apply_script.py` replays the Python tools and writes every edit as a literal
`Offset / Old / New` triple — the transparency the players asked for (§10.17 history). Keep that:
the generator runs `patch_resolution.py` / `patch_hd_paths.py` / `patch_clock.py` / (scaled)
`patch_pool.py` once per mode and emits a *family* of variants of these fixes, each ~200 lines, all
byte-listed; a `ComboBox` "Screen resolution" above the fix list picks the variant (the fixes remain
visible and tickable, the 640×480 entry unticks and disables the mode-dependent ones; `Requires`
keeps `resolution↔hdpaths↔clock` together; the CLI gets `-Resolution WxH`). Embedding the geometry
formulas in PowerShell instead would shrink the file but hide the bytes behind arithmetic — against
the maintainer's requirement of 14 Sep 2026. The "keep my monitor's aspect ratio" option: read the
primary display's **current mode and its full mode list** through `EnumDisplaySettingsEx`
(`Add-Type` with a small P/Invoke signature; still nothing but .NET and the Windows API — do **not**
use `[Screen]::PrimaryScreen.Bounds`, which Windows PowerShell 5.1 reports DPI-virtualised, e.g.
1536×864 for a 1920×1080 monitor at 125 %), reduce the current mode by its GCD to the aspect ratio,
and offer the modes the display lists that (1) have the same ratio when the checkbox is on and
(2) pass the geometry rules above and have a data set shipped. Default selection = the current
mode if it is supported, else the largest supported mode of that ratio.

**Estimated work.** Exe: the `imul` rewrite (3 sites), the lightmap stride (6 idioms) and the
slack rule in `Geometry`/`hud_layout.target()` — one day including a game test at one 16:9 and one
16:10 mode on both exes (the Council Wars offsets follow the `+0x60` rule and the existing
fixups). Data: one generation run per mode (`pad_background`, `hud_layout`, `paint_intro`,
`logo_art`, `split_hd_data` with a per-mode folder name, `build_ozi_overlay` for Council Wars) —
mechanical once the folder scheme is chosen. Patcher: generator loop + `ComboBox` + mode
enumeration — half a day, plus the headless GUI test. Open decisions for the maintainer: the mode
list to ship (data volume), the 8-character folder scheme (or option (b)), and whether the pool
grows per mode or once.

#### 10.24 Any width, any height: `imul` strides, a 256/512-byte lightmap stride, and the first 1280×800 build **(21 Sep 2026, maintainer request "implement it: imul strides, lightmap stride 256, then test 1280x800; all higher than original resolution must share the same high res folder"; implemented in `patch_resolution.py`, both exes; Classic confirmed in game the same day: intro, main menu, campaign battle at 1280×800, no crash, `error.log` empty)**

What changed in `tools/patch_resolution.py` (the 1024×768 plan and the generated patcher script
are byte-identical to before — checked by diffing the plan and regenerating
`Apply-DarkColonyPatches.ps1`):

1. **Width no longer has to be a power of two.** `Geometry.pow2` picks between two site
   lists that sit at the historical place of the six stride edits. `STRIDE_SHIFT_SITES` is the
   old form (`add` → `mov r,r`, `shl` count +1). `STRIDE_IMUL_SITES` (7 edits) rewrites the
   three §8.2 idioms in place for any width:

   | Classic file | Stock | 1280 | Meaning |
   |---|---|---|---|
   | `0x2B609` | `8D 14 8D 00000000` `lea edx,[ecx*4]` | `69 D1 00050000 90` `imul edx,ecx,1280; nop` | `driver.c` y·W |
   | `0x2B613` / `0x2B618` | `01 CA` / `C1 E2 07` | `89 D2` / `8D 52 00` | `add`, `shl` neutralised (`mov edx,edx`, `lea edx,[edx+0]`) |
   | `0x357B6` | `8D 04 8D 00000000` `lea eax,[ecx*4]` | `69 C1 00050000 90` | `engmain.c` y·W |
   | `0x357BD` / `0x357C5` | `01 C8` / `C1 E0 07` | `89 C0` / `8D 40 00` | as above |
   | `0x354B6` (11 bytes) | `C1 E0 02 01 F8 8B 5B 08 C1 E0 08` `shl eax,2; add eax,edi; mov ebx,[ebx+8]; shl eax,8` | `69 C0 000A0000 8B 5B 08 90 90` `imul eax,eax,2560; mov ebx,[ebx+8]` | `engmain.c` y·W·2 (bytes) |

   Council Wars: the same at +0x60 (`auto_offset`). Checked before writing: no jump target
   inside the three windows, no `.reloc` entry on the zero displacements, no flag consumer
   between the rewritten instructions and the next flag writer (`xor ebx,ebx` / `add`).

2. **Lightmap row stride.** Past 34 tiles across (`4·(2·tx+2) ≥ 288`) the six ×144 row idioms
   of `draw_terrain` (§10.4/10.5: `lea r,[row*8]; add r,row; shl r,4` at `0x453B0E/B10`,
   `B5C/B5E`, `B72/B74`, `BA6/BA8`, `BFB/C00`, `C23/C25`; file `0x52F0E`…`0x53025`) get the
   `add` neutralised and the shift count raised to 5 (256 bytes) or 6 (512 bytes):
   `LIGHTMAP_STRIDE_SITES`, 12 edits, `Geometry.lm_stride`/`lm_shift`. The two other
   `shl …,4` in the range (`0x453C0E`, `0x453C50`) are the ×544 stride of the light table at
   `0x533C90` and stay. The single-wrap argument of §10.5 holds for any even column count, so
   the cap becomes `tx < stride/4 − 1`: 62 tiles at 256, 126 at 512. The footprint uses the
   chosen stride, so the frame growth (§10.5) follows: 1280×800 = 75×51 half-tiles, 13 100
   bytes, frame `0x14CC → 0x337C`.

3. Still enforced: the view must be whole tiles in both axes (`W−128`, `H−32` multiples of 32).
   1280×800 (36×24), 1280×1024 (36×31), 1152×864 (32×26), 1280×960 (36×29), 2560×1440 (76×44,
   stride 512) pass; 800×600, 1600×1200, 1920×1080 are refused until the HUD absorbs the slack
   (§10.23, not done).

**Edit counts:** 1280×800 = 178 edits (165 − 6 shift + 7 imul + 12 stride), both exes plan
clean on the stock originals.

**Data build for 1280×800** (`scratchpad/work1280`, a copy of the stock `INTRFACE GAMESTAT
SPRITES ANIMATE exp/{intrface,gamestat,sprites,animate}` plus the root `PALETTE.*`,
`COLOUR.SET`, `FADE.DAT` — `logo_art.py` needs `PALETTE.GIF` in the game root): `pad_background.py
apply INTRFACE --width 1280 --height 800` (+ `exp/intrface`), `paint_intro.py apply GAME
--width 1280 --height 800`, `logo_art.py apply GAME`, `hud_layout.py build INTRFACE …`,
`hud_layout.py maine INTRFACE apply …` (note the argument order: `DIR ACTION`), then
`split_hd_data.py apply GAME --stock STOCK`. Result: the same 59 `INTRF_HD` names as at
1024×768, `exp/intrf_hd` five files (the padded CW `intrg.gif`/`intro.gif` were dropped: the
CW menu draws the painted root backdrop, as in the repository), `SPRITES/DC??_HD.SPR`,
`ANIMATE/DC??_HD.FIN`. **Per the maintainer's decision the HD set of whatever resolution is
chosen lives in the one `INTRF_HD` folder** (no per-mode folders; the exe strings stay
`intrf_hd`), so the game folder now holds the 1280×800 set and `git diff` shows the 59 files.
**Council Wars menu (second maintainer report, same day: "for engexp16 main menu the last two
buttons are not in place"):** `build_ozi_overlay.py` hard-coded the 1024×768 rows — OZI LOAD and
QUIT were written at (422, 619/645) into a script whose other rows sit at x=550, y=561/587/613 —
and the pack's globe markers at (+192,+144). It now reads the column x and the row pitch from the
`pushb 0/2/16` rows of `exp/intrf_hd/bintroe` (OZI LOAD = PLAY INTRO row + pitch, QUIT + 2·pitch:
1280×800 → 550, 639, 665) and the marker shift from its `size W H` line (`(W−640)/2, (H−480)/2`);
the committed 1024×768 script comes out unchanged. Re-applied to the game folder
(`exp/intrf_hd/bintroe`, `ozi_ns/intrf_hd/{bintroe,hxscene.txt,gxscene.txt}`).

**Tool bug found by the test — fixed in `split_hd_data.py`:** a script's `background
intrface/<gif>` was retargeted to `intrf_hd/` only when that GIF moved *in the same run*. The
HUD script was rebuilt after a first partial run had already moved `INTRFACE.GIF`, so
`INTRF_HD/MAINE` kept `background intrface/intrface` and the game drew the stock 640×480 frame
into the 1280×800 buffer (widgets in place, frame art missing; maintainer: "only the
interface of the battlefield is a bit wrong"). The tool now also counts the GIFs already
present in `INTRF_HD`. The fixed `MAINE` is installed; it is read when a battle screen opens,
so the running game shows it from the next mission on.

**Test exes** (not committed, no "patch" in the name, §10.18): `DC - Council wars/dc16wide.exe`
(SHA-256 `6852dee2…`; chain `nocd, resolution 1280x800, hdpaths, cursor, pool, speed, clock
1280x800, ddraw, camera, movies, sounds`) and `engexp16wide.exe` (`2cae7082…`, same without
`movies`/`sounds`, no `ozi`; maintainer ran it: menu rows see above, otherwise fine). **Classic run:** `SetDisplayMode` took the panel to
1280×800 (`Win32_VideoController` 1920×1200 → 1280×800), intro movie stretched to 1280×720
with 40 px bands (movie `Blt` at a non-power-of-two width), main menu full-frame, NEW CAMPAIGN →
mission 1 battle: 36×24-tile view, visibility/lighting smooth across the full width (no
wrap or black-line artefacts — the 256-byte stride and the frame growth work), minimap at
(1159,6), clock at (1248,770), money and status text in the panel's bottom cluster, 135 s in
battle, no Event 1000, `error.log` 0 bytes. A first launch died silently within a minute: the
Windows firewall prompt for the game's network access took the foreground (maintainer
report), the relaunch was fine. Screenshots: `CopyFromScreen` of the primary screen works
while the game owns the display (the DirectDraw primary is composited), `PrintWindow` too.

#### 10.25 The patcher's resolution drop-down: 640×480, 1024×768, 1280×1024, 1280×720, 1280×800 **(21 Sep 2026, maintainer request "implement the dropdown in patcher for resolution selection. patcher should check aspect ratio of monitor and mark its aspect ratio in dropdown as recommended; format WIDTHxHEIGHT (a:b) recommended; resolutions supported: 640x480, 1024x768, 1280x1024, 1280x720, 1280x800"; implemented in `gen_apply_script.py` → `Apply-DarkColonyPatches.ps1`, `patch_resolution.py`, `hud_layout.py`; tested on the command line under PowerShell 7 and 5.1 and headlessly in the window; not yet committed)**

**Exe side: the HUD slack rule.** 1280×720 leaves `720 − 6 − 26 = 688 = 21·32 + 16` rows: the
view is 36×21 tiles and the 16 spare rows go to the HUD's bottom bar. `Geometry` floors the view
height to whole tiles (`slack_y`, only the view height reaches the exe) and `hud_layout.target()`
makes the `bottom_bar` region `slack_y` taller, anchored to the bottom edge: `cmd_build` splices the
extra rows in at the bar's top edge, repeating its first two rows (`BAR_TOP_SEGMENT`), but only left
of the right panel — the bar's stretch under the panel is the panel's bottom cluster, already
painted by `right_panel`, and repeating those rows drew stripes under the BUILD button in the first
1280×720 frame. `cmd_maine` needs nothing: bottom-bar widgets move by `H − 480` and stay in the
bar's lower 26 rows. Spare **columns** (1366×768) still have no home and are refused. Plans:
1280×720 = 178 edits (imul + stride 256), 1280×1024 = 178 (36×31 tiles, exact), both exes.

**Data sets.** One `INTRF_HD` folder serves every resolution (maintainer decision, §10.24), so a set
per size was generated with the pipeline of §10.24 (`scratch make_set.sh`: copy the stock
`INTRFACE GAMESTAT SPRITES ANIMATE exp/{intrface,gamestat,sprites,animate}` + root `PALETTE.*
COLOUR.SET FADE.DAT`, then pad → paint → logo → hud build → hud maine → split; drop the two padded
CW `exp/intrf_hd/intr?.gif`). The 1024×768 output reproduces the committed set byte for byte apart
from CRLF/LF (git normalises the text files) and the `movies` line-154 edit that `patch_movies.py`
applies afterwards — a regression test of the whole pipeline. The four sets (2.9–4.0 MB each:
`INTRF_HD/` 54 files, `exp/intrf_hd/` 5, `SPRITES/DC??_HD.SPR`, `ANIMATE/DC??_HD.FIN`) are kept
**outside both repositories** in `Dark-Colony-development/hd_sets/<WxH>/` with a README (how to
install one: copy over the game folder, then `build_ozi_overlay.py --apply` and `patch_movies.py
apply`); where they ship is the maintainer's call (the game folder currently holds the 1280×800 set
from §10.24, uncommitted). The patcher does not copy sets; it checks the one in place.

**Generator (`gen_apply_script.py`).** `STOCK_MODE = '640x480'`, `HD_MODES = ['1024x768',
'1280x1024', '1280x720', '1280x800']`, `DEFAULT_MODE = '1024x768'` (the published exes),
`MODE_STEPS = {resolution, clock}` (replayed once per HD mode with `--width/--height`; the plan
cache is keyed by mode), `HD_STEPS = {resolution, hdpaths, clock, movies}` (absent in the stock
mode; `movies` because its ending names live in the `INTRF_HD` lists and it requires `hdpaths`).
Every build is replayed once per mode (5 × Classic, 5 × Council Wars; the map editor has no
modes), the per-step attribution moved into `attribute()`, and the results are merged: a fix
outside `MODE_STEPS` must come out byte-identical in every mode it exists in (asserted — the tools
touch disjoint bytes) and is emitted once with `Mode = $null` (`'hd'` for `hdpaths`/`movies`);
`resolution` and `clock` are emitted once per HD mode with `Mode = 'WxH'`, mode-aware `Name` and
`Description` (numbers from `patch_resolution.Geometry`: view, tiles, minimap x, movie rect, menu
shift, imul/shift, lightmap stride, slack) and, on `resolution`, `DataSize = @{ File =
'INTRF_HD\INTRFACE.GIF'; Width; Height }`. Builds carry `Modes`, `DefaultMode` and
`ReferenceSha256` (mode → SHA-256 with every fix of that mode; `PatchedSha256` = the default
mode's = the repository exe). The 1024×768 edits are unchanged. The script grew from 258 KB to
506 KB (6970 lines).

| Reference SHA-256 (every fix of the mode) | Classic `dc16.exe` | Council Wars `ENGEXP16.EXE` |
|---|---|---|
| 640×480 (nocd, cursor, pool, speed, ddraw, camera [, sounds / ozi]) | `5dcffacb…` | `6db52b4d…` |
| 1024×768 (= published `dc16new.exe` / `engexp16new.exe`) | `09e9c007…` | `c098d3dc…` |
| 1280×1024 | `4fb0d3f7…` | `57aabf54…` |
| 1280×720 | `3f0fdc4e…` | `8a2b5230…` |
| 1280×800 (= §10.24's test exes after `movies`) | `ba6f9e03…` | `5362a35d…` |

**Script (`Apply-DarkColonyPatches.ps1`).** New parameter `-Resolution WxH` (default = the build's
`DefaultMode`; `640x480` = no display fixes; unknown values are refused with the valid list). New
helpers: `Get-BuildPatches build mode` (the effective fix list: `Mode $null` always, `'hd'` in every
HD mode, `'WxH'` in that mode), `Resolve-Mode`, `Get-AspectLabel` (GCD, with 8:5 written 16:10 and
1366×768 counted as 16:9), `Get-MonitorSize` (**since the evening of 21 Sep 2026 `Screen.PrimaryScreen.Bounds` first**:
its Primary flag is explicit and the ratio survives DPI scaling; the earlier first choice
`Win32_VideoController.CurrentHorizontal/VerticalResolution` names one mode per adapter and, with
the maintainer's two monitors on one Intel adapter, reported the 1920×1200 panel while the 1920×1080
external screen was primary → "recommended" landed on 16:10; CIM is now the fallback), `Format-ModeLabel` → `"1280x800 (16:10)
recommended for your screen"` when the ratio equals the monitor's (wording and the preselection
below: maintainer request, same day), `Get-PreferredMode` (the window preselects the largest
recommended size, else the build's default; the command line keeps 1024x768 so `-All` reproduces
the published exe everywhere), `Get-GifSize` and `Get-DataSizeProblem` (a `DataSize` fix was unavailable when `INTRF_HD\INTRFACE.GIF` was not WxH — **superseded the same day by §10.26, where the patcher writes the set itself**). `Invoke-PatchRun`,
`Get-DataProblems`, `Get-UnavailableFixes`, `Get-RequirementLines`, `Write-PatchList` (lists the
modes and their reference hashes; fixes tagged `@ WxH`) and `Get-VerifyReport` (a fix with
variants is reported once, `APPLIED (1280x800)`, and a hash equal to a reference build is named)
take the mode. Window: a "Screen resolution" `ComboBox` (DropDownList) above the fix list, filled
per build with `Format-ModeLabel`, default preselected; changing it refills the list
(`$script:gui.FillList`, `$script:gui.Patches` = the effective list, all index-based handlers use
it) and re-runs the resource check; the result line says "byte-identical to the exe published in
the repository" for the default mode and "to the reference build for WxH" otherwise. Tested (all
pass): `-All` into the game folder with the 1280×800 set in place skips resolution/hdpaths/clock/
movies with the size reason and writes the 7 others; `-All -IgnoreMissingData` = published
`dc16new.exe`/`engexp16new.exe`; `-All -Resolution 1280x800` = §10.24's `dc16wide.exe`, the CW
selection without `ozi` = `engexp16wide.exe`, CW `-All -Resolution 1280x800` (with `ozi`) applies;
`-Resolution 640x480 -All`; `-Verify` on all of them; unknown resolution refused; the window
headless (combo items, default 1024×768 with four fixes greyed, 1280×800 all available and Apply =
`dc16wide.exe`, 640×480 = 7 fixes, 1280×720 shows the size reason); the same under Windows
PowerShell 5.1. Screenshot `gui_720.png` in the session scratch.

**Smaller sets (same day, maintainer request "implement 1 and 3" of the footprint review):** a set
was 2.8-3.2 MB, of which the two loading screens `LOAD.BMP`/`LOAD2.BMP` were 1.5-2.0 MB (uncompressed,
96 % black) and the three logo banks 0.7 MB - and the banks are **byte-identical in all four sets**
(re-baked onto black, `paint_intro` mode `black`), so they are not per-resolution data at all. Now:
(1) the banks and their FINs are the one shared committed copy `SPRITES/DC??_HD.SPR` +
`ANIMATE/DC??_HD.FIN` and no longer part of a set; (3) the loading screens are **written by the
patcher**: `Write-LoadingScreens` in `Apply-DarkColonyPatches.ps1` builds `INTRF_HD\LOAD.BMP` /
`LOAD2.BMP` for the chosen size from the stock `INTRFACE\LOAD.BMP` / `LOAD2.BMP` (the 640x480
picture centred on a black canvas; header as Pillow writes it - 1078-byte offset, 256 BGRX palette
entries, 96 dpi - so the output is byte-identical to `pad_background.py`'s, checked for all four
sizes) whenever the ones in place have another size (`Get-BmpSize`), at the end of `Invoke-PatchRun`
when an HD `resolution` variant was applied; the result line lists what was written. `hd_data` no
longer requires `INTRF_HD\LOAD*.BMP` but requires the stock pair instead. The committed `INTRF_HD`
keeps its 1024x768 pair so the published exe runs from a plain clone; the patcher overwrites them
when the size changes. A set is now `INTRF_HD/` (52 files) + `exp/intrf_hd/` (5), 0.5-0.6 MB
(`hd_sets/` updated).

**Not done / open:** the sets' home in the repository (or a "copy the set for me" step in the
patcher); a Council Wars game test at a non-1024 mode (the 1280×800 CW exe ran, §10.24); 1280×720
and 1280×1024 were not run in the game (plans and data only); spare columns (1366×768); `pool` is
still 32 MiB for every mode.

#### 10.26 The patcher builds the interface set itself: text rules, a compiled GIF codec, three shipped pictures per size **(21 Sep 2026, maintainer request "can we regenerate needed resources on the fly when patching so there are no missing resources?" → "Do this: 1) 40 text files … 2) 15 letterboxed background GIFs 3) INTRG.GIF, INTRO.GIF … ship them per size", with the codec in C# because "source code is there" — the script must say what is compiled, why, and what the alternatives cost; implemented in `gen_apply_script.py` → `Apply-DarkColonyPatches.ps1`; output checked file by file against the Python tools' sets for all four sizes; not yet committed)**

**What the loader allows.** `gifload.c` (`0x0044E818`) reads the 6-byte header (`GIF`, version `87a`/`89a`
or "bad version number"), the 7-byte screen descriptor, the global colour table, then **one byte
that must be the image separator** (no extension blocks are skipped) and the 9-byte image
descriptor, whose width/height are compared with the screen's (`0x0044E9C6`…`0x0044E9EA`, error
string "Don't be trying to pass me off one of em new fan…") — **left/top are ignored**. So a
letterboxed background cannot be made by moving the image inside a larger screen; the pixels have
to be re-encoded, exactly what `pad_background.pad_gif` does with Pillow.

**What the patcher does now (`Write-InterfaceSet`, run at the end of `Invoke-PatchRun` when an HD
`resolution` variant was applied).** From the stock files of the game folder it writes, for the
chosen size:

| output | rule (Python original) | how in the script |
|---|---|---|
| 19 padded menu scripts | `pad_background.edit_script`: widgets +(dx,dy) of the GIF's letterbox, `size 0 0 W H`; `split_hd_data`: `background intrface/…` → `intrf_hd/…` | `Edit-PaddedScript`, `Set-BackgroundHd`, on Latin-1 strings (one char per byte), lines split at LF keeping their own CR |
| 4 dialogs (`LOBJE LOPTE LQCE LSGE`) | `scan_dialogs`: 4-number `size` without background, rect and widgets +(dx0,dy0) | same |
| `BINTROE INTROE BUTTONSE DINTROE` | `paint_intro.layout_for/relayout`: cluster centre as a fraction of the height (+20 with a logo), grid centred, logo/title centred, `size W H` | `Edit-IntroScript` (`Get-Positioned`, `Test-Logo`, `Test-Title`; banker's rounding like Python's `round`) |
| `MAINE` | `hud_layout.cmd_maine`: panel x+=dx (y+=dy from row 399), bottom bar y+=dy (x+=dx from column 300), the PAUSED picture by half | `Edit-HudScript` |
| 4 briefing lists | `edit_scene`: the `frame x y` line after an `.avi` line +(dx0,dy0); `patch_movies`: `avi/hending.avi` → `avi/dchending.avi`, `aending` → `dcaending` when the `movies` fix is chosen **or `AVI\DCHENDING.AVI`/`DCAENDING.AVI` are in the folder** (found by the in-place test: a Council Wars run, which has no `movies` fix, would otherwise undo the Classic names in the shared folder) | `Edit-SceneList`, the two `Replace` calls |
| `INTRG.DAT INTRO.DAT` | `rename_dat_list`: `dcss.fin` etc. → `*_hd.fin` | `Edit-DatList` |
| 15 letterboxed GIFs | `pad_gif`: first black palette entry as border, picture centred, palette kept, same GIF version | `[DcGif]::Pad` — the compiled codec |
| `INTRG.GIF INTRO.GIF INTRFACE.GIF` | painted planet / spliced HUD frame: not derivable | copied from **`INTRF_HD\<WxH>\`** (shipped per size, 80-104 KB per size; the four folders are in the game folder, untracked) |
| `LOAD.BMP LOAD2.BMP` | §10.25 | `Write-LoadingScreens` |
| `exp\intrf_hd\bintroe introe shumane hxscene.txt gxscene.txt` | the same rules on `exp\intrface` / `exp\gamestat`, plus `build_ozi_overlay.menu_rows` on `bintroe` | `Edit-OziMenu` |
| `ozi_ns\intrf_hd\bintroe introe shumane` + two lists | copies of the exp scripts; the pack lists shifted | `build_ozi_overlay.py` now also writes the **unshifted** pack lists to `ozi_ns\gamestat\hxscene.txt`/`gxscene.txt` (inert for the exe, source for the patcher) |

Skipped like the Python tool: `MULTIE~1.TXT` (`split_hd_data` DROP). The set is written whenever an
HD display fix is applied (it overwrites what is in `INTRF_HD`), so the "which set is in the
folder" check of §10.25 (`DataSize`) is gone; the fixes' `Data` lists name the **inputs** instead
(47 `INTRFACE` files, the 4 `GAMESTAT` lists, the shared banks, the three per-size pictures; Council
Wars adds the 3 + 2 + 2 exp/OZI inputs), so "resources not found" now only happens for a folder that
is not a real game install or lacks the three pictures of that size.

**The codec.** `$GifCodecSource` is ~250 lines of C# 5 (`DcGif.Decode/Encode/Pad/Size`: 87a/89a,
global or local colour table, interlace, extension blocks skipped, LZW decoder with the KwKwK case,
LZW encoder with a 4096×256 prefix table and a clear code at 4096, 255-byte sub-blocks, header
`0x87` + 256-entry table + one full-screen descriptor + minimum code size 8 — the shape
`check_gif_layout` demands). `Initialize-GifCodec` hands it to `Add-Type` once; a failure (Constrained
Language Mode, AppLocker) is reported as "INTERFACE SET NOT WRITTEN: …" with the pointer to the
pre-built sets, and the exe is still written. **The script header and the INTERFACE SET section say
in words what is compiled, by which compiler (Windows PowerShell 5.1: `csc.exe` of the .NET
Framework in `C:\Windows`; PowerShell 7: its bundled Roslyn), that nothing is installed or written
by the compile, and what the alternatives would cost: the same codec in plain PowerShell at
15–30 s per set under 5.1 and minutes under 7 (measured loop speed 0.4 s resp. 3.6 s per million
iterations, ~30 million per set), or copying a pre-built set.** Measured: PowerShell 7 1.5–2.9 s per
set (the first includes the compile), Windows PowerShell 5.1 3.7 s (6.6 s for the whole patch run).

**Pitfalls met (PowerShell):** `GetNewClosure()` blocks cannot call the script's own functions (use
plain script blocks and dynamic scoping); inside `@(a + b, c + d)` the comma binds before `+`
(parenthesise); a block parameter `$w` shadows the width `$W` (names are case-insensitive) — the
blocks now take `$fields`; `return ,$array` from a function is double-wrapped by a caller's `@()`.

**Verification.** For each of the four sizes a scratch game folder with only the inputs was
patched (`-Patches nocd,resolution,hdpaths,cursor,pool,speed,clock,ddraw,camera,sounds`): every text
file of `INTRF_HD`, `exp\intrf_hd` and `ozi_ns\intrf_hd` **byte-identical** to the Python sets
(`hd_sets/<WxH>`, the OZI menu rows applied), every GIF **byte-identical as well** — the LZW
encoder turned out to match Pillow's exactly, and since the version byte is always written as
`GIF87a` (Pillow's behaviour whenever no 89a feature is used; `VICTORY.GIF` is the one GIF89a
source and showed up as a one-byte git diff after the maintainer regenerated 1024×768), a
regenerated 1024×768 set leaves `git status` clean apart from the two movie-name lines — the BMPs
as in §10.25 — `compare_sets.py`: 64
identical, 0 problems per size, also for the set built under Windows PowerShell 5.1. The headless
window run reports the set in its result line. `hd_sets/` is now a test fixture, not a shipping
artefact.

**In place:** `-All -Resolution 1280x800` for Classic and then Council Wars in the real game folder regenerated `INTRF_HD`, `exp\intrf_hd` and `ozi_ns\intrf_hd`; against a snapshot of the folder every text file is byte-identical and every GIF pixel-identical (64/64 after the AVI rule above). **Open:** a game test of a freshly generated set; 1280×720 and 1280×1024 still untested in the game; the four `INTRF_HD\<WxH>\`
folders and `ozi_ns\gamestat\*scene.txt` need committing.

#### 10.27 640×480 gets the movies and OZI fixes too: the exe reads copies the original never touches **(21 Sep 2026, maintainer report "when patching dc16.exe to 640x480 initial video is from council wars. when patching engexp16.exe then ozi missions not possible to patch"; fixed in `patch_movies.py`, `patch_ozi_menu.py`, `gen_apply_script.py`; tested on the command line and in the headless window)**

**Why they were missing.** §10.25 left `movies` out of the stock mode because its ending names live
in the `INTRF_HD` lists, and `ozi` required `hdpaths` because its menu rows live in
`exp\intrf_hd\bintroe`; at 640×480 the exe reads the stock `GAMESTAT` lists and the stock
`exp\intrface\bintroe`, which must stay byte-identical to the CD for the original exes (§10.17).
So the 640×480 Classic build played `avi/intro.avi` — the Council Wars intro in the shared folder —
and the 640×480 Council Wars build could not have the OZI mode at all (`-All` even threw, because a
required fix that does not exist at the resolution was not treated as unavailable).

**The way in.** The three DGROUP path strings end in exactly six letters followed by the next
string: `gamestat/hscene\0gamestat/gscene\0…` (Classic file `0x7FA10`/`0x7FA20`, CW `0x7FC10`/`0x7FC20`)
and `intrface/bintro\0intro.avi…` (file `0x7FC98` / `0x7FE98`, VA `0x482498` in both). The exe appends
`.txt` resp. the language letter, so a six-letter **name** can be swapped in place and the exe reads
a **new file** that the original never opens:

| fix @ 640×480 | exe edit (in place, no `.reloc`) | file the patcher writes | tool |
|---|---|---|---|
| `movies` (Classic) | `intro.avi` → `dcintro.avi` as before **+** `gamestat/hscene` → `gamestat/hscndc`, `gamestat/gscene` → `gamestat/gscndc` (3 edits, 17 bytes) | `GAMESTAT\HSCNDC.TXT`, `GSCNDC.TXT` = the stock lists with `avi/hending.avi` → `avi/dchending.avi`, `aending` → `dcaending` (`Write-StockEndingLists`) | `patch_movies.py --width 640 --height 480` (`find_list_sites`, `LIST_NAMES`; `apply` in a game folder writes the copies too) |
| `ozi` (Council Wars) | the 14 edits + `.reloc` insert as before **+** `intrface/bintro` → `intrface/bintoz` (16 edits) | `exp\intrface\bintoze` and `ozi_ns\intrface\bintoze` (OZI mode reads through the `ozi_ns/` prefix) = the stock CW menu with `Edit-OziMenu`'s rows: x = 228, OZI MISSIONS 392, OZI LOAD 418, QUIT 444 (`Write-StockOziMenu`) | `patch_ozi_menu.py --width 640 --height 480` (`STOCK_MODE_SITES`) |

At HD sizes nothing changes: `hdpaths` points the exe at `INTRF_HD`/`exp\intrf_hd`, whose lists and
menu already carry the names and rows. **Generator:** `movies` and `ozi` joined `MODE_STEPS`
(replayed per mode, `--width/--height` passed; the stock mode too), `HD_STEPS` is back to
`resolution, hdpaths, clock`; identical variants are merged (all HD modes → `Mode = 'hd'`, one
mode → its own entry), so each fix has one 640×480 entry and one shared HD entry; `Requires` and
`Data` are mode-aware (no `hdpaths` requirement at 640×480; `Data` adds the stock lists resp.
`exp\intrface\bintroe` as sources). **Script:** `Get-BuildPatches` includes a resolution's own
variants at 640×480 too (the first version skipped every tagged fix there), `Get-UnavailableFixes`
marks a fix whose required fix does not exist at the resolution, and `Invoke-PatchRun` runs the two
writers at 640×480 after the exe. Reference builds: Classic 640×480 `ec0e6cef…`, Council Wars
640×480 `81d6bc81…`.

**Menu layout at 640×480 (second report, same day: "main menu overlaps image at the bottom - move
buttons a bit higher").** The single column of five rows (340…444) put QUIT (444..469) on the
backdrop's bottom artwork, which starts at row 435 (`exp\intrface\intrg.gif`, measured), and the
column cannot move up because the code-drawn credits box occupies rows 230..330 (`0x4299`, CW
stock y 230, 100 rows): 104 px are free, five rows need 130. The stock script itself carries a
commented-out two-column plan (`%pushb 1 … 138 340`, `%pushb 3/4/5 … 318 …`), so `Edit-OziMenu640`
uses it: NEW CAMPAIGN (138,340), LOAD GAME (138,366) | OZI MISSIONS (318,340), OZI LOAD (318,366) |
QUIT centred (228,392); each button's LARGEBUTTON gadget moves with it, `%pushb 4` / `%gadget 10`
are brought back in, the last row ends at 417. `Edit-OziMenu` dispatches on the script's
`size 640 480` line; HD scripts keep the single column (their backdrop is 768+ rows tall).
`build_ozi_overlay.menu_rows` (Python) has no 640 branch: it never sees a stock-size script.
**Superseded 23 Sep 2026 (§10.35):** the menu script now carries Classic's whole 2x4 grid at every
size, so all eight rows fit above the artwork by construction, the OZI mode is two label renames and
`Edit-OziMenu640` is gone. The reasoning above is why the grid, and not a fifth single-column row, is
the layout the maintainer asked for.

**Checked:** `-All -Resolution 640x480` for both exes in the game folder (fix lists, the four copies
written with the right names and rows, stock `HSCENE.TXT`/`bintroe` unchanged against git),
`-Verify` reports `APPLIED (640x480)`, Windows PowerShell 5.1 parses and verifies, the headless
window lists 8 Classic / 7 Council Wars fixes at 640×480 with nothing greyed out; the HD outputs
are unchanged (1024×768 = the published exes, 1280×800 = §10.24's build). Not yet run in the game.

#### 10.28 Window restore after minimising: the swallowed `SC_RESTORE` and the activation while iconic **(21 Sep 2026, maintainer report "window restore after minimization is not working"; reproduced on this PC with a window-state probe and a thread sampler, root cause traced in both exes; patched — fix `restore`, `tools/patch_restore.py`, both exes; confirmed in game the same day: Alt+Tab, the taskbar button, the Start menu and a taskbar minimise all bring the game back within a frame)**

**Symptom.** Leave the running game with Alt+Tab, the Win key (Start menu) or a click on another
window: DirectDraw's exclusive-mode hook restores the desktop resolution and minimises the game
window (expected). Come back with Alt+Tab or the taskbar button and either the game *stays iconic
although it is the active window*, or — once something does restore the window — a **black window
of the game's size sits at desktop resolution**. The process is alive and busy, `error.log` stays
empty, nothing is logged by Windows.

**Reproduction** (throw-away `scratchpad/probe_minimize.py`: launches the exe, skips the intro with
posted ESC, then per half second logs `IsIconic`, the window rect, `EnumDisplaySettings`, the
foreground window and a screen-DC capture's non-black pixel count; `sampler.py` reads the main
thread's EIP and the game return addresses on its stack through `Wow64GetThreadContext` /
`ReadProcessMemory`). Stock `dc16new.exe` at 1280×800:

```
 19.6  Alt+Tab            iconic=1  mode 1920x1200  fg=Claude            (DirectDraw's hook: desktop mode, minimised)
 24.1  Alt+Tab back       iconic=1  mode 1920x1200  fg=Direct Draw Driver   <- active but still minimised, 4+ s
 31.4  ShowWindow(SW_RESTORE) from outside:
                          iconic=0  mode 1920x1200  fg=Direct Draw Driver   capture: 0 non-black pixels
```

While the game is minimised the main thread runs the frame loop at full speed inside `DDRAW.dll`
(`+0x25995`, a dword copy loop = the software blit of the emulated surfaces) and `restore_surfaces`
is called thousands of times a second (an instrumented build counted ~4 400/s) — the stock game has
no idle path when it is not visible (not changed by this fix).

**Cause 1 — the game has no message loop.** `frame_end` (`0x0042F1C8`, called once per drawn frame
after `present`) pulls posted messages with five range-filtered
`PeekMessageA(NULL, min, max, PM_REMOVE)` calls: `WM_MOUSEFIRST..WM_MOUSELAST` → mouse handler
`0x0042FD78` (or the DirectInput path `0x00450E80`), `WM_KEYFIRST..WM_KEYLAST` → `0x0042FED4`,
`WM_SYSCOMMAND` → if `wParam == SC_SCREENSAVE` return early (the screen saver is suppressed; nothing
else is looked at), `WM_SETCURSOR` → `SetCursor(NULL)`, `WM_DESTROY` → release + `PostQuitMessage`.
There is no `GetMessage`, `TranslateMessage` or `DispatchMessage` in the import table at all (USER32
imports: CharUpperBuffA, CreateWindowExA, DefWindowProcA, DestroyWindow, GetAsyncKeyState,
LoadIconA, LoadImageA, MessageBoxA, PeekMessageA, PostQuitMessage, RegisterClassA, SetCursor,
ShowWindow, UpdateWindow). Sent messages reach the window procedure `0x0042E340` during the
`PeekMessage` calls, posted ones are consumed raw. The last two peeks are dead code: Windows never
*posts* `WM_SETCURSOR` or `WM_DESTROY` (both are sent), and the game imports no `PostMessage`.
Windows restores a minimised window by **posting `WM_SYSCOMMAND` with `SC_RESTORE`** to it (Alt+Tab,
the taskbar button, Win+D undo) — the third peek removes it and drops it. Hence "active but
iconic".

**Cause 2 — DirectDraw re-sets the mode only during a proper activation.** The exclusive-mode hook
that `SetCooperativeLevel(hwnd, DDSCL_EXCLUSIVE|DDSCL_FULLSCREEN|DDSCL_ALLOWMODEX = 0x51)`
(`0x0042E84D`) installs on the window handles `WM_ACTIVATEAPP`: on deactivation it restores the
desktop mode and minimises the window, on activation it re-sets the game's mode — but only when the
window is not iconic at that moment (measured, not read from the DLL: an activation of the iconic
window followed by `ShowWindow(SW_RESTORE)` leaves the desktop mode; `SW_RESTORE` together with
the activation, as an outside `ShowWindow(SW_RESTORE)`+`SetForegroundWindow` does, brings the mode
back). The game's own recovery path cannot substitute for the hook: an instrumented build that
called `IDirectDraw::SetDisplayMode(W,H,16)` from `WM_SIZE`/`SIZE_RESTORED` got the mode back
(`DD_OK`, once), but from then on every `primary->Restore` in `restore_surfaces` (`0x0042E060`)
returned **`DDERR_WRONGMODE` 0x8876024B** for good — surfaces created before the mode loss cannot
be restored after an application-side mode set, they would have to be re-created (the whole
`ddex4.c` init). `SetCooperativeLevel` again made it worse (no mode, and even the outside cycle no
longer recovered); `ShowWindow(SW_RESTORE)` from `WM_ACTIVATEAPP(TRUE)` changed nothing.

**Fix — `patch_restore.py`** (`verify` / `plan` / `apply`, `.restore.bak`, pattern-located, both
exes; patcher fix **`restore`**, after `camera`, before `movies`/`sounds`/`ozi`): the 107 bytes from
the `WM_SYSCOMMAND` peek to the epilogue (Classic VA `0x0042F23D..0x0042F2A8`, file `0x2E63D`;
Council Wars `0x0042F29D`, file `0x2E69D`; the third peek plus the two dead ones) become 86 bytes of
code and 21 `NOP`:

```
push 1 ; push 112h ; push 112h ; push 0 ; lea eax,[ebp-1Ch] ; push eax
call PeekMessageA            (import thunk 0x0047EFD8 / CW 0x0047F038)
test eax,eax ; je epilogue
cmp [ebp-14h],0F140h ; je epilogue          ; SC_SCREENSAVE stays swallowed
push [ebp-10h] ; push [ebp-14h] ; push 112h ; push [ebp-1Ch]
call DefWindowProcA          (thunk 0x0047EFBA / 0x0047F01A)   ; SC_RESTORE, SC_MINIMIZE, ... take effect
cmp [ebp-14h],0F120h ; jne epilogue         ; SC_RESTORE?
push 6 ; push [ebp-1Ch] ; call ShowWindow   (thunk 0x0047EF90 / 0x0047EFF0)   ; SW_MINIMIZE: deactivate
push 9 ; push [ebp-1Ch] ; call ShowWindow                                      ; SW_RESTORE: activate, not iconic
jmp epilogue
```

The minimise/restore pair is the deactivate/activate cycle that the hook handles — the same thing
the outside `ShowWindow(SW_MINIMIZE)` … `ShowWindow(SW_RESTORE)+SetForegroundWindow` did in the
probe: DirectDraw re-sets the exclusive mode, the per-frame `restore_surfaces` succeeds at the next
`BltFast` and the frame is drawn. Calls go through the linker's import thunks (5-byte relative
calls, no absolute operands), so no `.reloc` entry changes and nothing moves; the fix is
resolution-independent (identical bytes in every patcher mode). Space: the Classic AUTO zero tail
is full since fix `camera` (`0x0047F1FD..0x0047F200`, 3 bytes) — the two dead peeks were the free
room here.

**Second part — no CPU burn while minimised (same day, maintainer: "fix cpu burn without stopping
the game when minimized").** Game ticks are clock-driven; the main loop's only pacing was the `Flip`
at the end of `present` (`0x0042E0FC`), and while minimised `present` never gets there: `BltFast`
fails with `DDERR_SURFACELOST`, `restore_surfaces` fails with `DDERR_WRONGMODE` and the routine
returns at `0x0042E14F` (`jne exit`) — a full core spent on ~4 400 useless passes per second. That
`jne` now goes to a 12-byte stub in the spare tail of the rewritten block above (`0x0042F293`, CW
`0x0042F2F3`): `push 1 ; call Sleep (thunk 0x0047F008 / CW +0x60) ; jmp exit 0x0042E2A1`. One system
timer period (≤ 16 ms) per pass while the surfaces are lost, nothing when they are not; ticks,
message pumping and the restore attempt still run every pass, so a multiplayer client keeps echoing
frames. Measured on the 1280×800 Classic build: **27.8 % of a core visible at the menu, 5.9 %
minimised** (was ~100 %), 486 of 524 thread samples inside the wait, `frame_end` still hit every
sample; Alt+Tab back afterwards as before. The stub adds a second edit to the fix (the 6-byte `jne`
operand; `patch_restore.py` also upgrades an exe that carries the morning form without the stub).
Smoke-tested by the maintainer on the Fly relay the same evening (minimised client stays in the battle), then committed; the published 1024×768 exes with both parts are SHA-256 Classic `ca488306…`, Council Wars `2ae4e2e9…`.

**Experiments, in order** (test builds `dc16test.exe`, deleted afterwards): dispatch only → the
window restores, mode stays 1920×1200, an outside minimise/restore cycle then recovers it fully;
`+ SetDisplayMode` on `WM_SIZE` → mode back, black, `DDERR_WRONGMODE` forever; `+ SetCooperativeLevel`
→ no mode, outside cycle no longer helps; `+ ShowWindow(SW_RESTORE)` on `WM_ACTIVATEAPP(TRUE)` → no
mode; **dispatch + cycle on `SC_RESTORE`** → restored, mode back, `Restore`/`BltFast` return 0, the
menu with its running credits animation is visible again within one frame.

**Verified 21 Sep 2026** on the 1280×800 builds of both exes: Alt+Tab away and back; outside minimise
+ posted `SC_RESTORE` (the taskbar button); Win key (Start menu takes the foreground, hook minimises
the game), Esc, posted `SC_RESTORE`; posted `SC_MINIMIZE` (taskbar click on the active game) +
Alt+Tab back. Every route: iconic 0, mode 1280×800, menu drawn, `error.log` empty. Not covered:
the intro movie player has its own wait loop (`0x0040903E`, `PeekMessage(WM_KEYDOWN)` + `Sleep`)
and was not tested; the window procedure's own `SC_SCREENSAVE` path (a *sent* one: logs "TRIED TO
ACTIVATE SCREEN SAVER" and exits) is unchanged; the busy loop while minimised is unchanged.

#### 10.29 Sound files fail from a deep folder: the wave loader's `OpenFile` and its 128-character path **(22 Sep 2026, found while running a patched build from a 161-character folder path; fix `longpath`, `tools/patch_longpath.py`, both exes)**

**Symptom.** Run either patched exe from a game folder whose path is long (the Claude scratchpad,
`...\scratchpad\lp\game\DC - Council wars`, 161 characters; a repository ZIP extracted under
`Downloads` and moved one or two folders deeper gets there too) and about five seconds after start,
during the intro movie, the box **"FILE NOT FOUND / A sound file is missing - see error.log"**
appears and the game exits (the `nocd` wave-loader exit, §10.19). `error.log` holds one line
`unable to open file ` with an **empty name**. The same files under a short path (`subst Q:`) run
without a hitch, and every other loader had already opened dozens of files from the long folder
(display mode set, palette, the intro AVI playing).

**Cause.** The wave loader (`0x00452A50`, CW `0x00452AB0`: `sound2.dat` banks at start-up, briefings,
ambience, `beat.wav`) is the **only** code in the game that opens files through the Windows 3.1-era
**`OpenFile(name, &OFSTRUCT, OF_READ)`** (KERNEL32 IAT slot `0x004804C8`; four call sites
`0x00452AC4`/`0x00452ADD`/`0x00452B4D`/`0x00452BBD`, all in this function; the thunk `0x0047EEBE`
is unused). `OpenFile` writes the file's full path into `OFSTRUCT.szPathName[OFS_MAXPATHNAME]`,
**128 bytes**, and returns `HFILE_ERROR` (-1) when the full path does not fit - so every WAV fails
once `<game folder>\sound\xxxxxxxx.wav` exceeds about 128 characters. Everything else goes through
the Watcom C runtime (`fopen` → `CreateFileA`), which has the normal `MAX_PATH` limit. The loader's
frame: `sub esp,890h; sub ebp,82h`; `[ebp-80Eh]` = 1024-byte buffer 1 = prefix (`0x487DC0`, empty in
Classic since fix `sounds`, `exp/` in CW) + name, `[ebp-40Eh]` = buffer 2 for the CD path attempts,
`[ebp-0Eh]` = the 136-byte OFSTRUCT, `[ebp+7Ah]` = bytes-read dword. Sequence: `OpenFile(buffer 1)`,
on failure `OpenFile(name)`, then (stock) two CD attempts into buffer 2, then the error exit
`fprintf(error.log, "unable to open file %s\n", buffer 2)` + display shutdown + `Sleep(2000)` +
`MessageBoxA` + `exit`. Since `nocd` turned the `jne` after the second open into a `jmp` past the
CD attempts (`0x00452AE9`), buffer 2 is never filled - hence the empty name. On success the handle
goes to `_llseek(h,0,FILE_END)` / `_llseek(h,0,0)` (`0x00480544`), `ReadFile` (`0x004804D4`) and
`_lclose` (`0x00480540`), all of which take a plain Win32 handle.

**Fix (`tools/patch_longpath.py`, 4 edits = 57 changed bytes per exe, nothing moves, no `.reloc`
change, register-relative operands and a call through the linker's import thunk only; Council
Wars = Classic + 0x60, pattern-located):**

| # | Classic VA (file) | CW VA | bytes | edit |
|---|---|---|---|---|
| 1 | `0x452AB7` (`0x51EB7`) | `0x452B17` | 20 | `push 0; lea eax,[ebp-0Eh]; push eax; lea eax,[ebp-80Eh]; push eax; call cs:[OpenFile]` → `lea eax,[ebp-80Eh]; call open_read; 9×nop` |
| 2 | `0x452AD6` (`0x51ED6`) | `0x452B36` | 14 | `push 0; lea eax,[ebp-0Eh]; push eax; push ebx; call cs:[OpenFile]` → `mov eax,ebx; call open_read; 7×nop` |
| 3 | `0x452AEF` (`0x51EEF`) | `0x452B4F` | 22 | **`open_read`** over the first CD attempt (dead since `nocd`; nothing else targets it): `push 0` (template), `push 0` (flags), `push 3` (`OPEN_EXISTING`), `push 0` (security), `push 1` (`FILE_SHARE_READ`), `push 80000000h` (`GENERIC_READ`), `push eax` (name), `call` CreateFileA thunk (`0x0047EF3C` / CW `0x0047EF9C`, IAT slot `0x004803F8`), `ret` |
| 4 | `0x452BCD` (`0x51FCD`) | `0x452C2D` | 4 | error exit `lea eax,[ebp-40Eh]` → `lea eax,[ebp-80Eh]`: error.log and the box name the file that was tried (`prefix+name`) |

`CreateFileA` returns `INVALID_HANDLE_VALUE` = -1 on failure, the value the loader's two `cmp
eax,-1` tests already expect, so the control flow is unchanged. The OFSTRUCT is no longer written
and nothing reads it. `OpenFile` also searched the exe folder, the current folder, the Windows
folders and `PATH` for a bare name; the game's WAV names always carry a folder or sit in the game
root, which is the current folder, so `CreateFileA` finds the same files. The tool plans and
verifies on a stock exe too (the generator takes every plan on the untouched original) but
**refuses `apply` without `nocd`**: with the stock `jne` the second open's failure would fall into
the stub and its `ret` would pop garbage. Patcher fix `longpath` (`Requires nocd`), applied after
`restore` and before `movies`/`sounds`/`ozi`.

**Verified 22 Sep 2026** with a copy of the game folder at the 161-character path: the unfixed CW
1280x720 build shows the box at 5.1 s (`error.log`: `unable to open file `), the fixed one plays
on (14 s sampled, `error.log` empty); both builds re-verified byte-exact through the regenerated
patcher (published 1024x768 exes Classic `71b570fa…`, CW `d4ca8555…`; old published + tool =
the same bytes). Not fixed and not affected: the Watcom runtime's own `MAX_PATH` (260) limit.

**Fifth edit, 25 Sep 2026 (after the Linux/Wine report of §10.42): the sound table's name loses its backslash.**
`sound\sound2.dat` (DGROUP Classic `0x485BA0` = file `0x833A0`, CW `0x485BA8` = file `0x835A8`; opened by the
sound module's table loader `0x4309C8` through the prefix helper at start-up) is the only path string in
either exe written with a backslash separator - every other file name uses `/`. The byte at +5 becomes `/`
(`5c` -> `2f`, one data byte, no code, no `.reloc`; Windows treats both separators alike). `patch_longpath.py`
locates the string in DGROUP in either form, identifies the build by the AUTO section's raw size so that the
published exes with the appended `.dcicon` section (742912 / 743424 bytes) are accepted, and `apply` upgrades
an exe that carries the four code edits without the byte (the 22-25 Sep 2026 builds) by that one edit. The
generator's `blocks_longpath` takes 5 edits (lengths 20/14/22/4/1). Published 1024x768 builds since then:
Classic **`89744c45…`** (was `e9cc0561…`), Ultimate **`2da5c86a…`** (was `d1a9b518…`) - the tool-upgraded
exes and the regenerated patcher's `-All -Resolution 1024x768` rebuild from the originals are byte-identical
under PowerShell 5.1 and 7 (scratch copy of the repository, the map editor build unchanged `c72dd205…`).
Not run in the game (a data byte in a name that Windows resolves either way); not tested under Wine (no Linux
machine here) - it removes the one difference, whether it was the cause on the player's machine is open.

#### 10.30 The patcher window's popups: in progress, succeeded, failed **(22 Sep 2026, maintainer request "in patcher gui - show a popup when patching is in progress and when it succeeds and when it fails"; `gen_apply_script.py` → `Apply-DarkColonyPatches.ps1`, window only, the command line is unchanged)**

Until now *Apply selected fixes* only changed the two-line log label at the bottom of the window, and
during an HD run (the exe plus the ~60-file `INTRF_HD` set, 3-5 s, longer the first time the C# GIF
codec is compiled) the window simply froze. Now:

* **In progress.** `Invoke-PatchRun` takes an optional `$Progress` script block and calls it with one
  line before each step (`Applying fix 3 of 12: 'hdpaths' (…, 30 edits)...`, `Writing dc16new.exe
  (659456 bytes)...`, `Writing the 1280x800 interface set into INTRF_HD …`). The window passes
  `$script:gui.Progress`, which writes the line into the log label and into a small owned form
  "Patching in progress" (fixed dialog, no control box, marquee bar, the current step as text).
  The run is synchronous on the UI thread, so the box is repainted by hand (`Refresh()` +
  `Application.DoEvents()`) and the main window is disabled with a wait cursor until the `finally`
  closes the box — a modal box could not be opened and closed by the same code path, and without
  the disabled owner a second click on *Apply* during `DoEvents` would re-enter the handler.
* **Outcome.** One message box after the run: *Patching succeeded* (information icon: the file
  written, size, fixes, SHA-256 and the "byte-identical to the exe published in the repository" /
  "to the reference build for WxH" note, "Start the game with this file"), *Patched, but the result
  differs from the reference build* (warning, asks for a report with the original's SHA-256),
  *Patching failed* (error: the exception text and "Nothing was written to …" — the bytes are
  checked before anything is written, so an exception means no file; **or** the exe was written but
  `Write-InterfaceSet` failed, which `Invoke-PatchRun` reports as a `NOT WRITTEN` line in
  `Generated` — that case is an error box too, the game would fail at start-up), *Nothing to do*
  (no fix ticked, empty output path, output = original) and the existing *Prerequisites missing*
  box. The log label keeps the same text as before.
* **Testability.** Every box goes through `$script:gui.Notify` (`param($text, $title, $icon)`,
  default `MessageBox.Show`); the headless test replaces it with a recorder and runs `& $script:gui.Apply
  $true`, while `$false` (the old `$confirmOverwrite` parameter, now `$interactive`) skips the
  overwrite question and every popup. Run under pwsh 7 and Windows PowerShell 5.1: all 12 Classic
  fixes at 1280x800 → `371cf781…` = `dc16new.exe` with the busy form closed and the window
  re-enabled afterwards; the refused, failed (output folder does not exist → `WriteAllBytes`
  throws), partial (`nocd` only at 640x480) and silent paths. Pitfall: in a double-quoted string
  `fixes$modeText:` parses as the scoped variable `$modeText:…` (a `ParserError` when the script is
  loaded) — write `${modeText}:`.
* **No output selection (same day, maintainer request "disable resulting path and filename
  selection").** The "Write to" box became a read-only "Written to" display and its `...`
  `SaveFileDialog` button was removed: the window always writes the build's `OutputName`
  (`dc16new.exe`, `engexp16new.exe`, `maped_ozi_ns_v1.2.exe`) beside the original — or, when the
  player browsed to an already patched exe and the window redirected the input to the untouched
  original beside it, that browsed file. The game needs its data files next to the exe and every
  README, test and shortcut names these files, so a free choice only produced exes in the wrong
  folder. The command line keeps `-Output` for special cases.

**Known wart, FIXED 25 Sep 2026 (found 22 Sep 2026 while rebuilding the game-folder exes from a tool session):
a relative `-Output` was resolved against the PROCESS working directory, not PowerShell's location.**
`Set-Location` changes only PowerShell's `$PWD`; `[System.IO.Path]::GetFullPath($Output)` (the
`$gameDir` derivation at the CLI entry, the `Get-DataProblems` folder, `Write-InterfaceSet`'s target)
and `[System.IO.File]::WriteAllBytes($OutputPath, ...)` in `Invoke-PatchRun` use .NET's
`Environment.CurrentDirectory`, which a `Set-Location` in the same session does not move (it stays
where the shell was started). `Test-Path` / `Resolve-Path` on the same `-Output` DO use `$PWD`, so the
two halves disagree: in the observed run (`Set-Location <repo>; .\Apply-DarkColonyPatches.ps1 -Original
"DC - Council wars\dc16.exe" -All -Output "DC - Council wars\dc16new.exe" -Overwrite`) the resource
check looked in `<process cwd>\DC - Council wars\` and greyed out `resolution`, `hdpaths`, `clock`,
`music`, `movies`/`ozi` as `RESOURCES NOT FOUND`, the remaining fixes were applied, and the write then
failed with `Could not find a part of the path`. Nothing was written, so the wart is harmless but
confusing; from an interactive shell started in the repo folder it never shows. `-Original` is safe
because the entry point runs `Resolve-Path` on it first. **Fix (in `gen_apply_script.py`, the emitted
CLI entry right after the `$Output` default is chosen):** normalise once with
`$Output = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)` (works for
files that do not exist yet, honours `$PWD`), and do the same for the window's redirected output path;
then every later `GetFullPath`/`WriteAllBytes` sees an absolute path. Regenerate and re-run the CLI
test with a relative `-Output` from a different `Set-Location`.
**Done 25 Sep 2026:** helper `Get-AbsolutePath` (that call) applied to `-Output` right after its default
and to the path the window loads (the redirected output derives from it). Test: process directory
`C:\Windows`, `Set-Location` elsewhere, relative `-Output` - the old script failed as described, the new
one writes; a full `-All -Resolution 1024x768` run that way gave the published `a71d038b…` with no fix
skipped (5.1 and 7). The same day `INSTALL.CMD` (starter, first named `Apply-DarkColonyPatches.cmd`: `powershell -NoProfile
-ExecutionPolicy Bypass -File` on the `.ps1`, because *Run with PowerShell* obeys the execution policy and
refuses a script from the downloaded ZIP) became the documented way to start the patcher, and the patcher gained a desktop shortcut to the written exe (checkbox "Desktop shortcut", `-DesktopShortcut`, start folder = game folder); plan §16.

#### 10.31 CD music: how the game plays its soundtrack, why it was silent, and the MP3 player that replaced the CD-audio module **(22 Sep 2026, maintainer request "investigate how to add original music from CDs", then "extract the tracks and encode them to mp3 at 192 kbit/s and update patcher to use original tracks in corresponding executables"; fix `music`, `tools/patch_music.py`, both exes; option 2 below was implemented the same day)**

Both game CDs are mixed-mode discs: track 1 is the data track, tracks 2..5 are Red Book audio - the
soundtrack was never a file in the game folder, so no repository copy carries it. Scanned from the
raw `.bin` images in `Documents` (2352-byte sectors; a sector is data when it starts with the 12-byte
sync `00 FF×10 00`, the audio part is raw 16-bit stereo 44.1 kHz little-endian PCM, byte order
verified by the sample-to-sample roughness of both interpretations; there is no `.cue`, so the track
boundaries below are the runs of digital silence of 4-6 s between the pieces - the pressing's pregaps):

| Disc | Audio sectors | Track 2 | Track 3 | Track 4 | Track 5 |
|---|---|---|---|---|---|
| Dark Colony (`Dark Colony.bin`, 283 901 sectors) | 238 019..283 901 = 10.2 min | 238 170..252 472, 3.18 min | 252 765..258 452, 1.26 min | 258 847..268 631, 2.17 min | 269 090..283 447, 3.19 min |
| Council Wars (`… Council Wars.bin`, 309 830 sectors) | 251 486..309 830 = 13.0 min | 251 636..262 763, 2.47 min | 263 064..279 374, 3.62 min | 279 676..293 801, 3.14 min | 294 103..309 530, 3.43 min |

**The player in the exe** (`cdaudio` module, Classic `0x004510D0..0x00451820`, CW +0x60 - the only
user of the `WINMM.dll` import `mciSendCommandA`, IAT `0x00480570`, 34 call sites): a plain MCI
CD-audio library - `cd_open` `0x004510D0` (`MCI_OPEN` with `MCI_OPEN_TYPE|MCI_OPEN_SHAREABLE`,
device type string `cdaudio` at `0x00487C1C`), `cd_play_from_here` `0x0045110C` (`MCI_SET` TMSF +
`MCI_PLAY` with neither `MCI_FROM` nor `MCI_TO` = play to the end of the disc), `cd_close` `0x00451158`,
`cd_stop` `0x00451188`, `cd_tracks` `0x00451408`, `cd_current_track` `0x00451444`,
`cd_seek_track` `0x004515B8` (TMSF, `MCI_SEEK` `MCI_TO` track, then `MCI_PLAY` without range),
`cd_read_toc` `0x0045164C` (track count into `0x0053280C`, capped at 20; MSF starts into `0x005327D0`),
`cd_mode` `0x004517B0` (`MCI_STATUS_MODE` → 1 open/no disc, 2 not ready, 3 stopped, 4 error, 0 = fine).
Above it sits `ddex4.c`'s music layer (device id `0x00489744`, "no CD audio" flag `0x00489748`,
CW `0x0048976C` / `0x00489770`), installed as vtable slots of the display object at `0x00489720`
(`0x0042FBF0`ff) and copied into the display wrapper (`0x0042C514`ff) as slots **+0xB8 open, +0xBC
play(track), +0xC0 stop, +0xC4 start, +0xC8 poll, +0xCC track count**:

| Method | Code | Called from | What it does |
|---|---|---|---|
| open | `0x0042F9F8` | `main.c` start-up `0x00404E60` (right after the settings are copied) | `cd_open`; on failure flag `0x00489748 = 1`, device `-1` → every other method returns at once |
| start | `0x0042FA9C` | `proto.c` game-start init `0x0041F09F` (last thing before `ui` is returned) | **seeks to track 2 and plays** - the playlist passed in `eax` (`0x004A46C0`) is overwritten and ignored |
| poll | `0x0042FAC0` | client frame `0x004320DB`, once per 5000 ms (`0x1388` timer at `0x004EB71C`) | `cd_mode`; when 3 (stopped = disc played to its end) or 4 (error): close, re-open, seek track 2, play - i.e. the whole disc from track 2 loops for the entire battle |
| stop | `0x0042FA80` | end of battle `0x00401A41`, main-menu button `0x0C` `0x0040502D` | `MCI_STOP` |
| play(track) | `0x0042FA4C` | nobody (slot copied, never called) | seek + play one track |

So the 1998 build's music is simply "CD from track 2 to the end, repeat", started when a battle
begins and stopped when it ends; the menus are silent. **The designers meant more:** every entry of
the campaign scene lists `GAMESTAT/HSCENE.TXT`, `GSCENE.TXT`, `HTSCENE.TXT`, `GTSCENE.TXT` and CW
`exp/gamestat/hxscene.txt`, `gxscene.txt` ends with a **per-mission playlist line** - `%d` values
until `-1` (`2 -1`, `2 3 5 -1`, `4 5 3 2 -1`, the training lists `7 1 2 -1`), parsed by `scenario.c`
`0x00429BC0`ff (`strtol` loop, assert `i < MAX_PLAYLIST_TRACKS` = 10, line 1260) into the bytes
`0x004A46C0..` with the count at `0x004A46CC` and cleared per mission at `0x004298D2`; the scene
entry before it is `name / map / two titles / two AVIs / "%d %d %d" / "%d"`. The music code never
reads the list (the `start`/`poll` methods take it as an argument and discard it), which is why every
mission sounds the same. The multiplayer `.SCN` files have no playlist (the `.SCN` reader `0x0041BAF0`
is a different parser, §6 of `DC16_MAP_FILES.md`).

**Volume.** The options screen's music `-`/`+` widgets `0x43`/`0x44` (`0x00432E86`/`0x00432EB3`,
range 0..10) call display slot **+0xE8** = `set_volume(level, method)` `0x004527F8` with method 0 =
**aux**: `0x00452870` walks `auxGetNumDevs` for the device whose `AUXCAPS.wTechnology == AUXCAPS_CDAUDIO`
and calls `auxSetVolume(level·0x1800 per channel)`; the sound widgets `0x2A`/`0x2B` use method 1 =
the mixer line `MIXERLINE_COMPONENTTYPE_SRC_WAVEOUT` (`0x00452580`, flag `0x0048C180`). Modern
Windows reports **zero aux devices** (`auxGetNumDevs() == 0` on this PC), so the music slider has
been a no-op for twenty years even with a CD in the drive.

**Why it is silent on today's PCs.** `mcicda.dll` still ships (32-bit `SysWOW64\mcicda.dll` present on
this Windows 11), but `MCI_OPEN cdaudio` fails without a CD-ROM drive (MCIERR 266 on this PC) → the
"no CD audio" flag is set at start-up and every music call returns immediately, with no message.
Windows' own `Mount-DiskImage` mounts ISO/VHD only, so the mixed-mode `.bin` cannot be presented as a
disc without a third-party virtual drive - and the `nocd` fix is unrelated: it removed the *file*
accesses to `<letter>:\dc\`, while MCI picks the first CD-ROM drive on its own.

**Ways to bring the music back** (assessment; the maintainer decides):

1. **Ripped tracks + a `winmm.dll` wrapper in the game folder** (no exe change). `WINMM.dll` is not a
   KnownDLL, so a `winmm.dll` beside the exes is loaded first. The wrapper must speak the *binary*
   `mciSendCommandA` interface (the game never uses `mciSendStringA`), implement `MCI_SEEK` + TMSF and
   let `MCI_PLAY` without `MCI_TO` run through the remaining tracks, answer `MCI_STATUS_MODE` with
   `MCI_MODE_STOP` at the end (the 5-s poll then restarts from track 2 - the original looping), and
   survive the game's close/re-open cycle. **The classic `ogg-winmm` fails this** (its `wav-winmm.c`
   has no `MCI_SEEK`, plays one track per `MCI_PLAY` and never loops - with Dark Colony it would play
   track 2 forever or nothing). **`Direct-WinMM`** (github.com/Jione/Direct-WinMM, CC0, v2.3.2 of
   Nov 2025, x86 build 450 KB: `winmm.dll` + `WinmmVol.exe`) handles `mciSendCommandA`, `MCI_SEEK`,
   TMSF, `MCI_TO`-less play to the last track, `MCI_MODE_STOP`, unconditional open without a drive,
   and even the aux functions (`auxGetNumDevs` = 1, `auxSetVolume` → its engine volume, so the music
   slider would work again). Tracks are looked up in the **current directory** as
   `music\Track%02d.wav|ogg|mp3|flac` (fallback: the sub-folder holding the most `*NN.ext` files).
   Open point: **both exes share one folder but the discs differ** - the wrapper has no per-exe
   folder, so either one soundtrack is shipped for both games (CW's 13 min vs Classic's 10; the
   game only ever plays "everything from track 2") or the tracks of both discs are combined into one
   `music\` set (eight tracks - the loop plays them all). Unverified in game: the wrapper's
   `MCI_MODE_STOP` after the last track and its behaviour under the game's close/re-open every 5 s.
2. **Patch the exes to play files themselves** (no third-party DLL): rewrite the then-dead
   `cdaudio` module in place (1.9 KB of room, both exes, +0x60) so `open` = `MCI_OPEN` with
   `MCI_OPEN_ELEMENT` `music\track%02d.mp3` (device `mpegvideo`: `mciqtz32.dll`, `l3codeca.acm` and
   `mciwave.dll` are all present in `SysWOW64`; verified here that `open … type mpegvideo`, `play`,
   `status mode` and **`setaudio volume`** work), `poll` advances to the next file on
   `MCI_MODE_STOP`, and - the real gain - **the scene lists' playlists are finally honoured** (the
   parsed bytes at `0x004A46C0` are there; multiplayer maps would take a default list; the two games
   could name different folders, `music\` vs `exp\music\`, through the same `exp/` prefix slot the
   wave loader uses). The music slider becomes `MCI_DGV_SETAUDIO volume`. More work (a small state
   machine, pattern-located, a patcher fix with `Data` = the track files) but the whole chain stays in
   the repo's own tools.
3. **Virtual CD-ROM drive** with a BIN/CUE image (WinCDEmu, Daemon Tools …): the stock code works
   unchanged, volume still dead, every player must install a driver and a `.cue` has to be written
   (the tracks' boundaries above). Not recommended beyond a personal test.

**Implemented (option 2, the same afternoon).** The eight tracks were sliced out of the `.bin`
images at the sector ranges above (raw PCM, no resampling) and encoded with LAME (`lameenc`, CBR
192 kbit/s, quality 2) into **`MUSIC/TRACK02.MP3..TRACK05.MP3`** (Dark Colony, 14 MB) and
**`exp/music/track02.mp3..track05.mp3`** (Council Wars, 18 MB), committed to the game repository.
`tools/patch_music.py` (verify / plan / apply, `.music.bak`, pattern-located, both exes; patcher fix
**`music`**, `Data` = the four tracks of the game, no `Requires`, order `... longpath, music, movies/sounds | ozi`)
rewrites the cdaudio module `0x004510D0..0x00451820` (0x751 bytes, CW +0x60) in place as an MP3
player on the MCI **`mpegvideo`** device - `mciqtz32.dll` + `quartz.dll` + `mp3dmod.dll`, present in
`SysWOW64` of every Windows since 98, through the exe's own `mciSendCommandA` import, nothing new
imported. The seven entry points the `ddex4.c` layer calls keep their addresses, so the layer and
its callers are untouched:

| Entry (module +) | New meaning |
|---|---|
| `cd_open` +0x000 | reads the saved music level (`0x00488DE8` / CW `0x00488E10`, 0..10) x100 into the state, opens `TRACK02` (not playing) - fails (0 = the layer's "no CD audio" flag, silence for good) only when the file is missing |
| `cd_play_from_here` +0x03C | no-op (`open_track` + `play_dev` already play) |
| `cd_close` +0x088, `cd_stop` +0x0B8 | `MCI_CLOSE` / `MCI_STOP` of the open element |
| `cd_tracks` +0x338 | 0 (dead caller) |
| `cd_seek_track(dev, dl=t)` +0x4E8 | close, open `TRACK0<t>`, `MCI_PLAY` asynchronous to the end; returns t+1 or 0 |
| `cd_mode` +0x6E0 -> `impl_mode` +0x240 | `MCI_STATUS MODE`: playing -> 0; `MCI_MODE_STOP` (file ended) -> open+play `cur+1`, or `TRACK02` when that file does not exist (= the disc's "everything from track 2, repeat"); no element / MCI error -> 4 (the layer closes and re-opens) |
| helpers | `close_state` +0x0E0, `open_track` +0x110 (builds the name from the 24-byte template by overwriting its digit - no sprintf), `play_dev` +0x1A0, `setvol_dev` +0x200 (`MCI_SETAUDIO` item `MCI_DGV_SETAUDIO_VOLUME` 0x4002, flags `ITEM|VALUE` 0x01800000, 0..1000); strings `"mpegvideo"` +0x1D0, template `music\track0?.mp3` / `exp\music\track0?.mp3` +0x1DC; the rest of the 0x751 bytes zero |

State = the dead TOC array of the old module (`.bss 0x005327D0`, both exes: +0 device id, +4 track,
+8 volume, +12 the built name). The aux volume walk `0x00452870` (CW `0x004528D0`, 110 bytes, the
music slider's `set_volume(level, 0)`) becomes 26 bytes: store level x100, `setvol_dev` on the open
element - **the music slider works again** and the level is re-applied to every track opened. The
22 + 2 absolute operands of the new code take over HIGHLOW `.reloc` entries of the old code in the
same pages (page 0x451000 had 41, page 0x452000 3); the 20 left over become type 0. All registers
but eax are preserved (Watcom register convention; `mciSendCommandA` is stdcall and clobbers
ecx/edx - the first build stored the track number *after* the call and got garbage, found in game by
reading the state block: the store comes before the call now). The mode/flag constants and the
end-of-file behaviour (`MCI_MODE_STOP` 0x20D after the last sample, a second `MCI_PLAY` then does
nothing - hence close/re-open per track) were verified on this PC through the string interface
before assembling. The scene-list playlists stay ignored (their numbers - track 1, 7 - do not fit the
shipped discs). Assembled once with keystone (dev script
`Dark-Colony-development/scratch/music_asm.py`); the tool ships the bytes with a fixup table and
needs only the stdlib.

Verified 22 Sep 2026 on the 1280x800 Classic build in the game folder: at the main menu the process
had loaded `mciqtz32.dll`, `quartz.dll`, `mp3dmod.dll`, `l3codeca.acm` (the start-up open), in the
demo battle the state block read `dev 1, track 2, volume 500, music\track02.mp3` and the process's
audio session was active; the patcher (`-All -Resolution 1280x800`) reproduces the tool's output
byte for byte. Published 1024x768 exes: Classic `5a2e10b7...`, CW `d7b30c1a...` (game-folder 1280x800
builds `fb0c542d...` / `f555d211...`). Not yet watched: the transition to track 3 after 190 s and a
Council Wars run; one Classic test run ended (exit 0, no error.log, no crash event) while the patcher
was rewriting the INTRF_HD set in the same folder, another right after an Esc that probably reached
the main menu (Esc = quit there) - re-test before blaming the module.


#### 10.32 32:9 (super ultra-wide) screens: the view wider than the map, a pillarboxed movie, and a dgVoodoo test rig **(22 Sep 2026, maintainer request "investigate how to add support for 32:9 (Super Ultra-Wide) screen and test it using voodoo virtual video card"; assessment plus one tool change (`patch_resolution.py` movie rectangle); nothing changed in the published exes or the patcher; 3840×1080 run on this PC through dgVoodoo 2 up to the main menu, no battle yet)**

**Modes.** 32:9 monitors are 3840×1080 (DFHD) and 5120×1440 (DQHD); 3840×1200 is the 32:10
variant. What the tool does with them today (`patch_resolution.py plan` on the stock Classic exe):

| mode | map view | slack rows | lightmap | edits | status |
|---|---|---|---|---|---|
| 3840×1080 | 3712×1024 = **116×32** tiles | 24 → HUD bar 50 px | stride 512, frame `0x14CC → 0x87FC` (34 732 B) | 178 | plans and runs (below) |
| 3840×1200 | 116×36 | 16 | 512, `0x97FC` | 178 | plans |
| 2560×1080 (21:9) | 76×32 | 24 | 512 | 178 | plans |
| 5120×1440 | 156×44 | 0 | **refused**: 156 tiles need a 1024-byte row stride (`shl …,7`; `LIGHTMAP_WIDE_STRIDES` stops at 512, cap 126 tiles); the frame would be ~92 KB, inside the 256 KB commit the patch sets | — | one constant + a re-check of the §10.5 parity argument |

**The blocker is the map, not the exe.** Every shipped map is 64, 96, 108, 112, 128 or 160 tiles
wide (`.MAP` headers, 292 files): 64×56 = the 14 training maps + `D2PLAY07`/`J2PLAY09`; 96×84 =
campaign missions 1–2 of both races, 18 + 4 two-player maps, `ALIEN01/02`, `HUMAN01/02`, three OZI
maps; 112×98 = missions 3–8; 128×112 and 160×140 = the rest. A 3840 screen shows 116 tiles, a 5120
screen 156: the view is wider than every 96- and 64-tile map, and at 5120 wider than everything but
the 160-tile maps. (The exposure already exists for 2560×1440/2560×1600 — 76 tiles > 64 — on the
16 small maps; the patcher does not offer those modes.) `proto.c` computes the camera bounds as
`[half, map − half]` (`0x41EE66..0x41EEA9`) and `clamp2d 0x436668` applies min first, then max, so
with `half > map/2` the camera sits at `max` and the view starts at a negative tile x (116 on 96:
tile −20). Two independent reading passes over `dc16.asm` (22 Sep 2026) traced what each consumer
then does; nothing clips to the map by itself, every consumer relies on the camera bounds:

| consumer | wider than the map | taller than the map | guard today |
|---|---|---|---|
| tile drawer `0x45011C` (bg/fg tiles + occlusion mask, called from `draw_objects 0x4544A8`) | flat pointer `map+4[0] + 4·(w·ty + tx)`: off-map columns continue into the neighbouring row (the far side of the map drawn shifted by a row); at row 0 / row h−1 the read leaves the tile block into the `smalloc` header → tile ids > 1415 → wild image pointer → **AV in the blitter** (`0x450316` or `0x48C14C/5C`) as soon as the camera reaches the top or bottom edge | same | none |
| `draw_terrain 0x453910` (lightmap pass, loop 1 `0x453A40..0x453B2D`, rows/cols −1..n) | reads `[row + col·4]` before/after the row: garbage light on the off-map strip, no fault | `view_ty < 0` → row index ≥ h → NULL row pointer → **AV `0x453ADE`** | equality flags for the ±1 border only (`0x4539DD..0x453A3B`) |
| vision/draw-list scan `0x4396D4` (rect from `clip_view_to_map 0x435E24`, which does **not** clip: `make_rect 0x4365BC` is four stores) | neighbour-row words, a wrong object may count as visible; header words at the block ends | **AV `0x439D5E`** (§10.2) | none |
| ambience pick `0x445AA4` | origin test only (`0x445ABD..0x445AE0`): `x0 < 0` → returns 0 → terrain class 0, **silent** | `z0 + 23 > h` → **AV `0x445B52`** (§10.22) | start corner only |
| minimap view box `0x43A064` (`0x43A19C..0x43A2D1`) | rect-intersect `0x436528` + the frame primitive `0x42BF5C` clip to the minimap → the box degenerates to the minimap border, **safe** | safe | yes |
| `draw_objects 0x4543FC` | iterates sprites, culls against the view — **safe** | safe | n/a |
| mouse pick `0x409850` / `0x4350D4` | tile bounds tested before any row lookup — safe; but a click on off-map ground gives pick −1 and a **spot order with the world point stored as 16-bit words** (`0x40969F/A6`, no range check: −0x500 → tile 251); what `begin_move 0x414FD0` does with it was not traced | same | partial |
| scrolling `0x40AE73..AC`, minimap drag `0x40A1D4/DA` | no own test, repaired by `clamp2d` at `0x40AF16` before rendering | same | via clamp |

Only two callers of `clamp2d` exist (`0x40AF16` and the `camera` stub `0x47F1F3`); the bounds are
read by them and the save writer `0x43BC98` only.

**Three ways to support 32:9, in ascending order of work:**

* **A. Cap the map view** at the smallest map the build must run (64 tiles = 2048 px; 96 tiles =
  3072 px if the training maps and the two small MP maps are given up) and hand the remaining
  columns to the HUD. `Geometry(viewport=…)` already builds such an exe (`--viewport 2048x1024`;
  `dc16uwc.exe` below plans clean, 178 edits) — what is missing is the HUD side:
  `hud_layout.target()` grows the right panel only by the frame delta and refuses spare columns,
  and `INSET_X` is fixed at 4 in the tool, so the view cannot be centred between two side panels
  without adding the view-rect x sites (`proto.c 0x41ED63`, `engmain.c` stride/pointer arithmetic)
  to the site list. Works on every map, but a 3840 screen ends up with 1664–2688 px of panel.
* **B. Pillarbox the map view per map at run time** (shrink the gc clip rect `ui+8` and offset the
  destination so `view.x/y ≥ 0` always): the renderers stay untouched, the HUD frame needs no
  change, but the view size is an immediate in ~40 sites, so this needs run-time code that
  recomputes the view struct (`0x435E7C`) and the clip rect from the map size at battle start —
  a stub of a few hundred bytes plus the bounds rule; the black side bars would show the HUD
  frame's hole colour.
* **C. Let the engine tolerate a view larger than the map** ("small map centred"): bounds rule
  `if max < min: min = max = map/2` at the bounds computation (both axes; the tile snap
  `0x40AF1E/2B` keeps it aligned), and clamps in the four consumers that read map rows from the
  view rect — tile drawer (per-tile `clamp(tx+col, 0, w−1)` / `clamp(ty+row, 0, h−1)` through the
  row table instead of the flat pointer, ~45 bytes), lightmap pass (replace the 50-byte flag
  application `0x453A80..0x453AB2` with clamps, fits in place), vision scan (intersect the rect
  after `call 0x4365BC` at `0x435E62`, ~28 bytes, also fixes the second caller `0x409A00`),
  ambience (~20 bytes in place of the two early returns), plus a refusal of off-map spot orders in
  the release handler. Off-map tiles then repeat the edge tile (or, with one more test, draw
  black). Room: the Classic AUTO zero tail is full (3 bytes); the dead `cd_probe` body
  `0x405EAC..0x405F88` (`ret` since `nocd`, ~220 bytes) is the obvious home, else a new PE
  section. **This is the only option that is 32:9 support rather than a workaround, and it also
  closes the 2560×1440 exposure.** Estimate: one to two days including the same test on Council
  Wars (+0x60 rule) and a game test on a 96-tile and a 64-tile map.

Whichever option: nothing is scaled (§9), so at 3840×1080 a unit is a quarter of its 1024×768
apparent size on the same monitor. A 2× integer-scaled 1920×540 render would be the alternative
"ultrawide" design and is out of reach for the exe (every blitter is 1:1); dgVoodoo can do it for
a test (`ImageScaleFactor`), a player cannot.

**Done in the tool (22 Sep 2026): the intro movie keeps its proportions.** `Geometry.movie_rect`
was `(0, (H−16:9)/2, W, …)` — the 320×180 frames stretched to the full width, at 32:9 twice as wide
as tall as they should be (seen in the first run, screenshot below). Now
`movie_w = min(W, H·16/9)`, `movie_h = min(H, W·9/16)`, both centred: letterboxed on screens
taller than 16:9, **pillarboxed** on wider ones (3840×1080 → dest `(960,0)-(2880,1080)`). The Blt
block of `draw_offscreen` (`movie_blt_block`, 62 bytes at file `0x7214`) stores `dest.left` as an
immediate when it is not zero — `xor edi,edi; mov dword ptr [ebp+62h],imm32` (9 bytes) instead of
`xor edi,edi; mov [ebp+62h],edi` (5), eating four of the six trailing NOPs; `_unsupported_left` is
gone. Every mode the patcher offers has `left = 0`, so the 1024×768/1280×* plans and the generated
script are byte-identical (checked by regenerating). Verified in the rig: the movie shows at
960..2880 with black pillars.

**Everything else at 3840×1080 worked as designed:** `SetDisplayMode(3840,1080,16)` accepted (by
dgVoodoo, see below), the full-frame main menu (`paint_intro.py`: planet centred, two button
columns at x 1740/1920, credits box at (+1600,+300)), the `INTRF_HD` set generated by the Python
chain of §10.24 (54 `INTRF_HD` files + 7 `exp/intrf_hd`, HUD bar 50 px), `error.log` empty through
intro and menu, no sign of the 32 MiB pool running out at the menu (a battle was not reached, so
the pool at 116×32 tiles is unmeasured). What a shipped mode would still need: `3840x1080` in the
generator's `HD_MODES` with its three pictures in `INTRF_HD\3840x1080\`, and one of A/B/C above.

**The test rig: dgVoodoo 2 as the "voodoo virtual video card".** dgVoodoo 2.87.5 (GitHub release
`dege-diosg/dgVoodoo2`, `dgVoodoo2_87_5.zip`, 9.3 MB, SHA-256 `5ffde692…`) wraps DirectDraw on
D3D11 and presents any resolution the application asks for on whatever monitor exists: its 32-bit
`MS\x86\DDraw.dll` beside the exe, `dgVoodoo.conf` with `FullScreenMode = true`,
`FullscreenAttributes = fake` (a screen-size window, no exclusive mode), `ScalingMode =
stretched_ar` (the 3840×1080 frame shown as 1920×540 letterboxed on the 1920×1200 panel),
`CaptureMouse = true`, `dgVoodooWatermark = false`, `VRAM = 512`, `ExtraEnumeratedResolutions =
3840x1080, 5120x1440`. The game ran from a scratch copy of the game folder mapped to `U:` with
`subst` (the tools' 3840×1080 set installed over the folder's 1280×800 set; the repository was not
touched). Three rig problems, each with its cause and fix:

1. **Windows closed the game 20–37 s after launch** (Event 1002 "stopped interacting with Windows
   and was closed", WER `AppHangB1`, exit code `0xCFFFFFFF`, `error.log` empty, no Event 1000),
   from the intro movie or a few seconds into the menu, with or without the MP3 music. A thread
   sampler (`Wow64GetThreadContext`, `scratchpad/uw/sampler.py`) put the main thread in the
   movie wait loop `0x40903E` (`PeekMessageA(&msg, 0, WM_KEYDOWN, WM_KEYDOWN, PM_REMOVE)` +
   `Sleep(100)`, i.e. no general message pump — the start-up load before it has none either) and
   the window title showed "(Not Responding)" at 14 s: the window was **ghosted**, and the ghost
   ends in the close. The stock game never meets this because the real `ddraw.dll` disables
   ghosting for an exclusive-mode process (inferred from behaviour: the same exe under the real
   driver is never ghosted); dgVoodoo does not. Fix for the rig: a 3.5 KB forwarding proxy
   (`scratchpad/uw/proxy/ddraw_noghost.c`, MSVC 32-bit) that calls
   `DisableProcessWindowsGhosting()` on the first `DirectDrawCreate(Ex)` and forwards the other
   13 exports to dgVoodoo's DLL renamed `dgv_ddraw.dll`. With it the game ran for minutes and
   survived the tester's clicks (the same clicks had "crashed" it before: a click on a ghost
   window brings the WER close dialog). **Player-facing caveat:** anyone running the game through
   dgVoodoo (or any wrapper that skips exclusive mode) hits this; an exe-side cure would be a
   general-range `PeekMessage` in the movie loop and the loader, not done.
2. **The pointer reached only the left third of the frame** (tester's report), then exactly
   half. Measured by reading the game's pointer globals (`0x4DFF14/1C`) from the process while
   moving the mouse: **the game pointer equals the Windows cursor position 1:1** — the
   DirectInput path `0x450E80` is inactive at the menu (its enable flag `0x5327BC` is 0, the
   accumulator `0x5327C0/C4` stays 0; the §7 "absolute" clamp path `0x450E20` stays at its
   (320,240) seed too), the position comes from the posted `WM_MOUSE*` messages the game peeks
   in `frame_end`, in window client pixels. dgVoodoo clips the Windows cursor to the app
   rectangle (y ≤ 1079) but never rescales those coordinates (it transforms `GetCursorPos`, which
   this exe does not import). Two layers: (a) this PC runs 150 % display scaling, so the
   DPI-unaware exe saw a 1280×800 logical screen — the left third of 3840. Fix: an external
   manifest `dc16uw.exe.manifest` beside the exe (`dpiAware true`, `dpiAwareness
   PerMonitorV2`), honoured because the exe has no embedded manifest; Windows caches the manifest
   state per exe, so the exe had to be touched after adding it (`GetDpiForWindow` 96 → 144);
   then the physical width, 1920 = half of 3840. (b) The scaling itself: the proxy DLL installs a
   `WH_GETMESSAGE` hook on the game thread that maps every `WM_MOUSE*` message from the
   letterboxed image rectangle (computed like `stretched_ar`, `DCUW_APP=WxH` in the environment,
   default 3840×1080) to frame pixels — once per message, on `PM_REMOVE` only, tagged in an unused
   `wParam` bit, because dgVoodoo and the shell peek the same message with `PM_NOREMOVE` first
   and a first version transformed some messages two or three times. Verified: mouse (100,600) →
   pointer (200,540), (1900,860) → (3800,1060), corners clamp; the tester reached the right-hand
   menu column. Forcing dgVoodoo's own `[DirectX] Resolution = 1920x540` was tried first and
   changed nothing for this game (1:1 stayed).
3. A first guess — doubling the DirectInput deltas at `0x450EAD` in the rig exes — did nothing,
   because that path is not the one in use at the menu; the two rig exes still carry it (34
   bytes, `add [5327C0],eax` ×2 / `add [5327C4],eax` ×2, `jmp 0x450F41`) and it would only matter
   if a battle turns the DirectInput flag on. Reverted before any battle measurement is trusted.

None of the three exist on a real 32:9 monitor with the real DirectDraw (1:1 frame, exclusive
mode, no scaling); only the ghosting (1) also hits players who run the game through dgVoodoo.
Screenshots (`scratchpad/uw/*.png`) captured with `BitBlt` from the screen DC = the top-left
1280×800 of the real desktop.

**Test builds** (scratch copy only, never in the repository): `dc16uw.exe` = stock Classic +
`nocd, resolution 3840x1080, hdpaths, cursor, pool, clock, ddraw, camera, restore, longpath,
music, movies, sounds`; `dc16uwc.exe` = the same with `--viewport
2048x1024` (64×32 tiles, safe on every map, HUD frame mismatched — option A without its HUD).
Runs: full build to the main menu five times (three closed by Windows before the proxy, two
clean), a 60 s unattended run intro → menu with music, the tester's hands-on runs. **First battle
at 32:9 (16:01, maintainer at the mouse, `dc16uw.exe`, full 116×32-tile view): Classic mission 1
(HUMAN01, 96×84 tiles — a map 20 tiles narrower than the view) played to the Victory debriefing
(kills 2, losses 6), no Event 1000/1002, `error.log` empty.** So the tile drawer's flat-pointer
read did not leave the tile block in that game (it needs the camera at the top or bottom edge
while the horizontal overrun reaches the block header) and the lightmap/vision garbage on the
off-map strip is not fatal; what the off-map strip looked like (the trace predicts the far side
of the map repeated one row down) was not captured — to be screenshotted in the next run. The
"probable crash" of the trace remains a real risk (a 64-tile map with the camera at the map's
bottom row is the case to try), not an observed one. A control run of the untouched `dc16.exe`
under the rig exited with code 0 after 49 s without any input or event — not investigated.

**Rules learned:** processes started from the tool session are DPI-unaware by default and see a
1280×800 desktop; check `GetDpiForWindow` before reasoning about window sizes. A hung-window close
leaves Event 1002 + `AppHangB1` (WER 1001) and exit `0xCFFFFFFF`, an access violation Event 1000
and `0xC0000005` — the two are told apart in the Application log. `.def` forwarder lines are
refused by this linker for a module with an underscore in its name; `#pragma comment(linker,
"/export:Name=module.Name")` works.

**Option C was implemented the same evening: §10.33.**

#### 10.33 Fix `widemap`: a view wider than the map — camera centred, every map read clamped **(22 Sep 2026, maintainer decision "go ahead with option C, start with the bounds rule"; `tools/patch_widemap.py`, patcher fix `widemap` (Requires `nocd`, `camera`), both exes; 3840×1080 and 5120×1440 added to the patcher's modes, **5120×1440 removed again the same evening** - maintainer rule "only one resolution per aspect ratio" (4:3 excepted for the stock 640×480), 3840×1080 kept because it is the size that was played; tested in the dgVoodoo rig of §10.32 on Classic mission 1, the maintainer at the mouse)**

Six stages, 26 edits per exe (12 code, 14 `.reloc`), 367 bytes changed in Classic / 365 in
Council Wars, nothing moves. The code lives in the **dead body of `cd_probe`**
(`0x405EAD..0x405F87`, 219 bytes; the entry byte is `ret` since `nocd` and the body was never
reached again; CW `0x405E8D`, the one function in `safefunc.c` that is −0x20, not +0x60) — the
first use of that region; its 14 HIGHLOW `.reloc` entries are re-pointed to the 11 new absolute
operands (10 bounds, 1 view-map pointer), the three spare ones become type 0. The four blocks
take 207 bytes; the remaining two stages are in place.

| stage | where | what | bytes |
|---|---|---|---|
| 1 bounds rule | body +0, entered from the init's `call` at `0x41EEB8` (the one `camera` redirected) | per axis `if max < min: min = max = (min+max)/2` (= map/2, since the two add up to the map size in world units); `jmp` on into the `camera` stub, which clamps the camera and continues into `load_ambience`. ecx only; eax/edx (ui, string) untouched | 73 |
| 2 tile drawer `0x45011C` | 51-byte column stub at body +73, called from the column-loop head `0x4501D2` (its two displaced instructions run inside the stub); frame `sub esp,38h → 48h` for two new locals | `[ebp-44h] = 4·(clamp(tx+col, 0, w−1) − tx)` replaces `edi*4` at the three `lea` sites (`0x45020C`, `0x450293`, `0x4502B3`, 7 → 3+4 NOP), `[ebp-48h] = mask_row + edi*4` replaces the `add eax,ecx` of the occlusion-mask pointer (`0x45027B`, real column kept). Off-map columns repeat the edge tile; the flat row pointer stays | 51 + 6 edits |
| 3 lightmap pass `0x453A80..0x453AB1` | in place (the 50-byte edge-flag application) | `row = clamp(view_ty+row, 0, h−1)`, `col = clamp(view_tx+col, 0, w−1)` with esi/edi as scratch (both dead there); identical to the stock ±1 corrections at the edges, defined everywhere; the flag computation `0x4539DD..0x453A3B` is now dead | 50 |
| 4 vision rect | 34-byte stub at body +124: `clip_view_to_map`'s `call make_rect` (`0x435E62`) → stub | `call make_rect` (eax = &rect), then `x0 = max(x0, 0)`, `x1 = min(x1, w)` (map from `view+0x14` = `0x5044C0`, the one new absolute operand); both callers (`0x4396D4` scan, `0x409A00`) get the clipped rect | 34 |
| 5 ambience `0x445ABD..0x445AEC` | in place (the two origin guards) | `x0 = max(x0, 0)`, `w = min(w, W − x0)`; the z guard keeps its stock meaning, returning 0 through the function's own path `0x445BEA` (the inline epilogue the stock z guard used is gone, 3 NOPs) | 48 |
| 6 spot order `0x40968C` | 49-byte stub at body +158, called from the two 16-bit stores `0x40969F` (14 → 5 + 9 NOP) | `x = clamp(x, 0, map_w·256 − 1)` (map from `[cl+0xC]+0x46F4C`: the builder's eax is the **client object**, its game-state pointer sits at `+0xC`, exactly as the pick `0x409850` reads it), `esi = x` (the click marker's copy), then the displaced stores; ecx (cl copy) saved | 49 |

Rows are never off the map for the shipped maps (the tallest view, 44 rows at 5120×1440, is
shorter than the smallest map, 56 rows), so stages 2, 4 and 6 handle the column direction only;
stages 1 and 3 handle both. **Verified in the rig (22 Sep 2026, `dc16uw.exe` = full 3840×1080
chain + `widemap`):** the bounds read back from the running game were `min_x = max_x = 12288`
(96 tiles × 128), camera x pinned at 12288, z bounds `4096..17408` untouched; mission 1 (96×84)
played by the maintainer without crash, `error.log` empty; the `plan`/`verify`/`apply` cycle is
idempotent on both exes; the Council Wars 3840×1080 build (`engexp16uw.exe`) reached its menu and
the demo battle. Not yet seen: a 64-tile map with the camera at the top row (the case the trace
called probable), a click on the black margin, the ambience picking up again.

**First-click crash of the first published form (22 Sep 2026 evening, maintainer: "your last changes
broke patched executables. on the battlefield first click hangs the game"):** the stage-6 stub's first
form loaded the map pointer as `mov ecx,[eax+46F4Ch]`, taking the order builder's `eax` for the game
state. It is the **client object** `cl`: the mouse handler `0x4098D4` passes the same object to the
pick `0x409850`, which reads `mov eax,[eax+0Ch]; mov esi,[eax+46F4Ch]` - the game-state pointer sits
at `cl+0xC`, `cl+0x46F4C` is garbage. Windows Application log: Event 1000 `0xC0000005` in
`dc16new.exe` at fault offset `0x5F58` (VA `0x405F58` = stub +13, the `cmp edx,[ecx+9A4B8h]`) and in
`engexp16new.exe` at `0x5F38` (the same +13), twice before that at `0x2BF32` (`mov word ptr [esi],di`
in the 16-bpp rect fill `0x42BEB8`: when the garbage pointer happened to be readable the "clamped" x
was garbage and the click marker was drawn off the surface). No `error.log` line - an access violation,
not an assert; the WER box behind the full-screen surface is the "hang". Every ground click in every
battle at every resolution hit it: the published `b0551fc0…` / `f00fc454…` (commit `91e3f06`; the
maintainer ran the GitHub ZIP from `Downloads`) were broken for about an hour. Fix: `mov ecx,[eax+0Ch]`
before the map load (stub 49 bytes, body 207 of 219, the other blocks and the `.reloc` edits unchanged;
the fixed exes differ from the broken ones in the 49 bytes at file `0x534D` / `0x532D` only). Published
1024x768 exes since: Classic `a71d038b…`, Council Wars `bb6a1e77…`. Confirmed by the maintainer in game
and by scripted `SendInput` clicks from a `subst V:` copy of the game folder (Esc skips the movie, NEW
CAMPAIGN -> leader name -> START CAMPAIGN -> NEXT -> TO BATTLE; then select a unit and give three spot
orders, one into unexplored ground): Classic mission 1 and Council Wars council mission 1, units move,
process alive, no Event 1000, `error.log` empty. Rule from this: nothing that touches the click path is
published without a ground click in game - the afternoon's checks (bounds read-back, rig run) never
gave an order.

**Tool.** `patch_widemap.py verify|plan|apply EXE` (`.widemap.bak`), pattern-located, both exes.
`plan` and `verify` also work on the untouched original — the generator takes every plan on the
original — by synthesising the state after `nocd` and `camera` (the call site's "old" bytes are the
`camera` call, the stub address comes from `patch_camera.STUB_VA`); `apply` refuses until both are
applied. The `.reloc` lines use the generator's `.reloc @ file 0x…: XXXX -> YYYY` form.

**Patcher.** Fix `widemap` sits between `camera` and `restore` in both game builds
(`Requires nocd, camera`); `blocks_widemap` asserts 12 code edits + 14 reloc edits per mode. With
the 1024-byte lightmap stride (`LIGHTMAP_WIDE_STRIDES = (256, 512, 1024)`, `shl …,7`, cap 254 tiles;
5120×1440 = 156×44 tiles, frame `0x14CC → 0x16D3C` = 93 KB inside the 256 KB commit) the modes
**3840×1080 and 5120×1440** joined `HD_MODES`; their three shipped pictures are
`INTRF_HD\3840x1080\` and `INTRF_HD\5120x1440\` (`INTRG.GIF`, `INTRO.GIF`, `INTRFACE.GIF`, 0.34 /
0.57 MB, from the Python chain of §10.24). The regenerated `Apply-DarkColonyPatches.ps1` (7 modes,
874 KB) reproduces the tool-chain builds byte for byte (checked on a scratch copy for 1024×768 and
3840×1080, both games — the Council Wars comparison needs `ozi` appended to the chain; 5120×1440
builds, SHA-256 `db313c2f…`). The patcher's data check wants the per-size picture folders next to
the exe: a copy of the game folder without `INTRF_HD\<WxH>\` silently drops `resolution`,
`hdpaths`, `clock` and `movies` under `-All` ("RESOURCES NOT FOUND") and every mode then comes out
identical — read the `skipping` lines before trusting a hash. Published 1024×768 outputs with
`widemap`: Classic SHA-256 `a71d038b…`, Council Wars `bb6a1e77…` (the first published form `b0551fc0…` / `f00fc454…` crashed on the first battlefield click, see the paragraph above) (before: `5a2e10b7…` /
`d7b30c1a…`); `dc16.asm` / `dcexp16.asm` regenerated from them.

**What a real 32:9 monitor still lacks:** the interface set and exes for 3840×1080 / 5120×1440 exist
only through the patcher now (nothing else changes for the published 1024×768 build, where every
map is wider than the 28-tile view); a game on a real 32:9 panel has not been run. The dgVoodoo rig
notes of §10.32 do not apply there.

#### 10.34 The untouched exes: music by config file, expansion without a disc **(22 Sep 2026, maintainer questions "is it possible to play this mp3 music from original executables mentioning them in some config files? is it possible to run expansion exe without a disc by tweaking config files?"; assessment from the stock disassemblies plus a live run of stock `ENGEXP16.EXE`; nothing changed in the exes or the patcher)**

**Music: no.** The stock exes read no configuration at all: the import tables have no
`GetPrivateProfile*`, no `Reg*`, no `GetDriveType`; the only start-up inputs are `HBNFUFL.A01`/`.A02`
(drive letter), the marker file **`full`** (present in the game folder since the CD install; its
existence clears `0x00488DF5` / CW `0x00488E1D`, the "read missing files from the CD" flag tested by the
file-open helper at `0x004063AE`) and the data files. The soundtrack is opened as MCI device *type*
`cdaudio` (`MCI_OPEN`, flags `MCI_OPEN_TYPE|MCI_OPEN_SHAREABLE`, no element, no drive letter, `0x004510D0`
/ CW `0x00451130`), so the original exe can only ever play red-book tracks of whatever CD-ROM drive
Windows picks; the scene-list playlist lines are parsed and never read (§10.31). Two ways round it
without touching the exe, neither a game config file: (1) a mixed-mode image (`.bin` + a `.cue`, the
repo has none) in a virtual drive that emulates audio tracks; (2) a drop-in **`WINMM.DLL` wrapper** in
the game folder — the exe imports `WINMM.dll` by name, `winmm` is not in this PC's `KnownDLLs`, so
the application directory wins; such wrappers redirect the `cdaudio` MCI calls to `Music\Track02.*`
files (ogg-winmm = OGG only; cdaudio-winmm 0.3 and Direct-WinMM play MP3) with their own `.ini`.
That is fix `music` done outside the exe, at the price of a third-party DLL beside the originals.

**Expansion without a disc: yes, with two files.** The probe (`cd_probe` `0x00405E8C`, §10.19) only
needs `<letter>:\dc\anim.dat` to open and `<letter>:\dc\a<n>` to be *un*writable; nothing checks the
drive type. Tested on a scratch copy of the game folder (`subst V:`) with `HBNFUFL.A02` = `X:` and
`subst X:` on a folder holding `dc\anim.dat` whose `dc` folder denies the user `WD,AD,DC` (`icacls`):
stock `ENGEXP16.EXE` ran 90 s to the main menu, flag `0x004A49B8` = 1, CD path `X:\dc\`, no file
written on X:, `error.log` empty, all four menu buttons active (screenshot), no `widget.c` assert (the
assert of 14 Sep only fires when the flag is 0; it is fixed in the data since §10.35, and the menu
this test showed has eight buttons now). Any read-only location works (a folder under a
deny ACL, a read-only share, a mounted `.iso`); a plain `subst` of a writable folder fails the write
test and the game runs as "no CD" (grey buttons). The in-game check (`0x004113E6`, every 5 s) only
reacts to a *change* of the flag, so the folder must stay in place while playing. Files missing
locally are still looked up under `<letter>:\dc\` first when the flag is 1 (helper `0x004063A1`), so a
complete game folder is required — the repo's is. The 2025 third Council Wars byte (`0x00478DD9`,
`jne`→`je`) sits in a Watcom C-runtime write helper (strlen + `WriteFile` to a handle at `0x00499448`),
not in any CD test; `nocd` keeps it only because the played build has it.

#### 10.35 The Council Wars original's "hang" at the main menu: six buttons greyed, three of them not in the script **(23 Sep 2026, maintainer report "engexp16.exe is hanging when entering main menu … maybe by moving updated version of markup file to another place?", then "add missing widgets which are the cause of the problem" and "do the same widgets layout as in dc16.exe where ozi missions replaces intro and ozi load replaces single player war", then "for unpatched engexp16.exe put credits a bit higher and buttons a bit lower so they don't overlap each other"; mechanism re-derived from the stock bytes, fixed in the data — `exp/intrface/bintroe` is now Classic's script; both generators and the patcher rewritten and cross-checked; game test pending)**

**Partly superseded the same evening by §10.36**, which adds the DARK COLONY and LOAD DC GAME buttons: the patched menu is seven rows, not five, the row grouping gains a gap after row 5, the second column is MULTI PLAYER WAR / ENCYCLOPEDIA on rows 1 and 2 with QUIT on row 7, and the credits box is removed from the patched build at 640x480 (from the evening of 23 Sep to 24 Sep 2026 at every resolution; since 24 Sep the HD sizes keep it and move the block down under it, §10.36), so the y/height values quoted below are superseded: the box keeps y = title + 11 but is 94 rows at 1024x768, 76 at 1280x720 and the stock 100 from 1280x800 up, and at 640x480 `ozi` removes it. The stock `exp/intrface/bintroe` that this section is about - Classic's grid 16 px lower, for the untouched exe - is unchanged.

**Which exe.** The report names `engexp16.exe`; the repository has two Council Wars builds, the
untouched `ENGEXP16.EXE` and the patched `engexp16new.exe`. The patched one was ruled out first: the
1280×800 build in the game folder (SHA-256 `f8d82877…`) carries all thirteen fixes, its
`INTRF_HD` set is the matching 1280×800 one, `error.log` is empty and the newest game entries in the
Windows Application log are the already-fixed `widemap` clicks of 22 Sep (§10.33) — and since the
game opens its log with `fopen("error.log","w")` at start-up (`0x0040529F`, then `error2.log`,
`error3.log` if locked), an empty log means the last launch from that folder logged nothing. The
symptom is the one first seen on 14 Sep 2026 (§10.17): the **untouched** exe, with no disc.

**The bytes.** `main.c`'s menu init calls the CD flag getter and, when the answer is 0, greys six
widgets before entering the message loop (call sites at the same addresses in both builds; only the
call targets carry the +0x60 shift of §10.10):

```
00404F18: E8 ..            call  00405E6C      ; CW flag getter (Classic 00405E8C), al = disc answered
00404F1D: 84 C0            test  al,al
00404F1F: 75 66            jne   00404F87      ; file 0x431F - the byte `nocd` turns into EB (jmp)
00404F21: BB 01000000      mov   ebx,1         ; six x set_greyed(ip, id, 1):
00404F2B: E8 ..            call  00424574      ; id 0    CW helper (Classic 00424514)
00404F3A: E8 ..            call  00424574      ; id 1
00404F4C: E8 ..            call  00424574      ; id 16
00404F5E: E8 ..            call  00424574      ; id 4
00404F70: E8 ..            call  00424574      ; id 2
00404F82: E8 ..            call  00424574      ; id 5
```

`set_greyed` computes `objects[id] = ip + 0x88 + 0x34*id`, writes the greyed flag at byte +0
**without looking at the widget's type**, and only then calls `widget_redraw`/`widget_get`
(CW `0x00421CF8`, Classic `0x00421C98`), which asserts `0 <= i < 300` (widget.c line 151) and
`ip->objects[i].type != unknown_obj` (line 152, the byte at `ip + 0x89 + 0x34*i`). The shipped
Council Wars `exp/intrface/bintroe` — byte-identical to the CD's `/EXPENG/EXP/INTRFACE/BINTROE` —
has buttons 1, 3, 4, 5 and gadgets 7, 9, 10, 11 `%`-commented out, so the second call hits an
undefined widget: `error.log` gets `assert failure, file widget.c line 152
(ip->objects[i].type != unknown_obj)` and the runtime's message box sits behind the exclusive-mode
surface, exactly as in §10.16. That is the whole "hang".

| greyed id | label (`textmsg`) | in the shipped CW script |
|---|---|---|
| 0 | NEW CAMPAIGN | yes |
| 1 | TRAINING | **commented out → assert** |
| 16 | PLAY INTRO | yes |
| 4 | SINGLE PLAYER WAR | **commented out** |
| 2 | LOAD GAME | yes |
| 5 | ENCYCLOPEDIA | **commented out** |

Classic's script defines all nineteen widgets, and its greying list is the same six ids (verified in
the stock `dc16.exe` bytes), which is why the Classic original merely comes up with a mostly grey
menu without its disc while the Council Wars original dies. Note what the grey menu leaves usable:
**only MULTI PLAYER WAR (id 3, not in the list) and QUIT (12)** — killing that greying is what the
2025 hand-patch at file `0x507F`/`0x509F` was for, and `nocd` does it today.

**"Moving the markup file" cannot help.** The overlay helper `0x004063E4` tries `exp/` + name and
then the bare name in the game root, so there is no third location: either that file carries the
widgets, or the file is removed and the exe reads the root `INTRFACE/BINTROE` (Classic's script,
which defines them). And neither buys a *playable* CD-less original, because every useful button is
greyed by design — the three routes to that are the patched exe, `-Patches nocd` alone (a stock
640×480 build with a live menu), and §10.34's read-only `<letter>:\dc\anim.dat`, which satisfies the
probe and leaves the menu fully enabled.

**The fix, as instructed: Council Wars gets Classic's widget layout.** `exp/intrface/bintroe` is now
Classic's `INTRFACE/BINTROE` — the 2×4 grid with all six buttons, their gadgets and the eight-pair
`banim 18 0 8 8` (the commented rows in the shipped file were the Classic ones, at the Classic
coordinates; the four-row centred column the Council Wars author left is in git history) — with the
whole grid **lowered by 16 px**, rows **330 / 356 / 382 / 408** instead of 314 / 340 / 366 / 392, for
the credits box below. The logo and title rows are untouched, so only the eight button/gadget pairs
differ from Classic's file. Every id the CD logic greys now exists, so the untouched exe reaches its
menu. The labels stay
Classic's in that file, because the untouched exe has no pack mode: button 16 plays the intro and
button 4 opens SINGLE PLAYER WAR there. The two OZI names live where the `ozi` exe patch is applied
— `exp/intrf_hd/bintroe` (HD builds) and `exp/intrface/bintoze` (the 640×480 copy, §10.27) — in the
two slots §10.13 rewires:

| | left column (x 135) | right column (x 326) | stock label (untouched exe) | y at 640×480 |
|---|---|---|---|---|
| row 1 | **ACADEMY** (1) | MULTI PLAYER WAR (3) | TRAINING | 280 |
| | *(+12 px, half a button height)* | | | |
| row 2 | **COUNCIL WARS** (0) | — | NEW CAMPAIGN | 318 |
| row 3 | **LOAD CW GAME** (2) | ENCYCLOPEDIA (5) | LOAD GAME | 344 |
| | *(+12 px)* | | | |
| row 4 | **OZI MISSIONS** (16) | — | PLAY INTRO | 382 |
| row 5 | **LOAD OZI GAME** (4) | QUIT (12) | SINGLE PLAYER WAR | 408 |

The two columns are **12 px apart** as well (1 px in the stock grid) and the block is re-centred on
the screen afterwards, so the buttons sit at x 135 / 326 at 640×480, 327 / 518 at 1024×768,
455 / 646 at 1280×∗ and 1735 / 1926 at 3840×1080 — 370 px wide, centred to the pixel.

That order is the maintainer's (third instruction of the day: ACADEMY first, then the two Council
Wars entries, then the pack's two, `MULTI PLAYER WAR` where it was, `ENCYCLOPEDIA` and `QUIT` in the
second column's rows 3 and 5), and so is the grouping (fourth instruction, "add spacing between the
rows of menu buttons, approximately 25 % of button height, between 1 and 2 row, between 3 and 4
rows", then "change to 50 % spacing and add 50 % spacing between columns"): a gap of
`round(25 * 0.5)` = **12 px** after rows 1 and 3 and between the two columns, taken from the
script's own button height, which sets ACADEMY, the Council Wars pair and the pack pair apart.
(25 × 0.5 = 12.5 rounds to 12 in Python *and* in .NET — both use round-half-to-even — which is what
keeps the Python tool and the patcher byte-identical.) It is **five** rows, so
`build_ozi_overlay.menu_layout` is a layout step again, not two renames: the ids stay with their
handlers (the exe patch rewires 16 and 4, §10.13), only positions and labels move, each gadget
follows its button, and the `banim` pairs are untouched. The block is anchored on the **bottom** row
of the grid it is given — the row the 640×480 artwork sits 3 px below — so the fifth row and the two
gaps are won at the top, and applying the step twice changes nothing (the pitch is the smallest row
distance in the script, the bottom row `max(y)`, both identical before and after).

**Why the grid is 16 px lower, and what the credits box gives up** (maintainer, same day: "for unpatched engexp16.exe put credits a bit higher and buttons a bit lower so they don't overlap each other"). The scrolling `credits.txt` TTY is the one element of this screen that the **exe** both positions and sizes, from four immediates of `main.c bintro`'s create call (`0x004284A8`): width `ecx` = 280 (file `0x4294`), y `ebx` (`0x4299`), height `push 100` (`0x429E`), x `edx` (`0x42A0`). Classic holds (178, 200), Council Wars (178, **230**) because its four-row column started 26 px lower (§10.7, §10.11). Classic's grid starts at 314, so the 230 box (rows 230..329) would cover the top 16 px of the first row, and in the **untouched** exe the box cannot move — an immediate is not data — so there the buttons take the whole step: first row **330**, one row below the box, bottom row ending at 432, and the backdrop's bottom artwork starts at row **436** (`exp/intrface/intrg.gif`, re-measured: the button band is black to row 435), which leaves 3 px. Those 3 px are the whole budget at the stock size, so the patched menu's fifth row and its two 12 px gaps — 27 px more block — have to come out of the credits box, which ends up **shorter and higher**: rows 280..432 for the buttons, box **9 px** above them. Two pairs of values, because the backdrops differ: at HD sizes **68 rows at y = 203** (`patch_resolution.py`: `credits_y(203, 296)` for the y and a **Council Wars-only site** for the height, `push 64h` → `push 44h` at `0x429E` — the first per-build *site* rather than a fixup, since Classic keeps its four rows and its 100-row box), and at 640×480 **52 rows at y = 219** (`patch_ozi_menu.py`'s two 640×480-only sites; that mode's `ozi` edit count 15 → 18), because the **stock** 640×480 backdrop draws the planet's crescent across rows 198..218 (measured: ~150 non-black pixels per row in the button band) while the painted HD backdrops are black throughout (measured at all four sizes over the box *and* button rows). 52 rows is about three lines of the 14 px menu font, 68 about four; the box scrolls, so the line count is cosmetic. Measured on the generated scripts, every mode is identical in form: **pitch 26 with +12 px after rows 1 and 3, the bottom row unmoved, the box 9 px above the first row** (640×480 block 280..432 / box 219..270; 1024×768 478..630 / 401..468; 1280×720 448..600 / 371..438; 1280×800 497..649 / 420..487; 1280×1024 635..787 / 558..625; 3840×1080 670..822 / 593..660), with the `DCUT` title 11 px above the box at HD sizes and 27 px at 640×480. Published Council Wars builds: 1024×768 SHA-256 `4783eb2d…`, 1280×800 `772672fc…`, 640×480 `45399ed3…`; Classic is untouched, every mode's reference hash unchanged (`a71d038b…` at 1024×768).

**What the toolchain does now.** Every derived script is letterboxed/relayouted from the one stock file, so the grid propagates by itself and the OZI mode is a small layout step on top of it: `build_ozi_overlay.menu_layout` / `menu_script` and the patcher's `Edit-OziMenu` place the eight buttons (and their gadgets) in the five-row order above, with the two half-height row gaps and the half-height column gap, and rewrite five `textmsg` lines — `COUNCIL WARS`, `ACADEMY`, `LOAD CW GAME`, `LOAD OZI GAME`, `OZI MISSIONS`; `MULTI PLAYER WAR`, `ENCYCLOPEDIA` and `QUIT` keep their stock texts. Both refuse a script that is not the complete 2×4 grid (all eight `pushb`, all eight `gadget`, `banim` with 8 pairs) and name what is missing, and both are idempotent. `Edit-OziMenu640` — the two-column special case of 21 Sep 2026 (§10.27), needed only because five single-column rows did not fit between the credits box and the bottom artwork — is gone: with the box out of the way the same two-column block fits at every size, which is what that plan was reaching for.

**The rule this breaks, deliberately.** `exp/intrface/bintroe` is the first exception to §10.17's
"everything the original exe reads is byte-identical to the CD" (maintainer instruction, 23 Sep
2026). Nothing else the original reads was touched; the inventory check of §10.17 needs one line of
tolerance for this file (it is Classic's script with the eight button rows 16 px lower).

**Checks made.** Regenerated `exp/intrf_hd/bintroe` for every mode through the patcher's own `Edit-OziMenu (Set-BackgroundHd (Edit-IntroScript …))` chain (`hd_sets/` fixtures for the four shipped sizes, the game folder's live 1280×800 pair and the `ozi_ns/intrf_hd` copy) and checked per mode that every greyed id is defined, that the five rows sit at pitch 26 with +12 px after rows 1 and 3 and the bottom row where the 2×4 grid had it, that the columns are 12 px apart and the block centred, that the credits box clears the first row by 9 px, and that box and buttons alike sit on black backdrop at every size. `build_ozi_overlay.py` reports `0 edits` against the result and refuses a script without the grid; `menu_script` applied twice is a no-op. The patcher rebuilt all three Council Wars modes from the stock exe and matched its own references (1280×800 `772672fc…`, 1024×768 `4783eb2d…`, 640×480 `45399ed3…`, whose `ozi` run also wrote `exp\intrface\bintoze` and `ozi_ns\intrface\bintoze` with the five-row layout), and an earlier run of the same rehearsal had rebuilt the maintainer's previous 1280×800 build byte for byte, which is what made the one-byte credits differences easy to confirm. The game folder's build was refreshed to `772672fc…` (`patch_resolution.py verify`: stage 2, 67/67; `patch_ozi_menu.py verify`: v2). The two campaign scene lists are the only files where the rehearsal copy differs — it was made without `AVI\`, and `HSCENE`/`GSCENE` keep the Classic ending names only when the `DC*.AVI` files are there (§10.18).

**Confirmed in game (23 Sep 2026, 1280×800 build `772672fc…`).** The menu comes up as designed: five rows in three groups at x 455 / 646, labels ACADEMY / COUNCIL WARS / LOAD CW GAME / OZI MISSIONS / LOAD OZI GAME and MULTI PLAYER WAR / ENCYCLOPEDIA / QUIT, the shortened credits box scrolling clear of both the title and the first button row, everything on black, `error.log` empty, the screen stable over 40 s. Four behaviours were measured rather than eyeballed:

* **hit rectangles** - hovering (736, 573) brightened the ENCYCLOPEDIA plate by **+93 %** while the   two control plates changed by 0 %, so the widget rects sit exactly where the script says;
* **ENCYCLOPEDIA** - one click opened the encyclopedia screen (Trooper entry, HUMANS / ARTIFACTS /   GRAYS, BACK). That button was `%`-commented out in every shipped Council Wars build, so this is the   first time it has been reachable there, and the id → handler binding survives the reordering;
* **QUIT** - one click and the process left by itself with **exit code 0**;
* **the plate animation - resolved in §10.40 (25 Sep 2026).** Sampling at ~50 Hz showed the row-1 plate varying and the row-2 plate constant with `anim_oneoff` on gadget 7, and the other way round with it on gadget 6; the move of `anim_oneoff` to the first row was reverted on request (23 Sep 2026) because what the flag does was not understood. It is now: `anim_oneoff` starts the plate's one-shot when the screen opens, and the `banim` widget chains the plates in the order of its pair list and reveals each button as its plate finishes - the opening wave the maintainer reported as "in wrong places" on 25 Sep 2026. Since then the generators derive the order from the layout and put `anim_oneoff` on the first plate of that order (§10.40).

**The rig, for the next time.** `subst V:` on the game folder (never a long path, §10.29); **SPACE** aborts the intro movie - its wait loop at `0x00409080` drains `WM_KEYDOWN` with `PeekMessageA(0x100, 0x100, PM_REMOVE)`, so any key does - and **never ESC**, which the menu itself takes as QUIT. Captures come from a **DPI-aware** process: the exclusive-mode mode switch makes the physical desktop 1280×800, so a screen-DC BitBlt of the top-left 1280×800 is the game's surface 1:1. Input must come from a **DPI-unaware** one, because the game is unaware too: on this 150 % desktop a DPI-aware `SetCursorPos` lands at exactly 1/1.5 of the intended point in the game's own space (the first click aimed at (736, 573) put the game's crosshair at (490, 375) and hit nothing). That is the opposite of the map-editor rule of §"Map editor notes", where windowed output has to be captured with `PrintWindow(PW_RENDERFULLCONTENT)`.

**Still open.** The untouched `ENGEXP16.EXE` without a disc (the assert this section started from - it should now reach a mostly grey menu); MULTI PLAYER WAR from the Council Wars build, which has never been played over the network; and the 640×480 and other HD modes of this menu, which were verified on the generated scripts only.


#### 10.36 DARK COLONY and LOAD DC GAME: the original campaign from the Council Wars menu **(23 Sep 2026, maintainer instruction "it's time to add classic missions to engexp16 by adding paired buttons DARK COLONY + LOAD DC GAME between ACADEMY and COUNCIL WARS in main menu. menu grows in height so in 640x480 resolution we'll have to remove credentials", right column "MULTI 1, ENCYCLOPEDIA 2, QUIT 7, because it looks ugly when encyclopedia is somewhere in the middle"; one crash found and fixed by the maintainer's first game test, confirmed in game the same day)**

**§10.37 (23-24 Sep 2026) built and then dropped a fifth mode on top of this one** (JUPITER MISSIONS, ids 8 and 9); its findings - the credits bytes are live init code, the prefix slots can move into the dead `intrface/credits.txt` string, the menu block can be anchored at `H-72` - are recorded there. This section describes the shipped state.

**What was already there.** `DC16_SINGLE_EXE_MERGE.md` (11 Sep 2026) established that the expansion
build *is* `dc16.exe`: the Classic campaign, the training missions, the encyclopedia and the network
code are all compiled in, and the two builds differ only in the `exp/` overlay helper, four
per-build medal constants and the CD strings. Its §5.1 read the scene-list chooser
(`0x00402FED`, repeated for the load path at `0x00403C9D`):

```
if (gs+0x14F0 != 0)   name = race ? "gamestat/gtscene" : "gamestat/htscene"   // training
else                  name = race ? "gamestat/gscene"  : "gamestat/hscene"    // Dark Colony
if (gs+0x14F4 != 0)   name = race ? "gamestat/gxscene" : "gamestat/hxscene"   // Council Wars
```

so a Classic campaign is `gs+0x14F4 = 0`, `gs+0x14F0 = 0` before `call 0x00401C08` - and
`gs+0x14F4` is **already** 0 for every button, because the menu writes it at `0x00405015..0x00405021`
right after it tears the interface down and before it dispatches on the id. Since 15 Sep 2026 the
one game folder is the complete Classic data set as well, so §5.5's blocking list (the briefings, the
encyclopedia) is empty. What was missing was two buttons and a mode.

**A fourth prefix mode.** §10.13's mechanism is four writable 8-byte DGROUP slots - the overlay
prefix `0x004826D0`, the wave-loader's copy `0x00487DC8` and the save folder at `0x00482344`
(LOAD GAME screen) and `0x00485E5C` (in-game dialog):

| mode | prefix | wave prefix | save folder |
|---|---|---|---|
| Council Wars | `exp/` | `exp/` | `esave` |
| OZI missions | `ozi_ns/` | `ozi_ns/` | `ozisave` |
| **Dark Colony** | **`dc/`** | **`dc/`** | **`save`** |

`dc/` deliberately matches almost nothing: the overlay helper `0x004063E4` tries `dc/<name>`, fails,
and retries the bare name in the game root, which is Classic's own data - the 106-type
`GAMESTAT/GAMESTAT.TXT` instead of the expansion's 118 rows, `MISSION/` instead of `exp/mission/`
(the wave prefix is the reason that matters, cf. §10.21), `SCENARIO/HUMAN` and `ALIEN`,
`INTRF_HD/HSCENE.TXT` and `GSCENE.TXT`, `INTRFACE/CREDITS.TXT`, the Classic story texts. `save` is
literally the folder `dc16new.exe` uses: both Classic builds hold `save` in the same two slots
(`0x00482344`, `0x00485E54` - the Council Wars second slot is 8 bytes higher), so a save written by
either exe is listed by the other. The one file in the overlay is **`dc/intrf_hd/bintroe`**, a copy
of the patched menu script: the mode is sticky, so after a Classic campaign the menu is reloaded
through `dc/`, and the game root holds *Classic's* menu, which has neither the new ids nor the
Council Wars labels. At 640x480 the patcher writes `dc/intrface/bintoze` beside it (§10.27).

**Two new button ids for one byte.** The menu accepts only the ids its handler knows, filtered
before the dispatch chain:

```
00404F97: mov  edx,[ebp-0Ch]
00404F9A: test edx,edx
00404F9C: jl   00404FA3
00404F9E: cmp  edx,5            ; file 0x439E - the byte this fix raises to 7
00404FA1: jle  00404FF1         ; accept 0..5
00404FA3: mov  ebx,[ebp-0Ch]
00404FA6: cmp  ebx,0Ch          ; QUIT
00404FA9: je   00404FF1
00404FAB: cmp  ebx,10h          ; PLAY INTRO / OZI MISSIONS
00404FAE: je   00404FF1
```

Anything else is ignored, and an accepted id with no branch would fall through to `0x0040513D` and
leave the menu function, so the filter has to be widened by exactly as much as the chain. `cmp edx,5`
-> `cmp edx,7` admits **6** and **7**, the first two free ids; in the stock script those numbers
belong to the `LARGEBUTTON` gadgets of buttons 0 and 1, which the menu generators renumber to 19 and
20 (the object array holds 300 entries, `MAX_WIN_OBJECTS`, and ids are sparse - `MAINE` uses 199).
The handlers go where the old PLAY INTRO body was: `patch_ozi_menu.py` has rewritten that 96-byte
block since 10 Sep 2026 and used 37 bytes of it, so 59 NOPs were free. 52 are used now:

```
004050DB: 75 25        jne 00405102          ; end of the id chain, was `jne 0040513D`
00405102: 83 FF 06     cmp edi,6             ; DARK COLONY
00405105: 75 16        jne 0040511D
00405107: C7 80 F0 14 00 00 00 00 00 00      ; mov dword ptr [eax+14F0h],0   (campaign, not training)
00405111: 89 C2        mov edx,eax           ; game state
00405113: 8B 45 FC     mov eax,[ebp-4]       ; screen
00405116: E8 ..        call 0047F390         ; tramp_dc_campaign = stub_dc_set ; jmp 00401C08
0040511B: EB 20        jmp 0040513D
0040511D: 83 FF 07     cmp edi,7             ; LOAD DC GAME
00405120: 75 14        jne 00405136          ; = the NOP pad, which falls through to 0040513D
00405122: C7 80 F0 14 00 00 00 00 00 00
0040512C: 89 C2        mov edx,eax
0040512E: 8B 45 FC     mov eax,[ebp-4]
00405131: E8 ..        call 0047F3A0         ; tramp_dc_load = stub_dc_set ; jmp 00403AA4
```

`gs+0x14F0` has to be written because it survives a training session (TRAINING sets it to 3);
`gs+0x14F4` does not, see above. `stub_dc_set` is 73 bytes at `0x0047F340` in the same shape as
`stub_pack` / `stub_cw_set`, the two trampolines 10 bytes each at `0x0047F390` / `0x0047F3A0`; the
camera stub of §10.22 ends at `0x0047F331` and the section at `0x0047F400`, so 86 bytes of the tail
are still free. The four `mov edi,imm32` slot addresses add four HIGHLOW entries to the `.reloc`
insert of page `0x7F000` (8 -> 12 entries, 16 -> 24 bytes; 28 of the section's 52 slack bytes left).
Load buttons stay deterministic, as they have been since 10 Sep: LOAD CW GAME lists `esave`, LOAD
OZI GAME `ozisave`, LOAD DC GAME `save`. Whether a save restores the campaign flags is not verified
by code reading - nothing outside the menu writes `gs+0x14F4` - but LOAD CW GAME has depended on
exactly that since 10 Sep, so the record must come back from the file.

**The menu grows to seven rows, and the credits box goes** (at every size until 24 Sep 2026; since then
at 640x480 only - see the 24 Sep paragraph below). The maintainer's order, with the
grouping rule of §10.35 extended to a gap after rows 1, 3 and 5:

```
ACADEMY       (1)   MULTI PLAYER WAR (3)
DARK COLONY   (6)   ENCYCLOPEDIA     (5)
LOAD DC GAME  (7)
COUNCIL WARS  (0)
LOAD CW GAME  (2)
OZI MISSIONS (16)
LOAD OZI GAME (4)   QUIT            (12)
```

Seven rows at pitch 26 with three 12 px gaps and a 25 px button is **217 rows**, and the block is
anchored on the bottom row as before, so it grows upwards into the credits box. There is no
resolution where the box survives: at 640x480 the backdrop's black band between the planet's
crescent and the bottom artwork is 218..434, exactly 217 rows (measured on `exp/intrface/intrg.gif`
over the button columns), and at the HD sizes the whole cluster - logo, title, credits, buttons - is
the same layout scaled by `credits_y`, so the box would have to shrink to about a line and a half
there too. It is removed instead: the create call is 45 bytes of eight pushes and
`call 0x004284A8`, which is `ret 20h`, so dropping the block balances the stack, and nothing after it
reads `eax`/`ebx`/`ecx`/`edx` (the next call takes its two arguments in `eax` and `edx`). The two
absolute operands it carried (`intrface/mfonto5`, `intrface/credits.txt`) become type 0 `.reloc`
padding. This supersedes both sets of credits immediates: `patch_resolution.py`'s Council Wars
fixups and the 640x480 pair `patch_ozi_menu.py` wrote on 21 Sep are simply overwritten, since `ozi`
runs last; a build with `resolution` but without `ozi` keeps its credits box, and the untouched exe
is a different file and keeps everything.

At 640x480 the 12 px gap becomes **11**: the block would otherwise start at row 216, two rows into
the crescent's tail. Both generators shrink the gap by a pixel at a time while the first row is
above the measured limit 218, which is a no-op at every other size, and the rows come out at
219/256/282/319/345/382/408 with the bottom row and the 3 px above the artwork unchanged. The HD
rows were 414..606 (1024x768), 384..576 (1280x720), 433..625 (1280x800), 571..763 (1280x1024) and
606..798 (3840x1080) in the 23 Sep form, all inside the backdrops' black bands (measured per size over
the button columns: 195..722, 188..674, 201..754, 242..978, 253..1034) and 24 px below the `DCUT`
title - the arrangement the next paragraph replaced.

**The credits box is back above 640x480 (24 Sep 2026, maintainer: "return back credentials for higher
that 640x480 resolutions!").** The 217-row argument holds for the stock backdrop only. The painted HD
backdrops are black from under the planet's crescent down to the bottom artwork, which starts at
**H-45** at every size (measured over the menu columns: rows 194..722 at 1024x768, 186..674 at
1280x720, 199..754 at 1280x800, 240..978 at 1280x1024, 253..1034 at 3840x1080), and the 23 Sep block,
scaled with the cluster, left 90 unused black rows under itself at 1024x768. So the HD block now hangs
from the **title** instead of the stock bottom row: its first row sits **120 rows under the `DCUT`
gadget's last row** - 11 px, the stock 100-row credits box, 9 px - unless that would take the bottom
row past **H-72** (the stock 640x480 bottom row 408, 2-3 px above the artwork; the anchor §10.37 found
for the nine-row menu), in which case the block stops there and the box gives up the difference. The
box keeps its place 11 px under the title (`credits_y(203, 296)`, unchanged) and gets the height the
block leaves, `patch_resolution.cw_credits_height` = min(100, H − 72 − 192 − 9 − y):

| mode | title last row | credits box (rows) | button rows 1..7 | plates end | artwork from |
|---|---|---|---|---|---|
| 1024x768 | 389 | 401..494 (94) | 504 / 542 / 568 / 606 / 632 / 670 / 696 | 721 | 723 |
| 1280x720 | 359 | 371..446 (76) | 456 / 494 / 520 / 558 / 584 / 622 / 648 | 673 | 675 |
| 1280x800 | 408 | 420..519 (100) | 529 / 567 / 593 / 631 / 657 / 695 / 721 | 746 | 755 |
| 1280x1024 | 546 | 558..657 (100) | 667 / 705 / 731 / 769 / 795 / 833 / 859 | 884 | 979 |
| 3840x1080 | 581 | 593..692 (100) | 702 / 740 / 766 / 804 / 830 / 868 / 894 | 919 | 1035 |
| 640x480 | 191 | removed | 219 / 256 / 282 / 319 / 345 / 382 / 408 (unchanged) | 433 | 436 |

(The `knobe` plates are 26 rows, one more than the `pushb` height, hence "plates end".) In the exe the
**640x480 form** of `ozi` keeps its two credits edits (create → 45 NOPs, destroy count 1 → 0) and the
page-0x4000 `.reloc` change; the **HD form has neither** (`patch_ozi_menu.build(stock_mode)`; the
patcher counts 24 `ozi` edits at 640x480 and 20 at an HD size), and `resolution`'s Council Wars-only
height site writes `push 5Eh` (94) at 1024x768 and `push 4Ch` (76) at 1280x720 and is left out where
the stock `push 64h` fits - `patch_resolution.resolve` now skips any site whose replacement equals the
expected bytes, so the plan never lists a no-op (which the generator would have rejected). The layout
rule lives in three places and was cross-checked: `build_ozi_overlay.menu_layout` (reads the `DCUT`
gadget and the `size` line, so applying it to its own output changes nothing), the patcher's
`Edit-OziMenu` (PowerShell twin - its output for all five HD sizes is byte-identical to the Python
tool's, the four `hd_sets` fixtures were regenerated) and `cw_credits_height` on the exe side. An exe
patched by the 23 Sep form at an HD size keeps its NOPs (the tool does not restore code;
`patch_ozi_menu.py verify` prints a note), so such a build is rebuilt from the original. Builds:
Council Wars 1024x768 **`ec7ab499…`** (was `b12a0b14…`; staged for the repository from a scratch
build, the maintainer's working copy keeps its 640x480 build), 640x480 `5d8202f8…` unchanged,
generator references 1280x1024 `be1905a7…`, 1280x720 `1f3e3c8b…`, 1280x800 `a1a6c6f6…`, 3840x1080
`6dbf747f…`; Classic untouched (`a71d038b…`). `dcexp16.asm` regenerated from the 1024x768 build
(`mov ebx,191h` / `push 5Eh` at `0x404E99` / `0x404E9E`). **Smoke test** (24 Sep 2026, scratch copy of
the game folder on `subst W:`, no `AVI\` so the intro is skipped, DPI-aware screen capture 14 s after
launch): the 1024x768 build reached the main menu with the credits text scrolling in the box columns
between rows 390 and 474, nothing but black between the box and the first button row (495..503),
buttons on 504..720, the Take 2 artwork from 723, `error.log` empty; a synthetic Esc from the probe
did not quit the game this time and it was terminated. Not run in the game: the other sizes and a
click through the menu.

**The whole cluster 15 rows higher (24 Sep 2026, second instruction: "move DC logo, DARK COLONY logo,
credentials and buttons block 15 points higher for resolutions except 640x480").** Council Wars only.
The letterbox rule (`paint_intro.layout_for`: the cluster keeps its stock vertical centre as a
fraction of the height, plus `LOGO_CLEARANCE` 20) now takes `paint_intro.cw_menu_lift(H)` rows off
the shift for the `exp/` override scripts, and the same amount comes off the credits y
(`patch_resolution`) and the block's H-72 cap (`build_ozi_overlay.menu_layout`, the patcher's
`Edit-OziMenu`), so logo, title, box and buttons move as one and the box heights stay 94 / 76 / 100.
The lift is **15 wherever the opaque, black-baked DC logo stays below the planet's crescent** - the
painted fade ends at row **112 of the 480-row design** over the logo's columns (measured on the shipped
backdrops: brightest pixel above the logo top 55/255 at 1280x720 with no black row between, 7/255 in
rows 183..184 at 1024x768, nothing at 1280x800; 112·H/480 = 168 / 179 / 187 keeps the logo clear of
every pixel brighter than 7/255) - and **0 at 1280x720**, where the logo already touches that tail
(a 15 px lift would cut a 310-px-wide black notch into the crescent). The function is a pure function
of H (`max(0, min(15, logo_top − round(112·H/480) − 1))`), so the exe tool, the two Python layout
tools and the PowerShell twin (`Get-MenuLift`) agree without measuring anything at patch time:

| mode | lift | logo top | title last row | credits box (rows) | button rows 1..7 | plates end | artwork from |
|---|---|---|---|---|---|---|---|
| 1024x768 | 15 | 183 | 374 | 386..479 (94) | 489 / 527 / 553 / 591 / 617 / 655 / 681 | 706 | 723 |
| 1280x720 | 0 | 168 | 359 | 371..446 (76) | 456 / 494 / 520 / 558 / 584 / 622 / 648 | 673 | 675 |
| 1280x800 | 15 | 202 | 393 | 405..504 (100) | 514 / 552 / 578 / 616 / 642 / 680 / 706 | 731 | 755 |
| 1280x1024 | 15 | 340 | 531 | 543..642 (100) | 652 / 690 / 716 / 754 / 780 / 818 / 844 | 869 | 979 |
| 3840x1080 | 15 | 375 | 566 | 578..677 (100) | 687 / 725 / 751 / 789 / 815 / 853 / 879 | 904 | 1035 |
| 640x480 | 0 | 0 | 191 | removed | 219 / 256 / 282 / 319 / 345 / 382 / 408 | 433 | 436 |

`exp/intrf_hd/introe` (the same cluster on INTRO.GIF, unreachable in the retail exe) is lifted with
`bintroe`, since both are override scripts; nothing of Classic's moves. Cross-checked: the patcher's
`exp\intrf_hd\bintroe` equals `paint_intro.relayout` + `build_ozi_overlay.menu_script` byte for byte
for all five HD sizes (apart from the `background` line, which `Set-BackgroundHd` / `split_hd_data.py`
retarget to `intrf_hd/`), every mode's exe matches its regenerated reference, 1280x720 (`1f3e3c8b…`)
and 640x480 (`5d8202f8…`) came out unchanged. Council Wars builds: 1024x768 **`39a49a3b…`**,
1280x800 `c11cfbab…`, 1280x1024 `f4a46caf…`, 3840x1080 `1d7b44b5…`; the `hd_sets` fixtures and
the game folder's three 1024x768 menu copies regenerated, `dcexp16.asm` regenerated. **Game test (24 Sep 2026, same rig as above):** the lifted 1024x768 build reached the menu with the logo
on rows 183..302 and only 2 non-black pixels in the 15 rows above it (the crescent's tail stays
untouched), the title on 342..374, the credits text scrolling in 386..442, black between box and
buttons (480..488) and below the block (707..722), buttons on 489..705, artwork from 723, `error.log`
empty, and this time the synthetic Esc quit the game with exit code 0.

**The crash the first game test found, and why it was mine.** Maintainer, after the first build:
"for DARK COLONY missions: race overview is empty and clicking button hangs the game". The Windows
Application log had Event 1000 for `engexp16new.exe`, `0xC0000005`, **fault offset `0x0004DE14`** =
VA `0x0044DE14`, which is `mov dword ptr [ebx+eax*4],0` in a three-instruction "clear a list" helper
reached from the interface-level pop `0x00425EAC`. The cause is the credits removal, not the data:
the text-window module allows exactly **one** instance - its create at `0x004284A8` increments the
count at `0x004D61C0` and asserts when it reaches 2 - so every screen that makes a TTY frees it
again, and `main.c bintro` does that on every menu click with

```
00405008: BA 01 00 00 00   mov edx,1        ; one TTY to free
0040500D: 8B 45 FC         mov eax,[ebp-4]
00405010: E8 ..            call 00429684    ; loop: [004D61C0]-- ; free
```

With the create gone the count went to **-1**, `0x004D61C0` stayed negative, the next screen's TTY -
the race overview's text - was created at index -1, that screen came up empty and the corrupted
neighbourhood took the level teardown down. The fix is one immediate: `mov edx,1` -> `mov edx,0`
at `0x00405008`, so the pair balances at zero. The per-frame walk `0x00427BB7` iterates
`0 .. [0x004D61C0]` and is a no-op at 0; all ten callers of `0x00429684` pass the number of TTYs
their own screen created, and this is the only one that changes. Lesson for the next screen element
that gets deleted: in this engine a widget is not just drawn, it is counted, and the counter lives
in a global that the *next* screen indexes.

**What the tools do.** `patch_ozi_menu.py` (fix `ozi`, Council Wars only) now makes 8 code edits
beyond the OZI ones - the id filter, the chain end, the two handlers inside the 96-byte block,
`stub_dc_set`, the two trampolines, the 45 NOPs and the destroy count (the last two, with their page-0x4000 `.reloc` entries, in the 640x480 form only since 24 Sep 2026 - `build(stock_mode)`, see the 24 Sep paragraph) - plus 4 `.reloc` entries; the
credits site is matched with wildcards on the three immediates so the tool accepts an exe patched
for any resolution, and an exe with the 21 Sep form upgrades in place (`v2` -> `v3`).
`build_ozi_overlay.py`'s `menu_layout` / `menu_script` and the patcher's `Edit-OziMenu` place ten
buttons instead of eight, renumber the two gadgets, clone the new `pushb` / `gadget` / `textmsg`
lines from the script's own `pushb 16` / `gadget 17` / `textmsg 8` so they keep its field layout,
and rewrite `banim` with ten pairs. Both accept either the stock 2x4 grid or their own output, so
they stay idempotent, and both refuse anything else by name. `Write-InterfaceSet` and
`Write-StockOziMenu` write the `dc/` copies; `ozi_data` lists `dc/intrf_hd/bintroe` so the patcher
refuses to build a menu whose Dark Colony mode has no script.

**Checks made.** Python and PowerShell produce byte-identical menu scripts at 640x480, 1024x768,
1280x720, 1280x800 and 1280x1024 (the four `hd_sets/` fixtures plus the stock script); applying
either twice changes nothing; `build_ozi_overlay.py` reports `0 edits` against the patcher's output
and the three copies (`exp/intrf_hd`, `ozi_ns/intrf_hd`, `dc/intrf_hd`) are identical. The patcher
rebuilds the tool chain's exe byte for byte at 1024x768 (`a19972fb…`) and 640x480
(`17e7a38120…`), and Classic is untouched (`a71d038b…`, every mode's reference hash unchanged).
`dcexp16.asm` regenerated from the new published exe.

**Confirmed in game (23 Sep 2026, maintainer, after the destroy-count fix): "dark colony missions
work now".** DARK COLONY reaches the race overview and plays the original campaign out of the
Council Wars build.

**ACADEMY joined the mode the same evening** (maintainer: "ACADEMY saves goes to COUNCIL WARS
saves. save them in DARK COLONY instead"). TRAINING has always been Classic content - it plays
`SCENARIO/TEST/htrain1..7` off the Classic training lists `intrf_hd/htscene` / `gtscene`, which no
`exp/` file shadows - but its handler still reached the campaign runner through
`tramp_cw_campaign`, so it ran in Council Wars mode and wrote its saves to `esave`. Its `call` at
`0x00405083` now goes to `tramp_dc_campaign` instead (the same trampoline DARK COLONY uses, five
bytes), which puts the training saves in `save/` beside the Dark Colony campaign's - exactly where
`dc16new.exe` puts its own - and lists them under LOAD DC GAME. A second effect comes free: the
briefing of training mission *n* is `mission/h<n>.wav`, and under the `exp/` prefix that resolved
to `exp/mission/h1..h7.wav`, the **Council Wars** briefings; through `dc/` it falls back to the
root `MISSION/H1..H7.WAV`, the Classic ones. This is the same shadowing the Classic `sounds` fix
took out of `dc16.exe` (§10.21), one button later. NEW CAMPAIGN keeps `tramp_cw_campaign`.

**Still open.** A training save round-trip (ACADEMY -> save -> LOAD DC GAME) and one for the
campaign (nothing outside the menu writes
`gs+0x14F4`, so the campaign record has to come back from the save file - as LOAD CW GAME has
assumed since 10 Sep), and a check that COUNCIL WARS / OZI MISSIONS still behave after a Classic
campaign has made the mode sticky. One known deviation, left in deliberately: the six **campaign
medals** of the mission-completed screen are awarded on the expansion's schedule, because the two
mission numbers that award the last two are per-build immediates (`DC16_SINGLE_EXE_MERGE.md` §5.3,
re-read 23 Sep 2026 - the four `cmp byte ptr [eax],imm8` at `0x00403FC7`, `0x00403FE8`,
`0x004040B3`, `0x004040D4`, Classic 5/0Eh/4/0Eh against Council Wars 0Eh/7/0Eh/7). The medals are
the `SMALLMEDALS` widgets 25..30 of `wingame` and nothing outside that screen and the save reads
them, so this is two icons lighting up at the wrong missions; making the sites mode-aware needs a
detour per site, about 100 bytes against the 86 left in the tail (70 since 25 Sep 2026). Routing
MULTI PLAYER WAR through the same stub would make the expansion client's tables identical to a
Classic client's for the relay's checksums (§4.2 of the merge document), 10 more bytes, untested.
**Done 25 Sep 2026** (`tramp_dc_net` at `0x47F3B0`, together with a data fix for the Gray
commander's deploy animation - see §10.39).

**Which balance tables the DARK COLONY mode plays with (25 Sep 2026, maintainer question
"investigate if 'dark colony' missions in ultimate version use calibration data for that set of
missions?"; investigation only, nothing changed).** Classic's own. The six tables
`gamestat/gamestat.txt`, `weapstat.txt`, `boomstat.txt`, `mbullet.txt`, `unitid.txt`, `depend.txt`
(DGROUP strings `0x4867D0`, `0x486680`, `0x48662C`, `0x486564`, `0x4863F4`, `0x486344`) are read
by six loaders (`0x43BBE0`, `0x43B74C`, `0x43B484`, `0x43B1B0`, `0x438778`, `0x437A20`) that
`load_tables` `0x43C4AC` calls in a row (`eax` = the table object `gs+0x7D38`). Its only caller is
the game-state initialiser `0x41BB50` (call at `0x41BBFE`), which runs at every battle start (from
the battle function `0x40122C`, entered from all eight game-start sites of the menus) and on every
save load (`0x40DD0E`, right after the save-magic `0x21340000` check). The global arrays
(`object_types` `0x50FCF8` and the rest) are rewritten each time; nothing survives from the previous
mode. Every loader opens through `0x406474` = the prefix helper `0x4063E4` with the required flag:
`<prefix>` + name first, the bare name in the game root second. DARK COLONY, LOAD DC GAME and
ACADEMY put `dc/` into the slot; the `dc/` overlay holds no `gamestat/` (only `intrf_hd/bintroe`,
`intrf_hd/lopte`, `intrface/credits.txt`), so all six fall through to root `GAMESTAT/` = the
Classic set (106 types), the very files `Dark Colony.exe` reads (its helper has no prefix). The
mission files fall through the same way (`exp/scenario` holds only `council` and `aerogen`). The
COUNCIL WARS mode would give the same numbers for the shared units anyway: `exp/gamestat/gamestat.txt`
is root plus the 12 appended types 106-117 (only the count line differs otherwise), and the expansion
ships no weapstat, boomstat, mbullet, unitid or depend of its own; `exp/gamestat/gxmestat.txt` (a
106-type copy) is dead - no string in the exe names it. The OZI mode's `ozi_ns/gamestat/` is the only
re-balanced set (against root: 85 changed lines in gamestat, 11 in weapstat, 18 in boomstat, 10 in
unitid, 27 in mbullet) and is reached only under `ozi_ns/`; the DC buttons reset the prefix before
every game, so it never bleeds into a Dark Colony mission. Saves agree with this: a save writes the
type count `0x518B34` plus 16 bytes per type (`0x43C4E4`, fields `+0x30`/`+0x38` of each
`object_types` row), the load `0x43C558` returns -1 on a count mismatch, so a 106-type DC save loads
only after LOAD DC GAME has set `dc/`. **Not per mode** - loaded once at start-up under `exp/` and
therefore shared by every mode: the 200-entry sound table `exp/sound/sound2.dat` (12 entries name
other files than root `SOUND/SOUND2.DAT` - ambience slots 19, 20, 23, 25, 26, 57-60, 180, 181 and
slot 169: whale→cow, click→mosq, dchirp2→birds, dchirp3→dog, daychirp→dog2, birdcall→r2bird,
bug→frog, chiggers→frogs, nightbug→cricket, rnatwea→cobra, owl→wolf, monk→seagull; audio only, the
rest of the table is identical apart from the `.\` path prefix), the animation list `exp/animozi.dat`
(simulation-identical to Classic since §10.39) and the sprite banks.


#### 10.37 The Jupiter mod 0.3 as a fifth menu mode: built, confirmed in game, and DROPPED **(23-24 Sep 2026, maintainer instruction "mod specific files must go under 'jupiter' folder next to 'exp' folder of 'Council wars' and must be added to main menu as 'JUPITER MISSIONS' and 'LOAD JUP GAME' as last in the left column ...", then, once it was clear that the mod has no missions of its own - "jupiter missions are the same as DARK COLONY missions. is that right?" - "drop it but save findings in documentation". Nothing of it is in the exes, the data, the tools or the patcher; this section is the record of what was learnt.)**

**Outcome first.** The mod turned out to be a unit / balance mod over the Classic table with **no
missions, no animations and no campaign button of its own**, so "JUPITER MISSIONS" could only be the
original Dark Colony campaign played with six extra unit types - the DARK COLONY mode of §10.36 under
another prefix. The maintainer dropped it on 24 Sep 2026. Everything below was implemented, cross-checked
and run in the game before that decision (menu, race screen, briefing, first mission, load screen), then
reverted: `patch_ozi_menu.py` is back at v3, `build_ozi_overlay.py` at the seven-row layout, the patcher
regenerated unchanged (CW 1024x768 `b12a0b14…`), the `jupiter/` overlay, `jupsave/` and the new
`build_jupiter_overlay.py` deleted, the extracted installer folder removed (`Documents/DCJM_setup.exe`
stays). The reusable findings: (1) the two prefix slots can be moved into the dead
`intrface/credits.txt` string, which lifts the 7-letter limit on a mode prefix to 11; (2) the 45 bytes of
the removed credits create are **live code on the menu's init path**, not a free hole - anything put
there needs a jump over itself; (3) the start-up sound table is exactly 200 entries, all loaded at
start-up, so no mode can add a sound; (4) the menu block can be anchored at `H-72` at every size
because every painted backdrop's bottom artwork starts at `H-45`, but nine rows never fit the
640x480 band; (5) two test-rig rules - a full-screen test on a locked desktop proves nothing, and the
exclusive surface reads back black unless the capturing process is DPI-aware. Should the mod come back
(as a skirmish or multiplayer mode, which is what its authors built it for), the code below is the
starting point.

**What the mod is.** `DCJM_setup.exe` (18.8 MB, `md5 062e6134…`, in `Documents`) is an Inno Setup
5.2.1 installer of "DC:Jupiter Mod 0.3" by MaraProject (`setuper.iss` inside: AppVerName "Jupiter
Mod 0.3", publisher URL `dc.maraproject.net`, built 21 Jul 2008 on a desktop). It was unpacked with
`innoextract` 1.9 (installed through winget, `dscharrer.innoextract`; 7-Zip cannot open Inno
archives) into `Dark-Colony-development/DC - Jupiter mod/` - 2065 files, 86 MB, one `{app}` root. It
is a **complete Dark Colony install with the Council Wars files folded into the root** (no `exp/`
folder: `anim.dat` = `exp/anim.dat`, the CW `animate/` and `sprites/` banks, terrains, `scenario/
aerogen|council`, the CW briefings in `mission/`), plus the untouched Jan 1998 `dc16.exe`
(`md5 8fc93346…`), `dc.exe`, a third-party `dc cd fix.exe`, the map editor's DLLs and InstallShield
leftovers. A byte comparison against the game folder (root, then `exp/`) leaves **the mod itself**:

| file | what changed |
|---|---|
| `gamestat/gamestat.txt` | **112** unit types = the 106-type table + six new: Major / Sitruc (106/107, TRSC/GRAY sprites, 47 speed, weapons 5 12 10 / 62 12 10, upgrade links 107/106), human and gray Bazooka (108/109, weapons 66-68 / 69-71), the gray Mothership (110, SAUC, 5000 health, weapon 72), the Dodo (111, TRUK); stat changes for GRAY, SCYT, T, ENGI, SLOM, HMINE |
| `gamestat/weapstat.txt` | **72** weapons: damage / shots / speed changes on the stock 64, plus 65 Barracks gun, 66-71 Nodegun turrets (TURR / XENO), 72 Mothership cannon (4000 damage) |
| `gamestat/depend.txt` | **86** rows: six new buildables (Major 1500, grey troopers 1500, Bazooka 700 x2, Mothership 4000, Dodo 400), Warrior Fold / Barracks 750, Gray 250, Slom 300, Ortu / Osprey 500 |
| `gamestat/unitid.txt` | six rows `1 r 106..111 289..296` (type -> text id) |
| `sound/slist.dat` | SEL / ACK / DEA / DPY lists for types 106-109 (borrowed stock sounds), `48 GUN 169 170`, `63 GUN 200` |
| `sound/sound2.dat` + `sound/ion.wav` | **entry 200** `SOUND\ION.WAV` appended to the 200-entry start-up sound table (see below) |
| `intrface/maine` | the battle HUD with `count` build buttons for the new units (285-288, 294, 296 at x=577) and their `textmsg` names |
| `intrface/bintroe`, `intrg.gif`, `intrq.gif`, `credits.txt` | the mod's own 640x480 menu: SINGLE PLAYER WAR (id 4) top left, MULTI PLAYER WAR, LOAD GAME, QUIT - no campaign button; a repainted backdrop and an unreferenced second one; MaraProject credits |
| `scenario/human/human09.tro` | the Council Wars copy with the `&&==` typo (the root has the fixed one) |
| `.ovh`, `.o16`, `scenario/mplayer/debug.txt`, `scenario/test/scene.txt`, `hbnfufl.*` (`G:`) | game-written or stray |

So the mod has **no missions of its own**: it is a unit / balance mod meant for skirmish and
multiplayer, and its table extends the 106-type **Classic** table (Council Wars' own
`exp/gamestat/gamestat.txt` has 118 rows, so the mod's tables would break the expansion's
campaign). "JUPITER MISSIONS" is therefore the **original Dark Colony campaign played with the mod's
tables** - the DARK COLONY mode of §10.36 with a different overlay.

**A fifth prefix mode, and why the prefix slots had to move.** The mechanism is §10.13's: four
writable DGROUP strings, a mode is their content, and the JUPITER MISSIONS handler writes
`jupiter/` / `jupiter/` / `jupsave` / `jupsave`. The overlay helper `0x4063E4` opens
`jupiter/<name>` first and falls back to the game root, so everything the overlay does not hold
comes from the Classic data (`SCENARIO/HUMAN|ALIEN`, `MISSION/` briefings, `INTRF_HD/HSCENE|
GSCENE.TXT`, sprites, sounds), and LOAD JUP GAME lists `jupsave/`. But `jupiter/` is **eight**
characters and the stock slots are eight bytes with the next string right behind them:
`exp/\0\0\0\0` at `0x4826D0` is followed by `strlen(e…` (an assert text), the wave loader's copy at
`0x487DC8` by `unable to open file %s` (the `error.log` message of §10.19/10.29), so an 8-letter
prefix would have no terminator - and a 6-letter folder name was not what was asked. Each prefix
slot is read by exactly **one** instruction, the `mov esi,imm32` of an inline `strcpy`
(`0x4063F9` in the helper, `0x452AC8` in the wave loader `0x452AB0`; both checked by grepping the
disassembly for the two addresses). Both operands are re-pointed at the string
**`intrface/credits.txt`** at `0x48246C` - 21 bytes + 3 alignment zeros = 24 bytes whose only
reference was the `push` of the credits TTY create that §10.36 removed (its `.reloc` entry is type
0 since then; a scan of all 18 258 HIGHLOW pointers of the published exe finds none into
`0x48246C..0x482484`, and the untouched exe has exactly the one from `0x404E90`). The 24 bytes
become two **12-byte** slots initialised to `exp/` (the start-up mode, as before); every stub
keeps writing 8 bytes, so bytes 9..12 stay zero and terminate the 8-letter prefix. The two
save-folder slots (`0x482344`, `0x485E5C`) hold 7-letter names and stay where they are. The old
prefix slots keep `exp/` and are dead; the `.reloc` entries of the two moved operands stay HIGHLOW
(an absolute operand is still an absolute operand).

| mode | prefix (slot A `0x48246C`, slot B `0x482478`) | save folder |
|---|---|---|
| Council Wars | `exp/` | `esave` |
| OZI missions | `ozi_ns/` | `ozisave` |
| Dark Colony (and ACADEMY) | `dc/` | `save` |
| **Jupiter mod** | **`jupiter/`** | **`jupsave`** |

**Two more button ids, and where their code went.** The filter byte of §10.36 goes from 7 to **9**
(`cmp edx,9` at `0x404F9E`), which admits ids **8** and **9** - in the stock script the
`LARGEBUTTON` plates of buttons 2 and 3, which the menu generators renumber to 23 and 24 (the new
plates are 25 and 26). The 59-byte dispatch block of §10.36 is full (52 used, 7 NOPs), so the
handlers went into the **45 bytes of the removed credits create** at `0x404E80` - inside the same
menu function, so `ebp` and the locals are the caller's - and the pad became two jumps. **Those 45
bytes are live code**: `main.c bintro` runs straight through them on every menu init (the credits
create used to sit there; §10.36 made them NOPs for exactly that reason). The first build put the
handler at `0x404E80` without a guard and died at menu init with an access violation at exactly
that address (Windows Application log, fault offset `0x4E80`: `mov [eax+14F0h],0` with the init
path's `eax`), so the block now opens with a 2-byte jump over itself and the handler is entered at
`0x404E82`:

```
00405120: 75 16              jne  00405138        ; LOAD DC GAME's "not 7" now lands on the jmp below
...
00405136: EB 05              jmp  0040513D        ; LOAD DC GAME's own fall-through (was the NOP pad)
00405138: E9 45 FD FF FF     jmp  00404E82        ; ids 8 and 9

00404E80: EB 2B                            jmp 00404EAD                  ; the menu init passes by
00404E82: C7 80 F0 14 00 00 00 00 00 00   mov dword ptr [eax+14F0h],0   ; campaign, not training (gs+0x14F4 is 0 already)
00404E8C: 89 C2                            mov edx,eax                   ; game state
00404E8E: 8B 45 FC                         mov eax,[ebp-4]               ; screen
00404E91: E8 1A A5 07 00                   call 0047F3B0                 ; stub_jup_set (keeps eax, edx, edi)
00404E96: 83 FF 09                         cmp edi,9
00404E99: 74 0A                            je  00404EA5
00404E9B: E8 68 CD FF FF                   call 00401C08                 ; JUPITER MISSIONS: the campaign runner
00404EA0: E9 98 02 00 00                   jmp 0040513D
00404EA5: E8 FA EB FF FF                   call 00403AA4                 ; LOAD JUP GAME: the load screen
00404EAA: EB F4                            jmp 00404EA0                  ; back onto the jmp 0040513D
00404EAC: 90                               nop
```

44 bytes + 1 NOP, no trampolines; the tool recognises and upgrades the crashing first form. `stub_jup_set` is the fourth 73-byte slot writer, at
**`0x47F3B0`** in the AUTO tail (the camera stub ends at `0x47F331`, `stub_dc_set` and its
trampolines at `0x47F3AA`; **7 bytes of the tail are left**, `0x47F3F9..0x47F400`); its four
`mov edi,imm32` operands add four HIGHLOW entries to the `.reloc` insert of page `0x7F000` (12 ->
**16** entries, 32 bytes; 20 of the section's 52 slack bytes left). `patch_ozi_menu.py` is at **v4**:
28 code / data edits and 20 `.reloc` edits, accepts the stock, v1, v2 and v3 forms of every site
and upgrades in place (the published v3 exe upgraded to the same bytes the patcher builds from
the original).

**The menu: nine rows, anchored on the bottom edge.** The left column is now ACADEMY, DARK COLONY,
LOAD DC GAME, COUNCIL WARS, LOAD CW GAME, OZI MISSIONS, LOAD OZI GAME, **JUPITER MISSIONS (8), LOAD
JUP GAME (9)**, with the 12 px group gap after rows 1, 3, 5 **and 7**; MULTI PLAYER WAR and
ENCYCLOPEDIA stay on rows 1 and 2 of the second column, QUIT on its last row (9). Anchoring the
block on the script's own bottom row, as §10.36 did, no longer works: at 1024x768 that row is 606
and nine rows with four gaps (256 px above it) would start at 350, **under the title sprite**
(`DCUT` at 352..385). Measured on every painted backdrop (`INTRG.GIF`, black run over the button
columns), the bottom artwork starts at **`H - 45`** at every size (435 / 675 / 723 / 755 / 979 /
1035 for 480 .. 1080 rows) because `paint_intro.py` copies the stock bottom band 1:1 at the same
distance from the bottom edge, and the stock 640x480 bottom row is 408 = `480 - 72`, 2 px above
the art. So the rule is now **bottom row = `H - 72`** at every size, which reproduces the stock
row at 640x480 and gives the HD blocks the room they need; both generators shrink the gap while
the first row would touch the crescent (640x480, limit 218) or the title (HD: the lowest edge of
the non-`LARGEBUTTON` gadgets + 12), and refuse if it still does not fit. Rows: 1024x768
**440..696** (title ends 385), 1280x720 392..648, 1280x800 472..728, 1280x1024 696..952. **At
640x480 nine rows do not fit at all**: 9 x 26 = 234 px without any gap against the 217-row band
between the crescent (218) and the art (435), so there the left column keeps its seven rows of
§10.36 (219/256/282/319/345/382/408) and the two Jupiter buttons take **rows 4 and 5 of the second
column** (x 326, y 319 / 345 - their own group, level with COUNCIL WARS / LOAD CW GAME). Both
`build_ozi_overlay.menu_layout` / `menu_script` and the patcher's `Edit-OziMenu` implement this
(`OZI_COLUMNS` / `OZI_COLUMNS_STOCK`, `BOTTOM_FROM_EDGE`, `TITLE_CLEARANCE`; the gap count now
counts only gaps inside the block), accept the stock 2x4 grid, their 23 Sep output (ids 19/20 +
8/9, 10 `banim` pairs) and their own (12 pairs), and refuse anything else by name.

**The overlay `DC - Council wars/jupiter/`** (new tool `tools/build_jupiter_overlay.py`, dry run /
`--apply`, input = the extracted installer folder, default
`<game>/../../Dark-Colony-development/DC - Jupiter mod`):

* `gamestat/gamestat.txt`, `depend.txt`, `unitid.txt`, `weapstat.txt` - the balance tables, read per
  game through the prefix (the four `gamestat/…` strings of DGROUP; the exe has no `gxmestat`
  string, `exp/gamestat/gxmestat.txt` is a leftover nothing reads).
* `sound/slist.dat` - the unit sound lists, read per game (`0x431390` from the game-start init
  `0x41EB40`), **minus the line `63 GUN 200 -1`**: the start-up sound table has room for exactly
  200 entries (`0x4309C8` clears `0..0xC7`, `0x4DFF30` + 116 bytes each, the list loader asserts
  `i < NUM_SOUNDS` at `0x431430`) and stores the index without a bound check, so the mod's own
  installer relied on entry 200 landing 116 bytes past the array. Every entry's WAV is opened at
  start-up by the sound module's load-all pass (`0x430DD3`, reached through the module's function
  table, loop over 0..199 at `0x430EA1` -> the wave loader) under the Council Wars prefix, so a
  **per-mode sound is not possible**: a 201st entry does not fit and replacing one of the 200 would
  reach every mode. `ion.wav` (the report of weapon 63 ZIMAL, the artifact weapon, silent in the
  stock game) is left out; the maintainer can trade a shared entry for it if wanted.
* `intrface/maine` - the mod's HUD verbatim, read as `jupiter/intrface/maine` by a 640x480 build.
* `intrf_hd/maine` - the same HUD laid out for the HD set (size from `exp/intrf_hd/bintroe`):
  `hud_layout.shift` on every widget (right panel + growth, bottom bar down, the panel's bottom
  cluster both, PAUSED by half), the `size` line, `background intrf_hd/intrface`. The tool checks
  that this transform reproduces the root `INTRF_HD/MAINE` from `INTRFACE/MAINE` (it does), and the
  patcher's `Write-InterfaceSet` writes the same file with `Edit-HudScript` + `Set-BackgroundHd`.
* `intrf_hd/bintroe` - the patched menu, written by `build_ozi_overlay.py` (the mode is sticky, so
  the menu is reloaded through `jupiter/`), at 640x480 `intrface/bintoze` by the patcher
  (`Write-StockOziMenu`, `.gitignore`d like the other copies).
* `../jupsave/jupsave.txt` - the save folder marker.

Not copied, and why: everything identical to the root or `exp/`; the mod's menu and backdrops (the
patched build has its own menu; `intrg.gif` would only show in this mode at 640x480 and its layout
does not match); `credits.txt` (the box is gone); `human09.tro` (the typo); caches and stray files;
the installer's binaries. **Animations: the mod has none of its own** - every `animate/` and
`sprites/` file is the Council Wars one, and its `anim.dat` is `exp/anim.dat`; the six new types
reuse the TRSC / GRAY / SAUC / TRUK banks.

**Patcher.** `ozi_data` lists the `jupiter/` tree and `jupsave/jupsave.txt` (from `git ls-files`,
so the overlay has to be in the index; 394 files for the fix now), `Write-InterfaceSet` writes
`jupiter\intrf_hd\bintroe` + `maine`, `Write-StockOziMenu` the 640x480 copy; the `.reloc` insert
note reads its length instead of saying 16. Regenerated `Apply-DarkColonyPatches.ps1` (998 KB).
Published 1024x768 outputs: Council Wars **`c9a8b838…`** (= the v3 exe upgraded in place by the
tool, byte-identical; the crashing first build was `671c58d2…`), 640x480 `0e49c9f8…`; Classic
untouched `a71d038b…`. `dcexp16.asm` regenerated from the new exe.

**Checks made.** Python and PowerShell menu scripts byte-identical for the stock 640x480 script,
the four `hd_sets/` fixtures (rewritten to nine rows) and the game folder's 1024x768 script;
both idempotent; `build_ozi_overlay.py` reports the three copies identical; a full `-All` patcher
run into a scratch copy at 1024x768 rebuilt the exe and every interface file (`exp`, `dc`, `ozi_ns`,
`jupiter` copies, `INTRF_HD/MAINE`) byte for byte, and a 640x480 run wrote the four `bintoze` copies
and matched the reference hash; `patch_ozi_menu.py verify` says v4 on both. The new code was read
back from the regenerated disassembly.

**Game test (23 Sep 2026, night).** Two lessons before the result. (1) A first scripted run
(`subst V:` on the game folder, SPACE, click JUPITER MISSIONS (416,682) -> START CAMPAIGN (680,506)
-> NEXT (780,606) -> TO BATTLE (691,603), screenshots) happened while the desktop was **locked**
(`LogonUI` alive, foreground window "Windows Default Lock Screen"): every capture came back black
and the game died with `0xC0000005` inside `igd9trinity32.dll` (the Intel D3D9 driver under
DirectDraw) on the first click - for the DARK COLONY control button too. A full-screen test on a
locked desktop proves nothing; check `Get-Process LogonUI` first. (2) Unlocked, the first build
died at menu init (fault offset `0x4E80`, above): the live-code mistake, fixed as shown. With the
fix the run went through: the nine-row menu as designed (screenshot: ACADEMY .. LOAD JUP GAME left,
MULTI PLAYER WAR / ENCYCLOPEDIA / QUIT right, title clear above), JUPITER MISSIONS -> the race
overview (HUMAN / GRAY, leader name) -> START CAMPAIGN -> briefing -> TO BATTLE -> the first
mission's battlefield (landing message, two units, HUD frame), and LOAD JUP GAME -> its load screen;
the process alive throughout, `error.log` empty, no Application-log event. Captures need a
**DPI-aware** child process (`SetProcessDPIAware` before the screen-DC BitBlt) - from the
DPI-unaware test process the exclusive surface reads back black. Still open: the HUD's new build
buttons in play (the capture shows the landing intro), a save round-trip through LOAD JUP GAME, the
640x480 menu in game, and where the unit names of the new types come from (`unitid.txt` maps them
to text ids 289..296, but the mod ships no text file with those ids - the mod's own menu never
offered a campaign, so its authors may never have seen the race overview or the encyclopedia with
these types). Medals: as in §10.36.


#### 10.38 One-shot installer: all three executables, new names, the high-resolution icon **(25 Sep 2026, maintainer: "all three files for patching must be selected by default so installer patches all in one shot. resulting files and shortcuts names must be: 'Dark Colony map editor 1.2', 'Dark Colony', 'Dark Colony Ultimate'. Based on DC.ICO create a high resolution icon and apply it to all three patched files", then "make them [the letters] a bit 3 dimensional" and "gray frame ... must be thinner. use best practices of icon drawing"; the day's earlier steps - `INSTALL.CMD`, the `-Output` fix, desktop shortcuts - are in plan §16)**

**Names.** The patched builds are `DC - Council wars/Dark Colony.exe` (was `dc16new.exe`), `DC - Council wars/Dark Colony Ultimate.exe` (was `engexp16new.exe`, before 15 Sep `DCEXP16.EXE`) and `Dark Colony - Map editor/Dark Colony map editor 1.2.exe` (**`Dark Colony Map Editor.exe` since later that day**, maintainer: "rename 'Dark Colony map editor 1.2' to 'Dark Colony Map Editor'" - `git mv`, patcher `OutputName`/`ProductName` = shortcut name, README/HOWTO/INSTALL.CMD; the bytes and the SHA-256 `c72dd205…` are unchanged; was `maped_ozi_ns_v1.2.exe`); the desktop shortcuts carry the same names without `.exe` (build field `ProductName`). Renamed in git with `git mv`, so history follows. Checked before renaming: the games call `GetModuleFileNameA` only from the Watcom C runtime (`0x45A5B2`, `0x45A6CF`, `0x46C820`), open their data by relative paths and never look at their own name; the editor does not import it at all. No name contains `patch`, `setup`, `update` or `install` (the UAC installer heuristic of 15 Sep; `INSTALL.CMD` is a batch file, `cmd.exe` has a manifest). All three started from a `subst` drive under the new names (both games 9 s past the intro, the editor to its window), `error.log` empty, no Application-log event.

**Window = a classical installer** (the maintainer's fourth request of the day, after a list with one Browse button, three rows with a Browse button each and a tab per checklist: "let's do the classical installer way for patch region instead of tabs. on opening there is a greeting message and button forward. second screen contains options for patching DC, third screen for patching CW and fourth for patching maped"). A fixed-size dialog with a white header band (title + subtitle of the step), five pages and a navigation bar (`Inspect an exe...`, a log line, `< Back` / `Next >` / `Cancel`; Next is the default button, Cancel the cancel button). **Welcome**: greeting, what becomes what, the list of the originals found at `<script folder>\<OriginalPath>` (paths shown relative to the script folder, "OK" = SHA-256 of the untouched original), the desktop-shortcut option (ticked). **Step 1..3 of 3** (Dark Colony, Dark Colony Ultimate, map editor): "Patch <name>" (ticked when the original was found), the original's path + its own Browse (dialog opens in that page's folder, the original's name as the first filter; a file of another build lands on that build's page and the log says so; the "already patched, original beside it" redirect of 21 Sep kept), the status lines, the screen resolution on the two game pages (two drop-downs kept equal, because both games share the one `INTRF_HD` folder), "Select all fixes" + the fix checklist + the page's own description box, "Written to". Each page keeps its own unticked fixes and unavailable fixes (resources checked against its output folder) across resolution changes. On step 3 Next reads **Patch**: `$script:gui.Apply` (unchanged: checks every ticked executable first - no fix, output = input, data problems: one message, nothing written - asks once about existing outputs, the "in progress" box, one result message box) and then the **Finished** page with every result line (Back and Cancel disabled, Next reads Close). Test hooks: `$script:gui.GoTo <step>`, `Apply $false` (returns one result per executable: `Item`, `R`, `Error`, `Shortcut`, `Kind`, `Line`), `Load`, `Items` (`.UI` = the page's controls), `Results`; `$c.All` / `List` / `Info` / `Out` / `Status` / `Res` are re-pointed to the current page. Every page control carries its executable's index in `.Tag`, because the handlers run outside `Show-PatcherWindow` (no closures). `DrawToBitmap` mis-draws a `TabControl` (the intermediate tab version); the wizard has none and captures cleanly. Command line: `-All` without `-Original` patches all three originals beside the script (`-Patches` and `-Output` need `-Original`); exit code 1 if one failed.

**Wrong or missing original = a big red box** (maintainer, same day: "if original of one of files are not originals, then bring in front a big red error and offer to select a correct file or to download it from original discs or our repo"). `SetError` puts an item into an error state for five cases - `missing` (nothing at `OriginalPath`), `unreadable`, `unknown` (neither the SHA-256 nor the layout of any build; lands on the page it was picked on, `Load`'s new `$target`), `patched` (the first fix is applied and no untouched original sits beside it) and `modified` (a known layout with another SHA-256 - **changed from 21 Sep, when such a file was accepted with an orange warning and per-byte checks; the command line's `-Force` still allows it**). The item then has no data, its "Patch <name>" box is unticked and locked, and its page shows a red panel in place of the resolution, checklist and description (those controls are hidden, so the panel is in front whatever the drawing order): a title such as "dc16_modified.exe IS NOT THE UNTOUCHED ORIGINAL", found vs. expected (path, size, SHA-256), and three remedies - **Select the correct file...** (the page's Browse), **Download from our repository** (`Start-Process` of the build's `RepoUrl`, the file's GitHub page - the script itself still downloads nothing) and the build's `SourceNote` for the original disc: Council Wars CD `EXPENG\ENGEXP16.EXE` and Dark Colony CD `DC\MAPED.EXE` are byte-identical to the repository originals, while the Dark Colony CD's `DC\DC16.EXE` is the August 1997 build (660480 bytes) that the fixes do not fit, so for `dc16.exe` only the repository helps. The welcome page adds a red banner listing every such original ("!!" in the found list); choosing the right file clears it. The three `RepoUrl`s answered 200 on 25 Sep 2026. Tested headless under 5.1 and 7 with a one-byte-changed `dc16.exe` copy, a lone patched `Dark Colony.exe`, `notepad.exe` picked on the Ultimate page and a missing editor original: titles and states as described, locked include boxes, Apply refused with every page in error, errors cleared by loading the originals again, the normal flow unchanged. Found on the way: an earlier edit of the found list made through a shell heredoc had lost its backslash literals (`TrimEnd('') + ''`, working by accident); rewritten with `[IO.Path]::DirectorySeparatorChar`.

**Icon.** `tools/make_dc_icon.py` re-draws `DC.ICO` (32x32, 16 colours: two-tone grey frame, black field, bevelled "DC", Mars rising from the bottom right) from its measured geometry in the 32-unit grid - letter strokes on rows 4/8 (D bars x 5..13, stem 7, bow 13; C bars 18..25, back 17, terminals 24), planet centre (17, 34) radius 22 - at 4x supersampling into `DC - Council wars/DC_HD.ICO`: 16, 20, 24, 32, 40, 48, 64 as 32-bit bitmaps with an AND mask for transparency, 96 and 256 as PNG (82 440 bytes). The letters are solid blocks: extruded towards the bottom right (the original's grey pixels right of and below every stroke read as exactly that), side walls shaded by the direction they face, chamfered front face lit from the top left with a specular edge, soft cast shadow. The planet is a lit sphere (light from the right and above, soft terminator) with a fixed-seed value-noise surface evaluated on the sphere (no seam), dark maria and a thin bright limb. Icon practice applied after the second request: line widths chosen per size in whole pixels instead of scaled (frame 1 px at 16..24, 1+1 px at 32..64, 2+3 px at 256, where the scaled frame had been 16 px), softly rounded anti-aliased corners and a transparent margin (1 px at 48/64, 4 px at 256), premultiplied box filtering so the edge keeps its grey, the design filling the field the frame leaves, and simplified small sizes (below 32 px: flat letters with a one-step drop, only the large surface features). A preview sheet (`--preview`) shows every size on a light and a dark strip beside the original.

**Fix `icon`** (`tools/patch_icon.py`, every build, always last): the file gets a NEW section `.dcicon` (file offset = end of file rounded up to FileAlignment - the editor's file ends unaligned at `0x52228` after `.debug` - VA = SizeOfImage, characteristics `0x40000040`) holding a complete resource directory plus the nine icon images and the group directory. The directory's entries for every other resource point at their OLD RVAs, so nothing moves: the editor's 73 dialog/menu/string/bitmap/accelerator resources stay in its `.rsrc` - where `blocksets`/`teams`/`healer`/`troopsframe` edit them, which keeps those fixes verifying - and only the old RT_ICON/RT_GROUP_ICON entries are left out. Groups: the games keep `DC16` and get the same group under id **101**, the id `create_window` passes to `LoadIconA` (`0x42E69E push 65h`, Classic); the stock exes have no id 101, so the game window had the default icon - after the fix its class icon is set (checked on the running game: class icon handle non-zero). The editor, which had no icon at all, gets group id 1 (its own code only loads the system icon `0x7F01`, so its title bar keeps the default; Explorer and the shortcut show the new one). Header edits (4): NumberOfSections +1, the 40-byte section header in the zero slack after the table, SizeOfImage (games `0x142000 -> 0x155000`, editor `0xA60000 -> 0xA80000`), resource data directory -> the new section. No code, no `.reloc`. The files grow by 83 481 (games) / 88 046 bytes (editor). Checked from a 32-bit process: `LoadLibraryEx(DONT_RESOLVE_DLL_REFERENCES)` maps all three, `FindResource` finds groups 101/`DC16`/1 and the editor's `MAPSIZE` dialog (836 bytes, as before); `PrivateExtractIcons` returns the 256 image.

**Patcher.** New edit kind `@{ Append = <file length before>; Sha256; Length; Base64 }` - state 'old' = the file ends at `Append`, 'new' = it is `Append + Length` long and the tail's SHA-256 matches (computed with `ComputeHash(data, offset, count)`, no PowerShell byte loop); `Invoke-Patch` applies the in-place edits first and the append last and refuses bytes whose SHA-256 differs from the stated one; `Find-BuildByContent` also accepts the grown size; `-List -Detail` prints the append as one line. Base64 instead of hex because it is a third smaller and `[Convert]::FromBase64String` is fast under 5.1 (a 75 KB hex string through the script's hex parser would loop per byte). The script grew from 0.82 to 1.19 MB; `-List` 2.3 s under 5.1, 1.2 s under 7. Published 1024x768 builds: `Dark Colony.exe` **`e9cc0561…`**, `Dark Colony Ultimate.exe` **`dcd973eb…`**, `Dark Colony map editor 1.2.exe` **`c72dd205…`**; without `icon` the same runs still give the previous `a71d038b…` / `39a49a3b…` / `1edc261a…`, i.e. the fix touches nothing else. Tested under 5.1 and 7: `INSTALL.CMD -All -Resolution 1024x768 -Overwrite -DesktopShortcut` on a scratch copy of the repository (three outputs byte-identical to the published builds, three shortcuts, 7 s), the headless window (three rows ticked by default, 15/14/5 fixes, one Apply = three results and three shortcuts, unticking a row on the shown form leaves it out), `-Verify` on old and new builds. `dc16.asm` / `dcexp16.asm` regenerated from the new published exes. Known and older than this change: `-Verify` reports `camera` as MIXED on every published build since `widemap` re-pointed the call `camera` had redirected.

#### 10.39 Network compatibility of Dark Colony and Dark Colony Ultimate: two fixes **(25 Sep 2026, maintainer question "check carefully, are classic and ultimate network games 100% compatible?", then "do both fixes and update the docs. get sure that ozi missions will not be broken without this animation", then "skip the game test, rebuild the exe"; static analysis + engine replays, NOT run in the game)**

**Answer before the fixes: no.** The two builds were the same network program but did not always load the same simulation data, and two differences could desynchronise a mixed game (and a Council Wars client against the relay server's `SYNC_CHECK=send` checksums, which come from the Classic data of the engine port).

What was checked and is identical (so the rest of the answer rests on data, not code):

* **Code.** A relocation-aware byte comparison of the two untouched exes (`dc16.exe` 1998 vs `ENGEXP16.EXE`; every HIGHLOW pointer compared through its target, every rel32 call through the target it reaches): outside `0x405000..0x406600` (main-menu handler, start-up, `safefunc.c`, the inserted `exp/` open helper `0x4063E4`) and the four medal constants (`0x403FC7`ff, `DC16_SINGLE_EXE_MERGE.md` §5.3) and the credits y, the code is the same - all 3 164 AUTO->AUTO pointers beyond that region map with +0 / +0x60, every call site agrees. Protocol, lockstep, checksum `0x44ABC0`, RNG, AI: one program.
* **Game state.** All 6 576 pointers into `.bss` have delta 0: the game state, the object and weapon tables and the type count `0x518B34` sit at the same addresses in both.
* **Constants.** DGROUP compared through the piecewise shift (+0 / +8 / +0x28): identical but for `save`/`esave`, `hbnfufl.a01`/`a02` and the CD message - the 256-entry rand table `0x488F20` included.
* **Patches.** Both published builds carry the same simulation fixes; build-specific are only `movies`/`sounds` (Classic) and `ozi` (Council Wars), none of them in the simulation.
* **Tables in the default mode.** `exp/gamestat/gamestat.txt` = Classic's 106 rows unchanged + 12 Council Wars rows (106..117); `weapstat`, `boomstat`, `mbullet`, `unitid`, `depend` come from the shared root. The type count is read only by a sound-id assert (`0x431E29`) and by the save/load of the upgrade bytes (`0x43C484`/`0x43C4F8`), never by the simulation, and the engine port uses a constant 130 for its per-type stats; so the 12 extra rows are harmless.
* **RNG consumers.** The 32 call sites of `rand` `0x4120F0` (CW `0x412150`, also 32) and every inline use of the index `0x489320` are in `mobiles.c`, the triggers, the missiles, the start shuffle and the AI - no sound, ambience or display code draws from it.

**Difference 1 - the Gray commander rally (data).** Council Wars' start-up animation list (`exp/anim.dat`, and `exp/animozi.dat` of the patched build) loads twelve FINs of its own BEFORE the Classic list. None duplicates a Classic name, but two add animations a Classic type finds by its sprite name: `troo.fin` = `TRSCDEPLOY0..14` (1 frame each) and `grrr.fin` = `GRAYDEPLOY0..14` (14 frames each, plus `GRRR`/`INSP` that nothing references). The deploy slot `type+0x94` is filled at game start by `0x43C16C`..`0x43C1A7` with `<sprite>DEPLOY`, falling back to the STAND set (the same pointer) when no such animation exists - which is Classic's case for every type. So in Council Wars the Security Trooper, the Gray and the eight commanders (types 0, 8, 69-76) had their own deploy set. The slot is only ever passed to `startAnim` (rally/deploy order `0x416A1C`, heal `0x41436E`, mining tower `0x4139D5`, wreck); the one place where its length matters is state 0xD `0x417DA4`, which waits for the animation to finish before the commander rally `0x417400` links units (`+0xD6 = 20 + (rand & 15)`, `DC16_BATTLE_ENGINE.md` §10.2). Human commanders: `TRSCDEPLOY` 1 frame of 2 ticks = STAND's 2 ticks, same timing. **Gray commanders: 14 frames = 28 ticks against 2**, so the rally, its `rand()` draws and the commander's return to idle happened 26 ticks later in Council Wars. Every multiplayer player gets a commander (lobby var 6, Commander Rank). Measured with the engine port (`src/engine`, bit-exact to Classic): the 19 battles in `Dark-Colony-Server/logs/replays` were replayed three times in lockstep - Classic sprite data, the Council Wars view before the fix (`tools/sprdata2json.js` on root + `exp/animate`, `exp/sprites`, `exp/animozi.dat`), and after it. **All six recordings with a Gray commander rally diverged 3 to 10 ticks after the first rally** (ticks 3128/3137, 3402/3412, 3942/3950, 3998/4001, 4076/4079, 3216/3221); the thirteen without one stayed identical; the fixed data is identical to Classic in all nineteen.

**Fix 1 = data:** `exp/animozi.dat` (read only by the patched Council Wars build, `ozi` DGROUP site) no longer lists `grrr.fin` and `troo.fin` (`tools/build_ozi_overlay.py`, `NOT_IN_OZI_ANIM`; idempotent). The files themselves and the stock `exp/anim.dat` stay (the original exe reads them; CD rule of §10.17). Result checked with `sprdata2json.js`: for the 106 Classic types, all weapons and all blast types the animation data - durations, bounding boxes, muzzle hotspots, and which slots share the STAND set - is identical to `data/classic/sprites.json`. **OZI and Council Wars are not broken by it:** nothing else names these animations (no script, scenario, table, FIN frame part or exe string - the set lookup builds `<sprite>DEPLOY` from the suffix only), the affected slots fall back to STAND exactly as every Classic unit does, and the only change in the Council Wars (118 types) and OZI (126 types) table sets is the `deploy` field of the TRSC/GRAY-sprite types - in OZI the same ten plus its own Human captain/major 118/119, which now rally in the STAND pose like Classic's Human commanders (same 2-tick timing). The pack's own units (Dalgar, Observer/`SPYO`, Reaper II/`REAE`, the VTOL scout, transmitter/generator) are unaffected. Cost: the Gray and Human commanders of all three campaigns lose their deploy pose in the Ultimate build (cosmetic; a Gray rally now takes 2 ticks there as in Dark Colony).

**Difference 2 - the sticky menu mode (code).** MULTI PLAYER WAR (`cmp edi,3` at `0x40508D`, `call 0x405C20` at `0x405097`) entered the network screen without a mode stub, and a network battle loads its tables per game start through the prefix. After OZI MISSIONS or LOAD OZI GAME in the same session, a network game therefore read `ozi_ns/gamestat` (126 types; weapons 25/26 at half damage, other `boomstat`/`mbullet`/`unitid`) and went out of sync against everybody. **Fix 2 = `ozi` item 8** (`tools/patch_ozi_menu.py`, now v4): the call goes to `tramp_dc_net` at `0x47F3B0` (`E8 8B FF FF FF` = `call stub_dc_set 0x47F340`, `E9 66 68 F8 FF` = `jmp 0x405C20`), 10 bytes of the AUTO zero tail after `tramp_dc_load`, relative operands only, no `.reloc` change; 70 tail bytes left (`0x47F3BA..0x47F400`). A network game now always runs in the Dark Colony mode (`dc/` + `save`): every table from the game root, i.e. the 106-type Classic set of `dc16.exe` and the engine port; the menu comes back through `dc/intrf_hd/bintroe` (or the 640x480 `dc/intrface/bintoze`) like after a DARK COLONY campaign. Registers: `stub_dc_set` saves eax/edi and touches nothing else, so the screen/game-state arguments in eax/edx reach `0x405C20` intact (the same contract as the two campaign trampolines).

**Builds.** Council Wars 1024x768 `7f5e5322…` (was `dcd973eb…`; 13 bytes: file `0x4498..0x449A` and `0x7E7B0..0x7E7B9`), 640x480 `e6f375e8…` (was `faa3511e…`), 1280x800 `2b430a8f…` (was `1859fa0a…`, the maintainer's working copy, rebuilt with the exe copied back alone so the interface set stayed); Classic `e9cc0561…` and the map editor unchanged. Tool chain = patcher byte for byte (PowerShell 5.1 run on a scratch copy of the game folder: "byte-identical to the executable published in the repository" at 1024x768, "to the reference build" at 1280x800); `dcexp16.asm` regenerated (differs from the previous dump only at the two edits). **Not run in the game** (maintainer decision): the MULTI PLAYER WAR click after OZI mode, a network battle from the Ultimate build, and an OZI mission with the new animation list. Still true for the untouched exe: stock `ENGEXP16.EXE` reads the stock `exp/anim.dat`, so it keeps difference 1 - and since §10.35 its menu script (`exp/intrface/bintroe` = Classic's grid) does show MULTI PLAYER WAR - but not difference 2 (it has no modes; its prefix is always `exp/`, whose tables are harmless). Only the patched Ultimate build is network-compatible with Dark Colony.

#### 10.40 The main menu's opening wave: `banim` decoded, the Ultimate plates now animate top to bottom **(25 Sep 2026, maintainer: "ultimate executable main screen. initial animation of buttons are in wrong places. animation must go from top buttons to bottom buttons"; data + tools only, no exe byte changed; confirmed in game)**

**What the maintainer saw.** When the Dark Colony Ultimate menu opens, the ten button plates play their
one-shot animation one after another and each label appears when its plate has settled. Since the
seven-row block of §10.36 the sequence hopped about the block: row 4, row 1, row 5, right row 1, row 7,
right row 2, row 6, right row 7, row 2, row 3. The stock 2×4 grid never showed the problem because the
generators kept the stock *pair order* while moving the buttons.

**How the wave works (button.c, read from `dc16.asm`; Council Wars = +0x60).** The widget keyword
table at `0x489588` is a list of `{name pointer, creator}` pairs (`… "textmsg"→0x42259C,
"gadget"→0x424A40, "label"→0x426BD4, "banim"→0x427854`, NULL-terminated at `0x489608`) — which also
corrects §10.15's reading of it: `0x424A40` is the **`gadget`** creator and the `label` (label button:
keywords `label`, `align`, `font`, assert text "reading label button widget") creator is `0x426BD4`;
both parse `x y w h` through `0x422414`, so the `POSITIONED` fix of §10.15 stands. `create_banim`
`0x427854` parses `banim <id> <desc> <n> <m> g1..gn b1..bm` (every id `< 300`, `MAX_OBJ`; asserts at
button.c lines 532/536) into a 16-byte record at `widget+0x28`: `{n, m, ids*, buttons* = ids + n}`.
The runtime `0x4279EC`, called from `0x427AE4` for every widget of type `0x0C` when the screen opens,
is a **blocking loop** that pumps the interface (`0x424294`) while `i < n`:

* when plate `k`'s animation **frame** (`0x425034`; §10.47 corrects the 25 Sep reading: this getter returns the frame index, `0x4250CC` the mode, and the modes are 0 loop / 1 one-shot / 2 stopped = finished) is 2, it **starts plate `k+1`**
  (`0x425164(ip, g[k+1], 1)`, plus display method `+0x7C(0xBA, 1)`, the same call it makes on entry)
  and advances `k`;
* when plate `i`'s own state (`0x4250CC`) is 2, it finalises it (`0x42436C(ip, g[i], 0)`), advances
  `i`, and **reveals the next button** of the button list whose byte `ip+0x8A+0x34·id` is set
  (`0x421AA4`; buttons with the byte clear are skipped).

So the first listed plate must carry **`anim_oneoff`** — nothing else starts it; that is what the
flag does, which closes the open question of §10.35 — the **order of the pairs is the order of the
wave**, and each pair's button appears when its plate has finished. Measured at 20 Hz on the fixed
1024×768 menu: the ten plates settle 11.57 … 12.19 s after launch, about 60 ms apart.

**The fix** (`build_ozi_overlay.menu_order` / the patcher's `Edit-OziMenu`): the pair order is derived
from the layout — **column by column, left column first, each from top to bottom**, the stock grid's own
sequence (its `6 7 8` = left column, `9 10 11` = right column, `17 13` = the later-added bottom row) —
and the plate of the first button in that order gets `anim_oneoff`, the other nine `anim_stopped`
(9th token of the gadget line; the two cloned Dark Colony plates included). Ultimate at every size:
ACADEMY → DARK COLONY → LOAD DC GAME → COUNCIL WARS → LOAD CW GAME → OZI MISSIONS → LOAD OZI GAME →
MULTI PLAYER WAR → ENCYCLOPEDIA → QUIT; `banim 18 0 10 10  20 21 22 19 8 17 10 9 11 13  1 6 7 0 2 16 4
3 5 12`, `anim_oneoff` on gadget 20. (A strict row-major wave — ACADEMY, MULTI PLAYER WAR, DARK COLONY,
ENCYCLOPEDIA, … — is a one-line change of the sort key; the column order was chosen because it is what
the stock menu does and the left column carries seven of the ten rows.) Data regenerated: the three
identical 1024×768 copies `exp/intrf_hd/bintroe`, `ozi_ns/intrf_hd/bintroe`, `dc/intrf_hd/bintroe`
(`build_ozi_overlay.py --apply`, only these three changed), the four `hd_sets/<WxH>` fixtures, the
640×480 `exp\intrface\bintoze` through the patcher. Checks: the PowerShell editor under 5.1 and 7 is
byte-identical to the Python tool on all six inputs (four fixtures, the game folder's script, the stock
640×480 grid) and both are idempotent; the committed blob is LF-only and the checkout CRLF, so compare
after stripping CR. Classic's `INTRF_HD/BINTROE` keeps the stock order (its grid did not move).

**Game test** (scratch copy of the game folder on `subst W:`, `avi\INTRO.AVI` renamed away so the menu
comes up directly at 10.6 s, 20 Hz screen-DC capture from a DPI-aware ctypes process, per-plate MD5
of the 179×25 rects): the plates settle in exactly the intended order — ACADEMY 11.57, DARK COLONY
11.62, LOAD DC GAME 11.67, COUNCIL WARS 11.74, LOAD CW GAME 11.84, OZI MISSIONS 11.89, LOAD OZI GAME
11.94, MULTI PLAYER WAR 12.05, ENCYCLOPEDIA 12.10, QUIT 12.19 s — each label appearing as its plate
finishes, the logo, title and credits box drawn after the wave, `error.log` empty. A posted ESC did
not quit the game (as noted before), the process was terminated. Not run: the other resolutions and
the 640×480 build.

#### 10.41 Music source DC / CW / ALL for Dark Colony Ultimate: a MUSIC row in the battlefield options, per-campaign defaults, a shuffle **(25 Sep 2026, maintainer: "ultimate version. now let's update musical selection. battlefield menu for sounds. add new entry for music selection with possibility to select: original game, expansion pack, shuffle them all. entries must be named: DC, CW, ALL. ACADEMY and DARK COLONY must play DC music by default, COUNCIL WARS must play CW by default, OZI MISSIONS must play shuffled all. But in sounds menu client can select any setting in any time", then "640x480 version must contain the same musical menu"; Ultimate only, confirmed in game)**

**What it does.** The options dialog of the battlefield (Game Option tab → Options, `LOPTE`) has a fifth
row **MUSIC** between CDROM VOLUME and GAME DETAIL, with the dialog's usual `-` / `+` buttons and a value
text **DC** (the Dark Colony disc, `MUSIC\TRACK02-05.MP3`), **CW** (the Council Wars disc,
`exp\music\track02-05.mp3`) or **ALL** (all eight tracks in a random order). A press switches the
music at once. Starting a campaign from the main menu sets the default: ACADEMY, DARK COLONY, LOAD DC
GAME and MULTI PLAYER WAR → DC, COUNCIL WARS and LOAD CW GAME → CW, OZI MISSIONS and LOAD OZI GAME →
ALL. The setting lives for the session (not saved); Dark Colony (Classic) is untouched and keeps
§10.31's one-set player.

**The module** (`tools/music_asm.py`, keystone at development time; the bytes and their fixup table are
pasted into `patch_music.py`, which stays stdlib-only; the seven entry points and the aux-walk hook
keep their offsets; 805 code bytes of the 0x751, 21 absolute operands against 41 stock / 22 earlier
`.reloc` entries in the page). State block (the dead TOC array `.bss 0x5327D0`): +0 device, **+4 track
index 0..7** (set = index/4, file = `TRACK0<2+index%4>`; replaces the track number), +8 volume, +12 the
built name, **+36 source** (0 DC / 1 CW / 2 ALL), +37 shuffle position, +38 order[8], +48 LCG state.
`cd_open` (every main-menu init) seeds the LCG from `rdtsc` (the game's `rand()` drives the simulation
and is never touched) and opens the first track of the current source without playing;
`cd_seek_track` (battle start) and a source change call `start_source` = close, `first_idx`
(DC/CW: the set's TRACK02; ALL: a fresh Fisher-Yates shuffle over the LCG, never starting with the
track that just ended), `open_track` (two templates, digit written into the copy), `play_dev`;
`impl_mode` on `MCI_MODE_STOP` plays `next_idx` (DC/CW: the set's four in a ring; ALL: the next of the
order, reshuffled after the eighth). Layout: `open_track` +0x110, `setvol_dev` +0x200 (the aux hook's
target, unchanged), `impl_mode` +0x240, `first_idx` +0x2E0, `shuffle` +0x340, `start_source` +0x3A0,
`opt_tail` +0x3C0, `opt_refresh_tail` +0x420, `play_dev` +0x450, `next_idx` +0x480.

**The dialog hooks** (interface.c, Council Wars addresses, pattern-located on the code *after* the
edited bytes so a patched exe is recognised): the options handler `0x432CE4(ctx)` reads the event with
`0x42417C(ip, &id)`, handles the button ids in a `cmp [ebp-8], id` chain and ends in `test bl,bl /
call 0x432584` (close when OK/cancel set the flag); its tail `0x432F20` (11 bytes) is now `mov eax,esi;
mov edx,[ebp-8]; call opt_tail` — `opt_tail` steps the source for id 71 (−) / 72 (+) modulo 3, restarts
the music when the layer's flag `0x489770` is clear and a device is open, calls the refresh routine
`0x432C28`, then does the original close test. **Every event kind reaches that tail** (`cmp eax,1 / jne
tail` at `0x432D02`), and the original tail was harmless for the others only because `bl` is set by
press events alone — the first build stepped twice per click (press + release). The `jne` now goes to the
epilogue `0x432F2B` (the third edit, 6 bytes). The refresh routine `0x432C28` writes every value text
(`sprintf` → `0x423ED4(ip, id, str)`, GAME DETAIL through `textmsg(ip, 10+detail)` `0x422718`); its
epilogue `0x432CD6` (12 bytes) is now `lea esp,[ebp+0FEh]; jmp opt_refresh_tail`, which — **only when
`ip->objects[73].type == 4` (an `in_text`, `0x34` bytes per object from `ip+0x88`)** — sets widget 73 to
`textmsg(20 + source)` and runs the five pops + `ret` itself. So a stock dialog script (no widget 73)
is safe; `0x423ED4` asserts on a missing widget, `0x422718` returns "" for a missing message.

**The dialog script** is data, one transformation in Python (`patch_music.music_row`) and PowerShell
(`Edit-MusicDialog`, byte-identical under 5.1 and 7, idempotent): GAME DETAIL (pushb 44/45, in_text
48, label 63, cell picture 19), the bottom frame cell (picture 15) and OK / cancel (55/56) move down one
row (32 px), the new row takes GAME DETAIL's old place (pushb 71 / 72, in_text 73, label 74 = textmsg 6
MUSIC, cell 22), pictures 20/21 fill the frame, textmsg 20/21/22 = DC / CW / ALL, the erase rect grows
by a row. The Ultimate exe reads the dialog through its mode prefix, so the copies go to **`exp/`,
`dc/` and `ozi_ns/`**: at the HD sizes `intrf_hd/lopte` from the set's `INTRF_HD/LOPTE` (root stays
Classic's, row-less); **at 640x480** the exe would read `exp/intrface/lopte`, a file the original exe
reads too, so the DGROUP name `intrface/lopt` becomes `intrface/lopm` (one byte; the exe appends the
language letter) and the copies are `intrface/lopme` from the stock `INTRFACE/LOPTE` (the same trick
as the menu's `bintoze`, §10.27). `patch_music.py apply` writes them (`--width/--height` selects the
size), the patcher's `Write-MusicDialogs` after the interface set; `.gitignore` ignores the 640x480
copies, the 1024x768 ones are committed. `music` Data for Ultimate = all eight tracks.

**The defaults** are three bytes in the OZI menu's mode stubs (`patch_ozi_menu.py`, `ozi` v5): each
`stub_*_set` grows from 73 (77) to exactly the 80 bytes of its slot with `mov byte [0x5327F4], N`
before the `ret` (N = 0 / 1 / 2 for `stub_dc_set` / `stub_cw_set` / `stub_pack`; the address from
`patch_music.music_state()`, any form of the module), three more HIGHLOW entries in the page-0x7F000
block (15 + 1 pad, insert 32 bytes; the `.reloc` slack was 52). Without `ozi` the byte is dead `.bss`
and the source stays DC; without `music` the stubs write a dead byte. **MULTI PLAYER WAR plays ALL**
(maintainer, same evening: "network game must be with shuffle all music set"): its trampoline
`tramp_dc_net` `0x47F3B0` is now `call stub_dc_set; mov byte [0x5327F4], 2; jmp 0x405C20` (17 bytes,
one more HIGHLOW entry, 16 in the page-0x7F000 insert = still 32 bytes; 63 tail bytes left behind it),
so a relay game runs on the Classic tables with the shuffled soundtrack.

**Council Wars credits in every menu mode** (same evening, maintainer: "ultimate executable main menu
must always show CW credentials"): `main.c bintro`'s credits TTY reads `intrface/credits.txt` through
the mode prefix, so after DARK COLONY / ACADEMY / LOAD DC GAME the box scrolled the ROOT file =
Classic's text ("DARK COLONY ... PROGRAMMING"), and in the OZI mode the pack's ("DARK COLONY MISSION
PACK by ozi_ns"). Data only: `build_ozi_overlay.py` skips the pack's `intrface/credits.txt` and copies
`exp/intrface/credits.txt` ("DARK COLONY Expansion pack") into both `ozi_ns/intrface/` and
`dc/intrface/` (the root file stays Classic's for `dc16.exe`); `ozi_data` lists the `dc/` copy. The
`dc/` overlay now holds three files: `intrf_hd/bintroe`, `intrf_hd/lopte`, `intrface/credits.txt`.

**Game test** (scratch copy on `subst W:`, `avi\INTRO.AVI` renamed away, clicks by `SendInput` from a
DPI-unaware process at the game's own coordinates, screen captures by a DPI-aware child, the state
block read with `ReadProcessMemory`): start-up `dev 1, idx 0, vol 500, music\track02.mp3, src DC`, LCG
seeded; ACADEMY (416,501) → name → START TRAINING (680,506) → NEXT (780,606) → TO BATTLE (691,603) →
Game Option tab (1002,102) → Options (931,213): the dialog shows the MUSIC row with `DC`; `+` (567,439)
→ `CW`, `exp\music\track02.mp3` (idx 4); `+` → `ALL`, a shuffled order and its first track playing
(`exp\music\track04.mp3`); `−` (471,439) → `CW`, `−` → `DC`; the stock rows still step by one (GAME
SPEED 100 → 110 %, SOUND 5 → 6). Main menu: COUNCIL WARS → `src 1`, OZI MISSIONS → `src 2`, ACADEMY →
`src 0`, DARK COLONY → `src 0` (BACK between them; every return to the main menu re-runs `cd_open`,
which re-seeds and re-opens the first track of the then-current source, silently, as before).
`error.log` empty throughout. Not run: the 640x480 build, the end-of-track transitions, the volume
slider on the new module (code unchanged), a save/load round trip.

#### 10.42 A Linux/Wine player's report: `safefunc.c` line 290 on `sound\sound2.dat`, silent music, a `ddraw.dll` for 32:9 **(25 Sep 2026, player report forwarded by the maintainer, "Investigate this. Linux + wine = several errors"; investigation, then one data byte per exe as the fifth edit of `longpath`, §10.29)**

The report (Wine + gamescope): a box "0, file safefunc.c, line 290" when running at 32:9, which
went away after "throwing ddraw.dll into game folder"; no music; `error.log` (the player wrote
"Error.log") holding two lines:

```
FILE Error opening file sound\sound2.dat with error num 1
assert failure, file safefunc.c line 290 (0)
```

**1. The two lines = a required start-up file could not be opened, before the display existed.**
They come from safefunc.c's generic open helper (CW `0x4061BC`, body `0x4062C1..0x406366`; Classic
has the same code at its own offsets - safefunc.c is not +0x60, see §10.19): the open failed, the
file is *required* (`bl`), and the display object `0x488E20` is still NULL (or `0x488E1C` is set), so
instead of the CD-prompt slot (`+0x4C`, since §10.19 edit 9 the FILE NOT FOUND box) it runs
`fprintf(error.log, "FILE Error opening file %s with error num %d\n", name, errno)` at `0x406301`
and `assert(0)` at `0x406323` (line `0x122` = 290) - the assert box the player saw. `error num` is
the Watcom C runtime's `errno`, whose **`ENOENT` is 1** (Watcom numbers its errno list from `EZERO`
0; `fopen` -> `CreateFileA`, `ERROR_FILE_NOT_FOUND`/`ERROR_PATH_NOT_FOUND` -> 1). **Reproduced on
Windows 25 Sep 2026:** a scratch copy of the game folder (`subst W:`) with both `SOUND\SOUND2.DAT`
and `exp\sound\sound2.dat` renamed away writes exactly these two lines and sits behind the
full-screen surface with a `#32770` message box until killed. The file is opened by the sound
module's table loader `0x4309C8` (CW; `mov eax, 0x485BA8 "sound\sound2.dat"`, mode `"rt"`) through the
prefix helper `0x406474`: `exp/sound\sound2.dat` optional, then `sound\sound2.dat` required. The sound
module is initialised from display slot `+0xFC` (`0x42E814`) **inside the display constructor**
(`0x42C654` -> window -> the created-callback `0x42E6C8` -> `0x405264` game init -> ... -> sound
init), and `set_display` `0x405E00` stores the object only when the constructor returns - which is
why any missing start-up file gives the assert form, not the FILE NOT FOUND box.

**What that run had already loaded:** `error.log` itself (mode `"w"` - the file is truncated at every
start, so the quoted lines are the LAST run before the player looked), `exp/animozi.dat`, and the
~180 banks of that list as `animate/<x>.fin` + `sprites/<x>.spr` - only 16 FIN / 13 SPR of them
live in `exp/`, the rest come from the root `ANIMATE/` and `SPRITES/`, uppercase on disk, asked
for in lowercase with forward slashes. So the working directory WAS the game folder and the file
lookups WERE case-insensitive; the only failure is the `sound2.dat` pair, and `sound\sound2.dat`
(Classic DGROUP `0x485BA0` = file `0x833A0`, CW `0x485BA8` = file `0x835A8`) is **the only path string
in either exe written with a backslash separator** (DGROUP scanned for `x\y` patterns; every other
path uses `/`; the `%c:\dc\` CD path is zeroed by `nocd`). Under a stock Wine a backslash in a
relative path is an ordinary separator and both files are in every copy of the repository, so
the cause on the player's machine is **undetermined** from the log: either the two files were
absent in that run (partial copy / copy in progress) or the file layer mishandled the backslash.
The reported cure - a `ddraw.dll` - has no mechanism connecting it: DirectDraw init sits between
the data load and the sound init but touches no path. Decisive tests for the player, in the game
folder: `ls -la SOUND/SOUND2.DAT exp/sound/sound2.dat`, and a fresh failing run with
`WINEDEBUG=+file wine "Dark Colony Ultimate.exe" 2>&1 | grep -i sound2` (ntdll prints the Unix
path it resolved for the name and the status). Applied the same day as the fifth edit of `longpath` (maintainer: "apply the backslash fix to both exes
and update patcher"): the one `\` is now `/` (file `0x833A5` Classic / `0x835AD` CW, one data byte, harmless
on Windows; §10.29, published builds `89744c45…` / `2da5c86a…`). Side note: the repository tracks
`DC - Council wars/ERROR.LOG`; on Linux Wine writes the game's `error.log` into it by the
case-insensitive match, which is what the player found.

**2. Music under Wine.** The `music` fix (§10.31/§10.41) plays MP3 through MCI `mpegvideo` =
`mciqtz32.dll` = DirectShow. Wine's `dlls/mciqtz32/mciqtz.c` (read 25 Sep 2026) covers everything
the module sends: `MCI_OPEN` needs `MCI_OPEN_ELEMENT` (the module sends `0x2202` = ELEMENT | TYPE |
WAIT), `MCI_PLAY` is asynchronous (flags 0), `MCI_STATUS` with `MCI_STATUS_MODE` answers
`MCI_MODE_STOP` (`0x20D`, the module's next-track trigger) after `EC_COMPLETE` because the notify
thread stops the graph, `MCI_SETAUDIO` with `MCI_DGV_SETAUDIO_ITEM | MCI_DGV_SETAUDIO_VALUE` /
`MCI_DGV_SETAUDIO_VOLUME` takes 0..1000 (the module sends level x 100, level 0..10). The one step
that fails without extra software is `IGraphBuilder::RenderFile` -> `MCIERR_INTERNAL`: Wine's
quartz decodes through winegstreamer, so the prefix needs a GStreamer MP3 decoder
(`mpegaudioparse` + `mpg123audiodec` or `avdec_mp3`; 32-bit plugins for a 32-bit prefix unless the
Wine build uses the new WoW64 mode - Debian/Ubuntu `gstreamer1.0-plugins-good:i386`
`gstreamer1.0-libav:i386`, Arch `lib32-gst-plugins-good lib32-gst-libav`; Proton bundles its
own). A failed open at battle start sets the game's "no CD audio" flag (`0x489748`, §10.31) and
the module is never called again that session - silence for good, no retry. Diagnostic:
`WINEDEBUG=+mci,+mciqtz,+quartz` and look for `MCIQTZ_mciOpen` / "Cannot render file". Nothing to
change on our side; a DirectShow-free fallback would be a new decoder module (WAV would be
~600 MB).

**3. 32:9 under Wine.** The exe asks for an exclusive 3840x1080 / 5120x1440 DirectDraw mode; under
Wine that is wined3d + winex11/gamescope. Our own Windows rig needed dgVoodoo for 32:9 as well
(§10.32), so a wrapper `ddraw.dll` is the expected route; on Wine a native `ddraw.dll` beside the
exe is loaded only with `WINEDLLOVERRIDES="ddraw=n,b"`, and the Windows-only ghosting proxy of
§10.32 is unnecessary (Wine has no window ghosting). Which `ddraw.dll` the player used is not
known.

**Open:** the actual cause of the `sound2.dat` failure on that machine (needs the `ls` and the
`+file` trace); whether the MP3 module plays under Wine once a decoder is present (never run - no
Linux machine here).

#### 10.43 1920×1080 and 1920×1200 join the patcher's resolutions; several sizes per aspect ratio from now on **(27 Sep 2026, maintainer: "Add 1920x1080 resolution to patcher", then "add to documentation that we are adding multiple resolutions for aspect ratios. add 1920x1200 too"; `gen_apply_script.py` → `Apply-DarkColonyPatches.ps1`, two new picture folders `INTRF_HD\1920x1080\` and `INTRF_HD\1920x1200\`, two new `hd_sets/` fixtures; no tool logic changed; not run in the game)**

**The rule.** From 22 to 27 Sep 2026 the resolution list held exactly one size per aspect ratio (§10.33: 5120×1440 was dropped for it; 4:3 excepted for the stock 640×480). **Since 27 Sep 2026 the list may hold several sizes per aspect ratio**: the maintainer added 1920×1080 beside 1280×720 (16:9) and 1920×1200 beside 1280×800 (16:10), and asked for the change of rule to be written down. Sizes are still added only on the maintainer's request - each one costs three shipped pictures (~0.2 MB) and a game test. The list is now 640×480, 1024×768 (4:3), 1280×1024 (5:4), 1280×720, 1920×1080 (16:9), 1280×800, 1920×1200 (16:10), 3840×1080 (32:9). One consequence for the generator: the window preselects the *last* recommended mode of `HD_MODES` (`Get-PreferredMode` walks the list and keeps the last match), so within one ratio the larger size must be listed after the smaller - `['1024x768', '1280x1024', '1280x720', '1280x800', '1920x1080', '1920x1200', '3840x1080']` - and a 16:9 or 16:10 monitor now gets its 1920-wide size preselected while the 1280-wide one stays offered and marked recommended too.

**What was already there.** The exe side needed nothing new: `patch_resolution.Geometry` has planned both sizes since §10.24/§10.25 - the width is not a power of two (the three `y*640` idioms become `imul`), 1792 view columns are exactly 56 tiles, and the view rows are whole tiles plus spare rows that `Geometry.slack_y` takes off the view and `hud_layout.target()` gives to the HUD's bottom bar (the path 1280×720 uses for its 16 rows): **1920×1080 = 1792×1024 view, 56×32 tiles, 24 spare rows, bar 50 px**; **1920×1200 = 1792×1152, 56×36 tiles, 16 spare rows, bar 42 px**. Lightmap stride 256 for both (115 half-tile columns; frames `0x14CC → 0x441C` / `→ 0x4C1C`), occlusion masks 229 376 / 258 048 bytes, menu furniture shifted by (+640, +300) / (+640, +360), the movie the full frame at 1920×1080 and a 1920×1080 letterbox at rows 60..1140 at 1920×1200. 178 edits per exe, like 1280×720. The sentence of §10.23 that 1920×1080 "is refused until the HUD absorbs the slack" described the morning of 21 Sep 2026; the slack rule of that afternoon made such sizes acceptable, only nobody had asked for them.

**What was done per size.** The three non-derivable pictures were rendered with the §10.25 pipeline (`make_set.sh`: stock `INTRFACE GAMESTAT SPRITES ANIMATE exp/{intrface,gamestat,sprites,animate}` + root palette files copied, then `pad_background` → `paint_intro` → `logo_art` → `hud_layout build` → `hud_layout maine apply` → `split_hd_data`) and shipped as **`DC - Council wars/INTRF_HD/<WxH>/INTRG.GIF, INTRO.GIF, INTRFACE.GIF`** (1920×1080: 69 912 / 70 407 / 17 205 bytes; the HUD frame splices 1280 columns at x=300 and the spare rows into the bar's top edge left of the panel, 158 panel widgets x += 1280, 11 bottom-row widgets y += H−480). The rest of each Python set (52 `INTRF_HD` files without the loading BMPs, 5 `exp/intrf_hd` files with the seven-row menu from `build_ozi_overlay.menu_script`) is a fixture in `Dark-Colony-development/hd_sets/<WxH>/` (57 files each). The patcher was regenerated twice (about 1 m 45 s each; 8 modes, 1 418 082 bytes): every existing mode's `ReferenceSha256` is unchanged (Classic 1024×768 `89744c45…`, Ultimate `2da5c86a…`, map editor `c72dd205…`), the new references are **Classic 1920×1080 `9a24896b…`, Ultimate `2b92968c…`; Classic 1920×1200 `dac590be…`, Ultimate `2259f106…`**.

**Council Wars menu** (`menu_layout`, hung from the title as at every HD size, §10.36; `cw_menu_lift` = 15 at both heights, box the stock 100 rows): 1920×1080 logo `DCUK` at (805,375), title 534..567, box 578..677, button rows **687 / 725 / 751 / 789 / 815 / 853 / 879** at x 775 / 966 (H−72 = 1008 cap not reached); 1920×1200 logo (805,449), title 608..641, box 652..751, rows **761 / 799 / 825 / 863 / 889 / 927 / 953** (cap 1128). Classic's grid: rows 694..772 (logo 380, title 539) at 1920×1080 and 766..844 (logo 452, title 611) at 1920×1200, x 780 / 960. **Generator pitfall:** `gen_apply_script.py` asserts that every shipped picture of every `HD_MODES` entry exists in the game folder (`set_sources`) - render and copy the three pictures BEFORE regenerating, or it stops with `AssertionError: ('classic', 'INTRF_HD\<WxH>\INTRG.GIF')` (hit once for 1920×1200).

**Checks.** On a clean `git archive HEAD` copy of the game repository plus the new picture folders, `Apply-DarkColonyPatches.ps1 -All -Resolution <WxH> -Overwrite` under PowerShell 7 and under Windows PowerShell 5.1 for both sizes: both games "byte-identical to the reference build", the map editor unchanged (`c72dd205…`), the written interface set (52 + 5 + 1 + 5 files, `LOAD.BMP`/`LOAD2.BMP` at the size) compared with the fixture by the scratch `compare_sets.py` - 64 identical, 0 problems per size and shell. `patch_resolution.py plan` on the untouched `dc16.exe` gives the 178 edits for both. **In the game** (maintainer: "verify that these resolutions are applied correctly"): the first runs showed the frame scaled by 1.5 and cut off ("nothing is fine! all screen is screwed up", "resolution does not fit on the screen!") - not the patch but Windows' DPI scaling of a DPI-unaware process, fixed by the embedded manifest of **§10.44**; with it both games at both sizes ran from a `subst` drive through main menu, name entry, briefing, TO BATTLE and a battlefield click with `error.log` empty, the frame 1:1 (menu buttons where the scripts put them, HUD panel and the taller bottom bar at the screen edges, the display in the 1920×1080 mode for that size and back to 1920×1200 afterwards). The published 1024×768 exes changed by the manifest only (§10.44); nothing committed at the time of writing.

#### 10.44 The game becomes DPI-aware: an embedded manifest in the `icon` section **(27 Sep 2026, maintainer, after the 1920-wide test frames did not fit the screen: "make the game DPI-aware via manifest and test again", then "embed manifest the sme way as icon"; `tools/patch_icon.py`, fix `icon` for the two games; confirmed in game at 1920×1080 and 1920×1200)**

**Symptom.** The first 1920×1200 and 1920×1080 runs on the development PC (1920×1200 panel, Windows display scaling 150 %) showed the menu with the DC logo at about (1210, 760) instead of (805, 449) and the credits at the bottom edge, the battlefield as an enlarged centre cut-out without HUD: the game's frame drawn at 1.5× and cropped. The exe and the data were right (mode set, menu, campaign screens and battlefield loaded, `error.log` empty); the picture was scaled by Windows.

**Cause.** `dc16.exe` / `ENGEXP16.EXE` are DPI-unaware processes (no manifest; Watcom 1997). On Windows 8 and later a 16-bit exclusive DirectDraw mode is emulated: Windows attaches the `DWM8And16BitMitigation` compatibility layer to the exe (visible under `HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers` - every game exe ever started on this PC has it; the Program Compatibility Assistant adds it after the first launch, and **a copy without it fails its first launch** with `Setting to 16 bit mode Failure` + `assert failure, file ddex4.c line 586` and a black screen, which is what a `subst` copy on a fresh path showed until the layer was set by hand), the desktop window manager composes the 16-bit primary surface, and for a DPI-unaware process that composed surface is scaled like any unaware window: by 1.5 at 150 %. 1280×800 - the maintainer's own size - is exactly the scaled desktop of this panel, so it "fitted" by coincidence (1280×800 × 1.5 = 1920×1200), and 1024×768 switches the physical mode, where the scaling is not visible. Every player with display scaling above 100 % who picks a size larger than their scaled desktop sees the cut-off frame.

**Fix.** An application manifest declaring `<dpiAware>true</dpiAware>` makes the loader mark the process DPI-aware before the first instruction runs; the game never asks about DPI, so nothing else changes. **(Superseded 28 Sep 2026, §10.50: that form is SYSTEM-DPI-aware and shrank every mode whose scale step differs from the desktop's to two thirds; the manifest is per-monitor V2 since then.)** Tested first as an external `Dark Colony.exe.manifest` beside the exe (honoured because the exe has no embedded manifest; `touch` the exe after adding one, the loader caches by timestamp): the 1920×1200 menu filled the screen exactly. Then, at the maintainer's instruction, embedded like the icon: `patch_icon.py` adds a resource **`RT_MANIFEST` (type 24), id 1** (`CREATEPROCESS_MANIFEST_RESOURCE_ID`), 463 bytes of UTF-8 XML, to the resource directory it builds in the appended `.dcicon` section - the same four header edits, the appended blob grows by 512 bytes (Classic 742 912 → 743 424 bytes, Ultimate 743 424 → 743 936). `CreateActCtxW(lpSource = exe, lpResourceName = 1)` succeeds on the patched exe and fails with 1813 (resource not found) on the original, so the loader sees it. **The games only**: `--manifest auto` (the default) embeds it in an exe that carries an icon group (`DC16`); the map editor's Borland dialogs are laid out in dialog units and would shrink at 150 %, so it keeps no manifest and its build (`c72dd205…`) is unchanged; `--manifest yes|no` overrides. `verify` reports `DPI-aware manifest embedded` or `no manifest (25 Sep 2026 form)`. An embedded manifest also makes Windows ignore any external `<exe>.manifest`.

**Consequences.** The fix `icon` carries the manifest for the games in every mode (a mode-independent variant, one Base64 blob), so **every game build's reference hash changed**: 1024×768 Classic `866ad0f5…` (was `89744c45…`), Ultimate `7abb952a…` (was `2da5c86a…`); 640×480 `7713cab1…` / `ed4bc7d7…`; 1920×1080 `58522e46…` / `5e296d04…`; 1920×1200 `4b6b3731…` / `a2d7c6bd…`; the map editor `c72dd205…` unchanged. Rebuilt on the clean copies under PowerShell 7 and 5.1 - byte-identical to the references at 1024×768, 1920×1080 and 1920×1200. The published 1024×768 exes were rebuilt by the patcher and staged into the repository index from the scratch build (`git hash-object` / `update-index`, the maintainer's working-copy builds untouched); `dc16.asm` / `dcexp16.asm` regenerated from them.

**Game test with the embedded manifest** (real `SendInput` from a DPI-aware driver process - the game being DPI-aware now, the input lands 1:1; posted `WM_MOUSE*` messages, which drove the unaware game, are no longer acted on): Dark Colony and Dark Colony Ultimate at 1920×1200 and at 1920×1080, each from a `subst` drive with the intro movie renamed away: menu at 12 s, TRAINING / ACADEMY, leader name typed, START, NEXT, TO BATTLE, battlefield at 40 s, one ground click, `error.log` empty, the display in 1920×1080 during that size's runs and restored after the kill. Captures: menus and HUD frames at the screen edges, the Ultimate seven-row block at rows 687..879 / 761..953, Classic's grid at 694..772 / 766..844.

**Rig notes.** (1) A test copy on a new path has no `DWM8And16BitMitigation` layer and fails its first launch; set it by hand (`Set-ItemProperty` on the `Layers` key, value `~ DWM8And16BitMitigation`, keyed by the exe path, `subst` letter included) and remove it afterwards. (2) The maintainer's own game running from the game folder makes a second exclusive-mode client quit at once with exit 0 - check the process list first. (3) Renaming `AVI\DCINTRO.AVI` away to skip the intro makes the patcher skip the `movies` fix ("RESOURCES NOT FOUND"): such a Classic build is partial and has no reference hash - restore the file before comparing hashes. (4) The Windows application log recorded a 0xC0000005 of the maintainer's `Dark Colony Ultimate.exe` at 19:30:45 that day, fault offset `0x2F918` = `0x42F918` (`mov eax,[0x489748]; mov edx,[eax]; call [edx+64h]` - a `Lock` through a NULL surface pointer, the known quit-time display bug of §10.22), before any test exe ran; unrelated to this work, still open.

#### 10.45 Battlefield chat lines at sizes with spare rows: the two `MAINE` overlays follow the map view's bottom edge **(28 Sep 2026, maintainer: "comments are messed up in network war" → "broken is not a lobby chat but battlefield chat!"; `tools/hud_layout.py` `shift`, patcher `Edit-HudScript`; data only, no exe byte; confirmed in a relay battle at 1920×1200)**

**How the battlefield chat is drawn.** The in-game chat handler `0x41DA2C` (`0x0E from, to_mask, text`, protocol doc §4.3) keeps a queue of six 88-byte entries in the net object (`+0x310` count, `+0x314` index, `+0x318` entries: 80 bytes of text, `+0x50` sender, `+0x54` mask; `0x41D950` drops the oldest when full). The client's per-frame display `0x40B10D..0x40B221` shows at most two of them through the HUD script: for line i = 0, 1 it sets widget `0xCB + i` (ids **203** and **204** of `INTRFACE/MAINE`) to the sender's colour (`0x423FDC`), the text (`0x423E74`) and reveals it (`0x421AA4`), and drops entries again after a time (`0x40B430` / `gs+0x469C`, 0x9C4 or 0x1D4C ticks depending on the count). So the chat lines are two ordinary `in_text` widgets - `in_text 204 0 10 425 72 1 ... mask` and `in_text 203 0 10 440 72 1 ... mask` - drawn with a mask over the last rows of the map view (the view ends at 454 in the stock layout; 440 + 14 = 454).

**The bug.** `hud_layout.shift` classified every `MAINE` widget at `y >= MSG_Y` (420) as bottom-bar furniture and moved it by the whole screen growth `dy = H − 480`. That is right for the bar's own widgets (y >= 454) and right for the chat lines as long as the map view ends `dy` lower than in the stock layout - true at 1024×768, 1280×800 and 1280×1024, where the view height is a whole number of tiles. At **1280×720, 1920×1080 and 1920×1200** the view is `slack_y` rows shorter (16 / 24 / 16, §10.25) and the bar that much taller, so the view's bottom edge is only `dy − slack_y` lower and the two lines moved `dy` landed in the bar: at 1920×1200 `203` at 1160 and `204` at 1145 with the view ending at 1158 - the lower line inside the bar's top rows, the upper one on the view's last rows; at 1920×1080 1040 / 1025 against a view ending at 1030; at 1280×720 680 / 665 against 678. The patcher's PowerShell port `Edit-HudScript` had the same rule (`$y -ge 420 → $y + $dy`).

**Fix.** `shift(x, y, dx, dy, sy)` (Python) and `Edit-HudScript` (`$sy = ($H - 32) % 32`, the same value as `slack_rows(H)`) treat the band `420 <= y < 454` as view overlays: `y + dy − sy`. Bar furniture (`y >= 454`) and the right panel are unchanged, as are all sizes without spare rows (1024×768, 1280×800 and 1280×1024 produce byte-identical `MAINE`). Results: 1920×1200 `204` 1129 / `203` 1144 (view ends 1158); 1920×1080 1001 / 1016 (1030); 1280×720 649 / 664 (678) - the lower line 14 rows above the view's edge at every size, as in the stock layout. `cmd_maine` prints the separate amount (`the two chat lines above the bar by 704`). The three fixtures `hd_sets/1280x720|1920x1080|1920x1200/INTRF_HD/MAINE` were regenerated through the whole pipeline (`make_set.sh`; only `MAINE` differed from the previous fixtures), the patcher regenerated (exe references unchanged - data only), a 1920×1200 rebuild on the clean copy wrote the corrected lines, and the game folder's `INTRF_HD/MAINE` (the maintainer's 1920×1200 set) was given the same two rows (byte-identical to the patcher's output).

**Test.** Local relay (`SYNC_CHECK=shadow BOT_HIRE=true`, so the Mercenary speaks in battle), the 1920×1200 Ultimate build from a `subst` copy, driven with real input: MULTI PLAYER WAR → TCP/IP → CONNECT TO SERVER → 127.0.0.1 → CONNECT → hall → `/1` → READY (twice in the room, F36) → countdown → battle on Plink - O; captures every 2 s. The Mercenary's offer lines (`I ally with anyone who pays me 1000 ...`, `And I play the game: base, workers, army, war.`) appeared on the map view's last rows at x 10, fully above the bottom bar, and faded after their time; `error.log` empty; relay: `client loaded`, RUNNING, `said: 3`. Before the fix the same lines sat half in the bar (the maintainer's report). Not run: 1920×1080 and 1280×720 battles (same rule, positions checked in the script only).

**Also seen on the way.** (1) The READY double press (maintainer: "is that a bug?"): after READY in the hall (= join) the big READY checkbox `checkb 133` arrives in the room pressed; the first click releases it (`'h'(1)`), the second presses (`'h'(2)`) - F36, known since 7 Sep 2026; the relay cannot release it, a ~10-byte client patch in the `'h'` handler (`0x40F38B`: also call `0x4272A8(ui, 0x85, status == 2)` for the own player) would. (2) A second click on the same spot needs a fresh `WM_MOUSEMOVE` (the driver jiggles the pointer). (3) The room's READY press was sometimes not sent by the game for many seconds; the driver re-clicks until the relay log shows status 2.

**The black band above the input line, and why the view cannot simply take the spare rows (maintainer, same session: "there is a black line right over comment creation line!" → "why do you need a filler? why the battlefield can't occupy all the space?" → "try option 1").** At the sizes with spare rows the bottom bar is made taller by repeating its first two rows (`BAR_TOP_SEGMENT`, §10.25), and rows 452-456 of the stock bar art are pure black (the grey bevel starts at 457), so the bar begins with a 16 px (1920×1200, 1280×720) or 24 px (1920×1080) black band right above the chat input. **Experiment:** a 1920×1200 Ultimate build whose view keeps the partial row - `Geometry` with `view_h = 1168` (36.5 tiles), only three exe sites differ from the normal build (`proto.c map view rect height`, `viewport height`, `occlusion mask size`; the tile count stays 36) - and a HUD frame with a 1168-row hole and the stock 26-row bar. Training mission, camera scrolled up so lit terrain reaches the view's bottom: **terrain is drawn down to row 1157 (6 + 36×32) and rows 1158-1177 stay black**, the bar's bevel starts at 1178; `error.log` empty, no crash. The tile drawer `0x45011C` derives its row count from the view height `>> 5` (§10.5, "for the record"), i.e. it floors, and the camera moves in whole tiles, so no code ever draws a partial tile row: a taller view only moves the black band from the bar into the view. Making the battlefield fill the space needs a drawer change - a 37th tile row drawn clipped to the view's bottom (the 32×32 blitters are table-dispatched by zoom and tile flags and write whole tiles; a stub would draw that row into a scratch area of `view_w × 32 × 2` bytes and copy its first 16 rows back, and the occlusion mask, sized by tiles, would need the extra row too) - several hours of exe work with the usual risk. The alternative that costs nothing in the exe is a filler that looks like bar art (the bevel rows 457-461 / 473-479 instead of the black rows), or moving the spare rows to the top border. The experiment's hooks were removed from the tools again; nothing of it ships.

**The bevel plate (maintainer, next: "fill the bar rows with bevel texture and test").** `hud_layout.cmd_build` now splices the spare rows in as a bevelled plate instead of repeating the bar's two black top rows (`_bevel_plate`): one highlight row (stock bar row 458, palette value 255), a body of the two brushed rows 459/460 (98/115) alternating, one shadow row (461, 47), each row one colour across the whole bar width (sampled at `BAR_SAMPLE_X` = 200 inside the message box's frame, so the plate is plain above the arrow buttons as well), placed at the bar's top edge; below it the stock bar follows unchanged (its three black rows, the box frame, the buttons), and the panel's column is painted by the right-panel region as before. At 1920×1200 the plate is rows 1158-1173 (highlight 1158, shadow 1173), the stock black rows 1174-1176, the box frame from 1177 - the map view now meets a grey frame instead of a black gap. Rebuilt: the three frames `hd_sets/1280x720|1920x1080|1920x1200/INTRF_HD/INTRFACE.GIF` = the shipped `INTRF_HD\<WxH>\INTRFACE.GIF` of those sizes (the patcher copies the shipped picture, nothing to regenerate there) and the game folder's active 1920×1200 `INTRF_HD/INTRFACE.GIF`. Confirmed in a 1920×1200 training battle from the `subst` copy: terrain to row 1157, the grey plate under it, the message line and the arrow buttons in place, `error.log` empty. The 1280×720 and 1920×1080 frames were built by the same code and checked in the picture only. **Other sizes (maintainer: "check other resolutions if they need the same fix"):** spare rows exist only where `H − 32` is not a multiple of 32 - 1280×720 (16), 1920×1080 (24), 1920×1200 (16) and **3840×1080 (24)**; 640×480, 1024×768, 1280×800, 1280×1024 and 5120×1440 have none and are untouched by both the chat-line rule and the plate. The shipped `INTRF_HD\3840x1080\INTRFACE.GIF` was rebuilt as well (it differs from the previous file in rows 1030-1053 left of the panel only); its `MAINE` is written by the patcher, whose `Edit-HudScript` puts the chat lines at 1001 / 1016 against a view ending at 1030. No 32:9 panel here, so that frame is checked in the picture only.

#### 10.46 Why every screen loads slower at a higher resolution: the palette conversion makes 512 full-surface round trips **(28 Sep 2026, maintainer: "investigate, why higher resolutions leads to longer load of every menu?" → "implement the fix for both exes and update the patcher"; measured in game at 1024×768 and 1920×1200; fix `palette` = `tools/patch_palette.py`, both exes, confirmed in game - see the end of the section)**

**Symptom.** Every screen change of the patched game (main menu → ACADEMY → name → START TRAINING → briefing → NEXT, the hall, the battle load) takes visibly longer the larger the resolution: about 1.5 s at 1024×768, about 3.5 s at 1920×1200 (the 3-4.5 s black screen before the hall of F71 / §16 entry of 28 Sep is the same thing). The script parse, the background GIF decode and the file loads are not it: together they are under 0.2 s per screen at both sizes.

**Cause: `set_palette` (`ddex4.c`, Classic `0x42F320`, Ultimate `0x42F380`; the function §10.16 calls `remap`).** `load_interface` ends in `window_draw` (`0x422D84` / `0x422DE4`), which loads the script's `palette` through `ctx+0x34` = `load_palette` (`0x42BAF8` / `0x42BB58`: reads the 768 palette bytes into `screen->palette+0x301`, then screen slot `+0x118`). `set_palette` converts the 256 entries to 16-bit pixels the portable 1997 way - **per entry** `GetDC(back buffer 0x48971C)` → `SetPixel(hdc, 0, 0, RGB(r,g,b))` → `ReleaseDC` → `Lock(NULL rect = the whole surface, DDLOCK_WAIT)` → read the 16-bit word at `lpSurface` → `Unlock`, stored at `screen->palette+0x602+2i` - and only then builds the three component tables at `0x4DEC90` (256 × {R,G,B} words: component value → its 5/6-bit field, from the masks `0x4DFF18/28/24` and the format flag `screen+0x14`, `0x235` = RGB565, `0x22B` = RGB555, both set by `create_surfaces` `0x42EB60`ff) that `make_colour` `0x42F2D0` (slot `+0x150`) uses. The same function is also the palette change at battle start (terrain palette) and runs twice during start-up. Every menu script names a palette (`palette palette`), so it runs for every screen, unchanged palette or not.

**Where the time goes (main-thread stack sampler, ~800 Hz, Ultimate build, three screen changes per size):**

| | 1024×768 (0.79 Mpx) | 1920×1200 (2.30 Mpx) |
|---|---|---|
| `set_palette` per screen change | 1.41 / 1.49 / 1.53 s | 3.54 / 3.23 / 3.19 s |
| `ReleaseDC`, per entry | 2.7-2.9 ms | 6.1-7.0 ms |
| `Unlock`, per entry | 2.7-2.9 ms | 6.1 ms |
| `GetDC`, per entry | 0.03-0.11 ms | 0.17-0.23 ms |
| `SetPixel`, `Lock`, the loop's own code | ≈ 0 | ≈ 0 |
| start-up (two runs) | 1.5 s | 3.2 s |

The thread sits in `win32u.dll` (kernel transitions), `ntdll.dll` and `igd9trinity32.dll` (Intel's D3D9 user-mode driver). On Windows 11 DirectDraw is emulated over D3D9 (the `DWM8And16BitMitigation` layer, §10.44): a surface DC is a system-memory copy of the surface, so **`ReleaseDC` writes the whole surface back**, and **`Unlock` of a NULL-rect lock uploads the whole surface** as well - 512 transfers of W×H×2 bytes (4.6 MB at 1920×1200) per palette, about 2.7 ns per pixel per transfer, i.e. the time is proportional to the pixel count plus a small constant. The stock 640×480 game pays the same 512 round trips (about 0.6 s extrapolated, not measured); on a 1997 driver with a real 16-bit mode these calls cost nothing measurable, which is why the code was acceptable then.

**The read-back computes nothing.** Read out of the running game at 1920×1200 (RGB565): all 256 `LUT[i]` equal `((r>>3)<<11) | ((g>>2)<<5) | (b>>3)` - plain truncation, which is what GDI's `SetPixel` does on a 16-bit surface - and all 256 equal `R[r] | G[g] | B[b]` from the function's own `0x4DEC90` tables (a rounding variant differs in 212). So `make_colour(r, g, b)` gives the same value without touching the surface.

**Fix outline (not done; a new exe fix, both games):** rewrite the loop body Classic `0x42F4C7..0x42F538` (Ultimate +0x60; 113 bytes: the five calls and the pixel read) as arithmetic - either build the component tables first (the existing tail code, run once as its own 256-loop) and fill the LUT with `R[r]|G[g]|B[b]`, or shift by the format flag directly (about 40 bytes). Nothing moves, no new import; the four HIGHLOW `.reloc` entries of the overwritten operands (`0x42F4CC`, `0x42F501`, `0x42F509`, `0x42F525`) become type 0; the three `ddraw` skip jumps of §10.16 inside `set_palette` become dead code but stay harmless (the loading-screen `Flip` skip is still needed); patcher order after `ddraw`. Expected gain: every screen change about 1.4 s faster at 1024×768 and 3.3 s at 1920×1200, start-up twice that, the battle load the same, the black screen before the hall (F71) under a second. Verification: the same `ReadProcessMemory` comparison of the 256 LUT words against the truncation formula after the patch, then the menu walk-through (no click-path code is touched, §10.33's rule does not apply).

**Method and rig (28 Sep 2026).** `subst V:` on the game folder itself (no copy; menu-only runs write nothing but `error.log`) with a `DWM8And16BitMitigation` layer entry for `V:\Dark Colony Ultimate.exe`, removed afterwards; a pre-existing `X:` mapping from another session was left alone. The 1024×768 comparison ran from a scratch copy of the folder without `AVI\` (224 MB, `robocopy /XD AVI`), built by the patcher on the command line (`-Original ENGEXP16.EXE -All -Resolution 1024x768 -Output ...`, byte-identical to the published `7abb952a…`, interface set written into the copy). Sampler: `SuspendThread` / `Wow64GetThreadContext` / `ReadProcessMemory` of 16 KB of stack / `ResumeThread` at ~800 Hz from a DPI-aware Python process that also drives the game with `SendInput` (ACADEMY (864,773) at 1920×1200, (416,501) at 1024×768; then `TEST`, START TRAINING, NEXT at the stock coordinates + (dx,dy)). Attribution: the five calls have fixed return-address slots below `set_palette`'s frame (`X-12` GetDC/ReleaseDC/Unlock, `X-20` SetPixel, `X-24` Lock, `X+0x49C` = the function's own return into `load_palette`), the live call is the highest valid slot, and the live frame of a window is the `X` with the most validated samples - `set_palette` runs at a different stack depth for each screen (five distinct frames per run), so a fixed `X` fails. Two false starts worth remembering: (1) the first pass classified by the innermost stale AUTO dword on the stack and with **Classic** addresses on the **Ultimate** exe (all of `ddex4.c` is +0x60 there) - it still pointed at the right function, by luck; (2) `SP_RET`-style slot checks need the frame's own depth, not a global constant. Scratch scripts `menuprobe2.py`, `analyze2.py`, `lutcheck.py` in the 28 Sep session scratchpad.

**Implemented the same day (maintainer: "implement the fix for both exes and update the patcher") - fix `palette`, `tools/patch_palette.py` (verify / plan / apply, `.palette.bak`, pattern-located, both exes, new patcher step right after `ddraw`; confirmed in game at 1920×1200 in both games).** Two in-place edits per exe, 5 `.reloc` entries, no absolute operand in the new bytes (so the bytes are identical in the two builds although their DGROUP globals differ by 0x28):

| Site (Classic; Ultimate +0x60) | Stock | New |
|---|---|---|
| `0x42F4C7..0x42F54D` (135 bytes) | `push &hdc` / `GetDC` / `SetPixel` / `ReleaseDC` / `Lock` / read `lpSurface` / store `LUT[i]` | `ebx = b>>3; eax = r>>3; ecx = g;` `if screen+0x14 == 0x235 { eax<<=11; ecx = (ecx>>2)<<5 } else { eax<<=10; ecx = (ecx>>3)<<5 };` `ax = eax\|ecx\|ebx; [screen->palette + 0x602 + 2·esi] = ax;` short `jmp` to the table build `0x42F54E`; NOP padding (58 code bytes) |
| `0x42F37C..0x42F393` (24 bytes) | `push 0` / `Unlock(back buffer)` / `test` / `je next` | `jmp 0x42F486` (next index) + 19 NOP |
| `.reloc` `0x42F37F`, `0x42F4CC`, `0x42F501`, `0x42F509`, `0x42F525` | HIGHLOW (the `mov eax,[back buffer]` and `call cs:[SetPixel]` operands) | type 0 ABSOLUTE padding |

Register use is the original's: on entry `eax` = b (masked), `[ebp+6Ah]` = r, `[ebp+6Eh]` = g, `edi` = screen, `esi` = index; the table build at `0x42F54E` recomputes everything from `esi`/`edi`, so clobbering `eax`/`ebx`/`ecx` is safe. The `ddraw` skip jumps (§10.16) at `0x42F394` / `0x42F3F5` / `0x42F43E` stay in place as dead code; the tool accepts the stock and the `ddraw`-patched exe alike, identifies a patched exe by the new body (build-independent bytes) and upgrades nothing else. Generator: `TOOL_OF`/`PLAN_OF` `palette`, `blocks_palette` (2 sites + 5 reloc lines = 7 edits), `PATCHES` entry after `ddraw`, both game builds' step lists; `Apply-DarkColonyPatches.ps1` regenerated (1 452 670 bytes). **Reference hashes (1024×768, every fix incl. the same day's `menuorder`):** Classic **`2357ca77…`** (was `86a06e69…`), Ultimate **`fa03709d…`** (was `95da6d53…`); applying `patch_palette.py` to the previously committed exes gives exactly these bytes, and `-All -Resolution 1024x768` on a scratch copy reproduces them under PowerShell 7 and 5.1 (pitfall met again: a copy without `AVI\DC*.AVI` skips `movies` and the Classic hash differs - copy the three files in first). The game folder's 1920×1200 builds were given the fix with the tool (`3367fb53…` / `65ce242f…`, uncommitted by design); `dc16.asm` / `dcexp16.asm` regenerated from the new 1024×768 builds.

**Verification in game (1920×1200, both games, `subst W:` on a scratch copy without `AVI\`).** (1) The 256 LUT words read out of the running patched game equal the truncation formula and the `make_colour` tables for all 256 entries - the same check that passed on the unpatched game, so the palette is bit-identical. (2) Timed menu walk with a screen watcher (MD5 of a top stripe every 20 ms): Ultimate ACADEMY → name screen 0.48 s after the click (unpatched build of the same day: 1.70 s), briefing → NEXT screen 0.41-0.62 s (unpatched: 5.06-5.22 s); Classic 0.21 s and 0.14-0.28 s. The sampler saw one sample inside `set_palette` in a whole run (the loop is now microseconds). `error.log` empty in all four runs. Not run: 640×480 and the battle load (same function, nothing size-specific in the edit), the 555 branch (no RGB555 display here).

#### 10.47 The main menu opens in a new order: DC logo, DARK COLONY title, the button wave, the credits **(28 Sep 2026, maintainer: "main menu items must initiate in different order. first must be 'DC' logo, second 'DARK COLONY' logo, then buttons, then credentials. suggest use best practices for initiating game main screen"; fix `menuorder` = `tools/patch_menu_order.py`, both games, `Requires nocd`; confirmed in game at 1920x1200 in both builds)**

**What the stock code does (main.c `bintro`, Classic `0x00404E80..0x00405060`, Council Wars the same
addresses - main.c is identical up to the CD-check branch, only the callees sit at +0x60).** After
`load_interface` the menu init runs, in this order: create the credits TTY (`0x00428448`, before the
screen is even loaded), `0x00424DB4(ip, 15, 1)` = hide the title gadget (`gadget+0xB2`), the display's
`+0x70` and `+0x7C(0x86, 1)` (menu sound), **`run_banims` `0x00427AE4`** - the blocking button wave of
§10.40 - then `0x00424770(ip)` = draw every button (types 2/3) + display `+0x54`, then
`start_anim(ip, 14, 1)` = the DC logo one-shot (`DCUK`, 12 frames), then the CD-less greying block
(dead since `nocd`), then the menu loop `0x00404F87`: `0x0042411C(ip, &ev)` polls; a press dispatches;
otherwise, **when the logo shows frame 10** (`0x00425034 == 0xA`), `0x00424F80(ip, 15, 0)` +
`start_anim(ip, 15, 1)` start the title (`DCUT`, 4 frames); and every pass runs the TTY update
`0x00427B44(display, 0, 0)`, which paints and scrolls the credits box. So the player saw buttons,
logo, title, credits (measured 25 Sep 2026 in §10.40: "logo, title and credits drawn after the wave").

**The animation object** (gadget `+0xA0`; `animate.c`): `+0` the gadget it belongs to, `+4` frame,
`+5` delay countdown, `+6` mode - **0 = `anim_loop`, 1 = `anim_oneoff`, 2 = `anim_stopped`** (the
keyword table `0x00484914/20/2C` is parsed by the `gadget` creator into `gadget+0xA8` at
`0x00424AA3..0x00424B04` and applied once at creation by `0x0042626C(anim, gadget, mode)` from
`0x00424C59`). A one-shot that runs out of frames sets mode 2 and frame 0 (`0x004262C6`), so **2 is
both "stopped" and "finished"**; the getter `0x0042670C` (via `0x004250CC(ip, id)`) returns the mode,
`0x004266FC` (via `0x00425034`) the frame. `start_anim` `0x00425164(ip, id, mode)` → `0x0042626C`:
no-op when the anim already belongs to that gadget in that mode, otherwise frame 0, delay 0, mode
set. The first build of this fix "parked" the first plate with mode 0 and the plate LOOPED under
the logo; the getters' 0/1/2 had been read as stopped/running/finished in §10.40 - corrected there.

**Why the data-only route fails.** Listing the logo and title as the first two `banim` plates would
chain them, but the wave hides every finished plate (`0x0042436C(ip, id, 0)` clears the visible
byte and erases it) - the logo would vanish - and the chain steps on **frame 2** of the previous
plate, so the buttons would start two frames into the logo anyway. The reorder has to be code.

**The fix - three blocks rewritten in place inside `bintro`, 194 bytes, no `.reloc` entry in any
of them (checked by the tool against the whole table), same layout in both exes:**

| block | VA (both exes) | bytes | was | now |
|---|---|---|---|---|
| A | `0x00404EF6` | 41 | run wave, draw buttons, start logo, dead CD flag read | `esi = ip`; read the `banim` widget (**id 18**, type `0x0C` checked; the id every menu script has used since 1997) → its record `{n, m, plates*, buttons*}` → first plate into `edi` (or -1); **park it: `start_anim(ip, plate, 2)`** = frame 0, stopped, before the first interface pump; `jmp` over `nocd`'s `EB 66` at `0x00404F1F` |
| B | `0x00404F21` | 102 | the six `set_greyed` calls of the CD-less path (dead since `nocd`) | `start_anim(ip, 14, 1)`; loop `pump 0x00424294(ip, &ev)` until `mode(14) == 2`; `0x00424F80(ip, 15, 0)`; `start_anim(ip, 15, 1)`; loop pump until `frame(15) >= 2`; `jmp` C+2 |
| C | `0x00404FB0` | 51 | the loop's "logo frame 10 → start title" check | `jmp 0x00404FE3` (the loop's else path = TTY update, so the loop is unchanged in effect); tail entered from B: `start_anim(ip, first plate, 1)` unless -1, `run_banims`, `0x00424770`, `jmp 0x00404F87` |

Why the first plate must be parked and restarted: the script gives it `anim_oneoff`, which starts it
at creation; pumped during the logo it would fly in alone and be **finished** by the time the wave
runs, and `run_banims` chains plate k+1 on `frame(k) == 2` - a finished plate sits at frame 0, so the
wave would never advance (a blocking loop = the menu hangs). Parking it (mode 2 = exactly the state
an `anim_stopped` plate is created in) and starting it just before `run_banims` keeps **the shipped
scripts unchanged**, and an older exe with the same data behaves as before. Immediates
`push imm8; pop reg` save the bytes that make B fit; `esi`/`edi` are callee-saved and the function
saves them itself (`0x00404DC8`). The callees are located by their bodies (`run_banims`,
`0x00424770`, `0x00424F80` by its assert line 255, the pump / getters / `start_anim` through the
`banim` runtime), so `verify`/`plan` work on stock, `nocd`-only and patched exes; `plan` on the
untouched original describes the same bytes as after `nocd` (block A stops before the CD branch),
`apply` refuses without `nocd`. Fix id `menuorder`, patcher order `..., music, menuorder, movies,
sounds | ozi, icon`; the generator parses the tool's three plan lines (`blocks_menuorder`).

**Measured (28 Sep 2026, 1920x1200 builds of both games from a `subst X:` copy with the intro AVIs
renamed away, 17-19 Hz screen-DC capture from a DPI-aware process, per-region MD5 change runs, the
cursor parked at (5,5) - the game's cursor sprite otherwise shows as a permanently "changing"
region; scratch `menuseq.py`).** Ultimate, seconds after the menu background is drawn: logo
animates **+0.44 .. +0.97**, title **+1.02 .. +1.14**, the first plate starts **+1.09** (title frame 2),
the ten plates settle +1.84 .. +2.49 in the §10.40 order, the credits box paints from **+2.49** and
scrolls on; the parked plate shows exactly one redraw at +0.44 (its frame 0 with the first pump).
Classic: logo +0.37 .. +0.87, title +0.98 .. +1.04, wave +1.04 .. +2.27, credits from +2.34.
`error.log` empty in both; the 0.44 s between background and logo is the stock gap before the first
pump (the `+0x7C(0x86, 1)` sound and the pump's timer init), unchanged. Not run: 640x480 and the
other HD sizes (the code has no size dependence), and no click during the wave.

**The staging, and why it is this way ("best practices").** Title screens are normally staged as a
cascade: brand mark → title → the interactive elements → secondary text, each step starting as the
previous settles, the whole under ~2.5 s, and never a pause with nothing moving. That is what the
blocks implement: the logo runs alone (0.5 s), the title starts when it has finished (sequential, as
asked), the wave starts at the title's second frame (a 0.1 s overlap so nothing stands still), the
labels appear as their plates settle, the credits come last because nobody reads them before the
buttons exist. Two knobs are single bytes in block B: `TITLE_FRAME_WAVE` (2) - raise it to 3 for a
strictly sequential title, lower to 0 to overlap fully - and the logo condition could be `frame >=
10` (the stock overlap) instead of `mode == 2` at the cost of 15 bytes B has not got. Not done, and
worth a fix of its own if wanted: **a click or key during the cascade jumps to the finished menu**
(the pump swallows input during the waits exactly as the stock wave did; the whole cascade is 2.5 s
after a 3-4 s screen load at 1920x1200, so the first thing to shorten is §10.46's palette loop).

#### 10.48 Battlefield chat: six lines, and every line announced with the mission-message sound **(28 Sep 2026, maintainer: "comments on the multiplayer battlefield must play the same sound as is playing for comments in single mode missions. on the battlefield we must have six lines of sent comments"; fix `chat` = `tools/patch_chat.py`, both games, `Requires palette`; data: `hud_layout.py maine` / patcher `Add-ChatLines`; confirmed in game at 1920×1200 by the rig and by the maintainer - "audio of chat message is ok. all 6 lines of chat are visible ok")**

**The two paths, as shipped.** A network chat line (`0x0E from, to_mask, text`, protocol doc §4.3) is queued by the in-game handler `0x0041DA2C` (Council Wars +0x60) in the net object's six-entry ring (`+0x310` count, `+0x314` newest index, `+0x318` 88-byte entries; `0x0041D950` drops the oldest when the ring is full, then the handler scans the text for the three cheat codes). The client's per-frame display `0x0040B10D..0x0040B221` (called from the client draw with `eax = gs`, `[ebp-8] = client`) keeps one drop timer in `gs+0x469C` (read through `0x0040B430`): the oldest line is dropped after 0x1D4C = 7.5 s, or after 0x9C4 = 2.5 s while more than two lines are queued, then `+0x314 = min(count-1, 1)` and the loop shows entry `index - i` in widget `0xCB + i`, i = 0, 1 - `in_text 203` (lower, y 440) and `204` (y 425) of `MAINE`, colour `0x00423FDC` from the sender's player record (`gs + 0xC9C + 0xE34*sender`), text `0x00423E74`, reveal `0x00421AA4`. Nothing in that path plays a sound. **A single-player mission message** is a different module: the trigger primitive `msg b0 b1 b2 b3 b4` (`.TRO`, e.g. `msg 2 0 3 3 8`) is parsed into a 28-byte action record at `0x0051B6AC` (opcode 0x0B, five byte operands at +4..+8), executed by the action interpreter `0x0043D904` (jump table `0x0043D8A8`, case 11 at `0x0043D967`: `b1` must be 0 - scenario.c assert 0x2EB - then `0x0044D88C(msgobj = gs+0x46FBC, b0, b2, b3, b4)`), which appends to the message object: 30 text pointers at +0 (`b2` indexes them), a 16-entry ring of `+0x78` text, `+0xB8` flags (bit 0 = flashing), `+0xF8` time, `+0x138` duration in seconds (`b4`; 0xFF = shown), `+0x178` colour (`b0`), `+0x1B8` intensity 0x1F, `+0x1F8` count, `+0x1FC` current. The drawer `0x00433C44` runs from the client draw at `0x0040AB90` only in the campaign modes (`gs+0x14F0` 0 or 3, never in a network game): it writes the current message into the bottom bar's `in_text 148` (0x94) and, when a new message becomes current (`0x00433D4E`) or within its first second (`0x00433DCB`), plays **sound table entry 187 = `SOUND\MSG.WAV`** (0.26 s) through the display object's play slot `[display+0x7C](eax = 187, edx = 1)`, `display = client+8`; that slot is filled by the display constructor (`0x0042C465`) from the sound module's slot +0x138 = `0x0042F968` → `0x00430F04` unless the no-sound flag `0x00489728` is set. PgUp/PgDn (`0x95`/`0x93` in the interface key handler `0x00433887`ff) step the ring.

**Fix `chat`, three edits per exe, identical bytes in both builds** (register-relative operands and rel32 calls whose targets move with the code; no `.reloc` change): (1) the display block `0x0040B10D..0x0040B221` (276 bytes) is rewritten in place (243 bytes + 33 NOP; assembled with keystone, `chat_asm.py` in the 28 Sep scratchpad): it first calls a helper that plays sound 187 exactly as the mission drawer does when the handler has left the marker `0x7FFFFFFF` in `+0x314` (a line arrived since the last frame; the field is recomputed every frame, so nothing else reads it), then runs the stock timer with the slow tier only (`cmp eax,1D4Ch`: the fast tier fired while more lines were queued than shown, which cannot happen with six shown), caps the newest index at **5**, pushes `client+0x7F4` (the interface object) and shows up to **six** lines - line i in widget **203 + i for i < 2 and 205 + i for i ≥ 2 (207..210; `count 205`/`206` are the HUD's counters)** - each only if `ip->objects[id].type == 4` (`in_text`; record stride 0x34 from `ip+0x88`, type byte at +1 - `0x00423E74` asserts on any other type), so a script with the stock two lines shows two and never asserts. (2) The handler's `inc dword ptr [esi+310h]` (6 bytes at `0x0041DB0F`) becomes `call stub; nop`. (3) `stub` (17 bytes: the `inc`, `mov dword ptr [esi+314h],7FFFFFFFh`, `ret`) and `helper` (34 bytes: marker test, `display->play_sound(0xBB, 1)` with eax saved) sit at **`0x0042F503..0x0042F535`** (CW `0x0042F563`), the first 51 of the 75 NOP bytes fix `palette` (§10.46) leaves in `set_palette` - live code in the untouched exe, hence `Requires palette`; `plan` on an untouched exe reports the room's old bytes as those NOPs (the patcher applies `palette` first), `apply` refuses without them; 24 bytes of the room are left. Lifetime: the oldest of up to six lines leaves 7.5 s after the previous drop (a burst of six is fully gone after 45 s); own lines and the relay's bot lines beep too (every `0x0E` whose mask includes the local player).

**Data.** `hud_layout.chat_lines` (run by `maine apply`, after the shift) and the patcher's `Add-ChatLines` (inside `Edit-HudScript`) insert `in_text 207..210` right after the shifted `in_text 203`, copies of that line with the id and the y replaced, 15 rows further up per line; idempotent (nothing added when a 207 line exists). Rows: stock 440/425/410/395/380/365 (203, 204, 207..210); 1024×768 728..653; 1920×1200 1144..1069 (the view ends at 1158); the two stock lines stay, so an exe without the fix and this script still shows two. The six fixtures `hd_sets/<WxH>/INTRF_HD/MAINE` and the game folder's 1920×1200 `MAINE` were given the four lines (nothing else changed); the stock 640×480 `INTRFACE/MAINE` is untouched, so the 640×480 build shows two lines with the sound (a patcher-written `intrface/mai?e` copy would give six - not done). Patcher regenerated (1 469 191 bytes; `blocks_chat` = 276 + 6 + 51 bytes, step after `menuorder`): on a clean `git archive HEAD` copy `-All -Resolution 1024x768` under pwsh 7 and `-Resolution 1920x1200` under PowerShell 5.1 reproduced the tool-chain exes byte for byte and wrote `MAINE` identical to the fixtures and to the game folder. **Published 1024×768 builds: Classic `bd86e63e…`, Ultimate `5acee874…`** (staged via `hash-object`/`update-index`); the game folder's 1920×1200 builds `9d2c60a5…` / `2dec194f…`; `dc16.asm` / `dcexp16.asm` regenerated.

**Game test (28 Sep 2026, Ultimate 1920×1200 from `subst W:` on the game folder, local relay `BOT_HIRE=true SYNC_CHECK=shadow LOG_LEVEL=debug`).** Rig `battle_chat.py` (menu → MULTI PLAYER WAR → TCP/IP → CONNECT TO SERVER → 127.0.0.1 → `/1` → READY twice; DPI-aware input and captures; `SetCursorPos(5,5)`) plus `chatter.mjs`, a `FakeClient` in room 1 with `readyPolicy: 'follow'` that sends `Tester: line N of 8` every 2.5 s from 25 s into the battle. Captures every 2.6 s of the view's last rows: lines 1-2, then 2-4, 2-5, 3-7, **3-8 = six lines at once**, oldest on top, then 4-8, 5-8, 6-8 … one line leaving every 7.5 s, all above the bevel plate; `error.log` empty. Sound: the battle music masks a process-wide meter, so the same run from a copy without `MUSIC\` and `exp\music\` (`subst Q:`) with a pycaw `IAudioMeterInformation` thread at 25 Hz: eight bursts of 0.33 s at peak 0.64 starting 0.2-0.7 s after each of the eight sends (81.4, 84.4, 86.4, 89.4, 91.4, 94.5, 96.4, 99.5 s), none before the first line. Rig lessons: the relay logs the hall's `/N` join as `READY status 2` too - match `"state":"LOBBY"` for the in-room READY; the fake client's `mready` reports game player 7, so shadow mode logs `shuffle mismatch … engine disabled, bots idle` (rig artefact, the bots then stand idle); music-free copy for any sound measurement.

### 10.65 REPLAY ONLINE GAME: the relay's recorded battles in the Ultimate main menu, watched from a participant's seat (2 Oct 2026)

Maintainer, 2 Oct 2026: "it's time to create a replay functionality for server and Ultimate executable.
Ultimate must contain 'REPLAY ONLINE GAME' in main menu right under 'ONLINE WAR'. it must bring up the
similar form as 'ONLINE WAR' does but with correct headers and modified. it must be possible to select
from client list of participants (8 radio buttons with client names on the right pane of form)." The
relay side is `RELAY_SERVER_PLAN.md` §21 (recordings on a Fly volume, the newest 50 kept, a private
viewer room per watcher) and `DC16_NETWORK_PROTOCOL.md` §4.5 / §6.10 (messages `0x55..0x59`).

**Menu.** Button id **9** in the right column's second row, the one reserved since 29 Sep: `build_ozi_overlay.py`
`OZI_COLUMNS = ((1, 6, 7, 0, 2, 16, 4), (8, 9, 3, 5, None, None, 12))`, `textmsg 12 REPLAY ONLINE GAME`
(18 characters of the 8-px menu font = 144 px on the 179-px plate). Widget ids are one object space, so
the button id 9 displaced MULTI PLAYER WAR's plate `gadget 9` → **25** (`OZI_RENUM`), the new plate is
**26**, `banim` has 12 pairs, and the patcher's `Edit-OziMenu` is the byte-identical port. The exe's
menu id filter `cmp edx,8` → **`cmp edx,9`** (`patch_online.py` `ID_FILTER_NEW`), and `online_dispatch`
in the `.dccode` section tests `edi` for 8 (`online_war`) and 9 (`replay_game`), everything else to the
loop head as before - still two edits in `AUTO`.

**Screen `REPLAYE`** (`<folder>/replay` + the language letter; 640x480: `INTRFACE/REPLAYE`), derived by
`patch_online.replay_script()` from the ONLINE script and by the patcher's `Edit-ReplayScript`: the list
narrowed from 56 to **40 columns** (320 px), the scroll bar, UP and DOWN 72 px LEFT of their stock LOADGE
places, the header line 40 columns, and right of the scroll bar the **participant pane**: `checkb 32..39`
(27x17, the lobby's READY boxes: `pictures hd_src/knobr` = **`HD_SRC\KNOBR.SPR`**, KNOBE's 149 cells plus
cell **149** = cell 9 with the "?" glyph blacked out and cell **150** = the same in the screen palette's
greys (82/65/41/35/23/11) - slot 0's box uses 150 for both states: the lobby host's seat is shown by
name but greyed and dead, the module never ticks it and undoes the engine's toggle (maintainer, same
day: "slot 0 must contain correct name of ai master as before, but its radio button must be grayed out
and unclickable"; the relay refuses the seat as well) - the maintainer, same day: "client radio buttons
must be empty instead of question mark"; `patch_online.py bank` builds it from `INTRFACE/KNOBE.SPR`, the
patcher lists it as the fix's `Data`; off = 149, on = 8, the green cross) at list x + 376, 30 px apart
from list top + 6, a 13-column read-only `in_text 40..47` right of each box (x + 407, +3), the heading
`in_text 48` "WATCH AS" at the header line's height; title "Replay Online Game" (**"Online Replay" since 3 Oct 2026** - maintainer: "'Reply online game' header does not fit in frame": 18 characters of `MFONTO2` are 302 px, measured on a 1024x768 capture, and LOADER.GIF's rounded title panel has about 290 px of flat interior, so the text sat on the rounded ends; 13 characters is the limit, data only; the same capture showed the TLS state line "... tick a player, press REPLAY." cut at the `in_text`'s 56 columns - the module says "... tick a player, REPLAY." (54) since then, Ultimate 1024x768 dark `05c43005…`), button **REPLAY** -
**greyed until a seat is ticked** (`set_greyed 0x424574(ip, id; bl = flag)`, the function main.c uses
for the CD-less menu buttons; maintainer: "'REPLAY' button must be disabled until user selects a client
to watch for"; nothing is preselected); background
`REPLAYBG.GIF` = LOADER.GIF with four frames (list, scroll bar, the pane x +368..+518, the text frame
across the whole width). The pane frame ends 2 px inside the 640-wide form at every size (638 at
640x480, 830 of 832 at 1024x768), so one layout serves all resolutions; the form's right artwork is
covered where the pane sits. The ONLINE geometry has no room for a right pane at 56 columns - the
maintainer's row format `[date time] [map name] [map type] [map slots count] [players] [AI count]
[duration]` fits 40 columns as `02.10 14:54 Armageddo Desert 8 2 1 12:34` (map 9, terrain 6, one
digit each for seats / players / computer players, `m:ss`; "1h40" from 100 minutes), with the header
`DATE  UTC   MAP       TERR.  S P A  M:SS` sent by the relay like the rows.

**Radio buttons = `checkb` widgets with the module's exclusivity.** The widget keyword table at
`0x4895B4` (`{name, creator}` pairs) names `checkb` → creator `0x4271AC`: the script line is
`checkb id 0 x y w h <cell off> <cell on> -`, the state struct at `objects[id]+0x28` (+0 state byte, +4
/ +8 the cells), the draw `0x427420` paints the cell by the state, the vtable `0x4896E4` = {redraw
`0x4270D0`, mouse handler `0x42711C`}. **The handler toggles the box itself** on a press (event 4):
it stores the id at `ip+0x431C`, inverts the state byte, redraws, plays sound `0x61` and returns 2
(now ticked) or 3 (now cleared); the hit dispatcher `0x423B30` puts the widget's index into the pump's
out-parameter, so `pump(ip, &arg)` (`0x42417C`) yields kind 2 / 3 with `arg` = the box. The lobby
code sets boxes from code with **`0x427308(eax = ip, edx = id, bl = state)`** (40 callers; asserts the
widget type). The module takes kind 2 / 3, remembers the clicked seat and sets all eight boxes
(`set_boxes`), so exactly one is ticked and the eight behave as radio buttons, and greys the REPLAY
button while none is; `update_pane` fills the names of the selected list entry with every box empty
(the `real` mask of the `REPLAY` message is carried for a client that wants to mark the real players),
bots' seats are selectable too (their names carry `AI ` since the same day). The
first build listened for kinds 4 / 5 - the return values of `0x4273B0`, which is the keyboard entry at
vtable +0xC, not the mouse handler - and the game test showed two ticked boxes. WATCH sends `0x58 RPLAY
id slot`; `0x59 REPLAYING slot` is handled exactly like `ENTERING` (the §10.51 proxy and network
entry), the viewer's game then runs the recorded battle from that seat.

**Module.** `online.c`: `run(ui, gs, replay)` shared by the exports `online_war` and `replay_game`;
`room_screen(..., replay)` loads the screen by mode, sends `0x55 RLIST` instead of `LIST`, parses
`0x56 REPLAYS` (count + header; the list is reset) and `0x57 REPLAY` (id, seats, players, bots, real
mask, u16 seconds, the row, eight names - into `Work.replays[50]`, 9.7 KB of the VirtualAlloc'd work
block, not the section), fills the list when the announced count is reached, polls `g_list_sel` every
loop to follow the selection, handles the box events, and after WATCH is silent until the answer (F81).
`GAME_CHECKB_SET 0x427308` and `GAME_SET_GREYED 0x424574` are the new engine addresses; no new Windows
import. **Money (F83; maintainer, same day: "fix money count increase problem in replay mode, when money
are not shrinking on purchase"):** a purchase is deducted from `MONEY` (`gs+0xBAC+p*0xE34`) only on
the machine that issued it, every machine books the price into `SPENT` (`gs+0xBB0+p*0xE34`), and
money is not in the checksum - so the viewer's seat only ever gained. `viewer_money_fix` in the proxy
thread (100 ms `select` timeout while `g_viewer`) subtracts every increase of the local seat's `SPENT`
(`gs+0x7D3C` = the local game player) from its `MONEY`, the normalisation the game's own `DISCONNECT`
handler applies; a drop of `SPENT` marks a re-initialised battle. **`gs` here is the simulation state
read from `.bss 0x4AA9DC`** (stored by the game start `0x41EAA0`, `mov ds:[4AA9DCh],eax`): the `gs`
the main menu hands the module is proto.c's campaign object (flags `+0x14F0`/`+0x14F4`, settings
`+0x1984`), a different allocation - the first build read local player 0, money 0 from it and
corrected nothing (found with a memory probe of the running viewer, `smoke_rig/money_probe.py`). `ONLINE.LOG` gets the
REPLAY lines (`--- REPLAY ONLINE GAME pressed`, `replay list: entries N`, the first box event's kind /
id, `RPLAY recording` / `slot`, `REPLAYING slot`). **The relay sends the list one command per frame**:
the module reads the first command of every frame (the room table was always one command), and the
first game test lost every entry behind a packed header.

**Patcher.** `gen_apply_script.py`: `Edit-ReplayScript`, `Get-FramedBackground` (the frame drawing shared
by both backgrounds), `Write-OnlineScreen` writes `REPLAYE` + `REPLAYBG.GIF` after `ONLINE` +
`ONLINEBG.GIF`, the 640x480 copies in `$STOCK_COPIES`, the two files excluded from `hd_data` like the
ONLINE pair, the fix `online` text. Fixtures `hd_sets/<WxH>/HD_<h>P/REPLAYE` + `REPLAYBG.GIF` and the
three `bintroe` copies per size regenerated from clean-copy patcher runs. Published Ultimate 1024x768
dark **`cd297478…`** (pwsh 7 = 5.1 = generator reference; the exe is 758 784 bytes with the
14 616-byte module; `4c7da630…` before the host-seat round, `8208da56…` before the REPLAY /
empty-box / money rounds of the same day);
`dcexp16.asm` regenerated. The installer carries a version and build number since the same day
(maintainer: "add version number and build number to the installer"): `PATCHER_VERSION` (1.0, set by
hand in `gen_apply_script.py` when the patcher's behaviour changes) and the build `YYYYMMDD.HHMM` =
the UTC time of the generation, plus the commits of both repositories (`+` = uncommitted changes), in
the window title, the welcome page's footer, the result box, the command-line banner and the file's
header (`$PatcherVersion`, `$PatcherBuild`, `$PatcherGenerated`).

**Confirmed in game (2 Oct 2026, 1024x768, a local relay with one recorded battle):** the menu, the
screen (list row, header, pane, frames), the radio behaviour, WATCH → the viewer lobby ("Replay of
2026-10-02 15:30: Plink - O, 1 min. You sit in Player3's seat (slot 3) ...") → READY → the battle
from that seat, `error.log` empty. Rig: `smoke_rig/drive.py` on a `subst X:` clean copy with
`DEFAULT_SERVER.TXT` = `127.0.0.1 plain`, the relay started with `RECORD_DIR`, the recording made by
`tools/fakeclient.js --room 1` (45 s); clicks at 1024x768: REPLAY ONLINE GAME (608,539), first list
row (352,254), box k (701, 260 + 30k), WATCH (640,604), the lobby's READY box of slot s (815, 170 + 19s).
A recording made with the fake client carries its synthetic MREADY (game player = slot), so the relay
logs "start shuffle differs from the recording" for it - harmless for the smoke test, absent for real
recordings.

## 11. Risks

| Risk | Assessment |
|---|---|
| No fallback if 1024×768×16 is refused | Real. `set_mode_16` / `create_surfaces` abort. Mitigate by keeping the unpatched exe alongside, and consider adding a mode-list walk later. |
| `pitch != width * 2` on the offscreen surface | The code ignores `lPitch` everywhere. For a `SYSTEMMEMORY` surface DirectDraw has always returned a tight pitch; 1024 px = 2048 B is a friendlier alignment than 1280 B, so this is lower risk after the change than before. Verify once in stage 1. |
| A missed hardcoded 640 | The §8.2 sweep was systematic but §8.3 lists three unresolved items. Symptom would be a skewed or torn band rather than a crash. |
| A missed hardcoded **512 / 448** (viewport constants) | **Realised once (§10.6):** `draw_terrain` steps the lightplane by a literal 512 per row; symptom was a regular grid of black lines, not a crash. Fixed (31 sites). Any other buffer sized from the viewport and filled by a hand-unrolled loop is a candidate for the same. |
| Menu scripts drifting from the exe | Low: the `size` line is data and the parser accepts both forms (§6, verified). |
| Multiplayer fairness | Stage 3 changes what a player can see. Not a desync (§10 stage 3), but a balance issue between patched and unpatched clients. Decide whether to gate it. |
| Growing `.bss` | Not needed — nothing resolution-dependent lives there (§5). |
| A fixed-size render buffer | **Realised, identified (§10.4) and fixed (§10.5).** `draw_terrain`'s stack lightmap holds 5248 bytes; the frame is now grown to fit (11 code sites + 2 PE header fields), and 28×23 tiles run. The 144-byte row stride is fine up to 34 across because the overflow only hits unused cells. Remaining unknown: lighting at the right edge was reasoned, not seen. |
| Stack usage | The grown `draw_terrain` frame is 7 KB (28×23). Watcom has no stack probe, so the patch also raises the PE stack commit to 256 KB and the reserve to 1 MB. Any other function found to size a buffer from the view will need the same treatment. |

---

## 12. Address index

| Address | Meaning |
|---|---|
| `0x00488DB4` / `0x00488DB8` | **screen width / height globals (`DGROUP`)** |
| `0x004510D0..0x00451820` (CW +0x60) | **`cdaudio` MCI module** (stock; **rewritten by fix `music`** as the MP3 player, entry points kept): `cd_open` `0x004510D0` (device type `cdaudio` `0x00487C1C`), `cd_play_from_here` `0x0045110C`, `cd_close` `0x00451158`, `cd_stop` `0x00451188`, `cd_tracks` `0x00451408`, `cd_seek_track` `0x004515B8`, `cd_read_toc` `0x0045164C`, `cd_mode` `0x004517B0`; the only user of `mciSendCommandA` (IAT `0x00480570`); §10.31 |
| `0x0042F9F8` / `0x0042FA9C` / `0x0042FAC0` / `0x0042FA80` / `0x0042FA4C` / `0x0042FA28` (CW +0x60) | `ddex4.c` music layer: open / start (seek track 2 + play, playlist ignored) / poll (restart from track 2 when stopped) / stop / play(track, unused) / track count; device id `0x00489744`, no-CD flag `0x00489748`; display-wrapper slots +0xB8..+0xCC, called from `0x00404E60`, `0x0041F09F`, `0x004320DB`, `0x00401A41`, `0x0040502D`; §10.31 |
| `0x00429BC0`ff / `0x004A46C0` | `scenario.c` scene-list reader: per-mission playlist `%d … -1` (max 10, assert line 1260) → bytes `0x004A46C0..`, count `0x004A46CC`, cleared at `0x004298D2`; never read by the music code; §10.31 |
| `0x004527F8` / `0x00452870` / `0x00452580` | `set_volume(level 0..10, method)` (display slot +0xE8, options widgets `0x43`/`0x44` music, `0x2A`/`0x2B` sound): method 0 = aux CD-audio device `auxSetVolume` (no such device on modern Windows; **fix `music` rewrites `0x00452870` as `MCI_SETAUDIO` volume**), method 1 = mixer `SRC_WAVEOUT` line; §10.31 |
| `0x0040117F`ff | `main.c`; full-screen rect at `0x004010E5` |
| `0x00405F88` (CW `0x00405F68`) | `safefunc.c` start-up: reads `HBNFUFL.A01`/`.A02`, builds the CD path `%c:\dc\` (`0x00482654`) into `0x004A48B0`, `full` marker → `0x00488DF5`, calls `cd_probe`; **patched: `jmp` from `0x00405FB3` to the `full` check, probe call NOPped** (`cddrive`, §10.19) |
| `0x00405EAC` (CW `0x00405E8C`) | `cd_probe`: `fopen <CD>anim.dat` + write test `<CD>a<rand>` → flag `0x004A49B8`; re-run by `load_interface` (`0x00423223`) and in game (`0x0041138D`); **patched to `ret`** (`cddrive`, §10.19) |
| `0x00405E8C` / `0x00405EA0` (CW `0x00405E6C` / `0x00405E80`) | CD-flag getter / CD-path getter (wave loader fallback `0x00452AEF`, `0x00452B5B`; generic helper `0x00406253`) |
| `0x004063DF` / `0x00452AE9` / `0x00401078` (CW `0x004063BF` / `0x00452B49` / `0x00401078`) | CD-path fallbacks of the open helper, the wave loader and the movie opener; **patched to `jmp` past the CD attempt** (`cddrive`, §10.19); wave loader box strings `0x00487DE0` / `0x00487DF0` (CW `+8`) |
| `0x00406FD0` / `0x00406FF0` | `avi_begin` / `avi_end` (mode cycle) |
| `0x004073C4` | clear back buffer for movie (stock: 640×480, pitch `0x500` literals; patched to read the Lock description, §10.8) |
| `0x00407068` | `avi_create_surfaces`: flip chain (`0x489718`/`0x48971C`, flag `0x488E0C`=1) or plain primary + 320×180 movie surface `0x488E00` (`0x004071F7`ff) |
| `0x00407440` / `0x00407018` / `0x0040764C` | `clear_and_flip` / `release_surfaces` / `restore_surfaces` |
| `0x00407674` | `draw_flip`: stock 2× software doubler into the back buffer, writes one row of two (§10.8) |
| `0x00407898` | `draw_offscreen`: 1:1 writer into the movie surface; `BltFast` at `0x00407E14` → stretching `Blt` (§10.8) |
| `0x0040762B` | movie letterbox: `yoff = 241 − H` |
| `0x00408CB8` / `0x00408AC8` | `play_avi` / display thread (`0x00408B37` picks the drawer by `0x488E0C`) |
| `0x00428448` / `0x004289D0` | `scenario.c` **text-file box** (TTY) / **animated picture window** (PIC) — the two primitives behind every code-positioned menu element; x in `edx`, y in `ebx` (§10.7) |
| `0x00428968` / `0x00428B8C` | TTY glyph placement / PIC per-frame draw |
| `0x00424A40` | `gadget` keyword creator (keyword table `0x00489588`, `{name, creator}` pairs; `label` = `0x00426BD4`, `banim` = `0x00427854`, §10.40) |
| `0x00427854` | `button.c` `create_banim`: n plate ids + m button ids at `widget+0x28`; the opening wave runs in `0x004279EC` (from `0x00427AE4`, type `0x0C`) (§10.40) |
| `0x00404E80`ff | main.c `bintro` menu init: credits TTY create, `load_interface("intrface/bintro")` `0x00404EC4`, then (stock) wave `0x00404EFE` → buttons `0x00404F0B` → logo `0x00404F13`; loop head `0x00404F87`, title-at-frame-10 `0x00404FB0`, TTY update `0x00404FE3`; fix `menuorder` rewrites `0x00404EF6..0x00404F1E`, `0x00404F21..0x00404F86`, `0x00404FB0..0x00404FE2` (§10.47) |
| `0x0042626C` / `0x00426294` | `animate.c` anim start `(anim, gadget, mode)` / anim step; anim object at `gadget+0xA0` (`+4` frame, `+6` mode 0 loop / 1 one-shot / 2 stopped=finished); getters `0x004266FC` frame, `0x0042670C` mode, wrapped by `0x00425034` / `0x004250CC(ip, id)`, starter `0x00425164(ip, id, mode)` (§10.47) |
| `0x00403732` | briefing screen: `intrface/epic` marker at `(gs+0x14E0, gs+0x14DC)` from `GAMESTAT/*SCENE.TXT` (`scenario.c 0x00429B67`) |
| `0x0041EB34` / `0x0041EB63` | `proto.c`: `"intrface/main"` → `load_interface`, HUD handle to `0x004AB1C4` — same function as the map view rect below |
| `0x0041ED1E`ff | `proto.c` initial camera + map view screen rect |
| `0x004231E8` / `0x004231B0` | `load_interface(app, name, flags)` / free — 21 call sites, one per screen |
| `0x0040B430` | `timeGetTime` thunk (**not** a loader — easy to misread next to the HUD load) |
| `0x0042BD78` | `new_screen` (0x1A4 bytes) |
| `0x0042E340` | **window procedure**: `WM_DESTROY`, `WM_SETCURSOR` (patched to return TRUE, §10.12), `WM_SYSCOMMAND`/`SC_SCREENSAVE`; `DefWindowProcA` tail `0x0042E3ED`, epilogue `0x0042E3FE` |
| `0x0042E688` | `create_window`: `WNDCLASSA` at `[ebp-0x28]`, `hCursor` `[ebp-0x10]` uninitialised in stock (§10.12); `CreateWindowExA` `0x0042E710`, `ShowWindow`/`UpdateWindow` `0x0042E783`/`0x0042E794` |
| `0x0042F1C8` / `0x0042F2B0` | message pump (`PeekMessageA` for `WM_SYSCOMMAND`, `WM_SETCURSOR`, `WM_DESTROY`) / per-frame `SetCursor(NULL)` + pump |
| `0x0047EF8A`ff | import thunks (`jmp dword ptr [IAT]`), `SetCursor` `0x0047EFF0`; zero tail `0x0047F1CA`–`0x0047F1FF` holds the §10.12 stub at `0x0047F1D0` |
| `0x004063E4` | **overlay file open**: prefix slot `0x004826D0` (`exp/`, 8 bytes) + name, fallback bare name in the game root (§10.13); wave-loader copy of the prefix `0x00487DC8`; save folder slots `0x00482344` / `0x00485E5C` |
| `0x00404DC8` | main menu: `bintro` load `0x00404EC4`, button dispatch `0x0040502C`ff (0 NEW CAMPAIGN → `gs+0x14F4=1`, 1 TRAINING → `gs+0x14F0=3`, 2 LOAD GAME `0x00403AA4`, 3 MULTI `0x00405C20`, 4 SINGLE `0x00405AE4`, 5 ENCYCLO `0x00402614`, 0x0C QUIT, 0x10 PLAY INTRO → OZI MISSIONS §10.13), between-mission loop `0x0040513D`; campaign runner `0x00401C08` |
| `0x004051CC` / `0x0042565C` | start-up `anim.dat` reader / FIN + sprite-bank loader (`animate/%s`, `sprites/%s` `0x0042538C`); `0x004309C8` `sound2.dat` (200 entries); balance tables per game `0x0043C4AC` |
| `0x0047F240` / `0x0047F290` / `0x0047F2E0`ff | §10.13 `stub_pack` / `stub_cw_set` and the three trampolines in the AUTO zero tail (DCEXP16); `0x00405AE4` SINGLE PLAYER WAR (dead since OZI LOAD took its button) |
| `0x0040C0BC` / `0x0040C0FC` / `0x0040C22C` | `smalloc.c`: create the local pool (size `imm32` at `0x00405319`, Classic `0x00405334`; stock 11.5 MB, patched 32 MiB, §10.13) / allocate / release; tenants listed in §10.13 |
| DGROUP `0x00482130`…`0x00485F34` (Classic file `0x7F930`…`0x83734`; DCEXP16 file +0x200) | the 30 literal interface paths (`intrface/bintro`, `gamestat/hscene`, `intrface/load.bmp`, …) whose directory part is `intrf_hd` in the patched exes (§10.17, `patch_hd_paths.py`); `load_interface` `0x004231E8` appends the language letter, `0x00403002`ff appends `.txt` to the briefing-list names |
| `0x0041BBFC` (CW `0x0041BC5C`) / `0x00488DEC` (CW `0x00488E14`) | `gs->tick_ms` (`+0x970`) initialiser and the persistent desired-tick setting (4th of the globals `0x00488DE0..` / `0x00488E08..`), both stock 66 ms = 100 %, patched 44 ms = 150 % (§10.14); copied into `gs->desired_ms` (`+0x96C`) at `0x0041EB29`, applied by the negotiation `0x00419830`; options-screen maths `0x00432F10` / `0x00432CD3` |
| `0x0042C29C` | `driver_create` — builds `ctx` (0xEC) + `screen`, sets clip/bounds |
| `0x0042C405`ff | copies `screen+0x100…` method slots into `ctx+0x30…` |
| `0x0042E688` | `create_window` (reads the globals) |
| `0x0042E7B4` | `win_init` |
| `0x0042E890` / `0x0042E914` | `SetDisplayMode` 8-bit / 16-bit |
| `0x0042E998` | `create_surfaces` |
| `0x0042F2D0` / `0x0042F320` | `make_colour` / `set_palette` |
| `0x0042F774` | `clear_screen` (`0x4B000` words + `Flip`) |
| `0x0042F828` / `0x0042F8D8` | `lock_screen` / `unlock_screen` |
| `0x0042FBF0` | `install_ddraw_driver` |
| `0x004223A8` | `widget.c` `size` keyword parser (2 or 4 numbers) |
| `0x00422D84` | `widget.c` `window_draw`: erases the window's `size` rect with `colour erase`, flips, then loads the background (§10.15); called at the end of `load_interface` `0x00423A46` |
| `0x0043AAC0` / `0x0043AB38` | `clock.c` `clock_init` / `clock_draw` (day/night hand, `sprites/cloc`, anchor at `0x0043ACB5`/`0x0043ACA2`; §10.15) |
| `0x004232E2`ff | `widget.c` interface-script keyword dispatch |
| `0x00435E24` | clip view rect to map (uses the 16/14 tile counts) |
| `0x00435E7C` | build the render view struct at `0x005044AC` |
| `0x00436080` | per-frame `set_view_target` (`y*1280` at `0x004360BE`) |
| `0x00436380`ff | main map render: lock, terrain, objects, unlock, minimap |
| `0x004365BC` | `make_rect(x, y, w, h)` → `{x, y, x+w, y+h}` |
| `0x0043A064` / `0x0043A43C` | minimap terrain plot / minimap object plot |
| `0x0040A070` | minimap click handler (mouse → map tile, then order/scroll by event type); called from the dispatcher `0x0040A484`ff after `point_in_rect` on `ui+0x7B4` (§10.9) |
| `0x0043658C` | `point_in_rect(x0, y0, x1, y1, px, py)` |
| `0x004AB184` | `ui+0x7B4`: minimap hit rect, built by `proto.c 0x0041EE43` |
| `0x0044EE30` | allocate the shade LUT at `0x0048C188` |
| `0x0042532C` | `load_sprite_bank` (`animate.c`) — `"sprites/%s"` (§6.3) |
| `0x0044F790` / `0x0044F818` | `.SPR` size helper / parser (`ctx+0x44` / `ctx+0x40`) (§6.3) |
| `0x0044FAC0` | **sprite-cell blitter**, raw cells (`juicel.c`) — resolution-independent |
| `0x0044FC44` | sprite-cell blitter, RLE cells; decode loop `0x0044FD4F` (§6.3) |
| `0x0044FE81`ff | per-cell draw dispatcher; applies cell offsets at `0x0044FE9A`/`0x0044FEA2` |
| `0x0044FFC0` | `image.c` full-screen 8-bit page (`malloc(W*H)`) |
| `0x00450E20` / `0x00450E80` | mouse absolute / DirectInput poll + clamp |
| `0x00453770` | `lighting_init` — allocates the "lightplane" at the viewport size, sets `0x0049931C` (viewport width) and `0x005360B0` (viewport pixel count) |
| `0x00453910` | `draw_terrain` (`lighting.c`); `>> 5` ⇒ 32-px tiles. **Fills the lightplane**, does not draw pixels (§10.6) |
| `0x00453CCB … 0x00454276`, `0x004542BC` | `draw_terrain`: the 31 lightplane row advances, literal `0x200` = 512 — **patched to the viewport width** (§10.6) |
| `0x004542D9` / `0x004542E8` | `draw_terrain`: tile advance (`+0x20`) / tile-row advance (`32 * [0x0049931C]`) — the two that were already width-correct |
| `0x0045011C` | **terrain tile drawer** (`tile.c`): dest from `view+0x10` stride, zoom shift from `view+0x12`, builds the occlusion mask (§10.6) |
| `0x004538C0` | `light_at(view, x, y)` = `[view+0x1C] + y*[0x0049931C] + x` |
| `0x0048C14C` / `0x0048C15C` | `DGROUP → AUTO` tile-blit dispatch tables, indexed by zoom and tile flags (`0x004503E1` / `0x004502E7`) |
| `0x005326A4` | 256-byte pixel→mask-bit table, built by `0x004500B0` |
| `0x0049931C` / `0x005360B0` | lightplane width / pixel count (set by `lighting_init`) |
| `0x00453AD4` | **§10.3 fault site** — `mov esi,[esi]` on the ground-layer row-pointer table; base is the local `[ebp+0x56]` |
| `0x0045393A` | `draw_terrain`: the only write of `[ebp+0x56]` = `[view+0x14] + 0x804` |
| `0x00453915` / `0x0045391B` | `draw_terrain` frame: `sub esp,14CCh` / `sub ebp,7Ah` |
| `0x00453B20` | **§10.4 overflowing store** — stack lightmap, `[eax+ebp-144Ah]` |
| `0x00453A40` / `0x00453B32` / `0x00453BC4` | stack-lightmap fill loop (even,even) / interpolate loop (odd,odd) / per-tile draw loop, reads (odd,odd) |
| `0x00453BB3` | interpolated lightmap store, `[ebx+ebp-144Eh]` |
| `0x00453B7A/B81/B88/B91`, `0x00453C05/C2D/C3C/C5D` | the other eight lightmap accesses (§10.5); with `0x00453B20`/`BB3` the complete set of ten `disp32` bases the patcher moves |
| `0x00453B07/B55/B6B/B9F/BF4/C1C` | the six ×144 row-stride idioms (`lea [s*8]; add; shl 4`) — **left unpatched**, see §10.5 |
| `0x00454303` | `draw_terrain` epilogue `lea esp,[ebp+7Ah]` — frame-size independent |
| `0x004539DD` | `draw_terrain`: first instruction after the tile counts are stored — breakpoint site for §10.5 |
| file `0xE0` / `0xE4` | PE optional header `SizeOfStackReserve` / `SizeOfStackCommit` (`0x13880` / `0x10000` stock), same in ENGEXP16 |
| `0x0040AB1C` / `0x0040AB2B` | view origin from the camera (half-viewport y / x) |
| `0x0040AF16` | camera clamp call site |
| `0x0040B0BC` / `0x0040B0E0` | frame render: view origin (half-viewport y / x) |
| `0x0041EE66` / `0x0041EE6F` | write the camera bounds into `ui+0x114…0x120` |
| `0x00436668` | generic `clamp2d(min_x, min_y, max_x, max_y, &x, &y)` |
| `0x0043613E` | render-item list guard, `cmp [0x005044E4],320h` (capacity 800) |
| `0x004AA9D0` | `ui` base; `+0x114…0x120` = camera bounds |
| `0x004F6F2C` / `0x005044E4` | static render-item list / its live count |
| `0x004537AD` | load `FADE.DAT` into `0x00533C90` (`0x2420` B) |
| `0x004543FC` | `draw_objects` (`sprite.c`) |
| `0x004DFF14` / `0x004DFF1C` | current mouse X / Y |
| `0x004DEC90/92/94` | R/G/B → 16-bit component LUTs |
| `0x005044AC` | render view struct (§5) |
| `0x0048C188` | shade LUT base (`level * 512 + idx * 2`) |
| `0x0048978C` / `0x00489790` | global `ctx` / `screen` |
| `0x00533C84` | current destination pixel pointer |

---

## 13. How this was produced

`dumpbin -NOLOGO -ALL -DISASM` output of Classic `dc16.exe` (`dc16.asm` in the development
folder), plus:

* the `DGROUP` raw hex dump reassembled into a binary to extract strings with their VAs
  (`VA = file offset + 0x402800`), which gave the assert `*.c` file names and hence a **module map
  in link order** — that is what localised the renderer to `engmain.c` / `lighting.c` /
  `sprite.c` / `juicel.c`;
* following the DirectDraw COM vtable offsets (`IDirectDraw::CreateSurface` `+0x18`,
  `SetCooperativeLevel` `+0x50`, `SetDisplayMode` `+0x54`; `IDirectDrawSurface::Blt` `+0x14`,
  `Flip` `+0x2C`, `GetAttachedSurface` `+0x30`, `GetDC` `+0x44`, `GetSurfaceDesc` `+0x58`,
  `Lock` `+0x64`, `SetPalette` `+0x7C`, `Unlock` `+0x80`) and the `DDSURFACEDESC` layout;
* enumerating the call sites of the `ctx+0x8C` / `ctx+0x90` lock/unlock pair to get the complete
  set of direct framebuffer writers;
* a scripted sweep of `shl reg, 4…12` preceded by a `×3`/`×5` idiom to find the strength-reduced
  strides of §8.2, which a search for `280h` alone does not find;
* relocation-tolerant byte matching of a window around each Classic site against `ENGEXP16.EXE`
  and Council Wars `dc16.exe` (masking any dword in `0x401000…0x542000`) to produce the
  cross-binary offsets, then a byte-for-byte re-check of every match; for §10.10 the same idea
  run as a sweep (a 32-byte window every `0x40` bytes, searched ±`0x200`) to get the delta *map*
  of the whole code section rather than one offset per site, plus `dumpbin -HEADERS` of both
  builds to separate file-pointer shifts from virtual-address shifts, and a `dumpbin -ALL -DISASM`
  of `ENGEXP16.EXE` (`engexp16.asm` in the development folder) to compare the `TTY`/`PIC` call
  sites and read the `exp/` opener;
* reading `INTRFACE/MULTIE~1.TXT`, `LOPTE` and `MAINE` directly, which is how the interface
  scripts turned out to be editable text;
* to identify the HUD: resolving every `intrface/*`, `sprites/*`, `animate/*`, `gamestat/*` string
  in `DGROUP` to the code addresses that reference it, then mapping those addresses to modules via
  the assert-string module map — which showed `"intrface/main"` being loaded by `proto.c` right
  beside the map-view rect, rather than by `interface.c` as the module name suggests;
* decoding `INTRFACE.GIF` with PIL and histogramming the presumed map rect against the rest, then
  reducing per-column and per-row opacity to runs, to confirm the frame geometry independently of
  the disassembly.

* to localise the §10.3 crash: a **viewport sweep** rather than a code audit — `--viewport`
  separates the map-view size from the framebuffer size, so four builds bracket the failure
  between 20×16 and 24×19 tiles and prove the arithmetic innocent; then `cdb` (WinDbg store
  package, `x86\cdb.exe`) driven by a `-cf` command file, whose register dump identified the one
  corrupted local from `esi = (index << 2) + [ebp+0x56]` in a single capture. Both are worth
  reaching for before sweeping the disassembly: the §10.2 lesson repeats itself here;

* to find the §10.4 buffer: a `cdb` **hardware watchpoint** on the one corrupted local, with the
  legitimate writer filtered out inside the breakpoint command — it named the store in a single
  run, after three earlier runs had been silently armed at the wrong address because `bp dc16+...`
  parses `dc16` as a hex *number*. Then the two caps were derived from the store's own index
  arithmetic and checked against the four bisection builds, which is what turned one register dump
  into a closed-form limit on both viewport dimensions;

* to decode `.SPR` (§6.3): reading `juicel.c`'s parser and both blitters rather than pattern-matching
  the files, then validating by re-encoding all 15 088 compressed cells and by rendering cells to PNG
  (the buttons and font glyphs are immediately recognisable, which is the cheapest possible check
  that a palette-and-RLE guess is right).

Three dead ends worth recording, so they are not repeated: the in-game panel is **not** in a `.SPR`
bank of its own (it is the `INTRFACE.GIF` background of `MAINE`); `interface.c` is the
save/load/options **dialog** module (`intrface/lsg`, `lobj`, `lqc`, `lopt`), not the HUD; and the
`.SPR` palette starts at offset **8**, not at the first `3F 3F 3F` triple — assuming the latter puts
the cell directory at 779 instead of 776, which by coincidence makes width/height look big-endian
and fits some files but not others.

#### 10.49 The battlefield in the menus' console style: frame, cell bank and dialog plates redrawn **(28 Sep 2026, maintainer: "battlefield interface and menus styles are bad. You must create the same style as in race selection, network lobby and other menus", then "use original sprites for buildings, units and upgrades", then "keep the icons and portraits, red-outlined button icons which mimics lobby button style everywhere"; `tools/hud_console.py`; data only, no exe byte; confirmed in a 1920×1200 training battle, both the HUD and the options / objectives dialogs)**

**Final form (after two corrections the same evening).** Three rules, taken from the lobby screen:

1. **Surfaces are grey pipework, not black.** `MULTIWIN.GIF` is 11-grey ground (67) with 35-grey
   tubes (65) edged by a 107-grey light line (40), 43-grey compartment outlines (62), 23-grey plain
   cells (66), vents (light dashes on 35-grey) and rounded tubes; black is only inside its read-out
   screens (the maintainer: "lobby is not black, it is in different intensities of gray"). The frame
   is therefore filled by a seeded generator (`Pipework`: horizontal bands of compartments / nested
   outlines / vents / plain cells / rounded tubes separated by 4-7 px tube bands; seed
   `W*10007+H`, so every run reproduces the shipped picture) over the panel column, the bottom bar
   and the borders, and the screens - minimap, the whole tab row + button grid (black, so a hidden
   button group leaves no hole), status, DAYS, money, dial, the message box, the DARK COLONY strip -
   are black windows with the light edge line cut into it.
2. **Every button is the lobby's plate.** The `KNOBE.SPR` ring measured on its cells 0, 4, 10 and
   28: a 3-px ring on black, outer index 100 = (79,7,7) with 101 = (39,7,7) in the four corners, the
   bright line 80 = (255,47,0), inner 100 with 98 / 99 softening its corners; pressed = the lobby's
   green grid (125 fill, 124 lines every 8 px, 123 border), used for the lit tab. Unit / building /
   upgrade cells keep their portrait pixels inside it; BUILD, the bar arrows' boxes, the dialogs'
   title plate, OK / cancel, the diplomacy plates and PAUSED carry it; the arrows are `KNOBE`'s own
   red triangles (cells 10..17); the tabs show the game font's digits 1 2 3 (green on the lit one).
3. **Icons are clean single-colour red-outline glyphs**, like the lobby's arrows (the maintainer on
   the first attempt's traced glyphs and `BUTTON.SPR` neon icons: "absolutely unacceptable ... messy
   ... do not obey lobby style"). Each of the 21 order / option icons is drawn as vector shapes in
   the tool (`_icon`: lines, polygons, circles, arcs, `?` and `$` from a bold TrueType font),
   rasterised through an 8x coverage map into the red ramp 97 / 99 / 100, 2-3 px strokes on the
   53x35 plate interior: quit X, save floppy, options `?`, allies (two rings), pause (two bars),
   objectives (open book), stop (octagon), move (four arrows), move & attack (arrows + bolt),
   waypoints, deploy (down arrow onto a line), deploy turret, deploy mine (spiked circle), napalm
   (flame), disease (three rings), steal money `$`, second / ground attack (crosshair), inspire
   (star), drop ship, saucer, and the unreferenced shovel. The cell-to-name mapping is `MAINE`'s own
   `textmsg` list (`ICON_CELLS`).

**Third round (maintainer: "up/down buttons of battlefield chat are skewed. day/night clock is old
on top of new one, and on the new one day and night parts are indistinguishable. previous style of
first tab was ok, return them back and create the same style buttons for third tab. battlefield menus
are still black, you must update thy stiles too!").**

* **Icons** - rule 3 above is superseded: the neon icons of `BUTTON.SPR` are the approved style. The
  twelve cells it has an icon for (`?`, `X`, stop, move, move & attack, waypoints, deploy turret,
  deploy mine, napalm, plague, deploy, steal money - `FROM_BUTTON`) carry them on the lobby plate
  (every pixel that is not the grey plate, hot-key letters included); the nine it lacks (save, allies,
  pause, objectives, crosshair, inspire, drop ship, saucer, shovel) are drawn in its idiom (`_neon`:
  the vector shapes rasterised into the cyan ramp 138 / 139 / 141 with softer thresholds, a bright
  core and a dim glow - cyan like its own `?`, so the team-colour remap treats them alike).
* **The bar arrows** were the lobby's triangle glyphs cut out of `KNOBE` cells 10..13, whose bevelled
  shading reads as skew at 16 px, placed at the cell's centre while the 16x16 cell is drawn at the
  top-left of a 20x19 button rect. Now: a symmetric outline triangle drawn by the rasteriser
  (`_triangle`), centred on (9.5, 9) of the cell = the middle of the frame's plate; pressed = green.
  The dialogs' step / scroll arrows are the same triangle centred on (8, 8).
* **The clock.** The hand is not a hand: each of the 36 `sprites/cloc` cells (28x28) is the whole
  metal dial with the hand painted on, blitted by `clock_draw` over the frame - which is why the
  stock face sat on top of the new one. Measured in game at 1920x1200 the cell lands at (1888..1915,
  1170..1197), i.e. stock (608..635, 450..477): the "bottom-right anchor" of §10.15 is in fact the
  cell's top-left. **`SPRITES/CLOCK.SPR`** (`hud_console.py clock`, 36 cells, stock flags and offsets)
  is the dial redrawn: light rim, the right half the day (65-grey with a yellow sun at 3 o'clock),
  the left half the night (11-grey with a grey moon at 9 o'clock), a 43-grey meridian, the red hand
  sweeping clockwise from 12 (cells 0..17 the day half, 18..35 the night half, as the stock cells
  do). The frame's dial is only the bezel now, centred on (621, 463) r 15. The patched exes read it
  through a third edit of fix **`clock`** (`patch_clock.py`): the DGROUP path `sprites/cloc` (12
  chars + four zero bytes, Classic file `0x83C68`, Ultimate `0x83E70`) becomes `sprites/clock` (14
  bytes written); the stock `SPRITES/CLOC.SPR` stays for the original exe, `SPRITES\CLOCK.SPR` is
  `hd_data`. **Every game build's reference hash changes**: 1024x768 Classic `88f59c1d…` (was
  `bd86e63e…`), Ultimate `63ccbe88…` (was `5acee874…`); the game folder's 1920x1200 builds
  `c7505258…` / `cdac7ca1…` (= the patcher's references for that size).
* **Dialog rows** are grey pipework: `_dialog_row` fills each 304x16 row with a `Pipework` band
  between 35-grey side tubes (seeded per row index, so equal rows tile), the top / bottom tubes on
  rows 0 / 2, the black list window (x 24..279) on rows 3 / 4 / 5.

**Fourth round (maintainer: "inactive tab buttons must be gray. active button must be red. build
button must have red text. third tab buttons must be the same style as action buttons for units!").**
The tab strips: the active tab is the lobby's red plate with a red digit, the inactive ones grey
plates (35-grey fill, 107-grey edge line) with a light-grey digit (`grey_plate`); the green grid is
no longer used. BUILD's lettering is `BUTTON` cell 76's, recoloured into the red ramp. The Game
Option tab (and every other non-portrait cell `BUTTON.SPR` has no icon for: crosshair, inspire, drop
ship, saucer, shovel) is built in the layout of BUTTON's unit action buttons: the pixels the ten
action cells 62..75 have in common (`action_template`: the rounded hot-key box in the top-left
corner and the circuit line descending from it) on the lobby plate, the hot-key in the box in
BUTTON's light greys (6 / 11 / 17; the keys are the ones the shipped MAINBUT badges show: O options,
Q quit, D attack / drop ship / saucer, F11 save, J objectives, ESC pause, the return arrow for
inspire, none for allies), the cyan neon icon in the area to the right (`ACTION_CELLS`,
`action_cell`). Confirmed in game.

**Fifth round (maintainer: "BUILD button font must be the same as in lobby. day/night clock is not
appearing instantly on game start").** BUILD is written like a lobby caption: `MFONTO5` glyphs
(the font every lobby script names for its `label centre` captions), centred on the plate, in the
colours the lobby's `remap 0` gives that font - sampled on the race-selection screen and mapped to
palette entries 86 / 221 / 99 / 100 / 244 (`LOBBY_CAPTION`); `BUTTON` cell 76's neon lettering is no
longer used. The clock: `clock_draw` blits a cell only when its index differs from the last drawn
one, so after a start the dial stayed empty until the first phase step (about 45 s in training); the
frame now carries the bank's first cell (day, hand at 12) at the cell's measured place under the
bezel, and the code's own cells overwrite it from the first step on (a loaded game mid-day shows the
12 o'clock hand for those seconds - data-only, accepted).

**Sixth round (maintainer: "BUILD button font must be just red! third tab, images on the buttons
must be in different colors similar to unit command buttons").** BUILD keeps the `MFONTO5` glyphs
but in plain red (`CAPTION_RED`: core 96 = (255,31,31), shadow 100; the lobby's sampled remap stays
in the tool as `LOBBY_CAPTION` for reference). The drawn icons take one hue each, as BUTTON's unit
commands do (`NEON_RAMPS`, `ACTION_CELLS`): quit orange, save green, options cyan, allies yellow,
pause red, objectives blue; crosshair red, inspire yellow, drop ship blue, saucer green, shovel white.
Confirmed in game.

**The shortcuts on the buttons (maintainer: "carefully write these action shortcuts (F11 and ESC)
on the corresponding buttons").** Both keys are real, traced and pressed in game: the key translator
(`0x0042FF75`ff, VK → the game's event codes) turns **F11** (VK `0x7A`, branch `0x00430610`) into
function-key event `0x12`, which the client's key jump table (`0x0040A3F0`, index code−8) routes to
`0x0040A5D9` → `0x00432708`, the save / load dialog (`intrface/lsg`); **ESC** (VK `0x1B`,
`0x00430248`) becomes code `0x74`, whose client handler `0x0040A572` → `0x0040A38C` sends pause
command 1 (or 2 = resume when `gs+0x46F51` is set) through `0x0040A1E8`; the game-state handler
`0x0041E5E4`ff sets / clears the flag the PAUSED overlay follows - one command for every client of a
network game. The other keys of the same handlers, read but not pressed: F1..F9 → codes `0x0B`..`0x10`
(other client functions), PgUp / PgDn (`0x95` / `0x93`) step the mission-message ring, digits select
groups, and the dialog key handler `0x004337F0`ff opens quit on `>` (Shift+.), save / load on `?`
(Shift+/), options on `@` (Shift+2) and objectives on `0xCA` (`J` with a modifier, not traced).
On the cells: a three-character key gets a wider hot-key box (`WIDE_BOX`, 26x14 inside the plate
ring, the template's box colours 60 / 101) with the key written in `MFONTO7` glyphs recoloured to
BUTTON's key greys (`KEY_FONT`), and the icon scaled into the space right of and below it
(`WIDE_ICON_AREA`; `_neon` scales a design to a smaller area).

**Seventh round (maintainer: "build units and upgrades icons must have gray frame instead of red
frame" → "do the same gray frame for second and third tab buttons. do diplomacy button have a
shortcut? diplomacy button must have a pigeon instead of eternity sign. under diplomacy header is
quirky, make it clearer").** Every grid button - portraits, the unit order buttons, the Game Option
tab - now carries a **grey frame** (`grey_frame`: the lobby ring's geometry in the pipework greys,
35 / 107 / 35 with 11-grey corners); red is left to the active tab, BUILD and the dialogs' buttons.
The Allies menu has **no shortcut**: the F1..F9 codes (`0x0B`..`0x10`, jump table `0x0040A3F0`)
issue unit orders through `0x00409A00` (order codes 1..6, `0x2B`, `0x31`, `0x45`), F11 opens save /
load, nothing routes to widget 151, and its shipped badge is blank too. Its icon is a dove (filled
body, head, beak, wing, tail, yellow). The diplomacy header strip (`MAINE` picture 152, cell 92,
118x43, shown above the player rows of group 153) is redrawn (`strip_cell`): a grey frame, `ALLIES`
in `MFONTO7` (yellow-orange) across the top band, and one clear icon per column of the rows below -
the drawn thin-stroke set `_column_icon` (peace sign yellow, oval eye cyan, speaker with sound
waves green = the chat action, dollar red = give money), each at its column's size; `ALLIES`
across the top band in the lobby caption font `MFONTO5` with only its core index kept, one flat
colour (75), so it reads as clean terminal text without the pixel font's shadow ramp. The rows'
24x17 plates (`small_plate`): 124 / 119 / 120 / 127 (empty, crossed circle = no pact, circle =
pact, small X) are **the stock cells byte for byte**, metal bevel included; 123's dark "1000 ⇒" is
inverted onto the grey frame in one light grey (index 15 = (172,172,172)). Settled after a side-by-
side of stock and new (maintainer: "for headers your own last drawn pictures are most acceptable.
for per user options - '1000 ->' icon is ok, but other options use exactly original" → "'ALLIES'
text must be terminal clear font ... '1000 ->' must be light gray"); an intermediate form used the
stock header pictures inverted (`_inverted_glyph`, still in the tool), whose talk figure lost its
mid-grey body. The panel itself opens only in a multiplayer battle (the training mission ignored the Allies
button), so the header is checked on the rendered sheet, the tab in game.

The conduit-ring description below is the FIRST form (28 Sep afternoon: grey `BUTTON.SPR` plates and
its neon icons on a black frame); it was built, tested in game and replaced. What still holds from it:
the geometry, the palette findings, the script edits and the patcher rules.

**Two visual languages in one game.** Every menu screen - race selection `SHUMAN.GIF`, the lobby
`MULTIWIN.GIF` / `NET.GIF` / `SERVER.GIF`, story, victory, encyclopedia - is drawn as dark rounded
conduits on black (palette 65 = (35,35,35) bands with a light centre line 40 = (106,106,106), dark
gaps 67 = (11,11,11), warm corners 60), green LED read-outs (120..125) and neon-outlined buttons
(`KNOBE.SPR`). The battlefield alone was brushed metal: the frame `INTRFACE.GIF`, the 133-cell bank
`MAINBUT.SPR` (bevelled plates around the unit portraits, embossed order buttons, a chrome stopwatch
for PAUSED) and the 14 dialog plates `POPP.SPR`. The stock data even holds the artists' console
version of the HUD buttons: **`INTRFACE/BUTTON.SPR`**, 114 cells that no script and no exe string
references (checked against every `pictures`/`text`/`background` line and the DGROUP strings), index
for index the same buttons as `MAINBUT` 0..113 - black plates with the conduit ring and a hot-key
badge, neon line icons, a neon BUILD plate (76, 88×37) and the three tab strips (77..79, 124×16,
tabs 1 / 2 / 3 lit). `MAINBUT` re-used slots 3, 40, 50, 57, 70, 71 and 76 for other things (the bar
arrows, PAUSED), and Council Wars added 114..132; otherwise the two banks agree cell by cell.

**Palette.** A battle runs under the terrain palette (`DESERT.GIF` … `exp/jubjub.gif`), the menus
under `PALETTE.GIF`. Measured over all nine: 99 indices are bit-identical everywhere and **every
other index differs by at most 3 of 255** - the terrain palettes are re-quantisations of the same
palette - so a frame drawn in `PALETTE.GIF` indices reads the same on every map. One thing the
frame's palette does not decide: **the cyan ramp 128..143 is the team colour**. A cell drawn by a
`pushb`/`count` widget has that ramp remapped to the player's colour (the human player's `?` and
floppy came out red in the test), while `scount`/`in_text` glyphs drawn through the text path stay
cyan (the money digits). `BUTTON.SPR`'s own icons are cyan for that reason; the traced glyphs of this
tool follow it (observed in game; the remap itself is not traced in the code).

**`tools/hud_console.py`** (Pillow + `spr.py` + the geometry of `hud_layout.py`):

* `frame --width W --height H` - the HUD frame from geometry, no resampling: the view hole at (4,6)
  exactly index 254, borders `[dark, band, band, light]` from the screen edge to the hole, the panel
  column of 124 px with every screen where code or a widget paints at its stock place shifted like
  `hud_layout.shift` shifts the widgets (minimap interior 518..614 × 6..89, tab row 92..111, grid
  518..635 × 112..398, status 520..633 × 404..415, BUILD 516..601 × 422..448 with the lettering of
  `BUTTON` cell 76 centred, `DAYS` in the game's own `MFONTO7` glyphs recoloured green (cell index =
  `ord(ch) - 31`, cell 1 = the empty space), days box 609..634 × 433..444, money 521..598 × 456..472,
  a dial of radius 17 at (620,461) with twelve green ticks for the code-drawn hand), a vertical
  `DARK COLONY` in LED green beside the minimap, the free panel height between grid and bottom
  cluster as a conduit rack (vent rails, shelves, dim read-outs), the bottom bar with the two arrow
  boxes and the message screen 49..509+dx × 461..473, and the spare rows (§10.45) as a thin conduit
  through the bar's top band. 3.1 % opaque at 1920×1200 (stock frame 8.9 % at 640×480).
* `bank` - the 133-cell `INTRF_HD/MAINBUT.SPR`: cells 0, 1, 62, 63, 65, 66, 68, 69, 72, 73, 74, 75
  taken from `BUTTON.SPR` (`?`, `X`, stop, move, move & attack, waypoints, deploy turret, deploy
  mine, napalm, plague, deploy, steal money - each checked against `MAINE`'s `textmsg` for the cell);
  **the unit, building and upgrade portraits of `MAINBUT` kept pixel for pixel** on a plain console
  plate (the metal bevel and its 3-px rim go, the black behind the portrait and the coloured glow
  outlines of the upgrade cells stay - maintainer's second instruction); the metal-only glyph cells
  (crosshair 2, floppy 4, handshake 117, book 118, star 121, the Council Wars deploy variants 125/126,
  the clock 131, detected as "more than 40 % of the interior is mid grey") traced to neon lines
  (dark pixels → the bright shade, their metal neighbours → a dim glow); the four bar arrows 40 / 50
  / 57 / 76 as hollow triangles; the tab strips 77..79 from `BUTTON`; the 24×17 diplomacy plates
  119 / 120 / 123 / 124 / 127 ringed and their glyphs green (124 was a console plate already); the
  118×43 diplomacy strip 92 as four conduit boxes with cyan icons; PAUSED 132 as a 123×137 conduit
  panel with two pause bars and the word in 2× `MFONTO7`; digits 104..113, 99 and 128 unchanged.
* `popp` - `INTRF_HD/POPP.SPR`: the 304×16 rows as conduit sides (0 rounded top, 2 rounded bottom,
  3/5 with the list glyph, 3/4/5 with the inset window lines), the 112×24 title plate, OK / cancel
  with their green tick and red cross on a ringed black plate, 16×16 scroll arrows.
* `apply TARGET [--game SRC] --width W --height H [--no-bank]` - writes the frame, the two banks and
  the script edits into a game folder or an `hd_sets/<WxH>` fixture: `INTRF_HD/MAINE` `pictures
  intrface/mainbut` → `intrf_hd/mainbut` and the tab-strip pictures `picture 3..6` from 110×12 at
  x 521 to **124×16 at x 516 (+ W−640)** (the strip is the panel's full width, the cell is drawn
  whole); the four dialogs `LOPTE LQCE LSGE LOBJE` and the three `lopte` copies `pictures
  intrface/popp` → `intrf_hd/popp`. Idempotent.
* `preview` - frame + one widget group composited to a PNG, for a look without the game.

The stock `INTRFACE/MAINBUT.SPR` and `POPP.SPR` stay for the original exe (the §10.17 rule); the
patched exes reach the new banks through the script paths, no code change (the `pictures` line is
resolved like `background`: `intrf_hd/mainbut` opened the bank in the test). **640×480 builds keep
the metal HUD** - at that size the scripts are the stock `INTRFACE/` ones.

**Patcher.** `Edit-HudScript` applies the tab-strip rule (`$TAB_STRIP`), `Set-BackgroundHd` also
retargets `pictures intrface/(mainbut|popp)` and is now applied to the sub-window dialogs too (their
branch of `Write-InterfaceSet` had skipped it - found by the fixture comparison), `Write-MusicDialogs`
inherits the line through `INTRF_HD/LOPTE`; the two banks are `hd_data` entries of their own
(`INTRF_HD\MAINBUT.SPR`, `INTRF_HD\POPP.SPR`, shipped, not generated - a folder without them shows
the display fixes as `[RESOURCES NOT FOUND]`); the seven shipped `INTRF_HD\<WxH>\INTRFACE.GIF` are
`hud_console.py frame` output (1024×768, 1280×1024, 1280×720, 1280×800, 1920×1080, 1920×1200,
3840×1080). Verified on a clean `git archive HEAD` copy plus the new files: `-All -Resolution
1024x768` under pwsh 7 and `1920x1200` under PowerShell 5.1 give the same exes as before (data only:
Classic `bd86e63e…` / Ultimate `5acee874…`, 1920×1200 `9d2c60a5…` / `2dec194f…`) and sets identical
to the six `hd_sets` fixtures (55 of 57 files; `HSCENE`/`GSCENE` name the DC endings because the
clean copy has `AVI\DC*.AVI`, the fixtures were made without - known since §10.26). The 1024×768
fixture's `exp/intrf_hd/introe` had missed the 25 Sep menu lift and was refreshed from the repository.

**Game test** (Classic 1920×1200 build from the game folder, `drive.py`: DPI-aware `SendInput` and
`ImageGrab`; TRAINING → name → START TRAINING → NEXT → battle): the frame, the minimap inside its
screen, the tab strip with tab 1 lit, the unit portrait on its plate, `BUILD`, `DAYS 000`, the money
`1500`, the dial, the message line; the Game Option tab's `X` / floppy / `?` / clock / book cells;
the Options dialog on the new plates (title plate, four rows, tick and cross) and the Objectives
dialog; `error.log` empty. The cyan glyphs render in the team colour (above). Not run: Ultimate,
the other sizes (same code path), a battle with a selected unit's order buttons, the PAUSED cell in
game (previewed only), the pressed/greyed states of the new cells.

**Lesson:** before drawing a new look, list the interface banks nothing references - the artists had
already drawn this one.

#### 10.50 Every mode but 1920×1200 shrunk to two thirds in the top-left corner: the manifest must be PER-MONITOR DPI-aware **(28 Sep 2026, maintainer: "patcher is broken. all graphical modes (except 1920x1200) are showing picture in left upper corner"; `tools/patch_icon.py`, fix `icon`, both games; confirmed in game at 1024×768, 1280×800, 1920×1080 and 1920×1200)**

**Symptom.** With the 27 Sep builds (§10.44) the game switches the display to the chosen mode, but the picture occupies only the top-left two thirds of it: at 1024×768 a 683×512 menu, at 1280×800 854×533, at 1280×720 854×481; 1920×1200 and 1920×1080 fill the screen. The maintainer's game folder held the 1280×800 patcher builds (`c548da2b…` / `28b77afe…`) with a matching set - the patcher's output was exactly the reference, so nothing in the exe edits or the data was wrong; the same exe *without* the `icon` fix (no manifest, DPI-unaware) filled the 1024×768 screen.

**Cause.** `<dpiAware>true</dpiAware>` declares the process **system-DPI-aware**: it is laid out once for the DPI the desktop has when it starts (144 at 150 %), and whenever a monitor's DPI differs from that the desktop window manager bitmap-scales the process's windows by `monitor / system`. The monitor's DPI is not constant: Windows only allows the scale steps a resolution supports, so the mode switch changes it. Measured on the 1920×1200 panel at 150 % with `GetDpiForWindow` on the game window from a per-monitor-aware probe: **96 (100 %) at 1024×768, 1280×720 and 1280×800; 144 at 1920×1080 and 1920×1200** (the window created before the switch also shows it: 1280×800 = 1920×1200 × 96/144, 1600×1000 = × 120/144 at 1280×1024, 1920×1200 at the two 1920-wide modes). The game window and the emulated 16-bit surface the `DWM8And16BitMitigation` layer presents through it were therefore drawn at 96/144 = two thirds. 1920×1200 has no DPI change and 1920×1080 keeps 150 %, which is why the 27 Sep test (both at 1920 wide) saw nothing; 1280×1024 (125 %) came out full in this test, unexplained. Without a manifest the DWM scales the unaware window by `monitor / 96` instead, which is 1 at every switched mode (the 96-dpi modes) and 1.5 only where the mode keeps the desktop's DPI - the §10.44 symptom at 1920 wide. Neither form fits every mode.

**Fix.** The manifest declares the process **per-monitor DPI-aware**: `<dpiAwareness>PerMonitorV2, PerMonitor</dpiAwareness>` (Windows 10 1703+, V1 on 8.1) with `<dpiAware>true/pm</dpiAware>` as the fallback for older loaders (`patch_icon.MANIFEST`, 589 bytes, was 463; the 512-byte alignment of the appended section absorbs it, the exes keep their sizes 743 424 / 743 936). A per-monitor-aware process is responsible for every monitor's DPI itself and is never scaled by the DWM; the game asks nothing about DPI and receives `WM_DPICHANGED` messages it never reads (it has no message loop), so the W×H frame fills the W×H mode. `patch_icon.py verify` now distinguishes the three forms (no manifest / system-DPI-aware 27 Sep form / per-monitor). Generator text and player docs updated, `Apply-DarkColonyPatches.ps1` regenerated: **every game build's reference hash changed** - 1024×768 Classic `928bb8d0…` (was `88f59c1d…`), Ultimate `98fe5e95…` (was `63ccbe88…`); 640×480 `ebf2c957…` / `a7c49e19…`; 1280×720 `1990169b…` / `4ea1487c…`; 1280×800 `bc4c0537…` / `e7392a4d…`; 1280×1024 `0a900523…` / `4c30c247…`; 1920×1080 `9d875fa0…` / `f1260918…`; 1920×1200 `7abeb48b…` / `58265655…`; 3840×1080 `8556fea1…` / `383cccad…`; map editor `c72dd205…` unchanged (no manifest). PowerShell 7 = 5.1 on the Classic 1024×768 build. The two 1024×768 exes staged from the scratch build; the maintainer's game folder rebuilt at 1280×800 with the regenerated patcher (`-All -Resolution 1280x800 -Overwrite`, byte-identical to the references, set unchanged); `dc16.asm` / `dcexp16.asm` regenerated.

**Game tests** (scratch copy on `subst X:`, per-path mitigation layer set for every test exe, `probe.py`: `EnumDisplaySettings` + game window rect + `GetDpiForWindow` every second, DPI-aware screenshots, real `SendInput` click):

| build | 1024×768 | 1280×720 | 1280×800 | 1280×1024 | 1920×1080 | 1920×1200 |
|---|---|---|---|---|---|---|
| 27 Sep form (system-aware) | 683×512 | 854×481 | 854×533 | full | full | full (§10.44) |
| no manifest (unaware) | full | - | full | - | full | 1.5× cropped (§10.44) |
| **per-monitor V2** | **full**, click OK | - | **full**, click OK (also Ultimate) | - | **full**, click OK | **full**, click OK |

"click OK" = TRAINING / ACADEMY clicked at the script's position opened the name screen (the 640×480 box centred at the letterbox offset). The final patcher outputs were run too: Classic 1024×768 from the scratch copy (intro plays letterboxed 1:1, SPACE, menu, TRAINING → name screen) and the game folder's Ultimate 1280×800 (menu, ACADEMY → name screen); `error.log` empty everywhere; the display back at 1920×1200 after every kill. Not run: 640×480 (no `resolution` fix, the same manifest), 1280×720 / 1280×1024 / 3840×1080 with the new manifest, a battle.

**Lessons.** (1) "DPI-aware" has three levels; for a full-screen program that switches modes only per-monitor awareness is safe, because the mode switch changes the monitor's DPI. (2) A display test at the desktop's own size proves nothing about the other modes - test at least one mode whose scale step differs (here 1024×768). (3) `GetDpiForWindow` on the game window from a per-monitor-aware probe shows the effective DPI the DWM applies; the window rect before the switch reveals the factor.


#### 10.51 ONLINE WAR: a room browser in the main menu, TLS to the relay, code in an appended section **(29 Sep 2026, maintainer: "first button on the right column add ONLINE WAR, it must lead to the form of room selection based on LOAD GAME form and it must contain maps from relay server ... Connection must happen to 8889 port which must use standard ssh encryption [-> TLS]. When map is selected and entering the room, relay must select free slot for the client"; fix `online` = `tools/patch_online.py` + `tools/online/online.c`; Dark Colony Ultimate only; server side in `RELAY_SERVER_PLAN.md` §20, messages in `DC16_NETWORK_PROTOCOL.md` §4.4 / §6.9)**

**Menu.** The seven-row block (§10.36) gets an eleventh button: right column row 1 **ONLINE WAR** (id 8, plate gadget 23, `textmsg 11`), row 2 empty (reserved for a replay button), **MULTI PLAYER WAR** (3) on row 3, **ENCYCLOPEDIA** (5) on row 4, QUIT (12) on row 7; `build_ozi_overlay.menu_layout` (`OZI_COLUMNS`) and the patcher's `Edit-OziMenu` place it, the wave order stays column by column (11 `banim` pairs). The menu's accepted-id filter `cmp edx,7` at `0x404F9E` (file `0x439E`; 5 in the stock exe, 7 since fix `ozi`) becomes `cmp edx,8`, and the seven NOP bytes at the end of the id chain (`0x405136..0x40513C`, left by fix `ozi`'s handlers; the id-7 branch falls through them into the loop head `0x40513D`) become `jmp .dccode` + 2 NOP. Those two edits are the only bytes that change in `AUTO`.

**Code section.** The module is C (`tools/online/online.c`, MSVC x86, no CRT, `/O1 /GS- /Zl`, built by `tools/online/build.cmd` into a relocatable DLL whose single `.text` section - `.rdata`/`.data`/`.bss` merged - `patch_online.py` rebases to the section's VA and carries as Base64), appended as section **`.dccode`** after `.dcicon` (file end rounded to `FileAlignment`, VA = `SizeOfImage`; characteristics code + read + write + execute; four header edits like fix `icon`; `Requires ozi, icon`, last in the Ultimate build). The dispatch at the section start is the id-8 handler: `cmp edi,8; jne 0x40513D; push edx (gs); push eax (ui); call online_war; add esp,8; jmp 0x40513D`. Game functions are called through naked register-convention thunks (Watcom: eax, edx, ebx, ecx; esi/edi/ebp preserved by the callee; `0x40122C` pops its stack argument itself). The only imports used through the exe's IAT are `LoadLibraryA` (`0x4804B0`) and `GetProcAddress` (`0x480480`); `ws2_32.dll`, `secur32.dll` and `kernel32.dll` functions are resolved at run time.

**What the module does** (details in plan §20.5): Dark Colony prefix mode + music ALL like MULTI PLAYER WAR; `DEFAULT_SERVER.TXT` (C/C++ comments, `host[:port]`, optional `plain`); TCP connect; Schannel TLS client with automatic certificate validation for the host name; the ONLINE WAR screen (`intrf_hd/onlin` + `e` = `INTRF_HD/ONLINE`, or `INTRFACE/ONLINE` when the HD copy does not exist - the module probes the file, so one exe serves every resolution) on the game's own interface engine, filled from `0x51 ROOMS`; `0x52 ENTER` -> `0x54 ENTERING`; then a loopback proxy thread and the stock network entry `0x40122C` with `127.0.0.1:<ephemeral port>`, so the game's own lobby and battle code run unchanged over the encrypted connection.

**Data.** `DEFAULT_SERVER.TXT` beside the exe (repo content; the patcher writes it only when absent) and the screen script `ONLINE`, derived from `LOADGE`: list widened from 392 to 448 px (56 -> 64 monospace columns; `MFONTO5` is 7 px per glyph, measured: A, W, i and 1 are all 7 px wide), scroll bar / UP / DOWN shifted right by 56 px, a read-only header `in_text` above the list and a status `in_text` below it, title "Online War", buttons ENTER / BACK, the save-mode widgets removed. Written by `patch_online.py` and by the patcher (`Write-OnlineScreen`, HD from the set's `INTRF_HD\LOADGE`, 640x480 from `INTRFACE\LOADGE`).

**Built and confirmed in game (29 Sep 2026).** Published Ultimate 1024x768 **`d55b1689…`** (756 224 bytes = the previous `98fe5e95…` + this fix; the patcher's `-All -Resolution 1024x768` under PowerShell 7 and the tool chain give the same bytes; the first published form `5b9efed6…` of the same day had the status line without the server line, see the screen paragraph below), 640x480 `ba41dc2c…` (PowerShell 5.1); Dark Colony `928bb8d0…` and the map editor `c72dd205…` unchanged. The section `.dccode` sits at VA `0x557000` (file `0xB5A00`), 11 650 bytes of code and data (0x2E00 raw), 292 rebased operands, `online_dispatch` at `+0x2D02`, `online_war` at `+0x2AC9`; the two `AUTO` edits are file `0x43A0` `07 -> 08` and `0x4536` `90×7 -> E9 <rel32> 90 90` (jmp `0x559D02`). `dcexp16.asm` regenerated from the new exe. The patcher's generator takes this tool's plan on the exe as the previous steps left it (`PLAN_ON_PATCHED`), because the tool refuses an original without `ozi` + `icon`; appending fixes are handled generically now (`blocks_appending`, `APPENDS`).

**Game tests** (scratch copy of the game folder on `subst X:`, `DWM8And16BitMitigation` layer for the path, DPI-aware `SendInput` driver `online_test.py` + `capture.py`; **every click coordinate must come from `EnumDisplaySettings`, not `GetSystemMetrics` - a process started before the game's mode switch keeps the desktop's 1920x1200 metrics and its absolute mouse coordinates land at a third of the intended point at 1024x768**; two test runs were lost to that): (1) local relay, `127.0.0.1 plain`: menu → ONLINE WAR → the screen with the seven rooms → row 1 → ENTER → `ENTERING slot 5` → the stock lobby with Mercenary, Marauder and "Player5" in the relay-chosen slot → READY → countdown → the battle runs through the proxy (relay `STARTING`/`RUNNING`, sync frames flowing, `error.log` empty). (2) Local relay with a self-signed certificate on `TLS_PORT=8891`: the module refuses it - status line `TLS handshake failed 0x80090325 (certificate not trusted)`, BACK returns to the menu. (3) The **live Fly relay with the shipped `DEFAULT_SERVER.TXT`**: `openssl s_client` shows TLS 1.3 with the Let's Encrypt `*.fly.dev` certificate, `fakeclient.js --online --tls` joins, and the game entered room 1 as slot 1 over TLS (lobby with Mercenary). Not run: 640x480 in the game, the other HD sizes, a full online battle on Fly with a second human.

**Mistakes on the way, kept for the next fix of this kind.** (1) Widget ids are ONE object space for every kind: `pushb 8` collided with `gadget 8` (LOAD CW GAME's plate) and the menu asserted at load (`widget.c` line 377) - the plate moved to 24 (`OZI_RENUM {6: 19, 7: 20, 8: 24}`), the check accepts either id of each pair. (2) `MFONTO5` advances **8 px per column** (7 px glyph + 1), so the 448 px list holds 56 columns, not 64; the row format is `name 18 · terrain 8 · seats 5 · players 7 · bots 4 · status 9`. (3) The network entry's address record is `{u16 port; u16 pad; char* host}` - the host pointer at +4 (the protocol doc said `u32 pad`; corrected). (4) With `ecx = 0` the network entry `0x40122C` builds the in-process MAILBOX network (`0x40BFB0`, the host's own connection) and the game connects to itself; the stock CONNECT handler passes `get_tcp_network()` = **`0x42E024`** in `ecx`, and so does the module now. (5) The decoration gadget 11 (CHOA) repaints its rect every frame and covered the header line's right end - dropped from the ONLINE script. (6) MSVC inline asm: a semicolon starts a COMMENT, one instruction per line; a merged `.bss` is materialised as raw zeros in the DLL (86 KB), so the module allocates its buffers with `VirtualAlloc` at run time (the section is 11 KB). (7) The relay must restart the join clocks at `ENTERING` (`firstMessageAt`, `joinedAt`): the module's LIST had already spent the grace and the game's own connection was evicted 3 s after joining.

**Screen, second round (same day; maintainer: "'online war' header text must be correctly positioned. server name must be mentioned as a first line of two right above mentioned TLS connection" → "bottom text area (server name etc.) must be wrapped into a gray frame as it is done on the other forms").** The title label's stock box (`label 6`, x 252 w 200 at 1024x768) is left of the rounded title panel of LOADER.GIF, which spans list x − 50 .. + 268 (measured 262..580): the label now has that box (`TITLE_X_OFF -50`, `TITLE_W 318`) and the text is centred in the panel. Below the list two read-only `in_text` lines: **`in_text 31` "Server: host:port"** at list bottom + 8 and **`in_text 17` the state** ("Connecting (TLS)...", "Connected (TLS). Select a room and press ENTER.", refusals, errors) at + 24; the module writes the server line once (or "Server: none (see DEFAULT_SERVER.TXT)"). Both sit in a **grey frame drawn into the background**: the screen's `background` line points at **`ONLINEBG.GIF`** = `LOADER.GIF` with a 3-px tube in the console greys of the lobby's boxes (palette greys 35 / 106 / 35, a 2-px black gap, black inside, one pixel off each corner; `patch_online.online_background`, Pillow, saved like `pad_background.py` so the patcher's C# codec - `Write-OnlineScreen`, which now calls `Initialize-GifCodec` because at 640x480 no interface set compiles it - writes the same bytes) around list x − 6 .. + 454, list bottom + 2 .. + 46 - **then (third round, maintainer: "headers section and maps section must be wrapped into a gray frame as it is done on the other forms. scroll bar must be wrapped as it is done on the other forms") three frames of that style (`patch_online.frame_rects`): header + list (x − 6 .. + 454, y list top − 22 .. list bottom + 3; the header `in_text 30` moved to list top − 16 inside it), the scroll channel (UP button x − 4 .. + 30, list top .. list bottom + 2, holding UP, the bar and DOWN) and the text lines (list bottom + 8 .. + 52, server line at + 14, state line at + 30).** PowerShell pitfall met again: inside `@( , )` the comma binds before `+`/`-`, so every rectangle coordinate is parenthesised (5.1 threw `op_Subtraction`, 7 silently drew another picture). The two animated decorations that repainted over those rows every frame, `gadget 11` (CHOA, top right, over the header) and `gadget 14` (ENCF, under the list, over the status line), are dropped from the script; the background art under them stays. `INTRF_HD/ONLINE` + `ONLINEBG.GIF` are repo content at 1024x768 (the generator's `hd_data` skips them, they have no stock source), the 640x480 `INTRFACE/ONLINE` + `ONLINEBG.GIF` are patcher-written copies (`.gitignore`), the six `hd_sets` fixtures carry both. Confirmed in game against Fly (capture: title centred, "Server: dark-colony-server.fly.dev:8889" over "Connected (TLS). Select a room and press ENTER." inside the frame).

**Screen, third round (3 Oct 2026; maintainer: '"DEFAULT_SERVER.TXT" upgrade. it must contain field "name=https://github.com/endotermic/Dark-Colony-Server" and "address=dark-colony-server.fly.dev". add a line with "Name:" above the "Server:" line on the "ONNLINE WAR" and "REPLAY ONLINE GAME" forms'; plan §16 + §20.2).** `DEFAULT_SERVER.TXT` is `name=` / `address=` lines (the bare address of the first form still read; `//` is a comment only at a line start or after white space, so the `https://` of the name survives), and both screens carry a third read-only line under the list: **`in_text 49` "Name: <name>" at B+14, "Server: host:port" (31) at B+30, the state line (17) at B+46**, the text frame of `ONLINEBG.GIF` / `REPLAYBG.GIF` B+8 .. B+68 (`TEXT_BOTTOM_DY`; the ENTER / BACK plates begin at B+87, at 640x480 the frame ends at 428 and the CHOB animation plate starts at 436, so every size has room). `online_script` / `Edit-OnlineScript` write the three lines (the replay script passes them through), `frame_rects` grows the frame, the module sets widget 49 only when the loaded script has it (`ip->objects[49].type == 4` - the engine's `set_text 0x423ED4` asserts on a missing widget, and a screen written by a pre-3 Oct patcher has no line 49) and clips both lines to 56 columns ("Name: " + the 48-character project URL = 54). Patcher 1.2; published Ultimate 1024x768 dark `a309a2f5…`; fixtures of the six sizes regenerated; the four stock 640x480 copies are patcher-written as before. **In game (3 Oct 2026, 1024x768 dark from a `subst X:` clean copy against a local plain relay, `DEFAULT_SERVER.TXT` = `name=Local test relay (plain) // trailing comment`, `address=127.0.0.1`, `plain`):** ONLINE WAR and REPLAY ONLINE GAME both show "Name: Local test relay (plain)" over "Server: 127.0.0.1:8888" over the state line inside the frame, the comment stripped from the name (`ONLINE.LOG`: `config: name Local test relay (plain)`), both connected, BACK returned to the menu, `error.log` empty. Rig lesson repeated: a driver that imports `drive.py` before the game's mode switch keeps the desktop's screen size - re-read it after the switch (`drive.SW, drive.SH = drive._screen()`), or every click lands at 1024/1920 of the intended point.

**"Connection lost" after ENTER about every other time - the hand-over race (29 Sep 2026, evening; maintainer: "when using 'online war' connecting to a selected lobby, often I have 'connection lost'"; plan F81 + §16, protocol doc §4.4 / §6.9).** The Fly log named it: `client left ... reason: "sequence 12, expected 0"` 60 ms after `online -> room`. `room_screen`'s loop kept sending the 700 ms keep-alive after `ENTER` until `poll_relay` had read `ENTERING` - one relay round trip (200-400 ms to Fly) - while the relay had already reset its counter for the game's stream at `ENTER`, so a keep-alive in flight was the "game's first frame" with the module's sequence number. Fix in `online.c`: an `entering` flag set when `ENTER` was sent stops the keep-alives and swallows a second ENTER click; `REFUSED` (ev 2) clears it. Rebuilt with `build.cmd --embed` (11 810 bytes of code and data, section size unchanged, the tool's Base64 re-embedded), patcher regenerated: **Ultimate 1024x768 `d970597f…`** (was `d55b1689…`; pwsh 7 = PowerShell 5.1 = generator reference), Dark Colony and the editor unchanged, `dcexp16.asm` regenerated. The relay drops module-only frames that arrive after `ENTERING` until the game's first frame (`client.handover`), so the exes built before this fix join as well once that relay is deployed. Proven with a fake relay (`fakerelay.py` in the 29 Sep evening scratchpad) that holds `ENTERING` for 3 s: old exe 4 keep-alives after `ENTER`, rebuilt exe none; then the rebuilt exe joined room 1 of the new local relay and sat 18 s in its lobby (`online_test.py`, `subst X:`; the layer entries and the mapping removed afterwards). Committed and pushed 29 Sep 2026 (Dark-Colony `4c85153`, Server `5e2eaeb`), relay deployed to Fly the same evening; **confirmed by the maintainer on Fly with the rebuilt exe ("tested on fly, works now")**.

**Debugging aid.** The module appends one line per step to **`ONLINE.LOG`** beside the exe (pressed, config, screen, connected / handshake error, ROOMS count, REFUSED, ENTERING slot, loopback port, proxy connected, network entry returned) - the first place to look when ONLINE WAR "does nothing".

#### 10.52 "Music is lagging periodically": the Dark Colony disc image carries stray read-ahead blocks, the player is fine **(29 Sep 2026, maintainer: "music is lagging periodically. something wrong with playing music", then "i see where is the problem. music rip is in very bad condition! redo rip and validate that it has no glitches!"; new tool `tools/rip_music.py`; data only, no exe byte; the Council Wars set re-ripped clean, the Dark Colony set re-ripped with the junk trimmed but its image cannot be repaired - the disc has to be ripped again)**

**The player was cleared first.** A Python replica of the module's MCI calls (`open … type mpegvideo`, `setaudio volume`, `play`, then `status mode` every 5 s like the game's poll at `0x004320DB`) was recorded for 40 s through a WASAPI loopback capture of the speaker endpoint (`loopcap.py` in the 29 Sep scratchpad: shared-mode `AUDCLNT_STREAMFLAGS_LOOPBACK`, 5-ms RMS timeline, device-position jumps and `DATA_DISCONTINUITY` flags): no silent run of 20 ms or longer, every status poll answered in 0.1 ms, only three sub-millisecond position jumps (2, 2 and 36 frames - the capture's own scheduling). Nothing in the module touches MCI per frame (§10.31 / §10.41). So the periodic "lag" had to be in the files.

**The files are faithful copies of the images - and the Dark Colony image is damaged.** Decoding the eight shipped MP3s (`miniaudio`) and aligning them with the PCM sliced from the `.bin` images at the §10.31 sector ranges gives the usual MP3 coding noise everywhere (SNR 17-30 dB overall, decoder delay 1105 frames, no 100-ms window near the signal level): the 22 Sep encode did what it should. The PCM itself, scanned for byte-identical sectors within each track, shows in **every Dark Colony track runs of 3-13 sectors (mostly 8-12 = 100-160 ms) that are exact copies of the audio exactly 92 sectors (1.23 s) later**: track 2 127 runs = 15.9 s = 8.3 % of the track, track 3 50 runs = 6.1 s = 8.1 %, track 4 83 runs = 10.2 s = 7.8 %, track 5 132 runs = 16.3 s = 8.5 %; the runs sit 1-3 s apart. The *early* copy is the stray one (its two junctions break the waveform by 3-17x the local sample-to-sample roughness, the late copy's junctions by 0.2-0.3x = seamless), so the ripping drive answered the read of those sectors with its read-ahead cache from 92 sectors further on; **the audio that belonged there is not in the image** (deleting the stray run does not restore continuity either, so it is an overwrite, not an insertion). That is what the maintainer heard: every second or two the music jumps 1.2 s ahead for a tenth of a second and back. The same defect shows up as a fixed-phase discontinuity (sample 57/58 of the sector, the chunk boundary of the ripping drive) in ~70 sectors per track while a random phase has 1-3. The **Council Wars image is clean**: no duplicated sector in any track, no fixed-phase excess (track 3's worst phase has 81 sectors above 8x their own mean against a 99th percentile of 71 over the other phases - dense music, the same on every phase). Two more defects of the old slicing, both audible at every loop start: **Dark Colony track 2 began with 50 ms of full-scale white noise** (pregap garbage) followed by 215 ms of digital silence, Dark Colony track 3 with a 180 ms audio fragment and 300 ms of silence, Council Wars track 2 with a full-scale click and 40 ms of silence.

**`tools/rip_music.py`** (numpy; `rip` needs `lameenc`, the decoded check `miniaudio`) replaces the 22 Sep scratch script: `scan IMAGE.bin` finds the audio range (binary search for the first sector without the 12-byte sync), the tracks (runs of digital silence, both channels below 3, of at least 150 sectors) and prints the glitch report per track - trim points, digital silence inside the music, **STRAY BLOCKS** (byte-identical sector runs of 3 or more, their distance, seconds and percentage lost), **SPLICES** (a sector phase whose count of sectors with an 8x error exceeds twice the 99th percentile of the other phases); `rip IMAGE.bin OUTDIR --names T` slices, trims and encodes (LAME CBR 192, quality 2, 44.1 kHz stereo) and verifies every file by decoding it against its PCM (alignment, SNR, worst loud 100-ms window; exit code 1 when a track has stray blocks or splices or the decode is off); `check FILE.mp3 …` walks the frame stream (sync integrity, bitrate) and scans the decoded audio with the same report. **Trimming rule:** a track starts where the last run of digital silence (>= 20 ms) inside its first second ends - that drops the pregap junk and the pressing's lead-in silence - and ends where the trailing silence begins; the four Dark Colony tracks now begin at 0.258 / 0.481 / 0.000 / 0.000 s of the old slices, the Council Wars ones at 0.045 / 0 / 0 / 0 s, and the tails lose 1-12 ms of silence. Track ranges found by the tool: Dark Colony `238170..252470`, `252765..258449`, `258849..268630`, `269090..283446`; Council Wars `251636..262762`, `263064..279372`, `279676..293800`, `294103..309529` (§10.31's table, within two sectors).

**Shipped now** (game folder, `MUSIC\TRACK02-05.MP3` 4 571 010 / 1 808 091 / 3 130 932 / 4 594 834 bytes, `exp\music\track02-05.mp3` 3 560 385 / 5 219 892 / 4 520 855 / 4 937 142 bytes; `track04.mp3` is byte-identical to the 22 Sep file, the others differ by the trims; LAME is deterministic - two runs of the tool give the same bytes): the **Council Wars set passes every check** (no stray blocks, no splices, no silence inside the music, decoded SNR 17-24 dB), the **Dark Colony set is only as good as its image** - the noise burst and the fragment at the track starts are gone, the stray blocks stay (`rip` exits 1 for that image and says so). The patcher's `music` fix lists the files by name only (`music_data`), so it needs no regeneration. **What fixes Dark Colony for good:** rip the disc again with a ripper that defeats the drive cache and corrects jitter - Exact Audio Copy in secure mode (test & copy, per-track WAV or a BIN/CUE image) or `cdrdao read-cd --paranoia-mode 3` / cdparanoia - then `rip_music.py scan` must print `stray blocks: none` and `splices: none` for every track before `rip` writes the set; `Dark Colony.bin` (dated 14 Sep 2026) was evidently made by a tool without either. If only WAVs come back, they can be encoded with the same LAME settings (`encode()` in the tool); a WAV per track skips the trimming question because a secure ripper writes the pregap as silence.

**The Council Wars material is hard-clipped.** Tracks 2 and 4 of that image have 5.9 % and 3.5 % of their samples flat on a ceiling of exactly 23196 = -3.0 dBFS (plateaus up to 63 samples, both channels, in every second of track 2), tracks 3 and 5 0.1-0.2 %; the Dark Colony tracks stay below 0.15 %. A ceiling at -3 dB with flat plateaus is a source that was clipped at full scale and attenuated afterwards - it is in the audio as the image has it, the rip cannot add or remove it, and `rip_music.py` now prints a `CLIPPED MATERIAL` line for it. Whether the pressed disc sounds the same or the image came from a processed source can only be told with the physical disc. Also seen on the way: a plain gain fit between a loopback recording of the speakers and the file gives 8.6 dB (Council Wars 3) and 1.8 dB (Dark Colony 5) - the endpoint's audio enhancements (Realtek APOs on "Speakers") reshape the signal, so loopback fidelity checks on this PC only prove time continuity (local alignment stayed at 0 samples over 24 s), not waveform equality.

**Shipped since the same evening: the maintainer's own download, copied byte for byte** (maintainer: "i downloaded all soundtracks into `Documents\DC - soundtracks`" -> "do not encode. just copy these tracks where appropriate!"). Eight MP3 files (YouTube uploads of both soundtracks, MPEG-1 Layer III VBR, 48 kHz, 1.0-3.8 MB each, peaks -5..0 dBFS, no clipped material, 0.2-1.2 s lead-in and 1-7 s tail of silence) were matched to the CD tracks by cross-correlation against the image PCM (`ytmatch.py` in the 29 Sep evening scratchpad; correlations 0.84-0.98, speed ratio 1.00000): the uploader's "Track 1-4" are CD tracks **2, 3, 5, 4** of Dark Colony, "Track 5-8" are CD tracks **4, 2, 3, 5** of Council Wars. Copied under the game names - `MUSIC\TRACK02.MP3` = Track 1, `TRACK03` = Track 2, `TRACK04` = Track 4, `TRACK05` = Track 3; `exp\music	rack02.mp3` = Track 6, `track03` = Track 7, `track04` = Track 5, `track05` = Track 8 - without re-encoding (the maintainer's decision; MP3-to-MP3 would only lose quality). The frame streams are intact (`rip_music.py check`: 0 bytes outside frames). The exe reads the files through MCI `mpegvideo`, which decodes VBR and 48 kHz without a change; **confirmed by the maintainer in the game the same evening ("music works now")**. The Council Wars downloads show no -3 dB ceiling, so the clipping above is a property of that image, not of the pressing. The re-ripped image sets of the same evening were in the game folder for about an hour and are superseded; `rip_music.py` stays for the day a secure disc rip arrives.

**Lessons.** (1) When a periodic audio defect is reported, measure the source before the player: aligning the encoded files with the raw PCM decides in one pass whether the encoder or the material is at fault. (2) A raw `.bin` is not a validated audio rip - duplicated-sector scans (exact twins at a constant distance) and a fixed-phase discontinuity test find cache-and-jitter damage that no waveform statistic can separate from musical transients. (3) Musical transients defeat "click" detectors that compare a sector's worst jump against the median phase; only the distribution over all phases (a spike vs. the 99th percentile) tells a rip splice from dense music.


#### 10.53 Battlefield dialogs: grey frames around every text box, a framed scroll channel, and the interface style guide **(30 Sep 2026, maintainer: "battlefield skin of forms are not good. text boxes must have gray frame. scroll bar must have appropriate frame. additionally write a clear and descriptive guidelines for drawing an interface elements"; `tools/hud_console.py` (`console_dialog`, cells 14 / 15 of `INTRF_HD/POPP.SPR`), `tools/patch_music.py`, the patcher's `Edit-DialogConsole`; new `docs/DC16_INTERFACE_STYLE_GUIDE.md`; data only, no exe byte; confirmed in game at 1024x768: all four dialogs, `error.log` empty)**

**What was wrong.** The §10.49 dialog rows put the read-outs of the options dialog (100%, MEDIUM,
HIGH) straight on the pipework - the `in_text` widget paints a bare black rectangle the size of its
text - the save-name field and the list windows had a single 1-px light line for a frame, and the
scroll bar (the engine's own red track, 10 px) ran over the pipework of the right-hand band with
nothing around UP, bar and DOWN. The lobby (MULTIWIN.GIF, measured: `67 65 40 65 67` around every
black box = 11 | 35 | 107 | 35 | 11) and the ONLINE screen's frames (§10.51, third round) had
already fixed what a frame is; the dialogs had not received it.

**The rule** (now `DC16_INTERFACE_STYLE_GUIDE.md` §3, §6-§8): every read-out, entry field, list and
scroll bar sits in a black box wrapped in the lobby tube - `11 | 35 | 107 | 35` from the outside in,
then black, the 35 ring's corner pixel off - two frames 2 px of ground apart, the panel border the
same tube seen from outside (`35 | 107 | 35 | 11`). A single-line text box is 24 px tall
(4 + 16 + 4), a scroll channel is an 18-px black column holding the 16x16 UP / DOWN plates with
1 px of black around them and the 10-px bar between.

**Plates** (`build_popp`; stock 14 cells, now 16): rows 0 / 1 / 2 = border + pipework (the border
was a light edge line with a 4-px band and is now the tube), rows **3 / 4 / 5** = the list window in
its frame at x 6..269 (black 10..265) and the **scroll channel** in its frame at x 272..297 (black
276..293), ground between border, frames and border - exactly 304 px (4 + 2 + 4 + 256 + 4 + 2 + 4 +
18 + 4 + 2 + 4); the frame tops on rows 0..3 of cell 3, the bottoms on rows 12..15 of cell 5. New
cells **14** = value box 78x24 and **15** = name box 264x24 (`text_box`), framed black. Everything
else (title plate 6, OK 7, cancel 8, arrows 10..13) unchanged.

**Scripts** (`console_dialog`, position-derived and idempotent; the patcher's `Edit-DialogConsole`
= the same text byte for byte under pwsh 7 and 5.1, tested on all five inputs and on both orders of
the music edit): the `list` moves to x row + 10, y top row + 4, height to the bottom row + 11
(LOBJE 314/292/168, LSGE 314/292/216 at 1024x768; before 324/291/169 and 217); UP / DOWN (pushb
cells 10 / 11 bound to the list) to x row + 277, y top row + 5 / bottom row - 5; the `scroll` bar to
x row + 280, y top row + 21, height bottom - top - 26. Every "-" / "+" pair (pushb cells 12 / 13)
gets a **value box picture** (cell 14) at ("-" x + 16, "-" y - 4) with its `in_text` at
("-" x + 23, "-" y + 3) - LOPTE `picture 23..26` at x 480, y 332 / 364 / 396 / 428, the Ultimate
copies with the MUSIC row `23..27` (460 for GAME DETAIL); the save-name `in_text 54` of LSGE turns
its two rows (cells 3 / 5, a list window without a list) into plain rows and gets the **name box**
`picture 25` at (310, 244) with the field at (318, 250). Box pictures are regenerated on every pass
(old ones dropped), numbered with the lowest free widget ids from 23 in y order, inserted as one
block after the last picture line; ids are one object space for every widget kind (§10.51's
`gadget 8` assert), so 23.. was chosen above the rows and the plates. The 640x480 dialogs (stock
`intrface/popp`) are untouched: the pass acts only on scripts naming `intrf_hd/popp`.

**Order independence.** `patch_music.write_dialogs` now runs `console_dialog` after `music_row`,
and the patcher's `Write-MusicDialogs` runs `Edit-DialogConsole` after `Edit-MusicDialog`
(`Write-InterfaceSet`'s dialog branch runs it after the letterbox shift); boxes-then-music-then-pass
= music-then-pass, checked. The three `exp/ dc/ ozi_ns/ intrf_hd/lopte` copies in the game folder
are that output.

**Regenerated:** `INTRF_HD/POPP.SPR` (the one shared copy, repo content), `INTRF_HD/LOPTE LSGE
LOBJE` + the three `lopte` copies at 1024x768 in the game folder (repo content), the six `hd_sets`
fixtures (only the three dialogs changed; `INTRFACE.GIF`, `MAINBUT.SPR`, `CLOCK.SPR` byte-identical
before and after), the patcher (1 523 442 bytes). Clean-copy check (`-All -Resolution 1024x768` and
`1920x1200 -IgnoreMissingData`, pwsh 7 and 5.1): dialogs = fixtures, copies = tool chain, POPP = the
shipped bank, exe hashes at the published references (Classic `928bb8d0…`, Ultimate `d970597f…`).
The game folder's checkout differs from the fixtures only in CRLF (autocrlf; the committed blobs are
LF-only) - compare with CR stripped.

**Confirmed in game (Classic build, 1024x768, training mission 1, DPI-aware `SendInput` rig from
the game folder):** F11 = save dialog (name box framed, cursor inside, list frame, channel with UP,
red bar, DOWN), Game Option tab: Options (four framed value boxes with 100% / 5 / 5 / HIGH centred,
"-" / "+" 2 px off the boxes), Objectives (text in the framed list, the red thumb in the channel),
Quit (unchanged apart from the border tube); `error.log` empty. Not run: the Ultimate copy with the
MUSIC row, other sizes (the pass is position-derived, the fixtures were regenerated for all six).
The `pushb` step buttons still draw their 16x16 arrow cell as a triangle without a visible plate,
as before this change.

**Rig notes.** `drive.py` (28 Sep scratchpad, now with `EnumDisplaySettingsW` for the screen size -
a driver started before the mode switch reads the desktop's size) from the game folder itself, no
`subst`: TRAINING (421,545) at the Classic 1024x768 menu, name, START TRAINING (680,506), NEXT
(780,606), TO BATTLE (691,603); Game Option tab (1002,102), its cells Quit (931,131), Options
(931,213), Objectives (931,336); dialog cancel / OK plates at their script rects + 16. A posted ESC
at the menu did not quit (known), the process was terminated.


#### 10.54 The battlefield OPTIONS dialog as a pre-battle form: one panel, plain labels, lobby steppers, text buttons **(30 Sep 2026, maintainer: "refactor style of battlefield 'options' menu using best practices taken from pre battle menus"; `tools/hud_console.py` (`console_dialog` → `_options_layout` / `_options_header`, POPP cells 16..22), the patcher's `Edit-DialogConsole`; guide §4, §6, §7, §9 amended; data only, no exe byte; confirmed in game at 1024x768: layout, hover brightness, a value step, CANCEL and OK)**

**What the pre-battle menus do** (measured on `MULTIE`, `LOADGE`, `NEWGAMEE`, `GETSVRE`, `NETOPTE`):
option rows are a plain `label ... align left ... remap 4` (MFONTO5, green) with the value between
KNOBE's ringed 14x14 arrows - `pushb ... 14 14 -11 14` left, `-11 16` right - and the `in_text`
`align centre` between them (the lobby's slot rows, x 409 / 473); actions are 90x26 `MEDBUTTON`
plates (KNOBE cell 2) with `label centre <msg> 0 - remap 0` captions, the way back left and the
action right (LOADGE: BACK 313, LOAD 403); every screen sets `bright_pushed 8` / `bright_highlight 4`
(the plate brightens under the pointer and while pressed, no second cell); titles are large-font
labels (`font 1 intrface/mfonto2`, 21-px caps, "Load Game") centred over the screen. The §10.49
dialog had none of that: labels on red plates (read as buttons), tick / cross icon plates, MFONTO7
everywhere, no hover feedback, and since the morning a framed box per value (§10.53).

**The form.** Rows 3..last of the dialog are **one framed panel** (POPP cells **16 / 17 / 18** =
panel top / middle / bottom: the tube at x 6..297, black 10..293, no channel; the bottom row holds
the panel's bottom tube on rows 4..7, ground on 8..11 and the dialog border on 12..15, so
`music_row`'s "clone row 14, move row 15" still grows the panel), the title `OPTIONS` is a `font 1`
label centred in a **header box** (cell **19**, 292x32 at x 6, y0 + 14: 2 px of ground above the
panel's top tube, the 21-px glyphs 2 px inside the box's black), and inside the panel each option is
one line: label at x 16 (172 px, `h 14`), `<` at x 194, the 8-column value at x 212, `>` at x 280,
the k-th option on y0 + 62 + 32 k (label / value + 1) - 10 px under the panel's top - and
**CANCEL / OK** as 90x26 text buttons (cell **20** = KNOBE 2 pixel for pixel; `textmsg 8 CANCEL` at
x 56, `textmsg 7 OK` at x 158, ids 55 / 56 unchanged for the handler) 32 px under the last option,
12 px above the panel's bottom. The steppers are cells **21 / 22** = KNOBE 14 / 16 (their ids
40..45, 67 / 68, 71 / 72 unchanged). Header lines added once: `font 0` → `intrface/mfonto5`,
`font 1 intrface/mfonto2` + `font_offset 1 31`, `bright_pushed 8`, `bright_highlight 4`, `textmsg 7
OK`, `textmsg 8 CANCEL`. The red title plate (`picture 3`) and the four label plates (16..19, cell
6) are dropped; the value boxes of §10.53 are gone (cell 14 stays in the bank as a spare). In game:
green labels (MFONTO5 under `remap 4`), cyan values and title, red rings, the CANCEL plate and its
caption visibly brighter under the pointer; `>` on GAME SPEED read 110 %; CANCEL and OK both closed
the dialog; `error.log` empty.

**Rules and pitfalls.** `console_dialog` chooses the form when the script has "-" / "+" pairs
(cells 12 / 13 or 21 / 22) and the list / name-box layout otherwise; the layout is derived from the
row positions, so the Ultimate copies with the MUSIC row (five options, buttons on y0 + 222, two
more middle rows) come out by the same rule, in either order with `music_row` (checked). The
PowerShell port equals the Python pass byte for byte under pwsh 7 and 5.1 on eight inputs (the
four dialogs, the Ultimate copy, the §10.49 plain form, the §10.53 boxed form at 1920x1200, the
boxed-then-music form). **Pitfall met:** the new constants shadowed `PANEL_X` (the HUD panel column
516 from `hud_layout`), and `render_frame` drew the whole frame with the panel at x 6 - caught by
the fixtures' `INTRFACE.GIF` hashes changing; renamed `OPT_PANEL_X` / `OPT_PANEL_W`. **Check the
frame hashes after every change to `hud_console.py`, even a data-only one.**

**Regenerated:** `INTRF_HD/POPP.SPR` (23 cells), `INTRF_HD/LOPTE` + the three `lopte` copies (the
list dialogs unchanged), the six fixtures (only `LOPTE`; frames byte-identical), the patcher. Not
run: the Ultimate copy in game, other sizes, the MUSIC row's `+` on the new plates (the handler is
unchanged, only positions moved). Follow-up worth doing: the same form for the save / objectives /
quit dialogs (text buttons SAVE / CANCEL / OK / YES, QUIT / NO, CONTINUE, header-box titles) so all
four dialogs share one language.

**Third round (same day; maintainer, on seeing the form: "every element must have gray frame. all
menus have some gray unclickable elements and lines between clickable elements").** The lobby's
slot rows (TCPWAIT.GIF, measured at x 300 / y 30) put every field in a **capsule** - a 3-px outline
`66 55 66` = 23 | 65 | 23 grey on black, 18 rows tall at a 19-row pitch, a rounded outer end drawn as
an inner arc - and join the capsules of one row with short 3-px **bars** of the same greys. The
options form now does the same: every option is a **strip picture** (POPP cell **23**, 282x24 at
x row + 11, y0 + 58 + 32 k; `capsule()` / `option_strip()`) with four capsules - label 140 (rounded
left end), "<" 22, value 70, ">" 32 (rounded right end, wide enough for the plate and the arc) -
joined by three 6-px bars; the label text at x 27 (4 px right of the arc, 116 px), the 8-column
value at x 188 (exactly the capsule's 64-px interior), the stepper plates at x 161 / 265 with 1 px
of black around them, everything on y0 + 63 + 32 k. The strips are regenerated boxes like the
header (ids 24..27, 28 with the MUSIC row). The style guide gains the capsule as its second frame
type (§3) and the rule "no bare element on a black panel" (§13). **Second collision of the day:**
`STRIP_CELL` already named MAINBUT's diplomacy header cell (92); the new constant took it over and
the HUD bank came out wrong (`MAINBUT.SPR` hash moved) - renamed `ROW_STRIP_CELL`. The duplicate
check `grep -oE '^[A-Z_0-9]+(, [A-Z_0-9]+)* =' hud_console.py | ... | uniq -d` is now part of the
routine, with the frame and bank hashes. Confirmed in game at 1024x768 (a fresh training battle:
capsules, bars and arcs as drawn, CANCEL brightens, CANCEL closes; `error.log` empty; the first
attempt found the training mission already won - mission 1 is short, do not leave a battle waiting).
Patcher port equal under pwsh 7 / 5.1 on the eight inputs; fixtures regenerated (frames unchanged);
POPP.SPR 24 cells; clean-copy patcher run: dialogs = fixtures, bank = shipped, exe hashes unchanged.

**Fourth round (same day; maintainer: "too early to push anything! we are not done yet! some frames
are not closed! check battlefield main interface and your 'options' interface").** Three open
frames, found by zooming the in-game captures to 4x and scanning `INTRFACE.GIF` at its edges:
(1) the pipework compartments of the HUD ran under the border tubes and under the read-out screens'
rings - `Pipework.region` filled a rectangle and the tubes / screens were painted over it, so every
compartment touching a border or a screen had no closing side (right panel edge: `67x20 40 65 65 67`,
the cell's outline running into the border); (2) the same at the screen edge for the screens whose
rings reached the border (GRID to x 635, the DAYS box); (3) the capsules' rounded ends were arcs
drawn inside a rectangle, not the outline. Fixes, all in `hud_console.py`: `Pipework.region(x0, y0,
x1, y1, holes)` cuts the area into horizontal strips at the holes' top / bottom edges and each strip
into the x-intervals free of holes, `fill_rect` fills each free rectangle with bands and tubes (under
3 rows ground, under 8 a tube); `render_frame` lists every hole first - the screens with their three
rings (`+3`), the BUILD / arrow plates (`+1`), the dial (`r + 5`), the border tubes, the view's edge
tubes - and draws the border tubes LAST, so a screen ring never cuts them; the DAYS label moved into
one screen with its value (`DAYS_PANEL` 605..632 x 420..444, its right ring stops before the border);
`capsule()` draws the three outline rings with `Canvas.ring(..., r)` and squares the straight end
again, so the semicircle is the frame. The frame of every size changed (`INTRF_HD/<WxH>/INTRFACE.GIF`
regenerated for all seven, the game folder's active set and the six fixtures = the shipped pictures);
`POPP.SPR` (cell 23) regenerated. Left as is: the dial's bezel at the right border (the clock cell's
position is the exe's, `patch_clock`; the border is drawn over it). Confirmed in game at 1024x768
(HUD panel top and bottom at 4x: every compartment ringed, DAYS framed; the options rows' ends
closed); `error.log` empty. **Not pushed** - the maintainer is reviewing.

**Fifth round (same day; maintainer: "do you see that battle interface tab buttons are braking
interface frame? 'OPTIONS' text does not have a frame at all. fix that").** (1) The tab strips were
124 px wide from x 516 with plates at 2..41 / 41..81 / 82..121 - the third plate ended at x 637 stock
= 1021 at 1024x768, inside the panel's border tube (1020..1023), and the opaque strip cell covered the
tube to 1023; the stock 640x480 layout has only a 2-px margin there. Now `tab_strip` is **120x16**
with plates 2..40 / 41..79 / 80..118 (the last ends at 634, 1 px inside the grid screen), `MAINE`'s
`picture 3..6` say `120 16` (`TAB_STRIP` and the patcher's `$TAB_STRIP` also match the 28 Sep
`124 16` form, so an existing set is rewritten), the click rects (`pushb 0/1/2`) are unchanged.
(2) The OPTIONS title had its frame all along - and its own label erased it: `label ... bg textbg`
paints its whole rect black before the glyphs, and the rect was the whole 292x32 box, so the tubes
and even the panel's top ring under it went black. The header box is now **200x32 at x 52**
(centred, pipework visible on both sides, so it reads as a framed element) and the label's rect is
the box's interior (x 56, y0 + 18, 192x24). Rule added to the guide (§3): a widget's rect lies inside
its frame; nothing crosses the border tube. Regenerated: `MAINBUT.SPR` (strips), `POPP.SPR` (cell
19), `MAINE` in the game folder and the six fixtures (`120 16`), `LOPTE` + copies, the patcher;
frames unchanged. Confirmed in game at 1024x768 (tab 1 and tab 3 active: the border's light line
runs unbroken right of tab 3; the title in its box with pipework either side); `error.log` empty.
Still uncommitted, pending the maintainer's review.

**Sixth round (same day; maintainer: "increase height and width of 'OPTIONS' frame. it must fill all
space between form border and actual options frame", then "battle interface tab button '1' are
braking interface frame. chat entering frame is broken too. and chat scrolling buttons must be
centered in they frame").** (1) The header box is **292x44 at x 6, y0 + 4** (cell 19): its outer
11-grey ring meets the border's at row 3 and the panel's at row 48 - 2-px seams, no pipework between;
rows 0..2 of the form are the new **blank cells 25 (border top + ground) / 24 (border sides +
ground)** so nothing peeks out beside the box; the label's rect 284x28 at + 4 / + 13 (glyphs centred
in the 36-px interior). **Bug found on the way:** `console_dialog` recognised rows by cells 0..5
only, so once a form's rows carried 16..18 / 24 / 25 a second pass was a no-op - the boxes-first
order lost the MUSIC strip. `ROW_KINDS` (0..5, 16, 17, 18, 24, 25) in both ports; the harness's
order-independence check caught it. (2) Tab plates: 36x14 at strip x 4 / 43 / 82, rows 0..13 -
2 px of the grid screen's black on every side; the first 120-px form had plate 1 against the
screen's ring. (3) The bar: the message screen `MSG` is (49, 460, 509, 472) - interior H-20..H-8,
rings to H-5, the bottom border from H-4 (before, the screen's bottom ring sat ON the border's light
line); the two bar texts `in_text 148 / 200` move to stock y 460 / 461 (`CHAT_LINE_Y`, `edit_hud_script`
now takes the height; patcher `$CHAT_LINE` in `Edit-HudScript`); the two arrow plates, which ran
into the bottom border, are gone - one **capsule channel** `CHAT_CHANNEL` (4, 457, 43, 475) is drawn
into the frame and the `pushb` cells (MAINBUT 40 / 50 / 57 / 76) are transparent 16x16 cells with
the triangle centred on the channel's halves (`BAR_ARROW_CENTRE`: up (11, 6), down (8, 6)). Every
size's `INTRFACE.GIF`, `MAINBUT.SPR`, `POPP.SPR` (26 cells), `MAINE` (game folder + fixtures) and
`LOPTE` + copies regenerated; confirmed in game at 1024x768 (tab 1 active clear of the ring, the
triangles centred in their channel, the message screen closed above the border; `error.log` empty).
Still uncommitted, pending the maintainer's review.

**Seventh round (same day; maintainer: "you didn't fix the problem with '1' tab frame! and now 'BUILD'
button lost left frame!", then "'DAYS' frame is too tight and days count is not horizontally centred
in the frame").** Pixel scan of the capture: at 1024x768 the map's right-edge tube sits at x 900..902
and the panel content stands ON it - BUILD's plate starts at 900, the screens at 902 (stock 516 /
518 against the tube at 512..514). The fourth round's "borders last" therefore overwrote BUILD's left
ring, and the 120-px tab strip, an opaque black picture from x 900 that the engine paints every
frame, cut the wall at the tab row. Fix: the view's right and bottom walls are drawn BEFORE the
screens and plates again (only the screen-edge borders stay last), as **BAND | LT | D11 from the map
outward** - a screen ring (BAND, LT from its black interior) then coincides with the wall instead of
jogging it by a pixel, a plate on the wall keeps its ring; `tab_strip` is transparent (index 0)
outside its plates. DAYS: the screen's bottom ring (733..735) had been overdrawn by the dial's bezel
(from 733) and the count's 20x6 glyphs sat at x 997..1016 in an interior 989..1016, 4 rows under
their widget's y. Now `BUILD_PLATE` (516, 422, 596, 448) is drawn 5 px narrower than the click rect,
`DAYS_PANEL` is (601, 420, 632, 441) - interior 32 px wide, bottom ring 3 rows above the bezel - and
`in_text 234` moves to stock (607, 429) (`HUD_TEXT_POS`, with the two bar texts; patcher
`$HUD_TEXT`), so the digits are centred in the screen and the label / count pair in its height.
Regenerated: every size's frame, `MAINBUT.SPR`, `MAINE` (game folder + fixtures). Confirmed in game
at 1024x768: the wall runs unbroken past tab 1, BUILD has its left ring, DAYS label and count framed
with margins; `error.log` empty. Still uncommitted.

**Eighth round (same day; maintainer: "'DAYS' and days count must not overlap each other").**
Measured in the capture: the `MFONTO7` caption is 10 rows (708..717 at 1024x768), the count's digits
are 10 rows and start at the widget's y, and the widget paints a **12-row** black rect from its y
(`bg erase`); the DAYS screen's interior is 22 rows (708..729) between the status screen's ring and
the dial's bezel. So a 10-row caption and the count cannot both fit: at y 429 the rect took the
caption's last row, at y 432 (the lowest 10 digit rows) the rect's rows 730..731 erased the screen's
bottom ring (`LT`, `BAND`). The caption is now the frame's own **5x7 pixel lettering**
(`caption_5x7` / `CAPTION_5X7`, green `GRN[1]`, 23 px wide, centred) on rows 420..426 stock, the
count at stock (607, **430**): digits 430..439, rect 430..441 = the interior's last row, a 3-row gap
under the caption, the ring intact (verified: rows 730..732 `LT BAND D11`, then the bezel). Frames
of every size, `MAINE` (game folder + fixtures) regenerated; confirmed in game at 1024x768;
`error.log` empty. Still uncommitted.

**Ninth round (same day; maintainer: "apply the same style to save, objectives and quit dialogs").**
`console_dialog` now treats every dialog as a form: rows 0..2 become the blank cells 25 / 24 under
the 292x44 header box (cell 19) with the title as a `font 1` (MFONTO2) label inside it, every cell-6
plate (the red title and label plates) is dropped, and the buttons are text buttons - `_text_button`
= `pushb N 0 x y w 26 -11 <cell> label centre <msg> 2 - remap 0`, the caption in **font 2 = MFONTO5**
(a new slot, so a dialog's font 0 is left alone; the options form alone switches font 0 to MFONTO5 -
the first try switched it everywhere and the objectives text, wrapped for MFONTO7's advance,
overflowed the window). Save (LSGE): CANCEL (55) at x 56 / OK (56) at x 158 on their own y, the
name box over blank rows; objectives (LOBJE): a lone OK centred at x 107; quit (LQCE, pushb 57
present): YES, QUIT (56) / NO, CONTINUE (57) as **180x26** plates (POPP cell **26** = KNOBE cell 0)
at x 62 with the dialog's textmsg 2 / 3, the two label widgets that carried the texts dropped. The
`_form_header` block is canonical: existing `font 1/2`, `font_offset 1/2`, `bright_*` and
`textmsg 7/8` lines are dropped and re-inserted (font 1, font 2 after `font_offset 0`; the bright
pair after `colour selbg`; OK / CANCEL after the first textmsg, not for the quit form), so a file
edited twice, or before / after `music_row`, equals a file edited once (checked: the 28 Sep plain
LOPTE through the pass = the game folder's file, CR-stripped). The over-edited `font 0` line of the
game folder's and fixtures' LOBJE / LQCE was put back to MFONTO7 by hand. POPP.SPR has 27 cells. PS
port equal under pwsh 7 / 5.1 on the eight inputs. Confirmed in game at 1024x768: save (F11),
objectives (text fits again, OK centred), quit (hover brightens NO, CONTINUE, the click closes),
options; `error.log` empty. Clean-copy patcher check: 16 of 16 dialog files = fixtures, the plate bank = the
shipped one, the Ultimate copies = the tool chain (the `sed` that put the LOBJE / LQCE font line back had
stripped the fixtures' CRs; both were rebuilt from the stock scripts through the tool chain). Uncommitted.

#### 10.55 Save, objectives and quit dialogs on the black panel **(30 Sep 2026, maintainer: '"save messages", "objectives", "really quit" must have black background'; `tools/hud_console.py` (`_dialog_row`, `_list_form`, POPP cell 27), the patcher's `Edit-DialogConsole`; guide §6, §7, §8, §13 amended; data only, no exe byte; confirmed in game at 1920x1200: all four dialogs)**

**What was wrong.** §10.54's ninth round made every dialog a form, but only the options form
stood on the black panel (cells 16 / 17 / 18); the save, objectives and quit dialogs kept the
§10.49 pipework rows (cells 1 / 2) between the header box, the list block and the buttons, and the
save name's box lay over blank rows - grey compartments where the OPTIONS dialog shows black (the
maintainer's screenshot of the four side by side).

**The fix.** `console_dialog` now gives EVERY dialog the same body: rows 3..last are the framed
black panel (cells 16 / 17 / 18) and the list block is a **compartment of that panel**: the list
window's frame (x 6..269) and the scroll channel's frame (x 272..297) share the panel's side tubes,
the 2 px between them (x 270..271) are the panel's black instead of ground, and the block's top and
bottom edges are **full-width dividers** (the frames' own top / bottom tubes continued across
x 266..275, the panel's side tubes drawn straight again over the frames' rounded corners - a clean
T-junction, checked at 4x in game). Row kinds: **3** = a list top AT the panel's top (the objectives
dialog: the panel's rounded corners, the two inner tubes hanging from its top edge), **27** (new,
`LIST_TOP_INNER`) = a list top INSIDE the panel (the save dialog, under the name box: straight sides
+ divider on rows 0..3), **4** middle, **5** bottom (divider on rows 12..15; the buttons follow on
black). The name box (cell 15) is now **280x24 inside the panel** at (row + 12, top row + 6), 2 px of
black to the panel's tubes, the `in_text` at (row + 20, top row + 12) (was 264x24 at x 6 on the
panel's own tube). The text buttons keep their own y (LSGE 752 / 776 bottom row at 1024x768:
2 px above the panel's bottom tube). POPP.SPR has **28 cells**; the pipework rows 0 / 1 / 2 stay in
the bank but no laid-out dialog uses them any more (the 640x480 dialogs are stock).

**Which rows are the list's.** The kinds are derived from the `list` widget's y range (the rows
with list cells inside `y0 - 16 < y < y0 + h`), not from the cells a row carries: the STOCK save
dialog frames its name field with two list-top / middle cells (rows 3 / 4, y 240 / 256 at
1024x768), and the first form of this pass took them for the list's top - only visible when the
patcher laid the stock script out on a clean copy (`LSGE DIFFERS from fixture`: rows 3 / 4 as
cells 3 / 4). The Python fixtures and the harness's inputs were all already laid out (name rows
blank), so they could not show it. **The harness now runs the four stock scripts too** (18 inputs:
the four laid-out dialogs, the Ultimate MUSIC copy, the §10.49 plain form, the §10.53 boxed forms
at 1920x1200 and from `15fc03b`, the §10.54 forms from `HEAD`, the four stock scripts, boxed-then-
music); pwsh 7 = 5.1 = Python on all of them, every input idempotent. Second slip of the same kind
as §10.54's sixth round: `ROW_KINDS` did not list the new cell 27, so a second pass did not see the
row and moved the list down a row - caught by the idempotence line of the preview script.

**Regenerated.** The game folder (the maintainer's 1920x1200 set): `INTRF_HD/POPP.SPR` and
`LSGE` / `LOBJE` / `LQCE` only - `INTRFACE.GIF`, `MAINBUT.SPR`, `SPRITES/CLOCK.SPR`, `MAINE`, `LOPTE`
and the three `lopte` copies came out byte-identical from `hud_console.py apply` (the frame / bank
hash routine); the six fixtures (`apply --no-bank`, the three dialogs per size, frames untouched;
the game folder's three = the 1920x1200 fixture's); the patcher (1 531 715 bytes). Exe hashes
unchanged.

**Game test** (Classic 1920x1200 build from the game folder itself, `drive.py`: SPACE aborts the
intro, TRAINING (869,804), name, START TRAINING (1130,722), NEXT (1228,822); F11; Game Option tab
(1898,102), Objectives (1827,336), Quit (1827,131), Options (1827,213); the dialogs sit at row x 752,
LSGE y0 408 at this size): Save Game, OBJECTIVES, REALLY QUIT? and OPTIONS all on black inside the
panel, list block dividers closed, CANCEL / OK / NO, CONTINUE / CANCEL closed them, `error.log`
empty. Not run: Ultimate, other sizes, 640x480 (stock).

**Rules.** (1) A pass that derives a layout from cell kinds is proven only on the STOCK input - keep
the stock scripts in the harness. (2) A list-type cell is not a list row; the `list` widget's range
says which rows are the list's. (3) Every dialog body is the black panel; a frame inside it either
shares the panel's tubes (a compartment with dividers) or keeps 2 px of black to them (a box).
Committed and pushed 30 Sep 2026: Dark-Colony `5d35735`, Server `410ecb2`.

**Quit buttons centred (1 Oct 2026, maintainer: "'really quit?' dialog buttons must be better centered in frame").**
The two 180x26 plates had kept the stock script's y (8 px under the panel's top tube, 30 px above
its bottom one). `_list_form` / `Edit-DialogConsole` now centre the pair in the panel's interior
(row 3 + 4 .. last row + 3, 112 rows): YES, QUIT at row 3 + 23, NO, CONTINUE 48 px lower
(`QUIT_GAP` 22 between the plates), 19 px of black above and below the pair (1024x768: y 375 / 423;
1920x1200: 591 / 639). Only `LQCE` changes (game folder, six fixtures, patcher); harness 18 inputs
pwsh 7 = 5.1 = Python; measured in game at 1920x1200 (red rings at rows 592..615 and 640..663 in the
572..683 interior), NO, CONTINUE closes, `error.log` empty. **Same for save / objectives (maintainer:
"do the same for save and objectives dialogs"):** OK / CANCEL are centred between the list block's
bottom divider (bottom row + 16) and the panel's bottom tube (last row + 4) - 52 rows in both
dialogs, so the 26-px plates get 13 px of black above and below: y = bottom row + 29 (1024x768 LSGE
525, LOBJE 477; 1920x1200 741 / 693; before: LSGE 2 px above the tube, LOBJE 8 / 19). A dialog
without a list keeps the script's y. Both ports; `LSGE` / `LOBJE` regenerated (game folder, six
fixtures, patcher); harness 18 inputs pwsh 7 = 5.1 = Python; measured in game at 1920x1200 (rings
at 742..765 in 728..779 and 694..717 in 680..731), CANCEL and OK close, `error.log` empty.

**Objectives' missing bottom border and the tab strip erasing the view's wall (1 Oct 2026, maintainer:
"'objectives' is missing bottom part frame of form. '1' tab when clicking tab '2' or '3' is deleting
left part of interface frame").** (1) The stock `LOBJE` says `size ... 304 272` for 18 rows of 16 =
288: the engine draws nothing below the `size` rect, so the last row (the panel's bottom tube + the
dialog border, cell 18; cell 2 in the stock) was never painted - a stock bug, visible since the panel
made the dialog's bottom a tube (capture: border column ends at row 727 = 456 + 272 - 1). `console_dialog`
/ `Edit-DialogConsole` now grow the `size` height to reach the last row + 16 (`SIZE_LINE`; only
LOBJE changes, 272 -> 288; LSGE 386, LQCE 176, LOPTE 240 already cover their rows). (2) The 120x16
tab-strip pictures (`MAINE` `picture 3..6` at x 516 + dx, MAINBUT cells 77..79) start ON the view's
wall (x 900..901 stock = strip columns 0..1, `BAND | LT`); §10.49's seventh round made the strip
transparent there, which is right for the first paint over the frame, but a tab click repaints the
strip and the engine erases the picture's rect first - the two wall columns went black. The strip
cells now carry the wall (`tab_strip`: column 0 BAND, column 1 LT, all 16 rows; the right end, x 1019
stock, is black in the frame and the ring sits at 1020, outside the strip). Regenerated: the game
folder's `MAINBUT.SPR` + `LOBJE`, the six fixtures' `LOBJE`, the patcher. In game at 1920x1200: the
wall pixels at x 1796 / 1797 read (33,32,33) / (107,105,107) before and after clicking tabs 2, 3 and
1; the objectives border column runs to row 743 and the bottom tube is drawn; `error.log` empty.
**Rule: a picture widget's rect is erased before every repaint - whatever the frame shows inside
that rect must be in the picture, transparency only saves the first paint.** And: a dialog's `size`
rect must cover every row (check `size` against the last row when adding rows).

#### 10.56 A realistic Mars backdrop for the main menu and every pre-battle screen **(1 Oct 2026, maintainer: "pre battle dialogs on higher resolutions than original are on black background. I need you to create realistic picture based on main menu background which can be used as a background for all prebattle forms including main menu"; `tools/paint_intro.py` (`paint`, `scene`, `render(..., band=False)`, `crescent_tail_row`), `tools/pad_background.py` (`compose_over_backdrop`, `--backdrop`), `tools/split_hd_data.py` (`HD_ONLY`), the patcher's `DcGif.Pad(src, W, H, backdrop)` / `Write-InterfaceSet`; a fourth shipped picture per size, `INTRF_HD/<WxH>/BACKDROP.GIF`; data only, no exe byte; confirmed in game at 1920x1200 in both builds)**

**What was wrong.** Every full-screen menu other than the main menu is the stock 640x480 picture
letterboxed by `pad_background.py` (§10.1): the GIF is centred on a black canvas and the widgets
are shifted by the content offset. At 1024x768 the black border is 192 px wide, at 1920x1200 it is
640 px: the race selection, the network screens, LOAD GAME, the story and victory screens sat as a
small picture in a black field. The main menu itself was repainted full-frame in §10.11 (a
procedural starfield and the rim-lit limb of Mars, measured from the stock art), but as a painted
gradient - a flat orange band with faint noise.

**The picture.** `paint_intro.paint` renders the scene realistically now, on the stock geometry
exactly: the planet circle (centre (320.2, 351) of 640x480, radius 291.4 rows), the lit crescent
45 px deep at the top of the limb, the 10 px glow outside it and the star density (4.95e-3 per sky
pixel) are the measured constants of §10.11, so every menu layout rule built on them (the DC logo
under the crescent's tail at row 112 of the 480-row design - `CRESCENT_TAIL`, `cw_menu_lift`; the
credits box; the button block hung from the title) holds without a change. Inside that frame:

* the planet is a sphere lit by a sun above and behind it (`sun_direction`: the elevation that puts
  the terminator 45 px inside the limb on the centre line, tan β = sqrt(2d-d²)/(1-d), β = 32.3°);
  the surface is a height field of rolling terrain and 900 bowl craters with raised rims (power-law
  radii, flat floors above 20 texture px), bump-mapped under a Mars-like albedo map (broad dark
  regions from a smoothstepped low-frequency noise, mottled plains, fine dust streaks); the Lambert
  term is steepened near the terminator (`nl^1.6`, rough-surface shadowing) and multiplied by an
  envelope on the FLAT sphere's n·L (`clip(nl/0.10)^2.5`), so no lit crater rim pokes beyond the
  geometric terminator - that envelope is what keeps the DC logo rows black (first renders put lit
  rims 7-10 rows below the limit at 1024x768 and 1280x720); a thin dust haze brightens the lit rim
  and glows outside the limb in two layers (dense warm, thin pale), fading with the angle from the
  top as the stock glow did;
* the sphere is parametrised about the VIEW axis (latitude = angular distance from the limb,
  longitude = position around it): the visible crescent then holds no texture pole and features
  foreshorten radially the way real terrain does. The first render used a pole at the top of the
  limb and every streak converged there;
* stars follow a magnitude-like brightness distribution (`0.075·(1-u)^-0.85`, many faint, a heavy
  bright tail), colour temperatures from blue-white to orange with rare red, point-source sizes
  that do not grow with the canvas, a soft halo on the bright ones;
* quantisation to the master palette (index 0 excluded, black = 254) with an ordered 4x4 Bayer
  dither of ±10/255 over the planet and its glow only, and only where the pixel is brighter than
  20/255, so the shading mixes the palette's orange, brown and tan ramps instead of banding while
  the dark fall-off towards the terminator and the sky stay clean. The sky and the stars are never
  dithered.

`render` now measures the result: `crescent_tail_row` is the last row over the DC logo's 310
columns with a pixel brighter than 7/255 (the measurement behind `CRESCENT_TAIL`), and a render
whose tail passes round(112·H/480) − 1 raises. Measured: 172 / 178 at 1024x768, 163 / 167 at
1280x720, 178 / 186 at 1280x800, 217 / 238 at 1280x1024, 228 / 251 at 1920x1080, 249 / 279 at
1920x1200, 228 / 251 at 3840x1080 (tail / limit). The scene is painted once per size and shared by
the three pictures (`scene`, a module cache): **INTRG.GIF** and **INTRO.GIF** with their stock
bottom bands as before, and the new **BACKDROP.GIF** without a band - the pre-battle screens'
ground. `paint_intro.py apply` writes INTRFACE/BACKDROP.GIF and `split_hd_data.py` moves it to
INTRF_HD (`HD_ONLY`: a file with no stock counterpart that belongs to the set); the shipped copies
are `INTRF_HD/<WxH>/BACKDROP.GIF` for all seven sizes (34-90 KB each), beside INTRG, INTRO and
INTRFACE.

**The screens.** `pad_background.pad_gif(src, dst, W, H, backdrop)` lays the 640x480 picture on
the backdrop instead of black, in a **panel frame** (style guide §3, the tube seen from outside):
outside in `35 | 107 | 35`, then 2 px of ground (11), then the picture; the outer ring's corner
pixel is ground and the light ring's corner is 35 like every frame in the guide. The frame makes the
cut deliberate - at 1024x768 the form's top edge (y 144) crosses the lit crescent (rows 96..179),
which is unavoidable with one picture for every screen and a 640x480 form that is 62 % of the
width; as a framed console window in front of the planet view it reads as intended. Every screen
keeps its own palette (the sprites drawn on it are decoded through it; MULTIWIN's differs from the
master in 155 entries, LOADER's in 55), so the backdrop goes through a 256-entry nearest-colour
table into the screen's palette (`backdrop_lut`: squared RGB distance, index 0 excluded, first of
equals; the backdrop's blacks to the screen's padding index - index 0 in every stock GIF, as the
black letterbox used) and the frame greys are the palette's nearest neutral entries
(`nearest_grey`). Measured remap error against the master palette: 0.7-0.9/255 mean for 13 of the
15 screens; LOADER.GIF and TCPWAIT.GIF have no (0,0,0) but index 0 and no dark grey below (7,7,7),
so their ground is 7 - invisible behind the frame. `--backdrop PATH|none`; by default the tool
takes `INTRF_HD/<WxH>/BACKDROP.GIF` under the game root, else a BACKDROP.GIF of the size beside
the scripts or in the base INTRFACE, else black as before. ONLINEBG.GIF is derived from the new
LOADER.GIF as before (`patch_online.online_background`) and inherits the backdrop and the frame.
The loading screens LOAD.BMP / LOAD2.BMP are not screens (LoadImageA + BitBlt) and keep their
black border.

**Patcher.** `set_sources` lists four shipped pictures (`SHIPPED_PICTURES`); `Write-InterfaceSet`
throws when `INTRF_HD\<WxH>\BACKDROP.GIF` is missing, like the other three, and passes its bytes
to `DcGif.Pad(src, W, H, backdrop)` - the byte-identical C# port of `compose_over_backdrop` (LUT,
rings, corners, picture; `NearestGrey`, `FrameGreys`); the black form `Pad(src, W, H)` stays for
callers without a backdrop. Compiled and compared under pwsh 7 and PowerShell 5.1: the C# output
for MULTIWIN at 1024x768 equals `pad_gif`'s file byte for byte. Clean-copy check at 1024x768 and 1920x1200 under both shells: sets = fixtures (57 of 57 comparable
files; HSCENE/GSCENE differ by the known DC ending names), exe hashes unchanged - data only.

**Regenerated.** The seven shipped sets `INTRF_HD/<WxH>/{INTRG,INTRO,BACKDROP}.GIF`; the 15
letterboxed GIFs + ONLINEBG.GIF + INTRG/INTRO of the six fixtures `hd_sets/<WxH>/INTRF_HD`; the
game folder's active 1920x1200 set (same 18 files); the patcher. Scripts, banks, HUD frame and
exes untouched. **In game (1920x1200, both builds):** main menu (planet, logo on black, title,
credits, button block), LOAD GAME, NEW CAMPAIGN's race selection and MULTI PLAYER WAR's network
screen, each centred in its frame on the planet view; QUIT exits 0; `error.log` empty. Not run:
the other sizes in game, the lobby / TCPWAIT / story / victory / encyclopedia screens (same code
path, same letterbox), 640x480 (stock files, untouched).

**Rig.** `drive.py` from the game folder at 1920x1200: SPACE aborts the intro only once the movie
plays (the first SPACE at 5 s was too early - press it again), Classic menu LOAD GAME (869, 830),
MULTI PLAYER WAR (1050, 777), QUIT (1050, 857), LOADGE BACK (953, 807), NET MAIN MENU (1183, 813);
Ultimate QUIT (1056, 966). Park the pointer at (5, 5) before a capture.

**Lessons.** A texture parametrisation must put its poles where the camera cannot see them. A
physically lit sphere disagrees with a hand-painted crescent exactly where layout rules were
measured (the terminator dips at the logo's outer columns) - measure the constraint in the render
and enforce it with an envelope, do not move the layout. Dither only where there is light to
dither: an ordered pattern over the dark fall-off lifts pixels past the 7/255 threshold the menu
rules rest on.

#### 10.57 The real Mars on the sphere: MOLA relief and the Viking colour mosaic **(1 Oct 2026, maintainer after seeing §10.56 in game: "planet is looking like an asteroid. Can you please take a real Martian topology and wrap on the sphere?"; `tools/mars_maps.py` (new), `tools/mars/` (new, the derived maps), `tools/paint_intro.py` (`mars_maps`, the planet block, `MARS_TILT` / `MARS_LON0` / `MARS_RELIEF`); data only, no exe byte, patcher unchanged; confirmed in game at 1920x1200)**

**Sources** (downloaded with the maintainer's permission, both public domain, kept in
`Dark-Colony-development/mars_maps/` outside the repositories): the MOLA MEGDR grid
`megt90n000eb.img` + `.lbl` (NASA PDS Geosciences Node, 16 px/degree, 5760x2880, MSB 16-bit metres
above the areoid, simple cylindrical, longitude 0..360 east from the left edge, +90 at the top,
33 MB) and the USGS "Mars Viking Colorized Global Mosaic 232m" 1 km JPEG
`mars_viking_mdim21_clrmosaic_1km.jpg` (21339x10670, 37 MB). `mars_maps.py prepare SRC` derives
what the render needs and what the Server repository carries (`tools/mars/`, 13.7 MB):
`mola_height_4096.png` (4096x2048 16-bit, metres = value·scale + offset from `mars_maps.json`;
box-filtered from the 16 ppd grid; 10.9 MB - 16-bit terrain compresses poorly, an 8-bit map would
step the slopes in 115 m terraces), `viking_color_4096.jpg` (Lanczos, quality 92, 2.8 MB) and
`mars_maps.json`. Two checks are built in and printed: the lowest point of the height map must be
the floor of Hellas (found at 32.8 S 62.2 E, −8163 m), and the colour mosaic's longitude origin is
detected by Syrtis Major, the darkest albedo feature at 8 N 70 E - the USGS JPEG runs −180..180
(Syrtis luminance 70 at the −180 position vs 117 at the 0..360 one) and is rolled by 2048 columns to
the MOLA convention.

**Wrapping.** `paint_intro.paint` keeps everything of §10.56 but the surface: the view-space normal
of every disc pixel is rotated into the planet frame - the view normal rolled about the view direction by
`MARS_ROLL` = −83°, the pole tilted `MARS_TILT` = 174° about the screen's x axis (the axis almost
along the view direction, south pole towards the viewer), then the planet turned so longitude
`MARS_LON0` = 179 E faces the top of the limb - giving latitude / longitude, which sample both maps bilinearly (the maps wrap
in longitude). The relief is the finite difference of the height along the sphere (dh/dlon over
R·cos(lat), dh/dlat over R, R = 3396 km) exaggerated `MARS_RELIEF` = 2.5 times (6 until the maintainer's "lights and shading right now are
too aggressive") - at 12 px per degree the true slopes are invisible - and applied as a bump to the normal through the planet's east /
north tangents rotated back into view space; the albedo is the mosaic's brightness (gamma 0.85) coloured with
`MARS_TINT` (1.05, 0.62, 0.36) - the stock crescent's orange - plus 1.2x the mosaic's own chroma for
the dark and light albedo regions, lit by a warm low sun (1.0, 0.68, 0.45), ×`BODY_GAIN` 2.6 (3.2
until the same complaint: the lit canyon walls clipped to full orange, "glows as a mirror"). After
the maintainer's "edges of obstacles are too bright" a sun-facing slope may brighten at most
`SLOPE_LIGHT_CAP` = 0.10 (in n·L) above the flat terrain around it while shadows keep their depth, and
the slope maps are blurred by `SLOPE_SMOOTH` = 2 passes of a 5-point kernel so the relief has no
one-texel edges (the first real-map
render used the mosaic's own butterscotch-brown; the maintainer: "make it warmer, more orange like
before"). Lambert
steepening, the terminator envelope, twilight, rim haze, glow, stars and the dither are unchanged.
The first adopted view (tilt 75, 245 E) put the Tharsis rise with Olympus Mons and the three Tharsis
Montes under the top of the limb and Valles Marineris down the right flank; the maintainer then asked
for "the side of Mars containing Mariner valley" (second view: tilt 86 / 285 E, the canyon along the
lit band just inside the limb) then "canyon must go from viewers night side across horizon but
not in 90 degrees. better to do 75 degrees angle to horizon", and finally "mariner valley must set to
50 degrees instead of 75 degrees". At the limb itself every surface
direction projects onto the horizon (the surface is edge-on there), so the angle is defined a little
inside: a brute-force search over roll and tilt (1° steps) puts the canyon's western end (7 S 268 E)
at the top of the limb and asks that the canyon point 12° further east lie inside the disc with the
screen vector from the limb point at the asked angle below the horizontal - 75° gave roll −83 /
tilt 178 / 178 E (78° measured), the final 50° gives roll −83 / tilt 174 / 179 E (49°). The canyon then climbs from the night side across the lit band and over the
horizon, the Tharsis plateau beside it; to keep the inner half of the band readable the lighting
fall-off was eased where the tail limit allows: `LAMBERT_POWER` 1.6 → 1.3 (`ENVELOPE_NL` 0.10 and
`ENVELOPE_POWER` 2.5 unchanged - 0.08 / 2.0 put lit canyon walls at row 184 over the DC logo columns
at 1024x768, 6 past the limit, and 171 / 167 at 1280x720; 1.3 / 0.10 / 2.5 gives 176 / 178 and
165 / 167, so the canyon now points at the logo rows and sits 2 rows inside the rule). Tried and rejected on the way: tilt 62 / 285 E (the canyons cut
by the terminator), tilt 45 / 300 E (on the limb, little relief), tilt 100 / 290 E with rolls of
0 / 30 / 60° under the old fall-off (lost in the dim half of the band), tilt 94 (a bead line on the
limb), relief 3 (too flat). The first realistic render's procedural cratered surface (`crater_field`,
`TEXTURE_SHAPE`, `CRATERS`) is gone from the tool.

**Tails** (`crescent_tail_row` / limit): 178 / 178 at 1024x768, 167 / 167 at 1280x720, 185 / 186
at 1280x800, 216 / 238, 228 / 251, 253 / 279, 228 / 251 - the real relief lights a few ridges
right up to the limit at the small sizes; the envelope still holds. **Regenerated:** the seven
shipped sets (INTRG, INTRO, BACKDROP), the six fixtures' 18 GIFs, the game folder's 1920x1200 set.
The patcher is untouched (it copies the shipped pictures; the composition rule did not change).
**In game** (1920x1200, Classic): main menu and LOAD GAME on the new planet, QUIT exits 0,
`error.log` empty. `MARS_TINT`, `sun_col` and the chroma factor in the planet block are the colour knobs.

**Lessons.** Check a downloaded planetary map's conventions against two landmarks before wrapping
it (lowest point, darkest feature) - the two public-domain products disagreed on the longitude
origin. Keep the heavy originals outside the repositories and commit a derived, documented
product the tool can regenerate from them.

#### 10.58 The patcher as a classical installer: one display fix per size, the 150 % speed back, the resolution chosen once, Dark Colony deprecated **(1 Oct 2026, maintainer: "let's update patcher. use best approaches for installer building. combine all patches related to resolution change into one single patch. return back patch for 150% game speed by default. don't select any resolution by default. force the client to select resolution once at the beginning, mark 640x480 as (original). mark patching 'Dark Colony' executable as deprecated and disabled and skipped by default"; `tools/gen_apply_script.py` → `Apply-DarkColonyPatches.ps1`; exe bytes: only the two `speed` dwords; tested headlessly under pwsh 7 and Windows PowerShell 5.1 and on the command line on a clean `git archive HEAD` copy; not run in the game)**

**One display fix.** The generator replays the three display tools as consecutive steps `resolution, hdpaths, clock` (moved in front of `cursor` / `pool`; the tools touch disjoint bytes, see the check below) and attributes them as ONE diff: `COMPOSITE = {'resolution': ['resolution', 'hdpaths', 'clock']}`, `FIX_OF` maps a step to its fix, the build loop folds the consecutive states of a composite into one `attribute()` call whose blocks are `blocks_display` = the union of the three tools' block lists (0 unannotated runs in every mode; 199 edits for Ultimate / 198 for Classic at 1024x768 = the 165/164 display sites + the 2 PE stack dwords + the 30 `intrf_hd` strings + the clock's 2 anchors and `sprites/clock`), and the merge phase iterates over fix ids instead of steps. The fix is `resolution`, named "WxH display: screen mode, interface data from INTRF_HD, clock hand", one variant per HD size (`Mode = 'WxH'`), `SetSources` + `Data = hd_data` on it, no `Requires`; `movies` / `ozi` (HD variants) and the "needs" texts now say `resolution`; `-Patches hdpaths` is refused as an unknown id. **Check:** every fix but `speed` at 1024x768 reproduces the exes published before this day byte for byte (Classic `928bb8d0…`, Ultimate `d970597f…`), so neither the merge nor the new step order changed a byte.

**Speed back.** Step `speed` (`patch_speed.py --percent 150`, 2 edits, §10.14) sits after `pool` again, with the description saying that the GAME SPEED slider still changes it in game. Published 1024x768 builds since then: **Ultimate `bc751e3b…`, Classic `5f85cee6…`** (editor `de8076dc…` unchanged); the two exes were rebuilt by the patcher on the clean copy and copied into the game folder working copy (whose `INTRF_HD` set is 1024x768); `dc16.asm` / `dcexp16.asm` regenerated from them (`mov dword ptr [esi+970h],2Ch` at `0x41BBFC` / `0x41BC5C`).

**No default resolution.** `DEFAULT_MODE` became `PUBLISHED_MODE` (emitted as `PublishedMode`, only for the reference-hash wording); `Resolve-Mode` throws for a game build without `-Resolution` with the labelled list ("choose the screen resolution for Dark Colony Ultimate with -Resolution <WxH>: 640x480 (original), 1024x768 (4:3), ..."); `Get-PreferredMode` is gone; `Format-ModeLabel` returns **`640x480 (original)`** for the stock size; `-All` without `-Original` checks the resolution once before writing anything. New switch **`-IncludeDeprecated`**.

**Deprecated Dark Colony.** `BUILDS` is ordered CouncilWars, MapEditor, Classic (= the window's pages and `-All`), the Classic entry carries `deprecated` = the reason ("Dark Colony Ultimate.exe plays the whole Dark Colony campaign (DARK COLONY, LOAD DC GAME, ACADEMY in its main menu) with every fix, so a separate Dark Colony.exe is no longer needed; it is kept for players who want the Classic executable on its own"), emitted as `Deprecated`; `-All` skips it with that text and the two ways to get it (`-IncludeDeprecated`, or `-Original "DC - Council wars\dc16.exe" -All -Resolution <WxH>`), `-List` and `Invoke-CliBuild` print it; the repository still ships the rebuilt `Dark Colony.exe`.

**The window** (`Show-PatcherWindow`): Welcome (the three originals found, in the new order, the deprecated one marked; the shortcut option) → **Step 1 of 5: Screen resolution** (one `RadioButton` per mode, `Tag` = the mode, nothing checked, Next disabled until one is; the 640x480 row adds "the game as it shipped: no display fix ..."; a note on what the choice does; `$script:gui.SetMode <mode>` ticks the radio, refills every page and enables Next - also the test hook) → Step 2..4: Dark Colony Ultimate, Map Editor, Dark Colony (deprecated) - the drop-downs are gone, a grey line shows the chosen size ("press < Back to change it"), a deprecated page has the include box unticked with an orange "DEPRECATED, skipped unless you tick it" suffix, an orange notice line, and its checklist greyed until ticked (`FillItem`: the list and "Select all" are live only while the executable is ticked, for every build; the info box explains the skip) → **Step 5 of 5: Ready to patch** (`$script:gui.Summary`: resolution, per executable input → output, "all N fixes for WxH (= the reference build)" or "n of N" with the left-out fixes and why, the interface set, "REPLACES the existing ...", skipped executables with the reason, shortcuts; Next reads Patch) → Finished (results + "Skipped: ..."). `Apply` refuses a ticked game without a chosen resolution. Test hooks: `GoTo`, `SetMode`, `Summary`, `Apply $false`, `Load`, `Controls.ModeRadios` / `ModePick` / `Ready` / `ReadyText`.

**Tests** (scratchpad `gui_test.ps1`, `run_tests.sh`; clean copy under `pwsh` and `powershell`): order and defaults, 8 radios none preselected, `640x480 (original)`, Next blocked at step 1 and the click guard, fix lists (Ultimate 1024x768 = `nocd, resolution, cursor, pool, speed, ddraw, palette, camera, widemap, restore, longpath, music, menuorder, chat, ozi, icon, online`; 640x480 without `resolution` and with the stock-mode `ozi` variant; Classic ends `movies, sounds, icon`; editor 15), the deprecated page (greyed, notice, info text, ticking enables 17 fixes), the summary text, a silent Apply = two results at the reference hashes with two shortcuts in a scratch desktop, the Finished text, a refused Apply without a mode. CLI: `-All` without `-Resolution` refused with nothing written; `-All -Resolution 1024x768` = Ultimate + editor at the references with the Classic skip note (pwsh 7); `-IncludeDeprecated` = all three (5.1); `-Original dc16.exe -Patches nocd` refused without `-Resolution`; `-Resolution 640x480 -Patches nocd` writes; `-Patches nocd,hdpaths` refused; the no-speed builds = the old published hashes; `-Verify`, `-List`; the written 1024x768 set = the fixture (57 files; HSCENE/GSCENE = the DC ending names, as documented).

**Pitfalls.** `Control.Visible` of a control on a form that was never shown reads `$false` whatever was set - a headless test checks the text or its own state, not `Visible`. A disabled `CheckedListBox` still raises `SelectedIndexChanged` when `SelectedIndex` is assigned, so `Select` wrote the highlighted fix's description over the "deprecated and skipped" explanation; `Select` now returns for an unticked executable.

**Second round the same day (maintainer: "dark mode must be optional but not preselected, customer must be forced to select light mode (classic) or dark mode of battlefield interface. resolution selection must be a dropdown, then under it dark/light theme selection and under it checkbox about patching deprecated executable (if not selected then options screen for dc16.exe must not appear at all)", then "remove already patched 'Dark Colony.exe' from repo by default").** **The battlefield interface is a theme.** The console-style HUD of §10.49-10.55 is the DARK theme: data (the shipped `INTRF_HD\<WxH>\INTRFACE.GIF`, the banks `INTRF_HD\MAINBUT.SPR` / `POPP.SPR`, `SPRITES\CLOCK.SPR`, `hud_console.edit_hud_script`'s tab strips and text positions, `console_dialog`'s layouts, `pictures intrf_hd/mainbut|popp`) plus ONE exe edit, the dial bank string `sprites/cloc` → `sprites/clock` - now the fix **`console`** (`patch_clock.py --part bank`; `--part anchors` is the composite display fix's part C, `--part all` the default for the tool chain), `Theme = 'dark'`, `Mode 'hd'`, `Requires resolution`, `Data` = the three banks (moved out of `hd_data`). The LIGHT (classic) theme = the stock metal interface: a fifth shipped picture per size, **`INTRF_HD\<WxH>\INTRFACE_LIGHT.GIF`** = `hud_layout.py build` on the stock `INTRFACE` folder (at 1024x768 byte-identical to the frame committed before the console style, Dark-Colony `6bad173`), copied as `INTRF_HD\INTRFACE.GIF`; `Write-InterfaceSet(..., $Console)` skips `$PIC_RETARGET`, `$TAB_STRIP`, `$HUD_TEXT` (`Edit-HudScript -Console $false`, `Set-BackgroundHd -Console $false`), `Edit-DialogConsole` is a no-op on `intrface/popp` scripts, `patch_music.write_dialogs(..., console=False)` / `--theme light` likewise; the light 1024x768 `MAINE`, `LOPTE`, `LQCE`, `LSGE`, `LOBJE` equal the `6bad173` files apart from line endings (fixture `hd_sets/1024x768/light/`). `Get-BuildPatches($Build, $Mode, $Theme)` drops a fix whose `Theme` differs; `Resolve-Theme` requires `-Theme light|dark` for a game at an HD size and returns '' at 640x480 (no theme there); `ReferenceSha256` has `'WxH'` (dark = every fix) and **`'WxH/light'`** (computed by reverting the `console` edit on the final bytes): 1024x768 light **Ultimate `56ead72e…`, Classic `31c215a3…`**; dark unchanged (`bc751e3b…` / `5f85cee6…`; the `console` step sits where the clock tool's bank edit sat, so the dark bytes are the same). **The options page** (step 1): the resolution as a `ComboBox` (`DropDownList`, item 0 = "(choose a screen resolution)"), under it the two theme radios (enabled only at an HD size), under it the checkbox "Also patch the deprecated Dark Colony"; Next is enabled when the size and - at an HD size - the theme are chosen; `$script:gui.SetMode / SetTheme / SetDeprecated / Refresh / Layout / StepOf` (`Visible` = the item indices with a page, `Last` = the Ready step: 4 without the deprecated build, 5 with it), the deprecated page exists but is shown only while the box is ticked, the summary and the Finished page say "NOT PATCHED - deprecated; tick ... on the options page". **`Dark Colony.exe` removed from the repository** (`git rm`, `.gitignore` entry; BUILDS `shipped=False` → `Shipped = $false`: `Published` is false for it, `-List`/`-Verify`/the CLI say "not shipped in the repository"). Tests as before plus the theme paths under pwsh 7 and 5.1 (headless: refusals without a theme, 640x480 without a theme, dark = published / light = light reference, the light set's `pictures intrface/mainbut`; CLI: `-All -Resolution 1024x768` without `-Theme` refused, `-Theme dark` = published + fixture, `-Theme light -IncludeDeprecated` = the light references, light scripts = `6bad173`, light exe keeps `sprites/cloc`). Not run in the game (the light set is the pre-console data the game ran on 28 Sep 2026).

### 10.59 Smoke test of every displayable resolution; "interface elements not in place" = a HUD script and a frame from different sizes (1 Oct 2026)

Maintainer: "do a smoke test for all resolutions. clients are complaining that some interface elements are not in place", with a player's 1920x1080 battlefield screenshot (dark theme): the right panel's tab/grid screen empty - no tab strip, no portrait, no money, no DAYS count, no hint text in the bottom bar, the chat arrows missing - and a lone cyan "000" on the map at about (1254, 735).

**Test.** One copy of the game folder without `AVI\` on `subst X:` (plus the `DWM8And16BitMitigation` layer for the two exes on that path), the patcher run from Windows PowerShell 5.1 per size (`-All -Resolution WxH -Theme dark|light -Overwrite`, the shell INSTALL.CMD uses), the written set compared with `hd_sets/<WxH>` (text with CR stripped, GIFs by pixels), then the game driven by `smoke.py` (1 Oct scratchpad; DPI-aware `SendInput`, click targets parsed from the written scripts' `pushb` lines so the same script serves every size): menu after the opening wave, ACADEMY, name screen, briefing, battle (14 s after TO BATTLE), a drag-selection over the view, tabs 2 / 3 / 1, OPTIONS, OBJECTIVES, F11 save and REALLY QUIT, `error.log` read at the end; ~65 s per size. Covered: **Dark Colony Ultimate dark at 640x480, 1024x768, 1280x720, 1280x800, 1280x1024, 1920x1080, 1920x1200; light at 1024x768 and 1920x1080; the deprecated Dark Colony build dark at 1920x1080** (3840x1080 and 5120x1440 cannot be shown on this panel). **Every element in place at every size**: tab strips at y 92..112 in the panel, the selected unit's portrait in the grid, the five Game Option cells, BUILD / DAYS / money / dial in the bar corner, the hint text and chat arrows in the bottom bar, every dialog centred on the view with working buttons; `error.log` empty throughout; exe hashes = the generator's references; written sets = the fixtures (the 1280x800 and 1280x1024 fixtures' `exp/intrf_hd/introe` had missed the 25 Sep 15 px lift, like the 1024x768 one before - refreshed from the patcher's output, the README says so). Side observation: the stock-layout dialogs (light theme, 640x480) have unlabelled tick/cross `pushb` icons, so the driver's CANCEL fallback hit YES, QUIT there and the run ended on the debrief screen - a rig limitation, the dialogs themselves are right.

**The player's screenshot reproduced.** Pairing the 1920x1080 `INTRFACE.GIF` with the 1024x768 `INTRF_HD\MAINE` gives exactly that picture: every engine-drawn HUD widget sits at the 1024x768 places - inside the 1792x1024 map view, which the terrain repaints every frame, so they are invisible - except the DAYS count (`in_text 234`, drawn on its own over the terrain for a moment) which shows as a cyan "000" floating on the map. In the player's picture the "000" sits at (1254, 735): x = 613 + 640, the DAYS text of a **1280-wide MAINE written by a patcher from before 30 Sep 2026** (the count moved to 607 that day), under a frame of 1 Oct. So the player's `INTRF_HD\` is a mixed set, not one patcher run. `Write-InterfaceSet` writes the scripts before the GIFs, so a run that fails half-way leaves a NEW script with an OLD frame, never the reverse; the mix must come from files copied between folders, a second game folder, or an older set left in place - a shadowing `exp\intrf_hd\maine`, `dc\intrf_hd\maine` or `ozi_ns\intrf_hd\maine` would do the same (none is written by any tool). **Rule: a layout complaint comes with the question which patcher run wrote `INTRFACE.GIF` and `MAINE` - the `size` line of MAINE (or the x of `picture 3`) against the GIF's width decides it in a second; a stray cyan "000" on the map is the signature.** Re-running the patcher at the player's size rewrites the whole set and fixes it.

**Follow-up, 1-2 Oct 2026:** an installer integrity check was built on this finding - a manifest of every shipped file embedded in the patcher, checked at start-up with a scrollable error popup and a download link, plus a consistency check of the installed set (frame size and theme, every script's `size` line, the HUD script's theme, the resolution and theme of each patched exe) that named the player's mix exactly - and **reverted the next day at the maintainer's word ("it doesn't solve anything but adds too much complexity"; Dark-Colony `4a9f849` + `79a8347` reverted, Server `462d86a` + `766db5c` reverted)**. What stays is the rule above: with a layout complaint, compare the player's `INTRF_HD\MAINE` `size` line with the frame's width and look for the stray "000"; re-running the patcher at their size rewrites the set.

### 10.61 One interface folder per resolution: HD_<height>P, HD_SRC, and every other size deleted (2 Oct 2026)

Maintainer: "looks like we have to absolutely isolate files for different resolutions to they own folders, so resources are never mixed. investigate, should it solve the problem with interface elements wrong placement?" -> "go ahead, build it. one more rule - when rebuilding for another resolution, delete all other resolution folders and resources. We must be sure that no there are no leftover anywhere from other resolutions resources."  The §10.59 case (a HUD script of one size under the frame of another) and every repository ZIP unpacked over an install of another size (the ZIP carried the 1024x768 `INTRF_HD\` files) were the mixing paths; the integrity check of §10.60 was reverted as too complex.

**Design.** The patched exe reads its interface data through 30 path strings whose 8-byte directory part is rewritten in place (`patch_hd_paths.py`), so a folder name has exactly 8 characters: **`HD_<height>P`** - `HD_0768P` (1024x768), `HD_0720P`, `HD_0800P`, `HD_1024P`, `HD_1080P`, `HD_1200P` - and **`UW_<height>P`** for the ultra-wide sizes (`UW_1080P` = 3840x1080, `UW_1440P` = 5120x1440; their heights would clash with the 16:9 sizes).  The exe strings and the scripts' `background` lines carry the lower-case form (`hd_1080p/bintro`, `background hd_1080p/intrface`); the Council Wars copies follow (`exp\HD_1080P\`, `dc\HD_1080P\`, `ozi_ns\HD_1080P\`).  The theme is NOT in the name: both themes write the complete set, and a run first deletes its own folder, so a dark HUD script cannot stay under a light frame either.  The patcher's INPUTS move out of the output folders into **`HD_SRC\`**: the five shipped pictures per size (`HD_SRC\1920x1080\INTRG.GIF, INTRO.GIF, BACKDROP.GIF, INTRFACE.GIF, INTRFACE_LIGHT.GIF`) and the two console banks (`HD_SRC\MAINBUT.SPR`, `POPP.SPR`; scripts say `pictures hd_src/mainbut|popp`).  `tools/hdfolder.py` is the one place that knows the rule (`hd_folder`, `hd_token`, `is_hd_folder`, `find_hd_folder`, `SRC_DIR`); PowerShell: `Get-HdFolder`, `Get-HdToken`, `$HD_SRC`, `$HD_FOLDER_RE`.

**The deletion rule** (`Remove-OtherInterfaceSets $GameDir $Keep`, run at the start of `Write-InterfaceSet` after the inputs were found, and - with `$Keep = ''` - in the 640x480 branch of `Invoke-PatchRun`): every folder matching `^(HD|UW)_\d{4}P$` or named `INTRF_HD` under the game folder and under `exp\`, `dc\`, `ozi_ns\` except the one being written, plus - for an HD build - the 640x480 copies (`exp|dc|ozi_ns\intrface\bintoze|lopme`, `GAMESTAT\HSCNDC.TXT|GSCNDC.TXT`, `INTRFACE\ONLINE|ONLINEBG.GIF`); the target folder itself is deleted and recreated, so a set is always written from scratch.  `Get-OtherInterfaceSets` lists them for the Ready page ("DELETES the interface files of other resolutions: ...") and the confirmation box ("These interface files of OTHER resolutions will be deleted ..."); every run's result reports "deleted HD_0768P\ (57 files - another resolution's interface set ...)".  Nothing else is ever deleted: the pattern is exact, `HD_SRC` and the stock folders stay.

**Exe side.** `patch_hd_paths.py --width W --height H` (or `--folder`) rewrites the 30 strings to the size's token; it recognises any earlier form (`intrface|gamestat|intrf_hd|(hd|uw)_dddd p`) and re-points it.  The `hdpaths` step is a `MODE_STEPS` member now, so the one `resolution` fix carries different bytes per size.  The ONLINE WAR module (`online.c`) no longer hard-codes `intrf_hd/onlin`: `init_screen_names` copies the 8-byte directory part of the exe's own `loadg` string (DGROUP `0x48234C`, `hd_1080p/loadg` after the fix) into `<folder>/onlin` and `<folder>\ONLINE` - through a volatile pointer with `#pragma optimize("", off)`, because the optimiser folded "exe address minus module address" into one relocated operand, which the rebasing tool rejects ("relocated operand outside the module").  Rebuilt and embedded (12 066 bytes, 295 relocations).

**Data tools.** `split_hd_data.py` (output folder from `--hd-folder` or the padded MAINE's size line, `HD_PREFIX` follows), `pad_background.py` (an HD folder is recognised by the name pattern), `build_ozi_overlay.py` (the folder found under `exp\`, `--hd-folder`), `hud_console.py apply` (banks into `HD_SRC`, `pictures hd_src/...`, the dialog copies per folder), `patch_music.write_dialogs`, `patch_movies.py` (the lists in the folder found beside the exe), `patch_online.py write_data`.  Generator: `hd_data` enumerates the repository's `HD_0768P`, `set_sources` / `console_data` name `HD_SRC`, `ozi_data` excludes `ozi_ns\HD_*P\` (a Data file that a run deletes would make `ozi` - and `online`, which requires it - "unavailable" at every other size; found by the test).

**Repository.** `git mv`-equivalent from HEAD's content (the maintainer's working-copy game folder was left untouched: the old `INTRF_HD\` and `exp|dc|ozi_ns\intrf_hd\` stay on disk, untracked and git-ignored, until the next patcher run deletes them): `INTRF_HD\<WxH>\*.GIF` and the two banks -> `HD_SRC\`, the 56 other files -> `HD_0768P\` with `background hd_0768p/` / `pictures hd_src/` lines, `exp|dc|ozi_ns\intrf_hd\*` -> `...\HD_0768P\`; `.gitignore` ignores every other `HD_*P` / `UW_*P` folder and the legacy ones.  The published Ultimate exe changes in its 30 strings and the ONLINE module: **1024x768 dark `61f6a72a…`** (staged from a clean-copy build; every other size's reference hash is in the patcher's `ReferenceSha256`); the editor `de8076dc…` unchanged; `dcexp16.asm` regenerated.  Fixtures: `hd_sets/<WxH>/HD_<height>P/` (banks dropped), `exp/HD_<height>P/`, `light/HD_0768P/`.

**Tests (2 Oct 2026):** a clean copy of the restructured index + the new patcher, PowerShell 5.1: 1024x768 dark (= the shipped set, exe = the new published hash) -> 1920x1080 light (HD_0768P and its three copies deleted, HD_1080P written) -> planted leftovers (`INTRF_HD`, `exp\intrf_hd`, `HD_1200P`, `ozi_ns\UW_1080P`, three 640x480 copies) + 1280x720 dark (all eleven deleted, HD_0720P written, exe = reference) -> 640x480 (every HD folder deleted, the eight 640x480 copies written) -> 1920x1200 dark (the copies deleted); every written set equals its fixture except the known HSCENE/GSCENE ending names; `HD_SRC` untouched throughout; the headless options-page test passes under 5.1 and 7 (console data = `HD_SRC\MAINBUT.SPR, HD_SRC\POPP.SPR, SPRITES\CLOCK.SPR`, the dark set's MAINE says `pictures hd_src/mainbut`).  **In game** (1920x1080 dark from a `subst` copy without `AVI\`): menu, ACADEMY, briefing, battle, drag-selection, tabs, OPTIONS / OBJECTIVES / F11 / REALLY QUIT all as before, `error.log` empty; the ONLINE WAR screen opens from `HD_1080P\ONLINE` (ONLINE.LOG: `screen: hd_1080p/onlin`, `connected (TLS)`) and BACK returns to the menu - the first build had lost the backslash of the probe path to a shell heredoc (`"\ONLINE"`) and fell back to the stock script; byte literals go through the Write tool, as the rule says.  Not run: the other sizes in game (their sets equal the fixtures that were run on 1 Oct), the light theme in game, a player's old install (the legacy folders are deleted on the first run).

**3840x1080 through the dgVoodoo virtual card (same day; maintainer: "for unsupported by monitor resolutions use voodoo virtual video card as you did previously ... that's for smoke test").**  The §10.32 rig had to be rebuilt - its files were in a cleaned scratchpad - and is now kept OUTSIDE the repositories, in `Dark-Colony-development/dgvoodoo/` beside CLAUDE.md (maintainer: "save dgvoodoo configuration near claude.md so it is not pushed anywhere"): `ddraw_noghost.c`, `build_proxy.cmd`, the built `DDraw.dll`, dgVoodoo's DLL as `dgv_ddraw.dll`, `dgVoodoo.conf`, README and the 9.3 MB zip (dgVoodoo 2.87.5 downloaded again with the maintainer's permission).  Two rig pitfalls on the way: dgVoodoo's template `dgVoodoo.conf` has CRLF endings and a regex edit that stopped at the CR left `FullscreenAttributes` and `ExtraEnumeratedResolutions` empty - without the extra resolution `SetDisplayMode(3840,1080,16)` fails exactly like the real driver ("Setting to 16 bit mode Failure", ddex4.c 586), and the `DWM8And16BitMitigation` layer entry must NOT be set for the dgVoodoo copy.  With the proxy's `WH_GETMESSAGE` hook the driver's clicks (frame coordinates scaled by 0.5 and offset by 330 rows) landed throughout: `UW_1080P\` written (HD_1080P deleted), menu, race screen, briefing, battle (the 96-tile map centred under the 116-tile view, `widemap`), drag-selection, tabs 1-3, OPTIONS / OBJECTIVES / F11 / REALLY QUIT, `error.log` empty.  **Result: every element in place at 3840x1080 too.**  5120x1440 is not a patcher mode (HD_SRC has seven sizes).

### 10.62 Smoke test of every resolution in both themes with the Lieutenant selected; one wrong hot-key badge in the dark theme (2 Oct 2026)

**Maintainer: "smoke test every resolution with light/dark modes and select Lieutenant to check placement of his commands on the interface. use ozi first human mission."**  Test only; nothing shipped changed.  The OZI pack's first human mission is `ozi_ns/scenario/globo/globo01` ("RETURN TO MARS", desert, the Classic HUMAN01 briefing): the player owns only the landing beacon (type 84 at tile 31,14) and trigger 8 drops `reinforce 0 31 14 0 7 69 1` at the first tick - seven troopers and ONE type-69 Lieutenant, who wears the trooper sprite (`TRSC`, same row in the Classic, Council Wars and OZI `gamestat.txt`), so he cannot be told apart on the screen.  The driver therefore reads him out of the running game: `gsread.py` scans the process for the beacon record (`X = 31·256+128`, `Z = 14·256+128`, type 84, team 0 - the record layout of `src/engine/mem.js`: `OBJ_BASE 0x7D48`, stride `0xDC`, X +0, Z +4, type +6, team +7; the 32 MiB `smalloc` pool is a `PAGE_EXECUTE_READWRITE` private region, so a scan that filters on `PAGE_READWRITE` misses it), derives `gs` from it (`MAX_OBJ` at `gs+0x7D40` in range, `MAP_PTR` at `gs+0x46F4C` a pointer), lists the team-0 objects and converts world to screen through the camera at `ui 0x4AA9D0 +0x108/+0x110` and the §10.33 rule (`screen = view origin + half view + (X − cam)/8`, y subtracts): at every size the Lieutenant is object 259 at tile (32.5, 15.5), the top-right figure of the 3x3 squad, clicked 12 px above his ground point.  The purple pulsing beacon light and the troopers' team-colour highlights are not a usable landmark (the light's colour changes every frame).

**Runs** (`smoke_ozi.py` / `runall.sh` / `chain.sh`, kept in `Dark-Colony-development/smoke_rig/` beside CLAUDE.md with the dgVoodoo rig; a `subst X:` copy of the game folder without `AVI\`, with `MUSIC\` and `exp\music\` so that `music` is not skipped; the patcher run from Windows PowerShell 5.1 per size and theme; a second copy on `Y:` with the dgVoodoo proxy for 3840x1080): Dark Colony Ultimate **dark and light at 1024x768, 1280x720, 1280x800, 1280x1024, 1920x1080, 1920x1200 and 3840x1080 (dgVoodoo, frame letterboxed into 1920x1200 at 0.5), plus 640x480** = fifteen runs, menu -> OZI MISSIONS -> name -> START CAMPAIGN -> NEXT -> TO BATTLE -> Lieutenant selected alone -> drag-selection -> tabs 2 / 3 / 1 -> OPTIONS, OBJECTIVES, F11, REALLY QUIT; ~60 s each.  **Every run: the exe byte-identical to the patcher's reference for that size and theme (1024x768 dark `61f6a72a…` = the published build, light `ceeebe98…`, 640x480 `abd40f88…`), the Lieutenant's five order cells - Stop (S), Move (M), Move & Attack, Waypoints (W), Inspire Troops (RET, the star) - in rows 1..5 of the left grid column, `LIEUTENANT` in the status screen above BUILD, the dialogs centred on the view, `error.log` empty.**  5120x1440 is not a patcher mode.  A first attempt that clicked the nine figures blind showed the same four-cell trooper panel nine times (the pod's figure and overlapping sprites take the clicks) - **select a unit through the object table, never by its picture.**  A chain script that waited with `pgrep` (not in Git Bash) started the light batch while the dark one still ran; both re-patched the same `X:` copy and the 1920x1200 dark run had to be repeated - one test copy, one batch at a time.

**Finding - the dark theme's Move & Attack cell carries the wrong hot-key letter.**  The dark `MAINBUT.SPR` (`hud_console.py`, `FROM_BUTTON`) takes cells 62, 63, 65, 66, 68, 69, 72, 73, 74, 75 from the never-shipped console bank `INTRFACE/BUTTON.SPR` as they are, hot-key badge included.  Three badges differ from the stock metal `MAINBUT.SPR`: **65 Move & Attack says `P` (stock `A`)**, **72 Napalm and 73 Disease say the return arrow (stock `D`)**; the other seven agree (S, M, W, return arrows, and the drawn cells carry the stock letters: J, RET, D, D, ESC, O, Q, D, F11).  Measured in game with the Lieutenant selected and the pressed cell read from screenshots (`hotkey_probe.py`): `M` lights the Move cell, **`A` lights Move & Attack, `P` changes nothing** - the stock letter is the live one.  Napalm / Disease were not pressed (no such unit in the mission).  **Fixed the same day (maintainer: "fix the hot-key badges and update patcher"):** `hud_console.py` `STOCK_BADGE = {65: 'A', 72: 'D', 73: 'D'}` - `button_icon(cell, key, template)` refills the key box (`KEY_BOX` + 1 px) from the ten cells' common template (the empty box) instead of copying BUTTON's letter, then draws the stock letter with `key_glyph` in BUTTON's key greys, the same glyphs the drawn cells (O, Q, D, J) carry; the other seven FROM_BUTTON cells are byte-identical to before.  Regenerated: `DC - Council wars/HD_SRC/MAINBUT.SPR` (the one shipped copy; md5 `d46addcf…` -> `a419cc8b…`, exactly cells 65, 72, 73 changed), the 1024x768 frame re-rendered and compared equal (the bank is the only output of the change), the patcher regenerated with `gen_apply_script.py` and came out byte-identical (the bank is a `Data` resource of the `console` fix, not script content, so no exe hash or edit changed).  Confirmed in game at 1024x768 dark: the Lieutenant's Move & Attack cell shows `A`.  The `hd_sets` fixtures hold no bank (it ships once in `HD_SRC`).  Committed and pushed 2 Oct 2026: Dark-Colony `98c1f43`, Server `4583452`.


### 10.63 Selection marker 16 px too high, ground clicks half a tile off: the camera tile snap at odd view heights (2 Oct 2026)

**Maintainer: "why lieutenant mark and units life indicator are too high on 1024 resolution above the unit itself? compare with 1920 resolution".**  Measured on the 10.62 screenshots (marker bottom row to helmet top, the Lieutenant selected): **34 px at 1024x768, 1280x720 and 1280x1024, 18 px at 640x480, 1280x800, 1920x1080, 1920x1200 and 3840x1080** - the stock gap is 18, the three sizes are 16 px off, and they are exactly the sizes whose view is an odd number of tile rows (23, 21, 31; every mode's width is an even tile count).  A correlation of the terrain around the squad between sizes showed terrain AND sprites 16 px lower than the pixel-exact frame at those sizes, the marker in the exact frame.

**Cause.**  The camera is the view centre; after the per-frame clamp (`clamp2d`, `0x40AF16`) its low bytes are zeroed - `mov byte ptr [ui+108h],0` / `mov byte ptr [ui+110h],0` at `0x40AF1E` / `0x40AF2B` (Classic; Ultimate +0x60), the tile snap.  The renderer's origin is `camera +- half` with the patched half-viewport constants (`0xB80` = 11.5 tiles at 1024x768, Stage 3), so with a camera at `.00` the origin lands on a half tile; the tile renderer (`>> 5`, section 3) floors it and draws the terrain and the objects from whole tiles, while the selection marker / health bar and the mouse pick (`0x409574`, `rect.h / 2` from the view rect) work in the exact frame.  At an even tile count half is whole tiles and nothing is lost (the stock 14 rows, 7 tiles).  Consequences at the odd sizes: the marker floated 16 px higher above every unit (the maintainer's report) and a ground click issued its order half a tile north of the clicked spot (the unit pick has tolerance, so units were still selectable).

**Fix (`patch_resolution.py`, two stage-3 sites in the `resolution` fix, 2 Oct 2026):** the snap byte becomes **`0x80`** on an axis whose tile count is odd (`g.tiles_x % 2` / `g.tiles_y % 2`), so the camera sits on a half tile and `camera +- half` is a tile boundary again - every consumer in one frame; on an even axis the site writes the stock `0` and nothing is emitted.  One byte per exe at 1024x768, 1280x720 and 1280x1024 (Classic file `0xA331`, Ultimate `0xA391`), none at the other modes.  The clamp bounds `[half, map - half]` already end in `.80` at those sizes, so the clamped camera reads 11.5 ... 72.5 tiles on the 96x84 map (measured through `gsread.py` while scrolling to all four edges with the arrow keys; alive, `error.log` empty, a ground click at the edge fine); the vision / ambience rect (`(cam - half) >> 8`, `0x40AB1C`) now covers rows `t-11 ... t+11` exactly.  **Confirmed in game at 1024x768, 1280x720 and 1280x1024 (dark): the gap is 18 px like stock**, the Lieutenant's exact screen point moved 16 px down onto his sprite.  Patcher regenerated (1 757 850 bytes): `resolution` has 200 / 199 edits at the odd sizes; references **Ultimate 1024x768 dark `b1725e95...` (the published exe, rebuilt in the game folder), Classic 1024x768 dark `24de980e...`**, Ultimate 1280x720 `48ecca5e...`, 1280x1024 `2c5df19d...`; the even sizes' hashes unchanged.  `dc16.asm` / `dcexp16.asm` regenerated from the 1024x768 reference builds (the Classic one built in a scratch copy with the three `DC*.AVI` present - without them the patcher skips `movies` and the build is not the reference).  Rig: `scroll_probe.py` in `smoke_rig/`.  Committed and pushed 2 Oct 2026: Dark-Colony `20904d3`, Server `4d4c82c`.  **Full smoke rerun after the push (maintainer: "rerun the full smoke test at 1024x768 and 1280x1024 with the fix"):** both sizes, dark and light, from the pushed patcher - exes = references (1024x768 light `fc50a224...`, 1280x1024 light `1e160482...`), the whole 10.62 sequence (menu, briefing, battle, Lieutenant, drag-selection, tabs, four dialogs), marker-to-helmet gap 18 px in all four runs, `error.log` empty.  **Lesson: a view of an odd number of tiles on an axis needs the camera on a half tile; compare the marker-to-sprite gap with stock whenever a new size is added.**

### 10.64 Tracer bullets for the human trooper and the Lieutenant; the Gray trooper's bolt at every weapon level (2 Oct 2026)

**Maintainer: "multiple units are throwing a projectile. investigate how much effort would be to add tracer bullets
with light tails for human and alien troopers" -> "alien trooper already have a projectile visible with glow on the
soil. do they loose this animation when upgrading weapon?" -> "ok, fix alien trooper weapon upgrade sprite problem.
and implement tier 1 for human trooper only" -> (watching the test) "i see that lieutenant dont have a tracer".
Data only, no exe byte; `tools/tracer.py`; confirmed in game at 1024x768 (Ultimate, OZI `globo01`).**

**How the engine draws a projectile.** The weapon loader `0x43B6EC` fills `weapon+0x2C` with the animation set
`<sprite>BULLET` when an animation `<sprite>BULLET0` exists (sprite = WEAPSTAT.TXT column 2); `create_missile`
`0x44192C` draws its random byte first and starts an animation instance in the missile record (`+0x20`) only
when that pointer is non-NULL; the missile drawer `0x439E88` (Ultimate +0x60; called from the client display
right after the object pass `0x4396D4`) queues, for every active missile with an animation, launch delay 0,
inside the map and inside the local player's vision mask, the cells of `set[heading >> 3]` through the sprite
queue `0x436128` (800 entries per frame, silently dropped beyond). The animation-set loader `0x426014` takes up
to 16 file facings `<name>BULLET<n>` with `n = (12 - i) & 15` for the set index `i = heading >> 1`, heading
0 = +x (east), 8 = +z (north, screen up), counter-clockwise; so file facing n points 270 - 22.5 n degrees:
0 south, 4 west, 8 north, 12 east (checked in game by the streak direction). **A bullet sprite is display
only**: the sync checksum `0x44ABC0` covers the missile COUNT and the objects, not animations or missile
positions, and no `rand()` depends on the bullet pointer - a player with this data and one without stay in sync,
and the server engine port needs no change (`src/engine/missile.js` line 210 does the same conditional
`startAnim`). What does feed `rand()` is the explosion sprite count (`weapon+0x40`, the hit path draws a random
explosion only when it is non-zero), so **a weapon's EXPLODE lookup must never change** - no `TRACEXPLODE`,
and weapon 5 keeps its sprite name (see below).

**Why troopers fired invisibly.** Weapons 1-3 (human trooper, TRSC type 0, upgrade levels 0/1/2) and 16/17
(Gray trooper type 8, levels 1/2) have the sprite name `weapons` in `GAMESTAT/WEAPSTAT.TXT`, and no FIN bank
defines `weaponsBULLET0` (the twelve BULLET animations that exist: BARR, BARR2, PUS, GRAY, BANG, EGG, TOXX, UA,
CMNDR, ZIMAL, SPIKE, TURR, XENO). Only the Gray level-0 weapon 15 says `GRAY`: `GRAYBULLET0` (GRAY.FIN frame
421) is two cells, the glow `glit 13` (8x8, draw mode 5) and the ground glow `smsp 0` (32x20 intensity ellipse,
draw mode 3 = the "glow on the soil", drawn in the blitter's ground pass before the sort). So the Gray bolt
vanished with the first weapon upgrade - a stock table oversight, not a design.

**Cell draw modes and registration (measured; the `.SPR` directory's yoffset plays no part in the position).**
The in-memory FIN cell record is `{bank*, u16 cell, i16 x, i16 y, a, b, mode, d}` (20 bytes; file record 22
bytes `char[8] bank, u16 cell, i16 x, i16 y, u16 a b mode d`); `mode` (file word 7) is the queue entry's byte
`+0x17` = the jump table `0x4543E4` (0..5; 3 = ground pass `0x46A0BC`, 1 = plain unit cells, 5 = the glow
cells), `a` = queue `+0x15` (2 = the "lit" flag `0x536410`, like height != 0: the blitter uses the constant
light 0xF0 instead of the lightplane), `d` = queue `+0x18`. A cell's LEFT edge is `x + xoffset` and its BOTTOM
edge `y` from the object's ground point (the culling code in `0x4543FC` compares `x + ox` and `y - h`; units
stand on their point: TRSC STAND `y 4, h 45`; the glow `x -139, xoffset 136, y 4, 8x8` is centred at (+1, 0));
the missile's height lifts the whole frame. The first build registered the streak with `y - yoffset` and the
vertical facings rode 24 px high - measured in game, fixed, re-measured.

**The fix.** `tools/tracer.py plan|apply|verify GAME` (+ `preview OUT.png`): (1) `SPRITES/TRAC.SPR`, 16 streak
cells (bright pale-yellow head, 26 px tail fading yellow -> amber -> orange -> red behind it, half-width 1.7 ->
0.55 px, ordered dither at the end; palette 68/70/72/75/78/80/82, none of the team ramp 128..143), one per
file facing; (2) `ANIMATE/TRAC.FIN`: banks `trac, glit, smsp`; animations `TRACBULLET0..15` AND `SMOKBULLET0..15`
(the alias for the Lieutenant's pistol, weapon 5 `SMOK`, found by the loader through the name - no table edit, so
`SMOKEXPLODE` and its rand() stay) on the same 16 frames, each = the Gray bolt's two cell records byte for byte
(`glit 13 -139 4 0 16 5 0`, `smsp 0 -59 22 0 16 3 0`) plus the streak cell registered with its head pixel on the
glow's centre (`x = 1 - hx - 136`, `y = h - hy`, `xoffset/yoffset` = the glow's 136/116); duration 0 = 2 ticks
like GRAYBULLET0; (3) the weapon tables: the ROOT `GAMESTAT/WEAPSTAT.TXT` and `ANIM.DAT` stay byte-identical for
the original exes; the patched Ultimate reads its tables through the mode prefix, so `dc/gamestat/weapstat.txt`
(DARK COLONY, ACADEMY, network, ONLINE WAR) and `exp/gamestat/weapstat.txt` (COUNCIL WARS) are copies of the root
table with weapons 1, 2, 3 -> `TRAC` and 16, 17 -> `GRAY`, and `ozi_ns/gamestat/weapstat.txt` (the pack's own
table) gets the same five edits in place (`build_ozi_overlay.py` applies `tracer.fix_weapstat` when it
regenerates the overlay); (4) `exp/animozi.dat` + `trac.fin` (`build_ozi_overlay.EXTRA_FINS`; the patched exe's
start-up list - the stock `exp/anim.dat` never loads it). The untouched `ENGEXP16.EXE` does read
`exp/gamestat/weapstat.txt` when present: it then draws the Gray bolt at every level and still nothing for TRAC
(no `trac.fin` in its list; a missing `BULLET0` is silent) - **the second deliberate data exception** after
`exp/intrface/bintroe` (section 10.35); the deprecated Dark Colony exe reads the root tables and gets nothing.
Patcher: the four files joined the `ozi` fix's `Data` list (`gen_apply_script.ozi_data`), no exe edit, hashes
unchanged, patcher regenerated. The bullet moves 4 x 60 = 240 units = 30 px per game tick (four
`update_missiles` passes in `0x442B50`), so the 26 px tail bridges the jumps between frames.

**Test** (`smoke_rig/tracer_test.py`, 1024x768 dark, `subst X:` copy without `AVI/`, layer entry set and
removed): OZI MISSIONS -> `globo01` -> the squad of seven troopers + Lieutenant found through the object table
(`gsread.py`, records at (0,0) are unused slots - the first run's centroid of them put the camera outside the map
and the vision scan `0x439DBE` read a NULL row pointer: an external camera write must be clamped to the bounds the
battle-start fix keeps at `ui+0x114..0x120`), the enemy Grays' upgrade level poked to 2 through
`object_types[8]+0x30+team` (`0x50FCF8`, same address in both exes), Move & Attack towards the nearest enemy,
camera on the squad, ~4 frames/s with the live missile list read before and after each grab. Evidence: missiles
of weapon 1 (46 observations), 5 (153, all with an animation - before the alias 21 of 99 were without, the
flying ones) and 17 (178) carry an animation; weapon-1 and weapon-5 bullets show the yellow streak, weapon-15
and weapon-17 bullets the stock white glow with the orange ground smear, all at the bullet's point within the
rig's one-tick capture jitter (30 px along the flight direction); `error.log` empty. The ramp-colour detector
also fires on the mech unit's orange shoulder lamps - judge placement from the per-missile crops
(`shots/tracer_missiles.png`), not from colour counts. Not run: other sizes, the light theme, a network game,
Dark Colony campaign missions (same data path through `dc/`).

**Lessons.** (1) The first "displaced streak" reading was wrong twice over: the white starbursts next to the
streaks were enemy bolts and Lieutenant hits, and a bullet captured 50-100 ms after the memory read has moved a
tick - compare against a stock projectile measured the same way before changing a registration rule. (2) A
sprite's placement rule is `left = x + xoffset, bottom = y`; derive it from the culling code and a unit's STAND
record, not from one glow cell. (3) Scratch heredocs keep mangling `b'\x02'`-style literals: edits with
backslashes go through a script file (the 25 Sep rule stands).

**Same day, the Gray commander (maintainer: "do alien leader have a visible projectile?" -> "go with option 2,
gray bolt for alien leader").** The Gray commanders (types 73-76) fire weapon 62, "ALIEN commander weapon",
whose sprite name is `SMOK` like the Lieutenant's weapon 5, so the `SMOKBULLET` alias had given them the human
yellow streak as a side effect (stock: invisible). Renaming weapon 62 to `GRAY` would have dropped its hit
animation (`SMOKEXPLODE`, one sprite) and with it a `rand()` call per hit = desync, so weapon 62 gets a name of
its own in the three overlay tables, **`SMOG`** (`WEAPON_SPRITES[62]`), and TRAC.FIN declares both lookups the
loader makes for it: `SMOGBULLET0..15` = the bare Gray bolt (glow + ground glow, no streak; frames 16..31) and
`SMOGEXPLODE0` = `SMOKEXPLODE0` copied byte for byte from TURR.FIN frames 184..191 (bank `ssss` cells 0..6,
durations 6 0 13 20 26 20 13 6, record extras 12 16 5 0; frames 32..39; `PISTOL_HIT`), so the explosion count
stays 1 and the hit looks the same. FIN: 4 banks (`trac glit smsp ssss`), 49 animations, 40 frames; `verify`
compares the parsed FIN against `fin_plan()`. **Loader probe in game** (`smoke_rig/weapon_probe.py`: OZI globo01
battle, `weapon_types[]` at `0x50E678` read through `ReadProcessMemory`, same address in both exes): weapon 62
bullet set `0x4D26D4`, explosions 1 (= weapon 5: set `0x4D0DD4`, explosions 1); weapons 1/2/3/16/17 have a set,
15 unchanged; 7 none, 10 two explosions, `error.log` empty. The commander's own shot was not watched (no Gray
commander in globo01); the upgraded commander levels use weapons 8/10/12 (mech gun, artillery shell) on both
sides, untouched.

**Same day, the streak lifted to rifle height (maintainer: "human lieutenant projectile have wrong offset" ->
"wrong offset is visible the most when shooting horizontally").** A missile flies at height 0, i.e. at the
shooter's ground point, and the first frames registered the streak on the Gray bolt's centre, so the tracer ran
along the shooter's FEET - invisible on shots up or down the screen, obvious on horizontal ones (the rifle is
~22 px above the feet). Now `GUN_LIFT = 22`: the streak's head and the bullet glow (`BULLET_GLOW` = the
`glit 13` record with `y 4 -> -18`) sit 22 px above the ground point, the ground glow `smsp 0` stays on the
soil; the `SMOG` frames (Gray commander) keep the stock bolt records unchanged. Measured in game before and
after with `smoke_rig/record_run.py` (frames + camera + missile list before/after each grab, analysed offline):
streak heads on the bullet point in every facing, then on the rifle line of the firing trooper on horizontal
shots. **Rig lesson - the camera snap:** reading the camera right after writing it gave the UNSNAPPED value;
the game snaps x to a whole tile and z to `.80` at 1024x768 before drawing, so the projection was off by the
remainder (184 units = the "23 px right" of the first Lieutenant probe, which was no sprite error at all).
Snap before projecting: `cam_x & ~0xFF`, `(cam_z & ~0xFF) | 0x80` (odd row count), or read the camera a frame
after the write. The offline recorder makes this class of measurement repeatable without another game run.

### 10.66 Tabs 1/2/3 and the DAYS count "lost after patching": the patcher followed the player's Windows regional format (2 Oct 2026)

**Report** (maintainer, with the player's folder as a ZIP and a 1920x1080 screenshot): after patching, the three tab
digits over the button grid and the DAYS count are gone; a cyan `000` floats on the terrain. The §10.59 signature
of a mixed set - but the player's exe was byte-identical to the patcher's 1920x1080 dark reference and the
`HD_1080P\` set had been written minutes earlier by the patcher of commit `cc633d5`, so no file had been
copied between sizes.

**Finding.** The player's `HD_1080P\MAINE` had every panel widget shifted by (640, 300) and `size 0 0 1920 1080` -
the generic letterbox rule for a MENU screen - with the tab pictures left at 110x12 and `in_text 234` at
(1253, 733) = 613 + 640 / 433 + 300: the HUD script had NOT gone through `Edit-HudScript`. `BINTROE`,
`DINTROE`, `INTROE` were letterboxed the same way instead of relaid out by `Edit-IntroScript`, and the stray
`MULTIE~1.TXT` that the set never contains was written; `BUTTONSE` - the one intro screen without the letter I -
was right. `Write-InterfaceSet` chooses its branches by `$name.ToLower() -eq 'maine'`,
`$introScreens -contains $lname` and `$lname -eq 'multie~1.txt'`, and .NET's `ToLower()` follows the thread's
culture: under a Turkish or Azerbaijani regional format `I` lowercases to the dotless `ı` (U+0131), so `MAINE`
becomes `maıne` and misses every comparison. The player's progress text said `3,7 s` (decimal comma), the
regional format's other trace.

**Proof.** The zip's own patcher run on a second copy with
`[Threading.Thread]::CurrentThread.CurrentCulture = 'tr-TR'` (PowerShell 5.1) wrote a set byte-identical to the
player's, all 65 files; with the 14 `.ToLower()` / `.ToUpper()` calls replaced by their Invariant forms in a
scratch copy, the same run gave the fixture set under 5.1 and 7.

**Fix (patcher 1.1, `gen_apply_script.py`):** every case conversion in the generated script is
`ToLowerInvariant()` / `ToUpperInvariant()`, and the script sets the thread's `CurrentCulture` and
`CurrentUICulture` to `InvariantCulture` right after `Set-StrictMode` - for the window and the command line alike -
so string comparison, hashtable keys, regex IgnoreCase and number formatting cannot depend on the player's
Windows settings either. Reference hashes unchanged (no exe byte depends on the culture). Verified on a clean
`git archive HEAD` copy: 5.1 under tr-TR at 1920x1080 dark and pwsh 7 under tr-TR at 1024x768 dark = fixtures
(HSCENE/GSCENE carry the DC ending names because the copy has the DC movies) and reference exes; 5.1 under the
default culture at 1024x768 light = the light fixture; the headless window test passes under both shells (its
1 Oct harness still looks for `INTRF_HD\MAINE` - two stale checks). Committed and pushed 2 Oct 2026: Dark-Colony `37542ea`, Server `df3d7f6`.

**Rules.** (1) A patched set that is wrong although the exe is the reference and the set is freshly written:
ask for the player's Windows regional format before anything else; the dotless-i cultures are tr-TR and az-Latn.
(2) The patcher is a byte-exact generator: nothing in it may use a culture-sensitive operation - `ToLower()`,
`ToUpper()`, `-f` on fractional numbers, `Sort-Object` on text that reaches a file - and every patcher change is
checked once with the thread culture set to `tr-TR`
(`powershell -Command "[Threading.Thread]::CurrentThread.CurrentCulture='tr-TR'; & .\Apply-DarkColonyPatches.ps1 -All ..."`).
(3) Inside a PowerShell function, `$args` is the automatic argument array - a parameter of that name is empty
and the patcher started without arguments opens its window (one lost test run that evening).

### 10.67 One LOAD GAME for every campaign: the saves of save/, esave/ and ozisave/ in one list, the menu five rows (3 Oct 2026)

**Request** (maintainer): "main menu. we must have only one 'LOAD GAME' button which will serve for saves for all
campaigns. place it as last button in left column. Game loading form use the same as 'online war' must have a format
[date]+[time]+[name]+[campaign name('Academy','Dark Colony','Council wars','Ozi missions')]. savefiles must remain in
they folders."

**How the stock LOAD GAME works** (`main.c` / `proto.c`, Ultimate addresses; Classic is +0 below 0x405000). The button
handler (id 2, `0x4050BF`) reaches `0x403AA4(ui, gs)` - through `tramp_cw_load` since §10.13, which sets the Council
Wars mode first. That routine calls the **picker screen `0x40388C(eax = ui, edx = buf, ebx = 0x40; cl = 0)`** (`cl = 1`
is the SAVE form the mission-won screen uses from `0x403B4C`): it lists the save folder named in the DGROUP slot
`0x482344` through the game's own directory walker (`0x42B31C`: opendir, every name ending in the extension slot
`0x482340` = `dcg`, extension stripped), shows `intrface/loadg` + E, and on LOAD returns `al = 1` with
`<folder>/<name>.dcg` in `buf` (`0x406CB8` = `%s/%s`, the extension appended when the name has no dot). `0x403AA4`
then opens that path through the mode prefix helper (`0x40E1F4` -> `0x406488` -> `0x4063E4`: prefix + path first,
the bare path when that fails - the same helper the save writer `0x40D4C8` uses with `wb`, which is why every save
sits in one of the three root folders: `exp/esave`, `dc/save`, `ozi_ns/ozisave` do not exist), reads it
(`0x40DBFC`), and - `gs+0x14F0` being 0 or 3 - resumes the campaign through `0x401C08`; a failed read shows
message 8 and returns to the menu. **The save header** (`0x40D4C8` / `0x429D2C` write it, `0x40DBFC` / `0x429EF8`
read it): `"DCSF"`, `u16 0x1D`, `u32 0x21340003`, then nine dwords of the campaign record - `[0]` the race byte,
**`[1]` `gs+0x14F0` = the game type (0 campaign, 2 network game, 3 training)**, `[2]` `gs+0x14E4` the side, `[3]`
`gs+0x14FC` the mission, `[4..7]` the settings `gs+0x1984..0x1990`, `[8]` `gs+0x14F4` the pack flag - then the
leader's strings and the per-mission records; an in-battle save continues with `0x21340000` and the battle state,
a between-missions save ends with `0x2134FFFF`. So the campaign record IS restored from the file (the open question
of §10.36 answered), and the first 46 bytes of a `.dcg` tell an ACADEMY save (type 3) from a DARK COLONY one (0) in
the shared `save/` folder; the folder tells the rest.

**Design.** The three campaign-specific load buttons (LOAD DC GAME 7, LOAD CW GAME 2, LOAD OZI GAME 4) differed only
in the mode stub run before `0x403AA4` - `stub_dc_set` / `stub_cw_set` / `stub_pack`, which set the prefix, the save
folder and the music source. One button therefore needs one picker that (a) lists all three folders, (b) labels each
save by folder and game type, and (c) runs the chosen save's stub before returning the path. Everything after the
picker - the open through the prefix helper (which falls back to the bare path, so the mode can be any), the read,
the campaign runner, the "load failed" box, the network-save branch - stays the stock code. The picker is a third
entry point of the `online` module (§10.51): **the one code edit is the rel32 of the `call 0x40388C` at `0x403ABC`
-> `load_game_picker`** (`patch_online.py` `PICKER_CALL`; 6 edits + the appended section now), a naked thunk that
hands the Watcom arguments to cdecl `load_picker(ui, buf, cap)` for `cl = 0` and jumps to the stock picker for the
save form (`push 0x40388C; ret`, no register touched). The stock picker, the LOAD DC GAME / LOAD OZI GAME handlers
and their trampolines stay in the exe, unreachable from the menu.

**The module side** (`online.c`, 18 899 bytes, exports `load_game_picker +0x48F0`, `online_dispatch`, `online_war`,
`replay_game`): `scan_saves` walks `save\*`, `esave\*`, `ozisave\*` with `FindFirstFileA` (the extension is checked
in code - a `*.dcg` pattern would also match longer extensions through the 8.3 names; names longer than the in-game
dialog's 32 characters are skipped), reads each file's first 46 bytes for the type, keeps the table sorted newest
first by `ftLastWriteTime` (at most 200), and `build_rows` formats **`dd.mm.yy hh:mm  <name, 26 columns>  <campaign>`**
= 8 + 1 + 5 + 2 + 26 + 2 + up to 12 = the list's 56 columns, local time via `FileTimeToLocalFileTime`; the campaign
is `Council wars` (esave), `Ozi missions` (ozisave), `Academy` (save, type 3), `Multiplayer` (save, type 2 - a
network game saved in battle; the stock code resumes it through `0x40122C` as a solo host game, §10.68 - since
fix `netsave` of the same day such a save can no longer be made), `Unknown` (no save header), else `Dark
Colony`. `load_screen` shows **`LOADALLE`** (`<folder>/loadall`, probed like ONLINE / REPLAYE, `intrface/loadall`
at 640x480) = the ONLINE script with the title `Load Game` and the button `LOAD` (`patch_online.loadall_script`,
the patcher's `Edit-LoadAllScript`; ONLINEBG.GIF is shared, no new picture), fills the header line `DATE TIME NAME
CAMPAIGN`, the list, and the three text lines - `Saves: N (save a, esave b, ozisave c)`, `File: <folder>/<name>.dcg`
of the selection, the state (`Select a save and press LOAD.` / `No saved game found.` / `Select a save first.` /
`Loading <name>...`) - and on LOAD writes the path into the caller's buffer, calls the folder's stub and returns 1;
BACK returns 0 and the stock code goes back to the menu. `ONLINE.LOG` gets `--- LOAD GAME pressed`, `saves found N`,
`LOAD <path>`.

**The menu.** `build_ozi_overlay.OZI_COLUMNS = ((1, 6, 0, 16, 2), (8, 9, 3, 5, 12))`, one gap after row 4
(`OZI_GAP_AFTER`), `OZI_DROPPED_BUTTONS = (4, 7)`: their `pushb`, their plates (gadgets 10 and 22) and `textmsg 10`
leave the script, `textmsg 3` is the stock `LOAD GAME` again, `textmsg 5` the stock `SINGLE PLAYER WAR` (so the tool
on an older output and the patcher on the stock script give the same bytes), `banim` pairs ten plates in wave order
(20 21 19 17 24 | 23 26 25 11 13); `menu_layout` accepts its own output (the dropped ids are not "missing", 10 pairs).
The block rises 116 rows instead of 192, so it hangs from the title at every size and **the credits box keeps its
stock 100 rows everywhere** (`patch_resolution.OZI_BLOCK_RISE` 116; `cw_credits_height` is 100 at every size and
the `push 64h` site is skipped - the `resolution` fix of Ultimate lost that edit at 1024x768 and 1280x720, Classic
is untouched). 1024x768: rows 495 / 521 / 547 / 573 / 611 at x 327 and 518, the box 386..485, 9 px above the first
row. The patcher's `Edit-OziMenu` is the byte-identical port (checked against `menu_script` on the stock script and
on the previous layout's output, pwsh 7 = PowerShell 5.1 under `tr-TR`). The 640x480 block keeps the box removed
(§10.36; the five rows would leave room again - not changed).

**Patcher 1.3** (`gen_apply_script.py`): `Write-OnlineScreen` writes `LOADALLE`, `$STOCK_COPIES` and `hd_data` know
it, the `online` fix's name and description, the `ozi` and deprecation texts; `.gitignore` of the game repository
lists the 640x480 copies `INTRFACE\LOADALLE` (and the `REPLAYE` / `REPLAYBG.GIF` copies that had been missing from
it). References: Ultimate 1024x768 dark **`35346be9…`**, light `6accd206…`, the other sizes in the script;
Dark Colony (`ae34e1d5…`) and the editor unchanged. A clean `git archive HEAD` copy patched with `-All -Resolution
1024x768 -Theme dark -Overwrite` under pwsh 7 and under PowerShell 5.1 with the `tr-TR` culture gave the reference
exe and identical 74-file sets (= the fixtures except the three `bintroe` and the new `LOADALLE`, which the fixtures
of all six sizes took from these runs; HSCENE/GSCENE differ by the DC ending names as always).

**In game (3 Oct 2026, 1024x768 dark, the clean copy on `subst X:`, `smoke_rig/saveload_test.py`).** Phase `make`:
ACADEMY, DARK COLONY, COUNCIL WARS and OZI MISSIONS each entered to their first battle, F11, a name typed into the
console save dialog, OK - `save/acad1.dcg` (header type 3), `save/dc1.dcg` (type 0), `esave/cw1.dcg`,
`ozisave/ozi1.dcg` (both type 0, pack flag 1 - the COUNCIL WARS handler sets `gs+0x14F4 = 1` as OZI does, which is
why the folder and not that flag decides the label). Phase `load`: LOAD GAME lists the four newest first
(`03.10.26 14:32  ozi1  Ozi missions` / `cw1  Council wars` / `dc1  Dark Colony` / `acad1  Academy`, `Saves: 4 (save 2,
esave 1, ozisave 1)`, `File: ozisave/ozi1.dcg` for the highlighted row; list rows 13-14 px apart), each row loaded
in turn (`ONLINE.LOG`: `LOAD ozisave/ozi1.dcg`, `esave/cw1.dcg`, `save/dc1.dcg`, `save/acad1.dcg`), each battle
came back as saved (the OZI landing squad, the Council Wars jungle start, the Dark Colony and Academy HQs), and a
save made right after each load landed in the loaded save's folder (`ozisave/ld0`, `esave/ld1`, `save/ld2`,
`save/ld3`) - the mode followed the pick; BACK returns to the menu; `error.log` empty throughout. Rig lessons: YES,
QUIT in a campaign battle shows the Defeat screen, whose MENU goes back to the campaign's own race / name screen -
its BACK reaches the main menu; the leader-name field keeps the previous text and typing appends; and a test that
saves after every load must delete those saves before the next pick, or the newest-first list shifts under the row
index (the first run loaded the same save four times). Not run: 640x480, other sizes in game, a between-missions
save, a multiplayer save.

**Same day (maintainer: "rename 'multiplayer war' to 'CUSTOM NET WAR'"):** the button id 3 of the patched Ultimate menu is labelled **CUSTOM NET WAR** (`textmsg 4`; `OZI_LABELS[4]`, the patcher's `Edit-OziMenu` `$labels[4]`, the DEFAULT_SERVER.TXT comment) - a label only, the handler (`tramp_dc_net` -> the network screen) is unchanged; the stock `exp/intrface/bintroe` the untouched exe reads keeps MULTI PLAYER WAR. The six fixtures' and the game folder's menu scripts regenerated; docs written before this say MULTI PLAYER WAR for the same button. **Committed and pushed 3 Oct 2026 (both changes): Dark-Colony `4138b49`, Server `3c5723c`.**


### 10.68 No save in a network battle: the Save Game cell hidden, F11 inert (3 Oct 2026)

**Question (maintainer: "investigate - when playing network game there is a 'save' button available. is this
saved mission visible in main menu 'LOAD GAME' form?"), then the instruction ("hide the save button in network
battles and update patcher").** Yes, it was listed. The battlefield save dialog (`0x00432708`, Ultimate
`0x00432768`; reached from F11 through the client's event table at `0x0040A63B` / `+0x60` and from the Game
Option tab's cell 63 through the dialog handler's `cmp edx,3Fh` at `0x00433887` / `+0x60` - the `pushb` id
doubles as the key code the handler switches on: `>` 62 quit, `?` 63 save, `@` 64 options) has no game-type
check and sends nothing over the network; CUSTOM NET WAR and ONLINE WAR run in the Dark Colony mode, so the
file landed in `save\<name>.dcg` with game type 2 at header offset 14, and the §10.67 picker labelled it
`Multiplayer`.

**What the stock code does with such a save.** The load routine `0x00403AA4` sets `gs+0x1580 = 1`, copies the
path into `gs+0x1581` when the file holds a battle (`gs+0x1981`, set by the reader at `0x0040DCF9` for the
`0x21340000` marker), and for a game type other than 0 / 3 takes the branch at `0x00403B30`:
`0x0040122C(ui, addr = 0, gs, net = 0; push 1)`. A null net builds the in-process mailbox network
(`0x0040BFB0`, `local.c`), the flag makes `0x0040B4B0` create the host's server object on it, the non-empty
path skips the lobby and the start-position shuffle (`0x004014EB` -> `0x00401765`), the battle state is
re-read at `0x004017C5` and the game starts at `0x0040195B` with every seat as saved - the other humans still
human-typed, unconnected, never disconnected, so their bases stand still; the relay's bots are not in the file
at all; no socket is opened and the CONNECT screen never appears. The wire protocol has no save or resume
message, so the relay cannot know of it. A `Multiplayer` row in LOAD GAME was therefore a solo continuation
against frozen opponents.

**Fix `netsave`** (`tools/patch_netsave.py`, both games, `Requires nocd`, patcher order right after `chat`;
6 edits per exe: two 5-byte hooks, two stubs, two `.reloc` words; Ultimate code at +0x60):

* **The widget record** at `ip+0x88+0x34*id`: `+0` the greyed byte (`set_greyed` writes it, the drawer copies
  it into the display's draw mode), `+1` the type, **`+2` shown**, **`+3` enabled**, `+5` a flag `0x00424468`
  (Classic `0x00424408`) sets for the message bar 148. The group/tab switch (`0x004243CC` Ultimate / `0x0042436C`
  Classic, called per member by `0x00424374`) writes `+2` only; **`0x00424488` (Classic `0x00424428`) =
  `widget_enable(ip, id; bl)` writes `+3`** and erases or redraws as needed; the widget drawer `0x00421B04` /
  `0x00421AA4` and the push-button hit test `0x00426D8C` / `0x00426D2C` both require `+2 && +3`. `MAINE`'s
  `group 65 0 202 62 63 64 151 196 5` is the Game Option page; the game start already uses `+3` to hide the
  Allies cell 151 in campaign games (`0x0041ECF4` / `0x0041EC94`). **Rule: hide a widget for good through `+3`;
  `+2` comes back with the next tab switch.** (The 110-entry availability table at `.bss 0x5049F0`, 0x34 per
  entry, drives `+3` of the production buttons from `0x00438018`; it never names cell 63.)
* **Hook 1**, the end of the game start's network branch (`0x0041ECE1` Ultimate / `0x0041EC81` Classic:
  `mov edx,94h ; mov eax,[hud_ip 0x4AB1C4] ; xor ebx,ebx ; call set_flag5 ; jmp +11h`, 19 bytes) -> `jmp stub_a ;
  14 x nop`; the displaced operand's HIGHLOW `.reloc` entry becomes type 0. **`stub_a`** (55 bytes) repeats the
  call and, when `[ebp-4]` (the battle state) `->+0x544` (the campaign record) `->+0x14F0 == 2`, calls
  `widget_enable(ip, 63, 0)`, then jumps to the continuation (`0x0041ED05` / `0x0041ECA5`). Types 1 (skirmish)
  and 2 share that branch; only 2 loses the cell.
* **Hook 2**, the dialog's first five bytes (`push ebx,ecx,edx,esi,edi`) -> `jmp stub_b`. **`stub_b`** (34 bytes,
  eax = client): `push ecx ; mov ecx,[eax+0Ch] (gs) ; mov ecx,[ecx+544h] ; cmp dword [ecx+14F0h],2 ; pop ecx ;
  je ret ; the five pushes ; jmp dialog+5 ; ret` - F11, the `?` key and the cell all end here, and both callers
  ignore the result (`0x0040A640` goes on to `0x00432314`, `0x00433893` clears `[esi+7F0h]`).
* **Where the stubs live**: the wave loader's dead CD attempt, `0x00452B05..0x00452B5D` (Ultimate `+0x60`) -
  dead since `nocd`'s `jmp` at `0x00452AE9`, the first 22 bytes taken by `longpath`'s `open_read`, 193 bytes
  in all, nothing jumps into it (checked in both disassemblies), identical in both builds but for one rel32.
  Its stock code held one absolute operand (`.reloc` HIGHLOW at `base+0x4B`); that entry is re-pointed to
  `stub_a`'s `mov eax,[hud_ip]` operand at `base+1`, so the table stays exact. 109 dead bytes remain after
  the stubs (`0x00452B5E..0x00452BC5` Classic).

**Verified (3 Oct 2026).** `patch_netsave.py plan` on both stock exes, `apply` after `nocd` + `longpath`,
re-run recognises its own bytes; the stubs disassembled with capstone; the tool on the published Ultimate
`35346be9...` gives exactly the regenerated patcher's output. Patcher **1.4** (`gen_apply_script.py`:
`TOOL_OF` / `PLAN_OF` / `blocks_netsave` / the `PATCHES` entry / `netsave` after `chat` in both game step lists).
References: Ultimate 1024x768 dark **`b3d3ead8...`**, light `eb79fba7...`; Classic 1024x768 dark `739e1219...`
(not shipped). A copy of the game folder on `subst X:` patched with pwsh 7 and with Windows PowerShell 5.1 under
`tr-TR`: the same exe, the interface set unchanged between the runs. **In game, 1024x768 dark**
(`smoke_rig/netsave_test.py`, a local relay with `LOG_LEVEL=debug`, `DEFAULT_SERVER.TXT` = `127.0.0.1:8888 plain`,
`AVI\INTRO.AVI` renamed away - the first run clicked into the intro movie): ONLINE WAR -> room 1 -> READY (the
seat read from the relay's `online -> room` line) -> battle -> Game Option tab: the Save cell's 59x41 rect is
pure black (0 lit pixels; the Quit cell 982), F11 and a click on the cell's place open nothing, tabs 1 / 2 / 3 and
back leave it black; ACADEMY -> battle -> the cell is drawn (1083 lit pixels) and F11 opens the Save Game
dialog; `error.log` empty in both runs. **Second round the same day (maintainer: "run: other resolutions, 640x480, the Dark Colony build in game, the replay viewer"; `smoke_rig/netsave_all.py WxH THEME net|custom|camp|replay [--classic] [--letterbox]`):** Dark Colony Ultimate at 1280x720, 1280x800, 1280x1024, 1920x1200 dark, 1920x1080 light and 640x480 (stock metal HUD) - ONLINE WAR battle: Save cell 0 lit pixels, F11 and a click on it inert, still 0 after tabs 1/2/3; ACADEMY: cell drawn, F11 opens the dialog - and **3840x1080 dark through dgVoodoo 2** (second copy, `--letterbox`, clicks scaled 0.5 to the 1920x540 frame at y 330) the same; **Dark Colony (`Dark Colony.exe`, 1024x768 dark) through CUSTOM NET WAR / MULTI PLAYER WAR**: TCP/IP -> CONNECT TO SERVER -> 127.0.0.1 -> hall -> `/1` -> READY (join) -> READY (room) -> battle: cell blank, F11 inert, TRAINING: cell drawn, F11 opens the dialog; **the replay viewer** (REPLAY ONLINE GAME, a 76 s recording of the 1024x768 ONLINE WAR battle, WATCH AS the recorded seat, READY in the viewer lobby): cell blank, F11 inert, still blank after the tab switches. Every run with an empty `error.log`; every patched exe = its reference. Rig lessons: the hall's READY is the join press and the room needs a second one - test the relay log for a READY received in `"state":"LOBBY"`; the viewer room is an ordinary lobby, the viewer presses READY and the box of an EMPTY seat is ignored (REPLAY stays greyed) - tick the seat the relay gave the recorded client; the recorded lobby plays in real time before the battle. Nothing changed on the relay. **Committed and pushed 3 Oct 2026: Dark-Colony `c77d671`, Server `7949bf8`.** A real resume of a relay battle would need
a save upload and a rebuilt lobby on the relay - not planned.

### 10.69 Frame limiter: never more than 60 frames per second (fix `fps`, 3 Oct 2026)

**Report** (maintainer: "in linux+wine speed of cursor animation and map scroll on the battlefield are
ridiculously fast. investigate" -> "why 1 is better than 2?" -> "go ahead with option 1 and update
patcher"). Both games, `tools/patch_fps.py`, patcher 1.5.

**Cause: the game has no frame limiter.** The main loop is paced by one thing only: the DirectDraw
`Flip(NULL, flags 0)` at the end of `present` (`ddex4.c`, Classic `0x0042E0FC`, Ultimate `0x0042E15C`;
the call at `0x0042E28A`, retried in a busy loop on every error but `DDERR_SURFACELOST`). On Windows the
flip completes at the monitor's vertical blank, so the loop runs at the refresh rate; with the flip
skipped the loop managed ~4 400 passes per second (the minimised measurement of section 10.28) - nothing
else holds it. Under Wine the flip returns at once: wined3d asks for swap interval 1 for a flip without
`DDFLIP_NOVSYNC`, but Xvfb, a Wine virtual desktop and gamescope have no vertical blank to wait for.
Measured in the WSL rig (`winefps.sh`, `WINEDEBUG=+timestamp,+ddraw` filtered to the `_Flip` lines):

| Environment | Flip calls per second |
|---|---|
| Windows, flip paced by the vertical blank | 60 |
| Wine rig (Xvfb, llvmpipe), before the fix | 350-380 |

Two battlefield consumers advance per frame with no time gate, so they ran six times too fast:

* **Map scroll** `0x0040AE6A..0x0040AEB6`: after the 100 ms edge dwell (clock-based: `ui+0x7E0[i]` holds
  the time the pointer entered zone i, `ui+0xF4` the frame's `timeGetTime`, stored at `0x0040ABC8`) the
  camera moves one whole tile (`0x100`) per frame per direction; the arrow keys (`ui+0x13C[i]`) the same.
  Designed: 60 tiles per second. At 370 frames per second a 96-tile map crosses in a quarter of a second.
* **Cursor animation**: the client display calls the cursor-advance routine `0x00422C7C` (anim object
  `ip+0x4330`, stepped by `0x00426294`, cursor index to the display's `set_cursor` slot `+0x148` =
  `0x0042FB6C`) once per frame at `0x0040B309`.

The menus are protected: the widget pump `0x00424294` steps its animations only when 16 ms have passed
(`0x00489618`), and the interface loop at `0x0042412A` gates the cursor at 33 ms (`0x00489614`) and
catches up by elapsed time. Game ticks are clock-driven (66 ms), so the simulation, the network echo
and the music were never affected. The same mechanism makes a Windows monitor above 60 Hz scroll too
fast in proportion (2.4x at 144 Hz) - never reported, probably never noticed.

**Options weighed:** (1) a frame limiter after the flip, (2) gate the two consumers at 16 ms like the
menus, (3) a Linux-side limiter (libstrangle, MangoHud `fps_limit`, gamescope). (1) was chosen: it
restores the cadence the whole program was written against (unknown per-frame consumers included, no
invented catch-up arithmetic), it is one stub in one place, and it also stops the full-core spin under
Wine; (2) would have left anything untraced fast and needed two gates with their own catch-up; (3) is
not packaged for 32-bit Ubuntu (`mangohud:i386` absent in 24.04) and helps nobody on Windows.

**Fix = `fps`** (`tools/patch_fps.py` verify / plan / apply, `.fps.bak`, pattern-located, both games,
`Requires ddraw`, patcher order `..., chat, netsave, fps, movies, sounds | ozi, icon, online`):
`present`'s epilogue `lea esp,[ebp+82h]` (`0x0042E2A1` / Ultimate `0x0042E301`, reached after the
flip, from the error paths and from fix `restore`'s minimised idle stub) becomes `jmp stub; nop`. The
stub (132 bytes of code): the displaced `lea`; `push eax` (present's return value); `call $+5; pop esi`
= its own address, so every operand is esi-relative or rel32 and **no absolute operand exists**;
`now = timeGetTime()`; if `now - last < 16`: `GetModuleHandleA("winmm.dll")`,
`GetProcAddress("timeBeginPeriod")(1)` when found, then `Sleep(1)` + `timeGetTime()` until 16 ms have
passed, then `timeEndPeriod(1)`; `last = now`; `pop eax`; back into the epilogue. The
`timeBeginPeriod` pair is not optional: a Windows 11 process that never raised the timer resolution
measured **`Sleep(1)` = 15.6 ms** (timeGetTime itself 1 ms steps), which would have turned a 144 Hz
monitor into 43 frames per second; raised only around the wait, fix `restore`'s `Sleep(1)` while
minimised keeps its one-tick length (the same stub then paces the minimised loop too). On a 60 Hz
Windows monitor the flip has already taken 16-17 ms (readings 16 or 17, never 15), so the stub never
waits. Where: the three dead assert bodies of `remap` (`ddex4.c` 1029/1033/1036; Classic
`0x0042F399..0x0042F3F4` 92 bytes, `0x0042F3FA..0x0042F423` 42, `0x0042F443..0x0042F46A` 40 = the strings; Ultimate
+0x60), dead since the `ddraw` jumps of section 10.16 - nothing branches into them (checked against
every jump and call target of the disassembly); the code steps over the two live 5-byte jumps with
`jmp +5`, body 3 holds the three names (`winmm.dll`, `timeBeginPeriod`, `timeEndPeriod`). The
timestamp dword lives at **`0x00481FF0`** = the zero-filled page slack of the writable `.idata` section
(raw size 0x1200, mapped to 0x2000; nothing in either exe references `0x481200..0x481FFF`; the tool
checks the section table). `.reloc`: the 17 HIGHLOW entries that described absolute operands inside the
rewritten ranges (strings, `error.log` pointer) become type 0 padding, page offset kept (the tails of bodies
2 and 3 stay as they are - fix `pointer`, section 10.70, uses them; until the same evening `fps` wrote the
whole bodies and 20 entries). Calls go through the
import thunks (`timeGetTime 0x0047F116`, `Sleep 0x0047F008`, `GetModuleHandleA 0x0047EF2A`,
`GetProcAddress 0x0047F06E`; Ultimate +0x60), so the two builds differ only in the three
displacements of the timestamp. `plan` works on the untouched exe (the bodies hold the same bytes
before and after `ddraw`), `apply` refuses without `ddraw`. **`patch_ddraw_lost.py` was re-anchored**:
it located its three remap sites by the first bytes of the dead bodies, which this fix overwrites; it
now derives them from the unchanged loop tail (`nxt - 0xF2 / 0x91 / 0x48`) and accepts the stock
`push` or its own jump at each, so `verify` works on stock, `ddraw`-patched and `fps`-patched exes.

**Verified.** Wine rig, fixed exe, 30 s at the menu: 114 Flip calls per second in pairs (16-18 ms
then 1-3 ms - wined3d answers the first call of each frame with an error the stock loop retries, so
that is **57-60 frames per second**), `error.log` empty; a 640x480 training battle under Wine as before.
Windows (`smoke_rig/fps_test.py`, `subst X:` copy, 1024x768 dark, ACADEMY battle): **60.2 frames per
second** (frame period median 17 ms, 14..19), the limiter's timestamp advancing 1000 ms per second (the
stub runs every frame), the RIGHT arrow held: **18 tiles in 18 frames = 60.1 tiles per second** (one
tile per frame, stock), quit clean, `error.log` empty. Clean `git archive HEAD` copy under pwsh 7 and
PowerShell 5.1 (`tr-TR`), `-All -IncludeDeprecated -Resolution 1024x768 -Theme dark`: references
**Ultimate `308ac5b7…`** (light `5bb3fdd4…`), **Dark Colony `32432f5c…`** (light `e2bd1fc3…`), editor
`de8076dc…` unchanged; the written set = the fixtures (HSCENE/GSCENE = the DC ending names, the copy has
the movies); the tool on the previous published Ultimate `b3d3ead8…` = the patcher's output. Not run:
the other sizes in game, a Windows monitor above 60 Hz (expected: capped at 60), the pointer-edge scroll
in the rig (the synthetic pointer move at x 894 did not start it; the key scroll measures the same
per-frame camera code).

**Rules.** Every per-frame effect of this engine assumes a 60 Hz frame; new per-frame code relies on
this limiter or gates itself by `timeGetTime`. A tool that anchors on dead code breaks the day that
dead code is reused - anchor on live bytes (the loop tail here). On Windows 11 a process gets 15.6 ms
from `Sleep(1)` until it calls `timeBeginPeriod` itself; measure before relying on a sleep length.

**Second round (same day, maintainer: "can you test these things on this machine using virtual monitor with
refresh rate 120hz? run. Other sizes in game, and a Windows monitor above 60 Hz ... One rig oddity: a synthetic
pointer move to the view's edge did not start the edge scroll" and, meanwhile, "pointer animation is too fast").**

* **Six sizes in game** (`sizes.sh`: the X: copy re-patched per size with PowerShell 5.1, `fps_test.py` at each):
  1024x768, 1280x720, 1280x800, 1280x1024, 1920x1080, 1920x1200, all dark - every exe = the generator's reference for
  its size, 60.0-60.2 frames per second (median 17 ms), the limiter timestamp advancing every frame, the RIGHT arrow
  held = one tile per frame (16-18 tiles in 17-18 frames at the 1280-wide sizes; at the 1920-wide sizes 7 tiles,
  because the 64-tile training map leaves a 56-tile view only 8 tiles of travel), `error.log` empty throughout.
* **The pointer-edge oddity explained** (`fps_test.py` reads the pointer the game uses, `0x4DFF14/1C`, the zone rect
  `ui+0x7D0..0x7DC` and the four dwell stamps `ui+0x7E0..0x7EC`): the zone is right (3, 3, W-131, H-51) and the
  stamps do get written when the game's pointer is in a zone (one was armed at 1280x720), but in battle the game's
  pointer does **not** follow a synthetic absolute jump - after `SendInput` to x 1277 the game read 1197, after 1917 it
  read 1791, i.e. the position lags the real cursor by the delta of the previous move. The in-battle pointer follows
  relative movement (the DirectInput path `0x450E80`, which accumulates deltas and mirrors into `0x4DFF14`), so the
  rig's jumps land short of the 3-px zone and nothing is armed. The mouse handler `0x433A15..0x433AE9` arms zone 0/2
  when x < left / x > right and zone 1/3 for y, clearing them otherwise. **Rule: measure scrolling with a held arrow
  key (same per-frame camera code); to use the pointer, move it in small steps.**
* **"Pointer animation is too fast"** - measured (`cursor_rate.py`, the cursor index `0x48972C` Ultimate /
  `0x489704` Classic written by `set_cursor`): in battle **30.2 index changes per second** (a three-frame cycle,
  indices 0..2, ten cycles a second) at 60 frames per second; at the main menu **11.5 per second** (the interface
  loop's 33 ms gate plus that cursor's own frame holds). The battle rate is the stock rate of a 60 Hz Windows
  machine - the cursor-advance call `0x40B309` runs once per frame and the step routine `0x426428` has no clock,
  it holds a frame for the FIN's per-frame count and then advances - and since the fix Wine shows the same 30.
  To slow it, the battlefield call would have to be gated like the menus (a stub at `0x40B309`: advance only when
  33 ms have passed since the last advance = 15 changes per second, or any other period); **built the same
  evening as fix `pointer` at the maintainer's word, section 10.70.**
* **120 Hz / above 60 Hz on this machine: not reproducible.** The panel offers only 60 Hz modes
  (`EnumDisplaySettings`, every size). dgVoodoo 2 with `ForceVerticalSync = false` was tried as a presenter without
  a vblank wait, in fake fullscreen and in real exclusive fullscreen: both still ran the OLD exe at 60 (13-18 % of a
  core at the menu, the compositor / DXGI path paces it) and the new exe at 60.2 limiter steps per second, so it
  proves nothing about the cap; dgVoodoo's fake-fullscreen pointer mapping also clamped the game's pointer at
  (552, 490) with the 1024x768 proxy, so no battle could be driven there (the 3840x1080 rig of section 10.32
  downscaled, this one upscales - the proxy / `CaptureMouse` interplay differs; left as is). A true test needs a
  monitor with a >60 Hz mode or a virtual display driver (an IddCx driver such as the open-source "Virtual Display
  Driver", installed as admin with its certificate - a system change left to the maintainer); the Wine rig remains
  the one presenter without a vblank wait, and there the cap holds (57-60 frames per second, 350-380 before). **Committed and pushed 3 Oct 2026: Dark-Colony `ef41557`, Server `98f5e88`.**

### 10.70 Battlefield pointer animation at the menus' pace (fix `pointer`, 3 Oct 2026)

**Request** (maintainer, after section 10.69's measurement: "pointer animation is too fast" -> "ok, let's try
battle cursor at the rate of gated the way the menus are"). Both games, `tools/patch_pointer.py`, patcher 1.6,
its own fix id so it can be left out independently of the frame limiter.

**What changes.** The client display advances the cursor animation once per frame (`call 0x00422C7C` at
`0x0040B309`, Ultimate `0x0040B369`; the step routine `0x00426428` has no clock, it holds a frame for the
FIN's per-frame count and then advances), so at 60 frames per second the battlefield pointer's three-frame
cycle turned ten times a second (30.2 cursor-index changes per second measured). The menus advance the same
animation only when 33 ms have passed since the last advance (interface loop `0x0042412A`: `now - last >
0x21`, one advance, `last = now`; the elapsed/33 loop that follows redraws widgets, it does not advance the
cursor again). The battlefield call now goes through the same gate:

    gate:  push eax ; now = timeGetTime() ; if now - last < 33: pop eax ; ret
           last = now ; pop eax ; jmp 0x00422C7C          (tail call; the client's `test esi,esi` after the call is untouched)

= one advance every second frame at 60 frames per second. **Measured** (`smoke_rig/cursor_rate.py`, X: copy,
1024x768 dark, ACADEMY battle): 14.2 cursor-index changes per second with the pointer parked (30.2 before),
11.2 at the menu as before; frame rate 60.2 and the key scroll (17 tiles in 17 frames) unchanged; `error.log`
empty; the Wine rig's 640x480 battle as before.

**Where.** 35 bytes in the tails of the second and third dead `remap` assert bodies that the `fps` stub leaves
free (Classic `0x0042F424..0x0042F438` 21 bytes - the range starts inside the `mov eax,[error.log]` that the
`fps` range cuts - and `0x0042F46B..0x0042F478` 14 bytes; Ultimate +0x60; dead since `ddraw`, so `Requires
ddraw`; independent of `fps`, after it in the patcher order). The timestamp is `0x00481FF4`, the dword after
the limiter's in the `.idata` page slack. `.reloc`: the tails' three HIGHLOW entries (page offsets `0x427`,
`0x46C`, `0x471`; Ultimate +0x60) are re-pointed to the gate's two absolute operands (`0x427 -> 0x42E`,
`0x46C -> 0x46D`) and the third becomes type 0, so the table stays exact; the hook's rel32 and the thunk call
move with the code, so the written bytes are identical in both builds. **`fps` was narrowed for this** (same
day, before anything was committed): it now writes body 2's first 42 bytes and body 3's first 40 (the
strings) instead of the whole bodies, neutralises 17 instead of 20 relocation entries, and leaves the tails
alone; a `pointer` applied without `fps` works on the stock tails too.

**Verified.** Clean `git archive HEAD` copy under pwsh 7 and PowerShell 5.1 (`tr-TR`): references **Ultimate
1024x768 dark `3cba8b55…`** (light `7ad1744c…`, 640x480 `be1b3fa0…`), **Dark Colony `d2424406…`** (light
`3a53818b…`, 640x480 `67c71ef9…`), editor `de8076dc…` unchanged; sets = fixtures. Tool chain: `ddraw` ->
`fps` -> `pointer` on both stock exes = the patcher's bytes, every tool's `verify` says patched afterwards,
`pointer` idempotent, `pointer` without `fps` applies and leaves `fps` applicable.

**Rule.** Two fixes sharing one dead region split it at an instruction boundary of the NEW code, not of the
old: the gate's first range starts two bytes into a dead instruction, which is harmless (nothing executes
there) but must be in the stock pattern. **Committed and pushed 3 Oct 2026: Dark-Colony `ef41557`, Server `98f5e88`.**

### 10.71 The installer package for ModDB: `patcher/`, the resources beside the script, the game from the two original discs (5 Oct 2026)

**Why.** ModDB's terms forbid uploading a full commercial game or third-party assets, the repository ships the whole game
(612 MB), and *Dark Colony* is sold nowhere (the holder is unclear: Take-Two or Ubisoft through SSI) - so every copy is
tolerated, not licensed. The position with the least exposure: distribute nothing of SSI's, let the player bring the
discs. An installer that downloads the fan-site packages (darkcolony.pl: a 492 MB English "1.1" archive with hacked exes,
the 554 MB DCUK cover-disc ISO, a 1.25 GB French Dark Colony + Council Wars) would make the patcher itself the
distributor, and no English Council Wars disc is there at all. Maintainer: "installer must have green plus yellow plus
the ozi_ns pack" (green = the project's own files, yellow = art derived from the game's own, the pack with credit).

**What is where (tools/discs.py manifest -> tools/disc_manifest.json).** Every tracked file of the two game folders
against the two CD images (`Dark Colony.bin`, volume DCUK, 5151 files; `Dark Colony - The Council Wars.bin`, volume
COUNCILWARS, 2441 files; raw 2352-byte MODE1 sectors, ISO 9660 level 1):

| class | files | what | where it lives now |
|---|---|---|---|
| disc | 2263 | stock game files: 1958 only on the Dark Colony disc (`/DC/` = the installed game: SCENARIO, GAMESTAT, CURSOR, MISSION, ENCYCLO, MAPED.EXE ...), 305 from the Council Wars disc (`/EXPENG/ENGEXP16.EXE`, `/EXPENG/EXP/` = `exp\`, and its `/DC/` with only ANIMATE, AVI, INTRFACE, SOUND, SPRITES, WALLPAPR); 80 under another disc name (the Classic movies as `AVI\DC*.AVI`, the ozi_ns copies of stock terrains / ambience / sounds, `dc\intrface\credits.txt`), 12 game-written `.OVH` taken as the disc has them | the game folder; a disc install extracts them |
| output | 75 | the `HD_<height>P` sets, the patched exes, the 640x480 copies | the game folder; the patcher writes them |
| local | 17 | the MP3 soundtrack (8), the January 1998 `dc16.exe` (the disc has the August 1997 build), InstallShield logs, `ERROR.LOG`, the `HBNFUFL` drive-letter files, the editor's `readme.doc` | the game folder only; never in the package |
| derived | 1 | `SCENARIO\HUMAN\human09.tro`: the disc's text has the `&&==` typo in mission 9's trigger, the 1998 update fixed it - the installer applies the same one-token fix | written by the installer |
| resource | 366 | the project's own files and the ozi_ns pack | the game folder AND a copy in **`patcher/game/`**, **`patcher/editor/`** |

Both discs are required: neither alone holds the shared game folder. The resources: `HD_SRC/` (the five pictures per
size, the console banks, KNOBR), `SPRITES/DC??_HD.SPR` + `CLOCK.SPR` + `TRAC.SPR`, `ANIMATE/*_HD.FIN` + `TRAC.FIN`,
`DC_HD.ICO`, `DEFAULT_SERVER.TXT`, `dc/gamestat/weapstat.txt`, `exp/animozi.dat` + the pack's `dalg|spyo|reae|tranozi`
FIN/SPR, the two modified stock files `exp/intrface/bintroe` and `exp/gamestat/weapstat.txt` (kept in the game folder as
well - the untouched ENGEXP16.EXE reads them), the pack's own `ozi_ns/gamestat`, `intrface/astory|hstory.txt`,
`mission/*.wav` (the pack's real briefings, 23 files, 50 MB - not silent placeholders; only `h80.wav` is), `scenario/
council|globo`, `jubjub.bts`, `special.bts`, seven sounds (not `ozisave/ozisave.txt` - see the end of this section); for the editor the Borland runtimes
`BWCC.DLL`, `BWCC32.DLL`, `CW3215MT.DLL` (the disc has them only inside the InstallShield `DATA.Z`) and
`scenario/atlantis.set`. A first form moved them out of the game folders with `git mv`; the maintainer's second
instruction the same day ("don't delete files from where they was! You must use 'patcher' directory as a source from where
you take resources and copy to the places where they must reside") restored them: the game folders keep every file (the
repository plays as checked out), `patcher/` holds a COPY, and `resources.py sync` / `check` keep the two sides equal (the
dev tools still write into the game folder; `sync` copies their resource outputs over).

**The generated script (patcher 2.0; `gen_disc_install.py` holds the new parts).** `$PSScriptRoot` is `patcher\`, the
repository root its parent. `Copy-Resources` copies `patcher\game` (or `patcher\editor`) into the folder a build is
written to before anything else; `Test-DataFile` counts a fix's data file as present when the game folder, the resource
folder or - while a disc install is planned - the manifest has it. **Disc install:** the welcome checkbox (ticked by
itself when no original is found beside the package) adds *Step 1: Game discs* - Council Wars disc, Dark Colony disc
(image `.iso` / `.bin` / `.cue`, or a drive / folder), install folder (default `Documents\Dark Colony`; the editor in
`Map editor\`). Next = `Install-DiscOriginals`: both discs opened and tested (`/EXPENG/ENGEXP16.EXE` + `/EXPENG/EXP/
ANIM.DAT`; `/DC/GAMESTAT/GAMESTAT.TXT` + `/DC/SCENARIO/HUMAN/HUMAN01.SCN` + `/DC/MAPED.EXE`), the two exes extracted and
SHA-256-checked against the builds (a French or Italian disc, or the 1997 `DC16.EXE`, is refused with the hash), loaded
like browsed originals. Patch = `Install-GameFromDiscs`: every `$DiscFiles` line (`root|to|disc[|from]`), the derived
files (`human09.tro`, `HBNFUFL.A01/.A02` = `D:`, the editor's `hbnfufl.a01` = `C:`), `SAVE`/`ESAVE`/`ozisave` folders; a
file already there with the right size is kept (resumable). CLI: `-InstallDir -CouncilWarsDisc -DarkColonyDisc -All
-Resolution -Theme`. **`DcDisc`** (C# in the script, `Initialize-DiscReader`, Add-Type like the GIF codec): PVD at sector
16, root directory record at byte 156 (extent at 158, size at 166), directory records walked recursively (`;1` stripped),
sector size from the sync pattern (2352: data at +16 for MODE1, +24 for MODE2 form 1; else 2048), `.cue` -> its FILE
line, folders / drive letters by enumeration; extraction in 64-sector chunks (~10 s for 400 MB from a `.bin`).

**The soundtrack (patcher 2.1, same day; maintainer: "music is on the original discs as real music disc tracks. installer
must rip them, and if possible convert to mp3").** Both CDs are mixed-mode: tracks 2-5 are the music (section 10.31).
`DcDisc.ScanAudio` finds them in a raw `.bin`: the data track is a prefix whose sectors carry the sync pattern (binary
search for its end), the rest is audio, tracks are the pieces separated by >= 150 all-silent sectors (|sample| < 3 on both
channels), pieces under 1500 sectors (20 s) are gap noise - the rules of `rip_music.py`; for a real disc in a drive the
TOC (`IOCTL_CDROM_READ_TOC`: control bit 2 clear = audio, MSF -> LBA - 150) and raw reads (`IOCTL_CDROM_RAW_READ`, mode
CDDA, 16 sectors per call) - written from the documentation, untested here (no optical drive). `WriteTrackWav` cuts the
pregap junk at the end of the last >= 20 ms silent run within the first second and the trailing silence (CW track 2: 45 ms,
DC track 2: 258 ms, track 3: 481 ms), the raw sectors being 44.1 kHz 16-bit stereo PCM already. `ConvertTo-Mp3` encodes
with Windows' own MP3 encoder through the WinRT `MediaTranscoder` (`MediaEncodingProfile.CreateMp3(High)`, bitrate forced
to 192 000, sample rate 44 100 - the profile's default was 48 kHz; ~1 s per 150 s of audio); WinRT is reachable from
Windows PowerShell 5.1 only, so under pwsh 7 the same script text runs in a hidden `powershell.exe` child; `Test-Mp3Encoder`
probes once with 0.2 s of silence (the "N" editions have no encoder without the Media Feature Pack). **MCI `mpegvideo`
refuses a WAV under the `.mp3` name (error 277 "A problem occurred in initializing MCI"), so there is no WAV fallback:
without an encoder the music is left out.** The encoded files are MPEG-1 layer 3, 192 kbps, 44.1 kHz, and the game's MCI
device plays them (`mcitest.py`). `Install-DiscMusic` writes `MUSIC\TRACK02-05.MP3` from the Dark Colony disc and
`exp\music\track02-05.mp3` from the Council Wars disc at the end of `Install-GameFromDiscs`, skips a folder that already
holds its four files, and `Test-DataFile` counts the eight as present while a disc install with audio is planned
(`$script:DiscInstall.Audio`, set by `Install-DiscOriginals`: four tracks on each disc + the encoder) - so an install
from `.bin` images or real CDs reaches the full reference `3cba8b55…`. **What a disc install still cannot have:** the
music from an `.iso` / mounted `.iso` / folder (data track only; Ultimate then 20 of 21 fixes, `b21baac1…`, said on the
page) and the deprecated Dark Colony build (needs the 1998 `dc16.exe`). This PC's `Dark Colony.bin` is the damaged rip of
section 10.52, so its ripped MP3s carry the stray blocks; a player's own disc does not.

**Smoke test as a player (5 Oct 2026, maintainer: "smoke test by placing installer files in a separate folder 'Dark Colony
patcher' in documents folder and after that create using this fresh copy of the installer 'Dark Colony Ultimate' in
documents from both CD discs and check integrity").** The zip unpacked into `Documents\Dark Colony patcher` (369 files),
`INSTALL.CMD` from there with `-InstallDir "Documents\Dark Colony Ultimate"`, both `.bin` images, 1024x768 dark: 2264 files
(401 MB) from the discs, 4 + 4 tracks ripped, 362 + 4 resources copied, both exes at the published hashes. Integrity against
the repository index: 2696 tracked game files identical, 8 MP3s (ripped, bytes differ by design), 12 `.OVH` as the disc has
them, 6 repository-only files absent by design (the 1998 `dc16.exe`, InstallShield logs, `readme.doc`, `ERROR.LOG`), 0
missing, 0 differing, one extra `HD_0768P\BACKDROP.GIF` (the set writer's fourth picture, untracked); manifest check 2263
identical; set = fixture; every resource = its `patcher/` copy; the MP3s play through MCI. The game started from the real
Documents path (no `subst`) into the first OZI battle, `error.log` empty. Side effect: the installer replaced the two desktop
shortcuts (they point at the Documents install now). The 1998 `dc16.exe` is on neither disc (the Dark Colony disc carries the
August 1997 build `4180f6e9…`, the Council Wars disc no Classic exe at all), so a disc install has no deprecated Dark Colony
build - the options: leave it (browse to a repository `dc16.exe` on its page), ship the 1998 exe as the official 1.01
update file in `patcher/game/`, or re-base the fixes onto the 1997 build. What the 1998 update changed (binary comparison,
no changelog inside): a full recompile (5.5 % of the code verbatim in 1997), a `.rsrc` section with the icon, the campaign
lists without `.txt` plus the new `hxscene`/`gxscene` lists and the `gjungle` terrain (the expansion's loader), the
CD prompt strings `CDROM NOT FOUND` / `Please insert The Dark Colony CD and Restart`, new asserts (`ip->objects[i].type !=
unknown_obj`, `strlen(actual_filename)!=0`, `sptr==(stack+2)`, `eq<=t.pool+MAX_POOL`), the keyword `funkytower`; same 11
DLLs and 155 imports; the official note: stability on newer PCs, many small fixes, human mission 9 repaired (= the
`human09.tro` typo the installer fixes itself). **Committed and pushed 5 Oct 2026: Dark-Colony `26483c0`, Server `58488c8`** (the maintainer's unstaged 1280x800 run in the game folder - `HD_0800P`, the deprecated `Dark Colony.exe`, the `HD_0768P` deletions - left in the working tree).

**Verified (scratchpad `run_tests.sh`, `gui_test.ps1`, `cmpset.py`).** Clean copy of the repository index: `-All
-Resolution 1024x768 -Theme dark` under pwsh 7 = Ultimate `3cba8b55…`, editor `de8076dc…`, set = fixture (the
fixture's `HSCENE`/`GSCENE` refreshed from HEAD - they name the DC endings), the resource copy a no-op (every file
already there and identical, `resources.py check` clean); disc install from the `.bin` images under PowerShell 5.1 (`tr-TR`) and from the
`.iso` + the Dark Colony disc as an extracted folder under pwsh 7: `discs.py check` = 2263 files identical to the
repository, set = fixture, `human09.tro` fixed, the drive-letter files in place, Ultimate `b21baac1…`; refusals
(`-InstallDir` alone, swapped discs); the headless window under both shells (steps shift by one with the discs page,
PrepareDiscs refuses empty / equal / swapped discs, Apply with the discs returns the disc line + two builds, a second
Apply keeps the files). **Pitfalls:** the ISO root record's extent is at PVD byte 158, not 162 (a first reader found no
files); PowerShell variable names are case-insensitive, so a local `$dir` clobbered the `$Dir` parameter and the
derived files landed under `SCENARIO\HUMAN\`; `` `u001a `` is a parse error in pwsh 7 and silently `u001a` in 5.1 -
`[char] 0x1A`. Package: `tools/make_installer_zip.py` (`INSTALL.CMD` + `PATCH_HOWTO.TXT` + `patcher/`, every file
checked against the index).

**`ozisave` is not carried; `ozisave.txt` is created from scratch (5 Oct 2026, maintainer: "patcher must not carry 'ozisave'. ozisave.txt must be created from scratch"; patcher 2.2).** `patcher/game/ozisave/ozisave.txt` is removed from the repository (`git rm`) and `tools/discs.py` classes `ozisave/ozisave.txt` as an OUTPUT (`OUTPUT_RE`), so it left the manifest's resource list (365 resources) and the installer zip (368 entries); `gen_apply_script.ozi_data` no longer lists it as a `Data` file. The generated script's `Write-OziSaveFolder` runs after every Dark Colony Ultimate build with the `ozi` fix (any resolution, the disc install too): it creates `ozisave\` when missing (stub_pack points both save-folder slots at it and the game writes `ozisave\<name>.dcg` without creating the folder) and writes `ozisave\ozisave.txt` with the one line the game folder's marker carries when no such file exists; nothing is overwritten (a second run writes nothing, saves beside it stay). Verified on a clean copy of the index with `ozisave\` deleted before each run: pwsh 7 and PowerShell 5.1 `tr-TR` at 1024x768 dark = `3cba8b55…` / `de8076dc…`, set = fixture, marker byte-identical to the repository's; 640x480 = `be1b3fa0…`; the disc install from `.iso` + folder = `b21baac1…`, 2263 disc files identical, `ozisave\ozisave.txt` present. The game folder's own `DC - Council wars/ozisave/ozisave.txt` stays tracked (an output now, like the HD sets).

### 10.72 The deprecated Dark Colony build removed from the installer (5 Oct 2026)

**Maintainer: "remove deprecated 'Dark colony' option from installer completely as won't be needed anymore."** Since
1 Oct 2026 (section 10.58) the Classic build `dc16.exe` -> `Dark Colony.exe` was deprecated: unticked by default, its page
shown only through a checkbox on the options page, `-All` skipping it unless `-IncludeDeprecated`, the patched file no
longer in the repository. Now it is gone from the installer altogether (patcher **2.3**):

* `gen_apply_script.py`: the `Classic` entry of `BUILDS` (with `CLASSIC_DEPRECATED`, `deprecated=`, `shipped=`), the
  `'classic'` keys of `ORIGINALS` / `GAME_DIR` / `RES_DIR`, the two Classic-only fixes **`movies`** (section 10.18) and
  **`sounds`** (section 10.21) with their block parsers, `movie_data` and tool entries, and every Classic branch of the
  block asserts (`nocd` 13 / 19 edits, `music` five or six blocks) are removed; `MODE_STEPS` loses `movies`.
* the generated script: no `Deprecated` / `Shipped` fields in the build records, no `-IncludeDeprecated` parameter, no
  "Also patch the deprecated Dark Colony" box or deprecation notices in the window (Welcome -> Options -> Dark Colony
  Ultimate -> Map Editor -> Ready to patch, 4 steps, 5 with the discs page), no `Write-StockEndingLists` (`movies` at
  640x480: `GAMESTAT\HSCNDC.TXT` / `GSCNDC.TXT`), no `Get-PatchOrder` / `Sort-ForPatching` (the 3 Oct "Ultimate last"
  order existed only because the Classic build rebuilt the interface set after it); `Write-InterfaceSet` lost its
  `$Movies` parameter - the HD `HSCENE` / `GSCENE` lists still name `avi/dchending.avi` / `dcaending.avi` when the
  `DC*.AVI` files are in the folder (the DARK COLONY mode of Dark Colony Ultimate plays them; fixtures unchanged).
  The script shrank from 1.98 MB to 1.33 MB (the Classic variants of every size are gone).
* `gen_disc_install.py`: `$ResourceRoots` without `Classic`. The untouched `dc16.exe` stays in the repository (manifest
  class `local`), `.gitignore` keeps `Dark Colony.exe`; the Python tools (`patch_movies.py`, `patch_wavprefix.py`, the
  rest) still patch it by hand for research.
* player docs: README, PATCH_HOWTO.TXT, INSTALL.CMD.

Verified on a clean copy of the index: `-All -Resolution 1024x768 -Theme dark` under pwsh 7 = **Ultimate `3cba8b55…`,
editor `de8076dc…`** (unchanged references), set = fixture, no `Dark Colony.exe` and no `HSCNDC` / `GSCNDC` written;
PowerShell 5.1 under `tr-TR` with `-Theme light` = `7ad1744c…`; `-IncludeDeprecated` is an unknown parameter;
`-Original dc16.exe` is refused as "not one of the two known original executables"; `-Verify` of a light build says
"not the published exe" (a leftover `$b.Shipped` read in that message and in the CLI result line was the one runtime error
the first test run found - the StrictMode rule again: every removed field needs a grep for its readers); the disc install
from `.iso` + folder = `b21baac1…`, 2263 disc files, two executables; the headless window test (`gui_test2.ps1`, 5 Oct
scratchpad) under both shells: two builds, no deprecated hooks or controls, steps 1..4, Apply = two results at the
published hashes, "2 of 2 executable(s) patched".

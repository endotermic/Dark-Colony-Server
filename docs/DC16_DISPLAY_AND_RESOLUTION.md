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
   `0x004895F0` pairs `"label"` with handler `0x00424A40`, which parses `x y w h` through the
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

**Design (maintainer decisions, 10 Sep 2026):** Council Wars keeps its single-column menu; the
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
behaves the same. A **CD-free 640×480 build** = original + `cdcheck` only (patcher `-Patches cdcheck`, 2 resp. 3
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
`exp\intrf_hdintroe`; at 640×480 the exe reads the stock `GAMESTAT` lists and the stock
`exp\intrfaceintroe`, which must stay byte-identical to the CD for the original exes (§10.17).
So the 640×480 Classic build played `avi/intro.avi` — the Council Wars intro in the shared folder —
and the 640×480 Council Wars build could not have the OZI mode at all (`-All` even threw, because a
required fix that does not exist at the resolution was not treated as unavailable).

**The way in.** The three DGROUP path strings end in exactly six letters followed by the next
string: `gamestat/hscene gamestat/gscene …` (Classic file `0x7FA10`/`0x7FA20`, CW `0x7FC10`/`0x7FC20`)
and `intrface/bintro intro.avi…` (file `0x7FC98` / `0x7FE98`, VA `0x482498` in both). The exe appends
`.txt` resp. the language letter, so a six-letter **name** can be swapped in place and the exe reads
a **new file** that the original never opens:

| fix @ 640×480 | exe edit (in place, no `.reloc`) | file the patcher writes | tool |
|---|---|---|---|
| `movies` (Classic) | `intro.avi` → `dcintro.avi` as before **+** `gamestat/hscene` → `gamestat/hscndc`, `gamestat/gscene` → `gamestat/gscndc` (3 edits, 17 bytes) | `GAMESTAT\HSCNDC.TXT`, `GSCNDC.TXT` = the stock lists with `avi/hending.avi` → `avi/dchending.avi`, `aending` → `dcaending` (`Write-StockEndingLists`) | `patch_movies.py --width 640 --height 480` (`find_list_sites`, `LIST_NAMES`; `apply` in a game folder writes the copies too) |
| `ozi` (Council Wars) | the 14 edits + `.reloc` insert as before **+** `intrface/bintro` → `intrface/bintoz` (16 edits) | `exp\intrfaceintoze` and `ozi_ns\intrfaceintoze` (OZI mode reads through the `ozi_ns/` prefix) = the stock CW menu with `Edit-OziMenu`'s rows: x = 228, OZI MISSIONS 392, OZI LOAD 418, QUIT 444 (`Write-StockOziMenu`) | `patch_ozi_menu.py --width 640 --height 480` (`STOCK_MODE_SITES`) |

At HD sizes nothing changes: `hdpaths` points the exe at `INTRF_HD`/`exp\intrf_hd`, whose lists and
menu already carry the names and rows. **Generator:** `movies` and `ozi` joined `MODE_STEPS`
(replayed per mode, `--width/--height` passed; the stock mode too), `HD_STEPS` is back to
`resolution, hdpaths, clock`; identical variants are merged (all HD modes → `Mode = 'hd'`, one
mode → its own entry), so each fix has one 640×480 entry and one shared HD entry; `Requires` and
`Data` are mode-aware (no `hdpaths` requirement at 640×480; `Data` adds the stock lists resp.
`exp\intrfaceintroe` as sources). **Script:** `Get-BuildPatches` includes a resolution's own
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
| `0x00424A40` | `widget.c` `label` keyword handler (keyword table `0x004895F0`) |
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

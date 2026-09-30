# Dark Colony interface style guide: how to draw interface elements

Guidelines for every piece of interface art that the patched executables show: the battlefield HUD
(`INTRF_HD/INTRFACE.GIF`, `INTRF_HD/MAINBUT.SPR`), the battlefield dialogs (`INTRF_HD/POPP.SPR` and
the scripts `LOPTE LQCE LSGE LOBJE`), the menu screens and every future screen. Written 30 Sep 2026
at the maintainer's request ("write a clear and descriptive guidelines for drawing an interface
elements") after the dialog frames were redone (`DC16_DISPLAY_AND_RESOLUTION.md` §10.53). The rules
are the ones the tools implement (`tools/hud_console.py`, `tools/patch_online.py`, the patcher's
`Edit-DialogConsole` / `Write-OnlineScreen`); when a rule and a tool disagree, fix one of them and
say so here.

## 1. One visual language: the console

Dark Colony's menus - race selection, the network lobby, story, victory, encyclopedia - are drawn in
one style, the **console**: dark grey pipework of several intensities, black read-out screens with
grey tube frames, black button plates with a thin red ring, clean single-colour glyphs. The stock
battlefield (metal HUD, bevelled dialog rows) was the one exception and has been redrawn in the
console style (§10.49). **Every new or changed interface element follows the console.** The
maintainer's words that define it:

* "You must create the same style as in race selection, network lobby and other menus."
* "Frames must be in lobby style. Lobby is not black, it is in different intensities of gray."
* "Keep the icons and portraits, red-outlined button icons which mimics lobby button style everywhere."
* "Text boxes must have gray frame. Scroll bar must have appropriate frame."
* "Refactor style of battlefield 'options' menu using best practices taken from pre battle menus."
* "Every element must have gray frame. All menus have some gray unclickable elements and lines
  between clickable elements."
* "Some frames are not closed! Check battlefield main interface and your 'options' interface."
* "Battle interface tab buttons are breaking interface frame. 'OPTIONS' text does not have a frame
  at all." Then: "Increase height and width of 'OPTIONS' frame. It must fill all space between form
  border and actual options frame." And: "Tab button '1' is breaking interface frame. Chat entering
  frame is broken too. And chat scrolling buttons must be centered in their frame." Then: "You didn't
  fix the problem with '1' tab frame! And now 'BUILD' button lost left frame!" and "'DAYS' frame is too
  tight and days count is not horizontally centred in the frame." And: "'DAYS' and days count must
  not overlap each other." Then: "Apply the same style to save, objectives and quit dialogs."

Reference pictures: `INTRFACE/MULTIWIN.GIF` (lobby), `INTRFACE/NET.GIF`, `INTRFACE/SHUMAN.GIF`,
`INTRFACE/KNOBE.SPR` (lobby buttons). Look at them at 2x-6x before drawing anything; the rules
below were measured on them.

## 2. Palette

All interface art is 8-bit indexed with the screen palette (`PALETTE.GIF`; the terrain palettes
match it within 3/255 at every index, so battlefield art may use any index). Decode and encode
sprites through the *screen* palette, never through a bank's embedded one.

| Role | Index | RGB | Use |
|---|---|---|---|
| black, erase | 254 | 0,0,0 | interiors of screens, text boxes, plates; the HUD's view hole |
| transparent | 0 | - | in `.SPR` cells only: "do not draw". Never use 0 for visible black in a cell - use 254 |
| ground | 67 | 11,11,11 | pipework ground, dark seams, the outer ring of a frame |
| dark cell | 66 | 23,23,23 | plain pipework cells, the line under a tube |
| tube body | 65 | 35,35,35 | tube / band body, the two dark rings of a frame, inactive plates |
| outline | 62 | 43,43,43 | compartment outlines inside pipework |
| mid grey | 55 / 49 | 65 / 82 | nested outlines, small details |
| light line | 40 | 107,107,107 | the light edge of every tube and frame |
| pale grey | 36 | 115,115,115 | rarely, highlights |
| red ring | 100, 101, 80, 98, 99 | 79,7,7 / 39,7,7 / 255,47,0 / 159,19,19 / 119,11,11 | the lobby button plate (§4) |
| red glyph ramp | 96..101 | 255,31,31 -> 39,7,7 | red icons, the value / caption red (`CAPTION_RED` 96/98/99/100) |
| green ramp | 120..125 | 103,255,0 -> 15,39,0 | the pressed plate's grid, green glyphs |
| cyan ramp | 128..143 | - | **the team colour**: in a cell drawn by a `pushb`/`count` widget it is remapped to the player's colour. Use it only where that is wanted; text drawn by the engine (`scount`) keeps cyan |

Rule of thumb for greys, outside to inside: ground 11 -> 35 -> **107** -> 35 -> black. The light
line is always one pixel wide and always sits between two 35-grey pixels.

## 3. The frame (tube)

The frame is the console's single most important element. Every read-out screen, list window, text
box and scroll channel is wrapped in it, and the dialog panel's border is the same tube seen from
outside.

**Frame, outside in (4 px):** `11 | 35 | 107 | 35`, then the black interior.
**Panel border, edge in (4 px):** `35 | 107 | 35 | 11`, then pipework.
**Corners:** the 35-grey ring drops its corner pixel (drawn 11) and the light ring's corner pixel is
35 - "one pixel off each corner", the ONLINE screen's rule. Nothing else is rounded in a frame; the
lobby's large rounded bends belong to the background art, not to widget frames.

Spacing rules around frames:

* Two frames never touch: **2 px of ground (11)** between them, and 2 px between a frame and the
  panel border. Together with the frames' own 11 rings that gives a 4-px dark seam.
* Inside a frame, the black interior begins right after the inner 35 ring. Widgets that paint their
  own black background (`list`, `in_text`) may start at the interior's first pixel; a plate inside a
  channel keeps **1 px of black** around it.
* Text keeps **at least 3 px of black** between itself and the inner 35 ring (2 px vertically in a
  24-px box holding the 12-px font).
* **A widget's rectangle lies inside its frame.** `label` and `in_text` paint their whole rect
  black (`bg textbg`) before the text, so a label rect as big as its box erases the box's tubes -
  the OPTIONS title lost its frame that way. Give the widget the box's interior (box + 4 on each
  side), never the box.
* **The view's right and bottom walls are drawn UNDER the panel content.** The stock layout puts the
  panel's plates at x 516 and its screens at 518 while the map's edge tube is 512..514, so BUILD's
  ring and the screens' rings lie on the wall: the wall is BAND | LT | dark from the map outward (a
  screen's ring, BAND then LT from its black, coincides with it) and is painted before the screens
  and plates; only the screen-edge borders are painted last. Pictures the engine draws over the wall
  (the tab strips) are transparent outside their plates.
* **Nothing crosses the border tube, and a plate inside a screen keeps 2 px of black to the
  screen's rings.** The tab strips are 120 px with 36x14 plates at x 4 / 43 / 82, rows 96..109 (2 px
  of black on every side); the stock 640x480 layout's 2-px margin does not exist any more.
* **The bar's arrows are bare triangles in one capsule channel** (x 4..43, rows H-23..H-5) drawn
  into the frame; the `pushb` cells are transparent around the triangle, centred on the channel's
  halves. The message screen's interior is H-20..H-8 so its rings end above the bottom border; the
  two bar texts sit at stock y 460 / 461 (2 px higher than shipped) inside it.
* **Header lines a pass adds are re-inserted canonically** (fonts 1 / 2, `bright_*`, the OK / CANCEL
  texts): the pass drops its own earlier additions first, so a file edited twice, or edited before or
  after the MUSIC-row edit, comes out byte-identical to one edited once - the patcher and the tool
  chain must agree on every path.
* **A plate may be drawn narrower than its click rectangle** when a neighbour needs the room: the
  BUILD plate is 81 px in an 86-px rect, which gives the DAYS screen a 32-px interior (601..632;
  its bottom ring ends above the dial's bezel) with the count centred at stock (607, 430).
* **Budget the rows before choosing a font.** An `in_text` paints a black rect of its font's line
  height (12 rows for `MFONTO7`) from its y, and its digits are 10 rows tall; the DAYS screen has 22
  rows between the status ring and the dial, so its caption is the frame's own **7-row pixel
  lettering** (`caption_5x7`, `CAPTION_5X7`: 5x7 capitals in the green ramp) on rows 420..426, a
  3-row gap, the count's rect on 430..441. A caption in `MFONTO7` (10 rows) could only touch it.
* A frame is never 1 px, never a single light line, never a bevel. The stock "thin light line" list
  window of §10.49 was wrong and is gone.

**The element capsule** is the second frame type, the one the lobby puts around every field of a
slot row (TCPWAIT.GIF, measured): a **3-px outline `23 | 65 | 23`** on black - dim, because it is
furniture, not a screen - rectangular, with a **rounded outer end** where a row begins or ends -
the outline itself bends into a semicircle, so the frame stays closed; an arc floating inside a
rectangle is an open frame - and **grey bars** (3 px thick, the same greys, 6 px long) joining the
capsules of one row. Every element of a form - a label, a value, a stepper
arrow - sits in its own capsule; the bars are the "lines between clickable elements". Text starts
4 px right of the arc; a plate inside a capsule keeps 1 px of black around it. The light 107 line
belongs to the screen frames only; a capsule never uses it.

Implementation: `hud_console.tube_frame(cv, x0, y0, x1, y1)` (inclusive rect; coordinates may lie
off the canvas, so a frame that continues into the next row is drawn with its far edge outside the
cell), `hud_console.panel_border`, `hud_console.capsule` / `option_strip` (the capsules and bars of
one option row, POPP cell 23), `patch_online.draw_frame` (the same greys as `FRAME_GREYS`),
patcher `Write-OnlineScreen` / `Edit-DialogConsole`.

## 4. Plates (buttons)

* **Lobby plate** (`lobby_plate`, from `KNOBE.SPR` cells 0/4/10/28): black fill, a 3-px red ring -
  outer 100 with 101 corners, the bright line 80, inner 100 with 98/99 corner softening. Used for
  every clickable button: BUILD, the active tab, the bar arrows, dialog buttons.
* **Text buttons** are the pre-battle menus' buttons and the first choice for any dialog action:
  `KNOBE.SPR` cell 2 (90x26, MEDBUTTON) or cell 0 (180x26, LARGEBUTTON) with `label centre <msg> 0 -
  remap 0` - the caption in the lobby's colours, centred by the engine - and the script-level
  `bright_pushed 8` / `bright_highlight 4`, which brighten the plate under the pointer and while
  pressed (the pressed state needs no second cell: `-11 <cell>`). Actions read as words (OK, CANCEL,
  LOAD, BACK), not as icons; the action sits right, the way back left (LOADGE: BACK left, LOAD right).
  A dialog bank that needs them copies the KNOBE cells pixel for pixel (POPP cells 20..22).
* **A plate is a button.** Never put a label or a title on a red plate: the eye reads it as
  clickable. Labels are plain text (§9), titles are plain text in a header box (§7).
* **Pressed plate** (`pressed_plate`): the green grid (fill 125, lines 124 every 8 x 7 px, border
  123). Only for the pressed / lit state.
* **Grey plate** (`grey_plate`): 35-grey fill, 11 outer ring, light ring, 43 inner ring. For
  inactive tabs and for the grey frames of grid buttons (portraits, unit orders, tab 3).
* **Arrows** are `KNOBE.SPR`'s own triangles: symmetric outline triangles in the red ramp, centred
  in the plate (bar cells on (9.5, 9), dialog 16x16 cells on (8, 8)); `arrow_cell`, `_triangle`.
  **Value steppers** are KNOBE's ringed 14x14 arrows, cell 14 (left) and 16 (right), as the lobby's
  slot rows use them (`pushb ... 14 14 -11 14` / `-11 16`): the value field sits between them.
* Plates are opaque: black is 254 inside a cell, never 0. A plate never carries pipework.
* **Button icons**: keep original portraits pixel for pixel inside the plate. Drawn icons follow
  `BUTTON.SPR`'s neon idiom - one hue per icon (`NEON_RAMPS`), a hot-key box top-left, the circuit
  line (`action_template`, `action_cell`); wide hot keys (F11, ESC) get the wide box with `MFONTO7`
  glyphs. Never auto-trace metal glyphs (§10.49 third round: "messy").

## 5. Pipework (the ground)

Everything that is not a screen, a frame or a plate is pipework: the HUD's panel column, bar and
borders, the dialog rows. `Pipework(cv, seed)` fills a rectangle with horizontal bands of
compartments (1-px 43 outlines on 11, nested), vent blocks (light dashes on 35), plain 23 cells and
rounded 35 tubes with a light edge, separated by tube bands; every cell has a 1-px 11 ring. Rules:

* **Seed it** (`W*10007+H` for a frame, `5000+row` for a dialog row) so every run of the tool gives
  the same bytes - the patcher compares against fixtures.
* **Every compartment is closed.** Pipework is laid around the screens (with their rings), the
  plates and the border tubes - `Pipework.region(..., holes)` cuts the area into strips at the
  holes' edges and fills only the free rectangles - so no compartment ring is ever cut by a
  screen, a plate or the screen edge. A cell that touches a hole keeps its own 11-grey ring and
  the hole's outer ring makes the seam. Borders are drawn last, so nothing cuts them either.
* Pipework is a background: nothing is read from it, nothing sits on it without a frame or a plate.
  Text on bare pipework is forbidden (the value read-outs of the options dialog were the last case).
* Rows of one kind tile: a dialog row cell is drawn once and repeated, so a row must look right
  above and below a copy of itself.

## 6. Dialog anatomy (`POPP.SPR` + `LOPTE LQCE LSGE LOBJE`)

A battlefield dialog is a stack of **304x16 rows** (pictures of cells 0..5) with widgets on top; x
below is relative to the row's x, y to the row's y. The layout is fixed so that every dialog looks
the same and every list gets its scroll channel:

| x | what |
|---|---|
| 0..3 | panel border (35, 107, 35, 11) |
| 4..5 | ground |
| 6..9 | list frame (11, 35, 107, 35) |
| **10..265** | **list window** (256 px black) - the `list` widget's x = row + 10 |
| 266..269 | list frame (35, 107, 35, 11) |
| 270..271 | black (the panel's interior between the two frames; ground before §10.55) |
| 272..275 | scroll channel frame |
| **276..293** | **scroll channel** (18 px black): the 16x16 UP / DOWN plates at x 277 with 1 px black around, the engine's 10-px `scroll` bar at x 280 |
| 294..297 | scroll channel frame |
| 298..299 | ground |
| 300..303 | panel border (11, 35, 107, 35) |

Row kinds: **16 / 17 / 18** the form panel top / middle / bottom (one tube frame x 6..297, black
inside - the body of EVERY dialog since §10.55), **3 / 27 / 4 / 5** a list compartment of that
panel: list top at the panel's top / list top inside the panel / middle / bottom (the list window's
frame x 6..269 and the scroll channel's frame x 272..297 share the panel's side tubes; the block's
top and bottom edges are full-width dividers - on rows 0..3 of cells 3 / 27, rows 12..15 of cell 5 -
so the list interior runs from the top row + 4 to the bottom row + 11), **24 / 25** blank / blank top
under the header box. Cells **0 / 1 / 2** (pipework top / plain / bottom) are the pre-10.55 rows and
are no longer laid out. Title plates (cell 6, 112x24), OK (7),
cancel (8) and the arrows (10 up, 11 down, 12 left, 13 right) are placed by the script. Two more
cells are **text boxes** (§7): 14 the value box 78x24, 15 the name box 264x24.

Vertical rules for a list: `list` y = top row + 4, height to the bottom row + 11; UP at top row + 5,
DOWN at bottom row - 5 (1 px black above / below the plate); the `scroll` bar from top row + 21,
height = bottom - top - 26. Every widget position is **derived from the rows**, never typed as a
screen coordinate: `hud_console.console_dialog` and the patcher's `Edit-DialogConsole` compute them,
so a dialog letterboxed to any resolution comes out right.

**Every dialog is a form** (§10.54): rows 0..2 are the blank cells 25 / 24 under the header box
(cell 19, 292x44 at x 6, y0 + 4) with the title as a font-1 (`MFONTO2`) label inside it, the red
title and label plates are gone, and the buttons are text buttons with their captions in font 2
(`MFONTO5`): OK (id 56) at x 158 and CANCEL (id 55) at x 56 as 90x26 plates (a lone OK centred at
x 107), the quit dialog's YES, QUIT / NO, CONTINUE as 180x26 plates (cell 26) at x 62. Rows 3..last
are the black panel in every dialog (§10.55): the list block is a compartment of it (cells 3 / 27 /
4 / 5, dividers full width), the save name sits in a 280x24 box (cell 15) inside the panel at
(x 12, top row + 6), the buttons stand on the panel's black. Which rows are the list's is decided by
the `list` widget's y range, never by a row's cell (the stock save dialog frames its name field with
two list cells).
The body font (font 0) is switched to `MFONTO5` only where the text is ours - the options form;
the objectives list's lines are wrapped for `MFONTO7` and overflow the window in a wider font.

**The options form** (a dialog with "-" / "+" pairs; §10.54) follows the pre-battle menus instead of
the list layout: rows 3..last are **one framed panel** the full inner width (cells 16 top / 17 middle /
18 bottom, frame x 6..297, black 10..293; the bottom row carries the panel's bottom tube on rows
4..7, ground and the dialog border on 12..15), the title is a font-1 (`MFONTO2`, 21 px) label
centred in a **header box** (cell 19, 292x44 at x 6, y0 + 4: it fills the space between the dialog
border and the panel's top tube, the rows behind it are the blank cells 25 / 24 - border and ground,
no pipework; the label's rect is 284x28 at + 4 / + 13, inside the box), and
inside the panel every option is one **capsule strip** (cell 23, 282x24 at x 11, y0 + 58 + 32 k):
four capsules - label 140 px with a rounded left end, "<" 22, value 70, ">" 32 with a rounded right
end - joined by three 6-px bars; the label text at x 27 (116 px), the 8-column centred value at
x 188 (exactly the capsule's 64-px interior), the 14x14 stepper plates at x 161 and x 265, all on
y0 + 63 + 32 k (texts 1 px lower); the first strip 6 px under the panel's top. CANCEL (x 56) and OK
(x 158), 90x26 text buttons, sit 32 px under the last option's arrows, 12 px above the panel's
bottom. A 32-px band added to the form (the MUSIC row) moves the buttons and the bottom
rows down and duplicates a middle row - `music_row` / `Edit-MusicDialog` do that, the layout pass
re-derives every position afterwards.

Widget ids are one object space for every kind (a `picture 24` and a `pushb 24` collide), so new
overlay pictures take the lowest free ids from 23 and are listed after the rows they cover.

## 7. Text boxes

Any text the engine writes - a read-out (`in_text ... read_only`), an entry field (`in_text`), a list
(`list`) - sits in a black box with the frame of §3. A **single-line text box is 24 px tall**: frame
4 + black 16 + frame 4; a 10-px (`MFONTO7`) or 12-px (`MFONTO5`) line sits vertically centred with
3 / 2 px of black above and below. Widths:

* **form panel** (options dialog): one framed black panel holds every option line; on it each
  element - label, "<", value, ">" - has its own capsule (§3) and the capsules of a line are joined
  by bars, the lobby's slot-row look; the light-tube box per value (POPP cell 14, 78x24, the 30 Sep
  morning form) is kept as a spare cell but is not the style: heavy frames belong to screens;
* **name box** (save game): 280 px inside the panel (x 12..291, 2 px of black to the panel's
  tubes), the field 4 px in from the interior's left edge; a field is left-aligned, a read-out
  centred;
* **list window**: 256 px inside its frame; the list widget paints its own black and its own text
  inset - do not add a second frame inside it.

The box is a **picture of a POPP cell laid over the rows** and listed before the widget that writes
into it; the widget paints its black over the box interior and its text on top. Text colour: the
scripts' `remap 4` for labels (`MFONTO5` comes out green) and titles (`MFONTO2` stays cyan), the
un-remapped cyan for the values a player changes (in game: green labels, cyan values and title, as
the lobby's rows), `remap 0` for button captions, `CAPTION_RED` for BUILD; never white on pipework.

## 8. Scroll bars

The engine draws the `scroll` widget itself (a 10-px red track with a red thumb). It always lives in
a **scroll channel**: a black column 18 px wide with UP above and DOWN below, wrapped in the frame,
2 px of the panel's black from the list frame (§6). On menu screens the same channel is drawn into the
background picture (`patch_online.frame_rects`: UP x - 4 .. x + w + 4). Never let a bar run over
pipework or over the panel border.

## 9. Text

* Fonts: `MFONTO5` (7x12, 12-px lines: the pre-battle menus' body font - labels, values, button
  captions, the save name, list rows, BUILD; it advances 8 px per column, so a 56-column list is
  448 px), `MFONTO2` (13x21 caps, descenders to 28: screen titles - "Load Game", "OPTIONS"),
  `MFONTO7` (6x10: tab digits, hot keys, the stock dialogs' small text - not for new forms).
  Glyph index = `ord(ch) - 31`.
* Captions on plates are drawn with the font's glyphs in one flat colour or the lobby ramp - no
  anti-aliasing, no drop shadow.
* Labels that name a row (GAME SPEED) are plain left-aligned text in their own capsule; the value
  they name sits at the right of the same line, in its capsule, between the stepper arrows (§6).
* Keep 3 px between glyphs and any frame ring; centre read-outs, left-align entry fields.

## 10. Icons and pictures

* Original unit / building / upgrade portraits are used pixel for pixel; art is never resampled up
  (re-render from geometry or an SVG master; `logo_art.py`, `make_dc_icon.py` are the examples).
* Drawn glyphs are vector shapes rasterised at 8x and thresholded into a 3-step ramp (`Glyph`,
  `_icon`, `_neon`), one hue per icon, a thin stroke; no gradients, no metal.
* The stock engine draws a few things by code (the day / night dial, the credits TTY, the chat
  lines): give them a matching background in the frame (`hud_console.py clock`, `_dial`), never a
  second copy of the art.

## 11. Menu screens (full-frame pictures)

A menu background is exactly framebuffer-sized (the blit has no stride), widget coordinates are
absolute, and `unmask` sprites carry the background baked in - re-bake them after any background
change (`paint_intro.py`). Screens derived from a stock screen (ONLINE from LOADGE) get their frames
drawn into the background GIF (`online_background`): header + list, scroll channel, text lines, the
same 35/107/35 tube as §3 with the 2-px black gap. Decorative animated gadgets that would repaint
over a frame or a text line are removed from the script, the art under them stays.

## 12. Process: from idea to shipped file

1. **Measure first.** Sample the reference picture (palette indices, run lengths) and write the
   numbers into the tool as named constants (`hud_console.py` §"the dialog plates").
2. **Draw by rule, seeded.** The tool must reproduce the shipped bytes on every run; the patcher's
   PowerShell port must produce the same text / pixels (the fixtures in
   `Dark-Colony-development/hd_sets/<WxH>/` are the reference, compare with CR stripped).
3. **Render a preview** (`hud_console.py preview`, or a scratch renderer that composes the script
   over the cells) and look at it at 2x. Show stock and new side by side before iterating -
   judgement is faster on a pair.
4. **Test in the game** (a `subst` drive or the game folder; DPI-aware `SendInput` driver; every
   dialog opened; `error.log` empty). A fix that touches the click path is clicked through.
5. **Regenerate**: the game folder (`hud_console.py apply GAME --width W --height H`), the six
   fixtures (`--no-bank`), the patcher (`gen_apply_script.py`), then verify the patcher on a scratch
   copy under pwsh 7 and 5.1 and check that the exe hashes did not move for data-only work.
6. **Document**: `DC16_DISPLAY_AND_RESOLUTION.md` §10.x (what, why, measurements, what was not run),
   `RELAY_SERVER_PLAN.md` §16, this guide if a rule changed.

## 13. Don'ts

* No metal, bevels, gradients or anti-aliased edges.
* No labels or titles on button plates; no icon-only OK / cancel where a word fits.
* No bare element on a black panel: every label, value and control gets a capsule, and the
  elements of one row are joined by bars.
* No open frame: a compartment cut by a screen, a plate or the screen edge, a border line cut by a
  screen's ring, or a rounded end drawn as a loose arc. Check the edges at 4x before shipping.
* No text on bare pipework; no pipework inside a dialog: a dialog's body is the black panel, and
  every black area on it is a framed compartment, a box or a plate.
* No layout pass proven on already laid-out inputs only: keep the STOCK scripts in the harness
  (§10.55's name-field rows).
* No 1-px frames, no frames touching each other or the border.
* No index 0 for visible black in a sprite cell; no cyan ramp in art that must keep its colour in a
  `pushb` cell.
* No hand-typed screen coordinates for widgets that belong to a row or a frame - derive them.
* No resampling of the original art.
* No one-size measurements applied to another size without re-measuring (§10.36's lesson).

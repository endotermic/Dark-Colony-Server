#!/usr/bin/env python3
"""The battlefield HUD in the game's own menu style (doc 10.49).

Dark Colony has two visual languages.  Every menu screen - race selection (SHUMAN.GIF), the network
lobby (MULTIWIN.GIF, NET.GIF, SERVER.GIF), story, victory, encyclopedia - is grey pipework: dark
greys of several intensities (palette 67 = (11,11,11), 66 = (23,23,23), 65 = (35,35,35),
62 = (43,43,43)) with light edge lines (40 = (107,107,107)), nested compartments, vents and rounded
tubes; black only inside the read-out screens; buttons are black plates with a 3-px red ring
(KNOBE.SPR) whose pressed state is a green grid; the only icons are clean red-outline glyphs (the
arrows).  The battlefield alone - the HUD frame INTRFACE.GIF, its cell bank MAINBUT.SPR and the
dialog plates POPP.SPR - was brushed, bevelled metal.  The maintainer asked for one style
(28 Sep 2026: "battlefield interface and menus styles are bad. You must create the same style as in
race selection, network lobby and other menus"; "keep the icons and portraits, red-outlined button
icons which mimics lobby button style everywhere"; "frames must be in lobby style. lobby is not
black, it is in different intensities of gray").

    frame    --width W --height H --out INTRFACE.GIF
             the HUD frame at any size from geometry (no resampling): pipework over the panel
             column, the bottom bar and the borders (seeded, so every run gives the same picture),
             black screens where the engine or a widget paints (minimap, button grid, status line,
             DAYS, money, dial, message box), the BUILD button and the two bar arrows as lobby plates
    bank     GAME_DIR --out INTRF_HD/MAINBUT.SPR
             the 133-cell HUD bank on lobby plates: the unit / building / upgrade portraits of
             MAINBUT.SPR pixel for pixel (maintainer), clean red-outline glyphs drawn here for every
             order and option button, KNOBE's own triangles for the arrows, the tab strips, PAUSED
    popp     GAME_DIR --out INTRF_HD/POPP.SPR
             the 24 dialog plates: pipework rows with the lobby tube as panel border, list rows with
             the framed list window and the framed scroll channel (3 / 4 / 5), red title plate, red
             OK / cancel, red arrows, the framed text boxes (14 spare value box, 15 name box), the
             options form's panel rows (16 / 17 / 18) and header box (19) and the lobby's text button
             and stepper arrows copied from KNOBE.SPR (20 / 21 / 22) and the option row's capsule strip
             (23) - doc 10.53 / 10.54 and
             docs/DC16_INTERFACE_STYLE_GUIDE.md; the scripts are laid out on them by console_dialog()
    apply    TARGET [--game SRC] --width W --height H [--no-bank]
             frame + banks + the script edits (`pictures intrf_hd/mainbut|popp`, tab strips) into a
             game folder or an hd_sets/<WxH> fixture
    preview  GAME_DIR --width W --height H [--orders] --out preview.png

Geometry shared with hud_layout.py (imported): the view is (4,6) .. (W-125, H-27-slack), panel
widgets at x >= 516 move right by W-640 and, from y 399 down, also down by H-480; bottom furniture
moves down by H-480; spare rows of the 32-px tile grid widen the bar at its top.

Palette: PALETTE.GIF indices; the terrain palettes carry the same colour within +-3 at every index
(measured 28 Sep 2026).  Index 254 is the erase colour: the hole is written as 254 exactly.  The cyan
ramp 128..143 is remapped to the player's team colour in widget-drawn cells (observed in game).
"""

import argparse
import math
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spr                                   # noqa: E402
import hud_layout                            # noqa: E402

# ---------------------------------------------------------------- palette (PALETTE.GIF indices)
BLK = 254        # black, the erase colour
D11 = 67         # (11,11,11)   the pipework's ground
D23 = 66         # (23,23,23)
BAND = 65        # (35,35,35)   tube / band body
G43 = 62         # (43,43,43)   compartment outlines
WARM = 60        # (57,49,49)
G65 = 55         # (65,65,65)
G82 = 49         # (82,82,82)
LT = 40          # (106,106,106) light edge line
G115 = 36
WHITE = 1
GRN = (120, 121, 122, 123, 124, 125)        # (103,255,0) (91,203,0) (67,159,0) (51,119,0) (31,79,0) (15,39,0)
RED = (96, 97, 98, 99, 100, 101)            # (255,31,31) (203,23,23) (159,19,19) (119,11,11) (79,7,7) (39,7,7)
CYAN = (138, 139, 141, 142, 143)
CYAN_TO_GREEN = {138: 120, 139: 121, 140: 121, 141: 122, 142: 123, 143: 124}
CYAN_TO_RED = {138: 97, 139: 98, 140: 98, 141: 99, 142: 100, 143: 101}

# the lobby button plate (KNOBE.SPR cells 0, 4, 10, 28, measured): outer 100 with 101 corners, the
# bright line 80 = (255,47,0), inner 100 with 98 / 99 corner softening; pressed = green grid
L_OUT, L_BRIGHT, L_CORNER, L_IN1, L_IN2 = 100, 80, 101, 98, 99
P_FILL, P_LINE, P_BORDER = 125, 124, 123

# ---------------------------------------------------------------- stock geometry (640x480)
SRC_W, SRC_H = hud_layout.SRC_W, hud_layout.SRC_H
INSET_X, INSET_Y = hud_layout.INSET_X, hud_layout.INSET_Y
VIEW_W, VIEW_H = hud_layout.VIEW_W, hud_layout.VIEW_H
PANEL_X = hud_layout.PANEL_X          # 516
BOTTOM_Y = hud_layout.BOTTOM_Y        # 454
PANEL_INSERT = hud_layout.PANEL_INSERT  # 399

MINIMAP = (518, 6, 614, 89)           # engine paints 96x84 at (519,6)
LETTER_STRIP = (617, 8, 634, 88)      # the vertical DARK COLONY beside the minimap
GRID = (518, 92, 635, 398)            # tab row 92..111 + 2 x 7 cells of 59x41 from 112
STATUS = (520, 404, 633, 415)         # in_text 79 at (520,404)
BUILD = (516, 422, 601, 448)          # pushb 19, 86x27 (the click rect)
BUILD_PLATE = (516, 422, 596, 448)    # the plate drawn 5 px narrower than the rect, so the DAYS screen gets a 32-px interior (maintainer: 'DAYS frame is too tight')
DAYS_TEXT = (606, 420)                # the DAYS caption (7-row pixel lettering, CAPTION_5X7) on rows 420..426; the count widget (in_text 234)
                                      # paints a 12-row black rect from its y 430, so the caption must end above 430
DAYS_BOX = (609, 433, 634, 444)       # in_text 234 at (613,433)
DAYS_PANEL = (601, 420, 632, 441)     # one screen for the DAYS label and its count: 1 px off the BUILD plate, right ring before the border tube, bottom ring above the dial's bezel
MONEY = (521, 456, 598, 472)          # scount 75 at (524,456)
DIAL = (621, 463, 15)                 # the 28x28 hand cell lands at (608..635, 450..477): centre (621.5, 463.5), measured in game
ARROWS = ((4, 460, 23, 478), (24, 460, 43, 478))   # the bar's two arrow buttons (pushb 147 / 149, MAINBUT cells 40 50 / 57 76)
CHAT_CHANNEL = (4, 457, 43, 475)      # one capsule around both arrows: rows 745..763 at 768 = between the view's tube and the bottom border
BAR_ARROW_CENTRE = {'up': (11.0, 6.0), 'down': (8.0, 6.0)}   # triangle centres in the 16x16 cells drawn at the rects' top-left: the channel's two halves
MSG = (49, 460, 509, 472)             # the message screen: interior H-20..H-8, rings to H-5, the border from H-4; in_text 148 / 200 2 px higher (CHAT_LINE_Y)
HUD_TEXT_POS = {148: (None, 460), 200: (None, 461), 234: (607, 430)}   # stock (x, y) of the bar texts (were y 462 / 463) and the DAYS count
                                      # (was 613, 433): the 20x10 digit glyphs start at the widget's y and its 12-row rect ends on the interior's
                                      # last row 441; 3 rows under the 7-row caption - the 22-row interior between the status ring and the dial


# ---------------------------------------------------------------- an index canvas
class Canvas:
    def __init__(self, w, h, fill=BLK):
        self.w, self.h = w, h
        self.px = bytearray([fill]) * (w * h)

    def put(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y * self.w + x] = c

    def get(self, x, y):
        return self.px[y * self.w + x] if 0 <= x < self.w and 0 <= y < self.h else BLK

    def hline(self, x0, x1, y, c):
        for x in range(min(x0, x1), max(x0, x1) + 1):
            self.put(x, y, c)

    def vline(self, x, y0, y1, c):
        for y in range(min(y0, y1), max(y0, y1) + 1):
            self.put(x, y, c)

    def fill(self, x0, y0, x1, y1, c):
        for y in range(y0, y1 + 1):
            self.hline(x0, x1, y, c)

    def ring(self, x0, y0, x1, y1, r, c):
        """1-px rounded rectangle outline (inclusive corners)."""
        r = max(0, min(r, (x1 - x0) // 2, (y1 - y0) // 2))
        self.hline(x0 + r, x1 - r, y0, c)
        self.hline(x0 + r, x1 - r, y1, c)
        self.vline(x0, y0 + r, y1 - r, c)
        self.vline(x1, y0 + r, y1 - r, c)
        if r:
            for cx, cy, sx, sy in ((x0 + r, y0 + r, -1, -1), (x1 - r, y0 + r, 1, -1),
                                   (x0 + r, y1 - r, -1, 1), (x1 - r, y1 - r, 1, 1)):
                for dx, dy in _arc(r):
                    self.put(cx + sx * dx, cy + sy * dy, c)

    def rfill(self, x0, y0, x1, y1, r, c):
        """Filled rounded rectangle."""
        r = max(0, min(r, (x1 - x0) // 2, (y1 - y0) // 2))
        for y in range(y0, y1 + 1):
            dy = max(0, (y0 + r) - y, y - (y1 - r))
            dx = r - int(round(math.sqrt(max(0, r * r - dy * dy)))) if dy else 0
            self.hline(x0 + dx, x1 - dx, y, c)

    def screen(self, x0, y0, x1, y1):
        """A black read-out window: interior black, light edge line, band, dark gap."""
        self.fill(x0, y0, x1, y1, BLK)
        self.ring(x0 - 1, y0 - 1, x1 + 1, y1 + 1, 1, LT)
        self.ring(x0 - 2, y0 - 2, x1 + 2, y1 + 2, 2, BAND)
        self.ring(x0 - 3, y0 - 3, x1 + 3, y1 + 3, 3, D11)

    def vents(self, x, y, n, step=3, w=4, vertical=True, c=LT, c2=D11):
        for i in range(n):
            if vertical:
                self.hline(x, x + w - 1, y + i * step, c)
                self.hline(x, x + w - 1, y + i * step + 1, c2)
            else:
                self.vline(x + i * step, y, y + w - 1, c)
                self.vline(x + i * step + 1, y, y + w - 1, c2)

    def blit(self, cell, x, y, transparent=(0, BLK), remap=None):
        w, h, px = cell['w'], cell['h'], cell['px']
        for yy in range(h):
            for xx in range(w):
                v = px[yy * w + xx]
                if v in transparent:
                    continue
                if remap:
                    v = remap.get(v, v)
                self.put(x + xx, y + yy, v)

    def image(self, palette):
        from PIL import Image
        im = Image.frombytes('P', (self.w, self.h), bytes(self.px))
        im.putpalette(palette)
        return im


def _arc(r):
    pts = set()
    x, y, err = r, 0, 1 - r
    while x >= y:
        pts.update({(x, y), (y, x)})
        y += 1
        if err < 0:
            err += 2 * y + 1
        else:
            x -= 1
            err += 2 * (y - x) + 1
    return list(pts)


# ---------------------------------------------------------------- the lobby button plate
def lobby_plate(w, h, canvas=None, x0=0, y0=0):
    cv = canvas or Canvas(w, h)
    x1, y1 = x0 + w - 1, y0 + h - 1
    cv.fill(x0, y0, x1, y1, BLK)
    cv.ring(x0, y0, x1, y1, 0, L_OUT)
    for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        cv.put(x, y, L_CORNER)
    cv.ring(x0 + 1, y0 + 1, x1 - 1, y1 - 1, 0, L_BRIGHT)
    cv.ring(x0 + 2, y0 + 2, x1 - 2, y1 - 2, 0, L_OUT)
    for cx, cy, sx, sy in ((x0 + 2, y0 + 2, 1, 1), (x1 - 2, y0 + 2, -1, 1), (x0 + 2, y1 - 2, 1, -1), (x1 - 2, y1 - 2, -1, -1)):
        cv.put(cx, cy, L_IN1)
        cv.put(cx + sx, cy, L_IN2)
        cv.put(cx, cy + sy, L_IN2)
    return cv


def pressed_plate(w, h, canvas=None, x0=0, y0=0):
    cv = canvas or Canvas(w, h)
    cv.fill(x0, y0, x0 + w - 1, y0 + h - 1, P_FILL)
    for x in range(x0 + 8, x0 + w - 1, 8):
        cv.vline(x, y0, y0 + h - 1, P_LINE)
    for y in range(y0 + 9, y0 + h - 1, 7):
        cv.hline(x0, x0 + w - 1, y, P_LINE)
    cv.ring(x0, y0, x0 + w - 1, y0 + h - 1, 0, P_BORDER)
    return cv


# ---------------------------------------------------------------- the lobby pipework
class Pipework:
    """Fills a rectangle with the menu screens' grey pipework: horizontal bands of compartments
    (1-px 43-grey outlines on 11-grey, nested), vent blocks (light dashes on 35-grey), plain 23-grey
    cells and rounded 35-grey tubes with a light edge, separated by tube bands.  Seeded per region so
    every run of the tool reproduces the shipped picture byte for byte."""

    def __init__(self, cv, seed):
        self.cv = cv
        self.rng = random.Random(seed)

    def region(self, x0, y0, x1, y1, holes=()):
        """Fill the rectangle with pipework, leaving every hole (inclusive rects: the screens with their
        rings, the plates, the border tubes) untouched and every compartment whole: the area is cut
        into horizontal strips at the holes' top and bottom edges, each strip into the x-intervals
        free of holes, and each free rectangle is filled by fill_rect().  A compartment cut by a screen
        or a border is an open frame (maintainer, 30 Sep 2026); the cells' own 11-grey rings and the
        holes' outer rings give the dark seam between them."""
        if x1 < x0 or y1 < y0:
            return
        hs = [h for h in holes if h[2] >= x0 and h[0] <= x1 and h[3] >= y0 and h[1] <= y1]
        cuts = {y0, y1 + 1}
        for hx0, hy0, hx1, hy1 in hs:
            if y0 < hy0 <= y1:
                cuts.add(hy0)
            if y0 <= hy1 < y1:
                cuts.add(hy1 + 1)
        ys = sorted(cuts)
        for ya, yb in zip(ys, ys[1:]):
            yb -= 1
            blocked = sorted((hx0, hx1) for hx0, hy0, hx1, hy1 in hs if hy0 <= yb and hy1 >= ya)
            x, free = x0, []
            for bx0, bx1 in blocked:
                if bx0 > x:
                    free.append((x, min(bx0 - 1, x1)))
                x = max(x, bx1 + 1)
            if x <= x1:
                free.append((x, x1))
            for fx0, fx1 in free:
                if fx1 - fx0 >= 2:
                    self.fill_rect(fx0, ya, fx1, yb)

    def fill_rect(self, x0, y0, x1, y1):
        """Bands of compartments separated by tube bands; a rect under 3 rows is ground, under 8 a tube."""
        cv, rng = self.cv, self.rng
        cv.fill(x0, y0, x1, y1, D11)
        if y1 - y0 < 2:
            return
        if y1 - y0 < 7:
            self.tube(x0, y0, x1, y1)
            return
        y = y0
        first = True
        while y <= y1:
            if not first:
                th = rng.choice((4, 5, 6, 7))
                yt = min(y + th - 1, y1)
                self.tube(x0, y, x1, yt)
                y = yt + 1
                if y > y1:
                    break
            first = False
            bh = rng.randint(16, 56)
            yb = min(y + bh - 1, y1)
            if y1 - yb < 10:
                yb = y1
            self.band(x0, y, x1, yb)
            y = yb + 1

    def tube(self, x0, y0, x1, y1):
        """A horizontal tube band: 35-grey body, light line along its top, dark line under it."""
        cv = self.cv
        cv.fill(x0, y0, x1, y1, BAND)
        cv.hline(x0, x1, y0, LT)
        if y1 > y0 + 1:
            cv.hline(x0, x1, y1, D11)
        if y1 - y0 >= 5:
            cv.hline(x0, x1, y1 - 1, D23)

    def band(self, x0, y0, x1, y1):
        cv, rng = self.cv, self.rng
        x = x0
        h = y1 - y0 + 1
        while x <= x1:
            w = rng.randint(10, 44)
            xb = min(x + w - 1, x1)
            if x1 - xb < 8:
                xb = x1
            style = rng.choice(('comp', 'comp', 'nested', 'vent', 'plain', 'tube', 'comp2'))
            if xb - x < 9 or h < 9:
                style = 'plain'
            self.cell(style, x, y0, xb, y1)
            x = xb + 1

    def cell(self, style, x0, y0, x1, y1):
        cv, rng = self.cv, self.rng
        if style == 'plain':
            cv.fill(x0 + 1, y0 + 1, x1 - 1, y1 - 1, D23)
            cv.ring(x0 + 1, y0 + 1, x1 - 1, y1 - 1, 0, G43)
        elif style in ('comp', 'comp2'):
            cv.fill(x0 + 1, y0 + 1, x1 - 1, y1 - 1, D11)
            cv.ring(x0 + 1, y0 + 1, x1 - 1, y1 - 1, 0, G43)
            if style == 'comp2' and x1 - x0 > 16 and y1 - y0 > 12:
                # split into two compartments
                if rng.random() < 0.5:
                    xm = (x0 + x1) // 2
                    cv.vline(xm, y0 + 1, y1 - 1, G43)
                else:
                    ym = (y0 + y1) // 2
                    cv.hline(x0 + 1, x1 - 1, ym, G43)
            if rng.random() < 0.5 and x1 - x0 > 14 and y1 - y0 > 12:
                cv.ring(x0 + 4, y0 + 4, x1 - 4, y1 - 4, 0, G65 if rng.random() < 0.5 else G43)
        elif style == 'nested':
            cv.fill(x0 + 1, y0 + 1, x1 - 1, y1 - 1, D11)
            k = 0
            while x1 - x0 - 2 * k > 8 and y1 - y0 - 2 * k > 8 and k < 12:
                cv.ring(x0 + 1 + k, y0 + 1 + k, x1 - 1 - k, y1 - 1 - k, 0, (G43, D23, G65)[(k // 3) % 3])
                k += 3
        elif style == 'vent':
            cv.fill(x0 + 1, y0 + 1, x1 - 1, y1 - 1, BAND)
            cv.ring(x0 + 1, y0 + 1, x1 - 1, y1 - 1, 0, G43)
            n = (y1 - y0 - 6) // 3
            if n > 0:
                cv.vents(x0 + 4, y0 + 4, n, step=3, w=max(2, x1 - x0 - 7), c=LT, c2=D11)
        elif style == 'tube':
            r = min(6, (x1 - x0) // 3, (y1 - y0) // 3)
            cv.rfill(x0 + 1, y0 + 1, x1 - 1, y1 - 1, r, BAND)
            cv.ring(x0 + 1, y0 + 1, x1 - 1, y1 - 1, r, LT)
            if x1 - x0 > 12 and y1 - y0 > 12:
                cv.ring(x0 + 4, y0 + 4, x1 - 4, y1 - 4, max(0, r - 3), D11)
        # dark gap around every cell
        cv.ring(x0, y0, x1, y1, 0, D11)


# ---------------------------------------------------------------- game files
def read_palette(game):
    from PIL import Image
    return Image.open(os.path.join(game, 'PALETTE.GIF')).getpalette()


def find_file(game, *parts):
    d = game
    for p in parts[:-1]:
        d = os.path.join(d, p)
    want = parts[-1].lower()
    for fn in os.listdir(d):
        if fn.lower() == want:
            return os.path.join(d, fn)
    raise FileNotFoundError(os.path.join(d, parts[-1]))


class Font:
    """MFONTO7: cell index = ord(ch) - 31 (cell 0 is the cursor block, cell 1 the empty space)."""

    def __init__(self, game, name='mfonto7.spr'):
        self.cells = spr.read_spr(find_file(game, 'INTRFACE', name))['cells']

    def glyph(self, ch):
        if ch == ' ':
            return None
        i = ord(ch) - 31
        return self.cells[i] if 0 <= i < len(self.cells) else None

    def width(self, text, spacing=1):
        w = 0
        for ch in text:
            g = self.glyph(ch)
            w += (g['w'] if g and g['w'] else 4) + spacing
        return w - spacing

    def draw(self, cv, x, y, text, remap=CYAN_TO_GREEN, spacing=1):
        for ch in text:
            g = self.glyph(ch)
            if g and g['w']:
                cv.blit(g, x, y, remap=remap)
                x += g['w'] + spacing
            else:
                x += 4 + spacing
        return x

    def draw_vertical(self, cv, x, y, text, remap=CYAN_TO_GREEN, spacing=1):
        for ch in text:
            g = self.glyph(ch)
            if g and g['w']:
                w, h = g['w'], g['h']
                for yy in range(h):
                    for xx in range(w):
                        v = g['px'][yy * w + xx]
                        if v in (0, BLK):
                            continue
                        cv.put(x + (h - 1 - yy), y + xx, remap.get(v, v))
                y += w + spacing
            else:
                y += 4 + spacing
        return y


# ---------------------------------------------------------------- clean red glyphs for the buttons
SCALE = 8
FONT_TTF = [os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', f) for f in ('arialbd.ttf', 'verdanab.ttf')]


class Glyph:
    """Vector-drawn icon rasterised through an 8x supersampled coverage map into the lobby's red
    ramp: full coverage -> 97 (203,23,23), partial -> 99 / 100.  Coordinates are in cell pixels."""

    def __init__(self, w, h):
        from PIL import Image, ImageDraw
        self.w, self.h = w, h
        self.im = Image.new('L', (w * SCALE, h * SCALE), 0)
        self.d = ImageDraw.Draw(self.im)

    def _s(self, pts):
        return [(p[0] * SCALE, p[1] * SCALE) for p in pts]

    def line(self, pts, width=2):
        self.d.line(self._s(pts), fill=255, width=int(width * SCALE), joint='curve')

    def poly(self, pts, width=2, fill=False):
        if fill:
            self.d.polygon(self._s(pts), fill=255)
        else:
            self.d.line(self._s(pts + [pts[0]]), fill=255, width=int(width * SCALE), joint='curve')

    def circle(self, c, r, width=2, fill=False):
        b = [(c[0] - r) * SCALE, (c[1] - r) * SCALE, (c[0] + r) * SCALE, (c[1] + r) * SCALE]
        if fill:
            self.d.ellipse(b, fill=255)
        else:
            self.d.ellipse(b, outline=255, width=int(width * SCALE))

    def ellipse(self, x0, y0, x1, y1, width=2, fill=False):
        b = [x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE]
        if fill:
            self.d.ellipse(b, fill=255)
        else:
            self.d.ellipse(b, outline=255, width=int(width * SCALE))

    def arc(self, x0, y0, x1, y1, a0, a1, width=2):
        self.d.arc([x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE], a0, a1, fill=255, width=int(width * SCALE))

    def arrow(self, p, q, width=2, head=6):
        """Line p->q with a filled arrow head at q."""
        self.line([p, q], width)
        ang = math.atan2(q[1] - p[1], q[0] - p[0])
        a1, a2 = ang + 2.5, ang - 2.5
        self.poly([q, (q[0] + head * math.cos(a1), q[1] + head * math.sin(a1)),
                   (q[0] + head * math.cos(a2), q[1] + head * math.sin(a2))], fill=True)

    def text(self, s, height, center):
        from PIL import ImageFont
        for path in FONT_TTF:
            if os.path.exists(path):
                f = ImageFont.truetype(path, int(height * SCALE))
                break
        else:
            f = ImageFont.load_default()
        b = self.d.textbbox((0, 0), s, font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        self.d.text((center[0] * SCALE - tw / 2 - b[0], center[1] * SCALE - th / 2 - b[1]), s, fill=255, font=f)

    def raster(self, bright=RED[1], mid=RED[3], dim=RED[4], lo=(150, 80, 35), size=None):
        from PIL import Image
        cov = self.im.resize(size or (self.w, self.h), Image.BOX)
        if size:
            self.w, self.h = size
        out = {}
        for y in range(self.h):
            for x in range(self.w):
                v = cov.getpixel((x, y))
                if v >= lo[0]:
                    out[(x, y)] = bright
                elif v >= lo[1]:
                    out[(x, y)] = mid
                elif v >= lo[2]:
                    out[(x, y)] = dim
        return out


def _icon(kind, w=53, h=35):
    g = Glyph(w, h)
    cx, cy = w / 2.0, h / 2.0
    if kind == 'quit':
        g.line([(cx - 12, cy - 12), (cx + 12, cy + 12)], 4)
        g.line([(cx + 12, cy - 12), (cx - 12, cy + 12)], 4)
    elif kind == 'options':
        g.text('?', 30, (cx, cy))
    elif kind == 'attack':
        g.circle((cx, cy), 11, 2)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            g.line([(cx + dx * 8, cy + dy * 8), (cx + dx * 15, cy + dy * 15)], 2)
        g.circle((cx, cy), 2, fill=True)
    elif kind == 'save':
        g.poly([(cx - 13, cy - 14), (cx + 10, cy - 14), (cx + 13, cy - 11), (cx + 13, cy + 14), (cx - 13, cy + 14)], 2)
        g.poly([(cx - 7, cy - 14), (cx + 6, cy - 14), (cx + 6, cy - 5), (cx - 7, cy - 5)], 2)
        g.poly([(cx - 8, cy + 3), (cx + 8, cy + 3), (cx + 8, cy + 14), (cx - 8, cy + 14)], 2)
    elif kind == 'stop':
        r = 15
        g.poly([(cx + r * math.cos(math.radians(22.5 + 45 * k)), cy + r * math.sin(math.radians(22.5 + 45 * k))) for k in range(8)], 3)
    elif kind in ('move', 'move_attack'):
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            g.arrow((cx + dx * 5, cy + dy * 5), (cx + dx * 16, cy + dy * 16 * (0.95 if dy else 1)), 2, 6)
        if kind == 'move_attack':
            g.poly([(cx + 3, cy - 8), (cx - 4, cy + 1), (cx + 1, cy + 1), (cx - 2, cy + 8), (cx + 5, cy - 1), (cx, cy - 1)], fill=True)
    elif kind == 'waypoints':
        pts = [(cx - 17, cy + 9), (cx - 3, cy - 9), (cx + 9, cy + 6), (cx + 18, cy - 8)]
        g.line(pts[:3], 2)
        g.arrow(pts[2], pts[3], 2, 6)
        for p in pts[:3]:
            g.poly([(p[0] - 3, p[1] - 3), (p[0] + 3, p[1] - 3), (p[0] + 3, p[1] + 3), (p[0] - 3, p[1] + 3)], fill=True)
    elif kind == 'shovel':
        g.line([(cx - 12, cy - 13), (cx + 4, cy + 3)], 3)
        g.poly([(cx + 2, cy + 1), (cx + 13, cy + 12), (cx + 6, cy + 15), (cx - 1, cy + 8)], fill=True)
    elif kind == 'turret':
        g.poly([(cx - 14, cy + 13), (cx + 14, cy + 13), (cx + 9, cy + 4), (cx - 9, cy + 4)], 2)
        g.arc(cx - 7, cy - 4, cx + 7, cy + 10, 180, 360, 2)
        g.line([(cx, cy + 1), (cx + 16, cy - 11)], 3)
    elif kind == 'mine':
        g.circle((cx, cy + 2), 8, 2)
        for k in range(8):
            a = math.radians(45 * k)
            g.line([(cx + 9 * math.cos(a), cy + 2 + 9 * math.sin(a)), (cx + 14 * math.cos(a), cy + 2 + 14 * math.sin(a))], 2)
    elif kind == 'napalm':
        g.poly([(cx, cy - 15), (cx + 7, cy - 6), (cx + 5, cy - 1), (cx + 12, cy - 3), (cx + 10, cy + 8), (cx + 4, cy + 14),
                (cx - 4, cy + 14), (cx - 11, cy + 7), (cx - 9, cy - 3), (cx - 4, cy), (cx - 6, cy - 8)], 2)
        g.poly([(cx, cy - 2), (cx + 4, cy + 4), (cx + 2, cy + 10), (cx - 2, cy + 10), (cx - 4, cy + 4)], fill=True)
    elif kind == 'disease':
        for k in range(3):
            a = math.radians(-90 + 120 * k)
            g.circle((cx + 6 * math.cos(a), cy + 1 + 6 * math.sin(a)), 7, 2)
        g.circle((cx, cy + 1), 2.5, fill=True)
    elif kind == 'deploy':
        g.line([(cx, cy - 14), (cx, cy + 4)], 4)
        g.poly([(cx - 11, cy + 2), (cx + 11, cy + 2), (cx, cy + 13)], fill=True)
        g.line([(cx - 15, cy + 15), (cx + 15, cy + 15)], 2)
    elif kind == 'steal':
        g.text('$', 30, (cx, cy))
    elif kind == 'allies':
        # a dove (maintainer: "diplomacy button must have a pigeon instead of eternity sign")
        g.ellipse(cx - 13, cy - 1, cx + 9, cy + 10, fill=True)                         # body
        g.circle((cx + 11, cy - 3), 4.5, fill=True)                                     # head
        g.poly([(cx + 15, cy - 3.5), (cx + 21, cy - 2), (cx + 15, cy - 1)], fill=True)  # beak
        g.poly([(cx - 5, cy), (cx - 15, cy - 15), (cx - 4, cy - 11), (cx + 5, cy - 1)], fill=True)   # wing
        g.poly([(cx - 12, cy + 2), (cx - 24, cy + 5), (cx - 23, cy + 11), (cx - 11, cy + 8)], fill=True)  # tail
        g.line([(cx + 1, cy + 10), (cx + 3, cy + 15)], 1.6)                             # legs
        g.line([(cx + 5, cy + 10), (cx + 6, cy + 15)], 1.6)
    elif kind == 'peace':
        g.circle((cx, cy), 13, 2.5)
        g.line([(cx, cy - 13), (cx, cy + 13)], 2.5)
        g.line([(cx, cy), (cx - 9.2, cy + 9.2)], 2.5)
        g.line([(cx, cy), (cx + 9.2, cy + 9.2)], 2.5)
    elif kind == 'eye':
        g.poly([(cx - 16, cy), (cx - 8, cy - 9), (cx + 8, cy - 9), (cx + 16, cy), (cx + 8, cy + 9), (cx - 8, cy + 9)], 2.5)
        g.circle((cx, cy), 5, fill=True)
    elif kind == 'coins':
        g.ellipse(cx - 12, cy - 4, cx + 12, cy + 4, 2.5)
        g.line([(cx - 12, cy), (cx - 12, cy + 8)], 2.5)
        g.line([(cx + 12, cy), (cx + 12, cy + 8)], 2.5)
        g.arc(cx - 12, cy + 4, cx + 12, cy + 12, 0, 180, 2.5)
        g.text('$', 12, (cx, cy))
    elif kind == 'give':
        g.line([(cx - 14, cy), (cx + 6, cy)], 3.5)
        g.poly([(cx + 4, cy - 9), (cx + 16, cy), (cx + 4, cy + 9)], fill=True)
    elif kind == 'objectives':
        g.poly([(cx - 20, cy - 10), (cx - 1, cy - 7), (cx - 1, cy + 13), (cx - 20, cy + 10)], 2)
        g.poly([(cx + 20, cy - 10), (cx + 1, cy - 7), (cx + 1, cy + 13), (cx + 20, cy + 10)], 2)
        for k in range(3):
            yy = cy - 3 + 5 * k
            g.line([(cx - 16, yy - 1.5), (cx - 5, yy)], 1)
            g.line([(cx + 5, yy), (cx + 16, yy - 1.5)], 1)
    elif kind == 'inspire':
        pts = []
        for k in range(10):
            r = 15 if k % 2 == 0 else 6.5
            a = math.radians(-90 + 36 * k)
            pts.append((cx + r * math.cos(a), cy + 1 + r * math.sin(a)))
        g.poly(pts, 2)
    elif kind == 'dropship':
        g.ellipse(cx - 16, cy - 8, cx + 16, cy + 6, 2)
        g.poly([(cx - 5, cy - 8), (cx + 5, cy - 8), (cx + 3, cy - 13), (cx - 3, cy - 13)], 2)
        for x in (cx - 10, cx, cx + 10):
            g.line([(x, cy + 6), (x, cy + 14)], 2)
    elif kind == 'saucer':
        g.ellipse(cx - 20, cy - 2, cx + 20, cy + 9, 2)
        g.arc(cx - 9, cy - 11, cx + 9, cy + 3, 180, 360, 2)
    elif kind == 'pause':
        g.poly([(cx - 10, cy - 11), (cx - 4, cy - 11), (cx - 4, cy + 11), (cx - 10, cy + 11)], fill=True)
        g.poly([(cx + 4, cy - 11), (cx + 10, cy - 11), (cx + 10, cy + 11), (cx + 4, cy + 11)], fill=True)
    return g


# BUTTON.SPR's neon idiom: a bright core with a softer glow, one hue per button (its own unit command
# buttons are red, green, orange and blue).  Ramps = (bright, mid, dim) palette indices.
NEON_RAMPS = {
    'cyan': (138, 139, 141),          # (0,255,255) (0,203,203) (0,119,119) - the team-colour ramp, remapped in game
    'green': (120, 121, 123),         # (103,255,0) (91,203,0) (51,119,0)
    'red': (96, 97, 99),              # (255,31,31) (203,23,23) (119,11,11)
    'orange': (78, 79, 129),          # (255,99,0) (255,75,0) (119,51,0)
    'yellow': (74, 75, 130),          # (255,203,0) (255,175,0) (79,31,0)
    'blue': (102, 103, 105),          # (87,147,255) (43,91,203) (11,43,119)
    'white': (1, 31, 55),             # (255,255,255) (123,123,123) (65,65,65)
}
NEON_LO = (110, 45, 12)


def _neon(kind, w=53, h=35, ramp='cyan'):
    """A drawn icon in BUTTON.SPR's neon idiom (1.5-px core, a dim glow around it) in one of its hues.
    Icons are designed on the 53x35 plate interior; a smaller area gets the design scaled down."""
    b, m, d = NEON_RAMPS[ramp]
    if (w, h) == (53, 35):
        return _icon(kind, w, h).raster(bright=b, mid=m, dim=d, lo=NEON_LO)
    s = min(w / 53.0, h / 35.0)
    tw, th = max(1, int(round(53 * s))), max(1, int(round(35 * s)))
    pts = _icon(kind, 53, 35).raster(bright=b, mid=m, dim=d, lo=NEON_LO, size=(tw, th))
    ox, oy = (w - tw) // 2, (h - th) // 2
    return {(x + ox, y + oy): v for (x, y), v in pts.items()}


def _triangle(direction, size=16, center=None, ramp=RED):
    """A clean symmetric outline triangle for the bar / dialog arrows (the stock 16x16 cells are
    drawn at the top-left of a 20x19 button rect, so the bar's are centred on (9.5, 9))."""
    g = Glyph(size, size)
    cx, cy = center or (size / 2.0, size / 2.0)
    if direction == 'up':
        pts = [(cx, cy - 4.5), (cx + 5.5, cy + 4), (cx - 5.5, cy + 4)]
    elif direction == 'down':
        pts = [(cx, cy + 4.5), (cx + 5.5, cy - 4), (cx - 5.5, cy - 4)]
    elif direction == 'left':
        pts = [(cx - 4.5, cy), (cx + 4, cy + 5.5), (cx + 4, cy - 5.5)]
    else:
        pts = [(cx + 4.5, cy), (cx - 4, cy + 5.5), (cx - 4, cy - 5.5)]
    g.poly(pts, 1.6)
    return g.raster(bright=ramp[1], mid=ramp[3], dim=ramp[4])


# MAINBUT cell -> glyph kind (from MAINE's textmsg lines: 62 Quit = cell 1, 63 Save Game = 4,
# 64 Options = 0, 151 Allies Menu = 117, 196 Pause Button = 131, 202 Objectives = 118, 150 Stop = 62,
# 33 Move Only = 63, 35 Move & Attack = 65, 36 Set waypoints = 66, 37 Deploy = 74, 139 Deploy Turret
# = 68, 140 Deploy Mine = 69, 142 Steal Money = 75, 143/146 Second / Ground Attack = 2, 144 Napalm = 72,
# 145 Disease = 73, 141 Inspire Troops = 121, 197 Drop Ship = 125, 198 Saucer = 126; 67 is unreferenced)
ICON_CELLS = {0: 'options', 1: 'quit', 2: 'attack', 4: 'save', 62: 'stop', 63: 'move', 65: 'move_attack',
              66: 'waypoints', 67: 'shovel', 68: 'turret', 69: 'mine', 72: 'napalm', 73: 'disease',
              74: 'deploy', 75: 'steal', 117: 'allies', 118: 'objectives', 121: 'inspire',
              125: 'dropship', 126: 'saucer', 131: 'pause'}
# BUTTON.SPR cells whose neon icon is taken as it is (same meaning as the MAINBUT cell, checked
# against MAINE's textmsg): '?', 'X', stop, move, move & attack, waypoints, deploy turret, deploy
# mine, napalm, plague, deploy, steal money.  The maintainer kept these ("previous style ... was
# ok, return them back") and asked for the same style on the Game Option tab, whose remaining cells
# (save, allies, pause, objectives) BUTTON.SPR has no icon for: those are drawn in its idiom (_neon).
FROM_BUTTON = (62, 63, 65, 66, 68, 69, 72, 73, 74, 75)
PLATE_IDX = {40, 49, 55, 60, 65, 67, 0, BLK}          # BUTTON.SPR's grey plate: everything else is icon


# ---------------------------------------------------------------- the frame
def render_frame(width, height, game):
    dx, dy = width - SRC_W, height - SRC_H
    sy = hud_layout.slack_rows(height)
    px_ = PANEL_X + dx
    view_x1 = INSET_X + VIEW_W + dx - 1
    view_y1 = INSET_Y + VIEW_H + dy - sy - 1
    bar_top = view_y1 + 1
    cv = Canvas(width, height)
    font = Font(game)
    button = spr.read_spr(find_file(game, 'INTRFACE', 'button.spr'))['cells']

    def P(x):
        return x + dx

    def B(y):
        return y + dy

    # --- every screen (with its 3 rings), plate (with a 1-px seam), dial and border tube is a hole the
    #     pipework is laid around, so no compartment is cut (maintainer, 30 Sep 2026: "frames are not closed")
    def sc(x0, y0, x1, y1, m=3):
        return (x0 - m, y0 - m, x1 + m, y1 + m)
    lx0, ly0, lx1, ly1 = LETTER_STRIP
    cx, cy, r = DIAL
    holes = [sc(P(MINIMAP[0]), MINIMAP[1], P(MINIMAP[2]), MINIMAP[3]),
             sc(P(lx0), ly0 - 1, P(lx1), ly1 + 1),
             sc(P(GRID[0]), GRID[1], P(GRID[2]), GRID[3]),
             sc(P(STATUS[0]), B(STATUS[1]), P(STATUS[2]), B(STATUS[3])),
             sc(P(BUILD_PLATE[0]), B(BUILD_PLATE[1]), P(BUILD_PLATE[2]), B(BUILD_PLATE[3]), 1),
             sc(P(DAYS_PANEL[0]), B(DAYS_PANEL[1]), P(DAYS_PANEL[2]), B(DAYS_PANEL[3])),
             sc(P(MONEY[0]), B(MONEY[1]), P(MONEY[2]), B(MONEY[3])),
             (P(cx) - r - 5, B(cy) - r - 5, P(cx) + r + 5, B(cy) + r + 5),
             sc(MSG[0], B(MSG[1]), MSG[2] + dx, B(MSG[3])),
             (0, 0, 3, height - 1), (0, 0, view_x1 + 3, 5), (view_x1 + 1, 0, view_x1 + 3, height - 1),
             (width - 4, 0, width - 1, height - 1), (0, height - 4, width - 1, height - 1),
             (px_, 0, width - 1, 5), (0, bar_top, view_x1 + 2, bar_top + 2)]
    qx0, qy0, qx1, qy1 = CHAT_CHANNEL
    holes.append(sc(qx0, B(qy0), qx1, B(qy1), 1))
    # --- pipework over everything that is not the view
    pw = Pipework(cv, seed=width * 10007 + height)
    pw.region(px_ + 3, 0, width - 1, height - 1, holes)                  # the panel column
    pw.region(0, bar_top, view_x1 + 2, height - 1, holes)                 # the bottom bar
    # --- the view's right and bottom walls: BAND | LT | dark from the map outward, drawn BEFORE the panel
    #     content - the stock layout puts BUILD at x 516 and the screens at 518 with the tube at 512..514, so a
    #     plate on the wall keeps its ring and a screen's ring (BAND, LT from its black) coincides with the wall
    #     instead of jogging (maintainer, 30 Sep 2026: "'BUILD' button lost left frame")
    for i, c in enumerate((BAND, LT, D11)):
        cv.vline(view_x1 + 1 + i, 0, height - 1, c)
        cv.hline(0, view_x1 + 2, bar_top + i, c)
    # --- panel screens (black where the engine or the widgets paint)
    x0, y0, x1, y1 = MINIMAP
    cv.screen(P(x0), y0, P(x1), y1)
    lx0, ly0, lx1, ly1 = LETTER_STRIP
    cv.screen(P(lx0), ly0 - 1, P(lx1), ly1 + 1)
    text = 'DARK COLONY'
    th = font.width(text)
    font.draw_vertical(cv, P(lx0) + (lx1 - lx0 + 1 - 10) // 2, ly0 + max(0, (ly1 - ly0 + 1 - th) // 2), text,
                       remap={138: 122, 139: 122, 140: 123, 141: 123, 142: 124, 143: 125})
    gx0, gy0, gx1, gy1 = GRID
    cv.screen(P(gx0), gy0, P(gx1), gy1)                                   # tab row + button grid
    sx0, sy0_, sx1, sy1_ = STATUS
    cv.screen(P(sx0), B(sy0_), P(sx1), B(sy1_))
    bx0, by0, bx1, by1 = BUILD_PLATE
    lobby_plate(bx1 - bx0 + 1, by1 - by0 + 1, cv, P(bx0), B(by0))
    _build_caption(cv, game, P(bx0), B(by0), bx1 - bx0 + 1, by1 - by0 + 1)
    dx0, dy0, dx1, dy1 = DAYS_PANEL                                       # one screen for the DAYS label and its value
    cv.screen(P(dx0), B(dy0), P(dx1), B(dy1))
    th = caption_5x7_width('DAYS')
    caption_5x7(cv, P(dx0) + (dx1 - dx0 + 1 - th) // 2, B(DAYS_TEXT[1]), 'DAYS', GRN[1])
    mx0, my0, mx1, my1 = MONEY
    cv.screen(P(mx0), B(my0), P(mx1), B(my1))
    cx, cy, r = DIAL
    _dial(cv, P(cx), B(cy), r)
    # the dial's face from the first frame: clock_draw blits a cell only when its index changes (the
    # first time up to a phase step after the start - maintainer: "day/night clock is not appearing
    # instantly on game start"), so the frame carries the day's first cell (hand at 12) under the
    # bezel, at the cell's measured place (608..635, 450..477 stock); the code's cells overwrite it.
    flags, clock_cells, _ = build_clock(game)
    cv.blit(clock_cells[0], P(608), B(450), transparent=(0,))

    # --- bottom bar: arrow plates and the message screen
    qx0, qy0, qx1, qy1 = CHAT_CHANNEL                                     # the arrows' capsule: the pushb cells draw the triangles into it
    capsule(cv, qx0, B(qy0), qx1, B(qy1))
    qx0, qy0, qx1, qy1 = MSG
    cv.screen(qx0, B(qy0), qx1 + dx, B(qy1))

    # borders around the view: a tube [dark, band, band, light] from the screen edge to the hole
    for i, c in enumerate((D11, BAND, BAND, LT)):
        cv.vline(i, 0, height - 1, c)
    for i, c in enumerate((D11, BAND, BAND, BAND, D23, LT)):
        cv.hline(0, view_x1 + 3, i, c)
    for i, c in enumerate((D11, BAND, BAND, LT)):                         # right screen edge
        cv.vline(width - 1 - i, 0, height - 1, c)
    for i, c in enumerate((D11, BAND, BAND, LT)):                         # bottom screen edge
        cv.hline(0, width - 1, height - 1 - i, c)
    for i, c in enumerate((D11, BAND, BAND, BAND, D23, LT)):              # panel top
        cv.hline(px_, width - 1, i, c)

    # --- the hole itself: exactly 254
    cv.fill(INSET_X, INSET_Y, view_x1, view_y1, BLK)
    return cv, dict(view=(INSET_X, INSET_Y, view_x1, view_y1), bar_top=bar_top, slack=sy)


# The lobby's button captions are MFONTO5 glyphs drawn through `remap 0`, which turns the font's cyan
# ramp red; sampled on the race-selection screen (28 Sep 2026) and mapped to the nearest palette
# entries: core 139 -> 86 (143,0,0), highlight 140 -> 221 (159,55,0), 141 -> 99, 142 -> 100 (79,7,7),
# shadow 143 -> 244 (47,15,0).
LOBBY_CAPTION = {139: 86, 140: 221, 141: 99, 142: 100, 143: 244, 138: 86}     # the lobby's exact remap (kept for reference)
CAPTION_RED = {138: 96, 139: 96, 140: 96, 141: 98, 142: 99, 143: 100}          # "BUILD button font must be just red": (255,31,31) core, (79,7,7) shadow


CAPTION_5X7 = {                       # 5x7 pixel capitals for the frame's own small captions (DAYS): MFONTO7's 10-row letters do not fit
    'D': ('11110', '10001', '10001', '10001', '10001', '10001', '11110'),
    'A': ('01110', '10001', '10001', '11111', '10001', '10001', '10001'),
    'Y': ('10001', '10001', '01010', '00100', '00100', '00100', '00100'),
    'S': ('01111', '10000', '10000', '01110', '00001', '00001', '11110'),
}


def caption_5x7(cv, x, y, text, colour, spacing=1):
    """Draw a 5x7 pixel caption; returns its width.  Letters not in CAPTION_5X7 leave a blank."""
    cx = x
    for ch in text:
        rows = CAPTION_5X7.get(ch)
        if rows:
            for r, bits in enumerate(rows):
                for c, b in enumerate(bits):
                    if b == '1':
                        cv.put(cx + c, y + r, colour)
        cx += 5 + spacing
    return cx - x - spacing


def caption_5x7_width(text, spacing=1):
    return len(text) * 5 + (len(text) - 1) * spacing


def _build_caption(cv, game, x, y, w, h, text='BUILD'):
    """BUILD as the lobby writes its buttons: MFONTO5, centred, in the caption red (maintainer:
    "BUILD button font must be the same as in lobby")."""
    font = Font(game, 'mfonto5.spr')
    tw = font.width(text, spacing=1)
    th = max((g['h'] for g in (font.glyph(ch) for ch in text) if g), default=12)
    font.draw(cv, x + (w - tw) // 2, y + (h - th) // 2, text, remap=CAPTION_RED, spacing=1)


def _paste_build_label(cv, cell, x, y, w, h):
    """BUTTON.SPR cell 76's neon BUILD lettering (rows 3..14 of the 88x37 cell) centred in the button."""
    cw, ch, px = cell['w'], cell['h'], cell['px']
    xs, ys = [], []
    for yy in range(3, 16):
        for xx in range(3, cw - 3):
            if px[yy * cw + xx] in GRN:
                xs.append(xx)
                ys.append(yy)
    if not xs:
        return
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
    ox = x + (w - (bx1 - bx0 + 1)) // 2 - bx0
    oy = y + (h - (by1 - by0 + 1)) // 2 - by0
    to_red = {120: RED[0], 121: RED[1], 122: RED[2], 123: RED[3], 124: RED[4], 125: RED[5], WHITE: RED[0]}
    for yy in range(by0, by1 + 1):
        for xx in range(bx0, bx1 + 1):
            v = px[yy * cw + xx]
            if v in GRN or v == WHITE:
                cv.put(ox + xx, oy + yy, to_red[v])                # red lettering (maintainer)


def _dial(cv, cx, cy, r):
    """The dial's bezel only: the face itself (day and night halves, the hand) is the code-drawn
    28x28 cell of SPRITES/CLOCK.SPR (`clock` below), which lands centred in this ring."""
    for yy in range(-r - 3, r + 4):
        for xx in range(-r - 3, r + 4):
            d2 = xx * xx + yy * yy
            if d2 <= (r + 3) * (r + 3):
                if d2 > (r + 2) * (r + 2):
                    c = D11
                elif d2 > (r + 1) * (r + 1):
                    c = BAND
                elif d2 > r * r:
                    c = LT
                else:
                    c = BLK
                cv.put(cx + xx, cy + yy, c)


# ---------------------------------------------------------------- the day/night dial cells
CLOCK_DAY, CLOCK_NIGHT, CLOCK_SUN, CLOCK_MOON = 55, 67, 74, 36    # (65,65,65) (11,11,11) (255,203,0) (115,115,115)


def build_clock(game):
    """SPRITES/CLOCK.SPR: the 36 hand cells of the stock SPRITES/CLOC.SPR (28x28, same flags and
    offsets) redrawn - a disc whose right half is the day (light grey, a sun at 3 o'clock) and left
    half the night (dark, a moon at 9 o'clock), a light rim, and the red hand sweeping clockwise
    from 12 through 6 (cells 0..17, the day) back to 12 (18..35, the night), as the stock cells do."""
    pal = read_palette(game)
    stock = spr.read_spr(find_file(game, 'SPRITES', 'cloc.spr'))
    cells = []
    for i, c in enumerate(stock['cells']):
        w, h = c['w'], c['h']
        cv = Canvas(w, h, 0)                                  # index 0 = transparent
        cx, cy, r = (w - 1) / 2.0, (h - 1) / 2.0, min(w, h) / 2.0 - 0.5
        for y in range(h):
            for x in range(w):
                d = math.hypot(x - cx, y - cy)
                if d > r:
                    continue
                if d > r - 1.2:
                    cv.put(x, y, LT)
                elif d > r - 2.2:
                    cv.put(x, y, BAND)
                else:
                    cv.put(x, y, CLOCK_DAY if x > cx else CLOCK_NIGHT)
        cv.vline(int(cx), int(cy - r + 3), int(cy + r - 3), G43)          # the day/night meridian
        cv.vline(int(cx) + 1, int(cy - r + 3), int(cy + r - 3), G43)
        # sun at 3 o'clock, moon at 9 o'clock
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1), (-1, 0), (2, 0), (0, -1), (1, -1), (0, 2), (1, 2)):
            cv.put(int(cx) + 8 + dx, int(cy) + dy, CLOCK_SUN)
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            cv.put(int(cx) - 9 + dx, int(cy) + dy, CLOCK_MOON)
        # the hand: 18 cells per half, clockwise from 12 o'clock
        a = math.radians(180.0 * i / 18.0)
        g = Glyph(w, h)
        tip = (cx + 0.5 + 11 * math.sin(a), cy + 0.5 - 11 * math.cos(a))
        g.line([(cx + 0.5, cy + 0.5), tip], 2.2)
        g.circle((cx + 0.5, cy + 0.5), 1.6, fill=True)
        for (x, y), v in g.raster(bright=RED[0], mid=RED[1], dim=RED[3]).items():
            if math.hypot(x - cx, y - cy) <= r - 2:
                cv.put(x, y, v)
        cells.append(dict(w=w, h=h, ox=c['ox'], oy=c['oy'], px=cv.px))
    return stock['flags'], cells, [tuple(pal[i * 3:i * 3 + 3]) for i in range(256)]


def cmd_clock(args):
    flags, cells, pal = build_clock(args.game)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    spr.write_spr(args.out, flags, cells, pal)
    print('wrote %s: %d dial cells (day = right half, night = left half, hand clockwise from 12)' % (args.out, len(cells)))
    return 0


def cmd_frame(args):
    cv, info = render_frame(args.width, args.height, args.game)
    im = cv.image(read_palette(args.game))
    im.save(args.out, format='GIF', version='GIF87a', interlace=False, optimize=False)
    hole = info['view']
    opaque = sum(1 for v in cv.px if v != BLK)
    print('wrote %s  %dx%d, %d opaque px (%.1f%%), hole (%d,%d)-(%d,%d), bar from row %d%s'
          % (args.out, args.width, args.height, opaque, 100.0 * opaque / (args.width * args.height),
             hole[0], hole[1], hole[2], hole[3], info['bar_top'],
             ', %d spare rows' % info['slack'] if info['slack'] else ''))
    return 0


# ---------------------------------------------------------------- the cell bank
PLATE_W, PLATE_H = 59, 41
ARROW_CELLS = {40: ('up', False), 50: ('up', True), 57: ('down', False), 76: ('down', True)}
TAB_CELLS = (77, 78, 79)
PAUSED_CELL = 132
STRIP_CELL = 92
SMALL_PLATES = (119, 120, 123, 124, 127)
KNOBE_ARROWS = {'up': 12, 'up_pressed': 13, 'down': 10, 'down_pressed': 11, 'left': 14, 'left_pressed': 15,
                'right': 16, 'right_pressed': 17}


def _is_grey(rgb, lo=20, hi=200):
    r, g, b = rgb
    return abs(r - g) <= 12 and abs(g - b) <= 12 and abs(r - b) <= 12 and lo <= r <= hi


def grey_frame(w, h):
    """The portrait cells' plate: the lobby ring's geometry in the pipework greys (35-grey outside
    and inside, the 107-grey light line between, 11-grey corners) on black - maintainer: "build
    units and upgrades icons must have gray frame instead of red frame"."""
    cv = Canvas(w, h)
    x1, y1 = w - 1, h - 1
    cv.ring(0, 0, x1, y1, 0, BAND)
    for x, y in ((0, 0), (x1, 0), (0, y1), (x1, y1)):
        cv.put(x, y, D11)
    cv.ring(1, 1, x1 - 1, y1 - 1, 0, LT)
    cv.ring(2, 2, x1 - 2, y1 - 2, 0, BAND)
    for cx, cy, sx, sy in ((2, 2, 1, 1), (x1 - 2, 2, -1, 1), (2, y1 - 2, 1, -1), (x1 - 2, y1 - 2, -1, -1)):
        cv.put(cx, cy, G43)
        cv.put(cx + sx, cy, G65)
        cv.put(cx, cy + sy, G65)
    return cv


def re_plate_portrait(cell, pal):
    """Grey frame + the MAINBUT portrait pixel for pixel (its black background and the coloured
    glow outlines of the upgrade cells included; only the metal bevel's 3-px rim goes)."""
    w, h, px = cell['w'], cell['h'], cell['px']
    out = grey_frame(w, h).px
    for y in range(3, h - 3):
        for x in range(3, w - 3):
            v = px[y * w + x]
            if v in (0, BLK):
                continue
            rgb = tuple(pal[v * 3:v * 3 + 3])
            if (x < 5 or x > w - 6 or y < 5 or y > h - 6) and _is_grey(rgb, 60, 200):
                continue                              # residual bevel grey at the rim
            out[y * w + x] = v
    return out


def icon_cell(kind, w=PLATE_W, h=PLATE_H):
    cv = lobby_plate(w, h)
    for (x, y), v in _neon(kind, w - 6, h - 6).items():
        cv.put(3 + x, 3 + y, v)
    return cv.px


def button_icon(cell, w=PLATE_W, h=PLATE_H):
    """A BUTTON.SPR cell's neon icon and hot-key letter (every pixel that is not its grey plate) on
    the lobby plate."""
    out = grey_frame(w, h).px
    px = cell['px']
    for y in range(3, h - 3):
        for x in range(3, w - 3):
            v = px[y * w + x]
            if v not in PLATE_IDX:
                out[y * w + x] = v
    return out


def glyph_pixels(cell, box=None):
    w, h, px = cell['w'], cell['h'], cell['px']
    x0, y0, x1, y1 = box or (0, 0, w - 1, h - 1)
    pts = {(x, y): px[y * w + x] for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)
           if px[y * w + x] not in (0, BLK)}
    if not pts:
        return {}, 0, 0
    mx, my = min(p[0] for p in pts), min(p[1] for p in pts)
    gw = max(p[0] for p in pts) - mx + 1
    gh = max(p[1] for p in pts) - my + 1
    return {(x - mx, y - my): v for (x, y), v in pts.items()}, gw, gh


def arrow_cell(direction, pressed, size=16, center=None, opaque=False):
    """A symmetric outline triangle (red; green when pressed) in a size x size cell.  The bar's
    16x16 cells are drawn at the top-left of the 20x19 button rects (4, 460) / (24, 460) and are
    transparent around the triangle, so it sits in the frame's chat channel (BAR_ARROW_CENTRE)."""
    cv = Canvas(size, size, BLK if opaque else 0)
    for (x, y), v in _triangle(direction, size, center, GRN if pressed else RED).items():
        cv.put(x, y, v)
    return dict(w=size, h=size, ox=0, oy=0, px=cv.px)


# the diplomacy rows' 24x17 plates: stock 119 = crossed circle (no pact), 120 = circle (pact), 123 =
# "1000 =>" (pay), 124 = empty plate, 127 = small X - drawn here as clear glyphs in the state's hue
SMALL_GLYPHS = {119: ('nopact', 'red'), 120: ('pact', 'green'), 123: ('pay', 'yellow'), 127: ('cross', 'red'), 124: (None, None)}


def _small_glyph(kind, w, h):
    g = Glyph(w, h)
    cx, cy = w / 2.0, h / 2.0
    if kind == 'pact':
        g.circle((cx, cy), 5, 1.8)
    elif kind == 'nopact':
        g.circle((cx, cy), 5, 1.8)
        g.line([(cx - 3.5, cy - 3.5), (cx + 3.5, cy + 3.5)], 1.6)
        g.line([(cx + 3.5, cy - 3.5), (cx - 3.5, cy + 3.5)], 1.6)
    elif kind == 'pay':
        g.text('$', 11, (cx - 4, cy))
        g.line([(cx + 1, cy), (cx + 7, cy)], 1.8)
        g.poly([(cx + 6, cy - 3), (cx + 9.5, cy), (cx + 6, cy + 3)], fill=True)
    elif kind == 'cross':
        g.line([(cx - 4, cy - 4), (cx + 4, cy + 4)], 2)
        g.line([(cx + 4, cy - 4), (cx - 4, cy + 4)], 2)
    return g


def _lum(pal, v):
    r, g, b = pal[v * 3:v * 3 + 3]
    return (r * 299 + g * 587 + b * 114) // 1000


def _inverted_glyph(cell, pal, box):
    """The stock cell's dark glyph inside `box` (dark on the metal plate), inverted to light greys
    on nothing: luminance <= 35 -> white (1), <= 60 -> 205-grey (6), <= 80 -> 180-grey (11).
    Returns {(x, y): index} relative to the glyph's bounding box, and its size."""
    w, px = cell['w'], cell['px']
    x0, y0, x1, y1 = box
    pts = {}
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            v = px[y * w + x]
            if v in (0, BLK):
                continue
            lm = _lum(pal, v)
            if lm <= 35:
                pts[(x, y)] = WHITE
            elif lm <= 60:
                pts[(x, y)] = 6
            elif lm <= 80:
                pts[(x, y)] = 11
    if not pts:
        return {}, 0, 0
    mx, my = min(p[0] for p in pts), min(p[1] for p in pts)
    return ({(x - mx, y - my): v for (x, y), v in pts.items()},
            max(p[0] for p in pts) - mx + 1, max(p[1] for p in pts) - my + 1)


def _coloured_pixels(cell, pal, box):
    """The stock cell's coloured (non-grey) pixels inside `box`, e.g. the green pact marks."""
    w, px = cell['w'], cell['px']
    x0, y0, x1, y1 = box
    out = {}
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            v = px[y * w + x]
            if v in (0, BLK):
                continue
            r, g, b = pal[v * 3:v * 3 + 3]
            if not _is_grey((r, g, b), 0, 255):
                out[(x, y)] = v
    return out


def small_plate(cell, index, pal):
    """24x17 diplomacy-row plates on the grey frame with the ORIGINAL marks (maintainer: "settings
    for each user take original images for peace, vision and talk. invert colors for original
    givemoney and use it"): 119 / 120 / 127 keep their green marks pixel for pixel; 123's dark
    "1000 =>" is inverted to light grey; 124 stays an empty plate."""
    w, h = cell['w'], cell['h']
    if index != 123:
        # exactly the original cells (maintainer: "other options use exactly original")
        return dict(w=w, h=h, ox=cell['ox'], oy=cell['oy'], px=bytearray(cell['px']))
    cv = grey_frame(w, h)
    pts, gw, gh = _inverted_glyph(cell, pal, (3, 2, w - 4, h - 4))
    ox, oy = (w - gw) // 2, (h - gh) // 2
    for (x, y), v in pts.items():
        cv.put(ox + x, oy + y, 15)                       # one light grey (172,172,172) - maintainer
    return dict(w=w, h=h, ox=cell['ox'], oy=cell['oy'], px=cv.px)


STRIP_COLUMNS = ((None, None), ('peace', 'yellow'), ('eye', 'cyan'), ('chat', 'green'), ('coins', 'red'))   # give money = the dollar


def _column_icon(kind, w, h):
    """The diplomacy header's column icons, drawn at their own size with thin (1.3-px) strokes so
    they fill the column (maintainer: "too fat lines and signs are too small")."""
    g = Glyph(w, h)
    cx, cy = w / 2.0, h / 2.0
    r = min(w, h) / 2.0 - 1.5
    if kind == 'peace':
        g.circle((cx, cy), r, 1.3)
        g.line([(cx, cy - r), (cx, cy + r)], 1.3)
        g.line([(cx, cy), (cx - r * 0.71, cy + r * 0.71)], 1.3)
        g.line([(cx, cy), (cx + r * 0.71, cy + r * 0.71)], 1.3)
    elif kind == 'eye':
        rx, ry = w / 2.0 - 1, h / 2.0 - 4.5
        g.ellipse(cx - rx, cy - ry, cx + rx, cy + ry, 1.3)
        g.circle((cx, cy), ry * 0.55, 1.3)
        g.circle((cx, cy), 1.3, fill=True)
    elif kind == 'coins':
        g.text('$', h + 2, (cx, cy))                        # give money
    elif kind == 'chat':
        # a speaker dot with sound waves (maintainer: the column is the chat action)
        sx = cx - r * 0.75
        g.circle((sx, cy), 2.6, fill=True)
        for k in (0.5, 0.95, 1.4):
            rr = r * k
            g.arc(sx - rr, cy - rr, sx + rr, cy + rr, 318, 42, 1.4)
    elif kind == 'give':
        g.line([(cx - r, cy), (cx + r * 0.3, cy)], 1.6)
        g.poly([(cx + r * 0.1, cy - r * 0.6), (cx + r, cy), (cx + r * 0.1, cy + r * 0.6)], 1.3)
    return g


STOCK_STRIP_BOXES = ((0, 21), (22, 45), (46, 69), (70, 93), (94, 117))   # the stock header's five boxes (x ranges), icons in rows 16..40


def strip_cell(cell, font, pal):
    """The 118x43 diplomacy header (MAINE picture 152, drawn above the player rows): a grey frame,
    the word ALLIES across the top band, and the ORIGINAL header pictures - peace sign, eyes,
    the talk figure, the vertical GIVE - inverted from dark-on-metal to light-on-dark, each centred
    in its column (maintainer: "take original headers, invert colors and apply")."""
    w, h = cell['w'], cell['h']
    cv = grey_frame(w, h)
    label = 'ALLIES'
    # a clean terminal look: the lobby caption font's glyph shapes in one flat colour, the shadow
    # and edge indices dropped (maintainer: "terminal clear font which looks great without alias")
    tw = font.width(label)
    font.draw(cv, (w - tw) // 2, 3, label, remap={138: 75, 139: 75, 140: 75, 141: 0, 142: 0, 143: 0})
    cv.hline(3, w - 4, 15, G43)
    col_w = (w - 6) / 5.0
    # the header pictures: the drawn thin-stroke set (peace, eye, speaker with waves, dollar) - the
    # maintainer preferred it over the inverted originals ("for headers your own last drawn
    # pictures are most acceptable")
    for k, (kind, ramp) in enumerate(STRIP_COLUMNS):
        x0 = 3 + int(round(k * col_w))
        x1 = 3 + int(round((k + 1) * col_w)) - 1
        if k:
            cv.vline(x0, 16, h - 4, G43)
        if kind:
            b, m, d = NEON_RAMPS[ramp]
            iw, ih = x1 - x0 - 2, h - 4 - 17
            for (x, y), v in _column_icon(kind, iw, ih).raster(bright=b, mid=m, dim=d).items():
                cv.put(x0 + 1 + x, 17 + y, v)
    return dict(w=w, h=h, ox=cell['ox'], oy=cell['oy'], px=cv.px)


def grey_plate(w, h, canvas=None, x0=0, y0=0):
    """An inactive button: the pipework's 35-grey plate with the light edge line."""
    cv = canvas or Canvas(w, h)
    x1, y1 = x0 + w - 1, y0 + h - 1
    cv.fill(x0, y0, x1, y1, BAND)
    cv.ring(x0, y0, x1, y1, 0, D11)
    cv.ring(x0 + 1, y0 + 1, x1 - 1, y1 - 1, 0, LT)
    cv.ring(x0 + 2, y0 + 2, x1 - 2, y1 - 2, 0, G43)
    return cv


TAB_STRIP_W = 120


def tab_strip(font, active):
    """120x16: the three tab buttons (pushb 0/1/2 at x 518 / 557 / 598, relative to the strip's x 516)
    with the digits 1 2 3: the active one the lobby's red plate with a red digit, the inactive ones
    grey plates with a light-grey digit (maintainer: "inactive tab buttons must be gray. active button
    must be red").  The plates sit 2 px inside the grid screen's black on every side (x 4..117 of the
    strip = 520..633 stock, rows 96..109), so neither the screen's rings nor the panel's border tube
    are touched (the 124-px form ran to 637, over the border; the first 120-px form's plate 1 sat
    against the screen's ring - maintainer, 30 Sep 2026: "tab buttons are breaking interface frame")."""
    cv = Canvas(TAB_STRIP_W, 16, 0)                    # transparent outside the plates
    cv.vline(0, 0, 15, BAND)                           # ... except the view's wall at x 900..901 (strip columns 0..1): the engine
    cv.vline(1, 0, 15, LT)                             # erases the strip's rect before a repaint (a tab click), so the strip carries it
    spans = ((4, 39), (43, 78), (82, 117))            # 36-px plates, 2 px of black to the screen's rings (x 902 / 1019) and between them
    for k, (x0, x1) in enumerate(spans):
        w = x1 - x0 + 1
        if k == active:
            lobby_plate(w, 14, cv, x0, 0)
            remap = {138: RED[0], 139: RED[1], 140: RED[1], 141: RED[2], 142: RED[3], 143: RED[4]}
        else:
            grey_plate(w, 14, cv, x0, 0)                  # 14 tall: 2 px of black above the button grid (row 112)
            remap = {138: G115, 139: LT, 140: LT, 141: G82, 142: G65, 143: G43}
        g = font.glyph(str(k + 1))
        if g and g['w']:
            cv.blit(g, x0 + (w - g['w']) // 2, 2, remap=remap)
    return dict(w=TAB_STRIP_W, h=16, ox=0, oy=0, px=cv.px)


# ---------------------------------------------------------------- the action-button layout
# BUTTON.SPR's unit action buttons (stop, move, ... - cells 62..75) share one layout: a rounded
# hot-key box in the top-left corner with the key in light grey, a "circuit" line descending from
# it, and the neon icon in the space to the right.  The maintainer asked for the Game Option tab
# in exactly that style ("third tab buttons must be the same style as action buttons for units"),
# so the template = the pixels those ten cells have in common, and the key letters are the ones
# the shipped MAINBUT badges show (O Q D F11 J ESC and the return arrow).
ACTION_TEMPLATE_CELLS = (62, 63, 65, 66, 68, 69, 72, 73, 74, 75)
KEY_BOX = (4, 3, 13, 12)                     # where the key glyph goes (cell coordinates)
ICON_AREA = (16, 3, 55, 37)                  # where the icon goes
KEY_GREY = dict(bright=6, mid=11, dim=17)    # (205,205,205) (180,180,180) (164,164,156): BUTTON's key letters
# (icon, hot-key, hue) - one hue per button like BUTTON's unit commands (maintainer: "images on the
# buttons must be in different colors similar to unit command buttons")
ACTION_CELLS = {0: ('options', 'O', 'cyan'), 1: ('quit', 'Q', 'orange'), 2: ('attack', 'D', 'red'),
                4: ('save', 'F11', 'green'), 67: ('shovel', '', 'white'), 117: ('allies', '', 'yellow'),
                118: ('objectives', 'J', 'blue'), 121: ('inspire', 'RET', 'yellow'),
                125: ('dropship', 'D', 'blue'), 126: ('saucer', 'D', 'green'), 131: ('pause', 'ESC', 'red')}


def action_template(button):
    common = None
    for i in ACTION_TEMPLATE_CELLS:
        s = button[i]['px']
        common = list(s) if common is None else [a if a == b else -1 for a, b in zip(common, s)]
    return common


def key_glyph(key):
    """The hot-key mark for the corner box: one or three characters, or the return arrow."""
    x0, y0, x1, y1 = KEY_BOX
    w, h = x1 - x0 + 1, y1 - y0 + 1
    g = Glyph(w, h)
    if key == 'RET':
        g.line([(w - 2.5, 2), (w - 2.5, h / 2.0 + 1), (3, h / 2.0 + 1)], 1.6)
        g.poly([(1.2, h / 2.0 + 1), (4.2, h / 2.0 - 1.6), (4.2, h / 2.0 + 3.6)], fill=True)
    elif len(key) == 1:
        g.text(key, h + 1, (w / 2.0, h / 2.0))
    elif key:
        g.text(key, h - 2, (w / 2.0, h / 2.0))
    return g.raster(lo=(120, 60, 25), **KEY_GREY)


KEY_FONT = {138: 6, 139: 11, 140: 11, 141: 17, 142: 17, 143: 62}   # MFONTO7's cyan ramp -> BUTTON's key greys
WIDE_BOX = (3, 3, 28, 16)                    # the hot-key box for a three-character key (F11, ESC): inside the plate ring
WIDE_ICON_AREA = (18, 18, 55, 37)            # the icon then sits right of and below the box


def action_cell(template, kind, key, ramp='cyan', font=None, w=PLATE_W, h=PLATE_H):
    out = grey_frame(w, h)                    # grey like the portraits (maintainer, 28 Sep evening); red marks the active tab and BUILD
    for y in range(3, h - 3):
        for x in range(3, w - 3):
            v = template[y * w + x]
            if v not in (-1, 0, BLK):
                out.put(x, y, v)
    wide = len(key) == 3 and font is not None
    if wide:
        # a wider key box in the template's own box colours (dark red 101 between two 60 lines,
        # 67 outside), the key written with the game's MFONTO7 glyphs so every letter is crisp
        # (maintainer: "carefully write these action shortcuts (F11 and ESC)")
        bx0, by0, bx1, by1 = WIDE_BOX
        out.fill(bx0, by0, bx1 + 1, by1 + 1, BLK)
        out.ring(bx0, by0, bx1, by1, 3, WARM)
        out.ring(bx0 + 1, by0 + 1, bx1 - 1, by1 - 1, 2, 101)
        tw = font.width(key, spacing=1)
        font.draw(out, bx0 + (bx1 - bx0 + 1 - tw) // 2, by0 + 2, key, remap=KEY_FONT, spacing=1)
        ix0, iy0, ix1, iy1 = WIDE_ICON_AREA
    else:
        kx, ky = KEY_BOX[0], KEY_BOX[1]
        if key:
            for (x, y), v in key_glyph(key).items():
                out.put(kx + x, ky + y, v)
        ix0, iy0, ix1, iy1 = ICON_AREA
    for (x, y), v in _neon(kind, ix1 - ix0 + 1, iy1 - iy0 + 1, ramp).items():
        out.put(ix0 + x, iy0 + y, v)
    return out.px


def paused_cell(font):
    """123x137 PAUSED panel: a lobby plate, the pause glyph in red, the word in LED green."""
    w, h = 123, 137
    cv = lobby_plate(w, h)
    cv.ring(5, 5, w - 6, h - 6, 0, L_OUT)
    for (x, y), v in _icon('pause', 60, 60).raster().items():
        cv.put(31 + x, 22 + y, v)
    label = 'PAUSED'
    tw = 2 * font.width(label, spacing=1)
    x = (w - tw) // 2
    for ch in label:
        g = font.glyph(ch)
        if g and g['w']:
            for yy in range(g['h']):
                for xx in range(g['w']):
                    v = g['px'][yy * g['w'] + xx]
                    if v in (0, BLK):
                        continue
                    c = CYAN_TO_GREEN.get(v, v)
                    for sx in (0, 1):
                        for sy in (0, 1):
                            cv.put(x + 2 * xx + sx, 98 + 2 * yy + sy, c)
            x += 2 * (g['w'] + 1)
    return dict(w=w, h=h, ox=0, oy=0, px=cv.px)


def build_bank(game):
    pal = read_palette(game)
    mainbut = spr.read_spr(find_file(game, 'INTRFACE', 'mainbut.spr'))
    knobe = spr.read_spr(find_file(game, 'INTRFACE', 'knobe.spr'))['cells']
    font = Font(game)
    cells = []
    button = spr.read_spr(find_file(game, 'INTRFACE', 'button.spr'))['cells']
    template = action_template(button)
    for i, c in enumerate(mainbut['cells']):
        w, h = c['w'], c['h']
        if i in FROM_BUTTON:
            cells.append(dict(w=PLATE_W, h=PLATE_H, ox=c['ox'], oy=c['oy'], px=button_icon(button[i])))
        elif i in ACTION_CELLS and (w, h) == (PLATE_W, PLATE_H):
            kind, key, ramp = ACTION_CELLS[i]
            cells.append(dict(w=w, h=h, ox=c['ox'], oy=c['oy'], px=action_cell(template, kind, key, ramp, font)))
        elif i in ICON_CELLS and (w, h) == (PLATE_W, PLATE_H):
            cells.append(dict(w=w, h=h, ox=c['ox'], oy=c['oy'], px=icon_cell(ICON_CELLS[i])))
        elif i in TAB_CELLS:
            cells.append(tab_strip(font, i - TAB_CELLS[0]))
        elif i in ARROW_CELLS:
            d, pressed = ARROW_CELLS[i]
            cells.append(arrow_cell(d, pressed, center=BAR_ARROW_CENTRE[d]))
        elif i == PAUSED_CELL:
            cells.append(paused_cell(font))
        elif i == STRIP_CELL:
            cells.append(strip_cell(c, Font(game, 'mfonto5.spr'), pal))
        elif i in SMALL_PLATES:
            cells.append(small_plate(c, i, pal))
        elif (w, h) == (PLATE_W, PLATE_H):
            cells.append(dict(w=w, h=h, ox=c['ox'], oy=c['oy'], px=re_plate_portrait(c, pal)))
        else:
            cells.append(dict(w=w, h=h, ox=c['ox'], oy=c['oy'], px=bytearray(c['px'])))   # digits, 99, 128, empty 122
    return mainbut['flags'], cells, [tuple(pal[i * 3:i * 3 + 3]) for i in range(256)]


def cmd_bank(args):
    flags, cells, pal = build_bank(args.game)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    spr.write_spr(args.out, flags, cells, pal)
    portraits = sum(1 for i, c in enumerate(cells) if (c['w'], c['h']) == (PLATE_W, PLATE_H) and i not in ICON_CELLS)
    print('wrote %s: %d cells (%d BUTTON.SPR neon icons, %d drawn neon icons, %d portraits on lobby plates, tabs, arrows, PAUSED)'
          % (args.out, len(cells), len(FROM_BUTTON), len(set(ICON_CELLS) - set(FROM_BUTTON)), portraits))
    return 0


# ---------------------------------------------------------------- the dialog plates (doc 10.53, DC16_INTERFACE_STYLE_GUIDE.md)
# A battlefield dialog (LOPTE LQCE LSGE LOBJE) is a stack of 304x16 rows from POPP.SPR with widgets
# on top.  x below is relative to the row.  Every frame is the lobby's tube read from the outside in:
# 11-grey seam, 35-grey, 107-grey light line, 35-grey, then the black interior (4 px); the panel
# border is the same tube seen from outside (35 | 107 | 35 | 11 seam, 4 px).  The maintainer
# (30 Sep 2026): "text boxes must have gray frame. scroll bar must have appropriate frame."
ROW_W, ROW_H = 304, 16
TUBE = (D11, BAND, LT, BAND)          # a frame, outside -> inside; interior black
BORDER = (BAND, LT, BAND, D11)        # the panel border, edge -> inside
LIST_X, LIST_W = 10, 256              # the list window (black) inside its frame x 6..269
LIST_FRAME_X = LIST_X - 4             # 6
CHANNEL_X, CHANNEL_W = 276, 18        # the scroll channel inside its frame x 272..297: UP, bar, DOWN
ARROW_X = CHANNEL_X + 1               # 277: the 16x16 arrow plates, 1 px black around them
SCROLL_X, SCROLL_W = 280, 10          # the engine's 10-px bar centred in the channel
LIST_TOP_DY, LIST_BOTTOM_DY = 4, 12   # black interior of the top row from row 4, of the bottom row to row 11
ARROW_TOP_DY, ARROW_BOTTOM_DY = 5, -5  # UP at top row + 5, DOWN at bottom row - 5 (1 px black above / below)
SCROLL_TOP_DY, SCROLL_SPAN_DY = 21, -26  # the bar between the arrows: y = top + 21, h = bottom - top - 26
BOX_H = 24                            # a single-line text box: frame 4 + interior 16 + frame 4
VALUE_BOX_W, VALUE_BOX_DX, VALUE_BOX_DY = 78, 16, -4   # LOPTE's read-out between "-" (x, y) and "+": box at (x + 16, y - 4)
VALUE_TEXT_DX, VALUE_TEXT_DY = 23, 3  # its in_text (8 columns, centred) at ("-" x + 23, "-" y + 3)
NAME_BOX_W, NAME_BOX_X, NAME_BOX_DY = 280, 12, 6     # LSGE's name field: a box inside the panel, 2 px of black to the
                                                    # panel's tubes (x 12..291, y top row + 6 under the panel's top tube)
NAME_TEXT_DX, NAME_TEXT_DY = NAME_BOX_X + 8, NAME_BOX_DY + 6  # its in_text at (row x + 20, top row + 12)
VALUE_BOX_CELL, NAME_BOX_CELL = 14, 15  # the two cells appended to POPP.SPR (stock: 14 cells, 0..13)
LIST_TOP_INNER = 27                   # a list top row INSIDE the panel (a compartment divider), cell 3 = at the panel's top
ROW_CELLS, LIST_CELLS = range(0, 6), (3, 4, 5, LIST_TOP_INNER)
FIRST_FREE_ID = 23                    # box pictures take the lowest free widget ids from here
# the options form (doc 10.54): the pre-battle menus' option row - a plain label left, the value between
# KNOBE's 14x14 arrows right (MULTIE), 90x26 text buttons (LOADGE), a large-font title (LOADGE)
PANEL_TOP, PANEL_MID, PANEL_BOTTOM = 16, 17, 18     # rows 3..last of an options dialog: one framed panel x 6..297
HEADER_CELL, BUTTON_CELL, MINUS_CELL, PLUS_CELL = 19, 20, 21, 22   # header box 292x32; KNOBE 2 (90x26), 14 / 16 (14x14)
KNOBE_COPIES = (2, 14, 16)            # KNOBE.SPR cells copied into POPP.SPR as 20, 21, 22
OPT_PANEL_X, OPT_PANEL_W = 6, 292
HEADER_DY, HEADER_H, HEADER_W, HEADER_X = 4, 44, 292, 6    # header box y0 + 4..47: from the border's seam down to the panel's top tube, full inner width
TITLE_X_INSET, TITLE_Y, TITLE_H = 4, 13, 28          # the title label's rect lies INSIDE the box (its black background must not erase the tubes):
                                                    # x + 4, 284 wide; y box + 13, 28 tall - the 21-px MFONTO2 glyphs (oy 2) centred in the 36-px interior
BLANK_ROW, BLANK_TOP = 24, 25                       # rows 0..2 under the header box: border + ground only, no pipework
ROW_KINDS = set(ROW_CELLS) | {PANEL_TOP, PANEL_MID, PANEL_BOTTOM, BLANK_ROW, BLANK_TOP, LIST_TOP_INNER}   # every cell a dialog row can carry
OPTION_DY, OPTION_PITCH = 63, 32                    # the k-th option's arrows at y0 + 63 + 32 k (strip 58..81); buttons after the last
# one option row is a strip picture (cell 23, 282x24 at row + 11): four lobby capsules - label 140, "<" 22, value 70,
# ">" 32 (plate + rounded end) - joined by 6-px bars (TCPWAIT.GIF slot rows, measured: 3-px outline 23 | 65 | 23, rounded outer ends)
ROW_STRIP_CELL, STRIP_X, STRIP_W, STRIP_H, STRIP_DY = 23, 11, 282, 24, -5   # strip y = arrows y - 5
CAPSULES = ((0, 140, True, False), (146, 22, False, False), (174, 70, False, False), (250, 32, False, True))  # x, w, round left / right
BARS = ((140, 146), (168, 174), (244, 250))                             # the grey bars between the capsules (x0, x1 exclusive)
LABEL_X, LABEL_W, MINUS_X, VALUE_X, PLUS_X = 27, 116, 161, 188, 265     # label 4 px right of its arc, value = the 64-px interior, plates 1 px in
OPTION_TEXT_DY, OPTION_ARROW_DY = 1, 0
OK_ID, CANCEL_ID, OK_X, CANCEL_X = 56, 55, 158, 56   # CANCEL left, OK right (LOADGE: BACK left, LOAD right)
OK_MSG, CANCEL_MSG = 7, 8
LARGE_BUTTON_CELL, LARGE_X = 26, 62                 # KNOBE 0 (180x26) copied into POPP: the quit dialog's two buttons, centred at row + 62
QUIT_GAP = 22                                       # ... and the pair centred vertically in the panel, 22 px between the plates (doc 10.55)
SIZE_LINE = re.compile(rb'^([ \t]*size[ \t]+\d+[ \t]+)(\d+)([ \t]+\d+[ \t]+)(\d+)(?=\s|$)')   # size X Y W H: groups 2 = Y, 4 = H
QUIT_NO_ID, QUIT_YES_MSG, QUIT_NO_MSG = 57, 2, 3   # pushb 57 = NO, CONTINUE; the texts are the quit dialog's textmsg 2 / 3
OK_CENTRE_X = 107                                   # a lone OK button (objectives) centred on the row
BOX_CELLS = {VALUE_BOX_CELL: (VALUE_BOX_W, BOX_H), NAME_BOX_CELL: (NAME_BOX_W, BOX_H), HEADER_CELL: (HEADER_W, HEADER_H),
             ROW_STRIP_CELL: (STRIP_W, STRIP_H)}


def _corner(cv, x0, y0, x1, y1, c):
    for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        cv.put(x, y, c)


def tube_frame(cv, x0, y0, x1, y1, fill=True):
    """The lobby's grey frame on the inclusive rect (x0, y0)..(x1, y1): rings 11 | 35 | 107 | 35 from
    the outside in, black inside.  The 35 ring drops its corner pixel and the light ring's corner is
    35 (the ONLINE screen's "one pixel off each corner").  Coordinates may lie outside the canvas:
    a frame that continues into the next row is drawn with its far edge off the cell."""
    if fill:
        cv.fill(x0, y0, x1, y1, BLK)
    for k, c in enumerate(TUBE):
        cv.ring(x0 + k, y0 + k, x1 - k, y1 - k, 0, c)
    _corner(cv, x0 + 1, y0 + 1, x1 - 1, y1 - 1, D11)
    _corner(cv, x0 + 2, y0 + 2, x1 - 2, y1 - 2, BAND)


def panel_border(cv, x0, y0, x1, y1):
    """The dialog panel's border: the tube seen from outside, 35 | 107 | 35 | 11 seam."""
    for k, c in enumerate(BORDER):
        cv.ring(x0 + k, y0 + k, x1 - k, y1 - k, 0, c)
    _corner(cv, x0, y0, x1, y1, D11)
    _corner(cv, x0 + 1, y0 + 1, x1 - 1, y1 - 1, BAND)


def capsule(cv, x0, y0, x1, y1, round_left=False, round_right=False):
    """A lobby element frame on black: 3-px outline 23 | 65 | 23 (TCPWAIT.GIF slot rows), rectangular,
    and where asked the end is a semicircle - the outline itself bends round, so the frame is closed.
    Unclickable furniture, so dim greys - the light 107 line is reserved for tube_frame()."""
    cv.fill(x0, y0, x1, y1, BLK)
    r = (y1 - y0) // 2
    for k, c in enumerate((D23, G65, D23)):
        ax0, ay0, ax1, ay1 = x0 + k, y0 + k, x1 - k, y1 - k
        rk = max(0, r - k) if (round_left or round_right) else 0
        cv.ring(ax0, ay0, ax1, ay1, rk, c)
        if rk and not round_left:                    # square this end again
            cv.fill(ax0, ay0, ax0 + rk, ay1, BLK)
            cv.hline(ax0, ax0 + rk, ay0, c)
            cv.hline(ax0, ax0 + rk, ay1, c)
            cv.vline(ax0, ay0, ay1, c)
        if rk and not round_right:
            cv.fill(ax1 - rk, ay0, ax1, ay1, BLK)
            cv.hline(ax1 - rk, ax1, ay0, c)
            cv.hline(ax1 - rk, ax1, ay1, c)
            cv.vline(ax1, ay0, ay1, c)


def option_strip():
    """One option row of the options form: label capsule, "<" cell, value capsule, ">" cell, joined by bars."""
    cv = Canvas(STRIP_W, STRIP_H)
    for x, w, rl, rr in CAPSULES:
        capsule(cv, x, 0, x + w - 1, STRIP_H - 1, rl, rr)
    ym = STRIP_H // 2
    for x0, x1 in BARS:
        cv.fill(x0, ym - 2, x1 - 1, ym, D23)
        cv.hline(x0, x1 - 1, ym - 1, G65)
    return cv


def text_box(w, h):
    """A framed black text box cell (the value read-outs of the options dialog, the save name)."""
    cv = Canvas(w, h)
    tube_frame(cv, 0, 0, w - 1, h - 1)
    return cv


def _dialog_row(i, w=ROW_W, h=ROW_H):
    """One 304x16 dialog row: 0 top, 1 plain, 2 bottom (panel border + pipework; the pre-10.55 rows,
    unused by the laid-out dialogs), 16 / 17 / 18 form panel top / middle / bottom (one tube frame
    x 6..297, black inside), 3 / 27 / 4 / 5 a list compartment of that panel - top at the panel's
    top / top inside the panel / middle / bottom (the list window in its frame at x 6..269 and the
    scroll channel in its frame at x 272..297, sharing the panel's side tubes), 24 / 25 blank /
    blank top under the header box (doc 10.54).  Pipework is seeded per row index, so every row of
    one kind is identical and rows of one kind tile."""
    FAR = 1000
    cv = Canvas(w, h, D11)
    pw = Pipework(cv, seed=5000 + i)
    px1 = OPT_PANEL_X + OPT_PANEL_W - 1                 # 297
    if i in LIST_CELLS:
        # a compartment of the black panel (doc 10.55): the panel's tubes at x 6..9 / 294..297 (its rounded
        # top corners on cell 3), inside it the list window's frame and the scroll channel's frame with
        # 2 px of black between them; a list top inside the panel (cell 27) and every list bottom (cell 5)
        # are full-width dividers - the frames' own top / bottom tubes continued across x 266..275, the
        # panel's side tubes drawn straight again over the frames' rounded corners
        py0 = 0 if i == 3 else -FAR
        tube_frame(cv, OPT_PANEL_X, py0, px1, FAR)
        ty0 = 0 if i in (3, LIST_TOP_INNER) else -FAR
        ty1 = h - 1 if i == 5 else FAR
        tube_frame(cv, LIST_FRAME_X, ty0, LIST_X + LIST_W + 3, ty1)
        tube_frame(cv, CHANNEL_X - 4, ty0, CHANNEL_X + CHANNEL_W + 3, ty1)
        tube_frame(cv, OPT_PANEL_X, py0, px1, FAR, fill=False)
        if i == LIST_TOP_INNER:
            for k, c in enumerate(TUBE):
                cv.hline(OPT_PANEL_X + 4, px1 - 4, k, c)
        if i == 5:
            for k, c in enumerate(TUBE):
                cv.hline(OPT_PANEL_X + 4, px1 - 4, h - 1 - k, c)
    elif i in (BLANK_ROW, BLANK_TOP):                   # under the options form's header box: border + ground only
        pass
    elif i in (PANEL_TOP, PANEL_MID, PANEL_BOTTOM):     # the options panel: one frame, full inner width, no channel;
        ty0 = 0 if i == PANEL_TOP else -FAR              # the bottom row = frame rows 4..7, ground, the dialog border 12..15
        ty1 = 7 if i == PANEL_BOTTOM else FAR
        tube_frame(cv, OPT_PANEL_X, ty0, OPT_PANEL_X + OPT_PANEL_W - 1, ty1)
    else:
        py0 = 4 if i == 0 else 0
        py1 = h - 5 if i == 2 else h - 1
        pw.band(4, py0, w - 5, py1)
    by0 = 0 if i in (0, BLANK_TOP) else -FAR
    by1 = h - 1 if i in (2, PANEL_BOTTOM) else FAR
    panel_border(cv, 0, by0, w - 1, by1)
    return cv


def build_popp(game):
    pal = read_palette(game)
    popp = spr.read_spr(find_file(game, 'INTRFACE', 'popp.spr'))
    cells = []
    for i, c in enumerate(popp['cells']):
        w, h = c['w'], c['h']
        if not w or not h:
            cells.append(dict(w=w, h=h, ox=c['ox'], oy=c['oy'], px=bytearray()))
            continue
        cv = Canvas(w, h)
        if (w, h) == (ROW_W, ROW_H):
            cv = _dialog_row(i, w, h)
        elif (w, h) == (112, 24):                            # title plate: a lobby button plate
            cv = lobby_plate(w, h)
        elif (w, h) == (32, 32):                             # OK (7) / cancel (8): lobby plate + stock glyph
            cv = lobby_plate(w, h)
            for y in range(4, h - 4):
                for x in range(4, w - 4):
                    v = c['px'][y * w + x]
                    rgb = tuple(pal[v * 3:v * 3 + 3])
                    if v in (0, BLK) or _is_grey(rgb):
                        continue
                    cv.put(x, y, v)
        elif (w, h) == (16, 16):                             # scroll / step arrows 10..13
            d = {10: 'up', 11: 'down', 12: 'left', 13: 'right'}[i]
            cv.px = arrow_cell(d, False, center=(8.0, 8.0), opaque=True)['px']
        else:
            cv.px = bytearray(c['px'])
        cells.append(dict(w=w, h=h, ox=c['ox'], oy=c['oy'], px=cv.px))
    assert len(cells) == VALUE_BOX_CELL, 'stock POPP.SPR has %d cells, expected %d' % (len(cells), VALUE_BOX_CELL)
    cells.append(dict(w=VALUE_BOX_W, h=BOX_H, ox=0, oy=0, px=text_box(VALUE_BOX_W, BOX_H).px))
    cells.append(dict(w=NAME_BOX_W, h=BOX_H, ox=0, oy=0, px=text_box(NAME_BOX_W, BOX_H).px))
    for kind in (PANEL_TOP, PANEL_MID, PANEL_BOTTOM):      # 16 / 17 / 18: the options panel rows
        cells.append(dict(w=ROW_W, h=ROW_H, ox=0, oy=0, px=_dialog_row(kind).px))
    cells.append(dict(w=HEADER_W, h=HEADER_H, ox=0, oy=0, px=text_box(HEADER_W, HEADER_H).px))         # 19: the header box
    knobe = spr.read_spr(find_file(game, 'INTRFACE', 'knobe.spr'))['cells']
    for src in KNOBE_COPIES:                                # 20 / 21 / 22: the lobby's text button and arrows, pixel for pixel
        c = knobe[src]
        cells.append(dict(w=c['w'], h=c['h'], ox=0, oy=0, px=bytearray(c['px'])))
    cells.append(dict(w=STRIP_W, h=STRIP_H, ox=0, oy=0, px=option_strip().px))                    # 23: one option row's capsules
    for kind in (BLANK_ROW, BLANK_TOP):                     # 24 / 25: the rows under the header box
        cells.append(dict(w=ROW_W, h=ROW_H, ox=0, oy=0, px=_dialog_row(kind).px))
    c = knobe[0]                                            # 26: the lobby's 180x26 text button (LARGEBUTTON) for the quit dialog
    cells.append(dict(w=c['w'], h=c['h'], ox=0, oy=0, px=bytearray(c['px'])))
    cells.append(dict(w=ROW_W, h=ROW_H, ox=0, oy=0, px=_dialog_row(LIST_TOP_INNER).px))    # 27: a list top inside the panel
    return popp['flags'], cells, [tuple(pal[i * 3:i * 3 + 3]) for i in range(256)]


def cmd_popp(args):
    flags, cells, pal = build_popp(args.game)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    spr.write_spr(args.out, flags, cells, pal)
    print('wrote %s: %d cells' % (args.out, len(cells)))
    return 0


# ---------------------------------------------------------------- preview
def cmd_preview(args):
    pal = read_palette(args.game)
    cv, info = render_frame(args.width, args.height, args.game)
    flags, cells, _ = build_bank(args.game)
    maine = args.maine or find_file(args.game, 'INTRFACE', 'maine')
    dx, dy = args.width - SRC_W, args.height - SRC_H
    sy = hud_layout.slack_rows(args.height)
    placed = set()
    show = {41, 42, 43, 44, 97, 98, 205, 46, 48, 71, 49, 50, 47, 51, 52, 134, 3, 147, 149, 75}
    if args.orders:
        show = {62, 63, 64, 151, 196, 202, 3, 147, 149, 75}
    for line in open(maine, 'rb').read().decode('latin1').splitlines():
        t = line.split('%')[0].split()
        if len(t) < 8 or t[0] not in ('pushb', 'checkb', 'count', 'picture', 'scount'):
            continue
        try:
            x, y = int(t[3]), int(t[4])
            cell = int(t[7])
        except ValueError:
            continue
        if cell < 0 or cell >= len(cells) or int(t[1]) not in show:
            continue
        if t[0] == 'picture' and int(t[1]) in (3, 4, 5, 6):
            x = 516
            cell = 77 + (2 if args.orders else 0)
        nx, ny = hud_layout.shift(x, y, dx, dy, sy) if args.maine is None else (x, y)
        if (nx, ny) in placed:
            continue
        placed.add((nx, ny))
        cv.blit(cells[cell], nx, ny)
    cv.image(pal).convert('RGB').save(args.out)
    print('wrote %s (%d widgets drawn)' % (args.out, len(placed)))
    return 0


# ---------------------------------------------------------------- apply: a whole set
PICTURES = re.compile(rb'^([ \t]*pictures[ \t]+)intrface/(mainbut|popp)\b', re.M | re.I)
TAB_STRIP = re.compile(rb'^(picture[ \t]+[3456][ \t]+0[ \t]+)(\d+)([ \t]+)96([ \t]+)(?:110|124|120)([ \t]+)(?:12|16)(?=\s)', re.M)
DIALOGS = ('LOPTE', 'LQCE', 'LSGE', 'LOBJE')
DIALOG_COPIES = ('exp/intrf_hd/lopte', 'dc/intrf_hd/lopte', 'ozi_ns/intrf_hd/lopte')


HUD_TEXT = re.compile(rb'^(in_text[ \t]+(148|200|234)[ \t]+\d+[ \t]+)(\d+)([ \t]+)(\d+)', re.M)


def edit_hud_script(data, width, height=SRC_H):
    """MAINE: `pictures intrf_hd/mainbut`, and the tab strips `picture 3..6` (stock 110x12 at x 521)
    as the 120x16 strips at the panel's left edge x 516 (+ W-640); the 124-px form of 28 Sep is rewritten too.  Idempotent; the patcher's
    Edit-HudScript does the same."""
    data = PICTURES.sub(rb'\1intrf_hd/\2', data)
    x = 516 + width - SRC_W

    def strip(m):
        return m.group(1) + str(x).encode() + m.group(3) + b'96' + m.group(4) + b'%d' % TAB_STRIP_W + m.group(5) + b'16'
    dx, dy = width - SRC_W, height - SRC_H

    def text(m):                                  # the bar's two texts inside the message screen, the DAYS count in its screen (doc 10.54)
        tx, ty = HUD_TEXT_POS[int(m.group(2))]
        nx = m.group(3) if tx is None else b'%d' % (tx + dx)
        return m.group(1) + nx + m.group(4) + b'%d' % (ty + dy)
    return HUD_TEXT.sub(text, TAB_STRIP.sub(strip, data))


WIDGET_KINDS = (b'pushb', b'checkb', b'in_text', b'picture', b'list', b'scroll', b'gadget', b'label',
                b'count', b'scount', b'group')


def _set_tokens(line, changes):
    """Rewrite whitespace-separated tokens (1-based index -> bytes) of a script line, keeping its spacing."""
    parts = re.split(rb'(\s+)', line)
    n = 0
    for k, p in enumerate(parts):
        if p and not p.isspace():
            n += 1
            if n in changes:
                parts[k] = changes[n]
    return b''.join(parts)


def _pushb_cell(t):
    """The plate cell of a `pushb id desc x y w h A B` line: the non-negative one of A / B
    (`-16 8` = OK plate 8, `10 -10` = UP arrow 10)."""
    for v in t[7:9]:
        if v.isdigit():
            return int(v)
    return -1


def console_dialog(data):
    """Lay a battlefield dialog script out on the console plates of POPP.SPR (a script naming
    `intrf_hd/popp`; anything else is returned unchanged).  Idempotent and position-derived, so the
    tool chain and the patcher may run it in any order and any number of times.  Every dialog is a
    form (doc 10.54, DC16_INTERFACE_STYLE_GUIDE.md §6): rows 0..2 are blank rows (cells 25 / 24)
    under the header box (cell 19) with the title as a font-1 (MFONTO2) label inside it, the red
    title / label plates (cell 6) are dropped, the buttons are the lobby's text buttons (cell 20,
    90x26, or cell 26, 180x26, `label centre`), font 0 is MFONTO5 and the hover brightness is on.

    * list rows (cells 3 / 4 / 5): the `list` sits in the list window (x row + 10, y top row + 4,
      down to the bottom row + 11), the UP / DOWN plates (pushb cells 10 / 11 bound to the list) in
      the scroll channel at x row + 277 (UP top row + 5, DOWN bottom row - 5) and the `scroll` bar
      between them (x row + 280, 10 px wide); OK (id 56) / CANCEL (id 55) are 90x26 text buttons at
      x row + 158 / + 56 (a lone OK centred at + 107) on their own y;
    * rows 3..last of EVERY dialog are one framed black panel (cells 16 / 17 / 18, doc 10.55); the
      list rows are compartments of it (cell 3 at the panel's top, 27 for a list top inside the panel,
      4, 5 - full-width dividers), the buttons and boxes sit on its black;
    * an `in_text` without a "-" / "+" pair (the save name): a name box picture (cell 15, 280x24)
      inside the panel at (row + 12, top row + 6), the in_text at (row + 20, top row + 12);
    * a dialog with "-" / "+" pairs (the options dialog): every option a capsule strip (cell 23) with
      its label, the value between KNOBE's arrows (cells 21 / 22) and CANCEL / OK under the last option;
    * the quit dialog (pushb 57 present): two 180x26 text buttons (cell 26) at x row + 62 with the
      YES, QUIT / NO, CONTINUE texts (textmsg 2 / 3), the two label widgets that carried them dropped;
    * box pictures are regenerated on every pass (old ones dropped), numbered with the lowest free
      widget ids from 23 in y order and inserted as one block after the last picture line."""
    if not re.search(rb'(?im)^[ \t]*pictures[ \t]+intrf_hd/popp\b', data):
        return data
    lines = data.split(b'\n')
    rec = {}                                     # line index -> (kind, id, toks) for widget lines
    for i, raw in enumerate(lines):
        toks = raw.split(b'%')[0].split()
        if len(toks) >= 5 and toks[0].lower() in WIDGET_KINDS and toks[1].isdigit() and toks[3].isdigit() and toks[4].isdigit():
            rec[i] = (toks[0].lower(), int(toks[1]), toks)
    rows = [(i, int(t[4]), int(t[3]), int(t[7])) for i, (k, n, t) in rec.items()
            if k == b'picture' and len(t) >= 8 and t[7].isdigit() and int(t[7]) in ROW_KINDS]
    if not rows:
        return data
    row_x = min(x for _, _, x, _ in rows)
    assert all(x == row_x for _, _, x, _ in rows), 'dialog rows at different x'
    changes = {}                                 # line index -> {1-based token: value}
    rebuilt = {}                                 # line index -> whole new body
    drop = set()                                 # line indices to drop
    boxes = []                                   # (y, x, cell)
    minus = [(int(t[3]), int(t[4])) for k, n, t in rec.values() if k == b'pushb' and _pushb_cell(t) in (12, MINUS_CELL)]
    quit_form = any(k == b'pushb' and n == QUIT_NO_ID for k, n, t in rec.values())
    # lists with their scroll channel
    for i, (k, n, t) in rec.items():
        if k != b'list':
            continue
        y0, h = int(t[4]), int(t[6])
        lr = [y for _, y, _, c in rows if c in LIST_CELLS and y0 - ROW_H < y < y0 + h]
        if not lr:
            continue
        top, bottom = min(lr), max(lr)
        ly = top + LIST_TOP_DY
        changes[i] = {4: b'%d' % (row_x + LIST_X), 5: b'%d' % ly, 7: b'%d' % (bottom + LIST_BOTTOM_DY - ly)}
        for j, (k2, n2, t2) in rec.items():
            if k2 == b'scroll' and top <= int(t2[4]) <= bottom + ROW_H:
                changes[j] = {4: b'%d' % (row_x + SCROLL_X), 5: b'%d' % (top + SCROLL_TOP_DY), 6: b'%d' % SCROLL_W,
                              7: b'%d' % (bottom - top + SCROLL_SPAN_DY)}
            elif k2 == b'pushb' and b'list' in t2[8:] and _pushb_cell(t2) in (10, 11) and top - ROW_H <= int(t2[4]) <= bottom + ROW_H:
                up = _pushb_cell(t2) == 10
                changes[j] = {4: b'%d' % (row_x + ARROW_X), 5: b'%d' % ((top + ARROW_TOP_DY) if up else (bottom + ARROW_BOTTOM_DY))}
    # the form frame every dialog shares: blank rows under the header box, the title inside it, no red plates
    ordered = sorted(rows, key=lambda r: r[1])
    y0 = ordered[0][1]
    for idx, (li, y, x, c) in enumerate(ordered[:3]):
        changes.setdefault(li, {})[8] = b'%d' % (BLANK_TOP if idx == 0 else BLANK_ROW)
    boxes.append((y0 + HEADER_DY, row_x + HEADER_X, HEADER_CELL))
    band = {my: k for k, (mx, my) in enumerate(sorted(minus, key=lambda p: p[1]))}

    def option_of(ty):
        for my, k in band.items():
            if abs(my - ty) <= 8:
                return k
        return None
    for i, (k, n, t) in rec.items():
        if k == b'picture' and len(t) >= 8 and t[7] == b'6':          # the red title / label plates
            drop.add(i)
        elif k == b'label' and b'centre' in t and option_of(int(t[4])) is None:     # the title
            changes[i] = {4: b'%d' % (row_x + HEADER_X + TITLE_X_INSET), 5: b'%d' % (y0 + HEADER_DY + TITLE_Y),
                          6: b'%d' % (HEADER_W - 2 * TITLE_X_INSET), 7: b'%d' % TITLE_H, 13: b'1'}
    if minus:
        _options_layout(rec, ordered, row_x, sorted(minus, key=lambda p: p[1]), option_of, changes, rebuilt, boxes)
    else:
        _list_form(rec, rows, ordered, row_x, quit_form, changes, rebuilt, drop, boxes)
    # apply the changes, drop the old box pictures, insert the new ones after the last picture line
    out, last_picture, used = [], None, set()
    bottom = ordered[-1][1] + ROW_H                  # the `size` rect must reach the last row: the engine draws nothing below it
    for i, raw in enumerate(lines):
        m = SIZE_LINE.match(raw)
        if m and int(m.group(4)) < bottom - int(m.group(2)):   # (the stock objectives dialog says 272 for its 18 rows)
            raw = m.group(1) + m.group(2) + m.group(3) + b'%d' % (bottom - int(m.group(2))) + raw[m.end():]
        if i in rec:
            k, n, t = rec[i]
            if i in drop or (k == b'picture' and len(t) >= 8 and t[7].isdigit() and int(t[7]) in BOX_CELLS):
                continue
            used.add(n)
            body, sep, comment = raw.partition(b'%')
            if i in rebuilt:
                raw = re.match(rb'\s*', body).group(0) + rebuilt[i] + (b'\r' if body.endswith(b'\r') else b'') + sep + comment
            elif i in changes:
                raw = _set_tokens(body, changes[i]) + sep + comment
            if k == b'picture':
                last_picture = len(out)
        out.append(raw)
    ids = iter(n for n in range(FIRST_FREE_ID, 1000) if n not in used)
    cr = b'\r' if out[last_picture].endswith(b'\r') else b''
    new = [b'picture  %d  0  %d   %d  %d  %d   %d' % ((next(ids), x, y) + BOX_CELLS[c] + (c,)) + cr
           for y, x, c in sorted(boxes)]
    out = out[:last_picture + 1] + new + out[last_picture + 1:]
    return b'\n'.join(_form_header(out, ok_cancel=not quit_form, body_font=bool(minus)))


def _text_button(n, x, y, w, cell, msg):
    return b'pushb    %d  0  %d  %d   %d  26  -11 %d  label centre %d 2  -  remap 0' % (n, x, y, w, cell, msg)   # font 2 = MFONTO5


def _options_layout(rec, ordered, row_x, minus, option_of, changes, rebuilt, boxes):
    """The options form (doc 10.54): rows 3..last one framed panel, a capsule strip per option with
    the label, the value and KNOBE's arrows, CANCEL / OK under the last option."""
    y0 = ordered[0][1]
    for idx, (li, y, x, c) in enumerate(ordered):
        if idx >= 3:
            changes.setdefault(li, {})[8] = b'%d' % (PANEL_TOP if idx == 3 else PANEL_BOTTOM if idx == len(ordered) - 1 else PANEL_MID)
    for k in range(len(minus)):
        boxes.append((y0 + OPTION_DY + OPTION_PITCH * k + STRIP_DY, row_x + STRIP_X, ROW_STRIP_CELL))
    for i, (k, n, t) in rec.items():
        ty = int(t[4])
        if k == b'pushb' and _pushb_cell(t) in (12, MINUS_CELL, 13, PLUS_CELL):
            o = option_of(ty)
            if o is None:
                continue
            plus = _pushb_cell(t) in (13, PLUS_CELL)
            changes[i] = {4: b'%d' % (row_x + (PLUS_X if plus else MINUS_X)), 5: b'%d' % (y0 + OPTION_DY + OPTION_PITCH * o + OPTION_ARROW_DY),
                          8: b'-11', 9: b'%d' % (PLUS_CELL if plus else MINUS_CELL)}
        elif k == b'pushb' and n in (OK_ID, CANCEL_ID):
            by = y0 + OPTION_DY + OPTION_PITCH * len(minus)
            rebuilt[i] = _text_button(n, row_x + (OK_X if n == OK_ID else CANCEL_X), by, 90, BUTTON_CELL, OK_MSG if n == OK_ID else CANCEL_MSG)
        elif k == b'in_text':
            o = option_of(ty)
            if o is not None:
                changes[i] = {4: b'%d' % (row_x + VALUE_X), 5: b'%d' % (y0 + OPTION_DY + OPTION_PITCH * o + OPTION_TEXT_DY)}
        elif k == b'label':
            o = option_of(ty)
            if o is not None:
                changes[i] = {4: b'%d' % (row_x + LABEL_X), 5: b'%d' % (y0 + OPTION_DY + OPTION_PITCH * o + OPTION_TEXT_DY), 6: b'%d' % LABEL_W, 7: b'14'}


def _list_form(rec, rows, ordered, row_x, quit_form, changes, rebuilt, drop, boxes):
    """The save, objectives and quit dialogs (doc 10.55): rows 3..last are the same black panel as the
    options form's (cells 16 / 17 / 18), the list rows are compartments of it (cell 3 when the list
    starts at the panel's top, 27 for a list top inside the panel, 4, 5), the name field sits in a
    name box inside the panel, the buttons are text buttons on their own y (OK right / CANCEL left,
    a lone OK centred; the quit dialog's two 180-px YES, QUIT / NO, CONTINUE buttons centred, their
    label widgets dropped).  A list ending on the dialog's last row is not laid out (no such dialog).
    List rows are the list-cell rows inside a `list` widget's y range: the stock save dialog frames
    its name field with two list-top / middle cells (rows 3 / 4) that are panel rows here."""
    has_cancel = any(k == b'pushb' and n == CANCEL_ID for k, n, t in rec.values())
    list_y = set()
    for k, n, t in rec.values():
        if k == b'list':
            ly0, lh = int(t[4]), int(t[6])
            list_y.update(y for _, y, _, c in rows if c in LIST_CELLS and ly0 - ROW_H < y < ly0 + lh)
    l_top, l_bottom = (min(list_y), max(list_y)) if list_y else (None, None)
    for idx, (li, y, x, c) in enumerate(ordered):
        if idx < 3:
            continue
        if y in list_y:
            kind = (3 if idx == 3 else LIST_TOP_INNER) if y == l_top else 5 if y == l_bottom else 4
        else:
            kind = PANEL_TOP if idx == 3 else PANEL_BOTTOM if idx == len(ordered) - 1 else PANEL_MID
        changes.setdefault(li, {})[8] = b'%d' % kind
    for i, (k, n, t) in rec.items():
        if k == b'in_text':
            ty = int(t[4])
            top = max(y for _, y, _, _ in rows if y <= ty)
            boxes.append((top + NAME_BOX_DY, row_x + NAME_BOX_X, NAME_BOX_CELL))
            changes[i] = {4: b'%d' % (row_x + NAME_TEXT_DX), 5: b'%d' % (top + NAME_TEXT_DY)}
        elif k == b'pushb' and n in (OK_ID, CANCEL_ID, QUIT_NO_ID) and b'list' not in t[8:]:
            by = int(t[4])
            if quit_form:
                # the pair centred in the panel's interior (row 3 + 4 .. last row + 3), YES above NO, 22 px apart
                inner_y0, inner_h = ordered[3][1] + 4, ordered[-1][1] - ordered[3][1]
                by = inner_y0 + (inner_h - (2 * 26 + QUIT_GAP)) // 2 + (0 if n == OK_ID else 26 + QUIT_GAP)
                rebuilt[i] = _text_button(n, row_x + LARGE_X, by, 180, LARGE_BUTTON_CELL, QUIT_YES_MSG if n == OK_ID else QUIT_NO_MSG)
            else:
                if l_bottom is not None:
                    # centred between the list block's bottom divider (bottom row + 16) and the panel's bottom tube (last row + 4)
                    by = l_bottom + ROW_H + (ordered[-1][1] + 4 - (l_bottom + ROW_H) - 26) // 2
                if n == OK_ID:
                    rebuilt[i] = _text_button(n, row_x + (OK_X if has_cancel else OK_CENTRE_X), by, 90, BUTTON_CELL, OK_MSG)
                else:
                    rebuilt[i] = _text_button(n, row_x + CANCEL_X, by, 90, BUTTON_CELL, CANCEL_MSG)
        elif quit_form and k == b'label' and b'centre' not in t:
            drop.add(i)                          # the YES, QUIT / NO, CONTINUE labels: the buttons carry the texts now


def _form_header(out, ok_cancel, body_font):
    """The form's header lines: font 1 MFONTO2 (titles), font 2 MFONTO5 (button captions), the hover
    brightness, and (when the form has OK / CANCEL buttons) their texts right after the first textmsg
    line - existing OK / CANCEL texts are dropped first, so the place does not depend on the file's
    history - the same for the font 1 / font 2 / bright_* lines.  Font 0 becomes MFONTO5 only for the
    options form (`body_font`): the objectives list's lines are wrapped for MFONTO7 and overflow the
    window in the wider font.  Line endings follow the line the additions are added after."""
    def cr_of(line):
        return b'\r' if line.endswith(b'\r') else b''
    added = re.compile(rb'\s*(textmsg\s+(%d|%d)|font\s+[12]|font_offset\s+[12]|bright_pushed|bright_highlight)\s' % (OK_MSG, CANCEL_MSG))
    out = [line for line in out if not added.match(line)]      # the lines this pass adds: dropped and re-inserted canonically
    first_msg = next((i for i, line in enumerate(out) if re.match(rb'\s*textmsg\s+\d+\s', line)), None)
    res = []
    for i, line in enumerate(out):
        body = line.rstrip(b'\r')
        m = re.match(rb'(\s*font\s+0\s+)intrface/mfonto7\b(.*)', body, re.I) if body_font else None
        if m:
            line = m.group(1) + b'intrface/mfonto5' + m.group(2) + cr_of(line)
        res.append(line)
        if re.match(rb'\s*font_offset\s+0\s', body):
            res.append(b'font 1 intrface/mfonto2' + cr_of(line))
            res.append(b'font_offset  1 31' + cr_of(line))
            res.append(b'font 2 intrface/mfonto5' + cr_of(line))
            res.append(b'font_offset  2 31' + cr_of(line))
        if re.match(rb'\s*colour\s+selbg\s', body):
            res.append(b'bright_pushed    8' + cr_of(line))
            res.append(b'bright_highlight 4' + cr_of(line))
        if ok_cancel and i == first_msg:
            res.append(b'textmsg %d OK' % OK_MSG + cr_of(line))
            res.append(b'textmsg %d CANCEL' % CANCEL_MSG + cr_of(line))
    return res


def edit_dialog_script(data):
    return console_dialog(PICTURES.sub(rb'\1intrf_hd/\2', data))


def _find(folder, name):
    for fn in os.listdir(folder):
        if fn.lower() == name.lower():
            return os.path.join(folder, fn)
    return None


def cmd_apply(args):
    game = args.game or args.target
    hd = os.path.join(args.target, 'INTRF_HD')
    if not os.path.isdir(hd):
        sys.exit('%s: no INTRF_HD folder' % args.target)
    pal = read_palette(game)
    cv, info = render_frame(args.width, args.height, game)
    gif = os.path.join(hd, 'INTRFACE.GIF')
    cv.image(pal).save(gif, format='GIF', version='GIF87a', interlace=False, optimize=False)
    print('wrote %s (%dx%d)' % (gif, args.width, args.height))
    if not args.no_bank:
        flags, cells, p = build_bank(game)
        spr.write_spr(os.path.join(hd, 'MAINBUT.SPR'), flags, cells, p)
        flags, cells, p = build_popp(game)
        spr.write_spr(os.path.join(hd, 'POPP.SPR'), flags, cells, p)
        flags, cells, p = build_clock(game)
        sprites = _find(args.target, 'SPRITES') or os.path.join(args.target, 'SPRITES')
        os.makedirs(sprites, exist_ok=True)
        spr.write_spr(os.path.join(sprites, 'CLOCK.SPR'), flags, cells, p)
        print('wrote %s, POPP.SPR and %s' % (os.path.join(hd, 'MAINBUT.SPR'), os.path.join(sprites, 'CLOCK.SPR')))
    edits = 0
    maine = _find(hd, 'MAINE')
    if maine:
        d = open(maine, 'rb').read()
        n = edit_hud_script(d, args.width, args.height)
        if n != d:
            open(maine, 'wb').write(n)
            edits += 1
    for name in DIALOGS:
        f = _find(hd, name)
        if f:
            d = open(f, 'rb').read()
            n = edit_dialog_script(d)
            if n != d:
                open(f, 'wb').write(n)
                edits += 1
    for rel in DIALOG_COPIES:
        f = os.path.join(args.target, *rel.split('/'))
        if os.path.exists(f):
            d = open(f, 'rb').read()
            n = edit_dialog_script(d)
            if n != d:
                open(f, 'wb').write(n)
                edits += 1
    print('%d script(s) edited (pictures intrf_hd/mainbut|popp, tab strips at x %d %dx16)' % (edits, 516 + args.width - SRC_W, TAB_STRIP_W))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('apply')
    p.add_argument('target', help='game folder or hd_sets/<WxH> fixture (must hold INTRF_HD)')
    p.add_argument('--game', default=None, help='where MAINBUT.SPR, BUTTON.SPR, KNOBE.SPR, POPP.SPR, the font and PALETTE.GIF are read (default: target)')
    p.add_argument('--width', type=int, default=1024)
    p.add_argument('--height', type=int, default=768)
    p.add_argument('--no-bank', action='store_true', help='frame + script edits only (fixtures: the banks ship once)')
    p = sub.add_parser('frame')
    p.add_argument('game')
    p.add_argument('--width', type=int, default=1024)
    p.add_argument('--height', type=int, default=768)
    p.add_argument('--out', required=True)
    p = sub.add_parser('bank')
    p.add_argument('game')
    p.add_argument('--out', required=True)
    p = sub.add_parser('popp')
    p.add_argument('game')
    p.add_argument('--out', required=True)
    p = sub.add_parser('clock')
    p.add_argument('game')
    p.add_argument('--out', required=True)
    p = sub.add_parser('preview')
    p.add_argument('game')
    p.add_argument('--width', type=int, default=1024)
    p.add_argument('--height', type=int, default=768)
    p.add_argument('--maine', default=None, help='an already shifted MAINE (default: the stock one, shifted here)')
    p.add_argument('--orders', action='store_true', help='show the Game Option tab buttons instead of the build tab')
    p.add_argument('--out', default='hud_preview.png')
    args = ap.parse_args(argv)
    return {'frame': cmd_frame, 'bank': cmd_bank, 'popp': cmd_popp, 'clock': cmd_clock,
            'preview': cmd_preview, 'apply': cmd_apply}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())

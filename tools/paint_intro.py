#!/usr/bin/env python3
"""Repaint the main-menu backgrounds (INTRFACE/INTRG.GIF, INTRO.GIF) at any framebuffer size.

The first menu after the intro movie (NEW CAMPAIGN, TRAINING, LOAD GAME, ...) is the script
`bintroe` (main.c `bintro` 0x00404DC8): background INTRG.GIF, the `DCUK` logo animation, the `DCUT`
title, and the scrolling credits.txt box that the *code* positions. `introe` is the same menu on
INTRO.GIF with the `DCSS` logo (not reached by the retail exe, which only names intrface/bintro),
and `BUTTONSE` / `DINTROE` are demo leftovers on INTRO.GIF. Council Wars shares the GIFs and
sprites and adds `exp/intrface/BINTROE` / `INTROE` overrides with one column of buttons. Both
stock GIFs are the same 640x480 picture - a starfield and the rim-lit limb of Mars - and differ
only in the bottom band (Take 2 logo and copyright in INTRG, SSI logo, red rule and copyright in
INTRO). At 1024x768 `pad_background.py` letterboxes them, which leaves black borders around a
640x480 picture. This tool paints genuine full-frame versions instead.

Design rules (docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.11, the realistic rendering 10.56):

* Nothing is resampled. The starfield and the planet are *procedural*: the master is the code and
  its parameters below, measured from the stock art (planet circle centre (320, 351) radius 291
  in the 640x480 frame, 10 px atmospheric glow outside the limb, a lit crescent 45 px deep at the
  top, 4.95e-3 stars per pixel with 2/3 of them faint). Any later resolution is a re-render. The
  SSI logo, rule and copyright line are pixel art at UI scale like the buttons and the DC logo;
  they are copied 1:1 from the stock file (the rule is extended to the full width), never scaled.
* Since 1 Oct 2026 the scene is rendered realistically (maintainer: "create realistic picture
  based on main menu background which can be used as a background for all prebattle forms
  including main menu"): the planet is a shaded sphere lit by a sun above and behind it, with a
  bump-mapped, cratered surface under a Mars-like albedo map and a dust haze on the lit rim and
  outside the limb; the stars follow a magnitude-like distribution with colour temperatures. The
  stock geometry is kept exactly (same circle, the terminator 45 px inside the limb on the centre
  line, the glow's extent), so every menu layout rule built on it still holds; `render` checks
  that nothing brighter than 7/255 lies below row CRESCENT_TAIL over the DC logo's columns.
* The same scene is written three times per size: INTRG.GIF and INTRO.GIF with their stock bottom
  bands (main menu) and BACKDROP.GIF without a band - the ground on which pad_background.py lays
  every pre-battle screen (the 640x480 picture centred in a grey panel frame) instead of black.
  The three ship per size as INTRF_HD/<WxH>/*.GIF; the patcher copies them.
* The 256-entry colour table is the game's master palette (identical to PALETTE.GIF) and is kept
  byte for byte: gifload.c feeds indices straight into the shade LUT and INTRO.RGB / INTRO.RMP
  are derived from this palette. The render is quantised to the nearest existing colour (index 0
  excluded: it is the sprite-transparent black; black is index 254 as in the stock file) with an
  ordered dither over the planet and its glow so the shading uses the palette's orange, brown and
  tan ramps together instead of banding; sky and stars are never dithered.
* The output keeps the byte layout the game's GIF parser expects (GIF87a, global colour table,
  image descriptor next, no extension blocks) - verified with pad_background.check_gif_layout.
* The logo sprites drawn `unmask` over the picture carry the stock backdrop baked into their
  cells (see SCREENS below); `apply` re-bakes them. The gadget names an *animation*
  (`ANIMATE/DCUK.FIN`, listed in `INTRG.DAT`), whose frames name the sprite bank `dcuk`, and
  animate.c opens banks through "sprites/%s" (0x00425356) - so the frames the game draws come
  from SPRITES/DCUK.SPR, while INTRFACE/DCUK.SPR is a byte-identical copy. `apply` re-bakes
  every copy it finds (learned from the third game test, which still showed the stock stars).

The scripts then go back to `size W H` with their widgets laid out for the new canvas: title,
credits and buttons form one cluster that keeps its stock vertical centre as a fraction of the
height and is centred horizontally (the button grid as a unit, the title on its own); the DC logo
moves with the cluster so that it stays right above the title as in the original, centred. The
credits box is positioned by two immediates in the exe; `plan`/`apply` print where it has to go
and `patch_resolution.py` stage 2 puts it there (fixups 0x42A0 / 0x4299).

CLI
    python paint_intro.py render OUT.GIF --stock INTRG.GIF.bak [--width 1024 --height 768] [--seed 7]
    python paint_intro.py plan   GAME_DIR [--width 1024 --height 768]
    python paint_intro.py apply  GAME_DIR [--width 1024 --height 768] [--seed 7]
    python paint_intro.py preview GAME_DIR OUT.png [--script bintroe]   # logo, title, buttons

`apply` renders INTRFACE/INTRG.GIF and INTRO.GIF (keeping a .bak of each stock 640x480 file if
none exists yet) and INTRFACE/BACKDROP.GIF (no stock counterpart; split_hd_data.py moves it to
INTRF_HD), re-bakes DCUK.SPR, DCUT.SPR and DCSS.SPR, and rewrites `bintroe`, `introe`,
`BUTTONSE`, `DINTROE` and the `exp/intrface` overrides from their pristine copies.
`pad_background.py` recognises the result (a script already at `size W H` for the target size)
and leaves it alone; its `revert` restores the stock files.

Since 14 Sep 2026 the game folders keep the stock files in INTRFACE / SPRITES and the 1024x768
files in INTRF_HD / SPRITES/*_HD.SPR (doc 10.17, split_hd_data.py). Run this tool on a *copy* of
the stock game folder (it works in place, as described above), then `split_hd_data.py apply` moves
the results into INTRF_HD, renames the re-baked banks to DCSS_HD / DCUK_HD / DCUT_HD with matching
ANIMATE/*_HD.FIN and retargets the scripts' `background` lines to `intrf_hd/`.
"""

import argparse
import math
import os
import re
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pad_background import check_gif_layout, POSITIONED, SIZE2, SIZE4  # noqa: E402

# ------------------------------------------------------------------ measured stock geometry
STOCK_W, STOCK_H = 640, 480
PLANET_CX, PLANET_CY, PLANET_R = 320.2 / STOCK_W, 351.0 / STOCK_H, 291.4 / STOCK_H
GLOW_PX = 10.0            # atmospheric glow outside the limb, at 480 rows
LIT_DEPTH_PX = 45.0       # depth of the lit crescent at the top of the limb, at 480 rows
STAR_DENSITY = 4.95e-3    # stars per sky pixel (1244 over 251 198 px in the stock file)
# the realistic rendering (1 Oct 2026, doc 10.56): surface texture in a (lat, lon) map about the
# view axis, crater count and the radius from which craters get flat floors (texture px), ordered
# dither amplitude (+-/255) and the brightness (/255) below which nothing is dithered - the dark
# fall-off towards the terminator must stay clean so that CRESCENT_TAIL holds
# The surface is the real one since 1 Oct 2026 (doc 10.57, maintainer: "take a real Martian topology
# and wrap on the sphere"): MOLA heights and the Viking colour mosaic from tools/mars/ (mars_maps.py).
# Orientation: the pole tilted MARS_TILT degrees towards the viewer about the screen's x axis (0 =
# north up, the polar cap at the top of the limb; 90 = pole at the disc centre, the equator on the
# limb), then the planet turned so that longitude MARS_LON0 (east) faces the top of the limb.
# MARS_RELIEF exaggerates the slopes for the bump shading (the real 1:1 relief is invisible at a
# few pixels per degree).
# Maintainer, 1 Oct 2026: "canyon must go from viewers night side across horizon but not in 90 degrees"
# - first 75, then "mariner valley must set to 50 degrees": the western end of Valles Marineris
# (7 S 268 E) sits at the top of the limb and the canyon's trace 12 degrees further east lies inside
# the disc 49 degrees below the horizontal (brute-force solve in the 1 Oct session, doc 10.57), so the
# canyon climbs from the night side across the lit band and over the horizon, the Tharsis plateau
# beside it.
MARS_TILT = 174.0          # the axis almost along the view direction, south pole towards the viewer
MARS_ROLL = -83.0          # ... and rolled, so that east runs obliquely up the screen at the crossing
MARS_LON0 = 179.0          # the longitude under the top of the limb
MARS_RELIEF = 2.5          # 6 until the maintainer's "lights and shading right now are too aggressive"
SLOPE_LIGHT_CAP = 0.10     # a sun-facing slope is at most this much (in n.L) brighter than flat ground
SLOPE_SMOOTH = 2           # passes of a 5-point blur over the slope maps
MARS_TINT = (1.05, 0.62, 0.36)   # the mosaic's brightness is coloured with this (x the sun) - the stock orange
MARS_RADIUS_M = 3396000.0
# lighting fall-off towards the terminator: Lambert steepened (shadowing) and the flat sphere's n.L
# envelope (nothing lit beyond it); 1.6 / 0.10 / 2.5 until the canyon view, which needs the inner half
# of the band readable (maintainer: the canyon runs from the night side across the horizon); 0.08 / 2.0
# put lit canyon walls 6 rows past the DC logo limit at 1024x768, so only the Lambert power eased
LAMBERT_POWER = 1.3
BODY_GAIN = 2.6            # overall surface brightness; 3.2 clipped the lit canyon walls to full orange ("glows as a mirror")
ENVELOPE_NL = 0.10
ENVELOPE_POWER = 2.5
DITHER_AMP = 10.0
DITHER_MIN = 20
LOGO_W = 310              # the DC logo sprite's width (crescent_tail_row measures under it)
BACKDROP_GIF = 'BACKDROP.GIF'   # the bare backdrop (no bottom band): ground of the pre-battle screens

# Bottom band of the stock files, copied 1:1 (rows 421..478; above and below it is black). In
# INTRO.GIF: SSI logo rows 423..447, a full-width red rule 448..458, copyright 462..475; in
# INTRG.GIF: the Take 2 logo and copyright, rows 435..478, no rule. Rows that are non-black
# across (almost) the whole stock width are rules and are extended over the new width.
BAND_TOP, BAND_BOTTOM = 421, 479
RULE_MIN_PX = 630

# Script -> background GIF and the `unmask` logo sprites drawn over it (the sprite names as
# they appear in the gadget lines). Matched case-insensitively; the Council Wars overrides in
# `exp/intrface` use the same entries. Rebake modes:
#   'full'  - the cell carries sky, stars and the limb (DCSS: 15 frames, DCUK: 14 frames,
#             frame 0 of DCUK a solid flash). Black is index 0, never 254. That backdrop is
#             *another quantisation* of the stock scene (only ~20 % of the limb pixels equal
#             the GIF byte for byte) and the letters shimmer from frame to frame, so a pixel is
#             taken as backdrop if it agrees with the per-pixel mode across the frames where
#             that mode has >= PLATE_MIN_AGREE supporters, or has a limb colour (orange / red /
#             brown ramp), or is a non-limb speck of <= STAR_MAX_PX connected pixels (a star),
#             or is black sky more than 1 px from the letters (their black shadows stay 0).
#   'black' - the backdrop becomes plain black (index 0): the maintainer wants the DC logo on
#             black, and at its new place over the dark disc the background is black anyway.
#             Because nothing has to be preserved but the logo, the rule is stricter than
#             'full': after the plate / limb-colour pass, a pixel also counts as backdrop if it
#             equals the *reference frame* (the frame with the least logo in it, i.e. the static
#             baked backdrop), and then everything that is not a piece of >= PIECE_MIN_PX
#             connected pixels is dropped too - stars glued to a piece's edge, halos of bright
#             stars (up to ~100 px), the glow streak of the limb. Small fragments survive only
#             when they are logo-coloured (colours found in the big pieces but not in the
#             reference backdrop). The fourth game test showed the light leftovers this fixes.
#   'stars' - the sprite sits over the black disc (DCUT title, 5 frames incl. a solid flash and
#             a red glow that the limb-colour rule must not touch): only pixels equal to a
#             non-black stock-backdrop pixel underneath (a baked star) are replaced.
#   'art'   - not re-baked here at all: the bank is re-set by tools/logo_art.py (the title of
#             the live menu, 10 Sep 2026). The DC mark stays the original pixel art on black:
#             two re-renders of it were rejected by the maintainer the same day.
SCREENS = {
    'bintroe':  dict(gif='INTRG.GIF', logos={b'DCUK': 'black', b'DCUT': 'art'}),
    'introe':   dict(gif='INTRO.GIF', logos={b'DCSS': 'black'}),
    'buttonse': dict(gif='INTRO.GIF', logos={}),
    'dintroe':  dict(gif='INTRO.GIF', logos={}),
}
LOGOS = (b'DCSS', b'DCUK')      # the DC logo: centred, moves with the cluster, sits above the title
# The logo is an opaque 310x120 rectangle on black. With the cluster's proportional shift alone
# (+173 at 768 rows) its top rows would clip the faint tail of the crescent (rows 173..190 hold
# a few hundred (27,7,0) pixels), so a screen with a logo shifts a little further, onto rows
# that are entirely black. patch_resolution.py adds the same amount to the credits box.
LOGO_CLEARANCE = 20
# Council Wars only (maintainer, 24 Sep 2026: "move DC logo, DARK COLONY logo, credentials and buttons
# block 15 points higher for resolutions except 640x480"): the whole main-menu cluster of the exp/
# override scripts is lifted by MENU_LIFT at the HD sizes - as far as the opaque DC logo stays below
# the painted crescent's tail, which ends at row CRESCENT_TAIL of the 480-row design over the logo's
# columns (measured on the shipped backdrops: 167 at 720 rows, 184 at 768, 189 at 800; 112 * H/480 =
# 168 / 179 / 187 keeps the logo clear of every pixel brighter than 7/255). At 1280x720 the logo
# already touches the tail, so the lift is 0 there; at 640x480 the rule gives 0 as well (stock
# backdrop, logo drawn over the planet). patch_resolution.py (credits box), build_ozi_overlay.py
# (button block cap) and the patcher's Get-MenuLift use this same function.
MENU_LIFT = 15
CRESCENT_TAIL = 112
CW_CLUSTER_CENTRE = 296          # exp/intrface/bintroe: title, credits and buttons span rows 159..433


def cw_menu_lift(height):
    """Rows the Council Wars HD menu cluster moves up: MENU_LIFT, or less where the DC logo would
    reach the crescent (0 at 1280x720 and at the stock size)."""
    logo_top = int(round(CW_CLUSTER_CENTRE * (height / STOCK_H - 1))) + LOGO_CLEARANCE   # layout_for's dy
    return max(0, min(MENU_LIFT, logo_top - int(round(CRESCENT_TAIL * height / STOCK_H)) - 1))


BUTTON_SPRITES = (b'LARGEBUTTON', b'MEDBUTTON')
PLATE_MIN_AGREE = 5
STAR_MAX_PX = 12
PIECE_MIN_PX = 100

# main.c bintro creates the scrolling credits TTY (280x100) at an imm32 x/y (doc 10.7):
# Classic (178, 200), Council Wars DCEXP16 (178, 230) with its own exp/intrface/credits.txt. It belongs to
# the cluster and moves with it; the numbers are patched by patch_resolution.py.
# Council Wars' 230 went with its own four-row menu (buttons from y=340); since 23 Sep 2026 its
# script is Classic's 2x4 grid, lowered by 16 px to y=330 so that the box (230..329 in the
# *unpatched* exe, where the immediate cannot be changed) no longer overlaps the top button row
# (doc 10.35). The PATCHED menu takes a fifth row and two half-button-height gaps above the grid
# (build_ozi_overlay.menu_layout), so wherever the exe IS patched the box sits 11 px under the title
# with the rows the block leaves (patch_resolution.cw_credits_height; at 640x480 patch_ozi_menu.py
# removes it, doc 10.36), and at the HD sizes the whole Council Wars cluster - logo, title, box,
# buttons - is cw_menu_lift() rows higher than the letterbox rule alone would put it. 230 and 100
# are what the stock exe holds and what patch_resolution.py / patch_ozi_menu.py overwrite.
CREDITS_W = 280
CREDITS_STOCK_Y = {'classic': 200, 'council wars': 203}
CREDITS_EXE_IMM_Y = {'classic': 200, 'council wars': 230}


def need_np():
    try:
        import numpy as np
        return np
    except ImportError:
        sys.exit('this tool needs NumPy and Pillow: pip install numpy Pillow')


def need_pil():
    try:
        from PIL import Image
        return Image
    except ImportError:
        sys.exit('this tool needs NumPy and Pillow: pip install numpy Pillow')


_MARS_MAPS = None


def mars_maps(np):
    """(height metres float32, colour 0..1 float32) from tools/mars/ (mars_maps.py prepare)."""
    global _MARS_MAPS
    if _MARS_MAPS is None:
        import mars_maps as mm
        try:
            hgt, rgb, _ = mm.load_maps()
        except (OSError, KeyError) as e:
            raise SystemExit('the Mars surface maps are missing (%s): run tools/mars_maps.py prepare, '
                             'see its docstring for the sources' % e)
        _MARS_MAPS = (hgt, rgb)
    return _MARS_MAPS


# ------------------------------------------------------------------ noise
def value_noise(np, rng, shape, cells):
    """Smooth value noise over `shape` with about `cells` lattice cells across the width."""
    h, w = shape
    gy, gx = max(2, int(round(cells * h / w)) + 1), int(cells) + 1
    grid = rng.random((gy + 1, gx + 1))
    ys = np.linspace(0, gy - 1e-6, h)
    xs = np.linspace(0, gx - 1e-6, w)
    y0 = np.floor(ys).astype(int)
    x0 = np.floor(xs).astype(int)
    fy = ys - y0
    fx = xs - x0
    fy = fy * fy * fy * (fy * (fy * 6 - 15) + 10)     # quintic smoothstep
    fx = fx * fx * fx * (fx * (fx * 6 - 15) + 10)
    a = grid[y0][:, x0]
    b = grid[y0][:, x0 + 1]
    c = grid[y0 + 1][:, x0]
    d = grid[y0 + 1][:, x0 + 1]
    top = a + (b - a) * fx[None, :]
    bot = c + (d - c) * fx[None, :]
    return top + (bot - top) * fy[:, None]


def fbm(np, rng, shape, cells, octaves, gain=0.5, ridged=False):
    out = np.zeros(shape)
    amp, total = 1.0, 0.0
    for _ in range(octaves):
        n = value_noise(np, rng, shape, cells)
        if ridged:
            n = 1.0 - abs(2 * n - 1)
        out += amp * n
        total += amp
        amp *= gain
        cells *= 2
    return out / total


# ------------------------------------------------------------------ painting
def sun_direction():
    """Unit vector towards the sun in view space (x right, y down, z towards the viewer). The sun
    sits above and behind the planet, at the elevation that puts the terminator LIT_DEPTH_PX inside
    the limb on the centre line - the depth measured in the stock picture: with d = depth / R the
    terminator (n.L = 0) at ny = -(1-d), nz = sqrt(2d-d^2) gives tan(beta) = sqrt(2d-d^2) / (1-d),
    32.3 degrees for 45 px of 291.4."""
    d = LIT_DEPTH_PX / (PLANET_R * STOCK_H)
    beta = math.atan2(math.sqrt(2 * d - d * d), 1 - d)
    return beta, (0.0, -math.sin(beta), -math.cos(beta))


def paint(np, width, height, seed):
    """Float RGB (0..1) canvas plus the boolean planet-disc mask.

    The realistic rendering of 1 Oct 2026 (doc 10.56 / 10.57): the stock geometry - circle, lit
    depth, glow extent, star density - with a physically shaded sphere instead of the stock's
    painted gradient. Sun direction from sun_direction(); the surface is the REAL one: the MOLA
    elevation grid gives the relief (finite differences along the sphere, exaggerated MARS_RELIEF
    times, bump-mapped) and the Viking colour mosaic the albedo (tools/mars/, mars_maps.py), the
    planet oriented by MARS_TILT / MARS_LON0; lit with a Lambert term steepened near the
    terminator (rough-surface shadowing) and kept inside the flat sphere's terminator envelope, so
    no ridge lights up beyond it (the menu's DC logo rows below CRESCENT_TAIL stay black, see
    crescent_tail_row); a thin dust haze brightens the lit rim and glows outside the limb in two
    layers. The first realistic render (the morning of 1 Oct) used a procedural cratered surface
    in a texture parametrised about the view axis; the maintainer: "planet is looking like an
    asteroid" - hence the real maps. Stars: a magnitude-like brightness distribution, colour
    temperatures from blue-white to orange, point-source sizes that do not grow with the canvas,
    a soft halo on the bright ones."""
    rng = np.random.default_rng(seed)
    s = height / STOCK_H                        # 1.0 at 640x480, 1.6 at 1024x768
    cx, cy, R = PLANET_CX * width, PLANET_CY * height, PLANET_R * height
    if abs(width / height - 4 / 3) > 1e-3:
        cx = width / 2                          # other aspects: centre the planet
    yy, xx = np.mgrid[0:height, 0:width].astype(float)
    dx, dy = xx + 0.5 - cx, yy + 0.5 - cy
    dist = np.sqrt(dx * dx + dy * dy)
    disc = dist <= R
    rgb = np.zeros((height, width, 3))

    # ---- stars: everything outside the disc
    sky_px = int((~disc).sum())
    n_stars = int(sky_px * STAR_DENSITY)
    sx = rng.random(n_stars) * width
    sy = rng.random(n_stars) * height
    u = rng.random(n_stars)
    bright = np.minimum(0.075 * (1 - u) ** -0.85, 1.5)     # many faint, a heavy bright tail
    t = rng.random(n_stars)
    tint = np.ones((n_stars, 3))
    tint[t < 0.18] = (0.78, 0.86, 1.00)                 # blue-white
    tint[(t >= 0.18) & (t < 0.30)] = (0.90, 0.94, 1.00)
    tint[(t >= 0.55) & (t < 0.75)] = (1.00, 0.95, 0.84)  # yellow-white
    tint[(t >= 0.75) & (t < 0.88)] = (1.00, 0.86, 0.66)  # orange
    tint[t >= 0.96] = (1.00, 0.70, 0.55)                # red
    sigma = 0.45 + 0.40 * np.minimum(bright, 1.0) + rng.random(n_stars) * 0.12
    order = np.argsort(bright)                  # bright ones last so they win overlaps
    pad = 6
    for i in order:
        x, y, b, sg = sx[i], sy[i], bright[i], sigma[i]
        ix, iy = int(x), int(y)
        x0, x1 = max(0, ix - pad), min(width, ix + pad + 1)
        y0, y1 = max(0, iy - pad), min(height, iy + pad + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        py, px = np.mgrid[y0:y1, x0:x1].astype(float)
        d2 = (px + 0.5 - x) ** 2 + (py + 0.5 - y) ** 2
        blob = np.exp(-d2 / (2 * sg * sg))
        blob *= b / blob.max()                  # the nearest pixel carries the full brightness
        patch = blob[:, :, None] * tint[i][None, None, :]
        if b > 0.8:                             # bright stars: a soft wider halo
            halo = 0.10 * b * np.exp(-d2 / (2 * 2.2 ** 2))
            patch += halo[:, :, None] * tint[i][None, None, :]
        rgb[y0:y1, x0:x1] += patch
    rgb[disc] = 0.0                             # the planet occludes the stars

    # ---- planet: a sphere, the sun above and behind it (sun_direction), the REAL surface
    # (doc 10.57): MOLA heights and the Viking colour mosaic (tools/mars_maps.py) wrapped on it
    _, L = sun_direction()
    L = np.array(L)
    rho = np.clip(dist / R, 0, 1)
    nx, ny = dx / R, dy / R
    nz = np.sqrt(np.clip(1 - rho * rho, 0, 1))
    nvec = np.stack([nx, ny, nz], -1)
    hgt_map, col_map = mars_maps(np)
    # view -> planet frame: tilt the pole towards the viewer by MARS_TILT (about the screen x
    # axis), then turn the planet by MARS_LON0 about its own axis (which longitude faces the top
    # of the limb). Screen y points down, so "up" is -y.
    tilt = math.radians(MARS_TILT)
    roll = math.radians(MARS_ROLL)
    up0 = -ny
    nx_r = nx * math.cos(roll) - up0 * math.sin(roll)        # roll the axis about the view direction
    up = nx * math.sin(roll) + up0 * math.cos(roll)
    px_ = nx_r
    py_ = up * math.cos(tilt) - nz * math.sin(tilt)          # planet "up" (towards the north pole)
    pz_ = up * math.sin(tilt) + nz * math.cos(tilt)          # planet "towards the viewer" meridian
    lat = np.arcsin(np.clip(py_, -1, 1))
    lon = (np.arctan2(px_, pz_) + math.radians(MARS_LON0)) % (2 * math.pi)
    th, tw = hgt_map.shape
    # bilinear samples of height (metres) and colour; the maps run 0..360 east, +90 at the top
    fx = lon / (2 * math.pi) * tw - 0.5
    fy = (0.5 - lat / math.pi) * th - 0.5
    x0 = np.floor(fx).astype(int)
    y0 = np.clip(np.floor(fy).astype(int), 0, th - 2)
    wx = (fx - x0)[..., None]
    wy = (fy - y0)[..., None]
    x0 %= tw
    x1 = (x0 + 1) % tw

    def sample(m):
        m = m if m.ndim == 3 else m[..., None]
        a = m[y0, x0] * (1 - wx) + m[y0, x1] * wx
        b = m[y0 + 1, x0] * (1 - wx) + m[y0 + 1, x1] * wx
        return a * (1 - wy) + b * wy

    H = sample(hgt_map)[..., 0]
    C = sample(col_map)
    # relief: finite differences of the height along the sphere (metres per metre), exaggerated
    # MARS_RELIEF times; the tangent frame is the planet's east / north at the pixel
    Rm = MARS_RADIUS_M
    dlon = 2 * math.pi / tw
    dlat = math.pi / th
    hx = (np.roll(hgt_map, -1, 1) - np.roll(hgt_map, 1, 1)) / (2 * dlon)      # dh / dlon
    hy = -(np.roll(hgt_map, -1, 0) - np.roll(hgt_map, 1, 0)) / (2 * dlat)     # dh / dlat (rows run south)
    for _ in range(SLOPE_SMOOTH):                   # soften the one-texel edges of the relief
        hx = (hx + np.roll(hx, 1, 1) + np.roll(hx, -1, 1) + np.roll(hx, 1, 0) + np.roll(hx, -1, 0)) / 5
        hy = (hy + np.roll(hy, 1, 1) + np.roll(hy, -1, 1) + np.roll(hy, 1, 0) + np.roll(hy, -1, 0)) / 5
    slope_e = sample(hx)[..., 0] / (Rm * np.maximum(np.cos(lat), 0.05)) * MARS_RELIEF
    slope_n = sample(hy)[..., 0] / Rm * MARS_RELIEF
    # east and north unit tangents in the planet frame, rotated back into view space
    east_p = np.stack([np.cos(lon - math.radians(MARS_LON0)), np.zeros_like(lon),
                       -np.sin(lon - math.radians(MARS_LON0))], -1)
    north_p = np.stack([-np.sin(lat) * np.sin(lon - math.radians(MARS_LON0)), np.cos(lat),
                        -np.sin(lat) * np.cos(lon - math.radians(MARS_LON0))], -1)

    def to_view(v):          # planet frame (x, up, towards viewer) -> view frame (x, y down, z)
        vx, vu, vz = v[..., 0], v[..., 1], v[..., 2]
        up_v = vu * math.cos(tilt) + vz * math.sin(tilt)
        z_v = -vu * math.sin(tilt) + vz * math.cos(tilt)
        x_v = vx * math.cos(roll) + up_v * math.sin(roll)    # undo the roll
        up_v = -vx * math.sin(roll) + up_v * math.cos(roll)
        return np.stack([x_v, -up_v, z_v], -1)

    npert = nvec - slope_e[..., None] * to_view(east_p) - slope_n[..., None] * to_view(north_p)
    npert /= np.linalg.norm(npert, axis=-1, keepdims=True) + 1e-12
    nl_flat = (nvec * L).sum(-1)
    nl = (npert * L).sum(-1)
    # a slope may darken freely (shadow) but brighten only SLOPE_LIGHT_CAP above the flat terrain
    # (maintainer: "edges of obstacles are too bright")
    nl = np.minimum(nl, nl_flat + SLOPE_LIGHT_CAP)
    # steeper than Lambert near the terminator (shadowing), inside the flat terminator envelope
    diff = np.clip(nl, 0, 1) ** LAMBERT_POWER * np.clip(nl_flat / ENVELOPE_NL, 0, 1) ** ENVELOPE_POWER
    sun_col = np.array([1.0, 0.68, 0.45])        # a low, dust-reddened sun
    # the mosaic's brightness in the menu's orange (MARS_TINT, the stock crescent's hue) plus a
    # share of the mosaic's own chroma for the dark / light albedo regions (maintainer, after the
    # first real-map render: "make it warmer, more orange like before")
    grey = C.mean(-1, keepdims=True)
    base_col = np.clip(grey ** 0.85 * np.array(MARS_TINT)[None, None, :] + (C - grey) * 1.2, 0, 1)
    body = base_col * (diff[:, :, None] * sun_col[None, None, :]) * BODY_GAIN
    twilight = 0.02 * np.exp(-np.clip(-nl_flat, 0, None) / 0.002) * (nl_flat < 0.02)
    body += twilight[:, :, None] * np.array([1.0, 0.45, 0.2])[None, None, :] * grey
    depth_in = np.clip(R - dist, 0, None) / s   # px inside the limb, in 480-row units
    lit_angle = np.clip(-ny, 0, 1)              # 1 at the top of the limb
    rim = np.exp(-depth_in / 3.5) * lit_angle ** 1.3        # dust haze brightens the lit rim
    body += 0.30 * rim[:, :, None] * np.array([1.0, 0.72, 0.50])[None, None, :]
    body = np.clip(body, 0, 1)
    body[~disc] = 0
    rgb[disc] = body[disc]

    # ---- atmosphere outside the limb: a dense warm lower haze and a thin paler upper one, both
    # fading with the angle from the top like the stock glow (GLOW_PX sets the visible extent)
    depth_out = np.clip(dist - R, 0, None) / s
    glow_fade = lit_angle ** 1.6
    g1 = 0.50 * np.exp(-depth_out / (GLOW_PX * 0.26)) * glow_fade
    g2 = 0.16 * np.exp(-depth_out / (GLOW_PX * 0.60)) * glow_fade
    g1[disc] = 0
    g2[disc] = 0
    rgb += g1[:, :, None] * np.array([1.0, 0.52, 0.22])[None, None, :]
    rgb += g2[:, :, None] * np.array([0.95, 0.62, 0.45])[None, None, :]
    return np.clip(rgb, 0, 1), disc


BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def quantise(np, rgb, palette, dither_mask=None, amp=DITHER_AMP):
    """Nearest palette colour per pixel; index 0 is never used (sprite-transparent black). Where
    dither_mask is set an ordered 4x4 Bayer pattern of +-amp/255 is added first, so the planet's
    smooth shading dithers between the palette's orange, brown and tan entries instead of
    banding; the sky and the stars are never dithered."""
    h, w, _ = rgb.shape
    pal = np.array(palette, float).reshape(256, 3)
    cand = np.arange(1, 256)
    pc = pal[cand]
    src = rgb * 255.0
    if dither_mask is not None:
        tile = np.tile((np.array(BAYER4, float) / 16.0 - 0.5), (h // 4 + 1, w // 4 + 1))[:h, :w]
        src = src + (tile * amp * dither_mask)[:, :, None]
    out = np.zeros((h, w), np.uint8)
    step = max(1, 2_000_000 // (len(cand) * w))
    for y0 in range(0, h, step):
        chunk = src[y0:y0 + step].reshape(-1, 1, 3)
        d = ((chunk - pc[None, :, :]) ** 2).sum(-1)
        out[y0:y0 + step] = cand[d.argmin(1)].reshape(-1, w)
    return out


_SCENE_CACHE = {}


def scene(np, width, height, seed, palette):
    """The quantised backdrop (index array) of one size, without any bottom band: painted once per
    (size, seed, palette) and shared by INTRG.GIF, INTRO.GIF and BACKDROP.GIF."""
    key = (width, height, seed, bytes(palette[:768]))
    if key not in _SCENE_CACHE:
        rgb, disc = paint(np, width, height, seed)
        s = height / STOCK_H
        cx, cy, R = PLANET_CX * width, PLANET_CY * height, PLANET_R * height
        if abs(width / height - 4 / 3) > 1e-3:
            cx = width / 2
        yy, xx = np.mgrid[0:height, 0:width].astype(float)
        dist = np.sqrt((xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2)
        # dither the planet and its glow only, and only where there is light to dither
        mask = (disc | (dist <= R + GLOW_PX * s * 2.5)).astype(float)
        mask[rgb.max(-1) < DITHER_MIN / 255.0] = 0
        _SCENE_CACHE[key] = quantise(np, rgb, palette, dither_mask=mask)
    return _SCENE_CACHE[key].copy()


def crescent_tail_row(np, idx, palette, width, height):
    """Last row (0-based) over the DC logo's columns (LOGO_W px centred) that holds a pixel brighter
    than 7/255, looking at the upper 60 % of the picture - the measurement behind CRESCENT_TAIL and
    cw_menu_lift. The limit for a size is round(CRESCENT_TAIL * height / 480) - 1."""
    pal = np.array(palette).reshape(256, 3)
    lx0 = (width - LOGO_W) // 2
    bright = pal[idx[:, lx0:lx0 + LOGO_W]].max(-1) > 7
    rows = np.where(bright.any(1))[0]
    rows = rows[rows < height * 0.6]
    return int(rows.max()) if len(rows) else -1


def render(stock_path, width, height, seed, band=True):
    """Index array (height x width, uint8) plus the stock palette (768 ints). With `band` the
    bottom band of the stock file (logo, rule, copyright) is copied in 1:1; without it the result
    is the bare backdrop (BACKDROP.GIF, the pre-battle screens' ground)."""
    np = need_np()
    Image = need_pil()
    with Image.open(stock_path) as st:
        if st.mode != 'P' or st.size != (STOCK_W, STOCK_H):
            raise ValueError('%s: expected the stock 640x480 paletted INTRO.GIF' % stock_path)
        palette = st.getpalette()
        stock = np.array(st)
    idx = scene(np, width, height, seed, palette)
    tail = crescent_tail_row(np, idx, palette, width, height)
    limit = int(round(CRESCENT_TAIL * height / STOCK_H)) - 1
    if tail > limit:
        raise ValueError('%dx%d: the crescent reaches row %d over the logo columns, limit %d (CRESCENT_TAIL)'
                         % (width, height, tail, limit))
    if not band:
        return idx, palette

    # bottom band, 1:1 from the stock file, same distance from the bottom edge, centred
    ox, oy = (width - STOCK_W) // 2, height - STOCK_H
    bandpx = stock[BAND_TOP:BAND_BOTTOM]
    for r in range(BAND_TOP, BAND_BOTTOM):
        row = stock[r]
        if (row != 254).sum() >= RULE_MIN_PX:               # a rule: solid across the full width
            vals, counts = np.unique(row[:200], return_counts=True)
            idx[r + oy, :] = vals[counts.argmax()]
    region = idx[BAND_TOP + oy:BAND_BOTTOM + oy, ox:ox + STOCK_W]
    region[bandpx != 254] = bandpx[bandpx != 254]                 # logo, rule detail, copyright
    return idx, palette


def save_gif(idx, palette, path):
    Image = need_pil()
    im = Image.fromarray(idx, 'P')
    im.putpalette(palette)
    im.save(path, format='GIF', version='GIF87a', interlace=False, optimize=False)
    bad = check_gif_layout(path)
    if bad:
        raise ValueError('%s: %s' % (path, bad))
    d = open(path, 'rb').read()
    if list(d[13:13 + 768]) != list(palette):
        raise ValueError('%s: colour table was not preserved' % path)


# ------------------------------------------------------------------ the DC logo sprite
def arc_family(np, palette):
    """Palette indices of the limb's colours: orange, red and brown ramps, no greys/pinks."""
    pal = np.array(palette).reshape(256, 3).astype(int)
    r, g, b = pal[:, 0], pal[:, 1], pal[:, 2]
    # (7,0,0) and (11,7,0), the darkest glow steps, must be in: g/b at most 0.65 r
    fam = (r > 0) & (g <= r * 0.65) & (b <= r * 0.65)
    fam[0] = False
    return fam


def label_components(np, mask):
    """8-connected component labels (0 = not in mask) and the component sizes (index label-1)."""
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    sizes = []
    n = 0
    ys, xs = np.nonzero(mask)
    for y, x in zip(ys.tolist(), xs.tolist()):
        if lab[y, x]:
            continue
        n += 1
        lab[y, x] = n
        stack = [(y, x)]
        size = 0
        while stack:
            cy, cx = stack.pop()
            size += 1
            for ny in (cy - 1, cy, cy + 1):
                if ny < 0 or ny >= h:
                    continue
                for nx in (cx - 1, cx, cx + 1):
                    if 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = n
                        stack.append((ny, nx))
        sizes.append(size)
    return lab, np.array(sizes, int)


def dilate(np, mask, r=1):
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy or dx:
                out |= np.roll(np.roll(mask, dy, 0), dx, 1)
    return out


def mode_over_frames(np, stack):
    """Per-pixel most frequent value across frames and how many frames carry it."""
    best = np.zeros(stack.shape[1:], np.uint8)
    cnt = np.zeros(stack.shape[1:], int)
    for v in np.unique(stack):
        c = (stack == v).sum(0)
        upd = c > cnt
        best[upd] = v
        cnt[upd] = c[upd]
    return best, cnt


def rebake_logo(spr_src, spr_dst, new_idx, palette, logo_xy, mode='full', stock_idx=None,
                stock_xy=None):
    """Replace the baked backdrop in every cell of a logo sprite with `new_idx` (the new
    background's index array) at `logo_xy`; see SCREENS for the two modes. Returns (cells,
    replaced px per cell)."""
    np = need_np()
    from spr import read_spr, write_spr
    s = read_spr(spr_src)
    cells = [np.frombuffer(bytes(c['px']), np.uint8).reshape(c['h'], c['w']) for c in s['cells']]
    hmax = max(c.shape[0] for c in cells)
    w = cells[0].shape[1]
    if mode in ('full', 'black'):
        same = [c for c in cells if c.shape[0] == hmax]
        plate, agree = mode_over_frames(np, np.stack(same))
        fam = arc_family(np, palette)
    ref = ref_pieces = ref_colours = None
    if mode == 'black':
        # the reference frame: the least logo (non-zero, non-limb pixels), not a solid flash
        cands = [c for c in same if len(np.unique(c)) > 1]
        ref = min(cands, key=lambda c: int(((c != 0) & ~fam[c]).sum()))
        lab, sizes = label_components(np, (ref != 0) & ~fam[ref])
        big = np.zeros(len(sizes) + 1, bool)
        big[1:] = sizes >= PIECE_MIN_PX
        ref_pieces = dilate(np, big[lab], 1)
        ref_colours = set(np.unique(ref[(ref != 0) & ~ref_pieces]).tolist())
    lx, ly = logo_xy
    newbg = new_idx[ly:ly + hmax, lx:lx + w].copy()
    newbg[newbg == 254] = 0                     # the sprite's black is index 0
    if mode == 'black':
        newbg[:] = 0
    out_cells, replaced = [], []
    for c, meta in zip(cells, s['cells']):
        h = c.shape[0]
        if mode == 'stars':
            sx, sy = stock_xy
            under = stock_idx[sy:sy + h, sx:sx + w]
            bg = (c == under) & (under != 254)
        else:
            bg = ((c == plate[:h]) & (agree[:h] >= PLATE_MIN_AGREE)) | fam[c]
            if mode == 'black':
                bg |= (c == ref[:h]) & (ref[:h] != 0) & ~ref_pieces[:h]
            other = (c != 0) & ~bg
            lab, sizes = label_components(np, other)
            if len(sizes):
                small = np.zeros(len(sizes) + 1, bool)
                small[1:] = sizes <= STAR_MAX_PX
                bg |= small[lab]
                if mode == 'black':
                    bigm = np.zeros(len(sizes) + 1, bool)
                    bigm[1:] = sizes >= PIECE_MIN_PX
                    pieces = bigm[lab]
                    piece_colours = set(np.unique(c[pieces]).tolist()) - ref_colours
                    for i, sz in enumerate(sizes):
                        if sz >= PIECE_MIN_PX or sz <= STAR_MAX_PX:
                            continue
                        comp = lab == i + 1
                        if np.isin(c[comp], list(piece_colours)).mean() < 0.5:
                            bg |= comp                      # not logo-coloured: a star or glow
            logo = (c != 0) & ~bg
            bg |= (c == 0) & ~dilate(np, logo, 1)   # sky, but keep the letters' black shadows
        out = np.where(bg, newbg[:h], c).astype(np.uint8)
        out_cells.append(dict(w=meta['w'], h=meta['h'], ox=meta['ox'], oy=meta['oy'],
                              px=bytearray(out.tobytes())))
        replaced.append(int(bg.sum()))
    write_spr(spr_dst, s['flags'], out_cells, s['palette'])
    return out_cells, replaced


# ------------------------------------------------------------------ scripts
def find_ci(directory, name):
    for fn in os.listdir(directory):
        if fn.lower() == name.lower():
            return os.path.join(directory, fn)
    return None


def intrface_dir(game_dir):
    d = find_ci(game_dir, 'INTRFACE')
    if not d or not os.path.isdir(d):
        raise SystemExit('%s: no INTRFACE folder' % game_dir)
    return d


def script_paths(game_dir):
    """[(path, screen-key, is_override)] for every script of SCREENS present in INTRFACE and in
    Council Wars' exp/intrface."""
    out = []
    idir = intrface_dir(game_dir)
    dirs = [(idir, False)]
    exp = find_ci(game_dir, 'exp')
    if exp:
        ei = find_ci(exp, 'intrface')
        if ei:
            dirs.append((ei, True))
    for d, override in dirs:
        for name in SCREENS:
            p = find_ci(d, name)
            if p and not p.lower().endswith('.bak'):
                out.append((p, name, override))
    return out


def pristine(path):
    """The stock text of a script: its .bak if we (or pad_background) made one, else the file
    itself when it is still `size 640 480`, else the letterboxed file un-shifted."""
    if os.path.exists(path + '.bak'):
        return open(path + '.bak', 'rb').read()
    data = open(path, 'rb').read()
    if SIZE2.search(data):
        return data
    m = re.search(rb'^[ \t]*size[ \t]+(\d+)[ \t]+(\d+)[ \t]+(\d+)[ \t]+(\d+)', data, re.M)
    if m and (int(m.group(3)), int(m.group(4))) == (STOCK_W, STOCK_H):
        x, y = int(m.group(1)), int(m.group(2))
        data, _ = relayout(data, lambda k, n, wx, wy, ww, wh, rest: (wx - x, wy - y),
                           size_line=b'size %d %d' % (STOCK_W, STOCK_H))
        return data
    raise ValueError('%s: cannot recover the stock layout' % path)


def _positioned(words):
    """`<kind> <n> <desc> <x> <y> [<w> <h>] ...` -> (kind, n, x, y, w, h, rest) or None. The
    w/h are 0 when the line has none (BUTTONSE's `picture 5 0 0 70 10000` has a sprite there)."""
    if len(words) < 5 or words[0].lower() not in POSITIONED:
        return None
    if not all(re.fullmatch(rb'-?\d+', t) for t in words[1:5]):
        return None
    w = h = 0
    rest = words[5:]
    if len(words) >= 7 and all(re.fullmatch(rb'\d+', t) for t in words[5:7]):
        w, h, rest = int(words[5]), int(words[6]), words[7:]
    return (words[0].lower(), int(words[1]), int(words[3]), int(words[4]), w, h, rest)


def parse_widgets(data):
    """(kind, number, x, y, w, h, rest-tokens) for every positioned widget line."""
    out = []
    for ln in data.split(b'\n'):
        p = _positioned(ln.partition(b'%')[0].split())
        if p:
            out.append(p)
    return out


def relayout(data, move, size_line):
    """Rewrite every positioned widget's x, y through move(kind, n, x, y, w, h, rest) -> (x, y),
    preserving the line's spacing, and replace the size line. Returns (data, count)."""
    out, moved = [], 0
    for ln in data.split(b'\n'):
        body, sep, comment = ln.partition(b'%')
        toks = re.findall(rb'\S+|[ \t]+', body)
        words = [t for t in toks if not t.isspace()]
        p = _positioned(words)
        if p:
            nx, ny = move(*p)
            n = 0
            for i, t in enumerate(toks):
                if t.isspace():
                    continue
                n += 1
                if n == 4:
                    toks[i] = b'%d' % nx
                elif n == 5:
                    toks[i] = b'%d' % ny
                    break
            body = b''.join(toks)
            moved += 1
        out.append(body + sep + comment)
    data = b'\n'.join(out)
    m = SIZE2.search(data) or SIZE4.search(data)
    if not m:
        raise ValueError('no size line')
    lead = re.match(rb'[ \t]*', m.group(0)).group(0)
    data = data[:m.start()] + lead + size_line + data[m.end():]
    return data, moved


def is_logo(kind, rest):
    return kind == b'gadget' and bool(rest) and rest[0] in LOGOS


def is_title(kind, rest):
    return kind == b'gadget' and bool(rest) and rest[0] not in BUTTON_SPRITES + LOGOS


def layout_for(data, width, height, lift=0):
    """The move() function for one stock script. Title, credits and buttons are one cluster
    that keeps its stock vertical centre as a fraction of the height (dy); the button grid is
    centred horizontally as a unit (dx), the title gadget (DCUT) on its own. The DC logo
    (DCSS/DCUK) is centred and moves by the same dy, so it stays right above the title with the
    stock gap (the maintainer's requirement, 10 Sep 2026). `lift` rows are taken off dy (the
    Council Wars overrides: cw_menu_lift). Returns (move, dx, dy)."""
    widgets = parse_widgets(data)
    cluster = [w for w in widgets if not is_logo(w[0], w[6])]
    if not cluster:
        raise ValueError('no widgets besides the logo')
    y0 = min(w[3] for w in cluster)
    y1 = max(w[3] + w[5] for w in cluster)
    dy = int(round((y0 + y1) / 2 * (height / STOCK_H - 1)))
    if any(is_logo(w[0], w[6]) for w in widgets):
        dy += LOGO_CLEARANCE
    dy -= lift
    grid = [w for w in cluster if not is_title(w[0], w[6])]
    x0 = min(w[2] for w in grid)
    x1 = max(w[2] + w[4] for w in grid)
    dx = int(round(width / 2 - (x0 + x1) / 2))

    def move(kind, n, x, y, w, h, rest):
        if is_logo(kind, rest) or is_title(kind, rest):
            return (width - w) // 2, y + dy
        return x + dx, y + dy
    return move, dx, dy


def logo_positions(data, move):
    """{sprite name: (stock xy, new xy, w, h)} for the logo/title gadgets of a stock script."""
    out = {}
    for kind, n, x, y, w, h, rest in parse_widgets(data):
        if kind == b'gadget' and rest and (is_logo(kind, rest) or is_title(kind, rest)):
            out[rest[0]] = ((x, y), move(kind, n, x, y, w, h, rest), w, h)
    return out


def apply_scripts(game_dir, width, height, dry_run=False):
    for path, key, override in script_paths(game_dir):
        data = pristine(path)
        lift = cw_menu_lift(height) if override else 0      # the Council Wars menu sits higher
        move, dx, dy = layout_for(data, width, height, lift)
        new, moved = relayout(data, move, b'size %d %d' % (width, height))
        rel = os.path.relpath(path, game_dir)
        if not dry_run:
            if not os.path.exists(path + '.bak'):
                shutil.copy2(path, path + '.bak')
            open(path, 'wb').write(new)
        print('%s %-26s %d widget(s): cluster and logo +(%d,%d)%s, centred, size %d %d'
              % ('  plan ' if dry_run else 'script', rel, moved, dx, dy,
                 ' (incl. the Council Wars lift of %d)' % lift if lift else '', width, height))
        if key == 'bintroe':
            build = 'council wars' if override else 'classic'
            sy = CREDITS_STOCK_Y[build]
            print('        credits box (%s exe, imm32 (178,%d)) must be at (%d,%d): '
                  'patch_resolution.py stage 2 fixups 0x42A0 / 0x4299'
                  % (build, CREDITS_EXE_IMM_Y[build], (width - CREDITS_W) // 2, sy + dy))


def stock_gif(idir, name):
    gif = find_ci(idir, name)
    if not gif:
        raise SystemExit('%s: no %s' % (idir, name))
    Image = need_pil()
    if os.path.exists(gif + '.bak'):
        return gif, gif + '.bak'
    with Image.open(gif) as im:
        if im.size == (STOCK_W, STOCK_H):
            return gif, gif
    raise SystemExit('%s is not 640x480 and has no .bak: cannot find the stock file' % gif)


SPRITE_DIRS = ('INTRFACE', 'SPRITES')


def logo_sprs(game_dir, name):
    """[(path, stock path)] of every copy of a logo sprite bank (the stock copy is the .bak once
    we rebaked). The game draws the animation's frames from SPRITES/; INTRFACE/ holds the same
    file and is kept in step."""
    out = []
    for d in SPRITE_DIRS:
        folder = find_ci(game_dir, d)
        spr = find_ci(folder, name.decode() + '.SPR') if folder else None
        if spr:
            out.append((spr, spr + '.bak' if os.path.exists(spr + '.bak') else spr))
    if not out:
        raise SystemExit('%s: no %s.SPR in %s' % (game_dir, name.decode(), '/'.join(SPRITE_DIRS)))
    return out


def logo_jobs(game_dir, width, height):
    """{sprite name: (mode, gif name, stock xy, new xy)} from the base INTRFACE scripts (the
    exp/ overrides draw the same sprites at the same places)."""
    jobs = {}
    for path, key, override in script_paths(game_dir):
        if override:
            continue
        data = pristine(path)
        move, _, _ = layout_for(data, width, height)
        for name, (stock_xy, new_xy, w, h) in logo_positions(data, move).items():
            mode = SCREENS[key]['logos'].get(name)
            if mode is None:
                raise ValueError('%s: gadget %s is not in SCREENS' % (path, name.decode()))
            if mode == 'art':
                continue                        # logo_art.py owns this bank
            jobs[name] = (mode, SCREENS[key]['gif'], stock_xy, new_xy)
    return jobs


# ------------------------------------------------------------------ commands
def cmd_render(args):
    idx, palette = render(args.stock, args.width, args.height, args.seed, band=not args.no_band)
    save_gif(idx, palette, args.out)
    print('wrote %s (%dx%d, seed %d%s)' % (args.out, args.width, args.height, args.seed,
                                           ', no bottom band' if args.no_band else ''))


def gifs_needed(game_dir):
    return sorted({SCREENS[key]['gif'] for _, key, _ in script_paths(game_dir)})


def cmd_plan(args):
    idir = intrface_dir(args.dir)
    for name in gifs_needed(args.dir):
        gif, stock = stock_gif(idir, name)
        print('%-10s -> %dx%d procedural repaint from %s' % (name, args.width, args.height,
                                                            os.path.basename(stock)))
    print('%-10s -> %dx%d the same scene without the bottom band (pre-battle screens)'
          % (BACKDROP_GIF, args.width, args.height))
    for name, (mode, gif, stock_xy, new_xy) in logo_jobs(args.dir, args.width, args.height).items():
        for spr, spr_stock in logo_sprs(args.dir, name):
            print('%-22s -> %s re-bake of all cells: backdrop %s, sprite at (%d,%d) instead of '
                  '(%d,%d), from %s' % (os.path.relpath(spr, args.dir), mode,
                                        'black' if mode == 'black' else 'from ' + gif,
                                        new_xy[0], new_xy[1], stock_xy[0], stock_xy[1],
                                        os.path.basename(spr_stock)))
    apply_scripts(args.dir, args.width, args.height, dry_run=True)
    print('plan only, nothing written.')


def cmd_apply(args):
    np = need_np()
    Image = need_pil()
    idir = intrface_dir(args.dir)
    renders, stocks, palette = {}, {}, None
    for name in gifs_needed(args.dir):
        gif, stock = stock_gif(idir, name)
        if stock == gif:
            shutil.copy2(gif, gif + '.bak')
            stock = gif + '.bak'
        idx, palette = render(stock, args.width, args.height, args.seed)
        save_gif(idx, palette, gif)
        renders[name] = idx
        with Image.open(stock) as im:
            stocks[name] = np.array(im)
        print('painted %-26s %dx%d, seed %d' % (os.path.relpath(gif, args.dir), args.width,
                                                args.height, args.seed))
    # the bare backdrop for the pre-battle screens (pad_background.py lays them over it)
    gif, stock = stock_gif(idir, 'INTRG.GIF')
    bd = os.path.join(idir, BACKDROP_GIF)
    idx, palette = render(stock, args.width, args.height, args.seed, band=False)
    save_gif(idx, palette, bd)
    print('painted %-26s %dx%d, seed %d (no bottom band)' % (os.path.relpath(bd, args.dir),
                                                            args.width, args.height, args.seed))
    for name, (mode, gif, stock_xy, new_xy) in logo_jobs(args.dir, args.width, args.height).items():
        for spr, spr_stock in logo_sprs(args.dir, name):
            if spr_stock == spr:
                shutil.copy2(spr, spr + '.bak')
                spr_stock = spr + '.bak'
            _, replaced = rebake_logo(spr_stock, spr, renders[gif], palette, new_xy, mode,
                                      stocks[gif], stock_xy)
            print('rebaked %-26s %d cells (%s), %d..%d backdrop px each replaced'
                  % (os.path.relpath(spr, args.dir), len(replaced), mode, min(replaced),
                     max(replaced)))
    apply_scripts(args.dir, args.width, args.height)


def cmd_preview(args):
    """Background + DC logo + the LARGEBUTTON cells at the positions the script now uses."""
    np = need_np()
    Image = need_pil()
    from spr import read_spr
    from PIL import ImageDraw
    idir = intrface_dir(args.dir)
    key = args.script.lower()
    gif = find_ci(idir, SCREENS[key]['gif'])
    with Image.open(gif) as im:
        pal = np.array(im.getpalette(), np.uint8).reshape(256, 3)   # the screen palette
        canvas = im.convert('RGB')
    script = find_ci(idir, key)
    data = open(script, 'rb').read()
    for kind, n, x, y, w, h, rest in parse_widgets(data):
        if kind != b'gadget' or not rest:
            continue
        is_logo = rest[0] in SCREENS[key]['logos']
        if not is_logo and rest[0] not in BUTTON_SPRITES:
            continue
        folder = find_ci(args.dir, 'SPRITES') if is_logo else idir
        spr = read_spr(find_ci(folder, (rest[0].decode() if is_logo else 'KNOBE') + '.SPR'))
        cell = spr['cells'][-1 if is_logo else 0]          # a logo's settled last frame
        px = np.frombuffer(bytes(cell['px']), np.uint8).reshape(cell['h'], cell['w'])
        tile = Image.fromarray(pal[px], 'RGB')
        # logos are drawn `unmask` (opaque, their own backdrop); buttons are masked
        mask = None if is_logo else Image.fromarray(((px != 0) * 255).astype(np.uint8), 'L')
        canvas.paste(tile, (x, y), mask)
    if key == 'bintroe':                                   # where the code puts the credits
        _, dx, dy = layout_for(pristine(script), canvas.width, canvas.height)
        cx, cy = (canvas.width - CREDITS_W) // 2, CREDITS_STOCK_Y['classic'] + dy
        ImageDraw.Draw(canvas).rectangle((cx, cy, cx + CREDITS_W - 1, cy + 99),
                                         outline=(0, 90, 160))
    canvas.save(args.out)
    print('wrote %s' % args.out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    r = sub.add_parser('render')
    r.add_argument('out')
    r.add_argument('--stock', required=True, help='a stock 640x480 INTRG.GIF / INTRO.GIF')
    r.add_argument('--no-band', action='store_true',
                   help='the bare backdrop without the bottom band (BACKDROP.GIF)')
    p = sub.add_parser('plan')
    p.add_argument('dir', help='a game directory (DC - Classic, DC - Council wars, ...)')
    a = sub.add_parser('apply')
    a.add_argument('dir')
    v = sub.add_parser('preview')
    v.add_argument('dir')
    v.add_argument('out')
    v.add_argument('--script', default='bintroe', help='bintroe (default) or introe')
    for s in (r, p, a):
        s.add_argument('--width', type=int, default=1024)
        s.add_argument('--height', type=int, default=768)
    for s in (r, a):
        s.add_argument('--seed', type=int, default=7)
    args = ap.parse_args(argv)
    return {'render': cmd_render, 'plan': cmd_plan, 'apply': cmd_apply,
            'preview': cmd_preview}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())

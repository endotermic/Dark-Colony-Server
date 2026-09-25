#!/usr/bin/env python3
"""Install the ozi_ns mission pack (2010) into Council Wars as the "OZI MISSIONS" campaign mode.

Input is the pack folder kept outside the game repository, next to it in `Documents`
(`../OZI_NS/M1PACK` seen from the repo; the base pack's overlay folder merged with its English text
layer).  Output is two things inside the Council Wars folder:

1. Additions to the shared base set `exp/`, because the animation list, every FIN/SPR bank and the
   sound table are loaded once at start-up through the Council Wars prefix (patch_ozi_menu.py
   explains why).  Nothing the ORIGINAL exe reads is modified (maintainer requirement,
   14 Sep 2026): `exp/anim.dat`, `tran.fin`, `tran.spr` stay stock, the patched exe opens
   `exp/animozi.dat` instead (DGROUP string `anim.dat` -> `animozi.dat`, patch_ozi_menu.py):
     - the pack's three new units dalg / spyo / reae (FIN + SPR), listed in `animozi.dat`;
     - the pack's transport as `tranozi.fin` + `tranozi.spr` (bank field renamed; a smoke
       animation and a real sprite for the pack's "transmitter"/"Generator"; no Council Wars or
       Classic balance table uses TRAN), replacing the `tran.fin` line in `animozi.dat`;
     - `grrr.fin` and `troo.fin` left OUT of `animozi.dat` (25 Sep 2026, network compatibility,
       doc 10.39): they are the only stock Council Wars FINs whose animations a Classic unit type
       picks up - GRAYDEPLOY / TRSCDEPLOY, the deploy poses of the Security Trooper, the Gray and
       the eight commanders (types 0, 8, 69-76; the pack's 118/119).  Classic has neither and falls
       back to STAND, and the commander rally waits for that animation: 28 ticks for a Gray
       commander in Council Wars against 2 in Classic, so a mixed network game (and one against the
       relay server's checksums) desynchronises at the first Gray rally.  Nothing else reads them
       (no script, scenario, table or other FIN names GRRR, INSP or the two DEPLOY sets);
       the stock `exp/anim.dat` keeps both lines for the original exe;
     - the main-menu labels 8 "PLAY INTRO" -> "OZI MISSIONS" and 5 "SINGLE PLAYER WAR" ->
       "OZI LOAD" in `exp/intrf_hd/bintroe` (the HD override script; the stock
       `exp/intrface/bintroe` keeps Classic's labels for the original exe, see
       split_hd_data.py and menu_rows() below).
   The sound table `sound2.dat` is left alone: it is a full 200-entry array and the pack's four
   replacements would overwrite gun sounds Council Wars uses.
2. `dc/intrf_hd/bintroe`, the one file of the DARK COLONY mode's overlay: that mode's prefix
   matches nothing else the game ships, so a Classic campaign started from the Council Wars menu
   reads the Classic data in the game root - but the menu itself has to stay the patched one.
3. The overlay `ozi_ns/` (seven characters: it has to fit the 8-byte prefix slot in the exe) with
   everything the pack loads per game: 22 missions, balance tables, unit list, sound assignments,
   ambience, terrains (incl. the pack's gatlan / gjungle / special), story and credits texts.  The
   two scene lists are renamed `.kor` -> `.txt` (the Polish edition's extension; our exe appends
   `.txt`) and written to `ozi_ns/intrf_hd/`, because the patched exe opens the briefing lists
   as `intrf_hd/hxscene` (patch_hd_paths.py, 14 Sep 2026).  Left out: the start-up-only files
   above (dead weight in the overlay), the pack's 640x480 interface screens (our 1024x768 menu,
   race-select and intro screens are copied from `exp/intrf_hd` into `ozi_ns/intrf_hd` instead,
   so pack mode looks identical), editor backups.  `ozisave/` is created for the pack's own
   save games.

The overlay only has to be complete relative to the game root, not to exp/: the pack was built on
a Classic root and carries every Council Wars file its missions need (checked: the only exp/
files invisible in pack mode are Council Wars' own missions, briefings and scene lists).

CLI
    python build_ozi_overlay.py "DC - Council wars"            (dry run: prints the plan)
    python build_ozi_overlay.py "DC - Council wars" --apply
    python build_ozi_overlay.py "DC - Council wars" --pack ../../OZI_NS/M1PACK --apply
"""

import argparse
import os
import re
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paint_intro import cw_menu_lift  # noqa: E402  (the Council Wars HD menu cluster sits higher, doc 10.36)

NEW_UNITS = ('dalg', 'spyo', 'reae')
REPLACED = ('tran',)
OZI_NAME = '%sozi'                  # pack version of a replaced stock bank: tran -> tranozi (7 chars)
OZI_ANIM = 'animozi.dat'            # the patched exe's start-up list (patch_ozi_menu.py); anim.dat stays stock
# stock Council Wars FINs kept out of animozi.dat so every Classic unit type animates exactly as in
# Classic dc16.exe (network lockstep, docstring item 1; doc DC16_DISPLAY_AND_RESOLUTION.md 10.39)
NOT_IN_OZI_ANIM = ('grrr.fin', 'troo.fin')
SKIP_DIRS = {'animate', 'sprites'}
SKIP_FILES = {'anim.dat', 'telp.fin', 'sound/sound2.dat', 'intrface/maine', 'intrface/bintroe',
              'intrface/introe', 'intrface/shumane', 'intrface/intrg.gif', 'intrface/intro.gif'}
SKIP_SUFFIXES = ('.bak', '.med')
UI_FROM_EXP = ('bintroe', 'shumane', 'introe')
HD_DIR = 'intrf_hd'                 # 1024x768 screens and briefing lists (split_hd_data.py / patch_hd_paths.py)
OVERLAY = 'ozi_ns'
SAVEDIR = 'ozisave'
LABEL_OLD, LABEL_NEW = b'PLAY INTRO', b'OZI MISSIONS'


def find_ci(folder, name):
    """Path of `name` inside `folder`, case-insensitively, or None."""
    if not os.path.isdir(folder):
        return None
    for f in os.listdir(folder):
        if f.lower() == name.lower():
            return os.path.join(folder, f)
    return None


def same(a, b):
    return os.path.isfile(b) and open(a, 'rb').read() == open(b, 'rb').read()


class Plan:
    def __init__(self, apply):
        self.apply, self.copies, self.edits, self.notes = apply, [], [], []

    def copy(self, src, dst, why):
        if same(src, dst):
            return
        self.copies.append((src, dst, why))
        if self.apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

    def write(self, dst, data, why):
        if os.path.isfile(dst) and open(dst, 'rb').read() == data:
            return
        self.edits.append((dst, why))
        if self.apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst, 'wb').write(data)


# The letterbox offset of the briefing globe: ((W-640)/2, (H-480)/2) for the screen size the HD
# set was built for, read from exp/intrf_hd/bintroe's `size W H` line by screen_geometry() in
# main(); (192, 144) is the 1024x768 value. Hard-coded until 21 Sep 2026, when the first
# 1280x800 set put the OZI rows and the pack's globe markers at the 1024x768 places (doc 10.24).
MARKER_SHIFT = (192, 144)
FRAME_XY = re.compile(rb'^(\s*)(\d+)(\s+)(\d+)(\s+)(\d+)(\s*)$')
SIZE_LINE = re.compile(rb'^\s*size\s+(?:\d+\s+\d+\s+)?(\d+)\s+(\d+)\s*$', re.M)
LIVE_PUSHB = re.compile(rb'^\s*pushb\s+(\d+)\s+\d+\s+(\d+)\s+(\d+)\s', re.M)   # `%` rows are comments
LIVE_GADGET = re.compile(rb'^\s*gadget\s+(\d+)\s', re.M)
BANIM_PAIRS = re.compile(rb'^\s*banim\s+18\s+\d+\s+(\d+)\s+(\d+)\s', re.M)

# The patched Council Wars menu (maintainer, 23 Sep 2026), by exe button id; see menu_layout().
# `None` = an empty place in the second column.  Ids 6 and 7 are the two Dark Colony buttons the
# same day's evening added (patch_ozi_menu.py gives them handlers and widens the menu's id filter);
# they were the LARGEBUTTON gadgets of buttons 0 and 1, which move to 19 and 20 here.
OZI_COLUMNS = ((1, 6, 7, 0, 2, 16, 4), (3, 5, None, None, None, None, 12))
# ... and the maintainer's grouping: a gap of half a button's height (25 -> 13 px) after rows 1, 3
# and 5, which separates ACADEMY, the two Dark Colony entries, the two Council Wars entries and the
# two pack entries, and the same half-height gap between the two columns (1 px in the stock grid),
# after which the block is re-centred on the screen.  Vertically (24 Sep 2026, maintainer: "return
# back credentials [credits] for higher than 640x480 resolutions") the block's first row sits
# OZI_CREDITS_ROOM rows under the DCUT title - 11 px, the stock 100-row credits box, 9 px - unless
# that would take the bottom row past H-72 (the stock 640x480 bottom row 408, 2-3 px above the
# bottom artwork that every backdrop starts at H-45): then the block stops there and the box loses
# the difference (patch_resolution.cw_credits_height: 94 rows at 1024x768, 76 at 1280x720, 100 at
# 1280x800 and above).  At 640x480 the 217-row block fills the backdrop's black band, so the block
# grows upwards from row 408 as before and patch_ozi_menu.py removes the box there.  Both anchors
# depend only on the title row and the screen size, so the layout is idempotent.  The whole Council
# Wars cluster - title included, so the block follows - sits paint_intro.cw_menu_lift(H) rows higher
# at the HD sizes (maintainer, same day: "... 15 points higher for resolutions except 640x480"), and
# so does the H-72 cap.
OZI_GAP_AFTER = (1, 3, 5)           # 1-based row numbers
OZI_GAP_OF_HEIGHT = 0.5             # of a button's height, between rows
OZI_COL_GAP_OF_HEIGHT = 0.5         # of a button's height, between the columns
OZI_CREDITS_ROOM = 11 + 100 + 9     # title -> first row at the HD sizes: 11 px, the stock 100-row credits box, 9 px
OZI_BOTTOM_MARGIN = 72              # the bottom row never passes H-72 (stock 640x480 row 408; artwork from H-45)
TITLE_GADGET = re.compile(rb'^\s*gadget\s+\d+\s+\d+\s+\d+\s+(\d+)\s+\d+\s+(\d+)\s+DCUT\b', re.M | re.I)
STOCK_BUTTONS = (0, 1, 2, 3, 4, 5, 12, 16)      # the stock script's `pushb` ids
STOCK_GADGETS = (8, 9, 10, 11, 13, 17)          # ... and the gadgets that keep their id
OZI_NEW_BUTTONS = (6, 7)                        # the two Dark Colony buttons
OZI_RENUM = {6: 19, 7: 20}                      # gadgets of buttons 0 and 1 -> free ids
OZI_GADGET = {0: 19, 1: 20, 2: 8, 3: 9, 4: 10, 5: 11,
              6: 21, 7: 22, 12: 13, 16: 17}     # pushb -> its LARGEBUTTON
OZI_LABEL_OF = {6: 9, 7: 10}                    # the new buttons -> their `textmsg` number
OZI_TEMPLATE = {'pushb': 16, 'gadget': 17, 'textmsg': 8}   # lines the new ones are cloned after
OZI_BANIM = 18                                  # the `banim` object id (unchanged)
# The `banim` widget (button.c `create_banim` 0x427854 / update 0x4279EC in Classic) is the
# menu's opening wave: the FIRST listed plate must carry `anim_oneoff` (it starts at open), and
# whenever plate k has finished its one-shot the widget starts plate k+1 and reveals button k+1
# - so the pair order IS the order of the wave, and until 25 Sep 2026 it was the stock script's
# (0 1 2 3 4 5 16 12 + 6 7), which the seven-row layout scattered over the block (maintainer:
# "initial animation of buttons are in wrong places. animation must go from top buttons to
# bottom buttons").  menu_order() lists the placed buttons column by column, each from top to
# bottom, left column first - the stock 2x4 grid's own sequence (6 7 8 = left column, 9 10 11 =
# right column, 17 13 = the bottom row appended later).
PLATE_FIRST, PLATE_REST = b'anim_oneoff', b'anim_stopped'   # the plate gadgets' 9th token
OZI_LABELS = {                                  # `textmsg` number (button id) -> new text
    1: b'COUNCIL WARS',                         # button 0, was NEW CAMPAIGN
    2: b'ACADEMY',                              # button 1, was TRAINING
    3: b'LOAD CW GAME',                         # button 2, was LOAD GAME
    5: b'LOAD OZI GAME',                        # button 4, was SINGLE PLAYER WAR / OZI LOAD
    8: b'OZI MISSIONS',                         # button 16, was PLAY INTRO
    9: b'DARK COLONY',                          # button 6, new
    10: b'LOAD DC GAME',                        # button 7, new
}
# 640x480: the first row of the backdrop below the planet's crescent (measured on the button
# columns of exp/intrface/intrg.gif, which is black from here to the artwork at row 435).  The
# painted HD backdrops are black from far above the block, so only this size constrains it.
STOCK_TOP_LIMIT = 218
DC_OVERLAY = 'dc'                   # Dark Colony mode's prefix: only the menu script lives here


def screen_geometry(menu_data):
    """(W, H) from the menu script's `size` line (`size W H` or `size 0 0 W H`)."""
    m = SIZE_LINE.search(menu_data)
    if not m:
        raise SystemExit('exp/%s/bintroe: no `size` line' % HD_DIR)
    return int(m.group(1)), int(m.group(2))


def shift_scene_markers(data):
    """Move the `frame x y` line of every mission block (the three integers after the second
    .avi line) by MARKER_SHIFT - the same edit pad_background.py made to the stock scene lists
    when the briefing screen moved to 1024x768 (doc section 10.2)."""
    out, prev_avi = [], False
    for ln in data.split(b'\n'):
        m = FRAME_XY.match(ln)
        if m and prev_avi:
            ln = b'%s%s%s%d%s%d%s' % (m.group(1), m.group(2), m.group(3), int(m.group(4)) + MARKER_SHIFT[0],
                                      m.group(5), int(m.group(6)) + MARKER_SHIFT[1], m.group(7))
        prev_avi = ln.strip().lower().endswith(b'.avi')
        out.append(ln)
    return b'\n'.join(out)


def scene_entries(pack, name):
    """Mission entries (`.scn` lines) of a pack scene list, `.kor` or `.txt`."""
    src = find_ci(os.path.join(pack, 'gamestat'), name + '.kor') or \
        find_ci(os.path.join(pack, 'gamestat'), name + '.txt')
    if not src:
        return []
    return [l for l in open(src, 'rb').read().decode('latin-1').splitlines() if l.strip().lower().endswith('.scn')]


def silent_wav(seconds=0.1):
    """A short silent PCM WAV in the format of the stock briefings (44.1 kHz, stereo, 16 bit)."""
    import io
    import wave
    buf = io.BytesIO()
    w = wave.open(buf, 'wb')
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(44100)
    w.writeframes(bytes(int(44100 * seconds) * 4))
    w.close()
    return buf.getvalue()


def fin_banks(path):
    d = open(path, 'rb').read()
    n = struct.unpack_from('<H', d, 6)[0]
    return [d[8 + 8 * i: 16 + 8 * i].split(b'\0')[0].decode('latin-1') for i in range(n)]


def base_set(game, pack, plan):
    exp = os.path.join(game, 'exp')
    for unit in NEW_UNITS + REPLACED:
        fin = find_ci(os.path.join(pack, 'Animate'), unit + '.fin')
        spr = find_ci(os.path.join(pack, 'sprites'), unit + '.spr')
        if not fin or not spr:
            raise SystemExit('pack lacks %s.fin / %s.spr' % (unit, unit))
        if unit in NEW_UNITS:
            plan.copy(fin, os.path.join(exp, 'animate', unit + '.fin'), 'new unit')
            plan.copy(spr, os.path.join(exp, 'sprites', unit + '.spr'), 'new unit')
        else:
            # The pack's transport replaces the stock one, but the stock files stay untouched for
            # the original exe: the pack version goes in as <unit>ozi (bank field renamed, 7 chars
            # + NUL: load_sprite_bank formats it as a C string) and only animozi.dat names it.
            new = OZI_NAME % unit
            data = open(fin, 'rb').read().replace(unit.encode().ljust(8, b'\0'), new.encode().ljust(8, b'\0'))
            plan.write(os.path.join(exp, 'animate', new + '.fin'), data, "pack transport, bank '%s'" % new)
            plan.copy(spr, os.path.join(exp, 'sprites', new + '.spr'), 'pack transport bank')
        for bank in fin_banks(fin):
            if bank.lower() == unit:
                continue
            if not (find_ci(os.path.join(exp, 'sprites'), bank + '.spr')
                    or find_ci(os.path.join(game, 'SPRITES'), bank + '.spr')):
                plan.notes.append('WARNING %s.fin needs sprite bank %s, not found' % (unit, bank))

    # exp/animozi.dat = the STOCK exp/anim.dat with the replaced units renamed, the two Classic-type
    # deploy FINs dropped (NOT_IN_OZI_ANIM) and the new units appended; the patched exe opens it
    # instead of anim.dat (patch_ozi_menu.py DGROUP_SITES).
    anim = find_ci(exp, 'anim.dat')
    data = open(anim, 'rb').read()
    eol = b'\r\n' if b'\r\n' in data else b'\n'
    lines = [l for l in data.split(eol) if l.strip().lower().decode('latin-1') not in NOT_IN_OZI_ANIM]
    lines = [((OZI_NAME % l.strip().decode()[:-4]) + '.fin').encode() if l.strip().lower()[:-4].decode() in REPLACED
             and l.strip().lower().endswith(b'.fin') else l for l in lines]
    have = {l.strip().lower() for l in lines}
    while lines and not lines[-1].strip():
        lines.pop()
    lines += [(u + '.fin').encode() for u in NEW_UNITS if (u + '.fin').encode() not in have]
    plan.write(os.path.join(exp, OZI_ANIM), eol.join(lines) + eol,
               'stock anim.dat + %s, %s, without %s' % (', '.join(OZI_NAME % u for u in REPLACED),
                                                         ', '.join(NEW_UNITS), ', '.join(NOT_IN_OZI_ANIM)))
    for stock in ('anim.dat', 'animate/tran.fin', 'sprites/tran.spr'):
        p = find_ci(os.path.join(exp, *stock.split('/')[:-1]), stock.split('/')[-1])
        if p and b'TRANSMOKEY' in open(p, 'rb').read() and stock != 'anim.dat':
            plan.notes.append('WARNING exp/%s is the PACK version, restore the stock file (CD /EXPENG/EXP)' % stock)
        if p and stock == 'anim.dat' and any(l.strip().lower() in {(u + '.fin').encode() for u in NEW_UNITS} for l in data.split(eol)):
            plan.notes.append('WARNING exp/anim.dat lists pack units, restore the stock file (CD /EXPENG/EXP)')

    menu = find_ci(os.path.join(exp, HD_DIR), 'bintroe')
    if not menu:
        raise SystemExit('missing exp/%s/bintroe (run split_hd_data.py first)' % HD_DIR)
    data = open(menu, 'rb').read()
    new, place = menu_script(data)
    plan.write(menu, new, 'patched menu: %d rows at x=%d / %d, y=%s; labels %s'
               % (len(OZI_COLUMNS[0]), place[1][0], place[3][0],
                  '/'.join(str(place[i][1]) for i in OZI_COLUMNS[0]),
                  ', '.join(t.decode() for t in OZI_LABELS.values())))
    if LABEL_NEW not in new or b'LOAD OZI GAME' not in new or b'DARK COLONY' not in new:
        plan.notes.append('WARNING exp/%s/bintroe: menu rows not recognised, edit by hand' % HD_DIR)
    # The Dark Colony mode reads every file through its own prefix `dc/` (patch_ozi_menu.py) and
    # falls back to the game root, which holds Classic's own data - including Classic's own menu
    # script.  So the overlay carries this one file, the patched menu, and nothing else.
    plan.write(os.path.join(game, DC_OVERLAY, HD_DIR, 'bintroe'), new,
               'Dark Colony mode: the patched menu (the only file in %s/)' % DC_OVERLAY)


def set_tokens(line, changes):
    """Replace whitespace-separated tokens of a script line by 1-based number, keeping its spacing."""
    toks, n = re.findall(rb'\S+|[ \t]+', line), 0
    for i, tok in enumerate(toks):
        if tok.isspace():
            continue
        n += 1
        if n in changes:
            toks[i] = changes[n]
    return b''.join(toks)


def textmsg_line(n, text):
    """A `textmsg` line in the script's own column layout (the text starts at column 16)."""
    num = b'%d' % n
    return b'textmsg ' + num + b' ' * (8 - len(num)) + text


def menu_layout(data):
    """Where the OZI mode's seven menu rows go, derived from the script's own button grid.

    The stock script (`exp/intrface/bintroe`, Classic's 2x4 grid since 23 Sep 2026, doc 10.35)
    is what the *untouched* exe reads; the patched builds read a copy of it in which this
    function arranges the ten buttons in the order the maintainer asked for - the Dark Colony,
    Council Wars and pack campaigns one under the other, each with its load button, and the
    three screens that are not a campaign in the second column:

        ACADEMY       (1)   MULTI PLAYER WAR (3)
        DARK COLONY   (6)   ENCYCLOPEDIA     (5)
        LOAD DC GAME  (7)
        COUNCIL WARS  (0)
        LOAD CW GAME  (2)
        OZI MISSIONS (16)
        LOAD OZI GAME (4)   QUIT            (12)

    Ids are the exe's button numbers, which decide the handler (patch_ozi_menu.py rewires 16 and
    4 to the pack and adds 6 and 7 for Dark Colony), so only the positions and the labels move.
    Columns, pitch and button size are read off the grid; the rows hang from the DCUT title: the
    first row OZI_CREDITS_ROOM (120) rows under it, leaving the code-drawn credits box its stock
    100 rows, unless the bottom row would pass H-72 - then the block stops there (94-row box at
    1024x768, 76 at 1280x720, and at 640x480, where H-72 is the stock bottom row 408, the whole
    block grows upwards from it and patch_ozi_menu.py removes the box).  Both limits depend only
    on the title and the screen size, so applying this twice changes nothing.  At 640x480 the
    group gap shrinks by a pixel so that the first row still clears the crescent.

    Returns ({id: (x, y)}, pitch)."""
    xy = {int(m.group(1)): (int(m.group(2)), int(m.group(3))) for m in LIVE_PUSHB.finditer(data)}
    gadgets = {int(m.group(1)) for m in LIVE_GADGET.finditer(data)}
    missing = [str(n) for n in STOCK_BUTTONS if n not in xy]
    missing += ['gadget %d' % n for n in STOCK_GADGETS if n not in gadgets]
    if not (set(OZI_RENUM) <= gadgets or set(OZI_RENUM.values()) <= gadgets):
        # the stock grid has them as 6 and 7, this function's own output as 19 and 20
        missing += ['gadget %d/%d' % (a, b) for a, b in sorted(OZI_RENUM.items())]
    m = BANIM_PAIRS.search(data)
    pairs = (len(STOCK_BUTTONS), len(STOCK_BUTTONS) + len(OZI_NEW_BUTTONS))
    if missing or not m or m.group(1) != m.group(2) or int(m.group(1)) not in pairs:
        raise SystemExit('exp/%s/bintroe: not Classic\'s 2x4 button grid (missing %s, banim %s) - '
                         'rebuild the HD set from exp/intrface/bintroe (doc 10.35)'
                         % (HD_DIR, ', '.join(missing) or 'nothing',
                            b' '.join(m.groups()).decode() if m else 'absent'))
    t = TITLE_GADGET.search(data)
    if not t:
        raise SystemExit('exp/%s/bintroe: no DCUT title gadget (the menu rows hang from it)' % HD_DIR)
    title_bottom = int(t.group(1)) + int(t.group(2))
    xs = sorted({x for x, _ in xy.values()})      # only checked, not used: see below
    ys = sorted({y for _, y in xy.values()})      # only the pitch is taken from the rows
    if len(xs) != 2 or len(ys) < 4:
        raise SystemExit('exp/%s/bintroe: expected two button columns and at least four rows, '
                         'found %d x %d' % (HD_DIR, len(xs), len(ys)))
    pitch = min(b - a for a, b in zip(ys, ys[1:]))
    sizes = {(int(m.group(1)), int(m.group(2))) for m in
             re.finditer(rb'(?m)^\s*pushb\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)\s+(\d+)\s', data)}
    bw, bh = min(w for w, _ in sizes), min(h for _, h in sizes)
    rows = len(OZI_COLUMNS[0])
    w, h = screen_geometry(data)
    top_limit = STOCK_TOP_LIMIT if (w, h) == (640, 480) else 0
    gap = int(round(bh * OZI_GAP_OF_HEIGHT))
    while True:
        rise = (rows - 1) * pitch + gap * len(OZI_GAP_AFTER)      # first row -> bottom row
        bottom = min(title_bottom + OZI_CREDITS_ROOM + rise, h - OZI_BOTTOM_MARGIN - cw_menu_lift(h))
        if gap == 0 or bottom - rise >= top_limit:
            break
        gap -= 1
    # row offsets from the first row: one pitch per row plus a gap after the rows in OZI_GAP_AFTER
    offs = [k * pitch + gap * sum(1 for r in OZI_GAP_AFTER if r <= k) for k in range(rows)]
    # the two columns get the full half-height gap and the block is re-centred on the screen
    col_gap = int(round(bh * OZI_COL_GAP_OF_HEIGHT))
    left = (w - (2 * bw + col_gap)) // 2
    cols = (left, left + bw + col_gap)
    place = {}
    for x, ids in zip(cols, OZI_COLUMNS):
        for k, i in enumerate(ids):
            if i is not None:
                place[i] = (x, bottom - (offs[-1] - offs[k]))
    return place, pitch


def menu_order(place):
    """The order of the opening wave = the `banim` pair order: the placed buttons column by
    column (left first), each column from top to bottom - see the note at PLATE_FIRST."""
    return tuple(sorted(place, key=lambda i: (place[i][0], place[i][1])))


def menu_script(data):
    """Lay the Council Wars main-menu script out for the patched exe: the rows of menu_layout(),
    the labels the three campaigns take over, the two new Dark Colony buttons with their plates,
    a `banim` that pairs all ten in the order of menu_order() and `anim_oneoff` on the first
    plate of that order (`anim_stopped` on the other nine), everything else (sizes, sprites,
    the logo and the title) untouched.  The new lines are cloned from the script's own
    `pushb 16` / `gadget 17` / `textmsg 8` so they keep its field layout, and re-cloned on a
    second run, which makes this idempotent.  The stock file mixes CRLF and bare LF line
    endings; each line keeps its own.  Returns (new data, {id: (x, y)})."""
    place, _ = menu_layout(data)
    order = menu_order(place)
    first_plate = OZI_GADGET[order[0]]
    move = dict(place)
    move.update({OZI_GADGET[i]: xy for i, xy in place.items()})   # each gadget follows its button
    generated = {b'pushb': set(OZI_NEW_BUTTONS),
                 b'gadget': {OZI_GADGET[i] for i in OZI_NEW_BUTTONS},
                 b'textmsg': set(OZI_LABEL_OF.values())}
    out = []
    for raw in data.split(b'\n'):
        line, cr = (raw[:-1], b'\r') if raw.endswith(b'\r') else (raw, b'')
        m = re.match(rb'\s*(pushb|gadget)\s+(\d+)\s', line)
        t = re.match(rb'\s*textmsg\s+(\d+)\s', line)
        if m:
            kind, i = m.group(1), int(m.group(2))
            if i in generated[kind]:
                continue                                  # re-emitted after its template line
            if kind == b'gadget' and i in OZI_RENUM:       # free the ids the new buttons take
                line, i = set_tokens(line, {2: b'%d' % OZI_RENUM[i]}), OZI_RENUM[i]
            if i in move:
                changes = {4: b'%d' % move[i][0], 5: b'%d' % move[i][1]}
                if kind == b'gadget':                      # a plate: the wave starts at the first
                    changes[9] = PLATE_FIRST if i == first_plate else PLATE_REST
                line = set_tokens(line, changes)
            out.append(line + cr)
            if i == OZI_TEMPLATE[kind.decode()]:
                for new in OZI_NEW_BUTTONS:
                    x, y = place[new]
                    ident = new if kind == b'pushb' else OZI_GADGET[new]
                    changes = {2: b'%d' % ident, 4: b'%d' % x, 5: b'%d' % y}
                    if kind == b'pushb':
                        changes[12] = b'%d' % OZI_LABEL_OF[new]
                    else:
                        changes[9] = PLATE_FIRST if ident == first_plate else PLATE_REST
                    out.append(set_tokens(line, changes) + cr)
            continue
        if t:
            n = int(t.group(1))
            if n in generated[b'textmsg']:
                continue
            if n in OZI_LABELS:
                line = textmsg_line(n, OZI_LABELS[n])
            out.append(line + cr)
            if n == OZI_TEMPLATE['textmsg']:
                for new in OZI_NEW_BUTTONS:
                    label = OZI_LABEL_OF[new]
                    out.append(textmsg_line(label, OZI_LABELS[label]) + cr)
            continue
        if re.match(rb'\s*banim\s+\d+\s', line):
            ids = order
            line = b'banim   %d  0  %d %d\t %s  %s' % (
                OZI_BANIM, len(ids), len(ids),
                b' '.join(b'%d' % OZI_GADGET[i] for i in ids), b' '.join(b'%d' % i for i in ids))
        out.append(line + cr)
    return b'\n'.join(out), place


def overlay(game, pack, plan):
    dst_root = os.path.join(game, OVERLAY)
    produced = set()
    for dp, dirs, files in os.walk(pack):
        rel = os.path.relpath(dp, pack)
        rel = '' if rel == '.' else rel.replace(os.sep, '/')
        dirs[:] = [d for d in dirs if not (rel == '' and d.lower() in SKIP_DIRS)]
        for f in files:
            key = (rel + '/' + f if rel else f).lower()
            if key in SKIP_FILES or key.endswith(SKIP_SUFFIXES):
                continue
            out = f[:-4] + '.txt' if key.startswith('gamestat/') and key.endswith('.kor') else f
            dst = os.path.join(dst_root, rel.replace('/', os.sep), out) if rel else os.path.join(dst_root, out)
            if key.startswith('gamestat/') and 'scene' in key:
                # the patched exe opens the briefing lists as `intrf_hd/<name>` + `.txt`
                dst = os.path.join(dst_root, HD_DIR, out)
            produced.add(os.path.normcase(dst))
            if key.startswith('gamestat/') and 'scene' in key:
                raw = open(os.path.join(dp, f), 'rb').read()
                plan.write(dst, shift_scene_markers(raw), 'scene list, globe markers +%d,+%d' % MARKER_SHIFT)
                # the unshifted list as well, under the stock name in ozi_ns/gamestat (inert for the
                # exe, which reads intrf_hd/): the patcher rebuilds the shifted copy from it for any
                # screen size (Apply-DarkColonyPatches.ps1 Write-InterfaceSet, 21 Sep 2026)
                unshifted = os.path.join(dst_root, 'gamestat', out)
                produced.add(os.path.normcase(unshifted))
                plan.write(unshifted, raw, 'scene list, unshifted (source for the patcher)')
            else:
                plan.copy(os.path.join(dp, f), dst, 'overlay')
    for name in UI_FROM_EXP:
        src = find_ci(os.path.join(game, 'exp', HD_DIR), name)
        if not src:
            plan.notes.append('WARNING exp/%s/%s missing (run split_hd_data.py first)' % (HD_DIR, name))
            continue
        dst = os.path.join(dst_root, HD_DIR, name)
        produced.add(os.path.normcase(dst))
        plan.copy(src, dst, '%dx%d screen from exp/%s' % (MARKER_SHIFT[0] * 2 + 640, MARKER_SHIFT[1] * 2 + 480, HD_DIR))
    # Briefings.  The scene loader plays `mission/h<n>` / `mission/g<n>` before every mission
    # through the wave loader, whose last resort after the overlay and the root is the CD path -
    # with no CD that is the "Please insert The Dark Colony Expansion Pak CD" prompt and an exit
    # (first game test, 10 Sep 2026).  The pack's speech (`M1PACK/mission/*.wav`, from the base
    # pack's MISSIEE folder) is copied by the walk above; any mission still without a WAV gets a
    # short silent one in the stock briefing format so the game never reaches the CD prompt.
    for side, count in (('h', len(scene_entries(pack, 'hxscene'))), ('g', len(scene_entries(pack, 'gxscene')))):
        for n in range(1, count + 1):
            dst = os.path.join(dst_root, 'mission', '%s%d.wav' % (side, n))
            if find_ci(os.path.dirname(dst), os.path.basename(dst)):
                continue
            produced.add(os.path.normcase(dst))
            plan.write(dst, silent_wav(), 'silent briefing placeholder')
    marker = os.path.join(game, SAVEDIR, SAVEDIR + '.txt')
    plan.write(marker, b'Save games of the OZI MISSIONS campaign mode (ozi_ns mission pack).\r\n',
               'save folder for pack mode')
    if os.path.isdir(dst_root):
        extra = [os.path.join(dp, f) for dp, _, fs in os.walk(dst_root) for f in fs
                 if os.path.normcase(os.path.join(dp, f)) not in produced]
        for e in extra:
            plan.notes.append('NOTE stale file in overlay (not produced by this tool): ' + e)
    for scene in ('hxscene.txt', 'gxscene.txt'):
        src = find_ci(os.path.join(pack, 'gamestat'), scene[:-4] + '.kor') or \
            find_ci(os.path.join(pack, 'gamestat'), scene)
        if not src:
            plan.notes.append('WARNING pack has no gamestat/%s' % scene)
            continue
        lines = open(src, 'rb').read().decode('latin-1').splitlines()
        for l in lines:
            if l.lower().endswith('.scn') and '/' in l:
                scn = os.path.join(pack, 'scenario', *l.strip().split('/'))
                if not find_ci(os.path.dirname(scn), os.path.basename(scn)):
                    plan.notes.append('WARNING %s lists %s, missing in the pack' % (scene, l.strip()))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('game', help='the "DC - Council wars" folder')
    ap.add_argument('--pack', help='the pack folder (default: <game>/../../OZI_NS/M1PACK, i.e. beside the game repo)')
    ap.add_argument('--apply', action='store_true', help='write; default is a dry run')
    a = ap.parse_args(argv)
    game = os.path.abspath(a.game)
    pack = os.path.abspath(a.pack or os.path.join(game, os.pardir, os.pardir, 'OZI_NS', 'M1PACK'))
    for p in (game, pack, os.path.join(game, 'exp', 'anim.dat')):
        if not os.path.exists(p):
            raise SystemExit('missing: ' + p)

    menu = find_ci(os.path.join(game, 'exp', HD_DIR), 'bintroe')
    if not menu:
        raise SystemExit('missing exp/%s/bintroe (run split_hd_data.py first)' % HD_DIR)
    global MARKER_SHIFT
    w, h = screen_geometry(open(menu, 'rb').read())
    MARKER_SHIFT = ((w - 640) // 2, (h - 480) // 2)
    print('HD set is %dx%d: globe markers shift by +%d,+%d' % (w, h, *MARKER_SHIFT))

    plan = Plan(a.apply)
    base_set(game, pack, plan)
    overlay(game, pack, plan)

    verb = 'written' if a.apply else 'would write'
    base = [c for c in plan.copies if c[2] != 'overlay'] + [e for e in plan.edits if OVERLAY not in e[0]]
    ov = [c for c in plan.copies if c[2] == 'overlay']
    print('%s: %d base-set files, %d overlay files, %d edits'
          % (verb, len([c for c in plan.copies if c[2] != 'overlay']), len(ov), len(plan.edits)))
    for src, dst, why in plan.copies:
        if why != 'overlay':
            print('  %-58s <- %s  (%s)' % (os.path.relpath(dst, game), os.path.relpath(src, pack), why))
    for dst, why in plan.edits:
        print('  %-58s    %s' % (os.path.relpath(dst, game), why))
    if ov:
        tops = {}
        for _, dst, _ in ov:
            top = os.path.relpath(dst, os.path.join(game, OVERLAY)).split(os.sep)[0]
            tops[top] = tops.get(top, 0) + 1
        print('  %s/: %s' % (OVERLAY, ', '.join('%s %d' % kv for kv in sorted(tops.items()))))
    for n in plan.notes:
        print('  ' + n)
    if not plan.copies and not plan.edits:
        print('  nothing to do, everything is in place')
    return 1 if any(n.startswith('WARNING') for n in plan.notes) else 0


if __name__ == '__main__':
    sys.exit(main())

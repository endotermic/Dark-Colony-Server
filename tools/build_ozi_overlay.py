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
     - the main-menu labels 8 "PLAY INTRO" -> "OZI MISSIONS" and 5 "SINGLE PLAYER WAR" ->
       "OZI LOAD" in `exp/intrf_hd/bintroe` (the HD override script; the stock
       `exp/intrface/bintroe` keeps Classic's labels for the original exe, see
       split_hd_data.py and menu_rows() below).
   The sound table `sound2.dat` is left alone: it is a full 200-entry array and the pack's four
   replacements would overwrite gun sounds Council Wars uses.
2. The overlay `ozi_ns/` (seven characters: it has to fit the 8-byte prefix slot in the exe) with
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

NEW_UNITS = ('dalg', 'spyo', 'reae')
REPLACED = ('tran',)
OZI_NAME = '%sozi'                  # pack version of a replaced stock bank: tran -> tranozi (7 chars)
OZI_ANIM = 'animozi.dat'            # the patched exe's start-up list (patch_ozi_menu.py); anim.dat stays stock
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
# `None` = an empty place in the second column.
OZI_COLUMNS = ((1, 0, 2, 16, 4), (3, None, 5, None, 12))
# ... and the maintainer's grouping: a gap of about a quarter of a button's height (25 -> 6 px)
# after row 1 and after row 3, which separates ACADEMY, the two Council Wars entries and the two
# pack entries. The block grows upwards by the two gaps (it is anchored on the bottom row), into
# the space the shortened credits box leaves (patch_resolution.CREDITS_H_CW).
OZI_GAP_AFTER = (1, 3)              # 1-based row numbers
OZI_GAP_OF_HEIGHT = 0.25
OZI_GADGET = {0: 6, 1: 7, 2: 8, 3: 9, 4: 10, 5: 11, 12: 13, 16: 17}   # pushb -> its LARGEBUTTON
OZI_LABELS = {                                  # `textmsg` number (button id) -> new text
    1: b'COUNCIL WARS',                         # button 0, was NEW CAMPAIGN
    2: b'ACADEMY',                              # button 1, was TRAINING
    3: b'LOAD CW GAME',                         # button 2, was LOAD GAME
    5: b'LOAD OZI GAME',                        # button 4, was SINGLE PLAYER WAR / OZI LOAD
    8: b'OZI MISSIONS',                         # button 16, was PLAY INTRO
}


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

    # exp/animozi.dat = the STOCK exp/anim.dat with the replaced units renamed and the new units
    # appended; the patched exe opens it instead of anim.dat (patch_ozi_menu.py DGROUP_SITES).
    anim = find_ci(exp, 'anim.dat')
    data = open(anim, 'rb').read()
    eol = b'\r\n' if b'\r\n' in data else b'\n'
    lines = data.split(eol)
    lines = [((OZI_NAME % l.strip().decode()[:-4]) + '.fin').encode() if l.strip().lower()[:-4].decode() in REPLACED
             and l.strip().lower().endswith(b'.fin') else l for l in lines]
    have = {l.strip().lower() for l in lines}
    while lines and not lines[-1].strip():
        lines.pop()
    lines += [(u + '.fin').encode() for u in NEW_UNITS if (u + '.fin').encode() not in have]
    plan.write(os.path.join(exp, OZI_ANIM), eol.join(lines) + eol,
               'stock anim.dat + %s, %s' % (', '.join(OZI_NAME % u for u in REPLACED), ', '.join(NEW_UNITS)))
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
    if new != data:
        plan.write(menu, new, 'OZI menu: five rows at x=%d / %d, y=%s; labels %s'
                   % (place[1][0], place[3][0],
                      '/'.join(str(place[i][1]) for i in OZI_COLUMNS[0]),
                      ', '.join(t.decode() for t in OZI_LABELS.values())))
    if LABEL_NEW not in new or b'LOAD OZI GAME' not in new:
        plan.notes.append('WARNING exp/%s/bintroe: menu rows not recognised, edit by hand' % HD_DIR)


def menu_layout(data):
    """Where the OZI mode's five menu rows go, derived from the script's own button grid.

    The stock script (`exp/intrface/bintroe`, Classic's 2x4 grid since 23 Sep 2026, doc 10.35)
    is what the *untouched* exe reads; the patched builds read a copy of it in which this
    function arranges the eight buttons in the order the maintainer asked for - the pack's two
    entries next to the Council Wars ones, the campaign entries renamed after the two campaigns
    and the rest of the second column left empty:

        ACADEMY       (1)   MULTI PLAYER WAR (3)
        COUNCIL WARS  (0)
        LOAD CW GAME  (2)   ENCYCLOPEDIA     (5)
        OZI MISSIONS (16)
        LOAD OZI GAME (4)   QUIT            (12)

    Ids are the exe's button numbers, which decide the handler (patch_ozi_menu.py rewires 16 and
    4 to the pack), so only the positions and the labels move.  The layout is anchored on the
    **bottom** row of the grid it is given, which is the one position that must not move (the
    640x480 backdrop's artwork starts 3 px below it, doc 10.27) - and because that row is the
    same before and after, applying this twice changes nothing.  The fifth row is won at the top,
    where the code-positioned credits box makes room: 8 px above the new first row in every
    patched build (patch_resolution.credits_y(196, 296) at HD sizes, patch_ozi_menu's 640x480
    site at the stock size).

    Returns ({id: (x, y)}, pitch)."""
    xy = {int(m.group(1)): (int(m.group(2)), int(m.group(3))) for m in LIVE_PUSHB.finditer(data)}
    gadgets = {int(m.group(1)) for m in LIVE_GADGET.finditer(data)}
    missing = [str(n) for n in sorted(OZI_GADGET) if n not in xy]
    missing += ['gadget %d' % n for n in sorted(OZI_GADGET.values()) if n not in gadgets]
    m = BANIM_PAIRS.search(data)
    if missing or not m or m.group(1) != b'8' or m.group(2) != b'8':
        raise SystemExit('exp/%s/bintroe: not Classic\'s 2x4 button grid (missing %s, banim %s) - '
                         'rebuild the HD set from exp/intrface/bintroe (doc 10.35)'
                         % (HD_DIR, ', '.join(missing) or 'nothing',
                            b' '.join(m.groups()).decode() if m else 'absent'))
    xs = sorted({x for x, _ in xy.values()})
    ys = sorted({y for _, y in xy.values()})
    if len(xs) != 2 or len(ys) < 4:
        raise SystemExit('exp/%s/bintroe: expected two button columns and at least four rows, '
                         'found %d x %d' % (HD_DIR, len(xs), len(ys)))
    pitch = min(b - a for a, b in zip(ys, ys[1:]))
    heights = {int(m.group(1)) for m in
               re.finditer(rb'(?m)^\s*pushb\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)\s', data)}
    gap = int(round(min(heights) * OZI_GAP_OF_HEIGHT))
    # row offsets from the first row: one pitch per row plus a gap after rows OZI_GAP_AFTER
    offs = [k * pitch + gap * sum(1 for r in OZI_GAP_AFTER if r <= k)
            for k in range(len(OZI_COLUMNS[0]))]
    place = {}
    for x, ids in zip(xs, OZI_COLUMNS):
        for k, i in enumerate(ids):
            if i is not None:
                place[i] = (x, ys[-1] - (offs[-1] - offs[k]))
    return place, pitch


def menu_script(data):
    """Lay the Council Wars main-menu script out for the OZI mode: five rows in the order of
    menu_layout(), the five labels the two campaigns and the pack take over, everything else
    (sizes, sprites, animations, `banim` pairs, the logo and the title) untouched.  Idempotent.
    The stock file mixes CRLF and bare LF line endings; each line keeps its own.
    Returns (new data, {id: (x, y)})."""
    place, _ = menu_layout(data)
    move = dict(place)
    move.update({OZI_GADGET[i]: xy for i, xy in place.items()})   # each gadget follows its button
    out = []
    for raw in data.split(b'\n'):
        line, cr = (raw[:-1], b'\r') if raw.endswith(b'\r') else (raw, b'')
        m = re.match(rb'\s*(pushb|gadget)\s+(\d+)\s', line)
        t = re.match(rb'\s*textmsg\s+(\d+)\s', line)
        if m and int(m.group(2)) in move:            # button and gadget ids do not overlap
            x, y = move[int(m.group(2))]
            toks, n = re.findall(rb'\S+|[ \t]+', line), 0
            for i, tok in enumerate(toks):
                if tok.isspace():
                    continue
                n += 1
                if n == 4:
                    toks[i] = b'%d' % x
                elif n == 5:
                    toks[i] = b'%d' % y
                    break
            line = b''.join(toks)
        elif t and int(t.group(1)) in OZI_LABELS:
            line = b'textmsg %d       %s' % (int(t.group(1)), OZI_LABELS[int(t.group(1))])
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

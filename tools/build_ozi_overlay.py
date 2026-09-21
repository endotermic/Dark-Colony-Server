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
     - the main-menu label 8 "PLAY INTRO" -> "OZI MISSIONS" in `exp/intrf_hd/bintroe` (the
       1024x768 override script; the stock `exp/intrface/bintroe` stays as it is for the
       original exe, see split_hd_data.py).
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
PUSHB_XY = re.compile(rb'^%?\s*pushb\s+(\d+)\s+\d+\s+(\d+)\s+(\d+)\s', re.M)


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
    new, (x, y_load, y_quit) = menu_script(data)
    if new != data:
        plan.write(menu, new, 'OZI MISSIONS (label 8), OZI LOAD (button 4 at %d,%d), QUIT -> %d'
                   % (x, y_load, y_quit))
    if LABEL_NEW not in new or b'OZI LOAD' not in new:
        plan.notes.append('WARNING exp/%s/bintroe: menu rows not recognised, edit by hand' % HD_DIR)


def menu_rows(data):
    """The row templates for the screen the script was laid out for.

    Council Wars is a single column: NEW CAMPAIGN (button 0), LOAD GAME (2), then PLAY INTRO
    (16), which becomes OZI MISSIONS; OZI LOAD (the SINGLE PLAYER WAR button id 4 / gadget 10,
    re-enabled) and QUIT (12 / gadget 13) follow below it at the same x and row pitch. At
    1024x768 that is x=422, rows 541 567 593 619 645; at 1280x800 x=550, 561 587 613 639 665.
    Returns (rows dict, (x, y_load, y_quit))."""
    xy = {int(m.group(1)): (int(m.group(2)), int(m.group(3))) for m in PUSHB_XY.finditer(data)}
    for need in (0, 2, 16):
        if need not in xy:
            raise SystemExit('exp/%s/bintroe: no `pushb %d` row, menu layout not recognised' % (HD_DIR, need))
    x, y0 = xy[0]
    pitch = xy[2][1] - y0
    if pitch <= 0 or xy[2][0] != x or xy[16][0] != x:
        raise SystemExit('exp/%s/bintroe: rows 0/2/16 are not a single column with a positive pitch' % HD_DIR)
    y_load = xy[16][1] + pitch
    y_quit = y_load + pitch
    rows = {
        rb'^%?\s*pushb\s+4\s+.*$':
            b'pushb   4       0       %-7d %-7d 179     25      -11     0        label centre   5 0 - remap 0' % (x, y_load),
        rb'^%?\s*gadget\s+10\s+.*$':
            b'gadget  10      0       %-7d %-7d 179     25      LARGEBUTTON  anim_stopped' % (x, y_load),
        rb'^\s*pushb\s+12\s+.*$':
            b'pushb   12      0       %-7d %-7d 179     25      -11     0  label centre 7 0 - remap 0' % (x, y_quit),
        rb'^\s*gadget\s+13\s+.*$':
            b'gadget  13      0       %-7d %-7d 179     25      LARGEBUTTON  anim_stopped' % (x, y_quit),
        rb'^\s*banim\s+18\s+.*$':
            b'banim   18  0  5 5\t 6 8 17 10 13  0 2 16 4 12',
        rb'^\s*textmsg\s+5\s+.*$': b'textmsg 5       OZI LOAD',
        rb'^\s*textmsg\s+8\s+.*$': b'textmsg 8       OZI MISSIONS',
    }
    return rows, (x, y_load, y_quit)


def menu_script(data):
    """Rewrite the five-button rows of the Council Wars main-menu script (idempotent).
    The stock file mixes CRLF and bare LF line endings; each line keeps its own.
    Returns (new data, (x, y_load, y_quit))."""
    rows, geom = menu_rows(data)
    out = []
    for raw in data.split(b'\n'):
        line, cr = (raw[:-1], b'\r') if raw.endswith(b'\r') else (raw, b'')
        for pat, repl in rows.items():
            if re.match(pat, line):
                line = repl
                break
        out.append(line + cr)
    return b'\n'.join(out), geom


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

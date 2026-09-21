#!/usr/bin/env python3
"""Make the patched Classic exe play the Classic movies under their own names (dc16new.exe only).

Since 15 Sep 2026 both games run from the "DC - Council wars" folder.  Council Wars has its own
INTRO.AVI, AENDING.AVI and HENDING.AVI, so the Classic movies live beside them as
AVI/DCINTRO.AVI, AVI/DCAENDING.AVI and AVI/DCHENDING.AVI.  The Classic exe names its movies in two
places:

  * the intro, played at start-up (0x004053A7) and by the PLAY INTRO button (0x004050FE), is the
    DGROUP string "intro.avi" at VA 0x004824A8 (file 0x7FCA8), appended to the "avi/" string at
    0x00482464.  Watcom aligned the next string ("rt", the fopen mode, at 0x004824B4) to 4 bytes, so
    "intro.avi\\0" is followed by two padding zeros: 12 bytes, exactly "dcintro.avi\\0".  The string
    is rewritten in place, its address does not change, no code and no .reloc entry is touched.
  * the two endings are data: line 154 of the campaign lists GAMESTAT/HSCENE.TXT ("avi/hending.avi")
    and GSCENE.TXT ("avi/aending.avi"), which the patched exe reads from INTRF_HD/ (patch
    `hdpaths`).  `apply` rewrites the two INTRF_HD lists beside the exe to "avi/dchending.avi" /
    "avi/dcaending.avi" when that folder exists (it does not in the patcher generator's replay, where
    only the exe is rebuilt), so the stock GAMESTAT/ lists the untouched exe reads stay as they are.

Council Wars' engexp16new.exe is refused: its "intro.avi" IS the Council Wars intro.

CLI
    python patch_movies.py verify EXE
    python patch_movies.py plan   EXE
    python patch_movies.py apply  EXE        (writes EXE.movies.bak first; edits INTRF_HD lists if present)
"""

import argparse
import os
import shutil
import struct
import sys

DGROUP_VA_TO_FILE = {'classic': 0x402800, 'cw': 0x402600}
SIZE_OF = {659456: 'classic', 659968: 'cw'}
STOCK = b'intro.avi\0\0\0'
NEW = b'dcintro.avi\0'
ANCHOR = b'/bintro\0'                      # the string before it ("intrface/bintro" or "intrf_hd/bintro")
LISTS = {'HSCENE.TXT': (b'avi/hending.avi', b'avi/dchending.avi'),
         'GSCENE.TXT': (b'avi/aending.avi', b'avi/dcaending.avi')}
# 640x480 (21 Sep 2026): the stock-size exe reads GAMESTAT/, whose lists the ORIGINAL exe reads too and
# which therefore stay untouched (maintainer rule of 14 Sep 2026).  The two DGROUP strings
# "gamestat/hscene" / "gamestat/gscene" (the exe appends ".txt") end in exactly six letters followed by
# the next string, so the name part becomes "hscndc" / "gscndc" in place and the exe reads
# GAMESTAT/HSCNDC.TXT / GSCNDC.TXT: copies of the stock lists naming the Classic endings, written by
# this tool (apply, in a game folder) and by the patcher (Apply-DarkColonyPatches.ps1).
LIST_NAMES = {'hscene': 'hscndc', 'gscene': 'gscndc'}
STOCK_LIST_OF = {'hscene': 'HSCENE.TXT', 'gscene': 'GSCENE.TXT'}


def find_list_sites(data):
    """[(old name, new name, file offset of the 6-letter name, state)] for the two list strings."""
    va, rsize, rptr = sections(data)['DGROUP']
    dg = bytes(data[rptr:rptr + rsize])
    out = []
    for old, new in LIST_NAMES.items():
        hits = []
        for name, state in ((old, 'stock'), (new, 'patched')):
            needle = b'gamestat/' + name.encode() + b'\0'
            i = dg.find(needle)
            while i >= 0:
                hits.append((rptr + i + 9, state))
                i = dg.find(needle, i + 1)
        if len(hits) != 1:
            raise SystemExit('expected exactly one "gamestat/%s" string, found %d - the 640x480 variant needs the '
                             'stock gamestat strings (not the hdpaths patch)' % (old, len(hits)))
        out.append((old, new, hits[0][0], hits[0][1]))
    return out



def sections(data):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    opt = struct.unpack_from('<H', data, pe + 20)[0]
    st = pe + 24 + opt
    out = {}
    for i in range(nsec):
        s = data[st + i * 40: st + i * 40 + 40]
        vsize, va, rsize, rptr = struct.unpack_from('<IIII', s, 8)
        out[s[:8].rstrip(b'\0').decode()] = (va, rsize, rptr)
    return out


def find_site(data):
    """(file_offset, state) of the unique intro-movie string, anchored on the "…/bintro" string before it."""
    va, rsize, rptr = sections(data)['DGROUP']
    dg = bytes(data[rptr:rptr + rsize])
    hits = []
    for s, state in ((STOCK, 'stock'), (NEW, 'patched')):
        i = 0
        while True:
            i = dg.find(s, i)
            if i < 0:
                break
            if dg[i - len(ANCHOR):i] == ANCHOR:
                hits.append((rptr + i, state))
            i += 1
    if len(hits) != 1:
        raise SystemExit('expected exactly one intro-movie string site, found %d: %s'
                         % (len(hits), ', '.join('%#x' % h[0] for h in hits)))
    return hits[0]


def list_state(folder):
    """{name: 'stock'|'patched'|'missing'|'other'} for the two INTRF_HD campaign lists."""
    out = {}
    for name, (old, new) in LISTS.items():
        p = os.path.join(folder, 'INTRF_HD', name)
        if not os.path.exists(p):
            out[name] = 'missing'
            continue
        d = open(p, 'rb').read()
        out[name] = 'stock' if d.count(old) == 1 and new not in d else 'patched' if d.count(new) == 1 and old not in d else 'other'
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    ap.add_argument('--width', type=int, default=1024, help='screen size the exe is patched for; 640x480 = the stock size')
    ap.add_argument('--height', type=int, default=768)
    a = ap.parse_args(argv)
    stock_mode = (a.width, a.height) == (640, 480)
    data = bytearray(open(a.exe, 'rb').read())
    game = SIZE_OF.get(len(data))
    if game != 'classic':
        print('%s: %s - not a Classic dc16.exe build (%d bytes); the Council Wars intro.avi is its own intro, nothing to do'
              % (a.exe, 'Council Wars build' if game == 'cw' else 'unknown build', len(data)))
        return 0 if a.command == 'verify' else 2
    off, state = find_site(data)
    va = off + DGROUP_VA_TO_FILE[game]
    folder = os.path.dirname(os.path.abspath(a.exe))
    lists = list_state(folder)
    list_sites = find_list_sites(data) if stock_mode else []
    print('%s: intro movie string %s ("%s"), file %#x VA %#x; %s'
          % (a.exe, state, bytes(data[off:off + 12]).rstrip(b'\0').decode(), off, va,
             'list names ' + ', '.join('%s %s' % (o, st) for o, _, _, st in list_sites) + ' (640x480 mode)' if stock_mode
             else 'INTRF_HD lists: ' + ', '.join('%s %s' % kv for kv in sorted(lists.items()))))
    if a.command == 'verify':
        return 0
    print('  DGROUP string "intro.avi" -> "dcintro.avi"  file %#x VA %#x 12 bytes: %s -> %s; the movie name appended to "avi/" at start-up (0x004053A7) and by PLAY INTRO (0x004050FE); "intro.avi\\0" plus its two alignment padding zeros is exactly 12 bytes, so the string grows in place, its address and the .reloc table are unchanged'
          % (off, va, STOCK.hex(' '), NEW.hex(' ')))
    if stock_mode:
        for old, new, o, st in list_sites:
            print('  DGROUP string "gamestat/%s" -> "gamestat/%s"  file %#x VA %#x 6 bytes: %s -> %s; the exe appends ".txt", so it reads GAMESTAT/%s.TXT - a copy of the stock %s naming the Classic ending, written by the patcher; the stock list stays untouched for the original exe'
                  % (old, new, o, o + DGROUP_VA_TO_FILE[game], old.encode().hex(' '), new.encode().hex(' '), new.upper(), STOCK_LIST_OF[old]))
    else:
        for name, (old, new) in sorted(LISTS.items()):
            print('  INTRF_HD/%s line 154: "%s" -> "%s" (the campaign ending the patched exe plays)' % (name, old.decode(), new.decode()))
    if a.command == 'plan':
        return 0
    changed = False
    if state == 'stock':
        data[off:off + 12] = NEW
        changed = True
    for old, new, o, st in list_sites:
        if st == 'stock':
            data[o:o + 6] = new.encode()
            changed = True
    if changed:
        bak = a.exe + '.movies.bak'
        shutil.copyfile(a.exe, bak)
        open(a.exe, 'wb').write(data)
        print('written %s; backup %s' % (a.exe, bak))
    else:
        print('exe already patched, nothing to do')
    if stock_mode:
        gs = os.path.join(folder, 'GAMESTAT')
        for old, new in LIST_NAMES.items():
            src = os.path.join(gs, STOCK_LIST_OF[old])
            if not os.path.exists(src):
                print('GAMESTAT/%s not found beside the exe - %s.TXT not written (fine inside the patcher generator)' % (STOCK_LIST_OF[old], new.upper()))
                continue
            o, nw = LISTS[STOCK_LIST_OF[old]]
            open(os.path.join(gs, new.upper() + '.TXT'), 'wb').write(open(src, 'rb').read().replace(o, nw))
            print('written GAMESTAT/%s.TXT (%s with %s -> %s)' % (new.upper(), STOCK_LIST_OF[old], o.decode(), nw.decode()))
        return 0
    for name, (old, new) in sorted(LISTS.items()):
        p = os.path.join(folder, 'INTRF_HD', name)
        st = lists[name]
        if st == 'stock':
            d = open(p, 'rb').read()
            open(p, 'wb').write(d.replace(old, new))
            print('written INTRF_HD/%s: %s -> %s' % (name, old.decode(), new.decode()))
        elif st == 'patched':
            print('INTRF_HD/%s already says %s' % (name, new.decode()))
        elif st == 'missing':
            print('INTRF_HD/%s not found beside the exe - list not edited (fine inside the patcher generator; in a game folder run split_hd_data.py first)' % name)
        else:
            raise SystemExit('INTRF_HD/%s: unexpected content (neither %s nor %s exactly once)' % (name, old.decode(), new.decode()))
    return 0


if __name__ == '__main__':
    sys.exit(main())

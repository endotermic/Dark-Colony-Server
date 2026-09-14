#!/usr/bin/env python3
"""Make the patched exe read its 1024x768 interface data from INTRF_HD/ (dc16.exe / DCEXP16.EXE).

The game opens every interface script, loading bitmap and briefing list through a literal path in
DGROUP: `intrface/bintro` (the language letter `e` is appended at run time), `intrface/load.bmp`,
`gamestat/hscene` (+ `.txt`), ...  Since 14 Sep 2026 the rebuilt 1024x768 files live in INTRF_HD/
under their stock names (split_hd_data.py), so that the untouched original exe can run from the
same folder with the stock INTRFACE/ and GAMESTAT/ files.  This patch rewrites the 8-byte
directory part of exactly the 30 strings whose files were rebuilt - `intrface` / `gamestat` ->
`intrf_hd`, same length, in place - and nothing else: fonts, text files, per-screen FIN lists
without logo banks and the sprite banks keep their stock paths and their single copy.  The two
per-screen lists that do name re-baked logo banks (INTRG.DAT, INTRO.DAT) are redirected: their
INTRF_HD copies say `dcuk_hd.fin` etc. (doc DC16_DISPLAY_AND_RESOLUTION.md 10.17).

Council Wars' overlay helper (0x4063E4) prefixes `exp/` and falls back to the game root, so the
same strings serve `exp/intrf_hd/bintroe` (override script) and the root INTRF_HD GIFs.

The strings are located by content, so one tool serves both builds (Classic DGROUP file offset
+0x200 = Council Wars).

CLI
    python patch_hd_paths.py verify EXE
    python patch_hd_paths.py plan   EXE
    python patch_hd_paths.py apply  EXE          (writes EXE.hdpaths.bak first)
"""

import argparse
import shutil
import struct
import sys

NEW_DIR = b'intrf_hd'
# exe string (without the trailing NUL) -> what the game opens with it
REDIRECT = [
    ('intrface/bintro', 'main menu script BINTROE (+ language letter e)'),
    ('intrface/newgame', 'new-game / mission-selection script NEWGAMEE'),
    ('intrface/story', 'story screen script STORYE'),
    ('intrface/encyclo', 'encyclopedia screen script ENCYCLOE'),
    ('intrface/shuman', 'campaign map script SHUMANE'),
    ('intrface/loadg', 'load-game screen script LOADGE'),
    ('intrface/wingame', 'end-of-game statistics script WINGAMEE'),
    ('intrface/multiwn', 'multiplayer results script MULTIWNE'),
    ('intrface/intrg.dat', 'main menu FIN list INTRG.DAT (INTRF_HD copy names dcuk_hd.fin, dcut_hd.fin)'),
    ('intrface/dpblank', 'direct-play blank screen script DPBLANKE'),
    ('intrface/ipxname', 'player-name screen script IPXNAMEE'),
    ('intrface/dplays', 'direct-play session screen script DPLAYSE'),
    ('intrface/getsvr', 'server-address screen script GETSVRE'),
    ('intrface/netopt', 'network options screen script NETOPTE'),
    ('intrface/meta', 'lobby meta screen script METAE'),
    ('intrface/lost', 'defeat screen script LOSTE'),
    ('intrface/multi', 'multiplayer lobby script MULTIE'),
    ('intrface/main', 'in-game HUD script MAINE (background intrf_hd/intrface = the rebuilt frame)'),
    ('intrface/load.bmp', 'first loading screen LOAD.BMP (opened by driver.c directly)'),
    ('intrface/load2.bmp', 'second loading screen LOAD2.BMP'),
    ('intrface/lsg', 'in-game save/load dialog script LSGE'),
    ('intrface/lobj', 'in-game objectives dialog script LOBJE'),
    ('intrface/lqc', 'in-game quit dialog script LQCE'),
    ('intrface/lopt', 'in-game options dialog script LOPTE'),
    ('gamestat/hscene', 'human campaign briefing list HSCENE.TXT (letterboxed globe markers)'),
    ('gamestat/gscene', 'alien campaign briefing list GSCENE.TXT'),
    ('gamestat/htscene', 'human training briefing list HTSCENE.TXT'),
    ('gamestat/gtscene', 'alien training briefing list GTSCENE.TXT'),
    ('gamestat/hxscene', 'Council Wars human campaign briefing list HXSCENE.TXT (exp/ overlay)'),
    ('gamestat/gxscene', 'Council Wars alien campaign briefing list GXSCENE.TXT (exp/ overlay)'),
]
IMAGE_BASE = 0x400000


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


def find_sites(data):
    """[(file offset of the 8-byte directory part, stock string, what, state)] in exe order;
    state is 'stock' or 'patched'.  Every string must occur exactly once in one of the two forms."""
    va, rsize, rptr = sections(data)['DGROUP']
    dgroup = bytes(data[rptr:rptr + rsize])
    out = []
    for s, what in REDIRECT:
        stock = s.encode() + b'\0'
        patched = NEW_DIR + stock[8:]
        hits = [(dgroup.find(stock), 'stock'), (dgroup.find(patched), 'patched')]
        hits = [(o, st) for o, st in hits if o >= 0]
        for needle in (stock, patched):
            if dgroup.count(needle) > 1:
                raise SystemExit('%r occurs %d times in DGROUP' % (needle, dgroup.count(needle)))
        if len(hits) != 1:
            raise SystemExit('expected exactly one of %r / %r in DGROUP, found %d' % (stock, patched, len(hits)))
        off, state = hits[0]
        # the byte before must be a NUL or the start of the section: a whole string, not a suffix
        if off > 0 and dgroup[off - 1] != 0:
            raise SystemExit('%r is not at a string start' % stock)
        out.append((rptr + off, s, what, state))
    out.sort()
    return out, va - rptr


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    sites, delta = find_sites(data)
    states = {st for _, _, _, st in sites}
    state = 'stock (reads INTRFACE/, GAMESTAT/)' if states == {'stock'} else \
        'patched (reads INTRF_HD/)' if states == {'patched'} else 'MIXED - %d of %d strings patched' % (
            sum(1 for s in sites if s[3] == 'patched'), len(sites))
    print('%s: interface paths %s' % (a.exe, state))
    if a.command == 'verify':
        return 0
    for off, s, what, st in sites:
        new = NEW_DIR.decode() + s[8:]
        print('  "%s" -> "%s"  file 0x%x VA 0x%x 8 bytes: %s%s'
              % (s, new, off, off + delta + IMAGE_BASE, what, '' if st == 'stock' else '  [already patched]'))
    if a.command == 'plan':
        return 0
    todo = [(off, s) for off, s, _, st in sites if st == 'stock']
    if not todo:
        print('nothing to do')
        return 0
    bak = a.exe + '.hdpaths.bak'
    shutil.copyfile(a.exe, bak)
    for off, s in todo:
        assert bytes(data[off:off + 8]) == s[:8].encode()
        data[off:off + 8] = NEW_DIR
    open(a.exe, 'wb').write(data)
    print('written %s (%d strings); backup %s' % (a.exe, len(todo), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

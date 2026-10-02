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

import argparse, re
import hdfolder
import shutil
import struct
import sys

NEW_DIR = b'intrf_hd'      # the legacy shared folder (14 Sep - 1 Oct 2026); since 2 Oct 2026 the resolution's own folder,
                           # hdfolder.hd_token(width, height) = hd_0768p, hd_1080p, uw_1080p ... (set by main from --width/--height or --folder)
DIR_PATTERN = rb'(intrface|gamestat|intrf_hd|(?:hd|uw)_[0-9]{4}p)'
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
        rest = stock[8:]                       # "/bintro\0": the part after the 8-byte directory
        pat = re.compile(DIR_PATTERN + re.escape(rest))
        # a whole string (the byte before is a NUL or the section start), in the stock form or pointing at any
        # interface folder a patcher ever wrote (intrf_hd, hd_0768p, ...)
        hits = [(m.start(), m.group(1)) for m in pat.finditer(dgroup) if m.start() == 0 or dgroup[m.start() - 1] == 0]
        if len(hits) != 1:
            raise SystemExit('expected exactly one string "<dir>%s" in DGROUP, found %d' % (rest[:-1].decode(), len(hits)))
        off, d = hits[0]
        state = 'stock' if d in (b'intrface', b'gamestat') else 'patched' if d == NEW_DIR else 'other:' + d.decode()
        out.append((rptr + off, s, what, state))
    out.sort()
    return out, va - rptr


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    ap.add_argument('--width', type=int, help='screen width: the folder is hdfolder.hd_token(width, height), e.g. hd_1080p')
    ap.add_argument('--height', type=int, help='screen height')
    ap.add_argument('--folder', help='the 8-character folder name explicitly (default without --width/--height: the legacy intrf_hd)')
    a = ap.parse_args(argv)
    global NEW_DIR
    if a.folder:
        NEW_DIR = a.folder.lower().encode()
    elif a.width and a.height:
        NEW_DIR = hdfolder.hd_token(a.width, a.height).encode()
    if len(NEW_DIR) != 8:
        raise SystemExit('the interface folder name must have exactly 8 characters (in-place rewrite of the path strings): %r' % NEW_DIR)
    data = bytearray(open(a.exe, 'rb').read())
    sites, delta = find_sites(data)
    states = {st for _, _, _, st in sites}
    others = sorted({st[6:] for st in states if st.startswith('other:')})
    state = 'stock (reads INTRFACE/, GAMESTAT/)' if states == {'stock'} else \
        'patched (reads %s/)' % NEW_DIR.decode().upper() if states == {'patched'} else \
        'pointing at another interface folder (%s/) - apply re-points them at %s/' % ('/, '.join(o.upper() for o in others), NEW_DIR.decode().upper()) if states == {'other:' + o for o in others} else \
        'MIXED - %d of %d strings read %s/' % (sum(1 for s in sites if s[3] == 'patched'), len(sites), NEW_DIR.decode().upper())
    print('%s: interface paths %s' % (a.exe, state))
    if a.command == 'verify':
        return 0
    for off, s, what, st in sites:
        new = NEW_DIR.decode() + s[8:]
        print('  "%s" -> "%s"  file 0x%x VA 0x%x 8 bytes: %s%s'
              % (s, new, off, off + delta + IMAGE_BASE, what, '' if st == 'stock' else '  [already patched]' if st == 'patched' else '  [points at %s]' % st[6:]))
    if a.command == 'plan':
        return 0
    todo = [(off, s) for off, s, _, st in sites if st != 'patched']
    if not todo:
        print('nothing to do')
        return 0
    bak = a.exe + '.hdpaths.bak'
    shutil.copyfile(a.exe, bak)
    for off, s in todo:
        assert re.fullmatch(DIR_PATTERN, bytes(data[off:off + 8])), bytes(data[off:off + 8])
        data[off:off + 8] = NEW_DIR
    open(a.exe, 'wb').write(data)
    print('written %s (%d strings); backup %s' % (a.exe, len(todo), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

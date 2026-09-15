#!/usr/bin/env python3
"""Unlock the greyed-out controls of the Dark Colony map editor (maped.exe) - the functional part of
the ozi_ns editor, without its Polish translation.

Investigated 15 Sep 2026 (CLAUDE.md "Map editor notes"): `maped_by_ozy_ns_v1.2PL.exe` has the very same
code as the original `maped.exe`; besides Polish dialog/menu texts, a new icon and retitled window
strings, its only functional change is the WS_DISABLED style bit (0x08000000) cleared on controls the
original shipped greyed out.  The unchanged code already handles every one of them.  This tool applies
exactly those style-bit edits to the English original, one byte per control (byte 3 of the control's
style dword in the DIALOG template, 0x58 -> 0x50), locating the controls by walking the PE resource
tree and the DLGTEMPLATE, so nothing is hard-wired to file offsets.

Fixes (ids as used by Apply-DarkColonyPatches.ps1):
  blocksets    New Map dialog (MAPSIZE): push buttons 16 Atlantis, 21 Training Set, 22 Special Set.  The
               WM_COMMAND jump table at 0x4122A5 routes them to block sets 2/3/4 (atlantis/htrain/special
               .bts) exactly like Desert and Jungle.  They need scenario\\atlantis.set, trainh.set,
               special.set + special.bts beside the editor (the ozi_ns pack ships them; the game does not),
               otherwise the editor shows "Can't open file".
  teams        Team Attributes dialog (RACE): the Team Colour group (radios 108-115 + group box 116) and the
               Allies group (radios 117-124 + group box 125).  The dialog procedure at 0x41E7B9 reads both
               groups (IsDlgButtonChecked loop 0x41E983..0x41E9D3) and writes %TeamColour / %TeamAllies.
  healer       Troop Attributes dialog (TROOPS): the Healer row - radio 508 and hit-points edit 678, which
               the dialog procedure at 0x41F88B reads (GetDlgItemTextA at 0x41FE16).  The game's tables
               know the healing units (GAMESTAT.TXT rows 49/50).
  troopsframe  TROOPS dialog frame: WS_THICKFRAME (0x00040000) -> WS_SYSMENU (0x00080000), i.e. a close box
               instead of a sizing border (byte 2 of the template style dword, 0xC4 -> 0xC8).

CLI
    python patch_maped.py verify EXE [--fix ID|all]
    python patch_maped.py plan   EXE [--fix ID|all]
    python patch_maped.py apply  EXE [--fix ID|all]      (writes EXE.unlock.bak first)
"""

import argparse
import shutil
import struct
import sys

WS_DISABLED_BYTE = 3          # style dword byte that holds bit 27
FIXES = {
    'blocksets': ('MAPSIZE', [(16, 'Atlantis'), (21, 'Training Set'), (22, 'Special Set')]),
    'teams': ('RACE', [(108, '0 Red'), (109, '1 Blue'), (110, '2 Yellow'), (111, '3 Purple'), (112, '4 Green'),
                       (113, '5 Orange'), (114, '6 Flesh'), (115, '7 Teal'), (116, 'Team Colour'),
                       (117, '0 Red'), (118, '1 Blue'), (119, '2 Yellow'), (120, '3 Purple'), (121, '4 Green'),
                       (122, '5 Orange'), (123, '6 Flesh'), (124, '7 Teal'), (125, 'Allies')]),
    'healer': ('TROOPS', [(508, ''), (678, '')]),
    'troopsframe': ('TROOPS', None),          # dialog style itself
}
ORDER = ['blocksets', 'teams', 'healer', 'troopsframe']
FRAME_OLD, FRAME_NEW = 0xC4, 0xC8            # 0x90C400C0 -> 0x90C800C0, byte 2


def sections(data):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    opt = struct.unpack_from('<H', data, pe + 20)[0]
    st = pe + 24 + opt
    out = {}
    for i in range(nsec):
        s = data[st + i * 40: st + i * 40 + 40]
        vsize, va, rsize, rptr = struct.unpack_from('<IIII', s, 8)
        out[s[:8].rstrip(b'\0').decode()] = (va, vsize, rsize, rptr)
    return out


def resources(data):
    """{(type, name, lang): (file_offset, size)} of every resource data entry."""
    va, vsize, rsize, rp = sections(data)['.rsrc']
    out = {}

    def walk(off, path):
        nn, ni = struct.unpack_from('<HH', data, rp + off + 12)
        for k in range(nn + ni):
            name, child = struct.unpack_from('<II', data, rp + off + 16 + k * 8)
            if name & 0x80000000:
                so = name & 0x7FFFFFFF
                n = struct.unpack_from('<H', data, rp + so)[0]
                key = data[rp + so + 2: rp + so + 2 + n * 2].decode('utf-16le')
            else:
                key = name
            if child & 0x80000000:
                walk(child & 0x7FFFFFFF, path + (key,))
            else:
                rva, size, _cp = struct.unpack_from('<III', data, rp + child)
                out[path + (key,)] = (rva - va + rp, size)
    walk(0, ())
    return out


def _wstr(data, o):
    s = ''
    while True:
        c = struct.unpack_from('<H', data, o)[0]
        o += 2
        if c == 0:
            return s, o
        s += chr(c)


def _sz_or_ord(data, o):
    w = struct.unpack_from('<H', data, o)[0]
    if w == 0:
        return '', o + 2
    if w == 0xFFFF:
        return '#%d' % struct.unpack_from('<H', data, o + 2)[0], o + 4
    return _wstr(data, o)


def dialog(data, off):
    """DLGTEMPLATE at file offset `off` -> (style_offset, [(id, text, style_offset)])."""
    style, _ex, n = struct.unpack_from('<IIH', data, off)
    o = off + 18
    _menu, o = _sz_or_ord(data, o)
    _cls, o = _sz_or_ord(data, o)
    _title, o = _wstr(data, o)
    if style & 0x40:                       # DS_SETFONT
        o += 2
        _font, o = _wstr(data, o)
    items = []
    for _ in range(n):
        o = (o + 3) & ~3
        so = o
        _cs, _cex, _x, _y, _w, _h, cid = struct.unpack_from('<IIhhhhH', data, o)
        o += 18
        _ccls, o = _sz_or_ord(data, o)
        text, o = _sz_or_ord(data, o)
        extra = struct.unpack_from('<H', data, o)[0]
        o += 2 + extra
        items.append((cid, text, so))
    return off, items


def edits(data, fixes):
    """[(fix, file_offset, old_byte, new_byte, note)] for the requested fixes, from the resource tree."""
    res = resources(data)
    out = []
    for fix in fixes:
        dlg, controls = FIXES[fix]
        key = (5, dlg, 0)
        if key not in res:
            raise SystemExit('DIALOG %s not found in the resource tree - not a maped.exe' % dlg)
        doff, items = dialog(data, res[key][0])
        if controls is None:
            out.append((fix, doff + 2, FRAME_OLD, FRAME_NEW,
                        'DIALOG %s template style byte 2: WS_THICKFRAME (0x00040000) -> WS_SYSMENU (0x00080000), sizing border -> close box' % dlg))
            continue
        for cid, text in controls:
            hits = [it for it in items if it[0] == cid and it[1] == text]
            if len(hits) != 1:
                raise SystemExit('DIALOG %s: control id %d %r found %d times (English original expected)' % (dlg, cid, text, len(hits)))
            so = hits[0][2] + WS_DISABLED_BYTE
            out.append((fix, so, 0x58, 0x50,
                        'DIALOG %s control id %d %s: style byte 3, WS_DISABLED (0x08000000) cleared - the control is usable' % (dlg, cid, ('"%s"' % text) if text else '(no text)')))
    return out


def state_of(data, off, old, new):
    b = data[off]
    return 'stock' if b == old else 'patched' if b == new else 'other(%02X)' % b


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    ap.add_argument('--fix', default='all', help='|'.join(ORDER) + '|all (default all)')
    a = ap.parse_args(argv)
    fixes = ORDER if a.fix == 'all' else [a.fix]
    if any(f not in FIXES for f in fixes):
        raise SystemExit('unknown fix id; valid: %s' % ', '.join(ORDER))
    data = bytearray(open(a.exe, 'rb').read())
    if 'CODE' not in sections(data) or '.rsrc' not in sections(data):
        raise SystemExit('%s: not a Borland-linked maped.exe (no CODE/.rsrc sections)' % a.exe)
    ed = edits(data, fixes)
    for fix in fixes:
        mine = [e for e in ed if e[0] == fix]
        states = {state_of(data, o, old, new) for _f, o, old, new, _n in mine}
        print('%s: [%s] %s (%d edits)' % (a.exe, fix, '/'.join(sorted(states)), len(mine)))
    if a.command == 'verify':
        return 0
    for fix, off, old, new, note in ed:
        print('  [%s] %s  file 0x%x 1 byte: %02x -> %02x' % (fix, note, off, old, new))
    if a.command == 'plan':
        return 0
    todo = [e for e in ed if state_of(data, e[1], e[2], e[3]) == 'stock']
    bad = [e for e in ed if state_of(data, e[1], e[2], e[3]).startswith('other')]
    if bad:
        raise SystemExit('unexpected bytes at %s - refusing' % ', '.join('0x%x' % e[1] for e in bad))
    if not todo:
        print('nothing to do (already applied)')
        return 0
    bak = a.exe + '.unlock.bak'
    shutil.copyfile(a.exe, bak)
    for _fix, off, _old, new, _note in todo:
        data[off] = new
    open(a.exe, 'wb').write(data)
    print('written %s (%d bytes changed); backup %s' % (a.exe, len(todo), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

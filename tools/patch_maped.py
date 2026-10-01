#!/usr/bin/env python3
"""Unlock the greyed-out controls and menu items of the Dark Colony map editor (maped.exe).

The editor is the developers' campaign tool shipped as a multiplayer map editor: it imports neither
EnableWindow nor EnableMenuItem, so every greyed control is a static flag in the resources (WS_DISABLED,
0x08000000, in the DIALOG templates; MF_GRAYED, 0x0001, in MAINMENU) and the code behind almost all of
them is complete (DC16_MAP_FILES.md section 13, 1 Oct 2026).  This tool clears those flags, one byte
per control or menu item, locating everything by walking the PE resource tree, the DLGTEMPLATEs and
the MENU template - nothing is hard-wired to file offsets.  Two menu items are re-pointed as well:
"City" (235) and "Troops" (236) under Team Attributes had no handler at all; the City State and Troop
Attributes dialogs hang on the WM_COMMAND ids 166 and 167 that nothing sent (one byte each).

Fixes (ids as used by Apply-DarkColonyPatches.ps1), the first four = the 15 Sep 2026 ozi_ns set:
  blocksets    New Map dialog (MAPSIZE): push buttons 16 Atlantis, 21 Training Set, 22 Special Set.  The
               WM_COMMAND jump table at 0x4122A5 routes them to block sets 2/3/4 (atlantis/htrain/special
               .bts) exactly like Desert and Jungle.  They need scenario\\atlantis.set, trainh.set,
               special.set + special.bts beside the editor (the ozi_ns pack ships them; the game does not),
               otherwise the editor shows "Can't open file".
  teams        Team Attributes dialog (RACE): the Team Colour group (radios 108-115 + group box 116) and the
               Allies group (radios 117-124 + group box 125).  RaceDialogProc 0x41E7B9 reads both groups
               and writes %TeamColour / %TeamAllies.
  healer       Troop Attributes dialog (TROOPS): the Healer row - radio 508 and hit-points edit 678.  The
               dialog handles the row (nine rows, 0x41FAA1), SaveScenario writes rows 0..7 only.
  troopsframe  TROOPS dialog frame: WS_THICKFRAME (0x00040000) -> WS_SYSMENU (0x00080000), i.e. a close box
               instead of a sizing border (byte 2 of the template style dword, 0xC4 -> 0xC8).
  race         RACE dialog, the rest: AI Type (group 106, edit 107 -> %AI), AI Slots (edits 126-140, the
               fifteen "Slot n" labels, group 141 -> %AISlots).  The game reads %AI in campaign games only
               and drops %AISlots.
  campaign     Scenario Stats dialog (TOD): the "Campaign" radio 107.  Scenario type 1 = the editor writes no
               .TRO and no automatic commanders (campaign scenario), type 0 = multiplayer.
  medfiles     Menu: Super Gen (237, batch re-export of every map in dirlist.txt), Load MED File (233) and
               Save MED File (103) - the editor's own binary document.
  blockmenu    Menu: the Block Type popup (its 18 items double the toolbar's block-type buttons).
  teamdialogs  Menu: City (235) and Troops (236) under Team Attributes un-greyed AND re-pointed to the
               command ids 166 / 167 that open the City State (city slots: none/buildable/prebuilt, hit
               points, money) and Troop Attributes (per-troop weapon/armour level) dialogs.
  lieutenants  Menu: Human Leutenant (160) and Alien Leutenant (161) - commander placement by hand (the
               multiplayer save also generates one per active team at its start position).
  artifacts    Menu: Solar Lens .. Ultimate (180-185) - loose artifacts, type 63..68 objects.
  lights       Menu: the Lights popup (direction and type of the twelve LIGHT objects 51..62).
  trigger      Toolbar Trigger button (TOOLMAIN 117, trigger ids into the .MTG) + menu Edit Trigger String
               (164).
  aiflags      Toolbar Flag button (TOOLMAIN 144) + the AI Flags popup.  WARNING: a flag is saved as the
               object line "x z 47 (type-100) priority" and type 47 is the Human mining tower in the shipped
               GAMESTAT.TXT - the game has no flag object.  Offered for completeness, off by choice.

Not touched: Edit Text (170) has no handler in WndProc and its dialog procedure is referenced nowhere, so
un-greying it would give a no-op menu item; the ATTRIB2 dialog (34 greyed radios) is never created.

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
MF_GRAYED = 0x01
MF_DISABLED = 0x02
MF_POPUP = 0x10
MF_END = 0x80

# A fix = list of edit specs:
#   ('dlg', DIALOG, [(id, text), ...])       clear WS_DISABLED on these controls (byte 3: 0x58 -> 0x50)
#   ('frame', DIALOG)                         TROOPS frame style byte 2: 0xC4 -> 0xC8
#   ('menu', [id|'popup text', ...])          clear MF_GRAYED|MF_DISABLED on these MAINMENU entries
#   ('menuid', [(old_id, new_id), ...])       re-point a MAINMENU item to another command id (low byte only)
SLOTS = [(0xFFFF, 'Slot %d' % k) for k in range(1, 16)]
FIXES = {
    'blocksets': [('dlg', 'MAPSIZE', [(16, 'Atlantis'), (21, 'Training Set'), (22, 'Special Set')])],
    'teams': [('dlg', 'RACE', [(108, '0 Red'), (109, '1 Blue'), (110, '2 Yellow'), (111, '3 Purple'), (112, '4 Green'),
                               (113, '5 Orange'), (114, '6 Flesh'), (115, '7 Teal'), (116, 'Team Colour'),
                               (117, '0 Red'), (118, '1 Blue'), (119, '2 Yellow'), (120, '3 Purple'), (121, '4 Green'),
                               (122, '5 Orange'), (123, '6 Flesh'), (124, '7 Teal'), (125, 'Allies')])],
    'healer': [('dlg', 'TROOPS', [(508, ''), (678, '')])],
    'troopsframe': [('frame', 'TROOPS')],
    'race': [('dlg', 'RACE', [(106, 'AI Type'), (107, '')] + [(i, '') for i in range(126, 141)] + SLOTS + [(141, 'AI Slots')])],
    'campaign': [('dlg', 'TOD', [(107, 'Campaign')])],
    'medfiles': [('menu', [237, 233, 103])],
    'blockmenu': [('menu', ['&Block Type'])],
    'teamdialogs': [('menu', [235, 236]), ('menuid', [(235, 166), (236, 167)])],
    'lieutenants': [('menu', [160, 161])],
    'artifacts': [('menu', [180, 181, 182, 183, 184, 185])],
    'lights': [('menu', ['&Lights'])],
    'trigger': [('dlg', 'TOOLMAIN', [(117, 'Trigger')]), ('menu', [164])],
    'aiflags': [('dlg', 'TOOLMAIN', [(144, 'Flag')]), ('menu', ['AI Flags'])],
}
ORDER = ['blocksets', 'teams', 'healer', 'troopsframe', 'race', 'campaign', 'medfiles', 'blockmenu',
         'teamdialogs', 'lieutenants', 'artifacts', 'lights', 'trigger', 'aiflags']
FRAME_OLD, FRAME_NEW = 0xC4, 0xC8            # 0x90C400C0 -> 0x90C800C0, byte 2
REPOINTED = {235: 166, 236: 167}            # menu items whose command id the teamdialogs fix changes (lookup on a patched exe)


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
    """{(type, name, lang): (file_offset, size)} of every resource data entry of the original .rsrc
    directory (the `icon` fix appends a second directory in .dcicon that points at the same data)."""
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


def menu(data, off):
    """MENU template at file offset `off` -> [(key, text, flags_offset, id_offset|None)], key = id or popup text."""
    _ver, hdr = struct.unpack_from('<HH', data, off)
    out = []

    def items(o):
        while True:
            flags = struct.unpack_from('<H', data, o)[0]
            if flags & MF_POPUP:
                text, n = _wstr(data, o + 2)
                out.append((text, text, o, None))
                n = items(n)
            else:
                mid = struct.unpack_from('<H', data, o + 2)[0]
                text, n = _wstr(data, o + 4)
                out.append((mid, text, o, o + 2))
            if flags & MF_END:
                return n
            o = n
    items(off + 4 + hdr)
    return out


def edits(data, fixes):
    """[(fix, file_offset, old_byte, new_byte, note)] for the requested fixes, from the resource tree."""
    res = resources(data)
    menu_items = None
    out = []
    for fix in fixes:
        for spec in FIXES[fix]:
            kind = spec[0]
            if kind in ('dlg', 'frame'):
                dlg = spec[1]
                key = (5, dlg, 0)
                if key not in res:
                    raise SystemExit('DIALOG %s not found in the resource tree - not a maped.exe' % dlg)
                doff, items = dialog(data, res[key][0])
                if kind == 'frame':
                    out.append((fix, doff + 2, FRAME_OLD, FRAME_NEW,
                                'DIALOG %s template style byte 2: WS_THICKFRAME (0x00040000) -> WS_SYSMENU (0x00080000), sizing border -> close box' % dlg))
                    continue
                for cid, text in spec[2]:
                    hits = [it for it in items if it[0] == cid and it[1] == text]
                    if len(hits) != 1:
                        raise SystemExit('DIALOG %s: control id %d %r found %d times (English original expected)' % (dlg, cid, text, len(hits)))
                    so = hits[0][2] + WS_DISABLED_BYTE
                    label = ('"%s"' % text) if text else '(no text)'
                    if cid == 0xFFFF:
                        label = 'label ' + label
                        cid_s = ''
                    else:
                        cid_s = 'id %d ' % cid
                    out.append((fix, so, 0x58, 0x50,
                                'DIALOG %s control %s%s: style byte 3, WS_DISABLED (0x08000000) cleared - the control is usable' % (dlg, cid_s, label)))
            else:
                if menu_items is None:
                    key = (4, 'MAINMENU', 0)
                    if key not in res:
                        raise SystemExit('MENU MAINMENU not found in the resource tree - not a maped.exe')
                    menu_items = menu(data, res[key][0])
                if kind == 'menu':
                    for k in spec[1]:
                        hits = [it for it in menu_items if it[0] in (k, REPOINTED.get(k))]
                        if len(hits) != 1:
                            raise SystemExit('MENU MAINMENU: entry %r found %d times (English original expected)' % (k, len(hits)))
                        key_, text, fo, _io = hits[0]
                        old = data[fo]
                        what = 'popup "%s"' % text if isinstance(k, str) else 'item %d "%s"' % (k, text)
                        out.append((fix, fo, old, old & ~(MF_GRAYED | MF_DISABLED),
                                    'MENU MAINMENU %s: flags byte 0, MF_GRAYED (0x0001) cleared - the entry is selectable' % what))
                else:   # menuid
                    for old_id, new_id in spec[1]:
                        hits = [it for it in menu_items if it[0] in (old_id, new_id)]
                        if len(hits) != 1:
                            raise SystemExit('MENU MAINMENU: item %d found %d times (English original expected)' % (old_id, len(hits)))
                        _k, text, _fo, io = hits[0]
                        assert old_id >> 8 == new_id >> 8
                        out.append((fix, io, old_id & 0xFF, new_id & 0xFF,
                                    'MENU MAINMENU item "%s": command id %d -> %d (the id its dialog handler in WndProc listens to)' % (text, old_id, new_id)))
    return out


def state_of(data, off, old, new):
    b = data[off]
    return 'stock' if b == old else 'patched' if b == new else 'other(%02X)' % b


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    ap.add_argument('--fix', default='all', help='|'.join(ORDER) + '|all (default all); several: a,b,c')
    a = ap.parse_args(argv)
    fixes = ORDER if a.fix == 'all' else a.fix.split(',')
    if any(f not in FIXES for f in fixes):
        raise SystemExit('unknown fix id; valid: %s' % ', '.join(ORDER))
    data = bytearray(open(a.exe, 'rb').read())
    if 'CODE' not in sections(data) or '.rsrc' not in sections(data):
        raise SystemExit('%s: not a Borland-linked maped.exe (no CODE/.rsrc sections)' % a.exe)
    # the menu flag edits are computed against the ORIGINAL flag byte: on an already patched exe the
    # "old" would read as the new value, so derive them from the stock pattern (greyed bit set)
    ed = []
    for fix, off, old, new, note in edits(data, fixes):
        if 'MF_GRAYED' in note and not (old & MF_GRAYED):
            old, new = old | MF_GRAYED, old          # already cleared: reconstruct the stock byte
        ed.append((fix, off, old, new, note))
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

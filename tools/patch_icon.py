#!/usr/bin/env python3
"""Give a Dark Colony executable the high-resolution icon DC_HD.ICO (fix `icon`, 25 Sep 2026).

The exes carry at most the game's 32x32, 16-colour icon: dc16.exe and ENGEXP16.EXE one RT_ICON
(id 1) in the group "DC16", maped.exe none at all.  This tool gives each of them every image of
DC_HD.ICO (made by make_dc_icon.py from the geometry of DC.ICO; 16..64 as 32-bit bitmaps, 96 and 256
as PNG), which Explorer, the taskbar and desktop shortcuts show at every size.

How, without moving anything that is already there:
  * A NEW SECTION ".dcicon" is appended to the file (file offset = the end of the file rounded up to
    FileAlignment, VA = SizeOfImage).  It holds a complete resource directory - every resource of the
    exe as before, plus the new icons - followed by the icon images and the group directories.
  * The old resources stay where they are and are still used: the new directory's data entries for
    them point at their old RVAs (the map editor's dialogs, which the editor fixes edit in place, keep
    working and keep verifying).  Only the old RT_ICON / RT_GROUP_ICON entries are left out; their
    bytes in the old .rsrc become unused.
  * Headers: NumberOfSections + 1, the new 40-byte section header in the zero slack after the section
    table, SizeOfImage, and the resource data directory (RVA, size) pointing at the new section.
    Nothing else changes; no code, no .reloc entry (the section holds no absolute pointers).
  * Groups: the games keep their group name "DC16" and gain the same group under id 101 - the id
    create_window passes to LoadIconA (0x42E69E push 65h, Classic; the stock exes have no id 101,
    so the game window had the default icon); the map editor gets group id 1.

The file grows by the new section, so this is always the LAST fix of a build (the patcher appends
it after every other edit).

CLI
    python patch_icon.py verify EXE [--ico FILE]
    python patch_icon.py plan   EXE [--ico FILE]
    python patch_icon.py apply  EXE [--ico FILE]     (writes EXE.icon.bak first)
--ico defaults to DC_HD.ICO in the Council Wars game folder of the repository beside this tool's
repository (../../Dark-Colony/DC - Council wars/DC_HD.ICO).
"""
import argparse
import hashlib
import os
import shutil
import struct
import sys

SECTION_NAME = b'.dcicon'
RT_ICON, RT_GROUP_ICON = 3, 14
GAME_GROUP_ID = 101            # LoadIconA(hInstance, MAKEINTRESOURCE(101)) in create_window
DEFAULT_ICO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Dark-Colony', 'DC - Council wars', 'DC_HD.ICO')


def align(v, a):
    return (v + a - 1) // a * a


class PE:
    def __init__(self, data):
        self.d = data
        self.pe = struct.unpack_from('<I', data, 0x3C)[0]
        assert data[self.pe:self.pe + 4] == b'PE\0\0'
        self.nsec_off = self.pe + 6
        self.nsec = struct.unpack_from('<H', data, self.nsec_off)[0]
        self.opt = self.pe + 24
        optsize = struct.unpack_from('<H', data, self.pe + 20)[0]
        assert struct.unpack_from('<H', data, self.opt)[0] == 0x10B, 'PE32 expected'
        self.sect_align, self.file_align = struct.unpack_from('<II', data, self.opt + 32)
        self.size_of_image_off = self.opt + 56
        self.size_of_image = struct.unpack_from('<I', data, self.size_of_image_off)[0]
        self.size_of_headers = struct.unpack_from('<I', data, self.opt + 60)[0]
        self.res_dd_off = self.opt + 96 + 2 * 8
        self.res_rva, self.res_size = struct.unpack_from('<II', data, self.res_dd_off)
        self.st = self.opt + optsize
        self.secs = []
        for i in range(self.nsec):
            name, vs, va, rs, ro = struct.unpack_from('<8sIIII', data, self.st + 40 * i)
            self.secs.append(dict(name=name.rstrip(b'\0'), vs=vs, va=va, rs=rs, ro=ro))

    def r2o(self, rva):
        for s in self.secs:
            if s['va'] <= rva < s['va'] + max(s['vs'], s['rs']):
                return s['ro'] + rva - s['va']
        raise ValueError(hex(rva))


def read_resources(pe):
    """[(type, name, lang, data_rva, size, codepage)] - type/name: int id or str."""
    d = pe.d
    if not pe.res_rva:
        return []
    base = pe.r2o(pe.res_rva)
    out = []

    def key(k):
        if k & 0x80000000:
            so = base + (k & 0x7FFFFFFF)
            n = struct.unpack_from('<H', d, so)[0]
            return d[so + 2:so + 2 + 2 * n].decode('utf-16le')
        return k

    def walk(off, path):
        nn, ni = struct.unpack_from('<HH', d, base + off + 12)
        for i in range(nn + ni):
            k, p = struct.unpack_from('<II', d, base + off + 16 + 8 * i)
            if p & 0x80000000:
                walk(p & 0x7FFFFFFF, path + [key(k)])
            else:
                rva, size, cp, _ = struct.unpack_from('<IIII', d, base + p)
                out.append((*path, key(k), rva, size, cp))
    walk(0, [])
    return out


def read_ico(path):
    d = open(path, 'rb').read()
    _, typ, n = struct.unpack_from('<HHH', d, 0)
    assert typ == 1, 'not an .ico'
    imgs = []
    for i in range(n):
        w, h, cc, _, planes, bpp, size, off = struct.unpack_from('<BBBBHHII', d, 6 + 16 * i)
        imgs.append(dict(w=w, h=h, cc=cc, planes=planes, bpp=bpp, data=d[off:off + size]))
    return imgs


def build_directory(entries, sec_rva):
    """Serialise a resource tree.  entries: [(type, name, lang, payload)] where payload is either
    ('rva', rva, size, cp) (existing data, left in place) or ('bytes', b'...') (stored in this section).
    Returns the section bytes (directory + strings + new data), 4-aligned blobs."""
    def sort_key(k):
        return (0, k.upper()) if isinstance(k, str) else (1, k)
    tree = {}
    for t, n, l, p in entries:
        tree.setdefault(t, {}).setdefault(n, {})[l] = p
    # sizes: directories first (breadth-first), then data entries, then strings, then blobs
    dirs = []                    # (key path, children keys)
    order_types = sorted(tree, key=sort_key)
    dirs.append(((), order_types))
    for t in order_types:
        dirs.append(((t,), sorted(tree[t], key=sort_key)))
    for t in order_types:
        for n in sorted(tree[t], key=sort_key):
            dirs.append(((t, n), sorted(tree[t][n], key=sort_key)))
    dir_off = {}
    off = 0
    for path, kids in dirs:
        dir_off[path] = off
        off += 16 + 8 * len(kids)
    leaves = [(t, n, l) for t in order_types for n in sorted(tree[t], key=sort_key) for l in sorted(tree[t][n], key=sort_key)]
    data_entry_off = {}
    for lf in leaves:
        data_entry_off[lf] = off
        off += 16
    strings = {}
    for path, kids in dirs:
        for k in kids:
            if isinstance(k, str) and k not in strings:
                strings[k] = off
                off += 2 + 2 * len(k)
    off = align(off, 8)
    blob_off = {}
    for lf in leaves:
        p = tree[lf[0]][lf[1]][lf[2]]
        if p[0] == 'bytes':
            blob_off[lf] = off
            off = align(off + len(p[1]), 8)
    out = bytearray(off)
    for path, kids in dirs:
        o = dir_off[path]
        named = sum(1 for k in kids if isinstance(k, str))
        struct.pack_into('<IIHHHH', out, o, 0, 0, 0, 0, named, len(kids) - named)
        for i, k in enumerate(kids):
            name_field = (0x80000000 | strings[k]) if isinstance(k, str) else k
            child = path + (k,)
            ptr = (0x80000000 | dir_off[child]) if len(child) < 3 else data_entry_off[child]
            struct.pack_into('<II', out, o + 16 + 8 * i, name_field, ptr)
    for s, o in strings.items():
        struct.pack_into('<H', out, o, len(s))
        out[o + 2:o + 2 + 2 * len(s)] = s.encode('utf-16le')
    for lf in leaves:
        p = tree[lf[0]][lf[1]][lf[2]]
        if p[0] == 'rva':
            struct.pack_into('<IIII', out, data_entry_off[lf], p[1], p[2], p[3], 0)
        else:
            struct.pack_into('<IIII', out, data_entry_off[lf], sec_rva + blob_off[lf], len(p[1]), 0, 0)
            out[blob_off[lf]:blob_off[lf] + len(p[1])] = p[1]
    return bytes(out)


def is_applied(pe):
    return any(s['name'] == SECTION_NAME for s in pe.secs)


def plan_edits(data, ico_path):
    """Return (header edits [(offset, old, new, note)], append offset, append bytes, summary)."""
    pe = PE(data)
    if is_applied(pe):
        raise SystemExit('already applied: the exe has a %s section' % SECTION_NAME.decode())
    imgs = read_ico(ico_path)
    old = read_resources(pe)
    kept = [(t, n, l, ('rva', rva, size, cp)) for t, n, l, rva, size, cp in old if t not in (RT_ICON, RT_GROUP_ICON)]
    old_groups = [n for t, n, l, *_ in old if t == RT_GROUP_ICON]
    langs = [l for t, n, l, *_ in old]
    lang = max(set(langs), key=langs.count) if langs else 0
    if old_groups:
        group_names = list(dict.fromkeys(old_groups)) + [GAME_GROUP_ID]      # the games: "DC16" + 101
        lang = next(l for t, n, l, *_ in old if t == RT_GROUP_ICON)
    else:
        group_names = [1]                                                    # the map editor
    icon_entries = []
    grp = struct.pack('<HHH', 0, 1, len(imgs))
    for i, im in enumerate(imgs, 1):
        icon_entries.append((RT_ICON, i, lang, ('bytes', im['data'])))
        grp += struct.pack('<BBBBHHIH', im['w'], im['h'], im['cc'], 0, im['planes'], im['bpp'], len(im['data']), i)
    entries = kept + icon_entries + [(RT_GROUP_ICON, g, lang, ('bytes', grp)) for g in group_names]
    # new section placement
    last_end = max(s['va'] + align(max(s['vs'], s['rs']), pe.sect_align) for s in pe.secs)
    sec_rva = align(max(last_end, pe.size_of_image), pe.sect_align)
    body = build_directory(entries, sec_rva)
    raw_off = align(len(data), pe.file_align)
    raw_size = align(len(body), pe.file_align)
    append = b'\0' * (raw_off - len(data)) + body + b'\0' * (raw_size - len(body))
    # header edits
    sh_off = pe.st + 40 * pe.nsec
    assert sh_off + 40 <= pe.size_of_headers and data[sh_off:sh_off + 40] == b'\0' * 40, 'no free section header slot'
    for s in pe.secs:
        if s['ro'] and s['ro'] < sh_off + 40:
            raise SystemExit('section table slack overlaps section data')
    new_sh = struct.pack('<8sIIIIIIHHI', SECTION_NAME, len(body), sec_rva, raw_size, raw_off, 0, 0, 0, 0, 0x40000040)
    new_soi = sec_rva + align(len(body), pe.sect_align)
    edits = [
        (pe.nsec_off, data[pe.nsec_off:pe.nsec_off + 2], struct.pack('<H', pe.nsec + 1),
         f'PE header: NumberOfSections {pe.nsec} -> {pe.nsec + 1} (the new {SECTION_NAME.decode()} section)'),
        (pe.size_of_image_off, data[pe.size_of_image_off:pe.size_of_image_off + 4], struct.pack('<I', new_soi),
         f'optional header: SizeOfImage 0x{pe.size_of_image:X} -> 0x{new_soi:X}'),
        (pe.res_dd_off, data[pe.res_dd_off:pe.res_dd_off + 8], struct.pack('<II', sec_rva, len(body)),
         f'optional header: resource directory 0x{pe.res_rva:X} ({pe.res_size} bytes) -> 0x{sec_rva:X} ({len(body)} bytes), the new section'),
        (sh_off, data[sh_off:sh_off + 40], new_sh,
         f'section table: new header {SECTION_NAME.decode()} VA 0x{sec_rva:X} size 0x{len(body):X}, file 0x{raw_off:X} size 0x{raw_size:X}, initialised read-only data (0x40000040), in the zero slack after the last header'),
    ]
    sizes = ', '.join(f"{im['w'] or 256}" for im in imgs)
    kept_types = sorted({t for t, *_ in kept}, key=lambda k: (isinstance(k, str), k))
    summary = (f'new section {SECTION_NAME.decode()} at file 0x{raw_off:X} (VA 0x{sec_rva:X}), {len(append)} bytes appended: resource directory '
               f'(kept in place: {len(kept)} resources of types {", ".join(map(str, kept_types)) or "none"}; left out: '
               f'{sum(1 for t, *_ in old if t in (RT_ICON, RT_GROUP_ICON))} old icon entries), {len(imgs)} icon images ({sizes}) '
               f'and the icon group{"s" if len(group_names) > 1 else ""} {", ".join(repr(g) for g in group_names)} from {os.path.basename(ico_path)}')
    return edits, len(data), append, summary


def apply_bytes(data, edits, at, append):
    out = bytearray(data)
    for off, old, new, _ in edits:
        assert out[off:off + len(old)] == old
        out[off:off + len(new)] = new
    assert len(out) == at
    return bytes(out) + append


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cmd', choices=['verify', 'plan', 'apply'])
    ap.add_argument('exe')
    ap.add_argument('--ico', default=DEFAULT_ICO)
    a = ap.parse_args()
    data = open(a.exe, 'rb').read()
    pe = PE(data)
    if a.cmd == 'verify':
        if is_applied(pe):
            s = next(s for s in pe.secs if s['name'] == SECTION_NAME)
            icons = [r for r in read_resources(pe) if r[0] == RT_ICON]
            print(f'{a.exe}: icon APPLIED ({SECTION_NAME.decode()} at VA 0x{s["va"]:X}, {len(icons)} icon images)')
        else:
            print(f'{a.exe}: icon not applied ({sum(1 for r in read_resources(pe) if r[0] == RT_ICON)} icon images in the stock resources)')
        return
    edits, at, append, summary = plan_edits(data, a.ico)
    if a.cmd == 'plan':
        print(f'{a.exe}: {len(edits)} header edits + {len(append)} bytes appended at file 0x{at:X}')
        for off, old, new, note in edits:
            print(f'  {note}  file 0x{off:x} {len(old)} bytes: {old.hex(" ")} -> {new.hex(" ")}')
        print(f'  append at file 0x{at:x} {len(append)} bytes sha256 {hashlib.sha256(append).hexdigest()}: {summary}')
        return
    shutil.copyfile(a.exe, a.exe + '.icon.bak')
    open(a.exe, 'wb').write(apply_bytes(data, edits, at, append))
    print(f'{a.exe}: icon applied - {summary}')


if __name__ == '__main__':
    main()

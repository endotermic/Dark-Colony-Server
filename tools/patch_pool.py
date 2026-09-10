#!/usr/bin/env python3
"""Enlarge Dark Colony's single "local pool" (dc16.exe / DCEXP16.EXE): 11.5 MB -> 32 MB.

Everything the game keeps for the length of a session or a mission is carved out of one arena:
`smalloc.c` creates it once at start-up (`SMalloc_Pool`, 0x0040C0BC in Council Wars, size in
`eax` = 0x00AF79E0 = 11 500 000 bytes) and every subsystem takes stack-like blocks from it with
`SMalloc(pool, size, name)` (0x0040C0FC), asserting "SMalloc: Out of memory in local pool"
(`smalloc.c` line 97) when the arena is full.  The tenants, from the disassembly: every sprite
bank named by the start-up animation list (~7.2 MB of SPR cells in Council Wars), the screen
backgrounds ("Background memory", one framebuffer each - 786 KB at 1024x768 instead of 307 KB),
the lightplane and tile buffers of a mission, "kev: mapinfo" 633 KB, "coloursetup" 393 KB,
"gifbuffer" 256 KB, "Gamestate" 291 KB, the flat maps, the AI, the interface widgets.

At 640x480 the stock size had headroom.  The 1024x768 build eats part of it, and adding the
ozi_ns mission pack's four unit banks (+0.74 MB at start-up) was enough to hit the assert on the
mission-briefing screen (second game test, 10 Sep 2026).  The arena is one malloc, so the fix is
the constant: 32 MB.  Nothing else changes - block headers are 32-bit and the size check is
unsigned.  WAV data, for the record, is not in the pool (GlobalAlloc).

The site is found by its byte pattern (`mov eax,imm32; call SMalloc_Pool`) in the code section,
so the same tool serves both builds; it refuses to run unless exactly one site is found.

CLI
    python patch_pool.py verify EXE
    python patch_pool.py plan   EXE
    python patch_pool.py apply  EXE        (writes EXE.pool.bak first)
"""

import argparse
import re
import shutil
import struct
import sys

AUTO_VA_TO_FILE = 0x400C00
STOCK_SIZE = 0x00AF79E0            # 11 500 000
NEW_SIZE = 0x02000000              # 32 MiB


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
    """(file_offset, current_size) of the unique `mov eax,<pool size>; call rel32` site."""
    va, rsize, rptr = sections(data)['AUTO']
    auto = bytes(data[rptr:rptr + rsize])
    hits = []
    for size in (STOCK_SIZE, NEW_SIZE):
        pat = b'\xB8' + struct.pack('<I', size) + b'\xE8'
        hits += [(rptr + m.start(), size) for m in re.finditer(re.escape(pat), auto)]
    if len(hits) != 1:
        raise SystemExit('expected exactly one pool-size site, found %d: %s'
                         % (len(hits), ', '.join('%#x' % h[0] for h in hits)))
    return hits[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    off, size = find_site(data)
    state = 'stock (%.1f MB)' % (size / 1e6) if size == STOCK_SIZE else 'patched (%d MB)' % (size >> 20)
    print('%s: local pool %s, site file %#x VA %#x' % (a.exe, state, off, off + AUTO_VA_TO_FILE))
    if a.command == 'verify':
        return 0
    print('  %s -> %s' % ((b'\xB8' + struct.pack('<I', size)).hex(' '),
                          (b'\xB8' + struct.pack('<I', NEW_SIZE)).hex(' ')))
    if a.command == 'plan' or size == NEW_SIZE:
        if size == NEW_SIZE and a.command == 'apply':
            print('nothing to do')
        return 0
    bak = a.exe + '.pool.bak'
    shutil.copyfile(a.exe, bak)
    struct.pack_into('<I', data, off + 1, NEW_SIZE)
    open(a.exe, 'wb').write(data)
    print('written %s; backup %s' % (a.exe, bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

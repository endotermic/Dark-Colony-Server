#!/usr/bin/env python3
"""Move the in-game day/night clock hand to the 1024x768 HUD (dc16.exe / DCEXP16.EXE).

The HUD's round day/night dial is two things: the face is part of the frame artwork
(INTRFACE/INTRFACE.GIF, rebuilt by hud_layout.py), the *hand* is a 28x28 cell of
`sprites/cloc` (36 cells: 18 for the day half, 18 for the night half) that `clock.c` draws by
code, not by the MAINE script. `clock_draw` (Classic 0x0043AB38, Council Wars 0x0043AB98) turns
the phase counter `gs+0x530` into a cell index (`counter / (phase_length / 18)`, `+18` at night)
and, whenever the index changes, blits that cell with its **bottom-right corner anchored at
(608, 450)**: `mov eax,260h` / `mov edx,1C2h` minus the cell's width/height (DC16_DISPLAY_AND_
RESOLUTION.md 10.15). Neither number is 640 or 480, so the resolution sweep never saw them.

At 1024x768 the point (608, 450) is inside the map viewport, the terrain paints over the hand
every frame, and the face on the panel never moves. hud_layout.py slides the panel's bottom
cluster by (+384, +288) (right_panel insert=399), so the face now sits at (964..992, 710..738)
and the anchor has to become (608 + 384, 450 + 288) = (992, 738). The two immediates are plain
constants (no .reloc entries), so the patch is two dwords per exe.

The site is found by pattern, so the same tool serves both builds:
    BA <y> 00 00 00  66 8B 58 06  29 DA  89 D3  31 D2  66 8B 50 04  B8 <x> 00 00 00  29 D0
    (mov edx,ANCHOR_Y; mov bx,[eax+6]; sub edx,ebx; ...; mov dx,[eax+4]; mov eax,ANCHOR_X; sub eax,edx)

CLI
    python patch_clock.py verify EXE
    python patch_clock.py plan   EXE [--width 1024 --height 768]
    python patch_clock.py apply  EXE [--width 1024 --height 768]     (writes EXE.clock.bak first)
"""

import argparse
import re
import shutil
import struct
import sys

AUTO_VA_TO_FILE = 0x400C00
STOCK = (608, 450)                  # bottom-right anchor of the hand at 640x480
PATTERN = re.compile(
    rb'\xBA(....)'                              # mov edx, ANCHOR_Y
    rb'\x66\x8B\x58\x06'                        # mov bx, word ptr [eax+6]   (cell height)
    rb'\x29\xDA\x89\xD3\x31\xD2'                # sub edx,ebx; mov ebx,edx; xor edx,edx
    rb'\x66\x8B\x50\x04'                        # mov dx, word ptr [eax+4]   (cell width)
    rb'\xB8(....)'                              # mov eax, ANCHOR_X
    rb'\x29\xD0',                               # sub eax,edx
    re.S)


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
    """(file offset of the y dword, file offset of the x dword, current (x, y), VA of the mov edx)."""
    va, rsize, rptr = sections(data)['AUTO']
    auto = bytes(data[rptr:rptr + rsize])
    hits = list(PATTERN.finditer(auto))
    if len(hits) != 1:
        raise SystemExit('expected exactly one clock_draw anchor site, found %d' % len(hits))
    m = hits[0]
    yoff = rptr + m.start(1)
    xoff = rptr + m.start(2)
    y = struct.unpack('<I', m.group(1))[0]
    x = struct.unpack('<I', m.group(2))[0]
    return yoff, xoff, (x, y), rptr + m.start() + AUTO_VA_TO_FILE


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    ap.add_argument('--width', type=int, default=1024, help='screen width the HUD was rebuilt for')
    ap.add_argument('--height', type=int, default=768, help='screen height the HUD was rebuilt for')
    a = ap.parse_args(argv)
    target = (STOCK[0] + a.width - 640, STOCK[1] + a.height - 480)

    data = bytearray(open(a.exe, 'rb').read())
    yoff, xoff, cur, va = find_site(data)
    state = ('stock (640x480)' if cur == STOCK else
             'patched for %dx%d' % (cur[0] - STOCK[0] + 640, cur[1] - STOCK[1] + 480))
    print('%s: clock hand anchor (%d, %d) - %s' % (a.exe, cur[0], cur[1], state))
    print('  clock_draw anchor site VA %#x: y dword at file %#x, x dword at file %#x' % (va, yoff, xoff))
    if a.command == 'verify':
        return 0
    print('  -> anchor (%d, %d) for %dx%d' % (target[0], target[1], a.width, a.height))
    if a.command == 'plan':
        return 0
    if cur == target:
        print('nothing to do')
        return 0
    bak = a.exe + '.clock.bak'
    shutil.copyfile(a.exe, bak)
    struct.pack_into('<I', data, xoff, target[0])
    struct.pack_into('<I', data, yoff, target[1])
    open(a.exe, 'wb').write(data)
    print('written %s; backup %s' % (a.exe, bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

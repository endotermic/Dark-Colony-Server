#!/usr/bin/env python3
"""Survive lost DirectDraw surfaces during Dark Colony's start-up (dc16.exe / DCEXP16.EXE).

Symptom (13 Sep 2026, two-monitor PC): the game "hangs" right before the intro movie - black
screen, nothing happens.  What really happens: `SetDisplayMode(1024,768,16)` in `win_init`
makes Windows re-lay out the desktop (the second monitor's origin jumps from x=1920 to x=1024),
and that re-layout is a display change the game did not ask for, so DirectDraw marks every
exclusive-mode surface *lost* (`DDERR_SURFACELOST` 0x887601C2) about 0.5-2 s after the mode
switch.  The start-up path never restores them: the palette remap (`ddex4.c` ~line 1000-1036,
`remap` 0x0042F320) does GetDC/SetPixel/ReleaseDC/Lock/Unlock on the back buffer 256 times and
asserts on the first failure ("POO can't unlock in remap", line 1029; Lock line 1033; GetDC line
1036), and the loading screen (0x0042EEF6) asserts when its `Flip` fails ("Flip Problem", line
893).  The assert handler writes `error.log` and shows a MessageBoxA that sits *behind* the
exclusive-mode primary surface - hence the "hang".  One monitor: no re-layout, no loss.

The fix is to make those four failures non-fatal.  The game already restores lost surfaces on
its per-frame flip path (`0x0042E141` / `0x0042E291` -> `restore_surfaces` 0x0042E060, which
also reloads the 32 cursor bitmaps), so the first frame after the intro repairs everything by
itself; the remap loop only loses the 16-bit LUT entries of the palette indices it could not
read while the surface was lost, and `remap` is run again with every palette change.  (A
variant that restored the surfaces inside `remap` and retried was tried first: the process died
silently with exit code -1 a few seconds later - the restore inside the loading sequence upset
the DirectDraw emulation layer.  Skipping is what the maintainer confirmed in game.)

Edits (Classic VAs; Council Wars at +0x60, everything pattern-located):

    0x0042F394  push "POO can't unlock in remap"  ->  jmp 0x0042F486   (next palette index)
    0x0042F3F5  push fmt (Lock assert)            ->  jmp 0x0042F486
    0x0042F43E  push fmt (GetDC assert)           ->  jmp 0x0042F486
    0x0042F033  je  0x0042F090 (Flip ok)          ->  jmp 0x0042F090   (Flip failure not fatal)

The three `push imm32` operands carried HIGHLOW `.reloc` entries; they become
IMAGE_REL_BASED_ABSOLUTE padding (type 0, page offset kept so `verify` still finds them).
Nothing is written unless every site holds its expected bytes.  Doc: DC16_DISPLAY_AND_RESOLUTION.md 10.16.

CLI
    python patch_ddraw_lost.py verify EXE
    python patch_ddraw_lost.py plan   EXE
    python patch_ddraw_lost.py apply  EXE          (writes EXE.ddraw.bak first)
"""

import argparse
import re
import shutil
import struct
import sys

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


def jmp(src, dst):
    return b'\xE9' + struct.pack('<i', dst - (src + 5))


class Image:
    def __init__(self, data):
        self.data = data
        self.secs = sections(data)
        va, rsize, rptr = self.secs['AUTO']
        self.auto_va = IMAGE_BASE + va
        self.auto_file = rptr
        self.auto = bytes(data[rptr:rptr + rsize])

    def va2file(self, va):
        return va - self.auto_va + self.auto_file

    def find(self, hexpat, name, adjust=0):
        """VA of the unique match of a hex pattern (`??` = any byte) in AUTO, plus `adjust`."""
        pat = b''.join(b'.' if t == '??' else re.escape(bytes.fromhex(t)) for t in hexpat.split())
        hits = [m.start() for m in re.finditer(pat, self.auto, re.DOTALL)]
        if len(hits) != 1:
            raise SystemExit('%s: expected one match, found %d' % (name, len(hits)))
        return self.auto_va + hits[0] + adjust


def sites_for(img):
    """[(name, va, expected_bytes, new_bytes)] and [(file_off, expected_u16, new_u16)] relocs."""
    d = img.data
    # anchors chosen after the bytes that change, so they match stock and patched alike
    unlock = img.find('8B 0D ?? ?? ?? ?? 51 E8 ?? ?? ?? ?? 83 C4 08 68 ?? ?? ?? ?? 68 05 04 00 00',
                      'remap Unlock assert', -5)                                    # 0x42F394
    lock = img.find('68 09 04 00 00 68 ?? ?? ?? ?? 68 ?? ?? ?? ?? A1 ?? ?? ?? ?? 50 B9 09 04 00 00',
                    'remap Lock assert', -5)                                        # 0x42F3F5
    getdc = img.find('68 0C 04 00 00 68 ?? ?? ?? ?? 68 ?? ?? ?? ?? 8B 15 ?? ?? ?? ?? 52 B9 0C 04 00 00',
                     'remap GetDC assert', -5)                                      # 0x42F43E
    nxt = img.find('46 81 FE 00 01 00 00 0F 8D', 'remap loop tail')                 # 0x42F486
    flip_je = img.find('68 ?? ?? ?? ?? A1 ?? ?? ?? ?? 50 E8 ?? ?? ?? ?? 83 C4 08 68 ?? ?? ?? ?? 68 7D 03 00 00',
                       'loading-screen Flip assert', -2)                            # 0x42F033
    if not (unlock < lock < getdc < nxt and getdc - unlock == 0xAA):
        raise SystemExit('remap layout differs: %#x %#x %#x %#x' % (unlock, lock, getdc, nxt))
    rel8 = d[img.va2file(flip_je) + 1]
    sites = []
    for name, va in (('remap: Unlock failure -> next index', unlock),
                     ('remap: Lock failure -> next index', lock),
                     ('remap: GetDC failure -> next index', getdc)):
        cur = bytes(d[img.va2file(va):img.va2file(va) + 5])
        old = cur if cur[:1] == b'\x68' else None        # the stock `push imm32` (operand read from the file)
        sites.append((name, va, old, jmp(va, nxt)))
    sites.append(('loading screen: Flip failure -> continue', flip_je, bytes([0x74, rel8]), bytes([0xEB, rel8])))
    # .reloc: the three push operands
    va, rsize, rptr = img.secs['.reloc']
    wanted = {unlock + 1, lock + 1, getdc + 1}
    relocs = []
    i = 0
    while i + 8 <= rsize:
        page, size = struct.unpack_from('<II', d, rptr + i)
        if size == 0:
            break
        for j in range(8, size, 2):
            e = struct.unpack_from('<H', d, rptr + i + j)[0]
            if IMAGE_BASE + page + (e & 0xFFF) in wanted and (e >> 12) in (0, 3):
                relocs.append((rptr + i + j, (3 << 12) | (e & 0xFFF), e & 0xFFF))
        i += size
    if len(relocs) != 3:
        raise SystemExit('expected 3 .reloc entries, found %d' % len(relocs))
    return sites, relocs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    img = Image(data)
    sites, relocs = sites_for(img)
    states = set()
    for name, va, old, new in sites:
        f = img.va2file(va)
        cur = bytes(data[f:f + len(new)])
        st = 'patched' if cur == new else 'stock' if old is not None and cur == old else 'UNKNOWN'
        states.add(st)
        print('  %-42s VA %#x file %#x: %s' % (name, va, f, st))
    for off, old, new in relocs:
        cur = struct.unpack_from('<H', data, off)[0]
        states.add('stock' if cur == old else 'patched' if cur == new else 'UNKNOWN')
    print('  %-42s 3 entries' % '.reloc entries of the push operands')
    overall = states.pop() if len(states) == 1 else 'MIXED'
    print('%s: %s' % (a.exe, overall))
    if a.command == 'verify':
        return 0 if overall in ('stock', 'patched') else 1
    if overall == 'patched':
        print('already patched, nothing to do')
        return 0
    if overall != 'stock':
        raise SystemExit('refusing: not in stock state')
    for name, va, old, new in sites:
        print('  %s\n    %s -> %s' % (name, old.hex(' '), new.hex(' ')))
    for off, old, new in relocs:
        print('  .reloc @ file %#x: %04X -> %04X' % (off, old, new))
    if a.command == 'plan':
        return 0
    bak = a.exe + '.ddraw.bak'
    shutil.copyfile(a.exe, bak)
    for name, va, old, new in sites:
        f = img.va2file(va)
        data[f:f + len(new)] = new
    for off, old, new in relocs:
        struct.pack_into('<H', data, off, new)
    open(a.exe, 'wb').write(data)
    print('written %s (%d code sites, %d .reloc entries); backup %s' % (a.exe, len(sites), len(relocs), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

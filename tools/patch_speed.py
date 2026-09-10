#!/usr/bin/env python3
"""Set Dark Colony's default game speed (dc16.exe / DCEXP16.EXE): 100 % -> 150 %.

The game runs one simulation tick every `gs->tick_ms` milliseconds (game state +0x970). Two
values feed it, and both have to change:

1. The game-state initialiser (0x0041BB50 in Council Wars, run at the start of every mission)
   sets `tick_ms` to 66 (`mov dword ptr [esi+970h],42h`).
2. Four *persistent* settings live in DGROUP globals (Council Wars 0x00488E08..0x00488E14,
   Classic 0x00488DE0..0x00488DEC; the main menu copies them into the campaign object at
   +0x1984..+0x1990 and back). The fourth is the desired tick length, stock 66. The per-game
   start-up (0x0041EA43) copies it into `gs->desired_ms` (+0x96C), and the speed negotiation
   (0x00419830, single player included) then sets `tick_ms` from it - so patching only (1) is
   undone within a second, which is what the first game test showed.

The options slider shows `6600 / tick_ms` rounded down to tens (100..200 in steps of 10) and
writes `TICK_DESSPEED(6600 / percent)` (doc DC16_DISPLAY_AND_RESOLUTION.md 10.14, plan F11).
Multiplayer is untouched because the relay server dictates TICK_SPEED, and a save game made
before the patch keeps the speed stored in it.

Both sites are found by byte pattern: `C7 86 70 09 00 00 imm32` for (1) and
`A1 <global> 89 82 90 19 00 00` (`mov eax,[global]; mov [edx+1990h],eax`) for (2); each must be
unique in the build.

CLI
    python patch_speed.py verify EXE
    python patch_speed.py plan   EXE [--percent 150]
    python patch_speed.py apply  EXE [--percent 150]      (writes EXE.speed.bak first)
"""

import argparse
import re
import shutil
import struct
import sys

AUTO_VA_TO_FILE = 0x400C00
STOCK_MS = 66                       # 100 %
PATTERN = re.escape(bytes.fromhex('C7 86 70 09 00 00'))     # mov dword ptr [esi+970h], imm32


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


SETTINGS_PATTERN = b'\xA1(....)' + re.escape(b'\x89\x82\x90\x19\x00\x00')   # mov eax,[g]; mov [edx+1990h],eax


def va_to_file(secs, va):
    for name, (sva, rsize, rptr) in secs.items():
        if 0x400000 + sva <= va < 0x400000 + sva + rsize:
            return rptr + (va - 0x400000 - sva)
    raise SystemExit('VA %#x is not in an initialised section' % va)


def find_sites(data):
    """[(name, file offset of the dword, current value, VA of the site)] - both must be unique."""
    secs = sections(data)
    va, rsize, rptr = secs['AUTO']
    auto = bytes(data[rptr:rptr + rsize])
    hits = [rptr + m.start() for m in re.finditer(PATTERN, auto)]
    if len(hits) != 1:
        raise SystemExit('expected exactly one tick_ms initialiser, found %d' % len(hits))
    init = hits[0] + 6
    hits = [(rptr + m.start(), struct.unpack('<I', m.group(1))[0]) for m in re.finditer(SETTINGS_PATTERN, auto, re.S)]
    if len(hits) != 1:
        raise SystemExit('expected exactly one settings->desired_ms copy, found %d' % len(hits))
    gva = hits[0][1]
    goff = va_to_file(secs, gva)
    return [('tick_ms initialiser', init, struct.unpack_from('<I', data, init)[0], init - 6 + AUTO_VA_TO_FILE),
            ('persistent setting (desired tick)', goff, struct.unpack_from('<I', data, goff)[0], gva)]


def percent_of(ms):
    return 6600 // ms // 10 * 10


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    ap.add_argument('--percent', type=int, default=150, help='game speed in percent (default 150)')
    a = ap.parse_args(argv)
    if not 100 <= a.percent <= 200 or 6600 % a.percent:
        raise SystemExit('percent must divide 6600 and lie in 100..200 (100, 110, 120, 132, 150, 165, 200)')
    new_ms = 6600 // a.percent

    data = bytearray(open(a.exe, 'rb').read())
    sites = find_sites(data)
    vals = {ms for _, _, ms, _ in sites}
    state = ('stock (100 %)' if vals == {STOCK_MS} else
             'patched (%d %%)' % percent_of(sites[1][2]) if len(vals) == 1 else 'MIXED')
    print('%s: default speed %s' % (a.exe, state))
    for name, off, ms, va in sites:
        print('  %-36s file %#7x  VA %#x  %d ms = %d %%' % (name, off, va, ms, percent_of(ms)))
    if a.command == 'verify':
        return 0
    print('  -> %d ms (%d %%) in both' % (new_ms, a.percent))
    if a.command == 'plan':
        return 0
    if vals == {new_ms}:
        print('nothing to do')
        return 0
    bak = a.exe + '.speed.bak'
    shutil.copyfile(a.exe, bak)
    for _, off, _, _ in sites:
        struct.pack_into('<I', data, off, new_ms)
    open(a.exe, 'wb').write(data)
    print('written %s; backup %s' % (a.exe, bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

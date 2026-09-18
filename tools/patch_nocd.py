#!/usr/bin/env python3
"""Stop Dark Colony from touching the CD drive letter at all (dc16.exe / ENGEXP16.EXE builds).

The `cdcheck` patch only bypasses the *result* of the CD test.  The test itself still runs, in
every build, patched or not, and it runs often:

  * `safefunc.c` start-up (0x00405F88 Classic / 0x00405F68 Council Wars) reads the drive letter the
    installer recorded in HBNFUFL.A01 (Classic) / HBNFUFL.A02 (Council Wars) - "D:" in the
    repository - and formats the CD path "%c:\\dc\\" into the DGROUP buffer 0x004A48B0, then calls
    the probe;
  * the probe `cd_probe` (0x00405EAC Classic / 0x00405E8C Council Wars) does
    fopen("D:\\dc\\anim.dat", "r") and, if that works, fopen("D:\\dc\\a<rand>", "w") + fwrite("foo")
    to see whether the medium refuses writes; the flag byte 0x004A49B8 ("CD present", read through
    0x00405E8C / 0x00405E6C) is the result;
  * the probe is repeated by `load_interface` (0x00423223 / 0x00423283) every time a menu screen
    opens, and by the in-game loop (0x0041138D / 0x004113ED), which compares the flag before and after;
  * the file-open helper (0x00406253) and the wave loader (0x00452AEF / 0x00452B5B, via the getter
    0x00405EA0 / 0x00405E80) fall back to "<CD path><name>" when a file is missing locally.

The game never calls SetErrorMode, so when the letter D: belongs to a drive that is not ready -
a card reader or a USB/optical drive without a medium, a removable disk that was unplugged, a
second hard disk that has spun down - Windows either shows its "Windows - No Disk / There is no
disk in the drive. Please insert a disk into drive \\Device\\Harddisk1\\DR1" hard-error box behind
the full-screen surface (looks like a hang and reads like a CD request) or stalls the game for
the seconds a disk needs to spin up, at start-up and again at every menu screen (the maintainer's
report of 18 Sep 2026: "all executables request the CD and hang on multi-hard-drive systems").

Two single-byte edits per exe make the game drive-letter free:

  1. `cd_probe` entry 0x00405EAC / 0x00405E8C: `push ebx` (53) -> `ret` (C3).  The probe returns at once, the
     flag stays 0 ("no CD"), which is exactly what the `cdcheck` bypasses already assume at the two
     menu sites; the in-game comparison sees "unchanged" and does nothing.  No fopen on D: ever.
  2. DGROUP format string "%c:\\dc\\" at 0x00482654: first byte '%' (25) -> NUL (00).  The CD
     path becomes the empty string, so the two fallbacks for a *missing* file ("<CD path><name>")
     retry the local name instead of opening a path on another drive; they then fail the way they
     always did (error.log line, the wave loader's message box).

Nothing moves, no relocation changes.  Both sites are found by their byte pattern / string, so
the one tool serves both builds and refuses to run unless each is found exactly once.

CLI
    python patch_nocd.py verify EXE
    python patch_nocd.py plan   EXE
    python patch_nocd.py apply  EXE        (writes EXE.nocd.bak first)
"""

import argparse
import re
import shutil
import struct
import sys

AUTO_VA_TO_FILE = 0x400C00
DGROUP_VA_TO_FILE = {659456: 0x402800, 659968: 0x402600}     # Classic 7 Jan 1998 build, Council Wars

# cd_probe: push ebx/ecx/edx/esi/ebp ; mov ebp,esp ; sub esp,100h ; push offset cdpath ; xor ah,ah ; push offset "%sanim.dat"
PROBE_TAIL = bytes.fromhex('51 52 56 55 89 E5 81 EC 00 01 00 00 68') + b'....' + bytes.fromhex('30 E4 68')
PROBE = {'stock': 0x53, 'patched': 0xC3}
FMT_STOCK = b'%c:\\dc\\\0'
FMT_PATCHED = b'\0c:\\dc\\\0'
FMT_ANCHOR = b'0\0\0\0'          # the "0" string + alignment padding just before the format string


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


def find_probe(data):
    """(file_offset, 'stock'|'patched') of the unique cd_probe entry byte."""
    va, rsize, rptr = sections(data)['AUTO']
    auto = bytes(data[rptr:rptr + rsize])
    hits = []
    for state, first in PROBE.items():
        pat = re.escape(bytes([first])) + re.escape(PROBE_TAIL[:13]) + b'.{4}' + re.escape(PROBE_TAIL[17:])
        hits += [(rptr + m.start(), state) for m in re.finditer(pat, auto, re.S)]
    if len(hits) != 1:
        raise SystemExit('expected exactly one cd_probe site, found %d: %s'
                         % (len(hits), ', '.join('%#x' % h[0] for h in hits)))
    return hits[0]


def find_format(data):
    """(file_offset, 'stock'|'patched') of the unique "%c:\\dc\\" format string."""
    va, rsize, rptr = sections(data)['DGROUP']
    dg = bytes(data[rptr:rptr + rsize])
    hits = []
    for s, state in ((FMT_STOCK, 'stock'), (FMT_PATCHED, 'patched')):
        i = 0
        while True:
            i = dg.find(s, i)
            if i < 0:
                break
            if dg[i - len(FMT_ANCHOR):i] == FMT_ANCHOR:
                hits.append((rptr + i, state))
            i += 1
    if len(hits) != 1:
        raise SystemExit('expected exactly one CD-path format string, found %d: %s'
                         % (len(hits), ', '.join('%#x' % h[0] for h in hits)))
    return hits[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    if len(data) not in DGROUP_VA_TO_FILE:
        raise SystemExit('%s: %d bytes - not a dc16.exe (659456) or ENGEXP16.EXE (659968) build' % (a.exe, len(data)))
    poff, pstate = find_probe(data)
    foff, fstate = find_format(data)
    print('%s: cd_probe entry %s (file %#x VA %#x), CD-path format string %s (file %#x VA %#x)'
          % (a.exe, pstate, poff, poff + AUTO_VA_TO_FILE, fstate, foff, foff + DGROUP_VA_TO_FILE[len(data)]))
    if a.command == 'verify':
        return 0
    edits = [(poff, PROBE['stock'], PROBE['patched'],
              'cd_probe (safefunc.c) entry: push ebx -> ret, the probe that opens <drive>:\\dc\\anim.dat at start-up, at every menu screen and in game returns at once, the "CD present" flag stays 0'),
             (foff, FMT_STOCK[0], FMT_PATCHED[0],
              'DGROUP format string "%c:\\dc\\" -> "" (first byte NUL): the CD path built from HBNFUFL.A0x becomes empty, so the fallbacks for a missing file retry the local name instead of a path on another drive')]
    for off, old, new, note in edits:
        print('  %s  file %#x VA %#x 1 byte: %02x -> %02x'
              % (note, off, off + (AUTO_VA_TO_FILE if off == poff else DGROUP_VA_TO_FILE[len(data)]), old, new))
    if a.command == 'plan':
        return 0
    if pstate == 'patched' and fstate == 'patched':
        print('nothing to do')
        return 0
    bak = a.exe + '.nocd.bak'
    shutil.copyfile(a.exe, bak)
    for off, old, new, _ in edits:
        if data[off] == new:
            continue
        assert data[off] == old, hex(off)
        data[off] = new
    open(a.exe, 'wb').write(data)
    print('written %s; backup %s' % (a.exe, bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

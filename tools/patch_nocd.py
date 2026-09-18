#!/usr/bin/env python3
"""Remove the CD path from Dark Colony completely (dc16.exe / ENGEXP16.EXE builds).

The `cdcheck` patch only bypasses the *result* of the CD test.  All the CD machinery stays in the
exe and runs, in every build, patched or not:

  * `safefunc.c` start-up (0x00405F88 Classic / 0x00405F68 Council Wars) opens HBNFUFL.A01
    (Classic) / HBNFUFL.A02 (Council Wars) - the drive letter the installer recorded, "D:" in the
    repository; a missing file is an assert (`safefunc.c` line 137) and a silent exit - reads the
    letter, formats "%c:\\dc\\" into the DGROUP buffer 0x004A48B0 (the CD path) and calls the probe;
  * `cd_probe` (0x00405EAC / 0x00405E8C) does fopen("D:\\dc\\anim.dat") and, if that works,
    fopen("D:\\dc\\a<rand>", "w") + fwrite("foo") to see whether the medium refuses writes; the flag
    byte 0x004A49B8 ("CD present") is the result.  `load_interface` (0x00423223 / 0x00423283)
    repeats the probe at every menu screen, the in-game loop (0x0041138D / 0x004113ED) periodically;
  * the file-open helper (0x00406253) and the wave loader (0x00452AEF / 0x00452B5B, via the CD-path
    getter 0x00405EA0 / 0x00405E80) fall back to "<CD path><name>" when a file is missing locally,
    and the wave loader then shows "CDROM NOT FOUND / Please insert The Dark Colony CD and Restart";
  * the movie opener 0x00401028 falls back to the CD path when the flag is set.

The game never calls SetErrorMode, so a letter D: that belongs to a drive that is not ready (card
reader or USB/optical drive without a medium, an unplugged removable disk, a spun-down second hard
disk) meant Windows' "No Disk / Please insert a disk into drive ..." hard-error box behind the
full-screen surface or a spin-up stall (maintainer report 18 Sep 2026).  Maintainer decision the
same day: the patched game must not touch the CD path AT ALL - not read HBNFUFL, not build the
path, not fall back to it, not ask for the disc.

Edits per exe (all inside existing instructions / strings; nothing moves):

  1. start-up 0x00405FB3 / 0x00405F93: the two `mov` that load the HBNFUFL name (10 bytes) become
     a near `jmp` (E9 rel32, +0x9C: too far for a short jump) to the "full" marker check, followed
     by five NOPs - the HBNFUFL open, the letter read, the sprintf of the
     CD path and the "CD present" reset are skipped (the flag lives in .bss and is 0 anyway).  The
     two absolute operands vanish, so their two HIGHLOW `.reloc` entries become type-0 padding.
  2. start-up `call cd_probe` (0x00406074 / 0x00406054) -> 5 NOPs.
  3. `cd_probe` entry (0x00405EAC / 0x00405E8C): `push ebx` -> `ret`, for the two remaining callers
     (menu screens, in-game check): the probe never runs.
  4. DGROUP "%c:\\dc\\" (0x00482654): erased (8 zero bytes) - dead data.
  5. file-open helper 0x004063DF / 0x004063BF: `je <try CD path>` -> `jmp <fail as usual>`.
  6. wave loader 0x00452AE9 / 0x00452B49: `jne found` -> `jmp` - after the two local names the loader
     goes straight to its error exit instead of the two CD-path attempts.
  7. movie opener 0x00401078: `je <no CD>` -> `jmp` - never the CD path, whatever the flag.
  8. DGROUP strings "CDROM NOT FOUND" -> "FILE NOT FOUND" and "Please insert The Dark Colony CD and
     Restart" (Council Wars: "...Expansion Pak CD - The Council Wars and Restart") ->
     "A sound file is missing - see error.log", NUL-padded to the original length: the box the wave
     loader shows for a missing WAV tells the truth.

HBNFUFL.A01 / .A02 are no longer read by the patched exes (the originals still need them).  All
sites are found by byte pattern / string, so one tool serves both builds; it refuses to run unless
every site is found exactly once.  An exe carrying the 18 Sep 2026 two-byte form of this fix
(probe `ret` + first format byte zeroed) is upgraded in place.

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

IMAGE_BASE = 0x400000
AUTO_VA_TO_FILE = 0x400C00
DGROUP_VA_TO_FILE = {659456: 0x402800, 659968: 0x402600}     # Classic 7 Jan 1998 build, Council Wars
NOP = b'\x90'

TITLE_OLD = b'CDROM NOT FOUND\0'
TITLE_NEW = b'FILE NOT FOUND\0\0'
MSG_PREFIX = b'Please insert The Dark Colony'
MSG_NEW = b'A sound file is missing - see error.log'
FMT_OLD = b'%c:\\dc\\\0'
FMT_HALF = b'\0c:\\dc\\\0'            # the two-byte form of 18 Sep 2026
FMT_ANCHORS = [b'hbnfufl.a01\x000\0\0\0', b'hbnfufl.a02\x000\0\0\0']    # the strings just before the format


def pat(s):
    return b''.join(b'.' if t == '??' else re.escape(bytes([int(t, 16)])) for t in s.split())


class Image:
    def __init__(self, data):
        self.data = data
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        nsec = struct.unpack_from('<H', data, pe + 6)[0]
        opt = struct.unpack_from('<H', data, pe + 20)[0]
        st = pe + 24 + opt
        self.secs = {}
        for i in range(nsec):
            s = data[st + i * 40: st + i * 40 + 40]
            vsize, va, rsize, rptr = struct.unpack_from('<IIII', s, 8)
            self.secs[s[:8].rstrip(b'\0').decode()] = (va, rsize, rptr)
        va, rsize, rptr = self.secs['AUTO']
        self.auto = bytes(data[rptr:rptr + rsize])
        self.auto_va = IMAGE_BASE + va
        va, rsize, rptr = self.secs['DGROUP']
        self.dg = bytes(data[rptr:rptr + rsize])
        self.dg_file = rptr

    def find(self, p, what, adjust=0):
        hits = [m.start() for m in re.finditer(pat(p), self.auto, re.S)]
        if len(hits) != 1:
            raise SystemExit('%s: expected one site, found %d' % (what, len(hits)))
        return self.auto_va + hits[0] + adjust

    def va2file(self, va):
        return va - AUTO_VA_TO_FILE

    def dg_find(self, alts, what, anchors=None):
        hits = []
        for s in alts:
            i = 0
            while True:
                i = self.dg.find(s, i)
                if i < 0:
                    break
                if anchors is None or any(self.dg[i - len(x):i] == x for x in anchors):
                    hits.append(i)
                i += 1
        if len(set(hits)) != 1:
            raise SystemExit('%s: expected one string site, found %d' % (what, len(set(hits))))
        return self.dg_file + hits[0]


def jmp_rel32(at, target):
    return b'\xE9' + struct.pack('<i', target - (at + 5))


def sites_for(img):
    """[(note, file_off, [accepted old bytes...], new bytes)] and [(reloc_file_off, old_u16, new_u16)]."""
    d = img.data
    f = img.va2file
    size = len(d)
    # 1./2. start-up: the two movs before fopen("hbnfufl.a0x") and the probe call after the "full" check
    # patterns are anchored on bytes this tool does not change, so they match stock and patched alike
    init = img.find('EB EB ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? E8 ?? ?? ?? ?? 30 FF 89 C2 88 3D', 'start-up HBNFUFL open', 2)
    full = img.find('BA ?? ?? ?? ?? B8 ?? ?? ?? ?? E8 ?? ?? ?? ?? 85 C0 74 0D 30 C9 88 0D', 'start-up "full" check')
    if not 0x80 <= full - init <= 0x100:            # 0xA1 in both builds; too far for a short jmp
        raise SystemExit('start-up layout differs: %#x -> %#x' % (init, full))
    probe_call = full + 0x20
    # 3. cd_probe entry
    probe = img.find('?? 51 52 56 55 89 E5 81 EC 00 01 00 00 68 ?? ?? ?? ?? 30 E4 68', 'cd_probe entry')
    # 5. generic open helper: je <CD attempt> followed by jmp <fail path>
    helper = img.find('8A 15 ?? ?? ?? ?? 89 45 F0 84 D2 75 0D 80 3D ?? ?? ?? ?? 00 0F 84 ?? ?? ?? ?? 83 7D F0 00 ?? ?? ?? ?? ?? ?? E9',
                      'open helper CD fallback', 30)
    fail = helper + 11 + struct.unpack_from('<i', d, f(helper) + 7)[0]        # target of the jmp after the je
    helper_stock = d[f(helper)] == 0x0F
    # 6. wave loader: jne after the second OpenFile, followed by the CD-path getter call
    wave = img.find('89 C6 83 F8 FF ?? ?? ?? ?? ?? ?? E8 ?? ?? ?? ?? 8D BD', 'wave loader CD fallback', 5)
    wave_stock = d[f(wave)] == 0x0F
    found = (wave + 6 + struct.unpack_from('<i', d, f(wave) + 2)[0] if wave_stock
             else wave + 5 + struct.unpack_from('<i', d, f(wave) + 1)[0])
    # 7. movie opener: je after "test al,al" on the CD flag
    rb = img.find('E8 ?? ?? ?? ?? 84 C0 ?? 63 E8 ?? ?? ?? ?? 8D BD ?? ?? ?? ?? 89 C6 57', 'movie opener CD fallback', 7)
    init_old = bytes(d[f(init):f(init) + 10])
    init_stock = init_old[:1] == b'\xBA' and init_old[5:6] == b'\xB8'
    sites = [
        ('start-up: mov edx,"r" / mov eax,"hbnfufl.a0x" -> jmp to the "full" marker check (HBNFUFL is not opened, no drive letter, no CD path, no probe)',
         f(init), [init_old] if init_stock else [], jmp_rel32(init, full) + NOP * 5),
        ('start-up: call cd_probe -> 5 NOPs', f(probe_call), [b'\xE8' + struct.pack('<i', probe - (probe_call + 5))], NOP * 5),
        ('cd_probe entry: push ebx -> ret (the two remaining callers, load_interface and the in-game check, get nothing)',
         f(probe), [b'\x53'], b'\xC3'),
        ('open helper: je <try "<CD path><name>"> -> jmp <fail as for any missing file>',
         f(helper), [bytes(d[f(helper):f(helper) + 6])] if helper_stock else [], jmp_rel32(helper, fail) + NOP),
        ('wave loader: jne <found> -> jmp: after the two local names the loader takes its error exit instead of the two CD-path attempts',
         f(wave), [b'\x0F\x85' + struct.pack('<i', found - (wave + 6))] if wave_stock else [], jmp_rel32(wave, found) + NOP),
        ('movie opener: je <no CD> -> jmp (never the CD path, whatever the flag says)', f(rb), [b'\x74\x63'], b'\xEB\x63'),
    ]
    # 4./8. DGROUP strings
    fmt = img.dg_find([FMT_OLD, FMT_HALF, b'\0' * 8], 'CD-path format string', FMT_ANCHORS)
    sites.append(('DGROUP "%c:\\dc\\" (the CD-path format, now dead) -> 8 zero bytes', fmt, [FMT_OLD, FMT_HALF], b'\0' * 8))
    title = img.dg_find([TITLE_OLD, TITLE_NEW], 'wave loader box title')
    sites.append(('DGROUP "CDROM NOT FOUND" -> "FILE NOT FOUND" (title of the wave loader\'s box for a missing WAV)',
                  title, [TITLE_OLD], TITLE_NEW))
    msg = img.dg_find([MSG_PREFIX, MSG_NEW], 'wave loader box text')
    cur = bytes(d[msg:d.find(b'\0', msg) + 1])
    if cur.startswith(MSG_PREFIX):
        msg_old, msg_len = cur, len(cur)
    else:
        msg_len = 45 if size == 659456 else 78
        msg_old = None
    msg_new = MSG_NEW + b'\0' * (msg_len - len(MSG_NEW))
    sites.append(('DGROUP "Please insert The Dark Colony ... CD and Restart" -> "A sound file is missing - see error.log" (NUL-padded to the old length)',
                  msg, [msg_old] if msg_old else [], msg_new))
    # .reloc: the two mov operands of the start-up site
    va, rsize, rptr = img.secs['.reloc']
    wanted = {init + 1, init + 6}
    relocs = []
    i = 0
    while i + 8 <= rsize:
        page, blk = struct.unpack_from('<II', d, rptr + i)
        if blk == 0:
            break
        for j in range(8, blk, 2):
            e = struct.unpack_from('<H', d, rptr + i + j)[0]
            if IMAGE_BASE + page + (e & 0xFFF) in wanted and (e >> 12) in (0, 3):
                relocs.append((rptr + i + j, (3 << 12) | (e & 0xFFF), e & 0xFFF))
        i += blk
    if len(relocs) != 2:
        raise SystemExit('expected 2 .reloc entries for the start-up movs, found %d' % len(relocs))
    return sites, relocs


def state_of(data, sites, relocs):
    st = []
    for note, off, olds, new in sites:
        cur = bytes(data[off:off + len(new)])
        st.append('patched' if cur == new else 'stock' if cur in olds else 'other')
    for off, old, new in relocs:
        e = struct.unpack_from('<H', data, off)[0]
        st.append('patched' if e == new else 'stock' if e == old else 'other')
    return st


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    if len(data) not in DGROUP_VA_TO_FILE:
        raise SystemExit('%s: %d bytes - not a dc16.exe (659456) or ENGEXP16.EXE (659968) build' % (a.exe, len(data)))
    img = Image(data)
    sites, relocs = sites_for(img)
    st = state_of(data, sites, relocs)
    summary = ('stock' if all(s == 'stock' for s in st) else 'patched' if all(s == 'patched' for s in st)
               else 'partially patched (%d of %d sites)' % (st.count('patched'), len(st)))
    if 'other' in st:
        raise SystemExit('%s: unexpected bytes at %d site(s): %s' % (a.exe, st.count('other'),
                         ', '.join(sites[i][0] if i < len(sites) else '.reloc' for i, s in enumerate(st) if s == 'other')))
    print('%s: CD path %s (%d code/data sites, %d .reloc entries)' % (a.exe, summary, len(sites), len(relocs)))
    if a.command == 'verify':
        return 0
    dgf = DGROUP_VA_TO_FILE[len(data)]
    for (note, off, olds, new), s in zip(sites, st):
        cur = bytes(data[off:off + len(new)])
        old = cur if s == 'stock' else (olds[0] if olds else cur)
        va = off + (AUTO_VA_TO_FILE if off < img.dg_file else dgf)
        print('  %s  file %#x VA %#x %d bytes: %s -> %s' % (note, off, va, len(new), old.hex(' '), new.hex(' ')))
    for off, old, new in relocs:
        print('  .reloc @ file %#x: %04X -> %04X' % (off, old, new))
    if a.command == 'plan':
        return 0
    if summary == 'patched':
        print('nothing to do')
        return 0
    bak = a.exe + '.nocd.bak'
    shutil.copyfile(a.exe, bak)
    for (note, off, olds, new), s in zip(sites, st):
        if s == 'stock':
            data[off:off + len(new)] = new
    for (off, old, new), s in zip(relocs, st[len(sites):]):
        if s == 'stock':
            struct.pack_into('<H', data, off, new)
    open(a.exe, 'wb').write(data)
    print('written %s (%d sites, %d .reloc entries); backup %s' % (a.exe, len(sites), len(relocs), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""The one CD fix: no CD required and no CD path at all (dc16.exe / ENGEXP16.EXE builds).

Since 18 Sep 2026 this tool is the whole CD fix (patcher id `nocd`), maintainer request: "one CD fix
which correctly fixes it".  It carries the historical 2025 `cdcheck` bytes - the two menu tests of
the "CD present" flag (`call cd_flag ; test al,al ; jne` at 0x00404F1F and 0x00405C9F / 0x00405C7F,
jne -> jmp) and, for Council Wars only, the inverted C-runtime jne at 0x00478DD9 that stopped the
expansion from throwing the player out of a running game - and everything below.

Those bypasses alone only ignore the *result* of the CD test.  All the CD machinery stays in the
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
  9. the "insert CD" prompt (21 Sep 2026, maintainer report: a player "still sees Please insert Dark
     Colony CD").  When a REQUIRED file is missing, the file-open helper does not fail: with the display
     up it calls the display object's CD-prompt method (slot +0x4C, 0x0042C0BC / 0x0042C11C; the only
     caller is 0x0040630D / 0x004062ED), which draws the sprite `intrface/insee` - the cyan "Please
     insert Dark Colony CD" box at (235,220) - and loops on fopen(name) until the file appears.  The
     text is a picture, not one of the strings of edit 8, and with the CD path gone the loop never ends:
     a hang behind a CD request that names no file.  The first 68 bytes of the method (up to and
     including its second `mov eax,"intrface/insee"`) are rewritten into the wave loader's own error
     exit (0x00452BD1 / 0x00452C31): fprintf(error.log, "unable to open file %s\n", name); flush;
     display shutdown; Sleep(2000); MessageBoxA(hwnd, name, "FILE NOT FOUND", MB_OK); exit(0).  The
     rest of the old body is dead code.  The four absolute operands (format string, error.log FILE*,
     caption, window handle) take over the .reloc entries of the four old operands that the rewrite
     overwrote or made dead (page offsets 0x0D8, 0x0DD, 0x0FC, 0x132 for Classic; the fifth, 0x1EB,
     still sits on an absolute operand in dead code and stays); every target is read from the wave
     loader's sequence and from the MessageBoxA import thunk (`jmp dword ptr [IAT]`), so one pattern
     serves both builds.  Seen in the wild with a clone that lacked `ozi_ns/intrf_hd/` (the OZI scene
     lists - the folder had never been committed): OZI MISSIONS -> NEXT on the story screen -> the
     prompt, for `intrf_hd/hxscene.txt`.  Now: a box naming the file, a line in error.log, exit.

HBNFUFL.A01 / .A02 are no longer read by the patched exes (the originals still need them: the first
character is the letter of the drive that holds the CD).  All sites are found by byte pattern /
string, so one tool serves both builds; it refuses to run unless every site is found exactly once.
An exe carrying only the 2025 `cdcheck` bytes, the 18 Sep 2026 two-byte form of the drive fix
(probe `ret` + first format byte zeroed) or the 18-21 Sep 2026 form without edit 9 is upgraded in
place.

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


def call_rel32(at, target):
    return b'\xE8' + struct.pack('<i', target - (at + 5))


# The CD-prompt method: its first 68 bytes in the stock build (prologue, `mov eax/ebx` of "intrface/insee"
# and "CD Prompt Sprite Memory", the sprite load, up to the second `mov eax,"intrface/insee"`) ...
PROMPT_STOCK = ('53 51 56 57 55 89 E5 83 EC 38 89 45 FC 89 55 E8 8B 40 20 8B 55 FC 89 45 F8 89 D1 B8 ?? ?? ?? ?? '
                'BB ?? ?? ?? ?? FF 51 44 8B 49 24 89 C2 89 C8 E8 ?? ?? ?? ?? 8B 5D FC 8D 55 D8 89 D9 89 45 E0 B8 ?? ?? ?? ??')
PROMPT_LEN = 68
PROMPT_OLD_OPERANDS = (0x1C, 0x21, 0x40, 0x76)     # .reloc'd operands: three inside the 68 bytes, one in the dead rest
PROMPT_NEW_OPERANDS = (3, 9, 45, 52)                # fmt, error.log FILE*, caption, hwnd in the new body


def prompt_body(at, fmt, log, fprintf, fflush, dclose, sleep, caption, hwnd, msgbox_thunk, exit_):
    """... and the 68 bytes that replace them: the wave loader's error exit with the file name as the box
    text.  Entered with eax = display object, edx = the name of the missing file.  Never returns."""
    b = b'\x52'                                                   # push edx           (keep the name)
    b += b'\x52' + b'\x68' + struct.pack('<I', fmt)             # push edx ; push "unable to open file %s\n"
    b += b'\xFF\x35' + struct.pack('<I', log)                    # push dword ptr [error_log]
    b += call_rel32(at + len(b), fprintf) + b'\x83\xC4\x0C'      # call fprintf ; add esp,0Ch
    b += call_rel32(at + len(b), fflush)                         # call flush
    b += call_rel32(at + len(b), dclose)                         # call display_close   (desktop mode back)
    b += b'\xB8' + struct.pack('<I', 2000)                       # mov eax,2000
    b += call_rel32(at + len(b), sleep)                          # call Sleep wrapper
    b += b'\x5A\x6A\x00'                                         # pop edx ; push 0     (MB_OK)
    b += b'\x68' + struct.pack('<I', caption)                    # push "FILE NOT FOUND"
    b += b'\x52' + b'\xFF\x35' + struct.pack('<I', hwnd)         # push edx (text = name) ; push dword ptr [hwnd]
    b += call_rel32(at + len(b), msgbox_thunk)                   # call MessageBoxA (import thunk)
    b += b'\x31\xC0'                                             # xor eax,eax
    b += call_rel32(at + len(b), exit_)                          # call exit
    assert len(b) == PROMPT_LEN, len(b)
    assert all(b[o - 1] in (0x68, 0x35) for o in PROMPT_NEW_OPERANDS)
    return b


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
    # 9. the CD-prompt method of the display object (its VA is the operand of the vtable set-up), the
    #    wave loader's error exit it is rebuilt from, and the MessageBoxA import thunk
    vt = img.find('C7 40 4C ?? ?? ?? ?? 8B 45 F8 8B 55 FC 8B 80 20 01 00 00 89 42 6C', 'display vtable set-up (CD-prompt slot)')
    prompt = struct.unpack_from('<I', d, f(vt) + 3)[0]
    if (prompt & 0xFFF) + PROMPT_LEN + 0x7B > 0x1000:
        raise SystemExit('CD-prompt method %#x straddles a page' % prompt)
    werr = img.find('50 68 ?? ?? ?? ?? 8B 15 ?? ?? ?? ?? 52 E8 ?? ?? ?? ?? 83 C4 0C E8 ?? ?? ?? ?? E8 ?? ?? ?? ?? B8 D0 07 00 00 '
                    'E8 ?? ?? ?? ?? 6A 00 68 ?? ?? ?? ?? 68 ?? ?? ?? ?? 8B 0D ?? ?? ?? ?? 51 2E FF 15 ?? ?? ?? ?? 31 C0 E8',
                    'wave loader error exit')
    wf = f(werr)
    u32 = lambda o: struct.unpack_from('<I', d, wf + o)[0]
    rel = lambda o: werr + o + 5 + struct.unpack_from('<i', d, wf + o + 1)[0]
    fmt_va, log_va, caption_va, hwnd_va, iat_va = u32(2), u32(8), u32(44), u32(55), u32(63)
    fprintf, fflush, dclose, sleep, exit_ = rel(13), rel(21), rel(26), rel(36), rel(69)
    thunk = img.find('FF 25 ' + ' '.join('%02X' % b for b in struct.pack('<I', iat_va)), 'MessageBoxA import thunk')
    body = prompt_body(prompt, fmt_va, log_va, fprintf, fflush, dclose, sleep, caption_va, hwnd_va, thunk, exit_)
    cur = bytes(d[f(prompt):f(prompt) + PROMPT_LEN])
    prompt_stock = re.fullmatch(pat(PROMPT_STOCK), cur, re.S) is not None
    # 0. the three historical `cdcheck` bytes (2025): the two menu tests of the "CD present" flag
    menu1 = img.find('8B 45 F0 E8 ?? ?? ?? ?? E8 ?? ?? ?? ?? 84 C0 ?? 66 BB 01 00 00 00', 'start-up CD test', 15)
    menu2 = img.find('89 45 F8 E8 ?? ?? ?? ?? 84 C0 ?? 12 BB 01 00 00 00', 'main-menu CD test', 10)
    init_old = bytes(d[f(init):f(init) + 10])
    init_stock = init_old[:1] == b'\xBA' and init_old[5:6] == b'\xB8'
    sites = [
        ('start-up CD test: jne -> jmp after "call cd_flag ; test al,al" (the game starts without the disc)',
         f(menu1), [b'\x75'], b'\xEB'),
        ('main-menu CD test: jne -> jmp after the same test (the CD-gated menu buttons stay enabled)',
         f(menu2), [b'\x75'], b'\xEB'),
    ]
    if size == 659968:
        # Council Wars only (the historical third `cdcheck` byte): a jne in C-runtime code that the 2025
        # hand patch inverted because the expansion threw the player out of a running game without the
        # disc; kept byte-identical to the build that has been played since.
        crt = img.find('53 51 89 C3 89 D1 8C DA E8 ?? ?? ?? ?? 85 C0 ?? 09 89 CA 89 D8 E8', 'in-game CD test (CRT)', 15)
        sites.append(('in-game CD test (Council Wars only): jne -> je in the C-runtime write path, the 2025 hand patch that stopped the expansion from throwing the player out of a running game',
                      f(crt), [b'\x75'], b'\x74'))
    sites += [
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
        ('CD-prompt method (display slot +4Ch, called for a missing required file): "draw intrface/insee and wait for the file" -> the wave loader\'s error exit: fprintf(error.log, "unable to open file %s", name); display shutdown; Sleep(2000); MessageBoxA(hwnd, name, "FILE NOT FOUND"); exit (68 bytes; the rest of the old body is dead)',
         f(prompt), [cur] if prompt_stock else [], body),
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
    # .reloc: the two mov operands of the start-up site (-> type 0), and the four operands of the old
    # CD-prompt body (two `mov eax,str`, one `mov ebx,str`, and the `mov ebx,str` in what is now dead
    # code) re-pointed at the four absolute operands of the new body
    va, rsize, rptr = img.secs['.reloc']
    wanted = {init + 1, init + 6}
    page_p = (prompt - IMAGE_BASE) & ~0xFFF
    old_offs = [(prompt + o) & 0xFFF for o in PROMPT_OLD_OPERANDS]
    new_offs = [(prompt + o) & 0xFFF for o in PROMPT_NEW_OPERANDS]
    relocs, got = [], set()
    i = 0
    while i + 8 <= rsize:
        page, blk = struct.unpack_from('<II', d, rptr + i)
        if blk == 0:
            break
        for j in range(8, blk, 2):
            pos = rptr + i + j
            e = struct.unpack_from('<H', d, pos)[0]
            if IMAGE_BASE + page + (e & 0xFFF) in wanted and (e >> 12) in (0, 3):
                relocs.append((pos, (3 << 12) | (e & 0xFFF), e & 0xFFF))
            elif page == page_p and (e >> 12) == 3 and (e & 0xFFF) in old_offs:
                k = old_offs.index(e & 0xFFF)
                got.add(k)
                relocs.append((pos, e, (3 << 12) | new_offs[k]))
            elif page == page_p and (e >> 12) == 3 and (e & 0xFFF) in new_offs:
                k = new_offs.index(e & 0xFFF)
                got.add(k)
                relocs.append((pos, (3 << 12) | old_offs[k], e))
        i += blk
    if len(relocs) != 6 or len(got) != 4:
        raise SystemExit('expected 2 + 4 .reloc entries (start-up movs, CD-prompt operands), found %d' % len(relocs))
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

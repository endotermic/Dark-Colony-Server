#!/usr/bin/env python3
r"""Wine's 64-bit-only "wow64" mode: the four segment-register-saving routines no longer write GS (fix `wow64`,
dc16.exe / ENGEXP16.EXE).

Why (19 Oct 2026; Wine bug 58141, https://bugs.winehq.org/show_bug.cgi?id=58141, fixed in Wine 10.14 by commit
94447cee; DC16_DISPLAY_AND_RESOLUTION.md section 10.79): under Wine's new "wow64" mode - a 64-bit-only Wine running
the 32-bit game, the default on Ubuntu 26.10 (Wine 10.0, no wine32 package) and the only mode of Wine 11 - the game
dies before its first screen with "err:virtual:virtual_setup_exception stack overflow" in Wine's log (the untouched
ENGEXP16.EXE too).  Four routines of the Watcom runtime save and restore the segment registers around their body
(`push es / push fs / push gs` on entry, `pop gs / pop fs / pop es` on exit, the compiler's convention for callbacks);
the `pop gs` is the problem.  On x86-64 Linux a write to GS, even of the value it already holds, clears the 64-bit GS
base that Wine's 64-bit side uses to reach its thread block; the next access faults, the fault handler needs the same
thread block, faults again, and the recursion exhausts the 64-bit stack (the same crash in MDK and Blood Omen, two
other Watcom-built games).  Wine 10.14 (Aug 2025) repairs the GS base in its fault handler; Wine 10.0 - 10.13 do not.
The 32-bit game never uses GS (flat Win32 model), so every `push gs` (0F A8) and `pop gs` (0F A9) becomes two NOPs:
the stacks stay balanced (each push keeps its pop; the routine entered at 0x440BBF leaves through the shared epilogue
at 0x440B8C), nothing moves.  Windows and the classic 32-bit Wine (wine32) run the game either way.

The seven sites (Council Wars VAs; Classic dc16.exe is 0x60 lower, the bytes are the same):

    0x440B4E  callback wrapper A (0x440B49)                    push gs
    0x440B8C  shared epilogue of wrappers A and B               pop gs
    0x440BBF  callback wrapper B (0x440BB8, leave + jmp 0x440B8C) push gs
    0x47A105  callback wrapper C (0x47A0FE)                    push gs
    0x47A1DF  epilogue of wrapper C                             pop gs
    0x47AB1A  callback wrapper D (0x47AB13)                    push gs
    0x47ABB2  epilogue of wrapper D                             pop gs

Found with a byte scan of the AUTO section for `push es / push fs / push gs` (06 0F A0 0F A8) and `pop gs / pop fs /
pop es` (0F A9 0F A1 07); the tool repeats that scan and refuses an exe where it finds any other such site.  14 bytes
in 7 edits, no .reloc entry involved, independent of every other fix (apply at any point; the patcher runs it last).
Verified 19 Oct 2026: under Wine 10.0 in wow64 mode the game reaches the main menu with this fix and dies without it.

CLI
    python patch_wow64.py verify EXE
    python patch_wow64.py plan   EXE
    python patch_wow64.py apply  EXE          (writes EXE.wow64.bak first)
"""

import argparse
import re
import shutil
import struct
import sys

IMAGE_BASE = 0x400000
AUTO_SIZE_OF = {0x7E200: 'Classic', 0x7E400: 'Council Wars'}      # raw size of the code section, same in the `.dcicon` builds
DELTA_OF = {'Classic': 0, 'Council Wars': 0x60}                   # the Council Wars code sits 0x60 above the Classic code

PUSH_GS, POP_GS, NOPS = b'\x0F\xA8', b'\x0F\xA9', b'\x90\x90'
PUSH_SEQ = b'\x06\x0F\xA0' + PUSH_GS          # push es ; push fs ; push gs   (push gs = the last two bytes)
POP_SEQ = POP_GS + b'\x0F\xA1\x07'            # pop gs ; pop fs ; pop es      (pop gs = the first two bytes)

# (Council Wars VA of the push gs / pop gs, kind, name as the patcher's edit comment shows it)
SITES = [
    (0x440B4E, 'push', 'callback wrapper A (0x00440B49)'),
    (0x440B8C, 'pop', 'shared epilogue of wrappers A and B'),
    (0x440BBF, 'push', 'callback wrapper B (0x00440BB8, exits via leave + jmp 0x00440B8C)'),
    (0x47A105, 'push', 'callback wrapper C (0x0047A0FE)'),
    (0x47A1DF, 'pop', 'epilogue of wrapper C'),
    (0x47AB1A, 'push', 'callback wrapper D (0x0047AB13)'),
    (0x47ABB2, 'pop', 'epilogue of wrapper D'),
]


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


class Image:
    def __init__(self, data):
        self.data = data
        self.secs = sections(data)
        va, rsize, rptr = self.secs['AUTO']
        self.auto_va = IMAGE_BASE + va
        self.auto_file = rptr
        self.auto = bytes(data[rptr:rptr + rsize])
        if rsize not in AUTO_SIZE_OF:
            raise SystemExit('unknown exe: AUTO section of %d bytes (Classic 0x7E200, Council Wars 0x7E400)' % rsize)
        self.build = AUTO_SIZE_OF[rsize]
        self.delta = DELTA_OF[self.build]

    def va2file(self, va):
        return va - self.auto_va + self.auto_file

    def scan(self, seq, adjust):
        """VAs of the GS instruction inside every occurrence of `seq` in the code section."""
        return [self.auto_va + m.start() + adjust for m in re.finditer(re.escape(seq), self.auto)]


def analyse(img):
    d = img.data
    sites = []
    states = {}
    for cw_va, kind, name in SITES:
        va = cw_va - 0x60 + img.delta
        f = img.va2file(va)
        stock = PUSH_GS if kind == 'push' else POP_GS
        cur = bytes(d[f:f + 2])
        # the neighbours of the instruction: push es / push fs before a push gs, pop fs / pop es after a pop gs
        around_ok = (bytes(d[f - 3:f]) == PUSH_SEQ[:3]) if kind == 'push' else (bytes(d[f + 2:f + 5]) == POP_SEQ[2:])
        if not around_ok:
            st = 'UNKNOWN'
        elif cur == stock:
            st = 'stock'
        elif cur == NOPS:
            st = 'patched'
        else:
            st = 'UNKNOWN'
        # the names quote Council Wars addresses; the Classic build's are 0x60 lower
        name = re.sub(r'0x([0-9A-F]{8})', lambda m: '0x%08X' % (int(m.group(1), 16) - 0x60 + img.delta), name)
        label = '0x%08X %s' % (va, name)
        states[label] = st
        sites.append((label, va, f, stock, '%s gs -> nop nop' % kind))
    # the scan that found the sites: no other push es/fs/gs or pop gs/fs/es sequence may exist in the code
    # section, in stock or in patched form (a different build would need its own table)
    expect_push = {va for _, va, _, s, _ in sites if s == PUSH_GS}
    expect_pop = {va for _, va, _, s, _ in sites if s == POP_GS}
    found_push = set(img.scan(PUSH_SEQ, 3)) | set(img.scan(PUSH_SEQ[:3] + NOPS, 3))
    found_pop = set(img.scan(POP_SEQ, 0)) | set(img.scan(NOPS + POP_SEQ[2:], 0))
    extra = sorted((found_push - expect_push) | (found_pop - expect_pop))
    if extra:
        raise SystemExit('unexpected segment-register sequences at %s - not the known build, refusing'
                         % ', '.join('0x%08X' % v for v in extra))
    missing = sorted((expect_push - found_push) | (expect_pop - found_pop))
    if missing:
        raise SystemExit('the known sites %s hold neither the stock nor the patched bytes - not the known build, refusing'
                         % ', '.join('0x%08X' % v for v in missing))
    return dict(sites=sites, states=states)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    img = Image(data)
    r = analyse(img)
    print('%s: %s build; %d push gs / %d pop gs sites of the Watcom callback wrappers (0x%08X .. 0x%08X)'
          % (a.exe, img.build, sum(1 for s in r['sites'] if s[3] == PUSH_GS), sum(1 for s in r['sites'] if s[3] == POP_GS),
             r['sites'][0][1], r['sites'][-1][1]))
    for k, v in r['states'].items():
        print('  %-72s %s' % (k, v))
    kinds = set(r['states'].values())
    overall = 'patched' if kinds == {'patched'} else 'stock' if kinds == {'stock'} else 'MIXED'
    print('%s: %s' % (a.exe, overall))
    if a.command == 'verify':
        return 0 if overall in ('stock', 'patched') else 1
    if overall == 'patched':
        print('already patched, nothing to do')
        return 0
    if overall != 'stock':
        raise SystemExit('refusing: not in stock state')
    edits = []
    for label, va, f, stock, note in r['sites']:
        old = bytes(data[f:f + 2])
        edits.append((label, va, f, old, NOPS, note))
        print('  %s   VA 0x%x file 0x%x %d bytes: %s -> %s; %s' % (label, va, f, len(NOPS), old.hex(' '), NOPS.hex(' '), note))
    if a.command == 'plan':
        return 0
    bak = a.exe + '.wow64.bak'
    shutil.copyfile(a.exe, bak)
    for label, va, f, old, new, note in edits:
        assert bytes(data[f:f + len(new)]) == old
        data[f:f + len(new)] = new
    open(a.exe, 'wb').write(data)
    print('written %s (%d edits, %d bytes); backup %s' % (a.exe, len(edits), sum(len(e[4]) for e in edits), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

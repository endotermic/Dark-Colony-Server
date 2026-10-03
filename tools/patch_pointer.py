#!/usr/bin/env python3
r"""Battlefield pointer animation at the menus' pace (dc16.exe / DCEXP16.EXE, fix `pointer`).

Why (3 Oct 2026, maintainer: "pointer animation is too fast" -> "let's try battle cursor at the rate of gated the
way the menus are"; DC16_DISPLAY_AND_RESOLUTION.md section 10.70): the client display advances the cursor animation
once per frame (`call 0x00422C7C` at `0x0040B309`, Council Wars `0x0040B369`; the step routine `0x00426428` has no
clock), so at 60 frames per second the battlefield pointer's three-frame cycle turns ten times a second (30 cursor
changes per second measured), while the menus advance the same animation only when 33 ms have passed since the
last advance (interface loop `0x0042412A`: `now - last > 0x21`, then `last = now`) - 11.5 changes per second with
the menu arrow.  This fix gives the battlefield call the menus' gate: the call becomes `call gate`, where

    gate:  push eax ; now = timeGetTime() ; if now - last < 33: pop eax ; ret
           last = now ; pop eax ; jmp 0x00422C7C            (tail call - returns into the client as before)

so the cursor advances every second frame at 60 frames per second (15 changes per second).  `last` lives at
`0x00481FF4`, the dword after fix `fps`'s timestamp in the `.idata` page slack.  The 35 bytes of code sit in the
tails of the second and third dead assert bodies of `remap` that fix `fps` leaves free (Classic
`0x0042F424..0x0042F438` 21 bytes and `0x0042F46B..0x0042F478` 14 bytes; CW +0x60; dead since fix `ddraw`); the
three HIGHLOW `.reloc` entries of those tails are re-pointed to the gate's two absolute operands (page offsets
`0x427 -> 0x42E`, `0x46C -> 0x46D`) and the third (`0x471`) becomes type 0 padding, so the table stays exact.
`timeGetTime` is reached through the import thunk (`0x0047F116`, CW `0x0047F176`); code, thunk and the
cursor-advance routine all sit at +0x60 in Council Wars and the timestamp is the same VA, so the written
bytes are identical in both builds.

Requires fix `ddraw` (the tails are live code without its jumps); independent of `fps` (uses the room `fps`
leaves, not its code), runs after it in the patcher.  `plan` works on the untouched exe, `apply` refuses without
`ddraw`.  Confirmed in game 3 Oct 2026: 15 cursor changes per second in battle, 60 frames per second unchanged.

CLI
    python patch_pointer.py verify EXE
    python patch_pointer.py plan   EXE
    python patch_pointer.py apply  EXE          (writes EXE.pointer.bak first)
"""

import argparse
import re
import shutil
import struct
import sys

IMAGE_BASE = 0x400000
GATE_MS = 0x21                                  # the menus' period (interface loop 0x42412A: cmp eax,21h)

HOOK_VA = 0x40B309                              # client display: call cursor_advance 0x422C7C (Classic; CW +0x60)
HOOK_CONTEXT = '8B 80 F4 07 00 00 8B 75 F0 E8 ?? ?? ?? ?? 85 F6 74 0E'      # mov eax,[eax+7F4h]; mov esi,[ebp-10h]; call; test esi,esi; je
ADVANCE_VA = 0x422C7C
TAIL2_VA, TAIL2_LEN = 0x42F424, 21              # tail of remap's second dead body (26 bytes free)
TAIL3_VA, TAIL3_LEN = 0x42F46B, 14              # tail of the third (27 bytes free)
TGT_THUNK = 0x47F116
LAST_VA = 0x481FF4                              # the gate's timestamp (.idata page slack, after fps's 0x481FF0)
DDRAW_JMPS = {0x42F394: 'E9 ED 00 00 00', 0x42F3F5: 'E9 8C 00 00 00', 0x42F43E: 'E9 43 00 00 00'}

# the stock bytes of the two tails (Classic; CW's string addresses are +8, matched with wildcards)
TAIL2_STOCK = '4A 00 BA ?? ?? ?? ?? E8 ?? ?? ?? ?? E8 ?? ?? ?? ?? 31 C0 E8 ?? ?? ?? ?? EB 48'      # 26 bytes (starts inside the mov eax,[errlog] that fps's range cuts)
TAIL3_STOCK = 'A1 B4 49 4A 00 BA ?? ?? ?? ?? E8 ?? ?? ?? ?? E8 ?? ?? ?? ?? 31 C0 E8 ?? ?? ?? ??'   # 27 bytes


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

    def va2file(self, va):
        return va - self.auto_va + self.auto_file

    def find(self, hexpat, name, adjust=0):
        pat = b''.join(b'.' if t == '??' else re.escape(bytes.fromhex(t)) for t in hexpat.split())
        hits = [m.start() for m in re.finditer(pat, self.auto, re.DOTALL)]
        if len(hits) != 1:
            raise SystemExit('%s: expected one match, found %d' % (name, len(hits)))
        return self.auto_va + hits[0] + adjust

    def matches(self, va, hexpat):
        f = self.va2file(va)
        toks = hexpat.split()
        cur = self.data[f:f + len(toks)]
        return len(cur) == len(toks) and all(t == '??' or int(t, 16) == b for t, b in zip(toks, cur))


def rel32(src, dst):
    return struct.pack('<i', dst - (src + 5))


def rel8(src, dst, size=2):
    d = dst - (src + size)
    assert -128 <= d <= 127, (hex(src), hex(dst), d)
    return struct.pack('<b', d)


def build_code(delta):
    t2, t3 = TAIL2_VA + delta, TAIL3_VA + delta
    cont, skip = t3, t3 + 12
    c = bytearray()
    c += b'\x50'                                              # +0  push eax                     ip (the call's argument)
    c += b'\xE8' + rel32(t2 + 1, TGT_THUNK + delta)           # +1  call timeGetTime
    c += b'\x89\xC2'                                          # +6  mov edx,eax
    c += b'\x2B\x05' + struct.pack('<I', LAST_VA)             # +8  sub eax,[last]               (.reloc entry at +10)
    c += b'\x83\xF8' + bytes([GATE_MS])                       # +14 cmp eax,21h
    c += b'\x72' + rel8(t2 + 17, skip)                        # +17 jb skip
    c += b'\xEB' + rel8(t2 + 19, cont)                        # +19 jmp cont
    assert len(c) == TAIL2_LEN
    tail2 = bytes(c)
    c = bytearray()
    c += b'\x89\x15' + struct.pack('<I', LAST_VA)             # +0  cont: mov [last],edx        (.reloc entry at +2)
    c += b'\x58'                                              # +6  pop eax
    c += b'\xE9' + rel32(t3 + 7, ADVANCE_VA + delta)          # +7  jmp cursor_advance (tail call)
    c += b'\x58'                                              # +12 skip: pop eax
    c += b'\xC3'                                              # +13 ret
    assert len(c) == TAIL3_LEN
    return tail2, bytes(c)


def reloc_table(data, page_va, wanted):
    """{page offset: (file offset of the entry word, value)} of the entries with the `wanted` page offsets in
    the .reloc block of `page_va`'s page."""
    rva, rsize, rptr = sections(data)['.reloc']
    out = {}
    pos, end = rptr, rptr + rsize
    while pos + 8 <= end:
        page, size = struct.unpack_from('<II', data, pos)
        if size < 8:
            break
        if IMAGE_BASE + page == page_va & ~0xFFF:
            for i in range(8, size, 2):
                v = struct.unpack_from('<H', data, pos + i)[0]
                if v & 0xFFF in wanted:
                    out[v & 0xFFF] = (pos + i, v)
        pos += size
    return out


def analyse(img):
    d = img.data
    hook_va = img.find(HOOK_CONTEXT, 'client cursor advance', adjust=9)
    delta = hook_va - HOOK_VA
    if delta not in (0, 0x60):
        raise SystemExit('unexpected cursor-advance call address %#x' % hook_va)
    tail2, tail3 = build_code(delta)
    hook_stock = b'\xE8' + rel32(hook_va, ADVANCE_VA + delta)
    hook_new = b'\xE8' + rel32(hook_va, TAIL2_VA + delta)
    iva, irsize, _ = img.secs['.idata']
    if not (IMAGE_BASE + iva + irsize <= LAST_VA and LAST_VA + 4 <= IMAGE_BASE + ((iva + irsize + 0xFFF) & ~0xFFF)):
        raise SystemExit('the timestamp slot %#x is not in the .idata page slack' % LAST_VA)
    ddraw_applied = all(img.matches(va + delta, pat) for va, pat in DDRAW_JMPS.items())
    sites = [('client display: call cursor_advance -> call gate', hook_va, hook_stock, hook_new,
              'the per-frame cursor advance of the battlefield goes through the 33 ms gate'),
             ('gate, part 1 (tail of remap dead body 2)', TAIL2_VA + delta, None, tail2,
              'push eax; now = timeGetTime(); if now - last < 33 ms: skip; else cont'),
             ('gate, part 2 (tail of remap dead body 3)', TAIL3_VA + delta, None, tail3,
              'cont: last = now; pop eax; jmp cursor_advance 0x422C7C.  skip: pop eax; ret')]
    states = {}
    for name, va, stock, new in [(n, v, s, w) for n, v, s, w, _ in sites]:
        f = img.va2file(va)
        cur = bytes(d[f:f + len(new)])
        if cur == new:
            st = 'patched'
        elif stock is not None:
            st = 'stock' if cur == stock else 'UNKNOWN'
        else:
            st = 'stock' if img.matches(va, TAIL2_STOCK if va == TAIL2_VA + delta else TAIL3_STOCK) else 'UNKNOWN'
        states[name] = st
    # the tails' three entries (page offsets, Classic 0x427 / 0x46C / 0x471; CW +0x60): the first two are re-pointed to the
    # gate's operands (sub eax,[last] at tail2+10, mov [last],edx at tail3+2), the third becomes type 0
    o1, o2, o3 = [(va + delta) & 0xFFF for va in (TAIL2_VA + 3, TAIL3_VA + 1, TAIL3_VA + 6)]
    n1, n2 = (TAIL2_VA + delta + 10) & 0xFFF, (TAIL3_VA + delta + 2) & 0xFFF
    rel = reloc_table(d, TAIL2_VA + delta, {o1, o2, o3, n1, n2})
    relocs = []
    if all(k in rel and rel[k][1] >> 12 == 3 for k in (o1, o2, o3)):
        relocs = [(rel[o1][0], rel[o1][1], 0x3000 | n1, 'the dead mov edx,imm32 operand -> the gate\'s sub eax,[last] operand'),
                  (rel[o2][0], rel[o2][1], 0x3000 | n2, 'the dead mov eax,[error.log] operand -> the gate\'s mov [last],edx operand'),
                  (rel[o3][0], rel[o3][1], o3, 'the dead mov edx,imm32 operand of tail 3 -> type 0 padding')]
        rstate = 'stock'
    elif all(k in rel and rel[k][1] >> 12 == 3 for k in (n1, n2)) and o3 in rel and rel[o3][1] >> 12 == 0:
        rstate = 'patched'
    else:
        rstate = 'UNKNOWN'
    states['.reloc entries of the tails'] = rstate
    return dict(delta=delta, sites=sites, states=states, relocs=relocs, ddraw=ddraw_applied)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    img = Image(data)
    r = analyse(img)
    delta = r['delta']
    print('%s: %s build; cursor-advance call VA 0x%08X, gate at 0x%08X / 0x%08X, timestamp 0x%08X%s'
          % (a.exe, 'Council Wars' if delta else 'Classic', HOOK_VA + delta, TAIL2_VA + delta, TAIL3_VA + delta, LAST_VA,
             '' if r['ddraw'] else '; fix ddraw NOT applied (the remap tails are still reachable)'))
    for k, v in r['states'].items():
        print('  %-50s %s' % (k, v))
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
    for name, va, stock, new, note in r['sites']:
        f = img.va2file(va)
        edits.append((name, va, f, bytes(data[f:f + len(new)]), new, note))
        print('  %s   VA 0x%x file 0x%x %d bytes: %s -> %s; %s' % (name, va, f, len(new), edits[-1][3].hex(' '), new.hex(' '), note))
    for off, old, new, note in r['relocs']:
        print('  .reloc @ file 0x%x: %04X -> %04X  (%s)' % (off, old, new, note))
    if a.command == 'plan':
        if not r['ddraw']:
            print('note: apply needs fix ddraw first (its jumps make the remap bodies dead code)')
        return 0
    if not r['ddraw']:
        raise SystemExit('refusing: fix ddraw is not applied, the remap assert bodies are still live code (apply patch_ddraw_lost.py first)')
    bak = a.exe + '.pointer.bak'
    shutil.copyfile(a.exe, bak)
    for name, va, f, old, new, note in edits:
        assert bytes(data[f:f + len(new)]) == old
        data[f:f + len(new)] = new
    for off, old, new, note in r['relocs']:
        assert struct.unpack_from('<H', data, off)[0] == old
        struct.pack_into('<H', data, off, new)
    open(a.exe, 'wb').write(data)
    print('written %s (%d code edits, %d .reloc entries); backup %s' % (a.exe, len(edits), len(r['relocs']), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

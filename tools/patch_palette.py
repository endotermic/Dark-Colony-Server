#!/usr/bin/env python3
"""Fast screen loads: the palette conversion without 512 surface round trips (dc16.exe / DCEXP16.EXE).

Every screen the game shows (each menu, briefing, the hall, the battle load, twice at start-up) ends
in `set_palette` (`ddex4.c`, Classic 0x0042F320, Council Wars 0x0042F380; §10.16 calls it `remap`),
which converts the 256 palette entries to 16-bit pixels the portable 1997 way - per entry
`GetDC(back buffer)` -> `SetPixel(hdc, 0, 0, RGB(r,g,b))` -> `ReleaseDC` -> `Lock(whole surface)` ->
read one word -> `Unlock`.  On Windows 11 DirectDraw is emulated over D3D9 and `ReleaseDC` and
`Unlock` each write the WHOLE surface back, so the loop costs 512 full-surface transfers: measured
1.4-1.5 s per screen change at 1024x768 and 3.2-3.5 s at 1920x1200 (28 Sep 2026, main-thread
stack sampler), i.e. the load time grows with the pixel count.  The value read back is plain
truncation of the RGB bytes to the surface format (verified for all 256 entries in the running
game: RGB565 `(r>>3)<<11 | (g>>2)<<5 | b>>3`), which the same function also derives for its
`make_colour` tables a few instructions later.

The fix computes the pixel in place and drops the five API calls (Classic VAs; Council Wars at
+0x60; everything pattern-located, the new bytes hold no absolute operand):

    0x0042F4C7..0x0042F54D  push &hdc / GetDC / SetPixel / ReleaseDC / Lock / read lpSurface / store LUT
                            -> ax = R(r) | G(g) | B(b) by the format flag screen+0x14 (0x235 = 565,
                               else 555), store LUT[i] at screen->palette+0x602+2i, short jmp to
                               the table build at 0x0042F54E; the rest of the 135 bytes is NOP
    0x0042F37C..0x0042F393  push 0 / Unlock(back buffer) / test / je next
                            -> jmp next index (0x0042F486) + NOPs (no Lock, so no Unlock)

Five `.reloc` entries of the overwritten `mov eax,[back buffer]` / `call cs:[SetPixel]` operands
become IMAGE_REL_BASED_ABSOLUTE padding (type 0, page offset kept).  Register use matches the
original: after the store the table build recomputes everything from esi/edi.  The `ddraw` fix's
three skip jumps inside the same function (§10.16) become dead code and stay harmless; this tool
accepts the stock and the `ddraw`-patched exe alike.  Doc: DC16_DISPLAY_AND_RESOLUTION.md §10.46.

CLI
    python patch_palette.py verify EXE
    python patch_palette.py plan   EXE
    python patch_palette.py apply  EXE          (writes EXE.palette.bak first)
"""

import argparse
import re
import shutil
import struct
import sys

IMAGE_BASE = 0x400000

# stock bytes of the two sites (`??` = an absolute operand byte that differs between the builds)
STOCK_BODY = ('51 89 45 72 A1 ?? ?? ?? ?? 50 8B 18 FF 53 44 85 C0 0F 85 60 FF FF FF '
              '31 C9 8A 45 6E 8A 4D 6A C1 E0 08 09 C1 31 C0 8A 45 72 C1 E0 10 09 C8 '
              '50 6A 00 6A 00 8B 4D 76 51 2E FF 15 ?? ?? ?? ?? 8B 5D 76 A1 ?? ?? ?? ?? 53 8B 08 50 FF 51 68 '
              '6A 00 6A 01 8D 4D FE B8 6C 00 00 00 51 89 45 FE A1 ?? ?? ?? ?? 6A 00 8B 18 50 FF 53 64 '
              '85 C0 0F 85 BC FE FF FF '
              '8D 0C 36 8B 47 18 01 C1 8B 45 22 66 8B 00 66 89 81 02 06 00 00')          # 135 bytes, 0x42F4C7..0x42F54D
STOCK_UNLOCK = '6A 00 A1 ?? ?? ?? ?? 50 8B 08 FF 91 80 00 00 00 85 C0 0F 84 F2 00 00 00'   # 24 bytes, 0x42F37C..0x42F393

# the replacement of the body: r in [ebp+6Ah], g in [ebp+6Eh], b in eax (masked) on entry
NEW_BODY_CODE = bytes.fromhex(
    '89 C3'                 # mov  ebx,eax            ; b
    'C1 EB 03'              # shr  ebx,3
    '8B 45 6A'              # mov  eax,[ebp+6Ah]      ; r
    'C1 E8 03'              # shr  eax,3
    '8B 4D 6E'              # mov  ecx,[ebp+6Eh]      ; g
    '81 7F 14 35 02 00 00'  # cmp  dword ptr [edi+14h],235h   ; RGB565?
    '75 0B'                 # jne  L555
    'C1 E0 0B'              # shl  eax,11
    'C1 E9 02'              # shr  ecx,2
    'C1 E1 05'              # shl  ecx,5
    'EB 09'                 # jmp  Ldone
    'C1 E0 0A'              # L555: shl eax,10
    'C1 E9 03'              # shr  ecx,3
    'C1 E1 05'              # shl  ecx,5
    '09 C8'                 # Ldone: or eax,ecx
    '09 D8'                 # or   eax,ebx
    '8B 4F 18'              # mov  ecx,[edi+18h]      ; screen->palette
    '66 89 84 71 02 06 00 00'   # mov word ptr [ecx+esi*2+602h],ax   ; LUT[i]
)


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
        """VA of the unique match of a hex pattern (`??` = any byte) in AUTO, plus `adjust`."""
        pat = b''.join(b'.' if t == '??' else re.escape(bytes.fromhex(t)) for t in hexpat.split())
        hits = [m.start() for m in re.finditer(pat, self.auto, re.DOTALL)]
        if len(hits) != 1:
            raise SystemExit('%s: expected one match, found %d' % (name, len(hits)))
        return self.auto_va + hits[0] + adjust


def patterned(hexpat):
    """(length, [(index, byte)] of the fixed bytes) of a hex pattern."""
    toks = hexpat.split()
    return len(toks), [(i, int(t, 16)) for i, t in enumerate(toks) if t != '??']


def matches(cur, hexpat):
    n, fixed = patterned(hexpat)
    return len(cur) == n and all(cur[i] == b for i, b in fixed)


def new_body(length):
    # code, then a short jump over the padding to the table build right after the site
    code = NEW_BODY_CODE
    rest = length - len(code) - 2
    assert 0 <= rest <= 0x7F
    return code + bytes([0xEB, rest]) + b'\x90' * rest


def new_unlock(site, nxt, length):
    j = b'\xE9' + struct.pack('<i', nxt - (site + 5))
    return j + b'\x90' * (length - len(j))


def sites_for(img):
    """[(name, va, stock pattern, new bytes)] and [(file_off, expected_u16, new_u16)] reloc edits."""
    d = img.data
    body = img.find(STOCK_BODY, 'remap: GetDC/SetPixel/Lock body')                                # 0x42F4C7
    unlock = img.find(STOCK_UNLOCK, 'remap: Unlock block')                                       # 0x42F37C
    nxt = img.find('46 81 FE 00 01 00 00 0F 8D', 'remap loop tail')                              # 0x42F486
    tail = img.find('8D 04 B5 00 00 00 00 29 F0 89 F2 8D 0C 00 C1 FA 1F 89 F0 C1 E2 03 1B C2 C1 F8 03',
                    'remap table build')                                                          # 0x42F54E
    body_len, _ = patterned(STOCK_BODY)
    unlock_len, _ = patterned(STOCK_UNLOCK)
    if not (unlock < nxt < body and body + body_len == tail and body - unlock == 0x14B):
        raise SystemExit('remap layout differs: unlock %#x next %#x body %#x tail %#x' % (unlock, nxt, body, tail))
    sites = [('remap: pixel read-back -> arithmetic', body, STOCK_BODY, new_body(body_len)),
             ('remap: Unlock of the read-back -> skipped', unlock, STOCK_UNLOCK, new_unlock(unlock, nxt, unlock_len))]
    # .reloc: the absolute operands inside the overwritten ranges
    wanted = {unlock + 3, body + 5, body + 0x3A, body + 0x42, body + 0x5E}
    va, rsize, rptr = img.secs['.reloc']
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
    if len(relocs) != 5:
        raise SystemExit('expected 5 .reloc entries, found %d' % len(relocs))
    return sites, relocs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    img = Image(data)
    try:
        sites, relocs = sites_for(img)
        stock_found = True
    except SystemExit as e:
        # the patched exe no longer holds the stock body: locate the sites by the new bytes instead
        stock_found = False
        sites, relocs = patched_sites(img, str(e))
    states = set()
    for name, va, pat, new in sites:
        f = img.va2file(va)
        cur = bytes(data[f:f + len(new)])
        st = 'patched' if cur == new else 'stock' if matches(cur, pat) else 'UNKNOWN'
        states.add(st)
        print('  %-44s VA %#x file %#x: %s' % (name, va, f, st))
    for off, old, new in relocs:
        cur = struct.unpack_from('<H', data, off)[0]
        states.add('stock' if cur == old else 'patched' if cur == new else 'UNKNOWN')
    print('  %-44s %d entries' % ('.reloc entries of the overwritten operands', len(relocs)))
    overall = states.pop() if len(states) == 1 else 'MIXED'
    print('%s: %s' % (a.exe, overall))
    if a.command == 'verify':
        return 0 if overall in ('stock', 'patched') else 1
    if overall == 'patched':
        print('already patched, nothing to do')
        return 0
    if overall != 'stock':
        raise SystemExit('refusing: not in stock state')
    for name, va, pat, new in sites:
        f = img.va2file(va)
        old = bytes(data[f:f + len(new)])
        print('  %s\n    %s -> %s' % (name, old.hex(' '), new.hex(' ')))
    for off, old, new in relocs:
        print('  .reloc @ file %#x: %04X -> %04X' % (off, old, new))
    if a.command == 'plan':
        return 0
    bak = a.exe + '.palette.bak'
    shutil.copyfile(a.exe, bak)
    for name, va, pat, new in sites:
        f = img.va2file(va)
        data[f:f + len(new)] = new
    for off, old, new in relocs:
        struct.pack_into('<H', data, off, new)
    open(a.exe, 'wb').write(data)
    print('written %s (%d code sites, %d .reloc entries); backup %s' % (a.exe, len(sites), len(relocs), bak))
    return 0


def patched_sites(img, why):
    """Sites of an exe that already carries the fix (found by the new body, whose bytes are build-independent)."""
    body_len, _ = patterned(STOCK_BODY)
    unlock_len, _ = patterned(STOCK_UNLOCK)
    try:
        body = img.find(new_body(body_len).hex(' '), 'remap: new body')
    except SystemExit:
        raise SystemExit('neither the stock nor the patched remap loop found (%s)' % why)
    unlock = body - 0x14B
    nxt = body - 0x41
    sites = [('remap: pixel read-back -> arithmetic', body, STOCK_BODY, new_body(body_len)),
             ('remap: Unlock of the read-back -> skipped', unlock, STOCK_UNLOCK, new_unlock(unlock, nxt, unlock_len))]
    wanted = {unlock + 3, body + 5, body + 0x3A, body + 0x42, body + 0x5E}
    d = img.data
    va, rsize, rptr = img.secs['.reloc']
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
    return sites, relocs


if __name__ == '__main__':
    sys.exit(main())

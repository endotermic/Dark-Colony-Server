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

A fifth site joined on 3 Oct 2026 (maintainer: "error on game startup"; two access violations at
Ultimate `0x42EE50` in the Windows Application log, 35 s apart, after a two-monitor desktop change):
the cursor set-up (`ddex4.c` 805-858) loads the 32 cursor bitmaps into surfaces, then LOCKS cursor
surface 0 to read its top-left pixel as the colour key (line 836, "Couldn't lock mouse").  A lost
surface fails that Lock, the message goes to the still-buffered `error.log` (lost in the crash, so
the log stays empty) and the code reads the key through the NULL `lpSurface` anyway.  The fix
replaces the 92-byte failure block with code that asks the surface for its pixel format
(`GetPixelFormat`, which DirectDraw answers for a lost surface too) and takes the key from it -
the cursor bitmaps' corner colour is palette entry 0 = RGB (109, 60, 0), which GDI's blit truncates
to 0x69E0 in 565 and 0x35E0 in 555 - then skips the pixel read and the Unlock; the per-frame
restore reloads the cursor bitmaps and the colour key set here stays with the surface objects.

The fix is to make those five failures non-fatal.  The game already restores lost surfaces on
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
    0x0042ED8C  cursor key: 92-byte Lock-failure block (print + assert)  ->  51 bytes:
                mov eax,[cursor_surface0]; lea edx,[ebp-5Eh] (= the DDSURFACEDESC's ddpfPixelFormat);
                mov dword [edx],20h; GetPixelFormat(eax, edx); eax = (dwGBitMask == 7E0h) ? 69E0h : 35E0h;
                mov [ebp+72h],eax; mov [ebp+76h],eax; jmp <after the Unlock block>   (+ NOPs)

The three `push imm32` operands of the remap sites and five of the six operands of the cursor
block carried HIGHLOW `.reloc` entries; they become IMAGE_REL_BASED_ABSOLUTE padding (type 0,
page offset kept so `verify` still finds them); the block's first operand (page offset +1) stays
HIGHLOW because the new `mov eax,[imm32]` keeps its operand at the same place.
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
    # the cursor colour key (3 Oct 2026): the Lock of cursor surface 0 and its 92-byte failure block
    # anchored on the Lock call BEFORE the block (push 0; push 1; lea edx,[ebp-0A6h]; push edx; mov eax,[surf0]; push 0;
    # mov ebx,[eax]; push eax; call [ebx+64h]; test eax,eax; je +5Ch), which the patch leaves alone
    cur_je = img.find('6A 00 6A 01 8D 95 5A FF FF FF 52 A1 ?? ?? ?? ?? 6A 00 8B 18 50 FF 53 64 85 C0 74 5C',
                      'cursor colour-key Lock', 26)                                 # the `je` 0x42ED8A
    cur_block = cur_je + 2                                                          # 0x42ED8C, 0x5C bytes
    cur_ok = cur_block + 0x5C                                                       # 0x42EDE8: mov edx,[ebp-82h]
    f_ok = img.va2file(cur_ok)
    tail = bytes(d[f_ok:f_ok + 0x2A])
    if not (tail[:11] == bytes.fromhex('8B957EFFFFFF31C0668B02') and tail[0x18:0x19] == b'\xA1'
            and tail[0x1D:0x28] == bytes.fromhex('508B10FF92800000008 5C0'.replace(' ', '')) and tail[0x28:0x2A] == b'\x74\x5C'):
        raise SystemExit('cursor colour-key code differs after the Lock')
    surf0 = tail[0x19:0x1D]                                                         # the operand of `mov eax,[4DFE90h]`
    cur_after_unlock = cur_ok + 0x2A + 0x5C                                         # 0x42EE6E: the `je` target after the Unlock block
    cur_new = (b'\xA1' + surf0 + bytes.fromhex('8D55A2 C70220000000 52 50 8B18 FF5354 B8E0690000 817DB6E0070000 7405 B8E0350000 894572 894576'.replace(' ', ''))
               + jmp(cur_block + 46, cur_after_unlock))
    assert len(cur_new) == 51, len(cur_new)
    cur_new += b'\x90' * (0x5C - len(cur_new))
    sites = []
    for name, va in (('remap: Unlock failure -> next index', unlock),
                     ('remap: Lock failure -> next index', lock),
                     ('remap: GetDC failure -> next index', getdc)):
        cur = bytes(d[img.va2file(va):img.va2file(va) + 5])
        old = cur if cur[:1] == b'\x68' else None        # the stock `push imm32` (operand read from the file)
        sites.append((name, va, old, jmp(va, nxt)))
    sites.append(('loading screen: Flip failure -> continue', flip_je, bytes([0x74, rel8]), bytes([0xEB, rel8])))
    f_blk = img.va2file(cur_block)
    cur_old = bytes(d[f_blk:f_blk + 0x5C])
    sites.append(('cursor colour key: Lock failure -> key from the pixel format', cur_block,
                  cur_old if cur_old[:1] == b'\x68' else None, cur_new))
    # .reloc: the three push operands of the remap sites + five of the cursor block's six operands
    # (block +1 keeps its HIGHLOW entry: the new `mov eax,[imm32]` operand sits there)
    va, rsize, rptr = img.secs['.reloc']
    wanted = {unlock + 1, lock + 1, getdc + 1, cur_block + 0x15, cur_block + 0x1F, cur_block + 0x24, cur_block + 0x30, cur_block + 0x47}
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
    if len(relocs) != 8:
        raise SystemExit('expected 8 .reloc entries, found %d' % len(relocs))
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
    print('  %-42s 8 entries' % '.reloc entries of the operands')
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

#!/usr/bin/env python3
"""No intro movie at start-up; the campaign buttons play their own (fix `intro`, Dark Colony Ultimate only).

Why (5 Oct 2026, maintainer: "remove movie from startup. respective movies must play when selecting mission pack
from main menu. 'dark colony' intro for 'DARK COLONY'. 'Council wars' intro for 'COUNCIL WARS'";
DC16_DISPLAY_AND_RESOLUTION.md section 10.74): the exe starts with the Council Wars intro (avi/intro.avi)
before the main menu, whatever the player is going to do, and the Dark Colony intro (AVI/DCINTRO.AVI, the
Classic disc's movie under its own name beside Council Wars' since 15 Sep 2026) was never played by this
build.  Now the main menu comes up at once; DARK COLONY plays avi/dcintro.avi and COUNCIL WARS plays
avi/intro.avi, each right before its campaign's race / name screen.  ACADEMY, OZI MISSIONS and LOAD GAME play
nothing (not asked for).  SPACE skips a movie as before; a missing movie file is skipped silently (the player
returns when the file does not open - the stock behaviour of fix nocd's movie opener).

How (3 code edits + 2 .reloc entries, on the exe AFTER fix ozi):
  1. `main` (`0x00405264`) built "avi/" + "intro.avi" into a local buffer and called the movie player
     `play_movie(eax = ui, edx = path)` (`0x00401028`) at `0x004053C1`, then entered the menu loop at
     `0x004053C6`.  Those 95 bytes (`0x00405367..0x004053C6`: two inlined strcpy/strcat loops and the call)
     become `jmp 0x004053C6` and host the new code; the two absolute operands they held (the "avi/" buffer
     at `.bss 0x004A46F2`, the "intro.avi" string at DGROUP `0x004824A8`) lose their HIGHLOW .reloc
     entries (-> type 0).  The new code has no absolute operand (rel32 calls and inline strings reached
     through `call next ; <string> ; next: pop edx`), so the relocation table stays exact.
  2. COUNCIL WARS (menu button 0, the stock NEW CAMPAIGN handler at `0x00405050`): its `call
     tramp_cw_campaign` (fix ozi: `call stub_cw_set ; jmp campaign_runner 0x00401C08`) at `0x00405065`
     becomes `call tramp_cw_intro`.
  3. DARK COLONY (button 6, fix ozi's handler at `0x00405102`): its `call tramp_dc_campaign` at
     `0x00405116` becomes `call tramp_dc_intro`.  ACADEMY (button 1) keeps `tramp_dc_campaign` at
     `0x00405083` and plays nothing.
  The trampolines (in the freed start-up block):
     tramp_cw_intro:  push edx ; call stub_cw_set ; call common ; db "avi/intro.avi",0      (25 bytes)
     tramp_dc_intro:  push edx ; call stub_dc_set ; call common ; db "avi/dcintro.avi",0    (27 bytes)
     common:          pop edx (-> the string) ; push eax ; call play_movie ; pop eax ; pop edx ;
                      jmp campaign_runner                                                   (14 bytes)
  At the call sites eax = the menu's ui object and edx = the campaign state (gs), exactly what the campaign
  runner takes; the mode stubs keep both (they push eax/edi), the movie player returns a value in eax, so eax
  and edx are saved around it.  The handler's own return address stays on top of the stack for the runner's
  `ret`, as with fix ozi's trampolines.
Requires fix `ozi` (the trampolines and mode stubs it created).  `plan` and `apply` work on the exe after
ozi; on the untouched exe both stop with a message.

CLI
    python patch_intro.py verify EXE
    python patch_intro.py plan   EXE
    python patch_intro.py apply  EXE        (writes EXE.intro.bak first)
"""

import argparse
import shutil
import struct
import sys

IMAGE_BASE = 0x400000
AUTO_SIZE_OF = {0x7E200: 'classic', 0x7E400: 'cw'}      # raw size of the code section, same in the `.dcicon` builds

# NEW CAMPAIGN = COUNCIL WARS handler: mov [eax+14F4h],1 ; mov edx,eax ; mov [eax+14F0h],edi ; mov eax,[ebp-4] ; call ... ; jmp ... ; cmp edi,1
SITE_CW = b'\xC7\x80\xF4\x14\x00\x00\x01\x00\x00\x00\x89\xC2\x89\xB8\xF0\x14\x00\x00\x8B\x45\xFC\xE8????\xE9????\x83\xFF\x01'
SITE_CW_CALL = 21
# DARK COLONY handler (fix ozi): cmp edi,6 ; jne +16h ; mov [eax+14F0h],0 ; mov edx,eax ; mov eax,[ebp-4] ; call ... ; jmp +20h ; cmp edi,7
SITE_DC = b'\x83\xFF\x06\x75\x16\xC7\x80\xF0\x14\x00\x00\x00\x00\x00\x00\x89\xC2\x8B\x45\xFC\xE8????\xEB\x20\x83\xFF\x07'
SITE_DC_CALL = 20
# main: "avi/" + "intro.avi" -> local buffer, play_movie(ebx, buffer); then the menu loop (mov eax,ebx ; call bintro ; jmp -9)
START_STOCK = bytes.fromhex(
    'be ?? ?? ?? ?? 8d bd 00 ff ff ff 57 8a 06 88 07 3c 00 74 10 8a 46 01 83 c6 02 88 47 01 83 c7 02 3c 00 75 e8 5f'
    'be ?? ?? ?? ?? 8d bd 00 ff ff ff 8d 95 00 ff ff ff 57 2b c9 49 b0 00 f2 ae 4f 8a 06 88 07 3c 00 74 10 8a 46 01'
    '83 c6 02 88 47 01 83 c7 02 3c 00 75 e8 5f 89 d8 e8 ?? ?? ?? ?? 89 d8 e8 ?? ?? ?? ?? eb f7'.replace('??', '3f'))
START_WILD = [i for i, c in enumerate('be ?? ?? ?? ?? 8d bd 00 ff ff ff 57 8a 06 88 07 3c 00 74 10 8a 46 01 83 c6 02 88 47 01 83 c7 02 3c 00 75 e8 5f'
                                      'be ?? ?? ?? ?? 8d bd 00 ff ff ff 8d 95 00 ff ff ff 57 2b c9 49 b0 00 f2 ae 4f 8a 06 88 07 3c 00 74 10 8a 46 01'
                                      '83 c6 02 88 47 01 83 c7 02 3c 00 75 e8 5f 89 d8 e8 ?? ?? ?? ?? 89 d8 e8 ?? ?? ?? ?? eb f7'.replace(' ', '')) if c == '?' and i % 2 == 0]
START_WILD = sorted({i // 2 for i in START_WILD})
BLOCK_LEN = 95                       # 0x405367..0x4053C6: the two string loops and the call to the movie player
PLAY_CALL = 90                       # offset of `call play_movie` inside the block
AVI_BUF_OPERAND = 1                  # offsets of the two absolute operands inside the block (their .reloc entries -> type 0):
INTRO_STR_OPERAND = 38               # `mov esi,imm32` at block+0 and block+37
CW_MOVIE = b'avi/intro.avi\0'
DC_MOVIE = b'avi/dcintro.avi\0'
T0 = 2                               # tramp_cw_intro at block+2 (after the 2-byte jmp)
T1 = T0 + 1 + 5 + 5 + len(CW_MOVIE)  # tramp_dc_intro
COMMON = T1 + 1 + 5 + 5 + len(DC_MOVIE)
COMMON_LEN = 14


def sections(data):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    opt = struct.unpack_from('<H', data, pe + 20)[0]
    st = pe + 24 + opt
    out = {}
    for i in range(nsec):
        s = data[st + i * 40: st + i * 40 + 40]
        vsize, rva, rsize, rptr = struct.unpack_from('<IIII', s, 8)
        out[s[:8].rstrip(b'\0').decode()] = (IMAGE_BASE + rva, rsize, rptr)
    return out


def matches(data, off, pattern, wild=()):
    if off + len(pattern) > len(data):
        return False
    return all(k in wild or p == 0x3F and k in START_WILD or data[off + k] == p for k, p in enumerate(pattern))


def matches_q(data, off, pattern):
    """`?` (0x3F) in the pattern = any byte (the call-site patterns)."""
    return off + len(pattern) <= len(data) and all(p == 0x3F or data[off + k] == p for k, p in enumerate(pattern))


def find_all(data, pattern, lo, hi, fn):
    hits = []
    i = data.find(pattern[0:1], lo)
    while 0 <= i < hi:
        if fn(data, i, pattern):
            hits.append(i)
        i = data.find(pattern[0:1], i + 1)
    return hits


def unique(data, pattern, what, lo, hi, fn):
    hits = find_all(data, pattern, lo, hi, fn)
    if len(hits) != 1:
        raise SystemExit('expected exactly one %s, found %d: %s' % (what, len(hits), ', '.join('%#x' % h for h in hits)))
    return hits[0]


def rel32(from_va, to_va):
    return struct.pack('<i', to_va - (from_va + 5))


def call_target(data, off, va):
    """Target VA of the 5-byte call/jmp whose opcode is at file offset `off` / VA `va`."""
    return va + 5 + struct.unpack_from('<i', data, off + 1)[0]


def reloc_entry(data, va):
    rva, rsize, rptr = sections(data)['.reloc']
    pos, end = rptr, rptr + rsize
    while pos + 8 <= end:
        page, size = struct.unpack_from('<II', data, pos)
        if size == 0:
            break
        if IMAGE_BASE + page <= va < IMAGE_BASE + page + 0x1000:
            for q in range(pos + 8, pos + size, 2):
                v = struct.unpack_from('<H', data, q)[0]
                if IMAGE_BASE + page + (v & 0xFFF) == va:
                    return q, v
        pos += size
    return None, None


def new_block(block_va, stub_cw, stub_dc, play, runner):
    b = bytearray()
    b += b'\xEB' + bytes([BLOCK_LEN - 2])                        # jmp block+95 = the menu loop
    assert len(b) == T0
    b += b'\x52'                                                   # tramp_cw_intro: push edx (gs)
    b += b'\xE8' + rel32(block_va + len(b), stub_cw)               # call stub_cw_set (exp/, esave, music CW)
    b += b'\xE8' + rel32(block_va + len(b), block_va + COMMON)     # call common (pushes the string's address)
    b += CW_MOVIE
    assert len(b) == T1
    b += b'\x52'                                                   # tramp_dc_intro: push edx
    b += b'\xE8' + rel32(block_va + len(b), stub_dc)               # call stub_dc_set (dc/, save, music DC)
    b += b'\xE8' + rel32(block_va + len(b), block_va + COMMON)     # call common
    b += DC_MOVIE
    assert len(b) == COMMON
    b += b'\x5A'                                                   # common: pop edx  (-> the movie path)
    b += b'\x50'                                                   # push eax         (the ui object)
    b += b'\xE8' + rel32(block_va + len(b), play)                  # call play_movie(eax = ui, edx = path)
    b += b'\x58'                                                   # pop eax
    b += b'\x5A'                                                   # pop edx          (gs)
    b += b'\xE9' + rel32(block_va + len(b), runner)                # jmp campaign_runner(ui, gs)
    assert len(b) == COMMON + COMMON_LEN
    b += b'\0' * (BLOCK_LEN - len(b))
    return bytes(b)


def trampoline(data, off, va):
    """fix ozi trampoline at off/va: call stub ; jmp runner -> (stub VA, runner VA), or None."""
    if data[off] != 0xE8 or data[off + 5] != 0xE9:
        return None
    return call_target(data, off, va), call_target(data, off + 5, va + 5)


def analyse(data):
    secs = sections(data)
    ava, arsize, arptr = secs['AUTO']
    game = AUTO_SIZE_OF.get(arsize)
    if game != 'cw':
        raise SystemExit('this fix is for Dark Colony Ultimate (the Council Wars exe) only; AUTO section of %#x bytes' % arsize)
    lo, hi = arptr, arptr + arsize

    def va_of(off):
        return off - arptr + ava

    def off_of(va):
        return va - ava + arptr

    cw = unique(data, SITE_CW, 'COUNCIL WARS (NEW CAMPAIGN) handler', lo, hi, matches_q)
    dc_hits = find_all(data, SITE_DC, lo, hi, matches_q)
    if len(dc_hits) != 1:
        raise SystemExit('the DARK COLONY handler of fix ozi is not in this exe (%d hits) - apply patch_ozi_menu.py first' % len(dc_hits))
    dc = dc_hits[0]
    cw_call, dc_call = cw + SITE_CW_CALL, dc + SITE_DC_CALL
    cw_target = call_target(data, cw_call, va_of(cw_call))
    dc_target = call_target(data, dc_call, va_of(dc_call))

    # the start-up block: stock (the two string loops + the call) or ours
    stock_hits = find_all(data, START_STOCK, lo, hi, matches)
    if len(stock_hits) == 1:
        state = 'stock'
        blk = stock_hits[0]
        blk_va = va_of(blk)
        play = call_target(data, blk + PLAY_CALL, blk_va + PLAY_CALL)
        # the two trampolines fix ozi left: call stub ; jmp runner
        t_cw = trampoline(data, off_of(cw_target), cw_target)
        t_dc = trampoline(data, off_of(dc_target), dc_target)
        if not t_cw or not t_dc or t_cw[1] != t_dc[1]:
            raise SystemExit('the campaign buttons do not call fix ozi\'s trampolines (COUNCIL WARS -> %#x, DARK COLONY -> %#x)' % (cw_target, dc_target))
        stub_cw, runner = t_cw
        stub_dc = t_dc[0]
    elif not stock_hits:
        state = 'patched'
        # our block: jmp +5Dh ; push edx ; call ; call ; "avi/intro.avi" ...
        pat = b'\xEB' + bytes([BLOCK_LEN - 2]) + b'\x52\xE8????\xE8????' + CW_MOVIE
        blk = unique(data, pat, 'the fix\'s start-up block', lo, hi, matches_q)
        blk_va = va_of(blk)
        if cw_target != blk_va + T0 or dc_target != blk_va + T1:
            raise SystemExit('the start-up block is ours but the campaign buttons call %#x / %#x' % (cw_target, dc_target))
        stub_cw = call_target(data, blk + T0 + 1, blk_va + T0 + 1)
        stub_dc = call_target(data, blk + T1 + 1, blk_va + T1 + 1)
        play = call_target(data, blk + COMMON + 2, blk_va + COMMON + 2)
        runner = call_target(data, blk + COMMON + 9, blk_va + COMMON + 9)
        if bytes(data[blk:blk + BLOCK_LEN]) != new_block(blk_va, stub_cw, stub_dc, play, runner):
            raise SystemExit('the start-up block at %#x is not exactly ours' % blk_va)
    else:
        raise SystemExit('expected one start-up intro block, found %d' % len(stock_hits))

    nb = new_block(blk_va, stub_cw, stub_dc, play, runner)
    edits = [
        (cw_call, bytes(data[cw_call:cw_call + 5]), b'\xE8' + rel32(va_of(cw_call), blk_va + T0),
         'COUNCIL WARS handler: call tramp_cw_campaign %#010x -> call tramp_cw_intro %#010x (mode exp/, the Council Wars intro, then the campaign)' % (cw_target if state == 'stock' else stub_cw, blk_va + T0)),
        (dc_call, bytes(data[dc_call:dc_call + 5]), b'\xE8' + rel32(va_of(dc_call), blk_va + T1),
         'DARK COLONY handler: call tramp_dc_campaign %#010x -> call tramp_dc_intro %#010x (mode dc/, the Dark Colony intro, then the campaign)' % (dc_target if state == 'stock' else stub_dc, blk_va + T1)),
        (blk, bytes(data[blk:blk + BLOCK_LEN]), nb,
         'main: the start-up intro ("avi/" + "intro.avi" built in a local buffer, play_movie %#010x) -> jmp to the menu loop %#010x; the freed 93 bytes hold tramp_cw_intro (%#010x: push edx; call stub_cw_set %#010x; call common; "avi/intro.avi"), tramp_dc_intro (%#010x: push edx; call stub_dc_set %#010x; call common; "avi/dcintro.avi") and common (%#010x: pop edx = the path; push eax; call play_movie; pop eax; pop edx; jmp campaign runner %#010x)'
         % (play, blk_va + BLOCK_LEN, blk_va + T0, stub_cw, blk_va + T1, stub_dc, blk_va + COMMON, runner)),
    ]
    relocs = []
    for k, what in ((AVI_BUF_OPERAND, 'the displaced mov esi,avi_buffer operand'), (INTRO_STR_OPERAND, 'the displaced mov esi,"intro.avi" operand')):
        q, v = reloc_entry(data, blk_va + k)
        if state == 'stock':
            if v is None or v >> 12 != 3:
                raise SystemExit('no HIGHLOW .reloc entry for the operand at %#x' % (blk_va + k))
            relocs.append((q, v, v & 0x0FFF, '%s at %#010x (now code without an absolute operand)' % (what, blk_va + k)))
        else:
            if v is None or v >> 12 != 0:
                raise SystemExit('the .reloc entry of %s at %#x is not type 0' % (what, blk_va + k))
            relocs.append((q, v, v, what))
    return dict(game=game, state=state, blk_va=blk_va, cw_va=va_of(cw_call), dc_va=va_of(dc_call), stub_cw=stub_cw, stub_dc=stub_dc,
                play=play, runner=runner, edits=edits, relocs=relocs, va_of=va_of)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    r = analyse(data)
    print('%s: Council Wars build, %s; start-up block VA 0x%08X, COUNCIL WARS call VA 0x%08X, DARK COLONY call VA 0x%08X, stub_cw_set 0x%08X, stub_dc_set 0x%08X, play_movie 0x%08X, campaign runner 0x%08X'
          % (a.exe, r['state'], r['blk_va'], r['cw_va'], r['dc_va'], r['stub_cw'], r['stub_dc'], r['play'], r['runner']))
    if a.command == 'verify':
        return 0
    for off, old, new, note in r['edits']:
        head, _, tail = note.partition(':')
        print('  %s VA 0x%x file 0x%x %d bytes: %s -> %s;%s' % (head, r['va_of'](off), off, len(old), old.hex(' '), new.hex(' '), tail))
    for off, old, new, note in r['relocs']:
        print('  .reloc @ file 0x%x: %04X -> %04X  (%s)' % (off, old, new, note))
    if a.command == 'plan':
        return 0
    if r['state'] != 'stock':
        print('exe already patched, nothing to do')
        return 0
    bak = a.exe + '.intro.bak'
    shutil.copyfile(a.exe, bak)
    for off, old, new, note in r['edits']:
        assert bytes(data[off:off + len(old)]) == old
        data[off:off + len(new)] = new
    for off, old, new, note in r['relocs']:
        assert struct.unpack_from('<H', data, off)[0] == old
        struct.pack_into('<H', data, off, new)
    open(a.exe, 'wb').write(data)
    print('written %s (%d code edits, %d .reloc entries); backup %s' % (a.exe, len(r['edits']), len(r['relocs']), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

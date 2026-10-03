#!/usr/bin/env python3
"""No save in a network battle (fix `netsave`, both games): the Save Game cell of the Game Option tab is hidden
and the F11 / `?` save dialog does nothing while the game type is 2 (network game).

Why (3 Oct 2026, maintainer: "when playing network game there is a 'save' button available. is this saved
mission visible in main menu 'LOAD GAME' form?" -> "hide the save button in network battles and update
patcher"; DC16_DISPLAY_AND_RESOLUTION.md section 10.68): the in-battle save dialog (`0x00432708`, Council Wars
`0x00432768`; reached from the F11 key through the client's event table at `0x0040A63B` / `+0x60` and from
the Game Option tab's `?` cell through the dialog handler at `0x0043388E` / `+0x60`) has no game-type check,
so a network battle could be saved into `save\\<name>.dcg` with game type 2 in the header.  The main menu's
load routine (`0x00403AA4`) resumes such a save through the network entry `0x0040122C` with no address and no
network object: the machine becomes the host of the in-process mailbox network, the lobby is skipped, and
the battle resumes from the file with every other human seat present but unconnected - a solo continuation
against frozen opponents, never a reconnection to the relay (the protocol has no save or resume message).
The one-button LOAD GAME picker of 3 Oct 2026 lists such a file as "Multiplayer".  Saving is therefore
switched off in network battles; campaign (0), skirmish (1) and training (3) games save as before.

How (two in-place hooks + two stubs in dead code, 6 edits per exe, Council Wars code at +0x60):
  1. game start (`proto.c` `0x0041EAA0`): the network branch of the HUD set-up ends with the 19 bytes
     `mov edx,94h ; mov eax,[hud_ip] ; xor ebx,ebx ; call set_flag5 ; jmp continue` (Classic `0x0041EC81`,
     Council Wars `0x0041ECE1`; the campaign branch right after it hides the Allies cell 151 with
     `widget_enable(ip, 151, 0)`).  Those 19 bytes become `jmp stub_a ; 14 x nop`, and the displaced
     `mov eax,[hud_ip]` loses its HIGHLOW .reloc entry (-> type 0).
  2. `stub_a` (55 bytes) repeats the displaced call and then, when `gs->scenario->game_type == 2`
     (`[ebp-4]` is the battle state in that frame, `+0x544` the campaign record, `+0x14F0` the type),
     calls `widget_enable(ip, 63, 0)` - the per-widget "enabled" byte (`ip+0x88+0x34*id+3`), which the
     tab switch does not touch (it sets the "shown" byte +2 of a group's members) and which both the
     widget drawer `0x00421AA4` and the push-button hit test `0x00426D2C` require - then jumps on.
  3. the save dialog's first five bytes `push ebx ; push ecx ; push edx ; push esi ; push edi` become
     `jmp stub_b`.
  4. `stub_b` (34 bytes, eax = client): `push ecx ; mov ecx,[eax+0Ch] (gs) ; mov ecx,[ecx+544h] ;
     cmp dword [ecx+14F0h],2 ; pop ecx ; je ret ; the five pushes ; jmp dialog+5 ; ret` - F11, the `?`
     key and the (hidden) cell all end in this function, and both callers ignore its result.
  Both stubs live in the wave loader's dead CD attempt (`0x00452B05..0x00452B5D` Classic, `+0x60` Council
  Wars): dead since fix `nocd` turned the second open's `jne` into a `jmp` past it, the first 22 bytes
  taken by fix `longpath`'s `open_read`, nothing jumps into the rest.  The stock code there held one
  absolute operand (`.reloc` HIGHLOW at `base+0x4B`); that entry is re-pointed to `stub_a`'s
  `mov eax,[hud_ip]` operand at `base+1`, so the .reloc table stays exact.  Register use: `set_flag5`
  keeps ebx, `widget_enable` takes bl; the continuation reloads eax/ebx/ecx/edx itself.
Requires fix `nocd` (the dead region).  `plan` works on the untouched exe; `apply` refuses without `nocd`.

CLI
    python patch_netsave.py verify EXE
    python patch_netsave.py plan   EXE
    python patch_netsave.py apply  EXE        (writes EXE.netsave.bak first)
"""

import argparse
import shutil
import struct
import sys

IMAGE_BASE = 0x400000
AUTO_SIZE_OF = {0x7E200: 'classic', 0x7E400: 'cw'}      # raw size of the code section, same in the `.dcicon` builds

# game start, end of the network branch: mov edx,94h ; mov eax,[hud_ip] ; xor ebx,ebx ; call set_flag5 ; jmp +11h ;
#   (campaign branch) mov edx,97h ; mov eax,[hud_ip]
SITE1 = b'\xBA\x94\x00\x00\x00\xA1????\x31\xDB\xE8????\xEB\x11\xBA\x97\x00\x00\x00\xA1????\x31\xDB\xE8????'
SITE1_LEN = 19
SITE1_NEW_TAIL = b'\x90' * 14
# save dialog entry: push ebx,ecx,edx,esi,edi ; push ebp ; mov ebp,esp ; sub esp,4 ; mov esi,eax ; mov eax,[eax+8] ;
#   mov ebx,"intrface/lsg" ; xor edx,edx ; mov eax,[eax+24h] ; call ; mov eax,[esi+8] ; mov ebx,str ; mov edx,18h
SITE2 = b'\x53\x51\x52\x56\x57\x55\x89\xE5\x83\xEC\x04\x89\xC6\x8B\x40\x08\xBB????\x31\xD2\x8B\x40\x24\xE8????\x8B\x46\x08\xBB????\xBA\x18\x00\x00\x00'
SITE2_LEN = 5
# wave loader, second live open (stock form or the `longpath` form), followed by mov esi,eax ; cmp eax,-1 ; exit (6) ; open_read (22)
OPEN_B_STOCK = b'\x6A\x00\x8D\x45\xF2\x50\x53\x2E\xFF\x15????\x89\xC6\x83\xF8\xFF'
OPEN_B_LONGPATH = b'\x89\xD8\xE8????' + b'\x90' * 7 + b'\x89\xC6\x83\xF8\xFF'
OPEN_B_LEN = 14
NOCD_EXIT = b'\xE9????\x90'
STOCK_EXIT = b'\x0F\x85????'
LONGPATH_STUB = 22
# the stock bytes of the dead CD attempt after open_read (the rel32 of its last call differs between the builds)
DEAD = bytes.fromhex('8a 46 01 83 c6 02 88 47 01 83 c7 02 3c 00 75 e8 5f 8d bd f2 fb ff ff 89 de 57 2b c9 49 b0 00 f2 ae 4f'
                     '8a 06 88 07 3c 00 74 10 8a 46 01 83 c6 02 88 47 01 83 c7 02 3c 00 75 e8 5f 6a 00 8d 45 f2 50 8d 85'
                     'f2 fb ff ff 50 2e ff 15 c8 04 48 00 89 c6 83 f8 ff 75 6b e8') + b'????'
DEAD_RELOC_AT = 0x4B                 # the dead code's one absolute operand (push offset ...), page offset relative to base
STUB_A_LEN = 55
STUB_B_LEN = 34
SAVE_WIDGET = 0x3F                   # MAINE `pushb 63` = the Save Game cell of the Game Option tab (the id doubles as its key `?`)
GAME_TYPE_NETWORK = 2


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


def matches(data, off, pattern):
    return off + len(pattern) <= len(data) and all(p == 0x3F or data[off + k] == p for k, p in enumerate(pattern))


def find_all(data, pattern, lo, hi):
    hits = []
    i = data.find(pattern[0:1], lo)
    while 0 <= i < hi:
        if matches(data, i, pattern):
            hits.append(i)
        i = data.find(pattern[0:1], i + 1)
    return hits


def unique(data, pattern, what, lo, hi):
    hits = find_all(data, pattern, lo, hi)
    if len(hits) != 1:
        raise SystemExit('expected exactly one %s, found %d: %s' % (what, len(hits), ', '.join('%#x' % h for h in hits)))
    return hits[0]


def rel32(from_va, to_va):
    """Operand of a 5-byte call/jmp at from_va reaching to_va."""
    return struct.pack('<i', to_va - (from_va + 5))


def reloc_entry(data, va):
    """(file offset of the .reloc word, its value) for the entry describing the dword at VA, or (None, None)."""
    rva, rsize, rptr = sections(data)['.reloc']
    pos, end = rptr, rptr + rsize
    while pos + 8 <= end:
        page, size = struct.unpack_from('<II', data, pos)
        if size == 0:
            break
        if IMAGE_BASE + page <= va < IMAGE_BASE + page + 0x1000:
            for q in range(pos + 8, pos + size, 2):
                v = struct.unpack_from('<H', data, q)[0]
                if v >> 12 and IMAGE_BASE + page + (v & 0xFFF) == va:
                    return q, v
        pos += size
    return None, None


def stub_a(base_va, hud_ip, set_flag5, widget_enable, cont):
    b = bytearray()
    b += b'\xA1' + struct.pack('<I', hud_ip)                       # mov eax,[hud_ip]          (abs operand at base+1)
    b += b'\xBA\x94\x00\x00\x00'                                   # mov edx,94h               the displaced call
    b += b'\x31\xDB'                                               # xor ebx,ebx
    b += b'\x50'                                                   # push eax
    b += b'\xE8' + rel32(base_va + len(b), set_flag5)              # call set_flag5(ip, 148, 0)
    b += b'\x58'                                                   # pop eax                   ip again
    b += b'\x8B\x55\xFC'                                           # mov edx,[ebp-4]           the battle state
    b += b'\x8B\x92\x44\x05\x00\x00'                               # mov edx,[edx+544h]        its campaign record
    b += b'\x81\xBA\xF0\x14\x00\x00' + struct.pack('<I', GAME_TYPE_NETWORK)   # cmp dword [edx+14F0h],2
    b += b'\x75\x0A'                                               # jne +10                   not a network game: keep the cell
    b += b'\xBA' + struct.pack('<I', SAVE_WIDGET)                  # mov edx,63
    b += b'\xE8' + rel32(base_va + len(b), widget_enable)          # call widget_enable(ip, 63, bl = 0)
    b += b'\xE9' + rel32(base_va + len(b), cont)                   # jmp continue
    assert len(b) == STUB_A_LEN, len(b)
    return bytes(b)


def stub_b(base_va, dialog_va):
    b = bytearray()
    b += b'\x51'                                                   # push ecx
    b += b'\x8B\x48\x0C'                                           # mov ecx,[eax+0Ch]         client -> gs
    b += b'\x8B\x89\x44\x05\x00\x00'                               # mov ecx,[ecx+544h]        campaign record
    b += b'\x81\xB9\xF0\x14\x00\x00' + struct.pack('<I', GAME_TYPE_NETWORK)   # cmp dword [ecx+14F0h],2
    b += b'\x59'                                                   # pop ecx                   (flags kept)
    b += b'\x74\x0A'                                               # je ret                    network game: no dialog
    b += b'\x53\x51\x52\x56\x57'                                   # push ebx,ecx,edx,esi,edi  the displaced prologue
    b += b'\xE9' + rel32(base_va + len(b), dialog_va + SITE2_LEN)  # jmp dialog+5
    b += b'\xC3'                                                   # ret
    assert len(b) == STUB_B_LEN, len(b)
    return bytes(b)


def analyse(data):
    secs = sections(data)
    ava, arsize, arptr = secs['AUTO']
    game = AUTO_SIZE_OF.get(arsize)
    if game is None:
        raise SystemExit('AUTO section of %#x bytes: neither the Classic nor the Council Wars build' % arsize)
    lo, hi = arptr, arptr + arsize

    def va_of(off):
        return off - arptr + ava

    def off_of(va):
        return va - ava + arptr

    # --- site 1: game start, network branch end (stock) or our jmp (patched)
    s1_hits = find_all(data, SITE1, lo, hi)
    if len(s1_hits) == 1:
        state = 'stock'
        s1 = s1_hits[0]
    elif not s1_hits:
        state = 'patched'
        pat = b'\xE9????' + SITE1_NEW_TAIL + SITE1[SITE1_LEN:]
        s1 = unique(data, pat, 'patched game-start hook', lo, hi)
    else:
        raise SystemExit('expected one game-start network branch, found %d' % len(s1_hits))
    s1_va = va_of(s1)
    camp = s1 + SITE1_LEN                                    # the campaign branch: mov edx,97h ; mov eax,[hud_ip] ; xor ebx,ebx ; call widget_enable
    hud_ip = struct.unpack_from('<I', data, camp + 6)[0]
    widget_enable = va_of(camp + 12) + 5 + struct.unpack_from('<i', data, camp + 13)[0]
    if state == 'stock':
        if struct.unpack_from('<I', data, s1 + 6)[0] != hud_ip:
            raise SystemExit('the two branches at %#x read different HUD pointers' % s1_va)
        set_flag5 = s1_va + 12 + 5 + struct.unpack_from('<i', data, s1 + 13)[0]
    else:
        set_flag5 = None                                     # recovered from the stub below
    cont = s1_va + SITE1_LEN + 0x11                          # target of the displaced `jmp +11h` = right after the campaign branch
    if cont != va_of(camp + 17):
        raise SystemExit('unexpected continuation %#x' % cont)

    # --- site 2: the save dialog entry
    if state == 'stock':
        s2 = unique(data, SITE2, 'save dialog entry', lo, hi)
    else:
        s2 = unique(data, b'\xE9????' + SITE2[SITE2_LEN:], 'patched save dialog entry', lo, hi)
    s2_va = va_of(s2)

    # --- the dead CD attempt of the wave loader, after fix longpath's open_read
    b_hits = find_all(data, OPEN_B_STOCK, lo, hi) + find_all(data, OPEN_B_LONGPATH, lo, hi)
    if len(b_hits) != 1:
        raise SystemExit('expected exactly one wave-loader second open, found %d' % len(b_hits))
    exit_off = b_hits[0] + OPEN_B_LEN + 5
    nocd = matches(data, exit_off, NOCD_EXIT)
    if not nocd and not matches(data, exit_off, STOCK_EXIT):
        raise SystemExit('the second open at %#x is followed by neither the stock `jne near` nor the `jmp` of fix nocd' % va_of(b_hits[0]))
    base = exit_off + 6 + LONGPATH_STUB
    base_va = va_of(base)
    sa = stub_a(base_va, hud_ip, set_flag5 if set_flag5 is not None else 0, widget_enable, cont)
    sb = stub_b(base_va + STUB_A_LEN, s2_va)
    if state == 'stock':
        if not matches(data, base, DEAD):
            raise SystemExit('the dead CD attempt at %#x does not look like the stock code: %s' % (base_va, bytes(data[base:base + 16]).hex(' ')))
    else:
        # recover set_flag5 from the stub's own call and re-check every byte
        set_flag5 = base_va + 13 + 5 + struct.unpack_from('<i', data, base + 14)[0]     # the call at stub_a+13
        sa = stub_a(base_va, hud_ip, set_flag5, widget_enable, cont)
        if bytes(data[base:base + STUB_A_LEN]) != sa or bytes(data[base + STUB_A_LEN:base + STUB_A_LEN + STUB_B_LEN]) != sb:
            raise SystemExit('the hooks are in place but the stubs at %#x are not ours' % base_va)
    old_a = bytes(data[base:base + STUB_A_LEN])
    old_b = bytes(data[base + STUB_A_LEN:base + STUB_A_LEN + STUB_B_LEN])

    # --- .reloc: the displaced operand's entry -> type 0; the dead operand's entry -> stub_a's operand
    r1_off, r1_val = reloc_entry(data, s1_va + 6)
    r2_off, r2_val = reloc_entry(data, base_va + DEAD_RELOC_AT)
    r2_new = 0x3000 | ((base_va + 1) & 0xFFF)
    if state == 'stock':
        if r1_val is None or r1_val >> 12 != 3:
            raise SystemExit('no HIGHLOW .reloc entry for the operand at %#x' % (s1_va + 6))
        if r2_val is None or r2_val >> 12 != 3:
            raise SystemExit('no HIGHLOW .reloc entry for the dead operand at %#x' % (base_va + DEAD_RELOC_AT))
        r1_new = r1_val & 0x0FFF
        r1_old, r2_old = r1_val, r2_val
    else:
        r1_old = r1_new = struct.unpack_from('<H', data, r1_off)[0] if r1_off else None
        r2_off, r2_old = reloc_entry(data, base_va + 1)
        if r2_old != r2_new:
            raise SystemExit('the .reloc entry of stub_a\'s operand is missing')

    new1 = b'\xE9' + rel32(s1_va, base_va) + SITE1_NEW_TAIL
    new2 = b'\xE9' + rel32(s2_va, base_va + STUB_A_LEN)
    edits = [
        (s1, bytes(data[s1:s1 + SITE1_LEN]), new1,
         'game start, network branch end: mov edx,94h; mov eax,[hud_ip %#010x]; xor ebx,ebx; call %#010x; jmp %#010x -> jmp stub_a %#010x; 14 x nop'
         % (hud_ip, set_flag5, cont, base_va)),
        (s2, bytes(data[s2:s2 + SITE2_LEN]), new2,
         'save dialog entry: push ebx,ecx,edx,esi,edi -> jmp stub_b %#010x' % (base_va + STUB_A_LEN)),
        (base, old_a, sa,
         'stub_a over the dead CD attempt: the displaced call, then if [ebp-4]->544h->14F0h (game type) == 2: widget_enable %#010x (ip, 63, 0) = the Save Game cell disabled; jmp %#010x'
         % (widget_enable, cont)),
        (base + STUB_A_LEN, old_b, sb,
         'stub_b: if client->gs->544h->14F0h == 2 return (no save dialog in a network battle), else the five pushes and jmp %#010x' % (s2_va + SITE2_LEN)),
    ]
    relocs = [(r1_off, r1_old, r1_new, 'the displaced mov eax,[hud_ip] operand at %#010x (now nop)' % (s1_va + 6)),
              (r2_off, r2_old, r2_new, 'the dead CD attempt\'s absolute operand at %#010x -> stub_a\'s mov eax,[hud_ip] operand at %#010x' % (base_va + DEAD_RELOC_AT, base_va + 1))]
    return dict(game=game, state=state, nocd=nocd, s1_va=s1_va, s2_va=s2_va, base_va=base_va, hud_ip=hud_ip,
                set_flag5=set_flag5, widget_enable=widget_enable, cont=cont, edits=edits, relocs=relocs, va_of=va_of)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    r = analyse(data)
    print('%s: %s build, %s, fix nocd %s; game-start hook VA 0x%08X, save dialog VA 0x%08X, stubs at VA 0x%08X (dead CD attempt), HUD pointer 0x%08X, set_flag5 0x%08X, widget_enable 0x%08X, continue 0x%08X'
          % (a.exe, 'Classic' if r['game'] == 'classic' else 'Council Wars', r['state'], 'applied' if r['nocd'] else 'NOT applied',
             r['s1_va'], r['s2_va'], r['base_va'], r['hud_ip'], r['set_flag5'], r['widget_enable'], r['cont']))
    if a.command == 'verify':
        return 0
    for off, old, new, note in r['edits']:
        head, _, tail = note.partition(':')
        print('  %s VA 0x%x file 0x%x %d bytes: %s -> %s;%s' % (head, r['va_of'](off), off, len(old), old.hex(' '), new.hex(' '), tail))
    for off, old, new, note in r['relocs']:
        if off is not None:
            print('  .reloc @ file 0x%x: %04X -> %04X  (%s)' % (off, old, new, note))
    if a.command == 'plan':
        return 0
    if r['state'] != 'stock':
        print('exe already patched, nothing to do')
        return 0
    if not r['nocd']:
        raise SystemExit('refusing to apply: fix nocd is not applied (the stubs go into the wave loader\'s CD attempt, '
                         'dead only once nocd skips it) - apply patch_nocd.py first')
    bak = a.exe + '.netsave.bak'
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

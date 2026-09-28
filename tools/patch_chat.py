#!/usr/bin/env python3
r"""Battlefield chat: six lines and the mission-message sound (dc16.exe / DCEXP16.EXE, fix `chat`).

In a network game a chat line (`0x0E from, to_mask, text`, protocol doc §4.3) is queued by the
in-game handler `0x0041DA2C` (Council Wars +0x60) in the net object's six-entry ring (`+0x310`
count, `+0x314` newest index, `+0x318` 88-byte entries) and shown by the client's per-frame chat
display `0x0040B10D..0x0040B221` through at most TWO `MAINE` widgets: `in_text 203` (the lower
line) and `204`, ids `0xCB + i`.  Nothing plays a sound.  A single-player mission message (trigger
`msg`, action 11 of `0x0043D904` → `0x0044D88C`) is shown by a different module (`0x00433C44`) in
the bottom bar's `in_text 148` and announces itself with sound table entry **187 = `SOUND\MSG.WAV`**
through the display object's play slot (`[display+0x7C](eax=187, edx=1)`, `display = client+8`).
Maintainer, 28 Sep 2026: "comments on the multiplayer battlefield must play the same sound as is
playing for comments in single mode missions. on the battlefield we must have six lines of sent
comments."  Doc: DC16_DISPLAY_AND_RESOLUTION.md §10.48.  Confirmed in game (Ultimate 1920x1200, relay battle) 28 Sep 2026.

Three edits per exe, identical bytes in both builds (only register-relative operands and rel32
calls whose targets move with the code; no `.reloc` change):

  1. the chat display `0x0040B10D..0x0040B221` (276 bytes) rewritten in place (243 bytes + NOPs):
     - first `call helper`: if the handler left the marker `0x7FFFFFFF` in `+0x314`, a line has
       arrived since the last frame → `display->play_sound(187, 1)`, exactly the mission drawer's call;
     - the newest-line index is capped at 5 instead of 1 and the loop shows up to SIX lines, line
       i in widget 203 + i for i < 2 and 205 + i for i >= 2 (207..210 - `count 205/206` are taken);
     - every line is drawn only if `ip->objects[id].type == 4` (`in_text`), so a script with the
       stock two lines (640x480, or an old set) shows two lines and never asserts (`0x00423E74`
       asserts on a missing widget);
     - the stock drain timer is kept (the oldest line drops 7.5 s after the previous drop); its
       fast tier (2.5 s while more lines wait than are shown) can no longer occur with six shown.
  2. the handler's `inc dword ptr [esi+310h]` (6 bytes at `0x0041DB0F`) → `call stub; nop`.
  3. `stub` + `helper` (51 bytes) in the NOP room fix `palette` left in `set_palette`
     (`0x0042F503..0x0042F54D`, 75 bytes; CW +0x60): the stub does the `inc` and writes the marker
     into `+0x314` (the display recomputes that field every frame, so nothing else reads it);
     the helper is the marker test + sound call.  **Requires `palette`** (the room is live code
     in the original); `plan` on an untouched exe reports the room's old bytes as the NOPs `palette`
     writes there (the patcher applies `palette` first and checks the bytes it finds), `apply`
     refuses without them.

The interface side is data: `hud_layout.py maine` (and the patcher's `Edit-HudScript`) add
`in_text 207..210` above the two stock lines of `INTRF_HD/MAINE`, 15 rows apart, so the HD sets
show six lines; the stock 640x480 `INTRFACE/MAINE` keeps two.

CLI
    python patch_chat.py verify EXE
    python patch_chat.py plan   EXE
    python patch_chat.py apply  EXE          (writes EXE.chat.bak first)
"""

import argparse
import os
import re
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patch_palette                                  # STOCK_BODY / NEW_BODY_CODE: where its NOP room is

IMAGE_BASE = 0x400000

# the stock chat display, 0x0040B10D..0x0040B221 (Classic; Council Wars +0x60 with identical bytes)
STOCK_BLOCK = bytes.fromhex(
    '8b b8 10 03 00 00 85 ff 0f 8e 7e 00 00 00 8b 55 f8 8b 8a 9c 46 00 00 85 c9 75 0d e8 03 03 00 00'
    '89 82 9c 46 00 00 eb 36 83 ff 02 7e 07 ba c4 09 00 00 eb 05 ba 4c 1d 00 00 8b 4d f8 e8 e2 02 00 00'
    '2b 81 9c 46 00 00 39 d0 76 13 8b 41 0c e8 f0 27 01 00 e8 cb 02 00 00 89 81 9c 46 00 00 8b 45 f8'
    '8b 40 0c 8b 90 10 03 00 00 4a 89 90 14 03 00 00 8b 45 f8 8b 40 0c 83 b8 14 03 00 00 01 7e 19 c7 80'
    '14 03 00 00 01 00 00 00 eb 0d 8b 45 f8 c7 80 9c 46 00 00 00 00 00 00 31 f6 eb 65 8d 04 8d 00 00 00'
    '00 29 c8 c1 e0 02 29 c8 8d ba 18 03 00 00 c1 e0 03 01 c7 69 47 50 34 0e 00 00 8d 8e cb 00 00 00'
    '8b 9c 02 9c 0c 00 00 8b 45 f8 89 ca 8b 80 f4 07 00 00 e8 f5 8d 01 00 8b 45 f8 89 fb 89 ca 8b 80'
    'f4 07 00 00 e8 7b 8c 01 00 8b 45 f8 89 ca 8b 80 f4 07 00 00 46 e8 9a 68 01 00 83 fe 02 7d 12 8b'
    '55 f8 8b 52 0c 8b 8a 14 03 00 00 29 f1 85 c9 7d 89')
assert len(STOCK_BLOCK) == 276

# the new display (assembled by the development script chat_asm.py; rel32 operands relative to the block)
NEW_BLOCK_CODE = bytes.fromhex(
    'e8 ?? ?? ?? ??'                        # call helper (marker test + MSG.WAV)             [rel32 filled in]
    '8b b8 10 03 00 00'                     # mov edi,[eax+310h]           count
    '85 ff 7e 5b'                           # test edi,edi; jle none
    '8b 4d f8'                              # mov ecx,[ebp-8]              client
    '8b 91 9c 46 00 00'                     # mov edx,[ecx+469Ch]          drop timer
    '85 d2 75 0d'                           # test edx,edx; jne timed
    'e8 ?? ?? ?? ??'                        # call now                                        [rel32]
    '89 81 9c 46 00 00'                     # mov [ecx+469Ch],eax
    'eb 25'                                 # jmp cap
    'e8 ?? ?? ?? ??'                        # timed: call now                                 [rel32]
    '2b 81 9c 46 00 00'                     # sub eax,[ecx+469Ch]
    '3d 4c 1d 00 00 76 13'                  # cmp eax,1D4Ch; jbe cap       7.5 s per drop (the stock slow tier)
    '8b 41 0c'                              # mov eax,[ecx+0Ch]            gs
    'e8 ?? ?? ?? ??'                        # call drop_oldest                                [rel32]
    'e8 ?? ?? ?? ??'                        # call now                                        [rel32]
    '89 81 9c 46 00 00'                     # mov [ecx+469Ch],eax
    '8b 41 0c'                              # cap: mov eax,[ecx+0Ch]
    '8b 90 10 03 00 00 4a'                  # mov edx,[eax+310h]; dec edx
    '83 fa 05 7e 05 ba 05 00 00 00'         # cmp edx,5; jle store; mov edx,5
    '89 90 14 03 00 00'                     # store: mov [eax+314h],edx     newest shown line
    'eb 0d'                                 # jmp loop_init
    '8b 45 f8 c7 80 9c 46 00 00 00 00 00 00'  # none: timer = 0
    '8b 45 f8 ff b0 f4 07 00 00'            # loop_init: push [client+7F4h]  (the interface object)
    '31 f6 eb 5c'                           # xor esi,esi; jmp cond
    '6b f9 58'                              # body: imul edi,ecx,58h        entry = 88 * index
    '8d bc 3a 18 03 00 00'                  # lea edi,[edx+edi+318h]
    '8d 8e cb 00 00 00'                     # lea ecx,[esi+0CBh]           widget 203 + i
    '83 fe 02 7c 03 83 c1 02'               # cmp esi,2; jl have_id; add ecx,2   (207..210 for lines 3..6)
    '8b 04 24'                              # have_id: mov eax,[esp]        ip
    '6b d9 34'                              # imul ebx,ecx,34h
    '80 bc 18 89 00 00 00 04 75 2e'         # cmp byte ptr [eax+ebx+89h],4; jne next   (objects[id].type == in_text?)
    '69 47 50 34 0e 00 00'                  # imul eax,[edi+50h],0E34h      sender
    '8b 9c 02 9c 0c 00 00'                  # mov ebx,[edx+eax+0C9Ch]       sender's colour
    '8b 04 24 89 ca e8 ?? ?? ?? ??'         # mov eax,[esp]; mov edx,ecx; call set_colour        [rel32]
    '8b 04 24 89 fb 89 ca e8 ?? ?? ?? ??'   # mov eax,[esp]; mov ebx,edi; mov edx,ecx; call set_text   [rel32]
    '8b 04 24 89 ca e8 ?? ?? ?? ??'         # mov eax,[esp]; mov edx,ecx; call reveal            [rel32]
    '46 83 fe 06 7d 12'                     # next: inc esi; cmp esi,6; jge done
    '8b 55 f8 8b 52 0c'                     # cond: mov edx,[ebp-8]; mov edx,[edx+0Ch]
    '8b 8a 14 03 00 00 29 f1 85 c9 7d 92'   # mov ecx,[edx+314h]; sub ecx,esi; test ecx,ecx; jge body
    '58'.replace('??', '00'))               # done: pop eax
# offsets of the rel32 operands inside NEW_BLOCK_CODE and their targets (Classic VAs; CW +0x60 = same rel32)
REL32 = [(0, 'helper'), (0x1C, 'now'), (0x29, 'now'), (0x3E, 'drop'), (0x43, 'now'), (0xBF, 'colour'), (0xCB, 'text'), (0xD5, 'reveal')]
assert all(NEW_BLOCK_CODE[o] == 0xE8 for o, _ in REL32)
TARGETS = {'now': 0x40B430, 'drop': 0x41D950, 'colour': 0x423FDC, 'text': 0x423E74, 'reveal': 0x421AA4}
assert len(NEW_BLOCK_CODE) == 243

# the handler's `inc dword ptr [esi+310h]`, located by its context (the cheat-code scan that follows)
STOCK_INC_CONTEXT = 'ff 86 10 03 00 00 80 3f 3a 74 03 47 eb f8 83 c7 02'

# the stub (17 bytes) and the helper (34 bytes), written into the room fix `palette` leaves in set_palette
STUB = bytes.fromhex('ff 86 10 03 00 00'                 # inc dword ptr [esi+310h]          (the replaced instruction)
                     'c7 86 14 03 00 00 ff ff ff 7f'     # mov dword ptr [esi+314h],7FFFFFFFh  marker: a new line
                     'c3')                               # ret
HELPER = bytes.fromhex('81 b8 14 03 00 00 ff ff ff 7f'   # cmp dword ptr [eax+314h],7FFFFFFFh
                       '75 15'                           # jne back
                       '8b 5d f8 8b 5b 08'               # mov ebx,[ebp-8]; mov ebx,[ebx+8]   display object
                       '50'                              # push eax
                       'b8 bb 00 00 00'                  # mov eax,0BBh                        sound table entry 187 = sound\msg.wav
                       'ba 01 00 00 00'                  # mov edx,1
                       'ff 53 7c'                        # call [ebx+7Ch]                      display->play_sound
                       '58'                              # pop eax
                       'c3')                             # back: ret
ROOM_OFFSET = len(patch_palette.NEW_BODY_CODE) + 2       # the room starts after palette's code + its short jmp
ROOM_LEN = 135 - ROOM_OFFSET                             # 75


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

    def find(self, hexpat, name, adjust=0, allow_none=False):
        pat = b''.join(b'.' if t == '??' else re.escape(bytes.fromhex(t)) for t in hexpat.split())
        hits = [m.start() for m in re.finditer(pat, self.auto, re.DOTALL)]
        if len(hits) != 1:
            if allow_none and not hits:
                return None
            raise SystemExit('%s: expected one match, found %d' % (name, len(hits)))
        return self.auto_va + hits[0] + adjust


def rel32(src, dst):
    return struct.pack('<i', dst - (src + 5))


def new_block(block_va, helper_va, delta):
    """The new display with its rel32 operands filled in (`delta` = 0 Classic, 0x60 Council Wars)."""
    b = bytearray(NEW_BLOCK_CODE)
    for off, name in REL32:
        dst = helper_va if name == 'helper' else TARGETS[name] + delta
        b[off + 1:off + 5] = rel32(block_va + off, dst)
    return bytes(b) + b'\x90' * (len(STOCK_BLOCK) - len(b))


def sites_for(img):
    """[(name, va, old, new, note)]; `old` of the room is the palette state (NOPs) even on an untouched exe.
    Returns (sites, needs_palette)."""
    # 1. the chat display block (stock or already ours)
    stock_va = img.find(STOCK_BLOCK.hex(' '), 'chat display (stock)', allow_none=True)
    ours_va = img.find(NEW_BLOCK_CODE[5:0x1D].hex(' '), 'chat display (patched)', adjust=-5, allow_none=True)
    block_va = stock_va if stock_va is not None else ours_va
    if block_va is None:
        raise SystemExit('the chat display 0x0040B10D (neither stock nor patched) was not found')
    delta = block_va - 0x40B10D
    if delta not in (0, 0x60):
        raise SystemExit('unexpected chat display address %#x' % block_va)
    # 2. the handler's inc (stock) or our call (patched)
    inc_va = img.find(STOCK_INC_CONTEXT, 'chat handler inc', allow_none=True)
    call_va = img.find('e8 ?? ?? ?? ?? 90 80 3f 3a 74 03 47 eb f8 83 c7 02', 'chat handler call', allow_none=True)
    site_va = inc_va if inc_va is not None else call_va
    if site_va is None or site_va != 0x41DB0F + delta:
        raise SystemExit('the chat handler site 0x0041DB0F (neither stock nor patched) was not found (%s)' % (site_va and hex(site_va)))
    # 3. the room: after fix palette's new body, or where the stock body still sits (palette missing)
    pal_new = img.find(patch_palette.NEW_BODY_CODE.hex(' '), 'palette: new body', allow_none=True)
    pal_stock = img.find(patch_palette.STOCK_BODY, 'palette: stock body', allow_none=True)
    body_va = pal_new if pal_new is not None else pal_stock
    if body_va is None:
        raise SystemExit('set_palette (fix palette site) not found')
    if body_va != 0x42F4C7 + delta:
        raise SystemExit('unexpected set_palette body address %#x' % body_va)
    needs_palette = pal_new is None
    room_va = body_va + ROOM_OFFSET
    stub_va = room_va
    helper_va = room_va + len(STUB)
    room_new = STUB + HELPER
    room_old = b'\x90' * len(room_new)
    sites = [
        ('client.c chat display: 6 lines, type check, sound on a new line', block_va, STOCK_BLOCK,
         new_block(block_va, helper_va, delta),
         'the 276-byte display of the chat ring rewritten in place: call helper (marker -> sound 187), newest index capped at 5, '
         'line i -> widget 203+i (i<2) / 205+i, drawn only if objects[id].type == 4, drain 7.5 s per oldest line'),
        ('chat handler: inc count -> call stub', site_va, bytes.fromhex(STOCK_INC_CONTEXT)[:6],
         b'\xE8' + rel32(site_va, stub_va) + b'\x90',
         'the queued line is counted by the stub, which also leaves the new-line marker 7FFFFFFF in the ring index'),
        ('stub + helper in the palette NOP room', room_va, room_old, room_new,
         'stub: inc [esi+310h]; mov [esi+314h],7FFFFFFF; ret.  helper: if marker, display->play_sound(187 = sound\\msg.wav, 1); ret'),
    ]
    return sites, needs_palette, delta


def state_of(cur, old, new):
    if cur == new:
        return 'patched'
    if cur == old:
        return 'stock'
    return 'UNKNOWN'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    img = Image(data)
    sites, needs_palette, delta = sites_for(img)
    print('%s: %s build; chat display VA 0x%08X, handler site 0x%08X, room 0x%08X (%d of %d bytes)%s'
          % (a.exe, 'Council Wars' if delta else 'Classic', sites[0][1], sites[1][1], sites[2][1],
             len(sites[2][3]), ROOM_LEN, '; fix palette NOT applied (its room is still live code)' if needs_palette else ''))
    states = []
    for name, va, old, new, note in sites:
        f = img.va2file(va)
        cur = bytes(data[f:f + len(new)])
        if name.startswith('stub') and needs_palette:
            st = 'awaiting palette'                   # the room still holds the stock palette loop
        else:
            st = state_of(cur, old, new)
        states.append(st)
        print('  %-58s VA %#x file %#x: %s' % (name, va, f, st))
    kinds = set(states)
    overall = ('patched' if kinds == {'patched'} else
               'stock' if kinds <= {'stock', 'awaiting palette'} else 'MIXED')
    print('%s: %s' % (a.exe, overall))
    if a.command == 'verify':
        return 0 if overall in ('stock', 'patched') else 1
    if overall == 'patched':
        print('already patched, nothing to do')
        return 0
    if overall != 'stock':
        raise SystemExit('refusing: not in stock state')
    for name, va, old, new, note in sites:
        f = img.va2file(va)
        print('  %s   VA 0x%x file 0x%x %d bytes: %s -> %s; %s' % (name, va, f, len(new), old.hex(' '), new.hex(' '), note))
    if a.command == 'plan':
        if needs_palette:
            print('note: the room bytes above are those fix palette writes (NOPs); apply needs palette first')
        return 0
    if needs_palette:
        raise SystemExit('refusing: fix palette is not applied, the stub room is still live code (apply patch_palette.py first)')
    bak = a.exe + '.chat.bak'
    shutil.copyfile(a.exe, bak)
    for name, va, old, new, note in sites:
        f = img.va2file(va)
        assert bytes(data[f:f + len(new)]) == old
        data[f:f + len(new)] = new
    open(a.exe, 'wb').write(data)
    print('written %s (%d sites); backup %s' % (a.exe, len(sites), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

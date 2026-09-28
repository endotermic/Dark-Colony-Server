#!/usr/bin/env python3
"""Main-menu opening order (fix `menuorder`, both games): logo, title, buttons, credits.

What the stock menu does (main.c `bintro`, Classic VA 0x00404E80.., Council Wars identical layout):
the credits TTY is created, the screen script is loaded, then the code runs the `banim` widget's
button wave as a BLOCKING loop (run_banims 0x00427AE4: plate after plate, each label revealed as
its plate settles), then shows every button, then starts the DC logo animation (`DCUK`, gadget 14,
12 frames) and, once the logo shows its frame 10, the title animation (`DCUT`, gadget 15, 4
frames) from the menu's own loop, which also scrolls the credits box from its first pass.  So the
player sees: buttons, logo, title, credits.

Maintainer instruction (28 Sep 2026): "first must be DC logo, second DARK COLONY logo, then
buttons, then credentials".  The new order is a cascade in the way modern title screens open -
brand mark, title, the interactive elements, the secondary text:

  1. the DC logo plays its one-shot (started at once, no wave before it);
  2. when it has finished, the title is prepared and started;
  3. when the title shows its second frame the button wave runs (plates top to bottom, labels as
     they settle, the plate sound per plate as before) - slight overlap with the title's tail;
  4. the first pass of the menu loop paints the credits box, as before.

Everything is rewritten IN PLACE inside `bintro`, three blocks, no byte elsewhere, no `.reloc`
entry in any of them (the code uses relative calls and register-relative operands only):

  A  VA 0x00404EF6, 41 bytes  (was: run wave, show buttons, start logo, dead CD flag read).
     Reads the `banim` widget (id 18, type 0x0C - the id every menu script has used since 1997)
     and STOPS its first plate: the script gives that plate `anim_oneoff`, which would otherwise
     make it fly in alone while the logo plays and, being finished by the time the wave runs,
     leave run_banims waiting for a frame that never comes (the wave chains plate k+1 on frame 2
     of plate k).  `start_anim(ip, plate, 2)` puts it back to frame 0 in state 2 = stopped (the state an
     `anim_stopped` plate is created in; 0 is `anim_loop`, 1 `anim_oneoff`) before the first
     interface pump - so the shipped scripts need no change.  A script without a banim at id 18
     is left alone (sentinel -1).  Ends with a short jump over the two bytes of fix `nocd`'s
     `jmp` (the former CD-check branch at 0x00404F1F, which must already be the `EB` form).
  B  VA 0x00404F21, 102 bytes (was: the six set_greyed calls of the CD-less path - dead code
     since fix `nocd`).  Starts the logo, pumps the interface until the logo's animation state is
     2 = finished, prepares and starts the title, pumps until the title's frame is >= 2, jumps to C.
  C  VA 0x00404FB0, 51 bytes  (was: the menu loop's "logo at frame 10 -> start the title" check,
     which the new order makes pointless).  First byte pair: `jmp` to the loop's TTY/credits
     update, so the loop's else path is unchanged in effect; then the one-time tail entered from
     B: start the first plate (the wave needs a running first plate), run the wave, show every
     button, jump to the loop head.

Callees (Classic; Council Wars +0x60), all located by their own bodies: start_anim 0x00425164
(ip, widget, mode: 0 loop, 1 one-shot, 2 stopped = the terminal state a finished one-shot reaches),
pump 0x00424294 (ip, &event), mode getter 0x004250CC (the mode: 2 = stopped or finished), frame getter
0x00425034, title_prepare 0x00424F80 (the stock
call before the title start), run_banims 0x00427AE4, show_buttons 0x00424770.

Requires fix `nocd` (the byte at 0x00404F1F must be `EB`; block B is live code without it).
`plan` works on the untouched original (the blocks' stock bytes are the same before and after
`nocd`), `apply` refuses without it.

CLI
    python patch_menu_order.py verify EXE
    python patch_menu_order.py plan   EXE
    python patch_menu_order.py apply  EXE        (writes EXE.menuorder.bak first)
"""

import argparse
import shutil
import struct
import sys

SIZE_OF = {659456: 'classic', 659968: 'cw'}
AUTO_SIZE_OF = {0x7E200: 'classic', 0x7E400: 'cw'}     # raw size of the code section, same in the `.dcicon` builds
IMAGE_BASE = 0x400000

BANIM_ID = 18            # `banim 18 ...` in every menu script (stock 1997 and every generated one)
LOGO_ID = 14             # gadget 14 = DCUK (the DC logo), gadget 15 = DCUT (the DARK COLONY title)
TITLE_ID = 15
TITLE_FRAME_WAVE = 2     # the wave starts when the title shows this frame (of 0..3)
WIDGET_BASE, WIDGET_SIZE = 0x88, 0x34          # ip->objects[i]: +1 type, +2 visible, +3 enabled, +0x28 object
TYPE_BANIM = 0x0C
A_LEN, B_LEN, C_LEN = 41, 102, 51
A_OFF_B = 43             # B = A + 41 (block) + 2 (the nocd jmp)
A_OFF_LOOP = 0x91        # loop head 0x00404F87 = A + 0x91
A_OFF_C = 0xBA           # C = 0x00404FB0 = A + 0xBA
C_OFF_ELSE = 51          # the loop's else path (TTY update) follows C directly

# main.c bintro, the code right before block A (load_interface .. the menu sound), unchanged by every fix
PRE = (b'\x31\xDB\xBA????\x8B\x45\xFC\xE8????\xBB\x01\x00\x00\x00\xBA\x0F\x00\x00\x00\x89\x45\xF0\xE8????'
       b'\x8B\x45\xFC\x31\xC9\x89\xC7\x31\xDB\x31\xD2\xFF\x57\x70\xBA\x01\x00\x00\x00\xB8\x86\x00\x00\x00\xFF\x57\x7C')
# stock A: mov eax,[ebp-10h]; mov ebx,1; call run_banims; mov eax,[ebp-10h]; mov edx,0Eh; call show_buttons;
#          mov eax,[ebp-10h]; call start_anim; call cd_flag; test al,al      (then jne/jmp +66h)
STOCK_A = (b'\x8B\x45\xF0\xBB\x01\x00\x00\x00\xE8????\x8B\x45\xF0\xBA\x0E\x00\x00\x00\xE8????'
           b'\x8B\x45\xF0\xE8????\xE8????\x84\xC0')
# stock B: six set_greyed(ip, id, 1) calls
STOCK_B = (b'\xBB\x01\x00\x00\x00\x8B\x45\xF0\x31\xD2\xE8????'
           b'\xBB\x01\x00\x00\x00\x8B\x45\xF0\x89\xDA\xE8????'
           b'\xBB\x01\x00\x00\x00\xBA\x10\x00\x00\x00\x8B\x45\xF0\xE8????'
           b'\xBB\x01\x00\x00\x00\xBA\x04\x00\x00\x00\x8B\x45\xF0\xE8????'
           b'\xBB\x01\x00\x00\x00\xBA\x02\x00\x00\x00\x8B\x45\xF0\xE8????'
           b'\xBB\x01\x00\x00\x00\xBA\x05\x00\x00\x00\x8B\x45\xF0\xE8????')
# loop head: lea edx,[ebp-0Ch]; mov eax,[ebp-10h]; call poll; cmp eax,1; jne +19h
LOOP_HEAD = b'\x8D\x55\xF4\x8B\x45\xF0\xE8????\x83\xF8\x01\x75\x19'
# stock C: mov edx,0Eh; mov eax,[ebp-10h]; call frame_get; cmp eax,0Ah; jne +21h; mov edx,0Fh; mov eax,[ebp-10h];
#          xor ebx,ebx; call title_prepare; mov ebx,1; mov edx,0Fh; mov eax,[ebp-10h]; call start_anim
STOCK_C = (b'\xBA\x0E\x00\x00\x00\x8B\x45\xF0\xE8????\x83\xF8\x0A\x75\x21'
           b'\xBA\x0F\x00\x00\x00\x8B\x45\xF0\x31\xDB\xE8????'
           b'\xBB\x01\x00\x00\x00\xBA\x0F\x00\x00\x00\x8B\x45\xF0\xE8????')
# after C: mov eax,[ebp-4]; xor ebx,ebx; xor edx,edx; call tty_update; jmp loop head
ELSE_PATH = b'\x8B\x45\xFC\x31\xDB\x31\xD2\xE8????\xEB\x96'

# function bodies (state-independent location of the callees)
RUN_BANIMS_BODY = (b'\x53\x51\x52\x55\x89\xE5\x89\xC3\x31\xC9\xEB\x08\x81\xF9\x2C\x01\x00\x00\x7D\x2D'
                   b'\x8D\x04\x8D\x00\x00\x00\x00\x29\xC8\xC1\xE0\x02\x8D\x14\x01\xC1\xE2\x02\x8D\x83\x88\x00\x00\x00'
                   b'\x01\xD0\x80\x78\x01\x0C\x75\x0A\x8B\x50\x28\x89\xD8\xE8????')
SHOW_BUTTONS_BODY = (b'\x53\x51\x52\x55\x89\xE5\x89\xC3\x31\xC9\xEB\x08\x81\xF9\x2C\x01\x00\x00\x7D\x33'
                     b'\x8D\x04\x8D\x00\x00\x00\x00\x29\xC8\xC1\xE0\x02\x8D\x14\x01\xC1\xE2\x02\x8D\x83\x88\x00\x00\x00'
                     b'\x01\xD0\x8A\x50\x01\x80\xFA\x02\x74\x05\x80\xFA\x03\x75\x09')
# inside the banim runtime (0x004279EC): lea edx,[ebp-0Ch]; mov eax,ecx; call pump
BANIM_PUMP = b'\x8D\x55\xF4\x89\xC8\xE8????'
# ... mov edx,[ebx+eax]; mov eax,ecx; call frame_get; cmp eax,2; jne +26h
BANIM_FRAME = b'\x8B\x14\x03\x89\xC8\xE8????\x83\xF8\x02\x75\x26'
# ... mov edx,[edx+ebx+4]; mov ebx,1; call start_anim
BANIM_START = b'\x8B\x54\x1A\x04\xBB\x01\x00\x00\x00\xE8????'
# ... mov edx,[ebx+eax]; mov eax,ecx; call mode_get; cmp eax,2; jne -75h
BANIM_MODE = b'\x8B\x14\x03\x89\xC8\xE8????\x83\xF8\x02\x75\x8B'
# title_prepare (0x00424F80): its widget.c assert names line 255 (0xFF); the sibling helpers use other lines
TITLE_PREPARE_BODY = (b'\x51\x56\x57\x55\x89\xE5\x83\xEC\x0C\x89\xC6\x89\xD7\x89\x5D\xF4\x8D\x04\x95\x00\x00\x00\x00'
                      b'\x29\xD0\xC1\xE0\x02\x01\xD0\xC1\xE0\x02\x8D\x96\x88\x00\x00\x00\x01\xC2\x8B\x42\x28\x89\x45\xF8'
                      b'\x80\x7A\x01\x0A\x74\x5C\x68????\x68\xFF\x00\x00\x00')


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


def find_all(data, pattern, start=0, end=None):
    hits = []
    end = len(data) if end is None else end
    first = pattern[0:1]
    i = data.find(first, start, end)
    while i >= 0:
        if i + len(pattern) <= end and all(p == 0x3F or data[i + k] == p for k, p in enumerate(pattern)):
            hits.append(i)
        i = data.find(first, i + 1, end)
    return hits


def unique(data, pattern, what, start=0, end=None):
    hits = find_all(data, pattern, start, end)
    if len(hits) != 1:
        raise SystemExit('expected exactly one %s, found %d: %s' % (what, len(hits), ', '.join('%#x' % h for h in hits)))
    return hits[0]


def matches(data, off, pattern):
    return all(p == 0x3F or data[off + k] == p for k, p in enumerate(pattern))


def rel32(from_va, to_va):
    return struct.pack('<i', to_va - (from_va + 5))


def rel8(from_va, to_va):
    d = to_va - (from_va + 2)
    assert -128 <= d <= 127, hex(d)
    return struct.pack('<b', d)


def reloc_entries(data):
    """Every HIGHLOW relocation target VA of the image."""
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    opt = pe + 24
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    st = opt + struct.unpack_from('<H', data, pe + 20)[0]
    secs = [struct.unpack_from('<IIII', data, st + i * 40 + 8) for i in range(nsec)]
    rva, size = struct.unpack_from('<II', data, opt + 96 + 5 * 8)

    def off(r):
        for vsize, srva, rsize, rptr in secs:
            if srva <= r < srva + rsize:
                return r - srva + rptr
        raise SystemExit('rva %#x outside every section' % r)
    p, end, out = off(rva), off(rva) + size, []
    while p + 8 <= end:
        page, blk = struct.unpack_from('<II', data, p)
        if blk < 8:
            break
        for q in range(p + 8, p + blk, 2):
            e = struct.unpack_from('<H', data, q)[0]
            if e >> 12 == 3:
                out.append(IMAGE_BASE + page + (e & 0xFFF))
        p += blk
    return out


def new_blocks(a_va, c, pump, mode_get, frame_get, start_anim, title_prepare, run_banims, show_buttons):
    """The three replacement blocks (A 41, B 102, C 51 bytes) for block A at `a_va`."""
    b_va, c_va = a_va + A_OFF_B, a_va + A_OFF_C
    loop_va, else_va = a_va + A_OFF_LOOP, c_va + C_OFF_ELSE
    w_type = WIDGET_BASE + WIDGET_SIZE * BANIM_ID + 1
    w_obj = WIDGET_BASE + WIDGET_SIZE * BANIM_ID + 0x28

    def block(va, length, items):
        b = bytearray()
        a = va
        labels = {}
        fixups = []
        for it in items:
            if isinstance(it, str):                 # label
                labels[it] = a
            else:
                kind, payload = it
                if kind == 'raw':
                    b.extend(payload); a += len(payload)
                elif kind == 'call':               # relative call to VA payload
                    b.extend(b'\xE8' + rel32(a, payload)); a += 5
                elif kind in ('jmp8', 'jne8', 'jl8', 'js8'):   # short jump to label/VA payload
                    op = {'jmp8': b'\xEB', 'jne8': b'\x75', 'jl8': b'\x7C', 'js8': b'\x78'}[kind]
                    fixups.append((len(b), a, kind, payload)); b.extend(op + b'\0'); a += 2
        for pos, at, kind, target in fixups:
            tva = labels[target] if isinstance(target, str) else target
            b[pos + 1] = rel8(at, tva)[0]
        assert len(b) <= length, (hex(va), len(b), length)
        b += b'\x90' * (length - len(b))
        return bytes(b)

    A = block(a_va, A_LEN, [
        ('raw', b'\x8B\x75\xF0'),                                   # mov  esi,[ebp-10h]        ip
        ('raw', b'\x83\xCF\xFF'),                                   # or   edi,-1               "no first plate"
        ('raw', b'\x80\xBE' + struct.pack('<I', w_type) + bytes([TYPE_BANIM])),   # cmp byte [esi+objects[18].type],banim
        ('jne8', 'done'),
        ('raw', b'\x8B\x86' + struct.pack('<I', w_obj)),            # mov  eax,[esi+objects[18].object]  banim record {n, m, plates*, buttons*}
        ('raw', b'\x8B\x40\x08'),                                   # mov  eax,[eax+8]          plates
        ('raw', b'\x8B\x38'),                                       # mov  edi,[eax]            first plate (the script's anim_oneoff one)
        ('raw', b'\x6A\x02\x5B'),                                   # push 2 ; pop ebx           mode 2 = stopped (as anim_stopped creates it); 0 would loop
        ('raw', b'\x89\xFA'),                                       # mov  edx,edi
        ('raw', b'\x89\xF0'),                                       # mov  eax,esi
        ('call', start_anim),                                       # start_anim(ip, plate, 2): frame 0, parked until the wave
        'done',
        ('jmp8', b_va),                                             # over fix nocd's `jmp +66h`
    ])
    B = block(b_va, B_LEN, [
        ('raw', b'\x6A\x01\x5B'),                                   # push 1 ; pop ebx           one-shot
        ('raw', b'\x6A' + bytes([LOGO_ID]) + b'\x5A'),              # push 14 ; pop edx
        ('raw', b'\x89\xF0'),                                       # mov  eax,esi
        ('call', start_anim),                                       # 1. the DC logo plays
        'L1',
        ('raw', b'\x8D\x55\xF4'),                                   # lea  edx,[ebp-0Ch]         event out
        ('raw', b'\x89\xF0'),
        ('call', pump),                                             # one interface pass (draws, steps the animations)
        ('raw', b'\x6A' + bytes([LOGO_ID]) + b'\x5A'),
        ('raw', b'\x89\xF0'),
        ('call', mode_get),                                         # logo animation state
        ('raw', b'\x83\xF8\x02'),                                   # cmp  eax,2                 finished?
        ('jne8', 'L1'),
        ('raw', b'\x31\xDB'),                                       # xor  ebx,ebx
        ('raw', b'\x6A' + bytes([TITLE_ID]) + b'\x5A'),
        ('raw', b'\x89\xF0'),
        ('call', title_prepare),                                    # 2. the title, prepared as the stock loop did ...
        ('raw', b'\x6A\x01\x5B'),
        ('raw', b'\x6A' + bytes([TITLE_ID]) + b'\x5A'),
        ('raw', b'\x89\xF0'),
        ('call', start_anim),                                       # ... and started
        'L2',
        ('raw', b'\x8D\x55\xF4'),
        ('raw', b'\x89\xF0'),
        ('call', pump),
        ('raw', b'\x6A' + bytes([TITLE_ID]) + b'\x5A'),
        ('raw', b'\x89\xF0'),
        ('call', frame_get),                                        # title frame
        ('raw', b'\x83\xF8' + bytes([TITLE_FRAME_WAVE])),           # cmp  eax,2
        ('jl8', 'L2'),
        ('jmp8', c_va + 2),                                         # -> the one-time tail in C
    ])
    C = block(c_va, C_LEN, [
        ('jmp8', else_va),                                          # the menu loop's else path: straight to the credits/TTY update
        ('raw', b'\x85\xFF'),                                       # test edi,edi               first plate known?
        ('js8', 'wave'),
        ('raw', b'\x6A\x01\x5B'),                                   # push 1 ; pop ebx
        ('raw', b'\x89\xFA'),                                       # mov  edx,edi
        ('raw', b'\x89\xF0'),
        ('call', start_anim),                                       # 3. the first plate starts ...
        'wave',
        ('raw', b'\x89\xF0'),
        ('call', run_banims),                                       # ... and the wave chains the rest, labels as the plates settle
        ('raw', b'\x89\xF0'),
        ('call', show_buttons),                                     # every button shown (as the stock code did after the wave)
        ('jmp8', loop_va),                                          # 4. the loop's first pass paints the credits box
    ])
    return A, B, C


def analyse(data):
    secs = sections(data)
    ava, arsize, arptr = secs['AUTO']
    aend = arptr + arsize
    game = AUTO_SIZE_OF.get(arsize)
    if game is None or (len(data) not in SIZE_OF and '.dcicon' not in secs):
        raise SystemExit('%d bytes, AUTO %#x: neither the Classic (659456) nor the Council Wars (659968) build' % (len(data), arsize))

    def va_of(off):
        return off - arptr + ava

    def off_of(va):
        return va - ava + arptr

    def call_target(off):
        return va_of(off) + 5 + struct.unpack_from('<i', data, off + 1)[0]

    pre = unique(data, PRE, 'main.c bintro load_interface sequence', arptr, aend)
    a_off = pre + len(PRE)
    a_va = va_of(a_off)
    b_off, c_off = a_off + A_OFF_B, a_off + A_OFF_C
    if not matches(data, a_off + A_OFF_LOOP, LOOP_HEAD):
        raise SystemExit('menu loop head not found at %#x' % va_of(a_off + A_OFF_LOOP))
    if not matches(data, c_off + C_OFF_ELSE, ELSE_PATH):
        raise SystemExit('menu loop else path not found at %#x' % va_of(c_off + C_OFF_ELSE))
    nocd_byte = data[a_off + A_LEN]
    if data[a_off + A_LEN + 1] != 0x66 or nocd_byte not in (0x75, 0xEB):
        raise SystemExit('the CD-check branch at %#x is neither `jne +66h` nor fix nocd\'s `jmp +66h`: %s'
                         % (va_of(a_off + A_LEN), data[a_off + A_LEN:a_off + A_LEN + 2].hex(' ')))
    nocd = nocd_byte == 0xEB

    # callees by their bodies
    run_banims = va_of(unique(data, RUN_BANIMS_BODY, 'run_banims body', arptr, aend))
    show_buttons = va_of(unique(data, SHOW_BUTTONS_BODY, 'show_buttons body', arptr, aend))
    title_prepare = va_of(unique(data, TITLE_PREPARE_BODY, 'title_prepare body', arptr, aend))
    rt_call = off_of(run_banims) + len(RUN_BANIMS_BODY) - 5
    banim_rt = call_target(rt_call)
    rt_off = off_of(banim_rt)
    rt_end = rt_off + 0x100
    pump = call_target(unique(data, BANIM_PUMP, 'banim runtime pump call', rt_off, rt_end) + 5)
    frame_get = call_target(unique(data, BANIM_FRAME, 'banim runtime frame getter', rt_off, rt_end) + 5)
    start_anim = call_target(unique(data, BANIM_START, 'banim runtime start_anim', rt_off, rt_end) + 9)
    mode_get = call_target(unique(data, BANIM_MODE, 'banim runtime mode getter', rt_off, rt_end) + 5)
    c = dict(pump=pump, mode_get=mode_get, frame_get=frame_get, start_anim=start_anim,
             title_prepare=title_prepare, run_banims=run_banims, show_buttons=show_buttons, banim_rt=banim_rt)
    A, B, C = new_blocks(a_va, c, pump, mode_get, frame_get, start_anim, title_prepare, run_banims, show_buttons)
    old_a, old_b, old_c = (bytes(data[a_off:a_off + A_LEN]), bytes(data[b_off:b_off + B_LEN]), bytes(data[c_off:c_off + C_LEN]))
    stock = matches(data, a_off, STOCK_A) and matches(data, b_off, STOCK_B) and matches(data, c_off, STOCK_C)
    if stock:
        # cross-check the stock call sites against the body-located callees
        checks = [(a_off + 8, run_banims), (a_off + 21, show_buttons), (a_off + 29, start_anim),
                  (c_off + 8, frame_get), (c_off + 28, title_prepare), (c_off + 46, start_anim)]
        for off, va in checks:
            if call_target(off) != va:
                raise SystemExit('stock call at %#x targets %#x, body search says %#x' % (va_of(off), call_target(off), va))
        state = 'stock'
    elif (old_a, old_b, old_c) == (A, B, C):
        state = 'patched'
    else:
        raise SystemExit('the three blocks at %#x / %#x / %#x are neither stock nor this fix\'s form' % (a_va, a_va + A_OFF_B, a_va + A_OFF_C))
    # nothing relocated inside the blocks (the tool writes relative calls and register-relative operands only)
    ranges = [(a_va, a_va + A_LEN), (a_va + A_OFF_B, a_va + A_OFF_B + B_LEN), (a_va + A_OFF_C, a_va + A_OFF_C + C_LEN)]
    inside = [r for r in reloc_entries(data) if any(lo <= r < hi for lo, hi in ranges)]
    if inside:
        raise SystemExit('.reloc entries inside the blocks: %s' % ', '.join('%#x' % r for r in inside))
    return dict(game=game, state=state, nocd=nocd, a_va=a_va, a_off=a_off, b_off=b_off, c_off=c_off,
                old=(old_a, old_b, old_c), new=(A, B, C), callees=c)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    r = analyse(data)
    c = r['callees']
    print('%s: %s build, %s, fix nocd %s; main.c bintro blocks at VA 0x%08X (%d) / 0x%08X (%d) / 0x%08X (%d); '
          'start_anim 0x%08X pump 0x%08X mode_get 0x%08X frame_get 0x%08X title_prepare 0x%08X run_banims 0x%08X (runtime 0x%08X) show_buttons 0x%08X'
          % (a.exe, 'Classic' if r['game'] == 'classic' else 'Council Wars', r['state'], 'applied' if r['nocd'] else 'NOT applied',
             r['a_va'], A_LEN, r['a_va'] + A_OFF_B, B_LEN, r['a_va'] + A_OFF_C, C_LEN,
             c['start_anim'], c['pump'], c['mode_get'], c['frame_get'], c['title_prepare'], c['run_banims'], c['banim_rt'], c['show_buttons']))
    if a.command == 'verify':
        return 0
    notes = [
        ('menu open: stop the first button plate, jump to the logo start',
         'the wave, the button show and the logo start move to the new blocks; the CD flag read (dead since fix nocd) goes; '
         'the banim widget (id %d) is read and its first plate - the script\'s anim_oneoff one - is parked at frame 0 in the stopped state with start_anim(ip, plate, 2) (animation modes: 0 loop, 1 one-shot, 2 stopped/finished), '
         'so it neither flies in alone during the logo nor is finished when the wave needs it; a script without a banim there is left alone (edi = -1); '
         'ends with a short jump over fix nocd\'s jmp at 0x%08X' % (BANIM_ID, r['a_va'] + A_LEN)),
        ('logo first, then the title: run in place of the dead CD-less greying calls',
         'start_anim(ip, %d, 1) plays the DC logo one-shot; the interface is pumped (0x%08X) until the logo\'s animation state is 2 = finished; '
         'then title_prepare(ip, %d, 0) + start_anim(ip, %d, 1) as the stock loop did at logo frame 10; pumped until the title shows frame %d; then jump to the tail in the third block'
         % (LOGO_ID, c['pump'], TITLE_ID, TITLE_ID, TITLE_FRAME_WAVE)),
        ('then the button wave, then the credits: the loop\'s frame-10 check becomes the one-time tail',
         'first two bytes: jmp to the loop\'s else path (the credits/TTY update) so the loop behaves as before; '
         'the tail entered from the second block starts the first plate (start_anim(ip, plate, 1), skipped when none was found), runs the wave (run_banims 0x%08X: plates one after another, each label revealed as its plate settles), '
         'shows every button (0x%08X) and jumps to the loop head 0x%08X, whose first pass paints the credits box'
         % (c['run_banims'], c['show_buttons'], r['a_va'] + A_OFF_LOOP)),
    ]
    for (name, note), off, old, new in zip(notes, (r['a_off'], r['b_off'], r['c_off']), r['old'], r['new']):
        print('  %s   VA 0x%x file 0x%x %d bytes: %s -> %s; %s' % (name, off - r['a_off'] + r['a_va'], off, len(old), old.hex(' '), new.hex(' '), note))
    if a.command == 'plan':
        return 0
    if r['state'] == 'patched':
        print('exe already patched, nothing to do')
        return 0
    if not r['nocd']:
        raise SystemExit('refusing to apply: fix nocd is not applied (the CD-less greying calls the second block replaces are live code) - apply patch_nocd.py first')
    bak = a.exe + '.menuorder.bak'
    shutil.copyfile(a.exe, bak)
    for off, new in zip((r['a_off'], r['b_off'], r['c_off']), r['new']):
        data[off:off + len(new)] = new
    open(a.exe, 'wb').write(data)
    print('written %s; backup %s' % (a.exe, bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

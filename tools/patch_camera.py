#!/usr/bin/env python3
"""Clamp the camera at battle start (fix `camera`, both games): no crash for start positions near the map edge.

Symptom (Fly log 19 Sep 2026, room 1, Plink - O): a player whose game player index gave the start
position (18,131) on a 160x140 map "hung" the moment the battlefield appeared and was evicted with
"no echo for frame 0" - the Windows Application log on the same PC shows an access violation of
dc16new.exe at module offset 0x45B52 at that second (WER dialog hidden behind the full-screen
surface, hence "hang"; the second player and the bots played on).

Cause: proto.c's game-start init (`0x0041ED11`ff) puts the camera on the local player's start tile,
`cam = start << 8` (`0x0041ED3A` / `0x0041ED48`), then computes the camera bounds
`[half, map - half]` (`0x0041EE66`..`0x0041EEA9`, half = 0xE00 x 0xB80 since fix `resolution`) -
but never applies them.  The per-frame render path clamps (`clamp2d 0x00436668`, called from
`0x0040AF16`) AFTER the client step, and the very first client step (`0x0040AAFC`) already hands
`camera - half` as the view rectangle to the ambience picker (`0x00432040` -> `0x00445AA4`, the
dominant-terrain-class count over the visible tiles).  That function checks only the rectangle's
origin against the map and then walks `origin .. origin + 28 x 23` through the row-pointer table
`map+0x804[z]`; rows past the map height hold NULL, so `test [edx],ecx` at `0x00445B52` faults.
With the stock 16x14 view (half 8/7) no shipped start position was close enough to the edge to
reach past the map; at 28x23 (half 14/11.5) every start row within 11 of the far edge crashes
(J8PLAY01 teams 0 and 1, D8PLAY01 team 3, D8PLAY03 teams 1 and 2, D8PLAY05 teams 0 and 1, ...).

Fix: right after the bounds are stored the init calls the ambience loader (`mov eax,ui;
mov edx,name; call 0x00432F80`).  That call is redirected to a 33-byte stub in the zero tail of
the code section which calls the game's own `clamp2d(min_x, min_z, max_x, max_z, &cam_x, &cam_z)`
with the bounds just written (`ui+0x114..0x120`) on the camera (`ui+0x108`, `ui+0x110`) and then
jumps to the original target - eax (ui) and edx are untouched, clamp2d preserves every register.
The stub uses only register-relative addressing, so no .reloc entry changes; nothing moves.
Classic stub at VA 0x0047F1DC (after the `cursor` stub), Council Wars at 0x0047F310 (after the
`ozi` stubs); both ranges are zero in the stock exes.  Council Wars offsets are found by pattern.
Single-player start-ups run through the same init and get the same clamp (harmless there).

CLI
    python patch_camera.py verify EXE
    python patch_camera.py plan   EXE
    python patch_camera.py apply  EXE        (writes EXE.camera.bak first)
"""

import argparse
import shutil
import struct
import sys

SIZE_OF = {659456: 'classic', 659968: 'cw'}
IMAGE_BASE = 0x400000
STUB_VA = {'classic': 0x47F1DC, 'cw': 0x47F310}
UI_BASE = 0x4AA9D0                                   # proto.c ui object; camera at +0x108/+0x110, bounds +0x114..0x120

# proto.c init, end of the bounds computation:
#   mov edx,[eax+9A4B8h] ; mov eax,[eax+9A4BCh] ; sub edx,ecx ; sub eax,ebx
#   mov [max_x],edx ; mov [max_z],eax ; mov eax,UI_BASE ; mov edx,imm32 ; call load_ambience
SITE_PATTERN = (b'\x29\xCA\x29\xD8'
                b'\x89\x15????' b'\xA3????'
                b'\xB8' + struct.pack('<I', UI_BASE) + b'\xBA????' b'\xE8????')
SITE_CALL_OFF = 4 + 6 + 5 + 5 + 5                    # offset of the E8 inside the pattern
# clamp2d: push ebx/ecx/edx/esi/edi/ebp ; mov ebp,esp ; mov ecx,[ebp+2Ch] ; mov edx,[ebp+30h] ; mov ebx,[ebp+1Ch] ; cmp ebx,[ecx]
CLAMP_PATTERN = b'\x53\x51\x52\x56\x57\x55\x89\xE5\x8B\x4D\x2C\x8B\x55\x30\x8B\x5D\x1C\x3B\x19\x7E\x02\x89\x19'
# the per-frame clamp call in the render path: push [eax+114h] ; call clamp2d
FRAME_CLAMP_PATTERN = b'\x8B\x90\x14\x01\x00\x00\x52\xE8'
# the camera init two dozen instructions earlier: mov ecx,view_h ; mov ebx,view_w (480x640 stock, 736x896 after
# fix `resolution`, hence wildcards) ; mov edx,[eax+0BD0h] (start x) ; lea esi,[ebp-1Ch] ; shl edx,8 ; mov eax,[eax+0BD4h]
CAMERA_INIT_PATTERN = b'\xB9????\xBB????\x8B\x90\xD0\x0B\x00\x00\x8D\x75\xE4\xC1\xE2\x08\x8B\x80\xD4\x0B\x00\x00'


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


def find_all(data, pattern):
    """File offsets of every match of a byte pattern with '?' wildcards."""
    hits = []
    first = pattern[0:1]
    i = data.find(first)
    while i >= 0:
        if i + len(pattern) <= len(data) and all(p == 0x3F or data[i + k] == p for k, p in enumerate(pattern)):
            hits.append(i)
        i = data.find(first, i + 1)
    return hits


def unique(data, pattern, what):
    hits = find_all(data, pattern)
    if len(hits) != 1:
        raise SystemExit('expected exactly one %s, found %d: %s' % (what, len(hits), ', '.join('%#x' % h for h in hits)))
    return hits[0]


def rel32(from_va, to_va):
    return struct.pack('<i', to_va - (from_va + 5))


def stub_bytes(stub_va, clamp_va, cont_va):
    """clamp2d(min_x, min_z, max_x, max_z, &cam_x, &cam_z) on the ui object in eax, then the original call."""
    b = bytearray()
    b += b'\x8D\xB0\x08\x01\x00\x00'   # lea  esi,[eax+108h]        esi = &cam_x
    b += b'\x8D\x4E\x08'               # lea  ecx,[esi+8]            ecx = &cam_z
    b += b'\x51'                       # push ecx                    &cam_z
    b += b'\x56'                       # push esi                    &cam_x
    b += b'\xFF\x76\x18'               # push dword ptr [esi+18h]    max_z  (ui+0x120)
    b += b'\xFF\x76\x14'               # push dword ptr [esi+14h]    max_x  (ui+0x11C)
    b += b'\xFF\x76\x10'               # push dword ptr [esi+10h]    min_z  (ui+0x118)
    b += b'\xFF\x76\x0C'               # push dword ptr [esi+0Ch]    min_x  (ui+0x114)
    b += b'\xE8' + rel32(stub_va + len(b), clamp_va)     # call clamp2d (ret 18h; preserves eax, edx)
    b += b'\xE9' + rel32(stub_va + len(b), cont_va)      # jmp  load_ambience (returns to the init)
    assert len(b) == 33
    return bytes(b)


def analyse(data):
    """Everything the three commands need, or SystemExit with a reason."""
    game = SIZE_OF.get(len(data))
    if game is None:
        raise SystemExit('%d bytes: neither the Classic (659456) nor the Council Wars (659968) build' % len(data))
    secs = sections(data)
    ava, arsize, arptr = secs['AUTO']

    def va_of(off):
        return off - arptr + ava

    def off_of(va):
        return va - ava + arptr

    site = unique(data, SITE_PATTERN, 'proto.c bounds+ambience-call site')
    call_off = site + SITE_CALL_OFF
    call_va = va_of(call_off)
    target_va = call_va + 5 + struct.unpack_from('<i', data, call_off + 1)[0]
    clamp_va = va_of(unique(data, CLAMP_PATTERN, 'clamp2d'))
    frame_off = unique(data, FRAME_CLAMP_PATTERN, 'per-frame clamp call')
    frame_call_va = va_of(frame_off + 7)
    frame_target = frame_call_va + 5 + struct.unpack_from('<i', data, frame_off + 8)[0]
    if frame_target != clamp_va:
        raise SystemExit('the render path calls %#x, clamp2d found at %#x' % (frame_target, clamp_va))
    cam_init_va = va_of(unique(data, CAMERA_INIT_PATTERN, 'camera init (start << 8)'))
    if not (site - 0x200 < off_of(cam_init_va) < site):
        raise SystemExit('camera init at %#x is not shortly before the bounds site %#x' % (cam_init_va, va_of(site)))

    stub_va = STUB_VA[game]
    stub_off = off_of(stub_va)
    if not (arptr <= stub_off and stub_off + 33 <= arptr + arsize):
        raise SystemExit('stub range %#x..%#x is outside the AUTO raw data' % (stub_va, stub_va + 33))
    cur_stub = bytes(data[stub_off:stub_off + 33])

    if target_va == stub_va:
        # already patched: the original target is the stub's final jmp
        cont_va = stub_va + 33 + struct.unpack_from('<i', data, stub_off + 29)[0]
        state = 'patched'
    else:
        cont_va = target_va
        state = 'stock'
    stub = stub_bytes(stub_va, clamp_va, cont_va)
    if state == 'patched' and cur_stub != stub:
        raise SystemExit('call at %#x already points at %#x but the stub there is not ours' % (call_va, stub_va))
    if state == 'stock' and cur_stub != bytes(33):
        raise SystemExit('stub range %#x..%#x is not zero: %s' % (stub_va, stub_va + 33, cur_stub.hex(' ')))
    if state == 'stock' and not (ava <= cont_va < ava + arsize):
        raise SystemExit('the call at %#x targets %#x, outside the code section' % (call_va, cont_va))
    return dict(game=game, state=state, call_va=call_va, call_off=call_off, cont_va=cont_va,
                clamp_va=clamp_va, cam_init_va=cam_init_va, stub_va=stub_va, stub_off=stub_off,
                old_call=bytes(data[call_off:call_off + 5]), new_call=b'\xE8' + rel32(call_va, stub_va),
                old_stub=cur_stub, new_stub=stub)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    r = analyse(data)
    print('%s: %s build, %s; camera init (start << 8) at VA 0x%08X, bounds+ambience call at VA 0x%08X -> 0x%08X, clamp2d 0x%08X, stub VA 0x%08X'
          % (a.exe, 'Classic' if r['game'] == 'classic' else 'Council Wars', r['state'], r['cam_init_va'], r['call_va'],
             r['cont_va'], r['clamp_va'], r['stub_va']))
    if a.command == 'verify':
        return 0
    print('  proto.c init: call load_ambience -> call stub   VA 0x%x file 0x%x 4 bytes: %s -> %s; rel32 operand of the E8 that follows the camera-bounds stores; the stub clamps the camera first and then continues into load_ambience 0x%08X'
          % (r['call_va'] + 1, r['call_off'] + 1, r['old_call'][1:].hex(' '), r['new_call'][1:].hex(' '), r['cont_va']))
    print('  stub clamp_camera in the AUTO zero tail          VA 0x%x file 0x%x 33 bytes: %s -> %s; lea esi,[eax+108h]; push &cam_z, &cam_x, max_z, max_x, min_z, min_x (ui+0x120..0x114); call clamp2d 0x%08X; jmp load_ambience 0x%08X - register-relative only, no .reloc entries'
          % (r['stub_va'], r['stub_off'], r['old_stub'].hex(' '), r['new_stub'].hex(' '), r['clamp_va'], r['cont_va']))
    if a.command == 'plan':
        return 0
    if r['state'] == 'stock':
        bak = a.exe + '.camera.bak'
        shutil.copyfile(a.exe, bak)
        data[r['call_off']:r['call_off'] + 5] = r['new_call']
        data[r['stub_off']:r['stub_off'] + 33] = r['new_stub']
        open(a.exe, 'wb').write(data)
        print('written %s; backup %s' % (a.exe, bak))
    else:
        print('exe already patched, nothing to do')
    return 0


if __name__ == '__main__':
    sys.exit(main())

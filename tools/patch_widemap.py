#!/usr/bin/env python3
"""A map view wider than the map (fix `widemap`, both games): centre the camera, clamp every map read.

Why (doc 10.32, 22 Sep 2026): the patched view is W-128 x H-32 pixels of whole 32-px tiles - 116
tiles across at 3840x1080, 76 at 2560x1440 - while the shipped maps are 64, 96, 108, 112, 128 or
160 tiles wide.  proto.c computes the camera bounds as [half, map - half] (`0x0041EE66`ff) and
`clamp2d 0x00436668` applies the minimum first, then the maximum, so once half > map/2 the camera
sits at `map - half` and the view starts at a negative tile.  Nothing downstream clips to the map:
the tile drawer `0x0045011C` reads the tile plane through one flat pointer
`map+4[0] + 4*(w*ty + tx)` and continues into the neighbouring rows for off-map columns (the far
side of the map appears one row down) and, at the first and last map row, into the smalloc header /
the next block - tile ids > 1415 there index past the tile table and the blitter dereferences a
wild image pointer; the lightmap pass of `draw_terrain` reads before/after the rows; the vision
scan's rect (`clip_view_to_map 0x00435E24`) is never intersected with the map; the ambience pick
`0x00445AA4` returns "no terrain" (silent) as soon as the rect origin is left of the map; a click on
the black margin stores an off-map world x as a 16-bit word in the spot order (tile 251 after the
wrap).  10.32 has the full table.

Six stages, code in the dead body of `cd_probe` (`0x00405EAD..0x00405F87` Classic, `0x00405E8D` CW:
the entry byte is `ret` since fix `nocd`, the 219 bytes behind it are never executed again; its 14
HIGHLOW `.reloc` entries are re-pointed to the new absolute operands, the spare ones become
type 0) or in place where the stock bytes leave room:

1. Bounds rule (73 bytes in the body).  The init's `call` after the bounds stores (redirected by
   fix `camera` to the clamp stub) goes to this block first: per axis, if max < min then
   min = max = (min + max) / 2 = map/2 (the two add up to the map size in world units).  Every
   later clamp - the per-frame `clamp2d`, the `camera` stub the block jumps on into - then pins
   that axis at the map centre; scrolling in it is a no-op; the tile snap in the render path keeps
   it tile-aligned (map widths are even).  Register ecx only; eax/edx (ui, string) untouched.
2. Tile drawer (51-byte column stub in the body + 6 in-place edits).  Per column the tile word is
   read at `[row] + 4*(clamp(tx+col, 0, w-1) - tx)` instead of `[row] + 4*col`: off-map columns
   repeat the edge tile, on-map columns are unchanged, the occlusion-mask column pointer keeps the
   real column.  Two new locals (frame `sub esp,38h` -> `48h`); the column-loop head calls the stub,
   which runs the two instructions it displaced; the three `lea r,[edi*4+0]` and the mask
   `add eax,ecx` become loads of the new locals.
3. Lightmap pass of `draw_terrain` (50 bytes in place, `0x00453A80`): the four edge-flag
   corrections that only handled the -1 / +n border cells become clamps of the map row to
   [0, h-1] and the column to [0, w-1] - identical at the edges, defined everywhere.
4. Vision/draw-list rect (34-byte stub in the body): `clip_view_to_map`'s `call make_rect` goes
   through a stub that clamps x0 to >= 0 and x1 to <= w afterwards (both callers, `0x004396D4` and
   `0x00409A00`, get the clipped rect).
5. Ambience pick (48 bytes in place, `0x00445ABD`): the origin guards become clamps - x0 >= 0 and
   the width shortened to w - x0 - so the pick counts the visible on-map tiles instead of returning
   "class 0"; the z guard keeps its stock meaning through the function's existing return path.
6. Spot order (49-byte stub in the body): the builder `0x0040968C` stores the world x/z as 16-bit
   words; x is clamped to [0, map_w*256-1] first (and its copy in esi, which the click marker
   uses), so a click on the black margin orders a move to the map edge instead of to tile 251.
   The builder's eax is the CLIENT object `cl`, whose game-state pointer is at `cl+0xC` (the pick
   `0x00409850` reads the map the same way, `mov eax,[eax+0Ch]; mov esi,[eax+46F4Ch]`); the first
   form of this stub (published 22 Sep 2026 for a few hours) read `[cl+0x46F4C]` as if eax were
   the game state and crashed with an access violation on the first battlefield click.

Rows are never off the map for the shipped maps (the tallest view, 44 rows at 5120x1440, is
shorter than the smallest map, 56 rows), so the row direction is left as it is in stages 2, 4 and
6 - the body has 219 bytes and the four blocks take 207.

Requires `nocd` (the body must be dead) and `camera` (the call the bounds block chains into).
Council Wars offsets are found by pattern (code +0x60, cd_probe -0x20, the `.bss` operands read
from the instructions).

CLI
    python patch_widemap.py verify EXE
    python patch_widemap.py plan   EXE
    python patch_widemap.py apply  EXE        (writes EXE.widemap.bak first)
"""

import argparse
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patch_camera                                   # STUB_VA: where fix `camera` puts its clamp stub

SIZE_OF = {659456: 'classic', 659968: 'cw'}
IMAGE_BASE = 0x400000
UI_BASE = 0x4AA9D0
BODY_LEN = 219                                        # 0x405EAD..0x405F87: up to the next function (init 0x405F88)
MAP_W, MAP_H = 0x9A4B0, 0x9A4B4                       # map object: width / height in tiles
MAP_WW = 0x9A4B8                                      # width in world units (tiles << 8)
GS_MAP = 0x46F4C                                      # game state: pointer to the map object

# the stock cd_probe body right after its entry byte: push ecx/edx/esi/ebp ; mov ebp,esp ; sub esp,100h ; push imm ; xor ah,ah ; push imm ; mov [flag],ah
BODY_PATTERN = b'\x51\x52\x56\x55\x89\xE5\x81\xEC\x00\x01\x00\x00\x68????\x30\xE4\x68????\x88\x25'
BODY_PATCHED_HEAD = b'\x8B\x0D????\x3B\x0D????\x7D\x14\x03\x0D'
# proto.c init, end of the bounds computation (see patch_camera.py)
SITE_PATTERN = (b'\x29\xCA\x29\xD8'
                b'\x89\x15????' b'\xA3????'
                b'\xB8' + struct.pack('<I', UI_BASE) + b'\xBA????' b'\xE8????')
SITE_CALL_OFF = 4 + 6 + 5 + 5 + 5
CAMERA_STUB_HEAD = b'\x8D\xB0\x08\x01\x00\x00\x8D\x4E\x08\x51\x56'      # lea esi,[eax+108h]; lea ecx,[esi+8]; push ecx; push esi
# tile drawer 0x0045011C: push ebx/ecx/esi/edi/ebp ; mov ebp,esp ; sub esp,38h ; mov esi,eax ; mov [ebp-24h],edx ; mov eax,[eax+8]
DRAWER_PATTERN = b'\x53\x51\x56\x57\x55\x89\xE5\x83\xEC\x38\x89\xC6\x89\x55\xDC\x8B\x40\x08\x89\x45\xD8'
DRAWER_PATTERN_PATCHED = DRAWER_PATTERN.replace(b'\x83\xEC\x38', b'\x83\xEC\x48')
# draw_terrain, lightmap pass: the edge-flag application 0x00453A80..0x00453AB1
LIGHTMAP_STOCK = bytes.fromhex('85f6 7406 83f9ff 7501 40 837d4200 7406 3b4d6a 7501 48 837d3e00 7408 83faff 7503 ff4572'
                               '837d4600 7408 3b556e 7503 ff4d72'.replace(' ', ''))
LIGHTMAP_NEW = bytes.fromhex(
    '8b7314'            # mov  esi,[ebx+14h]          map
    '85c0 7d02 31c0'    # row = max(row, 0)           (eax = view_ty + row)
    '8bbeb4a40900'      # mov  edi,[esi+9A4B4h]       h
    '4f'                # dec  edi
    '39f8 7e02 89f8'    # row = min(row, h-1)
    '8b7d72'            # mov  edi,[ebp+72h]          col = view_tx + col
    '85ff 7d02 31ff'    # col = max(col, 0)
    '8bb6b0a40900'      # mov  esi,[esi+9A4B0h]       w
    '4e'                # dec  esi
    '39f7 7e02 89f7'    # col = min(col, w-1)
    '897d72'            # mov  [ebp+72h],edi
    '909090'.replace(' ', ''))
# ambience pick 0x00445ABD..0x00445AEC: the two origin guards (x: return 0 through 0x445BEA; z: through an inline epilogue)
AMBIENCE_STOCK_PATTERN = (b'\x85\xFF\x7C\x0E\x8B\x96\x4C\x6F\x04\x00\x3B\xBA\xB0\xA4\x09\x00\x7E\x07\x31\xC0\xE9????'
                          b'\x85\xDB\x7C\x08\x3B\x9A\xB4\xA4\x09\x00\x7E\x0B\x31\xC0\x8D\x65\x7E\x5D\x5F\x5E\xC2\x04\x00')
# clip_view_to_map 0x00435E24: mov edx,[view.y] ; mov eax,[view.map] ; sar edx,5 ; mov eax,[eax+9A4B4h] ; sub eax,edx ; mov ecx,tiles_y ;
# lea edx,[eax-tiles_y] ; mov ebx,tiles_x ; mov eax,[view.x] ; lea esi,[ebp-14h] ; sar eax,5 ; mov edi,[ebp-4] ; call make_rect
CLIP_PATTERN = (b'\x8B\x15????\xA1????\xC1\xFA\x05\x8B\x80\xB4\xA4\x09\x00\x29\xD0\xB9????\x8D\x50?\xBB????'
                b'\xA1????\x8D\x75\xEC\xC1\xF8\x05\x8B\x7D\xFC\xE8????\x8D\x75\xEC\xA5\xA5\xA5\xA5')
CLIP_MAP_OFF = 7                                      # operand of `mov eax,[view.map]`
CLIP_CALL_OFF = 6 + 5 + 3 + 6 + 2 + 5 + 3 + 5 + 5 + 3 + 3 + 3
MAKE_RECT_HEAD = b'\x57\x55\x89\xE5\x83\xEC\x14\x89\x75\xFC\x89\xF7\x89\x45\xEC\x89\x55\xF0'
# spot order 0x0040968C: push ecx/esi/ebp ; mov ebp,esp ; mov ecx,eax ; mov esi,edx ; mov [eax+7B0h],1 ; mov word [eax+792h],bx ; mov word [eax+790h],dx
ORDER_PATTERN = (b'\x51\x56\x55\x89\xE5\x89\xC1\x89\xD6\xC7\x80\xB0\x07\x00\x00\x01\x00\x00\x00'
                 b'\x66\x89\x98\x92\x07\x00\x00\x66\x89\x90\x90\x07\x00\x00\x80\xB8\xA7\x46\x00\x00\x00')
ORDER_STORES_OFF = 19
ORDER_STORES = b'\x66\x89\x98\x92\x07\x00\x00\x66\x89\x90\x90\x07\x00\x00'

DRAWER_EDITS = [
    (0x07, b'\x83\xEC\x38', b'\x83\xEC\x48',
     'tile drawer: sub esp,38h -> 48h; two new locals [ebp-44h] = clamped column offset, [ebp-48h] = mask column pointer'),
    (0xB6, b'\x8A\x4D\xF4\x8B\x45\xF8', None,
     'tile drawer: column-loop head `mov cl,[ebp-0Ch]; mov eax,[ebp-8]` -> call column stub (which runs both); NOP'),
    (0xF0, b'\x8D\x0C\xBD\x00\x00\x00\x00', b'\x8B\x4D\xBC\x90\x90\x90\x90',
     'tile drawer: `lea ecx,[edi*4]` (tile word column) -> `mov ecx,[ebp-44h]` clamped offset'),
    (0x158, b'\x8B\x45\xC8\x8A\x5C\x11\x03\x01\xC8\xF6\xC3\x08', b'\x8B\x45\xB8\x8A\x5C\x11\x03\x90\x90\xF6\xC3\x08',
     'tile drawer: mask pointer `mov eax,[ebp-38h]; add eax,ecx` -> `mov eax,[ebp-48h]` (real column, computed by the stub)'),
    (0x177, b'\x8D\x14\xBD\x00\x00\x00\x00', b'\x8B\x55\xBC\x90\x90\x90\x90',
     'tile drawer: `lea edx,[edi*4]` (fg flag word) -> `mov edx,[ebp-44h]`'),
    (0x197, b'\x8D\x14\xBD\x00\x00\x00\x00', b'\x8B\x55\xBC\x90\x90\x90\x90',
     'tile drawer: `lea edx,[edi*4]` (fg tile word) -> `mov edx,[ebp-44h]`'),
]


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
    return out, pe + 24


def find_all(data, pattern):
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


def rel32(from_va, to_va, length=5):
    return struct.pack('<i', to_va - (from_va + length))


def reloc_entries(data, opt):
    """[(file offset of the 16-bit entry, page VA, type, target VA)] for the whole .reloc directory."""
    secs, _ = sections(data)
    rel_va, rel_rsize, rel_rptr = secs['.reloc']
    drva, dsz = struct.unpack_from('<II', data, opt + 96 + 5 * 8)
    off = rel_rptr + (IMAGE_BASE + drva - rel_va)
    end = off + dsz
    out = []
    while off + 8 <= end:
        page, size = struct.unpack_from('<II', data, off)
        if size == 0:
            break
        for i in range(8, size, 2):
            e = struct.unpack_from('<H', data, off + i)[0]
            out.append((off + i, IMAGE_BASE + page, e >> 12, IMAGE_BASE + page + (e & 0xFFF)))
        off += size
    return out


def bounds_block(block_va, min_x, min_z, max_x, max_z, camera_stub_va):
    """if max < min: min = max = (min + max) / 2, per axis; then jmp camera stub.  Returns (bytes, [operand offsets])."""
    b = bytearray()
    ops = []

    def axis(mn, mx):
        b.extend(b'\x8B\x0D'); ops.append(len(b)); b.extend(struct.pack('<I', mx))    # mov ecx,[max]
        b.extend(b'\x3B\x0D'); ops.append(len(b)); b.extend(struct.pack('<I', mn))    # cmp ecx,[min]
        b.extend(b'\x7D\x14')                                                          # jge +20 (skip the fix-up)
        b.extend(b'\x03\x0D'); ops.append(len(b)); b.extend(struct.pack('<I', mn))    # add ecx,[min]
        b.extend(b'\xD1\xF9')                                                          # sar ecx,1
        b.extend(b'\x89\x0D'); ops.append(len(b)); b.extend(struct.pack('<I', mn))    # mov [min],ecx
        b.extend(b'\x89\x0D'); ops.append(len(b)); b.extend(struct.pack('<I', mx))    # mov [max],ecx
    axis(min_x, max_x)
    axis(min_z, max_z)
    b.extend(b'\xE9' + rel32(block_va + len(b), camera_stub_va))                      # jmp camera stub (clamps, then load_ambience)
    assert len(b) == 73 and len(ops) == 10
    return bytes(b), ops


def column_stub():
    """Clamped tile column for the drawer; runs the two instructions it displaced, then returns."""
    b = (b'\x8B\x06'          # mov  eax,[esi]            view.x in pixels
         b'\xC1\xF8\x05'      # sar  eax,5                tx (negative when the view is wider than the map)
         b'\x8D\x14\x38'      # lea  edx,[eax+edi]        mx = tx + col
         b'\x85\xD2'          # test edx,edx
         b'\x7D\x02'          # jge  +2
         b'\x31\xD2'          # xor  edx,edx              mx = 0
         b'\x8B\x4E\x14'      # mov  ecx,[esi+14h]        map
         b'\x0F\xBF\x09'      # movsx ecx,word ptr [ecx]  w (tiles)
         b'\x49'              # dec  ecx                  w-1
         b'\x39\xCA'          # cmp  edx,ecx
         b'\x7E\x02'          # jle  +2
         b'\x89\xCA'          # mov  edx,ecx              mx = w-1
         b'\x29\xC2'          # sub  edx,eax              mx - tx
         b'\xC1\xE2\x02'      # shl  edx,2                *4
         b'\x89\x55\xBC'      # mov  [ebp-44h],edx        clamped column offset
         b'\x8B\x45\xC8'      # mov  eax,[ebp-38h]        mask row pointer
         b'\x8D\x04\xB8'      # lea  eax,[eax+edi*4]      real column
         b'\x89\x45\xB8'      # mov  [ebp-48h],eax
         b'\x8A\x4D\xF4'      # mov  cl,[ebp-0Ch]         displaced: zoom shift
         b'\x8B\x45\xF8'      # mov  eax,[ebp-8]          displaced: row
         b'\xC3')             # ret
    assert len(b) == 51
    return b


def vision_stub(stub_va, make_rect_va, view_map_va):
    """call make_rect, then x0 = max(x0, 0), x1 = min(x1, w) on the rect it filled (eax = &rect).  Returns (bytes, operand offset)."""
    b = bytearray()
    b += b'\xE8' + rel32(stub_va, make_rect_va)     # call make_rect            eax = &rect {x0, z0, x1, z1}
    b += b'\x8B\x15'; op = len(b); b += struct.pack('<I', view_map_va)   # mov edx,[view.map]
    b += b'\x83\x38\x00'                             # cmp  dword ptr [eax],0
    b += b'\x7D\x03'                                 # jge  +3
    b += b'\x83\x20\x00'                             # and  dword ptr [eax],0    x0 = 0
    b += b'\x8B\x8A' + struct.pack('<I', MAP_W)      # mov  ecx,[edx+9A4B0h]     w
    b += b'\x39\x48\x08'                             # cmp  [eax+8],ecx
    b += b'\x7E\x03'                                 # jle  +3
    b += b'\x89\x48\x08'                             # mov  [eax+8],ecx          x1 = w
    b += b'\xC3'                                     # ret
    assert len(b) == 34
    return bytes(b), op


def order_stub():
    """Clamp the world x of a spot order to the map, then run the two displaced stores."""
    b = (b'\x51'                                      # push ecx                  (ecx = cl copy, live in the caller)
         b'\x8B\x48\x0C'                              # mov  ecx,[eax+0Ch]        cl->gs: eax is the CLIENT object, its game-state pointer is at +0Ch
         b'\x8B\x89' + struct.pack('<I', GS_MAP) +    # mov  ecx,[ecx+46F4Ch]     map
         b'\x85\xD2'                                  # test edx,edx
         b'\x7D\x02'                                  # jge  +2
         b'\x31\xD2'                                  # xor  edx,edx              x = 0
         b'\x3B\x91' + struct.pack('<I', MAP_WW) +    # cmp  edx,[ecx+9A4B8h]     width in world units
         b'\x7C\x07'                                  # jl   +7
         b'\x8B\x91' + struct.pack('<I', MAP_WW) +    # mov  edx,[ecx+9A4B8h]
         b'\x4A'                                      # dec  edx                  x = width-1
         b'\x89\xD6'                                  # mov  esi,edx              the caller's copy (click marker)
         b'\x59'                                      # pop  ecx
         + ORDER_STORES +                             # mov word [eax+792h],bx ; mov word [eax+790h],dx   (displaced)
         b'\xC3')                                     # ret
    assert len(b) == 49
    return b


def ambience_block(block_va, ret0_va):
    """x0 = max(x0, 0); w = min(w, W - x0); z guard as stock (return 0 through the function's own path at ret0_va)."""
    b = bytearray()
    b += b'\x85\xFF\x7D\x02\x31\xFF'                 # test edi,edi ; jge +2 ; xor edi,edi        x0 = max(x0, 0)
    b += b'\x8B\x96' + struct.pack('<I', GS_MAP)     # mov  edx,[esi+46F4Ch]                     map
    b += b'\x8B\x8A' + struct.pack('<I', MAP_W)      # mov  ecx,[edx+9A4B0h]                     W
    b += b'\x29\xF9'                                 # sub  ecx,edi                              W - x0
    b += b'\x39\xC8\x7E\x02\x89\xC8'                 # cmp  eax,ecx ; jle +2 ; mov eax,ecx        w = min(w, W - x0)
    b += b'\x85\xDB\x7C\x08'                         # test ebx,ebx ; jl ret0                     z0 < 0 -> 0 (stock)
    b += b'\x3B\x9A' + struct.pack('<I', MAP_H)      # cmp  ebx,[edx+9A4B4h]
    b += b'\x7E\x0A'                                 # jle  continue (the stock code at +0x30)    z0 <= H (stock)
    b += b'\x31\xC0'                                 # ret0: xor eax,eax
    b += b'\xE9' + rel32(block_va + len(b), ret0_va)  # jmp the function's return-0 path
    b += b'\x90\x90\x90'
    assert len(b) == 48
    return bytes(b)


def analyse(data):
    game = SIZE_OF.get(len(data))
    if game is None:
        raise SystemExit('%d bytes: neither the Classic (659456) nor the Council Wars (659968) build' % len(data))
    secs, opt = sections(data)
    ava, arsize, arptr = secs['AUTO']

    def va_of(off):
        return off - arptr + ava

    def off_of(va):
        return va - ava + arptr

    # --- the home: cd_probe's dead body
    body_hits = find_all(data, BODY_PATTERN)
    if len(body_hits) == 1:
        body_off, state = body_hits[0], 'stock'
    else:
        body_hits = [h for h in find_all(data, BODY_PATCHED_HEAD) if data[h - 1] == 0xC3]
        if len(body_hits) != 1:
            raise SystemExit('cd_probe body not found in stock form (%d hits) nor in patched form' % len(body_hits))
        body_off, state = body_hits[0], 'patched'
    pending = []                                      # prerequisites missing on an untouched original (plan/verify only)
    if data[body_off - 1] != 0xC3:
        pending.append('nocd')
    body_va = va_of(body_off)

    def expect(stock, patched):
        return stock if state == 'stock' else patched

    edits = []      # (va, file offset, old bytes, new bytes, note)

    # --- 1. the bounds site and the camera stub
    site = unique(data, SITE_PATTERN, 'proto.c bounds+ambience-call site')
    max_x = struct.unpack_from('<I', data, site + 6)[0]
    max_z = struct.unpack_from('<I', data, site + 11)[0]
    min_x, min_z = max_x - 8, max_z - 8
    call_off = site + SITE_CALL_OFF
    call_va = va_of(call_off)
    target_va = call_va + 5 + struct.unpack_from('<i', data, call_off + 1)[0]
    if state == 'patched':
        if target_va != body_va:
            raise SystemExit('body patched but the bounds call at %#x targets %#x, not the body' % (call_va, target_va))
        camera_stub_va = body_va + 73 + struct.unpack_from('<i', data, body_off + 69)[0]
    else:
        camera_stub_va = target_va
    if data[off_of(camera_stub_va):off_of(camera_stub_va) + len(CAMERA_STUB_HEAD)] != CAMERA_STUB_HEAD:
        # the untouched original: the call still goes to load_ambience; fix `camera` redirects it to its stub first
        stub_va = patch_camera.STUB_VA[game]
        if data[off_of(stub_va):off_of(stub_va) + 33] != bytes(33):
            raise SystemExit('the bounds call at %#x does not lead to the `camera` clamp stub (target %#x) and the stub range %#x is not free'
                             % (call_va, camera_stub_va, stub_va))
        camera_stub_va = stub_va
        pending.append('camera')
    # the old bytes are those after fix `camera` (the generator checks them against its replay), not the original's
    edits.append((call_va, call_off, b'\xE8' + rel32(call_va, camera_stub_va), b'\xE8' + rel32(call_va, body_va),
                  'proto.c init: call camera stub -> call bounds rule (which chains into the camera stub)'))

    # --- body layout
    block, bounds_ops = bounds_block(body_va, min_x, min_z, max_x, max_z, camera_stub_va)
    col = column_stub()
    col_va = body_va + len(block)
    vision_va = col_va + len(col)
    order_va = vision_va + 34

    # --- 2. the tile drawer
    drawer_off = unique(data, expect(DRAWER_PATTERN, DRAWER_PATTERN_PATCHED), 'tile drawer 0x0045011C')
    drawer_va = va_of(drawer_off)
    for rel, stock, patched, note in DRAWER_EDITS:
        if patched is None:
            patched = b'\xE8' + rel32(drawer_va + rel, col_va) + b'\x90'
        edits.append((drawer_va + rel, drawer_off + rel, stock, patched, note))

    # --- 3. the lightmap pass
    lm_off = unique(data, expect(LIGHTMAP_STOCK, LIGHTMAP_NEW), 'draw_terrain edge-flag block 0x00453A80')
    edits.append((va_of(lm_off), lm_off, LIGHTMAP_STOCK, LIGHTMAP_NEW,
                  'draw_terrain lightmap pass: the four edge-flag corrections -> clamp the map row to [0,h-1] and the column to [0,w-1] (esi/edi scratch, eax = row, [ebp+72h] = column)'))

    # --- 4. the vision rect
    clip_off = unique(data, CLIP_PATTERN, 'clip_view_to_map 0x00435E24 rect build')
    view_map_va = struct.unpack_from('<I', data, clip_off + CLIP_MAP_OFF)[0]
    clip_call_off = clip_off + CLIP_CALL_OFF
    clip_call_va = va_of(clip_call_off)
    clip_target = clip_call_va + 5 + struct.unpack_from('<i', data, clip_call_off + 1)[0]
    if state == 'patched':
        if clip_target != vision_va:
            raise SystemExit('clip_view_to_map calls %#x, expected the vision stub %#x' % (clip_target, vision_va))
        make_rect_va = vision_va + 5 + struct.unpack_from('<i', data, off_of(vision_va) + 1)[0]
    else:
        make_rect_va = clip_target
    if data[off_of(make_rect_va):off_of(make_rect_va) + len(MAKE_RECT_HEAD)] != MAKE_RECT_HEAD:
        raise SystemExit('clip_view_to_map does not call make_rect (target %#x)' % make_rect_va)
    vis, vis_op = vision_stub(vision_va, make_rect_va, view_map_va)
    edits.append((clip_call_va, clip_call_off, b'\xE8' + rel32(clip_call_va, make_rect_va), b'\xE8' + rel32(clip_call_va, vision_va),
                  'clip_view_to_map: call make_rect -> call vision stub (make_rect, then x0 = max(x0,0), x1 = min(x1,w))'))

    # --- 5. the ambience pick
    amb_hits = find_all(data, AMBIENCE_STOCK_PATTERN)
    if state == 'stock':
        if len(amb_hits) != 1:
            raise SystemExit('expected one ambience guard block, found %d' % len(amb_hits))
        amb_off = amb_hits[0]
        amb_va = va_of(amb_off)
        ret0_va = amb_va + 0x19 + struct.unpack_from('<i', data, amb_off + 0x15)[0]
        amb_stock = bytes(data[amb_off:amb_off + 48])
    else:
        # patched form: our block; ret0 from our own jmp at +0x28
        cands = [h for h in find_all(data, b'\x85\xFF\x7D\x02\x31\xFF\x8B\x96' + struct.pack('<I', GS_MAP) + b'\x8B\x8A' + struct.pack('<I', MAP_W) + b'\x29\xF9')]
        if len(cands) != 1:
            raise SystemExit('patched ambience block not found (%d hits)' % len(cands))
        amb_off = cands[0]
        amb_va = va_of(amb_off)
        ret0_va = amb_va + 0x2D + struct.unpack_from('<i', data, amb_off + 0x29)[0]
        amb_stock = AMBIENCE_STOCK_PATTERN[:0x15] + rel32(amb_va + 0x14, ret0_va) + AMBIENCE_STOCK_PATTERN[0x19:]
    amb_new = ambience_block(amb_va, ret0_va)
    edits.append((amb_va, amb_off, amb_stock, amb_new,
                  'ambience pick: origin guards -> x0 = max(x0,0), w = min(w, W-x0); the z guard keeps its stock meaning (return 0 via 0x%08X)' % ret0_va))

    # --- 6. the spot order
    order_off = unique(data, ORDER_PATTERN if state == 'stock' else ORDER_PATTERN[:ORDER_STORES_OFF] + b'\xE8????' + b'\x90' * 9 + ORDER_PATTERN[ORDER_STORES_OFF + 14:],
                       'spot order builder 0x0040968C')
    stores_va = va_of(order_off + ORDER_STORES_OFF)
    edits.append((stores_va, order_off + ORDER_STORES_OFF, ORDER_STORES, b'\xE8' + rel32(stores_va, order_va) + b'\x90' * 9,
                  'spot order: the two 16-bit stores of the world point -> call order stub (clamps x to [0, map_w*256-1], updates esi, then stores)'))

    new_body = block + col + vis + order_stub()
    assert len(new_body) == 207 <= BODY_LEN
    old_body = bytes(data[body_off:body_off + BODY_LEN])
    if state == 'patched' and old_body[:len(new_body)] != new_body:
        raise SystemExit('the cd_probe body at %#x carries code that is not ours' % body_va)

    # --- consistency of every edit with the state
    for va, off, old, new, note in edits:
        cur = bytes(data[off:off + len(old)])
        if 'camera' in pending and off == call_off:
            continue                                  # still the original call; fix camera rewrites it first
        if cur != expect(old, new):
            raise SystemExit('bytes at %#x are %s, expected %s (%s)' % (va, cur.hex(' '), expect(old, new).hex(' '), note.split(':')[0]))
        for eoff, page, typ, tgt in reloc_entries(data, opt):
            if typ == 3 and va <= tgt < va + len(old) and not (body_va <= va < body_va + BODY_LEN):
                raise SystemExit('a .reloc entry points into the edit at %#x' % va)

    # --- .reloc: the body's own entries are re-pointed to the new absolute operands, spare ones neutralised
    operand_vas = [body_va + o for o in bounds_ops] + [vision_va + vis_op]
    highlow = [r for r in reloc_entries(data, opt) if body_va <= r[3] < body_va + BODY_LEN and r[2] == 3]
    if state == 'stock' and len(highlow) < len(operand_vas):
        raise SystemExit('only %d HIGHLOW .reloc entries in the cd_probe body, %d needed' % (len(highlow), len(operand_vas)))
    if state == 'patched':
        if sorted(r[3] for r in highlow) != sorted(operand_vas):
            raise SystemExit('the .reloc entries of the body do not match our operands')
        highlow += [r for r in reloc_entries(data, opt) if r[2] == 0 and r[1] == (body_va & ~0xFFF)]
    reloc_edits = []
    for i, (eoff, page, typ, tgt) in enumerate(highlow):
        old = bytes(data[eoff:eoff + 2])
        new = struct.pack('<H', (3 << 12) | (operand_vas[i] - page)) if i < len(operand_vas) else b'\x00\x00'
        reloc_edits.append((eoff, old, new, tgt, operand_vas[i] if i < len(operand_vas) else None))

    return dict(game=game, state=state, pending=pending, body_va=body_va, body_off=body_off, old_body=old_body, new_body=new_body,
                camera_stub_va=camera_stub_va, min_x=min_x, min_z=min_z, max_x=max_x, max_z=max_z,
                drawer_va=drawer_va, col_va=col_va, vision_va=vision_va, order_va=order_va, make_rect_va=make_rect_va,
                edits=edits, reloc_edits=reloc_edits)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    r = analyse(data)
    print('%s: %s build, %s%s; cd_probe body VA 0x%08X (%d of %d bytes), camera stub 0x%08X, bounds min/max x 0x%X/0x%X z 0x%X/0x%X, tile drawer 0x%08X, make_rect 0x%08X'
          % (a.exe, 'Classic' if r['game'] == 'classic' else 'Council Wars', r['state'],
             ' (untouched original: fixes %s still to be applied first)' % ', '.join(r['pending']) if r['pending'] else '',
             r['body_va'], len(r['new_body']), BODY_LEN,
             r['camera_stub_va'], r['min_x'], r['max_x'], r['min_z'], r['max_z'], r['drawer_va'], r['make_rect_va']))
    if a.command == 'verify':
        return 0
    n = len(r['new_body'])
    print('  cd_probe dead body: bounds rule + column stub + vision stub + order stub   VA 0x%x file 0x%x %d bytes: %s -> %s; bounds rule at +0 (per axis `if max < min: min = max = (min+max)/2`, 10 absolute operands, jmp camera stub 0x%08X), column stub at +73, vision stub at +124 (call make_rect 0x%08X, 1 absolute operand), order stub at +158'
          % (r['body_va'], r['body_off'], n, r['old_body'][:n].hex(' '), r['new_body'].hex(' '), r['camera_stub_va'], r['make_rect_va']))
    for va, off, old, new, note in r['edits']:
        head, _, tail = note.partition(': ')
        print('  %-52s VA 0x%x file 0x%x %d bytes: %s -> %s; %s' % (head, va, off, len(old), old.hex(' '), new.hex(' '), tail))
    for eoff, old, new, tgt, opv in r['reloc_edits']:
        print('  .reloc @ file 0x%x: %04X -> %04X   %s'
              % (eoff, struct.unpack('<H', old)[0], struct.unpack('<H', new)[0],
                 ('HIGHLOW entry of the dead cd_probe operand at 0x%08X re-pointed to the new operand at 0x%08X' % (tgt, opv)) if opv
                 else 'HIGHLOW entry of the dead cd_probe operand at 0x%08X -> type 0 ABSOLUTE padding' % tgt))
    if a.command == 'plan':
        return 0
    if r['pending']:
        raise SystemExit('apply refused: fixes %s must be applied first' % ', '.join(r['pending']))
    if r['state'] == 'stock':
        bak = a.exe + '.widemap.bak'
        shutil.copyfile(a.exe, bak)
        data[r['body_off']:r['body_off'] + n] = r['new_body']
        for va, off, old, new, note in r['edits']:
            data[off:off + len(new)] = new
        for eoff, old, new, tgt, opv in r['reloc_edits']:
            data[eoff:eoff + 2] = new
        open(a.exe, 'wb').write(data)
        print('written %s; backup %s' % (a.exe, bak))
    else:
        print('exe already patched, nothing to do')
    return 0


if __name__ == '__main__':
    sys.exit(main())

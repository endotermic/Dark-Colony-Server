#!/usr/bin/env python3
r"""Frame limiter: the main loop never runs faster than 60 frames per second (dc16.exe / DCEXP16.EXE, fix `fps`).

Why (3 Oct 2026, maintainer: "in linux+wine speed of cursor animation and map scroll on the battlefield are
ridiculously fast"; DC16_DISPLAY_AND_RESOLUTION.md section 10.69): the game has no frame limiter.  The only
thing that paces its main loop is the DirectDraw `Flip(NULL, flags 0)` at the end of `present`
(`ddex4.c`, Classic `0x0042E0FC`, Council Wars `0x0042E15C`), retried in a busy loop on every error but
SURFACELOST.  On Windows that flip completes at the monitor's vertical blank, so the loop runs at the
refresh rate (60 frames per second on most monitors), and the 1997 code counts on that: the battlefield
scrolls one whole tile per frame per direction once the pointer has dwelt 100 ms at an edge
(`0x0040AE6A..0x0040AEB6`) and the cursor animation is advanced once per frame by the client display
(`call 0x00422C7C` at `0x0040B309`).  Under Wine the flip returns at once (Xvfb, a Wine virtual desktop
and gamescope have no vertical blank to wait for; wined3d asks for swap interval 1 but nothing honours
it there) and the loop was measured at 350-380 frames per second: the map crosses in a quarter of a
second, the cursor flickers.  The same happens on any Windows monitor above 60 Hz, 2.4 times too fast
at 144 Hz.  The game ticks themselves are clock-driven (66 ms, `timeGetTime`), so the simulation, the
network echo and the menus - whose widget pump steps its animations only every 16 ms - were never
affected; only the battlefield's per-frame effects were.

How: `present`'s epilogue (`lea esp,[ebp+82h]` at `0x0042E2A1` / CW `0x0042E301`, reached after the
flip, from the error paths and from fix `restore`'s minimised idle stub) jumps to a stub that holds
the frame to at least 16 ms since the previous one:

    lea esp,[ebp+82h] ; push eax            the displaced instruction; present()'s return value kept
    call $+5 ; pop esi                       esi = own address (position-independent, no .reloc entry)
    now = timeGetTime(); if now - last >= 16: store
    hmod = GetModuleHandleA("winmm.dll"); if hmod: f = GetProcAddress(hmod, "timeBeginPeriod"); if f: f(1)
    do { Sleep(1); now = timeGetTime() } while now - last < 16
    if hmod: f = GetProcAddress(hmod, "timeEndPeriod"); if f: f(1)
    store: last = now ; pop eax ; jmp epilogue+6

`timeBeginPeriod(1)` around the wait matters on Windows: a process that never raised the timer
resolution gets ~15.6 ms from `Sleep(1)` (measured on Windows 11), which would turn a 144 Hz monitor
into 43 frames per second; raised only around the wait, fix `restore`'s `Sleep(1)` while minimised
keeps its one-tick length.  On a 60 Hz Windows monitor the flip has already taken 16-17 ms, so the
stub never waits and nothing changes.  Under Wine `Sleep(1)` is 1 ms and the loop settles at 60-62
frames per second.  The two winmm functions are not in the import table and are looked up through
`GetModuleHandleA` / `GetProcAddress` (imported) only when a wait is actually needed.

Where: the stub (132 bytes of code + three strings) lives in the three dead assert bodies of `remap`
(`ddex4.c` lines 1029/1033/1036, Classic `0x0042F399..0x0042F3F4` all 92 bytes, `0x0042F3FA..0x0042F423`
the first 42 of the second body, `0x0042F443..0x0042F46A` the first 40 of the third; CW +0x60), dead since
fix `ddraw` turned their three `push fmt` into jumps past them - nothing else jumps into them; the code
steps over the live 5-byte jump between bodies 1 and 2 with `jmp +5`.  The tails of bodies 2 and 3
(26 + 27 bytes) are left untouched; fix `pointer` (the battlefield cursor gate) uses them.
The timestamp dword lives at `0x00481FF0`, in the zero-filled page slack of the writable `.idata`
section (raw size 0x1200, mapped to 0x2000; nothing in either exe references the slack).  The 17
HIGHLOW `.reloc` entries that described absolute operands of the dead bodies become type 0 padding
(page offset kept); the new code has no absolute operand.  Calls go through the linker's import
thunks (`jmp [IAT]` at `0x0047EF00`ff), so the bytes are the same in both builds except for the
displacement of the timestamp (the code is at +0x60 in Council Wars, the `.idata` slack is not).

Requires fix `ddraw`; `plan` works on the untouched exe (the dead bodies hold the same bytes before
and after `ddraw`), `apply` refuses without it.  Confirmed: Wine rig 60-62 frames per second instead
of 350-380; Windows 60 Hz unchanged.

CLI
    python patch_fps.py verify EXE
    python patch_fps.py plan   EXE
    python patch_fps.py apply  EXE          (writes EXE.fps.bak first)
"""

import argparse
import re
import shutil
import struct
import sys

IMAGE_BASE = 0x400000
FRAME_MS = 16                                   # minimum frame period (60 frames per second)

# Classic VAs; Council Wars = +0x60 for everything in AUTO
HOOK_VA = 0x42E2A1                              # present(): lea esp,[ebp+82h] (6 bytes) -> jmp stub ; nop
BODY1_VA, BODY1_LEN = 0x42F399, 92              # remap: dead body behind ddraw's first jump  (Unlock assert)
BODY2_VA, BODY2_LEN = 0x42F3FA, 42              # dead body behind the second jump (Lock assert): its first 42 of 68 bytes
BODY3_VA, BODY3_LEN = 0x42F443, 40              # dead body behind the third jump (GetDC assert): its first 40 of 67 bytes
# the tails of bodies 2 and 3 (0x42F424..0x42F43D, 0x42F46B..0x42F485) stay as they are - fix `pointer` uses them
THUNKS = {'timeGetTime': 0x47F116, 'Sleep': 0x47F008, 'GetModuleHandleA': 0x47EF2A, 'GetProcAddress': 0x47F06E}
LAST_VA = 0x481FF0                              # the timestamp: .idata page slack, same VA in both builds

# the flip's SURFACELOST retry right before the epilogue (`je +10h` - the loading screen's copy of the same loop says `je +19h`)
HOOK_CONTEXT = '85 C0 74 10 3D C2 01 76 88 75 E6 E8 ?? ?? ?? ?? 85 C0 74 DD'
HOOK_STOCK = bytes.fromhex('8D A5 82 00 00 00')

# the stock bytes of the three dead bodies (Classic; CW differs in the rel32 operands of the calls and in the
# DGROUP addresses of the three strings, +8 there - both matched with wildcards)
BODY1_STOCK = ('8B 0D B4 49 4A 00 51 E8 ?? ?? ?? ?? 83 C4 08 68 ?? ?? ?? ?? 68 05 04 00 00 68 ?? ?? ?? ?? 68 ?? ?? ?? ?? '
               '8B 1D B4 49 4A 00 53 BA ?? ?? ?? ?? B9 05 04 00 00 E8 ?? ?? ?? ?? 83 C4 14 A1 B4 49 4A 00 BB ?? ?? ?? ?? '
               'E8 ?? ?? ?? ?? E8 ?? ?? ?? ?? 31 C0 E8 ?? ?? ?? ?? E9 91 00 00 00')
BODY2_STOCK = ('68 09 04 00 00 68 ?? ?? ?? ?? 68 ?? ?? ?? ?? A1 B4 49 4A 00 50 B9 09 04 00 00 BB ?? ?? ?? ?? '
               'E8 ?? ?? ?? ?? 83 C4 14 A1 B4 49 4A 00')
BODY3_STOCK = ('68 0C 04 00 00 68 ?? ?? ?? ?? 68 ?? ?? ?? ?? 8B 15 B4 49 4A 00 52 B9 0C 04 00 00 BB ?? ?? ?? ?? '
               'E8 ?? ?? ?? ?? 83 C4 14')
DDRAW_JMPS = {BODY1_VA - 5: 'E9 ED 00 00 00', BODY2_VA - 5: 'E9 8C 00 00 00', BODY3_VA - 5: 'E9 43 00 00 00'}

STRINGS = b'winmm.dll\0' + b'timeBeginPeriod\0' + b'timeEndPeriod\0'      # 10 + 16 + 14 = 40 bytes in body 3
STR_WINMM, STR_BEGIN, STR_END = 0, 10, 26


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

    def matches(self, va, hexpat):
        f = self.va2file(va)
        toks = hexpat.split()
        cur = self.data[f:f + len(toks)]
        return len(cur) == len(toks) and all(t == '??' or int(t, 16) == b for t, b in zip(toks, cur))


def rel32(src, dst):
    """rel32 operand of a 5-byte call/jmp at `src` reaching `dst`."""
    return struct.pack('<i', dst - (src + 5))


def rel8(src, dst, size=2):
    d = dst - (src + size)
    assert -128 <= d <= 127, (hex(src), hex(dst), d)
    return struct.pack('<b', d)


def disp32(base, target):
    return struct.pack('<i', target - base)


def build_code(delta):
    """(body1, body2, body3) bytes for the build at `delta` (0 Classic, 0x60 Council Wars)."""
    b1, b2, b3 = BODY1_VA + delta, BODY2_VA + delta, BODY3_VA + delta
    th = {k: v + delta for k, v in THUNKS.items()}
    pic = b1 + 12                                   # esi after `call $+5 ; pop esi`
    s_winmm, s_begin, s_end = b3 + STR_WINMM, b3 + STR_BEGIN, b3 + STR_END
    wait = b1 + 70
    store = b2 + 30
    c = bytearray()
    # ---- body 1 -------------------------------------------------------------------------------
    c += bytes.fromhex('8D A5 82 00 00 00')                       # +0  lea esp,[ebp+82h]        (displaced)
    c += b'\x50'                                                  # +6  push eax
    c += b'\xE8\x00\x00\x00\x00'                                  # +7  call $+5
    c += b'\x5E'                                                  # +12 pop esi
    c += b'\xE8' + rel32(b1 + 13, th['timeGetTime'])              # +13 call timeGetTime
    c += b'\x89\xC2'                                              # +18 mov edx,eax
    c += b'\x2B\x86' + disp32(pic, LAST_VA)                       # +20 sub eax,[esi+last]
    c += b'\x83\xF8' + bytes([FRAME_MS])                          # +26 cmp eax,16
    c += b'\x73' + rel8(b1 + 29, store)                           # +29 jae store
    c += b'\x8D\x86' + disp32(pic, s_winmm)                       # +31 lea eax,[esi+"winmm.dll"]
    c += b'\x50'                                                  # +37 push eax
    c += b'\xE8' + rel32(b1 + 38, th['GetModuleHandleA'])         # +38 call GetModuleHandleA
    c += b'\x89\xC7'                                              # +43 mov edi,eax
    c += b'\x85\xC0'                                              # +45 test eax,eax
    c += b'\x74' + rel8(b1 + 47, wait)                            # +47 jz wait
    c += b'\x8D\x86' + disp32(pic, s_begin)                       # +49 lea eax,[esi+"timeBeginPeriod"]
    c += b'\x50\x57'                                              # +55 push eax ; push edi
    c += b'\xE8' + rel32(b1 + 57, th['GetProcAddress'])           # +57 call GetProcAddress
    c += b'\x85\xC0'                                              # +62 test eax,eax
    c += b'\x74' + rel8(b1 + 64, wait)                            # +64 jz wait
    c += b'\x6A\x01\xFF\xD0'                                      # +66 push 1 ; call eax         timeBeginPeriod(1)
    assert len(c) == 70
    c += b'\x6A\x01'                                              # +70 wait: push 1
    c += b'\xE8' + rel32(b1 + 72, th['Sleep'])                    # +72 call Sleep
    c += b'\xE8' + rel32(b1 + 77, th['timeGetTime'])              # +77 call timeGetTime
    c += b'\x89\xC2'                                              # +82 mov edx,eax
    c += b'\x2B\x86' + disp32(pic, LAST_VA)                       # +84 sub eax,[esi+last]
    c += b'\xEB\x05'                                              # +90 jmp body2 (over the live ddraw jump)
    assert len(c) == BODY1_LEN
    body1 = bytes(c)
    # ---- body 2 -------------------------------------------------------------------------------
    c = bytearray()
    c += b'\x83\xF8' + bytes([FRAME_MS])                          # +0  cmp eax,16
    c += b'\x72' + rel8(b2 + 3, wait)                             # +3  jb wait
    c += b'\x85\xFF'                                              # +5  test edi,edi
    c += b'\x74' + rel8(b2 + 7, store)                            # +7  jz store
    c += b'\x8D\x86' + disp32(pic, s_end)                         # +9  lea eax,[esi+"timeEndPeriod"]
    c += b'\x50\x57'                                              # +15 push eax ; push edi
    c += b'\xE8' + rel32(b2 + 17, th['GetProcAddress'])           # +17 call GetProcAddress
    c += b'\x85\xC0'                                              # +22 test eax,eax
    c += b'\x74' + rel8(b2 + 24, store)                           # +24 jz store
    c += b'\x6A\x01\xFF\xD0'                                      # +26 push 1 ; call eax         timeEndPeriod(1)
    assert len(c) == 30
    c += b'\x89\x96' + disp32(pic, LAST_VA)                       # +30 store: mov [esi+last],edx
    c += b'\x58'                                                  # +36 pop eax
    c += b'\xE9' + rel32(b2 + 37, HOOK_VA + delta + 6)            # +37 jmp present's epilogue (after the lea)
    assert len(c) == BODY2_LEN
    body2 = bytes(c)
    # ---- body 3: the strings ----------------------------------------------------------------------
    assert len(STRINGS) == BODY3_LEN
    body3 = STRINGS
    return body1, body2, body3


def reloc_entries(data, lo_va, hi_va):
    """[(file offset of the .reloc word, value)] of the HIGHLOW entries describing dwords in [lo_va, hi_va)."""
    rva, rsize, rptr = sections(data)['.reloc']
    out = []
    pos = rptr
    end = rptr + rsize
    while pos + 8 <= end:
        page, size = struct.unpack_from('<II', data, pos)
        if size < 8:
            break
        for i in range(8, size, 2):
            v = struct.unpack_from('<H', data, pos + i)[0]
            if v >> 12 == 3 and lo_va <= IMAGE_BASE + page + (v & 0xFFF) < hi_va:
                out.append((pos + i, v))
        pos += size
    return out


def analyse(img):
    d = img.data
    hook_va = img.find(HOOK_CONTEXT, 'present epilogue', adjust=20)
    delta = hook_va - HOOK_VA
    if delta not in (0, 0x60):
        raise SystemExit('unexpected present() epilogue address %#x' % hook_va)
    hook_file = img.va2file(hook_va)
    hook_cur = bytes(d[hook_file:hook_file + 6])
    body1, body2, body3 = build_code(delta)
    bodies = [('remap dead body 1 (frame limiter, part 1)', BODY1_VA + delta, BODY1_STOCK, body1),
              ('remap dead body 2 (frame limiter, part 2)', BODY2_VA + delta, BODY2_STOCK, body2),
              ('remap dead body 3 (the three winmm names)', BODY3_VA + delta, BODY3_STOCK, body3)]
    hook_new = b'\xE9' + rel32(hook_va, BODY1_VA + delta) + b'\x90'
    # the .idata slack must really be slack: raw end below the page end that the next section starts at
    iva, irsize, _ = img.secs['.idata']
    slack_lo = IMAGE_BASE + iva + irsize
    slack_hi = IMAGE_BASE + ((iva + irsize + 0xFFF) & ~0xFFF)
    if not (slack_lo <= LAST_VA and LAST_VA + 4 <= slack_hi):
        raise SystemExit('.idata page slack is %#x..%#x, the timestamp slot %#x is outside it' % (slack_lo, slack_hi, LAST_VA))
    nxt = min(IMAGE_BASE + va for n, (va, _, _) in img.secs.items() if IMAGE_BASE + va > IMAGE_BASE + iva)
    if nxt < slack_hi:
        raise SystemExit('a section starts inside the .idata page (%#x)' % nxt)
    ddraw_applied = all(img.matches(va, pat) for va, pat in {k + delta: v for k, v in DDRAW_JMPS.items()}.items())
    # state
    states = {}
    states['hook'] = 'patched' if hook_cur == hook_new else 'stock' if hook_cur == HOOK_STOCK else 'UNKNOWN'
    for name, va, stock, new in bodies:
        f = img.va2file(va)
        cur = bytes(d[f:f + len(new)])
        states[name] = 'patched' if cur == new else 'stock' if img.matches(va, stock) else 'UNKNOWN'
    relocs = []
    for name, va, stock, new in bodies:
        relocs += reloc_entries(d, va, va + len(new))
    return dict(delta=delta, hook_va=hook_va, hook_file=hook_file, hook_cur=hook_cur, hook_new=hook_new,
                bodies=bodies, states=states, ddraw=ddraw_applied, relocs=relocs, page=(BODY1_VA + delta) & ~0xFFF)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    img = Image(data)
    r = analyse(img)
    delta = r['delta']
    print('%s: %s build; present() epilogue VA 0x%08X, stub in remap bodies 0x%08X / 0x%08X / 0x%08X, timestamp at 0x%08X%s'
          % (a.exe, 'Council Wars' if delta else 'Classic', r['hook_va'], BODY1_VA + delta, BODY2_VA + delta, BODY3_VA + delta,
             LAST_VA, '' if r['ddraw'] else '; fix ddraw NOT applied (the remap bodies are still reachable)'))
    for k, v in r['states'].items():
        print('  %-50s %s' % (k, v))
    kinds = set(r['states'].values())
    overall = 'patched' if kinds == {'patched'} else 'stock' if kinds == {'stock'} else 'MIXED'
    if overall == 'patched' and r['relocs']:
        overall = 'MIXED'                                      # code in, HIGHLOW entries still pointing into it
    print('%s: %s (%d HIGHLOW .reloc entries inside the bodies)' % (a.exe, overall, len(r['relocs'])))
    if a.command == 'verify':
        return 0 if overall in ('stock', 'patched') else 1
    if overall == 'patched':
        print('already patched, nothing to do')
        return 0
    if overall != 'stock':
        raise SystemExit('refusing: not in stock state')
    edits = [('present epilogue: lea esp -> jmp frame limiter', r['hook_va'], r['hook_file'], r['hook_cur'], r['hook_new'],
              'the epilogue of present() (after the Flip, also reached from the error paths and the minimised idle stub) runs the limiter first')]
    notes = ['lea esp,[ebp+82h]; push eax; call $+5; pop esi; now = timeGetTime(); if now - last < 16: GetModuleHandleA("winmm.dll"), '
             'GetProcAddress("timeBeginPeriod")(1) if found; then Sleep(1)+timeGetTime() until 16 ms passed (loop continues in body 2); jmp +5 over the live ddraw jump',
             'cmp/jb back to the wait loop; GetProcAddress("timeEndPeriod")(1) if the module was found; store: last = now; pop eax; jmp back into present()\'s epilogue',
             'the three names the limiter looks up at run time: "winmm.dll", "timeBeginPeriod", "timeEndPeriod"']
    for (name, va, stock, new), note in zip(r['bodies'], notes):
        f = img.va2file(va)
        edits.append((name, va, f, bytes(data[f:f + len(new)]), new, note))
    for name, va, f, old, new, note in edits:
        print('  %s   VA 0x%x file 0x%x %d bytes: %s -> %s; %s' % (name, va, f, len(new), old.hex(' '), new.hex(' '), note))
    for off, val in r['relocs']:
        print('  .reloc @ file 0x%x: %04X -> %04X  (HIGHLOW entry of a dead absolute operand at 0x%08X -> type 0 padding)'
              % (off, val, val & 0xFFF, IMAGE_BASE + (r['page'] - IMAGE_BASE) + (val & 0xFFF)))
    if a.command == 'plan':
        if not r['ddraw']:
            print('note: apply needs fix ddraw first (its jumps make the remap bodies dead code)')
        return 0
    if not r['ddraw']:
        raise SystemExit('refusing: fix ddraw is not applied, the remap assert bodies are still live code (apply patch_ddraw_lost.py first)')
    bak = a.exe + '.fps.bak'
    shutil.copyfile(a.exe, bak)
    for name, va, f, old, new, note in edits:
        assert bytes(data[f:f + len(new)]) == old
        data[f:f + len(new)] = new
    for off, val in r['relocs']:
        struct.pack_into('<H', data, off, val & 0xFFF)
    open(a.exe, 'wb').write(data)
    print('written %s (%d code edits, %d .reloc entries); backup %s' % (a.exe, len(edits), len(r['relocs']), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

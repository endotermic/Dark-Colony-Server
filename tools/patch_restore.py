#!/usr/bin/env python3
"""Window restore after minimising (fix `restore`, both games): Alt+Tab / taskbar bring the game back.

Symptom (maintainer report 21 Sep 2026, "window restore after minimization is not working"):
leave the running game with Alt+Tab, the Win key or a click elsewhere and DirectDraw minimises it
(exclusive mode lost, desktop mode restored).  Coming back with Alt+Tab or the taskbar the game
stays minimised although it is the active window, or - once something restores the window - it
shows a black 1024x768 window on the desktop-resolution screen.  The process is alive and busy,
`error.log` stays empty.

Cause (ddex4.c): the game has no message loop.  Its per-frame `frame_end` (`0x0042F1C8`) pulls
posted messages with five range-filtered `PeekMessageA(NULL, min, max, PM_REMOVE)` calls - mouse,
keyboard, WM_SYSCOMMAND (only to swallow SC_SCREENSAVE), WM_SETCURSOR and WM_DESTROY (the last two
are never posted, that code is dead) - and never dispatches any of them.  Windows restores a
minimised window by POSTING `WM_SYSCOMMAND SC_RESTORE` to it (Alt+Tab, the taskbar button, Win+D),
so the restore request is removed from the queue and dropped: the window is activated but stays
iconic.  Being activated while iconic also defeats DirectDraw's own window hook, which re-sets the
exclusive display mode only during a proper activation: whoever restores the window afterwards
gets a black window at desktop resolution, and every `Restore` of the primary surface fails with
DDERR_WRONGMODE 0x8876024B for good (the game's own `SetDisplayMode` does not help: surfaces
created before the mode loss can never be restored after an application-side mode set).

Fix: the WM_SYSCOMMAND peek hands the message to `DefWindowProcA` (imported, called through the
linker's import thunk) unless it is SC_SCREENSAVE, which stays swallowed as before; and when the
message was SC_RESTORE the game immediately runs `ShowWindow(hwnd, SW_MINIMIZE)` followed by
`ShowWindow(hwnd, SW_RESTORE)`: a deactivate/activate cycle on a window that is not iconic at the
moment of activation, which is exactly the path DirectDraw's hook handles - it re-sets the mode,
the game's per-frame `restore_surfaces` (`0x0042E060`) repairs the surfaces and the next frame is
drawn.  Verified in game 21 Sep 2026 (Classic, 1280x800: Alt+Tab, taskbar button, Start menu +
taskbar, taskbar minimise + Alt+Tab; all four routes back to the menu within a frame or two).
The 86 new bytes replace the WM_SYSCOMMAND peek plus the two dead peeks in place
(107 bytes, Classic VA 0x0042F23D..0x0042F2A8, Council Wars +0x60); the calls go through the
import thunks, nothing else moves, no .reloc entry changes.

Second part (same day): while the game is minimised its main loop spins at 100 % of a core - the
per-frame `present` (`0x0042E0FC`) fails its first BltFast with DDERR_SURFACELOST, `restore_surfaces`
fails with DDERR_WRONGMODE and the routine returns early, so the Flip that normally throttles the
loop is never reached (~4 400 passes per second measured).  The `jne exit` after that failed
restore (`0x0042E14F`) is redirected to a 12-byte stub in the NOP tail of the block above:
`Sleep(1)` (one system timer period, <= 16 ms, well inside the 44 ms game tick) and `jmp exit`.
Game ticks are clock-driven and still run every pass, only the idle spin is gone; once the window
is back the restore succeeds and the branch is never taken.  Sleep is an existing import with a
linker thunk, so this call is relative as well.

CLI
    python patch_restore.py verify EXE
    python patch_restore.py plan   EXE
    python patch_restore.py apply  EXE        (writes EXE.restore.bak first)
"""

import argparse
import shutil
import struct
import sys

SIZE_OF = {659456: 'classic', 659968: 'cw'}
IMAGE_BASE = 0x400000

# frame_end, from the WM_SYSCOMMAND peek to the epilogue (107 bytes); ? = IAT operands
#   push 1 ; push 112h ; push 112h ; push 0 ; lea eax,[ebp-1Ch] ; push eax ; call cs:[PeekMessageA]
#   test eax,eax ; je +9 ; cmp [ebp-14h],0F140h ; je +45h
#   push 1 ; push 20h ; push 20h ; push 0 ; lea eax,[ebp-1Ch] ; push eax ; call cs:[PeekMessageA] ; test ; je +9 ; push 0 ; call cs:[SetCursor]
#   push 1 ; push 2 ; push 2 ; push 0 ; lea eax,[ebp-1Ch] ; push eax ; call cs:[PeekMessageA] ; test ; je +0Eh ; call release_all ; push 0 ; call cs:[PostQuitMessage]
STOCK = (b'\x6A\x01\x68\x12\x01\x00\x00\x68\x12\x01\x00\x00\x6A\x00\x8D\x45\xE4\x50\x2E\xFF\x15????'
         b'\x85\xC0\x74\x09\x81\x7D\xEC\x40\xF1\x00\x00\x74\x45'
         b'\x6A\x01\x6A\x20\x6A\x20\x6A\x00\x8D\x45\xE4\x50\x2E\xFF\x15????\x85\xC0\x74\x09\x6A\x00\x2E\xFF\x15????'
         b'\x6A\x01\x6A\x02\x6A\x02\x6A\x00\x8D\x45\xE4\x50\x2E\xFF\x15????\x85\xC0\x74\x0E\xE8????\x6A\x00\x2E\xFF\x15????')
EPILOGUE = b'\x89\xEC\x5D\x5A\x59\x5B\xC3'        # mov esp,ebp ; pop ebp ; pop edx ; pop ecx ; pop ebx ; ret
BLOCK_LEN = len(STOCK)
PEEK_IAT_OFF = 21                                  # offset of the PeekMessageA IAT operand inside STOCK
# wndproc tail: mov ebx,[ebp+20h] ; push ebx ; push edi ; push esi ; mov esi,[ebp+14h] ; push esi ; call cs:[DefWindowProcA]
DEFWP_PATTERN = b'\x8B\x5D\x20\x53\x57\x56\x8B\x75\x14\x56\x2E\xFF\x15????'
# create_window: push 1 ; mov edx,[hwnd] ; push edx ; call cs:[ShowWindow]
SHOW_PATTERN = b'\x6A\x01\x8B\x15????\x52\x2E\xFF\x15????'
# present(): cmp eax,DDERR_SURFACELOST ; jne +0Dh ; call restore_surfaces ; test eax,eax ; jne exit
PRESENT_PATTERN = b'\x3D\xC2\x01\x76\x88\x75\x0D\xE8????\x85\xC0\x0F\x85????'
PRESENT_JNE_OFF = 14                               # offset of the 0F 85 inside the pattern
CODE_LEN = 86                                      # new code in the block; the sleep stub follows at +86
STUB_LEN = 12
SLEEP_MS = 1
SC_SCREENSAVE = 0xF140
SC_RESTORE = 0xF120
SW_MINIMIZE, SW_RESTORE = 6, 9


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


def rel32(from_va, to_va):
    return struct.pack('<i', to_va - (from_va + 5))


def rel8(from_va, to_va):
    d = to_va - (from_va + 2)
    assert -128 <= d <= 127, hex(d)
    return struct.pack('<b', d)


def new_block(block_va, epi_va, thunk_peek, thunk_defwp, thunk_show, thunk_sleep=None, exit_va=None):
    """The 86-byte replacement of the WM_SYSCOMMAND pump, then (unless thunk_sleep is None) the
    12-byte idle stub for present()'s failed-restore branch, NOP-padded to BLOCK_LEN."""
    b = bytearray()
    a = block_va

    def emit(x):
        nonlocal a
        b.extend(x)
        a += len(x)
    emit(b'\x6A\x01')                                    # push PM_REMOVE
    emit(b'\x68' + struct.pack('<I', 0x112))             # push WM_SYSCOMMAND  (max)
    emit(b'\x68' + struct.pack('<I', 0x112))             # push WM_SYSCOMMAND  (min)
    emit(b'\x6A\x00')                                    # push NULL           (any window of the thread)
    emit(b'\x8D\x45\xE4')                                # lea  eax,[ebp-1Ch]  MSG
    emit(b'\x50')                                        # push eax
    emit(b'\xE8' + rel32(a, thunk_peek))                 # call PeekMessageA (thunk)
    emit(b'\x85\xC0')                                    # test eax,eax
    emit(b'\x74' + rel8(a, epi_va))                      # je   epilogue       (nothing posted)
    emit(b'\x81\x7D\xEC' + struct.pack('<I', SC_SCREENSAVE))  # cmp [ebp-14h],SC_SCREENSAVE  (msg.wParam)
    emit(b'\x74' + rel8(a, epi_va))                      # je   epilogue       (swallowed, as before)
    emit(b'\xFF\x75\xF0')                                # push [ebp-10h]      msg.lParam
    emit(b'\xFF\x75\xEC')                                # push [ebp-14h]      msg.wParam
    emit(b'\x68' + struct.pack('<I', 0x112))             # push WM_SYSCOMMAND
    emit(b'\xFF\x75\xE4')                                # push [ebp-1Ch]      msg.hwnd
    emit(b'\xE8' + rel32(a, thunk_defwp))                # call DefWindowProcA (thunk): SC_RESTORE / SC_MINIMIZE / ... take effect
    emit(b'\x81\x7D\xEC' + struct.pack('<I', SC_RESTORE))  # cmp [ebp-14h],SC_RESTORE
    emit(b'\x75' + rel8(a, epi_va))                      # jne  epilogue
    emit(b'\x6A' + bytes([SW_MINIMIZE]))                 # push SW_MINIMIZE
    emit(b'\xFF\x75\xE4')                                # push msg.hwnd
    emit(b'\xE8' + rel32(a, thunk_show))                 # call ShowWindow (thunk)   deactivate ...
    emit(b'\x6A' + bytes([SW_RESTORE]))                  # push SW_RESTORE
    emit(b'\xFF\x75\xE4')                                # push msg.hwnd
    emit(b'\xE8' + rel32(a, thunk_show))                 # call ShowWindow (thunk)   ... and activate non-iconic: DirectDraw re-sets the mode
    emit(b'\xEB' + rel8(a, epi_va))                      # jmp  epilogue
    assert len(b) == CODE_LEN, len(b)
    if thunk_sleep is not None:
        # idle stub, entered from present() when the surfaces could not be restored (window minimised)
        emit(b'\x6A' + bytes([SLEEP_MS]))                  # push 1
        emit(b'\xE8' + rel32(a, thunk_sleep))               # call Sleep (thunk)
        emit(b'\xE9' + rel32(a, exit_va))                   # jmp  present() exit
        assert len(b) == CODE_LEN + STUB_LEN, len(b)
    b += b'\x90' * (BLOCK_LEN - len(b))
    return bytes(b)


def import_slot(data, dll, name):
    """IAT address of import `name` of `dll` (case-insensitive dll match)."""
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    opt = pe + 24
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    st = opt + struct.unpack_from('<H', data, pe + 20)[0]
    secs = []
    for i in range(nsec):
        vsize, rva, rsize, rptr = struct.unpack_from('<IIII', data, st + i * 40 + 8)
        secs.append((rva, rsize, rptr))

    def off(rva):
        for r, sz, pt in secs:
            if r <= rva < r + sz:
                return rva - r + pt
        raise SystemExit('rva %#x outside every section' % rva)
    imp_rva = struct.unpack_from('<I', data, opt + 96 + 8)[0]
    d = off(imp_rva)
    while True:
        int_rva, _, _, name_rva, iat_rva = struct.unpack_from('<IIIII', data, d)
        if int_rva == 0 and iat_rva == 0:
            break
        dn = data[off(name_rva):off(name_rva) + 32].split(b'\0')[0].decode()
        if dn.lower() == dll.lower():
            i = 0
            while True:
                th = struct.unpack_from('<I', data, off(int_rva) + 4 * i)[0]
                if th == 0:
                    break
                if not th & 0x80000000 and data[off(th) + 2:off(th) + 2 + len(name) + 1] == name.encode() + b'\0':
                    return IMAGE_BASE + iat_rva + 4 * i
                i += 1
        d += 20
    raise SystemExit('import %s!%s not found' % (dll, name))


def analyse(data):
    game = SIZE_OF.get(len(data))
    if game is None:
        raise SystemExit('%d bytes: neither the Classic (659456) nor the Council Wars (659968) build' % len(data))
    secs = sections(data)
    ava, arsize, arptr = secs['AUTO']
    aend = arptr + arsize

    def va_of(off):
        return off - arptr + ava

    def off_of(va):
        return va - ava + arptr

    # the import thunks are located from the IAT slots the stock code calls
    defwp_site = unique(data, DEFWP_PATTERN, 'wndproc DefWindowProcA tail', arptr, aend)
    iat_defwp = struct.unpack_from('<I', data, defwp_site + 13)[0]
    show_site = unique(data, SHOW_PATTERN, 'create_window ShowWindow call', arptr, aend)
    iat_show = struct.unpack_from('<I', data, show_site + 12)[0]

    def thunk(iat, what):
        return va_of(unique(data, b'\xFF\x25' + struct.pack('<I', iat), 'import thunk of ' + what, arptr, aend))
    thunk_defwp = thunk(iat_defwp, 'DefWindowProcA')
    thunk_show = thunk(iat_show, 'ShowWindow')
    iat_sleep = import_slot(data, 'KERNEL32.dll', 'Sleep')
    thunk_sleep = thunk(iat_sleep, 'Sleep')
    present_off = unique(data, PRESENT_PATTERN, "present(): failed-restore branch", arptr, aend)
    jne_off = present_off + PRESENT_JNE_OFF
    jne_va = va_of(jne_off)
    old_jne = bytes(data[jne_off:jne_off + 6])

    stock_hits = find_all(data, STOCK, arptr, aend)
    if stock_hits:
        if len(stock_hits) != 1:
            raise SystemExit('expected one frame_end WM_SYSCOMMAND pump, found %d' % len(stock_hits))
        block_off = stock_hits[0]
        iat_peek = struct.unpack_from('<I', data, block_off + PEEK_IAT_OFF)[0]
        state = 'stock'
    else:
        # already patched? our block starts like the stock one up to the call and ends with NOPs before the epilogue
        head = STOCK[:18]
        cands = [h for h in find_all(data, head, arptr, aend)
                 if data[h + 18] == 0xE8 and data[h + BLOCK_LEN:h + BLOCK_LEN + len(EPILOGUE)] == EPILOGUE]
        if len(cands) != 1:
            raise SystemExit('frame_end WM_SYSCOMMAND pump not found in stock form and no patched candidate (%d)' % len(cands))
        block_off = cands[0]
        rel = struct.unpack_from('<i', data, block_off + 19)[0]
        peek_thunk_va = va_of(block_off + 18) + 5 + rel
        if data[off_of(peek_thunk_va):off_of(peek_thunk_va) + 2] != b'\xFF\x25':
            raise SystemExit('the call at %#x does not target an import thunk' % va_of(block_off + 18))
        iat_peek = struct.unpack_from('<I', data, off_of(peek_thunk_va) + 2)[0]
        state = 'patched'
    thunk_peek = thunk(iat_peek, 'PeekMessageA')
    block_va = va_of(block_off)
    epi_off = block_off + BLOCK_LEN
    if data[epi_off:epi_off + len(EPILOGUE)] != EPILOGUE:
        raise SystemExit('frame_end epilogue not found after the block at %#x' % block_va)
    epi_va = va_of(epi_off)
    old = bytes(data[block_off:block_off + BLOCK_LEN])
    stub_va = block_va + CODE_LEN
    jne_target = jne_va + 6 + struct.unpack_from('<i', old_jne, 2)[0]
    if jne_target == stub_va:
        # already redirected: the exit is the stub's final jmp
        exit_va = stub_va + STUB_LEN + struct.unpack_from('<i', data, block_off + CODE_LEN + 8)[0]
        jne_state = 'patched'
    else:
        exit_va = jne_target
        jne_state = 'stock'
    if not (ava <= exit_va < ava + arsize):
        raise SystemExit('present() exit %#x is outside the code section' % exit_va)
    new = new_block(block_va, epi_va, thunk_peek, thunk_defwp, thunk_show, thunk_sleep, exit_va)
    v1 = new_block(block_va, epi_va, thunk_peek, thunk_defwp, thunk_show)      # 21 Sep 2026 morning form: no idle stub
    new_jne = b'\x0F\x85' + rel32(jne_va + 1, stub_va)                         # rel32 counted from the end of the 6-byte jne
    if state == 'patched':
        if old == new and jne_state == 'patched':
            pass
        elif old == v1 and jne_state == 'stock':
            state = 'v1 (restore without the idle stub)'
        else:
            raise SystemExit('block at %#x / jne at %#x are neither stock nor a known patched form' % (block_va, jne_va))
    elif jne_state != 'stock':
        raise SystemExit('stock block at %#x but the present() jne at %#x is already redirected' % (block_va, jne_va))
    return dict(game=game, state=state, block_va=block_va, block_off=block_off, epi_va=epi_va,
                thunk_peek=thunk_peek, thunk_defwp=thunk_defwp, thunk_show=thunk_show, thunk_sleep=thunk_sleep,
                iat_peek=iat_peek, iat_defwp=iat_defwp, iat_show=iat_show, old=old, new=new,
                jne_va=jne_va, jne_off=jne_off, old_jne=old_jne, new_jne=new_jne, stub_va=stub_va, exit_va=exit_va)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    r = analyse(data)
    print('%s: %s build, %s; frame_end WM_SYSCOMMAND pump at VA 0x%08X (%d bytes, epilogue 0x%08X), present() failed-restore jne at 0x%08X (exit 0x%08X), import thunks PeekMessageA 0x%08X DefWindowProcA 0x%08X ShowWindow 0x%08X Sleep 0x%08X'
          % (a.exe, 'Classic' if r['game'] == 'classic' else 'Council Wars', r['state'], r['block_va'], BLOCK_LEN, r['epi_va'],
             r['jne_va'], r['exit_va'], r['thunk_peek'], r['thunk_defwp'], r['thunk_show'], r['thunk_sleep']))
    if a.command == 'verify':
        return 0
    print('  frame_end: posted WM_SYSCOMMAND dispatched, SC_RESTORE re-activates   VA 0x%x file 0x%x %d bytes: %s -> %s; the WM_SYSCOMMAND PeekMessageA(PM_REMOVE) keeps swallowing SC_SCREENSAVE but hands every other system command to DefWindowProcA(msg.hwnd, WM_SYSCOMMAND, wParam, lParam) - so the SC_RESTORE that Alt+Tab, the taskbar and Win+D post to a minimised window restores it; for SC_RESTORE it then calls ShowWindow(hwnd, SW_MINIMIZE) and ShowWindow(hwnd, SW_RESTORE), the deactivate/activate cycle on a non-iconic window that makes DirectDraw re-set the exclusive display mode; the two dead peeks for WM_SETCURSOR and WM_DESTROY (never posted) are replaced; bytes 86..97 are the idle stub for present(): Sleep(%d) through the thunk 0x%08X, then jmp to the present() exit 0x%08X; the rest is NOP; calls through the import thunks 0x%08X / 0x%08X / 0x%08X, no .reloc changes'
          % (r['block_va'], r['block_off'], BLOCK_LEN, r['old'].hex(' '), r['new'].hex(' '), SLEEP_MS, r['thunk_sleep'], r['exit_va'], r['thunk_peek'], r['thunk_defwp'], r['thunk_show']))
    print('  present(): failed surface restore -> idle stub instead of exit   VA 0x%x file 0x%x 6 bytes: %s -> %s; the jne after restore_surfaces (taken while the window is minimised: BltFast DDERR_SURFACELOST, Restore DDERR_WRONGMODE) goes to the stub at 0x%08X, which sleeps one timer period and continues to the original exit 0x%08X - the main loop no longer spins at 100%% CPU while minimised, game ticks are clock-driven and unaffected'
          % (r['jne_va'], r['jne_off'], r['old_jne'].hex(' '), r['new_jne'].hex(' '), r['stub_va'], r['exit_va']))
    if a.command == 'plan':
        return 0
    if r['state'] != 'patched':
        bak = a.exe + '.restore.bak'
        shutil.copyfile(a.exe, bak)
        data[r['block_off']:r['block_off'] + BLOCK_LEN] = r['new']
        data[r['jne_off']:r['jne_off'] + 6] = r['new_jne']
        open(a.exe, 'wb').write(data)
        print('written %s; backup %s' % (a.exe, bak))
    else:
        print('exe already patched, nothing to do')
    return 0


if __name__ == '__main__':
    sys.exit(main())

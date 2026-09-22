#!/usr/bin/env python3
"""Wave loader opens files with CreateFileA instead of OpenFile (fix `longpath`, both games): sounds load from any folder depth.

Symptom (22 Sep 2026, found while running a patched build from a 170-character folder path): about
five seconds after start, during the intro movie, the box "FILE NOT FOUND / A sound file is missing -
see error.log" appears and the game exits; error.log holds one line `unable to open file ` with an
EMPTY name.  The very same files run from a short path (`subst Q:`) without a hitch.  Every other
loader in the game (`fopen`, the Watcom C runtime, i.e. CreateFileA) had already opened dozens of
files from that folder.

Cause: the wave loader (`0x00452A50`, Council Wars `0x00452AB0`; sound2.dat banks, briefings, ambience)
is the ONLY code that opens files through the Win16-era **`OpenFile(name, &OFSTRUCT, OF_READ)`**
(KERNEL32 IAT slot `0x004804C8`, four call sites, all in this function).  `OpenFile` writes the file's
full path into `OFSTRUCT.szPathName[OFS_MAXPATHNAME]`, 128 bytes, and fails with `HFILE_ERROR` when the
full path does not fit - so every WAV fails for a game folder whose path plus `sound\\xxxxxxxx.wav` exceeds
~128 characters (a deep `Downloads\\...\\Dark-Colony-main\\DC - Council wars` install is enough).  The
loader tries `prefix+name` (`[ebp-0x80E]`, prefix slot `0x487DC0`, empty in Classic since fix `sounds`,
`exp/` in Council Wars) and then the bare name; both fail the same way; since fix `nocd` skipped the two
CD attempts the error exit prints the never-filled CD buffer `[ebp-0x40E]`, hence the empty name.

Fix (4 edits per exe, nothing moves, no .reloc change, register-relative operands and calls through
the linker's import thunks only):
  1. first live open, 20 bytes `push 0; lea eax,[ebp-0Eh]; push eax; lea eax,[ebp-80Eh]; push eax;
     call cs:[OpenFile]` -> `lea eax,[ebp-80Eh]; call open_read; 9 x nop`;
  2. second live open (bare name), 14 bytes `push 0; lea eax,[ebp-0Eh]; push eax; push ebx;
     call cs:[OpenFile]` -> `mov eax,ebx; call open_read; 7 x nop`;
  3. `open_read` = 22 bytes written over the first CD attempt, dead code since fix `nocd` (the
     `jmp` at the end of the second open skips it; nothing else targets it):
     `push 0 (hTemplate); push 0 (flags); push 3 (OPEN_EXISTING); push 0 (security);
      push 1 (FILE_SHARE_READ); push 80000000h (GENERIC_READ); push eax (name);
      call CreateFileA thunk; ret` - CreateFileA returns INVALID_HANDLE_VALUE (-1) on failure, the
     value the loader's `cmp eax,-1` tests already expect, and its handle is what `_llseek`, `ReadFile`
     and `_lclose` (the loader's other calls) take;
  4. the error exit's `lea eax,[ebp-40Eh]` (the CD buffer) -> `lea eax,[ebp-80Eh]`, so error.log and the
     box name the file that was tried (`prefix+name`).
Requires fix `nocd` (the `E9` form of the second open's exit is checked - the stub region must be dead).
Council Wars offsets are Classic + 0x60, found by pattern.  The OFSTRUCT at `[ebp-0Eh]` is no longer
written; nothing reads it.  `OpenFile` also searched the exe folder, the current folder, the Windows
folders and PATH for a bare name - the game's WAV names always carry a folder or sit in the game root,
which is the current folder, so CreateFileA finds the same files.

CLI
    python patch_longpath.py verify EXE
    python patch_longpath.py plan   EXE
    python patch_longpath.py apply  EXE        (writes EXE.longpath.bak first)
"""

import argparse
import shutil
import struct
import sys

SIZE_OF = {659456: 'classic', 659968: 'cw'}
IMAGE_BASE = 0x400000

# wave loader, first live open (prefix+name):  push 0 ; lea eax,[ebp-0Eh] ; push eax ; lea eax,[ebp-80Eh] ; push eax ;
#   call cs:[OpenFile] ; mov esi,eax ; cmp eax,-1 ; jne near
SITE_A = b'\x6A\x00\x8D\x45\xF2\x50\x8D\x85\xF2\xF7\xFF\xFF\x50\x2E\xFF\x15????\x89\xC6\x83\xF8\xFF\x0F\x85????'
SITE_A_LEN = 20
# second live open (bare name), right after:  push 0 ; lea eax,[ebp-0Eh] ; push eax ; push ebx ; call cs:[OpenFile] ;
#   mov esi,eax ; cmp eax,-1 ; jmp near (fix nocd; stock has a 6-byte `jne near` and falls into the CD attempt) ; nop
SITE_B = b'\x6A\x00\x8D\x45\xF2\x50\x53\x2E\xFF\x15????\x89\xC6\x83\xF8\xFF'
SITE_B_LEN = 14
NOCD_EXIT = b'\xE9????\x90'
STOCK_EXIT = b'\x0F\x85????'          # stock: jne near, falls into the CD attempt when the bare open fails too
# first CD attempt (dead since nocd): call cd_path_getter ; lea edi,[ebp-40Eh] ; mov esi,eax ; push edi ; mov al,[esi] ; mov [edi],al
DEAD_CD = b'\xE8????\x8D\xBD\xF2\xFB\xFF\xFF\x89\xC6\x57\x8A\x06\x88\x07'
STUB_LEN = 22
# error exit: lea eax,[ebp-40Eh] ; push eax ; push fmt ; mov edx,[error_log] ; push edx ; call fprintf
ERR_SITE = b'\x8D\x85\xF2\xFB\xFF\xFF\x50\x68????\x8B\x15????\x52\xE8'
ERR_SITE_NEW = b'\x8D\x85\xF2\xF7\xFF\xFF\x50\x68????\x8B\x15????\x52\xE8'
BUF1 = b'\xF2\xF7\xFF\xFF'        # [ebp-0x80E]  prefix+name
BUF2 = b'\xF2\xFB\xFF\xFF'        # [ebp-0x40E]  CD path buffer (never filled since nocd)


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
    return out, pe


def rva_to_off(secs, rva):
    for va, rsize, rptr in secs.values():
        if va - IMAGE_BASE <= rva < va - IMAGE_BASE + rsize:
            return rva - (va - IMAGE_BASE) + rptr
    raise SystemExit('RVA %#x is in no section' % rva)


def import_slot(data, secs, pe, dll, func):
    """VA of the IAT slot of dll!func, from the import directory."""
    opt = pe + 24
    imp_rva = struct.unpack_from('<I', data, opt + 96 + 1 * 8)[0]       # data directory[1] = imports
    d = rva_to_off(secs, imp_rva)
    while True:
        int_rva, _, _, name_rva, iat_rva = struct.unpack_from('<IIIII', data, d)
        if iat_rva == 0:
            break
        n = rva_to_off(secs, name_rva)
        name = data[n:data.index(b'\0', n)].decode('latin1')
        if name.lower() == dll.lower():
            t = rva_to_off(secs, int_rva or iat_rva)
            i = 0
            while True:
                e = struct.unpack_from('<I', data, t + i * 4)[0]
                if e == 0:
                    break
                if not e & 0x80000000:
                    h = rva_to_off(secs, e) + 2
                    if data[h:data.index(b'\0', h)] == func.encode():
                        return IMAGE_BASE + iat_rva + i * 4
                i += 1
        d += 20
    raise SystemExit('%s!%s is not imported' % (dll, func))


def find_all(data, pattern, lo=0, hi=None):
    hits = []
    hi = len(data) if hi is None else hi
    first = pattern[0:1]
    i = data.find(first, lo)
    while 0 <= i < hi:
        if i + len(pattern) <= len(data) and all(p == 0x3F or data[i + k] == p for k, p in enumerate(pattern)):
            hits.append(i)
        i = data.find(first, i + 1)
    return hits


def unique(data, pattern, what, lo=0, hi=None):
    hits = find_all(data, pattern, lo, hi)
    if len(hits) != 1:
        raise SystemExit('expected exactly one %s, found %d: %s' % (what, len(hits), ', '.join('%#x' % h for h in hits)))
    return hits[0]


def rel32(from_va, to_va):
    return struct.pack('<i', to_va - (from_va + 5))


def stub_bytes(stub_va, createfile_thunk_va):
    """open_read(eax = name) -> eax = handle or -1: CreateFileA(name, GENERIC_READ, FILE_SHARE_READ, 0, OPEN_EXISTING, 0, 0)."""
    b = bytearray()
    b += b'\x6A\x00'                    # push 0            hTemplateFile
    b += b'\x6A\x00'                    # push 0            dwFlagsAndAttributes
    b += b'\x6A\x03'                    # push 3            OPEN_EXISTING
    b += b'\x6A\x00'                    # push 0            lpSecurityAttributes
    b += b'\x6A\x01'                    # push 1            FILE_SHARE_READ
    b += b'\x68\x00\x00\x00\x80'        # push 80000000h    GENERIC_READ
    b += b'\x50'                        # push eax          lpFileName
    b += b'\xE8' + rel32(stub_va + len(b), createfile_thunk_va)   # call CreateFileA (import thunk)
    b += b'\xC3'                        # ret
    assert len(b) == STUB_LEN
    return bytes(b)


def analyse(data):
    game = SIZE_OF.get(len(data))
    if game is None:
        raise SystemExit('%d bytes: neither the Classic (659456) nor the Council Wars (659968) build' % len(data))
    secs, pe = sections(data)
    ava, arsize, arptr = secs['AUTO']

    def va_of(off):
        return off - arptr + ava

    slot_open = import_slot(data, secs, pe, 'KERNEL32.dll', 'OpenFile')
    slot_create = import_slot(data, secs, pe, 'KERNEL32.dll', 'CreateFileA')
    thunk_off = unique(data, b'\xFF\x25' + struct.pack('<I', slot_create), 'CreateFileA import thunk', arptr, arptr + arsize)
    thunk_va = va_of(thunk_off)

    # the function: locate the first open by its stock form or by our patched form
    a_hits = find_all(data, SITE_A, arptr, arptr + arsize)
    if len(a_hits) == 1:
        state = 'stock'
        a_off = a_hits[0]
        if struct.unpack_from('<I', data, a_off + 16)[0] != slot_open:
            raise SystemExit('the first open at %#x does not call OpenFile' % va_of(a_off))
    elif not a_hits:
        state = 'patched'
        patched_a = b'\x8D\x85\xF2\xF7\xFF\xFF\xE8????' + b'\x90' * 9 + b'\x89\xC6\x83\xF8\xFF\x0F\x85????'
        a_off = unique(data, patched_a, 'patched first open', arptr, arptr + arsize)
    else:
        raise SystemExit('expected exactly one wave-loader open site, found %d' % len(a_hits))
    b_off = a_off + SITE_A_LEN + 5 + 6            # mov esi,eax ; cmp eax,-1 ; jne near
    if state == 'stock':
        if not all(SITE_B[k] in (0x3F, data[b_off + k]) for k in range(len(SITE_B))):
            raise SystemExit('second open not found at %#x' % va_of(b_off))
        if struct.unpack_from('<I', data, b_off + 10)[0] != slot_open:
            raise SystemExit('the second open at %#x does not call OpenFile' % va_of(b_off))
    else:
        patched_b = b'\x89\xD8\xE8????' + b'\x90' * 7 + b'\x89\xC6\x83\xF8\xFF'
        if not all(patched_b[k] in (0x3F, data[b_off + k]) for k in range(len(patched_b))):
            raise SystemExit('patched second open not found at %#x' % va_of(b_off))
    exit_off = b_off + SITE_B_LEN + 5             # after mov esi,eax ; cmp eax,-1
    nocd = all(NOCD_EXIT[k] in (0x3F, data[exit_off + k]) for k in range(len(NOCD_EXIT)))
    if not nocd and not all(STOCK_EXIT[k] in (0x3F, data[exit_off + k]) for k in range(len(STOCK_EXIT))):
        raise SystemExit('the second open at %#x is followed by neither the stock `jne near` nor the `jmp` of fix nocd: %s'
                         % (va_of(b_off), bytes(data[exit_off:exit_off + 6]).hex(' ')))
    stub_off = exit_off + 6                      # both forms are 6 bytes; the first CD attempt follows
    stub_va = va_of(stub_off)
    stub = stub_bytes(stub_va, thunk_va)
    cur_stub = bytes(data[stub_off:stub_off + STUB_LEN])
    if state == 'stock':
        if not all(DEAD_CD[k] in (0x3F, data[stub_off + k]) for k in range(len(DEAD_CD))):
            raise SystemExit('the dead CD attempt at %#x does not look like the stock code: %s' % (stub_va, cur_stub.hex(' ')))
    elif cur_stub != stub:
        raise SystemExit('the open sites already call %#x but the stub there is not ours' % stub_va)
    err_pat = ERR_SITE if state == 'stock' else ERR_SITE_NEW
    err_off = unique(data, err_pat, 'wave-loader error exit', stub_off, stub_off + 0x120)

    a_va, b_va = va_of(a_off), va_of(b_off)
    new_a = b'\x8D\x85' + BUF1 + b'\xE8' + rel32(a_va + 6, stub_va) + b'\x90' * 9
    new_b = b'\x89\xD8\xE8' + rel32(b_va + 2, stub_va) + b'\x90' * 7
    edits = [
        (a_off, bytes(data[a_off:a_off + SITE_A_LEN]), new_a,
         'wave loader, first open (prefix+name): OpenFile(name,&ofs,OF_READ) -> lea eax,[ebp-80Eh]; call open_read %#010x; 9 x nop' % stub_va),
        (b_off, bytes(data[b_off:b_off + SITE_B_LEN]), new_b,
         'wave loader, second open (bare name): OpenFile(ebx,&ofs,OF_READ) -> mov eax,ebx; call open_read %#010x; 7 x nop' % stub_va),
        (stub_off, cur_stub, stub,
         'stub open_read over the dead first CD attempt: push 0,0,OPEN_EXISTING,0,FILE_SHARE_READ,GENERIC_READ,eax; call CreateFileA thunk %#010x (IAT slot %#010x); ret - returns the handle or -1 like OpenFile did' % (thunk_va, slot_create)),
        (err_off + 2, BUF2 if state == 'stock' else BUF1, BUF1,
         'error exit fprintf/MessageBox name: lea eax,[ebp-40Eh] (CD buffer, empty since nocd) -> lea eax,[ebp-80Eh] (prefix+name)'),
    ]
    return dict(game=game, state=state, nocd=nocd, func_va=a_va, a_va=a_va, b_va=b_va, stub_va=stub_va, thunk_va=thunk_va,
                slot_create=slot_create, slot_open=slot_open, err_va=va_of(err_off), edits=edits, va_of=va_of)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    r = analyse(data)
    print('%s: %s build, %s, fix nocd %s; wave loader opens at VA 0x%08X / 0x%08X, stub VA 0x%08X (first CD attempt, dead with nocd), CreateFileA thunk 0x%08X (IAT slot 0x%08X), OpenFile slot 0x%08X, error exit 0x%08X'
          % (a.exe, 'Classic' if r['game'] == 'classic' else 'Council Wars', r['state'], 'applied' if r['nocd'] else 'NOT applied',
             r['a_va'], r['b_va'], r['stub_va'], r['thunk_va'], r['slot_create'], r['slot_open'], r['err_va']))
    if a.command == 'verify':
        return 0
    for off, old, new, note in r['edits']:
        head, _, tail = note.partition(':')
        print('  %s VA 0x%x file 0x%x %d bytes: %s -> %s;%s' % (head, r['va_of'](off), off, len(old), old.hex(' '), new.hex(' '), tail))
    if a.command == 'plan':
        return 0
    if r['state'] == 'stock':
        if not r['nocd']:
            raise SystemExit('refusing to apply: fix nocd is not applied (the second open still falls into the CD attempt '
                             'that hosts the stub) - apply patch_nocd.py first')
        bak = a.exe + '.longpath.bak'
        shutil.copyfile(a.exe, bak)
        for off, old, new, note in r['edits']:
            assert bytes(data[off:off + len(old)]) == old
            data[off:off + len(new)] = new
        open(a.exe, 'wb').write(data)
        print('written %s; backup %s' % (a.exe, bak))
    else:
        print('exe already patched, nothing to do')
    return 0


if __name__ == '__main__':
    sys.exit(main())

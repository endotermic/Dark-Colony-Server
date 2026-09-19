#!/usr/bin/env python3
"""Make the patched Classic exe read its WAV files from the game root, not from exp/ (dc16new.exe only).

Classic dc16.exe and Council Wars ENGEXP16.EXE are the same code base.  Council Wars opens every
data file through an overlay helper that prepends "exp/" and falls back to the bare name; in the
Classic build that helper has no prefix - with one exception: the wave loader (wave.c, 0x00452A50,
the function that opens "mission/h%d.wav" briefings and every other WAV) has its own prefix slot,
the 8-byte DGROUP string at VA 0x00487DC0 (file 0x855C0), and in the Classic build that slot still
says "exp/".  The loader builds prefix+name into a stack buffer, opens it, and only if that fails
opens the bare name (the third attempt, the CD path, is disabled by patch `nocd`).

In the old "DC - Classic" folder no exp/ tree existed, so the first attempt always failed and the
leftover was harmless.  Since 15 Sep 2026 both games run from "DC - Council wars", where
exp/mission/h1..h8.wav and g1..g8.wav are the Council Wars briefings and exp/sound/water.wav is the
Council Wars water ambience: the Classic exe found those first and played the Council Wars briefings
for Classic missions 1-8 (maintainer report 19 Sep 2026).  All other WAV names of the Classic game
have no exp/ twin.

The fix empties the prefix: the four bytes "exp/" become NUL, the first open then already uses the
bare name.  Data only, in place, no code and no .reloc entry changes; the slot is referenced by one
instruction (mov esi,0x00487DC0 at 0x00452A68).  Council Wars' engexp16new.exe is refused: its
wave loader must keep "exp/" (and the OZI MISSIONS mode swaps that very slot).

CLI
    python patch_wavprefix.py verify EXE
    python patch_wavprefix.py plan   EXE
    python patch_wavprefix.py apply  EXE        (writes EXE.wavprefix.bak first)
"""

import argparse
import shutil
import struct
import sys

DGROUP_VA_TO_FILE = {'classic': 0x402800, 'cw': 0x402600}
SIZE_OF = {659456: 'classic', 659968: 'cw'}
STOCK = b'exp/\0\0\0\0'
NEW = b'\0\0\0\0\0\0\0\0'
EDIT_LEN = 4                                   # only the four letters change
# wave loader: mov esi,imm32 ; lea edi,[ebp-80Eh] ; push edi ; mov al,[esi] (the strcpy of the prefix)
CODE_PATTERN = b'\x8D\xBD\xF2\xF7\xFF\xFF\x57\x8A\x06\x88\x07'


def sections(data):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    opt = struct.unpack_from('<H', data, pe + 20)[0]
    base = struct.unpack_from('<I', data, pe + 24 + 28)[0]      # PE32 ImageBase (0x400000)
    st = pe + 24 + opt
    out = {}
    for i in range(nsec):
        s = data[st + i * 40: st + i * 40 + 40]
        vsize, rva, rsize, rptr = struct.unpack_from('<IIII', s, 8)
        out[s[:8].rstrip(b'\0').decode()] = (base + rva, rsize, rptr)   # absolute VA, raw size, file ptr
    return out


def find_site(data, game):
    """(code_va, slot_file_offset, slot_va, state): the wave loader's prefix load and the slot it names."""
    secs = sections(data)
    ava, arsize, arptr = secs['AUTO']
    dva, drsize, drptr = secs['DGROUP']
    hits = []
    i = 0
    while True:
        i = data.find(CODE_PATTERN, i)
        if i < 0:
            break
        if data[i - 5] == 0xBE:                # mov esi,imm32 right before it
            imm = struct.unpack_from('<I', data, i - 4)[0]
            if dva <= imm < dva + drsize:
                hits.append((i - 5, imm))
        i += 1
    if len(hits) != 1:
        raise SystemExit('expected exactly one wave-loader prefix load, found %d: %s'
                         % (len(hits), ', '.join('%#x' % h[0] for h in hits)))
    code_off, slot_va = hits[0]
    slot_off = slot_va - dva + drptr
    assert slot_off == slot_va - DGROUP_VA_TO_FILE[game], 'DGROUP mapping mismatch'
    cur = bytes(data[slot_off:slot_off + 8])
    state = 'stock' if cur == STOCK else 'patched' if cur == NEW else 'other'
    if state == 'other':
        raise SystemExit('prefix slot at file %#x holds %r, neither "exp/" nor empty' % (slot_off, cur))
    return code_off + 0x400C00, slot_off, slot_va, state


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    game = SIZE_OF.get(len(data))
    if game != 'classic':
        print('%s: %s - not a Classic dc16.exe build (%d bytes); the Council Wars wave loader must keep its exp/ prefix, nothing to do'
              % (a.exe, 'Council Wars build' if game == 'cw' else 'unknown build', len(data)))
        return 0 if a.command == 'verify' else 2
    code_va, off, va, state = find_site(data, game)
    print('%s: wave-loader prefix slot %s (%r), file %#x VA %#x, loaded by mov esi,imm32 at VA %#x'
          % (a.exe, state, bytes(data[off:off + 8]), off, va, code_va))
    if a.command == 'verify':
        return 0
    print('  DGROUP string "exp/" -> "" (wave-loader prefix)  file %#x VA %#x %d bytes: %s -> %s; the wave loader (0x%08X) builds prefix+name first and falls back to the bare name, so in the shared Council Wars folder the Classic exe played exp/mission/h1-h8.wav, g1-g8.wav (the Council Wars briefings) and exp/sound/water.wav instead of the Classic files in MISSION/ and SOUND/; the slot is data only, same address, no code and no .reloc entry changes'
          % (off, va, EDIT_LEN, STOCK[:EDIT_LEN].hex(' '), NEW[:EDIT_LEN].hex(' '), code_va - 0x18))
    if a.command == 'plan':
        return 0
    if state == 'stock':
        bak = a.exe + '.wavprefix.bak'
        shutil.copyfile(a.exe, bak)
        data[off:off + EDIT_LEN] = NEW[:EDIT_LEN]
        open(a.exe, 'wb').write(data)
        print('written %s; backup %s' % (a.exe, bak))
    else:
        print('exe already patched, nothing to do')
    return 0


if __name__ == '__main__':
    sys.exit(main())

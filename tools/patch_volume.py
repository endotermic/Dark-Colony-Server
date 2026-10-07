#!/usr/bin/env python3
"""The SOUND slider attenuates the sound effects only, no longer the game's Windows per-application volume (fix `volume`,
Dark Colony Ultimate only).

Why (7 Oct 2026, maintainer: "I was trying to lower the volume of the game and to keep the volume of the music higher,
but both sliders were changing all audio" -> "CD music slider works as expected, but original sound slider regulates
both (music and effects) simultaneously"; DC16_DISPLAY_AND_RESOLUTION.md section 10.78): the options screen's sound
`-`/`+` (widgets 0x2A/0x2B) call `set_volume(level, 1)` (display slot +0xE8, `0x00452858`), whose method 1 walks the
legacy mixer API for the first mixer with a `MIXERLINE_COMPONENTTYPE_SRC_WAVEOUT` line and sets that line's VOLUME
control (`0x004525E0..0x004527F0`, `mixerSetControlDetails` wrapper `0x004527F4`, `mixerClose` wrapper `0x00452820`).
Since Windows Vista that line is the process's audio session - the game's own slider in the Windows Volume Mixer - so
it scales everything the process plays: the effects and, since fix `music` plays the soundtrack inside the same
process, the music.  Measured 7 Oct 2026 (scratch/voltest.py): level 5 -> session 0.5, level 2 -> 0.2, the MP3's
loopback level following; the MUSIC slider (MCI_SETAUDIO) moved the music only.  Windows also remembers the
per-application level between runs and nothing re-applied it at start-up.

How (4 code edits + 58 .reloc entries -> type 0, on the untouched exe, independent of every other fix):
  1. The 632 bytes `0x004525E0..0x00452858` (nothing but set_volume's method-1 branch reaches them) become the fix's
     module (tools/volume_asm.py, 411 bytes, position-independent: every global, import slot and table is reached
     through a base register loaded with `call next ; pop`, so the old code's 58 HIGHLOW .reloc entries become type 0
     and no new entry is needed):
       +0x000 sfx_set (ecx = level 0..10):  cache = level + 1 (`0x005337CC`, the walk's dead "control minimum");
              unless sound is off (`0x00489750`), every voice of every loaded sample (table `0x004DFF30`, 200 entries
              of 116 bytes: +0 voices, +0x44 sound2.dat volume, +0x48 loaded, +0x4C buffers) gets SetVolume(volume +
              attenuation) through setvol - the looping menu hum and the sounds of a running battle change at once;
              then the game's Windows per-application volume is set back to its maximum on every mixer with a
              WaveOut line (the walk the old code did, with the control's lMaximum instead of the level), so a player
              whose game an older build left quiet in the Volume Mixer gets it back with one press; returns eax = 0,
              which set_volume takes as "no mixer found" and returns.  Every other register is preserved.
       +0x143 setvol (ebx = IDirectSoundBuffer*, edx = volume in hundredths of a dB):  adds the attenuation of the
              current level - the cache, or the saved level `0x00488E0C` (default 5) while nothing was pressed in this
              run - clamps at DSBVOLUME_MIN (-10000) and calls IDirectSoundBuffer::SetVolume; eax = its HRESULT.
       +0x185 the attenuation per level (words, hundredths of a dB): 36*log10(level/10) = the taper of the Windows
              per-application volume the old slider set (measured: 0.5 = -10.8 dB, 0.2 = -25.8 dB); level 0 = silence.
  2. The sample engine's three SetVolume sites call setvol instead of the interface directly, so every effect is
     played at sound2.dat's volume plus the attenuation:
       play         `0x00431006`  mov edx,[ebp-4] ; push edx ; mov eax,[ebx] ; push ebx ; call [eax+3Ch]
                                  -> mov edx,[ebp-4] ; call setvol ; nop ; nop
       play w/ pan  `0x00431257`  push edi ; mov eax,[ebx] ; push ebx ; call [eax+3Ch]  -> mov edx,edi ; call setvol
       voice adjust `0x00431374`  push edi ; mov eax,[ebx] ; push ebx ; call [eax+3Ch]  -> mov edx,edi ; call setvol
     (edx holds the resolved volume at the first site: the caller's value, or sound2.dat's when the caller passed 1.)
  set_volume itself is untouched: `call 0x004525E0 ; test eax,eax ; je exit` at `0x00452887` is checked to be there.
  The MUSIC slider (fix music's MCI_SETAUDIO) is not involved.  Watcom register convention throughout.

CLI
    python patch_volume.py verify EXE
    python patch_volume.py plan   EXE
    python patch_volume.py apply  EXE        (writes EXE.volume.bak first)
"""

import argparse
import hashlib
import shutil
import struct
import sys

IMAGE_BASE = 0x400000
AUTO_SIZE_OF = {0x7E200: 'classic', 0x7E400: 'cw'}      # raw size of the code section, same in the `.dcicon` builds

MODULE_VA = 0x004525E0
MODULE_LEN = 0x278                                       # ..0x00452858 = set_volume
STOCK_MODULE_SHA = 'ddf4ea86b91be8779f8a6208ee68b5b503b2eb1a6b6cdfc451b4a7b54067c32f'
SETVOL = 0x143
TABLE = 0x185
# tools/volume_asm.py, 7 Oct 2026 (listing there; the bytes are position-independent apart from the five rel32 inside)
MODULE_HEX = (
    '60e8000000005d83ed068d41018985ec110e0083bd7071030000752d31f68dbd50d90800807f480175130fb60fe30e8b5c8f488b5744e808010000e2f2'
    '83c7744681fec800000072dbff95a4df020089c64e0f88e70000006a006a006a00568d85e8110e0050ff95a8df020085c075e18dbd10110e00c707a800'
    '0000c74718081000006a0357ffb5e8110e00ff95a0df020085c00f85940000008dbdb8110e00c707180000008b851c110e00894704c7470801000350c7'
    '470c01000000c74710940000008d85f4110e008947146a0257ffb5e8110e00ff959cdf020085c0754e8dbdd0110e00c707180000008b85f8110e008947'
    '04c7470801000000c7470c00000000c74710040000008d85f0110e008947148b855c120e008985f0110e006a0057ffb5e8110e00ff95acdf0200ffb5e8'
    '110e00ff9594df0200e912ffffff6131c0c35152e800000000598b81a2100e0085c075078b81e2660300404883f80a7605b80a0000000fbf44413b01c2'
    '81faf0d8ffff7d05baf0d8ffff528b0353ff503c5a59c3f0d8f0f12bf6a5f867fac4fbe1fcd2fda3fe5bff0000')
MODULE = bytes.fromhex(MODULE_HEX)
assert len(MODULE) == 411 and MODULE[SETVOL:SETVOL + 3] == b'\x51\x52\xE8' and MODULE[TABLE:TABLE + 2] == struct.pack('<h', -10000)
NEW_MODULE = MODULE + b'\0' * (MODULE_LEN - len(MODULE))

# the three SetVolume sites of the sample engine: (name, bytes before the site, site length, bytes after it, stock site bytes)
SITES = [
    ('play', bytes.fromhex('83F801750C8B45F88B8074FF4D008945FC'), 10, bytes.fromhex('85C00F85'), bytes.fromhex('8B55FC528B0353FF503C')),
    ('play with pan', bytes.fromhex('83FF0175098B7DF88BBF74FF4D00'), 7, bytes.fromhex('85C00F85'), bytes.fromhex('578B0353FF503C')),
    ('voice adjust', bytes.fromhex('F645FC017414'), 7, bytes.fromhex('85C0750956'), bytes.fromhex('578B0353FF503C')),
]
SET_VOLUME_BRANCH = bytes.fromhex('E854FDFFFF85C0743B')   # set_volume method 1: call 0x4525E0 ; test eax,eax ; je exit  (at 0x452887)


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


def rel32(from_va, to_va):
    return struct.pack('<i', to_va - (from_va + 5))


def find_all(data, pattern, lo, hi):
    hits, i = [], data.find(pattern, lo)
    while 0 <= i < hi:
        hits.append(i)
        i = data.find(pattern, i + 1)
    return hits


def unique(data, pattern, what, lo, hi, after=(0, b'')):
    """The one hit of `pattern` in [lo, hi) that is followed, `after[0]` bytes further on, by `after[1]`."""
    hits = [h for h in find_all(data, pattern, lo, hi) if data[h + len(pattern) + after[0]:h + len(pattern) + after[0] + len(after[1])] == after[1]]
    if len(hits) != 1:
        raise SystemExit('expected exactly one %s, found %d: %s' % (what, len(hits), ', '.join('%#x' % h for h in hits)))
    return hits[0]


def relocs(data, page_rva, lo, hi):
    """[(file position, entry)] of the .reloc entries of one page whose offset is in [lo, hi)."""
    rva, rsize, rptr = sections(data)['.reloc']
    out, i = [], 0
    while i + 8 <= rsize:
        page, blk = struct.unpack_from('<II', data, rptr + i)
        if blk == 0:
            break
        if page == page_rva:
            for j in range(8, blk, 2):
                e = struct.unpack_from('<H', data, rptr + i + j)[0]
                if e and lo <= (e & 0xFFF) < hi:
                    out.append((rptr + i + j, e))
        i += blk
    return out


def new_site(k, site_va):
    """The new bytes of site k (its call goes to setvol)."""
    if k == 0:
        return b'\x8B\x55\xFC' + b'\xE8' + rel32(site_va + 3, MODULE_VA + SETVOL) + b'\x90\x90'
    return b'\x89\xFA' + b'\xE8' + rel32(site_va + 2, MODULE_VA + SETVOL)


def analyse(data):
    secs = sections(data)
    ava, arsize, arptr = secs['AUTO']
    if AUTO_SIZE_OF.get(arsize) != 'cw':
        raise SystemExit('this fix is for Dark Colony Ultimate (the Council Wars exe) only; AUTO section of %#x bytes' % arsize)
    lo, hi = arptr, arptr + arsize

    def va_of(off):
        return off - arptr + ava

    def off_of(va):
        return va - ava + arptr

    # set_volume's branch must call the module's first byte and take eax = 0 as "done"
    sv = unique(data, SET_VOLUME_BRANCH, 'set_volume method-1 branch (call 0x4525E0 ; test eax,eax ; je)', lo, hi)
    if va_of(sv) + 5 + struct.unpack_from('<i', data, sv + 1)[0] != MODULE_VA:
        raise SystemExit('set_volume at %#x does not call %#x' % (va_of(sv), MODULE_VA))
    mod = off_of(MODULE_VA)
    cur = bytes(data[mod:mod + MODULE_LEN])
    if hashlib.sha256(cur).hexdigest() == STOCK_MODULE_SHA:
        state = 'stock'
    elif cur == NEW_MODULE:
        state = 'patched'
    else:
        raise SystemExit('the bytes at %#x are neither the stock mixer walk nor this fix\'s module' % MODULE_VA)
    edits = []
    for k, (name, before, n, after, stock) in enumerate(SITES):
        site = unique(data, before, name + ' SetVolume site (its context)', lo, hi, (n, after)) + len(before)
        site_va = va_of(site)
        new = new_site(k, site_va)
        have = bytes(data[site:site + n])
        if state == 'stock' and have != stock or state == 'patched' and have != new:
            raise SystemExit('%s at %#x: unexpected bytes %s' % (name, site_va, have.hex(' ')))
        edits.append((site, have, new, '%s: SetVolume of the voice, VA 0x%08X: %s -> %s' % (name, site_va,
                      'push the volume, call IDirectSoundBuffer::SetVolume' if k else 'edx = the resolved volume, push it, call IDirectSoundBuffer::SetVolume',
                      'edx = the volume, call setvol 0x%08X (adds the SOUND level\'s attenuation, clamps at -10000, SetVolume)%s' % (MODULE_VA + SETVOL, '' if k else ', 2 nops'))))
    edits.append((mod, cur, NEW_MODULE,
                  'set_volume method 1: VA 0x%08X, the legacy-mixer walk + mixerSetControlDetails/mixerClose wrappers (632 bytes; set the WaveOut line = the game\'s Windows per-application volume, which scales the music too) -> the fix\'s module (411 bytes, position-independent): sfx_set (cache = level+1 at 0x%08X; SetVolume(sound2.dat volume + attenuation) on every voice of every loaded sample; the Windows per-application volume back to its maximum; eax = 0 so set_volume returns), setvol at +0x%X (volume + attenuation of the cached or saved level 0x%08X, clamp -10000, SetVolume), the 11-word attenuation table at +0x%X (36*log10(level/10) in hundredths of a dB, level 0 = silence)'
                  % (MODULE_VA, 0x5337CC, SETVOL, 0x488E0C, TABLE)))
    # the .reloc entries of the old code's absolute operands -> type 0 (the module has none)
    page = (MODULE_VA - IMAGE_BASE) & ~0xFFF
    rel = []
    for pos, e in relocs(data, page, MODULE_VA & 0xFFF, (MODULE_VA & 0xFFF) + MODULE_LEN):
        if state == 'stock':
            if e >> 12 != 3:
                raise SystemExit('.reloc entry %04X at file %#x is not HIGHLOW' % (e, pos))
            rel.append((pos, e, 0, 'the old mixer walk\'s absolute operand at page offset 0x%03X (now position-independent code)' % (e & 0xFFF)))
    if state == 'stock' and len(rel) != 58:
        raise SystemExit('expected 58 HIGHLOW .reloc entries in the mixer walk, found %d' % len(rel))
    if state == 'patched' and rel:
        raise SystemExit('the module is ours but %d .reloc entries still point into it' % len(rel))
    return dict(state=state, edits=edits, relocs=rel, va_of=va_of, sv_va=va_of(sv))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    r = analyse(data)
    print('%s: Council Wars build, %s; module VA 0x%08X (setvol +0x%X, table +0x%X), set_volume branch VA 0x%08X, %d .reloc entries'
          % (a.exe, r['state'], MODULE_VA, SETVOL, TABLE, r['sv_va'], len(r['relocs'])))
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
    bak = a.exe + '.volume.bak'
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

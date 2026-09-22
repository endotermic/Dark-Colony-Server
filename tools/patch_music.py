#!/usr/bin/env python3
"""Original CD soundtrack from MP3 files (fix `music`, both games): the cdaudio module plays MUSIC\\TRACK02.MP3 ...

Background (DC16_DISPLAY_AND_RESOLUTION.md 10.31).  Both game CDs are mixed-mode discs: track 1 data,
tracks 2-5 Red Book audio.  The exe plays them through a plain MCI `cdaudio` library (Classic
0x004510D0..0x00451820, Council Wars +0x60): at battle start `seek track 2 + play to the end of the
disc`, a poll every 5 s that starts over at track 2 when the disc has stopped, `stop` at battle end.
Without a CD-ROM drive `MCI_OPEN cdaudio` fails at start-up and the game is silent for good; the
music slider of the options screen drives an aux (CD-audio line) volume that modern Windows no
longer has.  The per-mission playlists of the scene lists are parsed but never used by that code,
and their numbers do not match the shipped discs (track 1 = data, "7"), so they stay ignored here.

Fix: the whole cdaudio module (0x751 bytes, the seven entry points the ddex4.c music layer calls
stay at their addresses) is rewritten as an MP3 player on the MCI `mpegvideo` device (mciqtz32.dll,
DirectShow - ships with every Windows since 98; the game's own `mciSendCommandA` import, nothing
new is imported):

    cd_open()              +0x000  reads the saved music level (0..10) into the volume state, opens
                                   MUSIC\\TRACK02.MP3 (not playing); 0 = no file -> the music layer
                                   sets its "no CD audio" flag and never calls again
    cd_play_from_here(dev) +0x03C  no-op (a track plays as soon as it is opened by cd_seek_track)
    cd_close(dev)          +0x088  MCI_CLOSE of the open element
    cd_stop(dev)           +0x0B8  MCI_STOP
    cd_tracks(dev)         +0x338  0 (the caller is dead code)
    cd_seek_track(dev, t)  +0x4E8  close, open MUSIC\\TRACK0<t>.MP3, MCI_PLAY (asynchronous, to the end)
    cd_mode(dev)           +0x6E0  MCI_STATUS mode: playing -> 0; MCI_MODE_STOP (file ended) -> open
                                   and play the next track, or TRACK02 again when the next file
                                   does not exist (that is the original "whole disc, repeat");
                                   no element / MCI error -> 4 (the layer then closes and re-opens)
    helpers                +0x0E0 close_state, +0x110 open_track, +0x1A0 play_dev, +0x200 setvol_dev,
                           +0x240 impl_mode; strings "mpegvideo" +0x1D0 and the path template +0x1DC

The state (open device id, current track, volume 0..1000, the built file name) lives in the
.bss array the old module filled with the disc's table of contents (Classic 0x005327D0, 60 bytes,
read by nothing else).  The options screen's music slider (`set_volume(level, 0)` -> the aux walk
at 0x00452870 / CW 0x004528D0, 110 bytes) now stores level*100 and sends MCI_SETAUDIO volume to
the open element; the level is re-applied to every track opened.  Dark Colony reads
music\\track0N.mp3, Council Wars exp\\music\\track0N.mp3 - both exes share one folder and the two
discs differ (the repository ships the eight tracks encoded from the CD images at 192 kbit/s).

Every absolute operand of the new code takes over a HIGHLOW `.reloc` entry of the old code in the
same page (22 + 2), the remaining old entries become type 0 padding; nothing moves.  Council Wars
offsets are found by pattern (module +0x60; the same .bss array, the same IAT slot).

CLI
    python patch_music.py verify EXE
    python patch_music.py plan   EXE
    python patch_music.py apply  EXE        (writes EXE.music.bak first)
"""

import argparse
import hashlib
import re
import shutil
import struct
import sys

SIZE_OF = {659456: 'classic', 659968: 'cw'}
IMAGE_BASE = 0x400000
AUTO_VA_TO_FILE = 0x400C00
MODULE_LEN = 0x751          # cd_open .. the `ret` of the mode mapper (0x004510D0..0x00451820 inclusive)
AUX_LEN = 0x6E              # the aux-device volume walk (0x00452870..0x004528DD), followed by the entry-point jmp
TEMPLATE = {'classic': b'music\\track0?.mp3', 'cw': b'exp\\music\\track0?.mp3'}
DIGIT = {'classic': 12, 'cw': 16}                     # index of the '?' in the template
L_TEMPLATE, L_MPEGVIDEO, L_SETVOL = 0x1DC, 0x1D0, 0x200
STOCK_SHA = {                                         # SHA-256 of the stock module block and the stock aux block
    'classic': ('2491a5c0b49320c2bec5d2d1d5c35793194d4dc2de2bf5e9c0ad97705c2136fa', '83fbcd84aa92f7d8ca5b8ac076336ca3244b1cf58ab8b559c4fe6cd003238b60'),
    'cw': ('4e1758538d79d5de5f7d4c9e1c79a837511c4dbe4ed9c1227399757b40534796', '83fbcd84aa92f7d8ca5b8ac076336ca3244b1cf58ab8b559c4fe6cd003238b60'),
}

# The new module, assembled (keystone, dev-time only) for the Classic template with placeholder
# operands: 0x0A0B0Cxx = state block + xx, 0x0A0B0D00 = IAT slot of mciSendCommandA, 0x0A0B0E00 =
# the saved music level, 0x0A0Cxxxx = module base + xxxx.  FIXUPS lists (offset, kind, addend).
CODE = bytes.fromhex('e8db000000a1000e0b0a6bc064a3080c0b0ab802000000e8f4000000c30000000000000000000000000000000000000000000000000000000000000031c0c300000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000eb56000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000005152a1000c0b0a85c074106a006a00680808000050ff15000d0b0a31c05a59c300000000000000005152a1000c0b0a85c074176a006a00680408000050ff15000d0b0a31c0a3000c0b0a5a59c30000000000000000000000515256575589e583ec1489c2bedc010c0abf0c0c0b0ab906000000f3a50430a2180c0b0a8915040c0b0a31c08945ec8945f0c745f4d0010c0ac745f80c0c0b0a8945fc8d45ec50680222000068030800006a00ff15000d0b0a85c0751a8b45f0a3000c0b0a8b0d080c0b0ae880000000a1000c0b0aeb0231c089ec5d5f5e5a59c300000000000000000000000000000051525589e583ec0c31d28955f48955f88955fc8d55f4526a00680608000050ff15000d0b0a89ec5d5a59c3000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000051525589e583ec1831d28955e8c745ec02400000894df08955f48955f88955fc8d55e8526800008001687308000050ff15000d0b0a89ec5d5a59c3000000000051525589e583ec10a1000c0b0a85c0746631d28955f08955f4c745f8040000008955fc8d55f0526800010000681408000050ff15000d0b0a85c0753b817df40d020000752a8b15040c0b0a42e84ffeffff89d0e878feffff85c0750eb802000000e86afeffff85c0740de8f1feffff31c089ec5d5a59c3b804000000ebf3000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000031c0c300000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000052e8f2fbffff0fb6c2e81afcffff85c0740be8a1fcffff0fb6c2405ac331c05ac3000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000e95bfbffff0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000')
FIXUPS = [(6, 'VOL', 0), (14, 'STATE', 8), (187, 'STATE', 0), (207, 'IAT', 0), (227, 'STATE', 0), (247, 'IAT', 0), (254, 'STATE', 0), (285, 'MOD', 476), (290, 'STATE', 12), (304, 'DIGIT', 0), (310, 'STATE', 4), (325, 'MOD', 464), (332, 'STATE', 12), (357, 'IAT', 0), (369, 'STATE', 0), (375, 'STATE', 8), (385, 'STATE', 0), (449, 'IAT', 0), (561, 'IAT', 0), (585, 'STATE', 0), (628, 'IAT', 0), (647, 'STATE', 4)]
# the volume routine written over the aux walk: eax = level 0..10
#   push ecx ; imul ecx,eax,100 ; mov [state+8],ecx ; mov eax,[state] ; test eax,eax ; jz done ;
#   call setvol_dev ; done: pop ecx ; ret
AUX_CODE = bytes.fromhex('51 6b c8 64 89 0d 00 00 00 00 a1 00 00 00 00 85 c0 74 05 e8 00 00 00 00 59 c3'.replace(' ', ''))
AUX_STATE8, AUX_STATE, AUX_CALL = 6, 11, 19

# the ddex4.c music layer's `music_open`: push ebx/ecx/ebp ; mov ebp,esp ; call cd_open ; and eax,0FFFFh ;
# mov [dev],eax ; jne ok ; mov ebx,1 ; mov ecx,-1 ; ...   (intact in every build; the call target = the module)
MUSIC_OPEN_PATTERN = '53 51 55 89 E5 E8 ?? ?? ?? ?? 25 FF FF 00 00 A3 ?? ?? ?? ?? 75 16 BB 01 00 00 00 B9 FF FF FF FF'
# set_volume(level, method): ... ; mov ecx,eax ; mov eax,edx ; cmp ecx,0Ah ; jle ; mov ecx,0Ah ; jmp ; test ecx,ecx ; jge ;
# xor ecx,ecx ; test eax,eax ; jne mixer ; mov eax,ecx ; call aux_walk
SET_VOLUME_PATTERN = '53 51 55 89 E5 89 C1 89 D0 83 F9 0A 7E 07 B9 0A 00 00 00 EB 06 85 C9 7D 02 31 C9 85 C0 75 0B 89 C8 E8 ?? ?? ?? ??'
# stock cd_read_toc: mov [edi+toc],al ; xor eax,eax ; mov ax,[ebp-1Ch] ; sar eax,8 ; mov [edi+toc+1],al
TOC_STORE_PATTERN = '88 87 ?? ?? ?? ?? 31 C0 66 8B 45 E4 C1 F8 08 88 87'
# main.c start-up: mov eax,[music level] ; mov [campaign+1988h],eax  (the options screen's music slider edits that copy)
VOL_PATTERN = 'A1 ?? ?? ?? ?? 89 82 88 19 00 00'
# stock first bytes of the seven entry points (layout check before the rewrite)
STOCK_ENTRIES = {0x000: '51 52 55 89 E5 83 EC 14 8D 45 EC 50 68 00 21 00 00 68 03 08 00 00',
                 0x03C: '53 51 52 55 89 E5 83 EC 18 8D 5D F4 53 68 00 04 00 00',
                 0x088: '51 52 55 89 E5 6A 00 6A 00 68 04 08 00 00',
                 0x0B8: '51 52 55 89 E5 83 EC 04 8D 55 FC 52 6A 00 68 08 08 00 00',
                 0x338: '51 52 55 89 E5 83 EC 10 C7 45 F8 03 00 00 00',
                 0x4E8: '53 51 56 55 89 E5 83 EC 20 88 D3',
                 0x6E0: '51 52 55 89 E5 83 EC 10 C7 45 F8 04 00 00 00',
                 0x750: 'C3 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 53 51 52 56 57 55 89 E5 81 EC 00 03 00 00'}
STOCK_AUX_HEAD = '53 51 52 56 57 55 89 E5 83 EC 34 89 C7 2E FF 15 ?? ?? ?? ?? 89 45 FC 31 F6 3B 75 FC 73 47'


def pat(s):
    return b''.join(b'.' if t == '??' else re.escape(bytes([int(t, 16)])) for t in s.split())


class Image:
    def __init__(self, data):
        self.data = data
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        nsec = struct.unpack_from('<H', data, pe + 6)[0]
        opt = struct.unpack_from('<H', data, pe + 20)[0]
        st = pe + 24 + opt
        self.secs = {}
        for i in range(nsec):
            s = data[st + i * 40: st + i * 40 + 40]
            vsize, rva, rsize, rptr = struct.unpack_from('<IIII', s, 8)
            self.secs[s[:8].rstrip(b'\0').decode()] = (rva, rsize, rptr)
        rva, rsize, rptr = self.secs['AUTO']
        self.auto = bytes(data[rptr:rptr + rsize])
        self.auto_va = IMAGE_BASE + rva
        self.import_dir = struct.unpack_from('<II', data, pe + 24 + 96 + 8)   # rva, size

    def find(self, p, what):
        hits = [m.start() for m in re.finditer(pat(p), self.auto, re.S)]
        if len(hits) != 1:
            raise SystemExit('%s: expected one site, found %d' % (what, len(hits)))
        return self.auto_va + hits[0]

    def matches(self, va, p):
        return re.match(pat(p), self.auto[va - self.auto_va:va - self.auto_va + len(p.split())], re.S) is not None

    def rva2file(self, rva):
        for name, (srva, rsize, rptr) in self.secs.items():
            if srva <= rva < srva + rsize:
                return rptr + rva - srva
        raise SystemExit('rva %#x outside the file' % rva)

    def iat_of(self, dll, func):
        """VA of the IAT slot of an imported function (walks the import directory)."""
        d = self.data
        p = self.rva2file(self.import_dir[0])
        while True:
            int_rva, _, _, name_rva, iat_rva = struct.unpack_from('<IIIII', d, p)
            if int_rva == 0 and iat_rva == 0:
                break
            name = d[self.rva2file(name_rva):].split(b'\0')[0].decode()
            if name.lower() == dll.lower():
                q = self.rva2file(int_rva)
                i = 0
                while True:
                    e = struct.unpack_from('<I', d, q + 4 * i)[0]
                    if e == 0:
                        break
                    if not e & 0x80000000 and d[self.rva2file(e) + 2:].split(b'\0')[0].decode() == func:
                        return IMAGE_BASE + iat_rva + 4 * i
                    i += 1
            p += 20
        raise SystemExit('import %s!%s not found' % (dll, func))

    def relocs(self, page_rva, lo, hi):
        """[(file position, entry)] of the HIGHLOW entries of one page whose offset is in [lo, hi)."""
        rva, rsize, rptr = self.secs['.reloc']
        out, i = [], 0
        while i + 8 <= rsize:
            page, blk = struct.unpack_from('<II', self.data, rptr + i)
            if blk == 0:
                break
            if page == page_rva:
                for j in range(8, blk, 2):
                    e = struct.unpack_from('<H', self.data, rptr + i + j)[0]
                    if (e >> 12) == 3 and lo <= (e & 0xFFF) < hi:
                        out.append((rptr + i + j, e))
            i += blk
        return out


def va2file(va):
    return va - AUTO_VA_TO_FILE


def build_module(g, mod_va, state, iat, vol):
    code = bytearray(CODE)
    for off, kind, add in FIXUPS:
        if kind == 'STATE':
            v = state + add
        elif kind == 'DIGIT':
            v = state + 12 + DIGIT[g]
        elif kind == 'IAT':
            v = iat
        elif kind == 'VOL':
            v = vol
        elif kind == 'MOD':
            v = mod_va + add
        else:
            raise AssertionError(kind)
        struct.pack_into('<I', code, off, v)
    code[L_MPEGVIDEO:L_MPEGVIDEO + 10] = b'mpegvideo\0'
    t = TEMPLATE[g]
    assert t[DIGIT[g]:DIGIT[g] + 1] == b'?' and len(t) < 24
    code[L_TEMPLATE:L_TEMPLATE + 24] = t + b'\0' * (24 - len(t))
    code += b'\0' * (MODULE_LEN - len(code))
    return bytes(code)


def build_aux(aux_va, mod_va, state):
    code = bytearray(AUX_CODE)
    struct.pack_into('<I', code, AUX_STATE8, state + 8)
    struct.pack_into('<I', code, AUX_STATE, state)
    struct.pack_into('<i', code, AUX_CALL + 1, (mod_va + L_SETVOL) - (aux_va + AUX_CALL + 5))
    return bytes(code) + b'\0' * (AUX_LEN - len(code))


def sites_for(img, g):
    """Return (sites, relocs, info): sites = [(note, file_off, stock_sha, new_bytes)], relocs = [(pos, old, new)]."""
    d = img.data
    mo = img.find(MUSIC_OPEN_PATTERN, 'ddex4.c music_open')
    mod_va = mo + 10 + struct.unpack_from('<i', d, va2file(mo) + 6)[0]
    sv = img.find(SET_VOLUME_PATTERN, 'set_volume')
    aux_va = sv + 38 + struct.unpack_from('<i', d, va2file(sv) + 34)[0]
    iat = img.iat_of('WINMM.dll', 'mciSendCommandA')
    vol_site = img.find(VOL_PATTERN, 'music level start-up copy')
    vol = struct.unpack_from('<I', d, va2file(vol_site) + 1)[0]
    mod_file, aux_file = va2file(mod_va), va2file(aux_va)
    cur_mod, cur_aux = bytes(d[mod_file:mod_file + MODULE_LEN]), bytes(d[aux_file:aux_file + AUX_LEN])
    stock = all(img.matches(mod_va + o, p) for o, p in STOCK_ENTRIES.items()) and img.matches(aux_va, STOCK_AUX_HEAD)
    if stock:
        toc = img.find(TOC_STORE_PATTERN, 'cd_read_toc store')
        if not mod_va <= toc < mod_va + MODULE_LEN:
            raise SystemExit('cd_read_toc store outside the module')
        state = struct.unpack_from('<I', d, va2file(toc) + 2)[0]
    else:
        # patched (or foreign): the state block address sits in cd_open's `mov [state+8],eax`
        off = next(o for o, k, a in FIXUPS if k == 'STATE' and a == 8)
        state = struct.unpack_from('<I', d, mod_file + off)[0] - 8
    if not (d[aux_file + AUX_LEN] == 0xE9):
        raise SystemExit('entry-point jmp not found after the aux walk')
    new_mod = build_module(g, mod_va, state, iat, vol)
    new_aux = build_aux(aux_va, mod_va, state)
    sites = [('cdaudio module -> MP3 player on MCI mpegvideo (seven entry points kept: cd_open +0, play_from_here +0x3C, close +0x88, stop +0xB8, tracks +0x338, seek_track +0x4E8, mode +0x6E0; helpers, "mpegvideo" and the path template "%s" inside; the rest zero)' % TEMPLATE[g].decode().replace('\\', '\\\\'),
              mod_file, STOCK_SHA[g][0], new_mod),
             ('set_volume method 0: aux CD-audio device walk -> store level*100, MCI_SETAUDIO volume on the open element (the rest zero)',
              aux_file, STOCK_SHA[g][1], new_aux)]
    # .reloc: the old absolute operands inside the two blocks -> the new ones (same page), the rest -> type 0
    relocs = []
    for base_va, length, new_offs in ((mod_va, MODULE_LEN, sorted(o for o, k, a in FIXUPS)),
                                      (aux_va, AUX_LEN, [AUX_STATE8, AUX_STATE])):
        page = (base_va - IMAGE_BASE) & ~0xFFF
        lo = base_va & 0xFFF
        old = img.relocs(page, lo, lo + length)
        new_vals = [(3 << 12) | (lo + o) for o in new_offs]
        assert all(lo + o < 0x1000 for o in new_offs)
        olds = sorted(e for _, e in old)
        if all(e in olds for e in new_vals) and len(old) == len(new_vals):
            # already patched: reconstruct the stock entries from the code?  Not possible; report as patched
            relocs += [(pos, e, e) for pos, e in old]
            continue
        if len(old) < len(new_vals):
            raise SystemExit('page %#x: %d .reloc entries in the block, %d needed' % (page, len(old), len(new_vals)))
        for k, (pos, e) in enumerate(sorted(old, key=lambda x: x[1])):
            relocs.append((pos, e, new_vals[k] if k < len(new_vals) else 0))
    info = dict(mod_va=mod_va, aux_va=aux_va, state=state, iat=iat, vol=vol)
    return sites, relocs, info


def state_of(data, sites, relocs):
    st = []
    for note, off, stock_sha, new in sites:
        cur = bytes(data[off:off + len(new)])
        st.append('patched' if cur == new else 'stock' if hashlib.sha256(cur).hexdigest() == stock_sha else 'other')
    for off, old, new in relocs:
        e = struct.unpack_from('<H', data, off)[0]
        st.append('patched' if e == new else 'stock' if e == old else 'other')
    return st


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    if len(data) not in SIZE_OF:
        raise SystemExit('%s: %d bytes - not a dc16.exe (659456) or ENGEXP16.EXE (659968) build' % (a.exe, len(data)))
    g = SIZE_OF[len(data)]
    img = Image(data)
    sites, relocs, info = sites_for(img, g)
    st = state_of(data, sites, relocs)
    summary = ('stock' if all(s == 'stock' for s in st) else 'patched' if all(s == 'patched' for s in st)
               else 'partially patched (%d of %d sites)' % (st.count('patched'), len(st)))
    if 'other' in st:
        raise SystemExit('%s: unexpected bytes at %d site(s): %s' % (a.exe, st.count('other'),
                         ', '.join(sites[i][0][:40] if i < len(sites) else '.reloc' for i, s in enumerate(st) if s == 'other')))
    print('%s: music %s (%d code blocks, %d .reloc entries; module VA %#x, aux walk VA %#x, state %#x, IAT %#x, level %#x)'
          % (a.exe, summary, len(sites), len(relocs), info['mod_va'], info['aux_va'], info['state'], info['iat'], info['vol']))
    if a.command == 'verify':
        return 0
    for note, off, stock_sha, new in sites:
        print('  %s  file 0x%x VA 0x%x %d bytes' % (note, off, off + AUTO_VA_TO_FILE, len(new)))
    for off, old, new in relocs:
        print('  .reloc @ file %#x: %04X -> %04X' % (off, old, new))
    if a.command == 'plan':
        return 0
    if summary == 'patched':
        print('nothing to do')
        return 0
    bak = a.exe + '.music.bak'
    shutil.copyfile(a.exe, bak)
    for (note, off, stock_sha, new), s in zip(sites, st):
        if s == 'stock':
            data[off:off + len(new)] = new
    for (off, old, new), s in zip(relocs, st[len(sites):]):
        if s == 'stock':
            struct.pack_into('<H', data, off, new)
    open(a.exe, 'wb').write(data)
    print('written %s (%d code blocks, %d .reloc entries); backup %s' % (a.exe, len(sites), len(relocs), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

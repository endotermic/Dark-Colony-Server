#!/usr/bin/env python3
"""Add an "OZI MISSIONS" mode to Council Wars' DCEXP16.EXE (ozi_ns mission pack, 2010).

Council Wars reads every data file through one overlay helper (0x4063E4): it first tries the
name with the prefix stored at 0x4826D0 ("exp/") and falls back to the bare name in the game
root.  The wave loader (0x452AB0) has its own copy of the prefix at 0x487DC8, and the save
folder name "esave" sits in two slots (0x482344 for the LOAD GAME screen, 0x485E5C for the
in-game dialog).  All four are plain writable strings in DGROUP, 8 bytes each, so a campaign
mode is nothing more than the content of those four slots:

    Council Wars  exp/      exp/      esave     esave
    OZI missions  ozi_ns/   ozi_ns/   ozisave   ozisave

Balance tables, unit list, sound assignments, scene lists, missions, terrains and story texts
are (re)loaded per game, so switching the slots in the main menu is enough for them.  Only the
animation list, the FIN/SPR banks and the 200-entry sound table are loaded once at start-up
(0x405264 -> 0x4051CC -> 0x42565C), which is why the pack's new units live in exp/ instead of
the overlay (see tools/build_ozi_overlay.py).

Five code edits, all in the main-menu function 0x404DC8 (doc DC16_DISPLAY_AND_RESOLUTION.md
section 10.13); the menu script rows come from tools/build_ozi_overlay.py:

1. The PLAY INTRO handler (button id 0x10, 0x4050DD..0x40513C, 96 bytes) becomes the
   OZI MISSIONS handler: it sets the two campaign flags exactly like NEW CAMPAIGN does
   (gs+0x14F4 = 1 "expansion scenes", gs+0x14F0 = 0 "campaign"), calls `stub_pack` to write the
   pack strings into the four slots and enters the campaign runner 0x401C08; the rest of the
   old handler is NOP padding.
2. NEW CAMPAIGN and TRAINING reach the campaign runner through `call 0x401C08` at 0x405065 and
   0x405083; both calls go through `tramp_cw_campaign` (Council Wars strings, then 0x401C08).
3. LOAD GAME (button id 2, `call 0x403AA4` at 0x4050BF) goes through `tramp_cw_load`: it
   always lists and loads Council Wars saves (`esave`).
4. SINGLE PLAYER WAR (button id 4, `call 0x405AE4` at 0x4050AB, its only caller) becomes
   OZI LOAD through `tramp_pack_load`: pack strings, then the same load routine, so it always
   lists `ozisave`.  Both load buttons are therefore deterministic from the first screen; the
   mode is still sticky for everything else (mission continuation 0x405165 and the post-load
   call 0x403B29 stay direct).
5. Code lives in the zero tail of AUTO after the cursor stub: `stub_pack` 0x47F240 and
   `stub_cw_set` 0x47F290 (73 bytes each: `push eax/edi`, four slot writes with
   `mov edi,imm32; mov eax,imm32; stosd`, `pop; ret`), then the three 10-byte trampolines
   (`call stub; jmp target`) at 0x47F2E0 / 0x47F2F0 / 0x47F300.  Only the eight `mov edi,imm32`
   operands are absolute, so eight HIGHLOW entries are appended to the .reloc block of page
   0x7F000 (the table grows by 16 bytes into its 52 bytes of slack and the base-relocation
   directory size follows); the two operands that vanish with the old PLAY INTRO handler
   (0x4050DE -> .bss, 0x405103 -> DGROUP) become IMAGE_REL_BASED_ABSOLUTE padding.

Version 1 of this patch (10 Sep 2026, before OZI LOAD) had `stub_cw` tail-jump into 0x401C08
itself and left LOAD GAME direct; the tool recognises a v1 exe and upgrades it in place.
Nothing is written unless every site holds its stock, v1 or v2 bytes.

CLI
    python patch_ozi_menu.py verify "DC - Council wars/DCEXP16.EXE"
    python patch_ozi_menu.py plan   "DC - Council wars/DCEXP16.EXE"
    python patch_ozi_menu.py apply  "DC - Council wars/DCEXP16.EXE"     (writes EXE.ozi.bak first)
"""

import argparse
import shutil
import struct
import sys

AUTO_VA_TO_FILE = 0x400C00      # AUTO at VA 0x401000 = file 0x400 (both builds)
DGROUP_VA_TO_FILE = 0x402600    # DCEXP16: DGROUP at VA 0x482000 = file 0x7FA00

HANDLER = 0x4050DD              # PLAY INTRO handler body (after `cmp edi,10h / jne`)
HANDLER_LEN = 0x60              # up to and including `call 00401028` at 0x405138
HANDLER_EXIT = 0x40513D         # between-mission loop shared by every campaign button
NEW_CAMPAIGN_CALLS = (0x405065, 0x405083)   # `call 00401C08` in the NEW CAMPAIGN / TRAINING handlers
LOAD_GAME_CALL = 0x4050BF       # `call 00403AA4` in the LOAD GAME handler (button id 2)
SINGLE_WAR_CALL = 0x4050AB      # `call 00405AE4` in the SINGLE PLAYER WAR handler (button id 4)
CAMPAIGN_RUNNER = 0x401C08
LOAD_GAME = 0x403AA4
SINGLE_WAR = 0x405AE4
STUB_PACK = 0x47F240
STUB_CW = 0x47F290
TRAMP_CW_CAMPAIGN = 0x47F2E0
TRAMP_CW_LOAD = 0x47F2F0
TRAMP_PACK_LOAD = 0x47F300
SLOTS = (0x4826D0, 0x487DC8, 0x482344, 0x485E5C)
CW_STRINGS = (b'exp/\0\0\0\0', b'exp/\0\0\0\0', b'esave\0\0\0', b'esave\0\0\0')
PACK_STRINGS = (b'ozi_ns/\0', b'ozi_ns/\0', b'ozisave\0', b'ozisave\0')
RELOC_PAGE_HANDLER = 0x5000     # page of the old handler: operands at 0x0DE and 0x103 vanish
RELOC_PAGE_TAIL = 0x7F000       # page of the stubs: eight new operands

STOCK_HANDLER = bytes.fromhex(
    'be f2 46 4a 00 8d bd f0 fe ff ff 57 8a 06 88 07 3c 00 74 10 8a 46 01 83 c6 02 88 47 01'
    '83 c7 02 3c 00 75 e8 5f be a8 24 48 00 8d bd f0 fe ff ff 8d 95 f0 fe ff ff 57 2b c9 49'
    'b0 00 f2 ae 4f 8a 06 88 07 3c 00 74 10 8a 46 01 83 c6 02 88 47 01 83 c7 02 3c 00 75 e8'
    '5f 8b 45 fc e8 eb be ff ff')


def rel32(src_end, target):
    return struct.pack('<i', target - src_end)


def slot_writer(strings):
    """Code that stores four 8-byte strings into SLOTS with edi/eax; returns (bytes, operand offsets)."""
    out, operands = bytearray(), []
    for va, s in zip(SLOTS, strings):
        assert len(s) == 8
        operands.append(len(out) + 1)
        out += b'\xBF' + struct.pack('<I', va)          # mov edi, slot
        out += b'\xB8' + s[:4] + b'\xAB'                # mov eax, imm32 / stosd
        out += b'\xB8' + s[4:] + b'\xAB'
    return bytes(out), operands


def call(src, target):
    return b'\xE8' + rel32(src + 5, target)


def jmp(src, target):
    return b'\xE9' + rel32(src + 5, target)


def tramp(va, stub, target):
    return call(va, stub) + jmp(va + 5, target)


def build():
    """Return ([(name, va, [accepted old bytes...], new)], {page: [(offset, new_u16)]}).

    The first accepted variant of every site is the stock code, the others are earlier versions
    of this patch; `new` is the current version."""
    body, ops = slot_writer(PACK_STRINGS)
    stub_pack = b'\x50\x57' + body + b'\x5F\x58\xC3'                 # push eax/edi ... pop/pop/ret
    pack_ops = [2 + o for o in ops]
    body, ops = slot_writer(CW_STRINGS)
    stub_cw_v1 = b'\x50\x57' + body + b'\x5F\x58'
    stub_cw_v1 += jmp(STUB_CW + len(stub_cw_v1), CAMPAIGN_RUNNER)  # v1: tail-jump into 401C08
    stub_cw = b'\x50\x57' + body + b'\x5F\x58\xC3' + bytes(4)        # v2: plain subroutine
    assert len(stub_cw) == len(stub_cw_v1) == 77
    cw_ops = [2 + o for o in ops]

    h = bytearray()
    h += bytes.fromhex('C7 80 F4 14 00 00 01 00 00 00')     # mov dword ptr [eax+14F4h],1
    h += bytes.fromhex('C7 80 F0 14 00 00 00 00 00 00')     # mov dword ptr [eax+14F0h],0
    h += bytes.fromhex('89 C2')                             # mov edx,eax        (gs)
    h += bytes.fromhex('8B 45 FC')                          # mov eax,[ebp-4]    (screen)
    h += b'\xE8' + rel32(HANDLER + len(h) + 5, STUB_PACK)   # call stub_pack
    h += b'\xE8' + rel32(HANDLER + len(h) + 5, CAMPAIGN_RUNNER)   # call 00401C08
    disp = HANDLER_EXIT - (HANDLER + len(h) + 2)
    assert 0 < disp < 0x80
    h += b'\xEB' + bytes([disp])                            # jmp 0040513D
    h += b'\x90' * (HANDLER_LEN - len(h))
    assert len(h) == HANDLER_LEN == len(STOCK_HANDLER)

    sites = [('OZI MISSIONS handler (was PLAY INTRO)', HANDLER, [STOCK_HANDLER], bytes(h)),
             ('stub_pack (pack strings into the 4 slots)', STUB_PACK, [bytes(len(stub_pack))], stub_pack),
             ('stub_cw_set (Council Wars strings)', STUB_CW, [bytes(len(stub_cw)), stub_cw_v1], stub_cw),
             ('tramp_cw_campaign (stub_cw_set; jmp 401C08)', TRAMP_CW_CAMPAIGN, [bytes(10)],
              tramp(TRAMP_CW_CAMPAIGN, STUB_CW, CAMPAIGN_RUNNER)),
             ('tramp_cw_load (stub_cw_set; jmp 403AA4)', TRAMP_CW_LOAD, [bytes(10)],
              tramp(TRAMP_CW_LOAD, STUB_CW, LOAD_GAME)),
             ('tramp_pack_load (stub_pack; jmp 403AA4)', TRAMP_PACK_LOAD, [bytes(10)],
              tramp(TRAMP_PACK_LOAD, STUB_PACK, LOAD_GAME))]
    for va in NEW_CAMPAIGN_CALLS:
        sites.append(('NEW CAMPAIGN/TRAINING call -> tramp_cw_campaign', va,
                      [call(va, CAMPAIGN_RUNNER), call(va, STUB_CW)], call(va, TRAMP_CW_CAMPAIGN)))
    sites.append(('LOAD GAME call -> tramp_cw_load', LOAD_GAME_CALL,
                  [call(LOAD_GAME_CALL, LOAD_GAME)], call(LOAD_GAME_CALL, TRAMP_CW_LOAD)))
    sites.append(('OZI LOAD (was SINGLE PLAYER WAR) call -> tramp_pack_load', SINGLE_WAR_CALL,
                  [call(SINGLE_WAR_CALL, SINGLE_WAR)], call(SINGLE_WAR_CALL, TRAMP_PACK_LOAD)))
    relocs = {
        RELOC_PAGE_HANDLER: [(0x0DE, 0), (0x103, 0)],
        RELOC_PAGE_TAIL: [((STUB_PACK + o) & 0xFFF, 3) for o in pack_ops]
                       + [((STUB_CW + o) & 0xFFF, 3) for o in cw_ops],
    }
    return sites, relocs


# ------------------------------------------------------------------ PE helpers
def pe_layout(data):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    optsize = struct.unpack_from('<H', data, pe + 20)[0]
    opt = pe + 24
    dir_reloc = opt + 96 + 5 * 8                    # IMAGE_DIRECTORY_ENTRY_BASERELOC
    st = opt + optsize
    secs = {}
    for i in range(nsec):
        s = data[st + i * 40: st + i * 40 + 40]
        vsize, va, rsize, rptr = struct.unpack_from('<IIII', s, 8)
        secs[s[:8].rstrip(b'\0').decode()] = (va, rsize, rptr)
    return dir_reloc, secs


def reloc_blocks(data):
    """[(page_rva, [u16 entries], file_off)] in file order, plus (rptr, rsize)."""
    dir_reloc, secs = pe_layout(data)
    va, rsize, rptr = secs['.reloc']
    pos, end, blocks = rptr, rptr + rsize, []
    while pos + 8 <= end:
        rva, size = struct.unpack_from('<II', data, pos)
        if size == 0:
            break
        n = (size - 8) // 2
        blocks.append((rva, list(struct.unpack_from('<%dH' % n, data, pos + 8)), pos))
        pos += size
    return blocks, rptr, rsize, dir_reloc


def reloc_state(data, relocs):
    """'stock' / 'patched' / 'PARTIAL' for the reloc edits."""
    blocks = {rva: ents for rva, ents, _ in reloc_blocks(data)[0]}
    want = done = 0
    for page, edits in relocs.items():
        ents = blocks[page]
        for off, typ in edits:
            want += 1
            if typ == 0:
                if not any(e == (0x3000 | off) for e in ents):
                    done += 1
            elif (0x3000 | off) in ents:
                done += 1
    return 'patched' if done == want else 'stock' if done == 0 else 'PARTIAL'


def rebuild_reloc(data, relocs):
    """Apply the reloc edits, growing the table in place; updates the directory size."""
    blocks, rptr, rsize, dir_reloc = reloc_blocks(data)
    out = bytearray()
    for rva, ents, _ in blocks:
        ents = list(ents)
        for off, typ in relocs.get(rva, []):
            if typ == 0:
                ents = [0 if e == (0x3000 | off) else e for e in ents]
            elif (0x3000 | off) not in ents:
                ents.append(0x3000 | off)
        if len(ents) % 2:
            ents.append(0)                          # keep every block 4-byte aligned
        out += struct.pack('<II', rva, 8 + 2 * len(ents)) + struct.pack('<%dH' % len(ents), *ents)
    if len(out) > rsize:
        raise SystemExit('.reloc would need %d bytes, section holds %d' % (len(out), rsize))
    data[rptr:rptr + rsize] = out + bytes(rsize - len(out))
    struct.pack_into('<I', data, dir_reloc + 4, len(out))


# ------------------------------------------------------------------- checks
def resolve(data):
    sites, relocs = build()
    for va, s in zip(SLOTS, CW_STRINGS):
        off = va - DGROUP_VA_TO_FILE
        if data[off:off + 8] != s:
            raise SystemExit('not Council Wars DCEXP16.EXE: slot %#x holds %r, expected %r'
                             % (va, bytes(data[off:off + 8]), s))
    out, problems = [], []
    for name, va, olds, new in sites:
        off = va - AUTO_VA_TO_FILE
        have = bytes(data[off:off + len(new)])
        if have == new:
            out.append((name, off, new, new, 'done'))
        elif have in olds:
            out.append((name, off, have, new, 'stock' if have == olds[0] else 'v1'))
        else:
            problems.append('%s @ file %#x: expected %s, found %s' % (name, off, olds[0].hex(), have.hex()))
    if problems:
        raise SystemExit('refusing to patch, unexpected bytes:\n  ' + '\n  '.join(problems))
    return out, relocs


def state(edits):
    kinds = {k for *_, k in edits}
    if kinds == {'done'}:
        return 'patched (v2)'
    if kinds == {'stock'}:
        return 'stock'
    return 'v1 or partial (apply upgrades to v2)'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    a = ap.parse_args(argv)

    data = bytearray(open(a.exe, 'rb').read())
    edits, relocs = resolve(data)
    rstate = reloc_state(data, relocs)
    print('%s: Council Wars DCEXP16.EXE, OZI menu code %s, .reloc %s' % (a.exe, state(edits), rstate))
    if a.command == 'verify':
        return 0

    for site, off, exp, new, kind in edits:
        print('  %-56s file %#7x  VA %#x  %s' % (site, off, off + AUTO_VA_TO_FILE,
                                                  'done' if kind == 'done' else '%d bytes (%s)' % (len(new), kind)))
        if exp != new:
            print('      %s\n   -> %s' % (exp.hex(' '), new.hex(' ')))
    for page, redits in relocs.items():
        print('  .reloc page %#x: %s' % (page, ', '.join(
            '%03X->%s' % (off, 'ABS' if typ == 0 else 'HIGHLOW') for off, typ in redits)))
    if a.command == 'plan':
        return 0
    if state(edits) == 'patched (v2)' and rstate == 'patched':
        print('nothing to do')
        return 0

    bak = a.exe + '.ozi.bak'
    shutil.copyfile(a.exe, bak)
    for site, off, exp, new, kind in edits:
        data[off:off + len(new)] = new
    rebuild_reloc(data, relocs)
    open(a.exe, 'wb').write(data)
    print('written %s (%d code sites, %d .reloc edits); backup %s'
          % (a.exe, len(edits), sum(len(v) for v in relocs.values()), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Add the "OZI MISSIONS" and "DARK COLONY" campaign modes to Council Wars' DCEXP16.EXE.

Council Wars reads every data file through one overlay helper (0x4063E4): it first tries the
name with the prefix stored at 0x4826D0 ("exp/") and falls back to the bare name in the game
root.  The wave loader (0x452AB0) has its own copy of the prefix at 0x487DC8, and the save
folder name "esave" sits in two slots (0x482344 for the LOAD GAME screen, 0x485E5C for the
in-game dialog).  All four are plain writable strings in DGROUP, 8 bytes each, so a campaign
mode is nothing more than the content of those four slots:

    Council Wars  exp/      exp/      esave     esave
    OZI missions  ozi_ns/   ozi_ns/   ozisave   ozisave
    Dark Colony   dc/       dc/       save      save

Balance tables, unit list, sound assignments, scene lists, missions, terrains and story texts
are (re)loaded per game, so switching the slots in the main menu is enough for them.  Only the
animation list, the FIN/SPR banks and the 200-entry sound table are loaded once at start-up
(0x405264 -> 0x4051CC -> 0x42565C), which is why the pack's new units live in exp/ instead of
the overlay (see tools/build_ozi_overlay.py).

The Dark Colony mode (23 Sep 2026, doc section 10.36) is the same mechanism with a prefix that
matches nothing the game ships - `dc/` holds only the patched menu script, so every other open
falls through to the Classic data in the game root: the 106-type `GAMESTAT/GAMESTAT.TXT`, the
Classic briefings in `MISSION/`, `SCENARIO/HUMAN|ALIEN`, `INTRF_HD/HSCENE|GSCENE.TXT` and the
`SAVE/` folder the Classic exe itself uses (same two slot values, checked).  The campaign is
selected by the flags the scene-list chooser 0x402FED reads: `gs+0x14F4 = 0` (already zeroed for
every button at 0x405018) and `gs+0x14F0 = 0` (not training).

Code edits, all in the main-menu function 0x404DC8 (doc DC16_DISPLAY_AND_RESOLUTION.md
sections 10.13 and 10.36); the menu script rows come from tools/build_ozi_overlay.py:

1. The PLAY INTRO handler (button id 0x10, 0x4050DD..0x40513C, 96 bytes) becomes the
   OZI MISSIONS handler: it sets the two campaign flags exactly like NEW CAMPAIGN does
   (gs+0x14F4 = 1 "expansion scenes", gs+0x14F0 = 0 "campaign"), calls `stub_pack` to write the
   pack strings into the four slots and enters the campaign runner 0x401C08; the rest of the
   old handler is NOP padding.
2. NEW CAMPAIGN reaches the campaign runner through `call 0x401C08` at 0x405065, which goes
   through `tramp_cw_campaign` (Council Wars strings, then 0x401C08).  TRAINING does the same at
   0x405083, but through `tramp_dc_campaign` since 23 Sep 2026: ACADEMY plays SCENARIO/TEST off
   the Classic training scene lists, so its saves belong in `save` and its briefings in MISSION/.
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

6. The two new buttons are ids 6 and 7, the first free ids the menu's own filter accepts once
   `cmp edx,5` at 0x404F9E becomes `cmp edx,7` (0..7, 0Ch, 10h; anything else is ignored and
   would fall out of the menu loop).  Ids 6 and 7 were the LARGEBUTTON gadgets of buttons 0 and
   1, which build_ozi_overlay.py renumbers to 19 and 20.  The two handlers live in the 59 NOP
   bytes the old PLAY INTRO handler left behind (0x405102..0x40513C, 52 used): DARK COLONY sets
   `gs+0x14F0 = 0` and enters the campaign runner through `tramp_dc_campaign`, LOAD DC GAME does
   the same through `tramp_dc_load`, which always lists `save`.  `jne 0x40513D` at 0x4050DB (the
   end of the id chain) is retargeted at 0x405102.
7. The scrolling credits box is removed AT 640x480 ONLY (--width 640 --height 480): the seven-row
   menu is 217 rows tall and the black band of the 640x480 backdrop between the crescent and the
   artwork is exactly 217 rows (218..434, measured), so nothing is left for it there.  main.c
   bintro's TTY create call (0x404E80..0x404EAC, eight pushes and `call 0x4284A8`, which is
   `ret 20h` so the stack balances) becomes 45 NOPs; the two absolute operands it carried
   (`intrface/mfonto5`, `intrface/credits.txt`) become ABSOLUTE .reloc padding.  This supersedes
   the credits y/height sites of the 21 Sep version of this patch at that size.
   At the HD sizes the box STAYS (24 Sep 2026, maintainer: "return back credentials [credits] for
   higher than 640x480 resolutions"; from the evening of 23 Sep to 24 Sep it was removed at every
   size): the painted backdrops are black from far above the title down to the bottom artwork at
   H-45, so the menu block is moved DOWN instead - build_ozi_overlay.menu_layout (and the
   patcher's Edit-OziMenu) put its first row 120 rows under the DCUT title (11 px, the stock
   100-row box, 9 px), or less where the bottom row would pass H-72 (the stock 640x480 bottom
   row, 2-3 px above the artwork) - and patch_resolution.py writes the box: y = title + 11 as
   before (`credits_y(203, 296)`) and height = min(100, what the block leaves) = 94 rows at
   1024x768, 76 at 1280x720, the stock 100 at 1280x800, 1280x1024 and 3840x1080
   (`cw_credits_height`).  Neither credits edit nor the page-0x4000 .reloc change exists in the HD
   form of this patch; an exe patched by the 23 Sep form at an HD size keeps its NOPs (this tool
   does not restore code) - rebuild it from the original (the CLI prints a note).
   The DESTROY call has to go with the create at 640x480 (edit 8, found by the first game test): every menu click
   runs `mov edx,1; call 0x429684` at 0x405008, which frees that one TTY and decrements the
   global TTY count 0x4D61C0.  Without the create the count goes to -1, the next screen's TTY -
   the race overview's text - is built at index -1, so that screen comes up empty and the
   corrupted neighbourhood takes the level teardown down with an access violation at 0x44DE14.
   The count becomes 0.  (The TTY module allows exactly one instance: its create asserts when
   the count reaches 2, which is why the menu frees its own before leaving.)  At the HD sizes the
   create runs, so the destroy keeps its 1.
8. MULTI PLAYER WAR (button id 3, `call 0x405C20` at 0x405097, the network options screen) goes
   through `tramp_dc_net` since 25 Sep 2026 (doc 10.39): `call stub_dc_set; jmp 0x405C20`, 10 bytes
   at 0x47F3B0 in the zero tail, relative operands only (no .reloc change).  A network battle
   loads its balance tables per game start through the prefix, and the menu's mode is sticky, so
   after OZI MISSIONS / LOAD OZI GAME a network game read ozi_ns/gamestat (126 types, other weapon,
   explosion and bullet tables) and desynchronised against every other client and the relay
   server's checksums.  In the Dark Colony mode every table comes from the game root, i.e. the
   106-type Classic set that dc16.exe and the server's engine port use.  (The other half of network
   compatibility is data: build_ozi_overlay.py keeps grrr.fin / troo.fin out of animozi.dat.)

Version 1 of this patch (10 Sep 2026, before OZI LOAD) had `stub_cw` tail-jump into 0x401C08
itself and left LOAD GAME direct; the tool recognises a v1 exe and upgrades it in place.
Nothing is written unless every site holds its stock, v1 or v2 bytes (an exe of the v3 form, before
item 8, holds the stock call at 0x405097 and is upgraded to v4 the same way).

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
NEW_CAMPAIGN_CALL = 0x405065    # `call 00401C08` in the NEW CAMPAIGN handler (button id 0)
TRAINING_CALL = 0x405083        # ... and in the TRAINING / ACADEMY handler (button id 1)
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
# Dark Colony mode (23 Sep 2026).  The AUTO zero tail runs to the section end 0x47F400 and the
# camera stub of patch_camera.py ends at 0x47F331, so these three sit above it (86 bytes left).
STUB_DC = 0x47F340
TRAMP_DC_CAMPAIGN = 0x47F390
TRAMP_DC_LOAD = 0x47F3A0
DC_BUTTON = 6                   # "DARK COLONY"   (id 6 was the LARGEBUTTON gadget of button 0)
DC_LOAD_BUTTON = 7              # "LOAD DC GAME"  (id 7 was the LARGEBUTTON gadget of button 1)
ID_FILTER_IMM = 0x404F9E        # `cmp edx,5` in the menu's accepted-id filter: 5 -> 7
ID_CHAIN_END = 0x4050DB         # `jne 0040513D` after the last `cmp edi,10h`: -> DISPATCH
DISPATCH = 0x405102             # the 59 NOP bytes the old PLAY INTRO handler left behind
DISPATCH_END = 0x40513D         # the between-mission loop every campaign button jumps to
CAMPAIGN_FLAG = 0x14F0          # gs+0x14F0 = 0: campaign, not training (gs+0x14F4 is 0 already)
CREDITS_CALL = 0x404E80         # main.c bintro's credits TTY create: 8 pushes + call 0x4284A8
CREDITS_END = 0x404EAD
CREDITS_FREE = 0x405008         # `mov edx,1` of the matching destroy call (0x429684) -> 0
SLOTS = (0x4826D0, 0x487DC8, 0x482344, 0x485E5C)
CW_STRINGS = (b'exp/\0\0\0\0', b'exp/\0\0\0\0', b'esave\0\0\0', b'esave\0\0\0')
PACK_STRINGS = (b'ozi_ns/\0', b'ozi_ns/\0', b'ozisave\0', b'ozisave\0')
DC_STRINGS = (b'dc/\0\0\0\0\0', b'dc/\0\0\0\0\0', b'save\0\0\0\0', b'save\0\0\0\0')
RELOC_PAGE_HANDLER = 0x5000     # page of the old handler: operands at 0x0DE and 0x103 vanish
RELOC_PAGE_TAIL = 0x7F000       # page of the stubs: eight new operands, four for stub_dc_set
RELOC_PAGE_CREDITS = 0x4000     # page of the credits call: its two string operands vanish
CREDITS_RELOC = (0xE8B, 0xE90)  # `push intrface/mfonto5`, `push intrface/credits.txt`

# Network games in the Dark Colony mode (25 Sep 2026, docstring item 8)
NETWORK_CALL = 0x405097         # `call 00405C20` in the MULTI PLAYER WAR handler (button id 3)
NETWORK_OPTIONS = 0x405C20      # the netopt screen: DirectPlay / ACT AS SERVER / CONNECT TO SERVER
TRAMP_DC_NET = 0x47F3B0         # after tramp_dc_load (ends 0x47F3AA); 70 tail bytes left behind it

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


class Masked:
    """An accepted old form with wildcard bytes (mask byte 0 = don't care)."""

    def __init__(self, template, mask):
        assert len(template) == len(mask)
        self.template, self.mask = template, mask

    def __len__(self):
        return len(self.template)

    def matches(self, have):
        return len(have) == len(self.mask) and all(
            m == 0 or h == t for h, t, m in zip(have, self.template, self.mask))

    def hex(self, sep=''):
        return sep.join('??' if m == 0 else '%02x' % t for t, m in zip(self.template, self.mask))


# main.c bintro's credits TTY create (0x404E80..0x404EAC, doc 10.36): eight pushes, the 280-pixel
# width in ecx, the y in ebx, the height and the x, then `call 0x4284A8`, which is `ret 20h`, so
# dropping the whole block balances the stack and nothing downstream reads eax/ebx/ecx/edx.  The y,
# the height and the x are written per resolution by patch_resolution.py (and by the 21 Sep version
# of this patch at 640x480), so they are wildcards here.
CREDITS_TEMPLATE = bytes.fromhex(
    '6a 00 6a 00 6a 00 6a 05 6a 02 68 84 21 48 00 68 6c 24 48 00'   # push 0,0,0,5,2, font, credits.txt
    'b9 18 01 00 00'                                                # mov ecx,118h   (width 280)
    'bb 00 00 00 00'                                                # mov ebx,<y>
    '6a 00'                                                         # push <height>
    'ba 00 00 00 00'                                                # mov edx,<x>
    '8b 45 fc'                                                      # mov eax,[ebp-4]
    'e8 fb 35 02 00')                                               # call 004284A8
CREDITS_MASK = (b'\xff' * 25 + b'\xff' + b'\0' * 4 + b'\xff' + b'\0'
                + b'\xff' + b'\0' * 4 + b'\xff' * 8)
CREDITS_LEN = len(CREDITS_TEMPLATE)


def dispatch_block():
    """The two Dark Colony handlers, in the NOP tail of the menu's id chain (doc 10.36).

    Entered from the retargeted `jne` at the end of the chain with eax = the game state, edi = the
    button id and [ebp-4] = the screen.  `gs+0x14F4` (Council Wars scene lists) is already 0: the
    menu writes it at 0x405018 before it dispatches, for every button."""
    d = bytearray()
    for button, target, last in ((DC_BUTTON, TRAMP_DC_CAMPAIGN, False),
                                 (DC_LOAD_BUTTON, TRAMP_DC_LOAD, True)):
        d += b'\x83\xFF' + bytes([button])                  # cmp edi,<id>
        hole = len(d) + 1
        d += b'\x75\x00'                                    # jne <next handler / end>
        d += bytes.fromhex('C7 80') + struct.pack('<II', CAMPAIGN_FLAG, 0)   # mov [eax+14F0h],0
        d += b'\x89\xC2'                                    # mov edx,eax     (game state)
        d += b'\x8B\x45\xFC'                                # mov eax,[ebp-4] (screen)
        d += b'\xE8' + rel32(DISPATCH + len(d) + 5, target)  # call tramp_dc_*
        if not last:                                         # the last one falls through the pad
            d += b'\xEB' + bytes([DISPATCH_END - (DISPATCH + len(d) + 2)])
        d[hole] = len(d) - (hole + 1)
    return bytes(d)


def build(stock_mode=False):
    """Return ([(name, va, [accepted old bytes...], new)], {page: [(offset, new_u16)]}).

    The first accepted variant of every site is the stock code, the others are earlier versions
    of this patch; `new` is the current version.  `stock_mode` (640x480) adds the two credits
    edits and their .reloc change (docstring item 7); the HD form leaves the credits box alone."""
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
    handler_v2 = bytes(h) + b'\x90' * (HANDLER_LEN - len(h))    # v2: the rest was NOP padding
    assert DISPATCH == HANDLER + len(h)
    h += dispatch_block()                                   # v3: the two Dark Colony handlers
    h += b'\x90' * (HANDLER_LEN - len(h))
    assert len(h) == HANDLER_LEN == len(STOCK_HANDLER)

    body, ops = slot_writer(DC_STRINGS)
    stub_dc = b'\x50\x57' + body + b'\x5F\x58\xC3'              # push eax/edi ... pop/pop/ret
    dc_ops = [2 + o for o in ops]

    sites = [('OZI MISSIONS + DARK COLONY handlers (was PLAY INTRO)', HANDLER,
              [STOCK_HANDLER, handler_v2], bytes(h)),
             ('stub_pack (pack strings into the 4 slots)', STUB_PACK, [bytes(len(stub_pack))], stub_pack),
             ('stub_cw_set (Council Wars strings)', STUB_CW, [bytes(len(stub_cw)), stub_cw_v1], stub_cw),
             ('tramp_cw_campaign (stub_cw_set; jmp 401C08)', TRAMP_CW_CAMPAIGN, [bytes(10)],
              tramp(TRAMP_CW_CAMPAIGN, STUB_CW, CAMPAIGN_RUNNER)),
             ('tramp_cw_load (stub_cw_set; jmp 403AA4)', TRAMP_CW_LOAD, [bytes(10)],
              tramp(TRAMP_CW_LOAD, STUB_CW, LOAD_GAME)),
             ('tramp_pack_load (stub_pack; jmp 403AA4)', TRAMP_PACK_LOAD, [bytes(10)],
              tramp(TRAMP_PACK_LOAD, STUB_PACK, LOAD_GAME))]
    sites.append(('NEW CAMPAIGN call -> tramp_cw_campaign', NEW_CAMPAIGN_CALL,
                  [call(NEW_CAMPAIGN_CALL, CAMPAIGN_RUNNER), call(NEW_CAMPAIGN_CALL, STUB_CW)],
                  call(NEW_CAMPAIGN_CALL, TRAMP_CW_CAMPAIGN)))
    # ACADEMY (TRAINING) plays SCENARIO/TEST off the Classic scene lists htscene / gtscene, which the
    # Council Wars prefix never shadows, so it is a Dark Colony campaign in everything but the flag:
    # its saves belong in `save` next to them (maintainer, 23 Sep 2026) and its briefings in MISSION/
    # rather than the Council Wars ones in exp/mission (the same shadowing that the Classic `sounds`
    # fix removed from dc16.exe, doc 10.21).
    sites.append(('ACADEMY (TRAINING) call -> tramp_dc_campaign', TRAINING_CALL,
                  [call(TRAINING_CALL, CAMPAIGN_RUNNER), call(TRAINING_CALL, STUB_CW),
                   call(TRAINING_CALL, TRAMP_CW_CAMPAIGN)],
                  call(TRAINING_CALL, TRAMP_DC_CAMPAIGN)))
    sites.append(('LOAD GAME call -> tramp_cw_load', LOAD_GAME_CALL,
                  [call(LOAD_GAME_CALL, LOAD_GAME)], call(LOAD_GAME_CALL, TRAMP_CW_LOAD)))
    sites.append(('OZI LOAD (was SINGLE PLAYER WAR) call -> tramp_pack_load', SINGLE_WAR_CALL,
                  [call(SINGLE_WAR_CALL, SINGLE_WAR)], call(SINGLE_WAR_CALL, TRAMP_PACK_LOAD)))
    sites.append(('stub_dc_set (Dark Colony strings)', STUB_DC, [bytes(len(stub_dc))], stub_dc))
    sites.append(('tramp_dc_campaign (stub_dc_set; jmp 401C08)', TRAMP_DC_CAMPAIGN, [bytes(10)],
                  tramp(TRAMP_DC_CAMPAIGN, STUB_DC, CAMPAIGN_RUNNER)))
    sites.append(('tramp_dc_load (stub_dc_set; jmp 403AA4)', TRAMP_DC_LOAD, [bytes(10)],
                  tramp(TRAMP_DC_LOAD, STUB_DC, LOAD_GAME)))
    # MULTI PLAYER WAR always in the Dark Colony mode: the Classic tables, whatever mode was last
    sites.append(('tramp_dc_net (stub_dc_set; jmp 405C20)', TRAMP_DC_NET, [bytes(10)],
                  tramp(TRAMP_DC_NET, STUB_DC, NETWORK_OPTIONS)))
    sites.append(('MULTI PLAYER WAR call -> tramp_dc_net', NETWORK_CALL,
                  [call(NETWORK_CALL, NETWORK_OPTIONS)], call(NETWORK_CALL, TRAMP_DC_NET)))
    sites.append(('menu id filter: accept the button ids 6 and 7 (cmp edx,5 -> 7)', ID_FILTER_IMM,
                  [bytes.fromhex('83 FA 05')], bytes.fromhex('83 FA 07')))
    sites.append(('end of the id chain: jne 0040513D -> the Dark Colony handlers', ID_CHAIN_END,
                  [b'\x75' + bytes([DISPATCH_END - (ID_CHAIN_END + 2)])],
                  b'\x75' + bytes([DISPATCH - (ID_CHAIN_END + 2)])))
    relocs = {
        RELOC_PAGE_HANDLER: [(0x0DE, 0), (0x103, 0)],
        RELOC_PAGE_TAIL: [((STUB_PACK + o) & 0xFFF, 3) for o in pack_ops]
                       + [((STUB_CW + o) & 0xFFF, 3) for o in cw_ops]
                       + [((STUB_DC + o) & 0xFFF, 3) for o in dc_ops],
    }
    if stock_mode:
        # 640x480 only: the 217-row block fills the backdrop's black band, the box goes (item 7)
        sites.append(('credits TTY create -> %d NOPs (640x480: the seven-row menu needs the rows)' % CREDITS_LEN,
                      CREDITS_CALL, [Masked(CREDITS_TEMPLATE, CREDITS_MASK)], b'\x90' * CREDITS_LEN))
        sites.append(('credits TTY destroy count 1 -> 0 (nothing was created)', CREDITS_FREE,
                      [bytes.fromhex('ba 01 00 00 00')], bytes.fromhex('ba 00 00 00 00')))
        relocs[RELOC_PAGE_CREDITS] = [(o, 0) for o in CREDITS_RELOC]
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
# The start-up animation list is opened through the overlay helper as `anim.dat` (DGROUP 0x4824B8,
# mode "rt" at 0x4824B4, reader 0x4051CC).  The pack's base set (three new units and its transport
# `tranozi`) must not touch the stock `exp/anim.dat` / `tran.fin` / `tran.spr` that the ORIGINAL exe
# reads (maintainer requirement 14 Sep 2026: every file the original reads stays original), so the
# patched exe reads `exp/animozi.dat` instead: the 9-byte string has 3 bytes of slack before the
# next string ("w" at 0x4824C4), which fits "animozi.dat" exactly.  build_ozi_overlay.py writes it.
DGROUP_SITES = [
    ('start-up animation list "anim.dat" -> "animozi.dat" (exp/animozi.dat = stock list + pack units)',
     0x4824B8, [b'anim.dat\0\0\0\0'], b'animozi.dat\0'),
]
# 640x480 only (21 Sep 2026): at the stock size the exe reads exp/intrface/bintroe, which the ORIGINAL exe
# reads too and which therefore keeps its PLAY INTRO row; the main-menu script string "intrface/bintro"
# (language letter appended) ends in six letters followed by the next string, so the name becomes
# "bintoz" in place and the exe reads exp/intrface/bintoze (OZI mode: ozi_ns/intrface/bintoze) - the
# stock menu with the OZI rows, written by the patcher (Apply-DarkColonyPatches.ps1 Write-StockOziMenu).
# At HD sizes the hdpaths patch points the exe at exp/intrf_hd/bintroe instead, which carries the rows.
STOCK_MODE_SITES = [
    ('main menu script "intrface/bintro" -> "intrface/bintoz" (640x480: exp/intrface/bintoze = stock menu + OZI rows, written by the patcher)',
     0x482498, [b'intrface/bintro\0'], b'intrface/bintoz\0'),
]
def resolve(data, stock_mode=False):
    sites, relocs = build(stock_mode)
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
            out.append((name, off, va, new, new, 'done'))
            continue
        hit = next((i for i, o in enumerate(olds)
                    if (o.matches(have) if isinstance(o, Masked) else have == o)), None)
        if hit is None:
            problems.append('%s @ file %#x: expected %s, found %s'
                            % (name, off, olds[0].hex(), have.hex()))
        else:
            out.append((name, off, va, have, new, 'stock' if hit == 0 else 'v1'))
    for name, va, olds, new in DGROUP_SITES + (STOCK_MODE_SITES if stock_mode else []):
        off = va - DGROUP_VA_TO_FILE
        have = bytes(data[off:off + len(new)])
        if have == new:
            out.append((name, off, va, new, new, 'done'))
        elif have in olds:
            out.append((name, off, va, have, new, 'stock'))
        else:
            problems.append('%s @ file %#x: expected %s, found %s' % (name, off, olds[0].hex(), have.hex()))
    if problems:
        raise SystemExit('refusing to patch, unexpected bytes:\n  ' + '\n  '.join(problems))
    return out, relocs


def state(edits):
    kinds = {k for *_, k in edits}
    if kinds == {'done'}:
        return 'patched (v4)'
    if kinds == {'stock'}:
        return 'stock'
    return 'earlier version or partial (apply upgrades to v4)'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    ap.add_argument('--width', type=int, default=1024, help='screen size the exe is patched for; 640x480 adds the bintoz menu-script site and removes the credits box (docstring item 7)')
    ap.add_argument('--height', type=int, default=768)
    a = ap.parse_args(argv)

    data = bytearray(open(a.exe, 'rb').read())
    stock_mode = (a.width, a.height) == (640, 480)
    edits, relocs = resolve(data, stock_mode)
    rstate = reloc_state(data, relocs)
    print('%s: Council Wars DCEXP16.EXE, OZI menu code %s, .reloc %s' % (a.exe, state(edits), rstate))
    if not stock_mode and data[CREDITS_CALL - AUTO_VA_TO_FILE:CREDITS_END - AUTO_VA_TO_FILE] == b'\x90' * CREDITS_LEN:
        print('  NOTE: the credits TTY create is NOPs (the 23 Sep 2026 form of this patch); the HD form keeps the'
              ' box and this tool does not restore code - rebuild from the original exe (item 7)')
    if a.command == 'verify':
        return 0

    for site, off, va, exp, new, kind in edits:
        print('  %-56s file %#7x  VA %#x  %s' % (site, off, va,
                                                  'done' if kind == 'done' else '%d bytes (%s)' % (len(new), kind)))
        if exp != new:
            print('      %s\n   -> %s' % (exp.hex(' '), new.hex(' ')))
    for page, redits in relocs.items():
        print('  .reloc page %#x: %s' % (page, ', '.join(
            '%03X->%s' % (off, 'ABS' if typ == 0 else 'HIGHLOW') for off, typ in redits)))
    if a.command == 'plan':
        return 0
    if state(edits) == 'patched (v4)' and rstate == 'patched':
        print('nothing to do')
        return 0

    bak = a.exe + '.ozi.bak'
    shutil.copyfile(a.exe, bak)
    for site, off, va, exp, new, kind in edits:
        data[off:off + len(new)] = new
    rebuild_reloc(data, relocs)
    open(a.exe, 'wb').write(data)
    print('written %s (%d code sites, %d .reloc edits); backup %s'
          % (a.exe, len(edits), sum(len(v) for v in relocs.values()), bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

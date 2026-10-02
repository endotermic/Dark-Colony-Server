#!/usr/bin/env python3
"""patch_online.py - fix `online`: the ONLINE WAR button of Dark Colony Ultimate (29 Sep 2026).

    python patch_online.py verify EXE
    python patch_online.py plan   EXE
    python patch_online.py apply  EXE [--game DIR --width W --height H] [--output OUT]
    python patch_online.py data   DIR --width W --height H        (the screen script + DEFAULT_SERVER.TXT only)
    python patch_online.py embed  online\\online.dll                (developer: paste a rebuilt module below)

Dark Colony Ultimate only (`Dark Colony Ultimate.exe`, formerly engexp16new.exe); requires fixes
`ozi` (the menu's id chain and the id filter it widened) and `icon` (this fix appends its section
after `.dcicon`), so it is the LAST fix of the Ultimate build.  Docs: DC16_DISPLAY_AND_RESOLUTION.md
10.51, RELAY_SERVER_PLAN.md 20, DC16_NETWORK_PROTOCOL.md 4.4 / 6.9.

What the fix is
---------------
1. A NEW SECTION `.dccode` appended to the file (file offset = the end rounded up to FileAlignment,
   VA = SizeOfImage, code + read + write + execute).  It holds the compiled C module
   online/online.c - the ONLINE WAR screen (the game's own interface engine), DEFAULT_SERVER.TXT,
   the TLS client (Windows Schannel), the relay dialogue (0x50 LIST, 0x51 ROOMS, 0x52 ENTER,
   0x54 ENTERING) and the loopback proxy thread through which the stock lobby/battle code talks to
   the relay.  The module is built by online/build.cmd into a relocatable DLL; `embed` stores its
   single .text section (rdata/data/bss merged), its HIGHLOW relocations and its two exports in
   MODULE below, and plan/apply rebase the absolute operands to the section's VA.  Only the exe's
   LoadLibraryA / GetProcAddress IAT slots are used; the import table is untouched.
2. Three header edits (NumberOfSections + 1, the section header in the zero slack after the section
   table, SizeOfImage) - the resource directory is not touched (that was fix icon's business).
3. Two edits in AUTO: the menu's accepted-id filter `cmp edx,7` at 0x404F9E (5 in the stock exe, 7
   since fix ozi) becomes `cmp edx,8` so button id 8 = ONLINE WAR reaches the id chain, and the seven
   NOP bytes at the end of that chain (0x405136..0x40513C, left by fix ozi's Dark Colony handlers;
   the id-7 branch falls through them into the loop head 0x40513D) become `jmp online_dispatch` +
   2 NOP.  The dispatch (in the section) runs the module for id 8 and jumps to the loop head for
   anything else, so the fall-through behaves as before.

Data (`apply --game`, `data`): the screen script ONLINE - the LOAD GAME picker LOADGE with the list
widened from 392 to 448 px (56 columns of MFONTO5, 8 px per column), scroll bar / UP / DOWN 56 px further
right, a read-only header line above the list and a status line below it, the title "Online War",
the buttons ENTER / BACK, a server line above the status line, the title centred in its panel, the
save-mode widgets and the two animations over the header and status lines removed - written as
INTRF_HD/ONLINE from INTRF_HD/LOADGE at the HD sizes and as INTRFACE/ONLINE from INTRFACE/LOADGE at
640x480 (the module probes for intrf_hd\\ONLINE, so one exe serves every size), with its background
ONLINEBG.GIF = LOADER.GIF plus grey frames around the header + list, the scroll bar and the two text lines (Pillow); and DEFAULT_SERVER.TXT beside the exe,
written only when it is missing (a player's edit is never overwritten).  The patcher
(Apply-DarkColonyPatches.ps1, Write-OnlineScreen) does the same in PowerShell.
"""
import argparse
import base64
import io
import hashlib
import json
import os
import re
import struct
import sys

SECTION_NAME = b'.dccode'
SECTION_CHARS = 0xE0000020          # code, execute, read, write (the module keeps its state in the section)
IMAGE_BASE = 0x400000
AUTO_VA, AUTO_FILE = 0x401000, 0x400
ULTIMATE_AUTO_RAW = 0x7E400          # the Council Wars build's AUTO raw size (Classic: 0x7E200)
ID_FILTER = 0x404F9E                 # cmp edx, imm8 (83 FA xx): 05 stock, 07 after ozi, 08 after this fix
CHAIN_TAIL = 0x405136                # 7 NOP bytes -> jmp dispatch + 2 NOP
LOOP_HEAD = 0x40513D
DISPATCH_EXPORT, ENTRY_EXPORT = 'online_dispatch', 'online_war'
LIST_WIDTH_OLD, LIST_WIDTH_NEW = 392, 448
SHIFT = LIST_WIDTH_NEW - LIST_WIDTH_OLD
ROW_CHARS = 56                       # 448 px / 8 px per column (MFONTO5: 7 px glyphs on an 8 px advance)
W_STATUS, W_HEADER, W_SERVER = 17, 30, 31   # status line, column header, the server line above the status
TITLE_X_OFF, TITLE_W = -50, 318              # the title panel of LOADER.GIF spans list x - 50 .. + 268 (measured 262..580 at 1024x768)
BG_NAME = 'onlinebg'                         # the screen's background: LOADER.GIF + the grey frame around the two text lines
# the frame (maintainer, 29 Sep 2026: "bottom text area (server name etc.) must be wrapped into a gray frame as
# it is done on the other forms"): a 3-px tube in the console greys of the lobby's boxes - 35, the light edge
# 106/107, 35 - a 2-px black gap and a black interior, corners rounded by one pixel; LOADER.GIF's palette has
# 35 and 106 (indices found by value), 6 px outside the list's columns, from list bottom + 2 to + 46
FRAME_GREYS = (35, 106, 35)
# Three frames (maintainer, same day: "headers section and maps section must be wrapped into a gray frame as
# it is done on the other forms. scroll bar must be wrapped as it is done on the other forms"), all relative to
# the list rect (lx, ly, 448, lh); B = ly + lh = the list's bottom:
#   list frame   x lx-6 .. lx+454, y ly-22 .. B+3       (the column header in_text 30 at ly-16 sits inside)
#   scroll frame x ux-4 .. ux+30,  y ly .. B+2          (ux = lx+460 = the UP/DOWN buttons' x; UP at ly+4, DOWN at B-28)
#   text frame   x lx-6 .. lx+454, y B+8 .. B+52        (server line at B+14, state line at B+30)
FRAME_MARGIN_X = 6
HEADER_DY, LIST_TOP_DY, LIST_BOTTOM_DY = -16, -22, 3
SCROLL_DX, SCROLL_W = 12, 26
TEXT_TOP_DY, SERVER_DY, STATUS_DY, TEXT_BOTTOM_DY = 8, 14, 30, 52

DEFAULT_SERVER_TEXT = """/*
 * DEFAULT_SERVER.TXT - the relay server that ONLINE WAR connects to.
 *
 * Dark Colony Ultimate reads this file when you press ONLINE WAR in the main menu.
 * It connects to the address below with TLS encryption on port 8889 (the Dark Colony
 * Server relay, https://github.com/endotermic/Dark-Colony-Server), shows the rooms the
 * relay offers - map, terrain, seats, players, bots, status - and joins the room you pick.
 *
 * Usage:
 *   - one address, optionally with a port:      my.relay.example.org:8889
 *   - the word "plain" after the address turns the encryption off, for a relay on your own
 *     network without a certificate (the plain relay port is 8888):
 *                                                 192.168.1.10 plain
 *   - comments in the C++ style are ignored: "//" to the end of a line, or a block like this one.
 *
 * Keep one address in the file. MULTI PLAYER WAR (the in-game host / CONNECT TO SERVER
 * screens) does not read this file.
 */

dark-colony-server.fly.dev
""".replace('\n', '\r\n')

# --- MODULE BEGIN (written by `embed`, do not edit) ---
MODULE = json.loads(
    '{"text":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/////01BUCAgICAgICA'
    'gICAgICAgICBURVJSQUlOICBTRUFUUyBQTEFZRVJTIEJPVFMgU1RBVFVTAAAAaW50cmZhY2Uvb25saW4AAC9vbmxpbgAAXE9OTEl'
    'ORQBCTU9ubGluZQAAAAAxMjcuMC4wLjEAAAAwMTIzNDU2Nzg5QUJDREVGAAAAAGtlcm5lbDMyLmRsbAAAAAB3czJfMzIuZGxsAAB'
    'zZWN1cjMyLmRsbABDcmVhdGVGaWxlQQBSZWFkRmlsZQAAAABDbG9zZUhhbmRsZQBHZXRUaWNrQ291bnQAAAAAQ3JlYXRlVGhyZWF'
    'kAAAAAFNsZWVwAAAAR2V0TGFzdEVycm9yAAAAAFZpcnR1YWxBbGxvYwAAAABXcml0ZUZpbGUAAABTZXRGaWxlUG9pbnRlcgAAV1N'
    'BU3RhcnR1cAAAc29ja2V0AABjb25uZWN0AHNlbmQAAAAAcmVjdgAAAABzZWxlY3QAAGNsb3Nlc29ja2V0AGdldGhvc3RieW5hbWU'
    'AAABpbmV0X2FkZHIAAABodG9ucwAAAG50b2hzAAAAYmluZAAAAABsaXN0ZW4AAGFjY2VwdAAAZ2V0c29ja25hbWUAV1NBR2V0TGF'
    'zdEVycm9yAF9fV1NBRkRJc1NldAAAAABBY3F1aXJlQ3JlZGVudGlhbHNIYW5kbGVBAAAASW5pdGlhbGl6ZVNlY3VyaXR5Q29udGV'
    '4dEEAAFF1ZXJ5Q29udGV4dEF0dHJpYnV0ZXNBAEVuY3J5cHRNZXNzYWdlAABEZWNyeXB0TWVzc2FnZQAARnJlZUNvbnRleHRCdWZ'
    'mZXIAAABEZWxldGVTZWN1cml0eUNvbnRleHQAAABGcmVlQ3JlZGVudGlhbHNIYW5kbGUAAAByZXNvbHZlOiB3czJfMzIuZGxsIG5'
    'vdCBsb2FkZWQAAHJlc29sdmU6IHNlY3VyMzIuZGxsIG5vdCBsb2FkZWQAcmVzb2x2ZTogYSBmdW5jdGlvbiBpcyBtaXNzaW5nICh'
    'zZWUgdGhlIFcgdGFibGUgaW4gb25saW5lLmMpAAAAAE9OTElORS5MT0cAAA0KAABUTFMgZW5jcnlwdCBmYWlsZWQgAFRMUyBkZWN'
    'yeXB0IGZhaWxlZCAAVExTIHJlbmVnb3RpYXRpb24gcmVxdWVzdGVkAE1pY3Jvc29mdCBVbmlmaWVkIFNlY3VyaXR5IFByb3RvY29'
    'sIFByb3ZpZGVyAAAAAFRMUyBjcmVkZW50aWFscyBmYWlsZWQgAFRMUyBoYW5kc2hha2UgdGltZWQgb3V0AGNvbm5lY3Rpb24gY2x'
    'vc2VkIGR1cmluZyB0aGUgVExTIGhhbmRzaGFrZQAAc2VuZCBmYWlsZWQgZHVyaW5nIHRoZSBUTFMgaGFuZHNoYWtlAAAAAFRMUyB'
    'oYW5kc2hha2UgZmFpbGVkIAAAACAoY2VydGlmaWNhdGUgbm90IHRydXN0ZWQpAAAgKGNlcnRpZmljYXRlIG5hbWUgbWlzbWF0Y2g'
    'pAAAAACAobm90IGEgVExTIHNlcnZlcjsgdHJ5IGBwbGFpbmApAAAAAFRMUyBzdHJlYW0gc2l6ZXMgZmFpbGVkAERFRkFVTFRfU0V'
    'SVkVSLlRYVAAAREVGQVVMVF9TRVJWRVIuVFhUIG5vdCBmb3VuZCBiZXNpZGUgdGhlIGdhbWUAAAAAcGxhaW4AAABub3RscwAAAER'
    'FRkFVTFRfU0VSVkVSLlRYVCBuYW1lcyBubyBzZXJ2ZXIgYWRkcmVzcwAAUHJvdG9jb2wgZXJyb3I6IGJhZCBmcmFtZSBmcm9tIHR'
    'oZSByZWxheQAAAABQcm90b2NvbCBlcnJvcjogZnJhbWUgdG9vIGxvbmcAAHByb3h5OiBhY2NlcHQgZmFpbGVkAAAAAHByb3h5OiB'
    '0aGUgZ2FtZSBjb25uZWN0ZWQAAABwcm94eTogY2xvc2luZyBib3RoIGNvbm5lY3Rpb25zAFdTQVN0YXJ0dXAgZmFpbGVkAAAAQ2F'
    'ubm90IHJlc29sdmUgAHNvY2tldCgpIGZhaWxlZABDYW5ub3QgY29ubmVjdCB0byAAADoAAAAgKGVycm9yIAAAAAApAAAAc2NyZWV'
    'uOiAAAAAAc2NyZWVuIGxvYWRlZAAAAFNlcnZlcjogbm9uZSAoc2VlIERFRkFVTFRfU0VSVkVSLlRYVCkAAABTZXJ2ZXI6IAAAAAB'
    'Db25uZWN0aW5nIChubyBlbmNyeXB0aW9uKS4uLgAAAENvbm5lY3RpbmcgKFRMUykuLi4AY29ubmVjdGVkIChwbGFpbikAAABjb25'
    'uZWN0ZWQgKFRMUykAQ29ubmVjdGVkLiBTZWxlY3QgYSByb29tIGFuZCBwcmVzcyBFTlRFUi4AAABDb25uZWN0ZWQgKFRMUykuIFN'
    'lbGVjdCBhIHJvb20gYW5kIHByZXNzIEVOVEVSLgBDb25uZWN0aW9uIGxvc3QuAAAAAGNvbm5lY3Q6IAAAAE5vdCBjb25uZWN0ZWQ'
    'uIFByZXNzIEJBQ0sgYW5kIHRyeSBhZ2Fpbi4AAAAAU2VsZWN0IGEgcm9vbSBmaXJzdC4AAAAARW50ZXJpbmcgcm9vbSAAAC4uLgA'
    'tLS0gT05MSU5FIFdBUiBwcmVzc2VkAABWaXJ0dWFsQWxsb2MgZmFpbGVkAGNvbmZpZzogAAAAAGNvbmZpZzogaG9zdCAAAABjb25'
    'maWc6IHBvcnQgAAAAY29uZmlnOiBwbGFpbiAAAGJhY2sgdG8gdGhlIG1lbnUAAAAARU5URVJJTkcgc2xvdCAAAGxvb3BiYWNrIGx'
    'pc3RlbmVyIGZhaWxlZAAAAABsb29wYmFjayBwb3J0IAAAY2FsbGluZyB0aGUgZ2FtZSdzIG5ldHdvcmsgZW50cnksIHRjcCBuZXQ'
    'gb2JqZWN0IAAAAG5ldHdvcmsgZW50cnkgcmV0dXJuZWQgAAAAAAByar9qAAAAAA0AAACIAAAAOBwAADgOAAAYAAAAA4ADgAgcAAA'
    'gAAAAKBwAABAAAACRNgAAmDYAANA2AADgNgAAETcAABw3AABNNwAAWDcAAAAQAADUCwAAwBwAAAAiAAAAAAAAABAAACQDAAAuYnN'
    'zAAAAACQTAAAEAAAALmRhdGEAAAAoEwAAyAgAAC5yZGF0YQAA8BsAAEgAAAAucmRhdGEkdm9sdG1kAAAAOBwAAIgAAAAucmRhdGE'
    'kenp6ZGJnAAAAwBwAAAAiAAAudGV4dCRtbgAAAADAPgAAYgAAAC5lZGF0YQAAi1QkDItEJARWi/CF0nQTV4t8JBAr+IoMN4gORoP'
    'qAXX1X17Di0wkDIXJdCEPtkQkCFaL8WnAAQEBAVeLfCQMwekC86uLzoPhA/OqX16LRCQEw4tMJAQzwDgBdAdAgDwBAHX5w4tMJAg'
    'z0laLdCQIOBF0HVOLXCQUV4v+Syv5O9N9DIoBQogEOUGAOQB18F9bxgQyAF7Di1QkBFLosv///4tMJBAryAPCUf90JBBQ6LH///+'
    'DxBDDVYvsg+wMi0UMxkX/AIXAdQlqCsZF/jBZ6xhWagtZagpeM9JJ9/aAwjCIVA30hcB18F7/dRCNRfQDwVD/dQjonP///4PEDMn'
    'DVYvsg+wMi1UMagnGRf4AWYvCweoEg+APioCYEwAQiEQN9EmD+QJ96P91EI1F9GbHRfQweFD/dQjoWv///4PEDMnDikQkBDwgdBc'
    '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","'
    'vsize":12066,"text_rva":4096,"image_base":268435456,"relocs":[3545,3747,3763,3787,3807,3837,3873,388'
    '0,3904,3929,3936,3945,3961,3973,3983,3995,4005,4017,4027,4042,4052,4064,4074,4086,4096,4108,4118,413'
    '3,4143,4155,4165,4177,4187,4199,4209,4224,4234,4246,4256,4268,4278,4290,4300,4315,4325,4337,4347,435'
    '9,4369,4381,4391,4406,4416,4428,4438,4450,4460,4472,4482,4497,4507,4519,4529,4541,4551,4563,4573,458'
    '8,4598,4610,4620,4632,4642,4654,4664,4679,4689,4701,4711,4723,4733,4752,4758,4779,4794,4805,4833,530'
    '4,5322,5362,5403,5426,5444,5681,5753,5906,6140,6173,6319,6417,6525,6532,6553,6672,6764,6875,6987,701'
    '7,7058,7078,7109,7125,7141,7165,7176,7193,7202,7262,7293,7307,7345,7360,7527,7532,7556,7586,7871,789'
    '2,7942,8018,8025,8088,8154,8298,8318,8398,8412,8425,8441,8485,8495,8554,8562,8590,8659,8667,8687,870'
    '3,8708,8743,8749,8756,8768,8774,8788,8818,8828,8847,8870,8913,9002,9028,9044,9051,9062,9088,9108,913'
    '4,9157,9170,9243,9260,9300,9318,9369,9393,9422,9483,9506,9525,9545,9572,9588,9601,9620,9707,9715,976'
    '8,9780,9786,9804,9810,9823,9840,9846,9852,9889,9896,9978,9997,10038,10057,10089,10136,10170,10176,10'
    '182,10188,10203,10213,10220,10231,10241,10253,10280,10290,10328,10372,10411,10464,10469,10483,10496,'
    '10522,10558,10567,10594,10618,10623,10646,10662,10673,10690,10701,10746,10762,10780,10785,10798,1081'
    '2,10850,10855,10878,10900,10916,10933,10945,11049,11068,11073,11084,11101,11123,11151,11168,11180,11'
    '211,11242,11257,11294,11318,11349,11413,11425,11446,11454,11463,11483,11488,11499,11510,11521,11587,'
    '11608,11623,11636,11680,11698,11724,11735,11755,11773,11778,11786,11796,11802,11808,11814,11835,1185'
    '2,11865,11891,11901,11916,11922],"exports":{"online_dispatch":11941,"online_war":11372},"sha":"13991'
    '201b66479bdd76a8fab8ed1ecf762c7a31f85a287a0c1d1d3e829c23af1"}'
)
# --- MODULE END ---


def align(v, a):
    return (v + a - 1) // a * a


def va2file(va):
    return va - AUTO_VA + AUTO_FILE


# ------------------------------------------------------------------ PE helpers
class PE:
    def __init__(self, data):
        self.d = data
        self.pe = struct.unpack_from('<I', data, 0x3C)[0]
        assert data[self.pe:self.pe + 4] == b'PE\0\0', 'not a PE file'
        self.nsec_off = self.pe + 6
        self.nsec = struct.unpack_from('<H', data, self.nsec_off)[0]
        self.opt = self.pe + 24
        optsize = struct.unpack_from('<H', data, self.pe + 20)[0]
        assert struct.unpack_from('<H', data, self.opt)[0] == 0x10B, 'PE32 expected'
        self.image_base = struct.unpack_from('<I', data, self.opt + 28)[0]
        self.sect_align, self.file_align = struct.unpack_from('<II', data, self.opt + 32)
        self.size_of_image_off = self.opt + 56
        self.size_of_image = struct.unpack_from('<I', data, self.size_of_image_off)[0]
        self.size_of_headers = struct.unpack_from('<I', data, self.opt + 60)[0]
        self.dirs = self.opt + 96
        self.st = self.opt + optsize
        self.secs = []
        for i in range(self.nsec):
            name, vs, va, rs, ro, _, _, _, _, ch = struct.unpack_from('<8sIIIIIIHHI', data, self.st + 40 * i)
            self.secs.append(dict(name=name.rstrip(b'\0'), vs=vs, va=va, rs=rs, ro=ro, ch=ch))

    def r2o(self, rva):
        for s in self.secs:
            if s['va'] <= rva < s['va'] + max(s['vs'], s['rs']):
                return s['ro'] + rva - s['va']
        raise ValueError(hex(rva))

    def section(self, name):
        return next((s for s in self.secs if s['name'] == name), None)


# ------------------------------------------------------------------ the module (from the DLL)
def read_dll(path):
    """{'text': raw bytes of .text (virtual size), 'vsize', 'relocs': [offset in text], 'exports': {name: offset}, 'sha'}."""
    data = open(path, 'rb').read()
    pe = PE(data)
    text = pe.section(b'.text')
    assert text, 'the DLL has no .text section'
    assert len([s for s in pe.secs if s['name'] not in (b'.text', b'.reloc')]) == 0, \
        'the DLL must have only .text (+ .reloc): build it with build.cmd (/MERGE:.rdata=.text /MERGE:.data=.text /MERGE:.bss=.text)'
    raw = data[text['ro']:text['ro'] + text['rs']]
    body = raw[:text['vs']] if text['vs'] <= len(raw) else raw + b'\0' * (text['vs'] - len(raw))
    # relocations
    relocs = []
    rel = pe.section(b'.reloc')
    if rel:
        pos, end = rel['ro'], rel['ro'] + rel['rs']
        while pos + 8 <= end:
            page, size = struct.unpack_from('<II', data, pos)
            if size == 0:
                break
            for e in struct.unpack_from('<%dH' % ((size - 8) // 2), data, pos + 8):
                typ, off = e >> 12, e & 0xFFF
                if typ == 3:
                    rva = page + off
                    assert text['va'] <= rva < text['va'] + text['vs'] - 3, 'relocation outside .text'
                    relocs.append(rva - text['va'])
                elif typ != 0:
                    raise SystemExit('unexpected relocation type %d' % typ)
            pos += size
    # exports
    exp_rva, exp_size = struct.unpack_from('<II', data, pe.dirs)
    assert exp_rva, 'the DLL exports nothing'
    eo = pe.r2o(exp_rva)
    n_names = struct.unpack_from('<I', data, eo + 24)[0]
    addr_funcs, addr_names, addr_ords = struct.unpack_from('<III', data, eo + 28)
    exports = {}
    for i in range(n_names):
        name_rva = struct.unpack_from('<I', data, pe.r2o(addr_names) + 4 * i)[0]
        ordinal = struct.unpack_from('<H', data, pe.r2o(addr_ords) + 2 * i)[0]
        func_rva = struct.unpack_from('<I', data, pe.r2o(addr_funcs) + 4 * ordinal)[0]
        name = data[pe.r2o(name_rva):].split(b'\0')[0].decode()
        exports[name] = func_rva - text['va']
    for need in (DISPATCH_EXPORT, ENTRY_EXPORT):
        assert need in exports, 'export %s missing' % need
    return dict(text=body, vsize=text['vs'], text_rva=text['va'], image_base=pe.image_base,
                relocs=sorted(relocs), exports=exports, sha=hashlib.sha256(body).hexdigest())


def module():
    if MODULE is None:
        raise SystemExit('no module embedded: run online\\build.cmd --embed first')
    m = dict(MODULE)
    m['text'] = base64.b64decode(MODULE['text'])
    assert len(m['text']) == MODULE['vsize'] and hashlib.sha256(m['text']).hexdigest() == MODULE['sha']
    return m


def rebased(m, section_va):
    """The module's bytes with every absolute operand moved from the DLL's .text to `section_va`."""
    body = bytearray(m['text'])
    old_base = m['image_base'] + m['text_rva']
    for off in m['relocs']:
        v = struct.unpack_from('<I', body, off)[0]
        assert old_base <= v < old_base + m['vsize'] + 16, 'relocated operand outside the module: 0x%X' % v
        struct.pack_into('<I', body, off, v - old_base + section_va)
    return bytes(body)


# ------------------------------------------------------------------ the exe
def identify(data):
    pe = PE(data)
    auto = pe.section(b'AUTO')
    if not auto or auto['rs'] != ULTIMATE_AUTO_RAW:
        raise SystemExit('not the Council Wars / Dark Colony Ultimate build (AUTO raw size %s)' % (hex(auto['rs']) if auto else 'none'))
    return pe


def state(data):
    """'stock' (ozi applied, this fix not), 'applied', or a reason string why the fix cannot go in."""
    pe = identify(data)
    imm = data[va2file(ID_FILTER):va2file(ID_FILTER) + 3]
    tail = data[va2file(CHAIN_TAIL):va2file(CHAIN_TAIL) + 7]
    if pe.section(SECTION_NAME):
        return 'applied'
    if imm == bytes.fromhex('83FA05'):
        return 'fix ozi is not applied (the menu id filter still says 5): apply ozi first'
    if imm != bytes.fromhex('83FA07'):
        return 'unexpected menu id filter bytes %s' % imm.hex(' ')
    if tail != b'\x90' * 7:
        return 'the id chain tail at 0x405136 is not the 7 NOP bytes fix ozi leaves: %s' % tail.hex(' ')
    if not pe.section(b'.dcicon'):
        return 'fix icon is not applied (no .dcicon section): apply icon first, this fix appends its section after it'
    return 'stock'


def plan_edits(data):
    """Return (edits [(offset, old, new, note)], append offset, append bytes, summary)."""
    st = state(data)
    if st != 'stock':
        raise SystemExit('cannot apply: ' + ('already applied (the exe has a %s section)' % SECTION_NAME.decode() if st == 'applied' else st))
    pe = identify(data)
    m = module()
    last_end = max(s['va'] + align(max(s['vs'], s['rs']), pe.sect_align) for s in pe.secs)
    sec_rva = align(max(last_end, pe.size_of_image), pe.sect_align)
    sec_va = IMAGE_BASE + sec_rva
    body = rebased(m, sec_va)
    raw_off = align(len(data), pe.file_align)
    raw_size = align(len(body), pe.file_align)
    append = b'\0' * (raw_off - len(data)) + body + b'\0' * (raw_size - len(body))
    sh_off = pe.st + 40 * pe.nsec
    assert sh_off + 40 <= pe.size_of_headers and data[sh_off:sh_off + 40] == b'\0' * 40, 'no free section header slot'
    new_sh = struct.pack('<8sIIIIIIHHI', SECTION_NAME, m['vsize'], sec_rva, raw_size, raw_off, 0, 0, 0, 0, SECTION_CHARS)
    new_soi = sec_rva + align(m['vsize'], pe.sect_align)
    dispatch = sec_va + m['exports'][DISPATCH_EXPORT]
    jmp = b'\xE9' + struct.pack('<i', dispatch - (CHAIN_TAIL + 5)) + b'\x90\x90'
    edits = [
        (pe.nsec_off, data[pe.nsec_off:pe.nsec_off + 2], struct.pack('<H', pe.nsec + 1),
         'PE header: NumberOfSections %d -> %d (the new %s section)' % (pe.nsec, pe.nsec + 1, SECTION_NAME.decode())),
        (pe.size_of_image_off, data[pe.size_of_image_off:pe.size_of_image_off + 4], struct.pack('<I', new_soi),
         'optional header: SizeOfImage 0x%X -> 0x%X' % (pe.size_of_image, new_soi)),
        (sh_off, data[sh_off:sh_off + 40], new_sh,
         'section table: new header %s VA 0x%X size 0x%X, file 0x%X size 0x%X, code + read + write + execute (0x%08X), in the zero slack after the last header'
         % (SECTION_NAME.decode(), sec_rva, m['vsize'], raw_off, raw_size, SECTION_CHARS)),
        (va2file(ID_FILTER) + 2, b'\x07', b'\x08',
         'main menu id filter 0x404F9E: cmp edx,7 -> cmp edx,8 (button id 8 = ONLINE WAR reaches the id chain)'),
        (va2file(CHAIN_TAIL), b'\x90' * 7, jmp,
         'end of the id chain 0x405136: 7 NOP -> jmp online_dispatch 0x%X (+2 NOP); ids other than 8 continue at 0x40513D as before' % dispatch),
    ]
    summary = ('new section %s at file 0x%X (VA 0x%X), %d bytes appended: the ONLINE WAR module (%d bytes of code and data, '
               '%d absolute operands rebased, online_dispatch +0x%X, online_war +0x%X, module sha256 %s)'
               % (SECTION_NAME.decode(), raw_off, sec_va, len(append), m['vsize'], len(m['relocs']),
                  m['exports'][DISPATCH_EXPORT], m['exports'][ENTRY_EXPORT], m['sha'][:16]))
    return edits, len(data), append, summary


def apply_bytes(data, edits, at, append):
    out = bytearray(data)
    for off, old, new, _ in edits:
        assert out[off:off + len(old)] == old
        out[off:off + len(new)] = new
    assert len(out) == at
    return bytes(out) + append


# ------------------------------------------------------------------ data: the screen script
def set_tokens(line, changes):
    """Replace whitespace-separated tokens of a script line by 1-based number, keeping its spacing."""
    toks, n = re.findall(rb'\S+|[ \t]+', line), 0
    for i, tok in enumerate(toks):
        if tok.isspace():
            continue
        n += 1
        if n in changes:
            toks[i] = changes[n]
    return b''.join(toks)


def online_script(src):
    """LOADGE (any size) -> ONLINE: see the module docstring.  Idempotent on its own output."""
    toks = re.search(rb'(?m)^\s*list\s+0\s+\d+\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s', src)
    if not toks:
        raise SystemExit('LOADGE: no `list 0` line')
    lx, ly, lw, lh = (int(g) for g in toks.groups())
    already = lw == LIST_WIDTH_NEW   # this function's own output: nothing moves again
    if lw not in (LIST_WIDTH_OLD, LIST_WIDTH_NEW):
        raise SystemExit('LOADGE: list width %d, expected %d' % (lw, LIST_WIDTH_OLD))
    shifted = {b'scroll': {1}, b'pushb': {2, 3}, b'gadget': {7, 8}}
    # gadgets 11 (CHOA, the scope animation top right) and 14 (ENCF, the readout under the list) are erased
    # and repainted every frame over the header line's and the status line's right ends, so the ONLINE screen
    # does without them (the background art stays)
    drop = {b'label': {18}, b'pushb': {21}, b'group': {19, 20}, b'textmsg': {4, 5}, b'in_text': {W_HEADER, W_SERVER}, b'gadget': {11, 14}}
    texts = {1: b'Online War', 2: b'ENTER'}
    # below the list: the server line (host:port) and, under it, the status line (maintainer, 29 Sep 2026:
    # "server name must be mentioned as a first line of two right above [the] TLS connection")
    server_line = b'in_text  %d  0  %d  %d   %d    1  0  -  read_only' % (W_SERVER, lx, ly + lh + SERVER_DY, ROW_CHARS)
    status_line = b'in_text  %d  0  %d  %d   %d    1  0  -  read_only' % (W_STATUS, lx, ly + lh + STATUS_DY, ROW_CHARS)
    header_line = b'in_text  %d  0  %d  %d   %d    1  0  -  read_only' % (W_HEADER, lx, ly + HEADER_DY, ROW_CHARS)
    out = []
    for raw in src.split(b'\n'):
        line, cr = (raw[:-1], b'\r') if raw.endswith(b'\r') else (raw, b'')
        if re.match(rb'\s*background\s+\S+/\S+\s*$', line):               # intrf_hd/loader -> intrf_hd/onlinebg
            out.append(re.sub(rb'(\S+/)\S+\s*$', lambda mm: mm.group(1) + BG_NAME.encode(), line) + cr)
            continue
        m = re.match(rb'\s*(\w+)\s+(\d+)\s', line)
        if m:
            kind, ident = m.group(1), int(m.group(2))
            if ident in drop.get(kind, ()):
                continue
            if kind == b'list' and ident == 0:
                line = set_tokens(line, {6: b'%d' % LIST_WIDTH_NEW})
            elif ident in shifted.get(kind, ()) and not already:
                x = int(re.match(rb'\s*\w+\s+\d+\s+\d+\s+(\d+)', line).group(1))
                line = set_tokens(line, {4: b'%d' % (x + SHIFT)})
            elif kind == b'in_text' and ident == W_STATUS:
                out.append(server_line + cr)
                out.append(status_line + cr)
                out.append(header_line + cr)
                continue
            elif kind == b'label' and ident == 6:
                # the title: centred in the rounded title panel (the stock label box is left of the panel's centre)
                line = set_tokens(line, {4: b'%d' % (lx + TITLE_X_OFF), 6: b'%d' % TITLE_W})
            elif kind == b'textmsg' and ident in texts:
                num = b'%d' % ident
                line = b'textmsg ' + num + b' ' * (8 - len(num)) + texts[ident]
        out.append(line + cr)
    return b'\n'.join(out)


def grey_index(palette, value):
    """Index of the grey `value` in a 768-byte palette (nearest grey if absent)."""
    best = None
    for i in range(256):
        r, g, b = palette[3 * i:3 * i + 3]
        if r == g == b:
            d = abs(r - value)
            if best is None or d < best[0]:
                best = (d, i)
    assert best is not None, 'no grey in the palette'
    return best[1]


def frame_rects(lx, ly, lh):
    """The three frames as (x0, y0, x1, y1), x1/y1 exclusive: header + list, scroll bar with its buttons, the two text lines."""
    b = ly + lh
    ux = lx + LIST_WIDTH_NEW + SCROLL_DX
    return [
        (lx - FRAME_MARGIN_X, ly + LIST_TOP_DY, lx + LIST_WIDTH_NEW + FRAME_MARGIN_X, b + LIST_BOTTOM_DY),
        (ux - 4, ly, ux + SCROLL_W + 4, b + 2),
        (lx - FRAME_MARGIN_X, b + TEXT_TOP_DY, lx + LIST_WIDTH_NEW + FRAME_MARGIN_X, b + TEXT_BOTTOM_DY),
    ]


def draw_frame(pixels, width, x0, y0, x1, y1, greys, black):
    """Paint the frame into a row-major index buffer (a bytearray of width*height): tube, gap, interior."""
    def put(x, y, v):
        pixels[y * width + x] = v
    for y in range(y0, y1):
        for x in range(x0, x1):
            d = min(x - x0, x1 - 1 - x, y - y0, y1 - 1 - y)      # distance from the outer edge
            corner = (x in (x0, x1 - 1)) and (y in (y0, y1 - 1))
            if corner:
                continue                                          # one pixel off each corner
            put(x, y, greys[d] if d < len(greys) else black)


def online_background(gif_bytes, lx, ly, lh):
    """LOADER.GIF (any size) -> the ONLINE screen's background: the same picture with the grey frame."""
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit('Pillow is needed to draw the frame into the background (pip install pillow)')
    with Image.open(io.BytesIO(gif_bytes)) as im:
        if im.mode != 'P':
            raise SystemExit('LOADER.GIF is not a palette picture')
        palette = im.getpalette()[:768]
        w, h = im.size
        pixels = bytearray(im.tobytes())
    greys = [grey_index(palette, g) for g in FRAME_GREYS]
    black = grey_index(palette, 0)
    for rect in frame_rects(lx, ly, lh):
        draw_frame(pixels, w, *rect, greys, black)
    out = Image.frombytes('P', (w, h), bytes(pixels))
    out.putpalette(palette)
    buf = io.BytesIO()
    out.save(buf, format='GIF', version='GIF87a', interlace=False, optimize=False)   # as pad_background.py / the patcher's codec
    return buf.getvalue()


def find_ci(folder, name):
    if not os.path.isdir(folder):
        return None
    for f in os.listdir(folder):
        if f.lower() == name.lower():
            return os.path.join(folder, f)
    return None


def write_data(game, width, height, apply):
    """[(path, what)] written (or planned): the ONLINE script for the size and DEFAULT_SERVER.TXT if missing."""
    done = []
    hd = not (width == 640 and height == 480)
    import hdfolder
    folder = os.path.join(game, hdfolder.hd_folder(width, height) if hd else 'INTRFACE')   # the size's own folder (2 Oct 2026)
    src = find_ci(folder, 'LOADGE')
    if not src:
        raise SystemExit('%s: no LOADGE to derive the ONLINE screen from' % folder)
    data = online_script(open(src, 'rb').read())
    dst = find_ci(folder, 'ONLINE') or os.path.join(folder, 'ONLINE')
    if not (os.path.isfile(dst) and open(dst, 'rb').read() == data):
        done.append((dst, 'the ONLINE WAR screen (%dx%d) from %s' % (width, height, os.path.basename(src))))
        if apply:
            open(dst, 'wb').write(data)
    loader = find_ci(folder, 'LOADER.GIF')
    if not loader:
        raise SystemExit('%s: no LOADER.GIF to derive the ONLINE background from' % folder)
    m = re.search(rb'(?m)^\s*list\s+0\s+\d+\s+(\d+)\s+(\d+)\s+\d+\s+(\d+)\s', data)
    bg = online_background(open(loader, 'rb').read(), int(m.group(1)), int(m.group(2)), int(m.group(3)))
    bgdst = find_ci(folder, BG_NAME.upper() + '.GIF') or os.path.join(folder, BG_NAME.upper() + '.GIF')
    if not (os.path.isfile(bgdst) and open(bgdst, 'rb').read() == bg):
        done.append((bgdst, 'the ONLINE WAR background: LOADER.GIF with the grey frame around the text lines'))
        if apply:
            open(bgdst, 'wb').write(bg)
    cfg = find_ci(game, 'DEFAULT_SERVER.TXT')
    if not cfg:
        cfg = os.path.join(game, 'DEFAULT_SERVER.TXT')
        done.append((cfg, 'DEFAULT_SERVER.TXT (dark-colony-server.fly.dev; written only when missing)'))
        if apply:
            open(cfg, 'wb').write(DEFAULT_SERVER_TEXT.encode('latin1'))
    return done


# ------------------------------------------------------------------ CLI
def print_plan(edits, at, append, summary):
    for off, old, new, note in edits:
        print('  %s  file 0x%x %d bytes: %s -> %s' % (note, off, len(old), old.hex(' '), new.hex(' ')))
    print('  append at file 0x%x %d bytes sha256 %s: %s' % (at, len(append), hashlib.sha256(append).hexdigest(), summary))


def embed(dll_path, self_path):
    m = read_dll(dll_path)
    rec = dict(text=base64.b64encode(m['text']).decode(), vsize=m['vsize'], text_rva=m['text_rva'], image_base=m['image_base'],
               relocs=m['relocs'], exports=m['exports'], sha=m['sha'])
    src = open(self_path, encoding='utf-8').read()
    begin, end = '# --- MODULE BEGIN (written by `embed`, do not edit) ---\n', '# --- MODULE END ---'
    a, b = src.index(begin) + len(begin), src.index(end)
    text = json.dumps(rec, indent=None, separators=(',', ':'))
    # wrap the base64 so that the file stays readable
    lines = ['MODULE = json.loads(']
    while text:
        lines.append('    %r' % text[:100])
        text = text[100:]
    lines.append(')')
    src = src[:a] + '\n'.join(lines) + '\n' + src[b:]
    open(self_path, 'w', encoding='utf-8', newline='\n').write(src)
    print('embedded %s: %d bytes of code and data, %d relocations, exports %s, sha256 %s'
          % (os.path.basename(dll_path), m['vsize'], len(m['relocs']), ', '.join('%s +0x%X' % kv for kv in sorted(m['exports'].items())), m['sha'][:16]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('cmd', choices=['verify', 'plan', 'apply', 'data', 'embed'])
    ap.add_argument('target', help='the exe (verify/plan/apply), the game folder (data) or the DLL (embed)')
    ap.add_argument('--game', help='apply: also write the ONLINE screen and DEFAULT_SERVER.TXT into this game folder')
    ap.add_argument('--width', type=int, default=1024)
    ap.add_argument('--height', type=int, default=768)
    ap.add_argument('--output', help='apply: write here instead of in place (no backup then)')
    a = ap.parse_args()
    if a.cmd == 'embed':
        return embed(a.target, os.path.abspath(__file__))
    if a.cmd == 'data':
        for path, what in write_data(a.target, a.width, a.height, True):
            print('  written %s: %s' % (path, what))
        return None
    data = open(a.target, 'rb').read()
    if a.cmd == 'verify':
        st = state(data)
        if st == 'applied':
            pe = PE(data)
            s = pe.section(SECTION_NAME)
            body = data[s['ro']:s['ro'] + s['vs']]
            m = module() if MODULE else None
            same = m and rebased(m, IMAGE_BASE + s['va']) == body
            print('%s: online APPLIED (%s at VA 0x%X, %d bytes%s)' % (a.target, SECTION_NAME.decode(), IMAGE_BASE + s['va'], s['vs'],
                                                                       '; = the embedded module' if same else '; a DIFFERENT module build' if m else ''))
        else:
            print('%s: online NOT applied (%s)' % (a.target, st))
        return None
    edits, at, append, summary = plan_edits(data)
    print('%s: Dark Colony Ultimate, fix online: %d header/code edits + %d bytes appended' % (a.target, len(edits), len(append)))
    print_plan(edits, at, append, summary)
    if a.cmd == 'plan':
        if a.game:
            for path, what in write_data(a.game, a.width, a.height, False):
                print('  would write %s: %s' % (path, what))
        return None
    out = apply_bytes(data, edits, at, append)
    if a.output:
        open(a.output, 'wb').write(out)
        print('written %s (%d bytes)' % (a.output, len(out)))
    else:
        bak = a.target + '.online.bak'
        if not os.path.exists(bak):
            open(bak, 'wb').write(data)
        open(a.target, 'wb').write(out)
        print('written %s (%d bytes); backup %s' % (a.target, len(out), bak))
    if a.game:
        for path, what in write_data(a.game, a.width, a.height, True):
            print('  written %s: %s' % (path, what))
    return None


if __name__ == '__main__':
    main()

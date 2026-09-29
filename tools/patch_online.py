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
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAP////9NQVAgICAgICAgICAgICAgICAgVEVSUkFJTiAgU0VBVFMgUExBWUVSUyB'
    'CT1RTIFNUQVRVUwAAAGludHJmX2hkL29ubGluAABpbnRyZmFjZS9vbmxpbgAAaW50cmZfaGRcT05MSU5FAEJNT25saW5lAAAAADE'
    'yNy4wLjAuMQAAADAxMjM0NTY3ODlBQkNERUYAAAAAa2VybmVsMzIuZGxsAAAAAHdzMl8zMi5kbGwAAHNlY3VyMzIuZGxsAENyZWF'
    '0ZUZpbGVBAFJlYWRGaWxlAAAAAENsb3NlSGFuZGxlAEdldFRpY2tDb3VudAAAAABDcmVhdGVUaHJlYWQAAAAAU2xlZXAAAABHZXR'
    'MYXN0RXJyb3IAAAAAVmlydHVhbEFsbG9jAAAAAFdyaXRlRmlsZQAAAFNldEZpbGVQb2ludGVyAABXU0FTdGFydHVwAABzb2NrZXQ'
    'AAGNvbm5lY3QAc2VuZAAAAAByZWN2AAAAAHNlbGVjdAAAY2xvc2Vzb2NrZXQAZ2V0aG9zdGJ5bmFtZQAAAGluZXRfYWRkcgAAAGh'
    '0b25zAAAAbnRvaHMAAABiaW5kAAAAAGxpc3RlbgAAYWNjZXB0AABnZXRzb2NrbmFtZQBXU0FHZXRMYXN0RXJyb3IAX19XU0FGREl'
    'zU2V0AAAAAEFjcXVpcmVDcmVkZW50aWFsc0hhbmRsZUEAAABJbml0aWFsaXplU2VjdXJpdHlDb250ZXh0QQAAUXVlcnlDb250ZXh'
    '0QXR0cmlidXRlc0EARW5jcnlwdE1lc3NhZ2UAAERlY3J5cHRNZXNzYWdlAABGcmVlQ29udGV4dEJ1ZmZlcgAAAERlbGV0ZVNlY3V'
    'yaXR5Q29udGV4dAAAAEZyZWVDcmVkZW50aWFsc0hhbmRsZQAAAHJlc29sdmU6IHdzMl8zMi5kbGwgbm90IGxvYWRlZAAAcmVzb2x'
    '2ZTogc2VjdXIzMi5kbGwgbm90IGxvYWRlZAByZXNvbHZlOiBhIGZ1bmN0aW9uIGlzIG1pc3NpbmcgKHNlZSB0aGUgVyB0YWJsZSB'
    'pbiBvbmxpbmUuYykAAAAAT05MSU5FLkxPRwAADQoAAFRMUyBlbmNyeXB0IGZhaWxlZCAAVExTIGRlY3J5cHQgZmFpbGVkIABUTFM'
    'gcmVuZWdvdGlhdGlvbiByZXF1ZXN0ZWQATWljcm9zb2Z0IFVuaWZpZWQgU2VjdXJpdHkgUHJvdG9jb2wgUHJvdmlkZXIAAAAAVEx'
    'TIGNyZWRlbnRpYWxzIGZhaWxlZCAAVExTIGhhbmRzaGFrZSB0aW1lZCBvdXQAY29ubmVjdGlvbiBjbG9zZWQgZHVyaW5nIHRoZSB'
    'UTFMgaGFuZHNoYWtlAABzZW5kIGZhaWxlZCBkdXJpbmcgdGhlIFRMUyBoYW5kc2hha2UAAAAAVExTIGhhbmRzaGFrZSBmYWlsZWQ'
    'gAAAAIChjZXJ0aWZpY2F0ZSBub3QgdHJ1c3RlZCkAACAoY2VydGlmaWNhdGUgbmFtZSBtaXNtYXRjaCkAAAAAIChub3QgYSBUTFM'
    'gc2VydmVyOyB0cnkgYHBsYWluYCkAAAAAVExTIHN0cmVhbSBzaXplcyBmYWlsZWQAREVGQVVMVF9TRVJWRVIuVFhUAABERUZBVUx'
    'UX1NFUlZFUi5UWFQgbm90IGZvdW5kIGJlc2lkZSB0aGUgZ2FtZQAAAABwbGFpbgAAAG5vdGxzAAAAREVGQVVMVF9TRVJWRVIuVFh'
    'UIG5hbWVzIG5vIHNlcnZlciBhZGRyZXNzAABQcm90b2NvbCBlcnJvcjogYmFkIGZyYW1lIGZyb20gdGhlIHJlbGF5AAAAAFByb3R'
    'vY29sIGVycm9yOiBmcmFtZSB0b28gbG9uZwAAcHJveHk6IGFjY2VwdCBmYWlsZWQAAAAAcHJveHk6IHRoZSBnYW1lIGNvbm5lY3R'
    'lZAAAAHByb3h5OiBjbG9zaW5nIGJvdGggY29ubmVjdGlvbnMAV1NBU3RhcnR1cCBmYWlsZWQAAABDYW5ub3QgcmVzb2x2ZSAAc29'
    'ja2V0KCkgZmFpbGVkAENhbm5vdCBjb25uZWN0IHRvIAAAOgAAACAoZXJyb3IgAAAAACkAAABzY3JlZW46IAAAAABzY3JlZW4gbG9'
    'hZGVkAAAAU2VydmVyOiBub25lIChzZWUgREVGQVVMVF9TRVJWRVIuVFhUKQAAAFNlcnZlcjogAAAAAENvbm5lY3RpbmcgKG5vIGV'
    'uY3J5cHRpb24pLi4uAAAAQ29ubmVjdGluZyAoVExTKS4uLgBjb25uZWN0ZWQgKHBsYWluKQAAAGNvbm5lY3RlZCAoVExTKQBDb25'
    'uZWN0ZWQuIFNlbGVjdCBhIHJvb20gYW5kIHByZXNzIEVOVEVSLgAAAENvbm5lY3RlZCAoVExTKS4gU2VsZWN0IGEgcm9vbSBhbmQ'
    'gcHJlc3MgRU5URVIuAENvbm5lY3Rpb24gbG9zdC4AAAAAY29ubmVjdDogAAAATm90IGNvbm5lY3RlZC4gUHJlc3MgQkFDSyBhbmQ'
    'gdHJ5IGFnYWluLgAAAABTZWxlY3QgYSByb29tIGZpcnN0LgAAAABFbnRlcmluZyByb29tIAAALi4uAC0tLSBPTkxJTkUgV0FSIHB'
    'yZXNzZWQAAFZpcnR1YWxBbGxvYyBmYWlsZWQAY29uZmlnOiAAAAAAY29uZmlnOiBob3N0IAAAAGNvbmZpZzogcG9ydCAAAABjb25'
    'maWc6IHBsYWluIAAAYmFjayB0byB0aGUgbWVudQAAAABFTlRFUklORyBzbG90IAAAbG9vcGJhY2sgbGlzdGVuZXIgZmFpbGVkAAA'
    'AAGxvb3BiYWNrIHBvcnQgAABjYWxsaW5nIHRoZSBnYW1lJ3MgbmV0d29yayBlbnRyeSwgdGNwIG5ldCBvYmplY3QgAAAAbmV0d29'
    'yayBlbnRyeSByZXR1cm5lZCAAAAAAAE6bu2oAAAAADQAAAIgAAAAIHAAACA4AABgAAAADgAOAAAAAAAAAAAD4GwAAEAAAAAAQAAD'
    'ECwAAkRwAAA8hAAAAAAAAABAAAAQDAAAuYnNzAAAAAAQTAAAEAAAALmRhdGEAAAAIEwAA2AgAAC5yZGF0YQAA4BsAACgAAAAucmR'
    'hdGEkdm9sdG1kAAAACBwAAIgAAAAucmRhdGEkenp6ZGJnAAAAkBwAABAhAAAudGV4dCRtbgAAAACgPQAAYgAAAC5lZGF0YQAAAIt'
    'UJAyLRCQEVovwhdJ0E1eLfCQQK/iKDDeIDkaD6gF19V9ew4tMJAyFyXQhD7ZEJAhWi/FpwAEBAQFXi3wkDMHpAvOri86D4QPzql9'
    'ei0QkBMOLTCQEM8A4AXQHQIA8AQB1+cOLTCQIM9JWi3QkCDgRdB1Ti1wkFFeL/ksr+TvTfQyKAUKIBDlBgDkAdfBfW8YEMgBew4t'
    'UJARS6LL///+LTCQQK8gDwlH/dCQQUOix////g8QQw1WL7IPsDItFDMZF/wCFwHUJagrGRf4wWesYVmoLWWoKXjPSSff2gMIwiFQ'
    'N9IXAdfBe/3UQjUX0A8FQ/3UI6Jz///+DxAzJw1WL7IPsDItVDGoJxkX+AFmLwsHqBIPgD4qAiBMAEIhEDfRJg/kCfej/dRCNRfR'
    'mx0X0MHhQ/3UI6Fr///+DxAzJw4pEJAQ8IHQXPAl0EzwNdA88CnQLPAx0BzwLdAMzwMMzwEDDU1VWi3QkFFeLfCQUK/6KFDeKHo1'
    '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'
    'ubGluZV93YXIA","vsize":11778,"text_rva":4096,"image_base":268435456,"relocs":[3498,3700,3716,3740,37'
    '60,3790,3826,3833,3857,3882,3889,3898,3914,3926,3936,3948,3958,3970,3980,3995,4005,4017,4027,4039,40'
    '49,4061,4071,4086,4096,4108,4118,4130,4140,4152,4162,4177,4187,4199,4209,4221,4231,4243,4253,4268,42'
    '78,4290,4300,4312,4322,4334,4344,4359,4369,4381,4391,4403,4413,4425,4435,4450,4460,4472,4482,4494,45'
    '04,4516,4526,4541,4551,4563,4573,4585,4595,4607,4617,4632,4642,4654,4664,4676,4686,4705,4711,4732,47'
    '47,4758,4786,5257,5275,5315,5356,5379,5397,5634,5706,5859,6093,6126,6272,6370,6478,6485,6506,6625,67'
    '17,6828,6940,6970,7011,7031,7062,7078,7094,7118,7129,7146,7155,7215,7246,7260,7298,7313,7480,7485,75'
    '09,7539,7824,7845,7895,7971,7978,8041,8107,8251,8271,8351,8365,8378,8394,8438,8448,8507,8515,8543,86'
    '12,8620,8640,8656,8661,8696,8702,8709,8721,8727,8741,8771,8781,8800,8823,8866,8955,8981,8997,9004,90'
    '15,9041,9061,9087,9110,9123,9196,9213,9253,9271,9322,9346,9375,9436,9459,9478,9498,9525,9541,9554,95'
    '73,9660,9668,9721,9733,9739,9757,9763,9776,9793,9799,9805,9831,9878,9908,9914,9920,9926,9936,9946,99'
    '53,9964,9974,9986,10013,10023,10061,10105,10144,10197,10202,10216,10229,10255,10291,10300,10327,1035'
    '1,10356,10379,10395,10406,10423,10434,10479,10495,10513,10518,10531,10545,10579,10584,10606,10623,10'
    '645,10661,10678,10690,10784,10803,10808,10819,10836,10858,10886,10909,10921,10943,10958,10995,11019,'
    '11050,11114,11126,11147,11155,11164,11184,11189,11200,11211,11222,11288,11309,11324,11337,11381,1139'
    '9,11425,11436,11456,11474,11479,11487,11497,11503,11509,11515,11536,11553,11566,11592,11602,11617,11'
    '623],"exports":{"online_dispatch":11642,"online_war":11073},"sha":"ea7b3fe77142075d457d931c7a31357a1'
    '86c4cca90964cfd45dde9cb6a02a995"}'
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
    folder = os.path.join(game, 'INTRF_HD' if hd else 'INTRFACE')
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

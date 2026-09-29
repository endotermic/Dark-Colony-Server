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
the buttons ENTER / BACK, the save-mode widgets and the scope animation over the header removed - written as INTRF_HD/ONLINE from
INTRF_HD/LOADGE at the HD sizes and as INTRFACE/ONLINE from INTRFACE/LOADGE at 640x480 (the module
probes for intrf_hd\\ONLINE, so one exe serves every size); and DEFAULT_SERVER.TXT beside the exe,
written only when it is missing (a player's edit is never overwritten).  The patcher
(Apply-DarkColonyPatches.ps1, Write-OnlineScreen) does the same in PowerShell.
"""
import argparse
import base64
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
W_STATUS, W_HEADER = 17, 30

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
    'hZGVkAAAAQ29ubmVjdGluZyB0byAAACAobm8gZW5jcnlwdGlvbikuLi4AIChUTFMpLi4uAAAAY29ubmVjdGVkIChwbGFpbikAAAB'
    'jb25uZWN0ZWQgKFRMUykAQ29ubmVjdGVkLiBTZWxlY3QgYSByb29tIGFuZCBwcmVzcyBFTlRFUi4AAABDb25uZWN0ZWQgKFRMUyk'
    'uIFNlbGVjdCBhIHJvb20gYW5kIHByZXNzIEVOVEVSLgBDb25uZWN0aW9uIGxvc3QuAAAAAGNvbm5lY3Q6IAAAAE5vdCBjb25uZWN'
    '0ZWQuIFByZXNzIEJBQ0sgYW5kIHRyeSBhZ2Fpbi4AAAAAU2VsZWN0IGEgcm9vbSBmaXJzdC4AAAAARW50ZXJpbmcgcm9vbSAAAC4'
    'uLgAtLS0gT05MSU5FIFdBUiBwcmVzc2VkAABWaXJ0dWFsQWxsb2MgZmFpbGVkAGNvbmZpZzogAAAAAGNvbmZpZzogaG9zdCAAAAB'
    'jb25maWc6IHBvcnQgAAAAY29uZmlnOiBwbGFpbiAAAGJhY2sgdG8gdGhlIG1lbnUAAAAARU5URVJJTkcgc2xvdCAAAGxvb3BiYWN'
    'rIGxpc3RlbmVyIGZhaWxlZAAAAABsb29wYmFjayBwb3J0IAAAY2FsbGluZyB0aGUgZ2FtZSdzIG5ldHdvcmsgZW50cnksIHRjcCB'
    'uZXQgb2JqZWN0IAAAAG5ldHdvcmsgZW50cnkgcmV0dXJuZWQgAAAAAAAbi7tqAAAAAA0AAACIAAAA0BsAANANAAAYAAAAA4ADgAA'
    'AAAAAAAAAwBsAABAAAAAAEAAAjAsAAFkcAADHIAAAAAAAAAAQAAAEAwAALmJzcwAAAAAEEwAABAAAAC5kYXRhAAAACBMAAKAIAAA'
    'ucmRhdGEAAKgbAAAoAAAALnJkYXRhJHZvbHRtZAAAANAbAACIAAAALnJkYXRhJHp6emRiZwAAAFgcAADIIAAALnRleHQkbW4AAAA'
    'AID0AAGIAAAAuZWRhdGEAAACLVCQMi0QkBFaL8IXSdBNXi3wkECv4igw3iA5Gg+oBdfVfXsOLTCQMhcl0IQ+2RCQIVovxacABAQE'
    'BV4t8JAzB6QLzq4vOg+ED86pfXotEJATDi0wkBDPAOAF0B0CAPAEAdfnDi0wkCDPSVot0JAg4EXQdU4tcJBRXi/5LK/k7030MigF'
    'CiAQ5QYA5AHXwX1vGBDIAXsOLVCQEUuiy////i0wkECvIA8JR/3QkEFDosf///4PEEMNVi+yD7AyLRQzGRf8AhcB1CWoKxkX+MFn'
    'rGFZqC1lqCl4z0kn39oDCMIhUDfSFwHXwXv91EI1F9APBUP91COic////g8QMycNVi+yD7AyLVQxqCcZF/gBZi8LB6gSD4A+KgIg'
    'TABCIRA30SYP5An3o/3UQjUX0ZsdF9DB4UP91COha////g8QMycOKRCQEPCB0FzwJdBM8DXQPPAp0CzwMdAc8C3QDM8DDM8BAw1N'
    'VVot0JBRXi3wkFCv+ihQ3ih6NSr+NQiCA+RkPtugPtsKNU78PR+iNQyAPtsiA+hkPtsMPR8iJbCQUi8U6wXUMhMB0A0brxjPAQOs'
    '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","vsize":11650,"text_rva":4096,"image_base":268435456,'
    '"relocs":[3442,3644,3660,3684,3704,3734,3770,3777,3801,3826,3833,3842,3858,3870,3880,3892,3902,3914,'
    '3924,3939,3949,3961,3971,3983,3993,4005,4015,4030,4040,4052,4062,4074,4084,4096,4106,4121,4131,4143,'
    '4153,4165,4175,4187,4197,4212,4222,4234,4244,4256,4266,4278,4288,4303,4313,4325,4335,4347,4357,4369,'
    '4379,4394,4404,4416,4426,4438,4448,4460,4470,4485,4495,4507,4517,4529,4539,4551,4561,4576,4586,4598,'
    '4608,4620,4630,4649,4655,4676,4691,4702,4730,5201,5219,5259,5300,5323,5341,5578,5650,5803,6037,6070,'
    '6216,6314,6422,6429,6450,6569,6661,6772,6884,6914,6955,6975,7006,7022,7038,7062,7073,7090,7099,7159,'
    '7190,7204,7242,7257,7424,7429,7453,7483,7768,7789,7839,7915,7922,7985,8051,8195,8215,8295,8309,8322,'
    '8338,8382,8392,8451,8459,8487,8556,8564,8584,8600,8605,8640,8646,8653,8665,8671,8685,8715,8725,8744,'
    '8767,8810,8899,8925,8941,8948,8959,8985,9005,9031,9054,9067,9140,9157,9197,9215,9266,9290,9319,9380,'
    '9403,9422,9442,9469,9485,9498,9517,9604,9612,9665,9677,9683,9701,9707,9720,9737,9743,9749,9775,9816,'
    '9846,9852,9858,9864,9874,9884,9891,9902,9912,9924,9951,9961,10019,10024,10040,10055,10060,10083,1009'
    '9,10104,10118,10128,10157,10186,10195,10222,10243,10248,10271,10287,10298,10315,10326,10368,10384,10'
    '402,10407,10420,10434,10468,10473,10495,10512,10534,10550,10567,10579,10673,10692,10697,10708,10725,'
    '10747,10775,10798,10810,10832,10844,10878,10902,10933,10994,11006,11027,11035,11044,11064,11069,1108'
    '0,11091,11102,11168,11189,11204,11217,11261,11279,11305,11316,11336,11354,11359,11367,11377,11383,11'
    '389,11395,11416,11433,11446,11472,11482,11497,11503],"exports":{"online_dispatch":11522,"online_war"'
    ':10953},"sha":"823d7ef08b686000e688c661944f56b4d848e1471bc0fd5a5ed73b30026c6e91"}'
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
    # gadget 11 (CHOA, the small scope animation top right) is erased and repainted every frame over the
    # header line's right end, so the ONLINE screen does without it (the background art stays)
    drop = {b'label': {18}, b'pushb': {21}, b'group': {19, 20}, b'textmsg': {4, 5}, b'in_text': {W_HEADER}, b'gadget': {11}}
    texts = {1: b'Online War', 2: b'ENTER'}
    status_line = b'in_text  %d  0  %d  %d   %d    1  0  -  read_only' % (W_STATUS, lx, ly + lh + 8, ROW_CHARS)
    header_line = b'in_text  %d  0  %d  %d   %d    1  0  -  read_only' % (W_HEADER, lx, ly - 18, ROW_CHARS)
    out = []
    for raw in src.split(b'\n'):
        line, cr = (raw[:-1], b'\r') if raw.endswith(b'\r') else (raw, b'')
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
                out.append(status_line + cr)
                out.append(header_line + cr)
                continue
            elif kind == b'textmsg' and ident in texts:
                num = b'%d' % ident
                line = b'textmsg ' + num + b' ' * (8 - len(num)) + texts[ident]
        out.append(line + cr)
    return b'\n'.join(out)


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

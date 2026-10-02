#!/usr/bin/env python3
"""patch_online.py - fix `online`: the ONLINE WAR button of Dark Colony Ultimate (29 Sep 2026).

    python patch_online.py verify EXE
    python patch_online.py plan   EXE
    python patch_online.py apply  EXE [--game DIR --width W --height H] [--output OUT]
    python patch_online.py data   DIR --width W --height H        (the two screen scripts, their backgrounds + DEFAULT_SERVER.TXT only)
    python patch_online.py bank   DIR                             (developer: HD_SRC\\KNOBR.SPR = KNOBE + the empty radio box 149 + its grey twin 150)
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
   since fix ozi) becomes `cmp edx,9` so the button ids 8 = ONLINE WAR and 9 = REPLAY ONLINE GAME
   (2 Oct 2026) reach the id chain, and the seven NOP bytes at the end of that chain
   (0x405136..0x40513C, left by fix ozi's Dark Colony handlers; the id-7 branch falls through them
   into the loop head 0x40513D) become `jmp online_dispatch` + 2 NOP.  The dispatch (in the section)
   runs `online_war` for id 8, `replay_game` for id 9 and jumps to the loop head for anything else,
   so the fall-through behaves as before.

REPLAY ONLINE GAME (2 Oct 2026; plan 21, protocol doc 4.5, display doc 10.65): the second entry point of
the same module.  Its screen REPLAYE is ONLINE with the list narrowed to 40 columns (320 px) and, right
of the scroll bar, a participant pane - eight `checkb` boxes (the lobby's READY boxes, KNOBE cells 9 /
8) with a read-only name field each, under the heading WATCH AS - in a third grey frame; the dialogue
is 0x55 RLIST / 0x56 REPLAYS / 0x57 REPLAY / 0x58 RPLAY / 0x59 REPLAYING and the hand-over after
REPLAYING is the ENTERING one.  The relay formats the rows and the column header.

Data (`apply --game`, `data`): the screen script ONLINE - the LOAD GAME picker LOADGE with the list
widened from 392 to 448 px (56 columns of MFONTO5, 8 px per column), scroll bar / UP / DOWN 56 px further
right, a read-only header line above the list and a status line below it, the title "Online War",
the buttons ENTER / BACK, a server line above the status line, the title centred in its panel, the
save-mode widgets and the two animations over the header and status lines removed - written as
INTRF_HD/ONLINE from INTRF_HD/LOADGE at the HD sizes and as INTRFACE/ONLINE from INTRFACE/LOADGE at
640x480 (the module probes for <folder>\\ONLINE, so one exe serves every size), with its background
ONLINEBG.GIF = LOADER.GIF plus grey frames around the header + list, the scroll bar and the two text lines (Pillow);
REPLAYE + REPLAYBG.GIF the same way (replay_script / frame_rects(replay=True): list 40 columns, the pane frame);
and DEFAULT_SERVER.TXT beside the exe, written only when it is missing (a player's edit is never
overwritten).  The patcher (Apply-DarkColonyPatches.ps1, Write-OnlineScreen) does the same in PowerShell.
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
ID_FILTER = 0x404F9E                 # cmp edx, imm8 (83 FA xx): 05 stock, 07 after ozi, 09 after this fix (08 from 29 Sep to 2 Oct 2026)
ID_FILTER_NEW = 9                    # the highest button id: 8 = ONLINE WAR, 9 = REPLAY ONLINE GAME
CHAIN_TAIL = 0x405136                # 7 NOP bytes -> jmp dispatch + 2 NOP
LOOP_HEAD = 0x40513D
DISPATCH_EXPORT, ENTRY_EXPORT, REPLAY_EXPORT = 'online_dispatch', 'online_war', 'replay_game'
LIST_WIDTH_OLD, LIST_WIDTH_NEW = 392, 448
SHIFT = LIST_WIDTH_NEW - LIST_WIDTH_OLD
ROW_CHARS = 56                       # 448 px / 8 px per column (MFONTO5: 7 px glyphs on an 8 px advance)
W_STATUS, W_HEADER, W_SERVER = 17, 30, 31   # status line, column header, the server line above the status
# REPLAY ONLINE GAME (2 Oct 2026): the list is 40 columns (320 px), the scroll bar follows it 72 px further left than
# in the stock LOADGE, and the participant pane sits right of the scroll bar: eight `checkb` rows (KNOBE cells 9 off /
# 8 on, 27x17 as the lobby's READY boxes) 30 px apart from list top + 6, a 13-column read-only name right of each box,
# the heading WATCH AS above them at the header line's height; the pane frame x lx+368 .. lx+518 (150 px, inside the
# 640-wide form at every size: 638 of 640 at 640x480) from the list frame's top to its bottom
RLIST_WIDTH, RROW_CHARS = 320, 40
RSHIFT = RLIST_WIDTH - LIST_WIDTH_OLD        # -72
W_RADIO0, W_RNAME0, W_RPANE = 32, 40, 48
PANE_X0, PANE_X1 = 368, 518                  # the pane frame, relative to the list's x
RADIO_DX, RADIO_DY, RADIO_PITCH = 376, 6, 30 # the boxes: x relative to the list's x, y relative to the list's top
# The boxes come from HD_SRC\KNOBR.SPR (`pictures hd_src/knobr`): KNOBE's 149 cells plus cell 149 = KNOBE's cell 9 without
# the "?" glyph (maintainer, 2 Oct 2026: "client radio buttons must be empty instead of question mark"); the ticked state is
# KNOBE's green cross, cell 8.  The bank is a shipped input of the patcher (HD_SRC, like the console banks), made by `bank`.
RADIO_W, RADIO_H, RADIO_CELLS = 27, 17, (149, 8)
# Slot 0 is the lobby host (its client sets map and options), so it can never be a viewer's seat: its box shows
# cell 150 = the empty box in greys for BOTH states - dimmed, and a click (which the engine toggles and the module
# undoes) changes nothing visible (maintainer, 2 Oct 2026: "slot 0 must contain correct name of ai master as
# before, but it's radio button must be grayed out and unclickable").  The greys are the screen palette's
# (LOADER.GIF: 82 / 65 / 41 / 35 / 23 / 11 at indices 49 / 55 / 62 / 65 / 66 / 67), the reds they replace are
# KNOBE's 80 / 97 / 98 / 99 / 100 / 101 / 201 / 243.
HOST_SLOT = 0
RADIO_CELLS_HOST = (150, 150)
DIM_MAP = {80: 49, 97: 55, 98: 62, 99: 65, 100: 66, 101: 67, 201: 55, 243: 67}
BANK_NAME, BANK_SRC, EMPTY_CELL_FROM = 'knobr', 'KNOBE.SPR', 9
PICTURES_R = b"pictures hd_src/knobr"
RNAME_DX, RNAME_DY, RNAME_CHARS = 407, 3, 13 # the names: x relative to the list's x, y relative to the box, columns
BG_NAME_R = 'replaybg'
TEXTS_R = {1: b'Replay Online Game', 2: b'REPLAY'}
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
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAP////9NQVAgICAgICAgICAgICAgICAgVEVSUkFJTiAgU0V'
    'BVFMgUExBWUVSUyBCT1RTIFNUQVRVUwAAAFdBVENIIEFTAAAAAGludHJmYWNlL29ubGluAABpbnRyZmFjZS9yZXBsYXkAL29ubGl'
    'uAABcT05MSU5FAC9yZXBsYXkAXFJFUExBWUUAAAAAQk1PbmxpbmUAAAAAMTI3LjAuMC4xAAAAMDEyMzQ1Njc4OUFCQ0RFRgAAAAB'
    'rZXJuZWwzMi5kbGwAAAAAd3MyXzMyLmRsbAAAc2VjdXIzMi5kbGwAQ3JlYXRlRmlsZUEAUmVhZEZpbGUAAAAAQ2xvc2VIYW5kbGU'
    'AR2V0VGlja0NvdW50AAAAAENyZWF0ZVRocmVhZAAAAABTbGVlcAAAAEdldExhc3RFcnJvcgAAAABWaXJ0dWFsQWxsb2MAAAAAV3J'
    'pdGVGaWxlAAAAU2V0RmlsZVBvaW50ZXIAAFdTQVN0YXJ0dXAAAHNvY2tldAAAY29ubmVjdABzZW5kAAAAAHJlY3YAAAAAc2VsZWN'
    '0AABjbG9zZXNvY2tldABnZXRob3N0YnluYW1lAAAAaW5ldF9hZGRyAAAAaHRvbnMAAABudG9ocwAAAGJpbmQAAAAAbGlzdGVuAAB'
    'hY2NlcHQAAGdldHNvY2tuYW1lAFdTQUdldExhc3RFcnJvcgBfX1dTQUZESXNTZXQAAAAAQWNxdWlyZUNyZWRlbnRpYWxzSGFuZGx'
    'lQQAAAEluaXRpYWxpemVTZWN1cml0eUNvbnRleHRBAABRdWVyeUNvbnRleHRBdHRyaWJ1dGVzQQBFbmNyeXB0TWVzc2FnZQAARGV'
    'jcnlwdE1lc3NhZ2UAAEZyZWVDb250ZXh0QnVmZmVyAAAARGVsZXRlU2VjdXJpdHlDb250ZXh0AAAARnJlZUNyZWRlbnRpYWxzSGF'
    'uZGxlAAAAcmVzb2x2ZTogd3MyXzMyLmRsbCBub3QgbG9hZGVkAAByZXNvbHZlOiBzZWN1cjMyLmRsbCBub3QgbG9hZGVkAHJlc29'
    'sdmU6IGEgZnVuY3Rpb24gaXMgbWlzc2luZyAoc2VlIHRoZSBXIHRhYmxlIGluIG9ubGluZS5jKQAAAABPTkxJTkUuTE9HAAANCgA'
    'AVExTIGVuY3J5cHQgZmFpbGVkIABUTFMgZGVjcnlwdCBmYWlsZWQgAFRMUyByZW5lZ290aWF0aW9uIHJlcXVlc3RlZABNaWNyb3N'
    'vZnQgVW5pZmllZCBTZWN1cml0eSBQcm90b2NvbCBQcm92aWRlcgAAAABUTFMgY3JlZGVudGlhbHMgZmFpbGVkIABUTFMgaGFuZHN'
    'oYWtlIHRpbWVkIG91dABjb25uZWN0aW9uIGNsb3NlZCBkdXJpbmcgdGhlIFRMUyBoYW5kc2hha2UAAHNlbmQgZmFpbGVkIGR1cml'
    'uZyB0aGUgVExTIGhhbmRzaGFrZQAAAABUTFMgaGFuZHNoYWtlIGZhaWxlZCAAAAAgKGNlcnRpZmljYXRlIG5vdCB0cnVzdGVkKQA'
    'AIChjZXJ0aWZpY2F0ZSBuYW1lIG1pc21hdGNoKQAAAAAgKG5vdCBhIFRMUyBzZXJ2ZXI7IHRyeSBgcGxhaW5gKQAAAABUTFMgc3R'
    'yZWFtIHNpemVzIGZhaWxlZABERUZBVUxUX1NFUlZFUi5UWFQAAERFRkFVTFRfU0VSVkVSLlRYVCBub3QgZm91bmQgYmVzaWRlIHR'
    'oZSBnYW1lAAAAAHBsYWluAAAAbm90bHMAAABERUZBVUxUX1NFUlZFUi5UWFQgbmFtZXMgbm8gc2VydmVyIGFkZHJlc3MAAFByb3R'
    'vY29sIGVycm9yOiBiYWQgZnJhbWUgZnJvbSB0aGUgcmVsYXkAAAAAUHJvdG9jb2wgZXJyb3I6IGZyYW1lIHRvbyBsb25nAABtb25'
    'leSBmaXg6IHNpbSBzdGF0ZSAAAABtb25leSBmaXg6IGxvY2FsIHBsYXllciAAAAAAbW9uZXkgZml4OiBtb25leSAAAABtb25leSB'
    'maXg6IHNwZW50IAAAAHByb3h5OiBhY2NlcHQgZmFpbGVkAAAAAHByb3h5OiB0aGUgZ2FtZSBjb25uZWN0ZWQAAABwcm94eTogY2x'
    'vc2luZyBib3RoIGNvbm5lY3Rpb25zAFdTQVN0YXJ0dXAgZmFpbGVkAAAAQ2Fubm90IHJlc29sdmUgAHNvY2tldCgpIGZhaWxlZAB'
    'DYW5ub3QgY29ubmVjdCB0byAAADoAAAAgKGVycm9yIAAAAAApAAAAc2NyZWVuOiAAAAAAc2NyZWVuIGxvYWRlZAAAAFNlcnZlcjo'
    'gbm9uZSAoc2VlIERFRkFVTFRfU0VSVkVSLlRYVCkAAABTZXJ2ZXI6IAAAAABDb25uZWN0aW5nIChubyBlbmNyeXB0aW9uKS4uLgA'
    'AAENvbm5lY3RpbmcgKFRMUykuLi4AY29ubmVjdGVkIChwbGFpbikAAABjb25uZWN0ZWQgKFRMUykAQ29ubmVjdGVkLiBQaWNrIGE'
    'gYmF0dGxlLCB0aWNrIGEgcGxheWVyLCBwcmVzcyBSRVBMQVkuAABDb25uZWN0ZWQgKFRMUykuIFBpY2sgYSBiYXR0bGUsIHRpY2s'
    'gYSBwbGF5ZXIsIHByZXNzIFJFUExBWS4AAAAAQ29ubmVjdGVkLiBTZWxlY3QgYSByb29tIGFuZCBwcmVzcyBFTlRFUi4AAABDb25'
    'uZWN0ZWQgKFRMUykuIFNlbGVjdCBhIHJvb20gYW5kIHByZXNzIEVOVEVSLgBDb25uZWN0aW9uIGxvc3QuAAAAAGNvbm5lY3Q6IAA'
    'AAE5vIHJlY29yZGVkIGJhdHRsZSBvbiB0aGUgc2VydmVyIHlldC4AAAByZXBsYXkgbGlzdDogZW50cmllcyAAAABOb3QgY29ubmV'
    'jdGVkLiBQcmVzcyBCQUNLIGFuZCB0cnkgYWdhaW4uAAAAAFNlbGVjdCBhIHJvb20gZmlyc3QuAAAAAEVudGVyaW5nIHJvb20gAAA'
    'uLi4AU2VsZWN0IGEgYmF0dGxlIGZpcnN0LgAAVGljayB0aGUgcGxheWVyIHRvIHdhdGNoIGFzLgAAAABTdGFydGluZyB0aGUgcmV'
    'wbGF5IGFzIABSUExBWSByZWNvcmRpbmcgAAAAAFJQTEFZIHNsb3QgAGNoZWNrYiBldmVudCBraW5kIAAAY2hlY2tiIGV2ZW50IGl'
    'kIAAAAAAtLS0gUkVQTEFZIE9OTElORSBHQU1FIHByZXNzZWQAAC0tLSBPTkxJTkUgV0FSIHByZXNzZWQAAFZpcnR1YWxBbGxvYyB'
    'mYWlsZWQAY29uZmlnOiAAAAAAY29uZmlnOiBob3N0IAAAAGNvbmZpZzogcG9ydCAAAABjb25maWc6IHBsYWluIAAAYmFjayB0byB'
    '0aGUgbWVudQAAAABSRVBMQVlJTkcgc2xvdCAARU5URVJJTkcgc2xvdCAAAGxvb3BiYWNrIGxpc3RlbmVyIGZhaWxlZAAAAABsb29'
    'wYmFjayBwb3J0IAAAY2FsbGluZyB0aGUgZ2FtZSdzIG5ldHdvcmsgZW50cnksIHRjcCBuZXQgb2JqZWN0IAAAAG5ldHdvcmsgZW5'
    '0cnkgcmV0dXJuZWQgAAAAAACq879qAAAAAA0AAACIAAAAXB8AAFwRAAAYAAAAA4ADgCwfAAAgAAAATB8AABAAAAByPAAAeTwAAOk'
    '8AAD5PAAAKD0AADM9AABiPQAAbT0AAAAQAAD4DgAA5B8AALwoAAAAAAAAABAAADwEAAAuYnNzAAAAADwUAAAEAAAALmRhdGEAAAB'
    'AFAAA1AoAAC5yZGF0YQAAFB8AAEgAAAAucmRhdGEkdm9sdG1kAAAAXB8AAIgAAAAucmRhdGEkenp6ZGJnAAAA5B8AALwoAAAudGV'
    '4dCRtbgAAAACgSAAAeAAAAC5lZGF0YQAAi1QkDItEJARWi/CF0nQTV4t8JBAr+IoMN4gORoPqAXX1X17Di0wkDIXJdCEPtkQkCFa'
    'L8WnAAQEBAVeLfCQMwekC86uLzoPhA/OqX16LRCQEw4tMJAQzwDgBdAdAgDwBAHX5w4tMJAgz0laLdCQIOBF0HVOLXCQUV4v+Syv'
    '5O9N9DIoBQogEOUGAOQB18F9bxgQyAF7Di1QkBFLosv///4tMJBAryAPCUf90JBBQ6LH///+DxBDDVYvsg+wMi0UMxkX/AIXAdQl'
    'qCsZF/jBZ6xhWagtZagpeM9JJ9/aAwjCIVA30hcB18F7/dRCNRfQDwVD/dQjonP///4PEDMnDVYvsg+wMi1UMagnGRf4AWYvCweo'
    'Eg+APioDgFAAQiEQN9EmD+QJ96P91EI1F9GbHRfQweFD/dQjoWv///4PEDMnDikQkBDwgdBc8CXQTPA10DzwKdAs8DHQHPAt0AzP'
    'AwzPAQMNTVVaLdCQUV4t8JBQr/ooUN4oejUq/jUIggPkZD7boD7bCjVO/D0fojUMgD7bIgPoZD7bDD0fIiWwkFIvFOsF1DITAdAN'
    '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","'
    'vsize":14616,"text_rva":4096,"image_base":268435456,"relocs":[4349,4551,4567,4591,4611,4641,4677,468'
    '4,4708,4733,4740,4749,4765,4777,4787,4799,4809,4821,4831,4846,4856,4868,4878,4890,4900,4912,4922,493'
    '7,4947,4959,4969,4981,4991,5003,5013,5028,5038,5050,5060,5072,5082,5094,5104,5119,5129,5141,5151,516'
    '3,5173,5185,5195,5210,5220,5232,5242,5254,5264,5276,5286,5301,5311,5323,5333,5345,5355,5367,5377,539'
    '2,5402,5414,5424,5436,5446,5458,5468,5483,5493,5505,5515,5527,5537,5556,5562,5583,5598,5609,5637,616'
    '0,6178,6218,6259,6282,6300,6537,6609,6762,6996,7029,7175,7273,7381,7388,7409,7528,7620,7731,7843,787'
    '3,7914,7934,7965,7981,7997,8021,8032,8049,8058,8118,8149,8163,8201,8216,8383,8388,8412,8442,8727,874'
    '8,8798,8874,8881,8944,9010,9154,9174,9218,9224,9257,9271,9308,9322,9348,9559,9568,9709,9723,9736,975'
    '2,9786,9796,9855,9863,9891,9960,9968,9988,10004,10009,10032,10079,10096,10102,10117,10134,10145,1015'
    '8,10169,10190,10213,10219,10226,10238,10244,10258,10288,10298,10317,10340,10380,10451,10498,10533,10'
    '549,10556,10567,10593,10613,10639,10662,10675,10748,10765,10805,10823,10874,10898,10927,10988,11011,'
    '11030,11050,11077,11093,11106,11125,11212,11220,11273,11285,11291,11309,11315,11328,11345,11351,1135'
    '7,11400,11409,11428,11437,11450,11457,11646,11678,11722,11759,11773,11785,11809,11873,11878,11924,11'
    '929,11935,11944,11949,11954,11960,11994,11999,12004,12017,12030,12040,12074,12091,12132,12165,12209,'
    '12248,12301,12306,12320,12333,12359,12404,12409,12434,12466,12471,12492,12497,12504,12524,12535,1255'
    '2,12563,12611,12627,12645,12650,12663,12677,12731,12745,12750,12762,12782,12793,12801,12820,12842,12'
    '858,12875,12887,13045,13064,13069,13080,13097,13119,13147,13166,13171,13200,13225,13237,13261,13280,'
    '13292,13306,13348,13353,13385,13400,13405,13415,13428,13438,13449,13454,13475,13509,13516,13530,1358'
    '7,13602,13641,13658,13671,13702,13726,13757,13822,13829,13845,13851,13857,13863,13884,13892,13901,13'
    '918,13923,13934,13945,13956,13967,14033,14054,14069,14082,14128,14146,14153,14183,14194,14214,14234,'
    '14239,14247,14257,14263,14269,14275,14296,14313,14326,14353,14363,14378,14384],"exports":{"online_di'
    'spatch":14440,"online_war":14402,"replay_game":14421},"sha":"4f2843439dccacb55507b76912bd2d5429a9bbd'
    'ed1e9db5a503d87d3056718ac"}'
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
    replay = sec_va + m['exports'][REPLAY_EXPORT]
    edits = [
        (pe.nsec_off, data[pe.nsec_off:pe.nsec_off + 2], struct.pack('<H', pe.nsec + 1),
         'PE header: NumberOfSections %d -> %d (the new %s section)' % (pe.nsec, pe.nsec + 1, SECTION_NAME.decode())),
        (pe.size_of_image_off, data[pe.size_of_image_off:pe.size_of_image_off + 4], struct.pack('<I', new_soi),
         'optional header: SizeOfImage 0x%X -> 0x%X' % (pe.size_of_image, new_soi)),
        (sh_off, data[sh_off:sh_off + 40], new_sh,
         'section table: new header %s VA 0x%X size 0x%X, file 0x%X size 0x%X, code + read + write + execute (0x%08X), in the zero slack after the last header'
         % (SECTION_NAME.decode(), sec_rva, m['vsize'], raw_off, raw_size, SECTION_CHARS)),
        (va2file(ID_FILTER) + 2, b'\x07', bytes([ID_FILTER_NEW]),
         'main menu id filter 0x404F9E: cmp edx,7 -> cmp edx,9 (button ids 8 = ONLINE WAR and 9 = REPLAY ONLINE GAME reach the id chain)'),
        (va2file(CHAIN_TAIL), b'\x90' * 7, jmp,
         'end of the id chain 0x405136: 7 NOP -> jmp online_dispatch 0x%X (+2 NOP); id 8 runs online_war, id 9 replay_game 0x%X, other ids continue at 0x40513D as before' % (dispatch, replay)),
    ]
    summary = ('new section %s at file 0x%X (VA 0x%X), %d bytes appended: the ONLINE WAR / REPLAY ONLINE GAME module (%d bytes of code and data, '
               '%d absolute operands rebased, online_dispatch +0x%X, online_war +0x%X, replay_game +0x%X, module sha256 %s)'
               % (SECTION_NAME.decode(), raw_off, sec_va, len(append), m['vsize'], len(m['relocs']),
                  m['exports'][DISPATCH_EXPORT], m['exports'][ENTRY_EXPORT], m['exports'][REPLAY_EXPORT], m['sha'][:16]))
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


def replay_script(src):
    """ONLINE (the output of online_script, any size) -> REPLAYE: the list narrowed to 40 columns, the scroll bar,
    UP and DOWN 72 px left of their stock LOADGE places, the participant pane (checkb 32..39, in_text 40..47, the
    heading in_text 48), the title and the WATCH button, the background REPLAYBG.GIF.  Idempotent on its own output."""
    toks = re.search(rb'(?m)^\s*list\s+0\s+\d+\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s', src)
    if not toks:
        raise SystemExit('ONLINE: no `list 0` line')
    lx, ly, lw, lh = (int(g) for g in toks.groups())
    if lw not in (LIST_WIDTH_NEW, RLIST_WIDTH):
        raise SystemExit('ONLINE: list width %d, expected %d' % (lw, LIST_WIDTH_NEW))
    already = lw == RLIST_WIDTH
    shifted = {b'scroll': {1}, b'pushb': {2, 3}, b'gadget': {7, 8}}
    drop = {b'checkb': set(range(W_RADIO0, W_RADIO0 + 8)), b'in_text': set(range(W_RNAME0, W_RNAME0 + 8)) | {W_RPANE}}
    pane = []
    for k in range(8):
        bx, by = lx + RADIO_DX, ly + RADIO_DY + RADIO_PITCH * k
        cells = RADIO_CELLS_HOST if k == HOST_SLOT else RADIO_CELLS
        pane.append(b'checkb   %d  0  %d  %d   %d   %d  %d   %d   -' % (W_RADIO0 + k, bx, by, RADIO_W, RADIO_H, cells[0], cells[1]))
    for k in range(8):
        pane.append(b'in_text  %d  0  %d  %d   %d    1  0  -  read_only' % (W_RNAME0 + k, lx + RNAME_DX, ly + RADIO_DY + RADIO_PITCH * k + RNAME_DY, RNAME_CHARS))
    pane.append(b'in_text  %d  0  %d  %d   %d    1  0  -  read_only' % (W_RPANE, lx + RADIO_DX, ly + HEADER_DY, RNAME_CHARS + 4))
    out = []
    for raw in src.split(b'\n'):
        line, cr = (raw[:-1], b'\r') if raw.endswith(b'\r') else (raw, b'')
        if re.match(rb'\s*background\s+\S+/\S+\s*$', line):               # .../onlinebg -> .../replaybg
            out.append(re.sub(rb'(\S+/)\S+\s*$', lambda mm: mm.group(1) + BG_NAME_R.encode(), line) + cr)
            continue
        if re.match(rb'\s*pictures\s+\S+\s*$', line):                        # intrface/knobe -> hd_src/knobr (the bank with the empty box)
            out.append(PICTURES_R + cr)
            continue
        m = re.match(rb'\s*(\w+)\s+(\d+)\s', line)
        if m:
            kind, ident = m.group(1), int(m.group(2))
            if ident in drop.get(kind, ()):
                continue                                                      # the pane is re-emitted after the header line
            if kind == b'list' and ident == 0:
                line = set_tokens(line, {6: b'%d' % RLIST_WIDTH})
            elif ident in shifted.get(kind, ()) and not already:
                x = int(re.match(rb'\s*\w+\s+\d+\s+\d+\s+(\d+)', line).group(1))
                line = set_tokens(line, {4: b'%d' % (x - SHIFT + RSHIFT)})
            elif kind == b'in_text' and ident == W_HEADER:
                line = set_tokens(line, {6: b'%d' % RROW_CHARS})
                out.append(line + cr)
                for extra in pane:
                    out.append(extra + cr)
                continue
            elif kind == b'textmsg' and ident in TEXTS_R:
                num = b'%d' % ident
                line = b'textmsg ' + num + b' ' * (8 - len(num)) + TEXTS_R[ident]
        out.append(line + cr)
    return b'\n'.join(out)


def knobr_bank(knobe_bytes):
    """KNOBE.SPR -> KNOBR.SPR: the same 149 cells plus cell 149 = cell 9 (the lobby's unticked READY box) with the
    "?" glyph inside the inner square blacked out (rows 5..12, columns 10..17 of the 28x18 cell -> index 254), and
    cell 150 = cell 149 in greys (DIM_MAP) for the host seat's dead box."""
    import io as _io, tempfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import spr
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, 'knobe.spr')
        open(src, 'wb').write(knobe_bytes)
        bank = spr.read_spr(src)
        cells = bank['cells']
        if len(cells) != 149:
            raise SystemExit('KNOBE.SPR: %d cells, expected 149' % len(cells))
        c9 = cells[EMPTY_CELL_FROM]
        w, h = c9['w'], c9['h']
        px = bytearray(c9['px'])
        for y in range(5, 13):
            for x in range(10, 18):
                px[y * w + x] = 254
        cells.append(dict(w=w, h=h, ox=c9['ox'], oy=c9['oy'], px=px))
        cells.append(dict(w=w, h=h, ox=c9['ox'], oy=c9['oy'], px=bytearray(DIM_MAP.get(v, v) for v in px)))
        dst = os.path.join(td, 'knobr.spr')
        spr.write_spr(dst, bank['flags'], cells, bank['palette'])
        return open(dst, 'rb').read()


def write_bank(game, apply):
    """[(path, what)]: HD_SRC\\KNOBR.SPR from INTRFACE\\KNOBE.SPR."""
    src = find_ci(os.path.join(game, 'INTRFACE'), BANK_SRC)
    if not src:
        raise SystemExit('%s: no INTRFACE\\%s' % (game, BANK_SRC))
    data = knobr_bank(open(src, 'rb').read())
    folder = os.path.join(game, 'HD_SRC')
    dst = find_ci(folder, BANK_NAME.upper() + '.SPR') or os.path.join(folder, BANK_NAME.upper() + '.SPR')
    if os.path.isfile(dst) and open(dst, 'rb').read() == data:
        return []
    if apply:
        os.makedirs(folder, exist_ok=True)
        open(dst, 'wb').write(data)
    return [(dst, 'the radio-box bank: KNOBE.SPR + cell 149 = the empty box + cell 150 = the same in greys')]


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


def frame_rects(lx, ly, lh, replay=False):
    """The frames as (x0, y0, x1, y1), x1/y1 exclusive: header + list, scroll bar with its buttons, the two text lines
    - and for the REPLAY screen the participant pane right of the scroll bar, the text frame spanning the whole width."""
    b = ly + lh
    if replay:
        ux = lx + RLIST_WIDTH + SCROLL_DX
        return [
            (lx - FRAME_MARGIN_X, ly + LIST_TOP_DY, lx + RLIST_WIDTH + FRAME_MARGIN_X, b + LIST_BOTTOM_DY),
            (ux - 4, ly, ux + SCROLL_W + 4, b + 2),
            (lx + PANE_X0, ly + LIST_TOP_DY, lx + PANE_X1, b + LIST_BOTTOM_DY),
            (lx - FRAME_MARGIN_X, b + TEXT_TOP_DY, lx + PANE_X1, b + TEXT_BOTTOM_DY),
        ]
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


def online_background(gif_bytes, lx, ly, lh, replay=False):
    """LOADER.GIF (any size) -> the ONLINE (or, with `replay`, the REPLAYE) screen's background: the same picture with the grey frames."""
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
    for rect in frame_rects(lx, ly, lh, replay):
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
    # REPLAY ONLINE GAME (2 Oct 2026): the second screen, derived from the ONLINE script just written
    rdata = replay_script(data)
    rdst = find_ci(folder, 'REPLAYE') or os.path.join(folder, 'REPLAYE')
    if not (os.path.isfile(rdst) and open(rdst, 'rb').read() == rdata):
        done.append((rdst, 'the REPLAY ONLINE GAME screen (%dx%d) from the ONLINE script' % (width, height)))
        if apply:
            open(rdst, 'wb').write(rdata)
    rbg = online_background(open(loader, 'rb').read(), int(m.group(1)), int(m.group(2)), int(m.group(3)), replay=True)
    rbgdst = find_ci(folder, BG_NAME_R.upper() + '.GIF') or os.path.join(folder, BG_NAME_R.upper() + '.GIF')
    if not (os.path.isfile(rbgdst) and open(rbgdst, 'rb').read() == rbg):
        done.append((rbgdst, 'the REPLAY ONLINE GAME background: LOADER.GIF with the frames around the list, the scroll bar, the participant pane and the text lines'))
        if apply:
            open(rbgdst, 'wb').write(rbg)
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
    ap.add_argument('cmd', choices=['verify', 'plan', 'apply', 'data', 'embed', 'bank'])
    ap.add_argument('target', help='the exe (verify/plan/apply), the game folder (data) or the DLL (embed)')
    ap.add_argument('--game', help='apply: also write the ONLINE and REPLAYE screens and DEFAULT_SERVER.TXT into this game folder')
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
    if a.cmd == 'bank':
        for path, what in write_bank(a.target, True) or [(None, 'HD_SRC\\KNOBR.SPR is up to date')]:
            print('  %s%s' % (('written %s: ' % path) if path else '', what))
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

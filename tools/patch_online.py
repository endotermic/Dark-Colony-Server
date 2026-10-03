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
the buttons ENTER / BACK, a name line and a server line above the status line (the `name=` and the address of
DEFAULT_SERVER.TXT, 3 Oct 2026), the title centred in its panel, the
save-mode widgets and the two animations over the header and status lines removed - written as
INTRF_HD/ONLINE from INTRF_HD/LOADGE at the HD sizes and as INTRFACE/ONLINE from INTRFACE/LOADGE at
640x480 (the module probes for <folder>\\ONLINE, so one exe serves every size), with its background
ONLINEBG.GIF = LOADER.GIF plus grey frames around the header + list, the scroll bar and the two text lines (Pillow);
REPLAYE + REPLAYBG.GIF the same way (replay_script / frame_rects(replay=True): list 40 columns, the pane frame);
and DEFAULT_SERVER.TXT beside the exe (`name=` / `address=` lines since 3 Oct 2026), written when it is missing or
still holds nothing but the shipped address in the first, bare form (a player's own relay address is never
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
W_NAME = 49                                 # the name line above the server line (3 Oct 2026; 32..48 are the replay pane's)
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
# the title: MFONTO2 advances ~16.8 px per character and LOADER.GIF's rounded title panel has ~290 px of flat interior, so
# "Replay Online Game" (18 characters, 302 px) ran onto the panel's rounded ends at every size (maintainer, 3 Oct 2026:
# "'Reply online game' header does not fit in frame") - 13 characters is the longest title that fits
TEXTS_R = {1: b'Online Replay', 2: b'REPLAY'}
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
#   text frame   x lx-6 .. lx+454, y B+8 .. B+68        (name line at B+14, server line at B+30, state line at B+46;
#                                                        B+52 with two lines until 3 Oct 2026; the buttons sit at B+87)
FRAME_MARGIN_X = 6
HEADER_DY, LIST_TOP_DY, LIST_BOTTOM_DY = -16, -22, 3
SCROLL_DX, SCROLL_W = 12, 26
TEXT_TOP_DY, NAME_DY, SERVER_DY, STATUS_DY, TEXT_BOTTOM_DY = 8, 14, 30, 46, 68

DEFAULT_SERVER_TEXT = """/*
 * DEFAULT_SERVER.TXT - the relay server behind ONLINE WAR and REPLAY ONLINE GAME.
 *
 * Dark Colony Ultimate reads this file when you press one of those two buttons in the main
 * menu. It connects to the address below with TLS encryption on port 8889 (the Dark Colony
 * Server relay), shows the rooms or the recorded battles the relay offers and joins the one
 * you pick. Both screens show the name and the address from this file above the connection
 * state.
 *
 * Fields, one per line:
 *   name=<how the screens call this server>          any text; here the relay's project page
 *   address=<host or IP address>[:port]              the port defaults to 8889 (TLS)
 *   plain                                            optional: no encryption, for a relay on
 *                                                    your own network without a certificate
 *                                                    (the plain relay port is 8888)
 * Comments in the C++ style are ignored: "//" at the start of a line or after a space runs to
 * the end of the line (so an address like https://... is kept), or a block like this one.
 * A file holding only an address (the form before 3 Oct 2026) is still understood.
 *
 * Keep one server in the file. MULTI PLAYER WAR (the in-game host / CONNECT TO SERVER
 * screens) does not read this file.
 */

name=https://github.com/endotermic/Dark-Colony-Server
address=dark-colony-server.fly.dev
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
    'yZWFtIHNpemVzIGZhaWxlZABwbGFpbgAAAG5vdGxzAAAAREVGQVVMVF9TRVJWRVIuVFhUAABERUZBVUxUX1NFUlZFUi5UWFQgbm9'
    '0IGZvdW5kIGJlc2lkZSB0aGUgZ2FtZQAAAABuYW1lAAAAAGFkZHJlc3MAc2VydmVyAABob3N0AAAAAERFRkFVTFRfU0VSVkVSLlR'
    'YVCBuYW1lcyBubyBzZXJ2ZXIgYWRkcmVzcwAAUHJvdG9jb2wgZXJyb3I6IGJhZCBmcmFtZSBmcm9tIHRoZSByZWxheQAAAABQcm9'
    '0b2NvbCBlcnJvcjogZnJhbWUgdG9vIGxvbmcAAG1vbmV5IGZpeDogc2ltIHN0YXRlIAAAAG1vbmV5IGZpeDogbG9jYWwgcGxheWV'
    'yIAAAAABtb25leSBmaXg6IG1vbmV5IAAAAG1vbmV5IGZpeDogc3BlbnQgAAAAcHJveHk6IGFjY2VwdCBmYWlsZWQAAAAAcHJveHk'
    '6IHRoZSBnYW1lIGNvbm5lY3RlZAAAAHByb3h5OiBjbG9zaW5nIGJvdGggY29ubmVjdGlvbnMAV1NBU3RhcnR1cCBmYWlsZWQAAAB'
    'DYW5ub3QgcmVzb2x2ZSAAc29ja2V0KCkgZmFpbGVkAENhbm5vdCBjb25uZWN0IHRvIAAAOgAAACAoZXJyb3IgAAAAACkAAABzY3J'
    'lZW46IAAAAABzY3JlZW4gbG9hZGVkAAAATmFtZTogbm9uZSAoc2VlIERFRkFVTFRfU0VSVkVSLlRYVCkAU2VydmVyOiBub25lICh'
    'zZWUgREVGQVVMVF9TRVJWRVIuVFhUKQAAAE5hbWU6IAAALQAAAFNlcnZlcjogAAAAAENvbm5lY3RpbmcgKG5vIGVuY3J5cHRpb24'
    'pLi4uAAAAQ29ubmVjdGluZyAoVExTKS4uLgBjb25uZWN0ZWQgKHBsYWluKQAAAGNvbm5lY3RlZCAoVExTKQBDb25uZWN0ZWQuIFB'
    'pY2sgYSBiYXR0bGUsIHRpY2sgYSBwbGF5ZXIsIHByZXNzIFJFUExBWS4AAENvbm5lY3RlZCAoVExTKS4gUGljayBhIGJhdHRsZSw'
    'gdGljayBhIHBsYXllciwgUkVQTEFZLgAAQ29ubmVjdGVkLiBTZWxlY3QgYSByb29tIGFuZCBwcmVzcyBFTlRFUi4AAABDb25uZWN'
    '0ZWQgKFRMUykuIFNlbGVjdCBhIHJvb20gYW5kIHByZXNzIEVOVEVSLgBDb25uZWN0aW9uIGxvc3QuAAAAAGNvbm5lY3Q6IAAAAE5'
    'vIHJlY29yZGVkIGJhdHRsZSBvbiB0aGUgc2VydmVyIHlldC4AAAByZXBsYXkgbGlzdDogZW50cmllcyAAAABOb3QgY29ubmVjdGV'
    'kLiBQcmVzcyBCQUNLIGFuZCB0cnkgYWdhaW4uAAAAAFNlbGVjdCBhIHJvb20gZmlyc3QuAAAAAEVudGVyaW5nIHJvb20gAAAuLi4'
    'AU2VsZWN0IGEgYmF0dGxlIGZpcnN0LgAAVGljayB0aGUgcGxheWVyIHRvIHdhdGNoIGFzLgAAAABTdGFydGluZyB0aGUgcmVwbGF'
    '5IGFzIABSUExBWSByZWNvcmRpbmcgAAAAAFJQTEFZIHNsb3QgAGNoZWNrYiBldmVudCBraW5kIAAAY2hlY2tiIGV2ZW50IGlkIAA'
    'AAAAtLS0gUkVQTEFZIE9OTElORSBHQU1FIHByZXNzZWQAAC0tLSBPTkxJTkUgV0FSIHByZXNzZWQAAFZpcnR1YWxBbGxvYyBmYWl'
    'sZWQAY29uZmlnOiAAAAAAY29uZmlnOiBuYW1lIAAAAGNvbmZpZzogaG9zdCAAAABjb25maWc6IHBvcnQgAAAAY29uZmlnOiBwbGF'
    'pbiAAAGJhY2sgdG8gdGhlIG1lbnUAAAAAUkVQTEFZSU5HIHNsb3QgAEVOVEVSSU5HIHNsb3QgAABsb29wYmFjayBsaXN0ZW5lciB'
    'mYWlsZWQAAAAAbG9vcGJhY2sgcG9ydCAAAGNhbGxpbmcgdGhlIGdhbWUncyBuZXR3b3JrIGVudHJ5LCB0Y3AgbmV0IG9iamVjdCA'
    'AAABuZXR3b3JrIGVudHJ5IHJldHVybmVkIAAAAAAAXdvAagAAAAANAAAAiAAAALQfAAC0EQAAGAAAAAOAA4CEHwAAIAAAAKQfAAA'
    'QAAAA7D0AAPM9AABjPgAAcz4AAKI+AACtPgAA3D4AAOc+AAAAEAAAUA8AADwgAABUKgAAAAAAAAAQAAA8BAAALmJzcwAAAAA8FAA'
    'ABAAAAC5kYXRhAAAAQBQAACwLAAAucmRhdGEAAGwfAABIAAAALnJkYXRhJHZvbHRtZAAAALQfAACIAAAALnJkYXRhJHp6emRiZwA'
    'AADwgAABUKgAALnRleHQkbW4AAAAAkEoAAHgAAAAuZWRhdGEAAItUJAyLRCQEVovwhdJ0E1eLfCQQK/iKDDeIDkaD6gF19V9ew4t'
    'MJAyFyXQhD7ZEJAhWi/FpwAEBAQFXi3wkDMHpAvOri86D4QPzql9ei0QkBMOLTCQEM8A4AXQHQIA8AQB1+cOLTCQIM9JWi3QkCDg'
    'RdB1Ti1wkFFeL/ksr+TvTfQyKAUKIBDlBgDkAdfBfW8YEMgBew4tUJARS6LL///+LTCQQK8gDwlH/dCQQUOix////g8QQw1WL7IP'
    'sDItFDMZF/wCFwHUJagrGRf4wWesYVmoLWWoKXjPSSff2gMIwiFQN9IXAdfBe/3UQjUX0A8FQ/3UI6Jz///+DxAzJw1WL7IPsDIt'
    'VDGoJxkX+AFmLwsHqBIPgD4qA4BQAEIhEDfRJg/kCfej/dRCNRfRmx0X0MHhQ/3UI6Fr///+DxAzJw4pEJAQ8IHQXPAl0EzwNdA8'
    '8CnQLPAx0BzwLdAMzwMMzwEDDU1VWi3QkFFeLfCQUK/6KFDeKHo1Kv41CIID5GQ+26A+2wo1Tvw9H6I1DIA+2yID6GQ+2ww9HyIl'
    'sJBSLxTrBdQyEwHQDRuvGM8BA6wIzwF9eXVvDVYvsg30MAHQP/3UQ/3UM/1UIhcB1DesCM8CLTRTHAQAAAABdw1WNbCSMoQAQABC'
    'B7MwAAACFwHR9gz0gEAAQAHR0Vlcz/1dXagRXagFoAAAAQGiIFwAQ/9CL8IP+/3RUagJXV1b/FSQQABBoxgAAAP91fI1FqFDoMP7'
    '//2jIAAAAjUWoaJQXABBQ6E/+//+DxBiNRXBXUI1FqFDo+v3//1lQjUWoUFb/FSAQABBW/xUIEAAQX16DxXTJw1EzwFWLLbAESAB'
    'Agz2MEAAQAFeLPYAESACJRCQID4V9AwAAU1Zo9BQAEP/VaAQVABCL8P/VaBAVABCL2P/Vi+iNRCQQUGgcFQAQVlfo9P7//6MAEAA'
    'QjUQkIFBoKBUAEFZX6N7+//+jBBAAEI1EJDBQaDQVABBWV+jI/v//owgQABCNRCRAUGhAFQAQVlfosv7//4PEQKMMEAAQjUQkEFB'
    'oUBUAEFZX6Jn+//+jEBAAEI1EJCBQaGAVABBWV+iD/v//oxQQABCNRCQwUGhoFQAQVlfobf7//6MYEAAQjUQkQFBoeBUAEFZX6Ff'
    '+//+DxECjHBAAEI1EJBBQaIgVABBWV+g+/v//oyAQABCNRCQgUGiUFQAQVlfoKP7//6MkEAAQjUQkMFBopBUAEFNX6BL+//+jKBA'
    'AEI1EJEBQaLAVABBTV+j8/f//g8RAoywQABCNRCQQUGi4FQAQU1fo4/3//6MwEAAQjUQkIFBowBUAEFNX6M39//+jNBAAEI1EJDB'
    'QaMgVABBTV+i3/f//ozgQABCNRCRAUGjQFQAQU1foof3//4PEQKM8EAAQjUQkEFBo2BUAEFNX6Ij9//+jQBAAEI1EJCBQaOQVABB'
    'TV+hy/f//o0QQABCNRCQwUGj0FQAQU1foXP3//6NIEAAQjUQkQFBoABYAEFNX6Eb9//+DxECjTBAAEI1EJBBQaAgWABBTV+gt/f/'
    '/o1AQABCNRCQgUGgQFgAQU1foF/3//6NUEAAQjUQkMFBoGBYAEFNX6AH9//+jWBAAEI1EJEBQaCAWABBTV+jr/P//g8RAo1wQABC'
    'NRCQQUGgoFgAQU1fo0vz//6NgEAAQjUQkIFBoNBYAEFNX6Lz8//+jZBAAEI1EJDBQaEQWABBTV+im/P//o2gQABCNRCRAUGhUFgA'
    'QVVfokPz//4PEQKNsEAAQjUQkEFBocBYAEFVX6Hf8//+jcBAAEI1EJCBQaIwWABBVV+hh/P//o3QQABCNRCQwUGikFgAQVVfoS/z'
    '//6N4EAAQjUQkQFBotBYAEFVX6DX8//+DxECjfBAAEI1EJBBQaMQWABBVV+gc/P//o4AQABCNRCQgUGjYFgAQVVfoBvz//6OEEAA'
    'QjUQkMFBo8BYAEFVX6PD7//+LfCRAg8Qwo4gQABCJPYwQABCF9nUEM8DrL4X/dSmF23ULaAgXABDo6fv//1mF7XULaCgXABDo2vv'
    '//1loSBcAEOjP+///WYvHXltfXVnD/3QkCP90JAj/FWgQABDDVY1sJJCB7MgAAACNRahWvsgAAABW/3V4UOgg+v//Vv91fI1FqFD'
    'oRfr//41FqFDog/v//4PEHF6DxXDJw1WNbCSQgezIAAAAjUWoVr7IAAAAVv91eFDo4/n//1b/dXyNRahQ6Cn6//+NRahQ6Eb7//+'
    'DxBxeg8VwycNTVleLRCQQi1QkFDPbvkgyQgD/1l9eW8NTVleLRCQQvhAyQgD/1l9eW8NTVleLRCQQvkR7QgD/1l9eW8NTVleLRCQ'
    'Qi1QkFItcJBiLTCQcvrilQgD/1l9eW8NTVleLRCQQi1QkFL4oqEIA/9ZfXlvDU1ZXi0QkEItUJBSLXCQYvtQ+QgD/1l9eW8NTVle'
    'LRCQQi1QkFItcJBi+CHNCAP/WX15bw1NWV4tEJBCLVCQUi1wkGL50RUIA/9ZfXlvDU1ZXi0QkEItUJBS+fEFCAP/WX15bw1NWV4t'
    'EJBAz0otcJBS+/MBAAP/WX15bw1NWV4tEJBCLVCQUvmzCQAD/1l9eW8NTVle+QPNHAP/WX15bw1NWV74k4EIA/9ZfXlvDU1ZXi0Q'
    'kEItUJBSLXCQYi0wkHP90JCC+LBJAAP/WJf8AAABfXlvDVYvsgewMAQAAi0UMuegDAACZ9/lWiUX4M/ZpwugDAABGV4t9CIm9+P7'
    '//4m19P7//4lF/I1F+FBqAGoAjYX0/v//UGoA/xU8EAAQhcB+Eo2F9P7//1BX/xVoEAAQhcB1AjP2X4vGXsnDVot0JBBXhfZ+Hot'
    '8JBBqAFZX/3QkGP8VNBAAEIXAfg4r8AP4hfZ/5jPAQF9ewzPA6/lWi3QkCFeDPv90Dv82/xVAEAAQxwb/////M/85fiB0DY1GFFD'
    '/FYQQABCJfiA5fhx0DY1GDFD/FYgQABCJfhxfx0YIAQAAAF7Dg+w8U1VWi3QkTFeDfggAD4UoAQAAg34EAHUX/3QkWP90JFj/Nuh'
    'b////g8QM6Q0BAACLfCRYhf8PjtgAAACLbCRUi14sO/uLRiQPTt8FRJAAAFMDxlVQ6Nv2//+LTiSNhkSQAACJRCQwg8QMjYZEkAA'
    'AiUwkHAPBx0QkIAcAAACJRCQwjYFEkAAAA8PHRCQsAQAAAAPGiVwkKIlEJDwzyYtGKIlEJDSNRCQciUQkGI1EJBBRUFGNRhTHRCR'
    'EBgAAAFCJTCRUiUwkWIlMJFCJTCQgx0QkJAQAAAD/FXgQABCJRCRQhcB1M4tEJDQDRCQoA0QkHFCNhkSQAABQ/zbogv7//4PEDIX'
    'AdDMr+wPrhf8Pjyz///8zwEDrJGpggcZE1AAAaJgXABBW6G32//9qYP90JGBW6Pz2//+DxBgzwF9eXVuDxDzDg+xEU1VWV4t8JFi'
    'LtzhIAACF9g+OgwEAAI1HOMdEJCgBAAAAM8mJRCQsagOJTCQYjVcUiXQkKI1EJDxZx0D8AAAAAMcAAAAAAI1ADMdA7AAAAACD6QF'
    '15FGNRCQoiUwkHIlEJCSNRCQcUVBSx0QkLAQAAAD/FXwQABCL6IH9GAMJgA+EFAEAAIH9FwMJAA+E/AAAAIXtdAyB/SEDCQAPhbM'
    'AAABqBDP2jVwkKDPJWIlMJFiJRCQQg3sEAXVLixOF0nRFi49AkAAAuABIAAArwTvQD0/QjYE8SAAAUv9zCAPHiVQkHFDoBfX//4t'
    'EJCCDxAwBh0CQAACLRCQQi0wkWMdEJBQBAAAAg3sEBXUJi0sIizOJTCRYg8MMg+gBiUQkEHWUhfZ0EFZRjUc4UOi+9P//g8QM6wI'
    'z9om3OEgAAIH9IQMJAHQxg3wkFAAPhL/+//8zwEDrR2pgjbdE1AAAaKwXABBW6Or0//9qYFVW6Hz1//+DxBjrH2pgjYdE1AAAaMA'
    'XABBQ6Mn0//+DxAzrB8dHCAEAAACDyP/rAjPAX15dW4PERMNTVVaLdCQQM+1XOW4ID4XLAAAAi1wkIDluBHQhi75AkAAAi4Y8kAA'
    'AO/h/U1boKP7//1mFwA+IowAAAH/aU/826Or7//9ZWYXAD4SXAAAAVTluBHRui444SAAAuABIAAArwVCNQTgDxlD/Nv8VOBAAEIX'
    'AfmIBhjhIAACL3euYK/g7fCQcD098JBwFPEgAAFcDxlD/dCQg6K7z//8BvjyQAACDxAyLjjyQAAA7jkCQAAB1DImuQJAAAImuPJA'
    'AAIvH6x7/dCQg/3QkIP82/xU4EAAQhcB/CsdGCAEAAACDyP9fXl1bwzPA6/eB7IAAAABTVVZXajgz241EJFwz/1NHUIl8JBzoZPP'
    '//4u0JKAAAACNRCRkg8QMx0QkWAQAAACJXCR4x4QkiAAAADAAQACNbgxTVVNTUFNqAmjcFwAQU/8VbBAAEIvYhdt0JWpgX1eBxkT'
    'UAABoDBgAEFboTfP//1dTVujg8///g8QY6YICAAAzyYl+HImOOEgAAI1+FOsDjW4Mg3wkEACNRCQcagJaiVQkIIlMJCSJTCQciUw'
    'kKMdEJCwBAAAAiUQkMHQ6UY1EJBhQjUQkMFBXUVFRUWgcgQAA/7QkvAAAAFFV/xVwEAAQi9jHRCQQAAAAAMdGIAEAAADpAgEAAIu'
    'GOEgAAIXAdAiB+xgDCYB1TWiYOgAA/zboKfr//1lZhcAPhM0BAACLjjhIAAC4AEgAAGoAK8FQjUE4A8ZQ/zb/FTgQABCFwA+OngE'
    'AAAGGOEgAADPJi4Y4SAAAagJaUYlEJESNbjiNRCREiVQkSIlEJECNRCQYUI1EJDCJbCRQUFFRjUQkSIlMJGRQUVFoHIEAAP+0JLw'
    'AAACNRgyJTCR8V1CJTCR8iUwkZIlUJGj/FXAQABCL2GoAWYH7GAMJgA+E0/7//4N8JFAFdSuLTCRMhcl0I4u+OEgAAIvRK/qDxzg'
    'D/ooHiEUARUeD6gF19ImOOEgAAOsKx4Y4SAAAAAAAAIt8JCSF/3Q9i2wkHIXtdDWLBolEJBh+H2oAVVdQ/xU0EAAQhcB+NCvoA/i'
    'LRCQYhe1/5Yt8JCQz7UVX/xWAEAAQhe10HoXbdHyB+xIDCQB1Ho1+FDPJ6Tz+//+LfCQkM+3r12pgaGgYABDpiwAAAGpgX1eBxkT'
    'UAABokBgAEFboQPH//1dTVujT8f//g8QYgfslAwmAdQhXaKgYABDrHoH7IgMJgHUIV2jEGAAQ6w6B+yYDCYB1Uldo5BgAEFboM/H'
    '//+tBjUYkUGoEjUYUUP8VdBAAEIXAdAlqYGgIGQAQ6xgzwECJRgTrIWpgaDwYABDrB2pgaCQYABCNhkTUAABQ6L7w//+DxAwzwF9'
    'eXVuBxIAAAADDVYvsUVNWM9tTU2oDU2oBaAAAAID/dQiJXfz/FQAQABCL8IPI/zvwdC5Xi30MjUX8U1CLRRBIUFdW/xUEEAAQhcB'
    '1A4ld/Fb/FQgQABCLRfyIHDiLRfxfXlvJwzPAUFBqA1BqAWgAAACA/3QkHP8VABAAEIP4/3UDM8DDUP8VCBAAEDPAQMOLVCQEM8k'
    '4CnR/gDwRL3VyikQRATwvdSmFyXQSD7ZEEf9Q6N7w//+DxASFwHRUgDwKCnRPxgQKIEGAPAoAde/rQjwqdT1mxwQRICCDwQKAPBE'
    'AdDWKBAo8KnUHgHwKAS90DzwKdATGBAogQYA8CgB144A8EQB0EmbHBBEgIIPBAusBQYA8EQB1gcOLVCQIVYtsJAhWigqDzv9XM/+'
    'EyXRTgPk6i8cPRcZHi/CKDDqEyXXuhfZ4PY16ATPJA/5Tih+E23QqjUPQPAl3I2vJCg++w4PB0APIR4ofhNt1541B/z3+/wAAdwd'
    'miY0AAQAAxgQWAFtogAAAAFJV6Cnv//+DxAxfXl3DVYvsUVaLdQyAPgAPhJYAAABXi30IU4oGiEUM/3UM6Nzv//+DxASFwHQKRoo'
    'GiEUMhMB154oGhMB0a4veiEX8/3X86Ljv//+DxASFwHUKRooGiEX8hMB154A+AHQExgYARmggGQAQU+i27///WVmFwHUgaCgZABB'
    'T6KXv//9ZWYXAdQ84B3UVU1fo9v7//1lZ6wrHhwQBAAABAAAAgD4AD4Vx////W19eycNRUVOLXCQQM8BoCAEAAFBT6CPu//9oABA'
    'AAP81lBAAEGgwGQAQ6KD9//+DxBiFwHkd/3QkGGhEGQAQ/3QkHOg17v//M8CDxAxA6aEBAABXiz2UEAAQV+j1/f//gD8AWQ+ERwE'
    'AAFVWigeL9zPJitCA+gp0Ejw9dQWFyQ9Ez0eKB4rQhMB16YA/AIvXdAFHxgIAigaEwHQ5iEQkEP90JBDose7//4PEBIXAdCVGiga'
    'IRCQQhMB15esYjUL/i9APtgBQ6I/u//+DxASFwHQHxgIAO9Z35IA+AA+ExAAAAIXJD4SzAAAAjWkBM9LrFY1B/4vID7YAUOhb7v/'
    '/g8QEhcB0BogRO8535YpFAITAdCCIRCQU/3QkFOg67v//g8QEhcB0DEWKRQCIRCQUhMB15Gh0GQAQVug/7v//WVmFwHQXaIAAAAC'
    'Ng4AAAABVUOgn7f//g8QM60tofBkAEFboF+7//1lZhcB1ImiEGQAQVugG7v//WVmFwHURaIwZABBW6PXt//9ZWYXAdBgzwMYDAGa'
    'JgwABAABV6wFWU+i3/f//WVmAPwAPhb3+//9eXYA7AF91Gv90JBholBkAEP90JBzotOz//4PEDGoCWOsjM8BmOYMAAQAAdRaLgwQ'
    'BAAD32BvABbkiAABmiYMAAQAAM8BbWVnDVYvsi1UQU4odmBAAEFaLNZwQABCNQgOLyIlFEMH5CFL/dQyIBoDhD4rDwOAECsiNRgJ'
    'QiE4B6Obr//+LRRD+w1BW/3UIgOMPxkQG/wCIHZgQABDokvT//4PEGF5bXcNRUVNVVlcz/0c5fCQgfGmLVCQcM+1qB1mJTCQQD7Y'
    'CO8F3DovIiUwkEIXAD4ShAAAAu6UQABCNdwU7dCQgfzmKBBeIQ/uKRBcBiEP8ikQXAohD/YpEFwOIQ/6KRBcEi/6IQ/8zwIl8JBT'
    'rB4A8OgB0EEc7fCQgfPMzwF9eXVtZWcM7fCQgffGD+AJ0CUdAg/gDfNLrKotEJBSL92o4WSvwO/EPT/EDwlZQU+gT6///i0wkHIP'
    'EDItUJBxHxgQzAIkcrVQSABBFg8M+O+kPjGT///8zwIkNcBIAEEDrnYtMJARWM/aDfCQMAn0EM8Bew4oBVzwydgVqMl/rAw+2+DP'
    'AiT3MEwAQiTXIEwAQQIA8CAB0CEZAO0QkEHzyaihYO/APT/CNQQFWUGjQEwAQ6JDq//+DxAzGhtATABAAM8CF/w+UwF9ew4PsFFN'
    'VVldqB1s5XCQsD4ykAAAAiy3MEwAQhe0PiJYAAACLPcgTABA7/Q+NiAAAAIt0JChp17oAAACKBgMV/BIAEIgCikYBiEIBikYCiEI'
    'CikYDiEIDikYEiEIED7ZOBg+2RgVmweEIZgvIjUIIZolKBjPJg8IgiUwkEIlEJBiJVCQUhcnHRCQcKAAAAGoQWA9ERCQci8qDfCQ'
    'QAIlcJCAPREwkGIlMJBzrB4A8HgB0EUM7XCQsfPMzwF9eXVuDxBTDi/MrdCQgO/APT/CLRCQoA0QkIFZQUeif6f//i0QkKIPEDIt'
    'MJBBDi1QkFEGDwhGJTCQQxgQGAIt0JCiJVCQUg/kJD4x5////i0QkGIkEvQATABBHM8CJPcgTABA7/Q+dwOuVi0wkCFeD+QF8KYt'
    'UJAgPtgKD6FEPhJ0AAABqAl8rx3RPg+gBdDcrx3Qcg+gBdAgrx3QqM8Bfw41B/1CNQgFQ6Jf+///rDY1B/1CNQgFQ6B/+///32Fl'
    'ZG8D32F/DO8980otEJBAPtkoBagOJCFhfw1Zqf1iNcf878A9P8I1CAVZQaHgSABDozOj//4PEDMaGeBIAEAAzwIX2fheAuHgSABA'
    'AdAdAO8Z88usHxoB4EgAQAF6Lx1/DjUH/UI1CAVDo0fz//+uNUVNVVjPbV4s9+BIAEIlcJBCLLXQSABDraQ+2XQEPtkUAg+MPweM'
    'IC9iNQ/09/QMAAA+HlgAAADv7fE7/dCQcjUP9UI1FAlDo7v7//4stdBIAEIvwiz34EgAQK/tXjQQrUFXoKuj//4tcJCiDxBg784k'
    '9+BIAEA9P3olcJBCD/gN0RoP/An2S6wSLXCQQgf8AIAAAfUhqALgAIAAAK8dQjQQvUP90JCTom/P//4PEEIXAeEF0JIs9+BIAEAP'
    '4iT34EgAQ6Un///9qA1jrKmiAAAAAaMAZABDrDovD6xpogAAAAGjsGQAQaHgSABDoBOj//4PEDIPI/19eXVtZw4M9ABQAEAAPhJs'
    'AAABWizXcqUoAhfYPhIsAAABVi648fQAAg/0Hd35Tad00DgAAgz0IFAAQAFeLvDOwCwAAdT9WaAwaABDHBQgUABABAAAA6KLt//9'
    'VaCQaABDol+3///+0M6wLAABoQBoAEOiG7f//V2hUGgAQ6Hvt//+DxCChBBQAEDv4fQeLx6MEFAAQi88ryIXJfg0pjDOsCwAAiT0'
    'EFAAQX1tdXsOB7AwBAABVVzPtVVX/NTwUABD/FVwQABCL+KE8FAAQg/j/dBFQ/xVAEAAQxwU8FAAQ/////4P//3UdaGgaABDohOj'
    '///+0JBwBAADoG+///1lZ6ZEBAABWaIAaABDoZuj//6H4EgAQi7QkIAEAAFmFwH4eUP81dBIAEFfoue7//4PEDIXAD4RDAQAAiS3'
    '4EgAQUzluBHQ5i4ZAkAAAO4Y8kAAAfwg5rjhIAAB+I1VoACAAAP81/BMAEFbo4vH//4PEEIXAD4gDAQAAD4/lAAAAiw6LxYl8JBz'
    'HRCQYAQAAADlMhBx0FECD+AFy9HUMiUwkIMdEJBgCAAAAOS0AFAAQdA6JbCQQx0QkFKCGAQDrDMdEJBABAAAAiWwkFI1EJBBQVVW'
    'NRCQkUFX/FTwQABCL2IXbD4iUAAAA6Bn+//+F2w+ETP///41EJBhQV/8VaBAAEIXAdCtVaAAgAAD/NfwTABBX/xU4EAAQhcB+YFD'
    '/NfwTABBW6Dru//+DxAyFwHRMjUQkGFD/Nv8VaBAAEIXAD4T8/v//VWgAIAAA/zX8EwAQVuj58P//g8QQhcB4Hg+O3f7//1D/Nfw'
    'TABBX6Hft//+DxAyFwA+Fxf7//1tonBoAEOjw5v//WVf/FUAQABBW6IXt//9ZXl8zwF2BxAwBAADCBACB7KQBAABXi7wkrAEAAGi'
    'k1AAAagBX6P/k//+DxAzHB/////+NRCQYUGgCAgAA/xUoEAAQhcB0JGpgjYdE1AAAaLwaABBQ6A/l//+DxAzHRwgBAAAAM8DpgwE'
    'AAFNVi6wkuAEAAFZV/xVIEAAQi/CJdCQQg/7/dSxV/xVEEAAQhcB0VYtADIXAdE6DOAB0SWoE/zCNRCQYUOhZ5P//i3QkHIPEDGo'
    'AagFqAlhQ/xUsEAAQi9iJXCQQg/v/dUBqYI2HRNQAAGjgGgAQUOiK5P//g8QM6dUAAABqYFtTjbdE1AAAaNAaABBW6G3k//9TVVb'
    'ol+T//4PEGOmwAAAAahCNRCQYagBQ6A/k//+DxAxqAlhmiUQkFA+3hQABAABQ/xVMEAAQZolEJBaNRCQUahBQU4l0JCT/FTAQABC'
    'FwHR3amBbU423RNQAAGjwGgAQVugG5P//U1VW6DDk//9TaAQbABBW6CTk//8Pt4UAAQAAU1BW6Dbk//9TaAgbABBW6Ank//+DxDx'
    'T/xVkEAAQUFboGeT//1NoFBsAEFbo7OP//4PEGP90JBD/FUAQABDHRwgBAAAA6x+JH4O9BAEAAAB1GFVX6M7v//9ZWYXAdQtX6KL'
    'r//9ZM8DrAzPAQF5dW1+BxKQBAADDVYvsg+wUU1ZXahBfagBqAWoCW1OJffz/FSwQABCL8Ik1PBQAEIP+/w+EhAAAAFeNRexqAFD'
    'o+eL//4PEDGaJXewzwMdF8H8AAAFmiUXujUXsV1BW/xVUEAAQhcB1PmoB/zU8FAAQ/xVYEAAQhcB1LI1F/FCNRexQ/zU8FAAQ/xV'
    'gEAAQhcB1FP917v8VUBAAEItNCGaJATPAQOsY/zU8FAAQ/xVAEAAQxwU8FAAQ/////zPAX15bycNVi+yD7CTHRdxMI0gAi0XciUX'
    'og30IAHQJx0XwtBQAEOsHx0XwpBQAEItF8IlF5IN9CAB0CcdF7LwUABDrB8dF7KwUABCLReyJReDHRfgMFAAQx0X0IBQAEMdF/AA'
    'AAADrB4tF/ECJRfyDffwIfSKLRfgDRfyLTegDTfyKCYgIi0X0A0X8i03oA038igmICOvRx0X8AAAAAOsHi0X8QIlF/ItF5ANF/A+'
    '+AIXAdBOLRfgDRfyLTeQDTfyKCYhICOvZi0X4A0X8xkAIAMdF/AAAAADrB4tF/ECJRfyLReADRfwPvgCFwHQTi0X0A0X8i03gA03'
    '8igmISAjr2YtF9ANF/MZACADJw2tUJAg0M8CLTCQEgLwKiQAAAAQPlMDDVmiAAAAA/3QkEL54EgAQVuiH4f//VmoR/3QkHOgn6P/'
    '/g8QYXsNWM/Y7NTQUABB1CYX2dAUzwEDrAjPAUI1GIFD/dCQQ6Bjo//+DxAxGg/4IfNYzwDkFNBQAEF4PnMBQagX/dCQM6BHo//+'
    'DxAzDi0QkCFNWV4XAeBY7BcgTABB9DmnwugAAAAM1/BIAEOsCM/ZqKMcFNBQAEP////+NfjFbhfZ0B4A/AIvHdQW4FhsAEFBT/3Q'
    'kGOiL5///g8QMg8cRQ4P7MHXZX15b/3QkBOhR////WcOD7GhTi1wkcDPAVVZXi0skvRYbABBoyBQAEFGJTCRAiUQkLIlEJCCJRCQ'
    'oiUQkJMdEJDT+////iUQkOIlEJDCJrCSEAAAAo3ASABCjyBMAEMcFzBMAEP////+i0BMAEKP4EgAQopgQABDHBTQUABD/////6F3'
    'n////tCSYAAAA6If9//+DvCScAAAAALiEFAAQv5QUABBoIBQAEA9E+OjP7///vgwUABCFwIvOD0TPUWgYGwAQ6M3l//9oIBQAEOi'
    'u7///hcAPRPdWU+gx5v//i/BWiXQkXOhP5v//aCQbABDoWOH//4u8JLwAAAC4QBQAEIX/D0ToVWoeVuhx5v//M+2NhCS0AAAAVVB'
    'VVugr5v//g8RIhf90GGh4FAAQajBW6Evm//9q/1boav7//4PEFDmsJIQAAAB0O4C+fQoAAAR1EGg0GwAQajFW6CHm//+DxAxoWBs'
    'AEGofVugR5v///7QklAAAAFbovP3//4PEFOm+AQAAajmNRCRAaIAbABBQ6D/f//+LnCSMAAAAuIgbABBqOY2LgAAAAIA5AA9FwVC'
    'NRCRQUOhM3///g8QYgL59CgAABHUQjUQkPFBqMVboreX//4PEDGo5jUQkQGiMGwAQUOjt3v//ajmNRCRMU1DoEt///2o5jUQkWGg'
    'EGwAQUOgB3///D7eDAAEAAGo5UI1EJGhQ6A7f//+NRCRsUGofVuha5f//OasEAQAAubgbABC4mBsAEGiAAAAAD0TBUGh4EgAQ6Iv'
    'e//+DxEhoeBIAEGoRVugn5f//jUQkLFBW6Grl//9T/zWQEAAQ6Ar5//+DxByFwA+EngAAAGpQWIX/alVZD0XBM/+IRCR8RzmrBAE'
    'AALngGwAQuMwbABCJfCQkD0TBUOij3///V42EJIQAAABQ/zWQEAAQ6JHx//+DxBCFwHQ1i4sEAQAAOawkkAAAAHQauPAbABC6KBw'
    'AEIXJD0TCUFboTvz//4vd6x24YBwAELqMHAAQ6+RovBwAEFboM/z//4vfiVwkIFlZ/xUMEAAQiUQkKOsyoZAQABAFRNQAAFBW6A7'
    '8//+hkBAAEAVE1AAAUGjQHAAQ6Ffj//+DxBAz/0eL34lcJBg5bCQkD4Q3AQAAhdsPhS8BAAD/tCSMAAAA/zWQEAAQ6HD0//9ZWYX'
    'AeUqgeBIAEITAdAQ8Q3UXaIAAAABovBwAEGh4EgAQ6D7d//+DxAxoeBIAEGoRVuja4////zWQEAAQi9+JXCQo6Dnl//+DxBDpzQA'
    'AAIP4Aw+E7QMAADvHD4XZAQAAOawkkAAAAA+EsgEAAGjQEwAQah5W6Jbj////NcgTABBoABMAEFVW6FDj//+hyBMAEIPEHMdEJCz'
    '+////hcB1EmjcHAAQVugd+///ocgTABBZWVBoBB0AEOim4v//WVk5bCQcdVf/FQwQABArRCQoPbwCAAByRsZEJHxx/xUMEAAQiUQ'
    'kKI1EJHxXUP81kBAAEOjp7///g8QMhcB1H2i8HAAQVujA+v///zWQEAAQi9+JXCQk6Gfk//+DxAw5rCSQAAAAdBxVVujO4v//WVk'
    '7RCQsdA1QVolEJDTo+Pr//1lZjUQkIFBW6BLj//9ZWTvHD4VUAgAAg3wkIAQPhOYCAACDfCQgBQ+Fb/7//1VW6Ifi//9ZWYXbD4U'
    'LAgAAOWwkJA+EAQIAADlsJBwPhUr+//85rCSQAAAAD4XrAAAAhcAPiNkAAAA7BXASABAPjc0AAABrwD5ogAAAAGhgHQAQaHgSABD'
    'GRCQcUoqYoBAAEIhcJB3ojNv//w+2w7t4EgAQaIAAAABQU+jL2///aIAAAABocB0AEFPomtv//1NqEVboC+L//2oCjUQkRFD/NZA'
    'QABDoyO7//4PEPOlGAQAA/zVwEgAQaFQSABBVVuir4f//g8QQ6Yb+//+D+AIPhX3+//9oeBIAEGoRVujB4f//g8QMiWwkHOlq/v/'
    '/aLwcABBW6GL5////NZAQABCL34lcJCToCeP//4PEDOlc/f//aEgdABDpFAEAAIXAD4jwAAAAOwXIEwAQD43kAAAAiw00FAAQhck'
    'PiM8AAACLFfwSABBp2LoAAABrwRGDwDEDwwPCiUQkfIA4AA+EqAAAAIoEE2iAAAAAaKwdABBoeBIAEMZEJCBYiEQkIYhMJCLoctr'
    '//2iAAAAA/7QkjAAAAGh4EgAQ6I7a//9ogAAAAGhwHQAQaHgSABDoetr//2h4EgAQahFW6Ofg//+h/BIAEA+2BANQaMQdABDoJuD'
    '///81NBQAEGjYHQAQ6Bbg//+DxECNRCQUagNQ/zWQEAAQ6H3t//+DxAyFwA+E7v7//4l8JBzpsgAAAItcJBhojB0AEOscaHQdABD'
    'rFTmsJIQAAAC4HB0AEA9FhCSIAAAAUFboJfj//1lZ6TH8//85rCSQAAAAdH6D+AJ0BYP4A3V0i1wkIDlsJDB1HVBo5B0AEIl8JDj'
    'ojd///1No+B0AEOiC3///g8QQg8Pgg/sHdzVVVugH4P//WVmF23QohcB4JDsFyBMAEH0caci6AAAAa8MRAw38EgAQgHwIMQB0Bok'
    'dNBQAEFbox/f//1mLXCQY6ar7//+FwA+Fovv//1f/FRQQABDplvv//4vvjUQkNFDoZ9///8cEJMgUABD/dCQ86Cvg//9ZWYXtdRY'
    '5bCQkdBCF23UM/zWQEAAQ6ATh//9ZX16LxV1bg8Row1WL7IHsfAEAAFMz28dF/P////+JXfjo0dr//4XAD4RUAgAAVot1ELksHgA'
    'Qhfa4DB4AEA9EwVDoGtr//1mJNQAUABCJHQQUABCJHQgUABA5HTgUABB1ZmoEaAAwAABoAE0BAFP/FRwQABCLyIkNOBQAEIXJdQp'
    'oRB4AEOnvAAAAjYGk1AAAiQ2QEAAQo3QSABCNgaT0AACj/BMAEI2BpBQBAKOcEAAQjYGqGAEAo5QQABCNgaooAQCj/BIAEFfoWN/'
    '//4tdDI1FjGpgUI2FhP7//8YF9CdTAAJQx4PwFAAAAgAAAMZFjADoeOn//4v4g8QMhf90Eo1FjFBoWB4AEOib3f//WVnrQY2FBP/'
    '//1BoZB4AEOiG3f//jYWE/v//UGh0HgAQ6HXd//8Pt0WEUGiEHgAQ6KPd////dYholB4AEOiW3f//g8QgVo1F/FCNRYxQV42FhP7'
    '//1D/dQjotvb//4PEGF+FwHUPaKQeABDo4tj//+mSAAAA/3X8ucgeABCF9ri4HgAQD0TBUOhL3f//jUX4UOjQ8///g8QMhcB1GGj'
    'YHgAQ6KvY////NZAQABDoQ9///1nrUot1+A+3xlBo9B4AEOgS3f//WVmNRexQM8BQ/zWQEAAQaFE5ABBQUP8VEBAAEIXAdSb/NTw'
    'UABD/FUAQABD/NZAQABDHBTwUABD/////6O7e//9ZM8DrZlD/FQgQABAzwGaJdfBmiUXyx0X01BQAEOj83f//i/BWaAQfABDootz'
    '//2oAVlONRfBQ/3UI6O3d//9QaDgfABDoh9z//6E8FAAQg8Qkg/j/dBFQ/xVAEAAQxwU8FAAQ/////zPAQF5bycNqAP90JAz/dCQ'
    'M6Hf9//+DxAzDagH/dCQM/3QkDOhk/f//g8QMw4P/CHQUg/8JdRyLVfxQUujZ////g8QI6w2LVfxQUui3////g8QIuT1RQAD/4QA'
    'AAAD/////AAAAANZKAAABAAAAAwAAAAMAAAC4SgAAxEoAANBKAABjSgAAPUoAAFBKAADhSgAA8UoAAPxKAAAAAAEAAgBvbmxpbmU'
    'uZGxsAG9ubGluZV9kaXNwYXRjaABvbmxpbmVfd2FyAHJlcGxheV9nYW1lAA==","vsize":15112,"text_rva":4096,"image_'
    'base":268435456,"relocs":[4437,4639,4655,4679,4699,4729,4765,4772,4796,4821,4828,4837,4853,4865,4875'
    ',4887,4897,4909,4919,4934,4944,4956,4966,4978,4988,5000,5010,5025,5035,5047,5057,5069,5079,5091,5101'
    ',5116,5126,5138,5148,5160,5170,5182,5192,5207,5217,5229,5239,5251,5261,5273,5283,5298,5308,5320,5330'
    ',5342,5352,5364,5374,5389,5399,5411,5421,5433,5443,5455,5465,5480,5490,5502,5512,5524,5534,5546,5556'
    ',5571,5581,5593,5603,5615,5625,5644,5650,5671,5686,5697,5725,6248,6266,6306,6347,6370,6388,6625,6697'
    ',6850,7084,7117,7263,7361,7469,7476,7497,7616,7708,7819,7931,7961,8002,8022,8053,8069,8085,8109,8120'
    ',8137,8146,8206,8237,8251,8289,8304,8672,8689,8772,8777,8798,8825,9047,9087,9104,9121,9183,9252,9259'
    ',9322,9388,9532,9552,9596,9602,9635,9649,9686,9700,9726,9937,9946,10087,10101,10114,10130,10164,1017'
    '4,10233,10241,10269,10338,10346,10366,10382,10387,10410,10457,10474,10480,10495,10512,10523,10536,10'
    '547,10568,10591,10597,10604,10616,10622,10636,10666,10676,10695,10718,10758,10829,10876,10911,10927,'
    '10934,10945,10971,10991,11017,11040,11053,11126,11143,11183,11201,11252,11276,11305,11366,11389,1140'
    '8,11428,11455,11471,11484,11503,11590,11598,11651,11663,11669,11687,11693,11706,11723,11729,11735,11'
    '778,11787,11806,11815,11828,11835,12047,12079,12123,12160,12174,12186,12210,12268,12273,12322,12327,'
    '12333,12342,12347,12352,12358,12392,12397,12402,12415,12428,12438,12472,12489,12533,12575,12591,1263'
    '1,12649,12713,12743,12793,12798,12812,12825,12851,12896,12901,12929,12961,12966,12987,12992,12999,13'
    '019,13030,13047,13058,13106,13122,13140,13145,13158,13172,13226,13240,13245,13257,13277,13288,13296,'
    '13315,13337,13353,13370,13382,13540,13559,13564,13575,13592,13614,13642,13661,13666,13695,13720,1373'
    '2,13756,13775,13787,13801,13843,13848,13883,13898,13903,13913,13926,13936,13947,13952,13973,14007,14'
    '014,14028,14085,14100,14139,14156,14169,14200,14224,14255,14315,14322,14338,14344,14350,14356,14377,'
    '14385,14394,14411,14416,14427,14438,14449,14460,14526,14547,14564,14579,14592,14638,14656,14663,1469'
    '3,14704,14724,14744,14749,14757,14767,14773,14779,14785,14806,14823,14836,14863,14873,14888,14894],"'
    'exports":{"online_dispatch":14947,"online_war":14909,"replay_game":14928},"sha":"200a339e944eb7f0a45'
    '7ce1c7a47d87b58f0cb4440dd5b912e471b44c3aa1872"}'
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
    drop = {b'label': {18}, b'pushb': {21}, b'group': {19, 20}, b'textmsg': {4, 5}, b'in_text': {W_HEADER, W_SERVER, W_NAME}, b'gadget': {11, 14}}
    texts = {1: b'Online War', 2: b'ENTER'}
    # below the list: the name line (DEFAULT_SERVER.TXT `name=`; maintainer, 3 Oct 2026: 'add a line with "Name:" above
    # the "Server:" line'), the server line (host:port; maintainer, 29 Sep 2026: "server name must be mentioned as a
    # first line of two right above [the] TLS connection") and the status line
    name_line = b'in_text  %d  0  %d  %d   %d    1  0  -  read_only' % (W_NAME, lx, ly + lh + NAME_DY, ROW_CHARS)
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
                out.append(name_line + cr)
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


def bare_shipped_config(data):
    """True for a DEFAULT_SERVER.TXT of the first form (before 3 Oct 2026) that still names only the shipped relay:
    comments off (the module's rule: `//` counts at a line start or after white space), exactly one token,
    `dark-colony-server.fly.dev`.  Such a file is upgraded to the `name=` / `address=` form; anything else is the
    player's own setting and stays.  The patcher's Write-OnlineScreen applies the same test."""
    text = data.decode('latin1')
    text = re.sub(r'(?s)/\*.*?\*/', ' ', text)
    text = re.sub(r'(?m)(^|\s)//.*$', r'\1', text)
    return [t.lower() for t in text.split()] == ['dark-colony-server.fly.dev']


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
    if not cfg or bare_shipped_config(open(cfg, 'rb').read()):
        what = 'DEFAULT_SERVER.TXT (name= / address= dark-colony-server.fly.dev; written when missing or still the bare shipped address)'
        cfg = cfg or os.path.join(game, 'DEFAULT_SERVER.TXT')
        done.append((cfg, what))
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

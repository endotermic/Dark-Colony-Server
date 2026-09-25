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

Dark Colony Ultimate (the Council Wars build) since 25 Sep 2026 - a music SOURCE (maintainer: "add new
entry for music selection with possibility to select: original game, expansion pack, shuffle them all.
entries must be named: DC, CW, ALL"; docs/DC16_DISPLAY_AND_RESOLUTION.md 10.41):

    * the module (assembled by tools/music_asm.py, same entry points) keeps a source byte in its state
      block: 0 "DC" = MUSIC\\TRACK02-05.MP3, 1 "CW" = exp\\music\\track02-05.mp3, 2 "ALL" = the eight
      tracks in a random order (Fisher-Yates over a private LCG seeded from rdtsc - the game's rand()
      drives the simulation and is never touched; reshuffled after eight tracks, never the same
      track twice in a row).  The track index 0..7 (set = index / 4) replaces the track number.
    * the battlefield options dialog (interface.c LOPTE) gets a MUSIC row: buttons 71 "-" / 72 "+"
      cycle the source, in_text 73 shows textmsg 20/21/22 = DC / CW / ALL.  Two in-place hooks: the
      dialog handler's tail (`test bl,bl / call close` -> `opt_tail`, which also restarts the music
      on the new source when something plays) and the refresh routine's epilogue (-> `opt_refresh_tail`,
      which writes the text when the script has widget 73 - the stock dialog has not, so the hook
      is harmless with an old data set).  The row is data: music_row() adds it to the dialog script
      and `apply` writes the copies the three campaign modes read - HD `exp/intrf_hd/lopte`,
      `dc/intrf_hd/lopte`, `ozi_ns/intrf_hd/lopte` from INTRF_HD/LOPTE; 640x480 `exp/intrface/lopme`,
      `dc/intrface/lopme`, `ozi_ns/intrface/lopme` from INTRFACE/LOPTE, because at that size the exe
      would read exp/intrface/lopte, a file the ORIGINAL exe reads too, so the script name in DGROUP
      becomes `intrface/lopm` (+ language letter) and the stock dialog stays untouched.
    * the default per campaign is set by the OZI menu modes (patch_ozi_menu.py writes the byte in its
      mode stubs): ACADEMY / DARK COLONY / LOAD DC GAME -> DC, COUNCIL WARS / LOAD CW GAME -> CW,
      OZI MISSIONS / LOAD OZI GAME and MULTI PLAYER WAR -> ALL; the dialog changes it at any time.

    --width W --height H   640x480 adds the DGROUP name site and writes the 640x480 copies

CLI
    python patch_music.py verify EXE
    python patch_music.py plan   EXE
    python patch_music.py apply  EXE        (writes EXE.music.bak first; Council Wars: + the dialog copies)
"""

import argparse
import hashlib
import os
import re
import shutil
import struct
import sys

SIZE_OF = {659456: 'classic', 659968: 'cw', 742912: 'classic', 743424: 'cw'}   # + the `icon` fix's appended section
IMAGE_BASE = 0x400000
AUTO_VA_TO_FILE = 0x400C00
MODULE_LEN = 0x751          # cd_open .. the `ret` of the mode mapper (0x004510D0..0x00451820 inclusive)
AUX_LEN = 0x6E              # the aux-device volume walk (0x00452870..0x004528DD), followed by the entry-point jmp
TEMPLATE = {'classic': b'music\\track0?.mp3', 'cw': b'exp\\music\\track0?.mp3'}
DIGIT = {'classic': 12, 'cw': 16}                     # index of the '?' in the template
# --- generated by music_asm.py (do not edit) ---
CODE_CW = bytes.fromhex('5256e8d9000000a1108e48006bc064bed02753008946080f3131d083c801894630c6462500e8b6020000e8e10000005e5ac30000000000000000000031c0c300000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000eb5600000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000515256bed02753008b0685c074106a006a00680808000050ff157005480031c05e5a59c300000000515256bed02753008b0685c074146a006a00680408000050ff157005480031c089065e5a59c300000000000000000000515256575589e583ec1489c2be0c134500b90c000000f6c204740abef0134500b910000000bfd027530083c70c5751b906000000f3a5595f89d083e003043288040f89fe83ee0c89560431c08945ec8945f0c745f400134500897df88945fc8d45ec50680222000068030800006a00ff157005480085c075118b45f089068b4e08e86a0000008b06eb0231c089ec5d5f5e5a59c300000000000000000000000000000000000000000000000000000000000000000000000000000000000000006d706567766964656f0000006d757369635c747261636b303f2e6d70330000000000000000000000000000000000000051525589e583ec1831d28955e8c745ec02400000894df08955f48955f88955fc8d55e8526800008001687308000050ff157005480089ec5d5a59c300000000005152565589e583ec10bed02753008b0685c0745731d28955f08955f4c745f8040000008955fc8d55f0526800010000681408000050ff157005480085c0752c817df40d020000751ae8f301000050e84dfeffff58e877feffff85c0740ee8ae01000031c089ec5d5e5a59c3b804000000ebf200000000000000000000000000006578705c6d757369635c747261636b303f2e6d7033000000000000000000000056bed02753000fb646243c027305c1e002eb0de848000000c64625000fb646265ec300000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000031c0c300000000005051525731c9884c0e264183f90872f6b9070000008b463069c06d4ec6410539300000894630c1e81031d28d7901f7f78a440e268a64162688640e26884416264975d28a46263a460475098a66278866268846275f5a5958c300000000000000e83bfdffffe836ffffffe861fdffff85c0740ae898000000a1d0275300c300005152565789c7bed027530083fa47740c83fa4875310fb6462440eb070fb6462483c0023c0372022c03884624833d7097480000750a833e007405e8a1ffffff89f8e8f216feff84db740789f8e84310feff5f5e5a59c30000000000000000000080b95d0f0000047521bed02753000fb6562483c21489c8e8ac11fdff89c3ba4900000089c8e85a29fdff5d5e5a595bc351525589e583ec0c31d28955f48955f88955fc8d55f4526a00680608000050ff157005480089ec5d5a59c300000000005156bed02753000fb64e2480f902730e8b46044083e003c1e10209c8eb180fb64625403c087207e894feffff31c08846250fb64406265e59c3000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000052e8b2feffff85c07405b8010000005ac300000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000e95bfbffff000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000')
FIXUPS_CW = [(8, 'VOL', 0), (16, 'STATE', 0), (188, 'STATE', 0), (210, 'IAT', 0), (228, 'STATE', 0), (250, 'IAT', 0), (285, 'MOD', 476), (300, 'MOD', 704), (310, 'STATE', 0), (357, 'MOD', 464), (385, 'IAT', 0), (561, 'IAT', 0), (586, 'STATE', 0), (631, 'IAT', 0), (738, 'STATE', 0), (953, 'STATE', 0), (967, 'STATE', 0), (1006, 'FLAG', 0), (1026, 'REL_REFRESH', 0), (1037, 'REL_CLOSE_DLG', 0), (1066, 'STATE', 0), (1080, 'REL_TEXTMSG', 0), (1094, 'REL_SETTEXT', 0), (1137, 'IAT', 0), (1155, 'STATE', 0)]
STRINGS_CW = {'str_mpegvideo': 464, 'template_dc': 476, 'template_cw': 704}
# --- end generated ---

# Ultimate (Council Wars) only: the state block's source byte, the options-dialog hooks and the dialog row.
SRC_OFFSET = 36                 # state + 36: 0 DC, 1 CW, 2 ALL (patch_ozi_menu.py writes the per-mode default)
CW_STATE_IMM = 0x10             # CODE_CW: the imm32 of cd_open's `mov esi, state` (+0xF opcode)
OPT_TAIL, OPT_REFRESH_TAIL = 0x3C0, 0x420      # module offsets (music_asm.py LAYOUT)
# interface.c: the options refresh routine's epilogue (its start 14 bytes later = the handler) ...
# (both anchored on the code AFTER them, so a patched exe is recognised too)
REFRESH_TAIL_ANCHOR = '8B C0 53 51 52 56 57 55 89 E5 83 EC 28 89 C6 8B 00 8D 55 F8 8B 00 30 DB E8'   # = the handler's head
REFRESH_TAIL_LEN = 12                                                                       # 12 bytes before it
REFRESH_HEAD_PATTERN = '53 51 52 56 55 89 E5 81 EC 80 00 00 00 81 ED FE 00 00 00 89 C1 8B 00 8B 50 04'
# ... and the handler's tail: close the dialog when bl is set, then the epilogue
HANDLER_TAIL_ANCHOR = '89 EC 5D 5F 5E 5A 59 5B C3 53 51 52 56 57 55 89 E5 89 C1 8B 40 08 BB ?? ?? ?? ?? 31 D2 8B 40 24'
HANDLER_TAIL_LEN = 11                                                                       # 11 bytes before it
CLOSE_DLG_PATTERN = '53 51 52 56 55 89 E5 89 C1 8B 00 8B 00 E8 ?? ?? ?? ?? 89 C3 89 C6 8B 01 E8 ?? ?? ?? ?? 8B 41 08'
TEXTMSG_CALL = '83 C2 0A 8B 00 E8'             # in the refresh body: textmsg(ip, 10 + detail)
SETTEXT_CALL = 'BA 2E 00 00 00 8B 00 E8'       # in the refresh body: set in_text 0x2E
LAYER_START_PATTERN = '83 3D ?? ?? ?? ?? 00 75 14 31 C0 BA 02 00 00 00 66 A1'   # ddex4.c music start: the "no CD audio" flag
DIALOG_NAME = b'intrf_hd/lopt\0'               # DGROUP string of the options dialog script (HD form, after `hdpaths`)
DIALOG_NAME_640 = (b'intrface/lopt\0', b'intrface/lopm\0')   # 640x480: the exe reads exp/intrface/lopme instead
DIALOG_COPIES_HD = ('exp/intrf_hd/lopte', 'dc/intrf_hd/lopte', 'ozi_ns/intrf_hd/lopte')
DIALOG_COPIES_640 = ('exp/intrface/lopme', 'dc/intrface/lopme', 'ozi_ns/intrface/lopme')
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


def build_module_v1(g, mod_va, state, iat, vol):
    """The 22 Sep 2026 module (one track set, the disc's "everything from track 2" loop)."""
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


def build_module(g, mod_va, state, iat, vol, ext=None):
    """The module for game `g`: Classic = the one-set player, Council Wars = the source-aware one
    (`ext` = the pattern-located externals: flag, refresh, close_dlg, textmsg, settext)."""
    if g == 'classic':
        return build_module_v1(g, mod_va, state, iat, vol)
    code = bytearray(CODE_CW)
    for off, kind, add in FIXUPS_CW:
        if kind == 'STATE':
            v = state + add
        elif kind == 'IAT':
            v = iat
        elif kind == 'VOL':
            v = vol
        elif kind == 'FLAG':
            v = ext['flag']
        elif kind == 'MOD':
            v = mod_va + add
        elif kind.startswith('REL_'):
            v = (ext[kind[4:].lower()] - (mod_va + off + 4)) & 0xFFFFFFFF
        else:
            raise AssertionError(kind)
        struct.pack_into('<I', code, off, v)
    assert len(code) == MODULE_LEN
    return bytes(code)


def build_aux(aux_va, mod_va, state):
    code = bytearray(AUX_CODE)
    struct.pack_into('<I', code, AUX_STATE8, state + 8)
    struct.pack_into('<I', code, AUX_STATE, state)
    struct.pack_into('<i', code, AUX_CALL + 1, (mod_va + L_SETVOL) - (aux_va + AUX_CALL + 5))
    return bytes(code) + b'\0' * (AUX_LEN - len(code))


def module_state_address(img, mod_va):
    """The state block's address from whatever form the module is in (stock / v1 / current)."""
    d, mod_file = img.data, va2file(mod_va)
    if all(img.matches(mod_va + o, p) for o, p in STOCK_ENTRIES.items()):
        toc = img.find(TOC_STORE_PATTERN, 'cd_read_toc store')
        if not mod_va <= toc < mod_va + MODULE_LEN:
            raise SystemExit('cd_read_toc store outside the module')
        return struct.unpack_from('<I', d, va2file(toc) + 2)[0], 'stock'
    if d[mod_file:mod_file + 5] == CODE[:5]:               # v1: cd_open starts with `call close_state`
        off = next(o for o, k, a in FIXUPS if k == 'STATE' and a == 8)
        return struct.unpack_from('<I', d, mod_file + off)[0] - 8, 'v1'
    if d[mod_file:mod_file + 2] == CODE_CW[:2] and d[mod_file + 0xF] == CODE_CW[0xF]:
        return struct.unpack_from('<I', d, mod_file + CW_STATE_IMM)[0], 'v2'
    raise SystemExit('the cdaudio module is in an unknown state')


def music_state(data):
    """(state block VA, form) for other tools (patch_ozi_menu.py writes the source byte at state + SRC_OFFSET)."""
    img = Image(data)
    mo = img.find(MUSIC_OPEN_PATTERN, 'ddex4.c music_open')
    mod_va = mo + 10 + struct.unpack_from('<i', data, va2file(mo) + 6)[0]
    return module_state_address(img, mod_va)


def call_target(img, site_va):
    return site_va + 5 + struct.unpack_from('<i', img.data, va2file(site_va) + 1)[0]


def externals(img):
    """Council Wars: the addresses the module's dialog code calls into, all by pattern."""
    tail = img.find(REFRESH_TAIL_ANCHOR, 'options refresh epilogue') - REFRESH_TAIL_LEN
    head = img.find(REFRESH_HEAD_PATTERN, 'options refresh routine')
    htail = img.find(HANDLER_TAIL_ANCHOR, 'options handler tail') - HANDLER_TAIL_LEN
    if not head < tail < htail < tail + 0x400:
        raise SystemExit('options dialog code is not laid out as expected')
    body = img.auto[head - img.auto_va:tail - img.auto_va]
    tm = re.search(pat(TEXTMSG_CALL), body, re.S)
    st = re.search(pat(SETTEXT_CALL), body, re.S)
    if not tm or not st:
        raise SystemExit('textmsg / set-text calls not found in the refresh routine')
    flag_site = img.find(LAYER_START_PATTERN, 'music layer start')
    return dict(refresh_tail=tail, handler_tail=htail, refresh=head, handler=tail + REFRESH_TAIL_LEN + 2,
                textmsg=call_target(img, head + tm.end() - 1),
                settext=call_target(img, head + st.end() - 1),
                close_dlg=img.find(CLOSE_DLG_PATTERN, 'options dialog close'),
                flag=struct.unpack_from('<I', img.data, va2file(flag_site) + 2)[0])


def hook_sites(img, mod_va, ext):
    """The two in-place hooks of the options dialog (Council Wars): [(note, file, va, old, new)]."""
    t, h = ext['refresh_tail'], ext['handler_tail']
    old_t = bytes(img.data[va2file(t):va2file(t) + 12])
    old_h = bytes(img.data[va2file(h):va2file(h) + 11])
    new_t = bytes.fromhex('8D A5 FE 00 00 00') + b'\xE9' + struct.pack('<i', (mod_va + OPT_REFRESH_TAIL) - (t + 11)) + b'\x90'
    new_h = bytes.fromhex('89 F0 8B 55 F8') + b'\xE8' + struct.pack('<i', (mod_va + OPT_TAIL) - (h + 10)) + b'\x90'
    stock_t = bytes.fromhex('8D A5 FE 00 00 00 5D 5E 5A 59 5B C3')
    stock_h = bytes.fromhex('84 DB 74 07 89 F0 E8') + struct.pack('<i', ext['close_dlg'] - (h + 11))
    # the handler reaches its tail for EVERY event kind (`cmp eax,1 / jne tail`); the original tail only
    # tested the close flag, which press events alone set - opt_tail reads the widget id, so the other
    # event kinds (the button's release) must skip it: that `jne` now goes straight to the epilogue
    j = ext['handler'] + 0x1E
    old_j = bytes(img.data[va2file(j):va2file(j) + 6])
    stock_j = bytes.fromhex('0F 85') + struct.pack('<i', h - (j + 6))
    new_j = bytes.fromhex('0F 85') + struct.pack('<i', (h + 11) - (j + 6))
    if img.data[va2file(j) - 3:va2file(j)] != bytes.fromhex('83 F8 01'):
        raise SystemExit('options handler: `cmp eax,1` not where expected')
    return [('options refresh epilogue -> jmp opt_refresh_tail (in_text 73 = DC / CW / ALL when the dialog has it)',
             va2file(t), t, [stock_t], new_t, old_t),
            ('options handler tail -> call opt_tail (buttons 71 / 72 cycle the music source, then the original close)',
             va2file(h), h, [stock_h], new_h, old_h),
            ('options handler: events other than a button press skip the tail (jne tail -> jne epilogue; the original tail only tested the press-set close flag)',
             va2file(j), j, [stock_j], new_j, old_j)]


def sites_for(img, g, stock_mode=False):
    """Return (sites, relocs, info): sites = [(note, file_off, va, [accepted old bytes], new_bytes, current)],
    relocs = [(pos, old, new)]."""
    d = img.data
    mo = img.find(MUSIC_OPEN_PATTERN, 'ddex4.c music_open')
    mod_va = mo + 10 + struct.unpack_from('<i', d, va2file(mo) + 6)[0]
    sv = img.find(SET_VOLUME_PATTERN, 'set_volume')
    aux_va = sv + 38 + struct.unpack_from('<i', d, va2file(sv) + 34)[0]
    iat = img.iat_of('WINMM.dll', 'mciSendCommandA')
    vol_site = img.find(VOL_PATTERN, 'music level start-up copy')
    vol = struct.unpack_from('<I', d, va2file(vol_site) + 1)[0]
    mod_file, aux_file = va2file(mod_va), va2file(aux_va)
    state, form = module_state_address(img, mod_va)
    if not (d[aux_file + AUX_LEN] == 0xE9):
        raise SystemExit('entry-point jmp not found after the aux walk')
    ext = externals(img) if g == 'cw' else None
    new_mod = build_module(g, mod_va, state, iat, vol, ext)
    new_aux = build_aux(aux_va, mod_va, state)
    cur_mod, cur_aux = bytes(d[mod_file:mod_file + MODULE_LEN]), bytes(d[aux_file:aux_file + AUX_LEN])
    old_mods = [STOCK_SHA[g][0]]                          # accepted old forms: the stock block (by SHA-256) ...
    if g == 'cw':
        old_mods.append(build_module_v1(g, mod_va, state, iat, vol))   # ... and the 22 Sep 2026 module
    what = ('cdaudio module -> MP3 player on MCI mpegvideo (seven entry points kept: cd_open +0, play_from_here +0x3C, '
            'close +0x88, stop +0xB8, tracks +0x338, seek_track +0x4E8, mode +0x6E0; helpers, "mpegvideo" and the path '
            'template "%s" inside; the rest zero)' % TEMPLATE[g].decode().replace('\\', '\\\\'))
    if g == 'cw':
        what = ('cdaudio module -> MP3 player on MCI mpegvideo with a music source DC / CW / ALL (seven entry points kept: '
                'cd_open +0, play_from_here +0x3C, close +0x88, stop +0xB8, tracks +0x338, seek_track +0x4E8, mode +0x6E0; '
                'helpers, the options-dialog code opt_tail +0x3C0 / opt_refresh_tail +0x420, "mpegvideo" and the two path '
                'templates music\\\\track0?.mp3 / exp\\\\music\\\\track0?.mp3 inside; the rest zero)')
    sites = [(what, mod_file, mod_va, old_mods, new_mod, cur_mod),
             ('set_volume method 0: aux CD-audio device walk -> store level*100, MCI_SETAUDIO volume on the open element (the rest zero)',
              aux_file, aux_va, [STOCK_SHA[g][1]], new_aux, cur_aux)]
    if g == 'cw':
        sites += hook_sites(img, mod_va, ext)
        if stock_mode:
            old, new = DIALOG_NAME_640
            hits = [m.start() for m in re.finditer(re.escape(old), d)]
            cur = None
            if len(hits) == 1:
                cur = old
            else:
                hits = [m.start() for m in re.finditer(re.escape(new), d)]
                cur = new if len(hits) == 1 else None
            if cur is None:
                raise SystemExit('options dialog name string not found in DGROUP (640x480 form expected)')
            off = hits[0]
            sites.append(('options dialog script name "intrface/lopt" -> "intrface/lopm" (640x480: the exe reads exp/intrface/lopme, '
                          'the stock dialog with the MUSIC row, written by apply / the patcher; the original exe keeps its file)',
                          off, off + (DGROUP_VA_TO_FILE_CW if g == 'cw' else DGROUP_VA_TO_FILE_CLASSIC), [old], new, cur))
    # .reloc: the old absolute operands inside the two blocks -> the new ones (same page), the rest -> type 0
    relocs = []
    fixups = FIXUPS_CW if g == 'cw' else FIXUPS
    mod_offs = sorted(o for o, k, a in fixups if not k.startswith('REL_'))
    for base_va, length, new_offs in ((mod_va, MODULE_LEN, mod_offs), (aux_va, AUX_LEN, [AUX_STATE8, AUX_STATE])):
        page = (base_va - IMAGE_BASE) & ~0xFFF
        lo = base_va & 0xFFF
        old = img.relocs(page, lo, lo + length)
        new_vals = [(3 << 12) | (lo + o) for o in new_offs]
        assert all(lo + o < 0x1000 for o in new_offs)
        olds = sorted(e for _, e in old)
        if all(e in olds for e in new_vals) and len(old) == len(new_vals):
            relocs += [(pos, e, e) for pos, e in old]        # already in the current form
            continue
        if len(old) < len(new_vals):
            raise SystemExit('page %#x: %d .reloc entries in the block, %d needed' % (page, len(old), len(new_vals)))
        for k, (pos, e) in enumerate(sorted(old, key=lambda x: x[1])):
            relocs.append((pos, e, new_vals[k] if k < len(new_vals) else 0))
    relocs = [(pos, e, n) for pos, e, n in relocs if e != n]     # an entry both codes use at the same offset needs no edit
    info = dict(mod_va=mod_va, aux_va=aux_va, state=state, iat=iat, vol=vol, form=form)
    if ext:
        info.update(ext)
    return sites, relocs, info


DGROUP_VA_TO_FILE_CW, DGROUP_VA_TO_FILE_CLASSIC = 0x402600, 0x402800


def state_of(data, sites, relocs):
    st = []
    for note, off, va, olds, new, cur in sites:
        if cur == new:
            st.append('patched')
        elif any((cur == o) if isinstance(o, bytes) else hashlib.sha256(cur).hexdigest() == o for o in olds):
            st.append('stock')
        else:
            st.append('other')
    for off, old, new in relocs:
        e = struct.unpack_from('<H', data, off)[0]
        if old == new:                      # an entry that keeps its value follows the module block's state
            st.append(st[0] if e == new else 'other')
        else:
            st.append('patched' if e == new else 'stock' if e == old else 'other')
    return st


# ------------------------------------------------------------------- the dialog's MUSIC row (data)
ROW_STEP = 32                    # one dialog row = 32 px (two 16-px frame cells)
MUSIC_LABEL = 6                  # textmsg numbers: the row label ...
MUSIC_TEXTS = (20, 21, 22)       # ... and the three values
MUSIC_NAMES = (b'MUSIC', b'DC', b'CW', b'ALL')
NEW_IDS = dict(pushb=(71, 72), in_text=73, label=74, cell=22, frame=(20, 21))


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


def music_row(data):
    """Add the MUSIC row to the battlefield options dialog script (LOPTE, stock or HD): the GAME DETAIL
    row (pushb 44/45, in_text 48, label 63, its cell picture 19), the bottom frame cell (picture 15)
    and the OK / cancel buttons (55/56) move down one row; the new row takes GAME DETAIL's old place
    with pushb 71/72, in_text 73, label 74 (textmsg 6 MUSIC) and cell picture 22; two middle frame
    cells (pictures 20/21) fill the gap; textmsg 20/21/22 = DC / CW / ALL; the erase rect grows by a
    row.  Cloned lines keep the script's own column layout.  Idempotent (a script with pushb 71 is
    returned unchanged); the stock file mixes line endings, each line keeps its own."""
    if re.search(rb'(?m)^\s*pushb\s+71\s', data):
        return data
    move = {(b'pushb', 44), (b'pushb', 45), (b'in_text', 48), (b'label', 63), (b'picture', 19),
            (b'picture', 15), (b'pushb', 55), (b'pushb', 56)}
    clone = {(b'pushb', 44): {2: b'71'}, (b'pushb', 45): {2: b'72'}, (b'in_text', 48): {2: b'73'},
             (b'label', 63): {2: b'74', 9: b'%d' % MUSIC_LABEL}, (b'picture', 19): {2: b'22'}}
    out, frame14 = [], None
    for raw in data.split(b'\n'):
        line, cr = (raw[:-1], b'\r') if raw.endswith(b'\r') else (raw, b'')
        m = re.match(rb'\s*(pushb|in_text|label|picture|textmsg|size)\s+(\d+)\s', line)
        if not m:
            out.append(line + cr)
            continue
        kind, n = m.group(1), int(m.group(2))
        if kind == b'size':
            toks = re.findall(rb'\S+', line)
            out.append(set_tokens(line, {len(toks): b'%d' % (int(toks[-1]) + ROW_STEP)}) + cr)
            continue
        if kind == b'picture' and n == 14:
            frame14 = line
        if (kind, n) in clone:                       # the new widget at the old place ...
            out.append(set_tokens(line, clone[(kind, n)]) + cr)
        if (kind, n) in move:                        # ... and the old one a row lower
            y = int(re.findall(rb'\S+', line)[4])
            line = set_tokens(line, {5: b'%d' % (y + ROW_STEP)})
        if kind == b'picture' and n == 15 and frame14 is not None:
            y14 = int(re.findall(rb'\S+', frame14)[4])
            for k, fid in enumerate(NEW_IDS['frame']):
                out.append(set_tokens(frame14, {2: b'%d' % fid, 5: b'%d' % (y14 + 16 * (k + 1))}) + cr)
        out.append(line + cr)
        if kind == b'textmsg' and n == 12:
            for num, text in zip((MUSIC_LABEL,) + MUSIC_TEXTS, MUSIC_NAMES):
                out.append(b'textmsg %d %s' % (num, text) + cr)
    return b'\n'.join(out)


def find_ci(folder, name):
    if not os.path.isdir(folder):
        return None
    for f in os.listdir(folder):
        if f.lower() == name.lower():
            return os.path.join(folder, f)
    return None


def write_dialogs(game, stock_mode):
    """Write the Council Wars copies of the options dialog with the MUSIC row; returns the paths written."""
    src = find_ci(os.path.join(game, 'INTRFACE' if stock_mode else 'INTRF_HD'), 'lopte')
    if not src:
        return []
    new = music_row(open(src, 'rb').read())
    out = []
    for rel in DIALOG_COPIES_640 if stock_mode else DIALOG_COPIES_HD:
        if rel.startswith('ozi_ns/') and not os.path.isdir(os.path.join(game, 'ozi_ns')):
            continue                                  # no OZI data: no copy (dc/ is created, it holds only this and the menu)
        dst = os.path.join(game, *rel.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not (os.path.exists(dst) and open(dst, 'rb').read() == new):
            open(dst, 'wb').write(new)
        out.append(dst)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('verify', 'plan', 'apply'))
    ap.add_argument('exe')
    ap.add_argument('--width', type=int, default=1024, help='screen size the exe is patched for (Council Wars: 640x480 adds the dialog name site and writes the 640x480 dialog copies)')
    ap.add_argument('--height', type=int, default=768)
    a = ap.parse_args(argv)
    data = bytearray(open(a.exe, 'rb').read())
    if len(data) not in SIZE_OF:
        raise SystemExit('%s: %d bytes - not a dc16.exe (659456) or ENGEXP16.EXE (659968) build' % (a.exe, len(data)))
    g = SIZE_OF[len(data)]
    stock_mode = (a.width, a.height) == (640, 480)
    img = Image(data)
    sites, relocs, info = sites_for(img, g, stock_mode)
    st = state_of(data, sites, relocs)
    summary = ('stock' if all(s == 'stock' for s in st) else 'patched' if all(s == 'patched' for s in st)
               else 'partially patched (%d of %d sites)' % (st.count('patched'), len(st)))
    if 'other' in st:
        raise SystemExit('%s: unexpected bytes at %d site(s): %s' % (a.exe, st.count('other'),
                         ', '.join(sites[i][0][:40] if i < len(sites) else '.reloc' for i, s in enumerate(st) if s == 'other')))
    print('%s: music %s (%d code blocks, %d .reloc entries; module VA %#x, aux walk VA %#x, state %#x, IAT %#x, level %#x%s)'
          % (a.exe, summary, len(sites), len(relocs), info['mod_va'], info['aux_va'], info['state'], info['iat'], info['vol'],
             ', dialog handler %#x, refresh %#x, layer flag %#x' % (info['handler_tail'], info['refresh'], info['flag']) if g == 'cw' else ''))
    if a.command == 'verify':
        return 0
    for note, off, va, olds, new, cur in sites:
        print('  %s  file 0x%x VA 0x%x %d bytes' % (note, off, va, len(new)))
    for off, old, new in relocs:
        print('  .reloc @ file %#x: %04X -> %04X' % (off, old, new))
    game = os.path.dirname(os.path.abspath(a.exe))
    copies = (DIALOG_COPIES_640 if stock_mode else DIALOG_COPIES_HD) if g == 'cw' else ()
    if copies:
        print('  dialog copies with the MUSIC row (from %s/LOPTE): %s' % ('INTRFACE' if stock_mode else 'INTRF_HD', ', '.join(copies)))
    if a.command == 'plan':
        return 0
    if summary != 'patched':
        bak = a.exe + '.music.bak'
        shutil.copyfile(a.exe, bak)
        for (note, off, va, olds, new, cur), s in zip(sites, st):
            if s == 'stock':
                data[off:off + len(new)] = new
        for (off, old, new), s in zip(relocs, st[len(sites):]):
            if s == 'stock':
                struct.pack_into('<H', data, off, new)
        open(a.exe, 'wb').write(data)
        print('written %s (%d code blocks, %d .reloc entries); backup %s' % (a.exe, len(sites), len(relocs), bak))
    else:
        print('exe already patched')
    if copies:
        written = write_dialogs(game, stock_mode)
        print('dialog copies: %s' % (', '.join(os.path.relpath(w, game) for w in written) or 'source LOPTE not found, none written'))
    return 0


if __name__ == '__main__':
    sys.exit(main())

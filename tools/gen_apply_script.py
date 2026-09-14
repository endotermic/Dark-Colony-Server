"""Generate Apply-DarkColonyPatches.ps1 - the self-documenting PowerShell patcher that lives in the ROOT of
the Dark-Colony game repository (next to "DC - Classic" and "DC - Council wars", maintainer decision 14 Sep 2026).

The PowerShell script lets a player rebuild the patched dc16.exe / DCEXP16.EXE from the untouched
originals committed in the Dark-Colony repository, one patch at a time, with every changed byte
listed and explained.  This generator produces it by *replaying* the Python patch tools of this
folder on copies of the originals, diffing after each step (so every byte is attributed to
exactly one patch) and taking the per-edit descriptions from the tools' `plan` output.

    python tools/gen_apply_script.py "<path to Dark-Colony>" "<path to Dark-Colony>/Apply-DarkColonyPatches.ps1"

Needs: DC - Classic/dc16original1998.exe and DC - Council wars/engexp16original.exe in the game
repository, and the patch_*.py tools beside this file.  The generated script is validated here:
the sum of the per-patch edits must reproduce every intermediate exe, and the end result is
hashed into the script as the reference for "all patches applied".  Re-run after adding a patch
(add it to PATCHES/BUILDS below, with a block parser for its plan output).  The OZI patch is
kept last because its 16-byte .reloc insert shifts every later relocation entry.
"""
import re, struct, hashlib, sys, os, shutil, subprocess, tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
GAME = sys.argv[1]
OUT = sys.argv[2]
WORK = tempfile.mkdtemp(prefix='dcpatch_')

CD_BYTES = {'classic': [(0x431F, 0xEB), (0x509F, 0xEB)],
            'cw': [(0x431F, 0xEB), (0x781D9, 0x74), (0x507F, 0xEB)]}
ORIGINALS = {'classic': os.path.join(GAME, 'DC - Classic', 'dc16original1998.exe'),
             'cw': os.path.join(GAME, 'DC - Council wars', 'engexp16original.exe')}
TOOL_OF = {'resolution': 'patch_resolution.py', 'hdpaths': 'patch_hd_paths.py', 'cursor': 'patch_cursor.py',
           'pool': 'patch_pool.py', 'speed': 'patch_speed.py', 'clock': 'patch_clock.py',
           'ddraw': 'patch_ddraw_lost.py', 'ozi': 'patch_ozi_menu.py'}
PLAN_OF = {'resolution': 'resolution', 'hdpaths': 'hd_paths', 'cursor': 'cursor', 'pool': 'pool', 'speed': 'speed',
           'clock': 'clock', 'ddraw': 'ddraw_lost', 'ozi': 'ozi_menu'}
_plans = {}

def run_tool(tool, cmd, exe):
    r = subprocess.run([sys.executable, os.path.join(TOOLS, tool), cmd, exe], capture_output=True, text=True)
    if cmd == 'apply' and r.returncode != 0:
        raise SystemExit(f'{tool} apply failed on {exe}:\n{r.stdout}\n{r.stderr}')
    return r.stdout + r.stderr

def replay(g, steps):
    """Return (original bytes, [(step, bytes after that step)]) and fill _plans[(g, plan name)]."""
    orig = open(ORIGINALS[g], 'rb').read()
    work = os.path.join(WORK, f'{g}.exe')
    cur = bytearray(orig)
    for off, v in CD_BYTES[g]:
        assert cur[off] == 0x75, hex(off)
        cur[off] = v
    open(work, 'wb').write(cur)
    # plans: patch_resolution identifies builds by MD5 and wants the CD-fixed exe; the others take the original
    _plans[(g, 'resolution')] = run_tool('patch_resolution.py', 'plan', work)
    orig_copy = os.path.join(WORK, f'{g}_orig.exe')
    open(orig_copy, 'wb').write(orig)
    for step, tool in TOOL_OF.items():
        if step != 'resolution' and step in steps:
            _plans[(g, PLAN_OF[step])] = run_tool(tool, 'plan', orig_copy)
    states = [('cdcheck', bytes(cur))]
    for step in steps[1:]:
        run_tool(TOOL_OF[step], 'apply', work)
        states.append((step, open(work, 'rb').read()))
    return orig, states

def runs_of(prev, nxt):
    offs = [i for i in range(len(prev)) if prev[i] != nxt[i]]
    runs = []
    for o in offs:
        if runs and o == runs[-1][0] + runs[-1][1]:
            runs[-1][1] += 1
        else:
            runs.append([o, 1])
    return [(o, n) for o, n in runs]

def plan(g, tool):
    return _plans[(g, tool)]

# ----------------------------------------------------------------------------------------------
# per-patch block parsers: return [(offset, length, note)]
# ----------------------------------------------------------------------------------------------
def blocks_resolution(g):
    out = []
    text = plan(g, 'resolution')
    rows = [l for l in text.splitlines() if re.match(r'^\s+0x[0-9A-Fa-f]+\s', l)]
    rx = re.compile(r'^\s+(0x[0-9A-Fa-f]+)\s+(.+?)\s+((?:[0-9a-f]{2} )*[0-9a-f]{2})\s+->\s+((?:[0-9a-f]{2} )*[0-9a-f]{2})\s*$')
    for l in rows:
        m = rx.match(l)
        assert m, l
        off = int(m.group(1), 16)
        old = bytes.fromhex(m.group(3).replace(' ', '')); new = bytes.fromhex(m.group(4).replace(' ', ''))
        assert len(old) == len(new)
        out.append((off, len(old), m.group(2).strip(), old, new))
    assert len(out) == len(rows)
    return out

def reloc_lines(text, note_fmt):
    out = []
    for m in re.finditer(r'\.reloc @ file 0x([0-9a-f]+): ([0-9A-Fa-f]{4}) -> ([0-9A-Fa-f]{4})', text):
        a, b = int(m.group(2), 16), int(m.group(3), 16)
        ta, tb = a >> 12, b >> 12
        if tb == 0:
            what = f'entry {a:04X} (type {ta} HIGHLOW, page offset 0x{a & 0xfff:03X}) -> 0000: the absolute operand it described no longer exists, entry becomes type 0 ABSOLUTE padding'
        elif ta == tb:
            what = f'entry {a:04X} -> {b:04X}: the absolute operand moved from page offset 0x{a & 0xfff:03X} to 0x{b & 0xfff:03X}, entry follows it'
        else:
            what = f'entry {a:04X} -> {b:04X}: type {ta} -> {tb} (page offset kept)'
        out.append((int(m.group(1), 16), 2, note_fmt + what))
    return out

def blocks_cursor(g):
    t = plan(g, 'cursor')
    out = []
    for m in re.finditer(r'^\s+(.+?)\s+file 0x([0-9a-f]+)\s+VA 0x[0-9a-f]+\s+(\d+) bytes', t, re.M):
        out.append((int(m.group(2), 16), int(m.group(3)), m.group(1).strip()))
    out += reloc_lines(t, '.reloc table: ')
    return out

def blocks_ozi(g):
    t = plan(g, 'ozi_menu')
    out = []
    for m in re.finditer(r'^\s+(.+?)\s+file\s+0x([0-9a-f]+)\s+VA 0x[0-9a-f]+\s+(\d+) bytes', t, re.M):
        out.append((int(m.group(2), 16), int(m.group(3)), m.group(1).strip()))
    return out

def blocks_ddraw(g):
    t = plan(g, 'ddraw_lost')
    out = []
    lens = {'remap: Unlock failure -> next index': 5, 'remap: Lock failure -> next index': 5,
            'remap: GetDC failure -> next index': 5, 'loading screen: Flip failure -> continue': 2}
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+): stock', t, re.M):
        name = m.group(1).strip()
        out.append((int(m.group(2), 16), lens[name], name))
    out += reloc_lines(t, '.reloc table: ')
    return out

def blocks_pool(g):
    m = re.search(r'site file 0x([0-9a-f]+)', plan(g, 'pool'))
    return [(int(m.group(1), 16), 5, 'mov eax,imm32 before call SMalloc_Pool: pool size 11 500 000 (0x00AF79E0) -> 33 554 432 bytes (0x02000000, 32 MiB)')]

def blocks_speed(g):
    t = plan(g, 'speed'); out = []
    m = re.search(r'tick_ms initialiser\s+file 0x([0-9a-f]+)', t)
    out.append((int(m.group(1), 16), 4, 'game-state initialiser: imm32 of mov dword ptr [esi+970h],imm32 (gs->tick_ms) 66 ms -> 44 ms'))
    m = re.search(r'persistent setting \(desired tick\)\s+file 0x([0-9a-f]+)', t)
    out.append((int(m.group(1), 16), 4, 'DGROUP: persistent "desired tick" settings global (4th of four settings dwords) 66 ms -> 44 ms'))
    return out

def blocks_clock(g):
    t = plan(g, 'clock')
    m = re.search(r'y dword at file 0x([0-9a-f]+), x dword at file 0x([0-9a-f]+)', t)
    return [(int(m.group(1), 16), 4, 'clock_draw: imm32 of mov edx,ANCHOR_Y - bottom-right anchor y 450 (0x1C2) -> 738 (0x2E2)'),
            (int(m.group(2), 16), 4, 'clock_draw: imm32 of mov eax,ANCHOR_X - bottom-right anchor x 608 (0x260) -> 992 (0x3E0)')]

def blocks_hdpaths(g):
    t = plan(g, 'hd_paths'); out = []
    for m in re.finditer(r'^\s+"([^"]+)" -> "([^"]+)"\s+file 0x([0-9a-f]+) VA 0x[0-9a-f]+ 8 bytes: (.+)$', t, re.M):
        out.append((int(m.group(3), 16), 8, 'DGROUP string "%s" -> "%s": %s' % (m.group(1), m.group(2), m.group(4).strip())))
    assert len(out) == 30, len(out)
    return out

def blocks_cdcheck(g):
    if g == 'classic':
        return [(0x431F, 1, 'jne -> jmp right after "call 0x405E8C ; test al,al" (the CD-presence check returning a bool in al): always take the "CD present" path'),
                (0x509F, 1, 'jne -> jmp after the same CD test in the main-menu builder: the menu buttons that are greyed out without the CD stay enabled')]
    return [(0x431F, 1, 'jne -> jmp right after "call 0x405E8C ; test al,al" (the CD-presence check returning a bool in al): always take the "CD present" path'),
            (0x507F, 1, 'jne -> jmp after the same CD test in the main-menu builder: the menu buttons that are greyed out without the CD stay enabled'),
            (0x781D9, 1, 'jne -> je: the second CD check, run later, that threw the player out of a running game; inverted so it passes without the disc')]

# ----------------------------------------------------------------------------------------------
# patch catalogue (canonical application order)
# ----------------------------------------------------------------------------------------------
PATCHES = [
 dict(id='cdcheck', name='No CD required', date='28-30 Sep 2025', tool='hand-patched (commits f6246e1, 314ae66 in endotermic/Dark-Colony)',
      doc='CLAUDE.md "Patches applied so far"', blocks=blocks_cdcheck,
      desc='''The game refuses to start, and greys out most main-menu buttons, when it cannot find its
CD in a drive.  One function (VA 0x405E8C) answers "is the CD here?" with a bool in al and every
caller does "test al,al ; jne ok".  Turning that conditional jump (opcode 75) into an
unconditional jump (opcode EB) makes the game behave as if the disc were always present.
Council Wars has a third test that runs while a game is in progress and throws the player back
to the menu; that one is inverted (75 -> 74, jne -> je).

Nothing else changes: no code is added, no file access is redirected, one byte per site.'''),
 dict(id='resolution', name='1024x768 display', date='9 Sep 2026', tool='tools/patch_resolution.py (Dark-Colony-Server)',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md sections 8-10', blocks=blocks_resolution,
      desc='''The engine is hard-wired for 640x480: the DirectDraw display mode, the framebuffer stride
(y*640 done as shl 7 + add), clip rectangles, the map viewport (20x15 tiles), the minimap
position, the movie blit, the 44 code-positioned main-menu elements, the terrain light plane's
512-byte row advances and the size of draw_terrain's stack lightmap.  Every one of those
constants was read out of the disassembly and is replaced by the 1024x768 equivalent here:
  stage 1  display mode, framebuffer stride, clip rect
  stage 2  full-screen chrome, mouse, cursor clip, loading screens, and the 44 menu elements
           moved by (+192,+144) - the same offset the letterboxed 640x480 menu screens use
  stage 3  map viewport 896x736 (28x23 tiles) at (4,6), minimap 96x84 at (903,6), the 31
           lightplane row advances, and a bigger stack frame for draw_terrain (so the PE
           header's SizeOfStackReserve / SizeOfStackCommit go up as well - the two edits at
           file offsets 0xE0 / 0xE4)
  stage 4  movies: pitch-aware back-buffer clear and the 320x180 movie frames stretched to
           (0,96)-(1024,672) through IDirectDrawSurface::Blt
Every edit swaps one immediate constant or one arithmetic opcode inside an existing
instruction; no code is added and no instruction moves.  Council Wars is the same code at
+0x60 (AUTO) / +0x28 (DGROUP) with three site fixups, hence the slightly different offsets.

REQUIRES the rebuilt 1024x768 interface data that ships in the repository next to the exe -
since 14 Sep 2026 in the INTRF_HD/ folder, read through the "Interface data from INTRF_HD"
patch below (select both); with stock 640x480 data the menus draw in the top-left corner.'''),
 dict(id='hdpaths', name='Interface data from INTRF_HD (1024x768 files renamed)', date='14 Sep 2026', tool='tools/patch_hd_paths.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.17', blocks=blocks_hdpaths,
      desc='''The 1024x768 menus, HUD frame, loading screens, briefing-marker lists and re-baked logo sprites
used to replace the stock files under their stock names, so the untouched original exe could no
longer run from the same folder.  They now live under their stock names in INTRF_HD/ (Council Wars
also exp/intrf_hd/ and ozi_ns/intrf_hd/), the stock 640x480 files are back in INTRFACE/ and
GAMESTAT/, and the re-baked logo animations are SPRITES/DCSS_HD.SPR, DCUK_HD.SPR, DCUT_HD.SPR with
matching ANIMATE/*_HD.FIN.  The game opens each of those files through a literal path in the data
section ("intrface/bintro" plus the language letter, "gamestat/hscene" plus ".txt", ...), so this
patch rewrites the 8-byte directory part of exactly the 30 strings whose files were rebuilt:
"intrface" / "gamestat" -> "intrf_hd", same length, in place.  Fonts, text files, per-screen
sprite lists without logo banks and every other file keep their stock path and single copy; the two
lists that do name logo banks (INTRG.DAT, INTRO.DAT) are redirected to INTRF_HD copies that say
dcuk_hd.fin etc.  No code changes.  With this patch dc16original1998.exe / engexp16original.exe
(stock data) and the patched exe (INTRF_HD data) run side by side from one folder.  Only
meaningful together with the 1024x768 patch, and REQUIRES the INTRF_HD/ folder from the repository.'''),
 dict(id='cursor', name='Windows pointer stays hidden', date='10 Sep 2026', tool='tools/patch_cursor.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.12', blocks=blocks_cursor,
      desc='''The game draws its own cursor and hides the Windows pointer with SetCursor(NULL), but two
holes let the system pointer flicker back on a modern Windows: create_window never fills
WNDCLASSA.hCursor (a random stack value becomes the class cursor), and the window procedure
answers WM_SETCURSOR with SetCursor(NULL) and then falls through into DefWindowProcA, which
restores the class cursor.  Fix: hCursor = NULL, WM_SETCURSOR returns TRUE, and a 12-byte stub
in the zero tail of the code section (VA 0x47F1D0 / 0x47F230) calls SetCursor(NULL) right
after ShowWindow so the pointer is gone during the loading screen too.

Watcom's 7-byte "call cs:[import]" instructions become 5-byte relative calls to the import
thunks the linker already emitted, which frees the bytes for the new instructions without
moving any code.  The .reloc entries that described the moved or removed absolute operands
are updated (moved operand -> new page offset; vanished operand -> type 0 ABSOLUTE padding),
so the relocation table still describes the image exactly.  The stub lives in bytes that were
zero and inside the section's raw size, so the file layout is unchanged.'''),
 dict(id='pool', name='Local memory pool 11.5 MB -> 32 MiB', date='10 Sep 2026', tool='tools/patch_pool.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.13', blocks=blocks_pool,
      desc='''Everything the game keeps for a session (sprite banks, screen backgrounds, light plane, map
info, game state, AI, widgets) is carved from one arena created at start-up with
"mov eax,11500000 ; call SMalloc_Pool".  At 1024x768 the backgrounds alone grow from 307 KB to
786 KB each, and extra sprite banks exhausted the arena ("SMalloc: Out of memory in local
pool" in error.log).  The fix is the constant: 0x00AF79E0 -> 0x02000000 (32 MiB).  Block
headers are 32-bit and the size check unsigned, so nothing else changes.'''),
 dict(id='speed', name='Default game speed 150 %', date='10 Sep 2026', tool='tools/patch_speed.py --percent 150',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.14', blocks=blocks_speed,
      desc='''One simulation tick runs every gs->tick_ms milliseconds.  Two stock values feed it and both
must change or the game resets the speed within a second: the game-state initialiser
("mov dword ptr [esi+970h],66") and the persistent "desired tick" setting in DGROUP that the
options screen and the speed negotiation read.  66 ms = 100 %, 44 ms = 150 % (the slider shows
6600 / tick_ms).  Multiplayer speed comes from the server, saved games keep their own speed.
Cosmetic; pick it if you like the faster default.'''),
 dict(id='clock', name='Day/night clock hand re-anchored', date='13 Sep 2026', tool='tools/patch_clock.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.15', blocks=blocks_clock,
      desc='''The HUD's day/night hand is a sprite cell that clock.c blits by code with its bottom-right
corner at (608,450) - two plain immediates that are neither 640 nor 480, so the resolution
sweep did not touch them.  At 1024x768 that point lies inside the enlarged map view and the
terrain paints over the hand every frame.  The anchor moves to (992,738), where the rebuilt
HUD frame has the clock face.  Only meaningful together with the 1024x768 patch.'''),
 dict(id='ddraw', name='Two-monitor start-up hang fixed', date='13 Sep 2026', tool='tools/patch_ddraw_lost.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.16', blocks=blocks_ddraw,
      desc='''With two monitors, SetDisplayMode(1024,768,16) makes Windows re-lay out the desktop and
DirectDraw marks every exclusive-mode surface lost about a second later.  The stock start-up
code then asserts (palette remap: GetDC / Lock / Unlock; loading screen: Flip) into a
MessageBox hidden behind the full-screen surface: black screen, apparent hang.  The four
assert branches become "skip and continue": three "push format-string" instructions (5 bytes)
turn into "jmp next-palette-index", and the Flip check's je becomes jmp.  The game's own
per-frame restore path repairs the surfaces at the first frame.  The three push operands were
absolute pointers, so their .reloc entries become type 0 ABSOLUTE padding.'''),
 dict(id='ozi', name='OZI MISSIONS menu mode (Council Wars only)', date='10 Sep 2026', tool='tools/patch_ozi_menu.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.13', blocks=blocks_ozi, cw_only=True,
      desc='''Council Wars opens every data file through one helper that prefixes the name with the
8-byte string at DGROUP 0x4826D0 ("exp/"); the wave loader has its own copy and the save
folder name "esave" sits in two more slots.  A campaign *mode* is therefore the content of
those four writable slots.  This patch turns the unused PLAY INTRO button into OZI MISSIONS
and SINGLE PLAYER WAR into OZI LOAD, so the 2010 ozi_ns mission pack (22 missions) plays from
the main menu:
  * the PLAY INTRO handler body (96 bytes) becomes: set the two campaign flags, call
    stub_pack (writes "ozi_ns/" / "ozisave" into the four slots), enter the campaign runner;
    the rest is NOP padding
  * NEW CAMPAIGN / TRAINING / LOAD GAME go through trampolines that first write the Council
    Wars strings back ("exp/" / "esave"), OZI LOAD through one that writes the pack strings
  * the two 73-byte stubs and three 10-byte trampolines live in the zero tail of the code
    section (VA 0x47F240..0x47F30A) - bytes that were zero and already inside the section
  * the eight "mov edi,imm32" slot addresses in the stubs are absolute, so eight HIGHLOW
    entries are appended to the .reloc block of page 0x7F000: 16 bytes inserted, the block's
    size field and the PE base-relocation directory size grow by 16, and 16 zero bytes of
    slack at the end of the .reloc section are dropped so the file size stays the same.  The
    two absolute operands that vanished with the old PLAY INTRO body become type 0 padding.
  * the start-up animation list is opened as "animozi.dat" instead of "anim.dat" (one 12-byte
    string in the data section): exp/animozi.dat is the stock list plus the pack's three new
    units and its transport as "tranozi", so the stock exp/anim.dat, tran.fin and tran.spr that
    the original exe reads stay untouched.
REQUIRES the "DC - Council wars/ozi_ns/" overlay folder, exp/animozi.dat, exp/animate/tranozi.fin,
exp/sprites/tranozi.spr and the rewritten main-menu script (exp/intrf_hd/bintroe) from the
repository.  Because the .reloc insert shifts every later relocation entry, this patch is always
applied last.'''),
]

BUILDS = [
 dict(id='Classic', g='classic', exe='dc16.exe', orig_name='dc16original1998.exe',
      title='Dark Colony (Classic) dc16.exe, build linked 7 Jan 1998, 659456 bytes',
      steps=['cdcheck', 'resolution', 'hdpaths', 'cursor', 'pool', 'speed', 'clock', 'ddraw']),
 dict(id='CouncilWars', g='cw', exe='DCEXP16.EXE', orig_name='engexp16original.exe',
      title='Dark Colony - The Council Wars ENGEXP16.EXE (shipped as DCEXP16.EXE), 659968 bytes',
      steps=['cdcheck', 'resolution', 'hdpaths', 'cursor', 'pool', 'speed', 'clock', 'ddraw', 'ozi']),
]

def hexs(b):
    return ' '.join(f'{x:02X}' for x in b)

def ps_str(s):
    return "'" + s.replace("'", "''") + "'"

out = []
W = out.append

# ----------------------------------------------------------------------------------------------
# build the data, validating every step against the replay
# ----------------------------------------------------------------------------------------------
build_data = []
for B in BUILDS:
    g = B['g']
    cur, states = replay(g, B['steps'])
    orig_sha = hashlib.sha256(cur).hexdigest()
    patches_out = []
    for step, nxt in states:
        P = next(p for p in PATCHES if p['id'] == step)
        blocks = P['blocks'](g)
        edits = []          # (kind, offset, old, new, note)
        covered = set()
        special = None
        if step == 'ozi':
            # model: point edits outside the shifted region + one 16-byte insert
            pe = struct.unpack_from('<I', cur, 0x3c)[0]
            nsec = struct.unpack_from('<H', cur, pe + 6)[0]; opt = struct.unpack_from('<H', cur, pe + 20)[0]
            s = pe + 24 + opt; secs = {}
            for i in range(nsec):
                n = cur[s:s + 8].rstrip(b'\0').decode(); vs, va, rs, ro = struct.unpack_from('<IIII', cur, s + 8); secs[n] = (ro, rs); s += 40
            rstart, rend = secs['.reloc'][0], sum(secs['.reloc'])
            p = rstart; blk = None
            while p < rend:
                page, size = struct.unpack_from('<II', cur, p)
                if size == 0: break
                if page == 0x7F000: blk = (p, size)
                p += size
            ins_at = blk[0] + blk[1]
            ins = nxt[ins_at:ins_at + 16]
            assert cur[rend - 16:rend] == b'\0' * 16 and nxt[ins_at + 16:rend] == cur[ins_at:rend - 16]
            special = dict(offset=ins_at, bytes=ins, before=cur[ins_at:ins_at + 16], section_end=rend,
                           note=f'.reloc table: insert 8 HIGHLOW entries ({", ".join(f"{v:04X}" for v in struct.unpack("<8H", ins))}) at the end of the page-0x7F000 block; bytes 0x{ins_at:X}..0x{rend-16:X} move up by 16, the 16 zero slack bytes 0x{rend-16:X}..0x{rend:X} at the end of the section are dropped')
            covered.update(range(ins_at, rend))
            dirsz = pe + 24 + 96 + 5 * 8 + 4
            blocks = blocks + [
                (dirsz, 4, f'PE optional header: base-relocation directory size 0x{struct.unpack_from("<I", cur, dirsz)[0]:X} -> 0x{struct.unpack_from("<I", nxt, dirsz)[0]:X} (+16)'),
                (blk[0] + 4, 4, f'.reloc block for page 0x7F000 (header at 0x{blk[0]:X}): SizeOfBlock 0x{blk[1]:X} -> 0x{blk[1]+16:X}'),
            ]
            # page 0x5000 neutralised entries: find leftover runs inside .reloc before ins_at
        for blk_ in blocks:
            off, n, note = blk_[0], blk_[1], blk_[2]
            old, new = cur[off:off + n], nxt[off:off + n]
            if len(blk_) == 5:
                assert (old, new) == (blk_[3], blk_[4]), (g, step, hex(off), note, old.hex(), blk_[3].hex())
            assert old != new or n == 0, (g, step, hex(off), note)
            edits.append(('bytes', off, old, new, note)); covered.update(range(off, off + n))
        leftover = [(o, n) for o, n in runs_of(cur, nxt) if not any(i in covered for i in range(o, o + n))]
        for o, n in leftover:
            if step == 'ozi':
                words = struct.unpack(f'<{n//2}H', cur[o:o + n])
                note = '.reloc table, page-0x5000 block: entries ' + ', '.join(f'{w:04X}' for w in words) + ' -> 0000 (the absolute operands of the removed PLAY INTRO body at VA 0x4050DE / 0x405103 no longer exist; type 0 ABSOLUTE padding)'
            else:
                note = 'see patch description'
            edits.append(('bytes', o, cur[o:o + n], nxt[o:o + n], note))
        # split partially-overlapping? ensure no two edits overlap
        spans = sorted((e[1], e[1] + len(e[2])) for e in edits)
        for a, b in zip(spans, spans[1:]):
            assert a[1] <= b[0], (g, step, hex(a[0]), hex(b[0]))
        # validate: applying edits (+ insert) to cur gives nxt
        t = bytearray(cur)
        for _, off, old, new, _ in edits:
            assert bytes(t[off:off + len(old)]) == old
            t[off:off + len(new)] = new
        if special:
            e = special['section_end']; i = special['offset']
            t = bytearray(bytes(t[:i]) + special['bytes'] + bytes(t[i:e - 16]) + bytes(t[e:]))
        assert bytes(t) == nxt, (g, step)
        edits.sort(key=lambda e: e[1])
        patches_out.append(dict(P=P, edits=edits, special=special, nbytes=sum(1 for i in range(len(cur)) if cur[i] != nxt[i]), leftover=len(leftover)))
        print(f'{g:8s} {step:11s} {len(edits):4d} edits ({len(leftover)} unannotated runs), {patches_out[-1]["nbytes"]} bytes, insert={bool(special)}')
        cur = nxt
    build_data.append(dict(B=B, orig_sha=orig_sha, final_sha=hashlib.sha256(cur).hexdigest(), size=len(cur), patches=patches_out))

# ----------------------------------------------------------------------------------------------
# emit PowerShell
# ----------------------------------------------------------------------------------------------
W(r'''<#
.SYNOPSIS
    Rebuilds the patched Dark Colony executables from the untouched originals, one documented
    patch at a time, so that anyone can see exactly which bytes change and why.

.DESCRIPTION
    The executables shipped in https://github.com/endotermic/Dark-Colony are the original 1997/98
    binaries with a handful of byte patches (no CD check, 1024x768, cursor fix, ...).  Because a
    hand-modified exe cannot be signed and looks suspicious to antivirus heuristics, this script
    makes the modification fully transparent and reproducible:

      * run without arguments it opens a window: pick the original, tick the fixes you want (the first
        box selects all of them), press Apply - or drive it from the command line, see the examples
      * it never touches the input file; it writes a new file
      * every patch is a list of (file offset, old bytes, new bytes, reason) in plain text below
      * a byte is only written if the file still holds the documented old bytes at that offset
      * the SHA-256 of the input must match the known original (override with -Force, the
        per-byte checks stay on)
      * after writing it prints the SHA-256 of the result; with every patch selected the result
        is byte-identical to the executable published in the repository and the script says so

    The originals: "DC - Classic\dc16original1998.exe" (the dc16.exe of the January 1998 update,
    6 sections, entry point 0x4528DE) and "DC - Council wars\engexp16original.exe" (ENGEXP16.EXE
    from the Council Wars CD).  Both are committed untouched in the repository (commit 9b1b286).

    The script is complete in itself: it uses nothing but the .NET classes that ship with Windows
    PowerShell 5.1 / PowerShell 7 (System.IO.File, System.Security.Cryptography.SHA256, Windows Forms).
    No Python, no downloads, no external tools, no network access - a plain Windows installation is
    enough.  (Python is only used by the maintainer to regenerate this file from the repository.)  Read it top to bottom: the logic is ~200 lines at the end, the rest is data.

    How the patches were found is documented in the sister repository
    https://github.com/endotermic/Dark-Colony-Server, folder docs/ (DC16_DISPLAY_AND_RESOLUTION.md
    in particular) and tools/ (the Python patchers whose output this file reproduces).  This
    file was generated from those tools by replaying them on the originals and diffing after every
    step; the "old bytes" of every edit are therefore the bytes of the original (or of the previous
    patch in the fixed order below), and the sum of all patches is exactly the shipped exe.

.PARAMETER Original
    Path of the untouched original executable (dc16original1998.exe or engexp16original.exe).

.PARAMETER Output
    Where to write the patched copy.  Default: dc16.exe / DCEXP16.EXE next to the original.
    An existing file is not overwritten unless -Overwrite is given.

.PARAMETER Patches
    Patch ids to apply (see -List).  Order does not matter: they are always applied in the fixed
    canonical order.  Use -All for every patch of the build.  With neither, the window opens
    (with the original preloaded when -Original was given).

.PARAMETER Verify
    Instead of patching, inspect an existing exe: which build it is and which patches it carries.

.EXAMPLE
    .\Apply-DarkColonyPatches.ps1                               # the window
    .\Apply-DarkColonyPatches.ps1 -List
    .\Apply-DarkColonyPatches.ps1 -List -Detail                 # every single byte edit
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Classic\dc16original1998.exe" -All
        (run from the root of the Dark-Colony repository, where this file lives)
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Classic\dc16original1998.exe" -Patches cdcheck,resolution,hdpaths,pool
    .\Apply-DarkColonyPatches.ps1 -Verify "DC - Classic\dc16.exe"

.NOTES
    If Windows refuses to run the script ("running scripts is disabled"), start it once with
        powershell -ExecutionPolicy Bypass -File .\Apply-DarkColonyPatches.ps1
    Offsets are 0-based file offsets, written as PowerShell hex literals (0x431F).  Bytes are
    upper-case hex separated by spaces.  Code addresses quoted in the comments are virtual
    addresses (VA) inside the loaded image: VA = file offset + 0x400C00 for code, DGROUP data
    VA = file offset + 0x402800 (Classic) / 0x402600 (Council Wars).
#>
[CmdletBinding(DefaultParameterSetName = 'Apply')]
param(
    [Parameter(ParameterSetName = 'Apply', Position = 0)] [string] $Original,
    [Parameter(ParameterSetName = 'Apply')] [string] $Output,
    [Parameter(ParameterSetName = 'Apply')] [string[]] $Patches,
    [Parameter(ParameterSetName = 'Apply')] [switch] $All,
    [Parameter(ParameterSetName = 'Apply')] [switch] $Overwrite,
    [Parameter(ParameterSetName = 'Apply')] [switch] $Force,
    [Parameter(ParameterSetName = 'List')] [switch] $List,
    [Parameter(ParameterSetName = 'List')] [switch] $Detail,
    [Parameter(ParameterSetName = 'Verify')] [string] $Verify
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

# =================================================================================================
#  DATA - the two builds and their patches
#
#  Each edit:  @{ Offset = <file offset>; Old = '<hex bytes>'; New = '<hex bytes>'; Note = '<why>' }
#  One special edit kind (OZI patch only): @{ Insert = <offset>; Bytes = '<16 bytes>'; Before = '<the
#  16 bytes found there before>'; SectionEnd = <offset>; Note = ... } - inserts Bytes at Insert and
#  drops the 16 zero bytes just before SectionEnd, so the file size does not change.
# =================================================================================================
$Builds = @(
''')

for bd in build_data:
    B = bd['B']
    W(f'''    # ---------------------------------------------------------------------------------------------
    #  {B['title']}
    # ---------------------------------------------------------------------------------------------
    @{{
        Id             = {ps_str(B['id'])}
        Title          = {ps_str(B['title'])}
        OriginalName   = {ps_str(B['orig_name'])}
        OutputName     = {ps_str(B['exe'])}
        Size           = {bd['size']}
        OriginalSha256 = {ps_str(bd['orig_sha'])}   # untouched original
        PatchedSha256  = {ps_str(bd['final_sha'])}   # every patch applied = the exe in the repository (14 Sep 2026)
        Patches        = @(''')
    for pd in bd['patches']:
        P = pd['P']
        desc_lines = P['desc'].split('\n')
        W(f'''
            # ---- {P['id']}: {P['name']} ---------------------------------------------------------
            #  Added      : {P['date']}
            #  Made with  : {P['tool']}
            #  Documented : {P['doc']}
            #  Changes    : {pd['nbytes']} bytes in {len(pd['edits']) + (1 if pd['special'] else 0)} edits''')
        for l in desc_lines:
            W(f'            #  {l}'.rstrip())
        W(f'''            @{{
                Id = {ps_str(P['id'])}; Name = {ps_str(P['name'])}; Date = {ps_str(P['date'])}
                Tool = {ps_str(P['tool'])}; Doc = {ps_str(P['doc'])}
                Description = @'
{P['desc']}
'@
                Edits = @(''')
        for kind, off, old, new, note in pd['edits']:
            if pd['special'] and off > pd['special']['offset']:
                # should not happen (all point edits lie before the insert, except none) - keep order anyway
                pass
            W(f'                    # {note}')
            W(f'                    @{{ Offset = 0x{off:X}; Old = {ps_str(hexs(old))}; New = {ps_str(hexs(new))} }}')
        if pd['special']:
            sp = pd['special']
            W(f'                    # {sp["note"]}')
            W(f'                    @{{ Insert = 0x{sp["offset"]:X}; Bytes = {ps_str(hexs(sp["bytes"]))}; Before = {ps_str(hexs(sp["before"]))}; SectionEnd = 0x{sp["section_end"]:X} }}')
        W('                )\n            }')
    W('        )\n    }')
W(')')

W(r'''
# =================================================================================================
#  LOGIC - byte level helpers
# =================================================================================================
function ConvertFrom-HexString([string] $Hex) {
    $parts = $Hex.Trim() -split '\s+'
    $bytes = New-Object byte[] $parts.Count
    for ($i = 0; $i -lt $parts.Count; $i++) { $bytes[$i] = [Convert]::ToByte($parts[$i], 16) }
    return ,$bytes   # the comma keeps a 1-byte array an array (PowerShell would unroll it)
}

function Get-Sha256Hex([byte[]] $Data) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($Data)) -replace '-', '').ToLower() }
    finally { $sha.Dispose() }
}

function Test-BytesAt([byte[]] $Data, [int] $Offset, [byte[]] $Expected) {
    if ($Offset + $Expected.Length -gt $Data.Length) { return $false }
    for ($i = 0; $i -lt $Expected.Length; $i++) { if ($Data[$Offset + $i] -ne $Expected[$i]) { return $false } }
    return $true
}

# 'old' = the file still holds the documented original bytes, 'new' = the patched bytes, 'other' = neither.
function Get-EditState([byte[]] $Data, $Edit) {
    if ($Edit.ContainsKey('Insert')) {
        if (Test-BytesAt $Data $Edit.Insert (ConvertFrom-HexString $Edit.Bytes))  { return 'new' }
        if (Test-BytesAt $Data $Edit.Insert (ConvertFrom-HexString $Edit.Before)) { return 'old' }
        return 'other'
    }
    if (Test-BytesAt $Data $Edit.Offset (ConvertFrom-HexString $Edit.New)) { return 'new' }
    if (Test-BytesAt $Data $Edit.Offset (ConvertFrom-HexString $Edit.Old)) { return 'old' }
    return 'other'
}

# Applies one patch to a byte array and returns the new array.  Every edit is checked first;
# nothing is written unless all of them still hold their documented old bytes.
function Invoke-Patch([byte[]] $Data, $Patch) {
    foreach ($e in $Patch.Edits) {
        $state = Get-EditState $Data $e
        if ($state -ne 'old') {
            $where = if ($e.ContainsKey('Insert')) { '0x{0:X}' -f $e.Insert } else { '0x{0:X}' -f $e.Offset }
            throw ("fix '{0}': the bytes at file offset {1} are {2} - expected the documented original bytes. " +
                   "Is this the untouched original exe?") -f $Patch.Id, $where, $(if ($state -eq 'new') { 'already patched' } else { 'unknown' })
        }
    }
    $out = [byte[]] $Data.Clone()
    foreach ($e in $Patch.Edits) {
        if ($e.ContainsKey('Insert')) {
            [byte[]] $ins = ConvertFrom-HexString $e.Bytes
            $end = $e.SectionEnd
            for ($i = $end - $ins.Length; $i -lt $end; $i++) {
                if ($out[$i] -ne 0) { throw "fix '$($Patch.Id)': the section slack before 0x$('{0:X}' -f $end) is not zero, cannot insert" }
            }
            # shift [Insert, SectionEnd-16) up by 16, then drop in the new bytes
            [Array]::Copy($out, $e.Insert, $out, $e.Insert + $ins.Length, $end - $ins.Length - $e.Insert)
            [Array]::Copy($ins, 0, $out, $e.Insert, $ins.Length)
        } else {
            [byte[]] $new = ConvertFrom-HexString $e.New
            [Array]::Copy($new, 0, $out, $e.Offset, $new.Length)
        }
    }
    return ,$out
}

function Get-EditCount($Patch) { $n = 0; foreach ($e in $Patch.Edits) { $n++ }; return $n }

function Find-BuildBySha([string] $Sha) { foreach ($b in $Builds) { if ($b.OriginalSha256 -eq $Sha) { return $b } }; return $null }

# Guess the build of an arbitrary exe from its size and the state of the cdcheck edits.
function Find-BuildByContent([byte[]] $Data) {
    foreach ($b in $Builds) {
        if ($Data.Length -ne $b.Size) { continue }
        $ok = $true
        foreach ($e in $b.Patches[0].Edits) { if ((Get-EditState $Data $e) -eq 'other') { $ok = $false } }
        if ($ok) { return $b }
    }
    return $null
}

# The byte edits of one patch as text lines (what -List -Detail and the window show).
function Get-EditLines($Patch) {
    $lines = @()
    foreach ($e in $Patch.Edits) {
        if ($e.ContainsKey('Insert')) {
            $lines += ('insert @0x{0:X6}  {1}   (16 zero bytes dropped before 0x{2:X})' -f $e.Insert, $e.Bytes, $e.SectionEnd)
        } else {
            $lines += ('@0x{0:X6}  {1}  ->  {2}' -f $e.Offset, $e.Old, $e.New)
        }
    }
    return $lines
}

# Inspects an exe: which build, which patches it carries.  Returns text lines.
function Get-VerifyReport([string] $Path) {
    $data = [System.IO.File]::ReadAllBytes((Resolve-Path $Path).Path)
    $sha = Get-Sha256Hex $data
    $lines = @(('{0}' -f $Path), ('{0} bytes, SHA-256 {1}' -f $data.Length, $sha), '')
    $b = Find-BuildBySha $sha
    if ($b) { $lines += ('= the untouched original of {0}: no fix applied.' -f $b.Id); return $lines }
    $b = Find-BuildByContent $data
    if (-not $b) { $lines += 'Not a build this script knows (neither size nor code layout match).'; return $lines }
    $lines += ('build: {0}' -f $b.Title)
    if ($sha -eq $b.PatchedSha256) { $lines += '= the fully patched executable published in the repository.' }
    $lines += ''
    foreach ($p in $b.Patches) {
        $old = 0; $new = 0; $other = 0
        foreach ($e in $p.Edits) { switch (Get-EditState $data $e) { 'old' { $old++ } 'new' { $new++ } default { $other++ } } }
        $total = $old + $new + $other
        $verdict = if ($new -eq $total) { 'APPLIED' } elseif ($old -eq $total) { 'not applied' } else { "MIXED ($new applied, $old original, $other unknown)" }
        $lines += ('  {0,-12} {1,-12} {2}' -f $p.Id, $verdict, $p.Name)
    }
    return $lines
}

# Applies the chosen patches (canonical order) to the bytes of $OriginalPath and writes $OutputPath.
# Returns a small result object; throws on any check failure.
function Invoke-PatchRun([string] $OriginalPath, $Build, [object[]] $Chosen, [string] $OutputPath) {
    $data = [System.IO.File]::ReadAllBytes($OriginalPath)
    $ordered = @($Build.Patches | Where-Object { $p = $_; ($Chosen | Where-Object { $_.Id -eq $p.Id }) })
    $result = $data
    foreach ($p in $ordered) { $result = Invoke-Patch $result $p }
    [System.IO.File]::WriteAllBytes($OutputPath, $result)
    $outSha = Get-Sha256Hex $result
    $allCount = 0; foreach ($p in $Build.Patches) { $allCount++ }
    return @{
        Applied  = $ordered
        Sha256   = $outSha
        Size     = $result.Length
        Complete = ($ordered.Count -eq $allCount)
        Matches  = ($outSha -eq $Build.PatchedSha256)
    }
}

function Write-PatchList([switch] $WithEdits) {
    foreach ($b in $Builds) {
        Write-Host ''
        Write-Host ("=== {0}: {1}" -f $b.Id, $b.Title) -ForegroundColor Cyan
        Write-Host ("    original {0} ({1} bytes)  SHA-256 {2}" -f $b.OriginalName, $b.Size, $b.OriginalSha256)
        Write-Host ("    all patches -> {0}         SHA-256 {1}" -f $b.OutputName, $b.PatchedSha256)
        $n = 0
        foreach ($p in $b.Patches) {
            $n++
            Write-Host ''
            Write-Host ("  {0}. [{1}] {2}  ({3}, {4} edits)" -f $n, $p.Id, $p.Name, $p.Date, (Get-EditCount $p)) -ForegroundColor Yellow
            foreach ($line in ($p.Description -split "`r?`n")) { Write-Host ("       " + $line) }
            if ($WithEdits) { foreach ($line in (Get-EditLines $p)) { Write-Host ("       " + $line) -ForegroundColor DarkGray } }
        }
    }
    Write-Host ''
}

# =================================================================================================
#  WINDOW - the checkbox front end (Windows Forms, part of every Windows PowerShell)
# =================================================================================================
function Show-PatcherWindow([string] $PreloadPath) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()

    $script:gui = @{ Path = $null; Data = $null; Build = $null; IsOriginal = $false; Syncing = $false }
    $mono = New-Object System.Drawing.Font('Consolas', 9)

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Dark Colony patcher - rebuild the patched exe from the original, fix by fix'
    $form.Size = New-Object System.Drawing.Size(1000, 680)
    $form.MinimumSize = New-Object System.Drawing.Size(820, 560)
    $form.StartPosition = 'CenterScreen'
    $form.Font = New-Object System.Drawing.Font('Segoe UI', 9)

    # --- row 1: original exe
    $lblIn = New-Object System.Windows.Forms.Label
    $lblIn.Text = 'Original exe:'; $lblIn.Location = '12,15'; $lblIn.AutoSize = $true
    $txtIn = New-Object System.Windows.Forms.TextBox
    $txtIn.Location = '110,12'; $txtIn.Size = '760,23'; $txtIn.Anchor = 'Top,Left,Right'; $txtIn.ReadOnly = $true
    $btnBrowse = New-Object System.Windows.Forms.Button
    $btnBrowse.Text = 'Browse...'; $btnBrowse.Location = '880,10'; $btnBrowse.Size = '92,26'; $btnBrowse.Anchor = 'Top,Right'

    $lblStatus = New-Object System.Windows.Forms.Label
    $lblStatus.Location = '110,40'; $lblStatus.Size = '860,36'; $lblStatus.Anchor = 'Top,Left,Right'
    $lblStatus.Text = 'Pick dc16original1998.exe (Classic) or engexp16original.exe (Council Wars) - both are in the repository, untouched.'

    # --- left: the fixes
    $grpFix = New-Object System.Windows.Forms.GroupBox
    $grpFix.Text = 'Fixes to apply (always applied in this order)'; $grpFix.Location = '12,82'; $grpFix.Size = '450,470'
    $grpFix.Anchor = 'Top,Bottom,Left'
    $chkAll = New-Object System.Windows.Forms.CheckBox
    $chkAll.Text = 'Select all fixes  (result = the exe published in the repository)'
    $chkAll.Location = '12,24'; $chkAll.AutoSize = $true; $chkAll.Enabled = $false
    $chkAll.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
    $lst = New-Object System.Windows.Forms.CheckedListBox
    $lst.Location = '12,52'; $lst.Size = '426,406'; $lst.Anchor = 'Top,Bottom,Left,Right'
    $lst.CheckOnClick = $true; $lst.IntegralHeight = $false; $lst.Enabled = $false
    $lst.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $grpFix.Controls.AddRange(@($chkAll, $lst))

    # --- right: description of the highlighted fix
    $grpInfo = New-Object System.Windows.Forms.GroupBox
    $grpInfo.Text = 'What the highlighted fix changes'; $grpInfo.Location = '474,82'; $grpInfo.Size = '498,470'
    $grpInfo.Anchor = 'Top,Bottom,Left,Right'
    $txtInfo = New-Object System.Windows.Forms.TextBox
    $txtInfo.Location = '12,24'; $txtInfo.Size = '474,434'; $txtInfo.Anchor = 'Top,Bottom,Left,Right'
    $txtInfo.Multiline = $true; $txtInfo.ReadOnly = $true; $txtInfo.ScrollBars = 'Vertical'; $txtInfo.WordWrap = $true
    $txtInfo.Font = $mono; $txtInfo.BackColor = [System.Drawing.SystemColors]::Window
    $txtInfo.Text = @(
        'This window rebuilds the patched game executable from the untouched original,',
        'one fix at a time.  Every fix is a list of byte edits written out in this .ps1 file:',
        '',
        '    @{ Offset = 0x431F; Old = ''75''; New = ''EB'' }   # jne -> jmp after the CD check',
        '',
        'A byte is written only if the file still holds the documented old bytes, the input',
        'file is never modified, and the result is a new file whose SHA-256 is shown here.',
        'With every fix selected the result is byte-identical to the exe in the repository.',
        '',
        'Open this file in a text editor to read all of it - it uses nothing but the .NET',
        'classes that ship with Windows (File, SHA256, Windows Forms).',
        '',
        'Click a fix on the left to read what it does and see its byte edits.'
    ) -join "`r`n"
    $grpInfo.Controls.Add($txtInfo)

    # --- bottom: output + buttons + log
    $lblOut = New-Object System.Windows.Forms.Label
    $lblOut.Text = 'Write to:'; $lblOut.Location = '12,565'; $lblOut.AutoSize = $true; $lblOut.Anchor = 'Bottom,Left'
    $txtOut = New-Object System.Windows.Forms.TextBox
    $txtOut.Location = '110,562'; $txtOut.Size = '760,23'; $txtOut.Anchor = 'Bottom,Left,Right'
    $btnOut = New-Object System.Windows.Forms.Button
    $btnOut.Text = '...'; $btnOut.Location = '880,560'; $btnOut.Size = '92,26'; $btnOut.Anchor = 'Bottom,Right'
    $btnApply = New-Object System.Windows.Forms.Button
    $btnApply.Text = 'Apply selected fixes'; $btnApply.Location = '110,596'; $btnApply.Size = '170,30'; $btnApply.Anchor = 'Bottom,Left'
    $btnApply.Enabled = $false
    $btnVerify = New-Object System.Windows.Forms.Button
    $btnVerify.Text = 'Inspect an exe...'; $btnVerify.Location = '290,596'; $btnVerify.Size = '140,30'; $btnVerify.Anchor = 'Bottom,Left'
    $lblLog = New-Object System.Windows.Forms.Label
    $lblLog.Location = '440,596'; $lblLog.Size = '532,40'; $lblLog.Anchor = 'Bottom,Left,Right'; $lblLog.Font = $mono

    $form.Controls.AddRange(@($lblIn, $txtIn, $btnBrowse, $lblStatus, $grpFix, $grpInfo, $lblOut, $txtOut, $btnOut, $btnApply, $btnVerify, $lblLog))
    $script:gui.Controls = @{ Form = $form; In = $txtIn; Status = $lblStatus; All = $chkAll; List = $lst; Info = $txtInfo; Out = $txtOut; Apply = $btnApply; Log = $lblLog; Browse = $btnBrowse; OutBtn = $btnOut; Verify = $btnVerify }
    $c = $script:gui.Controls   # event handlers run outside this function's scope, so they reach the controls through this table

    # --- behaviour
    $loadOriginal = {
        param([string] $path)
        $c = $script:gui.Controls
        $g = $script:gui
        $g.Path = $null; $g.Data = $null; $g.Build = $null; $g.IsOriginal = $false
        $c.List.Items.Clear(); $c.All.Checked = $false
        try {
            $data = [System.IO.File]::ReadAllBytes($path)
        } catch {
            $c.Status.ForeColor = 'Firebrick'; $c.Status.Text = "cannot read: $($_.Exception.Message)"; return
        }
        $sha = Get-Sha256Hex $data
        $build = Find-BuildBySha $sha
        $isOriginal = ($null -ne $build)
        if (-not $build) { $build = Find-BuildByContent $data }
        $c.In.Text = $path
        if (-not $build) {
            $c.Status.ForeColor = 'Firebrick'
            $c.Status.Text = "Not a build this script knows ($($data.Length) bytes). Use dc16original1998.exe or engexp16original.exe from the repository."
            $c.List.Enabled = $false; $c.All.Enabled = $false; $c.Apply.Enabled = $false
            return
        }
        $g.Path = $path; $g.Data = $data; $g.Build = $build; $g.IsOriginal = $isOriginal
        if ($isOriginal) {
            $c.Status.ForeColor = 'DarkGreen'
            $c.Status.Text = "$($build.Title)`r`nSHA-256 $sha = the untouched original."
        } else {
            $c.Status.ForeColor = 'DarkOrange'
            $c.Status.Text = "$($build.Title)`r`nSHA-256 does not match the untouched original (already patched, or another copy). Every byte is still checked before it is written."
        }
        foreach ($p in $build.Patches) { [void] $c.List.Items.Add(('{0}   ({1})' -f $p.Name, $p.Date), $false) }
        $c.List.Enabled = $true; $c.All.Enabled = $true; $c.Apply.Enabled = $true
        $c.Out.Text = Join-Path (Split-Path $path) $build.OutputName
        if ($c.List.Items.Count -gt 0) { $c.List.SelectedIndex = 0 }
        $c.Log.Text = ''
    }
    $script:gui.Load = $loadOriginal

    $c.Browse.Add_Click({
        $c = $script:gui.Controls
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Title = 'Pick the untouched original executable'
        $dlg.Filter = 'Dark Colony executables (*.exe)|*.exe|All files (*.*)|*.*'
        if ($script:gui.Path) { $dlg.InitialDirectory = Split-Path $script:gui.Path }
        if ($dlg.ShowDialog($c.Form) -eq 'OK') { & $script:gui.Load $dlg.FileName }
    })

    $c.OutBtn.Add_Click({
        $c = $script:gui.Controls
        $dlg = New-Object System.Windows.Forms.SaveFileDialog
        $dlg.Title = 'Where to write the patched exe'; $dlg.Filter = 'Executable (*.exe)|*.exe'
        $dlg.OverwritePrompt = $false
        if ($c.Out.Text) { $dlg.FileName = Split-Path -Leaf $c.Out.Text; try { $dlg.InitialDirectory = Split-Path $c.Out.Text } catch {} }
        if ($dlg.ShowDialog($c.Form) -eq 'OK') { $c.Out.Text = $dlg.FileName }
    })

    # "Select all" <-> individual boxes, without the two events feeding each other
    $c.All.Add_CheckedChanged({
        $c = $script:gui.Controls
        if ($script:gui.Syncing) { return }
        $script:gui.Syncing = $true
        for ($i = 0; $i -lt $c.List.Items.Count; $i++) { $c.List.SetItemChecked($i, $c.All.Checked) }
        $script:gui.Syncing = $false
    })
    $c.List.Add_ItemCheck({
        param($sender, $e)
        $c = $script:gui.Controls
        if ($script:gui.Syncing) { return }
        $all = $true
        for ($i = 0; $i -lt $c.List.Items.Count; $i++) {
            $checked = if ($i -eq $e.Index) { $e.NewValue -eq 'Checked' } else { $c.List.GetItemChecked($i) }
            if (-not $checked) { $all = $false }
        }
        $script:gui.Syncing = $true; $c.All.Checked = $all; $script:gui.Syncing = $false
    })

    $c.List.Add_SelectedIndexChanged({
        $c = $script:gui.Controls
        $g = $script:gui
        if (-not $g.Build -or $c.List.SelectedIndex -lt 0) { return }
        $p = $g.Build.Patches[$c.List.SelectedIndex]
        $lines = @(
            $p.Name, ('=' * $p.Name.Length),
            ('id {0}   added {1}   {2} byte edits' -f $p.Id, $p.Date, (Get-EditCount $p)),
            ('made with {0}' -f $p.Tool), ('documented in {0}' -f $p.Doc), ''
        )
        # the descriptions are pre-wrapped for the source file; join each paragraph so the box wraps it itself
        $para = ''
        foreach ($l in ($p.Description -split "`r?`n")) {
            if ($l -eq '' -or $l -match '^\s') { if ($para) { $lines += $para; $para = '' }; $lines += $l }
            else { $para = if ($para) { "$para $l" } else { $l } }
        }
        if ($para) { $lines += $para }
        $lines += @('', 'Byte edits (file offset: old bytes -> new bytes):', '') + (Get-EditLines $p)
        $c.Info.Text = $lines -join "`r`n"
        $c.Info.SelectionStart = 0; $c.Info.SelectionLength = 0; $c.Info.ScrollToCaret()
    })

    $script:gui.Apply = {
        param([bool] $confirmOverwrite)
        $c = $script:gui.Controls
        $g = $script:gui
        if (-not $g.Build) { return $null }
        $chosen = @()
        for ($i = 0; $i -lt $c.List.Items.Count; $i++) { if ($c.List.GetItemChecked($i)) { $chosen += $g.Build.Patches[$i] } }
        if ($chosen.Count -eq 0) { $c.Log.ForeColor = 'Firebrick'; $c.Log.Text = 'No fix selected.'; return $null }
        $outPath = $c.Out.Text.Trim()
        if (-not $outPath) { $c.Log.ForeColor = 'Firebrick'; $c.Log.Text = 'Choose where to write the result.'; return $null }
        if ([System.IO.Path]::GetFullPath($outPath) -eq [System.IO.Path]::GetFullPath($g.Path)) {
            $c.Log.ForeColor = 'Firebrick'; $c.Log.Text = 'The output must not be the original file.'; return $null
        }
        if ((Test-Path $outPath) -and $confirmOverwrite) {
            $answer = [System.Windows.Forms.MessageBox]::Show($c.Form, "$outPath exists.`r`nReplace it?", 'Replace file?', 'YesNo', 'Question')
            if ($answer -ne 'Yes') { return $null }
        }
        try {
            $r = Invoke-PatchRun $g.Path $g.Build $chosen $outPath
        } catch {
            $c.Log.ForeColor = 'Firebrick'; $c.Log.Text = 'Nothing written.'
            [System.Windows.Forms.MessageBox]::Show($c.Form, $_.Exception.Message, 'Check failed - nothing written', 'OK', 'Error') | Out-Null
            return $null
        }
        $ids = ($r.Applied | ForEach-Object { $_.Id }) -join ', '
        if ($r.Complete -and $r.Matches) {
            $c.Log.ForeColor = 'DarkGreen'
            $c.Log.Text = "Written: $($r.Size) bytes, all $($r.Applied.Count) fixes.`r`nSHA-256 $($r.Sha256) = byte-identical to the exe published in the repository."
        } elseif ($r.Complete) {
            $c.Log.ForeColor = 'Firebrick'
            $c.Log.Text = "Written, but the SHA-256 differs from the published exe - please report this.`r`n$($r.Sha256)"
        } else {
            $c.Log.ForeColor = 'Black'
            $c.Log.Text = "Written: $($r.Size) bytes with $($r.Applied.Count) of $(Get-EditCount @{Edits=$g.Build.Patches}) fixes ($ids).`r`nSHA-256 $($r.Sha256)"
        }
        return $r
    }
    $c.Apply.Add_Click({ & $script:gui.Apply $true | Out-Null })

    $c.Verify.Add_Click({
        $c = $script:gui.Controls
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Title = 'Inspect an executable: which fixes does it carry?'
        $dlg.Filter = 'Dark Colony executables (*.exe)|*.exe|All files (*.*)|*.*'
        if ($dlg.ShowDialog($c.Form) -eq 'OK') {
            $c.Info.Text = (Get-VerifyReport $dlg.FileName) -join "`r`n"
            $c.List.ClearSelected()
        }
    })

    if ($PreloadPath) { & $loadOriginal ((Resolve-Path $PreloadPath).Path) }
    return $form
}

# =================================================================================================
#  ENTRY POINT
# =================================================================================================
if ($PSCmdlet.ParameterSetName -eq 'List') { Write-PatchList -WithEdits:$Detail; return }

if ($PSCmdlet.ParameterSetName -eq 'Verify') { Get-VerifyReport $Verify | ForEach-Object { Write-Host $_ }; return }

# No -All / -Patches: open the window (double-click, "Run with PowerShell", or just `.\Apply-DarkColonyPatches.ps1`)
if (-not $All -and -not $Patches) {
    $form = Show-PatcherWindow $Original
    [void] $form.ShowDialog()
    return
}

# --- command-line apply
if (-not $Original) { throw 'give -Original <exe> together with -All or -Patches' }
$origPath = (Resolve-Path $Original).Path
$data = [System.IO.File]::ReadAllBytes($origPath)
$sha = Get-Sha256Hex $data
Write-Host ("input : {0}" -f $origPath)
Write-Host ("        {0} bytes, SHA-256 {1}" -f $data.Length, $sha)

$build = Find-BuildBySha $sha
if (-not $build) {
    $build = Find-BuildByContent $data
    if (-not $build) { throw "This is not one of the two known original executables (size / layout mismatch)." }
    if (-not $Force) {
        throw ("The SHA-256 is not that of the untouched {0} original. Start from {1} (in the repository), " +
               "or pass -Force to rely on the per-byte checks alone.") -f $build.Id, $build.OriginalName
    }
    Write-Warning "SHA-256 does not match the untouched original; continuing because -Force was given (every edit is still byte-checked)."
}
Write-Host ("build : {0}" -f $build.Title)

$available = @($build.Patches)
if ($All) {
    $chosen = $available
} else {
    # accept -Patches a,b,c from a PowerShell prompt (array) as well as "a,b,c" / "a b c" from cmd / -File (one string)
    $chosen = @()
    foreach ($id in @($Patches | ForEach-Object { $_ -split '[\s,]+' } | Where-Object { $_ })) {
        $p = $available | Where-Object { $_.Id -eq $id }
        if (-not $p) { throw "unknown patch id '$id' for $($build.Id); valid: $(($available | ForEach-Object { $_.Id }) -join ', ')" }
        $chosen += $p
    }
}
if (-not $Output) { $Output = Join-Path (Split-Path $origPath) $build.OutputName }
if ((Test-Path $Output) -and -not $Overwrite) { throw "output '$Output' exists; pass -Overwrite to replace it" }
if ((Test-Path $Output) -and ((Resolve-Path $Output).Path -eq $origPath)) { throw 'refusing to overwrite the original' }

Write-Host ''
foreach ($p in @($available | Where-Object { $p = $_; ($chosen | Where-Object { $_.Id -eq $p.Id }) })) {
    Write-Host ("applying [{0,-10}] {1,-45} {2,3} edits" -f $p.Id, $p.Name, (Get-EditCount $p))
}
$r = Invoke-PatchRun $origPath $build $chosen $Output
Write-Host ''
Write-Host ("output: {0}" -f $Output)
Write-Host ("        {0} bytes, SHA-256 {1}" -f $r.Size, $r.Sha256)
if ($r.Complete) {
    if ($r.Matches) { Write-Host '        byte-identical to the executable published in the repository.' -ForegroundColor Green }
    else { Write-Warning 'all patches applied but the SHA-256 differs from the published executable - report this.' }
} else {
    Write-Host ("        {0} of {1} patches applied ({2}); a partial build has no published reference hash." -f $r.Applied.Count, $available.Count, (($r.Applied | ForEach-Object { $_.Id }) -join ', '))
}
''')

open(OUT, 'w', encoding='utf-8', newline='\r\n').write('\n'.join(out))
shutil.rmtree(WORK, ignore_errors=True)
print('wrote', OUT, os.path.getsize(OUT), 'bytes')

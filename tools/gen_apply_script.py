"""Generate Apply-DarkColonyPatches.ps1 - the self-documenting PowerShell patcher that lives in the ROOT of
the Dark-Colony game repository (next to "DC - Classic" and "DC - Council wars", maintainer decision 14 Sep 2026).

The PowerShell script lets a player rebuild the patched dc16new.exe / engexp16new.exe from the
untouched originals committed in the Dark-Colony repository, one patch at a time, with every changed
byte listed and explained.  This generator produces it by *replaying* the Python patch tools of this
folder on copies of the originals, diffing after each step (so every byte is attributed to
exactly one patch) and taking the per-edit descriptions from the tools' `plan` output.

    python tools/gen_apply_script.py "<path to Dark-Colony>" "<path to Dark-Colony>/Apply-DarkColonyPatches.ps1"

Needs: DC - Council wars/dc16.exe (the untouched Classic build of 7 Jan 1998; since 15 Sep 2026 both
games live in that one folder, the Classic original keeps its stock name and the patched result is
dc16new.exe) and DC - Council wars/ENGEXP16.EXE in the game repository, and the
patch_*.py tools beside this file.  The generated script is validated here:
the sum of the per-patch edits must reproduce every intermediate exe, and the end result is
hashed into the script as the reference for "all patches applied".  Re-run after adding a patch
(add it to PATCHES/BUILDS below, with a block parser for its plan output).  The OZI patch is
kept last because its 16-byte .reloc insert shifts every later relocation entry.  The CD fix is
ONE patch, `nocd` (patch_nocd.py; maintainer requirement 18 Sep 2026): it carries the three
hand-patched 2025 bytes (formerly `cdcheck`) and the removal of the whole CD path; it goes first,
and patch_resolution.py accepts the resulting exe by size (its MD5 table only knows the 2025 state).
"""
import re, struct, hashlib, sys, os, shutil, subprocess, tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
GAME = sys.argv[1]
OUT = sys.argv[2]
WORK = tempfile.mkdtemp(prefix='dcpatch_')

# Since 15 Sep 2026 both games run from the Council Wars folder (maintainer decision): the originals keep
# their stock names, "DC - Council wars/dc16.exe" and "ENGEXP16.EXE", and the patched builds are
# "dc16new.exe" and "engexp16new.exe" beside them (the latter was DCEXP16.EXE from 10 to 15 Sep 2026).
ORIGINALS = {'classic': os.path.join(GAME, 'DC - Council wars', 'dc16.exe'),
             'cw': os.path.join(GAME, 'DC - Council wars', 'ENGEXP16.EXE'),
             'maped': os.path.join(GAME, 'Dark Colony - Map editor', 'maped.exe')}
GAME_DIR = {'classic': os.path.join(GAME, 'DC - Council wars'), 'cw': os.path.join(GAME, 'DC - Council wars'),
            'maped': os.path.join(GAME, 'Dark Colony - Map editor')}

# Screen resolutions (21 Sep 2026, maintainer request: a drop-down in the patcher).  '640x480' is the
# stock mode = the build without its HD fixes; every HD mode is a separate replay of the two tools
# that take --width/--height (`resolution`, `clock`), emitted as per-mode variants of those fixes
# (same Id, `Mode` field).  All HD modes share the one INTRF_HD folder (maintainer decision), so
# `hdpaths` is the same in every HD mode; the patcher checks that the folder's set is the chosen size
# by reading the GIF header of INTRF_HD\INTRFACE.GIF (`DataSize`).  Doc 10.25.
STOCK_MODE = '640x480'
HD_MODES = ['1024x768', '1280x1024', '1280x720', '1280x800']
DEFAULT_MODE = '1024x768'           # the mode of the exes published in the repository
# tools replayed per mode (--width/--height): the display fixes differ per size; `movies` and `ozi` have
# a 640x480 variant (the exe is pointed at copies of the lists / the menu script that the original exe
# never reads, since the stock files must stay untouched) and one shared HD variant
MODE_STEPS = {'resolution', 'clock', 'movies', 'ozi'}
HD_STEPS = {'resolution', 'hdpaths', 'clock'}   # fixes that do not exist in the stock mode
CUR_MODE = None


def mode_wh(mode):
    w, h = mode.split('x')
    return int(w), int(h)


def mode_args(mode):
    w, h = mode_wh(mode)
    return ['--width', str(w), '--height', str(h)]


def geometry(mode):
    """patch_resolution.Geometry for the mode (the numbers the descriptions quote)."""
    sys.path.insert(0, TOOLS)
    import patch_resolution
    return patch_resolution.Geometry(*mode_wh(mode))


_LS_FILES = {}


def _tree(g, *parts, pattern=None):
    """Relative paths (backslashes, repository case) of the files the REPOSITORY holds under
    <game>/<parts>, sorted - `git ls-files`, not the disk, so save games, minimap caches and other
    files the game writes into those folders never end up in the list."""
    if g not in _LS_FILES:
        sub = os.path.relpath(GAME_DIR[g], GAME).replace('\\', '/')
        r = subprocess.run(['git', '-C', GAME, 'ls-files', '--', sub], capture_output=True, text=True, check=True)
        _LS_FILES[g] = [l[len(sub) + 1:] for l in r.stdout.splitlines() if l.startswith(sub + '/')]
    prefix = '/'.join(parts) + '/' if parts else ''
    out = []
    for rel in _LS_FILES[g]:
        if not rel.lower().startswith(prefix.lower()):
            continue
        name = rel.rsplit('/', 1)[-1]
        if name.lower().endswith('.bak') or (pattern and not re.search(pattern, name, re.I)):
            continue
        out.append(rel.replace('/', '\\'))
    return sorted(out, key=str.lower)


def hd_data(g, mode=None):
    """Data files the 1024x768 exe needs (patches `resolution` + `hdpaths`): the INTRF_HD tree, the
    re-baked logo banks and their FINs, and Council Wars' exp/intrf_hd overrides.  Enumerated from
    the game repository at generation time so the list is exact."""
    # Since 21 Sep 2026 the patcher GENERATES the INTRF_HD set (Write-InterfaceSet), so what it needs
    # are the stock inputs: the INTRFACE scripts, pictures, FIN lists and loading screens the set is
    # derived from (named after the repository's INTRF_HD: every output has a same-named input,
    # except the three shipped pictures, see set_sources), the four GAMESTAT briefing lists, the
    # shared re-baked logo banks, and for Council Wars the exp\ overrides and the OZI lists.
    hd = [f.rsplit('\\', 1)[-1] for f in _tree(g, 'INTRF_HD') if '\\' not in f[len('INTRF_HD\\'):]]
    files = []
    for name in hd:
        if name.upper() in ('INTRG.GIF', 'INTRO.GIF', 'INTRFACE.GIF'):
            continue                                    # shipped per size (set_sources)
        if name.upper().endswith('SCENE.TXT'):
            continue                                    # the briefing lists come from GAMESTAT (below)
        src = os.path.join(GAME_DIR[g], 'INTRFACE', name)
        assert os.path.exists(src), src
        files.append('INTRFACE\\' + name)
    files += ['GAMESTAT\\' + x for x in ('HSCENE.TXT', 'GSCENE.TXT', 'HTSCENE.TXT', 'GTSCENE.TXT')]
    files += _tree(g, 'SPRITES', pattern=r'_HD\.SPR$') + _tree(g, 'ANIMATE', pattern=r'_HD\.FIN$')
    if g == 'cw':
        files += ['exp\\intrface\\' + x for x in ('bintroe', 'introe', 'shumane')]
        files += ['exp\\gamestat\\' + x for x in ('hxscene.txt', 'gxscene.txt')]
        files += ['ozi_ns\\gamestat\\' + x for x in ('hxscene.txt', 'gxscene.txt')]
    for f in files:
        assert os.path.exists(os.path.join(GAME_DIR[g], f.replace('\\', os.sep))), (g, f)
    assert 55 <= len(files) <= 75, (g, len(files))
    return files


def set_sources(g, mode):
    """The three pictures of a resolution that cannot be derived: the painted main-menu backdrops and
    the spliced HUD frame, shipped as INTRF_HD\<WxH>\*.GIF."""
    out = ['INTRF_HD\\%s\\%s' % (mode, x) for x in ('INTRG.GIF', 'INTRO.GIF', 'INTRFACE.GIF')]
    for f in out:
        assert os.path.exists(os.path.join(GAME_DIR[g], f.replace('\\', os.sep))), (g, f)
    return out


def ozi_data(g, mode=None):
    """Data files the OZI MISSIONS mode needs: the whole ozi_ns/ overlay, the pack's base-set
    additions in exp/ (animozi.dat, the new units, the tranozi transport) and the ozisave marker."""
    files = _tree(g, 'ozi_ns') + _tree(g, 'ozisave')
    files += _tree(g, 'exp', pattern=r'^animozi\.dat$')
    files += _tree(g, 'exp', 'animate', pattern=r'^(dalg|spyo|reae|tranozi)\.fin$')
    files += _tree(g, 'exp', 'sprites', pattern=r'^(dalg|spyo|reae|tranozi)\.spr$')
    # 375 since 21 Sep 2026: the 19 `.o16` minimap caches of the pack maps were untracked (game-written,
    # `*.o16` is gitignored in Dark-Colony; the game recreates them on first load).
    assert len(files) >= 370, (g, len(files))
    files += ['ozi_ns\\gamestat\\hxscene.txt', 'ozi_ns\\gamestat\\gxscene.txt']   # unshifted lists, untracked at generation time
    if mode == STOCK_MODE:
        files += ['exp\\intrface\\bintroe']                              # source of the bintoze copies
    return files


TOOL_OF = {'nocd': 'patch_nocd.py',
           'resolution': 'patch_resolution.py', 'hdpaths': 'patch_hd_paths.py', 'cursor': 'patch_cursor.py',
           'pool': 'patch_pool.py', 'clock': 'patch_clock.py',
           'ddraw': 'patch_ddraw_lost.py', 'camera': 'patch_camera.py', 'restore': 'patch_restore.py',
           'movies': 'patch_movies.py', 'sounds': 'patch_wavprefix.py', 'ozi': 'patch_ozi_menu.py',
           # map editor: one tool, one fix id per step (the plan is taken once with --fix all)
           'blocksets': ('patch_maped.py', ['--fix', 'blocksets']), 'teams': ('patch_maped.py', ['--fix', 'teams']),
           'healer': ('patch_maped.py', ['--fix', 'healer']), 'troopsframe': ('patch_maped.py', ['--fix', 'troopsframe'])}
PLAN_OF = {'nocd': 'nocd',
           'resolution': 'resolution', 'hdpaths': 'hd_paths', 'cursor': 'cursor', 'pool': 'pool',
           'clock': 'clock', 'ddraw': 'ddraw_lost', 'camera': 'camera', 'restore': 'restore', 'movies': 'movies', 'sounds': 'wavprefix', 'ozi': 'ozi_menu',
           'blocksets': 'maped', 'teams': 'maped', 'healer': 'maped', 'troopsframe': 'maped'}
PLAN_ARGS = {'maped': ['--fix', 'all']}      # plan-time arguments per plan name (default: none)
_plans = {}

def tool_of(step):
    t = TOOL_OF[step]
    return (t, []) if isinstance(t, str) else t

def run_tool(tool, cmd, exe, args=()):
    r = subprocess.run([sys.executable, os.path.join(TOOLS, tool), cmd, exe, *args], capture_output=True, text=True)
    if cmd == 'apply' and r.returncode != 0:
        raise SystemExit(f'{tool} apply failed on {exe}:\n{r.stdout}\n{r.stderr}')
    return r.stdout + r.stderr

def replay(g, steps, mode=None):
    """Return (original bytes, [(step, bytes after that step)]) and fill _plans[(g, plan name, mode)].
    `mode` ('WxH') is passed to the MODE_STEPS tools as --width/--height."""
    global CUR_MODE
    CUR_MODE = mode
    orig = open(ORIGINALS[g], 'rb').read()
    work = os.path.join(WORK, f'{g}.exe')
    open(work, 'wb').write(orig)
    # every plan is taken on an untouched copy of the original (the plans describe stock -> patched bytes)
    orig_copy = os.path.join(WORK, f'{g}_orig.exe')
    open(orig_copy, 'wb').write(orig)
    for step in steps:
        key = (g, PLAN_OF[step], mode if step in MODE_STEPS else None)
        if key in _plans:
            continue
        tool, _args = tool_of(step)
        extra = mode_args(mode) if step in MODE_STEPS else []
        _plans[key] = run_tool(tool, 'plan', orig_copy, PLAN_ARGS.get(PLAN_OF[step], []) + extra)
    states = []
    for step in steps:
        tool, args = tool_of(step)
        extra = mode_args(mode) if step in MODE_STEPS else []
        run_tool(tool, 'apply', work, list(args) + extra)
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
    return _plans[(g, tool, CUR_MODE if tool in {PLAN_OF[s] for s in MODE_STEPS} else None)]

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

def blocks_camera(g):
    t = plan(g, 'camera'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2});(.*)$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ':' + m.group(6).rstrip(), old, new))
    assert len(out) == 2, (g, len(out))                                   # the call operand + the 33-byte stub
    return out

def blocks_restore(g):
    t = plan(g, 'restore'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2});(.*)$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3)) and len(old) in (107, 6)
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ':' + m.group(6).rstrip(), old, new))
    assert len(out) == 2, (g, len(out))                                   # the rewritten pump of frame_end + present()'s jne to the idle stub
    return out

def blocks_pool(g):
    m = re.search(r'site file 0x([0-9a-f]+)', plan(g, 'pool'))
    return [(int(m.group(1), 16), 5, 'mov eax,imm32 before call SMalloc_Pool: pool size 11 500 000 (0x00AF79E0) -> 33 554 432 bytes (0x02000000, 32 MiB)')]

def blocks_clock(g):
    t = plan(g, 'clock')
    m = re.search(r'y dword at file 0x([0-9a-f]+), x dword at file 0x([0-9a-f]+)', t)
    w, h = mode_wh(CUR_MODE)
    return [(int(m.group(1), 16), 4, 'clock_draw: imm32 of mov edx,ANCHOR_Y - bottom-right anchor y 450 (0x1C2) -> %d (0x%X)' % (h - 30, h - 30)),
            (int(m.group(2), 16), 4, 'clock_draw: imm32 of mov eax,ANCHOR_X - bottom-right anchor x 608 (0x260) -> %d (0x%X)' % (w - 32, w - 32))]

def blocks_movies(g):
    out = []
    for m in re.finditer(r'^\s+DGROUP string "([^"]+)" -> "([^"]+)"\s+file 0x([0-9a-f]+) VA 0x[0-9a-f]+ (\d+) bytes: (.+)$', plan(g, 'movies'), re.M):
        out.append((int(m.group(3), 16), int(m.group(4)), 'DGROUP string "%s" -> "%s": %s' % (m.group(1), m.group(2), m.group(5).strip())))
    assert out
    return out

def blocks_sounds(g):
    m = re.search(r'^\s+DGROUP string "exp/" -> "" \(wave-loader prefix\)\s+file 0x([0-9a-f]+) VA 0x[0-9a-f]+ 4 bytes: (.+)$', plan(g, 'wavprefix'), re.M)
    return [(int(m.group(1), 16), 4, 'DGROUP string "exp/" -> "" (wave-loader prefix): ' + m.group(2).strip())]

def movie_data(g, mode=None):
    """The AVI resources of the `movies` fix: the Classic movies under their own names.  The two
    INTRF_HD campaign lists that name the endings are interface resources and belong to `hd_data`
    (fix `hdpaths`, which `movies` requires) - maintainer request 15 Sep 2026: interface resources and
    AVI resources are checked as separate groups.  Fixed list (large binaries, not in `git ls-files`
    at generation time)."""
    files = ['AVI\\DCINTRO.AVI', 'AVI\\DCAENDING.AVI', 'AVI\\DCHENDING.AVI']
    if mode == STOCK_MODE:
        files += ['GAMESTAT\\HSCENE.TXT', 'GAMESTAT\\GSCENE.TXT']     # sources of the HSCNDC/GSCNDC copies
    for f in files:
        assert os.path.exists(os.path.join(GAME_DIR[g], f.replace('\\', os.sep))), f
    return files

def blocks_maped(fix):
    """Block parser factory for the map-editor fixes: lines `  [<fix>] <note>  file 0x.. 1 byte: 58 -> 50`."""
    def blocks(g):
        out = []
        for m in re.finditer(r'^\s+\[' + re.escape(fix) + r'\] (.+?)\s+file 0x([0-9a-f]+) 1 byte: ([0-9a-f]{2}) -> ([0-9a-f]{2})\s*$', plan(g, 'maped'), re.M):
            out.append((int(m.group(2), 16), 1, m.group(1).strip(), bytes.fromhex(m.group(3)), bytes.fromhex(m.group(4))))
        assert out, fix
        return out
    return blocks

def blocks_hdpaths(g):
    t = plan(g, 'hd_paths'); out = []
    for m in re.finditer(r'^\s+"([^"]+)" -> "([^"]+)"\s+file 0x([0-9a-f]+) VA 0x[0-9a-f]+ 8 bytes: (.+)$', t, re.M):
        out.append((int(m.group(3), 16), 8, 'DGROUP string "%s" -> "%s": %s' % (m.group(1), m.group(2), m.group(4).strip())))
    assert len(out) == 30, len(out)
    return out

def blocks_nocd(g):
    t = plan(g, 'nocd'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+file 0x([0-9a-f]+) VA 0x[0-9a-f]+ (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2})\s*$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip(), old, new))
    assert len(out) == (12 if g == 'classic' else 13), (g, len(out))      # 2/3 historical cdcheck bytes + 10 CD-path sites
    out += reloc_lines(t, '.reloc table: ')
    assert len(out) == (18 if g == 'classic' else 19), (g, len(out))      # + 2 start-up operands, 4 CD-prompt operands
    return out

# ----------------------------------------------------------------------------------------------
# patch catalogue (canonical application order)
# ----------------------------------------------------------------------------------------------
PATCHES = [
 dict(id='nocd', name='No CD: the game neither needs the disc nor touches the CD path', date='28-30 Sep 2025 / 18 Sep 2026 / 21 Sep 2026', tool='tools/patch_nocd.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.19; CLAUDE.md "Patches applied so far" (the 2025 bytes)', blocks=blocks_nocd,
      desc='''The game refuses to start, and greys out most main-menu buttons, when it cannot find its
CD in a drive.  This one fix removes the whole CD business from the exe:

  1. The two menu tests of the "CD present" flag ("call cd_flag ; test al,al ; jne ok") become
     unconditional jumps (opcode 75 -> EB): the game starts and keeps every menu button without
     the disc.  Council Wars has a third test that threw the player out of a running game; that
     one is inverted (75 -> 74).  These are the three bytes hand-patched in 2025.
  2. Those bypasses alone only ignore the ANSWER of the test.  Until 18 Sep 2026 the machinery
     itself still ran: at start-up the game opened HBNFUFL.A01 / HBNFUFL.A02 (the drive letter
     its installer recorded, "D:" in the repository; a missing file was a silent exit), built the
     path "D:\\dc\\" and probed it - it opened D:\\dc\\anim.dat and, if that existed, tried to
     create a file there to see whether the medium refuses writes.  The same probe ran again at
     every menu screen and periodically during a battle, two loaders fell back to "D:\\dc\\<name>"
     when a file was missing locally, the sound loader then asked to "insert The Dark Colony CD
     and Restart", and the movie opener fell back to the CD when the flag said the disc was in.
     The game never tells Windows to fail such accesses quietly (no SetErrorMode call), so when
     the letter D: belonged to a drive that was not ready - a card reader or a USB/optical drive
     without a medium, an unplugged removable disk, a second hard disk that had spun down -
     Windows showed its "No Disk / Please insert a disk into drive ..." box behind the full-screen
     game (a black screen that looks like a hang and reads like a CD request) or the game stalled
     for the seconds the disk needed to wake up.  Reported by players with more than one drive.
     Now: the start-up instructions that load the HBNFUFL name become a jump over the whole block
     (HBNFUFL is never opened, no letter, no path, no probe; the two absolute operands that vanish
     had .reloc entries, which become type-0 padding), the "call cd_probe" becomes five NOPs and
     cd_probe itself starts with "ret" for its two remaining callers, the file-open helper and the
     sound loader jump to their ordinary "file missing" exits instead of trying "<CD path><name>",
     the movie opener never takes its CD branch, the dead "%c:\\dc\\" string is zeroed, and the
     sound loader's box says "FILE NOT FOUND / A sound file is missing - see error.log".
  3. The "Please insert Dark Colony CD" box (21 Sep 2026, player report).  That text is a picture,
     not a string: when a file the game insists on is missing, the file-open helper draws the
     sprite intrface/insee over the screen and waits for the file to appear - once for the disc to
     be inserted, now forever.  Those 68 bytes of the display object's CD-prompt method become the
     sound loader's error exit with the file name as the message: a line "unable to open file
     <name>" in error.log, the desktop mode restored, a box "FILE NOT FOUND / <name>", exit.  The
     four absolute operands of the new code take over the relocation entries of the old ones.
     (Seen with a copy of the game that lacked ozi_ns\\intrf_hd\\: OZI MISSIONS -> NEXT showed the
     prompt for intrf_hd/hxscene.txt.)

Every edit sits inside an existing instruction or string; nothing moves.  The patched exe no
longer needs HBNFUFL.A01 / .A02 (the untouched originals still read the drive letter from them).'''),
 dict(id='resolution', name=lambda mode: '%s display' % mode, date='9 Sep 2026 (any size since 21 Sep 2026)', tool='tools/patch_resolution.py (Dark-Colony-Server)',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md sections 8-10, 10.24, 10.25', blocks=blocks_resolution,
      requires=['hdpaths'], data=hd_data, datasize=True,
      desc=lambda mode: (lambda g: '''The engine is hard-wired for 640x480: the DirectDraw display mode, the framebuffer stride
(y*640 done as shl 7 + add), clip rectangles, the map viewport (16x14 tiles), the minimap
position, the movie blit, the 44 code-positioned main-menu elements, the terrain light plane's
512-byte row advances and the size of draw_terrain's stack lightmap.  Every one of those
constants was read out of the disassembly and is replaced by the %(mode)s equivalent here:
  stage 1  display mode, framebuffer stride (%(stride)s), clip rect
  stage 2  full-screen chrome, mouse, cursor clip, loading screens, and the 44 menu elements
           moved by (+%(dx)d,+%(dy)d) - the same offset the letterboxed 640x480 menu screens use
  stage 3  map viewport %(vw)dx%(vh)d (%(tx)dx%(ty)d tiles) at (4,6)%(slack)s, minimap 96x84 at
           (%(mx)d,6), the 31 lightplane row advances, %(lm)s and a bigger stack frame for
           draw_terrain (so the PE header's SizeOfStackReserve / SizeOfStackCommit go up as
           well - the two edits at file offsets 0xE0 / 0xE4)
  stage 4  movies: pitch-aware back-buffer clear and the 320x180 movie frames stretched to
           (%(m0)d,%(m1)d)-(%(m2)d,%(m3)d) through IDirectDrawSurface::Blt
Every edit swaps one immediate constant or one arithmetic opcode inside an existing
instruction; no code is added and no instruction moves.  Council Wars is the same code at
+0x60 (AUTO) / +0x28 (DGROUP) with three site fixups, hence the slightly different offsets.

REQUIRES the interface data rebuilt for %(mode)s next to the exe - in the INTRF_HD/ folder,
read through the "Interface data from INTRF_HD" patch below (select both).  One INTRF_HD folder
serves every resolution, so it must hold the set built for THIS size: the patcher reads the
size of INTRF_HD\\INTRFACE.GIF and refuses a mismatch (with a 1024x768 set the game would draw
the menus and the HUD frame at the wrong size).  The two loading screens INTRF_HD\\LOAD.BMP /
LOAD2.BMP are not part of a set: this patcher writes them for the chosen size from the stock
INTRFACE\\LOAD.BMP / LOAD2.BMP (the 640x480 picture centred on a black %(mode)s canvas) whenever
the ones in place have another size.''' % dict(
          mode=mode, dx=g.menu_dx, dy=g.menu_dy, vw=g.view_w, vh=g.view_h, tx=g.tiles_x, ty=g.tiles_y,
          mx=g.minimap_x, m0=g.movie_rect[0], m1=g.movie_rect[1], m2=g.movie_rect[2], m3=g.movie_rect[3],
          stride='a shift, %d is a power of two' % g.w if g.pow2 else 'imul: %d is not a power of two' % g.w,
          slack=' plus %d spare rows given to the taller HUD bottom bar' % g.slack_y if g.slack_y else '',
          lm='the six lightmap row idioms x144 -> x%d (more than 34 tiles across),' % g.lm_stride if g.lm_stride_patch else ''))(geometry(mode))),
 dict(id='hdpaths', name='Interface data from INTRF_HD (rebuilt files renamed)', date='14 Sep 2026', tool='tools/patch_hd_paths.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.17', blocks=blocks_hdpaths,
      requires=['resolution'], data=hd_data,
      desc='''The rebuilt (1024x768 or another HD size) menus, HUD frame, loading screens, briefing-marker lists and re-baked logo sprites
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
dcuk_hd.fin etc.  No code changes.  With this patch the untouched dc16.exe / ENGEXP16.EXE
(stock data) and the patched exe (INTRF_HD data) run side by side from one folder.  Only
meaningful together with the display patch, and REQUIRES the INTRF_HD/ folder holding the set built for the chosen
resolution (one folder for every size, maintainer decision 21 Sep 2026).'''),
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
 dict(id='clock', name=lambda mode: 'Day/night clock hand re-anchored (%s)' % mode, date='13 Sep 2026', tool='tools/patch_clock.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.15', blocks=blocks_clock,
      requires=['resolution'],
      desc=lambda mode: '''The HUD's day/night hand is a sprite cell that clock.c blits by code with its bottom-right
corner at (608,450) - two plain immediates that are neither 640 nor 480, so the resolution
sweep did not touch them.  At %s that point lies inside the enlarged map view and the
terrain paints over the hand every frame.  The anchor moves to (%d,%d), where the rebuilt
HUD frame has the clock face.  Only meaningful together with the %s display patch.''' % (
          mode, mode_wh(mode)[0] - 32, mode_wh(mode)[1] - 30, mode)),
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
 dict(id='camera', name='Camera clamped at battle start: no crash when the start position is near the map edge', date='21 Sep 2026',
      tool='tools/patch_camera.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.22', blocks=blocks_camera,
      desc='''When a battle starts the game puts the camera on the player's start position and only
afterwards computes the camera limits ("half a screen from every map edge") - but it never applies
them to that first position.  The per-frame scrolling code clamps the camera, yet the very first
frame already uses the unclamped position: it hands "camera minus half a screen" as the visible
tile rectangle to the routine that picks the ambient sounds from the terrain on screen, and that
routine walks the rectangle row by row through the map's row-pointer table without checking the far
edge.  With the original 16x14-tile view no shipped start position was close enough to an edge for
the rectangle to leave the map; with the 1024x768 view (28x23 tiles, "1024x768" fix) every start
row within 11 tiles of the far map edge does - the row pointers past the map are NULL and the game
dies with an access violation the moment the battlefield appears (Windows' crash dialog stays
hidden behind the full-screen surface, so it looks like a hang; a relay server then drops the
player after 5 s and the other players continue).  Hit on Fly on 19 Sep 2026 by the player whose
game slot got start position 0 of "Plink - O" (row 131 of 140); Plink - O positions 0 and 1,
Armageddon 3, Circle of Friends 1 and 2, Olympus Mons 0 and 1 and others are affected the same way.
The fix redirects the call that follows the limit computation into a 33-byte routine placed in the
unused zero bytes at the end of the code section: it calls the game's own 2-D clamp function with
those limits on the camera and then continues into the routine the call originally targeted.
Only register-relative addressing, no relocation entries, nothing moves.  Harmless without the
1024x768 fix and in single-player missions (a clamp can only move the camera inside the map).'''),
 dict(id='restore', name='Window restore after minimising: Alt+Tab and the taskbar bring the game back', date='21 Sep 2026',
      tool='tools/patch_restore.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.28', blocks=blocks_restore,
      desc='''Leave the running game with Alt+Tab, the Win key or a click on another window and DirectDraw
minimises it and restores the desktop resolution.  Coming back with Alt+Tab or the taskbar button
the game stays minimised although it is the active window, or shows a black window at desktop
resolution; it is alive and busy, error.log stays empty.  The game has no message loop: once per
frame it pulls posted messages with range-filtered PeekMessage calls (mouse, keyboard, system
commands - the last only to swallow the screen-saver command) and dispatches none of them.
Windows restores a minimised window by posting the system command SC_RESTORE to it, so the request
is removed from the queue and dropped; being activated while still minimised also defeats
DirectDraw's own window hook, which re-sets the exclusive display mode only during a proper
activation - afterwards every attempt to restore the drawing surfaces fails with DDERR_WRONGMODE.
The fix rewrites that per-frame block in place (107 bytes, 86 of them new code, the rest NOP):
the system-command peek hands every command except the screen saver to DefWindowProcA, so
SC_RESTORE, SC_MINIMIZE and the others take effect, and after an SC_RESTORE it calls
ShowWindow(SW_MINIMIZE) followed by ShowWindow(SW_RESTORE) - a deactivate/activate cycle on a window
that is not minimised at the moment of activation, which is the path DirectDraw's hook handles: it
re-sets the mode, the game's own per-frame surface restore repairs the surfaces and the next frame
is drawn.  The two peeks it replaces looked for WM_SETCURSOR and WM_DESTROY, messages Windows never
posts (dead code).  Second part: while minimised the game's main loop used to spin at 100 % of a
processor core - the per-frame present routine fails its blit, fails the surface restore and
returns early, so the Flip that normally paces the loop is never reached (about 4 400 passes per
second).  The branch taken after that failed restore now goes to a 12-byte stub in the spare tail
of the same block: Sleep(1) - one system timer period, at most 16 ms, well inside the 44 ms game
tick - then back to the routine's exit.  Game ticks are clock-driven and keep running while
minimised (a multiplayer client stays in the game), only the idle spin is gone.  The four calls go
through the linker's import thunks; nothing moves, no relocation entry changes.  Verified in game
21 Sep 2026 on both exes (Alt+Tab, taskbar button, Start menu, minimise from the taskbar).'''),
 dict(id='movies', name='Classic movies under their own names: DCINTRO / DCAENDING / DCHENDING (Dark Colony only)', date='15 Sep 2026',
      tool='tools/patch_movies.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.18', blocks=blocks_movies, classic_only=True,
      requires=lambda mode: [] if mode == STOCK_MODE else ['hdpaths'], data=movie_data,
      desc='''Since 15 Sep 2026 both games run from the "DC - Council wars" folder.  Council Wars has its own
INTRO.AVI, AENDING.AVI and HENDING.AVI, so the Classic movies live beside them as AVI/DCINTRO.AVI,
DCAENDING.AVI and DCHENDING.AVI.  Without this fix the Classic exe in that folder plays the Council
Wars intro and endings.  The intro name is one data-section string, "intro.avi", appended to "avi/"
at start-up and by the PLAY INTRO button; the linker aligned the next string to 4 bytes, so
"intro.avi" plus its two padding zeros is exactly the 12 bytes of "dcintro.avi" - rewritten in
place, same address, no code and no relocation entry changes.  The two campaign endings are not in
the exe at all: line 154 of the campaign lists HSCENE.TXT / GSCENE.TXT names them, and the patched exe
reads those lists from INTRF_HD/ (fix "Interface data from INTRF_HD"), where they say
"avi/dchending.avi" / "avi/dcaending.avi" in the repository.  The stock GAMESTAT/ lists that the
untouched exe reads keep the stock names.  REQUIRES the three AVI files DCINTRO.AVI, DCAENDING.AVI,
DCHENDING.AVI in the AVI folder next to the exe (the two INTRF_HD lists come with the "Interface
data from INTRF_HD" fix).  Dark Colony only: the Council Wars exe's intro.avi is its own intro.'''),
 dict(id='sounds', name='WAV files read from the game root, not exp/: the Classic briefings and water ambience (Dark Colony only)', date='19 Sep 2026',
      tool='tools/patch_wavprefix.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.21', blocks=blocks_sounds, classic_only=True,
      desc='''Classic and Council Wars are one code base.  Council Wars opens its files through a helper that
puts "exp/" in front of every name and falls back to the bare name; the Classic build has no such
prefix - except in the wave loader, the function that opens the mission briefings (mission/h1.wav,
g1.wav ...) and every other WAV.  Its own 8-byte prefix slot still says "exp/" in the Classic exe.
In the old "DC - Classic" folder no exp/ tree existed, so that first attempt always failed and
nothing was noticed.  Since both games share the "DC - Council wars" folder, exp/mission/h1-h8.wav
and g1-g8.wav are the Council Wars briefings and exp/sound/water.wav the Council Wars water sound:
the Classic exe found them first and played the wrong briefings for missions 1-8.  The fix empties
the prefix (the four letters become NUL) so the loader opens MISSION/ and SOUND/ directly.  Data
only, in place, no code and no relocation entry changes.'''),
 dict(id='ozi', name='OZI MISSIONS menu mode (Council Wars only)', date='10 Sep 2026', tool='tools/patch_ozi_menu.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.13', blocks=blocks_ozi, cw_only=True,
      requires=lambda mode: [] if mode == STOCK_MODE else ['hdpaths'], data=ozi_data,
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
 # ---- map editor (maped.exe): the functional part of the ozi_ns editor, without its Polish resources
 dict(id='blocksets', name='New Map: Atlantis, Training and Special block sets selectable', date='15 Sep 2026', tool='tools/patch_maped.py --fix blocksets',
      doc='CLAUDE.md "Map editor notes" (Dark-Colony-development)', blocks=blocks_maped('blocksets'), editor_only=True,
      desc='''The original editor greys out three of the five block-set buttons of the New Map dialog: Atlantis,
Training Set and Special Set (the WS_DISABLED style bit, 0x08000000, is set in the dialog template).
The code behind them is complete - the dialog's command table routes the three buttons to block sets
2, 3 and 4 (atlantis.bts, htrain.bts, special.bts) exactly like Desert and Jungle - so this fix only
clears that bit: one byte per button, in the DIALOG resource, no code changes.  This is what the
"ozi_ns" editor did (together with a Polish translation and a renamed title, which stay out here).

The editor loads the block set's palette window from scenario\\<set>.set and its tiles from
<set>.bts.  The game itself ships only desert and jungle; atlantis.set, trainh.set, special.set and
special.bts come with the ozi_ns mission pack.  Without them the editor answers "Can't open file" when
one of the three buttons is pressed - nothing worse.'''),
 dict(id='teams', name='Team Attributes: Team Colour and Allies selectable', date='15 Sep 2026', tool='tools/patch_maped.py --fix teams',
      doc='CLAUDE.md "Map editor notes" (Dark-Colony-development)', blocks=blocks_maped('teams'), editor_only=True,
      desc='''The Team Attributes dialog ships with its Team Colour group (eight radio buttons) and its Allies group
(eight radio buttons) greyed out.  The dialog procedure reads both groups and writes them to the
scenario (%TeamColour, %TeamAllies) - the code was always there.  This fix clears WS_DISABLED on the
sixteen radio buttons and the two group boxes: 18 single-byte edits in the DIALOG resource.  The
AI Slots group of the same dialog stays disabled, as in every version of the editor.'''),
 dict(id='healer', name='Troop Attributes: Healer row usable', date='15 Sep 2026', tool='tools/patch_maped.py --fix healer',
      doc='CLAUDE.md "Map editor notes" (Dark-Colony-development)', blocks=blocks_maped('healer'), editor_only=True,
      desc='''In the Troop Attributes dialog the Healer row - its select radio button and its hit-points edit - is
greyed out, although the dialog procedure reads the edit like those of the other units and the game
knows the healing units (GAMESTAT.TXT rows 49 and 50).  Two single-byte edits clear WS_DISABLED.'''),
 dict(id='troopsframe', name='Troop Attributes: close box instead of sizing border', date='15 Sep 2026', tool='tools/patch_maped.py --fix troopsframe',
      doc='CLAUDE.md "Map editor notes" (Dark-Colony-development)', blocks=blocks_maped('troopsframe'), editor_only=True,
      desc='''Cosmetic, taken over from the ozi_ns editor: the Troop Attributes dialog's frame style changes from
WS_THICKFRAME (a sizing border, useless for a fixed layout) to WS_SYSMENU (a title-bar close box).
One byte in the DIALOG template's style dword.'''),
]

BUILDS = [
 dict(id='Classic', g='classic', exe='dc16new.exe', orig_name='dc16.exe',
      title='Dark Colony (Classic) dc16.exe, build linked 7 Jan 1998, 659456 bytes (patched build: dc16new.exe)',
      steps=['nocd', 'resolution', 'hdpaths', 'cursor', 'pool', 'clock', 'ddraw', 'camera', 'restore', 'movies', 'sounds']),
 dict(id='CouncilWars', g='cw', exe='engexp16new.exe', orig_name='ENGEXP16.EXE',
      title='Dark Colony - The Council Wars ENGEXP16.EXE, 659968 bytes (patched build: engexp16new.exe; called DCEXP16.EXE 10-15 Sep 2026)',
      steps=['nocd', 'resolution', 'hdpaths', 'cursor', 'pool', 'clock', 'ddraw', 'camera', 'restore', 'ozi']),
 dict(id='MapEditor', g='maped', exe='maped_ozi_ns_v1.2.exe', orig_name='maped.exe',
      title='Dark Colony map editor maped.exe (Aug 1997, Borland C++), 336424 bytes (unlocked build: maped_ozi_ns_v1.2.exe)',
      steps=['blocksets', 'teams', 'healer', 'troopsframe']),
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
def attribute(g, step, cur, nxt, mode):
    """Diff one step (cur -> nxt) into annotated edits, validated against the tool's plan blocks."""
    if True:
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
        pd = dict(P=P, edits=edits, special=special, nbytes=sum(1 for i in range(len(cur)) if cur[i] != nxt[i]), leftover=len(leftover), mode=None)
        print(f'{g:8s} {step:11s} {(mode or "-"):9s} {len(edits):4d} edits ({len(leftover)} unannotated runs), {pd["nbytes"]} bytes, insert={bool(special)}')
        return pd


build_data = []
for B in BUILDS:
    g = B['g']
    modes = [STOCK_MODE] + HD_MODES if any(s in HD_STEPS for s in B['steps']) else [None]
    per_mode = {}          # mode -> [pd per step]
    ref_sha = {}           # mode -> SHA-256 with every fix of that mode applied
    orig_sha = size = None
    for mode in modes:
        steps = [s for s in B['steps'] if not (mode == STOCK_MODE and s in HD_STEPS)]
        cur, states = replay(g, steps, mode)
        orig_sha, size = hashlib.sha256(cur).hexdigest(), len(cur)
        out_ = []
        for step, nxt in states:
            out_.append(attribute(g, step, cur, nxt, mode))
            cur = nxt
        per_mode[mode] = out_
        ref_sha[mode] = hashlib.sha256(cur).hexdigest()
    # merge: a fix outside MODE_STEPS must come out identical in every mode it exists in (the tools
    # touch disjoint bytes, so the edits' old bytes do not depend on the mode); it is emitted once.
    # `hdpaths` exists in every HD mode (Mode 'hd'), `resolution`/`clock` once per HD mode.
    patches_out = []
    for step in B['steps']:
        variants = [(m, pd) for m in modes for pd in per_mode[m] if pd['P']['id'] == step]
        if step in MODE_STEPS:
            # identical variants share one entry: all HD modes -> 'hd', every mode -> $null, else per mode
            groups = []
            for m, pd in variants:
                for g_modes, g_pd in groups:
                    if pd['edits'] == g_pd['edits'] and pd['special'] == g_pd['special']:
                        g_modes.add(m)
                        break
                else:
                    groups.append(({m}, pd))
            for g_modes, pd in groups:
                if len(g_modes) == 1:
                    pd['mode'] = next(iter(g_modes))
                elif g_modes == set(HD_MODES):
                    pd['mode'] = 'hd'
                elif g_modes == set(modes):
                    pd['mode'] = None
                else:
                    raise AssertionError((g, step, sorted(g_modes)))
                patches_out.append(pd)
        else:
            first = variants[0][1]
            for m, pd in variants[1:]:
                assert pd['edits'] == first['edits'] and pd['special'] == first['special'], (g, step, m)
            first['mode'] = 'hd' if step in HD_STEPS else None
            patches_out.append(first)
    build_data.append(dict(B=B, orig_sha=orig_sha, size=size, patches=patches_out, modes=[m for m in modes if m],
                           ref_sha={m: sha for m, sha in ref_sha.items() if m}, final_sha=ref_sha.get(DEFAULT_MODE, ref_sha[None] if None in ref_sha else None)))

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
      * the screen resolution is chosen in a drop-down (or -Resolution): 640x480, 1024x768, 1280x1024,
        1280x720, 1280x800; the sizes with your monitor's aspect ratio are marked "recommended for your
        screen" and the largest of them is preselected in the window (the command line defaults to
        1024x768, the published exes)
      * for an HD resolution the script also WRITES the interface data the patched exe reads
        (INTRF_HD\, exp\intrf_hd\, ozi_ns\intrf_hd\: menu scripts, HUD script, briefing lists,
        letterboxed backgrounds, loading screens) from the stock 640x480 files of the game folder and
        the three pictures per size that ship with the game (INTRF_HD\<WxH>\INTRG.GIF, INTRO.GIF,
        INTRFACE.GIF).  Re-encoding the GIF backgrounds needs a small GIF reader/writer: its C# SOURCE
        TEXT is in this file and is compiled in memory by Add-Type when the set is built, with the
        .NET compiler that is part of Windows (no download, no install, ~2 s).  Doing the same in
        plain PowerShell would take 15-30 s per set under Windows PowerShell 5.1 and minutes under
        PowerShell 7; if Add-Type is blocked on your PC, copy a pre-built set instead - see the
        "INTERFACE SET" section below for the details
      * a fix whose resources (data files it needs next to the exe: the INTRF_HD interface files,
        the DC*.AVI movies, the ozi_ns overlay) are not in the target folder is marked
        "RESOURCES NOT FOUND", its checkbox cannot be ticked and -All skips it; a fix that depends
        on such a fix is marked the same way

    The originals, both in the "DC - Council wars" folder (since 15 Sep 2026 the one folder both games
    run from): "dc16.exe" (the untouched Dark Colony exe of the January 1998 update, 6 sections, entry
    point 0x4528DE; its patched build is written as "dc16new.exe") and "ENGEXP16.EXE"
    (ENGEXP16.EXE from the Council Wars CD; patched build "engexp16new.exe").  Both are committed untouched
    in the repository.  The third build is the map editor "Dark Colony - Map editor\maped.exe" (the
    original from the Dark Colony CD): its fixes clear the "disabled" flag on dialog controls the
    original greyed out - the functional part of the ozi_ns editor, without the Polish translation -
    and write "maped_ozi_ns_v1.2.exe".

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
    Path of the untouched original executable (dc16.exe, ENGEXP16.EXE or the map editor's maped.exe).

.PARAMETER Output
    Where to write the patched copy.  Default: dc16new.exe / engexp16new.exe / maped_ozi_ns_v1.2.exe
    next to the original.
    An existing file is not overwritten unless -Overwrite is given.

.PARAMETER Resolution
    Screen resolution to patch for: 640x480 (the stock size: no display fixes), 1024x768 (default,
    the published exes), 1280x1024, 1280x720 or 1280x800.  The 'resolution' and 'clock' fixes exist
    once per size; all sizes share the one INTRF_HD data folder, which must hold the interface set
    built for the chosen size.  The window offers the same choice in a drop-down, marks the sizes
    with your monitor's aspect ratio as "recommended for your screen" and preselects the largest of
    them; without -Resolution the command line uses 1024x768, the size of the published exes.

.PARAMETER Patches
    Patch ids to apply (see -List).  Order does not matter: they are always applied in the fixed
    canonical order.  Use -All for every patch of the build.  With neither, the window opens
    (with the original preloaded when -Original was given).

.PARAMETER IgnoreMissingData
    Apply fixes whose resources (the INTRF_HD interface files, the DC*.AVI movies, the ozi_ns
    overlay, ...) are missing next to the output, or whose prerequisite fixes are not selected.
    Without it -All skips such fixes (reported as "resources not found") and an explicit -Patches
    list naming one is refused, because such an exe fails at start-up or draws garbage and the
    failure would look like a bug of the patch.  Each fix's Requires / Data lists say what it
    needs; -List prints them.

.PARAMETER Verify
    Instead of patching, inspect an existing exe: which build it is and which patches it carries.

.EXAMPLE
    .\Apply-DarkColonyPatches.ps1                               # the window
    .\Apply-DarkColonyPatches.ps1 -List
    .\Apply-DarkColonyPatches.ps1 -List -Detail                 # every single byte edit
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Council wars\dc16.exe" -All
        (run from the root of the Dark-Colony repository, where this file lives; writes dc16new.exe)
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Council wars\dc16.exe" -Patches nocd,resolution,hdpaths,pool
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Council wars\ENGEXP16.EXE" -All
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Council wars\dc16.exe" -All -Resolution 1280x800
    .\Apply-DarkColonyPatches.ps1 -Original "Dark Colony - Map editor\maped.exe" -All     (-> maped_ozi_ns_v1.2.exe)
    .\Apply-DarkColonyPatches.ps1 -Verify "DC - Council wars\dc16new.exe"

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
    [Parameter(ParameterSetName = 'Apply')] [string] $Resolution,
    [Parameter(ParameterSetName = 'Apply')] [switch] $All,
    [Parameter(ParameterSetName = 'Apply')] [switch] $Overwrite,
    [Parameter(ParameterSetName = 'Apply')] [switch] $Force,
    [Parameter(ParameterSetName = 'Apply')] [switch] $IgnoreMissingData,
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
    g = B['g']
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
        PatchedSha256  = {ps_str(bd['final_sha'])}   # every patch applied in the default resolution = the exe in the repository
        # screen resolutions this build can be patched for: '640x480' = the stock size (no display fixes),
        # the others select the per-resolution variants of the 'resolution' and 'clock' fixes below
        Modes          = @({', '.join(ps_str(m) for m in bd['modes'])})
        DefaultMode    = {ps_str(DEFAULT_MODE if bd['modes'] else '')}
        # SHA-256 with every fix of that resolution applied (the default one is the published exe)
        ReferenceSha256 = @{{ {'; '.join(f"{ps_str(m)} = {ps_str(bd['ref_sha'][m])}" for m in bd['modes']) if bd['modes'] else f"{ps_str('')} = {ps_str(bd['final_sha'])}"} }}
        Patches        = @(''')
    for pd in bd['patches']:
        P = pd['P']
        mode = pd['mode'] if pd['mode'] not in (None, 'hd') else None
        vmode = mode or (HD_MODES[0] if pd['mode'] == 'hd' else STOCK_MODE)   # a representative mode for callables
        name = P['name'](mode) if callable(P['name']) else P['name']
        desc = P['desc'](mode) if callable(P['desc']) else P['desc']
        desc_lines = desc.split('\n')
        W(f'''
            # ---- {P['id']}{' @ ' + mode if mode else ''}: {name} ---------------------------------------------------------
            #  Added      : {P['date']}
            #  Made with  : {P['tool']}
            #  Documented : {P['doc']}
            #  Changes    : {pd['nbytes']} bytes in {len(pd['edits']) + (1 if pd['special'] else 0)} edits''')
        for l in desc_lines:
            W(f'            #  {l}'.rstrip())
        req = P.get('requires', [])
        if callable(req):
            req = req(vmode)
        files = P['data'](g, vmode) if P.get('data') else []
        datasize = ''
        if P.get('datasize') and mode:
            srcs = set_sources(g, mode)
            files = files + srcs
            datasize = ("\n                # applying this fix also GENERATES the INTRF_HD interface set for this size from the stock files"
                        "\n                # (Write-InterfaceSet); these three pictures cannot be derived and ship with the game"
                        f"\n                SetSources = @({', '.join(ps_str(x) for x in srcs)})")
        W(f'''            @{{
                Id = {ps_str(P['id'])}; Name = {ps_str(name)}; Date = {ps_str(P['date'])}
                # $null = part of every resolution, 'hd' = every resolution but 640x480, 'WxH' = that one only
                Mode = {ps_str(pd['mode']) if pd['mode'] else '$null'}{datasize}
                Tool = {ps_str(P['tool'])}; Doc = {ps_str(P['doc'])}
                Description = @'
{desc}
'@
                # fixes that must be applied together with this one (the exe would not work otherwise)
                Requires = @({', '.join(ps_str(r) for r in req)})
                # data files this fix needs next to the exe ({len(files)}; listed from the repository when this
                # script was generated) - the patcher refuses to write when any of them is missing
                Data = @(''')
        for fpath in files:
            W(f'                    {ps_str(fpath)}')
        W('''                )
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

# --- screen resolutions (21 Sep 2026) -------------------------------------------------------------
function Get-ModeSize([string] $Mode) { $p = $Mode -split 'x'; return @([int]$p[0], [int]$p[1]) }
function Get-Gcd([int] $a, [int] $b) { while ($b) { $t = $a % $b; $a = $b; $b = $t }; return $a }

# "4:3", "5:4", "16:9", "16:10" - 8:5 is what everyone calls 16:10, and 1366x768 counts as 16:9
function Get-AspectLabel([int] $W, [int] $H) {
    if ($W -le 0 -or $H -le 0) { return '?' }
    $g = Get-Gcd $W $H; $a = [int]($W / $g); $b = [int]($H / $g)
    if ($a -eq 8 -and $b -eq 5) { return '16:10' }
    if ([math]::Abs($W / $H - 16 / 9) -lt 0.01) { return '16:9' }
    return ('{0}:{1}' -f $a, $b)
}

# The PRIMARY monitor's size.  First choice: the Windows Forms screen list, whose Primary flag is
# explicit; Windows PowerShell 5.1 sees the bounds DPI-scaled (1280x800 for a 1920x1200 panel at
# 150 %), but the aspect ratio survives the scaling, and the ratio is all the "recommended" mark
# needs.  Fallback: the video controller's mode (WMI), which is exact but names ONE mode per adapter -
# with two monitors on one adapter it can be the other monitor's (maintainer's laptop 21 Sep 2026:
# primary 1920x1080 external, controller reported the 1920x1200 panel).
function Get-MonitorSize {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        if ($b.Width -gt 0 -and $b.Height -gt 0) { return @([int]$b.Width, [int]$b.Height) }
    } catch {}
    try {
        $vc = @(Get-CimInstance Win32_VideoController -ErrorAction Stop | Where-Object { $_.CurrentHorizontalResolution -gt 0 })
        if ($vc.Count -gt 0) { return @([int]$vc[0].CurrentHorizontalResolution, [int]$vc[0].CurrentVerticalResolution) }
    } catch {}
    return $null
}

# "1280x800 (16:10) recommended for your screen" - recommended = the aspect ratio of the monitor this runs on
function Test-ModeRecommended([string] $Mode, $MonitorSize) {
    $wh = Get-ModeSize $Mode
    return [bool] ($MonitorSize -and (Get-AspectLabel $MonitorSize[0] $MonitorSize[1]) -eq (Get-AspectLabel $wh[0] $wh[1]))
}
function Format-ModeLabel([string] $Mode, $MonitorSize) {
    $wh = Get-ModeSize $Mode
    $label = '{0} ({1})' -f $Mode, (Get-AspectLabel $wh[0] $wh[1])
    if (Test-ModeRecommended $Mode $MonitorSize) { $label += ' recommended for your screen' }
    return $label
}
# The window's initial choice: the largest resolution with the monitor's aspect ratio, else the build's
# default (the published exe).  The command line keeps the build's default, so `-All` without
# -Resolution reproduces the published exe on every PC.
function Get-PreferredMode($Build, $MonitorSize) {
    $pick = $Build.DefaultMode
    foreach ($m in @($Build.Modes)) { if (Test-ModeRecommended $m $MonitorSize) { $pick = $m } }
    return $pick
}

# The fixes of a build for one resolution.  Mode $null = part of every resolution, 'hd' = every
# resolution but 640x480, 'WxH' = that resolution's variant of the fix (640x480 has its own variants of
# movies and ozi).
function Get-BuildPatches($Build, [string] $Mode) {
    $out = @()
    foreach ($p in $Build.Patches) {
        $m = $p.Mode
        if (-not $m) { $out += $p; continue }                                   # every resolution
        if ($Mode -and $m -eq $Mode) { $out += $p; continue }                  # this resolution's own variant (also 640x480)
        if ($m -eq 'hd' -and $Mode -and $Mode -ne '640x480') { $out += $p }    # the shared HD variant
    }
    return $out      # callers wrap it in @(); an empty list comes back as an empty array
}

# The resolution to use for a build: validated -Resolution, else the build's default; '' for a build
# without resolutions (the map editor).
function Resolve-Mode($Build, [string] $Mode) {
    $modes = @($Build.Modes)
    if ($modes.Count -eq 0) { return '' }
    if (-not $Mode) { return $Build.DefaultMode }
    if ($modes -notcontains $Mode) { throw ("unknown resolution '{0}' for {1}; valid: {2}" -f $Mode, $Build.Id, ($modes -join ', ')) }
    return $Mode
}

function Find-BuildBySha([string] $Sha) { foreach ($b in $Builds) { if ($b.OriginalSha256 -eq $Sha) { return $b } }; return $null }

# Guess the build of an arbitrary exe from its size and the state of the first patch's edits (nocd).
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
    else { foreach ($k in @($b.ReferenceSha256.Keys)) { if ($k -and $b.ReferenceSha256[$k] -eq $sha) { $lines += ('= every fix applied for {0} (the reference build of the generator, not the published exe).' -f $k) } } }
    $lines += ''
    # a fix with per-resolution variants (resolution, clock) is reported once, with the variant found
    $seen = @()
    foreach ($p in $b.Patches) {
        if ($seen -contains $p.Id) { continue }
        $seen += $p.Id
        $variants = @($b.Patches | Where-Object { $_.Id -eq $p.Id })
        $applied = @(); $untouched = 0; $mixed = 'MIXED'
        foreach ($v in $variants) {
            $old = 0; $new = 0; $other = 0
            foreach ($e in $v.Edits) { switch (Get-EditState $data $e) { 'old' { $old++ } 'new' { $new++ } default { $other++ } } }
            $total = $old + $new + $other
            if ($new -eq $total) { $applied += $v } elseif ($old -eq $total) { $untouched++ } else { $mixed = "MIXED ($new applied, $old original, $other unknown)" }
        }
        if ($applied.Count -gt 0) {
            $verdict = 'APPLIED'; if ($applied[0].Mode -and $applied[0].Mode -ne 'hd') { $verdict += ' (' + $applied[0].Mode + ')' }
            $name = $applied[0].Name
        } elseif ($untouched -eq $variants.Count) { $verdict = 'not applied'; $name = $p.Name }
        else { $verdict = $mixed; $name = $p.Name }
        $lines += ('  {0,-12} {1,-22} {2}' -f $p.Id, $verdict, $name)
    }
    return $lines
}

# Width and height of an 8-bit BMP from its BITMAPINFOHEADER, or $null.
function Get-BmpSize([string] $Path) {
    try {
        $fs = [System.IO.File]::OpenRead($Path); $h = New-Object byte[] 26; $n = $fs.Read($h, 0, 26); $fs.Dispose()
        if ($n -lt 26 -or $h[0] -ne 0x42 -or $h[1] -ne 0x4D) { return $null }
        return @([BitConverter]::ToInt32($h, 18), [Math]::Abs([BitConverter]::ToInt32($h, 22)))
    } catch { return $null }
}

# The loading screens driver.c shows through LoadImageA(..., W, H) + a full-screen BitBlt (doc 10.2):
# the 640x480 stock picture must sit centred on a black WxH canvas, or LoadImage stretches it.  They
# are not shipped per resolution (two uncompressed megabytes of mostly black); this writes
# INTRF_HD\LOAD.BMP and LOAD2.BMP from INTRFACE\LOAD.BMP / LOAD2.BMP for the chosen size, unless the
# ones in place already have that size.  Same bytes as tools/pad_background.py (Pillow's BMP writer:
# the source's palette size kept - 256 entries for LOAD.BMP, 255 for LOAD2.BMP - as BGRX, biClrUsed =
# biClrImportant = that count, 96 dpi, rows bottom-up), checked byte for byte against its output.
# Returns text lines about what was written.
function Write-LoadingScreens([string] $GameDir, [string] $Mode) {
    $lines = @()
    $wh = Get-ModeSize $Mode; $W = $wh[0]; $H = $wh[1]
    foreach ($name in 'LOAD.BMP', 'LOAD2.BMP') {
        $src = Join-Path $GameDir ('INTRFACE\' + $name)
        $dst = Join-Path $GameDir ('INTRF_HD\' + $name)
        $have = if (Test-Path -LiteralPath $dst) { Get-BmpSize $dst } else { $null }
        if ($have -and $have[0] -eq $W -and $have[1] -eq $H) { continue }
        if (-not (Test-Path -LiteralPath $src)) {
            # only reachable with -IgnoreMissingData (the stock pair is in the fix's Data list)
            $lines += ('INTRF_HD\{0} NOT written: the stock INTRFACE\{0} is not in this folder' -f $name)
            continue
        }
        $s = [System.IO.File]::ReadAllBytes($src)
        $off = [BitConverter]::ToInt32($s, 10); $sw = [BitConverter]::ToInt32($s, 18); $sh = [BitConverter]::ToInt32($s, 22)
        $bpp = [BitConverter]::ToUInt16($s, 28); $ncol = [BitConverter]::ToInt32($s, 46); if ($ncol -eq 0) { $ncol = 256 }
        if ($bpp -ne 8 -or $sh -le 0 -or $sw -gt $W -or $sh -gt $H) { throw "INTRFACE\$name is not an 8-bit ${sw}x${sh} bitmap that fits ${W}x${H}" }
        # palette: the source's entries as BGRX (the border is the first black entry)
        $palBytes = $ncol * 4; $hdr = 54 + $palBytes
        $pal = New-Object byte[] $palBytes
        [Array]::Copy($s, 54, $pal, 0, [Math]::Min($palBytes, $off - 54))
        for ($i = 0; $i -lt $palBytes; $i += 4) { $pal[$i + 3] = 0 }
        $pad = -1
        for ($i = 0; $i -lt $ncol; $i++) { if ($pal[4*$i] -eq 0 -and $pal[4*$i+1] -eq 0 -and $pal[4*$i+2] -eq 0) { $pad = $i; break } }
        if ($pad -lt 0) { throw "INTRFACE\$name has no black palette entry to pad with" }
        $srcStride = ($sw + 3) -band -bnot 3; $dstStride = ($W + 3) -band -bnot 3
        $out = New-Object byte[] ($hdr + $dstStride * $H)
        # BITMAPFILEHEADER + BITMAPINFOHEADER as Pillow writes them
        $out[0] = 0x42; $out[1] = 0x4D
        [Array]::Copy([BitConverter]::GetBytes([int]$out.Length), 0, $out, 2, 4)
        [Array]::Copy([BitConverter]::GetBytes([int]$hdr), 0, $out, 10, 4)
        [Array]::Copy([BitConverter]::GetBytes([int]40), 0, $out, 14, 4)
        [Array]::Copy([BitConverter]::GetBytes([int]$W), 0, $out, 18, 4)
        [Array]::Copy([BitConverter]::GetBytes([int]$H), 0, $out, 22, 4)
        $out[26] = 1; $out[28] = 8
        [Array]::Copy([BitConverter]::GetBytes([int]($dstStride * $H)), 0, $out, 34, 4)
        [Array]::Copy([BitConverter]::GetBytes([int]3780), 0, $out, 38, 4)     # 96 dpi
        [Array]::Copy([BitConverter]::GetBytes([int]3780), 0, $out, 42, 4)
        [Array]::Copy([BitConverter]::GetBytes([int]$ncol), 0, $out, 46, 4)
        [Array]::Copy([BitConverter]::GetBytes([int]$ncol), 0, $out, 50, 4)
        [Array]::Copy($pal, 0, $out, 54, $palBytes)
        if ($pad -ne 0) { for ($i = $hdr; $i -lt $out.Length; $i++) { $out[$i] = [byte]$pad } }
        # rows are stored bottom-up in both files: source row r lands on canvas row r + (H-sh)/2
        $x0 = [int](($W - $sw) / 2); $y0 = [int](($H - $sh) / 2)
        for ($r = 0; $r -lt $sh; $r++) {
            [Array]::Copy($s, $off + $r * $srcStride, $out, $hdr + ($r + $y0) * $dstStride + $x0, $sw)
        }
        $dir = Split-Path -Parent $dst
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
        [System.IO.File]::WriteAllBytes($dst, $out)
        $lines += ('wrote INTRF_HD\{0} ({1}x{2}, from INTRFACE\{0}{3})' -f $name, $W, $H, $(if ($have) { ', replacing a ' + $have[0] + 'x' + $have[1] + ' one' } else { '' }))
    }
    return $lines
}

# =================================================================================================
#  INTERFACE SET - the INTRF_HD files for the chosen resolution, generated from the stock game files
#
#  Until 21 Sep 2026 the resolution-dependent interface files (INTRF_HD\, exp\intrf_hd\,
#  ozi_ns\intrf_hd\) were built by the maintainer's Python tools and shipped as one set per size.
#  Since then this script builds them itself when an HD display fix is applied, from files every
#  game folder has: the stock 640x480 scripts, pictures and briefing lists in INTRFACE\, GAMESTAT\,
#  exp\intrface, exp\gamestat and ozi_ns\gamestat.  The rules are the Python tools' rules
#  (pad_background.py, paint_intro.py, hud_layout.py, split_hd_data.py, build_ozi_overlay.py,
#  patch_movies.py), reproduced here line by line; the output is byte-identical for every text
#  file and pixel-identical for every picture, checked against the tools' output for all sizes.
#
#  Three pictures per size cannot be derived and ship with the game: INTRF_HD\<WxH>\INTRG.GIF and
#  INTRO.GIF (the procedurally painted main-menu planet) and INTRFACE.GIF (the HUD frame).
#
#  ---- A NOTE ON THE COMPILED CODE BELOW ------------------------------------------------------------
#  The 15 menu backgrounds are GIF files.  The game's loader (gifload.c) insists that the picture is
#  exactly the size of the screen, so each 640x480 picture has to be decoded, centred on a black
#  WIDTHxHEIGHT canvas and encoded again (LZW).  That inner loop runs over some 30 million pixels per
#  set.  This script therefore carries the small C# source text of a GIF reader/writer
#  ($GifCodecSource, about 250 lines, plain to read) and hands it to Add-Type, which compiles it in
#  memory when the set is built:
#    * Windows PowerShell 5.1 uses csc.exe from the .NET Framework that is part of Windows
#      (C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe); PowerShell 7 uses the Roslyn
#      compiler it ships with.  No Visual Studio, SDK or download is needed; the compile takes ~2 s,
#      the codec then needs about a second for a whole set.  Nothing is written to disk by the
#      compile and nothing is installed.
#    * Alternatives without compilation: (a) the same codec in plain PowerShell - measured at about
#      15-30 s per set under Windows PowerShell 5.1 and several minutes under PowerShell 7 (a loop
#      over 30 million pixels); (b) the pre-built sets from the maintainer, copied into INTRF_HD by
#      hand.  If Add-Type is not allowed on your PC (Constrained Language Mode, AppLocker), this
#      script says so and points to (b); the exe itself is still written.
# =================================================================================================
$GifCodecSource = @'
using System;
using System.Collections.Generic;
using System.IO;

// DcGif: read one GIF (87a/89a, global or local colour table, interlaced or not, extension blocks
// skipped), centre it on a black canvas of another size, write it back as a plain GIF the game's
// loader accepts: header, 256-entry global colour table, one image descriptor at (0,0) filling the
// screen, no extension blocks, no interlace, LZW with an 8-bit minimum code size.  The LZW output
// is byte-identical to Pillow's (same clear-code and code-width rules), checked on all 15 backgrounds.
public static class DcGif
{
    public class Image
    {
        public int Width, Height;
        public byte[] Palette;      // 768 bytes RGB
        public byte[] Pixels;       // Width*Height palette indices
        public string Version;      // "GIF87a" / "GIF89a"
    }

    public static Image Decode(byte[] d)
    {
        if (d.Length < 13 || d[0] != (byte)'G' || d[1] != (byte)'I' || d[2] != (byte)'F') throw new Exception("not a GIF");
        Image im = new Image();
        im.Version = System.Text.Encoding.ASCII.GetString(d, 0, 6);
        int flags = d[10];
        int pos = 13;
        byte[] palette = new byte[768];
        if ((flags & 0x80) != 0)
        {
            int n = 2 << (flags & 7);
            Array.Copy(d, pos, palette, 0, Math.Min(768, n * 3));
            pos += n * 3;
        }
        while (pos < d.Length)
        {
            byte b = d[pos++];
            if (b == 0x3B) break;
            if (b == 0x21)
            {   // extension: label, then data sub-blocks up to a zero-length one
                pos++;
                while (pos < d.Length) { int len = d[pos++]; if (len == 0) break; pos += len; }
                continue;
            }
            if (b != 0x2C) throw new Exception("unexpected block 0x" + b.ToString("X2"));
            int iw = d[pos + 4] | (d[pos + 5] << 8), ih = d[pos + 6] | (d[pos + 7] << 8);
            int iflags = d[pos + 8];
            pos += 9;
            if ((iflags & 0x80) != 0)
            {
                int n = 2 << (iflags & 7);
                palette = new byte[768];
                Array.Copy(d, pos, palette, 0, Math.Min(768, n * 3));
                pos += n * 3;
            }
            int minCode = d[pos++];
            // gather the sub-blocks
            MemoryStream ms = new MemoryStream();
            while (pos < d.Length) { int len = d[pos++]; if (len == 0) break; ms.Write(d, pos, len); pos += len; }
            byte[] pixels = LzwDecode(ms.ToArray(), minCode, iw * ih);
            if ((iflags & 0x40) != 0) pixels = Deinterlace(pixels, iw, ih);
            im.Width = iw; im.Height = ih; im.Palette = palette; im.Pixels = pixels;
            return im;
        }
        throw new Exception("no image in GIF");
    }

    static byte[] Deinterlace(byte[] src, int w, int h)
    {
        byte[] dst = new byte[src.Length];
        int row = 0;
        int[] starts = { 0, 4, 2, 1 }; int[] steps = { 8, 8, 4, 2 };
        for (int pass = 0; pass < 4; pass++)
            for (int y = starts[pass]; y < h; y += steps[pass])
            { Array.Copy(src, row * w, dst, y * w, w); row++; }
        return dst;
    }

    static byte[] LzwDecode(byte[] data, int minCode, int count)
    {
        byte[] outp = new byte[count];
        int outPos = 0;
        int clear = 1 << minCode, eoi = clear + 1;
        int[] prefix = new int[4096]; byte[] suffix = new byte[4096]; int[] length = new int[4096];
        for (int i = 0; i < clear; i++) { prefix[i] = -1; suffix[i] = (byte)i; length[i] = 1; }
        int codeSize = minCode + 1, next = clear + 2, prev = -1;
        int bitBuf = 0, bitCnt = 0, pos = 0;
        byte[] stack = new byte[4097];
        while (true)
        {
            while (bitCnt < codeSize && pos < data.Length) { bitBuf |= data[pos++] << bitCnt; bitCnt += 8; }
            if (bitCnt < codeSize) break;
            int code = bitBuf & ((1 << codeSize) - 1);
            bitBuf >>= codeSize; bitCnt -= codeSize;
            if (code == clear) { codeSize = minCode + 1; next = clear + 2; prev = -1; continue; }
            if (code == eoi) break;
            int emit = code, firstOfEmit;
            if (code >= next)
            {   // KwKwK case: the code being defined; its string is prev's string + prev's first byte
                if (prev < 0) throw new Exception("bad LZW code");
                int n = length[prev];
                int p = prev;
                for (int i = n - 1; i >= 0; i--) { stack[i] = suffix[p]; p = prefix[p]; }
                firstOfEmit = stack[0];
                stack[n] = (byte)firstOfEmit;
                int total = n + 1;
                if (outPos + total > count) total = count - outPos;
                Array.Copy(stack, 0, outp, outPos, total); outPos += total;
            }
            else
            {
                int n = length[emit];
                int p = emit;
                for (int i = n - 1; i >= 0; i--) { stack[i] = suffix[p]; p = prefix[p]; }
                firstOfEmit = stack[0];
                int total = n;
                if (outPos + total > count) total = count - outPos;
                Array.Copy(stack, 0, outp, outPos, total); outPos += total;
            }
            if (prev >= 0 && next < 4096)
            {
                prefix[next] = prev; suffix[next] = (byte)firstOfEmit; length[next] = length[prev] + 1; next++;
                if (next == (1 << codeSize) && codeSize < 12) codeSize++;
            }
            prev = code;
            if (outPos >= count) break;
        }
        return outp;
    }

    // Encode pixels as a plain GIF: 256-entry global colour table, one full-screen image, LZW-8.
    public static byte[] Encode(string version, int w, int h, byte[] palette, byte[] pixels)
    {
        MemoryStream ms = new MemoryStream();
        BinaryWriter bw = new BinaryWriter(ms);
        bw.Write(System.Text.Encoding.ASCII.GetBytes(version.Length == 6 ? version : "GIF87a"));
        bw.Write((ushort)w); bw.Write((ushort)h);
        bw.Write((byte)0x87);            // global colour table, 256 entries
        bw.Write((byte)0); bw.Write((byte)0);
        byte[] pal = new byte[768]; Array.Copy(palette, 0, pal, 0, Math.Min(768, palette.Length));
        bw.Write(pal);
        bw.Write((byte)0x2C); bw.Write((ushort)0); bw.Write((ushort)0); bw.Write((ushort)w); bw.Write((ushort)h); bw.Write((byte)0);
        bw.Write((byte)8);
        byte[] lzw = LzwEncode(pixels, 8);
        for (int p = 0; p < lzw.Length; p += 255)
        {
            int n = Math.Min(255, lzw.Length - p);
            bw.Write((byte)n); bw.Write(lzw, p, n);
        }
        bw.Write((byte)0); bw.Write((byte)0x3B);
        bw.Flush();
        return ms.ToArray();
    }

    static byte[] LzwEncode(byte[] pixels, int minCode)
    {
        int clear = 1 << minCode, eoi = clear + 1;
        MemoryStream ms = new MemoryStream();
        int bitBuf = 0, bitCnt = 0;
        int codeSize = minCode + 1, next = clear + 2;
        // dictionary: (prefix code, byte) -> code, as a flat table indexed prefix*256+byte
        int[] table = new int[4096 * 256];
        for (int i = 0; i < table.Length; i++) table[i] = -1;
        Action<int> put = delegate (int code)
        {
            bitBuf |= code << bitCnt; bitCnt += codeSize;
            while (bitCnt >= 8) { ms.WriteByte((byte)(bitBuf & 0xFF)); bitBuf >>= 8; bitCnt -= 8; }
        };
        put(clear);
        if (pixels.Length == 0) { put(eoi); }
        else
        {
            int cur = pixels[0];
            for (int i = 1; i < pixels.Length; i++)
            {
                int k = pixels[i];
                int idx = cur * 256 + k;
                if (table[idx] >= 0) { cur = table[idx]; continue; }
                put(cur);
                if (next < 4096)
                {
                    table[idx] = next++;
                    if (next > (1 << codeSize) && codeSize < 12) codeSize++;
                }
                else
                {
                    put(clear);
                    for (int t = 0; t < table.Length; t++) table[t] = -1;
                    codeSize = minCode + 1; next = clear + 2;
                }
                cur = k;
            }
            put(cur);
            put(eoi);
        }
        if (bitCnt > 0) ms.WriteByte((byte)(bitBuf & 0xFF));
        return ms.ToArray();
    }

    // pad_background.pad_gif: the picture centred on a canvas of the first black palette entry.
    public static byte[] Pad(byte[] src, int W, int H)
    {
        Image im = Decode(src);
        if (im.Width > W || im.Height > H) throw new Exception("picture " + im.Width + "x" + im.Height + " does not fit " + W + "x" + H);
        int pad = -1;
        for (int i = 0; i < 256; i++) if (im.Palette[i * 3] == 0 && im.Palette[i * 3 + 1] == 0 && im.Palette[i * 3 + 2] == 0) { pad = i; break; }
        if (pad < 0) throw new Exception("no black palette entry to pad with");
        byte[] canvas = new byte[W * H];
        if (pad != 0) for (int i = 0; i < canvas.Length; i++) canvas[i] = (byte)pad;
        int x0 = (W - im.Width) / 2, y0 = (H - im.Height) / 2;
        for (int y = 0; y < im.Height; y++) Array.Copy(im.Pixels, y * im.Width, canvas, (y + y0) * W + x0, im.Width);
        // always GIF87a: Pillow writes 87a whenever no 89a feature (extension block) is used, whatever
        // the source said, and the game accepts both - so the output stays byte-identical to the
        // tools' sets (VICTORY.GIF is the one GIF89a source; found 21 Sep 2026 as a 1-byte git diff)
        return Encode("GIF87a", W, H, im.Palette, canvas);
    }

    // width and height from the logical screen descriptor
    public static int[] Size(byte[] d) { return new int[] { d[6] | (d[7] << 8), d[8] | (d[9] << 8) }; }
}
'@

$script:gifCodecReady = $false
function Initialize-GifCodec {
    if ($script:gifCodecReady) { return }
    try {
        if (-not ('DcGif' -as [type])) { Add-Type -TypeDefinition $GifCodecSource -ErrorAction Stop }
        $script:gifCodecReady = $true
    } catch {
        throw ("the GIF reader/writer could not be compiled (Add-Type): {0}`r`n" +
               "The exe was written, but the INTRF_HD interface set for this resolution was not. Either allow Add-Type " +
               "(it compiles the C# text in this file with the .NET compiler that ships with Windows) or copy a pre-built " +
               "set from the maintainer into INTRF_HD.") -f $_.Exception.Message
    }
}

# --- the text rules of the Python tools, on Latin-1 strings (one char per byte, so nothing is lost) ---
$script:latin1 = [System.Text.Encoding]::GetEncoding(28591)
function Read-Latin1([string] $Path) { return $script:latin1.GetString([System.IO.File]::ReadAllBytes($Path)) }
function Write-Latin1([string] $Path, [string] $Text) {
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [System.IO.File]::WriteAllBytes($Path, $script:latin1.GetBytes($Text))
}
function Find-CI([string] $Folder, [string] $Name) {     # case-insensitive file lookup, $null if absent
    if (-not (Test-Path -LiteralPath $Folder)) { return $null }
    foreach ($f in [System.IO.Directory]::GetFiles($Folder)) { if ([System.IO.Path]::GetFileName($f) -ieq $Name) { return $f } }
    return $null
}

$SIZE2 = [regex] '(?m)^([ \t]*)size([ \t]+)(\d+)([ \t]+)(\d+)([ \t]*\r?)$'
$SIZE4 = [regex] '(?m)^([ \t]*)size([ \t]+)(\d+)[ \t]+(\d+)[ \t]+(\d+)[ \t]+(\d+)([ \t]*\r?)$'
$BACKGROUND = [regex] '(?im)^[ \t]*background[ \t]+(?:intrface/|intrf_hd/)?(\S+)'
$BG_RETARGET = [regex] '(?im)^([ \t]*background[ \t]+)intrface/(\S+)'
$FRAME_XY = [regex] '^([ \t]*)(\d+)([ \t]+)(\d+)([ \t]+)(\d+)([ \t]*\r?)$'
$TOKENS = [regex] '\S+|[ \t]+'
$POSITIONED = @('pushb', 'checkb', 'in_text', 'picture', 'list', 'scroll', 'gadget', 'label', 'count', 'scount')   # pad_background / paint_intro
$HUD_KINDS = @('pushb', 'checkb', 'in_text', 'picture', 'list', 'scroll', 'gadget', 'count', 'scount')            # hud_layout (no label)

# Rewrites the 4th and 5th field (x, y) of every positioned widget line through $Move (a script block
# taking the words and returning @(x, y) or $null to leave the line) and returns the joined text.
# Lines are split at LF and keep their own CR, as the Python tools do.
function Edit-Widgets([string] $Text, [scriptblock] $Move, [string[]] $Kinds) {
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($ln in $Text.Split("`n")) {
        $i = $ln.IndexOf('%')
        $body = if ($i -ge 0) { $ln.Substring(0, $i) } else { $ln }
        $rest = if ($i -ge 0) { $ln.Substring($i) } else { '' }
        $toks = @($TOKENS.Matches($body) | ForEach-Object { $_.Value })
        $words = @($toks | Where-Object { $_.Trim().Length -gt 0 })
        if ($words.Count -gt 0 -and $Kinds -contains $words[0].ToLower()) {
            $xy = & $Move $words
            if ($xy) {
                $n = 0
                for ($t = 0; $t -lt $toks.Count; $t++) {
                    if ($toks[$t].Trim().Length -eq 0) { continue }
                    $n++
                    if ($n -eq 4 -and $xy[0] -ne $null) { $toks[$t] = [string][int]$xy[0] }
                    elseif ($n -eq 5) { if ($xy[1] -ne $null) { $toks[$t] = [string][int]$xy[1] }; break }
                }
                $body = -join $toks
            }
        }
        $out.Add($body + $rest)
    }
    return ($out -join "`n")
}

# pad_background.edit_script: widgets +(dx,dy) where the fields are plain numbers; size -> rect.
# (The $move blocks below are plain script blocks: PowerShell's dynamic scoping lets them read the
# caller's $dx/$dy/$W/$H and call this script's functions; a GetNewClosure() block could not.)
function Edit-PaddedScript([string] $Text, [int] $dx, [int] $dy, [int[]] $Rect) {
    $move = { param($fields) if ($fields.Count -lt 5) { return $null }
              $x = if ($fields[3] -match '^\d+$') { [int]$fields[3] + $dx } else { $null }
              $y = if ($fields[4] -match '^\d+$') { [int]$fields[4] + $dy } else { $null }
              return @($x, $y) }
    $t = Edit-Widgets $Text $move $POSITIONED
    $m = $SIZE2.Match($t); $tail = 6
    if (-not $m.Success) { $m = $SIZE4.Match($t); $tail = 7 }
    if ($m.Success) {
        $new = '{0}size{1}{2} {3} {4} {5}{6}' -f $m.Groups[1].Value, $m.Groups[2].Value, $Rect[0], $Rect[1], $Rect[2], $Rect[3], $m.Groups[$tail].Value
        $t = $t.Substring(0, $m.Index) + $new + $t.Substring($m.Index + $m.Length)
    }
    return $t
}

# paint_intro._positioned: kind n desc x y [w h] rest, with n/desc/x/y integers
function Get-Positioned([string[]] $w) {
    if ($w.Count -lt 5 -or $POSITIONED -notcontains $w[0].ToLower()) { return $null }
    for ($i = 1; $i -le 4; $i++) { if ($w[$i] -notmatch '^-?\d+$') { return $null } }
    $ww = 0; $hh = 0; $rest = @()
    if ($w.Count -ge 7 -and $w[5] -match '^\d+$' -and $w[6] -match '^\d+$') { $ww = [int]$w[5]; $hh = [int]$w[6]; if ($w.Count -gt 7) { $rest = $w[7..($w.Count-1)] } }
    elseif ($w.Count -gt 5) { $rest = $w[5..($w.Count-1)] }
    return @{ kind = $w[0].ToLower(); n = [int]$w[1]; x = [int]$w[3]; y = [int]$w[4]; w = $ww; h = $hh; rest = @($rest) }
}
$LOGOS = @('DCSS', 'DCUK'); $BUTTON_SPRITES = @('LARGEBUTTON', 'MEDBUTTON'); $LOGO_CLEARANCE = 20
function Test-Logo($p)  { return ($p.kind -eq 'gadget' -and $p.rest.Count -gt 0 -and $LOGOS -contains $p.rest[0]) }
function Test-Title($p) { return ($p.kind -eq 'gadget' -and $p.rest.Count -gt 0 -and ($BUTTON_SPRITES + $LOGOS) -notcontains $p.rest[0]) }

# paint_intro.layout_for + relayout: the title/credits/button cluster keeps its stock vertical centre
# as a fraction of the height, the button grid is centred horizontally, logo and title centred each.
function Edit-IntroScript([string] $Text, [int] $W, [int] $H) {
    $widgets = @()
    foreach ($ln in $Text.Split("`n")) {
        $i = $ln.IndexOf('%'); $body = if ($i -ge 0) { $ln.Substring(0, $i) } else { $ln }
        $p = Get-Positioned @($body -split '\s+' | Where-Object { $_ })
        if ($p) { $widgets += $p }
    }
    $cluster = @($widgets | Where-Object { -not (Test-Logo $_) })
    if ($cluster.Count -eq 0) { throw 'intro script: no widgets besides the logo' }
    $y0 = ($cluster | ForEach-Object { $_.y } | Measure-Object -Minimum).Minimum
    $y1 = ($cluster | ForEach-Object { $_.y + $_.h } | Measure-Object -Maximum).Maximum
    $dy = [int][Math]::Round(($y0 + $y1) / 2 * ($H / 480 - 1), [System.MidpointRounding]::ToEven)
    if (@($widgets | Where-Object { Test-Logo $_ }).Count -gt 0) { $dy += $LOGO_CLEARANCE }
    $grid = @($cluster | Where-Object { -not (Test-Title $_) })
    $x0 = ($grid | ForEach-Object { $_.x } | Measure-Object -Minimum).Minimum
    $x1 = ($grid | ForEach-Object { $_.x + $_.w } | Measure-Object -Maximum).Maximum
    $dx = [int][Math]::Round($W / 2 - ($x0 + $x1) / 2, [System.MidpointRounding]::ToEven)
    $move = { param($fields) $p = Get-Positioned $fields; if (-not $p) { return $null }   # not $w: it would shadow the width $W
              if ((Test-Logo $p) -or (Test-Title $p)) { return @([int](($W - $p.w) / 2), ($p.y + $dy)) }
              return @(($p.x + $dx), ($p.y + $dy)) }
    $t = Edit-Widgets $Text $move $POSITIONED
    $m = $SIZE2.Match($t); if (-not $m.Success) { $m = $SIZE4.Match($t) }
    if (-not $m.Success) { throw 'intro script: no size line' }
    $lead = ([regex] '^[ \t]*').Match($m.Value).Value
    return $t.Substring(0, $m.Index) + $lead + ('size {0} {1}' -f $W, $H) + $t.Substring($m.Index + $m.Length)
}

# hud_layout.cmd_maine: right-panel widgets slide right (and down with the panel's bottom cluster),
# bottom-bar furniture slides down (and right from the message box's end), the one in-view widget
# (PAUSED) by half the growth; size -> W H
function Edit-HudScript([string] $Text, [int] $W, [int] $H) {
    $dx = $W - 640; $dy = $H - 480
    $move = { param($fields) if ($fields.Count -lt 5 -or $fields[3] -notmatch '^\d+$' -or $fields[4] -notmatch '^\d+$') { return $null }
              $x = [int]$fields[3]; $y = [int]$fields[4]
              if ($x -ge 516) { $nx = $x + $dx; $ny = if ($y -ge 399) { $y + $dy } else { $y } }
              elseif ($y -ge 420) { $nx = if ($x -ge 300) { $x + $dx } else { $x }; $ny = $y + $dy }
              else { $nx = $x + [int][Math]::Floor($dx / 2); $ny = $y + [int][Math]::Floor($dy / 2) }
              if ($nx -eq $x -and $ny -eq $y) { return $null }
              return @($nx, $ny) }
    $t = Edit-Widgets $Text $move $HUD_KINDS
    $m = $SIZE2.Match($t)
    if ($m.Success) { $t = $t.Substring(0, $m.Index) + ('{0}size{1}{2} {3}{4}' -f $m.Groups[1].Value, $m.Groups[2].Value, $W, $H, $m.Groups[6].Value) + $t.Substring($m.Index + $m.Length) }
    return $t
}

# pad_background.edit_scene / build_ozi_overlay.shift_scene_markers: the `frame x y` line after an .avi line
function Edit-SceneList([string] $Text, [int] $dx, [int] $dy) {
    $out = New-Object System.Collections.Generic.List[string]
    $prevAvi = $false
    foreach ($ln in $Text.Split("`n")) {
        $m = $FRAME_XY.Match($ln)
        if ($m.Success -and $prevAvi) {
            $ln = '{0}{1}{2}{3}{4}{5}{6}' -f $m.Groups[1].Value, $m.Groups[2].Value, $m.Groups[3].Value, ([int]$m.Groups[4].Value + $dx), $m.Groups[5].Value, ([int]$m.Groups[6].Value + $dy), $m.Groups[7].Value
        }
        $prevAvi = $ln.Trim().ToLower().EndsWith('.avi')
        $out.Add($ln)
    }
    return ($out -join "`n")
}

# split_hd_data: `background intrface/<gif>` -> `intrf_hd/<gif>` (every background of a generated script moved)
function Set-BackgroundHd([string] $Text) { return $BG_RETARGET.Replace($Text, '$1intrf_hd/$2') }

# split_hd_data.rename_dat_list: the per-screen FIN lists name the re-baked logo banks
function Edit-DatList([string] $Text) {
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($raw in $Text.Split("`n")) {
        $cr = if ($raw.EndsWith("`r")) { "`r" } else { '' }
        $line = if ($cr) { $raw.Substring(0, $raw.Length - 1) } else { $raw }
        if (@('dcss.fin', 'dcuk.fin', 'dcut.fin') -contains $line.Trim().ToLower()) { $line = $line.Trim().Substring(0, $line.Trim().Length - 4) + '_hd.fin' }
        $out.Add($line + $cr)
    }
    return ($out -join "`n")
}

# The 640x480 two-column OZI menu (see Edit-OziMenu): a button and its LARGEBUTTON gadget move together,
# the commented-out OZI LOAD pair (pushb 4 / gadget 10) is brought back in, texts as at HD sizes.
function Edit-OziMenu640([string] $Text) {
    $place = @{ 0 = @(138, 340); 6 = @(138, 340);      # NEW CAMPAIGN
                2 = @(138, 366); 8 = @(138, 366);      # LOAD GAME
                16 = @(318, 340); 17 = @(318, 340);    # OZI MISSIONS (the PLAY INTRO row)
                4 = @(318, 366); 10 = @(318, 366);     # OZI LOAD (the SINGLE PLAYER WAR pair, re-enabled)
                12 = @(228, 392); 13 = @(228, 392) }   # QUIT, centred below
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($raw in $Text.Split("`n")) {
        $cr = if ($raw.EndsWith("`r")) { "`r" } else { '' }
        $line = if ($cr) { $raw.Substring(0, $raw.Length - 1) } else { $raw }
        if ($line -match '^\s*textmsg\s+5\s+') { $line = 'textmsg 5       OZI LOAD' }
        elseif ($line -match '^\s*textmsg\s+8\s+') { $line = 'textmsg 8       OZI MISSIONS' }
        elseif ($line -match '^\s*banim\s+18\s+') { $line = "banim   18  0  5 5`t 6 8 17 10 13  0 2 16 4 12" }
        else {
            $m = [regex]::Match($line, '^(%?)(\s*)(pushb|gadget)\s+(\d+)\s')
            if ($m.Success -and $place.ContainsKey([int]$m.Groups[4].Value)) {
                $xy = $place[[int]$m.Groups[4].Value]
                $body = $line.Substring($m.Groups[1].Length)          # drop a leading % (pushb 4 / gadget 10)
                $toks = @($TOKENS.Matches($body) | ForEach-Object { $_.Value })
                $n = 0
                for ($t = 0; $t -lt $toks.Count; $t++) {
                    if ($toks[$t].Trim().Length -eq 0) { continue }
                    $n++
                    if ($n -eq 4) { $toks[$t] = [string]$xy[0] } elseif ($n -eq 5) { $toks[$t] = [string]$xy[1]; break }
                }
                $line = -join $toks
            }
        }
        $out.Add($line + $cr)
    }
    return ($out -join "`n")
}

# build_ozi_overlay.menu_rows: OZI LOAD and QUIT one and two rows below the PLAY INTRO row, same column.
# At the stock size (a `size 640 480` script) five rows do not fit between the code-drawn credits box
# (rows 230..330) and the backdrop's bottom artwork (from row 435), and QUIT sat on the artwork
# (maintainer report 21 Sep 2026); the stock script's own commented-out two-column plan is used
# instead: Council Wars buttons at x=138, OZI buttons at x=318 (rows 340/366), QUIT centred at 392.
function Edit-OziMenu([string] $Text) {
    $m2 = $SIZE2.Match($Text)
    if ($m2.Success -and [int]$m2.Groups[3].Value -eq 640 -and [int]$m2.Groups[5].Value -eq 480) { return Edit-OziMenu640 $Text }
    $xy = @{}
    foreach ($m in ([regex] '(?m)^%?\s*pushb\s+(\d+)\s+\d+\s+(\d+)\s+(\d+)\s').Matches($Text)) { $xy[[int]$m.Groups[1].Value] = @([int]$m.Groups[2].Value, [int]$m.Groups[3].Value) }
    foreach ($need in 0, 2, 16) { if (-not $xy.ContainsKey($need)) { throw "exp\intrf_hd\bintroe: no pushb $need row" } }
    $x = $xy[0][0]; $pitch = $xy[2][1] - $xy[0][1]
    $yLoad = $xy[16][1] + $pitch; $yQuit = $yLoad + $pitch
    $rows = @(
        @('^%?\s*pushb\s+4\s+.*$',   ('pushb   4       0       {0,-7} {1,-7} 179     25      -11     0        label centre   5 0 - remap 0' -f $x, $yLoad)),
        @('^%?\s*gadget\s+10\s+.*$', ('gadget  10      0       {0,-7} {1,-7} 179     25      LARGEBUTTON  anim_stopped' -f $x, $yLoad)),
        @('^\s*pushb\s+12\s+.*$',    ('pushb   12      0       {0,-7} {1,-7} 179     25      -11     0  label centre 7 0 - remap 0' -f $x, $yQuit)),
        @('^\s*gadget\s+13\s+.*$',   ('gadget  13      0       {0,-7} {1,-7} 179     25      LARGEBUTTON  anim_stopped' -f $x, $yQuit)),
        @('^\s*banim\s+18\s+.*$',    "banim   18  0  5 5`t 6 8 17 10 13  0 2 16 4 12"),
        @('^\s*textmsg\s+5\s+.*$',   'textmsg 5       OZI LOAD'),
        @('^\s*textmsg\s+8\s+.*$',   'textmsg 8       OZI MISSIONS'))
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($raw in $Text.Split("`n")) {
        $cr = if ($raw.EndsWith("`r")) { "`r" } else { '' }
        $line = if ($cr) { $raw.Substring(0, $raw.Length - 1) } else { $raw }
        foreach ($r in $rows) { if ([regex]::IsMatch($line, $r[0])) { $line = $r[1]; break } }
        $out.Add($line + $cr)
    }
    return ($out -join "`n")
}

# The whole set for one resolution. $Movies: the Classic `movies` fix is applied, so the two campaign
# lists name the DC*.AVI endings (patch_movies.py).  Returns text lines about what was written.
function Write-InterfaceSet([string] $GameDir, [string] $Mode, [bool] $Movies) {
    $wh = Get-ModeSize $Mode; $W = $wh[0]; $H = $wh[1]
    $dx0 = [int][Math]::Floor(($W - 640) / 2); $dy0 = [int][Math]::Floor(($H - 480) / 2)
    $lines = @()
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $intrface = Join-Path $GameDir 'INTRFACE'; $hd = Join-Path $GameDir 'INTRF_HD'; $gamestat = Join-Path $GameDir 'GAMESTAT'
    $src = Join-Path $hd $Mode
    foreach ($need in 'INTRG.GIF', 'INTRO.GIF', 'INTRFACE.GIF') { if (-not (Find-CI $src $need)) { throw "INTRF_HD\$Mode\$need is missing: the painted backdrops and HUD frame for $Mode ship with the game and cannot be generated" } }
    Initialize-GifCodec
    $written = 0
    $introScreens = @('bintroe', 'introe', 'buttonse', 'dintroe')
    $gifsToPad = @{}
    # --- INTRFACE scripts -> INTRF_HD
    foreach ($f in [System.IO.Directory]::GetFiles($intrface)) {
        $name = [System.IO.Path]::GetFileName($f); $lname = $name.ToLower()
        if ($lname.EndsWith('.bak') -or $lname.EndsWith('.gif') -or $lname.EndsWith('.bmp') -or $lname.EndsWith('.spr') -or $lname.EndsWith('.rmp') -or $lname.EndsWith('.rgb')) { continue }
        if ($lname -eq 'multie~1.txt') { continue }     # a stray duplicate of MULTIE nothing reads (split_hd_data DROP)
        $text = Read-Latin1 $f
        if ($lname.EndsWith('.dat')) {
            $new = Edit-DatList $text
            if ($new -ne $text) { Write-Latin1 (Join-Path $hd $name) $new; $written++ }
            continue
        }
        $m4 = $SIZE4.Match($text); $m2 = $SIZE2.Match($text); $bg = $BACKGROUND.Match($text)
        if ($m4.Success -and -not $bg.Success) {
            $x = [int]$m4.Groups[3].Value; $y = [int]$m4.Groups[4].Value
            if ($x -eq 0 -and $y -eq 0) { continue }
            # a sub-window dialog: rect and widgets +(dx,dy)
            Write-Latin1 (Join-Path $hd $name) (Edit-PaddedScript $text $dx0 $dy0 @(($x + $dx0), ($y + $dy0), [int]$m4.Groups[5].Value, [int]$m4.Groups[6].Value)); $written++
            continue
        }
        if ($m4.Success -or -not $m2.Success -or -not $bg.Success) { continue }
        if ($lname -eq 'maine') {
            Write-Latin1 (Join-Path $hd $name) (Set-BackgroundHd (Edit-HudScript $text $W $H)); $written++
            continue
        }
        $gif = Find-CI $intrface ($bg.Groups[1].Value + '.GIF')
        if (-not $gif) { continue }
        if ($introScreens -contains $lname) {
            Write-Latin1 (Join-Path $hd $name) (Set-BackgroundHd (Edit-IntroScript $text $W $H)); $written++
        } else {
            $gs = [DcGif]::Size([System.IO.File]::ReadAllBytes($gif))
            Write-Latin1 (Join-Path $hd $name) (Set-BackgroundHd (Edit-PaddedScript $text ([int][Math]::Floor(($W - $gs[0]) / 2)) ([int][Math]::Floor(($H - $gs[1]) / 2)) @(0, 0, $W, $H))); $written++
        }
        $gifsToPad[[System.IO.Path]::GetFileName($gif).ToUpper()] = $gif
    }
    # --- backgrounds: the painted / spliced ones ship per size, the rest are letterboxed here
    foreach ($shipped in 'INTRG.GIF', 'INTRO.GIF', 'INTRFACE.GIF') {
        $gifsToPad.Remove($shipped)
        [System.IO.File]::Copy((Find-CI $src $shipped), (Join-Path $hd $shipped), $true); $written++
    }
    foreach ($k in @($gifsToPad.Keys | Sort-Object)) {
        $bytes = [DcGif]::Pad([System.IO.File]::ReadAllBytes($gifsToPad[$k]), $W, $H)
        [System.IO.File]::WriteAllBytes((Join-Path $hd ([System.IO.Path]::GetFileName($gifsToPad[$k]))), $bytes); $written++
    }
    # --- briefing lists.  HSCENE/GSCENE name the campaign endings: the Classic `movies` fix makes the
    # exe play DCHENDING/DCAENDING.AVI, so the lists say so when that fix is on - or when those files are
    # in the folder (the fix requires them; the Council Wars exe never reads these two lists, so a
    # Council Wars run in the shared folder must not undo the Classic names)
    $avi = Join-Path $GameDir 'AVI'
    if ((Find-CI $avi 'DCHENDING.AVI') -and (Find-CI $avi 'DCAENDING.AVI')) { $Movies = $true }
    foreach ($ln in 'HSCENE.TXT', 'GSCENE.TXT', 'HTSCENE.TXT', 'GTSCENE.TXT') {
        $p = Find-CI $gamestat $ln
        if (-not $p) { continue }
        $t = Edit-SceneList (Read-Latin1 $p) $dx0 $dy0
        if ($Movies) {
            if ($ln -eq 'HSCENE.TXT') { $t = $t.Replace('avi/hending.avi', 'avi/dchending.avi') }
            if ($ln -eq 'GSCENE.TXT') { $t = $t.Replace('avi/aending.avi', 'avi/dcaending.avi') }
        }
        Write-Latin1 (Join-Path $hd ([System.IO.Path]::GetFileName($p))) $t; $written++
    }
    # --- loading screens
    $lines += Write-LoadingScreens $GameDir $Mode
    # --- Council Wars: exp\intrface overrides -> exp\intrf_hd, and the OZI overlay's copies
    $expI = Join-Path $GameDir 'exp\intrface'; $expG = Join-Path $GameDir 'exp\gamestat'; $expHd = Join-Path $GameDir 'exp\intrf_hd'
    $expWritten = 0
    if (Test-Path -LiteralPath $expI) {
        foreach ($nm in 'bintroe', 'introe', 'shumane') {
            $p = Find-CI $expI $nm
            if (-not $p) { continue }
            $text = Read-Latin1 $p
            if ($nm -eq 'shumane') {
                $bg = $BACKGROUND.Match($text)
                $gif = if ($bg.Success) { Find-CI $intrface ($bg.Groups[1].Value + '.GIF') } else { $null }
                $gs = if ($gif) { [DcGif]::Size([System.IO.File]::ReadAllBytes($gif)) } else { @(640, 480) }
                $t = Set-BackgroundHd (Edit-PaddedScript $text ([int][Math]::Floor(($W - $gs[0]) / 2)) ([int][Math]::Floor(($H - $gs[1]) / 2)) @(0, 0, $W, $H))
            } else {
                $t = Set-BackgroundHd (Edit-IntroScript $text $W $H)
                if ($nm -eq 'bintroe') { $t = Edit-OziMenu $t }
            }
            Write-Latin1 (Join-Path $expHd ([System.IO.Path]::GetFileName($p))) $t; $expWritten++
        }
        foreach ($ln in 'hxscene.txt', 'gxscene.txt') {
            $p = Find-CI $expG $ln
            if ($p) { Write-Latin1 (Join-Path $expHd ([System.IO.Path]::GetFileName($p))) (Edit-SceneList (Read-Latin1 $p) $dx0 $dy0); $expWritten++ }
        }
    }
    $oziWritten = 0
    $ozi = Join-Path $GameDir 'ozi_ns'; $oziHd = Join-Path $ozi 'intrf_hd'; $oziG = Join-Path $ozi 'gamestat'
    if ((Test-Path -LiteralPath $ozi) -and $expWritten -gt 0) {
        foreach ($nm in 'bintroe', 'introe', 'shumane') {
            $p = Find-CI $expHd $nm
            if ($p) { if (-not (Test-Path -LiteralPath $oziHd)) { New-Item -ItemType Directory -Path $oziHd -Force | Out-Null }; [System.IO.File]::Copy($p, (Join-Path $oziHd $nm), $true); $oziWritten++ }
        }
        foreach ($ln in 'hxscene.txt', 'gxscene.txt') {
            $p = Find-CI $oziG $ln       # the pack's lists, unshifted (build_ozi_overlay.py keeps them there)
            if ($p) { Write-Latin1 (Join-Path $oziHd $ln) (Edit-SceneList (Read-Latin1 $p) $dx0 $dy0); $oziWritten++ }
        }
    }
    $lines += ('interface set for {0} written: INTRF_HD\ {1} files{2}{3} ({4:N1} s, GIFs re-encoded by the compiled DcGif codec)' -f $Mode, $written,
               $(if ($expWritten) { ", exp\intrf_hd\ $expWritten" } else { '' }), $(if ($oziWritten) { ", ozi_ns\intrf_hd\ $oziWritten" } else { '' }), $sw.Elapsed.TotalSeconds)
    return $lines
}

# --- 640x480 companions of two fixes: the exe is pointed at copies the original exe never reads ---------
# movies @ 640x480: GAMESTAT\HSCNDC.TXT / GSCNDC.TXT = the stock campaign lists naming the Classic endings
function Write-StockEndingLists([string] $GameDir) {
    $lines = @(); $gs = Join-Path $GameDir 'GAMESTAT'
    foreach ($pair in @(@('HSCENE.TXT', 'HSCNDC.TXT', 'avi/hending.avi', 'avi/dchending.avi'), @('GSCENE.TXT', 'GSCNDC.TXT', 'avi/aending.avi', 'avi/dcaending.avi'))) {
        $src = Find-CI $gs $pair[0]
        if (-not $src) { $lines += ('GAMESTAT\{0} NOT written: GAMESTAT\{1} is missing' -f $pair[1], $pair[0]); continue }
        Write-Latin1 (Join-Path $gs $pair[1]) ((Read-Latin1 $src).Replace($pair[2], $pair[3]))
        $lines += ('wrote GAMESTAT\{0} (= {1} naming {2}; the 640x480 exe reads this copy)' -f $pair[1], $pair[0], $pair[3])
    }
    return $lines
}
# ozi @ 640x480: exp\intrface\bintoze (and ozi_ns\intrface\bintoze) = the stock Council Wars menu + OZI rows
function Write-StockOziMenu([string] $GameDir) {
    $src = Find-CI (Join-Path $GameDir 'exp\intrface') 'bintroe'
    if (-not $src) { return @('exp\intrface\bintoze NOT written: exp\intrface\bintroe is missing') }
    $t = Edit-OziMenu (Read-Latin1 $src)
    $lines = @()
    foreach ($dir in 'exp\intrface', 'ozi_ns\intrface') {
        $d = Join-Path $GameDir $dir
        if (Test-Path -LiteralPath $d) { Write-Latin1 (Join-Path $d 'bintoze') $t; $lines += ('wrote {0}\bintoze (stock menu + OZI MISSIONS / OZI LOAD rows; the 640x480 exe reads this copy)' -f $dir) }
    }
    return $lines
}

# Applies the chosen patches (canonical order) to the bytes of $OriginalPath and writes $OutputPath.
# Returns a small result object; throws on any check failure.
function Invoke-PatchRun([string] $OriginalPath, $Build, [object[]] $Chosen, [string] $OutputPath, [string] $Mode) {
    $data = [System.IO.File]::ReadAllBytes($OriginalPath)
    $effective = @(Get-BuildPatches $Build $Mode)
    $ordered = @($effective | Where-Object { $p = $_; ($Chosen | Where-Object { $_.Id -eq $p.Id -and $_.Mode -eq $p.Mode }) })
    $result = $data
    foreach ($p in $ordered) { $result = Invoke-Patch $result $p }
    [System.IO.File]::WriteAllBytes($OutputPath, $result)
    $outSha = Get-Sha256Hex $result
    $ref = if ($Mode) { $Build.ReferenceSha256[$Mode] } else { $Build.PatchedSha256 }
    # an HD display fix was applied: build the INTRF_HD interface set for the chosen size (scripts,
    # briefing lists, letterboxed backgrounds, loading screens; the Council Wars and OZI copies too)
    $generated = @()
    if ($Mode -and $Mode -ne '640x480' -and ($ordered | Where-Object { $_.ContainsKey('SetSources') })) {
        $movies = [bool] ($ordered | Where-Object { $_.Id -eq 'movies' })
        try {
            $generated = @(Write-InterfaceSet (Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))) $Mode $movies)
        } catch {
            $generated = @('INTERFACE SET NOT WRITTEN: ' + $_.Exception.Message)
        }
    }
    if ($Mode -eq '640x480') {
        $dir = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
        if ($ordered | Where-Object { $_.Id -eq 'movies' }) { try { $generated += Write-StockEndingLists $dir } catch { $generated += 'GAMESTAT lists NOT written: ' + $_.Exception.Message } }
        if ($ordered | Where-Object { $_.Id -eq 'ozi' })    { try { $generated += Write-StockOziMenu $dir } catch { $generated += 'bintoze NOT written: ' + $_.Exception.Message } }
    }
    return @{
        Generated = $generated
        Applied   = $ordered
        Sha256    = $outSha
        Size      = $result.Length
        Mode      = $Mode
        Complete  = ($ordered.Count -eq $effective.Count)
        Matches   = ($outSha -eq $ref)                      # = the reference build for this resolution
        Published = ($outSha -eq $Build.PatchedSha256)      # = the exe in the repository
    }
}

# The safeguard: before anything is written, every chosen fix must have (a) the fixes it depends on
# chosen as well and (b) every data file it needs present under $GameDir (the folder the patched
# exe will run from = where it is written).  Returns text lines describing the problems; empty = ok.
# Without this an exe patched for 1024x768 in a folder without INTRF_HD/ fails at start-up or draws
# the menus into the top-left corner, and the player would blame the patch.
function Get-DataProblems($Build, [object[]] $Chosen, [string] $GameDir, [string] $Mode) {
    $problems = @()
    $chosenIds = @($Chosen | ForEach-Object { $_.Id })
    $effective = @(Get-BuildPatches $Build $Mode)
    foreach ($p in $Chosen) {
        foreach ($need in @($p.Requires)) {
            if ($chosenIds -notcontains $need) {
                $other = @($effective | Where-Object { $_.Id -eq $need })
                $otherName = if ($other.Count -gt 0) { $other[0].Name } else { $need }
                $problems += ("fix '{0}' ({1}) only works together with fix '{2}' ({3}) - select both or neither" -f $p.Id, $p.Name, $need, $otherName)
            }
        }
        $missing = @()
        foreach ($rel in @($p.Data)) { if (-not (Test-Path -LiteralPath (Join-Path $GameDir $rel))) { $missing += $rel } }
        if ($missing.Count -gt 0) {
            $total = 0; foreach ($d in @($p.Data)) { $total++ }
            $shown = @($missing | Select-Object -First 8) -join ', '
            if ($missing.Count -gt 8) { $shown += (', ... ({0} more)' -f ($missing.Count - 8)) }
            $problems += ("fix '{0}' ({1}) needs {2} data files under '{3}', {4} are missing: {5}. Copy the game folder from the repository " +
                          "(https://github.com/endotermic/Dark-Colony) or write the exe into the game folder there.") -f $p.Id, $p.Name, $total, $GameDir, $missing.Count, $shown
        }
    }
    return $problems
}

# Which fixes of a build cannot be applied into $GameDir: their resources (Data files) are not there,
# or a fix they require is itself unavailable.  Returns a hashtable id -> one-line reason (empty = all
# available).  The window greys these out as "RESOURCES NOT FOUND", -All skips them.
function Get-UnavailableFixes($Build, [string] $GameDir, [string] $Mode) {
    $out = @{}
    if (-not $GameDir) { return $out }
    $effective = @(Get-BuildPatches $Build $Mode)
    foreach ($p in $effective) {
        $missing = @(); $total = 0
        foreach ($rel in @($p.Data)) { $total++; if (-not (Test-Path -LiteralPath (Join-Path $GameDir $rel))) { $missing += $rel } }
        if ($missing.Count -gt 0) {
            $tops = @{}
            foreach ($m in $missing) { $top = ($m -split '\\')[0]; if ($tops.ContainsKey($top)) { $tops[$top]++ } else { $tops[$top] = 1 } }
            $where = @($tops.Keys | Sort-Object | ForEach-Object { '{0}\ ({1})' -f $_, $tops[$_] }) -join ', '
            $out[$p.Id] = ('{0} of {1} resource files missing: {2}' -f $missing.Count, $total, $where)
        }
    }
    # a fix that needs an unavailable fix is unavailable too (repeat until nothing changes: resolution <-> hdpaths are mutual)
    do {
        $changed = $false
        foreach ($p in $effective) {
            if ($out.ContainsKey($p.Id)) { continue }
            foreach ($need in @($p.Requires)) {
                if ($out.ContainsKey($need)) { $out[$p.Id] = ("needs fix '{0}', which is unavailable here" -f $need); $changed = $true; break }
                if (-not ($effective | Where-Object { $_.Id -eq $need })) { $out[$p.Id] = ("needs fix '{0}', which does not exist at this resolution" -f $need); $changed = $true; break }
            }
        }
    } while ($changed)
    return $out
}

# One-line summary of what a fix needs, for -List and the window.
function Get-RequirementLines($Build, $Patch) {
    $lines = @()
    $req = @($Patch.Requires)
    if ($req.Count -gt 0) { $lines += ('needs fix(es) ' + ($req -join ', ') + ' selected as well') }
    $n = 0; $tops = @{}
    foreach ($d in @($Patch.Data)) { $n++; $top = ($d -split '\\')[0]; if ($tops.ContainsKey($top)) { $tops[$top]++ } else { $tops[$top] = 1 } }
    if ($n -gt 0) {
        $parts = @($tops.Keys | Sort-Object | ForEach-Object { '{0}\ ({1})' -f $_, $tops[$_] })
        $lines += ('needs {0} data files next to the exe: {1} - checked before writing' -f $n, ($parts -join ', '))
    }
    if ($Patch.ContainsKey('SetSources')) {
        $lines += 'writes the INTRF_HD interface set for this resolution (scripts, briefing lists, letterboxed backgrounds, loading screens; exp\intrf_hd and ozi_ns\intrf_hd too) from the stock files and the three shipped pictures - the GIF codec is C# source in this file, compiled by Add-Type (see the INTERFACE SET section)'
    }
    return $lines
}

function Write-PatchList([switch] $WithEdits) {
    foreach ($b in $Builds) {
        Write-Host ''
        Write-Host ("=== {0}: {1}" -f $b.Id, $b.Title) -ForegroundColor Cyan
        Write-Host ("    original {0} ({1} bytes)  SHA-256 {2}" -f $b.OriginalName, $b.Size, $b.OriginalSha256)
        Write-Host ("    all patches -> {0}         SHA-256 {1}" -f $b.OutputName, $b.PatchedSha256)
        if (@($b.Modes).Count -gt 0) {
            Write-Host ("    resolutions: {0} (default {1}); reference SHA-256 with every fix of that resolution:" -f (($b.Modes | ForEach-Object { Format-ModeLabel $_ (Get-MonitorSize) }) -join ', '), $b.DefaultMode)
            foreach ($m in $b.Modes) { Write-Host ("      {0,-10} {1}" -f $m, $b.ReferenceSha256[$m]) }
        }
        $n = 0
        foreach ($p in $b.Patches) {
            $n++
            Write-Host ''
            $modeTag = if ($p.Mode -and $p.Mode -ne 'hd') { ' @ ' + $p.Mode } elseif ($p.Mode -eq 'hd') { ' @ every resolution but 640x480' } else { '' }
            Write-Host ("  {0}. [{1}{2}] {3}  ({4}, {5} edits)" -f $n, $p.Id, $modeTag, $p.Name, $p.Date, (Get-EditCount $p)) -ForegroundColor Yellow
            foreach ($line in ($p.Description -split "`r?`n")) { Write-Host ("       " + $line) }
            foreach ($line in (Get-RequirementLines $b $p)) { Write-Host ("       * " + $line) -ForegroundColor Magenta }
            if ($WithEdits) {
                foreach ($line in (Get-EditLines $p)) { Write-Host ("       " + $line) -ForegroundColor DarkGray }
                foreach ($d in @($p.Data)) { Write-Host ("       data  " + $d) -ForegroundColor DarkGray }
            }
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

    $script:gui = @{ Path = $null; Data = $null; Build = $null; IsOriginal = $false; Syncing = $false; Unavailable = @{}
                     Mode = ''; ModeList = @(); Patches = @(); Monitor = (Get-MonitorSize) }
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
    $lblStatus.Text = 'Pick dc16.exe (Dark Colony) or ENGEXP16.EXE (Council Wars) from the "DC - Council wars" folder, or maped.exe from "Dark Colony - Map editor" - all three are in the repository, untouched.'

    # --- left: the fixes
    $grpFix = New-Object System.Windows.Forms.GroupBox
    $grpFix.Text = 'Fixes to apply (always applied in this order)'; $grpFix.Location = '12,82'; $grpFix.Size = '450,470'
    $grpFix.Anchor = 'Top,Bottom,Left'
    # the resolution drop-down: "WIDTHxHEIGHT (aspect) recommended" - recommended = your monitor's aspect ratio
    $lblRes = New-Object System.Windows.Forms.Label
    $lblRes.Text = 'Screen resolution:'; $lblRes.Location = '12,25'; $lblRes.AutoSize = $true
    $cmbRes = New-Object System.Windows.Forms.ComboBox
    $cmbRes.Location = '130,21'; $cmbRes.Size = '300,23'; $cmbRes.DropDownStyle = 'DropDownList'; $cmbRes.Enabled = $false
    $chkAll = New-Object System.Windows.Forms.CheckBox
    $chkAll.Text = 'Select all fixes  (result = the reference build for the chosen resolution)'
    $chkAll.Location = '12,52'; $chkAll.AutoSize = $true; $chkAll.Enabled = $false
    $chkAll.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
    $lst = New-Object System.Windows.Forms.CheckedListBox
    $lst.Location = '12,80'; $lst.Size = '426,378'; $lst.Anchor = 'Top,Bottom,Left,Right'
    $lst.CheckOnClick = $true; $lst.IntegralHeight = $false; $lst.Enabled = $false
    $lst.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $grpFix.Controls.AddRange(@($lblRes, $cmbRes, $chkAll, $lst))

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
    $script:gui.Controls = @{ Form = $form; In = $txtIn; Status = $lblStatus; All = $chkAll; List = $lst; Info = $txtInfo; Out = $txtOut; Apply = $btnApply; Log = $lblLog; Browse = $btnBrowse; OutBtn = $btnOut; Verify = $btnVerify; Res = $cmbRes }
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
            $c.Status.Text = "Not a build this script knows ($($data.Length) bytes). Use dc16.exe or ENGEXP16.EXE from the repository's DC - Council wars folder, or maped.exe from Dark Colony - Map editor."
            $c.List.Enabled = $false; $c.All.Enabled = $false; $c.Apply.Enabled = $false; $c.Res.Enabled = $false
            return
        }
        $g.Path = $path; $g.Data = $data; $g.Build = $build; $g.IsOriginal = $isOriginal
        # the resolutions of this build; the default one preselected
        $g.Syncing = $true
        $c.Res.Items.Clear(); $g.ModeList = @($build.Modes)
        foreach ($m in $g.ModeList) { [void] $c.Res.Items.Add((Format-ModeLabel $m $g.Monitor)) }
        $c.Res.Enabled = ($g.ModeList.Count -gt 0)
        if ($g.ModeList.Count -gt 0) { $c.Res.SelectedIndex = [Math]::Max(0, [Array]::IndexOf($g.ModeList, (Get-PreferredMode $build $g.Monitor))) }
        $g.Syncing = $false
        if ($isOriginal) {
            $c.Status.ForeColor = 'DarkGreen'
            $c.Status.Text = "$($build.Title)`r`nSHA-256 $sha = the untouched original."
        } else {
            $c.Status.ForeColor = 'DarkOrange'
            $c.Status.Text = "$($build.Title)`r`nSHA-256 does not match the untouched original (already patched, or another copy). Every byte is still checked before it is written."
        }
        $c.List.Enabled = $true; $c.All.Enabled = $true; $c.Apply.Enabled = $true
        $c.Out.Text = Join-Path (Split-Path $path) $build.OutputName   # TextChanged -> Refresh (the list is not filled yet: no-op)
        & $script:gui.FillList
        $c.Log.Text = ''
    }
    $script:gui.Load = $loadOriginal

    # (Re)fills the fix list for the resolution chosen in the drop-down: the fixes of every resolution
    # plus the chosen resolution's variants of 'resolution' and 'clock' (none for 640x480).
    $script:gui.FillList = {
        $c = $script:gui.Controls
        $g = $script:gui
        if (-not $g.Build) { return }
        $g.Mode = if ($g.ModeList.Count -gt 0 -and $c.Res.SelectedIndex -ge 0) { $g.ModeList[$c.Res.SelectedIndex] } else { '' }
        $g.Patches = @(Get-BuildPatches $g.Build $g.Mode)
        $g.Syncing = $true
        $c.List.Items.Clear(); $c.All.Checked = $false
        foreach ($p in $g.Patches) { [void] $c.List.Items.Add(('{0}   ({1})' -f $p.Name, $p.Date), $false) }
        $g.Syncing = $false
        & $script:gui.Refresh
        if ($c.List.Items.Count -gt 0) { $c.List.SelectedIndex = 0 }
        if ($g.Mode -eq '640x480') { $c.Log.ForeColor = 'Black'; $c.Log.Text = '640x480 = the stock screen size: the display fixes (resolution, INTRF_HD paths, clock) are not offered.' }
    }
    $c.Res.Add_SelectedIndexChanged({ if (-not $script:gui.Syncing) { & $script:gui.FillList } })

    # Re-checks the resources of every fix against the folder the exe will be written to: fixes whose
    # files are missing (or which need such a fix) get "RESOURCES NOT FOUND" in their label, are
    # unticked and cannot be ticked.  Runs at load and whenever the output path changes.
    $script:gui.Refresh = {
        $c = $script:gui.Controls
        $g = $script:gui
        if (-not $g.Build) { return }
        $dir = $null
        try { $t = $c.Out.Text.Trim(); if ($t) { $dir = Split-Path -Parent ([System.IO.Path]::GetFullPath($t)) } } catch { $dir = $null }
        $g.Unavailable = Get-UnavailableFixes $g.Build $dir $g.Mode
        $g.Syncing = $true
        for ($i = 0; $i -lt $c.List.Items.Count; $i++) {
            $p = $g.Patches[$i]
            $was = $c.List.GetItemChecked($i)
            $text = '{0}   ({1})' -f $p.Name, $p.Date
            if ($g.Unavailable.ContainsKey($p.Id)) { $text = '[RESOURCES NOT FOUND]  ' + $text; $was = $false }
            $c.List.Items[$i] = $text
            $c.List.SetItemChecked($i, $was)
        }
        $g.Syncing = $false
        $n = 0; foreach ($k in $g.Unavailable.Keys) { $n++ }
        if ($n -gt 0) {
            $c.Log.ForeColor = 'DarkOrange'
            $c.Log.Text = "$n fix(es) cannot be applied into this folder - resources not found (see the label; click the fix for details)."
        }
    }
    $c.Out.Add_TextChanged({ & $script:gui.Refresh })

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

    # "Select all" <-> individual boxes, without the two events feeding each other; fixes whose
    # resources are not found stay unticked in both directions
    $c.All.Add_CheckedChanged({
        $c = $script:gui.Controls
        $g = $script:gui
        if ($g.Syncing) { return }
        $g.Syncing = $true
        for ($i = 0; $i -lt $c.List.Items.Count; $i++) {
            $avail = -not $g.Unavailable.ContainsKey($g.Patches[$i].Id)
            $c.List.SetItemChecked($i, ($c.All.Checked -and $avail))
        }
        $g.Syncing = $false
    })
    $c.List.Add_ItemCheck({
        param($sender, $e)
        $c = $script:gui.Controls
        $g = $script:gui
        if ($g.Syncing) { return }
        if ($e.NewValue -eq 'Checked' -and $g.Unavailable.ContainsKey($g.Patches[$e.Index].Id)) {
            $e.NewValue = 'Unchecked'   # cannot be ticked: resources not found
            $c.Log.ForeColor = 'Firebrick'
            $c.Log.Text = 'Resources not found for this fix in the output folder: ' + $g.Unavailable[$g.Patches[$e.Index].Id]
        }
        # "Select all" mirrors "every available fix is ticked"
        $all = $true
        for ($i = 0; $i -lt $c.List.Items.Count; $i++) {
            if ($g.Unavailable.ContainsKey($g.Patches[$i].Id)) { continue }
            $checked = if ($i -eq $e.Index) { $e.NewValue -eq 'Checked' } else { $c.List.GetItemChecked($i) }
            if (-not $checked) { $all = $false }
        }
        $g.Syncing = $true; $c.All.Checked = $all; $g.Syncing = $false
    })

    $c.List.Add_SelectedIndexChanged({
        $c = $script:gui.Controls
        $g = $script:gui
        if (-not $g.Build -or $c.List.SelectedIndex -lt 0 -or $c.List.SelectedIndex -ge $g.Patches.Count) { return }
        $p = $g.Patches[$c.List.SelectedIndex]
        $lines = @()
        if ($g.Unavailable.ContainsKey($p.Id)) {
            $lines += @('RESOURCES NOT FOUND - this fix cannot be applied into the output folder:', ('  ' + $g.Unavailable[$p.Id]),
                        '  Copy the game folder from the repository (https://github.com/endotermic/Dark-Colony), or write', '  the exe into it.', '')
        }
        $lines += @(
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
        $reqLines = @(Get-RequirementLines $g.Build $p)
        if ($reqLines.Count -gt 0) {
            $lines += @('', 'Prerequisites (checked before anything is written):')
            foreach ($l in $reqLines) { $lines += ('  * ' + $l) }
        }
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
        for ($i = 0; $i -lt $c.List.Items.Count; $i++) { if ($c.List.GetItemChecked($i)) { $chosen += $g.Patches[$i] } }
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
        $problems = @(Get-DataProblems $g.Build $chosen (Split-Path -Parent ([System.IO.Path]::GetFullPath($outPath))) $g.Mode)
        if ($problems.Count -gt 0) {
            $c.Log.ForeColor = 'Firebrick'; $c.Log.Text = 'Nothing written: data files or dependent fixes are missing (see the message).'
            [System.Windows.Forms.MessageBox]::Show($c.Form, (($problems | ForEach-Object { '* ' + $_ }) -join "`r`n`r`n") +
                "`r`n`r`nAn exe written without them fails at start-up or draws garbage, which would look like a bug of the fix. " +
                "Write the exe into the game folder from the repository, or run the script from the command line with -IgnoreMissingData.",
                'Prerequisites missing - nothing written', 'OK', 'Warning') | Out-Null
            return $null
        }
        try {
            $r = Invoke-PatchRun $g.Path $g.Build $chosen $outPath $g.Mode
        } catch {
            $c.Log.ForeColor = 'Firebrick'; $c.Log.Text = 'Nothing written.'
            [System.Windows.Forms.MessageBox]::Show($c.Form, $_.Exception.Message, 'Check failed - nothing written', 'OK', 'Error') | Out-Null
            return $null
        }
        $ids = ($r.Applied | ForEach-Object { $_.Id }) -join ', '
        $modeText = if ($r.Mode) { " for $($r.Mode)" } else { '' }
        if ($r.Generated.Count -gt 0) { $modeText += ' (' + ($r.Generated -join '; ') + ')' }
        if ($r.Complete -and $r.Published) {
            $c.Log.ForeColor = 'DarkGreen'
            $c.Log.Text = "Written: $($r.Size) bytes, all $($r.Applied.Count) fixes$modeText.`r`nSHA-256 $($r.Sha256) = byte-identical to the exe published in the repository."
        } elseif ($r.Complete -and $r.Matches) {
            $c.Log.ForeColor = 'DarkGreen'
            $c.Log.Text = "Written: $($r.Size) bytes, all $($r.Applied.Count) fixes$modeText.`r`nSHA-256 $($r.Sha256) = byte-identical to the reference build for $($r.Mode)."
        } elseif ($r.Complete) {
            $c.Log.ForeColor = 'Firebrick'
            $c.Log.Text = "Written, but the SHA-256 differs from the reference build$modeText - please report this.`r`n$($r.Sha256)"
        } else {
            $c.Log.ForeColor = 'Black'
            $c.Log.Text = "Written: $($r.Size) bytes with $($r.Applied.Count) of $($g.Patches.Count) fixes$modeText ($ids).`r`nSHA-256 $($r.Sha256)"
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
    if (-not $build) { throw "This is not one of the three known original executables (size / layout mismatch)." }
    if (-not $Force) {
        throw ("The SHA-256 is not that of the untouched {0} original. Start from {1} (in the repository), " +
               "or pass -Force to rely on the per-byte checks alone.") -f $build.Id, $build.OriginalName
    }
    Write-Warning "SHA-256 does not match the untouched original; continuing because -Force was given (every edit is still byte-checked)."
}
Write-Host ("build : {0}" -f $build.Title)
$mode = Resolve-Mode $build $Resolution
if ($mode) { Write-Host ("screen: {0}" -f (Format-ModeLabel $mode (Get-MonitorSize))) }

$available = @(Get-BuildPatches $build $mode)
if (-not $Output) { $Output = Join-Path (Split-Path $origPath) $build.OutputName }
$gameDir = Split-Path -Parent ([System.IO.Path]::GetFullPath($Output))
$unavailable = Get-UnavailableFixes $build $gameDir $mode
if ($All) {
    # every fix whose resources are in the target folder; the others are skipped and reported
    $chosen = @()
    foreach ($p in $available) {
        if ($unavailable.ContainsKey($p.Id) -and -not $IgnoreMissingData) {
            Write-Host ("skipping [{0,-10}] {1,-45} RESOURCES NOT FOUND: {2}" -f $p.Id, $p.Name, $unavailable[$p.Id]) -ForegroundColor DarkYellow
        } else {
            $chosen += $p
        }
    }
    if ($chosen.Count -eq 0) { throw 'nothing to apply: no fix has its resources in the target folder' }
} else {
    # accept -Patches a,b,c from a PowerShell prompt (array) as well as "a,b,c" / "a b c" from cmd / -File (one string)
    $chosen = @()
    foreach ($id in @($Patches | ForEach-Object { $_ -split '[\s,]+' } | Where-Object { $_ })) {
        $p = $available | Where-Object { $_.Id -eq $id }
        if (-not $p) { throw "unknown patch id '$id' for $($build.Id); valid: $(($available | ForEach-Object { $_.Id }) -join ', ')" }
        $chosen += $p
    }
}
if ((Test-Path $Output) -and -not $Overwrite) { throw "output '$Output' exists; pass -Overwrite to replace it" }
if ((Test-Path $Output) -and ((Resolve-Path $Output).Path -eq $origPath)) { throw 'refusing to overwrite the original' }

$problems = @(Get-DataProblems $build $chosen $gameDir $mode)
if ($problems.Count -gt 0) {
    foreach ($pr in $problems) { Write-Warning $pr }
    if (-not $IgnoreMissingData) {
        throw ("nothing written: the chosen fixes need data files or other fixes that are not there (see the warnings above). " +
               "An exe written anyway fails at start-up or draws garbage. Write it into the game folder from the repository, " +
               "or pass -IgnoreMissingData if you know what you are doing.")
    }
    Write-Warning 'continuing because -IgnoreMissingData was given.'
}
Write-Host ''
foreach ($p in @($available | Where-Object { $p = $_; ($chosen | Where-Object { $_.Id -eq $p.Id }) })) {
    Write-Host ("applying [{0,-10}] {1,-52} {2,3} edits" -f $p.Id, $p.Name, (Get-EditCount $p))
}
$r = Invoke-PatchRun $origPath $build $chosen $Output $mode
Write-Host ''
Write-Host ("output: {0}" -f $Output)
Write-Host ("        {0} bytes, SHA-256 {1}" -f $r.Size, $r.Sha256)
foreach ($gl in @($r.Generated)) { Write-Host ("        " + $gl) }
if ($r.Complete) {
    if ($r.Published) { Write-Host '        byte-identical to the executable published in the repository.' -ForegroundColor Green }
    elseif ($r.Matches) { Write-Host ("        byte-identical to the reference build for {0} (every fix of that resolution)." -f $mode) -ForegroundColor Green }
    else { Write-Warning 'all patches applied but the SHA-256 differs from the reference build - report this.' }
} else {
    Write-Host ("        {0} of {1} patches applied ({2}); a partial build has no published reference hash." -f $r.Applied.Count, $available.Count, (($r.Applied | ForEach-Object { $_.Id }) -join ', '))
    $skipped = @($available | Where-Object { $p = $_; -not ($r.Applied | Where-Object { $_.Id -eq $p.Id }) -and $unavailable.ContainsKey($p.Id) } | ForEach-Object { $_.Id })
    if ($skipped.Count -gt 0) { Write-Host ("        not applied, resources not found: {0}" -f ($skipped -join ', ')) -ForegroundColor DarkYellow }
}
''')

open(OUT, 'w', encoding='utf-8', newline='\r\n').write('\n'.join(out))
shutil.rmtree(WORK, ignore_errors=True)
print('wrote', OUT, os.path.getsize(OUT), 'bytes')

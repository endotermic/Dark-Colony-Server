"""Generate Apply-DarkColonyPatches.ps1 - the self-documenting PowerShell patcher that lives in the ROOT of
the Dark-Colony game repository (next to "DC - Classic" and "DC - Council wars", maintainer decision 14 Sep 2026).

The PowerShell script lets a player rebuild the patched "Dark Colony.exe" / "Dark Colony Ultimate.exe" (until
25 Sep 2026 dc16new.exe / engexp16new.exe) and the map editor from the
untouched originals committed in the Dark-Colony repository, one patch at a time, with every changed
byte listed and explained.  This generator produces it by *replaying* the Python patch tools of this
folder on copies of the originals, diffing after each step (so every byte is attributed to
exactly one patch) and taking the per-edit descriptions from the tools' `plan` output.

    python tools/gen_apply_script.py "<path to Dark-Colony>" "<path to Dark-Colony>/Apply-DarkColonyPatches.ps1"

Needs: DC - Council wars/dc16.exe (the untouched Classic build of 7 Jan 1998; since 15 Sep 2026 both
games live in that one folder, the Classic original keeps its stock name and the patched result is
"Dark Colony.exe", until 25 Sep 2026 dc16new.exe), DC - Council wars/ENGEXP16.EXE, Dark Colony - Map
editor/maped.exe and DC - Council wars/DC_HD.ICO (the icon of fix `icon`) in the game repository, and the
patch_*.py tools beside this file.  The generated script is validated here:
the sum of the per-patch edits must reproduce every intermediate exe, and the end result is
hashed into the script as the reference for "all patches applied".  Re-run after adding a patch
(add it to PATCHES/BUILDS below, with a block parser for its plan output).  The OZI patch is
kept last among the fixes that edit in place because its 16-byte .reloc insert shifts every later
relocation entry; since 25 Sep 2026 the icon fix follows it in every build, because it APPENDS a
section and so grows the file (emitted as an `Append` edit with the bytes in Base64).  The CD fix is
ONE patch, `nocd` (patch_nocd.py; maintainer requirement 18 Sep 2026): it carries the three
hand-patched 2025 bytes (formerly `cdcheck`) and the removal of the whole CD path; it goes first,
and patch_resolution.py accepts the resulting exe by size (its MD5 table only knows the 2025 state).
"""
import re, struct, hashlib, sys, os, shutil, subprocess, tempfile, base64

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
HD_MODES = ['1024x768', '1280x1024', '1280x720', '1280x800', '3840x1080']  # one size per aspect ratio (4:3 keeps the stock 640x480 too); maintainer rule of 22 Sep 2026
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
    files += ['dc\\intrf_hd\\bintroe']      # the DARK COLONY mode's overlay: the patched menu, nothing else
    if mode == STOCK_MODE:
        files += ['exp\\intrface\\bintroe']                              # source of the bintoze copies
    return files


# the icon every patched exe gets (fix `icon`, 25 Sep 2026): made by make_dc_icon.py from DC.ICO's geometry
ICON_FILE = os.path.join(GAME, 'DC - Council wars', 'DC_HD.ICO')

TOOL_OF = {'nocd': 'patch_nocd.py',
           'resolution': 'patch_resolution.py', 'hdpaths': 'patch_hd_paths.py', 'cursor': 'patch_cursor.py',
           'pool': 'patch_pool.py', 'clock': 'patch_clock.py',
           'ddraw': 'patch_ddraw_lost.py', 'camera': 'patch_camera.py', 'restore': 'patch_restore.py',
           'longpath': 'patch_longpath.py', 'music': 'patch_music.py', 'widemap': 'patch_widemap.py',
           'movies': 'patch_movies.py', 'sounds': 'patch_wavprefix.py', 'ozi': 'patch_ozi_menu.py',
           # map editor: one tool, one fix id per step (the plan is taken once with --fix all)
           'blocksets': ('patch_maped.py', ['--fix', 'blocksets']), 'teams': ('patch_maped.py', ['--fix', 'teams']),
           'healer': ('patch_maped.py', ['--fix', 'healer']), 'troopsframe': ('patch_maped.py', ['--fix', 'troopsframe']),
           # the high-resolution icon, last in every build (it appends a section); the .ico is in the game folder
           'icon': ('patch_icon.py', ['--ico', ICON_FILE])}
PLAN_OF = {'nocd': 'nocd',
           'resolution': 'resolution', 'hdpaths': 'hd_paths', 'cursor': 'cursor', 'pool': 'pool',
           'clock': 'clock', 'ddraw': 'ddraw_lost', 'camera': 'camera', 'restore': 'restore', 'longpath': 'longpath', 'widemap': 'widemap',
           'music': 'music',
           'movies': 'movies', 'sounds': 'wavprefix', 'ozi': 'ozi_menu',
           'blocksets': 'maped', 'teams': 'maped', 'healer': 'maped', 'troopsframe': 'maped',
           'icon': 'icon'}
PLAN_ARGS = {'maped': ['--fix', 'all'], 'icon': ['--ico', ICON_FILE]}      # plan-time arguments per plan name (default: none)
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

def blocks_longpath(g):
    t = plan(g, 'longpath'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2});(.*)$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3)) and len(old) in (20, 14, 22, 4)
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ':' + m.group(6).rstrip(), old, new))
    assert len(out) == 4, (g, len(out))                                   # two open sites, the open_read stub, the error-exit operand
    return out

def blocks_widemap(g):
    t = plan(g, 'widemap'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2});(.*)$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3)) and len(old) in (207, 5, 3, 6, 7, 12, 50, 48, 14), (g, len(old))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ':' + m.group(6).rstrip(), old, new))
    assert len(out) == 12, (g, len(out))                                  # body, bounds call, 6 drawer edits, lightmap, clip call, ambience, spot order
    out += reloc_lines(t, '.reloc table: ')
    assert len(out) == 26, (g, len(out))                                  # + the 14 re-pointed entries of the dead body
    return out

def blocks_music(g):
    t = plan(g, 'music'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+file 0x([0-9a-f]+) VA 0x[0-9a-f]+ (\d+) bytes\s*$', t, re.M):
        out.append((int(m.group(2), 16), int(m.group(3)), m.group(1).strip()))
    assert [n for _, n, _ in out] == [0x751, 0x6E], (g, out)             # the rewritten cdaudio module + the aux volume walk
    out += reloc_lines(t, '.reloc table: ')
    assert len(out) == 2 + 44, (g, len(out))                              # 24 re-pointed + 20 neutralised entries
    return out

def music_data(g, mode=None):
    """The MP3 tracks of the `music` fix: Dark Colony's four under MUSIC\\, Council Wars' four under
    exp\\music\\ (both games share one folder and the two discs differ)."""
    files = _tree(g, 'MUSIC', pattern=r'^track0[2-9]\.mp3$') if g == 'classic' else _tree(g, 'exp', 'music', pattern=r'^track0[2-9]\.mp3$')
    assert len(files) == 4, (g, files)
    for f in files:
        assert os.path.exists(os.path.join(GAME_DIR[g], f.replace('\\', os.sep))), f
    return files

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

def blocks_icon(g):
    """The four header edits of patch_icon.py (the appended section is taken from the replay, see attribute())."""
    t = plan(g, 'icon'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2})\s*$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip(), old, new))
    assert len(out) == 4, (g, len(out))
    m = re.search(r'^\s+append at file 0x([0-9a-f]+) (\d+) bytes sha256 ([0-9a-f]{64}): (.+)$', t, re.M)
    assert m, t
    ICON_APPEND[g] = dict(at=int(m.group(1), 16), n=int(m.group(2)), sha=m.group(3), note=m.group(4).strip())
    return out

ICON_APPEND = {}

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
 dict(id='widemap', name='Maps narrower than the screen: the view is centred on the map and every map read stays inside it', date='22 Sep 2026',
      tool='tools/patch_widemap.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.32', blocks=blocks_widemap,
      requires=['nocd', 'camera'],
      desc='''The battlefield view is the screen minus the panel, in whole 32-pixel tiles: 28 tiles across at
1024x768, 36 at 1280x800, 76 at 2560x1440, 116 at 3840x1080.  The maps are 64 to 160 tiles wide
(the training maps and two two-player maps 64, the first two campaign missions and 22 two-player
maps 96).  Once the view is wider than the map the game's camera limits ("half a screen from every
map edge") contradict each other and the camera settles at the far one, so the view begins left of
the map; nothing that then reads the map checks for that: the terrain drawer continues into the
neighbouring rows (the far side of the map drawn shifted by a row) and, at the top and bottom map
row, past the tile block into memory whose contents it takes for tile numbers - a crash in the tile
blitter as soon as the camera reaches those rows; the lighting pass reads before and after each row;
the visibility scan tests tiles of the wrong row; the ambient sounds fall silent (their picker gives
up when the visible rectangle starts left of the map); a click on the black margin sends units to
the far side of the map (the position wraps in a 16-bit field).  The fix, in six parts written into
the dead body of the CD-probe routine (unused since the "No CD" fix, which is therefore required,
as is the "Camera clamped" fix whose stub the first part chains into): (1) when the limits
contradict each other, both become the map centre, so the map sits centred in the view and cannot
scroll sideways; (2) the terrain drawer clamps every column to the map, so the margin repeats the
edge tiles instead of reading beyond them; (3) the lighting pass clamps rows and columns the same
way (in place of its four edge cases); (4) the visibility rectangle is cut to the map; (5) the
ambient-sound picker clamps its rectangle instead of giving up; (6) a spot order's x is clamped to
the map.  The 14 relocation entries of the dead routine are re-pointed to the new absolute operands
or neutralised.  Rows are never affected with the shipped maps (the tallest view, 44 rows at
5120x1440, is shorter than the smallest map, 56 rows), so only the column direction is handled.
Without a wide screen the fix changes nothing visible: every map is wider than 28 or 36 tiles.'''),
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
 dict(id='longpath', name='Sound files load from any folder depth: the wave loader no longer uses the 128-character OpenFile', date='22 Sep 2026',
      tool='tools/patch_longpath.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.29', blocks=blocks_longpath,
      requires=['nocd'],
      desc='''Installed in a deep folder (about 128 characters of path and more, e.g. a repository ZIP
extracted under Downloads and then moved into a sub-folder), the game shows "FILE NOT FOUND /
A sound file is missing - see error.log" about five seconds after start and exits; error.log
holds the line "unable to open file" with no name after it.  The same files run fine from a
short path.  Cause: the routine that loads every WAV (sound banks, briefings, ambience) is the
only code in the game that opens files through the Windows 3.1-era OpenFile function, which
writes the full path into a 128-character field and fails outright when it does not fit.  All
other loaders use the C runtime (CreateFileA underneath) and have no such limit, so the rest
of the game runs and only the first sound kills it.  The empty name is a leftover of the
"No CD" fix: the error message printed the buffer of the skipped CD attempt.  The fix replaces
the two live OpenFile calls with calls to a 22-byte routine written over the first CD attempt
(dead code since the "No CD" fix, which is therefore required): CreateFileA(name, GENERIC_READ,
FILE_SHARE_READ, OPEN_EXISTING) - it returns -1 on failure exactly like OpenFile, and its
handle is what the loader's seek, read and close calls take.  The error message now names the
file that was tried.  Register-relative operands and a call through the import thunk only;
nothing moves, no relocation entry changes; the same four edits at +0x60 in Council Wars.
Verified 22 Sep 2026: from a 161-character game folder path the unfixed Council Wars exe fails
at 5 s, the fixed one plays on with an empty error.log.'''),
 dict(id='music', name='Original CD soundtrack from MP3 files (MUSIC\\TRACK02-05.MP3 / exp\\music\\track02-05.mp3)', date='22 Sep 2026',
      tool='tools/patch_music.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.31', blocks=blocks_music, data=music_data,
      desc='''The soundtrack of both games was never a file: the CDs are mixed-mode discs with the music as
audio tracks 2-5 after the data track, and the game plays them through Windows' CD-audio
interface (MCI "cdaudio") - at the start of every battle it seeks to track 2 and plays the disc to
its end, checks every five seconds whether the disc has stopped and then starts over at track 2,
and stops the disc when the battle ends.  Without a CD-ROM drive that interface fails at start-up
and the game is silent for good; the music slider of the options screen sets a "CD line" volume
that modern sound drivers no longer have.

This fix rewrites the CD-audio routines in place (the seven entry points the music code calls
keep their addresses) as an MP3 player on Windows' own MCI "mpegvideo" device (mciqtz32.dll,
part of every Windows since 98; the exe imports nothing new): at battle start it opens and plays
MUSIC\\TRACK02.MP3, the five-second check plays the next file when one has ended and TRACK02
again after the last one - the original "whole disc, repeat" - and the music slider now sets the
volume of the playing file (the saved level is applied to every track).  Dark Colony reads
MUSIC\\TRACK0N.MP3, Council Wars exp\\music\\track0N.mp3, because both exes share one folder and
the two discs have different music.  Everything inside the two rewritten routines; the
relocation entries of the old code's absolute operands are re-pointed at the new ones and the
rest become padding; nothing moves.

REQUIRES the eight tracks from the repository (encoded from the CD images at 192 kbit/s, 32 MB):
MUSIC\\TRACK02.MP3 .. TRACK05.MP3 for Dark Colony, exp\\music\\track02.mp3 .. track05.mp3 for
Council Wars.  Without a TRACK02 file the game simply stays silent, as it does today.'''),
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
 dict(id='ozi', name='DARK COLONY and OZI MISSIONS menu modes (Council Wars only)', date='10 Sep 2026', tool='tools/patch_ozi_menu.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md sections 10.13 and 10.36', blocks=blocks_ozi, cw_only=True,
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
The same mechanism gives the expansion build the ORIGINAL Dark Colony campaign (23 Sep 2026,
doc 10.36).  The Council Wars executable is the same program as dc16.exe - the Classic campaign,
the training missions and the encyclopedia are all compiled in - and the Council Wars folder is
the complete Classic data set, so a fourth mode with a prefix that matches nothing ("dc/", which
holds only the patched menu script) makes every file a Classic campaign opens fall through to the
Classic data in the game root: the 106-type GAMESTAT/GAMESTAT.TXT, the briefings in MISSION/,
SCENARIO/HUMAN and ALIEN, INTRF_HD/HSCENE.TXT and GSCENE.TXT and the SAVE/ folder the Classic exe
itself uses.  Two buttons are added for it:
  * the menu's accepted-id filter (`cmp edx,5`) becomes `cmp edx,7`, which admits the button ids
    6 and 7 - the first free ids; the main-menu script moves the two LARGEBUTTON plates that used
    them to 19 and 20 and gives the new buttons the plates 21 and 22
  * the two handlers go into the 59 NOP bytes the old PLAY INTRO body left behind: DARK COLONY
    sets "campaign, not training" and enters the campaign runner through tramp_dc_campaign,
    LOAD DC GAME goes through tramp_dc_load, so it always lists the SAVE/ folder.  The stub and
    the two trampolines are 97 more bytes of the code section's zero tail (VA 0x47F340..0x47F3AA),
    and their four absolute slot addresses add four more entries to the .reloc insert
  * MULTI PLAYER WAR goes through a fourth trampoline, tramp_dc_net (25 Sep 2026; 10 bytes at
    VA 0x47F3B0, relative operands only): a network game always starts in the Dark Colony mode, so
    it reads the Classic balance tables from the game root like dc16.exe and the relay server do.
    The menu's mode is sticky, and after OZI MISSIONS a network game loaded the pack's tables and
    went out of sync against every other player.  For the same reason exp/animozi.dat no longer
    lists grrr.fin and troo.fin, the deploy poses of the Gray and Security Trooper sprites: Classic
    has neither, and a Gray commander's rally waited 28 ticks for that animation in Council Wars
    against 2 in Classic - a mixed network game went out of sync at the first Gray rally.  The
    commanders of all three campaigns now rally in their STAND pose, as in Dark Colony
  * at 640x480 only, the scrolling credits box is removed (main.c bintro's TTY create, 45 bytes
    -> NOPs, the call is `ret 20h` so the stack balances, and the matching destroy count 1 -> 0):
    the seven-row menu is 217 rows tall and the black band of the 640x480 backdrop between the
    planet's crescent and the artwork is exactly 217 rows.  The two string operands the call
    carried become type 0 relocation padding.  At the HD sizes the box stays (24 Sep 2026): the
    menu block is placed 120 rows under the title instead - 11 px, the stock 100-row box, 9 px -
    or as low as H-72 allows, and the `resolution` fix writes the box's height (94 rows at
    1024x768, 76 at 1280x720, the stock 100 from 1280x800 up).  The whole Council Wars menu
    cluster - logo, title, box, buttons - sits 15 rows higher than the letterbox rule at the HD
    sizes (same day; 0 at 1280x720, where the DC logo already touches the planet's crescent).
REQUIRES the "DC - Council wars/ozi_ns/" overlay folder, exp/animozi.dat, exp/animate/tranozi.fin,
exp/sprites/tranozi.spr, dc/intrf_hd/bintroe and the rewritten main-menu script
(exp/intrf_hd/bintroe) from the repository.  Because the .reloc insert shifts every later
relocation entry, this patch is always applied last.'''),
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
 # ---- every build, always last
 dict(id='icon', name='High-resolution icon (Explorer, taskbar, desktop shortcut)', date='25 Sep 2026', tool='tools/patch_icon.py (icon: tools/make_dc_icon.py -> DC - Council wars\\DC_HD.ICO)',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.38', blocks=blocks_icon,
      desc='''The exes carry at most the game's 32x32, 16-colour icon (dc16.exe and ENGEXP16.EXE; the map
editor none at all), which Windows blows up into a blur on the desktop, in Explorer and on the
taskbar.  This fix gives the exe every image of DC_HD.ICO (in the "DC - Council wars" folder): the
same design - grey frame, "DC", the planet Mars - re-drawn from the original's geometry by
tools/make_dc_icon.py, at 16, 20, 24, 32, 40, 48, 64 (bitmaps), 96 and 256 pixels (PNG).

How, without moving anything that is already in the file:
  * a NEW SECTION ".dcicon" is appended at the end of the file; it holds a complete resource
    directory plus the icon images.  The exe's other resources (the map editor's dialogs, menus and
    strings) stay where they are - the new directory points at them - so the other fixes are
    untouched; only the old 32x32 icon entries are left out
  * four header edits: the number of sections, the new section's 40-byte header in the zero bytes
    after the section table, SizeOfImage, and the resource directory entry of the optional header
  * the games keep their icon group "DC16" and get the same group under id 101 as well - the id the
    game's own window asks for (LoadIconA(hInstance, 101) in create_window); the original has no
    such group, so the game window had the default icon

No code changes.  The file grows by the new section (about 75 KB), which is why this fix is always
applied last.  The appended bytes are written below in Base64 (they are the icon images and the
directory that lists them); their SHA-256 is checked like every other edit.'''),
]

# Names of the patched builds and their desktop shortcuts since 25 Sep 2026 (maintainer: "resulting files and
# shortcuts names must be: 'Dark Colony map editor 1.2', 'Dark Colony', 'Dark Colony Ultimate'"); before that
# dc16new.exe, engexp16new.exe (DCEXP16.EXE 10-15 Sep 2026) and maped_ozi_ns_v1.2.exe.  `orig_path` is where the
# window finds the untouched original, relative to the repository root = the folder of the script.
BUILDS = [
 dict(id='Classic', g='classic', exe='Dark Colony.exe', product='Dark Colony', orig_name='dc16.exe', orig_path='DC - Council wars\\dc16.exe',
      title='Dark Colony (Classic) dc16.exe, build linked 7 Jan 1998, 659456 bytes (patched build: "Dark Colony.exe", until 25 Sep 2026 dc16new.exe)',
      source='NOT from the Dark Colony CD: its DC\\DC16.EXE is the August 1997 build (660480 bytes), which these fixes do not fit - they need dc16.exe of the January 1998 update (659456 bytes), so take it from our repository.',
      steps=['nocd', 'resolution', 'hdpaths', 'cursor', 'pool', 'clock', 'ddraw', 'camera', 'widemap', 'restore', 'longpath', 'music', 'movies', 'sounds', 'icon']),
 dict(id='CouncilWars', g='cw', exe='Dark Colony Ultimate.exe', product='Dark Colony Ultimate', orig_name='ENGEXP16.EXE', orig_path='DC - Council wars\\ENGEXP16.EXE',
      title='Dark Colony - The Council Wars ENGEXP16.EXE, 659968 bytes (patched build: "Dark Colony Ultimate.exe" - Council Wars plus the Dark Colony, OZI and Academy campaigns; until 25 Sep 2026 engexp16new.exe)',
      source='the Council Wars CD holds exactly this file as EXPENG\\ENGEXP16.EXE - copy it into the "DC - Council wars" folder.',
      steps=['nocd', 'resolution', 'hdpaths', 'cursor', 'pool', 'clock', 'ddraw', 'camera', 'widemap', 'restore', 'longpath', 'music', 'ozi', 'icon']),
 dict(id='MapEditor', g='maped', exe='Dark Colony map editor 1.2.exe', product='Dark Colony map editor 1.2', orig_name='maped.exe', orig_path='Dark Colony - Map editor\\maped.exe',
      title='Dark Colony map editor maped.exe (Aug 1997, Borland C++), 336424 bytes (unlocked build: "Dark Colony map editor 1.2.exe", until 25 Sep 2026 maped_ozi_ns_v1.2.exe)',
      source='the Dark Colony CD holds exactly this file as DC\\MAPED.EXE - copy it into the "Dark Colony - Map editor" folder as maped.exe.',
      steps=['blocksets', 'teams', 'healer', 'troopsframe', 'icon']),
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
            p = rstart; blk = None; pages = {}
            while p < rend:
                page, size = struct.unpack_from('<II', cur, p)
                if size == 0: break
                pages[page] = (p, size)
                if page == 0x7F000: blk = (p, size)
                p += size
            ins_at = blk[0] + blk[1]
            ins_len = struct.unpack_from('<I', nxt, blk[0] + 4)[0] - blk[1]   # 12 entries since 23 Sep 2026
            assert 0 < ins_len <= 64 and ins_len % 4 == 0, ins_len
            ins = nxt[ins_at:ins_at + ins_len]
            assert cur[rend - ins_len:rend] == b'\0' * ins_len and nxt[ins_at + ins_len:rend] == cur[ins_at:rend - ins_len]
            special = dict(offset=ins_at, bytes=ins, before=cur[ins_at:ins_at + ins_len], section_end=rend,
                           note=f'.reloc table: insert {ins_len // 2} HIGHLOW entries ({", ".join(f"{v:04X}" for v in struct.unpack(f"<{ins_len // 2}H", ins))}) at the end of the page-0x7F000 block; bytes 0x{ins_at:X}..0x{rend-ins_len:X} move up by {ins_len}, the {ins_len} zero slack bytes 0x{rend-ins_len:X}..0x{rend:X} at the end of the section are dropped')
            covered.update(range(ins_at, rend))
            dirsz = pe + 24 + 96 + 5 * 8 + 4
            blocks = blocks + [
                (dirsz, 4, f'PE optional header: base-relocation directory size 0x{struct.unpack_from("<I", cur, dirsz)[0]:X} -> 0x{struct.unpack_from("<I", nxt, dirsz)[0]:X} (+16)'),
                (blk[0] + 4, 4, f'.reloc block for page 0x7F000 (header at 0x{blk[0]:X}): SizeOfBlock 0x{blk[1]:X} -> 0x{blk[1]+ins_len:X}'),
            ]
            # neutralised entries of the pages 0x5000 and 0x4000: leftover runs inside .reloc
        for blk_ in blocks:
            off, n, note = blk_[0], blk_[1], blk_[2]
            old, new = cur[off:off + n], nxt[off:off + n]
            if len(blk_) == 5:
                assert (old, new) == (blk_[3], blk_[4]), (g, step, hex(off), note, old.hex(), blk_[3].hex())
            assert old != new or n == 0, (g, step, hex(off), note)
            edits.append(('bytes', off, old, new, note)); covered.update(range(off, off + n))
        if step == 'icon':
            # model: four header edits + the new section appended at the end of the file
            ap = ICON_APPEND[g]
            assert ap['at'] == len(cur) and len(nxt) == len(cur) + ap['n'], (g, ap['at'], len(cur), len(nxt))
            tail = nxt[len(cur):]
            assert hashlib.sha256(tail).hexdigest() == ap['sha'], g
            special = dict(kind='append', offset=len(cur), bytes=tail, sha=ap['sha'], note=ap['note'])
        leftover = [(o, n) for o, n in runs_of(cur, nxt[:len(cur)]) if not any(i in covered for i in range(o, o + n))]
        for o, n in leftover:
            if step == 'ozi':
                words = struct.unpack(f'<{n//2}H', cur[o:o + n])
                page = next((pg for pg, (bo, bs) in pages.items() if bo <= o < bo + bs), None)
                gone = {0x5000: 'the removed PLAY INTRO body at VA 0x4050DE / 0x405103',
                        0x4000: 'the two string pushes of the removed credits TTY create at VA 0x404E8B / 0x404E90'}[page]
                note = f'.reloc table, page-0x{page:X} block: entries ' + ', '.join(f'{w:04X}' for w in words) + f' -> 0000 ({gone} no longer exist; type 0 ABSOLUTE padding)'
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
        if special and special.get('kind') == 'append':
            t = bytearray(bytes(t) + special['bytes'])
        elif special:
            e = special['section_end']; i = special['offset']
            t = bytearray(bytes(t[:i]) + special['bytes'] + bytes(t[i:e - len(special['bytes'])]) + bytes(t[e:]))
        assert bytes(t) == nxt, (g, step)
        edits.sort(key=lambda e: e[1])
        pd = dict(P=P, edits=edits, special=special, nbytes=sum(1 for i in range(len(cur)) if cur[i] != nxt[i]) + (len(nxt) - len(cur)), leftover=len(leftover), mode=None)
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

      * run without arguments it opens a window: the three originals beside this script (Dark Colony,
        Council Wars, the map editor) are found and ticked with all their fixes, and one press of
        "Patch selected executables" writes "Dark Colony.exe", "Dark Colony Ultimate.exe" and
        "Dark Colony map editor 1.2.exe" with a shortcut of the same name on the desktop; untick what
        you do not want - or drive it from the command line, see the examples
      * it never touches the input file; it writes a new file
      * every patch is a list of (file offset, old bytes, new bytes, reason) in plain text below
      * a byte is only written if the file still holds the documented old bytes at that offset
      * the SHA-256 of the input must match the known original (override with -Force, the
        per-byte checks stay on)
      * after writing it prints the SHA-256 of the result; with every patch selected the result
        is byte-identical to the executable published in the repository and the script says so
      * the screen resolution is chosen in a drop-down (or -Resolution): 640x480, 1024x768, 1280x1024,
        1280x720, 1280x800, 3840x1080; the sizes with your monitor's aspect ratio are marked "recommended for your
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
    point 0x4528DE; its patched build is written as "Dark Colony.exe") and "ENGEXP16.EXE"
    (ENGEXP16.EXE from the Council Wars CD; patched build "Dark Colony Ultimate.exe" - Council Wars
    plus the Dark Colony, OZI and Academy campaigns).  Both are committed untouched in the repository.
    The third build is the map editor "Dark Colony - Map editor\maped.exe" (the original from the Dark
    Colony CD): its fixes clear the "disabled" flag on dialog controls the original greyed out - the
    functional part of the ozi_ns editor, without the Polish translation - and write
    "Dark Colony map editor 1.2.exe".  (Until 25 Sep 2026 the three were dc16new.exe, engexp16new.exe
    and maped_ozi_ns_v1.2.exe.)

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
    With -All and no -Original all three originals beside this script are patched, one after the
    other, each into its own output name.

.PARAMETER Output
    Where to write the patched copy (only together with -Original).  Default: "Dark Colony.exe" /
    "Dark Colony Ultimate.exe" / "Dark Colony map editor 1.2.exe" next to the original.
    An existing file is not overwritten unless -Overwrite is given.

.PARAMETER Resolution
    Screen resolution to patch for: 640x480 (the stock size: no display fixes), 1024x768 (default,
    the published exes), 1280x1024, 1280x720, 1280x800 or 3840x1080 (32:9).  The 'resolution' and 'clock' fixes exist
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

.PARAMETER DesktopShortcut
    After a successful write, put a shortcut to the patched exe on the desktop ("Dark Colony",
    "Dark Colony - Council Wars" or "Dark Colony map editor"; start folder = the game folder, which
    the game needs to find its data).  An existing shortcut of that name is replaced.  The window
    does the same with its "Desktop shortcut" checkbox (ticked by default).

.PARAMETER Verify
    Instead of patching, inspect an existing exe: which build it is and which patches it carries.

.EXAMPLE
    .\Apply-DarkColonyPatches.ps1                               # the window
    .\Apply-DarkColonyPatches.ps1 -All -DesktopShortcut           # all three executables, as the window does
    .\Apply-DarkColonyPatches.ps1 -List
    .\Apply-DarkColonyPatches.ps1 -List -Detail                 # every single byte edit
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Council wars\dc16.exe" -All
        (run from the root of the Dark-Colony repository, where this file lives; writes "Dark Colony.exe")
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Council wars\dc16.exe" -Patches nocd,resolution,hdpaths,pool
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Council wars\ENGEXP16.EXE" -All -DesktopShortcut
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Council wars\dc16.exe" -All -Resolution 1280x800
    .\Apply-DarkColonyPatches.ps1 -Original "Dark Colony - Map editor\maped.exe" -All     (-> "Dark Colony map editor 1.2.exe")
    .\Apply-DarkColonyPatches.ps1 -Verify "DC - Council wars\Dark Colony.exe"

.NOTES
    Double-click INSTALL.CMD beside this file: it starts this script with Windows
    PowerShell 5.1 and -ExecutionPolicy Bypass for that one run (Windows' own "Run with PowerShell"
    obeys the execution policy, which refuses a script from a downloaded ZIP), and passes any
    command-line options on.  Without it, if Windows refuses to run the script ("running scripts is
    disabled"), start it with
        powershell -ExecutionPolicy Bypass -File .\Apply-DarkColonyPatches.ps1
    Relative paths are taken from PowerShell's current location.
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
    [Parameter(ParameterSetName = 'Apply')] [switch] $DesktopShortcut,
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
#  And one that grows the file (icon patch only, always the last fix): @{ Append = <offset = the file's
#  length before>; Sha256 = '<of the appended bytes>'; Length = <n>; Base64 = '<the appended bytes>' }.
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
        ProductName    = {ps_str(B['product'])}      # the desktop shortcut's name
        OriginalPath   = {ps_str(B['orig_path'])}      # where the window looks for the original, relative to this script
        # where a player gets the original when theirs is missing or not the original (the window's red box)
        RepoUrl        = {ps_str('https://github.com/endotermic/Dark-Colony/blob/main/' + B['orig_path'].replace(chr(92), '/').replace(' ', '%20'))}
        SourceNote     = {ps_str(B['source'])}
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
        if pd['special'] and pd['special'].get('kind') == 'append':
            sp = pd['special']
            W(f'                    # {sp["note"]}')
            W(f'                    # (Base64 of the {len(sp["bytes"])} appended bytes; decode it to see them - it is the resource directory and the images of DC_HD.ICO)')
            W(f'                    @{{ Append = 0x{sp["offset"]:X}; Sha256 = {ps_str(sp["sha"])}; Length = {len(sp["bytes"])}')
            W(f'                       Base64 = {ps_str(base64.b64encode(sp["bytes"]).decode())} }}')
        elif pd['special']:
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

# Absolute path against PowerShell's current location.  .NET calls ([IO.File], [IO.Path]::GetFullPath)
# resolve a relative path against the PROCESS directory, which Set-Location does not move, while
# Test-Path / Resolve-Path use $PWD; every path is normalised once here so both agree.  The file does
# not have to exist yet.
function Get-AbsolutePath([string] $Path) {
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function Test-BytesAt([byte[]] $Data, [int] $Offset, [byte[]] $Expected) {
    if ($Offset + $Expected.Length -gt $Data.Length) { return $false }
    for ($i = 0; $i -lt $Expected.Length; $i++) { if ($Data[$Offset + $i] -ne $Expected[$i]) { return $false } }
    return $true
}

# 'old' = the file still holds the documented original bytes, 'new' = the patched bytes, 'other' = neither.
function Get-EditState([byte[]] $Data, $Edit) {
    if ($Edit.ContainsKey('Append')) {
        # the file must end exactly where the section is appended ('old'), or carry it ('new', by SHA-256)
        if ($Data.Length -eq $Edit.Append) { return 'old' }
        if ($Data.Length -eq $Edit.Append + $Edit.Length) {
            $sha = [System.Security.Cryptography.SHA256]::Create()
            try { $h = ([BitConverter]::ToString($sha.ComputeHash($Data, $Edit.Append, $Edit.Length)) -replace '-', '').ToLower() }
            finally { $sha.Dispose() }
            if ($h -eq $Edit.Sha256) { return 'new' }
        }
        return 'other'
    }
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
            $where = if ($e.ContainsKey('Insert')) { '0x{0:X}' -f $e.Insert } elseif ($e.ContainsKey('Append')) { '0x{0:X} (the end of the file)' -f $e.Append } else { '0x{0:X}' -f $e.Offset }
            $why = if ($state -eq 'new') { 'already patched - this file already carries the fix' } else { 'not the documented original bytes' }
            throw ("fix '{0}': the bytes at file offset {1} are {2}. The fixes apply to the untouched original exe of the repository " +
                   "(dc16.exe / ENGEXP16.EXE in 'DC - Council wars', maped.exe in the editor folder), not to an already patched build.") -f $Patch.Id, $where, $why
        }
    }
    $out = [byte[]] $Data.Clone()
    $append = $null
    foreach ($e in $Patch.Edits) {
        if ($e.ContainsKey('Append')) { $append = $e; continue }      # grows the file: done after the in-place edits
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
    if ($append) {
        [byte[]] $tail = [Convert]::FromBase64String($append.Base64)
        if ($tail.Length -ne $append.Length -or (Get-Sha256Hex $tail) -ne $append.Sha256) { throw "fix '$($Patch.Id)': the appended bytes in this script do not match their SHA-256 (the file was edited?)" }
        $grown = New-Object byte[] ($out.Length + $tail.Length)
        [Array]::Copy($out, $grown, $out.Length)
        [Array]::Copy($tail, 0, $grown, $out.Length, $tail.Length)
        $out = $grown
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
        # the original size, or the size with the icon section appended (fix icon grows the file)
        $sizes = @($b.Size)
        foreach ($p in $b.Patches) { foreach ($e in $p.Edits) { if ($e.ContainsKey('Append')) { $sizes += $e.Append + $e.Length } } }
        if ($sizes -notcontains $Data.Length) { continue }
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
        } elseif ($e.ContainsKey('Append')) {
            $lines += ('append @0x{0:X6}  {1} bytes, SHA-256 {2}  (the Base64 text in this script; see the fix description)' -f $e.Append, $e.Length, $e.Sha256)
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
# paint_intro.cw_menu_lift (24 Sep 2026, maintainer: "move DC logo, DARK COLONY logo, credentials and
# buttons block 15 points higher for resolutions except 640x480"): the Council Wars menu cluster sits
# 15 rows higher than the letterbox rule at the HD sizes - as far as the opaque DC logo stays below the
# painted crescent's tail (row 112 of the 480-row design, measured; 0 at 1280x720, where it already
# touches, and 0 at the stock size).  Used for exp\intrf_hd\bintroe and introe, the credits box
# (fix `resolution`) and the button block's H-72 cap (Edit-OziMenu).
$MENU_LIFT = 15; $CRESCENT_TAIL = 112; $CW_CLUSTER_CENTRE = 296
function Get-MenuLift([int] $H) {
    $logoTop = [int][Math]::Round($CW_CLUSTER_CENTRE * ($H / 480 - 1), [System.MidpointRounding]::ToEven) + $LOGO_CLEARANCE
    $tail = [int][Math]::Round($CRESCENT_TAIL * $H / 480, [System.MidpointRounding]::ToEven)
    return [Math]::Max(0, [Math]::Min($MENU_LIFT, $logoTop - $tail - 1))
}
function Test-Logo($p)  { return ($p.kind -eq 'gadget' -and $p.rest.Count -gt 0 -and $LOGOS -contains $p.rest[0]) }
function Test-Title($p) { return ($p.kind -eq 'gadget' -and $p.rest.Count -gt 0 -and ($BUTTON_SPRITES + $LOGOS) -notcontains $p.rest[0]) }

# paint_intro.layout_for + relayout: the title/credits/button cluster keeps its stock vertical centre
# as a fraction of the height, the button grid is centred horizontally, logo and title centred each.
# $Lift rows come off the vertical shift (the Council Wars overrides: Get-MenuLift).
function Edit-IntroScript([string] $Text, [int] $W, [int] $H, [int] $Lift = 0) {
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
    $dy -= $Lift
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

# build_ozi_overlay.menu_layout (23 Sep 2026, maintainer's order): the patched Council Wars menu has
# five rows, the second column only on rows 1, 3 and 5.  The numbers are the exe's button ids, which
# pick the handler (patch_ozi_menu.py rewires 16 and 4 to the pack), so only positions and labels move:
#
#     ACADEMY       (1)   MULTI PLAYER WAR (3)
#     COUNCIL WARS  (0)
#     LOAD CW GAME  (2)   ENCYCLOPEDIA     (5)
#     OZI MISSIONS (16)
#     LOAD OZI GAME (4)   QUIT            (12)
# The patched Council Wars main menu (doc 10.35 and 10.36), the PowerShell twin of
# tools/build_ozi_overlay.py menu_layout()/menu_script(): Classic's 2x4 button grid becomes seven
# rows in the left column - ACADEMY, the two Dark Colony entries, the two Council Wars entries and
# the two pack entries - with MULTI PLAYER WAR and ENCYCLOPEDIA at the top of the second column and
# QUIT on its last row, a gap of half a button height (12 px) after rows 1, 3 and 5 and the same
# gap between the columns, after which the block is re-centred on the screen.  Vertically the rows
# hang from the DCUT title gadget (24 Sep 2026, maintainer: "return back credentials [credits] for
# higher than 640x480 resolutions"): the first row 120 rows under it - 11 px, the stock 100-row
# credits box, 9 px - unless the bottom row would pass H-72 (the stock 640x480 bottom row 408,
# 2-3 px above the bottom artwork every backdrop starts at H-45); then the block stops there and
# the box gets shorter (the `resolution` fix writes its height: 94 rows at 1024x768, 76 at
# 1280x720, 100 from 1280x800 up).  At 640x480 that is the whole 217-row band, so the block grows
# upwards from row 408, the `ozi` fix removes the box, and the gap shrinks by a pixel so that the
# first row still clears the planet's crescent (rows 198..217).  Both anchors depend only on the
# title and the screen size, so applying this twice changes nothing.  The whole Council Wars cluster
# (title included, so the block follows) and the H-72 cap sit Get-MenuLift rows higher at the HD sizes.
# The two Dark Colony buttons are ids 6 and 7, which the stock script used for the LARGEBUTTON
# gadgets of buttons 0 and 1; those move to 19 and 20, the new plates are 21 and 22, and `banim`
# pairs all ten.  The new lines are cloned from the script's own `pushb 16` / `gadget 17` /
# `textmsg 8` so they keep its field layout.  The untouched exe keeps Classic's 2x4 grid and labels
# in exp\intrface\bintroe (doc 10.35).
function Set-ScriptTokens([string] $Line, $Changes) {
    $toks = @($TOKENS.Matches($Line) | ForEach-Object { $_.Value })
    $n = 0
    for ($i = 0; $i -lt $toks.Count; $i++) {
        if ($toks[$i].Trim().Length -eq 0) { continue }
        $n++
        if ($Changes.ContainsKey($n)) { $toks[$i] = [string] $Changes[$n] }
    }
    return (-join $toks)
}

function Get-TextmsgLine([int] $N, [string] $Text) {
    $num = [string] $N
    return ('textmsg ' + $num + (' ' * (8 - $num.Length)) + $Text)
}

function Edit-OziMenu([string] $Text) {
    $cols = @(@(1, 6, 7, 0, 2, 16, 4), @(3, 5, $null, $null, $null, $null, 12))
    $gapAfter = @(1, 3, 5)
    $stockButtons = @(0, 1, 2, 3, 4, 5, 12, 16)
    $stockGadgets = @(8, 9, 10, 11, 13, 17)
    $newButtons = @(6, 7)
    $renum = @{ 6 = 19; 7 = 20 }
    $gadgetOf = @{ 0 = 19; 1 = 20; 2 = 8; 3 = 9; 4 = 10; 5 = 11; 6 = 21; 7 = 22; 12 = 13; 16 = 17 }
    $labelOf = @{ 6 = 9; 7 = 10 }
    $template = @{ 'pushb' = 16; 'gadget' = 17 }
    $banimId = 18
    $banimOrder = @(0, 1, 2, 3, 4, 5, 16, 12, 6, 7)
    $textTemplate = 8
    $stockTopLimit = 218
    $labels = @{ 1 = 'COUNCIL WARS'; 2 = 'ACADEMY'; 3 = 'LOAD CW GAME'; 5 = 'LOAD OZI GAME'
                 8 = 'OZI MISSIONS'; 9 = 'DARK COLONY'; 10 = 'LOAD DC GAME' }
    $xy = @{}
    foreach ($m in ([regex] '(?m)^\s*pushb\s+(\d+)\s+\d+\s+(\d+)\s+(\d+)\s').Matches($Text)) { $xy[[int]$m.Groups[1].Value] = @([int]$m.Groups[2].Value, [int]$m.Groups[3].Value) }
    $gadgets = @{}
    foreach ($m in ([regex] '(?m)^\s*gadget\s+(\d+)\s').Matches($Text)) { $gadgets[[int]$m.Groups[1].Value] = $true }
    $missing = @()
    foreach ($need in $stockButtons) { if (-not $xy.ContainsKey($need)) { $missing += "pushb $need" } }
    foreach ($need in $stockGadgets) { if (-not $gadgets.ContainsKey($need)) { $missing += "gadget $need" } }
    # the stock grid has the two plates as 6 and 7, this function's own output as 19 and 20
    $haveOld = $true; $haveNew = $true
    foreach ($k in $renum.Keys) { if (-not $gadgets.ContainsKey([int]$k)) { $haveOld = $false } }
    foreach ($v in $renum.Values) { if (-not $gadgets.ContainsKey([int]$v)) { $haveNew = $false } }
    if (-not ($haveOld -or $haveNew)) { $missing += 'gadget 6/19, gadget 7/20' }
    $b = ([regex] '(?m)^\s*banim\s+18\s+\d+\s+(\d+)\s+(\d+)\s').Match($Text)
    $pairs = @([string] $stockButtons.Count, [string] ($stockButtons.Count + $newButtons.Count))
    if (-not $b.Success -or $b.Groups[1].Value -ne $b.Groups[2].Value -or -not ($pairs -contains $b.Groups[1].Value)) { $missing += 'banim 18 with 8 or 10 pairs' }
    if ($missing.Count) { throw ("bintroe: not Classic's 2x4 button grid (missing " + ($missing -join ', ') + ')') }
    $xs = @($xy.Values | ForEach-Object { $_[0] } | Sort-Object -Unique)
    $ys = @($xy.Values | ForEach-Object { $_[1] } | Sort-Object -Unique)
    if ($xs.Count -ne 2 -or $ys.Count -lt 4) { throw ('bintroe: expected two button columns and at least four rows, found {0} x {1}' -f $xs.Count, $ys.Count) }
    $pitch = [int]::MaxValue
    for ($i = 1; $i -lt $ys.Count; $i++) { if ($ys[$i] - $ys[$i - 1] -lt $pitch) { $pitch = $ys[$i] - $ys[$i - 1] } }
    $tm = [regex]::Match($Text, '(?im)^\s*gadget\s+\d+\s+\d+\s+\d+\s+(\d+)\s+\d+\s+(\d+)\s+DCUT\b')
    if (-not $tm.Success) { throw 'bintroe: no DCUT title gadget (the menu rows hang from it)' }
    $titleBottom = [int]$tm.Groups[1].Value + [int]$tm.Groups[2].Value
    $creditsRoom = 11 + 100 + 9      # title -> first row at the HD sizes: 11 px, the stock 100-row credits box, 9 px
    $bottomMargin = 72               # the bottom row never passes H-72 (stock 640x480 row 408; artwork from H-45)
    $sz = [regex]::Matches($Text, '(?m)^\s*pushb\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)\s+(\d+)\s')   # two passes: a
    $bw = ($sz | ForEach-Object { [int]$_.Groups[1].Value } | Measure-Object -Minimum).Minimum      # pipeline flattens
    $bh = ($sz | ForEach-Object { [int]$_.Groups[2].Value } | Measure-Object -Minimum).Minimum      # nested arrays
    $m4 = $SIZE4.Match($Text); $m2 = $SIZE2.Match($Text)
    $screenW = if ($m4.Success) { [int]$m4.Groups[5].Value } elseif ($m2.Success) { [int]$m2.Groups[3].Value } else { throw 'bintroe: no size line' }   # SIZE4 = size X Y W H
    $screenH = if ($m4.Success) { [int]$m4.Groups[6].Value } elseif ($m2.Success) { [int]$m2.Groups[5].Value } else { 0 }
    # 25 * 0.5 rounds to 12 in .NET and in Python alike, so both implementations produce the same bytes
    $gap = [int][Math]::Round($bh * 0.5)
    $rows = $cols[0].Count
    $topLimit = if ($screenW -eq 640 -and $screenH -eq 480) { $stockTopLimit } else { 0 }
    $bottom = 0
    while ($true) {
        $rise = ($rows - 1) * $pitch + $gap * $gapAfter.Count      # first row -> bottom row
        $bottom = [Math]::Min($titleBottom + $creditsRoom + $rise, $screenH - $bottomMargin - (Get-MenuLift $screenH))
        if ($gap -le 0 -or ($bottom - $rise) -ge $topLimit) { break }
        $gap--
    }
    $offs = @()
    for ($k = 0; $k -lt $rows; $k++) {
        $extra = 0
        foreach ($r in $gapAfter) { if ($r -le $k) { $extra += $gap } }
        $offs += ($k * $pitch + $extra)
    }
    $colGap = [int][Math]::Round($bh * 0.5)
    $left = [int][Math]::Floor(($screenW - (2 * $bw + $colGap)) / 2)
    $colX = @($left, ($left + $bw + $colGap))
    $place = @{}
    $move = @{}
    for ($c = 0; $c -lt $cols.Count; $c++) {
        for ($k = 0; $k -lt $cols[$c].Count; $k++) {
            $id = $cols[$c][$k]
            if ($null -ne $id) {
                $pos = @($colX[$c], ($bottom - ($offs[$rows - 1] - $offs[$k])))
                $place[[int]$id] = $pos
                $move[[int]$id] = $pos
                $move[[int]$gadgetOf[[int]$id]] = $pos        # the gadget follows its button
            }
        }
    }
    $dropPushb = @($newButtons)
    $dropGadget = @($newButtons | ForEach-Object { [int] $gadgetOf[[int]$_] })
    $dropText = @($newButtons | ForEach-Object { [int] $labelOf[[int]$_] })
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($raw in $Text.Split("`n")) {
        $cr = if ($raw.EndsWith("`r")) { "`r" } else { '' }
        $line = if ($cr) { $raw.Substring(0, $raw.Length - 1) } else { $raw }
        $m = [regex]::Match($line, '^\s*(pushb|gadget)\s+(\d+)\s')
        $t = [regex]::Match($line, '^\s*textmsg\s+(\d+)\s')
        if ($m.Success) {
            $kind = $m.Groups[1].Value
            $id = [int] $m.Groups[2].Value
            if ($kind -eq 'pushb' -and ($dropPushb -contains $id)) { continue }     # re-emitted below
            if ($kind -eq 'gadget' -and ($dropGadget -contains $id)) { continue }
            if ($kind -eq 'gadget' -and $renum.ContainsKey($id)) {                  # free ids 6 and 7
                $line = Set-ScriptTokens $line @{ 2 = [string] $renum[$id] }
                $id = [int] $renum[$id]
            }
            if ($move.ContainsKey($id)) {
                $pos = $move[$id]
                $line = Set-ScriptTokens $line @{ 4 = [string] $pos[0]; 5 = [string] $pos[1] }
            }
            $out.Add($line + $cr)
            if ($id -eq $template[$kind]) {
                foreach ($new in $newButtons) {
                    $pos = $place[[int]$new]
                    $ident = if ($kind -eq 'pushb') { [int] $new } else { [int] $gadgetOf[[int]$new] }
                    $changes = @{ 2 = [string] $ident; 4 = [string] $pos[0]; 5 = [string] $pos[1] }
                    if ($kind -eq 'pushb') { $changes[12] = [string] $labelOf[[int]$new] }
                    $out.Add((Set-ScriptTokens $line $changes) + $cr)
                }
            }
            continue
        }
        if ($t.Success) {
            $n = [int] $t.Groups[1].Value
            if ($dropText -contains $n) { continue }
            if ($labels.ContainsKey($n)) { $line = Get-TextmsgLine $n $labels[$n] }
            $out.Add($line + $cr)
            if ($n -eq $textTemplate) {
                foreach ($new in $newButtons) {
                    $lb = [int] $labelOf[[int]$new]
                    $out.Add((Get-TextmsgLine $lb $labels[$lb]) + $cr)
                }
            }
            continue
        }
        if ([regex]::IsMatch($line, '^\s*banim\s+\d+\s')) {
            $g = @($banimOrder | ForEach-Object { [string] $gadgetOf[[int]$_] })
            $bt = @($banimOrder | ForEach-Object { [string] $_ })
            $line = ('banim   {0}  0  {1} {1}' -f $banimId, $banimOrder.Count) + "`t " + ($g -join ' ') + '  ' + ($bt -join ' ')
        }
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
                $t = Set-BackgroundHd (Edit-IntroScript $text $W $H (Get-MenuLift $H))   # the Council Wars cluster sits higher
                if ($nm -eq 'bintroe') { $t = Edit-OziMenu $t }
            }
            Write-Latin1 (Join-Path $expHd ([System.IO.Path]::GetFileName($p))) $t; $expWritten++
        }
        foreach ($ln in 'hxscene.txt', 'gxscene.txt') {
            $p = Find-CI $expG $ln
            if ($p) { Write-Latin1 (Join-Path $expHd ([System.IO.Path]::GetFileName($p))) (Edit-SceneList (Read-Latin1 $p) $dx0 $dy0); $expWritten++ }
        }
    }
    # The DARK COLONY mode's prefix points at dc\, which holds the patched menu and nothing else:
    # every other file a Classic campaign opens falls through to the Classic data in the game root.
    $dcWritten = 0
    $dcSrc = Find-CI $expHd 'bintroe'
    if ($dcSrc -and $expWritten -gt 0) {
        $dcHd = Join-Path $GameDir 'dc\intrf_hd'
        if (-not (Test-Path -LiteralPath $dcHd)) { New-Item -ItemType Directory -Path $dcHd -Force | Out-Null }
        [System.IO.File]::Copy($dcSrc, (Join-Path $dcHd 'bintroe'), $true); $dcWritten++
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
    $lines += ('interface set for {0} written: INTRF_HD\ {1} files{2}{3}{4} ({5:N1} s, GIFs re-encoded by the compiled DcGif codec)' -f $Mode, $written,
               $(if ($expWritten) { ", exp\intrf_hd\ $expWritten" } else { '' }), $(if ($dcWritten) { ", dc\intrf_hd\ $dcWritten" } else { '' }),
               $(if ($oziWritten) { ", ozi_ns\intrf_hd\ $oziWritten" } else { '' }), $sw.Elapsed.TotalSeconds)
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
# ozi @ 640x480: exp\intrface\bintoze (and ozi_ns\intrface\bintoze) = the stock menu + the two OZI labels
function Write-StockOziMenu([string] $GameDir) {
    $src = Find-CI (Join-Path $GameDir 'exp\intrface') 'bintroe'
    if (-not $src) { return @('exp\intrface\bintoze NOT written: exp\intrface\bintroe is missing') }
    $t = Edit-OziMenu (Read-Latin1 $src)
    $lines = @()
    foreach ($dir in 'exp\intrface', 'ozi_ns\intrface', 'dc\intrface') {
        $d = Join-Path $GameDir $dir
        # dc\ is the DARK COLONY mode's overlay and holds only this file, so it is created here
        if ($dir -eq 'dc\intrface' -and -not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
        if (Test-Path -LiteralPath $d) { Write-Latin1 (Join-Path $d 'bintoze') $t; $lines += ('wrote {0}\bintoze (the patched menu; the 640x480 exe reads this copy)' -f $dir) }
    }
    return $lines
}

# Desktop shortcut to a patched exe (the window's "Desktop shortcut" checkbox, -DesktopShortcut on the
# command line).  The game opens its data files relative to its working folder, so the shortcut's
# "Start in" is the game folder - a COPY of the exe on the desktop would not find anything.  Made with
# the WScript.Shell COM object that is part of Windows; an existing shortcut of the same name is
# replaced.  $script:DesktopFolder lets a test write somewhere else than the real desktop.
$script:DesktopFolder = $null
function New-GameShortcut([string] $ExePath, $Build) {
    $name = $Build.ProductName          # "Dark Colony", "Dark Colony Ultimate", "Dark Colony map editor 1.2"
    $desktop = if ($script:DesktopFolder) { $script:DesktopFolder } else { [Environment]::GetFolderPath('Desktop') }
    if (-not $desktop -or -not (Test-Path -LiteralPath $desktop)) { throw 'this user has no desktop folder' }
    $exe = Get-AbsolutePath $ExePath
    $lnkPath = Join-Path $desktop ($name + '.lnk')
    $shell = New-Object -ComObject WScript.Shell
    try {
        $lnk = $shell.CreateShortcut($lnkPath)
        $lnk.TargetPath = $exe
        $lnk.WorkingDirectory = Split-Path -Parent $exe
        $lnk.IconLocation = "$exe,0"
        $lnk.Description = "$name - patched exe written by Apply-DarkColonyPatches.ps1"
        $lnk.Save()
    } finally {
        [void] [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
    return $lnkPath
}

# Applies the chosen patches (canonical order) to the bytes of $OriginalPath and writes $OutputPath.
# Returns a small result object; throws on any check failure.
# $Progress (optional): a script block called with one line of text before each step - the window
# shows it in its "patching in progress" box; the command line passes nothing.
function Invoke-PatchRun([string] $OriginalPath, $Build, [object[]] $Chosen, [string] $OutputPath, [string] $Mode, [scriptblock] $Progress) {
    $data = [System.IO.File]::ReadAllBytes($OriginalPath)
    $effective = @(Get-BuildPatches $Build $Mode)
    $ordered = @($effective | Where-Object { $p = $_; ($Chosen | Where-Object { $_.Id -eq $p.Id -and $_.Mode -eq $p.Mode }) })
    $result = $data
    $n = 0
    foreach ($p in $ordered) {
        $n++
        if ($Progress) { & $Progress ("Applying fix {0} of {1}: '{2}' ({3}, {4} edits)..." -f $n, $ordered.Count, $p.Id, $p.Name, @($p.Edits).Count) }
        $result = Invoke-Patch $result $p
    }
    if ($Progress) { & $Progress ("Writing {0} ({1} bytes)..." -f (Split-Path -Leaf $OutputPath), $result.Length) }
    [System.IO.File]::WriteAllBytes($OutputPath, $result)
    $outSha = Get-Sha256Hex $result
    $ref = if ($Mode) { $Build.ReferenceSha256[$Mode] } else { $Build.PatchedSha256 }
    # an HD display fix was applied: build the INTRF_HD interface set for the chosen size (scripts,
    # briefing lists, letterboxed backgrounds, loading screens; the Council Wars and OZI copies too)
    $generated = @()
    if ($Mode -and $Mode -ne '640x480' -and ($ordered | Where-Object { $_.ContainsKey('SetSources') })) {
        $movies = [bool] ($ordered | Where-Object { $_.Id -eq 'movies' })
        if ($Progress) { & $Progress ("Writing the {0} interface set into INTRF_HD (scripts, backgrounds, loading screens) - this takes a few seconds..." -f $Mode) }
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
# The screen resolution the window applies to a build: the drop-down's choice for the two games, '' for
# the map editor (it has none).  Script level, because the window's event handlers run outside
# Show-PatcherWindow and cannot see functions defined inside it.
function Get-GuiMode($Build) {
    if (@($Build.Modes).Count -gt 0 -and $script:gui.Mode) { return $script:gui.Mode }
    if (@($Build.Modes).Count -gt 0) { return $Build.DefaultMode }
    return ''
}

# The fixes of one window item that will be applied: every fix of its resolution that is available in
# the output folder and was not unticked.
function Get-GuiChosen($Item) {
    $out = @()
    foreach ($p in @(Get-BuildPatches $Item.Build (Get-GuiMode $Item.Build))) {
        if ($Item.Unavailable.ContainsKey($p.Id) -or $Item.Unticked.ContainsKey($p.Id)) { continue }
        $out += $p
    }
    return $out
}

function Show-PatcherWindow([string] $PreloadPath) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()

    # A classical installer (25 Sep 2026, maintainer: "let's do the classical installer way for patch region
    # instead of tabs. on opening there is a greeting message and button forward. second screen contains
    # options for patching DC, third screen for patching CW and fourth for patching maped"): Welcome ->
    # Dark Colony -> Dark Colony Ultimate -> map editor -> (Patch) -> Finished, with Back / Next / Cancel.
    # One item per build; the three untouched originals beside this script are found and ticked, each
    # page keeps its own fix choices (Unticked) and the fixes whose resources are missing.
    $items = @()
    foreach ($b in $Builds) {
        $items += @{ Build = $b; Path = $null; Data = $null; IsOriginal = $false; Out = $null; Checked = $false; Patches = @()
                     Status = 'not found - press Browse to pick it'; Color = 'Firebrick'; Unticked = @{}; Unavailable = @{}; Error = $null }
    }
    $script:gui = @{ Items = $items; Sel = -1; Step = 0; Syncing = $false; Mode = ''; ModeList = @(); Patches = @(); Monitor = (Get-MonitorSize)
                     Here = $PSScriptRoot; Results = $null }     # Here = the folder this script sits in = the repository root
    foreach ($b in $Builds) { if (@($b.Modes).Count -gt 0) { $script:gui.ModeList = @($b.Modes); break } }
    $mono = New-Object System.Drawing.Font('Consolas', 9)
    $bold = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Dark Colony patcher'
    $form.ClientSize = New-Object System.Drawing.Size(984, 700)
    $form.FormBorderStyle = 'FixedDialog'; $form.MaximizeBox = $false
    $form.StartPosition = 'CenterScreen'
    $form.Font = New-Object System.Drawing.Font('Segoe UI', 9)

    # --- header band: title and subtitle of the current step
    $header = New-Object System.Windows.Forms.Panel
    $header.Location = '0,0'; $header.Size = '984,64'; $header.BackColor = [System.Drawing.Color]::White
    $lblTitle = New-Object System.Windows.Forms.Label
    $lblTitle.Location = '20,10'; $lblTitle.Size = '940,24'; $lblTitle.Font = New-Object System.Drawing.Font('Segoe UI', 12, [System.Drawing.FontStyle]::Bold)
    $lblSub = New-Object System.Windows.Forms.Label
    $lblSub.Location = '34,36'; $lblSub.Size = '930,20'
    $header.Controls.AddRange(@($lblTitle, $lblSub))
    $sepTop = New-Object System.Windows.Forms.Label
    $sepTop.Location = '0,64'; $sepTop.Size = '984,2'; $sepTop.BorderStyle = 'Fixed3D'

    # --- page 0: welcome
    $pWelcome = New-Object System.Windows.Forms.Panel
    $pWelcome.Location = '0,66'; $pWelcome.Size = '984,580'
    $lblHello = New-Object System.Windows.Forms.Label
    $lblHello.Location = '24,16'; $lblHello.Size = '936,184'
    $lblHello.Text = @(
        'Welcome!  This installer builds the patched Dark Colony executables on your own PC, from the untouched',
        'original executables of this folder:',
        '',
        '    Dark Colony                  dc16.exe      ->  Dark Colony.exe',
        '    Dark Colony Ultimate         ENGEXP16.EXE  ->  Dark Colony Ultimate.exe   (Council Wars plus the Dark Colony,',
        '                                                                               OZI and Academy campaigns)',
        '    Dark Colony map editor 1.2   maped.exe     ->  Dark Colony map editor 1.2.exe',
        '',
        'The next three pages show the fixes of each executable - all of them are selected; you only have to',
        'press Next three times and then Patch.  Nothing is downloaded, the originals are never changed, and every',
        'byte this script writes is listed, with its reason, in Apply-DarkColonyPatches.ps1 (open it in Notepad).'
    ) -join "`r`n"
    $lblHello.Font = New-Object System.Drawing.Font('Consolas', 9.5)
    $lblFound = New-Object System.Windows.Forms.Label
    $lblFound.Location = '24,214'; $lblFound.Size = '936,76'; $lblFound.Font = $mono
    $chkLnk = New-Object System.Windows.Forms.CheckBox
    $chkLnk.Text = 'Put a shortcut to each patched executable on the desktop'; $chkLnk.Location = '24,306'; $chkLnk.AutoSize = $true
    $chkLnk.Checked = $true; $chkLnk.Font = $bold
    $lblLnk = New-Object System.Windows.Forms.Label
    $lblLnk.Location = '44,330'; $lblLnk.Size = '900,36'
    $lblLnk.Text = 'Named "Dark Colony", "Dark Colony Ultimate" and "Dark Colony map editor 1.2"; each starts in its game folder, where the game finds its files.  An older shortcut of the same name is replaced.'
    $lblNext = New-Object System.Windows.Forms.Label
    $lblNext.Location = '24,540'; $lblNext.Size = '936,20'; $lblNext.Text = 'Press Next to continue.'
    # a missing or wrong original: a big red banner here, the details and the remedies on its page
    $lblProblem = New-Object System.Windows.Forms.Label
    $lblProblem.Location = '24,374'; $lblProblem.Size = '936,160'; $lblProblem.Visible = $false
    $lblProblem.BackColor = [System.Drawing.Color]::FromArgb(192, 0, 0); $lblProblem.ForeColor = [System.Drawing.Color]::White
    $lblProblem.Font = New-Object System.Drawing.Font('Segoe UI', 10.5, [System.Drawing.FontStyle]::Bold); $lblProblem.Padding = '12,8,12,8'
    $pWelcome.Controls.AddRange(@($lblHello, $lblFound, $chkLnk, $lblLnk, $lblNext, $lblProblem))

    # --- pages 1..3: one per executable (the page's controls carry the executable's index in .Tag,
    # because the handlers run outside this function)
    $tip = New-Object System.Windows.Forms.ToolTip
    $pages = @()
    for ($i = 0; $i -lt $items.Count; $i++) {
        $b = $items[$i].Build
        $pnl = New-Object System.Windows.Forms.Panel
        $pnl.Location = '0,66'; $pnl.Size = '984,580'; $pnl.Visible = $false
        $inc = New-Object System.Windows.Forms.CheckBox
        $inc.Text = "Patch $($b.ProductName)  (writes $($b.OutputName))"; $inc.Location = '20,12'; $inc.AutoSize = $true; $inc.Font = $bold; $inc.Tag = $i
        $lo = New-Object System.Windows.Forms.Label
        $lo.Text = 'Original:'; $lo.Location = '20,43'; $lo.AutoSize = $true
        $path = New-Object System.Windows.Forms.TextBox
        $path.Location = '100,40'; $path.Size = '752,23'; $path.ReadOnly = $true; $path.TabStop = $false
        $brw = New-Object System.Windows.Forms.Button
        $brw.Text = 'Browse...'; $brw.Location = '860,38'; $brw.Size = '104,27'; $brw.Tag = $i
        $tip.SetToolTip($brw, "Pick the untouched original $($b.OriginalName) (another copy than the one found beside this script).")
        $det = New-Object System.Windows.Forms.Label
        $det.Location = '100,68'; $det.Size = '864,34'
        $res = $null
        if (@($b.Modes).Count -gt 0) {
            $lr = New-Object System.Windows.Forms.Label
            $lr.Text = 'Screen resolution:'; $lr.Location = '20,112'; $lr.AutoSize = $true
            $res = New-Object System.Windows.Forms.ComboBox
            $res.Location = '140,108'; $res.Size = '300,23'; $res.DropDownStyle = 'DropDownList'; $res.Tag = $i
            $ln = New-Object System.Windows.Forms.Label
            $ln.Text = 'the same for both games - they share the INTRF_HD interface folder'; $ln.Location = '450,112'; $ln.AutoSize = $true
            $ln.ForeColor = [System.Drawing.Color]::DimGray
            $pnl.Controls.AddRange(@($lr, $res, $ln))
        } else {
            $lr = $null
            $ln = New-Object System.Windows.Forms.Label
            $ln.Text = 'The map editor has no screen resolution to choose.'; $ln.Location = '20,112'; $ln.AutoSize = $true
            $ln.ForeColor = [System.Drawing.Color]::DimGray
            $pnl.Controls.Add($ln)
        }
        $all = New-Object System.Windows.Forms.CheckBox
        $all.Text = 'Select all fixes  (result = the reference build)'; $all.Location = '20,142'; $all.AutoSize = $true
        $all.Font = $bold; $all.Enabled = $false; $all.Tag = $i
        $lst = New-Object System.Windows.Forms.CheckedListBox
        $lst.Location = '20,168'; $lst.Size = '452,368'; $lst.CheckOnClick = $true; $lst.IntegralHeight = $false; $lst.Enabled = $false; $lst.Tag = $i
        $lst.Font = New-Object System.Drawing.Font('Segoe UI', 10)
        $li = New-Object System.Windows.Forms.Label
        $li.Text = 'What the highlighted fix changes (always applied in the order of the list):'; $li.Location = '484,144'; $li.AutoSize = $true
        $inf = New-Object System.Windows.Forms.TextBox
        $inf.Location = '484,168'; $inf.Size = '480,368'; $inf.Multiline = $true; $inf.ReadOnly = $true; $inf.ScrollBars = 'Vertical'
        $inf.WordWrap = $true; $inf.Font = $mono; $inf.BackColor = [System.Drawing.SystemColors]::Window
        $lw = New-Object System.Windows.Forms.Label
        $lw.Text = 'Written to:'; $lw.Location = '20,549'; $lw.AutoSize = $true
        $out = New-Object System.Windows.Forms.TextBox
        $out.Location = '100,546'; $out.Size = '864,23'; $out.ReadOnly = $true; $out.TabStop = $false
        # the big red error in front of the checklist when this original is missing or not the original
        # (maintainer, 25 Sep 2026: "if original of one of files are not originals, then bring in front a big
        # red error and offer to select a correct file or to download it from original discs or our repo")
        $err = New-Object System.Windows.Forms.Panel
        $err.Location = '20,104'; $err.Size = '944,436'; $err.Visible = $false
        $err.BackColor = [System.Drawing.Color]::FromArgb(192, 0, 0)
        $errTitle = New-Object System.Windows.Forms.Label
        $errTitle.Location = '18,14'; $errTitle.Size = '908,34'; $errTitle.ForeColor = [System.Drawing.Color]::White
        $errTitle.Font = New-Object System.Drawing.Font('Segoe UI', 16, [System.Drawing.FontStyle]::Bold)
        $errBody = New-Object System.Windows.Forms.Label
        $errBody.Location = '20,56'; $errBody.Size = '906,320'; $errBody.ForeColor = [System.Drawing.Color]::White
        $errBody.Font = New-Object System.Drawing.Font('Segoe UI', 10)
        $errPick = New-Object System.Windows.Forms.Button
        $errPick.Text = 'Select the correct file...'; $errPick.Location = '20,388'; $errPick.Size = '240,34'; $errPick.Tag = $i
        $errPick.BackColor = [System.Drawing.Color]::White; $errPick.Font = $bold
        $errGet = New-Object System.Windows.Forms.Button
        $errGet.Text = 'Download from our repository'; $errGet.Location = '272,388'; $errGet.Size = '260,34'; $errGet.Tag = $i
        $errGet.BackColor = [System.Drawing.Color]::White; $errGet.Font = $bold
        $tip.SetToolTip($errGet, "Opens $($b.RepoUrl) in your browser; this script itself downloads nothing.")
        $err.Controls.AddRange(@($errTitle, $errBody, $errPick, $errGet))
        $pnl.Controls.AddRange(@($inc, $lo, $path, $brw, $det, $all, $lst, $li, $inf, $lw, $out, $err))
        $err.BringToFront()
        $items[$i].UI = @{ Page = $pnl; Check = $inc; Path = $path; Browse = $brw; Status = $det; Res = $res; All = $all; List = $lst; Info = $inf; Out = $out
                           Error = $err; ErrorTitle = $errTitle; ErrorBody = $errBody; ErrorPick = $errPick; ErrorGet = $errGet
                           # hidden while the red box is shown, so it is in front whatever the drawing order
                           Behind = @(@($lr, $res, $ln, $all, $lst, $li, $inf) | Where-Object { $_ }) }
        $pages += $pnl
    }

    # --- page 4: finished
    $pDone = New-Object System.Windows.Forms.Panel
    $pDone.Location = '0,66'; $pDone.Size = '984,580'; $pDone.Visible = $false
    $txtDone = New-Object System.Windows.Forms.TextBox
    $txtDone.Location = '20,16'; $txtDone.Size = '944,520'; $txtDone.Multiline = $true; $txtDone.ReadOnly = $true
    $txtDone.ScrollBars = 'Vertical'; $txtDone.Font = $mono; $txtDone.BackColor = [System.Drawing.SystemColors]::Window
    $lblDone = New-Object System.Windows.Forms.Label
    $lblDone.Location = '20,546'; $lblDone.Size = '944,24'
    $pDone.Controls.AddRange(@($txtDone, $lblDone))

    # --- navigation bar
    $sepBot = New-Object System.Windows.Forms.Label
    $sepBot.Location = '0,646'; $sepBot.Size = '984,2'; $sepBot.BorderStyle = 'Fixed3D'
    $btnVerify = New-Object System.Windows.Forms.Button
    $btnVerify.Text = 'Inspect an exe...'; $btnVerify.Location = '12,658'; $btnVerify.Size = '122,30'
    $tip.SetToolTip($btnVerify, 'Check any Dark Colony executable: which build it is and which fixes it carries.')
    $lblLog = New-Object System.Windows.Forms.Label
    $lblLog.Location = '142,654'; $lblLog.Size = '470,40'; $lblLog.Font = $mono
    $btnBack = New-Object System.Windows.Forms.Button
    $btnBack.Text = '< Back'; $btnBack.Location = '628,658'; $btnBack.Size = '104,30'
    $btnNext = New-Object System.Windows.Forms.Button
    $btnNext.Text = 'Next >'; $btnNext.Location = '740,658'; $btnNext.Size = '112,30'; $btnNext.Font = $bold
    $btnCancel = New-Object System.Windows.Forms.Button
    $btnCancel.Text = 'Cancel'; $btnCancel.Location = '864,658'; $btnCancel.Size = '104,30'
    $form.AcceptButton = $btnNext; $form.CancelButton = $btnCancel

    $form.Controls.AddRange(@($header, $sepTop, $pWelcome) + $pages + @($pDone, $sepBot, $btnVerify, $lblLog, $btnBack, $btnNext, $btnCancel))
    # All / List / Info / Out are re-pointed to the current page's controls by Select
    $script:gui.Controls = @{ Form = $form; Title = $lblTitle; Sub = $lblSub; Welcome = $pWelcome; Found = $lblFound; Problem = $lblProblem; Done = $pDone; DoneText = $txtDone
                              DoneNote = $lblDone; Shortcut = $chkLnk; Back = $btnBack; Next = $btnNext; Cancel = $btnCancel; Apply = $btnNext
                              Verify = $btnVerify; Log = $lblLog; All = $items[0].UI.All; List = $items[0].UI.List; Info = $items[0].UI.Info
                              Out = $items[0].UI.Out; Status = $items[0].UI.Status; Res = $items[0].UI.Res }
    $c = $script:gui.Controls   # event handlers run outside this function's scope, so they reach the controls through this table

    # the resolution drop-downs of the two game pages, the largest size with your monitor's aspect preselected
    $script:gui.Syncing = $true
    if ($script:gui.ModeList.Count -gt 0) {
        $first = $null; foreach ($b in $Builds) { if (@($b.Modes).Count -gt 0) { $first = $b; break } }
        $sel = [Math]::Max(0, [Array]::IndexOf($script:gui.ModeList, (Get-PreferredMode $first $script:gui.Monitor)))
        $script:gui.Mode = $script:gui.ModeList[$sel]
        foreach ($it in $items) {
            if (-not $it.UI.Res) { continue }
            foreach ($m in $script:gui.ModeList) { [void] $it.UI.Res.Items.Add((Format-ModeLabel $m $script:gui.Monitor)) }
            $it.UI.Res.SelectedIndex = $sel
        }
    }
    $script:gui.Syncing = $false

    # --- behaviour
    # Repaints an executable's page header lines and the welcome page's list of originals.
    $script:gui.ShowRow = {
        param($it)
        $g = $script:gui
        $u = $it.UI
        $g.Syncing = $true
        $u.Path.Text = if ($it.Path) { $it.Path } else { '(not found beside this script: ' + $it.Build.OriginalPath + ')' }
        $u.Status.Text = if ($it.ContainsKey('Detail')) { $it.Detail } else { "$($it.Build.OriginalName) was not found at $($it.Build.OriginalPath) beside this script. Press Browse to pick it." }
        $u.Status.ForeColor = [System.Drawing.Color]::FromName($it.Color)
        $u.Check.Checked = [bool] $it.Checked
        $u.Check.Enabled = -not $it.Error
        $u.Out.Text = if ($it.Out) { $it.Out } else { '' }
        $u.Error.Visible = [bool] $it.Error
        foreach ($x in $u.Behind) { $x.Visible = -not $it.Error }
        if ($it.Error) { $u.ErrorTitle.Text = $it.Error.Title; $u.ErrorBody.Text = $it.Error.Body; $u.Error.BringToFront() }
        $g.Syncing = $false
        $bad = @($g.Items | Where-Object { $_.Error })
        $g.Controls.Problem.Visible = ($bad.Count -gt 0)
        if ($bad.Count -gt 0) {
            $g.Controls.Problem.Text = (@('PROBLEM - these originals cannot be used as they are:', '') +
                @($bad | ForEach-Object { '    ' + $_.Build.ProductName + ':  ' + $_.Error.Title }) +
                @('', 'Press Next: the page of each one explains what is wrong and offers to select the correct file or to download it.')) -join "`r`n"
        }
        $lines = @('Found:')
        foreach ($x in $g.Items) {
            $mark = if ($x.Data -and $x.IsOriginal) { 'OK ' } elseif ($x.Error) { '!! ' } else { '-- ' }
            $shown = $x.Path
            # beside this script (the normal case): the path relative to the repository folder, which fits the line
            $sep = [System.IO.Path]::DirectorySeparatorChar
            $root = if ($g.Here) { $g.Here.TrimEnd($sep) + $sep } else { $null }
            if ($shown -and $root -and $shown.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { $shown = $shown.Substring($root.Length) }
            elseif ($shown -and $shown.Length -gt 48) { $parts = $shown.Split($sep); if ($parts.Count -gt 2) { $shown = '...' + $sep + $parts[-2] + $sep + $parts[-1] } }
            $lines += ('  {0} {1,-28} {2}' -f $mark, $x.Build.ProductName, $(if ($shown) { "$shown  ($($x.Status))" } else { "not found ($($x.Build.OriginalPath)) - you can pick it on its page" }))
        }
        $g.Controls.Found.Text = $lines -join "`r`n"
    }

    # Re-checks the resources of an item's fixes against the folder its exe will be written to: fixes whose
    # files are missing (or which need such a fix) get "RESOURCES NOT FOUND" and are left out.
    $script:gui.Recheck = {
        param($it)
        $it.Unavailable = @{}
        if ($it.Out) { $it.Unavailable = Get-UnavailableFixes $it.Build (Split-Path -Parent ([System.IO.Path]::GetFullPath($it.Out))) (Get-GuiMode $it.Build) }
    }

    # Puts an item into the error state: nothing to patch, the include box locked, the big red panel on
    # its page with what was found, what is expected and the three ways to get the right file.
    # $kind: missing | unreadable | unknown | patched | modified.
    $script:gui.SetError = {
        param($it, [string] $path, $data, [string] $kind, [string] $why)
        $b = $it.Build
        $name = if ($path) { [System.IO.Path]::GetFileName($path) } else { $b.OriginalName }
        $it.Path = $path; $it.Data = $null; $it.IsOriginal = $false; $it.Checked = $false; $it.Out = $null; $it.Color = 'Firebrick'
        switch ($kind) {
            'missing'    { $title = "$($b.OriginalName) NOT FOUND"; $status = 'NOT FOUND'
                           $found = "nothing at $path" }
            'unreadable' { $title = "$name CANNOT BE READ"; $status = 'cannot be read'
                           $found = "$path`r`n            $why" }
            'unknown'    { $title = "$name IS NOT $($b.OriginalName.ToUpper()) - NOT A DARK COLONY EXECUTABLE"; $status = 'NOT the original - unknown file'
                           $found = "$path`r`n            $($data.Length) bytes, SHA-256 $(Get-Sha256Hex $data)" }
            'patched'    { $title = "$name IS ALREADY PATCHED - NOT THE ORIGINAL"; $status = 'NOT the original - already patched'
                           $found = "$path`r`n            $($data.Length) bytes, SHA-256 $(Get-Sha256Hex $data) (the fixes are already in it)" }
            default      { $title = "$name IS NOT THE UNTOUCHED ORIGINAL"; $status = 'NOT the original - modified copy'
                           $found = "$path`r`n            $($data.Length) bytes, SHA-256 $(Get-Sha256Hex $data)" }
        }
        $it.Status = $status
        $it.Detail = "$title - see the red box below."
        $it.Error = @{
            Title = $title
            Body  = (@(
                "Found:      $found",
                "Expected:   $($b.OriginalName), $($b.Size) bytes, SHA-256 $($b.OriginalSha256)",
                '',
                "The fixes are written for exactly this original, byte by byte, so $($b.ProductName) cannot be patched until the right file is chosen.  What to do:",
                '',
                "  1.  Select the correct file:  press ""Select the correct file..."" below and pick an untouched $($b.OriginalName).",
                "  2.  Download it from our repository:  press ""Download from our repository"" - the file's page opens in your browser.  Download it, save it as ""$($b.OriginalPath)"" in this folder, then press ""Select the correct file..."".",
                "  3.  Take it from the original disc:  $($b.SourceNote)"
            ) -join "`r`n")
        }
    }

    # Loads an exe into the item of its build (the build is recognised from the file, not from the page).
    # $target = the page it was picked on: a file that is no known build puts THAT page into the error state.
    $loadOriginal = {
        param([string] $path, [bool] $select = $true, [int] $target = -1)
        $c = $script:gui.Controls
        $g = $script:gui
        try {
            $path = Get-AbsolutePath $path
            $data = [System.IO.File]::ReadAllBytes($path)
        } catch {
            if ($target -ge 0) {
                $t = $g.Items[$target]; & $g.SetError $t $path $null 'unreadable' $_.Exception.Message
                & $g.Recheck $t; & $g.ShowRow $t; & $g.FillItem $t
            } else { $c.Log.ForeColor = 'Firebrick'; $c.Log.Text = "cannot read: $($_.Exception.Message)" }
            return
        }
        $sha = Get-Sha256Hex $data
        $build = Find-BuildBySha $sha
        $isOriginal = ($null -ne $build)
        if (-not $build) { $build = Find-BuildByContent $data }
        if (-not $build) {
            if ($target -ge 0) {
                $t = $g.Items[$target]; & $g.SetError $t $path $data 'unknown'
                & $g.Recheck $t; & $g.ShowRow $t; & $g.FillItem $t
                if ($select -and $g.Step -eq $target + 1) { & $g.Select $target }
            } else {
                $c.Log.ForeColor = 'Firebrick'
                $c.Log.Text = "$([System.IO.Path]::GetFileName($path)): not a build this script knows ($($data.Length) bytes)."
            }
            return
        }
        $it = $null; foreach ($x in $g.Items) { if ($x.Build.Id -eq $build.Id) { $it = $x } }
        $it.Error = $null
        $it.Path = $path; $it.Data = $data; $it.IsOriginal = $isOriginal; $it.Out = Join-Path (Split-Path $path) $build.OutputName
        $it.Checked = $true; $it.Color = 'DarkGreen'; $it.Status = 'untouched original'
        $it.Detail = "$($build.Title)`r`nSHA-256 $sha = the untouched original."
        if (-not $isOriginal) {
            # a known build, but not the untouched original: an already patched build (players pick the
            # game exe they play as the "original" - report of 21 Sep 2026), or another copy
            $patched = $false
            foreach ($e in $build.Patches[0].Edits) { if ((Get-EditState $data $e) -eq 'new') { $patched = $true } }
            $name = [System.IO.Path]::GetFileName($path)
            if ($patched) {
                $origBeside = Join-Path (Split-Path $path) $build.OriginalName
                $origData = $null
                if (Test-Path -LiteralPath $origBeside) {
                    try { $origData = [System.IO.File]::ReadAllBytes($origBeside) } catch { $origData = $null }
                    if ($origData -and (Get-Sha256Hex $origData) -ne $build.OriginalSha256) { $origData = $null }
                }
                if ($origData) {
                    # the untouched original sits beside it: that is the input, the picked file is the output
                    $it.Path = $origBeside; $it.Data = $origData; $it.IsOriginal = $true; $it.Out = $path
                    $it.Color = 'DarkOrange'; $it.Status = "$name is patched - using $($build.OriginalName) beside it"
                    $it.Detail = "$name is already a patched build, not the untouched original.`r`nUsing $($build.OriginalName) beside it as the input (its SHA-256 is the untouched original); the result replaces $name."
                } else {
                    & $g.SetError $it $path $data 'patched'
                }
            } else {
                # a known layout with another SHA-256: modified or damaged - not patched from here (the command
                # line's -Force still allows it, with every edit byte-checked)
                & $g.SetError $it $path $data 'modified'
            }
        }
        & $g.Recheck $it
        & $g.ShowRow $it
        & $g.FillItem $it
        if ($select -and $g.Step -ge 1 -and $g.Items[$g.Step - 1] -eq $it) { & $g.Select ($g.Step - 1) }
    }
    $script:gui.Load = $loadOriginal

    # (Re)fills one executable's page for the chosen resolution: its fixes, ticked unless unticked by the
    # player or unavailable (resources not found).
    $script:gui.FillItem = {
        param($it)
        $g = $script:gui
        $t = $it.UI
        $g.Syncing = $true
        $t.List.Items.Clear(); $t.All.Checked = $false
        $it.Patches = @()
        if ($it.Data) {
            $it.Patches = @(Get-BuildPatches $it.Build (Get-GuiMode $it.Build))
            $all = $true
            foreach ($p in $it.Patches) {
                $text = '{0}   ({1})' -f $p.Name, $p.Date
                $on = $true
                if ($it.Unavailable.ContainsKey($p.Id)) { $text = '[RESOURCES NOT FOUND]  ' + $text; $on = $false }
                elseif ($it.Unticked.ContainsKey($p.Id)) { $on = $false; $all = $false }
                [void] $t.List.Items.Add($text, $on)
            }
            $t.All.Checked = $all
        }
        $g.Syncing = $false
        $t.List.Enabled = [bool] $it.Data; $t.All.Enabled = [bool] $it.Data
        if ($g.Sel -ge 0 -and $g.Items[$g.Sel] -eq $it) { $g.Patches = $it.Patches }
    }

    # the "Patch <name>" box decides whether the executable is patched; Browse loads an original for it
    $rowCheck = {
        param($sender, $e)
        $g = $script:gui
        if ($g.Syncing) { return }
        $it = $g.Items[[int] $sender.Tag]
        if ($sender.Checked -and -not $it.Data) {
            $g.Syncing = $true; $sender.Checked = $false; $g.Syncing = $false
            $g.Controls.Log.ForeColor = 'Firebrick'; $g.Controls.Log.Text = "$($it.Build.ProductName): no usable original - press Browse to pick $($it.Build.OriginalName)."
        } else {
            $it.Checked = $sender.Checked
            & $g.ShowRow $it
        }
    }
    $rowBrowse = {
        param($sender, $e)
        $c = $script:gui.Controls
        $g = $script:gui
        $it = $g.Items[[int] $sender.Tag]
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Title = "Pick the untouched original $($it.Build.OriginalName) for $($it.Build.ProductName)"
        $dlg.Filter = "$($it.Build.OriginalName)|$($it.Build.OriginalName)|Executables (*.exe)|*.exe|All files (*.*)|*.*"
        # Start in the folder of this page's exe, else in its folder BESIDE THIS SCRIPT: without this the dialog
        # opens where Windows last used PowerShell's file dialogs - with two copies of the repository a player
        # picks the other copy's exe without noticing (22 Sep 2026: "the fresh copy isn't widescreen")
        if ($it.Path) { $dlg.InitialDirectory = Split-Path $it.Path }
        elseif ($g.Here) {
            $dir = Join-Path $g.Here (Split-Path $it.Build.OriginalPath)
            $dlg.InitialDirectory = if (Test-Path -LiteralPath $dir) { $dir } else { $g.Here }
        }
        if ($dlg.ShowDialog($c.Form) -ne 'OK') { return }
        & $g.Load $dlg.FileName $true ([int] $sender.Tag)
        # the file decides the page: say so when it belongs to another one
        $data = $null; try { $data = [System.IO.File]::ReadAllBytes($dlg.FileName) } catch { }
        if ($data) {
            $b = Find-BuildBySha (Get-Sha256Hex $data); if (-not $b) { $b = Find-BuildByContent $data }
            if ($b -and $b.Id -ne $it.Build.Id) {
                $c.Log.ForeColor = 'DarkOrange'
                $c.Log.Text = "$([System.IO.Path]::GetFileName($dlg.FileName)) is the original of $($b.ProductName), not of $($it.Build.ProductName) - loaded on its own page."
            }
        }
    }
    # "Select all" <-> individual boxes of the same page, without the two events feeding each other; fixes
    # whose resources are not found stay unticked in both directions.  The page's controls carry the
    # executable's index in .Tag.
    $tabAll = {
        param($sender, $e)
        $g = $script:gui
        if ($g.Syncing) { return }
        $it = $g.Items[[int] $sender.Tag]
        $g.Syncing = $true
        for ($i = 0; $i -lt $it.UI.List.Items.Count; $i++) {
            $id = $it.Patches[$i].Id
            $avail = -not $it.Unavailable.ContainsKey($id)
            $it.UI.List.SetItemChecked($i, ($sender.Checked -and $avail))
            if ($sender.Checked) { $it.Unticked.Remove($id) } else { $it.Unticked[$id] = $true }
        }
        $g.Syncing = $false
    }
    $tabCheck = {
        param($sender, $e)
        $c = $script:gui.Controls
        $g = $script:gui
        if ($g.Syncing) { return }
        $it = $g.Items[[int] $sender.Tag]
        $id = $it.Patches[$e.Index].Id
        if ($e.NewValue -eq 'Checked' -and $it.Unavailable.ContainsKey($id)) {
            $e.NewValue = 'Unchecked'   # cannot be ticked: resources not found
            $c.Log.ForeColor = 'Firebrick'
            $c.Log.Text = 'Resources not found for this fix in the output folder: ' + $it.Unavailable[$id]
        }
        if ($e.NewValue -eq 'Checked') { $it.Unticked.Remove($id) } else { $it.Unticked[$id] = $true }
        # "Select all" mirrors "every available fix is ticked"
        $all = $true
        for ($i = 0; $i -lt $sender.Items.Count; $i++) {
            if ($it.Unavailable.ContainsKey($it.Patches[$i].Id)) { continue }
            $checked = if ($i -eq $e.Index) { $e.NewValue -eq 'Checked' } else { $sender.GetItemChecked($i) }
            if (-not $checked) { $all = $false }
        }
        $g.Syncing = $true; $it.UI.All.Checked = $all; $g.Syncing = $false
    }
    # the page's description box: what its highlighted fix changes
    $script:gui.ShowFix = {
        param($it)
        $c = $script:gui.Controls
        $idx = $it.UI.List.SelectedIndex
        if ($idx -lt 0 -or $idx -ge $it.Patches.Count) { return }
        $p = $it.Patches[$idx]
        $lines = @()
        if ($it.Unavailable.ContainsKey($p.Id)) {
            $lines += @('RESOURCES NOT FOUND - this fix cannot be applied into the output folder:', ('  ' + $it.Unavailable[$p.Id]),
                        '  Copy the game folder from the repository (https://github.com/endotermic/Dark-Colony), or write', '  the exe into it.', '')
        }
        $lines += @(
            ('{0}: {1}' -f $it.Build.ProductName, $p.Name), ('=' * ($it.Build.ProductName.Length + 2 + $p.Name.Length)),
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
        $reqLines = @(Get-RequirementLines $it.Build $p)
        if ($reqLines.Count -gt 0) {
            $lines += @('', 'Prerequisites (checked before anything is written):')
            foreach ($l in $reqLines) { $lines += ('  * ' + $l) }
        }
        $lines += @('', 'Byte edits (file offset: old bytes -> new bytes):', '') + (Get-EditLines $p)
        $it.UI.Info.Text = $lines -join "`r`n"
        $it.UI.Info.SelectionStart = 0; $it.UI.Info.SelectionLength = 0; $it.UI.Info.ScrollToCaret()
    }
    $tabSelect = {
        param($sender, $e)
        $g = $script:gui
        & $g.ShowFix $g.Items[[int] $sender.Tag]
    }

    # the controls of every executable page
    $resChange = {
        param($sender, $e)
        $g = $script:gui
        if ($g.Syncing -or $sender.SelectedIndex -lt 0) { return }
        $g.Mode = $g.ModeList[$sender.SelectedIndex]
        # both games share the INTRF_HD folder: one resolution, shown on both pages
        $g.Syncing = $true
        foreach ($x in $g.Items) { if ($x.UI.Res -and $x.UI.Res -ne $sender) { $x.UI.Res.SelectedIndex = $sender.SelectedIndex } }
        $g.Syncing = $false
        foreach ($x in $g.Items) { & $g.Recheck $x; & $g.FillItem $x }
        if ($g.Sel -ge 0) { & $g.Select $g.Sel }
    }
    foreach ($it in $script:gui.Items) {
        $u = $it.UI
        $u.Check.Add_CheckedChanged($rowCheck)
        $u.Browse.Add_Click($rowBrowse)
        $u.ErrorPick.Add_Click($rowBrowse)
        $u.ErrorGet.Add_Click({ param($sender, $e) Start-Process $script:gui.Items[[int] $sender.Tag].Build.RepoUrl })
        $u.All.Add_CheckedChanged($tabAll)
        $u.List.Add_ItemCheck($tabCheck)
        $u.List.Add_SelectedIndexChanged($tabSelect)
        if ($u.Res) { $u.Res.Add_SelectedIndexChanged($resChange) }
    }

    # Popups (22 Sep 2026, maintainer request "show a popup when patching is in progress and when it
    # succeeds and when it fails"): while the bytes and the interface set are written a small owned
    # "Patching in progress" box names the current step (the run is synchronous on the UI thread, so
    # the box is repainted by hand between the steps and the main window is disabled meanwhile), and
    # the outcome of all executables is one message box.  Every box goes through $script:gui.Notify so a
    # headless test can replace it with a recorder; the button passes $interactive = $true, a test may
    # pass $false for silence.
    $script:gui.Notify = {
        param([string] $text, [string] $title, [string] $icon)
        [System.Windows.Forms.MessageBox]::Show($script:gui.Controls.Form, $text, $title, 'OK', $icon) | Out-Null
    }
    $script:gui.Busy = $null
    $script:gui.StepPrefix = ''
    $script:gui.Progress = {
        param([string] $step)
        $c = $script:gui.Controls
        $step = $script:gui.StepPrefix + $step
        $c.Log.ForeColor = 'Black'; $c.Log.Text = $step
        $b = $script:gui.Busy
        if ($b) {
            $b.Label.Text = "Patching in progress - please wait.`r`n`r`n$step"
            $b.Form.Refresh()
            [System.Windows.Forms.Application]::DoEvents()
        }
    }

    # Patches every ticked executable.  Returns one result per executable:
    # @{ Item; R (Invoke-PatchRun's result or $null); Error; Shortcut; Kind = ok|warning|error; Line }.
    $script:gui.Apply = {
        param([bool] $interactive)
        $c = $script:gui.Controls
        $g = $script:gui
        $todo = @($g.Items | Where-Object { $_.Checked -and $_.Data })
        $refused = $null
        if ($todo.Count -eq 0) { $refused = 'No executable ticked - tick at least one (its original must be found).' }
        foreach ($it in $todo) {
            if ($refused) { break }
            if (@(Get-GuiChosen $it).Count -eq 0) { $refused = "$($it.Build.ProductName): no fix selected - tick at least one fix, or untick the executable." }
            elseif ([System.IO.Path]::GetFullPath($it.Out) -eq [System.IO.Path]::GetFullPath($it.Path)) { $refused = "$($it.Build.ProductName): the output must not be the original file - the original is never written over." }
        }
        if ($refused) {
            $c.Log.ForeColor = 'Firebrick'; $c.Log.Text = $refused
            if ($interactive) { & $g.Notify $refused 'Nothing to do' 'Warning' }
            return $null
        }
        $problems = @()
        foreach ($it in $todo) {
            foreach ($pr in @(Get-DataProblems $it.Build @(Get-GuiChosen $it) (Split-Path -Parent ([System.IO.Path]::GetFullPath($it.Out))) (Get-GuiMode $it.Build))) {
                $problems += ('* {0}: {1}' -f $it.Build.ProductName, $pr)
            }
        }
        if ($problems.Count -gt 0) {
            $c.Log.ForeColor = 'Firebrick'; $c.Log.Text = 'Nothing written: data files or dependent fixes are missing (see the message).'
            & $g.Notify (($problems -join "`r`n`r`n") +
                "`r`n`r`nAn exe written without them fails at start-up or draws garbage, which would look like a bug of the fix. " +
                "Write the exes into the game folders from the repository, or run the script from the command line with -IgnoreMissingData.") `
                'Prerequisites missing - nothing written' 'Warning'
            return $null
        }
        $existing = @($todo | Where-Object { Test-Path -LiteralPath $_.Out } | ForEach-Object { $_.Out })
        if ($existing.Count -gt 0 -and $interactive) {
            $answer = [System.Windows.Forms.MessageBox]::Show($c.Form, ("These files exist and will be replaced:`r`n`r`n" + ($existing -join "`r`n") + "`r`n`r`nReplace them?"), 'Replace files?', 'YesNo', 'Question')
            if ($answer -ne 'Yes') { return $null }
        }
        # the "in progress" box: an owned, unclosable form with the current step and a marquee bar
        if ($interactive) {
            $busy = New-Object System.Windows.Forms.Form
            $busy.Text = 'Patching in progress'
            $busy.FormBorderStyle = 'FixedDialog'; $busy.ControlBox = $false; $busy.ShowInTaskbar = $false
            $busy.Size = New-Object System.Drawing.Size(560, 190)
            $busy.StartPosition = if ($c.Form.Visible) { 'CenterParent' } else { 'CenterScreen' }
            $busy.Font = $c.Form.Font
            $busyLabel = New-Object System.Windows.Forms.Label
            $busyLabel.Location = '16,16'; $busyLabel.Size = '512,96'
            $busyLabel.Text = "Patching in progress - please wait.`r`n`r`nPatching $($todo.Count) executable(s)..."
            $busyBar = New-Object System.Windows.Forms.ProgressBar
            $busyBar.Location = '16,120'; $busyBar.Size = '512,20'; $busyBar.Style = 'Marquee'; $busyBar.MarqueeAnimationSpeed = 30
            $busy.Controls.AddRange(@($busyLabel, $busyBar))
            $c.Form.Enabled = $false; $c.Form.UseWaitCursor = $true
            $busy.Show($c.Form)
            $g.Busy = @{ Form = $busy; Label = $busyLabel }
            $busy.Refresh(); [System.Windows.Forms.Application]::DoEvents()
        }
        $results = @()
        try {
            $k = 0
            foreach ($it in $todo) {
                $k++
                $g.StepPrefix = "[$k/$($todo.Count)] $($it.Build.ProductName): "
                $mode = Get-GuiMode $it.Build
                $res = @{ Item = $it; R = $null; Error = $null; Shortcut = $null; Kind = 'ok'; Line = '' }
                try {
                    $res.R = Invoke-PatchRun $it.Path $it.Build @(Get-GuiChosen $it) $it.Out $mode $g.Progress
                } catch {
                    $res.Error = $_.Exception.Message
                }
                $r = $res.R
                $name = $it.Build.ProductName
                $modeText = if ($mode) { " for $mode" } else { '' }
                if ($res.Error) {
                    $res.Kind = 'error'; $res.Line = "$name - FAILED, nothing written:`r`n    $($res.Error)"
                } else {
                    $notWritten = @($r.Generated | Where-Object { $_ -match 'NOT WRITTEN|NOT written' })
                    if ($notWritten.Count -gt 0) {
                        $res.Kind = 'error'
                        $res.Line = "$name - the exe was written, but the data files it needs were NOT:`r`n    " + ($notWritten -join "`r`n    ") + "`r`n    The game would fail at start-up with it; fix the cause (a read-only or locked folder?) and patch again."
                    } elseif ($r.Complete -and $r.Published) {
                        $res.Line = "$name - all $($r.Applied.Count) fixes$modeText, byte-identical to the exe published in the repository"
                    } elseif ($r.Complete -and $r.Matches) {
                        $res.Line = "$name - all $($r.Applied.Count) fixes$modeText, byte-identical to the reference build for $mode"
                    } elseif ($r.Complete) {
                        $res.Kind = 'warning'
                        $res.Line = "$name - all $($r.Applied.Count) fixes$modeText, but the SHA-256 differs from the reference build (please report it)"
                    } else {
                        $res.Line = "$name - $($r.Applied.Count) of $(@(Get-BuildPatches $it.Build $mode).Count) fixes$modeText (" + (($r.Applied | ForEach-Object { $_.Id }) -join ', ') + ')'
                    }
                    $res.Line += "`r`n    $($it.Out)`r`n    $($r.Size) bytes, SHA-256 $($r.Sha256)"
                    if ($r.Generated.Count -gt 0 -and $res.Kind -ne 'error') { $res.Line += "`r`n    " + ($r.Generated -join '; ') }
                    # the desktop shortcut, unless the exe is unusable because its data files were not written
                    if ($res.Kind -ne 'error' -and $c.Shortcut.Checked) {
                        try {
                            $res.Shortcut = New-GameShortcut $it.Out $it.Build
                            $res.Line += "`r`n    desktop shortcut: $([System.IO.Path]::GetFileName($res.Shortcut))"
                        } catch {
                            $res.Line += "`r`n    the desktop shortcut could not be created: $($_.Exception.Message)"
                            if ($res.Kind -eq 'ok') { $res.Kind = 'warning' }
                        }
                    }
                }
                $results += $res
            }
        } finally {
            $g.StepPrefix = ''
            if ($g.Busy) {
                $g.Busy.Form.Close(); $g.Busy.Form.Dispose(); $g.Busy = $null
                $c.Form.Enabled = $true; $c.Form.UseWaitCursor = $false
            }
        }
        $errors = @($results | Where-Object { $_.Kind -eq 'error' }).Count
        $warnings = @($results | Where-Object { $_.Kind -eq 'warning' }).Count
        if ($errors -eq $results.Count) { $title = 'Patching failed'; $icon = 'Error'; $c.Log.ForeColor = 'Firebrick' }
        elseif ($errors -gt 0) { $title = 'Patching finished with errors'; $icon = 'Error'; $c.Log.ForeColor = 'Firebrick' }
        elseif ($warnings -gt 0) { $title = 'Patched, with warnings'; $icon = 'Warning'; $c.Log.ForeColor = 'DarkOrange' }
        else { $title = 'Patching succeeded'; $icon = 'Information'; $c.Log.ForeColor = 'DarkGreen' }
        $ok = $results.Count - $errors
        $c.Log.Text = "$ok of $($results.Count) executable(s) patched" + $(if ($errors) { ", $errors failed" } else { '' }) + ' - see the message for details.'
        $text = ($results | ForEach-Object { $_.Line }) -join "`r`n`r`n"
        if ($ok -gt 0) { $text += "`r`n`r`nStart the games with the desktop shortcuts or the files above." }
        if ($interactive) { & $g.Notify $text $title $icon }
        return $results
    }

    # Makes executable $index the current one: the aliases All / List / Info / Out / Status / Res point at its
    # page's controls, and the description shows its highlighted fix.
    $script:gui.Select = {
        param([int] $index)
        $c = $script:gui.Controls
        $g = $script:gui
        $g.Sel = $index
        if ($index -lt 0) { return }
        $it = $g.Items[$index]
        $u = $it.UI
        $c.All = $u.All; $c.List = $u.List; $c.Info = $u.Info; $c.Out = $u.Out; $c.Status = $u.Status; $c.Res = $u.Res
        $g.Patches = $it.Patches
        $n = 0; foreach ($k in $it.Unavailable.Keys) { $n++ }
        if ($n -gt 0) { $c.Log.ForeColor = 'DarkOrange'; $c.Log.Text = "$($it.Build.ProductName): $n fix(es) cannot be applied into its folder - resources not found." }
        elseif ((Get-GuiMode $it.Build) -eq '640x480' -and @($it.Build.Modes).Count -gt 0) { $c.Log.ForeColor = 'Black'; $c.Log.Text = '640x480 = the stock screen size: the display fixes (resolution, INTRF_HD paths, clock) are not offered.' }
        else { $c.Log.Text = '' }
        if ($u.List.SelectedIndex -lt 0 -and $u.List.Items.Count -gt 0) { $u.List.SelectedIndex = 0 }
        else { & $g.ShowFix $it }
    }

    # Shows wizard step $step: 0 welcome, 1..n the executables, n+1 finished.
    $script:gui.GoTo = {
        param([int] $step)
        $c = $script:gui.Controls
        $g = $script:gui
        $n = $g.Items.Count
        $g.Step = $step
        $c.Welcome.Visible = ($step -eq 0)
        for ($i = 0; $i -lt $n; $i++) { $g.Items[$i].UI.Page.Visible = ($step -eq $i + 1) }
        $c.Done.Visible = ($step -eq $n + 1)
        if ($step -eq 0) {
            $c.Title.Text = 'Welcome to the Dark Colony patcher'
            $c.Sub.Text = 'Builds Dark Colony, Dark Colony Ultimate and the map editor 1.2 from the untouched originals in this folder.'
        } elseif ($step -le $n) {
            $b = $g.Items[$step - 1].Build
            $c.Title.Text = "Step $step of ${n}: $($b.ProductName)"
            $c.Sub.Text = "$($b.OriginalName) -> $($b.OutputName).  All fixes are selected; untick what you do not want, or untick the executable to leave it alone."
            & $g.Select ($step - 1)
        } else {
            $r = @($g.Results)
            $errors = @($r | Where-Object { $_.Kind -eq 'error' }).Count
            $c.Title.Text = if ($errors -eq 0) { 'Finished' } elseif ($errors -lt $r.Count) { 'Finished, with errors' } else { 'Patching failed' }
            $c.Sub.Text = '{0} of {1} executable(s) patched.' -f ($r.Count - $errors), $r.Count
            $c.DoneText.Text = ($r | ForEach-Object { $_.Line }) -join "`r`n`r`n"
            $c.DoneNote.Text = if ($errors -lt $r.Count) { 'Start the games with the desktop shortcuts or the files above.  Press Close to leave.' } else { 'Nothing usable was written - see above.  Press Close to leave.' }
            $c.Log.Text = ''
        }
        $c.Back.Enabled = ($step -gt 0 -and $step -le $n)
        $c.Next.Text = if ($step -eq $n) { 'Patch' } elseif ($step -eq $n + 1) { 'Close' } else { 'Next >' }
        $c.Cancel.Enabled = ($step -le $n)
    }
    $c.Next.Add_Click({
        $g = $script:gui
        $n = $g.Items.Count
        if ($g.Step -lt $n) { & $g.GoTo ($g.Step + 1); return }
        if ($g.Step -eq $n) {
            $r = & $g.Apply $true
            if ($r) { $g.Results = $r; & $g.GoTo ($n + 1) }
            return
        }
        $g.Controls.Form.Close()
    })
    $c.Back.Add_Click({ $g = $script:gui; if ($g.Step -gt 0) { & $g.GoTo ($g.Step - 1) } })
    $c.Cancel.Add_Click({ $script:gui.Controls.Form.Close() })

    $c.Verify.Add_Click({
        $c = $script:gui.Controls
        $g = $script:gui
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Title = 'Inspect an executable: which fixes does it carry?'
        $dlg.Filter = 'Dark Colony executables (*.exe)|*.exe|All files (*.*)|*.*'
        if ($dlg.ShowDialog($c.Form) -eq 'OK') {
            $report = (Get-VerifyReport $dlg.FileName) -join "`r`n"
            if ($g.Step -ge 1 -and $g.Step -le $g.Items.Count) { $c.Info.Text = $report; $c.List.ClearSelected() }
            else { & $g.Notify $report 'Inspect an exe' 'Information' }
        }
    })

    # the three originals beside this script, all ticked; then the exe given with -Original, if any
    foreach ($it in $script:gui.Items) {
        $p = if ($script:gui.Here) { Join-Path $script:gui.Here $it.Build.OriginalPath } else { $null }
        if ($p -and (Test-Path -LiteralPath $p)) { & $loadOriginal $p $false ([Array]::IndexOf($script:gui.Items, $it)) }
        else { & $script:gui.SetError $it $p $null 'missing'; & $script:gui.ShowRow $it; & $script:gui.FillItem $it }
    }
    if ($PreloadPath) { & $loadOriginal ((Resolve-Path $PreloadPath).Path) $false }
    & $script:gui.GoTo 0
    return $form
}

# =================================================================================================
#  ENTRY POINT
# =================================================================================================
if ($PSCmdlet.ParameterSetName -eq 'List') { Write-PatchList -WithEdits:$Detail; return }

if ($PSCmdlet.ParameterSetName -eq 'Verify') { Get-VerifyReport $Verify | ForEach-Object { Write-Host $_ }; return }

# No -All / -Patches: open the window (INSTALL.CMD, "Run with PowerShell", or just `.\Apply-DarkColonyPatches.ps1`)
if (-not $All -and -not $Patches) {
    $form = Show-PatcherWindow $Original
    [void] $form.ShowDialog()
    return
}

# --- command-line apply: one original (-Original), or - with -All and no -Original - all three originals
# beside this script, each written under its own name (25 Sep 2026: "installer patches all in one shot")
function Invoke-CliBuild([string] $OriginalFile, [string] $OutputFile) {
    $origPath = (Resolve-Path $OriginalFile).Path
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
    if (-not $OutputFile) { $OutputFile = Join-Path (Split-Path $origPath) $build.OutputName }
    $OutputFile = Get-AbsolutePath $OutputFile
    $gameDir = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputFile))
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
    if ((Test-Path $OutputFile) -and -not $Overwrite) { throw "output '$OutputFile' exists; pass -Overwrite to replace it" }
    if ((Test-Path $OutputFile) -and ((Resolve-Path $OutputFile).Path -eq $origPath)) { throw 'refusing to overwrite the original' }

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
    $r = Invoke-PatchRun $origPath $build $chosen $OutputFile $mode
    Write-Host ''
    Write-Host ("output: {0}" -f $OutputFile)
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
    if ($DesktopShortcut) {
        if (@($r.Generated | Where-Object { $_ -match 'NOT WRITTEN|NOT written' }).Count -gt 0) {
            Write-Warning 'no desktop shortcut: the data files this exe needs were not written (see above).'
        } else {
            Write-Host ("shortcut: {0}" -f (New-GameShortcut $OutputFile $build))
        }
    }
}

if ($Original) { Invoke-CliBuild $Original $Output; return }
if ($Patches) { throw 'give -Original <exe> together with -Patches (the fix ids differ per executable)' }
if ($Output) { throw '-Output needs -Original (with -All alone each executable is written under its own name beside its original)' }
$failed = 0; $done = 0
foreach ($b in $Builds) {
    $p = Join-Path $PSScriptRoot $b.OriginalPath
    Write-Host ''
    Write-Host ('=== {0}  ({1} -> {2})' -f $b.ProductName, $b.OriginalPath, $b.OutputName) -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $p)) { Write-Warning ('{0} not found at {1} - skipped' -f $b.OriginalName, $p); continue }
    try { Invoke-CliBuild $p $null; $done++ } catch { Write-Warning ('{0}: {1}' -f $b.ProductName, $_.Exception.Message); $failed++ }
}
Write-Host ''
Write-Host ('{0} executable(s) patched, {1} failed.' -f $done, $failed)
if ($failed -gt 0 -or $done -eq 0) { exit 1 }
''')

open(OUT, 'w', encoding='utf-8', newline='\r\n').write('\n'.join(out))
shutil.rmtree(WORK, ignore_errors=True)
print('wrote', OUT, os.path.getsize(OUT), 'bytes')

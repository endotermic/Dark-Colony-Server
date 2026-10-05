"""Generate Apply-DarkColonyPatches.ps1 - the self-documenting PowerShell patcher that lives in the ROOT of
the Dark-Colony game repository (next to "DC - Classic" and "DC - Council wars", maintainer decision 14 Sep 2026).

The PowerShell script lets a player rebuild the patched "Dark Colony Ultimate.exe" (until 25 Sep 2026
engexp16new.exe) and the map editor from the
untouched originals committed in the Dark-Colony repository, one patch at a time, with every changed
byte listed and explained.  This generator produces it by *replaying* the Python patch tools of this
folder on copies of the originals, diffing after each step (so every byte is attributed to
exactly one patch) and taking the per-edit descriptions from the tools' `plan` output.

    python tools/gen_apply_script.py "<path to Dark-Colony>" "<path to Dark-Colony>/Apply-DarkColonyPatches.ps1"

Needs: DC - Council wars/ENGEXP16.EXE (the untouched Council Wars exe; the deprecated Classic build - dc16.exe
of 7 Jan 1998 -> "Dark Colony.exe" - left this installer on 5 Oct 2026, Dark Colony Ultimate plays its
campaign), Dark Colony - Map editor/maped.exe and DC - Council wars/DC_HD.ICO (the icon of fix `icon`) in the game repository, and the
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
import re, struct, hashlib, sys, os, shutil, subprocess, tempfile, base64, json

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
from hdfolder import hd_folder, hd_token, SRC_DIR     # one interface folder per resolution (2 Oct 2026, doc 10.61)
import gen_disc_install                                # the disc install and the resource copy (5 Oct 2026)
import resources as resmod                            # where the patcher's resources live (patcher/game, patcher/editor)
GAME = sys.argv[1]
OUT = sys.argv[2]
# Since 5 Oct 2026 (maintainer: "put all patcher's resources and scripts (except installer.cmd and PATCH_HOWTO.TXT)
# into a separate folder", then "don't delete files from where they was! You must use 'patcher' directory as a source
# from where you take resources and copy to the places where they must reside") the generated script lives in
# <repo>/patcher/ with a COPY of the project's own data files beside it: patcher/game/<rel> is copied into the game
# folder, patcher/editor/<rel> into the map editor's folder, before a build is patched.  The game folders of the
# repository keep every file where it was (the checkout stays playable); tools/resources.py sync|check keep the two
# sides equal.  Data lists are enumerated from the game folders (_tree) and the copies (_rtree) alike.
RES_DIR = {'cw': os.path.join(GAME, resmod.PATCHER_DIR, 'game'),
           'maped': os.path.join(GAME, resmod.PATCHER_DIR, 'editor')}
MANIFEST = json.load(open(os.path.join(TOOLS, 'disc_manifest.json'), encoding='utf-8'))   # which disc holds which stock file (tools/discs.py)

# Version and build number of the generated installer (maintainer, 2 Oct 2026: "add version number and build
# number to the installer").  PATCHER_VERSION is set by hand here whenever the patcher's behaviour changes (its
# window, its options, a new or removed fix); the BUILD is derived at every generation: the UTC time of the run
# (YYYYMMDD.HHMM, unique and sortable) plus the commits of the two repositories the file was generated from
# (short hash, "+" when the working tree had uncommitted changes).  Both are shown in the window title, on the
# welcome page, in the result box and in the command-line banner, and written into the script's header.
PATCHER_VERSION = '2.4'   # 2.4: fix intro (no start-up movie; the campaign buttons play theirs); 2.0 (5 Oct 2026): patcher/ folder, resources beside the script, install from the two discs; 2.1: the soundtrack ripped from the discs; 2.2: ozisave\ozisave.txt created, not carried; 2.3: the deprecated Dark Colony build removed


def _git_state(repo):
    """'<short sha>[+]' of a repository's HEAD, '?' when git or the repository is unavailable."""
    try:
        sha = subprocess.run(['git', '-C', repo, 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', '-C', repo, 'status', '--porcelain'], capture_output=True, text=True, check=True).stdout.strip() != ''
        return sha + ('+' if dirty else '')
    except Exception:
        return '?'


import datetime as _dt
_NOW = _dt.datetime.now(_dt.timezone.utc)
PATCHER_BUILD = _NOW.strftime('%Y%m%d.%H%M')
PATCHER_GENERATED = '%s UTC from Dark-Colony-Server %s and Dark-Colony %s' % (
    _NOW.strftime('%Y-%m-%d %H:%M'), _git_state(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), _git_state(GAME))
WORK = tempfile.mkdtemp(prefix='dcpatch_')

# Since 15 Sep 2026 both games run from the Council Wars folder (maintainer decision): the original keeps its
# stock name "DC - Council wars/ENGEXP16.EXE" and the patched build is "Dark Colony Ultimate.exe" beside it
# (engexp16new.exe until 25 Sep 2026, DCEXP16.EXE from 10 to 15 Sep 2026).  The Classic build (dc16.exe ->
# "Dark Colony.exe", deprecated 1 Oct 2026) was removed from the installer on 5 Oct 2026 (maintainer: "remove
# deprecated 'Dark colony' option from installer completely as won't be needed anymore"); the untouched
# dc16.exe stays in the repository, the Python tools still patch it by hand.
ORIGINALS = {'cw': os.path.join(GAME, 'DC - Council wars', 'ENGEXP16.EXE'),
             'maped': os.path.join(GAME, 'Dark Colony - Map editor', 'maped.exe')}
GAME_DIR = {'cw': os.path.join(GAME, 'DC - Council wars'),
            'maped': os.path.join(GAME, 'Dark Colony - Map editor')}

# Screen resolutions (21 Sep 2026, maintainer request: a drop-down in the patcher).  '640x480' is the
# stock mode = the build without its HD fixes; every HD mode is a separate replay of the two tools
# that take --width/--height (`resolution`, `clock`), emitted as per-mode variants of those fixes
# (same Id, `Mode` field).  Since 2 Oct 2026 every HD mode has its OWN interface folder (HD_<height>P), so
# the path strings differ per mode too (part of the one `resolution` fix since
# 1 Oct 2026).  Doc 10.25, 10.58.
STOCK_MODE = '640x480'
HD_MODES = ['1024x768', '1280x1024', '1280x720', '1280x800', '1920x1080', '1920x1200', '3840x1080']  # several sizes per aspect ratio since 27 Sep 2026 (maintainer: 1920x1080, 1920x1200; until then one per ratio, 22 Sep 2026). The window preselects the LAST recommended mode of this list, so within one ratio the larger size must come after the smaller
PUBLISHED_MODE = '1024x768'
PUBLISHED_FOLDER = hd_folder(1024, 768)          # HD_0768P: the interface set the repository ships (2 Oct 2026)         # the mode of the exes published in the repository (NOT a default: since 1 Oct 2026 the
                                    # resolution is always chosen explicitly - window page 1, CLI -Resolution)
# tools replayed per mode (--width/--height): the display fixes differ per size; `movies` and `ozi` have
# a 640x480 variant (the exe is pointed at copies of the lists / the menu script that the original exe
# never reads, since the stock files must stay untouched) and one shared HD variant
MODE_STEPS = {'resolution', 'hdpaths', 'clock', 'ozi', 'music'}   # hdpaths since 2 Oct 2026: the folder is the size's own
HD_STEPS = {'resolution', 'hdpaths', 'clock', 'console'}   # replay steps that do not exist in the stock mode
# The battlefield interface theme (1 Oct 2026, maintainer: "dark mode must be optional but not preselected, customer
# must be forced to select light mode (classic) or dark mode of battlefield interface"): the console-style HUD of
# 28-30 Sep 2026 (doc 10.49-10.55) is the DARK theme = data (INTRF_HD\<WxH>\INTRFACE.GIF, the banks INTRF_HD\MAINBUT.SPR /
# POPP.SPR, SPRITES\CLOCK.SPR, the console script edits) plus ONE exe edit, the clock bank string `sprites/cloc` ->
# `sprites/clock` = fix `console` (Theme 'dark', HD only).  The LIGHT theme = the stock metal interface: the shipped
# INTRF_HD\<WxH>\INTRFACE_LIGHT.GIF (hud_layout.py build), the stock banks, no console edits, no `console` fix.
THEMES = ('light', 'dark')
# 1 Oct 2026 (maintainer: "combine all patches related to resolution change into one single patch"): the
# three display tools are replayed one after the other and attributed as ONE fix `resolution` per HD size
# (until then the fixes `resolution`, `hdpaths` and `clock`, with Requires between them).  A fix id in
# COMPOSITE names the consecutive replay steps folded into it; FIX_OF maps a step to its fix.
COMPOSITE = {'resolution': ['resolution', 'hdpaths', 'clock']}
FIX_OF = {s: f for f, ss in COMPOSITE.items() for s in ss}
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


def _rtree(g, *parts, pattern=None):
    """Like _tree, for the patcher's resource folder of the build (patcher/game | patcher/editor)."""
    key = 'res:' + g
    if key not in _LS_FILES:
        sub = os.path.relpath(RES_DIR[g], GAME).replace('\\', '/')
        r = subprocess.run(['git', '-C', GAME, 'ls-files', '--', sub], capture_output=True, text=True, check=True)
        _LS_FILES[key] = [l[len(sub) + 1:] for l in r.stdout.splitlines() if l.startswith(sub + '/')]
    prefix = '/'.join(parts) + '/' if parts else ''
    out = []
    for rel in _LS_FILES[key]:
        if not rel.lower().startswith(prefix.lower()):
            continue
        name = rel.rsplit('/', 1)[-1]
        if pattern and not re.search(pattern, name, re.I):
            continue
        out.append(rel.replace('/', '\\'))
    return sorted(out, key=str.lower)


def has_data(g, rel):
    """A fix's data file exists in the repository's game folder or among the resources (the patcher checks both)."""
    r = rel.replace('\\', os.sep)
    return os.path.exists(os.path.join(GAME_DIR[g], r)) or os.path.exists(os.path.join(RES_DIR[g], r))


def hd_data(g, mode=None):
    """Data files the 1024x768 exe needs (patches `resolution` + `hdpaths`): the INTRF_HD tree, the
    re-baked logo banks and their FINs, and Council Wars' exp/intrf_hd overrides.  Enumerated from
    the game repository at generation time so the list is exact."""
    # Since 21 Sep 2026 the patcher GENERATES the INTRF_HD set (Write-InterfaceSet), so what it needs
    # are the stock inputs: the INTRFACE scripts, pictures, FIN lists and loading screens the set is
    # derived from (named after the repository's INTRF_HD: every output has a same-named input,
    # except the four shipped pictures, see set_sources), the four GAMESTAT briefing lists, the
    # shared re-baked logo banks, and for Council Wars the exp\ overrides and the OZI lists.
    hd = [f.rsplit('\\', 1)[-1] for f in _tree(g, PUBLISHED_FOLDER) if '\\' not in f[len(PUBLISHED_FOLDER) + 1:]]
    files = []
    for name in hd:
        if name.upper() in SHIPPED_PICTURES:
            continue                                    # shipped per size (set_sources)
        if name.upper().endswith('SCENE.TXT'):
            continue                                    # the briefing lists come from GAMESTAT (below)
        if name.upper().endswith('.SPR'):
            continue                                    # the console-style banks belong to the dark theme (console_data)
        if name.upper() in ('ONLINE', 'ONLINEBG.GIF', 'REPLAYE', 'REPLAYBG.GIF', 'LOADALLE'):
            continue                                    # written by Write-OnlineScreen from LOADGE / LOADER.GIF (fix online, doc 10.51 / 10.65 / 10.67)
        src = os.path.join(GAME_DIR[g], 'INTRFACE', name)
        assert os.path.exists(src), src
        files.append('INTRFACE\\' + name)
    files += ['GAMESTAT\\' + x for x in ('HSCENE.TXT', 'GSCENE.TXT', 'HTSCENE.TXT', 'GTSCENE.TXT')]
    files += _rtree(g, 'SPRITES', pattern=r'_HD\.SPR$') + _rtree(g, 'ANIMATE', pattern=r'_HD\.FIN$')   # the re-baked logo banks are resources (5 Oct 2026)
    if g == 'cw':
        files += ['exp\\intrface\\' + x for x in ('bintroe', 'introe', 'shumane')]
        files += ['exp\\gamestat\\' + x for x in ('hxscene.txt', 'gxscene.txt')]
        files += ['ozi_ns\\gamestat\\' + x for x in ('hxscene.txt', 'gxscene.txt')]
    for f in files:
        assert has_data(g, f), (g, f)
    assert 55 <= len(files) <= 75, (g, len(files))
    return files


SHIPPED_PICTURES = ('INTRG.GIF', 'INTRO.GIF', 'BACKDROP.GIF', 'INTRFACE.GIF', 'INTRFACE_LIGHT.GIF')


def set_sources(g, mode):
    """The four pictures of a resolution that cannot be derived: the painted main-menu backdrops
    (INTRG / INTRO with their bottom bands, BACKDROP without one - the pre-battle screens' ground,
    doc 10.56) and the spliced HUD frame, shipped as HD_SRC\\<WxH>\\*.GIF (until 1 Oct 2026 INTRF_HD\\<WxH>)."""
    out = ['%s\\%s\\%s' % (SRC_DIR, mode, x) for x in SHIPPED_PICTURES]
    for f in out:
        assert os.path.exists(os.path.join(RES_DIR[g], f.replace('\\', os.sep))), (g, f)   # a resource (patcher/game) since 5 Oct 2026
    return out


def online_data(g, mode=None):
    """The ONLINE WAR / REPLAY ONLINE GAME fix's resource (2 Oct 2026): the radio-box bank HD_SRC\\KNOBR.SPR
    (patch_online.py bank: KNOBE + the empty box), read by the REPLAYE screen as `pictures hd_src/knobr`."""
    out = [SRC_DIR + '\\KNOBR.SPR']
    for f in out:
        assert os.path.exists(os.path.join(RES_DIR[g], f.replace('\\', os.sep))), (g, f)
    return out


def console_data(g, mode=None):
    """The dark theme's banks (fix `console`): the console-style HUD cells and dialog plates, and the
    redrawn clock dial the exe reads as sprites/clock (hud_console.py, doc 10.49)."""
    out = [SRC_DIR + '\\MAINBUT.SPR', SRC_DIR + '\\POPP.SPR', 'SPRITES\\CLOCK.SPR']
    for f in out:
        assert os.path.exists(os.path.join(RES_DIR[g], f.replace('\\', os.sep))), (g, f)
    return out


def ozi_data(g, mode=None):
    """Data files the OZI MISSIONS mode needs: the whole ozi_ns/ overlay and the pack's base-set
    additions in exp/ (animozi.dat, the new units, the tranozi transport).  NOT ozisave\\ozisave.txt: the
    save folder's marker is created by the patcher itself (Write-OziSaveFolder) - "patcher must not
    carry ozisave, ozisave.txt must be created from scratch" (maintainer, 5 Oct 2026)."""
    # not the pack's interface set copies ozi_ns\HD_0768P\ (the patcher writes them per resolution and deletes the other
    # sizes' folders - 2 Oct 2026): a Data file that a run deletes would make the fix "unavailable" at every other size
    # since 5 Oct 2026 the pack's own files are resources (patcher/game/ozi_ns ...); the ozi_ns files that are copies of
    # stock files (terrains, ambience, sounds) stay in the game folder, where a disc install extracts them
    files = sorted({f for f in _tree(g, 'ozi_ns') + _rtree(g, 'ozi_ns') if not re.match(r'(?i)ozi_ns\\((?:HD|UW)_\d{4}P|intrf_hd)\\', f)}, key=str.lower)   # the game folder and the patcher copies name the same files once
    files += _rtree(g, 'exp', pattern=r'^animozi\.dat$')
    files += _rtree(g, 'exp', 'animate', pattern=r'^(dalg|spyo|reae|tranozi)\.fin$')
    files += _rtree(g, 'exp', 'sprites', pattern=r'^(dalg|spyo|reae|tranozi)\.spr$')
    # 375 since 21 Sep 2026: the 19 `.o16` minimap caches of the pack maps were untracked (game-written,
    # `*.o16` is gitignored in Dark-Colony; the game recreates them on first load).
    assert len(files) >= 370, (g, len(files))
    files += ['ozi_ns\\gamestat\\hxscene.txt', 'ozi_ns\\gamestat\\gxscene.txt']   # unshifted lists, untracked at generation time
    files += ['dc\\intrface\\credits.txt']   # the DARK COLONY mode's overlay: the Council Wars credits (its menu and dialog copies are written per resolution)
    # tracer bullets (tracer.py, 2 Oct 2026): the TRAC bank the patched exe loads through animozi.dat and the
    # weapon-table overlays (human trooper -> TRAC, upgraded Gray trooper -> GRAY); the root tables stay stock
    files += _rtree(g, 'ANIMATE', pattern=r'^trac\.fin$') + _rtree(g, 'SPRITES', pattern=r'^trac\.spr$')
    files += _rtree(g, 'dc', 'gamestat', pattern=r'^weapstat\.txt$') + _rtree(g, 'exp', 'gamestat', pattern=r'^weapstat\.txt$')
    assert {f.lower() for f in files} >= {'animate\\trac.fin', 'sprites\\trac.spr', 'dc\\gamestat\\weapstat.txt',
                                          'exp\\gamestat\\weapstat.txt'}, 'tracer files missing from the index (git add them)'
    if mode == STOCK_MODE:
        files += ['exp\\intrface\\bintroe']                              # source of the bintoze copies
    return files


# the icon every patched exe gets (fix `icon`, 25 Sep 2026): made by make_dc_icon.py from DC.ICO's geometry
ICON_FILE = os.path.join(RES_DIR['cw'], 'DC_HD.ICO')      # a resource (patcher/game) since 5 Oct 2026

TOOL_OF = {'nocd': 'patch_nocd.py',
           'resolution': 'patch_resolution.py', 'hdpaths': 'patch_hd_paths.py', 'cursor': 'patch_cursor.py',
           'pool': 'patch_pool.py',
           # the clock tool's two anchor dwords are part of the one display fix; its bank string is the dark-theme fix `console`
           'clock': ('patch_clock.py', ['--part', 'anchors']), 'console': ('patch_clock.py', ['--part', 'bank']),
           # default game speed 150 % (10 Sep 2026; dropped 21 Sep 2026; back since 1 Oct 2026, maintainer: "return back patch for 150% game speed by default")
           'speed': ('patch_speed.py', ['--percent', '150']),
           'ddraw': 'patch_ddraw_lost.py', 'palette': 'patch_palette.py', 'camera': 'patch_camera.py', 'restore': 'patch_restore.py',
           'longpath': 'patch_longpath.py', 'music': 'patch_music.py', 'widemap': 'patch_widemap.py',
           'menuorder': 'patch_menu_order.py', 'chat': 'patch_chat.py', 'netsave': 'patch_netsave.py', 'fps': 'patch_fps.py', 'pointer': 'patch_pointer.py', 'intro': 'patch_intro.py',
           'ozi': 'patch_ozi_menu.py',
           # map editor: one tool, one fix id per step (the plan is taken once with --fix all)
           'blocksets': ('patch_maped.py', ['--fix', 'blocksets']), 'teams': ('patch_maped.py', ['--fix', 'teams']),
           'healer': ('patch_maped.py', ['--fix', 'healer']), 'troopsframe': ('patch_maped.py', ['--fix', 'troopsframe']),
           # 1 Oct 2026: everything else the editor greys out (DC16_MAP_FILES.md section 13)
           'race': ('patch_maped.py', ['--fix', 'race']), 'campaign': ('patch_maped.py', ['--fix', 'campaign']),
           'medfiles': ('patch_maped.py', ['--fix', 'medfiles']), 'blockmenu': ('patch_maped.py', ['--fix', 'blockmenu']),
           'teamdialogs': ('patch_maped.py', ['--fix', 'teamdialogs']), 'lieutenants': ('patch_maped.py', ['--fix', 'lieutenants']),
           'artifacts': ('patch_maped.py', ['--fix', 'artifacts']), 'lights': ('patch_maped.py', ['--fix', 'lights']),
           'trigger': ('patch_maped.py', ['--fix', 'trigger']), 'aiflags': ('patch_maped.py', ['--fix', 'aiflags']),
           # the high-resolution icon, last but one in the Ultimate build, last in the others (it appends a section); the .ico is in the game folder
           'icon': ('patch_icon.py', ['--ico', ICON_FILE]),
           # ONLINE WAR (29 Sep 2026, Ultimate only): appends the code section .dccode after .dcicon, so it follows icon
           'online': 'patch_online.py'}
PLAN_OF = {'nocd': 'nocd',
           'resolution': 'resolution', 'hdpaths': 'hd_paths', 'cursor': 'cursor', 'pool': 'pool',
           'clock': 'clock', 'console': 'clock', 'speed': 'speed', 'ddraw': 'ddraw_lost', 'palette': 'palette', 'camera': 'camera', 'restore': 'restore', 'longpath': 'longpath', 'widemap': 'widemap',
           'music': 'music', 'menuorder': 'menu_order', 'chat': 'chat', 'netsave': 'netsave', 'fps': 'fps', 'pointer': 'pointer', 'intro': 'intro',
           'ozi': 'ozi_menu',
           'blocksets': 'maped', 'teams': 'maped', 'healer': 'maped', 'troopsframe': 'maped',
           'race': 'maped', 'campaign': 'maped', 'medfiles': 'maped', 'blockmenu': 'maped', 'teamdialogs': 'maped',
           'lieutenants': 'maped', 'artifacts': 'maped', 'lights': 'maped', 'trigger': 'maped', 'aiflags': 'maped',
           'icon': 'icon', 'online': 'online'}
MAPED_STEPS = ['blocksets', 'teams', 'healer', 'troopsframe', 'race', 'campaign', 'medfiles', 'blockmenu',
               'teamdialogs', 'lieutenants', 'artifacts', 'lights', 'trigger', 'aiflags']
PLAN_ARGS = {'maped': ['--fix', 'all'], 'icon': ['--ico', ICON_FILE], 'speed': ['--percent', '150']}      # plan-time arguments per plan name (default: none)
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
        if key in _plans or step in PLAN_ON_PATCHED:
            continue
        tool, _args = tool_of(step)
        extra = mode_args(mode) if step in MODE_STEPS else []
        _plans[key] = run_tool(tool, 'plan', orig_copy, PLAN_ARGS.get(PLAN_OF[step], []) + extra)
    states = []
    for step in steps:
        tool, args = tool_of(step)
        extra = mode_args(mode) if step in MODE_STEPS else []
        if step in PLAN_ON_PATCHED:
            # this tool describes its edits only on the state it requires (ozi + icon applied): plan on the work file
            key = (g, PLAN_OF[step], mode if step in MODE_STEPS else None)
            _plans[key] = run_tool(tool, 'plan', work, PLAN_ARGS.get(PLAN_OF[step], []) + extra)
        run_tool(tool, 'apply', work, list(args) + extra)
        states.append((step, open(work, 'rb').read()))
    return orig, states

PLAN_ON_PATCHED = {'online', 'intro'}   # plans taken on the exe as the previous steps left it, not on the original (intro re-points fix ozi's trampoline calls)

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

def blocks_netsave(g):
    t = plan(g, 'netsave'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2});(.*)$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3)) and len(old) in (19, 5, 55, 34), (g, len(old))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ':' + m.group(6).rstrip(), old, new))
    out += reloc_lines(t, '.reloc table: ')
    assert len(out) == 6, (g, len(out))                                   # two hooks, two stubs, two .reloc entries
    return out

def blocks_intro(g):
    t = plan(g, 'intro'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2});(.*)$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3)) and len(old) in (5, 95), (g, len(old))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ':' + m.group(6).rstrip(), old, new))
    out += reloc_lines(t, '.reloc table: ')
    assert len(out) == 5, (g, len(out))                                   # two re-pointed calls, the start-up block, two .reloc entries
    return out

def blocks_fps(g):
    t = plan(g, 'fps'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2});(.*)$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3)) and len(old) in (6, 92, 42, 40), (g, len(old))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ':' + m.group(6).rstrip(), old, new))
    assert len(out) == 4, (g, len(out))                                   # the epilogue hook + the three remap bodies
    out += reloc_lines(t, '.reloc table: ')
    assert len(out) == 21, (g, len(out))                                  # + the 17 HIGHLOW entries inside the rewritten ranges -> type 0
    return out

def blocks_pointer(g):
    t = plan(g, 'pointer'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2});(.*)$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3)) and len(old) in (5, 21, 14), (g, len(old))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ':' + m.group(6).rstrip(), old, new))
    assert len(out) == 3, (g, len(out))                                   # the call + the gate's two parts
    out += reloc_lines(t, '.reloc table: ')
    assert len(out) == 6, (g, len(out))                                   # + three entries of the tails (two re-pointed, one -> type 0)
    return out

def blocks_ddraw(g):
    t = plan(g, 'ddraw_lost')
    out = []
    lens = {'remap: Unlock failure -> next index': 5, 'remap: Lock failure -> next index': 5,
            'remap: GetDC failure -> next index': 5, 'loading screen: Flip failure -> continue': 2,
            'cursor colour key: Lock failure -> key from the pixel format': 92}
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+): stock', t, re.M):
        name = m.group(1).strip()
        out.append((int(m.group(2), 16), lens[name], name))
    out += reloc_lines(t, '.reloc table: ')
    assert len(out) == 13, (g, len(out))                                  # 5 code sites + 8 .reloc entries (3 Oct 2026)
    return out

def blocks_palette(g):
    t = plan(g, 'palette')
    out = []
    lens = {'remap: pixel read-back -> arithmetic': 135, 'remap: Unlock of the read-back -> skipped': 24}
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+): stock', t, re.M):
        name = m.group(1).strip()
        out.append((int(m.group(2), 16), lens[name], name))
    out += reloc_lines(t, '.reloc table: ')
    assert len(out) == 7, (g, len(out))                                   # 2 code sites + 5 .reloc entries
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
        assert len(old) == len(new) == int(m.group(3)) and len(old) in (20, 14, 22, 4, 1)
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ':' + m.group(6).rstrip(), old, new))
    assert len(out) == 5, (g, len(out))                                   # two open sites, the open_read stub, the error-exit operand, the name byte
    return out

def blocks_menuorder(g):
    t = plan(g, 'menu_order'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2}); (.*)$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3)) and len(old) in (41, 102, 51), (g, len(old))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ': ' + m.group(6).rstrip(), old, new))
    assert len(out) == 3, (g, len(out))                                   # the three in-place blocks of main.c bintro
    return out

def blocks_chat(g):
    t = plan(g, 'chat'); out = []
    for m in re.finditer(r'^\s+(.+?)\s+VA 0x[0-9a-f]+ file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2}); (.*)$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3)) and len(old) in (276, 6, 51), (g, len(old))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip() + ': ' + m.group(6).rstrip(), old, new))
    assert len(out) == 3, (g, len(out))                                   # the chat display, the handler's inc, stub + helper in the palette room
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
    sizes = [n for _, n, _ in out]
    # the rewritten cdaudio module + the aux volume walk + the two options-dialog hooks and the press-only jne (+ the dialog name at 640x480)
    assert sizes in ([0x751, 0x6E, 12, 11, 6], [0x751, 0x6E, 12, 11, 6, 14]), (g, out)
    n = len(out)
    out += reloc_lines(t, '.reloc table: ')
    # 44 entries of the two pages minus those the old and the new code use at the same offset (no edit)
    assert 40 <= len(out) - n <= 44, (g, len(out) - n)
    return out

def music_data(g, mode=None):
    """The MP3 tracks of the `music` fix: Dark Colony's four under MUSIC\\; Dark Colony Ultimate plays
    both discs (its MUSIC row offers DC / CW / ALL), so it needs those four and Council Wars' four under
    exp\\music\\ (both games share one folder and the two discs differ)."""
    files = _tree(g, 'MUSIC', pattern=r'^track0[2-9]\.mp3$')
    if g == 'cw':
        files = files + _tree(g, 'exp', 'music', pattern=r'^track0[2-9]\.mp3$')
    assert len(files) == 8, (g, files)
    for f in files:
        assert os.path.exists(os.path.join(GAME_DIR[g], f.replace('\\', os.sep))), f
    return files

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

def blocks_display(g):
    """The one `resolution` fix (1 Oct 2026) = the display sweep + the 30 INTRF_HD path strings + the clock
    anchor, each tool's blocks as before; the three tools touch disjoint bytes (attribute() asserts it)."""
    return blocks_resolution(g) + blocks_hdpaths(g) + blocks_clock(g)

def blocks_clock(g):
    t = plan(g, 'clock')
    m = re.search(r'y dword at file 0x([0-9a-f]+), x dword at file 0x([0-9a-f]+)', t)
    w, h = mode_wh(CUR_MODE)
    b = re.search(r'hand bank path at file 0x([0-9a-f]+)', t)
    return [(int(m.group(1), 16), 4, 'clock_draw: imm32 of mov edx,ANCHOR_Y - bottom-right anchor y 450 (0x1C2) -> %d (0x%X)' % (h - 30, h - 30)),
            (int(m.group(2), 16), 4, 'clock_draw: imm32 of mov eax,ANCHOR_X - bottom-right anchor x 608 (0x260) -> %d (0x%X)' % (w - 32, w - 32))]

def blocks_console(g):
    b = re.search(r'hand bank path at file 0x([0-9a-f]+)', plan(g, 'clock'))
    return [(int(b.group(1), 16), 14, 'DGROUP string "sprites/cloc" -> "sprites/clock": the hand cells carry the dial face, SPRITES\\CLOCK.SPR is the face redrawn in the menu style (doc 10.49); the stock SPRITES\\CLOC.SPR stays for the original exe and for the light theme')]

def blocks_maped(fix):
    """Block parser factory for the map-editor fixes: lines `  [<fix>] <note>  file 0x.. 1 byte: 58 -> 50`."""
    def blocks(g):
        out = []
        for m in re.finditer(r'^\s+\[' + re.escape(fix) + r'\] (.+?)\s+file 0x([0-9a-f]+) 1 byte: ([0-9a-f]{2}) -> ([0-9a-f]{2})\s*$', plan(g, 'maped'), re.M):
            out.append((int(m.group(2), 16), 1, m.group(1).strip(), bytes.fromhex(m.group(3)), bytes.fromhex(m.group(4))))
        assert out, fix
        return out
    return blocks

def blocks_appending(g, step, plan_name, n_edits, what):
    """The header/code edits of a fix that appends a section (icon, online); the appended bytes are taken from
    the replay in attribute() and checked against the sha256 the tool's plan prints (APPENDS)."""
    t = plan(g, plan_name); out = []
    for m in re.finditer(r'^\s+(.+?)\s+file 0x([0-9a-f]+) (\d+) bytes: ((?:[0-9a-f]{2} )*[0-9a-f]{2}) -> ((?:[0-9a-f]{2} )*[0-9a-f]{2})\s*$', t, re.M):
        old = bytes.fromhex(m.group(4).replace(' ', '')); new = bytes.fromhex(m.group(5).replace(' ', ''))
        assert len(old) == len(new) == int(m.group(3))
        out.append((int(m.group(2), 16), len(old), m.group(1).strip(), old, new))
    assert len(out) == n_edits, (g, step, len(out))
    m = re.search(r'^\s+append at file 0x([0-9a-f]+) (\d+) bytes sha256 ([0-9a-f]{64}): (.+)$', t, re.M)
    assert m, t
    APPENDS.setdefault(g, {})[step] = dict(at=int(m.group(1), 16), n=int(m.group(2)), sha=m.group(3), note=m.group(4).strip(), what=what)
    return out

def blocks_icon(g):
    return blocks_appending(g, 'icon', 'icon', 4, 'the resource directory and the images of DC_HD.ICO')

def blocks_online(g):
    # 3 header edits + the menu id filter byte + the id chain tail jump + the LOAD GAME picker call (3 Oct 2026)
    return blocks_appending(g, 'online', 'online', 6, 'the ONLINE WAR / REPLAY ONLINE GAME / LOAD GAME module, compiled from tools/online/online.c (see the fix description)')

APPENDS = {}   # (build) -> step -> the appended section of an appending fix

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
    assert len(out) == 13, (g, len(out))                                  # 3 historical cdcheck bytes + 10 CD-path sites
    out += reloc_lines(t, '.reloc table: ')
    assert len(out) == 19, (g, len(out))                                  # + 2 start-up operands, 4 CD-prompt operands
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
     prompt for hd_<height>p/hxscene.txt.)

Every edit sits inside an existing instruction or string; nothing moves.  The patched exe no
longer needs HBNFUFL.A01 / .A02 (the untouched originals still read the drive letter from them).'''),
 dict(id='resolution', name=lambda mode: '%s display: screen mode, interface data from %s, clock hand' % (mode, hd_folder(*mode_wh(mode))),
      date='9 / 13 / 14 Sep 2026 (one fix since 1 Oct 2026)', tool='tools/patch_resolution.py + patch_hd_paths.py + patch_clock.py (Dark-Colony-Server)',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md sections 8-10, 10.15, 10.17, 10.24, 10.25, 10.58', blocks=blocks_display,
      data=hd_data, datasize=True,
      desc=lambda mode: (lambda g: """Everything the screen size changes, in ONE fix (until 1 Oct 2026 the three fixes "display",
"interface data from its own folder" and "clock hand", which only worked together and were always
selected together; the maintainer asked for one).  Three tools are replayed one after the other:

A. THE DISPLAY (patch_resolution.py).  The engine is hard-wired for 640x480: the DirectDraw
display mode, the framebuffer stride (y*640 done as shl 7 + add), clip rectangles, the map
viewport (16x14 tiles), the minimap position, the movie blit, the 44 code-positioned main-menu
elements, the terrain light plane's 512-byte row advances and the size of draw_terrain's stack
lightmap.  Every one of those constants was read out of the disassembly and is replaced by the
%(mode)s equivalent here:
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

B. INTERFACE DATA FROM HD_<height>P (patch_hd_paths.py, 30 edits).  The rebuilt menus, HUD frame,
loading screens, briefing-marker lists and re-baked logo sprites used to replace the stock files
under their stock names, so the untouched original exe could no longer run from the same folder.
They live under their stock names in the resolution's OWN folder %(folder)s/ (Council Wars also
exp/%(folder)s/ and ozi_ns/%(folder)s/; since 2 Oct 2026 - until then every size shared one
HD_<height>P/, which is how a player got a HUD script of one size under the frame of another), the
stock 640x480 files are back in INTRFACE/ and GAMESTAT/, and the re-baked
logo animations are SPRITES/DCSS_HD.SPR, DCUK_HD.SPR, DCUT_HD.SPR with matching ANIMATE/*_HD.FIN.
The game opens each of those files through a literal path in the data section ("intrface/bintro"
plus the language letter, "gamestat/hscene" plus ".txt", ...), so the 8-byte directory part of
exactly the 30 strings whose files were rebuilt is rewritten: "intrface" / "gamestat" ->
"%(token)s", same length, in place.  Fonts, text files, per-screen sprite lists without logo
banks and every other file keep their stock path and single copy; the two lists that do name
logo banks (INTRG.DAT, INTRO.DAT) are redirected to copies in %(folder)s/ that say dcuk_hd.fin etc.
No code changes.  With this the untouched dc16.exe / ENGEXP16.EXE (stock data) and the patched
exe (%(folder)s data) run side by side from one folder.

C. THE DAY/NIGHT CLOCK HAND (patch_clock.py --part anchors, 2 edits).  The HUD's dial is a sprite
cell that clock.c blits by code with its top-left corner at (608,450) - two plain immediates that
are neither 640 nor 480, so the sweep in A did not touch them.  At %(mode)s that point lies inside
the enlarged map view and the terrain paints over the dial every frame.  The anchor moves to
(%(cx)d,%(cy)d), where the rebuilt HUD frame has the clock face.  (The dark battlefield interface
also renames the dial's bank - that is the separate fix "console" below.)

REQUIRES the interface data built for %(mode)s next to the exe in the folder %(folder)s/ - one
folder per resolution (2 Oct 2026), so a set of another size can never be read by mistake.
Applying this fix makes the patcher WRITE that set (Write-InterfaceSet) from the stock 640x480
files and the five pictures per size that ship with the game (HD_SRC\\%(mode)s\\INTRG.GIF,
INTRO.GIF, BACKDROP.GIF, and the HUD frame INTRFACE.GIF for the dark battlefield interface or
INTRFACE_LIGHT.GIF for the light one - the theme is chosen with the resolution): menu scripts,
HUD script, briefing lists, letterboxed backgrounds, the two loading screens
%(folder)s\\LOAD.BMP / LOAD2.BMP (the 640x480 picture centred on a black %(mode)s canvas),
Council Wars' exp\\%(folder)s and ozi_ns\\%(folder)s copies - and first DELETES every other
resolution's folder (HD_*P / UW_*P, the pre-October HD_<height>P set, the 640x480 copies), so that no
file of another size is left anywhere (maintainer's rule).  The folder is also what the ONLINE
WAR screen is read from.""" % dict(
          mode=mode, dx=g.menu_dx, dy=g.menu_dy, vw=g.view_w, vh=g.view_h, tx=g.tiles_x, ty=g.tiles_y,
          mx=g.minimap_x, m0=g.movie_rect[0], m1=g.movie_rect[1], m2=g.movie_rect[2], m3=g.movie_rect[3],
          cx=mode_wh(mode)[0] - 32, cy=mode_wh(mode)[1] - 30,
          folder=hd_folder(*mode_wh(mode)), token=hd_token(*mode_wh(mode)),
          stride='a shift, %d is a power of two' % g.w if g.pow2 else 'imul: %d is not a power of two' % g.w,
          slack=' plus %d spare rows given to the taller HUD bottom bar' % g.slack_y if g.slack_y else '',
          lm='the six lightmap row idioms x144 -> x%d (more than 34 tiles across),' % g.lm_stride if g.lm_stride_patch else ''))(geometry(mode))),
 dict(id='console', name='Dark battlefield interface: the console-style HUD, dialogs and clock dial', date='28-30 Sep 2026 (a choice since 1 Oct 2026)',
      tool='tools/patch_clock.py --part bank (data: tools/hud_console.py)', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md sections 10.49-10.55, 10.58; docs/DC16_INTERFACE_STYLE_GUIDE.md',
      blocks=blocks_console, requires=['resolution'], data=console_data, theme='dark',
      desc="""The DARK battlefield interface (the maintainer's choice of 1 Oct 2026: "dark mode must be optional
but not preselected, customer must be forced to select light mode (classic) or dark mode").  From
28 to 30 Sep 2026 the brushed-metal battlefield HUD was redrawn in the visual language of the
game's menus: a grey pipework frame (the resolution folder's INTRFACE.GIF), buttons on the lobby's red-ringed
black plates with the original unit and building portraits (HD_SRC\\MAINBUT.SPR), the dialogs
(save, options, objectives, quit) as black forms with grey tube frames and the lobby's text
buttons (HD_SRC\\POPP.SPR, laid out by the console dialog pass of the set writer), and the
day/night dial redrawn in the same style (SPRITES\\CLOCK.SPR: light right half with a sun, dark
left half with a moon, a red hand).  Almost all of that is data the patcher writes with the
interface set when this fix is selected; this fix's ONE byte edit is the exe's name of the dial
bank, "sprites/cloc" -> "sprites/clock" (14 bytes in DGROUP, same length, in place), so the exe
draws SPRITES\\CLOCK.SPR instead of the stock metal dial SPRITES\\CLOC.SPR.

The LIGHT (classic) interface = this fix not selected: the set writer takes the shipped
HD_SRC\\<WxH>\\INTRFACE_LIGHT.GIF (the metal frame spliced to the size by hud_layout.py), the
scripts keep the stock banks INTRFACE\\MAINBUT.SPR / POPP.SPR and the stock dialog layouts (plus
the MUSIC row for Dark Colony Ultimate), and the exe keeps "sprites/cloc".  Only at the HD sizes:
at 640x480 (original) the game keeps its own interface and the theme is not asked.  The exes
published in the repository are the dark 1024x768 build."""),
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
 dict(id='speed', name='Default game speed 150 %', date='10 Sep 2026 (back since 1 Oct 2026)', tool='tools/patch_speed.py --percent 150',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.14', blocks=blocks_speed,
      desc="""One simulation tick runs every gs->tick_ms milliseconds.  Two stock values feed it and both
must change or the game resets the speed within a second: the game-state initialiser
("mov dword ptr [esi+970h],66") and the persistent "desired tick" setting in DGROUP that the
options screen and the speed negotiation read.  66 ms = 100 %, 44 ms = 150 % (the slider shows
6600 / tick_ms).  The default only: the GAME SPEED slider of the battlefield options dialog
still changes the speed at any time (100..200 %).  Multiplayer speed comes from the relay
server, saved games keep their own speed.  (In the patcher from 10 to 21 Sep 2026, removed at
the maintainer's request, and back since 1 Oct 2026, again at the maintainer's request.)"""),
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
 dict(id='palette', name='Fast screen loads: the palette conversion no longer makes 512 surface round trips', date='28 Sep 2026',
      tool='tools/patch_palette.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.46', blocks=blocks_palette,
      desc='''Every screen the game shows (each menu, the briefings, the multiplayer hall, the battle load,
twice at start-up) ends in the palette conversion of ddex4.c, which turns the 256 palette entries
into 16-bit pixels the portable 1997 way: for every entry GetDC on the back buffer, SetPixel,
ReleaseDC, then Lock the whole surface, read the pixel back, Unlock.  On Windows 11 DirectDraw is
emulated over Direct3D 9 and ReleaseDC and Unlock each copy the WHOLE surface, so the loop costs
512 full-screen transfers per screen change: measured 1.4-1.5 s at 1024x768 and 3.2-3.5 s at
1920x1200 - the reason every menu loads slower the larger the resolution.  The value read back is
nothing but the RGB bytes truncated to the surface's 5-6-5 (or 5-5-5) bit fields, verified for all
256 entries in the running game.  The fix computes exactly that in place (58 bytes of shifts and
ors, a short jump, NOP padding) and turns the now pointless Unlock into a jump to the next entry;
the five .reloc entries of the overwritten absolute operands become type 0 ABSOLUTE padding.  The
three "skip on failure" jumps of the two-monitor fix inside the same loop become dead code and stay
harmless.  Nothing else in a screen load takes more than a fraction of a second.'''),
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
at 5 s, the fixed one plays on with an empty error.log.  Fifth edit (25 Sep 2026): the name of
the sound table, "sound\\sound2.dat", is the only file name in the whole game written with a
backslash - every other path uses a forward slash.  A Linux player running the game under Wine
reported a start-up assert on exactly this file ("FILE Error opening file sound\\sound2.dat with
error num 1", safefunc.c line 290) while every file before it had loaded; the one backslash
becomes a forward slash (one data byte, no code), so this file is asked for the same way as all
the others.  Windows treats both separators alike.'''),
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

Dark Colony Ultimate (since 25 Sep 2026) plays both discs and lets you choose: its battlefield
options dialog (the Options button of the Game Option tab) gets a MUSIC row with "-" / "+" and the
values DC (the Dark Colony disc), CW (the Council Wars disc) and ALL (all eight tracks in a random
order, reshuffled after each round).  The campaign you start sets the default - ACADEMY and DARK
COLONY play DC, COUNCIL WARS plays CW, OZI MISSIONS and CUSTOM NET WAR (MULTI PLAYER WAR until 3 Oct 2026) play ALL (the menu fix writes it) - and the
dialog changes it at any time, with the music switching at once.  Two small in-place edits route
the dialog's new buttons and value text into the rewritten routines; the dialog script with the
new row is written beside the exe for the three campaign modes (exp\\, dc\\ and ozi_ns\\ copies
of HD_<height>P\\lopte - at 640x480 intrface\\lopme, because the exe would otherwise read the
original's own exp\\intrface\\lopte).

REQUIRES the eight tracks from the repository (encoded from the CD images at 192 kbit/s, 32 MB):
MUSIC\\TRACK02.MP3 .. TRACK05.MP3 for Dark Colony, exp\\music\\track02.mp3 .. track05.mp3 for
Council Wars (Dark Colony Ultimate needs both sets).  Without a TRACK02 file the game simply stays
silent, as it does today.'''),
 dict(id='menuorder', name='Main menu opens in order: DC logo, DARK COLONY title, buttons, credits', date='28 Sep 2026',
      tool='tools/patch_menu_order.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.47', blocks=blocks_menuorder,
      requires=['nocd'],
      desc='''When the main menu opens, the original runs the button wave first (the plates fly in one after
another and each label appears as its plate settles), then shows every button, then plays the DC
logo animation and, when the logo is nearly done, the DARK COLONY title; the credits box scrolls
from the first pass of the menu loop.  So the player sees buttons, logo, title, credits - the brand
mark last.  This fix reorders the opening the way title screens are normally staged: the DC logo
plays first, the title follows when the logo has finished, the button wave runs as soon as the
title shows its second frame (top to bottom, labels as the plates settle, the plate sound per plate
as before), and the credits box appears after the wave.  Everything is rewritten in place in the
menu's own set-up code: the wave, the button show and the logo start move into the room left by the
six "grey the buttons when no CD is found" calls (dead code since the "No CD" fix, which is
therefore required) and the loop's "start the title at logo frame 10" check, which the new order
makes pointless, becomes the tail that runs the wave.  The menu script's first plate (marked
anim_oneoff so the wave has something to chain from) is put back to its first frame before the
first screen update and started again when the wave begins, so the interface files need no change
and an older exe with the same files behaves as before.  Relative calls and register-relative
operands only; nothing moves, no relocation entry changes; the same three blocks in both games.'''),
 dict(id='chat', name='Battlefield chat: six lines, each new line announced with the mission-message sound', date='28 Sep 2026',
      tool='tools/patch_chat.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.48', blocks=blocks_chat,
      requires=['palette'],
      desc='''In a network battle the chat lines of the other players (and the relay's bots) appear on the last
rows of the map view, above the bottom bar.  The original shows only the two newest lines and shows
them silently, while a mission message in a single-player game announces itself with a sound
(SOUND\\MSG.WAV, entry 187 of the sound table).  This fix makes the battlefield chat behave like the
mission messages: every line that arrives plays that sound, and up to SIX lines stay on screen (the
oldest one leaves 7.5 seconds after the previous one left, as before).  The chat display of the
client is rewritten in place (276 bytes: the newest-line cap goes from 2 to 6 lines, line 3-6 use
the HUD script's new widgets 207-210, and a line is drawn only if its widget exists, so a HUD
script with the stock two lines still works and shows two); the chat handler's "count the queued
line" instruction becomes a call to a 17-byte stub that also leaves a "new line" mark, and a
34-byte helper plays the sound when the display finds that mark.  Stub and helper live in the 75
bytes the "fast screen loads" fix (palette) frees inside the palette conversion, which is
therefore required.  No absolute addresses are written, so the .reloc table is unchanged.  The six
lines themselves are data: the HUD script HD_<height>P\\MAINE written with the display fix gets
in_text 207..210 above the two stock chat lines (15 rows apart); the stock 640x480 MAINE keeps two.'''),
 dict(id='netsave', name='No save in a network battle: the Save Game cell hidden, F11 inert', date='3 Oct 2026',
      tool='tools/patch_netsave.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.68', blocks=blocks_netsave,
      requires=['nocd'],
      desc='''The Game Option tab of a network battle offered the same Save Game cell (and the F11 key) as a
campaign battle, and the file it wrote (save\\<name>.dcg, game type 2 in its header) showed up in the
main menu's LOAD GAME list as "Multiplayer".  Loading it never rejoined the relay game: the stock code
resumes a network save as the host of an in-process network with the lobby skipped, so the battle came
back with every other player's base standing still - a solo continuation against frozen opponents.
This fix switches saving off while the game type is 2 (network game): at battle start the Save Game
cell (widget 63) is disabled through the same per-widget flag the game uses to hide the Allies cell in
campaign battles - the tab switch does not touch it, the cell is neither drawn nor clickable - and the
save dialog's entry returns at once when the game type is 2, which covers F11 and the ? key as well.
Campaign, skirmish and training battles save as before.  Two 5-byte jumps in place (the end of the
game start's network branch, the dialog's first five bytes) and two small stubs (55 + 34 bytes) in the
wave loader's CD attempt, dead code since the "No CD" fix (required); the displaced absolute operand's
relocation entry is neutralised and the dead code's one entry is re-pointed to the stub's operand, so
the relocation table stays exact.  Same bytes at +0x60 in Council Wars.'''),
 dict(id='fps', name='Frame limiter: never more than 60 frames per second (Wine, monitors above 60 Hz)', date='3 Oct 2026',
      tool='tools/patch_fps.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.69', blocks=blocks_fps,
      requires=['ddraw'],
      desc='''The game has no frame limiter: the only thing that paces its main loop is the DirectDraw Flip at
the end of each frame, which on Windows waits for the monitor's vertical blank - 60 frames per second
on most monitors, and the 1997 code counts on that: the battlefield scrolls one tile per frame once
the pointer has rested at an edge, and the cursor animation advances once per frame.  Under Wine the
flip returns at once (a Wine virtual desktop, Xvfb and gamescope have no vertical blank to wait for)
and the loop was measured at 350-380 frames per second: the map crosses in a quarter of a second, the
cursor flickers.  A Windows monitor above 60 Hz has the same problem in proportion (2.4 times too fast
at 144 Hz).  The simulation itself, the network and the menus are clock-driven and were never
affected.  This fix makes the end of each frame wait until 16 ms have passed since the previous one:
the frame routine's epilogue jumps to a 132-byte stub that reads the clock (timeGetTime) and, only
when the frame was faster than that, raises the timer resolution (timeBeginPeriod 1, looked up in
winmm.dll at that moment - without it a plain Windows process sleeps 15.6 ms at a time), sleeps in
1 ms steps until the 16 ms are up and releases the resolution again (timeEndPeriod).  On a 60 Hz
Windows monitor the flip has already taken the 16 ms, so nothing changes there.  The stub and its
three names live in the three assert bodies of the palette remap that the "two-monitor start-up" fix
(ddraw, required) turned into dead code; the timestamp lives in the unused page slack of the exe's
import section; the 20 relocation entries of the dead bodies' absolute operands become padding and
the new code has none (it finds its own address), so the relocation table stays exact.  Same code in
both games (at +0x60 in Council Wars).'''),
 dict(id='pointer', name='Battlefield pointer animation at the pace of the menus (every 33 ms instead of every frame)', date='3 Oct 2026',
      tool='tools/patch_pointer.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.70', blocks=blocks_pointer,
      requires=['ddraw'],
      desc='''In a battle the game advances the pointer's animation once per frame, so at 60 frames per second the
crosshair's three-frame cycle turns ten times a second (30 cursor changes per second), while every menu
screen advances the same animation only when 33 ms have passed since the last step.  This fix gives the
battlefield the menus' pace: the client's cursor-advance call goes through a 35-byte gate that reads the
clock (timeGetTime) and lets the step through only when 33 ms have passed, so the pointer animates every
second frame (15 changes per second).  The gate lives in the free tails of the two dead assert bodies the
"two-monitor start-up" fix (ddraw, required) left in the palette remap; its timestamp sits in the unused page
slack of the import section next to the frame limiter's; the tails' three relocation entries are re-pointed to
the gate's two absolute operands (the third becomes padding), so the relocation table stays exact.  Same code
in both games (at +0x60 in Council Wars).  Independent of the frame limiter (fps).'''),
 dict(id='intro', name='No intro movie at start-up; DARK COLONY and COUNCIL WARS play their own intro (Dark Colony Ultimate only)', date='5 Oct 2026',
      tool='tools/patch_intro.py', doc='docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.74', blocks=blocks_intro, cw_only=True, requires=['ozi'],
      desc='''The game started with the Council Wars intro (avi/intro.avi) before the main menu, whatever the player
was going to do, and the Dark Colony intro - on the Dark Colony disc, kept beside the Council Wars one as
AVI/DCINTRO.AVI since both games share the folder - was never played by this build.  Now the main menu
comes up at once, DARK COLONY plays avi/dcintro.avi and COUNCIL WARS plays avi/intro.avi, each right
before its campaign's race and name screen.  ACADEMY, OZI MISSIONS and LOAD GAME play nothing.  SPACE
skips a movie as before; a missing movie file is skipped silently.
How: the 95 bytes of main() that built "avi/" + "intro.avi" and called the movie player become a jump
to the menu loop and hold the new code: two small trampolines (one per button: push edx; call the
button's mode stub of fix ozi; call common with the movie path inline) and a common tail (pop the path
into edx, save eax, call the movie player with eax = the menu object, restore, jump to the campaign
runner).  The COUNCIL WARS and DARK COLONY handlers call these trampolines instead of fix ozi's
plain ones (which set the mode and enter the campaign); ACADEMY keeps the plain one.  The two absolute
operands the old bytes held lose their .reloc entries (type 0); the new code has none.  Requires fix ozi
(its mode stubs and trampolines).'''),
 dict(id='ozi', name='DARK COLONY and OZI MISSIONS menu modes (Council Wars only)', date='10 Sep 2026', tool='tools/patch_ozi_menu.py',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md sections 10.13 and 10.36', blocks=blocks_ozi, cw_only=True,
      requires=lambda mode: [] if mode == STOCK_MODE else ['resolution'], data=ozi_data,
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
SCENARIO/HUMAN and ALIEN, HD_<height>P/HSCENE.TXT and GSCENE.TXT and the SAVE/ folder the Classic exe
itself uses.  Two buttons are added for it:
  * the menu's accepted-id filter (`cmp edx,5`) becomes `cmp edx,7`, which admits the button ids
    6 and 7 - the first free ids; the main-menu script moves the two LARGEBUTTON plates that used
    them to 19 and 20 and gives the new buttons the plates 21 and 22
  * the two handlers go into the 59 NOP bytes the old PLAY INTRO body left behind: DARK COLONY
    sets "campaign, not training" and enters the campaign runner through tramp_dc_campaign,
    LOAD DC GAME goes through tramp_dc_load, so it always lists the SAVE/ folder (since 3 Oct 2026
    the menu has ONE load button, id 2, whose picker fix online replaces by a browser over all three
    save folders; the handlers of ids 7 and 4 stay in the exe, unreachable).  The stub and
    the two trampolines are 97 more bytes of the code section's zero tail (VA 0x47F340..0x47F3AA),
    and their four absolute slot addresses add four more entries to the .reloc insert
  * MULTI PLAYER WAR (labelled CUSTOM NET WAR since 3 Oct 2026) goes through a fourth trampoline, tramp_dc_net (25 Sep 2026; 10 bytes at
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
    the seven-row menu of 23 Sep - 3 Oct 2026 was 217 rows tall and the black band of the 640x480
    backdrop between the planet's crescent and the artwork is exactly 217 rows (the five-row menu
    since 3 Oct 2026 would leave room again; the box stays removed there).  The two string operands
    the call carried become type 0 relocation padding.  At the HD sizes the box stays (24 Sep 2026):
    the menu block is placed 120 rows under the title instead - 11 px, the stock 100-row box, 9 px -
    or as low as H-72 allows, and the `resolution` fix writes the box's height where the block
    shortens it (the seven-row block: 94 rows at 1024x768, 76 at 1280x720; the five-row block fits
    everywhere, so the box keeps its stock 100 rows at every size).  The whole Council Wars menu
    cluster - logo, title, box, buttons - sits 15 rows higher than the letterbox rule at the HD
    sizes (same day; 0 at 1280x720, where the DC logo already touches the planet's crescent).
REQUIRES the "DC - Council wars/ozi_ns/" overlay folder, exp/animozi.dat, exp/animate/tranozi.fin,
exp/sprites/tranozi.spr, dc/HD_<height>P/bintroe and the rewritten main-menu script
(exp/HD_<height>P/bintroe) from the repository, plus the tracer-bullet data of 2 Oct 2026
(ANIMATE/TRAC.FIN, SPRITES/TRAC.SPR and the weapon-table overlays dc/gamestat/weapstat.txt,
exp/gamestat/weapstat.txt: the human trooper and the Lieutenant fire a visible streak, the Gray
trooper keeps its bolt at every weapon upgrade level and the Gray commander's pistol fires the
Gray bolt; data only, the patched exe reads the tables
through the mode prefix and the FIN through animozi.dat).  Because the .reloc insert shifts every
later relocation entry, this patch is always applied last.'''),
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
 # ---- map editor, 1 Oct 2026: everything else the editor ships greyed out (maintainer: "enable everything
 # what is disabled, so all features of the game are available").  The editor is the developers' campaign
 # tool released as a multiplayer map editor; it imports neither EnableWindow nor EnableMenuItem, so every
 # greyed state is a static flag in the resources and the code behind it is complete (DC16_MAP_FILES.md §13).
 dict(id='race', name='Team Attributes: AI Type and AI Slots editable', date='1 Oct 2026', tool='tools/patch_maped.py --fix race',
      doc='DC16_MAP_FILES.md section 13 (Dark-Colony-Server)', blocks=blocks_maped('race'), editor_only=True,
      desc='''The remaining greyed part of the Team Attributes dialog: the AI Type edit (written as %AI - the
computer player's personality 1..4, read by the game in campaign scenarios only; in a multiplayer game
the lobby decides who is human and who is AI) and the fifteen AI Slots edits (written as %AISlots, a
line the game reads and ignores).  33 single-byte edits clear WS_DISABLED on the edits, their labels
and the two group boxes.  The dialog procedure handled every one of them all along.'''),
 dict(id='campaign', name='Scenario Stats: Campaign scenario type selectable', date='1 Oct 2026', tool='tools/patch_maped.py --fix campaign',
      doc='DC16_MAP_FILES.md section 13 (Dark-Colony-Server)', blocks=blocks_maped('campaign'), editor_only=True,
      desc='''The Scenario Stats dialog shows the scenario type as two radio buttons, Multiplayer (0) and Campaign (1),
the second greyed out.  The number is the first value of the fourth line of the .SCN; the game ignores
it, but the editor itself does not: for a multiplayer scenario the save also writes the .TRO trigger
file (vent eruptions, artifact sites) and appends one commander per active team at its start position,
for a campaign scenario it writes neither (campaign missions bring their own scripts and units).  A new
map starts as type 1, so until now the only way to a multiplayer map was to press Multiplayer once; this
fix lets you switch back.  One byte.'''),
 dict(id='medfiles', name='File menu: Super Gen, Load MED File, Save MED File', date='1 Oct 2026', tool='tools/patch_maped.py --fix medfiles',
      doc='DC16_MAP_FILES.md section 13 (Dark-Colony-Server)', blocks=blocks_maped('medfiles'), editor_only=True,
      desc='''Three greyed entries of the File menu with complete handlers.  Load MED File / Save MED File read and
write the editor's own document (Maps (*.med), a binary dump of the whole editor state - the only form
that keeps editor-only data such as trigger names); "Save Map" is the export to the game's files (.SCN,
.MAP, .MTG, .TRO, .POP and, through pmap.exe, .PTH).  Super Gen is the developers' batch export: it reads
dirlist.txt beside the editor, loads every *.map listed there with its .mtg/.scn/.pop and re-exports it.
Three single-byte edits clear MF_GRAYED in the MAINMENU resource.'''),
 dict(id='blockmenu', name='Block Type menu selectable', date='1 Oct 2026', tool='tools/patch_maped.py --fix blockmenu',
      doc='DC16_MAP_FILES.md section 13 (Dark-Colony-Server)', blocks=blocks_maped('blockmenu'), editor_only=True,
      desc='''The whole Block Type menu (the eighteen block classes 0 Default .. 17 River Left) is greyed out; its items
are handled and do exactly what the numbered toolbar buttons do.  One byte clears MF_GRAYED on the popup.'''),
 dict(id='teamdialogs', name='Team Attributes menu: City State and Troop Attributes dialogs reachable', date='1 Oct 2026', tool='tools/patch_maped.py --fix teamdialogs',
      doc='DC16_MAP_FILES.md section 13 (Dark-Colony-Server)', blocks=blocks_maped('teamdialogs'), editor_only=True,
      desc='''The Team Attributes menu has two greyed entries, City and Troops, whose command ids (235, 236) have no
handler in the editor's window procedure at all - un-greying them would give dead menu items.  The two
dialogs they were meant to open exist and are complete, but hang on the command ids 166 and 167, which
nothing in the editor ever sends (the menu was renumbered at some point and the handlers were left
behind).  This fix clears MF_GRAYED on the two entries and changes their command ids to 166 and 167
(low byte only, four single-byte edits).  City State sets, per team, which of the five city buildings
start as buildable or prebuilt and with how many hit points, plus the start money - the five pairs of
the TEAM block that the game does read.  Troop Attributes sets a weapon and an armour level per troop
row; the file keeps only those two numbers (the game interprets them as upgrade levels of the race's
production buildings), the Buildable and Health columns are never saved and the Healer row is not
written at all.'''),
 dict(id='lieutenants', name='Troops menu: Human and Alien Leutenant placeable', date='1 Oct 2026', tool='tools/patch_maped.py --fix lieutenants',
      doc='DC16_MAP_FILES.md section 13 (Dark-Colony-Server)', blocks=blocks_maped('lieutenants'), editor_only=True,
      desc='''The two commander entries of the Troops menu are greyed because a multiplayer save generates the
commanders itself: one per active team, at the position set with the Start tool (the game turns it into
the owner's race), and loading a multiplayer scenario discards any commander found in the file.  With
the entries un-greyed (two single-byte edits) commanders can be placed by hand, which matters for
campaign-type scenarios, where nothing is generated.'''),
 dict(id='artifacts', name='Artifacts menu: single artifacts placeable', date='1 Oct 2026', tool='tools/patch_maped.py --fix artifacts',
      doc='DC16_MAP_FILES.md section 13 (Dark-Colony-Server)', blocks=blocks_maped('artifacts'), editor_only=True,
      desc='''Solar Lens, Maktor, Lunatek, Pinball, Tektarra and Ultimate are greyed in the Artifacts menu; only
Artifact Site is selectable.  In a multiplayer map artifacts are meant to come from the sites, whose
contents the generated .TRO hands out according to the lobby's artifact setting (s(6,0)); a loose
artifact placed by hand (an object of type 63..68 lying on the map) bypasses that setting.  The
handlers are complete; six single-byte edits clear MF_GRAYED.'''),
 dict(id='lights', name='Lights menu: light objects placeable', date='1 Oct 2026', tool='tools/patch_maped.py --fix lights',
      doc='DC16_MAP_FILES.md section 13 (Dark-Colony-Server)', blocks=blocks_maped('lights'), editor_only=True,
      desc='''The Lights menu (direction left/right/up/down, type Flicker/Medium/Bright) places the game's twelve
LIGHT objects, types 51..62, as team-8 objects; the game draws them on its light plane.  The shipped
scenarios use them in a handful of campaign maps and never in a multiplayer map.  One byte clears
MF_GRAYED on the popup.  ("Path Light" in the Objects menu is a different object, type 94.)'''),
 dict(id='trigger', name='Trigger tool and Edit Trigger String', date='1 Oct 2026', tool='tools/patch_maped.py --fix trigger',
      doc='DC16_MAP_FILES.md section 13 (Dark-Colony-Server)', blocks=blocks_maped('trigger'), editor_only=True,
      desc='''The toolbar's Trigger button (greyed) paints trigger ids onto map cells; they are saved in the .MTG file,
which the game's campaign scripts test with m(x,z).  Edit Trigger String (Scenario menu, greyed) names
the current trigger.  The editor writes only its fixed multiplayer trigger templates into the .TRO, so
a script that uses the painted ids must still be written by hand - the two controls are the campaign
authors' half of that workflow.  Two single-byte edits (WS_DISABLED on the button, MF_GRAYED on the
menu entry).'''),
 dict(id='aiflags', name='Flag tool and AI Flags menu (dead feature - see description)', date='1 Oct 2026', tool='tools/patch_maped.py --fix aiflags',
      doc='DC16_MAP_FILES.md section 13 (Dark-Colony-Server)', blocks=blocks_maped('aiflags'), editor_only=True,
      desc='''The toolbar's Flag button and the AI Flags menu (Defense) place "AI flags" with a priority and a type.
WARNING: the editor saves such a flag as the object line "x z 47 <type-100> <priority>", and object type
47 is the Human mining tower in the shipped GAMESTAT.TXT - the released game has no flag object and its
computer player reads no map flags.  A placed flag therefore becomes a mining tower of player 0 (type
"Defense") standing on open ground.  Included because the maintainer asked for every greyed control;
leave it unticked unless you want to experiment.  Two single-byte edits.'''),
 # ---- every build, always last
 dict(id='icon', name='High-resolution icon (Explorer, taskbar, desktop shortcut) + per-monitor DPI-aware manifest (games)', date='25 Sep 2026 / 27-28 Sep 2026', tool='tools/patch_icon.py (icon: tools/make_dc_icon.py -> DC - Council wars\\DC_HD.ICO)',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md sections 10.38 (icon) and 10.43 (manifest)', blocks=blocks_icon,
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

Since 27 Sep 2026 the same directory also holds, for the two GAMES, an application manifest
(resource type 24, id 1) that declares the process DPI-aware.  Without it Windows treats the game as
an old, DPI-unaware program and, on a desktop with display scaling above 100 %, scales its full-screen
picture like a window: at 150 % scaling a 1920x1080 or 1920x1200 game was shown at 1.5x with two
thirds of the picture off the screen (1280x800 happened to fit because it equals the scaled desktop).
Since 28 Sep 2026 the manifest says PER-MONITOR DPI-aware (dpiAwareness PerMonitorV2, with the
older forms as fallback): the first form, <dpiAware>true</dpiAware>, only made the game SYSTEM-DPI-
aware, and Windows still bitmap-scaled its window whenever the monitor's DPI differed from the
desktop's - which a mode switch causes, because a resolution only allows certain scale steps
(1024x768, 1280x720 and 1280x800 drop a 150 % desktop to 100 %, 1280x1024 to 125 %), so those modes
showed a picture shrunk to two thirds in the top-left corner, and only 1920x1080 / 1920x1200 filled
the screen.  A per-monitor-aware process is never scaled by Windows, so the picture is shown 1:1 at
every resolution.  The map editor gets no manifest (its dialogs would shrink).

No code changes.  The file grows by the new section (about 75 KB), which is why this fix is applied
last - after it only `online` (Dark Colony Ultimate), which appends its own section behind this one.
The appended bytes are written below in Base64 (they are the icon images, the manifest and the
directory that lists them); their SHA-256 is checked like every other edit.'''),
 dict(id='online', name='ONLINE WAR, REPLAY ONLINE GAME and one LOAD GAME for every campaign: a room browser and a replay browser for the relay server, TLS to port 8889, and a save browser over save/, esave/ and ozisave/ (Dark Colony Ultimate only)', date='29 Sep 2026 (REPLAY ONLINE GAME 2 Oct 2026, LOAD GAME 3 Oct 2026)',
      tool='tools/patch_online.py (module: tools/online/online.c, built by tools/online/build.cmd)',
      doc='docs/DC16_DISPLAY_AND_RESOLUTION.md sections 10.51, 10.65 and 10.67; docs/RELAY_SERVER_PLAN.md sections 20 and 21; docs/DC16_NETWORK_PROTOCOL.md sections 4.4, 4.5, 6.9 and 6.10',
      blocks=blocks_online, cw_only=True, requires=['ozi', 'icon'], data=online_data,
      desc='''The main menu of Dark Colony Ultimate gets an eleventh button, ONLINE WAR (top of the right column;
MULTI PLAYER WAR - CUSTOM NET WAR since 3 Oct 2026 - and ENCYCLOPEDIA move two rows down) and, since 2 Oct 2026, a twelfth right under it,
REPLAY ONLINE GAME.  ONLINE WAR opens a room browser built from the LOAD GAME screen that lists the rooms of the Dark
Colony Server relay - map, terrain, seats, players, bots, status - and joins the room you pick; the
relay then chooses a free slot for you.  The name and the address of the relay come from DEFAULT_SERVER.TXT
beside the exe (plain text with C++-style comments, `name=` and `address=` lines; the shipped file names
dark-colony-server.fly.dev and explains how to point the game at another relay or at an unencrypted LAN
relay; both screens show the two values above the connection state).  The connection is
TLS-encrypted with Windows' own Schannel (port 8889; the certificate is checked against the host
name), and the game's stock lobby and battle code then run unchanged through a small loopback proxy
inside the process, so CUSTOM NET WAR (the former MULTI PLAYER WAR button) and the network play itself are untouched.

What is changed in the exe:
  * a NEW SECTION ".dccode" is appended at the end of the file (after fix icon's ".dcicon", which is
    why this fix comes last): it holds the module compiled from tools/online/online.c - the screen
    logic on the game's own interface engine, the DEFAULT_SERVER.TXT parser, the TLS client, the room
    list dialogue with the relay (messages 0x50..0x54) and the proxy thread.  The module imports
    nothing: it takes LoadLibraryA and GetProcAddress from the exe's own import table and resolves
    the Windows socket, TLS and kernel functions at run time.  Three header edits register the
    section (section count, section header, image size).
  * the menu's accepted-id filter `cmp edx,7` -> `cmp edx,9` (button ids 8 = ONLINE WAR, 9 = REPLAY
    ONLINE GAME), and the seven NOP bytes at the end of the menu's id chain become a jump into the
    section (ids other than 8 and 9 return to the menu loop as before).
  * the stock LOAD GAME code's call of its one-folder picker screen (0x403ABC) is pointed at the
    module's save browser (3 Oct 2026).  Nothing else in the code changes.

LOAD GAME (3 Oct 2026) is the ONE load button of the menu, last in the left column: it lists the saves
of every campaign together - the folders save\\ (ACADEMY and DARK COLONY), esave\\ (COUNCIL WARS) and
ozisave\\ (OZI MISSIONS) stay as they are, every save keeps its folder - newest first, one row per save:
date, time, the name you gave it and the campaign (Academy, Dark Colony, Council wars, Ozi missions;
the game type in the save's header tells an ACADEMY save from a DARK COLONY one, a multiplayer game
saved in battle says Multiplayer).  LOAD switches the game to the save's campaign mode and the stock
code loads the file and resumes exactly as the three former load buttons did.  The buttons LOAD DC GAME
and LOAD OZI GAME are gone from the menu (their handlers stay in the exe, unreachable).

REPLAY ONLINE GAME lists the battles the relay recorded (date and time, map, terrain, seats, players,
computer players, length - the relay keeps the newest 50) and, right of the list, the eight players of
the selected battle with a radio box each (the boxes come from HD_SRC\\KNOBR.SPR, the lobby's READY
boxes with an empty box added; the host seat, slot 1 on the screen, is shown greyed and cannot be
watched - its client would control the lobby); tick one and REPLAY makes the relay play that battle back to you from
that player's seat - his fog of war, his base, the whole battle as it happened; you can scroll the map
but not act.  The same encrypted connection, the same module.

Data: the screen scripts HD_<height>P\\ONLINE, REPLAYE and LOADALLE (INTRFACE\ONLINE / REPLAYE / LOADALLE at
640x480) are derived from LOADGE by this script (list widened to 56 columns, header, name, server and status
lines, ENTER / BACK; the replay screen: a 40-column list and the participant pane; the load screen: ONLINE
with the title Load Game and the button LOAD), their backgrounds ONLINEBG.GIF /
REPLAYBG.GIF from LOADER.GIF, and DEFAULT_SERVER.TXT is written beside the exe when it is missing or still
holds only the shipped address in its first, bare form - a file with your own relay address is never
overwritten.  The appended bytes are written
below in Base64 with their SHA-256; the C source they were compiled from is in the Dark-Colony-Server
repository.'''),
]

# Names of the patched builds and their desktop shortcuts since 25 Sep 2026 (maintainer: "resulting files and
# shortcuts names must be: 'Dark Colony map editor 1.2', 'Dark Colony', 'Dark Colony Ultimate'"; the editor's name
# became 'Dark Colony Map Editor' later that day at the maintainer's request); before that
# dc16new.exe, engexp16new.exe (DCEXP16.EXE 10-15 Sep 2026) and maped_ozi_ns_v1.2.exe.  `orig_path` is where the
# window finds the untouched original, relative to the repository root = the folder of the script.
# The Classic build (dc16.exe -> "Dark Colony.exe", deprecated 1 Oct 2026 because Dark Colony Ultimate plays the whole
# Dark Colony campaign) left this list on 5 Oct 2026 (maintainer: "remove deprecated 'Dark colony' option from installer
# completely as won't be needed anymore") together with its two Classic-only fixes `movies` and `sounds` and the
# deprecated-build plumbing (-IncludeDeprecated, the options-page box).  The order of this list is the order of the
# window's pages and of `-All`.
BUILDS = [
 dict(id='CouncilWars', g='cw', exe='Dark Colony Ultimate.exe', product='Dark Colony Ultimate', orig_name='ENGEXP16.EXE', orig_path='DC - Council wars\\ENGEXP16.EXE',
      title='Dark Colony - The Council Wars ENGEXP16.EXE, 659968 bytes (patched build: "Dark Colony Ultimate.exe" - Council Wars plus the Dark Colony, OZI and Academy campaigns; until 25 Sep 2026 engexp16new.exe)',
      source='the Council Wars CD holds exactly this file as EXPENG\\ENGEXP16.EXE - copy it into the "DC - Council wars" folder.',
      steps=['nocd', 'resolution', 'hdpaths', 'clock', 'console', 'cursor', 'pool', 'speed', 'ddraw', 'palette', 'camera', 'widemap', 'restore', 'longpath', 'music', 'menuorder', 'chat', 'netsave', 'fps', 'pointer', 'ozi', 'intro', 'icon', 'online']),
 dict(id='MapEditor', g='maped', exe='Dark Colony Map Editor.exe', product='Dark Colony Map Editor', orig_name='maped.exe', orig_path='Dark Colony - Map editor\\maped.exe',
      title='Dark Colony map editor maped.exe (Aug 1997, Borland C++), 336424 bytes (unlocked build: "Dark Colony Map Editor.exe", until 25 Sep 2026 maped_ozi_ns_v1.2.exe)',
      source='the Dark Colony CD holds exactly this file as DC\\MAPED.EXE - copy it into the "Dark Colony - Map editor" folder as maped.exe.',
      steps=MAPED_STEPS + ['icon']),
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
            ins_len = struct.unpack_from('<I', nxt, blk[0] + 4)[0] - blk[1]   # 15 entries + 1 pad since 25 Sep 2026 (12 from 23 Sep)
            assert 0 < ins_len <= 64 and ins_len % 4 == 0, ins_len
            ins = nxt[ins_at:ins_at + ins_len]
            assert cur[rend - ins_len:rend] == b'\0' * ins_len and nxt[ins_at + ins_len:rend] == cur[ins_at:rend - ins_len]
            special = dict(offset=ins_at, bytes=ins, before=cur[ins_at:ins_at + ins_len], section_end=rend,
                           note=f'.reloc table: insert {ins_len // 2} HIGHLOW entries ({", ".join(f"{v:04X}" for v in struct.unpack(f"<{ins_len // 2}H", ins))}) at the end of the page-0x7F000 block; bytes 0x{ins_at:X}..0x{rend-ins_len:X} move up by {ins_len}, the {ins_len} zero slack bytes 0x{rend-ins_len:X}..0x{rend:X} at the end of the section are dropped')
            covered.update(range(ins_at, rend))
            dirsz = pe + 24 + 96 + 5 * 8 + 4
            blocks = blocks + [
                (dirsz, 4, f'PE optional header: base-relocation directory size 0x{struct.unpack_from("<I", cur, dirsz)[0]:X} -> 0x{struct.unpack_from("<I", nxt, dirsz)[0]:X} (+{ins_len})'),
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
        if step in APPENDS.get(g, {}):
            # model: a few header/code edits + the new section appended at the end of the file (icon, online)
            ap = APPENDS[g][step]
            assert ap['at'] == len(cur) and len(nxt) == len(cur) + ap['n'], (g, step, ap['at'], len(cur), len(nxt))
            tail = nxt[len(cur):]
            assert hashlib.sha256(tail).hexdigest() == ap['sha'], (g, step)
            special = dict(kind='append', offset=len(cur), bytes=tail, sha=ap['sha'], note=ap['note'], what=ap['what'])
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
        i = 0
        while i < len(states):
            step, nxt = states[i]
            fix = FIX_OF.get(step, step)
            if fix in COMPOSITE:
                # the consecutive replay steps of a composite fix -> one diff, one fix (1 Oct 2026)
                j = i
                while j + 1 < len(states) and FIX_OF.get(states[j + 1][0], states[j + 1][0]) == fix:
                    j += 1
                assert [s for s, _ in states[i:j + 1]] == [s for s in COMPOSITE[fix] if s in steps], (g, fix, mode)
                nxt = states[j][1]
                i = j
            out_.append(attribute(g, fix, cur, nxt, mode))
            cur = nxt
            i += 1
        per_mode[mode] = out_
        ref_sha[mode] = hashlib.sha256(cur).hexdigest()
        # the light theme = every fix but `console`: its edits reverted on the final bytes (nothing later touches them)
        con = [pd for pd in out_ if pd['P']['id'] == 'console']
        if con:
            light = bytearray(cur)
            for _, off, old, new, _ in con[0]['edits']:
                assert bytes(light[off:off + len(new)]) == new, (g, mode, hex(off))
                light[off:off + len(old)] = old
            ref_sha[mode + '/light'] = hashlib.sha256(light).hexdigest()
    # merge: a fix outside MODE_STEPS must come out identical in every mode it exists in (the tools
    # touch disjoint bytes, so the edits' old bytes do not depend on the mode); it is emitted once.
    # `resolution` (display + INTRF_HD paths + clock, one fix since 1 Oct 2026) once per HD mode.
    patches_out = []
    fix_ids = []
    for s in B['steps']:
        f = FIX_OF.get(s, s)
        if f not in fix_ids:
            fix_ids.append(f)
    for step in fix_ids:
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
                           ref_sha={m: sha for m, sha in ref_sha.items() if m}, final_sha=ref_sha.get(PUBLISHED_MODE, ref_sha[None] if None in ref_sha else None)))
    if None not in ref_sha:
        for m in HD_MODES:
            assert m + '/light' in ref_sha, (g, m)

# ----------------------------------------------------------------------------------------------
# emit PowerShell
# ----------------------------------------------------------------------------------------------
W(f'''<#
    Dark Colony patcher {PATCHER_VERSION}, build {PATCHER_BUILD} - generated {PATCHER_GENERATED}.

''')
W(r'''.SYNOPSIS
    Rebuilds the patched Dark Colony executables from the untouched originals, one documented
    patch at a time, so that anyone can see exactly which bytes change and why.

.DESCRIPTION
    The executables shipped in https://github.com/endotermic/Dark-Colony are the original 1997/98
    binaries with a handful of byte patches (no CD check, 1024x768, cursor fix, ...).  Because a
    hand-modified exe cannot be signed and looks suspicious to antivirus heuristics, this script
    makes the modification fully transparent and reproducible:

      * run without arguments it opens an installer window: Welcome -> Options (the screen
        resolution in a drop-down, 640x480 marked "(original)"; the battlefield interface, light =
        the classic metal one or dark = the console style of the menus - nothing is preselected,
        chosen once) -> one page per executable with its fixes, all ticked (Dark Colony Ultimate,
        the map editor) -> a summary of what will be written -> Patch -> Finished.  The originals
        beside this script are found by themselves; the result is "Dark Colony Ultimate.exe" and
        "Dark Colony Map Editor.exe" with a shortcut of the same name on the desktop - or drive it from the command line, see
        the examples
      * it never touches the input file; it writes a new file
      * every patch is a list of (file offset, old bytes, new bytes, reason) in plain text below
      * a byte is only written if the file still holds the documented old bytes at that offset
      * the SHA-256 of the input must match the known original (override with -Force, the
        per-byte checks stay on)
      * after writing it prints the SHA-256 of the result; with every patch selected the result
        is byte-identical to the executable published in the repository and the script says so
      * the screen resolution is chosen ONCE, explicitly: the window's first step (radio buttons, none
        preselected) or -Resolution on the command line (required for the games): 640x480 (original),
        1024x768, 1280x1024, 1280x720, 1280x800, 1920x1080, 1920x1200, 3840x1080; the sizes with your
        monitor's aspect ratio are marked "recommended for your screen"; the exes published in the
        repository are the 1024x768 build.  Everything the size changes is ONE fix per size
        ("WxH display: screen mode, interface data from INTRF_HD, clock hand")
      * the battlefield interface is chosen with it (-Theme light|dark, required at an HD size; the
        window's options page): LIGHT (classic) keeps the original brushed-metal HUD, dialogs and
        clock dial (the shipped INTRF_HD\<WxH>\INTRFACE_LIGHT.GIF as the frame); DARK is the console
        style of the menus drawn 28-30 Sep 2026 (INTRFACE.GIF, the INTRF_HD banks, the console dialog
        layouts) plus the one-edit fix "console" that points the exe at the redrawn clock dial.  The
        published exes are the dark 1024x768 build; 640x480 (original) has no theme to choose
      * for an HD resolution the script also WRITES the interface data the patched exe reads
        (INTRF_HD\, exp\intrf_hd\, ozi_ns\intrf_hd\: menu scripts, HUD script, briefing lists,
        letterboxed backgrounds, loading screens) from the stock 640x480 files of the game folder and
        the four pictures per size that ship with the game (INTRF_HD\<WxH>\INTRG.GIF, INTRO.GIF,
        BACKDROP.GIF, INTRFACE.GIF; the menu screens are laid over BACKDROP.GIF, the main menu's planet
        without its bottom band, inside a grey panel frame).  Re-encoding the GIF backgrounds needs a small GIF reader/writer: its C# SOURCE
        TEXT is in this file and is compiled in memory by Add-Type when the set is built, with the
        .NET compiler that is part of Windows (no download, no install, ~2 s).  Doing the same in
        plain PowerShell would take 15-30 s per set under Windows PowerShell 5.1 and minutes under
        PowerShell 7; if Add-Type is blocked on your PC, copy a pre-built set instead - see the
        "INTERFACE SET" section below for the details
      * a fix whose resources (data files it needs next to the exe: the INTRF_HD interface files,
        the ozi_ns overlay) are not in the target folder is marked
        "RESOURCES NOT FOUND", its checkbox cannot be ticked and -All skips it; a fix that depends
        on such a fix is marked the same way
      * since 5 Oct 2026 this script lives in the folder patcher\ of the repository together with a
        copy of the project's own data files: patcher\game\ (the painted backdrops and HUD frames per
        size, the console banks, the re-baked logo banks, the tracer bullets, the icon, the ozi_ns
        mission pack, the DARK COLONY mode's tables, DEFAULT_SERVER.TXT) is copied into the game folder
        and patcher\editor\ (the map editor's Borland runtime DLLs, the Atlantis block set) into the
        editor's folder before a build is patched - the repository's game folders hold the same files
        already, so there the copy changes nothing; a fix's data file counts as present when the
        resource folder holds it.  INSTALL.CMD + PATCH_HOWTO.TXT + patcher\ is the installer package
        published on ModDB: it holds nothing of the game itself.  The OZI save folder ozisave\ and
        its marker file ozisave.txt are not resources: the ozi step creates them when they are missing
      * a player without a game folder installs the game from the two ORIGINAL DISCS first (the
        welcome page's checkbox, ticked by itself when no game folder is found beside the package;
        -InstallDir with -CouncilWarsDisc and -DarkColonyDisc on the command line): the Council Wars
        disc (ENGEXP16.EXE, the expansion, the shared Classic data) and the Dark Colony disc (the
        missions, encyclopedia, cursors, briefings, the Classic movies, the map editor) are read as
        disc images (.iso, .bin, .cue) or from a drive, the game is copied into the install folder
        ("Dark Colony" in Documents by default, the map editor in its "Map editor" sub-folder), the
        resources are added and the executables are built there.  Which disc holds which file is the
        list $DiscFiles below (tools/discs.py); the disc reader is C# text in this file like the GIF
        codec.  The soundtrack - audio tracks 2-5 of both mixed-mode CDs - is ripped from a .bin / .cue
        image or a real drive and encoded to MP3 (192 kbit/s) with Windows' own encoder (WinRT
        MediaTranscoder; the "N" editions need the Media Feature Pack) into MUSIC\ and exp\music\, so
        fix music has its files; an .iso holds the data track only.

    The original of the game is "DC - Council wars\ENGEXP16.EXE" (ENGEXP16.EXE from the Council Wars CD;
    patched build "Dark Colony Ultimate.exe" - Council Wars plus the Dark Colony, OZI and Academy campaigns
    and the ONLINE WAR room browser), committed untouched in the repository.  The untouched Classic
    "dc16.exe" of the January 1998 update beside it is no longer patched: its build "Dark Colony.exe" was
    deprecated on 1 Oct 2026 and removed from this installer on 5 Oct 2026 - Dark Colony Ultimate plays
    the whole Dark Colony campaign.
    The second build is the map editor "Dark Colony - Map editor\maped.exe" (the original from the Dark
    Colony CD): its fixes clear the "disabled" flag on dialog controls the original greyed out - the
    functional part of the ozi_ns editor, without the Polish translation - and write
    "Dark Colony Map Editor.exe".  (Until 25 Sep 2026 the two were engexp16new.exe and maped_ozi_ns_v1.2.exe.)

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
    Path of the untouched original executable (ENGEXP16.EXE or the map editor's maped.exe).  With
    -All and no -Original the originals beside this script are patched, one after the other, each
    into its own output name.

.PARAMETER Output
    Where to write the patched copy (only together with -Original).  Default: "Dark Colony Ultimate.exe" /
    "Dark Colony Map Editor.exe" next to the original.
    An existing file is not overwritten unless -Overwrite is given.

.PARAMETER Resolution
    Screen resolution to patch for - REQUIRED for the game, there is no default (1 Oct 2026):
    640x480 (the original size: no display fix), 1024x768 (the published exes), 1280x1024, 1280x720,
    1280x800, 1920x1080, 1920x1200 or 3840x1080 (32:9).  The 'resolution' fix (screen mode, INTRF_HD
    paths, clock hand) exists once per HD size; all sizes share the one INTRF_HD data folder, which
    the patcher fills with the set built for the chosen size.  The window asks the same question on
    its first page (radio buttons, nothing preselected; the sizes with your monitor's aspect ratio
    are marked "recommended for your screen").

.PARAMETER Patches
    Patch ids to apply (see -List).  Order does not matter: they are always applied in the fixed
    canonical order.  Use -All for every patch of the build.  With neither, the window opens
    (with the original preloaded when -Original was given).

.PARAMETER IgnoreMissingData
    Apply fixes whose resources (the INTRF_HD interface files, the ozi_ns
    overlay, ...) are missing next to the output, or whose prerequisite fixes are not selected.
    Without it -All skips such fixes (reported as "resources not found") and an explicit -Patches
    list naming one is refused, because such an exe fails at start-up or draws garbage and the
    failure would look like a bug of the patch.  Each fix's Requires / Data lists say what it
    needs; -List prints them.

.PARAMETER Theme
    The battlefield interface, REQUIRED for a game at an HD resolution (no default): light = the
    original brushed-metal HUD, dialogs and clock dial (classic); dark = the console style of the
    menus (grey pipework frame, red-ringed buttons, black dialogs, redrawn clock dial; fix
    "console").  Not asked at 640x480 or for the map editor.  The window's options page asks the
    same question with two radio buttons, none preselected.

.PARAMETER InstallDir
    Install the game from the two original discs into this folder first (created if missing; the map
    editor goes into its "Map editor" sub-folder), then patch Dark Colony Ultimate and the map editor
    there.  Needs -CouncilWarsDisc, -DarkColonyDisc and -All (with -Resolution and -Theme).  Files
    already in the folder with the right size are kept, so an interrupted install can be resumed.

.PARAMETER CouncilWarsDisc
    The Council Wars disc: a disc image (.iso, .bin, .cue) or the drive / folder holding it.  Must
    carry EXPENG\ENGEXP16.EXE (the English expansion; the exe is verified by SHA-256).

.PARAMETER DarkColonyDisc
    The Dark Colony disc: a disc image or drive with DC\GAMESTAT, DC\SCENARIO and DC\MAPED.EXE.

.PARAMETER DesktopShortcut
    After a successful write, put a shortcut to the patched exe on the desktop ("Dark Colony",
    "Dark Colony - Council Wars" or "Dark Colony Map Editor"; start folder = the game folder, which
    the game needs to find its data).  An existing shortcut of that name is replaced.  The window
    does the same with its "Desktop shortcut" checkbox (ticked by default).

.PARAMETER Verify
    Instead of patching, inspect an existing exe: which build it is and which patches it carries.

.EXAMPLE
    .\Apply-DarkColonyPatches.ps1                               # the installer window
    .\Apply-DarkColonyPatches.ps1 -All -Resolution 1024x768 -Theme dark -DesktopShortcut
        (Dark Colony Ultimate and the map editor, as the window does)
    .\Apply-DarkColonyPatches.ps1 -List
    .\Apply-DarkColonyPatches.ps1 -List -Detail                 # every single byte edit
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Council wars\ENGEXP16.EXE" -All -Resolution 1920x1200 -Theme light -DesktopShortcut
        (run from the root of the Dark-Colony repository, where this file lives; writes "Dark Colony Ultimate.exe")
    .\Apply-DarkColonyPatches.ps1 -Original "DC - Council wars\ENGEXP16.EXE" -Resolution 640x480 -Patches nocd
        (the original 640x480 game that just does not ask for the CD)
    .\Apply-DarkColonyPatches.ps1 -Original "Dark Colony - Map editor\maped.exe" -All     (-> "Dark Colony Map Editor.exe")
    .\Apply-DarkColonyPatches.ps1 -Verify "DC - Council wars\Dark Colony Ultimate.exe"
    .\Apply-DarkColonyPatches.ps1 -InstallDir "$env:USERPROFILE\Documents\Dark Colony" -CouncilWarsDisc D:\ -DarkColonyDisc "E:\Dark Colony.iso" -All -Resolution 1920x1080 -Theme dark -DesktopShortcut
        (the game from the two discs into a new folder, then Dark Colony Ultimate and the map editor built there)

.NOTES
    Double-click INSTALL.CMD in the folder above this one (the package / repository root): it starts this script with Windows
    PowerShell 5.1 and -ExecutionPolicy Bypass for that one run (Windows' own "Run with PowerShell"
    obeys the execution policy, which refuses a script from a downloaded ZIP), and passes any
    command-line options on.  Without it, if Windows refuses to run the script ("running scripts is
    disabled"), start it with
        powershell -ExecutionPolicy Bypass -File .\Apply-DarkColonyPatches.ps1
    Relative paths are taken from PowerShell's current location.
    Offsets are 0-based file offsets, written as PowerShell hex literals (0x431F).  Bytes are
    upper-case hex separated by spaces.  Code addresses quoted in the comments are virtual
    addresses (VA) inside the loaded image: VA = file offset + 0x400C00 for code, DGROUP data
    VA = file offset + 0x402600 (Council Wars; 0x402800 in the Classic dc16.exe).
#>
[CmdletBinding(DefaultParameterSetName = 'Apply')]
param(
    [Parameter(ParameterSetName = 'Apply', Position = 0)] [string] $Original,
    [Parameter(ParameterSetName = 'Apply')] [string] $Output,
    [Parameter(ParameterSetName = 'Apply')] [string[]] $Patches,
    [Parameter(ParameterSetName = 'Apply')] [string] $Resolution,
    [Parameter(ParameterSetName = 'Apply')] [string] $Theme,
    [Parameter(ParameterSetName = 'Apply')] [switch] $All,
    [Parameter(ParameterSetName = 'Apply')] [switch] $Overwrite,
    [Parameter(ParameterSetName = 'Apply')] [switch] $Force,
    [Parameter(ParameterSetName = 'Apply')] [switch] $IgnoreMissingData,
    [Parameter(ParameterSetName = 'Apply')] [switch] $DesktopShortcut,
    [Parameter(ParameterSetName = 'Apply')] [string] $InstallDir,
    [Parameter(ParameterSetName = 'Apply')] [string] $CouncilWarsDisc,
    [Parameter(ParameterSetName = 'Apply')] [string] $DarkColonyDisc,
    [Parameter(ParameterSetName = 'List')] [switch] $List,
    [Parameter(ParameterSetName = 'List')] [switch] $Detail,
    [Parameter(ParameterSetName = 'Verify')] [string] $Verify
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
# Culture-invariant (2 Oct 2026, a player's report): this script generates byte-exact files, so nothing in it may
# depend on the player's Windows regional format.  Under a Turkish or Azerbaijani format .NET's ToLower() turns
# "MAINE" into "maıne" (dotless i), the HUD and intro scripts missed their branches in Write-InterfaceSet and the
# battlefield widgets were laid out as a letterboxed menu (tabs and DAYS count drawn inside the map).  Every case
# conversion below is the Invariant form, and the thread runs with the invariant culture (string comparison,
# hashtable keys, regex IgnoreCase and number formatting included) for the whole run, window or command line.
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Threading.Thread]::CurrentThread.CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture

# =================================================================================================
#  DATA - the two builds and their patches
#
#  Each edit:  @{ Offset = <file offset>; Old = '<hex bytes>'; New = '<hex bytes>'; Note = '<why>' }
#  One special edit kind (OZI patch only): @{ Insert = <offset>; Bytes = '<16 bytes>'; Before = '<the
#  16 bytes found there before>'; SectionEnd = <offset>; Note = ... } - inserts Bytes at Insert and
#  drops the 16 zero bytes just before SectionEnd, so the file size does not change.
#  And one that grows the file (the icon and online fixes, the last ones of a build): @{ Append = <offset = the file's
#  length before>; Sha256 = '<of the appended bytes>'; Length = <n>; Base64 = '<the appended bytes>' }.
# =================================================================================================
''')
W(f'''# Version and build of this patcher (maintainer, 2 Oct 2026): the version is set by hand in the generator when the
# patcher's behaviour changes, the build is the UTC time of the generation (YYYYMMDD.HHMM) - the commits it was
# generated from are in the header above.
$PatcherVersion = '{PATCHER_VERSION}'
$PatcherBuild = '{PATCHER_BUILD}'
$PatcherGenerated = '{PATCHER_GENERATED}'
$script:BannerShown = $false   # the command-line banner is printed once (Set-StrictMode: declare before reading)
''')
W(r'''$Builds = @(
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
        PatchedSha256  = {ps_str(bd['final_sha'])}   # every patch applied at the published resolution (1024x768, dark interface) = the exe in the repository
        # screen resolutions this build can be patched for: '640x480' = the original size (no display fix),
        # the others select that size's variant of the 'resolution' fix below.  One of them must be chosen
        # explicitly (window page 1 / -Resolution): there is no default (1 Oct 2026)
        Modes          = @({', '.join(ps_str(m) for m in bd['modes'])})
        PublishedMode  = {ps_str(PUBLISHED_MODE if bd['modes'] else '')}
        # SHA-256 with every fix of that resolution applied (the published one is the exe in the repository); 'WxH' = the
        # dark battlefield interface (every fix), 'WxH/light' = the light one (without fix console)
        ReferenceSha256 = @{{ {'; '.join(f"{ps_str(k)} = {ps_str(v)}" for k, v in bd['ref_sha'].items()) if bd['modes'] else f"{ps_str('')} = {ps_str(bd['final_sha'])}"} }}
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
                        "\n                # (Write-InterfaceSet); these five pictures cannot be derived and ship with the game (two HUD frames: dark / light)"
                        f"\n                SetSources = @({', '.join(ps_str(x) for x in srcs)})")
        W(f'''            @{{
                Id = {ps_str(P['id'])}; Name = {ps_str(name)}; Date = {ps_str(P['date'])}
                # $null = part of every resolution, 'hd' = every resolution but 640x480, 'WxH' = that one only
                Mode = {ps_str(pd['mode']) if pd['mode'] else '$null'}{datasize}
                # $null = part of both battlefield interface themes, 'dark' = only with the dark one (chosen with the resolution)
                Theme = {ps_str(P['theme']) if P.get('theme') else '$null'}
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
            W(f'                    # (Base64 of the {len(sp["bytes"])} appended bytes; decode it to see them - {sp["what"]})')
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
    try { return ([BitConverter]::ToString($sha.ComputeHash($Data)) -replace '-', '').ToLowerInvariant() }
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
            try { $h = ([BitConverter]::ToString($sha.ComputeHash($Data, $Edit.Append, $Edit.Length)) -replace '-', '').ToLowerInvariant() }
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
                   "(ENGEXP16.EXE in 'DC - Council wars', maped.exe in the editor folder), not to an already patched build.") -f $Patch.Id, $where, $why
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
# The folder a resolution's interface set lives in (2 Oct 2026, maintainer: "absolutely isolate files for different
# resolutions to their own folders, so resources are never mixed"): HD_<height>P, UW_ for the ultra-wide sizes - 8
# characters, the length of the `intrface` the exe's path strings are rewritten in place with (hd_1080p/bintro).  The
# patcher's inputs (the shipped pictures per size, the console banks) live in HD_SRC.  Every other size's folder is
# deleted when a set is written (Remove-OtherInterfaceSets).
function Get-HdFolder([string] $Mode) { $wh = Get-ModeSize $Mode; return ('{0}_{1:D4}P' -f $(if ($wh[0] * 2 -gt $wh[1] * 5) { 'UW' } else { 'HD' }), $wh[1]) }
function Get-HdToken([string] $Mode) { return (Get-HdFolder $Mode).ToLowerInvariant() }
$HD_SRC = 'HD_SRC'
$HD_FOLDER_RE = [regex] '^(?i)(HD|UW)_\d{4}P$'
# the copies the 640x480 build reads (fixes ozi / music / online at the original size)
$STOCK_COPIES = @('exp\intrface\bintoze', 'dc\intrface\bintoze', 'ozi_ns\intrface\bintoze', 'exp\intrface\lopme', 'dc\intrface\lopme', 'ozi_ns\intrface\lopme',
                  'INTRFACE\ONLINE', 'INTRFACE\ONLINEBG.GIF', 'INTRFACE\REPLAYE', 'INTRFACE\REPLAYBG.GIF', 'INTRFACE\LOADALLE')

# What a run for $Keep (a folder name, or '' for a 640x480 build) deletes: every other resolution's folder under the
# game folder and under exp\, dc\, ozi_ns\ (HD_*P / UW_*P and the pre-October INTRF_HD / intrf_hd), plus - for an
# HD build - the 640x480 copies.  Returns the paths relative to $GameDir.
function Get-OtherInterfaceSets([string] $GameDir, [string] $Keep) {
    $out = @()
    if (-not $GameDir -or -not (Test-Path -LiteralPath $GameDir)) { return $out }
    foreach ($sub in '', 'exp', 'dc', 'ozi_ns') {
        $d = if ($sub) { Join-Path $GameDir $sub } else { $GameDir }
        if (-not (Test-Path -LiteralPath $d)) { continue }
        foreach ($f in [System.IO.Directory]::GetDirectories($d)) {
            $name = [System.IO.Path]::GetFileName($f)
            if ($name -ieq $Keep -and $Keep) { continue }
            if ($HD_FOLDER_RE.IsMatch($name) -or $name -ieq 'INTRF_HD') { $out += $(if ($sub) { "$sub\$name" } else { $name }) }
        }
    }
    if ($Keep) { foreach ($rel in $STOCK_COPIES) { if (Test-Path -LiteralPath (Join-Path $GameDir $rel)) { $out += $rel } } }
    return $out
}
function Remove-OtherInterfaceSets([string] $GameDir, [string] $Keep) {
    $lines = @()
    foreach ($rel in @(Get-OtherInterfaceSets $GameDir $Keep)) {
        $p = Join-Path $GameDir $rel
        if ([System.IO.Directory]::Exists($p)) {
            $n = @([System.IO.Directory]::GetFiles($p, '*', 'AllDirectories')).Count
            [System.IO.Directory]::Delete($p, $true)
            $lines += ('deleted {0}\ ({1} files - another resolution''s interface set; nothing of another size is left)' -f $rel, $n)
        } elseif ([System.IO.File]::Exists($p)) {
            [System.IO.File]::Delete($p)
            $lines += ('deleted {0} (a 640x480 copy)' -f $rel)
        }
    }
    return $lines
}
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
# "640x480 (original)" for the stock size (1 Oct 2026, maintainer: "mark 640x480 as (original)"), else
# "1280x800 (16:10)" plus "recommended for your screen" when the aspect ratio is the monitor's.  Nothing
# is preselected anywhere: the window's first step and the command line's -Resolution make the choice.
function Format-ModeLabel([string] $Mode, $MonitorSize) {
    if ($Mode -eq '640x480') { return '640x480 (original)' }
    $wh = Get-ModeSize $Mode
    $label = '{0} ({1})' -f $Mode, (Get-AspectLabel $wh[0] $wh[1])
    if (Test-ModeRecommended $Mode $MonitorSize) { $label += ' recommended for your screen' }
    return $label
}

# The fixes of a build for one resolution.  Mode $null = part of every resolution, 'hd' = every
# resolution but 640x480, 'WxH' = that resolution's variant of the fix (the display fix exists once per
# HD size; 640x480 has its own variants of ozi and music).  Mode '' (none chosen yet) = the fixes
# every resolution shares.
function Get-BuildPatches($Build, [string] $Mode, [string] $Theme = '') {
    $out = @()
    foreach ($p in $Build.Patches) {
        if ($p.Theme -and $p.Theme -ne $Theme) { continue }                     # a fix of the other battlefield interface theme
        $m = $p.Mode
        if (-not $m) { $out += $p; continue }                                   # every resolution
        if ($Mode -and $m -eq $Mode) { $out += $p; continue }                  # this resolution's own variant (also 640x480)
        if ($m -eq 'hd' -and $Mode -and $Mode -ne '640x480') { $out += $p }    # the shared HD variant
    }
    return $out      # callers wrap it in @(); an empty list comes back as an empty array
}

# The battlefield interface theme to use: '' for a build without resolutions and at 640x480 (the game keeps
# its own interface), else the validated -Theme - required, there is no default (1 Oct 2026).
function Resolve-Theme($Build, [string] $Mode, [string] $Theme) {
    if (@($Build.Modes).Count -eq 0 -or -not $Mode -or $Mode -eq '640x480') { return '' }
    if (-not $Theme) {
        throw ("choose the battlefield interface for {0} with -Theme light (the original metal HUD, dialogs and clock dial) or -Theme dark (the console style of the menus, fix console). The executables published in the repository are the dark {1} build." -f $Build.ProductName, $Build.PublishedMode)
    }
    if (('light', 'dark') -notcontains $Theme) { throw ("unknown theme '{0}'; valid: light, dark" -f $Theme) }
    return $Theme
}

# The resolution to use for a build: the validated -Resolution; '' for a build without resolutions (the map
# editor).  There is no default (1 Oct 2026): a game build without -Resolution is refused with the list.
function Resolve-Mode($Build, [string] $Mode) {
    $modes = @($Build.Modes)
    if ($modes.Count -eq 0) { return '' }
    if (-not $Mode) {
        $mon = Get-MonitorSize
        throw ("choose the screen resolution for {0} with -Resolution <WxH>: {1}. 640x480 is the original size (no display fix); the executables published in the repository are the {2} build." -f
               $Build.ProductName, (($modes | ForEach-Object { Format-ModeLabel $_ $mon }) -join ', '), $Build.PublishedMode)
    }
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
    if ($sha -eq $b.PatchedSha256) { $lines += '= the fully patched executable published in the repository (1024x768, dark battlefield interface).' }
    else { foreach ($k in @($b.ReferenceSha256.Keys)) { if ($k -and $b.ReferenceSha256[$k] -eq $sha) { $lines += ('= every fix applied for {0} (the reference build of the generator{1}).' -f $k.Replace('/light', ', light battlefield interface'), ', not the published exe') } } }
    $lines += ''
    # a fix with per-resolution variants (resolution) is reported once, with the variant found
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
        $dst = Join-Path $GameDir ((Get-HdFolder $Mode) + '\' + $name)
        $have = if (Test-Path -LiteralPath $dst) { Get-BmpSize $dst } else { $null }
        if ($have -and $have[0] -eq $W -and $have[1] -eq $H) { continue }
        if (-not (Test-Path -LiteralPath $src)) {
            # only reachable with -IgnoreMissingData (the stock pair is in the fix's Data list)
            $lines += ('{1}\{0} NOT written: the stock INTRFACE\{0} is not in this folder' -f $name, (Get-HdFolder $Mode))
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
#  (pad_background.py, paint_intro.py, hud_layout.py, split_hd_data.py, build_ozi_overlay.py), reproduced here line by line; the output is byte-identical for every text
#  file and pixel-identical for every picture, checked against the tools' output for all sizes.
#
#  Four pictures per size cannot be derived and ship with the game: INTRF_HD\<WxH>\INTRG.GIF and
#  INTRO.GIF (the procedurally painted main-menu planet, with the Take 2 / SSI bottom bands),
#  BACKDROP.GIF (the same planet without a band: since 1 Oct 2026 the ground of every letterboxed
#  menu screen, which sits on it in a grey panel frame instead of on black - doc 10.56) and
#  INTRFACE.GIF (the HUD frame).
#
#  ---- A NOTE ON THE COMPILED CODE BELOW ------------------------------------------------------------
#  The 15 menu backgrounds are GIF files.  The game's loader (gifload.c) insists that the picture is
#  exactly the size of the screen, so each 640x480 picture has to be decoded, centred on the
#  WIDTHxHEIGHT backdrop (remapped into the picture's own palette) and encoded again (LZW).  That inner loop runs over some 30 million pixels per
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
// skipped), centre it on a canvas of another size - black, or the backdrop picture remapped into the
// GIF's palette with a grey panel frame around the picture (pad_background.compose_over_backdrop,
// doc 10.56) - and write it back as a plain GIF the game's
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
    // pad_background.nearest_grey: the neutral entry (never 0) nearest to g, the first of equals
    static int NearestGrey(byte[] pal, int g)
    {
        int best = -1, bestD = 1 << 30;
        for (int i = 1; i < 256; i++)
        {
            int r = pal[3 * i];
            if (r == pal[3 * i + 1] && r == pal[3 * i + 2] && Math.Abs(r - g) < bestD) { bestD = Math.Abs(r - g); best = i; }
        }
        if (best < 0) throw new Exception("palette has no neutral grey entry");
        return best;
    }

    // the panel frame around the picture, outside in (pad_background.FRAME_GREYS)
    static readonly int[] FrameGreys = new int[] { 35, 107, 35, 11, 11 };

    public static byte[] Pad(byte[] src, int W, int H) { return Pad(src, W, H, null); }

    // backdrop: the BACKDROP.GIF of the size (null = black border).  pad_background.pad_gif /
    // compose_over_backdrop: every backdrop palette entry goes to the nearest entry of the picture's
    // palette (index 0 excluded, squared RGB distance, first of equals), black to the padding index;
    // then the frame rings with the palette's nearest greys, the outer ring's corner pixels ground
    // and the light ring's corners 35; then the picture itself.
    public static byte[] Pad(byte[] src, int W, int H, byte[] backdrop)
    {
        Image im = Decode(src);
        if (im.Width > W || im.Height > H) throw new Exception("picture " + im.Width + "x" + im.Height + " does not fit " + W + "x" + H);
        int pad = -1;
        for (int i = 0; i < 256; i++) if (im.Palette[i * 3] == 0 && im.Palette[i * 3 + 1] == 0 && im.Palette[i * 3 + 2] == 0) { pad = i; break; }
        if (pad < 0) throw new Exception("no black palette entry to pad with");
        byte[] canvas = new byte[W * H];
        int x0 = (W - im.Width) / 2, y0 = (H - im.Height) / 2;
        if (backdrop == null)
        {
            if (pad != 0) for (int i = 0; i < canvas.Length; i++) canvas[i] = (byte)pad;
        }
        else
        {
            Image bg = Decode(backdrop);
            if (bg.Width != W || bg.Height != H) throw new Exception("backdrop is " + bg.Width + "x" + bg.Height + ", not " + W + "x" + H);
            byte[] lut = new byte[256];
            for (int i = 0; i < 256; i++)
            {
                int r = bg.Palette[3 * i], g = bg.Palette[3 * i + 1], b = bg.Palette[3 * i + 2];
                if (r == 0 && g == 0 && b == 0) { lut[i] = (byte)pad; continue; }
                int best = 1, bestD = 1 << 30;
                for (int j = 1; j < 256; j++)
                {
                    int dr = r - im.Palette[3 * j], dg = g - im.Palette[3 * j + 1], db = b - im.Palette[3 * j + 2];
                    int d = dr * dr + dg * dg + db * db;
                    if (d < bestD) { bestD = d; best = j; }
                }
                lut[i] = (byte)best;
            }
            for (int i = 0; i < canvas.Length; i++) canvas[i] = lut[bg.Pixels[i]];
            int n = FrameGreys.Length;
            for (int k = 0; k < n; k++)
            {
                byte gi = (byte)NearestGrey(im.Palette, FrameGreys[k]);
                int X0 = x0 - n + k, Y0 = y0 - n + k, X1 = x0 + im.Width + n - 1 - k, Y1 = y0 + im.Height + n - 1 - k;
                for (int x = X0; x <= X1; x++) { canvas[Y0 * W + x] = gi; canvas[Y1 * W + x] = gi; }
                for (int y = Y0; y <= Y1; y++) { canvas[y * W + X0] = gi; canvas[y * W + X1] = gi; }
            }
            {
                int X0 = x0 - n, Y0 = y0 - n, X1 = x0 + im.Width + n - 1, Y1 = y0 + im.Height + n - 1;
                byte g11 = (byte)NearestGrey(im.Palette, 11), g35 = (byte)NearestGrey(im.Palette, 35);
                canvas[Y0 * W + X0] = g11; canvas[Y0 * W + X1] = g11; canvas[Y1 * W + X0] = g11; canvas[Y1 * W + X1] = g11;
                canvas[(Y0 + 1) * W + X0 + 1] = g35; canvas[(Y0 + 1) * W + X1 - 1] = g35; canvas[(Y1 - 1) * W + X0 + 1] = g35; canvas[(Y1 - 1) * W + X1 - 1] = g35;
            }
        }
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
$BACKGROUND = [regex] '(?im)^[ \t]*background[ \t]+(?:intrface/|intrf_hd/|(?:hd|uw)_\d{4}p/)?(\S+)'
$BG_RETARGET = [regex] '(?im)^([ \t]*background[ \t]+)intrface/(\S+)'
$PIC_RETARGET = [regex] '(?im)^([ \t]*pictures[ \t]+)intrface/(mainbut|popp)\b'          # the console-style banks INTRF_HD\MAINBUT.SPR / POPP.SPR (doc 10.49)
$TAB_STRIP = [regex] '(?m)^(picture[ \t]+[3456][ \t]+0[ \t]+)(\d+)([ \t]+)96([ \t]+)(?:110|124|120)([ \t]+)(?:12|16)(?=\s)'
$HUD_TEXT = [regex] '(?m)^(in_text[ \t]+(148|200|234)[ \t]+\d+[ \t]+)(\d+)([ \t]+)(\d+)'
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
        if ($words.Count -gt 0 -and $Kinds -contains $words[0].ToLowerInvariant()) {
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
    if ($w.Count -lt 5 -or $POSITIONED -notcontains $w[0].ToLowerInvariant()) { return $null }
    for ($i = 1; $i -le 4; $i++) { if ($w[$i] -notmatch '^-?\d+$') { return $null } }
    $ww = 0; $hh = 0; $rest = @()
    if ($w.Count -ge 7 -and $w[5] -match '^\d+$' -and $w[6] -match '^\d+$') { $ww = [int]$w[5]; $hh = [int]$w[6]; if ($w.Count -gt 7) { $rest = $w[7..($w.Count-1)] } }
    elseif ($w.Count -gt 5) { $rest = $w[5..($w.Count-1)] }
    return @{ kind = $w[0].ToLowerInvariant(); n = [int]$w[1]; x = [int]$w[3]; y = [int]$w[4]; w = $ww; h = $hh; rest = @($rest) }
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
# bottom-bar furniture (y >= 454) slides down (and right from the message box's end), the two battlefield
# chat lines in the band 420..453 follow the map view's bottom edge (down by dy minus the spare rows the
# bar absorbs: 16 at 1280x720 and 1920x1200, 24 at 1920x1080; 28 Sep 2026), the one in-view widget
# (PAUSED) by half the growth; size -> W H
function Edit-HudScript([string] $Text, [int] $W, [int] $H, [bool] $Console = $true) {
    $dx = $W - 640; $dy = $H - 480; $sy = ($H - 32) % 32
    $move = { param($fields) if ($fields.Count -lt 5 -or $fields[3] -notmatch '^\d+$' -or $fields[4] -notmatch '^\d+$') { return $null }
              $x = [int]$fields[3]; $y = [int]$fields[4]
              if ($x -ge 516) { $nx = $x + $dx; $ny = if ($y -ge 399) { $y + $dy } else { $y } }
              elseif ($y -ge 454) { $nx = if ($x -ge 300) { $x + $dx } else { $x }; $ny = $y + $dy }
              elseif ($y -ge 420) { $nx = if ($x -ge 300) { $x + $dx } else { $x }; $ny = $y + $dy - $sy }
              else { $nx = $x + [int][Math]::Floor($dx / 2); $ny = $y + [int][Math]::Floor($dy / 2) }
              if ($nx -eq $x -and $ny -eq $y) { return $null }
              return @($nx, $ny) }
    $t = Add-ChatLines (Edit-Widgets $Text $move $HUD_KINDS)
    if (-not $Console) {         # the light theme keeps the stock strips and text positions
        $m = $SIZE2.Match($t)
        if ($m.Success) { $t = $t.Substring(0, $m.Index) + ('{0}size{1}{2} {3}{4}' -f $m.Groups[1].Value, $m.Groups[2].Value, $W, $H, $m.Groups[6].Value) + $t.Substring($m.Index + $m.Length) }
        return $t
    }
    # hud_console.edit_hud_script (28 Sep 2026, console-style HUD, doc 10.49): the three tab strips
    # `picture 3..6` (stock 110x12 at x 521) are the 124x16 BUTTON.SPR strips at the panel's left edge
    $tx = [string](516 + $dx)
    $t = $TAB_STRIP.Replace($t, { param($m) $m.Groups[1].Value + $tx + $m.Groups[3].Value + '96' + $m.Groups[4].Value + '120' + $m.Groups[5].Value + '16' })
    # hud_console.HUD_TEXT_POS: the bar's two texts (in_text 148 / 200) 2 px higher inside the message screen, the DAYS count
    # (in_text 234) at (609, 429) centred in its screen (doc 10.54)
    $t = $HUD_TEXT.Replace($t, { param($m)
        switch ($m.Groups[2].Value) { '148' { $nx = $m.Groups[3].Value; $y = 460 } '200' { $nx = $m.Groups[3].Value; $y = 461 } default { $nx = [string](607 + $dx); $y = 430 } }
        $m.Groups[1].Value + $nx + $m.Groups[4].Value + [string]($y + $dy) })
    $m = $SIZE2.Match($t)
    if ($m.Success) { $t = $t.Substring(0, $m.Index) + ('{0}size{1}{2} {3}{4}' -f $m.Groups[1].Value, $m.Groups[2].Value, $W, $H, $m.Groups[6].Value) + $t.Substring($m.Index + $m.Length) }
    return $t
}

# hud_layout.chat_lines (fix `chat`, 28 Sep 2026): six battlefield chat lines instead of two.  The patched exes
# show chat line i in widget 203 + i (i < 2) / 205 + i (i >= 2) and draw a line only if its widget exists, so
# `in_text 207..210` are inserted after the (already shifted) `in_text 203`, each a copy of that line with the id
# and the y replaced, 15 rows further up per line (205/206 are the HUD's count widgets).  Nothing is added when a
# 207 line is already there.
function Add-ChatLines([string] $Text) {
    $lines = $Text.Split("`n")
    foreach ($ln in $lines) {
        $i = $ln.IndexOf('%'); $body = if ($i -ge 0) { $ln.Substring(0, $i) } else { $ln }
        $w = @(@($TOKENS.Matches($body) | ForEach-Object { $_.Value }) | Where-Object { $_.Trim().Length -gt 0 })
        if ($w.Count -ge 2 -and $w[0] -eq 'in_text' -and $w[1] -eq '207') { return $Text }
    }
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($ln in $lines) {
        $out.Add($ln)
        $i = $ln.IndexOf('%')
        $body = if ($i -ge 0) { $ln.Substring(0, $i) } else { $ln }
        $rest = if ($i -ge 0) { $ln.Substring($i) } else { '' }
        $toks = @($TOKENS.Matches($body) | ForEach-Object { $_.Value })
        $words = @($toks | Where-Object { $_.Trim().Length -gt 0 })
        if ($words.Count -ge 5 -and $words[0] -eq 'in_text' -and $words[1] -eq '203') {
            $y = [int]$words[4]
            for ($k = 2; $k -le 5; $k++) {
                $t = @($toks); $n = 0
                for ($j = 0; $j -lt $t.Count; $j++) {
                    if ($t[$j].Trim().Length -eq 0) { continue }
                    $n++
                    if ($n -eq 2) { $t[$j] = [string](205 + $k) }
                    elseif ($n -eq 5) { $t[$j] = [string]($y - 15 * $k); break }
                }
                $out.Add((-join $t) + $rest)
            }
        }
    }
    return ($out -join "`n")
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
        $prevAvi = $ln.Trim().ToLowerInvariant().EndsWith('.avi')
        $out.Add($ln)
    }
    return ($out -join "`n")
}

# split_hd_data: `background intrface/<gif>` -> `intrf_hd/<gif>` (every background of a generated script moved)
# hud_console.apply: MAINE's `pictures intrface/mainbut` and the four battlefield dialogs' `pictures intrface/popp`
# -> `intrf_hd/...`, the console-style banks that ship in INTRF_HD (the stock banks stay for the original exe)
# $Console $false (the light theme) keeps `pictures intrface/mainbut|popp` = the stock metal banks
function Set-BackgroundHd([string] $Text, [bool] $Console = $true, [string] $Token = 'intrf_hd') {
    $t = $BG_RETARGET.Replace($Text, ('$1' + $Token + '/$2'))                 # the GIFs sit in the resolution's folder
    if ($Console) { $t = $PIC_RETARGET.Replace($t, ('$1' + $HD_SRC.ToLowerInvariant() + '/$2')) }   # the console banks ship once, in HD_SRC
    return $t
}

# split_hd_data.rename_dat_list: the per-screen FIN lists name the re-baked logo banks
function Edit-DatList([string] $Text) {
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($raw in $Text.Split("`n")) {
        $cr = if ($raw.EndsWith("`r")) { "`r" } else { '' }
        $line = if ($cr) { $raw.Substring(0, $raw.Length - 1) } else { $raw }
        if (@('dcss.fin', 'dcuk.fin', 'dcut.fin') -contains $line.Trim().ToLowerInvariant()) { $line = $line.Trim().Substring(0, $line.Trim().Length - 4) + '_hd.fin' }
        $out.Add($line + $cr)
    }
    return ($out -join "`n")
}

# build_ozi_overlay.menu_layout (23 Sep 2026, maintainer's order; 3 Oct 2026: one LOAD GAME button):
# the patched Council Wars menu has five rows.  The numbers are the exe's button ids, which pick the
# handler (patch_ozi_menu.py rewires 16 to the pack and adds 6; patch_online.py adds 8 and 9 and
# replaces button 2's picker by the save browser over every campaign's folder), so only positions
# and labels move:
#
#     ACADEMY       (1)   ONLINE WAR         (8)
#     DARK COLONY   (6)   REPLAY ONLINE GAME (9)
#     COUNCIL WARS  (0)   CUSTOM NET WAR     (3)
#     OZI MISSIONS (16)   ENCYCLOPEDIA       (5)
#
#     LOAD GAME     (2)   QUIT              (12)
# The patched Council Wars main menu (doc 10.35, 10.36 and 10.67), the PowerShell twin of
# tools/build_ozi_overlay.py menu_layout()/menu_script(): Classic's 2x4 button grid becomes five
# rows per column - the four campaigns and the one LOAD GAME button on the left, the three screens
# that are not a campaign, ENCYCLOPEDIA and QUIT on the right (from 23 Sep to 3 Oct 2026 seven rows:
# a load button under each campaign; LOAD DC GAME 7 and LOAD OZI GAME 4 leave the script, their
# plates and labels with them) - a gap of half a button height (12 px) after row 4 and the same
# gap between the columns, after which the block is re-centred on the screen.  Vertically the rows
# hang from the DCUT title gadget (24 Sep 2026, maintainer: "return back credentials [credits] for
# higher than 640x480 resolutions"): the first row 120 rows under it - 11 px, the stock 100-row
# credits box, 9 px - unless the bottom row would pass H-72 (the stock 640x480 bottom row 408,
# 2-3 px above the bottom artwork every backdrop starts at H-45); then the block stops there and
# the box gets shorter (the `resolution` fix writes its height; the seven-row block needed 94 rows
# at 1024x768 and 76 at 1280x720, the five-row block fits under the stock 100 everywhere).  At
# 640x480 the block grows upwards from row 408, the `ozi` fix removes the box (the seven-row block
# filled the 217-row band), and the gap shrinks by a pixel if the first row would touch the planet's
# crescent (rows 198..217).  Both anchors depend only on the
# title and the screen size, so applying this twice changes nothing.  The whole Council Wars cluster
# (title included, so the block follows) and the H-72 cap sit Get-MenuLift rows higher at the HD sizes.
# DARK COLONY is id 6, which the stock script used for the LARGEBUTTON gadget of button 0; the plates
# of buttons 0..3 move to 19, 20, 24 and 25, the new plates are 21 (DARK COLONY), 23 (ONLINE WAR) and
# 26 (REPLAY ONLINE GAME), and `banim` pairs all ten.  The new lines are cloned from the script's own
# `pushb 16` / `gadget 17` / `textmsg 8` so they keep its field layout.  The untouched exe keeps
# Classic's 2x4 grid and labels in exp\intrface\bintroe (doc 10.35).
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
    # left column: the four campaigns and LOAD GAME (2); right column: ONLINE WAR (8, 29 Sep 2026), REPLAY ONLINE GAME
    # (9, 2 Oct 2026), CUSTOM NET WAR (= MULTI PLAYER WAR, renamed 3 Oct 2026), ENCYCLOPEDIA, QUIT (3 Oct 2026: one load button, build_ozi_overlay.OZI_COLUMNS)
    $cols = @(@(1, 6, 0, 16, 2), @(8, 9, 3, 5, 12))
    $gapAfter = @(4)
    $stockButtons = @(0, 1, 2, 3, 4, 5, 12, 16)
    $stockGadgets = @(10, 11, 13, 17)   # 8 and 9 are renumbered (see $renum)
    $newButtons = @(6, 8, 9)
    $droppedButtons = @(4, 7)           # LOAD OZI GAME and LOAD DC GAME: their pushb, plate and label leave the script (3 Oct 2026)
    # widget ids are one object space for every kind: the plates 6, 7, 8 and 9 of buttons 0, 1, 2 and 3 move to
    # 19, 20, 24 and 25 so that the button ids 6, 7 (Dark Colony), 8 (ONLINE WAR) and 9 (REPLAY ONLINE GAME) are free
    $renum = @{ 6 = 19; 7 = 20; 8 = 24; 9 = 25 }
    $gadgetOf = @{ 0 = 19; 1 = 20; 2 = 24; 3 = 25; 4 = 10; 5 = 11; 6 = 21; 7 = 22; 8 = 23; 9 = 26; 12 = 13; 16 = 17 }
    $labelOf = @{ 6 = 9; 7 = 10; 8 = 11; 9 = 12 }
    $template = @{ 'pushb' = 16; 'gadget' = 17 }
    $banimId = 18
    # `banim` = the menu's opening wave: the first listed plate carries anim_oneoff, each finished
    # plate starts the next and reveals its button; the pair order is set below from the layout -
    # column by column (left first), each from top to bottom (build_ozi_overlay.menu_order)
    $plateFirst = 'anim_oneoff'; $plateRest = 'anim_stopped'
    $textTemplate = 8
    $stockTopLimit = 218
    # 3 = the stock LOAD GAME text again (LOAD CW GAME from 23 Sep to 3 Oct 2026); 5 = button 4's stock text, so that an
    # older output gets the stock line back; 10 = LOAD DC GAME, kept only for the drop list
    # 4 = CUSTOM NET WAR (was MULTI PLAYER WAR; maintainer, 3 Oct 2026: "rename 'multiplayer war' to 'CUSTOM NET WAR'")
    $labels = @{ 1 = 'COUNCIL WARS'; 2 = 'ACADEMY'; 3 = 'LOAD GAME'; 4 = 'CUSTOM NET WAR'; 5 = 'SINGLE PLAYER WAR'
                 8 = 'OZI MISSIONS'; 9 = 'DARK COLONY'; 10 = 'LOAD DC GAME'; 11 = 'ONLINE WAR'; 12 = 'REPLAY ONLINE GAME' }
    $xy = @{}
    foreach ($m in ([regex] '(?m)^\s*pushb\s+(\d+)\s+\d+\s+(\d+)\s+(\d+)\s').Matches($Text)) { $xy[[int]$m.Groups[1].Value] = @([int]$m.Groups[2].Value, [int]$m.Groups[3].Value) }
    $gadgets = @{}
    foreach ($m in ([regex] '(?m)^\s*gadget\s+(\d+)\s').Matches($Text)) { $gadgets[[int]$m.Groups[1].Value] = $true }
    $missing = @()
    $droppedGadgets = @($droppedButtons | ForEach-Object { [int] $gadgetOf[[int]$_] })
    # this function's own output lacks the dropped buttons and their plates
    foreach ($need in $stockButtons) { if (-not $xy.ContainsKey($need) -and -not ($droppedButtons -contains $need)) { $missing += "pushb $need" } }
    foreach ($need in $stockGadgets) { if (-not $gadgets.ContainsKey($need) -and -not ($droppedGadgets -contains $need)) { $missing += "gadget $need" } }
    # the stock grid has the plates as 6, 7, 8 and 9, this function's own output as 19, 20, 24 and 25 (the 23 Sep
    # form as 19, 20, 8, 9; the 29 Sep form as 19, 20, 24, 9): each pair needs one of its two ids
    foreach ($k in @($renum.Keys | Sort-Object)) { if (-not ($gadgets.ContainsKey([int]$k) -or $gadgets.ContainsKey([int]$renum[$k]))) { $missing += ('gadget {0}/{1}' -f $k, $renum[$k]) } }
    $b = ([regex] '(?m)^\s*banim\s+18\s+\d+\s+(\d+)\s+(\d+)\s').Match($Text)
    $placed = 0
    foreach ($col in $cols) { foreach ($id in $col) { if ($null -ne $id) { $placed++ } } }
    # stock grid (8), the 23 Sep form (10), the 29 Sep form (11), the 2 Oct form (12), this form (10 placed buttons)
    $pairs = @([string] $stockButtons.Count, [string] ($stockButtons.Count + 2), [string] ($stockButtons.Count + 3), [string] ($stockButtons.Count + 4), [string] $placed)
    if (-not $b.Success -or $b.Groups[1].Value -ne $b.Groups[2].Value -or -not ($pairs -contains $b.Groups[1].Value)) { $missing += 'banim 18 with 8, 10, 11 or 12 pairs' }
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
    # the wave order: by column x, then by row y (one integer key so 5.1 and 7 sort alike)
    $banimOrder = @(@($place.Keys) | Sort-Object { $place[[int]$_][0] * 100000 + $place[[int]$_][1] } | ForEach-Object { [int] $_ })
    $firstPlate = [int] $gadgetOf[[int]$banimOrder[0]]
    # the lines re-emitted after their template line, and the two dropped load buttons' lines, which simply leave
    $dropPushb = @($newButtons + $droppedButtons)
    $dropGadget = @(($newButtons + $droppedButtons) | ForEach-Object { [int] $gadgetOf[[int]$_] })
    $dropText = @(($newButtons + $droppedButtons) | Where-Object { $labelOf.ContainsKey([int]$_) } | ForEach-Object { [int] $labelOf[[int]$_] })
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
            if ($kind -eq 'gadget' -and $renum.ContainsKey($id)) {                  # free ids 6, 7, 8 and 9
                $line = Set-ScriptTokens $line @{ 2 = [string] $renum[$id] }
                $id = [int] $renum[$id]
            }
            if ($move.ContainsKey($id)) {
                $pos = $move[$id]
                $changes = @{ 4 = [string] $pos[0]; 5 = [string] $pos[1] }
                if ($kind -eq 'gadget') { $changes[9] = if ($id -eq $firstPlate) { $plateFirst } else { $plateRest } }   # a plate: the wave starts at the first
                $line = Set-ScriptTokens $line $changes
            }
            $out.Add($line + $cr)
            if ($id -eq $template[$kind]) {
                foreach ($new in $newButtons) {
                    $pos = $place[[int]$new]
                    $ident = if ($kind -eq 'pushb') { [int] $new } else { [int] $gadgetOf[[int]$new] }
                    $changes = @{ 2 = [string] $ident; 4 = [string] $pos[0]; 5 = [string] $pos[1] }
                    if ($kind -eq 'pushb') { $changes[12] = [string] $labelOf[[int]$new] }
                    else { $changes[9] = if ($ident -eq $firstPlate) { $plateFirst } else { $plateRest } }
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

# The whole set for one resolution.  The two campaign lists name the DC*.AVI endings when those movies are in
# the folder (the DARK COLONY mode of Dark Colony Ultimate plays them).  Returns text lines about what was written.
# $Console: the dark battlefield interface (fix `console` applied): the console HUD frame INTRFACE.GIF, the
# scripts pointed at the INTRF_HD banks, the tab strips and text positions of hud_console.edit_hud_script, the
# console dialog layouts.  $false = the light (classic) theme: INTRFACE_LIGHT.GIF as the frame, the stock banks and
# layouts (1 Oct 2026).
function Write-InterfaceSet([string] $GameDir, [string] $Mode, [bool] $Console = $true) {
    $wh = Get-ModeSize $Mode; $W = $wh[0]; $H = $wh[1]
    $dx0 = [int][Math]::Floor(($W - 640) / 2); $dy0 = [int][Math]::Floor(($H - 480) / 2)
    $lines = @()
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $folder = Get-HdFolder $Mode; $token = Get-HdToken $Mode
    $intrface = Join-Path $GameDir 'INTRFACE'; $hd = Join-Path $GameDir $folder; $gamestat = Join-Path $GameDir 'GAMESTAT'
    $src = Join-Path (Join-Path $GameDir $HD_SRC) $Mode
    $frame = if ($Console) { 'INTRFACE.GIF' } else { 'INTRFACE_LIGHT.GIF' }
    foreach ($need in 'INTRG.GIF', 'INTRO.GIF', 'BACKDROP.GIF', $frame) { if (-not (Find-CI $src $need)) { throw "$HD_SRC\$Mode\$need is missing: the painted backdrops and HUD frames for $Mode ship with the game and cannot be generated" } }
    # the maintainer's rule (2 Oct 2026): no file of another resolution stays anywhere - every other size's folder, the
    # pre-October INTRF_HD set and the 640x480 copies go, and this size's folder is rebuilt from scratch
    $lines += Remove-OtherInterfaceSets $GameDir $folder
    if (Test-Path -LiteralPath $hd) { [System.IO.Directory]::Delete($hd, $true) }
    [void] [System.IO.Directory]::CreateDirectory($hd)
    Initialize-GifCodec
    $written = 0
    $introScreens = @('bintroe', 'introe', 'buttonse', 'dintroe')
    $gifsToPad = @{}
    # --- INTRFACE scripts -> INTRF_HD
    foreach ($f in [System.IO.Directory]::GetFiles($intrface)) {
        $name = [System.IO.Path]::GetFileName($f); $lname = $name.ToLowerInvariant()
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
            # a sub-window dialog: rect and widgets +(dx,dy); `pictures intrface/popp` -> the console plates in INTRF_HD,
            # then the console layout (list window, scroll channel, framed text boxes; doc 10.53)
            Write-Latin1 (Join-Path $hd $name) (Edit-DialogConsole (Set-BackgroundHd (Edit-PaddedScript $text $dx0 $dy0 @(($x + $dx0), ($y + $dy0), [int]$m4.Groups[5].Value, [int]$m4.Groups[6].Value)) $Console $token)); $written++
            continue
        }
        if ($m4.Success -or -not $m2.Success -or -not $bg.Success) { continue }
        if ($lname -eq 'maine') {
            Write-Latin1 (Join-Path $hd $name) (Set-BackgroundHd (Edit-HudScript $text $W $H $Console) $Console $token); $written++
            continue
        }
        $gif = Find-CI $intrface ($bg.Groups[1].Value + '.GIF')
        if (-not $gif) { continue }
        if ($introScreens -contains $lname) {
            Write-Latin1 (Join-Path $hd $name) (Set-BackgroundHd (Edit-IntroScript $text $W $H) $true $token); $written++
        } else {
            $gs = [DcGif]::Size([System.IO.File]::ReadAllBytes($gif))
            Write-Latin1 (Join-Path $hd $name) (Set-BackgroundHd (Edit-PaddedScript $text ([int][Math]::Floor(($W - $gs[0]) / 2)) ([int][Math]::Floor(($H - $gs[1]) / 2)) @(0, 0, $W, $H)) $true $token); $written++
        }
        $gifsToPad[[System.IO.Path]::GetFileName($gif).ToUpperInvariant()] = $gif
    }
    # --- backgrounds: the painted / spliced ones ship per size, the rest are letterboxed here - laid
    # over BACKDROP.GIF (the main menu's planet without its bottom band) in a grey panel frame (doc 10.56)
    foreach ($shipped in 'INTRG.GIF', 'INTRO.GIF', 'BACKDROP.GIF') {
        $gifsToPad.Remove($shipped)
        [System.IO.File]::Copy((Find-CI $src $shipped), (Join-Path $hd $shipped), $true); $written++
    }
    # the HUD frame of the chosen theme becomes <folder>\INTRFACE.GIF (the name the HUD script reads)
    $gifsToPad.Remove('INTRFACE.GIF'); $gifsToPad.Remove('INTRFACE_LIGHT.GIF')
    [System.IO.File]::Copy((Find-CI $src $frame), (Join-Path $hd 'INTRFACE.GIF'), $true); $written++
    $backdrop = [System.IO.File]::ReadAllBytes((Find-CI $src 'BACKDROP.GIF'))
    foreach ($k in @($gifsToPad.Keys | Sort-Object)) {
        $bytes = [DcGif]::Pad([System.IO.File]::ReadAllBytes($gifsToPad[$k]), $W, $H, $backdrop)
        [System.IO.File]::WriteAllBytes((Join-Path $hd ([System.IO.Path]::GetFileName($gifsToPad[$k]))), $bytes); $written++
    }
    # --- briefing lists.  HSCENE/GSCENE name the campaign endings: the Classic movies live beside the Council
    # Wars ones as DCHENDING/DCAENDING.AVI (the Dark Colony disc's HENDING/AENDING renamed), and the DARK COLONY
    # mode of Dark Colony Ultimate reads these HD lists, so they name the DC files when those are in the folder
    # (until 5 Oct 2026 also when the Classic build's `movies` fix was applied - that build is gone)
    $avi = Join-Path $GameDir 'AVI'
    $dcMovies = [bool] ((Find-CI $avi 'DCHENDING.AVI') -and (Find-CI $avi 'DCAENDING.AVI'))
    foreach ($ln in 'HSCENE.TXT', 'GSCENE.TXT', 'HTSCENE.TXT', 'GTSCENE.TXT') {
        $p = Find-CI $gamestat $ln
        if (-not $p) { continue }
        $t = Edit-SceneList (Read-Latin1 $p) $dx0 $dy0
        if ($dcMovies) {
            if ($ln -eq 'HSCENE.TXT') { $t = $t.Replace('avi/hending.avi', 'avi/dchending.avi') }
            if ($ln -eq 'GSCENE.TXT') { $t = $t.Replace('avi/aending.avi', 'avi/dcaending.avi') }
        }
        Write-Latin1 (Join-Path $hd ([System.IO.Path]::GetFileName($p))) $t; $written++
    }
    # --- loading screens
    $lines += Write-LoadingScreens $GameDir $Mode
    # --- Council Wars: exp\intrface overrides -> exp\<folder>, and the OZI overlay's copies
    $expI = Join-Path $GameDir 'exp\intrface'; $expG = Join-Path $GameDir 'exp\gamestat'; $expHd = Join-Path $GameDir ('exp\' + $folder)
    if ((Test-Path -LiteralPath $expI) -and -not (Test-Path -LiteralPath $expHd)) { [void] [System.IO.Directory]::CreateDirectory($expHd) }
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
                $t = Set-BackgroundHd (Edit-PaddedScript $text ([int][Math]::Floor(($W - $gs[0]) / 2)) ([int][Math]::Floor(($H - $gs[1]) / 2)) @(0, 0, $W, $H)) $true $token
            } else {
                $t = Set-BackgroundHd (Edit-IntroScript $text $W $H (Get-MenuLift $H)) $true $token   # the Council Wars cluster sits higher
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
        $dcHd = Join-Path $GameDir ('dc\' + $folder)
        if (-not (Test-Path -LiteralPath $dcHd)) { New-Item -ItemType Directory -Path $dcHd -Force | Out-Null }
        [System.IO.File]::Copy($dcSrc, (Join-Path $dcHd 'bintroe'), $true); $dcWritten++
    }
    $oziWritten = 0
    $ozi = Join-Path $GameDir 'ozi_ns'; $oziHd = Join-Path $ozi $folder; $oziG = Join-Path $ozi 'gamestat'
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
    $lines += ('interface set for {0} ({6} battlefield interface) written into its own folder: {7}\ {1} files{2}{3}{4} ({5:N1} s, GIFs re-encoded by the compiled DcGif codec)' -f $Mode, $written,
               $(if ($expWritten) { ", exp\$folder\ $expWritten" } else { '' }), $(if ($dcWritten) { ", dc\$folder\ $dcWritten" } else { '' }),
               $(if ($oziWritten) { ", ozi_ns\$folder\ $oziWritten" } else { '' }), $sw.Elapsed.TotalSeconds, $(if ($Console) { 'dark' } else { 'light' }), $folder)
    return $lines
}

# --- 640x480 companion of fix ozi: the exe is pointed at a copy the original exe never reads ---------
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
# ozi: the OZI MISSIONS save folder.  stub_pack points the two save-folder slots at `ozisave`, and the game writes a save
# as ozisave\<name>.dcg without creating the folder - so the folder must exist before the first OZI save.  The marker
# file ozisave.txt (the same line the repository's game folder carries) is written from scratch here; it is not a
# resource of this script ("patcher must not carry ozisave" - maintainer, 5 Oct 2026).  Nothing is overwritten.
function Write-OziSaveFolder([string] $GameDir) {
    $lines = @()
    $d = Join-Path $GameDir 'ozisave'
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null; $lines += 'created ozisave\ (the OZI MISSIONS save folder)' }
    if (-not (Find-CI $d 'ozisave.txt')) {
        Write-Latin1 (Join-Path $d 'ozisave.txt') "Save games of the OZI MISSIONS campaign mode (ozi_ns mission pack).`r`n"
        $lines += 'wrote ozisave\ozisave.txt (the save folder''s marker, created from scratch)'
    }
    return $lines
}

# The battlefield options dialog with the MUSIC row (Dark Colony Ultimate, fix `music`, doc 10.41): the port of
# patch_music.music_row(), byte-identical.  The GAME DETAIL row (pushb 44/45, in_text 48, label 63, cell picture
# 19), the bottom frame cell (picture 15) and the OK / cancel buttons (55/56) move down one row (32 px); the new
# row takes GAME DETAIL's old place with pushb 71 "-" / 72 "+", in_text 73, label 74 (textmsg 6 MUSIC) and cell
# picture 22; two middle frame cells (pictures 20/21) fill the gap; textmsg 20/21/22 = DC / CW / ALL; the erase
# rect grows by a row.  Idempotent; each line keeps its own line ending.
function Edit-MusicDialog([string] $Text) {
    if ([regex]::IsMatch($Text, '(?m)^\s*pushb\s+71\s')) { return $Text }
    $move = @('pushb 44', 'pushb 45', 'in_text 48', 'label 63', 'picture 19', 'picture 15', 'pushb 55', 'pushb 56')
    $clone = @{ 'pushb 44' = @{ 2 = '71' }; 'pushb 45' = @{ 2 = '72' }; 'in_text 48' = @{ 2 = '73' }
                'label 63' = @{ 2 = '74'; 9 = '6' }; 'picture 19' = @{ 2 = '22' } }
    $out = New-Object System.Collections.Generic.List[string]
    $frame14 = $null
    foreach ($raw in $Text.Split("`n")) {
        $cr = if ($raw.EndsWith("`r")) { "`r" } else { '' }
        $line = if ($cr) { $raw.Substring(0, $raw.Length - 1) } else { $raw }
        $m = [regex]::Match($line, '^\s*(pushb|in_text|label|picture|textmsg|size)\s+(\d+)\s')
        if (-not $m.Success) { $out.Add($line + $cr); continue }
        $kind = $m.Groups[1].Value; $n = [int] $m.Groups[2].Value; $key = "$kind $n"
        if ($kind -eq 'size') {
            $toks = @([regex]::Matches($line, '\S+') | ForEach-Object { $_.Value })
            $ch = @{}; $ch[$toks.Count] = [string] ([int] $toks[$toks.Count - 1] + 32)
            $out.Add((Set-ScriptTokens $line $ch) + $cr); continue
        }
        if ($kind -eq 'picture' -and $n -eq 14) { $frame14 = $line }
        if ($clone.ContainsKey($key)) { $out.Add((Set-ScriptTokens $line $clone[$key]) + $cr) }
        if ($move -contains $key) {
            $y = [int] @([regex]::Matches($line, '\S+') | ForEach-Object { $_.Value })[4]
            $line = Set-ScriptTokens $line @{ 5 = [string] ($y + 32) }
        }
        if ($kind -eq 'picture' -and $n -eq 15 -and $null -ne $frame14) {
            $y14 = [int] @([regex]::Matches($frame14, '\S+') | ForEach-Object { $_.Value })[4]
            $out.Add((Set-ScriptTokens $frame14 @{ 2 = '20'; 5 = [string] ($y14 + 16) }) + $cr)
            $out.Add((Set-ScriptTokens $frame14 @{ 2 = '21'; 5 = [string] ($y14 + 32) }) + $cr)
        }
        $out.Add($line + $cr)
        if ($kind -eq 'textmsg' -and $n -eq 12) {
            foreach ($pair in @(@(6, 'MUSIC'), @(20, 'DC'), @(21, 'CW'), @(22, 'ALL'))) { $out.Add(('textmsg {0} {1}' -f $pair[0], $pair[1]) + $cr) }
        }
    }
    return ($out -join "`n")
}

# hud_console.console_dialog (doc 10.53 / 10.54, DC16_INTERFACE_STYLE_GUIDE.md §6): a battlefield dialog script on the
# console plates of INTRF_HD\POPP.SPR.  Only scripts naming intrf_hd/popp are touched.  Position-derived and idempotent,
# so the tool chain and this script produce the same text in any order.  Every dialog is a form: rows 0..2 are the blank
# cells 25 / 24 under the header box (cell 19, 292x44 at row + 6, y0 + 4) with the title as a font-1 (MFONTO2) label
# inside it (284x28 at + 4 / + 13), the red title / label plates (cell 6) are dropped, the buttons are the lobby's text
# buttons (cell 20 = 90x26, cell 26 = 180x26, `label centre N 2` with font 2 = MFONTO5), font 0 becomes MFONTO5 for the
# options form only, bright_pushed 8 / bright_highlight 4.
# Rows 3..last of EVERY dialog are one framed black panel (cells 16 / 17 / 18; doc 10.55).  List dialogs: the list rows
# are compartments of that panel (cell 3 when the list starts at the panel's top, 27 for a list top inside the panel,
# 4 middle, 5 bottom), the `list` sits in the rows' list window (x row + 10, y top row + 4, to the bottom row + 11), UP /
# DOWN (pushb cells 10 / 11 bound to the list) in the scroll channel at x row + 277 (top row + 5 / bottom row - 5) with
# the `scroll` bar between them (x row + 280, 10 px); an in_text (the save name) gets a name box picture (cell 15,
# 280x24) inside the panel at (row + 12, top row + 6), the in_text at (row + 20, top row + 12); OK (id 56) at row + 158
# (a lone OK at + 107) and CANCEL (id 55) at row + 56 as 90x26 text buttons on their own y.  The quit dialog (pushb 57):
# two 180x26 buttons at row + 62 with textmsg 2 / 3, the two label widgets dropped.  The options dialog ("-" / "+"
# pairs): every option a capsule strip picture (cell 23, 282x24 at
# row + 11, y0 + 58 + 32 k) with the label at row + 27 (116 px), the in_text at row + 188 and KNOBE's arrows (cells
# 21 / 22) at row + 161 / + 265 on y0 + 63 + 32 k, CANCEL / OK under the last option.  Box pictures are regenerated on
# every pass, numbered with the lowest free widget ids from 23 in y order, one block after the last picture line; the
# OK / CANCEL texts (textmsg 7 / 8) are re-placed right after the first textmsg line.
$DIALOG_WIDGETS = @('pushb', 'checkb', 'in_text', 'picture', 'list', 'scroll', 'gadget', 'label', 'count', 'scount', 'group')
$DIGITS = [regex] '^\d+$'
function Get-PushbCell([string[]] $t) {             # `-16 8` = plate 8, `10 -10` = UP arrow 10, `-11 21` = plate 21
    for ($k = 7; $k -le 8 -and $k -lt $t.Count; $k++) { if ($DIGITS.IsMatch($t[$k])) { return [int]$t[$k] } }
    return -1
}
function Get-TextButton([int] $n, [int] $x, [int] $y, [int] $w, [int] $cell, [int] $msg) {
    return ('pushb    {0}  0  {1}  {2}   {3}  26  -11 {4}  label centre {5} 2  -  remap 0' -f $n, $x, $y, $w, $cell, $msg)
}
function Edit-DialogConsole([string] $Text) {
    if (-not [regex]::IsMatch($Text, '(?im)^[ \t]*pictures[ \t]+(?:intrf_hd|hd_src)/popp\b')) { return $Text }
    $lines = $Text.Split("`n")
    $rec = @{}
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $body = $lines[$i]; $p = $body.IndexOf('%'); if ($p -ge 0) { $body = $body.Substring(0, $p) }
        $t = @([regex]::Matches($body, '\S+') | ForEach-Object { $_.Value })
        if ($t.Count -ge 5 -and $DIALOG_WIDGETS -contains $t[0].ToLowerInvariant() -and $DIGITS.IsMatch($t[1]) -and $DIGITS.IsMatch($t[3]) -and $DIGITS.IsMatch($t[4])) {
            $rec[$i] = @{ kind = $t[0].ToLowerInvariant(); id = [int]$t[1]; t = $t }
        }
    }
    $keys = @($rec.Keys | Sort-Object)
    $rows = @()
    foreach ($i in $keys) {
        $r = $rec[$i]
        if ($r.kind -eq 'picture' -and $r.t.Count -ge 8 -and $DIGITS.IsMatch($r.t[7]) -and ((0, 1, 2, 3, 4, 5, 16, 17, 18, 24, 25, 27) -contains [int]$r.t[7])) {
            $rows += @{ line = $i; y = [int]$r.t[4]; x = [int]$r.t[3]; cell = [int]$r.t[7] }
        }
    }
    if ($rows.Count -eq 0) { return $Text }
    $rowX = ($rows | ForEach-Object { $_.x } | Measure-Object -Minimum).Minimum
    $changes = @{}; $rebuilt = @{}; $drop = @{}; $boxes = @()
    $minus = @()
    $quitForm = $false; $hasCancel = $false
    foreach ($i in $keys) {
        $r = $rec[$i]
        if ($r.kind -eq 'pushb') {
            if ((12, 21) -contains (Get-PushbCell $r.t)) { $minus += , @([int]$r.t[3], [int]$r.t[4]) }
            if ($r.id -eq 57) { $quitForm = $true }
            if ($r.id -eq 55) { $hasCancel = $true }
        }
    }
    # lists with their scroll channel
    foreach ($i in $keys) {
        $r = $rec[$i]; if ($r.kind -ne 'list') { continue }
        $y0 = [int]$r.t[4]; $h = [int]$r.t[6]
        $lr = @($rows | Where-Object { (3, 4, 5, 27) -contains $_.cell -and $_.y -gt ($y0 - 16) -and $_.y -lt ($y0 + $h) } | ForEach-Object { $_.y })
        if ($lr.Count -eq 0) { continue }
        $top = ($lr | Measure-Object -Minimum).Minimum; $bottom = ($lr | Measure-Object -Maximum).Maximum
        $ly = $top + 4
        $changes[$i] = @{ 4 = [string]($rowX + 10); 5 = [string]$ly; 7 = [string]($bottom + 12 - $ly) }
        foreach ($j in $keys) {
            $q = $rec[$j]; $qy = [int]$q.t[4]
            if ($q.kind -eq 'scroll' -and $qy -ge $top -and $qy -le ($bottom + 16)) {
                $changes[$j] = @{ 4 = [string]($rowX + 280); 5 = [string]($top + 21); 6 = '10'; 7 = [string]($bottom - $top - 26) }
            } elseif ($q.kind -eq 'pushb' -and $q.t.Count -gt 8 -and (@($q.t[8..($q.t.Count - 1)]) -contains 'list') -and ((10, 11) -contains (Get-PushbCell $q.t)) -and $qy -ge ($top - 16) -and $qy -le ($bottom + 16)) {
                if ((Get-PushbCell $q.t) -eq 10) { $ay = $top + 5 } else { $ay = $bottom - 5 }
                $changes[$j] = @{ 4 = [string]($rowX + 277); 5 = [string]$ay }
            }
        }
    }
    # the form frame every dialog shares: blank rows under the header box, the title inside it, no red plates
    $ordered = @($rows | Sort-Object -Property @{ Expression = { $_.y } })
    $y0 = $ordered[0].y
    for ($k = 0; $k -lt 3 -and $k -lt $ordered.Count; $k++) {
        $rw = $ordered[$k]
        if (-not $changes.ContainsKey($rw.line)) { $changes[$rw.line] = @{} }
        if ($k -eq 0) { $changes[$rw.line][8] = '25' } else { $changes[$rw.line][8] = '24' }
    }
    $boxes += @{ y = ($y0 + 4); x = ($rowX + 6); cell = 19 }
    $ms = @($minus | Sort-Object -Property @{ Expression = { $_[1] } })
    $optionOf = { param($ty) for ($k = 0; $k -lt $ms.Count; $k++) { if ([Math]::Abs($ms[$k][1] - $ty) -le 8) { return $k } }; return -1 }
    foreach ($i in $keys) {
        $r = $rec[$i]
        if ($r.kind -eq 'picture' -and $r.t.Count -ge 8 -and $r.t[7] -eq '6') { $drop[$i] = $true }
        elseif ($r.kind -eq 'label' -and ($r.t -contains 'centre') -and ((& $optionOf ([int]$r.t[4])) -lt 0)) {
            $changes[$i] = @{ 4 = [string]($rowX + 10); 5 = [string]($y0 + 17); 6 = '284'; 7 = '28'; 13 = '1' }
        }
    }
    if ($minus.Count -gt 0) {
        # the options form
        for ($k = 3; $k -lt $ordered.Count; $k++) {
            $rw = $ordered[$k]
            if ($k -eq 3) { $cell = 16 } elseif ($k -eq $ordered.Count - 1) { $cell = 18 } else { $cell = 17 }
            if (-not $changes.ContainsKey($rw.line)) { $changes[$rw.line] = @{} }
            $changes[$rw.line][8] = [string]$cell
        }
        for ($k = 0; $k -lt $ms.Count; $k++) { $boxes += @{ y = ($y0 + 58 + 32 * $k); x = ($rowX + 11); cell = 23 } }
        foreach ($i in $keys) {
            $r = $rec[$i]; $ty = [int]$r.t[4]
            $o = & $optionOf $ty
            $pc = Get-PushbCell $r.t
            if ($r.kind -eq 'pushb' -and ((12, 21, 13, 22) -contains $pc)) {
                if ($o -lt 0) { continue }
                if ((13, 22) -contains $pc) { $px = $rowX + 265; $pcell = '22' } else { $px = $rowX + 161; $pcell = '21' }
                $changes[$i] = @{ 4 = [string]$px; 5 = [string]($y0 + 63 + 32 * $o); 8 = '-11'; 9 = $pcell }
            } elseif ($r.kind -eq 'pushb' -and ((55, 56) -contains $r.id)) {
                $by = $y0 + 63 + 32 * $ms.Count
                if ($r.id -eq 56) { $rebuilt[$i] = Get-TextButton 56 ($rowX + 158) $by 90 20 7 } else { $rebuilt[$i] = Get-TextButton 55 ($rowX + 56) $by 90 20 8 }
            } elseif ($r.kind -eq 'in_text') {
                if ($o -ge 0) { $changes[$i] = @{ 4 = [string]($rowX + 188); 5 = [string]($y0 + 63 + 32 * $o + 1) } }
            } elseif ($r.kind -eq 'label') {
                if ($o -ge 0) { $changes[$i] = @{ 4 = [string]($rowX + 27); 5 = [string]($y0 + 63 + 32 * $o + 1); 6 = '116'; 7 = '14' } }
            }
        }
    } else {
        # the save / objectives / quit dialogs: rows 3..last the same black panel, the list rows its compartments
        # (list rows = list-cell rows inside a `list` widget's y range: the stock save dialog frames its name
        # field with two list cells that are panel rows here)
        $listY = @()
        foreach ($i in $keys) {
            $r = $rec[$i]; if ($r.kind -ne 'list') { continue }
            $ly0 = [int]$r.t[4]; $lh = [int]$r.t[6]
            $listY += @($rows | Where-Object { (3, 4, 5, 27) -contains $_.cell -and $_.y -gt ($ly0 - 16) -and $_.y -lt ($ly0 + $lh) } | ForEach-Object { $_.y })
        }
        $lTop = -1; $lBottom = -1
        if ($listY.Count -gt 0) { $lTop = ($listY | Measure-Object -Minimum).Minimum; $lBottom = ($listY | Measure-Object -Maximum).Maximum }
        for ($k = 3; $k -lt $ordered.Count; $k++) {
            $rw = $ordered[$k]
            if ($listY -contains $rw.y) {
                if ($rw.y -eq $lTop) { if ($k -eq 3) { $cell = 3 } else { $cell = 27 } } elseif ($rw.y -eq $lBottom) { $cell = 5 } else { $cell = 4 }
            } else {
                if ($k -eq 3) { $cell = 16 } elseif ($k -eq $ordered.Count - 1) { $cell = 18 } else { $cell = 17 }
            }
            if (-not $changes.ContainsKey($rw.line)) { $changes[$rw.line] = @{} }
            $changes[$rw.line][8] = [string]$cell
        }
        foreach ($i in $keys) {
            $r = $rec[$i]
            if ($r.kind -eq 'in_text') {
                $ty = [int]$r.t[4]
                $top = ($rows | Where-Object { $_.y -le $ty } | ForEach-Object { $_.y } | Measure-Object -Maximum).Maximum
                $boxes += @{ y = ($top + 6); x = ($rowX + 12); cell = 15 }
                $changes[$i] = @{ 4 = [string]($rowX + 20); 5 = [string]($top + 12) }
            } elseif ($r.kind -eq 'pushb' -and ((55, 56, 57) -contains $r.id) -and -not ($r.t.Count -gt 8 -and (@($r.t[8..($r.t.Count - 1)]) -contains 'list'))) {
                $by = [int]$r.t[4]
                if ($quitForm) {
                    # the pair centred in the panel's interior (row 3 + 4 .. last row + 3), YES above NO, 22 px apart
                    $innerY0 = $ordered[3].y + 4; $innerH = $ordered[$ordered.Count - 1].y - $ordered[3].y
                    $by = $innerY0 + [int][Math]::Floor(($innerH - 74) / 2)
                    if ($r.id -eq 56) { $msg = 2 } else { $msg = 3; $by += 48 }
                    $rebuilt[$i] = Get-TextButton $r.id ($rowX + 62) $by 180 26 $msg
                } else {
                    # centred between the list block's bottom divider (bottom row + 16) and the panel's bottom tube (last row + 4)
                    if ($lBottom -ge 0) { $by = $lBottom + 16 + [int][Math]::Floor(($ordered[$ordered.Count - 1].y + 4 - ($lBottom + 16) - 26) / 2) }
                    if ($r.id -eq 56) {
                        if ($hasCancel) { $ox = $rowX + 158 } else { $ox = $rowX + 107 }
                        $rebuilt[$i] = Get-TextButton 56 $ox $by 90 20 7
                    } else {
                        $rebuilt[$i] = Get-TextButton 55 ($rowX + 56) $by 90 20 8
                    }
                }
            } elseif ($quitForm -and $r.kind -eq 'label' -and -not ($r.t -contains 'centre')) {
                $drop[$i] = $true
            }
        }
    }
    # apply the changes, drop the old box pictures and the red plates, insert the new boxes after the last picture line
    $out = New-Object System.Collections.Generic.List[string]
    $lastPicture = -1; $used = @{}
    $bottom = $ordered[$ordered.Count - 1].y + 16     # the `size` rect must reach the last row: the engine draws nothing below it
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $raw = $lines[$i]
        $ms = [regex]::Match($raw, '^([ \t]*size[ \t]+\d+[ \t]+)(\d+)([ \t]+\d+[ \t]+)(\d+)(?=\s|$)')
        if ($ms.Success -and [int]$ms.Groups[4].Value -lt ($bottom - [int]$ms.Groups[2].Value)) {   # (the stock objectives dialog says 272 for its 18 rows)
            $raw = $ms.Groups[1].Value + $ms.Groups[2].Value + $ms.Groups[3].Value + [string]($bottom - [int]$ms.Groups[2].Value) + $raw.Substring($ms.Length)
        }
        if ($rec.ContainsKey($i)) {
            $r = $rec[$i]
            if ($drop.ContainsKey($i) -or ($r.kind -eq 'picture' -and $r.t.Count -ge 8 -and $DIGITS.IsMatch($r.t[7]) -and ((14, 15, 19, 23) -contains [int]$r.t[7]))) { continue }
            $used[$r.id] = $true
            if ($rebuilt.ContainsKey($i) -or $changes.ContainsKey($i)) {
                $p = $raw.IndexOf('%')
                if ($p -ge 0) { $body = $raw.Substring(0, $p); $tail = $raw.Substring($p) } else { $body = $raw; $tail = '' }
                $cr = ''; if ($body.EndsWith("`r")) { $cr = "`r"; $body = $body.Substring(0, $body.Length - 1) }
                if ($rebuilt.ContainsKey($i)) { $body = [regex]::Match($body, '^\s*').Value + $rebuilt[$i] } else { $body = Set-ScriptTokens $body $changes[$i] }
                $raw = $body + $cr + $tail
            }
            if ($r.kind -eq 'picture') { $lastPicture = $out.Count }
        }
        $out.Add($raw)
    }
    $cr = ''; if ($out[$lastPicture].EndsWith("`r")) { $cr = "`r" }
    $next = 23; $new = @()
    foreach ($b in ($boxes | Sort-Object -Property @{ Expression = { $_.y } }, @{ Expression = { $_.x } })) {
        while ($used.ContainsKey($next)) { $next++ }
        $w = 78; $hh = 24
        if ($b.cell -eq 15) { $w = 280 } elseif ($b.cell -eq 19) { $w = 292; $hh = 44 } elseif ($b.cell -eq 23) { $w = 282 }
        $new += (('picture  {0}  0  {1}   {2}  {3}  {4}   {5}' -f $next, $b.x, $b.y, $w, $hh, $b.cell) + $cr)
        $next++
    }
    if ($new.Count -gt 0) { $out.InsertRange($lastPicture + 1, [string[]] $new) }
    # the form's header lines: font 0 MFONTO5, font 1 MFONTO2, the hover brightness, OK / CANCEL texts after the first textmsg
    $kept = New-Object System.Collections.Generic.List[string]
    foreach ($line in $out) { if (-not [regex]::IsMatch($line, '^\s*(textmsg\s+(7|8)|font\s+[12]|font_offset\s+[12]|bright_pushed|bright_highlight)\s')) { $kept.Add($line) } }
    $text = ($kept -join "`n")
    $firstMsg = -1
    for ($i = 0; $i -lt $kept.Count; $i++) { if ([regex]::IsMatch($kept[$i], '^\s*textmsg\s+\d+\s')) { $firstMsg = $i; break } }
    $res = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt $kept.Count; $i++) {
        $line = $kept[$i]
        $body = $line; $lcr = ''; if ($body.EndsWith("`r")) { $lcr = "`r"; $body = $body.Substring(0, $body.Length - 1) }
        if ($minus.Count -gt 0) { $body = [regex]::Replace($body, '(?i)^(\s*font\s+0\s+)intrface/mfonto7\b', '${1}intrface/mfonto5') }
        $res.Add($body + $lcr)
        if ([regex]::IsMatch($body, '^\s*font_offset\s+0\s')) {
            $res.Add('font 1 intrface/mfonto2' + $lcr); $res.Add('font_offset  1 31' + $lcr)
            $res.Add('font 2 intrface/mfonto5' + $lcr); $res.Add('font_offset  2 31' + $lcr)
        }
        if ([regex]::IsMatch($body, '^\s*colour\s+selbg\s')) {
            $res.Add('bright_pushed    8' + $lcr); $res.Add('bright_highlight 4' + $lcr)
        }
        if (-not $quitForm -and $i -eq $firstMsg) {
            $res.Add('textmsg 7 OK' + $lcr); $res.Add('textmsg 8 CANCEL' + $lcr)
        }
    }
    return ($res -join "`n")
}

# The copies of that dialog the Dark Colony Ultimate exe reads in its three campaign modes: HD sizes
# exp\intrf_hd\lopte, dc\intrf_hd\lopte, ozi_ns\intrf_hd\lopte from INTRF_HD\LOPTE (the set just written);
# 640x480 exp\intrface\lopme, dc\intrface\lopme, ozi_ns\intrface\lopme from the stock INTRFACE\LOPTE (the exe's
# script name is "intrface/lopm" there, so the original exe's exp\intrface\lopte is never touched).  dc\ is the
# DARK COLONY mode's overlay and is created if needed; ozi_ns\ only when the OZI data is there.
function Write-MusicDialogs([string] $GameDir, [string] $Mode) {
    $stock = ($Mode -eq '640x480')
    $src = if ($stock) { Find-CI (Join-Path $GameDir 'INTRFACE') 'LOPTE' } else { Find-CI (Join-Path $GameDir (Get-HdFolder $Mode)) 'LOPTE' }
    if (-not $src) { return @('options dialog copies NOT written: LOPTE is missing') }
    $t = Edit-DialogConsole (Edit-MusicDialog (Read-Latin1 $src))
    $name = if ($stock) { 'lopme' } else { 'lopte' }
    $sub = if ($stock) { 'intrface' } else { Get-HdFolder $Mode }
    $lines = @()
    foreach ($root in 'exp', 'dc', 'ozi_ns') {
        if ($root -eq 'ozi_ns' -and -not (Test-Path -LiteralPath (Join-Path $GameDir $root))) { continue }
        $d = Join-Path $GameDir (Join-Path $root $sub)
        if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
        Write-Latin1 (Join-Path $d $name) $t
        $lines += ('wrote {0}\{1}\{2} (the battlefield options dialog with the MUSIC row)' -f $root, $sub, $name)
    }
    return $lines
}

# The ONLINE WAR room screen (fix online, Dark Colony Ultimate): LOADGE -> ONLINE, the same edits as
# patch_online.online_script (byte-identical output): the list widened from 392 to 448 px (56 columns of
# MFONTO5, 8 px each), scroll bar / UP / DOWN and their plates 56 px further right, a read-only header
# line above the list and a status line below it (in_text 30 / 17), the title "Online War", the buttons
# ENTER / BACK, the save-mode widgets (label 18, pushb 21, the groups, textmsg 4 / 5) and the scope
# animations (gadgets 11 and 14, which repainted over the header and the status line) removed, the
# background line pointing at ONLINEBG.GIF (LOADER.GIF with a grey frame around the text lines); a name line (in_text 49, "Name: <name= of DEFAULT_SERVER.TXT>", 3 Oct 2026) and a server line (in_text 31, "Server: host:port") above the status line and the title label centred in its panel (list x - 50, 318 wide).
# Idempotent on its own output.
function Edit-OnlineScript([string] $Text) {
    $m = [regex]::Match($Text, '(?m)^\s*list\s+0\s+\d+\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s')
    if (-not $m.Success) { throw 'LOADGE: no list 0 line' }
    $lx = [int]$m.Groups[1].Value; $ly = [int]$m.Groups[2].Value; $lw = [int]$m.Groups[3].Value; $lh = [int]$m.Groups[4].Value
    $already = ($lw -eq 448)
    if ($lw -ne 392 -and $lw -ne 448) { throw ('LOADGE: list width {0}, expected 392' -f $lw) }
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($raw in $Text.Split("`n")) {
        $cr = if ($raw.EndsWith("`r")) { "`r" } else { '' }
        $line = if ($cr) { $raw.Substring(0, $raw.Length - 1) } else { $raw }
        $w = [regex]::Match($line, '^\s*(\w+)\s+(\d+)\s')
        $bgm = [regex]::Match($line, '^\s*background\s+\S+/\S+\s*$')
        if ($bgm.Success) { $out.Add(([regex]::Replace($line, '(\S+/)\S+\s*$', '${1}onlinebg')) + $cr); continue }
        if ($w.Success) {
            $kind = $w.Groups[1].Value; $id = [int]$w.Groups[2].Value
            $drop = ($kind -eq 'label' -and $id -eq 18) -or ($kind -eq 'pushb' -and $id -eq 21) -or ($kind -eq 'group') -or
                    ($kind -eq 'textmsg' -and ($id -eq 4 -or $id -eq 5)) -or ($kind -eq 'in_text' -and ($id -eq 30 -or $id -eq 31 -or $id -eq 49)) -or ($kind -eq 'gadget' -and ($id -eq 11 -or $id -eq 14))
            if ($drop) { continue }
            if ($kind -eq 'list' -and $id -eq 0) { $line = Set-ScriptTokens $line @{ 6 = '448' } }
            elseif ((-not $already) -and (($kind -eq 'scroll' -and $id -eq 1) -or ($kind -eq 'pushb' -and ($id -eq 2 -or $id -eq 3)) -or ($kind -eq 'gadget' -and ($id -eq 7 -or $id -eq 8)))) {
                $x = [int]([regex]::Match($line, '^\s*\w+\s+\d+\s+\d+\s+(\d+)').Groups[1].Value)
                $line = Set-ScriptTokens $line @{ 4 = [string]($x + 56) }
            }
            elseif ($kind -eq 'in_text' -and $id -eq 17) {
                $out.Add(('in_text  49  0  {0}  {1}   56    1  0  -  read_only' -f $lx, ($ly + $lh + 14)) + $cr)
                $out.Add(('in_text  31  0  {0}  {1}   56    1  0  -  read_only' -f $lx, ($ly + $lh + 30)) + $cr)
                $out.Add(('in_text  17  0  {0}  {1}   56    1  0  -  read_only' -f $lx, ($ly + $lh + 46)) + $cr)
                $out.Add(('in_text  30  0  {0}  {1}   56    1  0  -  read_only' -f $lx, ($ly - 16)) + $cr)
                continue
            }
            elseif ($kind -eq 'label' -and $id -eq 6) { $line = Set-ScriptTokens $line @{ 4 = [string]($lx - 50); 6 = '318' } }
            elseif ($kind -eq 'textmsg' -and $id -eq 1) { $line = Get-TextmsgLine 1 'Online War' }
            elseif ($kind -eq 'textmsg' -and $id -eq 2) { $line = Get-TextmsgLine 2 'ENTER' }
        }
        $out.Add($line + $cr)
    }
    return ($out -join "`n")
}

# The REPLAY ONLINE GAME screen (2 Oct 2026, doc 10.65): ONLINE -> REPLAYE, the same edits as patch_online.replay_script
# (byte-identical output): the list narrowed to 40 columns (320 px), scroll bar / UP / DOWN and their plates 72 px LEFT
# of their stock LOADGE places, the header line 40 columns, the participant pane after it - eight `checkb` rows (cells
# 149 off = the empty box / 8 on = the green cross of HD_SRC\KNOBR.SPR, `pictures hd_src/knobr`; slot 0, the lobby host, shows the grey dead box 150 for both states) 30 px apart from list
# top + 6 at list x + 376, a 13-column read-only name right of each box, the heading in_text 48 above them - the title
# "Online Replay" ("Replay Online Game" overran the title panel, 3 Oct 2026), the button REPLAY and the background REPLAYBG.GIF.  Idempotent on its own output.
function Edit-ReplayScript([string] $Text) {
    $m = [regex]::Match($Text, '(?m)^\s*list\s+0\s+\d+\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s')
    if (-not $m.Success) { throw 'ONLINE: no list 0 line' }
    $lx = [int]$m.Groups[1].Value; $ly = [int]$m.Groups[2].Value; $lw = [int]$m.Groups[3].Value
    if ($lw -ne 448 -and $lw -ne 320) { throw ('ONLINE: list width {0}, expected 448' -f $lw) }
    $already = ($lw -eq 320)
    $pane = New-Object System.Collections.Generic.List[string]
    for ($k = 0; $k -lt 8; $k++) { $cells = if ($k -eq 0) { '150   150' } else { '149   8' }; $pane.Add(('checkb   {0}  0  {1}  {2}   27   17  {3}   -' -f (32 + $k), ($lx + 376), ($ly + 6 + 30 * $k), $cells)) }   # slot 0 = the lobby host: the grey dead box
    for ($k = 0; $k -lt 8; $k++) { $pane.Add(('in_text  {0}  0  {1}  {2}   13    1  0  -  read_only' -f (40 + $k), ($lx + 407), ($ly + 6 + 30 * $k + 3))) }
    $pane.Add(('in_text  48  0  {0}  {1}   17    1  0  -  read_only' -f ($lx + 376), ($ly - 16)))
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($raw in $Text.Split("`n")) {
        $cr = if ($raw.EndsWith("`r")) { "`r" } else { '' }
        $line = if ($cr) { $raw.Substring(0, $raw.Length - 1) } else { $raw }
        $w = [regex]::Match($line, '^\s*(\w+)\s+(\d+)\s')
        $bgm = [regex]::Match($line, '^\s*background\s+\S+/\S+\s*$')
        if ($bgm.Success) { $out.Add(([regex]::Replace($line, '(\S+/)\S+\s*$', '${1}replaybg')) + $cr); continue }
        if ([regex]::IsMatch($line, '^\s*pictures\s+\S+\s*$')) { $out.Add('pictures hd_src/knobr' + $cr); continue }   # the bank with the empty box
        if ($w.Success) {
            $kind = $w.Groups[1].Value; $id = [int]$w.Groups[2].Value
            if (($kind -eq 'checkb' -and $id -ge 32 -and $id -le 39) -or ($kind -eq 'in_text' -and (($id -ge 40 -and $id -le 47) -or $id -eq 48))) { continue }   # re-emitted after the header line
            if ($kind -eq 'list' -and $id -eq 0) { $line = Set-ScriptTokens $line @{ 6 = '320' } }
            elseif ((-not $already) -and (($kind -eq 'scroll' -and $id -eq 1) -or ($kind -eq 'pushb' -and ($id -eq 2 -or $id -eq 3)) -or ($kind -eq 'gadget' -and ($id -eq 7 -or $id -eq 8)))) {
                $x = [int]([regex]::Match($line, '^\s*\w+\s+\d+\s+\d+\s+(\d+)').Groups[1].Value)
                $line = Set-ScriptTokens $line @{ 4 = [string]($x - 56 - 72) }
            }
            elseif ($kind -eq 'in_text' -and $id -eq 30) {
                $out.Add((Set-ScriptTokens $line @{ 6 = '40' }) + $cr)
                foreach ($e in $pane) { $out.Add($e + $cr) }
                continue
            }
            elseif ($kind -eq 'textmsg' -and $id -eq 1) { $line = Get-TextmsgLine 1 'Online Replay' }
            elseif ($kind -eq 'textmsg' -and $id -eq 2) { $line = Get-TextmsgLine 2 'REPLAY' }
        }
        $out.Add($line + $cr)
    }
    return ($out -join "`n")
}

# LOADER.GIF with grey frames drawn into it (patch_online.online_background / draw_frame: a 3-px tube in the palette's
# greys 35 / 106 / 35, a 2-px black gap, black inside, corner pixels off); $Rects = x0, y0, x1, y1 (exclusive).
function Get-FramedBackground($Im, $Rects) {
    $greyIdx = foreach ($g in 35, 106, 35, 0) {
        $best = -1; $bestD = 999
        for ($i = 0; $i -lt 256; $i++) {
            $r = $Im.Palette[3 * $i]
            if ($r -eq $Im.Palette[3 * $i + 1] -and $r -eq $Im.Palette[3 * $i + 2] -and [Math]::Abs([int]$r - $g) -lt $bestD) { $bestD = [Math]::Abs([int]$r - $g); $best = $i }
        }
        $best
    }
    $px = [byte[]] $Im.Pixels.Clone()
    foreach ($r in $Rects) {
        $x0 = $r[0]; $y0 = $r[1]; $x1 = $r[2]; $y1 = $r[3]
        for ($y = $y0; $y -lt $y1; $y++) {
            for ($x = $x0; $x -lt $x1; $x++) {
                if (($x -eq $x0 -or $x -eq ($x1 - 1)) -and ($y -eq $y0 -or $y -eq ($y1 - 1))) { continue }
                $d = [Math]::Min([Math]::Min($x - $x0, $x1 - 1 - $x), [Math]::Min($y - $y0, $y1 - 1 - $y))
                $v = if ($d -lt 3) { $greyIdx[$d] } else { $greyIdx[3] }
                $px[$y * $Im.Width + $x] = [byte]$v
            }
        }
    }
    return [DcGif]::Encode('GIF87a', $Im.Width, $Im.Height, $Im.Palette, $px)
}

# DEFAULT_SERVER.TXT as the repository ships it (the same bytes as patch_online.DEFAULT_SERVER_TEXT; `name=` / `address=` lines since 3 Oct 2026).
$DefaultServerText = (@('/*', ' * DEFAULT_SERVER.TXT - the relay server behind ONLINE WAR and REPLAY ONLINE GAME.', ' *', ' * Dark Colony Ultimate reads this file when you press one of those two buttons in the main', ' * menu. It connects to the address below with TLS encryption on port 8889 (the Dark Colony', ' * Server relay), shows the rooms or the recorded battles the relay offers and joins the one', ' * you pick. Both screens show the name and the address from this file above the connection', ' * state.', ' *', ' * Fields, one per line:', ' *   name=<how the screens call this server>          any text; here the relay''s project page', ' *   address=<host or IP address>[:port]              the port defaults to 8889 (TLS)', ' *   plain                                            optional: no encryption, for a relay on', ' *                                                    your own network without a certificate', ' *                                                    (the plain relay port is 8888)', ' * Comments in the C++ style are ignored: "//" at the start of a line or after a space runs to', ' * the end of the line (so an address like https://... is kept), or a block like this one.', ' * A file holding only an address (the form before 3 Oct 2026) is still understood.', ' *', ' * Keep one server in the file. CUSTOM NET WAR (the in-game host / CONNECT TO SERVER', ' * screens) does not read this file.', ' */', '', 'name=https://github.com/endotermic/Dark-Colony-Server', 'address=dark-colony-server.fly.dev') -join "`r`n") + "`r`n"

# The LOAD GAME screen of every campaign (3 Oct 2026, doc 10.67): ONLINE -> LOADALLE, the same edit as
# patch_online.loadall_script (byte-identical output): the title "Load Game" and the button LOAD; the list, the
# header line, the three text lines and the background ONLINEBG.GIF stay ONLINE's.  Idempotent on its own output.
function Edit-LoadAllScript([string] $Text) {
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($raw in $Text.Split("`n")) {
        $cr = if ($raw.EndsWith("`r")) { "`r" } else { '' }
        $line = if ($cr) { $raw.Substring(0, $raw.Length - 1) } else { $raw }
        $t = [regex]::Match($line, '^\s*textmsg\s+(\d+)\s')
        if ($t.Success -and $t.Groups[1].Value -eq '1') { $line = Get-TextmsgLine 1 'Load Game' }
        elseif ($t.Success -and $t.Groups[1].Value -eq '2') { $line = Get-TextmsgLine 2 'LOAD' }
        $out.Add($line + $cr)
    }
    return ($out -join "`n")
}

function Write-OnlineScreen([string] $GameDir, [string] $Mode) {
    $stock = ($Mode -eq '640x480')
    $sub = if ($stock) { 'INTRFACE' } else { Get-HdFolder $Mode }
    $src = Find-CI (Join-Path $GameDir $sub) 'LOADGE'
    if (-not $src) { return @('ONLINE screen NOT written: LOADGE is missing') }
    $t = Edit-OnlineScript (Read-Latin1 $src)
    $dst = Find-CI (Join-Path $GameDir $sub) 'ONLINE'
    if (-not $dst) { $dst = Join-Path (Join-Path $GameDir $sub) 'ONLINE' }
    Write-Latin1 $dst $t
    $lines = @(('wrote {0}\ONLINE (the ONLINE WAR room screen, derived from LOADGE)' -f $sub))
    # the background: LOADER.GIF with three grey frames - header + list, the scroll bar with its buttons, the name,
    # server and status lines (patch_online.online_background / frame_rects; the text frame B+8 .. B+68 since 3 Oct 2026)
    $loader = Find-CI (Join-Path $GameDir $sub) 'LOADER.GIF'
    if (-not $loader) { throw 'LOADER.GIF is missing' }
    Initialize-GifCodec    # at 640x480 no interface set is built, so the codec may not be compiled yet
    $lm = [regex]::Match($t, '(?m)^\s*list\s+0\s+\d+\s+(\d+)\s+(\d+)\s+\d+\s+(\d+)\s')
    $lx = [int]$lm.Groups[1].Value; $ly = [int]$lm.Groups[2].Value; $lh = [int]$lm.Groups[3].Value
    $im = [DcGif]::Decode([System.IO.File]::ReadAllBytes($loader))
    $b = $ly + $lh; $ux = $lx + 448 + 12
    # every coordinate in its own parentheses: inside @( , ) the comma binds before + and -
    $rects = @(@(($lx - 6), ($ly - 22), ($lx + 448 + 6), ($b + 3)), @(($ux - 4), $ly, ($ux + 26 + 4), ($b + 2)), @(($lx - 6), ($b + 8), ($lx + 448 + 6), ($b + 68)))
    $bgDst = Find-CI (Join-Path $GameDir $sub) 'ONLINEBG.GIF'
    if (-not $bgDst) { $bgDst = Join-Path (Join-Path $GameDir $sub) 'ONLINEBG.GIF' }
    [System.IO.File]::WriteAllBytes($bgDst, (Get-FramedBackground $im $rects))
    $lines += ('wrote {0}\ONLINEBG.GIF (the screen background: LOADER.GIF with grey frames around the list, the scroll bar and the text lines)' -f $sub)
    # REPLAY ONLINE GAME (2 Oct 2026, doc 10.65): the second screen from the ONLINE script, its background with the
    # list frame (40 columns), the scroll bar's frame, the participant pane's frame and the full-width text frame
    $rt = Edit-ReplayScript $t
    $rDst = Find-CI (Join-Path $GameDir $sub) 'REPLAYE'
    if (-not $rDst) { $rDst = Join-Path (Join-Path $GameDir $sub) 'REPLAYE' }
    Write-Latin1 $rDst $rt
    $lines += ('wrote {0}\REPLAYE (the REPLAY ONLINE GAME screen, derived from ONLINE)' -f $sub)
    $rux = $lx + 320 + 12
    $rRects = @(@(($lx - 6), ($ly - 22), ($lx + 320 + 6), ($b + 3)), @(($rux - 4), $ly, ($rux + 26 + 4), ($b + 2)), @(($lx + 368), ($ly - 22), ($lx + 518), ($b + 3)), @(($lx - 6), ($b + 8), ($lx + 518), ($b + 68)))
    $rBgDst = Find-CI (Join-Path $GameDir $sub) 'REPLAYBG.GIF'
    if (-not $rBgDst) { $rBgDst = Join-Path (Join-Path $GameDir $sub) 'REPLAYBG.GIF' }
    [System.IO.File]::WriteAllBytes($rBgDst, (Get-FramedBackground $im $rRects))
    $lines += ('wrote {0}\REPLAYBG.GIF (the replay screen background: frames around the list, the scroll bar, the participant pane and the text lines)' -f $sub)
    # LOAD GAME (3 Oct 2026, doc 10.67): the third screen from the ONLINE script; it shares ONLINEBG.GIF
    $lDst = Find-CI (Join-Path $GameDir $sub) 'LOADALLE'
    if (-not $lDst) { $lDst = Join-Path (Join-Path $GameDir $sub) 'LOADALLE' }
    Write-Latin1 $lDst (Edit-LoadAllScript $t)
    $lines += ('wrote {0}\LOADALLE (the LOAD GAME screen of every campaign, derived from ONLINE)' -f $sub)
    # DEFAULT_SERVER.TXT: written when missing - and a file of the first form (before 3 Oct 2026) that still names only
    # the shipped relay is upgraded to the name= / address= form (patch_online.bare_shipped_config: comments off, exactly
    # one token, dark-colony-server.fly.dev); anything else is the player's own setting and stays
    $cfgPath = Find-CI $GameDir 'DEFAULT_SERVER.TXT'
    $writeCfg = -not $cfgPath
    if ($cfgPath) {
        $ct = Read-Latin1 $cfgPath
        $ct = [regex]::Replace($ct, '(?s)/\*.*?\*/', ' ')
        $ct = [regex]::Replace($ct, '(?m)(^|\s)//.*$', '$1')
        $ctoks = @($ct -split '\s+' | Where-Object { $_ -ne '' })
        if ($ctoks.Count -eq 1 -and $ctoks[0].ToLowerInvariant() -eq 'dark-colony-server.fly.dev') { $writeCfg = $true }
    }
    if ($writeCfg) {
        if (-not $cfgPath) { $cfgPath = Join-Path $GameDir 'DEFAULT_SERVER.TXT' }
        Write-Latin1 $cfgPath $DefaultServerText
        $lines += 'wrote DEFAULT_SERVER.TXT (name= / address= dark-colony-server.fly.dev; a file with your own relay address is never overwritten)'
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
    $name = $Build.ProductName          # "Dark Colony", "Dark Colony Ultimate", "Dark Colony Map Editor"
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
function Invoke-PatchRun([string] $OriginalPath, $Build, [object[]] $Chosen, [string] $OutputPath, [string] $Mode, [string] $Theme, [scriptblock] $Progress) {
    $data = [System.IO.File]::ReadAllBytes($OriginalPath)
    # the patcher's resources (patcher\game | patcher\editor beside this script) into the folder the exe is written to,
    # before anything else: the interface set is built from them, and the exe reads them (5 Oct 2026)
    $generated = @(Copy-Resources $Build (Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))) $Progress)
    $effective = @(Get-BuildPatches $Build $Mode $Theme)
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
    $ref = if ($Mode) { $Build.ReferenceSha256[$Mode + $(if ($Theme -eq 'light') { '/light' } else { '' })] } else { $Build.PatchedSha256 }
    # an HD display fix was applied: build the INTRF_HD interface set for the chosen size (scripts,
    # briefing lists, letterboxed backgrounds, loading screens; the Council Wars and OZI copies too)
    if ($Mode -and $Mode -ne '640x480' -and ($ordered | Where-Object { $_.ContainsKey('SetSources') })) {
        $console = [bool] ($ordered | Where-Object { $_.Id -eq 'console' })
        if ($Progress) { & $Progress ("Writing the {0} interface set ({1} battlefield interface) into {2} (scripts, backgrounds, loading screens; other resolutions' folders are deleted) - this takes a few seconds..." -f $Mode, $(if ($console) { 'dark' } else { 'light' }), (Get-HdFolder $Mode)) }
        try {
            $generated += @(Write-InterfaceSet (Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))) $Mode $console)
        } catch {
            $generated += @('INTERFACE SET NOT WRITTEN: ' + $_.Exception.Message)
        }
    }
    if ($Mode -eq '640x480') {
        $dir = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
        # the original size reads the stock files: every HD folder (and the pre-October INTRF_HD) goes (maintainer's rule, 2 Oct 2026)
        try { $generated += Remove-OtherInterfaceSets $dir '' } catch { $generated += ('interface folders NOT deleted: ' + $_.Exception.Message) }
        if ($ordered | Where-Object { $_.Id -eq 'ozi' })    { try { $generated += Write-StockOziMenu $dir } catch { $generated += 'bintoze NOT written: ' + $_.Exception.Message } }
    }
    # Dark Colony Ultimate's `ozi` fix: the OZI MISSIONS save folder and its marker file, created when missing
    if ($Build.Id -eq 'CouncilWars' -and ($ordered | Where-Object { $_.Id -eq 'ozi' })) {
        $dir = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
        try { $generated += Write-OziSaveFolder $dir } catch { $generated += 'ozisave NOT created: ' + $_.Exception.Message }
    }
    # Dark Colony Ultimate's `music` fix: the options dialog with the MUSIC row for its three campaign modes
    if ($Mode -and $Build.Id -eq 'CouncilWars' -and ($ordered | Where-Object { $_.Id -eq 'music' })) {
        $dir = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
        try { $generated += Write-MusicDialogs $dir $Mode } catch { $generated += 'options dialog copies NOT written: ' + $_.Exception.Message }
    }
    # Dark Colony Ultimate's `online` fix: the ONLINE WAR and REPLAY screens for the size and DEFAULT_SERVER.TXT when missing (or still the bare shipped address)
    if ($Build.Id -eq 'CouncilWars' -and ($ordered | Where-Object { $_.Id -eq 'online' })) {
        $dir = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
        try { $generated += Write-OnlineScreen $dir $Mode } catch { $generated += 'ONLINE screen NOT written: ' + $_.Exception.Message }
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
function Get-DataProblems($Build, [object[]] $Chosen, [string] $GameDir, [string] $Mode, [string] $Theme = '') {
    $problems = @()
    $chosenIds = @($Chosen | ForEach-Object { $_.Id })
    $effective = @(Get-BuildPatches $Build $Mode $Theme)
    foreach ($p in $Chosen) {
        foreach ($need in @($p.Requires)) {
            if ($chosenIds -notcontains $need) {
                $other = @($effective | Where-Object { $_.Id -eq $need })
                $otherName = if ($other.Count -gt 0) { $other[0].Name } else { $need }
                $problems += ("fix '{0}' ({1}) only works together with fix '{2}' ({3}) - select both or neither" -f $p.Id, $p.Name, $need, $otherName)
            }
        }
        $missing = @()
        foreach ($rel in @($p.Data)) { if (-not (Test-DataFile $Build $GameDir $rel)) { $missing += $rel } }
        if ($missing.Count -gt 0) {
            $total = 0; foreach ($d in @($p.Data)) { $total++ }
            $shown = @($missing | Select-Object -First 8) -join ', '
            if ($missing.Count -gt 8) { $shown += (', ... ({0} more)' -f ($missing.Count - 8)) }
            $problems += ("fix '{0}' ({1}) needs {2} data files under '{3}' (or in the patcher's resource folder beside this script), {4} are missing: {5}. " +
                          "Install the game from your discs (the welcome page's checkbox), copy the game folder from the repository " +
                          "(https://github.com/endotermic/Dark-Colony) or write the exe into the game folder there.") -f $p.Id, $p.Name, $total, $GameDir, $missing.Count, $shown
        }
    }
    return $problems
}

# Which fixes of a build cannot be applied into $GameDir: their resources (Data files) are not there,
# or a fix they require is itself unavailable.  Returns a hashtable id -> one-line reason (empty = all
# available).  The window greys these out as "RESOURCES NOT FOUND", -All skips them.
function Get-UnavailableFixes($Build, [string] $GameDir, [string] $Mode, [string] $Theme = '') {
    $out = @{}
    if (-not $GameDir) { return $out }
    $effective = @(Get-BuildPatches $Build $Mode $Theme)
    foreach ($p in $effective) {
        $missing = @(); $total = 0
        foreach ($rel in @($p.Data)) { $total++; if (-not (Test-DataFile $Build $GameDir $rel)) { $missing += $rel } }
        if ($missing.Count -gt 0) {
            $tops = @{}
            foreach ($m in $missing) { $top = ($m -split '\\')[0]; if ($tops.ContainsKey($top)) { $tops[$top]++ } else { $tops[$top] = 1 } }
            $where = @($tops.Keys | Sort-Object | ForEach-Object { '{0}\ ({1})' -f $_, $tops[$_] }) -join ', '
            $out[$p.Id] = ('{0} of {1} resource files missing: {2}' -f $missing.Count, $total, $where)
        }
    }
    # a fix that needs an unavailable fix is unavailable too (repeat until nothing changes: requirements chain)
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
    if ($Patch.Theme) { $lines += ('only with the {0} battlefield interface (chosen together with the resolution)' -f $Patch.Theme) }
    if ($Patch.ContainsKey('SetSources')) {
        $lines += 'writes the interface set for this resolution into its own folder HD_<height>P (scripts, briefing lists, letterboxed backgrounds on the shipped BACKDROP.GIF in a grey panel frame, loading screens; exp\, dc\ and ozi_ns\ copies too) from the stock files and the shipped pictures in HD_SRC\<WxH>, and DELETES every other resolution''s folder first - the GIF codec is C# source in this file, compiled by Add-Type (see the INTERFACE SET section)'
    }
    return $lines
}

function Write-PatchList([switch] $WithEdits) {
    Write-Host ("Dark Colony patcher {0}, build {1} (generated {2})" -f $PatcherVersion, $PatcherBuild, $PatcherGenerated) -ForegroundColor Cyan
    foreach ($b in $Builds) {
        Write-Host ''
        Write-Host ("=== {0}: {1}" -f $b.Id, $b.Title) -ForegroundColor Cyan
        Write-Host ("    original {0} ({1} bytes)  SHA-256 {2}" -f $b.OriginalName, $b.Size, $b.OriginalSha256)
        Write-Host ("    all patches -> {0}         SHA-256 {1}" -f $b.OutputName, $b.PatchedSha256)
        if (@($b.Modes).Count -gt 0) {
            Write-Host ("    resolutions (choose one with -Resolution, no default): {0}; the published exe is the dark {1} build. Reference SHA-256 with every fix of that resolution (-Theme dark), and without fix console (-Theme light):" -f (($b.Modes | ForEach-Object { Format-ModeLabel $_ (Get-MonitorSize) }) -join ', '), $b.PublishedMode)
            foreach ($k in @($b.ReferenceSha256.Keys | Sort-Object)) { Write-Host ("      {0,-16} {1}" -f $k, $b.ReferenceSha256[$k]) }
        }
        $n = 0
        foreach ($p in $b.Patches) {
            $n++
            Write-Host ''
            $modeTag = if ($p.Mode -and $p.Mode -ne 'hd') { ' @ ' + $p.Mode } elseif ($p.Mode -eq 'hd') { ' @ every resolution but 640x480' } else { '' }
            if ($p.Theme) { $modeTag += ', ' + $p.Theme + ' battlefield interface only' }
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

''')
W(gen_disc_install.DISC_READER)
W(gen_disc_install.disc_data(MANIFEST))
W(gen_disc_install.DISC_LOGIC)
W(r'''
# =================================================================================================
#  WINDOW - the installer front end (Windows Forms, part of every Windows PowerShell)
# =================================================================================================
# The screen resolution the window applies to a build: the choice made on the "Options" page for the
# game ('' until the player has chosen one - nothing is preselected), '' for the map editor (it
# has none).  Script level, because the window's event handlers run outside Show-PatcherWindow and
# cannot see functions defined inside it.
function Get-GuiMode($Build) {
    if (@($Build.Modes).Count -gt 0) { return [string] $script:gui.Mode }
    return ''
}
# The battlefield interface theme ('light' / 'dark', '' until chosen) - only for a game at an HD size;
# at 640x480 (original) the game keeps its own interface and there is nothing to choose.
function Get-GuiTheme($Build) {
    if (@($Build.Modes).Count -gt 0 -and $script:gui.Mode -and $script:gui.Mode -ne '640x480') { return [string] $script:gui.Theme }
    return ''
}

# The fixes of one window item that will be applied: every fix of its resolution and theme that is
# available in the output folder and was not unticked.
function Get-GuiChosen($Item) {
    $out = @()
    foreach ($p in @(Get-BuildPatches $Item.Build (Get-GuiMode $Item.Build) (Get-GuiTheme $Item.Build))) {
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
    # options for patching DC, third screen for patching CW and fourth for patching maped"; 1 Oct 2026:
    # "use best approaches for installer building ... don't select any resolution by default. force the
    # client to select resolution once at the beginning, mark 640x480 as (original). mark patching 'Dark
    # Colony' executable as deprecated and disabled and skipped by default", then "dark mode must be
    # optional but not preselected, customer must be forced to select light mode (classic) or dark mode of
    # battlefield interface. resolution selection must be a dropdown, then under it dark/light theme
    # selection and under it checkbox about patching deprecated executable (if not selected then options
    # screen for dc16.exe must not appear at all)"):
    #   Welcome -> Options (resolution drop-down, light / dark battlefield interface; nothing preselected; the
    #   deprecated Dark Colony checkbox left with that build on 5 Oct 2026) -> one page per executable (Dark
    #   Colony Ultimate, the map editor) -> Ready to patch (the summary) -> Patch -> Finished, with Back / Next /
    #   Cancel.
    # One item per build; the untouched originals beside this script are found, each page keeps its own
    # fix choices (Unticked) and the fixes whose resources are missing.
    $items = @()
    foreach ($b in $Builds) {
        $items += @{ Build = $b; Path = $null; Data = $null; IsOriginal = $false; Out = $null; Checked = $false; Patches = @()
                     Status = 'not found - press Browse to pick it'; Color = 'Firebrick'; Unticked = @{}; Unavailable = @{}; Error = $null }
    }
    $script:gui = @{ Items = $items; Sel = -1; Step = 0; Syncing = $false; Mode = ''; Theme = ''
                     ModeList = @(); Patches = @(); Monitor = (Get-MonitorSize); Visible = @(); Last = 0
                     Here = (Split-Path -Parent $PSScriptRoot); Results = $null      # Here = the parent of patcher\ = the repository root (or the unpacked installer package)
                     Disc = $false; DiscCw = ''; DiscDc = ''; DiscDir = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Dark Colony'); Base = 1 }
    foreach ($b in $Builds) { if (@($b.Modes).Count -gt 0) { $script:gui.ModeList = @($b.Modes); break } }
    $n = $items.Count
    $mono = New-Object System.Drawing.Font('Consolas', 9)
    $bold = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)

    $form = New-Object System.Windows.Forms.Form
    $form.Text = "Dark Colony patcher $PatcherVersion (build $PatcherBuild)"
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
    $lblHello.Location = '24,16'; $lblHello.Size = '936,200'
    $lblHello.Text = @(
        'Welcome!  This installer builds the patched Dark Colony executables on your own PC, from the untouched',
        'original executables of this folder:',
        '',
        '    Dark Colony Ultimate         ENGEXP16.EXE  ->  Dark Colony Ultimate.exe   (Council Wars plus the Dark Colony,',
        '                                                                               OZI and Academy campaigns, ONLINE WAR)',
        '    Dark Colony Map Editor       maped.exe     ->  Dark Colony Map Editor.exe',
        '',
        'The next page asks for the screen resolution and the battlefield interface (light = classic, dark = the',
        'console style of the menus) - chosen once.  Then one page per executable shows its fixes (all selected),',
        'and a summary page before anything',
        'is written.  Nothing is downloaded, the originals are never changed, and every byte this script writes is',
        'listed, with its reason, in Apply-DarkColonyPatches.ps1 (open it in Notepad).'
    ) -join "`r`n"
    $lblHello.Font = New-Object System.Drawing.Font('Consolas', 9.5)
    $lblFound = New-Object System.Windows.Forms.Label
    $lblFound.Location = '24,226'; $lblFound.Size = '936,76'; $lblFound.Font = $mono
    $chkLnk = New-Object System.Windows.Forms.CheckBox
    $chkLnk.Text = 'Put a shortcut to each patched executable on the desktop'; $chkLnk.Location = '24,310'; $chkLnk.AutoSize = $true
    $chkLnk.Checked = $true; $chkLnk.Font = $bold
    $lblLnk = New-Object System.Windows.Forms.Label
    $lblLnk.Location = '44,334'; $lblLnk.Size = '900,36'
    $lblLnk.Text = 'Named "Dark Colony Ultimate", "Dark Colony Map Editor" (and "Dark Colony" if you patch it); each starts in its game folder, where the game finds its files.  An older shortcut of the same name is replaced.'
    # the disc install (5 Oct 2026, maintainer: "add a checkbox that adds a form to select both discs and installation
    # directory"): ticked, the wizard gets a "Game discs" page and copies the game from the player's own discs first
    $chkDisc = New-Object System.Windows.Forms.CheckBox
    $chkDisc.Text = 'Install the game first, from my original Dark Colony and Council Wars discs  (no game folder yet)'; $chkDisc.Location = '24,372'; $chkDisc.AutoSize = $true
    $chkDisc.Font = $bold
    $lblDisc = New-Object System.Windows.Forms.Label
    $lblDisc.Location = '44,396'; $lblDisc.Size = '900,40'
    $lblDisc.Text = ('Adds a page where you pick the two discs (a disc image .iso / .bin / .cue, or the drive of a mounted image or a real CD) and the ' +
                     'install folder (a "Dark Colony" folder in your Documents by default).  The game is copied from the discs, this patcher''s own files ' +
                     'are added and the executables are built there.  Ticked by itself when no game folder was found beside this installer.')
    $lblNext = New-Object System.Windows.Forms.Label
    $lblNext.Location = '24,556'; $lblNext.Size = '936,20'; $lblNext.Text = "Press Next to continue.          Dark Colony patcher $PatcherVersion, build $PatcherBuild (generated $PatcherGenerated)"
    # a missing or wrong original: a big red banner here, the details and the remedies on its page
    $lblProblem = New-Object System.Windows.Forms.Label
    $lblProblem.Location = '24,440'; $lblProblem.Size = '936,112'; $lblProblem.Visible = $false
    $lblProblem.BackColor = [System.Drawing.Color]::FromArgb(192, 0, 0); $lblProblem.ForeColor = [System.Drawing.Color]::White
    $lblProblem.Font = New-Object System.Drawing.Font('Segoe UI', 10.5, [System.Drawing.FontStyle]::Bold); $lblProblem.Padding = '12,8,12,8'
    $pWelcome.Controls.AddRange(@($lblHello, $lblFound, $chkLnk, $lblLnk, $chkDisc, $lblDisc, $lblNext, $lblProblem))

    # --- page "Game discs" (5 Oct 2026): shown as step 1 while the welcome checkbox is ticked - the two original discs
    # and the install folder.  Next checks the discs and takes the two originals (ENGEXP16.EXE, maped.exe) from them;
    # the whole game is copied at the Patch step.
    $pDisc = New-Object System.Windows.Forms.Panel
    $pDisc.Location = '0,66'; $pDisc.Size = '984,580'; $pDisc.Visible = $false
    $lblDiscIntro = New-Object System.Windows.Forms.Label
    $lblDiscIntro.Location = '24,14'; $lblDiscIntro.Size = '936,56'
    $lblDiscIntro.Text = ('The game is copied from your two original discs into the install folder (about 480 MB), this patcher''s own files are added and ' +
                          'the executables are built there.  A disc is a disc image file (.iso, .bin or .cue) or the drive letter of a mounted image or a real CD.  ' +
                          'Both discs are needed: the Council Wars disc holds the expansion and ENGEXP16.EXE, the Dark Colony disc the missions, the ' +
                          'encyclopedia, the Classic movies and the map editor.  Nothing is downloaded; the discs are read on this PC only.')
    $discRows = @()
    $y = 84
    foreach ($row in @(@('cw', 'Council Wars disc  (the "Dark Colony: The Council Wars" CD, volume COUNCILWARS):'),
                       @('dc', 'Dark Colony disc  (the original "Dark Colony" CD, volume DCUK):'),
                       @('dir', 'Install into  (a new or empty folder; an interrupted install can be resumed into the same folder):'))) {
        $l = New-Object System.Windows.Forms.Label
        $l.Text = $row[1]; $l.Location = "40,$y"; $l.AutoSize = $true; $l.Font = $bold
        $t = New-Object System.Windows.Forms.TextBox
        $t.Location = "40,$($y + 22)"; $t.Size = '660,23'; $t.Tag = $row[0]
        $b1 = New-Object System.Windows.Forms.Button
        $b2 = New-Object System.Windows.Forms.Button
        if ($row[0] -eq 'dir') {
            $b1.Text = 'Browse...'; $b1.Location = "836,$($y + 20)"; $b1.Size = '128,27'; $b1.Tag = 'dir'
            $b2.Visible = $false
        } else {
            $b1.Text = 'Image file...'; $b1.Location = "712,$($y + 20)"; $b1.Size = '116,27'; $b1.Tag = $row[0] + ':file'
            $b2.Text = 'Drive / folder...'; $b2.Location = "836,$($y + 20)"; $b2.Size = '128,27'; $b2.Tag = $row[0] + ':folder'
        }
        $pDisc.Controls.AddRange(@($l, $t, $b1, $b2))
        $discRows += @{ Key = $row[0]; Text = $t; File = $b1; Folder = $b2 }
        $y += 66
    }
    $lblDiscNote = New-Object System.Windows.Forms.Label
    $lblDiscNote.Location = '40,290'; $lblDiscNote.Size = '920,110'; $lblDiscNote.ForeColor = [System.Drawing.Color]::DimGray
    $lblDiscNote.Text = (@('The soundtrack: both CDs carry the music as audio tracks 2-5.  From a .bin / .cue image or a real CD in a drive they are ripped',
                          'and encoded to MP3 (192 kbit/s) with Windows'' own encoder into MUSIC\ and exp\music\ - the "music" fix plays them.  An .iso image and a',
                          'mounted .iso hold the data track only, so there the music is left out (copy the eight MP3 files from the repository instead).',
                          'Files already in the install folder with the right size are kept, so a second run after an interruption only fills the gaps.',
                          'Press Next: the discs are checked and ENGEXP16.EXE and maped.exe are taken from them; the rest is copied when you press Patch.') -join "`r`n")
    $lblDiscStatus = New-Object System.Windows.Forms.Label
    $lblDiscStatus.Location = '24,520'; $lblDiscStatus.Size = '936,52'; $lblDiscStatus.Font = $bold
    $lblDiscStatus.Text = 'Pick both discs and the install folder, then press Next.'
    $pDisc.Controls.AddRange(@($lblDiscIntro, $lblDiscNote, $lblDiscStatus))

    # --- page 1: options - the resolution drop-down, the battlefield interface theme, the deprecated build.
    # NOTHING is preselected (maintainer, 1 Oct 2026): Next stays disabled until the resolution and - at an
    # HD size - the theme are chosen.
    $pRes = New-Object System.Windows.Forms.Panel
    $pRes.Location = '0,66'; $pRes.Size = '984,580'; $pRes.Visible = $false
    $lblResIntro = New-Object System.Windows.Forms.Label
    $lblResIntro.Location = '24,14'; $lblResIntro.Size = '936,36'
    $lblResIntro.Text = ('These two choices are made once, here; you can come back ' +
                         'to this page with "< Back".  The executables published in the repository are the 1024x768 build with the dark interface.')
    $lblResL = New-Object System.Windows.Forms.Label
    $lblResL.Text = 'Screen resolution:'; $lblResL.Location = '40,62'; $lblResL.AutoSize = $true; $lblResL.Font = $bold
    $cmbRes = New-Object System.Windows.Forms.ComboBox
    $cmbRes.Location = '40,84'; $cmbRes.Size = '440,23'; $cmbRes.DropDownStyle = 'DropDownList'
    [void] $cmbRes.Items.Add('(choose a screen resolution)')
    foreach ($m in $script:gui.ModeList) { [void] $cmbRes.Items.Add((Format-ModeLabel $m $script:gui.Monitor)) }
    $cmbRes.SelectedIndex = 0
    $lblResNote = New-Object System.Windows.Forms.Label
    $lblResNote.Location = '40,112'; $lblResNote.Size = '920,54'; $lblResNote.ForeColor = [System.Drawing.Color]::DimGray
    $lblResNote.Text = ('640x480 (original) is the game as it shipped: no display fix, the stock menus and HUD, every other fix applied.  The sizes with ' +
                        'the aspect ratio of your monitor are marked "recommended for your screen".  Any other size selects that size''s display fix ' +
                        '(screen mode, map view, menus, HUD, movie frame, interface data, clock hand - one fix per size) and makes the patcher WRITE the ' +
                        'interface set for it into its own folder in the game folder (HD_0768P for 1024x768, HD_1080P for 1920x1080 ...; a few seconds) - ' +
                        'and DELETE the folders of every other resolution, so that no file of another size is left anywhere.')
    $lblThemeL = New-Object System.Windows.Forms.Label
    $lblThemeL.Text = 'Battlefield interface:'; $lblThemeL.Location = '40,180'; $lblThemeL.AutoSize = $true; $lblThemeL.Font = $bold
    $rbLight = New-Object System.Windows.Forms.RadioButton
    $rbLight.Location = '60,204'; $rbLight.AutoSize = $true; $rbLight.Tag = 'light'; $rbLight.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $rbLight.Text = 'Light (classic)   -   the original brushed-metal battlefield interface: frame, buttons, dialogs and clock dial as the game shipped them'
    $rbDark = New-Object System.Windows.Forms.RadioButton
    $rbDark.Location = '60,232'; $rbDark.AutoSize = $true; $rbDark.Tag = 'dark'; $rbDark.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $rbDark.Text = 'Dark   -   the console style of the menus: grey pipework frame, red-ringed buttons, black dialogs, redrawn clock dial'
    $lblThemeNote = New-Object System.Windows.Forms.Label
    $lblThemeNote.Location = '60,260'; $lblThemeNote.Size = '900,36'; $lblThemeNote.ForeColor = [System.Drawing.Color]::DimGray
    $lblThemeNote.Text = ('Data only, plus one 14-byte fix for the dark clock dial (fix "console").  At 640x480 (original) there is nothing to choose: ' +
                          'the game keeps its own interface.')
    $lblResPick = New-Object System.Windows.Forms.Label
    $lblResPick.Location = '24,540'; $lblResPick.Size = '936,20'; $lblResPick.Font = $bold
    $lblResPick.Text = 'Choose a screen resolution and a battlefield interface to continue.'
    $pRes.Controls.AddRange(@($lblResIntro, $lblResL, $cmbRes, $lblResNote, $lblThemeL, $rbLight, $rbDark, $lblThemeNote, $lblResPick))

    # --- pages 2..: one per executable (the page's controls carry the executable's index in .Tag,
    # because the handlers run outside this function)
    $tip = New-Object System.Windows.Forms.ToolTip
    $pages = @()
    for ($i = 0; $i -lt $n; $i++) {
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
        # the screen resolution and theme chosen on the options page (the games) - shown, not chosen here
        $res = New-Object System.Windows.Forms.Label
        $res.Location = '20,108'; $res.Size = '944,18'; $res.ForeColor = [System.Drawing.Color]::DimGray
        $res.Text = if (@($b.Modes).Count -gt 0) { 'Screen resolution: (chosen on the options page)' } else { 'The map editor has no screen resolution or interface theme to choose.' }
        $all = New-Object System.Windows.Forms.CheckBox
        $all.Text = 'Select all fixes  (result = the reference build)'; $all.Location = '20,164'; $all.AutoSize = $true
        $all.Font = $bold; $all.Enabled = $false; $all.Tag = $i
        $lst = New-Object System.Windows.Forms.CheckedListBox
        $lst.Location = '20,188'; $lst.Size = '452,348'; $lst.CheckOnClick = $true; $lst.IntegralHeight = $false; $lst.Enabled = $false; $lst.Tag = $i
        $lst.Font = New-Object System.Drawing.Font('Segoe UI', 10)
        $li = New-Object System.Windows.Forms.Label
        $li.Text = 'What the highlighted fix changes (always applied in the order of the list):'; $li.Location = '484,166'; $li.AutoSize = $true
        $inf = New-Object System.Windows.Forms.TextBox
        $inf.Location = '484,188'; $inf.Size = '480,348'; $inf.Multiline = $true; $inf.ReadOnly = $true; $inf.ScrollBars = 'Vertical'
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
        $pnl.Controls.AddRange(@($inc, $lo, $path, $brw, $det, $res, $all, $lst, $li, $inf, $lw, $out, $err))
        $err.BringToFront()
        $items[$i].UI = @{ Page = $pnl; Check = $inc; Path = $path; Browse = $brw; Status = $det; Res = $res; All = $all; List = $lst; Info = $inf; Out = $out
                           Error = $err; ErrorTitle = $errTitle; ErrorBody = $errBody; ErrorPick = $errPick; ErrorGet = $errGet
                           # hidden while the red box is shown, so it is in front whatever the drawing order
                           Behind = @($res, $all, $lst, $li, $inf) }
        $pages += $pnl
    }

    # --- ready to patch - the summary of everything that will be written
    $pReady = New-Object System.Windows.Forms.Panel
    $pReady.Location = '0,66'; $pReady.Size = '984,580'; $pReady.Visible = $false
    $txtReady = New-Object System.Windows.Forms.TextBox
    $txtReady.Location = '20,16'; $txtReady.Size = '944,520'; $txtReady.Multiline = $true; $txtReady.ReadOnly = $true
    $txtReady.ScrollBars = 'Vertical'; $txtReady.Font = $mono; $txtReady.BackColor = [System.Drawing.SystemColors]::Window
    $lblReady = New-Object System.Windows.Forms.Label
    $lblReady.Location = '20,546'; $lblReady.Size = '944,24'; $lblReady.Font = $bold
    $lblReady.Text = 'Press Patch to write the files listed above, or < Back to change something.'
    $pReady.Controls.AddRange(@($txtReady, $lblReady))

    # --- finished
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

    $form.Controls.AddRange(@($header, $sepTop, $pWelcome, $pDisc, $pRes) + $pages + @($pReady, $pDone, $sepBot, $btnVerify, $lblLog, $btnBack, $btnNext, $btnCancel))
    # All / List / Info / Out are re-pointed to the current page's controls by Select
    $script:gui.Controls = @{ Form = $form; Title = $lblTitle; Sub = $lblSub; Welcome = $pWelcome; Found = $lblFound; Problem = $lblProblem
                              Options = $pRes; ModeBox = $cmbRes; ThemeLight = $rbLight; ThemeDark = $rbDark; ModePick = $lblResPick
                              Ready = $pReady; ReadyText = $txtReady
                              Done = $pDone; DoneText = $txtDone; DoneNote = $lblDone; Shortcut = $chkLnk
                              DiscBox = $chkDisc; Discs = $pDisc; DiscStatus = $lblDiscStatus; DiscRows = $discRows
                              DiscCw = ($discRows | Where-Object { $_.Key -eq 'cw' }).Text; DiscDc = ($discRows | Where-Object { $_.Key -eq 'dc' }).Text; DiscDir = ($discRows | Where-Object { $_.Key -eq 'dir' }).Text
                              Back = $btnBack; Next = $btnNext; Cancel = $btnCancel; Apply = $btnNext
                              Verify = $btnVerify; Log = $lblLog; All = $items[0].UI.All; List = $items[0].UI.List; Info = $items[0].UI.Info
                              Out = $items[0].UI.Out; Status = $items[0].UI.Status; Res = $items[0].UI.Res }
    $c = $script:gui.Controls   # event handlers run outside this function's scope, so they reach the controls through this table

    # --- behaviour
    # The executables with a page in the wizard: every build.  Visible = their indices, Last = the "Ready" step number.
    $script:gui.Layout = {
        $g = $script:gui
        $vis = @()
        for ($i = 0; $i -lt $g.Items.Count; $i++) { $vis += $i }
        $g.Visible = $vis
        $g.Base = if ($g.Disc) { 2 } else { 1 }        # the options page's step: the "Game discs" page is step 1 while the disc install is on
        $g.Last = $vis.Count + $g.Base + 1
    }
    # the wizard step of executable $index, or -1 when its page is not shown
    $script:gui.StepOf = {
        param([int] $index)
        $k = [Array]::IndexOf(@($script:gui.Visible), $index)
        if ($k -lt 0) { return -1 }
        return $k + $script:gui.Base + 1
    }
    & $script:gui.Layout

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
        if (@($it.Build.Modes).Count -gt 0) {
            if (-not $g.Mode) { $u.Res.Text = 'Screen resolution: not chosen yet - go back to the options page' }
            else {
                $theme = Get-GuiTheme $it.Build
                $themeText = if ($g.Mode -eq '640x480') { 'the original interface' } elseif ($theme -eq 'light') { 'light (classic)' } elseif ($theme -eq 'dark') { 'dark (console style)' } else { 'NOT CHOSEN' }
                $u.Res.Text = 'Screen resolution: ' + (Format-ModeLabel $g.Mode $g.Monitor) + ';  battlefield interface: ' + $themeText + '   (chosen on the options page - press < Back to change)'
            }
        }
        $u.Error.Visible = [bool] $it.Error
        foreach ($x in $u.Behind) { $x.Visible = -not $it.Error }
        if ($it.Error) { $u.ErrorTitle.Text = $it.Error.Title; $u.ErrorBody.Text = $it.Error.Body; $u.Error.BringToFront() }
        $g.Syncing = $false
        $bad = @($g.Items | Where-Object { $_.Error -and -not ($g.Disc -and $_.Error.Kind -eq 'missing') })
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
            $lines += ('  {0} {1,-28} {2}' -f $mark, $x.Build.ProductName, $(if ($shown) { "$shown  ($($x.Status))" } elseif ($g.Disc) { 'taken from the discs (next page)' } else { "not found ($($x.Build.OriginalPath)) - you can pick it on its page" }))
        }
        $g.Controls.Found.Text = $lines -join "`r`n"
    }

    # Re-checks the resources of an item's fixes against the folder its exe will be written to: fixes whose
    # files are missing (or which need such a fix) get "RESOURCES NOT FOUND" and are left out.
    $script:gui.Recheck = {
        param($it)
        $it.Unavailable = @{}
        if ($it.Out) { $it.Unavailable = Get-UnavailableFixes $it.Build (Split-Path -Parent ([System.IO.Path]::GetFullPath($it.Out))) (Get-GuiMode $it.Build) (Get-GuiTheme $it.Build) }
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
            'unknown'    { $title = "$name IS NOT $($b.OriginalName.ToUpperInvariant()) - NOT A DARK COLONY EXECUTABLE"; $status = 'NOT the original - unknown file'
                           $found = "$path`r`n            $($data.Length) bytes, SHA-256 $(Get-Sha256Hex $data)" }
            'patched'    { $title = "$name IS ALREADY PATCHED - NOT THE ORIGINAL"; $status = 'NOT the original - already patched'
                           $found = "$path`r`n            $($data.Length) bytes, SHA-256 $(Get-Sha256Hex $data) (the fixes are already in it)" }
            default      { $title = "$name IS NOT THE UNTOUCHED ORIGINAL"; $status = 'NOT the original - modified copy'
                           $found = "$path`r`n            $($data.Length) bytes, SHA-256 $(Get-Sha256Hex $data)" }
        }
        $it.Status = $status
        $it.Detail = "$title - see the red box below."
        $it.Error = @{
            Kind  = $kind
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
    # A deprecated build is ticked only while its box on the options page is.
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
                if ($select -and $g.Step -eq (& $g.StepOf $target)) { & $g.Select $target }
            } else {
                $c.Log.ForeColor = 'Firebrick'
                $c.Log.Text = "$([System.IO.Path]::GetFileName($path)): not a build this script knows ($($data.Length) bytes)."
            }
            return
        }
        $it = $null; $index = -1
        for ($i = 0; $i -lt $g.Items.Count; $i++) { if ($g.Items[$i].Build.Id -eq $build.Id) { $it = $g.Items[$i]; $index = $i } }
        $it.Error = $null
        $it.Path = $path; $it.Data = $data; $it.IsOriginal = $isOriginal; $it.Out = Join-Path (Split-Path $path) $build.OutputName
        $it.Checked = $true
        $it.Color = 'DarkGreen'; $it.Status = 'untouched original'
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
        if ($select -and $g.Step -ge 2 -and $g.Step -eq (& $g.StepOf $index)) { & $g.Select $index }
    }
    $script:gui.Load = $loadOriginal

    # (Re)fills one executable's page for the chosen resolution and theme: its fixes, ticked unless unticked
    # by the player or unavailable (resources not found).  The checklist is live only while the executable
    # is ticked (a deprecated one starts unticked = disabled).
    $script:gui.FillItem = {
        param($it)
        $g = $script:gui
        $t = $it.UI
        $g.Syncing = $true
        $t.List.Items.Clear(); $t.All.Checked = $false
        $it.Patches = @()
        $hasMode = (@($it.Build.Modes).Count -eq 0) -or ([bool] $g.Mode -and ($g.Mode -eq '640x480' -or [bool] $g.Theme))
        if ($it.Data -and $hasMode) {
            $it.Patches = @(Get-BuildPatches $it.Build (Get-GuiMode $it.Build) (Get-GuiTheme $it.Build))
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
        $live = [bool] $it.Data -and $hasMode -and [bool] $it.Checked
        $t.List.Enabled = $live; $t.All.Enabled = $live
        if ($it.Data -and -not $hasMode) { $t.Info.Text = 'Choose the screen resolution and the battlefield interface on the options page first (press < Back).' }
        elseif ($it.Data -and -not $it.Checked) { $t.Info.Text = "$($it.Build.ProductName) is unticked and will be skipped.  Tick ""Patch $($it.Build.ProductName)"" above to patch it." }
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
            & $g.FillItem $it
            if ($g.Sel -ge 0 -and $g.Items[$g.Sel] -eq $it) { & $g.Select $g.Sel }
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

    # The disc install (5 Oct 2026).  SetDiscMode: the welcome checkbox = a "Game discs" page as step 1 and the originals
    # from the discs.  PrepareDiscs (Next on that page): opens both discs, checks them, extracts the two originals into
    # the install folder and loads them like browsed originals - the executable pages then work as always; the rest of
    # the game is copied at Patch (Apply).  SetDiscPaths is the test hook for the three text boxes.
    $script:gui.SetDiscMode = {
        param([bool] $on)
        $g = $script:gui
        $c = $g.Controls
        $g.Disc = $on
        if (-not $on) { $script:DiscInstall = $null }
        $g.Syncing = $true; $c.DiscBox.Checked = $on; $g.Syncing = $false
        & $g.Layout
        foreach ($x in $g.Items) { & $g.ShowRow $x }
        if ($g.Step -gt 0) { & $g.GoTo $g.Step }
    }
    $script:gui.SetDiscPaths = {
        param([string] $cw, [string] $dc, [string] $dir)
        $g = $script:gui
        $c = $g.Controls
        $g.DiscCw = $cw; $g.DiscDc = $dc; $g.DiscDir = $dir
        $c.DiscCw.Text = $cw; $c.DiscDc.Text = $dc; $c.DiscDir.Text = $dir
    }
    $script:gui.PrepareDiscs = {
        $g = $script:gui
        $c = $g.Controls
        $g.DiscCw = $c.DiscCw.Text.Trim(); $g.DiscDc = $c.DiscDc.Text.Trim(); $g.DiscDir = $c.DiscDir.Text.Trim()
        $c.DiscStatus.ForeColor = [System.Drawing.Color]::Firebrick
        if (-not $g.DiscCw -or -not $g.DiscDc -or -not $g.DiscDir) { $c.DiscStatus.Text = 'Pick both discs and the install folder first.'; return $false }
        if ($g.DiscCw -eq $g.DiscDc) { $c.DiscStatus.Text = 'The two discs are the same file or drive - the Council Wars and the Dark Colony disc are two different CDs.'; return $false }
        $c.DiscStatus.ForeColor = [System.Drawing.Color]::Black; $c.DiscStatus.Text = 'Reading the discs...'; $c.DiscStatus.Refresh()
        try {
            $g.DiscDir = Get-AbsolutePath $g.DiscDir
            [void] [System.IO.Directory]::CreateDirectory($g.DiscDir)
            $orig = Install-DiscOriginals $g.DiscCw $g.DiscDc $g.DiscDir
        } catch {
            $c.DiscStatus.ForeColor = [System.Drawing.Color]::Firebrick
            $c.DiscStatus.Text = 'Cannot install from these discs: ' + $_.Exception.Message
            return $false
        }
        $script:DiscInstall = @{ Dir = $g.DiscDir; Cw = $g.DiscCw; Dc = $g.DiscDc; Audio = [bool] $orig['Audio'] }    # the fixes' data files will be there at Patch
        foreach ($id in @($orig.Keys | Where-Object { $_ -ne 'Audio' -and $_ -ne 'AudioNote' })) { & $g.Load $orig[$id] $false }
        $c.DiscStatus.ForeColor = [System.Drawing.Color]::DarkGreen
        $c.DiscStatus.Text = ('Both discs are fine.  ENGEXP16.EXE and maped.exe were taken from them into {0}; the game itself ({1} files) is copied when you press Patch.  {2}' -f $g.DiscDir, $DiscFiles.Count,
            $(if ($orig['Audio']) { 'The soundtrack (4 + 4 audio tracks) will be ripped and encoded to MP3.' } else { 'No soundtrack from these discs: ' + $orig['AudioNote'] + ' - fix music is left out.' }))
        & $g.Refresh
        return $true
    }
    $c.DiscBox.Add_CheckedChanged({
        param($sender, $e)
        $g = $script:gui
        if ($g.Syncing) { return }
        & $g.SetDiscMode ([bool] $sender.Checked)
    })
    $discBrowse = {
        param($sender, $e)
        $c = $script:gui.Controls
        $parts = ([string] $sender.Tag).Split(':')
        $row = $c.DiscRows | Where-Object { $_.Key -eq $parts[0] }
        if ($parts[0] -eq 'dir') {
            $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
            $dlg.Description = 'The folder to install the game into (a new or empty folder)'
            if ($row.Text.Text -and (Test-Path -LiteralPath $row.Text.Text)) { $dlg.SelectedPath = $row.Text.Text }
            if ($dlg.ShowDialog($c.Form) -eq 'OK') { $row.Text.Text = $dlg.SelectedPath }
        } elseif ($parts[1] -eq 'file') {
            $dlg = New-Object System.Windows.Forms.OpenFileDialog
            $dlg.Title = $(if ($parts[0] -eq 'cw') { 'The Council Wars disc image' } else { 'The Dark Colony disc image' })
            $dlg.Filter = 'Disc images (*.iso;*.bin;*.cue;*.img)|*.iso;*.bin;*.cue;*.img|All files (*.*)|*.*'
            if ($dlg.ShowDialog($c.Form) -eq 'OK') { $row.Text.Text = $dlg.FileName }
        } else {
            $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
            $dlg.Description = $(if ($parts[0] -eq 'cw') { 'The drive (or folder) holding the Council Wars disc' } else { 'The drive (or folder) holding the Dark Colony disc' })
            $dlg.RootFolder = 'MyComputer'
            if ($dlg.ShowDialog($c.Form) -eq 'OK') { $row.Text.Text = $dlg.SelectedPath }
        }
    }
    foreach ($row in $c.DiscRows) { $row.File.Add_Click($discBrowse); $row.Folder.Add_Click($discBrowse) }

    # The options page.  Refresh: every executable page is refilled for the choices (its unticked fixes
    # survive), the theme radios are live only at an HD size, Next follows the state, the status line says
    # what is still missing.  SetMode / SetTheme are the handlers' work and the test hooks.
    $script:gui.Refresh = {
        $g = $script:gui
        $c = $g.Controls
        $hd = [bool] $g.Mode -and $g.Mode -ne '640x480'
        $c.ThemeLight.Enabled = $hd; $c.ThemeDark.Enabled = $hd
        & $g.Layout
        foreach ($x in $g.Items) { & $g.Recheck $x; & $g.ShowRow $x; & $g.FillItem $x }
        if ($g.Sel -ge 0) { & $g.Select $g.Sel }
        $ready = [bool] $g.Mode -and (-not $hd -or [bool] $g.Theme)
        $c.ModePick.Text = if (-not $g.Mode) { 'Choose a screen resolution and a battlefield interface to continue.' }
                           elseif (-not $ready) { 'Screen resolution: ' + (Format-ModeLabel $g.Mode $g.Monitor) + '.  Now choose the battlefield interface (light or dark) to continue.' }
                           elseif (-not $hd) { 'Screen resolution: 640x480 (original) - the game keeps its own interface.  Press Next to continue.' }
                           else { 'Screen resolution: ' + (Format-ModeLabel $g.Mode $g.Monitor) + ', ' + $g.Theme + ' battlefield interface.  Press Next to continue.' }
        if ($g.Step -eq $g.Base) { $c.Next.Enabled = $ready }
    }
    $script:gui.SetMode = {
        param([string] $mode)
        $g = $script:gui
        $c = $g.Controls
        if ($mode -and $g.ModeList -notcontains $mode) { throw "unknown resolution '$mode'" }
        $g.Mode = $mode
        $g.Syncing = $true
        $c.ModeBox.SelectedIndex = if ($mode) { [Array]::IndexOf($g.ModeList, $mode) + 1 } else { 0 }
        $g.Syncing = $false
        & $g.Refresh
    }
    $script:gui.SetTheme = {
        param([string] $theme)
        $g = $script:gui
        $c = $g.Controls
        if ($theme -and ('light', 'dark') -notcontains $theme) { throw "unknown theme '$theme'" }
        $g.Theme = $theme
        $g.Syncing = $true
        $c.ThemeLight.Checked = ($theme -eq 'light'); $c.ThemeDark.Checked = ($theme -eq 'dark')
        $g.Syncing = $false
        & $g.Refresh
    }
    $c.ModeBox.Add_SelectedIndexChanged({
        param($sender, $e)
        $g = $script:gui
        if ($g.Syncing) { return }
        & $g.SetMode $(if ($sender.SelectedIndex -le 0) { '' } else { [string] $g.ModeList[$sender.SelectedIndex - 1] })
    })
    $themeCheck = {
        param($sender, $e)
        $g = $script:gui
        if ($g.Syncing -or -not $sender.Checked) { return }
        & $g.SetTheme ([string] $sender.Tag)
    }
    $c.ThemeLight.Add_CheckedChanged($themeCheck)
    $c.ThemeDark.Add_CheckedChanged($themeCheck)
    foreach ($it in $script:gui.Items) {
        $u = $it.UI
        $u.Check.Add_CheckedChanged($rowCheck)
        $u.Browse.Add_Click($rowBrowse)
        $u.ErrorPick.Add_Click($rowBrowse)
        $u.ErrorGet.Add_Click({ param($sender, $e) Start-Process $script:gui.Items[[int] $sender.Tag].Build.RepoUrl })
        $u.All.Add_CheckedChanged($tabAll)
        $u.List.Add_ItemCheck($tabCheck)
        $u.List.Add_SelectedIndexChanged($tabSelect)
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

    # one line per executable that is not patched, with the reason (the summary and the finished page)
    $script:gui.SkippedLines = {
        $g = $script:gui
        $out = @()
        foreach ($it in $g.Items) {
            if ($it.Checked -and $it.Data) { continue }
            $b = $it.Build
            if (-not $it.Data) { $out += $b.ProductName + ' (no usable original)' }
            else { $out += $b.ProductName + ' (unticked on its page)' }
        }
        return $out
    }

    # The summary shown on the "Ready to patch" page: resolution and theme, every executable (written with
    # which fixes, or skipped and why), the interface set, the shortcuts.  Returns text lines.
    $script:gui.Summary = {
        $g = $script:gui
        $c = $g.Controls
        $lines = @()
        if ($g.Disc) {
            $lines += 'Game install:            from the discs ' + $g.DiscCw + '  and  ' + $g.DiscDc
            $lines += ('{0,-24} into {1}  ({2} files, about 480 MB; files already there with the right size are kept)' -f '', $g.DiscDir, $DiscFiles.Count)
            $lines += ''
        }
        $lines += 'Screen resolution:       ' + $(if ($g.Mode) { Format-ModeLabel $g.Mode $g.Monitor } else { 'NOT CHOSEN - go back to the options page' })
        $lines += 'Battlefield interface:   ' + $(if ($g.Mode -eq '640x480') { 'the original (640x480 keeps the stock interface)' } elseif ($g.Theme -eq 'light') { 'light (classic) - the original metal interface' } elseif ($g.Theme -eq 'dark') { 'dark - the console style of the menus' } else { 'NOT CHOSEN - go back to the options page' })
        $lines += ''
        # paths under the script's folder (the normal case) are shown relative to it, so a line fits
        $sep = [System.IO.Path]::DirectorySeparatorChar
        $root = if ($g.Here) { $g.Here.TrimEnd($sep) + $sep } else { $null }
        $short = { param([string] $p) if ($p -and $root -and $p.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { $p.Substring($root.Length) } else { $p } }
        foreach ($it in $g.Items) {
            $b = $it.Build
            if (-not $it.Data) {
                $lines += ('{0,-24} SKIPPED - no usable original ({1})' -f $b.ProductName, $it.Status)
            } elseif (-not $it.Checked) {
                $lines += ('{0,-24} SKIPPED - unticked on its page' -f $b.ProductName)
            } else {
                $chosen = @(Get-GuiChosen $it)
                $effective = @(Get-BuildPatches $b (Get-GuiMode $b) (Get-GuiTheme $b))
                $mode = Get-GuiMode $b
                $lines += ('{0,-24} {1}  ->  {2}' -f $b.ProductName, (& $short $it.Path), (& $short $it.Out))
                $what = if ($chosen.Count -eq $effective.Count) { 'all {0} fixes{1} (= the reference build)' -f $chosen.Count, $(if ($mode) { " for $mode" } else { '' }) }
                        else { '{0} of {1} fixes{2}' -f $chosen.Count, $effective.Count, $(if ($mode) { " for $mode" } else { '' }) }
                $lines += ('{0,-24} {1}' -f '', $what)
                $left = @($effective | Where-Object { $p = $_; -not ($chosen | Where-Object { $_.Id -eq $p.Id }) })
                foreach ($p in $left) {
                    $why = if ($it.Unavailable.ContainsKey($p.Id)) { 'resources not found' } else { 'unticked' }
                    $lines += ('{0,-24}   left out: {1} ({2})' -f '', $p.Name, $why)
                }
                if ($mode -and $mode -ne '640x480' -and ($chosen | Where-Object { $_.ContainsKey('SetSources') })) {
                    $folder = Get-HdFolder $mode
                    $lines += ('{0,-24} writes the {1} interface set ({2} battlefield interface) into {3}\ (and exp\{3}\, ozi_ns\{3}\ for Dark Colony Ultimate)' -f '', $mode, (Get-GuiTheme $b), $folder)
                    $gone = @(Get-OtherInterfaceSets (Split-Path -Parent ([System.IO.Path]::GetFullPath($it.Out))) $folder)
                    if ($gone.Count -gt 0) { $lines += ('{0,-24} DELETES the interface files of other resolutions: {1}' -f '', ($gone -join ', ')) }
                } elseif ($mode -eq '640x480') {
                    $gone = @(Get-OtherInterfaceSets (Split-Path -Parent ([System.IO.Path]::GetFullPath($it.Out))) '')
                    if ($gone.Count -gt 0) { $lines += ('{0,-24} DELETES the interface folders of the HD resolutions: {1}' -f '', ($gone -join ', ')) }
                }
                if (Test-Path -LiteralPath $it.Out) { $lines += ('{0,-24} REPLACES the existing {1}' -f '', (Split-Path -Leaf $it.Out)) }
            }
            $lines += ''
        }
        $lines += 'Desktop shortcuts:       ' + $(if ($c.Shortcut.Checked) { 'one per patched executable, starting in its game folder' } else { 'none' })
        $lines += ''
        $lines += 'The originals are never changed.  Every byte written is listed in Apply-DarkColonyPatches.ps1.'
        return $lines
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
            if (@($it.Build.Modes).Count -gt 0 -and -not $g.Mode) { $refused = "$($it.Build.ProductName): no screen resolution chosen - go back to the options page and choose one." }
            elseif (@($it.Build.Modes).Count -gt 0 -and $g.Mode -ne '640x480' -and -not $g.Theme) { $refused = "$($it.Build.ProductName): no battlefield interface chosen - go back to the options page and choose light or dark." }
            elseif (@(Get-GuiChosen $it).Count -eq 0) { $refused = "$($it.Build.ProductName): no fix selected - tick at least one fix, or untick the executable." }
            elseif ([System.IO.Path]::GetFullPath($it.Out) -eq [System.IO.Path]::GetFullPath($it.Path)) { $refused = "$($it.Build.ProductName): the output must not be the original file - the original is never written over." }
        }
        if ($refused) {
            $c.Log.ForeColor = 'Firebrick'; $c.Log.Text = $refused
            if ($interactive) { & $g.Notify $refused 'Nothing to do' 'Warning' }
            return $null
        }
        $problems = @()
        foreach ($it in $todo) {
            foreach ($pr in @(Get-DataProblems $it.Build @(Get-GuiChosen $it) (Split-Path -Parent ([System.IO.Path]::GetFullPath($it.Out))) (Get-GuiMode $it.Build) (Get-GuiTheme $it.Build))) {
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
        # the interface files of other resolutions that this run deletes (maintainer's rule, 2 Oct 2026)
        $gone = @()
        foreach ($it in $todo) {
            if (@($it.Build.Modes).Count -eq 0) { continue }
            $m = Get-GuiMode $it.Build
            $keep = if ($m -and $m -ne '640x480') { Get-HdFolder $m } else { '' }
            foreach ($x in @(Get-OtherInterfaceSets (Split-Path -Parent ([System.IO.Path]::GetFullPath($it.Out))) $keep)) { if ($gone -notcontains $x) { $gone += $x } }
        }
        if (($existing.Count -gt 0 -or $gone.Count -gt 0) -and $interactive) {
            $q = ''
            if ($existing.Count -gt 0) { $q += "These files exist and will be replaced:`r`n`r`n" + ($existing -join "`r`n") + "`r`n`r`n" }
            if ($gone.Count -gt 0) { $q += "These interface files of OTHER resolutions will be deleted from the game folder (nothing of another size may stay):`r`n`r`n" + ($gone -join "`r`n") + "`r`n`r`n" }
            $answer = [System.Windows.Forms.MessageBox]::Show($c.Form, ($q + 'Continue?'), 'Replace and delete files?', 'YesNo', 'Question')
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
            if ($g.Disc) {
                $g.StepPrefix = 'Discs: '
                try {
                    $dr = Install-GameFromDiscs $g.DiscCw $g.DiscDc $g.DiscDir $g.Progress
                    $line = 'Game installed from the discs into {0}: {1} files copied ({2} MB){3}' -f $g.DiscDir, $dr.Files, [int][Math]::Round($dr.Bytes / 1MB), $(if ($dr.Skipped) { ", $($dr.Skipped) already there" } else { '' })
                    $kind = 'ok'
                    foreach ($ml in @($dr.Music)) { $line += "`r`n    " + $ml; if ($ml -match 'NOT written') { $kind = 'warning' } }
                    if (@($dr.Missing).Count -gt 0) { $kind = 'warning'; $line += "`r`n    NOT on your discs ({0} files - another pressing?): {1}" -f @($dr.Missing).Count, (@($dr.Missing | Select-Object -First 6) -join ', ') }
                    $results += @{ Item = $null; R = $null; Error = $null; Shortcut = $null; Kind = $kind; Line = $line }
                } catch {
                    $results += @{ Item = $null; R = $null; Error = $_.Exception.Message; Shortcut = $null; Kind = 'error'; Line = 'Installing the game from the discs FAILED - nothing patched:' + "`r`n    " + $_.Exception.Message }
                    $todo = @()
                }
            }
            $k = 0
            foreach ($it in $todo) {
                $k++
                $g.StepPrefix = "[$k/$($todo.Count)] $($it.Build.ProductName): "
                $mode = Get-GuiMode $it.Build
                $theme = Get-GuiTheme $it.Build
                $res = @{ Item = $it; R = $null; Error = $null; Shortcut = $null; Kind = 'ok'; Line = '' }
                try {
                    $res.R = Invoke-PatchRun $it.Path $it.Build @(Get-GuiChosen $it) $it.Out $mode $theme $g.Progress
                } catch {
                    $res.Error = $_.Exception.Message
                }
                $r = $res.R
                $name = $it.Build.ProductName
                $modeText = if ($mode) { " for $mode" + $(if ($theme) { " ($theme interface)" } else { '' }) } else { '' }
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
                        $res.Line = "$name - all $($r.Applied.Count) fixes$modeText, byte-identical to the reference build"
                    } elseif ($r.Complete) {
                        $res.Kind = 'warning'
                        $res.Line = "$name - all $($r.Applied.Count) fixes$modeText, but the SHA-256 differs from the reference build (please report it)"
                    } else {
                        $res.Line = "$name - $($r.Applied.Count) of $(@(Get-BuildPatches $it.Build $mode $theme).Count) fixes$modeText (" + (($r.Applied | ForEach-Object { $_.Id }) -join ', ') + ')'
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
        $exeResults = @($results | Where-Object { $_.Item })
        $ok = @($exeResults | Where-Object { $_.Kind -ne 'error' }).Count
        $c.Log.Text = "$ok of $($exeResults.Count) executable(s) patched" + $(if ($errors) { ", $errors failed" } else { '' }) + ' - see the message for details.'
        $text = ($results | ForEach-Object { $_.Line }) -join "`r`n`r`n"
        $skipped = @(& $g.SkippedLines)
        if ($skipped.Count -gt 0) { $text += "`r`n`r`nNot patched: " + ($skipped -join ', ') }
        if ($ok -gt 0) { $text += "`r`n`r`nStart the games with the desktop shortcuts or the files above." }
        $text += "`r`n`r`nDark Colony patcher $PatcherVersion, build $PatcherBuild"
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
        elseif ((Get-GuiMode $it.Build) -eq '640x480' -and @($it.Build.Modes).Count -gt 0) { $c.Log.ForeColor = 'Black'; $c.Log.Text = '640x480 (original) = the stock screen size: the display fix and the interface theme are not offered.' }
        else { $c.Log.Text = '' }
        if ($it.Patches.Count -eq 0 -or -not $it.Checked) { return }       # the info box already explains why (no choice yet, unticked)
        if ($u.List.SelectedIndex -lt 0 -and $u.List.Items.Count -gt 0) { $u.List.SelectedIndex = 0 }
        else { & $g.ShowFix $it }
    }

    # Shows wizard step $step: 0 welcome, 1 options, 2.. the executables with a page (Visible), Last = ready,
    # Last + 1 = finished.
    $script:gui.GoTo = {
        param([int] $step)
        $c = $script:gui.Controls
        $g = $script:gui
        & $g.Layout
        $vis = @($g.Visible)
        $last = $g.Last
        $base = $g.Base
        if ($step -gt $last + 1) { $step = $last + 1 }
        $g.Step = $step
        $c.Welcome.Visible = ($step -eq 0)
        $c.Discs.Visible = ($g.Disc -and $step -eq 1)
        $c.Options.Visible = ($step -eq $base)
        for ($i = 0; $i -lt $g.Items.Count; $i++) { $g.Items[$i].UI.Page.Visible = ($step -eq (& $g.StepOf $i)) }
        $c.Ready.Visible = ($step -eq $last)
        $c.Done.Visible = ($step -eq $last + 1)
        $c.Next.Enabled = $true
        if ($step -eq 0) {
            $c.Title.Text = 'Welcome to the Dark Colony patcher'
            $c.Sub.Text = 'Builds Dark Colony Ultimate, the Map Editor (and, if you ask for it, the deprecated Dark Colony) from the untouched originals - of this folder, or from your discs.'
        } elseif ($g.Disc -and $step -eq 1) {
            $c.Title.Text = "Step 1 of ${last}: Game discs"
            $c.Sub.Text = 'Your original Dark Colony and Council Wars discs (disc images or drives) and the folder to install the game into.'
            if ($g.DiscCw -and -not $c.DiscCw.Text) { $c.DiscCw.Text = $g.DiscCw }
            if ($g.DiscDc -and -not $c.DiscDc.Text) { $c.DiscDc.Text = $g.DiscDc }
            if ($g.DiscDir -and -not $c.DiscDir.Text) { $c.DiscDir.Text = $g.DiscDir }
            $c.Log.Text = ''
        } elseif ($step -eq $base) {
            $c.Title.Text = "Step $base of ${last}: Options"
            $c.Sub.Text = 'The screen resolution, the battlefield interface (light = classic, dark = console style) and the deprecated executable.  Nothing is preselected.'
            $hd = [bool] $g.Mode -and $g.Mode -ne '640x480'
            $c.Next.Enabled = [bool] $g.Mode -and (-not $hd -or [bool] $g.Theme)
            $c.Log.Text = ''
        } elseif ($step -lt $last) {
            $index = $vis[$step - $base - 1]
            $it = $g.Items[$index]
            $b = $it.Build
            $c.Title.Text = "Step $step of ${last}: $($b.ProductName)"
            $c.Sub.Text = "$($b.OriginalName) -> $($b.OutputName).  All fixes are selected; untick what you do not want, or untick the executable to leave it alone."
            & $g.Select $index
        } elseif ($step -eq $last) {
            $c.Title.Text = "Step $last of ${last}: Ready to patch"
            $c.Sub.Text = 'Check the summary; nothing has been written yet.  Press Patch to write the files.'
            $c.ReadyText.Text = (& $g.Summary) -join "`r`n"
            $c.ReadyText.SelectionStart = 0; $c.ReadyText.SelectionLength = 0; $c.ReadyText.ScrollToCaret()
            $c.Log.Text = ''
        } else {
            $r = @($g.Results)
            $errors = @($r | Where-Object { $_.Kind -eq 'error' }).Count
            $exes = @($r | Where-Object { $_.Item })
            $c.Title.Text = if ($errors -eq 0) { 'Finished' } elseif ($errors -lt $r.Count) { 'Finished, with errors' } else { 'Patching failed' }
            $c.Sub.Text = '{0} of {1} executable(s) patched.' -f @($exes | Where-Object { $_.Kind -ne 'error' }).Count, $exes.Count
            $skipped = @(& $g.SkippedLines)
            $c.DoneText.Text = (($r | ForEach-Object { $_.Line }) -join "`r`n`r`n") + $(if ($skipped.Count -gt 0) { "`r`n`r`nNot patched: " + ($skipped -join ', ') } else { '' })
            $c.DoneNote.Text = if ($errors -lt $r.Count) { 'Start the games with the desktop shortcuts or the files above.  Press Close to leave.' } else { 'Nothing usable was written - see above.  Press Close to leave.' }
            $c.Log.Text = ''
        }
        $c.Back.Enabled = ($step -gt 0 -and $step -le $last)
        $c.Next.Text = if ($step -eq $last) { 'Patch' } elseif ($step -eq $last + 1) { 'Close' } else { 'Next >' }
        $c.Cancel.Enabled = ($step -le $last)
    }
    $c.Next.Add_Click({
        $g = $script:gui
        $last = $g.Last
        if ($g.Disc -and $g.Step -eq 1) {
            if (-not (& $g.PrepareDiscs)) { $g.Controls.Log.ForeColor = 'Firebrick'; $g.Controls.Log.Text = 'The discs are not ready - see the message on the page.'; return }
        }
        if ($g.Step -eq $g.Base) {
            $hd = [bool] $g.Mode -and $g.Mode -ne '640x480'
            if (-not $g.Mode -or ($hd -and -not $g.Theme)) { $g.Controls.Log.ForeColor = 'Firebrick'; $g.Controls.Log.Text = 'Choose a screen resolution and a battlefield interface first.'; return }
        }
        if ($g.Step -lt $last) { & $g.GoTo ($g.Step + 1); return }
        if ($g.Step -eq $last) {
            $r = & $g.Apply $true
            if ($r) { $g.Results = $r; & $g.GoTo ($last + 1) }
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
            if ($g.Step -ge 2 -and $g.Step -lt $g.Last) { $c.Info.Text = $report; $c.List.ClearSelected() }
            else { & $g.Notify $report 'Inspect an exe' 'Information' }
        }
    })

    # the originals beside this script, ticked (the deprecated one not); then the exe given with -Original, if any
    foreach ($it in $script:gui.Items) {
        $p = if ($script:gui.Here) { Join-Path $script:gui.Here $it.Build.OriginalPath } else { $null }
        if ($p -and (Test-Path -LiteralPath $p)) { & $loadOriginal $p $false ([Array]::IndexOf($script:gui.Items, $it)) }
        else { & $script:gui.SetError $it $p $null 'missing'; & $script:gui.ShowRow $it; & $script:gui.FillItem $it }
    }
    if ($PreloadPath) { & $loadOriginal ((Resolve-Path $PreloadPath).Path) $false }
    # the installer package unpacked on its own (no game folder beside it): the disc install is the way, ticked by itself
    if (-not $PreloadPath -and @($script:gui.Items | Where-Object { $_.Data }).Count -eq 0) { & $script:gui.SetDiscMode $true }
    & $script:gui.Refresh
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

# --- command-line apply: one original (-Original), or - with -All and no -Original - the originals beside
# this script, each written under its own name (25 Sep 2026: "installer patches all in one shot")
function Invoke-CliBuild([string] $OriginalFile, [string] $OutputFile) {
    $origPath = (Resolve-Path $OriginalFile).Path
    $data = [System.IO.File]::ReadAllBytes($origPath)
    $sha = Get-Sha256Hex $data
    if (-not $script:BannerShown) { $script:BannerShown = $true; Write-Host ("Dark Colony patcher {0}, build {1} (generated {2})" -f $PatcherVersion, $PatcherBuild, $PatcherGenerated) -ForegroundColor Cyan; Write-Host '' }
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
    $mode = Resolve-Mode $build $Resolution
    $theme = Resolve-Theme $build $mode $Theme
    if ($mode) { Write-Host ("screen: {0}" -f (Format-ModeLabel $mode (Get-MonitorSize))) }
    if ($theme) { Write-Host ("theme : {0} battlefield interface" -f $theme) }

    $available = @(Get-BuildPatches $build $mode $theme)
    if (-not $OutputFile) { $OutputFile = Join-Path (Split-Path $origPath) $build.OutputName }
    $OutputFile = Get-AbsolutePath $OutputFile
    $gameDir = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputFile))
    $unavailable = Get-UnavailableFixes $build $gameDir $mode $theme
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
            if (-not $p) { throw "unknown patch id '$id' for $($build.Id) at this resolution and theme; valid: $(($available | ForEach-Object { $_.Id }) -join ', ')" }
            $chosen += $p
        }
    }
    if ((Test-Path $OutputFile) -and -not $Overwrite) { throw "output '$OutputFile' exists; pass -Overwrite to replace it" }
    if ((Test-Path $OutputFile) -and ((Resolve-Path $OutputFile).Path -eq $origPath)) { throw 'refusing to overwrite the original' }

    $problems = @(Get-DataProblems $build $chosen $gameDir $mode $theme)
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
    $r = Invoke-PatchRun $origPath $build $chosen $OutputFile $mode $theme
    Write-Host ''
    Write-Host ("output: {0}" -f $OutputFile)
    Write-Host ("        {0} bytes, SHA-256 {1}" -f $r.Size, $r.Sha256)
    foreach ($gl in @($r.Generated)) { Write-Host ("        " + $gl) }
    if ($r.Complete) {
        if ($r.Published) { Write-Host '        byte-identical to the executable published in the repository.' -ForegroundColor Green }
        elseif ($r.Matches) { Write-Host ("        byte-identical to the reference build for {0}{1} (every fix of that resolution{2})." -f $mode, $(if ($theme) { ", $theme interface" } else { '' }), '') -ForegroundColor Green }
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
# --- the disc install from the command line (5 Oct 2026): copy the game from the two discs into -InstallDir, then
# patch Dark Colony Ultimate and the map editor there (like the window with its checkbox ticked)
if ($InstallDir -or $CouncilWarsDisc -or $DarkColonyDisc) {
    if (-not ($InstallDir -and $CouncilWarsDisc -and $DarkColonyDisc)) { throw '-InstallDir, -CouncilWarsDisc and -DarkColonyDisc belong together: the folder to install into and the two disc images (or drives)' }
    if (-not $All) { throw 'the disc install takes -All (every fix whose resources are there), together with -Resolution and -Theme' }
    $games = @($Builds | Where-Object { @($_.Modes).Count -gt 0 })
    $m0 = Resolve-Mode $games[0] $Resolution; [void] (Resolve-Theme $games[0] $m0 $Theme)
    Write-Host ("Dark Colony patcher {0}, build {1} (generated {2})" -f $PatcherVersion, $PatcherBuild, $PatcherGenerated) -ForegroundColor Cyan; $script:BannerShown = $true
    Write-Host ''
    Write-Host ('=== Installing the game from the discs into {0}' -f $InstallDir) -ForegroundColor Cyan
    Write-Host ("Council Wars disc: {0}`r`nDark Colony disc:  {1}" -f $CouncilWarsDisc, $DarkColonyDisc)
    $InstallDir = Get-AbsolutePath $InstallDir
    [void] [System.IO.Directory]::CreateDirectory($InstallDir)
    $pre = Install-DiscOriginals $CouncilWarsDisc $DarkColonyDisc $InstallDir       # checks both discs, the two exes, the soundtrack + encoder
    $script:DiscInstall = @{ Dir = $InstallDir; Cw = $CouncilWarsDisc; Dc = $DarkColonyDisc; Audio = [bool] $pre['Audio'] }
    if (-not $pre['Audio']) { Write-Warning ('no soundtrack from these discs: ' + $pre['AudioNote'] + ' - fix music is left out') }
    $dr = Install-GameFromDiscs $CouncilWarsDisc $DarkColonyDisc $InstallDir { param([string] $s) Write-Host ('  ' + $s) -ForegroundColor DarkGray }
    Write-Host ('{0} files copied ({1} MB), {2} already there' -f $dr.Files, [int][Math]::Round($dr.Bytes / 1MB), $dr.Skipped)
    foreach ($ml in @($dr.Music)) { if ($ml -match 'NOT written') { Write-Warning $ml } else { Write-Host ('music: ' + $ml) } }
    if (@($dr.Missing).Count -gt 0) { Write-Warning ('{0} file(s) are not on your discs (another pressing?): {1}' -f @($dr.Missing).Count, (@($dr.Missing | Select-Object -First 10) -join ', ')) }
    $failed = 0; $done = 0
    foreach ($o in $DiscOriginals) {
        $b = $Builds | Where-Object { $_.Id -eq $o.Build }
        $p = Join-Path (Get-InstallRoot $InstallDir $o.Root) $o.To
        Write-Host ''
        Write-Host ('=== {0}  ({1} -> {2})' -f $b.ProductName, $o.To, $b.OutputName) -ForegroundColor Cyan
        try { Invoke-CliBuild $p $null; $done++ } catch { Write-Warning ('{0}: {1}' -f $b.ProductName, $_.Exception.Message); $failed++ }
    }
    Write-Host ''
    Write-Host ('{0} executable(s) patched, {1} failed.' -f $done, $failed)
    if ($failed -gt 0 -or $done -eq 0) { exit 1 }
    return
}
if ($Patches) { throw 'give -Original <exe> together with -Patches (the fix ids differ per executable)' }
if ($Output) { throw '-Output needs -Original (with -All alone each executable is written under its own name beside its original)' }
# the screen resolution and the battlefield interface are chosen explicitly (1 Oct 2026): checked once here,
# before anything is written
$games = @($Builds | Where-Object { @($_.Modes).Count -gt 0 })
if ($games.Count -gt 0) { $m0 = Resolve-Mode $games[0] $Resolution; [void] (Resolve-Theme $games[0] $m0 $Theme) }
$failed = 0; $done = 0
foreach ($b in $Builds) {
    $p = Join-Path (Split-Path -Parent $PSScriptRoot) $b.OriginalPath      # the repository root = the parent of patcher\
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

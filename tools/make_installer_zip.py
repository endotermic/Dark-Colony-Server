"""Build the installer package for ModDB (5 Oct 2026): INSTALL.CMD + PATCH_HOWTO.TXT + patcher\\ of the Dark-Colony
repository, zipped - nothing of the game itself in it (the player installs the game from the original discs, or
points the installer at an existing game folder).

    python tools/make_installer_zip.py GAME_REPO [OUT_DIR]

writes OUT_DIR/Dark-Colony-Ultimate-installer-<version>.zip (OUT_DIR default: the scratch folder given, else the
current folder) and prints its contents by top-level folder and a check that no stock game file slipped in (every
file must be tracked in the repository under patcher/, INSTALL.CMD or PATCH_HOWTO.TXT - the working copy may hold
untracked leftovers).  The version is read from the patcher script's header."""
import os, re, subprocess, sys, zipfile

repo = os.path.abspath(sys.argv[1])
out_dir = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.getcwd()
script = os.path.join(repo, 'patcher', 'Apply-DarkColonyPatches.ps1')
head = open(script, encoding='utf-8').read(2000)
m = re.search(r'Dark Colony patcher (\S+), build (\S+)', head)
version, build = (m.group(1), m.group(2)) if m else ('x', 'x')
tracked = set(subprocess.run(['git', '-C', repo, 'ls-files', '-z', '--', 'patcher', 'INSTALL.CMD', 'PATCH_HOWTO.TXT'],
                             capture_output=True, check=True).stdout.decode('utf-8').split('\0')) - {''}
missing = [t for t in tracked if not os.path.exists(os.path.join(repo, t))]
if missing:
    sys.exit('tracked but not on disk: %s' % missing[:5])
name = 'Dark-Colony-Ultimate-installer-%s.zip' % version
os.makedirs(out_dir, exist_ok=True)
path = os.path.join(out_dir, name)
sizes = {}
with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for t in sorted(tracked):
        full = os.path.join(repo, t)
        z.write(full, t)
        top = t.split('/')[0] if '/' not in t else '/'.join(t.split('/')[:2])
        sizes[top] = sizes.get(top, 0) + os.path.getsize(full)
print('%s: %d files, %.1f MB zipped (patcher %s, build %s)' % (path, len(tracked), os.path.getsize(path) / 1048576, version, build))
for k, v in sorted(sizes.items()):
    print('  %8.1f MB  %s' % (v / 1048576, k))

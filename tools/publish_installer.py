"""Publish the installer package into the Dark-Colony-Ultimate repository (5 Oct 2026, maintainer: "create github repo
'Dark-Colony-Ultimate' and put there installer with it's resources as we decided earlier for ModDB").

    python tools/publish_installer.py GAME_REPO ULTIMATE_REPO

copies the package - exactly the files tracked in GAME_REPO under INSTALL.CMD, PATCH_HOWTO.TXT and patcher/ (the same
set tools/make_installer_zip.py zips) - into the working copy of the Dark-Colony-Ultimate repository, removes files
there that the package no longer has (under patcher/ only), and leaves the repository's own files alone (README.md,
.gitattributes, LICENSE, .git).  It stages nothing: review `git status` there, then commit and push.

The Dark-Colony-Ultimate repository holds nothing of the game itself: the player installs the game from the two
original discs (the installer's "Game discs" page) or points the installer at an existing game folder.
"""
import os, shutil, subprocess, sys

game, ult = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
if not os.path.isdir(os.path.join(ult, '.git')):
    sys.exit('%s is not a git working copy' % ult)
tracked = [t for t in subprocess.run(['git', '-C', game, 'ls-files', '-z', '--', 'INSTALL.CMD', 'PATCH_HOWTO.TXT', 'patcher'],
                                     capture_output=True, check=True).stdout.decode('utf-8').split('\0') if t]
copied = same = 0
want = set()
for t in tracked:
    src = os.path.join(game, t); dst = os.path.join(ult, t)
    want.add(os.path.normcase(os.path.abspath(dst)))
    data = open(src, 'rb').read()
    if os.path.exists(dst) and open(dst, 'rb').read() == data:
        same += 1; continue
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst); copied += 1
removed = 0
for dp, dns, fs in os.walk(os.path.join(ult, 'patcher')):
    for f in fs:
        p = os.path.normcase(os.path.abspath(os.path.join(dp, f)))
        if p not in want:
            os.remove(p); removed += 1
            print('removed', os.path.relpath(p, ult))
print('%d files in the package: %d copied, %d already equal, %d stale files removed -> %s' % (len(tracked), copied, same, removed, ult))

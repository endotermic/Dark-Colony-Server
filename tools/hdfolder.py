"""The folder a resolution's interface set lives in (2 Oct 2026, maintainer: "absolutely isolate files for
different resolutions to their own folders, so resources are never mixed").

The patched exe opens its interface data through 30 path strings whose 8-byte directory part is rewritten
in place (patch_hd_paths.py: `intrface/bintro` -> `hd_1080p/bintro`), so a folder name has exactly 8
characters: `HD_<height>P` - HD_0768P (1024x768), HD_0720P, HD_0800P, HD_1024P, HD_1080P, HD_1200P - and
`UW_<height>P` for the ultra-wide sizes (UW_1080P = 3840x1080, UW_1440P = 5120x1440), whose heights would
otherwise clash with the 16:9 sizes.  The exe strings and the scripts' `background` lines use the lower-case
form (`hd_0768p/intrg`).  The patcher's INPUTS - the five shipped pictures per size and the two console-style
banks - live in HD_SRC (`HD_SRC\\1920x1080\\INTRG.GIF`, `HD_SRC\\MAINBUT.SPR`; scripts say `pictures
hd_src/mainbut`).  Until 1 Oct 2026 every size shared one folder, INTRF_HD, which is how a player ended up
with a HUD script of one size under the frame of another (doc 10.59); the patcher now also deletes every
other size's folder when it writes a set (doc 10.61)."""
import re

SRC_DIR = 'HD_SRC'
SRC_TOKEN = 'hd_src'
LEGACY_DIR = 'INTRF_HD'
FOLDER_RE = re.compile(r'^(?:HD|UW)_\d{4}P$', re.I)
TOKEN_RE_BYTES = rb'(?:hd|uw)_[0-9]{4}p'          # the lower-case folder in a path string / script line


def hd_folder(width, height):
    """'HD_0768P' for 1024x768, 'UW_1080P' for 3840x1080."""
    return '%s_%04dP' % ('UW' if width * 2 > height * 5 else 'HD', height)


def hd_token(width, height):
    """The folder as the exe strings and the scripts carry it: 'hd_0768p'."""
    return hd_folder(width, height).lower()


def is_hd_folder(name, legacy=True):
    """A per-resolution folder name (or the pre-October INTRF_HD when legacy is set)."""
    return bool(FOLDER_RE.match(name)) or (legacy and name.upper() == LEGACY_DIR)


def find_hd_folder(parent, legacy=True):
    """The per-resolution folder inside `parent` (a game folder, exp\\, ...), or None."""
    import os
    if not os.path.isdir(parent):
        return None
    hits = sorted(n for n in os.listdir(parent) if is_hd_folder(n, legacy) and os.path.isdir(os.path.join(parent, n)))
    return os.path.join(parent, hits[0]) if hits else None

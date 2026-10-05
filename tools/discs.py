"""The two original discs as the installer's source of the game files (4-5 Oct 2026, maintainer: "installer - add a
checkbox that adds a form to select both discs and installation directory").

The ModDB installer package carries no file of Strategic Simulations' (the repository ships the whole game, the
package must not), so the installer rebuilds the game folder from the player's own discs: the Dark Colony CD
(volume DCUK, the `/DC/` tree = the installed game, 1997 build) and the Council Wars CD (volume COUNCILWARS:
`/DC/` = the Classic data the expansion shares, `/EXPENG/` = ENGEXP16.EXE and the `exp/` overlay).  Neither disc
alone holds everything the shared game folder of the repository needs: the Council Wars disc lacks SCENARIO,
GAMESTAT, CURSOR, MISSION and ENCYCLO, the Dark Colony disc lacks the expansion.  Both are mixed-mode CDs (track 1
data, tracks 2-5 audio); their `.bin` images are raw 2352-byte MODE1 sectors, `.iso` conversions are 2048-byte
sectors.  ISO 9660 level 1, upper-case 8.3 names with `;1` versions, no Joliet.

    python tools/discs.py list IMAGE [--out files.tsv]
        every file of the image with LBA, size and MD5 (an image, a .cue, a mounted drive or a folder)
    python tools/discs.py manifest CW_IMAGE DC_IMAGE GAME_REPO [--out tools/disc_manifest.json]
        for every tracked file of the game repository's two game folders: which disc path provides it, or
        that it is a patcher output, a repository-only file or one of the project's own resources
        (tools/resources.py reads the result; the generator embeds the disc entries in the patcher)
    python tools/discs.py check INSTALL_DIR GAME_REPO [--manifest tools/disc_manifest.json]
        compares an installed folder with the repository: every manifest entry present and identical

The manifest is committed (tools/disc_manifest.json) so that the patcher can be regenerated without the images;
rebuild it when the game repository gains or loses stock files.
"""
import argparse, hashlib, json, os, re, struct, subprocess, sys

SYNC = b'\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00'


class Disc:
    """An ISO 9660 image (.iso / raw .bin / .cue) or a folder (a mounted disc, or an extracted copy).  `files`
    maps the upper-case absolute path ('/DC/ANIM.DAT') to (lba, size); folders map to (path, size)."""

    def __init__(self, path):
        self.path = path
        self.files = {}
        self.volume = ''
        if os.path.isdir(path):
            self.f = None
            root = path.rstrip('\\/')
            for dp, _, fs in os.walk(root):
                for n in fs:
                    full = os.path.join(dp, n)
                    rel = '/' + os.path.relpath(full, root).replace(os.sep, '/')
                    self.files[rel.upper()] = (full, os.path.getsize(full))
            return
        if path.lower().endswith('.cue'):
            text = open(path, 'r', encoding='latin-1').read()
            m = re.search(r'^\s*FILE\s+"([^"]+)"', text, re.M) or re.search(r'^\s*FILE\s+(\S+)', text, re.M)
            if not m:
                raise ValueError('no FILE line in ' + path)
            path = os.path.join(os.path.dirname(os.path.abspath(path)), m.group(1))
        self.f = open(path, 'rb')
        head = self.f.read(16)
        if head[:12] == SYNC:
            mode = head[15]
            self.ss, self.off = 2352, (16 if mode == 1 else 24)
        else:
            self.ss, self.off = 2048, 0
        pvd = self.sector(16)
        if pvd[1:6] != b'CD001':
            raise ValueError('%s: no ISO 9660 volume descriptor at sector 16' % path)
        self.volume = pvd[40:72].decode('ascii', 'replace').strip()
        root_lba = struct.unpack('<I', pvd[158:162])[0]
        root_len = struct.unpack('<I', pvd[166:170])[0]
        self._walk(root_lba, root_len, '')

    def sector(self, lba):
        self.f.seek(lba * self.ss + self.off)
        return self.f.read(2048)

    def _walk(self, lba, length, prefix):
        data = self.read_extent(lba, length)
        pos = 0
        while pos < len(data):
            ln = data[pos]
            if ln == 0:
                pos = (pos // 2048 + 1) * 2048
                continue
            rec = data[pos:pos + ln]
            pos += ln
            ext = struct.unpack('<I', rec[2:6])[0]
            size = struct.unpack('<I', rec[10:14])[0]
            flags = rec[25]
            nl = rec[32]
            name = rec[33:33 + nl]
            if name in (b'\x00', b'\x01'):
                continue
            name = name.decode('ascii', 'replace').split(';')[0].rstrip('.')
            full = prefix + '/' + name
            if flags & 2:
                self._walk(ext, size, full)
            else:
                self.files[full.upper()] = (ext, size)

    def read_extent(self, lba, size):
        if self.ss == 2048:
            self.f.seek(lba * 2048)
            return self.f.read(size)
        n = (size + 2047) // 2048
        self.f.seek(lba * self.ss)
        raw = self.f.read(n * self.ss)
        return b''.join(raw[i * self.ss + self.off:i * self.ss + self.off + 2048] for i in range(n))[:size]

    def read(self, disc_path):
        ent = self.files[disc_path.upper()]
        if self.f is None:
            return open(ent[0], 'rb').read()
        return self.read_extent(*ent)

    def has(self, disc_path):
        return disc_path.upper() in self.files

    def size(self, disc_path):
        return self.files[disc_path.upper()][1]


def md5(data):
    return hashlib.md5(data).hexdigest()


# --- what a tracked file of the game repository is -------------------------------------------------------------
GAME_FOLDER = 'DC - Council wars'
EDITOR_FOLDER = 'Dark Colony - Map editor'
# (ozisave/ozisave.txt: the OZI save folder's marker is created by the patcher from scratch, never carried as a
# resource - maintainer, 5 Oct 2026)
OUTPUT_RE = re.compile(r'(?i)^(?:(?:exp|dc|ozi_ns)/)?(?:HD|UW)_\d{4}P/|^Dark Colony( Ultimate| Map Editor)?\.exe$|^INTRFACE/(ONLINE|ONLINEBG\.GIF|REPLAYE|REPLAYBG\.GIF|LOADALLE)$|^ozisave/ozisave\.txt$')
# repository-only files: not on a disc, not the project's resources, never in the installer package
LOCAL_RE = re.compile(r'(?i)^(?:MUSIC/|exp/music/).*\.mp3$|^dc16\.exe$|^DeIsL\d\.isu$|^readme\.doc$|^ERROR\.LOG$|^HBNFUFL\.A0[12]$|^hbnfufl\.a01$')
# files the installer derives: the 1998 update's typo fix in mission 9's trigger file (the disc has the 1997 text)
DERIVED = {'SCENARIO/HUMAN/human09.tro': 'tro_typo'}


def natural_sources(root, rel):
    """Where a repository file would sit on the discs (in order of preference)."""
    low = rel.lower()
    if root == 'editor':
        if low.startswith('scenario/'):
            return [('dc', '/DC/SCENARIO/' + rel[len('scenario/'):])]
        return [('dc', '/DC/' + rel)]
    if low.startswith('exp/'):
        return [('cw', '/EXPENG/EXP/' + rel[4:]), ('cw', '/EXPENG/' + rel[4:])]
    return [('dc', '/DC/' + rel), ('cw', '/DC/' + rel), ('cw', '/EXPENG/' + rel)]


def build_manifest(cw, dc, game_repo):
    discs = {'cw': cw, 'dc': dc}
    by_md5 = {}
    disc_md5 = {}        # (disc, upper path) -> md5, every file of the /DC/ and /EXPENG/ trees read once
    for key, d in discs.items():
        for p, (lba, size) in d.files.items():
            if p.split('/')[1] not in ('DC', 'EXPENG'):
                continue
            h = md5(d.read(p)) if size else md5(b'')
            disc_md5[(key, p)] = h
            by_md5.setdefault(h, []).append((key, p))
    files = subprocess.run(['git', '-C', game_repo, 'ls-files', '-z', '--', GAME_FOLDER, EDITOR_FOLDER],
                           capture_output=True, check=True).stdout.decode('utf-8').split('\0')
    entries, outputs, local, resources, derived = [], [], [], [], []
    for f in files:
        if not f:
            continue
        root = 'game' if f.startswith(GAME_FOLDER + '/') else 'editor'
        rel = f.split('/', 1)[1]
        if OUTPUT_RE.search(rel):
            outputs.append((root, rel)); continue
        if LOCAL_RE.search(rel):
            local.append((root, rel)); continue
        if rel in DERIVED:
            derived.append((root, rel, DERIVED[rel])); continue
        data = open(os.path.join(game_repo, f), 'rb').read()
        h = md5(data)
        src = None
        for key, p in natural_sources(root, rel):
            if disc_md5.get((key, p.upper())) == h:
                src = (key, p, False); break
        if src is None and rel.lower().endswith(('.ovh', '.o16')):
            for key, p in natural_sources(root, rel):           # a game-written minimap cache: the disc's copy will do
                if discs[key].has(p):
                    src = (key, p, True); break
        if src is None and h in by_md5:
            key, p = by_md5[h][0]
            src = (key, p, False)
        if src is None:
            resources.append((root, rel)); continue
        key, p, differs = src
        ent = {'root': root, 'to': rel, 'disc': key}
        nat = natural_sources(root, rel)[0]
        if (key, p.upper()) != (nat[0], nat[1].upper()):
            ent['from'] = p
        if differs:
            ent['differs'] = True
        entries.append(ent)
    return {
        'note': 'generated by tools/discs.py manifest: which disc path provides each tracked file of the game folders',
        'discs': {k: {'volume': d.volume, 'files': len(d.files)} for k, d in discs.items()},
        'files': entries,
        'derived': [{'root': r, 'to': t, 'rule': rule} for r, t, rule in derived],
        'outputs': ['%s:%s' % x for x in outputs],
        'local': ['%s:%s' % x for x in local],
        'resources': ['%s:%s' % x for x in resources],
    }


def source_of(ent):
    """The disc path of a manifest entry ('from' or the natural one)."""
    return ent.get('from') or natural_sources(ent['root'], ent['to'])[0][1]


def fix_tro_typo(data):
    """The January 1998 update's repair of mission 9's trigger file: `&&==` -> `==` (DC Patch 1.01)."""
    return data.replace(b'&&==', b'==')


def cmd_list(a):
    d = Disc(a.image)
    rows = []
    for p in sorted(d.files):
        size = d.files[p][1]
        rows.append((p, d.files[p][0] if d.f else 0, size, md5(d.read(p)) if size else md5(b'')))
    out = open(a.out, 'w', encoding='utf-8') if a.out else sys.stdout
    for r in rows:
        out.write('%s\t%s\t%d\t%s\n' % r)
    print('%s: volume %r, %d files' % (a.image, d.volume, len(rows)), file=sys.stderr)


def cmd_manifest(a):
    m = build_manifest(Disc(a.cw), Disc(a.dc), a.game_repo)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(m, f, indent=1)
    print('%d disc files, %d derived, %d outputs, %d repository-only, %d resources -> %s'
          % (len(m['files']), len(m['derived']), len(m['outputs']), len(m['local']), len(m['resources']), a.out))
    for r in m['resources']:
        print('  resource', r)


def cmd_check(a):
    m = json.load(open(a.manifest, encoding='utf-8'))
    roots = {'game': a.install_dir, 'editor': os.path.join(a.install_dir, 'Map editor')}
    repo = {'game': os.path.join(a.game_repo, GAME_FOLDER), 'editor': os.path.join(a.game_repo, EDITOR_FOLDER)}
    missing, differ, ok = [], [], 0
    for ent in m['files']:
        p = os.path.join(roots[ent['root']], ent['to'])
        if not os.path.exists(p):
            missing.append(p); continue
        if ent.get('differs'):
            ok += 1; continue
        a_ = open(p, 'rb').read(); b_ = open(os.path.join(repo[ent['root']], ent['to']), 'rb').read()
        if a_ == b_:
            ok += 1
        else:
            differ.append(p)
    print('%d identical, %d missing, %d differ' % (ok, len(missing), len(differ)))
    for x in missing[:20]:
        print('  missing', x)
    for x in differ[:20]:
        print('  differs', x)
    return 1 if missing or differ else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('list'); p.add_argument('image'); p.add_argument('--out')
    p = sub.add_parser('manifest'); p.add_argument('cw'); p.add_argument('dc'); p.add_argument('game_repo')
    p.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'disc_manifest.json'))
    p = sub.add_parser('check'); p.add_argument('install_dir'); p.add_argument('game_repo')
    p.add_argument('--manifest', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'disc_manifest.json'))
    a = ap.parse_args(argv)
    return {'list': cmd_list, 'manifest': cmd_manifest, 'check': cmd_check}[a.cmd](a) or 0


if __name__ == '__main__':
    sys.exit(main())

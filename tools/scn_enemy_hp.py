#!/usr/bin/env python3
r"""Start every enemy object of a campaign `.scn` at a fraction of its hit points (default 75 %).

Why (20 Oct 2026, maintainer: "ozi missions. first human mission must have all enemies with 75% of
they health by default"): the first human mission of the OZI pack, `ozi_ns/scenario/globo/globo01.scn`
(RETURN TO MARS), is to start with every enemy unit and structure at three quarters of its health.

How (DC16_MAP_FILES.md section 6.2): an object line is `x z type player a b`; the scenario loader
0x41BAF0 passes `a` to initObject 0x41B124 as the start hit points - `cmp ebx,-1; jle default`, so -1
(every stock line) means the type's hit points from column 12 of GAMESTAT.TXT and any other value is
the start value as is.  The tool rewrites `a` for every object whose owner is an enemy of the player's
team: a team in 0..7 other than the player's whose TeamAllies row does not ally it with the player
(the loader sets ally(i, j) both ways, so a flag on either side counts).  Vents (type 40: the column is
the eruption rate), artifact sites (37) and creature generators (player -1) are not objects of a team
and are left alone, as are the player's own and allied objects.  The default hit points come from the
pack's own GAMESTAT.TXT (`<scenario dir>/../../gamestat/gamestat.txt`, 126 types for the OZI pack);
when the type's race differs from the owner's race and column 31 names a counterpart, the loader
re-types the object (0x41C683), so the counterpart's hit points are the default.  A line that already
carries an explicit value (the pack sets 500 on the 21 incubators and 5 on three TORT structures of
globo01) is scaled from that value.  Results round to the nearest whole point, never below 1.

Only the sixth number of the affected lines changes: every other byte of the file - the `%Label`
comments, blank lines, the line ending (globo01.scn uses LF) - is kept.  `apply` writes `<file>.hp.bak`
first and refuses a file where no enemy object still holds -1 (already applied) unless --force; it
also refuses to write when the gamestat has no row for a type.

Units a trigger script (`.tro` reinforce / reinforce2) brings in during the mission are created at
run time with -1 and are not affected; a save game stores the live hit points.

CLI
    python tools/scn_enemy_hp.py verify "../Dark-Colony/DC - Council wars/ozi_ns/scenario/globo/globo01.scn"
    python tools/scn_enemy_hp.py plan   SCN [--percent 75] [--player 0] [--gamestat FILE]
    python tools/scn_enemy_hp.py apply  SCN [--percent 75] [--player 0] [--gamestat FILE] [--force]
"""

import argparse
import os
import re
import shutil
import sys

VENT, SITE = 40, 37
HP_COL, RACE_COL, COUNTERPART_COL = 11, 0, 30   # 0-based indexes into the 32 integers after the name


def read_gamestat(path):
    """{type index: (race, hp, counterpart)} from GAMESTAT.TXT (loader 0x43BB80: `%s` + 32 x `%d`)."""
    lines = [l for l in open(path, 'rb').read().decode('latin-1').splitlines()
             if l.strip() and not l.lstrip().startswith('%')]
    count = int(lines[0].split()[0])
    types = {}
    for i, l in enumerate(lines[1:1 + count]):
        cols = [int(x) for x in l.split()[1:33]]
        if len(cols) < 32:
            raise SystemExit('%s: type %d has %d columns, expected 32' % (path, i, len(cols)))
        types[i] = (cols[RACE_COL], cols[HP_COL], cols[COUNTERPART_COL])
    return types


def default_gamestat(scn):
    """The pack's gamestat/gamestat.txt (ozi_ns/, exp/) or the game root's GAMESTAT/GAMESTAT.TXT."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(scn))))
    for cand in (os.path.join(root, 'gamestat', 'gamestat.txt'), os.path.join(root, 'GAMESTAT', 'GAMESTAT.TXT')):
        for dp, _, fs in os.walk(os.path.dirname(cand)) if os.path.isdir(os.path.dirname(cand)) else []:
            for f in fs:
                if f.lower() == os.path.basename(cand).lower():
                    return os.path.join(dp, f)
            break
    raise SystemExit('no gamestat.txt found under %s; pass --gamestat' % root)


OBJ_RE = re.compile(r'^(\s*-?\d+\s+-?\d+\s+-?\d+\s+)(-?\d+)(\s+)(-?\d+)(\s+-?\d+)?(\s*)$')


def parse_scn(data):
    """(lines, teams, first object line index).  teams[i] = {'race', 'allies': set}.  Line reader
    0x41BA3C skips blank lines and `%` lines; the 8 TEAM blocks are read in a fixed order (section 6.1)."""
    lines = data.split('\n')
    sig = [(n, l.strip()) for n, l in enumerate(lines) if l.strip() and not l.strip().startswith('%')]
    teams, pos = {}, 0
    # header: tileset, base name, display name, type/active, phase, length, counter, fade
    pos = 8
    for i in range(8):
        n, l = sig[pos]
        if not (l.split()[0] == 'TEAM' and int(l.split()[1]) == i):
            raise SystemExit('line %d: expected "TEAM %d", found %r' % (n + 1, i, l))
        race = int(sig[pos + 1][1].split()[0])
        # race, money, ai, colour, depend list (one line), allies, aislots, start, city, pairs, 8 rows
        allies = {j for j, f in enumerate(sig[pos + 6][1].split()[:8]) if int(f) != 0}
        teams[i] = {'race': race, 'allies': allies}
        pos += 1 + 4 + 1 + 1 + 1 + 1 + 1 + 1 + 8
    return lines, teams, sig[pos][0]


def enemies_of(teams, player):
    out = set()
    for i in range(8):
        if i == player:
            continue
        if player in teams[i]['allies'] or i in teams[player]['allies']:
            continue
        out.add(i)
    return out


def plan_edits(scn, gamestat, percent, player):
    data = open(scn, 'rb').read().decode('latin-1')
    lines, teams, first = parse_scn(data)
    types = read_gamestat(gamestat)
    enemy = enemies_of(teams, player)
    edits, pending, missing = [], 0, set()
    for n in range(first, len(lines)):
        m = OBJ_RE.match(lines[n])
        if not m:
            continue
        x, z, t = [int(v) for v in m.group(1).split()]
        team, a = int(m.group(2)), int(m.group(4))
        if team not in enemy or t in (VENT, SITE):
            continue
        if t not in types:
            missing.add(t)
            continue
        race, hp, cp = types[t]
        eff = t
        if race != teams[team]['race'] and cp != -1 and cp in types:
            eff, (race, hp, cp) = cp, types[cp]
        base = a if a > -1 else hp
        new = max(1, int(base * percent / 100.0 + 0.5))
        if a == -1:
            pending += 1
        if new != a:
            edits.append((n, x, z, t, eff, team, a, base, new))
    return data, lines, teams, enemy, edits, pending, missing


def rewrite(lines, edits):
    for n, *_rest, new in edits:
        m = OBJ_RE.match(lines[n])
        lines[n] = m.group(1) + m.group(2) + m.group(3) + str(new) + (m.group(5) or '') + m.group(6)
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=('verify', 'plan', 'apply'))
    ap.add_argument('scn')
    ap.add_argument('--percent', type=float, default=75.0, help='start hit points in percent of the default (75)')
    ap.add_argument('--player', type=int, default=0, help="the human player's team (0)")
    ap.add_argument('--gamestat', help="the pack's GAMESTAT.TXT (default: found from the scenario folder)")
    ap.add_argument('--force', action='store_true', help='apply even when no enemy object holds -1 any more')
    a = ap.parse_args(argv)
    gamestat = a.gamestat or default_gamestat(a.scn)
    data, lines, teams, enemy, edits, pending, missing = plan_edits(a.scn, gamestat, a.percent, a.player)
    print('%s: player team %d, enemy teams %s, gamestat %s' % (a.scn, a.player, sorted(enemy), gamestat))
    if missing:
        print('ERROR gamestat has no row for type(s) %s' % sorted(missing))
    if a.mode == 'verify':
        print('%d enemy object lines still at -1 (default hit points): %s'
              % (pending, 'NOT applied' if pending else 'applied'))
        return 1 if pending or missing else 0
    for n, x, z, t, eff, team, old, base, new in edits:
        print('line %4d: team %d type %3d%s at (%3d,%3d)  hp %5d -> %d  (%g %% of %d)'
              % (n + 1, team, t, '' if eff == t else '->%d' % eff, x, z, old, new, a.percent, base))
    print('%d edits' % len(edits))
    if a.mode == 'plan' or missing:
        return 1 if missing else 0
    if not edits:
        print('nothing to do'); return 0
    if not pending and not a.force:
        print('refusing: no enemy object holds -1, the file looks already applied (use --force to rescale)')
        return 1
    bak = a.scn + '.hp.bak'
    if not os.path.exists(bak):
        shutil.copy2(a.scn, bak)
    out = rewrite(lines, edits)
    open(a.scn, 'wb').write(out.encode('latin-1'))
    print('written %s (backup %s)' % (a.scn, bak))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
r"""OZI pack, third alien mission `council/tarr03`: the first drop pod brings a harvester (25 Oct 2026,
maintainer: "ozi missions: aliens 3. our first dropship must deliver harverster").

Trigger 1 of tarr03.tro (the player enters its area) drops `reinforce 0 15 64 9 4 12 1 8 4`: a pod for
team 0 at (15,64) with 4 XENO (9), 1 PSYC (12) and 4 GRAY (8) - and no SLUG (14), the alien harvester,
although the briefing sends the player to a base there to gather an army (money 550, city at (7,63),
`exomoney 0 4` in the same trigger).  A `reinforce` carries up to five (type, count) pairs; pairs not
written are zero (the parser 0x43E738 reads what is there, see the engine port's parseAction), and the
pack itself ends another pod with `14 1` (`reinforce 0 9 71 75 1 8 6 10 5 14 1`).  The fix appends the
pair `14 1` to that one line; the pod then unloads one SLUG after the nine fighters (state 0x15 drops
one unit of the current entry per free landing tile).  Nothing else in the file changes.

CLI
    python tools/fix_tarr03_harvester.py verify "../Dark-Colony/DC - Council wars/ozi_ns/scenario/council"
    python tools/fix_tarr03_harvester.py plan   DIR
    python tools/fix_tarr03_harvester.py apply  DIR      (writes tarr03.tro.harvester.bak)
"""

import argparse
import os
import shutil
import sys

OLD = b'\nreinforce 0 15 64 9 4 12 1 8 4\n'
NEW = b'\nreinforce 0 15 64 9 4 12 1 8 4 14 1\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=('verify', 'plan', 'apply'))
    ap.add_argument('dir', help='the ozi_ns/scenario/council folder')
    a = ap.parse_args(argv)
    tro = os.path.join(a.dir, 'tarr03.tro')
    d = open(tro, 'rb').read()
    state = 'stock' if d.count(OLD) == 1 and NEW not in d else 'patched' if d.count(NEW) == 1 and OLD not in d else 'unknown'
    print('%s: %s' % (tro, state))
    if a.mode == 'verify':
        return 0 if state == 'patched' else 1
    if state == 'unknown':
        print('refusing: neither stock nor patched'); return 1
    print('tro: %r -> %r%s' % (OLD.strip(), NEW.strip(), '' if state == 'stock' else '  (done)'))
    if a.mode == 'plan' or state == 'patched':
        return 0
    shutil.copy2(tro, tro + '.harvester.bak')
    open(tro, 'wb').write(d.replace(OLD, NEW, 1)); print('written', tro)
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
r"""OZI pack, second alien mission `council/tarr02` (THE ROAD TO FREEDOM): make the allied captain's escorted
walk to the rebel camp complete (20 Oct 2026, maintainer's report: "allied human lieutenant is stuck in half
way", then "patrolling near the mine field ... player units are passing over but this is not triggering
lieutenant to move to the next waypoint").

The captain is the team 5 human lieutenant at (9,4).  The trigger script moves him in four legs, each a
`waypoint` action that gives "the first object on the tile" (0x43E183, all 800 records) an endless patrol
between two tiles (order 9): trigger 5 (29,29)-(31,29) by the mine field, trigger 7 (44,51)-(44,49),
trigger 21 (87,79)-(89,79), trigger 23 (104,71)-(104,67) in the rebel camp; trigger 9 (`trip 1 (S==5)`:
the captain enters its area) sends the rebels away and re-allies them with the player.

Two faults, both found in the data (DC16_MAP_FILES.md section 11):

1. Triggers 7, 21 and 23 are `trip 0 (S==0)`: armed with 99 lives by trigger 5, fired only by a PLAYER
   (team 0) unit stepping into their area, and the trip entry 0x43E610 spends a life on every such step
   whether or not the captain stands on one of the three pickup tiles at that moment.  Their areas are the
   corridors the escort walks through (area 7 is the 5x4 pocket before the mines plus four rows beyond
   them), so a group escorting the captain spends the 99 lives before he has parked, nothing re-arms the
   trigger, and he patrols his two tiles for the rest of the mission.  An escort unit standing on a pickup
   tile is also the record the scan finds first (lower index) and receives his order instead.
   Fix: the conditions become `(S==5)` - the captain's own steps fire the hand-off.  The step code reads a
   moving unit's PREVIOUS tile as its position and the three actions cover his whole patrol row, so his
   first patrol step is caught; his approach through the area costs a dozen lives of the 99.  The player's
   movements no longer matter.  (Trigger 9 already works this way.)
2. The rebel camp is a corridor exactly five tiles wide (x 102..106) and rows z 73 and z 74 are completely
   filled with rebel units and structures (TRSC REAP TRSC REAP TRSC / TRSC CRYO TRSC CRYO TRSC).  The
   captain enters the camp from the north (z 87) and his last waypoints and trigger 9's area (z 70..71)
   are south of that wall; units never pass through allies, and the only unit-free route is the loop
   through the player's half of the map, far longer than the 32-step path buffer.  Trigger 9 is what moves
   the rebels away, so the two wait for each other.
   Fix: trigger 9's area also covers x 102..106, z 75..76, the two free rows north of the wall, in the
   `.mtg` grid (file row = height-1-z); the original ten cells at z 70..71 stay.  He trips it on his way
   in, the mobile rebels of rows 72..74 leave (the T tower and the two CRYO structures are immobile and
   stay, leaving three free columns), and he continues to (104,71).  The formation itself is unchanged,
   so the player has no new shortcut through the camp.

Everything else of the two files is untouched (LF line ending of the .tro, every other .mtg byte).  The
installer's copy under patcher/game follows through tools/resources.py sync.

CLI
    python tools/fix_tarr02_captain.py verify "../Dark-Colony/DC - Council wars/ozi_ns/scenario/council"
    python tools/fix_tarr02_captain.py plan   DIR
    python tools/fix_tarr02_captain.py apply  DIR      (writes tarr02.tro.captain.bak / tarr02.mtg.captain.bak)
"""

import argparse
import os
import shutil
import sys

TRO_EDITS = [(b'\n7 trip 0 (S==0)\n', b'\n7 trip 0 (S==5)\n'),
             (b'\n21 trip 0 (S==0)\n', b'\n21 trip 0 (S==5)\n'),
             (b'\n23 trip 0 (S==0)\n', b'\n23 trip 0 (S==5)\n')]
MTG_CELLS = [(x, z) for z in (75, 76) for x in range(102, 107)]   # -> trigger 9
MTG_KEEP = [(x, z) for z in (70, 71) for x in range(102, 107)]    # must already be 9
TRIGGER = 9


def state_tro(data):
    stock = all(o in data and n not in data for o, n in TRO_EDITS)
    patched = all(n in data and o not in data for o, n in TRO_EDITS)
    return 'stock' if stock else 'patched' if patched else 'unknown'


def mtg_cell(d, x, z):
    w, h = d[0], d[1]
    return 2 + (h - 1 - z) * w + x


def state_mtg(d):
    w, h = d[0], d[1]
    if len(d) != 2 + w * h or not all(d[mtg_cell(d, x, z)] == TRIGGER for x, z in MTG_KEEP):
        return 'unknown'
    vals = {d[mtg_cell(d, x, z)] for x, z in MTG_CELLS}
    return 'stock' if vals == {0} else 'patched' if vals == {TRIGGER} else 'unknown'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=('verify', 'plan', 'apply'))
    ap.add_argument('dir', help='the ozi_ns/scenario/council folder')
    a = ap.parse_args(argv)
    tro, mtg = os.path.join(a.dir, 'tarr02.tro'), os.path.join(a.dir, 'tarr02.mtg')
    td, md = open(tro, 'rb').read(), open(mtg, 'rb').read()
    st, sm = state_tro(td), state_mtg(md)
    print('%s: %s\n%s: %s' % (tro, st, mtg, sm))
    if a.mode == 'verify':
        return 0 if st == sm == 'patched' else 1
    if 'unknown' in (st, sm):
        print('refusing: a file is neither stock nor patched'); return 1
    for o, n in TRO_EDITS:
        print('tro: %r -> %r%s' % (o.strip(), n.strip(), '' if st == 'stock' else '  (done)'))
    print('mtg: cells %s -> trigger %d%s' % (MTG_CELLS, TRIGGER, '' if sm == 'stock' else '  (done)'))
    if a.mode == 'plan':
        return 0
    if st == 'stock':
        shutil.copy2(tro, tro + '.captain.bak')
        for o, n in TRO_EDITS:
            td = td.replace(o, n, 1)
        open(tro, 'wb').write(td); print('written', tro)
    if sm == 'stock':
        shutil.copy2(mtg, mtg + '.captain.bak')
        md = bytearray(md)
        for x, z in MTG_CELLS:
            md[mtg_cell(md, x, z)] = TRIGGER
        open(mtg, 'wb').write(bytes(md)); print('written', mtg)
    return 0


if __name__ == '__main__':
    sys.exit(main())

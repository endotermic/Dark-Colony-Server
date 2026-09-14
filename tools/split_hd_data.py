#!/usr/bin/env python3
"""Split the 1024x768 interface data from the stock 640x480 files so both games run side by side.

Until 14 Sep 2026 the rebuilt 1024x768 menus, HUD frame, loading screens, briefing-marker lists
and re-baked logo sprites *replaced* the stock files under their stock names, so the untouched
originals (dc16original1998.exe, engexp16original.exe) could no longer run from the same folder:
they drew the 1024x768 pictures into a 640x480 frame.  The fix is a rename, not a copy:

  * every INTRFACE file that differs from stock moves to INTRF_HD/ (same name), the stock file
    comes back under its stock name; the patched exe opens `intrf_hd/<name>` instead of
    `intrface/<name>` for exactly those files (patch_hd_paths.py, 30 strings in DGROUP), the
    original exe keeps reading INTRFACE/;
  * a moved script's `background intrface/<gif>` line becomes `background intrf_hd/<gif>` when
    that GIF moved too (the two loading BMPs are opened by the exe directly);
  * the six briefing lists GAMESTAT/*SCENE.TXT (letterboxed globe markers) move to INTRF_HD/ as
    well - the exe's `gamestat/hscene` strings become `intrf_hd/hscene`, same length;
  * the re-baked logo animations SPRITES/DCSS.SPR, DCUK.SPR, DCUT.SPR become SPRITES/DCSS_HD.SPR
    etc.  The game reaches them through ANIMATE/<name>.FIN, whose frame records name the bank
    (`sprites/%s`, an 8-byte C string), so the tool writes ANIMATE/DCUK_HD.FIN etc. with the bank
    fields renamed (the animation *name* DCUK the scripts refer to stays), and INTRF_HD copies of
    the per-screen lists INTRG.DAT / INTRO.DAT that say `dcuk_hd.fin` instead of `dcuk.fin`;
  * Council Wars: the same for the `exp/intrface` override scripts -> `exp/intrf_hd`, and
    `exp/gamestat/*xscene.txt` -> `exp/intrf_hd` (the overlay helper prefixes `exp/` and falls
    back to the game root, so the GIFs stay in the root INTRF_HD).  The OZI overlay
    (`ozi_ns/intrf_hd`) is produced by build_ozi_overlay.py, run it afterwards.

Not carried over: INTRFACE/DCSS.SPR, DCUK.SPR, DCUT.SPR (byte copies nothing reads, doc 10.11)
and INTRFACE/MULTIE~1.TXT (a stray duplicate of MULTIE) - those are only restored to stock.

The stock tree is a folder with the same layout holding the stock files (at least INTRFACE,
GAMESTAT, SPRITES/DC??.SPR, exp/intrface, exp/gamestat), e.g. from the game repository:

    git -C Dark-Colony archive 0307feb "DC - Classic/INTRFACE" ... | tar -x -C stock

Idempotent: a second run finds INTRFACE == stock and does nothing.  A stock game folder that
was *not* patched (nothing differs) is left alone as well.

CLI
    python split_hd_data.py plan  GAME_DIR --stock STOCK_GAME_DIR
    python split_hd_data.py apply GAME_DIR --stock STOCK_GAME_DIR
"""

import argparse
import os
import re
import sys

HD_DIR = 'INTRF_HD'                      # sibling of INTRFACE, GAMESTAT (root) / of exp/intrface (Council Wars)
HD_PREFIX = b'intrf_hd/'
SCENE_LISTS = ('hscene.txt', 'gscene.txt', 'htscene.txt', 'gtscene.txt', 'hxscene.txt', 'gxscene.txt')
LOGO_BANKS = ('dcss', 'dcuk', 'dcut')     # SPRITES banks re-baked with the menu backdrop (paint_intro.py)
DROP = {'dcss.spr', 'dcuk.spr', 'dcut.spr', 'multie~1.txt'}
BACKGROUND = re.compile(rb'^([ \t]*background[ \t]+)intrface/(\S+)', re.M | re.I)


def find_ci(folder, name):
    if not folder or not os.path.isdir(folder):
        return None
    for f in os.listdir(folder):
        if f.lower() == name.lower():
            return os.path.join(folder, f)
    return None


def read(path):
    with open(path, 'rb') as fh:
        return fh.read()


class Plan:
    def __init__(self, apply):
        self.apply, self.actions, self.notes = apply, [], []

    def write(self, dst, data, why):
        if os.path.isfile(dst) and read(dst) == data:
            return
        self.actions.append((dst, why))
        if self.apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'wb') as fh:
                fh.write(data)


def rename_fin_banks(data, renamed):
    """Every 8-byte bank field `<bank>\\0...` of a renamed bank becomes `<bank>_hd\\0` (7 chars + NUL:
    load_sprite_bank 0x42532C formats the field with `sprites/%s` as a C string)."""
    for bank in renamed:
        old = bank.encode().ljust(8, b'\0')
        new = (bank + '_hd').encode().ljust(8, b'\0')
        assert len(new) == 8
        data = data.replace(old, new)
    return data


def rename_dat_list(data, renamed):
    out = []
    for raw in data.split(b'\n'):
        line = raw.rstrip(b'\r')
        if line.strip().lower() in {(b + '.fin').encode() for b in renamed}:
            line = line.strip()[:-4] + b'_hd.fin'
        out.append(line + (b'\r' if raw.endswith(b'\r') else b''))
    return b'\n'.join(out)


def split_interface(game, stock, rel, hd_rel, moved_base, plan):
    """Move the differing files of <game>/<rel> to <game>/<hd_rel>; restore stock.  Returns the
    lower-case names of the moved GIFs (for the `background` retargeting of override scripts)."""
    idir = find_ci(os.path.join(game, *rel[:-1]) if len(rel) > 1 else game, rel[-1])
    sdir = find_ci(os.path.join(stock, *rel[:-1]) if len(rel) > 1 else stock, rel[-1])
    if not idir:
        return set()
    if not sdir:
        plan.notes.append('WARNING %s: no stock counterpart, left alone' % '/'.join(rel))
        return set()
    hd_dir = os.path.join(game, *hd_rel)
    differing = {}
    for fn in sorted(os.listdir(idir), key=str.lower):
        if fn.lower().endswith('.bak') or not os.path.isfile(os.path.join(idir, fn)):
            continue
        s = find_ci(sdir, fn)
        if not s:
            plan.notes.append('NOTE %s/%s has no stock counterpart, left in place' % ('/'.join(rel), fn))
            continue
        cur = read(os.path.join(idir, fn))
        st = read(s)
        if cur != st:
            differing[fn] = (cur, st)
    moved_gifs = {fn.lower() for fn in differing if fn.lower().endswith('.gif')} | moved_base
    for fn, (cur, st) in differing.items():
        if fn.lower() in DROP:
            plan.write(os.path.join(idir, fn), st, 'restore stock (HD copy dropped: nothing reads it)')
            continue
        hd = cur
        if not fn.lower().endswith(('.gif', '.bmp', '.spr', '.dat')):
            def retarget(m):
                name = m.group(2)
                if (name.decode('latin-1').lower() + '.gif') in moved_gifs:
                    return m.group(1) + HD_PREFIX + name
                return m.group(0)
            hd = BACKGROUND.sub(retarget, cur)
        plan.write(os.path.join(hd_dir, fn), hd, 'HD file' + (' (background -> intrf_hd/)' if hd != cur else ''))
        plan.write(os.path.join(idir, fn), st, 'restore stock')
    return moved_gifs


def split_scene_lists(game, stock, rel, hd_rel, plan):
    gdir = find_ci(os.path.join(game, *rel[:-1]) if len(rel) > 1 else game, rel[-1])
    sdir = find_ci(os.path.join(stock, *rel[:-1]) if len(rel) > 1 else stock, rel[-1])
    if not gdir or not sdir:
        return
    for name in SCENE_LISTS:
        g, s = find_ci(gdir, name), find_ci(sdir, name)
        if not g or not s:
            continue
        cur, st = read(g), read(s)
        if cur != st:
            plan.write(os.path.join(game, *hd_rel, os.path.basename(g)), cur, 'HD briefing list')
            plan.write(g, st, 'restore stock')


def split_logo_banks(game, stock, plan):
    sprites = find_ci(game, 'SPRITES')
    animate = find_ci(game, 'ANIMATE')
    ssprites = find_ci(stock, 'SPRITES')
    renamed = []
    for bank in LOGO_BANKS:
        g = find_ci(sprites, bank + '.spr')
        s = find_ci(ssprites, bank + '.spr')
        if not g or not s:
            continue
        cur, st = read(g), read(s)
        if cur == st:
            if find_ci(sprites, bank + '_hd.spr'):
                renamed.append(bank)          # already split by an earlier run
            continue
        renamed.append(bank)
        plan.write(os.path.join(sprites, os.path.basename(g)[:-4] + '_HD.SPR'), cur, 'HD sprite bank')
        plan.write(g, st, 'restore stock')
    if not renamed:
        return renamed
    for bank in renamed:
        fin = find_ci(animate, bank + '.fin')
        if not fin:
            plan.notes.append('WARNING ANIMATE/%s.fin missing' % bank)
            continue
        data = read(fin)
        inside = [b for b in renamed if b.encode().ljust(8, b'\0') in data]
        plan.write(os.path.join(animate, os.path.basename(fin)[:-4] + '_HD.FIN'),
                   rename_fin_banks(data, renamed), 'FIN with bank fields %s' % ', '.join(b + '_hd' for b in inside))
    idir = find_ci(game, 'INTRFACE')
    for fn in sorted(os.listdir(idir), key=str.lower):
        if fn.lower().endswith('.dat'):
            data = read(os.path.join(idir, fn))
            new = rename_dat_list(data, renamed)
            if new != data:
                plan.write(os.path.join(game, HD_DIR, fn), new, 'FIN list naming the HD banks')
    return renamed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('plan', 'apply'))
    ap.add_argument('game', help='a game folder ("DC - Classic" or "DC - Council wars")')
    ap.add_argument('--stock', required=True, help='folder with the stock files in the same layout')
    a = ap.parse_args(argv)
    game, stock = os.path.abspath(a.game), os.path.abspath(a.stock)
    for p in (game, stock):
        if not os.path.isdir(p):
            raise SystemExit('missing: ' + p)
    plan = Plan(a.command == 'apply')
    moved = split_interface(game, stock, ('INTRFACE',), (HD_DIR,), set(), plan)
    split_interface(game, stock, ('exp', 'intrface'), ('exp', 'intrf_hd'), moved, plan)
    split_scene_lists(game, stock, ('GAMESTAT',), (HD_DIR,), plan)
    split_scene_lists(game, stock, ('exp', 'gamestat'), ('exp', 'intrf_hd'), plan)
    renamed = split_logo_banks(game, stock, plan)
    verb = 'written' if plan.apply else 'would write'
    print('%s: %d files (%s)' % (verb, len(plan.actions), os.path.basename(game)))
    for dst, why in plan.actions:
        print('  %-52s %s' % (os.path.relpath(dst, game), why))
    if renamed:
        print('  sprite banks renamed: ' + ', '.join('%s -> %s_hd' % (b, b) for b in renamed))
    for n in plan.notes:
        print('  ' + n)
    if not plan.actions:
        print('  nothing to do')
    return 1 if any(n.startswith('WARNING') for n in plan.notes) else 0


if __name__ == '__main__':
    sys.exit(main())

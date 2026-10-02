#!/usr/bin/env python3
"""Tracer bullets for the human trooper, and the Gray trooper's bolt at every weapon level (2 Oct 2026).

Why (docs/DC16_DISPLAY_AND_RESOLUTION.md section 10.64): the weapon loader (0x43B6EC) draws a projectile only
when an animation `<sprite>BULLET0` exists for the weapon's sprite name (WEAPSTAT.TXT column 2). The human
trooper's weapons 1-3 and the Gray trooper's upgraded weapons 16/17 say `weapons`, a name no FIN bank defines,
so those shots fly invisibly; only the Gray level-0 weapon 15 (`GRAY`, GRAYBULLET0 in GRAY.FIN) is drawn. The
projectile sprite is display-only: create_missile (0x44192C) draws its random byte before it looks at the
sprite and starts an animation instance only when the sprite exists; the sync checksum (0x44ABC0) covers the
missile COUNT, not animations, so this change never desyncs - not even against a player on an older exe.

What this tool writes (data only, no exe byte):
  * SPRITES/TRAC.SPR  - 16 streak cells, one per file facing (bright head, tail fading yellow -> red behind it)
  * ANIMATE/TRAC.FIN  - animations TRACBULLET0..15 (+ SMOKBULLET0..15 for the Lieutenant's pistol, ALIASES), each one frame of three cells
                        (and for the Gray commander's weapon 62, renamed SMOG: SMOGBULLET0..15 = the bare Gray bolt, SMOGEXPLODE0 =
                        its pistol hit animation copied from TURR.FIN so the explosion count - a rand() consumer - stays 1): the Gray bolt's own glow cell
                        (bank glit 13, draw mode 5) and ground glow (bank smsp 0, draw mode 3) with the Gray
                        bolt's exact cell records, plus the streak cell registered on the glow's centre
  * dc/gamestat/weapstat.txt, exp/gamestat/weapstat.txt - the ROOT table with weapons 1-3 -> TRAC,
                        16/17 -> GRAY and 62 -> SMOG; ozi_ns/gamestat/weapstat.txt gets the same five edits in place
                        (build_ozi_overlay.py applies them too when it regenerates the overlay)
  * exp/animozi.dat   - `trac.fin` appended (the patched exe's start-up list; exp/anim.dat stays stock)
The root GAMESTAT/WEAPSTAT.TXT and ANIM.DAT stay byte-identical for the original exes. The untouched
ENGEXP16.EXE does read exp/gamestat/weapstat.txt when present: it then draws the Gray bolt at every level and
still nothing for TRAC (its stock exp/anim.dat never loads trac.fin, and a missing BULLET0 is silent) - the
second deliberate data exception after exp/intrface/bintroe (section 10.35).

Facings: the animation-set loader (0x426014) takes the 16 file facings `<name>BULLET<n>` with
n = (12 - i) & 15 for the set index i = heading >> 1, and the heading (0..31, from the missile's direction
byte >> 3) is 0 = +x (east), 8 = +z (north, screen up), counter-clockwise. So file facing n points
270 - 22.5 n degrees (mathematical, y up): 0 south, 4 west, 8 north, 12 east; FACING_ANGLE below.

Cell registration (blitter 0x4543FC culling code + measured in game): a cell's LEFT edge is at x + xoffset
and its BOTTOM edge at y from the object's ground point (x/y = the FIN cell record, xoffset = the .SPR
directory entry; yoffset plays no part in the position, the missile's height lifts the whole frame). Units
stand on their point (TRSC STAND: y 4, h 45); GRAYBULLET0's glow (8x8, x -139, xoffset 136, y 4) is centred
at (+1, 0). The streak cells carry the glow's xoffset and put their head pixel on that centre.

usage:
    python tracer.py plan    GAME            # what would change
    python tracer.py apply   GAME            # write the bank, the FIN, the overlays, the animozi line
    python tracer.py verify  GAME            # check the files are in place and consistent
    python tracer.py preview OUT.png         # sheet of the 16 streak cells (needs Pillow)
"""
import math
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spr  # noqa: E402

BANK = 'trac'                 # bank / FIN name (7 chars max: load_sprite_bank formats "sprites/%s" as a C string)
ANIM = 'TRACBULLET'           # + file facing 0..15
# further weapons that get the same streak WITHOUT a table edit: the loader resolves `<sprite>BULLET<n>` by
# animation name, so the FIN also declares these names on the same frames. SMOK = weapon 5, the Lieutenant's
# pistol (its sprite name must stay SMOK: SMOKEXPLODE is its hit animation, and the explosion count feeds rand())
ALIASES = ('SMOKBULLET',)
# the Gray commander's weapon 62 shares the SMOK name with the Lieutenant's pistol, so it gets a name of its own
# in the overlay tables and the FIN declares both lookups the loader makes for it: SMOGBULLET<n> = the Gray
# bolt (glow + ground glow, no streak) and SMOGEXPLODE0 = the pistol's hit animation SMOKEXPLODE0 copied byte
# for byte (TURR.FIN frames 184..191: bank ssss cells 0..6, (duration, record)), so the explosion count stays 1
GRAY_COMMANDER = 'SMOG'
PISTOL_HIT = [(6, ('ssss', 0, -25, -11, 12, 16, 5, 0)), (0, ('ssss', 1, -25, -9, 12, 16, 5, 0)),
              (13, ('ssss', 1, -25, -9, 12, 16, 5, 0)), (20, ('ssss', 2, -25, -6, 12, 16, 5, 0)),
              (26, ('ssss', 3, -25, -5, 12, 16, 5, 0)), (20, ('ssss', 4, -25, -3, 12, 16, 5, 0)),
              (13, ('ssss', 5, -25, -2, 12, 16, 5, 0)), (6, ('ssss', 6, -25, 2, 12, 16, 5, 0))]
FIN_FLAGS = 0x1D
SPR_FLAGS = 0x81              # RLE like every stock bank
# the Gray bolt's two cells, byte for byte from GRAY.FIN frame 421 (bank, cell, x, y, a, b, mode, d)
GLOW_CELL = ('glit', 13, -139, 4, 0, 16, 5, 0)
SOIL_CELL = ('smsp', 0, -59, 22, 0, 16, 3, 0)
GLOW_OFFSETS = (136, 116)     # GLIT.SPR cell 13 xoffset / yoffset
GLOW_CENTRE = (GLOW_CELL[2] + GLOW_OFFSETS[0] + 4, GLOW_CELL[3] - 4)   # (+1, 0): left + w/2, bottom - h/2 (stock bolt, at ground level)
STREAK_MODE = 5               # drawn like the glow cell
# a missile flies at height 0 = the shooter's ground point, so a streak registered on the Gray bolt's centre runs
# along the shooter's FEET (measured 2 Oct 2026, maintainer: "wrong offset is visible the most when shooting
# horizontally"); the streak and its glow are lifted to rifle height, the ground glow stays on the soil
GUN_LIFT = 22                 # px above the ground point (TRSC rifle at ~55 % of the 43 px sprite)
BULLET_GLOW = GLOW_CELL[:3] + (GLOW_CELL[3] - GUN_LIFT,) + GLOW_CELL[4:]
# WEAPSTAT.TXT column 2 per weapon number: the human trooper's three levels get the tracer, the Gray
# trooper's two upgraded levels reuse the stock bolt
WEAPON_SPRITES = {1: 'TRAC', 2: 'TRAC', 3: 'TRAC', 16: 'GRAY', 17: 'GRAY', 62: 'SMOG'}
OVERLAY_TABLES = ('dc', 'exp')          # copies of the ROOT table with the edits
PACK_TABLE = os.path.join('ozi_ns', 'gamestat', 'weapstat.txt')   # the pack's own table, edited in place
ANIMOZI = os.path.join('exp', 'animozi.dat')

# ---- the streak -------------------------------------------------------------------------------
TAIL_PX = 26                  # length behind the head (the bullet moves ~30 px per game tick)
HEAD_HALF = 1.7               # half-width at the head, in pixels
TAIL_HALF = 0.55              # half-width at the end
# palette ramp from hot to cold (PALETTE.GIF indices): pale yellow, yellow, amber, orange, red-orange, red
RAMP = (68, 70, 72, 75, 78, 80, 82)
RAMP_STEPS = (0.80, 0.62, 0.46, 0.32, 0.20, 0.11, 0.05)   # intensity thresholds for RAMP[k]


def FACING_ANGLE(n):
    """Direction of flight of file facing n, in radians, mathematical convention (y up)."""
    return math.radians(270.0 - 22.5 * n)


def streak_pixels(n):
    """(w, h, px, head) of the streak cell for file facing n: px = w*h palette indices (0 transparent),
    head = (hx, hy) pixel of the bullet's position inside the cell."""
    ang = FACING_ANGLE(n)
    dx, dy = math.cos(ang), -math.sin(ang)             # screen direction of flight (y down)
    tx, ty = -dx * TAIL_PX, -dy * TAIL_PX               # tail end relative to the head
    margin = 3
    x0, x1 = min(0.0, tx) - margin, max(0.0, tx) + margin
    y0, y1 = min(0.0, ty) - margin, max(0.0, ty) + margin
    w, h = int(math.ceil(x1 - x0)), int(math.ceil(y1 - y0))
    hx, hy = int(round(-x0)), int(round(-y0))
    px = bytearray(w * h)
    L2 = TAIL_PX * TAIL_PX
    for j in range(h):
        for i in range(w):
            cx, cy = i + 0.5 - hx, j + 0.5 - hy            # pixel centre relative to the head
            t = (cx * tx + cy * ty) / L2                    # 0 at the head, 1 at the tail end
            if t < -0.08 or t > 1.0:
                continue
            tc = max(0.0, t)
            px_, py_ = tc * tx, tc * ty                     # nearest point on the segment
            d = math.hypot(cx - px_, cy - py_)
            half = HEAD_HALF + (TAIL_HALF - HEAD_HALF) * tc
            if d > half + 0.6:
                continue
            across = max(0.0, 1.0 - d / (half + 0.6)) ** 0.8
            along = (1.0 - tc) ** 1.15
            inten = across * along
            if t < 0:
                inten *= max(0.0, 1.0 + t / 0.08)          # a short round nose in front of the head
            # ordered dither so the fading tail thins out instead of ending in a hard step
            inten += ((i * 7 + j * 3) % 5 - 2) * 0.012
            v = 0
            for k, thr in enumerate(RAMP_STEPS):
                if inten >= thr:
                    v = RAMP[k]
                    break
            px[j * w + i] = v
    return w, h, px, (hx, hy)


def build_cells():
    cells = []
    for n in range(16):
        w, h, px, head = streak_pixels(n)
        cells.append(dict(w=w, h=h, ox=GLOW_OFFSETS[0], oy=GLOW_OFFSETS[1], px=px, head=head))
    return cells


def streak_record(n, cell):
    """FIN cell record placing the streak's head pixel on the glow's centre: left = x + xoffset, bottom = y."""
    hx, hy = cell['head']
    x = GLOW_CENTRE[0] - hx - GLOW_OFFSETS[0]
    y = GLOW_CENTRE[1] + (cell['h'] - hy) - GUN_LIFT
    return (BANK, n, x, y, 0, 16, STREAK_MODE, 0)


def palette(game):
    """The master palette (PALETTE.GIF) as 256 (r, g, b)."""
    from PIL import Image
    p = Image.open(find(game, 'PALETTE.GIF')).getpalette()
    return [tuple(p[3 * i:3 * i + 3]) for i in range(256)]


# ---- FIN writer --------------------------------------------------------------------------------
def cstr(s, n):
    b = s.encode('latin-1')
    if len(b) >= n:
        raise ValueError('%r does not fit %d bytes' % (s, n))
    return b + b'\0' * (n - len(b))


FIN_BANKS = (BANK, GLOW_CELL[0], SOIL_CELL[0], PISTOL_HIT[0][1][0])


def fin_plan(cells):
    """The FIN's content: frames [(duration, [records])] and animations [(name, first, last)]."""
    frames, anims = [], []
    for n in range(16):                                   # 0..15: streak frames (duration 0 -> 15 -> 2 ticks, like GRAYBULLET0)
        frames.append((0, [BULLET_GLOW, streak_record(n, cells[n]), SOIL_CELL]))
    for name in (ANIM,) + ALIASES:
        anims += [(name + str(n), n, n) for n in range(16)]
    base = len(frames)
    for n in range(16):                                   # 16..31: the bare Gray bolt for the Gray commander
        frames.append((0, [GLOW_CELL, SOIL_CELL]))
        anims.append((GRAY_COMMANDER + 'BULLET' + str(n), base + n, base + n))
    base = len(frames)
    for dur, rec in PISTOL_HIT:                           # 32..39: its hit animation = SMOKEXPLODE0
        frames.append((dur, [rec]))
    anims.append((GRAY_COMMANDER + 'EXPLODE0', base, base + len(PISTOL_HIT) - 1))
    return frames, anims


def build_fin(cells):
    frames, anims = fin_plan(cells)
    out = bytearray(struct.pack('<HHHH', FIN_FLAGS, len(frames), len(anims), len(FIN_BANKS)))
    for b in FIN_BANKS:
        out += cstr(b, 8)
    for name, first, last in anims:
        out += cstr(name, 16) + struct.pack('<HH', first, last)
    for dur, recs in frames:
        out += struct.pack('<HH', len(recs), dur)
        for _ in range(8):
            out += cstr('NONAME', 16) + struct.pack('<hh', 0, 0)
    for dur, recs in frames:
        for bank, cell, x, y, a, b, mode, d in recs:
            out += cstr(bank, 8) + struct.pack('<Hhh', cell, x, y) + struct.pack('<HHHH', a, b, mode, d)
    return bytes(out)


def parse_fin(data):
    """Minimal reader (the layout of sprdata2json.js): banks, {anim: (first, last)}, frames [(dur, [records])]."""
    flags, nf, na, nb = struct.unpack_from('<HHHH', data, 0)
    p = 8
    banks = [data[p + 8 * i:p + 8 * i + 8].split(b'\0')[0].decode('latin-1') for i in range(nb)]
    p += 8 * nb
    anims = {}
    for i in range(na):
        anims[data[p:p + 16].split(b'\0')[0].decode('latin-1')] = struct.unpack_from('<HH', data, p + 16)
        p += 20
    frames = []
    for i in range(nf):
        nc, dur = struct.unpack_from('<HH', data, p)
        frames.append([dur, nc, []])
        p += 164
    for f in frames:
        for k in range(f[1]):
            rec = (data[p:p + 8].split(b'\0')[0].decode('latin-1'),) + struct.unpack_from('<Hhh', data, p + 8) \
                + struct.unpack_from('<HHHH', data, p + 14)
            f[2].append(rec)
            p += 22
    return dict(flags=flags, banks=banks, anims=anims, frames=[(f[0], f[2]) for f in frames], trailing=len(data) - p)


# ---- tables ------------------------------------------------------------------------------------
ROW = re.compile(rb'^(\s*)(\d+)(\s+)(\S+)(\s.*)$')


def fix_weapstat(data):
    """The five sprite-name edits on a WEAPSTAT table, line endings and spacing kept; idempotent."""
    eol = b'\r\n' if b'\r\n' in data else b'\n'
    out = []
    for line in data.split(eol):
        m = ROW.match(line)
        if m and int(m.group(2)) in WEAPON_SPRITES and not line.lstrip().startswith(b'%'):
            line = m.group(1) + m.group(2) + m.group(3) + WEAPON_SPRITES[int(m.group(2))].encode() + m.group(5)
        out.append(line)
    return eol.join(out)


def add_animozi_line(data):
    eol = b'\r\n' if b'\r\n' in data else b'\n'
    lines = data.split(eol)
    want = (BANK + '.fin').encode()
    if any(l.strip().lower() == want for l in lines):
        return data
    while lines and not lines[-1].strip():
        lines.pop()
    return eol.join(lines + [want]) + eol


# ---- plan / apply ------------------------------------------------------------------------------
def find(root, *parts):
    """Case-insensitive path lookup; returns the repository spelling or the joined path if absent."""
    p = root
    for part in parts:
        if os.path.isdir(p):
            hit = [e for e in os.listdir(p) if e.lower() == part.lower()]
            p = os.path.join(p, hit[0] if hit else part)
        else:
            p = os.path.join(p, part)
    return p


def plan(game):
    """[(path, data, why)] of every file that would change."""
    cells = build_cells()
    pal = palette(game)
    changes = []

    def want(path, data, why):
        if not (os.path.isfile(path) and open(path, 'rb').read() == data):
            changes.append((path, data, why))

    tmp = os.path.join(game, 'SPRITES', '.trac.tmp')          # spr.write_spr writes to a path
    spr.write_spr(tmp, SPR_FLAGS, [dict(w=c['w'], h=c['h'], ox=c['ox'], oy=c['oy'], px=c['px']) for c in cells], pal)
    bank = open(tmp, 'rb').read()
    os.remove(tmp)
    want(find(game, 'SPRITES', BANK.upper() + '.SPR'), bank, '16 streak cells')
    want(find(game, 'ANIMATE', BANK.upper() + '.FIN'), build_fin(cells), '%s0..15: glow + streak + ground glow' % ANIM)

    root = open(find(game, 'GAMESTAT', 'WEAPSTAT.TXT'), 'rb').read()
    fixed = fix_weapstat(root)
    if fixed == root:
        raise SystemExit('the root WEAPSTAT.TXT has no rows to edit?')
    for ov in OVERLAY_TABLES:
        want(find(game, ov, 'gamestat', 'weapstat.txt'), fixed, 'root table, weapons %s' % describe())
    pack = find(game, *PACK_TABLE.split(os.sep))
    if os.path.isfile(pack):
        want(pack, fix_weapstat(open(pack, 'rb').read()), 'pack table, weapons %s' % describe())
    anim = find(game, *ANIMOZI.split(os.sep))
    if os.path.isfile(anim):
        want(anim, add_animozi_line(open(anim, 'rb').read()), '+ %s.fin' % BANK)
    else:
        changes.append((anim, None, 'MISSING (run build_ozi_overlay.py --apply first)'))
    return changes


def describe():
    return ', '.join('%d -> %s' % kv for kv in sorted(WEAPON_SPRITES.items()))


def verify(game):
    ok = True
    fin_path = find(game, 'ANIMATE', BANK.upper() + '.FIN')
    spr_path = find(game, 'SPRITES', BANK.upper() + '.SPR')
    if not (os.path.isfile(fin_path) and os.path.isfile(spr_path)):
        print('bank or FIN missing'); return False
    f = parse_fin(open(fin_path, 'rb').read())
    s = spr.read_spr(spr_path)
    frames, anims = fin_plan(build_cells())
    if f['trailing'] or f['banks'] != list(FIN_BANKS) or len(s['cells']) != 16:
        print('FIN/SPR inconsistent'); ok = False
    if f['anims'] != {name: (first, last) for name, first, last in anims}:
        print('animation list differs from the plan'); ok = False
    if f['frames'] != [(dur, list(recs)) for dur, recs in frames]:
        print('frames differ from the plan'); ok = False
    for bank in f['banks'][1:]:
        if not os.path.isfile(find(game, 'SPRITES', bank.upper() + '.SPR')):
            print('bank %s.SPR missing' % bank); ok = False
    for ov in OVERLAY_TABLES + ('ozi_ns',):
        p = find(game, ov, 'gamestat', 'weapstat.txt')
        if not os.path.isfile(p):
            print('%s missing' % p); ok = False; continue
        d = open(p, 'rb').read()
        if fix_weapstat(d) != d:
            print('%s not edited' % p); ok = False
    anim = find(game, *ANIMOZI.split(os.sep))
    if not (os.path.isfile(anim) and add_animozi_line(open(anim, 'rb').read()) == open(anim, 'rb').read()):
        print('%s lacks %s.fin' % (anim, BANK)); ok = False
    print('OK' if ok else 'PROBLEMS', '-', fin_path, spr_path)
    return ok


def preview(out):
    from PIL import Image, ImageDraw
    game = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Dark-Colony', 'DC - Council wars')
    pal = palette(game)
    cells = build_cells()
    cw = max(c['w'] for c in cells) + 10
    ch = max(c['h'] for c in cells) + 14
    sheet = Image.new('RGB', (8 * cw, 2 * ch), (34, 30, 28))
    d = ImageDraw.Draw(sheet)
    for n, c in enumerate(cells):
        ox, oy = (n % 8) * cw + 5, (n // 8) * ch + 12
        for j in range(c['h']):
            for i in range(c['w']):
                v = c['px'][j * c['w'] + i]
                if v:
                    sheet.putpixel((ox + i, oy + j), pal[v])
        hx, hy = c['head']
        d.point((ox + hx, oy + hy), fill=(0, 255, 255))
        d.text((ox, oy - 11), 'n%d' % n, fill=(200, 200, 200))
    sheet = sheet.resize((sheet.width * 4, sheet.height * 4), Image.NEAREST)
    sheet.save(out)
    print(out, sheet.size)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ('plan', 'apply', 'verify', 'preview'):
        print(__doc__); return 2
    cmd, arg = argv
    if cmd == 'preview':
        preview(arg); return 0
    game = os.path.abspath(arg)
    if cmd == 'verify':
        return 0 if verify(game) else 1
    changes = plan(game)
    if not changes:
        print('nothing to do, everything is in place'); return 0
    for path, data, why in changes:
        print('%s %-48s %s' % ('write ' if cmd == 'apply' else 'would ', os.path.relpath(path, game), why))
        if cmd == 'apply' and data is not None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, 'wb').write(data)
    if cmd == 'apply':
        return 0 if verify(game) else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

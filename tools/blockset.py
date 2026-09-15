#!/usr/bin/env python3
"""blockset.py - decode, verify, render and generate Dark Colony map-editor block sets (.SET).

The map editor (``maped.exe``) paints terrain exclusively from a *block set*: the left-hand block
pane lists prefabricated groups of tiles, filtered by the "block type" chosen on the toolbar.
``LoadBlockSet`` reads ``\\scenario\\<terrain>.set`` next to the editor into a static array of 1000
blocks (``0x43599C``, ``count * 0xC8C`` bytes), so a set may hold at most 1000 blocks.  The game
never opens these files (``docs/DC16_MAP_FILES.md`` section 3.1).

Format (decoded 15 Sep 2026 by matching the stock ``desert.set`` blocks against the 90 stock desert
maps; 450 of 655 blocks appear verbatim in the maps, all conventions below verified that way)::

    u32 count
    count x BLOCK (3212 bytes):
        u32 zero, u32 zero
        u32 category            block type 0..23 = the terrain class the editor writes into
                                attribute bits 10..15 of every pasted cell
        u32 plane[6][10*10]     six 10x10 planes, row-major, row 0 = first row of the .MAP file
            plane 0  background tile  (BTS editor index + 1; 0 = cell not part of the block)
            plane 1  attribute bit 5  (0/1)
            plane 2  foreground tile  (BTS editor index + 1; 0 = none)
            plane 3  attribute bit 6  (0/1)
            plane 4  foreground level (attribute bits 0..3, the renderer's 32-px offset)
            plane 5  ground code: 0 = walkable (attr 0x080), 1 = blocking (0x200),
                     2 = none (0x000), 3 = blocking+walkable bit (0x280), 4 = bit 8 (0x100)
        u8  zero[800]

Commands::

    blockset.py info   SET --bts BTS                 counts, categories, sizes, dangling tile refs
    blockset.py render SET --bts BTS --out PNG       preview sheet (8-bit BTS art, 6-bit palette)
    blockset.py mine   --bts BTS --out SET [--game DIR --terrain atlantis[,gatlan]] [MAP ...]
                                                     build a set from existing maps of that terrain

``mine`` cuts every map of the terrain into blocks the way the original artists must have built
them: 10x10 windows of one terrain class become fill blocks (clean ones without foreground first,
decorated ones after), connected same-class groups that fit into 10x10 become prefab pieces (rock
clusters, ruins, plants), and larger structures (cliff lines) are cut into 10x10 windows.  Blocks
are de-duplicated, ranked by frequency, capped per class and written ordered by category.  Every
tile reference is checked against the BTS.  Stdlib only.
"""
import argparse
import collections
import glob
import os
import struct
import sys
import zlib

BLOCK = 3212
GRID = 10
CELLS = GRID * GRID
MAX_BLOCKS = 1000

GROUND_TO_ATTR = {0: 0x080, 1: 0x200, 2: 0x000, 3: 0x280, 4: 0x100}


def attr_to_ground(attr):
    if attr & 0x200:
        return 3 if attr & 0x80 else 1
    if attr & 0x100:
        return 4
    return 0 if attr & 0x80 else 2


# --------------------------------------------------------------------------- file formats

class Bts:
    """.BTS tile bank: u32 remap_count, u32 tile_count, 768-byte 6-bit palette, tiles {u32 index, 32x32 px}."""

    def __init__(self, path):
        d = open(path, 'rb').read()
        self.remap_count, self.count = struct.unpack_from('<II', d)
        pal = d[8:8 + 768]
        self.palette = [(pal[i] * 4 + pal[i] // 16, pal[i + 1] * 4 + pal[i + 1] // 16,
                         pal[i + 2] * 4 + pal[i + 2] // 16) for i in range(0, 768, 3)]
        self.tiles = {}
        off = 8 + 768
        for _ in range(self.count):
            idx = struct.unpack_from('<I', d, off)[0]
            self.tiles[idx] = d[off + 4:off + 4 + 1024]
            off += 1028
        if off != len(d):
            raise SystemExit(f'{path}: size mismatch ({len(d)} bytes, expected {off})')


class Block:
    __slots__ = ('category', 'bg', 'bit5', 'fg', 'bit6', 'level', 'ground')

    def __init__(self, category=0):
        self.category = category
        self.bg = [0] * CELLS      # editor index + 1
        self.bit5 = [0] * CELLS
        self.fg = [0] * CELLS      # editor index + 1
        self.bit6 = [0] * CELLS
        self.level = [0] * CELLS
        self.ground = [0] * CELLS

    @classmethod
    def parse(cls, data):
        b = cls(struct.unpack_from('<I', data, 8)[0])
        w = struct.unpack_from('<600I', data, 12)
        b.bg, b.bit5, b.fg, b.bit6, b.level, b.ground = [list(w[i * CELLS:(i + 1) * CELLS]) for i in range(6)]
        return b

    def pack(self):
        return struct.pack('<III', 0, 0, self.category) + struct.pack(
            '<600I', *(self.bg + self.bit5 + self.fg + self.bit6 + self.level + self.ground)) + bytes(800)

    def cells(self):
        return [i for i in range(CELLS) if self.bg[i]]

    def size(self):
        c = self.cells()
        if not c:
            return (0, 0)
        return (max(i % GRID for i in c) + 1, max(i // GRID for i in c) + 1)

    def key(self):
        return (self.category, tuple(self.bg), tuple(self.bit5), tuple(self.fg), tuple(self.bit6),
                tuple(self.level), tuple(self.ground))

    def set_cell(self, x, y, bg, fg, attr):
        i = y * GRID + x
        self.bg[i] = bg + 1
        self.fg[i] = fg + 1 if fg else 0
        self.bit5[i] = (attr >> 5) & 1
        self.bit6[i] = (attr >> 6) & 1
        self.level[i] = attr & 0xF if fg else 0
        self.ground[i] = attr_to_ground(attr)


def read_set(path):
    d = open(path, 'rb').read()
    count = struct.unpack_from('<I', d)[0]
    if len(d) != 4 + count * BLOCK:
        raise SystemExit(f'{path}: {len(d)} bytes does not match count {count} ({4 + count * BLOCK} expected)')
    return [Block.parse(d[4 + i * BLOCK:4 + (i + 1) * BLOCK]) for i in range(count)]


def write_set(path, blocks):
    if len(blocks) > MAX_BLOCKS:
        raise SystemExit(f'{len(blocks)} blocks exceed the editor limit of {MAX_BLOCKS}')
    with open(path, 'wb') as f:
        f.write(struct.pack('<I', len(blocks)))
        for b in blocks:
            f.write(b.pack())


class GameMap:
    """.MAP: u32 w, u32 h, w*h x {u16 bg, u16 fg}, w*h x u16 attribute (rows in file order)."""

    def __init__(self, path):
        d = open(path, 'rb').read()
        self.path = path
        self.w, self.h = struct.unpack_from('<II', d)
        n = self.w * self.h
        if len(d) != 8 + n * 6:
            raise SystemExit(f'{path}: size mismatch')
        pairs = struct.unpack_from(f'<{n * 2}H', d, 8)
        self.bg = pairs[0::2]
        self.fg = pairs[1::2]
        self.attr = struct.unpack_from(f'<{n}H', d, 8 + n * 4)

    def cls(self, i):
        return self.attr[i] >> 10


# --------------------------------------------------------------------------- info

def dangling(blocks, bts):
    bad = collections.Counter()
    for k, b in enumerate(blocks):
        for v in b.bg + b.fg:
            if v and (v - 1) not in bts.tiles:
                bad[k] += 1
    return bad


def cmd_info(args):
    blocks = read_set(args.set)
    print(f'{args.set}: {len(blocks)} blocks')
    cats = collections.Counter(b.category for b in blocks)
    print('categories:', ', '.join(f'{c}:{n}' for c, n in sorted(cats.items())))
    sizes = collections.Counter(b.size() for b in blocks)
    print('sizes:', ', '.join(f'{w}x{h}:{n}' for (w, h), n in sizes.most_common(12)))
    fgblocks = sum(1 for b in blocks if any(b.fg))
    print(f'blocks with foreground: {fgblocks}, distinct: {len(set(b.key() for b in blocks))}')
    if args.bts:
        bts = Bts(args.bts)
        refs = set(v - 1 for b in blocks for v in b.bg + b.fg if v)
        bad = dangling(blocks, bts)
        print(f'tile refs: {len(refs)} distinct, {len(refs - set(bts.tiles))} not in {os.path.basename(args.bts)} '
              f'({bts.count} tiles); blocks with a dangling ref: {len(bad)}')
        if args.verbose and bad:
            print('  ', sorted(bad.items())[:40])
    return 0


# --------------------------------------------------------------------------- render

def write_png(path, width, height, rows):
    raw = b''.join(b'\x00' + bytes(r) for r in rows)

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    open(path, 'wb').write(png)


def cmd_render(args):
    blocks = read_set(args.set)
    bts = Bts(args.bts)
    if args.category is not None:
        blocks = [b for b in blocks if b.category == args.category]
    scale = args.scale
    tp = 32 // scale                       # pixels per tile
    bp = GRID * tp + 2                     # pixels per block cell incl. 2-px gap
    cols = args.columns
    rows_n = (len(blocks) + cols - 1) // cols
    width, height = cols * bp, rows_n * bp
    img = [bytearray(width * 3) for _ in range(height)]
    pal = bts.palette
    missing = (255, 0, 255)
    order = sorted(range(len(blocks)), key=lambda k: (blocks[k].category, k)) if args.group else range(len(blocks))
    for n, k in enumerate(order):
        b = blocks[k]
        ox, oy = (n % cols) * bp, (n // cols) * bp
        catcol = ((b.category * 53) % 200 + 40, (b.category * 97) % 200 + 40, (b.category * 151) % 200 + 40)
        for yy in range(GRID * tp):
            row = img[oy + yy]
            for xx in range(2):
                row[(ox + GRID * tp + xx) * 3:(ox + GRID * tp + xx) * 3 + 3] = bytes(catcol)
        for i in range(CELLS):
            if not b.bg[i]:
                continue
            cx, cy = ox + (i % GRID) * tp, oy + (i // GRID) * tp
            for layer, transparent in ((b.bg[i] - 1, False), (b.fg[i] - 1 if b.fg[i] else None, True)):
                if layer is None:
                    continue
                tile = bts.tiles.get(layer)
                for py in range(tp):
                    row = img[cy + py]
                    for px in range(tp):
                        if tile is None:
                            c = missing
                        else:
                            p = tile[(py * scale) * 32 + px * scale]
                            if transparent and p == 0:
                                continue
                            c = pal[p]
                        o = (cx + px) * 3
                        row[o], row[o + 1], row[o + 2] = c
            if b.ground[i] in (1, 3) and args.marks:
                o = (cx + tp - 2) * 3
                img[cy + tp - 2][o:o + 3] = b'\xff\x00\x00'
    write_png(args.out, width, height, img)
    print(f'{args.out}: {len(blocks)} blocks, {width}x{height}')
    return 0


# --------------------------------------------------------------------------- mine

def find_maps(game, terrains):
    maps = []
    for scn in glob.glob(os.path.join(game, '**', '*.[sS][cC][nN]'), recursive=True):
        with open(scn, 'rb') as f:
            first = f.readline().strip().lower().decode('latin-1')
        if first in terrains:
            base = os.path.splitext(scn)[0]
            for ext in ('.MAP', '.map', '.Map'):
                if os.path.exists(base + ext):
                    maps.append(base + ext)
                    break
    return sorted(set(maps))


def components(m):
    """8-connected groups of cells with the same terrain class; returns list of (cls, [cell indices])."""
    w, h = m.w, m.h
    label = [-1] * (w * h)
    comps = []
    for start in range(w * h):
        if label[start] >= 0:
            continue
        c = m.cls(start)
        cid = len(comps)
        stack = [start]
        label[start] = cid
        cells = []
        while stack:
            i = stack.pop()
            cells.append(i)
            y, x = divmod(i, w)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not dx and not dy:
                        continue
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w:
                        j = ny * w + nx
                        if label[j] < 0 and m.cls(j) == c:
                            label[j] = cid
                            stack.append(j)
        comps.append((c, cells))
    return comps


def block_from_cells(m, cells, cls, x0, y0):
    b = Block(cls)
    for i in cells:
        y, x = divmod(i, m.w)
        b.set_cell(x - x0, y - y0, m.bg[i], m.fg[i], m.attr[i])
    return b


def cmd_mine(args):
    bts = Bts(args.bts)
    maps = list(args.maps)
    if args.game:
        terrains = set(t.strip().lower() + ('' if t.strip().lower().endswith('.bts') else '.bts')
                       for t in args.terrain.split(','))
        maps += find_maps(args.game, terrains)
    if not maps:
        raise SystemExit('no maps given (use --game DIR --terrain NAME or list .MAP files)')
    loaded = [GameMap(p) for p in sorted(set(maps))]
    print(f'{len(loaded)} maps:', ', '.join(os.path.basename(m.path) for m in loaded))

    fills = {}        # key -> [block, count, has_fg]
    pieces = {}       # key -> [block, count]
    windows = {}      # key -> [block, count]
    skipped = 0

    def valid(b):
        return all((v - 1) in bts.tiles for v in b.bg + b.fg if v)

    def add(store, b, extra=()):
        if not valid(b):
            return False
        k = b.key()
        if k in store:
            store[k][1] += 1
        else:
            store[k] = [b, 1, *extra]
        return True

    for m in loaded:
        comps = components(m)
        owner = {}
        for cid, (c, cells) in enumerate(comps):
            for i in cells:
                owner[i] = cid
        for cid, (c, cells) in enumerate(comps):
            xs = [i % m.w for i in cells]
            ys = [i // m.w for i in cells]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            if bw <= GRID and bh <= GRID:
                if len(cells) < args.min_piece:
                    skipped += 1
                    continue
                add(pieces, block_from_cells(m, cells, c, x0, y0))
                continue
            # large structure: 10x10 windows over the bounding box, two offsets for variety
            for off in (0, GRID // 2):
                for wy in range(y0 + off, y1 + 1, GRID):
                    for wx in range(x0 + off, x1 + 1, GRID):
                        inside = [i for i in cells if wx <= i % m.w < wx + GRID and wy <= i // m.w < wy + GRID]
                        if len(inside) == CELLS and wx + GRID <= m.w and wy + GRID <= m.h:
                            b = block_from_cells(m, inside, c, wx, wy)
                            add(fills, b, (any(b.fg),))
                        elif len(inside) >= args.min_window:
                            add(windows, block_from_cells(m, inside, c, wx, wy))
    print(f'raw: {len(fills)} fill windows, {len(pieces)} pieces, {len(windows)} partial windows, '
          f'{skipped} tiny groups skipped')

    # The stock sets start with an empty category-0 block followed by a 2x2 block of one plain
    # walkable floor tile; the editor paints a new map with that second block, so give it the most
    # common bare floor cell of the source maps.
    floor = collections.Counter()
    for m in loaded:
        for i in range(m.w * m.h):
            if m.fg[i] == 0 and (m.attr[i] & 0x3FF) == 0x80 and m.bg[i] in bts.tiles:
                floor[(m.bg[i], m.cls(i))] += 1
    (tile, cls), _ = floor.most_common(1)[0]
    default = Block(cls)
    for y in range(2):
        for x in range(2):
            default.set_cell(x, y, tile, 0, 0x80 | (cls << 10))
    out = [Block(0), default]
    print(f'default floor: tile {tile} class {cls} ({floor[(tile, cls)]} cells)')

    # rank and cap per category
    percat = collections.defaultdict(lambda: {'fill': [], 'deco': [], 'piece': [], 'window': []})
    for b, n, has_fg in fills.values():
        percat[b.category]['deco' if has_fg else 'fill'].append((n, b))
    for b, n in pieces.values():
        percat[b.category]['piece'].append((n, b))
    for b, n in windows.values():
        percat[b.category]['window'].append((n, b))

    def spread(items, cap, keyfn):
        items = sorted(items, key=keyfn)
        if len(items) <= cap:
            return [b for _, b in items]
        step = len(items) / cap
        return [items[int(k * step)][1] for k in range(cap)]

    report = []
    for cat in sorted(percat):
        g = percat[cat]
        chosen = []
        # fills: varied textures first; the uniform single-tile windows (solid rock, void) are the
        # most frequent windows of every map and would otherwise head every category in the pane
        def variety(t):
            b = t[1]
            return (-len(set(b.bg[i] for i in b.cells())), -t[0])
        chosen += spread(g['fill'], args.cap_fill, variety)
        chosen += spread(g['deco'], args.cap_deco, variety)
        # pieces: multi-cell groups first (big and frequent), single cells separately and sparingly
        multi = [t for t in g['piece'] if len(t[1].cells()) > 1]
        single = [t for t in g['piece'] if len(t[1].cells()) == 1]
        chosen += spread(multi, args.cap_piece, lambda t: (-len(t[1].cells()), -t[0]))
        chosen += spread(single, args.cap_single, lambda t: -t[0])
        chosen += spread(g['window'], args.cap_window, lambda t: (-len(t[1].cells()), -t[0]))
        report.append(f'  class {cat:2d}: fill {len(g["fill"])}->{min(len(g["fill"]), args.cap_fill)}, '
                      f'decorated {len(g["deco"])}->{min(len(g["deco"]), args.cap_deco)}, '
                      f'pieces {len(multi)}->{min(len(multi), args.cap_piece)}, '
                      f'single tiles {len(single)}->{min(len(single), args.cap_single)}, '
                      f'windows {len(g["window"])}->{min(len(g["window"]), args.cap_window)}')
        out += chosen
    print('\n'.join(report))
    if len(out) > args.max_blocks:
        print(f'{len(out)} blocks exceed --max-blocks {args.max_blocks}; trimming pieces/windows per class')
        # trim the largest groups first until it fits
        while len(out) > args.max_blocks:
            cats = collections.Counter(b.category for b in out)
            worst = cats.most_common(1)[0][0]
            for k in range(len(out) - 1, -1, -1):
                if out[k].category == worst:
                    del out[k]
                    break
    write_set(args.out, out)
    print(f'{args.out}: {len(out)} blocks written ({len(set(b.key() for b in out))} distinct), '
          f'dangling refs: {sum(dangling(out, bts).values())}')
    return 0


# --------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('info', help='describe a .SET and check its tile references')
    p.add_argument('set')
    p.add_argument('--bts')
    p.add_argument('-v', '--verbose', action='store_true')
    p.set_defaults(func=cmd_info)

    p = sub.add_parser('render', help='render a .SET to a PNG preview sheet')
    p.add_argument('set')
    p.add_argument('--bts', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--scale', type=int, default=4, choices=(1, 2, 4, 8), help='tile downscale (default 4 = 8 px tiles)')
    p.add_argument('--columns', type=int, default=8)
    p.add_argument('--category', type=int)
    p.add_argument('--group', action='store_true', help='order blocks by category')
    p.add_argument('--marks', action='store_true', help='red dot on blocking cells')
    p.set_defaults(func=cmd_render)

    p = sub.add_parser('mine', help='build a .SET from existing maps')
    p.add_argument('maps', nargs='*', help='.MAP files')
    p.add_argument('--bts', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--game', help='game folder to scan for .SCN files (recursive)')
    p.add_argument('--terrain', default='atlantis', help='comma-separated terrain names of line 1 of the .SCN')
    p.add_argument('--cap-fill', type=int, default=40, help='clean 10x10 fill blocks per class')
    p.add_argument('--cap-deco', type=int, default=30, help='decorated 10x10 fill blocks per class')
    p.add_argument('--cap-piece', type=int, default=50, help='multi-cell prefab pieces per class')
    p.add_argument('--cap-single', type=int, default=10, help='single-cell pieces per class')
    p.add_argument('--cap-window', type=int, default=25, help='partial 10x10 windows of large structures per class')
    p.add_argument('--min-piece', type=int, default=1, help='smallest group kept as a piece (cells)')
    p.add_argument('--min-window', type=int, default=30, help='smallest partial window kept (cells)')
    p.add_argument('--max-blocks', type=int, default=MAX_BLOCKS)
    p.set_defaults(func=cmd_mine)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())

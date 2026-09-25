#!/usr/bin/env python3
"""Render the high-resolution Dark Colony icon DC_HD.ICO from the geometry of the game's DC.ICO.

The game ships one 32x32, 16-colour icon, `DC - Council wars/DC.ICO` (also the exe's own icon): a
two-tone grey frame around a black field, the bevelled letters "DC" at the top and the red planet
Mars rising from the bottom, lit from the right.  Resampling that bitmap up would only enlarge its
pixels (rule of this project: never resample DC art up, re-render it from geometry), so this tool
re-draws the same design from measured geometry, in the 32-unit grid of the original:

  frame      the original's outer ring 0..1 (dark grey) and inner ring 1..2 (light grey) around the
             black field 2..30 - drawn as a THIN bevelled bezel in whole pixels (maintainer, 25 Sep
             2026: "gray frame ... must be thinner. use best practices of icon drawing"): a dark outer
             edge and a light inner ring lit from the top left, 1 px at 16..24, 1+1 px at 32..64,
             2+3 px at 256, a dark hairline around the field from 96 px, softly rounded corners and a
             transparent margin (1 px at 48/64, 4 px at 256) so the icon sits cleanly on light and dark
             backgrounds; the design fills the field that is left
  letters    one-unit strokes of light grey on the rows 4 and 8 and the columns between them -
             D: bars x 5..13 (the left serif of the original runs past the stem at x 7), stem x 7,
             bow x 13; C: bars x 18..25, back x 17, terminals x 24 on rows 5 and 7 - as solid
             3-D blocks (maintainer, 25 Sep 2026: "make them a bit 3 dimensional"): the original's
             dark-grey pixels right of and below the strokes read as an extrusion, so the letters
             are extruded towards the bottom right (shaded side walls), with a chamfered front face
             lit from the top left, rounded corners and a soft cast shadow
  planet     disc centre (17, 34), radius 22 (the arc of the original meets the field's top at
             y 12 and its left edge at y 17), lit from the right and slightly above: bright red
             on the limb, mottled dark red on the day side, black on the night side

and renders it at 4x supersampling for every icon size, with straight alpha.  Sizes below 32 px are
drawn simplified (flat letters with a one-step drop, only the large surface features), because
sub-pixel detail only blurs there.  The surface pattern is a fixed-seed value noise on the sphere, so
the output is the same on every run.

    python make_dc_icon.py OUT.ICO [--preview PNG]

OUT.ICO holds 16, 20, 24, 32, 40, 48, 64 (32-bit BMP images) and 96, 256 (PNG).  Needs numpy + Pillow.
patch_icon.py puts the icon into the patched exes.
"""
import argparse
import io
import struct

import numpy as np
from PIL import Image, ImageFilter

SIZES_BMP = [16, 20, 24, 32, 40, 48, 64]
SIZES_PNG = [96, 256]
SS = 4                     # supersampling factor
SEED = 1997


# ---------------------------------------------------------------------------------------------
# helpers: coordinates in the 32-unit grid of the original icon
# ---------------------------------------------------------------------------------------------
def grid(n):
    """Sample centres of an n*SS square raster, in units (0..32)."""
    m = n * SS
    c = (np.arange(m) + 0.5) * (32.0 / m)
    return np.meshgrid(c, c)           # x, y


def rects_mask(x, y, rects):
    m = np.zeros(x.shape, bool)
    for x0, y0, x1, y1 in rects:
        m |= (x >= x0) & (x < x1) & (y >= y0) & (y < y1)
    return m


def rects_distance(x, y, rects):
    """Distance (units) from each sample to the union of axis-aligned rects (0 inside)."""
    d = np.full(x.shape, 1e9)
    for x0, y0, x1, y1 in rects:
        dx = np.maximum(np.maximum(x0 - x, 0), x - x1)
        dy = np.maximum(np.maximum(y0 - y, 0), y - y1)
        d = np.minimum(d, np.hypot(dx, dy))
    return d


def smooth(e0, e1, v):
    t = np.clip((v - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


# fixed-seed 3-D value noise, evaluated on the sphere so the pattern has no seam
_rng = np.random.RandomState(SEED)
_PERM = _rng.permutation(256)
_VALS = _rng.rand(256)


def _hash(ix, iy, iz):
    return _VALS[_PERM[(_PERM[(_PERM[ix & 255] + iy) & 255] + iz) & 255]]


def value_noise(px, py, pz):
    ix, iy, iz = np.floor(px).astype(int), np.floor(py).astype(int), np.floor(pz).astype(int)
    fx, fy, fz = px - ix, py - iy, pz - iz
    ux, uy, uz = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy), fz * fz * (3 - 2 * fz)
    out = 0
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                w = (ux if dx else 1 - ux) * (uy if dy else 1 - uy) * (uz if dz else 1 - uz)
                out = out + w * _hash(ix + dx, iy + dy, iz + dz)
    return out


def fbm(px, py, pz, octaves=5):
    total, amp, freq, norm = 0, 1.0, 1.0, 0
    for _ in range(octaves):
        total = total + amp * value_noise(px * freq + 11.3, py * freq + 5.7, pz * freq + 2.1)
        norm += amp
        amp *= 0.5
        freq *= 2.03
    return total / norm


# ---------------------------------------------------------------------------------------------
# the design
# ---------------------------------------------------------------------------------------------
D_RECTS = [(5, 4, 13, 5), (5, 8, 13, 9), (7, 4, 8, 9), (13, 5, 14, 8)]
C_RECTS = [(18, 4, 25, 5), (18, 8, 25, 9), (17, 5, 18, 8), (24, 5, 25, 6), (24, 7, 25, 8)]
LETTERS = D_RECTS + C_RECTS
PLANET_C = (17.0, 34.0)
PLANET_R = 22.0
LIGHT = np.array([0.86, -0.30, 0.42])
LIGHT = LIGHT / np.linalg.norm(LIGHT)


def frame_metrics(n):
    """Pixel geometry of the frame for an n-pixel icon, snapped to whole pixels (icon practice: line
    widths are chosen per size, not scaled - a frame scaled from the 32x32 original would be 16 px
    wide at 256 and eat the picture).  Returns margin, outer ring, inner ring, corner radius, hairline."""
    margin = 0 if n < 48 else max(1, round(n / 64))          # transparent border: 1 px at 48/64, 2 at 96, 4 at 256
    if n <= 24:
        outer, inner = 0, 1                                    # one light ring at the smallest sizes
    elif n <= 64:
        outer, inner = 1, 1
    else:
        outer, inner = max(1, round(n / 128)), max(1, round(n / 96))   # 256: 2 + 3 px
    radius = 0.0 if n <= 20 else n * 0.055                    # softly rounded corners
    hairline = n >= 96                                         # 1-px dark line between the ring and the field
    return margin, outer, inner, radius, hairline


def rrect_dist(px, py, x0, y0, x1, y1, r):
    """Signed distance (pixels) to a rounded rectangle, negative inside."""
    cx, cy, hx, hy = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    r = min(r, hx, hy)
    qx, qy = np.abs(px - cx) - (hx - r), np.abs(py - cy) - (hy - r)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r


def render(n):
    """RGBA float image (n*SS square, 0..1), colours NOT premultiplied."""
    m = n * SS
    pc = (np.arange(m) + 0.5) / SS                                   # sample centres in pixels
    px, py = np.meshgrid(pc, pc)
    margin, w_out, w_in, radius, hairline = frame_metrics(n)
    lo, hi = margin, n - margin
    d_outer = rrect_dist(px, py, lo, lo, hi, hi, radius)
    f = w_out + w_in
    d_field = rrect_dist(px, py, lo + f, lo + f, hi - f, hi - f, max(0.0, radius - f))
    # the design lives in the field: original units 2..30 map onto it
    a0, a1 = lo + f, hi - f
    x = 2 + (px - a0) * 28.0 / (a1 - a0)
    y = 2 + (py - a0) * 28.0 / (a1 - a0)
    px_per_unit = SS * (a1 - a0) / 28.0                               # supersamples per design unit
    small = n < 32                                                    # simplified drawing for 16/20/24
    img = np.zeros(x.shape + (3,))
    field = d_field < 0

    # --- planet
    dx, dy = (x - PLANET_C[0]) / PLANET_R, (y - PLANET_C[1]) / PLANET_R
    rr = dx * dx + dy * dy
    nz = np.sqrt(np.clip(1 - rr, 0, 1))
    nx, ny = dx, dy
    lam = nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]
    day = smooth(-0.12, 0.55, lam)                                   # soft terminator
    # surface: plains and highlands, dark maria; the small sizes keep only the large features
    sc = 2.6
    base = fbm(nx * sc, ny * sc, nz * sc, octaves=3 if small else 5)
    if small:
        alb = smooth(0.30, 0.72, base)
    else:
        detail = fbm(nx * sc * 3.1 + 7, ny * sc * 3.1, nz * sc * 3.1, octaves=4)
        alb = smooth(0.30, 0.72, 0.55 * base + 0.45 * detail)
    maria = smooth(0.52, 0.60, fbm(nx * 1.7 + 3, ny * 1.7, nz * 1.7, octaves=3))
    alb = alb * (1 - 0.55 * maria)
    dark = np.array([0.30, 0.0, 0.0])
    mid = np.array([0.62, 0.02, 0.01])
    bright = np.array([1.0, 0.10, 0.04])
    col = (dark[None, None, :] * (1 - alb[..., None]) + mid[None, None, :] * alb[..., None])
    col = col * (1 - smooth(0.62, 0.95, alb)[..., None]) + bright[None, None, :] * smooth(0.62, 0.95, alb)[..., None]
    shade = 0.03 + 1.05 * day * (0.55 + 0.45 * np.clip(lam, 0, 1))
    col = col * shade[..., None]
    rim = smooth(0.80, 1.0, np.sqrt(rr)) * smooth(0.05, 0.6, lam)   # thin bright atmosphere on the lit limb
    col = col + rim[..., None] * np.array([0.55, 0.06, 0.02])[None, None, :]
    pm = (field & (rr < 1))[..., None].astype(float)
    img = img * (1 - pm) + np.clip(col, 0, 1) * pm

    # --- letters: solid 3-D blocks.  The original's dark-grey pixels right of and below every stroke
    # are its extrusion; here the letters are extruded EXTRUDE units towards the bottom right (side
    # walls shaded by the direction they face), cast a soft shadow, and carry a chamfered front face
    # lit from the top left.  Below 32 px: flat face and a one-step drop instead (no sub-pixel detail).
    R = 0.5                                                          # face half-width beyond the unit strokes (rounded corners)
    EXTRUDE = np.array([0.55, 0.85])                                  # depth vector, units
    dist = rects_distance(x, y, LETTERS)
    front = dist <= R
    if not small:
        sh = rects_distance(x - 1.0, y - 1.5, LETTERS) <= R + 0.2    # soft cast shadow behind the block
        shb = np.array(Image.fromarray((sh * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.5 * px_per_unit)), float) / 255.0
        img = img * (1 - 0.8 * shb[..., None])
    steps = 1 if small else max(8, int(np.hypot(*EXTRUDE) * px_per_unit))
    for i in range(steps, 0, -1):
        t = i / steps
        ox, oy = EXTRUDE * t
        d_i = rects_distance(x - ox, y - oy, LETTERS)
        wall = (d_i <= R) & ~front
        if small:
            img[wall] = 0.30
            continue
        # which way this wall faces: compare the distance field a little to the right and below
        dr = rects_distance(x - ox - 0.15, y - oy, LETTERS) - d_i
        dd = rects_distance(x - ox, y - oy - 0.15, LETTERS) - d_i
        facing_down = np.clip(-dd / 0.15, 0, 1)
        facing_right = np.clip(-dr / 0.15, 0, 1)
        g = 0.34 + 0.20 * facing_down - 0.10 * facing_right - 0.10 * t
        img[wall] = np.clip(g[wall], 0.12, 1)[:, None]
    if small:
        img[front] = 0.86
    else:
        # chamfered front face: height from the distance field, normals from its gradient
        height = np.clip((R - dist) / 0.42, 0, 1)                    # 0 at the outline, 1 on the flat top
        hb = np.array(Image.fromarray((height * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(max(0.5, 0.10 * px_per_unit))), float) / 255.0
        gy, gx = np.gradient(hb)
        k = 0.42 / px_per_unit
        nrm = np.dstack([-gx / k, -gy / k, np.ones(hb.shape)])
        nrm /= np.linalg.norm(nrm, axis=2, keepdims=True)
        lt = np.array([-0.50, -0.62, 0.60]); lt /= np.linalg.norm(lt)
        lit = np.clip(nrm @ lt, 0, 1)
        hv = lt + np.array([0, 0, 1.0]); hv /= np.linalg.norm(hv)
        spec = np.clip(nrm @ hv, 0, 1) ** 40
        grey = 0.22 + 0.70 * lit + 0.35 * spec
        grey = grey - 0.06 * (y - 4) / 5.0                           # brushed metal: a touch darker towards the bottom
        img[front] = np.clip(grey[front], 0, 1)[:, None]
    img[~field] = 0

    # --- frame: a thin bevelled bezel in whole pixels - dark outer edge, light inner ring lit from the
    # top left, and at the large sizes a dark hairline where the ring meets the black field
    inside = d_outer < 0
    if w_out:
        ring_in = inside & ~field & (d_outer < -w_out)
    else:
        ring_in = inside & ~field
    ring_out = inside & ~field & ~ring_in
    tl = (px + py) / (2.0 * n)                                        # 0 top left .. 1 bottom right
    img[ring_out] = (0.40 - 0.08 * tl[ring_out])[:, None]
    img[ring_in] = (0.86 - 0.26 * tl[ring_in])[:, None]
    if hairline:
        hl = field & (d_field > -1.0)
        img[hl] = img[hl] * 0.35 + 0.08
    alpha = inside.astype(float)
    return np.dstack([img, alpha])


def to_image(img, n):
    """Box-filter the supersamples with premultiplied alpha (so the anti-aliased rounded edge keeps
    its grey instead of darkening), then return a straight-alpha RGBA image."""
    a = np.clip(img, 0, 1)
    rgb, al = a[..., :3] * a[..., 3:4], a[..., 3:4]
    rgb = rgb.reshape(n, SS, n, SS, 3).mean(axis=(1, 3))
    al = al.reshape(n, SS, n, SS, 1).mean(axis=(1, 3))
    rgb = np.where(al > 0, rgb / np.maximum(al, 1e-6), 0)
    out = np.dstack([rgb, al])
    return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), 'RGBA')


def bmp_entry(im):
    """ICO image: BITMAPINFOHEADER (height doubled) + 32-bit BGRA rows bottom-up + AND mask."""
    n = im.size[0]
    rgba = np.array(im.convert('RGBA'))
    bgra = rgba[:, :, [2, 1, 0, 3]][::-1].tobytes()
    mask_row = ((n + 31) // 32) * 4
    mask = bytearray()
    for row in rgba[::-1]:
        bits = np.packbits((row[:, 3] == 0).astype(np.uint8))       # 1 = transparent, for renderers without alpha
        mask += bits.tobytes() + b'\0' * (mask_row - len(bits))
    hdr = struct.pack('<IiiHHIIiiII', 40, n, 2 * n, 1, 32, 0, len(bgra) + len(mask), 0, 0, 0, 0)
    return hdr + bgra + bytes(mask)


def png_entry(im):
    buf = io.BytesIO()
    im.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def build_ico():
    """[(size, image bytes)] in ascending size, and the ICO file bytes."""
    imgs = []
    for n in sorted(SIZES_BMP + SIZES_PNG):
        im = to_image(render(n), n)
        imgs.append((n, im, bmp_entry(im) if n in SIZES_BMP else png_entry(im)))
    head = struct.pack('<HHH', 0, 1, len(imgs))
    off = 6 + 16 * len(imgs)
    dirs, blobs = b'', b''
    for n, _, data in imgs:
        dirs += struct.pack('<BBBBHHII', n % 256, n % 256, 0, 0, 1, 32, len(data), off + len(blobs))
        blobs += data
    return imgs, head + dirs + blobs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('out')
    ap.add_argument('--preview', help='also write a PNG sheet: every size 1:1 plus the 256 image and the original 32x32 enlarged')
    a = ap.parse_args()
    imgs, ico = build_ico()
    open(a.out, 'wb').write(ico)
    print(f'wrote {a.out}: {len(ico)} bytes, sizes ' + ', '.join(f'{n}{"(png)" if n in SIZES_PNG else ""}' for n, _, _ in imgs))
    if a.preview:
        # every size 1:1 on a light and on a dark strip (icons must work on both), then the 256 image on
        # both backgrounds and the original 32x32 enlarged for comparison
        small = [(n, im) for n, im, _ in imgs if n < 256]
        W = max(16 + sum(n + 16 for n, _ in small), 16 + 3 * (256 + 16))
        H = 2 * (16 + 96) + 16 + 256 + 16
        sheet = Image.new('RGB', (W, H), (240, 240, 240))
        sheet.paste((30, 30, 30), (0, 16 + 96 + 8, W, 2 * (16 + 96) + 8))
        for row, y0 in ((0, 16), (1, 16 + 96 + 16)):
            xx = 16
            for n, im in small:
                sheet.paste(im, (xx, y0 + 96 - n), im); xx += n + 16
        big = imgs[-1][1]
        y0 = 2 * (16 + 96) + 16
        sheet.paste(big, (16, y0), big)
        sheet.paste((30, 30, 30), (16 + 256 + 8, y0 - 8, 16 + 2 * 256 + 24, y0 + 256 + 8))
        sheet.paste(big, (16 + 256 + 16, y0), big)
        try:
            import os
            orig = os.path.join(os.path.dirname(os.path.abspath(a.out)), 'DC.ICO')
            o = Image.open(orig).convert('RGB').resize((256, 256), Image.NEAREST)
            sheet.paste(o, (16 + 2 * (256 + 16), y0))
        except Exception:
            pass
        sheet.save(a.preview)
        print('preview', a.preview)


if __name__ == '__main__':
    main()

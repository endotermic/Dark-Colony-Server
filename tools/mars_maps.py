#!/usr/bin/env python3
"""Derive the Mars surface maps that paint_intro.py wraps on the main-menu planet (doc 10.57).

Sources (both public domain, downloaded 1 Oct 2026 at the maintainer's request - "take a real
Martian topology and wrap on the sphere"):

* elevation: MOLA MEGDR `megt90n000eb.img` + `.lbl` (NASA PDS Geosciences Node,
  pds-geosciences.wustl.edu/mgs/mgs-m-mola-5-megdr-l3-v1/mgsl_300x/meg016/): 5760 x 2880, 16 px per
  degree, MSB 16-bit metres above the areoid, simple cylindrical, longitude 0..360 east from the
  left edge, latitude +90 at the top, 33 MB;
* colour: USGS Astrogeology "Mars Viking Colorized Global Mosaic 232m", the 1 km JPEG
  `mars_viking_mdim21_clrmosaic_1km.jpg` (astrogeology.usgs.gov/search/map/
  mars_viking_colorized_global_mosaic_232m): 21339 x 10670, simple cylindrical, planetocentric,
  37 MB. Its longitude origin is detected (0..360 or -180..180) by finding Syrtis Major, the
  darkest albedo feature, at 8 N 70 E and rolled to 0..360 east.

Output (committed in tools/mars/, ~4 MB, so the render is reproducible from the repository):

* `mola_height_4096.png`  - 4096 x 2048 16-bit greyscale, metres = value * scale + offset
                            (`mars_maps.json`), area-averaged from the 16 ppd grid;
* `viking_color_4096.jpg` - 4096 x 2048 RGB, Lanczos-reduced, quality 92;
* `mars_maps.json`        - scale / offset, source names, the detected longitude shift.

CLI
    python mars_maps.py prepare SRC_DIR [--out tools/mars] [--size 4096]
    python mars_maps.py info [--maps tools/mars]
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, 'mars')
HEIGHT_NAME = 'mola_height_4096.png'
COLOR_NAME = 'viking_color_4096.jpg'
META_NAME = 'mars_maps.json'
MOLA_IMG = 'megt90n000eb.img'
VIKING_JPG = 'mars_viking_mdim21_clrmosaic_1km.jpg'
MOLA_W, MOLA_H = 5760, 2880
SYRTIS_LAT, SYRTIS_LON = 8.0, 70.0         # the dark albedo feature used to detect the colour map's origin


def need():
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        sys.exit('this tool needs NumPy and Pillow: pip install numpy Pillow')
    Image.MAX_IMAGE_PIXELS = None
    return np, Image


def load_mola(np, path):
    raw = np.fromfile(path, dtype='>i2')
    if raw.size != MOLA_W * MOLA_H:
        raise SystemExit('%s: %d samples, expected %d' % (path, raw.size, MOLA_W * MOLA_H))
    return raw.reshape(MOLA_H, MOLA_W).astype(np.float32)


def area_mean(np, a, w, h):
    """Reduce a 2-D array to w x h by averaging whole blocks (the source must be a multiple)."""
    H, W = a.shape[:2]
    if W % w or H % h:
        raise ValueError('cannot block-average %dx%d to %dx%d' % (W, H, w, h))
    fy, fx = H // h, W // w
    if a.ndim == 2:
        return a.reshape(h, fy, w, fx).mean(axis=(1, 3))
    return a.reshape(h, fy, w, fx, a.shape[2]).mean(axis=(1, 3))


def detect_shift(np, rgb):
    """0 when the colour mosaic starts at 0 E, else the column roll that brings it to 0..360 east:
    Syrtis Major (dark) must be darker than the same spot shifted by 180 degrees."""
    h, w = rgb.shape[:2]
    lum = rgb.astype(float).mean(-1)
    y = int((90 - SYRTIS_LAT) / 180 * h)
    box = max(2, w // 72)                       # +-5 degrees
    def patch(lon):
        x = int((lon % 360) / 360 * w)
        xs = np.arange(x - box, x + box) % w
        return lum[max(0, y - box):y + box][:, xs].mean()
    at0 = patch(SYRTIS_LON)                     # map starting at 0 E
    at180 = patch(SYRTIS_LON + 180)             # map starting at -180 E (centre 0)
    return 0 if at0 < at180 else w // 2, at0, at180


def cmd_prepare(args):
    np, Image = need()
    size_w, size_h = args.size, args.size // 2
    os.makedirs(args.out, exist_ok=True)
    # --- elevation
    mola = load_mola(np, os.path.join(args.src, MOLA_IMG))
    # 5760 -> 4096 is not a whole ratio: average to 1152 x 576 blocks? No - resample with Pillow's
    # box filter on the float grid instead (area average), which handles any ratio.
    him = Image.fromarray(mola, 'F').resize((size_w, size_h), Image.BOX)
    hgt = np.asarray(him, dtype=np.float32)
    hmin, hmax = float(hgt.min()), float(hgt.max())
    scale = (hmax - hmin) / 65535.0
    q = np.clip(np.round((hgt - hmin) / scale), 0, 65535).astype(np.uint16)
    Image.fromarray(q).save(os.path.join(args.out, HEIGHT_NAME), optimize=True)   # uint16 -> I;16, zlib level 9
    # Hellas, the lowest point, as a sanity check of the longitude convention
    iy, ix = np.unravel_index(hgt.argmin(), hgt.shape)
    low_lat, low_lon = 90 - (iy + 0.5) / size_h * 180, (ix + 0.5) / size_w * 360
    # --- colour
    vk = Image.open(os.path.join(args.src, VIKING_JPG)).convert('RGB')
    vk = vk.resize((size_w, size_h), Image.LANCZOS)
    rgb = np.asarray(vk)
    shift, at0, at180 = detect_shift(np, rgb)
    if shift:
        rgb = np.roll(rgb, -shift, axis=1)
    Image.fromarray(rgb, 'RGB').save(os.path.join(args.out, COLOR_NAME), quality=92, optimize=True)
    meta = dict(height=dict(file=HEIGHT_NAME, width=size_w, height=size_h, unit='metre',
                            scale=scale, offset=hmin, source='MOLA MEGDR megt90n000eb.img (NASA PDS, 16 ppd)',
                            lowest_point=dict(lat=round(low_lat, 1), lon_east=round(low_lon, 1), metres=round(hmin))),
                color=dict(file=COLOR_NAME, width=size_w, height=size_h,
                           source='USGS Mars Viking Colorized Global Mosaic 232m, 1 km JPEG',
                           source_origin='-180..180 (rolled by %d px)' % shift if shift else '0..360',
                           syrtis_luminance=dict(at_0_360=round(at0, 1), at_m180_180=round(at180, 1))),
                convention='simple cylindrical, longitude 0..360 east from the left edge, latitude +90 at the top',
                licence='NASA / USGS public domain')
    with open(os.path.join(args.out, META_NAME), 'w') as fh:
        json.dump(meta, fh, indent=2)
    print('wrote %s, %s, %s' % (HEIGHT_NAME, COLOR_NAME, META_NAME))
    print('  height %.0f .. %.0f m (lowest point %.1f N %.1f E - Hellas is 42 S 70 E)' % (hmin, hmax, low_lat, low_lon))
    print('  colour origin: %s (Syrtis luminance %.1f vs %.1f)' % (meta['color']['source_origin'], at0, at180))


def load_maps(maps_dir=DEFAULT_OUT):
    """(height metres float32 HxW, colour float HxWx3 in 0..1, meta) for paint_intro.py."""
    np, Image = need()
    with open(os.path.join(maps_dir, META_NAME)) as fh:
        meta = json.load(fh)
    q = np.asarray(Image.open(os.path.join(maps_dir, HEIGHT_NAME)), dtype=np.float32)
    hgt = q * meta['height']['scale'] + meta['height']['offset']
    rgb = np.asarray(Image.open(os.path.join(maps_dir, COLOR_NAME)).convert('RGB'), dtype=np.float32) / 255.0
    return hgt, rgb, meta


def cmd_info(args):
    hgt, rgb, meta = load_maps(args.maps)
    print(json.dumps(meta, indent=2))
    print('height %dx%d %.0f..%.0f m; colour %dx%d mean %s' % (hgt.shape[1], hgt.shape[0], hgt.min(), hgt.max(),
                                                               rgb.shape[1], rgb.shape[0], rgb.mean((0, 1)).round(3)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('src', help='folder with %s and %s' % (MOLA_IMG, VIKING_JPG))
    p.add_argument('--out', default=DEFAULT_OUT)
    p.add_argument('--size', type=int, default=4096, help='output width (height = width / 2)')
    i = sub.add_parser('info')
    i.add_argument('--maps', default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    return {'prepare': cmd_prepare, 'info': cmd_info}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())

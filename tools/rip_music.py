#!/usr/bin/env python3
"""Rip and validate the CD soundtrack of Dark Colony / Council Wars from a raw `.bin` disc image.

Both discs are mixed-mode: track 1 data (2352-byte raw MODE1 sectors, 12-byte sync `00 FF*10 00`),
tracks 2..5 Red Book audio (2352 bytes = 588 stereo 16-bit LE frames per sector).  There is no
`.cue`, so the track boundaries are the runs of digital silence between the pieces.

    rip_music.py scan IMAGE.bin                      audio range, tracks, glitch report per track
    rip_music.py rip  IMAGE.bin OUTDIR [--names T]    slice, trim, encode (LAME CBR 192) and verify
    rip_music.py check FILE.mp3 ...                   frame-stream integrity + decoded-audio scan

The glitch report covers what a bad rip does to CD audio: (1) STRAY BLOCKS - runs of sectors that
are byte-identical to sectors elsewhere in the track (the drive served its read-ahead cache for the
wrong address; the audio that belonged there is NOT in the image and only a new rip of the disc
brings it back); (2) SPLICES - waveform discontinuities at a fixed sample phase in many sectors
(jitter between read chunks); (3) junk before the music (garbage in the pregap) and digital
silence inside a track.  Trimming rule: a track starts where the last >= 20 ms run of digital
silence inside its first second ends (drops pregap junk and the pressing's lead-in silence) and
ends where the trailing silence begins.

Needs numpy; `rip` needs `lameenc` (pip install lameenc); the MP3 verification needs `miniaudio`
(pip install miniaudio) and is skipped without it.  Written 29 Sep 2026 after the shipped Dark
Colony set turned out to carry the image's stray blocks (DC16_DISPLAY_AND_RESOLUTION.md 10.52).
"""
import argparse
import os
import sys

import numpy as np

SECTOR = 2352
FRAMES = 588
SR = 44100
SYNC = bytes([0]) + bytes([0xFF]) * 10 + bytes([0])
SILENT = 3                 # |sample| below this on both channels = digital silence (dither-free masters)
GAP_SECTORS = 150          # >= 2 s of silence separates two tracks


# ----------------------------------------------------------------------------- image access
def audio_range(path):
    """(first audio sector, sector count) - the first sector after the data track without the sync."""
    size = os.path.getsize(path)
    n = size // SECTOR
    with open(path, 'rb') as f:
        lo, hi = 0, n
        # the data track is a prefix: binary search for its end (data sectors carry the sync)
        while lo < hi:
            mid = (lo + hi) // 2
            f.seek(mid * SECTOR)
            if f.read(12) == SYNC:
                lo = mid + 1
            else:
                hi = mid
    return lo, n


def read_pcm(path, first, count):
    with open(path, 'rb') as f:
        f.seek(first * SECTOR)
        data = f.read(count * SECTOR)
    return np.frombuffer(data, dtype='<i2').reshape(-1, 2)


def silence_runs(x, min_frames):
    """[(start frame, end frame)) runs where both channels are below SILENT for >= min_frames."""
    q = np.abs(x).max(axis=1) < SILENT
    d = np.diff(np.concatenate([[0], q.astype(np.int8), [0]]))
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    return [(int(s), int(e)) for s, e in zip(starts, ends) if e - s >= min_frames]


def find_tracks(x, first_sector):
    """Track sector ranges [first, last] separated by >= GAP_SECTORS of digital silence."""
    gaps = silence_runs(x, GAP_SECTORS * FRAMES)
    tracks = []
    pos = 0
    for s, e in gaps:
        if s > pos:
            tracks.append((pos, s))
        pos = e
    if pos < len(x):
        tracks.append((pos, len(x)))
    out = []
    for a, b in tracks:
        # drop pieces shorter than 20 s (noise between gaps)
        if b - a >= 20 * SR:
            out.append((first_sector + a // FRAMES, first_sector + (b - 1) // FRAMES))
    return out


def trim(x):
    """(start, end) frames of the music: after the last >= 20 ms silence of the first second, before the trailing silence."""
    start = 0
    for s, e in silence_runs(x[:SR], SR // 50):
        start = e
    end = len(x)
    nz = np.nonzero(np.abs(x).max(axis=1) >= SILENT)[0]
    if len(nz):
        end = int(nz[-1]) + 1
    return start, end


# ----------------------------------------------------------------------------- glitch report
def stray_blocks(x):
    """Runs of sectors byte-identical to sectors elsewhere: [(first sector, last sector, distance)]."""
    n = len(x) // FRAMES
    sect = x[:n * FRAMES].reshape(n, FRAMES * 2)
    loud = np.abs(sect).max(axis=1) > 40
    seen = {}
    pairs = []
    for i in np.nonzero(loud)[0]:
        key = sect[i].tobytes()
        if key in seen:
            pairs.append((seen[key], int(i)))
        else:
            seen[key] = int(i)
    pairs.sort()
    runs = []
    for a, b in pairs:
        if runs and a == runs[-1][1] + 1 and b - a == runs[-1][2]:
            runs[-1][1] = a
        else:
            runs.append([a, a, b - a])
    return [(a, b, d) for a, b, d in runs if b - a + 1 >= 3]


def splices(x):
    """Per-sector-phase discontinuity statistics: (phase, mean error at it, median over phases, sectors above 8x)."""
    n = len(x) // FRAMES
    m = x[:n * FRAMES].astype(np.int32).sum(axis=1).reshape(n, FRAMES)
    e = np.abs(m[:, 2:] - 2 * m[:, 1:-1] + m[:, :-2])
    by_phase = e.mean(axis=0)
    local = e.mean(axis=1) + 1
    counts = ((e / local[:, None]) > 8).sum(axis=0)        # sectors whose error at this phase is 8x their own mean
    k = int(counts.argmax())
    others = np.delete(counts, k)
    # dense music makes every phase noisy (Council Wars track 3: median 14, 99th percentile 71, worst 81),
    # a rip splice stands alone (Dark Colony track 4: worst 75, every other phase below 6)
    ref = int(np.percentile(others, 99))
    return k + 2, float(by_phase[k]), float(np.median(by_phase)), int(counts[k]), ref


def report(x, label, decoded=False):
    """Print the glitch report; `decoded` = MP3 output, where byte-identical sectors and the sector
    phase no longer exist (those two scans need the raw image - use `scan`)."""
    start, end = trim(x)
    print('%s: %.2f min, peak %d, rms %.0f' % (label, len(x) / SR / 60, int(np.abs(x).max()), np.sqrt((x.astype(np.float64) ** 2).mean())))
    print('   trim: music from %.3f s to %.3f s (%.3f s cut at the head, %.3f s at the tail)' % (
        start / SR, end / SR, start / SR, (len(x) - end) / SR))
    # hard clipping in the material itself: samples sitting on a ceiling (Council Wars tracks 2 and 4
    # have 6 % / 3.5 % of their samples flat at -3 dBFS = 23196, plateaus up to 63 samples - the master)
    a = np.abs(x[start:end].astype(np.int32))
    ceiling = int(a.max())
    flat = np.diff(x[start:end, 0].astype(np.int32)) == 0
    near = a[1:, 0] >= ceiling - 3
    plateau = flat & near
    d = np.diff(np.concatenate([[0], plateau.astype(np.int8), [0]]))
    longest = int((np.nonzero(d == -1)[0] - np.nonzero(d == 1)[0]).max()) + 1 if plateau.any() else 0
    pct = float((a >= ceiling - 3).mean() * 100)
    if pct > 0.5:
        print('   CLIPPED MATERIAL: %.1f%% of the samples sit on the ceiling %d (%.1f dBFS), plateaus up to %d samples - the master, not the rip' % (
            pct, ceiling, 20 * np.log10(ceiling / 32768), longest))
    else:
        print('   level: peak %d (%.1f dBFS), %.3f%% of the samples on it' % (ceiling, 20 * np.log10(max(ceiling, 1) / 32768), pct))
    inner = [(s, e) for s, e in silence_runs(x[start:end], SR // 50)]
    print('   digital silence >= 20 ms inside the music: %d %s' % (
        len(inner), ' '.join('%.1fs/%dms' % ((start + s) / SR, (e - s) * 1000 // SR) for s, e in inner[:6])))
    if decoded:
        print('   (stray blocks and splices can only be scanned on the raw image - run `scan` on the .bin)')
        return start, end, [], 0, 0
    runs = stray_blocks(x)
    lost = sum(b - a + 1 for a, b, _ in runs) * FRAMES / SR
    if runs:
        dists = sorted(set(d for _, _, d in runs))
        print('   STRAY BLOCKS: %d runs, %.1f s = %.1f%% of the track is a copy of audio %s sectors away'
              ' (the audio that belonged there is not in the image); first %s' % (
                  len(runs), lost, lost / (len(x) / SR) * 100, dists if len(dists) < 4 else '%d..%d' % (dists[0], dists[-1]),
                  ' '.join('%d..%d' % (a, b) for a, b, _ in runs[:5])))
    else:
        print('   stray blocks: none')
    phase, val, med, bad, ref = splices(x)
    if bad > 2 * ref + 4:
        print('   SPLICES: %d sectors break at sample phase %d (mean error %.0f vs median %.0f; the 99th percentile of the other phases is %d) - jitter between read chunks' % (
            bad, phase, val, med, ref))
    else:
        print('   splices: none (worst phase %d: %d sectors, 99th percentile of the other phases %d)' % (phase, bad, ref))
    return start, end, runs, bad, ref


# ----------------------------------------------------------------------------- commands
def cmd_scan(args):
    first, n = audio_range(args.image)
    print('%s: %d sectors, audio from sector %d (%.1f min)' % (os.path.basename(args.image), n, first, (n - first) * FRAMES / SR / 60))
    x = read_pcm(args.image, first, n - first)
    tracks = find_tracks(x, first)
    print('tracks (sector ranges by silence gaps): %s' % ', '.join('%d..%d' % t for t in tracks))
    for i, (a, b) in enumerate(tracks, 2):
        report(x[(a - first) * FRAMES:(b - first + 1) * FRAMES], 'track %d (sectors %d..%d)' % (i, a, b))
    return 0


def encode(pcm, path, kbps):
    import lameenc
    enc = lameenc.Encoder()
    enc.set_bit_rate(kbps)
    enc.set_in_sample_rate(SR)
    enc.set_channels(2)
    enc.set_quality(2)
    enc.set_vbr(0) if hasattr(enc, 'set_vbr') else None
    out = bytearray()
    step = FRAMES * 100
    raw = np.ascontiguousarray(pcm, dtype='<i2').tobytes()
    for i in range(0, len(raw), step * 4):
        out += enc.encode(raw[i:i + step * 4])
    out += enc.flush()
    with open(path, 'wb') as f:
        f.write(out)
    return len(out)


def decode_mp3(path):
    try:
        import miniaudio
    except ImportError:
        return None
    dec = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=2, sample_rate=SR)
    return np.frombuffer(dec.samples, dtype='<i2').reshape(-1, 2)


def verify_mp3(path, pcm):
    """Decode and compare with the source: alignment, SNR, windows near the signal level."""
    mp3 = decode_mp3(path)
    if mp3 is None:
        print('   (miniaudio not installed - decoded check skipped)')
        return True
    n = min(len(pcm), SR * 8)
    a = pcm[:n].astype(np.float64).mean(axis=1); a -= a.mean()
    b = mp3[:n + 4096].astype(np.float64).mean(axis=1); b -= b.mean()
    L = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(b, L) * np.conj(np.fft.rfft(a, L)), L)
    off = int(np.argmax(c[:4096]))
    m = min(len(pcm), len(mp3) - off)
    d = mp3[off:off + m].astype(np.float64) - pcm[:m].astype(np.float64)
    win = SR // 10
    k = m // win
    sig = np.sqrt((pcm[:k * win].astype(np.float64) ** 2).reshape(k, -1).mean(axis=1))
    res = np.sqrt((d[:k * win] ** 2).reshape(k, -1).mean(axis=1))
    snr = 20 * np.log10((sig + 1) / (res + 1))
    loud = sig > 100
    worst = float(snr[loud].min()) if loud.any() else 99.0
    total = 20 * np.log10(np.sqrt((pcm[:m].astype(np.float64) ** 2).mean()) / (np.sqrt((d ** 2).mean()) + 1e-9))
    ok = worst > 3 and total > 12
    print('   decoded: %d frames (source %d, decoder delay %d), SNR %.1f dB overall, worst loud 100-ms window %.1f dB -> %s' % (
        len(mp3), len(pcm), off, total, worst, 'ok' if ok else 'SUSPECT'))
    return ok


def cmd_rip(args):
    first, n = audio_range(args.image)
    x = read_pcm(args.image, first, n - first)
    tracks = find_tracks(x, first)
    os.makedirs(args.outdir, exist_ok=True)
    problems = 0
    for i, (a, b) in enumerate(tracks, 2):
        seg = x[(a - first) * FRAMES:(b - first + 1) * FRAMES]
        start, end, runs, bad, ref = report(seg, 'track %d (sectors %d..%d)' % (i, a, b))
        pcm = seg[start:end]
        path = os.path.join(args.outdir, args.names % i)
        size = encode(pcm, path, args.bitrate)
        print('   -> %s: %d bytes, %.2f min' % (path, size, len(pcm) / SR / 60))
        if not verify_mp3(path, pcm):
            problems += 1
        if runs or bad > 2 * ref + 4:
            problems += 1
    if problems:
        print('%d track(s) with problems - see the report above' % problems)
    return 1 if problems else 0


def cmd_check(args):
    BR = {1: 32, 2: 40, 3: 48, 4: 56, 5: 64, 6: 80, 7: 96, 8: 112, 9: 128, 10: 160, 11: 192, 12: 224, 13: 256, 14: 320}
    SRS = {0: 44100, 1: 48000, 2: 32000}
    rc = 0
    for path in args.files:
        d = open(path, 'rb').read()
        i = 0
        if d[:3] == b'ID3':
            i = 10 + ((d[6] << 21) | (d[7] << 14) | (d[8] << 7) | d[9])
        frames = bad = 0
        rates = set()
        while i + 4 <= len(d):
            h = d[i:i + 4]
            if h[0] == 0xFF and (h[1] & 0xE0) == 0xE0 and (h[1] >> 3) & 3 == 3 and (h[1] >> 1) & 3 == 1 and BR.get(h[2] >> 4) and SRS.get((h[2] >> 2) & 3):
                br, sr = BR[h[2] >> 4], SRS[(h[2] >> 2) & 3]
                i += 144000 * br // sr + ((h[2] >> 1) & 1)
                frames += 1
                rates.add(br)
            else:
                bad += 1
                i += 1
        print('%s: %d frames (%.2f min), %d bytes outside frames, bitrates %s' % (path, frames, frames * 1152 / SR / 60, bad, sorted(rates)))
        mp3 = decode_mp3(path)
        if mp3 is not None:
            report(mp3, '   decoded', decoded=True)
        if bad:
            rc = 1
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('scan'); s.add_argument('image'); s.set_defaults(fn=cmd_scan)
    r = sub.add_parser('rip'); r.add_argument('image'); r.add_argument('outdir')
    r.add_argument('--names', default='TRACK%02d.MP3', help='file name pattern, %%02d = track number (default TRACK%%02d.MP3)')
    r.add_argument('--bitrate', type=int, default=192); r.set_defaults(fn=cmd_rip)
    c = sub.add_parser('check'); c.add_argument('files', nargs='+'); c.set_defaults(fn=cmd_check)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == '__main__':
    sys.exit(main())

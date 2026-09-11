// Dark Colony sprite / animation data -> JSON for the battle engine (src/engine/anim.js).
// Library + CLI, no dependencies.
//
//   node tools/sprdata2json.js "<game dir>" [--out data/classic/sprites.json] [--pretty] [--verbose]
//
// Reproduces, bit for bit, what Classic dc16.exe derives from ANIM.DAT, ANIMATE/*.FIN, SPRITES/*.SPR
// and the three balance files at start-up / game start, as far as the simulation needs it:
//
//   * ANIM.DAT (start-up reader 0x4051CC): one FIN file name per line; '%' lines and lines shorter
//     than 2 characters are skipped; every line is passed to the FIN loader 0x4255FC ("animate/%s").
//   * FIN container (animate.c 0x4255FC, all integers little-endian):
//       u16 flags (0x1D)  u16 nframes  u16 nanims  u16 nbanks
//       nbanks x char[8]   sprite bank names, opened as "sprites/<name>" (0x42532C; shared by name)
//       nanims x { char[16] name; u16 first_frame; u16 last_frame }         (44-byte header in memory)
//       nframes x { u16 ncells; u16 duration; 8 x { char[16] anim_name; i16 x; i16 y } }   (164 bytes)
//                  = the "frame parts": 8 named sub-animation slots per frame, "NONAME" = empty.
//                  Slot 7 is read by the muzzle-hotspot reader 0x426338, slot 6 by 0x426304.
//       sum(ncells) x { char[8] bank; u16 cell; i16 x; i16 y; u16 a, b, c, d }             (22 bytes)
//                  = the sprite cells drawn for each frame, in frame order (cell records; the last
//                  four words are display-only). Trailing bytes after the last record are ignored.
//     Durations: 0 -> 15, then ticks = trunc(15 * (duration + 3) / 100) (0x425AB6..0x425AE0; the
//     file value is in 1/100 s at the game's 15 ticks per second). Part names are resolved against
//     the animations known SO FAR (all headers of the same file are registered before its frames are
//     read, then the file's frames; earlier files are visible, later files are not); an unknown name
//     silently yields an empty slot. Cell x/y are stored as int16 x*8 and -(y*8).
//   * Animation sets (0x426014): for a base name and a suffix ("TRSC" + "STAND") the 16 file facings
//     "<base><suffix><n>" with n = (12 - i) & 15 for i = 0..15 are looked up; a 32-entry table indexed
//     by the game facing f takes the first existing entry of i = ((0x489628[k] + f + 32) & 31) >> 1 for
//     k = 0.., with the probe table 0, 1, -1, 2, -2, ..., 15, -15, 16. Every set allocation
//     (0x426540) is a distinct record; the engine compares sets by identity (startAnim 0x42626C), so
//     this file gives every allocation its own id, in the original allocation order
//     (0x43C44C: booms 0x43B424, object types 0x43BB80, weapons 0x43B6EC). Set id 0 = NULL.
//   * Bounding boxes (0x425FB4 over the 32 STAND facings, 0x425E8C per animation, union 0x4364AC,
//     widened to +-96 for non-flyers at 0x43BFB5) and the frame-part-6 "aim point" (0x43BF66).
//   * Muzzle hotspots per animation (0x426338, part slot 7): (x*8, -y*8, ticks before the frame).
//
// Names are compared case-sensitively (registry compare = strcmp 0x47C560). See the PORT NOTES of
// src/engine/anim.js for the run-time semantics.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const FORMAT = 'dc16-sprites';
export const FORMAT_VERSION = 1;

/** Probe order of 0x426014 (table 0x489628): facing offsets tried until an existing file facing is found. */
export const FACING_PROBES = [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6, 7, -7, 8, -8, 9, -9, 10, -10, 11, -11,
  12, -12, 13, -13, 14, -14, 15, -15, 16];

const NONAME = 'NONAME';
const i16 = (v) => ((v & 0xffff) << 16) >> 16;

// ---------------------------------------------------------------- file parsers

function cstr(buf, off, len) {
  let end = off;
  while (end < off + len && buf[end] !== 0) end++;
  return buf.toString('latin1', off, end);
}

/** Parse one .FIN file (layout in the header comment). */
export function parseFin(buf) {
  const flags = buf.readUInt16LE(0);
  const nframes = buf.readUInt16LE(2);
  const nanims = buf.readUInt16LE(4);
  const nbanks = buf.readUInt16LE(6);
  let p = 8;
  const banks = [];
  for (let i = 0; i < nbanks; i++, p += 8) banks.push(cstr(buf, p, 8));
  const anims = [];
  for (let i = 0; i < nanims; i++, p += 20) {
    anims.push({ name: cstr(buf, p, 16), first: buf.readUInt16LE(p + 16), last: buf.readUInt16LE(p + 18) });
  }
  const frames = [];
  for (let i = 0; i < nframes; i++) {
    const ncells = buf.readUInt16LE(p);
    const duration = buf.readUInt16LE(p + 2);
    p += 4;
    const parts = [];
    for (let k = 0; k < 8; k++, p += 20) {
      parts.push({ name: cstr(buf, p, 16), x: buf.readInt16LE(p + 16), y: buf.readInt16LE(p + 18) });
    }
    frames.push({ ncells, duration, parts, cells: [] });
  }
  for (const f of frames) {
    for (let c = 0; c < f.ncells; c++, p += 22) {
      if (p + 22 > buf.length) throw new Error('FIN truncated in the cell records');
      f.cells.push({
        bank: cstr(buf, p, 8), cell: buf.readUInt16LE(p + 8), x: buf.readInt16LE(p + 10), y: buf.readInt16LE(p + 12),
        extra: [buf.readUInt16LE(p + 14), buf.readUInt16LE(p + 16), buf.readUInt16LE(p + 18), buf.readUInt16LE(p + 20)],
      });
    }
  }
  return { flags, banks, anims, frames, trailing: buf.length - p };
}

/** Read the header + directory of a .SPR file (docs/DC16_DISPLAY_AND_RESOLUTION.md section 6.3). */
export function parseSprHeader(buf) {
  const flags = buf.readUInt16LE(0);
  const ncells = buf.readUInt16LE(2);
  const cells = [];
  let p = 8 + 768;
  for (let i = 0; i < ncells; i++, p += 8) {
    cells.push({ w: buf.readUInt16LE(p), h: buf.readUInt16LE(p + 2), xoff: buf.readUInt16LE(p + 4), yoff: buf.readUInt16LE(p + 6) });
  }
  return { flags, ncells, cells };
}

/** Balance-file line filter of the loaders: fgets, skip strlen < 3 (incl. '\n') and '%' lines. */
export function dataLines(text) {
  const out = [];
  for (const raw of text.split(/\r?\n/)) {
    if (raw.length < 2) continue;
    if (raw[0] === '%') continue;
    out.push(raw);
  }
  return out;
}

const tokens = (line) => line.trim().split(/\s+/).filter((t) => t.length > 0);

// ---------------------------------------------------------------- the registries

/** Case-insensitive file lookup so the tool also runs on case-sensitive file systems. */
function findFile(dir, name) {
  const direct = path.join(dir, name);
  if (fs.existsSync(direct)) return direct;
  const lower = name.toLowerCase();
  for (const f of fs.readdirSync(dir)) if (f.toLowerCase() === lower) return path.join(dir, f);
  return null;
}

/**
 * Mirrors the animate.c registries: sprite banks by name (0x4D619C), animations by name (0x4D61A8),
 * loaded sequentially in ANIM.DAT order.
 */
export class SpriteData {
  constructor(gameDir) {
    this.gameDir = gameDir;
    this.banks = new Map(); // name -> { name, cells: [{w,h,xoff,yoff}] }
    this.anims = new Map(); // name -> anim record
    this.animList = []; // load order
    this.sets = [null]; // set id -> { name, anims: [32 anim records] }; 0 = NULL
    this.files = [];
    this.warnings = [];
  }

  loadBank(name) {
    let bank = this.banks.get(name);
    if (bank) return bank; // 0x42532C: an already loaded bank is reused
    const file = findFile(path.join(this.gameDir, 'SPRITES'), `${name}.SPR`);
    if (!file) throw new Error(`sprite bank sprites/${name} not found`);
    const hdr = parseSprHeader(fs.readFileSync(file));
    bank = { name, cells: hdr.cells };
    this.banks.set(name, bank);
    return bank;
  }

  /** FIN loader 0x4255FC. */
  loadFin(fileName) {
    const file = findFile(path.join(this.gameDir, 'ANIMATE'), fileName);
    if (!file) throw new Error(`animate/${fileName} not found`);
    const fin = parseFin(fs.readFileSync(file));
    this.files.push(fileName);
    for (const b of fin.banks) this.loadBank(b);
    // animation headers (registered before any frame of this file is read)
    const headers = [];
    for (const a of fin.anims) {
      if (a.first >= fin.frames.length) throw new Error(`Corrupt Animation, start bogus, animation ${a.name}`);
      if (a.last >= fin.frames.length) throw new Error(`Corrupt Animation, end bogus, animation ${a.name}`);
      if (this.anims.has(a.name)) throw new Error(`Hey, I already have animation ${a.name} defined (${fileName})`);
      const rec = { name: a.name, file: fileName, first: a.first, last: a.last, count: a.last - a.first + 1, frames: null };
      this.anims.set(a.name, rec);
      this.animList.push(rec);
      headers.push(rec);
    }
    // frames: duration conversion, part resolution against the registry as it is now, cell records
    const frames = fin.frames.map((f) => {
      if (f.ncells > 100) throw new Error(`${f.ncells} frame parts in frame (${fileName})`);
      let d = f.duration === 0 ? 15 : f.duration;
      d = i16(Math.trunc((15 * (d + 3)) / 100));
      const parts = f.parts.map((pt) => ({
        anim: pt.name === NONAME ? null : this.anims.get(pt.name) ?? null, x: pt.x, y: pt.y,
      }));
      const cells = f.cells.map((c) => {
        const bank = this.banks.get(c.bank);
        if (!bank) throw new Error(`Whoa Batman, I don't have any record of juice file ${c.bank} (${fileName})`);
        if (c.cell >= bank.cells.length) throw new Error(`cell ${c.cell} out of range in bank ${c.bank} (${fileName})`);
        return { bank, cell: c.cell, x8: i16(c.x << 3), y8n: i16(-(c.y << 3)) };
      });
      return { ncells: f.ncells, dur: d, parts, cells };
    });
    for (const h of headers) h.frames = frames.slice(h.first, h.last + 1);
    return fin;
  }

  /** Start-up reader 0x4051CC: every line of ANIM.DAT. */
  loadAnimDat() {
    const file = findFile(this.gameDir, 'ANIM.DAT');
    if (!file) throw new Error('ANIM.DAT not found');
    for (const line of fs.readFileSync(file, 'latin1').split(/\r?\n/)) {
      if (line.length < 2 || line[0] === '%') continue;
      this.loadFin(line.trim());
    }
  }

  lookup(name) {
    return this.anims.get(name) ?? null;
  }

  /** 0x425464(name0, name1): NULL for "NONAME", else the animation named name0+name1. */
  lookup2(name0, name1) {
    const n = name0 + name1;
    if (n === NONAME) return null;
    return this.lookup(n);
  }

  /** 0x43BB30: does any "<base><suffix><n>" exist for n = 0, 2, ..., 30? */
  anyEvenFacing(base, suffix) {
    for (let n = 0; n < 32; n += 2) if (this.lookup(`${base}${suffix}${n}`)) return true;
    return false;
  }

  /** 0x426540: allocate a set record; returns its id (1-based, 0 = NULL). */
  newSet(name0, name1) {
    if (this.sets.length - 1 >= 1000) throw new Error('cset != MAXSETS');
    const id = this.sets.length;
    this.sets.push({ id, name: name0 + name1, anims: new Array(32).fill(null) });
    return id;
  }

  /** 0x426014: fill a set from the 16 file facings of base+suffix with the nearest-facing fallback. */
  buildSet(id, base, suffix) {
    const set = this.sets[id];
    const full = base + suffix;
    if (base.length + suffix.length + 5 >= 32) throw new Error(`strlen(name0)+strlen(name1)+5 < ANIM_NAME_LEN (${full})`);
    const local = [];
    let any = false;
    for (let i = 0; i < 16; i++) {
      const a = this.lookup(`${full}${(12 - i + 16) & 15}`);
      local.push(a);
      if (a) any = true;
    }
    if (!any) throw new Error(`I don't have any of animation set ${full}`);
    for (let f = 0; f < 32; f++) {
      let chosen = null;
      for (let k = 0; k < 32 && !chosen; k++) {
        const e = (FACING_PROBES[k] + f + 32) & 31;
        chosen = local[e >> 1];
      }
      if (!chosen) throw new Error(`Whoa, One of my animation angles is NULL (${full})`);
      set.anims[f] = chosen;
    }
    return id;
  }

  /** newSet + buildSet, the pattern of every object-type / weapon set. */
  makeSet(base, suffix) {
    return this.buildSet(this.newSet(base, suffix), base, suffix);
  }

  /** Boom-type set (0x43B576): one animation looked up by its full name, copied into all 32 facings. */
  makeSingleSet(name) {
    const id = this.newSet(name, '');
    const a = this.lookup2(name, '');
    this.sets[id].anims.fill(a);
    return id;
  }
}

// ---------------------------------------------------------------- geometry (rect.c helpers)

const RECT_EMPTY = [10000, 10000, -10000, -10000]; // 0x425210 and 0x425E9B..0x425EB0

/** 0x4364AC(dst, a, b): dst = { min(a0,b0), min(a1,b1), b2, max(a3,b3) } -- x1 is NOT a max (cmp eax,eax bug). */
function rectUnion(a, b) {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), b[2], Math.max(a[3], b[3])];
}

/** 0x425E8C: bounding rectangle of every cell of every frame of one animation, in position units (<<3). */
export function animRect(anim) {
  let acc = RECT_EMPTY.slice();
  if (!anim) return acc;
  for (const frame of anim.frames) {
    for (let k = 0; k < frame.ncells; k++) {
      const c = frame.cells[k];
      const hdr = c.bank.cells[c.cell];
      const x = c.x8 >> 3;
      const y = -(c.y8n >> 3);
      // 0x4365BC(x0 = xoff + x, y0 = y, w, -h) -> { x0, y, x0 + w, y - h }, then y components negated
      let r = [hdr.xoff + x, y, hdr.xoff + x + hdr.w, y - hdr.h];
      r = [r[0], (-r[1]) | 0, r[2], (-r[3]) | 0];
      // 0x436480: normalise
      if (r[2] < r[0]) [r[0], r[2]] = [r[2], r[0]];
      if (r[3] < r[1]) [r[1], r[3]] = [r[3], r[1]];
      // 0x4365E8(rect, 3): pixels -> 1/256-tile position units
      r = r.map((v) => v << 3);
      acc = rectUnion(acc, r);
    }
  }
  return acc;
}

/** 0x425FB4: union of the 32 facings of a set (facing 31 supplies x1, see rectUnion). */
export function setRect(set) {
  let acc = RECT_EMPTY.slice();
  for (let f = 0; f < 32; f++) acc = rectUnion(acc, animRect(set.anims[f]));
  return acc;
}

/** 0x426338 semantics for one animation: part slot `k` of every frame, with the ticks elapsed before that frame. */
export function partHotspots(anim, k) {
  const out = [];
  let acc = 0;
  for (const frame of anim.frames) {
    const pt = frame.parts[k];
    if (pt.anim) {
      if (out.length >= 8) throw new Error(`offset_pos < MAX_PROBED_OFFSETS (${anim.name})`);
      out.push([pt.x * 8, (-pt.y * 8) | 0, acc]); // | 0: no -0 in the JSON
    }
    acc += frame.dur;
  }
  return out;
}

/** 0x426304: part slot `k` of the first frame as (x, -y), or null. */
export function firstFramePart(anim, k) {
  const pt = anim.frames[0].parts[k];
  return pt.anim ? [pt.x, (-pt.y) | 0] : null;
}

// ---------------------------------------------------------------- the three loaders

/** BOOMSTAT.TXT sprite sets (0x43B424), in file order; returns booms[index] = { size, sets: [ids] }. */
export function loadBooms(sd, text) {
  const lines = dataLines(text);
  let li = 0;
  const count = parseInt(tokens(lines[li++])[0], 10);
  const booms = [];
  for (let n = 0; n < count; n++) {
    const [idx, size] = tokens(lines[li++]).map((t) => parseInt(t, 10));
    if (!(size >= 1)) throw new Error('size >= 1');
    const boom = { index: idx, size, sets: [] };
    for (let k = 0; k < 4; k++) {
      const name = tokens(lines[li++])[0];
      if (name === 'NONE') break;
      boom.sets.push(sd.makeSingleSet(name));
      if (!sd.lookup(name)) sd.warnings.push(`boom ${idx}: animation ${name} not found (set has 32 NULL facings)`);
    }
    li += size; // scatter matrix rows
    li += 3; // 3x3 matrix
    booms[idx] = boom;
  }
  return booms;
}

const POSES = ['MOVE', 'STAND', 'SCRCH', 'BURN', 'FIG', 'DEPLOY', 'BUILD', 'FUNK'];

/** GAMESTAT.TXT sprite fields (0x43BB80, 0x43BA3C, 0x43BB30), in file order. */
export function loadTypes(sd, text) {
  const lines = dataLines(text);
  let li = 0;
  const count = parseInt(tokens(lines[li++])[0], 10);
  if (!(count > 0 && count <= 130)) throw new Error('0 < objects <= NUMOBJTYPES');
  const types = [];
  for (let n = 0; n < count; n++) {
    const tk = tokens(lines[li++]);
    const name = tk[0];
    const cols = tk.slice(1).map((t) => parseInt(t, 10));
    const speed = cols[2]; // +0x0C
    const fly = cols[12] & 0xff; // +0x60 (byte)
    const t = { name, speed, fly, move: 0, stand: 0, scrch: 0, burn: 0, fig: 0, deploy: 0, build: 0, funk: 0, fire: [], die: [], blood: [] };
    if (speed !== 0) t.move = sd.makeSet(name, 'MOVE'); // 0x43BEF0
    t.stand = sd.makeSet(name, 'STAND'); // 0x43BF1C
    // 0x43BF54: bounding box of the STAND set; 0x43BF66: aim point from part 6 of STAND[0]'s first frame
    const box = setRect(sd.sets[t.stand]);
    const p6 = firstFramePart(sd.sets[t.stand].anims[0], 6);
    t.aim = p6 ? [p6[0], p6[1]] : [0, (-(box[3] >> 3)) | 0];
    if (fly === 0) { // 0x43BFB5: ground units are at least +-96 wide
      if (box[0] > -96) box[0] = -96;
      if (box[2] < 96) box[2] = 96;
      if (box[1] > -96) box[1] = -96;
      if (box[3] < 96) box[3] = 96;
    }
    t.bbox = box;
    // 0x43BA3C: firing animations
    let nf = 0;
    if (sd.lookup2(name, 'FIRE0')) { t.fire[0] = sd.makeSet(name, 'FIRE'); nf = 1; }
    if (sd.lookup2(name, 'FIREA0')) { t.fire[0] = sd.makeSet(name, 'FIREA'); nf = 1; }
    if (sd.lookup2(name, 'FIREB0')) { nf++; t.fire[nf - 1] = sd.makeSet(name, 'FIREB'); }
    if (sd.lookup2(name, 'FIREC0')) { nf++; t.fire[nf - 1] = sd.makeSet(name, 'FIREC'); }
    if (nf === 0) { t.fire[0] = t.stand; nf = 1; }
    t.fire.length = nf;
    // 0x43C009: death animations
    if (sd.anyEvenFacing(name, 'DIE')) {
      t.die = [sd.makeSet(name, 'DIE')];
    } else if (sd.anyEvenFacing(name, 'DIEA')) {
      t.die = [sd.makeSet(name, 'DIEA')];
      if (sd.anyEvenFacing(name, 'DIEB')) {
        t.die.push(sd.makeSet(name, 'DIEB'));
        if (sd.anyEvenFacing(name, 'DIEC')) t.die.push(sd.makeSet(name, 'DIEC'));
      }
    } else {
      t.die = [t.stand];
    }
    t.deploy = sd.anyEvenFacing(name, 'DEPLOY') ? sd.makeSet(name, 'DEPLOY') : t.stand; // 0x43C15C
    t.funk = sd.anyEvenFacing(name, 'FUNK') ? sd.makeSet(name, 'FUNK') : t.stand; // 0x43C1AD
    t.fig = sd.anyEvenFacing(name, 'FIG') ? sd.makeSet(name, 'FIG') : t.stand; // 0x43C1FE
    if (sd.anyEvenFacing(name, 'BUILDSTAND')) t.build = sd.makeSet(name, 'BUILDSTAND'); // 0x43C24F
    else if (sd.anyEvenFacing(name, 'BUILD')) t.build = sd.makeSet(name, 'BUILD');
    else t.build = 0;
    if (sd.anyEvenFacing(name, 'SCRCH')) { // 0x43C2DA
      t.scrch = sd.makeSet(name, 'SCRCH');
      t.burn = sd.anyEvenFacing(name, 'BURN') ? sd.makeSet(name, 'BURN') : t.scrch;
    } else {
      t.scrch = t.stand;
      t.burn = t.stand;
    }
    for (let c = 0; c < 7; c++) { // 0x43C3B5: BLOODA..BLOODG, gaps are skipped
      const suffix = `BLOOD${String.fromCharCode(0x41 + c)}`;
      if (sd.anyEvenFacing(name, suffix)) t.blood.push(sd.makeSet(name, suffix));
    }
    types.push(t);
  }
  return types;
}

/** WEAPSTAT.TXT sprite fields (0x43B6EC + 0x43B9C4), in file order; weapons[number] = { bullet, explode }. */
export function loadWeapons(sd, text, booms) {
  const lines = dataLines(text);
  let li = 0;
  const count = parseInt(tokens(lines[li++])[0], 10);
  if (!(count > 0 && count < 80)) throw new Error('0 < weapons < NUMWEAPTYPES');
  const weapons = [];
  for (let n = 0; n < count; n++) {
    const tk = tokens(lines[li++]);
    const number = parseInt(tk[0], 10);
    if (!(number >= 0 && number < 80)) throw new Error('number >= 0 && number < NUMWEAPTYPES');
    const name = tk[1];
    const cols = tk.slice(2).map((t) => parseInt(t, 10));
    const blast = cols[6]; // +0x1C
    const w = { number, name, bullet: 0, explode: [] };
    if (sd.lookup2(name, 'BULLET0')) w.bullet = sd.makeSet(name, 'BULLET'); // 0x43B91B
    if (sd.lookup2(name, 'EXPLODE0')) w.explode = [sd.makeSet(name, 'EXPLODE')]; // 0x43B959
    else if (sd.lookup2(name, 'EXPL0')) w.explode = [sd.makeSet(name, 'EXPL')];
    if (blast > 0) { // 0x43B9C4: the blast type's sets (up to 4, until the first NULL) replace the weapon's own
      const boom = booms[blast];
      const list = [];
      for (let k = 0; k < 4 && boom && boom.sets[k]; k++) list.push(boom.sets[k]);
      if (list.length > 0) w.explode = list;
    }
    weapons[number] = w;
  }
  return weapons;
}

// ---------------------------------------------------------------- JSON assembly

export function extract(gameDir, opts = {}) {
  const sd = new SpriteData(gameDir);
  sd.loadAnimDat();
  const gs = path.join(gameDir, 'GAMESTAT');
  const read = (f) => fs.readFileSync(findFile(gs, f), 'latin1');
  // allocation order of 0x43C44C: booms, (mbullet), types, (unitid), weapons
  const booms = loadBooms(sd, read('BOOMSTAT.TXT'));
  const types = loadTypes(sd, read('GAMESTAT.TXT'));
  const weapons = loadWeapons(sd, read('WEAPSTAT.TXT'), booms);

  // compact animation ids: only animations reachable from a set
  const animIds = new Map();
  const anims = [];
  for (let s = 1; s < sd.sets.length; s++) {
    for (const a of sd.sets[s].anims) {
      if (a && !animIds.has(a)) {
        animIds.set(a, anims.length);
        const rec = { n: a.name, d: a.frames.map((f) => f.dur) };
        const h = partHotspots(a, 7);
        if (h.length) rec.h = h;
        anims.push(rec);
      }
    }
  }
  const sets = sd.sets.map((s) => (s ? { n: s.name, a: s.anims.map((a) => (a ? animIds.get(a) : -1)) } : null));
  const out = {
    format: FORMAT,
    version: FORMAT_VERSION,
    source: { game: path.basename(gameDir), files: sd.files.length, banks: sd.banks.size, animations: sd.animList.length },
    anims,
    sets,
    types: types.map((t) => ({
      name: t.name, fly: t.fly, bbox: t.bbox, aim: t.aim, move: t.move, stand: t.stand, scrch: t.scrch, burn: t.burn,
      fig: t.fig, deploy: t.deploy, build: t.build, funk: t.funk, fire: t.fire, die: t.die, blood: t.blood,
    })),
    weapons: Array.from({ length: 80 }, (_, i) => (weapons[i] ? { name: weapons[i].name, bullet: weapons[i].bullet, explode: weapons[i].explode } : null)),
    booms: Array.from({ length: booms.length }, (_, i) => (booms[i] ? { size: booms[i].size, sets: booms[i].sets } : null)),
  };
  if (opts.verbose) {
    for (const w of sd.warnings) console.error(`warning: ${w}`);
    console.error(`${sd.files.length} FIN files, ${sd.banks.size} banks, ${sd.animList.length} animations, ${sets.length - 1} sets, ${anims.length} referenced animations`);
  }
  return { json: out, data: sd, types, weapons, booms };
}

export { POSES };

// ---------------------------------------------------------------- CLI

function main(argv) {
  const args = argv.slice(2);
  let out = null;
  let pretty = false;
  let verbose = false;
  const dirs = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--out') out = args[++i];
    else if (args[i] === '--pretty') pretty = true;
    else if (args[i] === '--verbose') verbose = true;
    else dirs.push(args[i]);
  }
  if (dirs.length !== 1) {
    console.error('usage: node tools/sprdata2json.js "<game dir>" [--out FILE] [--pretty] [--verbose]');
    process.exit(2);
  }
  const { json } = extract(dirs[0], { verbose });
  const text = pretty ? JSON.stringify(json, null, 1) : JSON.stringify(json);
  if (out) {
    fs.mkdirSync(path.dirname(out), { recursive: true });
    fs.writeFileSync(out, `${text}\n`);
    if (verbose) console.error(`wrote ${out} (${text.length} bytes)`);
  } else {
    process.stdout.write(`${text}\n`);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv);

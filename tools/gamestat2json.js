// Dark Colony GAMESTAT/*.TXT balance tables -> JSON. Library + CLI, no dependencies.
//
//   node tools/gamestat2json.js "<game dir>" --out data/classic/gamestat.json
//   node tools/gamestat2json.js "<game dir>"            (JSON to stdout)
//   node tools/gamestat2json.js "<game dir>" --pretty
//
// PORT NOTES (loader.c / depend.c of Classic dc16.exe, read from dc16.asm on 11 Sep 2026)
//
// This tool reproduces the *text parsing* of the six balance-table loaders exactly as the
// executable does it and writes the raw numbers it reads (per record: the numbers in file order,
// the sprite name and the record index). Every transformation of those numbers (25600/col,
// (pct<<8)/100, max-flight ticks, ...) is done in src/engine/tables.js, so the JSON is a faithful
// image of the files, not of memory.
//
// Driver 0x43C44C(game): 0x426530 (sprite banks, not here), then in this order
//   0x43B424 gamestat/boomstat.txt   0x43B150 gamestat/mbullet.txt   0x4379C0 gamestat/depend.txt
//   0x43BB80 gamestat/gamestat.txt   0x438718 gamestat/unitid.txt    0x43B6EC gamestat/weapstat.txt
//
// C runtime behaviour that matters (all files are opened with mode "rb", 0x486558/0x486338):
//   * the line reader 0x4066A0 is fgets(buf, 256, fp) -- the '\n' AND the '\r' of a CRLF file
//     stay in the buffer, so strlen(line) = visible chars + 2; a line longer than 255 bytes is
//     returned in pieces; EOF is a fatal assert ("engmain.c" line 449), i.e. the count in line 1
//     must not exceed the number of data lines;
//   * a "data line" is found by re-reading while strlen(line) < MINLEN or line[0] == '%'.
//     MINLEN is 4 for weapstat, mbullet, depend, unitid and 3 for gamestat and boomstat (0x43B3E8)
//     -- with CRLF files that means "skip lines with at most 1 / 0 visible characters";
//   * line 1 of every file except unitid is read with a plain fgets + atoi (0x47C085): no comment
//     skipping, the count must be the very first line;
//   * sscanf (0x440FD9) "%s %d ..." stops at the first field it cannot convert and leaves the rest
//     of the destinations untouched (the record memory is zero-initialised static data, the
//     stack temporaries are stale) -- Classic files always have all columns, the tool records the
//     numbers actually converted and tables.js treats missing ones as 0;
//   * strtol (0x47CBAA, base 10) with no digits returns 0 and does not advance -- every later
//     strtol of the same line then also yields 0 (depend/unitid);
//   * the word tokenizer 0x406A74 splits at the C whitespace class (9..13, 32; table 0x499A84+1);
//     0x406B5C = tokenizer + strtol with two fatal asserts (token present, number parsed);
//   * mbullet values go through strtod (0x454F09): floats are legal there.
//
// Per file (asserts are fatal in the exe: 0x47C02E -> exit; the tool throws in the same cases):
//   weapstat 0x43B6EC: count = atoi(line1), assert 0 < count < 80 (NUMWEAPTYPES). Each of the
//     `count` data lines (MINLEN 4): the text up to the FIRST SPACE (0x20 only, not tab) is the
//     weapon number via atoi (assert 0 <= n < 80; the record index), the rest is
//     sscanf "%s %d %d %d %d %d %d %d %d %d %d %d" -> name + 11 ints. Record index = weapon number
//     from the line (weapon 0 is never in the file and stays zero).
//   gamestat 0x43BB80: count = atoi(line1), assert 0 < count <= 130 (NUMOBJTYPES), stored at
//     0x518B34. Each data line (MINLEN 3): sscanf "%s" + 32 x "%d". Record index = line order.
//   mbullet 0x43B150: line1 atoi -> num_armours (assert > 0, 0x518B28), line2 atoi -> num_weapons
//     (assert > 0, 0x518B2C); then num_weapons data lines (MINLEN 4) of num_armours strtod values
//     (assert each one parsed: "lptr!=ptr").
//   boomstat 0x43B424: count = atoi(line1), assert 0 < count < 15 (NUMBLASTTYPES). Per entry:
//     data line `index size` (getint x2, assert size >= 1; index = record index), then up to 4
//     data lines each holding one sprite name until the name is "NONE" (a 5th name is not read:
//     the loop stops after 4 sprites without consuming the NONE line), then `size` data lines of
//     `size` ints (blast matrix), then 3 data lines of 3 ints (scatter matrix).
//   depend 0x4379C0: count = atoi(line1), assert 0 < count <= 110 (MAX_DEPEND_ITEMS). Per data
//     line (MINLEN 4), all strtol: id (assert id < 110; record index), cost, button, kind;
//     kind 0 or 2 -> three params, kind 1 -> one param, anything else -> assert(0); then
//     dependency ids stored one by one INCLUDING the -1 terminator (assert fewer than 5 stored
//     before the terminator: "j<MAX_DEPEND"). 0x506048 = count from line 1 (not max id + 1).
//   unitid 0x438718: no count line. 80 rows x 4 ints (MAX_LOOKUP) pre-filled with -1; each data
//     line (MINLEN 4) = 4 strtol; a row with all four values < 0 ends the file; assert fewer than
//     79 rows stored ("i<MAX_LOOKUP-1").
//
// JSON layout ({ format, version, source, booms, mbullet, depend, types, unitid, weapons }):
//   weapons.records[]: { index, name, cols[11] }        cols = weapon_class sound rate damage speed
//                                                        range blast shots reload kind oneshot
//   types.records[]:   { index, name, cols[32] }
//   mbullet:           { numArmours, numWeapons, rows[numWeapons][numArmours] } raw percentages
//   booms.records[]:   { index, size, sprites[], blast[size][size], scatter[3][3] } raw percentages
//   depend.records[]:  { index, cost, button, kind, params[1|3], deps[] (with the -1) }
//   unitid.rows[]:     [a, b, c, d] in file order (see tables.js for the meaning)
//   *.count:           the count read from line 1 (weapons, types, booms, depend)

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const FORMAT = 'dc16-gamestat';
export const FORMAT_VERSION = 1;

export const FILES = Object.freeze({
  boomstat: 'BOOMSTAT.TXT',
  mbullet: 'MBULLET.TXT',
  depend: 'DEPEND.TXT',
  gamestat: 'GAMESTAT.TXT',
  unitid: 'UNITID.TXT',
  weapstat: 'WEAPSTAT.TXT',
});

const FGETS_SIZE = 256; // 0x4066A0: edx = 100h

export class LoaderError extends Error {
  constructor(file, message) {
    super(`${file}: ${message}`);
    this.file = file;
  }
}

// ---- C runtime emulation ------------------------------------------------------------------------

const isSpace = (c) => c === 0x20 || (c >= 0x09 && c <= 0x0d);
const isDigit = (c) => c >= 0x30 && c <= 0x39;

/** fgets(buf, 256, fp) over a Buffer opened in binary mode: '\r' and '\n' stay in the line. */
export class LineReader {
  constructor(buf, file) {
    this.buf = buf;
    this.file = file;
    this.pos = 0;
    this.lineNo = 0;
  }

  /** One fgets call: at most 255 bytes, stops after '\n'. Throws at EOF like assert 0x4066B0. */
  fgets() {
    if (this.pos >= this.buf.length) throw new LoaderError(this.file, `unexpected end of file (fgets returned NULL, engmain.c 449) after line ${this.lineNo}`);
    let end = this.pos;
    const limit = Math.min(this.buf.length, this.pos + FGETS_SIZE - 1);
    while (end < limit) {
      const c = this.buf[end++];
      if (c === 0x0a) break;
    }
    const text = this.buf.toString('latin1', this.pos, end);
    this.pos = end;
    if (text.endsWith('\n')) this.lineNo++;
    return text; // strlen(text) === text.length (no NUL bytes in these files)
  }

  /** The data-line loop of every loader: skip strlen < minLen and '%' lines. */
  dataLine(minLen) {
    for (;;) {
      const text = this.fgets();
      if (text.length < minLen) continue;
      if (text.charCodeAt(0) === 0x25) continue; // '%'
      return text;
    }
  }
}

/** atoi 0x47C085: leading whitespace, optional sign, digits. */
export function atoi(s) {
  let i = 0;
  while (i < s.length && isSpace(s.charCodeAt(i))) i++;
  let neg = false;
  if (s[i] === '-' || s[i] === '+') { neg = s[i] === '-'; i++; }
  let v = 0;
  while (i < s.length && isDigit(s.charCodeAt(i))) v = v * 10 + (s.charCodeAt(i++) - 0x30);
  return (neg ? -v : v) | 0;
}

/** A char* cursor into a line. */
export class Cursor {
  constructor(s) {
    this.s = s;
    this.i = 0;
  }
}

/**
 * strtol(ptr, &ptr, 10) 0x47CBAA. Returns { value, parsed }; with no digits the value is 0 and the
 * cursor does not move (Watcom sets *endptr = nptr).
 */
export function strtol(cur) {
  const s = cur.s;
  let i = cur.i;
  while (i < s.length && isSpace(s.charCodeAt(i))) i++;
  let neg = false;
  if (s[i] === '-' || s[i] === '+') { neg = s[i] === '-'; i++; }
  const start = i;
  let v = 0;
  while (i < s.length && isDigit(s.charCodeAt(i))) v = v * 10 + (s.charCodeAt(i++) - 0x30);
  if (i === start) return { value: 0, parsed: false };
  cur.i = i;
  return { value: (neg ? -v : v) | 0, parsed: true };
}

const FLOAT_RE = /^[+-]?(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)/;

/** strtod(ptr, &ptr) 0x454F09. Returns { value, parsed }. */
export function strtod(cur) {
  const s = cur.s;
  let i = cur.i;
  while (i < s.length && isSpace(s.charCodeAt(i))) i++;
  const m = FLOAT_RE.exec(s.slice(i));
  if (!m) return { value: 0, parsed: false };
  cur.i = i + m[0].length;
  return { value: Number(m[0]), parsed: true };
}

/** Word tokenizer 0x406A74(&ptr): skip whitespace, return the word or null at the end of the line. */
export function token(cur) {
  const s = cur.s;
  let i = cur.i;
  while (i < s.length && isSpace(s.charCodeAt(i))) i++;
  if (i >= s.length) return null;
  const start = i;
  while (i < s.length && !isSpace(s.charCodeAt(i))) i++;
  cur.i = i;
  return s.slice(start, i);
}

/** 0x406B5C(&ptr): next word as an integer; both "no word" and "not a number" are fatal asserts. */
export function getInt(cur, file) {
  const t = token(cur);
  if (t === null) throw new LoaderError(file, `missing number in "${cur.s.trim()}" (engmain.c 930)`);
  const r = strtol(new Cursor(t));
  if (!r.parsed) throw new LoaderError(file, `"${t}" is not a number (engmain.c 944)`);
  return r.value;
}

/**
 * sscanf(line, "%s %d %d ...", name, &int...) 0x440FD9: returns { name, ints } where ints holds
 * only the successfully converted values (a failed %d ends the scan). name === null if even the
 * %s failed (empty line).
 */
export function sscanfNameInts(line, nInts) {
  const cur = new Cursor(line);
  const name = token(cur);
  const ints = [];
  if (name === null) return { name, ints };
  for (let k = 0; k < nInts; k++) {
    const r = strtol(cur);
    if (!r.parsed) break;
    ints.push(r.value);
  }
  return { name, ints };
}

// ---- the six loaders ----------------------------------------------------------------------------

/** 0x43B424 BOOMSTAT.TXT */
export function parseBoomstat(buf, file = FILES.boomstat) {
  const rd = new LineReader(buf, file);
  const count = atoi(rd.fgets());
  if (!(count > 0 && count < 15)) throw new LoaderError(file, `booms < NUMBLASTTYPES && booms > 0 failed (${count})`);
  const records = [];
  for (let i = 0; i < count; i++) {
    const cur = new Cursor(rd.dataLine(3));
    const index = getInt(cur, file);
    const size = getInt(cur, file);
    if (!(size >= 1)) throw new LoaderError(file, `size >= 1 failed for boom ${index}`);
    const sprites = [];
    for (let k = 0; k < 4; k++) {
      const c = new Cursor(rd.dataLine(3));
      const t = token(c);
      if (t === null) throw new LoaderError(file, `missing sprite name for boom ${index} (engmain.c 930)`);
      if (t === 'NONE') break;
      sprites.push(t);
    }
    const blast = [];
    for (let r = 0; r < size; r++) {
      const c = new Cursor(rd.dataLine(3));
      const row = [];
      for (let k = 0; k < size; k++) row.push(getInt(c, file));
      blast.push(row);
    }
    const scatter = [];
    for (let r = 0; r < 3; r++) {
      const c = new Cursor(rd.dataLine(3));
      const row = [];
      for (let k = 0; k < 3; k++) row.push(getInt(c, file));
      scatter.push(row);
    }
    records.push({ index, size, sprites, blast, scatter });
  }
  return { count, records };
}

/** 0x43B150 MBULLET.TXT */
export function parseMbullet(buf, file = FILES.mbullet) {
  const rd = new LineReader(buf, file);
  const numArmours = atoi(rd.fgets());
  if (!(numArmours > 0)) throw new LoaderError(file, `magic_bullet.num_armours>0 failed (${numArmours})`);
  const numWeapons = atoi(rd.fgets());
  if (!(numWeapons > 0)) throw new LoaderError(file, `magic_bullet.num_weapons>0 failed (${numWeapons})`);
  const rows = [];
  for (let r = 0; r < numWeapons; r++) {
    const cur = new Cursor(rd.dataLine(4));
    const row = [];
    for (let c = 0; c < numArmours; c++) {
      const v = strtod(cur);
      if (!v.parsed) throw new LoaderError(file, `lptr!=ptr failed: row ${r} has fewer than ${numArmours} values`);
      row.push(v.value);
    }
    rows.push(row);
  }
  return { numArmours, numWeapons, rows };
}

/** 0x4379C0 DEPEND.TXT */
export function parseDepend(buf, file = FILES.depend) {
  const rd = new LineReader(buf, file);
  const count = atoi(rd.fgets());
  if (!(count > 0 && count <= 110)) throw new LoaderError(file, `depends<=MAX_DEPEND_ITEM failed (${count})`);
  const records = [];
  for (let i = 0; i < count; i++) {
    const cur = new Cursor(rd.dataLine(4));
    const index = strtol(cur).value;
    if (!(index < 110)) throw new LoaderError(file, `number<MAX_DEPEND_ITEMS failed (${index})`);
    const cost = strtol(cur).value;
    const button = strtol(cur).value;
    const kind = strtol(cur).value;
    const params = [];
    if (kind === 0 || kind === 2) {
      for (let k = 0; k < 3; k++) params.push(strtol(cur).value);
    } else if (kind === 1) {
      params.push(strtol(cur).value);
    } else {
      throw new LoaderError(file, `item ${index}: unknown kind ${kind} (assert(0), depend.c 112)`);
    }
    const deps = [];
    for (;;) {
      const v = strtol(cur).value;
      deps.push(v);
      if (v === -1) break;
      if (deps.length >= 5) throw new LoaderError(file, `item ${index}: j<MAX_DEPEND failed (more than 4 dependencies)`);
    }
    records.push({ index, cost, button, kind, params, deps });
  }
  return { count, records };
}

/** 0x43BB80 GAMESTAT.TXT */
export function parseGamestat(buf, file = FILES.gamestat) {
  const rd = new LineReader(buf, file);
  const count = atoi(rd.fgets());
  if (!(count > 0 && count <= 130)) throw new LoaderError(file, `objects<=NUMOBJTYPES && objects>0 failed (${count})`);
  const records = [];
  for (let i = 0; i < count; i++) {
    const line = rd.dataLine(3);
    const { name, ints } = sscanfNameInts(line, 32);
    records.push({ index: i, name: name ?? '', cols: ints });
  }
  return { count, records };
}

/** 0x438718 UNITID.TXT */
export function parseUnitid(buf, file = FILES.unitid) {
  const rd = new LineReader(buf, file);
  const rows = [];
  for (;;) {
    const cur = new Cursor(rd.dataLine(4));
    const row = [];
    for (let k = 0; k < 4; k++) row.push(strtol(cur).value);
    if (row.every((v) => v < 0)) break;
    rows.push(row);
    if (rows.length >= 79) throw new LoaderError(file, 'i<MAX_LOOKUP-1 failed (too many rows)');
  }
  return { rows };
}

/** 0x43B6EC WEAPSTAT.TXT */
export function parseWeapstat(buf, file = FILES.weapstat) {
  const rd = new LineReader(buf, file);
  const count = atoi(rd.fgets());
  if (!(count > 0 && count < 80)) throw new LoaderError(file, `weapons < NUMWEAPTYPES && weapons > 0 failed (${count})`);
  const records = [];
  for (let i = 0; i < count; i++) {
    const line = rd.dataLine(4);
    const sp = line.indexOf(' ');
    if (sp < 0) throw new LoaderError(file, `data line without a space: "${line.trim()}" (the exe would scan past the buffer)`);
    const index = atoi(line.slice(0, sp));
    if (!(index >= 0 && index < 80)) throw new LoaderError(file, `number >= 0 && number < NUMWEAPTYPES failed (${index})`);
    const { name, ints } = sscanfNameInts(line.slice(sp + 1), 11);
    records.push({ index, name: name ?? '', cols: ints });
  }
  return { count, records };
}

// ---- folder level ---------------------------------------------------------------------------------

/** Case-insensitive lookup of GAMESTAT/<name> under the game directory (the game runs on Windows). */
export function findGamestatFile(gameDir, name) {
  const dir = findEntry(gameDir, 'GAMESTAT');
  if (!dir) return null;
  return findEntry(dir, name);
}

function findEntry(dir, name) {
  if (!fs.existsSync(dir)) return null;
  const want = name.toLowerCase();
  for (const e of fs.readdirSync(dir)) if (e.toLowerCase() === want) return path.join(dir, e);
  return null;
}

/**
 * Parse every table of <gameDir>/GAMESTAT in the driver's order and cross-check what the loaders
 * cross-check (weapon_class < num_weapons, defence_class < num_armours).
 */
export function convertGamestat(gameDir) {
  const read = (key) => {
    const f = findGamestatFile(gameDir, FILES[key]);
    if (!f) throw new LoaderError(FILES[key], `not found under ${path.join(gameDir, 'GAMESTAT')}`);
    return { buf: fs.readFileSync(f), file: path.basename(f) };
  };
  const files = {};
  const load = (key, fn) => {
    const { buf, file } = read(key);
    files[key] = file;
    return fn(buf, file);
  };
  const booms = load('boomstat', parseBoomstat);
  const mbullet = load('mbullet', parseMbullet);
  const depend = load('depend', parseDepend);
  const types = load('gamestat', parseGamestat);
  const unitid = load('unitid', parseUnitid);
  const weapons = load('weapstat', parseWeapstat);

  // 0x43BE7A: otp->defence_class>=0 && otp->defence_class<magic_bullet.num_armours
  for (const t of types.records) {
    const dc = t.cols[10] ?? 0;
    if (!(dc >= 0 && dc < mbullet.numArmours)) throw new LoaderError(files.gamestat, `type ${t.index} ${t.name}: defence class ${dc} out of range`);
  }
  // 0x43B8B1: wt->weapon_class >= 0 && wt->weapon_class < magic_bullet.num_weapons
  for (const w of weapons.records) {
    const wc = w.cols[0] ?? 0;
    if (!(wc >= 0 && wc < mbullet.numWeapons)) throw new LoaderError(files.weapstat, `weapon ${w.index}: weapon class ${wc} out of range`);
  }
  return {
    format: FORMAT,
    version: FORMAT_VERSION,
    source: { dir: path.basename(path.resolve(gameDir)), files },
    booms,
    mbullet,
    depend,
    types,
    unitid,
    weapons,
  };
}

// ---- CLI ------------------------------------------------------------------------------------------

function usage() {
  console.error('usage: node tools/gamestat2json.js "<game dir>" [--out FILE.json] [--pretty]');
  process.exit(2);
}

export function main(argv) {
  let out = null;
  let pretty = false;
  const inputs = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--out') out = argv[++i];
    else if (a === '--pretty') pretty = true;
    else if (a.startsWith('--')) usage();
    else inputs.push(a);
  }
  if (inputs.length !== 1) usage();
  let data;
  try {
    data = convertGamestat(inputs[0]);
  } catch (e) {
    console.error(e.message);
    process.exit(1);
  }
  const text = pretty ? JSON.stringify(data, null, 2) : JSON.stringify(data);
  if (out) {
    fs.mkdirSync(path.dirname(out), { recursive: true });
    fs.writeFileSync(out, text + '\n');
    console.error(`${out}: ${data.weapons.records.length} weapons, ${data.types.records.length} object types, `
      + `mbullet ${data.mbullet.numWeapons}x${data.mbullet.numArmours}, ${data.booms.records.length} booms, `
      + `${data.depend.records.length} depend items, ${data.unitid.rows.length} unitid rows (${text.length} bytes)`);
  } else {
    process.stdout.write(text + '\n');
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}

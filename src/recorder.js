// Game recorder (plan §18.4): one JSON-lines file per battle with everything a replay needs to
// re-run the engine offline and compare it with the clients' 0x08 checksums:
//   {"type":"start", ...}    map, tick length, the lobby slots at the start signal, options
//   {"type":"frame","a":..,"until":..,"cmds":"<hex>"}   every sync frame as broadcast (UNTIL excluded)
//   {"type":"rx08","slot":..,"tick":..,"checksum":..}   every 0x08 received from a client
//   {"type":"mready","slot":..,"gamePlayer":..}
//   {"type":"engine","tick":..,"checksum":..}           engine checksums (every tick while recording)
//   {"type":"note", ...}, {"type":"end", ...}
// Files are named <ISO time>-room<n>-<map>.jsonl inside RECORD_DIR (created on demand).

import fs from 'node:fs';
import path from 'node:path';

export class Recorder {
  constructor(dir, log = null) {
    this.dir = dir;
    this.log = log;
    this.fd = null;
    this.file = null;
    this.lines = 0;
  }

  open(room, header) {
    try {
      fs.mkdirSync(this.dir, { recursive: true });
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      const base = String(header.map?.file ?? 'map').replace(/\.scn$/i, '');
      this.file = path.join(this.dir, `${stamp}-room${room}-${base}.jsonl`);
      this.fd = fs.openSync(this.file, 'a');
      this.write({ type: 'start', ...header });
      return this.file;
    } catch (err) {
      this.log?.warn('recorder: cannot open file', { dir: this.dir, err: err.message });
      this.fd = null;
      return null;
    }
  }

  get active() {
    return this.fd !== null;
  }

  write(obj) {
    if (this.fd === null) return;
    try {
      fs.writeSync(this.fd, `${JSON.stringify(obj)}\n`);
      this.lines++;
    } catch (err) {
      this.log?.warn('recorder: write failed', { err: err.message });
      this.close();
    }
  }

  close(end = null) {
    if (this.fd === null) return;
    if (end) this.write({ type: 'end', ...end });
    try {
      fs.closeSync(this.fd);
    } catch {
      // ignore
    }
    this.fd = null;
  }
}

/** Read a recording back: array of the parsed lines. */
export function readRecording(file) {
  return fs
    .readFileSync(file, 'utf8')
    .split('\n')
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l));
}

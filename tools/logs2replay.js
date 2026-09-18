// Rebuild battle recordings from the server's log (RECORD_LOG, src/logrecorder.js) so that
// tools/replay.js can re-run them - the way to look at a battle that ran on Fly, where nothing
// but the log survives (plan §18.6, F50).
//
//   node tools/logs2replay.js FILE... [--out DIR] [--list] [--replay]
//   node tools/logs2replay.js --fetch APP [--since 7d|48h|ISO] [--token T] [--raw FILE] [--out DIR] [--list] [--replay]
//
// FILE is anything that holds the server's log lines: a `fly logs` capture (text or --json), a Logs
// API document, or the server's own stdout. `--fetch APP` pages the Fly Logs API
// (GET https://api.fly.io/api/v1/apps/APP/logs?next_token=<ns>, 100 entries a page, about seven
// days of history) from --since (default 7d) to now; the token comes from --token, FLY_API_TOKEN or
// `fly auth token`. --raw saves the fetched server lines for later runs. Every recording found is
// written as <start time>-room<n>-<map>.jsonl into --out (default logs/replays) unless --list;
// --replay runs tools/replay.js over each written file and prints its verdict.

import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { serverLinesOf, decodeLogRecordings, recordingFileName } from '../src/logrecorder.js';
import { replay } from './replay.js';

const API = 'https://api.fly.io/api/v1/apps';

function parseArgs(argv) {
  const o = { files: [], fetch: null, since: '7d', token: null, raw: null, out: 'logs/replays', list: false, replay: false, maxPages: 20000 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--fetch') o.fetch = argv[++i];
    else if (a === '--since') o.since = argv[++i];
    else if (a === '--token') o.token = argv[++i];
    else if (a === '--raw') o.raw = argv[++i];
    else if (a === '--out') o.out = argv[++i];
    else if (a === '--max-pages') o.maxPages = Number(argv[++i]);
    else if (a === '--list') o.list = true;
    else if (a === '--replay') o.replay = true;
    else if (a.startsWith('--')) throw new Error(`unknown option ${a}`);
    else o.files.push(a);
  }
  if (!o.files.length && !o.fetch) {
    throw new Error('usage: node tools/logs2replay.js FILE... | --fetch APP [--since 7d] [--token T] [--raw FILE] [--out DIR] [--list] [--replay]');
  }
  return o;
}

/** "7d", "36h", "90m" or an ISO date -> milliseconds since the epoch. */
export function sinceMs(spec, now = Date.now()) {
  const m = /^(\d+)([dhm])$/.exec(String(spec).trim());
  if (m) {
    const unit = { d: 86400e3, h: 3600e3, m: 60e3 }[m[2]];
    return now - Number(m[1]) * unit;
  }
  const t = Date.parse(spec);
  if (Number.isNaN(t)) throw new Error(`--since needs 7d, 36h, 90m or an ISO date, not ${spec}`);
  return t;
}

function flyToken(explicit) {
  const t = explicit ?? process.env.FLY_API_TOKEN ?? execFileSync('fly', ['auth', 'token'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  if (!t) throw new Error('no Fly token: pass --token, set FLY_API_TOKEN or log in with fly auth login');
  return /^(FlyV1|Bearer) /.test(t) ? t : `FlyV1 ${t}`;
}

/** Page the Logs API from `sinceMs` to now; returns the server's log lines (message strings). */
export async function fetchFlyLogs(app, { sinceMs: from, token, maxPages = 20000, fetchImpl = fetch, log = () => {} }) {
  const auth = flyToken(token);
  const messages = [];
  let next = `${BigInt(Math.floor(from))}000000`;
  let pages = 0;
  let lastTs = null;
  while (pages < maxPages) {
    const res = await fetchImpl(`${API}/${encodeURIComponent(app)}/logs?next_token=${next}`, { headers: { Authorization: auth } });
    if (res.status === 401) throw new Error('Fly Logs API: 401, the token is not accepted (flyctl tokens need the FlyV1 prefix, which is added here)');
    if (!res.ok) throw new Error(`Fly Logs API: HTTP ${res.status}`);
    const doc = await res.json();
    const data = Array.isArray(doc.data) ? doc.data : [];
    pages++;
    for (const e of data) if (typeof e?.attributes?.message === 'string') messages.push(e.attributes.message);
    const ts = data.at(-1)?.attributes?.timestamp ?? null;
    if (pages % 25 === 0) log(`  ${pages} pages, ${messages.length} lines, up to ${ts}`);
    const token2 = doc.meta?.next_token;
    if (!data.length || !token2 || token2 === next || ts === lastTs) break;
    lastTs = ts;
    next = String(token2);
  }
  return { messages, pages };
}

function summarise(r) {
  const start = r.lines.find((l) => l.type === 'start');
  const end = r.lines.find((l) => l.type === 'end');
  const ev = Object.entries(r.events)
    .filter(([k]) => !['start', 'frames', 'rx', 'end'].includes(k))
    .map(([k, v]) => `${k} ${v}`)
    .join(', ');
  return [
    `${r.rec}  ${r.startedAt ?? '?'}  room ${r.room ?? '?'}  ${start?.map?.file ?? '?'}`,
    `  ${r.frames} frames to tick ${r.lastUntil ?? '?'}, ${r.lines.filter((l) => l.type === 'rx08').length} sampled client checksums${ev ? `, ${ev}` : ''}`,
    end ? `  end: ${end.reason}, engine ${end.engineTime}, ${end.mismatches} mismatches${end.disabled ? `, disabled: ${end.disabled}` : ''}` : '  no end line (battle still running or its lines lost)',
    r.gaps.length ? `  GAPS in the log: seq ${r.gaps.map(([a, b]) => (a === b ? a : `${a}-${b}`)).join(', ')}` : null,
  ]
    .filter(Boolean)
    .join('\n');
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'));

if (isMain) {
  const opts = parseArgs(process.argv.slice(2));
  const objs = [];
  for (const f of opts.files) objs.push(...serverLinesOf(fs.readFileSync(f, 'utf8')));
  if (opts.fetch) {
    console.log(`fetching ${opts.fetch} logs since ${new Date(sinceMs(opts.since)).toISOString()} ...`);
    const { messages, pages } = await fetchFlyLogs(opts.fetch, { sinceMs: sinceMs(opts.since), token: opts.token, maxPages: opts.maxPages, log: console.log });
    console.log(`${messages.length} log lines in ${pages} pages`);
    if (opts.raw) {
      fs.mkdirSync(path.dirname(path.resolve(opts.raw)), { recursive: true });
      fs.writeFileSync(opts.raw, `${messages.join('\n')}\n`);
      console.log(`saved to ${opts.raw}`);
    }
    objs.push(...serverLinesOf(messages.join('\n')));
  }
  const recs = decodeLogRecordings(objs);
  console.log(`${objs.length} server log lines, ${recs.size} recording${recs.size === 1 ? '' : 's'}`);
  if (!opts.list) fs.mkdirSync(opts.out, { recursive: true });
  for (const r of [...recs.values()].sort((a, b) => String(a.startedAt).localeCompare(String(b.startedAt)))) {
    console.log(summarise(r));
    if (opts.list) continue;
    const file = path.join(opts.out, recordingFileName(r));
    fs.writeFileSync(file, `${r.lines.map((l) => JSON.stringify(l)).join('\n')}\n`);
    console.log(`  -> ${file}`);
    if (opts.replay) {
      const res = replay(r.lines, { stopAtMismatch: true });
      const verdict = res.error
        ? `ENGINE ERROR at tick ${res.error.tick}: ${res.error.message.split('\n')[0]}`
        : res.firstMismatch
          ? `FIRST MISMATCH at tick ${res.firstMismatch.tick}: engine ${res.firstMismatch.engine}, client ${res.firstMismatch.client}`
          : `clean: engine ran to tick ${res.engineTime}, ${res.compared} checksums compared`;
      console.log(`  replay: ${verdict}${res.asserts.length ? `; ${res.asserts.length} engine asserts, first at tick ${res.asserts[0].tick}: ${res.asserts[0].msg}` : ''}`);
    }
  }
}

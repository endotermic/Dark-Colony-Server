// Outcome of one arena job (from a --json file) for several candidate specs: node one.mjs <root> <json> <id> <spec>...
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, jsonFile, id, ...specs] = process.argv.slice(2);
const A = await import(pathToFileURL(path.join(root, 'src/arena.js')).href);
const job = JSON.parse(fs.readFileSync(jsonFile, 'utf8')).games.find((g) => g.job.id === Number(id)).job;
for (const a of specs) {
  const r = A.runGame({ ...job, a });
  console.log(`${a.slice(-60).padEnd(60)} ${r.winner}/${r.decidedBy ?? '-'} end ${r.ticks} | us f${r.a.fighters} m${r.a.mines} pk${r.a.peakMines} hq ${r.a.hq} died ${r.a.diedAt} | rusher f${r.b.fighters}`);
}

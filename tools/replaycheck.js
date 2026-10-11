// Re-run a recording (tools/arena2replay.js) through the engine the way synccheck does and compare every tick with the
// 0x08 checksums its frames carry - the same check the game makes while it plays the replay (plan §19.15):
//   node tools/replaycheck.js . <recording.jsonl>      -> "compared N, mismatches 0"
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const [root, file] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(root, p)).href);
const { readRecording } = await imp('src/recorder.js');
const { replay } = await imp('tools/replay.js');
const { splitCommands, decode, T } = await imp('src/commands.js');
const lines = readRecording(file);
const rx = [];
for (const l of lines) if (l.type === 'frame') for (const c of splitCommands(Buffer.from(l.cmds, 'hex'))) if (c.type === T.SYNC) { rx.push({ type: "rx08", slot: 0, tick: c.raw.readInt32LE(3), checksum: c.raw.readInt16LE(1) & 0xffff }); }
const res = replay([...lines, ...rx], { stopAtMismatch: true });
console.log(`ran to ${res.engineTime}, compared ${res.compared}, mismatches ${res.mismatches}`, res.firstMismatch ?? '', res.error ?? '');

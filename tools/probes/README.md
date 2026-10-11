# Arena probes

Diagnostic scripts written while tuning the Krusty bots against the rusher (9-11 Oct 2026, plan §19.15). Each one
re-runs arena games from a `tools/botarena.js --json <file>` result (deterministically: the same job gives the same
game) and prints one aspect of what the candidate (side `a`) does. Run them from the repository root; the first
argument is the repository path (`.`).

Typical loop: run the arena with `--json`, pick a lost job, look at it with a probe or turn it into a replay
(`tools/arena2replay.js`, `tools/replaycheck.js`), fix, re-run the job with `one.js`, then the whole pool.

Some take a "job file": a JSON `{ "games": [ <one game of an arena result> ] }` (cut one game out of a result file).

| Script | Shows | Usage |
|---|---|---|
| `one.js` | the outcome of one job for one or more candidate specs | `node tools/probes/one.js . <arena.json> <jobId> <spec>...` |
| `lines.js` | the candidate's chat lines matching a regex (escort, detour, counter-offensive, upgrades...) and its turret builders | `node tools/probes/lines.js . <arena.json> <spec> <regex> <jobId>...` |
| `money.js` | money, SPENT, the production queues, purchases and units per period | `node tools/probes/money.js . <arena.json> <jobId> [period] [from] [to]` |
| `fire.js` | per unit: ticks near an enemy and its states then (11 = firing, 3 = asleep/blocked, 5/6 = moving), last order | `node tools/probes/fire.js . <job.json> <jobId> <from> <to>` |
| `who.js` | our objects at a tick and the bot's order history for chosen objects | `node tools/probes/who.js . <job.json> <jobId> <tick> <obj,obj>` |
| `dbg.js` | one unit's situation whenever it sleeps or idles beside an enemy | `node tools/probes/dbg.js . <job.json> <obj> <to> [every]` |
| `group.js` | which Krusty task / minor holds an object at given ticks | `node tools/probes/group.js . <arena.json> <jobId> <obj> <tick,tick>` |
| `spot.js` | everything on and around a tile at a tick, and the bot's memory record there | `node tools/probes/spot.js . <arena.json> <jobId> <x> <z> <tick>` |
| `explorer.js` | the explorers: position, hp, the escort's vent (rate, hp, occupant), nearest enemy | `node tools/probes/explorer.js . <arena.json> <jobId> <from> <to> [every]` |
| `vents.js` | both HQs and the vents near ours with their distances | `node tools/probes/vents.js . <arena.json> <jobId> <tick>` |
| `episode.js` | the defence episode: threats, push, the seen enemies left out of it | `node tools/probes/episode.js . <arena.json> <jobId> [every]` |
| `retreat.js` | orders that send our tanks home with an enemy near | `node tools/probes/retreat.js . <job.json> [to]` |
| `mine.js` | each hit-point loss of our mines with enemies near: attackers seen / threats, our fighters' orders | `node tools/probes/mine.js . <arena.json> <jobId> [maxLines]` |
| `site.js` | one site (a mine) every N ticks: our object there, enemies within 14, our nearest units | `node tools/probes/site.js . <arena.json> <jobId> <x> <z> [from] [to] [every]` |
| `shooter.js` | who damages an object: units targeting it, enemies marked as firing at our team | `node tools/probes/shooter.js . <arena.json> <jobId> <obj> <from> <to>` |
| `mark.js` | an enemy's fire mark (O.ATTACKED) at our think ticks and whether the bot counts it | `node tools/probes/mark.js . <arena.json> <jobId> <enemyObj> <from> <to>` |
| `door.js` | who stands on our barracks' spawn tile while its queue waits | `node tools/probes/door.js . <arena.json> <jobId> [to]` |
| `scouts.js` | our scouts every 1000 ticks: position, hp, their job; retreats | `node tools/probes/scouts.js . <arena.json> <jobId>` |

The scripts were written for one investigation each and kept as they were; read the header of a script before
trusting its output beyond what it says.

// Smoke test against a running server (plan §13.7): scripted clients enter rooms through the hall
// and stay there, so that a real dc16.exe can be pointed at the server and the hall, the room
// counters, the refusals and a battle with the bots can be checked by hand.
//
//   node tools/smoketest.js [--host dark-colony-server.fly.dev] [--port 8888] [--plan 1:7:hold,2:3:follow]
//        [--name Smoke] [--status 30] [--duration 0] [--no-observer] [--no-reseat] [--ready-after 300]
//        [--tick-ms 20] [--join-timeout 10000]
//
// --plan        room:count:policy entries, comma-separated. Policies as in fakeclient.js: hold = the
//               bots never press READY inside the room (it stays a lobby, full or not); follow = they
//               press READY when a real player in the room does and release it when that player does,
//               so a human can start the battle with them; auto = they ready up at once (a battle
//               among bots: the room shows "in battle").
// --status      seconds between status reports (0 = none). --duration ms to run, 0 = until Ctrl+C.
// --no-observer no extra connection in the hall. The observer selects every room in turn with /N to
//               read the map line the server paints for it (the exact text a real client shows), and
//               once presses READY on every room it may not join, expecting the refusal.
// --no-reseat   when the last real player has left a battle, the bots of that room normally
//               disconnect (the room resets) and enter it again, ready for the next round.
//
// The default plan is the test of 8 Sep 2026: seven bots fill room 1 (7/7 full), three wait in
// room 2 (3/7 open) for a human to press READY. Exit code 1 if a bot could not be seated, was
// dropped while its room was still in the lobby, or a refusal did not come.

import { FakeClient, parseArgs } from './fakeclient.js';
import { T, build } from '../src/commands.js';

const HOST_DEFAULT = 'dark-colony-server.fly.dev';
const POLICY_TEXT = {
  hold: 'bots never press READY',
  follow: 'bots press READY when a real player does',
  auto: 'bots ready up at once',
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const stamp = () => new Date().toTimeString().slice(0, 8);
const say = (text) => console.log(`${stamp()} ${text}`);

/** `room:count[:policy]` entries -> groups. */
export function parsePlan(spec) {
  const groups = [];
  for (const part of String(spec).split(',')) {
    const m = /^\s*([1-7])\s*:\s*(\d+)\s*(?::\s*(auto|hold|follow))?\s*$/i.exec(part);
    if (!m) throw new Error(`bad plan entry "${part}", expected room:count[:auto|hold|follow]`);
    groups.push({
      room: Number(m[1]),
      count: Number(m[2]),
      policy: (m[3] ?? 'follow').toLowerCase(),
      bots: [],
      peerSlots: new Set(), // shared with the bots: who is a bot in this room (fakeclient follow)
      players: new Map(), // slot -> name of the real players seen in the room
      title: '',
      phase: 'lobby', // lobby | starting | running
      failures: [],
    });
  }
  return groups;
}

/**
 * The lines of chat window `next` that were not in `prev`. The window is a log: the lines after the
 * last line of the previous window are new; if that line is gone (scrolled away, or a header line
 * rewritten in place), the lines the previous window did not have are new.
 */
export function newLines(prev, next) {
  const p = prev.map((l) => l.trim()).filter(Boolean);
  const n = next.map((l) => l.trim()).filter(Boolean);
  if (p.length === 0) return n;
  const i = n.lastIndexOf(p[p.length - 1]);
  return i < 0 ? n.filter((l) => !p.includes(l)) : n.slice(i + 1);
}

class SmokeTest {
  constructor(opts) {
    this.opts = opts;
    this.groups = parsePlan(opts.plan);
    this.stopping = false;
    this.firstBot = null;
    this.observer = null;
    this.serial = 0;
  }

  botOptions(name, group) {
    const o = this.opts;
    return {
      host: o.host,
      port: o.port,
      name,
      room: group ? group.room : 0,
      readyPolicy: group ? group.policy : 'hold',
      peerSlots: group ? group.peerSlots : new Set(),
      announceName: true,
      readyAfterMs: group ? o.readyAfter : -1,
      tickMs: o.tickMs, // faster than the server's 33 ms: the bots are never the laggards of a battle
      loadMs: 200,
      log: () => {},
    };
  }

  /** The bot of a group that reports for it: the first one still connected. */
  lead(group) {
    return group.bots.find((b) => !b.closed) ?? null;
  }

  // ---- bots ---------------------------------------------------------------------------------

  newBot(group) {
    this.serial++;
    const bot = new FakeClient(this.botOptions(`${this.opts.name}${this.serial}`, group));
    if (!this.firstBot) this.firstBot = bot;
    group.bots.push(bot);
    this.wire(bot, group);
    return bot;
  }

  wire(bot, group) {
    const r = group.room;
    const isLead = () => this.lead(group) === bot;
    let window = [];
    bot.on('joined', (slot) => group.peerSlots.add(slot));
    bot.on('room', (title) => {
      group.title = title.split('\n')[0].trim();
    });
    bot.on('chatWindow', (lines) => {
      // one bot per room reports its chat; the hall greeting is shown once, by the first bot of all
      if (!isLead()) return;
      if (!bot.inRoom && bot !== this.firstBot) return;
      for (const l of newLines(window, lines)) say(`room ${r} chat | ${l}`);
      window = lines;
    });
    bot.on('lobby', (type, d) => {
      if (!isLead() || !bot.inRoom) return;
      if (d.player === undefined || d.player === 0 || group.peerSlots.has(d.player)) return; // Mercenary or a bot
      const known = group.players.get(d.player);
      switch (type) {
        case T.NAME:
          if (d.name && d.name !== known) {
            group.players.set(d.player, d.name);
            say(`room ${r}: player "${d.name}" ${known ? `renamed from "${known}"` : `arrived in slot ${d.player}`}`);
          }
          break;
        case T.READY:
          if (!known) break;
          if (d.status === 2) say(`room ${r}: ${known} pressed READY (${POLICY_TEXT[group.policy]})`);
          else if (bot.statuses[d.player] === 2) say(`room ${r}: ${known} released READY`);
          break;
        case T.DISCONNECT:
          if (known) {
            group.players.delete(d.player);
            say(`room ${r}: ${known} left the room`);
          }
          break;
        default:
          break;
      }
    });
    bot.on('loading', () => {
      if (!isLead()) return;
      group.phase = 'starting';
      say(`room ${r}: battle starting, the bots load and report MREADY`);
    });
    bot.on('sync', () => {
      if (group.phase === 'running' || !isLead()) return;
      group.phase = 'running';
      say(`room ${r}: battle running (first sync frame)`);
    });
    bot.on('disconnect', (p) => {
      if (!isLead()) return;
      const name = group.players.get(p);
      if (!name) {
        say(`room ${r}: slot ${p} left the battle`);
        return;
      }
      group.players.delete(p);
      say(`room ${r}: ${name} left the battle`);
      if (group.players.size === 0 && this.opts.reseat && !this.stopping) this.reseat(group);
    });
    bot.on('close', () => {
      if (this.stopping || bot.expectedClose) return;
      const inLobby = bot.state === 'lobby' || bot.state === 'connecting';
      const why = inLobby ? 'while the room was in the lobby' : `in state ${bot.state}`;
      say(`${bot.name}: connection closed ${why}`);
      if (inLobby) group.failures.push(`${bot.name} was dropped ${why}`);
    });
    bot.on('protocolError', (e) => say(`${bot.name}: protocol error ${e.message}`));
    bot.on('error', (e) => say(`${bot.name}: ${e.message}`));
  }

  /** Connect one bot and wait until it is inside its room. */
  async seat(bot, group) {
    const entered = new Promise((resolve) => {
      bot.once('room', () => resolve('room'));
      bot.once('close', () => resolve('closed'));
    });
    try {
      await bot.connect();
    } catch (err) {
      group.failures.push(`${bot.name} could not connect: ${err.message}`);
      say(`${bot.name}: could not connect (${err.message})`);
      return false;
    }
    const result = await Promise.race([entered, sleep(this.opts.joinTimeout).then(() => 'timeout')]);
    if (result === 'room') {
      say(`${bot.name}: slot ${bot.slot} -> room ${group.room} "${group.title}"`);
      return true;
    }
    const last = ([...bot.chat].reverse().find((l) => l.trim()) ?? '').trim();
    group.failures.push(`${bot.name} did not enter room ${group.room} (${result}; last chat line "${last}")`);
    say(`${bot.name}: did not enter room ${group.room} (${result}; last chat line "${last}")`);
    bot.expectedClose = true;
    bot.close();
    return false;
  }

  async seatAll() {
    for (const g of this.groups) {
      say(`room ${g.room}: seating ${g.count} bot(s), policy ${g.policy} (${POLICY_TEXT[g.policy]})`);
      for (let i = 0; i < g.count; i++) await this.seat(this.newBot(g), g);
    }
  }

  /** The last real player left the battle: the bots leave too (the room resets) and come back. */
  async reseat(group) {
    say(`room ${group.room}: no real player left in the battle, the bots leave and enter the room again`);
    for (const b of group.bots) {
      b.expectedClose = true;
      b.close();
    }
    group.bots = [];
    group.peerSlots.clear();
    group.players.clear();
    group.phase = 'lobby';
    await sleep(1500); // the room resets when its last player has gone
    if (this.stopping) return;
    for (let i = 0; i < group.count; i++) await this.seat(this.newBot(group), group);
  }

  // ---- observer -----------------------------------------------------------------------------

  async startObserver() {
    const bot = new FakeClient(this.botOptions(`${this.opts.name}Eye`, null));
    const obs = { bot, rooms: 7, titles: new Map(), replies: new Map(), window: [], gone: false };
    bot.on('title', (title) => {
      const m = /^>(\d) (.*)/.exec(title);
      if (m) obs.titles.set(Number(m[1]), m[2].trim());
    });
    bot.on('chatWindow', (lines) => {
      for (const l of newLines(obs.window, lines)) {
        const m = /^Type \/1\.\.\/(\d)/.exec(l);
        if (m) obs.rooms = Number(m[1]);
        const reply = /^(Cannot join room|Room) (\d):/.exec(l);
        if (reply) obs.replies.set(`${reply[1] === 'Room' ? 'select' : 'join'}${reply[2]}`, l);
      }
      obs.window = lines;
    });
    bot.on('room', (title) => {
      obs.gone = true;
      say(`observer: unexpectedly entered a room ("${title.split('\n')[0]}"), hall view unavailable from now on`);
    });
    bot.on('close', () => {
      if (!this.stopping && !obs.gone) say('observer: connection closed');
      obs.gone = true;
    });
    bot.on('error', (e) => say(`observer: ${e.message}`));
    bot.on('protocolError', (e) => say(`observer: protocol error ${e.message}`));
    try {
      await bot.connect();
      await sleep(600);
    } catch (err) {
      say(`observer: could not connect (${err.message})`);
      return;
    }
    say(`observer: slot ${bot.slot} in the hall`);
    this.observer = obs;
  }

  /**
   * Select every room in turn and read the map line the server paints for the observer's slot:
   * "N <map> <terrain> (k/s) <state>", plus the chat reply for a room it may not join. With `probe`,
   * press READY once on every room that is not open and expect "Cannot join room N: ...".
   */
  async sweep(probe) {
    const obs = this.observer;
    if (!obs || obs.gone) return [];
    const bot = obs.bot;
    const out = [];
    for (let n = 1; n <= obs.rooms && !obs.gone && !bot.closed; n++) {
      obs.replies.delete(`select${n}`);
      obs.replies.delete(`join${n}`);
      bot.send(build.lobbyChat(`${bot.name}: /${n}`));
      await sleep(350);
      const title = obs.titles.get(n) ?? '(no map line received)';
      let line = `${n} ${title}`;
      const select = obs.replies.get(`select${n}`);
      if (select) line += `   chat: "${select}"`;
      const open = /\) open$/.test(title);
      if (probe && !open && obs.titles.has(n) && !obs.gone) {
        // the refusal check; the map line was just refreshed, so the room is still not joinable
        bot.send(build.ready(2, bot.slot));
        await sleep(450);
        const join = obs.replies.get(`join${n}`);
        if (join) line += `   READY: "${join}"`;
        else if (!obs.gone) {
          line += '   READY: NO REFUSAL RECEIVED';
          this.groups[0].failures.push(`room ${n}: READY on a room that is not open was not refused`);
        }
      }
      out.push(line);
    }
    return out;
  }

  // ---- reports ------------------------------------------------------------------------------

  async report(probe = false) {
    say('status');
    for (const g of this.groups) {
      const alive = g.bots.filter((b) => !b.closed).length;
      const lead = this.lead(g);
      let line = `  room ${g.room} "${g.title || '?'}": ${alive}/${g.count} bots connected, `;
      if (g.phase === 'lobby') {
        const people = [...g.players.values()];
        line += `lobby (${POLICY_TEXT[g.policy]})${people.length ? `, real players: ${people.join(', ')}` : ''}`;
      } else if (g.phase === 'starting') {
        line += 'starting (waiting for everybody to finish loading)';
      } else {
        line += `battle running, ${lead ? lead.syncPayloads.length : 0} sync frames, tick ${lead ? lead.gameTime : 0}`;
      }
      say(line);
    }
    const hall = await this.sweep(probe);
    if (hall.length) {
      say(`  hall view of the observer (slot ${this.observer.bot.slot}):`);
      for (const l of hall) say(`    ${l}`);
    }
  }

  failures() {
    return this.groups.flatMap((g) => g.failures);
  }

  stop(reason) {
    if (this.stopping) return;
    this.stopping = true;
    say(`stopping: ${reason}`);
    for (const g of this.groups) for (const b of g.bots) b.close();
    if (this.observer) this.observer.bot.close();
    const f = this.failures();
    if (f.length) {
      say(`FAILURES (${f.length}):`);
      for (const x of f) say(`  - ${x}`);
    } else {
      say('no failures recorded');
    }
    process.exit(f.length ? 1 : 0);
  }

  async run() {
    const o = this.opts;
    say(`smoke test against ${o.host}:${o.port}, plan ${o.plan}`);
    await this.seatAll();
    if (o.observer) await this.startObserver();
    await this.report(true);
    const seats = this.groups.map((g) => `room ${g.room} ${g.bots.filter((b) => b.inRoom).length}/${g.count}`).join(', ');
    say(`seating done: ${seats}`);
    say(`connect with the game now: MULTI PLAYER WAR -> CONNECT TO SERVER -> ${o.host}`);
    if (o.status > 0) setInterval(() => this.report(false), o.status * 1000);
    if (o.duration > 0) setTimeout(() => this.stop(`duration of ${o.duration} ms is over`), o.duration);
    process.on('SIGINT', () => this.stop('Ctrl+C'));
    process.on('SIGTERM', () => this.stop('SIGTERM'));
  }
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('tools/smoketest.js');
if (isMain) {
  const args = parseArgs(process.argv.slice(2));
  const opts = {
    host: args.host ?? HOST_DEFAULT,
    port: Number(args.port ?? 8888),
    plan: args.plan ?? '1:7:hold,2:3:follow',
    name: args.name ?? 'Smoke',
    status: Number(args.status ?? 30),
    duration: Number(args.duration ?? 0),
    observer: args['no-observer'] !== 'true',
    reseat: args['no-reseat'] !== 'true',
    readyAfter: Number(args['ready-after'] ?? 300),
    tickMs: Number(args['tick-ms'] ?? 20),
    joinTimeout: Number(args['join-timeout'] ?? 10000),
  };
  new SmokeTest(opts).run().catch((err) => {
    say(`fatal: ${err.stack || err.message}`);
    process.exit(2);
  });
}

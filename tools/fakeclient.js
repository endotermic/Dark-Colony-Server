// A scripted dc16.exe client (plan §13.2/§13.3). Library + CLI.
//
//   node tools/fakeclient.js --port 8888 --count 3 [--host 127.0.0.1] [--ready-after 1000]
//        [--tick-ms 33] [--behave noEcho,noKeepalive,...] [--duration 20000] [--room 2] [--ready-policy auto|hold|follow]
//
// Behaviours: silent, noKeepalive, noEcho, noProgress, noMready, badSeq, garbage, cheat, speed, foreign
//
// READY inside a room (readyPolicy / --ready-policy): auto = press it --ready-after ms after entering
// (default); hold = never; follow = be ready exactly when a player outside peerSlots (a real player)
// is ready, so that a room of scripted clients waits for a human to start the battle (tools/smoketest.js).
//
// Hall (plan §17): when the first scenario title is the hall's, the client types "/<room>" (if
// --room is given) and presses READY to join; the room's own scenario message marks the arrival,
// after which READY is pressed again after --ready-after.

import net from 'node:net';
import { EventEmitter } from 'node:events';
import { FrameDecoder, encodeFrame } from '../src/frame.js';
import { splitCommands, decode, build, T } from '../src/commands.js';
import { HALL_TITLE_PREFIX } from '../src/hall.js';

export class FakeClient extends EventEmitter {
  constructor(opts = {}) {
    super();
    this.host = opts.host ?? '127.0.0.1';
    this.port = opts.port ?? 8888;
    this.name = opts.name ?? 'Bot';
    this.readyAfterMs = opts.readyAfterMs ?? 500;
    this.loadMs = opts.loadMs ?? 200;
    this.tickMs = opts.tickMs ?? 33;
    this.room = opts.room ?? 0; // hall: room number to select with "/N"; 0 = keep the server's choice
    this.readyPolicy = opts.readyPolicy ?? 'auto'; // READY inside a room: auto | hold | follow
    this.peerSlots = opts.peerSlots ?? new Set(); // follow: slots of the other scripted clients in the room
    this.announceName = opts.announceName ?? false; // send the name after the handshake, as if typed
    this.readyWanted = 1; // follow: the status last sent
    this.behave = new Set(opts.behave ?? []);
    this.log = opts.log ?? (() => {});
    this.state = 'connecting';
    this.slot = -1;
    this.seqOut = 0;
    this.decoder = new FrameDecoder();
    this.statuses = new Array(8).fill(0);
    this.types = new Array(8).fill(3);
    this.names = new Array(8).fill('');
    this.gotOwnStatus = false;
    this.hallTitle = false; // saw the hall's scenario title
    this.inRoom = false; // saw a room's scenario title
    this.scenarioTitle = '';
    this.marqueeSteps = 0; // name changes of a row while in the hall
    this.chat = [];
    this.syncPayloads = [];
    this.disconnects = [];
    this.reached = [];
    this.echoed = 0;
    this.gameTime = 0;
    this.until = -1;
    this.untilQueue = [];
    this.paused = false;
    this.timers = [];
    this.closed = false;
    this.badSeqSent = false;
  }

  connect() {
    return new Promise((resolve, reject) => {
      this.sock = net.connect(this.port, this.host);
      this.sock.setNoDelay(true);
      this.sock.once('connect', () => resolve());
      this.sock.on('error', (err) => {
        this.log(`${this.name}: socket error ${err.message}`);
        reject(err);
      });
      this.sock.on('data', (d) => this.onData(d));
      this.sock.on('close', () => {
        this.closed = true;
        this.clearTimers();
        this.log(`${this.name}: closed`);
        this.emit('close');
      });
    });
  }

  close() {
    this.clearTimers();
    if (this.sock) this.sock.destroy();
  }

  clearTimers() {
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
  }

  every(ms, fn) {
    this.timers.push(setInterval(fn, ms));
  }

  after(ms, fn) {
    const t = setTimeout(() => {
      this.timers = this.timers.filter((x) => x !== t);
      fn();
    }, ms);
    this.timers.push(t);
  }

  send(payload) {
    if (this.closed || !this.sock) return;
    let seq = this.seqOut;
    if (this.behave.has('badSeq') && !this.badSeqSent) {
      this.badSeqSent = true;
      seq = (seq + 5) & 15;
    }
    this.sock.write(encodeFrame(payload, seq));
    this.seqOut = (this.seqOut + 1) & 15;
  }

  onData(chunk) {
    let frames;
    try {
      frames = this.decoder.feed(chunk);
    } catch (err) {
      this.emit('protocolError', err);
      this.sock.destroy();
      return;
    }
    for (const f of frames) this.onFrame(f.payload);
  }

  onFrame(payload) {
    if (payload.length === 0) return;
    if (this.state === 'connecting') return this.onHandshake(payload);
    if (this.state === 'lobby') return this.onLobbyFrame(payload);
    if (this.state === 'loading' || this.state === 'game') return this.onGameFrame(payload);
    return undefined;
  }

  onHandshake(payload) {
    const cmds = splitCommands(payload);
    const v = cmds.find((c) => c.type === T.VERSION);
    if (!v) {
      this.emit('error', new Error(`first frame is not 'd' but ${payload[0]}`));
      return;
    }
    const d = decode(v);
    this.slot = d.id;
    this.state = 'lobby';
    this.log(`${this.name}: joined as slot ${this.slot}`);
    this.emit('joined', this.slot);
    if (this.behave.has('silent')) return;
    this.send(build.variable(8 + this.slot, 1)); // CD report, like the real client
    if (this.announceName) this.send(build.name(this.slot, this.name));
    if (this.behave.has('garbage')) this.sock.write(Buffer.from([0xff, 0xff, 0x00]));
    if (!this.behave.has('noKeepalive')) this.every(700, () => this.send(build.keepalive()));
    if (this.readyAfterMs >= 0) this.after(this.readyAfterMs, () => this.pressReady());
    if (this.behave.has('foreign')) this.after(300, () => this.send(build.name((this.slot % 7) + 1, 'Hacked')));
    // the rest of the dump may be in the same payload? no: one command per frame, handled below
  }

  /** Press the READY button: in the hall after typing the room command, if any; in a room only with readyPolicy auto. */
  pressReady() {
    if (this.state !== 'lobby') return;
    const inHall = this.hallTitle && !this.inRoom;
    if (inHall && this.room > 0) this.send(build.lobbyChat(`${this.name}: /${this.room}`));
    if (!inHall && this.readyPolicy !== 'auto') return;
    this.send(build.ready(2, this.slot));
  }

  onLobbyFrame(payload) {
    const window = []; // chat lines of this frame: the server repaints the whole window (plan §17.8)
    for (const c of splitCommands(payload)) {
      const d = decode(c);
      switch (c.type) {
        case T.SCENARIO:
          this.scenarioTitle = d.title;
          this.emit('title', d.title);
          if (d.title.startsWith(HALL_TITLE_PREFIX)) {
            this.hallTitle = true;
          } else if (this.hallTitle && !this.inRoom) {
            // moved from the hall into a room: the dump that follows carries the real statuses
            this.inRoom = true;
            this.log(`${this.name}: in room "${d.title.split('\n')[0]}"`);
            this.emit('room', d.title);
            if (this.readyAfterMs >= 0 && this.readyPolicy === 'auto') this.after(this.readyAfterMs, () => this.pressReady());
          }
          break;
        case T.NAME:
          if (this.hallTitle && !this.inRoom && this.names[d.player] && this.names[d.player] !== d.name) this.marqueeSteps++;
          this.names[d.player] = d.name;
          break;
        case T.READY:
          this.statuses[d.player] = d.status;
          if (d.player === this.slot) this.gotOwnStatus = true;
          break;
        case T.TYPE:
          this.types[d.player] = d.value;
          break;
        case T.DISCONNECT:
          this.statuses[d.player] = 0;
          this.types[d.player] = 3;
          break;
        case T.LOBBY_CHAT:
          this.chat.push(d.text);
          window.push(d.text);
          this.emit('chat', d.text);
          break;
        default:
          break;
      }
      this.emit('lobby', c.type, d);
    }
    if (window.length) this.emit('chatWindow', window);
    if (this.readyPolicy === 'follow' && (this.inRoom || !this.hallTitle)) this.follow();
    // the game leaves the lobby as soon as no slot has status 1 (F3)
    if (this.gotOwnStatus && this.statuses.every((s) => s !== 1)) this.leaveLobby();
  }

  /** readyPolicy follow: ready exactly when a player outside peerSlots (never Mercenary in slot 0) is ready. */
  follow() {
    if (this.state !== 'lobby') return;
    const humanReady = this.statuses.some((s, q) => s === 2 && q !== this.slot && q !== 0 && !this.peerSlots.has(q));
    const want = humanReady ? 2 : 1;
    if (want === this.readyWanted) return;
    this.readyWanted = want;
    this.send(build.ready(want, this.slot));
  }

  leaveLobby() {
    this.state = 'loading';
    this.clearTimers(); // keep-alives stop: the lobby loop is gone
    this.log(`${this.name}: loading`);
    this.emit('loading');
    this.after(this.loadMs, () => {
      this.state = 'game';
      if (!this.behave.has('noMready')) this.send(build.mready(this.slot, 2));
      this.every(this.tickMs, () => this.simulate());
      if (this.behave.has('cheat')) this.every(500, () => this.send(build.chat(this.slot, 0xff, `${this.name}: slag net`)));
      if (this.behave.has('speed')) this.every(500, () => this.send(build.tickSpeed(66)));
      this.emit('game');
    });
  }

  onGameFrame(payload) {
    const first = payload[0];
    if (first >= 0x64) return; // letters are ignored in-game
    if (first === T.UNTIL) {
      const cmds = splitCommands(payload);
      const u = decode(cmds[0]);
      this.syncPayloads.push(Buffer.from(payload));
      if (!this.behave.has('noEcho')) {
        this.send(build.until(u.a, u.until)); // echo before executing anything
        this.echoed++;
      }
      if (this.until < 0) this.until = u.until;
      else this.untilQueue.push(u.until);
      for (const c of cmds) {
        if (c.type === T.DISCONNECT) {
          const d = decode(c);
          this.disconnects.push(d.player);
          this.emit('disconnect', d.player);
        }
        if (c.type === T.TICK_SPEED) this.tickSpeed = decode(c).ms;
      }
      this.emit('sync', payload);
      return;
    }
    if (first === T.CHEAT) {
      const d = decode(splitCommands(payload)[0]);
      if (d.a === 1) this.paused = true;
      if (d.a === 2) this.paused = false;
    }
  }

  simulate() {
    if (this.state !== 'game' || this.paused || this.until < 0) return;
    if (this.gameTime + 1 === this.until) {
      if (!this.behave.has('noProgress')) this.send(build.until(-1, this.until));
      this.reached.push(this.until);
      this.until = this.untilQueue.length ? this.untilQueue.shift() : -1;
    }
    this.gameTime++;
  }
}

// ---- CLI ---------------------------------------------------------------------------------------

export function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    const val = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : 'true';
    out[key] = val;
  }
  return out;
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('tools/fakeclient.js');
if (isMain) {
  const args = parseArgs(process.argv.slice(2));
  const count = Number(args.count ?? 1);
  const behave = args.behave ? args.behave.split(',') : [];
  const clients = [];
  for (let i = 0; i < count; i++) {
    const c = new FakeClient({
      host: args.host,
      port: Number(args.port ?? 8888),
      name: `${args.name ?? 'Bot'}${i}`,
      readyAfterMs: Number(args['ready-after'] ?? 1000),
      tickMs: Number(args['tick-ms'] ?? 33),
      room: Number(args.room ?? 0),
      readyPolicy: args['ready-policy'] ?? 'auto',
      behave: i === count - 1 ? behave : [], // only the last client misbehaves
      log: (m) => console.log(m),
    });
    c.on('chat', (t) => console.log(`${c.name} <chat> ${t}`));
    c.on('disconnect', (p) => console.log(`${c.name} <disconnect> slot ${p}`));
    clients.push(c);
  }
  Promise.all(clients.map((c) => c.connect())).then(() => {
    const duration = Number(args.duration ?? 20000);
    setTimeout(() => {
      for (const c of clients) {
        console.log(`${c.name}: slot ${c.slot}, sync frames ${c.syncPayloads.length}, reached ${c.reached.length}, gameTime ${c.gameTime}`);
        c.close();
      }
      const ref = clients[0].syncPayloads.map((b) => b.toString('hex'));
      for (const c of clients.slice(1)) {
        const mine = c.syncPayloads.map((b) => b.toString('hex'));
        const n = Math.min(ref.length, mine.length);
        let same = 0;
        for (let i = 0; i < n; i++) if (ref[i] === mine[i]) same++;
        console.log(`${c.name}: ${same}/${n} sync frames identical to ${clients[0].name}`);
      }
      process.exit(0);
    }, duration);
  });
}

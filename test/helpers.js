// Test harness: a Room with a fake clock, fake sockets and scripted peers.

import { EventEmitter } from 'node:events';
import { FrameDecoder, encodeFrame } from '../src/frame.js';
import { splitCommands, decode, build, T } from '../src/commands.js';
import { Room } from '../src/room.js';
import { RoomPool } from '../src/rooms.js';
import { Hall } from '../src/hall.js';
import { loadConfig } from '../src/config.js';
import { silentLogger } from '../src/log.js';

let portCounter = 40000;

export class FakeSocket extends EventEmitter {
  constructor() {
    super();
    this.out = [];
    this.destroyed = false;
    this.ended = false;
    this.remoteAddress = '127.0.0.1';
    this.remotePort = portCounter++;
    this.decoder = new FrameDecoder();
  }

  write(buf) {
    this.out.push(Buffer.from(buf));
    return true;
  }

  end() {
    this.ended = true;
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    this.emit('close');
  }

  setNoDelay() {}

  setKeepAlive() {}

  /** Decode everything written since the last call into frame payloads. */
  frames() {
    const chunks = this.out;
    this.out = [];
    const res = [];
    for (const c of chunks) for (const f of this.decoder.feed(c)) res.push(f.payload);
    return res;
  }
}

/** Commands of a payload with their decoded fields merged in. */
export function cmdsOf(payload) {
  return splitCommands(payload).map((c) => ({ type: c.type, raw: c.raw, ...decode(c) }));
}

export class Peer {
  /** `entry` = the object whose accept(socket) receives the connection (a Room or the Hall). */
  constructor(h, name = 'Tester', entry = h.room) {
    this.h = h;
    this.name = name;
    this.sock = new FakeSocket();
    this.seq = 0;
    this.unread = [];
    this.all = [];
    entry.accept(this.sock);
    const first = this.take();
    const d = first.flatMap(cmdsOf).find((c) => c.type === T.VERSION);
    this.slot = d ? d.id : -1;
    this.unread = first; // leave the join dump readable for the test
  }

  /** New payloads since the last take(). */
  take() {
    const ps = this.sock.frames();
    this.all.push(...ps);
    const out = [...this.unread, ...ps];
    this.unread = [];
    return out;
  }

  takeCmds() {
    return this.take().flatMap(cmdsOf);
  }

  send(payload, seq = null) {
    const s = seq === null ? this.seq : seq;
    this.seq = (this.seq + 1) & 15;
    this.sock.emit('data', encodeFrame(payload, s & 15));
  }

  raw(bytes) {
    this.sock.emit('data', Buffer.from(bytes));
  }

  get client() {
    return this.h.room.slots[this.slot]?.client ?? null;
  }

  /** Chat line as the game sends it: "Name: text". */
  chat(text) {
    this.send(build.lobbyChat(`${this.name}: ${text}`));
  }

  /** Room (of a pool) whose slot table holds this peer's client, or null while in the hall. */
  roomOf(pool) {
    return pool.rooms.find((r) => r.slots[this.slot]?.client?.socket === this.sock) ?? null;
  }

  get gone() {
    return this.sock.destroyed;
  }

  /** The READY button. */
  pressReady() {
    this.send(build.ready(2, this.slot));
  }

  keepalive() {
    this.send(build.keepalive());
  }

  cdReport() {
    this.send(build.variable(8 + this.slot, 1));
  }
}

export class Harness {
  constructor(overrides = {}) {
    this.t = 1000;
    this.now = () => this.t;
    this.cfg = loadConfig({}, { START_COUNTDOWN_S: 0, MIN_PLAYERS: 2, ...overrides });
    this.randomSeq = null;
    this.room = new Room(this.cfg, silentLogger, this.now, (n) => this.random(n));
    this.peers = [];
  }

  random(n) {
    if (this.randomSeq && this.randomSeq.length) return this.randomSeq.shift() % n;
    return Math.floor(Math.random() * n);
  }

  advance(ms) {
    this.t += ms;
  }

  tick() {
    this.room.watchdogTick(this.t);
  }

  step() {
    this.room.step(this.t);
  }

  /** Advance the clock by `ms` in one go and run one step. */
  stepAfter(ms) {
    this.advance(ms);
    this.step();
  }

  join(name) {
    const p = new Peer(this, name);
    this.peers.push(p);
    return p;
  }
}

/** Several rooms plus the hall (plan §17), with the same fake clock and random sequence. */
export class HallHarness extends Harness {
  constructor(overrides = {}) {
    super(overrides);
    this.pool = new RoomPool(this.cfg, silentLogger, this.now, (n) => this.random(n));
    this.hall = new Hall(this.pool, this.cfg, silentLogger, this.now, (n) => this.random(n));
    this.room = this.pool.rooms[0];
  }

  tick() {
    this.pool.watchdogTick(this.t);
    this.hall.tick(this.t);
  }

  step() {
    this.pool.step(this.t);
    this.hall.step(this.t);
  }

  /** A peer that connects through the hall. */
  enter(name) {
    const p = new Peer(this, name, this.hall);
    this.peers.push(p);
    return p;
  }
}

/** Join `names`, make everybody ready, load, and return the peers with the room RUNNING. */
export function startBattle(h, names = ['A', 'B']) {
  const peers = names.map((n) => h.join(n));
  for (const p of peers) {
    p.take();
    p.cdReport();
  }
  for (const p of peers) p.pressReady();
  if (h.room.state !== 'STARTING') throw new Error(`expected STARTING, got ${h.room.state}`);
  for (const p of peers) p.send(build.mready(p.slot, 2));
  if (h.room.state !== 'RUNNING') throw new Error(`expected RUNNING, got ${h.room.state}`);
  for (const p of peers) p.take(); // discard lobby traffic
  return peers;
}

/** Echo and progress for a peer: answer every sync frame in `payloads` like the game does. */
export function answerSync(peer, payloads) {
  for (const p of payloads) {
    if (p[0] !== T.UNTIL) continue;
    const u = cmdsOf(p)[0];
    peer.send(build.until(u.a, u.until)); // echo
    peer.send(build.until(-1, u.until)); // reached
  }
}

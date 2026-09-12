// One TCP connection = one player. Holds the per-connection sequence counters, the frame
// decoder and the watchdog bookkeeping (plan §9.2). Lobby slot data lives in Room.slots.
//
// A connection is handled first by the Hall (room selection, plan §17) and then by a Room; the
// socket events are wired once and dispatched to whatever `owner` is at the time.

import { FrameDecoder, encodeFrame, checkSeq, MAX_FRAME } from './frame.js';
import { splitCommands } from './commands.js';
import { ChatView } from './chat.js';

/**
 * Concatenate command payloads into as few frame payloads as fit (F34: the client's lobby loop
 * reads one frame per iteration and its dispatcher runs every command of a frame, so one frame
 * per update keeps the client in step with the server).
 */
export function packPayloads(payloads, max = MAX_FRAME - 3) {
  const out = [];
  let cur = [];
  let size = 0;
  for (const p of payloads) {
    if (p.length > max) throw new RangeError(`command of ${p.length} bytes does not fit a frame`);
    if (size + p.length > max) {
      out.push(Buffer.concat(cur));
      cur = [];
      size = 0;
    }
    cur.push(p);
    size += p.length;
  }
  if (cur.length) out.push(Buffer.concat(cur));
  return out;
}

let nextClientId = 1;

export class Client {
  constructor(socket, id = nextClientId++, now = 0) {
    this.socket = socket;
    this.id = id;
    this.address = socket.remoteAddress ? `${socket.remoteAddress}:${socket.remotePort}` : 'unknown';
    this.owner = null; // Hall or Room that currently handles this connection
    this.slot = -1; // fixed for the whole connection: 'd' is sent once (F30)
    this.name = ''; // player name (also the chat prefix in the hall)
    this.seqOut = 0;
    this.seqIn = 0;
    this.decoder = new FrameDecoder();
    // watchdog bookkeeping
    this.joinedAt = now;
    this.lastSeen = now;
    this.firstMessageAt = 0;
    this.pendingEchoes = new Map(); // a (server time of a sync frame) -> ms when it was sent
    this.reachedUntil = 0;
    this.clientTime = 0;
    this.latencyMs = -1;
    this.strikes = 0;
    // phase flags
    this.mready = false;
    this.gamePlayer = -1; // game player index reported in MREADY (after the client-side shuffle)
    this.initMeCount = 0;
    this.gone = false;
    this.framesOut = 0;
    this.onSend = null; // optional (client, payloads) => void hook for tracing
    // hall state
    this.selected = -1; // index of the selected room; -1 = none yet (nothing is preselected)
    this.rows = null; // row texts last sent
    this.flags = null; // CD icons ("joinable") last sent
    this.chat = new ChatView(); // the client's lobby chat window as the server paints it (chat.js)
  }

  /** Wire the socket events to the current owner (Hall or Room). */
  wire() {
    const s = this.socket;
    s.on('error', (err) => this.owner?.onSocketError(this, err));
    s.on('data', (chunk) => this.owner?.onData(this, chunk));
    s.on('close', () => this.owner?.onSocketClose(this));
    try {
      s.setNoDelay(true);
      s.setKeepAlive(true, 15000);
    } catch {
      // fake sockets in tests
    }
  }

  nextSeq() {
    const s = this.seqOut;
    this.seqOut = (s + 1) & 15;
    return s;
  }

  /** Send one frame (payload = commands without terminator). */
  send(payload) {
    if (this.gone) return;
    if (this.onSend) this.onSend(this, [payload]);
    this.socket.write(encodeFrame(payload, this.nextSeq()));
    this.framesOut++;
  }

  /** Send several frames in one TCP write (used for the join dump so it arrives in one piece). */
  sendBatch(payloads) {
    if (this.gone || payloads.length === 0) return;
    if (this.onSend) this.onSend(this, payloads);
    this.socket.write(Buffer.concat(payloads.map((p) => encodeFrame(p, this.nextSeq()))));
    this.framesOut += payloads.length;
  }

  destroy() {
    this.gone = true;
    try {
      this.socket.destroy();
    } catch {
      // already gone
    }
  }
}

/**
 * Decode a TCP chunk into the commands of every new frame (duplicates skipped, sequence checked).
 * Returns { batches: [{ seq, cmds }], resyncs: [{ got, expected }], error: string | null }; the
 * batches before an error are still returned so that they can be handled before the eviction.
 */
export function readCommands(client, chunk, strictSeq) {
  const batches = [];
  const resyncs = [];
  let frames;
  try {
    frames = client.decoder.feed(chunk);
  } catch (err) {
    return { batches, resyncs, error: `bad frame: ${err.message}` };
  }
  for (const frame of frames) {
    const verdict = checkSeq(client.seqIn, frame.seq);
    if (verdict === 'duplicate') continue;
    if (verdict === 'mismatch') {
      if (strictSeq) return { batches, resyncs, error: `sequence ${frame.seq}, expected ${client.seqIn}` };
      resyncs.push({ got: frame.seq, expected: client.seqIn });
    }
    client.seqIn = (frame.seq + 1) & 15;
    try {
      batches.push({ seq: frame.seq, cmds: splitCommands(frame.payload) });
    } catch (err) {
      return { batches, resyncs, error: `bad command: ${err.message}` };
    }
  }
  return { batches, resyncs, error: null };
}

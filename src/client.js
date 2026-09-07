// One TCP connection = one player. Holds the per-connection sequence counters, the frame
// decoder and the watchdog bookkeeping (plan §9.2). Lobby slot data lives in Room.slots.

import { FrameDecoder, encodeFrame } from './frame.js';

export class Client {
  constructor(socket, id, now = 0) {
    this.socket = socket;
    this.id = id;
    this.address = socket.remoteAddress ? `${socket.remoteAddress}:${socket.remotePort}` : 'unknown';
    this.slot = -1;
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

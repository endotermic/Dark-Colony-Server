// Wire framing of dc16.exe (net.c / hack.c, reader 0x43AE8C, writer 0x421648).
//
//   byte 0      length low byte
//   byte 1      bits 0-3 length high nibble, bits 4-7 sequence number
//   bytes 2..   commands (each starting with a type byte)
//   last byte   0x00 terminator
//
// "length" counts the two header bytes, the commands and the terminator.

export const MAX_FRAME = 1024;
export const MIN_FRAME = 3;

export class ProtocolError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ProtocolError';
  }
}

/** Encode one frame. `payload` holds the commands without the terminator. */
export function encodeFrame(payload, seq) {
  const len = payload.length + 3;
  if (len > MAX_FRAME) throw new RangeError(`frame of ${len} bytes exceeds ${MAX_FRAME}`);
  const f = Buffer.allocUnsafe(len);
  f[0] = len & 0xff;
  f[1] = ((len >> 8) & 0x0f) | ((seq & 0x0f) << 4);
  payload.copy(f, 2);
  f[len - 1] = 0;
  return f;
}

/** Incremental decoder for one TCP stream. feed() returns the complete frames found so far. */
export class FrameDecoder {
  constructor() {
    this.acc = Buffer.alloc(0);
  }

  feed(chunk) {
    this.acc = this.acc.length ? Buffer.concat([this.acc, chunk]) : Buffer.from(chunk);
    const frames = [];
    for (;;) {
      if (this.acc.length < 2) break;
      const len = this.acc[0] | ((this.acc[1] & 0x0f) << 8);
      const seq = this.acc[1] >> 4;
      if (len < MIN_FRAME || len > MAX_FRAME) throw new ProtocolError(`invalid frame length ${len}`);
      if (this.acc.length < len) break;
      if (this.acc[len - 1] !== 0) throw new ProtocolError('missing frame terminator');
      frames.push({ seq, payload: Buffer.from(this.acc.subarray(2, len - 1)) });
      this.acc = this.acc.subarray(len);
    }
    return frames;
  }
}

/**
 * Sequence check as done by the game's reader: the previous number is a duplicate to skip,
 * the expected number is accepted, anything else is a mismatch.
 */
export function checkSeq(expected, got) {
  if (got === expected) return 'accept';
  if (got === ((expected - 1) & 15)) return 'duplicate';
  return 'mismatch';
}

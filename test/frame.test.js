import { test } from 'node:test';
import assert from 'node:assert/strict';
import { encodeFrame, FrameDecoder, checkSeq, ProtocolError } from '../src/frame.js';

test('encodeFrame writes length, sequence nibble and terminator', () => {
  const f = encodeFrame(Buffer.from([0x71]), 5);
  assert.deepEqual([...f], [4, 0x50, 0x71, 0]);
  const big = encodeFrame(Buffer.alloc(1021, 1), 15);
  assert.equal(big.length, 1024);
  assert.equal(big[0], 0x00);
  assert.equal(big[1], 0xf4);
  assert.equal(big[1023], 0);
  assert.throws(() => encodeFrame(Buffer.alloc(1022), 0), RangeError);
});

test('decoder handles coalesced and split frames', () => {
  const d = new FrameDecoder();
  const a = encodeFrame(Buffer.from([1, 2]), 0);
  const b = encodeFrame(Buffer.from([3]), 1);
  const both = Buffer.concat([a, b]);
  assert.equal(d.feed(both.subarray(0, 3)).length, 0);
  const out = d.feed(both.subarray(3));
  assert.equal(out.length, 2);
  assert.deepEqual([...out[0].payload], [1, 2]);
  assert.equal(out[0].seq, 0);
  assert.deepEqual([...out[1].payload], [3]);
  assert.equal(out[1].seq, 1);
  assert.equal(d.acc.length, 0);
});

test('decoder rejects bad lengths and a missing terminator', () => {
  assert.throws(() => new FrameDecoder().feed(Buffer.from([2, 0])), ProtocolError);
  assert.throws(() => new FrameDecoder().feed(Buffer.from([0x01, 0x04])), ProtocolError); // 1025
  assert.throws(() => new FrameDecoder().feed(Buffer.from([4, 0, 0x71, 7])), ProtocolError);
});

test('an empty payload frame is valid', () => {
  const out = new FrameDecoder().feed(Buffer.from([3, 0x20, 0]));
  assert.equal(out.length, 1);
  assert.equal(out[0].payload.length, 0);
  assert.equal(out[0].seq, 2);
});

test('checkSeq classifies accept, duplicate and mismatch', () => {
  assert.equal(checkSeq(3, 3), 'accept');
  assert.equal(checkSeq(3, 2), 'duplicate');
  assert.equal(checkSeq(0, 15), 'duplicate');
  assert.equal(checkSeq(3, 5), 'mismatch');
  assert.equal(checkSeq(3, 4), 'mismatch');
});

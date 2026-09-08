// The server-painted chat window (plan §17.8, F35).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { wrap, ChatView, CHAT_COLS, CHAT_ROWS } from '../src/chat.js';
import { T } from '../src/commands.js';
import { cmdsOf } from './helpers.js';

test('wrap: word-wraps at 40 columns, hard-splits long words, keeps blank lines as one space', () => {
  assert.deepEqual(wrap('short'), ['short']);
  assert.deepEqual(wrap(''), [' ']);
  assert.deepEqual(wrap('x'.repeat(40)), ['x'.repeat(40)]);
  assert.deepEqual(wrap('x'.repeat(41)), ['x'.repeat(40), 'x']);
  const t = 'Room 3 (Black Widow, desert, 0/7) selected. Press READY to join.';
  const lines = wrap(t);
  assert.ok(lines.every((l) => l.length <= CHAT_COLS));
  assert.equal(lines.join(' '), t, 'joining the lines with spaces restores the text');
  assert.deepEqual(lines, ['Room 3 (Black Widow, desert, 0/7)', 'selected. Press READY to join.']);
  assert.deepEqual(wrap('a\n\nb'), ['a', ' ', 'b']);
});

test('ChatView: the header stays at the top, messages scroll below it, the window is always ten lines', () => {
  const v = new ChatView(['Head one', 'Head two']);
  assert.equal(v.render().length, CHAT_ROWS);
  assert.deepEqual(v.render().slice(0, 2), ['Head one', 'Head two']);
  assert.ok(v.render().slice(2).every((l) => l === ' '));
  for (let i = 1; i <= 12; i++) v.push(`message ${i}`);
  const r = v.render();
  assert.equal(r.length, CHAT_ROWS);
  assert.deepEqual(r.slice(0, 2), ['Head one', 'Head two'], 'the header did not move');
  assert.deepEqual(r.slice(2), ['message 5', 'message 6', 'message 7', 'message 8', 'message 9', 'message 10', 'message 11', 'message 12']);
  // a long message takes several rows and pushes older ones out
  v.push('w'.repeat(100));
  assert.equal(v.render().length, CHAT_ROWS);
  assert.equal(v.render()[2], 'message 8');
  // the header can take at most nine rows
  const big = new ChatView(Array.from({ length: 12 }, (_, i) => `h${i}`));
  assert.equal(big.header.length, CHAT_ROWS - 1);
  big.push('last');
  assert.equal(big.render()[9], 'last');
  // payloads are ten chat commands with the same lines
  const cmds = v.payloads().map((p) => cmdsOf(p)[0]);
  assert.equal(cmds.length, CHAT_ROWS);
  assert.ok(cmds.every((c) => c.type === T.LOBBY_CHAT));
  assert.deepEqual(cmds.map((c) => c.text), v.render());
});

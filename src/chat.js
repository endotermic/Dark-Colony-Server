// The lobby chat window as the server sees it (plan §17.8). The client's chat control is a plain
// 10-line log: the 'e' handler (0x40ECE4) appends "\n" + text to the control's buffer, word-wraps at
// column 40 (it breaks when a line reaches width - 1 of the 41-column control, F26/F29) and then
// drops lines from the top until the text fits the ten rows (F35). So the server can paint the
// whole window at will: send exactly ten lines of at most 40 characters and that is what is shown.
//
// ChatView keeps, per client, a fixed header (the greeting, "at the top and static", maintainer
// 7 Sep 2026) and the most recent messages below it; render() gives the ten lines to send.

import { build } from './commands.js';

export const CHAT_COLS = 40; // longest line the client shows without wrapping it itself
export const CHAT_ROWS = 10;

/** Word-wrap `text` into lines of at most `cols` characters (a blank line is sent as one space). */
export function wrap(text, cols = CHAT_COLS) {
  const out = [];
  for (const para of String(text).split('\n')) {
    const words = para.split(/\s+/).filter(Boolean);
    let line = '';
    for (let w of words) {
      while (w.length > cols) {
        if (line) {
          out.push(line);
          line = '';
        }
        out.push(w.slice(0, cols));
        w = w.slice(cols);
      }
      if (!line) line = w;
      else if (line.length + 1 + w.length <= cols) line += ` ${w}`;
      else {
        out.push(line);
        line = w;
      }
    }
    out.push(line);
  }
  return out.map((l) => l || ' ');
}

export class ChatView {
  constructor(header = []) {
    this.header = [];
    this.lines = [];
    this.setHeader(header);
  }

  /** The static top part; at least one row is always left for messages. */
  setHeader(texts) {
    this.header = texts.flatMap((t) => wrap(t)).slice(0, CHAT_ROWS - 1);
    this.trim();
  }

  /** Append a message below the header; the oldest messages fall out of the window. */
  push(text) {
    this.lines.push(...wrap(text));
    this.trim();
  }

  trim() {
    const keep = CHAT_ROWS - this.header.length;
    if (this.lines.length > keep) this.lines.splice(0, this.lines.length - keep);
  }

  /** The ten lines of the window, top to bottom. */
  render() {
    const rows = [...this.header, ...this.lines];
    while (rows.length < CHAT_ROWS) rows.push(' ');
    return rows;
  }

  /** The ten 'e' commands that repaint the window. */
  payloads() {
    return this.render().map((l) => build.lobbyChat(l));
  }
}

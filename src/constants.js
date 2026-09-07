// Shared constants (kept dependency-free to avoid import cycles).

export const STATE = Object.freeze({ LOBBY: 'LOBBY', STARTING: 'STARTING', RUNNING: 'RUNNING' });

// Lobby slot types as used by the 'j' message (protocol doc §4.1).
export const SLOT_TYPE = Object.freeze({ AI_EASY: 0, AI_HARD: 1, HUMAN: 2, EMPTY: 3 });

export const SLOTS = 8;

// Lobby VAR defaults (protocol doc §4.1.1). Indices 8..15 are "player p has the CD": always 1 here.
export const VAR_DEFAULTS = Object.freeze([0, 0, 1, 0, 4, 4, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1]);

// In-game chat texts (after the ':') that dc16.exe treats as cheat codes (handler 0x41DA2C).
export const CHEAT_TEXTS = new Set(['we need equipment', "i'm fighting for that equipment", 'slag net']);

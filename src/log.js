// One JSON object per line on stdout (fly logs shows them as they are).

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40, silent: 100 };

export function createLogger(level = 'info', out = process.stdout) {
  const min = LEVELS[level] ?? LEVELS.info;
  const emit = (lvl, msg, fields) => {
    if (LEVELS[lvl] < min) return;
    out.write(`${JSON.stringify({ t: new Date().toISOString(), lvl, msg, ...fields })}\n`);
  };
  return {
    level,
    debug: (msg, fields) => emit('debug', msg, fields),
    info: (msg, fields) => emit('info', msg, fields),
    warn: (msg, fields) => emit('warn', msg, fields),
    error: (msg, fields) => emit('error', msg, fields),
  };
}

/** The same logger with `fields` (e.g. { room: 2 }) merged into every line. */
export function childLogger(log, fields) {
  const wrap = (fn) => (msg, extra) => fn(msg, { ...fields, ...extra });
  return { level: log.level, debug: wrap(log.debug), info: wrap(log.info), warn: wrap(log.warn), error: wrap(log.error) };
}

export const silentLogger = createLogger('silent');

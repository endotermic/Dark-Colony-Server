// Loads the battle engine (src/engine/) for the server. The engine is optional at run time: when
// SYNC_CHECK is off nothing is loaded, and when the module cannot be loaded (incomplete port, missing
// data files) the rooms run as a plain relay and say so in the log.

export async function loadEngine(log) {
  try {
    const m = await import('./engine/index.js');
    m.loadEngineData(); // fail early when the data files are missing
    return { createGame: m.createGame, loadMapJson: m.loadMapJson };
  } catch (err) {
    log.warn('battle engine not available', { err: err.message });
    return null;
  }
}

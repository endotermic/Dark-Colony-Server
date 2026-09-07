// Version from package.json, shown in the lobby greeting and the start-up log.

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const pkg = JSON.parse(readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'package.json'), 'utf8'));

/** Full version, e.g. "2.0.0". */
export const VERSION = pkg.version;

/** Major.minor, e.g. "2.0" (what players see in the lobby). */
export const VERSION_SHORT = pkg.version.split('.').slice(0, 2).join('.');

// The Mercenary's battle plan: a rusher (13 Sep 2026, maintainer request; plan §19.8).
//
// A Rusher reads the server engine's game state (plan §18) for one game player and returns
// ordinary in-game commands for it. The room puts them into its next sync frame, where every
// client executes them for the Mercenary's player exactly like a human's orders (F43). The only
// state it writes is the player's money in the engine, the fake player's one and only ledger
// (F44: the sender deducts, the handlers only book). It never calls G.rand() and never reads the
// checksum history, so the engine stays bit-exact with the clients.
//
// Character: a worker first (base income is 3 per 16 ticks, the economy is vents), then the
// barracks, then the cheapest infantry non-stop; once the first wave is out it saves for a second
// worker, then infantry again. The first wave leaves when `rushSize` armed units stand at home;
// every later pair follows as reinforcements; units are re-ordered every `reorderTicks`. The target is the nearest enemy base
// (its HQ while it stands, then its other buildings), and when no enemy building is left, whatever
// enemy unit the Mercenary's own vision shows. Allies (lobby team, or the 1000-money deal of
// mercenary.js through `isAlly`) are never targeted; when the target becomes an ally the troops
// are recalled. Every decision comes back as a chat line for mercenary.js to say.
//
// Defence (13 Sep 2026, maintainer request after the first live test: "tweak it to defend its base
// when it is attacked"): enemy units within `defendRadius` tiles of one of the own buildings are
// intruders. While there are any, the rush pauses (no wave leaves, no re-orders) and the soldiers
// at home assault the intruder nearest to the HQ; when they are fewer than `defendPerIntruder` per
// intruder (at least `defendMin`), the nearest units on their way are recalled to make the number
// (not the whole army: the second live test showed 30 units walking home for a single scout).
// Orders are refreshed every `defendReorderTicks` or when the
// nearest intruder has moved more than two tiles. Once the base is clear the troops count as fresh
// again and the next think sends them on as a wave.
//
// Sight: own buildings, enemy buildings (city slots are public on a fixed map) and everything the
// player's own team vision bit shows (grid.seenBy). Positions are the objects' raw x/z (1/256 tile,
// read unsigned: maps are up to 160 tiles, so the top bit of the int16 can be set).

import { GS, O, P, objAddr, playerAddr, u8, i16, u16, i32, w32, BUILDINGS_PER_SIDE } from './engine/mem.js';
import * as City from './engine/city.js';
import { cellGround, seenBy, EMPTY } from './engine/grid.js';
import { build } from './commands.js';

export const ORDER_MOVE = 2; // waypoint walk (DC16_BATTLE_ENGINE.md §4.2)
export const ORDER_ASSAULT = 7; // waypoint walk firing at everything in range
const SLOT_HQ = 0;
const SLOT_BARRACKS = 1;
const TYPE_VENT = 40;
const O_RATE = 0x32; // vent: petra per harvest step (renat.js)
const WORKER_TYPES = new Set([6, 14]); // EXPL, SLUG (unit class 6, DC16_AI.md §4)
const TOWER_TYPES = new Set([0x2f, 0x30]); // a worker deployed on a vent (renat.js idleVent)
const CHUNK = 100; // objects per 0x07 group: 7 + 6n bytes, well inside COMMAND_BUDGET
const CITY_OBJECTS = 8 * BUILDINGS_PER_SIDE; // objects below are city slots

export const RUSHER_DEFAULTS = Object.freeze({
  rushSize: 4, // armed units at home before the first wave leaves
  workers: 2, // workers wanted in total (walking or mining)
  reorderTicks: 400, // re-issue the assault order to units sent this long ago (~18 s at 44 ms)
  ventRetryTicks: 600, // a worker that has not reached its vent by then is sent again
  queueCap: 3, // infantry waiting in the barracks queue at most
  defendRadius: 10, // tiles from an own building within which an enemy unit is an intruder
  defendReorderTicks: 96, // refresh the defenders' orders this often while intruders remain
  defendPerIntruder: 3, // defenders wanted per intruder ...
  defendMin: 4, // ... and at least; the nearest units on their way are recalled to make the number
});

const alive = (life) => life !== 0 && life !== 10;
const dist2 = (ax, az, bx, bz) => (ax - bx) * (ax - bx) + (az - bz) * (az - bz);
const tile = (v) => v >> 8;

export class Rusher {
  /**
   * @param G       the engine Game (src/engine/engine.js)
   * @param player  game player index of the Mercenary
   * @param opts    { isAlly(q) -> bool, nameOf(q) -> string, ...RUSHER_DEFAULTS }
   */
  constructor(G, player, opts = {}) {
    this.G = G;
    this.p = player;
    this.isAlly = opts.isAlly ?? (() => false);
    this.nameOf = opts.nameOf ?? ((q) => `player ${q}`);
    this.o = { ...RUSHER_DEFAULTS, ...opts };
    this.sent = new Map(); // obj -> { tick, target }: units on their way
    this.jobs = new Map(); // worker obj -> { vent obj, tick }
    this.target = -1; // enemy player under attack
    this.waves = 0;
    this.troops = 0; // infantry bought
    this.workersBought = 0;
    this.gatherSaid = -1;
    this.said = new Set();
    this.thinks = 0;
    this.defending = null; // { since, pos, ordered } while intruders stand at the base
    this.defenses = 0; // defence episodes
  }

  // ---- ledger and lookups ---------------------------------------------------------------------

  money() {
    return i32(this.G.gs, playerAddr(this.p) + P.MONEY);
  }

  spend(cost) {
    const a = playerAddr(this.p) + P.MONEY;
    w32(this.G.gs, a, i32(this.G.gs, a) - cost);
  }

  race() {
    return i32(this.G.gs, playerAddr(this.p) + P.RACE);
  }

  slotHp(q, slot) {
    return i32(this.G.gs, playerAddr(q) + P.SLOT_HP + slot * 4);
  }

  /** Standing buildings of player q: [{ obj, slot, x, z }]. */
  buildings(q) {
    const gs = this.G.gs;
    const out = [];
    for (let slot = 0; slot < BUILDINGS_PER_SIDE; slot++) {
      if (this.slotHp(q, slot) === 0) continue;
      const obj = q * BUILDINGS_PER_SIDE + slot;
      const a = objAddr(obj);
      if (!alive(u8(gs, a + O.LIFE))) continue;
      out.push({ obj, slot, x: u16(gs, a + O.X), z: u16(gs, a + O.Z) });
    }
    return out;
  }

  /** Where the troops gather: the own HQ, else the city origin. */
  home() {
    const gs = this.G.gs;
    const hq = this.buildings(this.p).find((b) => b.slot === SLOT_HQ);
    if (hq) return [hq.x, hq.z];
    const pa = playerAddr(this.p);
    return [(i32(gs, pa + P.CITY_X) << 8) + 128, (i32(gs, pa + P.CITY_Z) << 8) + 128];
  }

  sayOnce(out, key, line) {
    if (this.said.has(key)) return;
    this.said.add(key);
    out.lines.push(line);
  }

  // ---- the think ------------------------------------------------------------------------------

  /** One decision round at engine tick `tick`: { commands: Buffer[], lines: string[] }. */
  think(tick) {
    const out = { commands: [], lines: [] };
    this.thinks++;
    const c = this.census();
    this.economy(c, out);
    this.workers(c, out, tick);
    if (!this.defend(c, out, tick)) this.attack(c, out, tick);
    return out;
  }

  /** Own units by role, live vents, and enemy units the own vision shows. */
  census() {
    const G = this.G;
    const gs = G.gs;
    const p = this.p;
    const c = { army: [], workers: [], towers: [], vents: [], enemyUnits: [] };
    const maxObj = i32(gs, GS.MAX_OBJ);
    for (let obj = 0; obj <= maxObj; obj++) {
      const a = objAddr(obj);
      if (!alive(u8(gs, a + O.LIFE))) continue;
      const type = u8(gs, a + O.TYPE);
      const team = u8(gs, a + O.TEAM);
      const t = G.tables.types[type];
      if (!t) continue;
      const rec = { obj, type, team, x: u16(gs, a + O.X), z: u16(gs, a + O.Z) };
      if (type === TYPE_VENT) {
        if (i16(gs, a + O_RATE) !== 0) c.vents.push(rec);
        continue;
      }
      if (obj < CITY_OBJECTS || team > 7) continue; // buildings come from the player blocks; wildlife is nobody's
      if (team === p) {
        if (WORKER_TYPES.has(type)) c.workers.push(rec);
        else if (TOWER_TYPES.has(type)) c.towers.push(rec);
        else if (t.speed !== 0 && t.weapon[0] !== -1) c.army.push(rec);
      } else if (t.speed !== 0 && !this.isAlly(team) && G.scenario.occupied[team]) {
        if (seenBy(G, tile(rec.x), tile(rec.z), p)) c.enemyUnits.push(rec);
      }
    }
    return c;
  }

  // ---- economy --------------------------------------------------------------------------------

  economy(c, out) {
    const p = this.p;
    const hqAlive = this.slotHp(p, SLOT_HQ) !== 0;
    const barracks = this.slotHp(p, SLOT_BARRACKS) !== 0;
    const nWorkers = c.workers.length + c.towers.length;
    const freeVents = this.freeVents(c).length;
    if (nWorkers === 0 && hqAlive && freeVents > 0) {
      this.buyWorker(out, 'A worker first: without a vent there is no money.');
      return;
    }
    if (!barracks && hqAlive) {
      this.buyBarracks(out);
      return;
    }
    if (nWorkers < this.o.workers && hqAlive && freeVents > 0 && this.waves > 0 && this.workerItem()) {
      // the first wave is out: the next purchase is the second worker, infantry waits meanwhile
      this.buyWorker(out, 'A second worker for the war chest.');
      return;
    }
    if (barracks) this.buyInfantry(c, out);
  }

  workerItem() {
    return this.troopItems().find((it) => WORKER_TYPES.has(it.type)) ?? null;
  }

  /** Troop items the player may buy now: [{ item, type, cost }]. */
  troopItems() {
    const G = this.G;
    const out = [];
    for (let i = 0; i < G.tables.depend.length; i++) {
      const it = G.tables.depend[i];
      if (!it || !it.active || it.kind !== 1) continue;
      const r = City.depCheckTroop(G, this.p, i);
      if (r.status === 1) out.push({ item: i, type: r.type, cost: r.cost });
    }
    return out;
  }

  buyWorker(out, why) {
    const w = this.workerItem();
    if (!w) return;
    if (this.money() < w.cost) {
      this.sayOnce(out, `save-worker-${this.workersBought}`, `Saving up for a${this.workersBought ? ' second' : ''} worker (${w.cost}).`);
      return;
    }
    this.spend(w.cost);
    this.workersBought++;
    out.commands.push(build.buildUnits(w.type, this.p, 1));
    out.lines.push(why);
  }

  buyBarracks(out) {
    const G = this.G;
    const race = this.race();
    const idx = G.tables.depend.findIndex((it) => it && it.active && it.kind === 0 && it.a === SLOT_BARRACKS && it.b === 0 && it.c === race);
    if (idx < 0) return;
    const r = City.depCheckBuilding(G, this.p, idx);
    if (r.status !== 1) return;
    const cost = City.itemCost(G, idx);
    if (this.money() < cost) {
      this.sayOnce(out, 'save-barracks', `Saving up for the ${race === 1 ? 'warrior hive' : 'barracks'} (${cost}).`);
      return;
    }
    this.spend(cost);
    out.commands.push(build.buildBuilding(r.slot, r.level, this.p));
    out.lines.push(race === 1 ? 'Growing a warrior hive.' : 'Building a barracks.');
  }

  buyInfantry(c, out) {
    const G = this.G;
    const gs = G.gs;
    let best = null;
    for (const it of this.troopItems()) {
      const t = G.tables.types[it.type];
      if (t.prodClass !== 0 || t.speed === 0 || t.weapon[0] === -1) continue;
      if (!best || it.cost < best.cost) best = it;
    }
    if (!best) return;
    const queued = gs.readUInt16LE(playerAddr(this.p) + P.QUEUE_LEN); // class 0 queue
    const room = this.o.queueCap - queued;
    if (room <= 0) return;
    const n = Math.min(room, Math.floor(this.money() / best.cost));
    if (n <= 0) return; // broke: not a decision, nothing to say
    this.spend(n * best.cost);
    this.troops += n;
    out.commands.push(build.buildUnits(best.type, this.p, n));
    out.lines.push(n === 1 ? 'Training one more soldier.' : `Training ${n} soldiers.`);
  }

  // ---- workers to vents -----------------------------------------------------------------------

  /** The object standing on a vent's tile, -1 if none. */
  ventOccupant(v) {
    const cell = cellGround(this.G, tile(v.x), tile(v.z)) & 0x3ff;
    return cell === EMPTY ? -1 : cell;
  }

  /** Vents nobody mines: free tile, or one of our own workers already standing there. */
  freeVents(c) {
    const own = new Set(c.workers.map((w) => w.obj));
    return c.vents.filter((v) => {
      const occ = this.ventOccupant(v);
      return occ === -1 || own.has(occ);
    });
  }

  workers(c, out, tick) {
    const vents = new Map(c.vents.map((v) => [v.obj, v]));
    const workerIds = new Set(c.workers.map((w) => w.obj));
    for (const obj of this.jobs.keys()) if (!workerIds.has(obj)) this.jobs.delete(obj);
    const taken = new Set();
    const idle = [];
    for (const w of c.workers) {
      const job = this.jobs.get(w.obj);
      const v = job ? vents.get(job.vent) : null;
      const occ = v ? this.ventOccupant(v) : -2;
      const valid = v && (occ === -1 || occ === w.obj) && (occ === w.obj || tick - job.tick < this.o.ventRetryTicks);
      if (valid) taken.add(job.vent);
      else {
        this.jobs.delete(w.obj);
        idle.push(w);
      }
    }
    const free = this.freeVents(c).filter((v) => !taken.has(v.obj));
    for (const w of idle) {
      let best = null;
      for (const v of free) {
        if (taken.has(v.obj)) continue;
        const d = dist2(w.x, w.z, v.x, v.z);
        if (!best || d < best.d) best = { v, d };
      }
      if (!best) {
        this.sayOnce(out, 'no-vent', 'No free vent for my worker. It waits.');
        break;
      }
      taken.add(best.v.obj);
      this.jobs.set(w.obj, { vent: best.v.obj, tick });
      out.commands.push(Buffer.concat([build.waypointsObjects([[best.v.x, best.v.z]], [w.obj]), build.order(w.obj, ORDER_MOVE)]));
      out.lines.push(`Worker to the vent at ${tile(best.v.x)},${tile(best.v.z)}.`);
    }
  }

  // ---- the defence ----------------------------------------------------------------------------

  /** Visible enemy units within defendRadius tiles of an own building, nearest to home first. */
  intruders(c) {
    const own = this.buildings(this.p);
    const [hx, hz] = this.home();
    if (own.length === 0) own.push({ x: hx, z: hz });
    const r2 = (this.o.defendRadius * 256) ** 2;
    const out = [];
    for (const u of c.enemyUnits) {
      if (!own.some((b) => dist2(b.x, b.z, u.x, u.z) <= r2)) continue;
      out.push({ ...u, d: dist2(hx, hz, u.x, u.z) });
    }
    return out.sort((a, b) => a.d - b.d);
  }

  /**
   * Intruders at the base: the soldiers at home assault the nearest one, the whole army when the
   * home guard is outnumbered. Returns true while the base is under attack (the rush pauses).
   */
  defend(c, out, tick) {
    const found = this.intruders(c);
    if (found.length === 0) {
      if (this.defending) {
        out.lines.push('My base is clear. Back to the plan.');
        this.defending = null;
      }
      return false;
    }
    const nearest = found[0];
    const pos = [nearest.x, nearest.z];
    let d = this.defending;
    const moved = d && dist2(d.pos[0], d.pos[1], pos[0], pos[1]) > (2 * 256) ** 2;
    if (!d) {
      d = this.defending = { since: tick, pos, ordered: -1 };
      this.defenses++;
      const name = this.nameOf(nearest.team);
      out.lines.push(`Intruders! ${found.length} of ${name}'s units at my base near ${tile(pos[0])},${tile(pos[1])}.`);
    } else if (!moved && tick - d.ordered < this.o.defendReorderTicks) {
      return true;
    }
    let guards = c.army.filter((u) => !this.sent.has(u.obj));
    const wanted = Math.max(this.o.defendMin, this.o.defendPerIntruder * found.length);
    if (guards.length < wanted && c.army.length > guards.length) {
      // outnumbered at home: the nearest of the units on their way come back, not the whole army
      const away = c.army.filter((u) => this.sent.has(u.obj)).map((u) => ({ u, d: dist2(u.x, u.z, pos[0], pos[1]) })).sort((a, b) => a.d - b.d);
      const back = away.slice(0, wanted - guards.length).map((x) => x.u);
      for (const u of back) this.sent.delete(u.obj);
      if (back.length > 0) out.lines.push(`${guards.length} at home against ${found.length}. ${back.length} come back to defend the base.`);
      guards = guards.concat(back);
    }
    d.pos = pos;
    d.ordered = tick;
    if (guards.length === 0) {
      this.sayOnce(out, `defend-nobody-${d.since}`, 'No soldiers at home to meet them. Training more.');
      return true;
    }
    this.orderUnits(guards, pos, ORDER_ASSAULT, out);
    return true;
  }

  // ---- the rush -------------------------------------------------------------------------------

  /** The enemy to hit: the current one while it has buildings, else the nearest base, else a visible unit. */
  chooseTarget(c) {
    const G = this.G;
    const [hx, hz] = this.home();
    let best = null;
    for (let q = 0; q < 8; q++) {
      if (q === this.p || !G.scenario.occupied[q] || this.isAlly(q)) continue;
      const bld = this.buildings(q);
      if (bld.length === 0) continue;
      const hq = bld.find((b) => b.slot === SLOT_HQ) ?? bld[0];
      if (q === this.target) return { player: q, pos: [hq.x, hq.z], base: true };
      const d = dist2(hx, hz, hq.x, hq.z);
      if (!best || d < best.d) best = { player: q, pos: [hq.x, hq.z], base: true, d };
    }
    if (best) return best;
    for (const u of c.enemyUnits) {
      const d = dist2(hx, hz, u.x, u.z);
      if (!best || d < best.d) best = { player: u.team, pos: [u.x, u.z], base: false, d };
    }
    return best;
  }

  /** One 0x07 (waypoint) + 0x05 (order) group per chunk of units. */
  orderUnits(units, pos, order, out) {
    for (let i = 0; i < units.length; i += CHUNK) {
      const ids = units.slice(i, i + CHUNK).map((u) => u.obj);
      out.commands.push(Buffer.concat([build.waypointsObjects([pos], ids), ...ids.map((o) => build.order(o, order))]));
    }
  }

  attack(c, out, tick) {
    const armyIds = new Set(c.army.map((u) => u.obj));
    for (const obj of this.sent.keys()) if (!armyIds.has(obj)) this.sent.delete(obj);
    const tgt = this.chooseTarget(c);
    if (!tgt) {
      if (this.target !== -1) {
        const name = this.nameOf(this.target);
        if (this.sent.size > 0) {
          this.orderUnits(c.army, this.home(), ORDER_MOVE, out);
          out.lines.push(this.isAlly(this.target) ? `${name} is my ally now. Everybody back home.` : 'Nobody left to fight. Everybody back home.');
        } else {
          out.lines.push(this.isAlly(this.target) ? `${name} is my ally now. I stand down.` : 'Nobody left to fight. I hold position.');
        }
        this.sent.clear();
        this.target = -1;
      }
      return;
    }
    const name = this.nameOf(tgt.player);
    if (tgt.player !== this.target) {
      if (this.target !== -1) {
        const old = this.nameOf(this.target);
        out.lines.push(this.isAlly(this.target) ? `${old} is my ally now. Turning on ${name}.` : `${old}'s base is rubble. Next: ${name}.`);
      } else if (!tgt.base) {
        out.lines.push(`Hunting ${name}'s units near ${tile(tgt.pos[0])},${tile(tgt.pos[1])}.`);
      }
      this.target = tgt.player;
      this.sent.clear(); // everybody gets the new destination
    }
    const fresh = c.army.filter((u) => !this.sent.has(u.obj));
    const stale = c.army.filter((u) => this.sent.has(u.obj) && tick - this.sent.get(u.obj).tick >= this.o.reorderTicks);
    if (this.waves === 0) {
      // the first wave waits for a strike force; afterwards whatever stands at home follows in pairs
      if (fresh.length < this.o.rushSize) {
        if (fresh.length !== this.gatherSaid) {
          this.gatherSaid = fresh.length;
          out.lines.push(`Gathering a strike force: ${fresh.length} of ${this.o.rushSize}. ${name}'s base is the target.`);
        }
        return;
      }
    } else if (this.sent.size > 0 && fresh.length < 2 && stale.length === 0) return;
    const going = [...fresh, ...stale];
    if (going.length === 0) return;
    this.orderUnits(going, tgt.pos, ORDER_ASSAULT, out);
    for (const u of going) this.sent.set(u.obj, { tick, target: tgt.player });
    if (fresh.length > 0) {
      this.waves++;
      if (this.waves === 1) out.lines.push(`RUSH! ${fresh.length} units storm ${name}'s base.`);
      else if (tgt.base) out.lines.push(`${fresh.length} more march on ${name}'s base.`);
      else out.lines.push(`${fresh.length} more hunt ${name}'s units.`);
    }
  }

  summary() {
    return { thinks: this.thinks, waves: this.waves, troops: this.troops, workers: this.workersBought, target: this.target, sent: this.sent.size, defenses: this.defenses, defending: this.defending !== null };
  }
}

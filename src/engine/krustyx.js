// Krusty's bot-mode extensions (7 Oct 2026, maintainer's brief, plan §19.13): the behaviours the
// original computer player lacks, switched on per bot with the krusty.js VARIANTS and measured with
// the arena (tools/botarena.js). Nothing here runs in exact mode: every hook returns at once when the
// think carries no `aux` (the engine's own AI players) or no switch is on.
//
// The state lives in `ctx.aux.x` (a plain object the KrustyBot owns), never in the original's kai
// layout, so the save format and every address of DC16_AI.md stay as they are. The extensions borrow
// the attack task's group slots 8..15 for their own units (squads 8..13, mine clearers 14, engineers
// 15); krusty.js skips those slots in attack_take, attack_plan and move_all (X.isSpecial), while
// purge and recount still see them.
//
//  * ratio (krusty.js): a second influence pool of the enemy's MOBILE ground strength (deployed towers
//    and mines left out), kept here, read by route_threat when the gate is N/10 to 1.
//  * pressure: once the barracks stands and the first mine runs, every PRESSURE_EVERY ticks three cheap
//    infantry are bought into a squad slot and sent, assault-move, at the nearest remembered enemy
//    mining tower, then the enemy base, no retreat ("kamikaze"); without a known target the squad
//    scouts the stalest reachable zone. At most MAX_SQUADS squads at a time.
//  * fortify: turret builders are bought while the player holds fewer than 2 + mines (max 8); the
//    census hands them to the defend task, whose mover deploys them where its groups stand - the home
//    zone and the mined vents.
//  * react: enemy mobile strength within two hops of home or one hop of a mine is a threat: the
//    production buys a mech (reaper / scythe, else infantry) before anything else, parked attack groups
//    and the home guard are routed to the threatened zone.
//  * upgrades=experience: the upgrade goals buy the weapon upgrade of the own type with the most kills
//    (anti-air types first once enemy flyers were seen) and the armour upgrade of the type with the most
//    losses, only for types fielded, and only with a money reserve left for the army.
//  * mines: engineers / sloms (they BECOME the mine, 450 each) are bought while laid mines plus
//    engineers stay under 2 + 2 x mines (max 10) and walk to spots: the boundary cells between the home
//    zone and each neighbour (perimeter), between the first and second ring (outer), the first steps
//    of the route towards a detected threat (route); a boundary of at most four walkable cells is a
//    bridge and comes first. On the spot the engineer deploys.
//  * clear: known enemy mines (hidden objects our detectors have revealed, kept in Krusty's memory
//    table) get a sergeant / psy-raider from slot 14 (bought when there is none) attacking them with
//    0x0B + 0x0E, or, without one, a mech borrowed from attack group 1 walked onto the mine.
//  * airscout: flying units of the scouting task patrol the zone whose centre we have not seen for
//    the longest, enemy anti-air within one hop avoided, zones with remembered enemy objects
//    preferred; two scouts are kept in production.
//  * hold (the doctrine against a rush, 7 Oct 2026, after the first ladder: the rusher's trickle of
//    infantry pairs took the HQ - and with the HQ the income, which the game credits only while the HQ
//    stands - from every variant while the defenders stood in four groups at the vents and at the home
//    guard's randomly re-routed zone): DANGER = an enemy was near home, a mine or a turret within
//    DANGER_MEMORY ticks, or an enemy fighter is known within HOME_RADIUS of the HQ, or the enemy mobile
//    strength we know of exceeds our own, or we field fewer than HOLD_MIN_FIGHTERS. Under danger the
//    split sends every new combat unit to the defend task, EVERY defender and every parked attack group
//    rallies at the HQ tile (assault-move; the original mover is silenced by marking the units as
//    ordered), no squad leaves, and the production buys a mech or an infantry before the goal chain while
//    our fighters near the HQ do not outnumber the enemy's there by HOLD_MARGIN. The split goes back to
//    the original 75 % attack when the danger is over, and every defender is released to the census at
//    that moment, so that the counter-offensive leaves with the whole army (maintainer, 7 Oct 2026: "a
//    few troopers hold the rusher's offence while we take three mining sites and get the factory as soon
//    as possible, then an instant counter-offensive with tanks"). The goal chain is reordered once at the start:
//    the factory's second level (which unlocks the turret builder) comes right after the second worker,
//    before "army 15" and the second science level, because a deployed turret takes 5 damage per
//    trooper shot and kills a trooper in 8 - the counter to the rush. Under danger turret builders are
//    bought as soon as they are buyable and walk to the HQ, where they deploy.
//  * focus (maintainer, 7 Oct 2026: "when multiple opponents, the bot must prefer and attack with new
//    units the opponent who is threatening the base / mining sites / turrets"): every think the enemy
//    mobile strength near our home, mines and turrets is added to that player's hostility, which decays
//    by a tenth per think; the most hostile player (above REACT_MIN) is the AGGRESSOR. While there is
//    one, the squads raid its mines and base only, and choose_target triples the score of zones it
//    holds (its mobile or ground strength, or its remembered buildings and mines there).

import { GS, O, P, u8, i8, i16, i32, w8, w16, w32, objAddr, playerAddr, MAX_OBJECTS } from './mem.js';
import * as Grid from './grid.js';
import { build } from '../commands.js';
import {
  variant, listOf, link, unlink, setRoute, setGoal, sendWaypointOrder, sendOrder, sendBuildUnits, money, spend, buyableTroops,
  buyableUpgrades, centreOf, seenByPlayer, famAt, adjCount, adj, zoneAddr, memAddr, minorAddr,
  K, Z, MN, OA, NO_GROUP, NZONES, NMINORS, ORDER_MOVE, ORDER_ASSAULT, ORDER_DEPLOY,
} from './krusty.js';

export const SQUAD_SLOTS = Object.freeze([8, 9, 10, 11, 12, 13]);
export const CLEAR_SLOT = 14;
export const ENG_SLOT = 15;
export const PRESSURE_EVERY = 1800; // ticks between squads (~80 s at 44 ms)
export const PRESSURE_SIZE = 3;
export const MAX_SQUADS = 3;
const FORMING_MAX = 1500; // ticks a squad waits for its recruits
const REISSUE = 320; // ticks before an order to a unit that has not arrived is repeated
const RETARGET = 1200; // ticks a squad keeps a target it has not reached
const DEPLOY_WAIT = 600; // ticks an engineer on its spot is left to finish deploying before the order is repeated
const PATROL_CLAIM = 640; // ticks a patrol destination is reserved for one scout
const REACT_MIN = 4; // mobile strength (a pair of infantry) that counts as a threat
const DANGER_MEMORY = 900; // ticks a threat near our assets keeps the hold doctrine on
const HOLD_MIN_FIGHTERS = 6; // below this many fighters the hold doctrine stays on (split, rally)
const HOLD_BUY_MIN = 3; // ... but with nobody at the gates only this many troopers are bought: the mines and the factory come first
const HOLD_OPENING_MAX = 8; // the few troopers of the opening at most, whatever stands at the gates
const HOME_RADIUS = 12; // tiles around the HQ that count as "at home"
const HOLD_MARGIN = 6; // fighters at home wanted beyond the enemy's there before the chain gets the money
const CALM_TICKS = 600; // ticks without a danger condition before the hold doctrine stands down
const RALLY_EVERY = 128; // ticks between rally orders under danger (the rusher re-orders its defenders every 96)
const RALLY_RADIUS = 18; // tiles around the HQ within which a known enemy fighter is hunted by the defenders
const TURRET_RING = 3; // tiles from the HQ within which a turret builder deploys under danger
const DEPLOY_REPEAT = 600; // ticks before a deploy order to a builder is repeated
const SPLIT_HOLD = 0; // kai split: everything to the defend task
const SPLIT_ORIGINAL = 0xc0; // the original 75 % attack
const REACT_EVERY = 320; // ticks between reactive re-routes
const UPGRADE_RESERVE = 1500; // money kept for the army when an upgrade is bought
const RESERVE = 1000; // money left to the goal chain when an extension buys turrets, engineers, scouts
const KIND_SQUAD = 1;
const KIND_ENGINEERS = 2;
const KIND_CLEARERS = 3;
const SPOT_PRIORITY = Object.freeze({ bridge: 0, route: 1, perimeter: 2, outer: 3 });

const INFANTRY = [0, 8];
const TANKS = [2, 10];
const DETECTORS = [4, 12];
const SCOUTS = [5, 13];
const TOWER_BUILDERS = [1, 9];
const ENGINEERS = [43, 44];
const MINING_TOWERS = [0x2f, 0x30];
const HIDDEN_MINES = [45, 46];

const alive = (life) => life !== 0 && life !== 10;
const tileOf = (gs, a) => [gs.readUInt16LE(a + O.X) >> 8, gs.readUInt16LE(a + O.Z) >> 8];
const isEnemy = (G, p, team) => team < 8 && team !== p && u8(G.gs, GS.ALLIANCE + 10 * p + team) === 0;
const cheb = (ax, az, bx, bz) => Math.max(Math.abs(ax - bx), Math.abs(az - bz));
const isIdle = (gs, a) => u8(gs, a + OA.BOTTOM_STATE) === 1;
/** The influence map's ground strength of one unit of `type` (krusty_general's formula). */
function gndOf(G, type) {
  const t = G.tables.types[type];
  const w = t.weapon[0];
  if (w === -1) return 0;
  const MB = G.tables.mbullet.rows;
  const wc = G.tables.weapons[w].weaponClass;
  const div = t.defenceClass === 2 ? 50 : MB[1][t.defenceClass];
  return div ? Math.trunc((25 * MB[wc][1]) / div) : 0;
}

/** Is any extension on for this think? */
function active(ctx) {
  const v = variant(ctx);
  return !!(v.ratio || v.pressure || v.fortify || v.react || v.mines || v.clear || v.airscout || v.upgrades || v.focus || v.hold);
}

/** The extension state, created on first use; null in exact mode or without a switch. */
export function state(ctx) {
  if (!ctx.aux || !active(ctx)) return null;
  if (!ctx.aux.x) {
    ctx.aux.x = {
      mobOwner: new Int8Array(NZONES).fill(-1),
      mobStr: new Uint16Array(NZONES),
      lastSeen: new Int32Array(NZONES).fill(-1),
      claim: new Int32Array(NZONES).fill(-1000000),
      interest: new Uint8Array(NZONES),
      special: new Uint8Array(NMINORS),
      squads: new Map(),
      forming: -1,
      pending: 0,
      formingSince: 0,
      lastPressure: -1000000,
      spots: null,
      spotKeys: new Set(),
      assign: new Map(),
      lastOrder: new Map(),
      lastReact: -1000000,
      lastMineSeen: -1000000,
      home: -1,
      threatZone: -1,
      threatStr: 0,
      own: { mines: 0, mineZones: [], turretZones: [], laid: 0, engineers: 0 },
      enemy: { mines: [], buildings: [], hiddenMines: [], flyers: 0 },
      interestTeam: new Int8Array(NZONES).fill(-1),
      hostility: new Float64Array(8),
      aggressor: -1,
      ownStr: 0,
      enemyStr: 0,
      fighters: 0,
      ownNearHome: 0,
      enemyNearHome: 0,
      intruders: [],
      hq: [0, 0],
      lastThreatTick: -1000000,
      lastRally: -1000000,
      danger: false,
      calmSince: -1,
      goalsReordered: false,
      stats: { squads: 0, raids: 0, minesLaid: 0, turrets: 0, reacts: 0, clears: 0, patrols: 0 },
    };
  }
  return ctx.aux.x;
}

export function isSpecial(ctx, m) {
  const x = ctx.aux?.x;
  return !!x && x.special[m] !== 0;
}

// ---- the mobile pool (VARIANT ratio) ---------------------------------------------------------------

export function mobileReset(ctx) {
  const x = state(ctx);
  if (!x) return;
  x.mobOwner.fill(-1);
  x.mobStr.fill(0);
}

/** The influence map's pool rule for the mobile pool: same owner adds, a stronger newcomer takes the zone. */
export function mobileAdd(ctx, zone, team, s) {
  const x = state(ctx);
  if (!x) return;
  const owner = x.mobOwner[zone];
  const str = x.mobStr[zone];
  if (owner === team) x.mobStr[zone] = str + s;
  else if (s >= str) {
    x.mobStr[zone] = s - str;
    x.mobOwner[zone] = team;
  } else x.mobStr[zone] = str - s;
}

export function mobilePool(ctx) {
  const x = state(ctx);
  return x ? { owner: x.mobOwner, str: x.mobStr } : null;
}

// ---- the general: what we own, what we know, what threatens us ----------------------------------

export function general(ctx) {
  const x = state(ctx);
  if (!x) return;
  const { G, p, kai } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  if (x.home < 0) for (let z = 1; z < 255; z++) if (u8(kai, zoneAddr(z) + Z.HOP) === 0) x.home = z;
  if (x.home < 0) x.home = 1;
  if (variant(ctx).hold && !x.goalsReordered) {
    // goals 7..10 of DC16_AI.md §10: workers 2, factory 2 (turrets), army 15, science 2 (the original: workers 2, army 15, science 2, factory 2)
    setGoal(kai, 7, 0, 2);
    setGoal(kai, 8, 1, 2);
    setGoal(kai, 9, 2, 15);
    setGoal(kai, 10, 1, 4);
    x.goalsReordered = true;
  }
  const own = { mines: 0, mineZones: [], turretZones: [], laid: 0, engineers: 0 };
  const pa = playerAddr(p);
  x.hq = [i32(gs, pa + P.CITY_X), i32(gs, pa + P.CITY_Z)];
  let ownStr = 0;
  let fighters = 0;
  let ownNearHome = 0;
  const maxObj = i32(gs, GS.MAX_OBJ);
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    if (!alive(u8(gs, a + O.LIFE)) || u8(gs, a + O.TEAM) !== p) continue;
    const type = u8(gs, a + O.TYPE);
    if (type < 16 && G.tables.types[type].weapon[0] !== -1 && !G.tables.types[type].fly) {
      fighters++;
      ownStr += gndOf(G, type);
      const [tx, tz] = tileOf(gs, a);
      if (cheb(tx, tz, x.hq[0], x.hq[1]) <= HOME_RADIUS) ownNearHome++;
    }
    if (MINING_TOWERS.includes(type)) {
      own.mines++;
      const [tx, tz] = tileOf(gs, a);
      const z = famAt(G, tx, tz);
      if (z) own.mineZones.push(z);
    } else if (HIDDEN_MINES.includes(type)) own.laid++;
    else if (ENGINEERS.includes(type)) own.engineers++;
    else if (type === 41 || type === 42) {
      const [tx, tz] = tileOf(gs, a);
      const z = famAt(G, tx, tz);
      if (z) own.turretZones.push(z);
    }
  }
  x.own = own;
  x.ownStr = ownStr;
  x.fighters = fighters;
  x.ownNearHome = ownNearHome;
  // what Krusty knows of the enemy: its memory table (seen, and not yet seen gone)
  const enemy = { mines: [], buildings: [], hiddenMines: [], flyers: 0 };
  let enemyNearHome = 0;
  const intruders = [];
  x.interest.fill(0);
  x.interestTeam.fill(-1);
  for (let o = 0; o < MAX_OBJECTS; o++) {
    const ma = memAddr(o);
    const type = i8(kai, ma + 2);
    if (type === -1) continue;
    const team = u8(kai, ma + 3);
    if (!isEnemy(G, p, team)) continue;
    const t = G.tables.types[type];
    if (!t) continue;
    const rec = { o, x: u8(kai, ma), z: u8(kai, ma + 1), type, team };
    if (type < 16 && t.weapon[0] !== -1 && !t.fly) {
      const d = cheb(rec.x, rec.z, x.hq[0], x.hq[1]);
      if (d <= HOME_RADIUS) enemyNearHome++;
      // a record our own vision covers right now was refreshed this think: a live position, not a ghost
      if (d <= RALLY_RADIUS && rec.x < G.map.w && rec.z < G.map.h && seenByPlayer(G, rec.x, rec.z, p)) intruders.push(rec);
    }
    if (MINING_TOWERS.includes(type)) enemy.mines.push(rec);
    else if (HIDDEN_MINES.includes(type)) enemy.hiddenMines.push(rec);
    else if (o < 120 && t.weapon[0] === -1) enemy.buildings.push(rec);
    if (t.fly) enemy.flyers++;
    if (MINING_TOWERS.includes(type) || o < 120) {
      const z = famAt(G, rec.x, rec.z);
      if (z) {
        x.interest[z] = 1;
        x.interestTeam[z] = team;
      }
    }
  }
  x.enemy = enemy;
  x.enemyNearHome = enemyNearHome;
  x.intruders = intruders;
  // staleness: when did our own vision last cover a zone's centre
  for (let z = 1; z < 255; z++) {
    const [cx, cz] = centreOf(kai, z);
    if (cx === 0 && cz === 0) continue;
    if (cx < G.map.w && cz < G.map.h && seenByPlayer(G, cx, cz, p)) x.lastSeen[z] = tick;
  }
  // threat: the strongest enemy-owned mobile zone within two hops of home or one hop of a mine
  const near = new Uint8Array(NZONES);
  const mark = (z, depth) => {
    near[z] = 1;
    if (depth === 0) return;
    for (let k = adjCount(G, z); k >= 1; k--) mark(adj(G, z, k), depth - 1);
  };
  mark(x.home, 2);
  for (const z of own.mineZones) mark(z, 1);
  for (const z of own.turretZones) mark(z, 1);
  x.threatZone = -1;
  x.threatStr = 0;
  for (let q = 0; q < 8; q++) x.hostility[q] *= 0.9; // VARIANT focus: hostility decays by a tenth per think
  for (let z = 1; z < 255; z++) {
    if (!near[z]) continue;
    const owner = x.mobOwner[z];
    if (owner === -1 || !isEnemy(G, p, owner)) continue;
    x.hostility[owner] += x.mobStr[z];
    if (x.mobStr[z] > x.threatStr) {
      x.threatStr = x.mobStr[z];
      x.threatZone = z;
    }
  }
  if (x.threatStr < REACT_MIN) x.threatZone = -1;
  if (x.threatZone >= 0) x.lastThreatTick = tick;
  // the hold doctrine: danger on -> everything to the defend task, off -> the original split
  let enemyStr = 0;
  for (let z = 1; z < 255; z++) if (x.mobOwner[z] !== -1 && isEnemy(G, p, x.mobOwner[z])) enemyStr += x.mobStr[z];
  x.enemyStr = enemyStr;
  if (variant(ctx).hold) {
    const condition = tick - x.lastThreatTick < DANGER_MEMORY || enemyNearHome > 0 || enemyStr > ownStr || fighters < HOLD_MIN_FIGHTERS;
    // hysteresis: the doctrine stands down only after CALM_TICKS without a condition (no flicker)
    if (condition) x.calmSince = -1;
    else if (x.calmSince < 0) x.calmSince = tick;
    const danger = condition || (x.danger && tick - x.calmSince < CALM_TICKS);
    if (danger !== x.danger) {
      x.danger = danger;
      x.stats.holds = (x.stats.holds ?? 0) + (danger ? 1 : 0);
      if (!danger) {
        // the counter-offensive: every defender back to the census, which hands 75 % of them to the attack task
        let n = 0;
        for (let m = 0; m < NMINORS; m++) {
          const mn = minorAddr(1, m);
          if (!u8(kai, mn + MN.ACTIVE)) continue;
          for (const o of listOf(gs, kai, 1, m)) {
            const a = objAddr(o);
            const type = u8(gs, a + O.TYPE);
            if (!(type < 16) || G.tables.types[type].weapon[0] === -1) continue; // builders and deployed towers stay
            unlink(gs, kai, o, 1, m);
            w8(gs, a + OA.STATUS, 0);
            n++;
          }
        }
        x.stats.counter = (x.stats.counter ?? 0) + 1;
        ctx.say(`The danger is over: ${n} defenders join the counter-offensive.`);
      } else ctx.say(`Danger (enemy ${enemyStr} vs ours ${ownStr}, ${fighters} fighters, ${enemyNearHome} at the gates): the army holds at the HQ.`);
    }
    w32(kai, K.SPLIT, danger ? SPLIT_HOLD : SPLIT_ORIGINAL);
  }
  x.aggressor = -1;
  if (variant(ctx).focus) {
    let best = 0;
    for (let q = 0; q < 8; q++) {
      if (x.hostility[q] >= REACT_MIN && x.hostility[q] > best && isEnemy(G, p, q)) {
        best = x.hostility[q];
        x.aggressor = q;
      }
    }
  }
  if (variant(ctx).mines && x.threatZone >= 0) addRouteSpots(ctx, x.threatZone);
}

// ---- production: one purchase before the goal chain ----------------------------------------------

/** Buy one unit of the first buyable type in `types`; false when none is buyable or affordable. */
function buy(ctx, types, n, reason) {
  const { G, p } = ctx;
  const it = buyableTroops(G, p).find((i) => types.includes(i.type));
  if (!it || money(G, p) < it.cost * n) return false;
  spend(G, p, it.cost * n);
  sendBuildUnits(ctx, it.type, n);
  ctx.say(`${reason}: ${n} ${G.tables.types[it.type].name.toLowerCase()}.`);
  return true;
}

/**
 * The extensions' purchases, at most one per think, BEFORE the goal chain, which then runs with what is
 * left (the return value, "replace the chain", is false: the original's economy and tech keep going).
 * Defence first (hold / react), then turrets, engineers, scouts, a detector - the last four only with a
 * money reserve so that the chain's next goal is not starved.
 */
/** VARIANT hold: the opening - no factory yet and fewer than three workers-or-mines: the mines come first. */
function opening(ctx) {
  const x = state(ctx);
  if (!x || !variant(ctx).hold) return false;
  const { G, p } = ctx;
  if (i32(G.gs, playerAddr(p) + P.SLOT_HP + 4 * 3) !== 0) return false;
  return listOf(G.gs, ctx.kai, 0, 0).length < 3;
}

/**
 * VARIANT hold: during the opening the goal chain builds only the HQ and the barracks and buys no army
 * (the few troopers are hold's own purchases), so that the money goes to the three mining sites; the
 * factory follows as soon as they stand (maintainer, 7 Oct 2026).
 */
export function skipGoal(ctx, kind, param) {
  if (!opening(ctx)) return false;
  if (kind === 1) return param !== 8 && param !== 0;
  return kind === 2;
}

export function production(ctx, have) {
  const x = state(ctx);
  if (!x) return false;
  const v = variant(ctx);
  const { G, p } = ctx;
  if (G.stat(6, p) >= i32(G.gs, GS.UNIT_CAP)) return false;
  // the hold doctrine buys a fighter when intruders at the gates outnumber ours there, or, with nobody at
  // the gates, while fewer than HOLD_MIN_FIGHTERS stand; tanks first once the factory and science exist
  const holdBuys = v.hold && x.danger && x.fighters < (opening(ctx) ? HOLD_OPENING_MAX : 1000) && (x.enemyNearHome > 0 ? x.ownNearHome < x.enemyNearHome + HOLD_MARGIN : x.fighters < HOLD_BUY_MIN);
  // with hold on, hold rules the purchases (react's "a fighter per think while a threat is near" starved the mines in the arena)
  if (holdBuys || (v.react && !v.hold && x.threatZone >= 0)) {
    if (buy(ctx, TANKS, 1, 'Defence') || buy(ctx, INFANTRY, 1, 'Defence')) x.stats.reacts++;
    return false;
  }
  if (v.hold && x.danger) {
    // under danger the money is for fighters, turrets and the chain's tech, nothing else
    if (v.fortify && have[1] < Math.min(8, 2 + x.own.mines) && buy(ctx, TOWER_BUILDERS, 1, 'Fortify')) x.stats.turrets++;
    return false;
  }
  const spare = money(G, p) - RESERVE;
  if (v.fortify && have[1] < Math.min(8, 2 + x.own.mines) && spare >= 900 && buy(ctx, TOWER_BUILDERS, 1, 'Fortify')) x.stats.turrets++;
  else if (v.mines && x.own.mines >= 1 && have[8] < Math.min(10, 2 + 2 * x.own.mines) && spare >= 450 && buy(ctx, ENGINEERS, 1, 'Mines')) return false;
  else if (v.airscout && have[5] < 2 && spare >= 600 && buy(ctx, SCOUTS, 1, 'Air patrol')) return false;
  else if (v.clear && x.enemy.hiddenMines.length && have[4] === 0 && spare >= 1500 && buy(ctx, DETECTORS, 1, 'Mine clearing')) return false;
  return false;
}

// ---- the census hands some units to the extension slots --------------------------------------------

/** Activate attack-task slot m for the extension kind (an empty list standing at home). */
function ensureSlot(ctx, m, kind) {
  const x = state(ctx);
  const { kai } = ctx;
  const mn = minorAddr(2, m);
  if (!u8(kai, mn + MN.ACTIVE)) {
    w8(kai, mn + MN.ACTIVE, 1);
    w16(kai, mn + MN.HEAD, -1);
    w16(kai, mn + MN.TAIL, -1);
    w32(kai, mn + MN.ZONE, x.home);
    w32(kai, mn + MN.DEST, x.home);
    w8(kai, mn + MN.STATE, 0);
    w16(kai, mn + MN.STEP, 0);
  }
  x.special[m] = kind;
  return m;
}

/** Give slot m's units back to the census and free the slot. */
function release(ctx, m) {
  const x = state(ctx);
  const { G, kai } = ctx;
  const gs = G.gs;
  for (const o of listOf(gs, kai, 2, m)) {
    const a = objAddr(o);
    w8(gs, a + OA.STATUS, 0);
    w16(gs, a + OA.NEXT, NO_GROUP);
  }
  w8(kai, minorAddr(2, m) + MN.ACTIVE, 0);
  w16(kai, minorAddr(2, m) + MN.HEAD, -1);
  w16(kai, minorAddr(2, m) + MN.TAIL, -1);
  x.special[m] = 0;
  x.squads.delete(m);
}

const slotUnits = (ctx, m, types = null) =>
  listOf(ctx.G.gs, ctx.kai, 2, m).filter((o) => {
    const a = objAddr(o);
    return alive(u8(ctx.G.gs, a + O.LIFE)) && (!types || types.includes(u8(ctx.G.gs, a + O.TYPE)));
  });

/** The slot a free unit goes to, or -1 for the original routing. */
export function take(ctx, o, cls) {
  const x = state(ctx);
  if (!x) return -1;
  const v = variant(ctx);
  const type = u8(ctx.G.gs, objAddr(o) + O.TYPE);
  if (v.mines && ENGINEERS.includes(type)) return ensureSlot(ctx, ENG_SLOT, KIND_ENGINEERS);
  if (v.clear && DETECTORS.includes(type) && x.enemy.hiddenMines.length && slotUnits(ctx, CLEAR_SLOT).length === 0) return ensureSlot(ctx, CLEAR_SLOT, KIND_CLEARERS);
  if (v.pressure && x.forming >= 0 && x.pending > 0 && cls === 0) {
    x.pending--;
    return x.forming;
  }
  return -1;
}

// ---- the tasks after the original's four -----------------------------------------------------------

export function tasks(ctx) {
  const x = state(ctx);
  if (!x) return;
  const v = variant(ctx);
  if (v.pressure) squads(ctx);
  if (v.mines) engineers(ctx);
  if (v.clear) clearers(ctx);
  if (v.hold && x.danger) rally(ctx);
  else if (v.react) reactMoves(ctx);
}

/**
 * Under danger every defender (all defend groups) and every parked attack group hunts the intruders:
 * each fighter assault-moves onto the nearest known enemy fighter within RALLY_RADIUS of the HQ (one
 * order per intruder, re-issued every RALLY_EVERY ticks, like the rusher's own defence), or, with no
 * intruder known, gathers at the HQ tile. The units are marked as ordered to their group's zone so that
 * move_group leaves them alone, and the home guard's destination is pinned to the home zone.
 */
function rally(ctx) {
  const x = state(ctx);
  const { G, kai } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  const guard = minorAddr(1, 0);
  if (u8(kai, guard + MN.ACTIVE) && (i32(kai, guard + MN.DEST) & 0xff) !== x.home) setRoute(ctx, 1, 0, x.home, null);
  if (tick - x.lastRally < RALLY_EVERY) return;
  x.lastRally = tick;
  const objs = [];
  const builders = [];
  const collect = (t, m) => {
    const zone = i32(kai, minorAddr(t, m) + MN.ZONE) & 0xff;
    for (const o of listOf(gs, kai, t, m)) {
      const a = objAddr(o);
      if (!alive(u8(gs, a + O.LIFE))) continue;
      const type = u8(gs, a + O.TYPE);
      if (TOWER_BUILDERS.includes(type)) {
        w8(gs, a + OA.ZONE, zone);
        builders.push(o);
        continue;
      }
      if (!(type < 16) || G.tables.types[type].weapon[0] === -1) continue;
      w8(gs, a + OA.ZONE, zone);
      objs.push(o);
    }
  };
  for (let m = 0; m < NMINORS; m++) if (u8(kai, minorAddr(1, m) + MN.ACTIVE)) collect(1, m);
  for (const m of [0, 1]) {
    const mn = minorAddr(2, m);
    if (!u8(kai, mn + MN.ACTIVE)) continue;
    const st = u8(kai, mn + MN.STATE);
    if (st === 2 || st === 3) collect(2, m);
  }
  // turret builders: to the HQ, and deploy once within TURRET_RING of it
  for (const o of builders) {
    const a = objAddr(o);
    const [ux, uz] = tileOf(gs, a);
    const last = x.lastOrder.get(o) ?? -1000000;
    if (cheb(ux, uz, x.hq[0], x.hq[1]) <= TURRET_RING) {
      if (tick - last > DEPLOY_REPEAT) {
        sendOrder(ctx, o, ORDER_DEPLOY);
        x.lastOrder.set(o, tick);
        ctx.say(`Turret deployed at ${ux},${uz}.`);
      }
    } else if (isIdle(gs, a) || tick - last > REISSUE) {
      sendWaypointOrder(ctx, [o], [[(x.hq[0] << 8) + 128, (x.hq[1] << 8) + 128]], ORDER_MOVE);
      x.lastOrder.set(o, tick);
    }
  }
  if (!objs.length) return;
  x.stats.rallies = (x.stats.rallies ?? 0) + 1;
  if (!x.intruders.length) {
    sendWaypointOrder(ctx, objs, [[(x.hq[0] << 8) + 128, (x.hq[1] << 8) + 128]], ORDER_ASSAULT);
    return;
  }
  const byTarget = new Map();
  for (const o of objs) {
    const [ux, uz] = tileOf(gs, objAddr(o));
    let best = null;
    let bestD = 1e9;
    for (const r of x.intruders) {
      const d = cheb(ux, uz, r.x, r.z);
      if (d < bestD) {
        bestD = d;
        best = r;
      }
    }
    const key = `${best.x},${best.z}`;
    if (!byTarget.has(key)) byTarget.set(key, { x: best.x, z: best.z, objs: [] });
    byTarget.get(key).objs.push(o);
  }
  for (const t of byTarget.values()) sendWaypointOrder(ctx, t.objs, [[(t.x << 8) + 128, (t.z << 8) + 128]], ORDER_ASSAULT);
}

/** Stalest reachable zone at least three hops out: where a squad without a target goes. */
function scoutZone(ctx) {
  const x = state(ctx);
  const { kai } = ctx;
  const tick = ctx.tick | 0;
  let best = -1;
  let bestScore = -1;
  for (let z = 1; z < 255; z++) {
    const hop = u8(kai, zoneAddr(z) + Z.HOP);
    if (hop === 0xff || hop < 3) continue;
    const score = x.lastSeen[z] < 0 ? 100000 + hop : tick - x.lastSeen[z];
    if (score > bestScore) {
      bestScore = score;
      best = z;
    }
  }
  return best;
}

/** Nearest remembered enemy mine, else building, else a scouting zone; `skip` = the key just finished. */
function squadTarget(ctx, cx, cz, skip) {
  const x = state(ctx);
  const pick = (list, kind) => {
    let best = null;
    let bestD = 1e9;
    for (const r of list) {
      const key = `${kind}:${r.o}`;
      if (key === skip) continue;
      const d = cheb(cx, cz, r.x, r.z);
      if (d < bestD) {
        bestD = d;
        best = { ...r, kind, key };
      }
    }
    return best;
  };
  let t = null;
  if (x.aggressor >= 0) {
    const his = (list) => list.filter((r) => r.team === x.aggressor);
    t = pick(his(x.enemy.mines), 'mine') ?? pick(his(x.enemy.buildings), 'base');
  }
  t = t ?? pick(x.enemy.mines, 'mine') ?? pick(x.enemy.buildings, 'base');
  if (t) return t;
  const z = scoutZone(ctx);
  if (z < 0) return null;
  const [zx, zz] = centreOf(ctx.kai, z);
  return { o: -1, x: zx, z: zz, kind: 'scout', key: `scout:${z}` };
}

function squads(ctx) {
  const x = state(ctx);
  const { G, p, kai } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  if (x.forming >= 0 && (x.pending === 0 || tick - x.formingSince > FORMING_MAX)) {
    x.pending = 0;
    x.forming = -1;
  }
  const activeSquads = SQUAD_SLOTS.filter((m) => x.special[m]).length;
  const barracks = i32(gs, playerAddr(p) + P.SLOT_HP + 4) !== 0;
  const cap = i32(gs, GS.UNIT_CAP);
  if (x.forming < 0 && barracks && !x.danger && x.own.mines >= 1 && tick - x.lastPressure >= PRESSURE_EVERY && activeSquads < MAX_SQUADS && G.stat(6, p) + PRESSURE_SIZE <= cap) {
    const it = buyableTroops(G, p).find((i) => INFANTRY.includes(i.type));
    const slot = SQUAD_SLOTS.find((m) => !x.special[m]);
    if (it && slot !== undefined && money(G, p) >= it.cost * PRESSURE_SIZE + RESERVE) {
      spend(G, p, it.cost * PRESSURE_SIZE);
      sendBuildUnits(ctx, it.type, PRESSURE_SIZE);
      ensureSlot(ctx, slot, KIND_SQUAD);
      x.forming = slot;
      x.pending = PRESSURE_SIZE;
      x.formingSince = tick;
      x.lastPressure = tick;
      x.stats.squads++;
      ctx.say(`Pressure squad ${slot}: ${PRESSURE_SIZE} ${G.tables.types[it.type].name.toLowerCase()} ordered.`);
    }
  }
  for (const m of SQUAD_SLOTS) {
    if (x.special[m] !== KIND_SQUAD) continue;
    const units = slotUnits(ctx, m);
    if (units.length === 0) {
      if (m !== x.forming) release(ctx, m);
      continue;
    }
    if (m === x.forming) continue;
    let sq = x.squads.get(m);
    if (!sq) {
      sq = { tx: -1, tz: -1, o: -1, key: '', issued: -1000000 };
      x.squads.set(m, sq);
    }
    let sx = 0;
    let sz = 0;
    let idle = true;
    let arrived = false;
    for (const o of units) {
      const a = objAddr(o);
      const [ux, uz] = tileOf(gs, a);
      sx += ux;
      sz += uz;
      if (!isIdle(gs, a)) idle = false;
      if (sq.tx >= 0 && cheb(ux, uz, sq.tx, sq.tz) <= 2) arrived = true;
    }
    sx = Math.round(sx / units.length);
    sz = Math.round(sz / units.length);
    if (arrived && sq.o >= 0) {
      // at the target: attack the remembered object while it stands and we still know it is there
      const ta = objAddr(sq.o);
      const stillThere = alive(u8(gs, ta + O.LIFE)) && isEnemy(G, p, u8(gs, ta + O.TEAM)) && i8(kai, memAddr(sq.o) + 2) !== -1;
      if (stillThere) {
        if (tick - sq.issued >= REISSUE) {
          for (const o of units) ctx.emit([build.targetObj(o, sq.o), build.order(o, 0x0e)]);
          sq.issued = tick;
        }
        continue;
      }
    }
    if (sq.tx < 0 || idle || arrived || tick - sq.issued > RETARGET) {
      const target = squadTarget(ctx, sx, sz, arrived ? sq.key : '');
      if (!target) continue;
      sq.tx = target.x;
      sq.tz = target.z;
      sq.o = target.o;
      sq.key = target.key;
      sq.issued = tick;
      x.stats.raids++;
      sendWaypointOrder(ctx, units, [[(target.x << 8) + 128, (target.z << 8) + 128]], ORDER_ASSAULT);
      ctx.say(`Squad ${m} (${units.length}) raids the ${target.kind} at ${target.x},${target.z}.`);
    }
  }
}

// ---- mines: spots and engineers ---------------------------------------------------------------------

/**
 * The walkable boundary cells of zone a towards zone b for every (a, b) in `wanted` (Map a -> Set b),
 * one pass over the map. Returns Map "a-b" -> [[x, z], ...].
 */
function boundaryCells(G, wanted) {
  const out = new Map();
  const w = G.map.w;
  const h = G.map.h;
  for (let z = 1; z < h - 1; z++) {
    for (let x = 1; x < w - 1; x++) {
      const a = famAt(G, x, z);
      const bs = wanted.get(a);
      if (!bs) continue;
      for (const b of bs) {
        if (famAt(G, x + 1, z) !== b && famAt(G, x - 1, z) !== b && famAt(G, x, z + 1) !== b && famAt(G, x, z - 1) !== b) continue;
        if (!Grid.isWalkable(G, x, z)) continue;
        const key = `${a}-${b}`;
        if (!out.has(key)) out.set(key, []);
        out.get(key).push([x, z]);
      }
    }
  }
  return out;
}

/** The spot of a boundary: the walkable cell nearest its mean; four cells or fewer make a bridge. */
function spotOf(cells, kind, key) {
  if (!cells || cells.length === 0) return null;
  let mx = 0;
  let mz = 0;
  for (const [x, z] of cells) {
    mx += x;
    mz += z;
  }
  mx /= cells.length;
  mz /= cells.length;
  let best = cells[0];
  let bestD = 1e9;
  for (const c of cells) {
    const d = (c[0] - mx) ** 2 + (c[1] - mz) ** 2;
    if (d < bestD) {
      bestD = d;
      best = c;
    }
  }
  return { x: best[0], z: best[1], kind: cells.length <= 4 ? 'bridge' : kind, width: cells.length, done: false, tries: 0, key };
}

function computeSpots(ctx) {
  const x = state(ctx);
  const { G } = ctx;
  const home = x.home;
  const ring1 = new Set();
  for (let k = adjCount(G, home); k >= 1; k--) ring1.add(adj(G, home, k));
  const wanted = new Map();
  const kinds = new Map();
  const want = (a, b, kind) => {
    if (!wanted.has(a)) wanted.set(a, new Set());
    wanted.get(a).add(b);
    kinds.set(`${a}-${b}`, kind);
  };
  for (const b of ring1) want(home, b, 'perimeter');
  for (const z1 of ring1) for (let k = adjCount(G, z1); k >= 1; k--) {
    const n = adj(G, z1, k);
    if (n !== home && !ring1.has(n)) want(z1, n, 'outer');
  }
  const cells = boundaryCells(G, wanted);
  const spots = [];
  for (const [key, kind] of kinds) {
    const sp = spotOf(cells.get(key), kind, key);
    if (sp) {
      spots.push(sp);
      x.spotKeys.add(key);
    }
  }
  return spots;
}

/** Spots on the first two steps of the route from home towards a threatened zone. */
function addRouteSpots(ctx, zone) {
  const x = state(ctx);
  if (!x.spots) x.spots = computeSpots(ctx);
  const { G } = ctx;
  const wanted = new Map();
  let cur = x.home;
  for (let i = 0; i < 2 && cur !== zone; i++) {
    const next = G.map.path.routing[((cur & 0xff) << 8) + (zone & 0xff)];
    if (next === 0) break;
    const key = `${cur}-${next}`;
    if (!x.spotKeys.has(key)) {
      if (!wanted.has(cur)) wanted.set(cur, new Set());
      wanted.get(cur).add(next);
      x.spotKeys.add(key);
    }
    cur = next;
  }
  if (!wanted.size) return;
  const cells = boundaryCells(G, wanted);
  for (const [key, list] of cells) {
    const sp = spotOf(list, 'route', key);
    if (sp) x.spots.push(sp);
  }
}

const spotDone = (G, sp) => sp.done || Grid.secId(G, sp.x, sp.z) !== Grid.EMPTY;

function pickSpot(ctx, o, busy) {
  const x = state(ctx);
  const { G } = ctx;
  const [ux, uz] = tileOf(G.gs, objAddr(o));
  let best = -1;
  let bestKey = 1e9;
  for (let i = 0; i < x.spots.length; i++) {
    const sp = x.spots[i];
    if (busy.has(i) || spotDone(G, sp)) continue;
    const key = SPOT_PRIORITY[sp.kind] * 1000 + cheb(ux, uz, sp.x, sp.z);
    if (key < bestKey) {
      bestKey = key;
      best = i;
    }
  }
  return best;
}

function engineers(ctx) {
  const x = state(ctx);
  if (!x.special[ENG_SLOT]) return;
  const { G } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  if (!x.spots) x.spots = computeSpots(ctx);
  const units = slotUnits(ctx, ENG_SLOT, ENGINEERS);
  const busy = new Set();
  for (const [o, si] of x.assign) {
    if (units.includes(o)) busy.add(si);
    else x.assign.delete(o);
  }
  for (const o of units) {
    const a = objAddr(o);
    let si = x.assign.get(o);
    if (si === undefined || spotDone(G, x.spots[si])) {
      si = pickSpot(ctx, o, busy);
      if (si < 0) continue;
      x.assign.set(o, si);
      busy.add(si);
    }
    const sp = x.spots[si];
    const [ux, uz] = tileOf(gs, a);
    const last = x.lastOrder.get(o) ?? -1000000;
    if (ux === sp.x && uz === sp.z) {
      if (Grid.secId(G, ux, uz) !== Grid.EMPTY) sp.done = true; // somebody's mine is here already
      else if (tick - last > DEPLOY_WAIT || (isIdle(gs, a) && tick - last > REISSUE)) {
        // one deploy order; the animation takes a while and a repeated order would restart it
        sendOrder(ctx, o, ORDER_DEPLOY);
        x.lastOrder.set(o, tick);
        x.stats.minesLaid++;
        ctx.say(`Mine laid at ${ux},${uz} (${sp.kind}, ${sp.width} cells wide).`);
      }
      continue;
    }
    if (isIdle(gs, a) || tick - last > REISSUE) {
      if (++sp.tries > 8) {
        sp.done = true; // unreachable: give it up
        continue;
      }
      sendWaypointOrder(ctx, [o], [[(sp.x << 8) + 128, (sp.z << 8) + 128]], ORDER_MOVE);
      x.lastOrder.set(o, tick);
    }
  }
}

// ---- mine clearing -----------------------------------------------------------------------------------

function clearers(ctx) {
  const x = state(ctx);
  const { G, p, kai } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  const mines = x.enemy.hiddenMines;
  const units = x.special[CLEAR_SLOT] ? slotUnits(ctx, CLEAR_SLOT) : [];
  if (!mines.length) {
    if (x.special[CLEAR_SLOT] && tick - x.lastMineSeen > 3000) release(ctx, CLEAR_SLOT); // nothing to clear: back to the army
    return;
  }
  x.lastMineSeen = tick;
  if (!units.length) {
    // no detector yet (production buys one): a mech of attack group 1 walks onto the mine meanwhile
    const tank = listOf(gs, kai, 2, 1).find((o) => alive(u8(gs, objAddr(o) + O.LIFE)) && TANKS.includes(u8(gs, objAddr(o) + O.TYPE)));
    if (tank === undefined) return;
    unlink(gs, kai, tank, 2, 1);
    ensureSlot(ctx, CLEAR_SLOT, KIND_CLEARERS);
    link(gs, kai, tank, 2, CLEAR_SLOT);
    x.stats.clears++;
    ctx.say(`A ${G.tables.types[u8(gs, objAddr(tank) + O.TYPE)].name.toLowerCase()} goes to clear a mine.`);
    return;
  }
  for (const o of units) {
    const a = objAddr(o);
    const [ux, uz] = tileOf(gs, a);
    let best = null;
    let bestD = 1e9;
    for (const m of mines) {
      const d = cheb(ux, uz, m.x, m.z);
      if (d < bestD) {
        bestD = d;
        best = m;
      }
    }
    const last = x.lastOrder.get(o) ?? -1000000;
    if (!isIdle(gs, a) && tick - last < REISSUE) continue;
    if (DETECTORS.includes(u8(gs, a + O.TYPE))) ctx.emit([build.targetObj(o, best.o), build.order(o, 0x0e)]);
    else sendWaypointOrder(ctx, [o], [[(best.x << 8) + 128, (best.z << 8) + 128]], ORDER_MOVE);
    x.lastOrder.set(o, tick);
    void p;
  }
}

// ---- reactive moves ---------------------------------------------------------------------------------

function reactMoves(ctx) {
  const x = state(ctx);
  const { G, kai } = ctx;
  const tick = ctx.tick | 0;
  if (x.threatZone < 0) return;
  if (tick - x.lastReact < REACT_EVERY) return;
  x.lastReact = tick;
  for (const m of [0, 1]) {
    const mn = minorAddr(2, m);
    if (!u8(kai, mn + MN.ACTIVE)) continue;
    const st = u8(kai, mn + MN.STATE);
    if (st !== 2 && st !== 3) continue; // a marching group keeps its target
    if ((i32(kai, mn + MN.DEST) & 0xff) === x.threatZone && st === 0) continue;
    setRoute(ctx, 2, m, x.threatZone, null);
    w8(kai, mn + MN.STATE, 0);
  }
  const guard = minorAddr(1, 0);
  if (u8(kai, guard + MN.ACTIVE) && (i32(kai, guard + MN.DEST) & 0xff) !== x.threatZone && u8(kai, zoneAddr(x.threatZone) + Z.HOP) <= 2) {
    setRoute(ctx, 1, 0, x.threatZone, null);
    G.globals.krustyReissue = 1;
  }
  ctx.say(`Threat ${x.threatStr} at zone ${x.threatZone}: the guard and the parked groups go there.`);
}

// ---- air patrol ----------------------------------------------------------------------------------------

/** VARIANT airscout: a flyer of the scouting task gets a patrol destination; true when handled. */
export function airPatrol(ctx, o, idle) {
  const x = state(ctx);
  if (!x || !variant(ctx).airscout) return false;
  const { G, p, kai } = ctx;
  const gs = G.gs;
  const a = objAddr(o);
  const t = G.tables.types[u8(gs, a + O.TYPE)];
  if (!t || !t.fly) return false;
  const tick = ctx.tick | 0;
  const last = x.lastOrder.get(o) ?? -1000000;
  if (!idle && tick - last < 1500) return true; // on its way
  let best = -1;
  let bestScore = -1;
  for (let z = 1; z < 255; z++) {
    const [cx, cz] = centreOf(kai, z);
    if (cx === 0 && cz === 0) continue;
    if (tick - x.claim[z] < PATROL_CLAIM) continue;
    let aa = false;
    const own = i8(kai, zoneAddr(z) + Z.AA_OWNER);
    if (own !== -1 && own !== p) aa = true;
    for (let k = adjCount(G, z); k >= 1 && !aa; k--) {
      const ow = i8(kai, zoneAddr(adj(G, z, k)) + Z.AA_OWNER);
      if (ow !== -1 && ow !== p) aa = true;
    }
    if (aa) continue;
    let score = x.lastSeen[z] < 0 ? 100000 : tick - x.lastSeen[z];
    if (score < 300) continue;
    if (x.interest[z]) score += 3000;
    if (score > bestScore) {
      bestScore = score;
      best = z;
    }
  }
  if (best < 0) return true;
  x.claim[best] = tick;
  x.lastOrder.set(o, tick);
  w8(gs, a + OA.STATUS, 4);
  const [cx, cz] = centreOf(kai, best);
  sendWaypointOrder(ctx, [o], [[cx << 8, cz << 8]], ORDER_MOVE);
  x.stats.patrols++;
  return true;
}

// ---- focus: the aggressor's zones score higher ----------------------------------------------------

/** VARIANT focus: the multiplier of a zone's target score; 3 for a zone the aggressor holds, else 1. */
export function focus(ctx, z) {
  const x = state(ctx);
  if (!x || x.aggressor < 0) return 1;
  const { kai } = ctx;
  const q = x.aggressor;
  if (x.mobOwner[z] === q || i8(kai, zoneAddr(z) + Z.G_OWNER) === q || x.interestTeam[z] === q) return 3;
  return 1;
}

// ---- upgrades by experience ------------------------------------------------------------------------

/**
 * VARIANT upgrades=experience: the upgrade to buy for `which` (0 weapon, 1 armour), or null. Weapon:
 * the fielded type with the most kills (an anti-air type first once enemy flyers were seen); armour:
 * the fielded type with the most losses. Only with UPGRADE_RESERVE left after the price.
 */
export function upgradeWanted(ctx, which) {
  const x = state(ctx);
  if (!x) return null;
  const { G, p } = ctx;
  const ups = buyableUpgrades(G, p, which).filter((u) => u.fielded > 0);
  if (!ups.length) return null;
  let pick = null;
  if (which === 0 && x.enemy.flyers > 0) {
    pick = ups.find((u) => {
      const t = G.tables.types[u.type];
      const w = t.weapon[0];
      return w !== -1 && G.tables.mbullet.rows[G.tables.weapons[w].weaponClass][2] > 0;
    }) ?? null;
  }
  if (!pick) {
    const score = (u) => (which === 0 ? G.typeStat(3, p, u.type) : G.typeStat(0, p, u.type));
    const scored = ups.filter((u) => score(u) > 0).sort((a, b) => score(b) - score(a) || a.item - b.item);
    pick = scored[0] ?? null;
  }
  if (!pick) return null;
  if (money(G, p) < pick.cost + UPGRADE_RESERVE) return null;
  return pick;
}

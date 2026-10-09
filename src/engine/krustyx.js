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
//  * landmines (krusty.js; 8 Oct 2026): a third pool, the original's ground strength with the land mines
//    left out, read by the attack task's gate and target test (gatePool).
//
// 8 Oct 2026, after the traces of the losses to the rusher (plan §19.14: no robot factory by tick 9000 in
// 52 of 56 games, the danger flag on 97-99 % of every game, defenders bought and fed in one by one,
// explorers walking into the rusher, the counter-offensive leaving outnumbered), maintainer: "implement
// all five points" and the escorted expansion:
//  * factory: once the first wave was seen at the base or a mine, the opening's three-site rule is lifted
//    and the bot SAVES for the tanks (science, then the robot factory: 4000): no army goal, no
//    expansion, no turrets or scouts, and hold buys a trooper only in an emergency (fewer than
//    HOLD_BUY_MIN fighters, or outnumbered at the gates). Ends when a mech is buyable.
//  * alarm: DANGER = an enemy fighter seen NOW near the HQ (RALLY_RADIUS) or within MINE_RADIUS of a
//    mine or turret, kept ALARM_MEMORY ticks; the conditions "fewer than 6 fighters" and "remembered
//    enemy strength above ours" (always true against a rusher) are gone.
//  * batch: hold buys troopers BATCH_SIZE at a time (one only when nobody stands at the gates of an
//    attacked HQ), tanks one at a time; under danger the defenders gather at the HQ and hunt the
//    intruder nearest to it only as a group of BATCH_SIZE (or at once when it is within HQ_THREAT_RADIUS
//    of the HQ); units already in the fight keep fighting.
//  * safe: an explorer's way to a vent is tested against the mobile pool (enemy units, not towers or
//    buildings), and no explorer is bought for EXPL_COOLDOWN ticks after one died.
//  * counter: the defenders are released into the counter-offensive (and the split goes back to 75 %
//    attack) only when our fighting power (infantry 1, mech 3, other 2) reaches COUNTER_RATIO times the
//    power of the strongest enemy as far as we know it, and at least COUNTER_MIN; until then everything
//    stays in the defend task.
//  * escort: an explorer for a vent beyond ESCORT_FREE_HOP hops of home goes with ESCORT_SIZE troopers
//    (taken from the defenders, the missing ones bought), slot ESCORT_SLOT: they gather at the HQ, march
//    on the vent, the explorer FOLLOW tiles behind them (HOLD_BACK while they fight); an enemy fighter
//    seen within CONTACT_RADIUS asks for a reinforcement every REINFORCE_EVERY ticks (a mech, else two
//    troopers); at the vent the explorer deploys while the troopers guard it, then the troopers return to
//    the census. When the escort dies the explorer is recalled to the HQ and a new try starts after
//    RETRY_AFTER ticks (a vent that failed scores worse for a while).

import { GS, O, P, u8, i8, i16, i32, w8, w16, w32, objAddr, playerAddr, MAX_OBJECTS } from './mem.js';
import * as Grid from './grid.js';
import { build } from '../commands.js';
import {
  variant, listOf, link, unlink, setRoute, setGoal, sendWaypointOrder, sendOrder, sendBuildUnits, money, spend, buyableTroops,
  buyableUpgrades, centreOf, seenByPlayer, famAt, adjCount, adj, zoneAddr, memAddr, minorAddr,
  K, Z, MN, OA, NO_GROUP, NZONES, NMINORS, ORDER_MOVE, ORDER_ASSAULT, ORDER_DEPLOY,
} from './krusty.js';

export const SQUAD_SLOTS = Object.freeze([8, 9, 10, 11]);
export const COMMANDER_SLOT = 12; // VARIANT lieutenant
export const ESCORT_SLOT = 13;
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
const SPLIT_HOLD = 0; // kai split: everything to the defend task
const SPLIT_ORIGINAL = 0xc0; // the original 75 % attack
const REACT_EVERY = 320; // ticks between reactive re-routes
const UPGRADE_RESERVE = 1500; // money kept for the army when an upgrade is bought
const RESERVE = 1000; // money left to the goal chain when an extension buys turrets, engineers, scouts
const KIND_SQUAD = 1;
const KIND_ENGINEERS = 2;
const KIND_CLEARERS = 3;
const KIND_ESCORT = 4;
const KIND_COMMANDER = 5;
const COMMANDER_TYPES = [69, 70, 71, 72, 73, 74, 75, 76]; // VARIANT lieutenant: the four ranks of both races
const COMMANDER_ENGAGE = 14; // tiles around the commander or the HQ within which a seen enemy fighter matters
const CLUSTER_RADIUS = 6; // tiles: a fighter and ours this close to it make a group (the largest one is followed)
const COMMANDER_BEHIND = 3; // tiles behind the centre of our fighters, away from the enemy
const LT_RETREAT = 4; // VARIANT lieutenant: tiles the commander steps back when hit
const LT_RETREAT_HOLD = 96; // ... and ticks it stays back before it attacks again
const STAR_REACH = 8; // tiles: enemies this close to our fighters, ours this close to the commander
const STAR_MIN = 3; // our armed units within STAR_REACH of the commander before the star command
// 8 Oct 2026 (factory, alarm, batch, safe, counter, escort)
const ALARM_MEMORY = 320; // ticks a live sighting keeps the alarm on (then CALM_TICKS of hysteresis)
const DEFEND_ASSET_RADIUS = 10; // tiles around an own mine or turret within which an enemy fighter is attacking it (the rusher's 10)
const DEFEND_REACH = 30; // tiles from a threat within which every armed unit of ours joins the defence
const PUSH_LINK = 12; // tiles: a seen enemy fighter this close to one the defence hunted last think belongs to the same attack
const SHOOTER_RADIUS = 24; // tiles from the HQ or a mine / turret within which an enemy firing at us is a threat at once
const NEAR_RADIUS = 32; // tiles from the HQ within which an enemy fighter is "nearby" while an attack is on ...
const NEAR_ASSET_RADIUS = 16; // ... and from an own mine or turret
const BACKUP_RADIUS = 48; // tiles from the HQ within which an enemy fighter coming closer is the attacker's backup
const IN_FIGHT = 6; // tiles: a unit with a seen enemy fighter this close is fighting it - the defence does not re-target it
const CONTACT = 8; // tiles: a unit with a seen enemy fighter this close is in contact - no order may take it away (holdGround)
const RETREAT_SLACK = 2; // tiles: an order taking a unit in contact further than this from its nearest enemy is a retreat
const TURRET_ENGAGE = 12; // tiles: an enemy fighter this close to a turret builder is an attack it lands against
const TURRET_MARGIN = 2; // tiles beyond the enemies' weapon range where a turret builder lands (the deployment takes time)
const TURRET_SPOT = 4; // tiles from the HQ of the spots a turret builder without a group zone goes to
const QUEUE_FULL = 3; // units waiting in a production queue at which no more is bought into it
const QUEUE_STALL = 256; // ticks without a unit out of a non-empty queue after which it counts as stalled
const GATHER_FRONT = 5; // tiles from the HQ towards the danger where the rally gathers (on the HQ tile it crowded the doors)
const DOOR_STEP = 2; // tiles a unit standing on a building's spawn tile is moved outwards
const IDLE_ENGAGE = 8; // tiles: an idle armed unit of ours with an enemy fighter this close attacks it, wherever it stands
const DEFEND_EVERY = 96; // ticks between two rounds of defence orders (the rusher's 96); an idle defender is ordered at once
const MINE_RADIUS = 6; // tiles around an own mine or turret within which an enemy fighter raises the alarm
const BATCH_SIZE = 3; // troopers bought and sent together
const GATHER_RADIUS = 6; // tiles around the HQ where the defenders gather
const HQ_THREAT_RADIUS = 7; // an intruder this close to the HQ is attacked at once, group or not
const FIGHT_RADIUS = 6; // a defender this close to the hunted intruder is in the fight and stays in it
const EXPL_COOLDOWN = 1500; // ticks without a new explorer after one died
const COUNTER_MIN = 8; // fighting power below which no counter-offensive leaves
const COUNTER_RATIO = 1.5; // our power against the strongest known enemy's
export const ESCORT_FREE_HOP = 1; // vents this close to home are reached without an escort
const ESCORT_SIZE = 3;
const SECOND_ESCORT = 1; // VARIANT second: the trooper that leads the second explorer
const MECH_FIRST = 3; // VARIANT mechfirst: mechs produced before the third explorer
const SECOND_FIRST_MAX = 3000; // VARIANT second: ticks after which the barracks no longer waits for the second explorer
const ESCORT_HOME_KEEP = 2; // defenders left at home when the escort is taken from them
const FOLLOW = 4; // tiles the explorer keeps behind its escort
const HOLD_BACK = 8; // ... while the escort fights
const CONTACT_RADIUS = 8; // tiles around the escort (or its explorer) within which a seen enemy fighter is contact
const ARRIVE = 3; // tiles from the vent at which the escort has arrived
const SHIELD_JOIN = 10; // VARIANT shield: tiles within which the explorer counts as with its escort
const SHIELD_SCAN = 12; // VARIANT shield: tiles around the escort whose seen enemy fighters give the side to avoid
const SHIELD_SIDE = 2; // VARIANT shield: tiles beyond the escort's centre on the far side
const DECOY_RADIUS = 10; // tiles around the meeting point within which the decoy escort picks its enemy
const DETOUR_CLEAR = 20; // VARIANT second, the detour: a vent this close to an enemy fighter seen is no way out
const DETOUR_PATH = 8; // tiles: the detour's path keeps this far from every enemy fighter we see (closer only where it starts)
const DETOUR_BASE = 30; // tiles: no detour to a vent this close to a known enemy building
const DETOUR_MAX = 120; // steps: the longest detour path searched
const ROUTE_POINTS = 8; // waypoints of a planned route (the game's object holds eight)
const GUARD_OFFSET = 2; // tiles short of the vent where the escort stands (the vent tile is the explorer's)
const ESCORT_MAX_REINF = 3; // reinforcement requests per expedition at most
const REINFORCE_EVERY = 480; // ticks between reinforcement requests while in contact
const RETRY_AFTER = 600; // ticks before a new try after a failed one
const GATHER_MAX = 1500; // ticks the escort waits at the HQ for its troopers
const ESCORT_MAX = 6000; // ticks an expedition may take at most
const PEND_MAX = 1500; // ticks a bought recruit is waited for
const FAIL_MEMORY = 4000; // ticks a failed vent scores worse
const EXPLORER_OBJ_TYPES = [6, 14];
const INTERPOSE_SCAN = 8; // tiles around an attacker searched for the building it is after (VARIANT patrol)
const INTERPOSE_REISSUE = 384; // ticks before a defender still walking to its spot gets a new one
const PATROL_OFFSET = 2; // tiles outside the buildings' box that the patrol ring keeps
const PATROL_STEP = 320; // ticks between two steps of the patrol round
const CHOKE_MAX = 7; // VARIANT choke: a base exit this many cells wide or narrower is held
const CHOKE_RING = 4; // the walk starts on the ring this many tiles around the HQ (the buildings stand inside it)
const CHOKE_FROM = 2; // ... and the exit is searched from this many steps beyond the ring
const CHOKE_TO = 20; // ... to this many
const CHOKE_FAR = 45; // a cell leads out of the base when a walk away from the HQ goes on to this many steps
const CHOKE_BACK = [2, 3, 4]; // steps inside the exit where the defenders stand, the nearest rows first
const CHOKE_SPREAD = 4; // tiles from the exit's centre within which a defender's spot lies
const ESCORT_SITES = 3; // mining sites the escort reaches under danger too, as the brief has it (three sites, then the factory)
const TYPE_VENT = 0x28;
const O_VENT_RATE = 0x32;
const NO_ESCORT = Object.freeze({ on: false, objs: new Set(), zone: 0 });
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
/** An idle unit that is not shooting (its top state is not the cooldown between two shots, 0x0B): it may be ordered. */
const idleNotFiring = (gs, a) => isIdle(gs, a) && u8(gs, a + O.STACK + 2 * u8(gs, a + O.SP)) !== 0x0b;
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

/** An armed ground unit (the census' fighters). */
const isFighter = (G, type) => type < 16 && G.tables.types[type].weapon[0] !== -1 && !G.tables.types[type].fly;
/** VARIANT counter: the fighting power of one unit - infantry 1, mech 3 (it kills a trooper four times faster), other 2. */
const powerOf = (type) => (INFANTRY.includes(type) ? 1 : TANKS.includes(type) ? 3 : 2);

/** Is any extension on for this think? */
function active(ctx) {
  const v = variant(ctx);
  return !!(
    v.ratio || v.pressure || v.fortify || v.react || v.mines || v.clear || v.airscout || v.upgrades || v.focus || v.hold ||
    v.landmines || v.factory || v.alarm || v.batch || v.safe || v.counter || v.escort || v.patrol || v.second || v.shield || v.gate || v.tech || v.upnow || v.mechfirst || v.lieutenant || v.noscout ||
    v.infup || v.keep2 || v.choke
  );
}

/** The extension state, created on first use; null in exact mode or without a switch. */
export function state(ctx) {
  if (!ctx.aux || !active(ctx)) return null;
  if (!ctx.aux.x) {
    ctx.aux.x = {
      mobOwner: new Int8Array(NZONES).fill(-1),
      mobStr: new Uint16Array(NZONES),
      gateOwner: new Int8Array(NZONES).fill(-1),
      gateStr: new Uint16Array(NZONES),
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
      seenFighters: [],
      liveThreat: 0,
      lastLiveThreat: -1000000,
      waveSeen: false,
      saving: false,
      explLost: 0,
      lastExplLoss: -1000000,
      power: 0,
      enemyPower: 0,
      holding: false,
      patrolPhase: -1,
      secondDone: false,
      thirdFailed: false, // VARIANT gate
      esc: newEscort(),
      stats: { squads: 0, raids: 0, minesLaid: 0, turrets: 0, reacts: 0, clears: 0, patrols: 0 },
    };
  }
  return ctx.aux.x;
}

export function isSpecial(ctx, m) {
  const x = ctx.aux?.x;
  return !!x && x.special[m] !== 0;
}

// ---- the mobile pool (VARIANT ratio) and the pool without land mines (VARIANT landmines) ----------

export function mobileReset(ctx) {
  const x = state(ctx);
  if (!x) return;
  x.mobOwner.fill(-1);
  x.mobStr.fill(0);
  x.gateOwner.fill(-1);
  x.gateStr.fill(0);
}

/** The influence map's pool rule: same owner adds, a stronger newcomer takes the zone. */
function poolAdd(owners, strs, zone, team, s) {
  const str = strs[zone];
  if (owners[zone] === team) strs[zone] = str + s;
  else if (s >= str) {
    strs[zone] = s - str;
    owners[zone] = team;
  } else strs[zone] = str - s;
}

export function mobileAdd(ctx, zone, team, s) {
  const x = state(ctx);
  if (x) poolAdd(x.mobOwner, x.mobStr, zone, team, s);
}

export function gateAdd(ctx, zone, team, s) {
  const x = state(ctx);
  if (x) poolAdd(x.gateOwner, x.gateStr, zone, team, s);
}

export function gatePool(ctx) {
  const x = state(ctx);
  return x ? { owner: x.gateOwner, str: x.gateStr } : null;
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
  let power = 0;
  const assets = []; // tiles of our mines and turrets (VARIANT alarm)
  const minePos = []; // tiles of our mines
  const maxObj = i32(gs, GS.MAX_OBJ);
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    if (!alive(u8(gs, a + O.LIFE)) || u8(gs, a + O.TEAM) !== p) continue;
    const type = u8(gs, a + O.TYPE);
    if (isFighter(G, type)) {
      fighters++;
      ownStr += gndOf(G, type);
      power += powerOf(type);
      const [tx, tz] = tileOf(gs, a);
      if (cheb(tx, tz, x.hq[0], x.hq[1]) <= HOME_RADIUS) ownNearHome++;
    }
    if (MINING_TOWERS.includes(type)) {
      own.mines++;
      const [tx, tz] = tileOf(gs, a);
      assets.push([tx, tz]);
      minePos.push([tx, tz]);
      const z = famAt(G, tx, tz);
      if (z) own.mineZones.push(z);
    } else if (HIDDEN_MINES.includes(type)) own.laid++;
    else if (ENGINEERS.includes(type)) own.engineers++;
    else if (type === 41 || type === 42) {
      const [tx, tz] = tileOf(gs, a);
      assets.push([tx, tz]);
      const z = famAt(G, tx, tz);
      if (z) own.turretZones.push(z);
    }
  }
  x.power = power;
  x.own = own;
  if (own.mines >= 2) x.secondDone = true; // VARIANT second: done once two mines stood
  x.ownStr = ownStr;
  x.fighters = fighters;
  x.ownNearHome = ownNearHome;
  // what Krusty knows of the enemy: its memory table (seen, and not yet seen gone)
  const enemy = { mines: [], buildings: [], hiddenMines: [], flyers: 0 };
  let enemyNearHome = 0;
  const intruders = [];
  const threats = []; // seen enemy fighters at the base or at one of our mines / turrets (defend)
  x.prevDist ??= new Map(); // enemy object -> its distance to our HQ at the last think (approaching backup)
  const nowDist = new Map();
  const seenFighters = [];
  const enemyPower = new Array(8).fill(0);
  let liveThreat = 0;
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
    if (isFighter(G, type) || COMMANDER_TYPES.includes(type)) { // an enemy commander fights too (its gun reaches 6 tiles)
      enemyPower[team] += powerOf(type);
      const d = cheb(rec.x, rec.z, x.hq[0], x.hq[1]);
      if (d <= HOME_RADIUS) enemyNearHome++;
      // a record our own vision covers right now was refreshed this think: a live position, not a ghost
      if (rec.x < G.map.w && rec.z < G.map.h && seenByPlayer(G, rec.x, rec.z, p)) {
        rec.approaching = x.prevDist.has(o) && d < x.prevDist.get(o);
        nowDist.set(o, d);
        seenFighters.push(rec);
        if (d <= RALLY_RADIUS) intruders.push(rec);
        if (d <= RALLY_RADIUS || assets.some(([ax, az]) => cheb(rec.x, rec.z, ax, az) <= DEFEND_ASSET_RADIUS)) threats.push(rec);
        if (d <= RALLY_RADIUS || assets.some(([ax, az]) => cheb(rec.x, rec.z, ax, az) <= MINE_RADIUS)) liveThreat++;
      }
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
  x.threats = threats;
  x.prevDist = nowDist;
  x.assets = assets;
  // an enemy firing at one of ours - the engine marks the shooter with its target's team (O.ATTACKED) and reveals its
  // tile to that team: a threat from its first shot within SHOOTER_RADIUS of the HQ or a mine (maintainer, 9 Oct 2026: "base and mining
  // sites must be defended immediately"; in the Hoops replay the attackers of a mine became targets only when our
  // vision reached them)
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    const team = u8(gs, a + O.TEAM);
    if (!alive(u8(gs, a + O.LIFE)) || !isEnemy(G, p, team)) continue;
    const mark = u8(gs, a + O.ATTACKED);
    if (!(mark & 0x1f) || mark >> 5 !== p) continue;
    const type = u8(gs, a + O.TYPE);
    if (!isFighter(G, type) && !COMMANDER_TYPES.includes(type)) continue; // the rusher's commander shot a Hoops mine for 500 ticks unseen
    const [tx, tz] = tileOf(gs, a);
    // at the perimeter only: a skirmish far afield is left to the units there (anywhere, it stalled the expansion)
    if (cheb(tx, tz, x.hq[0], x.hq[1]) > SHOOTER_RADIUS && !assets.some(([ax, az]) => cheb(tx, tz, ax, az) <= SHOOTER_RADIUS)) continue;
    let rec = seenFighters.find((r) => r.o === o);
    if (rec) {
      rec.x = tx;
      rec.z = tz;
    } else {
      rec = { o, x: tx, z: tz, type, team, approaching: false };
      seenFighters.push(rec);
    }
    rec.shooting = true;
    if (!threats.includes(rec)) threats.push(rec);
  }
  // the production queues: when did each last move (a unit came out) - see queueOpen
  x.queues ??= [0, 1, 2, 3].map(() => ({ len: 0, since: 0 }));
  for (let k = 0; k < 4; k++) {
    const q = x.queues[k];
    const len = gs.readUInt16LE(pa + P.QUEUE_LEN + 2 * k);
    if (len < q.len || q.len === 0) q.since = tick;
    q.len = len;
  }
  x.seenFighters = seenFighters;
  x.liveThreat = liveThreat;
  if (liveThreat > 0) {
    x.lastLiveThreat = tick;
    x.waveSeen = true; // VARIANT factory: the first wave came
  }
  x.enemyPower = Math.max(...enemyPower);
  // VARIANT safe: an explorer lost (the game's per-type loss counter) starts the cooldown of the expansion lane
  const explLost = G.typeStat(0, p, 6) + G.typeStat(0, p, 14);
  if (explLost > x.explLost) {
    x.lastExplLoss = tick;
    if (own.mines === 2) markThirdFailed(ctx, 'an explorer for the third site was lost'); // VARIANT gate
  }
  x.explLost = explLost;
  // VARIANT factory: after the first wave, save for the tanks until a mech can be bought (barracks standing);
  // VARIANT gate: the same saving once the third site failed - lifting the gate alone builds nothing, the defence's
  // troopers and the army goals spend every coin before science could
  // VARIANT tech (maintainer, 8 Oct 2026: "do science and factory right after barracks"): the saving from the barracks on
  const saveFor = (variant(ctx).factory && x.waveSeen) || (variant(ctx).gate && x.thirdFailed) || variant(ctx).tech;
  // maintainer, 9 Oct 2026 (the Hoops replay): "delay purchase of factory until perimeter of the base and perimeter of
  // the nearest to the base mine" are secured - while an enemy threatens either (x.threats within RALLY_RADIUS of the HQ
  // or DEFEND_ASSET_RADIUS of the mine nearest to it) there is no saving for the tech and no factory (skipGoal): the
  // money goes into the defence
  let nearMine = null;
  for (const m of minePos) if (!nearMine || cheb(m[0], m[1], x.hq[0], x.hq[1]) < cheb(nearMine[0], nearMine[1], x.hq[0], x.hq[1])) nearMine = m;
  x.perimeterAttacked = x.threats.some(
    (r) => cheb(r.x, r.z, x.hq[0], x.hq[1]) <= RALLY_RADIUS || (nearMine && cheb(r.x, r.z, nearMine[0], nearMine[1]) <= DEFEND_ASSET_RADIUS),
  );
  x.saving = !!saveFor && !x.perimeterAttacked && i32(gs, pa + P.SLOT_HP + 4) !== 0 && !buyableTroops(G, p).some((it) => TANKS.includes(it.type));
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
    // VARIANT alarm: only enemy fighters seen now near the base or a mine; else the doctrine's first form
    const condition = variant(ctx).alarm
      ? liveThreat > 0 || tick - x.lastLiveThreat < ALARM_MEMORY
      : tick - x.lastThreatTick < DANGER_MEMORY || enemyNearHome > 0 || enemyStr > ownStr || fighters < HOLD_MIN_FIGHTERS;
    // hysteresis: the doctrine stands down only after CALM_TICKS without a condition (no flicker)
    if (condition) x.calmSince = -1;
    else if (x.calmSince < 0) x.calmSince = tick;
    const danger = condition || (x.danger && tick - x.calmSince < CALM_TICKS);
    if (danger !== x.danger) {
      x.danger = danger;
      x.stats.holds = (x.stats.holds ?? 0) + (danger ? 1 : 0);
      if (danger) ctx.say(`Danger (enemy ${enemyStr} vs ours ${ownStr}, ${fighters} fighters, ${enemyNearHome} at the gates): the army holds at the HQ.`);
      else ctx.say('The danger is over.');
    }
    // VARIANT counter: the army stays home until our power is COUNTER_RATIO times the strongest enemy's we know of
    const ready = !variant(ctx).counter || power >= Math.max(COUNTER_MIN, Math.ceil(COUNTER_RATIO * x.enemyPower));
    const holding = danger || !ready;
    if (holding !== x.holding) {
      x.holding = holding;
      if (!holding) {
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
        ctx.say(`Counter-offensive: ${n} defenders (power ${power} against ${x.enemyPower} known) join the attack.`);
      }
    }
    w32(kai, K.SPLIT, holding ? SPLIT_HOLD : SPLIT_ORIGINAL);
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

/**
 * A unit of `type` may be bought: its production queue (the type's prodClass) holds fewer than QUEUE_FULL units and has
 * not stood still for QUEUE_STALL ticks. Maintainer, 9 Oct 2026 (the Hoops of Fury replay: "money just disappears and
 * nothing is bought!"): a building makes its next unit only when its spawn tile is free (city.js: it nudges the occupant
 * and waits) - a rusher standing at the barracks door froze the queue, the bot paid 350 a think into it (18 greys queued,
 * none came out) and lost it all with the barracks. True without the extension layer (the original's behaviour).
 */
export function queueOpen(ctx, type) {
  const x = ctx.aux?.x;
  if (!x?.queues) return true;
  const q = x.queues[ctx.G.tables.types[type].prodClass];
  if (!q) return true;
  return q.len < QUEUE_FULL && !(q.len > 0 && (ctx.tick | 0) - q.since > QUEUE_STALL);
}

/** Buy one unit of the first buyable type in `types`; false when none is buyable or affordable. */
function buy(ctx, types, n, reason) {
  const { G, p } = ctx;
  const it = buyableTroops(G, p).find((i) => types.includes(i.type) && queueOpen(ctx, i.type));
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
  if (variant(ctx).factory && x.waveSeen) return false; // VARIANT factory: the wave came, the tanks before the third mine
  if (variant(ctx).gate && x.thirdFailed) return false; // VARIANT gate: the third site failed, science (and the factory) no longer wait for it
  if (variant(ctx).tech) return false; // VARIANT tech: science and the factory right after the barracks, no third site first
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
  if (kind === 1 && param === 0 && secondFirst(ctx)) return true; // VARIANT second: the barracks after the second explorer
  // no robot factory (building kinds 1 and 2: its levels) while the perimeter of the base or of the nearest mine is attacked
  if (kind === 1 && (param === 1 || param === 2) && state(ctx)?.perimeterAttacked) return true;
  // VARIANT factory: while saving for science and the robot factory no army goal spends (hold buys in an emergency)
  if (state(ctx)?.saving && kind === 2) return true;
  // VARIANT infup: while an infantry upgrade is due the chain builds only HQ, barracks and science, and no army goal spends
  if ((kind === 1 || kind === 2) && infupDue(ctx)) return kind === 2 || (param !== 8 && param !== 0 && param !== 3);
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
  if (escortOn(ctx) && escortBuy(ctx)) return false; // VARIANT escort: its reinforcements first, the expedition is under fire
  // VARIANT escort: up to the third mining site its troopers come before hold's, unless the HQ stands unguarded
  const unguarded = x.fighters < HOLD_BUY_MIN || (x.ownNearHome === 0 && x.intruders.length > 0);
  if (escortOn(ctx) && x.own.mines < ESCORT_SITES && !unguarded && escortRecruit(ctx)) return false;
  // VARIANT upnow (maintainer, 8 Oct 2026: "won't reaper upgrade help?"): the upgrades of battle experience (weapon: the
  // type with most kills, armour: most losses) bought before hold's troopers and the chain, without a reserve, once a mech
  // can be bought - only an unguarded HQ comes first (under a rush hold's purchases would otherwise take every coin)
  if (v.upnow && !unguarded && (buyUpgrade(ctx) || upgradeDue(ctx))) return false; // bought, or the money waits for it
  // VARIANT infup (maintainer, 9 Oct 2026: "trooper upgrades right after the hard rush is faced"): once science stands
  // and the first wave came, the infantry's level-1 upgrades before hold's troopers, the factory and the army goals
  if (infupDue(ctx) && !unguarded) {
    buyInfup(ctx);
    return false; // bought, or the money waits for it
  }
  // the hold doctrine buys a fighter when intruders at the gates outnumber ours there, or, with nobody at
  // the gates, while fewer than HOLD_MIN_FIGHTERS stand; tanks first once the factory and science exist
  let holdBuys = v.hold && x.danger && x.fighters < (opening(ctx) ? HOLD_OPENING_MAX : 1000) && (x.enemyNearHome > 0 ? x.ownNearHome < x.enemyNearHome + HOLD_MARGIN : x.fighters < HOLD_BUY_MIN);
  // VARIANT factory: while saving for the tanks only an emergency buys - too few fighters, or outnumbered at the gates
  const emergency = x.fighters < HOLD_BUY_MIN || x.ownNearHome < x.enemyNearHome;
  if (x.saving && !emergency) holdBuys = false;
  // with hold on, hold rules the purchases (react's "a fighter per think while a threat is near" starved the mines in the arena)
  if (holdBuys || (v.react && !v.hold && x.threatZone >= 0)) {
    // one unit at a time as soon as the money is there (maintainer, 9 Oct 2026: "if you want to build several units, build
    // one by one as soon as money is available" - VARIANT batch no longer makes the money wait for three)
    if (buy(ctx, TANKS, 1, 'Defence') || buy(ctx, INFANTRY, 1, 'Defence')) x.stats.reacts++;
    return false;
  }
  if (x.saving) return false; // VARIANT factory: nothing else until the tanks can be bought
  if (escortOn(ctx) && !x.danger && escortRecruit(ctx)) return false; // VARIANT escort: the troopers of a new expedition
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
  if (v.lieutenant && COMMANDER_TYPES.includes(type)) return ensureSlot(ctx, COMMANDER_SLOT, KIND_COMMANDER); // VARIANT lieutenant
  // VARIANT escort: the recruits and reinforcements bought for the expedition
  const e = x.esc;
  // VARIANT second: the starting trooper (the first free infantry of the first census) does not go scouting, it waits
  // at the HQ in the escort slot for the second explorer
  if (v.second && !x.secondDone && cls === 0 && e.phase === 'idle' && !slotUnits(ctx, ESCORT_SLOT).length) return ensureSlot(ctx, ESCORT_SLOT, KIND_ESCORT);
  if (escortOn(ctx) && e.phase !== 'idle' && ((cls === 2 && e.pendTank > 0) || (cls === 0 && e.pendInf > 0))) {
    if (cls === 2) e.pendTank--;
    else e.pendInf--;
    return ensureSlot(ctx, ESCORT_SLOT, KIND_ESCORT);
  }
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
  const hunting = defend(ctx);
  engageIdle(ctx);
  turrets(ctx);
  if (v.hold && x.danger) rally(ctx, hunting);
  else if (hunting) {
    // the defence has every armed unit near the threats
  } else if (v.react) reactMoves(ctx);
  else if (v.patrol) patrolHome(ctx);
  if (escortOn(ctx)) escort(ctx);
  if (v.lieutenant) lieutenant(ctx);
  clearDoors(ctx);
}

/** VARIANT noscout: no unit to the scouting task and no scout bought before the robot factory (slot 2) stands. */
export function noScouting(ctx) {
  return !!state(ctx) && !!variant(ctx).noscout && i32(ctx.G.gs, playerAddr(ctx.p) + P.SLOT_HP + 4 * 2) === 0;
}

/**
 * VARIANT lieutenant (maintainer, 8 Oct 2026: "keep the lieutenant alive and behind the own troops; apply the star
 * command when the enemy attacks"). The commander (types 69..76, the lieutenant of the default rank) lives in slot
 * COMMANDER_SLOT, out of the original groups. It follows our largest group (maintainer, 8 Oct 2026: the fighter with the
 * most of ours within CLUSTER_RADIUS tiles, and those around it) COMMANDER_BEHIND tiles behind its centre - away from
 * the enemy fighters seen within COMMANDER_ENGAGE tiles of it, else on the side of home; with no fighter at all it waits
 * at the HQ (its gun reaches 6 tiles). The star command is its
 * rally (deploy order 0x0D, DC16_BATTLE_ENGINE.md §10.2: up to 6 / 8 / 10 / 12 armed units within 10 tiles deal 130..160 %
 * for 320..560 ticks; needs charge >= 32, which grows 1 per 32 ticks): given when enemy fighters stand within
 * STAR_REACH tiles of our fighters and at least STAR_MIN of ours are within STAR_REACH of the commander.
 */
function lieutenant(ctx) {
  const x = state(ctx);
  const { G, p } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  const units = slotUnits(ctx, COMMANDER_SLOT);
  if (!units.length) return;
  const o = units[0];
  const a = objAddr(o);
  const [lx, lz] = tileOf(gs, a);
  const [hx, hz] = x.hq;
  const friends = [];
  // maintainer, 9 Oct 2026: "commander is sent with exploiter" - the escort of an expedition is no group to follow; with
  // nobody else it waits at the HQ
  const skip = x.special[ESCORT_SLOT] ? new Set(slotUnits(ctx, ESCORT_SLOT)) : null;
  const maxObj = i32(gs, GS.MAX_OBJ);
  for (let f = 120; f <= maxObj; f++) {
    const fa = objAddr(f);
    if (f === o || u8(gs, fa + O.TEAM) !== p || !alive(u8(gs, fa + O.LIFE)) || !isFighter(G, u8(gs, fa + O.TYPE))) continue;
    if (skip?.has(f)) continue;
    const [fx, fz] = tileOf(gs, fa);
    friends.push([fx, fz]);
  }
  const last = x.lastOrder.get(o) ?? -1000000;
  // maintainer, 9 Oct 2026: "lieutenant must be smart enough. it must fight unless he is under fire, then he must retreat
  // few tiles back and retry offence again" - a unit under a move order does not shoot, and the commander got one every 64
  // ticks (in the replay it fired in 65 of 1351 ticks beside the enemy). It moves by assault-moves and attacks the enemy
  // nearest to it; hit (its hit points fell since the last think) it walks LT_RETREAT tiles straight away from the
  // enemies around it (a plain move, at once), waits LT_RETREAT_HOLD ticks, then attacks again
  const hp = i32(gs, a + O.HP);
  const hit = hp < (x.ltHp ?? hp);
  x.ltHp = hp;
  if (hit) {
    ltRetreat(ctx, o, lx, lz);
    return;
  }
  if (tick < (x.ltRetreatUntil ?? 0)) return;
  const go = (tx, tz) => {
    if (cheb(lx, lz, tx, tz) <= 2 || tick - last < 64) return;
    sendWaypointOrder(ctx, [o], [[(tx << 8) + 128, (tz << 8) + 128]], ORDER_ASSAULT);
    x.lastOrder.set(o, tick);
  };
  if (!friends.length) {
    go(hx, hz);
    return;
  }
  // the largest group: the fighter with the most of ours within CLUSTER_RADIUS tiles, and those around it
  let core = friends[0];
  let coreN = -1;
  for (const f of friends) {
    let n = 0;
    for (const g of friends) if (cheb(f[0], f[1], g[0], g[1]) <= CLUSTER_RADIUS) n++;
    if (n > coreN) {
      coreN = n;
      core = f;
    }
  }
  const group = friends.filter(([fx, fz]) => cheb(fx, fz, core[0], core[1]) <= CLUSTER_RADIUS);
  let gx = 0;
  let gz = 0;
  for (const [ux, uz] of group) {
    gx += ux;
    gz += uz;
  }
  gx /= group.length;
  gz /= group.length;
  const enemies = x.seenFighters.filter((r) => cheb(r.x, r.z, gx, gz) <= COMMANDER_ENGAGE);
  let dx;
  let dz;
  if (enemies.length) {
    // the star command: enemies at the group, enough of ours within the rally's reach, the charge there
    const inReach = friends.filter(([fx, fz]) => cheb(fx, fz, lx, lz) <= STAR_REACH).length;
    const engaged = group.some(([fx, fz]) => enemies.some((r) => cheb(r.x, r.z, fx, fz) <= STAR_REACH));
    if (engaged && inReach >= STAR_MIN && u8(gs, a + O.CHARGE) >= 0x20) {
      sendOrder(ctx, o, ORDER_DEPLOY);
      x.lastOrder.set(o, tick);
      x.stats.stars = (x.stats.stars ?? 0) + 1;
      ctx.say(`Star command at ${lx},${lz}: ${Math.min(inReach, 6)} units rallied.`);
      return;
    }
    {
      // the offence: an assault-move onto the enemy fighter nearest to it
      let t = enemies[0];
      for (const r of enemies) if (cheb(r.x, r.z, lx, lz) < cheb(t.x, t.z, lx, lz)) t = r;
      if (tick - last >= 64 || isIdle(gs, a)) {
        sendWaypointOrder(ctx, [o], [[(t.x << 8) + 128, (t.z << 8) + 128]], ORDER_ASSAULT);
        x.lastOrder.set(o, tick);
      }
      return;
    }
    let ex = 0;
    let ez = 0;
    for (const r of enemies) {
      ex += r.x;
      ez += r.z;
    }
    dx = gx - ex / enemies.length; // behind = away from the enemy
    dz = gz - ez / enemies.length;
  } else {
    dx = hx - gx; // no enemy near the group: trailing it on the side of home
    dz = hz - gz;
  }
  const n = Math.hypot(dx, dz);
  const back = n < 1 ? 0 : COMMANDER_BEHIND / n;
  const tx = Math.min(G.map.w - 1, Math.max(0, Math.round(gx + dx * back)));
  const tz = Math.min(G.map.h - 1, Math.max(0, Math.round(gz + dz * back)));
  go(tx, tz);
}

/**
 * The commander under fire: LT_RETREAT tiles away from the centre of the enemy fighters seen within COMMANDER_ENGAGE
 * tiles of the commander (none seen: towards the HQ), a plain move given at once; no new order for LT_RETREAT_HOLD ticks.
 */
function ltRetreat(ctx, o, lx, lz) {
  const x = state(ctx);
  const { G } = ctx;
  const tick = ctx.tick | 0;
  const near = x.seenFighters.filter((r) => cheb(r.x, r.z, lx, lz) <= COMMANDER_ENGAGE);
  let dx;
  let dz;
  if (near.length) {
    dx = lx - near.reduce((s, r) => s + r.x, 0) / near.length;
    dz = lz - near.reduce((s, r) => s + r.z, 0) / near.length;
  } else {
    dx = x.hq[0] - lx;
    dz = x.hq[1] - lz;
  }
  const n = Math.hypot(dx, dz);
  x.ltRetreatUntil = tick + LT_RETREAT_HOLD;
  x.stats.ltRetreats = (x.stats.ltRetreats ?? 0) + 1;
  if (n < 1) return;
  const tx = Math.min(G.map.w - 1, Math.max(0, Math.round(lx + (dx / n) * LT_RETREAT)));
  const tz = Math.min(G.map.h - 1, Math.max(0, Math.round(lz + (dz / n) * LT_RETREAT)));
  sendWaypointOrder(ctx, [o], [[(tx << 8) + 128, (tz << 8) + 128]], ORDER_MOVE);
  x.lastOrder.set(o, tick);
}

/**
 * The defence (maintainer, 9 Oct 2026: "when base or mine is attacked, troops must fight accordingly. there must be no
 * units which doesn't fight. Look at rusher logic"): while enemy fighters we see stand within RALLY_RADIUS tiles of the
 * HQ or DEFEND_ASSET_RADIUS of one of our mines or turrets (x.threats), every armed unit of ours within DEFEND_REACH tiles
 * of one of them - whatever its group: defenders, attack groups, scouts, the trooper the second site keeps at the HQ -
 * assault-moves onto the threat nearest to it, every DEFEND_EVERY ticks, an idle one at once (an idle unit of a human
 * seat fires only at what is in its weapon range and gives chase within 4 tiles: in the 9 Oct replay three of them slept
 * beside the rushers). Left out: the commander (lieutenant: it fights and steps back by itself) and an expedition's
 * escort on its way (it fights what it meets). The units are marked as ordered to their group's zone so that move_group
 * leaves them alone. True while threats stand.
 */
function defend(ctx) {
  const x = state(ctx);
  const { G, kai } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  // maintainer, 9 Oct 2026: "if enemy is attacking, we must push it until there are no single enemy in that attack",
  // "continue fighting with intruders even when base is safe while opponent have backup which is approaching". Once an
  // attack came (x.threats), the defence goes on against every enemy fighter we see that belongs to it - within
  // PUSH_LINK tiles of one hunted last think, or within IN_FIGHT of one of our fighters - and against the backup: enemy
  // fighters coming closer to our HQ within BACKUP_RADIUS. It ends when none of them is seen.
  // On Circle of Friends (9 Oct 2026: "we don't go on the offensive, though we must keep pressing the enemy while it is
  // nearby") one enemy stood by the base nearly all game, and while any did only those were hunted - the ones fighting
  // our units 20 tiles out were left alone. While an attack is on, the targets are the UNION: the threats, every seen
  // enemy fighter within PUSH_LINK of a threat or of last think's targets, within IN_FIGHT of one of our fighters,
  // approaching within BACKUP_RADIUS, or nearby (NEAR_RADIUS of the HQ, NEAR_ASSET_RADIUS of a mine or turret).
  let threats = x.threats ?? [];
  if (threats.length || x.pushFront?.length) {
    const ours = [];
    const maxO = i32(gs, GS.MAX_OBJ);
    for (let o = 120; o <= maxO; o++) {
      const a = objAddr(o);
      if (u8(gs, a + O.TEAM) === ctx.p && alive(u8(gs, a + O.LIFE)) && isFighter(G, u8(gs, a + O.TYPE))) ours.push(tileOf(gs, a));
    }
    const front = [...(x.pushFront ?? []), ...threats.map((r) => [r.x, r.z])];
    const assets = x.assets ?? [];
    threats = (x.seenFighters ?? []).filter(
      (r) =>
        threats.includes(r) ||
        front.some(([fx, fz]) => cheb(r.x, r.z, fx, fz) <= PUSH_LINK) ||
        ours.some(([ux, uz]) => cheb(r.x, r.z, ux, uz) <= IN_FIGHT) ||
        (r.approaching && cheb(r.x, r.z, x.hq[0], x.hq[1]) <= BACKUP_RADIUS) ||
        cheb(r.x, r.z, x.hq[0], x.hq[1]) <= NEAR_RADIUS ||
        assets.some(([ax, az]) => cheb(r.x, r.z, ax, az) <= NEAR_ASSET_RADIUS),
    );
  }
  x.pushFront = threats.map((r) => [r.x, r.z]);
  x.pushing = threats.length > 0 && !x.threats?.length;
  if (!threats.length) return false;
  const round = tick - (x.lastDefend ?? -1000000) >= DEFEND_EVERY;
  if (round) x.lastDefend = tick;
  const expedition = x.esc.phase !== 'idle' && x.esc.phase !== 'gather';
  const byTarget = new Map();
  for (const t of [1, 2, 3]) {
    for (let m = 0; m < NMINORS; m++) {
      if (!u8(kai, minorAddr(t, m) + MN.ACTIVE)) continue;
      if (t === 2 && (m === COMMANDER_SLOT || (m === ESCORT_SLOT && expedition))) continue;
      const zone = i32(kai, minorAddr(t, m) + MN.ZONE) & 0xff;
      for (const o of listOf(gs, kai, t, m)) {
        const a = objAddr(o);
        if (!alive(u8(gs, a + O.LIFE))) continue;
        const type = u8(gs, a + O.TYPE);
        if (!isFighter(G, type) || TOWER_BUILDERS.includes(type) || COMMANDER_TYPES.includes(type)) continue;
        const [ux, uz] = tileOf(gs, a);
        let best = null;
        let bestD = 1e9;
        for (const r of threats) {
          const d = cheb(ux, uz, r.x, r.z);
          if (d < bestD) {
            bestD = d;
            best = r;
          }
        }
        // maintainer, 9 Oct 2026: "all these units (trooper, tank, artillery, sarge, turret) must take offensive actions
        // while opponent is attacking" - no distance limit; a unit already fighting another enemy beside it keeps that fight
        if (!(round || idleNotFiring(gs, a))) continue;
        if (bestD > IN_FIGHT && (x.seenFighters ?? []).some((r) => cheb(r.x, r.z, ux, uz) <= IN_FIGHT)) continue;
        w8(gs, a + OA.ZONE, zone);
        const key = `${best.x},${best.z}`;
        if (!byTarget.has(key)) byTarget.set(key, { x: best.x, z: best.z, objs: [] });
        byTarget.get(key).objs.push(o);
      }
    }
  }
  for (const g of byTarget.values()) sendWaypointOrder(ctx, g.objs, [[(g.x << 8) + 128, (g.z << 8) + 128]], ORDER_ASSAULT);
  if (byTarget.size) x.stats.defends = (x.stats.defends ?? 0) + 1;
  return true;
}

/**
 * Only the lieutenant retreats (maintainer, 9 Oct 2026: "retreat must be only for lieutenant!"): every order the bot
 * gives (krusty.js sendWaypointOrder: the original's group moves, the rally, the escort, the defence) passes here. An
 * armed unit in contact - an enemy fighter we see within CONTACT tiles - is taken out of an order whose destination
 * lies more than RETREAT_SLACK tiles further from that enemy than the unit stands: it stays and fights. Left alone: the
 * commander (it steps back by itself), unarmed units (explorers, turret builders before they land) and the doorkeeper's
 * step off a spawn tile. Returns the objects that may go; the original's behaviour without the extension layer.
 */
export function holdGround(ctx, objs, dest) {
  const x = ctx.aux?.x;
  if (!x || x.doorStep || !x.seenFighters?.length) return objs;
  const { G } = ctx;
  const gs = G.gs;
  const dx = dest[0] >> 8;
  const dz = dest[1] >> 8;
  const out = [];
  for (const o of objs) {
    const a = objAddr(o);
    const type = u8(gs, a + O.TYPE);
    if (!isFighter(G, type) || COMMANDER_TYPES.includes(type) || TOWER_BUILDERS.includes(type)) {
      out.push(o);
      continue;
    }
    const [ux, uz] = tileOf(gs, a);
    let near = null;
    let nd = CONTACT + 1;
    for (const r of x.seenFighters) {
      const d = cheb(ux, uz, r.x, r.z);
      if (d < nd) {
        nd = d;
        near = r;
      }
    }
    if (near && cheb(dx, dz, near.x, near.z) > nd + RETREAT_SLACK) {
      x.stats.heldGround = (x.stats.heldGround ?? 0) + 1;
      continue;
    }
    out.push(o);
  }
  return out;
}

/**
 * The turret builders (TURR / XENO, unarmed until deployed; the turret's gun reaches 6 tiles, a trooper's 4) - maintainer,
 * 9 Oct 2026: "turrets must not patrol the base. either they go to destination, either (in case of nearby enemy attack)
 * they land as close to the enemy as they can", "as close to the enemy to not get killed before deployment, of course".
 * move_group no longer moves them (krusty.js) and the rally does not either. Each builder: with an enemy fighter within
 * TURRET_ENGAGE tiles of it (or a threat to the base within DEFEND_REACH) it walks towards the nearest one and lands
 * TURRET_MARGIN tiles beyond the weapon range of that enemy's group - at once when hit; with none it walks to its
 * destination (its group's zone centre when it first shows up, else one of eight spots TURRET_SPOT tiles around the HQ)
 * and lands there. A landing that does not happen (blocked cell) is repeated, a tile nearer home after three tries.
 */
function turrets(ctx) {
  const x = state(ctx);
  const { G, p, kai } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  x.turret ??= new Map();
  const seen = x.seenFighters ?? [];
  const threats = x.threats ?? [];
  const rangeOf = (r) => {
    const w = G.tables.types[r.type]?.weapon?.[0] ?? -1;
    return w >= 0 ? G.tables.weapons[w].range : 0;
  };
  const dirs = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];
  const clampX = (v) => Math.min(G.map.w - 1, Math.max(0, v));
  const clampZ = (v) => Math.min(G.map.h - 1, Math.max(0, v));
  const maxObj = i32(gs, GS.MAX_OBJ);
  const present = new Set();
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    if (u8(gs, a + O.TEAM) !== p || !alive(u8(gs, a + O.LIFE)) || !TOWER_BUILDERS.includes(u8(gs, a + O.TYPE))) continue;
    present.add(o);
    const [ux, uz] = tileOf(gs, a);
    let t = x.turret.get(o);
    if (!t) {
      const zone = u8(gs, a + OA.ZONE);
      let dest = zone ? centreOf(kai, zone) : null;
      if (!dest || (dest[0] === 0 && dest[1] === 0)) {
        const k = (x.stats.turretSpots = (x.stats.turretSpots ?? 0) + 1) % 8;
        dest = [x.hq[0] + dirs[k][0] * TURRET_SPOT, x.hq[1] + dirs[k][1] * TURRET_SPOT];
      }
      t = { dest: [clampX(dest[0]), clampZ(dest[1])], hp: i32(gs, a + O.HP), last: -1000000, lands: 0, landAt: null };
      x.turret.set(o, t);
    }
    const hp = i32(gs, a + O.HP);
    const hit = hp < t.hp;
    t.hp = hp;
    let foe = null;
    let fd = 1e9;
    for (const r of seen) {
      const d = cheb(ux, uz, r.x, r.z);
      if (d < fd && (d <= TURRET_ENGAGE || (threats.includes(r) && d <= DEFEND_REACH))) {
        fd = d;
        foe = r;
      }
    }
    const here = t.landAt && t.landAt[0] === ux && t.landAt[1] === uz;
    const land = () => {
      if (here && tick - t.last < 64) return;
      t.lands = here ? t.lands + 1 : 1;
      if (t.lands > 3) {
        // the cell does not take a turret: one tile nearer home, then again
        const sx = Math.sign(x.hq[0] - ux) || 1;
        const sz = Math.sign(x.hq[1] - uz) || 1;
        sendWaypointOrder(ctx, [o], [[(clampX(ux + sx) << 8) + 128, (clampZ(uz + sz) << 8) + 128]], ORDER_MOVE);
        t.lands = 0;
        t.landAt = null;
        t.last = tick;
        return;
      }
      sendOrder(ctx, o, ORDER_DEPLOY);
      t.landAt = [ux, uz];
      t.last = tick;
      if (t.lands === 1) {
        x.stats.turrets = (x.stats.turrets ?? 0) + 1;
        ctx.say(foe ? `Turret lands at ${ux},${uz} against the enemy at ${foe.x},${foe.z}.` : `Turret lands at ${ux},${uz}.`);
      }
    };
    if (foe) {
      // the enemy's reach: the longest weapon among the enemy fighters around the nearest one
      let reach = 0;
      for (const r of seen) if (cheb(r.x, r.z, foe.x, foe.z) <= 4) reach = Math.max(reach, rangeOf(r));
      const stop = reach + TURRET_MARGIN;
      if (hit || fd <= stop) {
        land();
        continue;
      }
      if (isIdle(gs, a) || tick - t.last >= 64) {
        const n = Math.max(1, fd);
        const tx = clampX(Math.round(foe.x + ((ux - foe.x) / n) * stop));
        const tz = clampZ(Math.round(foe.z + ((uz - foe.z) / n) * stop));
        sendWaypointOrder(ctx, [o], [[(tx << 8) + 128, (tz << 8) + 128]], ORDER_MOVE);
        t.last = tick;
        t.landAt = null;
      }
      continue;
    }
    const dd = cheb(ux, uz, t.dest[0], t.dest[1]);
    if (dd <= 1 || (isIdle(gs, a) && dd <= 3)) {
      land();
      continue;
    }
    if (isIdle(gs, a) || tick - t.last >= REISSUE) {
      sendWaypointOrder(ctx, [o], [[(t.dest[0] << 8) + 128, (t.dest[1] << 8) + 128]], ORDER_MOVE);
      t.last = tick;
      t.landAt = null;
    }
  }
  for (const o of [...x.turret.keys()]) if (!present.has(o)) x.turret.delete(o);
}

/** krusty.js move_group: once the extension layer runs, turrets() moves and lands the turret builders. */
export const ownsTurrets = (ctx) => !!ctx.aux?.x;

/**
 * Where the rally gathers with no intruder to hunt: GATHER_FRONT tiles from the HQ towards the nearest enemy fighter we
 * see (else the threat zone, else the map's centre) - beside the HQ, not on it (maintainer, 9 Oct 2026: "own unit
 * blocked barracks!": sent to the HQ tile every 128 ticks, the defenders crowded round the building and one stood on the
 * barracks' spawn tile for 2200 ticks while three paid-for greys waited). Raw coordinates for sendWaypointOrder.
 */
function gatherPoint(ctx) {
  const x = state(ctx);
  const { G, kai } = ctx;
  const [hx, hz] = x.hq;
  let to = null;
  let bd = 1e9;
  for (const r of x.seenFighters ?? []) {
    const d = cheb(r.x, r.z, hx, hz);
    if (d < bd) {
      bd = d;
      to = [r.x, r.z];
    }
  }
  if (!to && x.threatZone >= 0) to = centreOf(kai, x.threatZone);
  if (!to || (to[0] === hx && to[1] === hz)) to = [G.map.w >> 1, G.map.h >> 1];
  const dx = to[0] - hx;
  const dz = to[1] - hz;
  const n = Math.hypot(dx, dz) || 1;
  const gx = Math.min(G.map.w - 1, Math.max(0, Math.round(hx + (dx / n) * GATHER_FRONT)));
  const gz = Math.min(G.map.h - 1, Math.max(0, Math.round(hz + (dz / n) * GATHER_FRONT)));
  return [(gx << 8) + 128, (gz << 8) + 128];
}

/**
 * Our own units off the buildings' doors: a building makes its next unit only when its spawn tile is free, and a unit
 * under an order does not step aside when the building nudges it. Every think, for each production queue with units
 * waiting, a mobile unit of ours standing on that building's ground spawn tile is sent DOOR_STEP tiles further out
 * (an assault-move: it keeps fighting).
 */
function clearDoors(ctx) {
  const x = state(ctx);
  const { G, p } = ctx;
  const gs = G.gs;
  const pa = playerAddr(p);
  const off = G.consts.spawnOffsets.values;
  const [hx, hz] = x.hq;
  for (let k = 0; k < 4; k++) {
    if (!gs.readUInt16LE(pa + P.QUEUE_LEN + 2 * k)) continue;
    const sx = hx + off[k * 3 * 2];
    const sz = hz + off[k * 3 * 2 + 1];
    if (sx < 0 || sz < 0 || sx >= G.map.w || sz >= G.map.h) continue;
    const o = G.map.ground[sz * G.map.w + sx] & 0x3ff;
    if (o < 120 || o >= MAX_OBJECTS) continue;
    const a = objAddr(o);
    if (u8(gs, a + O.TEAM) !== p || !alive(u8(gs, a + O.LIFE)) || !G.tables.types[u8(gs, a + O.TYPE)]?.speed) continue;
    const ox = Math.sign(sx - hx);
    const oz = Math.sign(sz - hz);
    const tx = Math.min(G.map.w - 1, Math.max(0, sx + (ox || 1) * DOOR_STEP));
    const tz = Math.min(G.map.h - 1, Math.max(0, sz + (oz || 1) * DOOR_STEP));
    x.doorStep = true; // holdGround lets this step through
    sendWaypointOrder(ctx, [o], [[(tx << 8) + 128, (tz << 8) + 128]], ORDER_MOVE);
    x.doorStep = false;
    x.stats.doors = (x.stats.doors ?? 0) + 1;
  }
}

/**
 * No unit stands idle beside the enemy (maintainer, 9 Oct 2026: "there must be no units which doesn't fight"): an idle
 * armed unit of ours (the commander and turret builders aside) with an enemy fighter we see within IDLE_ENGAGE tiles
 * assault-moves onto the nearest one - out in the field too, where the defence does not reach (an idle unit of a human
 * seat fires only at what is in its weapon range and sleeps up to 45 ticks at a time).
 */
function engageIdle(ctx) {
  const x = state(ctx);
  const { G, p } = ctx;
  const gs = G.gs;
  const foes = x.seenFighters ?? [];
  if (!foes.length) return;
  const maxObj = i32(gs, GS.MAX_OBJ);
  let n = 0;
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    if (u8(gs, a + O.TEAM) !== p || !alive(u8(gs, a + O.LIFE)) || !idleNotFiring(gs, a)) continue;
    const type = u8(gs, a + O.TYPE);
    if (!isFighter(G, type) || TOWER_BUILDERS.includes(type) || COMMANDER_TYPES.includes(type)) continue;
    const [ux, uz] = tileOf(gs, a);
    let best = null;
    let bestD = IDLE_ENGAGE + 1;
    for (const r of foes) {
      const d = cheb(ux, uz, r.x, r.z);
      if (d < bestD) {
        bestD = d;
        best = r;
      }
    }
    if (!best) continue;
    sendWaypointOrder(ctx, [o], [[(best.x << 8) + 128, (best.z << 8) + 128]], ORDER_ASSAULT);
    n++;
  }
  if (n) x.stats.engaged = (x.stats.engaged ?? 0) + n;
}

/**
 * Under danger every defender (all defend groups) and every parked attack group hunts the intruders:
 * each fighter assault-moves onto the nearest known enemy fighter within RALLY_RADIUS of the HQ (one
 * order per intruder, re-issued every RALLY_EVERY ticks, like the rusher's own defence), or, with no
 * intruder known, gathers at the HQ tile. The units are marked as ordered to their group's zone so that
 * move_group leaves them alone, and the home guard's destination is pinned to the home zone.
 */
function rally(ctx, hunting = false) {
  const x = state(ctx);
  const { G, kai } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  const guard = minorAddr(1, 0);
  if (u8(kai, guard + MN.ACTIVE) && (i32(kai, guard + MN.DEST) & 0xff) !== x.home) setRoute(ctx, 1, 0, x.home, null);
  if (tick - x.lastRally < RALLY_EVERY) return;
  x.lastRally = tick;
  const objs = [];
  const collect = (t, m) => {
    const zone = i32(kai, minorAddr(t, m) + MN.ZONE) & 0xff;
    for (const o of listOf(gs, kai, t, m)) {
      const a = objAddr(o);
      if (!alive(u8(gs, a + O.LIFE))) continue;
      const type = u8(gs, a + O.TYPE);
      if (TOWER_BUILDERS.includes(type)) continue; // turrets() moves and lands them
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
  if (!objs.length || hunting) return; // the defence (defend) orders the fighters while threats stand
  x.stats.rallies = (x.stats.rallies ?? 0) + 1;
  // VARIANT choke: with one narrow exit the defenders hold its inner mouth; only intruders inside it are hunted
  if (variant(ctx).choke && holdChoke(ctx, objs)) return;
  const hqPoint = gatherPoint(ctx);
  if (!x.intruders.length) {
    if (variant(ctx).patrol) walkRing(ctx, objs); // VARIANT patrol: around the base instead of on the HQ tile
    else sendWaypointOrder(ctx, objs, [hqPoint], ORDER_ASSAULT);
    return;
  }
  if (variant(ctx).patrol) {
    interpose(ctx, objs); // VARIANT patrol: the defenders take the shots meant for the buildings (before batch's group rule)
    return;
  }
  if (variant(ctx).batch) {
    // VARIANT batch: one target, the intruder nearest to the HQ; the units in the fight stay in it, the ones at
    // the HQ join it as a group of BATCH_SIZE (at once when it is at the HQ), everybody else gathers at the HQ
    let target = x.intruders[0];
    for (const r of x.intruders) if (cheb(r.x, r.z, x.hq[0], x.hq[1]) < cheb(target.x, target.z, x.hq[0], x.hq[1])) target = r;
    const fighting = [];
    const atHome = [];
    const away = [];
    for (const o of objs) {
      const [ux, uz] = tileOf(gs, objAddr(o));
      if (cheb(ux, uz, target.x, target.z) <= FIGHT_RADIUS) fighting.push(o);
      else if (cheb(ux, uz, x.hq[0], x.hq[1]) <= GATHER_RADIUS) atHome.push(o);
      else away.push(o);
    }
    const hqHit = cheb(target.x, target.z, x.hq[0], x.hq[1]) <= HQ_THREAT_RADIUS;
    const hunters = fighting.length + atHome.length >= BATCH_SIZE || hqHit ? [...fighting, ...atHome] : fighting;
    const waiting = hunters === fighting ? [...atHome, ...away] : away;
    if (hunters.length) sendWaypointOrder(ctx, hunters, [[(target.x << 8) + 128, (target.z << 8) + 128]], ORDER_ASSAULT);
    if (waiting.length) sendWaypointOrder(ctx, waiting, [hqPoint], ORDER_ASSAULT);
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

// ---- the base exit (VARIANT choke) -------------------------------------------------------------------

/**
 * VARIANT choke (maintainer, 9 Oct 2026: "blocking tight passages where they exist, so the opponent is limited in
 * firepower on our troops"): the base's exit, found once per game. A walk (8 directions, walkable cells) from the ring
 * CHOKE_RING tiles around the HQ; a cell LEADS OUT when the walk goes on from it to CHOKE_FAR steps. W(d) = the cells
 * at step d that lead out: the width of the way out at that distance (an open base grows by about eight per step, a
 * ramp or a corridor stays narrow). The exit is the d in CHOKE_FROM..CHOKE_TO with the smallest W, held only when W is
 * at most CHOKE_MAX; its cells fall into GAPS (8-connected pieces: Armageddon has bases with one passage seven wide and
 * bases with two or three gaps of one to four cells), each with its own defenders' spots. Of the 56 arena seats only
 * five on Armageddon qualify. Returns null or { d, width, dist, gaps: [{ cells, cx, cz, spots }], held }.
 */
function chokeOf(ctx) {
  const x = state(ctx);
  if (x.choke !== undefined) return x.choke;
  x.choke = null;
  const { G } = ctx;
  const W = G.map.w;
  const H = G.map.h;
  const ok = (cx, cz) => cx >= 0 && cz >= 0 && cx < W && cz < H && Grid.isWalkable(G, cx, cz);
  const [hx, hz] = x.hq;
  const dist = new Int16Array(W * H).fill(-1);
  let front = [];
  for (let dz = -CHOKE_RING; dz <= CHOKE_RING; dz++) {
    for (let dx = -CHOKE_RING; dx <= CHOKE_RING; dx++) {
      if (Math.max(Math.abs(dx), Math.abs(dz)) !== CHOKE_RING || !ok(hx + dx, hz + dz)) continue;
      dist[(hz + dz) * W + hx + dx] = 0;
      front.push([hx + dx, hz + dz]);
    }
  }
  const order = [];
  while (front.length) {
    const next = [];
    for (const [cx, cz] of front) {
      order.push([cx, cz]);
      const d = dist[cz * W + cx];
      if (d >= CHOKE_FAR) continue;
      for (let k = 0; k < 9; k++) {
        const nx = cx + (k % 3) - 1;
        const nz = cz + Math.floor(k / 3) - 1;
        if (k !== 4 && ok(nx, nz) && dist[nz * W + nx] < 0) {
          dist[nz * W + nx] = d + 1;
          next.push([nx, nz]);
        }
      }
    }
    front = next;
  }
  const out = new Uint8Array(W * H);
  for (let i = order.length - 1; i >= 0; i--) {
    const [cx, cz] = order[i];
    const d = dist[cz * W + cx];
    if (d >= CHOKE_FAR) {
      out[cz * W + cx] = 1;
      continue;
    }
    for (let k = 0; k < 9 && !out[cz * W + cx]; k++) {
      const nx = cx + (k % 3) - 1;
      const nz = cz + Math.floor(k / 3) - 1;
      if (ok(nx, nz) && dist[nz * W + nx] === d + 1 && out[nz * W + nx]) out[cz * W + cx] = 1;
    }
  }
  const width = new Array(CHOKE_TO + 1).fill(0);
  const rows = Array.from({ length: CHOKE_TO + 1 }, () => []);
  for (const [cx, cz] of order) {
    const d = dist[cz * W + cx];
    if (d <= CHOKE_TO && out[cz * W + cx]) {
      width[d]++;
      rows[d].push([cx, cz]);
    }
  }
  let best = CHOKE_FROM;
  for (let d = CHOKE_FROM; d <= CHOKE_TO; d++) if (width[d] < width[best]) best = d;
  const cells = rows[best];
  if (!cells.length || cells.length > CHOKE_MAX) return null;
  // the gaps: 8-connected pieces of the exit's cells
  const gaps = [];
  const taken = new Set();
  for (let s0 = 0; s0 < cells.length; s0++) {
    if (taken.has(s0)) continue;
    taken.add(s0);
    const piece = [cells[s0]];
    const stack = [s0];
    while (stack.length) {
      const i = stack.pop();
      for (let j = 0; j < cells.length; j++) {
        if (!taken.has(j) && cheb(cells[i][0], cells[i][1], cells[j][0], cells[j][1]) <= 1) {
          taken.add(j);
          piece.push(cells[j]);
          stack.push(j);
        }
      }
    }
    const cx = piece.reduce((acc, c) => acc + c[0], 0) / piece.length;
    const cz = piece.reduce((acc, c) => acc + c[1], 0) / piece.length;
    // the defenders' spots: cells CHOKE_BACK steps inside the gap and within CHOKE_SPREAD of its centre, nearest row first
    const spots = [];
    for (const back of CHOKE_BACK) {
      const row = [];
      for (const [sx, sz] of order) {
        if (dist[sz * W + sx] !== best - back) continue;
        if (Math.max(Math.abs(sx - cx), Math.abs(sz - cz)) > CHOKE_SPREAD) continue;
        row.push([sx, sz]);
      }
      row.sort((a, b) => (a[0] - cx) ** 2 + (a[1] - cz) ** 2 - ((b[0] - cx) ** 2 + (b[1] - cz) ** 2));
      spots.push(...row);
    }
    if (spots.length) gaps.push({ cells: piece, cx, cz, spots });
  }
  if (!gaps.length) return null;
  x.choke = { d: best, width: cells.length, dist, gaps, held: gaps.reduce((a, g) => (g.cells.length > a.cells.length ? g : a)) };
  ctx.say(`The base exit is ${cells.length} wide in ${gaps.length} gap(s) (${gaps.map((g) => `${Math.round(g.cx)},${Math.round(g.cz)}`).join(' ')}): the defenders hold it under danger.`);
  return x.choke;
}

/**
 * VARIANT choke, under danger: intruders inside the exit (fewer steps from the base than the exit) are left to the
 * usual hunt; with none inside, every defender assault-moves to its own spot on the inner mouth of the gap nearest to
 * the nearest enemy fighter we see (else the gap held last; the widest at first; the spots shared out in a fixed order,
 * so a defender keeps its spot from rally to rally) and fires at whatever comes out of the passage - every enemy that
 * steps out meets all of ours, the ones behind it in the passage are out of range. True when the defenders were
 * ordered to the exit.
 */
function holdChoke(ctx, objs) {
  const x = state(ctx);
  const ch = chokeOf(ctx);
  if (!ch) return false;
  const { G } = ctx;
  const inside = x.intruders.filter((r) => {
    const d = r.x < G.map.w && r.z < G.map.h ? ch.dist[r.z * G.map.w + r.x] : -1;
    return d >= 0 && d < ch.d - 1;
  });
  if (inside.length) {
    x.intruders = inside; // the hunt below goes after the ones inside only
    return false;
  }
  if (ch.gaps.length > 1 && x.seenFighters.length) {
    let bestD = 1e9;
    for (const g of ch.gaps) {
      for (const r of x.seenFighters) {
        const d = cheb(r.x, r.z, g.cx, g.cz);
        if (d < bestD) {
          bestD = d;
          ch.held = g;
        }
      }
    }
  }
  const spots = ch.held.spots;
  const groups = new Map();
  [...objs].sort((a, b) => a - b).forEach((o, i) => {
    const sp = spots[i % spots.length];
    const key = `${sp[0]},${sp[1]}`;
    if (!groups.has(key)) groups.set(key, { sp, objs: [] });
    groups.get(key).objs.push(o);
  });
  for (const { sp, objs: g } of groups.values()) sendWaypointOrder(ctx, g, [[(sp[0] << 8) + 128, (sp[1] << 8) + 128]], ORDER_ASSAULT);
  x.stats.chokes = (x.stats.chokes ?? 0) + 1;
  return true;
}

// ---- the base patrol (VARIANT patrol) -----------------------------------------------------------------

/**
 * An attacker fires at the NEAREST object in its weapon range, unit or building alike (find_target's score
 * is 0 for every living target, so the first cell of its spiral search wins), and re-picks on every step of
 * an assault move. So a defender standing on the tile beside the attacker, on the side of our nearest
 * building, is always nearer than the building and takes the shots meant for it (maintainer, 8 Oct 2026:
 * "troops must take all damage instead of the base"). Each defender walks (a plain move: no stopping to
 * shoot on the way) to such a tile of the attacker nearest to it, the attackers shared out evenly; standing
 * there it fires back by itself (idle units fire at whatever is in range). An attacker with none of our
 * buildings within INTERPOSE_SCAN tiles is simply attacked.
 */
function interpose(ctx, units) {
  const x = state(ctx);
  const { G, p } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  const spots = [];
  for (const r of x.intruders) {
    let bx = -1;
    let bz = -1;
    let bd = 1e9;
    for (let dz = -INTERPOSE_SCAN; dz <= INTERPOSE_SCAN; dz++) {
      for (let dx = -INTERPOSE_SCAN; dx <= INTERPOSE_SCAN; dx++) {
        const cx = r.x + dx;
        const cz = r.z + dz;
        if (cx < 0 || cz < 0 || cx >= G.map.w || cz >= G.map.h) continue;
        const id = groundIdAt(G, cx, cz);
        if (id >= 120 || u8(gs, objAddr(id) + O.TEAM) !== p) continue; // our city buildings only
        const d = cheb(r.x, r.z, cx, cz);
        if (d < bd) {
          bd = d;
          bx = cx;
          bz = cz;
        }
      }
    }
    if (bx < 0) spots.push({ x: r.x, z: r.z, hunt: true, objs: [] });
    else spots.push({ x: r.x + Math.sign(bx - r.x), z: r.z + Math.sign(bz - r.z), hunt: false, objs: [] });
  }
  const cap = Math.ceil(units.length / spots.length);
  for (const o of units) {
    const [ux, uz] = tileOf(gs, objAddr(o));
    let best = null;
    let bd = 1e9;
    for (const s of spots) {
      const d = cheb(ux, uz, s.x, s.z);
      if (s.objs.length < cap && d < bd) {
        bd = d;
        best = s;
      }
    }
    // in place (or on its way there): it draws the fire and shoots back - a new order would stop its firing
    const last = x.lastOrder.get(o) ?? -1000000;
    if (!best.hunt && (bd <= 2 || (!isIdle(gs, objAddr(o)) && tick - last < INTERPOSE_REISSUE))) continue;
    x.lastOrder.set(o, tick);
    best.objs.push(o);
  }
  for (const s of spots) {
    if (!s.objs.length) continue;
    sendWaypointOrder(ctx, s.objs, [[(s.x << 8) + 128, (s.z << 8) + 128]], ORDER_ASSAULT);
  }
  x.stats.interposes = (x.stats.interposes ?? 0) + 1;
}

/** The ring PATROL_OFFSET tiles outside the box of our city buildings: its four corners and four edge middles. */
function patrolRing(ctx) {
  const { G, p } = ctx;
  const gs = G.gs;
  let x0 = 1e9;
  let z0 = 1e9;
  let x1 = -1;
  let z1 = -1;
  for (let o = 15 * p; o < 15 * p + 15; o++) {
    const a = objAddr(o);
    if (!alive(u8(gs, a + O.LIFE)) || u8(gs, a + O.TEAM) !== p) continue;
    const [tx, tz] = tileOf(gs, a);
    x0 = Math.min(x0, tx);
    z0 = Math.min(z0, tz);
    x1 = Math.max(x1, tx + 2); // the object stands on a corner of a footprint up to 3 x 3
    z1 = Math.max(z1, tz + 2);
  }
  if (x1 < 0) return [];
  const cl = (v, hi) => Math.min(hi - 1, Math.max(0, v));
  const l = cl(x0 - PATROL_OFFSET, G.map.w);
  const r = cl(x1 + PATROL_OFFSET, G.map.w);
  const t = cl(z0 - PATROL_OFFSET, G.map.h);
  const b = cl(z1 + PATROL_OFFSET, G.map.h);
  const mx = (l + r) >> 1;
  const mz = (t + b) >> 1;
  return [[l, t], [mx, t], [r, t], [r, mz], [r, b], [mx, b], [l, b], [l, mz]];
}

/** The units walk the ring, each one point further every PATROL_STEP ticks (assault: they fire at what they meet). */
function walkRing(ctx, units) {
  const x = state(ctx);
  const gs = ctx.G.gs;
  const ring = patrolRing(ctx);
  if (!ring.length || !units.length) return;
  const phase = Math.floor((ctx.tick | 0) / PATROL_STEP);
  const fresh = phase !== x.patrolPhase;
  x.patrolPhase = phase;
  const byPoint = new Map();
  units.forEach((o, i) => {
    if (!fresh && !isIdle(gs, objAddr(o))) return;
    const k = (i + phase) % ring.length;
    if (!byPoint.has(k)) byPoint.set(k, []);
    byPoint.get(k).push(o);
  });
  for (const [k, objs] of byPoint) sendWaypointOrder(ctx, objs, [[(ring[k][0] << 8) + 128, (ring[k][1] << 8) + 128]], ORDER_ASSAULT);
}

/** VARIANT patrol without danger: the home guard walks the ring (the original sends it to a random zone near home). */
function patrolHome(ctx) {
  const x = state(ctx);
  const { G, kai } = ctx;
  const gs = G.gs;
  const guard = minorAddr(1, 0);
  if (!u8(kai, guard + MN.ACTIVE)) return;
  if ((i32(kai, guard + MN.DEST) & 0xff) !== x.home) setRoute(ctx, 1, 0, x.home, null);
  if ((ctx.tick | 0) - x.lastRally < RALLY_EVERY) return;
  x.lastRally = ctx.tick | 0;
  const zone = i32(kai, guard + MN.ZONE) & 0xff;
  const units = listOf(gs, kai, 1, 0).filter((o) => {
    const a = objAddr(o);
    if (!alive(u8(gs, a + O.LIFE)) || !isFighter(G, u8(gs, a + O.TYPE))) return false;
    w8(gs, a + OA.ZONE, zone); // move_group leaves it alone
    return true;
  });
  walkRing(ctx, units);
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
    const it = buyableTroops(G, p).find((i) => INFANTRY.includes(i.type) && queueOpen(ctx, i.type));
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

// ---- the escorted expansion (VARIANT escort) ---------------------------------------------------------

function newEscort() {
  return {
    // idle -> gather (troopers at the HQ) -> march (to the vent, the explorer behind) -> arrived (deploying);
    // VARIANT second: march -> decoy on contact (the explorer alone to the vent, the escort fighting at cx, cz)
    phase: 'idle',
    cx: 0,
    cz: 0,
    hp: new Map(), // escort trooper -> hit points at the last think (VARIANT second: fire contact)
    shielding: false, // VARIANT shield: walking on in contact, the explorer on the far side
    expl: -1,
    vent: -1,
    vx: 0,
    vz: 0,
    zone: 0,
    since: 0,
    issued: -1000000,
    explOrdered: -1000000,
    lastReinf: -1000000,
    reinforced: 0, // reinforcement requests of this expedition (at most ESCORT_MAX_REINF)
    retryAt: 0,
    size: ESCORT_SIZE, // troopers of this expedition (SECOND_ESCORT for the second site)
    recruits: 0, // troopers still to buy for the expedition
    reinfTank: 0, // reinforcements still to buy
    reinfInf: 0,
    pendInf: 0, // bought, waited for in the census (take)
    pendTank: 0,
    pendSince: 0,
    fails: new Map(), // vent object -> { n, tick }
  };
}

/** The escort machinery runs for the switch escort (every expansion) or second (the second mining site only). */
const escortOn = (ctx) => !!(variant(ctx).escort || variant(ctx).second);

/**
 * VARIANT second: a second worker-or-mine exists and two mines never stood yet - the second explorer goes with
 * SECOND_ESCORT troopers (the starting trooper), the first one (already on its way or deployed) is not touched.
 */
function secondDue(ctx, x) {
  return !!variant(ctx).second && !x.secondDone && listOf(ctx.G.gs, ctx.kai, 0, 0).length >= 2;
}

/**
 * VARIANT second (maintainer, 8 Oct 2026: "build the second explorer right before the barracks and send it with
 * one trooper to the vent"): no barracks yet, fewer than two workers-or-mines (queued ones counted) and not later
 * than SECOND_FIRST_MAX - the expansion lane buys the second explorer and the barracks goal waits for it.
 */
export function secondFirst(ctx) {
  const x = state(ctx);
  if (!x || !variant(ctx).second || (ctx.tick | 0) > SECOND_FIRST_MAX) return false;
  const { G, p, kai } = ctx;
  const gs = G.gs;
  const pa = playerAddr(p);
  if (i32(gs, pa + P.SLOT_HP + 4) !== 0) return false;
  let n = listOf(gs, kai, 0, 0).length;
  for (let k = 0; k < 4; k++) {
    const len = gs.readUInt16LE(pa + P.QUEUE_LEN + 2 * k);
    for (let i = 0; i < len; i++) if (EXPLORER_OBJ_TYPES.includes(u8(gs, pa + P.QUEUE + 800 * k + i))) n++;
  }
  return n < 2;
}

/** For worker_update: the escorted explorer (moved by the escort only) and the zone of its vent. */
export function escortInfo(ctx) {
  const x = state(ctx);
  if (!x || !escortOn(ctx)) return NO_ESCORT;
  const e = x.esc;
  // before the first wave (no enemy around yet, no troopers to spare) and with no mine yet the dispatch of
  // worker_update reaches every vent (with safe: over a route free of enemy units); VARIANT second: the
  // second explorer goes with its trooper whatever the wave
  const on = (variant(ctx).escort && x.own.mines > 0 && x.waveSeen) || secondDue(ctx, x);
  return { on, objs: e.expl >= 0 ? new Set([e.expl]) : NO_ESCORT.objs, zone: e.phase === 'idle' ? 0 : e.zone };
}

/** VARIANTS factory / gate (saving for the tanks) and safe (an explorer died a moment ago): the expansion lane waits. */
export function expansionPaused(ctx) {
  const x = state(ctx);
  if (!x) return false;
  const v = variant(ctx);
  if (v.keep2 && keep2Due(ctx)) return false; // VARIANT keep2: below two mining sites the lane never waits
  return x.saving || (v.safe && (ctx.tick | 0) - x.lastExplLoss < EXPL_COOLDOWN) || mechsFirst(ctx);
}

/**
 * VARIANT keep2 (maintainer, 9 Oct 2026: "face the rusher with two mines"): fewer than two mines stand (a lost mine
 * counts as missing) - the expansion lane buys the explorer whatever the saving modes say.
 */
function keep2Due(ctx) {
  const x = state(ctx);
  return !!x && x.own.mines < 2;
}

/**
 * VARIANT mechfirst (maintainer, 8 Oct 2026: "don't save money for the third explorer until a few reapers / scythes are
 * produced"): with two workers-or-mines standing, the lane buys no explorer before MECH_FIRST mechs were produced
 * (fielded plus lost, the game's per-type counters).
 */
function mechsFirst(ctx) {
  if (!variant(ctx).mechfirst) return false;
  const { G, p, kai } = ctx;
  if (listOf(G.gs, kai, 0, 0).length < 2) return false; // the first two sites are not held back
  let produced = 0;
  for (const t of TANKS) produced += G.typeStat(0, p, t);
  const maxObj = i32(G.gs, GS.MAX_OBJ);
  for (let o = 120; o <= maxObj; o++) {
    const a = objAddr(o);
    if (u8(G.gs, a + O.TEAM) === p && alive(u8(G.gs, a + O.LIFE)) && TANKS.includes(u8(G.gs, a + O.TYPE))) produced++;
  }
  return produced < MECH_FIRST;
}

const groundIdAt = (G, x, z) => G.map.ground[z * G.map.w + x] & 0x3ff;

/** The reinforcements asked for in contact: a mech if one can be bought, else two troopers. */
function escortBuy(ctx) {
  const x = state(ctx);
  const e = x.esc;
  if (e.phase === 'idle') return false;
  const tick = ctx.tick | 0;
  if (e.reinfTank > 0) {
    if (buy(ctx, TANKS, 1, 'Escort reinforcement')) {
      e.reinfTank--;
      e.pendTank++;
      e.pendSince = tick;
      x.stats.reinforcements = (x.stats.reinforcements ?? 0) + 1;
      return true;
    }
    return false; // the money waits for the tank
  }
  if (e.reinfInf > 0 && buy(ctx, INFANTRY, 1, 'Escort reinforcement')) {
    // one trooper at a time as the money comes
    e.reinfInf--;
    e.pendInf++;
    e.pendSince = tick;
    if (!e.reinfInf) x.stats.reinforcements = (x.stats.reinforcements ?? 0) + 1;
    return true;
  }
  return false;
}

/** The troopers of the expedition that the defenders could not give, bought one at a time as the money comes. */
function escortRecruit(ctx) {
  const x = state(ctx);
  const e = x.esc;
  if (e.phase !== 'gather' || e.recruits <= 0) return false;
  if (!buy(ctx, INFANTRY, 1, 'Escort')) return false;
  e.recruits--;
  e.pendInf++;
  e.pendSince = ctx.tick | 0;
  return true;
}

/** Order the explorer somewhere (a plain move) at most every other think. */
function moveExplorer(ctx, tx, tz, force = false) {
  const e = state(ctx).esc;
  const tick = ctx.tick | 0;
  if (!force && tick - e.explOrdered < 64) return;
  sendWaypointOrder(ctx, [e.expl], [[(tx << 8) + 128, (tz << 8) + 128]], ORDER_MOVE);
  e.explOrdered = tick;
}

/** End the expedition: the troopers back to the census, the explorer free again (unless it is the mine now). */
function endEscort(ctx, why, recall) {
  const x = state(ctx);
  const e = x.esc;
  const { G, p } = ctx;
  const gs = G.gs;
  if (x.special[ESCORT_SLOT]) release(ctx, ESCORT_SLOT);
  if (e.expl >= 0) {
    const a = objAddr(e.expl);
    if (alive(u8(gs, a + O.LIFE)) && u8(gs, a + O.TEAM) === p && EXPLORER_OBJ_TYPES.includes(u8(gs, a + O.TYPE))) {
      w8(gs, a + OA.ZONE, 0); // worker_update may send it again (or the next expedition takes it)
      if (recall) moveExplorer(ctx, x.hq[0], x.hq[1], true);
    }
  }
  ctx.say(`Escort: ${why}.`);
  Object.assign(e, newEscort(), { fails: e.fails, retryAt: e.retryAt });
}

/**
 * VARIANT gate (maintainer, 8 Oct 2026: "lift the science gate when the third site fails"): hold's opening keeps the
 * chain to the HQ and the barracks until three workers-or-mines stand; once an attempt at the third site has failed
 * (its escort failed, or an explorer died while two mines stood) science and the factory no longer wait for it.
 */
function markThirdFailed(ctx, why) {
  const x = state(ctx);
  if (x.thirdFailed || !variant(ctx).gate) return;
  x.thirdFailed = true;
  ctx.say(`Gate: ${why} - science and the factory no longer wait for the third mine.`);
}

function failEscort(ctx, why) {
  const x = state(ctx);
  const e = x.esc;
  if (e.size !== SECOND_ESCORT && x.own.mines === 2) markThirdFailed(ctx, `the third site's escort failed (${why})`);
  const tick = ctx.tick | 0;
  const f = e.fails.get(e.vent);
  e.fails.set(e.vent, { n: (f && tick - f.tick < FAIL_MEMORY ? f.n : 0) + 1, tick });
  e.retryAt = tick + RETRY_AFTER;
  x.stats.escortsLost = (x.stats.escortsLost ?? 0) + 1;
  endEscort(ctx, why, true);
}

/** idle -> gather: a free explorer, a free vent beyond ESCORT_FREE_HOP, troopers from the defenders (the rest bought). */
function startEscort(ctx) {
  const x = state(ctx);
  const e = x.esc;
  const { G, p, kai } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  const second = secondDue(ctx, x); // VARIANT second: right after the barracks, whatever the wave, danger or saving
  if (tick < e.retryAt) return;
  if (!second && (!variant(ctx).escort || x.saving || x.own.mines === 0 || !x.waveSeen)) return;
  const size = second ? SECOND_ESCORT : ESCORT_SIZE;
  const list = listOf(gs, kai, 0, 0);
  let expl = -1;
  for (const o of list) {
    const a = objAddr(o);
    if (EXPLORER_OBJ_TYPES.includes(u8(gs, a + O.TYPE)) && alive(u8(gs, a + O.LIFE)) && u8(gs, a + OA.ZONE) === 0) expl = o;
  }
  if (expl < 0) return;
  const taken = new Set(list.map((o) => u8(gs, objAddr(o) + OA.ZONE)));
  let best = -1;
  let bestScore = 1e9;
  for (let o = 0; o < MAX_OBJECTS; o++) {
    const a = objAddr(o);
    if (u8(gs, a + O.TYPE) !== TYPE_VENT || i16(gs, a + O_VENT_RATE) === 0 || !alive(u8(gs, a + O.LIFE))) continue;
    const [vx, vz] = tileOf(gs, a);
    if (groundIdAt(G, vx, vz) !== 0x3ff) continue;
    const vfam = famAt(G, vx, vz);
    if (!vfam || taken.has(vfam)) continue;
    const hop = u8(kai, zoneAddr(vfam) + Z.HOP);
    if (hop <= ESCORT_FREE_HOP || hop === 0xff) continue;
    const f = e.fails.get(o);
    const score = hop + (f && tick - f.tick < FAIL_MEMORY ? 3 * f.n : 0);
    if (score < bestScore) {
      bestScore = score;
      best = o;
    }
  }
  if (best < 0) return;
  // the troopers: defenders nearest to the HQ (and the infantry standing in for scouts, task 3, where the starting
  // trooper often is), ESCORT_HOME_KEEP left at home
  const defenders = [];
  for (const t of [1, 3]) {
    for (let m = 0; m < NMINORS; m++) {
      if (!u8(kai, minorAddr(t, m) + MN.ACTIVE)) continue;
      for (const o of listOf(gs, kai, t, m)) {
        const a = objAddr(o);
        const type = u8(gs, a + O.TYPE);
        if (!alive(u8(gs, a + O.LIFE)) || !(INFANTRY.includes(type) || TANKS.includes(type))) continue;
        const [ux, uz] = tileOf(gs, a);
        const d = cheb(ux, uz, x.hq[0], x.hq[1]);
        // a trooper far afield (a stand-in scout) is not taken, one is bought instead - except the second site's trooper,
        // before the barracks the starting trooper is the only one there can be (the gather calls it home first)
        if (d <= HOME_RADIUS || second) defenders.push({ o, t, m, d });
      }
    }
  }
  defenders.sort((a, b) => a.d - b.d);
  // under danger (against a rush nearly always): up to the third site the expedition goes anyway, ESCORT_HOME_KEEP
  // defenders stay and the rest is bought ("while few troopers hold the rusher's offence, take three mining sites");
  // beyond it only a surplus goes, ESCORT_HOME_KEEP plus one per enemy at the gates staying. The second site's
  // trooper is simply the nearest one (the starting trooper), none kept back.
  const third = x.own.mines < ESCORT_SITES;
  const keep = second ? 0 : ESCORT_HOME_KEEP + (x.danger && !third ? x.enemyNearHome : 0);
  // VARIANT second: the trooper reserved at the HQ since the first census is already in the slot
  const reserved = x.special[ESCORT_SLOT] ? slotUnits(ctx, ESCORT_SLOT).filter((o) => isFighter(G, u8(gs, objAddr(o) + O.TYPE))).length : 0;
  const take = Math.max(0, Math.min(size - reserved, defenders.length - keep));
  const give = reserved + take;
  if (!second && x.danger && !third && give < size) return;
  ensureSlot(ctx, ESCORT_SLOT, KIND_ESCORT);
  for (const d of defenders.slice(0, take)) {
    unlink(gs, kai, d.o, d.t, d.m);
    link(gs, kai, d.o, 2, ESCORT_SLOT);
  }
  const va = objAddr(best);
  const [vx, vz] = tileOf(gs, va);
  Object.assign(e, { phase: 'gather', expl, vent: best, vx, vz, zone: famAt(G, vx, vz), since: tick, issued: -1000000, explOrdered: -1000000, lastReinf: -1000000, size, recruits: size - give });
  x.stats.escorts = (x.stats.escorts ?? 0) + 1;
  ctx.say(`Escort: ${give} troopers and an explorer for the vent at ${vx},${vz}${e.recruits ? `, ${e.recruits} more to train` : ''}.`);
}

/**
 * VARIANT shield (maintainer, 8 Oct 2026: "when the escort meets an enemy it continues walking to the vent; the explorer
 * moves to the position of maximal distance to the offending enemy - enemy from the left, explorer on the right side of
 * the escort, so all damage is taken by the escort"): the troopers walk on to their point by the vent (a plain move, no
 * stopping to fight); the explorer keeps SHIELD_SIDE tiles beyond the escort's centre on the side away from the centre
 * of the enemy fighters seen within SHIELD_SCAN tiles, one tile ahead towards the vent, re-ordered every think. An
 * attacker that hits without being seen gives no side: the explorer then trails FOLLOW tiles behind as before.
 */
function shieldMarch(ctx, units, cx, cz, ex, ez, guardPoint) {
  const x = state(ctx);
  const e = x.esc;
  const G = ctx.G;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  if (!e.shielding || units.some((o) => isIdle(gs, objAddr(o))) || tick - e.issued >= REISSUE) {
    if (!e.shielding) {
      x.stats.shields = (x.stats.shields ?? 0) + 1;
      ctx.say(`Escort in contact near ${cx},${cz}: it walks on to ${e.vx},${e.vz}, the explorer on its far side.`);
    }
    e.shielding = true;
    sendWaypointOrder(ctx, units, [guardPoint], ORDER_MOVE);
    e.issued = tick;
  }
  let sx = 0;
  let sz = 0;
  let n = 0;
  for (const r of x.seenFighters) {
    if (cheb(r.x, r.z, cx, cz) > SHIELD_SCAN) continue;
    sx += r.x;
    sz += r.z;
    n++;
  }
  let dx;
  let dz;
  if (n) {
    dx = cx - sx / n; // away from the enemy
    dz = cz - sz / n;
  } else {
    dx = ex - cx; // no side known: behind, on the explorer's own side
    dz = ez - cz;
  }
  const dn = Math.hypot(dx, dz) || 1;
  const fx = e.vx - cx;
  const fz = e.vz - cz;
  const fn = Math.hypot(fx, fz) || 1;
  const side = n ? SHIELD_SIDE : FOLLOW;
  const ahead = n ? 1 : 0;
  const tx = Math.min(G.map.w - 1, Math.max(0, Math.round(cx + (dx / dn) * side + (fx / fn) * ahead)));
  const tz = Math.min(G.map.h - 1, Math.max(0, Math.round(cz + (dz / dn) * side + (fz / fn) * ahead)));
  if (cheb(ex, ez, tx, tz) > 1) {
    sendWaypointOrder(ctx, [e.expl], [[(tx << 8) + 128, (tz << 8) + 128]], ORDER_MOVE);
    e.explOrdered = tick;
  }
}

/**
 * VARIANT second, the detour (maintainer, 9 Oct 2026: "exploiter is not avoiding offenders ... sent right in the direction of the
 * opponents"; on contact: "detour to another vent"): at the second site's first exchange of fire the explorer gives up
 * its vent (a fail mark, as failEscort) for the nearest free vent (zone hops from home, then tiles from the explorer)
 * that lies DETOUR_CLEAR tiles from every enemy fighter seen and whose straight way from the explorer keeps DETOUR_PATH
 * tiles from them; the escort holds the enemy at the meeting point as before. False when there is no such vent.
 */
function detour(ctx, ex, ez) {
  const x = state(ctx);
  const e = x.esc;
  const { G, kai } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  const foes = x.seenFighters ?? [];
  const bases = x.enemy?.buildings ?? [];
  const taken = new Set(listOf(gs, kai, 0, 0).filter((o) => o !== e.expl).map((o) => u8(gs, objAddr(o) + OA.ZONE)));
  const plan = safeSearch(ctx, ex, ez);
  let best = -1;
  let bestLen = 1e9;
  for (let o = 0; o < MAX_OBJECTS; o++) {
    const a = objAddr(o);
    if (o === e.vent || u8(gs, a + O.TYPE) !== TYPE_VENT || i16(gs, a + O_VENT_RATE) === 0 || !alive(u8(gs, a + O.LIFE))) continue;
    const [vx, vz] = tileOf(gs, a);
    if (groundIdAt(G, vx, vz) !== 0x3ff) continue;
    const vfam = famAt(G, vx, vz);
    if (!vfam || taken.has(vfam)) continue;
    if (foes.some((r) => cheb(r.x, r.z, vx, vz) <= DETOUR_CLEAR)) continue;
    if (bases.some((r) => cheb(r.x, r.z, vx, vz) <= DETOUR_BASE)) continue;
    const len = plan.dist[vz * G.map.w + vx];
    if (len < 0 || len >= bestLen) continue;
    bestLen = len;
    best = o;
  }
  if (best < 0) return false;
  const f = e.fails.get(e.vent);
  e.fails.set(e.vent, { n: (f && tick - f.tick < FAIL_MEMORY ? f.n : 0) + 1, tick });
  const [vx, vz] = tileOf(gs, objAddr(best));
  ctx.say(`Escort: detour from the vent at ${e.vx},${e.vz} to the one at ${vx},${vz} (${bestLen} steps, clear of the enemy).`);
  Object.assign(e, { vent: best, vx, vz, zone: famAt(G, vx, vz), since: tick, route: routeTo(plan, vx, vz, G.map.w) });
  x.stats.detours = (x.stats.detours ?? 0) + 1;
  return true;
}

/**
 * The detour's search: a walk (8 directions, walkable cells) from (ex, ez) that keeps DETOUR_PATH tiles from every
 * enemy fighter we see - near the start only as far as the explorer already is - up to DETOUR_MAX steps. Returns
 * { dist, prev } over the map's cells (-1 = not reached).
 */
function safeSearch(ctx, ex, ez) {
  const x = state(ctx);
  const { G } = ctx;
  const W = G.map.w;
  const H = G.map.h;
  const foes = x.seenFighters ?? [];
  const startD = foes.reduce((m, r) => Math.min(m, cheb(r.x, r.z, ex, ez)), 99);
  const keep = Math.max(1, Math.min(DETOUR_PATH, startD));
  const dist = new Int16Array(W * H).fill(-1);
  const prev = new Int32Array(W * H).fill(-1);
  const blocked = (cx, cz) => foes.some((r) => cheb(r.x, r.z, cx, cz) < keep);
  dist[ez * W + ex] = 0;
  let front = [[ex, ez]];
  for (let d = 1; d <= DETOUR_MAX && front.length; d++) {
    const next = [];
    for (const [cx, cz] of front) {
      for (let k = 0; k < 9; k++) {
        if (k === 4) continue;
        const nx = cx + (k % 3) - 1;
        const nz = cz + Math.floor(k / 3) - 1;
        if (nx < 0 || nz < 0 || nx >= W || nz >= H || dist[nz * W + nx] >= 0) continue;
        if (!Grid.isWalkable(G, nx, nz) || blocked(nx, nz)) continue;
        dist[nz * W + nx] = d;
        prev[nz * W + nx] = cz * W + cx;
        next.push([nx, nz]);
      }
    }
    front = next;
  }
  return { dist, prev };
}

/** Up to ROUTE_POINTS evenly spaced tiles of the searched path to (tx, tz), the last one (tx, tz) itself. */
function routeTo(plan, tx, tz, W) {
  const cells = [];
  for (let i = tz * W + tx; i >= 0; i = plan.prev[i]) cells.push(i);
  cells.reverse();
  const out = [];
  for (let k = 1; k <= ROUTE_POINTS; k++) {
    const i = cells[Math.min(cells.length - 1, Math.round((k * (cells.length - 1)) / ROUTE_POINTS))];
    const p = [i % W, Math.floor(i / W)];
    if (!out.length || out.at(-1)[0] !== p[0] || out.at(-1)[1] !== p[1]) out.push(p);
  }
  return out;
}

/** The explorer along its planned route (the points still ahead of it), else straight to the vent at `va`. */
function sendExplorerRoute(ctx, va) {
  const e = state(ctx).esc;
  const gs = ctx.G.gs;
  const [ex, ez] = tileOf(gs, objAddr(e.expl));
  if (e.route?.length) {
    let k = 0;
    let bd = 1e9;
    e.route.forEach(([px, pz], i) => {
      const d = cheb(px, pz, ex, ez);
      if (d < bd) {
        bd = d;
        k = i;
      }
    });
    const ahead = e.route.slice(bd <= 2 ? k + 1 : k);
    if (ahead.length) {
      sendWaypointOrder(ctx, [e.expl], ahead.map(([px, pz]) => [(px << 8) + 128, (pz << 8) + 128]), ORDER_MOVE);
      return;
    }
  }
  sendWaypointOrder(ctx, [e.expl], [[gs.readUInt16LE(va + O.X), gs.readUInt16LE(va + O.Z)]], ORDER_MOVE);
}

/**
 * VARIANT second (maintainer, 8 Oct 2026): at the first exchange of fire the explorer runs on to the vent alone, not
 * waiting for the escort, which stays and fights where it met the enemy (cx, cz), drawing the enemy's troops onto itself.
 */
function startDecoy(ctx, units, va, cx, cz, why) {
  const x = state(ctx);
  const e = x.esc;
  const gs = ctx.G.gs;
  e.phase = 'decoy';
  e.cx = cx;
  e.cz = cz;
  w8(gs, objAddr(e.expl) + OA.ZONE, e.zone);
  sendExplorerRoute(ctx, va);
  e.explOrdered = ctx.tick | 0;
  e.issued = -1000000;
  x.stats.decoys = (x.stats.decoys ?? 0) + 1;
  ctx.say(`Escort: ${why}, the explorer runs on to ${e.vx},${e.vz} alone, the escort holds the enemy there.`);
  decoy(ctx, units, va);
}

/**
 * VARIANT second, after the contact: the explorer heads for the vent on its own (it deploys there by itself), the
 * escort fights at the meeting point (the nearest enemy fighter seen within DECOY_RADIUS of it, else the point itself),
 * re-ordered every RALLY_EVERY ticks. The escort dying no longer stops the explorer.
 */
function decoy(ctx, units, va) {
  const x = state(ctx);
  const e = x.esc;
  const gs = ctx.G.gs;
  const tick = ctx.tick | 0;
  const ea = objAddr(e.expl);
  const [ex, ez] = tileOf(gs, ea);
  if ((ex !== e.vx || ez !== e.vz) && (isIdle(gs, ea) || tick - e.explOrdered > REISSUE)) {
    sendExplorerRoute(ctx, va);
    e.explOrdered = tick;
  }
  if (!units.length || tick - e.issued < RALLY_EVERY) return;
  e.issued = tick;
  let target = null;
  let bd = DECOY_RADIUS + 1;
  for (const r of x.seenFighters) {
    const d = cheb(r.x, r.z, e.cx, e.cz);
    if (d < bd) {
      bd = d;
      target = r;
    }
  }
  const [tx, tz] = target ? [target.x, target.z] : [e.cx, e.cz];
  sendWaypointOrder(ctx, units, [[(tx << 8) + 128, (tz << 8) + 128]], ORDER_ASSAULT);
}

function escort(ctx) {
  const x = state(ctx);
  const e = x.esc;
  const { G, p } = ctx;
  const gs = G.gs;
  const tick = ctx.tick | 0;
  if ((e.pendInf || e.pendTank) && tick - e.pendSince > PEND_MAX) e.pendInf = e.pendTank = 0; // the recruits went elsewhere
  if (e.phase === 'idle') {
    // VARIANT second: the reserved trooper waits at the HQ - not while the perimeter is attacked (the defence has it);
    // during the push beyond it, it waits for the next explorer
    if (x.special[ESCORT_SLOT] && tick - e.issued >= REISSUE && !x.threats.length) {
      const away = slotUnits(ctx, ESCORT_SLOT).filter((o) => {
        const [ux, uz] = tileOf(gs, objAddr(o));
        return cheb(ux, uz, x.hq[0], x.hq[1]) > 3;
      });
      if (away.length) sendWaypointOrder(ctx, away, [[(x.hq[0] << 8) + 128, (x.hq[1] << 8) + 128]], ORDER_ASSAULT);
      e.issued = tick;
    }
    startEscort(ctx);
    return;
  }
  const ea = objAddr(e.expl);
  if (!alive(u8(gs, ea + O.LIFE)) || u8(gs, ea + O.TEAM) !== p) {
    failEscort(ctx, 'the explorer was lost');
    return;
  }
  if (MINING_TOWERS.includes(u8(gs, ea + O.TYPE))) {
    x.stats.escorted = (x.stats.escorted ?? 0) + 1;
    e.expl = -1; // the mine stays in the worker task with its vent zone
    endEscort(ctx, `the mine at ${e.vx},${e.vz} stands`, false);
    return;
  }
  const va = objAddr(e.vent);
  const occupant = groundIdAt(G, e.vx, e.vz);
  const taken = occupant !== 0x3ff && occupant !== 0x3fe && u8(gs, objAddr(occupant) + O.TEAM) !== p; // somebody else's unit or mine
  if (!alive(u8(gs, va + O.LIFE)) || i16(gs, va + O_VENT_RATE) === 0 || taken) {
    endEscort(ctx, `the vent at ${e.vx},${e.vz} is gone`, true);
    return;
  }
  if (tick - e.since > ESCORT_MAX) {
    failEscort(ctx, `the vent at ${e.vx},${e.vz} took too long`);
    return;
  }
  const units = slotUnits(ctx, ESCORT_SLOT).filter((o) => isFighter(G, u8(gs, objAddr(o) + O.TYPE)));
  const [hx, hz] = x.hq;
  const [ex, ez] = tileOf(gs, ea);
  const idleAny = units.some((o) => isIdle(gs, objAddr(o)));
  // the troopers' point: GUARD_OFFSET tiles short of the vent on the HQ's side, so that the vent tile stays free for the explorer
  const gx = Math.min(G.map.w - 1, Math.max(0, e.vx + GUARD_OFFSET * Math.sign(hx - e.vx)));
  const gz = Math.min(G.map.h - 1, Math.max(0, e.vz + GUARD_OFFSET * Math.sign(hz - e.vz)));
  const guardPoint = [(gx << 8) + 128, (gz << 8) + 128];
  if (e.phase === 'gather') {
    if (cheb(ex, ez, hx, hz) > 3) moveExplorer(ctx, hx, hz);
    const pending = e.recruits + e.pendInf + e.pendTank;
    const gathered = units.filter((o) => {
      const [ux, uz] = tileOf(gs, objAddr(o));
      return cheb(ux, uz, hx, hz) <= GATHER_RADIUS + 2;
    }).length;
    // the march starts from the HQ: the whole escort gathered there (or, after GATHER_MAX, whoever has come); the
    // second site's trooper goes straight to the vent from wherever it is, the explorer falls in behind it (below)
    const direct = e.size === SECOND_ESCORT && units.length >= e.size;
    if (direct || (gathered >= e.size || (gathered === units.length && gathered && !pending)) || (gathered && tick - e.since > GATHER_MAX)) {
      e.phase = 'march';
      e.issued = -1000000;
      ctx.say(`Escort: ${units.length} troopers march on the vent at ${e.vx},${e.vz}, the explorer behind them.`);
    } else if (!units.length && tick - e.since > GATHER_MAX) {
      failEscort(ctx, 'no troopers came');
      return;
    } else {
      // maintainer, 9 Oct 2026: "why can't you start expansion in parallel to the battle, when perimeter of the base is
      // secured and forces are pushing enemy further?" - the expedition waits only while the perimeter is attacked
      // (x.threats: the base, a mine, a shooter at us); during the push beyond it the escort gathers and marches
      if (units.length && !x.threats?.length && (idleAny || tick - e.issued >= REISSUE)) {
        sendWaypointOrder(ctx, units, [[(hx << 8) + 128, (hz << 8) + 128]], ORDER_ASSAULT);
        e.issued = tick;
      }
      return;
    }
  }
  if (e.phase === 'decoy') {
    decoy(ctx, units, va);
    return;
  }
  const second = e.size === SECOND_ESCORT;
  // VARIANT second: fire contact = an escort trooper lost hit points since the last think (the shooter need not be in sight)
  let hit = false;
  for (const o of units) {
    const hp = i32(gs, objAddr(o) + O.HP);
    if (hp < (e.hp.get(o) ?? hp)) hit = true;
    e.hp.set(o, hp);
  }
  if (!units.length && second && e.phase === 'march') {
    // the escort fell before a split was seen: the explorer does not wait for it, it goes on to the vent alone
    startDecoy(ctx, units, va, ex, ez, `the escort of ${e.vx},${e.vz} fell`);
    return;
  }
  if (!units.length) {
    failEscort(ctx, `the escort to ${e.vx},${e.vz} fell, the explorer comes home`);
    return;
  }
  let cx = 0;
  let cz = 0;
  for (const o of units) {
    const [ux, uz] = tileOf(gs, objAddr(o));
    cx += ux;
    cz += uz;
  }
  cx = Math.round(cx / units.length);
  cz = Math.round(cz / units.length);
  // contact = an enemy fighter met on the way; inside the base the defenders fight the attackers and the escort
  // walks out without stopping (it would otherwise stand at the gates, the rusher's trickle never ending)
  const inBase = cheb(cx, cz, hx, hz) <= RALLY_RADIUS;
  const seen = x.seenFighters.some((r) => cheb(r.x, r.z, cx, cz) <= CONTACT_RADIUS || cheb(r.x, r.z, ex, ez) <= CONTACT_RADIUS);
  // the second site's escort splits at the first exchange of fire wherever it happens (VARIANT second); the larger
  // escorts count only what they meet outside the base
  const contact = second ? hit || seen : !inBase && seen;
  if (contact && tick - e.lastReinf >= REINFORCE_EVERY && !e.reinfTank && !e.reinfInf && e.reinforced < ESCORT_MAX_REINF) {
    e.reinforced++;
    if (buyableTroops(G, p).some((it) => TANKS.includes(it.type))) e.reinfTank = 1;
    else e.reinfInf = 2;
    e.lastReinf = tick;
    ctx.say(`Escort in contact near ${cx},${cz}: ${e.reinfTank ? 'a tank' : 'two troopers'} as reinforcement.`);
  }
  // VARIANT shield: in contact the escort walks on and the explorer keeps to its far side (once it has joined the escort);
  // only the larger escorts - the second site's single trooper is too thin a shield, there the explorer splits off
  // (maintainer, 8 Oct 2026: "split for second, shield for larger escorts")
  const shielding = e.phase === 'march' && contact && !second && !!variant(ctx).shield && cheb(ex, ez, cx, cz) <= SHIELD_JOIN;
  if (!shielding && e.shielding) {
    e.shielding = false;
    e.issued = -1000000; // the march orders again at once (assault: fire at what they meet)
  }
  if (e.phase === 'march' && contact && second) {
    if (!detour(ctx, ex, ez)) {
      // no vent clear of the enemy: home, along a path clear of it where there is one
      const plan = safeSearch(ctx, ex, ez);
      const [hx0, hz0] = x.hq;
      let home = null;
      for (let dz = -3; dz <= 3 && !home; dz++) for (let dx = -3; dx <= 3 && !home; dx++) if (plan.dist[(hz0 + dz) * G.map.w + hx0 + dx] >= 0) home = [hx0 + dx, hz0 + dz];
      const expl = e.expl;
      failEscort(ctx, `escort in contact near ${cx},${cz} and no vent clear of the enemy, the explorer comes home`);
      if (home) sendWaypointOrder(ctx, [expl], routeTo(plan, home[0], home[1], G.map.w).map(([px, pz]) => [(px << 8) + 128, (pz << 8) + 128]), ORDER_MOVE);
      return;
    }
    startDecoy(ctx, units, objAddr(e.vent), cx, cz, `escort in contact near ${cx},${cz}`);
    return;
  }
  if (e.phase === 'march') {
    if (cheb(cx, cz, e.vx, e.vz) <= ARRIVE && (!contact || shielding)) {
      e.phase = 'arrived';
      w8(gs, ea + OA.ZONE, e.zone);
      sendWaypointOrder(ctx, [e.expl], [[gs.readUInt16LE(va + O.X), gs.readUInt16LE(va + O.Z)]], ORDER_MOVE); // the vent's raw position, as worker_update
      e.explOrdered = tick;
      ctx.say(`Escort at the vent ${e.vx},${e.vz}: the explorer deploys.`);
      return;
    }
    if (shielding) {
      shieldMarch(ctx, units, cx, cz, ex, ez, guardPoint);
      return;
    }
    if (idleAny || tick - e.issued >= REISSUE) {
      // the troopers on the vent; stragglers and reinforcements first to the escort
      const lead = [];
      const behind = [];
      for (const o of units) {
        const [ux, uz] = tileOf(gs, objAddr(o));
        (cheb(ux, uz, cx, cz) <= 6 ? lead : behind).push(o);
      }
      const order = inBase ? ORDER_MOVE : ORDER_ASSAULT;
      if (lead.length) sendWaypointOrder(ctx, lead, [guardPoint], order);
      if (behind.length) sendWaypointOrder(ctx, behind, [[(cx << 8) + 128, (cz << 8) + 128]], order);
      e.issued = tick;
    }
    // the explorer waits (at the HQ) until the escort is nearer to the vent than it is - the second site's trooper may
    // come from afar - then it keeps FOLLOW tiles behind the escort (HOLD_BACK in contact), on its own side
    if (cheb(cx, cz, e.vx, e.vz) + FOLLOW > cheb(ex, ez, e.vx, e.vz)) {
      if (cheb(ex, ez, hx, hz) > 3) moveExplorer(ctx, hx, hz);
      return;
    }
    const back = contact ? HOLD_BACK : FOLLOW;
    let dx = ex - cx;
    let dz = ez - cz;
    if (Math.max(Math.abs(dx), Math.abs(dz)) < 2) {
      dx = hx - cx;
      dz = hz - cz;
    }
    const n = Math.hypot(dx, dz) || 1;
    const tx = Math.min(G.map.w - 1, Math.max(0, Math.round(cx + (dx / n) * back)));
    const tz = Math.min(G.map.h - 1, Math.max(0, Math.round(cz + (dz / n) * back)));
    if (cheb(ex, ez, tx, tz) > 2) moveExplorer(ctx, tx, tz);
    return;
  }
  // arrived: the troopers guard the vent while the explorer deploys
  if (idleAny || tick - e.issued >= REISSUE) {
    const off = units.filter((o) => {
      const [ux, uz] = tileOf(gs, objAddr(o));
      return cheb(ux, uz, gx, gz) > 1 || (ux === e.vx && uz === e.vz);
    });
    if (off.length) sendWaypointOrder(ctx, off, [guardPoint], ORDER_ASSAULT);
    e.issued = tick;
  }
  if ((ex !== e.vx || ez !== e.vz) && (isIdle(gs, ea) || tick - e.explOrdered > REISSUE)) {
    sendWaypointOrder(ctx, [e.expl], [[gs.readUInt16LE(va + O.X), gs.readUInt16LE(va + O.Z)]], ORDER_MOVE);
    e.explOrdered = tick;
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
/**
 * VARIANT upnow: once a mech can be bought, the weapon upgrade (else the armour upgrade) that battle experience asks for
 * (upgradeWanted), bought outright without UPGRADE_RESERVE. Level 1 costs 1000: weapon +25 % damage, armour -20 %
 * damage taken (armour multipliers 256, 204, 170). True when one was bought.
 */
function upgradeDue(ctx) {
  const { G, p } = ctx;
  if (!buyableTroops(G, p).some((it) => TANKS.includes(it.type))) return false;
  return (upgradeWanted(ctx, 0, -1e9) ?? upgradeWanted(ctx, 1, -1e9)) !== null; // wanted, whatever the money
}

function buyUpgrade(ctx) {
  const { G, p } = ctx;
  if (!buyableTroops(G, p).some((it) => TANKS.includes(it.type))) return false;
  const up = upgradeWanted(ctx, 0, 0) ?? upgradeWanted(ctx, 1, 0);
  if (!up) return false;
  spend(G, p, up.cost);
  ctx.emit([build.upgrade(up.which, up.type, up.level, p)]);
  ctx.say(`${up.which ? 'Armour' : 'Weapon'} upgrade ${up.level} for ${G.tables.types[up.type].name.toLowerCase()}.`);
  const x = state(ctx);
  x.stats.upgrades = (x.stats.upgrades ?? 0) + 1;
  return true;
}

/**
 * VARIANT infup: the next level-1 upgrade of the infantry (trooper / grey) in the switch's order - armour first by default
 * (the duels of 9 Oct 2026: armour turns a trooper's 25 damage per shot into 19, 43 shots to kill instead of 32; the
 * weapon upgrade 31, 26 shots - eight armoured troopers beat nine plain ones in 10 of 16 clumps, eight with the weapon
 * upgrade in 4) - once science stands and the first wave was seen; null when none is left or the conditions fail.
 */
function infupNext(ctx) {
  const x = state(ctx);
  const mode = variant(ctx).infup;
  if (!x || !mode || !x.waveSeen) return null;
  const { G, p } = ctx;
  const order = mode === 'armour' ? [1, 0] : mode === 'weapon' ? [0, 1] : mode === 'armouronly' ? [1] : [0];
  for (const which of order) {
    const up = buyableUpgrades(G, p, which).find((u) => INFANTRY.includes(u.type) && u.level === 1);
    if (up) return up;
  }
  return null;
}

const infupDue = (ctx) => infupNext(ctx) !== null;

function buyInfup(ctx) {
  const { G, p } = ctx;
  const up = infupNext(ctx);
  if (!up || money(G, p) < up.cost) return false;
  spend(G, p, up.cost);
  ctx.emit([build.upgrade(up.which, up.type, up.level, p)]);
  ctx.say(`${up.which ? 'Armour' : 'Weapon'} upgrade ${up.level} for ${G.tables.types[up.type].name.toLowerCase()} (infantry).`);
  const x = state(ctx);
  x.stats.infups = (x.stats.infups ?? 0) + 1;
  x.stats[`infup${up.which}At`] = ctx.tick | 0;
  return true;
}

export function upgradeWanted(ctx, which, reserve = UPGRADE_RESERVE) {
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
  if (money(G, p) < pick.cost + reserve) return null;
  return pick;
}

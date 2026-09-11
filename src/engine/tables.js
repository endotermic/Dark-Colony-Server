// Balance tables of Classic dc16.exe (loader.c / depend.c / pervasve.c) as per-game mutable records.
//
// PORT NOTES (read from dc16.asm on 11 Sep 2026; text parsing lives in tools/gamestat2json.js)
//
// loadTables(json) rebuilds, from data/classic/gamestat.json, exactly what the six loaders leave in
// memory after start-up:
//   0x43B6EC weapstat -> weapon_types[80]   72-byte records at 0x50E678
//   0x43BB80 gamestat -> object_types[130] 280-byte records at 0x50FCF8, count at 0x518B34
//   0x43B150 mbullet  -> magic_bullet { num_armours 0x518B28, num_weapons 0x518B2C, rows 0x518B30 } int16
//   0x43B424 boomstat -> boom_types[15]   136-byte records at 0x50DE80
//   0x4379C0 depend   -> depend_items[110] 52-byte records at 0x5049F0, count at 0x506048
//   0x438718 unitid   -> lookup[80]        16-byte rows at 0x5044F0 (depend.c, MAX_LOOKUP = 80)
//   0x454AA0 pervasve.c: building_count 0x48C190, pervasive_types[16] at 0x5360B4 (filled by gamestat)
// The arrays are static (zero-initialised) in the exe, so an index the file does not define is a
// zeroed record here as well, and record index == original array index (weapon number, object type
// id, boom index, depend item id). Every record is a fresh plain object per loadTables() call: the
// engine mutates weaponLevel[]/armourLevel[] (command 0x0C, 0x41CBD4) and swaps weapon slots for
// specials, so tables must not be shared between games.
//
// Computed fields (instruction-exact):
//   weapon.maxFlight  +0x18 = idiv((((range << 8) + 0x400) * 2 + 1), 2 * speed) + 1     (0x43BA01)
//                     speed 0 would be an x86 #DE in the exe; loadTables throws instead.
//   type.armour[0]    +0x24 = 25600 / 100 = 256; armour[1..2] = idiv(25600, col9|col10)   (0x43BE12)
//   type.rallyBonus   +0xFC = idiv(col26 << 8, 100)                                        (0x43BDDD)
//   type.scenery      +0x00 = (col32 != 0) ? 1 : 0;   type.hasSpecial +0x10C = (col29 != 0) ? 1 : 0
//   type.fly          +0x60 = (uint8) col13
//   type.pervasive    +0x78 = col17 != 0 ? ++building_count : 0  (1-based slot; pervasive_types[slot] = type;
//                     assert building_count < 16 "building_count<MAX_PERVASIVE")            (0x43BDFB)
//   type.weaponLevel[8] +0x30 / armourLevel[8] +0x38 = 0 (bytes)
//   boom.blast/scatter  = (int16) idiv(pct << 8, 100)                                      (0x43B630/0x43B67D)
//   mbullet.rows[w][a]  = (int16) trunc(double(pct) * 0.01 * 256): strtod, fmul 0.01 (0x486610), fstp/fld,
//                     fmul 256.0 (0x486618), then 0x42B5B0 = frndint with control word 0x1F?? = ROUND
//                     TOWARD ZERO, fistp. docs/DC16_BATTLE_ENGINE.md §2.3 says "round(pct*0.01*256)"
//                     -- it is truncation: 7 % -> 17 (not 18), 33 % -> 84, 12 % -> 30, 164 % -> 419.
//                     For integer percentages the x87 product is exact before the single rounding to
//                     double, so JS `pct * 0.01` reproduces it bit for bit.
//
// Sprite-derived fields (NOT filled here, see SPRITE_RULES below; anim.js / data/classic/sprites.json):
//   weapon: bullet +0x2C, explode[4] +0x30..+0x3C, numExplode +0x40 (the part that comes from the
//           blast type's BOOMSTAT sprite names IS applied here, because it is table data)
//   type:   bbox +0x48..+0x5F, move +0x7C, stand +0x80, scrch +0x84, burn +0x88, fig +0x8C,
//           deploy +0x94, build +0x98, funk +0x9C, fire[3] +0xA0, die[3] +0xAC, blood[7] +0xBC,
//           numBlood +0xD8, numFire +0xE4, numDie +0xE8
//   boom:   sprites[4] +0x00 (names are known: they are literal in BOOMSTAT.TXT)
//   Sprite fields hold the sprite NAME (string) when the loader resolves it unconditionally (STAND,
//   MOVE, boom names) or the fallback the loader would use when no dedicated sprite exists; the
//   sprite agent must apply SPRITE_RULES with the real sprite inventory to get the exact values.
//
// Disagreements with docs/DC16_BATTLE_ENGINE.md §2 (the doc is otherwise confirmed):
//   * §2.3 rounding: truncation, not round-to-nearest (see above).
//   * §2.1 "lines shorter than 4 characters are skipped": the files are CRLF and are opened "rb", so
//     the 4 includes "\r\n" (visible length <= 1 is skipped); gamestat and boomstat use 3, not 4.
//   * §2.2 lists `+0x78` as footprint handle "(inferred)": it is the 1-based pervasve.c slot
//     (0x454AA0), allocated in GAMESTAT order for every type whose column 17 is non-zero (Classic:
//     POOP 37 -> 1, VENT 40 -> 2, T 41 -> 3, XDEPLOY 42 -> 4, EDPLY 47 -> 5, SDPL 48 -> 6).
//   * §2.5 / §15.1 depend deps: the -1 terminator is stored too (`+0x20..` int32[5] incl. -1);
//     0x506048 is the count from line 1 (80), not the highest id + 1 (85).
//   * UNITID.TXT is not mentioned: lookup[] rows are { isUnit, raceSel, type, buttonId } (see
//     lookupUnitId) and feed the build-menu / info button id of objects that have no DEPEND item.

export const NUMWEAPTYPES = 80;
export const NUMOBJTYPES = 130;
export const NUMBLASTTYPES = 15;
export const MAX_DEPEND_ITEMS = 110;
export const MAX_DEPEND = 5;
export const MAX_LOOKUP = 80;
export const MAX_PERVASIVE = 16;

export const WEAPON_SIZE = 0x48;
export const TYPE_SIZE = 0x118;
export const BOOM_SIZE = 0x88;
export const DEPEND_SIZE = 0x34;

const idiv = (a, b) => (a / b) | 0;
const i16 = (v) => ((v & 0xffff) << 16) >> 16;

// ---- weapon_types (WEAPSTAT.TXT) -----------------------------------------------------------------
//
// | field        | offset | type    | source                                                       |
// |--------------|--------|---------|--------------------------------------------------------------|
// | weaponClass  | +0x00  | int32   | col 1 (row of the magic-bullet matrix)                       |
// | sound        | +0x04  | int32   | col 2                                                        |
// | rateOfFire   | +0x08  | int32   | col 3                                                        |
// | damage       | +0x0C  | int32   | col 4                                                        |
// | speed        | +0x10  | int32   | col 5 (position units per tick)                              |
// | range        | +0x14  | int32   | col 6 (tiles)                                                |
// | maxFlight    | +0x18  | int32   | computed (see PORT NOTES)                                    |
// | blast        | +0x1C  | int32   | col 7 (boom_types index)                                     |
// | shots        | +0x20  | int32   | col 8                                                        |
// | reload       | +0x24  | int32   | col 9                                                        |
// | oneShot      | +0x28  | uint8   | col 11                                                       |
// | bullet       | +0x2C  | sprite* | SPRITE: <name>BULLET if <name>BULLET0 exists, else 0         |
// | explode[4]   | +0x30  | sprite* | SPRITE / boom sprites (see PORT NOTES)                       |
// | numExplode   | +0x40  | int32   | 0, 1 (own EXPLODE/EXPL) or the blast type's sprite count      |
// | kind         | +0x44  | uint8   | col 10 (missile kind, §7.2)                                  |

function zeroWeapon(index) {
  return {
    index, name: '', weaponClass: 0, sound: 0, rateOfFire: 0, damage: 0, speed: 0, range: 0, maxFlight: 0,
    blast: 0, shots: 0, reload: 0, oneShot: 0, bullet: null, explode: [null, null, null, null], numExplode: 0,
    kind: 0, loaded: false,
  };
}

function loadWeapons(json, mbullet, booms) {
  const weapons = [];
  for (let i = 0; i < NUMWEAPTYPES; i++) weapons.push(zeroWeapon(i));
  for (const r of json.records) {
    if (!(r.index >= 0 && r.index < NUMWEAPTYPES)) throw new Error(`weapon ${r.index}: number >= 0 && number < NUMWEAPTYPES`);
    const c = (k) => r.cols[k] ?? 0; // a %d that failed leaves the (zeroed static) field alone
    const w = weapons[r.index];
    w.loaded = true;
    w.name = r.name;
    w.weaponClass = c(0);
    w.sound = c(1);
    w.rateOfFire = c(2);
    w.damage = c(3);
    w.speed = c(4);
    w.range = c(5);
    w.blast = c(6);
    w.shots = c(7);
    w.reload = c(8);
    w.kind = c(9) & 0xff; // mov byte ptr [esi+44h],al
    w.oneShot = c(10) & 0xff; // mov byte ptr [esi+28h],al
    if (!(w.weaponClass >= 0 && w.weaponClass < mbullet.numWeapons)) {
      throw new Error(`weapon ${r.index}: wt->weapon_class >= 0 && wt->weapon_class < magic_bullet.num_weapons`);
    }
    // 0x43B91B..0x43B9C4: bullet / EXPLODE / EXPL are sprite-inventory dependent (SPRITE_RULES).
    // 0x43B9C4: blast > 0 -> copy the boom's leading non-null sprites; a non-zero count wins.
    if (w.blast > 0) {
      const boom = booms[w.blast];
      let n = 0;
      while (n < 4 && boom.sprites[n] !== null) {
        w.explode[n] = boom.sprites[n];
        n++;
      }
      if (n > 0) w.numExplode = n;
    }
    // 0x43BA01: maximum flight ticks
    if (w.speed === 0) throw new Error(`weapon ${r.index}: speed 0 (idiv by zero in the exe)`);
    w.maxFlight = idiv(((w.range << 8) + 0x400) * 2 + 1, 2 * w.speed) + 1;
  }
  return weapons;
}

// ---- object_types (GAMESTAT.TXT) -----------------------------------------------------------------
//
// | field          | offset | type      | source                                                     |
// |----------------|--------|-----------|------------------------------------------------------------|
// | scenery        | +0x00  | uint8     | col 32 != 0                                                |
// | race           | +0x04  | int32     | col 1 (0 human, 1 alien, -1 neutral)                       |
// | turnSpeed      | +0x08  | int32     | col 2                                                      |
// | speed          | +0x0C  | int32     | col 3                                                      |
// | visionNight    | +0x10  | int32     | col 5 (ObsN)                                               |
// | visionDay      | +0x14  | int32     | col 4 (ObsD)                                               |
// | weapon[3]      | +0x18  | int32[3]  | cols 6,7,8 (weapon index per upgrade level, -1 unarmed)    |
// | armour[3]      | +0x24  | int32[3]  | 256, idiv(25600, col 9), idiv(25600, col 10)               |
// | weaponLevel[8] | +0x30  | uint8[8]  | 0 (per player, MUTABLE)                                    |
// | armourLevel[8] | +0x38  | uint8[8]  | 0 (per player, MUTABLE)                                    |
// | defenceClass   | +0x40  | int32     | col 11 (column of the magic-bullet matrix)                 |
// | hp             | +0x44  | int32     | col 12                                                     |
// | bbox           | +0x48  | int32[6]  | SPRITE: minX +0x48, minY +0x4C, maxX +0x50, maxY +0x54,     |
// |                |        |           |   hotX +0x58, hotY +0x5C (see SPRITE_RULES.bbox)           |
// | fly            | +0x60  | uint8     | col 13                                                     |
// | signature      | +0x64  | int32     | col 14                                                     |
// | hidden         | +0x68  | int32     | col 15                                                     |
// | commando       | +0x6C  | int32     | col 16                                                     |
// | healer         | +0x70  | int32     | col 18                                                     |
// | healer2        | +0x74  | int32     | col 19                                                     |
// | pervasive      | +0x78  | int32     | col 17 != 0 ? pervasve.c slot (1..15) : 0                  |
// | move           | +0x7C  | sprite*   | <name>MOVE when speed != 0, else untouched (0)             |
// | stand          | +0x80  | sprite*   | <name>STAND                                                |
// | scrch          | +0x84  | sprite*   | SPRITE: <name>SCRCH, fallback STAND                        |
// | burn           | +0x88  | sprite*   | SPRITE: <name>BURN, fallback SCRCH (or STAND)              |
// | fig            | +0x8C  | sprite*   | SPRITE: <name>FIG, fallback STAND                          |
// | (unused)       | +0x90  | -         | never written by the loader                                |
// | deploy         | +0x94  | sprite*   | SPRITE: <name>DEPLOY, fallback STAND                       |
// | build          | +0x98  | sprite*   | SPRITE: <name>BUILD, else <name>BUILDSTAND, else 0         |
// | funk           | +0x9C  | sprite*   | SPRITE: <name>FUNK, fallback STAND                         |
// | fire[3]        | +0xA0  | sprite*[3]| SPRITE: FIRE/FIREA, FIREB, FIREC; fallback [STAND]          |
// | die[3]         | +0xAC  | sprite*[3]| SPRITE: DIE or DIEA, DIEB, DIEC; fallback [STAND]           |
// | (unused)       | +0xB8  | -         | never written by the loader                                |
// | blood[7]       | +0xBC  | sprite*[7]| SPRITE: <name>BLOODA..BLOODG (consecutive from A)          |
// | numBlood       | +0xD8  | int32     | SPRITE: number of blood sprites                            |
// | standStill     | +0xDC  | int32     | col 20                                                     |
// | colour         | +0xE0  | int32     | col 22                                                     |
// | numFire        | +0xE4  | int32     | SPRITE: 1..3 (1 with the STAND fallback)                   |
// | numDie         | +0xE8  | int32     | SPRITE: 1..3 (1 with the STAND fallback)                   |
// | prodClass      | +0xEC  | int32     | col 21 (production queue class)                            |
// | flyer          | +0xF0  | int32     | col 23                                                     |
// | rankCode       | +0xF4  | int32     | col 24                                                     |
// | chargeRegen    | +0xF8  | int32     | col 25                                                     |
// | rallyBonus     | +0xFC  | int32     | idiv(col 26 << 8, 100)                                     |
// | rallySize      | +0x100 | int32     | col 27                                                     |
// | deployCode     | +0x104 | int32     | col 28                                                     |
// | specialParam   | +0x108 | int32     | col 29                                                     |
// | hasSpecial     | +0x10C | uint8     | col 29 != 0                                                |
// | specialWeapon  | +0x110 | int32     | col 30                                                     |
// | counterpart    | +0x114 | int32     | col 31                                                     |

function zeroBbox() {
  return { minX: 0, minY: 0, maxX: 0, maxY: 0, hotX: 0, hotY: 0 };
}

function zeroType(index) {
  return {
    index, name: '', scenery: 0, race: 0, turnSpeed: 0, speed: 0, visionNight: 0, visionDay: 0,
    weapon: [0, 0, 0], armour: [0, 0, 0], weaponLevel: new Uint8Array(8), armourLevel: new Uint8Array(8),
    defenceClass: 0, hp: 0, bbox: zeroBbox(), fly: 0, signature: 0, hidden: 0, commando: 0, healer: 0, healer2: 0,
    pervasive: 0, move: null, stand: null, scrch: null, burn: null, fig: null, deploy: null, build: null, funk: null,
    fire: [null, null, null], die: [null, null, null], blood: [null, null, null, null, null, null, null], numBlood: 0,
    standStill: 0, colour: 0, numFire: 0, numDie: 0, prodClass: 0, flyer: 0, rankCode: 0, chargeRegen: 0,
    rallyBonus: 0, rallySize: 0, deployCode: 0, specialParam: 0, hasSpecial: 0, specialWeapon: 0, counterpart: 0,
    loaded: false,
  };
}

function loadTypes(json, mbullet) {
  if (!(json.count > 0 && json.count <= NUMOBJTYPES)) throw new Error('objects<=NUMOBJTYPES && objects>0');
  const types = [];
  for (let i = 0; i < NUMOBJTYPES; i++) types.push(zeroType(i));
  const pervasive = { count: 0, types: new Int32Array(MAX_PERVASIVE) }; // 0x48C190 (reset by 0x454A90), 0x5360B4
  for (const r of json.records) {
    const t = types[r.index];
    const c = (k) => r.cols[k - 1] ?? 0; // k = file column 1..32
    t.loaded = true;
    t.name = r.name;
    t.race = c(1);
    t.turnSpeed = c(2);
    t.speed = c(3);
    t.visionDay = c(4);
    t.visionNight = c(5);
    t.weapon = [c(6), c(7), c(8)];
    t.armour = [100, c(9), c(10)];
    t.defenceClass = c(11);
    t.hp = c(12);
    t.fly = c(13) & 0xff;
    t.signature = c(14);
    t.hidden = c(15);
    t.commando = c(16);
    t.healer = c(18);
    t.healer2 = c(19);
    t.standStill = c(20);
    t.prodClass = c(21);
    t.colour = c(22);
    t.flyer = c(23);
    t.rankCode = c(24);
    t.chargeRegen = c(25);
    t.rallyBonus = c(26);
    t.rallySize = c(27);
    t.deployCode = c(28);
    t.specialParam = c(29);
    t.specialWeapon = c(30);
    t.counterpart = c(31);
    // 0x43BD9D..: post-processing in the loader's order
    t.hasSpecial = t.specialParam !== 0 ? 1 : 0;
    t.scenery = c(32) !== 0 ? 1 : 0;
    t.rallyBonus = idiv(t.rallyBonus << 8, 100);
    if (c(17) !== 0) {
      // 0x454AA0(type): building_count++ (pre-increment), assert < MAX_PERVASIVE, slot -> type
      pervasive.count++;
      if (!(pervasive.count < MAX_PERVASIVE)) throw new Error('building_count<MAX_PERVASIVE');
      pervasive.types[pervasive.count] = r.index;
      t.pervasive = pervasive.count;
    }
    for (let k = 0; k < 3; k++) {
      if (t.armour[k] === 0) throw new Error(`type ${r.index}: armour column ${k} is 0 (idiv by zero in the exe)`);
      t.armour[k] = idiv(25600, t.armour[k]);
    }
    if (!(t.defenceClass >= 0 && t.defenceClass < mbullet.numArmours)) {
      throw new Error(`type ${r.index}: otp->defence_class>=0 && otp->defence_class<magic_bullet.num_armours`);
    }
    // sprites: unconditional names + the fallbacks the loader uses when nothing dedicated exists
    if (t.speed !== 0) t.move = `${t.name}MOVE`;
    t.stand = `${t.name}STAND`;
    t.scrch = t.stand;
    t.burn = t.stand;
    t.fig = t.stand;
    t.deploy = t.stand;
    t.build = null;
    t.funk = t.stand;
    t.fire = [t.stand, null, null];
    t.numFire = 1;
    t.die = [t.stand, null, null];
    t.numDie = 1;
    t.numBlood = 0;
  }
  return { types, pervasive };
}

// ---- magic_bullet (MBULLET.TXT) ------------------------------------------------------------------

/** One matrix entry: strtod -> * 0.01 -> to double -> * 256 -> truncate toward zero -> int16. */
export function mbulletEntry(pct) {
  const d = pct * 0.01; // fmul qword ptr [0x486610]; fstp qword ptr [ebp-18h]
  return i16(Math.trunc(d * 256)); // fmul qword ptr [0x486618]; frndint (RC = truncate); fistp; mov word ptr
}

function loadMbullet(json) {
  const { numArmours, numWeapons } = json;
  if (!(numArmours > 0)) throw new Error('magic_bullet.num_armours>0');
  if (!(numWeapons > 0)) throw new Error('magic_bullet.num_weapons>0');
  const rows = json.rows.map((row) => row.map(mbulletEntry));
  return { numArmours, numWeapons, rows };
}

// ---- boom_types (BOOMSTAT.TXT) -------------------------------------------------------------------
//
// | field       | offset | type       | source                                                  |
// |-------------|--------|------------|---------------------------------------------------------|
// | sprites[4]  | +0x00  | sprite*[4] | sprite names until NONE (0-terminated when < 4)         |
// | size        | +0x10  | uint8      | `index size` line                                       |
// | blast[7][7] | +0x12  | int16      | (pct << 8) / 100, row stride 14 bytes, [row][col]       |
// | scatter[3][3]| +0x74 | int16      | (pct << 8) / 100, row stride 6 bytes                    |

function zeroBoom(index) {
  return {
    index, sprites: [null, null, null, null], size: 0,
    blast: Array.from({ length: 7 }, () => [0, 0, 0, 0, 0, 0, 0]),
    scatter: [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
    loaded: false,
  };
}

const pctFixed = (pct) => i16(idiv(pct << 8, 100));

function loadBooms(json) {
  if (!(json.count > 0 && json.count < NUMBLASTTYPES)) throw new Error('booms < NUMBLASTTYPES && booms > 0');
  const booms = [];
  for (let i = 0; i < NUMBLASTTYPES; i++) booms.push(zeroBoom(i));
  for (const r of json.records) {
    if (!(r.index >= 0 && r.index < NUMBLASTTYPES)) throw new Error(`boom ${r.index}: index out of range`);
    if (!(r.size >= 1)) throw new Error(`boom ${r.index}: size >= 1`);
    if (r.size > 7) throw new Error(`boom ${r.index}: size ${r.size} overflows the 7x7 blast matrix`);
    const b = booms[r.index];
    b.loaded = true;
    b.size = r.size & 0xff;
    for (let k = 0; k < 4; k++) b.sprites[k] = k < r.sprites.length ? r.sprites[k] : null;
    for (let row = 0; row < r.size; row++) {
      for (let col = 0; col < r.size; col++) b.blast[row][col] = pctFixed(r.blast[row][col]);
    }
    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 3; col++) b.scatter[row][col] = pctFixed(r.scatter[row][col]);
    }
  }
  return booms;
}

// ---- depend_items (DEPEND.TXT) -------------------------------------------------------------------
//
// | field    | offset | type     | source                                                          |
// |----------|--------|----------|-----------------------------------------------------------------|
// | defined  | +0x00  | uint8    | 1 for every id in the file (all 110 cleared first)              |
// | status   | +0x04  | int32    | 0 (dep_recompute 0x437D00 sets 0 done / 1 available / 2 blocked)|
// | cost     | +0x08  | int32    | col 2                                                           |
// | button   | +0x0C  | int32    | col 3 (interface button id)                                     |
// | kind     | +0x10  | int32    | col 4 (0 building, 1 troop, 2 upgrade)                          |
// | params[3]| +0x14  | int32[3] | kind 0/2: three values, kind 1: one value (+0x18/+0x1C stay 0)  |
// | deps[5]  | +0x20  | int32[5] | dependency ids INCLUDING the -1 terminator, rest 0              |

function zeroDepend(index) {
  return { index, defined: 0, status: 0, cost: 0, button: 0, kind: 0, params: [0, 0, 0], deps: [0, 0, 0, 0, 0] };
}

function loadDepend(json) {
  if (!(json.count > 0 && json.count <= MAX_DEPEND_ITEMS)) throw new Error('depends<=MAX_DEPEND_ITEM');
  const items = [];
  for (let i = 0; i < MAX_DEPEND_ITEMS; i++) items.push(zeroDepend(i));
  for (const r of json.records) {
    if (!(r.index < MAX_DEPEND_ITEMS)) throw new Error('number<MAX_DEPEND_ITEMS');
    if (r.index < 0) throw new Error(`depend item ${r.index}: negative id (the exe would write before the array)`);
    const d = items[r.index];
    d.defined = 1;
    d.status = 0;
    d.cost = r.cost;
    d.button = r.button;
    d.kind = r.kind;
    if (r.kind === 0 || r.kind === 2) d.params = [r.params[0], r.params[1], r.params[2]];
    else if (r.kind === 1) d.params[0] = r.params[0];
    else throw new Error(`depend item ${r.index}: bad kind ${r.kind}`);
    if (r.deps.length > MAX_DEPEND) throw new Error(`depend item ${r.index}: j<MAX_DEPEND`);
    for (let k = 0; k < r.deps.length; k++) d.deps[k] = r.deps[k];
  }
  return { count: json.count, items };
}

// ---- lookup (UNITID.TXT, depend.c) ---------------------------------------------------------------
//
// | field   | offset | type  | source                                                              |
// |---------|--------|-------|---------------------------------------------------------------------|
// | isUnit  | +0x00  | int32 | col 1: 1 = object index >= 152 (unit), 0 = city slot (building)      |
// | raceSel | +0x04  | int32 | col 2: -1 any race; else compared with (player race ^ 1): 1 = human |
// | type    | +0x08  | int32 | col 3: object type id                                               |
// | id      | +0x0C  | int32 | col 4: interface button id (same number space as DEPEND column 3)   |
// All 80 rows start as -1; a row whose four values are all negative ends the file.

function loadUnitid(json) {
  const rows = [];
  for (let i = 0; i < MAX_LOOKUP; i++) rows.push({ isUnit: -1, raceSel: -1, type: -1, id: -1 });
  json.rows.forEach((r, i) => {
    if (i >= MAX_LOOKUP - 1) throw new Error('i<MAX_LOOKUP-1');
    rows[i] = { isUnit: r[0], raceSel: r[1], type: r[2], id: r[3] };
  });
  return { count: json.rows.length, rows };
}

/**
 * 0x43864C(type, objIndex, race) -> button id or -1. Used by the selected-object info / build-menu
 * code (0x4377DC) after 0x4385B0 found no DEPEND item for the type. UI only, not part of the
 * simulation; ported because it is the only reader of the UNITID table.
 */
export function lookupUnitId(unitid, type, objIndex, race) {
  const isUnit = objIndex >= 0x98 ? 1 : 0;
  const sel = (race & ~0xff) | ((race & 0xff) ^ 1); // xor ah,1 on the low byte only
  for (let i = 0; ; i++) {
    const row = unitid.rows[i];
    if (row === undefined || row.type === -1) return -1;
    if (i === MAX_LOOKUP) throw new Error('count!=MAX_LOOKUP');
    if (row.type === type && row.isUnit === isUnit && (row.raceSel === -1 || row.raceSel === sel)) return row.id;
  }
}

// ---- sprite rules for the sprite agent (anim.js) ---------------------------------------------------
//
// exists(name, suffix)    = 0x425464: the sprite bank "<name><suffix>" exists
// existsAny(name, suffix) = 0x43BB30: exists(name, `${suffix}${k}`) for some even k in 0..30 ("%s%d")
// load(name, suffix)      = 0x426540: the bank "<name><suffix>" (fatal if missing)
export const SPRITE_RULES = Object.freeze({
  weapon: {
    // 0x43B91B: bullet = exists(name,'BULLET0') ? load(name,'BULLET') : 0
    bullet: { offset: 0x2c, exists: 'BULLET0', load: 'BULLET' },
    // 0x43B959: numExplode = 0; EXPLODE0 -> explode[0] = EXPLODE, numExplode = 1; else EXPL0 -> EXPL, 1.
    // Afterwards (0x43B9C4) a blast type with sprites overwrites explode[0..n-1] and numExplode = n;
    // loadTables already applied that part, so only weapons with numExplode === 0 need this check.
    explode: { offset: 0x30, countOffset: 0x40, candidates: [{ exists: 'EXPLODE0', load: 'EXPLODE' }, { exists: 'EXPL0', load: 'EXPL' }] },
  },
  type: {
    // 0x43BEED: move = speed != 0 ? load(name,'MOVE') : untouched; 0x43BF1C: stand = load(name,'STAND')
    // 0x43BF45: bbox[0..3] = 0x425FB4(stand) (16 bytes: minX, minY, maxX, maxY);
    //           0x426304(stand.frame0, 6, &pt) == 1 ? (hotX = pt.x, hotY = -pt.y) : (hotX = 0, hotY = -(maxY >> 3));
    //           fly == 0 -> minX = min(minX, -96), maxX = max(maxX, 96), minY = min(minY, -96), maxY = max(maxY, 96)
    bbox: { offset: 0x48, from: 'STAND', hotspot: 6, groundWiden: 96 },
    // 0x43BA3C: fire: exists FIRE0 -> fire[0] = FIRE, n = 1; exists FIREA0 -> fire[0] = FIREA, n = 1;
    //           exists FIREB0 -> fire[++n - 1] = FIREB ... FIREC0 likewise; n == 0 -> fire[0] = stand, n = 1; numFire = n
    fire: { offset: 0xa0, countOffset: 0xe4, direct: ['FIRE', 'FIREA'], extra: ['FIREB', 'FIREC'], fallback: 'STAND' },
    // 0x43C009: existsAny DIE -> die[0] = DIE, numDie = 1; else existsAny DIEA -> die[0] = DIEA, numDie = 1,
    //           then DIEB -> die[1], numDie = 2, then DIEC -> die[2], numDie = 3 (each step requires the previous);
    //           neither -> die[0] = stand, numDie = 1
    die: { offset: 0xac, countOffset: 0xe8, single: 'DIE', chain: ['DIEA', 'DIEB', 'DIEC'], fallback: 'STAND' },
    deploy: { offset: 0x94, existsAny: 'DEPLOY', fallback: 'STAND' }, // 0x43C15C
    funk: { offset: 0x9c, existsAny: 'FUNK', fallback: 'STAND' }, // 0x43C1AD
    fig: { offset: 0x8c, existsAny: 'FIG', fallback: 'STAND' }, // 0x43C1FE
    build: { offset: 0x98, existsAny: ['BUILD', 'BUILDSTAND'], fallback: null }, // 0x43C24F: first of the two, else 0
    // 0x43C2DA: existsAny SCRCH -> scrch = SCRCH, burn = existsAny BURN ? BURN : scrch; else scrch = burn = stand
    scrch: { offset: 0x84, existsAny: 'SCRCH', fallback: 'STAND' },
    burn: { offset: 0x88, existsAny: 'BURN', fallback: 'SCRCH' },
    // 0x43C3B5: for c in 'A'..'G': existsAny `BLOOD${c}` -> blood[numBlood++] = load(name, `BLOOD${c}`) (gaps allowed)
    blood: { offset: 0xbc, countOffset: 0xd8, suffixes: ['BLOODA', 'BLOODB', 'BLOODC', 'BLOODD', 'BLOODE', 'BLOODF', 'BLOODG'] },
  },
});

// ---- entry point -------------------------------------------------------------------------------------

/**
 * Build fresh, mutable per-game tables from data/classic/gamestat.json (tools/gamestat2json.js).
 * Loader order of the driver 0x43C44C: booms, mbullet, depend, gamestat, unitid, weapstat.
 */
export function loadTables(json) {
  if (json.format !== 'dc16-gamestat') throw new Error(`unexpected table format ${json.format}`);
  const booms = loadBooms(json.booms);
  const mbullet = loadMbullet(json.mbullet);
  const depend = loadDepend(json.depend);
  const { types, pervasive } = loadTypes(json.types, mbullet);
  const unitid = loadUnitid(json.unitid);
  const weapons = loadWeapons(json.weapons, mbullet, booms);
  return {
    weapons,
    types,
    numTypes: json.types.count, // 0x518B34
    mbullet,
    booms,
    depend: depend.items,
    numDepend: depend.count, // 0x506048
    unitid,
    pervasive,
  };
}

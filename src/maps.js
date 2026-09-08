// The multiplayer maps that ship with Classic Dark Colony (SCENARIO/MPLAYER/*.SCN). Each .SCN file
// starts with the terrain file, the base name and the display name; this table was generated from
// the 56 files in the Dark-Colony repository (7 Sep 2026). The player count is the 2nd character of
// the file name (F15/F22). The game's own host shows exactly these names in the lobby title.

const TABLE = [
  ["A2PLAY01.SCN", "Entrails from the Crypt", "atlantis"],
  ["D2PLAY01.SCN", "Dead Man's Wharf", "desert"],
  ["D2PLAY02.SCN", "The Rennat Maze", "desert"],
  ["D2PLAY03.SCN", "Sergei's Inlet", "desert"],
  ["D2PLAY04.SCN", "No-man's Land", "desert"],
  ["D2PLAY05.SCN", "Near..Far", "desert"],
  ["D2PLAY06.SCN", "Kiss o' Death", "desert"],
  ["D2PLAY07.SCN", "Hide and Go", "desert"],
  ["D2PLAY08.SCN", "Sneak Attack", "desert"],
  ["D2PLAY09.SCN", "Stairway to Hell", "desert"],
  ["D2PLAY10.SCN", "Treasure Rift", "desert"],
  ["D4PLAY01.SCN", "Four Corners", "desert"],
  ["D4PLAY02.SCN", "Monkey in the Middle", "desert"],
  ["D4PLAY03.SCN", "Napalm Valley", "desert"],
  ["D4PLAY04.SCN", "Island Petra Madness", "desert"],
  ["D4PLAY05.SCN", "Martian Bridges", "desert"],
  ["D4PLAY06.SCN", "The Crossroads", "desert"],
  ["D4PLAY07.SCN", "Hell's Bucket", "desert"],
  ["D4PLAY08.SCN", "Beon Bay", "desert"],
  ["D4PLAY09.SCN", "Bloody Cross", "desert"],
  ["D4PLAY10.SCN", "River Rage", "desert"],
  ["D8PLAY01.SCN", "Armageddon", "desert"],
  ["D8PLAY02.SCN", "Black Widow", "desert"],
  ["D8PLAY03.SCN", "Circle of Friends", "desert"],
  ["D8PLAY04.SCN", "Close Encounters", "desert"],
  ["D8PLAY05.SCN", "Olympus Mons", "desert"],
  ["D8PLAY06.SCN", "Big Crater", "desert"],
  ["D8PLAY07.SCN", "Lost Treasure", "desert"],
  ["D8PLAY08.SCN", "New Mississippi", "desert"],
  ["D8PLAY09.SCN", "Nowhere to Run", "desert"],
  ["D8PLAY10.SCN", "Crater Valley", "desert"],
  ["J2PLAY01.SCN", "Pond Thing", "jungle"],
  ["J2PLAY02.SCN", "Rough in the Jungle", "jungle"],
  ["J2PLAY03.SCN", "The Split", "jungle"],
  ["J2PLAY04.SCN", "Death's Ribcage", "jungle"],
  ["J2PLAY05.SCN", "Bottlenecks", "jungle"],
  ["J2PLAY06.SCN", "Hidden Rift", "jungle"],
  ["J2PLAY07.SCN", "Mine Fields", "jungle"],
  ["J2PLAY08.SCN", "Sideswiped", "jungle"],
  ["J2PLAY09.SCN", "Martian Zoo", "jungle"],
  ["J4PLAY01.SCN", "4 Kingdoms", "jungle"],
  ["J4PLAY02.SCN", "Jungle Joe", "jungle"],
  ["J4PLAY03.SCN", "Green Achers", "jungle"],
  ["J4PLAY04.SCN", "Nowhere to Walk", "jungle"],
  ["J4PLAY05.SCN", "Checkers", "jungle"],
  ["J4PLAY06.SCN", "Inside Corner", "jungle"],
  ["J4PLAY07.SCN", "The Rice Fields", "jungle"],
  ["J6PLAY01.SCN", "Martian Mansion", "jungle"],
  ["J6PLAY02.SCN", "Martian Mayhem", "jungle"],
  ["J8PLAY01.SCN", "Plink - O", "jungle"],
  ["J8PLAY02.SCN", "Hoops of Fury", "jungle"],
  ["J8PLAY03.SCN", "Scars and Stripes", "jungle"],
  ["J8PLAY04.SCN", "Hole in the Middle", "jungle"],
  ["J8PLAY05.SCN", "The Crossroads II", "jungle"],
  ["J8PLAY06.SCN", "River Rush", "jungle"],
  ["J8PLAY07.SCN", "Rings of fire", "jungle"],
];

/** file (upper case, with .SCN) -> { file, name, terrain, players } */
export const MAPS = new Map(TABLE.map(([file, name, terrain]) => [file, { file, name, terrain, players: Number(file[1]) }]));

/**
 * Resolve one ROOMS entry: "D8PLAY01", "d8play01.scn", or "FILE:Name[:terrain]" for a map that is
 * not in the table. Returns { file, name, terrain, players }.
 */
export function resolveMap(spec) {
  const parts = String(spec).split(':').map((s) => s.trim());
  let file = parts[0].toUpperCase();
  if (!file.endsWith('.SCN')) file += '.SCN';
  if (file.length < 2 || !/[1-8]/.test(file[1])) {
    throw new Error(`ROOMS entry "${spec}": the 2nd character of the file name must be the player count (1-8)`);
  }
  const known = MAPS.get(file);
  const name = parts[1] || known?.name;
  if (!name) throw new Error(`ROOMS entry "${spec}": unknown map, give it a name as FILE:Name[:terrain]`);
  if (name.length > 42) throw new Error(`ROOMS entry "${spec}": the map name must be at most 42 characters`);
  const terrain = (parts[2] || known?.terrain || 'desert').toLowerCase();
  return { file, name, terrain, players: Number(file[1]) };
}

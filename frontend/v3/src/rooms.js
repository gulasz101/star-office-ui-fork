// Room types (SOU-195): which room an agent gets, as plain data. Rooms are Tiled maps in
// maps/rooms/<type>.json built by scripts/v3/build_rooms.py.

export const ROOM_TYPES = ['garage', 'serverroom', 'factory', 'mystery', 'tokenden', 'career', 'writer'];

export const ROOM_NAMES = {
  garage: 'garage studio', serverroom: 'server room', factory: 'factory floor', mystery: 'mystery cabin',
  tokenden: 'token den', career: 'career office', writer: "writer's loft",
};

// Unknown agents are "dummy workers" and share the universal factory look.
export const FALLBACK_ROOM = 'factory';

// Room personality per agent (SOU-294) comes from the API, never from this repo: each
// /events/agents or /public/agents entry carries `room: {type, hoodie}`, set when the
// agent was registered (backend/tokens.py). Keyed by lowercased name, like the backend.
const personalities = new Map();
const keyOf = (agent) => String(agent || '').trim().toLowerCase();

const validHoodie = (h) =>
  h && typeof h === 'object' && ['h', 's', 'v'].every((k) => Number.isFinite(h[k])) ? { h: h.h, s: h.s, v: h.v } : null;

export function setPersonality(agent, room) {
  if (!keyOf(agent)) return;
  const type = room && ROOM_TYPES.includes(room.type) ? room.type : null;
  personalities.set(keyOf(agent), { type, hoodie: validHoodie(room && room.hoodie) });
}

// every entry of an agents read; entries without a name are skipped
export function setPersonalities(entries) {
  for (const e of Array.isArray(entries) ? entries : []) if (e && e.agent) setPersonality(e.agent, e.room);
}

export const hasPersonality = (agent) => personalities.has(keyOf(agent));
export const clearPersonalities = () => personalities.clear();

export function roomTypeFor(agent) {
  const p = personalities.get(keyOf(agent));
  return (p && p.type) || FALLBACK_ROOM;
}

// Prop art: map object name -> served file. Names missing here (or files that fail to load)
// get a placeholder box, drawn ones (belt) are animated by the renderer.
export const PROP_ART = Object.fromEntries(
  ['desk', 'chair', 'rack', 'bookshelf', 'coffee', 'rug', 'couch', 'bass', 'amp', 'cat', 'van', 'spool', 'crate']
    .map((n) => [n, `assets/office-01/props/${n}.png`])
    .concat(['cat_box', 'rack_v2', 'armchair', 'fireplace', 'woodpile'].map((n) => [n, `assets/lodge/props/${n}.png`]))
    .concat(['gear_shelf_a', 'gear_shelf_b', 'workbench', 'ups_stack', 'cable_coil', 'stool', 'network_cabinet']
      .map((n) => [n, `assets/serverroom/props/${n}.png`]))
    .concat(['control_desk', 'ore_pile', 'item_box'].map((n) => [n, `assets/factory/props/${n}.png`]))
    .concat(Object.entries({
      mystery: ['curiosity_shelf', 'eye_painting', 'crystal_ball', 'journal_stand', 'strange_rug', 'lantern_green'],
      tokenden: ['token_stacks', 'lucky_cat', 'red_lanterns', 'tea_table', 'token_server', 'abacus_desk'],
      career: ['coat_stand', 'whiteboard', 'ring_light', 'advisor_desk', 'framed_certs', 'ficus'],
      writer: ['typewriter_desk', 'paper_stacks', 'paper_bin', 'reading_chair', 'cork_board', 'book_tower', 'paper_stacks_lined'],
      garage: ['van_parked', 'roller_door', 'pegboard', 'toolbox', 'tire_stack', 'oil_drum'],
      serverroom: ['gear_shelf_full_a', 'gear_shelf_full_b'],
    }).flatMap(([set, names]) => names.map((n) => [n, `assets/${set}/props/${n}.png`]))),
);
export const FLAT_PROPS = new Set(['rug', 'belt', 'cable_coil', 'strange_rug']);
// Hung on the back wall (SOU-214): drawn just above the wall tiles, never in front of anything.
export const WALL_DECALS = new Set(['eye_painting', 'red_lanterns', 'framed_certs', 'cork_board', 'roller_door', 'pegboard']);

// Animated props (SOU-213): name -> [file, frame w, frame h, frames, fps]. Loaded as
// spritesheets under prop-<name>; the room plays prop-anim-<name> on them.
export const SHEET_PROPS = {
  belt_h: ['assets/factory/props/belt_h.png', 32, 32, 4, 10],
  assembler: ['assets/factory/props/assembler.png', 64, 80, 2, 2],
  inserter: ['assets/factory/props/inserter.png', 32, 48, 2, 3],
};

// Hoodie colour per agent (SOU-202): the registered one, else a hue hashed from the name,
// so unregistered agents are stable and rarely look alike (saturation/value kept tame).
export function hoodieFor(agent) {
  const p = personalities.get(keyOf(agent));
  if (p && p.hoodie) return p.hoodie;
  let hash = 0;
  for (const ch of keyOf(agent)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return { h: hash % 360, s: 0.8, v: 0.9 };
}

// Room types (SOU-195): which room an agent gets, as plain data. Rooms are Tiled maps in
// maps/rooms/<type>.json built by scripts/v3/build_rooms.py.

export const ROOM_TYPES = ['garage', 'serverroom', 'factory', 'mystery', 'tokenden', 'career', 'writer'];

export const ROOM_NAMES = {
  garage: 'garage studio', serverroom: 'server room', factory: 'factory floor', mystery: 'mystery cabin',
  tokenden: 'token den', career: 'career office', writer: "writer's loft",
};

// First match wins. Hermes profiles report as `hermes-<profile>`.
const RULES = [
  [/^claude(-code)?$/, 'garage'],
  [/^hermes-andrzej$/, 'serverroom'],
  [/^hermes-default$/, 'mystery'],
  [/^hermes-szarik$/, 'tokenden'],
  [/^hermes-(careerpa|zdzislaw)$/, 'career'],
  [/^hermes-florian$/, 'writer'],
  [/^(openchamber|opencode)/, 'factory'],
];

// Unknown agents are "dummy workers" and share the universal factory look.
export const FALLBACK_ROOM = 'factory';

export function roomTypeFor(agent) {
  const name = String(agent || '').trim().toLowerCase();
  for (const [re, type] of RULES) if (re.test(name)) return type;
  return FALLBACK_ROOM;
}

// Prop art: map object name -> served file. Names missing here (or files that fail to load)
// get a placeholder box, drawn ones (belt) are animated by the renderer.
export const PROP_ART = Object.fromEntries(
  ['desk', 'chair', 'rack', 'bookshelf', 'coffee', 'rug', 'couch', 'bass', 'amp', 'cat', 'van', 'spool', 'crate']
    .map((n) => [n, `assets/office-01/props/${n}.png`]),
);
export const FLAT_PROPS = new Set(['rug', 'belt']);

// Hoodie colour per agent (SOU-202): null keeps the generated orange.
const HOODIES = [
  [/^claude(-code)?$/, null],
  [/^hermes-default$/, { h: 52, s: 0.95, v: 1.05 }], // Bill Cipher yellow
  [/^hermes-szarik$/, { h: 2, s: 1, v: 0.95 }],
  [/^hermes-andrzej$/, { h: 125, s: 0.8, v: 0.8 }], // homelab green
  [/^hermes-(careerpa|zdzislaw)$/, { h: 222, s: 0.85, v: 0.62 }], // navy blazer energy
  [/^hermes-florian$/, { h: 280, s: 0.7, v: 0.85 }],
  [/^(openchamber|opencode)/, { h: 178, s: 0.8, v: 0.8 }],
];
const SPARE_HOODIES = [{ h: 320, s: 0.7, v: 0.9 }, { h: 95, s: 0.8, v: 0.85 }, { h: 200, s: 0.8, v: 0.9 }];

export function hoodieFor(agent) {
  const name = String(agent || '').trim().toLowerCase();
  for (const [re, c] of HOODIES) if (re.test(name)) return c;
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return SPARE_HOODIES[hash % SPARE_HOODIES.length];
}

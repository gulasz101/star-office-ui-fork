// Room types (SOU-195): which room an agent gets, as plain data. Rooms are Tiled maps in
// maps/rooms/<type>.json built by scripts/v3/build_rooms.py.

export const ROOM_TYPES = ['garage', 'serverroom', 'factory', 'mystery', 'tokenden', 'career', 'writer'];

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

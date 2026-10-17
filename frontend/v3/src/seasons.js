// Seasons (SOU-273): what the outside of the Lodge is made of.
//
// One season is picked per page load — `?season=<name>` pins it, otherwise it is random — and
// building.js / lodge.js / ambience.js read everything they draw outside from here. Adding a
// season is therefore a data edit plus a procedural drawing branch, not a renderer rewrite.
//
// The real sprites land in SOU-274..276 under `assets/seasons/<season>/`; until then a name with
// no loaded texture is drawn by lodge.js, exactly as the lodge tiles already fall back.
//
// Pure data plus one pure function: nothing here touches Phaser, so the Node test imports this
// module on its own.

// Winter's night multiply, unchanged since SOU-197. Every season carries its own.
export const NIGHT_TINT = 0x8a98c4;

// Slot order of `assets/seasons/<season>/tiles.png`, and the order the season's four tile names
// are listed in below: ground_a, ground_b, path, edge.
export const TILE_SLOTS = ['ground_a', 'ground_b', 'path', 'edge'];

// `assets/seasons/<season>/props/<file>` for the three trees, and the box each is drawn in.
// The sizes are the pines': big 64x96, tall 64x128, small 32x64.
export const TREE_FILES = ['tree_a', 'tree_b', 'tree_small'];
export const TREE_SIZE = [[64, 96], [64, 128], [32, 64]];

const seasonNames = (name) => ({
  ground: [`${name}_ground_a`, `${name}_ground_b`],
  path: `${name}_path`,
  edge: `${name}_edge`,
  trees: TREE_FILES.map((f) => `${name}_${f}`),
  label: name,
});

// The three non-winter seasons: name scheme from seasonNames(), their own night tint, and their
// own weather. Colours for the procedural fallback live in lodge.js (it owns how art is drawn).
const season = (name, tint, weather) => ({ ...seasonNames(name), tint, weather });

export const SEASONS = {
  // Winter reproduces the SOU-197 night exactly: the lodge's own tiles and pines, NIGHT_TINT,
  // and the snowfall settings that used to sit in ambience.js snowfall().
  winter: {
    ground: ['snow_a', 'snow_b'],
    path: 'snow_path',
    edge: 'snow_edge',
    trees: ['pine_a', 'pine_b', 'pine_small'],
    tint: NIGHT_TINT,
    label: 'winter',
    weather: {
      texture: 'flake',
      lifespan: 9000,
      speedY: { min: 14, max: 30 },
      speedX: { min: -8, max: 6 },
      scale: { min: 0.5, max: 1.2 },
      alpha: { start: 0.9, end: 0 },
      frequency: 22,
      quantity: 2,
    },
  },

  // Leaves: 6x6 orange/red/brown, slower than snow, blown sideways, tumbling.
  autumn: season('autumn', 0xa2949e, {
    texture: 'leaf',
    frame: [0, 1, 2],
    lifespan: 11000,
    speedY: { min: 10, max: 22 },
    speedX: { min: -30, max: -4 },
    scale: 1,
    alpha: { start: 0.95, end: 0.2 },
    rotate: { start: 0, end: 360 },
    frequency: 44,
    quantity: 2,
  }),

  // Petals: 4x4 pink and white, slow, a gentle drift rather than a sideways blow.
  spring: season('spring', 0x94a8a0, {
    texture: 'petal',
    frame: [0, 1],
    lifespan: 12000,
    speedY: { min: 8, max: 16 },
    speedX: { min: -12, max: 4 },
    scale: 1,
    alpha: { start: 0.9, end: 0.15 },
    rotate: { start: 0, end: 180 },
    frequency: 52,
    quantity: 2,
  }),

  // Fireflies: 3x3 yellow-green, additive, hovering (speedY around 0) and blinking. Sparse on
  // purpose — 1 per 66 ms over 9 s is ~136 alive against the snowfall's ~818, so under a third.
  summer: season('summer', 0x8fa4c0, {
    texture: 'firefly',
    blend: 'ADD',
    blink: true,
    lifespan: 9000,
    speedY: { min: -4, max: 4 },
    speedX: { min: -10, max: 10 },
    scale: 1,
    frequency: 66,
    quantity: 1,
  }),
};

export const SEASON_KEYS = Object.keys(SEASONS);

/** How many particles a weather config keeps alive at once: rate x lifespan. */
export const weatherAlive = (weather) =>
  ((weather.quantity || 1) / (weather.frequency || 1)) * (weather.lifespan || 0);

/**
 * The season for this page load. `?season=<name>` picks that season; an unknown name, no
 * parameter, or anything unparseable falls back to a uniformly random one. Never throws.
 */
export function pickSeason(search = '', random = Math.random) {
  let wanted = null;
  try {
    wanted = new URLSearchParams(search).get('season');
  } catch {
    wanted = null;
  }
  if (wanted && Object.prototype.hasOwnProperty.call(SEASONS, wanted)) return wanted;
  const i = Math.floor(random() * SEASON_KEYS.length);
  return SEASON_KEYS[i] || SEASON_KEYS[0];
}

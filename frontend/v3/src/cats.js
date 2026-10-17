// SOU-278: what a corridor cat is (a coat, eight poses) and what two cats do when they meet.
// Pure: no Phaser, no side effects — `tests/cats.test.mjs` runs this in plain Node, and ambience.js
// is the only place a decision taken here becomes a sprite.
//
// The frame layout is SOU-277's (cats 1 of 2) and it is a CROSS-TICKET CONTRACT: every coat is an
// 8-frame 256x32 strip, frames 0-3 walk facing right, 4 sitting, 5 curled up asleep, 6 crouched
// with the tail up, 7 arched back. Nothing here reads a PNG — a coat whose file has not landed yet
// still behaves, because ambience.js draws a placeholder sheet under the same texture key.
import { TILE } from './config.js';
import { CORRIDOR_Y, CORRIDOR_H } from './world.js';

// The seven coats, in the order the loader asks for them: assets/cats/<coat>.png
export const COATS = ['tabby_orange', 'black', 'white', 'grey', 'calico', 'tuxedo', 'siamese'];
export const coatKey = (coat) => `cat-${coat}`;
export const coatFile = (coat) => `assets/cats/${coat}.png`;

export const CAT_FRAME_W = 32;
export const CAT_FRAME_H = 32;
export const CAT_FRAMES_PER_COAT = 8;
export const CAT_FRAMES = { walk: [0, 1, 2, 3], sit: 4, sleep: 5, pounce: 6, hiss: 7 };

// the cartoon dust cloud a fight hides behind (SOU-277): 4 frames of 48x32
export const FIGHT_CLOUD = {
  key: 'fight-cloud',
  file: 'assets/cats/fight_cloud.png',
  frameWidth: 48,
  frameHeight: 32,
  frames: 4,
  frameRate: 10,
};

// how often an idle cat looks around, how far it looks, and how long it stays uninterested after
export const NOTICE_MIN_MS = 3000;
export const NOTICE_MAX_MS = 6000;
export const NOTICE_REACH_TILES = 3;
export const COOLDOWN_MS = 20000;

// The interaction roll, in one table, so the weights are a fact and not a comment.
export const INTERACTIONS = [
  { kind: 'chase', weight: 35 },
  { kind: 'play', weight: 30 },
  { kind: 'groom', weight: 20 },
  { kind: 'fight', weight: 15 },
];
export const INTERACTION_KINDS = INTERACTIONS.map((i) => i.kind);
export const TOTAL_WEIGHT = INTERACTIONS.reduce((n, i) => n + i.weight, 0);

// What each kind costs, straight off the ticket. `hold` is the frame the cat holds once it settles.
export const PLANS = {
  chase: { hold: null, speedMul: 1.8, lagMs: 400 },
  play: { hold: CAT_FRAMES.pounce, sitAt: CAT_FRAMES.sit, hopPx: 4, hopMs: 250, durationMs: 2000 },
  groom: { hold: CAT_FRAMES.sleep, durationMs: 2200, heart: true, gapPx: 30 },
  fight: { hold: CAT_FRAMES.hiss, hissMs: 500, cloudMs: 1500, fleeMul: 1.8, fleeTiles: 5 },
};

// The corridor's walkable band, in world px — the same bounds the cat has always moved inside.
// `x0/x1` are world tile columns of the building (walls included), so the cats keep 2 tiles clear
// of both end walls, and the row band is the corridor itself: y from CORRIDOR_Y+0.9 tiles down to
// CORRIDOR_Y+CORRIDOR_H-0.3.
export function corridorLimits(world) {
  return {
    minX: (world.x0 + 2) * TILE,
    maxX: (world.x1 - 2) * TILE,
    minY: (CORRIDOR_Y + 0.9) * TILE,
    maxY: (CORRIDOR_Y + CORRIDOR_H - 0.3) * TILE,
  };
}

/** A random spot inside the corridor band. Cats never leave it and never enter a room. */
export function corridorSpot(world, random = Math.random) {
  const { minX, maxX, minY, maxY } = corridorLimits(world);
  return { x: minX + random() * (maxX - minX), y: minY + random() * (maxY - minY) };
}

export const clampToCorridor = (world, x, y) => {
  const { minX, maxX, minY, maxY } = corridorLimits(world);
  return { x: Math.min(maxX, Math.max(minX, x)), y: Math.min(maxY, Math.max(minY, y)) };
};

/** One roll: 'chase' 35%, 'play' 30%, 'groom' 20%, 'fight' 15%. */
export function pickInteraction(random = Math.random) {
  let roll = random() * TOTAL_WEIGHT;
  for (const { kind, weight } of INTERACTIONS) {
    if (roll < weight) return kind;
    roll -= weight;
  }
  return INTERACTION_KINDS[INTERACTION_KINDS.length - 1];
}

/**
 * A coat no cat is wearing yet; once all seven are out, any coat may repeat.
 * `used` is the list already worn (order irrelevant) and the pool keeps COATS' order, so a given
 * random stream always yields the same coat.
 */
export function pickCoat(random = Math.random, used = []) {
  const free = COATS.filter((c) => !used.includes(c));
  const pool = free.length ? free : COATS;
  const i = Math.min(pool.length - 1, Math.floor(random() * pool.length));
  return pool[i];
}

/**
 * Who pairs off on this notice tick (SOU-278: every 3-6 s, each idle cat looks for another idle cat
 * within 3 tiles and rolls ONE interaction).
 *
 * `cats` are the live cat descriptors the renderer keeps: anything with x, y, state and
 * cooldownUntil. A cat is available when it is sitting — `state === 'sit'`, which is where both a
 * sitting cat and one that has just finished walking live — and its cooldown has expired.
 * Candidates are matched nearest-first, a cat pairs at most once per tick, and a 'fight' is only
 * allowed while no other fight is running: `opts.fightActive` carries the fights already on screen,
 * so the Lodge never has two at once, not even two started inside the same tick.
 *
 * Returns [{ a, b, kind }] for the caller to apply; it writes nothing.
 */
export function pairUp(cats, now, opts = {}) {
  const random = opts.random || Math.random;
  const tile = opts.tile ?? TILE;
  const reach = (opts.reach ?? NOTICE_REACH_TILES) * tile;
  const list = Array.isArray(cats) ? cats : [];
  const available = (c) => !!c && c.state === 'sit' && (c.cooldownUntil || 0) <= now;
  const taken = new Set();
  const pairs = [];
  let fighting = !!opts.fightActive;
  for (const a of list) {
    if (taken.has(a) || !available(a)) continue;
    let b = null;
    let best = Infinity;
    for (const c of list) {
      if (c === a || taken.has(c) || !available(c)) continue;
      const d = Math.hypot(c.x - a.x, c.y - a.y);
      if (d <= reach && d < best) {
        best = d;
        b = c;
      }
    }
    if (!b) continue;
    const kind = pickInteraction(random);
    if (kind === 'fight') {
      if (fighting) continue; // one fight at a time in the whole Lodge
      fighting = true;
    }
    taken.add(a);
    taken.add(b);
    pairs.push({ a, b, kind });
  }
  return pairs;
}

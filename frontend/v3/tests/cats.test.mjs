// SOU-278: the corridor cats' pure logic — the interaction roll, the pairing rules, the coat
// rotation, the SOU-277 frame contract and the corridor band the cats are allowed to live in.
// Everything here is a fact the ticket states; the renderer (ambience.js) only executes it.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  COATS, FIGHT_CLOUD, CAT_FRAMES, CAT_FRAMES_PER_COAT, INTERACTIONS, INTERACTION_KINDS, PLANS,
  TOTAL_WEIGHT, NOTICE_REACH_TILES, COOLDOWN_MS, corridorLimits, corridorSpot, clampToCorridor,
  pickCoat, pickInteraction, pairUp,
} from '../src/cats.js';

// xorshift32, so every draw below is identical on every machine
const seeded = (seed) => {
  let s = seed >>> 0;
  return () => {
    s ^= s << 13; s ^= s >>> 17; s ^= s << 5;
    return (s >>> 0) / 4294967296;
  };
};

const makeCat = (x, y, { state = 'sit', cooldownUntil = 0 } = {}) => ({ x, y, state, cooldownUntil });

// ---------------------------------------------------------------------------
// AC1.1 — the weights
// ---------------------------------------------------------------------------

test('AC1: the roll weights are the ticket\'s, 35 / 30 / 20 / 15', () => {
  assert.deepEqual(INTERACTIONS, [
    { kind: 'chase', weight: 35 },
    { kind: 'play', weight: 30 },
    { kind: 'groom', weight: 20 },
    { kind: 'fight', weight: 15 },
  ]);
  assert.equal(TOTAL_WEIGHT, 100);
  assert.deepEqual(INTERACTION_KINDS, ['chase', 'play', 'groom', 'fight']);
});

test('AC1: over 1000 seeded draws every weight lands within +/-4%', () => {
  const r = seeded(20261017);
  const counts = Object.fromEntries(INTERACTION_KINDS.map((k) => [k, 0]));
  for (let i = 0; i < 1000; i++) counts[pickInteraction(r)] += 1;
  for (const { kind, weight } of INTERACTIONS) {
    const share = counts[kind] / 1000;
    const want = weight / 100;
    assert.ok(Math.abs(share - want) <= 0.04, `${kind}: ${counts[kind]}/1000 = ${(share * 100).toFixed(1)}% vs ${weight}%`);
  }
  assert.equal(Object.values(counts).reduce((n, v) => n + v, 0), 1000);
});

test('the roll is exhaustive and never falls off the end of the table', () => {
  for (const v of [0, 0.34, 0.35001, 0.65, 0.84999, 0.85001, 0.999999]) {
    assert.ok(INTERACTION_KINDS.includes(pickInteraction(() => v)), `roll ${v}`);
  }
  assert.equal(pickInteraction(() => 0), 'chase');
  assert.equal(pickInteraction(() => 0.999999), 'fight');
});

// ---------------------------------------------------------------------------
// AC1.2 — the cooldown
// ---------------------------------------------------------------------------

test('AC1: a cat on cooldown is not paired, and pairs again the moment it expires', () => {
  const now = 1_000_000;
  const r = () => 0.1; // chase
  const warm = makeCat(0, 400, { cooldownUntil: now + COOLDOWN_MS });
  const cold = makeCat(60, 400);
  assert.deepEqual(pairUp([warm, cold], now, { random: r }), [], 'a cooling cat stays out of it');
  assert.equal(pairUp([warm, cold], now + COOLDOWN_MS, { random: r }).length, 1, 'exactly at the deadline it is available');
});

test('AC1: the cooldown is the ticket\'s 20 s', () => {
  assert.equal(COOLDOWN_MS, 20000);
});

// ---------------------------------------------------------------------------
// AC1.3 — one fight at a time
// ---------------------------------------------------------------------------

test('AC1: one tick rolls at most ONE fight even when every draw is a fight', () => {
  const line = Array.from({ length: 6 }, (_, i) => makeCat(i * 24, 400));
  const pairs = pairUp(line, 0, { random: () => 0.99 }); // 0.99 -> 'fight'
  assert.equal(pairs.length, 1, `only the first pair got a fight, not ${pairs.length}`);
  assert.equal(pairs[0].kind, 'fight');
});

test('AC1: while a fight is on screen no second fight can start', () => {
  const line = Array.from({ length: 6 }, (_, i) => makeCat(i * 24, 400));
  const pairs = pairUp(line, 0, { random: () => 0.99, fightActive: true });
  assert.deepEqual(pairs, [], 'every fight roll was refused');
  // the same cats, same tick, but a non-fight kind still gets through: three pairs, all chases
  const chases = pairUp(line, 0, { random: () => 0.1, fightActive: true });
  assert.equal(chases.length, 3, 'the live fight only blocked the fights');
  assert.ok(chases.every((p) => p.kind === 'chase'), chases.map((p) => p.kind).join(','));
});

test('AC1: over a simulated minute of ticks a fight never overlaps another fight', () => {
  const r = seeded(4242);
  const cats = Array.from({ length: 6 }, (_, i) => makeCat(i * 24, 400));
  const fights = [];
  let overlaps = 0;
  let started = 0;
  for (let tick = 0; tick < 60; tick++) {
    const now = tick * 1000;
    const live = fights.filter((f) => f.until > now);
    if (live.length > 1) overlaps += 1;
    const pairs = pairUp(cats, now, { random: r, fightActive: live.length > 0 });
    for (const p of pairs) {
      if (p.kind === 'fight') {
        started += 1;
        fights.push({ until: now + 2000 });
      }
      // what the renderer does: both cats are busy, then both cool down
      for (const c of [p.a, p.b]) c.cooldownUntil = now + COOLDOWN_MS / 10;
    }
  }
  assert.equal(overlaps, 0, `${overlaps} ticks had two fights alive`);
  assert.ok(started >= 3, `the simulation actually produced fights (${started})`);
});

// ---------------------------------------------------------------------------
// AC1.4 — coats
// ---------------------------------------------------------------------------

test('AC1: seven coats, and none is reused until all seven have been worn', () => {
  const r = seeded(278);
  const used = [];
  for (let i = 0; i < COATS.length; i++) used.push(pickCoat(r, used));
  assert.equal(new Set(used).size, COATS.length, `duplicate coat in ${used.join(', ')}`);
  assert.deepEqual([...used].sort(), [...COATS].sort(), 'all seven different coats');
  for (let i = 0; i < 20; i++) assert.ok(COATS.includes(pickCoat(r, used)), 'with the full set worn any coat may repeat');
});

test('AC1: which coat a cat gets follows the draw, not a fixed order', () => {
  const first = (seed) => {
    const r = seeded(seed);
    const used = [];
    for (let i = 0; i < COATS.length; i++) used.push(pickCoat(r, used));
    return used.join(',');
  };
  assert.notEqual(first(1), first(2), 'two seeds wear two different orders');
  assert.equal(first(1), first(1), 'and the same seed always wears the same order');
});

// ---------------------------------------------------------------------------
// The pairing rules the ticket adds around the roll
// ---------------------------------------------------------------------------

test('cats pair only with a sitting cat within 3 tiles, and a cat pairs once per tick', () => {
  const near = 2.9 * 32;
  assert.equal(pairUp([makeCat(0, 400), makeCat(near, 400)], 0, { random: () => 0.1 }).length, 1);
  assert.equal(pairUp([makeCat(0, 400), makeCat(3.1 * 32, 400)], 0, { random: () => 0.1 }).length, 0, '3.1 tiles is out of reach');
  assert.equal(NOTICE_REACH_TILES, 3);
  // a cat that is mid-walk (or mid-interaction) is not idle
  assert.equal(pairUp([makeCat(0, 400, { state: 'walk' }), makeCat(20, 400)], 0, { random: () => 0.1 }).length, 0);
  assert.equal(pairUp([makeCat(0, 400, { state: 'interact' }), makeCat(20, 400)], 0, { random: () => 0.1 }).length, 0);
  // three cats in a heap: one pair, never one cat in two pairs
  const heap = [makeCat(0, 400), makeCat(20, 400), makeCat(40, 400)];
  const pairs = pairUp(heap, 0, { random: () => 0.1 });
  assert.equal(pairs.length, 1);
  const seen = pairs.flatMap((p) => [p.a, p.b]);
  assert.equal(new Set(seen).size, seen.length, 'no cat is in two pairs');
  // no cats, one cat: nothing happens
  assert.deepEqual(pairUp([], 0), []);
  assert.deepEqual(pairUp([makeCat(0, 400)], 0, { random: () => 0.1 }), []);
});

// ---------------------------------------------------------------------------
// The corridor band (ticket item 3) and the SOU-277 contract
// ---------------------------------------------------------------------------

test('cats stay in the corridor band: never in a room row, never through an end wall', () => {
  const world = { x0: 0, x1: 95 };
  const { minX, maxX, minY, maxY } = corridorLimits(world);
  const r = seeded(9);
  for (let i = 0; i < 500; i++) {
    const s = corridorSpot(world, r);
    assert.ok(s.x >= minX && s.x <= maxX, `x ${s.x} inside ${minX}..${maxX}`);
    assert.ok(s.y >= minY && s.y <= maxY, `y ${s.y} inside ${minY}..${maxY}`);
    assert.ok(Math.floor(s.y / 32) >= 12 && Math.floor(s.y / 32) <= 15, 'a corridor row, not a room row');
    assert.ok(Math.floor(s.x / 32) >= 2 && Math.floor(s.x / 32) <= 93, 'not in an end wall');
  }
  assert.deepEqual(clampToCorridor(world, -999, -999), { x: minX, y: minY });
  assert.deepEqual(clampToCorridor(world, 99999, 99999), { x: maxX, y: maxY });
});

test('the frame layout is SOU-277\'s contract, in every coat', () => {
  assert.deepEqual(COATS, ['tabby_orange', 'black', 'white', 'grey', 'calico', 'tuxedo', 'siamese']);
  assert.equal(COATS.length, 7);
  assert.equal(CAT_FRAMES_PER_COAT, 8);
  assert.deepEqual(CAT_FRAMES, { walk: [0, 1, 2, 3], sit: 4, sleep: 5, pounce: 6, hiss: 7 });
  assert.deepEqual(FIGHT_CLOUD, {
    key: 'fight-cloud',
    file: 'assets/cats/fight_cloud.png',
    frameWidth: 48,
    frameHeight: 32,
    frames: 4,
    frameRate: 10,
  });
});

test('the plans are the ticket\'s numbers: 1.8x and 0.4 s chase, 2 s play, 0.5 s + 1.5 s fight', () => {
  assert.equal(PLANS.chase.speedMul, 1.8);
  assert.equal(PLANS.chase.lagMs, 400);
  assert.equal(PLANS.play.durationMs, 2000);
  assert.equal(PLANS.play.hold, CAT_FRAMES.pounce);
  assert.equal(PLANS.groom.hold, CAT_FRAMES.sleep);
  assert.equal(PLANS.groom.heart, true);
  assert.equal(PLANS.fight.hold, CAT_FRAMES.hiss);
  assert.equal(PLANS.fight.hissMs, 500);
  assert.equal(PLANS.fight.cloudMs, 1500);
});

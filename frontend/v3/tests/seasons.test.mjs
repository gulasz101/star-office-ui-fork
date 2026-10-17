// SOU-273: the season system. pickSeason() is the only way a page picks a season, every season
// carries the full shape the renderer reads, winter still reproduces the SOU-197 night exactly,
// and the summer emitter stays sparse.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  SEASONS, SEASON_KEYS, TILE_SLOTS, TREE_FILES, TREE_SIZE, NIGHT_TINT, pickSeason, weatherAlive,
} from '../src/seasons.js';

const REQUIRED = ['ground', 'path', 'edge', 'trees', 'tint', 'weather', 'label'];

test('?season=autumn returns autumn', () => {
  assert.equal(pickSeason('?season=autumn'), 'autumn');
  assert.equal(pickSeason('season=summer'), 'summer');
  for (const key of SEASON_KEYS) assert.equal(pickSeason(`?season=${key}`), key);
});

test('an unknown season, an empty search or junk still returns one of the four', () => {
  for (const search of ['?season=nope', '', '?season=', '?other=1', '?season=WINTER', '?season=0', null, undefined, '?season']) {
    assert.ok(SEASON_KEYS.includes(pickSeason(search)), `${JSON.stringify(search)} picked a season`);
  }
});

test('with a seeded random function, 400 draws hit all four seasons and each at least 60 times', () => {
  // xorshift32, so this run is identical on every machine
  let s = 20261017 >>> 0;
  const seeded = () => {
    s ^= s << 13; s ^= s >>> 17; s ^= s << 5;
    return (s >>> 0) / 4294967296;
  };
  const counts = Object.fromEntries(SEASON_KEYS.map((k) => [k, 0]));
  for (let i = 0; i < 400; i++) counts[pickSeason('', seeded)] += 1;
  assert.equal(SEASON_KEYS.filter((k) => counts[k] === 0).length, 0, `all four drawn: ${JSON.stringify(counts)}`);
  for (const k of SEASON_KEYS) assert.ok(counts[k] >= 60, `${k} drawn ${counts[k]} of 400`);
});

test('every season carries every key the renderer reads', () => {
  for (const [name, s] of Object.entries(SEASONS)) {
    for (const key of REQUIRED) assert.ok(key in s, `${name}.${key} exists`);
    assert.equal(s.label, name, `${name}.label`);
    assert.equal(typeof s.tint, 'number', `${name}.tint`);
    assert.equal(s.ground.length, 2, `${name}.ground is a pair`);
    assert.equal(s.trees.length, 3, `${name}.trees is a trio`);
    // the four tile names line up with the strip the art drop ships: ground_a, ground_b, path, edge
    assert.equal([...s.ground, s.path, s.edge].length, TILE_SLOTS.length, `${name} has ${TILE_SLOTS.length} tiles`);
    for (const key of ['lifespan', 'frequency', 'quantity']) assert.equal(typeof s.weather[key], 'number', `${name}.weather.${key}`);
    assert.equal(typeof s.weather.texture, 'string', `${name}.weather.texture`);
  }
  // the non-winter trees are named in the order main.js loads them: props/tree_a, tree_b, tree_small
  for (const name of SEASON_KEYS.filter((k) => k !== 'winter')) {
    assert.deepEqual(SEASONS[name].trees.map((t) => t.split('_').slice(1).join('_')), TREE_FILES, `${name} tree order`);
  }
});

test('the tree boxes are the pines: 64x96 big, 64x128 tall, 32x64 small', () => {
  assert.deepEqual(TREE_SIZE, [[64, 96], [64, 128], [32, 64]]);
});

test('winter is the SOU-197 night, unchanged', () => {
  const w = SEASONS.winter;
  assert.deepEqual(w.ground, ['snow_a', 'snow_b']);
  assert.equal(w.path, 'snow_path');
  assert.equal(w.edge, 'snow_edge');
  assert.deepEqual(w.trees, ['pine_a', 'pine_b', 'pine_small']);
  assert.equal(w.tint, NIGHT_TINT);
  assert.equal(NIGHT_TINT, 0x8a98c4);
  // the settings that used to sit in ambience.js snowfall(), field for field
  assert.deepEqual(w.weather, {
    texture: 'flake',
    lifespan: 9000,
    speedY: { min: 14, max: 30 },
    speedX: { min: -8, max: 6 },
    scale: { min: 0.5, max: 1.2 },
    alpha: { start: 0.9, end: 0 },
    frequency: 22,
    quantity: 2,
  });
});

test('the summer emitter is at most a third of the snowfall', () => {
  const snow = weatherAlive(SEASONS.winter.weather);
  const fireflies = weatherAlive(SEASONS.summer.weather);
  assert.ok(fireflies * 3 <= snow, `${fireflies.toFixed(1)} fireflies alive vs ${snow.toFixed(1)} flakes`);
});

test('summer fireflies hover, blink and glow additively', () => {
  const f = SEASONS.summer.weather;
  assert.equal(f.blend, 'ADD');
  assert.equal(f.blink, true);
  assert.ok(f.speedY.min <= 0 && f.speedY.max >= 0, `speedY sits around 0 (${f.speedY.min}..${f.speedY.max})`);
  assert.ok(Math.abs(f.speedY.min) <= 6 && Math.abs(f.speedY.max) <= 6, 'fireflies hover, they do not fall');
});

test('the four seasons are visibly different things', () => {
  const pick = (f) => SEASON_KEYS.map((k) => f(SEASONS[k]));
  for (const [what, values] of [
    ['ground', pick((s) => s.ground[0])],
    ['path', pick((s) => s.path)],
    ['edge', pick((s) => s.edge)],
    ['tint', pick((s) => s.tint)],
    ['weather texture', pick((s) => s.weather.texture)],
  ]) {
    assert.equal(new Set(values).size, SEASON_KEYS.length, `every season has its own ${what}`);
  }
  // autumn is slower than winter and drifts sideways; spring is slower still
  assert.ok(SEASONS.autumn.weather.speedY.max < SEASONS.winter.weather.speedY.max);
  assert.ok(SEASONS.autumn.weather.speedX.min < SEASONS.winter.weather.speedX.min, 'leaves blow sideways');
  assert.ok(SEASONS.spring.weather.speedY.max < SEASONS.autumn.weather.speedY.max);
  // the ground the shell paints never changes for a season once picked
  assert.deepEqual(SEASONS.autumn.ground, ['autumn_ground_a', 'autumn_ground_b']);
});

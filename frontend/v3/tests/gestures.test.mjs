// SOU-299: the touch and framing maths behind the phone Lodge.
import test from 'node:test';
import assert from 'node:assert/strict';
import { distance, midpoint, pinchZoom, coverZoom, isPhone, reservedBottom, overviewZoom } from '../src/gestures.js';

test('pinch scales the starting zoom by how far the fingers spread, clamped', () => {
  assert.equal(pinchZoom(1, 100, 200, 0.25, 4), 2);
  assert.equal(pinchZoom(1, 100, 50, 0.25, 4), 0.5);
  assert.equal(pinchZoom(2, 100, 1000, 0.25, 4), 4, 'never past the max');
  assert.equal(pinchZoom(0.5, 100, 1, 0.3, 4), 0.3, 'never below the min');
  assert.equal(pinchZoom(1.5, 0, 80, 0.25, 4), 1.5, 'a zero-length start changes nothing');
});

test('distance and midpoint of two touches', () => {
  assert.equal(distance({ x: 0, y: 0 }, { x: 3, y: 4 }), 5);
  assert.deepEqual(midpoint({ x: 10, y: 20 }, { x: 30, y: 60 }), { x: 20, y: 40 });
});

test('cover zoom: the world always fills the screen', () => {
  const world = { width: 4000, height: 2000 };
  assert.equal(coverZoom({ width: 400, height: 800 }, world), 0.4, 'portrait: height decides');
  assert.equal(coverZoom({ width: 1600, height: 400 }, world), 0.4, 'landscape: width decides');
  assert.equal(coverZoom({ width: 400, height: 800 }, { width: 0, height: 0 }), 0);
});

test('phones by their short side, and only phones reserve the bottom strip', () => {
  assert.ok(isPhone({ width: 390, height: 844 }));
  assert.ok(isPhone({ width: 844, height: 390 }));
  assert.ok(!isPhone({ width: 1400, height: 900 }));
  assert.equal(reservedBottom({ width: 1400, height: 900 }), 0);
  assert.ok(reservedBottom({ width: 390, height: 844 }) > 0);
});

test('overview zoom: fit on desktop, never a void, readable rooms in portrait', () => {
  const world = { width: 4000, height: 2400 };
  const building = { width: 2600, height: 600 };
  assert.equal(overviewZoom({ view: { width: 1400, height: 900 }, building, world, fit: 0.5 }), 0.5, 'desktop keeps its fit');
  const portrait = overviewZoom({ view: { width: 390, height: 844 }, building, world, fit: 0.125 });
  assert.ok(portrait >= 844 / 2400, 'covers the screen');
  assert.ok(portrait * building.height >= 844 * 0.34 - 1e-9, 'the building fills a third of the height');
  const landscape = overviewZoom({ view: { width: 844, height: 390 }, building, world, fit: 0.3 });
  assert.equal(landscape, Math.max(0.3, 844 / 4000), 'landscape phones just fit, no portrait boost');
});

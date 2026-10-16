// SOU-202: per-agent hoodies. Only the saturated orange band moves; skin, hair, jeans and
// the outline (sampled from the generated sheet) stay put.
import test from 'node:test';
import assert from 'node:assert/strict';
import { swapHoodie, rgbToHsv } from '../src/palette.js';
import { hoodieFor } from '../src/rooms.js';

const px = (...rgbs) => new Uint8ClampedArray(rgbs.flatMap(([r, g, b]) => [r, g, b, 255]));
const HOODIE = [[240, 135, 43], [179, 93, 22], [255, 196, 106]];
const KEEP = [[20, 22, 31], [244, 234, 210], [42, 47, 62], [68, 97, 138], [226, 211, 180], [195, 202, 217]];

test('hoodie shades move to the target hue, keeping their light/dark order', () => {
  const d = swapHoodie(px(...HOODIE), { h: 125 });
  const hs = [0, 4, 8].map((i) => rgbToHsv(d[i], d[i + 1], d[i + 2]));
  for (const [h] of hs) assert.ok(h > 105 && h < 145, `green-ish hue, got ${h}`);
  assert.ok(hs[1][2] < hs[0][2], 'the shadow shade stays darker');
});

test('skin, hair, jeans and outline are untouched', () => {
  const before = px(...KEEP);
  const after = swapHoodie(px(...KEEP), { h: 280, s: 0.7, v: 0.8 });
  assert.deepEqual([...after], [...before]);
});

test('transparent pixels and a null target are left alone', () => {
  const d = new Uint8ClampedArray([240, 135, 43, 0]);
  assert.deepEqual([...swapHoodie(d, { h: 200 })], [240, 135, 43, 0]);
  assert.deepEqual([...swapHoodie(px(...HOODIE), null)], [...px(...HOODIE)]);
});

test('hoodie colours per agent: claude keeps orange, the others differ and are stable', () => {
  assert.equal(hoodieFor('claude-code'), null);
  const hs = ['hermes-default', 'hermes-szarik', 'hermes-andrzej', 'hermes-careerpa', 'hermes-florian', 'openchamber']
    .map((a) => hoodieFor(a).h);
  assert.equal(new Set(hs).size, hs.length, 'all distinct');
  assert.deepEqual(hoodieFor('someone-new'), hoodieFor('someone-new'));
});

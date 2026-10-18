// SOU-300: the loading screen's label.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loaderText, loaderProgress, loaderDone } from '../src/loader.js';

test('label: unknown, partial and complete progress', () => {
  assert.equal(loaderText(undefined), 'lighting the lodge…');
  assert.equal(loaderText(0), 'lighting the lodge… 0%');
  assert.equal(loaderText(0.426), 'lighting the lodge… 42%');
  assert.equal(loaderText(1), 'opening the doors…');
  assert.equal(loaderText(-3), 'lighting the lodge… 0%');
});

test('progress and done are safe without a DOM (Node, tests)', () => {
  assert.doesNotThrow(() => loaderProgress(0.5));
  assert.doesNotThrow(() => loaderDone());
});

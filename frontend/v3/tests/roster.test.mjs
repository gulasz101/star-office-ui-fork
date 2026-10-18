// SOU-236: the roster chip's colour matches the agent's hoodie, the dot says what it does.
import test from 'node:test';
import assert from 'node:assert/strict';
import { hoodieCss, statusOf } from '../src/roster.js';
import { setPersonality, clearPersonalities } from '../src/rooms.js';

test('chip colours follow the registered hoodie; unregistered agents get distinct spares', () => {
  clearPersonalities();
  setPersonality('orange-one', { type: 'garage', hoodie: { h: 28, s: 1, v: 1 } });
  assert.equal(hoodieCss('orange-one'), 'rgb(240, 135, 43)', 'h28 x1 x1 is the generated orange');
  setPersonality('green-one', { type: 'serverroom', hoodie: { h: 125, s: 0.8, v: 0.8 } });
  assert.notEqual(hoodieCss('green-one'), hoodieCss('orange-one'));
  const spares = ['a-1', 'b-2', 'c-3', 'd-4', 'e-5', 'f-6'].map(hoodieCss);
  assert.ok(new Set(spares).size >= 5, 'unregistered agents rarely look alike');
  assert.notEqual(hoodieCss('wanderer-one'), hoodieCss('wanderer-two'));
});

test('status dot: working, idle or asleep; a walking character shows where it is going', () => {
  assert.equal(statusOf({ state: { activity: 'typing' } }), 'working');
  assert.equal(statusOf({ state: { activity: 'coffee' } }), 'idle');
  assert.equal(statusOf({ state: { activity: 'sleeping' } }), 'asleep');
  assert.equal(statusOf({ walking: true, target: { activity: 'building' }, state: { activity: 'sleeping' } }), 'working');
  assert.equal(statusOf({}), 'idle');
});

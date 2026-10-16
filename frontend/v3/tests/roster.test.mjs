// SOU-236: the roster chip's colour matches the agent's hoodie, the dot says what it does.
import test from 'node:test';
import assert from 'node:assert/strict';
import { hoodieCss, statusOf } from '../src/roster.js';

test('chip colours: claude keeps the generated orange, every profile differs', () => {
  assert.equal(hoodieCss('claude-code'), 'rgb(240, 135, 43)');
  const all = ['claude-code', 'hermes-default', 'hermes-szarik', 'hermes-andrzej', 'hermes-careerpa', 'hermes-florian', 'openchamber'].map(hoodieCss);
  assert.equal(new Set(all).size, all.length);
});

test('status dot: working, idle or asleep; a walking character shows where it is going', () => {
  assert.equal(statusOf({ state: { activity: 'typing' } }), 'working');
  assert.equal(statusOf({ state: { activity: 'coffee' } }), 'idle');
  assert.equal(statusOf({ state: { activity: 'sleeping' } }), 'asleep');
  assert.equal(statusOf({ walking: true, target: { activity: 'building' }, state: { activity: 'sleeping' } }), 'working');
  assert.equal(statusOf({}), 'idle');
});

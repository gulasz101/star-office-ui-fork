// SOU-294: a room's look comes from the API entry, never from a name list in this repo.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  roomTypeFor, setPersonality, setPersonalities, hasPersonality, clearPersonalities, ROOM_TYPES, FALLBACK_ROOM,
} from '../src/rooms.js';

test('room type from the agents entry; unknown or invalid types fall back to the factory', () => {
  clearPersonalities();
  setPersonalities([
    { agent: 'ops-bot', room: { type: 'serverroom', hoodie: null } },
    { agent: 'odd-bot', room: { type: 'castle', hoodie: null } },
    { agent: 'bare-bot', room: { type: null, hoodie: null } },
    { agent: 'old-backend-bot' }, // a pre-SOU-293 backend sends no room at all
    null,
  ]);
  assert.equal(roomTypeFor('ops-bot'), 'serverroom');
  assert.equal(roomTypeFor('Ops-Bot'), 'serverroom');
  assert.equal(roomTypeFor('odd-bot'), FALLBACK_ROOM);
  assert.equal(roomTypeFor('bare-bot'), FALLBACK_ROOM);
  assert.equal(roomTypeFor('old-backend-bot'), FALLBACK_ROOM);
  assert.equal(roomTypeFor('never-seen'), FALLBACK_ROOM);
  assert.ok(hasPersonality('bare-bot'));
  assert.ok(!hasPersonality('never-seen'));
});

test('a later read updates the personality', () => {
  clearPersonalities();
  setPersonality('x', { type: 'garage' });
  setPersonality('x', { type: 'writer' });
  assert.equal(roomTypeFor('x'), 'writer');
});

test('the fallback is a real room type', () => {
  assert.ok(ROOM_TYPES.includes(FALLBACK_ROOM));
});

test('no agent names live in the renderer source', () => {
  for (const f of ['rooms.js', 'main.js', 'building.js', 'office.js', 'roster.js']) {
    const src = readFileSync(new URL(`../src/${f}`, import.meta.url), 'utf8');
    assert.doesNotMatch(src, /hermes-|claude|openchamber|opencode/i, f);
  }
});

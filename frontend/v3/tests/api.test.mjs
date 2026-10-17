// SOU-221: public feed items have no id; the page derives a stable one from their content.
import test from 'node:test';
import assert from 'node:assert/strict';
import { contentId, normalise } from '../src/api.js';

const item = { agent: 'claude-code', event_type: 'tool_call', created_at: '2026-10-17T01:00:00Z', payload: { tool: 'Read' } };

test('content ids are stable, numeric and differ for different events', () => {
  assert.equal(contentId(item), contentId({ ...item }));
  assert.equal(typeof contentId(item), 'number');
  assert.notEqual(contentId(item), contentId({ ...item, created_at: '2026-10-17T01:00:01Z' }));
  assert.notEqual(contentId(item), contentId({ ...item, payload: { tool: 'Edit' } }));
});

test('normalise adds an id only when the item has none', () => {
  assert.equal(normalise(item).id, contentId(item));
  assert.equal(normalise({ ...item, id: 7 }).id, 7);
});

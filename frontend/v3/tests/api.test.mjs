// SOU-221: public feed items have no id; the page derives a stable one from their content.
import test from 'node:test';
import assert from 'node:assert/strict';
import { contentId, normalise, faceFromStatus, detectFace, currentFace } from '../src/api.js';

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

// SOU-288: a public face whose DB read fails answers 500, and that must not flip the page to
// the dev routes (which do not exist on the public face, so it would poll 404s forever).
test('only a 404 on /public/agents means the dev face', () => {
  assert.equal(faceFromStatus(404), 'dev');
  for (const status of [200, 500, 502, 503]) assert.equal(faceFromStatus(status), 'public');
});

test('detectFace keeps the public face when /public/agents fails with 500, and caches it', async () => {
  const realFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => (calls++, { ok: false, status: 500 });
  try {
    assert.equal(await detectFace(), 'public');
    assert.equal(await detectFace(), 'public');
    assert.equal(calls, 1);
    assert.equal(currentFace(), 'public');
  } finally {
    globalThis.fetch = realFetch;
  }
});

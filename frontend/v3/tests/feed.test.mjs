import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FeedState, ACTIVE_WINDOW_MS } from '../src/feed.js';
import { IDLE_COFFEE_MS, IDLE_SLEEP_MS } from '../activity.js';

const T0 = Date.parse('2026-10-16T12:00:00Z');
const at = (ms) => new Date(T0 + ms).toISOString();
let nextId = 1;
const ev = (agent, ms, event_type, payload = {}) => ({ id: nextId++, agent, event_type, payload, created_at: at(ms) });

test('newest event per agent wins within a batch, regardless of feed order', () => {
  const f = new FeedState();
  const batch = [
    ev('claude', 3000, 'command_run', { command: 'pytest -q' }),
    ev('claude', 1000, 'file_edit', { file: 'a.py' }),
    ev('hermes', 2000, 'tool_call', { tool: 'web_search' }),
  ].reverse(); // the API returns newest first
  const out = f.ingest(batch, { now: T0 + 3000 });
  const by = Object.fromEntries(out.map((o) => [o.agent, o.target]));
  assert.equal(by.claude.zone, 'build');
  assert.equal(by.hermes.zone, 'read');
});

test('the inclusive since-floor does not replay events', () => {
  const f = new FeedState();
  const e1 = ev('claude', 1000, 'file_edit', { file: 'a.py' });
  assert.equal(f.ingest([e1], { now: T0 + 1000 }).length, 1);
  assert.equal(f.cursor, e1.created_at);
  // next poll returns the boundary event again plus a new one
  const e2 = ev('claude', 1000, 'tool_call', { tool: 'Read' });
  const out = f.ingest([e2, e1], { now: T0 + 1500 });
  assert.equal(out.length, 1);
  assert.equal(out[0].target.zone, 'read');
  assert.equal(f.ingest([e2, e1], { now: T0 + 2000 }).length, 0);
});

test('initial load skips agents that went quiet long ago', () => {
  const f = new FeedState();
  const now = T0 + ACTIVE_WINDOW_MS + 60_000;
  const out = f.ingest([ev('old', 0, 'file_edit', { file: 'x' }), ev('live', now - 5000, 'tool_call', { tool: 'Bash' })], { initial: true, now });
  assert.deepEqual(out.map((o) => o.agent), ['live']);
});

test('idle escalates to coffee then sleep, once each', () => {
  const f = new FeedState();
  f.ingest([ev('claude', 0, 'file_edit', { file: 'a.py' })], { now: T0 });
  assert.deepEqual(f.idle(T0 + IDLE_COFFEE_MS - 1), []);
  const coffee = f.idle(T0 + IDLE_COFFEE_MS + 1);
  assert.equal(coffee[0].target.zone, 'wait');
  assert.deepEqual(f.idle(T0 + IDLE_COFFEE_MS + 2000), [], 'no repeat while still on coffee');
  const sleep = f.idle(T0 + IDLE_SLEEP_MS + 1);
  assert.equal(sleep[0].target.activity, 'sleeping');
});

test('a resting agent is never dragged back to the coffee machine', () => {
  const f = new FeedState();
  f.ingest([ev('claude', 0, 'code_completion', {})], { now: T0 });
  assert.deepEqual(f.idle(T0 + IDLE_COFFEE_MS + 1), []);
  const out = f.idle(T0 + IDLE_SLEEP_MS + 1);
  assert.equal(out[0].target.zone, 'rest');
  assert.equal(out[0].target.activity, 'sleeping');
});

test('a new event after idling resets the agent to work', () => {
  const f = new FeedState();
  f.ingest([ev('claude', 0, 'file_edit', { file: 'a.py' })], { now: T0 });
  f.idle(T0 + IDLE_SLEEP_MS + 1);
  const out = f.ingest([ev('claude', IDLE_SLEEP_MS + 5000, 'tool_call', { tool: 'Edit', file: 'b.py' })], { now: T0 + IDLE_SLEEP_MS + 5000 });
  assert.equal(out[0].target.zone, 'work');
  assert.deepEqual(f.idle(T0 + IDLE_SLEEP_MS + 6000), []);
});

test('junk items are ignored', () => {
  const f = new FeedState();
  assert.deepEqual(f.ingest([null, {}, { id: 99 }], { now: T0 }), []);
  assert.deepEqual(f.ingest(undefined, { now: T0 }), []);
});

test('the legacy v1 "Star" agent gets no office but still advances the cursor', () => {
  const f = new FeedState();
  const star = ev('Star', 9000, 'state_change', { state: 'idle' });
  const out = f.ingest([star, ev('claude', 1000, 'file_edit', { file: 'a.py' })], { now: T0 + 9000 });
  assert.deepEqual(out.map((o) => o.agent), ['claude']);
  assert.equal(f.cursor, star.created_at);
});

// ---- SOU-211: background work -------------------------------------------------------
import { BACKGROUND_OPEN_MS } from '../src/feed.js';

const bgs = (agent, ms, id, kind = 'shell', label = 'sleep') => ev(agent, ms, 'background_start', { task_id: id, kind, label });
const bge = (agent, ms, id, extra = {}) => ev(agent, ms, 'background_end', { task_id: id, kind: 'shell', label: 'sleep', status: 'ok', ...extra });

test('background events are queued in order and never move the character', () => {
  const f = new FeedState();
  const out = f.ingest([bge('claude', 3000, 't1', { exit_code: 0 }), bgs('claude', 2000, 't2', 'agent', 'Explore'), bgs('claude', 1000, 't1'), ev('claude', 500, 'file_edit', { file: 'a.py' })], { now: T0 + 3000 });
  assert.equal(out.length, 1);
  assert.equal(out[0].target.zone, 'work', 'the file edit decides where the character goes');
  const q = f.takeBackground();
  assert.deepEqual(q.map((x) => [x.op, x.task.id, x.task.kind]), [['start', 't1', 'shell'], ['start', 't2', 'agent'], ['end', 't1', 'shell']]);
  assert.equal(q[1].task.label, 'Explore');
  assert.deepEqual(f.takeBackground(), [], 'taking empties the queue');
});

test('a batch of only background events produces no character target', () => {
  const f = new FeedState();
  assert.deepEqual(f.ingest([bgs('claude', 1000, 'x')], { now: T0 + 1000 }), []);
  assert.equal(f.takeBackground().length, 1);
});

test('page load keeps only background work that is still running and recent', () => {
  const f = new FeedState();
  const now = T0 + BACKGROUND_OPEN_MS + 10 * 60_000;
  f.ingest([
    bgs('claude', 0, 'ancient'), // started too long ago, never ended: lost
    bgs('claude', now - 60_000, 'done'), bge('claude', now - 30_000, 'done'),
    bgs('claude', now - 20_000, 'running'),
    ev('claude', now - 1000, 'tool_call', { tool: 'Read' }),
  ], { initial: true, now });
  assert.deepEqual(f.takeBackground().map((x) => [x.op, x.task.id]), [['start', 'running']]);
});

test('background events without a task id are dropped', () => {
  const f = new FeedState();
  f.ingest([ev('claude', 1000, 'background_start', { kind: 'shell' })], { now: T0 + 1000 });
  assert.deepEqual(f.takeBackground(), []);
});

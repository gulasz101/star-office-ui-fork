/**
 * activity.test.mjs — contract tests for frontend/v3/activity.js (SOU-156).
 * Run: node --test frontend/v3/tests/
 *
 * activity.js is the pure data layer of the v3 renderer: mapEvent(event) turns
 * one GET /events/recent item into { zone, activity, bubble } — "where should
 * the character go and what is it doing" — and idleState(msSinceLastEvent)
 * decides when the character drifts to coffee / the couch. No Phaser, no DOM:
 * everything here runs under bare `node --test`.
 *
 * The event shape is one envelope item (backend/app.py events_recent_endpoint,
 * ADR-035 schema v1): { id, agent, event_type, payload, created_at }. Payload
 * fields per type: reporter/openchamber-star-office-reporter/README.md
 * (tool, file, command, state, status, ...).
 *
 * TABLE-DRIVEN: every row pins the exact {zone, activity, bubble}. AC2: every
 * tool name listed in SOU-156 has a row (COVERAGE meta-test enforces it), and
 * a VARIANTS meta-test re-runs every row case-insensitively and behind
 * mcp__server__tool / mcp_tool prefixes. AC3: the PRIVACY table plus a global
 * leak invariant checked across ALL cases.
 */
'use strict';

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  mapEvent,
  idleState,
  IDLE_COFFEE_MS,
  IDLE_SLEEP_MS,
  BUBBLE_MAX_LEN,
} from '../activity.js';

const ZONES = ['work', 'ops', 'build', 'read', 'wait', 'rest', 'entry'];
const ACTIVITIES = [
  'typing', 'terminal', 'building', 'reading', 'thinking', 'coffee',
  'sleeping', 'arriving', 'leaving', 'celebrating',
];

/** Wrap a raw event_type/payload as one feed item. */
function ev(event_type, payload = {}, extra = {}) {
  return {
    id: 1,
    agent: 'test-agent',
    event_type,
    payload,
    created_at: '2026-10-16T12:00:00Z',
    ...extra,
  };
}

/** tool_call envelope for a given tool name + payload. */
function toolCall(tool, payload = {}) {
  return ev('tool_call', { tool, status: 'ok', ...payload });
}

/**
 * The global leak invariant every returned triple must satisfy:
 * - zone/activity from the fixed enums, bubble a non-empty string
 * - bubble <= BUBBLE_MAX_LEN chars
 * - never a path (no '/', no '\', no '..'), never an argument dump (no '--'),
 *   never an auth leak (no 'Bearer'), never quotes.
 */
function assertTriple(triple, ctx = '') {
  assert.ok(triple && typeof triple === 'object', `${ctx}: expected an object`);
  const { zone, activity, bubble } = triple;
  assert.ok(ZONES.includes(zone), `${ctx}: zone ${JSON.stringify(zone)} not in ${ZONES}`);
  assert.ok(ACTIVITIES.includes(activity), `${ctx}: activity ${JSON.stringify(activity)} not in ${ACTIVITIES}`);
  assert.equal(typeof bubble, 'string', `${ctx}: bubble must be a string`);
  assert.ok(bubble.length > 0, `${ctx}: bubble must not be empty`);
  assert.ok(
    [...bubble].length <= BUBBLE_MAX_LEN,
    `${ctx}: bubble ${JSON.stringify(bubble)} exceeds ${BUBBLE_MAX_LEN} chars (${[...bubble].length})`,
  );
  for (const banned of ['/', '\\', '..', '--', 'Bearer', 'bearer', '"', "'"]) {
    assert.ok(!bubble.includes(banned), `${ctx}: bubble ${JSON.stringify(bubble)} leaks ${JSON.stringify(banned)}`);
  }
  // token VALUES must never appear; a file merely named tokens.py is a legal
  // basename bubble, so flag assignment shapes (?token=, token:, token=) only.
  assert.ok(
    !/[?&\s]token\s*[:=]|^token\s*[:=]/i.test(bubble),
    `${ctx}: bubble ${JSON.stringify(bubble)} looks like a token assignment leak`,
  );
}

test('invariant sanity: the checker itself rejects bad triples', () => {
  assert.throws(() => assertTriple({ zone: 'kitchen', activity: 'typing', bubble: 'ok' }));
  assert.throws(() => assertTriple({ zone: 'work', activity: 'typing', bubble: 'x'.repeat(29) }));
  assert.throws(() => assertTriple({ zone: 'ops', activity: 'terminal', bubble: '$ curl --header secret' }));
  assertTriple({ zone: 'work', activity: 'typing', bubble: 'editing app.py' });
});

// ---------------------------------------------------------------------------
// TOOL TABLE — one primary case per tool name listed in SOU-156 (AC2).
// `covers` names the spec entry this row satisfies.
// ---------------------------------------------------------------------------

const TOOL_CASES = [
  // --- Claude Code ---
  { covers: 'Bash (cc)', event: toolCall('Bash', { command: 'ls -la /home/andrzej/work' }),
    zone: 'ops', activity: 'terminal', bubble: '$ ls' },
  { covers: 'Read (cc)', event: toolCall('Read', { file_path: '/docs/guide.md' }),
    zone: 'read', activity: 'reading', bubble: 'reading guide.md' },
  { covers: 'Edit (cc)', event: toolCall('Edit', { file: '/repo/backend/app.py' }),
    zone: 'work', activity: 'typing', bubble: 'editing app.py' },
  { covers: 'Write (cc)', event: toolCall('Write', { file: '/tmp/notes.md' }),
    zone: 'work', activity: 'typing', bubble: 'editing notes.md' },
  { covers: 'MultiEdit (cc)', event: toolCall('MultiEdit', { file: '/x/y/router.ts' }),
    zone: 'work', activity: 'typing', bubble: 'editing router.ts' },
  { covers: 'NotebookEdit (cc)', event: toolCall('NotebookEdit', { notebook_path: '/nb/train.ipynb' }),
    zone: 'work', activity: 'typing', bubble: 'editing train.ipynb' },
  { covers: 'Grep (cc)', event: toolCall('Grep', { pattern: 'secret_token = "abc"' }),
    zone: 'read', activity: 'reading', bubble: 'searching files' },
  { covers: 'Glob (cc)', event: toolCall('Glob', { pattern: 'src/**/*.py' }),
    zone: 'read', activity: 'reading', bubble: 'listing files' },
  { covers: 'WebFetch (cc)', event: toolCall('WebFetch', { url: 'https://api.internal/v1?token=xyz' }),
    zone: 'read', activity: 'reading', bubble: 'fetching web' },
  { covers: 'WebSearch (cc)', event: toolCall('WebSearch', { query: 'phaser 4 tilemap' }),
    zone: 'read', activity: 'reading', bubble: 'searching web' },
  { covers: 'Task (cc)', event: toolCall('Task', { description: 'research subagents' }),
    zone: 'work', activity: 'thinking', bubble: 'running task' },
  { covers: 'Agent (cc)', event: toolCall('Agent', { subagent_type: 'explore' }),
    zone: 'work', activity: 'thinking', bubble: 'spawning agent' },
  { covers: 'TodoWrite (cc)', event: toolCall('TodoWrite', { todos: [] }),
    zone: 'work', activity: 'thinking', bubble: 'planning work' },

  // --- OpenCode / OpenChamber ---
  { covers: 'bash (oc)', event: toolCall('bash', { command: 'git push origin main' }),
    zone: 'build', activity: 'building', bubble: '$ git push' },
  { covers: 'read (oc)', event: toolCall('read', { file: 'README.md' }),
    zone: 'read', activity: 'reading', bubble: 'reading README.md' },
  { covers: 'edit (oc)', event: toolCall('edit', { file: 'backend/app.py' }),
    zone: 'work', activity: 'typing', bubble: 'editing app.py' },
  { covers: 'write (oc)', event: toolCall('write', { file: 'frontend/index.html' }),
    zone: 'work', activity: 'typing', bubble: 'editing index.html' },
  { covers: 'patch (oc/hermes)', event: toolCall('patch', { file: 'scripts/deploy.sh' }),
    zone: 'work', activity: 'typing', bubble: 'editing deploy.sh' },
  { covers: 'grep (oc)', event: toolCall('grep', { pattern: 'TODO' }),
    zone: 'read', activity: 'reading', bubble: 'searching files' },
  { covers: 'glob (oc)', event: toolCall('glob', { pattern: '**/*.test.mjs' }),
    zone: 'read', activity: 'reading', bubble: 'listing files' },
  { covers: 'list (oc)', event: toolCall('list', { path: '.' }),
    zone: 'read', activity: 'reading', bubble: 'listing files' },
  { covers: 'webfetch (oc)', event: toolCall('webfetch', { url: 'https://example.com' }),
    zone: 'read', activity: 'reading', bubble: 'fetching web' },
  { covers: 'task (oc)', event: toolCall('task', {}),
    zone: 'work', activity: 'thinking', bubble: 'running task' },
  { covers: 'todowrite (oc)', event: toolCall('todowrite', {}),
    zone: 'work', activity: 'thinking', bubble: 'planning work' },

  // --- Hermes ---
  { covers: 'terminal (hermes)', event: toolCall('terminal', { command: 'kubectl apply -f apps/sou.yaml' }),
    zone: 'build', activity: 'building', bubble: '$ kubectl apply' },
  { covers: 'read_file (hermes)', event: toolCall('read_file', { file: '/etc/app/config.yaml' }),
    zone: 'read', activity: 'reading', bubble: 'reading config.yaml' },
  { covers: 'write_file (hermes)', event: toolCall('write_file', { file: '/tmp/report.csv' }),
    zone: 'work', activity: 'typing', bubble: 'editing report.csv' },
  { covers: 'search_files (hermes)', event: toolCall('search_files', { pattern: 'password' }),
    zone: 'read', activity: 'reading', bubble: 'searching files' },
  { covers: 'web_search (hermes)', event: toolCall('web_search', { query: 'phaser 4 release notes' }),
    zone: 'read', activity: 'reading', bubble: 'searching web' },
  { covers: 'web_extract (hermes)', event: toolCall('web_extract', { url: 'https://docs.example' }),
    zone: 'read', activity: 'reading', bubble: 'fetching web' },
  { covers: 'browser_* (hermes)', event: toolCall('browser_navigate', { url: 'https://plan.voitech.dev' }),
    zone: 'read', activity: 'reading', bubble: 'browsing web' },
  { covers: 'browser_* click (hermes)', event: toolCall('browser_click', { ref: 'e12' }),
    zone: 'read', activity: 'reading', bubble: 'browsing web' },
  { covers: 'execute_code (hermes)', event: toolCall('execute_code', { code: 'print(open("/etc/passwd").read())' }),
    zone: 'ops', activity: 'terminal', bubble: 'running code' },
  { covers: 'delegate_task (hermes)', event: toolCall('delegate_task', { task: 'summarize logs' }),
    zone: 'work', activity: 'thinking', bubble: 'delegating' },
  { covers: 'memory (hermes)', event: toolCall('memory', { content: 'user prefers dark mode' }),
    zone: 'work', activity: 'thinking', bubble: 'jotting notes' },
  { covers: 'todo (hermes)', event: toolCall('todo', { items: ['ship M1'] }),
    zone: 'work', activity: 'thinking', bubble: 'planning work' },
  { covers: 'skill_view (hermes)', event: toolCall('skill_view', { name: 'tilemaps' }),
    zone: 'read', activity: 'reading', bubble: 'reading skill' },

  // --- file payload aliases and no-file fallbacks ---
  { covers: 'edit via path alias', event: toolCall('edit', { path: 'src/deep/dir/module.js' }),
    zone: 'work', activity: 'typing', bubble: 'editing module.js' },
  { covers: 'edit without file', event: toolCall('Edit', {}),
    zone: 'work', activity: 'typing', bubble: 'editing' },
  { covers: 'read without file', event: toolCall('read', {}),
    zone: 'read', activity: 'reading', bubble: 'reading' },
  { covers: 'bash without command', event: toolCall('Bash', {}),
    zone: 'ops', activity: 'terminal', bubble: '$ shell' },
];

for (const c of TOOL_CASES) {
  test(`tool: ${c.covers} -> ${c.zone}/${c.activity} "${c.bubble}"`, () => {
    const got = mapEvent(c.event);
    assertTriple(got, c.covers);
    assert.deepEqual({ ...got }, { zone: c.zone, activity: c.activity, bubble: c.bubble });
  });
}

// ---------------------------------------------------------------------------
// AC2 meta-tests: the coverage list and the name-robustness loop.
// ---------------------------------------------------------------------------

const SPEC_TOOL_NAMES = [
  // Claude Code
  'Bash', 'Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Grep', 'Glob',
  'WebFetch', 'WebSearch', 'Task', 'Agent', 'TodoWrite',
  // OpenCode / OpenChamber
  'bash', 'read', 'edit', 'write', 'patch', 'grep', 'glob', 'list', 'webfetch',
  'task', 'todowrite',
  // Hermes
  'terminal', 'read_file', 'write_file', 'search_files', 'web_search',
  'web_extract', 'browser_*', 'execute_code', 'delegate_task', 'memory', 'todo',
  'skill_view',
];

test('AC2 coverage: every tool name in the SOU-156 list has a table case', () => {
  const covered = TOOL_CASES.map((c) => c.covers).join('\n');
  for (const name of SPEC_TOOL_NAMES) {
    if (name === 'browser_*') {
      assert.match(covered, /browser_\*/, `${name} has no case`);
      continue;
    }
    // e.g. 'Bash (cc)' / 'bash (oc)' rows
    assert.match(covered, new RegExp(`^${name} \\(`, 'm'), `${name} has no case`);
  }
  // 'patch' is listed once for both OpenCode and Hermes; one shared row is fine
  assert.match(covered, /patch \(oc\/hermes\)/);
});

test('AC2 robustness: case-insensitive + mcp__server__tool / mcp_tool prefixes map identically', () => {
  for (const c of TOOL_CASES) {
    const base = c.event.payload.tool;
    const canonical = mapEvent(c.event);
    const variants = [base, base.toUpperCase(), `mcp__srv__${base}`, `mcp_${base}`];
    const { tool: _dropped, ...payloadWithoutTool } = c.event.payload;
    for (const v of variants) {
      const got = mapEvent(toolCall(v, payloadWithoutTool));
      assert.deepEqual(
        { ...got },
        { ...canonical },
        `tool variant ${JSON.stringify(v)} of ${base} must map identically`,
      );
    }
  }
});

// ---------------------------------------------------------------------------
// EVENT TYPES — the eight in-use types plus unknown fallbacks.
// ---------------------------------------------------------------------------

const EVENT_CASES = [
  { name: 'session_start', event: ev('session_start', { directory: '/x', engine: 'opencode' }),
    zone: 'entry', activity: 'arriving', bubble: 'arriving' },
  { name: 'session_end', event: ev('session_end', { reason: 'idle_timeout' }),
    zone: 'rest', activity: 'sleeping', bubble: 'signing off' },
  { name: 'code_completion', event: ev('code_completion', { files_touched: 3, edits: 5 }),
    zone: 'rest', activity: 'celebrating', bubble: 'done!' },
  { name: 'file_edit event', event: ev('file_edit', { tool: 'Edit', file: '/secrets/tokens.py' }),
    zone: 'work', activity: 'typing', bubble: 'editing tokens.py' },
  { name: 'command_run event', event: ev('command_run', { command: 'pytest -q backend/tests' }),
    zone: 'build', activity: 'building', bubble: '$ pytest' },
  { name: 'itsaplan_issue_transition', event: ev('itsaplan_issue_transition', { issue: 'SOU-156', to: 'Done' }),
    zone: 'work', activity: 'thinking', bubble: 'updating board' },

  // legacy state_change values -> sensible zones
  { name: 'state_change idle', event: ev('state_change', { state: 'idle' }),
    zone: 'wait', activity: 'coffee', bubble: 'coffee break' },
  { name: 'state_change writing', event: ev('state_change', { state: 'writing' }),
    zone: 'work', activity: 'typing', bubble: 'writing' },
  { name: 'state_change researching', event: ev('state_change', { state: 'researching' }),
    zone: 'read', activity: 'reading', bubble: 'researching' },
  { name: 'state_change executing', event: ev('state_change', { state: 'executing' }),
    zone: 'ops', activity: 'terminal', bubble: 'executing' },
  { name: 'state_change syncing', event: ev('state_change', { state: 'syncing' }),
    zone: 'build', activity: 'building', bubble: 'syncing' },
  { name: 'state_change error', event: ev('state_change', { state: 'error' }),
    zone: 'wait', activity: 'coffee', bubble: 'hit an error' },
  { name: 'state_change unknown value', event: ev('state_change', { state: 'vibing' }),
    zone: 'work', activity: 'thinking', bubble: 'vibing' },

  // anything else must still map to something sensible
  { name: 'unknown event_type', event: ev('lunch_break', {}),
    zone: 'work', activity: 'thinking', bubble: 'lunch_break' },
  { name: 'unknown event_type with tool', event: ev('lunch_break', { tool: 'Edit' }),
    zone: 'work', activity: 'thinking', bubble: 'edit' },
  { name: 'tool_call with unknown tool', event: toolCall('zip', { file: '/a/b.tar.gz' }),
    zone: 'work', activity: 'thinking', bubble: 'zip' },
  { name: 'tool_call with mcp unknown tool', event: toolCall('mcp__hindsight__recall', {}),
    zone: 'work', activity: 'thinking', bubble: 'recall' },
  { name: 'event without event_type', event: { id: 2, agent: 'x', payload: {} },
    zone: 'work', activity: 'thinking', bubble: 'unknown' },
];

for (const c of EVENT_CASES) {
  test(`event: ${c.name} -> ${c.zone}/${c.activity} "${c.bubble}"`, () => {
    const got = mapEvent(c.event);
    assertTriple(got, c.name);
    assert.deepEqual({ ...got }, { zone: c.zone, activity: c.activity, bubble: c.bubble });
  });
}

// ---------------------------------------------------------------------------
// SHELL CLASSIFICATION — ops vs build, per the ticket rule list.
// ---------------------------------------------------------------------------

const COMMAND_CASES = [
  // ops/terminal: plain shell work
  { cmd: 'ls -la', zone: 'ops', activity: 'terminal', bubble: '$ ls' },
  { cmd: 'cat /etc/hosts', zone: 'ops', activity: 'terminal', bubble: '$ cat' },
  { cmd: 'ssh host@box.local', zone: 'ops', activity: 'terminal', bubble: '$ ssh' },
  { cmd: 'git status && git diff', zone: 'ops', activity: 'terminal', bubble: '$ git status' },
  { cmd: 'docker ps -a', zone: 'ops', activity: 'terminal', bubble: '$ docker ps' },
  { cmd: 'npm run dev', zone: 'ops', activity: 'terminal', bubble: '$ npm dev' },
  { cmd: 'npx vitest', zone: 'ops', activity: 'terminal', bubble: '$ npx' },
  { cmd: 'cd /srv/app', zone: 'ops', activity: 'terminal', bubble: '$ cd' },
  // build/test/deploy-ish -> build/building
  { cmd: 'make build', zone: 'build', activity: 'building', bubble: '$ make' },
  { cmd: 'npm run test', zone: 'build', activity: 'building', bubble: '$ npm test' },
  { cmd: 'npm test', zone: 'build', activity: 'building', bubble: '$ npm test' },
  { cmd: 'pnpm build', zone: 'build', activity: 'building', bubble: '$ pnpm build' },
  { cmd: 'yarn lint', zone: 'build', activity: 'building', bubble: '$ yarn lint' },
  { cmd: 'pytest tests/unit -x', zone: 'build', activity: 'building', bubble: '$ pytest' },
  { cmd: 'cargo check', zone: 'build', activity: 'building', bubble: '$ cargo check' },
  { cmd: 'go build ./cmd/app', zone: 'build', activity: 'building', bubble: '$ go build' },
  { cmd: 'go test ./...', zone: 'build', activity: 'building', bubble: '$ go test' },
  { cmd: 'docker build -t sou:latest .', zone: 'build', activity: 'building', bubble: '$ docker build' },
  { cmd: 'kubectl apply -f flux/apps.yaml', zone: 'build', activity: 'building', bubble: '$ kubectl apply' },
  { cmd: 'kubectl rollout restart deploy/sou', zone: 'build', activity: 'building', bubble: '$ kubectl rollout' },
  { cmd: 'flux reconcile kustomize-source', zone: 'build', activity: 'building', bubble: '$ flux' },
  { cmd: 'helm upgrade --install sou ./chart', zone: 'build', activity: 'building', bubble: '$ helm' },
  { cmd: 'git push origin master', zone: 'build', activity: 'building', bubble: '$ git push' },
  { cmd: 'sudo systemctl restart nginx', zone: 'ops', activity: 'terminal', bubble: '$ systemctl' },
  { cmd: 'GH_TOKEN=abc gh pr create', zone: 'ops', activity: 'terminal', bubble: '$ gh' },
];

for (const c of COMMAND_CASES) {
  test(`command: ${JSON.stringify(c.cmd)} -> ${c.zone}/${c.activity} "${c.bubble}"`, () => {
    for (const event of [toolCall('Bash', { command: c.cmd }), ev('command_run', { command: c.cmd })]) {
      const got = mapEvent(event);
      assertTriple(got, c.cmd);
      assert.deepEqual({ ...got }, { zone: c.zone, activity: c.activity, bubble: c.bubble }, `for ${c.cmd}`);
    }
  });
}

// ---------------------------------------------------------------------------
// AC3 PRIVACY — tokens, paths, arguments, long commands, weird input.
// ---------------------------------------------------------------------------

const PRIVACY_CASES = [
  {
    name: 'bearer token never reaches the bubble',
    event: ev('command_run', { command: 'curl -s -H "Authorization: Bearer abc123XYZ" https://api.voitech.dev/secrets' }), // gitleaks:allow synthetic privacy canary, not a credential
    bubble: '$ curl',
  },
  {
    name: 'inline env secret is dropped',
    event: toolCall('Bash', { command: 'DATABASE_PASSWORD=hunter2 psql -c "SELECT 1"' }),
    bubble: '$ psql',
  },
  {
    name: 'file bubbles keep the basename only',
    event: ev('file_edit', { file: '/storage/workspaces/secret-project/backend/app.py' }),
    bubble: 'editing app.py',
  },
  {
    name: 'dotfile paths leak nothing but the name',
    event: toolCall('write_file', { file: '/home/andrzej/.ssh/id_ed25519' }),
    bubble: 'editing id_ed25519',
  },
  {
    name: 'long argument-heavy command collapses to program+subcommand',
    event: toolCall('bash', {
      command: 'npm run build -- --max-old-space-size=8192 --config /etc/webpack/prod.config.js && gzip -9 dist/*',
    }),
    bubble: '$ npm build',
  },
  {
    name: 'very long basename is fitted, path still hidden',
    event: toolCall('Edit', { file: '/x/' + 'a_very_long_component_name_index'.repeat(3) + '.ts' }),
    bubblePrefix: 'editing ',
  },
  {
    name: 'grep pattern is never shown',
    event: toolCall('Grep', { pattern: 'api_key="sk-live-abcdef123456"' }), // gitleaks:allow fake key shape; proves the pattern never reaches the bubble
    bubble: 'searching files',
  },
  {
    name: 'browser url is never shown',
    event: toolCall('browser_navigate', { url: 'https://admin:pw@internal.corp/panel?token=xyz' }),
    bubble: 'browsing web',
  },
  {
    name: 'code_completion counts never leak content',
    event: ev('code_completion', { files_touched: ['secret.env'], diff: '+API_KEY=xyz' }),
    bubble: 'done!',
  },
];

for (const c of PRIVACY_CASES) {
  test(`privacy: ${c.name}`, () => {
    const got = mapEvent(c.event);
    assertTriple(got, c.name);
    if (c.bubble !== undefined) {
      assert.equal(got.bubble, c.bubble);
    }
    if (c.bubblePrefix !== undefined) {
      assert.ok(got.bubble.startsWith(c.bubblePrefix), `${got.bubble} should start with ${c.bubblePrefix}`);
    }
    for (const banned of ['abc123XYZ', 'hunter2', 'secret-project', '/storage', '/home', '.ssh', 'sk-live', 'admin:pw', 'API_KEY']) {
      assert.ok(!got.bubble.includes(banned), `${got.bubble} leaks ${banned}`);
    }
  });
}

test('privacy sweep: no bubble across every case in this file leaks path/args/secrets', () => {
  const all = [...TOOL_CASES, ...EVENT_CASES, ...COMMAND_CASES, ...PRIVACY_CASES]
    .map((c) => c.event ?? toolCall('Bash', { command: c.cmd }))
    .filter((e) => e && typeof e === 'object');
  assert.ok(all.length > TOOL_CASES.length, 'sweep should cover every table');
  for (const event of all) {
    assertTriple(mapEvent(event), `event_type=${event.event_type}`);
  }
});

// ---------------------------------------------------------------------------
// ROBUSTNESS — the module must never throw on garbage feed items.
// ---------------------------------------------------------------------------

test('mapEvent never throws on junk input', () => {
  const junk = [undefined, null, 0, 'str', [], {}, { payload: null }, { event_type: 'tool_call' },
    { event_type: 'tool_call', payload: { tool: '' } }, { event_type: 'tool_call', payload: { tool: 42 } },
    { event_type: '  ', payload: {} }, { event_type: 'file_edit', payload: { file: '' } },
    { event_type: 'file_edit', payload: { file: '/' } }, { event_type: 'command_run', payload: { command: '   ' } },
    { event_type: 'state_change' }, { event_type: 'state_change', payload: {} }];
  for (const j of junk) {
    const got = mapEvent(j);
    assertTriple(got, `junk ${JSON.stringify(j)}`);
  }
});

test('mapEvent accepts payload sent as a JSON string (defensive)', () => {
  const got = mapEvent({ id: 1, agent: 'a', event_type: 'file_edit', payload: '{"file":"/x/y/app.py"}' });
  assertTriple(got, 'string payload');
  assert.equal(got.bubble, 'editing app.py');
});

// ---------------------------------------------------------------------------
// idleState (D3) — thresholds as exported constants, boundary-tested.
// ---------------------------------------------------------------------------

test('idle thresholds are exported constants with the ticket values', () => {
  assert.equal(IDLE_COFFEE_MS, 60_000);
  assert.equal(IDLE_SLEEP_MS, 300_000);
  assert.equal(BUBBLE_MAX_LEN, 28);
});

const IDLE_CASES = [
  { ms: 0, want: null },
  { ms: 59_999, want: null },
  { ms: 60_000, want: { zone: 'wait', activity: 'coffee' } },
  { ms: 120_000, want: { zone: 'wait', activity: 'coffee' } },
  { ms: 300_000, want: { zone: 'wait', activity: 'coffee' } },
  { ms: 300_001, want: { zone: 'rest', activity: 'sleeping' } },
  { ms: 8_640_000, want: { zone: 'rest', activity: 'sleeping' } },
  { ms: -1, want: null },
  { ms: NaN, want: null },
  { ms: Infinity, want: null },
  { ms: undefined, want: null },
  { ms: '60000', want: { zone: 'wait', activity: 'coffee' } }, // feed values are strings sometimes
];

for (const c of IDLE_CASES) {
  test(`idleState(${String(c.ms)}) -> ${c.want ? `${c.want.zone}/${c.want.activity}` : 'null'}`, () => {
    const got = idleState(c.ms);
    if (c.want === null) {
      assert.equal(got, null);
    } else {
      assertTriple(got, `idleState(${String(c.ms)})`);
      assert.equal(got.zone, c.want.zone);
      assert.equal(got.activity, c.want.activity);
    }
  });
}

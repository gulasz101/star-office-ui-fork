/**
 * activity.js — the pure data layer of the v3 office renderer (SOU-156).
 *
 * Turns one agent event (an item from GET /events/recent, ADR-035 schema v1:
 * { id, agent, event_type, payload, created_at }) into "where should the
 * character go and what is it doing": { zone, activity, bubble }. No Phaser,
 * no DOM, no dependencies — the renderer imports this; the tests run it under
 * bare `node --test`.
 *
 * Contract (SOU-156, rulings recorded on the ticket):
 *  - zone ∈ work | ops | build | read | wait | rest | entry
 *  - activity ∈ typing | terminal | building | reading | thinking | coffee |
 *    sleeping | arriving | leaving | celebrating ('leaving' is reserved for
 *    the renderer's walk-to-door animation; the map never emits it)
 *  - bubble: human text, <= BUBBLE_MAX_LEN chars. Privacy is hard-wired:
 *    shell bubbles show "$ <program>" plus a subcommand for the well-behaved
 *    CLI families only — never arguments, never URLs, never patterns, never
 *    output. File bubbles show the basename only, never the path.
 *  - mapEvent never throws: a junk feed item still produces a valid triple.
 */

/** Stay-put window: fresher than this -> no idle change. */
export const IDLE_COFFEE_MS = 60_000;
/** Past this the character has drifted onto the couch. */
export const IDLE_SLEEP_MS = 300_000;
/** Hard cap for every bubble, in characters (code points). */
export const BUBBLE_MAX_LEN = 28;

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

/** The feed serialises payload as JSON; accept both shapes, never throw. */
function readPayload(raw) {
  if (isObj(raw)) return raw;
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      return isObj(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }
  return {};
}

/** Fit any human text into the bubble budget without cutting a code point. */
function fit(text) {
  const chars = [...String(text)];
  return chars.length <= BUBBLE_MAX_LEN
    ? String(text)
    : chars.slice(0, BUBBLE_MAX_LEN - 1).join('') + '…';
}

/** 'mcp__server__tool' / 'mcp_tool' -> 'tool'; lowercase, junk -> ''. */
function normTool(raw) {
  if (typeof raw !== 'string' && typeof raw !== 'number') return '';
  let name = String(raw).trim().toLowerCase();
  if (!name) return '';
  if (name.startsWith('mcp__')) {
    const parts = name.split('__').filter(Boolean);
    name = parts.length > 1 ? parts[parts.length - 1] : name.slice(5);
  } else if (name.startsWith('mcp_')) {
    name = name.slice(4);
  }
  return name;
}

/** Basename of a file-ish payload value; '' for anything unusable. */
function basenameOf(value) {
  if (typeof value !== 'string') return '';
  let s = value.trim();
  if (!s) return '';
  s = s.replace(/[/\\]+$/, ''); // trailing separators: '/' or '/repo/'
  const last = s.split(/[/\\]/).pop() || '';
  return last.trim();
}

const FILE_KEYS = ['file', 'path', 'target_file', 'file_path', 'notebook_path'];

/** First usable basename across the payload aliases reporters actually use. */
function fileFrom(payload) {
  for (const key of FILE_KEYS) {
    const base = basenameOf(payload[key]);
    if (base) return base;
  }
  return '';
}

function triple(zone, activity, bubble) {
  return { zone, activity, bubble: fit(bubble) };
}

// ---------------------------------------------------------------------------
// tool table — normalized tool name -> what it means in the office
// ---------------------------------------------------------------------------

const TOOL_KINDS = new Map([
  // Claude Code
  ['edit', 'edit'], ['write', 'edit'], ['multiedit', 'edit'], ['notebookedit', 'edit'],
  ['read', 'read'],
  ['grep', 'search'], ['glob', 'list'], ['list', 'list'],
  ['webfetch', 'fetch'], ['websearch', 'websearch'],
  ['task', 'task'], ['agent', 'agent'], ['todowrite', 'todo'],
  // OpenCode / OpenChamber
  ['bash', 'shell'], ['patch', 'edit'], ['web_fetch', 'fetch'],
  // Hermes
  ['terminal', 'shell'], ['read_file', 'read'], ['write_file', 'edit'],
  ['search_files', 'search'], ['web_search', 'websearch'], ['web_extract', 'fetch'],
  ['execute_code', 'exec'], ['delegate_task', 'delegate'], ['memory', 'memory'],
  ['todo', 'todo'], ['skill_view', 'skill'],
]);

const KIND_SHAPES = {
  search: () => triple('read', 'reading', 'searching files'),
  list: () => triple('read', 'reading', 'listing files'),
  fetch: () => triple('read', 'reading', 'fetching web'),
  websearch: () => triple('read', 'reading', 'searching web'),
  task: () => triple('work', 'thinking', 'running task'),
  agent: () => triple('work', 'thinking', 'spawning agent'),
  delegate: () => triple('work', 'thinking', 'delegating'),
  todo: () => triple('work', 'thinking', 'planning work'),
  memory: () => triple('work', 'thinking', 'jotting notes'),
  skill: () => triple('read', 'reading', 'reading skill'),
  exec: () => triple('ops', 'terminal', 'running code'),
};

function shapeEdit(payload) {
  const base = fileFrom(payload);
  return triple('work', 'typing', base ? `editing ${base}` : 'editing');
}

function shapeRead(payload) {
  const base = fileFrom(payload);
  return triple('read', 'reading', base ? `reading ${base}` : 'reading');
}

function mapToolCall(name, payload) {
  if (!name) return triple('work', 'thinking', 'unknown');
  if (name.startsWith('browser_')) return triple('read', 'reading', 'browsing web');
  const kind = TOOL_KINDS.get(name);
  if (kind === 'edit') return shapeEdit(payload);
  if (kind === 'read') return shapeRead(payload);
  if (kind === 'shell') return shapeCommand(payload.command);
  if (KIND_SHAPES[kind]) return KIND_SHAPES[kind]();
  // unknown tool: still something sensible — thinking, named after the tool
  return triple('work', 'thinking', name);
}

// ---------------------------------------------------------------------------
// shell commands — zone pick + the privacy sanitizer
// ---------------------------------------------------------------------------

// Programs whose bare identifier subcommand is safe and useful to show.
const DISPLAY_SUB_PROGS = new Set(['git', 'npm', 'pnpm', 'yarn', 'docker', 'kubectl', 'cargo', 'go']);
// Always build/test/deploy work, whatever follows them.
const BUILD_ALWAYS = new Set(['make', 'pytest', 'cargo', 'flux', 'helm']);
// Subcommand-scoped build classifiers.
const GIT_BUILD = new Set(['push', 'pull', 'clone']);
const DOCKER_BUILD = new Set(['build']);
const KUBECTL_BUILD = new Set(['apply', 'rollout', 'delete', 'scale', 'patch']);
const GO_BUILD = new Set(['build', 'test', 'run', 'install']);
const RUNNER_BUILD_VERBS = new Set(['build', 'test', 'lint', 'install', 'i', 'add', 'ci']);
const RUNNER_PROGS = new Set(['npm', 'pnpm', 'yarn']);
// Wrapper tokens that are not the real program.
const COMMAND_WRAPPERS = new Set(['sudo', 'doas', 'env', 'time', 'nohup', 'command']);

const SUB_IDENT = /^[a-z][a-z0-9_-]{0,11}$/; // bare verb, never a flag or path
const PROG_SHAPE = /^[a-z0-9._-]{1,20}$/;

function shellSegments(command) {
  return String(command)
    .split(/&&|\|\||;/)
    .map((seg) => seg.trim())
    .filter(Boolean);
}

/** Parse one segment -> { prog, sub, isBuild } with privacy enforced. */
function parseSegment(seg) {
  const tokens = seg.split(/\s+/).filter(Boolean);
  let i = 0;
  while (
    i < tokens.length &&
    (/^[a-z_][a-z0-9_]*=/i.test(tokens[i]) || COMMAND_WRAPPERS.has(tokens[i].toLowerCase()))
  ) {
    i += 1; // skip KEY=VAL assignments and sudo/env/time wrappers
  }
  const raw = tokens[i] || '';
  let prog = raw.split(/[/\\]/).pop().toLowerCase().replace(/\.exe$/, '');
  if (!prog || !PROG_SHAPE.test(prog)) prog = 'shell';

  let sub = tokens[i + 1] ? tokens[i + 1].toLowerCase() : '';
  if (!SUB_IDENT.test(sub)) sub = '';
  let verb = sub;
  if (RUNNER_PROGS.has(prog) && sub === 'run') {
    const after = tokens[i + 2] ? tokens[i + 2].toLowerCase() : '';
    verb = SUB_IDENT.test(after) ? after : '';
  }

  let isBuild = false;
  if (BUILD_ALWAYS.has(prog)) isBuild = true;
  else if (prog === 'git') isBuild = GIT_BUILD.has(verb);
  else if (prog === 'docker') isBuild = DOCKER_BUILD.has(verb);
  else if (prog === 'kubectl') isBuild = KUBECTL_BUILD.has(verb);
  else if (prog === 'go') isBuild = GO_BUILD.has(verb);
  else if (RUNNER_PROGS.has(prog)) isBuild = RUNNER_BUILD_VERBS.has(verb);

  const shown = DISPLAY_SUB_PROGS.has(prog) ? (verb || sub) : '';
  const bubble = '$ ' + prog + (shown ? ` ${shown}` : '');
  return { prog, bubble, isBuild };
}

function shapeCommand(command) {
  if (typeof command !== 'string' || !command.trim()) {
    return triple('ops', 'terminal', '$ shell');
  }
  const parsed = shellSegments(command).map(parseSegment).filter((p) => p.prog);
  if (!parsed.length) return triple('ops', 'terminal', '$ shell');
  const buildOne = parsed.find((p) => p.isBuild);
  if (buildOne) return triple('build', 'building', buildOne.bubble);
  return triple('ops', 'terminal', parsed[0].bubble);
}

// ---------------------------------------------------------------------------
// event types
// ---------------------------------------------------------------------------

const LEGACY_STATES = {
  idle: () => triple('wait', 'coffee', 'coffee break'),
  writing: () => triple('work', 'typing', 'writing'),
  researching: () => triple('read', 'reading', 'researching'),
  executing: () => triple('ops', 'terminal', 'executing'),
  syncing: () => triple('build', 'building', 'syncing'),
  error: () => triple('wait', 'coffee', 'hit an error'),
};

/**
 * One feed item -> { zone, activity, bubble }. Never throws.
 * @param {{event_type?: string, payload?: object|string}} event
 */
export function mapEvent(event) {
  const item = isObj(event) ? event : {};
  const payload = readPayload(item.payload);
  const type = typeof item.event_type === 'string' ? item.event_type.trim().toLowerCase() : '';

  switch (type) {
    case 'session_start':
      return triple('entry', 'arriving', 'arriving');
    case 'session_end':
      return triple('rest', 'sleeping', 'signing off');
    case 'code_completion':
      return triple('rest', 'celebrating', 'done!');
    case 'file_edit':
      return shapeEdit(payload);
    case 'command_run':
      return shapeCommand(payload.command);
    case 'itsaplan_issue_transition':
      return triple('work', 'thinking', 'updating board');
    case 'tool_call':
      return mapToolCall(normTool(payload.tool), payload);
    case 'state_change': {
      const state = typeof payload.state === 'string' ? payload.state.trim().toLowerCase() : '';
      if (LEGACY_STATES[state]) return LEGACY_STATES[state]();
      return triple('work', 'thinking', state || 'unknown');
    }
    default: {
      // any other event type still maps to something sensible:
      // thinking, named after its tool if it has one, else the event type
      const name = normTool(payload.tool);
      if (name) return triple('work', 'thinking', name);
      return triple('work', 'thinking', type || 'unknown');
    }
  }
}

/**
 * Idle fallback for the renderer: given ms since the last event, decide
 * whether the character should drift somewhere. null means "stay put".
 * @param {number|string} msSinceLastEvent
 * @returns {{zone: string, activity: string, bubble: string} | null}
 */
export function idleState(msSinceLastEvent) {
  const ms = Number(msSinceLastEvent);
  if (!Number.isFinite(ms) || ms < 0) return null;
  if (ms < IDLE_COFFEE_MS) return null;
  if (ms <= IDLE_SLEEP_MS) return triple('wait', 'coffee', 'coffee break');
  return triple('rest', 'sleeping', 'sleeping');
}

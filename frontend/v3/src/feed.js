// Pure decision logic for the live event feed (SOU-160). No Phaser, no fetch: main.js does
// the I/O, the Node tests exercise this directly.
import { mapEvent, idleState } from '../activity.js';

// The v1 renderer seeds a demo main agent called "Star" into the feed; v3 has no privileged
// main agent, so it never gets an office.
export const IGNORED_AGENTS = new Set(['Star']);

// Agents whose newest event is older than this don't get an office on page load. Within it
// they keep their room, asleep with the lights off if they've been idle (SOU-197).
export const ACTIVE_WINDOW_MS = 24 * 60 * 60_000;

// Background work (SOU-211): helpers in the room, never a reason to move the character.
export const BACKGROUND_TYPES = new Set(['background_start', 'background_end']);
// On page load, a start older than this without an end is assumed lost.
export const BACKGROUND_OPEN_MS = 3 * 60 * 60_000;

const ts = (e) => Date.parse(e.created_at) || 0;
const byTime = (a, b) => ts(a) - ts(b) || a.id - b.id;

export class FeedState {
  constructor() {
    this.seen = new Set();
    this.cursor = null; // newest created_at seen, sent back as ?since= (inclusive)
    this.agents = new Map(); // name -> { lastAt, last: {zone,activity,bubble} }
    this.bgQueue = []; // background events not yet taken by the renderer, oldest first
  }

  /**
   * Background work since the last call, oldest first:
   * [{ agent, op: 'start'|'end', task: {id, kind, label, status?, exit_code?}, at }].
   */
  takeBackground() {
    const out = this.bgQueue;
    this.bgQueue = [];
    return out;
  }

  /**
   * Feed one poll response (any order). Returns the targets to apply now, one per agent:
   * [{ agent, target: {zone, activity, bubble}, at }]. Within a batch only the newest event
   * per agent counts, so a burst of tool calls doesn't make the character zigzag.
   * @param {Array} events items from GET /events/recent
   * @param {{initial?: boolean, now?: number}} opts initial = first load: only agents
   *   active within ACTIVE_WINDOW_MS are returned
   */
  ingest(events, { initial = false, now = Date.now() } = {}) {
    const fresh = (events || []).filter((e) => e && e.agent && !this.seen.has(e.id)).sort(byTime);
    const latest = new Map();
    const bg = [];
    for (const e of fresh) {
      // ignored agents still advance the cursor, or the since-floor would stall on them
      this.seen.add(e.id);
      if (!this.cursor || ts(e) > Date.parse(this.cursor)) this.cursor = e.created_at;
      if (IGNORED_AGENTS.has(e.agent)) continue;
      if (BACKGROUND_TYPES.has(e.event_type)) bg.push(e);
      else latest.set(e.agent, e);
    }
    this._queueBackground(bg, initial, now);
    if (this.seen.size > 5000) this.seen = new Set([...this.seen].slice(-2000));

    const out = [];
    for (const [agent, e] of latest) {
      const at = ts(e);
      if (initial && now - at > ACTIVE_WINDOW_MS) continue;
      const target = mapEvent(e);
      this.agents.set(agent, { lastAt: at, last: target });
      out.push({ agent, target, at });
    }
    return out;
  }

  _queueBackground(events, initial, now) {
    const items = [];
    for (const e of events) {
      const p = e.payload && typeof e.payload === 'object' ? e.payload : {};
      const id = typeof p.task_id === 'string' ? p.task_id : '';
      if (!id) continue;
      const task = {
        id, kind: p.kind === 'agent' ? 'agent' : 'shell',
        label: typeof p.label === 'string' ? p.label.slice(0, 28) : '',
        status: p.status, exit_code: p.exit_code,
      };
      items.push({ agent: e.agent, op: e.event_type === 'background_start' ? 'start' : 'end', task, at: ts(e) });
    }
    if (initial) {
      // page load: only what is still running, no replay of finished helpers
      const open = new Map();
      for (const it of items) {
        const key = `${it.agent}\u0000${it.task.id}`;
        if (it.op === 'start' && now - it.at < BACKGROUND_OPEN_MS) open.set(key, it);
        else open.delete(key);
      }
      this.bgQueue.push(...open.values());
    } else {
      this.bgQueue.push(...items);
    }
  }

  /**
   * Idle escalation for every known agent at time `now`. Returns targets that differ from
   * what the agent is currently doing. An agent resting (session ended, celebrating) is
   * never dragged back to the coffee machine; it can only fall asleep.
   */
  idle(now = Date.now()) {
    const out = [];
    for (const [agent, s] of this.agents) {
      const t = idleState(now - s.lastAt);
      if (!t) continue;
      if (s.last.zone === 'rest' && t.zone !== 'rest') continue;
      if (t.zone === s.last.zone && t.activity === s.last.activity) continue;
      s.last = t;
      out.push({ agent, target: t, at: now });
    }
    return out;
  }
}

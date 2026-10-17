// RoomCard (SOU-203): the focused room's agent, what it's doing and its recent events, in a
// small panel. Text only goes through activity.js (the same sanitiser the bubbles use) and
// textContent, never raw payloads or innerHTML.
import { mapEvent } from '../activity.js';
import { ROOM_NAMES } from './rooms.js';
import { agentEvents } from './api.js';

const REFRESH_MS = 3000;

const time = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(+d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
};

function ago(iso) {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (!Number.isFinite(s)) return 'never';
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

// background_start/_end read as "▶ $ sleep" / "✓ Explore" (labels are reporter-sanitised)
function background(e) {
  if (e.event_type !== 'background_start' && e.event_type !== 'background_end') return null;
  const p = e.payload && typeof e.payload === 'object' ? e.payload : {};
  const what = `${p.kind === 'agent' ? '' : '$ '}${typeof p.label === 'string' ? p.label.slice(0, 24) : 'task'}`;
  if (e.event_type === 'background_start') return { bubble: `▶ ${what}` };
  const failed = p.status === 'error' || p.exit_code > 0;
  return { bubble: `${failed ? '✗' : '✓'} ${what}` };
}

export class RoomCard {
  constructor() {
    this.root = document.getElementById('card');
    this.office = null;
    this.events = [];
    this.timer = null;
  }

  show(office) {
    this.office = office;
    this.events = [];
    this.render();
    this.root.classList.add('open');
    clearInterval(this.timer);
    this.refresh();
    this.timer = setInterval(() => this.refresh(), REFRESH_MS);
  }

  hide() {
    this.office = null;
    clearInterval(this.timer);
    this.root.classList.remove('open');
  }

  async refresh() {
    const o = this.office;
    if (!o) return;
    try {
      const events = await agentEvents(o.agentId, 8);
      if (this.office === o) this.events = events;
    } catch {
      // keep the last list; the card still shows the live activity
    }
    if (this.office === o) this.render();
  }

  render() {
    const o = this.office;
    if (!o) return;
    // while walking, the target is what it's about to do
    const st = (o.character.walking && o.character.target) || o.character.state || {};
    const root = this.root;
    root.replaceChildren();
    const head = el('div', 'card-head');
    head.append(el('span', 'card-name', o.agentId), el('span', 'card-room', ROOM_NAMES[o.roomType] || o.roomType));
    root.append(head);
    const now = el('div', 'card-now');
    now.append(el('span', 'card-label', 'now '), el('span', '', [st.activity, st.bubble].filter(Boolean).join(' · ') || 'arriving'));
    root.append(now);
    if (this.events.length) root.append(el('div', 'card-seen', `last seen ${ago(this.events[0].created_at)}`));
    const bg = o.helpers ? o.helpers.list() : [];
    if (bg.length) {
      const box = el('div', 'card-bg');
      box.append(el('span', 'card-label', `background (${bg.length}) `));
      box.append(el('span', '', bg.map((t) => `${t.kind === 'agent' ? '◆' : '$'} ${t.label || t.kind} ${ago(new Date(t.startedAt).toISOString()).replace(' ago', '')}`).join(' · ')));
      root.append(box);
    }
    const list = el('ol', 'card-events');
    for (const e of this.events) {
      const t = background(e) || mapEvent(e);
      const li = el('li');
      li.append(el('span', 'card-time', time(e.created_at)), el('span', 'card-type', e.event_type), el('span', 'card-what', t.bubble || t.activity));
      list.append(li);
    }
    if (!this.events.length) list.append(el('li', 'card-empty', 'no events yet'));
    root.append(list);
    root.append(el('div', 'card-hint', 'Esc · back to the lodge   F · next room'));
  }
}

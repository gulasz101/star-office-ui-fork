// Which face is the page served from (SOU-221)? The public viewer (SOU-220, SOU_MODE=public)
// only has /public/feed and /public/agents; dev and ingest have /events/recent and
// /events/agents. One probe decides, and everything that reads the feed goes through here.
let face = null;

export async function detectFace() {
  if (face) return face;
  try {
    const res = await fetch('/public/agents', { cache: 'no-store' });
    face = res.ok ? 'public' : 'dev';
  } catch {
    face = 'dev';
  }
  return face;
}

export const currentFace = () => face || 'dev';

// Public items carry no id on purpose (nothing enumerable). The feed de-duplicates by id, so
// derive a stable numeric one from the item's content (32-bit FNV-1a; collisions are harmless:
// at worst one duplicate event is skipped).
export function contentId(e) {
  const s = `${e.agent}|${e.event_type}|${e.created_at}|${JSON.stringify(e.payload || {})}`;
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h;
}

export const normalise = (e) => (e && e.id === undefined ? { ...e, id: contentId(e) } : e);

export const agentsUrl = () => (currentFace() === 'public' ? '/public/agents' : '/events/agents');

export const feedUrl = (q) => `${currentFace() === 'public' ? '/public/feed' : '/events/recent'}?${q}`;

// one agent's recent events, for the room card (the public feed has no agent filter)
export async function agentEvents(agent, limit = 8) {
  if (currentFace() === 'public') {
    const res = await fetch('/public/feed?limit=200', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const mine = (await res.json()).filter((e) => e.agent === agent).slice(0, limit);
    if (mine.length) return mine;
    // a quiet agent's events are older than the newest 200: show at least its last one
    const agents = await fetch('/public/agents', { cache: 'no-store' }).then((r) => (r.ok ? r.json() : []));
    const entry = agents.find((a) => a.agent === agent);
    return entry && entry.last_event ? [entry.last_event] : [];
  }
  const res = await fetch(`/events/recent?agent=${encodeURIComponent(agent)}&limit=${limit}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

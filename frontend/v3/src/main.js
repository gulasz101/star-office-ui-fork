import { COLORS, TILE } from './config.js';
import { Building } from './building.js';
import { FeedState } from './feed.js';
import { CameraRig } from './camera.js';
import { snowfall, CorridorCat } from './ambience.js';
import { RoomCard } from './card.js';
import { ROOM_TYPES, PROP_ART } from './rooms.js';
import { LODGE_PROPS, lodgePropKey } from './lodge.js';

export const ART_SET = 'office-01';

// Default: live. Offices appear as agents show up in GET /events/recent.
// ?agents=a,b pins offices instead (no feed); ?demo=1 cycles zones on the pinned offices.
const params = new URLSearchParams(location.search);
const PINNED = (params.get('agents') || '').split(',').map((s) => s.trim()).filter(Boolean);
const DEMO = params.get('demo') === '1';
const LIVE = !PINNED.length && !DEMO;
const POLL_MS = 1500;
const hud = (text) => {
  const el = document.getElementById('status');
  if (el) el.textContent = text;
};

class OfficeScene extends Phaser.Scene {
  constructor() {
    super('office');
  }

  preload() {
    for (const t of ROOM_TYPES) this.load.tilemapTiledJSON(`room-${t}`, `maps/rooms/${t}.json`);
    this.load.image('tiles-placeholder', 'assets/placeholder/tiles.png');
    // Generated art (SOU-158 contract). Any file that's missing falls back to its placeholder,
    // so a partial art drop renders fine. ?art=0 forces placeholders for comparison.
    if (params.get('art') !== '0') {
      const base = `assets/${ART_SET}`;
      this.load.image(`tiles-${ART_SET}`, `${base}/tiles.png`);
      for (const [name, path] of Object.entries(PROP_ART)) this.load.image(`prop-${name}`, path);
      this.load.spritesheet(`sheet-${ART_SET}`, `${base}/character/${ART_SET}.png`, { frameWidth: 32, frameHeight: 48 });
      // the Lodge set (SOU-198) is optional until it lands; lodge.js draws what's missing
      if (params.get('lodge') !== '0') {
        this.load.image('lodge-tiles', 'assets/lodge/tiles.png');
        for (const name of Object.keys(LODGE_PROPS)) this.load.image(lodgePropKey(name), `assets/lodge/props/${name}.png`);
        this.load.spritesheet('lodge-cat-walk', 'assets/lodge/props/cat_walk.png', { frameWidth: 32, frameHeight: 32 });
      }
      this.load.on('loaderror', (file) => console.info(`[v3] art fallback: ${file.key} not found`));
    }
  }

  create() {
    this.cameras.main.setBackgroundColor(COLORS.background);
    this.building = new Building(this);
    for (const id of PINNED.length ? PINNED : DEMO ? ['claude-code'] : []) this.building.addOffice(id, { arrive: false });

    this.card = new RoomCard();
    this.events.on('room-focus', (o) => (o ? this.card.show(o) : this.card.hide()));
    this.rig = new CameraRig(this, this.building);
    this.snow = snowfall(this, this.building);
    this.events.on('building-grew', () => {
      this.snow.destroy();
      this.snow = snowfall(this, this.building);
    });
    // owner: "more agents <=> more cats in corridor": one cat per room, at least one, at most 8
    this.cats = [];
    const topUpCats = () => {
      const want = Math.max(1, Math.min(8, this.building.offices.size));
      while (this.cats.length < want) this.cats.push(new CorridorCat(this, this.building));
    };
    topUpCats();
    this.events.on('building-grew', topUpCats);

    const toggleDebug = () => {
      for (const o of this.building.offices.values()) o.toggleDebug();
    };
    this.input.keyboard.on('keydown-G', toggleDebug);
    if (params.get('debug') === '1') toggleDebug();

    // handle for the browser console and for tests
    window.office = {
      scene: this,
      building: this.building,
      offices: () => [...this.building.offices.keys()],
      // office.agent('claude-code').do({ zone: 'build', activity: 'building', bubble: '$ docker build' })
      // office.bg('claude-code', 'start', { id: 't1', kind: 'shell', label: 'sleep' })
      bg: (id, op, task) => this.applyBackground([{ agent: id, op, task, at: Date.now() }]),
      agent: (id) => {
        const o = this.building.offices.get(id) || [...this.building.offices.values()][0];
        return o && o.character;
      },
    };
    if (DEMO) this.demo();
    if (LIVE) this.startLive();
  }

  // arrive = walk in from the bottom of the world; agents found already idle on page load
  // start inside their room instead
  office(agent, arrive = true) {
    if (!this.building.offices.has(agent)) {
      this.building.addOffice(agent, { arrive });
    }
    return this.building.offices.get(agent);
  }

  apply(updates) {
    const now = Date.now();
    for (const { agent, target, at } of updates) {
      const o = this.office(agent, !at || now - at < 120_000);
      o.character.do(target);
      o.setLights(target.activity !== 'sleeping');
    }
    this.caption();
  }

  // background work (SOU-211): helpers in the agent's room, the character stays put
  applyBackground(items) {
    for (const { agent, op, task, at } of items) {
      const o = this.office(agent, !at || Date.now() - at < 120_000);
      if (op === 'start') o.helpers.start(task);
      else o.helpers.end(task);
    }
  }

  // the empty and the all-asleep states say so instead of showing a silent screen
  caption() {
    const el = document.getElementById('caption');
    if (!el) return;
    const offices = [...this.building.offices.values()];
    let text = '';
    if (!offices.length) text = 'nobody here yet · the lodge is waiting for its first agent';
    else if (offices.every((o) => !o.lightsOn)) text = 'the lodge is asleep · nobody is working right now';
    el.textContent = text;
    el.style.opacity = text ? '1' : '0';
  }

  startLive() {
    this.feed = new FeedState();
    window.office.feed = this.feed;
    let first = true;
    let busy = false;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        // SOU-201: the first load starts from /events/agents — every agent
        // active in the last 24 h with its newest event — so one busy agent
        // can no longer monopolise the 200-row recent window and hide the
        // quiet ones. Older backend (no endpoint) or an unexpected body:
        // fall through to plain /events/recent discovery, as before.
        if (first) {
          try {
            const seed = await fetch('/events/agents', { cache: 'no-store' });
            if (seed.ok) {
              const agents = await seed.json();
              if (Array.isArray(agents)) {
                this.apply(this.feed.ingest(agents.map((r) => r && r.last_event), { initial: true }));
              }
            }
          } catch { /* no /events/agents yet; the recent poll below still discovers everyone */ }
        }
        const q = new URLSearchParams({ limit: '200' });
        if (this.feed.cursor) q.set('since', this.feed.cursor);
        const res = await fetch(`/events/recent?${q}`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        // ingest is idempotent per event id, so rows the seed already applied
        // here change nothing; initial stays true until this poll succeeds
        this.apply(this.feed.ingest(await res.json(), { initial: first }));
        this.applyBackground(this.feed.takeBackground());
        first = false;
        const n = this.building.offices.size;
        hud(n ? `live · ${n} agent${n > 1 ? 's' : ''} · ${new Date().toLocaleTimeString()}` : 'live · waiting for the first agent event…');
        this.caption();
      } catch (err) {
        hud(`feed error: ${err.message} (retrying)`);
      } finally {
        busy = false;
      }
    };
    poll();
    this.time.addEvent({ delay: POLL_MS, loop: true, callback: poll });
    this.time.addEvent({ delay: 1000, loop: true, callback: () => this.apply(this.feed.idle()) });
  }

  // ?demo=1 cycles the agent through every zone, for eyeballing and screenshots
  demo() {
    const steps = [
      { zone: 'work', activity: 'typing', bubble: 'editing app.py' },
      { zone: 'ops', activity: 'terminal', bubble: '$ kubectl' },
      { zone: 'read', activity: 'reading', bubble: 'reading docs' },
      { zone: 'build', activity: 'building', bubble: '$ docker build' },
      { zone: 'wait', activity: 'coffee', bubble: 'coffee break' },
      { zone: 'rest', activity: 'sleeping', bubble: 'sleeping' },
    ];
    let i = 0;
    const next = () => {
      for (const o of this.building.offices.values()) {
        o.character.do(steps[i % steps.length]);
        o.setLights(steps[i % steps.length].activity !== 'sleeping');
      }
      i++;
    };
    next();
    this.time.addEvent({ delay: 4500, loop: true, callback: next });
  }
}

new Phaser.Game({
  type: Phaser.AUTO,
  parent: 'game',
  backgroundColor: COLORS.background,
  pixelArt: true,
  scale: {
    mode: Phaser.Scale.RESIZE,
    width: window.innerWidth,
    height: window.innerHeight,
  },
  scene: [OfficeScene],
});

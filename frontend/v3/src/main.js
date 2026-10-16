import { COLORS, TILE } from './config.js';
import { Building } from './building.js';
import { FeedState } from './feed.js';

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
    this.load.tilemapTiledJSON('office-01', 'maps/office-01.json');
    this.load.image('tiles-placeholder', 'assets/placeholder/tiles.png');
  }

  create() {
    this.cameras.main.setBackgroundColor(COLORS.background);
    this.building = new Building(this, 'office-01');
    for (const id of PINNED.length ? PINNED : DEMO ? ['claude-code'] : []) this.building.addOffice(id);

    this.fitCamera();
    this.scale.on('resize', () => this.fitCamera());

    const toggleDebug = () => {
      for (const o of this.building.offices.values()) o.toggleDebug();
    };
    this.input.keyboard.on('keydown-D', toggleDebug);
    if (params.get('debug') === '1') toggleDebug();

    // handle for the browser console and for tests
    window.office = {
      scene: this,
      building: this.building,
      offices: () => [...this.building.offices.keys()],
      // office.agent('claude-code').do({ zone: 'build', activity: 'building', bubble: '$ docker build' })
      agent: (id) => {
        const o = this.building.offices.get(id) || [...this.building.offices.values()][0];
        return o && o.character;
      },
    };
    if (DEMO) this.demo();
    if (LIVE) this.startLive();
  }

  office(agent) {
    if (!this.building.offices.has(agent)) {
      this.building.addOffice(agent);
      this.fitCamera();
    }
    return this.building.offices.get(agent);
  }

  apply(updates) {
    for (const { agent, target } of updates) this.office(agent).character.do(target);
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
        const q = new URLSearchParams({ limit: '200' });
        if (this.feed.cursor) q.set('since', this.feed.cursor);
        const res = await fetch(`/events/recent?${q}`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        this.apply(this.feed.ingest(await res.json(), { initial: first }));
        first = false;
        const n = this.building.offices.size;
        hud(n ? `live · ${n} agent${n > 1 ? 's' : ''} · ${new Date().toLocaleTimeString()}` : 'live · waiting for the first agent event…');
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
      for (const o of this.building.offices.values()) o.character.do(steps[i % steps.length]);
      i++;
    };
    next();
    this.time.addEvent({ delay: 4500, loop: true, callback: next });
  }

  // Largest integer zoom that fits the whole building with a small margin.
  fitCamera() {
    const cam = this.cameras.main;
    const b = this.building.bounds;
    const margin = TILE / 4;
    const zx = Math.floor(this.scale.width / (b.width + margin * 2));
    const zy = Math.floor(this.scale.height / (b.height + margin * 2));
    const zoom = Math.max(1, Math.min(zx, zy));
    cam.setZoom(zoom);
    cam.centerOn(b.centerX, b.centerY);
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

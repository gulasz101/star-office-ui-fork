import { COLORS, TILE } from './config.js';
import { Building } from './building.js';
import { FeedState } from './feed.js';
import { CameraRig } from './camera.js';
import { weather, Cats } from './ambience.js';
import { RoomCard } from './card.js';
import { Roster } from './roster.js';
import { detectFace, agentsUrl, feedUrl, normalise } from './api.js';
import { ROOM_TYPES, PROP_ART, SHEET_PROPS } from './rooms.js';
import { LODGE_PROPS, lodgePropKey } from './lodge.js';
import { SEASONS, TREE_FILES, pickSeason } from './seasons.js';
import { COATS, CAT_FRAME_W, CAT_FRAME_H, FIGHT_CLOUD, coatKey, coatFile, pickCoat } from './cats.js';

export const ART_SET = 'office-01';

// Default: live. Offices appear as agents show up in GET /events/recent.
// ?agents=a,b pins offices instead (no feed); ?demo=1 cycles zones on the pinned offices.
const params = new URLSearchParams(location.search);
const PINNED = (params.get('agents') || '').split(',').map((s) => s.trim()).filter(Boolean);
const DEMO = params.get('demo') === '1';
const LIVE = !PINNED.length && !DEMO;
const POLL_MS = 1500;

// SOU-273: one season per page load — ?season=<name> pins it, otherwise it is random. The ground,
// path, trees, night tint and weather outside the Lodge all come from this one choice.
const SEASON = pickSeason(location.search);
const SEASON_LABEL = SEASONS[SEASON].label;
// SOU-278: ?cats=0 puts every cat back on the Lodge's single sheet (the pre-ticket look), and
// ?art=0 is the no-generated-art comparison mode, so neither loads the seven coat files.
const CAT_ART = params.get('art') !== '0' && params.get('cats') !== '0';
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
      for (const [name, [path, frameWidth, frameHeight]] of Object.entries(SHEET_PROPS)) {
        this.load.spritesheet(`prop-${name}`, path, { frameWidth, frameHeight });
      }
      this.load.spritesheet(`sheet-${ART_SET}`, `${base}/character/${ART_SET}.png`, { frameWidth: 32, frameHeight: 48 });
      // SOU-278: the seven coats and the fight cloud (SOU-277's art). A file that is not there yet is
      // no problem — ambience.js draws a placeholder sheet under the same key, so the coat still
      // exists and the real PNG takes over the day it lands.
      if (CAT_ART) {
        for (const coat of COATS) {
          this.load.spritesheet(coatKey(coat), coatFile(coat), { frameWidth: CAT_FRAME_W, frameHeight: CAT_FRAME_H });
        }
        this.load.spritesheet(FIGHT_CLOUD.key, FIGHT_CLOUD.file, { frameWidth: FIGHT_CLOUD.frameWidth, frameHeight: FIGHT_CLOUD.frameHeight });
      }
      // the Lodge set (SOU-198) is optional until it lands; lodge.js draws what's missing
      if (params.get('lodge') !== '0') {
        this.load.image('lodge-tiles', 'assets/lodge/tiles.png');
        this.load.image('lodge-soft-tiles', 'assets/lodge/tiles_soft.png');
        this.load.image('garage-tiles', 'assets/garage/tiles.png');
        for (const name of Object.keys(LODGE_PROPS)) this.load.image(lodgePropKey(name), `assets/lodge/props/${name}.png`);
        this.load.spritesheet('lodge-cat-walk', 'assets/lodge/props/cat_walk.png', { frameWidth: 32, frameHeight: 32 });
      }
      // SOU-273: the chosen season's own art (SOU-274..276 deliver it). A 404 is fine — lodge.js
      // draws the tile or tree it has no texture for. Only the chosen season is loaded.
      if (SEASON !== 'winter') {
        this.load.image(`season-tiles-${SEASON}`, `assets/seasons/${SEASON}/tiles.png`);
        SEASONS[SEASON].trees.forEach((name, i) => {
          this.load.image(lodgePropKey(name), `assets/seasons/${SEASON}/props/${TREE_FILES[i]}.png`);
        });
      }
      this.load.on('loaderror', (file) => console.info(`[v3] art fallback: ${file.key} not found`));
    }
  }

  create() {
    this.cameras.main.setBackgroundColor(COLORS.background);
    for (const [name, [, , , frames, frameRate]] of Object.entries(SHEET_PROPS)) {
      if (!this.textures.exists(`prop-${name}`)) continue;
      this.anims.create({ key: `prop-anim-${name}`, frames: this.anims.generateFrameNumbers(`prop-${name}`, { start: 0, end: frames - 1 }), frameRate, repeat: -1 });
    }
    this.building = new Building(this, SEASON);
    for (const id of PINNED.length ? PINNED : DEMO ? ['claude-code'] : []) this.building.addOffice(id, { arrive: false });

    this.card = new RoomCard();
    this.events.on('room-focus', (o) => (o ? this.card.show(o) : this.card.hide()));
    this.rig = new CameraRig(this, this.building);
    this.roster = new Roster((o) => this.rig.focus(o));
    const renderRoster = () => this.roster.render(this.building.offices.values(), this.rig.focused);
    this.events.on('room-focus', renderRoster);
    this.events.on('building-grew', renderRoster);
    this.time.addEvent({ delay: 1000, loop: true, callback: renderRoster });
    this.weather = weather(this, this.building, SEASON);
    this.events.on('building-grew', () => {
      this.weather.destroy();
      this.weather = weather(this, this.building, SEASON);
    });
    // owner: "more agents <=> more cats in corridor": one cat per room, at least one, at most 8
    // SOU-278: main.js hands out the coats — each new cat wears one no other cat has until all
    // seven have been out, after which any coat may come round again.
    const coats = [];
    this.cats = new Cats(this, this.building, {
      art: CAT_ART,
      nextCoat: () => {
        const coat = pickCoat(Math.random, coats);
        coats.push(coat);
        return coat;
      },
    });
    const topUpCats = () => this.cats.topUp();
    topUpCats();
    this.events.on('building-grew', topUpCats);
    // the recording hook this ticket asks for: window.__lodge.cats.force('fight') runs one
    // interaction on the nearest pair, ignoring the roll and the cooldown (a second fight while one
    // is already on screen is still refused — that rule has no exception)
    window.__lodge = { cats: this.cats, scene: this };

    const toggleDebug = () => {
      for (const o of this.building.offices.values()) o.toggleDebug();
    };
    this.input.keyboard.on('keydown-G', toggleDebug);
    if (params.get('debug') === '1') toggleDebug();

    // handle for the browser console and for tests
    window.office = {
      scene: this,
      building: this.building,
      season: SEASON,
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
    // the season is part of the caption in every mode, not just while the feed is up
    else hud(`${PINNED.length ? 'pinned' : 'demo'} · ${SEASON_LABEL}`);
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
            await detectFace();
            const seed = await fetch(agentsUrl(), { cache: 'no-store' });
            if (seed.ok) {
              const agents = await seed.json();
              if (Array.isArray(agents)) {
                this.apply(this.feed.ingest(agents.map((r) => r && normalise(r.last_event)), { initial: true }));
              }
            }
          } catch { /* no /events/agents yet; the recent poll below still discovers everyone */ }
        }
        const q = new URLSearchParams({ limit: '200' });
        if (this.feed.cursor) q.set('since', this.feed.cursor);
        const res = await fetch(feedUrl(q), { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        // ingest is idempotent per event id, so rows the seed already applied
        // here change nothing; initial stays true until this poll succeeds
        this.apply(this.feed.ingest((await res.json()).map(normalise), { initial: first }));
        this.applyBackground(this.feed.takeBackground());
        first = false;
        const n = this.building.offices.size;
        hud(n
          ? `live · ${SEASON_LABEL} · ${n} agent${n > 1 ? 's' : ''}`
          : `live · ${SEASON_LABEL} · waiting for the first agent event…`);
        this.caption();
      } catch (err) {
        hud(`live · ${SEASON_LABEL} · feed error: ${err.message} (retrying)`);
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

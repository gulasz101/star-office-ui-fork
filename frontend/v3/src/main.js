import { COLORS, TILE } from './config.js';
import { Building } from './building.js';

// ?agents=claude,hermes shows one office per agent (M2 preview); default is one office.
const params = new URLSearchParams(location.search);
const AGENTS = (params.get('agents') || 'claude-code').split(',').map((s) => s.trim()).filter(Boolean);

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
    for (const id of AGENTS) this.building.addOffice(id);

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
    if (params.get('demo') === '1') this.demo();
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

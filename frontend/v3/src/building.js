// Building: the list of offices and where each one goes in the world. M1 has one office;
// M2 (SOU-161) replaces `slotFor` with the real growth pattern.
import { OFFICE_W, OFFICE_GAP, TILE } from './config.js';
import { OfficeModule } from './office.js';

const AGENT_COLORS = [0x4f7cc9, 0xc94f6d, 0x4fc98a, 0xc9a24f, 0x8a4fc9, 0x4fbfc9];

export class Building {
  constructor(scene, mapKey) {
    this.scene = scene;
    this.mapKey = mapKey;
    this.offices = new Map();
  }

  slotFor(index) {
    return { originX: index * (OFFICE_W + OFFICE_GAP), originY: 0 };
  }

  addOffice(agentId) {
    if (this.offices.has(agentId)) return this.offices.get(agentId);
    const index = this.offices.size;
    const office = new OfficeModule(this.scene, {
      mapKey: this.mapKey,
      agentId,
      ...this.slotFor(index),
      color: AGENT_COLORS[index % AGENT_COLORS.length],
    });
    this.offices.set(agentId, office);
    return office;
  }

  get bounds() {
    const rects = [...this.offices.values()].map((o) => o.bounds);
    if (!rects.length) return new Phaser.Geom.Rectangle(0, 0, TILE, TILE);
    const x0 = Math.min(...rects.map((r) => r.x));
    const y0 = Math.min(...rects.map((r) => r.y));
    const x1 = Math.max(...rects.map((r) => r.right));
    // + one tile below for the name tags under each door
    const y1 = Math.max(...rects.map((r) => r.bottom)) + TILE;
    return new Phaser.Geom.Rectangle(x0, y0, x1 - x0, y1 - y0);
  }
}

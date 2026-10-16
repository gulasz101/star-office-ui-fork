// Background helpers (SOU-211): an agent's background work, visible in its room.
//   kind "shell" -> a little hover bot by the rack, labelled with the safe command label
//   kind "agent" -> a mini-me (the agent's own sheet, smaller) reading by the bookshelf
// They come in through the door, work, pop a check (or a cross) when done and leave.
import { TILE } from './config.js';

const MAX_SHOWN = 6;
const STEP_MS = 260;
const MINI_SCALE = 0.7;
const SPOT_RADIUS = 4;

export function ensureHelperTextures(scene) {
  if (scene.textures.exists('ph-bot')) return;
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  g.fillStyle(0x2a2f3a).fillRect(7, 0, 2, 4); // antenna mast
  g.fillStyle(0x8fa3b8).fillRect(2, 4, 12, 9); // head
  g.fillStyle(0xb7c7d8).fillRect(2, 4, 12, 2);
  g.fillStyle(0x1d2a3a).fillRect(4, 7, 8, 4); // visor
  g.fillStyle(0x4fe0b8).fillRect(5, 8, 2, 2).fillRect(9, 8, 2, 2); // eyes
  g.fillStyle(0x6f8399).fillRect(4, 13, 8, 5); // body
  g.fillStyle(0xff9a3a).fillRect(6, 14, 4, 2); // chest light
  g.fillStyle(0x4a5668).fillRect(3, 18, 3, 2).fillRect(10, 18, 3, 2); // thrusters
  g.generateTexture('ph-bot', 16, 20);
  g.clear();
  g.fillStyle(0x000000, 0.35).fillEllipse(8, 3, 14, 5);
  g.generateTexture('ph-shadow', 16, 6);
  g.destroy();
}

const label = (scene, text) => scene.add.text(0, 0, text, {
  fontFamily: 'monospace', fontSize: '8px', color: '#f4ead2', stroke: '#1d1f2a', strokeThickness: 3, resolution: 4,
}).setOrigin(0.5, 1);

export class Helpers {
  constructor(office) {
    this.office = office;
    this.scene = office.scene;
    this.items = new Map(); // task id -> helper
    this.hidden = new Map(); // task id -> task, over MAX_SHOWN
    this.moreText = null;
    ensureHelperTextures(this.scene);
    this.scene.events.on('update', this._sync, this);
  }

  // running background work, for the room card
  list() {
    return [...this.items.values()].filter((h) => !h.leaving).map((h) => h.task)
      .concat([...this.hidden.values()]);
  }

  start(task) {
    if (this.items.has(task.id) || this.hidden.has(task.id)) return;
    task.startedAt = Date.now();
    if ([...this.items.values()].filter((h) => !h.leaving).length >= MAX_SHOWN) {
      this.hidden.set(task.id, task);
      return this._more();
    }
    this.items.set(task.id, task.kind === 'agent' ? this._mini(task) : this._bot(task));
  }

  end(task) {
    if (this.hidden.delete(task.id)) return this._more();
    const h = this.items.get(task.id);
    if (!h || h.leaving) return;
    h.leaving = true;
    const ok = task.status !== 'error' && !(task.exit_code > 0);
    const mark = this.scene.add.text(h.sprite.x, h.sprite.y - 30, ok ? '✓' : '✗', {
      fontFamily: 'monospace', fontSize: '14px', color: ok ? '#7cff6a' : '#ff5a5a', stroke: '#1d1f2a', strokeThickness: 4,
    }).setOrigin(0.5, 1).setDepth(100003);
    this.scene.tweens.add({ targets: mark, y: mark.y - 12, alpha: { from: 1, to: 0 }, duration: 1400, onComplete: () => mark.destroy() });
    this.scene.time.delayedCall(900, () => this._leave(h, task.id));
  }

  // ---- placement --------------------------------------------------------------------

  // free tiles around a zone (room-local), nearest first, not taken by another helper
  _spot(zoneName) {
    const o = this.office;
    const z = o.zones[zoneName] || o.zones.entry;
    const zoneTiles = new Set(Object.values(o.zones).map((q) => `${q.tx},${q.ty}`));
    const taken = new Set([...this.items.values()].map((h) => h.tileKey));
    const cands = [];
    for (let dy = -SPOT_RADIUS; dy <= SPOT_RADIUS; dy++) {
      for (let dx = -SPOT_RADIUS; dx <= SPOT_RADIUS; dx++) {
        const tx = z.tx + dx;
        const ty = z.ty + dy;
        const d = Math.abs(dx) + Math.abs(dy);
        if (!d || d > SPOT_RADIUS || ty < 3 || ty > 9 || tx < 1 || tx > o.widthTiles - 2) continue;
        if (o.blocked[ty][tx] || zoneTiles.has(`${tx},${ty}`) || taken.has(`${tx},${ty}`)) continue;
        cands.push({ tx, ty, d });
      }
    }
    cands.sort((a, b) => a.d - b.d || a.ty - b.ty || a.tx - b.tx);
    return cands[0] || { tx: z.tx, ty: z.ty };
  }

  _door() {
    const e = this.office.zones.entry;
    return { tx: e.tx, ty: e.ty };
  }

  _px(tx, ty) {
    return { x: this.office.ox + tx * TILE + TILE / 2, y: this.office.oy + ty * TILE + TILE - 3 };
  }

  // ---- the two kinds ----------------------------------------------------------------

  _bot(task) {
    const s = this.scene;
    const spot = this._spot('ops');
    const from = this._px(this._door().tx, this._door().ty);
    const to = this._px(spot.tx, spot.ty);
    const shadow = s.add.image(from.x, from.y, 'ph-shadow').setOrigin(0.5, 0.5);
    const sprite = s.add.image(from.x, from.y - 10, 'ph-bot').setOrigin(0.5, 1);
    const led = s.add.rectangle(from.x, from.y - 30, 2, 2, 0x4fe0b8).setBlendMode(Phaser.BlendModes.ADD);
    s.tweens.add({ targets: led, alpha: { from: 1, to: 0.2 }, duration: 380, yoyo: true, repeat: -1 });
    const text = label(s, task.label ? `$ ${task.label}` : 'background');
    // tweens move `pos` (the spot on the floor); _sync adds the hover bob on top
    const h = { task, kind: 'shell', sprite, shadow, led, text, pos: { ...from }, bob: { v: 0 }, tileKey: `${spot.tx},${spot.ty}` };
    s.tweens.add({ targets: h.bob, v: -3, duration: 700, yoyo: true, repeat: -1, ease: 'Sine.easeInOut' });
    const dist = Math.hypot(to.x - from.x, to.y - from.y);
    s.tweens.add({ targets: h.pos, x: to.x, y: to.y, duration: Math.max(500, dist * 7), ease: 'Sine.easeInOut' });
    return h;
  }

  _mini(task) {
    const s = this.scene;
    const spot = this._spot('read');
    const key = this.office.character.sheetKey;
    const door = this._door();
    const p = this._px(door.tx, door.ty);
    const sprite = s.add.sprite(p.x, p.y, key, 0).setOrigin(0.5, 1).setScale(MINI_SCALE);
    const text = label(s, task.label || 'subagent');
    const h = { task, kind: 'agent', sprite, text, tile: { ...door }, tileKey: `${spot.tx},${spot.ty}`, key };
    this._walk(h, spot, () => {
      sprite.anims.play(`${key}-work-up`, true);
    });
    return h;
  }

  // walk a mini-me tile by tile along the room's real paths (world grid)
  _walk(h, dest, done) {
    const o = this.office;
    const path = o.nav.path([o.originX + h.tile.tx, h.tile.ty], [o.originX + dest.tx, dest.ty]);
    const steps = path ? path.slice(1) : [[o.originX + dest.tx, dest.ty]];
    const next = () => {
      if (!steps.length || !h.sprite.active) return done && done();
      const [wx, wy] = steps.shift();
      const tx = wx - o.originX;
      const dx = tx - h.tile.tx;
      const dy = wy - h.tile.ty;
      const facing = dx > 0 ? 'right' : dx < 0 ? 'left' : dy > 0 ? 'down' : 'up';
      h.sprite.anims.play(`${h.key}-walk-${facing}`, true);
      const q = this._px(tx, wy);
      this.scene.tweens.add({
        targets: h.sprite, x: q.x, y: q.y, duration: STEP_MS,
        onComplete: () => {
          h.tile = { tx, ty: wy };
          next();
        },
      });
    };
    next();
  }

  _leave(h, id) {
    const door = this._door();
    const gone = () => {
      this.scene.tweens.add({
        targets: [h.sprite, h.text, h.shadow, h.led].filter(Boolean), alpha: 0, duration: 400,
        onComplete: () => {
          for (const o of [h.sprite, h.text, h.shadow, h.led]) if (o) o.destroy();
          this.items.delete(id);
          this._promote();
        },
      });
    };
    if (h.kind === 'agent') return this._walk(h, door, gone);
    const to = this._px(door.tx, door.ty);
    this.scene.tweens.add({ targets: h.pos, x: to.x, y: to.y, duration: 700, ease: 'Sine.easeIn', onComplete: gone });
  }

  // a slot freed up: bring in the oldest hidden task
  _promote() {
    const first = this.hidden.entries().next();
    if (first.done) return this._more();
    const [id, task] = first.value;
    this.hidden.delete(id);
    this.items.set(id, task.kind === 'agent' ? this._mini(task) : this._bot(task));
    this._more();
  }

  _more() {
    const n = this.hidden.size;
    if (!n) {
      if (this.moreText) this.moreText.setVisible(false);
      return;
    }
    const z = this.office.zones.ops || this.office.zones.entry;
    const p = this._px(z.tx, z.ty);
    if (!this.moreText) this.moreText = label(this.scene, '').setDepth(100002);
    this.moreText.setText(`+${n} more`).setPosition(p.x + 22, p.y - 40).setVisible(true);
  }

  _sync() {
    for (const h of this.items.values()) {
      const s = h.sprite;
      if (!s.active) continue;
      if (h.kind === 'shell') {
        const x = Math.round(h.pos.x);
        const y = Math.round(h.pos.y);
        const b = Math.round(h.bob.v);
        s.setPosition(x, y - 10 + b).setDepth(y + 30);
        h.shadow.setPosition(x, y).setDepth(y - 10);
        h.led.setPosition(x, y - 29 + b).setDepth(y + 31);
        h.text.setPosition(x, y - 32 + b).setDepth(100001);
      } else {
        s.setDepth(s.y + 2);
        h.text.setPosition(s.x, s.y - 36).setDepth(100001);
      }
    }
  }
}

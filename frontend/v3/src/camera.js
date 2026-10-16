// CameraRig (SOU-196): overview of the whole Lodge, drag/keys to pan, wheel/+/- to zoom at
// the cursor, click a room to fly to it, Esc (or a click on the focused room / the snow) to
// go back. The building growing only re-frames the view while the user is in overview.
import { TILE } from './config.js';

const ZOOMS = [0.25, 0.5, 1, 1.5, 2, 2.5, 3, 4];
const FLY_MS = 550;
const KEY_PAN = 0.6; // screen px per ms
const DRAG_SLOP = 5;

export class CameraRig {
  constructor(scene, building) {
    this.scene = scene;
    this.building = building;
    this.cam = scene.cameras.main;
    this.mode = 'overview'; // overview | focus | free
    this.focused = null;
    this.drag = null;

    scene.input.on('pointerdown', (p) => {
      this.drag = { x: p.x, y: p.y, sx: this.cam.scrollX, sy: this.cam.scrollY, moved: false };
    });
    scene.input.on('pointermove', (p) => {
      if (!this.drag || !p.isDown) return;
      const dx = p.x - this.drag.x;
      const dy = p.y - this.drag.y;
      if (!this.drag.moved && Math.hypot(dx, dy) < DRAG_SLOP) return;
      this.drag.moved = true;
      this.mode = 'free';
      this.cam.setScroll(this.drag.sx - dx / this.cam.zoom, this.drag.sy - dy / this.cam.zoom);
    });
    scene.input.on('pointerup', (p) => {
      const d = this.drag;
      this.drag = null;
      if (d && !d.moved) this.click(p);
    });
    scene.input.on('wheel', (p, _over, _dx, dy) => this.zoomStep(dy > 0 ? -1 : 1, p.x, p.y));

    const kb = scene.input.keyboard;
    kb.on('keydown-ESC', () => this.overview());
    kb.on('keydown-ZERO', () => this.overview());
    kb.on('keydown-F', () => this.cycle());
    for (const k of ['PLUS', 'NUMPAD_ADD']) kb.on(`keydown-${k}`, () => this.zoomStep(1));
    for (const k of ['MINUS', 'NUMPAD_SUBTRACT']) kb.on(`keydown-${k}`, () => this.zoomStep(-1));
    kb.on('keydown', (e) => {
      if (e.key === '=' || e.key === '+') this.zoomStep(1);
    });
    this.keys = kb.addKeys('W,A,S,D,UP,DOWN,LEFT,RIGHT');
    scene.events.on('update', (_t, dt) => this.update(dt));

    scene.events.on('building-grew', () => {
      this.applyBounds();
      if (this.mode === 'overview') this.overview(true);
    });
    scene.scale.on('resize', () => {
      if (this.mode === 'overview') this.overview(true);
      else if (this.mode === 'focus' && this.focused) this.focus(this.focused, true);
    });
    this.applyBounds();
    this.overview(true);
  }

  applyBounds() {
    const b = this.building.worldBounds;
    this.cam.setBounds(b.x, b.y, b.width, b.height);
  }

  // largest zoom that shows `rect` with a margin: half steps from 1 up (pixels stay crisp
  // enough), eighths below 1 for the overview of a big building
  fitZoom(rect, margin = TILE / 2) {
    const z = Math.min(this.scale.width / (rect.width + margin * 2), this.scale.height / (rect.height + margin * 2));
    return z >= 1 ? Math.min(4, Math.floor(z * 2) / 2) : Math.max(0.125, Math.floor(z * 8) / 8);
  }

  get scale() {
    return this.scene.scale;
  }

  fly(rect, zoom, instant) {
    const cx = rect.centerX;
    const cy = rect.centerY;
    if (instant) {
      this.cam.setZoom(zoom);
      this.cam.centerOn(cx, cy);
      return;
    }
    this.cam.zoomTo(zoom, FLY_MS, 'Sine.easeInOut', true);
    this.cam.pan(cx, cy, FLY_MS, 'Sine.easeInOut', true);
  }

  overview(instant = false) {
    this.mode = 'overview';
    this.focused = null;
    this.scene.events.emit('room-focus', null);
    const b = this.building.bounds;
    this.fly(b, this.fitZoom(b), instant);
  }

  focus(office, instant = false) {
    this.mode = 'focus';
    this.focused = office;
    this.scene.events.emit('room-focus', office);
    const r = office.bounds;
    // the room plus its doorway and name plate
    const rect = new Phaser.Geom.Rectangle(r.x, r.y, r.width, r.height + TILE * 1.5);
    this.fly(rect, this.fitZoom(rect, TILE / 4), instant);
  }

  officeAt(wx, wy) {
    for (const o of this.building.offices.values()) {
      const b = o.bounds;
      if (wx >= b.x && wx < b.right && wy >= b.y && wy < b.bottom + TILE) return o;
    }
    return null;
  }

  click(p) {
    const w = this.cam.getWorldPoint(p.x, p.y);
    const o = this.officeAt(w.x, w.y);
    if (!o || (this.mode === 'focus' && this.focused === o)) return this.overview();
    this.focus(o);
  }

  cycle() {
    const list = [...this.building.offices.values()].sort((a, b) => a.slot - b.slot);
    if (!list.length) return;
    const i = this.focused ? list.indexOf(this.focused) : -1;
    this.focus(list[(i + 1) % list.length]);
  }

  zoomStep(dir, sx = this.scale.width / 2, sy = this.scale.height / 2) {
    const cam = this.cam;
    const cur = cam.zoom;
    let next = dir > 0 ? ZOOMS.find((z) => z > cur + 1e-6) : [...ZOOMS].reverse().find((z) => z < cur - 1e-6);
    if (next === undefined) return;
    // keep the world point under the cursor where it is
    const before = cam.getWorldPoint(sx, sy);
    cam.setZoom(next);
    cam.preRender();
    const after = cam.getWorldPoint(sx, sy);
    cam.setScroll(cam.scrollX + before.x - after.x, cam.scrollY + before.y - after.y);
    this.mode = 'free';
  }

  update(dt) {
    const k = this.keys;
    const dx = (k.D.isDown || k.RIGHT.isDown ? 1 : 0) - (k.A.isDown || k.LEFT.isDown ? 1 : 0);
    const dy = (k.S.isDown || k.DOWN.isDown ? 1 : 0) - (k.W.isDown || k.UP.isDown ? 1 : 0);
    if (!dx && !dy) return;
    this.mode = 'free';
    const step = (KEY_PAN * dt) / this.cam.zoom;
    this.cam.setScroll(this.cam.scrollX + dx * step, this.cam.scrollY + dy * step);
  }
}

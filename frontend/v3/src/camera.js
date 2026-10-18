// CameraRig (SOU-196): overview of the whole Lodge, drag/keys to pan, wheel/+/- to zoom at
// the cursor, click a room to fly to it, Esc (or a click on the focused room / the snow) to
// go back. The building growing only re-frames the view while the user is in overview.
// Touch (SOU-299): one finger pans, two fingers pinch-zoom around their midpoint and pan.
import { TILE } from './config.js';
import { distance, midpoint, pinchZoom, coverZoom, overviewZoom, reservedBottom } from './gestures.js';

const ZOOMS = [0.25, 0.5, 1, 1.5, 2, 2.5, 3, 4];
const FLY_MS = 550;
const KEY_PAN = 0.6; // screen px per ms
const DRAG_SLOP = 5;
const PINCH_RATE = 0.01; // zoom factor per pinch delta unit
const PINCH_SNAP_MS = 220; // settle on a crisp zoom step after the pinch stops
const NOTCH = 100; // accumulated mouse-wheel delta per zoom step

const MAX_ZOOM = ZOOMS[ZOOMS.length - 1];
const nearestZoom = (z) => ZOOMS.reduce((best, s) => (Math.abs(Math.log(s / z)) < Math.abs(Math.log(best / z)) ? s : best));

export class CameraRig {
  constructor(scene, building) {
    this.scene = scene;
    this.building = building;
    this.cam = scene.cameras.main;
    this.mode = 'overview'; // overview | focus | free
    this.focused = null;
    this.drag = null;
    this.pinch = null;

    scene.input.addPointer(1); // a second touch pointer, or a pinch can never be seen
    scene.input.on('pointerdown', (p) => {
      const pair = this.downPair();
      if (pair) return this.startPinch(pair);
      if (this.pinch || this.afterPinch) return;
      this.drag = { x: p.x, y: p.y, sx: this.cam.scrollX, sy: this.cam.scrollY, moved: false };
    });
    scene.input.on('pointermove', (p) => {
      if (this.pinch) {
        const pair = this.downPair();
        if (pair) this.movePinch(pair);
        return;
      }
      if (!this.drag || !p.isDown) return;
      const dx = p.x - this.drag.x;
      const dy = p.y - this.drag.y;
      if (!this.drag.moved && Math.hypot(dx, dy) < DRAG_SLOP) return;
      this.drag.moved = true;
      this.mode = 'free';
      this.cam.setScroll(this.drag.sx - dx / this.cam.zoom, this.drag.sy - dy / this.cam.zoom);
    });
    scene.input.on('pointerup', (p) => {
      if (this.pinch) {
        if (!this.downPair()) this.endPinch();
        return;
      }
      if (this.afterPinch) {
        // the finger left over from a pinch: its lift is not a tap
        if (!this.anyDown()) this.afterPinch = false;
        this.drag = null;
        return;
      }
      const d = this.drag;
      this.drag = null;
      if (d && !d.moved) this.click(p);
    });
    scene.input.on('wheel', (p, _over, dx, dy) => this.wheel(p, dx, dy));

    const kb = scene.input.keyboard;
    kb.on('keydown-ESC', () => this.overview());
    kb.on('keydown-ZERO', () => this.overview());
    kb.on('keydown-F', () => this.cycle());
    for (const k of ['PLUS', 'NUMPAD_ADD']) kb.on(`keydown-${k}`, () => this.zoomStep(1));
    for (const k of ['MINUS', 'NUMPAD_SUBTRACT']) kb.on(`keydown-${k}`, () => this.zoomStep(-1));
    kb.on('keydown', (e) => {
      // layouts where + is not the PLUS/NUMPAD_ADD key; those two already zoomed above
      if ((e.key === '=' || e.key === '+') && e.keyCode !== 187 && e.keyCode !== 107) this.zoomStep(1);
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

  downPair() {
    const { pointer1: a, pointer2: b } = this.scene.input;
    return a && b && a.isDown && b.isDown ? [a, b] : null;
  }

  anyDown() {
    const { pointer1: a, pointer2: b } = this.scene.input;
    return Boolean((a && a.isDown) || (b && b.isDown));
  }

  startPinch([a, b]) {
    this.drag = null;
    this.mode = 'free';
    this.pinch = { dist: distance(a, b), zoom: this.cam.zoom, mid: midpoint(a, b) };
  }

  movePinch([a, b]) {
    const pin = this.pinch;
    const m = midpoint(a, b);
    this.zoomAt(pinchZoom(pin.zoom, pin.dist, distance(a, b), this.minZoom(), MAX_ZOOM), m.x, m.y);
    // the two fingers moving together drag the world along
    this.cam.setScroll(this.cam.scrollX - (m.x - pin.mid.x) / this.cam.zoom, this.cam.scrollY - (m.y - pin.mid.y) / this.cam.zoom);
    pin.mid = m;
  }

  endPinch() {
    const m = this.pinch.mid;
    this.pinch = null;
    this.afterPinch = true;
    this.zoomAt(nearestZoom(this.cam.zoom), m.x, m.y); // settle on a crisp step
  }

  // never so far out that the view shows past the forest's edge
  minZoom() {
    return Math.max(0.125, coverZoom(this.scale, this.building.worldBounds));
  }

  // the screen area the camera frames into: phones keep the bottom strip for the HUD
  get viewport() {
    return { width: this.scale.width, height: this.scale.height - reservedBottom(this.scale) };
  }

  // largest zoom that shows `rect` with a margin: half steps from 1 up (pixels stay crisp
  // enough), eighths below 1 for the overview of a big building
  fitZoom(rect, margin = TILE / 2) {
    const v = this.viewport;
    const z = Math.min(v.width / (rect.width + margin * 2), v.height / (rect.height + margin * 2));
    return z >= 1 ? Math.min(4, Math.floor(z * 2) / 2) : Math.max(0.125, Math.floor(z * 8) / 8);
  }

  get scale() {
    return this.scene.scale;
  }

  fly(rect, zoom, instant) {
    zoom = Math.min(MAX_ZOOM, Math.max(this.minZoom(), zoom));
    const cx = rect.centerX;
    // shift down by half the reserved strip so the rect sits centred in the free area above it
    const cy = rect.centerY + reservedBottom(this.scale) / 2 / zoom;
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
    const zoom = overviewZoom({ view: this.scale, building: b, world: this.building.worldBounds, fit: this.fitZoom(b) });
    this.fly(b, zoom, instant);
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

  /**
   * Wheel input comes from three very different devices (SOU-215):
   *   - touchpad pinch: the browser sends wheel + ctrlKey with small deltas -> smooth zoom at
   *     the cursor, snapped to the nearest crisp step when the gesture stops
   *   - touchpad two-finger scroll: small, often fractional, often with deltaX -> pan
   *   - mouse wheel: whole notches (|deltaY| >= 50, no deltaX, or line mode) -> one zoom
   *     step per notch, accumulated so a fast flick doesn't skip three levels
   */
  wheel(p, dx, dy) {
    const e = p.event || {};
    if (e.ctrlKey) {
      this.zoomAt(this.cam.zoom * Math.exp(-dy * PINCH_RATE), p.x, p.y);
      clearTimeout(this.snapTimer);
      this.snapTimer = setTimeout(() => this.zoomAt(nearestZoom(this.cam.zoom), p.x, p.y), PINCH_SNAP_MS);
      return;
    }
    const notch = e.deltaMode === 1 || (dx === 0 && Math.abs(dy) >= 50 && Number.isInteger(dy));
    if (notch) {
      this.wheelAcc = (this.wheelAcc || 0) + dy;
      if (Math.abs(this.wheelAcc) >= NOTCH) {
        this.zoomStep(this.wheelAcc > 0 ? -1 : 1, p.x, p.y);
        this.wheelAcc = 0;
      }
      return;
    }
    this.mode = 'free';
    this.cam.setScroll(this.cam.scrollX + dx / this.cam.zoom, this.cam.scrollY + dy / this.cam.zoom);
  }

  zoomStep(dir, sx = this.scale.width / 2, sy = this.scale.height / 2) {
    const cur = this.cam.zoom;
    const next = dir > 0 ? ZOOMS.find((z) => z > cur + 1e-6) : [...ZOOMS].reverse().find((z) => z < cur - 1e-6);
    if (next !== undefined) this.zoomAt(next, sx, sy);
  }

  // set the zoom, keeping the world point under (sx, sy) where it is
  zoomAt(zoom, sx = this.scale.width / 2, sy = this.scale.height / 2) {
    const cam = this.cam;
    const next = Math.min(MAX_ZOOM, Math.max(this.minZoom(), zoom));
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

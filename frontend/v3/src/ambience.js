// Night at the Lodge (SOU-197): cold blue snow outside, warm light pools inside, blinking LEDs
// on the racks, snowfall that stops at the roofline, rooms whose lights go out when their
// agent sleeps, and a cat that patrols the corridor when nobody is around.
import { TILE } from './config.js';
import { rng } from './lodge.js';
import { CORRIDOR_Y, CORRIDOR_H, FRONT_Y } from './world.js';

export const NIGHT_TINT = 0x8a98c4; // multiplies the snow and the forest
const WARM = 0xffb35a;
const LED_COLORS = [0x4fe0b8, 0x4fe0b8, 0xff9a3a, 0x7cff6a];

// prop -> where its light sits (fraction of the image from the top-left) and how it glows
const LIGHTS = {
  desk: { fx: 0.55, fy: 0.25, r: 90, color: 0xffc070, a: 0.42 },
  coffee: { fx: 0.5, fy: 0.3, r: 60, color: WARM, a: 0.32 },
  bookshelf: { fx: 0.5, fy: 0.12, r: 50, color: 0xffd08a, a: 0.2 },
  couch: { fx: 0.15, fy: 0.1, r: 70, color: 0xffb060, a: 0.22 },
  van: { fx: 0.3, fy: 0.85, r: 60, color: 0xfff0c0, a: 0.18 },
  amp: { fx: 0.5, fy: 0.5, r: 30, color: 0xff7a3a, a: 0.25 },
  // set C (SOU-214): the props that carry their own light
  lantern_green: { fx: 0.5, fy: 0.35, r: 70, color: 0x6aff8a, a: 0.32 },
  crystal_ball: { fx: 0.5, fy: 0.35, r: 44, color: 0xb48cff, a: 0.3 },
  eye_painting: { fx: 0.5, fy: 0.45, r: 50, color: 0xffe066, a: 0.22 },
  red_lanterns: { fx: 0.5, fy: 0.5, r: 64, color: 0xff4a3a, a: 0.3 },
  token_stacks: { fx: 0.5, fy: 0.4, r: 30, color: 0xffd34a, a: 0.25 },
  token_server: { fx: 0.5, fy: 0.45, r: 50, color: 0x4fe0b8, a: 0.2 },
  ring_light: { fx: 0.5, fy: 0.2, r: 56, color: 0xfff6e0, a: 0.3 },
  typewriter_desk: { fx: 0.7, fy: 0.2, r: 80, color: 0xffc070, a: 0.38 },
  advisor_desk: { fx: 0.5, fy: 0.25, r: 80, color: 0xffd08a, a: 0.32 },
  abacus_desk: { fx: 0.5, fy: 0.25, r: 70, color: 0xffc070, a: 0.32 },
  reading_chair: { fx: 0.8, fy: 0.2, r: 44, color: 0xffb060, a: 0.3 },
};

export function ensureAmbienceTextures(scene) {
  if (!scene.textures.exists('glow')) {
    const size = 128;
    const tex = scene.textures.createCanvas('glow', size, size);
    const ctx = tex.context;
    const grad = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    grad.addColorStop(0, 'rgba(255,255,255,1)');
    grad.addColorStop(0.35, 'rgba(255,255,255,0.45)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, size, size);
    tex.refresh();
  }
  if (!scene.textures.exists('flake')) {
    const g = scene.make.graphics({ x: 0, y: 0 }, false);
    g.fillStyle(0xffffff).fillRect(0, 0, 2, 2);
    g.generateTexture('flake', 2, 2);
    g.destroy();
  }
  if (!scene.textures.exists('ph-cat-walk')) drawCat(scene);
}

export function glow(scene, x, y, r, color, alpha, depth = 61000) {
  return scene.add.image(x, y, 'glow').setScale((r * 2) / 128).setTint(color).setAlpha(alpha)
    .setBlendMode(Phaser.BlendModes.ADD).setDepth(depth).setData('a', alpha);
}

/** Warm light pools and rack LEDs for one room. Returns the objects so lights can dim. */
export function lightRoom(scene, office) {
  const objs = [];
  const r = rng(office.slot * 7919 + 17);
  for (const p of office.props) {
    const img = p.image;
    const L = LIGHTS[p.name];
    if (L) {
      const x = img.x - img.width / 2 + img.width * L.fx;
      const y = img.y - img.height + img.height * L.fy;
      objs.push(glow(scene, x, y, L.r, L.color, L.a));
    }
    if (p.name === 'rack' || p.name === 'rack_v2') {
      // a teal haze in front of the rack and a handful of blinking LEDs on its face
      const big = p.name === 'rack_v2';
      objs.push(glow(scene, img.x, img.y - img.height * 0.45, big ? 70 : 46, 0x4fe0b8, big ? 0.2 : 0.16));
      for (let k = 0; k < (big ? 12 : 7); k++) {
        const led = scene.add.rectangle(
          Math.round(img.x - img.width / 2 + 8 + r() * (img.width - 16)),
          Math.round(img.y - img.height + 18 + r() * (img.height - 30)),
          2, 1, LED_COLORS[Math.floor(r() * LED_COLORS.length)],
        ).setBlendMode(Phaser.BlendModes.ADD).setDepth(img.depth + 1).setData('a', 1);
        scene.tweens.add({
          targets: led, alpha: { from: 1, to: 0.15 }, duration: 180 + r() * 900,
          yoyo: true, repeat: -1, delay: r() * 1500, hold: r() * 600,
        });
        objs.push(led);
      }
    }
  }
  // the corridor lamp in front of every door
  const door = office.tileToWorld(6.5, office.heightTiles);
  objs.push(glow(scene, door.x, door.y + TILE * 0.5, 70, WARM, 0.22));
  return objs;
}

/** Darkness over a room, faded in when its agent sleeps (lights off). */
export function roomShade(scene, office) {
  const b = office.bounds;
  return scene.add.rectangle(b.x + TILE, b.y + TILE, b.width - 2 * TILE, b.height - 2 * TILE, 0x10142a, 1)
    .setOrigin(0, 0).setAlpha(0).setDepth(60000);
}

/** Snowfall over the whole field that melts at the building's footprint. */
export function snowfall(scene, building) {
  const wb = building.worldBounds;
  const b = building.bounds;
  const roof = new Phaser.Geom.Rectangle(b.x, b.y, b.width, (FRONT_Y + 1) * TILE - b.y);
  const emitter = scene.add.particles(0, 0, 'flake', {
    emitZone: { type: 'random', source: new Phaser.Geom.Rectangle(wb.x, wb.y, wb.width, wb.height) },
    deathZone: { type: 'onEnter', source: roof },
    lifespan: 9000, speedY: { min: 14, max: 30 }, speedX: { min: -8, max: 6 },
    scale: { min: 0.5, max: 1.2 }, alpha: { start: 0.9, end: 0 }, frequency: 22, quantity: 2,
  });
  emitter.setDepth(70000);
  return emitter;
}

// 4-frame orange tabby, 20x14 per frame, walking right
function drawCat(scene) {
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  for (let f = 0; f < 4; f++) {
    const o = f * 20;
    const bob = f % 2;
    g.fillStyle(0xd9822b).fillRect(o + 4, 5 + bob, 11, 5); // body
    g.fillStyle(0xf0a24a).fillRect(o + 5, 5 + bob, 9, 2);
    g.fillStyle(0xa85a18).fillRect(o + 7, 6 + bob, 1, 3).fillRect(o + 10, 6 + bob, 1, 3); // stripes
    g.fillStyle(0xd9822b).fillRect(o + 14, 2 + bob, 5, 5); // head
    g.fillStyle(0xd9822b).fillRect(o + 14, 1 + bob, 1, 1).fillRect(o + 18, 1 + bob, 1, 1); // ears
    g.fillStyle(0x1d1f2a).fillRect(o + 17, 4 + bob, 1, 1); // eye
    g.fillStyle(0xd9822b).fillRect(o + 1, 3 + bob - (f === 1 ? 1 : 0), 3, 2); // tail
    const legs = [[5, 13, 8, 11], [6, 12, 7, 13], [5, 13, 8, 11], [4, 14, 9, 12]][f];
    g.fillStyle(0xb8661e);
    for (const lx of legs) g.fillRect(o + lx, 10 + bob, 1, 3 - bob);
  }
  g.generateTexture('ph-cat-walk', 80, 14);
  g.destroy();
  for (let f = 0; f < 4; f++) scene.textures.get('ph-cat-walk').add(f, 0, f * 20, 0, 20, 14);
}

/** The corridor cat: wanders between random spots, sits, naps. */
export class CorridorCat {
  constructor(scene, building) {
    this.scene = scene;
    this.building = building;
    // the generated tabby (SOU-198) when it's there, the drawn one otherwise
    const sheet = scene.textures.exists('lodge-cat-walk') ? 'lodge-cat-walk' : 'ph-cat-walk';
    const anim = `cat-walk-${sheet}`;
    if (!scene.anims.exists(anim)) {
      scene.anims.create({ key: anim, frames: [0, 1, 2, 3].map((f) => ({ key: sheet, frame: f })), frameRate: 8, repeat: -1 });
    }
    this.anim = anim;
    const w = building.world;
    const y = (CORRIDOR_Y + 1 + Math.random() * (CORRIDOR_H - 1.4)) * TILE;
    const x = (w.x0 + 2 + Math.random() * (w.x1 - w.x0 - 4)) * TILE;
    this.sprite = scene.add.sprite(x, y, sheet, 0).setOrigin(0.5, 1).setScale(sheet === 'ph-cat-walk' ? 1.5 : 1);
    this.zzz = scene.add.text(0, 0, 'z', { fontFamily: 'monospace', fontSize: '9px', color: '#cfe3ff', stroke: '#1d1f2a', strokeThickness: 3 })
      .setOrigin(0.5, 1).setVisible(false).setDepth(100000);
    this.next();
    scene.events.on('update', () => {
      this.sprite.setDepth(this.sprite.y + 2);
      this.zzz.setPosition(this.sprite.x + 8, this.sprite.y - 18);
    });
  }

  next() {
    const w = this.building.world;
    const s = this.sprite;
    const r = Math.random();
    if (r < 0.3) {
      // nap
      s.anims.stop();
      s.setFrame(0);
      this.zzz.setVisible(true);
      this.scene.time.delayedCall(4000 + Math.random() * 5000, () => (this.zzz.setVisible(false), this.next()));
      return;
    }
    const tx = (w.x0 + 2 + Math.random() * (w.x1 - w.x0 - 4)) * TILE;
    const ty = (CORRIDOR_Y + 0.9 + Math.random() * (CORRIDOR_H - 1.2)) * TILE;
    const dist = Math.hypot(tx - s.x, ty - s.y);
    s.setFlipX(tx < s.x);
    s.anims.play(this.anim, true);
    this.scene.tweens.add({
      targets: s, x: tx, y: ty, duration: dist * 22,
      onComplete: () => {
        s.anims.stop();
        s.setFrame(0);
        this.scene.time.delayedCall(800 + Math.random() * 2500, () => this.next());
      },
    });
  }
}

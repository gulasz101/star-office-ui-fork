// Night at the Lodge (SOU-197): cold blue snow outside, warm light pools inside, blinking LEDs
// on the racks, season weather that stops at the roofline (SOU-273), rooms whose lights go out
// when their agent sleeps, and a cat that patrols the corridor when nobody is around.
import { TILE } from './config.js';
import { rng } from './lodge.js';
import { CORRIDOR_Y, CORRIDOR_H, FRONT_Y } from './world.js';
import { SEASONS, NIGHT_TINT } from './seasons.js';

// the night multiply lives with the seasons now (winter's tint is the SOU-197 one); re-exported
// here because this module is where the night ambience is described
export { NIGHT_TINT };

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
  // SOU-273 season weather: leaves, petals and fireflies, each a small strip whose frames are
  // numbered 0..n-1 so an emitter picks one at random with `frame: [0, 1, ...]`.
  particleSheet(scene, 'leaf', 6, 6, [
    (c, x) => drawLeaf(c, x, 0xd9822b, 0x9a5a18),
    (c, x) => drawLeaf(c, x, 0xb8392e, 0x7e241c),
    (c, x) => drawLeaf(c, x, 0x8a5a2b, 0x5c3a18),
  ]);
  particleSheet(scene, 'petal', 4, 4, [
    (c, x) => drawPetal(c, x, 0xf2a6c0),
    (c, x) => drawPetal(c, x, 0xf6f2f7),
  ]);
  particleSheet(scene, 'firefly', 3, 3, [(c, x) => drawFirefly(c, x)]);
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

// fireflies blink: a hard on/off, three cycles over a particle's life
const blink = (particle) => (Math.sin(particle.lifeT * Math.PI * 6) > 0 ? 1 : 0.12);

/**
 * The season's weather over the whole field, melting at the building's footprint (SOU-273).
 * Every season shares this emit zone and this roof death zone, so nothing falls on the building.
 */
export function weather(scene, building, season = 'winter') {
  const w = (SEASONS[season] || SEASONS.winter).weather;
  const wb = building.worldBounds;
  const b = building.bounds;
  const roof = new Phaser.Geom.Rectangle(b.x, b.y, b.width, (FRONT_Y + 1) * TILE - b.y);
  const config = {
    emitZone: { type: 'random', source: new Phaser.Geom.Rectangle(wb.x, wb.y, wb.width, wb.height) },
    deathZone: { type: 'onEnter', source: roof },
    lifespan: w.lifespan, speedY: w.speedY, speedX: w.speedX, scale: w.scale,
    alpha: w.blink ? { onUpdate: blink } : w.alpha,
    frequency: w.frequency, quantity: w.quantity,
  };
  if (w.rotate) config.rotate = w.rotate;
  if (w.frame) config.frame = w.frame;
  const emitter = scene.add.particles(0, 0, w.texture, config);
  if (w.blend) emitter.setBlendMode(Phaser.BlendModes[w.blend]);
  emitter.setDepth(70000);
  return emitter;
}

const hex = (n) => `#${n.toString(16).padStart(6, '0')}`;

// a strip of w x h frames, one painter per frame, drawn once
function particleSheet(scene, key, w, h, painters) {
  if (scene.textures.exists(key)) return;
  const tex = scene.textures.createCanvas(key, w * painters.length, h);
  tex.context.imageSmoothingEnabled = false;
  painters.forEach((paint, i) => paint(tex.context, i * w));
  tex.refresh();
  painters.forEach((_, i) => tex.add(i, 0, i * w, 0, w, h));
}

// 6x6 leaf: a rounded blade with a darker midrib and a stem
function drawLeaf(c, x, colour, rib) {
  c.fillStyle = hex(colour);
  c.fillRect(x + 2, 0, 2, 1);
  c.fillRect(x + 1, 1, 4, 2);
  c.fillRect(x, 2, 6, 3);
  c.fillRect(x + 1, 4, 4, 1);
  c.fillRect(x + 2, 5, 2, 1);
  c.fillStyle = hex(rib);
  c.fillRect(x + 3, 1, 1, 4);
  c.fillRect(x + 2, 5, 2, 1);
}

// 4x4 petal: a small round blade
function drawPetal(c, x, colour) {
  c.fillStyle = hex(colour);
  c.fillRect(x + 1, 0, 2, 1);
  c.fillRect(x, 1, 4, 2);
  c.fillRect(x + 1, 3, 2, 1);
}

// 3x3 firefly: yellow-green with a brighter core (drawn additive on screen)
function drawFirefly(c, x) {
  c.fillStyle = '#d8ff6a';
  c.fillRect(x + 1, 0, 1, 1);
  c.fillRect(x, 1, 3, 1);
  c.fillRect(x + 1, 2, 1, 1);
  c.fillStyle = '#f2ffb0';
  c.fillRect(x + 1, 1, 1, 1);
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

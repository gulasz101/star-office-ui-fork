// Night at the Lodge (SOU-197): cold blue snow outside, warm light pools inside, blinking LEDs
// on the racks, season weather that stops at the roofline (SOU-273), rooms whose lights go out
// when their agent sleeps, and the corridor cats — one per room, each in its own coat, who
// wander, sit, nap and now and then chase, play, groom or fight each other (SOU-278).
import { TILE } from './config.js';
import { rng } from './lodge.js';
import { FRONT_Y } from './world.js';
import { SEASONS, NIGHT_TINT } from './seasons.js';
import {
  COATS, coatKey, CAT_FRAMES, CAT_FRAME_W, CAT_FRAME_H, CAT_FRAMES_PER_COAT, FIGHT_CLOUD, PLANS,
  COOLDOWN_MS, NOTICE_MIN_MS, NOTICE_MAX_MS, INTERACTION_KINDS, corridorSpot, clampToCorridor,
  pickCoat, pickInteraction, pairUp,
} from './cats.js';

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
  // SOU-278: SOU-277 publishes assets/cats/<coat>.png — an 8-frame 256x32 strip per coat — and a
  // 4-frame 48x32 fight cloud. Until a file lands (and in any checkout with no art drop) the SAME
  // texture key gets a drawn sheet, so a coat is never missing and the real PNG wins the moment it
  // loads. The drawn sheets are placeholders for the art, never a stand-in for the feature.
  for (const coat of COATS) drawCoatSheet(scene, coat);
  if (!scene.textures.exists(FIGHT_CLOUD.key)) drawFightCloud(scene);
  if (!scene.textures.exists('cat-heart')) drawHeart(scene);
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

// ---------------------------------------------------------------------------------------------
// SOU-278 placeholder coats. SOU-277 ships `assets/cats/<coat>.png`; until those files land (and in
// any checkout with no art drop) every coat gets a drawn sheet under the SAME key, so a coat is
// never missing and the PNG that lands later wins with no code change.
// ---------------------------------------------------------------------------------------------

// one palette per coat: body and belly, a mark and a second mark, an eye and a nose
const COAT_PALETTE = {
  tabby_orange: { body: 0xd9822b, belly: 0xf2cfa4, mark: 0xa85a18, mark2: 0x8a4810, eye: 0x2a2a22, nose: 0xe8a0a0 },
  black: { body: 0x2b2f3a, belly: 0x3c4150, mark: 0x191c24, mark2: 0x101319, eye: 0xf2d24a, nose: 0x4a4f5e },
  white: { body: 0xf2f0ea, belly: 0xffffff, mark: 0xd6d2c8, mark2: 0xc0bcb2, eye: 0x3a4a6a, nose: 0xf0a8b0 },
  grey: { body: 0x8a8f9c, belly: 0xb8bcc6, mark: 0x5a5f6c, mark2: 0x4a4f5c, eye: 0x9ae04a, nose: 0xc8a0a8 },
  calico: { body: 0xf2ede4, belly: 0xfffdf8, mark: 0xd9822b, mark2: 0x2b2f3a, eye: 0x3a6a3a, nose: 0xf0a8b0 },
  tuxedo: { body: 0x23272f, belly: 0xf4f2ee, mark: 0x14171d, mark2: 0xf4f2ee, eye: 0x8ad24a, nose: 0x4a4f5e },
  siamese: { body: 0xe4d6bc, belly: 0xf6ecd8, mark: 0x4a3a2c, mark2: 0x33261c, eye: 0x4a8ad2, nose: 0xc09090 },
};

// A pose is a list of body parts, so a coat can RE-PAINT a part (siamese points, tuxedo chest)
// instead of needing a second drawing.
const PART = {
  body: 'body', head: 'body', ear: 'body', tail: 'body', leg: 'body', paw: 'body', haunch: 'body', arch: 'body',
  belly: 'belly', stripe: 'mark', patch1: 'mark', patch2: 'mark2', eye: 'eye', nose: 'nose', mouth: 'eye',
};
const COAT_REPAINT = {
  siamese: { head: 'mark', ear: 'mark', tail: 'mark', leg: 'mark', paw: 'mark' },
  tuxedo: { paw: 'belly', stripe: 'body' },
};
const partColour = (coat, palette, part) => palette[(COAT_REPAINT[coat] || {})[part] || PART[part] || 'body'];

// Which pose lives at which frame comes from cats.js, never from a second copy of the layout.
const POSE_AT_FRAME = {
  0: 'walk', 1: 'walk', 2: 'walk', 3: 'walk',
  [CAT_FRAMES.sit]: 'sit', [CAT_FRAMES.sleep]: 'sleep', [CAT_FRAMES.pounce]: 'pounce', [CAT_FRAMES.hiss]: 'hiss',
};

// One 32x32 pose, feet on the bottom row (SOU-277's contract), side view except the sit.
// Parts are painted in order, so a later one covers an earlier one.
function poseParts(pose, f) {
  const bob = pose === 'walk' && f % 2 ? 1 : 0;
  if (pose === 'walk') {
    const legs = [[7, 10, 14, 17], [8, 11, 13, 16], [7, 10, 14, 17], [6, 11, 15, 18]][f % 4];
    const wag = [0, 1, 0, 2][f % 4];
    return [
      ['tail', 1 + wag, 15 + bob, 4, 3],
      ['body', 5, 18 + bob, 16, 7],
      ['belly', 6, 24 + bob, 13, 1],
      ...legs.map((lx) => ['leg', lx, 25 + bob, 2, 7 - bob]),
      ['head', 20, 13 + bob, 9, 8],
      ['ear', 20, 10 + bob, 2, 3], ['ear', 26, 10 + bob, 2, 3],
      ['eye', 24, 16 + bob, 1, 2], ['nose', 28, 18 + bob, 1, 1],
      ['stripe', 9, 18 + bob, 1, 4], ['stripe', 13, 18 + bob, 1, 4], ['stripe', 17, 18 + bob, 1, 4],
      ['patch1', 11, 20 + bob, 4, 3], ['patch2', 16, 18 + bob, 3, 2],
    ];
  }
  if (pose === 'sit') {
    return [
      ['tail', 2, 27, 6, 3], ['tail', 1, 29, 4, 2],
      ['haunch', 7, 21, 16, 11], ['belly', 12, 20, 7, 11], ['body', 11, 14, 10, 7],
      ['paw', 12, 28, 3, 4], ['paw', 17, 28, 3, 4],
      ['head', 11, 6, 11, 8], ['ear', 11, 3, 3, 3], ['ear', 19, 3, 3, 3],
      ['eye', 13, 9, 2, 2], ['eye', 18, 9, 2, 2], ['nose', 15, 12, 2, 1],
      ['stripe', 15, 6, 1, 3], ['patch1', 17, 21, 5, 5],
    ];
  }
  if (pose === 'sleep') {
    return [
      ['tail', 5, 27, 16, 3],
      ['body', 4, 19, 22, 13],
      ['head', 17, 21, 10, 9], ['ear', 18, 18, 3, 3], ['ear', 24, 18, 3, 3],
      ['eye', 22, 25, 3, 1], ['nose', 26, 27, 1, 1],
      ['belly', 7, 29, 10, 2], ['stripe', 10, 19, 1, 4],
      ['patch1', 8, 21, 5, 5], ['patch2', 15, 26, 4, 4],
    ];
  }
  if (pose === 'pounce') {
    return [
      ['tail', 1, 6, 4, 15],
      ['haunch', 3, 20, 9, 12], ['body', 6, 22, 17, 8], ['belly', 7, 29, 12, 1],
      ...[6, 12, 18].map((lx) => ['leg', lx, 29, 3, 3]),
      ['head', 20, 18, 9, 8], ['ear', 20, 15, 2, 3], ['ear', 26, 15, 2, 3],
      ['eye', 24, 21, 2, 2], ['nose', 28, 23, 1, 1],
      ['stripe', 10, 22, 1, 4], ['patch1', 13, 24, 4, 4],
    ];
  }
  // hiss: arched back, hackles up, tail straight up, head low and forward
  return [
    ['tail', 1, 6, 5, 15],
    ['haunch', 3, 22, 9, 10], ['body', 5, 20, 18, 8], ['arch', 9, 16, 10, 6], ['belly', 6, 27, 13, 1],
    ...[6, 12, 18].map((lx) => ['leg', lx, 29, 3, 3]),
    ['head', 20, 22, 10, 8], ['ear', 19, 20, 4, 2], ['ear', 27, 20, 4, 2],
    ['eye', 26, 25, 2, 2], ['mouth', 28, 28, 2, 2],
    ['stripe', 11, 16, 1, 4], ['patch1', 13, 21, 5, 4],
  ];
}

function drawCoatSheet(scene, coat) {
  const key = coatKey(coat);
  if (scene.textures.exists(key)) return; // SOU-277's file wins; this only fills the gap
  const palette = COAT_PALETTE[coat] || COAT_PALETTE.tabby_orange;
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  for (let f = 0; f < CAT_FRAMES_PER_COAT; f++) {
    const x0 = f * CAT_FRAME_W;
    for (const [part, x, y, w, h] of poseParts(POSE_AT_FRAME[f] || 'walk', f)) {
      g.fillStyle(partColour(coat, palette, part));
      g.fillRect(x0 + x, y, w, h);
    }
  }
  g.generateTexture(key, CAT_FRAME_W * CAT_FRAMES_PER_COAT, CAT_FRAME_H);
  g.destroy();
  for (let f = 0; f < CAT_FRAMES_PER_COAT; f++) {
    scene.textures.get(key).add(f, 0, f * CAT_FRAME_W, 0, CAT_FRAME_W, CAT_FRAME_H);
  }
}

// the dust cloud a fight hides behind, 4 x 48x32: puffs, paws poking out, a couple of stars
function drawFightCloud(scene) {
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  const w = FIGHT_CLOUD.frameWidth;
  const h = FIGHT_CLOUD.frameHeight;
  const grow = [7, 9, 10, 8];
  for (let f = 0; f < FIGHT_CLOUD.frames; f++) {
    const o = f * w;
    const cx = o + w / 2;
    const cy = 20;
    const r = grow[f];
    g.fillStyle(0xe9e4d8);
    g.fillCircle(cx - 9, cy, r - 1);
    g.fillCircle(cx + 9, cy, r - 2);
    g.fillCircle(cx, cy - 7, r);
    g.fillCircle(cx - 15, cy + 4, 3);
    g.fillCircle(cx + 15, cy + 4, 3);
    g.fillStyle(0xcfc8b8);
    g.fillCircle(cx, cy + 3, r - 2);
    g.fillStyle(0xf6f2ea);
    g.fillRect(cx - 19, cy + 1, 4, 3);
    g.fillRect(cx + 15, cy + 1, 4, 3);
    g.fillStyle(0xffe066);
    for (let k = 0; k < 3; k++) {
      const a = (f + k) % 4;
      g.fillRect(cx + [-15, 12, -7, 8][a], cy + [-13, -11, -16, -15][a], 2, 2);
    }
  }
  g.generateTexture(FIGHT_CLOUD.key, w * FIGHT_CLOUD.frames, h);
  g.destroy();
  for (let f = 0; f < FIGHT_CLOUD.frames; f++) {
    scene.textures.get(FIGHT_CLOUD.key).add(f, 0, f * w, 0, w, h);
  }
}

// 7x6 heart, floated up once when two cats groom side by side
function drawHeart(scene) {
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  g.fillStyle(0xff7aa8);
  g.fillRect(1, 0, 2, 1).fillRect(4, 0, 2, 1);
  g.fillRect(0, 1, 7, 2);
  g.fillRect(1, 3, 5, 1);
  g.fillRect(2, 4, 3, 1);
  g.fillRect(3, 5, 1, 1);
  g.fillStyle(0xffc0d4).fillRect(1, 1, 2, 1);
  g.generateTexture('cat-heart', 7, 6);
  g.destroy();
}

// today's walk speed, in ms per world px; the chase and the flee run at 1.8x this
const WALK_MS_PER_PX = 22;

/**
 * The corridor cat (SOU-278): a coat, eight poses and a life between interactions — wander, sit,
 * nap with the `z` above it, and whatever two cats decide to do together.
 *
 * `opts.coat` is the coat name and `opts.sheet` the texture it resolved to. main.js hands the coats
 * out; a coat whose file SOU-277 has not shipped is a drawn placeholder under the same key, and with
 * `?cats=0` every cat rides the Lodge's single `lodge-cat-walk` sheet — 4 frames, so the sit, sleep,
 * pounce and hiss poses collapse onto frame 0 exactly as they did before this ticket.
 *
 * `state` is the vocabulary pairUp() reads: 'sit' (sitting or just finished walking — idle),
 * 'walk', 'sleep' and 'interact'. A cat mid-interaction is busy and pairs with nobody.
 */
export class CorridorCat {
  constructor(scene, building, opts = {}) {
    this.scene = scene;
    this.building = building;
    this.random = opts.random || Math.random;
    this.coat = opts.coat || COATS[0];
    this.sheet = opts.sheet || this.fallbackSheet();
    this.walkAnim = `cat-walk-${this.sheet}`;
    if (!scene.anims.exists(this.walkAnim)) {
      const frames = CAT_FRAMES.walk.filter((f) => this.hasFrame(f)).map((f) => ({ key: this.sheet, frame: f }));
      scene.anims.create({ key: this.walkAnim, frames: frames.length ? frames : [{ key: this.sheet, frame: 0 }], frameRate: 8, repeat: -1 });
    }
    this.state = 'sit'; // sit | walk | sleep | interact
    this.cooldownUntil = 0;
    this.partner = null;
    this.waiting = null;
    const spot = corridorSpot(building.world, this.random);
    this.sprite = scene.add.sprite(spot.x, spot.y, this.sheet, 0).setOrigin(0.5, 1);
    if (this.sheet === 'ph-cat-walk') this.sprite.setScale(1.5);
    this.zzz = scene.add.text(0, 0, 'z', { fontFamily: 'monospace', fontSize: '9px', color: '#cfe3ff', stroke: '#1d1f2a', strokeThickness: 3 })
      .setOrigin(0.5, 1).setVisible(false).setDepth(100000);
    scene.events.on('update', () => {
      this.sprite.setDepth(this.sprite.y + 2);
      this.zzz.setPosition(this.sprite.x + 8, this.sprite.y - 18);
    });
    this.sit(1200 + this.random() * 2500);
  }

  // the Lodge's cat sheet when the coat art is off (or the drawn one if even that is missing)
  fallbackSheet() {
    return this.scene.textures.exists('lodge-cat-walk') ? 'lodge-cat-walk' : 'ph-cat-walk';
  }

  get x() {
    return this.sprite.x;
  }

  get y() {
    return this.sprite.y;
  }

  /** How many frames this sheet has (the drawn fallback has 4, a SOU-277 coat has 8). */
  frames() {
    const tex = this.scene.textures.get(this.sheet);
    return tex ? Math.max(0, tex.frameTotal - 1) : 0;
  }

  hasFrame(f) {
    return f < this.frames();
  }

  /** Hold a pose: its own frame where the sheet has it, frame 0 otherwise (the pre-SOU-278 look). */
  hold(frame) {
    this.sprite.anims.stop();
    this.sprite.setFrame(this.hasFrame(frame) ? frame : 0);
  }

  face(x) {
    this.sprite.setFlipX(x < this.sprite.x);
  }

  /** Everything that has to stop before a pose change or an interaction takes over. */
  cancel() {
    if (this.waiting) {
      this.waiting.remove(false);
      this.waiting = null;
    }
    this.scene.tweens.killTweensOf(this.sprite);
    this.zzz.setVisible(false);
  }

  after(ms, fn) {
    if (this.waiting) this.waiting.remove(false);
    this.waiting = this.scene.time.delayedCall(ms, () => {
      this.waiting = null;
      fn();
    });
  }

  /** Sitting (frame 4), then back to wandering. This is the idle state interactions pick from. */
  sit(ms = null) {
    this.state = 'sit';
    this.hold(CAT_FRAMES.sit);
    this.after(ms === null ? 800 + this.random() * 2500 : ms, () => this.next());
  }

  /** Napping: frame 5 with the `z` above it (it used to be a frame-0 freeze). */
  sleep(ms) {
    this.state = 'sleep';
    this.hold(CAT_FRAMES.sleep);
    this.zzz.setVisible(true);
    this.after(ms, () => this.next());
  }

  /** Wander: nap 30% of the time, otherwise walk to a corridor spot and sit down. */
  next() {
    if (this.state === 'interact') return;
    if (this.random() < 0.3) {
      this.sleep(4000 + this.random() * 5000);
      return;
    }
    this.walkTo(corridorSpot(this.building.world, this.random), 1, () => this.sit());
  }

  /**
   * Walk to a spot in the corridor at `speedMul` times the usual speed, then call back.
   * Returns the tween's duration so a caller can line two cats up.
   */
  walkTo(spot, speedMul = 1, done) {
    const s = this.sprite;
    const dist = Math.hypot(spot.x - s.x, spot.y - s.y);
    const ms = Math.max(120, (dist * WALK_MS_PER_PX) / speedMul);
    this.state = 'walk';
    this.zzz.setVisible(false);
    s.setFlipX(spot.x < s.x);
    s.anims.play(this.walkAnim, true);
    this.scene.tweens.add({
      targets: s,
      x: spot.x,
      y: spot.y,
      duration: ms,
      onComplete: () => {
        s.anims.stop();
        if (done) done();
      },
    });
    return ms;
  }
}

/**
 * The Lodge's cats (SOU-278): one per room, each in a coat no other cat wears, and every 3-6 s the
 * ones idling in the corridor notice each other.
 *
 * The roll, the reach, the cooldown and the one-fight-at-a-time rule all live in cats.js (pure and
 * tested); this class is the part that moves sprites, and `force()` is the hook the ticket asks for
 * so a recording can trigger one interaction on the nearest pair.
 */
export class Cats {
  constructor(scene, building, opts = {}) {
    this.scene = scene;
    this.building = building;
    this.random = opts.random || Math.random;
    this.coats = [];
    this.nextCoat = opts.nextCoat || (() => pickCoat(this.random, this.coats));
    this.art = opts.art !== false; // false = ?cats=0: everybody rides the Lodge's one sheet
    this.list = [];
    this.fights = 0;
    this.tickTimer = null;
    if (this.scene.textures.exists(FIGHT_CLOUD.key) && !this.scene.anims.exists('fight-cloud')) {
      this.scene.anims.create({
        key: 'fight-cloud',
        frames: this.scene.anims.generateFrameNumbers(FIGHT_CLOUD.key, { start: 0, end: FIGHT_CLOUD.frames - 1 }),
        frameRate: FIGHT_CLOUD.frameRate,
        repeat: -1,
      });
    }
    this.schedule();
  }

  get count() {
    return this.list.length;
  }

  get fighting() {
    return this.fights > 0;
  }

  /** One cat per room, at least one, at most 8 (the owner's rule since SOU-197). */
  topUp() {
    const want = Math.max(1, Math.min(8, this.building.offices.size));
    while (this.list.length < want) this.list.push(this.spawn());
    return this.list;
  }

  spawn() {
    const coat = this.nextCoat(this.coats);
    this.coats.push(coat);
    return new CorridorCat(this.scene, this.building, {
      coat,
      sheet: this.art && this.scene.textures.exists(coatKey(coat)) ? coatKey(coat) : null,
      random: this.random,
    });
  }

  /** The notice tick: whoever is sitting and off cooldown may pair up. */
  tick() {
    const pairs = pairUp(this.list, Date.now(), { random: this.random, fightActive: this.fighting });
    for (const { a, b, kind } of pairs) this.start(a, b, kind);
    return pairs;
  }

  schedule() {
    const wait = NOTICE_MIN_MS + this.random() * (NOTICE_MAX_MS - NOTICE_MIN_MS);
    this.tickTimer = this.scene.time.delayedCall(wait, () => {
      this.tick();
      this.schedule();
    });
  }

  /**
   * Run one interaction on a pair. Both cats are busy for its length and then cool down for 20 s;
   * a fight also holds the Lodge's single fight slot until it is over.
   */
  start(a, b, kind) {
    a.cancel();
    b.cancel();
    a.state = 'interact';
    b.state = 'interact';
    a.partner = b;
    b.partner = a;
    if (kind === 'fight') this.fights += 1;
    const done = () => {
      if (kind === 'fight') this.fights = Math.max(0, this.fights - 1);
      for (const c of [a, b]) {
        c.partner = null;
        c.cooldownUntil = Date.now() + COOLDOWN_MS;
        c.sit();
      }
    };
    this[kind](a, b, done);
    return { a, b, kind };
  }

  /**
   * `window.__lodge.cats.force('fight')` — the nearest pair, cooldown and weights ignored, for a
   * recording. A fight is refused while one is already on screen, because that rule has no
   * exception; returns the pair it started, or null if there was none to start.
   */
  force(kind = null) {
    const k = INTERACTION_KINDS.includes(kind) ? kind : pickInteraction(this.random);
    if (k === 'fight' && this.fighting) return null;
    const now = Date.now();
    const idle = this.list.filter((c) => c.state !== 'interact');
    const free = idle.filter((c) => (c.cooldownUntil || 0) <= now);
    const pool = free.length >= 2 ? free : idle;
    if (pool.length < 2) return null;
    let best = null;
    for (const a of pool) {
      for (const c of pool) {
        if (c === a) continue;
        const d = Math.hypot(c.x - a.x, c.y - a.y);
        if (!best || d < best.d) best = { a, b: c, d };
      }
    }
    return this.start(best.a, best.b, k);
  }

  // chase: one cat runs to a corridor spot at 1.8x walk speed, the other follows 0.4 s behind,
  // and both sit at the end
  chase(a, b, done) {
    const plan = PLANS.chase;
    const spot = corridorSpot(this.building.world, this.random);
    let landed = 0;
    const arrive = () => {
      if (++landed === 2) done();
    };
    a.walkTo(spot, plan.speedMul, () => {
      a.hold(CAT_FRAMES.sit);
      arrive();
    });
    this.scene.time.delayedCall(plan.lagMs, () => b.walkTo(spot, plan.speedMul, arrive));
  }

  // play: they face each other and alternate the pounce frame (6) with a small hop, for about 2 s
  play(a, b, done) {
    const plan = PLANS.play;
    a.face(b.x);
    b.face(a.x);
    const cycles = Math.max(1, Math.round(plan.durationMs / (2 * plan.hopMs)));
    for (const c of [a, b]) {
      c.hold(plan.hold);
      this.scene.tweens.add({
        targets: c.sprite,
        y: c.sprite.y - plan.hopPx,
        duration: plan.hopMs,
        yoyo: true,
        repeat: cycles - 1,
      });
    }
    let phase = 0;
    this.scene.time.addEvent({
      delay: 2 * plan.hopMs,
      repeat: cycles - 1,
      callback: () => {
        phase += 1;
        for (const c of [a, b]) c.hold(phase % 2 ? plan.sitAt : plan.hold);
      },
    });
    this.scene.time.delayedCall(plan.durationMs, done);
  }

  // groom: they walk side by side, both curl up (frame 5), and one small heart floats up
  groom(a, b, done) {
    const plan = PLANS.groom;
    const world = this.building.world;
    const spot = corridorSpot(world, this.random);
    const gap = plan.gapPx * (a.x <= b.x ? 1 : -1);
    let landed = 0;
    const arrive = () => {
      if (++landed < 2) return;
      for (const c of [a, b]) c.hold(plan.hold);
      if (plan.heart) this.heart((a.x + b.x) / 2, Math.min(a.y, b.y) - 26);
      this.scene.time.delayedCall(plan.durationMs, done);
    };
    a.walkTo(spot, 1, arrive);
    b.walkTo(clampToCorridor(world, spot.x + gap, spot.y), 1, arrive);
  }

  /** One small heart, floating up once. */
  heart(x, y) {
    if (!this.scene.textures.exists('cat-heart')) return;
    const h = this.scene.add.image(x, y, 'cat-heart').setDepth(100000).setAlpha(0.95);
    this.scene.tweens.add({ targets: h, y: y - 22, alpha: 0, duration: 1400, onComplete: () => h.destroy() });
  }

  // fight: both hiss (frame 7) for 0.5 s, a dust cloud plays at their midpoint for about 1.5 s with
  // both cats hidden, then they run off in opposite directions
  fight(a, b, done) {
    const plan = PLANS.fight;
    a.face(b.x);
    b.face(a.x);
    for (const c of [a, b]) {
      c.state = 'interact';
      c.hold(plan.hold);
    }
    this.scene.time.delayedCall(plan.hissMs, () => {
      const mx = (a.x + b.x) / 2;
      const my = Math.max(a.y, b.y);
      for (const c of [a, b]) c.sprite.setVisible(false);
      const cloud = this.scene.add.sprite(mx, my, FIGHT_CLOUD.key, 0).setOrigin(0.5, 1).setDepth(100000);
      if (this.scene.anims.exists('fight-cloud')) cloud.anims.play('fight-cloud', true);
      this.scene.time.delayedCall(plan.cloudMs, () => {
        cloud.destroy();
        for (const c of [a, b]) c.sprite.setVisible(true);
        const world = this.building.world;
        const left = a.x <= b.x ? a : b;
        const right = left === a ? b : a;
        let landed = 0;
        const arrive = () => {
          if (++landed === 2) done();
        };
        left.walkTo(clampToCorridor(world, left.x - plan.fleeTiles * TILE, left.y), plan.fleeMul, arrive);
        right.walkTo(clampToCorridor(world, right.x + plan.fleeTiles * TILE, right.y), plan.fleeMul, arrive);
      });
    });
  }
}

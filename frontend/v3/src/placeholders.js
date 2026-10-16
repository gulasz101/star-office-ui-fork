// Placeholder prop and character textures, drawn with Graphics so layout and code can be
// judged before the FLUX art set (SOU-158) lands. Each prop is a box: top face + front face,
// sized to its footprint from the map, plus extra height for tall things.
import { TILE, CHAR_W, CHAR_H } from './config.js';

// key -> [front colour, top colour, height above footprint in px]
const PROP_STYLE = {
  desk: [0x8a5a34, 0xb07a48, 22],
  chair: [0x3a3f52, 0x50566e, 18],
  rack: [0x2b2f3a, 0x41475a, 40],
  bookshelf: [0x6e4a2c, 0x8f6440, 40],
  coffee: [0x5b5f66, 0x7d828b, 26],
  rug: [0x7a3b3b, 0x8f4a4a, 0],
  couch: [0x3d6b73, 0x4f8790, 14],
  bass: [0xb5651d, 0xd08035, 30],
  amp: [0x26282e, 0x3a3d45, 14],
  cat: [0xb08a5a, 0xc8a070, 8],
  van: [0xd8c8a0, 0xeadcb8, 40],
  spool: [0xa0703c, 0xc08850, 12],
  crate: [0x7a5a36, 0x9a7848, 12],
};

export function propTextureKey(name) {
  return `ph-prop-${name}`;
}

// Draws the texture for a prop whose footprint is w x h px, if it doesn't exist yet.
export function ensurePropTexture(scene, name, w, h) {
  const key = propTextureKey(name);
  if (scene.textures.exists(key)) return key;
  const [front, top, lift] = PROP_STYLE[name] || [0x666a75, 0x8a8e99, 16];
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  const height = h + lift;
  if (lift === 0) {
    // flat decal (rug)
    g.fillStyle(front, 1).fillRect(2, 2, w - 4, h - 4);
    g.lineStyle(2, top, 1).strokeRect(5, 5, w - 10, h - 10);
  } else {
    const frontH = Math.min(lift + Math.round(h * 0.35), height - 6);
    // top face fills the space above the front face
    g.fillStyle(top, 1).fillRect(1, 0, w - 2, height - frontH);
    g.fillStyle(front, 1).fillRect(1, height - frontH, w - 2, frontH - 1);
    g.lineStyle(1, 0x111318, 1).strokeRect(1, 0, w - 2, height - 1);
    g.fillStyle(0x000000, 0.25).fillRect(1, height - 3, w - 2, 2);
  }
  g.generateTexture(key, w, height);
  g.destroy();
  return key;
}

// Character sheet contract (also the brief for the FLUX art, SOU-158):
// 8 columns x 4 rows of 32x48 frames. Rows = facing: down, left, right, up.
// Columns: 0 idle, 1-4 walk cycle, 5-6 work (typing), 7 sleep.
export const SHEET_COLS = 8;
export const FACINGS = ['down', 'left', 'right', 'up'];
export const FRAME = { idle: 0, walk: [1, 2, 3, 4], work: [5, 6], sleep: 7 };

// Draws a placeholder sheet in that layout so the controller can be built before the art.
export function ensureCharacterSheet(scene, key, color) {
  if (scene.textures.exists(key)) return key;
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  const skin = 0xf2d2b0;
  const hair = 0x2a2230;
  const legs = 0x2c3140;
  FACINGS.forEach((facing, row) => {
    for (let col = 0; col < SHEET_COLS; col++) {
      const ox = col * CHAR_W;
      const oy = row * CHAR_H;
      const walking = col >= 1 && col <= 4;
      const working = col === 5 || col === 6;
      const sleeping = col === 7;
      const bob = walking && (col === 2 || col === 4) ? -1 : 0;
      const step = walking ? [0, 3, 0, -3][col - 1] : 0;
      g.fillStyle(0x000000, 0.3).fillEllipse(ox + 16, oy + 44, 22, 8);
      // legs
      g.fillStyle(legs, 1);
      if (facing === 'left' || facing === 'right') {
        g.fillRect(ox + 13 + step, oy + 36, 4, 8).fillRect(ox + 15 - step, oy + 36, 4, 8);
      } else {
        g.fillRect(ox + 11, oy + 36 + (step > 0 ? -1 : 0), 4, 8).fillRect(ox + 17, oy + 36 + (step < 0 ? -1 : 0), 4, 8);
      }
      // body
      g.fillStyle(color, 1).fillRoundedRect(ox + 9, oy + 21 + bob, 14, 17, 4);
      // arms: forward and alternating while working
      g.fillStyle(skin, 1);
      if (working) {
        const lift = col === 5 ? 0 : 2;
        if (facing === 'up') g.fillRect(ox + 8, oy + 22 + lift, 3, 6).fillRect(ox + 21, oy + 24 - lift, 3, 6);
        else if (facing === 'down') g.fillRect(ox + 10, oy + 29 - lift, 4, 3).fillRect(ox + 18, oy + 29 + lift - 2, 4, 3);
        else g.fillRect(ox + (facing === 'right' ? 20 : 7), oy + 27 - lift, 5, 3);
      } else if (facing === 'left' || facing === 'right') {
        g.fillRect(ox + 14 - step, oy + 25 + bob, 3, 9);
      } else {
        g.fillRect(ox + 6, oy + 24 + bob, 3, 10).fillRect(ox + 23, oy + 24 + bob, 3, 10);
      }
      // head (anime proportions: big)
      const hy = oy + 13 + bob;
      g.fillStyle(skin, 1).fillCircle(ox + 16, hy, 10);
      g.fillStyle(hair, 1);
      if (facing === 'up') {
        g.fillCircle(ox + 16, hy, 10);
      } else {
        g.fillRect(ox + 6, hy - 10, 20, 7);
        if (facing === 'left') g.fillRect(ox + 18, hy - 6, 8, 8);
        if (facing === 'right') g.fillRect(ox + 6, hy - 6, 8, 8);
      }
      // eyes
      if (facing !== 'up') {
        g.fillStyle(0x111111, 1);
        const eye = (x) => (sleeping ? g.fillRect(x - 1, hy + 2, 4, 1) : g.fillRect(x, hy, 2, 4));
        if (facing === 'down') { eye(ox + 11); eye(ox + 19); }
        if (facing === 'left') eye(ox + 9);
        if (facing === 'right') eye(ox + 21);
      }
    }
  });
  g.generateTexture(key, CHAR_W * SHEET_COLS, CHAR_H * FACINGS.length);
  g.destroy();
  const tex = scene.textures.get(key);
  for (let row = 0; row < FACINGS.length; row++) {
    for (let col = 0; col < SHEET_COLS; col++) {
      tex.add(row * SHEET_COLS + col, 0, col * CHAR_W, row * CHAR_H, CHAR_W, CHAR_H);
    }
  }
  return key;
}

export const PLACEHOLDER_TILE = TILE;

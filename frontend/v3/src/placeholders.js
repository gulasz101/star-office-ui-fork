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

// A capsule-ish placeholder character, 32x48, facing down.
export function ensureCharacterTexture(scene, key, color) {
  if (scene.textures.exists(key)) return key;
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  g.fillStyle(0x000000, 0.3).fillEllipse(CHAR_W / 2, CHAR_H - 4, 22, 8); // shadow
  g.fillStyle(color, 1).fillRoundedRect(8, 20, 16, 22, 5); // body
  g.fillStyle(0xf2d2b0, 1).fillCircle(CHAR_W / 2, 14, 9); // head
  g.fillStyle(0x2a2230, 1).fillRect(7, 5, 18, 7); // hair
  g.fillStyle(0x111111, 1).fillRect(12, 14, 2, 3).fillRect(18, 14, 2, 3); // eyes
  g.generateTexture(key, CHAR_W, CHAR_H);
  g.destroy();
  return key;
}

export const PLACEHOLDER_TILE = TILE;

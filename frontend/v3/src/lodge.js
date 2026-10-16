// Lodge shell textures (SOU-195): corridor, front wall, snow outside, pines and lanterns.
// The generated set (SOU-198) is a 16-slot strip at assets/lodge/tiles.png plus props; any
// piece that isn't there yet is drawn here in the same warm-night palette, so the Lodge
// renders before, during and after the art drop.

export const LODGE_TILES = [
  'snow_a', 'snow_b', 'snow_path', 'corridor_floor', 'corridor_runner', 'wall_cap', 'wall_face',
  'wall_base', 'stone_step', 'room_plank_a', 'room_plank_b', 'stone_floor', 'metal_grate',
  'door_mat', 'snow_edge', 'spare',
];
export const LODGE_PROPS = {
  pine_a: [64, 96], pine_b: [64, 128], pine_small: [32, 64], lantern_post: [32, 64],
  woodpile: [64, 48], fireplace: [64, 96], armchair: [48, 48], wall_lamp: [32, 32],
  window_night: [64, 48], cat_box: [64, 64], rack_v2: [64, 128],
};

export const tileKey = (name) => `lodge-${name}`;
export const lodgePropKey = (name) => `lodge-prop-${name}`;

// small deterministic RNG so the forest doesn't reshuffle on every reload
export function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    return (s >>> 0) / 4294967296;
  };
}

/** Make every lodge-<tile> texture: sliced from the generated strip if loaded, else drawn. */
export function ensureLodgeTextures(scene) {
  const strip = scene.textures.exists('lodge-tiles') ? scene.textures.get('lodge-tiles').getSourceImage() : null;
  LODGE_TILES.forEach((name, i) => {
    const key = tileKey(name);
    if (scene.textures.exists(key)) return;
    if (strip && strip.width >= (i + 1) * 32) {
      const tex = scene.textures.createCanvas(key, 32, 32);
      tex.context.imageSmoothingEnabled = false;
      tex.context.drawImage(strip, i * 32, 0, 32, 32, 0, 0, 32, 32);
      tex.refresh();
    } else {
      drawTile(scene, key, name, i);
    }
  });
  // the strip rooms use as their tileset: the generated one, or the drawn tiles side by side
  if (!scene.textures.exists('lodge-strip')) {
    const tex = scene.textures.createCanvas('lodge-strip', 32 * LODGE_TILES.length, 32);
    tex.context.imageSmoothingEnabled = false;
    LODGE_TILES.forEach((name, i) => tex.context.drawImage(scene.textures.get(tileKey(name)).getSourceImage(), i * 32, 0));
    tex.refresh();
  }
  for (const [name, [w, h]] of Object.entries(LODGE_PROPS)) {
    const key = lodgePropKey(name);
    if (!scene.textures.exists(key)) drawProp(scene, key, name, w, h);
  }
  if (!scene.textures.exists('ph-belt')) drawBelt(scene);
  if (!scene.textures.exists('ph-item')) {
    const g = scene.make.graphics({ x: 0, y: 0 }, false);
    g.fillStyle(0x6b4a2a).fillRect(0, 2, 10, 8).fillStyle(0x9a7040).fillRect(0, 0, 10, 3);
    g.fillStyle(0x3c2a18).fillRect(4, 3, 2, 7);
    g.generateTexture('ph-item', 10, 10);
    g.destroy();
  }
}

function drawTile(scene, key, name, seed) {
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  const r = rng(1000 + seed * 77);
  const speckle = (cols, n) => {
    for (let k = 0; k < n; k++) {
      g.fillStyle(cols[Math.floor(r() * cols.length)]).fillRect(Math.floor(r() * 32), Math.floor(r() * 32), 1, 1);
    }
  };
  const planks = (base, seam, light, rows) => {
    g.fillStyle(base).fillRect(0, 0, 32, 32);
    rows.forEach((y, k) => {
      g.fillStyle(seam).fillRect(0, y, 32, 1);
      g.fillStyle(light).fillRect(0, y + 1, 32, 1);
      const off = (k * 13 + 7) % 32;
      g.fillStyle(seam).fillRect(off, y + 1, 1, (rows[k + 1] ?? 32) - y - 1);
    });
  };
  switch (name) {
    case 'snow_a':
    case 'snow_b':
    case 'spare':
      g.fillStyle(0xd9e2ee).fillRect(0, 0, 32, 32);
      speckle([0xc5d1e1, 0xbac8da, 0xeef3f9], name === 'snow_b' ? 40 : 22);
      if (name === 'snow_b') g.fillStyle(0x9aa7b8).fillRect(9, 20, 2, 1).fillRect(22, 8, 1, 1);
      break;
    case 'snow_path':
      g.fillStyle(0xc4cedc).fillRect(0, 0, 32, 32);
      speckle([0xb3bfcf, 0xd2dbe6], 30);
      g.fillStyle(0xa9b5c6).fillRect(9, 5, 3, 5).fillRect(19, 18, 3, 5);
      break;
    case 'corridor_floor':
      planks(0x6a4428, 0x482c18, 0x7d5232, [0, 8, 16, 24]);
      break;
    case 'room_plank_a':
    case 'room_plank_b':
      planks(name.endsWith('a') ? 0x9a6a40 : 0x8c5e38, 0x6a4426, 0xb07c4c, [0, 8, 16, 24]);
      break;
    case 'corridor_runner':
      g.fillStyle(0x7a2e22).fillRect(0, 2, 32, 28);
      g.fillStyle(0xb8643c).fillRect(0, 4, 32, 2).fillRect(0, 26, 32, 2);
      g.fillStyle(0xd9a05a);
      for (let x = 2; x < 32; x += 8) g.fillRect(x, 14, 4, 4);
      g.fillStyle(0x5a1e16).fillRect(0, 2, 32, 1).fillRect(0, 29, 32, 1);
      break;
    case 'wall_cap':
      g.fillStyle(0x3a281b).fillRect(0, 0, 32, 32);
      g.fillStyle(0x56402c).fillRect(0, 0, 32, 3);
      g.fillStyle(0x2a1c12).fillRect(0, 29, 32, 3);
      break;
    case 'wall_face':
    case 'wall_base':
      // horizontal logs, lit from the top
      for (let y = 0; y < 32; y += 8) {
        g.fillStyle(0x8a5a32).fillRect(0, y, 32, 8);
        g.fillStyle(0xa8743f).fillRect(0, y + 1, 32, 2);
        g.fillStyle(0x5c3a1f).fillRect(0, y + 7, 32, 1);
        g.fillStyle(0x6e4626).fillRect(((y * 3) % 28) + 2, y + 3, 3, 3);
      }
      if (name === 'wall_base') g.fillStyle(0x3a2616).fillRect(0, 26, 32, 6);
      break;
    case 'stone_step':
    case 'stone_floor':
      g.fillStyle(0x7d828c).fillRect(0, 0, 32, 32);
      g.fillStyle(0x5f646e).fillRect(0, 15, 32, 1).fillRect(15, 0, 1, 15).fillRect(7, 16, 1, 16).fillRect(24, 16, 1, 16);
      speckle([0x8e939c, 0x6c717b], 18);
      break;
    case 'metal_grate':
      g.fillStyle(0x3a3e46).fillRect(0, 0, 32, 32);
      g.fillStyle(0x2a2d33);
      for (let x = 2; x < 32; x += 6) g.fillRect(x, 2, 2, 28);
      g.fillStyle(0x6a5a3a).fillRect(0, 0, 32, 1);
      break;
    case 'door_mat':
      g.fillStyle(0x6a4a2a).fillRect(2, 4, 28, 24);
      g.fillStyle(0x8a6a3a);
      for (let y = 7; y < 26; y += 4) g.fillRect(4, y, 24, 1);
      break;
    case 'snow_edge':
      g.fillStyle(0xd9e2ee).fillRect(0, 0, 32, 32);
      g.fillStyle(0xeef3f9).fillRect(0, 0, 32, 6);
      speckle([0xc5d1e1], 16);
      break;
    default:
      g.fillStyle(0xff00ff).fillRect(0, 0, 32, 32);
  }
  g.generateTexture(key, 32, 32);
  g.destroy();
}

function drawProp(scene, key, name, w, h) {
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  const r = rng(name.length * 991);
  if (name.startsWith('pine')) {
    const cx = w / 2;
    g.fillStyle(0x4a2e1a).fillRect(cx - 3, h - 12, 6, 12);
    const tiers = name === 'pine_b' ? 4 : 3;
    const top = 4;
    const span = h - 14 - top;
    for (let t = 0; t < tiers; t++) {
      const y0 = top + (span * t) / tiers;
      const y1 = top + (span * (t + 1.35)) / tiers;
      const half = (w / 2 - 2) * ((t + 1.2) / (tiers + 0.2));
      g.fillStyle(0x1d3d33).fillTriangle(cx, y0, cx - half, y1, cx + half, y1);
      g.fillStyle(0x2a5444).fillTriangle(cx, y0, cx - half * 0.55, y1 - 3, cx, y1 - 3);
      // snow resting on each tier
      g.fillStyle(0xe6edf6).fillTriangle(cx, y0, cx - half * 0.45, y0 + (y1 - y0) * 0.45, cx + half * 0.3, y0 + (y1 - y0) * 0.38);
      g.fillStyle(0xdfe7f2).fillRect(cx - half + 2, y1 - 2, half * 0.7, 2).fillRect(cx + half * 0.2, y1 - 2, half * 0.6, 2);
    }
  } else if (name === 'lantern_post') {
    g.fillStyle(0x24262c).fillRect(14, 14, 4, h - 14).fillRect(10, h - 3, 12, 3);
    g.fillStyle(0x1a1b20).fillRect(9, 4, 14, 3);
    g.fillStyle(0xffc46a).fillRect(11, 7, 10, 10);
    g.fillStyle(0xfff0c0).fillRect(14, 9, 4, 6);
    g.fillStyle(0x24262c).fillRect(10, 17, 12, 2).fillRect(15, 7, 2, 10);
  } else if (name === 'woodpile') {
    for (let row = 0; row < 3; row++) {
      for (let i = 0; i < 5 - row; i++) {
        const x = 4 + row * 5 + i * 11;
        const y = h - 12 - row * 10;
        g.fillStyle(0x7a4e2a).fillCircle(x + 5, y + 5, 5);
        g.fillStyle(0xc89a62).fillCircle(x + 5, y + 5, 3);
      }
    }
    g.fillStyle(0xe6edf6).fillRect(14, h - 38, 30, 3);
  } else {
    // anything else: a soft warm block, honest placeholder until the art lands
    g.fillStyle(0x6a4a30).fillRect(2, h * 0.3, w - 4, h * 0.7 - 2);
    g.fillStyle(0x8a6440).fillRect(2, h * 0.3, w - 4, 4);
    for (let k = 0; k < 6; k++) g.fillStyle(r() > 0.5 ? 0x4fd1b0 : 0xff9a3a).fillRect(6 + r() * (w - 12), h * 0.4 + r() * h * 0.5, 2, 2);
  }
  g.generateTexture(key, w, h);
  g.destroy();
}

// 32x16 belt strip with chevrons; scrolled sideways by the renderer
function drawBelt(scene) {
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  g.fillStyle(0x2a2c31).fillRect(0, 0, 32, 20);
  g.fillStyle(0x45484f).fillRect(0, 0, 32, 2).fillRect(0, 18, 32, 2);
  g.fillStyle(0xc9a23a);
  for (let x = 0; x < 32; x += 8) {
    g.fillRect(x, 6, 2, 2).fillRect(x + 2, 8, 2, 2).fillRect(x + 4, 10, 2, 2);
    g.fillRect(x + 2, 12, 2, 2).fillRect(x, 14, 2, 2);
  }
  g.generateTexture('ph-belt', 32, 20);
  g.destroy();
}

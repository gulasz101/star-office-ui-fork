// Lodge shell textures (SOU-195): corridor, front wall, snow outside, pines and lanterns.
// The generated set (SOU-198) is a 16-slot strip at assets/lodge/tiles.png plus props; any
// piece that isn't there yet is drawn here in the same warm-night palette, so the Lodge
// renders before, during and after the art drop.
// SOU-273 adds the three non-winter seasons' tiles and trees, drawn here until SOU-274..276
// deliver assets/seasons/<season>/. They are deliberately NOT part of LODGE_TILES: that list is
// the rooms' tileset (office.js addTilesetImage('lodge', 'lodge-strip')) and its slot order is a
// published contract mirrored in scripts/v3/build_rooms.py.

import { SEASONS, TREE_SIZE } from './seasons.js';

export const LODGE_TILES = [
  'snow_a', 'snow_b', 'snow_path', 'corridor_floor', 'corridor_runner', 'wall_cap', 'wall_face',
  'wall_base', 'stone_step', 'room_plank_a', 'room_plank_b', 'stone_floor', 'metal_grate',
  'door_mat', 'snow_edge', 'spare',
];
// Extra room tiles appended after the 16 lodge slots (SOU-231): the garage concrete and the
// softened stone/log tiles from the SOU-216 polish pass, each from its own published strip.
export const EXTRA_TILES = [
  ['garage_floor', 'garage-tiles', 0], ['garage_floor_stain', 'garage-tiles', 1],
  ['stone_floor_soft', 'lodge-soft-tiles', 0], ['wall_face_soft', 'lodge-soft-tiles', 1],
];
export const ROOM_TILES = LODGE_TILES.concat(EXTRA_TILES.map(([name]) => name));

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
  // the extra tiles: sliced from their strips, or a stand-in copied from the nearest lodge tile
  const STAND_IN = { garage_floor: 'stone_floor', garage_floor_stain: 'stone_floor', stone_floor_soft: 'stone_floor', wall_face_soft: 'wall_face' };
  for (const [name, stripKey, slot] of EXTRA_TILES) {
    const key = tileKey(name);
    if (scene.textures.exists(key)) continue;
    const src = scene.textures.exists(stripKey) ? scene.textures.get(stripKey).getSourceImage() : null;
    const tex = scene.textures.createCanvas(key, 32, 32);
    tex.context.imageSmoothingEnabled = false;
    if (src && src.width >= (slot + 1) * 32) tex.context.drawImage(src, slot * 32, 0, 32, 32, 0, 0, 32, 32);
    else tex.context.drawImage(scene.textures.get(tileKey(STAND_IN[name])).getSourceImage(), 0, 0);
    tex.refresh();
  }
  if (!scene.textures.exists('lodge-strip')) {
    const tex = scene.textures.createCanvas('lodge-strip', 32 * ROOM_TILES.length, 32);
    tex.context.imageSmoothingEnabled = false;
    ROOM_TILES.forEach((name, i) => tex.context.drawImage(scene.textures.get(tileKey(name)).getSourceImage(), i * 32, 0));
    tex.refresh();
  }
  for (const [name, [w, h]] of Object.entries(LODGE_PROPS)) {
    const key = lodgePropKey(name);
    if (!scene.textures.exists(key)) drawProp(scene, key, name, w, h);
  }
  // the three non-winter seasons (SOU-273): a 4-slot strip at assets/seasons/<season>/tiles.png
  // and three tree props. Sliced when SOU-274..276 have delivered them, drawn here otherwise.
  for (const [season, spec] of Object.entries(SEASONS)) {
    if (season === 'winter') continue; // winter is the lodge's own tiles and pines
    const sheet = scene.textures.exists(`season-tiles-${season}`)
      ? scene.textures.get(`season-tiles-${season}`).getSourceImage() : null;
    [...spec.ground, spec.path, spec.edge].forEach((name, i) => {
      const key = tileKey(name);
      if (scene.textures.exists(key)) return;
      if (sheet && sheet.width >= (i + 1) * 32) {
        const tex = scene.textures.createCanvas(key, 32, 32);
        tex.context.imageSmoothingEnabled = false;
        tex.context.drawImage(sheet, i * 32, 0, 32, 32, 0, 0, 32, 32);
        tex.refresh();
      } else {
        drawTile(scene, key, name, i);
      }
    });
    spec.trees.forEach((name, i) => {
      const key = lodgePropKey(name);
      if (!scene.textures.exists(key)) drawProp(scene, key, name, ...TREE_SIZE[i]);
    });
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
    default: {
      // SOU-273 season tiles: ochre/green ground, an earth path, the lip the ground meets
      const m = /^(autumn|spring|summer)_(ground_[ab]|path|edge)$/.exec(name);
      if (m) drawSeasonTile(g, r, m[1], m[2]);
      else g.fillStyle(0xff00ff).fillRect(0, 0, 32, 32);
    }
  }
  g.generateTexture(key, 32, 32);
  g.destroy();
}

// SOU-273: the procedural look of each non-winter season. ground is [base, alternate]; speck is
// what is scattered over the ground; the trees share a crown pair plus a shadow tone.
const SEASON_ART = {
  autumn: {
    ground: [0x8a6a34, 0x7d5c2b],
    speck: [0xa8802f, 0xb5651d, 0x6e4a22], // fallen leaves
    path: 0x8a6440, pathDark: 0x6e4c2e, edge: 0x7a5636,
    crown: [0xb5651d, 0xb8392e], crownDark: 0x7e3a18, blossom: null,
  },
  spring: {
    ground: [0x5a8a3a, 0x518034],
    speck: [0xf2a6c0, 0xf6f2f7], // flower dots
    path: 0x8a6440, pathDark: 0x6e4c2e, edge: 0x7a5636,
    crown: [0x4e8a3a, 0x62a04c], crownDark: 0x35682a, blossom: 0xf2a6c0,
  },
  summer: {
    ground: [0x3f6b2f, 0x38612a],
    speck: [0x4a7a36, 0x35592a], // deeper blades
    path: 0x8a6440, pathDark: 0x6e4c2e, edge: 0x7a5636,
    crown: [0x2f5d2a, 0x24471f], crownDark: 0x1b3717, blossom: null,
  },
};

function drawSeasonTile(g, r, season, slot) {
  const art = SEASON_ART[season];
  const dots = (cols, n, size = 1) => {
    for (let k = 0; k < n; k++) {
      g.fillStyle(cols[Math.floor(r() * cols.length)]);
      g.fillRect(Math.floor(r() * 32), Math.floor(r() * 32), size, size);
    }
  };
  if (slot === 'path') {
    g.fillStyle(art.path).fillRect(0, 0, 32, 32);
    dots([art.pathDark, art.path], 26);
    g.fillStyle(art.pathDark).fillRect(9, 5, 3, 5).fillRect(19, 18, 3, 5); // two stones
    return;
  }
  if (slot === 'edge') {
    g.fillStyle(art.edge).fillRect(0, 0, 32, 32);
    g.fillStyle(art.pathDark).fillRect(0, 0, 32, 6); // the lip the ground meets
    dots([art.path, art.pathDark], 16);
    return;
  }
  g.fillStyle(art.ground[slot === 'ground_b' ? 1 : 0]).fillRect(0, 0, 32, 32);
  dots(art.speck, season === 'autumn' ? 22 : 12);
  if (season === 'spring') {
    // a handful of flowers big enough to read, not just single pixels
    g.fillStyle(art.speck[0]).fillRect(6, 9, 2, 2).fillRect(23, 24, 2, 2);
    g.fillStyle(art.speck[1]).fillRect(17, 4, 2, 2).fillRect(11, 26, 2, 2);
  }
}

// a round deciduous crown on a short trunk in the season's crown colours; spring's larger trees
// carry blossom. The crown always overlaps the trunk, so the tree never floats.
function drawTree(g, r, season, size, w, h) {
  const art = SEASON_ART[season];
  const cx = w / 2;
  const trunkH = Math.max(8, Math.round(h * 0.16));
  const trunkW = Math.max(4, Math.round(w * 0.11));
  g.fillStyle(0x5a3a22).fillRect(cx - trunkW / 2, h - trunkH, trunkW, trunkH);
  g.fillStyle(0x6e4a2a).fillRect(cx - trunkW / 2, h - trunkH, Math.max(1, Math.round(trunkW / 3)), trunkH);
  const bottom = h - trunkH + trunkH * 0.35; // the crown overlaps the trunk
  const rad = Math.min(w / 2 - 1, (bottom - 1) * 0.36);
  g.fillStyle(art.crownDark).fillCircle(cx, bottom - rad, rad);
  g.fillStyle(art.crown[0]);
  g.fillCircle(cx - rad * 0.55, bottom - rad * 1.35, rad * 0.72);
  g.fillCircle(cx + rad * 0.55, bottom - rad * 1.35, rad * 0.7);
  g.fillStyle(art.crown[1]).fillCircle(cx, bottom - rad * 1.85, rad * 0.6);
  if (art.blossom && size !== 'a') {
    for (let k = 0; k < 12; k++) {
      g.fillStyle(r() > 0.5 ? art.blossom : 0xf6f2f7);
      g.fillRect(Math.round(cx - rad + r() * rad * 2), Math.round(bottom - rad * 2.6 + r() * rad * 1.6), 2, 2);
    }
  }
}

function drawProp(scene, key, name, w, h) {
  const g = scene.make.graphics({ x: 0, y: 0 }, false);
  const r = rng(name.length * 991);
  const tree = /^(autumn|spring|summer)_tree_(a|b|small)$/.exec(name);
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
  } else if (tree) {
    drawTree(g, r, tree[1], tree[2], w, h);
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

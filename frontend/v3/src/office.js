// OfficeModule: one agent's room in the Lodge. It owns its own Tilemap instance (a Tilemap
// layer can't be created twice from one map), its props, zones and character, placed at its
// slot's origin. The character walks on the Building's world grid (corridor included), so
// it can arrive from outside and leave again; zones stay this room's.
import { TILE, COLORS, CHAR_W, CHAR_H } from './config.js';
import { ensurePropTexture, ensureCharacterSheet } from './placeholders.js';
import { AgentCharacter } from './character.js';
import { FLAT_PROPS, SHEET_PROPS, WALL_DECALS, hoodieFor } from './rooms.js';
import { swapHoodie } from './palette.js';
import { ROOM_W } from './world.js';
import { lightRoom, roomShade } from './ambience.js';
import { Helpers } from './helpers.js';

// even with the lights on it's night: a light dusk over the room makes the lamp pools read
const DIM = 0.16;

export class OfficeModule {
  /**
   * @param {Phaser.Scene} scene
   * @param {object} opts
   * @param {string} opts.mapKey   tilemap cache key (Tiled JSON)
   * @param {string} opts.agentId  agent shown in this office
   * @param {number} opts.slot     building slot (0 = middle, then +1, -1, ...)
   * @param {object} opts.grid     buildGrid() of the map
   * @param {Building} opts.building
   * @param {boolean} opts.arrive  walk in from the bottom of the world instead of starting inside
   */
  constructor(scene, { mapKey, agentId, slot = 0, grid, roomType, building, arrive = true, color = 0x4f7cc9 }) {
    this.scene = scene;
    this.agentId = agentId;
    this.slot = slot;
    this.roomType = roomType;
    this.originX = slot * ROOM_W;
    this.originY = 0;
    const originX = this.originX;
    const originY = 0;
    this.ox = originX * TILE;
    this.oy = originY * TILE;

    this.map = scene.make.tilemap({ key: mapKey });
    // rooms share the Lodge strip with the corridor (slot order fixed by the SOU-198 contract)
    const tileset = this.map.addTilesetImage('lodge', 'lodge-strip');
    this.floor = this.map.createLayer('floor', tileset, this.ox, this.oy);
    this.walls = this.map.createLayer('walls', tileset, this.ox, this.oy);
    this.floor.setDepth(-1000);
    // wall faces sit behind everything that stands in the room; caps along the front edge
    // are drawn by y-sorting like props would be, which is good enough for M1
    this.walls.setDepth(-999);

    this.widthTiles = this.map.width;
    this.heightTiles = this.map.height;

    // walkability, props and zones come from the same pure module the tests use
    this.grid = grid;
    this.zones = this.grid.zones;
    this.blocked = this.grid.blocked;
    this.props = this._createProps();
    // what the character walks on: world tiles, this room's zones shifted into the world
    const world = building.world;
    this.nav = {
      scene,
      zones: world.zonesOf(slot),
      tileToWorld: (tx, ty) => ({ x: tx * TILE + TILE / 2, y: ty * TILE + TILE / 2 }),
      path: (from, to) => world.path(from, to),
    };
    this.character = this._createCharacter(color, arrive ? world.spawn : this.nav.zones.entry);
    this.helpers = new Helpers(this);
    this.glows = lightRoom(scene, this);
    this.shade = roomShade(scene, this).setAlpha(DIM);
    this.lightsOn = true;
    this.label = this._createLabel();
    this.debug = null;
  }

  get bounds() {
    return new Phaser.Geom.Rectangle(this.ox, this.oy, this.widthTiles * TILE, this.heightTiles * TILE);
  }

  // tile coords inside this office -> world px of the tile centre
  tileToWorld(tx, ty) {
    return { x: this.ox + tx * TILE + TILE / 2, y: this.oy + ty * TILE + TILE / 2 };
  }

  _createProps() {
    const layer = this.map.getObjectLayer('props');
    const props = [];
    const belts = [];
    for (const o of layer ? layer.objects : []) {
      if (o.name === 'belt') {
        belts.push(o);
        continue;
      }
      const art = `prop-${o.name}`;
      const key = this.scene.textures.exists(art) ? art : ensurePropTexture(this.scene, o.name, o.width, o.height);
      // anchor = bottom-centre of the footprint, so the art can be taller than its footprint
      const x = this.ox + o.x + o.width / 2;
      const y = this.oy + o.y + o.height;
      const animated = SHEET_PROPS[o.name] && this.scene.anims.exists(`prop-anim-${o.name}`);
      const img = animated
        ? this.scene.add.sprite(x, y, key, 0).setOrigin(0.5, 1).play({ key: `prop-anim-${o.name}`, startFrame: Math.floor(Math.random() * SHEET_PROPS[o.name][3]) })
        : this.scene.add.image(x, y, key).setOrigin(0.5, 1);
      img.setDepth(WALL_DECALS.has(o.name) ? -900 : FLAT_PROPS.has(o.name) ? -500 : y);
      props.push({ name: o.name, image: img, x: o.x, y: o.y, w: o.width, h: o.height });
    }
    this._createBelts(belts);
    return props;
  }

  // Conveyor belts (factory): contiguous belt tiles on a row become one scrolling strip with
  // crates riding along it.
  _createBelts(belts) {
    if (!belts.length) return;
    const rows = new Map();
    for (const o of belts) {
      const ty = Math.floor(o.y / TILE);
      if (!rows.has(ty)) rows.set(ty, []);
      rows.get(ty).push(Math.floor(o.x / TILE));
    }
    this.belts = [];
    for (const [ty, xs] of rows) {
      xs.sort((a, b) => a - b);
      let start = xs[0];
      for (let i = 1; i <= xs.length; i++) {
        if (i < xs.length && xs[i] === xs[i - 1] + 1) continue;
        const end = xs[i - 1];
        const x = this.ox + start * TILE;
        const w = (end - start + 1) * TILE;
        // the generated belt (SOU-199) is a 4-frame tile; the drawn strip is the fallback
        const art = this.scene.anims.exists('prop-anim-belt_h');
        const y = this.oy + ty * TILE + (art ? 0 : 6);
        let strip = null;
        if (art) {
          for (let tx = start; tx <= end; tx++) {
            this.scene.add.sprite(this.ox + tx * TILE, y, 'prop-belt_h', 0).setOrigin(0, 0).setDepth(-500).play('prop-anim-belt_h');
          }
        } else {
          strip = this.scene.add.tileSprite(x, y, w, 20, 'ph-belt').setOrigin(0, 0).setDepth(-500);
        }
        const itemKey = this.scene.textures.exists('prop-item_box') ? 'prop-item_box' : 'ph-item';
        const items = [];
        for (let k = 0; k < Math.max(1, Math.round(w / 64)); k++) {
          const item = this.scene.add.image(x + 8, y + (art ? 6 : 4), itemKey).setOrigin(0.5, 0).setDepth(-499);
          this.scene.tweens.add({
            targets: item, x: x + w - 6, duration: w * 28, repeat: -1, delay: (k * w * 28) / Math.round(w / 64 || 1),
          });
          items.push(item);
        }
        if (strip) this.belts.push({ strip, items });
        if (i < xs.length) start = xs[i];
      }
    }
    this.scene.events.on('update', (_t, dt) => {
      for (const b of this.belts) b.strip.tilePositionX -= dt * 0.036;
    });
  }

  _createCharacter(color, start) {
    const base = 'sheet-office-01';
    let sheetKey = this.scene.textures.exists(base) ? base : ensureCharacterSheet(this.scene, `ph-sheet-${this.agentId}`, color);
    const hoodie = hoodieFor(this.agentId);
    if (sheetKey === base && hoodie) sheetKey = recolouredSheet(this.scene, base, `${base}-${this.agentId}`, hoodie);
    return new AgentCharacter(this.nav, { agentId: this.agentId, sheetKey, start });
  }

  // lights out while the agent sleeps, back on when it does anything else
  setLights(on) {
    if (on === this.lightsOn) return;
    this.lightsOn = on;
    this.scene.tweens.add({ targets: this.shade, alpha: on ? DIM : 0.6, duration: 900 });
    for (const g of this.glows) this.scene.tweens.add({ targets: g, alpha: on ? g.getData('a') : 0.04, duration: 900 });
  }

  _createLabel() {
    const z = this.zones.entry || { tx: 1, ty: 1 };
    const doorway = this.tileToWorld(z.tx + 0.5, this.heightTiles - 1);
    return this.scene.add
      .text(doorway.x, doorway.y + TILE / 2 + 2, this.agentId, {
        fontFamily: 'monospace', fontSize: '10px', color: COLORS.label,
        stroke: COLORS.labelShadow, strokeThickness: 3,
      })
      .setOrigin(0.5, 0)
      .setDepth(100000);
  }

  toggleDebug() {
    if (this.debug) {
      this.debug.destroy();
      this.debug = null;
      return;
    }
    const g = this.scene.add.graphics().setDepth(200000);
    g.fillStyle(0xff3355, 0.28);
    for (let y = 0; y < this.heightTiles; y++) {
      for (let x = 0; x < this.widthTiles; x++) {
        if (this.blocked[y][x]) g.fillRect(this.ox + x * TILE, this.oy + y * TILE, TILE, TILE);
      }
    }
    const texts = [];
    for (const [name, z] of Object.entries(this.zones)) {
      g.fillStyle(0x33e0a0, 0.55).fillRect(this.ox + z.tx * TILE + 4, this.oy + z.ty * TILE + 4, TILE - 8, TILE - 8);
      const p = this.tileToWorld(z.tx, z.ty);
      texts.push(this.scene.add.text(p.x, p.y - 22, name, {
        fontFamily: 'monospace', fontSize: '9px', color: '#aaffdd', stroke: '#000', strokeThickness: 3,
      }).setOrigin(0.5, 1).setDepth(200001));
    }
    this.debug = { destroy: () => { g.destroy(); texts.forEach((t) => t.destroy()); } };
  }
}

// A canvas copy of the generated sheet with the hoodie recoloured (SOU-202), sliced into the
// same numbered frames the animations use.
function recolouredSheet(scene, baseKey, key, hoodie) {
  if (scene.textures.exists(key)) return key;
  const src = scene.textures.get(baseKey).getSourceImage();
  const tex = scene.textures.createCanvas(key, src.width, src.height);
  const ctx = tex.context;
  ctx.drawImage(src, 0, 0);
  const img = ctx.getImageData(0, 0, src.width, src.height);
  swapHoodie(img.data, hoodie);
  ctx.putImageData(img, 0, 0);
  tex.refresh();
  const cols = Math.floor(src.width / CHAR_W);
  const rows = Math.floor(src.height / CHAR_H);
  for (let i = 0; i < cols * rows; i++) tex.add(i, 0, (i % cols) * CHAR_W, Math.floor(i / cols) * CHAR_H, CHAR_W, CHAR_H);
  return key;
}

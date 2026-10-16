// OfficeModule: one agent's office. It owns its own Tilemap instance (a Tilemap layer can't be
// created twice from one map), its props, zones and character slot, all placed at a world
// origin so the Building can put many offices side by side (M2).
import { TILE, COLORS } from './config.js';
import { ensurePropTexture, ensureCharacterTexture } from './placeholders.js';

export class OfficeModule {
  /**
   * @param {Phaser.Scene} scene
   * @param {object} opts
   * @param {string} opts.mapKey   tilemap cache key (Tiled JSON)
   * @param {string} opts.agentId  agent shown in this office
   * @param {number} opts.originX  world origin in tiles
   * @param {number} opts.originY  world origin in tiles
   */
  constructor(scene, { mapKey, agentId, originX = 0, originY = 0, color = 0x4f7cc9 }) {
    this.scene = scene;
    this.agentId = agentId;
    this.originX = originX;
    this.originY = originY;
    this.ox = originX * TILE;
    this.oy = originY * TILE;

    this.map = scene.make.tilemap({ key: mapKey });
    const tileset = this.map.addTilesetImage('placeholder', 'tiles-placeholder');
    this.floor = this.map.createLayer('floor', tileset, this.ox, this.oy);
    this.walls = this.map.createLayer('walls', tileset, this.ox, this.oy);
    this.floor.setDepth(-1000);
    // wall faces sit behind everything that stands in the room; caps along the front edge
    // are drawn by y-sorting like props would be, which is good enough for M1
    this.walls.setDepth(-999);

    this.widthTiles = this.map.width;
    this.heightTiles = this.map.height;

    this.zones = this._readZones();
    this.props = this._createProps();
    this.blocked = this._buildCollisionGrid();
    this.character = this._createCharacter(color);
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

  _readZones() {
    const layer = this.map.getObjectLayer('zones');
    const zones = {};
    for (const o of layer ? layer.objects : []) {
      zones[o.name] = { tx: Math.floor(o.x / TILE), ty: Math.floor(o.y / TILE) };
    }
    return zones;
  }

  _createProps() {
    const layer = this.map.getObjectLayer('props');
    const props = [];
    for (const o of layer ? layer.objects : []) {
      const key = ensurePropTexture(this.scene, o.name, o.width, o.height);
      // anchor = bottom-centre of the footprint, so the art can be taller than its footprint
      const x = this.ox + o.x + o.width / 2;
      const y = this.oy + o.y + o.height;
      const img = this.scene.add.image(x, y, key).setOrigin(0.5, 1);
      const collides = (o.properties || []).find((p) => p.name === 'collides');
      const flat = o.name === 'rug';
      img.setDepth(flat ? -500 : y);
      props.push({
        name: o.name, image: img,
        tx: Math.floor(o.x / TILE), ty: Math.floor(o.y / TILE),
        tw: Math.round(o.width / TILE), th: Math.round(o.height / TILE),
        collides: collides ? collides.value : true,
      });
    }
    return props;
  }

  // true = can't walk. Walls come from tile property `collides`, props from their footprint.
  _buildCollisionGrid() {
    const grid = [];
    for (let y = 0; y < this.heightTiles; y++) {
      const row = [];
      for (let x = 0; x < this.widthTiles; x++) {
        const t = this.walls.getTileAt(x, y);
        row.push(Boolean(t && t.properties && t.properties.collides));
      }
      grid.push(row);
    }
    for (const p of this.props) {
      if (!p.collides) continue;
      for (let y = p.ty; y < p.ty + p.th; y++) {
        for (let x = p.tx; x < p.tx + p.tw; x++) {
          if (grid[y] && x < this.widthTiles) grid[y][x] = true;
        }
      }
    }
    return grid;
  }

  _createCharacter(color) {
    const key = ensureCharacterTexture(this.scene, `ph-char-${this.agentId}`, color);
    const z = this.zones.entry || { tx: 1, ty: 1 };
    const p = this.tileToWorld(z.tx, z.ty);
    // feet at the bottom of the tile
    const sprite = this.scene.add.image(p.x, p.y + TILE / 2, key).setOrigin(0.5, 1);
    sprite.setDepth(sprite.y);
    return sprite;
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

// OfficeModule: one agent's office. It owns its own Tilemap instance (a Tilemap layer can't be
// created twice from one map), its props, zones and character slot, all placed at a world
// origin so the Building can put many offices side by side (M2).
import { TILE, COLORS } from './config.js';
import { ensurePropTexture, ensureCharacterSheet } from './placeholders.js';
import { AgentCharacter } from './character.js';
import { buildGrid, findPath } from './grid.js';

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

    // walkability, props and zones come from the same pure module the tests use
    this.grid = buildGrid(scene.cache.tilemap.get(mapKey).data);
    this.zones = this.grid.zones;
    this.blocked = this.grid.blocked;
    this.props = this._createProps();
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

  _createProps() {
    const layer = this.map.getObjectLayer('props');
    const props = [];
    for (const o of layer ? layer.objects : []) {
      const key = ensurePropTexture(this.scene, o.name, o.width, o.height);
      // anchor = bottom-centre of the footprint, so the art can be taller than its footprint
      const x = this.ox + o.x + o.width / 2;
      const y = this.oy + o.y + o.height;
      const img = this.scene.add.image(x, y, key).setOrigin(0.5, 1);
      const flat = o.name === 'rug';
      img.setDepth(flat ? -500 : y);
      props.push({ name: o.name, image: img });
    }
    return props;
  }

  // path between two tiles of this office, or null
  path(from, to) {
    return findPath(this.grid, from, to);
  }

  _createCharacter(color) {
    const sheetKey = ensureCharacterSheet(this.scene, `ph-sheet-${this.agentId}`, color);
    return new AgentCharacter(this, {
      agentId: this.agentId, sheetKey, start: this.zones.entry || { tx: 1, ty: 1 },
    });
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

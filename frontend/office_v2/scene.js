/**
 * scene.js - the engine scene of renderer v2 (SOU-124).
 *
 * Contract from the issue, line by line:
 *   preload: tileset images + the map JSON + the prop textures;
 *   create:  this.add.tilemap / addTilesetImage / createLayer for `floor`,
 *            and for `collision` a layer with setCollisionByProperty({collides:true});
 *            props grounded per SOU-147 (the SOU-124 AC3 correction): the
 *            CONTENT measured at publish time (props[].art_bbox + props[].image)
 *            lands base-centre on tileToWorldXY(gx, gy) and its on-screen width
 *            is tiles * tile_w - the image box is padding, not the object;
 *            depth = depthKey(gx, gy, map width) -
 *            the one rule from depth.js, no magic constants anywhere else;
 *            walls.png as a plain image BEHIND the tilemap, positioned from
 *            walls.origin (its top-left, per gen_iso_room.build_walls);
 *            camera bounds = the map's world extent, centred on the room.
 *
 * A small readout is exposed on window.__SOU2 so the browser proof (SOU-124
 * S5) can print real numbers for AC3/AC4/AC5 from the RUNNING page.
 */
import { depthKey, WALL_DEPTH, FLOOR_DEPTH } from './depth.js';

/** Independent evaluation of the descriptor's declared lattice formula.
 *  Nothing here reads Phaser: it is the second opinion the proof compares
 *  the engine's tileToWorldXY against (AC3 middle column). */
export function latticeScreen(desc, gx, gy) {
  const [ox, oy] = desc.lattice.origin;
  const tw = desc.lattice.tile_w;
  const th = desc.lattice.tile_h;
  return { x: ox + (tw / 2) * (gx - gy), y: oy + (gx + gy) * (th / 2) };
}

class RoomScene extends Phaser.Scene {
  constructor(descriptor, packBase) {
    super({ key: 'room' });
    this.desc = descriptor;
    this.base = packBase;
  }

  preload() {
    const d = this.desc;
    this.load.image('tiles-floor', this.base + d.tileset.image);
    this.load.image('tiles-collision', this.base + d.tileset.collision_image);
    this.load.image('walls', this.base + d.walls.image);
    this.load.tilemapTiledJSON('room', this.base + d.map);
    for (const p of d.props) this.load.image('prop:' + p.id, this.base + p.asset);
  }

  create() {
    const d = this.desc;
    const [ox, oy] = d.lattice.origin;

    // --- tilemap: the engine's own isometric layers (CPU TilemapLayer;
    // TilemapGPULayer is orthographic-only and not applicable here) --------
    const map = this.add.tilemap('room');
    const floorSet = map.addTilesetImage('floor', 'tiles-floor');
    const collisionSet = map.addTilesetImage('collision', 'tiles-collision');
    if (!floorSet || !collisionSet) {
      throw new Error('tileset name mismatch: room.map.json tilesets must be "floor" and "collision"');
    }
    const collisionLayer = map.createLayer('collision', collisionSet, ox, oy, false);
    collisionLayer.setDepth(FLOOR_DEPTH);
    collisionLayer.setCollisionByProperty({ collides: true });
    collisionLayer.setVisible(false); // hidden authority for walkability, never drawn

    // walls BEHIND everything lattice: created before the floor and ranked
    // from depth.js (wall < floor < any prop cell). Position = walls.origin,
    // the image's TOP-LEFT on the canvas (build_walls emits it as such).
    const [wx, wy] = d.walls.origin;
    const walls = this.add.image(wx, wy, 'walls').setOrigin(0, 0).setDepth(WALL_DEPTH);

    const floorLayer = map.createLayer('floor', floorSet, ox, oy, false);
    floorLayer.setDepth(FLOOR_DEPTH);

    // --- props from the descriptor, by grid coordinate --------------------
    // SOU-147 grounding. The pack PNGs are 640px canvases: setOrigin(0.5, 1)
    // pinned the IMAGE box bottom to the lattice point while the drawn object
    // sits 45-163 px above it, and nothing was scaled, so props hovered at
    // canvas size. The measured art_bbox (alpha > 128 tight rect, recorded at
    // publish time) is what lands on the lattice point, at the DECLARED width
    // tiles * tile_w. The scene stays dumb: every number below is data.
    const rowLength = map.width; // tie-free base for the depth rule = grid width
    const placed = [];
    for (const p of d.props) {
      const [gx, gy] = p.grid;
      if (!Array.isArray(p.art_bbox) || !Array.isArray(p.image) || p.art_bbox.length !== 4) {
        throw new Error(`prop '${p.id}' carries no measured art_bbox/image - ` +
                        'this pack predates SOU-147 grounding; republish it');
      }
      const [bx0, by0, bx1, by1] = p.art_bbox;
      const [imgW, imgH] = p.image;
      const pos = floorLayer.tileToWorldXY(gx, gy); // engine truth, layer origin included
      const scale = (p.tiles * d.lattice.tile_w) / (bx1 - bx0);
      const img = this.add
        .image(pos.x, pos.y, 'prop:' + p.id)
        .setOrigin((bx0 + bx1) / 2 / imgW, by1 / imgH) // anchor = CONTENT bottom-centre
        .setScale(scale)
        .setDepth(depthKey(gx, gy, rowLength));
      placed.push({ id: p.id, gx, gy, engine: { x: pos.x, y: pos.y }, img,
                    artBbox: p.art_bbox, image: p.image, tiles: p.tiles });
    }

    // --- camera: bounds = the map's world extent, centred on the room -----
    const cam = this.cameras.main;
    cam.setBounds(0, 0, d.canvas.w, d.canvas.h);
    const centre = floorLayer.tileToWorldXY(d.grid.w / 2, d.grid.h / 2);
    cam.centerOn(centre.x, centre.y);

    // --- readout for the proof harness -------------------------------------
    // NOTE (SOU-124 finding): Phaser 4.2.1 does not define
    // Phaser.Core.Events.POST_UPDATE (the v3 constant is gone), so the readout
    // is emitted synchronously at the end of create() - every number below is
    // pure scene-graph state that exists once create() has run.
    this.__v2 = { map, floorLayer, collisionLayer, walls, placed, rowLength };
    {
      window.__SOU2 = {
        ready: true,
        phaserVersion: Phaser.VERSION,
        renderer: this.game.renderer ? this.game.renderer.name || String(this.game.renderer.type) : 'headless',
        roomId: d.room_id,
        canvas: { w: d.canvas.w, h: d.canvas.h },
        grid: { w: d.grid.w, h: d.grid.h },
        walls: { x: this.__v2.walls.x, y: this.__v2.walls.y, depth: this.__v2.walls.depth },
        floor: {
          isTilemapLayer: this.__v2.floorLayer instanceof Phaser.Tilemaps.TilemapLayer,
          position: { x: this.__v2.floorLayer.x, y: this.__v2.floorLayer.y },
          depth: this.__v2.floorLayer.depth,
          tiles: this.__v2.floorLayer.getTilesWithin(0, 0, map.width, map.height, { interesting: true }).length,
        },
        props: this.__v2.placed.map((p) => {
          // grounding numbers read from the LIVE object (position, origin,
          // scale, texture size) - the proof recomputes nothing the scene
          // asserts about itself; sprite.x alone is no longer the report.
          const img = p.img;
          const [bx0, by0, bx1, by1] = p.artBbox;
          const [imgW, imgH] = p.image;
          const sx = Math.abs(img.scaleX);
          const sy = Math.abs(img.scaleY);
          return {
            id: p.id,
            grid: [p.gx, p.gy],
            computed: latticeScreen(d, p.gx, p.gy), // descriptor formula, engine-independent
            engine: p.engine,                       // tileToWorldXY at placement
            sprite: {
              x: img.x, y: img.y, depth: img.depth,
              scaleX: img.scaleX, scaleY: img.scaleY,
              originX: img.originX, originY: img.originY,
              displayWidth: img.displayWidth, displayHeight: img.displayHeight,
            },
            grounding: {
              art_bbox: p.artBbox, image: p.image, tiles: p.tiles,
              declared_width: p.tiles * d.lattice.tile_w,      // AC2 contract
              content_width: (bx1 - bx0) * sx,                  // live scale x measured bbox
              content_base: {                                   // where the CONTENT bottom-centre
                x: img.x + ((bx0 + bx1) / 2 - img.originX * imgW) * sx, // actually lands on screen
                y: img.y + (by1 - img.originY * imgH) * sy,             // (general formula: origin
              },                                                          // and scale from the object)
            },
          };
        }),
        collision: (() => {
          const cl = this.__v2.collisionLayer;
          let flagged = 0;
          cl.forEachTile((t) => { if (t && t.properties && t.properties.collides === true && t.collideRight === true) flagged++; });
          const placedTiles = cl.filterTiles((t) => t && t.index >= 0).length;
          const sampleCell = d.blocked.find(([bx, by]) => bx === 8 && by === 2) || d.blocked[0];
          const t = cl.getTileAt(sampleCell[0], sampleCell[1], true);
          const within = cl.getTilesWithin(0, 0, map.width, map.height, { interesting: true }).length;
          return {
            isTilemapLayer: cl instanceof Phaser.Tilemaps.TilemapLayer,
            isTilemapGPULayer: cl instanceof Phaser.Tilemaps.TilemapGPULayer,
            visible: cl.visible,
            descriptorBlockedCount: d.blocked.length,
            getTilesWithinRaw: within,
            placedTiles,
            collidesFlagged: flagged,
            sampleBlocked: {
              cell: sampleCell,
              tileIndex: t ? t.index : null,
              collidesProp: t ? t.properties.collides : null,
              collideEdges: t ? [t.collideLeft, t.collideRight, t.collideUp, t.collideDown] : null,
            },
          };
        })(),
        depth: (() => {
          const byId = Object.fromEntries(this.__v2.placed.map((p) => [p.id, p.img.depth]));
          const drawnOrder = this.__v2.placed
            .slice()
            .sort((a, b) => a.img.depth - b.img.depth || a.img.y - b.img.y || a.img.x - b.img.x)
            .map((p) => p.id);
          return { perProp: byId, rowLength: this.__v2.rowLength, drawnOrder };
        })(),
      };
    }
  }
}

export function createRoomScene(descriptor, packBase) {
  return new RoomScene(descriptor, packBase);
}

export function exposeReadout(roomId) {
  window.__SOU2 = { ready: false, roomId };
}

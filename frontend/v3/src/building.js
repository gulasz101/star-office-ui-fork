// Building: the Lodge (SOU-195). Rooms sit in slots along one corridor, slot 0 in the middle;
// the front wall has one entrance under slot 0 and a path runs from it to the bottom of the
// world, where arriving agents appear. Layout maths lives in world.js (pure, tested).
// The outside — ground, path, edge, trees and their night tint — is the season's (SOU-273).
import { TILE } from './config.js';
import { OfficeModule } from './office.js';
import { buildGrid } from './grid.js';
import { roomTypeFor } from './rooms.js';
import { ensureLodgeTextures, tileKey, lodgePropKey, rng } from './lodge.js';
import { ensureAmbienceTextures, glow } from './ambience.js';
import { SEASONS } from './seasons.js';
import { World, slotForIndex, ROOM_W, ROOM_H, CORRIDOR_Y, CORRIDOR_H, FRONT_Y, SPAWN_Y, DOOR_COLS } from './world.js';

const AGENT_COLORS = [0x4f7cc9, 0xc94f6d, 0x4fc98a, 0xc9a24f, 0x8a4fc9, 0x4fbfc9];
const FOREST_MARGIN = 26; // tiles of forest drawn around the building

// A long building's overview is zoomed far out, so on a tall screen the field above and
// below must grow with the building's width or the void shows (SOU-226 review).
const vMargin = (spanTiles) => Math.max(FOREST_MARGIN, Math.ceil(spanTiles * 0.4));

export class Building {
  constructor(scene, season = 'winter') {
    this.scene = scene;
    this.season = season;
    this.offices = new Map();
    this.world = new World();
    this.shell = [];
    ensureLodgeTextures(scene);
    ensureAmbienceTextures(scene);
    this.drawShell();
  }

  // the season this building is drawn in: its tiles, trees, tint and weather
  get spec() {
    return SEASONS[this.season] || SEASONS.winter;
  }

  addOffice(agentId, { arrive = true } = {}) {
    if (this.offices.has(agentId)) return this.offices.get(agentId);
    const index = this.offices.size;
    const slot = slotForIndex(index);
    const roomType = roomTypeFor(agentId);
    const mapKey = `room-${roomType}`;
    const grid = buildGrid(this.scene.cache.tilemap.get(mapKey).data);
    this.world.addRoom(slot, grid);
    const office = new OfficeModule(this.scene, {
      mapKey, agentId, slot, grid, roomType, building: this, arrive,
      color: AGENT_COLORS[index % AGENT_COLORS.length],
    });
    this.offices.set(agentId, office);
    this.drawShell();
    this.scene.events.emit('building-grew', office);
    return office;
  }

  // the building itself (rooms, corridor, front wall, entrance steps) in world px
  get bounds() {
    const x0 = this.world.x0 * TILE;
    const x1 = (this.world.x1 + 1) * TILE;
    return new Phaser.Geom.Rectangle(x0, -TILE / 2, x1 - x0, (FRONT_Y + 3) * TILE);
  }

  // everything worth scrolling to: the whole snow field drawn around the building
  get worldBounds() {
    const x0 = (this.world.x0 - FOREST_MARGIN) * TILE;
    const x1 = (this.world.x1 + 1 + FOREST_MARGIN) * TILE;
    const vm = vMargin(this.world.x1 - this.world.x0 + 1);
    return new Phaser.Geom.Rectangle(x0, -vm * TILE, x1 - x0, (SPAWN_Y + vm * 2) * TILE);
  }

  drawShell() {
    for (const o of this.shell) o.destroy();
    this.shell = [];
    const s = this.scene;
    const w = this.world;
    const add = (o) => (this.shell.push(o), o);
    const strip = (name, tx, ty, wt, ht, depth) =>
      add(s.add.tileSprite(tx * TILE, ty * TILE, wt * TILE, ht * TILE, tileKey(name)).setOrigin(0, 0).setDepth(depth));

    const x0 = w.x0;
    const x1 = w.x1;
    const span = x1 - x0 + 1;

    // the season's ground everywhere around the building, then the trodden path up to the
    // entrance; everything outside is tinted for the night, the inside stays warm.
    // ground[1] is the strip's alternate ground slot (winter's snow_b): the field is painted
    // with ground[0], exactly as it was before the seasons landed.
    const spec = this.spec;
    const night = (o, tint = spec.tint) => o.setTint(tint);
    const vm = vMargin(span);
    night(strip(spec.ground[0], x0 - FOREST_MARGIN, -vm, span + FOREST_MARGIN * 2, SPAWN_Y + vm * 2, -3000));
    night(strip(spec.path, DOOR_COLS[0], FRONT_Y + 1, 2, SPAWN_Y - FRONT_Y + 12, -2990));
    night(strip(spec.edge, x0, FRONT_Y + 1, span, 1, -2995));

    // corridor: floor, runner, end walls, front wall with the entrance gap
    strip('corridor_floor', x0 + 1, CORRIDOR_Y, span - 2, CORRIDOR_H, -1000);
    add(s.add.tileSprite((x0 + 1) * TILE, (CORRIDOR_Y + 1.5) * TILE, (span - 2) * TILE, TILE, tileKey('corridor_runner'))
      .setOrigin(0, 0).setDepth(-998));
    strip('wall_cap', x0, CORRIDOR_Y, 1, CORRIDOR_H + 1, -999);
    strip('wall_cap', x1, CORRIDOR_Y, 1, CORRIDOR_H + 1, -999);
    strip('wall_cap', x0, FRONT_Y, DOOR_COLS[0] - x0, 1, -999);
    strip('wall_cap', DOOR_COLS[1] + 1, FRONT_Y, x1 - DOOR_COLS[1], 1, -999);
    strip('door_mat', DOOR_COLS[0], FRONT_Y, 2, 1, -999);
    // no rooms yet: close the corridor's north side so the empty lodge is a whole building
    if (!this.offices.size) strip('wall_cap', x0, ROOM_H - 1, span, 1, -999);
    // the outside face of the front wall (logs), with the doorway left open
    night(strip('wall_face_soft', x0, FRONT_Y + 1, DOOR_COLS[0] - x0, 1, -2980), 0xb4aac4);
    night(strip('wall_face_soft', DOOR_COLS[1] + 1, FRONT_Y + 1, x1 - DOOR_COLS[1], 1, -2980), 0xb4aac4);
    night(strip('stone_step', DOOR_COLS[0], FRONT_Y + 1, 2, 1, -2980), 0xb4aac4);
    // warm light spilling out of the doorway onto the snow
    add(glow(s, (DOOR_COLS[0] + 1) * TILE, (FRONT_Y + 1.6) * TILE, 90, 0xffb35a, 0.3, -2900));

    // lanterns either side of the entrance and at the corridor ends
    const prop = (name, tx, ty, opts = {}) => {
      const img = s.add.image(tx * TILE + (opts.dx || 0), ty * TILE, lodgePropKey(name)).setOrigin(0.5, 1);
      img.setDepth(opts.depth ?? ty * TILE);
      return add(img);
    };
    for (const lx of [DOOR_COLS[0] - 0.5, DOOR_COLS[1] + 1.5]) {
      prop('lantern_post', lx, FRONT_Y + 2.6);
      add(glow(s, lx * TILE, (FRONT_Y + 2.6) * TILE - 50, 64, 0xffb35a, 0.5));
    }
    night(prop('woodpile', x0 + 2.5, FRONT_Y + 3.4), 0xb4aac4);
    // a reading armchair at each end of the corridor, with its own lamp glow
    for (const [ax, flip] of [[x0 + 1.9, false], [x1 - 0.9, true]]) {
      prop('armchair', ax, CORRIDOR_Y + CORRIDOR_H).setFlipX(flip);
      add(glow(s, ax * TILE, (CORRIDOR_Y + CORRIDOR_H - 1) * TILE, 56, 0xffb35a, 0.22));
    }

    // the forest: deterministic scatter outside the building footprint, never on the path
    const r = rng(4242);
    const bx0 = x0 - 1;
    const bx1 = x1 + 1;
    for (let ty = -vm + 2; ty < SPAWN_Y + vm; ty += 2) {
      for (let tx = x0 - FOREST_MARGIN; tx < x1 + FOREST_MARGIN; tx += 2) {
        const jx = tx + r() * 1.6;
        const jy = ty + r() * 1.6;
        const roll = r();
        const insideX = jx > bx0 - 1.5 && jx < bx1 + 1.5;
        if (insideX && jy > -1 && jy < FRONT_Y + 4) continue; // the building and its front yard
        if (Math.abs(jx - (DOOR_COLS[0] + 1)) < 4 && jy >= FRONT_Y) continue; // the path
        const nearBuilding = Math.abs(jy - FRONT_Y / 2) < FRONT_Y && (Math.abs(jx - bx0) < 6 || Math.abs(jx - bx1) < 6);
        if (roll > (nearBuilding ? 0.55 : 0.42)) continue;
        // the season's trees in the pines' three roles: tall, big, small. The r() sequence is
        // untouched — one draw, and a second only off the first branch — so the scatter never
        // moves; only the sprite changes.
        const kind = r() < 0.35 ? spec.trees[1] : r() < 0.7 ? spec.trees[0] : spec.trees[2];
        const img = night(prop(kind, jx, jy + 1));
        if (jy + 1 <= 0) img.setDepth(-1500 + jy); // behind the rooms' back walls
      }
    }
  }
}

export { ROOM_W, ROOM_H };

// Building: the Lodge (SOU-195). Rooms sit in slots along one corridor, slot 0 in the middle;
// the front wall has one entrance under slot 0 and a snow path runs from it to the bottom of
// the world, where arriving agents appear. Layout maths lives in world.js (pure, tested).
import { TILE } from './config.js';
import { OfficeModule } from './office.js';
import { buildGrid } from './grid.js';
import { roomTypeFor } from './rooms.js';
import { ensureLodgeTextures, tileKey, lodgePropKey, rng } from './lodge.js';
import { ensureAmbienceTextures, glow, NIGHT_TINT } from './ambience.js';
import { World, slotForIndex, ROOM_W, ROOM_H, CORRIDOR_Y, CORRIDOR_H, FRONT_Y, SPAWN_Y, DOOR_COLS } from './world.js';

const AGENT_COLORS = [0x4f7cc9, 0xc94f6d, 0x4fc98a, 0xc9a24f, 0x8a4fc9, 0x4fbfc9];
const FOREST_MARGIN = 26; // tiles of snowy forest drawn around the building

export class Building {
  constructor(scene) {
    this.scene = scene;
    this.offices = new Map();
    this.world = new World();
    this.shell = [];
    ensureLodgeTextures(scene);
    ensureAmbienceTextures(scene);
    this.drawShell();
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
    return new Phaser.Geom.Rectangle(x0, -FOREST_MARGIN * TILE, x1 - x0, (SPAWN_Y + FOREST_MARGIN * 2) * TILE);
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

    // snow everywhere around the building, then the trodden path up to the entrance;
    // everything outside is tinted for the night, the inside stays warm
    const night = (o, tint = NIGHT_TINT) => o.setTint(tint);
    night(strip('snow_a', x0 - FOREST_MARGIN, -FOREST_MARGIN, span + FOREST_MARGIN * 2, SPAWN_Y + FOREST_MARGIN * 2, -3000));
    night(strip('snow_path', DOOR_COLS[0], FRONT_Y + 1, 2, SPAWN_Y - FRONT_Y + 12, -2990));
    night(strip('snow_edge', x0, FRONT_Y + 1, span, 1, -2995));

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
    night(strip('wall_face', x0, FRONT_Y + 1, DOOR_COLS[0] - x0, 1, -2980), 0xb4aac4);
    night(strip('wall_face', DOOR_COLS[1] + 1, FRONT_Y + 1, x1 - DOOR_COLS[1], 1, -2980), 0xb4aac4);
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

    // the forest: deterministic scatter outside the building footprint, never on the path
    const r = rng(4242);
    const bx0 = x0 - 1;
    const bx1 = x1 + 1;
    for (let ty = -FOREST_MARGIN + 2; ty < SPAWN_Y + FOREST_MARGIN; ty += 2) {
      for (let tx = x0 - FOREST_MARGIN; tx < x1 + FOREST_MARGIN; tx += 2) {
        const jx = tx + r() * 1.6;
        const jy = ty + r() * 1.6;
        const roll = r();
        const insideX = jx > bx0 - 1.5 && jx < bx1 + 1.5;
        if (insideX && jy > -1 && jy < FRONT_Y + 4) continue; // the building and its front yard
        if (Math.abs(jx - (DOOR_COLS[0] + 1)) < 4 && jy >= FRONT_Y) continue; // the path
        const nearBuilding = Math.abs(jy - FRONT_Y / 2) < FRONT_Y && (Math.abs(jx - bx0) < 6 || Math.abs(jx - bx1) < 6);
        if (roll > (nearBuilding ? 0.55 : 0.42)) continue;
        const kind = r() < 0.35 ? 'pine_b' : r() < 0.7 ? 'pine_a' : 'pine_small';
        const img = night(prop(kind, jx, jy + 1));
        if (jy + 1 <= 0) img.setDepth(-1500 + jy); // behind the rooms' back walls
      }
    }
  }
}

export { ROOM_W, ROOM_H };

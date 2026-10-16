// The Lodge world (SOU-195), pure: where rooms go, which tiles are walkable across the whole
// building, and A* over it. No Phaser; the renderer and the Node tests share it.
//
// Rows (world tiles, y down):
//   0 .. ROOM_H-1           rooms, one per slot, each ROOM_W wide; doors in their front wall
//   CORRIDOR_Y .. +H-1      the corridor, open along the whole building, end walls at the ends
//   FRONT_Y                 the building's front wall; one entrance below slot 0
//   FRONT_Y+1 .. SPAWN_Y    outside: a snow path from the entrance to the bottom edge
// Slot 0 is in the middle; the building grows +1, -1, +2, -2, ... so it stays balanced.

export const ROOM_W = 16;
export const ROOM_H = 12;
export const CORRIDOR_Y = ROOM_H;
export const CORRIDOR_H = 4;
export const FRONT_Y = CORRIDOR_Y + CORRIDOR_H;
export const OUTSIDE_H = 8;
export const SPAWN_Y = FRONT_Y + OUTSIDE_H;
export const DOOR_COLS = [6, 7]; // inside a room / under slot 0 for the entrance
export const PATH_COLS = [5, 6, 7, 8];

export const slotForIndex = (i) => (i === 0 ? 0 : i % 2 ? (i + 1) / 2 : -i / 2);

export class World {
  constructor() {
    this.rooms = new Map(); // slot -> { grid, zones }
  }

  addRoom(slot, grid) {
    this.rooms.set(slot, grid);
  }

  get minSlot() {
    return this.rooms.size ? Math.min(...this.rooms.keys()) : 0;
  }

  get maxSlot() {
    return this.rooms.size ? Math.max(...this.rooms.keys()) : 0;
  }

  // world tile x range of the building, walls included
  get x0() {
    return this.minSlot * ROOM_W;
  }

  get x1() {
    return (this.maxSlot + 1) * ROOM_W - 1;
  }

  get spawn() {
    return { tx: DOOR_COLS[0], ty: SPAWN_Y };
  }

  // room tile -> world tile
  toWorld(slot, tx, ty) {
    return { tx: slot * ROOM_W + tx, ty };
  }

  zonesOf(slot) {
    const room = this.rooms.get(slot);
    const out = {};
    for (const [name, z] of Object.entries(room.zones)) out[name] = { ...z, tx: slot * ROOM_W + z.tx };
    return out;
  }

  walkable(x, y) {
    if (y < 0 || y > SPAWN_Y || x < this.x0 || x > this.x1) return false;
    if (y < ROOM_H) {
      const slot = Math.floor(x / ROOM_W);
      const room = this.rooms.get(slot);
      if (!room) return false;
      return !room.blocked[y][x - slot * ROOM_W];
    }
    if (y < FRONT_Y) return x > this.x0 && x < this.x1;
    if (y === FRONT_Y) return DOOR_COLS.includes(x);
    return PATH_COLS.includes(x);
  }

  /** A* (4-way, Manhattan, binary heap) between world tiles; [[x,y], ...] or null. */
  path([sx, sy], [gx, gy]) {
    if (!this.walkable(gx, gy)) return null;
    const W = this.x1 - this.x0 + 1;
    const key = (x, y) => y * W + (x - this.x0);
    const h = (x, y) => Math.abs(x - gx) + Math.abs(y - gy);
    const heap = new MinHeap();
    const g = new Map([[key(sx, sy), 0]]);
    const came = new Map();
    const closed = new Set();
    heap.push([h(sx, sy), 0, sx, sy]);
    while (heap.size) {
      const [, cost, x, y] = heap.pop();
      const k = key(x, y);
      if (closed.has(k)) continue;
      closed.add(k);
      if (x === gx && y === gy) {
        const out = [[x, y]];
        let c = k;
        while (came.has(c)) {
          c = came.get(c);
          out.unshift([(c % W) + this.x0, Math.floor(c / W)]);
        }
        return out;
      }
      for (const [dx, dy] of DIRS) {
        const nx = x + dx;
        const ny = y + dy;
        if (!this.walkable(nx, ny)) continue;
        const nk = key(nx, ny);
        const ng = cost + 1;
        if (ng < (g.get(nk) ?? Infinity)) {
          g.set(nk, ng);
          came.set(nk, k);
          heap.push([ng + h(nx, ny), ng, nx, ny]);
        }
      }
    }
    return null;
  }
}

const DIRS = [[0, -1], [1, 0], [0, 1], [-1, 0]];

// ties broken towards the larger g, so paths run straight instead of zigzagging
const less = (a, b) => a[0] < b[0] || (a[0] === b[0] && a[1] > b[1]);

class MinHeap {
  constructor() {
    this.a = [];
  }

  get size() {
    return this.a.length;
  }

  push(v) {
    const a = this.a;
    a.push(v);
    let i = a.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (!less(a[i], a[p])) break;
      [a[i], a[p]] = [a[p], a[i]];
      i = p;
    }
  }

  pop() {
    const a = this.a;
    const top = a[0];
    const last = a.pop();
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < a.length && less(a[l], a[m])) m = l;
        if (r < a.length && less(a[r], a[m])) m = r;
        if (m === i) break;
        [a[i], a[m]] = [a[m], a[i]];
        i = m;
      }
    }
    return top;
  }
}

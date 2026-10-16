// Pure walkability + pathfinding for one office, built straight from the Tiled JSON.
// No Phaser here: the renderer, the character controller and the Node tests share it.
//
// Rules: a wall tile blocks if its tileset tile has property collides=true; a prop blocks
// its footprint unless its object has collides=false. Zones are points; each names the tile
// the character stands on, plus optional `face` (up/down/left/right) and `seat`
// (direction of a one-tile hop onto furniture, e.g. sitting on the couch).

const prop = (o, name, fallback) => {
  const p = (o.properties || []).find((x) => x.name === name);
  return p ? p.value : fallback;
};

export function buildGrid(map) {
  const { width, height, tilewidth: tw, tileheight: th } = map;
  const blocked = Array.from({ length: height }, () => new Array(width).fill(false));

  const collidingGids = new Set();
  for (const ts of map.tilesets || []) {
    for (const t of ts.tiles || []) {
      if (prop(t, 'collides', false)) collidingGids.add(ts.firstgid + t.id);
    }
  }
  const layer = (name) => (map.layers || []).find((l) => l.name === name);

  const walls = layer('walls');
  if (walls) {
    walls.data.forEach((gid, i) => {
      if (collidingGids.has(gid)) blocked[Math.floor(i / width)][i % width] = true;
    });
  }

  const props = [];
  for (const o of (layer('props') || {}).objects || []) {
    const p = {
      name: o.name,
      tx: Math.floor(o.x / tw), ty: Math.floor(o.y / th),
      tw: Math.round(o.width / tw), th: Math.round(o.height / th),
      collides: prop(o, 'collides', true),
    };
    props.push(p);
    if (!p.collides) continue;
    for (let y = p.ty; y < p.ty + p.th; y++) {
      for (let x = p.tx; x < p.tx + p.tw; x++) {
        if (y >= 0 && y < height && x >= 0 && x < width) blocked[y][x] = true;
      }
    }
  }

  const zones = {};
  for (const o of (layer('zones') || {}).objects || []) {
    zones[o.name] = {
      tx: Math.floor(o.x / tw), ty: Math.floor(o.y / th),
      face: prop(o, 'face', 'down'),
      seat: prop(o, 'seat', null),
    };
  }

  return { width, height, blocked, props, zones };
}

export const walkable = (grid, x, y) =>
  x >= 0 && y >= 0 && x < grid.width && y < grid.height && !grid.blocked[y][x];

const DIRS = [[0, -1], [1, 0], [0, 1], [-1, 0]];

// A* with 4-way moves and a Manhattan heuristic. Returns [[x,y], ...] from start to goal
// inclusive, or null if unreachable. Grids are tiny (16x12), so a sorted open list is fine.
export function findPath(grid, [sx, sy], [gx, gy]) {
  if (!walkable(grid, gx, gy)) return null;
  const key = (x, y) => y * grid.width + x;
  const h = (x, y) => Math.abs(x - gx) + Math.abs(y - gy);
  const came = new Map();
  const g = new Map([[key(sx, sy), 0]]);
  const open = [[h(sx, sy), 0, sx, sy]];
  const closed = new Set();
  while (open.length) {
    open.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
    const [, cost, x, y] = open.shift();
    const k = key(x, y);
    if (closed.has(k)) continue;
    closed.add(k);
    if (x === gx && y === gy) {
      const path = [[x, y]];
      let c = k;
      while (came.has(c)) {
        c = came.get(c);
        path.unshift([c % grid.width, Math.floor(c / grid.width)]);
      }
      return path;
    }
    for (const [dx, dy] of DIRS) {
      const nx = x + dx;
      const ny = y + dy;
      if (!walkable(grid, nx, ny)) continue;
      const nk = key(nx, ny);
      const ng = cost + 1;
      if (ng < (g.get(nk) ?? Infinity)) {
        g.set(nk, ng);
        came.set(nk, k);
        open.push([ng + h(nx, ny), ng, nx, ny]);
      }
    }
  }
  return null;
}

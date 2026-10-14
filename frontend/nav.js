/**
 * nav.js — navigation + room layer for the Star Office scene.
 *
 * Framework-agnostic on purpose: no Phaser, no DOM. Runs in node (for tests)
 * and in the browser (for the renderer). The renderer reads the layout; it does
 * not hardcode coordinates (SOU-88 AC5: layout is data, not code).
 *
 * Why this exists: the lodge art is a FRONT-FACING illustration, not a top-down
 * map. There is no "walkable floor" in the image the way a tile map would have.
 * So we model the floor as a horizontal BAND and treat y within that band as
 * depth (larger y = nearer the viewer). Furniture that reaches the floor casts a
 * FOOTPRINT into the band; agents path around footprints instead of walking
 * through a fireplace.
 */

'use strict';

const DEFAULT_LAYOUT = {
  id: 'lodge',
  scene: { width: 1216, height: 832 },

  // Grid cell size in scene pixels. Movement is snapped to this grid, which is
  // also the natural size for a pixel-art walk step.
  cell: 16,

  /**
   * The walkable floor band. `top` is where the floor plane becomes usable
   * (roughly the furniture base line); `bottom` is the front edge of the room.
   * Measured from the rendered scene, not guessed.
   */
  floor: { top: 612, bottom: 820 },

  /**
   * FLOOR-CONTACT footprints, not visual bounding boxes. A backrest or a
   * lampshade rises into the air but does not block walking; only the part of
   * an object that meets the floor plane does. Modelling visual bboxes as
   * footprints walled off the entire bottom-right of the room.
   * Boxes are {x1,y1,x2,y2} in scene pixels, measured off the render.
   * `pad` grows the box so an agent's body cannot clip the object's edge.
   */
  footprints: [
    { id: 'bookcase',  x1: 0,   y1: 745, x2: 215,  y2: 808, pad: 4 },
    { id: 'desk',      x1: 225, y1: 662, x2: 528,  y2: 712, pad: 4 },
    { id: 'fireplace', x1: 522, y1: 600, x2: 832,  y2: 672, pad: 6 },
    { id: 'firewood',  x1: 892, y1: 618, x2: 1018, y2: 664, pad: 4 },
    { id: 'armchair',  x1: 772, y1: 712, x2: 1002, y2: 812, pad: 4 },
    { id: 'ottoman',   x1: 1004, y1: 752, x2: 1122, y2: 814, pad: 4 },
  ],

  /**
   * Named anchors: where an agent STANDS for a given activity. Anchors are
   * authored as intent ("this is the desk working spot"), and resolved onto the
   * nearest walkable cell at load so they can never land inside furniture.
   */
  anchors: {
    desk:      { x: 460, y: 706, facing: 'up'    },
    hearth:    { x: 690, y: 700, facing: 'up'    },
    rug:       { x: 640, y: 745, facing: 'down'  },
    armchair:  { x: 730, y: 800, facing: 'right' },
    firewood:  { x: 880, y: 700, facing: 'up'    },
    window:    { x: 1050, y: 760, facing: 'right' },
    door:      { x: 300,  y: 790, facing: 'down' },
  },

  /**
   * Rooms. The building is a set of rooms; every registered agent is allocated
   * its own room on join (SOU-88 AC1). Rooms are DATA - adding one is a config
   * change, not a renderer change.
   *
   * NOTE: only the 'lodge' room has art today. The art pack that gives each
   * agent a visually distinct room is blocked (licence-clean generation route).
   * The allocation logic and the movement into/out of rooms are real and tested
   * now, so the art can drop in without touching the logic.
   */
  rooms: [
    {
      id: 'lodge',
      name: 'The Lodge',
      kind: 'shared',          // shared facility room - not allocatable
      bounds: { x1: 0, y1: 0, x2: 1216, y2: 832 },
      door: { x: 300, y: 790 },
    },
  ],

  // Where a newly-allocated room would be carved out, in order. Each entry is a
  // station anchor the agent owns inside the building.
  stationSlots: [
    { id: 'station-1', x: 360,  y: 745 },
    { id: 'station-2', x: 900,  y: 745 },
    { id: 'station-3', x: 170,  y: 760 },
    { id: 'station-4', x: 1080, y: 700 },
    { id: 'station-5', x: 560,  y: 790 },
    { id: 'station-6', x: 1180, y: 800 },
  ],

  /**
   * Activity -> destination. `null` means "stay at your own station".
   * This is the state machine that makes movement LOGICAL: an agent executing a
   * build walks to the desk; an idle agent goes to the hearth or its station.
   */
  activityDestinations: {
    idle:        null,        // own station
    writing:     'desk',
    coding:      'desk',
    researching: 'desk',
    executing:   'hearth',
    syncing:     'window',
    blocked:     'hearth',
    error:       'hearth',
    waiting:     'rug',
  },
};

// ---------------------------------------------------------------------------
// grid
// ---------------------------------------------------------------------------

function inBox(x, y, b, pad = 0) {
  return x >= b.x1 - pad && x <= b.x2 + pad && y >= b.y1 - pad && y <= b.y2 + pad;
}

/**
 * Build the walkable grid from a layout. Returns a grid object the path finder
 * and the renderer both use, so they can never disagree about what is walkable.
 */
function buildGrid(layout) {
  const cell = layout.cell;
  const cols = Math.ceil(layout.scene.width / cell);
  const rows = Math.ceil(layout.scene.height / cell);
  const { top, bottom } = layout.floor;

  const grid = {
    cell, cols, rows,
    walkable: new Uint8Array(cols * rows),
    blockedBy: new Array(cols * rows).fill(null),
  };

  for (let cy = 0; cy < rows; cy++) {
    for (let cx = 0; cx < cols; cx++) {
      const x = cx * cell + cell / 2;   // cell centre
      const y = cy * cell + cell / 2;
      const i = cy * cols + cx;

      // outside the floor band -> not walkable
      if (y < top || y > bottom) continue;

      // inside a furniture footprint -> not walkable, and record why
      let hit = null;
      for (const f of layout.footprints) {
        if (inBox(x, y, f, f.pad || 0)) { hit = f.id; break; }
      }
      if (hit) { grid.blockedBy[i] = hit; continue; }

      grid.walkable[i] = 1;
    }
  }
  return grid;
}

const cellOf = (grid, x, y) => {
  const cx = Math.floor(x / grid.cell), cy = Math.floor(y / grid.cell);
  if (cx < 0 || cy < 0 || cx >= grid.cols || cy >= grid.rows) return null;
  return { cx, cy, i: cy * grid.cols + cx };
};
const centreOf = (grid, cx, cy) => ({
  x: cx * grid.cell + grid.cell / 2,
  y: cy * grid.cell + grid.cell / 2,
});

/**
 * Snap a point to the nearest walkable cell. Anchors and agent positions are
 * authored freely and resolved through this, so nothing ever stands in a
 * fireplace. Searches outward in rings; returns null if the whole grid is
 * blocked (which a test asserts cannot happen).
 */
function nearestWalkable(grid, x, y, maxRing = 40) {
  const c = cellOf(grid, x, y);
  if (!c) return null;
  if (grid.walkable[c.i]) return centreOf(grid, c.cx, c.cy);

  for (let r = 1; r <= maxRing; r++) {
    let best = null, bestD = Infinity;
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;  // ring only
        const nx = c.cx + dx, ny = c.cy + dy;
        if (nx < 0 || ny < 0 || nx >= grid.cols || ny >= grid.rows) continue;
        if (!grid.walkable[ny * grid.cols + nx]) continue;
        const p = centreOf(grid, nx, ny);
        const d = (p.x - x) ** 2 + (p.y - y) ** 2;
        if (d < bestD) { bestD = d; best = p; }
      }
    }
    if (best) return best;
  }
  return null;
}

// ---------------------------------------------------------------------------
// path finding — A* with 8-way movement and a diagonal-corner rule
// ---------------------------------------------------------------------------

const DIRS = [
  [1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1],
  [1, 1, Math.SQRT2], [1, -1, Math.SQRT2], [-1, 1, Math.SQRT2], [-1, -1, Math.SQRT2],
];

function octile(ax, ay, bx, by) {
  const dx = Math.abs(ax - bx), dy = Math.abs(ay - by);
  return (dx + dy) + (Math.SQRT2 - 2) * Math.min(dx, dy);
}

/**
 * A* between two scene points. Returns an array of {x,y} waypoints starting at
 * the resolved start and ending at the resolved goal, or null if unreachable.
 *
 * Diagonals are only allowed when BOTH orthogonal neighbours are walkable, so a
 * path can never squeeze through a diagonal gap between two furniture corners.
 */
function findPath(grid, sx, sy, gx, gy) {
  const start = nearestWalkable(grid, sx, sy);
  const goal = nearestWalkable(grid, gx, gy);
  if (!start || !goal) return null;

  const s = cellOf(grid, start.x, start.y);
  const g = cellOf(grid, goal.x, goal.y);
  if (s.i === g.i) return [{ x: start.x, y: start.y }];

  const open = new Map();       // i -> {i, cx, cy, g, f, parent}
  const closed = new Set();
  const push = (n) => open.set(n.i, n);
  const h = (cx, cy) => octile(cx, cy, g.cx, g.cy);

  push({ i: s.i, cx: s.cx, cy: s.cy, g: 0, f: h(s.cx, s.cy), parent: null });

  while (open.size) {
    // pick lowest f
    let cur = null;
    for (const n of open.values()) if (!cur || n.f < cur.f) cur = n;
    open.delete(cur.i);
    closed.add(cur.i);

    if (cur.i === g.i) {
      const out = [];
      for (let n = cur; n; n = n.parent) out.push({ x: centreOf(grid, n.cx, n.cy).x, y: centreOf(grid, n.cx, n.cy).y });
      return out.reverse();
    }

    for (const [dx, dy, cost] of DIRS) {
      const nx = cur.cx + dx, ny = cur.cy + dy;
      if (nx < 0 || ny < 0 || nx >= grid.cols || ny >= grid.rows) continue;
      const ni = ny * grid.cols + nx;
      if (!grid.walkable[ni] || closed.has(ni)) continue;

      // no cutting furniture corners on a diagonal
      if (dx !== 0 && dy !== 0) {
        if (!grid.walkable[cur.cy * grid.cols + nx]) continue;
        if (!grid.walkable[ny * grid.cols + cur.cx]) continue;
      }

      const ng = cur.g + cost;
      const prev = open.get(ni);
      if (prev && ng >= prev.g) continue;
      push({ i: ni, cx: nx, cy: ny, g: ng, f: ng + h(nx, ny), parent: cur });
    }
  }
  return null;   // unreachable
}

/**
 * Turn a raw A* cell path into a walkable route by removing collinear points.
 * Fewer waypoints = smoother walk cycle, and it makes the route readable in a
 * rendered overlay.
 */
function simplify(path) {
  if (!path || path.length < 3) return path;
  const out = [path[0]];
  for (let i = 1; i < path.length - 1; i++) {
    const a = out[out.length - 1], b = path[i], c = path[i + 1];
    const cross = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    if (Math.abs(cross) > 1e-6) out.push(b);
  }
  out.push(path[path.length - 1]);
  return out;
}

// ---------------------------------------------------------------------------
// rooms + agent allocation
// ---------------------------------------------------------------------------

/**
 * Allocate a room/station to an agent. Stable: an agent that already has an
 * assignment keeps it across restarts and re-joins (SOU-88 AC1). Returns a NEW
 * assignment map; does not mutate the input.
 */
function allocateStations(layout, existing, agentIds) {
  const assigned = {};

  // keep a prior assignment only if it is still valid: the agent is still here
  // AND no other agent currently holds that same slot
  const holderOf = {};
  for (const [agentId, slotId] of Object.entries(existing || {})) {
    if (!agentIds.includes(agentId)) continue;
    if (holderOf[slotId] && holderOf[slotId] !== agentId) continue;   // conflict, first wins
    holderOf[slotId] = agentId;
  }
  for (const [slotId, agentId] of Object.entries(holderOf)) assigned[agentId] = slotId;

  // hand out the remaining slots, skipping every slot already in use
  const usedSlots = new Set(Object.values(assigned));
  const free = layout.stationSlots.map((s) => s.id).filter((id) => !usedSlots.has(id));
  for (const a of agentIds) {
    if (assigned[a]) continue;
    const slot = free.shift();
    if (!slot) break;                            // more agents than stations
    assigned[a] = slot;
    usedSlots.add(slot);
  }
  return assigned;
}

function stationAnchor(layout, slotId) {
  return layout.stationSlots.find((s) => s.id === slotId) || null;
}

/**
 * Resolve where an agent should be for its current activity:
 *   - an activity mapped to a shared anchor -> that anchor
 *   - an activity mapped to null             -> the agent's own station
 * Always resolved onto a walkable cell.
 */
function destinationFor(grid, layout, activity, slotId) {
  const key = layout.activityDestinations[activity];
  const target = key ? layout.anchors[key] : stationAnchor(layout, slotId);
  if (!target) return null;
  const p = nearestWalkable(grid, target.x, target.y);
  return p ? { x: p.x, y: p.y, anchor: key || slotId, facing: target.facing || 'down' } : null;
}

// Works as a node module (tests) AND as a plain browser script (renderer).
// In the browser this file is loaded before the renderer and its functions are
// globals, so the renderer never re-implements the layout.
const NAV = {
  DEFAULT_LAYOUT,
  buildGrid,
  nearestWalkable,
  findPath,
  simplify,
  allocateStations,
  stationAnchor,
  destinationFor,
  inBox,
  cellOf,
  centreOf,
};

if (typeof module !== 'undefined' && module.exports) module.exports = NAV;
if (typeof window !== 'undefined') window.NAV = NAV;

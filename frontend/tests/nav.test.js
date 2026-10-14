/**
 * nav.test.js — contract tests for the scene navigation + room layer.
 * Run: node --test frontend/tests/nav.test.js
 *
 * These assert the invariants the renderer depends on, so a layout edit that
 * makes an anchor unreachable or puts an agent inside a fireplace FAILS here
 * instead of silently shipping a sprite walking through a wall.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const nav = require('../nav.js');

const L = nav.DEFAULT_LAYOUT;
const grid = nav.buildGrid(L);

test('grid: some cells are walkable and some are blocked', () => {
  const walk = grid.walkable.reduce((a, b) => a + b, 0);
  assert.ok(walk > 100, `expected a usable floor, got ${walk} walkable cells`);
  assert.ok(walk < grid.walkable.length, 'expected furniture to block some cells');
});

test('grid: blocked cells record which footprint blocked them', () => {
  const reasons = new Set(grid.blockedBy.filter(Boolean));
  for (const id of ['bookcase', 'desk', 'fireplace', 'armchair']) {
    assert.ok(reasons.has(id), `expected ${id} to block at least one cell`);
  }
});

test('grid: nothing above the floor band is walkable (no walking on walls)', () => {
  const { top } = L.floor;
  for (let cy = 0; cy < grid.rows; cy++) {
    const y = cy * grid.cell + grid.cell / 2;
    if (y >= top) continue;
    for (let cx = 0; cx < grid.cols; cx++) {
      assert.strictEqual(
        grid.walkable[cy * grid.cols + cx], 0,
        `cell (${cx},${cy}) at y=${y} is above the floor but walkable`
      );
    }
  }
});

test('anchors: every anchor resolves onto a walkable cell', () => {
  for (const [name, a] of Object.entries(L.anchors)) {
    const p = nav.nearestWalkable(grid, a.x, a.y);
    assert.ok(p, `anchor '${name}' could not be resolved`);
    const c = nav.cellOf(grid, p.x, p.y);
    assert.strictEqual(grid.walkable[c.i], 1, `anchor '${name}' resolved to a blocked cell`);
  }
});

test('anchors: resolution never moves an anchor more than ~2 cells', () => {
  // If it does, the anchor is authored inside furniture and should be fixed in
  // the layout rather than silently teleported by the resolver.
  for (const [name, a] of Object.entries(L.anchors)) {
    const p = nav.nearestWalkable(grid, a.x, a.y);
    const moved = Math.hypot(p.x - a.x, p.y - a.y);
    assert.ok(moved <= L.cell * 2, `anchor '${name}' was nudged ${moved.toFixed(1)}px`);
  }
});

test('stations: every station slot is walkable', () => {
  for (const s of L.stationSlots) {
    const p = nav.nearestWalkable(grid, s.x, s.y);
    const c = nav.cellOf(grid, p.x, p.y);
    assert.strictEqual(grid.walkable[c.i], 1, `station '${s.id}' is not walkable`);
  }
});

test('floor is CONNECTED: every anchor can reach every other anchor', () => {
  // The important one. A room split in two by a footprint pair would strand
  // agents, and it would look like a bug in the walk cycle rather than the map.
  const names = Object.keys(L.anchors);
  const base = L.anchors[names[0]];
  for (const name of names.slice(1)) {
    const t = L.anchors[name];
    const path = nav.findPath(grid, base.x, base.y, t.x, t.y);
    assert.ok(path, `no path from '${names[0]}' to '${name}' — floor is split`);
  }
});

test('stations are CONNECTED to the shared anchors', () => {
  for (const s of L.stationSlots) {
    const path = nav.findPath(grid, s.x, s.y, L.anchors.desk.x, L.anchors.desk.y);
    assert.ok(path, `station '${s.id}' cannot reach the desk`);
  }
});

test('path: starts at the agent, ends at the destination', () => {
  const path = nav.findPath(grid, L.stationSlots[0].x, L.stationSlots[0].y, L.anchors.desk.x, L.anchors.desk.y);
  assert.ok(path && path.length >= 2);
  const start = nav.nearestWalkable(grid, L.stationSlots[0].x, L.stationSlots[0].y);
  const end = nav.nearestWalkable(grid, L.anchors.desk.x, L.anchors.desk.y);
  assert.strictEqual(path[0].x, start.x);
  assert.strictEqual(path[path.length - 1].x, end.x);
});

test('path: NO waypoint is ever inside a furniture footprint', () => {
  const paths = [];
  for (const a of Object.values(L.anchors)) {
    for (const b of Object.values(L.anchors)) {
      const p = nav.findPath(grid, a.x, a.y, b.x, b.y);
      if (p) paths.push({ a, b, p });
    }
  }
  assert.ok(paths.length > 0);
  for (const { a, b, p } of paths) {
    for (const wp of p) {
      for (const f of L.footprints) {
        const inside = wp.x >= f.x1 && wp.x <= f.x2 && wp.y >= f.y1 && wp.y <= f.y2;
        assert.ok(!inside, `path ${JSON.stringify(a)}->${JSON.stringify(b)} walks through '${f.id}' at (${wp.x},${wp.y})`);
      }
    }
  }
});

test('path: consecutive waypoints never cut a diagonal through a corner', () => {
  const p = nav.findPath(grid, L.anchors.desk.x, L.anchors.desk.y, L.anchors.armchair.x, L.anchors.armchair.y);
  assert.ok(p);
  for (let i = 1; i < p.length; i++) {
    const dx = Math.abs(p[i].x - p[i - 1].x), dy = Math.abs(p[i].y - p[i - 1].y);
    if (dx > 0 && dy > 0) {
      // both orthogonal neighbours must be walkable
      const A = nav.cellOf(grid, p[i].x, p[i - 1].y);
      const B = nav.cellOf(grid, p[i - 1].x, p[i].y);
      assert.ok(grid.walkable[A.i] && grid.walkable[B.i], 'diagonal cut through a corner');
    }
  }
});

test('path: a point inside furniture resolves out rather than failing', () => {
  const insideFireplace = { x: 700, y: 400 };
  const p = nav.nearestWalkable(grid, insideFireplace.x, insideFireplace.y);
  assert.ok(p, 'should resolve to the nearest legal standing spot');
  const f = L.footprints.find((f) => f.id === 'fireplace');
  const stillInside = p.x >= f.x1 && p.x <= f.x2 && p.y >= f.y1 && p.y <= f.y2;
  assert.ok(!stillInside, 'resolved point is still inside the fireplace');
});

test('rooms: station allocation is stable across a restart / re-join', () => {
  const agents = ['hermes-szarik', 'openchamber', 'opencode', 'florian'];
  const first = nav.allocateStations(L, {}, agents);
  assert.strictEqual(Object.keys(first).length, 4);
  assert.strictEqual(new Set(Object.values(first)).size, 4, 'stations must be distinct');

  // same agents re-join with the same prior assignment -> identical result
  const second = nav.allocateStations(L, first, agents);
  assert.deepStrictEqual(second, first, 'allocation must be stable across restarts');
});

test('rooms: a new agent does not steal an existing agent station', () => {
  const agents = ['a', 'b'];
  const first = nav.allocateStations(L, {}, agents);
  const withNew = nav.allocateStations(L, first, ['a', 'b', 'c']);
  assert.strictEqual(withNew.a, first.a);
  assert.strictEqual(withNew.b, first.b);
  assert.ok(withNew.c && ![first.a, first.b].includes(withNew.c), 'new agent got an occupied station');
});

test('rooms: no overlap when N agents share the same activity', () => {
  // The core multi-agent defect this whole layer exists to fix: two agents in
  // the same STATE must not land on the same tile.
  const agents = ['a', 'b', 'c', 'd', 'e', 'f'];
  const assign = nav.allocateStations(L, {}, agents);
  const spots = agents.map((a) => {
    const d = nav.destinationFor(grid, L, 'idle', assign[a]);   // idle -> own station
    return d ? `${d.x},${d.y}` : null;
  });
  assert.ok(spots.every(Boolean), 'every idle agent must have a station destination');
  assert.strictEqual(new Set(spots).size, spots.length, `agents overlap: ${spots.join(' | ')}`);
});

test('rooms: activity maps to the intended facility', () => {
  const assign = nav.allocateStations(L, {}, ['a']);
  const exec = nav.destinationFor(grid, L, 'executing', assign.a);
  const desk = nav.destinationFor(grid, L, 'writing', assign.a);
  assert.strictEqual(exec.anchor, 'hearth');
  assert.strictEqual(desk.anchor, 'desk');
  assert.notStrictEqual(`${exec.x},${exec.y}`, `${desk.x},${desk.y}`);
});

test('rooms: an unknown activity falls back to the agent station, not null', () => {
  const assign = nav.allocateStations(L, {}, ['a']);
  const d = nav.destinationFor(grid, L, 'some-new-state', assign.a);
  assert.ok(d, 'unknown activity must not strand the agent');
  assert.ok(d.x > 0 && d.y > 0);
});

test('path: NO SEGMENT between waypoints crosses a footprint', () => {
  // Waypoints being outside is NOT the same claim as the path being outside:
  // two legal waypoints can have a straight line between them that clips a
  // furniture corner. Sample each segment densely and test the boxes.
  const hits = [];
  for (const a of Object.values(L.anchors)) {
    for (const b of Object.values(L.anchors)) {
      const path = nav.findPath(grid, a.x, a.y, b.x, b.y);
      if (!path) continue;
      for (let i = 1; i < path.length; i++) {
        const p = path[i - 1], q = path[i];
        const len = Math.hypot(q.x - p.x, q.y - p.y);
        const n = Math.max(2, Math.ceil(len / 2));
        for (let k = 0; k <= n; k++) {
          const t = k / n;
          const x = p.x + (q.x - p.x) * t, y = p.y + (q.y - p.y) * t;
          for (const f of L.footprints) {
            if (x >= f.x1 && x <= f.x2 && y >= f.y1 && y <= f.y2) {
              hits.push(`${JSON.stringify(a)}->${JSON.stringify(b)} enters '${f.id}' at (${x.toFixed(0)},${y.toFixed(0)})`);
            }
          }
        }
      }
    }
  }
  assert.deepStrictEqual(hits, [], `routes cross furniture:\n${hits.join('\n')}`);
});

// ---------------------------------------------------------------------------
// multi-room
// ---------------------------------------------------------------------------

const built = nav.buildBuilding(nav.BUILDING);

test('building: both rooms build a usable grid', () => {
  assert.ok(Object.keys(built.rooms).length >= 2, 'expected at least two rooms');
  for (const [id, r] of Object.entries(built.rooms)) {
    const walk = r.grid.walkable.reduce((a, b) => a + b, 0);
    assert.ok(walk > 100, `room '${id}' has almost no walkable floor (${walk} cells)`);
  }
});

test('building: every doorway resolves onto walkable floor in BOTH rooms', () => {
  for (const l of nav.BUILDING.links) {
    const a = nav.doorPoint(built, l.a.room, l.a.door);
    const b = nav.doorPoint(built, l.b.room, l.b.door);
    assert.ok(a, `door '${l.a.door}' in room '${l.a.room}' is not walkable`);
    assert.ok(b, `door '${l.b.door}' in room '${l.b.room}' is not walkable`);
  }
});

test('building: second room anchors are walkable and connected', () => {
  for (const [name, a] of Object.entries(nav.SERVER_ROOM.anchors)) {
    const p = nav.nearestWalkable(built.rooms.serverroom.grid, a.x, a.y);
    assert.ok(p, `server room anchor '${name}' unresolved`);
    for (const [n2, b] of Object.entries(nav.SERVER_ROOM.anchors)) {
      if (n2 === name) continue;
      const path = nav.findPath(built.rooms.serverroom.grid, a.x, a.y, b.x, b.y);
      assert.ok(path, `server room anchor '${name}' cannot reach '${n2}'`);
    }
  }
});

test('linkChain: finds the hop between two rooms and is symmetric', () => {
  const ab = nav.linkChain(built, 'lodge', 'serverroom');
  const ba = nav.linkChain(built, 'serverroom', 'lodge');
  assert.strictEqual(ab.length, 1);
  assert.strictEqual(ba.length, 1);
  assert.strictEqual(ab[0].to, 'serverroom');
  assert.strictEqual(ba[0].to, 'lodge');
});

test('linkChain: same room is zero hops, unknown room is null', () => {
  assert.deepStrictEqual(nav.linkChain(built, 'lodge', 'lodge'), []);
  assert.strictEqual(nav.linkChain(built, 'lodge', 'nowhere'), null);
});

test('CROSS-ROOM route walks out of one room, through the door, into the other', () => {
  // The whole point of the multi-room layer: an agent ordered from the lodge to
  // the server room must WALK there, not appear there.
  const from = nav.stationAnchor(nav.DEFAULT_LAYOUT, 'station-1');
  const to = nav.SERVER_ROOM.anchors.rack;

  const legs = nav.findRoute(built, 'lodge', from, 'serverroom', to);
  assert.ok(legs, 'cross-room route should exist');

  const roomLegs = legs.filter((l) => l.path);
  const doorLegs = legs.filter((l) => l.door);
  assert.strictEqual(roomLegs.length, 2, 'expected one walking leg per room');
  assert.strictEqual(doorLegs.length, 1, 'expected exactly one doorway crossing');
  assert.strictEqual(roomLegs[0].room, 'lodge');
  assert.strictEqual(roomLegs[1].room, 'serverroom');

  // the first leg must END at the lodge side of the door, and the second must
  // START at the server-room side - otherwise the crossing is a teleport
  const doorA = legs.find((l) => l.door).door.from;
  const doorB = legs.find((l) => l.door).door.to;
  const legOneEnd = roomLegs[0].path[roomLegs[0].path.length - 1];
  const legTwoStart = roomLegs[1].path[0];
  assert.strictEqual(legOneEnd.x, doorA.x);
  assert.strictEqual(legOneEnd.y, doorA.y);
  assert.strictEqual(legTwoStart.x, doorB.x);
  assert.strictEqual(legTwoStart.y, doorB.y);
});

test('CROSS-ROOM route: no leg enters a footprint in its own room', () => {
  const legs = nav.findRoute(built, 'lodge', nav.DEFAULT_LAYOUT.anchors.door,
                                    'serverroom', nav.SERVER_ROOM.anchors.workbench);
  assert.ok(legs);
  for (const leg of legs) {
    if (!leg.path) continue;
    const fps = built.rooms[leg.room].layout.footprints;
    for (let i = 1; i < leg.path.length; i++) {
      const p = leg.path[i - 1], q = leg.path[i];
      const len = Math.hypot(q.x - p.x, q.y - p.y);
      const n = Math.max(2, Math.ceil(len / 2));
      for (let k = 0; k <= n; k++) {
        const t = k / n;
        const x = p.x + (q.x - p.x) * t, y = p.y + (q.y - p.y) * t;
        for (const f of fps) {
          const inside = x >= f.x1 && x <= f.x2 && y >= f.y1 && y <= f.y2;
          assert.ok(!inside, `leg in '${leg.room}' enters '${f.id}' at (${x.toFixed(0)},${y.toFixed(0)})`);
        }
      }
    }
  }
});

test('route: same-room request returns a single leg, no doorway', () => {
  const a = nav.DEFAULT_LAYOUT.anchors.desk, b = nav.DEFAULT_LAYOUT.anchors.rug;
  const legs = nav.findRoute(built, 'lodge', a, 'lodge', b);
  assert.strictEqual(legs.length, 1);
  assert.strictEqual(legs[0].room, 'lodge');
  assert.ok(legs[0].path.length >= 2);
});

test('route: all six lodge anchors reach the server room', () => {
  const to = nav.SERVER_ROOM.anchors.rack;
  for (const [name, a] of Object.entries(nav.DEFAULT_LAYOUT.anchors)) {
    const legs = nav.findRoute(built, 'lodge', a, 'serverroom', to);
    assert.ok(legs, `anchor '${name}' cannot reach the server room`);
  }
});

test('simplify: fewer or equal waypoints, same endpoints', () => {
  const raw = nav.findPath(grid, L.anchors.door.x, L.anchors.door.y, L.anchors.armchair.x, L.anchors.armchair.y);
  const s = nav.simplify(raw);
  assert.ok(s.length <= raw.length);
  assert.deepStrictEqual(s[0], raw[0]);
  assert.deepStrictEqual(s[s.length - 1], raw[raw.length - 1]);
});

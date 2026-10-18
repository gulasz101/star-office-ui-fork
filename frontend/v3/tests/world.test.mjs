// SOU-195: the Lodge world. Every room template is complete and reachable, slots grow
// around the centre, and an arriving agent can walk from the bottom of the screen into
// any room through the entrance and the corridor.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildGrid, findPath } from '../src/grid.js';
import { World, slotForIndex, ROOM_W, ROOM_H, FRONT_Y, SPAWN_Y, DOOR_COLS } from '../src/world.js';
import { ROOM_TYPES } from '../src/rooms.js';

const ZONES = ['work', 'ops', 'build', 'read', 'wait', 'rest', 'entry'];
const load = (type) => JSON.parse(readFileSync(new URL(`../maps/rooms/${type}.json`, import.meta.url)));
const grids = Object.fromEntries(ROOM_TYPES.map((t) => [t, buildGrid(load(t))]));

for (const type of ROOM_TYPES) {
  test(`room ${type}: 16x12, all 7 zones, each walkable and reachable from the door`, () => {
    const g = grids[type];
    assert.equal(g.width, ROOM_W);
    assert.equal(g.height, ROOM_H);
    assert.deepEqual(Object.keys(g.zones).sort(), [...ZONES].sort());
    for (const c of DOOR_COLS) assert.equal(g.blocked[ROOM_H - 1][c], false, `door column ${c} open`);
    for (const name of ZONES) {
      const z = g.zones[name];
      assert.equal(g.blocked[z.ty][z.tx], false, `${type}.${name} stands on a free tile`);
      assert.ok(findPath(g, [DOOR_COLS[0], ROOM_H - 1], [z.tx, z.ty]), `${type}.${name} reachable from the door`);
      if (z.seat) {
        const d = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[z.seat];
        assert.equal(g.blocked[z.ty + d[1]][z.tx + d[0]], true, `${type}.${name} seat hops onto furniture`);
      }
    }
  });
}

test('slots grow around the centre: 0, +1, -1, +2, -2', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5].map(slotForIndex), [0, 1, -1, 2, -2, 3]);
});

// room types per agent: tests/rooms.test.mjs (SOU-294, from the API)

function lodge(n) {
  const w = new World();
  const types = ROOM_TYPES.slice(0, n);
  types.forEach((t, i) => w.addRoom(slotForIndex(i), grids[t]));
  return { w, types };
}

test('an arriving agent walks from the bottom edge into every zone of every room', () => {
  const { w, types } = lodge(ROOM_TYPES.length);
  types.forEach((type, i) => {
    const slot = slotForIndex(i);
    for (const [name, z] of Object.entries(w.zonesOf(slot))) {
      const p = w.path([w.spawn.tx, w.spawn.ty], [z.tx, z.ty]);
      assert.ok(p, `${type} (slot ${slot}).${name} reachable from the spawn`);
      assert.deepEqual(p[0], [w.spawn.tx, SPAWN_Y]);
      assert.ok(p.some(([x, y]) => y === FRONT_Y && DOOR_COLS.includes(x)), 'goes through the entrance');
    }
  });
});

test('paths stay on walkable tiles, move one tile at a time, and never cut through a room wall', () => {
  const { w } = lodge(3);
  const z = w.zonesOf(-1).work;
  const p = w.path([w.spawn.tx, w.spawn.ty], [z.tx, z.ty]);
  for (let i = 0; i < p.length; i++) {
    assert.ok(w.walkable(...p[i]));
    if (i) assert.equal(Math.abs(p[i][0] - p[i - 1][0]) + Math.abs(p[i][1] - p[i - 1][1]), 1);
  }
  // room-to-room: leave by the door, cross the corridor, enter by the other door
  const a = w.zonesOf(1).ops;
  const q = w.path([z.tx, z.ty], [a.tx, a.ty]);
  assert.ok(q.some(([, y]) => y >= ROOM_H), 'goes through the corridor');
});

test('outside the building only the snow path is walkable; no room means no floor', () => {
  const { w } = lodge(1);
  assert.equal(w.walkable(0, SPAWN_Y), false);
  assert.equal(w.walkable(6, SPAWN_Y), true);
  assert.equal(w.walkable(3, FRONT_Y), false);
  assert.equal(w.walkable(ROOM_W + 3, 5), false, 'slot 1 has no room yet');
  assert.equal(w.walkable(w.x0, ROOM_H + 1), false, 'corridor end wall');
});

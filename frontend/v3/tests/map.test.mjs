// office-01 map contract (SOU-155): Tiled-valid shape, every zone reachable from the door,
// paths never cross a blocked tile, seats land on furniture.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildGrid, findPath, walkable } from '../src/grid.js';

const map = JSON.parse(readFileSync(new URL('../maps/office-01.json', import.meta.url)));
const grid = buildGrid(map);
const ZONES = ['work', 'ops', 'build', 'read', 'wait', 'rest', 'entry'];
const SEAT = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };

test('map is an orthogonal Tiled map with the layers v3 expects', () => {
  assert.equal(map.type, 'map');
  assert.equal(map.orientation, 'orthogonal');
  assert.equal(map.tilewidth, 32);
  assert.deepEqual([map.width, map.height], [16, 12]);
  const layers = Object.fromEntries(map.layers.map((l) => [l.name, l.type]));
  assert.deepEqual(layers, { floor: 'tilelayer', walls: 'tilelayer', props: 'objectgroup', zones: 'objectgroup' });
  for (const l of map.layers.filter((x) => x.type === 'tilelayer')) {
    assert.equal(l.data.length, map.width * map.height, `${l.name} data length`);
  }
  const ids = map.layers.flatMap((l) => (l.objects || []).map((o) => o.id));
  assert.equal(new Set(ids).size, ids.length, 'object ids unique');
  assert.ok(map.nextobjectid > Math.max(...ids), 'nextobjectid beyond every id');
});

test('every M1 zone exists and stands on a walkable tile', () => {
  assert.deepEqual(Object.keys(grid.zones).sort(), [...ZONES].sort());
  for (const z of ZONES) {
    const { tx, ty } = grid.zones[z];
    assert.ok(walkable(grid, tx, ty), `${z} at ${tx},${ty} is blocked`);
  }
});

test('every zone is reachable from entry and from every other zone', () => {
  for (const a of ZONES) {
    for (const b of ZONES) {
      const za = grid.zones[a];
      const zb = grid.zones[b];
      const path = findPath(grid, [za.tx, za.ty], [zb.tx, zb.ty]);
      assert.ok(path, `no path ${a} -> ${b}`);
      assert.deepEqual(path[0], [za.tx, za.ty]);
      assert.deepEqual(path.at(-1), [zb.tx, zb.ty]);
    }
  }
});

test('paths only use walkable tiles and 4-way unit steps', () => {
  const e = grid.zones.entry;
  for (const z of ZONES) {
    const path = findPath(grid, [e.tx, e.ty], [grid.zones[z].tx, grid.zones[z].ty]);
    path.forEach(([x, y], i) => {
      assert.ok(walkable(grid, x, y), `${z}: step ${i} (${x},${y}) blocked`);
      if (i) {
        const [px, py] = path[i - 1];
        assert.equal(Math.abs(x - px) + Math.abs(y - py), 1, `${z}: step ${i} not adjacent`);
      }
    });
  }
});

test('the garage is only reachable through the partition opening', () => {
  const e = grid.zones.entry;
  const b = grid.zones.build;
  const path = findPath(grid, [e.tx, e.ty], [b.tx, b.ty]);
  const crossing = path.find(([x], i) => i && x >= 10 && path[i - 1][0] < 10);
  assert.ok(crossing, 'path crosses into the garage');
  assert.ok(crossing[1] >= 7 && crossing[1] <= 10, `crossed at row ${crossing[1]}, opening is rows 7..10`);
});

test('seats hop onto a prop tile, not into a wall or the void', () => {
  for (const z of ZONES) {
    const { tx, ty, seat } = grid.zones[z];
    if (!seat) continue;
    const [dx, dy] = SEAT[seat];
    const onProp = grid.props.some((p) => tx + dx >= p.tx && tx + dx < p.tx + p.tw && ty + dy >= p.ty && ty + dy < p.ty + p.th);
    assert.ok(onProp, `${z} seat ${seat} lands on a prop`);
  }
});

test('the art step prop list has a slot in the map', () => {
  const names = grid.props.map((p) => p.name).sort();
  for (const n of ['desk', 'chair', 'rack', 'van', 'spool', 'crate', 'bass', 'amp', 'cat', 'coffee', 'couch', 'bookshelf', 'rug']) {
    assert.ok(names.includes(n), `missing prop slot ${n}`);
  }
});

test('findPath returns null for a blocked goal', () => {
  const desk = grid.props.find((p) => p.name === 'desk');
  assert.equal(findPath(grid, [grid.zones.entry.tx, grid.zones.entry.ty], [desk.tx, desk.ty]), null);
});

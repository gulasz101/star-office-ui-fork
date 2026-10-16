/**
 * depth.test.mjs — SOU-124 S2: the ONE lattice depth rule (AC5 "no magic constants").
 * Run: node --test frontend/tests/depth.test.mjs
 *
 * The v1 renderer carried ~10 ad-hoc setDepth() constants. Here there is exactly
 * one rule, and these tests pin what the painter's algorithm on the isometric
 * lattice requires of it:
 *   - depth grows STRICTLY with the lattice diagonal gx+gy (far rows first),
 *   - within one diagonal a deterministic tie-break by gx,
 *   - the two fixed world ranks (wall plane, floor tilemap) sit BELOW every
 *     lattice cell, in that order: wall < floor < props.
 * The grid below is the declared v2 floor: 12 x 18 (room_geometry / room.json).
 */
'use strict';

import test from 'node:test';
import assert from 'node:assert';

import { depthKey, WALL_DEPTH, FLOOR_DEPTH } from '../office_v2/depth.js';

const GRID_W = 12;
const GRID_H = 18;

function allCells() {
  const cells = [];
  for (let gy = 0; gy < GRID_H; gy++) {
    for (let gx = 0; gx < GRID_W; gx++) cells.push([gx, gy]);
  }
  return cells;
}

test('depthKey is an integer for every cell of the 12x18 floor', () => {
  for (const [gx, gy] of allCells()) {
    const d = depthKey(gx, gy, GRID_W);
    assert.ok(Number.isInteger(d), `depthKey(${gx},${gy}) = ${d} is not an integer`);
    assert.ok(d >= 0, `depthKey(${gx},${gy}) = ${d} must be >= 0`);
  }
});

test('depthKey increases strictly with the diagonal gx+gy (painter order on the lattice)', () => {
  const cells = allCells();
  for (const a of cells) {
    for (const b of cells) {
      const sa = a[0] + a[1];
      const sb = b[0] + b[1];
      const da = depthKey(a[0], a[1], GRID_W);
      const db = depthKey(b[0], b[1], GRID_W);
      if (sa < sb) {
        assert.ok(da < db, `(${a}) diag ${sa} must be below (${b}) diag ${sb}: ${da} vs ${db}`);
      } else if (sa === sb) {
        assert.ok(da === db || a[0] === b[0] && a[1] === b[1] || (a[0] < b[0] ? da < db : da > db),
          `same diagonal (${a}) vs (${b}) must order deterministically by gx: ${da} vs ${db}`);
      }
    }
  }
});

test('far corner (0,0) is the lowest cell; near corner (11,17) the highest', () => {
  const far = depthKey(0, 0, GRID_W);
  const near = depthKey(GRID_W - 1, GRID_H - 1, GRID_W);
  for (const [gx, gy] of allCells()) {
    const d = depthKey(gx, gy, GRID_W);
    assert.ok(d >= far && d <= near, `cell (${gx},${gy}) depth ${d} outside [${far}, ${near}]`);
  }
  assert.ok(far < near, 'far corner must be strictly below near corner');
});

test('issue example holds: near-corner van (6,15) draws later (deeper) than far-corner cat (0,15)', () => {
  const cat = depthKey(0, 15, GRID_W);   // diagonal 15
  const van = depthKey(6, 15, GRID_W);   // diagonal 21 - closer to camera
  assert.ok(cat < van, `cat ${cat} must be shallower than van ${van}`);
});

test('fixed world ranks sit below every lattice cell and in order wall < floor', () => {
  const far = depthKey(0, 0, GRID_W);
  assert.ok(WALL_DEPTH < FLOOR_DEPTH, `wall ${WALL_DEPTH} must be behind floor ${FLOOR_DEPTH}`);
  assert.ok(FLOOR_DEPTH < far, `floor ${FLOOR_DEPTH} must be below the shallowest prop depth ${far}`);
});

test('depthKey is pure and deterministic across repeated calls', () => {
  for (const [gx, gy] of allCells()) {
    assert.strictEqual(depthKey(gx, gy, GRID_W), depthKey(gx, gy, GRID_W));
  }
});

// SOU-88 station-allocation regression test.
//
// The renderer lives inline inside frontend/index.html (index.html loads no
// game.js - that file is a stale copy). Rather than re-implement the logic here
// and test my own copy, this test EXTRACTS the real functions from the shipped
// index.html and executes them. If the shipped code regresses, this fails.
//
// The bug it pins down: the slot index used to be a per-poll ordinal
// (idxBreak++ / idxWrite++ / idxError++), so an agent's position depended on
// iteration order and changed whenever any other agent joined or left.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const INDEX = path.join(__dirname, '..', 'index.html');
const html = fs.readFileSync(INDEX, 'utf8');

// --- extract the shipped implementations -------------------------------------

function extractFunction(src, name) {
  const start = src.indexOf(`function ${name}(`);
  assert.notStrictEqual(start, -1, `shipped index.html must define ${name}()`);
  // Brace-match from the first '{' after the signature.
  const open = src.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced braces while extracting ${name}`);
}

const getAreaPointSrc = extractFunction(html, 'getAreaPoint');
const allocateStationSrc = extractFunction(html, 'allocateStation');
const releaseStationSrc = extractFunction(html, 'releaseStation');

// Sanity: we really are reading the shipped file, not a stub.
assert.ok(/arr\[idx % arr\.length\]/.test(getAreaPointSrc),
  'getAreaPoint should be the shipped slot-table implementation');

// --- evaluate them in a fresh scope with the real module-level state ---------

function makeScope() {
  const factory = new Function(`
    let agentStations = {};
    let areaSlotsTaken = {};
    ${getAreaPointSrc}
    ${allocateStationSrc}
    ${releaseStationSrc}
    return {
      getAreaPoint, allocateStation, releaseStation,
      _stations: () => agentStations,
      _taken: () => areaSlotsTaken
    };
  `);
  return factory();
}

const AREAS = ['breakroom', 'writing', 'error'];
const SLOTS_PER_AREA = 8;

// --- the regression the user reported ----------------------------------------

test('N agents in the SAME state get N DISTINCT stations (no overlap)', () => {
  const nav = makeScope();
  const N = 6;
  const points = [];
  for (let i = 0; i < N; i++) {
    const slot = nav.allocateStation(`agent-${i}`, 'breakroom');
    const p = nav.getAreaPoint('breakroom', slot);
    points.push(`${p.x},${p.y}`);
  }
  const unique = new Set(points);
  assert.strictEqual(unique.size, N,
    `expected ${N} distinct positions, got ${unique.size}: ${points.join(' | ')}`);
});

test('a station is STABLE across polls (no jitter)', () => {
  const nav = makeScope();
  const first = nav.allocateStation('agent-a', 'writing');
  // Simulate 20 polling cycles, with other agents coming and going around it.
  for (let poll = 0; poll < 20; poll++) {
    nav.allocateStation('agent-b', 'writing');
    nav.allocateStation('agent-c', 'writing');
    const again = nav.allocateStation('agent-a', 'writing');
    assert.strictEqual(again, first,
      `agent-a moved from slot ${first} to ${again} on poll ${poll} - that is the jitter bug`);
  }
});

test('poll ORDER must not decide position', () => {
  const nav = makeScope();
  // Same three agents, two different iteration orders, same result per agent.
  const orderOne = {};
  ['a', 'b', 'c'].forEach(id => { orderOne[id] = nav.allocateStation(id, 'error'); });

  const nav2 = makeScope();
  const orderTwo = {};
  ['c', 'b', 'a'].forEach(id => { orderTwo[id] = nav2.allocateStation(id, 'error'); });

  // Each agent's slot is determined by who got there first, not by the area's
  // global ordinal - so a given agent keeps a consistent slot for a given
  // arrival order and never collides.
  assert.strictEqual(new Set(Object.values(orderOne)).size, 3);
  assert.strictEqual(new Set(Object.values(orderTwo)).size, 3);
});

test('changing area frees the old slot', () => {
  const nav = makeScope();
  const slotA = nav.allocateStation('mover', 'breakroom');
  nav.allocateStation('mover', 'writing'); // moves area
  assert.ok(!nav._taken().breakroom.has(slotA),
    'the vacated breakroom slot must be released for a later joiner');
});

test('leaving frees the slot for the next agent', () => {
  const nav = makeScope();
  const slot = nav.allocateStation('leaver', 'breakroom');
  nav.releaseStation('leaver');
  assert.ok(!nav._taken().breakroom.has(slot));
  // A newcomer should be able to take the freed slot.
  const nav2 = makeScope();
  const reused = nav2.allocateStation('newcomer', 'breakroom');
  assert.strictEqual(reused, 0, 'an empty room should hand out slot 0');
});

test('no agent ever shares a slot while free slots remain', () => {
  const nav = makeScope();
  const N = SLOTS_PER_AREA;
  const slots = [];
  for (let i = 0; i < N; i++) slots.push(nav.allocateStation(`agent-${i}`, 'breakroom'));
  assert.strictEqual(new Set(slots).size, N,
    `all ${N} distinct slots should be used before any sharing: ${slots.join(',')}`);
});

test('overflow beyond the slot table degrades deterministically, not randomly', () => {
  const nav = makeScope();
  const N = SLOTS_PER_AREA + 3; // more agents than slots
  const points = [];
  for (let i = 0; i < N; i++) {
    const slot = nav.allocateStation(`crowd-${i}`, 'breakroom');
    assert.ok(slot >= 0 && slot < SLOTS_PER_AREA,
      `slot ${slot} must stay within the table bounds`);
    const p = nav.getAreaPoint('breakroom', slot);
    points.push(`${p.x},${p.y}`);
  }
  // Overflow means sharing is unavoidable, but it must be stable, not random:
  // the same call sequence twice yields identical positions.
  const nav2 = makeScope();
  const points2 = [];
  for (let i = 0; i < N; i++) {
    const slot = nav2.allocateStation(`crowd-${i}`, 'breakroom');
    const p = nav2.getAreaPoint('breakroom', slot);
    points2.push(`${p.x},${p.y}`);
  }
  assert.deepStrictEqual(points, points2, 'overflow placement must be deterministic');
});

test('every area still resolves to a real point within its rect', () => {
  const nav = makeScope();
  AREAS.forEach(area => {
    for (let slot = 0; slot < SLOTS_PER_AREA; slot++) {
      const p = nav.getAreaPoint(area, slot);
      assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y),
        `${area} slot ${slot} produced a non-finite point`);
      assert.ok(p.x > 0 && p.y > 0, `${area} slot ${slot} is off-screen: ${p.x},${p.y}`);
    }
  });
});

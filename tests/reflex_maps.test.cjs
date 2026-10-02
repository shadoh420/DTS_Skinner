// Reflex map page: brush CSG and the map file reader/writer, run by `node --test tests/` (and by test_reflex_maps.py).
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {ReflexBrush: B} = require(path.join(__dirname, '../static/reflex-maps/brush.js'));

const wall = {material: 'wall'}, cutter = {material: 'cut'};
const near = (a, b, message) => assert.ok(Math.abs(a - b) < 1e-3, `${message || ''} ${a} ≠ ${b}`);
const totalVolume = brushes => brushes.reduce((sum, brush) => sum + B.volume(brush), 0);
function assertConvexSolid(brush) {
  assert.ok(brush, 'brush exists');
  assert.deepEqual(B.check(brush), []);
  // Wound counter-clockwise from outside: every Newell normal points away from the middle.
  const middle = brush.vertices.reduce((s, v) => s.map((x, i) => x + v[i] / brush.vertices.length), [0, 0, 0]);
  for (const face of brush.faces) {
    const points = face.indices.map(i => brush.vertices[i]), n = B.newell(points);
    const toFace = points[0].map((x, i) => x - middle[i]);
    assert.ok(n[0] * toFace[0] + n[1] * toFace[1] + n[2] * toFace[2] > 0, 'face wound outward');
  }
}
// No two pieces share volume.
function assertDisjoint(pieces) {
  for (let i = 0; i < pieces.length; i++) for (let j = i + 1; j < pieces.length; j++) assert.equal(B.intersects(pieces[i], pieces[j]), false, `pieces ${i} and ${j} overlap`);
}

test('a box is a convex solid with six faces and its volume', () => {
  const box = B.box([0, 0, 0], [64, 32, 16], wall);
  assertConvexSolid(B.rebuild(box));
  assert.equal(box.faces.length, 6);
  near(B.volume(box), 64 * 32 * 16);
  assert.deepEqual(B.bounds(box), {min: [0, 0, 0], max: [64, 32, 16]});
});

test('rebuilding from planes keeps each face its fields and welds shared corners', () => {
  const box = B.box([0, 0, 0], [16, 16, 16], wall);
  box.faces[1] = {...box.faces[1], material: 'top'};
  const rebuilt = B.rebuild(box);
  assert.equal(rebuilt.vertices.length, 8);
  assert.equal(rebuilt.faces.filter(face => face.material === 'top').length, 1);
  const top = rebuilt.faces.find(face => face.material === 'top');
  assert.ok(top.indices.every(i => rebuilt.vertices[i][2] === 16));
});

test('check finds a concave brush and a bent face', () => {
  const box = B.box([0, 0, 0], [16, 16, 16], wall);
  const bent = {vertices: box.vertices.map((v, i) => i === 6 ? [16, 16, 20] : v), faces: box.faces};
  assert.ok(B.check(bent).includes('a bent face'));
  const dented = {vertices: box.vertices.map((v, i) => i === 6 ? [8, 8, 8] : v), faces: box.faces};
  assert.ok(B.check(dented).length);
  assert.equal(B.convex(box), true);
});

test('split cuts a box in two along a plane; the cut faces take the given face', () => {
  const box = B.box([0, 0, 0], [64, 64, 64], wall);
  const {back, front} = B.split(box, {normal: [1, 0, 0], distance: 16}, cutter);
  assertConvexSolid(back); assertConvexSolid(front);
  near(B.volume(back), 16 * 64 * 64); near(B.volume(front), 48 * 64 * 64);
  assert.equal(back.faces.filter(face => face.material === 'cut').length, 1);
  assert.equal(B.split(box, {normal: [1, 0, 0], distance: 100}).front, null);
});

test('clip through three points keeps what is behind them, or both halves', () => {
  const box = B.box([0, 0, 0], [64, 64, 64], wall);
  // A diagonal plane x + y = 64 facing +x+y (right-hand order seen from that side).
  const kept = B.clip(box, [64, 0, 0], [0, 64, 0], [0, 64, 64], cutter);
  assert.equal(kept.length, 1);
  near(B.volume(kept[0]), 64 * 64 * 64 / 2);
  assert.equal(kept[0].faces.length, 5);
  assert.equal(B.clip(box, [64, 0, 0], [0, 64, 0], [0, 64, 64], cutter, true).length, 2);
});

test('subtract leaves convex pieces that fill a without b and do not overlap', () => {
  const a = B.box([0, 0, 0], [128, 128, 128], wall), b = B.box([32, 32, -16], [96, 96, 64], cutter);
  const pieces = B.subtract(a, b);
  pieces.forEach(assertConvexSolid);
  assertDisjoint(pieces);
  near(totalVolume(pieces), 128 ** 3 - 64 * 64 * 64);
  assert.ok(pieces.every(piece => !B.intersects(piece, b)));
  // The faces of the hole take the cutter's fields.
  assert.ok(pieces.some(piece => piece.faces.some(face => face.material === 'cut')));
});

test('subtract of a brush it does not touch gives the brush back; of a brush it is inside, nothing', () => {
  const a = B.box([0, 0, 0], [16, 16, 16], wall);
  assert.deepEqual(B.subtract(a, B.box([32, 0, 0], [48, 16, 16], cutter)), [a]);
  assert.deepEqual(B.subtract(a, B.box([16, 0, 0], [32, 16, 16], cutter)), [a], 'touching is not overlapping');
  assert.deepEqual(B.subtract(a, B.box([-8, -8, -8], [24, 24, 24], cutter)), []);
});

test('hollow makes walls of the given thickness around an empty inside', () => {
  const room = B.box([0, 0, 0], [256, 256, 128], wall);
  const walls = B.hollow(room, 16);
  walls.forEach(assertConvexSolid);
  assertDisjoint(walls);
  assert.equal(walls.length, 6);
  near(totalVolume(walls), 256 * 256 * 128 - 224 * 224 * 96);
  assert.ok(walls.every(piece => piece.faces.every(face => face.material === 'wall')));
});

test('merge joins brushes whose union is convex and refuses others', () => {
  const left = B.box([0, 0, 0], [32, 32, 32], wall), right = B.box([32, 0, 0], [64, 32, 32], {material: 'right'});
  const joined = B.merge(left, right);
  assertConvexSolid(joined);
  assert.equal(joined.faces.length, 6);
  near(B.volume(joined), 64 * 32 * 32);
  assert.ok(joined.faces.some(face => face.material === 'right'));
  assert.equal(B.merge(left, B.box([32, 32, 0], [64, 64, 32], wall)), null, 'an L is not convex');
  assert.equal(B.merge(left, B.box([64, 0, 0], [96, 32, 32], wall)), null, 'a gap is not convex');
});

test('offset grows and shrinks every face, and returns null when nothing is left', () => {
  const box = B.box([0, 0, 0], [32, 32, 32], wall);
  near(B.volume(B.offset(box, 8)), 48 ** 3);
  near(B.volume(B.offset(box, -8)), 16 ** 3);
  assert.equal(B.offset(box, -16), null);
});

test('a wedge rebuilds with five faces and a convex hull of its points is itself', () => {
  const wedge = B.clip(B.box([0, 0, 0], [64, 64, 64], wall), [64, 0, 0], [0, 64, 0], [0, 64, 64], cutter)[0];
  const hulled = B.hull(wedge.vertices, () => wall);
  assertConvexSolid(hulled);
  near(B.volume(hulled), B.volume(wedge));
  assert.equal(hulled.faces.length, 5);
});

test('triangles fan a convex face and hand a concave one to the triangulator', () => {
  const box = B.box([0, 0, 0], [16, 16, 16], wall);
  assert.equal(B.triangles(box, box.faces[0]).length, 2);
  const concave = {vertices: [[0, 0, 0], [4, 0, 0], [4, 4, 0], [2, 1, 0], [0, 4, 0]], faces: [{indices: [0, 1, 2, 3, 4]}]};
  let asked = false;
  B.triangles(concave, concave.faces[0], () => { asked = true; return []; });
  assert.ok(asked);
});

test('raycast finds the face a ray enters by and misses what it passes', () => {
  const box = B.box([0, 0, 0], [16, 16, 16], wall);
  box.faces[5] = {...box.faces[5], material: 'west'};
  const hit = B.raycast(box, [-10, 8, 8], [1, 0, 0]);
  near(hit.distance, 10);
  assert.equal(hit.face.material, 'west');
  assert.equal(B.raycast(box, [-10, 30, 8], [1, 0, 0]), null);
  assert.equal(B.raycast(box, [-10, 8, 8], [-1, 0, 0]), null);
});

test('subtract conserves volume for random convex brushes: a = pieces + (a ∩ b)', () => {
  let seed = 12345;
  const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const point = () => [random() * 256 - 128, random() * 256 - 128, random() * 256 - 128];
  const randomBrush = material => {
    let brush = B.box([-96, -96, -96], [96, 96, 96], {material});
    for (let cuts = 0; cuts < 4 && brush; cuts++) {
      const kept = B.clip(brush, point(), point(), point(), {material})[0];
      if (kept && B.volume(kept) > 1000) brush = kept;
    }
    return brush;
  };
  let checked = 0;
  for (let round = 0; round < 60; round++) {
    const a = randomBrush('a'), b = B.translate(randomBrush('b'), point().map(x => x / 2));
    const pieces = B.subtract(a, b), shared = B.intersection(a, b);
    pieces.forEach(assertConvexSolid);
    assertDisjoint(pieces);
    const expected = B.volume(a), found = totalVolume(pieces) + (shared ? B.volume(shared) : 0);
    assert.ok(Math.abs(expected - found) < expected * 1e-6 + 1e-2, `round ${round}: ${expected} ≠ ${found}`);
    if (shared && pieces.length) checked++;
  }
  assert.ok(checked > 20, `only ${checked} rounds overlapped`);
});

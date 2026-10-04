/* Block meshing for the Diabotical map page (static/diabotical-maps/blocks.js). Run: node --test tests/diabotical_maps.test.cjs */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {buildBlocks, SIZE, FACE} = require('../static/diabotical-maps/blocks.js').DiaboticalBlocks;

// Blocks as the import writes them: [x, y, z, shape, turn, open faces, six face materials].
function blocks(...list) {
  const view = new DataView(new ArrayBuffer(list.length * SIZE));
  list.forEach(([x, y, z, shape, turn, open, faces], i) => {
    const at = i * SIZE;
    view.setInt16(at, x, true); view.setInt16(at + 2, y, true); view.setInt16(at + 4, z, true);
    view.setUint8(at + 6, shape); view.setUint8(at + 7, turn); view.setUint8(at + 8, open);
    faces.forEach((material, face) => view.setUint8(at + 10 + face, material));
  });
  return view.buffer;
}
const triangles = ({positions}) => positions.length / 9;
const corner = (array, i) => Array.from(array.slice(i * 3, i * 3 + 3));
// Each triangle's corners turn counter-clockwise seen from where its normal points (Three's front face).
function assertOutward({positions, normals}) {
  for (let t = 0; t < positions.length / 9; t++) {
    const [a, b, c] = [0, 1, 2].map(k => corner(positions, t * 3 + k)), n = corner(normals, t * 3);
    const u = b.map((v, i) => v - a[i]), w = c.map((v, i) => v - a[i]);
    const cross = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]];
    assert.ok(cross[0] * n[0] + cross[1] * n[1] + cross[2] * n[2] > 0, `triangle ${t} faces inward`);
  }
}

test('a cube fills its cell, each face in its own material and facing out', () => {
  const groups = buildBlocks(blocks([2, -1, 3, 1, 0, 0x3f, [0, 1, 2, 3, 4, 5]]), [1, 1, 1, 1, 1, 1]);
  assert.equal(groups.size, 6);
  // Faces +z, -x, -z, +x, top, bottom of the file; z is mirrored, so the file's +z face points to -Z.
  const normals = [[0, 0, -1], [-1, 0, 0], [0, 0, 1], [1, 0, 0], [0, 1, 0], [0, -1, 0]];
  for (const [material, group] of groups) {
    assert.equal(triangles(group), 2);
    assert.deepEqual(corner(group.normals, 0), normals[material]);
    assertOutward(group);
    for (let i = 0; i < group.positions.length / 3; i++) {
      const [x, y, z] = corner(group.positions, i);
      assert.ok(x >= 80 && x <= 120 && y >= -20 && y <= 0 && z >= -160 && z <= -120, `${[x, y, z]} lies outside the cell`);
    }
  }
  const top = groups.get(4);
  for (let i = 0; i < 6; i++) assert.equal(top.positions[i * 3 + 1], 0);
});

test('closed faces are left out and texture coordinates follow the material scale', () => {
  const groups = buildBlocks(blocks([0, 0, 0, 1, 0, 0b010000, [7, 7, 7, 7, 1, 7]]), [1, .125]);
  assert.deepEqual([...groups.keys()], [1]);
  const {positions, uvs} = groups.get(1);
  // Top: u along +X, v along -Z, a texture every 40 / 0.125 = 320 units.
  for (let i = 0; i < positions.length / 3; i++) {
    assert.ok(Math.abs(uvs[i * 2] - positions[i * 3] / 320) < 1e-6);
    assert.ok(Math.abs(uvs[i * 2 + 1] + positions[i * 3 + 2] / 320) < 1e-6);
  }
});

test('a half block keeps three corners and turns its lost corner with the turn', () => {
  for (let turn = 0; turn < 4; turn++) {
    const groups = buildBlocks(blocks([0, 0, 0, 3, turn, 0x3f, [0, 1, 2, 3, 4, 5]]), []);
    const all = [...groups.values()];
    assert.equal(all.reduce((sum, group) => sum + triangles(group), 0), 8);  // Top, bottom, two sides, the slope.
    all.forEach(assertOutward);
    const top = groups.get(4);
    const corners = new Set([0, 1, 2].map(i => corner(top.positions, i)).map(([x, , z]) => `${x / 40},${z / 40 + 1}`));
    const lost = [[0, 0], [0, 1], [1, 1], [1, 0]][turn];
    assert.equal(corners.size, 3);
    assert.ok(!corners.has(lost.join(',')), `turn ${turn} kept its lost corner`);
    // The sloped side, facing the lost corner, in the material and texture axes of the face numbered by the turn
    // (found in the game's export).
    const [material, sloped] = [...groups].find(([, group]) => Array.from(group.normals).some((value, i) => i % 3 === 0 && value && group.normals[i + 2]));
    assert.equal(material, turn);
    const at = Array.from(sloped.normals).findIndex((value, i) => i % 3 === 0 && value && sloped.normals[i + 2]) / 3;
    const slope = corner(sloped.normals, at);
    assert.ok(Math.abs(Math.hypot(...slope) - 1) < 1e-6);
    assert.deepEqual(slope.map(Math.sign), [lost[0] ? 1 : -1, 0, lost[1] ? 1 : -1]);
    const {u} = FACE[turn], point = corner(sloped.positions, at);
    assert.ok(Math.abs(sloped.uvs[at * 2] - (point[0] * u[0] + point[1] * u[1] + point[2] * u[2]) / 40) < 1e-6);
  }
});

test('shapes that draw nothing and an empty map give no surfaces', () => {
  assert.equal(buildBlocks(blocks([0, 0, 0, 2, 0, 0x3f, [1, 1, 1, 1, 1, 1]]), []).size, 0);
  assert.equal(buildBlocks(new ArrayBuffer(0), []).size, 0);
});

test('an entities file gives each prop group its matrices, and the markers and liquids', () => {
  const {parseEntities} = require('../static/diabotical-maps/entities.js').DiaboticalEntities;
  let head = Buffer.from(JSON.stringify({props: [['a/b|stone|', 2], ['c||m', 1]], markers: [['spawn', 1, 2, 3]], liquids: []}));
  head = Buffer.concat([head, Buffer.alloc((4 - head.length % 4) % 4, 32)]);
  const matrices = Float32Array.from({length: 36}, (_, i) => i);
  const file = Buffer.concat([Buffer.from(Uint32Array.of(head.length).buffer), head, Buffer.from(matrices.buffer)]);
  const {props, markers, liquids} = parseEntities(file.buffer.slice(file.byteOffset, file.byteOffset + file.length));
  assert.deepEqual(props.map(({model, material, mirrored, matrices}) => [model, material, mirrored, matrices.length, matrices[0]]),
    [['a/b', 'stone', false, 24, 0], ['c', '', true, 12, 24]]);
  assert.deepEqual(markers, [['spawn', 1, 2, 3]]);
  assert.deepEqual(liquids, []);
});

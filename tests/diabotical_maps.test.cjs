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

test('an entities file gives each prop group its matrices and tints, and the markers, liquids and decals', () => {
  const {parseEntities} = require('../static/diabotical-maps/entities.js').DiaboticalEntities;
  let head = Buffer.from(JSON.stringify({props: [['a/b|stone|', 2, 1], ['c||m', 1, 0]], markers: [['spawn', 1, 2, 3]], liquids: [], decals: [['arrow', 2]]}));
  head = Buffer.concat([head, Buffer.alloc((4 - head.length % 4) % 4, 32)]);
  const matrices = Float32Array.from({length: 36}, (_, i) => i), tints = Uint32Array.of(0x1ff0000, 0, 0, 0, 0, 0x1336699);
  const boxes = Float32Array.from({length: 48}, (_, i) => 100 + i), extras = Int32Array.of(-1, 1, 1000, 0x112233ff, 4, -2);
  const file = Buffer.concat([Buffer.from(Uint32Array.of(head.length).buffer), head, Buffer.from(matrices.buffer), Buffer.from(tints.buffer),
    Buffer.from(boxes.buffer), Buffer.from(extras.buffer)]);
  const {props, markers, liquids, decals} = parseEntities(file.buffer.slice(file.byteOffset, file.byteOffset + file.length));
  assert.deepEqual(decals.map(({material, matrices, extras, orders}) => [material, matrices.length, matrices[12], extras[0], extras[1], orders[2], orders[5]]),
    [['arrow', 48, 112, 0xffffffff, 1, 1000, -2]]);
  assert.deepEqual(props.map(({model, material, mirrored, matrices}) => [model, material, mirrored, matrices.length, matrices[0]]),
    [['a/b', 'stone', false, 24, 0], ['c', '', true, 12, 24]]);
  assert.deepEqual([...props[0].tints], [...tints]);
  assert.equal(props[1].tints, null);
  assert.deepEqual(markers, [['spawn', 1, 2, 3]]);
  assert.deepEqual(liquids, []);
});

test('lights are listed per cell and ambient nodes make a grid', () => {
  const {buildLights, buildGrid, nodeWeights, materialClass, CELL, WIDTH} = require('../static/diabotical-maps/lighting.js').DiaboticalLighting;
  // A diffuse point light of radius 100 at the origin and a capsule from x 1000 along +x for 300, radius 50, its
  // specular light 3 times its colour.
  const built = buildLights([[0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 100, 33, 0, 0, 0, 0], [2, 1000, 0, 0, 1, 0, 0, 1, 1, 1, 50, 25, 0, 0, 300, 3]]);
  assert.deepEqual(built.min, [-100, -100, -100]);
  assert.deepEqual(built.size, [Math.ceil(1450 / CELL), 1, 1]);
  const list = cell => { const [at, count] = built.cells.slice(cell * 2, cell * 2 + 2); return Array.from(built.lists.slice(at, at + count)); };
  assert.deepEqual([0, 1, 2, 3, 4, 5].map(list), [[0], [], [], [], [1], [1]]);
  assert.equal(built.cells.length % (WIDTH * 2), 0);
  assert.deepEqual(Array.from(built.rows.slice(16, 32)), [1000, 0, 0, 50, 1, 1, 1, 2, 1, 0, 0, 25, 0, 0, 300, 3]);
  // Material ids: most blocks' 40 reflect half, metals reflect whole and take no ambient, 46 takes 1.4 times it.
  assert.deepEqual([0, 40, 46, 51, 60, 103].map(materialClass), [[0, 1, 0], [.5, 1, 0], [.5, 1.4, 0], [1, 0, 1], [1, 0, 0], [0, 1, 0]]);
  // A sphere's share is 0.86 - d, its colour's weight the share squared; a cubic node is whole to 0.86 and gone at 1.11.
  assert.deepEqual([nodeWeights(0, .36), nodeWeights(0, .86)], [[.5, .25], [0, 0]]);
  assert.deepEqual([nodeWeights(1, .86), nodeWeights(1, 1.11)], [[1, 1], [0, 0]]);
  const grid = buildGrid([[0, 0, 0, 0, 100, 0, 0, .5]]);
  const at = point => {
    const i = point.map((v, k) => Math.floor((v - grid.min[k]) / ((grid.max[k] - grid.min[k]) / grid.size[k]))), base = ((i[2] * grid.size[1] + i[1]) * grid.size[0] + i[0]) * 4;
    return Array.from(grid.data.slice(base, base + 4));
  };
  const centre = at([0, 0, 0]), edge = at([grid.min[0] + 1, 0, 0]);
  assert.ok(centre[3] > .7 && centre[2] > .2 && centre[0] === 0, String(centre));  // A texel centre near the node.
  assert.deepEqual(edge, [0, 0, 0, 0]);
  assert.equal(buildGrid([]), null);
});

test('a decal takes the part of each surface in its box that faces its local +z', () => {
  const {createProjector} = require('../static/diabotical-maps/decals.js').DiaboticalDecals;
  // A 40 x 40 box, 10 deep, its local z up the world's y (so it faces the floor's top), centred at (10, 0, 0).
  const projector = createProjector([[40, 0, 0, 10, 0, 0, 10, 0, 0, -40, 0, 0]]);
  const up = [0, 1, 0];
  // The floor y = 0 from -100 to 100, counter-clockwise from above, then the same seen from below and a wall facing +x.
  projector.add([-100, 0, 100], [100, 0, 100], [100, 0, -100], up, up, up);
  projector.add([-100, 0, 100], [100, 0, -100], [-100, 0, -100], up, up, up);
  projector.add([-100, 0, 100], [100, 0, -100], [100, 0, 100], up, up, up);
  projector.add([0, -50, 0], [0, -50, -50], [0, 50, 0], up, up, up);
  const {positions, uvs, normals} = projector.out[0];
  let area = 0;
  for (let i = 0; i < positions.length; i += 9) {
    const [ax, , az, bx, , bz, cx, , cz] = positions.slice(i, i + 9);
    area += ((bx - ax) * (cz - az) - (cx - ax) * (bz - az)) / -2;
  }
  assert.ok(Math.abs(area - 1600) < 1e-6, area);
  assert.ok(positions.every((v, i) => i % 3 === 0 ? v >= -10 - 1e-9 && v <= 30 + 1e-9 : i % 3 === 1 ? v === 0 : Math.abs(v) <= 20 + 1e-9));
  assert.ok(uvs.every(v => v >= -1e-9 && v <= 1 + 1e-9));
  // uv follows local x and y: u 0 at world x -10, v 0 at world z +20 (local -y).
  const corner = positions.findIndex((v, i) => i % 3 === 0 && Math.abs(v + 10) < 1e-9 && Math.abs(positions[i + 2] - 20) < 1e-9);
  assert.ok(corner >= 0);
  assert.deepEqual(uvs.slice(corner / 3 * 2, corner / 3 * 2 + 2).map(v => Math.round(v * 1e6) / 1e6), [0, 0]);
  assert.deepEqual(normals.slice(0, 3), [0, 1, 0]);
  // Cut to a second box half as wide along x: half the area, the texture where it was.
  const cut = createProjector([[40, 0, 0, 10, 0, 0, 10, 0, 0, -40, 0, 0]], [[20, 0, 0, 10, 0, 0, 10, 0, 0, -40, 0, 0]]);
  cut.add([-100, 0, 100], [100, 0, 100], [100, 0, -100], up, up, up);
  cut.add([-100, 0, 100], [100, 0, -100], [-100, 0, -100], up, up, up);
  const half = cut.out[0];
  assert.ok(half.positions.every((v, i) => i % 3 !== 0 || (v >= -1e-9 && v <= 20 + 1e-9)));
  assert.ok(half.uvs.every((v, i) => i % 2 || (v >= .25 - 1e-9 && v <= .75 + 1e-9)));
});

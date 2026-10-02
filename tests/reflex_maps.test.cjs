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

// ---- Map files ----
const fs = require('node:fs');
const {ReflexMap: M} = require(path.join(__dirname, '../static/reflex-maps/mapfile.js'));
// Written as the game writes a map (laid out as the stock maps are): a prefab with an entity and a brush whose
// second face has no material, a global placing it twice, a property with spaces and a negative zero.
const SAMPLE = [
  'reflex map version 8',
  'prefab step',
  '\tentity',
  '\t\ttype WorldSpawn',
  '\tbrush',
  '\t\tvertices',
  '\t\t\t0.000000 0.000000 0.000000', '\t\t\t16.000000 0.000000 0.000000', '\t\t\t16.000000 0.000000 32.000000', '\t\t\t0.000000 0.000000 32.000000',
  '\t\t\t0.000000 8.000000 0.000000', '\t\t\t16.000000 8.000000 0.000000', '\t\t\t16.000000 8.000000 32.000000', '\t\t\t0.000000 8.000000 32.000000',
  '\t\tfaces',
  '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 1 2 3 0xff332805 common/materials/wood/bare',
  '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 7 6 5 4 0x00000000 ',
  '\t\t\t0.000000 0.000000 1.000000 1.000000 90.000008 0 4 5 1 0xff332805 common/materials/wood/bare',
  '\t\t\t0.000000 -64.000000 1.000000 0.750000 0.000000 2 6 7 3 0xff332805 common/materials/wood/bare',
  '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 1 5 6 2 0xff332805 common/materials/wood/bare',
  '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 3 7 4 0 0xff332805 common/materials/wood/bare',
  'global',
  '\tentity',
  '\t\ttype WorldSpawn',
  '\t\tString32 targetGameOverCamera end',
  '\t\tString256 ownerString Someone + Someone Else',
  '\t\tColourXRGB32 fogColor ff67ba88',
  '\tentity',
  '\t\ttype Prefab',
  '\t\tVector3 position 100.000000 0.000000 0.000000',
  '\t\tVector3 angles 90.000000 -0.000000 0.000000',
  '\t\tString64 prefabName step',
  '\tentity',
  '\t\ttype Prefab',
  '\t\tVector3 position -100.000000 0.000000 0.000000',
  '\t\tString64 prefabName step',
  '\tentity',
  '\t\ttype Pickup',
  '\t\tVector3 position 0.000000 16.000000 0.000000',
  '\t\tUInt8 pickupType 4',
  '\tentity',
  '\t\ttype Prefab',
  '\t\tString64 prefabName nowhere',
  '',
].join('\r\n');

test('a map written back is the file read, byte for byte', () => {
  const map = M.parse(SAMPLE);
  assert.equal(M.write(map), SAMPLE);
  assert.equal(map.version, 8);
  assert.deepEqual(map.groups.map(group => [group.kind, group.name, group.items.map(item => item.kind)]),
    [['prefab', 'step', ['entity', 'brush']], ['global', '', ['entity', 'entity', 'entity', 'entity', 'entity']]]);
});

test('faces keep offset, scale, rotation, colour and an empty material', () => {
  const brush = M.prefab(M.parse(SAMPLE), 'step').items[1];
  assert.deepEqual(brush.faces[0], {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0, indices: [0, 1, 2, 3], colour: '0xff332805', material: 'common/materials/wood/bare'});
  assert.equal(brush.faces[1].material, '');
  assert.equal(brush.faces[2].rotation, 90.000008);
  assert.deepEqual([brush.faces[3].v, brush.faces[3].scaleV], [-64, 0.75]);
  assert.deepEqual(M.colourOf(brush.faces[0]).map(c => Math.round(c * 255)), [0x33, 0x28, 0x05, 255]);
  assert.equal(M.colourOf(brush.faces[1]), null, 'zero alpha means the material colour');
  // The brush as stored is a convex solid wound outward.
  assertConvexSolid(B.rebuild(brush));
  assert.deepEqual(B.check(brush), []);
});

test('properties are typed, strings keep their spaces and a negative zero keeps its sign', () => {
  const [world, placed] = M.global(M.parse(SAMPLE)).items;
  assert.equal(M.property(world, 'ownerString'), 'Someone + Someone Else');
  assert.equal(M.property(world, 'fogColor'), 'ff67ba88');
  assert.ok(Object.is(M.property(placed, 'angles')[1], -0));
  assert.equal(M.fixed(-0), '-0.000000');
  // From 10^17 up, 17 significant digits then zeros, as the game's C runtime writes them (AbandonedShelter).
  assert.equal(M.fixed(Number('1602806319568810500000000.000000')), '1602806319568810500000000.000000');
  assert.equal(M.fixed(-123456789012345678901), '-123456789012345680000.000000');
  assert.equal(M.fixed(99999999999999984), '99999999999999984.000000');
});

test('prefabs are found whatever the case of their name, as the game finds them', () => {
  const text = SAMPLE.replace('\t\tString64 prefabName step\r\n\tentity\r\n\t\ttype Pickup', '\t\tString64 prefabName STEP\r\n\tentity\r\n\t\ttype Pickup');
  assert.notEqual(text, SAMPLE);
  const map = M.parse(text);
  assert.equal(M.flatten(map).brushes.length, 2);
  assert.equal(M.prefab(map, 'Step').name, 'step');
});

test('flatten places prefabs where Prefab entities put them, turned by yaw about y', () => {
  const flat = M.flatten(M.parse(SAMPLE));
  assert.equal(flat.brushes.length, 2);
  assert.deepEqual(flat.missing, ['nowhere']);
  const [turned, plain] = flat.brushes.map(entry => B.bounds(entry.brush));
  // Yaw 90 takes +z to +x: the step's 32-long side now runs along x.
  assert.deepEqual(turned.min.map(Math.round), [100, 0, -16]);
  assert.deepEqual(turned.max.map(Math.round), [132, 8, 0]);
  assert.deepEqual(plain, {min: [-100, 0, 0], max: [-84, 8, 32]});
  // Placed copies share the stored faces; only the vertices move.
  assert.equal(flat.brushes[0].brush.faces, flat.brushes[0].source.faces);
  assert.deepEqual(flat.entities.map(entry => entry.entity.type), ['WorldSpawn', 'Prefab', 'WorldSpawn', 'Prefab', 'WorldSpawn', 'Pickup', 'Prefab']);
});

test('a prefab that places itself stops instead of recursing forever', () => {
  const map = M.parse(['reflex map version 8', 'prefab loop', '\tentity', '\t\ttype Prefab', '\t\tString64 prefabName loop',
    'global', '\tentity', '\t\ttype Prefab', '\t\tString64 prefabName loop', ''].join('\n'));
  assert.ok(M.flatten(map).entities.length <= 20);
  assert.equal(M.write(map), ['reflex map version 8', 'prefab loop', '\tentity', '\t\ttype Prefab', '\t\tString64 prefabName loop',
    'global', '\tentity', '\t\ttype Prefab', '\t\tString64 prefabName loop', ''].join('\n'), 'LF files stay LF');
});

test('version 6 maps have no groups and no face colours', () => {
  const text = ['reflex map version 6', 'entity', '\ttype WorldSpawn', 'brush', '\tvertices',
    '\t\t0.000000 0.000000 0.000000', '\t\t8.000000 0.000000 0.000000', '\t\t0.000000 8.000000 0.000000', '\t\t0.000000 0.000000 8.000000',
    '\tfaces',
    '\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 2 1 internal/editor/textures/grid',
    '\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 1 3 internal/editor/textures/grid',
    '\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 3 2 internal/editor/textures/grid',
    '\t\t0.000000 0.000000 1.000000 1.000000 0.000000 1 2 3 internal/editor/textures/grid', ''].join('\r\n');
  const map = M.parse(text);
  assert.equal(M.write(map), text);
  const brush = M.global(map).items[1];
  assert.equal(brush.faces[0].colour, null);
  assert.equal(brush.faces[0].material, 'internal/editor/textures/grid');
  assertConvexSolid(B.rebuild(brush));
});

test('a damaged file names the line at fault', () => {
  assert.throws(() => M.parse('not a map'), /line 1/);
  assert.throws(() => M.parse(SAMPLE.replace('0 1 2 3 0xff332805', '0 1 2 99 0xff332805')), /vertex the brush does not have/);
  assert.throws(() => M.parse(SAMPLE.replace('\t\t\t16.000000 0.000000 0.000000', '\t\t\t16.000000 zero 0.000000')), /line 8: bad vertex/);
  assert.throws(() => M.parse(SAMPLE.replace('\tbrush', '\tbrsh')), /expected entity or brush/);
});

test('a new map is a valid version 8 map with a WorldSpawn', () => {
  const text = M.write(M.empty());
  assert.equal(M.write(M.parse(text)), text);
  assert.equal(M.global(M.parse(text)).items[0].type, 'WorldSpawn');
});

test('edited brushes write back as a map the reader accepts', () => {
  const map = M.parse(SAMPLE), group = M.global(map);
  const room = B.box([0, 0, 0], [256, 128, 256], {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0, colour: '0xff80827a', material: 'common/materials/stone/stone'});
  for (const wall of B.hollow(room, 16)) group.items.push({kind: 'brush', ...wall});
  const again = M.parse(M.write(map));
  const walls = M.global(again).items.filter(item => item.kind === 'brush');
  assert.equal(walls.length, 6);
  walls.forEach(wall => assertConvexSolid(B.rebuild(wall)));
  near(totalVolume(walls), 256 * 128 * 256 - 224 * 96 * 224);
});

// Maps of your own: REFLEX_MAPS=folder (searched for .map files) reads each, writes it back and compares.
test('every map in REFLEX_MAPS reads, writes back unchanged and places its prefabs', {skip: !process.env.REFLEX_MAPS && 'set REFLEX_MAPS to a folder of .map files'}, () => {
  const files = [];
  const walk = folder => { for (const entry of fs.readdirSync(folder, {withFileTypes: true})) {
    const full = path.join(folder, entry.name);
    if (entry.isDirectory()) walk(full); else if (/\.map$/i.test(entry.name)) files.push(full);
  } };
  walk(process.env.REFLEX_MAPS);
  assert.ok(files.length, 'no .map files found');
  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8'), map = M.parse(text);
    assert.equal(M.write(map), text, file);
    const flat = M.flatten(map);
    assert.deepEqual(flat.missing, [], file);
    assert.ok(flat.brushes.length, file);
  }
});

test("a brush belongs to the entity before it: the WorldSpawn's are the world, a Teleporter's its volume", () => {
  const box = ['\tbrush', '\t\tvertices', '\t\t\t0.000000 0.000000 0.000000', '\t\t\t8.000000 0.000000 0.000000', '\t\t\t0.000000 8.000000 0.000000', '\t\t\t0.000000 0.000000 8.000000',
    '\t\tfaces', '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 2 1 0x00000000 ', '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 1 3 0x00000000 ',
    '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 3 2 0x00000000 ', '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 1 2 3 0x00000000 '];
  // As Aerowalk has them: the world's brushes after its WorldSpawn, a teleporter followed by its volume.
  const map = M.parse(['reflex map version 8', 'global', '\tentity', '\t\ttype WorldSpawn', ...box, ...box,
    '\tentity', '\t\ttype Pickup', '\t\tVector3 position 0.000000 0.000000 0.000000',
    '\tentity', '\t\ttype Teleporter', '\t\tString32 target p1', ...box, ''].join('\r\n'));
  const owners = M.flatten(map).brushes.map(entry => entry.owner.type);
  assert.deepEqual(owners, ['WorldSpawn', 'WorldSpawn', 'Teleporter']);
  assert.deepEqual(M.flatten(map).brushes.map(entry => M.isVolume(entry.owner)), [false, false, true]);
  // A new world brush goes after the WorldSpawn's run, before the pickup.
  assert.equal(M.worldInsertAt(M.global(map)), 3);
  assert.equal(M.worldInsertAt({items: [{kind: 'brush'}, {kind: 'entity', type: 'Effect'}]}), 1);
});

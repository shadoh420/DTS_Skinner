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

// ---- The bridge tool ----
const faceOf = (brush, predicate) => { const face = brush.faces.find(f => predicate(f.indices.map(i => brush.vertices[i]))); return face.indices.map(i => brush.vertices[i]); };

test('a bridge between faces looking at each other is one straight convex brush filling the gap', () => {
  const left = B.box([0, 0, 0], [32, 32, 32], wall), right = B.box([96, 0, 0], [128, 32, 32], wall);
  const a = faceOf(left, points => points.every(p => p[0] === 32)), b = faceOf(right, points => points.every(p => p[0] === 96));
  const [run] = B.bridge(a, [1, 0, 0], b, [-1, 0, 0], 1, cutter);
  assertConvexSolid(run);
  assert.deepEqual(B.bounds(run), {min: [32, 0, 0], max: [96, 32, 32]});
  near(B.volume(run), 64 * 32 * 32);
  assert.ok(run.faces.every(face => face.material === 'cut'));
  // Split into steps, the pieces fill the same gap without overlapping.
  const steps = B.bridge(a, [1, 0, 0], b, [-1, 0, 0], 4, cutter);
  assert.equal(steps.length, 4);
  steps.forEach(assertConvexSolid);
  assertDisjoint(steps);
  near(totalVolume(steps), 64 * 32 * 32);
});

test('a bridge between faces at a right angle is an arch whose steps meet end to end', () => {
  // The top of a block and the side of a higher one: the run leaves upward and comes into the side going +x.
  const low = B.box([0, 0, 0], [32, 32, 32], wall), high = B.box([160, 96, 0], [192, 128, 32], wall);
  const a = faceOf(low, points => points.every(p => p[1] === 32)), b = faceOf(high, points => points.every(p => p[0] === 160));
  const steps = B.bridge(a, [0, 1, 0], b, [-1, 0, 0], 6, cutter);
  assert.equal(steps.length, 6);
  for (const step of steps) assert.ok(B.volume(step) > 100, 'every step has volume');
  // It starts on the top face and ends on the side face, corner for corner.
  const key = p => p.map(v => Math.round(v * 1000) / 1000).join();
  const first = new Set(steps[0].vertices.map(key)), last = new Set(steps.at(-1).vertices.map(key));
  assert.ok(a.every(p => first.has(key(p))) && b.every(p => last.has(key(p))));
  // Consecutive steps share their joining section, so the run has no gaps.
  for (let k = 0; k + 1 < steps.length; k++) {
    const shared = steps[k].vertices.filter(p => steps[k + 1].vertices.some(q => key(q) === key(p)));
    assert.equal(shared.length, 4, `steps ${k} and ${k + 1} share a section`);
  }
  // Every face of every step is wound outward (Newell normal away from the middle of its step).
  for (const step of steps) {
    const middle = step.vertices.reduce((s, v) => s.map((x, i) => x + v[i] / step.vertices.length), [0, 0, 0]);
    for (const face of step.faces) {
      const points = face.indices.map(i => step.vertices[i]), n = B.newell(points), c = points.reduce((s, v) => s.map((x, i) => x + v[i] / points.length), [0, 0, 0]);
      assert.ok(n[0] * (c[0] - middle[0]) + n[1] * (c[1] - middle[1]) + n[2] * (c[2] - middle[2]) > 0);
    }
  }
  // Corner for corner as the run turns: the edge of the top facing the high block (x = 32) becomes the underside of
  // the arch, so meets the bottom of the side (y = 96); the far edge (x = 0) meets its top (y = 128). Each step keeps
  // its sections' corners in order, the first section's then the next's.
  const index = corner => steps[0].vertices.findIndex(p => key(p) === key(corner));
  for (const [from, to] of [[[32, 32, 0], [160, 96, 0]], [[32, 32, 32], [160, 96, 32]], [[0, 32, 0], [160, 128, 0]], [[0, 32, 32], [160, 128, 32]]]) {
    assert.deepEqual(steps.at(-1).vertices[index(from) + 4].map(Math.round), to, `${from} runs to ${to}`);
  }
});

test('a bridge refuses faces of different corner counts', () => {
  const box = B.box([0, 0, 0], [32, 32, 32], wall);
  const quad = faceOf(box, points => points.every(p => p[0] === 32));
  assert.equal(B.bridge(quad, [1, 0, 0], [[64, 0, 0], [64, 32, 0], [64, 0, 32]], [-1, 0, 0], 2, cutter), null);
});

test('texture coordinates project a face on the axis plane it faces, then turn, scale and offset them', () => {
  const box = B.box([0, 0, 0], [64, 32, 16], {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0});
  const top = box.faces.find(face => face.indices.every(i => box.vertices[i][1] === 32));
  const corner = brush => B.texcoords(brush, brush.faces[box.faces.indexOf(top)])[top.indices.indexOf(box.vertices.findIndex(v => v[0] === 64 && v[1] === 32 && v[2] === 16))];
  const withFace = fields => ({vertices: box.vertices, faces: box.faces.map(face => face === top ? {...face, ...fields} : face)});
  // A floor or ceiling: u along x, v against z.
  assert.deepEqual(corner(box), [64, -16]);
  assert.deepEqual(corner(withFace({u: 16, v: -32})), [80, -48]);
  assert.deepEqual(corner(withFace({scaleU: 2, scaleV: -1})), [32, 16]);
  const [u, v] = corner(withFace({rotation: 90}));
  near(u, 16); near(v, 64);
  // A wall facing x: u along z, v down y.
  const side = box.faces.find(face => face.indices.every(i => box.vertices[i][0] === 64));
  const coords = B.texcoords(box, side), at = side.indices.indexOf(box.vertices.findIndex(v => v[0] === 64 && v[1] === 32 && v[2] === 16));
  assert.deepEqual(coords[at], [16, -32]);
});

test('breaking a placement gives what flatten draws, and the inverse of its placement gives the prefab back', () => {
  const map = M.parse(SAMPLE), flat = M.flatten(map);
  const turned = M.global(map).items.find(item => item.type === 'Prefab' && M.property(item, 'angles'));
  const parts = M.breakPrefab(map, turned);
  assert.equal(parts.world.length, 1);
  assert.deepEqual(parts.world[0].vertices, flat.brushes[0].brush.vertices.map(v => v.map(x => Math.round(x * 1e6) / 1e6 + 0)));
  const placed = M.placement(map, turned), back = M.moveItem(parts.world[0], M.invert(placed.transform), -placed.yaw);
  assert.deepEqual(back.vertices, M.prefab(map, 'step').items.find(item => item.kind === 'brush').vertices);
  // A placement's yaw is added to the angles of what it holds, or given to a turnable entity that has none.
  const spawn = {kind: 'entity', type: 'PlayerSpawn', properties: [{type: 'Vector3', name: 'position', value: [0, 0, 32]}]};
  const moved = M.moveItem(spawn, placed.transform, placed.yaw);
  assert.deepEqual(M.property(moved, 'position'), [132, 0, 0]);
  assert.deepEqual(M.property(moved, 'angles'), [90, 0, 0]);
  assert.deepEqual(M.property(M.moveItem(moved, placed.transform, 300), 'angles'), [30, 0, 0]);
  assert.equal(M.breakPrefab(map, M.global(map).items.find(item => M.property(item, 'prefabName') === 'nowhere')), null);
});

test('setPrefab makes a prefab before the map, or replaces what one holds; prefabUses counts its placements', () => {
  const map = M.parse(SAMPLE), brush = {kind: 'brush', ...B.box([0, 0, 0], [16, 16, 16], {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0, colour: '0x00000000', material: ''})};
  const light = {kind: 'entity', type: 'PointLight', properties: [{type: 'Vector3', name: 'position', value: [8, 24, 8]}]};
  M.setPrefab(map, 'lamp', {world: [brush], rest: [light]});
  assert.deepEqual(map.groups.map(group => group.kind === 'global' ? 'global' : group.name), ['step', 'lamp', 'global']);
  assert.deepEqual(M.prefab(map, 'lamp').items.map(item => item.type || item.kind), ['WorldSpawn', 'brush', 'PointLight']);
  const head = M.prefab(map, 'step').items[0];
  M.setPrefab(map, 'STEP', {world: [brush], rest: []});
  assert.equal(M.prefab(map, 'step').items[0], head, 'an existing prefab keeps its WorldSpawn');
  assert.equal(M.prefab(map, 'step').items.length, 2);
  assert.deepEqual(M.prefabUses(map).map(({name, uses}) => [name, uses]), [['step', 2], ['lamp', 0]]);
  const text = M.write(map);
  assert.equal(M.write(M.parse(text)), text);
  assert.equal(M.flatten(M.parse(text)).brushes.length, 2);
});

test('every placement in REFLEX_MAPS breaks into what flatten draws, and goes back into its prefab', {skip: !process.env.REFLEX_MAPS && 'set REFLEX_MAPS to a folder of .map files'}, () => {
  const fs = require('node:fs'), files = [];
  const walk = folder => { for (const entry of fs.readdirSync(folder, {withFileTypes: true})) {
    const full = path.join(folder, entry.name);
    if (entry.isDirectory()) walk(full); else if (/\.map$/i.test(entry.name)) files.push(full);
  } };
  walk(process.env.REFLEX_MAPS);
  let placements = 0;
  for (const file of files) {
    const map = M.parse(fs.readFileSync(file, 'utf8')), flat = M.flatten(map);
    for (const entity of M.global(map).items.filter(item => item.type === 'Prefab')) {
      const parts = M.breakPrefab(map, entity);
      if (!parts) continue;
      placements++;
      // flatten's brushes placed directly by this entity, those of nested prefabs left out.
      const drawn = flat.brushes.filter(entry => entry.path.length === 1 && entry.path[0] === entity);
      const broken = [...parts.world, ...parts.rest.filter(item => item.kind === 'brush')];
      assert.equal(broken.length, drawn.length, file);
      const pool = drawn.map(entry => entry.brush), same = (a, b) => a.vertices.length === b.vertices.length && a.vertices.every((v, j) => v.every((x, k) => Math.abs(x - b.vertices[j][k]) < 1e-3));
      for (const brush of broken) {
        const match = pool.findIndex(other => same(brush, other));
        assert.ok(match >= 0, `${file}: a broken brush flatten does not draw`);
        pool.splice(match, 1);
      }
      const placed = M.placement(map, entity), inverse = M.invert(placed.transform);
      const original = M.parts(placed.group.items);
      [...parts.world, ...parts.rest].forEach((item, i) => {
        const source = [...original.world, ...original.rest][i], back = M.moveItem(item, inverse, -placed.yaw);
        if (item.kind === 'brush') back.vertices.forEach((v, j) => v.forEach((x, k) => near(x, source.vertices[j][k], file)));
        else if (M.property(source, 'position')) M.property(back, 'position').forEach((x, k) => near(x, M.property(source, 'position')[k], file));
      });
    }
  }
  assert.ok(placements > 100, `${placements} placements`);
});

// ---- Play mode (movement.js) ----
const {ReflexMovement: P} = require(path.join(__dirname, '../static/reflex-maps/movement.js'));
const room = extra => P.createWorld([
  {brush: B.box([-1024, -64, -1024], [1024, 0, 1024], {}), tag: 'floor'},
  {brush: B.box([256, 0, -1024], [320, 512, 1024], {}), tag: 'wall'},
  ...extra.map(([min, max, tag]) => ({brush: B.box(min, max, {}), tag})),
]);
const run = (world, player, input, seconds) => { for (let t = 0; t < seconds; t += 1 / 125) P.move(player, {forward: 0, right: 0, jump: false, yaw: 0, ...input}, world, 1 / 125); return player; };
const spawn = (x, y, z) => ({origin: [x, y, z], velocity: [0, 0, 0], ground: null});

test('a player falls onto the floor and stands on it, the box 24 below its middle', () => {
  const world = room([]), player = run(world, spawn(0, 200, 0), {}, 2);
  // A box stops an eighth of a unit off what it meets (Quake 3's SURFACE_CLIP_EPSILON).
  assert.ok(Math.abs(player.origin[1] - 24.125) < .01, `stands at ${player.origin[1]}`);
  assert.ok(player.ground);
  assert.deepEqual(player.velocity.map(Math.round), [0, 0, 0]);
});

test('walking reaches 320 units a second, a wall stops it and running along a wall slides', () => {
  const world = room([]), player = run(world, spawn(0, 24, 0), {forward: 1, yaw: Math.PI / 2}, 1);
  // Yaw 90 looks along +x; the wall's face is at x 256, the box 15 wide.
  assert.ok(Math.abs(player.origin[0] - (256 - 15 - .125)) < .01, `stopped by the wall at ${player.origin[0]}`);
  const runner = run(world, spawn(0, 24, 0), {forward: 1, yaw: 0}, .5);
  near(Math.hypot(runner.velocity[0], runner.velocity[2]), 320, 'speed');
  // Into the wall at 45 degrees: the part along it carries on.
  const slider = run(world, spawn(200, 24, 0), {forward: 1, yaw: Math.PI / 4}, 1);
  assert.ok(slider.origin[0] < 241.5 && slider.origin[2] > 100, `slid to ${slider.origin}`);
});

test('a jump rises 270²/(2 × 800) units, and steps up to 18 units high are climbed, higher ones not', () => {
  const world = room([[[64, 0, -64], [256, 16, 64], 'step'], [[-128, 0, -64], [-64, 32, 64], 'ledge']]);
  const jumper = spawn(0, 24, -300);
  run(world, jumper, {}, .2);
  let top = 0;
  for (let t = 0; t < 1; t += 1 / 125) { P.move(jumper, {forward: 0, right: 0, jump: t === 0, yaw: 0}, world, 1 / 125); top = Math.max(top, jumper.origin[1]); }
  assert.ok(Math.abs(top - 24.125 - 270 * 270 / 1600) < 2, `jumped ${top - 24.125}`);
  const climber = run(world, spawn(0, 24, 0), {forward: 1, yaw: Math.PI / 2}, 1);
  assert.ok(Math.abs(climber.origin[1] - 40.125) < .01, `on the step at ${climber.origin[1]}`);
  const blocked = run(world, spawn(0, 24, 0), {forward: 1, yaw: -Math.PI / 2}, 1);
  assert.ok(Math.abs(blocked.origin[1] - 24.125) < .01, `not on the ledge: ${blocked.origin[1]}`);
  assert.ok(Math.abs(blocked.origin[0] - (-64 + 15.125)) < .01, `stopped by the ledge at ${blocked.origin[0]}`);
});

test('a trace reports what it hit; a box inside a brush starts solid; free finds room above', () => {
  const world = room([]);
  const hit = world.trace([0, 100, 0], [0, -100, 0]);
  assert.equal(hit.tag, 'floor');
  near(hit.end[1], 24.125, 'stops just above the floor');
  assert.ok(world.trace([0, 0, 0], [0, 0, 0]).startSolid);
  const room_ = P.free(world, [0, 0, 0]);
  assert.ok(room_[1] > 24 && room_[1] <= 32 && !world.trace(room_, room_).startSolid, `free at ${room_}`);
  // A jump pad's launch lands its player on the target at the top of the arc.
  const velocity = P.launch([0, 24, 0], [400, 280, 0]), time = velocity[1] / P.GRAVITY;
  near(velocity[0] * time, 400, 'across'); near(velocity[1] * time - P.GRAVITY * time * time / 2, 256, 'up');
});

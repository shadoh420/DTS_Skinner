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

test('texture coordinates follow the axes, offsets and rotation measured in the game', () => {
  const box = B.box([0, 0, 0], [64, 32, 16], {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0});
  // The face of the box looking along `normal`, with `fields`; [u, v] at its corner `at`.
  const faceAlong = normal => box.faces.find(face => face.indices.every(i => box.vertices[i][normal.findIndex(x => x)] === (normal.some(x => x > 0) ? [64, 32, 16][normal.findIndex(x => x)] : 0)));
  const at = (normal, corner, fields = {}) => {
    const face = faceAlong(normal), brush = {vertices: box.vertices, faces: box.faces.map(f => f === face ? {...f, ...fields} : f)};
    const index = box.vertices.findIndex(v => v.every((x, k) => x === corner[k]));
    return B.texcoords(brush, brush.faces[box.faces.indexOf(face)])[face.indices.indexOf(index)];
  };
  // Which way u and v grow on each face (the report's faces test map): [normal, U, V].
  const measured = [[[0, 0, -1], [0, 1, 0], [1, 0, 0]], [[0, 0, 1], [1, 0, 0], [0, 1, 0]], [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    [[-1, 0, 0], [0, -1, 0], [0, 0, 1]], [[0, 1, 0], [-1, 0, 0], [0, 0, 1]], [[0, -1, 0], [1, 0, 0], [0, 0, 1]]];
  for (const [normal, U, V] of measured) {
    const corners = box.vertices.filter(v => faceAlong(normal).indices.some(i => box.vertices[i] === v));
    for (const p of corners) {
      const [u, v] = at(normal, p);
      near(u, U[0] * p[0] + U[1] * p[1] + U[2] * p[2]);
      near(v, V[0] * p[0] + V[1] * p[1] + V[2] * p[2]);
    }
  }
  // Offsets are 1/16 unit and added before the scale (64 moves the texture 4 units whatever the scale, as in the game);
  // a scale divides; rotation +90 on the −z face turns U from +y to +x.
  assert.deepEqual(at([0, 0, -1], [64, 32, 0], {u: 16, v: -32}), [33, 62]);
  assert.deepEqual(at([0, 0, -1], [64, 32, 0], {scaleU: 2, scaleV: -1}), [16, -64]);
  assert.deepEqual(at([0, 0, -1], [64, 32, 0], {u: 64, scaleU: 2, v: 64, scaleV: -1}), [18, -68]);
  // U follows the face's first edge, so a box face turned any way keeps u growing up its side. (An earlier reading had
  // U flip from +y to −y at 45° by the axis the normal is nearest; coplanar plates listed from different corners
  // showed the first edge sets it, 2026-10-18, and the tilted test maps behind that reading are gone.)
  const uGrowsUp = degrees => {
    const t = degrees * Math.PI / 180, turn = ([x, y, z]) => [x * Math.cos(t) - z * Math.sin(t), y, x * Math.sin(t) + z * Math.cos(t)];
    const cube = B.box([-8, -8, -8], [8, 8, 8], {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0}), brush = {vertices: cube.vertices.map(turn), faces: cube.faces};
    const face = brush.faces.find(f => B.normalize(B.newell(f.indices.map(i => brush.vertices[i])))[2] < -.6);
    const coords = B.texcoords(brush, face), ys = face.indices.map(i => brush.vertices[i][1]);
    return coords[ys.indexOf(Math.max(...ys))][0] > coords[ys.indexOf(Math.min(...ys))][0];
  };
  assert.deepEqual([uGrowsUp(-44), uGrowsUp(-46), uGrowsUp(46)], [true, true, true]);
  // The −z face listed from its second corner starts along +x: u and v trade places, as on the game's seam pairs.
  const shifted = {...box.faces[0], indices: [...box.faces[0].indices.slice(1), box.faces[0].indices[0]]};
  B.texcoords(box, shifted).forEach(([u, v], k) => { const p = box.vertices[shifted.indices[k]]; near(u, p[0]); near(v, -p[1]); });
  // Extruded side faces start along the extrusion, as B.box's walls start up them.
  const prism = B.extrude([[0, 0, 0], [64, 0, 0], [0, 0, 64]], [0, 96, 0]);
  for (const face of prism.faces.filter(f => f.indices.length === 4)) assert.deepEqual(B.normalize(B.firstEdge(face.indices.map(i => prism.vertices[i]))), [0, 1, 0]);
  const [u, v] = at([0, 0, -1], [64, 32, 0], {rotation: 90});
  near(u, 64); near(v, -32);
});

test("a volume's texture stands upright on every wall, as the game's editor draws it", () => {
  const box = B.box([0, 0, 0], [64, 32, 16], {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0});
  // [normal, U, V]: U = n × +y and V = n × U (measured on the +z and +x faces), so v grows downward everywhere.
  for (const [normal, U, V] of [[[0, 0, 1], [-1, 0, 0], [0, -1, 0]], [[1, 0, 0], [0, 0, 1], [0, -1, 0]], [[0, 0, -1], [1, 0, 0], [0, -1, 0]], [[-1, 0, 0], [0, 0, -1], [0, -1, 0]]]) {
    const axis = normal.findIndex(x => x), face = box.faces.find(f => f.indices.every(i => box.vertices[i][axis] === (normal[axis] > 0 ? [64, 32, 16][axis] : 0)));
    B.texcoords(box, face, true).forEach(([u, v], k) => {
      const p = box.vertices[face.indices[k]];
      near(u, U[0] * p[0] + U[1] * p[1] + U[2] * p[2]);
      near(v, V[0] * p[0] + V[1] * p[1] + V[2] * p[2]);
    });
  }
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

test('mirroring a brush keeps it convex and wound outward, and mirrors an entity place and yaw', () => {
  const wedge = B.rebuild({vertices: [[0, 0, 0], [64, 0, 0], [0, 0, 32], [64, 0, 32], [0, 48, 0], [0, 48, 32]],
    faces: [[0, 1, 3, 2], [0, 2, 5, 4], [0, 4, 1], [2, 3, 5], [1, 4, 5, 3]].map(indices => ({indices, material: 'm'}))});
  assertConvexSolid(B.rebuild(wedge));
  const mirrored = M.mirrorItem({kind: 'brush', ...wedge}, 0, 64);
  assertConvexSolid(mirrored);
  assert.deepEqual(B.bounds(mirrored), {min: [64, 0, 0], max: [128, 48, 32]});
  near(B.volume(mirrored), B.volume(wedge));
  const spawn = {kind: 'entity', type: 'PlayerSpawn', properties: [{type: 'Vector3', name: 'position', value: [16, 0, 8]}, {type: 'Vector3', name: 'angles', value: [30, 0, 0]}]};
  const across = (axis, centre) => { const e = M.mirrorItem(spawn, axis, centre); return [M.property(e, 'position'), M.property(e, 'angles')[0]]; };
  assert.deepEqual(across(0, 0), [[-16, 0, 8], 330]);
  assert.deepEqual(across(2, 0), [[16, 0, -8], 150]);
  assert.deepEqual(across(1, 8), [[16, 16, 8], 30]);
});

const {ReflexLight: L} = require(path.join(__dirname, '../static/reflex-maps/light.js'));

// A .light file of two probes (2 × 1 × 1, 64 apart) and one 4 × 4 reflection probe, as the editor writes one.
function lightFile() {
  const halves = values => values.map(v => v === 1 ? 0x3c00 : v === .5 ? 0x3800 : 0);
  const count = 2, header = Buffer.alloc(60);
  header.writeUInt16LE(2, 0); header.writeUInt16LE(0x1323, 2); header.writeFloatLE(64, 4);
  [2, 1, 1].forEach((n, i) => header.writeUInt32LE(n, 8 + i * 4));
  [-32, 0, 0].forEach((v, i) => header.writeFloatLE(v, 24 + i * 4));
  [1 / 128, 1 / 64, 1 / 64].forEach((v, i) => header.writeFloatLE(v, 36 + i * 4));
  [.5, .5, .5].forEach((v, i) => header.writeFloatLE(v, 48 + i * 4));
  // Plane cAr: probe 0 sends red 1 from every way (the constant term), probe 1 red 0.5 from +x only.
  const planes = [];
  for (let plane = 0; plane < 7; plane++) {
    const data = Buffer.alloc(count * 8);
    if (plane === 0) halves([0, 0, 0, 1, .5, 0, 0, 0]).forEach((h, i) => data.writeUInt16LE(h, i * 2));
    planes.push(data);
  }
  const cubes = Buffer.alloc(16 + 13 + 6 * 8);
  cubes.writeUInt16LE(2, 0); cubes.writeUInt16LE(0x1324, 2); cubes.writeUInt32LE(1, 4); cubes.writeUInt32LE(4, 8); cubes.writeUInt32LE(1, 12);
  [0, 64, 0].forEach((v, i) => cubes.writeFloatLE(v, 16 + i * 4));
  cubes[28] = 1;
  for (let face = 0; face < 6; face++) cubes.writeUInt16LE(0xf800, 29 + face * 8);  // BC1: colour 0 pure red, every texel colour 0
  return Buffer.concat([header, ...planes, Buffer.from([1, 1]), cubes]);
}

test('a .light file gives its probe grid, the light each probe sends a surface, and its reflection probes', () => {
  const light = L.parse(lightFile());
  assert.deepEqual([light.size, light.origin, light.cell], [[2, 1, 1], [-32, 0, 0], 64]);
  assert.equal(light.planes.length, 7);
  assert.deepEqual([...light.indices], [1, 1]);
  assert.deepEqual(L.irradiance(light, 0, [0, 1, 0]), [1, 0, 0]);
  assert.deepEqual(L.irradiance(light, 1, [1, 0, 0]), [.5, 0, 0]);
  assert.deepEqual(L.irradiance(light, 1, [-1, 0, 0]), [-.5, 0, 0]);
  assert.equal(light.cubes.length, 1);
  assert.deepEqual(light.cubes[0].position, [0, 64, 0]);
  assert.deepEqual([...L.decodeBC1(light.cubes[0].faces[2][0].data, 4).slice(0, 4)], [255, 0, 0, 255]);
  assert.throws(() => L.parse(Buffer.alloc(80)), /not a Reflex light file/);
});

test('the light grid lists in each cell the lights that reach it, and packs a spot light\'s cone', () => {
  const grid = L.lightGrid([{position: [0, 0, 0], colour: [1, 1, 1], near: 0, far: 100},
    {position: [1000, 0, 0], colour: [1, 0, 0], near: 16, far: 64, direction: [0, -1, 0], cosInner: Math.cos(.1), cosOuter: Math.cos(.5),
      shadow: {tile: 3, texel: .01, matrix: Array.from({length: 16}, (_, k) => k + 1)}}]);
  const cellOf = p => p.map((v, k) => Math.floor((v - grid.origin[k]) / grid.cell));
  const listed = p => { const [x, y, z] = cellOf(p), i = x + grid.size[0] * (y + grid.size[1] * z); return [...grid.list.slice(grid.cells[i * 2], grid.cells[i * 2] + grid.cells[i * 2 + 1])]; };
  assert.deepEqual(listed([0, 0, 0]), [0]);
  assert.deepEqual(listed([1000, 0, 0]), [1]);
  assert.deepEqual(listed([500, 0, 0]), []);
  assert.deepEqual([...grid.data.slice(32, 44)].map(v => Math.round(v * 1000) / 1000), [1000, 0, 0, 16, 1, 0, 0, 64, 0, -1, 0, Math.round(Math.cos(.5) * 1000) / 1000]);
  assert.equal(grid.data[45], 1);  // a spot
  // Its shadow: the atlas tile, the texel size, then its matrix; a light without one has tile -1.
  assert.deepEqual([...grid.data.slice(46, 64)].map(v => Math.round(v * 1000) / 1000), [3, .01, ...Array.from({length: 16}, (_, k) => k + 1)]);
  assert.equal(grid.data[14], -1);
});

test('flatten gives each entity the transform of the prefab placement it is in', () => {
  const map = M.parse(['reflex map version 8', 'prefab lamp', '\tentity', '\t\ttype WorldSpawn', '\tentity', '\t\ttype Effect',
    '\t\tVector3 position 8.000000 0.000000 0.000000', 'global', '\tentity', '\t\ttype WorldSpawn', '\tentity', '\t\ttype Prefab',
    '\t\tVector3 position 0.000000 64.000000 0.000000', '\t\tVector3 angles 90.000000 0.000000 0.000000', '\t\tString64 prefabName lamp', ''].join('\r\n'));
  const effect = M.flatten(map).entities.find(entry => entry.entity.type === 'Effect');
  assert.deepEqual(effect.position.map(v => Math.round(v)), [0, 64, -8]);
  assert.deepEqual(M.apply(effect.transform, [8, 0, 0]).map(v => Math.round(v)), [0, 64, -8]);
  assert.equal(M.flatten(map).entities[0].transform, null);
});

test('whole numbers go into half-float bits and come back exactly (probe indices ride in a half-float texture)', () => {
  for (const n of [0, 1, 2, 3, 49, 128, 255, 2048]) assert.equal(L.half(L.toHalf(n)), n);
});

const {ReflexMotion: Motion} = require(path.join(__dirname, '../static/reflex-maps/motion.js'));

test('a looping animation poses its bones between frames, through their parents and binds, and wraps to the first frame', () => {
  // Two bones, the second a child of the first and 10 along its x; two frames 1 s apart (fps 1): the root turns a
  // quarter about y (+x toward −z, row vectors) between them; binds are identity.
  const rest = [0, 0, 0, 0, 0, 0, 1, 1, 1, 1], turned = [0, 0, 0, 0, Math.SQRT1_2, 0, Math.SQRT1_2, 1, 1, 1], child = [10, 0, 0, 0, 0, 0, 1, 1, 1, 1];
  const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const animation = {bones: 2, frames: 2, fps: 1, parents: [-1, 0], binds: [...identity, ...identity], records: [...rest, ...turned, ...child, ...child]};
  const place = (m, p) => [0, 1, 2].map(c => p[0] * m[c] + p[1] * m[4 + c] + p[2] * m[8 + c] + m[12 + c]);
  const round = v => v.map(c => Math.round(c * 1000) / 1000 + 0);
  // At rest the child's origin is 10 along x; a quarter turned, it is at z −10; half way, an eighth.
  assert.deepEqual(round(place(Motion.skinMatrices(animation, 0)[1], [0, 0, 0])), [10, 0, 0]);
  assert.deepEqual(round(place(Motion.skinMatrices(animation, 1)[1], [0, 0, 0])), [0, 0, -10]);
  assert.deepEqual(round(place(Motion.skinMatrices(animation, .5)[1], [0, 0, 0])), round([10 * Math.SQRT1_2, 0, -10 * Math.SQRT1_2]));
  // After the last frame comes the first again: 1.5 s is half way back.
  assert.deepEqual(round(place(Motion.skinMatrices(animation, 1.5)[0], [1, 0, 0])), round([Math.SQRT1_2, 0, -Math.SQRT1_2]));
  // A bind moves the vertex into the bone's frame first.
  const bound = {...animation, binds: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -10, 0, 0, 1, ...identity]};
  assert.deepEqual(round(place(Motion.skinMatrices(bound, 0)[0], [10, 0, 0])), [0, 0, 0]);
});

test('a particle lives its life from its slot offset round its emitter period, and is gone until the period ends', () => {
  assert.equal(Motion.particleAge(0, .5, 2, 1.5), .5);
  assert.equal(Motion.particleAge(1.2, .5, 2, 1.5), null);  // age 1.7: past its life
  assert.equal(Math.round(Motion.particleAge(2.25, .5, 2, 1.5) * 100) / 100, .75);  // round again
});

test("a model's animation block gives each part it moves its weights, bones and vertices before posing", () => {
  // As tools/reflex_models.py write_mesh writes it: 'ANIM', one bone, two frames at 30 a second, parent -1, an identity
  // bind, two records, then two moving parts (numbers 2 and 0) of one and two vertices.
  const floats = values => Buffer.from(new Float32Array(values).buffer), ints = values => Buffer.from(new Int32Array(values).buffer);
  const vertex = bone => Buffer.concat([floats([1, 0, 0, 0]), Buffer.from([bone, 0, 0, 0])]), place = xs => floats(xs.flatMap(x => [x, 0, 0]));
  const buffer = Buffer.concat([Buffer.from('ANIM'), ints([1, 2]), floats([30]), ints([-1]), floats([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]),
    floats([0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 5, 0, 0, 0, 0, 0, 1, 1, 1, 1]), ints([2, 2]), vertex(0), place([7]), place([0]),
    ints([0]), vertex(0), vertex(0), place([3, 4]), place([0, 1])]);
  const array = buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.length);
  const parts = [{positions: new Float32Array(6)}, {positions: new Float32Array(3)}, {positions: new Float32Array(3)}];
  const animation = Motion.readAnimation(array, 0, parts);
  assert.deepEqual([animation.bones, animation.frames, animation.fps, animation.records[10]], [1, 2, 30, 5]);
  assert.deepEqual([...parts[2].moving.positions], [7, 0, 0]);
  assert.deepEqual([...parts[0].moving.positions], [3, 0, 0, 4, 0, 0]);
  assert.deepEqual([...parts[0].moving.weights.slice(0, 4)], [1, 0, 0, 0]);
  assert.equal(parts[1].moving, undefined);
  assert.equal(Motion.readAnimation(new ArrayBuffer(8), 0, parts), null);
});

// ---- Texture lock, subtract's merge and the hidden faces an OBJ export drops ----
const textured = {u: 12, v: -40, scaleU: .5, scaleV: 2, rotation: 15, material: 'wall'};
// p' = R p + t, R the turn by `degrees` about the unit `axis` (Rodrigues).
function turned(brush, axis, degrees, about = [0, 0, 0]) {
  const k = axis.map(x => x / Math.hypot(...axis)), a = degrees * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
  const turn = p => {
    const kp = k[0] * p[0] + k[1] * p[1] + k[2] * p[2], cr = [k[1] * p[2] - k[2] * p[1], k[2] * p[0] - k[0] * p[2], k[0] * p[1] - k[1] * p[0]];
    return p.map((x, i) => x * c + cr[i] * s + k[i] * kp * (1 - c));
  };
  return {vertices: brush.vertices.map(v => turn(v.map((x, i) => x - about[i])).map((x, i) => x + about[i])), faces: brush.faces.map(f => ({...f, indices: [...f.indices]}))};
}
function assertSameTexcoords(before, after, message) {
  before.faces.forEach((face, f) => B.texcoords(before, face).forEach(([u, v], k) => {
    const [u2, v2] = B.texcoords(after, after.faces[f])[k];
    near(u2, u, `${message} face ${f} corner ${k} u`); near(v2, v, `${message} face ${f} corner ${k} v`);
  }));
}

test('texture lock keeps every face texture where it was as a brush moves and turns', () => {
  const box = B.box([-24, 8, 40], [40, 72, 96], textured);
  const moves = [
    ['moved', B.translate(box, [37, 5, -11])],
    ['yawed 90°', turned(box, [0, 1, 0], 90, [8, 40, 68])],  // The nearest axis of each wall changes: U0 jumps.
    ['yawed 30°', turned(box, [0, 1, 0], 30, [100, 0, -50])],
    ['turned 50° about a slant axis', turned(B.translate(box, [3, 3, 3]), [1, 2, 3], 50)],
  ];
  for (const [label, after] of moves) {
    assert.ok(B.texcoords(after, after.faces[0]).some(([u], k) => Math.abs(u - B.texcoords(box, box.faces[0])[k][0]) > 1), `${label}: unlocked swims`);
    const locked = B.lock(box, after);
    assertSameTexcoords(box, locked, label);
    for (const face of locked.faces) assert.ok(face.scaleU === .5 && face.scaleV === 2 && face.rotation >= 0 && face.rotation < 360, label);
  }
});

test('texture lock: a clip keeps the texture of the faces it keeps, and a dragged corner pins the rest of the face', () => {
  const box = B.box([0, 0, 0], [64, 64, 64], textured);
  const [clipped] = B.clip(box, [0, 48, 0], [64, 48, 0], [0, 32, 64], textured);
  // Each corner the clipped brush shares with the box has the texture coordinates it had on the box's face on that plane.
  const key = plane => plane.normal.map(n => Math.round(n * 1e6)).join();
  const onBox = new Map(B.planesOf(box).map(plane => [key(plane), plane.face]));
  let shared = 0;
  for (const plane of B.planesOf(clipped)) {
    const original = onBox.get(key(plane));
    if (!original) continue;  // The cut face.
    const now = B.texcoords(clipped, plane.face), before = B.texcoords(box, original);
    plane.face.indices.forEach((index, k) => {
      const at = original.indices.findIndex(i => box.vertices[i].every((x, n) => x === clipped.vertices[index][n]));
      if (at >= 0) { shared++; near(now[k][0], before[at][0]); near(now[k][1], before[at][1]); }
    });
  }
  assert.ok(shared >= 12, `${shared} shared corners`);
  // Corner 6 (64, 64, 64) up 16: the faces round it bend; their other corners keep their texture coordinates, to the
  // foreshortening of the bend (a fraction of a unit).
  const after = {vertices: box.vertices.map((v, i) => i === 6 ? [64, 80, 64] : v), faces: box.faces};
  const locked = B.lock(box, after), close = (a, b, message) => assert.ok(Math.abs(a - b) < .5, `${message} ${a} ≠ ${b}`);
  box.faces.forEach((face, f) => {
    const before = B.texcoords(box, face), now = B.texcoords(locked, locked.faces[f]);
    face.indices.forEach((index, k) => { if (index !== 6) { close(now[k][0], before[k][0], `face ${f} u`); close(now[k][1], before[k][1], `face ${f} v`); } });
    if (!face.indices.includes(6)) assert.equal(locked.faces[f], after.faces[f]);
  });
});

test('mergeAll joins the pieces subtract leaves where they make one convex brush that looks the same', () => {
  const block = B.box([0, 0, 0], [128, 64, 64], wall);
  let pieces = [block];
  // Two cutters, the second wider: it cuts each strip the first left into three, and the pieces at the far end join again.
  for (const cut of [B.box([16, 16, 48], [64, 48, 80], cutter), B.box([48, 8, 48], [112, 56, 80], cutter)]) pieces = pieces.flatMap(p => B.subtract(p, cut));
  const merged = B.mergeAll(pieces);
  assert.deepEqual([pieces.length, merged.length], [9, 7]);
  near(totalVolume(merged), totalVolume(pieces));
  merged.forEach(assertConvexSolid);
  assertDisjoint(merged);
  // Side by side: one brush, unless the faces on the shared top differ.
  assert.equal(B.mergeAll([B.box([0, 0, 0], [32, 32, 32], wall), B.box([32, 0, 0], [64, 32, 32], wall)]).length, 1);
  const other = B.box([32, 0, 0], [64, 32, 32], wall);
  other.faces[1] = {...other.faces[1], material: 'other'};
  assert.equal(B.mergeAll([B.box([0, 0, 0], [32, 32, 32], wall), other]).length, 2);
});

test('exposed drops what solid brushes bury: faces pressed together, the earlier of two flush faces, all of a brush inside', () => {
  const faceArea = pieces => pieces.reduce((sum, p) => sum + Math.hypot(...B.newell(p)) / 2, 0);
  const areas = (brushes, solid = brushes.map(() => true)) => B.exposed(brushes, solid).map(faces => faces.map(faceArea));
  const below = B.box([0, 0, 0], [64, 64, 32], wall);  // Face 1 is its top (z = 32), face 0 its bottom.
  // Stacked exactly: the top of one and the bottom of the other go.
  let result = areas([below, B.box([0, 0, 32], [64, 64, 64], wall)]);
  near(result[0][1], 0); near(result[1][0], 0); near(result[0][0], 64 * 64); near(result[0][2], 64 * 32);
  // Overhang: the lower top keeps its ring.
  result = areas([below, B.box([16, 16, 32], [48, 48, 64], wall)]);
  near(result[0][1], 64 * 64 - 32 * 32); near(result[1][0], 0);
  // Overlapping with flush tops: the overlap's top is drawn once, by the later brush.
  result = areas([below, B.box([32, 0, 0], [96, 64, 32], wall)]);
  near(result[0][1] + result[1][1], 96 * 64); near(result[1][1], 64 * 64);
  // Side by side: the faces pressed together go, and the tops a neighbour only touches stay one polygon each.
  const pieces = B.exposed([below, B.box([64, 0, 0], [128, 64, 32], wall)], [true, true]);
  assert.deepEqual(pieces.map(faces => faces[1].length), [1, 1]);
  assert.equal(pieces[0][4].length + pieces[1][5].length, 0);
  // A box inside a box is gone; a glass (not solid) box hides nothing.
  const inner = B.box([16, 16, 8], [48, 48, 24], wall);
  assert.ok(areas([below, inner])[1].every(a => a < 1e-6));
  near(areas([below, inner], [true, false])[0].reduce((a, b) => a + b, 0), 2 * 64 * 64 + 4 * 64 * 32);
});

test('buried lists the faces solid brushes bury wholly, not those they only partly cover', () => {
  const floor = B.box([0, 0, 0], [128, 128, 16], wall), crate = B.box([32, 32, 16], [64, 64, 48], wall), beside = B.box([64, 32, 16], [96, 64, 48], wall);
  // The crates' bottoms lie on the floor and their shared sides press together; the floor's top is only partly covered.
  assert.deepEqual(B.buried([floor, crate, beside], [true, true, true]), [[], [0, 4], [0, 5]]);
  // Glass hides nothing; `only` leaves the other brushes uncut but still hiding.
  assert.deepEqual(B.buried([floor, crate, beside], [true, true, false]), [[], [0], [0, 5]]);
  assert.deepEqual(B.buried([floor, crate, beside], [true, true, true], new Set([2])), [[], [], [0, 5]]);
});

test('validate gives fixed codes: too few faces, a face with no normal, duplicate planes, planes enclosing nothing', () => {
  const box = B.box([0, 0, 0], [64, 64, 64], wall), codes = brush => B.validate(brush).map(issue => issue.code);
  assert.deepEqual(B.validate(box), []);
  assert.deepEqual(B.validate({...box, faces: [...box.faces, {...box.faces[1]}]}), [{code: 'duplicate-plane', face: 6}]);
  assert.deepEqual(B.validate({...box, faces: box.faces.map((face, i) => i === 2 ? {...face, indices: face.indices.slice(0, 2)} : face)}), [{code: 'zero-normal', face: 2}]);
  assert.deepEqual(codes({...box, faces: box.faces.slice(0, 3)}), ['too-few-faces']);
  // Two squares crossing at the middle, each face both ways round: x ≤ 0, x ≥ 0, y ≤ 0, y ≥ 0 bound only a line.
  const cross = {vertices: [[0, -1, -1], [0, 1, -1], [0, 1, 1], [0, -1, 1], [-1, 0, -1], [1, 0, -1], [1, 0, 1], [-1, 0, 1]],
    faces: [[0, 1, 2, 3], [3, 2, 1, 0], [4, 5, 6, 7], [7, 6, 5, 4]].map(indices => ({...wall, indices}))};
  assert.deepEqual(codes(cross), ['encloses-nothing']);
  assert.ok(B.FATAL.has('encloses-nothing') && B.FATAL.has('too-few-faces') && !B.FATAL.has('duplicate-plane') && !B.FATAL.has('zero-normal'));
});

test('bevel cuts an edge back by the width along both faces, in flat steps on the arc between them', () => {
  const box = B.box([0, 0, 0], [64, 64, 64], textured), edge = [[64, 0, 64], [64, 64, 64]];  // Where +x meets +z.
  const chamfer = B.bevel(box, [edge], 16);
  assertConvexSolid(chamfer);
  assert.equal(chamfer.faces.length, 7);
  near(B.volume(chamfer), 64 ** 3 - 64 * 16 * 16 / 2);
  // Four steps: the corner square less the polygon on the quarter circle of radius 16.
  const round = B.bevel(box, [edge], 16, 4);
  assertConvexSolid(round);
  assert.equal(round.faces.length, 10);
  near(B.volume(round), 64 ** 3 - 64 * (256 - 2 * 256 * Math.sin(Math.PI / 8)));
  // Two edges at once, across a corner; an edge the brush does not have, or no width, gives null.
  assertConvexSolid(B.bevel(box, [edge, [[0, 0, 64], [64, 0, 64]]], 8, 2));
  assert.equal(B.bevel(box, [[[0, 0, 0], [64, 64, 64]]], 8), null);
  assert.equal(B.bevel(box, [edge], 0), null);
});

test('bevel works on a slanted edge, and the new faces take the texture on from the faces they meet', () => {
  // A wedge: its slope meets the floor at 30°.
  const wedge = B.hull([[0, 0, 0], [96, 0, 0], [0, 64, 0], [96, 64, 0], [0, 0, 96 * Math.tan(Math.PI / 6)], [0, 64, 96 * Math.tan(Math.PI / 6)]], () => textured);
  const edge = [[96, 0, 0], [96, 64, 0]], result = B.bevel(wedge, [edge], 12, 3);
  assertConvexSolid(result);
  assert.equal(result.faces.length, wedge.faces.length + 3);
  // Nothing within 12 of the edge along the floor or along the slope is left.
  const sx = Math.cos(Math.PI / 6);
  assert.ok(result.vertices.every(v => v[0] <= 96 - 12 + 1e-3 || v[2] > 1e-3), 'floor cut back 12');
  near(Math.max(...result.vertices.filter(v => v[2] < 1e-3).map(v => v[0])), 84, 'floor ends 12 short');
  near(Math.min(...result.vertices.filter(v => v[2] > 1e-3 && Math.abs(v[2] - (96 - v[0]) * Math.tan(Math.PI / 6)) < 1e-3).map(v => (96 - v[0]) / sx)), 12, 'slope ends 12 short');
  // Every corner a new face shares with the floor or the slope has the texture coordinates it has on that face (the
  // end faces, square to the seam, are not continued).
  const kept = result.faces.filter(face => B.planesOf(wedge).some(p => face.indices.every(i => Math.abs(p.normal.reduce((s, n, k) => s + n * result.vertices[i][k], 0) - p.distance) < 1e-3)));
  const fresh = result.faces.filter(face => !kept.includes(face));
  assert.equal(fresh.length, 3);
  const ends = new Set(B.planesOf(result).filter(p => Math.abs(p.normal[1]) > .5).map(p => p.face));
  let seams = 0;
  for (const face of fresh) {
    const uv = B.texcoords(result, face);
    for (const old of kept.filter(old => !ends.has(old))) {
      const olduv = B.texcoords(result, old);
      face.indices.forEach((index, k) => {
        const at = old.indices.indexOf(index);
        if (at >= 0) { seams++; near(uv[k][0], olduv[at][0], 'u at the seam'); near(uv[k][1], olduv[at][1], 'v at the seam'); }
      });
    }
  }
  assert.ok(seams >= 4, `${seams} seam corners`);
});

test('mergeAll without looks joins a cutter of several brushes into the one convex brush they make', () => {
  const left = B.box([0, 0, 0], [32, 32, 32], wall), right = B.box([32, 0, 0], [64, 32, 32], {...wall, u: 8});
  assert.equal(B.mergeAll([left, right]).length, 2);
  const [one] = B.mergeAll([left, right], false);
  near(B.volume(one), 64 * 32 * 32);
  assert.equal(B.mergeAll([left, B.box([40, 0, 0], [64, 32, 32], wall)], false).length, 2, 'a gap is not filled');
  // A door cutter in three layers through a wall, each with its own texture offset: carved layer by layer the inner
  // layers' faces cut the wall and the pieces cannot be merged back (7); joined first, a door's 3.
  const slab = B.box([-256, -16, 0], [256, 16, 384], wall);
  const layers = [[-32, -4], [-4, 4], [4, 32]].map(([a, b], i) => B.box([-48, a, -16], [48, b, 192], {...cutter, u: i * 3}));
  const carve = cutters => B.mergeAll(cutters.reduce((pieces, c) => pieces.flatMap(p => B.subtract(p, c)), [slab]));
  assert.deepEqual([carve(layers).length, carve(B.mergeAll(layers, false)).length], [7, 3]);
  near(totalVolume(carve(B.mergeAll(layers, false))), B.volume(slab) - 96 * 32 * 192);
});

// ---- Shapes that remember their numbers, and texture wrap ----
function assertSoundShape(brushes, count) {
  assert.ok(brushes, 'made');
  assert.equal(brushes.length, count);
  for (const brush of brushes) {
    assertConvexSolid(brush);
    assert.ok(B.validate(brush).every(issue => !B.FATAL.has(issue.code)), 'no fatal issue');
  }
  assertDisjoint(brushes);
}
const extentOf = brushes => {
  const all = brushes.map(B.bounds);
  return {min: [0, 1, 2].map(k => Math.min(...all.map(b => b.min[k]))), max: [0, 1, 2].map(k => Math.max(...all.map(b => b.max[k])))};
};
const sharedCorners = (a, b) => a.vertices.filter(v => b.vertices.some(w => w.every((x, k) => x === v[k]))).length;

test('stairs are one solid box per step, each a rise higher and a run further on', () => {
  const steps = B.stairs({steps: 6, rise: 16, run: 32, width: 128}, wall);
  assertSoundShape(steps, 6);
  near(totalVolume(steps), 128 * 32 * 16 * 6 * 7 / 2);
  assert.deepEqual(extentOf(steps), {min: [-64, 0, 0], max: [64, 96, 192]});
  steps.forEach((step, i) => assert.deepEqual(B.bounds(step), {min: [-64, 0, 32 * i], max: [64, 16 * (i + 1), 32 * (i + 1)]}));
  assert.equal(B.stairs({steps: 0, rise: 16, run: 32, width: 128}), null);
});

test('an arch and a ring of wedges are convex pieces sharing their corners, of the volume their chords bound', () => {
  const arch = B.arch({span: 256, thickness: 32, depth: 24, segments: 8}, wall);
  assertSoundShape(arch, 8);
  near(totalVolume(arch) / 1e3, 8 * .5 * (128 ** 2 - 96 ** 2) * Math.sin(Math.PI / 8) * 24 / 1e3);
  assert.deepEqual(extentOf(arch), {min: [-128, 0, -12], max: [128, 128, 12]});
  for (let i = 0; i + 1 < arch.length; i++) assert.equal(sharedCorners(arch[i], arch[i + 1]), 4, `pieces ${i} and ${i + 1} meet face to face`);
  // A full ring of wedges meeting on the axis is a round pillar.
  const pillar = B.ring({axis: 1, inner: 0, outer: 64, from: 0, to: 128, sides: 16, degrees: 360}, wall);
  assertSoundShape(pillar, 16);
  near(totalVolume(pillar) / 1e3, 16 * .5 * 64 ** 2 * Math.sin(Math.PI / 8) * 128 / 1e3, 'corners kept to six places');
  assert.equal(sharedCorners(pillar[15], pillar[0]), 4, 'the ring closes');
  assert.equal(B.arch({span: 64, thickness: 40, depth: 8, segments: 4}), null, 'thicker than the radius');
  assert.equal(B.arch({span: 64, thickness: 8, depth: 8, segments: 1}), null, 'a half turn in one piece is not convex');
});

test('a door arch is two legs and a half circle on them, filling its box but the doorway', () => {
  const door = B.doorArch({width: 128, height: 192, thickness: 16, depth: 32, segments: 8}, wall);
  assertSoundShape(door, 10);
  assert.deepEqual(extentOf(door), {min: [-64, 0, -16], max: [64, 192, 16]});
  near(totalVolume(door), 2 * 16 * 128 * 32 + 8 * .5 * (64 ** 2 - 48 ** 2) * Math.sin(Math.PI / 8) * 32);
  assert.equal(sharedCorners(door[1], door[2]), 4, 'a leg carries the end piece of the arch');
  assert.equal(B.doorArch({width: 128, height: 32, thickness: 16, depth: 32, segments: 8}), null, 'lower than its half width');
});

test('a curved hallway is a floor, a ceiling and two walls per segment, none overlapping', () => {
  const hall = B.hallway({radius: 256, width: 128, wall: 16, floor: 16, height: 160, segments: 6, degrees: 90}, wall);
  assertSoundShape(hall, 24);
  const e = extentOf(hall);
  assert.deepEqual([e.min[1], e.max[1]], [0, 160]);
  near(e.max[0], 400); near(e.max[2], 400);  // The outer wall's outside, 256 + 128 + 16 out along +x and +z.
  assert.equal(B.hallway({radius: 256, width: 128, wall: 16, floor: 80, height: 160, segments: 6, degrees: 90}), null, 'floor and ceiling meet');
});

test('a radial array turns copies about the vertical as a yaw does, the originals counted but not returned', () => {
  const pillar = B.box([100, 0, -16], [132, 32, 16], wall), copies = B.radial([pillar], [0, 0, 0], 4, 90);
  assert.equal(copies.length, 3);
  assert.deepEqual(B.bounds(copies[0]), {min: [-16, 0, -132], max: [16, 32, -100]});
  assert.deepEqual(copies[0].vertices, M.moveItem({kind: 'brush', ...pillar}, M.compose([0, 0, 0], [90, 0, 0])).vertices);
  for (const copy of copies) { assertConvexSolid(copy); near(B.volume(copy), B.volume(pillar)); }
  assert.deepEqual(B.bounds(B.radial([pillar], [100, 0, 0], 2, 180)[0]), {min: [68, 0, -16], max: [100, 32, 16]});
});

// Every two faces `wanted` picks that meet along an edge (two corners at the same places), as the pairs of texture
// coordinates the two faces give their shared corners.
function seams(brushes, wanted) {
  const faces = [];
  brushes.forEach(brush => brush.faces.forEach(face => { if (wanted(brush, face)) faces.push({points: face.indices.map(i => brush.vertices[i]), uv: B.texcoords(brush, face)}); }));
  const out = [];
  for (let i = 0; i < faces.length; i++) for (let j = i + 1; j < faces.length; j++) {
    const shared = [];
    faces[i].points.forEach((p, k) => { const at = faces[j].points.findIndex(q => q.every((x, n) => Math.abs(x - p[n]) < 1e-6)); if (at >= 0) shared.push([faces[i].uv[k], faces[j].uv[at]]); });
    if (shared.length >= 2) out.push(shared);
  }
  return out;
}
const continuous = seam => seam.every(([a, b]) => Math.abs(a[0] - b[0]) < 1e-3 && Math.abs(a[1] - b[1]) < 1e-3);
const onRadius = (radius, axes) => (brush, face) => face.indices.every(i => Math.abs(Math.hypot(...axes.map(k => brush.vertices[i][k])) - radius) < 1e-3);

test('wrap runs a texture round a 16-sided pillar with one seam, where the loop closes, and leaves its caps', () => {
  const sides = [...Array(16).keys()].map(k => [80 * Math.cos(k * Math.PI / 8), 0, 80 * Math.sin(k * Math.PI / 8)]);
  const pillar = B.extrude(sides, [0, 200, 0], {material: 'wall'});
  const start = pillar.faces.findIndex(face => face.indices.length === 4);
  pillar.faces[start] = {...pillar.faces[start], ...textured};
  const [wrapped] = B.wrap([pillar], {brush: 0, face: start});
  const side = (brush, face) => face.indices.length === 4, list = seams([wrapped], side);
  assert.equal(list.length, 16);
  assert.equal(list.filter(continuous).length, 15, 'unbroken across 15 of the 16 edges');
  // Where the walks round either way meet, the texture is a whole perimeter apart (in texture units, scale undone).
  const [open] = list.filter(seam => !continuous(seam)), perimeter = 16 * 2 * 80 * Math.sin(Math.PI / 16);
  for (const [a, b] of open) near(Math.hypot((a[0] - b[0]) * textured.scaleU, (a[1] - b[1]) * textured.scaleV), perimeter, 'the seam is the perimeter');
  wrapped.faces.forEach((face, f) => {
    if (side(wrapped, face)) assert.ok(face.material === 'wall' && face.scaleU === .5 && face.scaleV === 2, 'the look carried');
    else assert.equal(face, pillar.faces[f], 'caps untouched');
  });
  // Unwrapped, every edge is a seam.
  assert.equal(seams([pillar], side).filter(continuous).length, 0);
});

test('wrap crosses from brush to brush along the outside of a wedge pillar and the underside of an arch', () => {
  const wedges = B.ring({axis: 1, inner: 0, outer: 64, from: 0, to: 128, sides: 16, degrees: 360}, {material: 'wall'});
  const outside = onRadius(64, [0, 2]), first = wedges[0].faces.findIndex(face => outside(wedges[0], face));
  wedges[0] = {...wedges[0], faces: wedges[0].faces.map((face, f) => f === first ? {...face, ...textured} : face)};
  const wrapped = B.wrap(wedges, {brush: 0, face: first});
  assert.equal(seams(wrapped, outside).filter(continuous).length, 15);
  // Only the outside changes: the faces between the wedges are not the way on.
  wrapped.forEach((brush, b) => brush.faces.forEach((face, f) => { if (!outside(brush, face)) assert.equal(face, wedges[b].faces[f]); }));
  const arch = B.arch({span: 256, thickness: 32, depth: 24, segments: 8}, {material: 'wall'}), under = onRadius(96, [0, 1]);
  const at = arch[3].faces.findIndex(face => under(arch[3], face));
  arch[3] = {...arch[3], faces: arch[3].faces.map((face, f) => f === at ? {...face, ...textured} : face)};
  const done = B.wrap(arch, {brush: 3, face: at}), list = seams(done, under);
  assert.equal(list.length, 7);
  assert.ok(list.every(continuous), 'an open strip has no seam');
});

test('wrap goes round right angles only onto the faces it is given', () => {
  const box = B.box([0, 0, 0], [96, 64, 40], {material: 'wall'});
  box.faces[0] = {...box.faces[0], ...textured};
  assert.equal(B.wrap([box], {brush: 0, face: 0})[0], box, 'no face turns less than a right angle');
  const around = [0, 1, 4, 5].map(face => ({brush: 0, face})), [wrapped] = B.wrap([box], {brush: 0, face: 0}, around);
  const side = (brush, face) => [0, 1, 4, 5].includes(brush.faces.indexOf(face)), list = seams([wrapped], side);
  assert.equal(list.filter(continuous).length, 3);
  for (const [a, b] of list.find(seam => !continuous(seam))) near(Math.hypot((a[0] - b[0]) * .5, (a[1] - b[1]) * 2), 2 * (96 + 40));
  assert.equal(wrapped.faces[2], box.faces[2]);
});

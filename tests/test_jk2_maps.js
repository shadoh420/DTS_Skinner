'use strict';
const assert = require('node:assert/strict');
const rtcw = require('../static/rtcw-maps/viewer.js');
const jk2 = require('../static/jk2-maps/viewer.js');
assert.equal(jk2.data, '/jk2-map-data/');
assert.equal(jk2.defaultMap, 'sp_kejim_post');
assert.equal(jk2.bspVersion, 47);
assert.equal(rtcw.data, '/rtcw-map-data/');
assert.notEqual(jk2.addPlaced, rtcw.addPlaced);
assert.deepEqual(jk2.matrix([0, 0, 0], [1, 1, 1], [1, 2, 3]).slice(12), [1, 2, 3, 1]);
const item = {origin: [1, 2, 3], angles: [0, 0, 0], scale: [1, 1, 1], spin: true, bob: {base: 4, rate: .005}};
assert.ok(Math.abs(jk2.pose(item, .512)[0]) < 1e-10);
assert.equal(jk2.pose(item, 0)[14], 7 + Math.cos(5) * 3);
assert.equal(rtcw.spinAngle(1), 1000 * 360 / 4095);
const material = {stages: [{map: 'texture.png', rgbGen: ['identity']},
  {map: '$lightmap', rgbGen: ['identity'], blend: ['gl_dst_color', 'gl_zero']}]};
assert.equal(jk2.stagesOf(material, true, false), undefined);
assert.equal(jk2.stagesOf(material, false, false, false), undefined); // LIGHTMAP_NONE is not BY_VERTEX.
assert.equal(jk2.stagesOf(material, false, false, true)[1].map, '$whiteimage');
assert.deepEqual(jk2.stagesOf(material, false, false, true)[1].rgbGen, ['exactvertex']);
assert.equal(material.stages[1].map, '$lightmap'); // No mutation across lit/unlit groups.
const firstLight = {stages: [...material.stages].reverse()};
assert.equal(jk2.stagesOf(firstLight, false, false, true).length, 1);
assert.deepEqual(jk2.stagesOf(firstLight, false, false, true)[0].rgbGen, ['exactvertex']);
assert.equal(jk2.stagesOf(firstLight, false, false, true)[0].blend, undefined);
assert.equal(jk2.stagesOf({implicit: true}, false, false), undefined);
const diffuse = {stages: [{map: 'texture.png', rgbGen: ['lightingdiffuse']}, {map: 'glow.png', rgbGen: ['identity']}]};
assert.deepEqual(jk2.stagesOf(diffuse, false, true).map(s => s.rgbGen), [['exactvertex'], ['identity']]);
const light = {ambient: [32, 40, 50], directed: [64, 80, 96], direction: [1, 0, 0]};
const matrix = jk2.matrix([0, 0, 0], [1, 1, 1], [0, 0, 0]);
assert.deepEqual(jk2.diffuse([1, 0, 0], matrix, light), [96/255, 120/255, 146/255, 1]);
assert.deepEqual(jk2.diffuse([-1, 0, 0], matrix, light), [32/255, 40/255, 50/255, 1]);
assert.deepEqual(jk2.diffuse([1, 0, 0], jk2.matrix([0, 180, 0], [1, 1, 1], [0, 0, 0]), light), [32/255, 40/255, 50/255, 1]);
assert.deepEqual(jk2.diffuse([1, 0, 0], matrix, null), [1, 1, 1, 1]);
assert.equal(rtcw.stagesOf, undefined);
// Exercise actual Three geometry instances without creating a renderer/browser.
global.THREE = require('../static/three.min.js');
(async () => {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute([1, 0, 0], 3));
  const shared = [{geometry, materials: [new THREE.MeshBasicMaterial()], sort: 3}];
  const context = {root: new THREE.Group(), loadItemModel: async () => shared, ticks: []};
  const placement = {origin: [0, 0, 0], angles: [0, 0, 0], scale: [1, 1, 1], models: ['synthetic'], light};
  const a = await jk2.addPlaced(placement, context);
  const b = await jk2.addPlaced({...placement, angles: [0, 180, 0]}, context);
  assert.equal(geometry.attributes.tint, undefined);
  assert.notEqual(a.children[0].geometry, b.children[0].geometry);
  assert.ok(Math.abs(a.children[0].geometry.attributes.tint.getX(0) - 96/255) < 1e-6);
  assert.ok(Math.abs(b.children[0].geometry.attributes.tint.getX(0) - 32/255) < 1e-6);
  const spinner = await jk2.addPlaced({...placement, spin: true}, context);
  context.ticks[0](1.024);
  assert.ok(Math.abs(spinner.children[0].geometry.attributes.tint.getX(0) - 32/255) < 1e-6);
  console.log('JK2 map configuration, vertex stages and per-instance light grid OK');
})().catch(error => {console.error(error); process.exitCode = 1;});

'use strict';
const assert = require('node:assert/strict');
const THREE = require('../static/three.min.js');
const P = require('../static/particles.js');

// A template is 16 RGBA texels in the order the vertex shader reads them.
const t = {count: [2, 3], life: [.5, .9], delay: [0, .2], even: true, cullRange: 350, origin: [[0, -8, -8], [0, 8, 8]],
  rotation: [87, 93], spin: [-40, 40], velocity: [[0, -5, -5], [10, 5, 5]], gravity: [20, 60],
  size: {start: [3, 6], end: [24, 28], curve: {linear: true, kind: 'clamp', parm: 50}},
  alpha: {start: 1, end: 0, curve: {linear: true, kind: 'nonlinear', parm: 50}}, rgb: {start: [[1, .78, .78], [1, .49, .49]]},
  rgbTogether: true, alphaFadesRgb: true, worldVelocity: true, frames: [4, 2]};
const row = P.pack(t), texel = n => [...row.slice(n * 4, n * 4 + 4)].map(v => +v.toFixed(4));
assert.deepEqual(texel(0), [.5, .9, 0, .2]);
assert.deepEqual(texel(1), [2, 3, 1, 350]);
assert.deepEqual(texel(2), [0, -8, -8, 87]);
assert.deepEqual(texel(7), [0, 0, 0, 60]);
assert.deepEqual(texel(8), [3, 6, 24, 28]);
assert.deepEqual(texel(9), [1, 1, 0, 0]);
assert.deepEqual(texel(10), [1, .78, .78, 1], 'rgbTogether in w');
assert.deepEqual(texel(11).slice(3), [1], 'alphaFadesRgb in w');
assert.deepEqual(texel(12), [1, .78, .78, 2], 'rgb end defaults to the start; worldVelocity is bit 2');
assert.deepEqual(texel(14), [9, 50, 3, 50], 'size linear+clamp, alpha linear+nonlinear');
assert.deepEqual(texel(15), [0, 0, 4, 8], 'rgb has no curve; flipbook 4 columns, 8 frames');
assert.equal(P.pack({frames: [5, 5], frameCount: 24})[15 * 4 + 3], 24, 'a grid whose last row is not full');
assert.equal(P.pack({sizeTogether: true})[12 * 4 + 3], 16, 'one random number for start and end size');

// Slots: one per live burst x particle; a primitive's shaders share the slots; the cap thins them evenly.
const texture = new THREE.Texture();
const emitter = {origin: [1, 2, 3], forward: [0, 0, 1], right: [0, -1, 0], template: 0, period: .2, batch: 'a'};
let system = P.create(THREE, [t], [emitter], {a: {texture}});
assert.equal(system.wanted, 6 * 3, 'ceil((0.2 + 0.9) / 0.2) bursts of 3');
assert.equal(system.instances, 18);
assert.equal(system.meshes.length, 1);
const data = system.meshes[0].geometry.attributes.iOrigin.data.array;
assert.deepEqual([...data.slice(0, 4)], [1, 2, 3, 0]);
assert.deepEqual([...data.slice(12, 14)].map(v => +v.toFixed(4)), [0, 1.2], 'first slot: phase 0, loop 6 periods');
system = P.create(THREE, [t], [{...emitter, share: [0, 2]}, {...emitter, batch: 'b', share: [1, 2]}], {a: {texture}, b: {texture}});
assert.equal(system.instances, 18, 'two shaders split the 18 slots');
assert.deepEqual(system.meshes.map(m => m.geometry.instanceCount), [9, 9]);
system = P.create(THREE, [t], [emitter], {a: {texture}}, {maxSlots: 9});
assert.ok(system.instances <= 10 && system.instances >= 8, 'the cap keeps about half');
system.setQuality('off');
assert.equal(system.meshes[0].visible, false);
system.setQuality('medium');
assert.equal(system.meshes[0].visible, true);
assert.equal(system.meshes[0].material.uniforms.uDensity.value, .5);
system.update(3, [4, 5, 6]);
assert.equal(system.meshes[0].material.uniforms.uTime.value, 3);
assert.deepEqual(system.meshes[0].material.uniforms.uEye.value.toArray(), [4, 5, 6]);
assert.equal(P.create(THREE, [t], [emitter], {a: {texture: null}}).meshes.length, 0, 'a batch without a texture draws nothing');
// Source extras: the sine curve (kind 32, parm the power), a distance fade and screen-sized motes (bit 8).
const dust = P.pack({alpha: {start: 0, end: .2, curve: {kind: 'sine', parm: 2}}, fade: 512, screenSize: true, worldOrigin: true});
assert.deepEqual([...dust.slice(14 * 4 + 2, 14 * 4 + 4)], [32, 2]);
assert.equal(dust[13 * 4 + 3], 512);
assert.equal(dust[12 * 4 + 3], 9);
// Scaled page axes: sizes follow the scale, and update(..., true) turns the page eye back into game axes.
const scaled = P.create(THREE, [t], [emitter], {a: {texture}}, {toWorld: [0, 0, .5, .5, 0, 0, 0, .5, 0]});
assert.equal(scaled.meshes[0].material.uniforms.uSizeScale.value, .5);
scaled.update(1, [1, 2, 3], true);
assert.deepEqual(scaled.meshes[0].material.uniforms.uEye.value.toArray().map(v => +v.toFixed(6)), [6, 2, 4]);
console.log('particles ok');

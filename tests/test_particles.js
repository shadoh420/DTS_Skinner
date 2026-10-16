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
// Troika (Bloodlines) templates: a header, a track directory, then the keys, in one texel stream.
const tracks = Array.from({length: 17}, () => []);
tracks[3] = [[0, 0, 0, 0], [0, 1 / 3, 20, 60]]; tracks[16] = [[0, 0, 40, 40]];
const flame = {model: 'troika', loop: false, movealign: true, collide: true, aspect: [.5, .25], lifeRate: [.9, 1], depthOffset: -2,
  spawn: {radius: [0, 5], timescale: [1, 1]}, tracks, parentLife: 1e9, rateMax: 0, life: [1, 1.2], count: 1, delay: 0};
const run = P.packTroika(flame), at = n => [...run.slice(n * 4, n * 4 + 4)].map(v => +v.toFixed(4));
assert.deepEqual(at(0), [.9, 1, 1, 1]);
assert.deepEqual(at(1), [2 + 8, -2, .5, .25], 'movealign and collide bits, depth offset, sprite half sides');
assert.deepEqual(at(2), [0, 5, 0, 0]);
assert.deepEqual(at(6), [255, 255, 255, 255], 'spawn colour multipliers default to 255');
assert.deepEqual(at(10), [1e9, 0, 0, 1], 'parent life, max rate, delay, gain (default 1)');
assert.deepEqual(at(14).slice(2), [22, 2], 'track 3 starts after the 22-texel header and has 2 keys');
assert.deepEqual(at(21), [22 + 2, 1, 0, 0], 'track 16 (the rate) follows');
assert.deepEqual(at(23), [0, .3333, 20, 60]);
const mixed = P.create(THREE, [t, flame], [emitter, {...emitter, template: 1, batch: 'b', floor: -64}], {a: {texture}, b: {texture}});
assert.equal(mixed.meshes.length, 2);
assert.ok(!mixed.meshes[0].material.vertexShader.includes('uKeys') && mixed.meshes[1].material.vertexShader.includes('uKeys'));
const troikaData = mixed.meshes[1].geometry.attributes.iOrigin.data.array;
assert.equal(troikaData[3], 0, 'the only troika template starts the stream');
assert.equal(troikaData[15], -64, 'the floor below a colliding emitter rides in iTime.w');
// Rain around the player: follow and outdoors bits; the eye's rain leaf (options.outdoors, game axes) gates it.
assert.equal(P.packTroika({...flame, follow: true, outdoors: true})[4], 2 + 8 + 32 + 64);
const rain = P.create(THREE, [{...flame, follow: true, outdoors: true}], [{...emitter, batch: 'b'}], {b: {texture}},
  {outdoors: {root: 0, nodes: [[0, 0, 1, 100, -2, -1]]}});  // above z 100 is a rain leaf (1), below is not (0)
const outdoors = rain.meshes[0].material.uniforms.uOutdoors;
rain.update(0, [0, 0, 150]);
assert.equal(outdoors.value, 1);
rain.update(0, [0, 0, 50]);
assert.equal(outdoors.value, 0);
assert.equal(system.meshes[0].material.uniforms.uOutdoors.value, 1, 'without rain leaves nothing is gated');
console.log('particles ok');

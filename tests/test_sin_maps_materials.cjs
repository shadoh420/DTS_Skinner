/* Run: node tests/test_sin_maps_materials.cjs — shader contract only, no browser. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Vector { constructor(...v) { this.values = v; } normalize() { return this; } }
class Material { constructor(options) { Object.assign(this, options); } }
const context = {window: {}, THREE: {Vector2: Vector, Vector3: Vector, ShaderMaterial: Material,
  MeshBasicMaterial: Material, DoubleSide: 2, NearestFilter: 1003, CustomBlending: 5, OneFactor: 201}};
vm.createContext(context);
for (const name of ['quake2', 'sin']) vm.runInContext(fs.readFileSync(`static/${name}-maps/materials.js`, 'utf8'), context);
const surface = {magFilter: 999, clone() { return {...this}; }};
const group = {kind: 'normal', flags: 64, size: [8, 8], unlit: false, alpha: 1,
  settings: {masked: true, nofilter: true, nonlit: .7}};
const material = context.window.sinMaps.material(group, surface, {});
assert.equal(material.uniforms.alphaMode.value, 4);
assert.equal(material.uniforms.flowing.value, false);
assert.equal(material.uniforms.unlitLight.value, .7);
assert.equal(material.transparent, false);
assert.equal(material.depthWrite, true);
assert.equal(material.uniforms.surface.value.magFilter, 1003);
assert.equal(surface.magFilter, 999);
assert.match(material.fragmentShader, /texel\.a <= 0/);
assert.match(material.fragmentShader, /texel\.a <= \.666/);
assert.match(material.fragmentShader, /texture2D\(lights,lightUV\)/);
const translucent = context.window.sinMaps.material({...group, alpha: .25, settings: {}}, surface, {});
assert.equal(translucent.transparent, true);
assert.equal(translucent.depthWrite, false);
assert.equal(translucent.uniforms.alpha.value, .25);
const actor = context.window.sinMaps.material({...group, kind: 'model', settings: {cull: 'none', alphaFunc: 'GE128'}}, surface, {});
assert.equal(actor.side, 2);
assert.equal(actor.uniforms.model.value, true);
assert.equal(actor.uniforms.alphaMode.value, 3);
const additive = context.window.sinMaps.material({...group, settings: {blend: 'add'}}, surface, {});
assert.equal(additive.blending, 5);
assert.equal(additive.blendSrc, 201);
assert.equal(additive.blendDst, 201);
assert.equal(additive.depthWrite, false);
// Existing Q2 material retains its own FLOWING interpretation.
assert.equal(context.window.quake2Maps.material(group, surface, {}).uniforms.flowing.value, true);
const blend = (map, position) => Array.from(context.window.sinMaps.viewBlend(map, position));
const water = {root: 0, nodes: [[1, 0, 0, 64, -2, -1]], color: [0, 0, 1], alpha: .1};
assert.deepEqual(blend({water}, {x: 0, y: 0, z: 3}), [0, 0, 1, .1]);
assert.deepEqual(blend({water}, {x: 0, y: 0, z: 1}), [0, 0, 0, 0]);
assert.deepEqual(blend({}, {x: 0, y: 0, z: 3}), [0, 0, 0, 0]);
assert.deepEqual(blend(null, {x: 0, y: 0, z: 3}), [0, 0, 0, 0]);
assert.deepEqual(blend({water: {...water, root: -2, nodes: []}}, {x: 0, y: 0, z: 1}), [0, 0, 1, .1]);
const viewer = fs.readFileSync('static/unreal-maps/viewer.js', 'utf8');
assert.match(viewer.split('\n').find(line => line.includes('document.title =')), /sin \? 'SiN' :/);
assert.match(viewer, /new THREE.Vector4\(0, 0, 0, 0\)/); // Other pages keep zero blend.
console.log('SiN material contracts OK (no WebGL/render acceptance claimed)');
// TRANSLATE scrolls at trans_mag texture units a second along trans_angle; light styles step at 10 Hz.
const scrolling = context.window.sinMaps.material({...group, settings: {flowing: true, trans: [16, 90]}}, surface, {});
assert.ok(Math.abs(scrolling.uniforms.scroll.value.values[1] - 2) < 1e-9);
assert.match(scrolling.vertexShader, /texUV = uv - scroll \* seconds;/);
const styled = context.window.sinMaps.material({...group, settings: {}}, surface, {});
styled.userData = {quake2Lit: true};
context.window.quake2Maps.patchMaterial(styled, {styles: [4]}, {light_styles: {offset: .25, scale: 1 / 12, patterns: {4: 'mamamamamama'}}});
assert.equal('STYLED' in styled.defines, true);
context.window.quake2Maps.update(.15);  // Step 1 of 'mamamamamama' is 'a'.
assert.equal(styled.uniforms.styleA.value, 0);
context.window.quake2Maps.update(.25);
assert.equal(styled.uniforms.styleA.value, 1);
for (const shader of [styled.fragmentShader, styled.vertexShader]) {
  assert.equal((shader.match(/#ifdef/g) || []).length, (shader.match(/#endif/g) || []).length);
  assert.equal((shader.match(/\{/g) || []).length, (shader.match(/\}/g) || []).length);
}
console.log('SiN translation and light style contracts OK');

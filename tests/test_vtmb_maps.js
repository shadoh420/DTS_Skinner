/* No browser or renderer: verify adapter options and shared-page isolation. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}, THREE: {
  ShaderMaterial: class { constructor(options) { Object.assign(this, options); } },
  Vector3: class { constructor(...values) { this.values = values; } set(...values) { this.values = values; return this; } dot(v) { return this.values.reduce((t, x, i) => t + x * v.values[i], 0); } },
  DoubleSide: 2, FrontSide: 0, AdditiveBlending: 2, NormalBlending: 1
}};
vm.runInNewContext(fs.readFileSync('static/vtmb-maps/materials.js', 'utf8'), context);
const config = context.window.skinnerMapViewerConfig;
assert.equal(config.dataRoot, '/vtmb-map-data/');
assert.equal(config.importRoute, '/import_vtmb_maps');
const material = config.material({kind: 'surface', settings: {alphaFunc: 'GE128', cull: 'none', selfillum: true}}, {}, {});
assert.equal(material.uniforms.cutout.value, true);
assert.equal(material.uniforms.selfillum.value, true);
assert.equal(material.side, 2);
assert.equal(material.depthWrite, true);
assert.equal(config.material({kind: 'model'}, {}, {}).uniforms.unlit.value, true);
const additive = config.material({settings: {blend: ['gl_src_alpha', 'gl_one']}}, {}, {});
assert.equal(additive.blending, 2); assert.equal(additive.depthWrite, false);
// Fog and TextureScroll: one shared uniform pair, repointed per pass and per camera region.
const fogged = config.material({kind: 'surface', settings: {parameters: {proxies: {texturescroll: {texturescrollvar: '$BaseTextureTransform', texturescrollrate: '.5', texturescrollangle: '90'}}}}}, {}, {});
assert.equal(fogged.uniforms.fogTo.value, 1); assert.equal(additive.uniforms.fogTo.value, 0);
assert.equal(config.material({settings: {parameters: {'$nofog': '1'}}}, {}, {}).uniforms.fogTo.value, -1);
assert.strictEqual(fogged.uniforms.fogRange, additive.uniforms.fogRange);
assert.ok(Math.abs(fogged.uniforms.pan.value[0]) < 1e-9 && Math.abs(fogged.uniforms.pan.value[1] - .5) < 1e-9);
context.window.quake2Maps = {loadSky: async () => {}};
const region = {fog: {color: [0, 0, 1], start: 0, end: 2, max: 1}};
config.load({lighting: {brightness: 1}, fog: {color: [1, 0, 0], color2: [0, 1, 0], dir: [0, 0, 1], blend: true, start: 1, end: 9, max: .5},
  sky3d: {fog: {color: [1, 1, 1], start: 0, end: 3}}, regions: [null, region]}, null).then(() => {
  const range = () => fogged.uniforms.fogRange.value.values;
  assert.deepEqual(range(), [1, 9, .5]);
  config.update(0, {getWorldDirection: v => v.set(0, 0, 1)}, {}, null);
  assert.deepEqual(fogged.uniforms.fogColor.value.values, [1, 0, 0]);  // Looking along fogdir: the primary colour.
  config.pass('sky'); assert.deepEqual(range(), [0, 3, 1]);
  config.pass('main'); assert.deepEqual(range(), [1, 9, .5]);
  config.update(0, {getWorldDirection: v => v.set(0, 0, 1)}, {}, region); assert.deepEqual(range(), [0, 2, 1]);
}).catch(error => { console.error(error); process.exitCode = 1; });
for (const game of ['quake', 'quake2', 'sin', 'anachronox', 'daikatana', 'unreal']) {
  const page = fs.readFileSync(`static/${game}-maps/index.html`, 'utf8');
  assert.ok(!page.includes('/vtmb-maps/'), game);
}
console.log('Bloodlines map adapter assertions passed');

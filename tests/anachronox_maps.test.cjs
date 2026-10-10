'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const root = path.resolve(__dirname, '..');
const context = {window: {}, THREE: {
  Vector2: class { constructor(...v) { this.values = v; } },
  Vector3: class { constructor(...v) { this.values = v; } normalize() { return this; } },
  ShaderMaterial: class { constructor(options) { Object.assign(this, options); } },
  LinearFilter: 'linear', CustomBlending: 'custom', OneFactor: 'one', SrcAlphaFactor: 'srcAlpha',
  OneMinusSrcAlphaFactor: 'invAlpha', DstColorFactor: 'dstColor', DoubleSide: 'double'
}};
vm.createContext(context);
for (const file of ['quake2-maps/materials.js', 'anachronox-maps/materials.js']) {
  vm.runInContext(fs.readFileSync(path.join(root, 'static', file), 'utf8'), context);
}
const anox = context.window.anachronoxMaps;
const group = {kind: 'normal', unlit: false, flags: 0, alpha: 1, size: [128, 128]};
test('RGB lighting, exponential fog and no Q2 intensity compensation', () => {
  anox.fog = {density: .001, color: [.1, .2, .3]};
  const material = anox.material(group, {}, {});
  assert.equal(material.uniforms.fogDensity.value, .032);
  assert.match(material.fragmentShader, /exp\(-fogDensity \* fogDepth\)/);
  assert.equal(material.uniforms.twoPassFog.value, true);
  assert.match(material.fragmentShader, /texel.rgb \* light/);
  assert.doesNotMatch(material.fragmentShader, /vec3\(\.5\)|pow\(/);
  assert.equal(material.depthWrite, true);
  // Loading the adapter never changes the pre-existing Q2 shader.
  assert.match(context.window.quake2Maps.material(group, {}, {}).fragmentShader, /vec3\(\.5\)/);
});
test('alpha-test, banner and MDA material settings', () => {
  const masked = anox.material({...group, flags: 0x20000}, {}, {});
  assert.equal(masked.uniforms.alphaMode.value, 1);
  assert.equal(masked.transparent, true);
  assert.equal(masked.depthWrite, true);
  assert.equal(masked.uniforms.useAlpha.value, true);
  const imageAlpha = anox.material({...group, flags: 16, texture_alpha: true}, {}, {});
  assert.equal(imageAlpha.uniforms.useAlpha.value, true);
  assert.equal(imageAlpha.uniforms.alpha.value, 1);
  assert.equal(imageAlpha.transparent, true);
  assert.equal(imageAlpha.depthWrite, false);
  const banner = anox.material({...group, flags: 0x10000}, {}, {});
  assert.equal(banner.transparent, true);
  assert.equal(banner.depthWrite, false);
  assert.equal(banner.side, 'double');
  const model = anox.material({...group, kind: 'model', model_light: [.2, .3, .4],
    settings: {alphaFunc: 'GT0', blend: ['gl_one', 'gl_one'], depthWrite: false, cull: 'none'}}, {}, {});
  assert.equal(model.uniforms.model.value, true);
  assert.equal(model.uniforms.alphaMode.value, 1);
  assert.equal(model.blendSrc, 'one');
  assert.equal(model.blendDst, 'one');
});
test('Anachronox-only page wiring and final-frame gamma', () => {
  const viewer = fs.readFileSync(path.join(root, 'static/unreal-maps/viewer.js'), 'utf8');
  const page = fs.readFileSync(path.join(root, 'static/anachronox-maps/index.html'), 'utf8');
  assert.match(page, /data-game="anachronox"/);
  assert.match(page, /anachronox-maps\/materials.js/);
  assert.match(viewer, /anachronox: 1, daikatana: 1/);
  assert.match(viewer, /if \(anachronox\) return window.anachronoxMaps.material/);
  assert.match(viewer, /anachronox \? '\/import_anachronox_maps'/);
});

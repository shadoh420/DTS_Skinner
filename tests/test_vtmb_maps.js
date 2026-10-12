/* No browser or renderer: verify adapter options and shared-page isolation. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}, THREE: {
  ShaderMaterial: class { constructor(options) { Object.assign(this, options); } },
  Vector3: class { constructor(...values) { this.values = values; } },
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
for (const game of ['quake', 'quake2', 'sin', 'anachronox', 'daikatana', 'unreal']) {
  const page = fs.readFileSync(`static/${game}-maps/index.html`, 'utf8');
  assert.ok(!page.includes('/vtmb-maps/'), game);
}
console.log('Bloodlines map adapter assertions passed');

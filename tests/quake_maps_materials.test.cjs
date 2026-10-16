// CPU material construction only: Quake light styles and rerelease fog wiring, no WebGL.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const test = require('node:test'), assert = require('node:assert/strict');
class Vector { constructor(...v) { this.values = v; } set(...v) { this.values = v; return this; } normalize() { return this; } }
const context = {window: {}, THREE: {Vector2: Vector, Vector3: Vector, LinearFilter: 1006, ClampToEdgeWrapping: 1001,
  ShaderMaterial: class { constructor(o) { Object.assign(this, o); this.isShaderMaterial = true; } },
  MeshBasicMaterial: class { constructor(o) { Object.assign(this, o); } }}};
vm.createContext(context);
for (const game of ['quake2', 'quake']) vm.runInContext(fs.readFileSync(path.join(__dirname, `../static/${game}-maps/materials.js`), 'utf8'), context);
const quake = context.window.quakeMaps, q2 = context.window.quake2Maps;
const map = {light_styles: {offset: .5, scale: 22, patterns: {1: 'mmnmmommommnonmmonqnmmo', 4: 'mamamamamama'}},
  fog: {color: [.2, .3, .4], exp2: .025, sky: .5}};

test('two animated styles on a classic surface, scaled as R_BuildLightMap does', () => {
  const m = quake.material({kind: 'normal', size: [16, 16]}, {}, {}, {}, false, null);
  quake.patchMaterial(m, {kind: 'normal', styles: [1, 4]}, map);
  assert.equal('STYLED' in m.defines && 'FOG' in m.defines, true);
  q2.update(.1);  // Step 1: 'm' and 'a'.
  assert.deepEqual([m.uniforms.styleA.value, m.uniforms.styleB.value], [12 * 22, 0]);
  assert.match(m.fragmentShader, /max\(64\., code - \(moving\.r \* styleA \+ moving\.g \* styleB\) \* 255\. \/ 4\.\)/);
  assert.equal(m.uniforms.fogExp2.value, .025);
  assert.equal((m.fragmentShader.match(/#ifdef/g) || []).length, (m.fragmentShader.match(/#endif/g) || []).length);
});

test('the rerelease skybox takes skyfog; maps without fog or styles stay as they were', () => {
  q2.sky = {};
  const sky = quake.material({kind: 'sky', size: [1, 1]}, {}, {}, {}, true, {});
  quake.patchMaterial(sky, {kind: 'sky'}, map);
  assert(sky.fragmentShader.includes('mix(color.rgb, fogColor, skyFog)'));
  const plain = quake.material({kind: 'normal', size: [16, 16]}, {}, {}, {}, true, null);
  quake.patchMaterial(plain, {kind: 'normal'}, {});
  assert.equal(plain.defines, undefined);
});

test("'{' fence textures discard palette index 255, as Quakespasm's TEXPREF_ALPHA does", () => {
  const fence = quake.material({kind: 'normal', name: '{vines2', size: [64, 64]}, {}, {}, {}, true, null);
  quake.patchMaterial(fence, {kind: 'normal', styles: [1]}, map);
  assert.equal('FENCE' in fence.defines && 'STYLED' in fence.defines, true);
  assert.match(fence.fragmentShader, /#ifdef FENCE\s+if \(pixel\(texUV\) == 255\.\) discard;/);
  assert.equal(quake.material({kind: 'normal', name: 'city4_6', size: [64, 64]}, {}, {}, {}, false, null).defines, undefined);
});

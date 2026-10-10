// CPU material construction only; the import host owns GPU/game comparisons.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const test = require('node:test'), assert = require('node:assert/strict');
const context = {window: {}, THREE: {
  Vector2: class { constructor(...v) { this.values = v; } },
  Vector3: class { constructor(...v) { this.values = v; } normalize() { return this; } },
  ShaderMaterial: class { constructor(args) { Object.assign(this, args); this.isShaderMaterial = true; } },
  NormalBlending: 1, AdditiveBlending: 2, DoubleSide: 2, LinearFilter: 1006
}};
vm.createContext(context);
for (const game of ['quake2', 'daikatana']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, `../static/${game}-maps/materials.js`), 'utf8'), context);
}
const dk = context.window.daikatanaMaps;
dk.fog = {enabled: true, color: [.78, .99, .4], start: 200, end: 9600, sky_end: 12000};
dk.clouds = [];
const group = {kind: 'normal', unlit: false, flags: 0, alpha: 1, size: [128, 128]};
const material = (overrides = {}) => dk.material({...group, ...overrides}, {}, {});

test('lit BSP uses two separately fogged passes, without a third final fog', () => {
  for (const kind of ['normal', 'turbulent']) {
    const m = material({kind});
    assert(m.fragmentShader.includes('mix(fogRGB, sampleColor.rgb, f) * mix(fogRGB, illumination, f)'));
    assert(!m.fragmentShader.includes('mix(fogRGB, gl_FragColor.rgb,'));
    assert.equal(m.uniforms.fogStart.value, 200 / 32);
    assert.equal(m.uniforms.fogEnd.value, 9600 / 32);
    assert.equal((m.fragmentShader.match(/void main/g) || []).length, 1);
  }
});

test('masked window is unlit, alpha blended after opaque sky, with depth writes', () => {
  // An older pack can still have unlit=false: the adapter must correct it.
  const m = material({flags: 0x80000});
  assert.equal(m.uniforms.unlit.value, true);
  assert.equal(m.transparent, true);
  assert.equal(m.depthWrite, true);
  assert.equal(m.blending, context.THREE.NormalBlending);
  assert(m.fragmentShader.includes('sampleColor.a <= 0.'));
  assert(m.fragmentShader.includes('mix(fogRGB, gl_FragColor.rgb,'));
  assert(!m.fragmentShader.includes('mix(fogRGB, illumination, f)'));
});

test('sky, fullbright, alias and sprites receive fog only once', () => {
  for (const spec of [{kind: 'sky'}, {unlit: true}, {kind: 'model'},
    {kind: 'sprite', sprite_size: [2, 2], sprite_pivot: [1, 1]}, {kind: 'sprite_oriented', additive: true}]) {
    const m = material(spec);
    assert(m.fragmentShader.includes('mix(fogRGB, gl_FragColor.rgb,'));
    assert(!m.fragmentShader.includes('mix(fogRGB, illumination, f)'));
    if (spec.kind === 'model') assert(m.fragmentShader.includes('alias ? aliasLight.rgb'));
    if (spec.kind === 'sprite_oriented') assert.equal(m.blending, context.THREE.AdditiveBlending);
  }
});

test('clouds use the sky upload filter without mipmaps', async () => {
  const cloud = {};
  await dk.load({fog: dk.fog, skybox: {rotate: 0, axis: [0, 0, 1],
    faces: Object.fromEntries(['rt', 'lf', 'bk', 'ft', 'up', 'dn'].map(k => [k, {texture: k}])),
    clouds: {layers: [{texture: 'cloud', tile: 8, alpha: 1}, {texture: 'cloud', tile: 2, alpha: .7}]}}},
  async name => name === 'cloud' ? cloud : {});
  assert.equal(cloud.generateMipmaps, false);
  assert.equal(cloud.minFilter, context.THREE.LinearFilter);
  assert.equal(cloud.magFilter, context.THREE.LinearFilter);
});

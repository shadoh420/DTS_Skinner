// Synthetic adapter regression: node tests/test_redbaron_maps_stream.cjs
// Real Three.js math, stubbed I/O; no browser or retail assets.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const THREE = require('../static/three.min.js');
global.THREE = THREE;
global.window = {};
global.Option = class { constructor(text, value) { this.text = text; this.value = value; } };
const elements = new Map();
global.document = {getElementById(id) {
  if (!elements.has(id)) elements.set(id, {
    replaceChildren(...children) { this.children = children; },
    addEventListener(event, callback) { this[event] = callback; }, textContent: ''
  });
  return elements.get(id);
}};
global.requestAnimationFrame = () => { throw Error('Hidden-pane loads must not depend on rAF'); };
const textures = [];
let textureDisposals = 0, geometryDisposals = 0;
THREE.TextureLoader.prototype.load = function (url, done) {
  const texture = new THREE.Texture();
  texture.addEventListener('dispose', () => textureDisposals++);
  textures.push(texture);
  done(texture);
};
const dispose = THREE.BufferGeometry.prototype.dispose;
THREE.BufferGeometry.prototype.dispose = function () { geometryDisposals++; dispose.call(this); };
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '../static/rb3d-maps/stream.js'), 'utf8'));
const chunks = Array.from({length: 64}, (_, i) => {
  const x = i % 8 * 10000, z = Math.floor(i / 8) * 10000;
  return {path: `regions/0/${i}`, bounds: [x, z, x + 10000, z + 10000]};
});
const matrix = new THREE.Matrix4().identity().toArray();
const objects = [...Array.from({length: 16}, () => ({model: null, matrix})), {model: 'hangar.json', matrix}];
const target = new THREE.Vector3(1000, 0, 1000), eye = new THREE.Vector3(1000, 600, 0);
const jump = new THREE.Matrix4().lookAt(eye, target, new THREE.Vector3(0, 1, 0)).setPosition(eye).toArray();
const requests = [];
let hold = true, outstanding = 0, peak = 0;
const get = async url => {
  if (url.endsWith('index.json')) return {json: async () => ({name: 'Synthetic', chunks,
    jumps: [{name: 'Field', kind: 0, matrix: jump}, {name: 'Town', type: 'Town', matrix: jump}]})};
  if (url.includes('models/')) return {json: async () => ({vertices: [0,0,0, 1,0,0, 0,1,0],
    normals: [0,0,1, 0,0,1, 0,0,1],
    uvs: [0,0, 1,0, 0,1], indices: [0,1,2, 0,2,1], groups: [
      {start: 0, count: 3, materialIndex: 0, shade: 1},
      {start: 3, count: 3, materialIndex: 1, shade: 0}],
    material_textures: ['shape.png', 'shape.png']})};
  if (url.endsWith('.json')) {
    outstanding++; peak = Math.max(peak, outstanding);
    if (hold) await new Promise(resolve => requests.push(resolve));
    outstanding--;
    return {json: async () => ({vertices: 3, stride: 8, groups: [{start: 0, count: 3, texture: 'ground.png'}], objects})};
  }
  return {arrayBuffer: async () => new Float32Array([0,0,0, 0,0, 0,1,0,
    1,0,0, 1,0, 0,1,0, 0,0,1, 0,1, 0,1,0]).buffer};
};
const flush = () => new Promise(resolve => setTimeout(resolve, 5));
(async () => {
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(60, 1, .2, 25000);
  const lighting = {model: 'baron-glide', toward: [0,1,0], intensity: 1,
    ambient: .5, terrainScale: 11, visibility: 1, weather: 1};
  const range = window.rb3dMaps.fogRange(0, lighting);
  assert.deepEqual(range, [103219*.0381, 147456*.0381]);
  const fog = distance => Math.min(255, Math.floor(Math.max(0, Math.min(1,
    (distance-range[0])/(range[1]-range[0]))) * 65535 / 128 + .5/128)) / 255;
  assert.equal(fog(range[0]), 0);
  assert.equal(fog(range[0] + .25*(range[1]-range[0])), 128/255);
  assert.equal(fog((range[0]+range[1])/2), 1, 'Glide saturates near half the nominal ramp');
  const storm = window.rb3dMaps.fogRange(0, {...lighting, weather: 5});
  assert.deepEqual(storm, [25804*.0381, 73728*.0381]);
  assert.ok(window.rb3dMaps.fogRange(2000, lighting)[0] > range[0]);
  assert.ok(window.rb3dMaps.fogRange(0, {...lighting, visibility: 0})[1] < range[1]);
  camera.position.set(35000, 1000, 35000);
  const frame = await window.rb3dMaps.start({scene, camera,
    map: {regions: [{id: 0, name: 'Synthetic'}], region: 0, objects, viewpoints: [], lighting}, data: '', get});
  assert.equal(scene.children[0].children.length, 1, 'Non-drawable objects have no placeholder mesh');
  assert.equal(elements.get('jump').children[1].text, 'Field (Airfield)');
  assert.equal(elements.get('jump').children[2].text, 'Town (Town)');
  elements.get('jump').change({target: {value: '0'}});
  assert.ok(camera.position.distanceTo(eye) < 1e-8);
  assert.ok(camera.getWorldDirection(new THREE.Vector3()).distanceTo(target.clone().sub(eye).normalize()) < 1e-8);
  camera.position.set(35000, 1000, 35000);
  frame(1); frame(1);
  assert.equal(requests.length, 2);
  camera.position.set(300000, 1000, 300000); frame(1);
  hold = false; requests.splice(0).forEach(resolve => resolve());
  await flush(); frame(1);
  assert.equal(scene.children.length, 1);
  assert.equal(geometryDisposals, 2);
  assert.equal(textureDisposals, 1);
  camera.position.set(35000, 1000, 35000);
  for (let i = 0; i < 40; i++) { frame(1); await flush(); }
  assert.equal(scene.children.length, 26);
  assert.ok(peak <= 2);
  for (const group of scene.children.slice(1)) assert.equal(group.children.length, 2, 'Terrain and one placed shape');
  for (const mesh of scene.children[1].children) {
    assert.ok(mesh.geometry.getAttribute('normal'));
    const material = mesh.material[0];
    const shader = {uniforms: {}, vertexShader: THREE.ShaderLib.basic.vertexShader,
      fragmentShader: THREE.ShaderLib.basic.fragmentShader};
    material.onBeforeCompile(shader);
    assert.equal(shader.uniforms.rbAmbient.value, .5);
    assert.ok(shader.vertexShader.includes('length(mvPosition.xyz)'));
    assert.ok(shader.vertexShader.includes('mat3(modelMatrix) * normal'));
    assert.ok(shader.fragmentShader.includes('diffuseColor.rgb *= rbLight / rbW'));
    assert.ok(shader.vertexShader.includes('rbW = gl_Position.w'));
    assert.ok(shader.fragmentShader.includes('#ifdef USE_FOG'));
    assert.ok(!shader.fragmentShader.includes('#include <fog_fragment>'));
    assert.ok(shader.fragmentShader.includes('#include <alphatest_fragment>'));
  }
  const unlit = scene.children[1].children[1].material[1];
  const unlitShader = {uniforms: {}, vertexShader: THREE.ShaderLib.basic.vertexShader,
    fragmentShader: THREE.ShaderLib.basic.fragmentShader};
  unlit.onBeforeCompile(unlitShader);
  assert.ok(unlitShader.vertexShader.includes('rbLight = 1.0;'));
  assert.ok(!unlitShader.vertexShader.includes('dot(n, rbToward)'));
  assert.notEqual(unlit.customProgramCacheKey(), scene.children[1].children[1].material[0].customProgramCacheKey());
  scene.fog = new THREE.Fog(0xa0b0c0, 1, 2);
  frame(0);
  assert.equal(camera.far, window.rb3dMaps.fogRange(camera.position.y, lighting)[1]);
  scene.fog = null; camera.far = 25000; // Shared viewer's haze-off behaviour.
  for (const t of textures) {
    assert.equal(t.magFilter, THREE.NearestFilter); assert.equal(t.minFilter, THREE.NearestFilter);
    assert.equal(t.wrapS, THREE.RepeatWrapping); assert.equal(t.wrapT, THREE.RepeatWrapping);
    assert.equal(t.generateMipmaps, false); assert.equal(t.anisotropy, 1);
  }
  camera.position.set(300000, 1000, 300000); frame(1); await flush();
  assert.equal(scene.children.length, 1);
  assert.equal(geometryDisposals, 52);
  assert.equal(textureDisposals, 2, 'Mission still owns the shared shape texture');
  console.log('PASS: native light/fog shader paths and ranges; normals; no placeholders; typed jumps; point sampling; hidden-pane timers; two requests; 25 chunks; disposal');
})().catch(error => { console.error(error); process.exitCode = 1; });

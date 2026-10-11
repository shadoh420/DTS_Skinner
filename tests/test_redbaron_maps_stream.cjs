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
    uvs: [0,0, 1,0, 0,1], indices: [0,1,2], groups: [{start: 0, count: 3, materialIndex: 0}],
    material_textures: ['shape.png']})};
  if (url.endsWith('.json')) {
    outstanding++; peak = Math.max(peak, outstanding);
    if (hold) await new Promise(resolve => requests.push(resolve));
    outstanding--;
    return {json: async () => ({vertices: 3, groups: [{start: 0, count: 3, texture: 'ground.png'}], objects})};
  }
  return {arrayBuffer: async () => new Float32Array(15).buffer};
};
const flush = () => new Promise(resolve => setTimeout(resolve, 5));
(async () => {
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(60, 1, .2, 25000);
  camera.position.set(35000, 1000, 35000);
  const frame = await window.rb3dMaps.start({scene, camera,
    map: {regions: [{id: 0, name: 'Synthetic'}], region: 0, objects, viewpoints: []}, data: '', get});
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
  for (const t of textures) {
    assert.equal(t.magFilter, THREE.NearestFilter); assert.equal(t.minFilter, THREE.NearestFilter);
    assert.equal(t.wrapS, THREE.RepeatWrapping); assert.equal(t.wrapT, THREE.RepeatWrapping);
    assert.equal(t.generateMipmaps, false); assert.equal(t.anisotropy, 1);
  }
  camera.position.set(300000, 1000, 300000); frame(1); await flush();
  assert.equal(scene.children.length, 1);
  assert.equal(geometryDisposals, 52);
  assert.equal(textureDisposals, 2, 'Mission still owns the shared shape texture');
  console.log('PASS: no placeholders; typed look-at jumps; terrain and shape point/wrap/no-mip sampling; hidden-pane timers; two requests; 25 chunks; disposal');
})().catch(error => { console.error(error); process.exitCode = 1; });

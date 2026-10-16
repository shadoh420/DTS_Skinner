// The RB3D page's Glide sky (static/rb3d-maps/stream.js): node tests/test_redbaron_maps_sky.cjs
// Real Three.js maths, stubbed I/O; no browser or retail assets.
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
  if (!elements.has(id)) elements.set(id, {replaceChildren() {}, addEventListener() {}, textContent: ''});
  return elements.get(id);
}};
const loaded = [];
THREE.TextureLoader.prototype.load = function (url, done) { loaded.push(url); done(new THREE.Texture()); };
vm.runInThisContext(fs.readFileSync(path.join(__dirname, '../static/rb3d-maps/stream.js'), 'utf8'));
const get = async url => ({json: async () => ({name: 'Synthetic', chunks: [], jumps: []})});
const flush = () => new Promise(resolve => setTimeout(resolve, 5));
(async () => {
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(60, 1, .2, 25000);
  const lighting = {model: 'baron-glide', toward: [0, 1, 0], intensity: 1, ambient: .5, terrainScale: 11, visibility: 1, weather: 1};
  const sky = {ring: ['ring0.png', 'ring1.png', 'ring2.png', 'ring3.png', 'ring4.png', 'ring5.png'],
    clouds: [null, 'sheet1.png', 'sheet2.png', 'sheet3.png', 'sheet4.png', 'sheet5.png']};
  camera.position.set(1000, 1372, 2000);
  const frame = await window.rb3dMaps.start({scene, camera, data: '',
    map: {regions: [{id: 0, name: 'Synthetic'}], region: 0, objects: [], viewpoints: [], lighting, sky}, get});
  frame(0); await flush(); frame(0); await flush();
  const [, ring, clouds] = scene.children;
  assert.ok(loaded.includes('textures/ring1.png') && loaded.includes('textures/sheet1.png'), 'Weather 1 picks hz-cld1 and csheet1');
  // Sixteen panels on a 20000-unit ring, centred 2500 units above the eye, 2500 tall, a chord + 2% wide.
  const position = ring.children[0].geometry.getAttribute('position'), native = .0381;
  assert.equal(position.count, 64);
  assert.ok(ring.position.equals(camera.position), 'The ring moves with the eye');
  const corner = n => new THREE.Vector3().fromBufferAttribute(position, n);
  for (let n = 0; n < 16; n++) {
    const top = corner(4 * n), bottom = corner(4 * n + 3), left = corner(4 * n), right = corner(4 * n + 1);
    assert.ok(Math.abs(top.y - 3750 * native) < 1e-3 && Math.abs(bottom.y - 1250 * native) < 1e-3);
    assert.ok(Math.abs(left.distanceTo(right) - 2 * Math.sin(Math.PI / 16) * 20000 * 1.02 * native) < 1e-3);
    const middle = left.clone().add(right).multiplyScalar(.5);
    assert.ok(Math.abs(Math.hypot(middle.x, middle.z) - 20000 * native) < 1e-3);
  }
  // Panel n shows atlas tile n mod 8: four across, two down, from v .01.
  const uv = ring.children[0].geometry.getAttribute('uv');
  assert.deepEqual([uv.getX(0), uv.getY(0), uv.getX(1), uv.getY(3)].map(v => +v.toFixed(4)), [0, .01, .25, .51]);
  assert.deepEqual([uv.getX(4 * 5), uv.getY(4 * 5)].map(v => +v.toFixed(4)), [.25, .51]);
  assert.deepEqual([uv.getX(4 * 9), uv.getY(4 * 9)].map(v => +v.toFixed(4)), [.25, .01]);
  // Weather 1 has one cloud layer at 160000 units; every sheet is 80000 units across, flat.
  assert.ok(clouds.children.length >= 1 && clouds.children.length <= 48);
  for (const mesh of clouds.children) {
    assert.ok(Math.abs(mesh.position.y - 160000 * native) <= 3 * 20000 * native + 1e-3);
    mesh.geometry.computeBoundingBox();
    const size = mesh.geometry.boundingBox.getSize(new THREE.Vector3());
    assert.ok(Math.abs(size.x - 80000 * native) < 1e-3 && size.y < 1e-6);
  }
  const first = clouds.children.map(mesh => mesh.position.toArray());
  camera.position.x += 10; frame(0);
  assert.deepEqual(clouds.children.map(mesh => mesh.position.toArray()), first, 'Same cell, same clouds');
  // Weather 3 doubles the band and has three layers; weather 0 has no cloud sheets.
  lighting.weather = 3; frame(0); await flush(); frame(0); await flush();
  const tall = ring.children[0].geometry.getAttribute('position');
  assert.ok(Math.abs(new THREE.Vector3().fromBufferAttribute(tall, 0).y - 5000 * native) < 1e-3);
  assert.equal(new Set(clouds.children.map(mesh => Math.round(mesh.position.y / native / 1000))).size > 1, true);
  lighting.weather = 0; frame(0); await flush(); frame(0); await flush();
  assert.equal(clouds.children.length, 0);
  assert.equal(ring.children.length, 1);
  console.log('PASS: sky ring placement, atlas tiles, weather switch, cloud layers');
})().catch(error => { console.error(error); process.exitCode = 1; });

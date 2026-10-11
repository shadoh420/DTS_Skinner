'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const rtcw = require('../static/rtcw-maps/viewer.js');
const matrix = rtcw.matrix([0, 90, 0], [2, 3, 4], [10, 20, 30]);
assert.ok(Math.abs(matrix[0]) < 1e-9);
assert.equal(matrix[1], 2);
assert.equal(matrix[4], -3);
assert.deepEqual(matrix.slice(12), [10, 20, 30, 1]);
assert.equal(rtcw.spinAngle(0), 0);
assert.equal(rtcw.spinAngle(4.096), 0);
assert.ok(Math.abs(rtcw.spinAngle(2.048) - 180) < .05);
console.log('RTCW map placement/slow-spin parameters passed');

// Run the shared viewer against invented draw lumps, with Three's real geometry
// classes but no DOM/browser/WebGL. Both profiles must preserve their own lighting.
function fixture(version) {
  const lumps = Array.from({length: 17}, () => Buffer.alloc(0));
  lumps[7] = Buffer.alloc(40); lumps[10] = Buffer.alloc(9 * 44); lumps[13] = Buffer.alloc(104);
  lumps[14] = Buffer.alloc(49152, 32);
  for (let i = 0; i < 9; i++) {
    const at = i * 44;
    lumps[10].writeFloatLE(i % 3, at); lumps[10].writeFloatLE(Math.floor(i / 3), at + 4);
    lumps[10].writeFloatLE(1, at + 36);
    lumps[10].fill(32, at + 40, at + 44);
  }
  lumps[13].writeInt32LE(2, 8); lumps[13].writeInt32LE(9, 16);
  lumps[13].writeInt32LE(3, 96); lumps[13].writeInt32LE(3, 100);
  const header = Buffer.alloc(144); header.write('IBSP'); header.writeInt32LE(version, 4);
  let offset = 144;
  lumps.forEach((lump, i) => { header.writeInt32LE(offset, 8 + i * 8); header.writeInt32LE(lump.length, 12 + i * 8); offset += lump.length; });
  const result = Buffer.concat([header, ...lumps]);
  return result.buffer.slice(result.byteOffset, result.byteOffset + result.byteLength);
}
async function viewer(profile) {
  const THREE = {...require('../static/three.min.js')};
  const rendered = [];
  THREE.WebGLRenderer = class {
    setPixelRatio() {} setSize() {} setRenderTarget() {} setAnimationLoop() {}
    getDrawingBufferSize(v) { return v.set(320, 240); }
    render(scene) { rendered.push(scene); }
  };
  THREE.TextureLoader = class { load(url, success) { success(new THREE.Texture()); } };
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {id, parentElement: {clientWidth: 320, clientHeight: 240},
      addEventListener() {}, after() {}, replaceChildren() {}, append() {}, matches() { return false; }});
    return elements.get(id);
  }
  let start;
  const scene = {name:'fixture', bsp:'fixture.bsp', shaders:[{name:'test',stages:[{map:'$lightmap',rgbGen:['identitylighting'],depthWrite:true}],sort:3,cull:'none'}],
    viewpoints:[{origin:[0,0,64],pitch:0,yaw:90}], models:[], items:[], itemModels:{}, unresolved:[], limits:[]};
  const model = [{vertices:[0,0,0,1,0,0,0,1,0],normals:[0,0,1,0,0,1,0,0,1],uvs:[0,0,1,0,0,1],indices:[0,1,2]}];
  if (profile) {
    scene.lighting = {identityLight:1,lightScale:4,displayScale:1,displayGamma:1,fov:90,gamma:1.3,intensity:1,hardwareGamma:false,textureMode:'GL_LINEAR_MIPMAP_LINEAR'};
    scene.itemModels.prop = {file:'models/prop.json',shaders:[{name:'test',sort:3,cull:'none',stages:[{map:'prop.png',rgbGen:['lightingdiffuse'],alphaFunc:'ge128',depthWrite:true}]}]};
    scene.placed = [{models:['prop'],origin:[10,20,30],angles:[0,90,0],scale:[2,3,4],spin:false}];
  }
  const window = {skinMap: null, addEventListener(name, fn) { if (name === 'DOMContentLoaded') start = fn; }};
  const context = {THREE, window, document:{getElementById:element,addEventListener(){},createElement:element},
    localStorage:{getItem(){return null;},setItem(){}}, devicePixelRatio:1, location:{search:''},
    ResizeObserver:class {observe(){}}, Option:class {}, URLSearchParams, performance, console,
    fetch: async url => ({ok:true,json:async()=> url.endsWith('index.json') ? [{id:'fixture',name:'fixture',group:'test'}] : url.endsWith('prop.json') ? model : scene,
      arrayBuffer:async()=>fixture(profile ? 47 : 46)})};
  vm.createContext(context);
  if (profile) vm.runInContext(fs.readFileSync(require.resolve('../static/rtcw-maps/viewer.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(require.resolve('../static/q3-maps/viewer.js'),'utf8'),context);
  await start();
  assert.equal(element('status').textContent,'Map ready · speed 400');
  const root=window.skinnerQ3Maps.scene.children[0], mesh=root.children[0];
  assert.equal(mesh.geometry.attributes.position.count,25); // 4x4 patch quads.
  assert.equal(mesh.geometry.index.count,96);
  assert.equal(mesh.material.uniforms.map.value.image.data[0],profile ? 128 : 64);
  assert.ok(mesh.material.vertexShader.includes(profile ? 'vec3(1.0)' : 'vec3(0.5)'));
  if (profile) {
    const holder=root.children[1], prop=holder.children[0];
    assert.deepEqual(holder.matrix.elements.slice(12),[10,20,30,1]);
    assert.equal(prop.geometry.attributes.position.count,3);
    assert.ok(prop.material.fragmentShader.includes('if (c.a < .5) discard;'));
    assert.equal(prop.material.uniforms.map.value.minFilter,THREE.LinearMipmapLinearFilter);
  }
  window.skinnerQ3Maps.draw(0);
  assert.equal(rendered[1].children[0].material.uniforms.displayScale.value,profile ? 1 : 2);
}
Promise.resolve().then(()=>viewer(false)).then(()=>viewer(true)).then(()=>console.log('Shared Q3/RTCW patch geometry and lighting profiles passed')).catch(error=>{console.error(error);process.exitCode=1;});

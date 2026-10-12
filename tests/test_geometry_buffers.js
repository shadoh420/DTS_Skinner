'use strict';
const assert = require('node:assert/strict');
const geometry = require('../static/geometry-buffers.js');
const THREE = require('../static/three.min.js');
const original = {
  vertices: [0,0,0, 1,0,0, 0,1,0], normals: [1/3,0,1, 0,0,1, 0,0,1],
  uvs: [0,0, 1,0, 0,1], indices: [0,1,2]
};
const arrays = {};
let offset = 0;
for (const [key, values] of Object.entries(original)) {
  const dtype = key === 'indices' ? '<u4' : key === 'normals' ? '<f8' : '<f4';
  arrays[key] = {offset, count: values.length, dtype};
  offset += values.length * (dtype === '<f8' ? 8 : 4);
}
const binary = new ArrayBuffer(offset), view = new DataView(binary);
for (const [key, item] of Object.entries(arrays)) original[key].forEach((v,i) => {
  if (item.dtype === '<u4') view.setUint32(item.offset+i*4, v, true);
  else if (item.dtype === '<f8') view.setFloat64(item.offset+i*8, v, true);
  else view.setFloat32(item.offset+i*4, v, true);
});
const model = {geometry_buffers: {file: 'a'.repeat(64)+'.bin', arrays},
  groups: [{start:0, count:3, materialIndex:0, vertexStart:0, vertexCount:3}]};
assert.equal(arrays.normals.offset % 8, 4); // float64 storage need not be aligned.
assert.deepEqual(geometry.surfaces(model, binary), [original]);
const decoded = geometry.surfaces(model, binary)[0];
const mesh = new THREE.BufferGeometry();
mesh.setAttribute('position', new THREE.Float32BufferAttribute(decoded.vertices, 3));
mesh.setIndex(decoded.indices);
assert.equal(mesh.index.count, 3);
assert.throws(() => geometry.surfaces(model, binary.slice(0,-1)), /range/);
assert.throws(() => geometry.filename({file:'../bad.bin'}), /filename/);
model.groups[0].vertexStart = 1;
assert.throws(() => geometry.surfaces(model,binary), /range/);
console.log('Binary map prop geometry, unaligned doubles and validation: OK');

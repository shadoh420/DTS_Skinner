'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const context = {window: {}, THREE: {
  RGBA_S3TC_DXT1_Format: 1, RGBA_S3TC_DXT3_Format: 3, RGBA_S3TC_DXT5_Format: 5,
  LinearFilter: 10, LinearMipmapLinearFilter: 11,
  CompressedTexture: class {constructor(mipmaps, width, height, format) {Object.assign(this, {mipmaps, image:{width,height}, format, userData:{}});}}
}};
vm.runInNewContext(fs.readFileSync('static/dds.js', 'utf8'), context);
for (const [fourcc, size, format] of [[0x31545844,8,1], [0x33545844,16,3], [0x35545844,16,5]]) {
  const buffer = new ArrayBuffer(128+size*3), v = new DataView(buffer);
  for (const [at, value] of [[0,0x20534444],[4,124],[12,4],[16,4],[28,3],[76,32],[80,4],[84,fourcc]]) v.setUint32(at,value,true);
  new Uint8Array(buffer,128).set(Array.from({length:size*3}, (_,i)=>i));
  const texture = context.window.skinnerDDS.parse(buffer);
  assert.equal(texture.format, format); assert.equal(texture.mipmaps.length,3);
  assert.deepEqual([...texture.mipmaps[0].data], [...new Uint8Array(buffer,128,size)]);
  assert.equal(texture.mipmaps[2].width,1); assert.equal(texture.flipY,false);
  assert.equal(texture.generateMipmaps,false); assert.equal(texture.userData.completeMipChain,true);
  assert.equal(texture.encoding,undefined); assert.equal(texture.colorSpace,undefined);
  assert.throws(()=>context.window.skinnerDDS.parse(buffer.slice(0,-1)),/Truncated/);
  v.setUint32(112,0x200,true); assert.throws(()=>context.window.skinnerDDS.parse(buffer),/layout/);
}
console.log('DDS authored mip/format/orientation guards passed');

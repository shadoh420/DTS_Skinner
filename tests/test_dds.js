'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const context = {window: {}, THREE: {
  RGBA_S3TC_DXT1_Format: 1, RGBA_S3TC_DXT3_Format: 3, RGBA_S3TC_DXT5_Format: 5,
  LinearFilter: 10, LinearMipmapLinearFilter: 11,
  RGBAFormat: 12,
  DataTexture: class {constructor(data,width,height,format) {Object.assign(this,{image:{data,width,height},format,userData:{}});}},
  CompressedTexture: class {constructor(mipmaps, width, height, format) {Object.assign(this, {mipmaps, image:{width,height}, format, userData:{}});}}
}};
vm.runInNewContext(fs.readFileSync('static/dds.js', 'utf8'), context);
for (const [fourcc, size, format] of [[0x31545844,8,1], [0x33545844,16,3], [0x35545844,16,5], [0x42475852,16,5]]) {
  const buffer = new ArrayBuffer(128+size*3), v = new DataView(buffer);
  for (const [at, value] of [[0,0x20534444],[4,124],[12,4],[16,4],[28,3],[76,32],[80,4],[84,fourcc]]) v.setUint32(at,value,true);
  new Uint8Array(buffer,128).set(Array.from({length:size*3}, (_,i)=>i));
  const texture = context.window.skinnerDDS.parse(buffer);
  assert.equal(texture.format, format); assert.equal(texture.mipmaps.length,3);
  assert.deepEqual([...texture.mipmaps[0].data], [...new Uint8Array(buffer,128,size)]);
  assert.equal(texture.mipmaps[2].width,1); assert.equal(texture.flipY,false);
  assert.equal(texture.generateMipmaps,false); assert.equal(texture.userData.completeMipChain,true);
  assert.equal(texture.encoding,undefined); assert.equal(texture.colorSpace,undefined);
  assert.equal(texture.userData.normalEncoding, fourcc===0x42475852 ? 'rxgb' : undefined);
  assert.throws(()=>context.window.skinnerDDS.parse(buffer.slice(0,-1)),/Truncated/);
  v.setUint32(112,0x200,true); assert.throws(()=>context.window.skinnerDDS.parse(buffer),/layout/);
}
for (const bytes of [3,4]) {
  const buffer=new ArrayBuffer(128+bytes), v=new DataView(buffer);
  for (const [at,value] of [[0,0x20534444],[4,124],[12,1],[16,1],[76,32],[80,bytes===4?0x41:0x40],[88,bytes*8],[92,0xff0000],[96,0xff00],[100,0xff],[104,bytes===4?0xff000000:0]]) v.setUint32(at,value,true);
  new Uint8Array(buffer,128).set([3,2,1,77].slice(0,bytes));
  const t=context.window.skinnerDDS.parse(buffer);
  assert.deepEqual([...t.image.data],[1,2,3,bytes===4?77:255]);
  assert.equal(t.flipY,false); assert.equal(t.mipmaps.length,1);
  v.setUint32(92,0xf800,true); assert.throws(()=>context.window.skinnerDDS.parse(buffer),/layout/);
}
// WebGL S3TC sizes: level 0 a multiple of 4, later levels 1, 2 or a multiple of 4. Others decode to RGBA.
function dxt(width, height, levels, fourcc, blocks) {
  const buffer=new ArrayBuffer(128+blocks.length), v=new DataView(buffer);
  for (const [at,value] of [[0,0x20534444],[4,124],[12,height],[16,width],[28,levels],[76,32],[80,4],[84,fourcc]]) v.setUint32(at,value,true);
  new Uint8Array(buffer,128).set(blocks); return context.window.skinnerDDS.parse(buffer);
}
const block = (c0,c1,rows) => [c0&255,c0>>8,c1&255,c1>>8,...rows];
let t = dxt(2,2,1,0x31545844,block(0xf800,0x001f,[0x04,0x0e,0,0]));  // red > blue: four colours
assert.equal(t.format,12); assert.equal(t.userData.completeMipChain,false);
assert.deepEqual([...t.mipmaps[0].data],[255,0,0,255, 0,0,255,255, 170,0,85,255, 85,0,170,255]);
t = dxt(2,2,1,0x31545844,block(0x001f,0xf800,[0x04,0x0e,0,0]));  // blue <= red: three colours + transparent black
assert.deepEqual([...t.image.data],[0,0,255,255, 255,0,0,255, 128,0,128,255, 0,0,0,0]);
t = dxt(1,1,1,0x33545844,[0x0a,0,0,0,0,0,0,0,...block(0xffff,0,[0,0,0,0])]);  // DXT3: 4-bit alpha
assert.deepEqual([...t.image.data],[255,255,255,170]);
t = dxt(1,1,1,0x42475852,[255,0,2,0,0,0,0,0,...block(0x07e0,0,[0,0,0,0])]);  // RXGB/DXT5: alpha ramp, channels kept
assert.deepEqual([...t.image.data],[0,255,0,219]); assert.equal(t.userData.normalEncoding,'rxgb');
t = dxt(2,64,7,0x31545844,Array(8*(16+8+4+2+1+1+1)).fill(0));  // Doom 3 base_trim/ppipe_s
assert.equal(t.format,12); assert.deepEqual(Array.from(t.mipmaps,m=>[m.width,m.height,m.data.length]),
  [[2,64,512],[1,32,128],[1,16,64],[1,8,32],[1,4,16],[1,2,8],[1,1,4]]);
assert.equal(t.userData.completeMipChain,true); assert.equal(t.minFilter,11);
t = dxt(12,12,2,0x31545844,Array(8*(9+4)).fill(0));  // 12 is fine at level 0, its 6x6 level is not
assert.equal(t.format,12); assert.equal(t.mipmaps[1].data.length,6*6*4);
t = dxt(8,4,4,0x31545844,Array(8*(2+1+1+1)).fill(0));  // 8x4, 4x2, 2x1, 1x1 stay compressed
assert.equal(t.format,1); assert.equal(t.mipmaps[0].data.length,16);
console.log('DDS authored mip/format/orientation/WebGL size guards passed');

'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}, THREE: {NearestFilter: 'nearest', FrontSide: 'front',
  MeshBasicMaterial: class {constructor(settings) {Object.assign(this, settings);}}}};
vm.runInNewContext(fs.readFileSync('static/azrael-maps/materials.js', 'utf8'), context);
const texture = {};
const masked = context.window.azraelMaps.material({masked: true, alpha: 1}, texture);
assert.equal(masked.alphaTest, .5);
assert.equal(masked.transparent, false);
assert.equal(masked.depthWrite, true);
assert.equal(masked.color, 0xffffff); // painted texture is not tinted by the XAF's black material colour
assert.equal(masked.fog, false);
assert.equal(masked.toneMapped, false);
assert.equal(texture.minFilter, 'nearest');
assert.equal(texture.generateMipmaps, false);
const translucent = context.window.azraelMaps.material({transparent: true, alpha: .4}, {});
assert.equal(translucent.opacity, .4);
assert.equal(translucent.depthWrite, false);
assert.equal(translucent.alphaTest, 0);
console.log('Azrael map materials: passed');

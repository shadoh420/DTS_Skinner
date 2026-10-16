// The N64 Maps page's OBJ/MTL parsing, on synthetic files only.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const test = require('node:test'), assert = require('node:assert/strict');
const context = {window: {addEventListener() {}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/n64-maps/viewer.js'), 'utf8'), context);

test('normals come from the vn index of each corner', () => {
  const buckets = context.parseObj(['v 0 0 0 1 1 1 1', 'v 1 0 0', 'v 0 1 0', 'v 1 1 0', 'vt 0 0', 'vn 0 0 1', 'vn 1 0 0',
    'usemtl m1EnvMappingClampSClampTTexScaleS0.031250TexScaleT0.031250', 'f 1/1/1 2/1/1 3/1/1', 'f 2/1/2 4/1/2 3/1/2'].join('\n'));
  const bucket = buckets.get('m1EnvMappingClampSClampTTexScaleS0.031250TexScaleT0.031250');
  assert.deepEqual([...bucket.normal], [0, 0, 1, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 1, 0, 0]);
  assert.equal(bucket.normal.length, bucket.position.length);
});

test('a file without vn lines has no normals, so the page computes face normals', () => {
  const bucket = context.parseObj('v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\nusemtl m2\nf 1 2 3 4').get('m2');
  assert.equal(bucket.position.length, 18);  // The quad is split into two triangles.
  assert.equal(bucket.normal.length, 0);
});

test('materials map to their texture base names', () => {
  const out = new Map();
  context.parseMtl('newmtl m1EnvMapping\nKd 1 1 1\nmap_Kd textures\\a.png\nnewmtl m2\n', out);
  assert.deepEqual([...out], [['m1EnvMapping', 'a.png'], ['m2', null]]);
});

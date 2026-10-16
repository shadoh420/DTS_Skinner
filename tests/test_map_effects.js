'use strict';
// Shared Unreal-family viewer effects (static/unreal-maps/viewer.js): the line-of-sight grid behind coronas, movers'
// poses, and billboard batches.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const THREE = require('../static/three.min.js');
const text = fs.readFileSync(require.resolve('../static/unreal-maps/viewer.js'), 'utf8');
const between = (start, end) => text.slice(text.indexOf(start), text.indexOf(end, text.indexOf(start)));
const close = (a, b, message) => assert.ok(Math.abs(a - b) < 1e-5, `${message}: ${a} vs ${b}`);

(async () => {
  // A wall: the square x, y in [-1, 1] at z = 0 (world group 0); a model group's square at z = 5 does not block.
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array([
    -1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0, -1, -1, 5, 1, -1, 5, 1, 1, 5, -1, 1, 5]), 3));
  geometry.setIndex([0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]);
  const map = {groups: [{start: 0, count: 6, flags: 0}, {start: 6, count: 6, flags: 0, kind: 'model'}]};
  const camera = new THREE.PerspectiveCamera(60, 1, .05, 500);
  const context = vm.createContext({THREE, map, camera, ue1: false, performance, renderer: null, scene: new THREE.Scene(),
    loadTexture: async () => new THREE.Texture()});
  vm.runInContext('const moving = [], sprites = [];' + between('  // Mirrors (UE1 MIRRORED surfaces', '  async function buildWorld') +
    ';this.occluderGrid = occluderGrid; this.updateMovers = updateMovers; this.moving = moving; this.buildSprites = buildSprites;' +
    'this.updateSprites = updateSprites; this.sprites = sprites; this.overlay = overlay;', context);
  const blocked = context.occluderGrid(geometry), V = (x, y, z) => new THREE.Vector3(x, y, z);
  assert.equal(blocked(V(0, 0, -2), V(0, 0, 2)), true, 'crosses the wall');
  assert.equal(blocked(V(.5, .5, 2), V(-.5, .2, -3)), true, 'crosses it slanted, both ways');
  assert.equal(blocked(V(3, 0, -2), V(3, 0, 2)), false, 'passes beside it');
  assert.equal(blocked(V(0, 0, 2), V(0, 0, 8)), false, 'model faces do not block');
  assert.equal(blocked(V(0, 0, -2), V(0, 0, -.5)), false, 'stops short of it');

  // Movers: a quarter turn a second about +y through (1, 0, 0); keys loop over the period, back to the first.
  const mesh = () => ({matrix: new THREE.Matrix4()});
  context.moving.push({pivot: [1, 0, 0], spin: [0, 1, 0, Math.PI / 2], mesh: mesh()},
    {keys: [[0, 0, 0, 0, 0, 0, 0, 1], [2, 0, 2, 0, 0, 0, 0, 1]], period: 4, mesh: mesh()});
  context.updateMovers(1);
  const turned = V(2, 0, 0).applyMatrix4(context.moving[0].mesh.matrix);
  close(turned.x, 1, 'spin x'); close(turned.z, -1, 'spin z');
  close(V(0, 0, 0).applyMatrix4(context.moving[1].mesh.matrix).y, 1, 'halfway to the second key');
  context.updateMovers(3);
  close(V(0, 0, 0).applyMatrix4(context.moving[1].mesh.matrix).y, 1, 'halfway back to the first key');

  // Billboards: an occluded corona batch starts hidden and fades in once the line to the eye is clear.
  map.sprites = [{texture: 'flare.png', blend: 'translucent', screen: true, occlude: true, items: [[0, 0, 3, .2, 1, .5, .25, 1]]},
    {texture: 'glow.png', blend: 'add', items: [[0, 0, -3, 1, 1, 1, 1]]}];
  await context.buildSprites(geometry);
  assert.equal(context.overlay.children.length, 1, 'screen sprites draw after the level');
  assert.equal(context.sprites.length, 2);
  assert.equal(context.sprites[1].shown[0], 1, 'plain sprites show at once');
  assert.equal(context.sprites[1].material.uniforms.additive.value, true);
  camera.position.set(0, 0, 6); camera.lookAt(0, 0, 0); camera.updateMatrixWorld();
  context.updateSprites(0); context.updateSprites(.05);
  close(context.sprites[0].shown[0], .5, 'half faded in after half the fade');
  context.updateSprites(.2);
  close(context.sprites[0].shown[0], 1, 'fully shown');
  camera.position.set(0, 0, -6); camera.lookAt(0, 0, 3); camera.updateMatrixWorld();
  context.updateSprites(.25); context.updateSprites(.5);
  close(context.sprites[0].shown[0], 0, 'behind the wall it fades out');
})().then(() => console.log('Shared viewer line of sight, movers and billboards: OK'))
  .catch(error => { console.error(error); process.exitCode = 1; });

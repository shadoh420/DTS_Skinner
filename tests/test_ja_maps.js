'use strict';
const assert = require('node:assert/strict');
const ja = require('../static/ja-maps/viewer.js');
const jk2 = require('../static/jk2-maps/viewer.js');
assert.equal(ja.data, '/ja-map-data/');
assert.equal(jk2.data, '/jk2-map-data/');
assert.equal(ja.defaultMap, 'sp_yavin1');
assert.equal(ja.diffuse, jk2.diffuse);
assert.equal(ja.stagesOf, jk2.stagesOf);
const item = {origin: [0,0,0], angles: [0,0,0], scale: [1,1,1], bob: {base: 28, rate: 0, amplitude: 4}};
assert.equal(ja.pose(item, 0)[14], 32);
delete item.bob.amplitude;
assert.equal(jk2.pose(item, 0)[14], 31);
item.bob = null;
item.pivot = [2,3,4];
item.scale = [1.5,1.5,1.5];
assert.deepEqual(ja.pose(item, 0).slice(12,15), [-2,-3,-4]);
console.log('JA map configuration and bob amplitude: OK');
// Actual Three projection: nearby geometry survives, distant geometry does not.
const THREE = require('../static/three.min.js');
global.THREE = THREE;
const texture = new THREE.Texture();
ja.configureTexture(texture, {textureMode: 'GL_LINEAR_MIPMAP_NEAREST'}, false);
assert.equal(texture.minFilter, THREE.LinearMipmapNearestFilter);
assert.equal(texture.magFilter, THREE.LinearFilter);
const camera = new THREE.PerspectiveCamera(80, 4/3, 2, 60000);
ja.configureCamera(camera, {section: 'sp', farClip: 3117.6});
assert.equal(camera.far, 3117.6);
const project = z => new THREE.Vector4(0, 0, -z, 1).applyMatrix4(camera.projectionMatrix);
assert.ok(project(3000).z < project(3000).w);
assert.ok(project(3200).z > project(3200).w);
ja.configureCamera(camera, {section: 'mp'});
assert.equal(camera.far, 10392);
assert.throws(() => ja.configureCamera(camera, {farClip: NaN}), /far clip/);
assert.equal(jk2.configureCamera, undefined);
assert.equal(jk2.skyAtFarDepth, undefined);
assert.equal(ja.skyAtFarDepth, true);
console.log('JA projection distance and JK2 isolation: OK');
// The far plane rejects whole branches/leaves, not triangles or centroids.
const tree = {
  surfaceCount: 4, world: [0, 3],
  nodes: [[-10,-10,-10,250,10,10,-1,-2]],
  leaves: [[-10,-10,-10,10,10,10,0,2], [200,-10,-10,250,10,10,2,2]],
  surfaces: [0,1,1,2],
  inline: [{first:3,count:1,corners:[[205,-1,-1],[215,1,1]]}],
};
const mask = (eye,dir,distance=100) => [...ja.visibleSurfaces(tree,eye,dir,distance)];
assert.deepEqual(mask([0,0,0],[1,0,0]), [1,1,0,0]); // Shared surface survives near leaf.
assert.deepEqual(mask([150,0,0],[1,0,0]), [1,1,1,1]); // Moving camera admits far leaf and brush.
assert.deepEqual(mask([0,0,0],[-1,0,0]), [1,1,1,1]); // Turning re-evaluates the plane.
assert.equal(ja.boundsVisible([99,0,0,101,1,1],[0,0,0],[1,0,0],100),true);
assert.equal(ja.boundsVisible([100,0,0,101,1,1],[0,0,0],[1,0,0],100),true);
assert.equal(ja.boundsVisible([101,0,0,102,1,1],[0,0,0],[1,0,0],100),false);
const branch = {...tree,nodes:[[200,-10,-10,250,10,10,-1,-2]]};
assert.deepEqual([...ja.visibleSurfaces(branch,[0,0,0],[1,0,0],100)], [0,0,0,0]);
// Branch rejection wins even when a (deliberately inconsistent) child is near.
const geometry = new THREE.BufferGeometry();
geometry.setIndex([0,1,2,3,4,5,6,7,8]);
const batch={geometry,ranges:[[0,0,3],[1,3,3],[2,6,3]],original:geometry.index.array.slice(),previous:new Uint8Array(3).fill(255)};
ja.compactBatch(batch,[1,0,1]);
assert.equal(geometry.drawRange.count,6);
assert.deepEqual([...geometry.index.array.slice(0,6)],[0,1,2,6,7,8]);
const version=geometry.index.version;
ja.compactBatch(batch,[1,0,1]);assert.equal(geometry.index.version,version); // No redundant GPU upload.
ja.compactBatch(batch,[0,0,0]);assert.equal(geometry.drawRange.count,0);
ja.compactBatch(batch,[1,1,1]);assert.deepEqual([...geometry.index.array],[0,1,2,3,4,5,6,7,8]);
const map={section:'sp',distanceCull:100/1.02,bspCull:tree};
const dynamic=new THREE.BufferGeometry();dynamic.setIndex([0,1,2]);
const sky=new THREE.BufferGeometry();sky.setIndex([0,1,2]);
const mp=new THREE.BufferGeometry();mp.setIndex([0,1,2]);
ja.bindWorldCull(map,dynamic,[[2,0,3]],{});
ja.bindWorldCull(map,sky,[[2,0,3]],{sky:true});
ja.bindWorldCull({...map,section:'mp'},mp,[[2,0,3]],{});
camera.position.set(0,0,0);camera.lookAt(1,0,0);camera.updateMatrixWorld(true);
ja.updateCull(map,camera);assert.equal(dynamic.drawRange.count,0);
assert.equal(sky.drawRange.count,Infinity);assert.equal(mp.drawRange.count,Infinity);
camera.position.x=150;camera.updateMatrixWorld(true);
ja.updateCull({...map,section:'mp'},camera);assert.equal(dynamic.drawRange.count,0);
ja.updateCull(map,camera);assert.equal(dynamic.drawRange.count,3);
assert.equal(jk2.bindWorldCull,undefined);assert.equal(jk2.updateCull,undefined);
console.log('JA SP tree, shared surfaces, camera movement, batches, sky and MP isolation: OK');
(async () => {
  const ticks=[],root=new THREE.Group(),shape=new THREE.BoxGeometry(10,10,10);
  const placed={models:['synthetic'],origin:[210,0,0],angles:[0,0,0],scale:[1,1,1]};
  const holder=await ja.addPlaced(placed,{map,root,ticks,loadItemModel:async()=>[{geometry:shape,materials:[new THREE.MeshBasicMaterial()],sort:3}]});
  camera.position.set(0,0,0);camera.lookAt(1,0,0);camera.updateMatrixWorld(true);
  ja.updateCull(map,camera);assert.equal(holder.visible,false);
  holder.matrix.setPosition(50,0,0);ja.updateCull(map,camera);assert.equal(holder.visible,true);
  // A placed model survives even if every world branch was rejected.
  ja.updateCull({...map,bspCull:branch},camera);assert.equal(holder.visible,true);
  console.log('JA separate whole-prop culling and current transforms: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});

'use strict';
const assert=require('node:assert/strict');
const v=require('../static/q3-maps/visibility.js');
const THREE=require('../static/three.min.js');
const tree={surfaceCount:4,world:[0,3],bounds:[-100,-100,-100,100,100,100],
  planes:[[1,0,0,0]],nodes:[[-100,-100,-100,100,100,100,-1,-2,0]],
  leaves:[[0,-100,-100,100,100,100,0,2,0,0],[-100,-100,-100,0,100,100,2,2,1,1]],
  surfaces:[0,2,1,2],inline:[],pvs:{numClusters:2,clusterBytes:1,bits:btoa(String.fromCharCode(1,3))}};
assert.equal(v.pointCluster(tree,[1,0,0]),0);assert.equal(v.pointCluster(tree,[-1,0,0]),1);
assert.equal(v.pointCluster(tree,[0,0,0]),1); // Exact split chooses child 1.
assert.equal(v.pointCluster(tree,[101,0,0]),-1);
assert.equal(v.create(undefined),null);assert.equal(v.create({...tree,pvs:undefined}),null);
const runtime=v.create(tree), geometry=new THREE.BufferGeometry();
geometry.setIndex([0,1,2,3,4,5,6,7,8,9,10,11]);
runtime.bind(geometry,[[0,0,3],[1,3,3],[2,6,3],[3,9,3]],{});
assert.deepEqual([...runtime.update([1,0,0],[1,0,0])],[1,0,1,1]);
assert.deepEqual([...geometry.index.array.slice(0,9)],[0,1,2,6,7,8,9,10,11]);
// Shared face and inline model survive; moving/turning within cluster does no PVS rebuild or index upload.
const version=geometry.index.version;
runtime.update([50,2,3],[-1,0,0]);assert.equal(runtime.rebuilds,1);assert.equal(geometry.index.version,version);
assert.deepEqual([...runtime.update([-1,0,0],[1,0,0])],[1,1,1,1]);assert.equal(runtime.rebuilds,2);
assert.equal(geometry.drawRange.count,12);
assert.deepEqual([...runtime.update([200,0,0],[1,0,0])],[1,1,1,1]);
const novis=v.create({...tree,pvs:{numClusters:0,clusterBytes:0,bits:''}});
assert.deepEqual([...novis.update([1,0,0],[1,0,0])],[1,1,1,1]);
const padded=v.create({...tree,pvs:{numClusters:2,clusterBytes:4,bits:btoa(String.fromCharCode(1,0,0,0,3,0,0,0))}});
assert.deepEqual([...padded.update([-1,0,0],[1,0,0])],[1,1,1,1]);
assert.deepEqual([...padded.update([1,0,0],[1,0,0])],[1,0,1,1]);
const solid={...tree,leaves:tree.leaves.map(l=>l.slice())};solid.leaves[0][8]=-1;
assert.deepEqual([...v.create(solid).update([1,0,0],[1,0,0])],[1,1,1,1]);
const sky=new THREE.BufferGeometry();sky.setIndex([0,1,2]);
runtime.bind(sky,[[1,0,3]],{sky:true});runtime.update([1,0,0],[1,0,0]);assert.equal(sky.drawRange.count,Infinity);
// A batch that finishes loading later is gated even without a cluster transition.
const late=new THREE.BufferGeometry();late.setIndex([0,1,2]);runtime.bind(late,[[1,0,3]],{});
runtime.update([1,0,0],[1,0,0]);assert.equal(late.drawRange.count,0);
// Same face in invisible near leaf + visible far leaf must disappear when combining PVS and distance.
const combined={...tree,leaves:[[50,-10,-10,60,10,10,0,2,0,0],[-10,-10,-10,0,10,10,2,2,1,1]],
 inline:[{first:3,count:1,corners:[[0,0,0],[1,1,1]]}]};
const ja=v.create(combined);
assert.deepEqual([...ja.update([1,0,0],[1,0,0],20)],[0,0,0,1]);
assert.deepEqual([...ja.update([1,0,0],[-1,0,0],20)],[1,0,1,1]);
assert.equal(ja.rebuilds,1); // Rotation changes distance cull, not the cached PVS.
const noTree=v.create({...tree,nodes:[],leaves:[]});assert.deepEqual([...noTree.update([0,0,0],[1,0,0])],[1,1,1,1]);
console.log('PVS selection, caching, batch gating, fallback, shared surfaces, inline and sky isolation, JA composition: OK');

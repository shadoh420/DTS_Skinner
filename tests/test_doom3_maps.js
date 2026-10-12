'use strict';
const assert=require('node:assert/strict');
const M=require('../static/doom3-maps/materials.js');
const tables={pulse:{values:[0,1,0],clamp:true},loop:{values:[0,1],clamp:false}};
assert.equal(M.evaluate(['*','parm0',['table','pulse','time']],.5,[2],tables),2);
assert.equal(M.evaluate(['table','loop',-.25],0,[],tables),.5);
assert.deepEqual(M.uvMatrix({uv:[['scale',2,3],['translate',.5,1]]},0,[],{}),[2,0,1,0,3,3]);
const identity=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1];
const data={areaCount:3,nodes:[{plane:[1,0,0,0],children:[-1,-2]}],portals:[
 {areas:[0,1],points:[[0,-.5,0],[0,.5,0],[.1,.5,0],[.1,-.5,0]]},
 {areas:[1,2],points:[[2,-.5,0],[2,.5,0],[3,.5,0],[3,-.5,0]]}]};
assert.deepEqual([...M.visibleAreas([1,0,0],data,identity)].sort(),[0,1]);
assert.ok(M.portalRect([[0,0,-2],[.5,0,0],[0,.5,0]],identity),'near-plane crossing is retained');
assert.equal(M.areaAt([-1,0,0],data.nodes),1);
const [s,t]=M.tangents([0,0,0,1,0,0,0,1,0],[0,0,1,0,0,1],[0,0,1,0,0,1,0,0,1],[0,1,2]);
assert.deepEqual([...s],[1,0,0,1,0,0,1,0,0]);assert.deepEqual([...t],[0,1,0,0,1,0,0,1,0]);
console.log('Doom expressions, UV transforms, portal visibility and tangent space OK');

const normal=[0,0,1,0,0,1,0,0,1,0,0,1];
M.tangents([0,0,0,1,0,0,0,1,0,0,0,3],[0,0,1,0,0,1,0,1],normal,[0,1,2,0,3,1],[{start:0,count:6}]);
assert.deepEqual(normal.slice(0,3),[0,1,0], 'dominant triangle replaces averaged normals');

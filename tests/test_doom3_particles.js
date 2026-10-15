'use strict';
const assert=require('node:assert/strict');
const P=require('../static/doom3-maps/particles.js');
// idRandom: seed = 69069*seed+1, 15-bit result.
const r=new P.Random(0);assert.equal(r.int(),1);assert.equal(r.int(),69070&0x7fff);
const base={material:'m',count:1,bunching:1,time:1,cycles:0,timeOffset:0,deadTime:0,distribution:'rect',distributionParms:[0,0,0,0],
 direction:'cone',directionParms:[0,0,0,0],orientation:'view',orientationParms:[0,0,0,0],customPath:'standard',customPathParms:Array(8).fill(0),
 speed:[0,0],rotation:[0,0],angle:90,size:[2,2],aspect:[1,1],gravity:0,worldGravity:false,offset:[1,2,3],animationFrames:0,animationRate:0,
 color:[1,.5,.25,1],fadeColor:[0,0,0,0],fadeIn:.1,fadeOut:.25,fadeIndex:0,randomDistribution:true,entityColor:false,cycleMsec:1000};
const view={left:[0,1,0],up:[0,0,1],forward:[1,0,0]},table=()=>0;
const buffers=s=>{const n=P.stageCapacity(s);return {position:new Float32Array(n*3),uv:new Float32Array(n*2),color:new Float32Array(n*4),count:0};};
let out=buffers(base);
assert.equal(P.simulate(base,500,[1,1,1,1,0,0,0,0,0],view,out,table),4);
// angle 90: left/up rotate a quarter turn; the quad stays 2 units around the offset origin.
const centre=[0,1,2].map(k=>(out.position[k]+out.position[3+k]+out.position[6+k]+out.position[9+k])/4);
assert.deepEqual(centre.map(v=>+v.toFixed(5)),[1,2,3]);
assert.deepEqual([...out.color.slice(0,4)],[...Float32Array.from([1,.5,.25,1],c=>Math.trunc(c*255)/255)],'mid-life particles use the full colour');
P.simulate(base,950,[1,1,1,1,0,0,0,0,0],view,out,table);assert.ok(out.color[0]<.25,'fade out over the last quarter');
// Gravity pulls in emitter-local -Z by gravity*age^2; speed integrates over the cone direction.
out=buffers(base);P.simulate({...base,gravity:4},500,[1,1,1,1],view,out,table);
assert.ok(Math.abs((out.position[2]+out.position[5]+out.position[8]+out.position[11])/4-(3-4*.25))<1e-5);
// cycles 1: nothing after the first cycle; bunching staggers spawn times.
assert.equal(P.simulate({...base,cycles:1},1500,[1,1,1,1],view,out,table),0);
assert.equal(P.simulate({...base,count:2},250,[1,1,1,1],view,buffers({...base,count:2}),table),4,'second particle spawns at 500 ms');
// Deterministic: same time, same particles.
const a=buffers(base),b=buffers(base),sphere={...base,distribution:'sphere',distributionParms:[10,10,10,0],angle:0};
P.simulate(sphere,321,[1,1,1,1],view,a,table);P.simulate(sphere,321,[1,1,1,1],view,b,table);assert.deepEqual(a.position,b.position);
// Animation frames cross-fade into a second quad.
out=buffers({...base,animationFrames:4});assert.equal(P.simulate({...base,animationFrames:4},500,[1,1,1,1],view,out,table),8);
assert.ok(Math.abs(out.uv[8]-out.uv[0]-.25)<1e-6);
// Aimed trails: (1+trails) quads per particle.
const aimed={...base,orientation:'aimed',orientationParms:[2,.3,0,0],speed:[100,100]};
out=buffers(aimed);assert.equal(P.simulate(aimed,500,[1,1,1,1],view,out,table),12);
assert.deepEqual(P.quadIndex(4),[0,2,3,0,3,1]);
console.log('Particles OK');
// R_ParticleDeform: per-triangle emission at a barycentric point, particle offset along the triangle's frame.
const tri=P.surfaceTriangles(new Float32Array([0,0,0, 64,0,0, 0,64,0]),new Float32Array([0,0,1,0,0,1,0,0,1]),new Float32Array([1,0,0,1,0,0,1,0,0]),new Float32Array([0,1,0,0,1,0,0,1,0]),[0,1,2]);
assert.equal(tri.total,2048);
const lift={...base,offset:[0,0,5],count:2};
assert.equal(P.stageCapacity(lift,tri,true),4,'particle2: count * area / 4096 particles');
assert.equal(P.stageCapacity(lift,tri,false),8);
out={position:new Float32Array(24),uv:new Float32Array(16),color:new Float32Array(32),count:0};
assert.equal(P.simulate(lift,500,[1,1,1,1],view,out,table,tri,false),4,'one of two bunched particles is alive');
const c=[0,1,2].map(k=>(out.position[k]+out.position[3+k]+out.position[6+k]+out.position[9+k])/4);
assert.ok(Math.abs(c[2]-5)<1e-2&&c[0]>=0&&c[1]>=0&&c[0]+c[1]<=64.01,'inside the triangle, lifted along its normal: '+c);
console.log('Particle deform OK');

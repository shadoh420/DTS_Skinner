'use strict';
const assert=require('node:assert/strict');
const M=require('../static/doom3-maps/materials.js');
const tables={pulse:{values:[0,1,0],clamp:true},loop:{values:[0,1],clamp:false}};
assert.equal(M.evaluate(['*','parm0',['table','pulse','time']],.5,[2],tables),2);
assert.equal(M.evaluate(['table','loop',-.25],0,[],tables),.5);
assert.deepEqual(M.uvMatrix({uv:[['scale',2,3],['translate',.5,1]]},0,[],{}),[2,0,1,0,3,3]);
assert.equal(M.evaluate(['!=','global2',1],0,[],{},[1,1,1]),0,'startup disables laser firing stage');
assert.equal(M.evaluate(['*','parm1','global1'],0,[1,.3],{},[1,1,1]),.3);
assert.ok(Math.abs(M.evaluate(['*','sound','parm1'],0,[1,.75],{},[],.6)-.45)<1e-12);
assert.equal(M.evaluate('sound',0,[],{}),0,'no emitter is dark');
const pcm=new Float32Array(1024);pcm[0]=-.25;pcm[1]=.25;pcm[512]=-1;pcm[513]=1;
const envelope=M.soundEnvelope(pcm),soundSource={envelope,looping:true};
const expected=Math.atan(16384/32767)/(Math.PI/4);
assert.ok(Math.abs(M.soundAmplitude(soundSource,0)-expected)<1e-6);
assert.ok(Math.abs(M.soundAmplitude(soundSource,envelope.duration)-expected)<1e-6,'looping wraps');
assert.equal(M.soundAmplitude({...soundSource,looping:false},envelope.duration),0);
assert.equal(M.soundAmplitude({...soundSource,waitForTrigger:true},0),0);
assert.equal(M.soundAmplitude({...soundSource,noFlicker:true},0),Math.atan(2)/(Math.PI/4));
assert.equal(M.soundAmplitude(soundSource,0,1),1,'constant amplitude changes only sound-reactive expressions');
assert.deepEqual(M.uvMatrix({uv:[['translate','global1',0]]},0,[],{},[0,.25]),[1,0,.25,0,1,0]);
// Alpha-only makealpha writes preserve lit RGB; the next stage uses that alpha.
function blend(stage,src,dst) {
 const f=M.ambientBlend(stage),factor=(name,k)=>({gl_zero:0,gl_one:1,gl_dst_alpha:dst[3],gl_src_alpha:src[3],gl_dst_color:dst[k],gl_src_color:src[k]})[name];
 return src.map((v,k)=>v*factor(f[k===3?2:0],k)+dst[k]*factor(f[k===3?3:1],k));
}
const masked=blend({maskcolor:true},[1,1,1,.2],[.1,.15,.2,1]);
assert.deepEqual(masked,[.1,.15,.2,.2]);
assert.deepEqual(blend({blend:['gl_dst_alpha','gl_one']},[.5,0,0,1],masked),[.2,.15,.2,.4]);
assert.deepEqual(blend({maskalpha:true},[1,0,0,1],masked),[1,0,0,.2]);
assert.deepEqual(M.ambientBlend({blend:['gl_dst_color','gl_src_color']}),['gl_dst_color','gl_src_color','gl_dst_alpha','gl_src_alpha']);
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

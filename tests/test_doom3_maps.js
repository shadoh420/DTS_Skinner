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
// RB_STD_DrawShaderPasses: every ambient stage after the interactions, by material sort, then stage sequence.
assert.ok(M.ambientOrder(-3,0)>10,'GUI/subview stages still follow interactions');
assert.ok(M.ambientOrder(0,999)<M.ambientOrder(4,0),'opaque sky before translucent windows');
assert.ok(M.ambientOrder(2,5)<M.ambientOrder(2,6));
assert.equal(M.ambientOrder(undefined,1),M.ambientOrder(0,1),'old imports without sort');
// tr_deform.cpp rebuilds. Quad in z=0 facing +z, corners not in cyclic order.
const quad={position:new Float32Array([-1,-1,0, 1,1,0, 1,-1,0, -1,1,0]),normal:new Float32Array([0,0,1,0,0,1,0,0,1,0,0,1]),
 uv:new Float32Array(8),color:new Float32Array(12).fill(1),index:new Uint32Array([0,2,1,0,1,3])};
const flareLayout=M.deformLayout('flare',quad),buffers=n=>({position:new Float32Array(n*3),uv:new Float32Array(n*2),color:new Float32Array(n*3)});
assert.equal(flareLayout.vertices,16);assert.equal(flareLayout.index.length,54);
let out=buffers(16);
assert.equal(M.deform('flare',quad,flareLayout,out,[0,0,-10],[1,0,0],[0,0,1],[4]),false,'flares vanish from behind');
assert.ok(M.deform('flare',quad,flareLayout,out,[0,0,10],[1,0,0],[0,0,1],[4]));
assert.equal(out.color[0],1,'facing flares are full bright');
for(let v=4;v<16;v++){const r=Math.hypot(out.position[v*3],out.position[v*3+1]);assert.ok(r>2.5&&r<7,'halo ring lies `spread` outside the quad: '+r);assert.equal(out.uv[v*2],0);}
M.deform('flare',quad,flareLayout,out,[3,0,10],[1,0,0],[0,0,1],[4]);for(let v=4;v<16;v++)assert.ok(out.position[v*3+2]>=-1e-6,'halo never sinks behind the flare plane');
M.deform('flare',quad,flareLayout,out,[1000,0,10],[1,0,0],[0,0,1],[4]);assert.ok(out.color[0]>0&&out.color[0]<.1,'edge-on flares fade');
assert.equal(M.deformLayout('flare',{...quad,index:new Uint32Array(3)}),null,'only single quads, like the engine');
// Sprite: view-aligned square of radius |v0-mid|/sqrt(2), standard corner texcoords.
out=buffers(4);M.deform('sprite',quad,M.deformLayout('sprite',quad),out,[0,0,10],[0,1,0],[0,0,1]);
assert.deepEqual([...out.position.slice(0,3)].map(v=>+v.toFixed(3)),[0,1,1]);assert.deepEqual([...out.uv.slice(0,4)],[0,0,1,0]);
// Tube: a 100 x 2 strip along x turns its short ends toward a viewer in its own plane.
const tube={position:new Float32Array([0,-1,0, 0,1,0, 100,1,0, 100,-1,0]),normal:quad.normal,uv:new Float32Array(8),color:quad.color,index:new Uint32Array([0,1,2,0,2,3])};
out=buffers(4);M.deform('tube',tube,M.deformLayout('tube',tube),out,[50,100,0],[1,0,0],[0,0,1]);
for(let v=0;v<4;v++){assert.ok(Math.abs(Math.abs(out.position[v*3+2])-1)<1e-6,'width now spans z');assert.ok(Math.abs(out.position[v*3+1])<1e-6);}
assert.ok(out.position[2]*out.position[11]>0,'no twist: corners 0 and 3 stay on one side');
// Turbulent: st += range*table(f), range*table(f+.5); f = offset + domain*(.003x+.007y+.011z) + offset.
const flatQuad={...tube,uv:new Float32Array(8)};out=buffers(4);
M.deform('turbulent',flatQuad,M.deformLayout('turbulent',flatQuad),out,[0,0,0],[1,0,0],[0,0,1],[2,.25,1],f=>f);
assert.ok(Math.abs(out.uv[0]-2*(.5-.007))<1e-6&&Math.abs(out.uv[1]-2*(1-.007))<1e-6,"vertex 0 is at y=-1");
console.log('Deforms OK');
// Fog plane: a 100-unit point light at z=50 has its fog top at z=150, positive above.
const fog=M.fogPlane({projection:[[0,0,0,0],[0,0,0,0],[0,0,0,1],[0,0,.005,.25]]});
assert.deepEqual(fog.map(v=>+v.toFixed(6)),[0,0,1,-150]);
// Quake 4 portal fade: open within near, black (and closed to traversal) from far.
const fadePortal={areas:[0,1],points:[[0,-.5,0],[0,.5,0],[0,.5,1],[0,-.5,1]],fade:['_black',150,200]};
assert.equal(M.portalFade(fadePortal,[100,0,.5]),0);assert.equal(M.portalFade(fadePortal,[175,0,.5]),.5);assert.equal(M.portalFade(fadePortal,[250,0,.5]),1);
assert.deepEqual([...M.visibleAreas([1,0,0],{...data,portals:[{...data.portals[0],fade:['_black',-2,-1]},data.portals[1]],portalIndexed:false,portalAdjacency:null},identity)],[0],'faded portal blocks visibility');
// Material.cpp shear: about the texture centre.
assert.deepEqual(M.uvMatrix({uv:[['shear',.5,0]]},0,[],{}),[1,.5,-.25,0,1,0]);
assert.equal(M.evaluate('ismultiplayer',0,[],{},Object.assign([],{isMultiplayer:1})),1);
assert.equal(M.evaluate('decallife',0,[],{}),0);
// Movers: idRotater spins about its origin from its spawn angles; idBobber DECELSINE amplitude; parents compose.
const apply=(m,p)=>[0,1,2].map(r=>m[r][0]*p[0]+m[r][1]*p[1]+m[r][2]*p[2]+m[r][3]);
const near=(a,b)=>a.forEach((v,i)=>assert.ok(Math.abs(v-b[i])<1e-6,a+' != '+b));
const movers=[{kind:'rotate',origin:[10,0,0],angles:[0,30,0],delta:[0,90,0],parent:null},
 {kind:'bob',origin:[0,0,0],delta:[0,0,64],start:0,duration:2000,parent:null},
 {kind:'rotate',origin:[0,0,0],angles:[0,0,0],delta:[0,90,0],parent:1}];
near(apply(M.moverMatrix(movers,0,1000),[11,0,0]),[10,1,0]);
near(apply(M.moverMatrix(movers,0,0),[11,0,0]),[11,0,0]);
near(apply(M.moverMatrix(movers,1,2000),[0,0,0]),[0,0,64*2*Math.SQRT1_2]);
near(apply(M.moverMatrix(movers,1,4000),[0,0,0]),[0,0,0]);
near(apply(M.moverMatrix(movers,2,2000),[1,0,0]),[-1,0,64*2*Math.SQRT1_2]);
near(apply(M.moverMatrix(movers,null,5),[1,2,3]),[1,2,3]);
// anglesAxis matches idAngles::ToMat3: yaw 90 turns forward (x) to y.
near(M.anglesAxis([0,90,0]).map(r=>r[0]),[0,1,0]);

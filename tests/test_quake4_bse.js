'use strict';
const assert=require('node:assert/strict');
const B=require('../static/doom3-maps/bse.js');
const near=(a,b,e=1e-6)=>a.forEach((v,i)=>assert.ok(Math.abs(v-b[i])<e,a+' != '+b));
const r=B.rng(1,2,3);const first=r();assert.equal(B.rng(1,2,3)(),first,'seeded per particle');
// Domains: point, line (shared fraction; linearSpacing by index), box surface on a face, sphere inside its box.
near(B.sample({type:'point',values:[1,2,3],flags:[]},3,r),[1,2,3]);
const line=B.sample({type:'line',values:[0,0,10,20],flags:[]},2,B.rng(9));assert.ok(Math.abs(line[1]-2*line[0])<1e-9);
near(B.sample({type:'line',values:[0,0,0,10,0,0],flags:['linearspacing']},3,r,2,5),[5,0,0]);
const face=B.sample({type:'box',values:[-1,-1,-1,1,1,1],flags:['surface']},3,B.rng(4));assert.ok(face.some(v=>Math.abs(Math.abs(v)-1)<1e-12));
const ball=B.sample({type:'sphere',values:[0,0,0,10,10,10],flags:[]},3,B.rng(5));assert.ok(ball.every(v=>v>=0&&v<=10));
const sprite=(extra={})=>({kind:'spawner',start:[0,0],duration:[0,0],count:[3,3],locked:false,constant:false,
 particle:{kind:'sprite',material:'m',duration:[2,2],gravity:[0,0],additive:false,start:{size:{type:'point',values:[10,10],flags:[]},fade:{type:'point',values:[1],flags:[]}},end:{size:{type:'point',values:[20,20],flags:[]}},motion:{size:{table:'linear',rate:null,count:null,offset:null}},...extra}});
const view={left:[0,1,0],up:[0,0,1],forward:[1,0,0]},linear=(name,f)=>Math.max(0,Math.min(1,f));
const buffers=n=>({position:new Float32Array(n*3),uv:new Float32Array(n*2),color:new Float32Array(n*4),count:0});
let effect={segments:[sprite()]},out=buffers(B.capacity(effect.segments[0]));
assert.equal(B.simulate(effect,0,1,false,view,out,linear),12,'three sprites alive mid-life');
// Diamond corners at +-left*size and +-up*size; size halfway from 10 to 20 at half life.
near(out.position.slice(0,3),[0,-15,0]);
assert.equal(B.simulate(effect,0,2.5,false,view,out,linear),0,'dead after the life');
assert.equal(B.effectPeriod(effect),2,'a spawner effect lasts its particles');
assert.equal(B.simulate(effect,0,6.5,true,view,out,linear),12,'looping replays every period');
// Emitter: 10 per second for 1 s, 0.5 s life: at 0.75 s the births from 0.3 to 0.7 are alive.
effect={segments:[{...sprite(),kind:'emitter',duration:[1,1],count:[10,10],particle:{...sprite().particle,duration:[.5,.5]}}]};
out=buffers(B.capacity(effect.segments[0]));assert.equal(B.simulate(effect,0,.75,false,view,out,linear)/4,5);
// Additive particles premultiply the fade; gravity pulls along the emitter's down vector.
effect={segments:[sprite({additive:true,gravity:[.01,.01],start:{fade:{type:'point',values:[.5],flags:[]}},end:{},motion:{}})]};
out=buffers(B.capacity(effect.segments[0]));B.simulate(effect,0,1,false,{...view,down:[0,0,-1]},out,linear);
near(out.color.slice(0,4),[.5,.5,.5,.25]);
const mid=[0,1,2].map(k=>(out.position[k]+out.position[3+k]+out.position[6+k]+out.position[9+k])/4);near(mid,[0,0,-.5*10.66],1e-4);
console.log('BSE OK');

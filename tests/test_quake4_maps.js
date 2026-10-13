const assert=require('assert');
const M=require('../static/doom3-maps/materials.js');
const unit=[1,0,0,0,1,0,0,0,1];
const close=(a,b)=>a.forEach((v,i)=>assert(Math.abs(v-b[i])<1e-10,`${a} != ${b}`));
close(M.skyMatrix(null,17),unit);
close(M.skyMatrix([0,0,0],17),unit);
// A quarter turn in 15 seconds at one revolution per minute.
close(M.skyMatrix([0,0,1],15),[0,1,0,-1,0,0,0,0,1]);
const a=30*Math.PI/180;
close(M.skyMatrix([30,0,0],0),[Math.cos(a),0,-Math.sin(a),0,1,0,Math.sin(a),0,Math.cos(a)]);
const inverseAxis=[0,1,0,-1,0,0,0,0,1];
close(M.skyMatrix([0,0,0],0,inverseAxis),inverseAxis);
const data={portalSky:{origin:[20160,3632,374]},areaSky:{_area0:false,_area1:true,_area5:true}};
assert.deepStrictEqual(M.portalSkyOrigin(data,new Set([1])),data.portalSky.origin);
assert.deepStrictEqual(M.portalSkyOrigin(data,new Set([0,5])),data.portalSky.origin);
assert.strictEqual(M.portalSkyOrigin(data,new Set([0,2])),null);
assert.strictEqual(M.portalSkyOrigin({},new Set([1])),null);

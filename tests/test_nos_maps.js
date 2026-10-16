/* Synthetic adapter check: no browser or installed game. */
const assert=require('assert');
global.window=global;global.THREE=require('../static/three.min.js');
require('../static/tas-maps/adapter.js');
require('../static/nos-maps/adapter.js');
require('../static/tvg-maps/materials.js');
const adapter=window.skinnerMapViewerConfig;
window.skinnerNosMaps={scene:new THREE.Scene(),renderer:{setClearColor(){}},camera:new THREE.PerspectiveCamera()};
(async()=>{
  assert.equal(adapter.dataRoot,'/nos-map-data/');assert.equal(adapter.importRoute,'/import_nos_maps');
  assert.equal(adapter.storageKey,'skinner.nosmaps');assert.equal(adapter.games[0][0],'nos');
  assert.equal(typeof adapter.buildInstances,'function');
  await adapter.load({preview_extent:2000,zones:[{density:.1,color:[128,128,128,255]}]});
  assert.ok(window.skinnerUnrealFog.ueFogRange.value.y>=10000);assert.equal(window.skinnerUnrealFog.ueFogMode.value,1);
  assert.ok(adapter.lightLevels({sun:{brightness:20}}).sun<=.6);
  const mat=adapter.material({texture:'missing_nos.png',settings:{blend:['gl_src_alpha','gl_one']}},null);
  assert.equal(mat.opacity,.2);assert.equal(mat.depthWrite,false);
  const grey=adapter.material({texture:'missing_nos.png',settings:{}},new THREE.Texture());
  assert.equal(grey.color.getHex(),0x999999);assert.equal(grey.map,null);
  assert.equal(grey.transparent,false);assert.equal(grey.depthWrite,true);
  assert.equal(grey.side,THREE.FrontSide);
  // A missing material must still provide opaque, raycastable ground through
  // the real shared instance builder, with the authored placement transform.
  const buffer=new ArrayBuffer(152),file='a'.repeat(64)+'.bin';
  new Float32Array(buffer,0,12).set([-10,0,-10,10,0,-10,10,0,10,-10,0,10]);
  new Float32Array(buffer,48,12).set([0,1,0,0,1,0,0,1,0,0,1,0]);
  new Uint32Array(buffer,128,6).set([0,2,1,0,3,2]);
  const model={geometry:{file,arrays:{vertices:{offset:0,count:12,dtype:'<f4'},normals:{offset:48,count:12,dtype:'<f4'},
    uvs:{offset:96,count:8,dtype:'<f4'},indices:{offset:128,count:6,dtype:'<u4'}}},store:'models',
    groups:[{start:0,count:6,texture:'missing_nos.png',settings:{}}]};
  const objects=await adapter.buildInstances({model_library:{floor:model},instances:[{look:'floor',entity:'Ground',npc:false,
    matrix:new THREE.Matrix4().makeTranslation(0,3,0).toArray()}],terrain:[],decorations:[]},'/',
    async url=>({arrayBuffer:async()=>url.endsWith(file)?buffer:new ArrayBuffer(0)}),async()=>new THREE.Texture());
  const floor=objects.find(o=>o.isMesh);floor.updateMatrixWorld(true);
  const hits=new THREE.Raycaster(new THREE.Vector3(0,5,0),new THREE.Vector3(0,-1,0)).intersectObject(floor);
  assert.ok(hits.length);assert.equal(hits[0].distance,2);assert.equal(floor.material[0].map,null);
  const sky=adapter.skyMaterial({},null);assert.equal(sky.fog,false);assert.equal(sky.depthWrite,false);
  console.log('Nosgoth shared instance adapter, sky, haze and effect material: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});

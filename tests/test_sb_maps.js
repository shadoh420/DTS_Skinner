/* Synthetic Spellbreak adapter check: no browser or installed game. */
const assert=require('assert');
global.window=global;global.THREE=require('../static/three.min.js');
require('../static/tas-maps/adapter.js');
require('../static/sb-maps/adapter.js');
require('../static/tvg-maps/materials.js');
const adapter=window.skinnerMapViewerConfig;
window.skinnerSbMaps={scene:new THREE.Scene(),renderer:{setClearColor(){}},camera:new THREE.PerspectiveCamera()};
(async()=>{
  assert.equal(adapter.dataRoot,'/sb-map-data/');assert.equal(adapter.importRoute,'/import_sb_maps');
  assert.equal(adapter.storageKey,'skinner.sbmaps');assert.equal(adapter.games[0][0],'sb');
  const light=adapter.lightLevels({sun:{brightness:1}});
  assert.ok(light.ambient>=.5&&light.sun>=.3&&light.sun<=.8);
  // UE4 exponential height fog uses the shared HeightFogCommon path (mode 2).
  await adapter.load({preview_extent:4500,zones:[{kind:'ExponentialHeightFogComponent',density:.03,color:[31,57,81,255],height:400,height_falloff:.2}]});
  assert.equal(window.skinnerUnrealFog.ueFogMode.value,2);
  // Placed meshes arrive as instanced decoration batches with an authored cull distance.
  const buffer=new ArrayBuffer(152),file='b'.repeat(64)+'.bin';
  new Float32Array(buffer,0,12).set([-1,0,-1,1,0,-1,1,0,1,-1,0,1]);
  new Float32Array(buffer,48,12).set([0,1,0,0,1,0,0,1,0,0,1,0]);
  new Uint32Array(buffer,128,6).set([0,2,1,0,3,2]);
  const model={source:'/Game/Rock.Rock',geometry:{file,arrays:{vertices:{offset:0,count:12,dtype:'<f4'},normals:{offset:48,count:12,dtype:'<f4'},
    uvs:{offset:96,count:8,dtype:'<f4'},indices:{offset:128,count:6,dtype:'<u4'}}},store:'models',
    groups:[{start:0,count:6,texture:'rock.dds',settings:{}}]};
  const matrices=new Float32Array(32);matrices.set(new THREE.Matrix4().makeTranslation(5,0,0).toArray(),0);
  matrices.set(new THREE.Matrix4().makeTranslation(-5,0,0).toArray(),16);
  const blob=new Response(new Blob([matrices]).stream().pipeThrough(new CompressionStream('gzip')));
  const gz=await blob.arrayBuffer();
  const objects=await adapter.buildInstances({id:'m',model_library:{rock:model},instances:[],terrain:[],
    decorations:[{look:'rock',entity:'Tile',offset:0,count:2,decorationType:'Placed meshes',fade:[0,5250]}]},'/',
    async url=>url.endsWith('decorations.bin.gz')?new Response(gz):({arrayBuffer:async()=>url.endsWith(file)?buffer:new ArrayBuffer(0)}),
    async()=>new THREE.Texture());
  const batch=objects.find(o=>o.isInstancedMesh);
  assert.equal(batch.count,2);assert.equal(batch.userData.decorationType,'Placed meshes');
  const far=new THREE.PerspectiveCamera();far.position.set(0,0,500);batch.onBeforeRender(null,null,far);assert.equal(batch.count,0);
  const near=new THREE.PerspectiveCamera();batch.onBeforeRender(null,null,near);assert.equal(batch.count,2);
  console.log('Spellbreak adapter: fog, light levels, instanced batches with cull distance: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});

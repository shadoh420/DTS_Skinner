const assert=require('assert');
const {arrays}=require('../static/eye-maps/materials.js');
const description={file:'a'.repeat(64)+'.bin',arrays:{vertices:{dtype:'<f4',offset:0,count:3},indices:{dtype:'<u4',offset:12,count:1}}};
const buffer=new ArrayBuffer(16);new Float32Array(buffer,0,3).set([1,2,3]);
assert.deepEqual([...arrays(description,buffer).vertices],[1,2,3]);
assert.throws(()=>arrays({...description,file:'../x'},buffer));
assert.throws(()=>arrays(description,buffer.slice(0,12)));

// Actual vendored Three objects: no renderer, browser or GPU context.
global.THREE=require('../static/three.min.js');
delete require.cache[require.resolve('../static/eye-maps/materials.js')];
require('../static/eye-maps/materials.js');
const adapter=global.skinnerMapViewerConfig;
async function testInstances(){
  const geometry={file:'b'.repeat(64)+'.bin',arrays:{
    vertices:{dtype:'<f4',offset:0,count:9},normals:{dtype:'<f4',offset:36,count:9},
    uvs:{dtype:'<f4',offset:72,count:6},indices:{dtype:'<u4',offset:96,count:3}}};
  const binary=new ArrayBuffer(108),lights=new Float32Array(27).fill(.25);
  lights.fill(.75,9,18);
  const group={start:0,count:3,kind:'model',texture:'a.dds',settings:{}};
  const instance={look:'one',lighting:{offset:0,count:3},matrix:[1,0,0,0,0,1,0,0,0,0,1,0,2,3,4,1],entity:10,npc:true,classname:'npc_test'};
  const map={id:'synthetic',model_library:{one:{geometry,groups:[group]},two:{geometry,groups:[group]}},
    instances:[instance,{...instance,look:'two',entity:11,npc:false,lighting:{offset:36,count:3}},
      {...instance,entity:12,lighting:{offset:72,count:3}}]};
  const requests=[],texture=new THREE.Texture();adapter.exposure=1;
  const get=async url=>{requests.push(url);return{arrayBuffer:async()=>url.endsWith('lighting.bin')?lights.buffer:binary};};
  const meshes=await adapter.buildInstances(map,'/data/',get,async()=>texture);
  assert.equal(requests.filter(x=>x.includes('/models/')).length,1);
  assert.strictEqual(meshes[0].geometry.attributes.position,meshes[1].geometry.attributes.position);
  assert.strictEqual(meshes[0].geometry.index,meshes[1].geometry.index);
  assert.equal(meshes[1].geometry.attributes.bakedLight.array[0],.75);
  assert.notStrictEqual(meshes[0].geometry.attributes.bakedLight,meshes[1].geometry.attributes.bakedLight);
  assert.strictEqual(meshes[0].material,meshes[2].material);
  assert.strictEqual(meshes[0].material[0].uniforms.surface.value,texture);
  assert.deepEqual(meshes[0].matrix.elements.slice(12,15),[2,3,4]);
  assert.equal(meshes[0].matrixAutoUpdate,false);
  assert.deepEqual(meshes.map(m=>m.userData.npc),[true,false,true]);
  map.instances[0]={...instance,lighting:{offset:0,count:10}};
  await assert.rejects(()=>adapter.buildInstances(map,'/data/',get,async()=>texture),/Invalid instance lighting/);
}
testInstances().then(()=>console.log('EYE shared geometry, skin buffers, per-instance lighting, NPC tags: OK')).catch(error=>{console.error(error);process.exitCode=1;});

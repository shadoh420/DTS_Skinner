/* CPU-only checks with the repository's Three.js; no browser or renderer. */
const assert=require('assert');
const {gzipSync}=require('zlib');
const {arrays,terrainArrays}=require('../static/tvg-maps/materials.js');
const desc={file:'a'.repeat(64)+'.bin',arrays:{vertices:{offset:0,count:9,dtype:'<f4'},normals:{offset:36,count:9,dtype:'<f4'},uvs:{offset:72,count:6,dtype:'<f4'},indices:{offset:96,count:3,dtype:'<u4'}}};
const buffer=new ArrayBuffer(108);new Float32Array(buffer,0,9).set([0,0,0,0,0,1,-1,0,0]);new Uint32Array(buffer,96,3).set([0,2,1]);
assert.equal(arrays(desc,buffer).vertices.length,9);
assert.throws(()=>arrays({...desc,file:'../x'},buffer));
assert.throws(()=>arrays(desc,buffer.slice(0,100)));
const terrain={width:2,height:2,offset:0,bytes:8,visibility_bytes:4,edge_bytes:4,to_world:[0,0,32768,52.5,0,0,0,52.5,0,0,0,.25],
  layers:[{texture:'ground.dds',alpha:'mask.png',scale:[1,1],pan:[0,0],rotation:0}],textures:{},vertex_light:'light.png'};
const grid=new ArrayBuffer(16);new Uint16Array(grid,0,4).fill(32768);new Uint8Array(grid)[8]=1;
const a=terrainArrays(terrain,grid);
assert.deepEqual([...a.positions],[0,0,0,0,0,1,-1,0,0,-1,0,1]);
assert.deepEqual([...a.indices],[0,2,1,1,2,3]);
new Uint8Array(grid)[12]=1;assert.deepEqual([...terrainArrays(terrain,grid).indices],[0,3,1,0,2,3]);
new Uint8Array(grid)[8]=0;assert.equal(terrainArrays(terrain,grid).indices.length,0);new Uint8Array(grid)[8]=1;
assert.throws(()=>terrainArrays({...terrain,width:3},grid),/Invalid terrain/);

global.THREE=require('../static/three.min.js');
delete require.cache[require.resolve('../static/tvg-maps/materials.js')];
require('../static/tvg-maps/materials.js');
const adapter=global.skinnerMapViewerConfig;
global.skinnerTvgMaps={scene:new THREE.Scene(),renderer:{setClearColor(){}}};
async function run(){
  const group={start:0,count:3,materialIndex:0,kind:'model',texture:'model.dds',settings:{}};
  const matrix=[1,0,0,0,0,1,0,0,0,0,1,0,2,3,4,1];
  const instance={look:'one',matrix,entity:4,npc:true,classname:'AI.Hero',colors:{offset:0,count:3}};
  const map={id:'test',zones:[],model_library:{one:{geometry:desc,groups:[group]},two:{geometry:desc,groups:[group]}},
    instances:[instance,{...instance,look:'two',entity:5,npc:false,colors:null}],terrain:[terrain],
    decorations:[{look:'one',entity:8,count:2,offset:0,fade:[0,1000]}]};
  const mats=new Float32Array([...matrix,...matrix]);mats[28]=20;
  const light=new Uint8Array([10,20,30,255,40,50,60,255,70,80,90,255]);
  const requests=[],texture=new THREE.Texture();
  const get=async url=>{requests.push(url);return new Response(url.endsWith('.gz')?gzipSync(new Uint8Array(mats.buffer)):
    url.endsWith('lighting.bin')?light:url.endsWith('terrain.bin')?grid:buffer);};
  await adapter.load(map);
  const objects=await adapter.buildInstances(map,'/data/',get,async()=>texture);
  const [one,two,ground,deco]=objects;
  assert.equal(requests.filter(x=>x.includes('/models/')).length,1);
  assert.strictEqual(one.geometry.attributes.position,two.geometry.attributes.position);
  assert.strictEqual(one.geometry.attributes.position,deco.geometry.attributes.position);
  assert.strictEqual(one.geometry.index,two.geometry.index);
  assert.deepEqual([...one.geometry.attributes.color.array],[30,20,10,60,50,40,90,80,70]);
  assert.equal(two.geometry.attributes.color,undefined);
  assert.deepEqual(one.matrix.elements,matrix);assert.equal(one.matrixAutoUpdate,false);
  assert.equal(one.userData.npc,true);assert.equal(two.userData.npc,false);assert.equal(deco.userData.npc,false);
  assert.equal(deco.count,2);assert.equal(deco.frustumCulled,true);
  assert.ok(deco.geometry.boundingSphere.containsPoint(new THREE.Vector3(20,3,4)));
  assert.ok(ground.material.fragmentShader.includes('alpha0'));
  assert.ok(ground.material.uniforms.light.value);
  const camera=new THREE.PerspectiveCamera();camera.position.set(1e5,1e5,1e5);
  deco.onBeforeRender(null,null,camera);assert.equal(deco.count,0);
  camera.position.set(2,3,4);deco.onBeforeRender(null,null,camera);assert.equal(deco.count,2);
  map.instances[0]={...instance,colors:{offset:0,count:100}};
  await assert.rejects(()=>adapter.buildInstances(map,'/data/',get,async()=>texture),/Invalid vertex lighting/);
}
run().then(()=>console.log('TVG compact terrain, shared buffers, lighting, decoration bounds and NPC tags: OK'))
  .catch(error=>{console.error(error);process.exitCode=1;});

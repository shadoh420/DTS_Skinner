/* Synthetic CPU checks only; no browser, WebGL context or retail data. */
const assert=require('assert');
global.window=global;
global.THREE=require('../static/three.min.js');
require('../static/tas-maps/adapter.js');
const {decorationToggles}=require('../static/tvg-maps/materials.js');
const adapter=global.skinnerMapViewerConfig;
global.skinnerTasMaps={scene:new THREE.Scene(),renderer:{setClearColor(){}}};

async function run(){
  assert.equal(adapter.dataRoot,'/tas-map-data/');
  assert.equal(adapter.storageKey,'skinner.tasmaps');
  await adapter.load({zones:[{kind:'HeightFogComponent',density:.001,color:[32,64,128,255],start:100}]});
  assert.ok(global.skinnerTasMaps.scene.fog.far>global.skinnerTasMaps.scene.fog.near);
  const layer={texture:'diffuse.dds',alpha:'weight.png',matrix:new THREE.Matrix4().toArray().reduce((a,v,i)=>{if(i%4===0)a.push([]);a.at(-1).push(v);return a;},[])};
  const material=await adapter.terrainMaterial({layers:Array(5).fill(layer),textures:{}},async()=>new THREE.Texture());
  assert.ok(material.fragmentShader.includes('texture2D(layer4'));
  assert.ok(material.uniforms.alpha4.value);
  assert.ok(!material.fragmentShader.includes('layer5'));

  const desc={file:'a'.repeat(64)+'.bin',arrays:{vertices:{offset:0,count:9,dtype:'<f4'},normals:{offset:36,count:9,dtype:'<f4'},uvs:{offset:72,count:6,dtype:'<f4'},indices:{offset:96,count:3,dtype:'<u4'}}};
  const buffer=new ArrayBuffer(108);new Float32Array(buffer).set([0,0,0,1,0,0,0,1,0]);new Uint32Array(buffer,96).set([0,1,2]);
  const model={store:'map-models',geometry:desc,groups:[{start:0,count:3,materialIndex:0,texture:'diffuse.dds',settings:{}}]};
  const map={id:'test',model_library:{one:model},instances:[{look:'one',matrix:new THREE.Matrix4().toArray(),entity:'turret',npc:true}],terrain:[],decorations:[],sun:{direction:[0,1,0],color:[1,.5,.2],brightness:2}};
  const requests=[];
  const objects=await adapter.buildInstances(map,'/tas-map-data/',async url=>{requests.push(url);return new Response(url.endsWith('lighting.bin')?new ArrayBuffer(0):buffer);},async()=>new THREE.Texture());
  assert.ok(requests.includes('/tas-map-data/map-models/'+desc.file));
  assert.equal(objects[0].userData.npc,true);
  assert.equal(objects.at(-1).intensity,2);

  const element=()=>({children:[],events:{},append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},addEventListener(name,fn){this.events[name]=fn;}});
  const panel=element(),list=element(),saved=new Map();panel.hidden=true;
  global.document={getElementById:id=>id==='decorPanel'?panel:list,createElement:element};
  global.localStorage={getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)};
  const tree={visible:true,userData:{decorationType:'SpeedTree stand-in / Tree',instances:13}};
  const grass={visible:true,userData:{decorationType:'Grass',instances:4}};
  decorationToggles([tree,grass],'skinner.tasmaps.hiddenDecorations');
  const all=list.children[0].children[0];all.checked=false;all.events.change();
  assert.equal(tree.visible,false);assert.equal(grass.visible,false);
  assert.ok(saved.has('skinner.tasmaps.hiddenDecorations'));
  assert.ok(!saved.has('skinner.tvgmaps.hiddenDecorations'));
  decorationToggles([tree,grass],'skinner.tasmaps.hiddenDecorations');
  assert.equal(list.children.length,3);assert.equal(tree.visible,false);
}
run().then(()=>console.log('TAS five terrain layers, shared instance adapter, NPCs and Decorations: OK'))
  .catch(error=>{console.error(error);process.exitCode=1;});

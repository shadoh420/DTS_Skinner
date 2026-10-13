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
  assert.ok(window.skinnerNosMaps.scene.fog.far>=10000);
  assert.ok(adapter.lightLevels({sun:{brightness:20}}).sun<=.6);
  const mat=adapter.material({texture:'missing_nos.png',settings:{blend:['gl_src_alpha','gl_one']}},null);
  assert.equal(mat.opacity,.2);assert.equal(mat.depthWrite,false);
  const sky=adapter.skyMaterial({},null);assert.equal(sky.fog,false);assert.equal(sky.depthWrite,false);
  console.log('Nosgoth shared instance adapter, sky, haze and effect material: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});

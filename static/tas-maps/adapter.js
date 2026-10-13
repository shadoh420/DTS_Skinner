/* Ascend supplies presentation settings; Vengeance's adapter owns shared buffers,
   the five-layer terrain shader, instancing and the per-type Decorations panel. */
window.skinnerInstancedMapOptions={
  label:'Tribes: Ascend',storageKey:'skinner.tasmaps',debugName:'skinnerTasMaps',
  games:[['tas','C:\\Games\\Tribes_Ascend_Parting_Gifts']],
  dataRoot:'/tas-map-data/',importRoute:'/import_tas_maps',computeNormals:true,filterUnavailable:true,
  lightLevels(map) {
    return {ambient:.35,sun:Math.min(.6,Math.max(0,(map.sun?.brightness??1)*.2))};
  },
  skyMaterial(group,texture) {
    const material=new THREE.MeshBasicMaterial({map:texture,color:texture?0xffffff:0x7f98aa,
      side:THREE.DoubleSide,fog:false,depthWrite:false,depthTest:false,transparent:false});
    // Opaque render queue + negative object order puts sky before opaque and translucent scenery.
    // Keep projected XY, but put depth inside the far clip plane regardless of dome size.
    material.onBeforeCompile=shader=>{shader.vertexShader=shader.vertexShader.replace('#include <project_vertex>',
      '#include <project_vertex>\ngl_Position.z=gl_Position.w*.999999;');};
    material.customProgramCacheKey=()=> 'tas-sky-far-depth-v1';
    return material;
  },
  async load(map) {
    const {scene,renderer,camera}=window.skinnerTasMaps;
    scene.background=null;scene.fog=null;renderer.setClearColor(0x7f98aa);
    const span=Math.max(500,map.preview_extent||3000);
    if(camera){camera.far=Math.max(5000,span*3);camera.updateProjectionMatrix();}
    const fog=map.zones?.[0];
    if(fog&&fog.density>0) {
      const c=fog.color||[128,128,128,255],bytes=c.some(v=>v>1);
      const color=bytes?new THREE.Color(c[2]/255,c[1]/255,c[0]/255):new THREE.Color(...c.slice(0,3));
      // ponytail: UE3 height integration is not reproduced; bound a light distance haze
      // by the playable extent. Keep authored colour/start and preserve native data in JSON.
      const near=Math.max(fog.start/52.5,span*.35),far=Math.max(near+span*4,span*5);
      scene.fog=new THREE.Fog(color,near,far);renderer.setClearColor(color);
    }
  }
};

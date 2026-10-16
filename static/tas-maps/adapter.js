/* Ascend supplies presentation settings; Vengeance's adapter owns shared buffers,
   the five-layer terrain shader, instancing and the per-type Decorations panel. */
window.createUE3MapOptions=(options={})=>({
  label:'Tribes: Ascend',storageKey:'skinner.tasmaps',debugName:'skinnerTasMaps',
  games:[['tas','C:\\Games\\Tribes_Ascend_Parting_Gifts']],
  dataRoot:'/tas-map-data/',importRoute:'/import_tas_maps',computeNormals:true,filterUnavailable:true,
  lightLevels(map) {
    return {ambient:.35,sun:Math.min(.6,Math.max(0,(map.sun?.brightness??1)*.2))};
  },
  // Translucent effects whose graph gives no texture (water, forcefields, fog volumes, beams) draw as a faint
  // tint, not the missing-texture checker or a solid emissive constant.
  groupMaterial(group) {
    const file=group.texture||'',rgb=/^constant_(\d+)_(\d+)_(\d+)_/.exec(file);
    if(!group.settings?.blend?.length||!(rgb||file.startsWith('missing_')))return null;
    return new THREE.MeshBasicMaterial({color:rgb?new THREE.Color(rgb[1]/255,rgb[2]/255,rgb[3]/255):0x6f8fa0,
      transparent:true,opacity:.2,depthWrite:false,side:THREE.DoubleSide});
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
  // Vengeance's shared load() reads emissive textures, then calls this.
  prepare(map) {
    const {scene,renderer,camera}=window[options.debugName||'skinnerTasMaps'],fog=window.skinnerUnrealFog;
    scene.background=null;scene.fog=null;renderer.setClearColor(0x7f98aa);fog.ueFogMode.value=0;
    const span=Math.max(500,map.preview_extent||3000);
    if(camera){camera.far=Math.max(5000,span*3);camera.updateProjectionMatrix();}
    const rgb=c=>c.some(v=>v>1)?[c[2]/255,c[1]/255,c[0]/255]:c.slice(0,3);  // FColor B, G, R, A
    const height=map.zones?.find(z=>z.kind==='ExponentialHeightFogComponent'&&z.enabled!==false&&z.density>0);
    if(height) {
      // UE3 setup of HeightFogCommon.usf's parameters (its C++ is not shipped). Hypothesis, as in Flax's port of the same
      // model (ExponentialHeightFog::GetExponentialHeightFogData): density and falloff / 1000, min opacity
      // 1 - FogMaxOpacity, colours times their brightness; the light vector is the map's sun (the importer's dominant
      // directional light). Data check: Phlegethon's 0.05 per unit unscaled would be opaque within 60 units.
      const inscatter=rgb(height.color||[128,128,128,255]).map(v=>v*(height.LightInscatteringBrightness??1));
      const opposite=height.OppositeLightColor?rgb(height.OppositeLightColor).map(v=>v*(height.OppositeLightBrightness??1)):inscatter;
      fog.ueFogMode.value=2;fog.ueFogColor.value.fromArray(inscatter);fog.ueFogOpposite.value.fromArray(opposite);
      fog.ueFogParams.value.set(height.density/1000*52.5,Math.max(height.height_falloff??.2,1e-4)/1000*52.5,
        Math.cos((height.LightTerminatorAngle??0)*Math.PI/180),(height.start||0)/52.5);
      fog.ueFogHeight.value=(height.height||0)/52.5;fog.ueFogMinOpacity.value=1-(height.FogMaxOpacity??1);
      fog.ueFogLight.value.fromArray(map.sun?.direction||[0,1,0]).normalize();
      renderer.setClearColor(fog.ueFogColor.value);
      return;
    }
    const layer=map.zones?.[0];
    if(layer&&layer.density>0) {
      // ponytail: legacy HeightFog layers (7 Ascend maps) keep a light distance haze bounded by the playable extent.
      const near=Math.max((layer.start??0)/52.5,span*.35);
      fog.ueFogMode.value=1;fog.ueFogColor.value.fromArray(rgb(layer.color||[128,128,128,255]));fog.ueFogRange.value.set(near,Math.max(near+span*4,span*5));
      renderer.setClearColor(fog.ueFogColor.value);
    }
  },
  ...options
});
window.skinnerInstancedMapOptions=window.createUE3MapOptions();

/* Ascend supplies presentation settings; Vengeance's adapter owns shared buffers,
   the five-layer terrain shader, instancing and the per-type Decorations panel. */
window.skinnerInstancedMapOptions={
  label:'Tribes: Ascend',storageKey:'skinner.tasmaps',debugName:'skinnerTasMaps',
  games:[['tas','C:\\Games\\Tribes_Ascend_Parting_Gifts']],
  dataRoot:'/tas-map-data/',importRoute:'/import_tas_maps',computeNormals:true,filterUnavailable:true,
  async load(map) {
    const {scene,renderer}=window.skinnerTasMaps;
    scene.background=null;renderer.setClearColor(0x7f98aa);
    const fog=map.zones?.[0];
    if(fog&&fog.density>0) {
      const c=fog.color||[128,128,128,255],bytes=c.some(v=>v>1);
      const color=bytes?new THREE.Color(c[2]/255,c[1]/255,c[0]/255):new THREE.Color(...c.slice(0,3));
      const density=fog.density*(fog.kind==='ExponentialHeightFogComponent' ? .001 : 1);
      const near=fog.start/52.5,far=Math.max(near+1,1/Math.max(density,1e-9)/52.5);
      scene.fog=new THREE.Fog(color,near,far);renderer.setClearColor(color);
    }
  }
};

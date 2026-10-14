/* Share UE3 sky, light/haze limits and faint translucent-effect previews. */
const nosUE3Options=window.createUE3MapOptions();
window.skinnerInstancedMapOptions=window.createUE3MapOptions({
  label:'Nosgoth',storageKey:'skinner.nosmaps',debugName:'skinnerNosMaps',
  games:[['nos','C:\\Program Files (x86)\\Steam\\steamapps\\common\\nosgoth']],
  dataRoot:'/nos-map-data/',importRoute:'/import_nos_maps',
  groupMaterial(group,texture) {
    const effect=nosUE3Options.groupMaterial(group,texture);
    if(effect)return effect;
    if((group.texture||'').startsWith('missing_'))return new THREE.MeshLambertMaterial({
      color:0x999999,side:group.settings?.cull==='none'?THREE.DoubleSide:THREE.FrontSide});
    return null;
  }
});

/* Spellbreak (UE 4.22) on the shared UE3 options: sky, exponential height fog, faint translucent effects.
   Every placed mesh is an instanced batch, so the Decorations panel lists "Placed meshes" and each foliage type. */
window.skinnerInstancedMapOptions=window.createUE3MapOptions({
  label:'Spellbreak',storageKey:'skinner.sbmaps',debugName:'skinnerSbMaps',
  games:[['sb','C:\\Games\\Spellbreak\\client']],
  dataRoot:'/sb-map-data/',importRoute:'/import_sb_maps',
  // Toon-shaded game with bright baked light: a stronger fixed ambient than the UE3 pages.
  lightLevels(map) { return {ambient:.6,sun:Math.min(.8,Math.max(.3,(map.sun?.brightness??1)*.6))}; }
});

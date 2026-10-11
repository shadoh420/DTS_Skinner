/* Azrael previews: painted light, per-bundle palettes, # colour-key cutouts. */
'use strict';
window.azraelMaps = {
  material(group, texture) {
    if (texture) {
      texture.magFilter = texture.minFilter = THREE.NearestFilter;
      texture.generateMipmaps = false;
      texture.anisotropy = 1;
      texture.needsUpdate = true;
    }
    return new THREE.MeshBasicMaterial({map: texture, color: texture ? 0xffffff : 0x808080,
      side: THREE.FrontSide, opacity: group.alpha === undefined ? 1 : group.alpha,
      transparent: !!group.transparent, depthWrite: !group.transparent,
      alphaTest: group.masked ? .5 : 0, fog: false, toneMapped: false});
  }
};

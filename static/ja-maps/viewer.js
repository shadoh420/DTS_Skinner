/* JA uses Raven geometry, lighting and controls from the JK2 viewer. */
'use strict';
(function (host) {
  const config = typeof module === 'object' && module.exports
    ? {...require('../jk2-maps/viewer.js')} : {...host.skinnerMapConfig};
  Object.assign(config, {
    title: 'Jedi Academy Maps', gameName: 'Jedi Academy', storageKey: 'skinner.jamaps',
    data: '/ja-map-data/', importRoute: '/import_ja_maps', defaultMap: 'sp_yavin1',
    // Global fog is GL exp2 fog with r_drawfog 2; fog passes also cover decals and see-through shaders.
    skyAtFarDepth: true, glGlobalFog: true, fogEqualSort: 5,
    flareFaces: 'world',  // r_flares 1 in jaconfig.cfg and jampconfig.cfg.
  });
  config.configureCamera = (camera, map) => {
    // Old scene files need reimport for worldspawn overrides; section defaults
    // still prevent the former shared 60000-unit far plane.
    const far = map.farClip ?? (map.section === 'mp' ? 6000 : 12000) * 1.732;
    if (!Number.isFinite(far) || far < 2048) throw new Error('Invalid JA far clip');
    camera.far = far;
    camera.updateProjectionMatrix();
  };
  const visibility = typeof module === 'object' && module.exports
    ? require('../q3-maps/visibility.js') : host.skinnerVisibility;
  config.boundsVisible = visibility.boundsVisible;
  config.visibleSurfaces = visibility.visibleSurfaces;
  config.worldCullDistance = map => map.section === 'sp' ? map.distanceCull * 1.02 : null;
  const batches = [], props = [];
  config.bindWorldCull = (map, geometry, ranges, shader) => {
    if (map.section !== 'sp' || !map.bspCull || shader.sky) return;
    batches.push({geometry, ranges, original: geometry.index.array.slice(), previous: new Uint8Array(ranges.length).fill(255)});
  };
  config.compactBatch = visibility.compactBatch;
  const addPlaced = config.addPlaced;
  config.addPlaced = async (item, context) => {
    const holder = await addPlaced(item, context);
    if (context.map?.section === 'sp' && context.map.bspCull) {
      const bounds = new THREE.Box3();
      for (const mesh of holder.children) {
        mesh.geometry.computeBoundingBox();bounds.union(mesh.geometry.boundingBox);
      }
      if (!bounds.isEmpty()) props.push({holder, corners: Array.from({length: 8}, (_, i) => new THREE.Vector3(
        i & 1 ? bounds.max.x : bounds.min.x, i & 2 ? bounds.max.y : bounds.min.y, i & 4 ? bounds.max.z : bounds.min.z))});
    }
    return holder;
  };
  let visible, forward;
  config.updateCull = (map, camera) => {
    if (map?.section !== 'sp' || !map.bspCull) return;
    forward ||= new THREE.Vector3();camera.getWorldDirection(forward);
    const eye = [camera.position.x, -camera.position.z, camera.position.y];
    const direction = [forward.x, -forward.z, forward.y], distance = map.distanceCull * 1.02;
    if (!map.bspCull.pvs) {
      visible = config.visibleSurfaces(map.bspCull, eye, direction, distance, visible);
      for (const batch of batches) config.compactBatch(batch, visible);
    }
    // Props use their current pose/transform, never a world leaf's visibility.
    for (const {holder, corners} of props) {
      const m = holder.matrix.elements;
      holder.visible = corners.some(p => direction.reduce((sum, d, j) =>
        sum + d * (m[j]*p.x + m[4+j]*p.y + m[8+j]*p.z + m[12+j] - eye[j]), 0) <= distance);
    }
  };
  if (typeof module === 'object' && module.exports) module.exports = config;
  else host.skinnerMapConfig = config;
})(typeof window === 'object' ? window : globalThis);

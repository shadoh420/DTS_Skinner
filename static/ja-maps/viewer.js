/* JA uses Raven geometry, lighting and controls from the JK2 viewer. */
'use strict';
(function (host) {
  const config = typeof module === 'object' && module.exports
    ? {...require('../jk2-maps/viewer.js')} : {...host.skinnerMapConfig};
  Object.assign(config, {
    title: 'Jedi Academy Maps', gameName: 'Jedi Academy', storageKey: 'skinner.jamaps',
    data: '/ja-map-data/', importRoute: '/import_ja_maps', defaultMap: 'sp_yavin1',
    skyAtFarDepth: true,
  });
  config.configureCamera = (camera, map) => {
    // Old scene files need reimport for worldspawn overrides; section defaults
    // still prevent the former shared 60000-unit far plane.
    const far = map.farClip ?? (map.section === 'mp' ? 6000 : 12000) * 1.732;
    if (!Number.isFinite(far) || far < 2048) throw new Error('Invalid JA far clip');
    camera.far = far;
    camera.updateProjectionMatrix();
  };
  // Native game axes. Reject only when the entire box lies past the fifth plane.
  config.boundsVisible = (bounds, eye, direction, distance) => {
    let nearest = 0;
    for (let j = 0; j < 3; j++) nearest += direction[j] * (bounds[j + (direction[j] < 0 ? 3 : 0)] - eye[j]);
    return nearest <= distance;
  };
  config.visibleSurfaces = (tree, eye, direction, distance, visible = new Uint8Array(tree.surfaceCount)) => {
    visible.fill(1);
    if (tree.leaves.length) {
      visible.fill(0, tree.world[0], tree.world[0] + tree.world[1]);
      const stack = tree.nodes.length ? [0] : tree.leaves.map((_, i) => -1-i);
      while (stack.length) {
        const index = stack.pop(), row = index < 0 ? tree.leaves[-1-index] : tree.nodes[index];
        if (!config.boundsVisible(row, eye, direction, distance)) continue;
        if (index >= 0) stack.push(row[6], row[7]);
        else for (let i = row[6], end = i + row[7]; i < end; i++) visible[tree.surfaces[i]] = 1;
      }
    }
    // Inline models are submitted separately by the renderer, not through leaves.
    for (const model of tree.inline) visible.fill(Number(model.corners.some(p =>
      direction.reduce((depth, d, j) => depth + d * (p[j] - eye[j]), 0) <= distance)), model.first, model.first + model.count);
    return visible;
  };
  const batches = [], props = [];
  config.bindWorldCull = (map, geometry, ranges, shader) => {
    if (map.section !== 'sp' || !map.bspCull || shader.sky) return;
    batches.push({geometry, ranges, original: geometry.index.array.slice(), previous: new Uint8Array(ranges.length).fill(255)});
  };
  config.compactBatch = (batch, visible) => {
    const {geometry, ranges, original, previous} = batch;
    let changed = false;
    for (let i = 0; i < ranges.length; i++) {
      const value = visible[ranges[i][0]];
      if (previous[i] !== value) changed = true;
      previous[i] = value;
    }
    if (!changed) return;
    let count = 0;
    for (const [face, start, length] of ranges) if (visible[face]) {
      geometry.index.array.set(original.subarray(start, start + length), count);
      count += length;
    }
    geometry.index.needsUpdate = true;
    geometry.setDrawRange(0, count);
  };
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
    visible = config.visibleSurfaces(map.bspCull, eye, direction, distance, visible);
    for (const batch of batches) config.compactBatch(batch, visible);
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

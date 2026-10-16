/* BSP cluster visibility and whole-surface draw batching. Native game axes. */
'use strict';
(function (host) {
  const api = {};
  api.boundsVisible = (bounds, eye, direction, distance) => {
    let nearest = 0;
    for (let j = 0; j < 3; j++) nearest += direction[j] * (bounds[j + (direction[j] < 0 ? 3 : 0)] - eye[j]);
    return nearest <= distance;
  };
  api.pointCluster = (tree, eye) => {
    if (!tree.nodes.length || tree.bounds && eye.some((v,j) => v < tree.bounds[j] || v > tree.bounds[j+3])) return -1;
    let index = 0;
    while (index >= 0) {
      const node = tree.nodes[index], plane = tree.planes[node[8]];
      const distance = eye.reduce((sum,v,j) => sum + v * plane[j], -plane[3]);
      index = node[distance > 0 ? 6 : 7];
    }
    return tree.leaves[-1-index][8];
  };
  api.visibleSurfaces = (tree, eye, direction, distance, visible = new Uint8Array(tree.surfaceCount), allowed = null) => {
    visible.fill(1);
    if (tree.leaves.length) {
      visible.fill(0, tree.world[0], tree.world[0] + tree.world[1]);
      const stack = tree.nodes.length ? [0] : tree.leaves.map((_,i) => -1-i);
      while (stack.length) {
        const index = stack.pop(), row = index < 0 ? tree.leaves[-1-index] : tree.nodes[index];
        if (index < 0 && allowed && !allowed[-1-index]) continue;
        if (!api.boundsVisible(row, eye, direction, distance)) continue;
        if (index >= 0) stack.push(row[6], row[7]);
        else for (let i = row[6], end = i + row[7]; i < end; i++) visible[tree.surfaces[i]] = 1;
      }
    }
    for (const model of tree.inline) visible.fill(Number(model.corners.some(p =>
      direction.reduce((depth,d,j) => depth + d * (p[j] - eye[j]), 0) <= distance)), model.first, model.first + model.count);
    return visible;
  };
  // What a fixed eye sees through the PVS: the surfaces of the leaves its cluster sees, and a test for whether a
  // point lies in one of them. Null without PVS data or outside every cluster.
  api.clusterMask = (tree, eye) => {
    if (!tree?.pvs) return null;
    const {numClusters, clusterBytes, bits} = tree.pvs, bytes = Uint8Array.from(atob(bits), c => c.charCodeAt(0));
    const cluster = api.pointCluster(tree, eye), sees = c => c >= 0 && c < numClusters && !!(bytes[cluster * clusterBytes + (c >> 3)] & 1 << (c & 7));
    if (!bytes.length || cluster < 0 || cluster >= numClusters) return null;
    const mask = new Uint8Array(tree.surfaceCount);
    for (const leaf of tree.leaves) if (sees(leaf[8])) for (let j = leaf[6], end = j + leaf[7]; j < end; j++) mask[tree.surfaces[j]] = 1;
    return {mask, visible: point => sees(api.pointCluster(tree, point))};
  };
  api.compactBatch = (batch, visible) => {
    const {geometry, ranges, original, previous} = batch;
    let changed = false;
    for (let i = 0; i < ranges.length; i++) {
      const value = visible[ranges[i][0]];
      if (previous[i] !== value) changed = true;
      previous[i] = value;
    }
    if (!changed) return;
    let count = 0;
    for (const [face,start,length] of ranges) if (visible[face]) {
      geometry.index.array.set(original.subarray(start,start+length),count);count += length;
    }
    geometry.index.needsUpdate = true;geometry.setDrawRange(0,count);
  };
  api.create = tree => {
    if (!tree?.pvs) return null; // Old imports retain their existing behavior.
    const {numClusters,clusterBytes,bits} = tree.pvs;
    const bytes = Uint8Array.from(atob(bits), c => c.charCodeAt(0));
    const batches = [], allowed = new Uint8Array(tree.leaves.length), mask = new Uint8Array(tree.surfaceCount);
    let cluster, dirty = true, all = true, lastView = '', rebuilds = 0;
    return {
      get cluster() { return cluster; },
      get rebuilds() { return rebuilds; },
      bind(geometry,ranges,shader) {
        if (shader.sky) return; // Preserve far-box sky behind culled world geometry.
        batches.push({geometry,ranges,original:geometry.index.array.slice(),previous:new Uint8Array(ranges.length).fill(255)});
        dirty = true;
      },
      update(eye, direction, distance = null) {
        const current = api.pointCluster(tree,eye);
        if (current !== cluster) {
          cluster = current;dirty = true;rebuilds++;
          all = !bytes.length || cluster < 0 || cluster >= numClusters;
          allowed.fill(Number(all));mask.fill(1);
          if (!all) {
            mask.fill(0,tree.world[0],tree.world[0]+tree.world[1]);
            tree.leaves.forEach((leaf,i) => {
              const c = leaf[8];
              if (c < 0 || !(bytes[cluster*clusterBytes+(c>>3)] & (1 << (c&7)))) return;
              allowed[i] = 1;
              for (let j=leaf[6],end=j+leaf[7];j<end;j++) mask[tree.surfaces[j]] = 1;
            });
          }
        }
        if (distance !== null) {
          const view = [...eye,...direction,distance].join(',');
          if (view !== lastView) dirty = true;
          lastView = view;
          // Filter leaves before union: intersecting two surface masks would
          // incorrectly retain a face shared by an invisible near and visible far leaf.
          if (dirty) api.visibleSurfaces(tree,eye,direction,distance,mask,all ? null : allowed);
        }
        if (dirty) { for (const batch of batches) api.compactBatch(batch,mask);dirty = false; }
        return mask;
      },
    };
  };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else host.skinnerVisibility = api;
})(typeof window === 'object' ? window : globalThis);

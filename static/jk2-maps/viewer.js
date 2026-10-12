/* Raven data is validated and converted on import; reuse the RTCW draw controls. */
'use strict';
(function (host) {
  const config = typeof module === 'object' && module.exports
    ? {...require('../rtcw-maps/viewer.js')} : {...host.skinnerMapConfig};
  Object.assign(config, {
    title: 'Jedi Outcast Maps', gameName: 'Jedi Outcast', storageKey: 'skinner.jk2maps',
    data: '/jk2-map-data/', importRoute: '/import_jk2_maps', defaultMap: 'sp_kejim_post', bspVersion: 47, vertexLightmaps: true,
  });
  // Raven FinishShader: vertex-lit BSP shaders replace their lightmap stage
  // with exact vertex colour. Placed models use the sampled light grid below.
  config.stagesOf = (shader, lit, model, vertexLit) => {
    if (shader.default || shader.implicit) {
      if (model && shader.implicit) return [{map: shader.map, rgbGen: ['exactvertex'], depthWrite: true}];
      return undefined;
    }
    if (model) return shader.stages.map(stage => ['lightingdiffuse', 'lightingdiffuseentity'].includes(stage.rgbGen[0])
      ? {...stage, rgbGen: ['exactvertex']} : stage);
    if (lit || !vertexLit) return undefined;
    const index = shader.stages.findIndex(stage => stage.map === '$lightmap');
    if (index < 0) return undefined;
    const stages = shader.stages.map(stage => ({...stage}));
    if (index === 0) {
      stages.shift();
      if (!stages.length) return stages;
      delete stages[0].blend;
      delete stages[0].alphaFunc;
      delete stages[0].depthFunc;
      stages[0].depthWrite = true;
    } else stages[index].map = '$whiteimage';
    stages[index].rgbGen = ['exactvertex'];
    stages[index].alphaGen = ['identity'];
    return stages;
  };
  config.diffuse = (normal, matrix, light) => {
    if (!light) return [1, 1, 1, 1]; // Old imports retain their previous lighting until reimported.
    // R_SetupEntityLighting transforms the world direction into the model axes;
    // RB_CalcDiffuseColor clamps and quantizes each vertex before interpolation.
    const incoming = Math.max(0, normal.reduce((sum, n, j) => sum + n *
      light.direction.reduce((dot, d, k) => dot + d * matrix[j * 4 + k], 0), 0));
    return [...light.ambient.map((a, j) => Math.min(255, Math.max(0, Math.floor(a + incoming * light.directed[j]))) / 255), 1];
  };
  const addPlaced = config.addPlaced;
  config.pose = (item, seconds) => {
    const origin = [...item.origin], angles = [...item.angles];
    if (item.spin) angles.splice(0, 3, 0, (seconds * 1000 % 2048) * 360 / 2048, 0);
    if (item.bob) origin[2] += item.bob.base + Math.cos((seconds * 1000 + 1000) * item.bob.rate) * (item.bob.amplitude ?? 3);
    const matrix = config.matrix(angles, item.scale, origin);
    if (item.pivot) for (let j = 0; j < 3; j++)
      matrix[12 + j] -= item.pivot.reduce((sum, p, k) => sum + p * matrix[k * 4 + j] / item.scale[k], 0);
    return matrix;
  };
  config.addPlaced = async (item, context) => {
    const holder = await addPlaced({...item, spin: false}, context);
    const geometries = new Map();
    for (const mesh of holder.children) {
      if (!geometries.has(mesh.geometry)) {
        const geometry = mesh.geometry.clone();
        geometry.setAttribute('tint', new THREE.Float32BufferAttribute(new Float32Array(geometry.attributes.normal.count * 4), 4));
        geometries.set(mesh.geometry, geometry);
      }
      mesh.geometry = geometries.get(mesh.geometry);
    }
    const update = seconds => {
      const matrix = config.pose(item, seconds);
      holder.matrix.fromArray(matrix);
      holder.matrixWorldNeedsUpdate = true;
      for (const geometry of geometries.values()) {
        const {normal, tint} = geometry.attributes;
        for (let i = 0; i < normal.count; i++) tint.setXYZW(i, ...config.diffuse(
          [normal.getX(i), normal.getY(i), normal.getZ(i)], matrix, item.light));
        tint.needsUpdate = true;
      }
    };
    update(0);
    if (item.spin || item.bob) context.ticks.push(update);
    return holder;
  };
  if (typeof module === 'object' && module.exports) module.exports = config;
  else host.skinnerMapConfig = config;
})(typeof window === 'object' ? window : globalThis);

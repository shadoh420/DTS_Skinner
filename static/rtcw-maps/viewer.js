/* RTCW parameters for the shared Q3 draw/stage viewer. Also exercised in Node. */
'use strict';
(function (host) {
  const api = {
    title: 'RTCW Maps', gameName: 'Return to Castle Wolfenstein', storageKey: 'skinner.rtcwmaps',
    data: '/rtcw-map-data/', importRoute: '/import_rtcw_maps', bspVersion: 47,
    defaultMap: 'sp_escape1', skyMipmaps: true, ambientScale: .5,  // r_ambientScale 0.5 in RTCW and ET (Q3 0.6).
    configureTexture(texture, lighting, plain) {
      if (plain || !lighting) return;
      const modes = {
        GL_NEAREST: [THREE.NearestFilter, THREE.NearestFilter],
        GL_LINEAR: [THREE.LinearFilter, THREE.LinearFilter],
        GL_NEAREST_MIPMAP_NEAREST: [THREE.NearestMipmapNearestFilter, THREE.NearestFilter],
        GL_LINEAR_MIPMAP_NEAREST: [THREE.LinearMipmapNearestFilter, THREE.LinearFilter],
        GL_NEAREST_MIPMAP_LINEAR: [THREE.NearestMipmapLinearFilter, THREE.NearestFilter],
        GL_LINEAR_MIPMAP_LINEAR: [THREE.LinearMipmapLinearFilter, THREE.LinearFilter],
      };
      [texture.minFilter, texture.magFilter] = modes[lighting.textureMode] || modes.GL_LINEAR_MIPMAP_LINEAR;
    },
    defaultTexels(lighting = {gamma: 1, intensity: 1, hardwareGamma: true}) {
      const data = new Uint8Array(16 * 16 * 4);
      const inside = Math.min(255, Math.round(255 * (Math.min(255, Math.floor(32 * lighting.intensity)) / 255) ** (lighting.hardwareGamma ? 1 : 1 / lighting.gamma)));
      for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
        const edge = x === 0 || y === 0 || x === 15 || y === 15;
        data.set(edge ? [0, 0, 0, 255] : [inside, inside, inside, 32], (y * 16 + x) * 4);
      }
      return data;
    },
    // AnglesToAxis: native pitch/yaw/roll, columns of the placement matrix.
    matrix(angles, scale, origin) {
      const [p, y, r] = angles.map(x => x * Math.PI / 180);
      const cp = Math.cos(p), sp = Math.sin(p), cy = Math.cos(y), sy = Math.sin(y), cr = Math.cos(r), sr = Math.sin(r);
      return [cp*cy*scale[0], cp*sy*scale[0], -sp*scale[0], 0,
        (sr*sp*cy-cr*sy)*scale[1], (sr*sp*sy+cr*cy)*scale[1], sr*cp*scale[1], 0,
        (cr*sp*cy+sr*sy)*scale[2], (cr*sp*sy-sr*cy)*scale[2], cr*cp*scale[2], 0, ...origin, 1];
    },
    spinAngle(seconds) { return (seconds * 1000 % 4096) * 360 / 4095; },
    async addPlaced(item, {root, loadItemModel, ticks, lightAt, useLight}) {
      const holder = new THREE.Group();
      for (const surfaces of await Promise.all(item.models.map(loadItemModel))) {
        for (const {geometry, materials, sort} of surfaces) materials.forEach((material, stage) => {
          const mesh = new THREE.Mesh(geometry, material);
          mesh.renderOrder = sort * 100000 + 99000 + stage;
          holder.add(mesh);
        });
      }
      holder.matrixAutoUpdate = false;
      const update = seconds => {
        const angles = item.spin ? [0, api.spinAngle(seconds), 0] : item.angles;
        holder.matrix.fromArray(api.matrix(angles, item.scale, item.origin));
        holder.matrixWorldNeedsUpdate = true;
      };
      update(0); root.add(holder);
      // ponytail: an unevenly scaled model takes its mean scale; the game scales the light per axis.
      useLight?.(holder, lightAt?.(item.origin, item.pickup ? 32 : 0), item.scale.reduce((sum, value) => sum + value, 0) / 3);
      if (item.spin) ticks.push(update);
      return holder;
    },
  };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else host.skinnerMapConfig = api;
})(typeof window === 'object' ? window : globalThis);

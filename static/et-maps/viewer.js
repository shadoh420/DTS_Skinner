/* ET rules layered on the RTCW placement and shared Q3 stage viewer. */
'use strict';
(function (host) {
  const base = typeof module === 'object' && module.exports ? require('../rtcw-maps/viewer.js') : host.skinnerMapConfig;
  const api = {...base, title: 'ET Maps', gameName: 'Enemy Territory', storageKey: 'skinner.etmaps',
    data: '/et-map-data/', importRoute: '/import_et_maps', defaultMap: 'battery', externalLightmaps: true, foliage: true,
    fogCurve: 'linear',  // ET's fog image is linear in s·t (R_CreateFogImage), not the square root of Q3's.
    sceneFile(search = host.location.search) { return new URLSearchParams(search).get('built') === '1' ? 'scene-built.json' : 'scene.json'; },
    stagesOf(shader, lit, model) {
      if (!shader.implicitType) return null;
      const stage = {map: shader.map, rgbGen: [model ? 'lightingdiffuse' : lit ? 'identity' : 'exactvertex'], depthWrite: true, noMipmaps: !!shader.noMipmaps};
      if (shader.implicitType === 'implicitmask') stage.alphaFunc = 'ge128';
      if (shader.implicitType === 'implicitblend') stage.blend = ['gl_src_alpha', 'gl_one_minus_src_alpha'];
      if (!lit || model) { if (!model) stage.alphaGen = ['vertex']; return [stage]; }
      if (shader.implicitType === 'implicitmap') return [
        {map: '$lightmap', tcGen: 'lightmap', rgbGen: ['identity'], depthWrite: true},
        {...stage, blend: ['gl_dst_color', 'gl_zero']}
      ];
      return [stage, {map: '$lightmap', tcGen: 'lightmap', rgbGen: ['identity'], blend: ['gl_dst_color', 'gl_zero'], depthWrite: true, depthFunc: 'equal'}];
    },
  };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    host.skinnerMapConfig = api;
    host.addEventListener('DOMContentLoaded', () => {
      const checkbox = document.getElementById('built');
      checkbox.checked = api.sceneFile() === 'scene-built.json';
      checkbox.addEventListener('change', () => {
        const url = new URL(host.location.href);
        if (checkbox.checked) url.searchParams.set('built', '1'); else url.searchParams.delete('built');
        host.location.assign(url.href);
      });
    });
  }
})(typeof window === 'object' ? window : globalThis);

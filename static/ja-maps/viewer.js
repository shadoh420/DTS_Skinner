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
  if (typeof module === 'object' && module.exports) module.exports = config;
  else host.skinnerMapConfig = config;
})(typeof window === 'object' ? window : globalThis);

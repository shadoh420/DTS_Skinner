/* JA uses Raven geometry, lighting and controls from the JK2 viewer. */
'use strict';
(function (host) {
  const config = typeof module === 'object' && module.exports
    ? {...require('../jk2-maps/viewer.js')} : {...host.skinnerMapConfig};
  Object.assign(config, {
    title: 'Jedi Academy Maps', gameName: 'Jedi Academy', storageKey: 'skinner.jamaps',
    data: '/ja-map-data/', importRoute: '/import_ja_maps', defaultMap: 'sp_yavin1',
  });
  if (typeof module === 'object' && module.exports) module.exports = config;
  else host.skinnerMapConfig = config;
})(typeof window === 'object' ? window : globalThis);

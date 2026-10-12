/* Raven data is validated and converted on import; reuse the RTCW draw controls. */
'use strict';
(function (host) {
  const config = typeof module === 'object' && module.exports
    ? {...require('../rtcw-maps/viewer.js')} : {...host.skinnerMapConfig};
  Object.assign(config, {
    title: 'Jedi Outcast Maps', gameName: 'Jedi Outcast', storageKey: 'skinner.jk2maps',
    data: '/jk2-map-data/', importRoute: '/import_jk2_maps', defaultMap: 'sp_kejim_post', bspVersion: 47,
  });
  const addPlaced = config.addPlaced;
  config.pose = (item, seconds) => {
    const origin = [...item.origin], angles = [...item.angles];
    if (item.spin) angles.splice(0, 3, 0, (seconds * 1000 % 2048) * 360 / 2048, 0);
    if (item.bob) origin[2] += item.bob.base + Math.cos((seconds * 1000 + 1000) * item.bob.rate) * 3;
    return config.matrix(angles, item.scale, origin);
  };
  config.addPlaced = async (item, context) => {
    const holder = await addPlaced({...item, spin: false}, context);
    const update = seconds => {
      holder.matrix.fromArray(config.pose(item, seconds));
      holder.matrixWorldNeedsUpdate = true;
    };
    update(0);
    if (item.spin || item.bob) context.ticks.push(update);
    return holder;
  };
  if (typeof module === 'object' && module.exports) module.exports = config;
  else host.skinnerMapConfig = config;
})(typeof window === 'object' ? window : globalThis);

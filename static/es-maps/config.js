/* The Earthsiege 1 and 2 Maps pages (/maps/es1/, /maps/es2/): the T1 viewer with each game's pack, title and import. */
(() => {
  const game = location.pathname.includes('/es1/') ? 'es1' : 'es2', title = game === 'es1' ? 'Earthsiege Maps' : 'Earthsiege 2 Maps';
  window.skinnerT1MapConfig = {data: `/${game}-map-data/`, title, storageKey: `skinner.${game}maps`, importRoute: `/import_es_maps/${game}`,
    folder: game === 'es1' ? 'Earthsiege' : 'Earthsiege 2', firstMap: game === 'es1' ? 'b0_c0_0a' : 'c1_01'};
  document.title = `DTS Skinner — ${title}`;
  window.addEventListener('DOMContentLoaded', () => {
    document.getElementById('pageTitle').textContent = title;
    document.getElementById('folderLabel').textContent = `${window.skinnerT1MapConfig.folder} folder`;
  });
})();

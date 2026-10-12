/* One viewer, separate catalogs/import routes, one texture library per game. */
window.idTech4MapConfigs={
 doom3:{game:'doom3',label:'Doom 3',data:'/doom3-map-data/',import:'/import_doom3_maps',
  install:'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Doom 3',
  description:'Doom 3 and Resurrection of Evil. The game folder is only read.'},
 quake4:{game:'quake4',label:'Quake 4',data:'/quake4-map-data/',import:'/import_quake4_maps',
  install:'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Quake 4',
  description:'Campaign and deathmatch layouts. Textures share the Quake 4 model library. The game folder is only read.'}
};
window.idTech4MapConfig=window.idTech4MapConfigs[location.pathname.startsWith('/maps/quake4/')?'quake4':'doom3'];

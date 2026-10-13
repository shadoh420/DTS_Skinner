'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const NPC = require('../static/doom3-maps/npcs.js');

for (const game of ['doom3', 'quake4']) {
  for (const classname of ['monster_turret', 'monster_turret_flying', 'monster_big_hh_tank', 'char_sentry', 'map_char_marine']) {
    assert.equal(NPC.isNPC({classname}, game), true, `${game}: ${classname}`);
  }
  for (const classname of ['vehicle_walker', 'convoy_vehicle_flatbed', 'env_ragdoll_marine', 'env_dead_d_leg',
    'func_static', 'func_mover', 'item_armor_shard', 'weapon_machinegun', 'moveable_marine_helmet']) {
    assert.equal(NPC.isNPC({classname, model: 'model_char_marine'}, game), false, `${game}: ${classname} is not an NPC`);
  }
  assert.equal(NPC.isNPC({classname: 'unknown_actor', model: 'model_monster_scientist'}, game), true);
  assert.equal(NPC.isNPC({classname: 'unknown', model: 'models/mapobjects/strogg/marine/box.lwo'}, game), false);
  assert.equal(NPC.isNPC({classname: 'monster_imp', hidden: true, provenance: {spawner: 'spawn1'}}, game), true);
  assert.equal(NPC.isNPC({classname: 'monster_imp', npc: false}, game), false, 'explicit tag wins');
  assert.equal(NPC.isNPC({classname: 'new_actor', npc: true}, game), true);
  assert.equal(NPC.isNPC({classname: 'cin_marine', model: 'env_ragdoll_marine'}, game), false);
}
for (const classname of ['admin_imp1', 'admin_overhear_swann', 'admin_overhear_player', 'admin_wounded_marine',
  'delta1_scipull', 'delta2a_zombie_office1', 'delta2b_tankrev', 'marscity_receptionist', 'underground_maint_old_charles',
  'erebus2_cinematic_hunter1', 'erebus2_spooked', 'erebus5_cloud_cinematic', 'erebus6_bottech',
  'hellhole_cin_cyberdemon', 'hellhole_cin_cyberdemon2', 'underground_invasion_labzombie']) {
  assert.equal(NPC.isNPC({classname}, 'doom3'), true, classname);
}
for (const classname of ['admin_impdoors', 'admin_doorframe_pinkyattack', 'admin_bfgcase', 'marscity_dufflebag',
  'delta2b_tankimpwires', 'comm1_sentryloader', 'monster_container_door']) {
  const entity = {classname: classname === 'monster_container_door' ? 'func_mover' : classname, model: classname};
  assert.equal(NPC.isNPC(entity, 'doom3'), false, classname);
}
for (const classname of ['airdefense_char_marine', 'monster_strogg_marine', 'dispersal_tactical_transfer',
  'dispersal_stroggified_kane', 'medlabs_nanocyte_kidnap_creature', 'storage_guardian']) {
  assert.equal(NPC.isNPC({classname}, 'quake4'), true, classname);
}
assert.equal(NPC.isNPC({classname: 'env_marine_emp', model: 'model_marine_emp'}, 'quake4'), false);
assert.equal(NPC.isNPC({classname: 'convoy2_cin_gev', model: 'model_convoy2_cin_gev'}, 'quake4'), false);
assert.equal(NPC.isNPC({classname: 'admin_imp1'}, 'quake4'), false, 'aliases are game-specific');
assert.equal(NPC.isNPC({classname: 'monster_imp'}, 'eye'), false, 'new games need their own tags');
assert.equal(NPC.isNPC({classname: 'MONSTER_DEMON_IMP'}, 'doom3'), true);

// Synthetic exported Doom scene: two surfaces belong to one NPC, one NPC has no drawable geometry.
const sample = {game: 'doom3', entities: [
  {name: 'imp', classname: 'monster_demon_imp', hidden: true},
  {name: 'swann', classname: 'admin_overhear_swann'},
  {name: 'scientist', classname: 'delta1_scipull'},
  {name: 'corpse', classname: 'env_ragdoll_marine'},
  {name: 'door', classname: 'func_door'},
  {name: 'missing', classname: 'monster_zombie'},
  {name: 'empty', classname: 'monster_zombie'}
], surfaces: ['imp', 'imp', 'swann', 'scientist', 'corpse', 'door', 'worldspawn', 'unknown'].map(entity => ({entity, count: 3}))};
sample.surfaces.push({entity: 'empty', count: 0});
assert.deepEqual([...NPC.owners(sample)], ['imp', 'swann', 'scientist']);
assert.equal(NPC.owners({}).size, 0);

// Execute the actual draw() body with a tiny rendering harness. OFF must survive
// subsequent frames and the portal-sky pass, without overriding ordinary culling.
const viewer = fs.readFileSync(require.resolve('../static/doom3-maps/viewer.js'), 'utf8');
const draw = viewer.slice(viewer.indexOf('function draw('), viewer.indexOf('function pointCamera('));
const batches = [true, false, true, false].map((npc, i) => ({npc, group: {}, meshes: [],
  surface: {areas: i === 2 ? [9] : [], parms: []}, geometry: {boundingBox: {outside: i === 3}}}));
const renders = [], camera = {position: {toArray: () => [0, 0, 0]}, updateMatrixWorld() {}};
const harness = {data: {}, batches, npcs: {enabled: false}, camera, scene: {}, copyScene: {}, copyCamera: {},
  matrix: {elements: [], multiplyMatrices() {}}, frustum: {setFromProjectionMatrix() {}, intersectsBox: b => !b.outside},
  M: {visibleAreas: () => new Set([0]), portalSkyOrigin: () => [1, 2, 3], soundAmplitude: () => 0},
  skyCamera: {...camera, copy() {}, position: {fromArray() {}, toArray: () => [1, 2, 3]}},
  colorTarget: {width: 1, height: 1}, soundAmplitudeOverride: null, lastMetrics: {}, updatePass() {},
  renderer: {domElement: {width: 1, height: 1}, info: {render: {calls: 1, triangles: 1}},
    setRenderTarget() {}, clearDepth() {}, render(scene) {
      if (scene === harness.scene) renders.push(batches.map(b => b.group.visible));
    }}};
vm.createContext(harness); vm.runInContext(draw, harness);
harness.draw(); harness.draw(1);
assert.equal(renders.length, 4, 'two views per frame');
for (const visible of renders) assert.deepEqual(visible, [false, true, false, false]);
harness.npcs.enabled = true; harness.draw(2);
assert.deepEqual(renders.at(-1), [true, true, false, false], 'ON preserves portal and frustum culling');
console.log('Doom/Q4 NPC classifier, synthetic count (3), and per-frame/portal-sky visibility OK');

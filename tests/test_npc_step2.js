'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const THREE = require('../static/three.min.js');
const source = name => fs.readFileSync(require.resolve('../static/' + name + '/viewer.js'), 'utf8');
const between = (text, start, end) => text.slice(text.indexOf(start), text.indexOf(end, text.indexOf(start)));
const toggle = () => ({enabled: false, count: null, setCount(n) { this.count = n; }});

async function sharedGroups(game) {
  const text = source('unreal-maps'), control = toggle(), scene = new THREE.Scene();
  const geometry = new THREE.BufferGeometry(), texture = new THREE.Texture();
  const map = {groups: [
    {entity: 10, npc: true, classname: 'Pawn', start: 0, count: 3},
    {entity: 10, npc: true, classname: 'Pawn', start: 3, count: 3},
    {entity: 20, npc: false, classname: 'Prop', start: 6, count: 3},
    {entity: 30, npc: true, classname: 'Creature', start: 9, count: 3},
  ].map(g => ({...g, texture: 'shared.png', flags: 0, kind: 'model', alpha: 1, size: [4, 4], settings: {}, model_light: [1, 1, 1]}))};
  const context = vm.createContext({window: {skinnerNPCToggle: control}, THREE, scene, map, geometry,
    config: null, quake: game === 'quake', quake2: game === 'quake2', azrael: false,
    daikatana: game === 'daikatana', anachronox: game === 'anachronox', sin: game === 'sin',
    loadTexture: async () => texture});
  vm.runInContext(between(text, '  const npcDraws', '  const storageKey'), context);
  if (['sin', 'daikatana', 'anachronox'].includes(game)) {
    vm.runInContext(fs.readFileSync(require.resolve('../static/quake2-maps/materials.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(require.resolve('../static/' + game + '-maps/materials.js'), 'utf8'), context);
    map.fog = {enabled: false, color: [0, 0, 0], start: 0, end: 100, sky_end: 100};
    map.skybox = {rotate: 0, axis: [0, 0, 1], clouds: {layers: []},
      faces: Object.fromEntries(['rt', 'lf', 'bk', 'ft', 'up', 'dn'].map(face => [face, {texture: 'sky.png'}]))};
    await context.window[game + 'Maps'].load(map, async () => texture);
  }
  if (['quake', 'quake2', 'vtmb'].includes(game)) {
    vm.runInContext(fs.readFileSync(require.resolve('../static/quake2-maps/materials.js'), 'utf8'), context);
    if (game !== 'quake2') vm.runInContext(fs.readFileSync(require.resolve('../static/' + game + '-maps/materials.js'), 'utf8'), context);
    if (game === 'vtmb') context.config = context.window.skinnerMapViewerConfig;
  }
  vm.runInContext(between(text, '  const MASKED', '  async function buildWorld'), context);
  if (game === 'quake') vm.runInContext('quakeColormap = new THREE.Texture()', context);
  await vm.runInContext('(async()=>{' + between(text, '    const materials = await Promise.all(map.groups', '\n  function showNotes()').replace(/\n  }\s*$/, '') + '})()', context);
  const materials = scene.children[0].material;
  assert.equal(control.count, 2, 'groups count unique actors, not material passes');
  assert.deepEqual(Array.from(materials, m => m.visible), [false, false, true, false]);
  assert.notStrictEqual(materials[0], materials[2], 'same-texture NPC and scenery have independent visibility');
  control.onChange(true);
  assert.ok(materials.every(m => m.visible));
  control.onChange(false);
  assert.deepEqual(Array.from(materials, m => m.visible), [false, false, true, false]);
}

async function t1Objects() {
  const text = source('t1-maps'), control = toggle(), scene = new THREE.Scene();
  const geometry = new THREE.BoxGeometry(), materials = [new THREE.MeshBasicMaterial()];
  const context = vm.createContext({window: {skinnerNPCToggle: control}, THREE, scene,
    loadModel: async () => ({geometry, materials}), occluders: [], weather: false, missing: 0});
  vm.runInContext(between(text, '  const npcDraws', '  const storageKey'), context);
  vm.runInContext(between(text, '  const placeholder', '  // Sky:'), context);
  for (const [entity, npc, model] of [[1, true, 'shared'], [2, false, 'shared'], [1, true, 'shared'], [3, true, null]]) {
    await context.addObject({entity, npc, model, name: String(entity), matrix: new THREE.Matrix4().elements});
  }
  vm.runInContext('npcToggle.setCount(npcIDs.size)', context);
  assert.equal(control.count, 1, 'duplicate owner counts once; unresolved model does not count');
  assert.deepEqual(scene.children.map(m => m.visible), [false, true, false, false]);
  assert.strictEqual(scene.children[0].material, scene.children[1].material);
  assert.equal(materials[0].visible, true, 'hide instances without hiding shared model materials');
  control.onChange(true);
  assert.ok(scene.children.every(m => m.visible));
  control.onChange(false);
  assert.deepEqual(scene.children.map(m => m.visible), [false, true, false, false]);
}

(async () => {
  for (const game of ['unreal', 'ut', 'sin', 'daikatana', 'anachronox', 'dx', 'vtmb', 'quake', 'quake2']) await sharedGroups(game);
  await t1Objects();
})().then(() => console.log('Shared group and T1 object NPC visibility/counts: OK'))
  .catch(error => { console.error(error); process.exitCode = 1; });

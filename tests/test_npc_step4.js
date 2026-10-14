'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const THREE = require('../static/three.min.js');
global.THREE = THREE;
const source = fs.readFileSync(require.resolve('../static/q3-maps/viewer.js'), 'utf8');
const start = source.indexOf('  const npcToggle ='), end = source.indexOf('  const camera =', start);
assert.ok(start > 0 && end > start);

async function check(game) {
  const config = require('../static/' + game + '-maps/viewer.js');
  const toggle = {enabled: false, count: -1, setCount(n) { this.count = n; }};
  const root = new THREE.Group();
  const context = vm.createContext({window: {skinnerNPCToggle: toggle}, root, THREE});
  vm.runInContext(source.slice(start, end), context);
  assert.equal(toggle.count, 0);
  const geometry = new THREE.BoxGeometry(), material = new THREE.MeshBasicMaterial();
  const shared = [{geometry, materials: [material], sort: 1}];
  const map = {section: 'sp', bspCull: {pvs: true}, distanceCull: 10000};
  const holders = [], ticks = [];
  for (const [entity, npc] of [[1, true], [1, true], [2, true], [3, false]]) {
    const item = {entity, npc, models: ['same'], origin: [0, 0, 0], angles: [0, 0, 0], scale: [1, 1, 1]};
    const holder = await config.addPlaced(item, {root, ticks, map, loadItemModel: async () => shared});
    holders.push(holder);Object.assign(context, {item, holder});
    vm.runInContext('trackNPC(item, holder)', context);
  }
  const shown = holder => { for (let p = holder; p; p = p.parent) if (!p.visible) return false; return true; };
  assert.equal(toggle.count, 2, 'head/attachments share actor ID; separate actors still count');
  assert.deepEqual(holders.map(shown), [false, false, false, true]);
  const camera = new THREE.PerspectiveCamera();
  config.updateCull?.(map, camera);
  ticks.forEach(tick => tick(10));
  assert.deepEqual(holders.map(shown), [false, false, false, true], 'frame culling cannot override OFF');
  assert.equal(material.visible, true, 'shared scenery material remains visible');
  toggle.onChange(true);
  assert.ok(holders.every(shown));
  holders[0].visible = false; // Existing distance culling still wins when ON.
  toggle.onChange(false);toggle.onChange(true);
  assert.equal(shown(holders[0]), false);
  vm.runInContext('npcLoadFailed = true; npcToggle.setCount(0); trackNPC(item, holder)', context);
  assert.equal(toggle.count, 0, 'failed map load cannot regain a count from pending loads');
}
(async () => {
  for (const game of ['rtcw', 'jk2', 'ja']) await check(game);
  console.log('RTCW/JK2/JA shared NPC visibility and unique counts passed');
})().catch(error => { console.error(error);process.exitCode = 1; });

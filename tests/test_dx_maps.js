// No browser: execute the shared viewer's real configuration expressions for both page modes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/unreal-maps/viewer.js', 'utf8');
function config(dx) {
  const context = vm.createContext({dx, azrael: false, sin: false, anachronox: false, daikatana: false, quake2: false, quake: false});
  for (const key of ['storageKey', 'POWER', 'displayPower', 'GAMES', 'dataRoot']) {
    const declaration = source.match(new RegExp(`const ${key} = [\\s\\S]*?;`));
    assert.ok(declaration, key);
    vm.runInContext(declaration[0], context);
  }
  return text => vm.runInContext(text, context);
}
let run = config(true);
assert.equal(run('storageKey'), 'skinner.dxmaps');
assert.equal(run('JSON.stringify(GAMES.map(x => x[0]))'), '["dx"]');
assert.equal(run('dataRoot("dx")'), '/dx-map-data/');
assert.equal(run('displayPower("dx", {display: {power: 0.7}})'), 0.7);
assert.equal(run('displayPower("dx", {})'), 2 / 3);
assert.equal(run('displayPower("dx", {display: {power: -1}})'), 2 / 3);
run = config(false);
assert.equal(run('storageKey'), 'skinner.unrealmaps');
assert.equal(run('JSON.stringify(GAMES.map(x => x[0]))'), '["unreal","ut"]');
assert.equal(run('dataRoot("ut")'), '/unreal-map-data/ut/');
assert.equal(run('displayPower("unreal", {})'), 0.8);
assert.equal(run('displayPower("ut", {display: {power: 0.1}})'), 0.67);
assert.ok(source.indexOf('map = {...await') < source.indexOf('uniforms.power.value = displayPower'));
assert.match(source, /dx \? '\/import_dx_maps'/);
console.log('DX and Unreal/UT viewer configuration passed');

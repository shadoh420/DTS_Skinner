'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../static/npc-toggle.js'), 'utf8');

// Only the DOM operations used by this control; no browser or dependencies required.
function page(path, storage = new Map(), {blocked = false, writeBlocked = false, lateHeader = false, count = 3} = {}) {
  function element(tag) {
    return {tag, children: [], events: {}, attrs: {},
      append(...children) { this.children.push(...children); },
      setAttribute(k, v) { this.attrs[k] = v; },
      addEventListener(k, fn) { this.events[k] = fn; }};
  }
  let header = lateHeader ? null : element('header'), observe, disconnected = false;
  const document = {readyState: 'loading', body: element('body'), createElement: element,
    querySelector: () => header, addEventListener: (event, fn) => { document[event] = fn; }};
  const context = {window: {}, document, location: {pathname: path},
    MutationObserver: class { constructor(fn) { observe = fn; } observe() {} disconnect() { disconnected = true; } }};
  Object.defineProperty(context, 'localStorage', {get() {
    if (blocked) throw Error('Storage denied');
    return {getItem: k => storage.get(k) ?? null, setItem: (k, v) => {
      if (writeBlocked) throw Error('Storage full');
      storage.set(k, v);
    }};
  }});
  vm.runInNewContext(source, context);
  const control = context.window.skinnerNPCToggle;
  // The viewer can set a count before DOMContentLoaded mounts the control.
  if (count !== null) control.setCount(count);
  document.DOMContentLoaded();
  if (lateHeader) { header = element('header'); observe(); assert.equal(disconnected, true); }
  const label = header.children[0], [input, text] = label.children;
  assert.equal(header.children.length, 1);
  assert.equal(input.attrs['aria-label'], 'NPCs');
  return {control, input, text};
}

const storage = new Map(), first = page('/maps/doom3/', storage);
assert.equal(first.control.enabled, true);
assert.equal(first.text.textContent, 'NPCs (3)');
const empty = page('/maps/doom3/', storage, {count: null});
assert.equal(empty.input.disabled, true, 'starts disabled before a viewer supplies data');
assert.equal(empty.input.checked, true, 'an empty map preserves default ON');
assert.equal(storage.size, 0, 'empty maps do not write a preference');
let changed = [];
first.control.onChange = value => changed.push(value);
first.input.checked = false; first.input.events.change();
assert.deepEqual(changed, [false]);
assert.equal(storage.get('skinner.maps.doom3.npcs'), 'false');
first.control.setCount(0);
assert.equal(first.input.disabled, true);
assert.equal(first.input.checked, false);
assert.equal(first.text.textContent, 'NPCs (none)');
assert.deepEqual(changed, [false], 'counts do not change the saved preference or emit changes');
first.control.setCount(12);
assert.equal(first.input.disabled, false);
assert.equal(first.control.enabled, false);
assert.equal(page('/maps/doom3/', storage).control.enabled, false, 'reload restores OFF');
assert.equal(page('/maps/quake4/', storage).control.enabled, true, 'separate viewer key');
const blocked = page('/maps/doom3/', storage, {blocked: true});
assert.equal(blocked.control.enabled, true);
blocked.input.checked = false; blocked.input.events.change();
assert.equal(blocked.control.enabled, false, 'storage failure keeps the in-page choice');
const full = page('/maps/doom3/', storage, {writeBlocked: true});
assert.equal(full.control.enabled, false, 'read works even when writes fail');
full.input.checked = true; full.input.events.change();
assert.equal(full.control.enabled, true);
assert.equal(storage.get('skinner.maps.doom3.npcs'), 'false', 'failed write leaves stored value alone');
const t2 = page('/maps/', storage, {lateHeader: true});
t2.input.checked = false; t2.input.events.change();
assert.equal(storage.get('skinner.maps.t2.npcs'), 'false');
for (const value of [0, -1, NaN, Infinity]) { t2.control.setCount(value); assert.equal(t2.input.disabled, true); }
let stopped = false;
t2.input.events.keydown({stopPropagation() { stopped = true; }});
assert.equal(stopped, true, 'checkbox keyboard input stays out of camera controls');
console.log('NPC toggle: default, storage, viewer keys, empty maps and async header OK');

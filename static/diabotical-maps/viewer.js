/* Vanilla Three.js free-flight viewer for the pack written by tools/import_diabotical_map.py: each map's blocks,
   meshed by blocks.js in the game's /export coordinates (y up), drawn in their materials' textures. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const storageKey = 'skinner.diaboticalmaps';
  const settings = {fov: 100, invertX: false, invertY: false};
  try { Object.assign(settings, JSON.parse(localStorage.getItem(storageKey) || '{}')); } catch (_) { /* Defaults remain usable. */ }
  const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(settings)); } catch (_) { /* Storage may be unavailable. */ } };

  const canvas = $('c');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x9fc4e0);
  scene.add(new THREE.HemisphereLight(0xdde8f0, 0x5a5048, .75));
  const sun = new THREE.DirectionalLight(0xfff4e0, .6);
  sun.position.set(.4, 1, .25);
  scene.add(sun);
  const camera = new THREE.PerspectiveCamera(60, 1, 2, 40000);
  camera.rotation.order = 'YXZ';
  const data = '/diabotical-map-data/';
  let speed = 600, ready = false, map = null, start = null, missing = 0;
  const showStatus = text => { $('status').textContent = text; };
  const showReady = () => showStatus(`${missing ? `Map loaded with ${missing} missing textures` : 'Map ready'} · speed ${Math.round(speed)}`);

  async function get(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} (${response.status})`);
    return response;
  }
  // A material with no texture is drawn in a flat colour of its own, from its name.
  function flatColour(name) {
    let hash = 2166136261;
    for (const c of name) hash = Math.imul(hash ^ c.charCodeAt(0), 16777619);
    return new THREE.Color().setHSL((hash >>> 0) % 360 / 360, .25, .55);
  }
  const loader = new THREE.TextureLoader();
  function material(name, entry) {
    if (!entry) return new THREE.MeshLambertMaterial({color: flatColour(name)});
    const texture = loader.load(data + 'textures/' + entry.texture, undefined, undefined, () => { missing++; if (ready) showReady(); });
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
    return new THREE.MeshLambertMaterial({map: texture});
  }

  function showNotes() {
    if (!map) return;
    const parts = [`${map.blocks.toLocaleString()} blocks drawn, map version ${map.version}${map.author ? `, by ${map.author}` : ''}`];
    if (map.untextured.length) parts.push('Not in the game files, so drawn in a flat colour: ' + map.untextured.join(', '));
    $('mapNotes').textContent = ' This map — ' + parts.join('. ') + '.';
  }
  $('notes').after(Object.assign(document.createElement('span'), {id: 'mapNotes'}));

  function applySettings() {
    for (const id of ['invertX', 'invertY']) $(id).checked = settings[id];
    $('fov').value = settings.fov;
    const main = canvas.parentElement, aspect = main.clientWidth / Math.max(main.clientHeight, 1);
    renderer.setSize(main.clientWidth, main.clientHeight, false);
    camera.aspect = aspect;
    // The field is horizontal, as in the game's settings; Three's is vertical.
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2) / aspect));
    camera.updateProjectionMatrix();
  }
  // The start view: above one corner of the blocks' bounds, looking across them.
  function showStart() {
    if (!start) return;
    const {min, max} = start, size = max.clone().sub(min);
    camera.position.set(min.x - size.x * .1, max.y + Math.max(size.y * .3, 200), max.z + size.z * .1);
    camera.lookAt(min.clone().add(max).multiplyScalar(.5));
  }

  $('fov').addEventListener('change', event => { settings.fov = Math.max(30, Math.min(130, Number(event.target.value) || 100)); save(); applySettings(); });
  for (const id of ['invertX', 'invertY']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
  $('reset').addEventListener('click', () => { speed = 600; showStart(); if (ready) showReady(); });
  new ResizeObserver(applySettings).observe(canvas.parentElement);

  // Captured mouse and plain drag share one look function: mouse right looks right, mouse up looks up.
  const keys = new Set();
  let dragging = false;
  canvas.addEventListener('mousedown', () => {
    dragging = true;
    try { Promise.resolve(canvas.requestPointerLock()).catch(() => { /* Drag-to-look still works. */ }); } catch (_) { /* Same. */ }
  });
  window.addEventListener('mouseup', () => { dragging = false; });
  window.addEventListener('blur', () => { dragging = false; keys.clear(); });
  document.addEventListener('mousemove', event => {
    if (!dragging && document.pointerLockElement !== canvas) return;
    camera.rotation.y -= event.movementX * .0025 * (settings.invertX ? -1 : 1);
    camera.rotation.x = Math.max(-1.55, Math.min(1.55, camera.rotation.x - event.movementY * .0025 * (settings.invertY ? -1 : 1)));
  });
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    speed = Math.max(20, Math.min(20000, speed * (event.deltaY < 0 ? 1.2 : 1 / 1.2)));
    if (ready) showReady();
  }, {passive: false});
  document.addEventListener('keydown', event => {
    if (event.target.matches('input:not([type=checkbox]), select')) return;
    if (/^(Key[WASD]|Space|Shift(Left|Right))$/.test(event.code)) { keys.add(event.code); event.preventDefault(); }
  });
  document.addEventListener('keyup', event => keys.delete(event.code));

  const draw = () => renderer.render(scene, camera);
  const clock = new THREE.Clock(), forward = new THREE.Vector3(), right = new THREE.Vector3(), move = new THREE.Vector3();
  renderer.setAnimationLoop(() => {
    const delta = Math.min(clock.getDelta(), .1), held = code => Number(keys.has(code));
    camera.getWorldDirection(forward);
    right.crossVectors(forward, camera.up).normalize();
    move.copy(forward).multiplyScalar(held('KeyW') - held('KeyS')).addScaledVector(right, held('KeyD') - held('KeyA'));
    move.y += held('Space') - held('ShiftLeft') - held('ShiftRight');
    if (move.lengthSq()) camera.position.addScaledVector(move.normalize(), speed * delta);
    draw();
  });
  window.skinnerDiaboticalMaps = {renderer, scene, camera, draw};  // For checks in a hidden page, where no frame is drawn.

  $('map').addEventListener('change', event => { location.search = '?map=' + encodeURIComponent(event.target.value); });
  $('import').addEventListener('click', async () => {
    const game = $('gamePath').value.trim();
    if (!game) { $('importStatus').textContent = 'Enter your Diabotical folder.'; return; }
    $('import').disabled = true;
    $('importStatus').textContent = 'Importing maps… the whole game takes about half a minute.';
    try {
      const response = await fetch('/import_diabotical_maps', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({game, replace: $('replace').checked})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      try { localStorage.setItem(storageKey + '.game', game); } catch (_) { /* Path is simply not remembered. */ }
      const failed = Object.entries(result.failed).map(([name, reason]) => `${name}: ${reason}`);
      $('importStatus').textContent = `Imported ${result.imported.length}, skipped ${result.skipped.length} already imported` +
        (failed.length ? `, failed ${failed.length} (${failed.join('; ')})` : '') +
        (result.untextured.length ? `. Materials not in the game files, drawn in flat colours: ${result.untextured.join(', ')}` : '') + '.';
      if (result.imported.length && !map) location.reload();
    } catch (error) { $('importStatus').textContent = error.message; }
    finally { $('import').disabled = false; }
  });
  try { $('gamePath').value = localStorage.getItem(storageKey + '.game') || ''; } catch (_) { /* Field stays empty. */ }

  try {
    let maps = [];
    try { maps = await (await get(data + 'index.json')).json(); } catch (_) { /* No pack yet. */ }
    if (!maps.length) { $('importPanel').open = true; throw new Error('no maps imported yet. Use Import maps above.'); }
    let mapId = new URLSearchParams(location.search).get('map') || '';
    if (!maps.some(item => item.id === mapId)) mapId = (maps.find(item => item.id === 'duel_bioplant') || maps[0]).id;
    const byGroup = new Map();
    for (const item of maps) byGroup.set(item.group, [...(byGroup.get(item.group) || []), item]);
    $('map').replaceChildren(...[...byGroup].map(([group, items]) => {
      const element = Object.assign(document.createElement('optgroup'), {label: group});
      element.append(...items.map(item => new Option(item.name, item.id)));
      return element;
    }));
    $('map').value = mapId;
    map = maps.find(item => item.id === mapId);
    document.title = `${map.name} — Diabotical Maps`;
    showStatus('Loading blocks…');
    const [buffer, entries] = await Promise.all([get(`${data}maps/${map.file}`).then(r => r.arrayBuffer()), get(data + 'materials.json').then(r => r.json())]);
    // The last material of a map is unnamed and drawn as default, as the game does.
    const names = map.materials.map(name => name || 'default');
    map.untextured = [...new Set(names.filter(name => !entries[name]))];
    const groups = DiaboticalBlocks.buildBlocks(buffer, names.map(name => (entries[name] || {}).scale ?? 1));
    const bounds = new THREE.Box3();
    for (const [index, {positions, normals, uvs}] of groups) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
      geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
      geometry.computeBoundingBox();
      bounds.union(geometry.boundingBox);
      scene.add(new THREE.Mesh(geometry, material(names[index] || 'default', entries[names[index]])));
    }
    if (!bounds.isEmpty()) start = {min: bounds.min, max: bounds.max};
    showNotes();
    applySettings();
    showStart();
    ready = true;
    showReady();
  } catch (error) {
    showStatus('Could not load map: ' + error.message);
  }
});

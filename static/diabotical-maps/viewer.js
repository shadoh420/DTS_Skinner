/* Vanilla Three.js free-flight viewer for the pack written by tools/import_diabotical_map.py: each map's blocks,
   meshed by blocks.js in the game's /export coordinates (y up), drawn in their materials' textures. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const storageKey = 'skinner.diaboticalmaps';
  const settings = {fov: 100, invertX: false, invertY: false, props: true, markers: true};
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
  const loader = new THREE.TextureLoader(), textures = new Map(), materials = new Map();
  // A material by name, once per side: mirrored props are drawn from the back, their triangles' winding reversed.
  function material(name, entry, side = THREE.FrontSide) {
    const key = `${name}|${side}`;
    if (materials.has(key)) return materials.get(key);
    let made;
    if (!entry) made = new THREE.MeshLambertMaterial({color: flatColour(name), side});
    else {
      if (!textures.has(entry.texture)) {
        const texture = loader.load(data + 'textures/' + entry.texture, undefined, undefined, () => { missing++; if (ready) showReady(); });
        texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
        texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
        textures.set(entry.texture, texture);
      }
      // Foliage and the like are cut out by their texture's alpha and seen from both sides.
      made = new THREE.MeshLambertMaterial({map: textures.get(entry.texture), side: entry.cutout ? THREE.DoubleSide : side,
        alphaTest: entry.cutout ? .5 : 0, transparent: !!entry.blend, depthWrite: !entry.blend});
    }
    materials.set(key, made);
    return made;
  }

  const layers = {props: new THREE.Group(), markers: new THREE.Group()};
  scene.add(layers.props, layers.markers);
  // Markers for what the game draws by itself: spawns, pickups, jump pads, teleporters, flags.
  const MARKERS = [[/^spawn/, 0x3fd06a, 'cone'], [/^hpt|^health/, 0x8fe04a], [/^armor/, 0xf0a020], [/^weapon/, 0xd040e0, 'box'],
    [/^ammo/, 0xa080c0, 'box'], [/^(jumppad|jp$)/, 0x30d0f0, 'disc'], [/^(teleport|tpexit)/, 0x4060ff, 'disc'], [/^flag/, 0xff4040, 'cone'],
    [/^(doubledamage|tripledamage|crystal|coin)/, 0xffe040]];
  const SHAPES = {cone: new THREE.ConeGeometry(12, 40, 12).translate(0, 20, 0), box: new THREE.BoxGeometry(20, 20, 20),
    disc: new THREE.CylinderGeometry(30, 30, 4, 20).translate(0, 2, 0), ball: new THREE.SphereGeometry(12, 12, 8)};
  function addMarkers(list) {
    const byStyle = new Map();
    for (const [kind, x, y, z] of list) {
      const style = MARKERS.find(([pattern]) => pattern.test(kind));
      if (style) byStyle.set(style, [...(byStyle.get(style) || []), [x, y, -z]]);
    }
    const matrix = new THREE.Matrix4();
    for (const [[, colour, shape], points] of byStyle) {
      const mesh = new THREE.InstancedMesh(SHAPES[shape || 'ball'], new THREE.MeshLambertMaterial({color: colour, emissive: colour, emissiveIntensity: .35}), points.length);
      points.forEach((point, i) => mesh.setMatrixAt(i, matrix.makeTranslation(...point)));
      layers.markers.add(mesh);
    }
  }
  // Liquids as see-through boxes, their size the entity's scale, centred on it (as the game's water surface is: top at y + height / 2).
  function addLiquids(list, entries) {
    for (const [x, y, z, width, height, depth, name] of list) {
      const colour = entries[name] ? 0x3a7fb0 : flatColour(name || 'liquid');
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth),
        new THREE.MeshLambertMaterial({color: colour, transparent: true, opacity: .45, depthWrite: false}));
      mesh.position.set(x, y, -z);
      layers.markers.add(mesh);
    }
  }
  // Props: each model's triangles (8 floats a corner: position, normal, uv) by material, drawn instanced.
  async function addProps(props, models, entries) {
    const needed = [...new Set(props.map(prop => prop.model))].filter(model => models[model]);
    const buffers = new Map(await Promise.all(needed.map(async model =>
      [model, await get(`${data}models/${models[model].file}`).then(r => r.arrayBuffer())])));
    const geometries = new Map(), matrix = new THREE.Matrix4();
    let drawn = 0, absent = 0;
    for (const {model, material: override, mirrored, matrices} of props) {
      if (!buffers.has(model)) { absent += matrices.length / 12; continue; }
      let at = 0;
      for (const [own, corners] of models[model].groups) {
        const key = `${model}|${at}`;
        if (!geometries.has(key)) {
          const interleaved = new THREE.InterleavedBuffer(new Float32Array(buffers.get(model), at * 32, corners * 8), 8);
          const geometry = new THREE.BufferGeometry();
          geometry.setAttribute('position', new THREE.InterleavedBufferAttribute(interleaved, 3, 0));
          geometry.setAttribute('normal', new THREE.InterleavedBufferAttribute(interleaved, 3, 3));
          geometry.setAttribute('uv', new THREE.InterleavedBufferAttribute(interleaved, 2, 6));
          geometries.set(key, geometry);
        }
        at += corners;
        const name = override || own, entry = entries[name];
        if (entry && entry.hidden) continue;
        const count = matrices.length / 12;
        const mesh = new THREE.InstancedMesh(geometries.get(key), material(name, entry, mirrored ? THREE.BackSide : THREE.FrontSide), count);
        for (let i = 0; i < count; i++) {
          const m = matrices.subarray(i * 12, i * 12 + 12);
          mesh.setMatrixAt(i, matrix.set(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10], m[11], 0, 0, 0, 1));
        }
        mesh.frustumCulled = false;  // r149 culls an instanced mesh by its one model's bounds.
        layers.props.add(mesh);
      }
      drawn += matrices.length / 12;
    }
    return {drawn, absent};
  }

  function showNotes() {
    if (!map) return;
    const parts = [`${map.blocks.toLocaleString()} blocks drawn, map version ${map.version}${map.author ? `, by ${map.author}` : ''}`];
    if (map.props) parts.push(`${map.props.drawn.toLocaleString()} props drawn` + (map.props.absent ? `, ${map.props.absent.toLocaleString()} left out (no model file)` : ''));
    if (map.untextured.length) parts.push('Not in the game files, so drawn in a flat colour: ' + map.untextured.join(', '));
    $('mapNotes').textContent = ' This map — ' + parts.join('. ') + '.';
  }
  $('notes').after(Object.assign(document.createElement('span'), {id: 'mapNotes'}));

  function applySettings() {
    for (const id of ['invertX', 'invertY', 'props', 'markers']) $(id).checked = settings[id];
    layers.props.visible = settings.props;
    layers.markers.visible = settings.markers;
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
  for (const id of ['invertX', 'invertY', 'props', 'markers']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
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
    $('importStatus').textContent = 'Importing maps… the whole game takes about five minutes the first time.';
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
    applySettings();
    showStart();
    if (map.entities) {
      showStatus('Loading props…');
      const [found, models] = await Promise.all([get(`${data}maps/${map.entities}`).then(r => r.arrayBuffer()), get(data + 'models.json').then(r => r.json())]);
      const {props, markers, liquids} = DiaboticalEntities.parseEntities(found);
      addMarkers(markers);
      addLiquids(liquids, entries);
      map.props = await addProps(props, models, entries);
    }
    showNotes();
    ready = true;
    showReady();
  } catch (error) {
    showStatus('Could not load map: ' + error.message);
  }
});

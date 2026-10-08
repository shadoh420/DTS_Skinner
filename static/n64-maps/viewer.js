/* Vanilla Three.js free-flight viewer for the maps copied by tools/import_n64_map.py: N64 level conversions made for
   Blender by L. Spiro (https://github.com/L-Spiro), as .obj pieces whose vertices carry RGBA colour after their position.
   Surfaces are drawn as L. Spiro's Blender material script draws them: unlit, texture times vertex colour, with flags
   read from material names. Used with L. Spiro's permission; no conversion is included. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const storageKey = 'skinner.n64maps';
  const settings = {fov: 90, invertX: false, invertY: false};
  try { Object.assign(settings, JSON.parse(localStorage.getItem(storageKey) || '{}')); } catch (_) { /* Defaults remain usable. */ }
  const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(settings)); } catch (_) { /* Storage may be unavailable. */ } };

  const canvas = $('c');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x10141c);
  const camera = new THREE.PerspectiveCamera(60, 1, 1, 1000);
  camera.rotation.order = 'YXZ';
  const data = '/n64-map-data/';
  let speed = 100, missing = 0, ready = false, map = null, home = null;
  const showStatus = text => { $('status').textContent = text; };
  const showReady = () => showStatus(`${missing ? `Map loaded with ${missing} missing textures` : 'Map ready'} · speed ${Math.round(speed)}`);

  async function get(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} (${response.status})`);
    return response;
  }

  // One texture load per file; each wrap mode it is drawn with is a clone sharing the image.
  const loader = new THREE.TextureLoader(), images = new Map(), textures = new Map();
  function loadTexture(file, wrapS, wrapT) {
    const key = `${file}|${wrapS}|${wrapT}`;
    if (!images.has(file)) images.set(file, new Promise(resolve => loader.load(`${data}maps/${map.id}/${encodeURIComponent(file)}`,
      texture => { texture.anisotropy = 8; resolve(texture); }, undefined, () => { missing++; resolve(null); })));
    if (!textures.has(key)) textures.set(key, images.get(file).then(texture => {
      if (!texture) return null;
      const copy = texture.clone();
      Object.assign(copy, {wrapS, wrapT, needsUpdate: true});
      return copy;
    }));
    return textures.get(key);
  }

  // newmtl name -> texture file (its base name: the import keeps the folder's files only).
  function parseMtl(text, out) {
    let name = null;
    for (const line of text.split('\n')) {
      const [word, ...rest] = line.trim().split(/\s+/);
      if (word === 'newmtl') { name = rest.join(' '); out.set(name, null); }
      else if (word === 'map_Kd' && name !== null) out.set(name, rest.join(' ').split(/[\\/]/).pop());
    }
  }

  // Triangles bucketed by material: positions, RGBA colours (white where the vertex has none) and texture coordinates.
  function parseObj(text) {
    const v = [], vt = [], buckets = new Map();
    let current = null;
    const use = name => { if (!buckets.has(name)) buckets.set(name, {position: [], color: [], uv: []}); current = buckets.get(name); };
    for (const line of text.split('\n')) {
      const fields = line.trim().split(/\s+/);
      if (fields[0] === 'v') v.push(fields.slice(1, 8).map(Number));
      else if (fields[0] === 'vt') vt.push([Number(fields[1]), Number(fields[2])]);
      else if (fields[0] === 'usemtl') use(fields.slice(1).join(' '));
      else if (fields[0] === 'f') {
        if (!current) use('');
        const corners = fields.slice(1).map(corner => corner.split('/').map(Number));
        for (let i = 1; i + 1 < corners.length; i++) for (const [at, uvAt] of [corners[0], corners[i], corners[i + 1]]) {
          const [x, y, z, r = 1, g = 1, b = 1, a = 1] = v[at - 1], [s, t] = vt[uvAt - 1] || [0, 0];
          current.position.push(x, y, z);
          current.color.push(r, g, b, a);
          current.uv.push(s, t);
        }
      }
    }
    return buckets;
  }

  // The flags the conversion writes into material names (m12ClampSMirrorT, m3TopFlagTransparentOpacity0.4_...).
  const materials = new Map();
  function material(name, mtl) {
    if (!materials.has(name)) materials.set(name, (async () => {
      const wrap = axis => name.includes('Mirror' + axis) ? THREE.MirroredRepeatWrapping : name.includes('Clamp' + axis) ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
      const file = mtl.get(name), decal = name.includes('TopFlag'), blended = decal || /Transparent|Opacity/.test(name);
      return new THREE.MeshBasicMaterial({
        map: file ? await loadTexture(file, wrap('S'), wrap('T')) : null, vertexColors: true,
        transparent: blended, depthWrite: !blended, alphaTest: blended ? 0 : .5,  // Opaque surfaces cut out where the texture is clear.
        side: decal || name.includes('CullBoth') ? THREE.DoubleSide : THREE.FrontSide,
        polygonOffset: decal, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
      });
    })());
    return materials.get(name);
  }

  async function addObj(file, mtl) {
    const buckets = [...parseObj(await (await get(`${data}maps/${map.id}/${encodeURIComponent(file)}`)).text())];
    const geometry = new THREE.BufferGeometry(), position = [], color = [], uv = [];
    for (const [index, [, bucket]] of buckets.entries()) {
      geometry.addGroup(position.length / 3, bucket.position.length / 3, index);
      position.push(...bucket.position); color.push(...bucket.color); uv.push(...bucket.uv);
    }
    if (!position.length) return;
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(position, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(color, 4));  // Four components: vertex alpha is drawn.
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    const mesh = new THREE.Mesh(geometry, await Promise.all(buckets.map(([name]) => material(name, mtl))));
    if (buckets.some(([name]) => name.includes('TopFlag'))) mesh.renderOrder = 1;
    scene.add(mesh);
  }

  function applySettings() {
    for (const id of ['invertX', 'invertY']) $(id).checked = settings[id];
    $('fov').value = settings.fov;
    const main = canvas.parentElement, aspect = main.clientWidth / Math.max(main.clientHeight, 1);
    renderer.setSize(main.clientWidth, main.clientHeight, false);
    camera.aspect = aspect;
    // Horizontal field of view, as on the other map pages; Three's is vertical.
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2) / aspect));
    camera.updateProjectionMatrix();
  }
  // No spawn points in a conversion: start above one corner of the map's box, looking at its middle.
  function showHome() {
    if (!home) return;
    camera.position.copy(home.position);
    camera.rotation.set(0, 0, 0);
    camera.lookAt(home.target);
    speed = home.speed;
  }

  $('fov').addEventListener('change', event => { settings.fov = Math.max(30, Math.min(130, Number(event.target.value) || 90)); save(); applySettings(); });
  for (const id of ['invertX', 'invertY']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
  $('reset').addEventListener('click', () => { showHome(); if (ready) showReady(); });
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
    const top = home ? home.speed * 20 : 20000;
    speed = Math.max(top / 4000, Math.min(top, speed * (event.deltaY < 0 ? 1.2 : 1 / 1.2)));
    if (ready) showReady();
  }, {passive: false});
  document.addEventListener('keydown', event => {
    if (event.target.matches('input:not([type=checkbox]), select')) return;
    if (/^(Key[WASD]|Space|Shift(Left|Right))$/.test(event.code)) { keys.add(event.code); event.preventDefault(); }
  });
  document.addEventListener('keyup', event => keys.delete(event.code));

  const clock = new THREE.Clock(), forward = new THREE.Vector3(), right = new THREE.Vector3(), move = new THREE.Vector3();
  renderer.setAnimationLoop(() => {
    const delta = Math.min(clock.getDelta(), .1), held = code => Number(keys.has(code));
    camera.getWorldDirection(forward);
    right.crossVectors(forward, camera.up).normalize();
    move.copy(forward).multiplyScalar(held('KeyW') - held('KeyS')).addScaledVector(right, held('KeyD') - held('KeyA'));
    move.y += held('Space') - held('ShiftLeft') - held('ShiftRight');
    if (move.lengthSq()) camera.position.addScaledVector(move.normalize(), speed * delta);
    renderer.render(scene, camera);
  });
  window.skinnerN64Maps = {renderer, scene, camera};  // For checks in a hidden page, where no frame is drawn.

  $('map').addEventListener('change', event => { location.search = '?map=' + encodeURIComponent(event.target.value); });
  $('import').addEventListener('click', async () => {
    const folder = $('folderPath').value.trim();
    if (!folder) { $('importStatus').textContent = 'Enter the folder you extracted the maps to.'; return; }
    $('import').disabled = true;
    $('importStatus').textContent = 'Importing maps…';
    try {
      const response = await fetch('/import_n64_maps', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({folder, replace: $('replace').checked})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      try { localStorage.setItem(storageKey + '.folder', folder); } catch (_) { /* Path is simply not remembered. */ }
      const failed = Object.entries(result.failed).map(([name, reason]) => `${name}: ${reason}`);
      $('importStatus').textContent = `Imported ${result.imported.length}, skipped ${result.skipped.length} already imported` +
        (failed.length ? `, failed ${failed.length} (${failed.join('; ')})` : '') + '.';
      if (result.imported.length && !failed.length && !map) location.reload();
    } catch (error) { $('importStatus').textContent = error.message; }
    finally { $('import').disabled = false; }
  });
  try { $('folderPath').value = localStorage.getItem(storageKey + '.folder') || ''; } catch (_) { /* Field stays empty. */ }

  try {
    let maps = [];
    try { maps = await (await get(data + 'index.json')).json(); } catch (_) { /* Nothing imported yet. */ }
    if (!maps.length) { $('importPanel').open = true; throw new Error('no maps imported yet. Use Import maps above.'); }
    const wanted = new URLSearchParams(location.search).get('map');
    map = maps.find(item => item.id === wanted) || maps[0];
    const byGame = new Map();
    for (const item of maps) byGame.set(item.game || 'Other', [...(byGame.get(item.game || 'Other') || []), item]);
    $('map').replaceChildren(...[...byGame].sort(([a], [b]) => (a === 'Other') - (b === 'Other') || a.localeCompare(b)).map(([game, items]) => {
      const element = Object.assign(document.createElement('optgroup'), {label: game});
      element.append(...items.map(item => new Option(item.name, item.id)));
      return element;
    }));
    $('map').value = map.id;
    document.title = `${map.name} — N64 Maps`;
    applySettings();
    const mtl = new Map();
    for (const file of map.mtls) parseMtl(await (await get(`${data}maps/${map.id}/${encodeURIComponent(file)}`)).text(), mtl);
    let loaded = 0;
    await Promise.all(map.objs.map(async file => { await addObj(file, mtl); showStatus(`Loading pieces ${++loaded}/${map.objs.length}`); }));
    const box = new THREE.Box3().setFromObject(scene), size = box.getSize(new THREE.Vector3()).length() || 1;
    camera.near = size / 5000;
    camera.far = size * 4;
    home = {target: box.getCenter(new THREE.Vector3()), position: new THREE.Vector3(box.min.x, box.max.y + size * .1, box.max.z), speed: size / 10};
    applySettings();
    showHome();
    ready = true;
    showReady();
  } catch (error) { showStatus('Map could not load: ' + error.message); }
});

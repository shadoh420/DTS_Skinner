/* Vanilla Three.js free-flight viewer for the pack written by tools/import_t1_map.py.
   The pack is already in viewer space: Tribes (x, y, z-up) -> Three (x, z, -y). */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const storageKey = 'skinner.t1maps';
  const settings = {fov: 90, fog: true, invertX: false, invertY: false};
  try { Object.assign(settings, JSON.parse(localStorage.getItem(storageKey) || '{}')); } catch (_) { /* Defaults remain usable. */ }
  const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(settings)); } catch (_) { /* Storage may be unavailable. */ } };

  const canvas = $('c');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 1, .2, 9000);
  camera.rotation.order = 'YXZ';
  const data = '/t1-map-data/';
  let speed = 40, missing = 0, ready = false, map = null, mapId = '';
  const showStatus = text => { $('status').textContent = text; };
  const showReady = () => showStatus(`${missing ? `Map loaded with ${missing} missing assets` : 'Map ready'} · speed ${Math.round(speed)}`);

  async function get(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} (${response.status})`);
    return response;
  }
  const loader = new THREE.TextureLoader(), textures = new Map();
  function loadTexture(url, repeat) {
    if (!textures.has(url)) textures.set(url, new Promise(resolve => loader.load(url, texture => {
      texture.flipY = false;
      texture.anisotropy = 8;
      if (repeat) texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      resolve(texture);
    }, undefined, () => { missing++; resolve(null); })));
    return textures.get(url);
  }

  async function buildTerrain(terrain) {
    const [heightData, squareData, lightData] = await Promise.all(['heights.bin', 'materials.bin', 'light.bin']
      .map(async name => (await get(`${data}maps/${mapId}/${name}`)).arrayBuffer()));
    const heights = new Float32Array(heightData), squares = new Uint8Array(squareData), light = new Uint16Array(lightData);
    const size = terrain.squares, unit = terrain.unit, lightStep = (terrain.lightWidth - 1) / size;
    // Texture corners (lower-left, lower-right, upper-right, upper-left) for the 3-bit square orientation:
    // bit 2 and bit 4 mirror, bit 1 rotates, applied in that order as DarkStar does.
    const corners = [];
    for (let orientation = 0; orientation < 8; orientation++) {
      const uv = {0: [0, 1], 2: [0, 0], 4: [1, 0], 6: [1, 1]};
      const swap = (a, b) => { [uv[a], uv[b]] = [uv[b], uv[a]]; };
      if (orientation & 2) { swap(0, 6); swap(2, 4); }
      if (orientation & 4) { swap(0, 2); swap(4, 6); }
      if (orientation & 1) { swap(2, 4); swap(2, 6); swap(2, 0); }
      corners.push([uv[0], uv[6], uv[4], uv[2]]);
    }
    // Squares with an empty level are holes cut for buildings. Sorting by texture keeps one draw per texture.
    const order = [...Array(size * size).keys()].filter(square => !(squares[square * 2] >> 3 & 7))
      .sort((a, b) => squares[a * 2 + 1] - squares[b * 2 + 1]);
    const positions = new Float32Array(order.length * 12), uvs = new Float32Array(order.length * 8);
    const colors = new Float32Array(order.length * 12), indices = new Uint32Array(order.length * 6);
    const geometry = new THREE.BufferGeometry(), slots = [];
    order.forEach((square, n) => {
      const x = square % size, y = Math.floor(square / size), slot = squares[square * 2 + 1];
      [[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1]].forEach(([px, py], corner) => {
        positions.set([px * unit, heights[py * (size + 1) + px], -py * unit], n * 12 + corner * 3);
        uvs.set(corners[squares[square * 2] & 7][corner], n * 8 + corner * 2);
        const packed = light[py * lightStep * terrain.lightWidth + px * lightStep];
        colors.set([(packed >> 8 & 15) / 15, (packed >> 4 & 15) / 15, (packed & 15) / 15], n * 12 + corner * 3);
      });
      // DarkStar alternates the split diagonal in a checkerboard.
      indices.set(((x ^ y) & 1 ? [0, 1, 3, 1, 2, 3] : [0, 1, 2, 0, 2, 3]).map(corner => n * 4 + corner), n * 6);
      if (slots[slots.length - 1] !== slot) { slots.push(slot); geometry.addGroup(n * 6, 0, slots.length - 1); }
      geometry.groups[geometry.groups.length - 1].count += 6;
    });
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    geometry.computeBoundingSphere();
    const materials = await Promise.all(slots.map(async slot => {
      const texture = terrain.textures[slot] && await loadTexture(data + 'textures/' + terrain.textures[slot]);
      return new THREE.MeshBasicMaterial(texture ? {map: texture, vertexColors: true} : {color: 0xcc00cc});
    }));
    const width = size * unit, [x, y, z] = terrain.position;
    for (let row = 0; row < terrain.rows; row++) for (let column = 0; column < terrain.columns; column++) {
      const mesh = new THREE.Mesh(geometry, materials);
      mesh.position.set(x + column * width, z, -(y + row * width));
      scene.add(mesh);
    }
  }

  const models = new Map();
  function loadModel(name, pack, lightUv) {
    // Catalog models come from the workshop; buildings the catalog lacks were exported into the pack at import.
    // Lightmap coordinates are per building type, so lit placements of one building still share a geometry.
    const key = name + '|' + (lightUv || '');
    if (!models.has(key)) models.set(key, (async () => {
      const [model, uv2] = await Promise.all([
        get(pack ? data + 'models/' + name : `/model_json/${encodeURIComponent(name)}?game=t1`).then(response => response.json()),
        lightUv && get(data + 'textures/' + lightUv).then(response => response.arrayBuffer())]);
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(model.vertices, 3));
      geometry.setAttribute('uv', new THREE.Float32BufferAttribute(model.uvs, 2));
      if (uv2) geometry.setAttribute('uv2', new THREE.BufferAttribute(new Float32Array(uv2), 2));
      // Interiors store one group per surface; merge to one draw per material.
      const byMaterial = model.material_textures.map(() => []);
      const parts = model.groups && model.groups.length ? model.groups : [{start: 0, count: model.indices.length, materialIndex: 0}];
      for (const part of parts) byMaterial[part.materialIndex].push(...model.indices.slice(part.start, part.start + part.count));
      geometry.setIndex(byMaterial.flat());
      let start = 0;
      byMaterial.forEach((list, index) => { if (list.length) geometry.addGroup(start, list.length, index); start += list.length; });
      geometry.computeVertexNormals();
      const materials = await Promise.all(model.material_textures.map(async file => {
        const blank = file.startsWith('[');
        const texture = blank || !file ? null : await loadTexture(pack ? data + 'textures/' + file : `/texture/${encodeURIComponent(file)}?game=t1`, true);
        return new THREE.MeshLambertMaterial({map: texture, color: texture ? 0xffffff : blank ? 0x999999 : 0xcc00cc, side: THREE.DoubleSide});
      }));
      return {geometry, materials};
    })());
    return models.get(key);
  }
  const placeholder = new THREE.BoxGeometry(4, 4, 4).translate(0, 2, 0), magenta = new THREE.MeshBasicMaterial({color: 0xcc00cc});
  async function addObject(object) {
    let mesh;
    try {
      if (!object.model) throw new Error('No preview model');
      const model = await loadModel(object.model, object.source === 'pack', object.light && object.light.uv);
      const lightMap = object.light && await loadTexture(data + 'textures/' + object.light.map);
      if (lightMap) { lightMap.generateMipmaps = false; lightMap.minFilter = THREE.LinearFilter; }  // Mip levels would bleed between atlas cells.
      // The mission lightmap already holds the sun and the building's own lights: texture × lightmap, no scene lights.
      mesh = new THREE.Mesh(model.geometry, !lightMap ? model.materials : model.materials.map(material => new THREE.MeshBasicMaterial(
        {map: material.map, color: material.color, side: THREE.DoubleSide, lightMap})));
    } catch (_) { missing++; mesh = new THREE.Mesh(placeholder, magenta); }
    mesh.name = object.name;
    mesh.applyMatrix4(new THREE.Matrix4().fromArray(object.matrix));
    scene.add(mesh);
  }

  // Sky: sixteen textured panels around the camera with flat caps above and below, drawn behind everything.
  // The dome is scaled down to sit inside the clip range; centred on the camera, its size does not show.
  const sky = new THREE.Group(), haze = new THREE.Color();
  async function buildSky(dome, visible) {
    if (!dome.textures) { scene.background = new THREE.Color(`rgb(${dome.color.join(',')})`); return; }
    const point = (index, upper) => {
      const angle = THREE.MathUtils.degToRad(67.5 + dome.feature - 22.5 * index);
      return [Math.cos(angle) * visible * 1.05, (upper ? .75 : -.125) * dome.size, -Math.sin(angle) * visible * 1.05];
    };
    const add = (positions, uvs, indices, material) => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions.flat(), 3));
      if (uvs) geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
      geometry.setIndex(indices);
      const mesh = new THREE.Mesh(geometry, Object.assign(material, {depthTest: false, depthWrite: false, fog: false, side: THREE.DoubleSide}));
      mesh.renderOrder = -1;
      mesh.frustumCulled = false;
      sky.add(mesh);
    };
    await Promise.all(dome.textures.map(async (file, slot) => {
      const texture = file && await loadTexture(data + 'textures/' + file);
      if (texture) add([point(slot, false), point(slot + 1, false), point(slot + 1, true), point(slot, true)],
        [0, 1, 1, 1, 1, 0, 0, 0], [0, 1, 2, 0, 2, 3], new THREE.MeshBasicMaterial({map: texture}));
    }));
    for (const upper of [true, false]) add(
      [[0, upper ? dome.size : -dome.size, 0], ...Array.from({length: 16}, (_, slot) => point(slot, upper))], null,
      Array.from({length: 16}, (_, slot) => [0, slot + 1, (slot + 1) % 16 + 1]).flat(),
      new THREE.MeshBasicMaterial({color: `rgb(${(upper ? dome.top : dome.bottom).join(',')})`}));
    sky.scale.setScalar(10 / Math.max(visible * 1.05, dome.size));
    scene.add(sky);
  }

  // Rain or snow: particles in a box around the camera that wrap as they or the camera leave it. Counts, box size
  // and fall speeds follow ArenaPrototype's precipitation. Buildings do not shelter from it.
  let weather = null;
  function buildWeather({rain, intensity, wind}) {
    const count = Math.floor(intensity * (rain ? 2048 : 1024)), radius = rain ? 110 : 200, size = radius * 2;
    const step = [wind[0] * (rain ? 2 : 1), rain ? -150 : -35, -wind[1] * (rain ? 2 : 1)];
    const drops = Float32Array.from({length: count * 3}, () => Math.random() * size);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * (rain ? 6 : 3)), 3));
    const particles = rain  // Rain is a short streak along its fall, snow a flake.
      ? new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({color: 0xaaaaaa, transparent: true, opacity: .55}))
      : new THREE.Points(geometry, new THREE.PointsMaterial({color: 0xffffff, size: .5}));
    particles.frustumCulled = false;
    scene.add(particles);
    weather = delta => {
      const positions = geometry.attributes.position.array, centre = camera.position.toArray();
      for (let index = 0; index < drops.length; index++) {
        const axis = index % 3, offset = drops[index] + step[axis] * delta - centre[axis] + radius;
        drops[index] = centre[axis] + (offset % size + size) % size - radius;
        const at = rain ? (index - axis) * 2 + axis : index;
        positions[at] = drops[index];
        if (rain) positions[at + 3] = drops[index] - step[axis] * .03;
      }
      geometry.attributes.position.needsUpdate = true;
    };
  }

  function applySettings() {
    for (const id of ['fog', 'invertX', 'invertY']) $(id).checked = settings[id];
    $('fov').value = settings.fov;
    const main = canvas.parentElement, aspect = main.clientWidth / Math.max(main.clientHeight, 1);
    renderer.setSize(main.clientWidth, main.clientHeight, false);
    camera.aspect = aspect;
    // Tribes field of view is horizontal; Three's is vertical.
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2) / aspect));
    if (map) {
      // Some missions set haze beyond the visible distance: no haze band, just the far limit.
      const visible = map.terrain.visibleDistance;
      scene.fog = settings.fog ? new THREE.Fog(haze, Math.min(map.terrain.hazeDistance, visible - 1), visible) : null;
      camera.far = settings.fog ? map.terrain.visibleDistance : 9000;
    }
    camera.updateProjectionMatrix();
  }
  function showViewpoint(index) {
    if (!map || !map.viewpoints[index]) return;
    const matrix = new THREE.Matrix4().fromArray(map.viewpoints[index]);
    camera.position.setFromMatrixPosition(matrix);
    camera.rotation.setFromRotationMatrix(matrix, 'YXZ');
    camera.rotation.z = 0;
  }

  $('fov').addEventListener('change', event => { settings.fov = Math.max(30, Math.min(110, Number(event.target.value) || 90)); save(); applySettings(); });
  for (const id of ['fog', 'invertX', 'invertY']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
  $('reset').addEventListener('click', () => { speed = 40; showViewpoint(0); if (ready) showReady(); });
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
    speed = Math.max(2, Math.min(2000, speed * (event.deltaY < 0 ? 1.2 : 1 / 1.2)));
    if (ready) showReady();
  }, {passive: false});
  document.addEventListener('keydown', event => {
    if (event.target.matches('input:not([type=checkbox]), select')) return;
    if (/^Digit[1-9]$/.test(event.code)) showViewpoint(Number(event.code.slice(5)) - 1);
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
    sky.position.copy(camera.position);
    if (weather) weather(delta);
    renderer.render(scene, camera);
  });

  $('map').addEventListener('change', event => { location.search = '?map=' + encodeURIComponent(event.target.value); });
  $('import').addEventListener('click', async () => {
    const game = $('gamePath').value.trim();
    if (!game) { $('importStatus').textContent = 'Enter your Tribes folder.'; return; }
    $('import').disabled = true;
    $('importStatus').textContent = 'Importing maps… a full install can take a few minutes.';
    try {
      const response = await fetch('/import_t1_maps', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({game, missions: $('missionPath').value.trim(), replace: $('replace').checked})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      try { localStorage.setItem(storageKey + '.game', game); } catch (_) { /* Path is simply not remembered. */ }
      const failed = Object.entries(result.failed).map(([name, reason]) => `${name}: ${reason}`);
      $('importStatus').textContent = `Imported ${result.imported.length}, skipped ${result.skipped.length} already imported` +
        (failed.length ? `, failed ${failed.length} (${failed.join('; ')})` : '') + '.';
      if (result.imported.length && !failed.length) location.search = '?map=' + encodeURIComponent(result.imported[0].toLowerCase().replace(/[^a-z0-9_-]/g, '_'));
    } catch (error) { $('importStatus').textContent = error.message; }
    finally { $('import').disabled = false; }
  });
  try { $('gamePath').value = localStorage.getItem(storageKey + '.game') || ''; } catch (_) { /* Field stays empty. */ }

  try {
    let maps = [];
    try { maps = await (await get(data + 'index.json')).json(); } catch (_) { /* No pack yet. */ }
    if (!maps.length) { $('importPanel').open = true; throw new Error('no maps imported yet. Use Import maps above.'); }
    mapId = new URLSearchParams(location.search).get('map') || '';
    if (!maps.some(item => item.id === mapId)) mapId = (maps.find(item => item.id === 'raindance') || maps[0]).id;
    $('map').replaceChildren(...maps.map(item => new Option(item.type ? `${item.name} · ${item.type}` : item.name, item.id)));
    $('map').value = mapId;
    map = await (await get(`${data}maps/${mapId}/scene.json`)).json();
    document.title = `${map.mission} — T1 Maps`;
    scene.background = haze.set(`rgb(${map.haze.join(',')})`);
    if (map.sky) await buildSky(map.sky, map.terrain.visibleDistance);  // Packs imported before the sky was added have none.
    scene.add(new THREE.AmbientLight(new THREE.Color(...map.sun.ambient)));
    const sun = new THREE.DirectionalLight(new THREE.Color(...map.sun.intensity));
    // Toward the sun as the importer places it (azimuth + 90° from file x, incidence up). Shades only what has no lightmap.
    const azimuth = THREE.MathUtils.degToRad(map.sun.azimuth), elevation = THREE.MathUtils.degToRad(map.sun.incidence);
    sun.position.set(-Math.sin(azimuth) * Math.cos(elevation), Math.sin(elevation), -Math.cos(azimuth) * Math.cos(elevation));
    scene.add(sun);
    if (map.weather) buildWeather(map.weather);
    applySettings();
    showViewpoint(0);
    showStatus('Loading terrain…');
    await buildTerrain(map.terrain);
    let loaded = 0;
    await Promise.all(map.objects.map(async object => { await addObject(object); showStatus(`Loading map objects ${++loaded}/${map.objects.length}`); }));
    $('notes').textContent += map.warnings.length ? ' This map: ' + map.warnings.join('. ') + '.' : '';
    ready = true;
    showReady();
  } catch (error) { showStatus('Map could not load: ' + error.message); }
});

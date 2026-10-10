/* Vanilla Three.js free-flight viewer for the pack written by tools/import_unreal_map.py.
   Positions are already in Three's axes and metres; each polygon faces counter-clockwise. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const quake = document.body.dataset.game === 'quake';
  const quake2 = document.body.dataset.game === 'quake2';
  const storageKey = quake2 ? 'skinner.quake2maps' : quake ? 'skinner.quakemaps' : 'skinner.unrealmaps';
  const settings = {fov: 90, invertX: false, invertY: false};
  try { Object.assign(settings, JSON.parse(localStorage.getItem(storageKey) || '{}')); } catch (_) { /* Defaults remain usable. */ }
  const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(settings)); } catch (_) { /* Storage may be unavailable. */ } };

  const canvas = $('c');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.autoClear = false;  // The sky is drawn first, then the level over it.
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 1, .05, 5000), skyCamera = new THREE.PerspectiveCamera(60, 1, .05, 5000);
  camera.rotation.order = 'YXZ';
  const skyTurn = new THREE.Quaternion();
  // The game's display gamma, as a power on the finished frame (drawn into a target and ramped onto the screen, after
  // blending). Unreal: 227's OpenGL device ramps by pow(c, 1 / (2.5 * Brightness)), Brightness 0.5 by default (fitted to
  // 227 shots of NyLeve's and Vortex2's starts). UT: 0.67, fitted to UT 469 shots (D3D11, Brightness 0.7) of
  // DM-Deck16][, DM-Morpheus and DM-Turbine's starts.
  const POWER = {unreal: 1 / (2.5 * .5), ut: .67, quake: 1, hipnotic: 1, rogue: 1, quake2: 1, xatrix: 1, ctf: 1,
    qextras: 1, dopa: 1, mg1: 1, mg3: 1, qctf: 1};
  const frame = new THREE.WebGLRenderTarget(1, 1, {samples: 4});
  const ramp = new THREE.Scene(), flat = new THREE.Camera();
  ramp.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({
    uniforms: {frame: {value: frame.texture}, power: {value: POWER.unreal}}, depthTest: false, depthWrite: false,
    vertexShader: 'varying vec2 at; void main() { at = uv; gl_Position = vec4(position.xy, 0., 1.); }',
    fragmentShader: 'uniform sampler2D frame; uniform float power; varying vec2 at;' +
      'void main() { gl_FragColor = vec4(pow(texture2D(frame, at).rgb, vec3(power)), 1.); }'})));
  // At least 1 x 1: a hidden page has no size, and a zero-sized target fails every draw.
  const fitFrame = () => { const size = renderer.getDrawingBufferSize(new THREE.Vector2()); frame.setSize(Math.max(size.x, 1), Math.max(size.y, 1)); };
  const GAMES = quake2 ? ['quake2', 'xatrix', 'rogue', 'ctf'].map(id => [id, 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Quake 2\\baseq2']) : quake ? ['quake', 'hipnotic', 'rogue', 'qextras', 'dopa', 'mg1', 'mg3', 'qctf'].map(id => [id, 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Quake']) : [['unreal', 'C:\\Unreal'], ['ut', 'C:\\UnrealTournament']];  // Each game's pack and usual folder.
  const dataRoot = id => quake2 ? `/quake2-map-data/${id === 'quake2' ? '' : id + '/'}` : quake ? `/quake-map-data/${id === 'quake' ? '' : id + '/'}` : `/unreal-map-data/${id}/`;
  let data = dataRoot(GAMES[0][0]);  // The shown map's pack.
  let speed = 8, missing = 0, ready = false, map = null;
  const showStatus = text => { $('status').textContent = text; };
  const showReady = () => showStatus(`${missing ? `Map loaded with ${missing} missing textures` : 'Map ready'} · speed ${speed.toFixed(1)} m/s`);

  async function get(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} (${response.status})`);
    return response;
  }
  const loader = new THREE.TextureLoader(), textures = new Map();
  function loadTexture(file) {
    if (!textures.has(file)) textures.set(file, new Promise(resolve => loader.load(data + 'textures/' + file, texture => {
      texture.flipY = false;  // The game's v runs down the image, as the rows do without the flip.
      texture.wrapS = texture.wrapT = quake ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
      texture.anisotropy = quake || quake2 ? 1 : 8;
      if (quake2) { texture.magFilter = THREE.LinearFilter; texture.minFilter = THREE.LinearMipmapNearestFilter; }
      if (quake) { texture.magFilter = texture.minFilter = THREE.NearestFilter; texture.generateMipmaps = false; }
      resolve(texture);
    }, undefined, () => { missing++; resolve(null); })));
    return textures.get(file);
  }

  // Poly flags (EPolyFlags): masked 0x2, translucent 0x4, modulated 0x40, fake backdrop 0x80 (where the sky shows),
  // two-sided 0x100. Blending as the game's OpenGL device draws them: translucent adds the texture over what is behind
  // (one, one minus source colour), modulated doubles what is behind by it (destination colour, source colour); neither
  // hides what is behind it from later surfaces.
  const MASKED = 0x2, TRANSLUCENT = 0x4, MODULATED = 0x40, FAKE_BACKDROP = 0x80, TWO_SIDED = 0x100;
  const panning = [];
  let lightMap = null;  // The map's lightmaps in one atlas: the game draws texture x lightmap x 2.
  let quakeColormap = null;
  function surfaceMaterial(group, texture) {
    if (quake2) return window.quake2Maps.material(group, texture, lightMap);
    if (quake) return window.quakeMaps.material(group, texture, lightMap, quakeColormap, map.lighting === 'rgb', map.skybox);
    const side = group.flags & TWO_SIDED ? THREE.DoubleSide : THREE.FrontSide;
    // A backdrop is a hole onto the sky drawn before the level: it keeps the level behind it hidden, draws nothing.
    if (group.flags & FAKE_BACKDROP) return new THREE.MeshBasicMaterial({colorWrite: false, side});
    if (texture && group.pan) {  // Auto-panning: its own copy of the texture, moved each frame.
      texture = texture.clone();
      texture.needsUpdate = true;
      panning.push({texture, pan: group.pan});
    }
    // Meshes carry their light in vertex colours (their lightmap texel is white); the level's colours are white.
    const common = {map: texture, color: texture ? 0xffffff : 0x808080, side, vertexColors: !!map.colors,
      ...(lightMap ? {lightMap, lightMapIntensity: 2} : {})};
    if (group.flags & (TRANSLUCENT | MODULATED)) {
      const add = group.flags & TRANSLUCENT;
      return new THREE.MeshBasicMaterial({...common, transparent: true, depthWrite: false, blending: THREE.CustomBlending,
        blendSrc: add ? THREE.OneFactor : THREE.DstColorFactor, blendDst: add ? THREE.OneMinusSrcColorFactor : THREE.SrcColorFactor});
    }
    return new THREE.MeshBasicMaterial({...common, alphaTest: group.flags & MASKED ? .5 : 0});
  }
  async function buildWorld(buffer) {
    const vertices = map.vertices, geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(buffer, 0, vertices * 3), 3));
    geometry.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(buffer, vertices * 12, vertices * 2), 2));
    if (map.lightmap) {  // Packs from before the lighting have no lightmap coordinates and draw evenly lit.
      geometry.setAttribute('uv2', new THREE.BufferAttribute(new Float32Array(buffer, vertices * 20, vertices * 2), 2));
      lightMap = await new Promise((resolve, reject) => loader.load(`${data}maps/${map.id}/lightmap.png`, resolve, undefined, reject));
      lightMap.flipY = false;
      lightMap.generateMipmaps = false;
      lightMap.minFilter = THREE.LinearFilter;
    }
    if (map.colors) geometry.setAttribute('color', new THREE.BufferAttribute(new Uint8Array(buffer, vertices * 28, vertices * 4), 4, true));
    const indexAt = vertices * (map.colors ? 32 : map.lightmap ? 28 : 20);
    geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(buffer, indexAt, map.indices), 1));
    if (quake) quakeColormap = await loadTexture('colormap.png');
    if (quake2) await window.quake2Maps.loadSky(map.skybox, loadTexture);
    if (quake && map.skybox) await window.quake2Maps.loadSky(map.skybox, loadTexture);
    const materials = await Promise.all(map.groups.map(async (group, index) => {
      geometry.addGroup(group.start, group.count, index);
      return surfaceMaterial(group, !quake2 && group.flags & FAKE_BACKDROP ? null : await loadTexture(group.texture));
    }));
    scene.add(new THREE.Mesh(geometry, materials));
  }

  function showNotes() {
    const parts = [];
    if (quake) {
      const guesses = [...new Set((map.instances || []).filter(item => item.guess).map(item => item.classname))];
      if (guesses.length) parts.push('Brush positions not verified, shown at authored origin: ' + guesses.join(', '));
      const unhandled = (map.bspx || []).filter(item => !item.handled && item.reason === 'not handled').map(item => item.name);
      if (unhandled.length) parts.push('BSPX extensions not handled: ' + unhandled.join(', '));
    }
    if ((map.missing || []).length) parts.push((quake ? 'Missing source textures (fallback shown): ' : 'Textures the game files do not hold, drawn grey: ') + map.missing.join('; '));
    if (missing) parts.push(`${missing} textures of the pack did not load`);
    $('mapNotes').textContent = parts.length ? ' This map — ' + parts.join('. ') + '.' : '';
  }
  $('notes').after(Object.assign(document.createElement('span'), {id: 'mapNotes'}));

  function applySettings() {
    for (const id of ['invertX', 'invertY']) $(id).checked = settings[id];
    $('fov').value = settings.fov;
    const main = canvas.parentElement, aspect = main.clientWidth / Math.max(main.clientHeight, 1);
    renderer.setSize(main.clientWidth, main.clientHeight, false);
    fitFrame();
    camera.aspect = aspect;
    // The game's field of view is horizontal; Three's is vertical.
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2) / aspect));
    camera.updateProjectionMatrix();
  }
  // A viewpoint's yaw turns from Unreal's x (the page's +z) toward its y (the page's -x); pitch is up.
  function showViewpoint(index) {
    const view = map && map.viewpoints[index];
    if (!view) return;
    camera.position.fromArray(view.origin);
    camera.rotation.set(THREE.MathUtils.degToRad(view.pitch), Math.PI - THREE.MathUtils.degToRad(view.yaw), quake2 ? THREE.MathUtils.degToRad(view.roll || 0) : 0);
  }

  $('fov').addEventListener('change', event => { settings.fov = Math.max(30, Math.min(130, Number(event.target.value) || 90)); save(); applySettings(); });
  for (const id of ['invertX', 'invertY']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
  $('reset').addEventListener('click', () => { speed = 8; showViewpoint(0); if (ready) showReady(); });
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
    speed = Math.max(.5, Math.min(500, speed * (event.deltaY < 0 ? 1.2 : 1 / 1.2)));
    if (ready) showReady();
  }, {passive: false});
  document.addEventListener('keydown', event => {
    if (event.target.matches('input:not([type=checkbox]), select')) return;
    if (/^Digit[1-9]$/.test(event.code)) showViewpoint(Number(event.code.slice(5)) - 1);
    if (/^(Key[WASD]|Space|Shift(Left|Right))$/.test(event.code)) { keys.add(event.code); event.preventDefault(); }
  });
  document.addEventListener('keyup', event => keys.delete(event.code));

  // An Unreal rotator (pitch, yaw, roll; 65536 to a turn) as a rotation in the page's axes: the matrix of
  // tools/import_unreal.py rotation(), with Unreal's x, y, z read as the page's z, -x, y.
  const unrealTurn = rotator => {
    const [p, y, r] = rotator.map(a => a * Math.PI / 32768);
    const [cp, sp, cy, sy, cr, sr] = [Math.cos(p), Math.sin(p), Math.cos(y), Math.sin(y), Math.cos(r), Math.sin(r)];
    const columns = [[cp * cy, cp * sy, sp], [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
      [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]];
    const axis = [[1, -1], [2, 1], [0, 1]];  // Page axis: Unreal axis, sign.
    const rows = axis.map(([i, si]) => axis.map(([j, sj]) => columns[j][i] * si * sj));
    return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().setFromMatrix3(new THREE.Matrix3().set(...rows.flat())));
  };

  // The sky zone is the level seen from the SkyZoneInfo, turned with the view and by the zone's own rotation (turning
  // on by its rate where the game turns it); the level is drawn over it, its backdrops leaving it showing; then the
  // display gamma, onto `target` (the screen by default).
  function draw(seconds, target = null) {
    if (quake) window.quakeMaps.time.value = seconds;
    if (quake2) window.quake2Maps.time.value = seconds;
    for (const {texture, pan} of panning) texture.offset.set(pan[0] * seconds % 1, pan[1] * seconds % 1);
    if (map && map.sky && map.sky.rate) skyTurn.copy(unrealTurn(map.sky.rotator.map((a, i) => a + map.sky.rate[i] * seconds)));
    if (target) frame.setSize(target.width, target.height);
    renderer.setRenderTarget(frame);
    renderer.clear();
    if (map && map.sky) {
      skyCamera.position.fromArray(map.sky.origin);
      skyCamera.projectionMatrix.copy(camera.projectionMatrix);
      skyCamera.quaternion.copy(skyTurn).multiply(camera.quaternion);
      renderer.render(scene, skyCamera);
      renderer.clearDepth();
    }
    renderer.render(scene, camera);
    renderer.setRenderTarget(target);
    renderer.render(ramp, flat);
    if (target) fitFrame();
  }
  const clock = new THREE.Clock(), forward = new THREE.Vector3(), right = new THREE.Vector3(), move = new THREE.Vector3();
  let seconds = 0;
  renderer.setAnimationLoop(() => {
    const delta = Math.min(clock.getDelta(), .1), held = code => Number(keys.has(code));
    camera.getWorldDirection(forward);
    right.crossVectors(forward, camera.up).normalize();
    move.copy(forward).multiplyScalar(held('KeyW') - held('KeyS')).addScaledVector(right, held('KeyD') - held('KeyA'));
    move.y += held('Space') - held('ShiftLeft') - held('ShiftRight');
    if (move.lengthSq()) camera.position.addScaledVector(move.normalize(), speed * delta);
    seconds += delta;
    draw(seconds);
  });
  window[quake2 ? 'skinnerQuake2Maps' : quake ? 'skinnerQuakeMaps' : 'skinnerUnrealMaps'] = {renderer, scene, camera, draw, showViewpoint, unrealTurn};  // For checks in a hidden page, where no frame is drawn.

  $('map').addEventListener('change', event => { location.search = '?map=' + encodeURIComponent(event.target.value); });
  // Each game's folder is remembered on its own (Unreal's under the key it had before UT maps).
  const pathKey = () => storageKey + ($('importGame').value === 'unreal' ? '.game' : '.game.' + $('importGame').value);
  const showPath = () => {
    $('gamePath').placeholder = GAMES.find(([id]) => id === $('importGame').value)[1];
    try { $('gamePath').value = localStorage.getItem(pathKey()) || ''; } catch (_) { $('gamePath').value = ''; }
  };
  $('importGame').addEventListener('change', showPath);
  $('import').addEventListener('click', async () => {
    const path = $('gamePath').value.trim() || $('gamePath').placeholder, chosen = $('importGame').value;
    $('import').disabled = true;
    $('importStatus').textContent = quake ? 'Importing classic Quake maps…' : 'Importing maps… a full install takes two to three minutes.';
    try {
      const response = await fetch(quake2 ? '/import_quake2_maps' : quake ? '/import_quake_maps' : '/import_unreal_maps', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path, game: chosen, replace: $('replace').checked})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      try { localStorage.setItem(pathKey(), path); } catch (_) { /* Path is simply not remembered. */ }
      const failed = Object.entries(result.failed).map(([name, reason]) => `${name}: ${reason}`);
      $('importStatus').textContent = `Imported ${result.imported.length}, skipped ${result.skipped.length}${quake ? '' : ' already imported'}` +
        (failed.length ? `, failed ${failed.length} (${failed.join('; ')})` : '') + '.';
      // Reload to list a game's maps the first time they arrive.
      if (result.imported.length && !failed.length && (quake || !map || !$('map').querySelector(`option[value^="${chosen}/"]`))) location.reload();
    } catch (error) { $('importStatus').textContent = error.message; }
    finally { $('import').disabled = false; }
  });
  showPath();

  try {
    // Both games' maps in one list, each as GAME/ID; a game not imported yet has no index.
    const maps = [];
    for (const [id] of GAMES) {
      try { maps.push(...(await (await get(`${dataRoot(id)}index.json`)).json()).map(item => ({...item, game: id, key: `${id}/${item.id}`}))); }
      catch (_) { /* Not imported. */ }
    }
    if (!maps.length) { $('importPanel').open = true; throw new Error('no maps imported yet. Use Import maps above.'); }
    let key = new URLSearchParams(location.search).get('map') || '';
    if (!key.includes('/')) key = GAMES[0][0] + '/' + key;  // Links from before UT maps name an Unreal map alone.
    const item = maps.find(found => found.key === key) || maps.find(found => found.key === (quake2 ? 'quake2/base1' : quake ? 'quake/start' : 'unreal/nyleve')) || maps[0];
    const byGroup = new Map();
    for (const found of maps) byGroup.set(found.group, [...(byGroup.get(found.group) || []), found]);
    $('map').replaceChildren(...[...byGroup].map(([group, items]) => {
      const element = Object.assign(document.createElement('optgroup'), {label: group});
      element.append(...items.map(found => new Option(found.title ? `${found.name} · ${found.title}` : found.name, found.key)));
      return element;
    }));
    $('map').value = item.key;
    data = dataRoot(item.game);
    ramp.children[0].material.uniforms.power.value = POWER[item.game];
    // Offer the import of the game whose maps are not there yet.
    const absent = GAMES.find(([id]) => !maps.some(found => found.game === id));
    if (absent) { $('importGame').value = absent[0]; showPath(); }
    const mapId = item.id;
    map = {...await (await get(`${data}maps/${mapId}/scene.json`)).json(), id: mapId};
    document.title = `${map.title || map.name} — ${quake2 ? 'Quake II' : quake ? 'Quake' : 'Unreal'} Maps`;
    if (map.sky) skyTurn.setFromRotationMatrix(new THREE.Matrix4().setFromMatrix3(new THREE.Matrix3().set(...map.sky.rotation)));
    applySettings();
    showViewpoint(0);
    await buildWorld(await (await get(`${data}maps/${mapId}/geometry.bin`)).arrayBuffer());
    showNotes();
    ready = true;
    showReady();
  } catch (error) { showStatus('Map could not load: ' + error.message); }
});

/* Vanilla Three.js free-flight viewer for the pack written by tools/import_diabotical_map.py: each map's blocks,
   meshed by blocks.js in the game's /export coordinates (y up), drawn in their materials' textures. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const storageKey = 'skinner.diaboticalmaps';
  const settings = {fov: 100, invertX: false, invertY: false, props: true, markers: true, terrain: true};
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
  function texture(file) {
    if (!textures.has(file)) {
      const made = loader.load(data + 'textures/' + file, undefined, undefined, () => { missing++; if (ready) showReady(); });
      made.wrapS = made.wrapT = THREE.RepeatWrapping;
      made.anisotropy = renderer.capabilities.getMaxAnisotropy();
      textures.set(file, made);
    }
    return textures.get(file);
  }
  // With a colour mask (`mask`, the material's map 4), the texture is tinted by each prop's accent colours, as the
  // game's tilemask.ps does: toward accent × the texel's mean by the mask's red, green and blue for accents 1, 2, 3.
  function material(name, entry, mask) {
    const key = mask ? name + '#tinted' : name;
    if (materials.has(key)) return materials.get(key);
    const side = THREE.FrontSide;
    let made;
    if (!entry || !entry.texture) made = new THREE.MeshLambertMaterial({color: flatColour(name), side});
    else {
      // Foliage and the like are cut out by their texture's alpha and seen from both sides.
      made = new THREE.MeshLambertMaterial({map: texture(entry.texture), side: entry.cutout ? THREE.DoubleSide : side,
        alphaTest: entry.cutout ? .5 : 0, transparent: !!entry.blend, depthWrite: !entry.blend});
      if (mask) made.onBeforeCompile = shader => {
        shader.uniforms.maskMap = {value: texture(mask.texture)};
        shader.vertexShader = 'attribute vec4 accent1, accent2, accent3;\nvarying vec4 vAccent1, vAccent2, vAccent3;\n' +
          shader.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\nvAccent1 = accent1; vAccent2 = accent2; vAccent3 = accent3;');
        shader.fragmentShader = 'uniform sampler2D maskMap;\nvarying vec4 vAccent1, vAccent2, vAccent3;\n' + shader.fragmentShader.replace('#include <map_fragment>', `
          vec4 texel = texture2D(map, vUv);
          vec3 weight = texture2D(maskMap, vUv).rgb, tinted = texel.rgb;
          float grey = (tinted.r + tinted.g + tinted.b) / 3.;
          tinted = mix(tinted, vAccent1.rgb * grey, weight.r * vAccent1.a);
          tinted = mix(tinted, vAccent2.rgb * grey, weight.g * vAccent2.a);
          tinted = mix(tinted, vAccent3.rgb * grey, weight.b * vAccent3.a);
          diffuseColor *= vec4(tinted, texel.a);`);
      };
    }
    materials.set(key, made);
    return made;
  }
  const maskOf = (name, entry, entries) => entry && entry.texture && (entries[name + '#4'] || {}).texture ? entries[name + '#4'] : null;
  // A model group's geometry with each instance's three accents (alpha 0 where none is set: no tint): the prop's own
  // (tints, 0x1RRGGBB or 0) or else the material's.
  function withAccents(base, tints, defaults, count) {
    const geometry = new THREE.BufferGeometry(), accents = new Float32Array(count * 12);
    for (const [name, attribute] of Object.entries(base.attributes)) geometry.setAttribute(name, attribute);
    for (let i = 0; i < count; i++) {
      for (let j = 0; j < 3; j++) {
        const own = tints ? tints[i * 3 + j] : 0, value = own ? own & 0xffffff : (defaults || [])[j];
        if (value != null) accents.set([(value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255, 1], (j * count + i) * 4);
      }
    }
    for (let j = 0; j < 3; j++) geometry.setAttribute(`accent${j + 1}`, new THREE.InstancedBufferAttribute(accents.subarray(j * count * 4, (j + 1) * count * 4), 4));
    return geometry;
  }

  const layers = {props: new THREE.Group(), markers: new THREE.Group(), terrain: new THREE.Group()};
  scene.add(layers.props, layers.markers, layers.terrain);
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
    for (const {model, material: override, mirrored, matrices, tints} of props) {
      if (!buffers.has(model)) { absent += matrices.length / 12; continue; }
      let at = 0;
      for (const [own, corners] of models[model].groups) {
        const key = `${model}|${at}|${mirrored}`;
        if (!geometries.has(key)) {
          let floats = new Float32Array(buffers.get(model), at * 32, corners * 8);
          // A mirrored prop keeps its triangles facing out by turning each the other way round (a BackSide
          // material would also flip its normals, already mirrored by the instance matrix).
          if (mirrored) {
            floats = floats.slice();
            for (let t = 0; t < corners; t += 3) {
              const second = floats.slice((t + 1) * 8, (t + 2) * 8);
              floats.copyWithin((t + 1) * 8, (t + 2) * 8, (t + 3) * 8);
              floats.set(second, (t + 2) * 8);
            }
          }
          const interleaved = new THREE.InterleavedBuffer(floats, 8);
          const geometry = new THREE.BufferGeometry();
          geometry.setAttribute('position', new THREE.InterleavedBufferAttribute(interleaved, 3, 0));
          geometry.setAttribute('normal', new THREE.InterleavedBufferAttribute(interleaved, 3, 3));
          geometry.setAttribute('uv', new THREE.InterleavedBufferAttribute(interleaved, 2, 6));
          geometries.set(key, geometry);
        }
        at += corners;
        const name = override || own, entry = entries[name];
        if (entry && entry.hidden) continue;
        const count = matrices.length / 12, mask = maskOf(name, entry, entries);
        const mesh = new THREE.InstancedMesh(mask ? withAccents(geometries.get(key), tints, entry.accents, count) : geometries.get(key),
          material(name, entry, mask), count);
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

  // The heightmap terrain: a vertex per pixel of its PNG (height in red, dirt mask in green), drawn as the game's
  // tileter.ps: ground texture where flat, mixed with dirt by the mask, the cliff texture (4x larger) on slopes.
  const GROUND_REPEAT = 40 / 128;  // Ground tiles per 40-unit cell: one every 128 units, measured in the game (cliff: 512).
  async function addTerrain(terrain, entries) {
    const image = await createImageBitmap(await get(data + 'maps/' + terrain.file).then(r => r.blob()), {colorSpaceConversion: 'none', premultiplyAlpha: 'none'});
    const {width, height} = image, context = Object.assign(document.createElement('canvas'), {width, height}).getContext('2d');
    context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, width, height).data, [ox, oy, oz] = terrain.offset;
    const positions = new Float32Array(width * height * 3), uvs = new Float32Array(width * height * 2), dirt = new Float32Array(width * height);
    for (let row = 0, i = 0; row < height; row++) {
      for (let column = 0; column < width; column++, i++) {
        positions.set([(column - width / 2) * terrain.cell + ox, oy + 8 * terrain.scale * pixels[i * 4], -((row - height / 2) * terrain.cell + oz)], i * 3);
        uvs.set([column * GROUND_REPEAT, row * GROUND_REPEAT], i * 2);
        dirt[i] = pixels[i * 4 + 1] / 255;
      }
    }
    const index = new Uint32Array((width - 1) * (height - 1) * 6);
    for (let row = 0, at = 0; row < height - 1; row++) {
      for (let column = 0; column < width - 1; column++) {
        const a = row * width + column, b = a + width;  // b is the next row: toward -z on the page, so (a, a + 1, b) faces up.
        index.set([a, a + 1, b, a + 1, b + 1, b], at);
        at += 6;
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
    geometry.setAttribute('dirt', new THREE.BufferAttribute(dirt, 1));
    geometry.setIndex(new THREE.BufferAttribute(index, 1));
    geometry.computeVertexNormals();
    const name = terrain.material, ground = entries[name], made = new THREE.MeshLambertMaterial(
      ground && ground.texture ? {map: texture(ground.texture)} : {color: flatColour(name)});
    if (ground && ground.texture) {
      const slot = number => texture((entries[`${name}#${number}`] || ground).texture);
      made.onBeforeCompile = shader => {
        Object.assign(shader.uniforms, {cliffMap: {value: slot(3)}, dirtMap: {value: slot(5)}});
        shader.vertexShader = 'attribute float dirt;\nvarying float vDirt, vUp;\n' +
          shader.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\nvDirt = dirt; vUp = abs(normal.y);');
        shader.fragmentShader = 'uniform sampler2D cliffMap, dirtMap;\nvarying float vDirt, vUp;\n' + shader.fragmentShader.replace('#include <map_fragment>', `
          vec3 level = mix(texture2D(map, vUv).rgb, texture2D(dirtMap, vUv).rgb, vDirt), cliff = texture2D(cliffMap, vUv * .25).rgb;
          diffuseColor.rgb *= mix(cliff, level, clamp(1. - 20. * (.85 - vUp), 0., 1.));`);
      };
    }
    layers.terrain.add(new THREE.Mesh(geometry, made));
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
    for (const id of ['invertX', 'invertY', 'props', 'markers', 'terrain']) $(id).checked = settings[id];
    for (const id of ['props', 'markers', 'terrain']) layers[id].visible = settings[id];
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
  for (const id of ['invertX', 'invertY', 'props', 'markers', 'terrain']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
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
      const name = names[index] || 'default', entry = entries[names[index]], mask = maskOf(name, entry, entries);
      if (!mask) { scene.add(new THREE.Mesh(geometry, material(name, entry))); continue; }
      // Tinted by its material's own accents: one instance, to share the props' path.
      const mesh = new THREE.InstancedMesh(withAccents(geometry, null, entry.accents, 1), material(name, entry, mask), 1);
      mesh.setMatrixAt(0, new THREE.Matrix4());
      scene.add(mesh);
    }
    if (!bounds.isEmpty()) start = {min: bounds.min, max: bounds.max};
    applySettings();
    showStart();
    if (map.terrain) {
      showStatus('Loading terrain…');
      await addTerrain(map.terrain, entries);
    }
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

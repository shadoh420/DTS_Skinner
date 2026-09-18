/* Vanilla Three.js workshop. UI workflow inspired by exogen's T2 Model Skinner;
   implemented here without its framework or editor dependencies. */
'use strict';
window.addEventListener('DOMContentLoaded', () => {
  const $ = id => document.getElementById(id);
  const compare = (a, b) => a.localeCompare(b, 'en', {numeric: true, sensitivity: 'base'});
  const scene = new THREE.Scene();
  scene.add(new THREE.AmbientLight(0xffffff, .6));
  const light = new THREE.DirectionalLight(0xffffff, .8);
  light.position.set(2, 4, 3); scene.add(light);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
  const renderer = new THREE.WebGLRenderer({canvas: $('c'), antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearColor($('backgroundColor').value);
  const orbit = new THREE.OrbitControls(camera, renderer.domElement);
  orbit.enableDamping = true;
  orbit.dampingFactor = 0.12;
  let game = 't1', catalog = [], filtered = [], textures = [], selectedName = '';
  let overrides = {}, data = null, group = null, radius = 1, materials = [], loadedParams = null;
  let loadSerial = 0, catalogSerial = 0, request = null, loading = false;
  let textureFailures = new Set(), versions = null, pollBusy = false;
  let textureSerial = 0;
  let textureMetadata = new Map(), drafts = {}, sizeReference = null, hueReference = null;
  const identityTransform = () => ({rotation: 0, flip_x: false, flip_y: false});
  const slotTransform = (model, slot) => (model.material_texture_transforms || [])[slot] || identityTransform();
  const copy = value => JSON.parse(JSON.stringify(value));
  const history = [], future = [];
  let historyBusy = false, lastState = null, orbitStart = null, walkingStart = null;
  const historyFields = ['gameSelect', 'modelSearch', 'categorySelect', 'modelSelect', 'materialSelect', 'textureGame', 'skinSearch', 'skinSelect',
    'widthMin', 'widthMax', 'heightMin', 'heightMax', 'sizeTolerance', 'hueTolerance', 'thumbnailSize', 'textureTags', 'textureName',
    'viewSelect', 'wireframe', 'lighting', 'turntable', 'backgroundColor', 'navigationStyle', 'walkSpeed', 'moveStep'];
  function snapshot() {
    return copy({game, selectedName, overrides, drafts, sizeReference, hueReference,
      ui: Object.fromEntries(historyFields.map(id => [id, $(id).type === 'checkbox' ? $(id).checked : $(id).value])),
      position: group?.position.toArray() || null, orientation: group?.quaternion.toArray() || null,
      camera: camera.position.toArray(), target: orbit.target.toArray()});
  }
  function updateHistoryButtons() {
    $('undoAction').disabled = historyBusy || loading || !history.length;
    $('redoAction').disabled = historyBusy || loading || !future.length;
    $('undoAction').title = history.length ? `Ctrl+Z · ${history.at(-1).label}` : 'Ctrl+Z';
    $('redoAction').title = future.length ? `Ctrl+Shift+Z / Ctrl+Y · ${future.at(-1).label}` : 'Ctrl+Shift+Z / Ctrl+Y';
    for (const [gallery, header] of [['galleryUndo', 'undoAction'], ['galleryRedo', 'redoAction']]) {
      $(gallery).disabled = $(header).disabled; $(gallery).title = $(header).title;
    }
  }
  function recordAction(label, before, after, tagChange) {
    if (JSON.stringify(before) === JSON.stringify(after) && !tagChange) { lastState = after; return; }
    history.push({label, before, after, tagChange});
    if (history.length > 100) history.shift();
    future.length = 0; lastState = after;
    $('historyStatus').textContent = `${label} · ${history.length}/100`;
    updateHistoryButtons();
  }
  function setHistoryBusy(value) {
    historyBusy = value;
    document.querySelector('.workspace').inert = value;
    $('galleryDialogBody').inert = value;
    updateHistoryButtons();
  }
  async function perform(label, operation) {
    if (historyBusy || loading) return;
    const before = copy(lastState || snapshot());
    historyBusy = true; updateHistoryButtons();
    try {
      // Synchronous input/change handlers must not make the focused form inert:
      // doing so can redirect the next keystroke to the field being blurred.
      let tagChange = operation();
      if (tagChange && typeof tagChange.then === 'function') {
        setHistoryBusy(true); tagChange = await tagChange;
      }
      recordAction(label, before, snapshot(), tagChange?.filename ? tagChange : null);
    } catch (error) {
      $('historyStatus').textContent = error.message;
      $('tagStatus').textContent = error.message;
    } finally { setHistoryBusy(false); }
  }
  function action(id, event, label, operation) { $(id).addEventListener(event, () => perform(label, operation)); }
  async function postTags(gameId, filename, tags) {
    const response = await fetch(`/texture_tags?${query({}, gameId)}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({filename, tags})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not save tags');
    return result.tags;
  }
  async function restoreState(state) {
    stopWalking();
    const oldLibrary = $('textureGame').value, oldGame = game;
    const reload = game !== state.game || selectedName !== state.selectedName || JSON.stringify(overrides) !== JSON.stringify(state.overrides) ||
      $('lighting').checked !== state.ui.lighting || $('textureName').value !== state.ui.textureName;
    if (game !== state.game) { $('gameSelect').value = state.game; await loadCatalog(); }
    const restoreFields = () => {
      for (const [id, value] of Object.entries(state.ui)) {
        if ($(id).type === 'checkbox') $(id).checked = value; else $(id).value = value;
      }
    };
    restoreFields();
    game = state.game; selectedName = state.selectedName; overrides = copy(state.overrides); drafts = copy(state.drafts);
    sizeReference = copy(state.sizeReference); hueReference = copy(state.hueReference);
    if (oldLibrary !== state.ui.textureGame || oldGame !== game) await loadTextureLibrary();
    filterCatalog(); $('modelSelect').value = selectedName;
    if (reload) await loadModel(false);
    restoreFields();
    if (group && state.position && state.orientation) {
      group.position.fromArray(state.position); group.quaternion.fromArray(state.orientation);
      try { localStorage.setItem(positionKey(), JSON.stringify(state.position)); localStorage.setItem(orientationKey(), JSON.stringify(state.orientation)); } catch (_) { /* Live undo still works. */ }
    }
    orbit.enableDamping = false; orbit.update();
    camera.position.fromArray(state.camera); orbit.target.fromArray(state.target); orbit.update(); orbit.enableDamping = true;
    renderer.setClearColor($('backgroundColor').value);
    materials.forEach(material => { material.wireframe = $('wireframe').checked; });
    updatePositionControls(); updateNavigationHint();
    updateMaterialInspector(); filterSkins(state.ui.skinSelect);
    $('textureWorkbench').style.setProperty('--thumb-size', `${state.ui.thumbnailSize}px`);
  }
  async function stepHistory(redo) {
    if (historyBusy || loading) return;
    const from = redo ? future : history, to = redo ? history : future, entry = from.at(-1);
    if (!entry) return;
    setHistoryBusy(true);
    try {
      if (entry.tagChange) {
        const tag = entry.tagChange;
        await postTags(tag.game, tag.filename, redo ? tag.after : tag.before);
        if (tag.game === $('textureGame').value && textureMetadata.has(tag.filename)) textureMetadata.get(tag.filename).tags = copy(redo ? tag.after : tag.before);
      }
      await restoreState(redo ? entry.after : entry.before);
      from.pop(); to.push(entry); lastState = snapshot();
      $('historyStatus').textContent = `${redo ? 'Redid' : 'Undid'} ${entry.label}`;
    } catch (error) { $('historyStatus').textContent = `History failed: ${error.message}`; }
    finally { setHistoryBusy(false); }
  }
  $('undoAction').addEventListener('click', () => stepHistory(false));
  $('redoAction').addEventListener('click', () => stepHistory(true));
  $('galleryUndo').addEventListener('click', () => stepHistory(false));
  $('galleryRedo').addEventListener('click', () => stepHistory(true));
  document.addEventListener('keydown', event => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.target.matches('input,textarea,[contenteditable="true"]')) return;
    if (event.key.toLowerCase() === 'z' || event.key.toLowerCase() === 'y') {
      event.preventDefault(); stepHistory(event.shiftKey || event.key.toLowerCase() === 'y');
    }
  });
  orbit.addEventListener('start', () => { if (!historyBusy && !loading) orbitStart = snapshot(); });
  orbit.addEventListener('end', () => {
    if (!orbitStart || historyBusy || loading) return;
    orbit.enableDamping = false; orbit.update(); orbit.enableDamping = true;
    recordAction('Move camera', orbitStart, snapshot()); orbitStart = null;
  });
  const textureId = (name, gameId = game) => `${gameId}/${name}`;
  const sourceGame = (model, slot) => (model.material_texture_games || [])[slot] || game;
  const positionKey = () => `skinner.position.${game}.${selectedName}`;
  function updatePositionControls() {
    for (const axis of ['X', 'Y', 'Z']) {
      $(`position${axis}`).value = group ? Number(group.position[axis.toLowerCase()].toPrecision(10)) : 0;
      $(`position${axis}`).disabled = !group || loading;
    }
  }
  function savePosition() {
    if (!group || loading) return;
    try { localStorage.setItem(positionKey(), JSON.stringify(group.position.toArray())); } catch (_) { /* Controls remain usable without storage. */ }
    updatePositionControls();
  }
  function restorePosition() {
    try {
      const value = JSON.parse(localStorage.getItem(positionKey()));
      if (Array.isArray(value) && value.length === 3 && value.every(Number.isFinite)) group.position.fromArray(value);
    } catch (_) { /* Ignore invalid or unavailable storage. */ }
  }
  const keys = new Set(), look = new THREE.Euler(0, 0, 0, 'YXZ');
  const forward = new THREE.Vector3(), right = new THREE.Vector3(), movement = new THREE.Vector3();
  let walking = false, navigationRequested = false, orbitDistance = 1;
  const orbitHint = 'Drag: orbit · Wheel: zoom · Right-drag: pan · Shift + `: walk/fly';
  function updateNavigationHint() {
    $('navigationHint').textContent = walking ? `${$('navigationStyle').value === 'walk' ? 'Walk' : 'Fly'} · WASD: move · E/Q: up/down · Shift/Alt: speed · Wheel: speed (${Number($('walkSpeed').value).toFixed(2)}) · Esc: exit` : orbitHint;
  }
  function stopWalking() {
    navigationRequested = false;
    keys.clear();
    if (walking) {
      camera.getWorldDirection(forward);
      orbit.target.copy(camera.position).addScaledVector(forward, orbitDistance);
      orbit.enabled = true;
      walking = false;
      $('viewport').classList.remove('walking');
      $('walkMode').setAttribute('aria-pressed', 'false');
      orbit.update();
      if (walkingStart && !historyBusy) recordAction('Walk / fly camera', walkingStart, snapshot());
      walkingStart = null;
    }
    if (document.pointerLockElement === renderer.domElement) document.exitPointerLock();
    updateNavigationHint();
  }
  async function startWalking() {
    if (!group || loading || navigationRequested) return;
    navigationRequested = true;
    try {
      await renderer.domElement.requestPointerLock();
    } catch (_) { stopWalking(); $('navigationHint').textContent = 'Mouse capture unavailable. Click Walk / Fly to try again.'; }
  }
  function moveCamera(delta) {
    camera.getWorldDirection(forward);
    if ($('navigationStyle').value === 'walk') forward.set(-Math.sin(look.y), 0, -Math.cos(look.y));
    right.set(Math.cos(look.y), 0, -Math.sin(look.y));
    const down = (...codes) => codes.some(code => keys.has(code));
    movement.copy(forward).multiplyScalar(Number(down('KeyW', 'ArrowUp')) - Number(down('KeyS', 'ArrowDown')));
    movement.addScaledVector(right, Number(down('KeyD', 'ArrowRight')) - Number(down('KeyA', 'ArrowLeft')));
    movement.y += Number(down('KeyE')) - Number(down('KeyQ'));
    const speed = Math.max(.01, Math.min(100000, Number($('walkSpeed').value) || 5));
    const multiplier = down('ShiftLeft', 'ShiftRight') ? 4 : down('AltLeft', 'AltRight') ? .2 : 1;
    if (movement.lengthSq()) camera.position.addScaledVector(movement.normalize(), speed * multiplier * delta);
  }
  $('walkMode').addEventListener('click', () => walking ? stopWalking() : startWalking());
  $('fullscreen').addEventListener('click', async () => {
    try {
      if (document.fullscreenElement === $('viewport')) await document.exitFullscreen();
      else await $('viewport').requestFullscreen();
    } catch (_) { $('navigationHint').textContent = 'Fullscreen unavailable in this browser window.'; }
  });
  document.addEventListener('fullscreenchange', () => {
    const active = document.fullscreenElement === $('viewport');
    $('fullscreen').textContent = active ? 'Exit fullscreen' : 'Fullscreen';
    $('fullscreen').setAttribute('aria-pressed', String(active));
    stopWalking();
  });
  document.addEventListener('pointerlockchange', () => {
    if (document.pointerLockElement !== renderer.domElement) { stopWalking(); return; }
    if (!navigationRequested || !group || loading) { stopWalking(); return; }
    // The event supports both Promise and legacy void requestPointerLock implementations.
    walkingStart = snapshot();
    navigationRequested = false;
    orbit.enableDamping = false; orbit.update(); orbit.enableDamping = true;
    orbitDistance = Math.max(camera.position.distanceTo(orbit.target), .01);
    look.setFromQuaternion(camera.quaternion, 'YXZ');
    orbit.enabled = false; walking = true; keys.clear();
    $('turntable').checked = false;
    $('viewport').classList.add('walking');
    $('walkMode').setAttribute('aria-pressed', 'true');
    updateNavigationHint();
  });
  document.addEventListener('pointerlockerror', () => { stopWalking(); $('navigationHint').textContent = 'Mouse capture unavailable. Click Walk / Fly to try again.'; });
  window.addEventListener('blur', stopWalking);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopWalking(); });
  document.addEventListener('keydown', event => {
    if (!walking && (event.target.matches('input,select,textarea') || event.target.isContentEditable)) return;
    if (event.shiftKey && event.code === 'Backquote') {
      event.preventDefault(); if (!event.repeat) walking ? stopWalking() : startWalking(); return;
    }
    if (!walking) return;
    if (event.code === 'Escape' || event.code === 'Enter') { event.preventDefault(); stopWalking(); return; }
    keys.add(event.code); event.preventDefault();
  });
  document.addEventListener('keyup', event => keys.delete(event.code));
  document.addEventListener('mousemove', event => {
    if (!walking) return;
    look.y -= event.movementX * .002;
    look.x = Math.max(-Math.PI / 2 + .001, Math.min(Math.PI / 2 - .001, look.x - event.movementY * .002));
    camera.quaternion.setFromEuler(look);
  });
  renderer.domElement.addEventListener('wheel', event => {
    if (!walking) return;
    event.preventDefault();
    $('walkSpeed').value = Number(Math.max(.01, Math.min(100000, (Number($('walkSpeed').value) || 5) * (event.deltaY < 0 ? 1.2 : 1 / 1.2))).toPrecision(4));
    updateNavigationHint();
  }, {passive: false});
  const orientationKey = () => `skinner.orientation.${game}.${selectedName}`;
  function saveOrientation() {
    if (!group || loading) return;
    $('turntable').checked = false;
    try { localStorage.setItem(orientationKey(), JSON.stringify(group.quaternion.toArray())); } catch (_) { /* Rotation still works when storage is unavailable. */ }
  }
  function restoreOrientation() {
    try {
      const value = JSON.parse(localStorage.getItem(orientationKey()));
      if (Array.isArray(value) && value.length === 4 && value.every(Number.isFinite) && Math.hypot(...value) > 0) group.quaternion.fromArray(value).normalize();
    } catch (_) { /* Ignore unavailable storage or an invalid saved value. */ }
  }

  function query(extra = {}, gameId = game) {
    return new URLSearchParams({game: gameId, ...extra}).toString();
  }
  function modelQuery() {
    return query({texture: $('textureName').value.trim(), materials: JSON.stringify(overrides)});
  }
  function textureUrl(name, gameId = game, transform = identityTransform()) {
    const extra = transform.rotation || transform.flip_x || transform.flip_y ?
      {rotation: transform.rotation, flip_x: Number(transform.flip_x), flip_y: Number(transform.flip_y)} : {};
    return `/texture/${encodeURIComponent(name)}?${query(extra, gameId)}`;
  }
  function option(label, value) { return new Option(label, value); }
  function showWarnings(items) {
    $('warnings').replaceChildren(...[...new Set(items.filter(Boolean).map(x => typeof x === 'string' ? x : JSON.stringify(x)))].map(text => {
      const li = document.createElement('li'); li.textContent = text; return li;
    }));
  }
  function available(entry) {
    return !/unsupported|failed|error|no.visible|no.geometry/i.test(entry.status || '');
  }
  function disposeModel() {
    if (group) {
      scene.remove(group);
      group.traverse(obj => { if (obj.geometry) obj.geometry.dispose(); });
      group = null;
    }
    disposeMaterials(materials); materials = [];
  }
  function disposeMaterials(items) {
    const maps = new Set(items.map(material => material.map).filter(Boolean));
    maps.forEach(map => { map.dispose(); if (map.image && map.image.close) map.image.close(); });
    items.forEach(material => material.dispose());
  }
  function emptyInspector() {
    data = null;
    loadedParams = null;
    $('materialSelect').replaceChildren();
    $('materialSelect').disabled = true;
    $('loadedTexturesList').replaceChildren();
    $('geometryInfo').textContent = '';
    $('animationInfo').textContent = '';
    updateMaterialInspector();
  }
  async function json(url, signal) {
    const response = await fetch(url, {cache: 'no-store', signal});
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { const body = await response.json(); message = body.error || body.message || message; } catch (_) { /* Status remains useful for non-JSON failures. */ }
      throw new Error(message);
    }
    return response.json();
  }
  async function loadCatalog() {
    stopWalking();
    const serial = ++catalogSerial;
    game = $('gameSelect').value;
    const gameId = game;
    ++loadSerial;
    if (request) request.abort();
    loading = false;
    catalog = []; filtered = []; textures = []; versions = null; selectedName = '';
    overrides = {}; disposeModel(); emptyInspector();
    $('modelSelect').replaceChildren();
    $('exportObjBtn').disabled = true;
    $('exportGlbBtn').disabled = true;
    $('modelSearch').value = ''; $('skinSearch').value = '';
    $('status').textContent = 'Loading catalog…';
    $('textureGame').value = game;
    $('q3Import').hidden = game !== 'q3';
    $('coverageReport').hidden = game === 't1';
    $('coverageReport').href = game === 'q3' ? '/q3_inventory' : '/static/t2/inventory.json';
    $('coverageReport').textContent = `View ${game.toUpperCase()} inventory & coverage report`;
    showWarnings([]);
    try {
      const results = await Promise.allSettled([
        json(`/list_models?${query({}, gameId)}`),
        loadTextureLibrary(),
        allTextureVersions()
      ]);
      if (serial !== catalogSerial) return;
      if (results[0].status === 'rejected') throw results[0].reason;
      // Keep the server's catalog order, including its stable punctuation/case ties.
      catalog = results[0].value;
      versions = results[2].status === 'fulfilled' ? results[2].value : null;
      $('categorySelect').replaceChildren(option('All families', ''), ...[...new Set(catalog.map(x => x.category || 'Other'))].sort(compare).map(x => option(x, x)));
      const unavailable = catalog.filter(entry => !available(entry)).length;
      $('catalogSummary').textContent = `${game.toUpperCase()} · ${catalog.length.toLocaleString()} entries · ${catalog.length - unavailable} previews${unavailable ? ` · ${unavailable} unavailable` : ''}`;
      filterCatalog();
      if (filtered.length) {
        const preferred = filtered.find(x => x.model_name === 'disc') || filtered[0];
        $('modelSelect').value = preferred.model_name;
        await selectModel();
      } else {
        $('status').textContent = 'This game has no imported catalog entries.';
        if (game === 'q3') $('q3Import').open = true;
      }
    } catch (error) {
      if (serial === catalogSerial) $('status').textContent = `Catalog unavailable: ${error.message}`;
    }
  }
  function filterCatalog() {
    const search = $('modelSearch').value.toLocaleLowerCase();
    const family = $('categorySelect').value;
    filtered = catalog.filter(entry => (!family || (entry.category || 'Other') === family) && `${entry.model_name} ${entry.display_name || ''} ${entry.category || ''}`.toLocaleLowerCase().includes(search));
    $('modelSelect').replaceChildren(...filtered.map(entry => {
      const opt = option(`${entry.display_name || entry.model_name}${available(entry) ? '' : ' [no preview]'}`, entry.model_name);
      opt.dataset.unavailable = String(!available(entry)); return opt;
    }));
    if (filtered.some(x => x.model_name === selectedName)) $('modelSelect').value = selectedName;
    else $('modelSelect').selectedIndex = -1;
    $('modelCount').textContent = `${filtered.length.toLocaleString()} of ${catalog.length.toLocaleString()} models`;
    $('previousModel').disabled = $('nextModel').disabled = !filtered.length;
  }
  function selectModel() {
    const name = $('modelSelect').value;
    if (!name) return;
    selectedName = name; overrides = {};
    const entry = catalog.find(x => x.model_name === name);
    $('textureName').value = entry.texture_name || '';
    $('exportStatus').textContent = '';
    $('viewSelect').value = 'perspective';
    return loadModel(false);
  }
  async function makeTexture(name, gameId, signal, flags, settings, textureGame, transform) {
    const response = await fetch(textureUrl(name, textureGame, transform), {cache: 'no-store', signal});
    if (!response.ok) throw new Error(`Missing texture: ${name}`);
    // T2 opaque skin alpha stores reflectivity; never bake it into the RGB color.
    const bitmap = await createImageBitmap(await response.blob(), {premultiplyAlpha: 'none'});
    const texture = new THREE.Texture(bitmap);
    texture.needsUpdate = true;
    texture.flipY = false;
    texture.magFilter = THREE.NearestFilter;
    texture.minFilter = THREE.NearestFilter;
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    if (gameId === 't2') {
      texture.wrapS = flags & 1 ? THREE.RepeatWrapping : THREE.ClampToEdgeWrapping;
      texture.wrapT = flags & 2 ? THREE.RepeatWrapping : THREE.ClampToEdgeWrapping;
      texture.magFilter = THREE.LinearFilter;
      texture.minFilter = flags & 128 ? THREE.LinearFilter : THREE.LinearMipmapLinearFilter;
      texture.generateMipmaps = !(flags & 128);
    }
    if (gameId === 'q3') {
      texture.wrapS = texture.wrapT = settings.clamp ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
      texture.magFilter = THREE.LinearFilter;
      texture.minFilter = THREE.LinearMipmapLinearFilter;
    }
    return texture;
  }
  function applyQ3Material(material, settings) {
    material.side = settings.cull === 'none' ? THREE.DoubleSide : settings.cull === 'front' ? THREE.BackSide : THREE.FrontSide;
    material.alphaTest = settings.alphaFunc === 'GE128' ? .5 : settings.alphaFunc === 'GT0' ? .5 / 255 : 0;
    material.transparent = Boolean(settings.blend && settings.blend.length);
    material.depthWrite = settings.depthWrite !== false;
    if (material.transparent) {
      const factors = {gl_zero: THREE.ZeroFactor, gl_one: THREE.OneFactor,
        gl_src_color: THREE.SrcColorFactor, gl_one_minus_src_color: THREE.OneMinusSrcColorFactor,
        gl_dst_color: THREE.DstColorFactor, gl_one_minus_dst_color: THREE.OneMinusDstColorFactor,
        gl_src_alpha: THREE.SrcAlphaFactor, gl_one_minus_src_alpha: THREE.OneMinusSrcAlphaFactor,
        gl_dst_alpha: THREE.DstAlphaFactor, gl_one_minus_dst_alpha: THREE.OneMinusDstAlphaFactor,
        gl_src_alpha_saturate: THREE.SrcAlphaSaturateFactor};
      material.blending = THREE.CustomBlending;
      material.blendSrc = factors[settings.blend[0]] ?? THREE.SrcAlphaFactor;
      material.blendDst = factors[settings.blend[1]] ?? THREE.OneMinusSrcAlphaFactor;
    }
    const environment = settings.tcGen === 'environment' && material.map;
    material.userData.q3ViewOrigin = new THREE.Vector3();
    material.onBeforeCompile = shader => {
      if (environment) {
        // Native tcGen environment uses the reflected eye vector in model space.
        shader.uniforms.q3ViewOrigin = {value: material.userData.q3ViewOrigin};
        shader.vertexShader = 'uniform vec3 q3ViewOrigin;\n' + shader.vertexShader.replace('#include <uv_vertex>', `
          #include <uv_vertex>
          vec3 eye = normalize(q3ViewOrigin - position);
          vec3 reflected = 2.0 * normalize(normal) * dot(normalize(normal), eye) - eye;
          vUv = vec2(0.5 + reflected.x * 0.5, 0.5 - reflected.y * 0.5);
        `);
      }
      if (settings.alphaFunc === 'LT128') shader.fragmentShader = shader.fragmentShader.replace('#include <alphatest_fragment>', 'if (diffuseColor.a >= 0.5) discard;');
    };
    material.customProgramCacheKey = () => `q3:${Boolean(environment)}:${settings.alphaFunc || ''}`;
  }
  async function loadModel(preserveView = true) {
    if (!selectedName) return;
    if (!preserveView) stopWalking();
    const serial = ++loadSerial, gameId = game, name = selectedName;
    const params = modelQuery();
    const slot = preserveView ? $('materialSelect').value : '';
    if (request) request.abort();
    request = new AbortController();
    const signal = request.signal;
    loading = true;
    $('exportObjBtn').disabled = true;
    $('exportGlbBtn').disabled = true;
    $('status').textContent = `Loading ${name}…`;
    $('modelTitle').textContent = `${gameId.toUpperCase()} / ${(catalog.find(x=>x.model_name===name)||{}).display_name || name}`;
    const oldRotation = group ? group.quaternion.clone() : null;
    const oldPosition = group ? group.position.clone() : null;
    disposeModel(); emptyInspector();
    const entry = catalog.find(x => x.model_name === name) || {};
    showWarnings(entry.warnings || []);
    if (!available(entry)) {
      $('status').textContent = `Preview unavailable: ${entry.status}.`;
      const reason = entry.error || entry.reason || (entry.warnings || [])[0] || entry.status;
      $('geometryInfo').textContent = `Preview unavailable: ${typeof reason === 'string' ? reason : JSON.stringify(reason)}`;
      showWarnings([...(entry.warnings || []), entry.error, entry.reason]);
      loading = false; return;
    }
    let newMaterials = [];
    try {
      const model = await json(`/model_json/${encodeURIComponent(name)}?${params}`, signal);
      if (serial !== loadSerial) return;
      if (!model.vertices || !model.vertices.length) throw new Error('No visible geometry in this model.');
      const names = model.material_textures && model.material_textures.length ? model.material_textures : ['[Default material]'];
      const failures = new Set(), cache = new Map();
      const flagsFor = index => Number((model.material_flags || [])[index] || 0);
      const settingsFor = index => (model.material_settings || [])[index] || {};
      const textureKey = index => `${textureId(names[index], sourceGame(model, index))}|${JSON.stringify(slotTransform(model, index))}|${flagsFor(index)}|${Boolean(settingsFor(index).clamp)}`;
      for (let index = 0; index < names.length; index++) {
        const filename = names[index], key = textureKey(index);
        if (!filename || filename.startsWith('[') || cache.has(key)) continue;
        cache.set(key, makeTexture(filename, gameId, signal, flagsFor(index), settingsFor(index), sourceGame(model, index), slotTransform(model, index)).catch(() => { failures.add(textureId(filename, sourceGame(model, index))); return null; }));
      }
      await Promise.all(cache.values());
      for (let index = 0; index < names.length; index++) {
        const filename = names[index];
        const key = textureKey(index), flags = gameId === 't2' ? flagsFor(index) : 0;
        const map = cache.has(key) ? await cache.get(key) : null;
        const Material = $('lighting').checked && !(flags & 32) ? THREE.MeshLambertMaterial : THREE.MeshBasicMaterial;
        const transparent = Boolean(flags & (4 | 8 | 16));
        newMaterials.push(new Material({
          name: filename, map, color: map ? 0xffffff : failures.has(textureId(filename, sourceGame(model, index))) ? 0xcc00cc : 0x999999,
          side: THREE.DoubleSide, wireframe: $('wireframe').checked,
          transparent, depthWrite: !transparent,
          blending: flags & 8 ? THREE.AdditiveBlending : flags & 16 ? THREE.SubtractiveBlending : THREE.NormalBlending
        }));
        if (gameId === 'q3' && model.material_settings) applyQ3Material(newMaterials[newMaterials.length - 1], settingsFor(index));
      }
      if (serial !== loadSerial) { disposeMaterials(newMaterials); return; }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(model.vertices, 3));
      geometry.setAttribute('uv', new THREE.Float32BufferAttribute(model.uvs, 2));
      if (model.indices && model.indices.length) geometry.setIndex(model.indices);
      if (model.normals && model.normals.length === model.vertices.length) geometry.setAttribute('normal', new THREE.Float32BufferAttribute(model.normals, 3));
      else geometry.computeVertexNormals();
      if (model.groups && model.groups.length) model.groups.forEach(part => geometry.addGroup(part.start, part.count, part.materialIndex));
      else geometry.addGroup(0, model.indices ? model.indices.length : model.vertices.length / 3, 0);
      geometry.computeBoundingSphere();
      radius = Math.max(geometry.boundingSphere.radius, 0.001);
      const center = geometry.boundingSphere.center;
      geometry.translate(-center.x, -center.y, -center.z);
      group = new THREE.Group();
      const mesh = new THREE.Mesh(geometry, newMaterials);
      mesh.onBeforeRender = (renderer, scene, camera, geometry, material) => {
        if (material.userData.q3ViewOrigin) mesh.worldToLocal(camera.getWorldPosition(material.userData.q3ViewOrigin));
      };
      group.add(mesh);
      if (preserveView && oldRotation) group.quaternion.copy(oldRotation);
      else restoreOrientation();
      if (preserveView && oldPosition) group.position.copy(oldPosition);
      else restorePosition();
      scene.add(group); materials = newMaterials; newMaterials = [];
      data = model; textureFailures = failures; loadedParams = params;
      if (!preserveView || !oldRotation) frameModel();
      $('geometryInfo').textContent = `${(model.vertices.length / 3).toLocaleString()} vertices · ${(model.indices.length / 3).toLocaleString()} triangles · ${names.length} material slots${failures.size ? ` · ${failures.size} missing texture${failures.size === 1 ? '' : 's'}` : ''}`;
      $('status').textContent = failures.size ? `Preview loaded with ${failures.size} missing texture${failures.size === 1 ? '' : 's'}.` : 'Preview ready.';
      const metadata = model.metadata || {};
      const warnings = [...(entry.warnings || []), ...(model.warnings || []), ...(metadata.warnings || []), ...[...failures].map(x => `Missing texture: ${x}. Geometry remains visible in magenta.`)];
      if (gameId === 'q3' && !model.material_settings) warnings.push('Reimport your Q3 game folder to restore shader materials and corrected face orientation. Existing PNG edits are kept.');
      showWarnings(warnings);
      const sequences = metadata.sequences || metadata.embedded_sequences || [];
      const external = metadata.external_sequences || [];
      $('animationInfo').textContent = `Static preview; GLB exports available geometry animation clips. ${gameId === 't2' ? `${sequences.length} embedded sequences; ${external.length} external animation files recorded. ` : ''}${metadata.pose || ''}`;
      $('materialSelect').replaceChildren(...names.map((filename, index) => ({filename, index, label: (model.material_names || [])[index] || filename})).sort((a, b) => compare(a.label, b.label) || a.index - b.index).map(x => option(`${x.label} · slot ${x.index}`, x.index)));
      $('materialSelect').disabled = false;
      if (slot && [...$('materialSelect').options].some(x => x.value === slot)) $('materialSelect').value = slot;
      $('loadedTexturesList').replaceChildren(...[...new Set(names.map((filename, index) => textureId(filename, sourceGame(model, index))))].sort(compare).map(filename => {
        const li = document.createElement('li'); li.textContent = filename + (failures.has(filename) ? ' — MISSING' : '');
        if (failures.has(filename)) li.style.color = '#efb987'; return li;
      }));
      updateMaterialInspector();
    } catch (error) {
      disposeMaterials(newMaterials);
      if (serial !== loadSerial || error.name === 'AbortError') return;
      $('status').textContent = `Preview unavailable: ${error.message}`;
      $('geometryInfo').textContent = `Preview unavailable: ${error.message}`;
      showWarnings([...(entry.warnings || []), 'This catalog entry has not been removed.']);
    } finally {
      if (serial === loadSerial) {
        loading = false;
        updatePositionControls();
        $('exportObjBtn').disabled = !group;
        $('exportGlbBtn').disabled = !group;
        $('applySkin').disabled = !group || !$('skinSelect').value;
        updateHistoryButtons();
      }
    }
  }
  function updateMaterialInspector() {
    const slot = Number($('materialSelect').value);
    const filename = data && data.material_textures ? data.material_textures[slot] : null;
    const textureGame = data ? sourceGame(data, slot) : game;
    const valid = filename && !filename.startsWith('[') && !textureFailures.has(textureId(filename, textureGame));
    $('texturePreview').hidden = true;
    $('texturePreviewEmpty').hidden = false;
    $('texturePreviewEmpty').textContent = filename ? valid ? 'Loading texture…' : 'Texture unavailable' : 'No material selected';
    $('textureDetails').textContent = filename ? `${textureGame.toUpperCase()} / ${filename}` : '';
    $('downloadTexture').classList.toggle('disabled', !valid);
    if (valid) {
      const opaque = game === 't2' && !(Number((data.material_flags || [])[slot] || 0) & (4 | 8 | 16));
      $('texturePreview').src = `${textureUrl(filename, textureGame, slotTransform(data, slot))}${opaque ? '&opaque=1' : ''}&v=${Date.now()}`;
      if (opaque) $('textureDetails').textContent += ' · RGB preview; alpha retained in PNG';
      $('downloadTexture').href = textureUrl(filename, textureGame, slotTransform(data, slot)) + '&download=1';
      $('downloadTexture').download = filename;
    } else {
      $('texturePreview').removeAttribute('src');
      $('downloadTexture').removeAttribute('href');
    }
    $('resetSkin').disabled = !Object.hasOwn(overrides, slot);
    filterSkins(textureGame === $('textureGame').value ? filename : null);
  }
  function filterSkins(preferred) {
    const terms = $('skinSearch').value.toLocaleLowerCase().split(/[\s,]+/).filter(Boolean);
    const previous = preferred || $('skinSelect').value;
    const bounds = ['widthMin', 'widthMax', 'heightMin', 'heightMax'].map(id => $(id).value === '' ? null : $(id).valueAsNumber);
    const validBounds = bounds.every(value => value === null || Number.isInteger(value) && value > 0) &&
      !(bounds[0] !== null && bounds[1] !== null && bounds[0] > bounds[1]) &&
      !(bounds[2] !== null && bounds[3] !== null && bounds[2] > bounds[3]);
    const sizeTolerance = $('sizeTolerance').valueAsNumber, hueTolerance = $('hueTolerance').valueAsNumber;
    const list = textures.filter(name => {
      const meta = textureMetadata.get(name) || {};
      if (!terms.every(term => `${name} ${(meta.tags || []).join(' ')}`.toLocaleLowerCase().includes(term)) || !validBounds) return false;
      if (bounds.some(x => x !== null) && (!meta.width || !meta.height)) return false;
      if (bounds[0] !== null && meta.width < bounds[0] || bounds[1] !== null && meta.width > bounds[1] ||
          bounds[2] !== null && meta.height < bounds[2] || bounds[3] !== null && meta.height > bounds[3]) return false;
      if (sizeReference && (!Number.isFinite(sizeTolerance) || sizeTolerance < 0 || !meta.width ||
          Math.abs(meta.width - sizeReference.width) > sizeTolerance || Math.abs(meta.height - sizeReference.height) > sizeTolerance)) return false;
      if (hueReference) {
        if (!Number.isFinite(hueTolerance) || hueTolerance < 0 || hueTolerance > 180 || !meta.width) return false;
        if (hueReference.hue === null || meta.hue === null) return hueReference.hue === meta.hue;
        const difference = Math.abs(meta.hue - hueReference.hue);
        if (Math.min(difference, 360 - difference) > hueTolerance) return false;
      }
      return true;
    });
    $('skinSelect').replaceChildren(...list.map(name => option(name, name)));
    if (list.includes(previous)) $('skinSelect').value = previous;
    $('skinSelect').disabled = !list.length;
    $('applySkin').disabled = loading || !data || !list.length || $('materialSelect').disabled;
    const library = $('textureGame').value;
    $('textureCount').textContent = `${list.length.toLocaleString()} of ${textures.length.toLocaleString()} textures · ${library.toUpperCase()}${library === 't2' ? ' · RGB thumbnails' : ''}`;
    $('similarityStatus').textContent = !validBounds ? 'Enter positive whole-pixel ranges with minimum ≤ maximum.' :
      [sizeReference && `Size near ${sizeReference.filename} (${sizeReference.width} × ${sizeReference.height}) ± ${sizeTolerance} px`,
       hueReference && `Hue near ${hueReference.filename} (${hueReference.hue === null ? 'neutral' : Math.round(hueReference.hue) + '°'}) ± ${hueTolerance}°`].filter(Boolean).join(' · ');
    $('textureGallery').replaceChildren(...list.map(name => {
      const button = document.createElement('button');
      button.className = 'texture-thumb'; button.type = 'button';
      const meta = textureMetadata.get(name) || {};
      button.dataset.texture = name; button.title = `${name}${meta.width ? ` · ${meta.width} × ${meta.height}` : ''}${meta.tags?.length ? ` · ${meta.tags.join(', ')}` : ''}`;
      button.setAttribute('aria-pressed', String(name === $('skinSelect').value));
      const thumbnail = document.createElement('img');
      thumbnail.alt = ''; thumbnail.loading = 'lazy'; thumbnail.decoding = 'async';
      thumbnail.width = 96; thumbnail.height = 88;
      // T2 stores reflectivity in alpha; RGB keeps opaque skins recognizable.
      const version = versions && versions[textureId(name, library)];
      thumbnail.src = `${textureUrl(name, library)}${library === 't2' ? '&opaque=1' : ''}&v=${encodeURIComponent(JSON.stringify(version || []))}`;
      thumbnail.addEventListener('error', () => { button.classList.add('missing'); button.title = `${name} — preview unavailable`; });
      const label = document.createElement('span'); label.textContent = name;
      const dimensions = document.createElement('small'); dimensions.textContent = meta.width ? `${meta.width} × ${meta.height}${meta.tags?.length ? ' · ' + meta.tags.join(', ') : ''}` : meta.error || 'Metadata unavailable';
      button.append(thumbnail, label, dimensions);
      button.addEventListener('click', () => perform('Select texture', () => { $('skinSelect').value = name; selectThumbnail(); }));
      return button;
    }));
    updateCandidate();
  }
  function selectThumbnail() {
    for (const button of $('textureGallery').children) {
      button.setAttribute('aria-pressed', String(button.dataset.texture === $('skinSelect').value));
    }
    updateCandidate();
  }
  function candidateTransform() {
    return drafts[textureId($('skinSelect').value, $('textureGame').value)] || identityTransform();
  }
  function updateCandidate() {
    const name = $('skinSelect').value, library = $('textureGame').value, meta = textureMetadata.get(name);
    const valid = Boolean(name && meta?.width), transform = candidateTransform();
    for (const id of ['rotateTextureLeft', 'rotateTextureRight', 'flipTextureX', 'flipTextureY', 'resetTextureCopy', 'similarSize', 'similarHue', 'saveTextureTags', 'textureTags']) $(id).disabled = !valid;
    $('candidatePreview').hidden = !valid; $('candidateEmpty').hidden = valid;
    $('saveTextureCopy').classList.toggle('disabled', !valid || !(transform.rotation || transform.flip_x || transform.flip_y));
    $('textureTags').value = (meta?.tags || []).join(', ');
    $('tagStatus').textContent = '';
    if (!valid) {
      $('candidatePreview').removeAttribute('src'); $('saveTextureCopy').removeAttribute('href');
      $('candidateDetails').textContent = name ? 'Image metadata unavailable; try Reload textures.' : 'No texture matches the search.';
      return;
    }
    const url = textureUrl(name, library, transform), version = versions?.[textureId(name, library)];
    $('candidatePreview').src = `${url}${library === 't2' ? '&opaque=1' : ''}&v=${encodeURIComponent(JSON.stringify(version || []))}`;
    $('saveTextureCopy').href = url + '&download=1';
    const swapped = transform.rotation % 180 !== 0;
    $('candidateDetails').textContent = `${library.toUpperCase()} / ${name} · ${swapped ? meta.height : meta.width} × ${swapped ? meta.width : meta.height} · ${transform.rotation}°${transform.flip_x ? ' · horizontal flip' : ''}${transform.flip_y ? ' · vertical flip' : ''}`;
  }
  function transformCandidate(operation) {
    if (!$('skinSelect').value) return;
    const transform = {...candidateTransform()};
    if (typeof operation === 'number') {
      transform.rotation = (transform.rotation + operation + 360) % 360;
      [transform.flip_x, transform.flip_y] = [transform.flip_y, transform.flip_x];
    } else if (operation === 'reset') Object.assign(transform, identityTransform());
    else transform[operation] = !transform[operation];
    drafts[textureId($('skinSelect').value, $('textureGame').value)] = transform;
    updateCandidate();
  }
  function clearTextureFilters() {
    for (const id of ['skinSearch', 'widthMin', 'widthMax', 'heightMin', 'heightMax']) $(id).value = '';
    sizeReference = hueReference = null;
  }
  function frameModel() {
    if (!group) return;
    stopWalking();
    orbit.enableDamping = false; orbit.update(); orbit.enableDamping = true;
    const halfFov = Math.min(camera.fov * Math.PI / 360, Math.atan(Math.tan(camera.fov * Math.PI / 360) * camera.aspect));
    const distance = radius / Math.sin(halfFov) * 1.18;
    const directions = {perspective: [0, .25, 1], front: [0, 0, 1], back: [0, 0, -1], left: [-1, 0, 0], right: [1, 0, 0], top: [0, 1, .0001], bottom: [0, -1, .0001]};
    // Imported T2 forward (+Y) becomes -Z in the viewer's Y-up coordinates.
    if (game === 't2') for (const direction of Object.values(directions)) direction[2] *= -1;
    camera.near = Math.max(Math.min(radius / 10000, .01), .0001);
    camera.far = Math.max(radius * 100, 10);
    camera.updateProjectionMatrix();
    camera.position.copy(new THREE.Vector3(...directions[$('viewSelect').value]).normalize().multiplyScalar(distance)).add(group.position);
    orbit.target.copy(group.position); orbit.update();
  }
  async function loadTextureLibrary() {
    const serial = ++textureSerial, gameId = $('textureGame').value;
    const previous = $('skinSelect').value;
    $('texturePath').textContent = gameId === 'q3' ? 'local-data/q3/textures' : gameId === 't2' ? 'static/textures/t2' : 'static/textures';
    textures = []; textureMetadata = new Map(); filterSkins();
    $('textureCount').textContent = 'Reading texture dimensions and colors…';
    try {
      const entries = await json(`/texture_metadata?${query({}, gameId)}`);
      if (serial !== textureSerial) return;
      textureMetadata = new Map(entries.map(entry => [entry.filename, entry]));
      textures = entries.map(entry => entry.filename).sort(compare);
      filterSkins(previous);
      if (!textures.length) $('skinSelect').replaceChildren(option(gameId === 'q3' ? 'Import Quake 3 to add textures' : 'No PNG textures in this library', ''));
    } catch (error) {
      if (serial === textureSerial) { $('skinSelect').replaceChildren(option('Texture library unavailable; try Reload textures', '')); $('textureCount').textContent = error.message; }
    }
  }
  async function allTextureVersions() {
    const results = await Promise.all(['t1', 't2', 'q3'].map(async gameId => {
      const values = await json(`/texture_versions?${query({}, gameId)}`);
      return Object.entries(values).map(([name, version]) => [textureId(name, gameId), version]);
    }));
    return Object.fromEntries(results.flat());
  }
  async function reloadTextures() {
    const serial = catalogSerial;
    await loadTextureLibrary();
    if (serial === catalogSerial) await loadModel(true);
  }
  action('gameSelect', 'change', 'Change game', loadCatalog);
  $('importQ3').addEventListener('click', async () => {
    const path = $('q3Path').value.trim();
    if (!path) { $('importStatus').textContent = 'Enter a local game folder or PK3 file.'; return; }
    $('importQ3').disabled = true; $('importStatus').textContent = 'Importing models and textures…';
    try {
      const response = await fetch('/import_q3', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      $('importStatus').textContent = `${result.entries} entries imported; ${result.ready} previews. Existing PNG edits kept.`;
      if (game === 'q3') await loadCatalog();
      history.length = future.length = 0; lastState = snapshot(); updateHistoryButtons();
      $('historyStatus').textContent = 'History restarted after import';
    } catch (error) { $('importStatus').textContent = error.message; }
    finally { $('importQ3').disabled = false; }
  });
  action('modelSearch', 'input', 'Search models', filterCatalog);
  action('categorySelect', 'change', 'Filter models', filterCatalog);
  action('modelSelect', 'change', 'Select model', selectModel);
  for (const [id, step] of [['previousModel', -1], ['nextModel', 1]]) action(id, 'click', 'Select model', () => {
    if (!filtered.length) return;
    const index = filtered.findIndex(x => x.model_name === $('modelSelect').value);
    $('modelSelect').value = filtered[(index + step + filtered.length) % filtered.length].model_name; return selectModel();
  });
  action('materialSelect', 'change', 'Select material', updateMaterialInspector);
  action('skinSearch', 'input', 'Search textures', () => filterSkins());
  action('skinSelect', 'change', 'Select texture', () => {
    selectThumbnail();
    $('textureGallery').querySelector('[aria-pressed="true"]')?.scrollIntoView({block: 'nearest'});
  });
  action('textureGame', 'change', 'Change texture library', () => { clearTextureFilters(); return loadTextureLibrary(); });
  action('applySkin', 'click', 'Apply texture', () => {
    const transform = copy(candidateTransform());
    overrides[$('materialSelect').value] = {game: $('textureGame').value, filename: $('skinSelect').value};
    if (transform.rotation || transform.flip_x || transform.flip_y) overrides[$('materialSelect').value].transform = transform;
    return loadModel(true);
  });
  action('resetSkin', 'click', 'Reset material', () => { delete overrides[$('materialSelect').value]; return loadModel(true); });
  action('applyFallback', 'click', 'Apply fallback', () => loadModel(true));
  action('loadModelBtn', 'click', 'Reload textures', reloadTextures);
  action('frameModel', 'click', 'Frame model', frameModel);
  action('viewSelect', 'change', 'Camera view', frameModel);
  $('backgroundColor').addEventListener('input', () => renderer.setClearColor($('backgroundColor').value));
  action('backgroundColor', 'change', 'Background color', () => {});
  action('wireframe', 'change', 'Wireframe', () => materials.forEach(material => { material.wireframe = $('wireframe').checked; }));
  action('lighting', 'change', 'Lighting', () => loadModel(true));
  for (const id of ['turntable', 'navigationStyle', 'walkSpeed', 'moveStep']) action(id, 'change', 'Preview settings', updateNavigationHint);
  $('texturePreview').addEventListener('load', () => { $('texturePreview').hidden = false; $('texturePreviewEmpty').hidden = true; $('textureDetails').textContent += ` · ${$('texturePreview').naturalWidth} × ${$('texturePreview').naturalHeight}`; });
  $('texturePreview').addEventListener('error', () => { $('texturePreviewEmpty').textContent = 'Texture unavailable'; });
  for (const [axis, vector] of [['X', [1, 0, 0]], ['Y', [0, 1, 0]], ['Z', [0, 0, 1]]]) {
    for (const [suffix, direction] of [['90', 1], ['N90', -1]]) action(`rot${axis}${suffix}`, 'click', 'Rotate model', () => {
      if (group) group.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(...vector), direction * Math.PI / 2));
      saveOrientation();
    });
  }
  action('resetRot', 'click', 'Reset orientation', () => {
    if (group && !loading) {
      group.rotation.set(0, 0, 0); saveOrientation();
      $('viewSelect').value = 'perspective'; frameModel();
    }
  });
  action('resetAll', 'click', 'Reset all', async () => {
    if (!selectedName) return;
    $('status').textContent = 'Restoring defaults…';
    $('exportObjBtn').disabled = $('exportGlbBtn').disabled = true;
    stopWalking();
    try { localStorage.removeItem(orientationKey()); localStorage.removeItem(positionKey()); } catch (_) { /* Reset the live model even without storage. */ }
    if (group) { group.quaternion.identity(); group.position.set(0, 0, 0); }
    overrides = {};
    $('textureName').value = (catalog.find(entry => entry.model_name === selectedName) || {}).texture_name || '';
    $('viewSelect').value = 'perspective';
    for (const id of ['turntable', 'wireframe', 'lighting']) $(id).checked = false;
    $('backgroundColor').value = '#182229'; renderer.setClearColor('#182229');
    $('walkSpeed').value = 5; $('navigationStyle').value = 'walk'; $('moveStep').value = 1;
    $('textureGame').value = game; clearTextureFilters(); $('exportStatus').textContent = '';
    drafts = {};
    await loadTextureLibrary(); await loadModel(false);
  });
  for (const axis of ['X', 'Y', 'Z']) {
    $(`position${axis}`).addEventListener('input', () => {
      const value = $(`position${axis}`).valueAsNumber;
      if (group && !loading && Number.isFinite(value)) {
        group.position[axis.toLowerCase()] = value;
        try { localStorage.setItem(positionKey(), JSON.stringify(group.position.toArray())); } catch (_) { /* Preview still moves. */ }
      }
    });
    action(`position${axis}`, 'change', 'Position model', updatePositionControls);
    for (const [suffix, sign] of [['N', -1], ['P', 1]]) action(`move${axis}${suffix}`, 'click', 'Move model', () => {
      const step = $('moveStep').valueAsNumber;
      if (group && !loading && Number.isFinite(step) && step > 0) { group.position[axis.toLowerCase()] += sign * step; savePosition(); }
    });
  }
  action('resetPosition', 'click', 'Reset position', () => { if (group && !loading) { group.position.set(0, 0, 0); savePosition(); } });
  for (const [id, operation] of [['rotateTextureLeft', -90], ['rotateTextureRight', 90], ['flipTextureX', 'flip_x'], ['flipTextureY', 'flip_y'], ['resetTextureCopy', 'reset']]) {
    action(id, 'click', 'Transform texture copy', () => transformCandidate(operation));
  }
  for (const id of ['widthMin', 'widthMax', 'heightMin', 'heightMax', 'sizeTolerance', 'hueTolerance']) action(id, 'input', 'Filter textures', () => filterSkins());
  for (const [id, kind] of [['similarSize', 'size'], ['similarHue', 'hue']]) action(id, 'click', `Find similar ${kind}`, () => {
    const meta = textureMetadata.get($('skinSelect').value);
    if (!meta?.width) return;
    clearTextureFilters();
    if (kind === 'size') sizeReference = copy(meta); else hueReference = copy(meta);
    $('textureFilters').open = true; filterSkins(meta.filename);
  });
  action('clearTextureFilters', 'click', 'Clear texture filters', () => { clearTextureFilters(); filterSkins(); });
  $('thumbnailSize').addEventListener('input', () => $('textureWorkbench').style.setProperty('--thumb-size', `${$('thumbnailSize').value}px`));
  action('thumbnailSize', 'change', 'Thumbnail size', () => {});
  action('saveTextureTags', 'click', 'Edit texture tags', async () => {
    const filename = $('skinSelect').value, gameId = $('textureGame').value, meta = textureMetadata.get(filename);
    const before = copy(meta.tags), tags = $('textureTags').value.split(',').map(tag => tag.trim()).filter(Boolean);
    const after = await postTags(gameId, filename, tags);
    meta.tags = after; filterSkins(filename); $('tagStatus').textContent = 'Tags saved locally.';
    return {game: gameId, filename, before, after};
  });
  $('expandGallery').addEventListener('click', () => {
    $('galleryDialogBody').append($('textureWorkbench')); $('galleryDialog').showModal();
    $('textureGallery').querySelector('[aria-pressed="true"]')?.scrollIntoView({block: 'nearest'});
  });
  $('closeGallery').addEventListener('click', () => $('galleryDialog').close());
  $('galleryDialog').addEventListener('close', () => { $('textureWorkbenchHome').append($('textureWorkbench')); $('expandGallery').focus(); });
  $('exportObjBtn').addEventListener('click', async () => {
    if (!group || loading || loadedParams === null) return;
    const name = selectedName, gameId = game, params = loadedParams;
    $('exportObjBtn').disabled = true; $('exportStatus').textContent = 'Preparing ZIP…';
    try {
      const response = await fetch(`/export_obj/${encodeURIComponent(name)}?${params}`);
      if (!response.ok) throw new Error(`Server returned ${response.status}`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = `${gameId}_${name}_export.zip`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      $('exportStatus').textContent = `Downloaded ${gameId.toUpperCase()} ${name}. Missing textures, if any, are listed inside the ZIP.`;
    } catch (error) { $('exportStatus').textContent = `Export failed: ${error.message}`; }
    finally { $('exportObjBtn').disabled = loading || !group; }
  });
  $('exportGlbBtn').addEventListener('click', async () => {
    if (!group || loading || loadedParams === null) return;
    const name = selectedName, gameId = game, params = loadedParams;
    $('exportGlbBtn').disabled = true; $('exportStatus').textContent = 'Preparing GLB animation clips…';
    try {
      const response = await fetch(`/export_glb/${encodeURIComponent(name)}?${params}`);
      if (!response.ok) {
        const error = await response.json(); throw new Error(error.error || `Server returned ${response.status}`);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = `${gameId}_${name}.glb`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      const clips = Number(response.headers.get('X-Skinner-Animation-Clips') || 0);
      const unavailable = response.headers.get('X-Skinner-Animation-Status') === 'static-fallback';
      $('exportStatus').textContent = `Downloaded ${gameId.toUpperCase()} ${name} GLB · ${unavailable ? 'static only: source animation could not be retained; details are embedded in the GLB' : clips ? `${clips} animation clips` : 'static model (no geometry animation)'}.`;
    } catch (error) { $('exportStatus').textContent = `GLB export failed: ${error.message}`; }
    finally { $('exportGlbBtn').disabled = loading || !group; }
  });
  new ResizeObserver(() => {
    const {width, height} = $('viewport').getBoundingClientRect();
    camera.aspect = width / height; camera.updateProjectionMatrix(); renderer.setSize(width, height, false);
  }).observe($('viewport'));
  let lastTime = 0;
  renderer.setAnimationLoop(time => {
    const delta = Math.max(0, Math.min((time - lastTime) / 1000, .1));
    if ($('turntable').checked && group && !walking) group.rotation.y += delta * .4;
    if (walking) moveCamera(delta); else orbit.update();
    lastTime = time; renderer.render(scene, camera);
  });
  // Local fingerprint polling also catches atomic saves and works fully offline.
  setInterval(async () => {
    if (pollBusy || loading || historyBusy || !data) return;
    pollBusy = true;
    const gameId = game;
    try {
      const current = await allTextureVersions();
      if (gameId !== game || loading || historyBusy) return;
      const changed = versions && (data.material_textures || []).some((name, slot) => {
        const id = textureId(name, sourceGame(data, slot));
        return JSON.stringify(current[id]) !== JSON.stringify(versions[id]);
      });
      versions = current;
      if (changed) {
        setHistoryBusy(true);
        try { await reloadTextures(); lastState = snapshot(); }
        finally { setHistoryBusy(false); }
      }
    } catch (_) { /* Manual reload stays available if polling is unavailable. */ }
    finally { pollBusy = false; }
  }, 2000);
  loadCatalog().then(() => { lastState = snapshot(); updateHistoryButtons(); });
});

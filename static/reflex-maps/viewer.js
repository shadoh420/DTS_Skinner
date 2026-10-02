/* Free-flight viewer and brush editor for Reflex Arena maps (mapfile.js reads them, brush.js edits their brushes).
   The map file is drawn as it is: Reflex has no compile step. Coordinates stay the game's (y up, left-handed) under
   a root mirrored on z, so x right, y up and z forward appear as in the game; Three turns the front faces of a
   mirrored mesh round itself, so faces wound counter-clockwise from outside in the file face out here too.

   0 (or Tab) switches between flying and editing, as the game's 0 switches between playing and editing. The editing
   controls follow the game's editor binds (game_default.cfg, "bind me"): a click selects a brush of the map, Ctrl
   adds to the selection, the right button looks; dragging a selected brush moves it on the grid, Alt dragging it up
   and down, and Shift dragging one of its faces pushes or pulls that face; G clones, Backspace deletes, Z and X undo
   and redo, K picks up the material under the cursor and M puts it on the selection (Shift+M on one face). The
   toolbar's CSG, which the game's editor did not have, works on the selection. As the game's me_createtype, 1 to 8
   choose what a click makes: 1 a brush, 2 and 3 a teleporter's and a jump pad's volume (each dragged out on a
   surface), 4 to 8 a target, effect, pickup, point light or player spawn (placed with a click). V shows the corners
   of the selected brushes to drag, as the game's vertex mode. Entities are selected and moved as brushes are. Shift
   and a click picks a face; B then bridges it to the face aimed at, as the game's bridge tool (me_startbridge), the
   wheel setting the steps. N shows the properties of the selected entity, or of the map's WorldSpawn
   (me_showproperties). Brushes placed by a prefab are drawn but not edited: their prefab holds them. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const B = window.ReflexBrush, M = window.ReflexMap;
  const storageKey = 'skinner.reflexmaps';
  const settings = {fov: 100, invertX: false, invertY: false, grid: 16};  // The game's me_snapdistance is 16.
  try { Object.assign(settings, JSON.parse(localStorage.getItem(storageKey) || '{}')); } catch (_) { /* Defaults remain usable. */ }
  const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(settings)); } catch (_) { /* Storage may be unavailable. */ } };

  const canvas = $('c');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene(), root = new THREE.Group();
  root.scale.z = -1;  // The game's left-handed axes in Three's right-handed ones.
  scene.add(root);
  const camera = new THREE.PerspectiveCamera(90, 1, 1, 65536);
  camera.rotation.order = 'YXZ';
  const data = '/reflex-map-data/';
  let speed = 400, map = null, mapName = '', editing = false, flat = null;
  const entryOfTriangle = [], clipEntryOfTriangle = [], glassEntryOfTriangle = [], volumeEntryOfTriangle = [];
  const notes = [];
  const showStatus = text => { $('status').textContent = text; };

  // ---- Colours ----
  // Shading is linear, as the game's physically based materials are, and the frame is encoded as sRGB. A face with a
  // colour of its own (alpha above zero) is that colour, taken as the sRGB bytes a colour picker gives; otherwise its
  // material's albedo, read from the game's material files by the import (materials.json) and linear already, with
  // its metallic value. Without one, a material's colour is guessed from its name.
  const GUESSED = {
    'metal/gunmetal': [.30, .31, .33], 'metal/steel_stained': [.42, .40, .37], 'metal/aluminum': [.70, .71, .72], 'metal/p_metal': [.40, .40, .42],
    'metal/gold': [.80, .64, .30], 'metal/steel': [.55, .56, .58], 'stone/concrete': [.55, .55, .52], 'stone/stone': [.50, .48, .45],
    'stone/brick': [.52, .32, .25], 'wood/bare': [.45, .32, .20], 'fabric/silk': [.80, .78, .76], 'fabric/cotton': [.74, .73, .70],
    'fabric/leather': [.36, .23, .15], 'lava/lava': [1, .45, .10], 'slime/slime': [.30, .75, .20], 'veg/ivy': [.25, .40, .20],
    'dev_grey128': [.50, .50, .50], 'dev_grey192': [.75, .75, .75], 'dev_grey64': [.25, .25, .25], 'race/race_finish': [.85, .85, .85],
  };
  const linear = channel => Math.pow(channel, 2.2);
  let packColours = {};
  const materials = new Map();
  // {albedo (linear), metallic} of a material.
  function materialOf(name) {
    if (!materials.has(name)) {
      const read = packColours[name];
      if (read && read.colour) materials.set(name, {albedo: read.colour, metallic: read.metallic || 0});
      else {
        const known = Object.keys(GUESSED).find(key => name.includes(key));
        let colour = known && GUESSED[known];
        if (!colour) {
          // Anything else is a pale colour of its own, so neighbouring materials stay apart.
          let hash = 0;
          for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
          colour = new THREE.Color().setHSL((hash % 360) / 360, .25, .55).toArray();
        }
        materials.set(name, {albedo: colour.map(linear), metallic: 0});
      }
    }
    return materials.get(name);
  }
  // Diffuse colour and the colour of reflections at normal incidence: 4 % for anything not metal, the albedo for metal.
  function faceShade(face) {
    const material = materialOf(face.material || ''), own = M.colourOf(face);
    const albedo = own ? own.slice(0, 3).map(linear) : material.albedo, metallic = material.metallic;
    return {diffuse: albedo.map(c => c * (1 - metallic)), specular: albedo.map(c => .04 * (1 - metallic) + c * metallic)};
  }
  // See-through materials, by shader (light beams, glass, race start and finish, pickup and powerup glows) or by
  // name for water, whose fluid shader lava and slime share; drawn after everything else, faintly.
  const SEE_THROUGH = /alphaFresnel|GLASS|raceStartFinish|glowPickup|powerup/;
  const isSeeThrough = face => {
    const name = face.material || '', read = packColours[name];
    return read ? SEE_THROUGH.test(read.shader || '') || /liquids\/water/.test(name) : /fx_light_beam|race_(start|finish)|glass|liquids\/water/.test(name);
  };
  // Faces the game does not draw: the editor's clip materials (player, weapon and full clip).
  const isClip = face => /^internal\/editor\/textures\/editor_.*clip/.test(face.material || '');

  // ---- Drawing brushes ----
  const uniforms = {uGrid: {value: 0}, uSun: {value: new THREE.Vector3(.35, .8, .5).normalize()}};
  const brushMaterial = new THREE.ShaderMaterial({
    uniforms,
    extensions: {derivatives: true},
    vertexShader: `attribute vec3 color; attribute vec3 specular; varying vec3 vColor; varying vec3 vSpecular; varying vec3 vNormal; varying vec3 vPos; varying float vDepth;
      void main() {
        vColor = color; vSpecular = specular; vNormal = normal; vPos = position;
        vec4 view = modelViewMatrix * vec4(position, 1.);
        vDepth = -view.z;
        gl_Position = projectionMatrix * view;
      }`,
    // One fixed sun, light from the sky and a grey surrounding to reflect, as the map's baked light and reflection
    // probes are not read; while editing, the editor's grid.
    fragmentShader: `uniform float uGrid; uniform vec3 uSun; varying vec3 vColor; varying vec3 vSpecular; varying vec3 vNormal; varying vec3 vPos; varying float vDepth;
      void main() {
        vec3 n = normalize(vNormal);
        float light = .2 + .6 * max(dot(n, uSun), 0.) + .25 * (n.y * .5 + .5);
        vec3 c = vColor * light + vSpecular * .9;
        if (uGrid > 0.) {
          vec3 p = vPos / uGrid, w = fwidth(p) + 1e-5;
          vec3 g = abs(fract(p - .5) - .5) / w + abs(n) * 1e3;
          float line = 1. - min(min(min(g.x, g.y), g.z), 1.);
          vec3 q = vPos / (uGrid * 8.), wq = fwidth(q) + 1e-5;
          vec3 gq = abs(fract(q - .5) - .5) / wq + abs(n) * 1e3;
          float major = 1. - min(min(min(gq.x, gq.y), gq.z), 1.);
          c = mix(c, c * .45 + .004, max(line * .5, major) * clamp(1. - vDepth / 3000., 0., 1.));
        }
        gl_FragColor = vec4(pow(c, vec3(1. / 2.2)), 1.);
      }`,
  });
  const clipMaterial = new THREE.MeshBasicMaterial({color: 0xb04cff, transparent: true, opacity: .22, depthWrite: false, side: THREE.DoubleSide});
  const selectedFill = new THREE.MeshBasicMaterial({color: 0xf0c674, transparent: true, opacity: .25, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1});
  const selectedEdges = new THREE.LineBasicMaterial({color: 0xf0c674, transparent: true, opacity: .9, depthTest: false});
  const glassMaterial = brushMaterial.clone();
  Object.assign(glassMaterial, {transparent: true, depthWrite: false, side: THREE.DoubleSide});
  glassMaterial.uniforms = uniforms;
  glassMaterial.fragmentShader = brushMaterial.fragmentShader.replace('gl_FragColor = vec4(pow(c, vec3(1. / 2.2)), 1.);', 'gl_FragColor = vec4(pow(c, vec3(1. / 2.2)), .35);');
  const world = new THREE.Mesh(new THREE.BufferGeometry(), brushMaterial), clips = new THREE.Mesh(new THREE.BufferGeometry(), clipMaterial);
  const glass = new THREE.Mesh(new THREE.BufferGeometry(), glassMaterial);
  // The brushes that are a teleporter's, jump pad's, race start's or finish's or trigger's volume: shown while editing.
  const volumes = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({color: 0x3fc8ff, transparent: true, opacity: .2, depthWrite: false, side: THREE.DoubleSide}));
  const selection = new THREE.Group();
  glass.renderOrder = 1; clips.renderOrder = 2; volumes.renderOrder = 2; selection.renderOrder = 3;
  root.add(world, glass, clips, volumes, selection);

  // Concave faces (a vertex dragged in the game's editor) are cut into triangles by Three's ear clipping.
  function triangulate(points, normal) {
    const axis = Math.abs(normal[0]) > Math.abs(normal[1]) ? (Math.abs(normal[0]) > Math.abs(normal[2]) ? 0 : 2) : (Math.abs(normal[1]) > Math.abs(normal[2]) ? 1 : 2);
    const [a, b] = [[1, 2], [2, 0], [0, 1]][axis];
    return THREE.ShapeUtils.triangulateShape(points.map(p => new THREE.Vector2(p[a], p[b])), []);
  }
  function buildMesh(entries, wanted, owners) {
    const positions = [], normals = [], colours = [], speculars = [];
    owners.length = 0;
    entries.forEach((entry, index) => {
      const brush = entry.brush;
      for (const face of brush.faces) {
        if (wanted(face, entry) === false) continue;
        const n = B.normalize(B.newell(face.indices.map(i => brush.vertices[i])));
        const {diffuse, specular} = faceShade(face);
        for (const triangle of B.triangles(brush, face, triangulate)) {
          for (const vertex of triangle) {
            positions.push(...brush.vertices[vertex]);
            normals.push(...n);
            colours.push(...diffuse);
            speculars.push(...specular);
          }
          owners.push([index, face]);
        }
      }
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colours, 3));
    geometry.setAttribute('specular', new THREE.Float32BufferAttribute(speculars, 3));
    geometry.computeBoundingSphere();
    return geometry;
  }

  // ---- Entities ----
  // Marked where the map places them, coloured by kind; Effects (the game's models and lights) are small and grey.
  const markerGeometry = new THREE.OctahedronGeometry(1);
  const entityLayer = new THREE.Group();
  root.add(entityLayer);
  function pickupColour(type) {
    return type < 20 ? 0xe5534b : type < 40 ? 0xf0883e : type < 50 ? 0x3fb9c9 : type < 60 ? 0xf0c674 : 0xb57edc;
  }
  function markerOf(entity) {
    const type = entity.type;
    if (type === 'PlayerSpawn') return [0x5bd75b, 12];
    if (type === 'Pickup') return [pickupColour(M.property(entity, 'pickupType') || 0), 10];
    if (type === 'Teleporter') return [0x4c8dff, 14];
    if (type === 'JumpPad') return [0x9be15d, 14];
    if (type === 'Target') return [0xffffff, 6];
    if (type === 'PointLight') return [0xfff3b0, 5];
    if (type === 'Effect') return [0x8a949a, 3];
    return null;  // WorldSpawn, Prefab, probes, paths and the rest are not marked.
  }
  const markers = [];  // {entry, mesh} of every marked entity, for picking.
  function buildEntities() {
    entityLayer.clear();
    markers.length = 0;
    const byKind = new Map();
    for (const entry of flat.entities) {
      const marker = entry.position && markerOf(entry.entity);
      if (!marker) continue;
      const key = marker.join('|');
      if (!byKind.has(key)) byKind.set(key, {marker, entries: []});
      byKind.get(key).entries.push(entry);
    }
    const matrix = new THREE.Matrix4();
    for (const {marker: [colour, size], entries} of byKind.values()) {
      const mesh = new THREE.InstancedMesh(markerGeometry, new THREE.MeshBasicMaterial({color: colour}), entries.length);
      mesh.userData.editOnly = size <= 3;  // Effects: a map can have a thousand; they show while editing.
      entries.forEach(({position: p}, index) => mesh.setMatrixAt(index, matrix.makeScale(size, size * 1.6, size).setPosition(p[0], p[1], p[2])));
      entries.forEach(entry => markers.push({entry, mesh}));
      entityLayer.add(mesh);
    }
  }

  // ---- The map ----
  const globalGroup = () => M.global(map);
  function rebuild() {
    flat = M.flatten(map);
    ownerOfItem = new Map(flat.brushes.filter(entry => !entry.path.length).map(entry => [entry.source, entry.owner]));
    for (const mesh of [world, clips, glass, volumes]) mesh.geometry.dispose();
    const volume = entry => M.isVolume(entry.owner);
    world.geometry = buildMesh(flat.brushes, (face, entry) => !volume(entry) && !isClip(face) && !isSeeThrough(face), entryOfTriangle);
    glass.geometry = buildMesh(flat.brushes, (face, entry) => !volume(entry) && !isClip(face) && isSeeThrough(face), glassEntryOfTriangle);
    clips.geometry = buildMesh(flat.brushes, (face, entry) => !volume(entry) && isClip(face), clipEntryOfTriangle);
    volumes.geometry = buildMesh(flat.brushes, (face, entry) => volume(entry), volumeEntryOfTriangle);
    buildEntities();
    for (const mesh of entityLayer.children) if (mesh.userData.editOnly) mesh.visible = editing;
    showSelection();
  }

  function worldSpawn() { return (globalGroup().items || []).find(item => item.kind === 'entity' && item.type === 'WorldSpawn'); }
  function applySky() {
    const spawn = worldSpawn(), hex = spawn && (M.property(spawn, 'sky.horizonColor') || M.property(spawn, 'sky.skyTopColor') || M.property(spawn, 'fogColor'));
    const colour = hex ? new THREE.Color(`#${String(hex).slice(-6)}`) : new THREE.Color(0x2a3b47);
    if (colour.getHSL({}).l < .05) colour.setHex(0x2a3b47);  // A black horizon (night skies) still shows the map's edges.
    scene.background = colour;
  }

  // Viewpoints: the camera the map ends on (its WorldSpawn's targetGameOverCamera) first, then the spawn points.
  // Yaw 0 looks along +z and turns toward +x, as prefabs turn; positive pitch is taken to look down.
  function viewpoints() {
    if (!flat) return [];
    const end = worldSpawn() && M.property(worldSpawn(), 'targetGameOverCamera');
    const of = type => flat.entities.filter(({entity, position}) => entity.type === type && position);
    return [...of('Target').filter(({entity}) => M.property(entity, 'name') === end), ...of('PlayerSpawn')]
      .map(({entity, position}) => ({position, angles: M.property(entity, 'angles') || [0, 0, 0], height: entity.type === 'PlayerSpawn' ? 48 : 0}));
  }
  function showViewpoint(index) {
    const view = viewpoints()[index];
    if (!view) {
      // No viewpoint: above the middle of the map, looking down into it.
      const box = new THREE.Box3().setFromObject(world);
      if (box.isEmpty()) { camera.position.set(0, 256, 512); camera.rotation.set(-.4, 0, 0); return; }
      const middle = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
      camera.position.set(middle.x, box.max.y + size.length() * .15, middle.z + size.z * .6);
      camera.rotation.set(-.6, 0, 0);
      return;
    }
    const [x, y, z] = view.position;
    camera.position.set(x, y + view.height, -z);
    camera.rotation.set(-THREE.MathUtils.degToRad(view.angles[1]), -THREE.MathUtils.degToRad(view.angles[0]), 0);
  }

  function showNotes() {
    const parts = [];
    if (flat && flat.missing.length) parts.push('Prefabs named but not in the file, so not drawn: ' + flat.missing.join(', '));
    const bent = flat ? flat.brushes.filter(entry => !B.convex(entry.brush)).length : 0;
    if (bent) parts.push(`${bent} brushes are not convex or have bent faces; they are drawn, and CSG leaves them alone`);
    // Materials some face draws in its material's colour, where that colour is a guess.
    const guessed = new Set();
    if (flat) for (const {brush, owner} of flat.brushes) if (!M.isVolume(owner)) for (const face of brush.faces) if (!M.colourOf(face) && !isClip(face) && !(packColours[face.material || ''] || {}).colour) guessed.add(face.material || 'no material');
    if (guessed.size) parts.push(`Drawn in a colour guessed from the material's name, as the import found no albedo for it: ${[...guessed].sort().join(', ')}`);
    parts.push(...notes);
    $('mapNotes').textContent = parts.length ? ' This map — ' + parts.join('. ') + '.' : '';
  }
  $('notes').after(Object.assign(document.createElement('span'), {id: 'mapNotes'}));

  function load(text, name) {
    const parsed = M.parse(text);
    if (!M.global(parsed)) parsed.groups.push({kind: 'global', name: '', items: []});
    map = parsed; mapName = name; selected.clear(); undoStack.length = 0; redoStack.length = 0;
    rebuild();
    applySky();
    showViewpoint(0);
    showNotes();
    document.title = `${name} — Reflex Maps`;
    showReady();
  }
  function showReady() {
    const editable = globalGroup().items.filter(item => item.kind === 'brush').length;
    showStatus(`${mapName}: ${flat.brushes.length} brushes (${editable} in the map, ${flat.brushes.length - editable} placed by prefabs), ` +
      `${flat.entities.length} entities · speed ${Math.round(speed)}`);
    updateTools();
  }

  // ---- Editing ----
  const selected = new Set(), undoStack = [], redoStack = [];
  const snap = value => Math.round(value / settings.grid) * settings.grid;
  let lastPoint = null, lastNormal = null, createType = 0, vertexMode = false;
  // The bridge tool: the face Shift-click picked, whether B is aiming from it, the steps, and what a click would make.
  let bridgeFrom = null, bridging = false, segments = 4, bridgePreview = null;
  // Fields new faces take: those K picked up, else plain concrete.
  let template = {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0, colour: '0x00000000', material: 'common/materials/stone/concrete'};
  const say = text => { $('selection').textContent = text; };
  const isBrush = item => item.kind === 'brush';
  const selectedBrushes = () => [...selected].filter(isBrush);
  // The entity each brush of the map belongs to: the entity before it in the list (mapfile.js flatten).
  let ownerOfItem = new Map();
  const ownerOf = item => ownerOfItem.get(item) || null;
  const isWorldBrush = item => isBrush(item) && !M.isVolume(ownerOf(item));
  const positionOf = entity => M.property(entity, 'position');
  const withPosition = (entity, position) => ({...entity, properties: entity.properties.map(p => p.name === 'position' ? {...p, value: position.map(v => Math.round(v * 1e6) / 1e6 + 0)} : p)});

  // A step of undo is the map's list of items and what was selected, so undoing brings back both.
  const snapshot = () => ({items: globalGroup().items.slice(), selected: [...selected]});
  function remember() {
    undoStack.push(snapshot());
    if (undoStack.length > 100) undoStack.shift();
    redoStack.length = 0;
  }
  function restore(from, to) {
    if (!from.length) return;
    to.push(snapshot());
    const step = from.pop();
    globalGroup().items = step.items;
    selected.clear();
    for (const item of step.selected) if (step.items.includes(item)) selected.add(item);
    rebuild(); showReady();
  }
  // Changes the map's list, as one step of undo. `changes` maps an item to the items (or brushes) standing in its
  // place, which so keep its owner; `inserts` adds things at a place: 'world' (in the WorldSpawn's run of brushes, so a
  // brush is the world's and not that of whatever entity comes last), 'end', or after an item.
  const asItem = thing => thing.kind ? thing : {kind: 'brush', vertices: thing.vertices, faces: thing.faces};
  function replace(changes, inserts = [], select = null) {
    remember();
    const items = [], changed = [], added = [];
    for (const item of globalGroup().items) {
      if (!changes.has(item)) { items.push(item); continue; }
      const instead = changes.get(item).map(asItem);
      items.push(...instead);
      changed.push(...instead);
    }
    for (const {at, things} of inserts) {
      const fresh = things.map(asItem);
      const index = at === 'world' ? M.worldInsertAt({items}) : at === 'end' ? items.length : (items.indexOf(at) + 1 || items.length);
      items.splice(index, 0, ...fresh);
      added.push(...fresh);
    }
    globalGroup().items = items;
    selected.clear();
    selection.position.set(0, 0, 0);
    for (const item of select === 'added' ? added : select === 'changed' ? changed : select || []) if (items.includes(item)) selected.add(item);
    rebuild(); showReady();
  }

  // ---- Selection overlay, vertex dots and the shape being drawn ----
  const ghost = new THREE.Group();
  root.add(ghost);
  const dotMaterial = new THREE.PointsMaterial({color: 0xf0c674, size: 9, sizeAttenuation: false, depthTest: false, transparent: true});
  const hoverMaterial = new THREE.PointsMaterial({color: 0xffffff, size: 14, sizeAttenuation: false, depthTest: false, transparent: true});
  const hoverDot = new THREE.Points(new THREE.BufferGeometry(), hoverMaterial);
  hoverDot.renderOrder = 5; hoverDot.visible = false;
  root.add(hoverDot);
  let dots = [];  // {item, index, position} of the corners of the selected brushes, in vertex mode.
  const edgesOf = brush => {
    const lines = [];
    for (const face of brush.faces) face.indices.forEach((index, n) => lines.push(...brush.vertices[index], ...brush.vertices[face.indices[(n + 1) % face.indices.length]]));
    const edges = new THREE.BufferGeometry();
    edges.setAttribute('position', new THREE.Float32BufferAttribute(lines, 3));
    return new THREE.LineSegments(edges, selectedEdges);
  };
  // `preview` maps a selected brush to the brush a drag would make of it.
  function showSelection(preview = new Map()) {
    selection.clear();
    dots = [];
    if (!flat) return;
    for (const entry of flat.brushes) {
      if (!selected.has(entry.source) || entry.path.length) continue;
      const brush = preview.get(entry.source) || entry.brush;
      selection.add(new THREE.Mesh(buildMesh([{brush}], () => true, []), selectedFill), edgesOf(brush));
      if (vertexMode) brush.vertices.forEach((position, index) => dots.push({item: entry.source, index, position}));
    }
    for (const entry of flat.entities) {
      if (!selected.has(entry.entity) || entry.path.length || !entry.position) continue;
      const box = new THREE.Box3Helper(new THREE.Box3().setFromCenterAndSize(new THREE.Vector3(...entry.position), new THREE.Vector3(24, 36, 24)), 0xf0c674);
      box.material.depthTest = false;
      selection.add(box);
    }
    if (bridgeFrom && !globalGroup().items.includes(bridgeFrom.item)) { bridgeFrom = null; bridging = false; }
    if (bridgeFrom) {
      const outline = new THREE.LineLoop(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(bridgeFrom.points.flat(), 3)),
        new THREE.LineBasicMaterial({color: 0x3fc8ff, depthTest: false, transparent: true}));
      outline.renderOrder = 4;
      selection.add(outline);
    }
    if (dots.length) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(dots.flatMap(dot => dot.position), 3));
      const points = new THREE.Points(geometry, dotMaterial);
      points.renderOrder = 4;
      selection.add(points);
    }
    updateTools();
  }
  // Labels each create type as the game's me_createtype does; 1 to 3 are dragged out on a surface (a brush, and the
  // volumes of a teleporter and a jump pad), 4 to 8 are placed with a click.
  const CREATE = {
    1: {label: 'brush'}, 2: {type: 'Teleporter', label: 'teleporter'}, 3: {type: 'JumpPad', label: 'jump pad'},
    4: {type: 'Target', label: 'target'}, 5: {type: 'Effect', label: 'effect'}, 6: {type: 'Pickup', label: 'pickup'},
    7: {type: 'PointLight', label: 'point light'}, 8: {type: 'PlayerSpawn', label: 'player spawn'},
  };
  function updateTools() {
    if (!flat) return;
    const brushes = selectedBrushes(), count = selected.size, convex = brushes.length > 0 && brushes.every(B.convex);
    $('subtract').disabled = !convex;
    $('hollow').disabled = brushes.length !== 1 || !convex;
    $('merge').disabled = brushes.length < 2 || !convex;
    $('clip').disabled = !convex || !lastPoint;
    $('duplicate').disabled = $('delete').disabled = !count;
    $('undo').disabled = !undoStack.length; $('redo').disabled = !redoStack.length;
    $('mode').title = ($('mode').textContent = bridging ? `Bridge (B): aim at a face · wheel ${segments} step${segments > 1 ? 's' : ''} · click makes · Esc stops`
      : createType ? `Create ${CREATE[createType].label} (${createType}): ${createType <= 3 ? 'drag on a surface' : 'click to place'} · Esc stops`
      : vertexMode ? 'Vertex mode (V): drag a corner · Esc stops' : 'Edit mode');
    showPanel();
    $('material').textContent = `${template.material || 'no material'}${template.colour && M.colourOf(template) ? ` · ${template.colour}` : ''}`;
    $('swatch').style.background = '#' + new THREE.Color(...faceShade(template).diffuse.map(c => Math.pow(c, 1 / 2.2))).getHexString();
  }

  // ---- Picking ----
  // The brush face under the mouse, or a marked entity within 12 pixels of it and not behind a solid face. Placed prefab
  // brushes and entities are reported, not selected.
  const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
  function pick(event, entities = true) {
    const rect = canvas.getBoundingClientRect();
    pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    camera.updateMatrixWorld();  // The camera may have moved since the last frame drew.
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects([world, glass, clips, volumes].filter(mesh => mesh.visible), false);
    // Only solid faces hide a marker; volumes, clips and glass are seen through.
    const hit = hits[0], solid = hits.find(candidate => candidate.object === world);
    let nearest = null;
    if (entities) {
      const v = new THREE.Vector3();
      for (const marker of markers) {
        if (!marker.mesh.visible) continue;
        v.set(marker.entry.position[0], marker.entry.position[1], -marker.entry.position[2]);
        const distance = v.distanceTo(camera.position);
        v.project(camera);
        if (v.z > 1) continue;
        const off = Math.hypot(rect.left + (v.x + 1) / 2 * rect.width - event.clientX, rect.top + (1 - v.y) / 2 * rect.height - event.clientY);
        if (off < 12 && (!solid || distance < solid.distance + 16) && (!nearest || distance < nearest.distance)) nearest = {entry: marker.entry, distance};
      }
    }
    if (nearest) return {entity: nearest.entry, point: nearest.entry.position, normal: [0, 1, 0]};
    if (!hit) return null;
    const owners = hit.object === world ? entryOfTriangle : hit.object === glass ? glassEntryOfTriangle : hit.object === clips ? clipEntryOfTriangle : volumeEntryOfTriangle;
    const [index, face] = owners[hit.faceIndex];
    const point = root.worldToLocal(hit.point.clone()).toArray();
    const normal = hit.face.normal.toArray();  // Geometry normals are in the game's axes already.
    return {entry: flat.brushes[index], face, point, normal};
  }
  const adding = event => event.ctrlKey || event.metaKey;
  // What B.check found, as a sentence: 'has a bent face and is not convex'.
  const PROBLEM = {'a bent face': 'has a bent face', 'not convex': 'is not convex', 'a face with no area': 'has a face with no area', 'fewer than four faces': 'has fewer than four faces'};
  const problemText = problems => problems.map(problem => PROBLEM[problem] || problem).join(' and ').replace(/ and (?=.* and )/g, ', ');
  function describe(item, entry) {
    if (!isBrush(item)) {
      const name = M.property(item, 'name') || M.property(item, 'target') || M.property(item, 'effectName') || (item.type === 'Pickup' ? `type ${M.property(item, 'pickupType')}` : '');
      return `${item.type}${name !== undefined && name !== '' ? ` ${name}` : ''}`;
    }
    const owner = entry.owner, problems = B.check(item);
    const what = M.isVolume(owner) ? `${owner.type} volume${M.property(owner, 'target') ? ` to ${M.property(owner, 'target')}` : ''}` : 'brush';
    return `${what}${problems.length ? ` · it ${problemText(problems)}, so CSG and dragging its faces leave it alone` : ''}`;
  }
  // The face a Shift-click picked: its brush, the face, its corners and outward normal, for the bridge tool.
  function pickFace(hit) {
    const item = hit.entry.source, plane = B.planesOf(item).find(candidate => candidate.face === hit.face);
    if (!plane) return null;
    return {item, face: hit.face, points: hit.face.indices.map(i => item.vertices[i]), normal: plane.normal};
  }
  function select(event) {
    if (event.shiftKey) {
      const hit = pick(event, false);
      if (hit && !hit.entry.path.length) {
        bridgeFrom = pickFace(hit);
        showSelection();
        say(bridgeFrom ? `Face picked (${bridgeFrom.points.length} corners) · B bridges it to another face` : '');
      }
      return;
    }
    const hit = pick(event);
    if (!hit) { if (!adding(event)) selected.clear(); say(''); showSelection(); return; }
    lastPoint = hit.point; lastNormal = hit.normal;
    const entry = hit.entity || hit.entry;
    if (entry.path.length) {
      say(`Part of prefab "${M.property(entry.path[0], 'prefabName')}", placed by a Prefab entity; edit the prefab in Reflex`);
      if (!adding(event)) selected.clear();
      showSelection();
      return;
    }
    const item = hit.entity ? hit.entity.entity : hit.entry.source;
    if (adding(event)) { if (selected.has(item)) selected.delete(item); else selected.add(item); } else { selected.clear(); selected.add(item); }
    showSelection();
    say(`${selected.size} selected · ${describe(item, entry)}`);
  }
  // The fields of a face, without its vertices: what a new brush or M gives faces.
  const fieldsOf = face => { const fields = {...face}; delete fields.indices; return fields; };

  // ---- Dragging (the game's +editorprimary with +editorvertical and +editorfacemode) ----
  // The mouse ray in the game's axes.
  function ray(event) {
    const rect = canvas.getBoundingClientRect();
    pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    camera.updateMatrixWorld();  // The camera may have moved since the last frame drew.
    raycaster.setFromCamera(pointer, camera);
    const {origin, direction} = raycaster.ray;
    return {origin: [origin.x, origin.y, -origin.z], direction: [direction.x, direction.y, -direction.z]};
  }
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  // How far along the line through `point` in direction `along` the mouse ray comes closest.
  function alongLine(point, along, {origin, direction}) {
    const w = point.map((p, i) => p - origin[i]), a = dot(along, along), b = dot(along, direction), c = dot(direction, direction);
    const denominator = a * c - b * b;
    return Math.abs(denominator) < 1e-9 ? 0 : (b * dot(direction, w) - c * dot(along, w)) / denominator;
  }
  // Where the mouse ray meets the plane square to `axis` at `level`; null when it runs along it or away.
  function onPlane(mouse, axis, level) {
    if (Math.abs(mouse.direction[axis]) < 1e-6) return null;
    const t = (level - mouse.origin[axis]) / mouse.direction[axis];
    return t > 0 ? mouse.origin.map((o, i) => o + mouse.direction[i] * t) : null;
  }
  // A drag on the grid from `point`: across its level, or up and down with Alt. Returns the offset.
  function gridOffset(event, point) {
    const mouse = ray(event), grid = settings.grid;
    if (event.altKey) return [0, Math.round(alongLine(point, [0, 1, 0], mouse) / grid) * grid, 0];
    const at = onPlane(mouse, 1, point[1]);
    return at && [Math.round((at[0] - point[0]) / grid) * grid, 0, Math.round((at[2] - point[2]) / grid) * grid];
  }

  // The corner dot nearest the mouse, within 10 pixels.
  function nearestDot(event) {
    const rect = canvas.getBoundingClientRect(), v = new THREE.Vector3();
    let best = null;
    for (const candidate of dots) {
      v.set(candidate.position[0], candidate.position[1], -candidate.position[2]).project(camera);
      if (v.z > 1) continue;
      const off = Math.hypot(rect.left + (v.x + 1) / 2 * rect.width - event.clientX, rect.top + (1 - v.y) / 2 * rect.height - event.clientY);
      if (off < 10 && (!best || off < best.off)) best = {...candidate, off};
    }
    return best;
  }
  function showHover(position) {
    hoverDot.visible = !!position;
    if (position) { hoverDot.geometry.dispose(); hoverDot.geometry = new THREE.BufferGeometry(); hoverDot.geometry.setAttribute('position', new THREE.Float32BufferAttribute(position, 3)); }
  }
  // A brush with one corner moved, as the game's vertex mode moves it: faces may bend. A corner dropped on another
  // corner becomes it, and faces left with fewer than three corners go; null when fewer than four faces are left.
  function moveCorner(brush, index, position) {
    let vertices = brush.vertices.map((v, i) => i === index ? position : v), faces = brush.faces;
    const same = vertices.findIndex((v, i) => i !== index && Math.hypot(v[0] - position[0], v[1] - position[1], v[2] - position[2]) < 1e-3);
    if (same >= 0) {
      faces = faces.map(face => {
        const ids = face.indices.map(i => i === index ? same : i);
        return {...face, indices: ids.filter((id, n) => id !== ids[(n + 1) % ids.length])};
      }).filter(face => new Set(face.indices).size >= 3)
        .map(face => ({...face, indices: face.indices.map(i => i > index ? i - 1 : i)}));
      vertices = vertices.filter((_, i) => i !== index);
    }
    return faces.length >= 4 ? {vertices, faces} : null;
  }

  // Where a click of create type 4 to 8 puts an entity: on a floor where it was clicked, against a wall or ceiling
  // one grid step out from it; on the grid across.
  function placeAt(point, normal) {
    if (normal[1] > .7) return [snap(point[0]), Math.round(point[1] * 1000) / 1000, snap(point[2])];
    return point.map((p, i) => snap(p + normal[i] * settings.grid));
  }
  // New entities, with the properties the stock maps give every one of their type (and the most common values).
  function uniqueName(prefix) {
    const taken = new Set(flat.entities.map(entry => M.property(entry.entity, 'name')));
    let n = 1;
    while (taken.has(prefix + n)) n++;
    return prefix + n;
  }
  function newEntity(type, position) {
    const yaw = ((Math.round(THREE.MathUtils.radToDeg(-camera.rotation.y) / 45) * 45 % 360) + 540) % 360 - 180;
    const vector = (name, value) => ({type: 'Vector3', name, value});
    const properties = {
      Target: () => [vector('position', position), vector('angles', [yaw, 0, 0]), {type: 'String32', name: 'name', value: uniqueName('target')}],
      Effect: () => [vector('position', position), {type: 'String64', name: 'effectName', value: 'common/meshes/concrete/concrete_tile_64x64'}],
      Pickup: () => [vector('position', position), {type: 'UInt8', name: 'pickupType', value: 40}],
      PointLight: () => [vector('position', position), {type: 'ColourXRGB32', name: 'color', value: 'ffffc400'},
        {type: 'Float', name: 'nearAttenuation', value: 32}, {type: 'Float', name: 'farAttenuation', value: 160}, {type: 'Float', name: 'intensity', value: 1.5}],
      PlayerSpawn: () => [vector('position', position), vector('angles', [yaw, 0, 0])],
    }[type];
    return {kind: 'entity', type, properties: properties ? properties() : []};
  }
  // The box a create drag has drawn: its footprint on the surface, grown out of it by four grid steps.
  function drawnBox(drag) {
    const min = [0, 0, 0], max = [0, 0, 0], depth = settings.grid * 4;
    for (let axis = 0; axis < 3; axis++) {
      if (axis === drag.axis) {
        const far = drag.level + drag.sign * depth;
        min[axis] = Math.min(drag.level, far); max[axis] = Math.max(drag.level, far);
      } else { min[axis] = Math.min(drag.start[axis], drag.end[axis]); max[axis] = Math.max(drag.start[axis], drag.end[axis]); }
    }
    return {min, max, empty: [0, 1, 2].some(axis => axis !== drag.axis && max[axis] - min[axis] < 1e-6)};
  }
  function startCreate(event) {
    const hit = pick(event, false), mouse = ray(event);
    let point, normal;
    if (hit) ({point, normal} = hit);
    else {
      // Nothing under the mouse: the ground plane, y = 0.
      point = onPlane(mouse, 1, 0);
      if (!point) return;
      normal = [0, 1, 0];
    }
    if (createType >= 4) { drag = {kind: 'place', point, normal}; return; }
    const axis = normal.map(Math.abs).indexOf(Math.max(...normal.map(Math.abs))), sign = Math.sign(normal[axis]) || 1;
    const level = Math.abs(point[axis] - snap(point[axis])) < .01 ? snap(point[axis]) : point[axis];
    const start = point.map((p, i) => i === axis ? level : snap(p));
    drag = {kind: 'create', axis, sign, level, start, end: start};
  }

  let drag = null;
  function startDrag(event) {
    if (bridging) { makeBridge(); drag = {kind: 'done'}; return; }  // The click is the bridge's, not a selection.
    if (vertexMode) {
      const corner = nearestDot(event);
      if (corner) { drag = {kind: 'corner', ...corner, offset: [0, 0, 0], result: null}; return; }
    }
    if (createType) { startCreate(event); return; }
    const hit = pick(event);
    if (!hit) return;
    const entry = hit.entity || hit.entry;
    if (entry.path.length) return;
    const item = hit.entity ? hit.entity.entity : hit.entry.source;
    if (event.shiftKey) {
      // Push or pull the face under the mouse of a selected brush along its normal.
      if (hit.entity || !selected.has(item) || !B.convex(item)) return;
      const plane = B.planesOf(item).find(candidate => candidate.face === hit.face);
      if (plane) drag = {kind: 'face', item, plane, point: hit.point, offset: 0, result: item};
      return;
    }
    if (adding(event)) return;
    if (!selected.has(item)) { selected.clear(); selected.add(item); showSelection(); }
    drag = {kind: 'move', point: hit.point, offset: [0, 0, 0]};
  }
  function continueDrag(event) {
    const grid = settings.grid;
    if (drag.kind === 'place') return;
    if (drag.kind === 'move') {
      const offset = gridOffset(event, drag.point);
      if (!offset) return;
      drag.offset = offset.map(x => x + 0);
      selection.position.set(...drag.offset);
      say(`Move ${drag.offset.join(' ')}`);
      return;
    }
    if (drag.kind === 'corner') {
      const offset = gridOffset(event, drag.position);
      if (!offset) return;
      // The corner lands on the grid along the axes it moves on.
      const position = drag.position.map((p, i) => (event.altKey ? i === 1 : i !== 1) ? snap(p + offset[i]) : p);
      const result = moveCorner(drag.item, drag.index, position);
      if (!result) return;
      drag.result = result; drag.to = position;
      showSelection(new Map([[drag.item, result]]));
      showHover(position);
      say(`Corner to ${position.map(v => Math.round(v * 1000) / 1000).join(' ')}`);
      return;
    }
    if (drag.kind === 'create') {
      const at = onPlane(ray(event), drag.axis, drag.level);
      if (!at) return;
      drag.end = at.map((p, i) => i === drag.axis ? drag.level : snap(p));
      const {min, max, empty} = drawnBox(drag);
      ghost.clear();
      if (!empty) {
        const box = new THREE.Box3Helper(new THREE.Box3(new THREE.Vector3(...min), new THREE.Vector3(...max)), 0xf0c674);
        box.material.depthTest = false;
        ghost.add(box);
        say(`${CREATE[createType].label}: ${max.map((v, i) => Math.round((v - min[i]) * 1000) / 1000).join(' × ')}`);
      }
      return;
    }
    // A face square to an axis lands on the grid; a slanted one moves by whole grid steps.
    const {plane} = drag, along = alongLine(drag.point, plane.normal, ray(event)), square = plane.normal.some(n => Math.abs(Math.abs(n) - 1) < 1e-6);
    const distance = square ? Math.round((plane.distance + along) / grid) * grid : plane.distance + Math.round(along / grid) * grid;
    if (distance === plane.distance + drag.offset) return;
    const result = B.fromPlanes(B.planesOf(drag.item).map(candidate => candidate === plane || candidate.face === plane.face ? {...candidate, distance} : candidate));
    if (!result) return;  // Pushed through the brush: keep the last shape that was one.
    drag.offset = distance - plane.distance;
    drag.result = result;
    showSelection(new Map([[drag.item, result]]));
    say(`Face ${drag.offset > 0 ? '+' : ''}${Math.round(drag.offset * 1000) / 1000}`);
  }
  // Ends a drag; true when it made an edit. `still` says the mouse did not move: a click.
  function endDrag(still) {
    const done = drag;
    drag = null;
    if (done.kind === 'done') return true;
    selection.position.set(0, 0, 0);
    ghost.clear();
    if (done.kind === 'move' && done.offset.some(Boolean)) {
      replace(new Map([...selected].map(item => [item, [isBrush(item) ? B.translate(item, done.offset) : withPosition(item, positionOf(item).map((v, i) => v + done.offset[i]))]])), [], 'changed');
      say(`Moved ${done.offset.join(' ')}`);
      return true;
    }
    if (done.kind === 'face' && done.offset) {
      replace(new Map([[done.item, [done.result]]]), [], 'changed');
      say(`Face moved ${Math.round(done.offset * 1000) / 1000}`);
      return true;
    }
    if (done.kind === 'corner' && done.result) {
      const kept = [...selected].filter(item => item !== done.item);
      replace(new Map([[done.item, [done.result]]]), [], 'changed');
      kept.forEach(item => selected.add(item));
      showSelection();
      say(`Corner moved${B.check(done.result).length ? ` · the brush now ${problemText(B.check(done.result))}` : ''}`);
      return true;
    }
    if (done.kind === 'create') {
      const {min, max, empty} = drawnBox(done);
      if (empty) { showSelection(); return still ? false : true; }
      const type = CREATE[createType].type;
      if (!type) {
        replace(new Map(), [{at: 'world', things: [B.box(min, max, template)]}], 'added');
        say(`New brush ${max.map((v, i) => v - min[i]).join(' × ')}`);
      } else {
        // A volume: its entity, followed by the brush that is the volume (no material, as the stock maps have them).
        const volume = B.box(min, max, {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0, colour: '0x00000000', material: ''});
        replace(new Map(), [{at: 'end', things: [newEntity(type, null), volume]}], 'added');
        say(`New ${CREATE[createType].label} volume; give it a target to link it`);
      }
      return true;
    }
    if (done.kind === 'place' && still) {
      const type = CREATE[createType].type, entity = newEntity(type, placeAt(done.point, done.normal));
      replace(new Map(), [{at: 'end', things: [entity]}], 'added');
      say(`New ${describe(entity)} at ${positionOf(entity).join(' ')}`);
      return true;
    }
    showSelection();
    return false;
  }

  // ---- The bridge tool ----
  function startBridge() {
    if (!bridgeFrom) { say('Shift-click a face first; B then bridges it to the face you aim at'); return; }
    bridging = true; createType = 0; vertexMode = false;
    showHover(null);
    updateTools();
    say('Aim at the face to bridge to; the wheel sets the steps');
    if (mouseAt) aimBridge(mouseAt);
  }
  function stopBridge() { bridging = false; bridgePreview = null; ghost.clear(); updateTools(); say(''); }
  function aimBridge(event) {
    ghost.clear();
    bridgePreview = null;
    const hit = pick(event, false);
    if (!hit || hit.entry.path.length || (hit.entry.source === bridgeFrom.item && hit.face === bridgeFrom.face)) { say('Aim at the face to bridge to'); return; }
    const to = pickFace(hit);
    if (!to) return;
    if (to.points.length !== bridgeFrom.points.length) { say(`That face has ${to.points.length} corners, the picked one ${bridgeFrom.points.length}: a bridge needs as many`); return; }
    const brushes = B.bridge(bridgeFrom.points, bridgeFrom.normal, to.points, to.normal, segments, fieldsOf(bridgeFrom.face));
    if (!brushes) { say('These faces cannot be joined'); return; }
    bridgePreview = brushes;
    for (const brush of brushes) { const edges = edgesOf(brush); edges.material = bridgeEdges; ghost.add(edges); }
    const bent = brushes.filter(brush => !B.convex(brush)).length;
    say(`Bridge of ${segments} step${segments > 1 ? 's' : ''}${bent ? `; ${bent} bend slightly, as the game allows` : ''} · click makes it`);
  }
  const bridgeEdges = new THREE.LineBasicMaterial({color: 0x3fc8ff, depthTest: false, transparent: true});
  function makeBridge() {
    if (!bridgePreview) return;
    const brushes = bridgePreview;
    bridging = false; bridgePreview = null; bridgeFrom = null; ghost.clear();
    replace(new Map(), [{at: 'world', things: brushes}], 'added');
    say(`Bridged with ${brushes.length} brush${brushes.length > 1 ? 'es' : ''}`);
  }

  // ---- The property panel (N, the game's me_showproperties) ----
  // What the stock maps give each type, for adding: [type, name, default]. Names an entity of the map has are added too.
  const SCHEMA = {
    WorldSpawn: [['String256', 'title', ''], ['String256', 'ownerString', ''], ['String32', 'targetGameOverCamera', ''], ['UInt8', 'playersMin', 1], ['UInt8', 'playersMax', 16],
      ['Bool8', 'modeFFA', 1], ['Bool8', 'mode1v1', 1], ['Bool8', 'mode2v2', 1], ['Bool8', 'modeTDM', 1], ['Bool8', 'modeCTF', 0], ['Bool8', 'modeRace', 0],
      ['ColourXRGB32', 'fogColor', 'ff000000'], ['Float', 'fogDistanceStart', 0], ['Float', 'fogDistanceEnd', 0], ['ColourXRGB32', 'sky.horizonColor', 'ff000000'],
      ['ColourXRGB32', 'sky.skyTopColor', 'ff000000'], ['ColourXRGB32', 'sky.sunColor', 'ffffffff'], ['Float', 'sky.timeOfDay', 12]],
    Teleporter: [['String32', 'target', ''], ['String32', 'linkOutOnUsed', '']],
    JumpPad: [['String32', 'target', '']],
    Target: [['Vector3', 'position', [0, 0, 0]], ['Vector3', 'angles', [0, 0, 0]], ['String32', 'name', ''], ['String32', 'nameNext', ''], ['Float', 'speed', 160]],
    Pickup: [['Vector3', 'position', [0, 0, 0]], ['UInt8', 'pickupType', 40], ['Vector3', 'angles', [0, 0, 0]], ['UInt8', 'tokenIndex', 1], ['String32', 'linkOutOnPickedUp', '']],
    PlayerSpawn: [['Vector3', 'position', [0, 0, 0]], ['Vector3', 'angles', [0, 0, 0]], ['Bool8', 'teamA', 0], ['Bool8', 'teamB', 0], ['Bool8', 'initialSpawn', 0],
      ['Bool8', 'modeFFA', 0], ['Bool8', 'mode1v1', 0], ['Bool8', 'modeTDM', 0], ['Bool8', 'modeCTF', 0], ['Bool8', 'modeRace', 0]],
    PointLight: [['Vector3', 'position', [0, 0, 0]], ['ColourXRGB32', 'color', 'ffffc400'], ['Float', 'nearAttenuation', 32], ['Float', 'farAttenuation', 160], ['Float', 'intensity', 1.5]],
    Effect: [['Vector3', 'position', [0, 0, 0]], ['String64', 'effectName', ''], ['Float', 'effectScale', 1], ['Vector3', 'angles', [0, 0, 0]],
      ['String256', 'material0Name', ''], ['ColourARGB32', 'material0Albedo', 'ffffffff']],
  };
  // pickupType: the numbers reflex-map's converter names; 20, 70, 71 and 80 are guesses from where the stock maps use them.
  const PICKUPS = {1: 'Shotgun', 2: 'Grenade launcher', 3: 'Plasma rifle', 4: 'Rocket launcher', 5: 'Ion cannon', 6: 'Bolt rifle', 7: 'Stake gun',
    20: 'Burst gun ammo (guess)', 21: 'Shotgun ammo', 22: 'Grenades', 23: 'Plasma ammo', 24: 'Rockets', 25: 'Ion ammo', 26: 'Bolts',
    40: 'Health 5', 41: 'Health 25', 42: 'Health 50', 43: 'Mega health', 50: 'Armour shard', 51: 'Light armour', 52: 'Medium armour', 53: 'Heavy armour',
    60: 'Carnage (quad damage)', 70: 'Flag, team A (guess)', 71: 'Flag, team B (guess)', 80: 'Race token (guess)'};
  const NAMES_OF = {target: 'Target', nameNext: 'Target', targetGameOverCamera: 'Target'};
  let panelOpen = false;
  // The entity the panel shows: the selected one, the entity a selected volume belongs to, or with nothing selected
  // the map's WorldSpawn.
  // A volume and its entity are one thing here: a teleporter just made selects both.
  function panelTarget() {
    if (!selected.size) return worldSpawn() || null;
    const subjects = new Set([...selected].map(item => isBrush(item) && M.isVolume(ownerOf(item)) ? ownerOf(item) : item));
    if (subjects.size > 1) return {many: subjects.size};
    const [subject] = subjects;
    return isBrush(subject) ? {brush: subject} : subject;
  }
  function element(tag, properties = {}, ...children) {
    const made = Object.assign(document.createElement(tag), properties);
    made.append(...children);
    return made;
  }
  // Suggestions for a text property: the map's Target names, effects or materials.
  function suggestions(name) {
    if (NAMES_OF[name]) return flat.entities.filter(entry => entry.entity.type === 'Target').map(entry => M.property(entry.entity, 'name')).filter(Boolean);
    if (name === 'effectName') return flat.entities.filter(entry => entry.entity.type === 'Effect').map(entry => M.property(entry.entity, 'effectName')).filter(Boolean);
    if (/^material\dName$/.test(name)) return Object.keys(packColours);
    return [];
  }
  // Writes one property (or removes it when `value` is undefined) as a step of undo, keeping the selection.
  function setProperty(entity, index, value) {
    const properties = value === undefined ? entity.properties.filter((_, i) => i !== index)
      : index >= entity.properties.length ? [...entity.properties, value] : entity.properties.map((p, i) => i === index ? {...p, value} : p);
    const fresh = {...entity, properties}, keep = [...selected].map(item => item === entity ? fresh : item);
    replace(new Map([[entity, [fresh]]]), [], keep);
  }
  function input(property, index, entity) {
    const {type, name, value} = property, change = next => setProperty(entity, index, next);
    const number = (current, step, apply) => element('input', {type: 'number', step, value: String(current), onchange: event => { const read = Number(event.target.value); if (Number.isFinite(read)) apply(read); }});
    if (type === 'Vector3') return element('span', {className: 'vector'}, ...value.map((v, axis) => number(v, 'any', read => change(value.map((old, i) => i === axis ? read : old)))));
    if (type === 'Float') return number(value, 'any', change);
    if (type === 'Bool8') return element('input', {type: 'checkbox', checked: !!value, onchange: event => change(event.target.checked ? 1 : 0)});
    if (name === 'pickupType') {
      const list = {...PICKUPS};
      if (!(value in list)) list[value] = `Type ${value}`;
      return element('select', {onchange: event => change(Number(event.target.value))},
        ...Object.entries(list).map(([n, label]) => element('option', {value: n, selected: Number(n) === value, textContent: `${n} · ${label}`})));
    }
    if (/^U?Int\d+$/.test(type)) return number(value, 1, read => change(Math.max(0, Math.round(read))));
    if (/^Colour/.test(type)) {
      const hex = String(value).padStart(8, '0').slice(-8), text = element('input', {type: 'text', value: hex, maxLength: 8, className: 'hex',
        onchange: event => { if (/^[0-9a-fA-F]{8}$/.test(event.target.value)) change(event.target.value.toLowerCase()); }});
      return element('span', {className: 'colour'}, element('input', {type: 'color', value: '#' + hex.slice(2), onchange: event => change(hex.slice(0, 2) + event.target.value.slice(1))}), text);
    }
    // Strings: an empty one is removed rather than written empty.
    const length = Number((/\d+$/.exec(type) || [256])[0]), options = [...new Set(suggestions(name))].sort();
    const field = element('input', {type: 'text', value, maxLength: length, onchange: event => change(event.target.value === '' ? undefined : event.target.value)});
    if (options.length) { field.setAttribute('list', 'list-' + name); return element('span', {}, field, element('datalist', {id: 'list-' + name}, ...options.map(o => element('option', {value: o})))); }
    return field;
  }
  function showPanel() {
    const panel = $('props');
    panel.hidden = !panelOpen || !editing;
    if (panel.hidden || !flat) return;
    const body = $('propsBody'), target = panelTarget();
    body.replaceChildren();
    if (!target) { body.textContent = 'This map has no WorldSpawn.'; return; }
    if (target.many) { body.textContent = `${target.many} selected: select one entity to see its properties.`; return; }
    if (target.brush) {
      const materials = [...new Set(target.brush.faces.map(face => face.material || 'no material'))];
      body.append(element('p', {textContent: `A brush of the world: ${target.brush.faces.length} faces, ${target.brush.vertices.length} corners; ${materials.join(', ')}. Brushes have no properties; K and M set their material.`}));
      return;
    }
    const entity = target;
    body.append(element('h3', {textContent: entity.type === 'WorldSpawn' && !selected.size ? 'WorldSpawn (the map)' : entity.type}));
    const rows = element('div', {className: 'rows'});
    entity.properties.forEach((property, index) => rows.append(element('label', {title: `${property.type} ${property.name}`},
      element('span', {textContent: property.name}), input(property, index, entity),
      element('button', {type: 'button', textContent: '×', title: `Remove ${property.name}`, onclick: () => setProperty(entity, index, undefined)}))));
    body.append(rows);
    // Adding: what the stock maps give this type, and what entities of this map of the type have, not yet set.
    const seen = new Map((SCHEMA[entity.type] || []).map(([type, name, value]) => [name, {type, name, value}]));
    for (const entry of flat.entities) if (entry.entity.type === entity.type) for (const p of entry.entity.properties) if (!seen.has(p.name)) seen.set(p.name, {...p});
    const missing = [...seen.values()].filter(p => !entity.properties.some(own => own.name === p.name));
    if (missing.length) {
      const choose = element('select', {}, ...missing.map((p, i) => element('option', {value: i, textContent: `${p.name} (${p.type})`})));
      body.append(element('div', {className: 'add'}, choose, element('button', {type: 'button', textContent: 'Add', onclick: () => {
        const p = missing[Number(choose.value)];
        // A string starts as the first suggestion (a Target's name for a target), else its own name, never empty.
        const value = Array.isArray(p.value) ? [...p.value] : p.value === '' ? suggestions(p.name)[0] || p.name : p.value;
        setProperty(entity, entity.properties.length, {type: p.type, name: p.name, value});
      }})));
    }
    if (M.VOLUMES.has(entity.type) && !M.property(entity, 'target') && /Teleporter|JumpPad/.test(entity.type)) body.append(element('p', {className: 'hint', textContent: 'Set target to the name of a Target to link it.'}));
  }
  function togglePanel() { panelOpen = !panelOpen; showPanel(); say(panelOpen ? 'Properties: N closes' : ''); }

  // K and M: the game's me_getmaterial and me_setmaterial.
  let mouseAt = null;
  function pickMaterial() {
    const hit = mouseAt && pick(mouseAt, false);
    if (!hit) return;
    template = fieldsOf(hit.face);
    updateTools();
    say(`Picked ${template.material || 'no material'}${M.colourOf(template) ? ' · ' + template.colour : ''}`);
  }
  function applyMaterial(oneFace) {
    const paint = face => ({...face, material: template.material, colour: template.colour});
    if (oneFace) {
      const hit = mouseAt && pick(mouseAt, false);
      if (!hit || hit.entry.path.length) return;
      const item = hit.entry.source;
      replace(new Map([[item, [{vertices: item.vertices, faces: item.faces.map(face => face === hit.face ? paint(face) : face)}]]]), [], selected.has(item) ? 'changed' : null);
      say(`Material on one face: ${template.material || 'no material'}`);
      return;
    }
    const items = selectedBrushes();
    if (!items.length) return;
    replace(new Map(items.map(item => [item, [{vertices: item.vertices, faces: item.faces.map(paint)}]])), [], 'changed');
    say(`Material on ${items.length} brushes: ${template.material || 'no material'}`);
  }

  // The toolbar's operations, each one step of undo. CSG works on brushes of the world, not on volumes.
  function others(of) { return globalGroup().items.filter(item => isWorldBrush(item) && !of.includes(item) && B.convex(item)); }
  const actions = {
    newBrush() {
      const forward = new THREE.Vector3();
      camera.getWorldDirection(forward);
      const at = camera.position.clone().addScaledVector(forward, 256);
      const size = settings.grid * 8, centre = [snap(at.x), snap(at.y), snap(-at.z)];
      replace(new Map(), [{at: 'world', things: [B.box(centre.map(c => c - size / 2), centre.map(c => c + size / 2), template)]}], 'added');
      say(`New ${size}-unit box`);
    },
    subtract() {
      const cutters = selectedBrushes(), changes = new Map();
      for (const item of others(cutters)) for (const cutter of cutters) {
        const current = changes.get(item) || [item];
        const pieces = current.flatMap(piece => B.subtract(piece, cutter));
        if (pieces.length !== current.length || pieces.some((piece, index) => piece !== current[index])) changes.set(item, pieces);
      }
      if (!changes.size) { say('The selection touches no other brush'); return; }
      const cut = changes.size;
      replace(changes, [], cutters);
      say(`Carved ${cut} brushes`);
    },
    hollow() {
      const [item] = selectedBrushes(), walls = B.hollow(item, settings.grid);
      if (walls.length === 1 && walls[0] === item) { say(`Too small to hollow with ${settings.grid}-unit walls`); return; }
      replace(new Map([[item, walls]]));
      say(`Hollowed into ${walls.length} walls`);
    },
    merge() {
      const [first, ...rest] = selectedBrushes();
      let joined = first;
      for (const next of rest) joined = joined && B.merge(joined, next);
      if (!joined) { say('These brushes do not make one convex brush together'); return; }
      replace(new Map([[first, [joined]], ...rest.map(item => [item, []])]), [], 'changed');
      say('Merged');
    },
    clip() {
      // The grid plane through the point clicked last, across the axis the camera faces most.
      const forward = new THREE.Vector3();
      camera.getWorldDirection(forward);
      const facing = [forward.x, forward.y, -forward.z], axis = facing.map(Math.abs).indexOf(Math.max(...facing.map(Math.abs)));
      const normal = [0, 0, 0];
      normal[axis] = 1;
      const plane = {normal, distance: snap(lastPoint[axis])}, changes = new Map();
      for (const item of selectedBrushes()) {
        const {back, front} = B.split(item, plane, template);
        if (back && front) changes.set(item, [back, front]);
      }
      if (!changes.size) { say(`The plane ${'xyz'[axis]} = ${plane.distance} does not cut the selection`); return; }
      replace(changes, [], null);
      say(`Split at ${'xyz'[axis]} = ${plane.distance}`);
    },
    // Clones one grid step along x: a brush beside its original (so with the same owner), a volume as a new entity
    // with its brush, an entity after its original.
    duplicate() {
      const step = [settings.grid, 0, 0], inserts = [];
      for (const item of selected) {
        if (isBrush(item)) {
          const owner = ownerOf(item);
          if (M.isVolume(owner)) inserts.push({at: 'end', things: [{...owner, properties: owner.properties.map(p => ({...p}))}, B.translate(item, step)]});
          else inserts.push({at: item, things: [B.translate(item, step)]});
        } else if (!M.VOLUMES.has(item.type)) {
          const position = positionOf(item);
          inserts.push({at: item, things: [position ? withPosition(item, position.map((v, i) => v + step[i])) : {...item}]});
        }
      }
      if (!inserts.length) return;
      replace(new Map(), inserts, 'added');
      say(`Cloned ${inserts.length}`);
    },
    // Deletes the selection; a volume's entity goes with the last of its brushes, and a volume entity takes its brushes.
    delete() {
      const gone = new Set(selected);
      for (const entry of flat.brushes) {
        if (entry.path.length || !M.isVolume(entry.owner)) continue;
        if (gone.has(entry.owner)) gone.add(entry.source);
      }
      for (const item of selectedBrushes()) {
        const owner = ownerOf(item);
        if (M.isVolume(owner) && flat.brushes.every(entry => entry.owner !== owner || entry.path.length || gone.has(entry.source))) gone.add(owner);
      }
      replace(new Map([...gone].map(item => [item, []])));
      say(`Deleted ${gone.size}`);
    },
    undo() { restore(undoStack, redoStack); },
    redo() { restore(redoStack, undoStack); },
    save() {
      const blob = new Blob([M.write(map)], {type: 'text/plain'}), link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = /\.map$/i.test(mapName) ? mapName : `${mapName || 'untitled'}.map`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    },
  };
  function move(offset) {
    if (selected.size) replace(new Map([...selected].map(item => [item, [isBrush(item) ? B.translate(item, offset) : withPosition(item, (positionOf(item) || [0, 0, 0]).map((v, i) => v + offset[i]))]])), [], 'changed');
  }
  for (const [id, action] of Object.entries(actions)) $(id).addEventListener('click', event => { action(); event.target.blur(); });
  $('propsButton').addEventListener('click', event => { togglePanel(); event.target.blur(); });
  $('propsClose').addEventListener('click', () => { panelOpen = false; showPanel(); });

  function setCreate(type) {
    createType = createType === type ? 0 : type;
    if (createType) { vertexMode = false; bridging = false; bridgePreview = null; ghost.clear(); }
    showHover(null);
    updateTools();
    say(createType ? `${CREATE[createType].label}: ${createType <= 3 ? 'drag a rectangle on a surface' : 'click where it goes'}` : '');
  }
  function setVertexMode(on) {
    vertexMode = on;
    if (on) { createType = 0; bridging = false; bridgePreview = null; ghost.clear(); }
    showHover(null);
    showSelection();
    say(on ? (selectedBrushes().length ? 'Drag a corner; Alt drags it up and down' : 'Select a brush to see its corners') : '');
  }
  function setEditing(on) {
    editing = on;
    $('tools').hidden = !on;
    clips.visible = volumes.visible = selection.visible = on;
    for (const mesh of entityLayer.children) if (mesh.userData.editOnly) mesh.visible = on;
    uniforms.uGrid.value = on ? settings.grid : 0;
    $('crosshair').hidden = on;
    if (!on) { createType = 0; vertexMode = false; bridging = false; bridgePreview = null; showHover(null); ghost.clear(); }
    if (on && document.pointerLockElement === canvas) document.exitPointerLock();
    $('help').textContent = on
      ? '0 fly · Click select (Ctrl adds) · Drag move (Alt up/down) · Shift-drag face · Shift-click face, B bridge · 1–8 create · V vertices · N properties · Right-drag look · WASD QE · G clone · Backspace delete · Z/X undo/redo · K/M material'
      : '0 edit · Click to capture / drag to look · WASD move · Space up · Shift down · Wheel speed · Esc release · 1–9 viewpoints';
    updateTools();
    applySettings();  // The toolbar changes the scene's height; reading it lays the page out now, not a frame later.
  }

  // ---- View ----
  function applySettings() {
    for (const id of ['invertX', 'invertY']) $(id).checked = settings[id];
    $('fov').value = settings.fov;
    $('grid').value = String(settings.grid);
    const main = canvas.parentElement, aspect = main.clientWidth / Math.max(main.clientHeight, 1);
    renderer.setSize(main.clientWidth, main.clientHeight, false);
    camera.aspect = aspect;
    // The game's field of view is horizontal; Three's is vertical.
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2) / aspect));
    camera.updateProjectionMatrix();
  }
  $('fov').addEventListener('change', event => { settings.fov = Math.max(30, Math.min(130, Number(event.target.value) || 100)); save(); applySettings(); });
  for (const id of ['invertX', 'invertY']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
  $('grid').addEventListener('change', event => { settings.grid = Number(event.target.value); event.target.blur(); save(); setEditing(editing); updateTools(); });
  $('reset').addEventListener('click', () => { speed = 400; showViewpoint(0); if (flat) showReady(); });
  new ResizeObserver(applySettings).observe(canvas.parentElement);

  // Flying: a click captures the mouse (dragging also looks). Editing: a click selects, the right button looks.
  const keys = new Set();
  let looking = false, downAt = null;
  canvas.addEventListener('contextmenu', event => event.preventDefault());
  canvas.addEventListener('mousedown', event => {
    if (editing) {
      if (event.button === 2) looking = true;
      else if (event.button === 0 && flat) { downAt = [event.clientX, event.clientY]; startDrag(event); }
      return;
    }
    looking = true;
    try { Promise.resolve(canvas.requestPointerLock()).catch(() => { /* Drag-to-look still works. */ }); } catch (_) { /* Same. */ }
  });
  window.addEventListener('mouseup', event => {
    const still = downAt && Math.hypot(event.clientX - downAt[0], event.clientY - downAt[1]) < 5;
    const dragged = editing && event.button === 0 && drag ? endDrag(still) : false;
    if (editing && event.button === 0 && still && !dragged && !createType && flat) select(event);
    if (event.button === 2 || !editing) looking = false;
    downAt = null;
  });
  window.addEventListener('blur', () => { looking = false; keys.clear(); });
  document.addEventListener('mousemove', event => {
    if (event.target === canvas) mouseAt = {clientX: event.clientX, clientY: event.clientY};
    if (drag && editing) { continueDrag(event); return; }
    if (editing && vertexMode && !looking && event.target === canvas) { const corner = nearestDot(event); showHover(corner && corner.position); }
    if (editing && bridging && !looking && event.target === canvas) aimBridge(event);
    if (!looking && document.pointerLockElement !== canvas) return;
    camera.rotation.y -= event.movementX * .0025 * (settings.invertX ? -1 : 1);
    camera.rotation.x = Math.max(-1.55, Math.min(1.55, camera.rotation.x - event.movementY * .0025 * (settings.invertY ? -1 : 1)));
  });
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    if (editing && bridging) {
      segments = Math.max(1, Math.min(32, segments + (event.deltaY < 0 ? 1 : -1)));
      updateTools();
      if (mouseAt) aimBridge(mouseAt);
      return;
    }
    speed = Math.max(20, Math.min(20000, speed * (event.deltaY < 0 ? 1.2 : 1 / 1.2)));
    if (flat) showReady();
  }, {passive: false});
  document.addEventListener('keydown', event => {
    if (event.target.matches('input:not([type=checkbox]), select')) return;
    const ctrl = event.ctrlKey || event.metaKey, key = event.key.toLowerCase();
    if (event.code === 'Tab' || (event.code === 'Digit0' && !ctrl)) { event.preventDefault(); setEditing(!editing); return; }
    if (/^Digit[1-9]$/.test(event.code) && !ctrl && !editing) showViewpoint(Number(event.code.slice(5)) - 1);
    if (editing && flat) {
      if (/^Digit[1-8]$/.test(event.code) && !ctrl) { setCreate(Number(event.code.slice(5))); return; }
      if (!ctrl && event.code === 'KeyV') { setVertexMode(!vertexMode); return; }
      if (ctrl && key === 'z') { event.preventDefault(); actions[event.shiftKey ? 'redo' : 'undo'](); return; }
      if (ctrl && key === 'y') { event.preventDefault(); actions.redo(); return; }
      if (ctrl && event.shiftKey && key === 's') { event.preventDefault(); if (!$('subtract').disabled) actions.subtract(); return; }
      if (ctrl && key === 's') { event.preventDefault(); actions.save(); return; }
      if (ctrl && key === 'd') { event.preventDefault(); if (selected.size) actions.duplicate(); return; }
      if (ctrl && key === 'm') { event.preventDefault(); if (!$('merge').disabled) actions.merge(); return; }
      if (!ctrl && event.code === 'KeyZ') { actions.undo(); return; }
      if (!ctrl && event.code === 'KeyX') { actions.redo(); return; }
      if (!ctrl && event.code === 'KeyG') { if (selected.size) actions.duplicate(); return; }
      if (!ctrl && event.code === 'KeyK') { pickMaterial(); return; }
      if (!ctrl && event.code === 'KeyM') { applyMaterial(event.shiftKey); return; }
      if (!ctrl && key === 'b') { if (bridging) stopBridge(); else startBridge(); return; }
      if (!ctrl && event.code === 'KeyN') { togglePanel(); return; }
      if (!ctrl && key === 'h' && !$('hollow').disabled) { actions.hollow(); return; }
      if (!ctrl && key === 'c' && !$('clip').disabled) { actions.clip(); return; }
      if ((event.code === 'Delete' || event.code === 'Backspace') && selected.size) { event.preventDefault(); actions.delete(); return; }
      if (event.code === 'Escape') {
        if (drag) { drag = null; selection.position.set(0, 0, 0); ghost.clear(); showSelection(); return; }
        if (bridging) { stopBridge(); return; }
        if (bridgeFrom) { bridgeFrom = null; showSelection(); say(''); return; }
        if (createType) { setCreate(createType); return; }
        if (vertexMode) { setVertexMode(false); return; }
        selected.clear(); say(''); showSelection(); return;
      }
      // Arrows move the selection along the horizontal axis nearest to where the camera looks.
      const step = settings.grid, yaw = -camera.rotation.y, along = Math.round(yaw / (Math.PI / 2)) & 3;
      const ahead = [[0, 0, 1], [1, 0, 0], [0, 0, -1], [-1, 0, 0]][along], side = [[1, 0, 0], [0, 0, -1], [-1, 0, 0], [0, 0, 1]][along];
      const arrows = {ArrowUp: ahead, ArrowDown: ahead.map(x => -x), ArrowRight: side, ArrowLeft: side.map(x => -x), PageUp: [0, 1, 0], PageDown: [0, -1, 0]};
      if (arrows[event.code] && selected.size) { event.preventDefault(); move(arrows[event.code].map(x => x * step + 0)); return; }
    }
    if (/^(Key[WASDQE]|Space|Shift(Left|Right))$/.test(event.code) && !ctrl) { keys.add(event.code); if (event.code === 'Space') event.preventDefault(); }
  });
  document.addEventListener('keyup', event => keys.delete(event.code));

  const clock = new THREE.Clock(), forward = new THREE.Vector3(), right = new THREE.Vector3(), step = new THREE.Vector3();
  renderer.setAnimationLoop(() => {
    const delta = Math.min(clock.getDelta(), .1), held = code => Number(keys.has(code));
    camera.getWorldDirection(forward);
    right.crossVectors(forward, camera.up).normalize();
    step.copy(forward).multiplyScalar(held('KeyW') - held('KeyS')).addScaledVector(right, held('KeyD') - held('KeyA'));
    step.y += held('Space') + held('KeyE') - held('KeyQ') - (editing ? 0 : held('ShiftLeft') + held('ShiftRight'));
    if (step.lengthSq()) camera.position.addScaledVector(step.normalize(), speed * delta);
    renderer.render(scene, camera);
  });
  window.skinnerReflexMaps = {renderer, scene, camera, root, get map() { return map; }, get flat() { return flat; }, get template() { return template; },
    get createType() { return createType; }, get vertexMode() { return vertexMode; }, get bridging() { return bridging; }, get segments() { return segments; }, load, actions, selected, setEditing, showViewpoint};

  // ---- Loading ----
  $('open').addEventListener('change', async event => {
    const file = event.target.files[0];
    if (!file) return;
    try { load(await file.text(), file.name.replace(/\.map$/i, '')); history.replaceState(null, '', location.pathname); } catch (error) { showStatus(`${file.name} could not load: ${error.message}`); }
    event.target.value = '';
    event.target.blur();
  });
  $('map').addEventListener('change', event => { location.search = event.target.value ? '?map=' + encodeURIComponent(event.target.value) : ''; });
  $('import').addEventListener('click', async () => {
    const game = $('gamePath').value.trim();
    if (!game) { $('importStatus').textContent = 'Enter your Reflex Arena folder.'; return; }
    $('import').disabled = true;
    $('importStatus').textContent = 'Importing maps…';
    try {
      const response = await fetch('/import_reflex_maps', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({game, replace: $('replace').checked})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      try { localStorage.setItem(storageKey + '.game', game); } catch (_) { /* Path is simply not remembered. */ }
      const failed = Object.entries(result.failed).map(([name, reason]) => `${name}: ${reason}`);
      $('importStatus').textContent = `Imported ${result.imported.length}, skipped ${result.skipped.length} already imported` +
        (failed.length ? `, failed ${failed.length} (${failed.join('; ')})` : '') + `. ${result.materials} material colours read.`;
      if (result.imported.length && !failed.length && !map) location.reload();
    } catch (error) { $('importStatus').textContent = error.message; }
    finally { $('import').disabled = false; }
  });
  try { $('gamePath').value = localStorage.getItem(storageKey + '.game') || ''; } catch (_) { /* Field stays empty. */ }

  applySettings();
  setEditing(false);
  let maps = [];
  try { maps = await (await fetch(data + 'index.json')).json(); } catch (_) { /* No pack yet, or the page is served without Skinner. */ }
  try { packColours = await (await fetch(data + 'materials.json')).json() || {}; } catch (_) { /* Colours stay guessed. */ }
  if (!Array.isArray(maps)) maps = [];
  const byGroup = new Map();
  for (const item of maps) byGroup.set(item.group, [...(byGroup.get(item.group) || []), item]);
  $('map').replaceChildren(new Option(maps.length ? 'New empty map' : 'No maps imported (Import maps, or open a file)', ''), ...[...byGroup].map(([group, items]) => {
    const element = Object.assign(document.createElement('optgroup'), {label: group});
    element.append(...items.map(item => new Option(item.title && item.title !== item.name ? `${item.name} · ${item.title}` : item.name, item.id)));
    return element;
  }));
  const wanted = new URLSearchParams(location.search).get('map') || '', entry = maps.find(item => item.id === wanted);
  $('map').value = entry ? entry.id : '';
  try {
    if (entry) {
      showStatus(`Loading ${entry.name}…`);
      const response = await fetch(`${data}maps/${entry.file}`);
      if (!response.ok) throw new Error(`${entry.file} (${response.status})`);
      load(await response.text(), entry.name);
    } else {
      if (!maps.length) $('importPanel').open = true;
      load(M.write(M.empty()), 'untitled');
      setEditing(true);
      say('New map: B makes a box in front of the camera; 0 flies; Open .map file loads one from disk');
    }
  } catch (error) { showStatus('Map could not load: ' + error.message); }
});

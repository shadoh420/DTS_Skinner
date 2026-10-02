/* Free-flight viewer and brush editor for Reflex Arena maps (mapfile.js reads them, brush.js edits their brushes).
   The map file is drawn as it is: Reflex has no compile step. Coordinates stay the game's (y up, left-handed) under
   a root mirrored on z, so x right, y up and z forward appear as in the game; Three turns the front faces of a
   mirrored mesh round itself, so faces wound counter-clockwise from outside in the file face out here too.

   Tab switches between flying and editing, as the game's editor switches between playing and editing. While
   editing, a click selects a brush of the map (Shift adds), the right button looks, and the toolbar's CSG works on
   the selection. Brushes placed by a prefab are drawn but not edited: their prefab holds them. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const B = window.ReflexBrush, M = window.ReflexMap;
  const storageKey = 'skinner.reflexmaps';
  const settings = {fov: 100, invertX: false, invertY: false, grid: 8};
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
  let speed = 400, map = null, mapName = '', editing = false, flat = null, entryOfTriangle = [], clipEntryOfTriangle = [];
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
  const world = new THREE.Mesh(new THREE.BufferGeometry(), brushMaterial), clips = new THREE.Mesh(new THREE.BufferGeometry(), clipMaterial);
  const selection = new THREE.Group();
  clips.renderOrder = 1; selection.renderOrder = 2;
  root.add(world, clips, selection);

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
        if (wanted(face) === false) continue;
        const n = B.normalize(B.newell(face.indices.map(i => brush.vertices[i])));
        const {diffuse, specular} = faceShade(face);
        for (const triangle of B.triangles(brush, face, triangulate)) {
          for (const vertex of triangle) {
            positions.push(...brush.vertices[vertex]);
            normals.push(...n);
            colours.push(...diffuse);
            speculars.push(...specular);
          }
          owners.push(index);
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
  function buildEntities() {
    entityLayer.clear();
    const byKind = new Map();
    for (const {entity, position} of flat.entities) {
      const marker = position && markerOf(entity);
      if (!marker) continue;
      const key = marker.join('|');
      if (!byKind.has(key)) byKind.set(key, {marker, positions: []});
      byKind.get(key).positions.push(position);
    }
    const matrix = new THREE.Matrix4();
    for (const {marker: [colour, size], positions} of byKind.values()) {
      const mesh = new THREE.InstancedMesh(markerGeometry, new THREE.MeshBasicMaterial({color: colour}), positions.length);
      mesh.userData.editOnly = size <= 3;  // Effects: a map can have a thousand; they show while editing.
      positions.forEach((p, index) => mesh.setMatrixAt(index, matrix.makeScale(size, size * 1.6, size).setPosition(p[0], p[1], p[2])));
      entityLayer.add(mesh);
    }
  }

  // ---- The map ----
  const globalGroup = () => M.global(map);
  function rebuild() {
    flat = M.flatten(map);
    world.geometry.dispose(); clips.geometry.dispose();
    world.geometry = buildMesh(flat.brushes, face => !isClip(face), entryOfTriangle);
    clips.geometry = buildMesh(flat.brushes, face => isClip(face), clipEntryOfTriangle);
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
    if (flat) for (const {brush} of flat.brushes) for (const face of brush.faces) if (!M.colourOf(face) && !isClip(face) && !packColours[face.material || '']) guessed.add(face.material || 'no material');
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
  let lastPoint = null, lastNormal = null;
  // Fields a new brush takes: those of the selected face's brush if any, else plain concrete.
  let template = {u: 0, v: 0, scaleU: 1, scaleV: 1, rotation: 0, colour: '0x00000000', material: 'common/materials/stone/concrete'};

  function remember() {
    undoStack.push(globalGroup().items.slice());
    if (undoStack.length > 100) undoStack.shift();
    redoStack.length = 0;
  }
  function restore(from, to) {
    if (!from.length) return;
    to.push(globalGroup().items.slice());
    globalGroup().items = from.pop();
    for (const item of [...selected]) if (!globalGroup().items.includes(item)) selected.delete(item);
    rebuild(); showReady();
  }
  // Replaces brushes of the map: `changes` maps an old brush to the brushes standing in its place.
  function replace(changes, added = [], select = null) {
    remember();
    const items = [];
    for (const item of globalGroup().items) {
      if (!changes.has(item)) { items.push(item); continue; }
      items.push(...changes.get(item).map(brush => ({kind: 'brush', vertices: brush.vertices, faces: brush.faces})));
    }
    const fresh = added.map(brush => ({kind: 'brush', vertices: brush.vertices, faces: brush.faces}));
    items.push(...fresh);
    globalGroup().items = items;
    selected.clear();
    for (const item of select === 'added' ? fresh : select || []) if (items.includes(item)) selected.add(item);
    rebuild(); showReady();
  }
  const selectedBrushes = () => [...selected];
  const say = text => { $('selection').textContent = text; };

  function showSelection() {
    selection.clear();
    if (!flat) return;
    for (const entry of flat.brushes) {
      if (!selected.has(entry.source) || entry.path.length) continue;
      const geometry = buildMesh([entry], () => true, []);
      selection.add(new THREE.Mesh(geometry, selectedFill));
      const lines = [];
      for (const face of entry.brush.faces) face.indices.forEach((index, n) => lines.push(...entry.brush.vertices[index], ...entry.brush.vertices[face.indices[(n + 1) % face.indices.length]]));
      const edges = new THREE.BufferGeometry();
      edges.setAttribute('position', new THREE.Float32BufferAttribute(lines, 3));
      selection.add(new THREE.LineSegments(edges, selectedEdges));
    }
    updateTools();
  }
  function updateTools() {
    if (!flat) return;
    const count = selected.size, convex = selectedBrushes().every(B.convex);
    $('subtract').disabled = !count || !convex;
    $('hollow').disabled = count !== 1 || !convex;
    $('merge').disabled = count < 2 || !convex;
    $('clip').disabled = !count || !convex || !lastPoint;
    $('duplicate').disabled = $('delete').disabled = !count;
    $('undo').disabled = !undoStack.length; $('redo').disabled = !redoStack.length;
    if (count) say(`${count} selected · arrows / PgUp PgDn move by ${settings.grid}`);
  }

  // Picks the brush under the mouse. Placed prefab brushes are reported, not selected.
  const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
  function pick(event) {
    const rect = canvas.getBoundingClientRect();
    pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const targets = [world, clips];
    const hit = raycaster.intersectObjects(targets, false)[0];
    if (!hit) return null;
    const owners = hit.object === world ? entryOfTriangle : clipEntryOfTriangle, entry = flat.brushes[owners[hit.faceIndex]];
    const point = root.worldToLocal(hit.point.clone()).toArray();
    const normal = hit.face.normal.toArray();  // Geometry normals are in the game's axes already.
    return {entry, point, normal};
  }
  function select(event) {
    const hit = pick(event);
    if (!hit) { if (!event.shiftKey) selected.clear(); say(''); showSelection(); return; }
    lastPoint = hit.point; lastNormal = hit.normal;
    if (hit.entry.path.length) {
      say(`Part of prefab "${M.property(hit.entry.path[0], 'prefabName')}", placed by a Prefab entity; edit the prefab in Reflex`);
      if (!event.shiftKey) selected.clear();
      showSelection();
      return;
    }
    const item = hit.entry.source;
    if (event.shiftKey) { if (selected.has(item)) selected.delete(item); else selected.add(item); } else { selected.clear(); selected.add(item); }
    const face = item.faces.find(f => !isClip(f)) || item.faces[0];
    if (face) template = {...face, indices: undefined};
    delete template.indices;
    const problems = B.check(item);
    say(`${selected.size} selected${problems.length ? ` · this brush is ${problems.join(', ')}; CSG leaves it alone` : ''} · ${face ? face.material || 'no material' : ''}`);
    showSelection();
  }

  // The brush operations, each one step of undo.
  function others(of) { return globalGroup().items.filter(item => item.kind === 'brush' && !of.includes(item) && B.convex(item)); }
  const actions = {
    newBrush() {
      const forward = new THREE.Vector3();
      camera.getWorldDirection(forward);
      const at = camera.position.clone().addScaledVector(forward, 256);
      const size = settings.grid * 8, centre = [snap(at.x), snap(at.y), snap(-at.z)];
      const brush = B.box(centre.map(c => c - size / 2), centre.map(c => c + size / 2), template);
      replace(new Map(), [brush], 'added');
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
      let [first, ...rest] = selectedBrushes(), joined = first;
      for (const next of rest) { joined = joined && B.merge(joined, next); }
      if (!joined) { say('These brushes do not make one convex brush together'); return; }
      replace(new Map([[first, [joined]], ...rest.map(item => [item, []])]), [], null);
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
    duplicate() {
      const copies = selectedBrushes().map(item => B.translate(item, [settings.grid, 0, 0]));
      replace(new Map(), copies, 'added');
      say(`Copied ${copies.length}`);
    },
    delete() {
      const gone = selectedBrushes();
      replace(new Map(gone.map(item => [item, []])));
      say(`Deleted ${gone.length}`);
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
    const items = selectedBrushes();
    if (!items.length) return;
    const moved = items.map(item => B.translate(item, offset));
    replace(new Map(items.map((item, index) => [item, [moved[index]]])), [], null);
    // Keep the moved brushes selected.
    const fresh = globalGroup().items.filter(item => moved.some(brush => brush.vertices === item.vertices));
    fresh.forEach(item => selected.add(item));
    showSelection();
  }
  for (const [id, action] of Object.entries(actions)) $(id).addEventListener('click', event => { action(); event.target.blur(); });

  function setEditing(on) {
    editing = on;
    $('tools').hidden = !on;
    clips.visible = selection.visible = on;
    for (const mesh of entityLayer.children) if (mesh.userData.editOnly) mesh.visible = on;
    uniforms.uGrid.value = on ? settings.grid : 0;
    $('crosshair').hidden = on;
    if (on && document.pointerLockElement === canvas) document.exitPointerLock();
    $('help').textContent = on
      ? 'Tab fly · Click select (Shift adds) · Right-drag look · WASD move · Arrows / PgUp PgDn move selection · B box · H hollow · C split · Del delete · Ctrl+Z undo'
      : 'Tab edit · Click to capture / drag to look · WASD move · Space up · Shift down · Wheel speed · Esc release · 1–9 viewpoints';
    requestAnimationFrame(applySettings);  // The toolbar changes the canvas height.
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
      if (event.button === 2) looking = true; else if (event.button === 0) downAt = [event.clientX, event.clientY];
      return;
    }
    looking = true;
    try { Promise.resolve(canvas.requestPointerLock()).catch(() => { /* Drag-to-look still works. */ }); } catch (_) { /* Same. */ }
  });
  window.addEventListener('mouseup', event => {
    if (editing && event.button === 0 && downAt && Math.hypot(event.clientX - downAt[0], event.clientY - downAt[1]) < 5 && flat) select(event);
    looking = false; downAt = null;
  });
  window.addEventListener('blur', () => { looking = false; keys.clear(); });
  document.addEventListener('mousemove', event => {
    if (!looking && document.pointerLockElement !== canvas) return;
    camera.rotation.y -= event.movementX * .0025 * (settings.invertX ? -1 : 1);
    camera.rotation.x = Math.max(-1.55, Math.min(1.55, camera.rotation.x - event.movementY * .0025 * (settings.invertY ? -1 : 1)));
  });
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    speed = Math.max(20, Math.min(20000, speed * (event.deltaY < 0 ? 1.2 : 1 / 1.2)));
    if (flat) showReady();
  }, {passive: false});
  document.addEventListener('keydown', event => {
    if (event.target.matches('input:not([type=checkbox]), select')) return;
    const ctrl = event.ctrlKey || event.metaKey, key = event.key.toLowerCase();
    if (event.code === 'Tab') { event.preventDefault(); setEditing(!editing); return; }
    if (/^Digit[1-9]$/.test(event.code) && !ctrl) showViewpoint(Number(event.code.slice(5)) - 1);
    if (editing && flat) {
      if (ctrl && key === 'z') { event.preventDefault(); actions[event.shiftKey ? 'redo' : 'undo'](); return; }
      if (ctrl && key === 'y') { event.preventDefault(); actions.redo(); return; }
      if (ctrl && event.shiftKey && key === 's') { event.preventDefault(); if (!$('subtract').disabled) actions.subtract(); return; }
      if (ctrl && key === 's') { event.preventDefault(); actions.save(); return; }
      if (ctrl && key === 'd') { event.preventDefault(); if (selected.size) actions.duplicate(); return; }
      if (ctrl && key === 'm') { event.preventDefault(); if (!$('merge').disabled) actions.merge(); return; }
      if (!ctrl && key === 'b') { actions.newBrush(); return; }
      if (!ctrl && key === 'h' && !$('hollow').disabled) { actions.hollow(); return; }
      if (!ctrl && key === 'c' && !$('clip').disabled) { actions.clip(); return; }
      if ((event.code === 'Delete' || event.code === 'Backspace') && selected.size) { event.preventDefault(); actions.delete(); return; }
      if (event.code === 'Escape') { selected.clear(); say(''); showSelection(); return; }
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
    step.y += held('Space') + held('KeyE') - held('ShiftLeft') - held('ShiftRight') - held('KeyQ');
    if (step.lengthSq()) camera.position.addScaledVector(step.normalize(), speed * delta);
    renderer.render(scene, camera);
  });
  window.skinnerReflexMaps = {renderer, scene, camera, root, get map() { return map; }, get flat() { return flat; }, load, actions, selected, setEditing, showViewpoint};

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
      say('New map: B makes a box in front of the camera; Open .map file loads one from disk');
    }
  } catch (error) { showStatus('Map could not load: ' + error.message); }
});

/* Vanilla Three.js free-flight viewer for the pack written by tools/import_unreal_map.py.
   Positions are already in Three's axes (Azrael keeps native units); each polygon faces counter-clockwise. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const dx = document.body.dataset.game === 'dx';
  const quake = document.body.dataset.game === 'quake';
  const quake2 = document.body.dataset.game === 'quake2';
  const azrael = document.body.dataset.game === 'azrael';
  const sin = document.body.dataset.game === 'sin';
  const anachronox = document.body.dataset.game === 'anachronox';
  const daikatana = document.body.dataset.game === 'daikatana';
  const config = window.skinnerMapViewerConfig; // Optional adapter; existing pages keep their original paths/rendering.
  const ue1 = !config && !(quake || quake2 || azrael || sin || anachronox || daikatana);  // Unreal, UT and Deus Ex
  const npcDraws = [], npcIDs = new Set();
  const npcToggle = window.skinnerNPCToggle;
  if (npcToggle) {
    npcToggle.setCount(0);
    npcToggle.onChange = enabled => { for (const draw of npcDraws) draw.visible = enabled; };
  }
  function trackNPC(draw, metadata) {
    if (metadata.npc !== true) return;
    npcDraws.push(draw); npcIDs.add(metadata.entity);
    draw.visible = npcToggle ? npcToggle.enabled : true;
  }
  // Page seconds as a shared uniform, so adapters' shaders animate without their own clocks.
  const time = window.skinnerMapTime = window.skinnerMapTime || {value: 0};
  // Per-frame extras: the adapter plus any script that pushed onto window.skinnerMapExtras. Each may define
  // load(map, context), patchMaterial(material, group, map), update(seconds, camera, map, region) and pass('sky' | 'main').
  const extras = () => [config, ...(window.skinnerMapExtras || []),
    quake && window.quakeMaps, (quake || quake2 || sin || anachronox || daikatana) && window.quake2Maps,
    sin && window.sinMaps, anachronox && window.anachronoxMaps, daikatana && window.daikatanaMaps].filter(Boolean);
  const storageKey = config ? config.storageKey : dx ? 'skinner.dxmaps' : azrael ? 'skinner.azraelmaps' : sin ? 'skinner.sinmaps' : anachronox ? 'skinner.anachronoxmaps' : daikatana ? 'skinner.daikatanamaps' : quake2 ? 'skinner.quake2maps' : quake ? 'skinner.quakemaps' : 'skinner.unrealmaps';
  const settings = {fov: azrael ? 2 * Math.atan(.8) * 180 / Math.PI : 90, invertX: false, invertY: false};
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
  // Anachronox: captured driver rejects the gamma ramp. gl_gamma 1.4 applies only where a driver accepts it.
  const POWER = {dx: 1 / (2.5 * .6), azrael: 1, sin: 1, '2015': 1, anachronox: 1, daikatana: 1, unreal: 1 / (2.5 * .5), ut: .67, quake: 1, hipnotic: 1, rogue: 1, quake2: 1, xatrix: 1, ctf: 1,
    qextras: 1, dopa: 1, mg1: 1, mg3: 1, qctf: 1};
  const displayPower = (game, map) => game === 'dx' && Number.isFinite(map.display?.power) && map.display.power > 0 ? map.display.power : POWER[game];
  const frame = new THREE.WebGLRenderTarget(1, 1, {samples: 4});
  const ramp = new THREE.Scene(), flat = new THREE.Camera();
  ramp.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({
    uniforms: {frame: {value: frame.texture}, power: {value: POWER.unreal}, viewBlend: {value: new THREE.Vector4(0, 0, 0, 0)},
      flashScale: {value: new THREE.Vector3(1, 1, 1)}, flashAdd: {value: new THREE.Vector3()}}, depthTest: false, depthWrite: false,
    vertexShader: 'varying vec2 at; void main() { at = uv; gl_Position = vec4(position.xy, 0., 1.); }',
    // viewBlend mixes toward a colour after the ramp (Quake-family cshifts); flash scales and adds before it, as UE1's
    // EndFlash draws into the frame ahead of the gamma ramp (zone ViewFlash/ViewFog).
    fragmentShader: 'uniform sampler2D frame; uniform float power; uniform vec4 viewBlend; uniform vec3 flashScale, flashAdd; varying vec2 at;' +
      'void main() { vec3 c = pow(clamp(texture2D(frame, at).rgb * flashScale + flashAdd, 0., 1.), vec3(power));' +
      ' gl_FragColor = vec4(mix(c, viewBlend.rgb, viewBlend.a), 1.); }'})));
  const rampUniforms = ramp.children[0].material.uniforms;
  // At least 1 x 1: a hidden page has no size, and a zero-sized target fails every draw.
  const fitFrame = () => { const size = renderer.getDrawingBufferSize(new THREE.Vector2()); frame.setSize(Math.max(size.x, 1), Math.max(size.y, 1)); };
  const GAMES = config ? config.games : dx ? [['dx', 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Deus Ex']] : azrael ? [['azrael', "C:\\Program Files (x86)\\GOG Galaxy\\Games\\Azrael's Tear"]] : sin ? ['sin', '2015', 'ctf'].map(id => [id, 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\SiN 1']) : anachronox ? [['anachronox', 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Anachronox']] : daikatana ? [['daikatana', 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Daikatana\\data']] : quake2 ? ['quake2', 'xatrix', 'rogue', 'ctf'].map(id => [id, 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Quake 2\\baseq2']) : quake ? ['quake', 'hipnotic', 'rogue', 'qextras', 'dopa', 'mg1', 'mg3', 'qctf'].map(id => [id, 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Quake']) : [['unreal', 'C:\\Unreal'], ['ut', 'C:\\UnrealTournament']];  // Each game's pack and usual folder.
  const dataRoot = id => config ? config.dataRoot : dx ? '/dx-map-data/' : azrael ? '/azrael-map-data/' : sin ? `/sin-map-data/${id}/` : anachronox ? '/anachronox-map-data/' : daikatana ? '/daikatana-map-data/' : quake2 ? `/quake2-map-data/${id === 'quake2' ? '' : id + '/'}` : quake ? `/quake-map-data/${id === 'quake' ? '' : id + '/'}` : `/unreal-map-data/${id}/`;
  let data = dataRoot(GAMES[0][0]);  // The shown map's pack.
  let speed = azrael ? 160 : 8, missing = 0, ready = false, map = null;
  const showStatus = text => { $('status').textContent = text; };
  const showReady = () => showStatus(`${missing ? `Map loaded with ${missing} missing textures` : 'Map ready'} · speed ${speed.toFixed(1)} ${azrael ? 'units/s' : 'm/s'}`);

  async function get(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} (${response.status})`);
    return response;
  }
  const loader = new THREE.TextureLoader(), textures = new Map();
  function loadTexture(file) {
    if (!textures.has(file)) textures.set(file, new Promise(resolve => {
      const loaded = texture => {
      texture.flipY = false;  // The game's v runs down the image, as the rows do without the flip.
      texture.wrapS = texture.wrapT = quake ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
      texture.anisotropy = quake || quake2 || daikatana || anachronox || sin ? 1 : 8;
      if (quake2 || daikatana || anachronox || sin) { texture.magFilter = THREE.LinearFilter; texture.minFilter = THREE.LinearMipmapNearestFilter; }
      if (quake) { texture.magFilter = texture.minFilter = THREE.NearestFilter; texture.generateMipmaps = false; }
      if (config && config.configureTexture) config.configureTexture(texture);
      if (texture.isCompressedTexture && !texture.userData.completeMipChain) texture.minFilter = THREE.LinearFilter;
      resolve(texture);
      };
      const failed = () => { missing++; resolve(null); };
      const url = data + 'textures/' + file;
      if (file.toLowerCase().endsWith('.dds') && window.skinnerDDS) window.skinnerDDS.load(url).then(loaded, failed);
      else loader.load(url, loaded, undefined, failed);
    }));
    return textures.get(file);
  }

  // Poly flags (EPolyFlags): masked 0x2, translucent 0x4, modulated 0x40, fake backdrop 0x80 (where the sky shows),
  // two-sided 0x100. Blending as the game's OpenGL device draws them: translucent adds the texture over what is behind
  // (one, one minus source colour), modulated doubles what is behind by it (destination colour, source colour); neither
  // hides what is behind it from later surfaces.
  const MASKED = 0x2, TRANSLUCENT = 0x4, MODULATED = 0x40, FAKE_BACKDROP = 0x80, TWO_SIDED = 0x100;
  const panning = [], animated = [], moving = [], sprites = [];
  // Source 3D skies: the sky area's surfaces and props, drawn first from the sky camera; the level's sky faces show it.
  const sky3dDraws = [], mainDraws = [], skyFaces = [];
  let particles = null;
  let skyBackdrop = null;
  let lightMap = null;  // The map's lightmaps in one atlas: the game draws texture x lightmap x 2.
  let quakeColormap = null;
  function surfaceMaterial(group, texture) {
    if (config) return config.material(group, texture, lightMap);
    if (azrael) return window.azraelMaps.material(group, texture);
    if (sin) return window.sinMaps.material(group, texture, lightMap);
    if (anachronox) return window.anachronoxMaps.material(group, texture, lightMap);
    if (daikatana) return window.daikatanaMaps.material(group, texture, lightMap);
    if (quake2) return window.quake2Maps.material(group, texture, lightMap);
    if (quake) return window.quakeMaps.material(group, texture, lightMap, quakeColormap, map.lighting === 'rgb', map.skybox);
    const side = group.flags & TWO_SIDED ? THREE.DoubleSide : THREE.FrontSide;
    if (group.mirror) return mirrorMaterial(group.mirror);
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
  // Mirrors (UE1 MIRRORED surfaces; group.mirror = plane [a, b, c, d], a*x + b*y + c*z = d): the level seen from the eye
  // reflected in the plane, drawn into a target each frame and shown through the surface instead of its texture
  // (SurrealEngine VisibleFrame mirror portals; three.js's Reflector technique, with an oblique near plane on the mirror).
  // ponytail: no sky zone, coronas or mirrors in the reflection; one target per plane at half the frame's size.
  const mirrors = new Map();
  function mirrorMaterial(plane) {
    const key = plane.join(',');
    if (!mirrors.has(key)) {
      const normal = new THREE.Vector3(plane[0], plane[1], plane[2]);
      mirrors.set(key, {normal, point: normal.clone().multiplyScalar(plane[3] / normal.lengthSq()), materials: [],
        target: new THREE.WebGLRenderTarget(1, 1), camera: new THREE.PerspectiveCamera(), textureMatrix: new THREE.Matrix4()});
    }
    const mirror = mirrors.get(key);
    const material = new THREE.ShaderMaterial({uniforms: {reflection: {value: mirror.target.texture}, textureMatrix: {value: mirror.textureMatrix}},
      vertexShader: `uniform mat4 textureMatrix; varying vec4 at;
      void main() { at = textureMatrix * modelMatrix * vec4(position, 1.); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }`,
      fragmentShader: 'uniform sampler2D reflection; varying vec4 at; void main() { gl_FragColor = vec4(texture2DProj(reflection, at).rgb, 1.); }'});
    mirror.materials.push(material);
    return material;
  }
  const mirrorView = new THREE.Vector3(), mirrorLook = new THREE.Vector3(), mirrorTurn = new THREE.Matrix4(), mirrorPlane = new THREE.Plane(),
    clipPlane = new THREE.Vector4(), clipQ = new THREE.Vector4();
  function renderMirrors() {
    if (!mirrors.size) return;
    const size = renderer.getDrawingBufferSize(new THREE.Vector2());
    for (const mirror of mirrors.values()) {
      const {normal, point, camera: virtual, target, textureMatrix} = mirror;
      mirrorView.subVectors(point, camera.position);
      if (mirrorView.dot(normal) > 0) continue;  // The eye behind the mirror sees nothing of it.
      target.setSize(Math.max(1, size.x >> 1), Math.max(1, size.y >> 1));
      mirrorView.reflect(normal).negate().add(point);
      mirrorTurn.extractRotation(camera.matrixWorld);
      mirrorLook.set(0, 0, -1).applyMatrix4(mirrorTurn).add(camera.position);
      mirrorLook.subVectors(point, mirrorLook).reflect(normal).negate().add(point);
      virtual.position.copy(mirrorView);
      virtual.up.set(0, 1, 0).applyMatrix4(mirrorTurn).reflect(normal);
      virtual.lookAt(mirrorLook);
      virtual.far = camera.far;
      virtual.updateMatrixWorld();
      virtual.projectionMatrix.copy(camera.projectionMatrix);
      textureMatrix.set(.5, 0, 0, .5, 0, .5, 0, .5, 0, 0, .5, .5, 0, 0, 0, 1).multiply(virtual.projectionMatrix).multiply(virtual.matrixWorldInverse);
      // Oblique near plane on the mirror (Lengyel), so nothing behind it shows in the reflection.
      mirrorPlane.setFromNormalAndCoplanarPoint(normal.clone().normalize(), point).applyMatrix4(virtual.matrixWorldInverse);
      clipPlane.set(mirrorPlane.normal.x, mirrorPlane.normal.y, mirrorPlane.normal.z, mirrorPlane.constant);
      const e = virtual.projectionMatrix.elements;
      clipQ.set((Math.sign(clipPlane.x) + e[8]) / e[0], (Math.sign(clipPlane.y) + e[9]) / e[5], -1, (1 + e[10]) / e[14]);
      clipPlane.multiplyScalar(2 / clipPlane.dot(clipQ));
      e[2] = clipPlane.x; e[6] = clipPlane.y; e[10] = clipPlane.z + 1; e[14] = clipPlane.w;
      virtual.projectionMatrixInverse.copy(virtual.projectionMatrix).invert();
      for (const material of mirror.materials) material.visible = false;
      renderer.setRenderTarget(target);
      renderer.clear();
      renderer.render(scene, virtual);
      for (const material of mirror.materials) material.visible = true;
    }
  }

  // The world's triangles in a coarse grid, for line-of-sight tests (coronas): every group but placed models, movers,
  // NPCs, the 3D sky area and UE1's non-solid surfaces, as the game's own line checks see only the level's BSP.
  function occluderGrid(geometry) {
    const at = geometry.attributes.position.array, index = geometry.index.array, tris = [];
    for (const g of map.groups) {
      if (g.mesh || g.kind === 'model' || g.mover !== undefined || g.npc || g.sky3d || (ue1 && g.flags & 0x8)) continue;
      for (let i = g.start; i < g.start + g.count; i += 3) tris.push(i);
    }
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const i of tris) for (let k = 0; k < 3; k++) for (let a = 0; a < 3; a++) {
      const v = at[index[i + k] * 3 + a]; lo[a] = Math.min(lo[a], v); hi[a] = Math.max(hi[a], v);
    }
    // ponytail: a uniform grid of at most 96 cells an axis; a BVH if huge maps make the tests slow.
    const cell = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], 1) / 96, n = [0, 1, 2].map(a => Math.floor((hi[a] - lo[a]) / cell) + 1);
    const cellOf = (v, a) => Math.min(n[a] - 1, Math.max(0, Math.floor((v - lo[a]) / cell))), cells = new Map();
    for (let t = 0; t < tris.length; t++) {
      const r = [0, 1, 2].map(a => { const v = [0, 1, 2].map(k => at[index[tris[t] + k] * 3 + a]); return [cellOf(Math.min(...v), a), cellOf(Math.max(...v), a)]; });
      for (let x = r[0][0]; x <= r[0][1]; x++) for (let y = r[1][0]; y <= r[1][1]; y++) for (let z = r[2][0]; z <= r[2][1]; z++) {
        const key = (x * n[1] + y) * n[2] + z;
        if (!cells.has(key)) cells.set(key, []);
        cells.get(key).push(t);
      }
    }
    const seen = new Int32Array(tris.length); let stamp = 0;
    const p = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()], e1 = new THREE.Vector3(), e2 = new THREE.Vector3(),
      d = new THREE.Vector3(), s = new THREE.Vector3(), h = new THREE.Vector3(), q = new THREE.Vector3();
    // Does the segment a -> b cross a world triangle (Moeller-Trumbore, either side)? Cells sampled every half cell.
    return (a, b) => {
      stamp++;
      d.subVectors(b, a);
      const steps = Math.ceil(d.length() / cell * 2) + 1;
      for (let k = 0; k <= steps; k++) {
        const x = a.x + d.x * k / steps, y = a.y + d.y * k / steps, z = a.z + d.z * k / steps;
        const list = cells.get((cellOf(x, 0) * n[1] + cellOf(y, 1)) * n[2] + cellOf(z, 2));
        if (!list) continue;
        for (const t of list) {
          if (seen[t] === stamp) continue;
          seen[t] = stamp;
          for (let c = 0; c < 3; c++) p[c].fromArray(at, index[tris[t] + c] * 3);
          e1.subVectors(p[1], p[0]); e2.subVectors(p[2], p[0]); h.crossVectors(d, e2);
          const det = e1.dot(h);
          if (Math.abs(det) < 1e-12) continue;
          s.subVectors(a, p[0]);
          const u = s.dot(h) / det;
          if (u < 0 || u > 1) continue;
          q.crossVectors(s, e1);
          const v = d.dot(q) / det;
          if (v < 0 || u + v > 1) continue;
          const f = e2.dot(q) / det;
          if (f > 1e-5 && f < 1) return true;
        }
      }
      return false;
    };
  }

  // Billboards (map.sprites, one batch a texture): {texture, frames?, fps?, blend: add | translucent | alpha | modulate,
  // screen?, occlude?, depth?, range?, aspect?, fx_falloff?, items: [[x, y, z, size, r, g, b, a], ...]}. A world sprite
  // is `size` wide (page units) and aspect x size tall, facing the camera; a screen sprite (UE1 corona) is a square
  // `size` x the viewport's width wide whatever its distance, its tint dimmed by fx_falloff (see the shader) when
  // given. Occluded sprites are those the line to the eye finds a world
  // triangle on; they fade out (and back in) over `fade` seconds. Depth-tested unless depth is false.
  // add: Source's additive sprites (the texture's alpha scales what is added); translucent: UE1's (colour only).
  const BLEND = {add: ['SrcAlphaFactor', 'OneFactor'], translucent: ['OneFactor', 'OneMinusSrcColorFactor'],
    alpha: ['SrcAlphaFactor', 'OneMinusSrcAlphaFactor'], modulate: ['DstColorFactor', 'SrcColorFactor']};
  const overlay = new THREE.Scene();  // Sprites drawn after the level without depth (coronas).
  let blocked = null;
  async function buildSprites(geometry) {
    const quad = new THREE.PlaneGeometry(1, 1), viewAspect = sprites.viewAspect = {value: 1}, halfWidth = sprites.halfWidth = {value: 1};
    if (map.sprites.some(batch => batch.occlude)) blocked = occluderGrid(geometry);
    for (const batch of map.sprites) {
      const texture = await loadTexture(batch.texture), n = batch.items.length;
      if (!texture || !n) continue;
      const shape = new THREE.InstancedBufferGeometry();
      shape.index = quad.index; shape.setAttribute('position', quad.attributes.position); shape.setAttribute('uv', quad.attributes.uv);
      const item = new Float32Array(n * 8), shown = new Float32Array(n).fill(batch.occlude ? 0 : 1);
      batch.items.forEach((values, i) => { item.set(values, i * 8); if (values.length < 8) item[i * 8 + 7] = 1; });
      const buffer = new THREE.InstancedInterleavedBuffer(item, 8);
      shape.setAttribute('center', new THREE.InterleavedBufferAttribute(buffer, 3, 0));
      shape.setAttribute('size', new THREE.InterleavedBufferAttribute(buffer, 1, 3));
      shape.setAttribute('tint', new THREE.InterleavedBufferAttribute(buffer, 4, 4));
      shape.setAttribute('shown', new THREE.InstancedBufferAttribute(shown, 1));
      shape.instanceCount = n;
      const [src, dst] = BLEND[batch.blend] || BLEND.alpha;
      const material = new THREE.ShaderMaterial({
        transparent: true, depthWrite: false, depthTest: batch.depth !== false && !batch.screen, side: THREE.DoubleSide,
        blending: THREE.CustomBlending, blendSrc: THREE[src], blendDst: THREE[dst],
        uniforms: {surface: {value: texture}, screen: {value: !!batch.screen}, aspect: {value: batch.aspect || 1}, viewAspect, halfWidth,
          fxFalloff: {value: batch.fx_falloff || 0},
          modulate: {value: batch.blend === 'modulate'}, additive: {value: batch.blend === 'translucent'}},
        vertexShader: `attribute vec3 center; attribute float size, shown; attribute vec4 tint; uniform bool screen, additive, modulate;
        uniform float aspect, viewAspect, halfWidth, fxFalloff; varying vec2 at; varying vec4 colour;
        void main() {
          at = vec2(uv.x, 1. - uv.y);  // Rows run down the image (textures are not flipped).
          colour = additive ? vec4(tint.rgb * tint.a * shown, 1.) : modulate ? vec4(mix(vec3(.5), tint.rgb, shown), 1.) : vec4(tint.rgb, tint.a * shown);
          if (screen) {
            vec4 clip = projectionMatrix * viewMatrix * vec4(center, 1.);
            // fx_falloff: the tint x min(1, half the frame's width in pixels x fx_falloff / the view depth (clip.w)), the
            // width in CSS pixels on screen (the window the game would have), a target's own pixels when capturing.
            if (fxFalloff > 0. && clip.w > 0.) colour.rgb = min(halfWidth * fxFalloff / clip.w, 1.) * tint.rgb * tint.a * shown;
            gl_Position = clip.w <= 0. ? vec4(2., 2., 2., 1.) : clip + vec4(position.xy * 2. * size * vec2(1., viewAspect) * clip.w, 0., 0.);
          } else {
            vec4 view = viewMatrix * vec4(center, 1.);
            view.xy += position.xy * vec2(size, size * aspect);
            gl_Position = projectionMatrix * view;
          }
          if (shown <= 0.) gl_Position = vec4(2., 2., 2., 1.);
        }`,
        fragmentShader: `uniform sampler2D surface; varying vec2 at; varying vec4 colour;
        void main() { vec4 c = texture2D(surface, at); gl_FragColor = vec4(c.rgb * colour.rgb, c.a * colour.a); }`});
      const mesh = Object.assign(new THREE.Mesh(shape, material), {frustumCulled: false, renderOrder: 1});
      (batch.screen ? overlay : scene).add(mesh);
      const frames = batch.frames?.length > 1 ? await Promise.all(batch.frames.map(loadTexture)) : null;
      sprites.push({batch, material, frames, shown, attribute: shape.attributes.shown, target: new Uint8Array(n), next: 0,
        centers: batch.items.map(values => new THREE.Vector3(values[0], values[1], values[2]))});
    }
  }
  const frustum = new THREE.Frustum(), viewProjection = new THREE.Matrix4();
  let lastSeconds = 0;
  function updateSprites(seconds) {
    const delta = Math.max(0, Math.min(seconds - lastSeconds, .25));
    lastSeconds = seconds;
    if (!sprites.length) return;
    sprites.viewAspect.value = camera.aspect;
    frustum.setFromProjectionMatrix(viewProjection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
    const budget = performance.now() + 2;  // ms of line tests a frame; the rest wait their turn (round robin).
    for (const s of sprites) {
      const {batch, centers, shown, target} = s;
      if (s.frames) s.material.uniforms.surface.value = s.frames[Math.floor(seconds * (batch.fps || 10)) % s.frames.length] || s.frames[0];
      if (!batch.occlude && !batch.range) continue;
      const start = s.next;
      for (let k = 0; k < centers.length; k++) {
        const i = (start + k) % centers.length, c = centers[i];
        const inRange = !batch.range || c.distanceTo(camera.position) < batch.range;
        if (!inRange || !frustum.containsPoint(c)) target[i] = 0;
        else if (batch.occlude && performance.now() < budget) { target[i] = blocked(c, camera.position) ? 0 : 1; s.next = (i + 1) % centers.length; }
        else if (!batch.occlude) target[i] = 1;
      }
      // ponytail: a linear fade (batch.fade seconds, 0.1 by default); hypothesis, SurrealEngine switches at once.
      const step = delta / (batch.fade || .1);
      for (let i = 0; i < shown.length; i++) shown[i] = target[i] ? Math.min(1, shown[i] + step) : batch.occlude ? Math.max(0, shown[i] - step) : 0;
      s.attribute.needsUpdate = true;
    }
  }
  // A mover's pose now: keys [[t, x, y, z, qx, qy, qz, qw], ...] (an offset and turn from where it is drawn, about its
  // pivot) looping every `period` seconds (the last key's time by default), times a steady spin [x, y, z, radians a second].
  const moverTurn = new THREE.Quaternion(), moverSpin = new THREE.Quaternion(), moverAt = new THREE.Vector3(),
    keyTurn = new THREE.Quaternion(), moverPivot = new THREE.Vector3(), moverAxis = new THREE.Vector3(), unit = new THREE.Vector3(1, 1, 1);
  function updateMovers(seconds) {
    for (const mover of moving) {
      moverAt.set(0, 0, 0); moverTurn.identity();
      const keys = mover.keys;
      if (keys?.length) {
        const period = mover.period || keys[keys.length - 1][0] || 1, t = ((seconds + (mover.phase || 0)) % period + period) % period;
        let k = keys.length - 1;
        while (k > 0 && keys[k][0] > t) k--;
        const a = keys[k], b = keys[(k + 1) % keys.length], span = (k + 1 < keys.length ? b[0] : period) - a[0];
        const f = span > 0 ? Math.min(1, (t - a[0]) / span) : 0;
        moverAt.set(a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f);
        moverTurn.set(a[4], a[5], a[6], a[7]).slerp(keyTurn.set(b[4], b[5], b[6], b[7]), f);
      }
      if (mover.spin) moverTurn.multiply(moverSpin.setFromAxisAngle(moverAxis.fromArray(mover.spin).normalize(), mover.spin[3] * seconds));
      moverPivot.fromArray(mover.pivot || [0, 0, 0]);
      // pivot + offset + turn x (point - pivot)
      mover.mesh.matrix.compose(moverAt.add(moverPivot).sub(moverPivot.applyQuaternion(moverTurn)), moverTurn, unit);
      mover.mesh.matrixWorldNeedsUpdate = true;
    }
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
    // Environment-mapped mesh faces (UE1 flag 0x10) reflect the eye ray off their normals.
    if ((config && config.computeNormals) || (ue1 && map.groups.some(group => group.flags & 0x10))) geometry.computeVertexNormals();
    if (config && config.prepareGeometry) config.prepareGeometry(geometry, map);
    // Movers (map.movers; their groups name one by `mover`): their own mesh over the same vertices, moved each frame.
    for (const mover of map.movers || []) {
      mover.geometry = new THREE.BufferGeometry();
      for (const [name, attribute] of Object.entries(geometry.attributes)) mover.geometry.setAttribute(name, attribute);
      mover.geometry.setIndex(geometry.index);
    }
    if (config) await config.load(map, loadTexture);
    if (quake) quakeColormap = await loadTexture('colormap.png');
    if (sin) await window.sinMaps.load(map, loadTexture);
    if (anachronox) await window.anachronoxMaps.load(map, loadTexture);
    if (daikatana) await window.daikatanaMaps.load(map, loadTexture);
    if (quake2) await window.quake2Maps.loadSky(map.skybox, loadTexture);
    if (quake && map.skybox) await window.quake2Maps.loadSky(map.skybox, loadTexture);
    for (const extra of window.skinnerMapExtras || []) await extra.load?.(map, {data, get, loadTexture, loader, scene, renderer, camera, lightMap});
    const materials = await Promise.all(map.groups.map(async (group, index) => {
      (map.movers?.[group.mover]?.geometry || geometry).addGroup(group.start, group.count, index);
      const material = surfaceMaterial(group, !config && !quake2 && !daikatana && !anachronox && !sin && group.flags & FAKE_BACKDROP ? null : await loadTexture(group.texture));
      for (const extra of extras()) extra.patchMaterial?.(material, group, map);
      // Decals and overlays lie on their surfaces: drawn a little nearer, as the engine's decal depth bias does.
      if (group.decal) Object.assign(material, {polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -4});
      trackNPC(material, group);
      // Frame chains (Quake +0..+9, Quake II/SiN nexttexinfo, UE1 AnimNext, Source AnimatedTexture): every frame's
      // texture, swapped at the chain's rate. ponytail: world groups only; placed models keep their first frame.
      if (group.anim?.frames?.length > 1) animated.push({material, fps: group.anim.fps, frames: await Promise.all(group.anim.frames.map(loadTexture))});
      if (map.sky3d) (group.sky3d ? sky3dDraws : mainDraws).push(material);
      if (map.sky3d && group.kind === 'sky') skyFaces.push(material);
      return material;
    }));
    scene.add(new THREE.Mesh(geometry, materials));
    for (const mover of map.movers || []) {  // moved each frame from here on
      mover.mesh = Object.assign(new THREE.Mesh(mover.geometry, materials), {frustumCulled: false, matrixAutoUpdate: false});
      scene.add(mover.mesh);
      moving.push(mover);
    }
    if (map.sprites?.length) await buildSprites(geometry, materials);
    // Source effects and Bloodlines' own particles of the import's map.particles in the shared particle system
    // (particles.js), in groups of their own: the level's for the main pass, the 3D sky area's (batches flagged sky3d)
    // for the sky pass.
    if (map.particles && window.skinnerParticles) {
      const group = new THREE.Group(), skyGroup = new THREE.Group();
      scene.add(group, skyGroup);
      if (map.sky3d) { mainDraws.push(group); sky3dDraws.push(skyGroup); }
      particles = await window.skinnerParticles.mount(THREE, map.particles, {loadTexture: stage => loadTexture((stage.frames || [stage.map])[0]),
        add: mesh => (map.particles.batches[mesh.userData.batch]?.sky3d ? skyGroup : group).add(mesh), toWorld: map.particles.toWorld, up: map.particles.up});
    }
    if (config && config.buildInstances) {
      for (const object of await config.buildInstances(map, data, get, loadTexture)) {
        scene.add(object); trackNPC(object, object.userData);
        if (map.sky3d) (object.userData.sky3d ? sky3dDraws : mainDraws).push(object);
      }
    }
    if (npcToggle) npcToggle.setCount(npcIDs.size);
  }

  function showNotes() {
    const parts = [];
    if (config) {
      parts.push(...(map.gaps || []));
      if ((map.model_failures || []).length) parts.push(`${map.model_failures.length} model placements could not be drawn; see import report`);
    }
    if (quake) {
      const guesses = [...new Set((map.instances || []).filter(item => item.guess).map(item => item.classname))];
      if (guesses.length) parts.push('Brush positions not verified, shown at authored origin: ' + guesses.join(', '));
      const unhandled = (map.bspx || []).filter(item => !item.handled && item.reason === 'not handled').map(item => item.name);
      if (unhandled.length) parts.push('BSPX extensions not handled: ' + unhandled.join(', '));
    }
    if ((daikatana || anachronox || sin) && (map.model_failures || []).length) parts.push(`${map.model_failures.length} scenery models could not be drawn`);
    if ((map.missing || []).length) parts.push((quake || daikatana || anachronox || sin ? 'Missing source textures (fallback shown): ' : 'Textures the game files do not hold, drawn grey: ') + map.missing.join('; '));
    if (azrael || anachronox || sin) parts.push(...(map.gaps || []));
    if (dx) {
      if (map.actors?.failed?.length) parts.push('Actors not drawn: ' + map.actors.failed.join('; '));
      if (map.issues?.length) parts.push(`${map.issues.length} package/class read issues recorded in scene.json`);
    }
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
    if (azrael && map) { camera.near = map.camera.near; camera.far = map.camera.far; }
    // Three uses vertical FOV. Azrael's field follows the longer viewport axis.
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2) / (azrael ? Math.max(1, aspect) : aspect)));
    camera.updateProjectionMatrix();
  }
  // A viewpoint's yaw turns from Unreal's x (the page's +z) toward its y (the page's -x); pitch is up.
  function showViewpoint(index) {
    const view = map && map.viewpoints[index];
    if (!view) return;
    camera.position.fromArray(view.origin);
    camera.rotation.set(THREE.MathUtils.degToRad(view.pitch), Math.PI - THREE.MathUtils.degToRad(view.yaw), (config || quake2 || daikatana || anachronox || sin) ? THREE.MathUtils.degToRad(view.roll || 0) : 0);
  }

  $('fov').addEventListener('change', event => { settings.fov = Math.max(30, Math.min(130, Number(event.target.value) || 90)); save(); applySettings(); });
  for (const id of ['invertX', 'invertY']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
  $('reset').addEventListener('click', () => { speed = azrael ? 160 : 8; showViewpoint(0); if (ready) showReady(); });
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
  // The convex region holding a point: map.region_brushes first (planes [a, b, c, d], inside where a*x + b*y + c*z <= d),
  // then map.region_tree (nodes [a, b, c, d, front, back]; a negative child -1 - i ends at map.regions[i]). Page axes.
  function regionAt(p) {
    if (!map || !map.regions) return null;
    for (const brush of map.region_brushes || []) {
      if (brush.planes.every(([a, b, c, d]) => a * p.x + b * p.y + c * p.z <= d)) return map.regions[brush.region] || null;
    }
    const tree = map.region_tree;
    if (!tree) return null;
    let node = tree.root;
    while (node >= 0) { const q = tree.nodes[node]; node = q[0] * p.x + q[1] * p.y + q[2] * p.z - q[3] >= 0 ? q[4] : q[5]; }
    return map.regions[-1 - node] || null;
  }
  const npcHidden = draw => npcDraws.includes(draw) && !(npcToggle ? npcToggle.enabled : true);
  function showPass(sky) {
    for (const draw of sky3dDraws) draw.visible = sky && !npcHidden(draw);
    for (const draw of mainDraws) draw.visible = !sky && !npcHidden(draw);
    for (const material of skyFaces) material.colorWrite = sky;  // In the level, sky faces are holes onto the 3D sky.
    for (const extra of extras()) extra.pass?.(sky ? 'sky' : 'main');
  }

  function draw(seconds, target = null) {
    time.value = seconds;
    if (quake) window.quakeMaps.time.value = seconds;
    if (window.quake2Maps) window.quake2Maps.time.value = seconds;
    const region = regionAt(camera.position);
    if (sin) rampUniforms.viewBlend.value.fromArray(window.sinMaps.viewBlend(map, camera.position));
    else rampUniforms.viewBlend.value.fromArray(region?.blend || [0, 0, 0, 0]);
    const flash = region?.flash || [1, 1, 1, 0, 0, 0];
    rampUniforms.flashScale.value.fromArray(flash, 0);
    rampUniforms.flashAdd.value.fromArray(flash, 3);
    if (map) for (const extra of extras()) extra.update?.(seconds, camera, map, region);
    camera.updateMatrixWorld();
    updateMovers(seconds);
    updateSprites(seconds);
    particles?.update(seconds, camera.position.toArray(), true);
    for (const {material, fps, frames} of animated) {
      const texture = frames[Math.floor(seconds * fps) % frames.length];
      if (!texture) continue;
      const uniform = material.uniforms && (material.uniforms.surface || material.uniforms.indices);
      if (uniform) uniform.value = texture; else if (material.map) material.map = texture;
    }
    for (const {texture, pan} of panning) texture.offset.set(pan[0] * seconds % 1, pan[1] * seconds % 1);
    if (map && map.sky && map.sky.rate) skyTurn.copy(unrealTurn(map.sky.rotator.map((a, i) => a + map.sky.rate[i] * seconds)));
    if (target) frame.setSize(target.width, target.height);
    renderMirrors();
    renderer.setRenderTarget(frame);
    renderer.clear();
    if (map && map.sky) {
      skyCamera.position.fromArray(map.sky.origin);
      skyCamera.projectionMatrix.copy(camera.projectionMatrix);
      skyCamera.quaternion.copy(skyTurn).multiply(camera.quaternion);
      renderer.render(scene, skyCamera);
      renderer.clearDepth();
    }
    if (map && map.sky3d) {  // Source sky_camera: the sky area seen from origin + view / scale, turned with the view.
      skyCamera.position.fromArray(map.sky3d.origin).addScaledVector(camera.position, 1 / map.sky3d.scale);
      skyCamera.quaternion.copy(camera.quaternion);
      skyCamera.projectionMatrix.copy(camera.projectionMatrix);
      skyCamera.projectionMatrixInverse.copy(camera.projectionMatrixInverse);
      showPass(true);
      // The 2D sky first, all round the sky camera: Source draws its skybox in the 3D sky view (viewrender.cpp
      // CSkyboxView::Setup adds DF_DRAWSKYBOX), so directions the sky area's own sky faces miss still show it.
      if (!skyBackdrop && skyFaces.length) {
        const material = skyFaces[0].clone();
        Object.assign(material, {uniforms: skyFaces[0].uniforms, side: THREE.BackSide, depthWrite: false, depthTest: false, colorWrite: true});
        skyBackdrop = new THREE.Scene();
        skyBackdrop.add(Object.assign(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material), {frustumCulled: false}));
      }
      if (skyBackdrop) { skyBackdrop.children[0].position.copy(skyCamera.position); renderer.render(skyBackdrop, skyCamera); }
      renderer.render(scene, skyCamera);
      renderer.clearDepth();
      showPass(false);
    }
    renderer.render(scene, camera);
    if (overlay.children.length) { sprites.halfWidth.value = target ? target.width / 2 : frame.width / 2 / renderer.getPixelRatio(); renderer.render(overlay, camera); }
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
  window[config ? config.debugName : dx ? 'skinnerDxMaps' : azrael ? 'skinnerAzraelMaps' : sin ? 'skinnerSinMaps' : anachronox ? 'skinnerAnachronoxMaps' : daikatana ? 'skinnerDaikatanaMaps' : quake2 ? 'skinnerQuake2Maps' : quake ? 'skinnerQuakeMaps' : 'skinnerUnrealMaps'] = {renderer, scene, camera, draw, showViewpoint, unrealTurn, overlay, sprites, mirrors, moving, get particles() { return particles; }, blocked: (a, b) => blocked?.(a, b)};  // For checks in a hidden page, where no frame is drawn.

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
    $('importStatus').textContent = azrael ? 'Importing rooms and extras…' : quake ? 'Importing classic Quake maps…' : 'Importing maps… a full install takes two to three minutes.';
    try {
      const response = await fetch(config ? config.importRoute : dx ? '/import_dx_maps' : azrael ? '/import_azrael_maps' : sin ? '/import_sin_maps' : anachronox ? '/import_anachronox_maps' : daikatana ? '/import_daikatana_maps' : quake2 ? '/import_quake2_maps' : quake ? '/import_quake_maps' : '/import_unreal_maps', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path, game: chosen, replace: $('replace').checked})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      try { localStorage.setItem(pathKey(), path); } catch (_) { /* Path is simply not remembered. */ }
      const failed = Object.entries(result.failed).map(([name, reason]) => `${name}: ${reason}`);
      $('importStatus').textContent = `Imported ${result.imported.length}, skipped ${result.skipped.length}${quake || azrael ? '' : ' already imported'}` +
        (failed.length ? `, failed ${failed.length} (${failed.join('; ')})` : '') + '.';
      // Reload to list a game's maps the first time they arrive.
      if (azrael && result.imported.length) location.reload();
      if (!azrael && result.imported.length && !failed.length && (quake || !map || !$('map').querySelector(`option[value^="${chosen}/"]`))) location.reload();
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
    const available = azrael || config?.filterUnavailable ? maps.filter(found => found.status === 'ready') : maps;
    const item = available.find(found => found.key === key) || available.find(found => found.key === (dx ? 'dx/01_nyc_unatcoisland' : azrael ? 'azrael/room-001-entrycoord' : sin ? 'sin/bank' : anachronox ? 'anachronox/ballotine' : daikatana ? 'daikatana/e1m1a' : quake2 ? 'quake2/base1' : quake ? 'quake/start' : 'unreal/nyleve')) || available[0];
    if (!item) throw new Error("No drawable scenes imported; see the import report.");
    const byGroup = new Map();
    for (const found of maps) byGroup.set(found.group, [...(byGroup.get(found.group) || []), found]);
    $('map').replaceChildren(...[...byGroup].map(([group, items]) => {
      const element = Object.assign(document.createElement('optgroup'), {label: group});
      element.append(...items.map(found => Object.assign(new Option(azrael ? found.name + (found.aliases.length ? ' (' + found.aliases.join(', ') + ')' : '') + (found.status !== 'ready' ? ' — unavailable' : '') : found.title ? `${found.name} · ${found.title}` : found.name, found.key), {disabled: (azrael || config?.filterUnavailable) && found.status !== 'ready', title:found.reason||''})));
      return element;
    }));
    $('map').value = item.key;
    // Download the shown map as OBJ/MTL with textures (app.py export_map_obj).
    const exportLink = $('exportObj') || $('map').insertAdjacentElement('afterend', Object.assign(document.createElement('a'), {id: 'exportObj', textContent: 'Export OBJ', title: 'Download this map as OBJ/MTL with textures (ZIP)'}));
    exportLink.href = `/export_map_obj/${document.body.dataset.game || 'unreal'}/${item.game}/${item.id}`;
    exportLink.setAttribute('download', '');
    data = dataRoot(item.game);
    // Offer the import of the game whose maps are not there yet.
    const absent = GAMES.find(([id]) => !maps.some(found => found.game === id));
    if (absent) { $('importGame').value = absent[0]; showPath(); }
    const mapId = item.id;
    map = {...await (await get(`${data}maps/${mapId}/scene.json`)).json(), id: mapId};
    ramp.children[0].material.uniforms.power.value = config ? map.lighting.display_power : displayPower(item.game, map);
    document.title = `${map.title || map.name} — ${config ? config.label : dx ? "Deus Ex" : azrael ? "Azrael's Tear" : sin ? 'SiN' : anachronox ? 'Anachronox' : daikatana ? 'Daikatana' : quake2 ? 'Quake II' : quake ? 'Quake' : 'Unreal'} Maps`;
    if (azrael) $('mapSource').textContent = map.source;
    if (map.sky) skyTurn.setFromRotationMatrix(new THREE.Matrix4().setFromMatrix3(new THREE.Matrix3().set(...map.sky.rotation)));
    applySettings();
    showViewpoint(0);
    await buildWorld(await (await get(`${data}maps/${mapId}/geometry.bin`)).arrayBuffer());
    showNotes();
    ready = true;
    showReady();
  } catch (error) { if (npcToggle) npcToggle.setCount(0); showStatus('Map could not load: ' + error.message); }
});

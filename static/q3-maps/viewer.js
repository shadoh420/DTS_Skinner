/* Vanilla Three.js free-flight viewer for the pack written by tools/import_q3_map.py.
   Geometry keeps the game's own axes (x, y, z up) under a root turned to Three's (x, z, -y).
   What is drawn follows ArenaPrototype's Quake3StaticWorldScene and Quake3StaticStage shader. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const storageKey = 'skinner.q3maps';
  const settings = {fov: 90, animate: true, invertX: false, invertY: false};
  try { Object.assign(settings, JSON.parse(localStorage.getItem(storageKey) || '{}')); } catch (_) { /* Defaults remain usable. */ }
  const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(settings)); } catch (_) { /* Storage may be unavailable. */ } };

  const canvas = $('c');
  const renderer = new THREE.WebGLRenderer({canvas});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene(), root = new THREE.Group();
  root.rotation.x = -Math.PI / 2;
  root.updateMatrixWorld();
  scene.add(root);
  const camera = new THREE.PerspectiveCamera(60, 1, 2, 60000);
  camera.rotation.order = 'YXZ';
  const data = '/q3-map-data/';
  let speed = 400, missing = 0, ready = false, map = null;
  const failures = [];
  const showStatus = text => { $('status').textContent = text; };
  const showReady = () => showStatus(`${missing ? `Map loaded with ${missing} missing textures` : 'Map ready'} · speed ${Math.round(speed)}`);
  // A shader the driver rejects is reported by Three on the console; it belongs in the notes.
  const consoleError = console.error;
  console.error = (...args) => {
    if (String(args[0]).startsWith('THREE.WebGLProgram') && !failures.length) { failures.push('a shader failed to compile'); showNotes(); }
    consoleError.apply(console, args);
  };

  async function get(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} (${response.status})`);
    return response;
  }
  const loader = new THREE.TextureLoader(), textures = new Map();
  const white = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);
  white.needsUpdate = true;
  // CNQ3's default image, drawn where a shader cannot be built: dark grey with a red line along s, a green one
  // along t and a yellow diagonal (tr_image.cpp R_CreateDefaultImage).
  const fallbackTexels = new Uint8Array(16 * 16 * 4).fill(32);
  for (let i = 0; i < 16; i++) {
    const level = 64 + 8 * i;
    fallbackTexels.set([level, 32, 32, 255], i * 4);
    fallbackTexels.set([32, level, 32, 255], i * 64);
    fallbackTexels.set([level, level, 32, 255], i * 68);
  }
  const fallback = new THREE.DataTexture(fallbackTexels, 16, 16);
  fallback.wrapS = fallback.wrapT = THREE.RepeatWrapping;
  fallback.generateMipmaps = true;
  fallback.minFilter = THREE.LinearMipmapLinearFilter;
  fallback.magFilter = THREE.LinearFilter;
  fallback.needsUpdate = true;
  function loadTexture(file, clamp, plain) {
    const key = file + (clamp ? '|clamp' : '') + (plain ? '|plain' : '');
    if (!textures.has(key)) textures.set(key, new Promise(resolve => loader.load(data + 'textures/' + file, texture => {
      texture.flipY = false;  // The game's t runs down the image, as the rows do without the flip.
      if (plain) { texture.generateMipmaps = false; texture.minFilter = THREE.LinearFilter; } else texture.anisotropy = 8;
      if (!clamp) texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      resolve(texture);
    }, undefined, () => { missing++; resolve(null); })));
    return textures.get(key);
  }

  // The game brightens light by one bit and, where a channel overflows, scales the colour down to keep its hue
  // (R_ColorShiftLightingBytes with the default r_mapOverBrightBits 2 and r_overBrightBits 1).
  function shiftLight(r, g, b, out, at) {
    r *= 2; g *= 2; b *= 2;
    const most = Math.max(r, g, b);
    if (most > 255) { r = r * 255 / most; g = g * 255 / most; b = b * 255 / most; }
    out[at] = r; out[at + 1] = g; out[at + 2] = b;
  }

  // Shader time and the camera in the game's axes, shared by every stage material.
  const uniforms = {uTime: {value: 0}, uView: {value: new THREE.Vector3()}};
  const number = value => { const text = String(Number(value) || 0), float = /[.e]/.test(text) ? text : text + '.0'; return value < 0 ? `(${float})` : float; };
  const WAVES = {sin: 0, triangle: 1, square: 2, sawtooth: 3, inversesawtooth: 4};
  const waveCall = ([func, base, amplitude, phase, frequency]) => `wave(${WAVES[func] || 0}, ${[base, amplitude, phase, frequency].map(number).join(', ')})`;
  const WAVE = `uniform float uTime;
    float wave(int f, float base, float amplitude, float phase, float frequency) {
      float x = fract(phase + uTime * frequency);
      float v = f == 0 ? sin(x * 6.2831853) : f == 1 ? (x < .25 ? x * 4. : x < .75 ? 2. - x * 4. : x * 4. - 4.) : f == 2 ? (x < .5 ? 1. : -1.) : f == 3 ? x : 1. - x;
      return base + v * amplitude;
    }`;
  // Texture coordinates of a stage as GLSL acting on `st`, with the vertex or sky direction in P and the normal in N
  // (RB_CalcEnvironmentTexCoords, RB_CalcScrollTexCoords, RB_CalcRotateTexCoords, RB_CalcStretchTexCoords,
  // RB_CalcTurbulentTexCoords; the cloud layer is ArenaPrototype's NativeCloudTexCoord).
  function coordinates(stage, sky) {
    let code = sky ? `float height = ${number(sky.cloudHeight)}, squared = dot(P, P);
        float p = (-2. * P.z * 4096. + 2. * sqrt(max(P.z * P.z * 16777216. + squared * (8192. * height + height * height), 0.))) / (2. * squared);
        vec3 onSphere = normalize(P * p + vec3(0., 0., 4096.));
        vec2 st = vec2(acos(clamp(onSphere.x, -1., 1.)), acos(clamp(onSphere.y, -1., 1.)));`
      : stage.tcGen === 'lightmap' ? 'vec2 st = lm;'
      : stage.tcGen === 'environment' ? 'vec3 viewer = normalize(uView - P); vec3 turned = N * 2. * dot(N, viewer) - viewer; vec2 st = vec2(.5 + turned.y * .5, .5 - turned.z * .5);'
      : 'vec2 st = uv;';
    for (const [kind, ...values] of stage.tcMods || []) {
      const v = values.map(number);
      if (kind === 'scroll') code += `st += fract(uTime * vec2(${v[0]}, ${v[1]}));`;
      if (kind === 'scale') code += `st *= vec2(${v[0]}, ${v[1]});`;
      if (kind === 'transform') code += `st = vec2(st.x * ${v[0]} + st.y * ${v[2]} + ${v[4]}, st.x * ${v[1]} + st.y * ${v[3]} + ${v[5]});`;
      if (kind === 'rotate') code += `{ float a = -${v[0]} * uTime * .01745329, c = cos(a), s = sin(a); st = vec2(st.x * c - st.y * s + .5 - .5 * c + .5 * s, st.x * s + st.y * c + .5 - .5 * s - .5 * c); }`;
      if (kind === 'stretch') code += `{ float p = 1. / ${waveCall(values)}; st = st * p + .5 - .5 * p; }`;
      if (kind === 'turb') code += `st += sin((vec2(P.x + P.z, P.y) * .0009765625 + ${v[2]} + uTime * ${v[3]}) * 6.2831853) * ${v[1]};`;
    }
    return code;
  }
  // Colours are what the game writes before its gamma table doubles the picture: identity light is one half
  // (ArenaPrototype's NativeIdentityLightScale), and the frame is doubled after it is drawn.
  function colour(stage) {
    const [kind, ...values] = stage.rgbGen, [alphaKind, ...alphaValues] = stage.alphaGen || ['identity'];
    const rgb = kind === 'identitylighting' ? 'vec3(.5)' : kind === 'vertex' ? 'tint.rgb * .5' : kind === 'exactvertex' ? 'tint.rgb'
      : kind === 'oneminusvertex' ? '(1. - tint.rgb) * .5' : kind === 'wave' ? `vec3(clamp(${waveCall(values)} * .5, 0., 1.))`
      : kind === 'const' ? `vec3(${values.map(number).join(', ')})` : kind === 'lightingdiffuse' ? 'vec3(.5)' : 'vec3(1.)';  // Models are lit evenly.
    const alpha = alphaKind === 'vertex' ? 'tint.a' : alphaKind === 'oneminusvertex' ? '1. - tint.a' : alphaKind === 'wave' ? `clamp(${waveCall(alphaValues)}, 0., 1.)`
      : alphaKind === 'const' ? number(alphaValues[0]) : alphaKind === 'lightingspecular' ? 'specular(P, N)'
      : alphaKind === 'portal' ? `clamp(length(P - uView) / ${number(alphaValues[0])}, 0., 1.)` : '1.';
    return `vec4(${rgb}, ${alpha})`;
  }
  const FACTORS = {gl_one: THREE.OneFactor, gl_zero: THREE.ZeroFactor, gl_src_color: THREE.SrcColorFactor, gl_one_minus_src_color: THREE.OneMinusSrcColorFactor,
    gl_dst_color: THREE.DstColorFactor, gl_one_minus_dst_color: THREE.OneMinusDstColorFactor, gl_src_alpha: THREE.SrcAlphaFactor,
    gl_one_minus_src_alpha: THREE.OneMinusSrcAlphaFactor, gl_dst_alpha: THREE.DstAlphaFactor, gl_one_minus_dst_alpha: THREE.OneMinusDstAlphaFactor,
    gl_src_alpha_saturate: THREE.SrcAlphaSaturateFactor};
  // The game's front faces wind clockwise, Three's the other way.
  const SIDES = {front: THREE.BackSide, back: THREE.FrontSide, none: THREE.DoubleSide};
  // deformVertexes wave, move and bulge (RB_CalcDeformVertexes, RB_CalcMoveVertexes, RB_CalcBulgeVertexes), applied
  // as the game applies them: before the stages' texture coordinates and colours are worked out.
  const deform = shader => (shader.deforms || []).map(([kind, ...values]) => {
    if (kind === 'bulge') return `P += N * sin(uv.x * ${number(values[0])} + uTime * ${number(values[2])}) * ${number(values[1])};`;
    if (kind === 'move') return `P += vec3(${values.slice(0, 3).map(number).join(', ')}) * ${waveCall(values.slice(3))};`;
    const [spread, func, base, amplitude, phase, frequency] = values;  // A wave that does not move is one height everywhere.
    return `P += N * wave(${WAVES[func] || 0}, ${number(base)}, ${number(amplitude)}, ${number(phase)}${frequency ? ` + (P.x + P.y + P.z) * ${number(spread)}` : ''}, ${number(frequency)});`;
  }).join(' ');
  const animated = [];
  function stageMaterial(shader, stage, texture, frames) {
    const sky = shader.sky, st = coordinates(stage, sky);
    const test = {gt0: 'if (c.a <= 0.) discard;', lt128: 'if (c.a >= .5) discard;', ge128: 'if (c.a < .5) discard;'}[stage.alphaFunc] || '';
    const material = new THREE.ShaderMaterial({
      uniforms: {...uniforms, map: {value: texture}},
      vertexShader: `invariant gl_Position;
        ${WAVE}
        uniform vec3 uView; attribute vec2 lm; attribute vec4 tint; varying vec2 vSt; varying vec4 vColor; varying vec3 vDir;
        float specular(vec3 P, vec3 N) {  // RB_CalcSpecularAlpha, with the game's one fixed light.
          vec3 light = normalize(vec3(-960., 1980., 96.) - P), turned = N * 2. * dot(N, light) - light;
          float level = max(dot(turned, normalize(uView - P)), 0.);
          return min(level * level * level * level, 1.);
        }
        void main() {
          // In the game's axes wherever the mesh stands: the world as it is, a pickup turned and lifted.
          vec4 placed = modelMatrix * vec4(position, 1.);
          vec3 facing = mat3(modelMatrix) * normal, P = vec3(placed.x, -placed.z, placed.y), N = normalize(vec3(facing.x, -facing.z, facing.y));
          ${deform(shader)}
          ${sky ? '' : st + ' vSt = st;'}
          vColor = ${colour(stage)};
          vDir = P - uView;
          gl_Position = projectionMatrix * viewMatrix * vec4(P.x, P.z, -P.y, 1.);
        }`,
      fragmentShader: `${WAVE}
        uniform sampler2D map; varying vec2 vSt; varying vec4 vColor; varying vec3 vDir;
        void main() {
          ${sky ? 'vec3 P = vDir, N = vec3(0., 0., 1.); ' + st : 'vec2 st = vSt;'}
          vec4 c = texture2D(map, st) * vColor;
          ${test}
          gl_FragColor = c;
        }`,
      side: SIDES[shader.cull] || THREE.BackSide, transparent: true, depthWrite: !!stage.depthWrite,
      depthFunc: stage.depthFunc === 'equal' ? THREE.EqualDepth : THREE.LessEqualDepth,
      blending: stage.blend ? THREE.CustomBlending : THREE.NoBlending,
      blendSrc: stage.blend ? FACTORS[stage.blend[0]] || THREE.OneFactor : THREE.OneFactor,
      blendDst: stage.blend ? FACTORS[stage.blend[1]] || THREE.OneFactor : THREE.ZeroFactor,
      polygonOffset: !!shader.polygonOffset, polygonOffsetFactor: -1, polygonOffsetUnits: -2});
    if (frames && frames.length > 1) animated.push({material, frames, fps: stage.fps});
    return material;
  }
  // The six sides of a sky box, placed as the game's MakeSkyVec places them.
  function boxMaterial(sides) {
    const names = ['rt', 'bk', 'lf', 'ft', 'up', 'dn'];
    return new THREE.ShaderMaterial({
      uniforms: {...uniforms, ...Object.fromEntries(names.map((name, index) => [name, {value: sides[index]}]))},
      vertexShader: 'invariant gl_Position; uniform vec3 uView; varying vec3 vDir; void main() { vDir = position - uView; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }',
      fragmentShader: `uniform sampler2D rt, bk, lf, ft, up, dn; varying vec3 vDir;
        vec4 side(sampler2D image, vec2 p) { return texture2D(image, vec2(p.x + 1., 1. - p.y) * .5); }
        void main() {
          vec3 d = vDir, a = abs(d);
          vec4 c = a.x >= a.y && a.x >= a.z ? (d.x > 0. ? side(rt, vec2(-d.y, d.z) / a.x) : side(lf, vec2(d.y, d.z) / a.x))
            : a.y >= a.z ? (d.y > 0. ? side(bk, vec2(d.x, d.z) / a.y) : side(ft, vec2(-d.x, d.z) / a.y))
            : (d.z > 0. ? side(up, vec2(-d.y, -d.x) / a.z) : side(dn, vec2(-d.y, d.x) / a.z));
          gl_FragColor = vec4(c.rgb * .5, 1.);
        }`,
      side: THREE.DoubleSide, transparent: true, blending: THREE.NoBlending, depthWrite: true});
  }

  // Draw lumps of the map file (IBSP 46): models 7, vertices 10, indices 11, faces 13, lightmaps 14.
  function buildWorld(buffer) {
    const view = new DataView(buffer);
    if (view.getUint32(0, true) !== 0x50534249 || view.getInt32(4, true) !== 46) throw new Error('not a Quake 3 map file');
    const lump = (index, size) => ({at: view.getInt32(8 + index * 8, true), count: Math.floor(view.getInt32(12 + index * 8, true) / size)});
    const models = lump(7, 40), vertices = lump(10, 44), indexes = lump(11, 4), faces = lump(13, 104), lightmaps = lump(14, 49152);
    const float = at => view.getFloat32(at, true), int = at => view.getInt32(at, true);

    // All lightmaps in one texture, so a shader is one draw whatever lightmaps its surfaces use.
    const columns = Math.max(1, Math.ceil(Math.sqrt(lightmaps.count))), rows = Math.max(1, Math.ceil(lightmaps.count / columns));
    const texels = new Uint8Array(columns * rows * 16384 * 4).fill(255), bytes = new Uint8Array(buffer);
    for (let index = 0; index < lightmaps.count; index++) for (let y = 0; y < 128; y++) for (let x = 0; x < 128; x++) {
      const from = lightmaps.at + index * 49152 + (y * 128 + x) * 3;
      shiftLight(bytes[from], bytes[from + 1], bytes[from + 2], texels, (((Math.floor(index / columns) * 128 + y) * columns + index % columns) * 128 + x) * 4);
    }
    const atlas = new THREE.DataTexture(texels, columns * 128, rows * 128);
    atlas.magFilter = atlas.minFilter = THREE.LinearFilter;
    atlas.needsUpdate = true;

    // Brush models an entity places away from where they were built (doors and lifts with an origin).
    const moved = new Map();
    for (const {model, origin} of map.models || []) if (model > 0 && model < models.count) {
      const at = models.at + model * 40;
      for (let face = int(at + 24), last = face + int(at + 28); face < last; face++) moved.set(face, origin);
    }

    const positions = [], normals = [], uvs = [], lms = [], tints = [], groups = new Map(), colour = [0, 0, 0];
    // One vertex: position, texture st, lightmap st, normal, colour. Lightmap st is kept half a texel inside its
    // cell, which is what clamping did when each lightmap was a texture of its own.
    function emit(values, origin, lightmap) {
      positions.push(values[0] + origin[0], values[1] + origin[1], values[2] + origin[2]);
      uvs.push(values[3], values[4]);
      if (lightmap < 0) lms.push(values[5], values[6]);
      else lms.push((lightmap % columns + Math.min(Math.max(values[5], 1 / 256), 255 / 256)) / columns,
        (Math.floor(lightmap / columns) + Math.min(Math.max(values[6], 1 / 256), 255 / 256)) / rows);
      const length = Math.hypot(values[7], values[8], values[9]) || 1;
      normals.push(values[7] / length, values[8] / length, values[9] / length);
      shiftLight(values[10], values[11], values[12], colour, 0);
      tints.push(colour[0] / 255, colour[1] / 255, colour[2] / 255, values[13] / 255);
    }
    const read = index => {
      const at = vertices.at + index * 44, values = [];
      for (let n = 0; n < 10; n++) values.push(float(at + n * 4));
      for (let n = 0; n < 4; n++) values.push(view.getUint8(at + 40 + n));
      return values;
    };
    const none = [0, 0, 0];
    for (let face = 0; face < faces.count; face++) {
      const at = faces.at + face * 104, shader = int(at), type = int(at + 8), first = int(at + 12), count = int(at + 16);
      if (!map.shaders[shader] || type < 1 || type > 3 || first < 0 || first + count > vertices.count) continue;
      const lightmap = int(at + 28) >= 0 && int(at + 28) < lightmaps.count ? int(at + 28) : -1, origin = moved.get(face) || none;
      const key = shader + (lightmap < 0 ? '|vertex' : '|lit');
      if (!groups.has(key)) groups.set(key, {shader: map.shaders[shader], lit: lightmap >= 0, indices: []});
      const indices = groups.get(key).indices, base = positions.length / 3;
      if (type === 2) {
        // A patch is a grid of quadratic Bézier spans, each drawn as 4 × 4 quads (ArenaPrototype's default).
        const width = int(at + 96), height = int(at + 100), steps = 4;
        if (width < 3 || height < 3 || !(width & 1) || !(height & 1) || width * height !== count) continue;
        const controls = Array.from({length: count}, (_, n) => read(first + n));
        const across = (width - 1) / 2 * steps + 1, down = (height - 1) / 2 * steps + 1;
        const weights = t => [(1 - t) * (1 - t), 2 * (1 - t) * t, t * t];
        for (let y = 0; y < down; y++) for (let x = 0; x < across; x++) {
          const spanX = Math.min(Math.floor(x / steps), (width - 1) / 2 - 1), spanY = Math.min(Math.floor(y / steps), (height - 1) / 2 - 1);
          const wu = weights(x / steps - spanX), wv = weights(y / steps - spanY), values = new Array(14).fill(0);
          for (let j = 0; j < 3; j++) for (let i = 0; i < 3; i++) {
            const control = controls[(spanY * 2 + j) * width + spanX * 2 + i], weight = wu[i] * wv[j];
            for (let n = 0; n < 14; n++) values[n] += control[n] * weight;
          }
          emit(values, origin, lightmap);
        }
        for (let y = 0; y < down - 1; y++) for (let x = 0; x < across - 1; x++) {
          const corner = base + y * across + x;
          indices.push(corner, corner + across, corner + across + 1, corner, corner + across + 1, corner + 1);
        }
      } else {
        const firstIndex = int(at + 20), indexCount = int(at + 24);
        if (firstIndex < 0 || firstIndex + indexCount > indexes.count) continue;
        for (let n = 0; n < count; n++) emit(read(first + n), origin, lightmap);
        for (let n = 0; n < indexCount; n++) {
          const offset = int(indexes.at + (firstIndex + n) * 4);
          indices.push(base + (offset >= 0 && offset < count ? offset : 0));
        }
      }
    }
    const attributes = {position: new THREE.Float32BufferAttribute(positions, 3), normal: new THREE.Float32BufferAttribute(normals, 3),
      uv: new THREE.Float32BufferAttribute(uvs, 2), lm: new THREE.Float32BufferAttribute(lms, 2), tint: new THREE.Float32BufferAttribute(tints, 4)};
    return {atlas, attributes, groups: [...groups.values()]};
  }

  // Stages of a surface without a script, as R_FindShader builds them: the image under the lightmap, or lit by
  // the vertex colours where the surface has no lightmap.
  const stagesOf = (shader, lit, model) => shader.default ? [{map: '$default', rgbGen: ['identitylighting'], depthWrite: true}]
    : !shader.implicit ? shader.stages
    : model ? [{map: shader.map, rgbGen: ['lightingdiffuse'], depthWrite: true}]
    : lit ? [{map: '$lightmap', tcGen: 'lightmap', rgbGen: ['identity'], depthWrite: true}, {map: shader.map, rgbGen: ['identity'], blend: ['gl_dst_color', 'gl_zero'], depthWrite: false}]
    : [{map: shader.map, rgbGen: ['exactvertex'], depthWrite: true}];
  const loading = new THREE.MeshBasicMaterial({color: 'rgb(0, 109, 56)', wireframe: true});
  // The materials of a shader's stages, once their textures are in; nothing for a shader that names none.
  async function stageMaterials(shader, stages, atlas) {
    const ready = await Promise.all(stages.map(async stage => {
      if (stage.map === '$lightmap') return [atlas || white];
      if (stage.map === '$whiteimage') return [white];
      if (stage.map === '$default') return [fallback];
      return Promise.all((stage.frames || [stage.map]).map(file => loadTexture(file, stage.clamp)));
    }));
    return stages.map((stage, index) => ready[index][0] && stageMaterial(shader, stage, ready[index][0], ready[index].filter(Boolean)));
  }
  // One mesh of each opaque surface, for dropping pickups to the floor. They are in no scene, so a ray meets them
  // in the game's own axes.
  const floors = [], floorMaterial = new THREE.MeshBasicMaterial({side: THREE.DoubleSide});
  async function addGroup({shader, lit, indices}, {atlas, attributes}, serial) {
    const geometry = new THREE.BufferGeometry();
    Object.entries(attributes).forEach(([name, attribute]) => geometry.setAttribute(name, attribute));
    geometry.setIndex(indices);
    // The attributes hold the whole map, so the sphere that culls this surface is found over its own vertices.
    const centre = new THREE.Box3(), point = new THREE.Vector3();
    for (const index of indices) centre.expandByPoint(point.fromBufferAttribute(attributes.position, index));
    geometry.boundingSphere = centre.getBoundingSphere(new THREE.Sphere());
    // Until its textures arrive a surface is the green wireframe the T1 terrain shows while loading.
    const placeholder = new THREE.Mesh(geometry, loading);
    root.add(placeholder);
    const add = (material, stage) => {
      const mesh = new THREE.Mesh(geometry, material);
      mesh.renderOrder = shader.sort * 100000 + serial * 16 + stage;  // By sort, then shader by shader, stage after stage.
      mesh.name = shader.name;
      root.add(mesh);
    };
    {
      if (shader.sky && shader.sky.box) add(boxMaterial(await Promise.all(shader.sky.box.map(file => loadTexture(file, true, true)))), 0);
      // ponytail: one draw per stage, as the game without multitexture; fold lightmap × texture into one pass if maps get heavy.
      (await stageMaterials(shader, stagesOf(shader, lit) || [], lit && atlas)).forEach((material, index) => { if (material) add(material, index + 1); });
    }
    root.remove(placeholder);
    if (shader.sort === 3 && !shader.sky) floors.push(new THREE.Mesh(geometry, floorMaterial));
  }

  // Pickups, shown as the game's cg_ents.c CG_Item shows them: turning once in 2.048 seconds (health twice as
  // fast), bobbing 4 ± 4 units, weapons half as large again and turning about their middle, and the second model
  // of a health item or powerup turning the other way, a powerup's 12 units up.
  const itemModels = new Map(), pickups = [];
  function loadItemModel(path) {
    if (!itemModels.has(path)) itemModels.set(path, (async () => {
      const model = map.itemModels[path], surfaces = await (await get(data + model.file)).json();
      return Promise.all(surfaces.map(async (surface, index) => {
        const geometry = new THREE.BufferGeometry(), shader = model.shaders[index];
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(surface.vertices, 3));
        geometry.setAttribute('normal', new THREE.Float32BufferAttribute(surface.normals, 3));
        geometry.setAttribute('uv', new THREE.Float32BufferAttribute(surface.uvs, 2));
        geometry.setIndex(surface.indices);
        const materials = (await stageMaterials(shader, stagesOf(shader, false, true) || [])).filter(Boolean);
        return {geometry, materials, sort: shader.sort};
      }));
    })());
    return itemModels.get(path);
  }
  async function addItem(item, serial) {
    const models = await Promise.all(item.models.map(loadItemModel)), holder = new THREE.Group(), weapon = item.kind === 'weapon';
    const [x, y, z] = item.origin;
    let height = z;
    if (!item.suspended) {
      // The game drops the item's 30-unit box until it rests on something solid; here one line from its centre
      // down to the nearest opaque surface stands in for that, and its centre comes to rest 15 above it.
      const hit = new THREE.Raycaster(new THREE.Vector3(x, y, z), new THREE.Vector3(0, 0, -1), 0, 4096).intersectObjects(floors, false)[0];
      if (hit) height = hit.point.z + 15;
    }
    holder.position.set(x, y, height);
    models.forEach((surfaces, which) => {
      const part = new THREE.Group();
      for (const {geometry, materials, sort} of surfaces) materials.forEach((material, stage) => {
        const mesh = new THREE.Mesh(geometry, material);
        mesh.renderOrder = sort * 100000 + 99000 + stage;
        if (weapon) mesh.position.fromArray(map.itemModels[item.models[which]].middle).negate();
        part.add(mesh);
      });
      if (weapon) part.scale.setScalar(1.5);
      if (which) part.position.z = item.kind === 'powerup' ? 12 : 0;
      holder.add(part);
    });
    root.add(holder);
    pickups.push({holder, height, fast: item.kind === 'health', rate: 5 + serial * .01});
  }

  function showNotes() {
    if (!map) return;
    const parts = [];
    if (map.unresolved.length) parts.push("Not in the game files, so drawn with the game's dark default image as the game draws them: " + map.unresolved.join('; '));
    if ((map.outside || []).length) parts.push('Filled in from your extras folder, not the game files: ' + map.outside.join('; '));
    if ((map.guessed || []).length) parts.push('Guessed from a file of the same name in another folder, which may not be the same thing: ' + map.guessed.join('; '));
    if (map.limits.length) parts.push('Not drawn or simplified: ' + map.limits.join('; '));
    if (missing) parts.push(`${missing} textures of the pack did not load`);
    if (failures.length) parts.push(failures.join('; '));
    $('mapNotes').textContent = parts.length ? ' This map — ' + parts.join('. ') + '.' : '';
  }
  $('notes').after(Object.assign(document.createElement('span'), {id: 'mapNotes'}));

  function applySettings() {
    for (const id of ['animate', 'invertX', 'invertY']) $(id).checked = settings[id];
    $('fov').value = settings.fov;
    const main = canvas.parentElement, aspect = main.clientWidth / Math.max(main.clientHeight, 1);
    renderer.setSize(main.clientWidth, main.clientHeight, false);
    frame.setSize(...renderer.getDrawingBufferSize(new THREE.Vector2()).toArray());
    camera.aspect = aspect;
    // The game's field of view is horizontal; Three's is vertical.
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2) / aspect));
    camera.updateProjectionMatrix();
  }
  function showViewpoint(index) {
    const view = map && map.viewpoints[index];
    if (!view) return;
    const [x, y, z] = view.origin;
    camera.position.set(x, z, -y);
    let yaw = view.yaw, pitch = view.pitch;  // Yaw turns from +x toward +y; pitch is positive downward.
    if (view.target) {
      const [dx, dy, dz] = view.target.map((value, axis) => value - view.origin[axis]);
      yaw = THREE.MathUtils.radToDeg(Math.atan2(dy, dx));
      pitch = -THREE.MathUtils.radToDeg(Math.atan2(dz, Math.hypot(dx, dy)));
    }
    camera.rotation.set(-THREE.MathUtils.degToRad(pitch), THREE.MathUtils.degToRad(yaw - 90), 0);
  }

  $('fov').addEventListener('change', event => { settings.fov = Math.max(30, Math.min(130, Number(event.target.value) || 90)); save(); applySettings(); });
  for (const id of ['animate', 'invertX', 'invertY']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
  $('reset').addEventListener('click', () => { speed = 400; showViewpoint(0); if (ready) showReady(); });
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
    if (/^Digit[1-9]$/.test(event.code)) showViewpoint(Number(event.code.slice(5)) - 1);
    if (/^(Key[WASD]|Space|Shift(Left|Right))$/.test(event.code)) { keys.add(event.code); event.preventDefault(); }
  });
  document.addEventListener('keyup', event => keys.delete(event.code));

  // Stages are drawn into a frame that keeps alpha, which some blends read back (GL_ONE_MINUS_DST_ALPHA), and the
  // frame is shown doubled, as the game's gamma table doubles what the stages drew (r_overBrightBits 1).
  const frame = new THREE.WebGLRenderTarget(1, 1, {samples: 4}), shown = new THREE.Scene(), shownCamera = new THREE.Camera();
  shown.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({uniforms: {frame: {value: frame.texture}}, depthTest: false, depthWrite: false,
    vertexShader: 'varying vec2 at; void main() { at = uv; gl_Position = vec4(position.xy, 0., 1.); }',
    fragmentShader: 'uniform sampler2D frame; varying vec2 at; void main() { gl_FragColor = vec4(texture2D(frame, at).rgb * 2., 1.); }'})));
  shown.children[0].frustumCulled = false;
  function draw(seconds) {
    uniforms.uTime.value = seconds;
    uniforms.uView.value.set(camera.position.x, -camera.position.z, camera.position.y);
    for (const {material, frames, fps} of animated) material.uniforms.map.value = frames[Math.floor(seconds * fps) % frames.length];
    for (const {holder, height, fast, rate} of pickups) {
      holder.position.z = height + 4 + Math.cos((seconds + 1) * rate) * 4;
      holder.rotation.z = seconds % 2.048 / 2.048 * 2 * Math.PI * (fast ? 2 : 1);
      if (holder.children[1]) holder.children[1].rotation.z = -holder.rotation.z - seconds % 1.024 / 1.024 * 2 * Math.PI;  // Its own turn, undoing the holder's.
    }
    renderer.setRenderTarget(frame);
    renderer.render(scene, camera);
    renderer.setRenderTarget(null);
    renderer.render(shown, shownCamera);
  }
  const clock = new THREE.Clock(), forward = new THREE.Vector3(), right = new THREE.Vector3(), move = new THREE.Vector3();
  let shaderTime = 0;
  renderer.setAnimationLoop(() => {
    const delta = Math.min(clock.getDelta(), .1), held = code => Number(keys.has(code));
    camera.getWorldDirection(forward);
    right.crossVectors(forward, camera.up).normalize();
    move.copy(forward).multiplyScalar(held('KeyW') - held('KeyS')).addScaledVector(right, held('KeyD') - held('KeyA'));
    move.y += held('Space') - held('ShiftLeft') - held('ShiftRight');
    if (move.lengthSq()) camera.position.addScaledVector(move.normalize(), speed * delta);
    if (settings.animate) shaderTime += delta;
    draw(shaderTime);
  });
  window.skinnerQ3Maps = {renderer, scene, camera, draw};  // For checks in a hidden page, where no frame is drawn.

  const select = id => { location.search = '?map=' + encodeURIComponent(id); };
  $('map').addEventListener('change', event => select(event.target.value));
  $('import').addEventListener('click', async () => {
    const game = $('gamePath').value.trim();
    if (!game) { $('importStatus').textContent = 'Enter your Quake 3 folder.'; return; }
    $('import').disabled = true;
    $('importStatus').textContent = 'Importing maps… a full install takes about a minute.';
    try {
      const response = await fetch('/import_q3_maps', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({game, replace: $('replace').checked})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      try { localStorage.setItem(storageKey + '.game', game); } catch (_) { /* Path is simply not remembered. */ }
      const failed = Object.entries(result.failed).map(([name, reason]) => `${name}: ${reason}`), unresolved = Object.keys(result.unresolved);
      $('importStatus').textContent = `Imported ${result.imported.length}, skipped ${result.skipped.length} already imported` +
        (failed.length ? `, failed ${failed.length} (${failed.join('; ')})` : '') +
        (unresolved.length ? `. ${unresolved.length} name files the game does not have (see their Preview notes): ${unresolved.join(', ')}` : '') + '.';
      if (result.imported.length && !failed.length && !map) location.reload();
    } catch (error) { $('importStatus').textContent = error.message; }
    finally { $('import').disabled = false; }
  });
  try { $('gamePath').value = localStorage.getItem(storageKey + '.game') || ''; } catch (_) { /* Field stays empty. */ }

  try {
    let maps = [];
    try { maps = await (await get(data + 'index.json')).json(); } catch (_) { /* No pack yet. */ }
    if (!maps.length) { $('importPanel').open = true; throw new Error('no maps imported yet. Use Import maps above.'); }
    let mapId = new URLSearchParams(location.search).get('map') || '';
    if (!maps.some(item => item.id === mapId)) mapId = (maps.find(item => item.id === 'q3dm1') || maps[0]).id;
    const byGroup = new Map();
    for (const item of maps) byGroup.set(item.group, [...(byGroup.get(item.group) || []), item]);
    $('map').replaceChildren(...[...byGroup].map(([group, items]) => {
      const element = Object.assign(document.createElement('optgroup'), {label: group});
      element.append(...items.map(item => new Option(item.longname ? `${item.name} · ${item.longname}` : item.name, item.id)));
      return element;
    }));
    $('map').value = mapId;
    map = await (await get(`${data}maps/${mapId}/scene.json`)).json();
    document.title = `${map.name} — Q3 Maps`;
    showNotes();
    applySettings();
    showViewpoint(0);
    showStatus('Loading map…');
    const world = buildWorld(await (await get(data + map.bsp)).arrayBuffer());
    if (!world.groups.length) failures.push('the map file holds no surfaces to draw');
    let loaded = 0;
    await Promise.all(world.groups.map(async (group, serial) => { await addGroup(group, world, serial); showStatus(`Loading surfaces ${++loaded}/${world.groups.length}`); }));
    showStatus('Placing pickups…');
    root.updateMatrixWorld(true);
    await Promise.all((map.items || []).map(addItem));
    showNotes();
    ready = true;
    showReady();
  } catch (error) { showStatus('Map could not load: ' + error.message); }
});

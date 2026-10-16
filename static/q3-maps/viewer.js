/* Vanilla Three.js free-flight viewer for the pack written by tools/import_q3_map.py.
   Geometry keeps the game's own axes (x, y, z up) under a root turned to Three's (x, z, -y).
   What is drawn follows ArenaPrototype's Quake3StaticWorldScene and Quake3StaticStage shader. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const config = window.skinnerMapConfig || {};
  const storageKey = config.storageKey || 'skinner.q3maps';
  // Every engine of the family draws the sky at the far limit of depth (glDepthRange 1, 1).
  const skyAtFarDepth = config.skyAtFarDepth ?? true;
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
  // NPC visibility belongs to a parent of the adapter's holder: JA rewrites
  // holder.visible for distance culling every frame. Shared geometry stays shared.
  const npcToggle = window.skinnerNPCToggle;
  const npcHolders = [], npcEntities = new Set();
  let npcLoadFailed = false;
  npcToggle?.setCount(0);
  if (npcToggle) npcToggle.onChange = enabled => npcHolders.forEach(holder => { holder.visible = enabled; });
  const trackNPC = (item, holder) => {
    if (npcLoadFailed || !item.npc || !holder?.children.length) return;
    const visibility = new THREE.Group();
    visibility.visible = npcToggle?.enabled ?? true;
    visibility.add(holder);root.add(visibility);
    npcHolders.push(visibility);npcEntities.add(item.entity);
    npcToggle?.setCount(npcEntities.size);
  };
  const camera = new THREE.PerspectiveCamera(60, 1, 2, 60000);
  camera.rotation.order = 'YXZ';
  const data = config.data || '/q3-map-data/';
  let speed = 400, missing = 0, ready = false, map = null, visibility = null;
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
  if (config.defaultTexels) fallbackTexels.set(config.defaultTexels());
  const fallback = new THREE.DataTexture(fallbackTexels, 16, 16);
  fallback.wrapS = fallback.wrapT = THREE.RepeatWrapping;
  fallback.generateMipmaps = true;
  fallback.minFilter = THREE.LinearMipmapLinearFilter;
  fallback.magFilter = THREE.LinearFilter;
  fallback.needsUpdate = true;
  function loadTexture(file, clamp, plain) {
    if (file === '$default') return Promise.resolve(fallback);
    const key = file + (clamp ? '|clamp' : '') + (plain ? '|plain' : '');
    if (!textures.has(key)) textures.set(key, new Promise(resolve => loader.load(data + 'textures/' + file, texture => {
      texture.flipY = false;  // The game's t runs down the image, as the rows do without the flip.
      if (plain) { texture.generateMipmaps = false; texture.minFilter = THREE.LinearFilter; } else texture.anisotropy = 8;
      if (config.configureTexture) config.configureTexture(texture, map && map.lighting, plain);
      if (!clamp) texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      resolve(texture);
    }, undefined, () => { missing++; resolve(null); })));
    return textures.get(key);
  }

  // The game brightens light by one bit and, where a channel overflows, scales the colour down to keep its hue
  // (R_ColorShiftLightingBytes with the default r_mapOverBrightBits 2 and r_overBrightBits 1).
  function shiftLight(r, g, b, out, at) {
    const factor = map && map.lighting ? map.lighting.lightScale : 2;
    r *= factor; g *= factor; b *= factor;
    const most = Math.max(r, g, b);
    if (most > 255) { r = r * 255 / most; g = g * 255 / most; b = b * 255 / most; }
    out[at] = r; out[at + 1] = g; out[at + 2] = b;
  }

  // Shader time and the camera in the game's axes, shared by every stage material.
  const uniforms = {uTime: {value: 0}, uView: {value: new THREE.Vector3()}, uForward: {value: new THREE.Vector3(1, 0, 0)},
    uViewLeft: {value: new THREE.Vector3(0, 1, 0)}, uViewUp: {value: new THREE.Vector3(0, 0, 1)}, uTanFov: {value: 1}};
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
      : stage.tcGenVectors ? `vec2 st = vec2(dot(P, vec3(${stage.tcGenVectors.slice(0, 3).map(number).join(', ')})), dot(P, vec3(${stage.tcGenVectors.slice(3, 6).map(number).join(', ')})));`
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
    const identity = number(map && map.lighting ? map.lighting.identityLight : .5);
    const rgb = kind === 'identitylighting' ? `vec3(${identity})` : kind === 'vertex' ? `tint.rgb * ${identity}` : kind === 'exactvertex' ? 'tint.rgb'
      : kind === 'oneminusvertex' ? `(1. - tint.rgb) * ${identity}` : kind === 'wave' ? `vec3(clamp(${waveCall(values)} * ${identity}, 0., 1.))`
      : kind === 'const' ? `vec3(${values.map(number).join(', ')})` : kind === 'lightingdiffuse' ? 'diffuseLight(N)' : 'vec3(1.)';
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
    // A light flare drawn as id's renderer draws it (RB_AddFlare, RB_RenderFlare): a square r_flareSize 40 / 640 of
    // the view's width (times the flare's scale) plus 8 / depth across either side of its middle, dimmed as the
    // surface turns away. It is hidden where the game's test hides it: the view's depth more than `pull` in
    // front of it (RB_TestFlare; the frame's depth test stands in for the read back), and for coronas past
    // cg_coronafardist or outside the view cone (CG_Corona). A zero normal (a corona) never dims.
    if (kind === 'flare') {
      const [pull, cone, far] = values, cosine = cone === 'fov' ? 'inversesqrt(1. + uTanFov * uTanFov)' : number(cone);
      return `{ vec3 toEye = uView - sprite.xyz; float dist = max(length(toEye), .001); toEye /= dist;
        float ahead = -dot(toEye, uForward), facing = dot(normal, normal) > .5 ? dot(toEye, normal) : 1.;
        float depth = max(dist * ahead, 1.), pulled = max(depth - ${number(pull)} * ahead, 1.);
        float spread = (40. / 640. * sprite.w + 8. / depth) * 2. * pulled * uTanFov;
        P = facing < 0. || ahead <= ${cosine} || dist > ${number(far)} ? uView - uForward
          : sprite.xyz + toEye * ${number(pull)} + ((1. - 2. * uv.x) * uViewLeft + (1. - 2. * uv.y) * uViewUp) * spread;
        vertexTint.rgb *= max(facing, 0.); N = -uForward; }`;
    }
    // Raven's flare surface (RB_SurfaceFlare): a card of its own shader 3 units out from the surface, 30 units
    // (or the shader's portal range) across either side, shrinking nearer than 512, as bright as it faces the view.
    if (kind === 'flarecard') return `if (sprite.w > 0.) { vec3 dir = sprite.xyz - uView; float dist = max(length(dir), .001);
      vertexTint = vec4(vec3(abs(dot(dir / dist, normal))), 1.);
      P = sprite.xyz + ((1. - 2. * uv.x) * uViewLeft + (1. - 2. * uv.y) * uViewUp) * max(sprite.w * min(dist / 512., 1.), 5.); N = -uForward; }`;
    // A vertex the import gave no sprite data (w 0) is left where it is.
    if (kind === 'autosprite') return 'if (sprite.w > 0.) { P = sprite.xyz + ((1. - 2. * uv.x) * uViewLeft + (1. - 2. * uv.y) * uViewUp) * sprite.w; N = -uForward; }';
    if (kind === 'autosprite2') return 'if (sprite.w != 0.) P = sprite.xyz + normalize(cross(spriteAxis, uForward)) * sprite.w;';
    if (kind === 'bulge') return `P += N * sin(uv.x * ${number(values[0])} + uTime * ${number(values[2])}) * ${number(values[1])};`;
    if (kind === 'move') return `P += vec3(${values.slice(0, 3).map(number).join(', ')}) * ${waveCall(values.slice(3))};`;
    const [spread, func, base, amplitude, phase, frequency] = values;  // A wave that does not move is one height everywhere.
    return `P += N * wave(${WAVES[func] || 0}, ${number(base)}, ${number(amplitude)}, ${number(phase)}${frequency ? ` + (P.x + P.y + P.z) * ${number(spread)}` : ''}, ${number(frequency)});`;
  }).join(' ');
  // Fog. x is the depth inside the fog over its depth for opaque, worked out per vertex as RB_CalcFogTexCoords
  // does (s: depth along the view; t: the share of the ray past the fog's visible plane, 0 for a point outside it,
  // which interpolates as the game's fog texture coordinates do); the curve that turns x into opacity is the
  // engine's: the fog image's square root (R_FogFactor), CNQ3's 1 - 2^-10x, ET's linear image, and GL fog's
  // exp2 (JA global fog, r_drawfog 2), linear and exp (RTCW and ET fogvars, which use the radial distance).
  const FOG_CURVES = {sqrt: 'sqrt(clamp(x, 0., 1.))', cnq3: '1. - exp2(-10. * x)', linear: 'clamp(x, 0., 1.)', exp2: '1. - exp(-5.5413 * x * x)', exp: '1. - exp(-x)'};
  function fogVertex(fog, sky) {
    const [nx, ny, nz, w] = (fog.plane || []).map(number);
    // A sky is fogged where the game puts its clouds, a box zFar / 1.75 away.
    const metric = sky ? number((map.farClip || camera.far) / 1.75) : fog.radial ? 'length(P - uView)' : 'dot(P - uView, uForward)';
    return `vFogS = ${metric} * ${number(fog.scale)};
      ${fog.plane ? `float eyeT = ${w} - dot(vec3(${nx}, ${ny}, ${nz}), uView), fogT = ${w} - dot(vec3(${nx}, ${ny}, ${nz}), P);
      vFogT = eyeT < 0. ? (fogT < 1. ? 0. : fogT / (fogT - eyeT)) : (fogT < 0. ? 0. : 1.);` : 'vFogT = 1.;'}`;
  }
  const fogAlpha = fog => `float x = vFogS * vFogT, fogAlpha = ${FOG_CURVES[fog.curve]};`;
  // A stage under GL fog is fogged towards the colour that leaves its blend as fogged as one pass would be: black
  // for an added stage, white for a multiplying one (JA's mGLFogColorOverride), else the fog's colour.
  const glFogTarget = (stage, fog) => {
    const blend = String(stage.blend || '');
    return blend === 'gl_one,gl_one' ? 'vec3(0.)' : blend === 'gl_dst_color,gl_zero' || blend === 'gl_zero,gl_src_color' ? 'vec3(1.)' : fogColour(fog);
  };
  const fogColour = fog => `vec3(${fog.color.map(value => number(value * (map.lighting ? map.lighting.identityLight : .5))).join(', ')})`;
  const animated = [];
  function stageMaterial(shader, stage, texture, frames) {
    const sky = shader.sky, st = coordinates(stage, sky);
    const foliage = config.foliage && shader.distanceCull;
    const fade = foliage ? `if (foliageOrigin.w > 0.) {
      float z = -(viewMatrix * vec4(foliageOrigin.x, foliageOrigin.z, -foliageOrigin.y, 1.)).z * uFovScale;
      float alpha = (${number(foliage[1])} - z * foliageOrigin.w) / ${number(foliage[1] - foliage[0])};
      vFoliage = (z < -64. || alpha < ${number(foliage[2])}) ? 0. : 1.;
      vertexTint.a = clamp(alpha, 0., 1.);
    }` : '';
    const test = {gt0: 'if (c.a <= 0.) discard;', lt128: 'if (c.a >= .5) discard;', ge128: 'if (c.a < .5) discard;'}[stage.alphaFunc] || '';
    // A model's light from the light grid, set per object by useLight; until then, or without a grid, it is lit evenly.
    const diffuse = stage.rgbGen[0] === 'lightingdiffuse', identity = map && map.lighting ? map.lighting.identityLight : .5;
    // A fog pass (RB_FogPass) draws the fog's colour at its opacity; a stage under GL fog is fogged itself.
    const fog = stage.fogPass || stage.glFog;
    const material = new THREE.ShaderMaterial({
      uniforms: {...uniforms, map: {value: texture}, ...(foliage ? {uFovScale: {value: settings.fov / 90}} : {}),
        ...(diffuse ? {uAmbient: {value: new THREE.Vector3().setScalar(identity)}, uDirected: {value: new THREE.Vector3()}, uLightDir: {value: new THREE.Vector3(0, 0, 1)}} : {})},
      vertexShader: `invariant gl_Position;
        ${WAVE}
        ${foliage ? 'uniform float uFovScale; attribute vec4 foliageOrigin; varying float vFoliage;' : ''}
        ${diffuse ? `uniform vec3 uAmbient, uDirected, uLightDir;  // RB_CalcDiffuseColor
        vec3 diffuseLight(vec3 N) { return min(uAmbient + max(dot(N, uLightDir), 0.) * uDirected, 1.); }` : ''}
        uniform vec3 uView, uForward; attribute vec2 lm; attribute vec4 tint; varying vec2 vSt; varying vec4 vColor; varying vec3 vDir;
        ${fog ? 'varying float vFogS, vFogT;' : ''}
        ${(shader.deforms || []).some(([kind]) => kind.startsWith('autosprite') || kind.startsWith('flare')) ? 'attribute vec4 sprite; attribute vec3 spriteAxis; uniform vec3 uViewLeft, uViewUp; uniform float uTanFov;' : ''}
        float specular(vec3 P, vec3 N) {  // RB_CalcSpecularAlpha, with the game's one fixed light.
          vec3 light = normalize(vec3(-960., 1980., 96.) - P), turned = N * 2. * dot(N, light) - light;
          float level = max(dot(turned, normalize(uView - P)), 0.);
          return min(level * level * level * level, 1.);
        }
        void main() {
          // In the game's axes wherever the mesh stands: the world as it is, a pickup turned and lifted.
          vec4 placed = modelMatrix * vec4(position, 1.);
          vec3 facing = mat3(modelMatrix) * normal, P = vec3(placed.x, -placed.z, placed.y), N = normalize(vec3(facing.x, -facing.z, facing.y));
          vec4 vertexTint = tint;
          ${deform(shader)}
          ${sky ? '' : st + ' vSt = st;'}
          ${foliage ? 'vFoliage = 1.;' + fade : ''}
          vColor = ${colour(stage).replace(/\btint\b/g, 'vertexTint')};
          vDir = P - uView;
          ${fog ? fogVertex(fog, sky) : ''}
          gl_Position = projectionMatrix * viewMatrix * vec4(P.x, P.z, -P.y, 1.);
          ${(sky || shader.atFar) && skyAtFarDepth ? 'gl_Position.z = gl_Position.w;' : ''}
        }`,
      fragmentShader: `${WAVE}
        ${foliage ? 'varying float vFoliage;' : ''}
        uniform sampler2D map; varying vec2 vSt; varying vec4 vColor; varying vec3 vDir;
        ${fog ? 'varying float vFogS, vFogT;' : ''}
        void main() {
          ${sky ? 'vec3 P = vDir, N = vec3(0., 0., 1.); ' + st : 'vec2 st = vSt;'}
          ${foliage ? 'if (vFoliage < .5) discard;' : ''}
          vec4 c = texture2D(map, st) * vColor;
          ${test}
          ${fog ? fogAlpha(fog) : ''}
          ${stage.fogPass ? `c = vec4(${fogColour(fog)}, fogAlpha);` : stage.glFog ? `c.rgb = mix(c.rgb, ${glFogTarget(stage, fog)}, fogAlpha);` : ''}
          gl_FragColor = c;
        }`,
      side: SIDES[shader.cull] || THREE.BackSide, transparent: true, depthWrite: !!stage.depthWrite,
      depthFunc: stage.depthFunc === 'equal' ? THREE.EqualDepth : THREE.LessEqualDepth,
      blending: stage.blend ? THREE.CustomBlending : THREE.NoBlending,
      blendSrc: stage.blend ? FACTORS[stage.blend[0]] || THREE.OneFactor : THREE.OneFactor,
      blendDst: stage.blend ? FACTORS[stage.blend[1]] || THREE.OneFactor : THREE.ZeroFactor,
      polygonOffset: !!shader.polygonOffset, polygonOffsetFactor: -1, polygonOffsetUnits: -2});
    material.userData = {shader, stage, texture, frames};  // For fogging models.
    if (foliage) material.onBeforeRender = () => { material.uniforms.uFovScale.value = settings.fov / 90; };
    if (frames && frames.length > 1) animated.push({material, frames, fps: stage.fps});
    return material;
  }
  // The six sides of a sky box, placed as the game's MakeSkyVec places them. The inner box of RTCW and ET is
  // blended over the clouds by its alpha (DrawSkySideInner).
  function boxMaterial(sides, inner = false) {
    const names = ['rt', 'bk', 'lf', 'ft', 'up', 'dn'];
    return new THREE.ShaderMaterial({
      uniforms: {...uniforms, ...Object.fromEntries(names.map((name, index) => [name, {value: sides[index]}]))},
      vertexShader: `invariant gl_Position; uniform vec3 uView; varying vec3 vDir; void main() { vDir = position - uView; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); ${skyAtFarDepth ? 'gl_Position.z = gl_Position.w;' : ''} }`,
      fragmentShader: `uniform sampler2D rt, bk, lf, ft, up, dn; varying vec3 vDir;
        vec4 side(sampler2D image, vec2 p) { return texture2D(image, vec2(p.x + 1., 1. - p.y) * .5); }
        void main() {
          vec3 d = vDir, a = abs(d);
          vec4 c = a.x >= a.y && a.x >= a.z ? (d.x > 0. ? side(rt, vec2(-d.y, d.z) / a.x) : side(lf, vec2(d.y, d.z) / a.x))
            : a.y >= a.z ? (d.y > 0. ? side(bk, vec2(d.x, d.z) / a.y) : side(ft, vec2(-d.x, d.z) / a.y))
            : (d.z > 0. ? side(up, vec2(-d.y, -d.x) / a.z) : side(dn, vec2(-d.y, d.x) / a.z));
          gl_FragColor = vec4(c.rgb * ${number(map && map.lighting ? map.lighting.identityLight : .5)}, ${inner ? 'c.a' : '1.'});
        }`,
      side: THREE.DoubleSide, transparent: true, depthWrite: !inner,
      ...(inner ? {blending: THREE.CustomBlending, blendSrc: THREE.SrcAlphaFactor, blendDst: THREE.OneMinusSrcAlphaFactor} : {blending: THREE.NoBlending})});
  }

  // Draw lumps of the map file (IBSP 46): models 7, vertices 10, indices 11, faces 13, lightmaps 14.
  function buildWorld(buffer, externalAtlas = null) {
    const view = new DataView(buffer);
    if (view.getUint32(0, true) !== 0x50534249 || view.getInt32(4, true) !== (config.bspVersion || 46)) throw new Error(config.bspVersion === 47 ? 'not an RTCW map file' : 'not a Quake 3 map file');
    const lump = (index, size) => ({at: view.getInt32(8 + index * 8, true), count: Math.floor(view.getInt32(12 + index * 8, true) / size)});
    const models = lump(7, 40), vertices = lump(10, 44), indexes = lump(11, 4), faces = lump(13, 104), lightmaps = lump(14, 49152);
    const float = at => view.getFloat32(at, true), int = at => view.getInt32(at, true);

    // The light grid (lump 15): 8 bytes a cell over the world model's bounds, cells gridsize apart (R_LoadLightGrid).
    const grid = lump(15, 8), worldspawn = lump(0, 1);
    let lightGrid = null;
    if (grid.count && models.count) {
      const text = new TextDecoder('latin1').decode(new Uint8Array(buffer, worldspawn.at, worldspawn.count)).split('}')[0];
      const given = (/"gridsize"\s+"([^"]*)"/i.exec(text) || [])[1]?.trim().split(/\s+/).map(Number) || [];
      const size = given.length === 3 && given.every(value => value > 0) ? given : [64, 64, 128];
      const origin = size.map((step, i) => step * Math.ceil(float(models.at + i * 4) / step));
      const bounds = size.map((step, i) => Math.round((step * Math.floor(float(models.at + 12 + i * 4) / step) - origin[i]) / step) + 1);
      if (bounds[0] * bounds[1] * bounds[2] === grid.count) lightGrid = {origin, size, bounds, cells: new Uint8Array(buffer, grid.at, grid.count * 8)};
    }

    // All lightmaps in one texture, so a shader is one draw whatever lightmaps its surfaces use.
    const external = externalAtlas && map.lightmapAtlas;
    const columns = external ? external.columns : Math.max(1, Math.ceil(Math.sqrt(lightmaps.count))), rows = external ? external.rows : Math.max(1, Math.ceil(lightmaps.count / columns));
    const texels = new Uint8Array(external ? 0 : columns * rows * 16384 * 4).fill(255), bytes = new Uint8Array(buffer);
    for (let index = 0; !external && index < lightmaps.count; index++) for (let y = 0; y < 128; y++) for (let x = 0; x < 128; x++) {
      const from = lightmaps.at + index * 49152 + (y * 128 + x) * 3;
      shiftLight(bytes[from], bytes[from + 1], bytes[from + 2], texels, (((Math.floor(index / columns) * 128 + y) * columns + index % columns) * 128 + x) * 4);
    }
    const atlas = externalAtlas || new THREE.DataTexture(texels, columns * 128, rows * 128);
    atlas.magFilter = atlas.minFilter = THREE.LinearFilter;
    atlas.needsUpdate = true;

    // Brush models an entity places away from where they were built (doors and lifts with an origin).
    const moved = new Map();
    for (const {model, origin} of map.models || []) if (model > 0 && model < models.count) {
      const at = models.at + model * 40;
      for (let face = int(at + 24), last = face + int(at + 28); face < last; face++) moved.set(face, origin);
    }

    const positions = [], normals = [], uvs = [], lms = [], tints = [], groups = new Map(), colour = [0, 0, 0];
    const foliageOrigins = [], foliageFaces = new Map((config.foliage ? map.bspNotes || [] : []).filter(n => n.action === 'foliage instances expanded').map(n => [n.surface, n]));
    // Sprites (deformVertexes autosprite and autosprite2) keep, per vertex, what the deform needs: the quad's middle
    // and radius, or the middle of the vertex's short edge, its signed half length and the quad's long axis.
    const spriteOf = shader => shader && (shader.deforms || []).find(([kind]) => kind === 'autosprite' || kind === 'autosprite2')?.[0];
    // Flare surfaces (type 4): id's renderer draws its flare shader at each (RB_SurfaceFlare), Raven a card of the
    // surface's own shader; CNQ3, the Q3 page's engine, draws none (ParseFlare).
    const flareMode = typeof config.flareFaces === 'function' ? config.flareFaces(map) : config.flareFaces, flares = [], flareShaders = new Map();
    const sprites = map.shaders.some(spriteOf) || flareMode === 'world' ? [] : null, spriteAxes = [];
    // The faces of each moving brush model, drawn under that mover's own group.
    const moverOf = new Map();
    (map.movers || []).forEach(({model}, index) => {
      if (model > 0 && model < models.count) for (let face = int(models.at + model * 40 + 24), last = face + int(models.at + model * 40 + 28); face < last; face++) moverOf.set(face, index);
    });
    // One vertex: position, texture st, lightmap st, normal, colour. Lightmap st is kept half a texel inside its
    // cell, which is what clamping did when each lightmap was a texture of its own.
    function emit(values, origin, lightmap, foliage = [0, 0, 0, 0], sprite = [0, 0, 0, 0], axis = [0, 0, 0]) {
      if (config.foliage) foliageOrigins.push(...foliage);
      if (sprites) { sprites.push(...sprite); spriteAxes.push(...axis); }
      positions.push(values[0] + origin[0], values[1] + origin[1], values[2] + origin[2]);
      uvs.push(values[3], values[4]);
      if (lightmap < 0) lms.push(values[5], values[6]);
      else {
        const inset = 1 / (2 * (external ? external.size : 128));
        lms.push((lightmap % columns + Math.min(Math.max(values[5], inset), 1 - inset)) / columns,
          (Math.floor(lightmap / columns) + Math.min(Math.max(values[6], inset), 1 - inset)) / rows);
      }
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
      const fog = map.fogs && map.fogs[int(at + 4)] ? int(at + 4) : -1;
      if (type === 4 && flareMode) {
        // ParseFlare: the middle in lightmapOrigin, the colour in lightmapVecs[0], the normal in lightmapVecs[2].
        const [origin, colour, normal] = [48, 60, 84].map(offset => [0, 1, 2].map(axis => float(at + offset + axis * 4)));
        if (flareMode === 'screen') { flares.push({origin, colour, normal}); continue; }
        if (!map.shaders[shader] || !Math.hypot(...normal)) continue;  // A zero normal leaves the card black.
        const key = shader + '|flare|' + fog;
        if (!flareShaders.has(shader)) flareShaders.set(shader, {...map.shaders[shader], deforms: [...map.shaders[shader].deforms || [], ['flarecard']]});
        if (!groups.has(key)) groups.set(key, {shader: flareShaders.get(shader), lit: false, fog, indices: []});
        const base = positions.length / 3, middle = origin.map((value, axis) => value + 3 * normal[axis]);
        const portal = (map.shaders[shader].stages || []).find(stage => stage.alphaGen?.[0] === 'portal');
        for (let k = 0; k < 4; k++) emit([...middle, +(k === 1 || k === 2), +(k >= 2), 0, 0, ...normal, 255, 255, 255, 255], none, -1, undefined, [...middle, portal ? portal.alphaGen[1] : 30]);
        groups.get(key).indices.push(...[0, 1, 3, 3, 1, 2].map(n => base + n));
        continue;
      }
      if (!map.shaders[shader] || type < 1 || type > 3 || first < 0 || first + count > vertices.count) continue;
      const lightmap = (external ? external.valid.includes(int(at + 28)) : int(at + 28) >= 0 && int(at + 28) < lightmaps.count) ? int(at + 28) : -1, origin = moved.get(face) || none;
      const vertexLit = config.vertexLightmaps && int(at + 28) === -3, mover = moverOf.get(face);
      const key = shader + (lightmap < 0 ? config.vertexLightmaps && !vertexLit ? '|unlit' : '|vertex' : '|lit') + '|' + fog + (mover === undefined ? '' : '|m' + mover);
      if (!groups.has(key)) groups.set(key, {shader: map.shaders[shader], lit: lightmap >= 0, vertexLit, fog, mover, indices: [], ...(map.bspCull ? {ranges: []} : {})});
      const indices = groups.get(key).indices, base = positions.length / 3;
      const rangeStart = indices.length;
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
        const sprite = spriteOf(map.shaders[shader]);
        if (sprite && count % 4 === 0 && indexCount === count / 4 * 6) {
          // Independent quads, as the deforms expect (AutospriteDeform, Autosprite2Deform).
          const offsets = Array.from({length: indexCount}, (_, n) => int(indexes.at + (firstIndex + n) * 4));
          for (let quad = 0; quad < count / 4; quad++) {
            const corners = [0, 1, 2, 3].map(k => read(first + quad * 4 + k)), point = k => [0, 1, 2].map(axis => corners[k][axis] + origin[axis]);
            if (sprite === 'autosprite') {
              // RB_AddQuadStamp: corners at middle ± left ± up, texture and lightmap st at the quad's corners, the
              // first vertex's colour throughout.
              const mid = [0, 1, 2].map(axis => [0, 1, 2, 3].reduce((sum, k) => sum + point(k)[axis], 0) / 4);
              const radius = Math.hypot(...point(0).map((value, axis) => value - mid[axis])) * .707;
              corners.forEach((values, k) => {
                const stamped = [...values.slice(0, 3), +(k === 1 || k === 2), +(k >= 2), +(k === 1 || k === 2), +(k >= 2), ...values.slice(7, 10), ...corners[0].slice(10)];
                emit(stamped, origin, lightmap, undefined, [...mid, radius]);
              });
              indices.push(...[0, 1, 3, 3, 1, 2].map(n => base + quad * 4 + n));
            } else {
              // The two shortest edges, their middles, and the long axis between them; each edge turns about its
              // middle across the axis and the view, the way round its winding in the indexes says.
              const edges = [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]], nums = [0, 0], lengths = [999999, 999999];
              edges.forEach(([a, b], j) => {
                const length = point(a).reduce((sum, value, axis) => sum + (value - point(b)[axis]) ** 2, 0);
                if (length < lengths[0]) { nums[1] = nums[0]; lengths[1] = lengths[0]; nums[0] = j; lengths[0] = length; }
                else if (length < lengths[1]) { nums[1] = j; lengths[1] = length; }
              });
              const mids = nums.map(j => point(edges[j][0]).map((value, axis) => (value + point(edges[j][1])[axis]) / 2));
              const major = mids[1].map((value, axis) => value - mids[0][axis]), placed = [];
              nums.forEach((j, e) => {
                const [a, b] = edges[j], half = .5 * Math.sqrt(lengths[e]);
                let k = 0;
                while (k < 5 && !(offsets[quad * 6 + k] === quad * 4 + a && offsets[quad * 6 + k + 1] === quad * 4 + b)) k++;
                placed[a] = [...mids[e], k === 5 ? half : -half];
                placed[b] = [...mids[e], k === 5 ? -half : half];
              });
              corners.forEach((values, k) => emit(values, origin, lightmap, undefined, placed[k], major));
              for (const offset of offsets.slice(quad * 6, quad * 6 + 6)) indices.push(base + (offset >= 0 && offset < count ? offset : 0));
            }
          }
          if (map.bspCull) groups.get(key).ranges.push([face, rangeStart, indices.length - rangeStart]);
          continue;
        }
        for (let n = 0; n < count; n++) {
          const foliage = foliageFaces.get(face), instance = foliage ? Math.floor(n / foliage.vertices) : 0;
          emit(read(first + n), origin, lightmap, foliage ? [...read(foliage.sourceVertex + instance).slice(0, 3), instance & 1 ? (instance & 2 ? 1.5625 : 1.25) : 1] : undefined);
        }
        for (let n = 0; n < indexCount; n++) {
          const offset = int(indexes.at + (firstIndex + n) * 4);
          indices.push(base + (offset >= 0 && offset < count ? offset : 0));
        }
      }
      if (map.bspCull) groups.get(key).ranges.push([face, rangeStart, indices.length - rangeStart]);
    }
    const attributes = {position: new THREE.Float32BufferAttribute(positions, 3), normal: new THREE.Float32BufferAttribute(normals, 3),
      uv: new THREE.Float32BufferAttribute(uvs, 2), lm: new THREE.Float32BufferAttribute(lms, 2), tint: new THREE.Float32BufferAttribute(tints, 4)};
    if (config.foliage) attributes.foliageOrigin = new THREE.Float32BufferAttribute(foliageOrigins, 4);
    if (sprites) Object.assign(attributes, {sprite: new THREE.Float32BufferAttribute(sprites, 4), spriteAxis: new THREE.Float32BufferAttribute(spriteAxes, 3)});
    const bounds = [0, 1, 2, 3, 4, 5].map(n => models.count ? float(models.at + n * 4) : 0);  // The world model's mins and maxs.
    return {atlas, attributes, groups: [...groups.values()], lightGrid, flares, bounds};
  }

  // A model's light at a point, as R_SetupEntityLightingGrid and R_SetupEntityLighting work it out: the eight
  // cells around it blended, cells in walls (all black) left out, scaled by r_ambientScale / r_directedScale,
  // plus a minimum ambient (Q3 adds 32 to every model, RTCW and ET to RF_MINLIGHT ones), and ambient no brighter
  // than identity light. Null without a grid.
  let lightGrid = null;
  function lightAt(point, minimum = 32) {
    if (!lightGrid) return null;
    const {origin, size, bounds, cells} = lightGrid, pos = [], frac = [];
    for (let i = 0; i < 3; i++) {
      const v = (point[i] - origin[i]) / size[i];
      pos[i] = Math.min(Math.max(Math.floor(v), 0), bounds[i] - 1);
      frac[i] = v - Math.floor(v);
    }
    const step = [1, bounds[0], bounds[0] * bounds[1]], ambient = [0, 0, 0], directed = [0, 0, 0], direction = [0, 0, 0], shifted = [0, 0, 0];
    let total = 0;
    for (let corner = 0; corner < 8; corner++) {
      let factor = 1, cell = pos[0] + pos[1] * step[1] + pos[2] * step[2], j;
      for (j = 0; j < 3; j++) {
        if (!(corner & 1 << j)) { factor *= 1 - frac[j]; continue; }
        if (pos[j] + 1 > bounds[j] - 1) break;  // ioquake3's guard; CNQ3 reads past the edge.
        factor *= frac[j]; cell += step[j];
      }
      const at = cell * 8;
      if (j < 3 || !(cells[at] + cells[at + 1] + cells[at + 2])) continue;
      total += factor;
      for (const [into, from] of [[ambient, at], [directed, at + 3]]) {
        shiftLight(cells[from], cells[from + 1], cells[from + 2], shifted, 0);  // R_LoadLightGrid shifts both.
        for (let i = 0; i < 3; i++) into[i] += factor * shifted[i];
      }
      const lat = cells[at + 7] * Math.PI / 128, lng = cells[at + 6] * Math.PI / 128;
      direction[0] += factor * Math.cos(lat) * Math.sin(lng); direction[1] += factor * Math.sin(lat) * Math.sin(lng); direction[2] += factor * Math.cos(lng);
    }
    const scale = total > 0 && total < .99 ? 1 / total : 1, identity = map.lighting ? map.lighting.identityLight : .5;
    const length = Math.hypot(...direction) || 1;
    return {ambient: ambient.map(value => Math.min(value * scale * (config.ambientScale ?? .6) + identity * minimum, identity * 255) / 255),
      directed: directed.map(value => value * scale * (config.directedScale ?? 1) / 255), direction: direction.map(value => value / length)};
  }
  // Light a model's meshes: their stage materials are shared, so the light is set just before each is drawn.
  // `scale` is the model's own size, which the game's axes carry into the light's direction.
  function useLight(object, light, scale = 1) {
    if (!light) return;
    object.traverse(mesh => {
      if (!mesh.isMesh || !mesh.material.uniforms || !mesh.material.uniforms.uAmbient) return;
      mesh.onBeforeRender = (renderer, scene, camera, geometry, material) => {
        material.uniforms.uAmbient.value.fromArray(light.ambient);
        material.uniforms.uDirected.value.fromArray(light.directed).multiplyScalar(scale);
        material.uniforms.uLightDir.value.fromArray(light.direction);
        material.uniformsNeedUpdate = true;
      };
    });
  }

  // Stages of a surface without a script, as R_FindShader builds them: the image under the lightmap, or lit by
  // the vertex colours where the surface has no lightmap.
  const defaultStagesOf = (shader, lit, model) => shader.default ? [{map: '$default', rgbGen: ['identitylighting'], depthWrite: true}]
    : !shader.implicit ? shader.stages
    : model ? [{map: shader.map, rgbGen: ['lightingdiffuse'], depthWrite: true}]
    : lit ? [{map: '$lightmap', tcGen: 'lightmap', rgbGen: ['identity'], depthWrite: true}, {map: shader.map, rgbGen: ['identity'], blend: ['gl_dst_color', 'gl_zero'], depthWrite: false}]
    : [{map: shader.map, rgbGen: ['exactvertex'], depthWrite: true}];
  const stagesOf = (shader, lit, model, vertexLit) => (config.stagesOf && config.stagesOf(shader, lit, model, vertexLit)) || defaultStagesOf(shader, lit, model);
  const loading = new THREE.MeshBasicMaterial({color: 'rgb(0, 109, 56)', wireframe: true});
  // The materials of a shader's stages, once their textures are in; nothing for a shader that names none.
  async function stageMaterials(shader, stages, atlas) {
    const ready = await Promise.all(stages.map(async stage => {
      if (stage.map === '$lightmap') return [atlas || white];
      if (stage.map === '$whiteimage') return [white];
      if (stage.map === '$default') return [fallback];
      return Promise.all((stage.frames || [stage.map]).map(file => loadTexture(file, stage.clamp, stage.noMipmaps)));
    }));
    return stages.map((stage, index) => ready[index][0] && stageMaterial(shader, stage, ready[index][0], ready[index].filter(Boolean)));
  }

  // The fog passes of a surface under fog `fogNum` (-1 for none) and the map's own fog (RTCW and ET fogvars).
  // FinishShader gives an opaque shader (JA: up to see-through) a pass that matches its depth and a fog shader one
  // over what lies behind it; other shaders get none. The map's fog is GL fog: the pass stands in for it on opaque
  // shaders, the game's single multitextured pass, and blended stages are fogged themselves (mapFogged).
  const fogStyle = fog => fog.global && config.glGlobalFog ? {color: fog.color, scale: 1 / fog.depth, curve: 'exp2'}
    : {color: fog.color, scale: 1 / fog.depth, plane: fog.global ? null : fog.plane, curve: config.fogCurve || 'cnq3'};
  const mapFogStyle = fog => fog.linear ? {color: fog.color, scale: 1 / fog.end, radial: true, curve: 'linear'}
    : {color: fog.color, scale: fog.density, radial: true, curve: 'exp'};
  function fogPasses(shader, fogNum) {
    const opaque = shader.sort <= (config.fogEqualSort ?? 3), volume = map.fogs && map.fogs[fogNum];
    const styles = [(opaque || shader.fog) && volume && !(shader.sky && !(shader.stages || []).length) && fogStyle(volume),
      opaque && !shader.sky && map.mapFog && mapFogStyle(map.mapFog)];
    return styles.filter(Boolean).map(fogPass => ({map: '$whiteimage', rgbGen: ['identity'], fogPass, blend: ['gl_src_alpha', 'gl_one_minus_src_alpha'],
      depthWrite: false, ...(opaque ? {depthFunc: 'equal'} : {})}));
  }
  const mapFogged = (shader, stages) => map.mapFog && !shader.sky && shader.sort > (config.fogEqualSort ?? 3)
    ? stages.map(stage => ({...stage, glFog: mapFogStyle(map.mapFog)})) : stages;
  // A model takes the first fog around its origin (R_ComputeFogNum tests its bounds against each fog's), else the
  // map's fog. Its stages are fogged themselves, as GL fog does, and not by a pass of equal depth: once a model is
  // turned or scaled, two programs need not round its depth alike. One stage, the usual model, comes out as the
  // pass would; ponytail: under both a fog volume and the map's fog a model takes the volume's alone.
  const fogMaterials = new Map();
  function addFog(holder, origin) {
    const fogNum = (map.fogs || []).findIndex(fog => fog && (fog.global || fog.bounds.every((value, i) => i < 3 ? origin[i] >= value : origin[i - 3] <= value)));
    const style = fogNum >= 0 ? fogStyle(map.fogs[fogNum]) : map.mapFog && mapFogStyle(map.mapFog);
    if (!style) return;
    holder.traverse(mesh => {
      const {shader, stage, texture, frames} = mesh.isMesh && mesh.material.userData || {};
      // Fog passes fog opaque shaders and fog shaders; GL map fog fogs every stage.
      if (!stage || !(shader.sort <= (config.fogEqualSort ?? 3) || shader.fog || fogNum < 0)) return;
      const key = mesh.material.uuid + '|' + fogNum;
      if (!fogMaterials.has(key)) fogMaterials.set(key, stageMaterial(shader, {...stage, glFog: style}, texture, frames));
      mesh.material = fogMaterials.get(key);
    });
  }
  // One mesh of each opaque surface, for dropping pickups to the floor. They are in no scene, so a ray meets them
  // in the game's own axes.
  const floors = [], floorMaterial = new THREE.MeshBasicMaterial({side: THREE.DoubleSide});
  async function addGroup({shader, lit, vertexLit, fog, mover, indices, ranges}, {atlas, attributes}, serial) {
    const parent = mover === undefined ? root : movers[mover].holder;
    if (shader.sky && map.mapFog && map.mapFog.linear) return;  // Linear map fog hides the sky (RB_StageIteratorSky).
    const geometry = new THREE.BufferGeometry();
    Object.entries(attributes).forEach(([name, attribute]) => geometry.setAttribute(name, attribute));
    geometry.setIndex(indices);
    // The attributes hold the whole map, so the sphere that culls this surface is found over its own vertices.
    const centre = new THREE.Box3(), point = new THREE.Vector3();
    for (const index of indices) centre.expandByPoint(point.fromBufferAttribute(attributes.position, index));
    geometry.boundingSphere = centre.getBoundingSphere(new THREE.Sphere());
    // Until its textures arrive a surface is the green wireframe the T1 terrain shows while loading.
    const placeholder = new THREE.Mesh(geometry, loading), meshes = [];
    root.add(placeholder);
    const add = (material, stage) => {
      const mesh = new THREE.Mesh(geometry, material);
      meshes.push(mesh);
      if (shader.sky && skyAtFarDepth) mesh.frustumCulled = false;
      mesh.renderOrder = shader.sort * 100000 + serial * 16 + stage;  // By sort, then shader by shader, stage after stage.
      mesh.name = shader.name;
      parent.add(mesh);
    };
    {
      if (shader.sky && shader.sky.box) add(boxMaterial(await Promise.all(shader.sky.box.map(file => loadTexture(file, true, !config.skyMipmaps)))), 0);
      // ponytail: one draw per stage, as the game without multitexture; fold lightmap × texture into one pass if maps get heavy.
      const materials = await stageMaterials(shader, mapFogged(shader, stagesOf(shader, lit, false, vertexLit) || []), lit && atlas);
      materials.forEach((material, index) => { if (material) add(material, index + 1); });
      if (shader.sky && shader.sky.inner) add(boxMaterial(await Promise.all(shader.sky.inner.map(file => loadTexture(file, true, !config.skyMipmaps))), true), materials.length + 1);
      fogPasses(shader, fog).forEach((pass, n) => add(stageMaterial(shader, pass, white), materials.length + 2 + n));
    }
    root.remove(placeholder);
    if (shader.sky) skyGeometries.push(geometry);
    worldGroups.push({indices, ranges, meshes, sky: !!shader.sky, mover});
    if (shader.sort === 3 && !shader.sky) {
      let floorGeometry = geometry;
      if (map.bspCull) {
        // Culling edits draw indices. Collision retains all triangles, while
        // sharing the large, immutable vertex attributes with the draw batch.
        floorGeometry = new THREE.BufferGeometry();
        Object.entries(attributes).forEach(([name, attribute]) => floorGeometry.setAttribute(name, attribute));
        floorGeometry.setIndex(geometry.index.clone());
        floorGeometry.boundingSphere = geometry.boundingSphere;
      }
      floors.push(new THREE.Mesh(floorGeometry, floorMaterial));
    }
    // Movers and flare cards are entities, not world leaves: no world culling.
    if (!ranges || mover !== undefined) return;
    if (visibility) visibility.bind(geometry, ranges, shader);
    else if (config.bindWorldCull) config.bindWorldCull(map, geometry, ranges, shader);
  }

  // The sky portal (props_skyportal in RTCW, misc_skyportal in JA; CG_DrawSkyBoxPortal and RDF_SKYBOXPORTAL): the
  // scene seen from the portal's origin, with the view's angles and field of view, is drawn first, sky and all; then
  // the view draws everything but its sky over it. What the portal shows is fixed: the surfaces of the leaves its
  // cluster can see, and the props, pickups and movers standing in them (G_PortalifyEntities). ponytail: the
  // portal's own fog (fogcolor, fognear, fogfar) is not drawn.
  const worldGroups = [];
  let skyPortal = null;
  function setupSkyPortal() {
    const seen = map.skyPortal && map.bspCull && window.skinnerVisibility.clusterMask(map.bspCull, map.skyPortal.origin);
    if (!seen) return;
    const swaps = [], skies = [];
    for (const {indices, ranges, meshes, sky, mover} of worldGroups) {
      if (sky) { skies.push(...meshes); continue; }
      if (mover !== undefined || !ranges) continue;
      const kept = ranges.flatMap(([face, start, length]) => seen.mask[face] ? indices.slice(start, start + length) : []);
      if (!kept.length) continue;
      const geometry = new THREE.BufferGeometry(), centre = new THREE.Box3(), point = new THREE.Vector3(), main = meshes[0].geometry;
      Object.entries(main.attributes).forEach(([name, attribute]) => geometry.setAttribute(name, attribute));
      geometry.setIndex(kept);
      for (const index of kept) centre.expandByPoint(point.fromBufferAttribute(main.attributes.position, index));
      geometry.boundingSphere = centre.getBoundingSphere(new THREE.Sphere());
      swaps.push(...meshes.map(mesh => ({mesh, geometry, main})));
    }
    const shown = new Set([...swaps.map(swap => swap.mesh), ...skies]);
    // Placed models and pickups by where they stand, movers by their origin.
    root.children.forEach(child => {
      const at = child.isMesh ? null : new THREE.Vector3().setFromMatrixPosition(child.matrixAutoUpdate ? child.matrix.compose(child.position, child.quaternion, child.scale) : child.matrix);
      if (at && (at.lengthSq() ? seen.visible(at.toArray()) : false)) shown.add(child);
    });
    movers.forEach(({spec, holder}) => { if (seen.visible(spec.origin)) shown.add(holder); });
    skyPortal = {swaps, skies, shown, camera: camera.clone(), eye: new THREE.Vector3(...map.skyPortal.origin)};
  }
  function drawSkyPortal() {
    const {swaps, shown, camera: view, eye} = skyPortal, changed = [];
    view.copy(camera);
    view.position.set(eye.x, eye.z, -eye.y);
    view.updateMatrixWorld();
    uniforms.uView.value.copy(eye);
    for (const swap of swaps) swap.mesh.geometry = swap.geometry;
    // The portal's own set, whatever the view's culling (JA's distance cull) did to it this frame.
    for (const child of root.children) if (child.visible !== shown.has(child)) { changed.push(child); child.visible = !child.visible; }
    renderer.render(scene, view);
    for (const child of changed) child.visible = !child.visible;
    for (const swap of swaps) swap.mesh.geometry = swap.main;
    uniforms.uView.value.set(camera.position.x, -camera.position.z, camera.position.y);
  }

  // Movers (g_mover.c): a moving brush model's surfaces hang under a group whose matrix carries them from the pose
  // their faces were written in to where its motion has them now (BG_EvaluateTrajectory).
  const movers = [], turned = new THREE.Matrix4();
  const axisMatrix = (angles, into) => {  // AnglesToAxis, its forward, left and up as the columns.
    const [p, y, r] = angles.map(THREE.MathUtils.degToRad), cp = Math.cos(p), sp = Math.sin(p), cy = Math.cos(y), sy = Math.sin(y), cr = Math.cos(r), sr = Math.sin(r);
    return into.set(cp * cy, sr * sp * cy - cr * sy, cr * sp * cy + sr * sy, 0, cp * sy, sr * sp * sy + cr * cy, cr * sp * sy - sr * cy, 0, -sp, sr * cp, cr * cp, 0, 0, 0, 0, 1);
  };
  function alongPath({points, legs}, seconds) {  // Reached_Train: wait at a corner, then straight on to the next.
    const total = legs.reduce((sum, leg) => sum + leg.wait + leg.duration, 0);
    let time = total > 0 ? seconds % total : 0;
    for (let i = 0; i < legs.length; i++) {
      if (time < legs[i].wait) return points[i];
      time -= legs[i].wait;
      const to = points[(i + 1) % points.length], share = time / legs[i].duration;
      if (time < legs[i].duration) return points[i].map((value, axis) => value + (to[axis] - value) * share);
      time -= legs[i].duration;
    }
    return points[0];
  }
  function placeMover({spec, holder}, seconds) {
    const wave = spec.period > 0 ? Math.sin(2 * Math.PI * (seconds / spec.period - spec.phase)) : 0;
    const origin = spec.type === 'sine' ? spec.origin.map((value, axis) => value + spec.delta[axis] * wave) : spec.type === 'path' ? alongPath(spec, seconds) : spec.origin;
    const angles = spec.type === 'spin' ? spec.angles.map((value, axis) => value + spec.rate[axis] * seconds)
      : spec.type === 'swing' ? [spec.angles[0], spec.angles[1], spec.angles[2] + spec.amplitude * wave] : spec.angles;
    holder.matrix.makeTranslation(...spec.base.origin.map(value => -value))
      .premultiply(axisMatrix(spec.base.angles, turned).transpose()).premultiply(axisMatrix(angles, turned))
      .premultiply(turned.makeTranslation(...origin));
    holder.matrixWorldNeedsUpdate = true;
  }

  // Quads that a deform places each frame (flares and coronas about their middles), or set out once (the sun).
  function quads(list, corners = null) {
    const geometry = new THREE.BufferGeometry(), set = (name, size, values) => geometry.setAttribute(name, new THREE.Float32BufferAttribute(values, size));
    const each = make => list.flatMap(item => [0, 1, 2, 3].flatMap(k => make(item, k)));
    set('position', 3, each((item, k) => corners ? corners[k] : item.middle));
    set('sprite', 4, each(item => [...item.middle, item.size]));
    set('normal', 3, each(item => item.normal || [0, 0, 0]));
    set('tint', 4, each(item => [...item.colour, 1]));
    set('uv', 2, each((item, k) => [+(k === 1 || k === 2), +(k >= 2)]));  // RB_AddQuadStamp's corners.
    geometry.setIndex(list.flatMap((_, n) => [0, 1, 3, 3, 1, 2].map(k => n * 4 + k)));
    return geometry;
  }
  async function addQuads(shader, geometry, renderOrder) {
    return (await stageMaterials(shader, shader.stages || [])).filter(Boolean).map((material, stage) => {
      const mesh = new THREE.Mesh(geometry, material);
      mesh.frustumCulled = false;
      mesh.renderOrder = renderOrder + stage;
      root.add(mesh);
      return mesh;
    });
  }
  // id's flares (r_flares 1 in RTCW and ET): flare surfaces, which pass the depth test up to 24 units behind what
  // is in front of them, and coronas, which need a clear line (CG_Corona's trace), each drawn with the flare shader
  // after everything else (RB_RenderFlares). ponytail: a flare is clipped where something covers part of it
  // rather than shown or hidden whole, and comes and goes at once instead of over r_flareFade's fifth of a second.
  async function addFlares(faces) {
    if (!map.flareShader) return;
    const identity = map.lighting ? map.lighting.identityLight : .5;
    const scaled = colour => colour.map(value => value * identity);  // RB_RenderFlare: the colour × identity light.
    if (faces.length) await addQuads({...map.flareShader, deforms: [['flare', 24, 0, 1e9]]},
      quads(faces.map(({origin, colour, normal}) => ({middle: origin, size: 1, colour: scaled(colour), normal}))), 1e9);
    if ((map.coronas || []).length) await addQuads({...map.flareShader, deforms: [['flare', 1, config.coronaCone ?? .6, 1536]]},
      quads(map.coronas.map(({origin, colour, scale}) => ({middle: origin, size: scale, colour: scaled(colour)}))), 1e9 + 10);
  }
  // The sun (RB_DrawSun, r_drawSun 1): the sun shader on a square a fifth of its distance across either side, out
  // along the sky's q3map_sun direction, at the far end of depth, so that only the sky lies behind it.
  let sun = [];
  async function addSun() {
    if (!map.sun?.shader) return;
    const toward = new THREE.Vector3(...map.sun.direction).normalize(), distance = camera.far / 1.75;
    // PerpendicularVector: the axis least along the direction, made square to it.
    const least = [0, 1, 2].reduce((best, axis) => Math.abs(toward.getComponent(axis)) < Math.abs(toward.getComponent(best)) ? axis : best, 0);
    const left = new THREE.Vector3().setComponent(least, 1).addScaledVector(toward, -toward.getComponent(least)).normalize();
    const up = new THREE.Vector3().crossVectors(toward, left), middle = toward.clone().multiplyScalar(distance);
    const corner = (a, b) => middle.clone().addScaledVector(left, a * .2 * distance).addScaledVector(up, b * .2 * distance).toArray();
    sun = await addQuads({...map.sun.shader, atFar: true}, quads([{middle: middle.toArray(), size: 0, colour: [1, 1, 1], normal: toward.clone().negate().toArray()}],
      [corner(1, 1), corner(-1, 1), corner(-1, -1), corner(1, -1)]), 2 * 100000 + 99990);  // After the sky (sort 2).
  }

  // Rain and snow (the import's map.weather). Where it falls comes from two height maps drawn from above once, as ET
  // reads its trace map (CG_GenerateTracemap): the sky's height over each spot and the highest other surface there,
  // the ground. A drop falls between the two, ET's way, within 1000 units of the eye (MAX_ATMOSPHERIC_DISTANCE), or
  // in its own column (RTCW's misc_snow). ponytail: Raven's outside brushes and weather zones are taken to be wherever
  // the sky is open; a drop is a line, a flake a point.
  const skyGeometries = [], falls = [];
  let lastFall = 0;
  function heightMap(geometries, [minX, minY, minZ, maxX, maxY, maxZ], size) {
    const target = new THREE.WebGLRenderTarget(size, size, {type: THREE.FloatType}), heights = new Float32Array(size * size * 4);
    const top = new THREE.Scene(), turned = new THREE.Group(), view = new THREE.OrthographicCamera(-(maxX - minX) / 2, (maxX - minX) / 2, (maxY - minY) / 2, -(maxY - minY) / 2, 1, maxZ - minZ + 2000);
    const material = new THREE.ShaderMaterial({side: THREE.DoubleSide,
      vertexShader: 'varying float z; void main() { z = position.z; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }',
      fragmentShader: 'varying float z; void main() { gl_FragColor = vec4(z, 1., 0., 1.); }'});
    turned.rotation.x = -Math.PI / 2;
    top.add(turned);
    for (const geometry of geometries) turned.add(Object.assign(new THREE.Mesh(geometry, material), {frustumCulled: false}));
    view.position.set((minX + maxX) / 2, maxZ + 1000, -(minY + maxY) / 2);  // Above the world, looking down with game +y up.
    view.up.set(0, 0, -1);
    view.lookAt((minX + maxX) / 2, 0, -(minY + maxY) / 2);
    renderer.setRenderTarget(target);
    renderer.setClearColor(0x000000, 0);
    renderer.clear();
    renderer.render(top, view);
    renderer.readRenderTargetPixels(target, 0, 0, size, size, heights);
    renderer.setRenderTarget(null);
    target.dispose();
    material.dispose();
    // The height at a spot, or null where nothing lies under it.
    return (x, y) => {
      const at = (Math.min(Math.max(Math.floor((y - minY) / (maxY - minY) * size), 0), size - 1) * size + Math.min(Math.max(Math.floor((x - minX) / (maxX - minX) * size), 0), size - 1)) * 4;
      return heights[at + 1] ? heights[at] : null;
    };
  }
  function addWeather(specs, bounds) {
    const clear = renderer.getClearColor(new THREE.Color()), alpha = renderer.getClearAlpha();
    const ground = heightMap(floors.map(mesh => mesh.geometry), bounds, 512), sky = heightMap(skyGeometries, bounds, 512);
    renderer.setClearColor(clear, alpha);
    const identity = map.lighting ? map.lighting.identityLight : .5;
    for (const {type, count, speed, length, wind, column} of specs) {
      const rain = type === 'rain', drops = new Float32Array(Math.min(count, 4000) * 3), total = drops.length / 3;
      const geometry = new THREE.BufferGeometry(), eye = uniforms.uView.value;
      geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(total * (rain ? 6 : 3)), 3));
      const particles = rain ? new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({color: new THREE.Color().setScalar(identity * .5), transparent: true, opacity: .4}))
        : new THREE.Points(geometry, new THREE.PointsMaterial({color: new THREE.Color().setScalar(identity * 1.5), size: length, transparent: true, opacity: .8}));
      particles.frustumCulled = false;
      particles.renderOrder = 1e9 - 10;
      root.add(particles);
      const respawn = (i, anywhere) => {
        for (let attempt = 0; attempt < 4; attempt++) {
          let x, y, low, high;
          if (column) {
            x = column.min[0] + Math.random() * (column.max[0] - column.min[0]);
            y = column.min[1] + Math.random() * (column.max[1] - column.min[1]);
            [low, high] = [ground(x, y) ?? -Infinity, column.top];
          } else {
            const angle = Math.random() * 2 * Math.PI, distance = 20 + 1000 * Math.random();
            x = eye.x + Math.sin(angle) * distance;
            y = eye.y + Math.cos(angle) * distance;
            const roof = sky(x, y);
            if (roof === null) continue;
            [low, high] = [Math.max(ground(x, y) ?? -Infinity, eye.z - 1000), Math.min(roof, eye.z + 1000)];
          }
          if (!(high > low)) continue;
          // A new drop starts at the top, once the fall is under way; the first fill spreads them through the height.
          drops.set([x, y, anywhere || column ? low + Math.random() * (high - low) : high], i * 3);
          return;
        }
        drops.set([eye.x, eye.y, -1e9], i * 3);  // Nowhere open near: parked far below, out of view.
      };
      for (let i = 0; i < total; i++) respawn(i, true);
      falls.push(delta => {
        const positions = geometry.attributes.position.array, step = [wind[0] * delta, wind[1] * delta, -speed * delta];
        for (let i = 0; i < total; i++) {
          for (let axis = 0; axis < 3; axis++) drops[i * 3 + axis] += step[axis];
          const x = drops[i * 3], y = drops[i * 3 + 1], z = drops[i * 3 + 2], floor = ground(x, y);
          if (z < -1e8 || floor !== null && z < floor || !column && Math.hypot(x - eye.x, y - eye.y) > 1020) respawn(i, false);
          positions.set(drops.subarray(i * 3, i * 3 + 3), i * (rain ? 6 : 3));
          if (rain) positions.set([drops[i * 3] - wind[0] / speed * length, drops[i * 3 + 1] - wind[1] / speed * length, drops[i * 3 + 2] + length], i * 6 + 3);
        }
        geometry.attributes.position.needsUpdate = true;
      });
    }
  }

  // Pickups, shown as the game's cg_ents.c CG_Item shows them: turning once in 2.048 seconds (health twice as
  // fast), bobbing 4 ± 4 units, weapons half as large again and turning about their middle, and the second model
  // of a health item or powerup turning the other way, a powerup's 12 units up.
  const itemModels = new Map(), pickups = [], placedTicks = [];
  function loadItemModel(path) {
    if (!itemModels.has(path)) itemModels.set(path, (async () => {
      const model = map.itemModels[path];
      let surfaces = await (await get(data + model.file)).json();
      if (surfaces.geometry_buffers) {
        const filename = GeometryBuffers.filename(surfaces.geometry_buffers);
        const binary = await (await get(data + model.file.replace(/[^/]+$/, filename))).arrayBuffer();
        surfaces = GeometryBuffers.surfaces(surfaces, binary);
      }
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
    // Team Arena's barrel hangs at the weapon's tag_barrel, turned with it.
    const barrel = item.barrel ? await loadItemModel(item.barrel.model) : [];
    models.forEach((surfaces, which) => {
      const part = new THREE.Group(), middle = new THREE.Vector3().fromArray(map.itemModels[item.models[which]].middle);
      for (const [{geometry, materials, sort}, offset] of [...surfaces.map(surface => [surface, null]), ...(which ? [] : barrel.map(surface => [surface, item.barrel.origin]))]) materials.forEach((material, stage) => {
        const mesh = new THREE.Mesh(geometry, material);
        mesh.renderOrder = sort * 100000 + 99000 + stage;
        if (weapon) mesh.position.fromArray(offset || [0, 0, 0]).sub(middle);
        part.add(mesh);
      });
      if (weapon) part.scale.setScalar(1.5);
      if (which) part.position.z = item.kind === 'powerup' ? 12 : 0;
      holder.add(part);
      useLight(part, lightAt([x, y, height + 4]), weapon ? 1.5 : 1);  // Lit where it bobs about.
    });
    root.add(holder);
    addFog(holder, [x, y, height + 4]);
    pickups.push({holder, height, fast: item.kind === 'health', rate: 5 + serial * .01});
  }

  function showNotes() {
    if (!map) return;
    const parts = [];
    if (map.bspCull?.pvs) parts.push('World cluster visibility is enabled. Door area portals and server entity visibility are not simulated; sky and separately submitted models remain independent of the world PVS');
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
    uniforms.uTanFov.value = Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2);
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(uniforms.uTanFov.value / aspect));
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
  shown.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({uniforms: {frame: {value: frame.texture}, displayScale: {value: 2}, displayGamma: {value: 1}}, depthTest: false, depthWrite: false,
    vertexShader: 'varying vec2 at; void main() { at = uv; gl_Position = vec4(position.xy, 0., 1.); }',
    fragmentShader: config.bspVersion === 47
      ? 'uniform sampler2D frame; uniform float displayScale, displayGamma; varying vec2 at; void main() { gl_FragColor = vec4(pow(texture2D(frame, at).rgb, vec3(1. / displayGamma)) * displayScale, 1.); }'
      : 'uniform sampler2D frame; varying vec2 at; void main() { gl_FragColor = vec4(texture2D(frame, at).rgb * 2., 1.); }'})));
  shown.children[0].frustumCulled = false;
  const cullDirection = new THREE.Vector3(), forwardOfView = new THREE.Vector3();
  function draw(seconds) {
    uniforms.uTime.value = seconds;
    uniforms.uView.value.set(camera.position.x, -camera.position.z, camera.position.y);
    const ahead = camera.getWorldDirection(forwardOfView);
    uniforms.uForward.value.set(ahead.x, -ahead.z, ahead.y);
    const axes = camera.matrixWorld.elements;  // Columns: right, up.
    uniforms.uViewLeft.value.set(-axes[0], axes[2], -axes[1]);
    uniforms.uViewUp.value.set(axes[4], -axes[6], axes[5]);
    for (const {material, frames, fps} of animated) material.uniforms.map.value = frames[Math.floor(seconds * fps) % frames.length];
    for (const tick of placedTicks) tick(seconds);
    for (const mover of movers) placeMover(mover, seconds);
    for (const mesh of sun) mesh.position.copy(uniforms.uView.value);
    for (const fall of falls) fall(Math.min(Math.max(seconds - lastFall, 0), .1));
    lastFall = seconds;
    for (const {holder, height, fast, rate} of pickups) {
      holder.position.z = height + 4 + Math.cos((seconds + 1) * rate) * 4;
      holder.rotation.z = seconds % 2.048 / 2.048 * 2 * Math.PI * (fast ? 2 : 1);
      if (holder.children[1]) holder.children[1].rotation.z = -holder.rotation.z - seconds % 1.024 / 1.024 * 2 * Math.PI;  // Its own turn, undoing the holder's.
    }
    if (config.updateCull) config.updateCull(map, camera);
    if (visibility) {
      const direction = camera.getWorldDirection(cullDirection);
      visibility.update([camera.position.x, -camera.position.z, camera.position.y],
        [direction.x, -direction.z, direction.y], config.worldCullDistance ? config.worldCullDistance(map) : null);
    }
    renderer.setRenderTarget(frame);
    if (skyPortal) {
      drawSkyPortal();
      for (const mesh of skyPortal.skies) mesh.visible = false;
      renderer.autoClearColor = false;
    }
    renderer.render(scene, camera);
    if (skyPortal) {
      for (const mesh of skyPortal.skies) mesh.visible = true;
      renderer.autoClearColor = true;
    }
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
  window.skinnerQ3Maps = {renderer, scene, camera, draw, movers, get skyPortal() { return skyPortal; }, set skyPortal(value) { skyPortal = value; }};  // For checks in a hidden page, where no frame is drawn.

  const select = id => { location.search = '?map=' + encodeURIComponent(id); };
  $('map').addEventListener('change', event => select(event.target.value));
  $('import').addEventListener('click', async () => {
    const game = $('gamePath').value.trim();
    if (!game) { $('importStatus').textContent = `Enter your ${config.gameName || 'Quake 3'} folder.`; return; }
    $('import').disabled = true;
    $('importStatus').textContent = 'Importing maps… a full install takes about a minute.';
    try {
      const response = await fetch(config.importRoute || '/import_q3_maps', {method: 'POST', headers: {'Content-Type': 'application/json'},
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
    if (!maps.some(item => item.id === mapId)) mapId = (maps.find(item => item.id === (config.defaultMap || 'q3dm1')) || maps[0]).id;
    const byGroup = new Map();
    for (const item of maps) byGroup.set(item.group, [...(byGroup.get(item.group) || []), item]);
    $('map').replaceChildren(...[...byGroup].map(([group, items]) => {
      const element = Object.assign(document.createElement('optgroup'), {label: group});
      element.append(...items.map(item => new Option(item.longname ? `${item.name} · ${item.longname}` : item.name, item.id)));
      return element;
    }));
    $('map').value = mapId;
    map = await (await get(`${data}maps/${mapId}/${config.sceneFile ? config.sceneFile() : 'scene.json'}`)).json();
    visibility = window.skinnerVisibility.create(map.bspCull);
    if (config.configureCamera) config.configureCamera(camera, map);
    else if (map.farClip) { camera.far = map.farClip; camera.updateProjectionMatrix(); }  // The view ends at the fog (RTCW, ET) or distanceCull (JK2).
    // A global fog, or linear map fog, clears the frame to its colour (RB_DrawBuffer, RB_BeginDrawingView).
    const clearFog = (map.fogs || []).find(fog => fog && fog.global) || (map.mapFog && map.mapFog.linear && map.mapFog);
    if (clearFog) renderer.setClearColor(new THREE.Color(...clearFog.color.map(value => value * (map.lighting ? map.lighting.identityLight : .5))));
    document.title = `${map.name} — ${config.title || 'Q3 Maps'}`;
    if (map.lighting) {
      settings.fov = map.lighting.fov;
      shown.children[0].material.uniforms.displayScale.value = map.lighting.displayScale;
      shown.children[0].material.uniforms.displayGamma.value = map.lighting.displayGamma;
      if (config.defaultTexels) { fallbackTexels.set(config.defaultTexels(map.lighting)); fallback.needsUpdate = true; }
    }
    showNotes();
    applySettings();
    showViewpoint(0);
    showStatus('Loading map…');
    const externalAtlas = config.externalLightmaps && map.lightmapAtlas ? await loadTexture(map.lightmapAtlas.file, true, true) : null;
    const world = buildWorld(await (await get(data + map.bsp)).arrayBuffer(), externalAtlas);
    lightGrid = world.lightGrid;
    if (!world.groups.length) failures.push('the map file holds no surfaces to draw');
    for (const spec of map.movers || []) {
      const holder = new THREE.Group();
      holder.matrixAutoUpdate = false;
      root.add(holder);
      movers.push({spec, holder});
    }
    let loaded = 0;
    await Promise.all(world.groups.map(async (group, serial) => { await addGroup(group, world, serial); showStatus(`Loading surfaces ${++loaded}/${world.groups.length}`); }));
    await Promise.all([addFlares(world.flares), addSun()]);
    if ((map.weather || []).length) addWeather(map.weather, world.bounds);
    showStatus('Placing pickups…');
    root.updateMatrixWorld(true);
    await Promise.all((map.items || []).map(addItem));
    if (config.addPlaced) await Promise.all((map.placed || []).map(async item => {
      const holder = await config.addPlaced(item, {root, loadItemModel, ticks: placedTicks, map, lightAt, useLight, addFog});
      trackNPC(item, holder);
    }));
    setupSkyPortal();
    showNotes();
    ready = true;
    showReady();
  } catch (error) { npcLoadFailed = true; npcToggle?.setCount(0); showStatus('Map could not load: ' + error.message); }
});

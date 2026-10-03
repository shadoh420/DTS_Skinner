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
   (me_showproperties). Numpad + and − turn the selection (me_rotate_inc/dec, by the angle step, me_snapangle); the
   arrows, Home/End/Insert/Delete, PgUp/PgDn and , and . move, scale, flip and turn the texture of the face under the
   cursor (me_texcoords_*), and Shift with the arrows nudges the selection instead. C is clip mode: two or three points
   make a plane, Enter clips and Shift+Enter splits. A click on anything a prefab places selects its Prefab entity;
   the console (`) breaks, updates, makes and lists prefabs (me_breakprefab, me_updateprefab, me_createprefab,
   me_listprefabs), and a double click on a placement edits its prefab in place. Ctrl+Shift-click picks more faces;
   Mirror mirrors the selection. Faces are textured from the game's materials or any of Skinner's texture libraries
   (the material browser). 0 plays: a player walks through the map (movement.js); F flies instead. */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const B = window.ReflexBrush, M = window.ReflexMap, L = window.ReflexLight;
  const storageKey = 'skinner.reflexmaps';
  // The game's me_snapdistance is 16; its me_snapangle default is not known, and 45 is the step most stock angles are on.
  const settings = {fov: 110, invertX: false, invertY: false, grid: 16, angle: 45};
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
  // material's albedo, read from the game's material files by the import (materials.json), with its metallic value
  // and roughness. The game raises the albedo to the power 2.2, as it does a face's colour (its shaders do; measured:
  // concrete's 0.37 draws 0.107 of what a white face does). Without one, a material's colour is guessed from its name.
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
  // {albedo (linear), metallic, roughness} of a material.
  function materialOf(name) {
    if (!materials.has(name)) {
      const read = packColours[name];
      if (read && read.colour) materials.set(name, {albedo: read.colour.map(linear), metallic: read.metallic || 0, roughness: read.roughness ?? .8, sss: read.sss || 0});
      else {
        const known = Object.keys(GUESSED).find(key => name.includes(key));
        let colour = known && GUESSED[known];
        if (!colour) {
          // Anything else is a pale colour of its own, so neighbouring materials stay apart.
          let hash = 0;
          for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
          colour = new THREE.Color().setHSL((hash % 360) / 360, .25, .55).toArray();
        }
        materials.set(name, {albedo: colour.map(linear), metallic: 0, roughness: .8});
      }
    }
    return materials.get(name);
  }
  // Glowing materials (common/materials/effects/glow: shader standard_ALBEDOCOLOUR_ALBEDOINTENSITY) are not lit: they
  // shine (colour × intensity)^2.2, as that shader has it; so do the other forward (standard_…) shaders the game draws
  // solid (their material's flags lack 0x200), at intensity 1. What this gives is the factor on the raised colour.
  const GLOWS = /standard_ALBEDOCOLOUR_ALBEDOINTENSITY/;
  const isForward = read => /^internal\/shaders\/standard_/.test(read.shader || '');
  function glowOf(name) {
    const read = packColours[name];
    if (!read) return 0;
    if (GLOWS.test(read.shader || '')) return (read.intensity ?? 1) ** 2.2;
    // Water, lava and slime (internal/shaders/fluid): unlit, (colour × 3.3)^2.2; water is added (isSeeThrough).
    if (/^internal\/shaders\/fluid/.test(read.shader || '')) return 3.3 ** 2.2;
    return isForward(read) && read.flags !== undefined && !(read.flags & 0x200) ? 1 : 0;
  }
  // Diffuse colour, the colour of reflections at normal incidence (1.54 % for anything not metal, as the game's
  // lighting shader has it, the albedo for metal) and roughness; for a glowing material its light (emit 1). A
  // translucent one (…_SSS shaders: paper, cloth) carries its sss as a negative emit.
  function faceShade(face) {
    const material = LIBRARY.test(face.material || '') ? {albedo: [1, 1, 1], metallic: 0, roughness: .8} : materialOf(face.material || ''), own = M.colourOf(face);
    // The game leaves a face's colour off the dev materials, whose shader (…_TINTED) takes the material's own tint.
    const tinted = /_TINTED/.test((packColours[face.material || ''] || {}).shader || '');
    const albedo = own && !tinted ? own.slice(0, 3).map(linear) : material.albedo, metallic = material.metallic;
    const glow = glowOf(face.material || '');
    if (glow) return {diffuse: albedo.map(c => c * glow), specular: [0, 0, 0], roughness: 1, emit: 1};
    return {diffuse: albedo.map(c => c * (1 - metallic)), specular: albedo.map(c => .015395 * (1 - metallic) + c * metallic), roughness: material.roughness, emit: -(material.sss || 0)};
  }
  // See-through materials, by shader (light beams, glass, race start and finish, pickup and powerup glows) or by
  // name for water, whose fluid shader lava and slime share; drawn after everything else, added (ADDED).
  const SEE_THROUGH = /alphaFresnel|GLASS|raceStartFinish|glowPickup|powerup/;
  const isSeeThrough = face => {
    const name = face.material || '', read = packColours[name];
    return read ? SEE_THROUGH.test(read.shader || '') || /liquids\/water/.test(name) : /fx_light_beam|race_(start|finish)|glass|liquids\/water/.test(name);
  };
  // Faces the game does not draw: the editor's clip materials (player, weapon and full clip).
  const isClip = face => /^internal\/editor\/textures\/editor_.*clip/.test(face.material || '');

  // ---- Drawing brushes ----
  // ---- Light ----
  // The map's baked light (light.js), as the game's lighting shader (gbuffer_light_fullscreen) applies it in play:
  // diffuse light from the probe grid at the point, for its normal, and reflections from the reflection probe its
  // grid cell names, blurred by roughness and weighted by the split-sum lookup (here Karis' fit of it); diffuse less
  // what the surface reflects. Positions and normals are the game's (the geometry is in its coordinates). A map
  // without baked light is lit as the game lights one: evenly, about 0.55 (measured on white faces in play).
  const empty3D = new THREE.Data3DTexture(new Uint16Array(4), 1, 1, 1);
  Object.assign(empty3D, {type: THREE.HalfFloatType, needsUpdate: true});
  const emptyArray = new THREE.DataArrayTexture(new Uint8Array(4), 1, 1, 1);
  emptyArray.needsUpdate = true;
  const uniforms = {uGrid: {value: 0}, uLit: {value: 0}, uEye: {value: new THREE.Vector3()}, uAmbient: {value: new THREE.Vector3(.55, .55, .5)},
    uProbeScale: {value: new THREE.Vector3()}, uProbeOffset: {value: new THREE.Vector3()}, uGain: {value: 1}, uCubes: {value: 0}, uCubeMips: {value: 5},
    uProbeIndex: {value: empty3D}, uCube: {value: emptyArray},
    uLightCount: {value: 0}, uLightCell: {value: 128}, uLightOrigin: {value: new THREE.Vector3()}, uLightSize: {value: [1, 1, 1]},
    uLights: {value: null}, uLightList: {value: null}, uLightCells: {value: empty3D},
    uSun: {value: new THREE.Vector3(0, 1, 0)}, uSunColour: {value: new THREE.Vector3()}, uSunMatrix: {value: new THREE.Matrix4()}, uSunDepth: {value: null}, uSunTexel: {value: 0},
    uSunBias: {value: 0}, uNearMatrix: {value: new THREE.Matrix4().makeTranslation(9, 9, 9)}, uNearDepth: {value: null}, uNearBias: {value: 0}};
  for (let i = 0; i < 7; i++) uniforms['uSH' + i] = {value: empty3D};
  scene.onBeforeRender = (_, __, view) => uniforms.uEye.value.set(view.position.x, view.position.y, -view.position.z);
  const LIGHTING = `precision highp sampler3D; precision highp sampler2DArray;
    uniform float uLit, uGain, uCubes, uCubeMips; uniform vec3 uEye, uAmbient, uProbeScale, uProbeOffset;
    uniform sampler3D uSH0, uSH1, uSH2, uSH3, uSH4, uSH5, uSH6, uProbeIndex; uniform sampler2DArray uCube;
    uniform float uLightCount, uLightCell; uniform vec3 uLightOrigin; uniform ivec3 uLightSize;
    uniform sampler2D uLights, uLightList; uniform sampler3D uLightCells;
    uniform vec3 uSun, uSunColour; uniform mat4 uSunMatrix, uNearMatrix; uniform sampler2D uSunDepth, uNearDepth; uniform float uSunTexel, uSunBias, uNearBias;
    // How much of the sun reaches the point in one shadow map: a 3 × 3 texel box, each compare weighted bilinearly, so
    // edges ramp instead of stepping (the game's cascades are about as soft). -1 outside the map.
    float sunShadow(sampler2D depth, mat4 matrix, float bias, vec3 p) {
      vec4 s = matrix * vec4(p.x, p.y, -p.z, 1.);
      vec3 q = s.xyz / s.w * .5 + .5;
      if (any(lessThan(q, vec3(.002))) || any(greaterThan(q, vec3(.998)))) return -1.;
      vec2 t = q.xy / uSunTexel - .5, f = fract(t), at = (floor(t) + .5) * uSunTexel;
      float lit = 0.;
      for (int y = -1; y <= 2; y++) for (int x = -1; x <= 2; x++) {
        float w = (x == -1 ? 1. - f.x : x == 2 ? f.x : 1.) * (y == -1 ? 1. - f.y : y == 2 ? f.y : 1.);
        lit += w * (q.z - bias <= texture(depth, at + vec2(x, y) * uSunTexel).r ? 1. : 0.);
      }
      return lit / 9.;
    }
    // Light through a translucent surface (the _SSS shaders' sss times the albedo's red, s): 2 s (t + s (w − t)) of
    // the diffuse light whichever way the surface faces, t = sat(v · −l)⁴ toward the viewer, w = sat(0.6 (−n · l) + 0.4).
    vec3 through(vec3 n, vec3 v, vec3 l, float s, vec3 diffuse, vec3 light) {
      float t = clamp(dot(v, -l), 0., 1.); t *= t; t *= t;
      return 2. * s * (t + s * (clamp(dot(-n, l) * .6 + .4, 0., 1.) - t)) * diffuse * light;
    }
    // The sun (gbuffer_light_directional): the lights' BRDF from its direction, where its shadow maps see the point:
    // the fine one around the viewer, else the one of the whole map.
    vec3 sunLight(vec3 p, vec3 n, vec3 v, vec3 diffuse, vec3 f0, float rough, float s) {
      float nl = clamp(dot(n, uSun), 0., 1.);
      if (uSunTexel <= 0. || (nl <= 0. && s <= 0.)) return vec3(0.);
      float slope = 2. * (1. + 3. * sqrt(1. - nl * nl) / max(nl, .05));
      float lit = sunShadow(uNearDepth, uNearMatrix, uNearBias * slope, p);
      if (lit < 0.) lit = sunShadow(uSunDepth, uSunMatrix, uSunBias * slope, p);
      if (lit < 0.) lit = 1.;
      float a2 = rough * rough; a2 *= a2;
      float nh = max(clamp(dot(n, normalize(v + uSun)), 0., 1.), .01), den = nh * nh * (a2 - 1.) + 1.;
      vec3 light = uSunColour * nl * lit;
      return (1. - dot(f0, vec3(.299, .587, .114))) * (diffuse * light + through(n, v, uSun, s, diffuse, uSunColour * lit)) + a2 / (3.14159265 * den * den) * f0 * light * 2.;
    }
    vec3 probeLight(vec3 p, vec3 n) {
      if (uLit < .5) return uAmbient;
      vec3 uvw = p * uProbeScale + uProbeOffset; vec4 a = vec4(n, 1.), b = n.xyzz * n.yzzx;
      vec3 c = vec3(dot(texture(uSH0, uvw), a), dot(texture(uSH1, uvw), a), dot(texture(uSH2, uvw), a));
      c += vec3(dot(texture(uSH3, uvw), b), dot(texture(uSH4, uvw), b), dot(texture(uSH5, uvw), b));
      return (c + texture(uSH6, uvw).rgb * (n.x * n.x - n.y * n.y)) * uGain;
    }
    // The reflection probe of the point's grid cell (counted from 1; ponytail: nearest cell only, where the game blends
    // the eight around the point when they differ), looked up as a cube map: +x, -x, +y, -y, +z, -z, rows top down.
    vec3 reflected(vec3 p, vec3 r, float rough) {
      if (uCubes < .5) return uAmbient;
      float index = floor(texture(uProbeIndex, p * uProbeScale + uProbeOffset).r * 255. + .5);
      vec3 a = abs(r); vec2 st; float face;
      if (a.x >= a.y && a.x >= a.z) { face = r.x > 0. ? 0. : 1.; st = vec2(r.x > 0. ? -r.z : r.z, -r.y) / a.x; }
      else if (a.y >= a.z) { face = r.y > 0. ? 2. : 3.; st = vec2(r.x, r.y > 0. ? r.z : -r.z) / a.y; }
      else { face = r.z > 0. ? 4. : 5.; st = vec2(r.z > 0. ? r.x : -r.x, -r.y) / a.z; }
      vec3 c = textureLod(uCube, vec3(st * .5 + .5, clamp(index - 1., 0., uCubes - 1.) * 6. + face), min(rough * uCubeMips, uCubeMips - 1.)).rgb;
      return pow(c, vec3(2.2));
    }
    // The map's point and spot lights (light.js lightGrid), as gbuffer_light_point and gbuffer_light_spot light a
    // surface: Lambert diffuse and a GGX highlight, twice; only the lights of the point's grid cell (at most 64).
    vec3 dynamicLight(vec3 p, vec3 n, vec3 v, vec3 diffuse, vec3 f0, float rough, float s) {
      if (uLightCount < .5) return vec3(0.);
      ivec3 c = ivec3(floor((p - uLightOrigin) / uLightCell));
      if (any(lessThan(c, ivec3(0))) || any(greaterThanEqual(c, uLightSize))) return vec3(0.);
      vec2 entry = texelFetch(uLightCells, c, 0).rg;
      int first = int(entry.x), count = int(entry.y);
      float a2 = rough * rough; a2 *= a2;
      vec3 sum = vec3(0.);
      for (int k = 0; k < 64; k++) {
        if (k >= count) break;
        int at = first + k, i = int(texelFetch(uLightList, ivec2(at % 4096, at / 4096), 0).r);
        vec4 a = texelFetch(uLights, ivec2(0, i), 0), b = texelFetch(uLights, ivec2(1, i), 0);
        vec4 d = texelFetch(uLights, ivec2(2, i), 0), e = texelFetch(uLights, ivec2(3, i), 0);
        vec3 toLight = a.xyz - p; float distance = length(toLight); vec3 l = toLight / distance;
        float fall = clamp(1. - (distance - a.w) / max(b.w - a.w, 1e-3), 0., 1.); fall *= fall;
        if (e.y > .5) { float edge = clamp((dot(-l, d.xyz) - d.w) * e.x, 0., 1.); fall *= edge * edge; }
        float nl = clamp(dot(n, l), 0., 1.);
        if (fall <= 0. || (nl <= 0. && s <= 0.)) continue;
        float nh = max(clamp(dot(n, normalize(v + l)), 0., 1.), .01), den = nh * nh * (a2 - 1.) + 1.;
        vec3 light = b.rgb * fall * nl;
        sum += (1. - dot(f0, vec3(.299, .587, .114))) * (diffuse * light + through(n, v, l, s, diffuse, b.rgb * fall)) + a2 / (3.14159265 * den * den) * f0 * light * 2.;
      }
      return sum;
    }
    vec3 shade(vec3 diffuse, vec3 f0, float rough, vec3 p, vec3 n, float s) {
      vec3 v = normalize(uEye - p); float nv = dot(n, v);
      vec4 k = rough * vec4(-1., -.0275, -.572, .022) + vec4(1., .0425, 1.04, -.04);
      float a004 = min(k.x * k.x, exp2(-9.28 * clamp(nv, 0., 1.))) * k.x + k.y;
      vec2 ab = vec2(-1.04, 1.04) * a004 + k.zw;
      vec3 spec = reflected(p, 2. * nv * n - v, rough) * (f0 * ab.x + ab.y);
      return (1. - dot(f0, vec3(.299, .587, .114))) * diffuse * probeLight(p, n) + spec + dynamicLight(p, n, v, diffuse, f0, rough, s) + sunLight(p, n, v, diffuse, f0, rough, s);
    }`;
  const brushMaterial = new THREE.ShaderMaterial({
    uniforms,
    extensions: {derivatives: true},
    vertexShader: `attribute vec3 color; attribute vec3 specular; attribute float roughness; attribute float emit; attribute vec2 texcoord; varying vec3 vColor; varying vec3 vSpecular; varying float vRough; varying float vEmit; varying vec3 vNormal; varying vec3 vPos; varying float vDepth; varying vec2 vTex;
      void main() {
        vColor = color; vSpecular = specular; vRough = roughness; vEmit = emit; vNormal = normal; vPos = position; vTex = texcoord;
        vec4 view = modelViewMatrix * vec4(position, 1.);
        vDepth = -view.z;
        gl_Position = projectionMatrix * view;
      }`,
    // The map's light (LIGHTING); while editing, the editor's grid. A textured face (USE_MAP) multiplies its colour by
    // its texture, sRGB, repeating every uRepeat units of the texture coordinates brush.js gives.
    fragmentShader: `${LIGHTING}
      uniform float uGrid; varying vec3 vColor; varying vec3 vSpecular; varying float vRough; varying float vEmit; varying vec3 vNormal; varying vec3 vPos; varying float vDepth; varying vec2 vTex;
      #ifdef USE_MAP
      uniform sampler2D uMap; uniform vec2 uRepeat;
      #endif
      void main() {
        vec3 n = normalize(vNormal);
        vec3 albedo = vColor;
        #ifdef USE_MAP
        vec4 texel = texture2D(uMap, vTex / uRepeat);
        if (texel.a < .5) discard;  // Alpha-keyed, as the game's ALPHAKEYED shaders are.
        albedo *= pow(texel.rgb, vec3(2.2));
        #endif
        vec3 c = vEmit > .5 ? albedo : shade(albedo, vSpecular, vRough, vPos, n, max(-vEmit, 0.) * vColor.r);
        if (uGrid > 0.) {
          vec3 p = vPos / uGrid, w = fwidth(p) + 1e-5;
          vec3 g = abs(fract(p - .5) - .5) / w + abs(n) * 1e3;
          float line = 1. - min(min(min(g.x, g.y), g.z), 1.);
          vec3 q = vPos / (uGrid * 8.), wq = fwidth(q) + 1e-5;
          vec3 gq = abs(fract(q - .5) - .5) / wq + abs(n) * 1e3;
          float major = 1. - min(min(min(gq.x, gq.y), gq.z), 1.);
          c = mix(c, c * .45 + .004, max(line * .5, major) * clamp(1. - vDepth / 3000., 0., 1.));
        }
        gl_FragColor = vec4(c, 1.);
      }`,
  });
  const clipMaterial = new THREE.MeshBasicMaterial({color: 0xb04cff, transparent: true, opacity: .22, depthWrite: false, side: THREE.DoubleSide});
  const selectedFill = new THREE.MeshBasicMaterial({color: 0xf0c674, transparent: true, opacity: .25, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1});
  const selectedEdges = new THREE.LineBasicMaterial({color: 0xf0c674, transparent: true, opacity: .9, depthTest: false});
  // See-through forward shaders (teleporter rings, glass, water, light strips) add their colour to the frame, in linear
  // light: the game's material flags give them its additive blends (assumed from the flags of its materials, the same
  // for its particles called …ADDITIVE; ponytail: its alpha and premultiplied blends are drawn added too). Front faces
  // only: none of these shaders is …DOUBLESIDED, and the game culls back faces.
  const ADDED = {transparent: true, depthWrite: false, side: THREE.FrontSide, blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneFactor};
  const glassMaterial = Object.assign(brushMaterial.clone(), ADDED);
  glassMaterial.uniforms = uniforms;
  // Light beams, race starts and finishes, pickup and powerup glows on brush faces (Phobos, Aerowalk, TheCatalyst):
  // their own shaders are not drawn; they stay faint, a third over what is behind them, from both sides.
  const FAINT = /alphaFresnel|raceStartFinish|glowPickup|powerup/, faint = {key: 'faint'};
  const faintMaterial = Object.assign(brushMaterial.clone(), {transparent: true, depthWrite: false, side: THREE.DoubleSide});
  faintMaterial.uniforms = uniforms;
  faintMaterial.fragmentShader = brushMaterial.fragmentShader.replace('gl_FragColor = vec4(c, 1.);', 'gl_FragColor = vec4(c, .35);');
  const world = new THREE.Mesh(new THREE.BufferGeometry(), [brushMaterial]), clips = new THREE.Mesh(new THREE.BufferGeometry(), clipMaterial);
  const glass = new THREE.Mesh(new THREE.BufferGeometry(), [glassMaterial]);
  // The brushes that are a teleporter's, jump pad's, race start's or finish's or trigger's volume: shown while editing.
  const volumes = new THREE.Mesh(new THREE.BufferGeometry(), []);
  // The edges of the volumes the game's editor does not draw (race starts and finishes), faint, so they can be found;
  // a child of volumes, so shown while editing only and not picked by itself.
  const volumeOutlines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({color: 0x3fc8ff, transparent: true, opacity: .4, depthWrite: false}));
  volumes.add(volumeOutlines);
  const selection = new THREE.Group();
  glass.renderOrder = 1; clips.renderOrder = 2; volumes.renderOrder = 2; selection.renderOrder = 3;
  root.add(world, glass, clips, volumes, selection);

  // Concave faces (a vertex dragged in the game's editor) are cut into triangles by Three's ear clipping.
  function triangulate(points, normal) {
    const axis = Math.abs(normal[0]) > Math.abs(normal[1]) ? (Math.abs(normal[0]) > Math.abs(normal[2]) ? 0 : 2) : (Math.abs(normal[1]) > Math.abs(normal[2]) ? 1 : 2);
    const [a, b] = [[1, 2], [2, 0], [0, 1]][axis];
    return THREE.ShapeUtils.triangulateShape(points.map(p => new THREE.Vector2(p[a], p[b])), []);
  }
  // The faces of `entries` that `wanted` takes, as one geometry; `owners` gets [entry index, face] per triangle. With
  // `textured`, faces are grouped by texture, the untextured first, and `materials` gets the material of each group.
  // `textured` true takes a face's texture from its material; a function (face, entry) gives it instead. A group's
  // material is `materialOf` its texture (none: null).
  function buildMesh(entries, wanted, owners, textured = false, materials = null, materialOf = source => source ? texturedMaterial(source) : brushMaterial) {
    const buckets = new Map([[null, {positions: [], normals: [], colours: [], speculars: [], roughness: [], emits: [], coords: [], owners: []}]]);
    owners.length = 0;
    entries.forEach((entry, index) => {
      const brush = entry.brush;
      for (const face of brush.faces) {
        if (wanted(face, entry) === false) continue;
        const n = B.normalize(B.newell(face.indices.map(i => brush.vertices[i])));
        const {diffuse, specular, roughness, emit} = faceShade(face), source = !textured ? null : textured === true ? textureSource(face) : textured(face, entry), key = source && source.key;
        if (!buckets.has(key)) buckets.set(key, {source, positions: [], normals: [], colours: [], speculars: [], roughness: [], emits: [], coords: [], owners: []});
        const bucket = buckets.get(key), uv = B.texcoords(brush, face, M.isVolume(entry.owner));
        for (const triangle of B.triangles(brush, face, triangulate)) {
          for (const vertex of triangle) {
            bucket.positions.push(...brush.vertices[vertex]);
            bucket.normals.push(...n);
            bucket.colours.push(...diffuse);
            bucket.speculars.push(...specular);
            bucket.roughness.push(roughness);
            bucket.emits.push(emit);
            bucket.coords.push(...uv[face.indices.indexOf(vertex)]);
          }
          bucket.owners.push([index, face]);
        }
      }
    });
    const geometry = new THREE.BufferGeometry(), all = {positions: [], normals: [], colours: [], speculars: [], roughness: [], emits: [], coords: []};
    if (materials) materials.length = 0;
    let start = 0;
    for (const [key, bucket] of buckets) {
      // Copied a value at a time: spreading a large map's arrays into push overflows the stack.
      for (const name in all) { const from = bucket[name], to = all[name]; for (let i = 0; i < from.length; i++) to.push(from[i]); }
      for (const owner of bucket.owners) owners.push(owner);
      const count = bucket.positions.length / 3;
      if (materials && count) { geometry.addGroup(start, count, materials.length); materials.push(materialOf(bucket.source)); }
      start += count;
    }
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(all.positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(all.normals, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(all.colours, 3));
    geometry.setAttribute('specular', new THREE.Float32BufferAttribute(all.speculars, 3));
    geometry.setAttribute('roughness', new THREE.Float32BufferAttribute(all.roughness, 1));
    geometry.setAttribute('emit', new THREE.Float32BufferAttribute(all.emits, 1));
    geometry.setAttribute('texcoord', new THREE.Float32BufferAttribute(all.coords, 2));
    geometry.computeBoundingSphere();
    return geometry;
  }

  // ---- Textures ----
  // A face's texture: one of the game's, which the import decoded from its material (repeating every 128 units at
  // scale 1, as in the game: the dev grid's lines are 16 units apart), or one of Skinner's texture
  // libraries, which a material named skinner/<library>/<file> takes (repeating every half its size in pixels, as
  // Quake 3's default scale of 0.5 has it).
  const LIBRARY = /^skinner\/(t1|t2|q3|reflex)\/(.+)$/;
  function textureSource(face) {
    const name = face.material || '', library = LIBRARY.exec(name);
    if (library) return {key: name.toLowerCase(), url: `/texture/${encodeURIComponent(library[2] + '.png')}?game=${library[1]}`, repeat: null};
    const read = packColours[name];
    return read && read.texture ? {key: 'reflex:' + read.texture, url: `${data}textures/${encodeURIComponent(read.texture)}`, repeat: 128} : null;
  }
  const white = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);
  white.needsUpdate = true;
  const texturedMaterials = new Map(), textureFailures = new Set();
  function texturedMaterial(source) {
    if (texturedMaterials.has(source.key)) return texturedMaterials.get(source.key);
    const material = new THREE.ShaderMaterial({
      uniforms: {...uniforms, uMap: {value: white}, uRepeat: {value: new THREE.Vector2(source.repeat || 128, source.repeat || 128)}},
      defines: {USE_MAP: ''}, extensions: {derivatives: true}, vertexShader: brushMaterial.vertexShader, fragmentShader: brushMaterial.fragmentShader,
    });
    if (source.shine) Object.assign(material, ADDED);
    texturedMaterials.set(source.key, material);
    new THREE.TextureLoader().load(source.url, texture => {
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      texture.flipY = false;
      texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
      texture.needsUpdate = true;
      material.uniforms.uMap.value = texture;
      if (!source.repeat) material.uniforms.uRepeat.value.set(texture.image.width / 2, texture.image.height / 2);
      sunStale = true;  // its cut-out shadow, from the next frame
    }, undefined, () => { textureFailures.add(source.key); showNotes(); });
    return material;
  }

  // A volume as the game's editor draws it (effects.json's volumes): its kind's texture, unlit, 41 % opaque (the
  // texture's alpha), repeating every 16 units at the face's scale 1; race starts and finishes not at all (still
  // picked; the page outlines them faintly). Measured in the game. Packs from before that draw every volume pale blue.
  const volumeSource = (face, entry) => {
    const name = (packEffects.volumes || {})[entry.owner.type], read = name && packColours[name];
    return read && read.texture ? {key: name, url: `${data}textures/${encodeURIComponent(read.texture)}`} : null;
  };
  const volumeMaterials = new Map([[null, new THREE.MeshBasicMaterial({color: 0x3fc8ff, transparent: true, opacity: .2, depthWrite: false, side: THREE.DoubleSide})]]);
  function volumeMaterial(source) {
    if (!source && packEffects.volumes) return volumeMaterials.get('hidden') || volumeMaterials.set('hidden', new THREE.MeshBasicMaterial({visible: false})).get('hidden');
    if (volumeMaterials.has(source && source.key)) return volumeMaterials.get(source && source.key);
    const material = new THREE.MeshBasicMaterial({transparent: true, depthWrite: false, visible: false});  // 41 %: the texture's alpha
    volumeMaterials.set(source.key, material);
    new THREE.TextureLoader().load(source.url, texture => {
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      texture.flipY = false;
      material.map = texture;
      material.visible = true;
      material.needsUpdate = true;
    });
    return material;
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
      // Not drawn where a model shows the entity (the game's editor models, effects, pickups); still picked by.
      const modelled = packEffects.editor && ['PlayerSpawn', 'Target', 'PointLight', 'Effect', 'Pickup', 'WorkshopScreenshot', 'NavLink', 'ReflectionProbe'].includes(entries[0].entity.type);
      const mesh = new THREE.InstancedMesh(markerGeometry, new THREE.MeshBasicMaterial({color: colour, visible: !modelled}), entries.length);
      // Marked while editing only, as the game's play mode shows none (models show effects and pickups).
      mesh.userData.editOnly = true;
      entries.forEach(({position: p}, index) => mesh.setMatrixAt(index, matrix.makeScale(size, size * 1.6, size).setPosition(p[0], p[1], p[2])));
      entries.forEach(entry => markers.push({entry, mesh}));
      entityLayer.add(mesh);
    }
  }

  // ---- Models and lights ----
  // What Effect entities, pickups and teleporters place (effects.json and models/*.bin, written by the import from the
  // game's .effect and .mesh files: tools/reflex_models.py), merged into geometry in the map's coordinates and lit as
  // the brushes are; and the map's lights: PointLight entities and the lights effects carry. Rebuilt with the map
  // (ponytail: every model again on each change; cache per entity if large maps edit slowly).
  let packEffects = {effects: {}, pickups: {}, entities: {}};
  const meshes = new Map();  // model file -> its parts once loaded (null when it could not load)
  // Models' see-through forward shaders add as the brushes' do (ADDED); textured ones through texturedMaterial.
  const shineMaterial = Object.assign(brushMaterial.clone(), ADDED);
  shineMaterial.uniforms = uniforms;
  // Pickup holograms and ammo glows, as the game's hologram and glowPickup shaders shade them (read from its compiled
  // shaders; ponytail: their noise, scan lines and movement are left out, the scan lines as their average): vColor is
  // the material's colour as it is, vTex.x the height up the model over the material's vSize, vTex.y its gradMul.
  // Holograms are brightest facing the viewer and glows at their rim; either is bright enough to bloom.
  const forwardShade = body => Object.assign(new THREE.ShaderMaterial({uniforms, vertexShader: brushMaterial.vertexShader,
    fragmentShader: `uniform vec3 uEye; varying vec3 vColor; varying vec3 vNormal; varying vec3 vPos; varying vec2 vTex;
      float wrap(float x) { return sign(x) * fract(abs(x)); }
      void main() { vec3 n = normalize(vNormal); float facing = clamp(dot(n, normalize(uEye - vPos)), 0., 1.); ${body} }`}), ADDED);
  const FORWARD_SHADES = {
    hologram: forwardShade(`vec3 b = (vColor + .09) * clamp(1. - vTex.x, 0., 1.) * vTex.y + .4 * clamp(wrap(vTex.x * .5) + .5, 0., 1.);
      float f = pow(facing, 8.);
      vec3 x = b * b * (1. + 3.2 * f) + .2 * f;
      x *= (1. + 4. * clamp(-n.y, 0., 1.)) * (1. + 2. * clamp(n.y, 0., 1.));
      gl_FragColor = vec4(pow(1.5 * x, vec3(1.5)) * 1.5, 1.);`),  // × its alpha, 1.5
    glowPickup: forwardShade(`float f = pow(1. - facing, 3.), s = clamp(wrap(vTex.x * .666667) * .75 + .5, 0., 1.);
      float r = 10.24 * f * s * s + .32 * f;
      gl_FragColor = vec4(((.8 * s * vColor + 1.) * r + .05 * s) * vColor, 1.);`),
    // Light beams and pads' glows: sat(sat(n · v)^pow × mul) of their colour (× intensityMul), n turned to the viewer
    // (both sides drawn); mul and pow ride in the texture coordinates.
    alphaFresnel: Object.assign(forwardShade(`float f = clamp(pow(abs(dot(n, normalize(uEye - vPos))), vTex.y) * vTex.x, 0., 1.);
      gl_FragColor = vec4(f * vColor, 1.);`), {side: THREE.DoubleSide}),
  };
  const models = new THREE.Mesh(new THREE.BufferGeometry(), [brushMaterial]), modelShine = new THREE.Mesh(new THREE.BufferGeometry(), [shineMaterial]);
  // What the game shows in its editor only (a reflection probe's sphere).
  const editorModels = new THREE.Mesh(new THREE.BufferGeometry(), [brushMaterial]);
  modelShine.renderOrder = 1;
  // The models' shadow meshes, drawn only into the sun's shadow maps (layer 1). Written by the import as a part of
  // material slot 255.
  const SHADOW_PART = 255, modelShadow = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
  modelShadow.layers.set(1);
  root.add(models, modelShine, editorModels, modelShadow);
  let modelBuild = 0;
  function readModel(buffer) {
    const view = new DataView(buffer), parts = [];
    let at = 4;
    for (let i = view.getUint32(0, true); i > 0; i--) {
      const material = view.getUint32(at, true), vertices = view.getUint32(at + 4, true), indices = view.getUint32(at + 8, true);
      at += 12;
      const positions = new Float32Array(buffer, at, vertices * 3); at += vertices * 12;
      const normals = new Float32Array(buffer, at, vertices * 3); at += vertices * 12;
      const uvs = new Float32Array(buffer, at, vertices * 2); at += vertices * 8;
      const colours = new Uint8Array(buffer, at, vertices * 4); at += vertices * 4;
      parts.push({material, positions, normals, uvs, colours, indices: new Uint32Array(buffer, at, indices)}); at += indices * 4;
    }
    return parts;
  }
  const hexColour = value => { const hex = String(value).replace(/^0x/, '').padStart(8, '0'); return [1, 3, 5, 7].map(i => parseInt(hex.slice(i - 1, i + 1), 16) / 255); };
  const EDITOR_ONLY = /^internal\/misc\/reflectionprobe$/;
  // Each model to draw: {effect, matrix (3 × 4 rows, the map's coordinates), entity, editorOnly}.
  function modelInstances() {
    const placed = [];
    for (const {entity, transform} of flat.entities) {
      const name = entity.type === 'Effect' ? M.property(entity, 'effectName') : entity.type === 'Pickup' ? packEffects.pickups[M.property(entity, 'pickupType') ?? 0]
        : (packEffects.entities || {})[entity.type];
      const effect = name && packEffects.effects[name];
      if (!effect) continue;
      const scale = entity.type === 'Effect' ? M.property(entity, 'effectScale') ?? 1 : 1;
      const at = (lift = 0) => {
        const position = M.property(entity, 'position') || [0, 0, 0];
        const local = M.compose([position[0], position[1] + lift, position[2]], M.property(entity, 'angles') || [0, 0, 0]).map(row => row.map((v, k) => k < 3 ? v * scale : v));
        return transform ? M.multiply(transform, local) : local;
      };
      // A pickup with a pad (health, armour, powerups, weapons) floats above it: 30 units, measured (the game bobs
      // and turns it); ammunition lies on the floor.
      const pad = entity.type === 'Pickup' && packEffects.effects[name.replace(/_pickup$/, '') + '_pad'];
      if (pad) placed.push({effect: pad, matrix: at(), entity, editorOnly: false});
      placed.push({effect, matrix: at(pad ? 30 : 0), entity, editorOnly: EDITOR_ONLY.test(name) || entity.type === 'ReflectionProbe'});
    }
    // While editing, what the game's editor shows for an entity (effects.json's editor list): a spawn, target, point
    // light, screenshot camera or nav link its model, an effect with no model of its own a red "!", and a reflection
    // probe a mirror sphere 16 in radius (the game draws one; measured on screen).
    const shown = packEffects.editor || {};
    for (const {entity, transform} of flat.entities) {
      const position = M.property(entity, 'position');
      if (!position) continue;
      const placeAt = scale => { const local = M.compose(position, M.property(entity, 'angles') || [0, 0, 0]).map(row => row.map((v, k) => k < 3 ? v * scale : v)); return transform ? M.multiply(transform, local) : local; };
      const effectName = entity.type === 'Effect' && M.property(entity, 'effectName');
      if (entity.type === 'ReflectionProbe' || EDITOR_ONLY.test(effectName || '')) { placed.push({effect: PROBE_SPHERE, matrix: placeAt(1), entity, editorOnly: true}); continue; }
      const own = effectName && packEffects.effects[effectName];
      if (own && own.meshes.length) continue;
      const end = entity.type === 'NavLink' && [0, false].includes(M.property(entity, 'isStart'));
      const effect = packEffects.effects[shown[end ? 'NavLinkEnd' : entity.type]];
      if (effect) placed.push({effect, matrix: placeAt(1), entity, editorOnly: true});
    }
    return placed;
  }
  // The reflection probe's sphere: a mirror (metal, white, smooth) that shows the probe it stands in.
  const PROBE_SPHERE = {meshes: [{file: '#sphere', materials: ['#mirror'], colours: [null], scale: 16}], lights: []};
  {
    const sphere = new THREE.SphereGeometry(1, 24, 16), count = sphere.attributes.position.count;
    meshes.set('#sphere', [{material: 0, positions: sphere.attributes.position.array, normals: sphere.attributes.normal.array,
      uvs: sphere.attributes.uv.array, colours: new Uint8Array(count * 4).fill(255), indices: Uint32Array.from(sphere.index.array)}]);
  }
  const LINEAR_BYTE = Array.from({length: 256}, (_, i) => linear(i / 255));
  const ATTRIBUTES = {position: 3, normal: 3, color: 3, specular: 3, roughness: 1, emit: 1, texcoord: 2};
  let modelsDrawn = '';  // what the model geometry was last built from
  function buildModels() {
    const build = ++modelBuild, placed = modelInstances();
    const wanted = [...new Set(placed.flatMap(({effect}) => effect.meshes.map(record => record.file)))].filter(file => !meshes.has(file));
    buildLights(placed.filter(item => !item.editorOnly));
    buildParticles(placed.filter(item => !item.editorOnly));
    if (wanted.length) {
      Promise.all(wanted.map(file => fetch(`${data}models/${encodeURIComponent(file)}`).then(r => r.ok ? r.arrayBuffer() : null).catch(() => null)
        .then(buffer => meshes.set(file, buffer && readModel(buffer))))).then(() => { if (build === modelBuild) buildModels(); });
    }
    // An edit that moves no model (most change brushes) keeps the merged geometry.
    const drawn = placed.map(({effect, matrix, entity, editorOnly}) => effect.meshes.map(record => record.file + (meshes.get(record.file) ? '' : '?')).join() +
      JSON.stringify(matrix) + JSON.stringify(entity.properties) + editorOnly).join('|');
    if (drawn !== modelsDrawn) { modelsDrawn = drawn; mergeModels(placed); }
    editorModels.visible = editing;
    updateSun();
    applySky();
  }
  // Every placed mesh part into the geometry of where it goes: lit (per texture, untextured first), shining, or the
  // editor's; planned first (material, colour, count), then written into arrays of their final size.
  function mergeModels(placed) {
    const lit = new Map([[null, {source: null, plans: [], count: 0}]]), shine = new Map([[null, {source: null, plans: [], count: 0}]]), editor = {source: null, plans: [], count: 0};
    const shadowPlans = [];
    for (const {effect, matrix, entity, editorOnly} of placed) {
      for (const record of effect.meshes) {
        const parts = meshes.get(record.file);
        if (!parts) continue;
        const ownShadow = parts.some(part => part.material === SHADOW_PART);
        for (const part of parts) {
          if (part.material === SHADOW_PART || (!ownShadow && !editorOnly)) {
            if (!editorOnly) shadowPlans.push({part, matrix, scale: record.scale || 1});
            if (part.material === SHADOW_PART) continue;
          }
          // The material: the entity's (materialNName), else the effect's, else the mesh's; the colour: the entity's
          // (materialNAlbedo), else the effect's, else the material's, raised to 2.2 as the game does. Parts given a
          // clip material are not drawn, as the game draws no clip faces.
          const slot = part.material, name = M.property(entity, `material${slot}Name`) || record.materials[slot] || '';
          // God rays and light beams fade with the view in the game; they are left out.
          const read = packColours[name] || {};
          if (isClip({material: name}) || /godrays/.test(read.shader || '')) continue;
          const own = M.property(entity, `material${slot}Albedo`), ownColour = own !== undefined && hexColour(own);
          const set = record.colours[slot], material = materialOf(name), glow = glowOf(name);
          const given = ownColour && ownColour[0] > 0 ? ownColour.slice(1) : set ? set.slice(0, 3) : null;
          // Holograms, pickup glows and the see-through forward shaders (standard_…: rings, ribbons, glass) add their
          // colour to the frame; glows and solid forward shaders are solid and shine.
          const shader = read.shader || '', kind = (/hologram|glowPickup|alphaFresnel/.exec(shader) || [])[0], fresnel = kind === 'alphaFresnel' && (read.fresnel || [.1, 4, .075]);
          const shines = !glow && Boolean(kind || isForward(read) || SEE_THROUGH.test(shader));
          let out = editorOnly ? editor : null;
          if (!out) {
            const texture = !kind && read.texture, key = kind || (texture ? (shines ? 'shine:' : 'model:') + texture : null), into = shines ? shine : lit;
            if (!into.has(key)) into.set(key, {source: kind ? null : {key, url: `${data}textures/${encodeURIComponent(texture)}`, repeat: 1, shine: shines}, material: kind && FORWARD_SHADES[kind], plans: [], count: 0});
            out = into.get(key);
          }
          // Holograms and glows shade their colour as it is; the standard_ shaders raise it to 2.2, and take the
          // mesh's vertex colours only where their name says VERTEXCOLOUR.
          const forward = shines && isForward(read), colour = given || read.colour || [1, 1, 1];
          const shown = fresnel ? colour.map(c => c * fresnel[2]) : kind ? colour : forward ? colour.map(linear) : shines ? colour : given ? given.map(linear) : material.albedo;
          out.plans.push({part, matrix, scale: record.scale || 1, shown, glow, metallic: glow || shines ? 0 : material.metallic,
            emit: glow || shines ? 1 : -(material.sss || 0), roughness: material.roughness, vertexColours: Boolean(fresnel) || (!kind && !(forward && !/VERTEXCOLOUR/.test(shader))),
            gradient: kind && !fresnel && (read.gradient || (kind === 'hologram' ? [56, .75] : [24, .025])), fresnel});
          out.count += part.indices.length;
        }
      }
    }
    const fill = (mesh, buckets, materials) => {
      mesh.geometry.dispose();
      const geometry = mesh.geometry = new THREE.BufferGeometry(), total = buckets.reduce((sum, bucket) => sum + bucket.count, 0);
      const arrays = Object.fromEntries(Object.entries(ATTRIBUTES).map(([name, size]) => [name, new Float32Array(total * size)]));
      const {position, normal, color, specular, roughness, emit, texcoord} = arrays;
      if (materials) materials.length = 0;
      let at = 0;
      for (const bucket of buckets) {
        if (materials && bucket.count) { geometry.addGroup(at, bucket.count, materials.length); materials.push(bucket.material || (bucket.source ? texturedMaterial(bucket.source) : mesh === modelShine ? shineMaterial : brushMaterial)); }
        for (const {part, matrix: m, scale: s, shown, glow, metallic, emit: shining, roughness: rough, vertexColours, gradient, fresnel} of bucket.plans) {
          const {positions, normals, uvs, colours, indices} = part;
          for (const index of indices) {
            const x = positions[index * 3] * s, y = positions[index * 3 + 1] * s, z = positions[index * 3 + 2] * s;
            const nx = normals[index * 3], ny = normals[index * 3 + 1], nz = normals[index * 3 + 2];
            for (let r = 0; r < 3; r++) position[at * 3 + r] = m[r][0] * x + m[r][1] * y + m[r][2] * z + m[r][3];
            const tx = m[0][0] * nx + m[0][1] * ny + m[0][2] * nz, ty = m[1][0] * nx + m[1][1] * ny + m[1][2] * nz, tz = m[2][0] * nx + m[2][1] * ny + m[2][2] * nz, l = Math.hypot(tx, ty, tz) || 1;
            normal[at * 3] = tx / l; normal[at * 3 + 1] = ty / l; normal[at * 3 + 2] = tz / l;
            // A hologram's or glow's shading runs up the mesh as it is (before its scale), over its material's height.
            if (gradient) { texcoord[at * 2] = positions[index * 3 + 1] / gradient[0]; texcoord[at * 2 + 1] = gradient[1]; }
            else if (fresnel) { texcoord[at * 2] = fresnel[0]; texcoord[at * 2 + 1] = fresnel[1]; }
            else { texcoord[at * 2] = uvs[index * 2]; texcoord[at * 2 + 1] = uvs[index * 2 + 1]; }
            // The mesh's vertex colour (BGRA) multiplies the albedo (2.2 too), as the game's stylized shader has it.
            for (let k = 0; k < 3; k++) {
              const base = shown[k] * (vertexColours ? LINEAR_BYTE[colours[index * 4 + 2 - k]] : 1);  // BGRA
              color[at * 3 + k] = glow ? base * glow : base * (1 - metallic);
              specular[at * 3 + k] = shining ? 0 : .015395 * (1 - metallic) + base * metallic;
            }
            roughness[at] = rough; emit[at] = shining;
            at++;
          }
        }
      }
      for (const [name, size] of Object.entries(ATTRIBUTES)) geometry.setAttribute(name, new THREE.BufferAttribute(arrays[name], size));
      geometry.computeBoundingSphere();
    };
    fill(models, [...lit.values()], models.material);
    // The sun's shadows of models: each mesh's shadow mesh (the game casts with it: trees and shrubs by their solid
    // hull, not their leaf cards); a mesh from a pack imported without them casts with what it shows.
    const shadow = [];
    for (const {part, matrix: m, scale: s} of shadowPlans) {
      const {positions, indices} = part;
      for (const index of indices) {
        const x = positions[index * 3] * s, y = positions[index * 3 + 1] * s, z = positions[index * 3 + 2] * s;
        for (let r = 0; r < 3; r++) shadow.push(m[r][0] * x + m[r][1] * y + m[r][2] * z + m[r][3]);
      }
    }
    modelShadow.geometry.dispose();
    modelShadow.geometry = new THREE.BufferGeometry();
    modelShadow.geometry.setAttribute('position', new THREE.Float32BufferAttribute(shadow, 3));
    fill(modelShine, [...shine.values()], modelShine.material);
    fill(editorModels, [editor], editorModels.material);
  }
  // A light's colour as the game's lighting shader gets it: (colour × intensity)², times 0.87 (measured: a white
  // PointLight of intensity 1 over a grey floor, near 0 and far 200, and inside near 100; grey 0x80 gives a quarter,
  // intensity 2 four times). A PointLight without them has near 16, far 128 and intensity 1 (measured, ±4).
  const lightColour = (colour, intensity) => colour.map(c => .87 * (c * intensity) ** 2);
  let lightTextures2 = [];
  // Particles (ponytail: one still sprite per emitter, where the game streams them; bloom does the rest): an
  // additive emitter (its material's flags have 0x40: flames, sparks) as a sprite of the first frame of its
  // texture's flipbook, its birth size wide, in its birth colour^2.2 times the material's albedoIntensity (or its
  // colour^2.2, standard_ shaders), times how many the game keeps alive at once (rate × mean lifetime), at the bone
  // it emits from. Smoke (blended, not added) is left out.
  const particles = new THREE.Group(), particleHeight = {value: 1};
  root.add(particles);
  function buildParticles(placed) {
    for (const points of particles.children.splice(0)) { points.geometry.dispose(); points.material.dispose(); }
    const byTexture = new Map();
    for (const {effect, matrix} of placed) for (const particle of effect.particles || []) {
      const read = packColours[particle.material];
      if (!read || !read.texture || !(read.flags & 0x40)) continue;
      const scale = Math.hypot(matrix[0][0], matrix[1][0], matrix[2][0]), p = particle.position || [0, 0, 0];
      const at = [0, 1, 2].map(r => matrix[r][0] * p[0] + matrix[r][1] * p[1] + matrix[r][2] * p[2] + matrix[r][3]);
      at[1] += particle.size * scale / 2;  // ponytail: flames rise from the emitter; half a sprite up, a guess
      const tint = (read.colour || [1, 1, 1]).map(c => c ** 2.2), gain = read.intensity ?? 1, [r, g, b, a] = particle.colour;
      if (!byTexture.has(read.texture)) byTexture.set(read.texture, {frames: read.flipbook || [1, 1], position: [], colour: [], size: []});
      const into = byTexture.get(read.texture);
      into.position.push(...at);
      into.colour.push(...[r, g, b].map((c, i) => Math.max(c, 0) ** 2.2 * gain * tint[i] * a * (particle.alive || 1)));
      into.size.push(particle.size * scale);
    }
    for (const [texture, {frames, position, colour, size}] of byTexture) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(position, 3));
      geometry.setAttribute('colour', new THREE.Float32BufferAttribute(colour, 3));
      geometry.setAttribute('size', new THREE.Float32BufferAttribute(size, 1));
      const material = new THREE.ShaderMaterial({...ADDED, uniforms: {uMap: {value: white}, uFrame: {value: new THREE.Vector2(1 / frames[1], 1 / frames[0])}, uHeight: particleHeight},
        vertexShader: `attribute vec3 colour; attribute float size; uniform float uHeight; varying vec3 vColour;
          void main() { vColour = colour; vec4 view = modelViewMatrix * vec4(position, 1.); gl_Position = projectionMatrix * view;
            gl_PointSize = size * projectionMatrix[1][1] * uHeight * .5 / max(-view.z, 1.); }`,
        fragmentShader: `uniform sampler2D uMap; uniform vec2 uFrame; varying vec3 vColour;
          void main() { vec4 t = texture2D(uMap, gl_PointCoord * uFrame); gl_FragColor = vec4(pow(t.rgb, vec3(2.2)) * t.a * vColour, 1.); }`});
      new THREE.TextureLoader().load(`${data}textures/${encodeURIComponent(texture)}`, map => { map.flipY = false; map.needsUpdate = true; material.uniforms.uMap.value = map; });
      particles.add(new THREE.Points(geometry, material));
    }
  }
  function buildLights(placed) {
    const lights = [];
    for (const {entity, position} of flat.entities) {
      if (entity.type !== 'PointLight' || !position) continue;
      const colour = hexColour(M.property(entity, 'color') ?? 'ffffffff').slice(1);
      lights.push({position, colour: lightColour(colour, M.property(entity, 'intensity') ?? 1), near: M.property(entity, 'nearAttenuation') ?? 16, far: M.property(entity, 'farAttenuation') ?? 128});
    }
    // An effect's own lights, where its mesh's bone puts them (a spot shining along the bone's x axis), unless the
    // entity overrides them (pointLightOverridden, spotLightOverridden with the values it gives).
    for (const {effect, matrix, entity} of placed) {
      for (const light of effect.lights) {
        const spot = light.kind === 'spot', prefix = spot ? 'spotLight' : 'pointLight', own = M.property(entity, prefix + 'Overridden');
        const value = (name, fallback) => own ? M.property(entity, prefix + name) ?? fallback : fallback;
        const colourHex = own && M.property(entity, prefix + 'Color');
        const colour = colourHex !== undefined && colourHex !== false ? hexColour(colourHex).slice(1) : light.colour;
        const p = light.position || [0, 0, 0], at = [0, 1, 2].map(r => matrix[r][0] * p[0] + matrix[r][1] * p[1] + matrix[r][2] * p[2] + matrix[r][3]);
        // The effect's scale (effectScale, and its prefab's) scales the light's reach too, overridden or not.
        const scale = Math.hypot(matrix[0][0], matrix[1][0], matrix[2][0]);
        const entry = {position: at, colour: lightColour(colour, value('Intensity', light.intensity)), near: value('Near', light.near) * scale, far: value('Far', light.far) * scale};
        if (spot) {
          const along = light.direction || [0, -1, 0], d = [0, 1, 2].map(r => matrix[r][0] * along[0] + matrix[r][1] * along[1] + matrix[r][2] * along[2]), l = Math.hypot(...d) || 1;
          entry.direction = d.map(c => c / l);
          entry.cosInner = Math.cos(value('InnerAnglesDegrees', light.inner) * Math.PI / 180);
          entry.cosOuter = Math.cos(value('OuterAnglesDegrees', light.outer) * Math.PI / 180);
        }
        if (entry.far > entry.near && Math.max(...entry.colour) > 0) lights.push(entry);
      }
    }
    for (const texture of lightTextures2.splice(0)) texture.dispose();
    const grid = L.lightGrid(lights);
    const texture = (tex, more) => { Object.assign(tex, {type: THREE.FloatType, minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, needsUpdate: true}, more); lightTextures2.push(tex); return tex; };
    uniforms.uLights.value = texture(new THREE.DataTexture(grid.data, 4, Math.max(1, grid.count)), {format: THREE.RGBAFormat});
    uniforms.uLightList.value = texture(new THREE.DataTexture(grid.list, Math.min(4096, grid.list.length), Math.ceil(grid.list.length / 4096)), {format: THREE.RedFormat});
    if (grid.list.length > 4096) uniforms.uLightList.value.image.data = Float32Array.from({length: 4096 * Math.ceil(grid.list.length / 4096)}, (_, i) => grid.list[i] || 0);
    uniforms.uLightCells.value = texture(new THREE.Data3DTexture(grid.cells, ...grid.size), {format: THREE.RGFormat});
    uniforms.uLightOrigin.value.fromArray(grid.origin); uniforms.uLightSize.value = grid.size; uniforms.uLightCell.value = grid.cell;
    uniforms.uLightCount.value = grid.count;
  }

  // ---- The map ----
  const globalGroup = () => M.global(map);
  function rebuild() {
    flat = M.flatten(map);
    playWorld = null; player = null;
    if (openPrefab && !globalGroup().items.some(item => brokenFrom.get(item) === openPrefab.from)) openPrefab = null;
    ownerOfItem = new Map(flat.brushes.filter(entry => !entry.path.length).map(entry => [entry.source, entry.owner]));
    for (const mesh of [world, clips, glass, volumes]) mesh.geometry.dispose();
    const volume = entry => M.isVolume(entry.owner);
    world.geometry = buildMesh(flat.brushes, (face, entry) => !volume(entry) && !isClip(face) && !isSeeThrough(face), entryOfTriangle, true, world.material);
    glass.geometry = buildMesh(flat.brushes, (face, entry) => !volume(entry) && !isClip(face) && isSeeThrough(face), glassEntryOfTriangle,
      face => FAINT.test((packColours[face.material || ''] || {}).shader || '') ? faint : null, glass.material, source => source ? faintMaterial : glassMaterial);
    clips.geometry = buildMesh(flat.brushes, (face, entry) => !volume(entry) && isClip(face), clipEntryOfTriangle);
    volumes.geometry = buildMesh(flat.brushes, (face, entry) => volume(entry), volumeEntryOfTriangle, volumeSource, volumes.material, volumeMaterial);
    volumes.geometry.setAttribute('uv', new THREE.Float32BufferAttribute(volumes.geometry.attributes.texcoord.array.map(v => v / 16), 2));
    volumeOutlines.geometry.dispose();
    const unseen = volumes.geometry.groups.filter(group => volumes.material[group.materialIndex] === volumeMaterials.get('hidden')), corners = [];
    for (const {start, count} of unseen) corners.push(...volumes.geometry.attributes.position.array.subarray(start * 3, (start + count) * 3));
    const outlined = new THREE.BufferGeometry();
    outlined.setAttribute('position', new THREE.Float32BufferAttribute(corners, 3));
    volumeOutlines.geometry = new THREE.EdgesGeometry(outlined);
    outlined.dispose();
    buildEntities();
    for (const mesh of entityLayer.children) if (mesh.userData.editOnly) mesh.visible = editing;
    buildModels();
    showSelection();
  }

  function worldSpawn() { return (globalGroup().items || []).find(item => item.kind === 'entity' && item.type === 'WorldSpawn'); }
  // The sun: up from the horizon in the direction sky.skyAngle turns +z by at 6:00 (sky.timeOfDay), overhead at
  // 12:00, down on the far side at 18:00, 15° an hour (measured with a pole's shadow at 6, 9, 12, 15 and 18, and at
  // 9 and 12 turned 90°); a map that gives neither is at 14:00 turned 30° (measured: Aerowalk and Phobos give no
  // angle); its light (2.94, 2.65, 2.13) at full incidence on a grey floor (measured at noon). Clip brushes cast no
  // shadow (measured). Its
  // shadows from two depth maps of the brushes and models: one of the whole map, and a finer one of the 2048 units
  // around the viewer, drawn again when the viewer has moved 256 (ponytail: two cascades; the game has four).
  const SUN_LIGHT = [2.94, 2.65, 2.13];
  const shadowMap = () => new THREE.WebGLRenderTarget(4096, 4096, {depthTexture: new THREE.DepthTexture(4096, 4096), depthBuffer: true});
  const sunTarget = shadowMap(), nearTarget = shadowMap();
  const sunCamera = new THREE.OrthographicCamera(), shadowCaster = new THREE.MeshBasicMaterial();
  // A textured material casts through its texture where the brush shader keeps it (alpha ≥ ½): leaves and other
  // alpha-keyed cards cast their shape, not their card.
  const cutCasters = new WeakMap();
  const casterOf = material => {
    if (!material || !material.defines || material.defines.USE_MAP === undefined) return shadowCaster;
    if (!cutCasters.has(material)) cutCasters.set(material, new THREE.ShaderMaterial({uniforms: {uMap: material.uniforms.uMap, uRepeat: material.uniforms.uRepeat}, side: material.side,
      vertexShader: 'attribute vec2 texcoord; varying vec2 vTex; void main() { vTex = texcoord; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }',
      fragmentShader: 'uniform sampler2D uMap; uniform vec2 uRepeat; varying vec2 vTex; void main() { if (texture2D(uMap, vTex / uRepeat).a < .5) discard; gl_FragColor = vec4(1.); }'}));
    return cutCasters.get(material);
  };
  let sunStale = false;
  sunCamera.layers.set(1);
  world.layers.enable(1);
  let sunReach = 0, nearAt = null;
  // One shadow map: Three's coordinates (z mirrored), looking down the sun's way at `middle`, `half` wide each way,
  // deep enough for all of the map; the bias of a texel in its depth.
  function sunPass(target, matrix, middle, half) {
    const toward = new THREE.Vector3(uniforms.uSun.value.x, uniforms.uSun.value.y, -uniforms.uSun.value.z);
    sunCamera.position.copy(middle).addScaledVector(toward, sunReach * 2);
    sunCamera.lookAt(middle);
    Object.assign(sunCamera, {left: -half, right: half, top: half, bottom: -half, near: 0, far: sunReach * 4});
    sunCamera.updateProjectionMatrix(); sunCamera.updateMatrixWorld();
    matrix.multiplyMatrices(sunCamera.projectionMatrix, sunCamera.matrixWorldInverse);
    const was = {target: renderer.getRenderTarget(), background: scene.background}, materials = world.material;
    world.material = [].concat(world.material).map(casterOf);
    scene.background = null;
    renderer.setRenderTarget(target);
    renderer.clear();
    renderer.render(scene, sunCamera);
    renderer.setRenderTarget(was.target);
    world.material = materials;
    scene.background = was.background;
    return half / 2048 / (sunReach * 4);
  }
  function updateSun() {
    const spawn = worldSpawn(), hour = Number((spawn && M.property(spawn, 'sky.timeOfDay')) ?? 14), turn = Number((spawn && M.property(spawn, 'sky.skyAngle')) ?? 30) * Math.PI / 180;
    const height = (hour - 6) * 15 * Math.PI / 180, enabled = !spawn || M.property(spawn, 'sky.sunEnabled') !== 0;
    const sun = new THREE.Vector3(Math.sin(turn) * Math.cos(height), Math.sin(height), Math.cos(turn) * Math.cos(height)).normalize();
    uniforms.uSun.value.copy(sun);
    uniforms.uSunColour.value.fromArray(SUN_LIGHT);
    uniforms.uSunTexel.value = 0;
    nearAt = null; sunReach = 0;
    if (!enabled || sun.y <= 0) return;
    const box = new THREE.Box3().setFromObject(world).union(new THREE.Box3().setFromObject(models));
    if (box.isEmpty()) return;
    sunReach = box.getSize(new THREE.Vector3()).length() / 2 + 16;
    uniforms.uSunBias.value = sunPass(sunTarget, uniforms.uSunMatrix.value, box.getCenter(new THREE.Vector3()), sunReach);
    uniforms.uSunDepth.value = sunTarget.depthTexture;
    uniforms.uSunTexel.value = 1 / 4096;
    followSun();
  }
  // Before each frame: the fine shadow map again where the viewer has gone.
  function followSun() {
    if (sunStale) { sunStale = false; updateSun(); return; }
    if (!sunReach || (nearAt && nearAt.distanceTo(camera.position) < 256)) return;
    nearAt = camera.position.clone();
    uniforms.uNearBias.value = sunPass(nearTarget, uniforms.uNearMatrix.value, nearAt, 1024);
    uniforms.uNearDepth.value = nearTarget.depthTexture;
  }
  // ---- Sky ----
  // The game's sky, from its compiled shaders (internal/shaders/sky2 and clouds) and, where the WorldSpawn gives no
  // value, the defaults its WorldSpawn starts with (read from reflex.exe; its 14:00 and 30° are the sun's measured
  // defaults). A dome of directions d: above sky.horizonLine the horizon colour toward the top colour (all of it from
  // d.y 1/2 up), below it the bottom colour toward the horizon's (from d.y −1/2), each times its intensity; a halo
  // (max(1 − |line − d.y|, 0) × horizonColor)^haloExponent × horizonIntensity; above the line the sun, s^(sharpness ×
  // 100) × sunColor × sunIntensitySize, and its halo s^haloExponentSun × horizonColor × haloExponentSunIntensity, s
  // the cosine to the sun. Colours are the bytes / 255 as they are (no 2.2: the default blue so matches the game's).
  // Stars: the sky material's star texture (sky_stars_c) seen along d's three axes, each weighted by that axis^6, times
  // starsIntensity, faded out toward the horizon line (by its halo^(haloExponent / 10)).
  // The clouds: the game's cloud dome (sky_clouds1, mesh and texture from the import) in cloudsColor, as covered as
  // its texture's red twice (once ½ along), plus bias, says, ((n − ½) × roughness + ½)^((1 − coverage) × multiplier)
  // × density × thickness, faded by the dome's vertex colour; drifting with time × cloudsSpeed.
  const SKY_DEFAULTS = {skyTopColor: 'ff1a6bd4', skyHorizonColor: 'ff599dff', skyBottomColor: 'ffb1d4f2', skyTopColorIntensity: .5,
    skyHorizonColorIntensity: .5, skyBottomColorIntensity: .5, sunColor: 'ffffe6b9', sunIntensitySize: 16, sunSharpness: 32, horizonColor: 'ffffb644',
    horizonIntensity: .2, horizonHaloExponent: 3, horizonHaloExponentSun: 4, horizonHaloExponentSunIntensity: .2, horizonLine: -.1,
    cloudsColor: 'ffb2b2b2', cloudsCoverage: .8, 'cloudsSpeed.x': 1, 'cloudsSpeed.y': .01, cloudsCoverageMultiplier: 16, cloudsBias: .1, cloudsRoughness: .1,
    cloudsDensity: .8, cloudsThickness: .8, starsIntensity: 0};
  // The fog a map leaves out: WorldSpawn's defaults in reflex.exe (.data 0x1409858d8, after the sky's), faint at map scale.
  const FOG_DEFAULTS = {fogColor: 'ffb4e1ff', fogDistanceStart: 512, fogDistanceEnd: 8192, fogHeightTop: 0, fogHeightBottom: -8192};
  const skyUniforms = {uSun: uniforms.uSun, uTop: {value: new THREE.Vector3()}, uHorizon: {value: new THREE.Vector3()}, uBottom: {value: new THREE.Vector3()},
    uSunColour: {value: new THREE.Vector3()}, uHalo: {value: new THREE.Vector3()}, uHaloIntensity: {value: 0}, uHaloExponent: {value: 1}, uHaloSun: {value: 1},
    uHaloSunIntensity: {value: 0}, uSharpness: {value: 1}, uLine: {value: 0},
    uStars: {value: white}, uStarsIntensity: {value: 0}, uTime: {value: 0}, uCloudSpeed: {value: new THREE.Vector2()},
    uClouds: {value: white}, uCloudColour: {value: new THREE.Vector3()}, uCloud: {value: new THREE.Vector4()}, uCloudLook: {value: new THREE.Vector2()}};
  const skyVertex = `attribute vec2 texcoord; attribute float fade; varying vec3 vDir; varying vec2 vTex; varying float vFade;
    void main() { vDir = position; vTex = texcoord; vFade = fade; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }`;
  const skyMaterial = new THREE.ShaderMaterial({uniforms: skyUniforms, side: THREE.DoubleSide, depthWrite: false, vertexShader: skyVertex,
    fragmentShader: `uniform vec3 uSun, uTop, uHorizon, uBottom, uSunColour, uHalo; uniform float uHaloIntensity, uHaloExponent, uHaloSun, uHaloSunIntensity, uSharpness, uLine;
      uniform sampler2D uStars; uniform float uStarsIntensity; varying vec3 vDir;
      float star(vec2 at) { return pow(texture2D(uStars, at).r, 2.2); }
      void main() {
        vec3 d = normalize(vDir); float s = clamp(dot(uSun, d), 0., 1.), above = d.y > uLine ? 1. : 0.;
        vec3 c = mix(mix(uBottom, uHorizon, clamp(2. * d.y + 1., 0., 1.)), mix(uHorizon, uTop, clamp(2. * d.y - 1., 0., 1.)), above);
        c += pow(max(1. - abs(uLine - d.y), 0.) * uHalo, vec3(uHaloExponent)) * uHaloIntensity;
        c += above * (pow(s, uHaloSun) * uHalo * uHaloSunIntensity + pow(s, uSharpness * 100.) * uSunColour);
        vec3 w = pow(abs(d), vec3(6.));
        float stars = (star(d.xy) * w.z + star(d.xz) * w.y + star(d.zy) * w.x) * uStarsIntensity;
        c += stars * (1. - min(pow(abs(1. - abs(uLine - d.y)), uHaloExponent * .1), 1.));
        gl_FragColor = vec4(c, 1.);
      }`});
  const cloudMaterial = new THREE.ShaderMaterial({uniforms: skyUniforms, side: THREE.DoubleSide, transparent: true, depthWrite: false, vertexShader: skyVertex,
    fragmentShader: `uniform sampler2D uClouds; uniform vec3 uCloudColour; uniform vec4 uCloud; uniform vec2 uCloudLook, uCloudSpeed; uniform float uTime; varying vec2 vTex; varying float vFade;
      void main() {
        vec2 s = uTime * uCloudSpeed, grow = 1. + s.yx * .00005;
        float n = pow(texture2D(uClouds, vTex * grow + s * .0001).r, 2.2) + pow(texture2D(uClouds, (vTex + .5 + s * .0002) * grow + s * .0001).r, 2.2) + uCloudLook.x;
        float m = (n - .5) * uCloudLook.y + .5;
        float a = clamp(clamp(min(pow(abs(m), (1. - uCloud.x) * uCloud.y), 1.) * uCloud.z, 0., 1.) * uCloud.w, 0., 1.) * vFade;
        gl_FragColor = vec4(uCloudColour, a);
      }`});
  // Both domes (radius 5 in the game's files) around the viewer, far off, in the game's coordinates (z mirrored).
  const skyDome = new THREE.Group(), skyMesh = new THREE.Mesh(new THREE.SphereGeometry(5, 48, 24), skyMaterial), cloudMesh = new THREE.Mesh(new THREE.BufferGeometry(), cloudMaterial);
  skyMesh.geometry.setAttribute('fade', new THREE.Float32BufferAttribute(new Float32Array(skyMesh.geometry.attributes.position.count), 1));
  skyMesh.geometry.setAttribute('texcoord', skyMesh.geometry.attributes.uv);
  skyDome.add(skyMesh, cloudMesh);
  skyDome.scale.set(6000, 6000, -6000);
  skyMesh.renderOrder = -2; cloudMesh.renderOrder = -1;
  for (const mesh of [skyMesh, cloudMesh]) { mesh.frustumCulled = false; mesh.raycast = () => {}; }
  scene.add(skyDome);
  skyMesh.onBeforeRender = (_, __, view) => { skyDome.position.copy(view.position); skyDome.updateMatrixWorld(); };
  let cloudsDrawn = null;
  function applySky() {
    const spawn = worldSpawn(), value = name => (spawn && M.property(spawn, 'sky.' + name)) ?? SKY_DEFAULTS[name];
    const colour = name => new THREE.Vector3(...hexColour(value(name)).slice(1));
    const u = skyUniforms;
    u.uTop.value.copy(colour('skyTopColor')).multiplyScalar(value('skyTopColorIntensity'));
    u.uHorizon.value.copy(colour('skyHorizonColor')).multiplyScalar(value('skyHorizonColorIntensity'));
    u.uBottom.value.copy(colour('skyBottomColor')).multiplyScalar(value('skyBottomColorIntensity'));
    u.uSunColour.value.copy(colour('sunColor')).multiplyScalar(value('sunIntensitySize'));
    u.uHalo.value.copy(colour('horizonColor'));
    Object.assign(u.uHaloIntensity, {value: value('horizonIntensity')}); Object.assign(u.uHaloExponent, {value: value('horizonHaloExponent')});
    Object.assign(u.uHaloSun, {value: value('horizonHaloExponentSun')}); Object.assign(u.uHaloSunIntensity, {value: value('horizonHaloExponentSunIntensity')});
    Object.assign(u.uSharpness, {value: value('sunSharpness')}); Object.assign(u.uLine, {value: value('horizonLine')});
    u.uCloudColour.value.copy(colour('cloudsColor'));
    u.uCloud.value.set(value('cloudsCoverage'), value('cloudsCoverageMultiplier'), value('cloudsDensity'), value('cloudsThickness'));
    u.uCloudLook.value.set(value('cloudsBias'), value('cloudsRoughness'));
    u.uCloudSpeed.value.set(value('cloudsSpeed.x'), value('cloudsSpeed.y'));
    u.uStarsIntensity.value = value('starsIntensity');
    const stars = (packColours['internal/world/skies/sky2'] || {}).texture;
    if (stars && u.uStars.value === white) new THREE.TextureLoader().load(`${data}textures/${encodeURIComponent(stars)}`, texture => {
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping; texture.flipY = false; texture.needsUpdate = true;
      u.uStars.value = texture;
    });
    // The fog (the frame's composite pass), as reflex.exe hands it over: start kept below end, bottom below top.
    const fogged = (name, fallback) => (spawn && M.property(spawn, name)) ?? FOG_DEFAULTS[name] ?? fallback;
    fog.uFogColour.value.fromArray(hexColour(fogged('fogColor')).slice(1));
    const end = fogged('fogDistanceEnd'), top = fogged('fogHeightTop');
    fog.uFog.value.set(Math.min(fogged('fogDistanceStart'), end - .001), end, top, Math.min(fogged('fogHeightBottom'), top - .001));
    scene.background = null;
    // The cloud dome, once its mesh has loaded (packs from before it have none: no clouds).
    const effect = packEffects.effects[(packEffects.sky || {}).clouds], record = effect && effect.meshes[0];
    if (!record || cloudsDrawn === record.file) return;
    if (!meshes.has(record.file)) {
      fetch(`${data}models/${encodeURIComponent(record.file)}`).then(r => r.ok ? r.arrayBuffer() : null).catch(() => null)
        .then(buffer => { meshes.set(record.file, buffer && readModel(buffer)); applySky(); });
      return;
    }
    const part = (meshes.get(record.file) || [])[0], read = packColours[record.materials[0]] || {};
    if (!part) return;
    cloudsDrawn = record.file;
    const geometry = new THREE.BufferGeometry(), count = part.positions.length / 3;
    geometry.setAttribute('position', new THREE.BufferAttribute(part.positions, 3));
    geometry.setAttribute('texcoord', new THREE.BufferAttribute(part.uvs, 2));
    geometry.setAttribute('fade', new THREE.Float32BufferAttribute(Float32Array.from({length: count}, (_, i) => LINEAR_BYTE[part.colours[i * 4 + 2]]), 1));
    geometry.setIndex(new THREE.BufferAttribute(part.indices, 1));
    cloudMesh.geometry.dispose(); cloudMesh.geometry = geometry;
    if (read.texture) new THREE.TextureLoader().load(`${data}textures/${encodeURIComponent(read.texture)}`, texture => {
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping; texture.flipY = false; texture.needsUpdate = true;
      u.uClouds.value = texture;
    });
  }

  // Viewpoints: the camera the map ends on (its WorldSpawn's targetGameOverCamera) first, then the spawn points.
  // Yaw 0 looks along +z and turns toward +x, as prefabs turn; positive pitch is taken to look down.
  function viewpoints() {
    if (!flat) return [];
    const end = worldSpawn() && M.property(worldSpawn(), 'targetGameOverCamera');
    const of = type => flat.entities.filter(({entity, position}) => entity.type === type && position);
    return [...of('Target').filter(({entity}) => M.property(entity, 'name') === end), ...of('PlayerSpawn')]
      .map(({entity, position}) => ({position, angles: M.property(entity, 'angles') || [0, 0, 0], height: entity.type === 'PlayerSpawn' ? 48 : 0, spawn: entity.type === 'PlayerSpawn'}));
  }
  // Walking, 1–9 are the spawn points only: the end camera is often out in the air.
  function showViewpoint(index) {
    const all = viewpoints(), spawns = all.filter(view => view.spawn), view = (!editing && walking && spawns.length ? spawns : all)[index];
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
    if (flat) for (const {brush, owner} of flat.brushes) if (!M.isVolume(owner)) for (const face of brush.faces) if (!M.colourOf(face) && !isClip(face) && !LIBRARY.test(face.material || '') && !(packColours[face.material || ''] || {}).colour) guessed.add(face.material || 'no material');
    if (guessed.size) parts.push(`Drawn in a colour guessed from the material's name, as the import found no albedo for it: ${[...guessed].sort().join(', ')}`);
    if (textureFailures.size) parts.push(`Textures that could not load: ${[...textureFailures].join(', ')}`);
    parts.push(...notes);
    $('mapNotes').textContent = parts.length ? ' This map — ' + parts.join('. ') + '.' : '';
  }
  $('notes').after(Object.assign(document.createElement('span'), {id: 'mapNotes'}));

  // The baked light of an imported map (light.js) as textures: the seven probe planes as half floats, the reflection
  // probe of each cell, and every reflection probe's six faces as layers of one texture (mips made by the GPU).
  const lightTextures = [];
  function useLight(light) {
    for (const texture of lightTextures.splice(0)) texture.dispose();
    uniforms.uLit.value = uniforms.uCubes.value = 0;
    for (let i = 0; i < 7; i++) uniforms['uSH' + i].value = empty3D;
    uniforms.uProbeIndex.value = empty3D; uniforms.uCube.value = emptyArray;
    if (!light) return;
    const [nx, ny, nz] = light.size, grid = data => {
      const texture = new THREE.Data3DTexture(data, nx, ny, nz);
      Object.assign(texture, {minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, needsUpdate: true});
      lightTextures.push(texture);
      return texture;
    };
    light.planes.forEach((plane, i) => { uniforms['uSH' + i].value = Object.assign(grid(plane), {type: THREE.HalfFloatType}); });
    uniforms.uProbeIndex.value = Object.assign(grid(light.indices), {format: THREE.RedFormat, unpackAlignment: 1, minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter});
    uniforms.uProbeScale.value.fromArray(light.scale); uniforms.uProbeOffset.value.fromArray(light.offset);
    uniforms.uLit.value = 1;
    // As many probes as the GPU's array textures hold (WebGL 2 promises 256 layers, 42 probes; Phobos has 49); a cell
    // naming one beyond takes the last.
    const gl = renderer.getContext(), kept = light.cubes.slice(0, Math.floor(gl.getParameter(gl.MAX_ARRAY_TEXTURE_LAYERS) / 6));
    const side = light.cubeSize, layer = side * side * 4, faces = new Uint8Array(layer * 6 * kept.length);
    kept.forEach((cube, i) => cube.faces.forEach((levels, face) => faces.set(L.decodeBC1(levels[0].data, side), (i * 6 + face) * layer)));
    if (kept.length) {
      const cubes = new THREE.DataArrayTexture(faces, side, side, kept.length * 6);
      Object.assign(cubes, {minFilter: THREE.LinearMipmapLinearFilter, magFilter: THREE.LinearFilter, generateMipmaps: true, needsUpdate: true});
      lightTextures.push(cubes);
      uniforms.uCube.value = cubes; uniforms.uCubes.value = kept.length; uniforms.uCubeMips.value = light.cubeMips;
    }
  }

  function load(text, name) {
    useLight(null);
    const parsed = M.parse(text);
    if (!M.global(parsed)) parsed.groups.push({kind: 'global', name: '', items: []});
    map = parsed; mapName = name; selected.clear(); undoStack.length = 0; redoStack.length = 0;
    rebuild();
    showViewpoint(0);
    showNotes();
    document.title = `${name} — Reflex Maps`;
    showReady();
  }
  function showReady() {
    const editable = globalGroup().items.filter(item => item.kind === 'brush').length;
    showStatus(`${mapName}: ${flat.brushes.length} brushes (${editable} in the map, ${flat.brushes.length - editable} placed by prefabs), ` +
      `${flat.entities.length} entities · ${!editing && walking ? 'walking' : `speed ${Math.round(speed)}`}`);
    updateTools();
  }

  // ---- Editing ----
  const selected = new Set(), undoStack = [], redoStack = [];
  const snap = value => Math.round(value / settings.grid) * settings.grid;
  let lastPoint = null, lastNormal = null, createType = 0, vertexMode = false;
  // The bridge tool: the face Shift-click picked, whether B is aiming from it, the steps, and what a click would make.
  let bridging = false, segments = 4, bridgePreview = null;
  // Faces Shift-click picked, {item, index} (Ctrl+Shift-click adds and removes): the texture keys and Shift+M work on
  // them, and B bridges from the last.
  let picked = [];
  const bridgeFace = () => { const last = picked[picked.length - 1]; return last ? pickFace({entry: {source: last.item}, face: last.item.faces[last.index]}) : null; };
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

  // A step of undo is the lists of every group (the map's and its prefabs') and what was selected, so undoing brings
  // back both.
  const snapshot = () => ({groups: map.groups.map(group => ({...group, items: group.items.slice()})), selected: [...selected]});
  function remember() {
    undoStack.push(snapshot());
    if (undoStack.length > 100) undoStack.shift();
    redoStack.length = 0;
  }
  function restore(from, to) {
    if (!from.length) return;
    to.push(snapshot());
    const step = from.pop();
    map.groups = step.groups;
    selected.clear();
    const items = globalGroup().items;
    for (const item of step.selected) if (items.includes(item)) selected.add(item);
    rebuild(); showReady();
  }
  // Changes the map's list, as one step of undo. `changes` maps an item to the items (or brushes) standing in its
  // place, which so keep its owner; `inserts` adds things at a place: 'world' (in the WorldSpawn's run of brushes, so a
  // brush is the world's and not that of whatever entity comes last), 'end', or after an item. `alter`, when given,
  // changes the map's prefabs in the same step.
  const asItem = thing => thing.kind ? thing : {kind: 'brush', vertices: thing.vertices, faces: thing.faces};
  // Items that came out of breaking a prefab, with the prefab and its placement, so me_updateprefab can put them back;
  // what stands in an item's place, and its clones, inherit it.
  const brokenFrom = new WeakMap();
  function replace(changes, inserts = [], select = null, alter = null) {
    remember();
    if (alter) alter();
    const items = [], changed = [], added = [];
    for (const item of globalGroup().items) {
      if (!changes.has(item)) { items.push(item); continue; }
      const instead = changes.get(item).map(asItem);
      if (instead.length === 1 && instead[0].kind === 'brush' && instead[0].faces.length === (item.faces || []).length) for (const face of picked) if (face.item === item) face.item = instead[0];
      if (brokenFrom.has(item)) for (const next of instead) if (!brokenFrom.has(next)) brokenFrom.set(next, brokenFrom.get(item));
      items.push(...instead);
      changed.push(...instead);
    }
    for (const {at, things} of inserts) {
      const fresh = things.map(asItem);
      if (brokenFrom.has(at)) for (const next of fresh) if (!brokenFrom.has(next)) brokenFrom.set(next, brokenFrom.get(at));
      // While a prefab is open, what is made goes into it.
      if (openPrefab && (at === 'world' || at === 'end')) for (const next of fresh) if (!brokenFrom.has(next)) brokenFrom.set(next, openPrefab.from);
      const index = at === 'world' ? M.worldInsertAt({items}) : at === 'end' ? items.length : (items.indexOf(at) + 1 || items.length);
      items.splice(index, 0, ...fresh);
      added.push(...fresh);
    }
    if (openPrefab) syncPrefab(items);
    globalGroup().items = items;
    selected.clear();
    selection.position.set(0, 0, 0);
    for (const item of select === 'added' ? added : select === 'changed' ? changed : select || []) if (items.includes(item)) selected.add(item);
    rebuild(); showReady();
  }

  // ---- Selection overlay, vertex dots and the shape being drawn ----
  const ghost = new THREE.Group();
  root.add(ghost);
  const pickedOutline = new THREE.LineBasicMaterial({color: 0x3fc8ff, depthTest: false, transparent: true});
  const dotMaterial = new THREE.PointsMaterial({color: 0xf0c674, size: 9, sizeAttenuation: false, depthTest: false, transparent: true});
  const hoverMaterial = new THREE.PointsMaterial({color: 0xffffff, size: 14, sizeAttenuation: false, depthTest: false, transparent: true});
  const hoverDot = new THREE.Points(new THREE.BufferGeometry(), hoverMaterial);
  hoverDot.renderOrder = 5; hoverDot.visible = false;
  root.add(hoverDot);
  let dots = [];  // {item, index, position} of the corners of the selected brushes, in vertex mode.
  // The face the texture keys changed last, {item, index}, drawn with its texture coordinates as brush.js guesses the
  // game maps them: tiles of 64 units shaded red along u and green along v, chequered every 16, so moving, scaling,
  // flipping and turning show.
  let texturePreview = null;
  const textureMaterial = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
    vertexShader: `attribute vec2 texcoord; varying vec2 vTex;
      void main() { vTex = texcoord; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }`,
    fragmentShader: `varying vec2 vTex;
      void main() {
        vec2 tile = fract(vTex / 64.);
        float check = mod(floor(vTex.x / 16.) + floor(vTex.y / 16.), 2.);
        gl_FragColor = vec4(vec3(tile.x, tile.y, .3) * (.65 + .35 * check), .85);
      }`,
  });
  function textureMesh(brush, face) {
    const coords = B.texcoords(brush, face), positions = [], texcoords = [];
    for (const triangle of B.triangles(brush, face, triangulate)) for (const vertex of triangle) {
      positions.push(...brush.vertices[vertex]);
      texcoords.push(...coords[face.indices.indexOf(vertex)]);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('texcoord', new THREE.Float32BufferAttribute(texcoords, 2));
    const mesh = new THREE.Mesh(geometry, textureMaterial);
    mesh.renderOrder = 4;
    return mesh;
  }
  const edgesOf = (...brushes) => {
    const lines = [];
    for (const brush of brushes) for (const face of brush.faces) face.indices.forEach((index, n) => lines.push(...brush.vertices[index], ...brush.vertices[face.indices[(n + 1) % face.indices.length]]));
    const edges = new THREE.BufferGeometry();
    edges.setAttribute('position', new THREE.Float32BufferAttribute(lines, 3));
    return new THREE.LineSegments(edges, selectedEdges);
  };
  // `preview` maps a selected brush to the brush a drag would make of it.
  function showSelection(preview = new Map()) {
    selection.clear();
    dots = [];
    if (!flat) return;
    // The selected brushes, and those a selected Prefab entity places, in one mesh and one set of lines.
    const shown = [];
    for (const entry of flat.brushes) {
      if (entry.path.length ? !selected.has(entry.path[0]) : !selected.has(entry.source)) continue;
      const brush = preview.get(entry.source) || entry.brush;
      shown.push({brush});
      if (vertexMode && !entry.path.length) brush.vertices.forEach((position, index) => dots.push({item: entry.source, index, position}));
    }
    if (shown.length) selection.add(new THREE.Mesh(buildMesh(shown, () => true, []), selectedFill), edgesOf(...shown.map(entry => entry.brush)));
    // A face whose material has no texture shows its texture coordinates as a pattern; a textured one shows itself.
    if (texturePreview && globalGroup().items.includes(texturePreview.item)) {
      const face = texturePreview.item.faces[texturePreview.index];
      if (!textureSource(face)) selection.add(textureMesh(texturePreview.item, face));
    } else texturePreview = null;
    for (const entry of flat.entities) {
      if (!selected.has(entry.entity) || entry.path.length || !entry.position) continue;
      const box = new THREE.Box3Helper(new THREE.Box3().setFromCenterAndSize(new THREE.Vector3(...entry.position), new THREE.Vector3(24, 36, 24)), 0xf0c674);
      box.material.depthTest = false;
      selection.add(box);
    }
    const items = new Set(globalGroup().items);
    picked = picked.filter(face => items.has(face.item) && face.item.faces[face.index]);
    if (!picked.length) bridging = false;
    for (const {item, index} of picked) {
      const outline = new THREE.LineLoop(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(item.faces[index].indices.flatMap(i => item.vertices[i]), 3)), pickedOutline);
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
    7: {type: 'PointLight', label: 'point light'}, 8: {type: 'PlayerSpawn', label: 'player spawn'}, 9: {type: 'Prefab', label: 'prefab'},
  };
  const createLabel = () => createType === 9 ? `prefab ${placeName}` : CREATE[createType].label;
  function updateTools() {
    if (!flat) return;
    const brushes = selectedBrushes(), count = selected.size, convex = brushes.length > 0 && brushes.every(B.convex);
    $('subtract').disabled = !convex;
    $('hollow').disabled = brushes.length !== 1 || !convex;
    $('merge').disabled = brushes.length < 2 || !convex;
    $('split').disabled = !convex || !lastPoint;
    $('clipButton').classList.toggle('on', clipMode);
    $('rotateInc').disabled = $('rotateDec').disabled = $('mirror').disabled = !count;
    $('duplicate').disabled = $('delete').disabled = !count;
    $('undo').disabled = !undoStack.length; $('redo').disabled = !redoStack.length;
    $('mode').title = $('mode').textContent = (bridging ? `Bridge (B): aim at a face · wheel ${segments} step${segments > 1 ? 's' : ''} · click makes · Esc stops`
      : clipMode ? `Clip (C): ${clipPoints.length} of 3 points · Enter clips · Shift+Enter splits · wheel flips · Esc`
      : createType ? `Create ${createLabel()}${createType <= 8 ? ` (${createType})` : ''}: ${createType <= 3 ? 'drag on a surface' : 'click to place'} · Esc stops`
      : vertexMode ? 'Vertex mode (V): drag a corner · Esc stops' : 'Edit mode') + (openPrefab ? ` · prefab ${openPrefab.name} open (Esc with nothing selected closes)` : '');
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
      const name = M.property(item, 'name') || M.property(item, 'target') || M.property(item, 'effectName') || M.property(item, 'prefabName') || (item.type === 'Pickup' ? `type ${M.property(item, 'pickupType')}` : '');
      const placed = item.type === 'Prefab' ? ` · ${flat.brushes.filter(e => e.path[0] === item).length} brushes; me_breakprefab breaks it into the map` : '';
      return `${item.type}${name !== undefined && name !== '' ? ` ${name}` : ''}${placed}`;
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
        const face = {item: hit.entry.source, index: hit.entry.source.faces.indexOf(hit.face)};
        const at = picked.findIndex(other => other.item === face.item && other.index === face.index);
        if (adding(event)) { if (at >= 0) picked.splice(at, 1); else picked.push(face); } else picked = [face];
        showSelection();
        say(picked.length === 1 ? `Face picked (${hit.face.indices.length} corners) · the texture keys and Shift+M work on it · B bridges it to another face`
          : picked.length ? `${picked.length} faces picked · the texture keys and Shift+M work on them · B bridges from the last` : '');
      }
      return;
    }
    const hit = pick(event);
    if (!hit) { if (!adding(event)) selected.clear(); say(''); showSelection(); return; }
    lastPoint = hit.point; lastNormal = hit.normal;
    const item = itemOf(hit);
    if (adding(event)) { if (selected.has(item)) selected.delete(item); else selected.add(item); } else { selected.clear(); selected.add(item); }
    showSelection();
    say(`${selected.size} selected · ${describe(item, hit.entity || hit.entry)}`);
  }
  // What a click on a hit selects: the brush or entity, or for one a prefab places, the Prefab entity of the map that
  // places it (as the game selects a prefab as one thing).
  const itemOf = hit => { const entry = hit.entity || hit.entry; return entry.path.length ? entry.path[0] : hit.entity ? hit.entity.entity : hit.entry.source; };
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

  // The index of the point of `points` nearest the mouse on screen, within 10 pixels; -1 when none is.
  function nearestOf(points, event) {
    const rect = canvas.getBoundingClientRect(), v = new THREE.Vector3();
    let best = -1, nearest = 10;
    camera.updateMatrixWorld();
    points.forEach((point, index) => {
      v.set(point[0], point[1], -point[2]).project(camera);
      if (v.z > 1) return;
      const off = Math.hypot(rect.left + (v.x + 1) / 2 * rect.width - event.clientX, rect.top + (1 - v.y) / 2 * rect.height - event.clientY);
      if (off < nearest) { nearest = off; best = index; }
    });
    return best;
  }
  // The corner dot nearest the mouse, within 10 pixels.
  const nearestDot = event => dots[nearestOf(dots.map(dot => dot.position), event)] || null;
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
      Prefab: () => prefabEntity(placeName, position, [(yaw + 360) % 360, 0, 0]).properties,
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
  // Where the mouse is on a surface (or, with nothing under it, on the ground plane y = 0): the point, its normal, the
  // axis the surface faces most and the point on the grid across that axis.
  function surfaceAt(event) {
    const hit = pick(event, false);
    let point, normal;
    if (hit) ({point, normal} = hit);
    else {
      point = onPlane(ray(event), 1, 0);
      if (!point) return null;
      normal = [0, 1, 0];
    }
    const axis = normal.map(Math.abs).indexOf(Math.max(...normal.map(Math.abs))), sign = Math.sign(normal[axis]) || 1;
    const level = Math.abs(point[axis] - snap(point[axis])) < .01 ? snap(point[axis]) : point[axis];
    return {point, normal, axis, sign, level, snapped: point.map((p, i) => i === axis ? level : snap(p))};
  }
  function startCreate(event) {
    const at = surfaceAt(event);
    if (!at) return;
    if (createType >= 4) { drag = {kind: 'place', point: at.point, normal: at.normal}; return; }
    drag = {kind: 'create', axis: at.axis, sign: at.sign, level: at.level, start: at.snapped, end: at.snapped};
  }

  let drag = null;
  function startDrag(event) {
    if (bridging) { makeBridge(); drag = {kind: 'done'}; return; }  // The click is the bridge's, not a selection.
    if (clipMode) {
      // A click near a point takes it to drag; elsewhere it adds one (a fourth starts the plane again), which the drag
      // then moves.
      let index = nearestOf(clipPoints, event);
      if (index < 0) {
        const at = surfaceAt(event);
        if (!at) { drag = {kind: 'done'}; return; }
        if (clipPoints.length >= 3) clipPoints = [];
        clipPoints.push(at.snapped);
        index = clipPoints.length - 1;
        showClip();
      }
      drag = {kind: 'clippoint', index};
      return;
    }
    if (vertexMode) {
      const corner = nearestDot(event);
      if (corner) { drag = {kind: 'corner', ...corner, offset: [0, 0, 0], result: null}; return; }
    }
    if (createType) { startCreate(event); return; }
    const hit = pick(event);
    if (!hit) return;
    const item = itemOf(hit), entry = hit.entity || hit.entry;
    if (event.shiftKey) {
      if (entry.path.length) return;
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
    if (drag.kind === 'place' || drag.kind === 'done') return;
    if (drag.kind === 'clippoint') {
      const at = surfaceAt(event);
      if (at) { clipPoints[drag.index] = at.snapped; showClip(); }
      return;
    }
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
    if (done.kind === 'done' || done.kind === 'clippoint') return true;
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
      say(`New ${describe(entity).replace(/ · .*/, '')} at ${positionOf(entity).join(' ')}`);
      return true;
    }
    showSelection();
    return false;
  }

  // ---- The bridge tool ----
  function startBridge() {
    if (!picked.length) { say('Shift-click a face first; B then bridges it to the face you aim at'); return; }
    bridging = true; createType = 0; vertexMode = false; clipMode = false; clipPoints = []; ghost.clear();
    showHover(null);
    updateTools();
    say('Aim at the face to bridge to; the wheel sets the steps');
    if (mouseAt) aimBridge(mouseAt);
  }
  function stopBridge() { bridging = false; bridgePreview = null; ghost.clear(); updateTools(); say(''); }
  function aimBridge(event) {
    ghost.clear();
    bridgePreview = null;
    const hit = pick(event, false), bridgeFrom = bridgeFace();
    if (!bridgeFrom) { stopBridge(); return; }
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
    bridging = false; bridgePreview = null; picked = []; ghost.clear();
    replace(new Map(), [{at: 'world', things: brushes}], 'added');
    say(`Bridged with ${brushes.length} brush${brushes.length > 1 ? 'es' : ''}`);
  }

  // ---- The property panel (N, the game's me_showproperties) ----
  // What the stock maps give each type, for adding: [type, name, default]. Names an entity of the map has are added too.
  const SCHEMA = {
    WorldSpawn: [['String256', 'title', ''], ['String256', 'ownerString', ''], ['String32', 'targetGameOverCamera', ''], ['UInt8', 'playersMin', 1], ['UInt8', 'playersMax', 16],
      ['Bool8', 'modeFFA', 1], ['Bool8', 'mode1v1', 1], ['Bool8', 'mode2v2', 1], ['Bool8', 'modeTDM', 1], ['Bool8', 'modeCTF', 0], ['Bool8', 'modeRace', 0],
      ['ColourXRGB32', 'fogColor', 'ffb4e1ff'], ['Float', 'fogDistanceStart', 512], ['Float', 'fogDistanceEnd', 8192], ['ColourXRGB32', 'sky.horizonColor', 'ff000000'],
      ['ColourXRGB32', 'sky.skyTopColor', 'ff000000'], ['ColourXRGB32', 'sky.sunColor', 'ffffffff'], ['Float', 'sky.timeOfDay', 12]],
    Teleporter: [['String32', 'target', ''], ['String32', 'linkOutOnUsed', '']],
    JumpPad: [['String32', 'target', '']],
    Target: [['Vector3', 'position', [0, 0, 0]], ['Vector3', 'angles', [0, 0, 0]], ['String32', 'name', ''], ['String32', 'nameNext', ''], ['Float', 'speed', 160]],
    Pickup: [['Vector3', 'position', [0, 0, 0]], ['UInt8', 'pickupType', 40], ['Vector3', 'angles', [0, 0, 0]], ['UInt8', 'tokenIndex', 1], ['String32', 'linkOutOnPickedUp', '']],
    PlayerSpawn: [['Vector3', 'position', [0, 0, 0]], ['Vector3', 'angles', [0, 0, 0]], ['Bool8', 'teamA', 0], ['Bool8', 'teamB', 0], ['Bool8', 'initialSpawn', 0],
      ['Bool8', 'modeFFA', 0], ['Bool8', 'mode1v1', 0], ['Bool8', 'modeTDM', 0], ['Bool8', 'modeCTF', 0], ['Bool8', 'modeRace', 0]],
    PointLight: [['Vector3', 'position', [0, 0, 0]], ['ColourXRGB32', 'color', 'ffffc400'], ['Float', 'nearAttenuation', 32], ['Float', 'farAttenuation', 160], ['Float', 'intensity', 1.5]],
    Prefab: [['Vector3', 'position', [0, 0, 0]], ['String64', 'prefabName', ''], ['Vector3', 'angles', [0, 0, 0]]],
    Effect: [['Vector3', 'position', [0, 0, 0]], ['String64', 'effectName', ''], ['Float', 'effectScale', 1], ['Vector3', 'angles', [0, 0, 0]],
      ['String256', 'material0Name', ''], ['ColourARGB32', 'material0Albedo', 'ffffffff']],
  };
  // pickupType: the numbers reflex-map's converter names; 20, 70, 71 and 80 are guesses from where the stock maps use them.
  const PICKUPS = {1: 'Shotgun', 2: 'Grenade launcher', 3: 'Plasma rifle', 4: 'Rocket launcher', 5: 'Ion cannon', 6: 'Bolt rifle', 7: 'Stake gun',
    20: 'Burst gun ammo (guess)', 21: 'Shotgun ammo', 22: 'Grenades', 23: 'Plasma ammo', 24: 'Rockets', 25: 'Ion ammo', 26: 'Bolts',
    40: 'Health 5', 41: 'Health 25', 42: 'Health 50', 43: 'Mega health', 50: 'Armour shard', 51: 'Light armour', 52: 'Medium armour', 53: 'Heavy armour',
    60: 'Carnage (quad damage)', 70: 'Flag, team A (guess)', 71: 'Flag, team B (guess)', 80: 'Race token (guess)'};
  const NAMES_OF = {target: 'Target', nameNext: 'Target', targetGameOverCamera: 'Target'};
  let panelOpen = false, panelView = 'properties', prefabDraft = '';
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
    if (name === 'prefabName') return M.prefabUses(map).map(entry => entry.name);
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
    $('propsTitle').textContent = panelView === 'prefabs' ? 'Prefabs' : panelView === 'materials' ? 'Materials' : 'Properties';
    if (panelView === 'materials') { showMaterials(body); return; }
    shownMaterials = '';
    body.replaceChildren();
    if (panelView === 'prefabs') { showPrefabList(body); return; }
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
  function togglePanel() {
    panelOpen = !(panelOpen && panelView === 'properties');
    panelView = 'properties';
    showPanel();
    say(panelOpen ? 'Properties: N closes' : '');
  }
  // The prefab view of the panel: making, breaking and updating, and the map's prefabs with how many placements each
  // has, to place or select.
  function showPrefabList(body) {
    const button = (textContent, onclick, title = '') => element('button', {type: 'button', textContent, onclick, title});
    body.append(element('p', {textContent: 'Break a placement to edit what it holds; Update puts the selection back into every placement.'}));
    const name = element('input', {type: 'text', value: prefabDraft, placeholder: 'name', maxLength: 64, oninput: event => { prefabDraft = event.target.value; }});
    body.append(element('div', {className: 'make'}, name, button('Make from selection', () => createPrefab(prefabDraft.trim()), 'me_createprefab <name>')),
      element('div', {className: 'actions'},
        button('Break selected', breakPrefabs, 'me_breakprefab: the selected placements become what their prefab holds'),
        openPrefab ? button(`Close ${openPrefab.name}`, closePrefab, 'me_closeprefab: put the open prefab back as its placement') : button('Edit selected in place', editPrefabInPlace, 'me_editprefab (or double click a placement): its other placements change as you edit'),
        button('Update from selection', () => updatePrefab(prefabDraft.trim() || undefined), 'me_updateprefab [name]: the selection becomes what the prefab holds, in every placement')));
    const uses = M.prefabUses(map);
    if (!uses.length) { body.append(element('p', {textContent: 'This map has no prefabs.'})); return; }
    body.append(element('table', {className: 'prefabs'}, ...uses.map(({name: prefab, uses: count}) => element('tr', {},
      element('td', {textContent: prefab}), element('td', {textContent: String(count), title: 'Placements'}),
      element('td', {}, button('Place', () => placePrefab(prefab), 'Click a surface to place it (me_createtype prefab)'), ' ',
        button('Select', () => {
          const wanted = prefab.toLowerCase();
          selected.clear();
          for (const item of globalGroup().items) if (!isBrush(item) && item.type === 'Prefab' && String(M.property(item, 'prefabName')).toLowerCase() === wanted) selected.add(item);
          showSelection();
          say(`${selected.size} placement${selected.size === 1 ? '' : 's'} of ${prefab} in the map selected`);
        }, 'Select its placements in the map'))))));
  }

  // ---- The material browser (the game's me_activematerial) ----
  // The game's materials, with the thumbnails the import read, and the textures of Skinner's libraries, which a face
  // takes as the material skinner/<library>/<file>. A click makes one the material M puts on; a double click also
  // puts it on the selection.
  const LIBRARIES = {reflex: 'Reflex materials', t1: 'Tribes 1 textures', t2: 'Tribes 2 textures', q3: 'Quake 3 textures', 'reflex-textures': 'Reflex textures'};
  let library = 'reflex', materialSearch = '', shownMaterials = '';
  const libraryLists = new Map();  // Library → [{name, image, label}], once read.
  function libraryEntries(which) {
    if (which === 'reflex') {
      return Object.entries(packColours).filter(([name, read]) => !/^internal\//.test(name) || flat.brushes.some(entry => entry.brush.faces.some(face => face.material === name)))
        .map(([name, read]) => ({name, image: read.thumb ? `${data}thumbs/${encodeURIComponent(read.thumb)}` : null, colour: read.colour, label: name}))
        .sort((a, b) => a.name.localeCompare(b.name));
    }
    if (libraryLists.has(which)) return libraryLists.get(which);
    libraryLists.set(which, null);
    const game = which === 'reflex-textures' ? 'reflex' : which;
    fetch(`/list_textures?game=${game}`).then(response => response.ok ? response.json() : []).catch(() => []).then(files => {
      libraryLists.set(which, (Array.isArray(files) ? files : []).map(file => ({name: `skinner/${game}/${file.replace(/\.png$/i, '')}`, image: `/texture/${encodeURIComponent(file)}?game=${game}`, label: file})));
      shownMaterials = '';
      showPanel();
    });
    return null;
  }
  function chooseMaterial(name, apply) {
    template = {...template, material: name, colour: '0x00000000'};
    shownMaterials = '';
    updateTools();
    if (apply) applyMaterial(false);
    else say(`Material ${name}: M puts it on the selection, Shift+M on the face under the cursor`);
  }
  function showMaterials(body) {
    const entries = libraryEntries(library), key = [library, materialSearch, template.material, entries ? entries.length : -1].join('|');
    if (key === shownMaterials) return;  // Redrawn only when what it shows changes, not on every selection or drag.
    shownMaterials = key;
    body.replaceChildren();
    const choose = element('select', {onchange: event => { library = event.target.value; showPanel(); }},
      ...Object.entries(LIBRARIES).map(([value, label]) => element('option', {value, textContent: label, selected: value === library})));
    const search = element('input', {type: 'search', placeholder: 'Search', value: materialSearch, oninput: event => { materialSearch = event.target.value; const at = event.target.selectionStart; showPanel(); const again = $('propsBody').querySelector('input[type=search]'); again.focus(); again.setSelectionRange(at, at); }});
    body.append(element('div', {className: 'make'}, choose, search));
    if (!entries) { body.append(element('p', {textContent: 'Reading the library…'})); return; }
    const words = materialSearch.toLowerCase().split(/\s+/).filter(Boolean);
    const found = entries.filter(entry => words.every(word => entry.label.toLowerCase().includes(word)));
    const grid = element('div', {className: 'materials'});
    for (const entry of found.slice(0, 240)) {
      const tile = element('button', {type: 'button', title: entry.name, className: entry.name === template.material ? 'chosen' : '',
        onclick: () => chooseMaterial(entry.name, false), ondblclick: () => chooseMaterial(entry.name, true)});
      if (entry.image) tile.append(element('img', {src: entry.image, alt: '', loading: 'lazy'}));
      else tile.append(element('span', {className: 'colour', style: `background:#${new THREE.Color(...(entry.colour || [.5, .5, .5]).map(c => Math.pow(c, 1 / 2.2))).getHexString()}`}));
      tile.append(element('span', {textContent: entry.label.split('/').pop().replace(/\.png$/i, '')}));
      grid.append(tile);
    }
    body.append(grid, element('p', {className: 'hint', textContent: found.length > 240 ? `${found.length} found; the first 240 shown: search to narrow` : found.length ? 'Click: M puts it on. Double click: on the selection now.' : 'Nothing found.'}));
  }
  function showMaterialBrowser() {
    if (panelOpen && panelView === 'materials') { panelOpen = false; showPanel(); return; }
    panelOpen = true; panelView = 'materials'; shownMaterials = ''; showPanel();
  }

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
      const targets = faceTargets();
      if (!targets.length || targets[0].placed) return;
      editFaces(targets, paint);
      say(`Material on ${targets.length === 1 ? 'one face' : `${targets.length} faces`}: ${template.material || 'no material'}`);
      return;
    }
    const items = selectedBrushes();
    if (!items.length) return;
    replace(new Map(items.map(item => [item, [{vertices: item.vertices, faces: item.faces.map(paint)}]])), [], 'changed');
    say(`Material on ${items.length} brushes: ${template.material || 'no material'}`);
  }

  // ---- Texture keys (the game's me_texcoords_*) ----
  // On the face Shift-click picked, else the face under the cursor: the arrows move its texture a grid step (left and
  // right along u, up and down along v), Home and Insert scale it up and down along u, End and Delete along v, PgUp and
  // PgDn flip it along u and v, and , and . turn it by the angle step.
  const SCALE_STEP = .25;  // The stock maps' scales other than 1 are mostly quarters (0.25, 0.75, 1.25).
  const TEXTURE_KEYS = {ArrowLeft: ['u', -1], ArrowRight: ['u', 1], ArrowUp: ['v', 1], ArrowDown: ['v', -1], Home: ['scaleU', 1], Insert: ['scaleU', -1],
    End: ['scaleV', 1], Delete: ['scaleV', -1], PageUp: ['scaleU', 0], PageDown: ['scaleV', 0], Comma: ['rotation', -1], Period: ['rotation', 1]};
  // The faces the texture keys and Shift+M work on: those picked, else the one under the cursor ({placed} when that
  // is a prefab's).
  function faceTargets() {
    if (picked.length) return picked;
    const hit = mouseAt && pick(mouseAt, false);
    if (!hit) return [];
    return hit.entry.path.length ? [{placed: hit.entry.path[0]}] : [{item: hit.entry.source, index: hit.entry.source.faces.indexOf(hit.face)}];
  }
  // Changes faces as one step of undo, keeping the selection and the picked faces.
  function editFaces(targets, change) {
    targets = targets.map(target => ({...target}));
    const byItem = new Map();
    for (const {item, index} of targets) byItem.set(item, [...(byItem.get(item) || []), index]);
    const fresh = new Map([...byItem].map(([item, indices]) => [item, {kind: 'brush', vertices: item.vertices, faces: item.faces.map((face, index) => indices.includes(index) ? change(face) : face)}]));
    replace(new Map([...fresh].map(([item, made]) => [item, [made]])), [], [...selected].map(item => fresh.get(item) || item));
    return fresh;
  }
  function moveTexture(code) {
    const targets = faceTargets();
    if (!targets.length) { say('Aim at a face, or Shift-click one, to move its texture'); return; }
    if (targets[0].placed) { say(`That face is prefab ${M.property(targets[0].placed, 'prefabName')}'s: break the prefab (me_breakprefab) to change it`); return; }
    const [field, sign] = TEXTURE_KEYS[code];
    const change = face => {
      const value = Number(face[field]) || 0;
      let next;
      if (field === 'u' || field === 'v') next = value + sign * settings.grid * 16;  // Offsets are 1/16 unit.
      else if (field === 'rotation') next = ((value + sign * settings.angle) % 360 + 360) % 360;
      else if (!sign) next = -(value || 1);
      else next = (value < 0 ? -1 : 1) * Math.max(SCALE_STEP, (Math.abs(value) || 1) + sign * SCALE_STEP);
      return {...face, [field]: Math.round(next * 1e6) / 1e6 + 0};
    };
    // The picked faces follow their brushes into the new ones, so the first is noted before the edit.
    const {item: before, index} = targets[0], fresh = editFaces(targets, change), item = fresh.get(before);
    texturePreview = {item, index};
    showSelection();
    const f = item.faces[index];
    say(`Texture${targets.length > 1 ? ` of ${targets.length} faces` : ''}: offset ${f.u} ${f.v} · scale ${f.scaleU} ${f.scaleV} · rotation ${f.rotation}°`);
  }

  // ---- Turning (numpad + and −, the game's me_rotate_inc and me_rotate_dec, by me_snapangle) ----
  // The items of the map selected, with the brushes of a selected volume entity and the entity of a selected volume.
  function withVolumes(items) {
    const all = new Set(items);
    for (const entry of flat.brushes) if (!entry.path.length && M.isVolume(entry.owner) && all.has(entry.owner)) all.add(entry.source);
    for (const item of [...all]) if (isBrush(item) && M.isVolume(ownerOf(item))) all.add(ownerOf(item));
    return globalGroup().items.filter(item => all.has(item));
  }
  // The box round items: brushes' corners and entities' positions, and with `placed` the brushes a Prefab places.
  function extent(items, placed = false) {
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    const add = point => { for (let i = 0; i < 3; i++) { min[i] = Math.min(min[i], point[i]); max[i] = Math.max(max[i], point[i]); } };
    for (const item of items) {
      if (isBrush(item)) { item.vertices.forEach(add); continue; }
      const position = positionOf(item);
      if (position) add(position);
      if (placed && item.type === 'Prefab') for (const entry of flat.brushes) if (entry.path[0] === item) entry.brush.vertices.forEach(add);
    }
    return min[0] <= max[0] ? {min, max} : null;
  }
  const translation = offset => [[1, 0, 0, offset[0]], [0, 1, 0, offset[1]], [0, 0, 1, offset[2]]];
  const onGrid = value => Math.abs(value - snap(value)) < 1e-6;
  // Turns the selection about the vertical through its middle, +z toward +x for `sign` 1 as a yaw turns; entities'
  // yaw turns with it. A quarter turn of a selection whose corner was on the grid leaves it on the grid: when the turn
  // about the middle does not, the selection moves the rest of the way, less than a grid step.
  function rotateSelection(sign) {
    const items = withVolumes([...selected]), box = items.length && extent(items, true);
    if (!box) { say('Select something to turn'); return; }
    const degrees = sign * settings.angle, centre = box.min.map((v, i) => (v + box.max[i]) / 2), turn = M.compose([0, 0, 0], [degrees, 0, 0]);
    let transform = turn.map((row, i) => [row[0], row[1], row[2], centre[i] - (row[0] * centre[0] + row[1] * centre[1] + row[2] * centre[2])]);
    if (degrees % 90 === 0) {
      const before = extent(items), after = extent(items.map(item => M.moveItem(item, transform, degrees)));
      const shift = [0, 0, 0];
      for (const axis of [0, 2]) if (onGrid(before.min[axis]) && !onGrid(after.min[axis])) shift[axis] = snap(after.min[axis]) - after.min[axis];
      transform = transform.map((row, i) => [row[0], row[1], row[2], row[3] + shift[i]]);
    }
    const turned = items.map(item => M.moveItem(item, transform, degrees));
    replace(new Map(items.map((item, i) => [item, [turned[i]]])), [], turned.filter((_, i) => selected.has(items[i])));
    say(`Turned ${degrees > 0 ? '+' : ''}${degrees}° about ${Math.round(centre[0] * 1000) / 1000} ${Math.round(centre[2] * 1000) / 1000}`);
  }

  // ---- Mirroring (the toolbar's Mirror, or me_mirror x|y|z; the game's editor had none) ----
  // Mirrors the selection across the plane through its middle square to `axis`; without one, left to right as the
  // camera sees it (the horizontal axis nearest the view's right).
  function mirrorSelection(axis) {
    const items = withVolumes([...selected]), box = items.length && extent(items, true);
    if (!box) { say('Select something to mirror'); return; }
    if (axis === undefined) {
      const side = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
      axis = Math.abs(side.x) >= Math.abs(side.z) ? 0 : 2;
    }
    const centre = (box.min[axis] + box.max[axis]) / 2, mirrored = items.map(item => M.mirrorItem(item, axis, centre));
    replace(new Map(items.map((item, i) => [item, [mirrored[i]]])), [], mirrored.filter((_, i) => selected.has(items[i])));
    const placements = items.filter(item => !isBrush(item) && item.type === 'Prefab').length;
    say(`Mirrored across ${'xyz'[axis]} = ${Math.round(centre * 1000) / 1000}${placements ? ` · ${placements} prefab placement${placements > 1 ? 's' : ''} moved and turned, not mirrored inside` : ''}`);
  }

  // ---- Prefabs (the game's me_createprefab, me_breakprefab, me_updateprefab, me_listprefabs) ----
  const PREFAB_NAME = /^[A-Za-z0-9_.-]+$/;
  // Selected items as a prefab holds them: the world's brushes, then the entities, a volume entity followed by its
  // brushes. A WorldSpawn is never taken in; the prefab has its own.
  function prefabParts(items, owner = ownerOf) {
    const world = items.filter(item => isBrush(item) && !M.isVolume(owner(item))), rest = [];
    for (const item of items) {
      if (isBrush(item) || item.type === 'WorldSpawn') continue;
      rest.push(item);
      if (M.isVolume(item)) rest.push(...items.filter(other => isBrush(other) && owner(other) === item));
    }
    return {world, rest};
  }
  const moveParts = ({world, rest}, transform, yaw) => ({world: world.map(item => M.moveItem(item, transform, yaw)), rest: rest.map(item => M.moveItem(item, transform, yaw))});
  const prefabEntity = (name, position, angles = [0, 0, 0]) => ({kind: 'entity', type: 'Prefab', properties: [
    {type: 'Vector3', name: 'position', value: position}, {type: 'String64', name: 'prefabName', value: name}, {type: 'Vector3', name: 'angles', value: angles}]});
  // Where a new prefab's origin goes: the middle of the selection across, its bottom, on the grid.
  function originOf(items) {
    const {min, max} = extent(items, true);
    return [snap((min[0] + max[0]) / 2), Math.floor(min[1] / settings.grid + 1e-9) * settings.grid + 0, snap((min[2] + max[2]) / 2)];
  }
  // Whether Prefab entities among `items` place the prefab `name`, at any depth.
  function holds(name, items, seen = new Set()) {
    for (const item of items) {
      if (isBrush(item) || item.type !== 'Prefab') continue;
      const inner = M.prefab(map, M.property(item, 'prefabName'));
      if (!inner || seen.has(inner)) continue;
      if (inner.name.toLowerCase() === name.toLowerCase()) return true;
      seen.add(inner);
      if (holds(name, inner.items, seen)) return true;
    }
    return false;
  }
  const counted = ({world, rest}) => `${world.length + rest.filter(isBrush).length} brushes and ${rest.filter(item => !isBrush(item)).length} entities`;
  // me_createprefab: the selection becomes the prefab `name`, placed where it stood by a new Prefab entity.
  function createPrefab(name) {
    if (!name || !PREFAB_NAME.test(name)) { say('me_createprefab <name>: a name of letters, digits, _, . and -'); return; }
    const existing = M.prefab(map, name);
    if (existing) { say(`There is a prefab ${existing.name} already: me_updateprefab ${existing.name} changes it`); return; }
    const items = withVolumes([...selected]).filter(item => isBrush(item) || item.type !== 'WorldSpawn');
    if (!items.length) { say('Select what the prefab is to hold, then me_createprefab <name>'); return; }
    const origin = originOf(items), placed = prefabEntity(name, origin);
    const local = moveParts(prefabParts(items), translation(origin.map(v => -v)), 0);
    replace(new Map(items.map(item => [item, []])), [{at: 'end', things: [placed]}], [placed], () => M.setPrefab(map, name, local));
    say(`Prefab ${name}: ${counted(local)}, placed at ${origin.join(' ')}`);
  }
  // me_breakprefab: each selected placement becomes copies of what its prefab holds, where it placed them; the prefab
  // stays in the map for its other placements.
  function breakPrefabs() {
    const placements = [...selected].filter(item => !isBrush(item) && item.type === 'Prefab');
    if (!placements.length) { say('Select a placed prefab (click any part of it), then me_breakprefab'); return; }
    const changes = new Map(), world = [], made = [], missing = [];
    for (const entity of placements) {
      const parts = M.breakPrefab(map, entity);
      if (!parts) { missing.push(M.property(entity, 'prefabName')); continue; }
      const from = {name: M.placement(map, entity).group.name, position: positionOf(entity) || [0, 0, 0], angles: M.property(entity, 'angles') || [0, 0, 0]};
      for (const item of [...parts.world, ...parts.rest]) brokenFrom.set(item, from);
      changes.set(entity, parts.rest);
      world.push(...parts.world);
      made.push(...parts.world, ...parts.rest);
    }
    if (!changes.size) { say(`This map has no prefab named ${missing.join(', ')}`); return; }
    replace(changes, world.length ? [{at: 'world', things: world}] : [], made);
    say(`Broke ${changes.size} placement${changes.size > 1 ? 's' : ''} into ${counted({world, rest: made.slice(world.length)})}; me_updateprefab puts the selection back into the prefab`);
  }
  // me_updateprefab: the selection becomes what the prefab holds, in every placement of it, and is replaced by a
  // placement where it stands. Without a name, the prefab the selection was broken from; it then goes back into that
  // placement's frame (its position and turn), so the other placements keep theirs. A prefab named but not broken
  // takes the selection about a new origin, as me_createprefab would.
  function updatePrefab(name) {
    const items = withVolumes([...selected]).filter(item => isBrush(item) || item.type !== 'WorldSpawn');
    if (!items.length) { say('Select what the prefab is to hold, then me_updateprefab'); return; }
    const sources = [...new Set(items.map(item => brokenFrom.get(item)).filter(Boolean).map(from => from.name))];
    if (!name) {
      if (sources.length !== 1) { say(sources.length ? `The selection comes from ${sources.join(' and ')}: me_updateprefab <name>` : 'me_updateprefab <name>: which prefab the selection is to become'); return; }
      name = sources[0];
    }
    const group = M.prefab(map, name);
    if (!group) { say(`This map has no prefab named ${name}: me_createprefab ${name} makes one`); return; }
    const parts = prefabParts(items);
    if (holds(group.name, parts.rest)) { say(`A prefab cannot hold itself: the selection places ${group.name}`); return; }
    const frame = items.map(item => brokenFrom.get(item)).find(from => from && from.name.toLowerCase() === group.name.toLowerCase()) || {position: originOf(items), angles: [0, 0, 0]};
    const local = moveParts(parts, M.invert(M.compose(frame.position, frame.angles)), -frame.angles[0]);
    const placed = prefabEntity(group.name, frame.position, frame.angles);
    replace(new Map(items.map(item => [item, []])), [{at: 'end', things: [placed]}], [placed], () => M.setPrefab(map, group.name, local));
    const uses = M.prefabUses(map).find(entry => entry.group === M.prefab(map, group.name));
    say(`Prefab ${group.name} now holds ${counted(local)}, in its ${uses ? uses.uses : 1} placement${uses && uses.uses === 1 ? '' : 's'}`);
  }
  // ---- Editing a prefab in place (double click a placement, or me_editprefab; me_closeprefab or Escape) ----
  // The placement is broken into the map as me_breakprefab breaks it, and while it is open every edit is also written
  // into the prefab, so its other placements show it at once; what is made meanwhile goes into the prefab too.
  // Closing puts the pieces back as the placement (me_updateprefab).
  let openPrefab = null;  // {name, from: the record its pieces share in brokenFrom}
  function editPrefabInPlace() {
    const placements = [...selected].filter(item => !isBrush(item) && item.type === 'Prefab');
    if (placements.length !== 1) { say('Select one placed prefab (click any part of it) to edit it in place'); return; }
    if (openPrefab) closePrefab();
    const entity = placements[0], parts = M.breakPrefab(map, entity);
    if (!parts) { say(`This map has no prefab named ${M.property(entity, 'prefabName')}`); return; }
    const from = {name: M.placement(map, entity).group.name, position: positionOf(entity) || [0, 0, 0], angles: M.property(entity, 'angles') || [0, 0, 0]};
    for (const item of [...parts.world, ...parts.rest]) brokenFrom.set(item, from);
    replace(new Map([[entity, parts.rest]]), parts.world.length ? [{at: 'world', things: parts.world}] : [], []);
    openPrefab = {name: from.name, from};
    updateTools();
    const others = (M.prefabUses(map).find(entry => entry.name === from.name) || {uses: 1}).uses;
    say(`Editing prefab ${from.name} in place${others ? ` · its ${others} other placement${others > 1 ? 's' : ''} change with it` : ''} · Escape when nothing is selected, or me_closeprefab, puts it back`);
  }
  // Writes the open prefab's pieces among `items` into the prefab, in its placement's frame.
  function syncPrefab(items) {
    const {from} = openPrefab, inside = items.filter(item => brokenFrom.get(item) === from);
    if (!inside.length) return;  // Every piece deleted: the prefab is left as it was, and closing does nothing.
    const owners = new Map();
    let owner = null;
    for (const item of items) { if (isBrush(item)) owners.set(item, owner); else owner = item; }
    const parts = prefabParts(inside, item => owners.get(item) || null);
    if (holds(from.name, parts.rest)) return;
    M.setPrefab(map, from.name, moveParts(parts, M.invert(M.compose(from.position, from.angles)), -from.angles[0]));
  }
  function closePrefab() {
    if (!openPrefab) { say('No prefab is open: double click a placement, or me_editprefab, to open one'); return; }
    const {from} = openPrefab, inside = globalGroup().items.filter(item => brokenFrom.get(item) === from);
    openPrefab = null;
    if (!inside.length) { updateTools(); say(`Closed prefab ${from.name}; its pieces were all deleted, so it is as it was`); return; }
    selected.clear();
    inside.forEach(item => selected.add(item));
    updatePrefab(from.name);
  }

  // me_listprefabs: the panel's list of the map's prefabs.
  function showPrefabs() { panelOpen = true; panelView = 'prefabs'; showPanel(); say(''); }
  let placeName = '';  // The prefab create type 9 places (Place in the list).
  function placePrefab(name) { placeName = name; createType = 0; setCreate(9); }

  // ---- The clipper (C, the game's editortoggleclipmode) ----
  // Two or three points clicked on surfaces (on the grid across them) make a plane; Enter keeps the part of each
  // selected brush on the far side of it from the camera, Shift+Enter keeps both parts, and the wheel or Ctrl+Enter
  // flips which side is kept. With two points the plane stands upright through them (or, through an upright line,
  // holds the view direction). The cut faces take the picked material, as new brushes do; a volume's, its own.
  let clipMode = false, clipPoints = [], clipFlip = false, clipPlane = null;
  const clipKept = new THREE.LineBasicMaterial({color: 0x7ee787, depthTest: false, transparent: true});
  const clipCut = new THREE.LineBasicMaterial({color: 0xff6b6b, depthTest: false, transparent: true, opacity: .6});
  const clipDots = new THREE.PointsMaterial({color: 0xff6b6b, size: 11, sizeAttenuation: false, depthTest: false, transparent: true});
  const clipBrushes = () => selectedBrushes().filter(B.convex);
  const clipFields = item => M.isVolume(ownerOf(item)) ? fieldsOf(item.faces[0]) : template;
  function setClipMode(on) {
    clipMode = on; clipPoints = []; clipPlane = null;
    if (on) { createType = 0; vertexMode = false; bridging = false; bridgePreview = null; }
    showHover(null);
    showClip();
    showSelection();
    say(on ? (clipBrushes().length ? 'Click two or three points of the cutting plane on surfaces' : 'Select the brushes to clip, then click two or three points') : '');
  }
  function clipPlaneOf() {
    if (clipPoints.length < 2) return null;
    let [a, b, c] = clipPoints;
    if (!c) {
      const d = b.map((v, i) => v - a[i]), length = Math.hypot(...d);
      if (length < 1e-6) return null;
      if (Math.abs(d[1]) < .9 * length) c = [b[0], b[1] + 64, b[2]];
      else { const f = new THREE.Vector3(); camera.getWorldDirection(f); c = [b[0] + f.x * 64, b[1], b[2] - f.z * 64]; }
    }
    const plane = B.planeFromPoints(a, b, c);
    if (!(Math.hypot(...plane.normal) > .5)) return null;
    const eye = [camera.position.x, camera.position.y, -camera.position.z], facing = dot(plane.normal, eye) > plane.distance;
    return facing !== clipFlip ? {normal: plane.normal, distance: plane.distance} : {normal: plane.normal.map(n => -n + 0), distance: -plane.distance + 0};
  }
  function showClip() {
    ghost.clear();
    clipPlane = clipMode ? clipPlaneOf() : null;
    if (!clipMode) return;
    if (clipPoints.length) {
      const points = new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(clipPoints.flat(), 3)), clipDots);
      points.renderOrder = 5;
      ghost.add(points);
    }
    if (clipPlane) {
      const kept = [], cut = [];
      for (const item of clipBrushes()) {
        const {back, front} = B.split(item, clipPlane, clipFields(item));
        if (back) kept.push(back);
        if (front) cut.push(front);
      }
      for (const [brushes, material] of [[kept, clipKept], [cut, clipCut]]) if (brushes.length) { const lines = edgesOf(...brushes); lines.material = material; lines.renderOrder = 4; ghost.add(lines); }
      say(`Plane through ${clipPoints.length} points: green is kept, red cut away · Enter clips, Shift+Enter splits, wheel flips`);
    }
    updateTools();
  }
  function flipClip() { clipFlip = !clipFlip; showClip(); }
  function applyClip(both) {
    if (!clipPlane) { say('Click two or three points first'); return; }
    const changes = new Map();
    for (const item of clipBrushes()) {
      const {back, front} = B.split(item, clipPlane, clipFields(item));
      if (back && front) changes.set(item, both ? [back, front] : [back]);
      else if (!back && !both) changes.set(item, []);  // Wholly on the side cut away.
    }
    if (!changes.size) { say('The plane does not cut the selection'); return; }
    replace(changes, [], 'changed');
    showClip();
    say(`${both ? 'Split' : 'Clipped'} ${changes.size} brush${changes.size > 1 ? 'es' : ''}`);
  }

  // ---- The console (`), for the game's editor commands that have no key ----
  const TYPES = {worldspawn: 1, teleporter: 2, jumppad: 3, target: 4, effect: 5, pickup: 6, pointlight: 7, playerspawn: 8, prefab: 9};
  // A grid or angle step from a command: the toolbar's list takes a value it does not have.
  function setStep(key, text) {
    const value = Number(text), list = $(key);
    if (!(value > 0)) { say(`${key === 'grid' ? 'me_snapdistance' : 'me_snapangle'} <number>: now ${settings[key]}`); return; }
    if (![...list.options].some(option => Number(option.value) === value)) list.append(new Option(String(value)));
    settings[key] = value;
    save();
    setEditing(editing);
    say(`${key === 'grid' ? 'Grid' : 'Angle step'} ${value}`);
  }
  const COMMANDS = {
    me_createprefab: name => createPrefab(name),
    me_breakprefab: () => breakPrefabs(),
    me_updateprefab: name => updatePrefab(name),
    me_listprefabs: () => showPrefabs(),
    me_editprefab: () => editPrefabInPlace(),
    me_closeprefab: () => closePrefab(),
    me_rotate_inc: () => rotateSelection(1),
    me_rotate_dec: () => rotateSelection(-1),
    me_snapangle: value => setStep('angle', value),
    me_mirror: (axis = '') => { const index = 'xyz'.indexOf(axis.toLowerCase()); mirrorSelection(index >= 0 && axis ? index : undefined); },
    me_snapdistance: value => setStep('grid', value),
    me_createtype: (type = '', name = '') => {
      const number = TYPES[type.toLowerCase()];
      if (!number) { say(`me_createtype ${Object.keys(TYPES).join('|')}`); return; }
      if (number === 9) { if (M.prefab(map, name)) placePrefab(M.prefab(map, name).name); else say('me_createtype prefab <name of a prefab of this map>'); return; }
      if (createType !== number) setCreate(number);
    },
    me_showproperties: () => { panelOpen = true; panelView = 'properties'; showPanel(); },
    me_activematerial: name => { if (name) chooseMaterial(name, false); else showMaterialBrowser(); },
    editortoggleclipmode: () => setClipMode(!clipMode),
    editortogglevertexmode: () => setVertexMode(!vertexMode),
    help: () => say(`Commands: ${Object.keys(COMMANDS).join(', ')}`),
  };
  function run(line) {
    const [command, ...args] = line.trim().split(/\s+/);
    if (!command) return;
    const handler = COMMANDS[command.toLowerCase()];
    if (!handler) { say(`Unknown command ${command}; help lists them`); return; }
    handler(...args);
  }
  $('console').addEventListener('keydown', event => {
    event.stopPropagation();
    if (event.key === 'Enter') { const line = event.target.value; event.target.value = ''; event.target.blur(); if (editing && flat) run(line); }
    if (event.key === 'Escape') event.target.blur();
  });

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
    split() {
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
  $('materialsButton').addEventListener('click', event => { showMaterialBrowser(); event.target.blur(); });
  $('prefabsButton').addEventListener('click', event => { if (panelOpen && panelView === 'prefabs') { panelOpen = false; showPanel(); } else showPrefabs(); event.target.blur(); });
  $('rotateInc').addEventListener('click', event => { rotateSelection(1); event.target.blur(); });
  $('mirror').addEventListener('click', event => { mirrorSelection(event.shiftKey ? 1 : undefined); event.target.blur(); });
  $('rotateDec').addEventListener('click', event => { rotateSelection(-1); event.target.blur(); });
  $('clipButton').addEventListener('click', event => { setClipMode(!clipMode); event.target.blur(); });
  $('angle').addEventListener('change', event => { settings.angle = Number(event.target.value); event.target.blur(); save(); updateTools(); });
  $('propsClose').addEventListener('click', () => { panelOpen = false; showPanel(); });

  function setCreate(type) {
    createType = createType === type ? 0 : type;
    if (createType) { vertexMode = false; bridging = false; bridgePreview = null; clipMode = false; clipPoints = []; ghost.clear(); }
    showHover(null);
    updateTools();
    say(createType ? `${createLabel()}: ${createType <= 3 ? 'drag a rectangle on a surface' : 'click where it goes'}` : '');
  }
  function setVertexMode(on) {
    vertexMode = on;
    if (on) { createType = 0; bridging = false; bridgePreview = null; clipMode = false; clipPoints = []; ghost.clear(); }
    showHover(null);
    showSelection();
    say(on ? (selectedBrushes().length ? 'Drag a corner; Alt drags it up and down' : 'Select a brush to see its corners') : '');
  }
  function setEditing(on) {
    editing = on;
    $('tools').hidden = !on;
    clips.visible = volumes.visible = selection.visible = on;
    for (const mesh of entityLayer.children) if (mesh.userData.editOnly) mesh.visible = on;
    editorModels.visible = on;
    uniforms.uGrid.value = on ? settings.grid : 0;
    $('crosshair').hidden = on;
    if (!on) { createType = 0; vertexMode = false; bridging = false; bridgePreview = null; clipMode = false; clipPoints = []; texturePreview = null; showHover(null); ghost.clear(); }
    if (on && document.pointerLockElement === canvas) document.exitPointerLock();
    // Playing starts where the camera is, as the game's play mode starts where the editor's camera was.
    player = null;
    $('help').textContent = helpText();
    if (flat) showReady();
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
    // The game's field of view (r_fov, 110 by default) is horizontal on a 4:3 frame, whatever the window's shape (measured:
    // 110 shows 124° across at 16:9); Three's is vertical.
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2) * 3 / 4));
    camera.updateProjectionMatrix();
  }
  $('fov').addEventListener('change', event => { settings.fov = Math.max(30, Math.min(130, Number(event.target.value) || 110)); save(); applySettings(); });
  for (const id of ['invertX', 'invertY']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
  $('grid').addEventListener('change', event => { settings.grid = Number(event.target.value); event.target.blur(); save(); setEditing(editing); updateTools(); });
  $('reset').addEventListener('click', () => { speed = 400; showViewpoint(0); if (flat) showReady(); });
  new ResizeObserver(applySettings).observe(canvas.parentElement);

  // Flying: a click captures the mouse (dragging also looks). Editing: a click selects, the right button looks.
  const keys = new Set();
  let looking = false, downAt = null;
  canvas.addEventListener('contextmenu', event => event.preventDefault());
  canvas.addEventListener('dblclick', event => {
    if (!editing || !flat || createType || clipMode || bridging || vertexMode) return;
    const hit = pick(event);
    const entry = hit && (hit.entity || hit.entry);
    if (!entry || !entry.path.length) return;
    selected.clear(); selected.add(entry.path[0]);
    editPrefabInPlace();
  });
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
    if (editing && event.button === 0 && still && !dragged && !createType && !clipMode && flat) select(event);
    if (event.button === 2 || !editing) looking = false;
    downAt = null;
  });
  window.addEventListener('blur', () => { looking = false; keys.clear(); });
  document.addEventListener('mousemove', event => {
    if (event.target === canvas) mouseAt = {clientX: event.clientX, clientY: event.clientY};
    if (drag && editing) { continueDrag(event); return; }
    if (editing && vertexMode && !looking && event.target === canvas) { const corner = nearestDot(event); showHover(corner && corner.position); }
    if (editing && bridging && !looking && event.target === canvas) aimBridge(event);
    // The texture preview stays while the mouse is on its face (or the face is the one Shift-click picked).
    if (editing && texturePreview && !looking && event.target === canvas && !picked.some(face => face.item === texturePreview.item)) {
      const hit = pick(event, false);
      if (!hit || hit.entry.source !== texturePreview.item || hit.face !== texturePreview.item.faces[texturePreview.index]) { texturePreview = null; showSelection(); }
    }
    if (!looking && document.pointerLockElement !== canvas) return;
    camera.rotation.y -= event.movementX * .0025 * (settings.invertX ? -1 : 1);
    camera.rotation.x = Math.max(-1.55, Math.min(1.55, camera.rotation.x - event.movementY * .0025 * (settings.invertY ? -1 : 1)));
  });
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    if (editing && clipMode) { flipClip(); return; }
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
    if (/^Digit[1-9]$/.test(event.code) && !ctrl && !editing) { showViewpoint(Number(event.code.slice(5)) - 1); player = null; }
    if (event.code === 'KeyF' && !ctrl && !editing) { setWalking(!walking); return; }
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
      if (!ctrl && event.code === 'KeyC') { setClipMode(!clipMode); return; }
      if (event.code === 'Backquote') { event.preventDefault(); $('console').focus(); return; }
      if (clipMode && (event.code === 'Enter' || event.code === 'NumpadEnter')) { event.preventDefault(); if (ctrl) flipClip(); else applyClip(event.shiftKey); return; }
      if (event.code === 'NumpadAdd' || event.code === 'NumpadSubtract') { event.preventDefault(); rotateSelection(event.code === 'NumpadAdd' ? 1 : -1); return; }
      if (event.code === 'Backspace' && selected.size) { event.preventDefault(); actions.delete(); return; }
      if (event.code === 'Escape') {
        if (drag) { drag = null; selection.position.set(0, 0, 0); ghost.clear(); showSelection(); if (clipMode) showClip(); return; }
        if (clipMode) { if (clipPoints.length) { clipPoints = []; showClip(); say(''); } else setClipMode(false); return; }
        if (bridging) { stopBridge(); return; }
        if (picked.length) { picked = []; showSelection(); say(''); return; }
        if (createType) { setCreate(createType); return; }
        if (vertexMode) { setVertexMode(false); return; }
        if (texturePreview) { texturePreview = null; showSelection(); say(''); return; }
        if (!selected.size && openPrefab) { closePrefab(); return; }
        selected.clear(); say(''); showSelection(); return;
      }
      // Shift and the arrows (or PgUp and PgDn) move the selection a grid step along the horizontal axis nearest to
      // where the camera looks (or up and down); without Shift these keys are the game's texture keys.
      const step = settings.grid, yaw = -camera.rotation.y, along = Math.round(yaw / (Math.PI / 2)) & 3;
      const ahead = [[0, 0, 1], [1, 0, 0], [0, 0, -1], [-1, 0, 0]][along], side = [[1, 0, 0], [0, 0, -1], [-1, 0, 0], [0, 0, 1]][along];
      const arrows = {ArrowUp: ahead, ArrowDown: ahead.map(x => -x), ArrowRight: side, ArrowLeft: side.map(x => -x), PageUp: [0, 1, 0], PageDown: [0, -1, 0]};
      if (event.shiftKey && !ctrl && arrows[event.code]) { event.preventDefault(); if (selected.size) move(arrows[event.code].map(x => x * step + 0)); return; }
      if (!event.shiftKey && !ctrl && !event.altKey && TEXTURE_KEYS[event.code]) { event.preventDefault(); moveTexture(event.code); return; }
    }
    if (/^(Key[WASDQE]|Space|Shift(Left|Right))$/.test(event.code) && !ctrl) { keys.add(event.code); if (event.code === 'Space') event.preventDefault(); }
  });
  document.addEventListener('keyup', event => keys.delete(event.code));

  // ---- Play mode (movement.js): walking through the map, as the game's play mode; F flies instead ----
  const P = window.ReflexMovement;
  let walking = true, player = null, playWorld = null, triggers = null, tick = 0, lowest = -1e9;
  const LIQUID = /(^|\/)liquids\//;
  // The brushes a player meets: those of the world, not volumes, liquids or weapon clips; and the volumes, which
  // teleport and launch.
  function buildPlayWorld() {
    const solid = [], volume = [];
    for (const entry of flat.brushes) {
      if (M.isVolume(entry.owner)) { volume.push({brush: entry.brush, tag: entry.owner}); continue; }
      const faces = entry.brush.faces;
      if (faces.some(face => LIQUID.test(face.material || '')) || faces.every(face => /editor_weaponclip$/.test(face.material || ''))) continue;
      solid.push({brush: entry.brush, tag: entry});
    }
    playWorld = P.createWorld(solid);
    triggers = P.createWorld(volume);
    const box = new THREE.Box3().setFromObject(world);
    lowest = box.isEmpty() ? -4096 : box.min.y - 2048;
  }
  // Puts the player with its eye at `eye` (the map's axes), or as near above as there is room; looks along `yaw`.
  function placePlayer(eye) {
    if (!playWorld) buildPlayWorld();
    const origin = P.free(playWorld, [eye[0], eye[1] - P.PLAYER.eye, eye[2]]);
    player = origin ? {origin, velocity: [0, 0, 0], ground: null} : null;
    if (!origin) say('No room for the player here: F flies');
  }
  const targetNamed = name => flat.entities.find(entry => entry.entity.type === 'Target' && M.property(entry.entity, 'name') === name && entry.position);
  // Teleporters and jump pads the player stands in: a teleporter puts it at its Target, facing the Target's yaw,
  // at the speed it had; a jump pad throws it onto its Target.
  function touchTriggers() {
    const inside = triggers.trace(player.origin, player.origin);
    if (!inside.allSolid || !inside.tag) return;
    const owner = inside.tag, target = targetNamed(M.property(owner, 'target'));
    if (!target) return;
    if (owner.type === 'Teleporter') {
      const yaw = (M.property(target.entity, 'angles') || [0])[0] * Math.PI / 180, speed = Math.hypot(player.velocity[0], player.velocity[2]);
      player.origin = P.free(playWorld, [target.position[0], target.position[1] + 24.125, target.position[2]]) || player.origin;
      player.velocity = [Math.sin(yaw) * speed, 0, Math.cos(yaw) * speed];
      camera.rotation.y = -yaw;
    } else if (owner.type === 'JumpPad') {
      player.velocity = P.launch(player.origin, target.position);
      player.ground = null;
    }
  }
  function playStep(delta) {
    if (!playWorld) buildPlayWorld();
    if (!player) { placePlayer([camera.position.x, camera.position.y, -camera.position.z]); if (!player) return; }
    const held = code => Number(keys.has(code)), input = {forward: held('KeyW') - held('KeyS'), right: held('KeyD') - held('KeyA'), jump: keys.has('Space'), yaw: -camera.rotation.y};
    tick = Math.min(tick + delta, .1);
    while (tick >= 1 / 125) {
      P.move(player, input, playWorld, 1 / 125);
      touchTriggers();
      tick -= 1 / 125;
    }
    if (player.origin[1] < lowest) { showViewpoint(0); placePlayer([camera.position.x, camera.position.y, -camera.position.z]); if (!player) return; }
    camera.position.set(player.origin[0], player.origin[1] + P.PLAYER.eye, -player.origin[2]);
  }
  function setWalking(on) {
    walking = on;
    player = null;
    if (flat) showReady();
    $('help').textContent = helpText();
  }
  const helpText = () => editing
    ? '0 play · Click select (Ctrl adds) · Drag move (Alt up/down) · Shift-drag face · Shift-click face, B bridge · 1–8 create · V vertices · C clip · Numpad +/− turn · Arrows, Home/End/Ins/Del, PgUp/PgDn, , . texture · Shift+arrows nudge · N properties · ` console · Right-drag look · WASD QE · G clone · Backspace delete · Z/X undo/redo · K/M material'
    : walking ? '0 edit · Click to capture / drag to look · WASD walk · Space jump · F fly · Esc release · 1–9 viewpoints'
      : '0 edit · Click to capture / drag to look · WASD move · Space up · Shift down · Wheel speed · F walk · Esc release · 1–9 viewpoints';

  // ---- The frame ----
  // As the game makes it (its compiled shaders and reflex.exe): the scene in linear light into a half-float target,
  // where added glows run past 1; bloom (bloomHighPass: what is brighter than 2.2 in luma, half the excess; five
  // targets of half the size each, bloomBlur across and down; upsample adds each into the next larger with a 9-tap tent
  // of radius .02 down to .0015 of the frame's width, weighted 1, 1, 1.5, .5 and .2 into the scene); the map's fog
  // (postEffects_FOG); then r_gamma's 2.2. The editor's volumes, markers and outlines are drawn after that, straight
  // onto the frame as before, against the scene's depth.
  const sceneTarget = (w, h) => new THREE.WebGLRenderTarget(w, h, {type: THREE.HalfFloatType, samples: 4, depthTexture: new THREE.DepthTexture(w, h)});
  const blurTarget = (w, h) => new THREE.WebGLRenderTarget(Math.max(1, w), Math.max(1, h), {type: THREE.HalfFloatType, depthBuffer: false});
  let hdr = null, bright = null, levels = [];
  const quadScene = new THREE.Scene(), quadCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1), quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2));
  quad.frustumCulled = false; quadScene.add(quad);
  const pass = (fragmentShader, uniforms, more) => new THREE.ShaderMaterial({uniforms, depthTest: false, depthWrite: false, ...more, fragmentShader,
    vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = vec4(position.xy, 0., 1.); }'});
  const TENT = `vec3 tent(sampler2D t, vec2 at, vec2 r) {
      vec2 d = r * .707;
      return texture2D(t, at).rgb * .25 + (texture2D(t, at + vec2(r.x, 0.)).rgb + texture2D(t, at - vec2(r.x, 0.)).rgb + texture2D(t, at + vec2(0., r.y)).rgb + texture2D(t, at - vec2(0., r.y)).rgb) * .125
        + (texture2D(t, at + d).rgb + texture2D(t, at - d).rgb + texture2D(t, at + vec2(d.x, -d.y)).rgb + texture2D(t, at - vec2(d.x, -d.y)).rgb) * .0625;
    }`;
  const highPass = pass(`uniform sampler2D uInput; varying vec2 vUv;
    void main() { vec3 c = max(texture2D(uInput, vUv).rgb, 0.); gl_FragColor = vec4(c * clamp((dot(c, vec3(.299, .587, .114)) - 2.2) * .5, 0., 1.), 1.); }`, {uInput: {value: null}});
  const blurPass = pass(`uniform sampler2D uInput; uniform vec2 uStep; varying vec2 vUv;
    void main() {
      vec3 c = texture2D(uInput, vUv).rgb * .227027;
      float w[4] = float[](.194595, .121622, .054054, .016216);
      for (int i = 0; i < 4; i++) c += (texture2D(uInput, vUv + uStep * float(i + 1)).rgb + texture2D(uInput, vUv - uStep * float(i + 1)).rgb) * w[i];
      gl_FragColor = vec4(c, 1.);
    }`, {uInput: {value: null}, uStep: {value: new THREE.Vector2()}});
  const upsamplePass = pass(`${TENT} uniform sampler2D uInput; uniform vec2 uRadius; uniform float uIntensity; varying vec2 vUv;
    void main() { gl_FragColor = vec4(tent(uInput, vUv, uRadius) * uIntensity, 1.); }`, {uInput: {value: null}, uRadius: {value: new THREE.Vector2()}, uIntensity: {value: 1}},
  {transparent: true, blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneFactor});
  // Fog where the scene has depth (not the sky): f = max(h², d²) of the way through [start, end] by distance and down
  // through [top, bottom] by height (top waving 4 up and down), toward fogColor (its bytes / 255).
  const fog = {uFogColour: {value: new THREE.Vector3()}, uFog: {value: new THREE.Vector4(0, 1e9, -1e9, -2e9)}};
  const compositePass = pass(`${TENT} uniform sampler2D uScene, uBloom, uDepth; uniform vec2 uRadius; uniform mat4 uInverse; uniform vec3 uViewer, uFogColour; uniform vec4 uFog; uniform float uTime; varying vec2 vUv;
    void main() {
      vec3 c = texture2D(uScene, vUv).rgb + tent(uBloom, vUv, uRadius) * .2;
      float depth = texture2D(uDepth, vUv).r;
      if (depth < 1.) {
        vec4 w = uInverse * vec4(vUv * 2. - 1., depth * 2. - 1., 1.);
        vec3 p = w.xyz / w.w;
        float d = clamp((distance(p, uViewer) - uFog.x) / (uFog.y - uFog.x), 0., 1.);
        float h = clamp((uFog.z - (p.y + 4. * sin(p.x / 48. + .8 * uTime - p.z / 48.))) / (uFog.z - uFog.w), 0., 1.);
        c = mix(c, uFogColour, max(h * h, d * d));
      }
      gl_FragColor = vec4(pow(max(c, 0.), vec3(1. / 2.2)), 1.);
      gl_FragDepth = depth;
    }`, {uScene: {value: null}, uBloom: {value: null}, uDepth: {value: null}, uRadius: {value: new THREE.Vector2()}, uInverse: {value: new THREE.Matrix4()}, uViewer: {value: new THREE.Vector3()}, uTime: {value: 0}, ...fog},
  {depthTest: true, depthWrite: true, depthFunc: THREE.AlwaysDepth});
  const BLOOM_RADII = [.0015, .003, .006, .0125, .02], BLOOM_WEIGHTS = [.2, .5, 1.5, 1, 1];
  function runPass(material, values, target, clear = true) {
    for (const [name, value] of Object.entries(values)) material.uniforms[name].value = value;
    quad.material = material;
    renderer.setRenderTarget(target);
    renderer.autoClear = clear;
    renderer.render(quadScene, quadCamera);
    renderer.autoClear = true;
  }
  const overlays = [clips, volumes, selection, entityLayer, ghost, hoverDot], drawingSize = new THREE.Vector2(), aspect = new THREE.Vector2();
  function draw(view = camera) {
    renderer.getDrawingBufferSize(drawingSize);
    const [w, h] = [drawingSize.x, drawingSize.y];
    particleHeight.value = h;
    if (!hdr || hdr.width !== w || hdr.height !== h) {
      for (const target of [hdr, bright, ...levels.flat()]) if (target) { if (target.depthTexture) target.depthTexture.dispose(); target.dispose(); }
      hdr = sceneTarget(w, h); bright = blurTarget(w, h);
      levels = BLOOM_RADII.map((_, i) => [blurTarget(w >> (i + 1), h >> (i + 1)), blurTarget(w >> (i + 1), h >> (i + 1))]);
    }
    skyUniforms.uTime.value = clock.elapsedTime;
    const shown = overlays.map(object => object.visible);
    for (const object of overlays) object.visible = false;
    renderer.setRenderTarget(hdr);
    renderer.render(scene, view);
    runPass(highPass, {uInput: hdr.texture}, bright);
    let from = bright;
    for (const [across, down] of levels) {
      runPass(blurPass, {uInput: from.texture, uStep: new THREE.Vector2(1 / across.width, 0)}, across);
      runPass(blurPass, {uInput: across.texture, uStep: new THREE.Vector2(0, 1 / down.height)}, down);
      from = down;
    }
    const radius = r => new THREE.Vector2(r, r * w / h);
    for (let i = levels.length - 1; i > 0; i--) runPass(upsamplePass, {uInput: levels[i][1].texture, uRadius: radius(BLOOM_RADII[i]), uIntensity: BLOOM_WEIGHTS[i]}, levels[i - 1][1], false);
    view.updateMatrixWorld();
    runPass(compositePass, {uScene: hdr.texture, uBloom: levels[0][1].texture, uDepth: hdr.depthTexture, uRadius: radius(BLOOM_RADII[0]),
      uInverse: new THREE.Matrix4().multiplyMatrices(view.matrixWorld, view.projectionMatrixInverse), uViewer: view.position, uTime: clock.elapsedTime}, null);
    // The overlays, alone, onto the frame.
    const rest = [...root.children.filter(object => !overlays.includes(object)), skyDome], restShown = rest.map(object => object.visible);
    for (const object of rest) object.visible = false;
    overlays.forEach((object, i) => { object.visible = shown[i]; });
    renderer.autoClear = false;
    renderer.render(scene, view);
    renderer.autoClear = true;
    rest.forEach((object, i) => { object.visible = restShown[i]; });
  }

  const clock = new THREE.Clock(), forward = new THREE.Vector3(), right = new THREE.Vector3(), step = new THREE.Vector3();
  renderer.setAnimationLoop(() => {
    const delta = Math.min(clock.getDelta(), .1), held = code => Number(keys.has(code));
    if (!editing && walking && flat) { playStep(delta); followSun(); draw(); return; }
    camera.getWorldDirection(forward);
    right.crossVectors(forward, camera.up).normalize();
    step.copy(forward).multiplyScalar(held('KeyW') - held('KeyS')).addScaledVector(right, held('KeyD') - held('KeyA'));
    step.y += held('Space') + held('KeyE') - held('KeyQ') - (editing ? 0 : held('ShiftLeft') + held('ShiftRight'));
    if (step.lengthSq()) camera.position.addScaledVector(step.normalize(), speed * delta);
    followSun();
    draw();
  });
  window.skinnerReflexMaps = {renderer, scene, camera, root, get map() { return map; }, get flat() { return flat; }, get template() { return template; },
    get createType() { return createType; }, get vertexMode() { return vertexMode; }, get bridging() { return bridging; }, get segments() { return segments; }, get clipMode() { return clipMode; }, get clipPoints() { return clipPoints; },
    get player() { return player; }, get openPrefab() { return openPrefab; }, get walking() { return walking; }, settings, run, load, actions, selected, setEditing, setWalking, showViewpoint, beforeShot: followSun, draw};

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
  packColours['#mirror'] = {colour: [1, 1, 1], metallic: 1, roughness: 0, shader: 'internal/shaders/deferredPbr'};  // the reflection probe's sphere
  try { packEffects = await (await fetch(data + 'effects.json')).json() || packEffects; } catch (_) { /* No models are drawn. */ }
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
      if (entry.light) {
        const light = await fetch(`${data}maps/${entry.light}`);
        try { useLight(L.parse(await light.arrayBuffer())); } catch (error) { notes.push(`Its baked light could not be read (${error.message})`); showNotes(); }
      }
    } else {
      if (!maps.length) $('importPanel').open = true;
      load(M.write(M.empty()), 'untitled');
      setEditing(true);
      say('New map: B makes a box in front of the camera; 0 flies; Open .map file loads one from disk');
    }
  } catch (error) { showStatus('Map could not load: ' + error.message); }
});

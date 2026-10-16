/* Shared particle billboards for the Maps pages: one instanced draw per texture and blend, every particle worked
   out in the vertex shader from the time alone (no per-frame CPU work beyond a few uniforms).

   An emitter fires a burst every `period` seconds; a burst spawns `count` particles, each after its own delay, each
   living `life` seconds. A particle slot replays every `loop` = enough whole periods to cover delay + life, and draws
   new random numbers each replay, so the system is a pure function of time: pages can draw any moment.

   Templates (all ranges are [min, max]; times in seconds; vectors in the page's game axes):
     count, life, delay, even (delays spread evenly over the burst), origin [min3, max3] (emitter axes unless
     worldOrigin), velocity / acceleration [min3, max3] (emitter axes unless worldVelocity / worldAcceleration),
     gravity (along `up`), rotation (degrees), spin (degrees a second), cullRange (0: none),
     size / alpha {start, end, curve}, rgb {start [min3, max3], end [min3, max3], curve}, rgbTogether (one random
     number for all three channels), alphaFadesRgb (alpha scales rgb and the drawn alpha is 1), frames [columns, rows]
     (flipbook over the life; frameCount when the grid's last row is not full), sizeTogether (one random number
     for the start and end size), fade (alpha falls to 0 at this distance from the eye), screenSize (sizes are shares of
     the view depth). A curve is {linear, kind:
     'nonlinear' | 'wave' | 'clamp' | 'sine' | null, parm, random}: Raven FX's element biasing (FxPrimitives.cpp), which
     a plain start-to-end fade is {linear: true}; 'sine' runs start -> end -> start as sin(pi x life)^parm.
   Game axes map to the page by `toWorld` (sizes scale with it); `up` is the game's up for gravity.
   Quality tiers thin the slots and shorten the draw distance; the slot cap bounds the vertex work of any scene. */
(function(root) {
  'use strict';
  const TIERS = {high: {density: 1, distance: 1e9}, medium: {density: .5, distance: 4096}, low: {density: .25, distance: 2048}, off: {density: 0, distance: 0}};
  const ROW = 16, MAX_SLOTS = 65536;  // texels per template; instanced quads per scene.
  const KIND = {nonlinear: 2, wave: 4, clamp: 8, sine: 32};
  const curveCode = c => !c ? [0, 0] : [(c.linear ? 1 : 0) + (KIND[c.kind] || 0) + (c.random ? 16 : 0), c.parm || 0];
  const pair = v => Array.isArray(v) ? [v[0], v.length > 1 ? v[1] : v[0]] : [v || 0, v || 0];
  const box = v => v ? [v[0], v[1] || v[0]] : [[0, 0, 0], [0, 0, 0]];

  // A template as 16 RGBA float texels (see the shader's T(n)).
  function pack(t) {
    const out = new Float32Array(ROW * 4), set = (n, ...v) => out.set(v, n * 4);
    const [o0, o1] = box(t.origin), [v0, v1] = box(t.velocity), [a0, a1] = box(t.acceleration);
    const [rs0, rs1] = box(t.rgb?.start || [[1, 1, 1], [1, 1, 1]]), [re0, re1] = box(t.rgb?.end || t.rgb?.start || [[1, 1, 1], [1, 1, 1]]);
    const size = t.size || {}, alpha = t.alpha || {}, frames = t.frames || [1, 1];
    set(0, ...pair(t.life), ...pair(t.delay));
    set(1, ...pair(t.count || 1), t.even ? 1 : 0, t.cullRange || 0);
    set(2, ...o0, pair(t.rotation)[0]); set(3, ...o1, pair(t.rotation)[1]);
    set(4, ...v0, pair(t.spin)[0]); set(5, ...v1, pair(t.spin)[1]);
    set(6, ...a0, pair(t.gravity)[0]); set(7, ...a1, pair(t.gravity)[1]);
    set(8, ...pair(size.start ?? 1), ...pair(size.end ?? size.start ?? 1));
    set(9, ...pair(alpha.start ?? 1), ...pair(alpha.end ?? alpha.start ?? 1));
    set(10, ...rs0, t.rgbTogether ? 1 : 0); set(11, ...rs1, t.alphaFadesRgb ? 1 : 0);
    set(12, ...re0, (t.worldOrigin ? 1 : 0) + (t.worldVelocity ? 2 : 0) + (t.worldAcceleration ? 4 : 0) + (t.screenSize ? 8 : 0) + (t.sizeTogether ? 16 : 0)); set(13, ...re1, t.fade || 0);
    set(14, ...curveCode(size.curve), ...curveCode(alpha.curve)); set(15, ...curveCode(t.rgb?.curve), frames[0], t.frameCount || frames[0] * frames[1]);
    return out;
  }

  const VERTEX = `precision highp float; precision highp int;
    uniform sampler2D uTemplates; uniform float uTime, uDensity, uDistance, uColorScale, uRgb, uAlpha, uSizeScale;
    uniform mat3 uToWorld; uniform vec3 uUp, uEye;
    attribute vec2 corner; attribute vec4 iOrigin, iForward, iRight, iTime;
    varying vec2 vSt; varying vec4 vColor;
    vec4 T(int n) { return texelFetch(uTemplates, ivec2(n, int(iOrigin.w)), 0); }
    uint state;
    float rnd() { state = state * 747796405u + 2891336453u; uint w = ((state >> ((state >> 28u) + 4u)) ^ state) * 277803737u; return float((w >> 22u) ^ w) / 4294967295.; }
    float pick(vec2 range) { return mix(range.x, range.y, rnd()); }
    vec3 pick3(vec3 a, vec3 b) { return vec3(mix(a.x, b.x, rnd()), mix(a.y, b.y, rnd()), mix(a.z, b.z, rnd())); }
    // FxPrimitives.cpp's Update*: perc runs 1 -> 0 over the life (linear), blended with the nonlinear, wave or clamp
    // term; the value is start * perc + end * (1 - perc). parm is a percent of the life, or a wave's cos(age * parm * pi).
    float curve(vec2 code, float age, float life) {
      int mode = int(code.x); float p1 = 1., p2 = 1., at = code.y * .01 * life;
      if ((mode & 1) != 0) p1 = 1. - age / life;
      if ((mode & 2) != 0) { if (age > at) p2 = 1. - (age - at) / max(life - at, 1e-4); p1 = (mode & 1) != 0 ? .5 * (p1 + p2) : p2; }
      else if ((mode & 4) != 0) p1 *= cos(age * code.y * 3.14159265);
      else if ((mode & 8) != 0) { p2 = age < at ? (at - age) / max(at, 1e-4) : 0.; p1 = (mode & 1) != 0 ? .5 * (p1 + p2) : p2; }
      else if ((mode & 32) != 0) p1 = 1. - pow(sin(3.14159265 * clamp(age / life, 0., 1.)), code.y);
      if ((mode & 16) != 0) { uint keep = state; state ^= uint(uTime * 60.) * 2654435761u; p1 *= rnd(); state = keep; }
      return p1;
    }
    void hide() { gl_Position = vec4(2., 2., 2., 1.); vColor = vec4(0.); vSt = vec2(0.); }
    void main() {
      float local = uTime + iTime.x, cycle = floor(local / iTime.y);
      local -= cycle * iTime.y;
      state = floatBitsToUint(iForward.w) ^ (uint(int(cycle) + 65536) * 2246822519u) ^ (uint(iRight.w) * 3266489917u);
      rnd();
      vec4 t0 = T(0), t1 = T(1);
      float count = floor(pick(t1.xy) + .5), slot = iRight.w;
      float delay = t1.z > .5 ? slot * abs(t0.w - t0.z) / max(count, 1.) : pick(t0.zw);
      float life = pick(t0.xy), age = local - delay;
      vec3 origin = iOrigin.xyz, toEye = origin - uEye;
      float far = dot(toEye, toEye), cull = t1.w;
      if (slot >= count || age < 0. || age >= life || iTime.z >= uDensity || far > uDistance * uDistance || cull > 0. && far > cull * cull) { hide(); return; }
      vec3 F = iForward.xyz, R = iRight.xyz, U = cross(R, F);
      mat3 axes = mat3(F, R, U);  // Raven's axis[0..2]: the effect's x, y and z.
      int world = int(T(12).w);
      vec4 o0 = T(2), o1 = T(3), v0 = T(4), v1 = T(5), a0 = T(6), a1 = T(7);
      vec3 o = pick3(o0.xyz, o1.xyz), v = pick3(v0.xyz, v1.xyz), a = pick3(a0.xyz, a1.xyz);
      vec3 p = origin + ((world & 1) != 0 ? o : axes * o);
      v = (world & 2) != 0 ? v : axes * v;
      a = ((world & 4) != 0 ? a : axes * a) + uUp * pick(vec2(a0.w, a1.w));
      p += v * age + .5 * a * age * age;
      if (distance(p, uEye) < 16.) { hide(); return; }  // CParticle::Cull: not drawn this close to the eye.
      float rotation = pick(vec2(o0.w, o1.w)) + pick(vec2(v0.w, v1.w)) * age;
      vec4 s = T(8), al = T(9), rs0 = T(10), rs1 = T(11), re0 = T(12), re1 = T(13), c0 = T(14), c1 = T(15);
      float sk = rnd(), size0 = mix(s.x, s.y, sk), size1 = mix(s.z, s.w, (world & 16) != 0 ? sk : rnd()), alpha0 = pick(al.xy), alpha1 = pick(al.zw);
      vec3 rgb0, rgb1;
      if (rs0.w > .5) { float k = rnd(); rgb0 = mix(rs0.rgb, rs1.rgb, k); rgb1 = mix(re0.rgb, re1.rgb, k); }
      else { rgb0 = pick3(rs0.rgb, rs1.rgb); rgb1 = pick3(re0.rgb, re1.rgb); }
      float ps = curve(c0.xy, age, life), pa = curve(c0.zw, age, life), pc = curve(c1.xy, age, life);
      float radius = size0 * ps + size1 * (1. - ps);
      float alpha = clamp(alpha0 * pa + alpha1 * (1. - pa), 0., 1.);
      vec3 rgb = clamp(rgb0 * pc + rgb1 * (1. - pc), 0., 1.);
      if (re1.w > 0.) alpha *= clamp(1. - sqrt(far) / re1.w, 0., 1.);
      if (rs1.w > .5) { rgb *= alpha; alpha = 1.; }
      vColor = vec4(mix(vec3(1.), rgb, uRgb) * uColorScale, mix(1., alpha, uAlpha));
      vec3 P = uToWorld * p;
      vec3 right = vec3(viewMatrix[0][0], viewMatrix[1][0], viewMatrix[2][0]), up = vec3(viewMatrix[0][1], viewMatrix[1][1], viewMatrix[2][1]);
      float angle = radians(rotation), c = cos(angle), n = sin(angle);
      vec3 L = c * right + n * up, W = c * up - n * right;  // RB_SurfaceSprite turns the sprite's left and up by its rotation.
      // screenSize: the size is a share of the view depth (constant on screen), as Source's DUSTFLAGS_SCALEMOTES.
      radius *= (world & 8) != 0 ? -(viewMatrix * vec4(P, 1.)).z : uSizeScale;
      P += (L * corner.x + W * corner.y) * radius;
      // Flipbook: c1.w frames, row-major in c1.z columns, played over the life.
      vec2 frames = vec2(c1.z, ceil(c1.w / max(c1.z, 1.))), st = corner * vec2(.5, -.5) + .5;
      if (c1.w > 1.) { float f = min(floor(age / life * c1.w), c1.w - 1.); st = (st + vec2(mod(f, frames.x), floor(f / frames.x))) / frames; }
      vSt = st;
      gl_Position = projectionMatrix * viewMatrix * vec4(P, 1.);
    }`;
  const FRAGMENT = `uniform sampler2D map; uniform float uAlphaTest; varying vec2 vSt; varying vec4 vColor;
    void main() { vec4 c = texture2D(map, vSt) * vColor; if (c.a < uAlphaTest) discard; gl_FragColor = c; }`;

  // emitters: [{origin, forward, right, template, period, batch, share: [i, n]}] where batch is a key into `batches`:
  //   {texture, blendSrc, blendDst (Three factors), rgb, alpha (use the particle's colour / alpha), colorScale, alphaTest, renderOrder}.
  // options: {toWorld: 9 numbers (column-major, game -> page), up: game up, quality, maxSlots}.
  function create(THREE, templates, emitters, batches, options = {}) {
    const texels = new Float32Array(Math.max(1, templates.length) * ROW * 4);
    templates.forEach((t, i) => texels.set(pack(t), i * ROW * 4));
    const table = new THREE.DataTexture(texels, ROW, Math.max(1, templates.length), THREE.RGBAFormat, THREE.FloatType);
    table.needsUpdate = true;
    // Slots: per emitter, one per (burst still alive) x (particle in a burst).
    const slots = [];
    for (const e of emitters) {
      const t = templates[e.template], period = Math.max(e.period, .01);
      const span = pair(t.delay)[1] + pair(t.life)[1], bursts = Math.max(1, Math.ceil(span / period)), count = Math.ceil(pair(t.count || 1)[1]);
      slots.push({e, bursts, count, period, loop: bursts * period});
    }
    const wanted = slots.reduce((n, s) => n + s.bursts * s.count / (s.e.share?.[1] || 1), 0), cap = options.maxSlots || MAX_SLOTS;
    const keep = Math.min(1, cap / Math.max(wanted, 1));  // ponytail: an over-full scene drops slots evenly, not by distance.
    const groups = new Map();
    for (const {e, bursts, count, period, loop} of slots) {
      const list = groups.get(e.batch) || groups.set(e.batch, []).get(e.batch), seed = Math.random(), [share, shares] = e.share || [0, 1];
      for (let b = 0; b < bursts; b++) for (let k = 0; k < count; k++) {
        // A primitive with several shaders: each of its emitters (one a shader) takes every shares-th slot.
        if ((b * count + k) % shares !== share || ((b * count + k) * 0.6180339887 + seed) % 1 >= keep) continue;
        // iTime.z: the slot's place in the quality tiers' thinning (kept while below the tier's density).
        list.push(e.origin[0], e.origin[1], e.origin[2], e.template, ...e.forward, Math.random() * 1e6, ...e.right, k,
          -b * period - (e.offset || 0), loop, ((b * 7 + k) * 0.7548776662 + seed) % 1, 0);
      }
    }
    const meshes = [], uniforms = {uTemplates: {value: table}, uTime: {value: 0}, uDensity: {value: 1}, uDistance: {value: 1e9},
      uEye: {value: new THREE.Vector3()}, uUp: {value: new THREE.Vector3(...(options.up || [0, 0, 1]))},
      uToWorld: {value: new THREE.Matrix3().fromArray(options.toWorld || [1, 0, 0, 0, 1, 0, 0, 0, 1])}, uSizeScale: {value: 1}};
    uniforms.uSizeScale.value = Math.cbrt(Math.abs(uniforms.uToWorld.value.determinant())) || 1;
    const toGame = uniforms.uToWorld.value.clone().invert(), eye = new THREE.Vector3();
    const base = new THREE.InstancedBufferGeometry();
    base.setAttribute('corner', new THREE.Float32BufferAttribute([1, 1, -1, 1, -1, -1, 1, -1], 2));
    base.setIndex([0, 1, 2, 0, 2, 3]);
    let instances = 0;
    for (const [key, data] of groups) {
      const batch = batches[key], n = data.length / 16;
      if (!n || !batch || !batch.texture) continue;
      const geometry = base.clone(), buffer = new THREE.InstancedInterleavedBuffer(new Float32Array(data), 16);
      ['iOrigin', 'iForward', 'iRight', 'iTime'].forEach((name, i) => geometry.setAttribute(name, new THREE.InterleavedBufferAttribute(buffer, 4, i * 4)));
      geometry.instanceCount = n;
      const material = new THREE.ShaderMaterial({uniforms: {...uniforms, map: {value: batch.texture}, uColorScale: {value: batch.colorScale ?? 1},
          uRgb: {value: batch.rgb === false ? 0 : 1}, uAlpha: {value: batch.alpha === false ? 0 : 1}, uAlphaTest: {value: batch.alphaTest || 0}},
        vertexShader: VERTEX, fragmentShader: FRAGMENT, transparent: true, depthWrite: false, side: THREE.DoubleSide, forceSinglePass: true,  // one pass, not back then front
        blending: THREE.CustomBlending, blendSrc: batch.blendSrc ?? THREE.OneFactor, blendDst: batch.blendDst ?? THREE.OneFactor});
      const mesh = new THREE.Mesh(geometry, material);
      mesh.frustumCulled = false;
      mesh.renderOrder = batch.renderOrder ?? 1e9 - 20;
      meshes.push(mesh);
      instances += n;
    }
    const system = {meshes, instances, wanted, quality: 'high',
      setQuality(name) {
        const tier = TIERS[name] || TIERS.high;
        system.quality = TIERS[name] ? name : 'high';
        uniforms.uDensity.value = tier.density;
        uniforms.uDistance.value = tier.distance;
        for (const mesh of meshes) mesh.visible = tier.density > 0;
      },
      // eye: the camera in game axes, or in the page's when `world`.
      update(seconds, at, world = false) {
        uniforms.uTime.value = seconds;
        eye.set(...at);
        uniforms.uEye.value.copy(world ? eye.applyMatrix3(toGame) : eye);
      },
      dispose() { for (const mesh of meshes) { mesh.geometry.dispose(); mesh.material.dispose(); } table.dispose(); }};
    system.setQuality(options.quality || 'high');
    return system;
  }

  // The page's quality control: a select in the header, remembered per page.
  function control(onChange) {
    const key = 'skinner.maps.' + (location.pathname.split('/')[2] || '') + '.particles';
    let quality = 'high';
    try { quality = localStorage.getItem(key) || 'high'; } catch (_) { /* Storage may be unavailable. */ }
    if (!TIERS[quality]) quality = 'high';
    const label = document.createElement('label'), select = document.createElement('select');
    label.id = 'particle-quality';
    label.append('Particles ', select);
    for (const name of Object.keys(TIERS)) select.append(new Option(name, name, false, name === quality));
    select.setAttribute('aria-label', 'Particle quality');
    select.addEventListener('change', () => {
      quality = select.value;
      try { localStorage.setItem(key, quality); } catch (_) { /* Keep the in-page choice. */ }
      select.blur();
      onChange(quality);
    });
    const header = document.querySelector('header');
    if (header) header.append(label);
    return {get quality() { return quality; }, setCount(n) { label.title = `${n} particle emitters on this map`; }};
  }

  // A page's map.particles ({templates, batches, emitters}) with its control. A batch is a shader stage as the
  // imports write them: {map | frames, blend [src, dst] GL names, rgbGen, alphaGen, alphaFunc, scale}; the particle's colour
  // and alpha are used where rgbGen / alphaGen take the vertex's, scaled by `identity` for rgbGen vertex.
  // options: {loadTexture(stage) -> Promise<Texture>, add(mesh), identity, toWorld, up}.
  const FACTOR = {gl_one: 'OneFactor', gl_zero: 'ZeroFactor', gl_src_color: 'SrcColorFactor', gl_one_minus_src_color: 'OneMinusSrcColorFactor',
    gl_dst_color: 'DstColorFactor', gl_one_minus_dst_color: 'OneMinusDstColorFactor', gl_src_alpha: 'SrcAlphaFactor',
    gl_one_minus_src_alpha: 'OneMinusSrcAlphaFactor', gl_dst_alpha: 'DstAlphaFactor', gl_one_minus_dst_alpha: 'OneMinusDstAlphaFactor'};
  async function mount(THREE, {templates, batches, emitters}, options) {
    const identity = options.identity ?? 1, made = {};
    let system = null;
    const ui = control(quality => system?.setQuality(quality));
    await Promise.all(Object.entries(batches).map(async ([name, stage]) => {
      const [src, dst] = (stage.blend || ['gl_one', 'gl_zero']).map(f => THREE[FACTOR[String(f).toLowerCase()]]);
      const rgb = (stage.rgbGen || ['vertex'])[0], alpha = (stage.alphaGen || ['identity'])[0];
      made[name] = {texture: await options.loadTexture(stage), blendSrc: src ?? THREE.OneFactor, blendDst: dst ?? THREE.ZeroFactor,
        rgb: rgb === 'vertex' || rgb === 'exactvertex', alpha: alpha === 'vertex', colorScale: (stage.scale ?? 1) * (rgb === 'vertex' || rgb === 'identitylighting' ? identity : 1),
        alphaTest: {gt0: 1 / 255, ge128: .5}[String(stage.alphaFunc || '').toLowerCase()] || 0};
    }));
    system = create(THREE, templates, emitters, made, {toWorld: options.toWorld, up: options.up, quality: ui.quality});
    for (const mesh of system.meshes) options.add(mesh);
    ui.setCount(emitters.length);
    return system;
  }

  const api = {create, control, mount, pack, TIERS};
  if (typeof module !== 'undefined') module.exports = api; else root.skinnerParticles = api;
})(typeof window !== 'undefined' ? window : globalThis);

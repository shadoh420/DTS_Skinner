/* Other games' skies for the T1 Maps viewer's Sky list (viewer.js showSky): one function per /t1-map-skies renderer
   kind beyond its own dome and the Quake III box. Each draws `entry` into `group`, which the viewer keeps centred on
   the camera and scales to fit its clip range; `base` is the source pack's data URL. */
'use strict';
window.t1Skies = {
  // Native axes, z up (as the viewer's dome and box): Three (x, y, z) -> (x, -z, y).
  vertexShader: 'varying vec3 vDir; void main() { vDir = vec3(position.x, -position.z, position.y); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }',
  ball(material, radius, group, order = -3.5) {
    Object.assign(material, {depthTest: false, depthWrite: false, side: THREE.BackSide, fog: false});
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 32, 16), material);
    mesh.renderOrder = order;
    mesh.frustumCulled = false;
    group.add(mesh);
  },

  // Placed sky meshes (Nosgoth's domes, Vengeance's sky-zone models): each model's buffer and textures from its pack,
  // its matrix already relative to the sky's centre and turned as the source page's sky camera sees it; drawn in list
  // order behind everything, then scaled so the farthest vertex sits inside the viewer's sky radius.
  async meshes({entry, radius, group, loadTexture}) {
    const holder = new THREE.Group(), box = new THREE.Box3();
    let order = 0;
    for (const model of entry.sky.models) {
      const response = await fetch(entry.base + model.store + '/' + model.file);
      if (!response.ok) continue;
      const buffer = await response.arrayBuffer(), geometry = new THREE.BufferGeometry();
      let at = 0;  // The pack's model buffer: arrays back to back (tvg-maps/materials.js arrays()).
      for (const [name, item] of Object.entries(model.arrays).sort(([, a], [, b]) => a.offset - b.offset)) {
        if (item.offset !== at || at + item.count * 4 > buffer.byteLength) break;
        const values = item.dtype === '<u4' ? new Uint32Array(buffer, at, item.count) : new Float32Array(buffer, at, item.count);
        if (name === 'indices') geometry.setIndex(new THREE.BufferAttribute(values, 1));
        else if (name === 'vertices') geometry.setAttribute('position', new THREE.BufferAttribute(values, 3));
        else if (name === 'uvs') geometry.setAttribute('uv', new THREE.BufferAttribute(values, 2));
        at += item.count * 4;
      }
      if (!geometry.index || !geometry.attributes.position) continue;
      const materials = await Promise.all(model.groups.map(async (part, index) => {
        geometry.addGroup(part.start, part.count, index);
        const texture = part.texture && await loadTexture(entry.base + 'textures/' + part.texture, true), s = part.settings || {};
        const blend = part.kind === 'sky' ? [] : s.blend || [];  // Sky surfaces draw opaque, as the source pages draw them.
        const factors = {gl_zero: THREE.ZeroFactor, gl_one: THREE.OneFactor, gl_src_alpha: THREE.SrcAlphaFactor,
          gl_one_minus_src_alpha: THREE.OneMinusSrcAlphaFactor, gl_src_color: THREE.SrcColorFactor,
          gl_one_minus_src_color: THREE.OneMinusSrcColorFactor, gl_dst_color: THREE.DstColorFactor};
        const map = texture && (s.uvScale || s.uvOffset || s.clamp) ? texture.clone() : texture;
        if (map && map !== texture) {
          map.needsUpdate = true;
          if (s.clamp) map.wrapS = map.wrapT = THREE.ClampToEdgeWrapping;
          if (s.uvScale) map.repeat.fromArray(s.uvScale);
          if (s.uvOffset) map.offset.fromArray(s.uvOffset);
        }
        return new THREE.MeshBasicMaterial({map, color: map ? 0xffffff : 0x7f98aa, side: THREE.DoubleSide, fog: false,
          depthTest: false, depthWrite: false, alphaTest: s.alphaTest || 0, opacity: s.opacity ?? 1, transparent: !!blend.length,
          ...(blend.length ? {blending: THREE.CustomBlending, blendSrc: factors[blend[0]] ?? THREE.SrcAlphaFactor,
            blendDst: factors[blend[1]] ?? THREE.OneMinusSrcAlphaFactor} : {})});
      }));
      const mesh = new THREE.Mesh(geometry, materials);
      mesh.matrixAutoUpdate = false;
      mesh.matrix.fromArray(model.matrix);
      mesh.renderOrder = -3.5 + order++ * .001 + (model.groups.some(part => (part.settings?.blend || []).length && part.kind !== 'sky') ? .1 : 0);
      mesh.frustumCulled = false;
      geometry.computeBoundingBox();
      box.union(geometry.boundingBox.clone().applyMatrix4(mesh.matrix));
      holder.add(mesh);
    }
    const far = Math.max(...[box.min, box.max].flatMap(p => [p.x, p.y, p.z]).map(Math.abs).filter(Number.isFinite), 1e-6);
    holder.scale.setScalar(radius * .95 / (far * Math.sqrt(3)));
    // A dome that is an open bowl above its pivot (most are) would leave a gap above the horizon, where the source
    // level's own scenery stands: lower it until its rim is just below the camera.
    holder.position.y = -Math.max(0, box.min.y + .05 * far) * holder.scale.y;
    group.add(holder);
  },

  // Unreal, Unreal Tournament, Deus Ex: the sky zone is the source level itself, seen from its SkyZoneInfo and turned
  // with the view and by the zone's rotation (and rate), as unreal-maps/viewer.js draw() does; its fake backdrops
  // draw nothing. Its own pass behind the T1 world, through the game's display power. Returns {render, dispose}.
  // `adapter` is a map page's adapter (Vengeance's) whose surfaces, models and terrain the level is built with instead.
  async unreal({entry, loadTexture}, adapter = null) {
    const source = entry.sky, scene = new THREE.Scene(), get = async url => { const r = await fetch(url); if (!r.ok) throw Error(url); return r; };
    const map = {...await (await get(`${entry.base}maps/${source.map}/scene.json`)).json(), id: source.map};
    const file = name => loadTexture(entry.base + 'textures/' + name, true);
    if (adapter) {  // Its load and terrain read the page's scene by its debug name: give them this one.
      window[adapter.debugName] = {scene, renderer: {setClearColor() {}}};
      await adapter.load(map, file);
    }
    const buffer = await (await get(`${entry.base}maps/${source.map}/geometry.bin`)).arrayBuffer(), n = map.vertices;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(buffer, 0, n * 3), 3));
    geometry.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(buffer, n * 12, n * 2), 2));
    let lightMap = null;
    if (map.lightmap) {
      geometry.setAttribute('uv2', new THREE.BufferAttribute(new Float32Array(buffer, n * 20, n * 2), 2));
      lightMap = await new Promise(resolve => new THREE.TextureLoader().load(`${entry.base}maps/${source.map}/lightmap.png`, resolve, undefined, () => resolve(null)));
      if (lightMap) Object.assign(lightMap, {flipY: false, generateMipmaps: false, minFilter: THREE.LinearFilter});
    }
    if (map.colors) geometry.setAttribute('color', new THREE.BufferAttribute(new Uint8Array(buffer, n * 28, n * 4), 4, true));
    geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(buffer, n * (map.colors ? 32 : map.lightmap ? 28 : 20), map.indices), 1));
    if (adapter?.computeNormals) geometry.computeVertexNormals();
    adapter?.prepareGeometry?.(geometry, map);
    // Poly flags as unreal-maps/viewer.js surfaceMaterial: masked 0x2, translucent 0x4, modulated 0x40, fake backdrop
    // 0x80, two-sided 0x100; panning surfaces move their own copy of the texture.
    const panning = [], materials = await Promise.all(map.groups.map(async (group, index) => {
      geometry.addGroup(group.start, group.count, index);
      if (adapter) return adapter.material(group, group.texture && await file(group.texture), lightMap);
      const side = group.flags & 0x100 ? THREE.DoubleSide : THREE.FrontSide;
      if (group.flags & 0x80) return new THREE.MeshBasicMaterial({colorWrite: false, side});
      let texture = group.texture && await loadTexture(entry.base + 'textures/' + group.texture, true);
      if (texture && group.pan) { texture = texture.clone(); texture.needsUpdate = true; panning.push({texture, pan: group.pan}); }
      const common = {map: texture, color: texture ? 0xffffff : 0x808080, side, vertexColors: !!map.colors, fog: false, ...(lightMap ? {lightMap, lightMapIntensity: 2} : {})};
      if (group.flags & 0x44) {
        const add = group.flags & 0x4;
        return new THREE.MeshBasicMaterial({...common, transparent: true, depthWrite: false, blending: THREE.CustomBlending,
          blendSrc: add ? THREE.OneFactor : THREE.DstColorFactor, blendDst: add ? THREE.OneMinusSrcColorFactor : THREE.SrcColorFactor});
      }
      return new THREE.MeshBasicMaterial({...common, alphaTest: group.flags & 0x2 ? .5 : 0});
    }));
    scene.add(new THREE.Mesh(geometry, materials));
    if (adapter?.buildInstances) for (const object of await adapter.buildInstances(map, entry.base, get, file)) scene.add(object);
    const turn = rotator => {  // unreal-maps/viewer.js unrealTurn: Unreal's x, y, z are the page's z, -x, y.
      const [p, y, r] = rotator.map(a => a * Math.PI / 32768);
      const [cp, sp, cy, sy, cr, sr] = [Math.cos(p), Math.sin(p), Math.cos(y), Math.sin(y), Math.cos(r), Math.sin(r)];
      const columns = [[cp * cy, cp * sy, sp], [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp], [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]];
      const axis = [[1, -1], [2, 1], [0, 1]];
      return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().setFromMatrix3(new THREE.Matrix3().set(...axis.flatMap(([i, si]) => axis.map(([j, sj]) => columns[j][i] * si * sj)))));
    };
    const skyTurn = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().setFromMatrix3(new THREE.Matrix3().set(...source.rotation)));
    const skyCamera = new THREE.PerspectiveCamera(), target = new THREE.WebGLRenderTarget(1, 1);
    const ramp = new THREE.Scene(), flat = new THREE.Camera();
    ramp.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({uniforms: {frame: {value: target.texture}, power: {value: entry.power || 1}},
      depthTest: false, depthWrite: false, vertexShader: 'varying vec2 at; void main() { at = uv; gl_Position = vec4(position.xy, 0., 1.); }',
      fragmentShader: 'uniform sampler2D frame; uniform float power; varying vec2 at; void main() { gl_FragColor = vec4(pow(texture2D(frame, at).rgb, vec3(power)), 1.); }'})));
    const size = new THREE.Vector2();
    return {
      render(renderer, camera, seconds) {
        for (const {texture, pan} of panning) texture.offset.set(pan[0] * seconds % 1, pan[1] * seconds % 1);
        if (source.rate) skyTurn.copy(turn(source.rotator.map((a, i) => a + source.rate[i] * seconds)));
        renderer.getDrawingBufferSize(size);
        if (target.width !== size.x || target.height !== size.y) target.setSize(size.x, size.y);
        skyCamera.position.fromArray(source.origin);
        skyCamera.projectionMatrix.copy(camera.projectionMatrix);
        skyCamera.projectionMatrixInverse.copy(camera.projectionMatrixInverse);
        skyCamera.quaternion.copy(skyTurn).multiply(camera.quaternion);
        const current = renderer.getRenderTarget();
        renderer.setRenderTarget(target);
        renderer.clear();
        renderer.render(scene, skyCamera);
        renderer.setRenderTarget(current);
        renderer.render(ramp, flat);
      },
      dispose() {
        scene.traverse(object => { object.geometry?.dispose(); [].concat(object.material || []).forEach(material => material.dispose()); });
        for (const {texture} of panning) texture.dispose();
        lightMap?.dispose();
        target.dispose();
      },
    };
  },

  // Tribes: Vengeance: the same pass over the level as its own page builds it (tvg-maps/materials.js, loaded on first use).
  async vengeance(args) {
    if (!window.skinnerMapViewerConfig) await new Promise((resolve, reject) => document.head.append(Object.assign(document.createElement('script'),
      {src: '/static/tvg-maps/materials.js', onload: resolve, onerror: reject})));
    return this.unreal(args, window.skinnerMapViewerConfig);
  },

  // Skinner Sky (static/skinner-sky, docs/skinner-sky.md): a preset of plain numbers drawn by SkinnerSky.cginc, the
  // file Unity's SkinnerSky.shader includes too. Air: single scattering in 8 steps; then stars, Milky Way, moon, sun,
  // aurora, cirrus and lit clouds over it; one noise texture. Uniforms are the preset's keys, missing ones these
  // (the same defaults as the Unity shader's properties); out through exposure, ACES and the display's 2.2.
  skinnerDefaults: {sunAzimuth: 200, sunElevation: 35, sunColor: [1, .98, .95], sunIntensity: 20, sunSize: 1.2,
    moonAzimuth: 20, moonElevation: -30, moonColor: [.9, .92, 1], moonIntensity: 0, moonSize: 3.5,
    rayleigh: [1, 1, 1], ozone: 1, mie: 1, mieG: .8, groundColor: [.25, .24, .22], nightColor: [.0004, .0007, .0014], exposure: 1.5,
    starIntensity: 0, starDensity: 250, milkyWay: 0, cloudCoverage: .5, cloudSoftness: .15, cloudScale: 20, cloudHeight: 2,
    cloudSpeed: [.02, .005, 0], cloudColor: [1, 1, 1], cloudDarkness: 1, cloudOpacity: 1, cirrusCoverage: 0, cirrusScale: 150,
    cirrusSpeed: [.04, .01, 0], auroraIntensity: 0, auroraColor: [.1, 1, .4], auroraColor2: [.6, .2, 1]},
  // Drawn as docs/volumetrics.md: the air and volumetric clouds go into a 1024x256 sky texture, 1/8 of its rows a
  // frame (all of it on the first draw); the screen adds the sun, stars, moon and aurora per pixel. That costs about a
  // quarter of the per-pixel sky. Height fog (SkinnerFog.cginc, lit by the sky's horizon ring) is a pass over the
  // finished frame from its depth: fog.target() is where the viewer draws sky and world, fog.draw() puts them on screen.
  // The world is already display colour, so the fog's colour goes through the sky's exposure and ACES before the mix.
  // The target is multisampled 4x as the page's canvas is: +0.28 ms a frame at 2560x1440 (RX 7800 XT). The sky ball
  // stays in the viewer's sky group, drawn with the world; update() draws the sky texture's band and the ring first.
  // `ground`: the height the fog's fogBase is measured from (the map's start).
  volumetricDefaults: {cloudThickness: 1.5, fogDensity: .0012, fogHeight: 40, fogBase: 0, fogBrightness: 1},
  async skinner({entry, radius, group, loadTexture, time, ground = 0}) {
    const text = url => fetch(url).then(response => response.ok ? response.text() : null, () => null);
    const [noise, core, clouds, fog] = await Promise.all([loadTexture(entry.base + 'SkinnerSkyNoise.png', true),
      text(entry.base + 'SkinnerSky.cginc'), text('/static/volumetrics/SkinnerClouds.cginc'), text('/static/volumetrics/SkinnerFog.cginc')]);
    if (!noise || !core || !clouds || !fog) return null;
    const U = {skyNoise: {value: noise}, uTime: time, layerCoverage: {value: 0}, pano: {value: null}, ring: {value: null}}, declared = [];
    for (const [key, fallback] of Object.entries({...this.skinnerDefaults, ...this.volumetricDefaults})) {
      // The type is the declaration's (the default's): a preset's number for a colour fills all three, a list for a number gives its first.
      const value = entry.sky[key] ?? fallback, vector = Array.isArray(fallback);
      U[key] = {value: vector ? new THREE.Vector3(...[].concat(value, value, value).slice(0, 3)) : [].concat(value)[0]};
      declared.push(`uniform ${vector ? 'vec3' : 'float'} ${key};`);
    }
    U.fogBase.value += ground;
    // The core's own 2.5D cloud layer reads layerCoverage (0): the volumetric clouds replace it.
    const header = (prefix = '') => `uniform sampler2D skyNoise, pano, ring; uniform float uTime, layerCoverage; ${declared.join(' ')}
      #define UNROLL\n#define SAMPLE_LOD(s, uv, lod) textureLod(s, uv, lod)\n${prefix}#define cloudCoverage layerCoverage\n${core}\n#undef cloudCoverage\n${clouds}\n${fog}
      vec3 display(vec3 c) { return pow(aces(c * exposure), vec3(1. / 2.2)); }\n`;
    const quadVertex = 'varying vec2 vUv; void main() { vUv = uv; gl_Position = vec4(position.xy, 0., 1.); }';
    const flat = new THREE.Camera(), quad = (fragmentShader, uniforms = U) => new THREE.Scene().add(Object.assign(new THREE.Mesh(new THREE.PlaneGeometry(2, 2),
      new THREE.ShaderMaterial({uniforms, vertexShader: quadVertex, fragmentShader, depthTest: false, depthWrite: false})), {frustumCulled: false}));
    const half = {type: THREE.HalfFloatType, depthBuffer: false, wrapS: THREE.RepeatWrapping, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter};
    // The quality tier (the T1 Maps page's Skinner Sky panel can change it): sky texture size, march steps, and the
    // share of the texture's rows rewritten a frame (1/rows).
    const Q = {width: 1024, height: 256, steps: 24, rows: 8};
    const pano = new THREE.WebGLRenderTarget(Q.width, Q.height, half), ring = new THREE.WebGLRenderTarget(64, 1, half);
    pano.scissorTest = true;
    U.pano.value = pano.texture;
    U.ring.value = ring.texture;
    U.panoSize = {value: new THREE.Vector2(Q.width, Q.height)};
    const makePano = () => quad(header(`#define CLOUD_STEPS ${Q.steps}\n#define SKINNER_SKIP_BEHIND\n`) + `uniform vec2 panoSize;
      void main() { vec3 d = panoDir(gl_FragCoord.xy / panoSize), s = skinnerSky(d); vec4 c = skinnerClouds(d, s); gl_FragColor = vec4(s * c.a + c.rgb, c.a); }`);
    let panoScene = makePano();
    // The fog's colour by azimuth: the low sky 2-26 degrees up, averaged round under a triangle weight 54 degrees to
    // each side, read every 6 degrees (a sharp ring shows through thin fog as spokes; reads 12 degrees apart under
    // equal weights put a hard-edged bright band below a low sun). Same kernel as SkinnerVolumetrics.shader pass 1.
    const ringScene = quad(header() + `void main() {
      vec3 s = vec3(0., 0., 0.);
      for (int i = -8; i <= 8; i++) for (int j = 0; j < 3; j++) s += float(9 - abs(i)) * texture2D(pano, panoUV(toward(gl_FragCoord.x / 64. * 360. + float(i) * 6., 2. + float(j * j) * 6.))).rgb;
      gl_FragColor = vec4(s / 243., 1.);
    }`);
    this.ball(new THREE.ShaderMaterial({uniforms: U, vertexShader: this.vertexShader, extensions: {derivatives: true}, blending: THREE.NoBlending, fragmentShader: header() + `varying vec3 vDir;
      void main() {
        vec3 d = normalize(vDir);
        vec4 c = texture2D(pano, panoUV(d));
        vec3 s = c.rgb + (d.z > 0. ? skinnerBehind(d, c.rgb, toLight(PLANET + .002, d.z) * c.a) : vec3(0., 0., 0.));
        gl_FragColor = vec4(display(s) + (hash33(vec3(gl_FragCoord.xy, 7.)).x - .5) / 255., 1.);
      }`}), radius, group);
    const F = {...U, sceneColor: {value: null}, sceneDepth: {value: null}, projectionInverse: {value: null}, viewInverse: {value: null}, eyeZ: {value: 0}};
    const fogScene = quad(header() + `uniform sampler2D sceneColor, sceneDepth; uniform mat4 projectionInverse, viewInverse; uniform float eyeZ; varying vec2 vUv;
      void main() {
        float z = texture2D(sceneDepth, vUv).r;
        vec4 v = projectionInverse * vec4(vUv * 2. - 1., z * 2. - 1., 1.);
        v /= v.w;
        vec3 w = normalize((viewInverse * vec4(v.xyz, 0.)).xyz), rd = vec3(w.x, -w.z, w.y);
        float through = fogThrough(eyeZ, rd, z >= 1. ? 1e5 : length(v.xyz));
        gl_FragColor = vec4(mix(display(texture2D(ring, vec2(panoUV(rd).x, .5)).rgb * fogBrightness), texture2D(sceneColor, vUv).rgb, through), 1.);
      }`, F);
    let frame = null, row = 0, whole = true;
    const size = new THREE.Vector2();
    return {
      uniforms: U,  // For checks (skinnerT1Maps.skyPass()) and the page's Skinner Sky panel.
      redraw() { whole = true; },  // The whole sky texture on the next frame, after a value changed.
      quality(q) {
        const steps = Q.steps;
        Object.assign(Q, q);
        if (pano.width !== Q.width || pano.height !== Q.height) pano.setSize(Q.width, Q.height);
        U.panoSize.value.set(Q.width, Q.height);
        if (Q.steps !== steps) {
          panoScene.traverse(mesh => { mesh.geometry?.dispose(); mesh.material?.dispose(); });
          panoScene = makePano();
        }
        row = 0;
        whole = true;
      },
      update(renderer) {
        const current = renderer.getRenderTarget(), rows = whole ? Q.height : Math.ceil(Q.height / Q.rows);
        pano.scissor.set(0, whole ? 0 : row, Q.width, rows);
        renderer.setRenderTarget(pano);
        renderer.render(panoScene, flat);
        if (whole) whole = false; else row = (row + rows) % Q.height;
        renderer.setRenderTarget(ring);
        renderer.render(ringScene, flat);
        renderer.setRenderTarget(current);
      },
      fog: {
        target(renderer) {
          renderer.getDrawingBufferSize(size);
          if (frame?.width !== size.x || frame?.height !== size.y) {
            frame?.dispose();
            frame = new THREE.WebGLRenderTarget(size.x, size.y, {samples: renderer.capabilities.isWebGL2 ? 4 : 0, depthTexture: new THREE.DepthTexture(size.x, size.y)});
            F.sceneColor.value = frame.texture;
            F.sceneDepth.value = frame.depthTexture;
          }
          return frame;
        },
        draw(renderer, camera) {
          F.projectionInverse.value = camera.projectionMatrixInverse;
          F.viewInverse.value = camera.matrixWorld;
          F.eyeZ.value = camera.position.y;
          renderer.setRenderTarget(null);
          renderer.render(fogScene, flat);
        },
      },
      dispose() {
        for (const object of [panoScene, ringScene, fogScene]) object.traverse(mesh => { mesh.geometry?.dispose(); mesh.material?.dispose(); });
        for (const target of [pano, ring, frame]) { target?.depthTexture?.dispose(); target?.dispose(); }
      },
    };
  },

  // Quake: the sky texture's halves as two layers, front over back (index 0 clear), scrolling at 16 and 8 texels a
  // second, coloured through colormap row 32: the Quake Maps page's sky (quake-maps/materials.js kind 1), the ray's
  // height tripled to flatten the dome as R_DrawSkyChain does.
  async quake1({entry, radius, group, loadTexture, time}) {
    const [indices, colors] = await Promise.all([loadTexture(entry.base + 'textures/' + entry.sky.texture),
      loadTexture(entry.base + 'textures/colormap.png')]);
    if (!indices || !colors) return;
    for (const texture of [indices, colors]) Object.assign(texture, {magFilter: THREE.NearestFilter, minFilter: THREE.NearestFilter, generateMipmaps: false, needsUpdate: true});
    this.ball(new THREE.ShaderMaterial({uniforms: {indices: {value: indices}, colors: {value: colors}, seconds: time}, vertexShader: this.vertexShader,
      fragmentShader: `uniform sampler2D indices, colors; uniform float seconds; varying vec3 vDir;
        float pixel(vec2 uv) { return floor(texture2D(indices, fract(uv)).r * 255. + .5); }
        void main() {
          vec3 ray = vDir;
          ray.z *= 3.;
          vec2 st = ray.xy * (6. * 63. / max(length(ray), .001));
          vec2 back = fract((st + seconds * 8.) / 128.), front = fract((st + seconds * 16.) / 128.);
          float a = pixel(vec2(front.x * .5, front.y)), b = pixel(vec2(.5 + back.x * .5, back.y));
          gl_FragColor = texture2D(colors, vec2(((a == 0. ? b : a) + .5) / 256., 32.5 / 64.));
        }`, blending: THREE.NoBlending}), radius, group);
  },
};

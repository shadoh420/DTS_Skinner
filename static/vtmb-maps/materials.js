/* Bloodlines adapter for the shared Quake-family free-flight viewer. */
'use strict';
(function() {
  const time = window.skinnerMapTime = window.skinnerMapTime || {value: 0};
  // Fog: every material shares these two uniforms; update() and pass() repoint fogRange (start, end, max density) and refill fogColor.
  const fog = {fogColor: {value: new THREE.Vector3()}, fogRange: {value: null}}, OFF = new THREE.Vector3(0, 1, 0), forward = new THREE.Vector3();
  const spec = f => f && {range: new THREE.Vector3(f.start, f.end, f.max === undefined ? 1 : f.max), color: f.color, color2: f.color2 || f.color,
    dir: new THREE.Vector3(...(f.dir || [0, 0, 1])), blend: !!f.blend};
  const gamma = (s, b, i) => s.color[i] * b + s.color2[i] * (1 - b);
  let fogs = {main: null, sky: null, current: null, regions: new Map()}, view = null;
  function applyFog(s) {
    fog.fogRange.value = s ? s.range : OFF;
    if (!s) return;
    // fogblend: primary colour toward fogdir, secondary opposite (viewrender.cpp GetFogColor).
    const b = s.blend && view ? .5 * view.getWorldDirection(forward).dot(s.dir) + .5 : 1;
    fog.fogColor.value.set(gamma(s, b, 0), gamma(s, b, 1), gamma(s, b, 2));
  }
  applyFog(null);
  // TextureScroll on $basetexturetransform: uv + time * rate * (cos, sin)(angle), wrapped to [0, 1)
  // (texturescrollmaterialproxy.cpp OnBind); rate and angle may name a material variable (CFloatInput).
  function pan(p) {
    const b = (p.proxies || {}).texturescroll;
    if (!b || String(b.texturescrollvar).toLowerCase() !== '$basetexturetransform') return [0, 0];
    const value = (v, d) => { if (typeof v === 'string' && v[0] === '$') v = p[v.toLowerCase()]; const n = parseFloat(v); return Number.isFinite(n) ? n : d; };
    const rate = value(b.texturescrollrate, 1), angle = value(b.texturescrollangle, 0) * Math.PI / 180;
    return [rate * Math.cos(angle), rate * Math.sin(angle)];
  }
  window.skinnerMapViewerConfig = {
    label: 'Bloodlines', storageKey: 'skinner.vtmbmaps', debugName: 'skinnerVtmbMaps',
    games: [['vtmb', 'C:\\GOG Games\\VtMB']], dataRoot: '/vtmb-map-data/', importRoute: '/import_vtmb_maps',
    brightness: 1, fog, vertexLight: false,
    configureTexture(texture) {
      texture.anisotropy = 4;
      texture.magFilter = THREE.LinearFilter;
      texture.minFilter = THREE.LinearMipmapLinearFilter;
    },
    async load(map, loadTexture) {
      this.brightness = map.lighting.brightness;
      this.vertexLight = !!map.colors;  // Packs with vertex colours light models at their vertices (tools/source_light.py).
      // Worldspawn fog; the 3D sky pass uses the sky_camera's (none when it has fogenable 0); in water, the water material's.
      fogs = {main: spec(map.fog), sky: spec(map.sky3d && map.sky3d.fog), regions: new Map((map.regions || []).filter(Boolean).map(r => [r, spec(r.fog)]))};
      fogs.current = fogs.main; applyFog(fogs.main);
      await window.quake2Maps.loadSky(map.skybox, loadTexture);
    },
    update(seconds, camera, map, region) {
      view = camera; fogs.current = region && fogs.regions.get(region) || fogs.main; applyFog(fogs.current);
    },
    pass(which) { applyFog(which === 'sky' ? fogs.sky : fogs.current); },
    material(group, texture, lightMap) {
      if (group.kind === 'sky') return window.quake2Maps.material(group, texture, lightMap);  // The 2D sky is never fogged.
      const settings = group.settings || {}, blend = settings.blend || [], parameters = settings.parameters || {};
      const alpha = group.alpha === undefined ? 1 : group.alpha;
      // Additive surfaces fog toward black (BaseVSShader.cpp FogToBlack on additive passes); $nofog: none.
      const fogTo = !['0', '', undefined].includes(parameters['$nofog']) ? -1 : blend[1] === 'gl_one' ? 0 : 1;
      return new THREE.ShaderMaterial({
        transparent: !!blend.length || alpha < 1, depthWrite: !blend.length && alpha >= 1,
        side: settings.cull === 'none' ? THREE.DoubleSide : THREE.FrontSide,
        blending: blend[1] === 'gl_one' ? THREE.AdditiveBlending : THREE.NormalBlending,
        uniforms: {surface: {value: texture}, lights: {value: lightMap},
          unlit: {value: !!settings.unlit || (group.kind === 'model' && !this.vertexLight)}, vertexLight: {value: group.kind === 'model' && this.vertexLight},
          selfillum: {value: !!settings.selfillum}, cutout: {value: !!settings.alphaFunc},
          useAlpha: {value: !!blend.length || !!settings.alphaFunc}, alpha: {value: alpha},
          tint: {value: new THREE.Vector3(...(group.tint || [1, 1, 1]))}, brightness: {value: this.brightness},
          fogColor: fog.fogColor, fogRange: fog.fogRange, fogTo: {value: fogTo}, seconds: time, pan: {value: pan(parameters)}},
        vertexShader: `attribute vec2 uv2; ${this.vertexLight ? 'attribute vec4 color;' : ''} uniform vec2 pan; uniform float seconds;
        varying vec2 texUV, lightUV; varying vec3 vertexColour; varying float fogDepth;
        void main() { texUV = uv + fract(pan * seconds); lightUV = uv2; vertexColour = ${this.vertexLight ? 'color.rgb' : 'vec3(1.)'};
          vec4 view = modelViewMatrix * vec4(position, 1.); fogDepth = -view.z; gl_Position = projectionMatrix * view; }`,
        fragmentShader: `uniform sampler2D surface, lights;
        uniform bool unlit, vertexLight, selfillum, cutout, useAlpha; uniform float alpha, brightness, fogTo;
        uniform vec3 tint, fogColor, fogRange; varying vec2 texUV, lightUV; varying vec3 vertexColour; varying float fogDepth;
        void main() {
          vec4 base = texture2D(surface, texUV);
          if (cutout && base.a < .5) discard;
          vec3 illumination = unlit ? vec3(1.) : vertexLight ? vertexColour : texture2D(lights, lightUV).rgb;
          if (selfillum) illumination = mix(illumination, vec3(1.), base.a);
          vec3 rgb = min(base.rgb * illumination * tint * brightness, 1.);
          // Hypothesis: Bloodlines (2004, before Source's pixel fog) uses fixed-function linear fog on view depth, blended on
          // the gamma colour; Source's later pixel fog squares its factor to match it (common_ps_fxc.h BlendPixelFog).
          float f = min(fogRange.z, clamp((fogDepth - fogRange.x) / max(fogRange.y - fogRange.x, 1e-4), 0., 1.));
          if (fogTo >= 0.) rgb = mix(rgb, fogColor * fogTo, f);
          gl_FragColor = vec4(rgb, (useAlpha ? base.a : 1.) * alpha);
        }`
      });
    }
  };
})();

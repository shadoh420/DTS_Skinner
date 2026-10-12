/* Bloodlines adapter for the shared Quake-family free-flight viewer. */
'use strict';
window.skinnerMapViewerConfig = {
  label: 'Bloodlines', storageKey: 'skinner.vtmbmaps', debugName: 'skinnerVtmbMaps',
  games: [['vtmb', 'C:\\GOG Games\\VtMB']], dataRoot: '/vtmb-map-data/', importRoute: '/import_vtmb_maps',
  brightness: 1,
  configureTexture(texture) {
    texture.anisotropy = 4;
    texture.magFilter = THREE.LinearFilter;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
  },
  async load(map, loadTexture) {
    this.brightness = map.lighting.brightness;
    await window.quake2Maps.loadSky(map.skybox, loadTexture);
  },
  material(group, texture, lightMap) {
    if (group.kind === 'sky') return window.quake2Maps.material(group, texture, lightMap);
    const settings = group.settings || {}, blend = settings.blend || [];
    const alpha = group.alpha === undefined ? 1 : group.alpha;
    return new THREE.ShaderMaterial({
      transparent: !!blend.length || alpha < 1, depthWrite: !blend.length && alpha >= 1,
      side: settings.cull === 'none' ? THREE.DoubleSide : THREE.FrontSide,
      blending: blend[1] === 'gl_one' ? THREE.AdditiveBlending : THREE.NormalBlending,
      uniforms: {surface: {value: texture}, lights: {value: lightMap},
        unlit: {value: !!settings.unlit || group.kind === 'model'},
        selfillum: {value: !!settings.selfillum}, cutout: {value: !!settings.alphaFunc},
        useAlpha: {value: !!blend.length || !!settings.alphaFunc}, alpha: {value: alpha},
        tint: {value: new THREE.Vector3(...(group.tint || [1, 1, 1]))}, brightness: {value: this.brightness}},
      vertexShader: `attribute vec2 uv2; varying vec2 texUV, lightUV;
        void main() { texUV = uv; lightUV = uv2;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }`,
      fragmentShader: `uniform sampler2D surface, lights;
        uniform bool unlit, selfillum, cutout, useAlpha; uniform float alpha, brightness;
        uniform vec3 tint; varying vec2 texUV, lightUV;
        void main() {
          vec4 base = texture2D(surface, texUV);
          if (cutout && base.a < .5) discard;
          vec3 illumination = unlit ? vec3(1.) : texture2D(lights, lightUV).rgb;
          if (selfillum) illumination = mix(illumination, vec3(1.), base.a);
          gl_FragColor = vec4(base.rgb * illumination * tint * brightness, (useAlpha ? base.a : 1.) * alpha);
        }`
    });
  }
};

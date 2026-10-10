/* Anachronox build 46. Display gamma is applied after blending by viewer.js. */
'use strict';
window.anachronoxMaps = {
  fog: {density: 0, color: [0, 0, 0]},
  fogPasses: 2,
  async load(map, loadTexture) {
    this.fog = map.fog || {density: 0, color: [0, 0, 0]};
    this.fogPasses = (map.lighting || {}).fog_passes || 2;
    window.quake2Maps.time.value = 0; // The preview holds texture/sky motion at spawn.
    await window.quake2Maps.loadSky(map.skybox, loadTexture);
  },
  material(group, texture, lightMap) {
    const material = window.quake2Maps.material(group, texture, lightMap);
    if (group.kind === 'sky' || !texture) return material;
    const settings = group.settings || {}, flags = group.flags || 0;
    const banner = !!(flags & 0x10000), masked = !!(flags & 0x20000);
    const alphaFunc = settings.alphaFunc || (masked ? 'GT0' : '');
    material.transparent = masked || banner || !!group.texture_alpha || group.alpha < 1 || !!settings.blend;
    material.depthWrite = settings.depthWrite === undefined ? masked || !material.transparent : settings.depthWrite;
    if (settings.cull === 'none' || banner) material.side = THREE.DoubleSide;
    if (settings.blend) {
      material.blending = THREE.CustomBlending;
      const factors = {gl_one: THREE.OneFactor, gl_src_alpha: THREE.SrcAlphaFactor,
        gl_one_minus_src_alpha: THREE.OneMinusSrcAlphaFactor, gl_dst_color: THREE.DstColorFactor};
      material.blendSrc = factors[settings.blend[0]];
      material.blendDst = factors[settings.blend[1]];
    }
    Object.assign(material.uniforms, {
      fogDensity: {value: this.fog.density * 32}, fogColor: {value: new THREE.Vector3(...this.fog.color)},
      modelLight: {value: new THREE.Vector3(...(group.model_light || [1, 1, 1]))},
      model: {value: group.kind === 'model'}, useAlpha: {value: banner || !!group.texture_alpha || !!settings.blend || !!alphaFunc},
      twoPassFog: {value: this.fogPasses === 2 && !group.unlit && group.kind !== 'model'},
      alphaMode: {value: {GT0: 1, LT128: 2, GE128: 3}[alphaFunc] || 0}
    });
    material.vertexShader = 'varying float fogDepth;\n' + material.vertexShader.replace(
      'gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.);',
      'vec4 eye = modelViewMatrix * vec4(position,1.); fogDepth = abs(eye.z); gl_Position = projectionMatrix * eye;');
    material.fragmentShader = `
      uniform sampler2D surface, lights;
      uniform bool unlit, model, useAlpha, twoPassFog;
      uniform float alpha, fogDensity; uniform int alphaMode;
      uniform vec3 fogColor, modelLight;
      varying vec2 texUV, lightUV; varying float fogDepth;
      void main() {
        vec4 texel = texture2D(surface, texUV);
        if (alphaMode == 1 && texel.a <= 0.) discard;
        if (alphaMode == 2 && texel.a >= .5) discard;
        if (alphaMode == 3 && texel.a < .5) discard;
        vec3 light = model ? modelLight : unlit ? vec3(1.) : texture2D(lights, lightUV).rgb;
        vec3 color = texel.rgb * light;
        // GL_EXP (0x800), density in native units; fog precedes the display ramp.
        float f = exp(-fogDensity * fogDepth);
        color = twoPassFog ? mix(fogColor, texel.rgb, f) * mix(fogColor, light, f) : mix(fogColor, color, f);
        gl_FragColor = vec4(color, alpha * (useAlpha ? texel.a : 1.));
      }`;
    return material;
  }
};

/* SiN ref_gl: RGB lightmaps, local SWL palettes; page time drives warps, translation, frames and light styles. */
'use strict';
window.sinMaps = {
  intensity: 1,
  viewBlend(map, position) {
    const water = map && map.water;
    if (!water) return [0, 0, 0, 0];
    const [x, y, z] = [position.z * 32, position.x * 32, position.y * 32];
    let node = water.root;
    while (node >= 0) {
      const p = water.nodes[node];
      node = p[p[0] * x + p[1] * y + p[2] * z - p[3] >= 0 ? 4 : 5];
    }
    return node === -2 ? [...water.color, water.alpha] : [0, 0, 0, 0];
  },
  async load(map, loadTexture) {
    this.intensity = (map.lighting || {}).intensity || 1;
    await window.quake2Maps.loadSky(map.skybox, loadTexture);
  },
  material(group, texture, lightMap) {
    const settings = group.settings || {};
    if (settings.nofilter && texture) {
      texture = texture.clone();
      texture.magFilter = texture.minFilter = THREE.NearestFilter;
      texture.needsUpdate = true;
    }
    const material = window.quake2Maps.material(group, texture, lightMap);
    if (group.kind === 'sky' || !texture) return material;
    const alphaFunc = settings.alphaFunc || (settings.masked ? 'SIN_MASKED' : '');
    material.transparent = group.alpha < 1 || !!settings.blend;
    material.depthWrite = settings.depthWrite === undefined ? !material.transparent : settings.depthWrite;
    if (settings.blend === 'add') {
      material.blending = THREE.CustomBlending;
      material.blendSrc = material.blendDst = THREE.OneFactor;
    }
    if (settings.cull === 'none') material.side = THREE.DoubleSide;
    material.uniforms.flowing.value = false; // SiN conveyor bit 64 is not Q2 texture flow.
    // TRANSLATE surfaces scroll trans_mag map units a second at trans_angle (SDK docs/node/surfacecom.htm trans_mag,
    // trans_angle). Hypothesis: texture-space units and axes, the texture moving toward the angle.
    const [speed, angle] = settings.flowing && settings.trans || [0, 0], turn = angle * Math.PI / 180;
    material.uniforms.scroll = {value: new THREE.Vector2(speed * Math.cos(turn) / group.size[0], speed * Math.sin(turn) / group.size[1])};
    material.vertexShader = 'uniform vec2 scroll;\n' + material.vertexShader.replace('texUV = uv;', 'texUV = uv - scroll * seconds;');
    Object.assign(material.uniforms, {
      model: {value: group.kind === 'model'},
      modelLight: {value: new THREE.Vector3(...(group.model_light || [1, 1, 1]))},
      surfaceColor: {value: new THREE.Vector3(...(settings.color || [1, 1, 1]))},
      unlitLight: {value: settings.nonlit === undefined ? 1 / this.intensity : settings.nonlit},
      alphaMode: {value: {GT0: 1, LT128: 2, GE128: 3, SIN_MASKED: 4}[alphaFunc] || 0},
      useAlpha: {value: !!alphaFunc || material.transparent}
    });
    material.fragmentShader = `
      uniform sampler2D surface, lights; uniform bool unlit, model, useAlpha;
      uniform float alpha, unlitLight; uniform int alphaMode;
      uniform vec3 modelLight, surfaceColor; varying vec2 texUV, lightUV;
      ${window.quake2Maps.lightGLSL}
      void main() {
        vec4 texel = texture2D(surface, texUV);
        if (alphaMode == 1 && texel.a <= 0.) discard;
        if (alphaMode == 2 && texel.a >= .5) discard;
        if (alphaMode == 3 && texel.a < .5) discard;
        if (alphaMode == 4 && texel.a <= .666) discard; // ref_gl 1001446A: GL_GREATER, 0.666f.
        vec3 light = model ? modelLight : unlit ? vec3(unlitLight) : lightAt();
        gl_FragColor = vec4(texel.rgb * light * surfaceColor, alpha * (useAlpha ? texel.a : 1.));
      }`;
    return material;
  }
};

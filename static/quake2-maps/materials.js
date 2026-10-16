/* ref_gl 3.20: intensity=2 upload, RGB lightmaps at modulate=1, display gamma=1. */
'use strict';
window.quake2Maps = {
  time: {value: 0},
  sky: {},
  styled: [],  // {uniform, pattern, scale}: animated light styles of this page's materials.
  // A face's animated style is a raw sample `styleOffset` below its tile; gl_rsurf.c R_BuildLightMap adds sample * value,
  // then scales all channels by 255 / max when one exceeds 255.
  lightGLSL: `uniform float styleA, styleOffset;
    vec3 lightAt() {
      vec3 c = texture2D(lights,lightUV).rgb;
    #ifdef STYLED
      c += texture2D(lights, lightUV + vec2(0., styleOffset)).rgb * styleA;
      c /= max(1., max(c.r, max(c.g, c.b)));
    #endif
      return c;
    }`,
  // Patterns step at 10 Hz (cl_fx.c CL_RunLightStyles: cl.time / 100); value = (char - 'a') * the pack's scale.
  update(seconds) {
    const step = Math.floor(seconds * 10);
    for (const s of this.styled) s.uniform.value = (s.pattern.charCodeAt(step % s.pattern.length) - 97) * s.scale;
  },
  addStyles(material, names, styles) {
    const lights = styles && styles.patterns;
    if (!lights || !names.length) return;
    material.defines = {...material.defines, STYLED: ''};
    material.uniforms.styleOffset.value = styles.offset;
    names.forEach((style, i) => lights[style] && this.styled.push({uniform: material.uniforms[i ? 'styleB' : 'styleA'], pattern: lights[style], scale: styles.scale}));
  },
  patchMaterial(material, group, map) {
    if (material.userData && material.userData.quake2Lit && group.styles) this.addStyles(material, group.styles, map.light_styles);
  },
  async loadSky(sky, loadTexture) {
    this.sky = {rate: {value: sky.rotate}, axis: {value: new THREE.Vector3(...sky.axis).normalize()}};
    for (const suffix of ['rt', 'lf', 'bk', 'ft', 'up', 'dn']) {
      const texture = await loadTexture(sky.faces[suffix].texture);
      if (texture) {
        texture.wrapS = texture.wrapT = THREE.ClampToEdgeWrapping;
        texture.minFilter = texture.magFilter = THREE.LinearFilter;
        texture.generateMipmaps = false;
      }
      this.sky[suffix] = {value: texture};
    }
  },
  material(group, texture, lightMap) {
    if (group.kind === 'sky') return new THREE.ShaderMaterial({
      uniforms: {...this.sky, seconds: this.time},
      vertexShader: `varying vec3 world; void main() {
        vec4 p = modelMatrix * vec4(position, 1.); world = p.xyz;
        gl_Position = projectionMatrix * viewMatrix * p;
      }`,
      fragmentShader: `
        uniform sampler2D rt, lf, bk, ft, up, dn;
        uniform float seconds, rate; uniform vec3 axis; varying vec3 world;
        vec2 skyUV(vec2 st) {
          vec2 uv = (st + 1.) * .5; uv.y = 1. - uv.y;
          float edge = rate == 0. ? 1./512. : 1./256.;
          return clamp(uv, vec2(edge), vec2(1. - edge));
        }
        void main() {
          vec3 r = (world - cameraPosition).zxy; // Back to native x/y/z.
          float a = radians(-seconds * rate), c = cos(a), s = sin(a);
          r = r*c + cross(axis,r)*s + axis*dot(axis,r)*(1.-c);
          vec3 d = abs(r); vec4 color;
          // gl_warp.c vec_to_st and skytexorder: +X rt, -X lf, +Y bk, -Y ft, +Z up, -Z dn.
          if (d.x >= d.y && d.x >= d.z) {
            if (r.x > 0.) color = texture2D(rt, skyUV(vec2(-r.y,r.z)/d.x));
            else color = texture2D(lf, skyUV(vec2(r.y,r.z)/d.x));
          } else if (d.y >= d.z) {
            if (r.y > 0.) color = texture2D(bk, skyUV(vec2(r.x,r.z)/d.y));
            else color = texture2D(ft, skyUV(vec2(-r.x,r.z)/d.y));
          } else {
            if (r.z > 0.) color = texture2D(up, skyUV(vec2(-r.y,-r.x)/d.z));
            else color = texture2D(dn, skyUV(vec2(-r.y,r.x)/d.z));
          }
          gl_FragColor = vec4(color.rgb,1.);
        }`
    });
    if (!texture) return new THREE.MeshBasicMaterial({color: 0xff00ff});
    return new THREE.ShaderMaterial({
      transparent: group.alpha < 1, depthWrite: group.alpha === 1, userData: {quake2Lit: true},
      uniforms: {surface: {value: texture}, lights: {value: lightMap}, seconds: this.time, styleA: {value: 0}, styleOffset: {value: 0},
        size: {value: new THREE.Vector2(...group.size)}, warp: {value: group.kind === 'turbulent'},
        flowing: {value: !!(group.flags & 64)}, unlit: {value: group.unlit}, alpha: {value: group.alpha},
        shade: {value: group.shade ?? 1}, glow: {value: !!group.glow}},
      vertexShader: `
        attribute vec2 uv2; uniform vec2 size; uniform float seconds; uniform bool warp, flowing;
        varying vec2 texUV, lightUV;
        // Ref_gl indexes a 256-entry sine table (amplitude 8), once per subdivided warp vertex.
        vec2 turb(vec2 phase) {
          vec2 index = phase * (256./6.28318530718);
          index = sign(index) * floor(abs(index));
          return sin(mod(index,256.) * (6.28318530718/256.)) * 8.;
        }
        void main() {
          lightUV = uv2; vec2 st = uv * size;
          if (warp) {
            texUV = (st + turb(st.yx * .125 + seconds)) / 64.;
            if (flowing) texUV.x -= fract(seconds * .5);
          } else {
            texUV = uv;
            if (flowing) { float scroll = -64. * fract(seconds/40.); if (scroll == 0.) scroll = -64.; texUV.x += scroll/size.x; }
          }
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.);
        }`,
      fragmentShader: `
        uniform sampler2D surface, lights; uniform bool unlit, glow; uniform float alpha, shade, seconds;
        varying vec2 texUV, lightUV;
        ${this.lightGLSL}
        void main() {
          // Unlit alpha/warp polys use glColor(inverse_intensity); ordinary surfaces multiply RGB lightmaps once.
          vec3 illumination = unlit ? vec3(.5) : lightAt();
          // Placed models read the light below them; items (RF_GLOW) pulse by 0.1 at 7 rad/s, never under 0.8 of it,
          // and the shade dots scale it per vertex (gl_mesh.c R_DrawAliasModel; shade is their mean).
          if (glow) illumination = max(illumination + .1 * sin(seconds * 7.), illumination * .8);
          illumination *= shade;  // (its own line: the Daikatana adapter replaces the next one)
          gl_FragColor = vec4(texture2D(surface,texUV).rgb * illumination,alpha);
        }`
    });
  }
};

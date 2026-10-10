/* ref_gl 3.20: intensity=2 upload, RGB lightmaps at modulate=1, display gamma=1. */
'use strict';
window.quake2Maps = {
  time: {value: 0},
  sky: {},
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
      transparent: group.alpha < 1, depthWrite: group.alpha === 1,
      uniforms: {surface: {value: texture}, lights: {value: lightMap}, seconds: this.time,
        size: {value: new THREE.Vector2(...group.size)}, warp: {value: group.kind === 'turbulent'},
        flowing: {value: !!(group.flags & 64)}, unlit: {value: group.unlit}, alpha: {value: group.alpha}},
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
        uniform sampler2D surface, lights; uniform bool unlit; uniform float alpha;
        varying vec2 texUV, lightUV;
        void main() {
          // Unlit alpha/warp polys use glColor(inverse_intensity); ordinary surfaces multiply RGB lightmaps once.
          vec3 illumination = unlit ? vec3(.5) : texture2D(lights,lightUV).rgb;
          gl_FragColor = vec4(texture2D(surface,texUV).rgb * illumination,alpha);
        }`
    });
  }
};

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

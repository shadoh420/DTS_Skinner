/* Quake indexed materials: original software colormap, or rerelease RGB lightmaps with fullbright bypass. */
'use strict';
window.quakeMaps = {
  time: {value: 0},
  material(group, texture, lightMap, colormap, rgbLighting, skybox) {
    if (group.kind === 'sky' && skybox) return window.quake2Maps.material(group, texture, lightMap);
    if (!texture || !colormap) return new THREE.MeshBasicMaterial({color: 0xff00ff});
    return new THREE.ShaderMaterial({
      uniforms: {indices: {value: texture}, lights: {value: lightMap}, colors: {value: colormap},
        seconds: this.time, size: {value: new THREE.Vector2(...group.size)},
        rgbLighting: {value: !!rgbLighting},
        kind: {value: group.kind === 'sky' ? 1 : group.kind === 'turbulent' ? 2 : 0}},
      vertexShader: `
        attribute vec2 uv2;
        varying vec2 texUV, lightUV;
        varying vec3 world;
        void main() {
          texUV = uv; lightUV = uv2;
          vec4 p = modelMatrix * vec4(position, 1.);
          world = p.xyz;
          gl_Position = projectionMatrix * viewMatrix * p;
        }`,
      fragmentShader: `
        uniform sampler2D indices, lights, colors;
        uniform float seconds;
        uniform vec2 size;
        uniform int kind;
        uniform bool rgbLighting;
        varying vec2 texUV, lightUV;
        varying vec3 world;
        // Manual repeat keeps NPOT index images intact on WebGL1: automatic resizing would invent indices.
        float pixel(vec2 uv) { return floor(texture2D(indices, fract(uv)).r * 255. + .5); }
        vec4 color(float index, float row) {
          return texture2D(colors, vec2((index + .5) / 256., (index >= 224. ? 32.5 : row + .5) / 64.));
        }
        void main() {
          if (kind == 1) {
            vec3 ray = world - cameraPosition;
            ray.y *= 3.;
            vec2 st = ray.zx * (6. * 63. / max(length(ray), .001));
            vec2 back = fract((st + seconds * 8.) / 128.);
            vec2 front = fract((st + seconds * 16.) / 128.);
            float a = pixel(vec2(front.x * .5, front.y));
            float b = pixel(vec2(.5 + back.x * .5, back.y));
            gl_FragColor = color(a == 0. ? b : a, 32.);
          } else if (kind == 2) {
            vec2 st = texUV * size;
            vec2 warp = sin(st.yx * .125 + seconds) * 8.;
            gl_FragColor = color(pixel((st + warp) / size), 32.);
          } else if (rgbLighting) {
            float index = pixel(texUV);
            vec3 base = color(index, 32.).rgb;
            vec3 illumination = texture2D(lights, lightUV).rgb * (255. / 128.);
            gl_FragColor = vec4(index >= 224. ? base : min(base * illumination, vec3(1.)), 1.);
          } else {
            // RG stores a 16-bit fixed-point colormap coordinate. Bilinear sampling interpolates luxels
            // before the high byte selects a row, just as software Quake interpolates blocklights.
            float code = dot(texture2D(lights, lightUV).rg, vec2(65280., 255.));
            gl_FragColor = color(pixel(texUV), clamp(floor(code / 256.), 0., 63.));
          }
        }`
    });
  }
};

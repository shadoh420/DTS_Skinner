/* Daikatana's still frame: BSP41 lightmaps, indexed cutouts and linear world fog. */
'use strict';
window.daikatanaMaps = {
  async load(map, loadTexture) {
    await window.quake2Maps.loadSky(map.skybox, loadTexture);
    this.fog = map.fog;
    this.clouds = map.skybox.clouds.layers;
    this.cloudTexture = this.clouds.length ? await loadTexture(this.clouds[0].texture) : null;
    if (this.cloudTexture) {
      this.cloudTexture.minFilter = this.cloudTexture.magFilter = THREE.LinearFilter;
      this.cloudTexture.generateMipmaps = false; // it_sky, like the cube faces.
    }
    window.quake2Maps.time.value = 0;
  },
  material(group, texture, lightMap) {
    const material = window.quake2Maps.material(group, texture, lightMap);
    if (!material.isShaderMaterial) return material;
    const sky = group.kind === 'sky', alias = group.kind === 'model';
    const maskedWall = !!(group.flags & 0x80000) && !alias && !group.kind.startsWith('sprite');
    const lightmapped = !sky && !alias && !group.kind.startsWith('sprite') && !group.unlit && !maskedWall;
    // Retail fallback: no SGIS multitexture means masked surfaces skip the
    // lightmap queue (10017E9B). Also handles packs made before this correction.
    if (maskedWall) material.uniforms.unlit.value = true;
    const fog = this.fog;
    Object.assign(material.uniforms, {
      fogOn: {value: fog.enabled}, fogRGB: {value: new THREE.Vector3(...fog.color)},
      fogStart: {value: fog.start / 32}, fogEnd: {value: (sky ? fog.sky_end : fog.end) / 32},
      alias: {value: alias}, masked: {value: !!(group.flags & 0x80000) || alias},
      cloud: {value: this.cloudTexture}, cloudsOn: {value: !!this.cloudTexture},
      cloudTile: {value: new THREE.Vector2(...(this.clouds.length ? this.clouds.map(c => c.tile) : [1, 1]))},
      cloudAlpha: {value: this.clouds.length ? this.clouds[1].alpha : 0}
    });
    material.vertexShader = 'varying float fogDepth; varying vec4 aliasLight; attribute vec4 color;\n' + material.vertexShader;
    material.vertexShader = material.vertexShader.replace('void main() {', 'void main() {\n' +
      'fogDepth = -(modelViewMatrix * vec4(position,1.)).z; aliasLight = color;');
    if (group.kind === 'sprite') {
      material.uniforms.spriteSize = {value: new THREE.Vector2(...group.sprite_size)};
      material.uniforms.spritePivot = {value: new THREE.Vector2(...group.sprite_pivot)};
      material.vertexShader = 'uniform vec2 spriteSize, spritePivot;\n' + material.vertexShader;
      material.vertexShader = material.vertexShader.replace('gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.);',
        'vec4 p = modelViewMatrix * vec4(position,1.); p.xy += vec2(uv.x, 1.-uv.y) * spriteSize - spritePivot; gl_Position = projectionMatrix * p;');
    }
    if (group.kind === 'sprite' || group.kind === 'sprite_oriented') {
      material.transparent = true;
      material.depthWrite = false;
      material.side = THREE.DoubleSide;
      material.blending = group.additive ? THREE.AdditiveBlending : THREE.NormalBlending;
    }
    material.fragmentShader = `varying float fogDepth; varying vec4 aliasLight;
      uniform bool fogOn, alias, masked, cloudsOn; uniform vec3 fogRGB;
      uniform float fogStart, fogEnd, cloudAlpha; uniform vec2 cloudTile; uniform sampler2D cloud;
    ` + material.fragmentShader;
    if (sky) {
      // ref_gl 1001D680 builds a 20 x 20 arch, x/y +-8192 and
      // z=2048*sin(i*pi/19)*sin(j*pi/19). Intersect its triangulated height field.
      material.fragmentShader = `
        float cloudHeight(vec2 xy) {
          vec2 g = clamp((xy / 16384. + .5) * 19., 0., 18.99999);
          vec2 i = floor(g), f = fract(g);
          vec2 a = sin(i * (3.14159265359/19.));
          vec2 b = sin((i+1.) * (3.14159265359/19.));
          float h00=a.x*a.y, h10=b.x*a.y, h01=a.x*b.y, h11=b.x*b.y;
          return 2048. * (f.x+f.y < 1. ? h00+(h10-h00)*f.x+(h01-h00)*f.y
            : h11+(h01-h11)*(1.-f.x)+(h10-h11)*(1.-f.y));
        }
        vec3 cloudPosition(vec3 direction) {
          vec3 d = normalize(direction);
          float lo=0., hi=min(8192./max(max(abs(d.x),abs(d.y)),.00001), 2048./max(d.z,.00001));
          for (int i=0; i<18; ++i) {
            float t=(lo+hi)*.5;
            if (d.z*t < cloudHeight(d.xy*t)) lo=t; else hi=t;
          }
          return d*((lo+hi)*.5);
        }
      ` + material.fragmentShader;
      material.fragmentShader = material.fragmentShader.replace('gl_FragColor = vec4(color.rgb,1.);', `
        vec3 ray = normalize(r);
        float eyeProjection = fogDepth / length(world - cameraPosition);
        dkFogDepth = eyeProjection * (4096./32.) / max(max(abs(ray.x),abs(ray.y)),abs(ray.z));
        if (cloudsOn && r.z > 0.) {
          vec3 cloudPoint = cloudPosition(r);
          dkFogDepth = eyeProjection * length(cloudPoint) / 32.;
          vec2 cloudUV = cloudPoint.xy / 16384.;
          vec4 first = texture2D(cloud, cloudUV * cloudTile.x);
          vec4 second = texture2D(cloud, cloudUV * cloudTile.y);
          color.rgb = first.rgb;
          color.rgb = mix(color.rgb, second.rgb, second.a * cloudAlpha);
        }
        gl_FragColor = vec4(color.rgb,1.);`);
    } else {
      material.fragmentShader = material.fragmentShader.replace('unlit ? vec3(.5)', 'alias ? aliasLight.rgb : unlit ? vec3(1.)');
      material.fragmentShader = material.fragmentShader.replace('gl_FragColor = vec4(texture2D(surface,texUV).rgb * illumination,alpha);', `
        vec4 sampleColor = texture2D(surface,texUV);
        if (masked && sampleColor.a <= 0.) discard;
        gl_FragColor = vec4(sampleColor.rgb * illumination, alpha * sampleColor.a);
        ${lightmapped ? `
        // Texture pass and ZERO,SRC_COLOR lightmap pass both have GL_FOG.
        // R_BlendLightmaps 100178AF / 100178FB; retail SGIS fallback profile.
        if (fogOn) {
          float f = clamp((fogEnd - dkFogDepth) / max(.001, fogEnd - fogStart), 0., 1.);
          gl_FragColor.rgb = mix(fogRGB, sampleColor.rgb, f) * mix(fogRGB, illumination, f);
        }` : ''}`);
    }
    material.fragmentShader = material.fragmentShader.replace('void main() {', 'void main() { float dkFogDepth = fogDepth;');
    if (!lightmapped) material.fragmentShader = material.fragmentShader.replace(/}\s*$/, `
        if (fogOn) gl_FragColor.rgb = mix(fogRGB, gl_FragColor.rgb,
          clamp((fogEnd - dkFogDepth) / max(.001, fogEnd - fogStart), 0., 1.));
      }`);
    if (group.flags & 0x40000) {
      material.transparent = true;
      material.depthWrite = false;
      material.blending = THREE.NormalBlending;
    }
    if (maskedWall) {
      // 1001876B enables alpha test AND blending, while keeping depth writes.
      material.transparent = true;
      material.depthWrite = true;
      material.blending = THREE.NormalBlending;
    }
    return material;
  }
};

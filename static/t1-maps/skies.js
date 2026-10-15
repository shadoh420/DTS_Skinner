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

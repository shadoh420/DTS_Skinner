/* UE1 extras for the Unreal/UT and Deus Ex Maps pages (tools/import_unreal_map.py): volumetric zone fog and the
   changing lights, through the viewer's window.skinnerMapExtras hooks. Other pages do not load it; there it does nothing. */
'use strict';
(() => {
  const FOGS = 16;  // Fog lights a fragment adds up; a map with more takes the 16 nearest the eye each frame.
  // Poly flags without fog: translucent, modulated, fake backdrop, unlit (SurrealEngine: no fogmap on unlit surfaces,
  // no fog colour on translucent or modulated draws, D3D11RenderDevice.cpp PF_RenderFog test).
  const NO_FOG = 0x4 | 0x40 | 0x80 | 0x400000;
  const on = () => !window.skinnerMapViewerConfig && ['unreal', 'dx'].includes(document.body.dataset.game || 'unreal');
  const fogBall = {value: Array.from({length: FOGS}, () => new THREE.Vector4())};
  const fogTint = {value: Array.from({length: FOGS}, () => new THREE.Vector4())};
  const fogCount = {value: 0}, fogId = {value: new Int32Array(FOGS)};  // fogId: each picked light's place in fog_lights
  const animValue = {value: null}, animId = {value: null}, animLight = {value: null};
  let fogs = null, order = null, near = null, lights = null, bytes = null, flicker = -1;

  // SurrealEngine FogmapBuilder::SphereDensity (Inigo Quilez's analytic integral of a quadratically falling density
  // in a sphere) along the eye ray to the surface, and FogmapBuilder::AddLight's blend of each light; the surface then
  // takes fog.rgb + colour x (1 - fog.a) (SurrealEngine's surface shader, GLFileResource.cpp "Fog map"). Only the
  // lights of the surface's group.fog_mask count (hypothesis, measured on NaliC2: see fog_bit in the importer).
  const FOG_PARS = `uniform vec4 ue1FogBall[${FOGS}], ue1FogTint[${FOGS}]; uniform int ue1FogCount, ue1FogId[${FOGS}], ue1FogMask; varying vec3 ue1World;
vec3 ue1Fog(vec3 colour) {
  vec3 ray = ue1World - cameraPosition; float depth = length(ray); ray /= depth; vec4 fog = vec4(0.);
  for (int i = 0; i < ${FOGS}; i++) {
    if (i >= ue1FogCount) break;
    if (((ue1FogMask >> ue1FogId[i]) & 1) == 0) continue;
    float r = 1. / ue1FogBall[i].w; vec3 rc = (cameraPosition - ue1FogBall[i].xyz) * r;
    float b = dot(ray, rc), c = dot(rc, rc) - 1., h = b * b - c;
    if (h < 0.) continue;
    h = sqrt(h);
    float t1 = max(-b - h, 0.), t2 = min(-b + h, depth * r);
    if (t2 <= t1) continue;
    float amount = (c * t1 + b * t1 * t1 + t1 * t1 * t1 / 3. - c * t2 - b * t2 * t2 - t2 * t2 * t2 / 3.) * .75;
    float alpha = min(amount * ue1FogTint[i].w, 1.);
    fog = vec4(ue1FogTint[i].rgb * amount + fog.rgb * (1. - alpha), min(fog.a + alpha, 1.));
  }
  return fog.rgb + colour * (1. - fog.a);
}
`;
  // The changing lights' layer: up to three a texel, each its shadow x falloff / 2 (lightanim.png) times its colour
  // now (ue1AnimLight, texel id - 1), each at most 1 as the importer's lightmap() adds lights.
  const ANIM_PARS = `uniform sampler2D ue1AnimValue, ue1AnimId, ue1AnimLight;
vec3 ue1Anim(vec2 at) {
  vec3 v = texture2D(ue1AnimValue, at).rgb; ivec3 n = ivec3(texture2D(ue1AnimId, at).rgb * 255. + .5); vec3 sum = vec3(0.);
  if (n.r > 0) sum += min(v.r * texelFetch(ue1AnimLight, ivec2(n.r - 1, 0), 0).rgb, 1.);
  if (n.g > 0) sum += min(v.g * texelFetch(ue1AnimLight, ivec2(n.g - 1, 0), 0).rgb, 1.);
  if (n.b > 0) sum += min(v.b * texelFetch(ue1AnimLight, ivec2(n.b - 1, 0), 0).rgb, 1.);
  return sum;
}
`;
  const layer = (loader, url, nearest) => loader.loadAsync(url).then(texture => {
    texture.flipY = false; texture.generateMipmaps = false;
    texture.minFilter = texture.magFilter = nearest ? THREE.NearestFilter : THREE.LinearFilter;
    return texture;
  });

  (window.skinnerMapExtras = window.skinnerMapExtras || []).push({
    async load(map, ctx) {
      fogs = lights = null;
      if (!on()) return;
      if (map.fog_lights?.length) {
        fogs = map.fog_lights;
        order = new Int32Array(fogs.length); near = new Float32Array(fogs.length);
      }
      if (map.light_anim?.lights?.length && ctx.lightMap) {
        try {
          [animValue.value, animId.value] = await Promise.all([layer(ctx.loader, `${ctx.data}maps/${map.id}/lightanim.png`, false),
            layer(ctx.loader, `${ctx.data}maps/${map.id}/lightanim-ids.png`, true)]);
          lights = map.light_anim.lights;
          bytes = new Uint8Array(lights.length * 4).fill(255);
          animLight.value = new THREE.DataTexture(bytes, lights.length, 1);
        } catch (_) { lights = null; }  // The layer missing: the lights stay at their average.
      }
    },
    patchMaterial(material, group) {
      const fog = fogs && group.fog_mask && !(group.flags & NO_FOG), anim = lights && group.light_anim && material.lightMap;
      if (!fog && !anim) return;
      material.onBeforeCompile = shader => {
        if (fog) {
          Object.assign(shader.uniforms, {ue1FogBall: fogBall, ue1FogTint: fogTint, ue1FogCount: fogCount, ue1FogId: fogId,
            ue1FogMask: {value: group.fog_mask}});
          shader.vertexShader = 'varying vec3 ue1World;\n' + shader.vertexShader.replace('#include <project_vertex>',
            '#include <project_vertex>\nue1World = (modelMatrix * vec4(transformed, 1.)).xyz;');
          shader.fragmentShader = FOG_PARS + shader.fragmentShader.replace('#include <output_fragment>',
            'outgoingLight = ue1Fog(outgoingLight);\n#include <output_fragment>');
        }
        if (anim) {
          Object.assign(shader.uniforms, {ue1AnimValue: animValue, ue1AnimId: animId, ue1AnimLight: animLight});
          shader.fragmentShader = ANIM_PARS + shader.fragmentShader.replace('vec4 lightMapTexel = texture2D( lightMap, vUv2 );',
            'vec4 lightMapTexel = texture2D( lightMap, vUv2 ); lightMapTexel.rgb = min(lightMapTexel.rgb + ue1Anim(vUv2), 1.);');
        }
      };
      material.customProgramCacheKey = () => 'ue1' + (fog ? 'f' : '') + (anim ? 'a' : '');
    },
    update(seconds, camera, map, region) {
      if (fogs) {
        // The eye's zone decides (fog_lights in tools/import_unreal_map.py); the nearest 16 by distance to the sphere.
        const count = region?.fog ? Math.min(fogs.length, FOGS) : 0, p = camera.position;
        if (count) {
          for (let i = 0; i < fogs.length; i++) {
            const f = fogs[i];
            order[i] = i; near[i] = Math.hypot(f[0] - p.x, f[1] - p.y, f[2] - p.z) - f[3];
          }
          if (fogs.length > FOGS) order.sort((a, b) => near[a] - near[b]);
          for (let k = 0; k < count; k++) {
            const f = fogs[order[k]];
            fogBall.value[k].set(f[0], f[1], f[2], f[3]); fogTint.value[k].set(f[4], f[5], f[6], f[7]); fogId.value[k] = order[k];
          }
        }
        fogCount.value = count;
      }
      if (lights) {
        // SurrealEngine LightmapBuilder::GetLightColor: pulse 0.65 + 0.35 sin, subtle pulse 0.8 + 0.2 sin, of
        // 2 pi (LightPhase / 255 + seconds x 40 / LightPeriod); blink on while that mod 2 < 1; strobe while
        // seconds x 10 mod 2 < 1; flicker on or off at random, re-picked 25 times a second (SurrealEngine's cap,
        // LightSystem::BeginFrame; the original picked every frame).
        const tick = Math.floor(seconds * 25), roll = tick !== flicker;
        flicker = tick;
        for (let i = 0; i < lights.length; i++) {
          const light = lights[i], type = light[0], turns = light[2] / 255 + seconds * 40 / Math.max(light[1], 1);
          let f = type === 2 ? .65 + .35 * Math.sin(turns * 2 * Math.PI) : type === 7 ? .8 + .2 * Math.sin(turns * 2 * Math.PI)
            : type === 3 ? +(turns % 2 < 1) : type === 5 ? +(seconds * 10 % 2 < 1) : 1;
          if (type === 4) { if (roll) bytes[i * 4 + 3] = Math.random() < .5 ? 255 : 0; f = bytes[i * 4 + 3] / 255; }
          bytes[i * 4] = Math.round(255 * Math.min(light[3] * f, 1)); bytes[i * 4 + 1] = Math.round(255 * Math.min(light[4] * f, 1));
          bytes[i * 4 + 2] = Math.round(255 * Math.min(light[5] * f, 1));
        }
        animLight.value.needsUpdate = true;
      }
    },
  });
})();

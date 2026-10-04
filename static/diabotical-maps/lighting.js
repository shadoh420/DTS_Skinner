/* The game's lighting for the Diabotical map page (tile.cs.cso, measured in runs 16 to 18; the importer's read_lights
   turns the entities into light on a white surface): ambient (shadow ambient where the sun is shadowed) replaced
   where ambient nodes reach by a 3D grid built here, as the game builds one when a map loads; the sun, times the
   shadow colour in its shadow; and point, spot and capsule lights, unshadowed, found per 256-unit cell. The page
   multiplies that light by the texture's bytes, as the game does. Node runs it too (tests/diabotical_maps.test.cjs). */
(function (exports) {
  'use strict';
  const CELL = 256, WIDTH = 1024, MAX_PER_CELL = 64;

  /* A node's share of the ambient and its colour, d its distance over its radius: a sphere's share fades as a
     smoothstep to its radius and its colour faster, as (1 - d / 0.74)^2.4 (run 18's floor under nodes of radius 200
     and 300); a cubic node's (d its largest axis distance) is whole to 0.875 and gone at 1.55 (run 18). */
  const smooth = t => t * t * (3 - 2 * t), clamp = t => Math.min(1, Math.max(0, t));
  function nodeWeights(cubic, d) {
    if (cubic) { const w = smooth(clamp((1.55 - d) / .675)); return [w, w]; }
    return [smooth(clamp(1 - d)), clamp(1 - d / .74) ** 2.4];
  }

  /* The ambient grid over the nodes' reach, at most `budget` texels: RGBA floats, rgb the nodes' colour added up, a
     their combined share (1 - the product of what each leaves). Empty texels border it, so outside reads none. */
  function buildGrid(nodes, budget = 1 << 20) {
    if (!nodes.length) return null;
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (const [cubic, x, y, z, radius] of nodes) {
      const reach = radius * (cubic ? 1.55 : 1);
      [x, y, z].forEach((v, k) => { min[k] = Math.min(min[k], v - reach); max[k] = Math.max(max[k], v + reach); });
    }
    const cell = Math.max(16, Math.cbrt((max[0] - min[0]) * (max[1] - min[1]) * (max[2] - min[2]) / budget));
    const size = [0, 1, 2].map(k => Math.ceil((max[k] - min[k]) / cell) + 3);
    for (let k = 0; k < 3; k++) min[k] -= cell * 1.5;  // Texel centres at min + (i + 0.5) cell.
    for (let k = 0; k < 3; k++) max[k] = min[k] + size[k] * cell;
    const data = new Float32Array(size[0] * size[1] * size[2] * 4);
    for (let i = 3; i < data.length; i += 4) data[i] = 1;  // What the nodes leave of the ambient, made a share below.
    for (const [cubic, x, y, z, radius, r, g, b] of nodes) {
      const reach = radius * (cubic ? 1.55 : 1), centre = [x, y, z];
      const low = centre.map((v, k) => Math.max(0, Math.floor((v - reach - min[k]) / cell - .5)));
      const high = centre.map((v, k) => Math.min(size[k] - 1, Math.ceil((v + reach - min[k]) / cell - .5)));
      for (let k = low[2]; k <= high[2]; k++) {
        const dz = min[2] + (k + .5) * cell - z;
        for (let j = low[1]; j <= high[1]; j++) {
          const dy = min[1] + (j + .5) * cell - y;
          for (let i = low[0]; i <= high[0]; i++) {
            const dx = min[0] + (i + .5) * cell - x;
            const d = (cubic ? Math.max(Math.abs(dx), Math.abs(dy), Math.abs(dz)) : Math.hypot(dx, dy, dz)) / radius;
            const [share, colour] = nodeWeights(cubic, d);
            if (!share && !colour) continue;
            const at = ((k * size[1] + j) * size[0] + i) * 4;
            data[at] += r * colour; data[at + 1] += g * colour; data[at + 2] += b * colour; data[at + 3] *= 1 - share;
          }
        }
      }
    }
    for (let i = 3; i < data.length; i += 4) data[i] = 1 - data[i];
    return {data, size, min, max};
  }

  // Where a light reaches: its bounds (a capsule's segment, grown by the radius).
  function reach([kind, x, y, z, dx, dy, dz, , , , radius, , , , length]) {
    const ends = kind === 2 ? [[x, y, z], [x + dx * length, y + dy * length, z + dz * length]] : [[x, y, z]];
    return [0, 1, 2].map(k => [Math.min(...ends.map(e => e[k])) - radius, Math.max(...ends.map(e => e[k])) + radius]);
  }

  /* The lights as rows of 16 floats (4 texels: position, radius; colour, kind; direction, inner radius; cos of the
     half cone, softness, length, specular scale) and, per 256-unit cell over their reach, the list of those that
     reach it (up to 64): cells as (offset, count) pairs and the lists end to end, each laid out WIDTH texels a row. */
  function buildLights(lights) {
    const rows = new Float32Array(Math.max(1, lights.length) * 16);
    lights.forEach(([kind, x, y, z, dx, dy, dz, r, g, b, radius, inner, cone, softness, length, specular = 0], i) =>
      rows.set([x, y, z, radius, r, g, b, kind, dx, dy, dz, inner, cone, softness, length, specular], i * 16));
    if (!lights.length) return {rows, count: 0, min: [0, 0, 0], size: [0, 0, 0], cells: new Float32Array(4), lists: new Float32Array(1)};
    const bounds = lights.map(reach), min = [0, 1, 2].map(k => Math.min(...bounds.map(b => b[k][0])));
    const size = [0, 1, 2].map(k => Math.max(1, Math.ceil((Math.max(...bounds.map(b => b[k][1])) - min[k]) / CELL)));
    const lists = Array.from({length: size[0] * size[1] * size[2]}, () => []);
    bounds.forEach((b, light) => {
      const [low, high] = [0, 1].map(end => b.map(([a, c], k) => Math.min(size[k] - 1, Math.max(0, Math.floor(((end ? c : a) - min[k]) / CELL)))));
      for (let k = low[2]; k <= high[2]; k++) for (let j = low[1]; j <= high[1]; j++) for (let i = low[0]; i <= high[0]; i++) {
        const list = lists[(k * size[1] + j) * size[0] + i];
        if (list.length < MAX_PER_CELL) list.push(light);  // ponytail: a 65th light in a cell is dropped there.
      }
    });
    const total = lists.reduce((sum, list) => sum + list.length, 0);
    const cells = new Float32Array(Math.ceil(lists.length / WIDTH) * WIDTH * 2), flat = new Float32Array(Math.max(1, Math.ceil(total / WIDTH)) * WIDTH);
    let at = 0;
    lists.forEach((list, cell) => { cells.set([at, list.length], cell * 2); flat.set(list, at); at += list.length; });
    return {rows, count: lights.length, min, size, cells, lists: flat};
  }

  /* What tile.cs makes of a material id (its map 3's red, else its material_id): [reflection, ambient, tint by
     albedo]. Reflection is the share of the albedo taken as F0 and, above 0, the envmap reflected (at 0.5 tinted by the
     ambient's hue; at 1, metals 51 to 79, as its luminance, or by the albedo for 51); metals take no ambient and 46
     takes 1.4 times it. ponytail: the flat ambients of 25, 44, 45, 48, 49, 65 and 120 to 139 (bots, pickups) and
     52 and 53's colours are left out. */
  function materialClass(id) {
    const metal = id > 50 && id <= 79;
    const reflection = metal ? 1 : [40, 41, 42, 44, 45, 46, 47, 49].includes(id) || (id >= 120 && id <= 139) ? .5 : 0;
    return [reflection, metal ? 0 : id === 46 ? 1.4 : 1, id === 51 ? 1 : 0];
  }

  /* GLSL: declarations, the vertex shader's line, and gameLight(world normal, world position, toward the eye, sun
     visibility), which returns the diffuse light and leaves the specular light in gameSpecular (tile.cs's GGX: Schlick
     visibility with k = (rough + 1)^2 / 8, Fresnel on N.L, the distribution raised to 1 / 2.2) and the ambient light
     in gameAmbientLight. Set first: gameF0, gameRough (at least 0.01), gameStrength (the specular map's green) and
     gameAmbientScale. */
  const vertexHead = 'varying vec3 vGameWorld;\n';
  const vertexBody = `
    vec4 gameWorld = vec4(transformed, 1.);
    #ifdef USE_INSTANCING
      gameWorld = instanceMatrix * gameWorld;
    #endif
    vGameWorld = (modelMatrix * gameWorld).xyz;`;
  const fragmentHead = `
    precision highp sampler3D;
    varying vec3 vGameWorld;
    uniform sampler2D gameLights, gameCells, gameLists;
    uniform sampler3D gameGrid;
    uniform vec3 gameCellMin, gameCellCount, gameGridMin, gameGridMax, gameAmbient, gameShadowAmbient, gameShadowColour, gameSunColour, gameSunToward;
    uniform float gameSunSpecular;
    vec3 gameF0, gameSpecular, gameAmbientLight;
    float gameRough, gameStrength, gameAmbientScale;
    vec3 gameGGX(vec3 n, vec3 l, vec3 v, float nl) {
      float k = (gameRough + 1.) * (gameRough + 1.) / 8., a4 = pow(gameRough, 4.), nh = clamp(dot(n, normalize(l + v)), 0., 1.);
      float d = a4 / (3.14159265 * pow(nh * nh * (a4 - 1.) + 1., 2.));
      float visibility = .25 / ((nl * (1. - k) + k) * (clamp(dot(n, v), 0., 1.) * (1. - k) + k));
      return (gameF0 + (1. - gameF0) * pow(1. - nl, 5.)) * visibility * pow(d, 1. / 2.2) * nl * gameStrength;
    }
    vec3 gameLight(vec3 n, vec3 p, vec3 v, float sunLit) {
      vec4 grid = texture(gameGrid, clamp((p - gameGridMin) / (gameGridMax - gameGridMin), 0., 1.));
      gameAmbientLight = mix(gameShadowAmbient, gameAmbient, sunLit) * (1. - grid.a) + grid.rgb;
      vec3 light = gameAmbientLight * gameAmbientScale, shade = mix(gameShadowColour, vec3(1.), sunLit);
      float nl = max(dot(n, gameSunToward), 0.);
      light += gameSunColour * nl * shade;
      gameSpecular = gameSunColour * gameSunSpecular * gameGGX(n, gameSunToward, v, nl) * shade;
      ivec3 c = ivec3(floor((p - gameCellMin) / ${CELL}.));
      if (any(lessThan(c, ivec3(0))) || any(greaterThanEqual(c, ivec3(gameCellCount)))) return light;
      int cell = c.x + int(gameCellCount.x) * (c.y + int(gameCellCount.y) * c.z);
      vec2 range = texelFetch(gameCells, ivec2(cell % ${WIDTH}, cell / ${WIDTH}), 0).rg;
      for (int k = 0; k < ${MAX_PER_CELL}; k++) {
        if (k >= int(range.y)) break;
        int at = int(range.x) + k, i = int(texelFetch(gameLists, ivec2(at % ${WIDTH}, at / ${WIDTH}), 0).r);
        vec4 a = texelFetch(gameLights, ivec2(0, i), 0), b = texelFetch(gameLights, ivec2(1, i), 0);
        vec4 d = texelFetch(gameLights, ivec2(2, i), 0), e = texelFetch(gameLights, ivec2(3, i), 0);
        vec3 q = a.xyz;
        if (b.w > 1.5) { vec3 s = d.xyz * e.z; q += s * clamp(dot(p - a.xyz, s) / max(dot(s, s), 1e-4), 0., 1.); }
        vec3 to = q - p;
        float dist = length(to);
        if (dist >= a.w) continue;
        vec3 l = to / max(dist, 1e-4);
        float fade = pow(clamp((a.w - dist) / max(a.w - d.w, 1e-3), 0., 1.), 2.2);
        if (b.w > .5 && b.w < 1.5) {
          float cosine = dot(-l, d.xyz);
          fade *= cosine > e.x ? (e.y > 0. ? clamp((cosine - e.x) / e.y, 0., 1.) : 1.) : 0.;
        }
        float nl = max(dot(n, l), 0.);
        light += b.rgb * nl * fade;
        if (e.w > 0.) gameSpecular += b.rgb * e.w * gameGGX(n, l, v, nl) * fade;
      }
      return light;
    }
  `;
  /* In place of the Lambert shader's light: the game's, from the first directional light's shadow, and the
     material's (gameSpecularMap: red gloss, green strength; gameClass: materialClass) specular light and envmap
     reflection: the cube (gameEnvmap, its faces as in the game's files, which start at the game's third mip) along
     the reflection in game axes, at mip roughness^(1 / 2.2) x 10. */
  const fragmentBody = `
    #include <aomap_fragment>
    {
      float sunLit = 1.;
      #if defined( USE_SHADOWMAP ) && NUM_DIR_LIGHT_SHADOWS > 0
        if (receiveShadow) sunLit = getShadow(directionalShadowMap[0], directionalLightShadows[0].shadowMapSize,
          directionalLightShadows[0].shadowBias, directionalLightShadows[0].shadowRadius, vDirectionalShadowCoord[0]);
      #endif
      #ifdef USE_UV
        vec4 specularTexel = texture2D(gameSpecularMap, vUv);
      #else
        vec4 specularTexel = texture2D(gameSpecularMap, vec2(.5));
      #endif
      // Toward the eye from the view-space position (three sets no cameraPosition for Lambert materials).
      vec3 n = inverseTransformDirection(normal, viewMatrix), v = inverseTransformDirection(normalize(vViewPosition), viewMatrix);
      float rough = 1. - specularTexel.r * gameGloss;
      gameRough = max(rough, .01);
      gameStrength = specularTexel.g;
      gameF0 = gameClass.x * diffuseColor.rgb;
      gameAmbientScale = gameClass.y;
      vec3 colour = diffuseColor.rgb * gameLight(n, vGameWorld, v, sunLit) + gameSpecular;
      if (gameClass.x > 0.) {
        vec3 r = reflect(-v, n), hue = min(clamp(gameAmbientLight, 0., 1.) + 1e-4, 1.);
        vec3 seen = textureLod(gameEnvmap, vec3(r.x, r.y, -r.z), max(pow(max(rough, 0.), 1. / 2.2) * 10. - 2., 0.)).rgb;
        seen *= hue / max(hue.r, max(hue.g, hue.b)) * specularTexel.g;
        colour += gameClass.x < .7 ? seen : gameClass.z > 0. ? seen * diffuseColor.rgb : vec3(dot(seen, vec3(.21, .72, .07)));
      }
      reflectedLight.directDiffuse = colour;
      reflectedLight.indirectDiffuse = vec3(0.);
    }`;
  const fragmentUniforms = 'uniform sampler2D gameSpecularMap;\nuniform samplerCube gameEnvmap;\nuniform vec3 gameClass;\nuniform float gameGloss;\n';

  exports.DiaboticalLighting = {buildGrid, buildLights, nodeWeights, materialClass,
    shader: {vertexHead, vertexBody, fragmentHead: fragmentUniforms + fragmentHead, fragmentBody}, CELL, WIDTH};
})(typeof module !== 'undefined' ? module.exports : window);

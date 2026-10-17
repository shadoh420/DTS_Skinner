/* Vanilla Three.js free-flight viewer for the pack written by tools/import_t1_map.py.
   The pack is already in viewer space: Tribes (x, y, z-up) -> Three (x, z, -y). */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  // RB3D supplies only its pack/streaming adapter; the T1 path keeps its defaults.
  const adapter = window.rb3dMaps;
  const npcDraws = [], npcIDs = new Set(), npcToggle = window.skinnerNPCToggle;
  if (npcToggle) {
    npcToggle.setCount(0);
    npcToggle.onChange = enabled => { for (const draw of npcDraws) draw.visible = enabled; };
  }
  // Another DarkStar game's page (Starsiege) keeps the T1 drawing and names its own pack, title and import.
  const site = Object.assign({data: '/t1-map-data/', title: 'T1 Maps', storageKey: 'skinner.t1maps', importRoute: '/import_t1_maps',
    folder: 'Tribes', firstMap: 'raindance'}, window.skinnerT1MapConfig);
  const storageKey = adapter ? 'skinner.rb3dmaps' : site.storageKey;
  const settings = {fov: 90, fog: true, weather: '', lights: true, invertX: false, invertY: false, sky: ''};
  try { Object.assign(settings, JSON.parse(localStorage.getItem(storageKey) || '{}')); } catch (_) { /* Defaults remain usable. */ }
  if (typeof settings.weather !== 'string') settings.weather = settings.weather === false ? 'none' : '';  // Saved by the old on/off box.
  const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(settings)); } catch (_) { /* Storage may be unavailable. */ } };

  const canvas = $('c');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 1, .2, 9000);
  camera.rotation.order = 'YXZ';
  let data = adapter ? '/rb3d-map-data/' : site.data;
  let streamFrame = null;
  let speed = 40, missing = 0, ready = false, map = null, mapId = '';
  const showStatus = text => { $('status').textContent = text; };
  const showReady = () => showStatus(`${missing ? `Map loaded with ${missing} missing assets` : 'Map ready'} · speed ${Math.round(speed)}`);

  async function get(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} (${response.status})`);
    return response;
  }
  const loader = new THREE.TextureLoader(), textures = new Map();
  function loadTexture(url, repeat) {
    // Other games' skies may be DDS (Bloodlines, E.Y.E.): the shared loader, opaque, as their own pages draw them.
    if (!textures.has(url) && /\.dds$/i.test(url) && window.skinnerDDS) textures.set(url, window.skinnerDDS.load(url).then(texture => {
      if (repeat) texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      texture.userData.alpha = false;
      return texture;
    }, () => { missing++; return null; }));
    if (!textures.has(url)) textures.set(url, new Promise(resolve => loader.load(url, texture => {
      texture.flipY = false;
      texture.anisotropy = 8;
      if (repeat) texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      // Whether any texel is see-through: a Tribes 1.40 PNG's tRNS/alpha is its coverage (tree leaves).
      const {width, height} = texture.image, context = Object.assign(document.createElement('canvas'), {width, height}).getContext('2d');
      context.drawImage(texture.image, 0, 0);
      texture.userData.alpha = context.getImageData(0, 0, width, height).data.some((value, index) => index % 4 === 3 && value < 255);
      resolve(texture);
    }, undefined, () => { missing++; resolve(null); })));
    return textures.get(url);
  }

  let ground = () => -Infinity, highest = -Infinity;  // Terrain height under a point and its peak, for sight lines.
  async function buildTerrain(terrain) {
    const [heightData, squareData, lightData] = await Promise.all(['heights.bin', 'materials.bin', 'light.bin']
      .map(async name => (await get(`${data}maps/${mapId}/${name}`)).arrayBuffer()));
    const heights = new Float32Array(heightData), squares = new Uint8Array(squareData), light = new Uint16Array(lightData);
    // A pack with pieces draws a k x k piece of its slot's texture on each square (pieces.bin: piece index, row-major from the top).
    const pieces = terrain.pieces && new Uint8Array(await (await get(`${data}maps/${mapId}/pieces.bin`)).arrayBuffer());
    const size = terrain.squares, unit = terrain.unit, lightStep = (terrain.lightWidth - 1) / size;
    // Texture corners (lower-left, lower-right, upper-right, upper-left) for the 3-bit square orientation:
    // bit 2 and bit 4 mirror, bit 1 rotates, applied in that order as DarkStar does.
    const corners = [];
    for (let orientation = 0; orientation < 8; orientation++) {
      const uv = {0: [0, 1], 2: [0, 0], 4: [1, 0], 6: [1, 1]};
      const swap = (a, b) => { [uv[a], uv[b]] = [uv[b], uv[a]]; };
      if (orientation & 2) { swap(0, 6); swap(2, 4); }
      if (orientation & 4) { swap(0, 2); swap(4, 6); }
      if (orientation & 1) { swap(2, 4); swap(2, 6); swap(2, 0); }
      corners.push([uv[0], uv[6], uv[4], uv[2]]);
    }
    // Squares with an empty level are holes cut for buildings. Sorting by texture keeps one draw per texture.
    const order = [...Array(size * size).keys()].filter(square => !(squares[square * 2] >> 3 & 7))
      .sort((a, b) => squares[a * 2 + 1] - squares[b * 2 + 1]);
    const positions = new Float32Array(order.length * 12), uvs = new Float32Array(order.length * 8);
    const colors = new Float32Array(order.length * 12), indices = new Uint32Array(order.length * 6);
    const geometry = new THREE.BufferGeometry(), slots = [];
    order.forEach((square, n) => {
      const x = square % size, y = Math.floor(square / size), slot = squares[square * 2 + 1];
      [[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1]].forEach(([px, py], corner) => {
        positions.set([px * unit, heights[py * (size + 1) + px], -py * unit], n * 12 + corner * 3);
        const uv = corners[squares[square * 2] & 7][corner], k = pieces ? terrain.pieces[slot] : 1, piece = pieces ? pieces[square] : 0;
        uvs.set([(piece % k + uv[0]) / k, (Math.floor(piece / k) + uv[1]) / k], n * 8 + corner * 2);
        const packed = light[py * lightStep * terrain.lightWidth + px * lightStep];
        colors.set([(packed >> 8 & 15) / 15, (packed >> 4 & 15) / 15, (packed & 15) / 15], n * 12 + corner * 3);
      });
      // DarkStar alternates the split diagonal in a checkerboard; a pack with splitBit flips it per square (bit 6).
      const flip = terrain.splitBit ? squares[square * 2] >> 6 & 1 : 0;
      indices.set(((x ^ y) & 1 ^ flip ? [0, 1, 3, 1, 2, 3] : [0, 1, 2, 0, 2, 3]).map(corner => n * 4 + corner), n * 6);
      if (slots[slots.length - 1] !== slot) { slots.push(slot); geometry.addGroup(n * 6, 0, slots.length - 1); }
      geometry.groups[geometry.groups.length - 1].count += 6;
    });
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    geometry.computeBoundingSphere();
    // The terrain is in view at once, as the green wireframe t2-mapper's terrain shows until its textures load.
    // Unlit here: this geometry carries the terrain lightmap as vertex colours and has no normals to light.
    const materials = slots.map(() => new THREE.MeshBasicMaterial({color: 'rgb(0, 109, 56)', wireframe: true}));
    const width = size * unit, [x, y, z] = terrain.position;
    highest = z + heights.reduce((most, height) => Math.max(most, height), -Infinity);
    ground = (worldX, worldZ) => {  // The game's terrain repeats without end, so this wraps.
      const column = (((worldX - x) / unit) % size + size) % size, row = (((-worldZ - y) / unit) % size + size) % size;
      const i = Math.floor(column), j = Math.floor(row), u = column - i, v = row - j, at = (a, b) => heights[b * (size + 1) + a];
      return z + (at(i, j) * (1 - u) + at(i + 1, j) * u) * (1 - v) + (at(i, j + 1) * (1 - u) + at(i + 1, j + 1) * u) * v;
    };
    for (let row = 0; row < terrain.rows; row++) for (let column = 0; column < terrain.columns; column++) {
      const mesh = new THREE.Mesh(geometry, materials);
      mesh.position.set(x + column * width, z, -(y + row * width));
      scene.add(mesh);
    }
    // The tiles share the material list, so each slot turns textured on all of them as its texture arrives.
    await Promise.all(slots.map(async (slot, index) => {
      const texture = terrain.textures[slot] && await loadTexture(data + 'textures/' + terrain.textures[slot]);
      materials[index].dispose();
      materials[index] = withPointLights(new THREE.MeshBasicMaterial(texture ? {map: texture, vertexColors: true} : {color: 0xcc00cc}));
    }));
  }

  const models = new Map();
  function loadModel(name, pack, lightUv) {
    // Catalog models come from the workshop; buildings the catalog lacks were exported into the pack at import.
    // Lightmap coordinates are per building type, so lit placements of one building still share a geometry.
    const key = name + '|' + (lightUv || '');
    if (!models.has(key)) models.set(key, (async () => {
      const [model, uv2] = await Promise.all([
        get(pack ? data + 'models/' + name : `/model_json/${encodeURIComponent(name)}?game=t1`).then(response => response.json()),
        lightUv && get(data + 'textures/' + lightUv).then(response => response.arrayBuffer())]);
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(model.vertices, 3));
      geometry.setAttribute('uv', new THREE.Float32BufferAttribute(model.uvs, 2));
      if (uv2) geometry.setAttribute('uv2', new THREE.BufferAttribute(new Float32Array(uv2), 2));
      // Interiors store one group per surface; merge to one draw per material.
      const byMaterial = model.material_textures.map(() => []);
      const parts = model.groups && model.groups.length ? model.groups : [{start: 0, count: model.indices.length, materialIndex: 0}];
      for (const part of parts) byMaterial[part.materialIndex].push(...model.indices.slice(part.start, part.start + part.count));
      geometry.setIndex(byMaterial.flat());
      let start = 0;
      byMaterial.forEach((list, index) => { if (list.length) geometry.addGroup(start, list.length, index); start += list.length; });
      geometry.computeVertexNormals();
      const materials = await Promise.all(model.material_textures.map(async (file, slot) => {
        const blank = file.startsWith('[');
        const texture = blank || !file ? null : await loadTexture(pack ? data + 'textures/' + file : `/texture/${encodeURIComponent(file)}?game=t1`, true);
        const color = texture ? 0xffffff : blank ? 0x999999 : 0xcc00cc, side = THREE.DoubleSide;
        // The shape's TS material flags, as ts_matRender.cpp draws them: unshaded (0x100), transparent (0x1000: colour key,
        // depth written) and translucent (0x2000: blended, no depth write, no haze) are all drawn unlit; an additive
        // bitmap (0x10000, set at import) adds itself. Other flagged slots are opaque.
        const flags = model.material_flags ? model.material_flags[slot] || 0 : null;
        if (flags & 0x3100) {
          const translucent = Boolean(flags & 0x2000), additive = translucent && flags & 0x10000;
          return new THREE.MeshBasicMaterial({map: texture, color, side, transparent: translucent, depthWrite: !translucent, fog: !translucent,
            alphaTest: flags & 0x1000 ? .5 : 0, ...additive ? {blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneFactor} : {}});
        }
        // Models without flags (older packs, most catalog models): a texture with alpha is blended by it, as the game draws its leaves.
        const transparent = flags === null && Boolean(texture && texture.userData.alpha);
        const look = {map: texture, color, side, transparent, depthWrite: !transparent, alphaTest: transparent ? .02 : 0};
        return map.sun.ramp ? withRampShade(new THREE.MeshBasicMaterial(look)) : new THREE.MeshLambertMaterial(look);
      }));
      return {geometry, materials};
    })());
    return models.get(key);
  }
  // Building lightmaps. The atlas holds the mission's light and the lights that never change; lights the game animates
  // come in the building's animation file (layout in the importer's bake_lightmap) and are blended in here, after
  // DarkStar's ITRInstance (stepLightTime, updateLight, updateSpecialLight, merge): a light loops through its states,
  // each state naming an intensity map per surface, and colour × intensity is added to the 4-bit lightmap, saturating.
  // ponytail: blended on the CPU and each changed atlas uploaded whole, every light of the map whether in view or not;
  // blend in a shader, or skip distant buildings, if a map with many animated buildings stutters.
  const lightMaps = new Map(), lightSteps = [];
  function loadLightMap({map: file, anim}) {
    const key = file + '|' + (anim || '');
    if (!lightMaps.has(key)) lightMaps.set(key, (async () => {
      const [texture, buffer] = await Promise.all([loadTexture(data + 'textures/' + file),
        anim && get(data + 'textures/' + anim).then(response => response.arrayBuffer()).catch(() => null)]);
      if (texture) { texture.generateMipmaps = false; texture.minFilter = THREE.LinearFilter; }  // Mip levels would bleed between atlas cells.
      if (!texture || !buffer) return texture;
      const {width, height} = texture.image, context = Object.assign(document.createElement('canvas'), {width, height}).getContext('2d');
      context.drawImage(texture.image, 0, 0);
      const base = context.getImageData(0, 0, width, height).data, pixels = new Uint8Array(base);
      const lit = new THREE.DataTexture(pixels, width, height);
      lit.magFilter = lit.minFilter = THREE.LinearFilter;
      lit.needsUpdate = true;
      const view = new DataView(buffer);
      let at = 0;
      const int = () => view.getInt32((at += 4) - 4, true), float = () => view.getFloat32((at += 4) - 4, true), short = () => view.getUint16((at += 2) - 2, true);
      const lightCount = int(), cells = Array.from({length: int()}, () => ({x: short(), y: short(), w: short(), h: short(), lights: []}));
      const lights = Array.from({length: lightCount}, () => {
        const light = {flags: int(), duration: float(), time: 0, state: -1, colour: [0, 0, 0]};
        light.states = Array.from({length: int()}, () => {
          const state = {time: float(), colour: [short(), short(), short()], maps: new Map()};
          at += 2;
          for (let entries = int(); entries; entries--) {
            const cell = cells[int()];
            state.maps.set(cell, int());
            if (!cell.lights.includes(light)) cell.lights.push(light);
          }
          return state;
        });
        return light;
      });
      const intensity = new Uint8Array(buffer, at), changed = new Set();
      const step = seconds => {
        for (const light of lights) {
          const states = light.states, last = states.length - 1;
          light.time += seconds;
          while (light.time > light.duration) light.time -= light.duration;
          let index = 0, mix = 0;
          if (light.flags & 4) {
            // A flicker light: every states[1].time a state is drawn at random, weighted by how long it lasts.
            if (light.state >= 0) {
              if (light.time < states[1].time) continue;
              light.time %= states[1].time;
              const pick = Math.random() * light.duration;
              index = states.findIndex((state, number) => number && state.time > pick) - 1;
              if (index < 0) index = last;
            }
          } else {
            // Any other: the colour runs from each state's to the next one's, and from the last back to the first.
            while (index < last && light.time > states[index + 1].time) index++;
            const span = (index < last ? states[index + 1].time : light.duration) - states[index].time;
            mix = span ? (light.time - states[index].time) / span : 1;
          }
          const from = states[index].colour, to = states[index < last ? index + 1 : 0].colour;
          const colour = from.map((value, channel) => Math.floor((value + (to[channel] - value) * mix) / 256 + .5) & 255);
          if (index === light.state && colour.every((value, channel) => value === light.colour[channel])) continue;
          for (const state of [states[Math.max(light.state, 0)], states[index]]) for (const cell of state.maps.keys()) changed.add(cell);
          light.state = index;
          light.colour = colour;
        }
        for (const cell of changed) {
          const sources = cell.lights.map(light => [light.colour, light.states[light.state].maps.get(cell)])
            .filter(([colour, start]) => start !== undefined && colour.some(Boolean));
          // One texel beyond each edge is the gutter, which repeats the edge.
          for (let v = -1; v <= cell.h; v++) for (let u = -1; u <= cell.w; u++) {
            const s = Math.max(0, Math.min(cell.w - 1, u)), t = Math.max(0, Math.min(cell.h - 1, v));
            const source = ((cell.y + t) * width + cell.x + s) * 4, target = ((cell.y + v) * width + cell.x + u) * 4;
            let red = base[source] / 17, green = base[source + 1] / 17, blue = base[source + 2] / 17;
            for (const [colour, start] of sources) {
              const level = intensity[start + t * cell.w + s];
              if (level < 16) continue;
              red += colour[0] * level >> 12;
              green += colour[1] * level >> 12;
              blue += colour[2] * level >> 12;
            }
            pixels[target] = Math.min(15, red) * 17;
            pixels[target + 1] = Math.min(15, green) * 17;
            pixels[target + 2] = Math.min(15, blue) * 17;
          }
        }
        if (changed.size) lit.needsUpdate = true;
        changed.clear();
      };
      step(0);
      lightSteps.push(step);
      return lit;
    })());
    return lightMaps.get(key);
  }

  // Mission point lights (SimLight type 2, and items such as flags that glow, Item::getLight). The terrain and buildings
  // take the ones that are not static at run time, as GridRender and ITRInstance::mergeDynamicLight do: colour ×
  // (1 - distance / range) added to the baked light, saturating; static ones are already in those bakes. Shapes take
  // every one as a three PointLight with the same linear falloff (the game's shape path has a hard edge at the range).
  // ponytail: every light is summed for every terrain and building pixel; cull by distance if a map with many stutters.
  let pointLights = [], dynamicLights = [];
  const lightUniforms = {t1LightPos: {value: []}, t1LightColor: {value: []}, t1LightRange: {value: []}};
  // Earthsiege 2 shapes (a scene with sun.ramp): each face takes the sun as DBSIM's Light_ComputeShadeForFace does,
  // shade = min(255, 512 cos) with no ambient, through the .RMP row shade * 31 >> 8 (the ramp: row, light). The face
  // normal is the one facing the eye, as DBSIM lights a back face with its normal negated.
  const rampUniforms = {t1Toward: {value: new THREE.Vector3()}, t1Ramp: {value: []}};
  function withRampShade(material) {
    material.onBeforeCompile = shader => {
      Object.assign(shader.uniforms, rampUniforms);
      shader.vertexShader = 'varying vec3 t1Shaded;\n' + shader.vertexShader.replace('#include <project_vertex>',
        '#include <project_vertex>\n\tt1Shaded = (modelMatrix * vec4(transformed, 1.0)).xyz;');
      shader.fragmentShader = `varying vec3 t1Shaded;
        uniform vec3 t1Toward;
        uniform vec2 t1Ramp[${map.sun.ramp.length}];
        float t1Shade() {
          vec3 n = normalize(cross(dFdx(t1Shaded), dFdy(t1Shaded)));
          if (dot(n, cameraPosition - t1Shaded) < 0.) n = -n;
          float row = floor(min(255., 512. * max(dot(n, t1Toward), 0.)) * 31. / 256.), light = t1Ramp[0].y;
          for (int i = 1; i < ${map.sun.ramp.length}; i++) if (row > t1Ramp[i - 1].x)
            light = mix(t1Ramp[i - 1].y, t1Ramp[i].y, clamp((row - t1Ramp[i - 1].x) / (t1Ramp[i].x - t1Ramp[i - 1].x), 0., 1.));
          return light;
        }\n` + shader.fragmentShader.replace('#include <color_fragment>', '#include <color_fragment>\n\tdiffuseColor.rgb *= t1Shade();');
    };
    return material;
  }

  function withPointLights(material) {
    if (!dynamicLights.length) return material;
    material.onBeforeCompile = shader => {
      Object.assign(shader.uniforms, lightUniforms);
      shader.vertexShader = 'varying vec3 t1World;\n' + shader.vertexShader.replace('#include <project_vertex>',
        '#include <project_vertex>\n\tt1World = (modelMatrix * vec4(transformed, 1.0)).xyz;');
      shader.fragmentShader = `#define T1_LIGHTS ${dynamicLights.length}
        varying vec3 t1World;
        uniform vec3 t1LightPos[T1_LIGHTS], t1LightColor[T1_LIGHTS];
        uniform float t1LightRange[T1_LIGHTS];
        vec3 t1Dynamic() {
          vec3 sum = vec3(0.);
          for (int i = 0; i < T1_LIGHTS; i++) sum += t1LightColor[i] * max(0., 1. - distance(t1World, t1LightPos[i]) / t1LightRange[i]);
          return sum;
        }\n` + shader.fragmentShader  // Terrain: the bake is the vertex colour; buildings: the lightmap.
        .replace('#include <color_fragment>', '#ifdef USE_COLOR\n\tdiffuseColor.rgb *= min(vColor + t1Dynamic(), 1.);\n#endif')
        .replace('lightMapTexel.rgb * lightMapIntensity * RECIPROCAL_PI', 'min(lightMapTexel.rgb * lightMapIntensity * RECIPROCAL_PI + t1Dynamic(), 1.)');
    };
    return material;
  }
  // Item::getLight's pulse: 0.15 + 0.85 (0.5 + 0.5 sin(π t / period)).
  function stepPointLights(seconds) {
    for (const light of pointLights) if (light.pulse) {
      const level = .15 + .85 * (.5 + .5 * Math.sin(Math.PI * seconds / light.pulse));
      light.shape.intensity = level;
      if (light.uniform) light.uniform.setRGB(...light.color).multiplyScalar(level);
    }
  }
  const spinners = [], up = new THREE.Vector3(0, 1, 0);  // Items that turn, 3 rad/s (Item::clientProcess).

  // A powered shape's "power" sequence (StaticShape::onPower), baked by the importer: vertex frames blended over the
  // clip's times, looping or held at the end (a turret raised), and its IFL tracks remapping material slots
  // (TS::ShapeInstance::Thread: each track's key at or before the time; a later track wins). Every placement of a
  // shape shares one geometry, so they run in step, as a base powered at the mission's start does.
  // ponytail: frames blended and normals rebuilt on the CPU each frame; move to a vertex shader if many shapes stutter.
  const animations = new Map(), animators = [];
  function loadAnimation(file) {
    if (!animations.has(file)) animations.set(file, (async () => {
      const clip = await (await get(data + 'textures/' + file)).json();
      const frames = new Float32Array(await (await get(data + 'textures/' + clip.frames)).arrayBuffer());
      const size = frames.length / clip.times.length, last = clip.times.length - 1, slots = clip.groups.map(group => group.materialIndex);
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(frames.slice(0, size), 3));
      geometry.setAttribute('uv', new THREE.Float32BufferAttribute(clip.uvs, 2));
      geometry.setIndex(clip.indices);
      for (const {start, count, materialIndex} of clip.groups) geometry.addGroup(start, count, materialIndex);
      geometry.computeVertexNormals();
      geometry.boundingSphere = new THREE.Sphere().setFromPoints(Array.from({length: frames.length / 3}, (_, n) => new THREE.Vector3().fromArray(frames, n * 3)));
      const position = geometry.attributes.position.array;
      animators.push(seconds => {
        const time = clip.loop && clip.duration ? seconds % clip.duration : Math.min(seconds, clip.duration);
        let i = 0;
        while (i < last - 1 && clip.times[i + 1] <= time) i++;
        if (last) {
          const span = clip.times[i + 1] - clip.times[i], mix = span > 0 ? Math.min(1, Math.max(0, (time - clip.times[i]) / span)) : 0;
          for (let k = 0; k < size; k++) position[k] = frames[i * size + k] + (frames[(i + 1) * size + k] - frames[i * size + k]) * mix;
          geometry.attributes.position.needsUpdate = true;
          geometry.computeVertexNormals();
        }
        const remap = new Map();
        for (const keys of clip.ifl) remap.set(keys[0][1], keys.reduce((found, key) => key[0] <= time ? key : found, keys[0])[2]);
        geometry.groups.forEach((group, n) => { group.materialIndex = remap.get(slots[n]) ?? slots[n]; });
      });
      return geometry;
    })());
    return animations.get(file);
  }

  const placeholder = new THREE.BoxGeometry(4, 4, 4).translate(0, 2, 0), magenta = new THREE.MeshBasicMaterial({color: 0xcc00cc});
  // Until its model arrives each placed object is the orange wireframe box t2-mapper shows for a loading interior.
  const loadingBox = new THREE.BoxGeometry(10, 10, 10), loadingMaterial = new THREE.MeshStandardMaterial({color: 'orange', wireframe: true});
  async function addObject(object) {
    const matrix = new THREE.Matrix4().fromArray(object.matrix), loading = new THREE.Mesh(loadingBox, loadingMaterial);
    loading.applyMatrix4(matrix);
    if (object.model && !object.npc) scene.add(loading);
    let mesh, drawn = false;
    try {
      if (!object.model) throw new Error('No preview model');
      const model = await loadModel(object.model, object.source === 'pack', object.light && object.light.uv);
      const lightMap = object.light && await loadLightMap(object.light);
      // The mission lightmap already holds the sun and the building's own lights: texture × lightmap, no scene lights.
      const geometry = object.animation ? await loadAnimation(object.animation).catch(() => model.geometry) : model.geometry;
      mesh = new THREE.Mesh(geometry, !lightMap ? model.materials : model.materials.map(material => withPointLights(new THREE.MeshBasicMaterial(
        {map: material.map, color: material.color, side: THREE.DoubleSide, lightMap,
          transparent: material.transparent, depthWrite: material.depthWrite, alphaTest: material.alphaTest}))));
      drawn = true;
    } catch (_) { missing++; mesh = new THREE.Mesh(placeholder, magenta); }
    mesh.name = object.name;
    mesh.applyMatrix4(matrix);
    scene.remove(loading);
    scene.add(mesh);
    if (object.npc === true) {
      npcDraws.push(mesh);
      mesh.visible = npcToggle ? npcToggle.enabled : true;
      if (drawn) npcIDs.add(object.entity); // Missing-model boxes are hidden too, but do not count as actors.
    }
    occluders.push(mesh);
    if (object.spin) spinners.push(mesh);
    if (object.shelter) {  // Read once any weather shows, whichever the Weather list picks.
      const building = {shelter: object.shelter, matrix};
      shelterQueue.push(building);
      if (weather) await addShelter(building);
    }
  }

  // Sky: sixteen textured panels around the camera with flat caps above and below, drawn behind everything.
  // The dome is scaled down to sit inside the clip range; centred on the camera, its size does not show.
  // The panels can be swapped for another game's sky (the Sky list: domes, Quake III shaders, Quake II boxes); the own stars,
  // planets and flare show only with its own sky.
  const sky = new THREE.Group(), own = new THREE.Group(), haze = new THREE.Color();
  let panels = new THREE.Group();
  sky.add(panels, own);
  async function buildSky(dome, visible, group, base = data) {
    if (!dome.textures) { scene.background = new THREE.Color(`rgb(${dome.color.join(',')})`); return; }
    const point = (index, upper) => {
      const angle = THREE.MathUtils.degToRad(67.5 + dome.feature - 22.5 * index);
      return [Math.cos(angle) * visible * 1.05, (upper ? .75 : -.125) * dome.size, -Math.sin(angle) * visible * 1.05];
    };
    const add = (positions, uvs, indices, material) => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions.flat(), 3));
      if (uvs) geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
      geometry.setIndex(indices);
      const mesh = new THREE.Mesh(geometry, Object.assign(material, {depthTest: false, depthWrite: false, fog: false, side: THREE.DoubleSide}));
      mesh.renderOrder = -3;
      mesh.frustumCulled = false;
      group.add(mesh);
    };
    await Promise.all(dome.textures.map(async (file, slot) => {
      const texture = file && await loadTexture(base + 'textures/' + file);
      if (texture) add([point(slot, false), point(slot + 1, false), point(slot + 1, true), point(slot, true)],
        [0, 1, 1, 1, 1, 0, 0, 0], [0, 1, 2, 0, 2, 3], new THREE.MeshBasicMaterial({map: texture}));
    }));
    for (const upper of [true, false]) add(
      [[0, upper ? dome.size : -dome.size, 0], ...Array.from({length: 16}, (_, slot) => point(slot, upper))], null,
      Array.from({length: 16}, (_, slot) => [0, slot + 1, (slot + 1) % 16 + 1]).flat(),
      new THREE.MeshBasicMaterial({color: `rgb(${(upper ? dome.top : dome.bottom).join(',')})`}));
  }

  // A Quake III sky shader on a ball round the camera: its box, then its cloud layers, as the Q3 Maps page draws
  // them (q3-maps/viewer.js boxMaterial and coordinates) but at full brightness, the T1 page having no overbright.
  const skyTime = {value: 0}, FACTORS = {gl_one: THREE.OneFactor, gl_zero: THREE.ZeroFactor, gl_src_color: THREE.SrcColorFactor,
    gl_one_minus_src_color: THREE.OneMinusSrcColorFactor, gl_dst_color: THREE.DstColorFactor, gl_one_minus_dst_color: THREE.OneMinusDstColorFactor,
    gl_src_alpha: THREE.SrcAlphaFactor, gl_one_minus_src_alpha: THREE.OneMinusSrcAlphaFactor, gl_dst_alpha: THREE.DstAlphaFactor,
    gl_one_minus_dst_alpha: THREE.OneMinusDstAlphaFactor};
  const skyFill = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);  // Black until showSky gives it the haze.
  skyFill.needsUpdate = true;
  async function buildQ3Sky(shader, radius, group, base = '/q3-map-data/', power = 1) {
    const number = value => { const text = String(Number(value) || 0); return `(${/[.e]/.test(text) ? text : text + '.0'})`; };
    const ball = new THREE.SphereGeometry(radius, 32, 16);
    const vertexShader = 'varying vec3 vDir; void main() { vDir = vec3(position.x, -position.z, position.y); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.); }';
    const add = (material, order) => {
      Object.assign(material, {depthTest: false, depthWrite: false, side: THREE.BackSide});
      const mesh = new THREE.Mesh(ball, material);
      mesh.renderOrder = order;
      mesh.frustumCulled = false;
      group.add(mesh);
    };
    // A face the entry has no file for (null: a Tribes 2 down face that never shipped) is skyFill, a plain colour;
    // a named face that fails to load still drops the box. Those TR2 skies' sides are blank white below the horizon
    // (fogged in the game), so without a down face everything below the horizon takes the fill.
    const box = shader.sky.box && await Promise.all(shader.sky.box.map(file => file ? loadTexture(base + 'textures/' + file) : skyFill));
    if (box && box.every(Boolean)) {
      const names = ['rt', 'bk', 'lf', 'ft', 'up', 'dn'];
      // As the Quake II page draws its box: no mipmaps (they seam where the face changes) and the edge texels clamped.
      for (const face of box) Object.assign(face, {generateMipmaps: false, minFilter: THREE.LinearFilter, needsUpdate: true});
      // power: the source page's display ramp, pow(rgb, power) (Bloodlines draws its whole frame through 2/3).
      // A Quake II skyrotate turns the look direction about skyaxis, rotate degrees a second (quake2-maps/materials.js).
      add(new THREE.ShaderMaterial({uniforms: {...Object.fromEntries(names.map((name, index) => [name, {value: box[index]}])), power: {value: power}, upTurn: {value: !!shader.sky.upTurn},
        below: {value: box[5] === skyFill}, uTime: skyTime, rate: {value: shader.sky.rotate || 0}, axis: {value: new THREE.Vector3(...shader.sky.axis || [0, 0, 1]).normalize()}}, vertexShader,
        fragmentShader: `uniform sampler2D rt, bk, lf, ft, up, dn; uniform float power, uTime, rate; uniform bool upTurn, below; uniform vec3 axis; varying vec3 vDir;
          vec2 top(vec2 p) { return upTurn ? vec2(-p.y, p.x) : p; }  // Tribes 2's top face is a quarter turn round.
          vec4 side(sampler2D image, vec2 p) { return texture2D(image, clamp(vec2(p.x + 1., 1. - p.y) * .5, .002, .998)); }
          void main() {
            float turn = radians(-uTime * rate), c = cos(turn), s = sin(turn);
            vec3 d = vDir * c + cross(axis, vDir) * s + axis * dot(axis, vDir) * (1. - c), a = abs(d);
            gl_FragColor = vec4((a.x >= a.y && a.x >= a.z ? (d.x > 0. ? side(rt, vec2(-d.y, d.z) / a.x) : side(lf, vec2(d.y, d.z) / a.x))
              : a.y >= a.z ? (d.y > 0. ? side(bk, vec2(d.x, d.z) / a.y) : side(ft, vec2(-d.x, d.z) / a.y))
              : (d.z > 0. ? side(up, top(vec2(-d.y, -d.x) / a.z)) : side(dn, vec2(-d.y, d.x) / a.z))).rgb, 1.);
            if (below && d.z < 0.) gl_FragColor = texture2D(dn, vec2(.5));
            gl_FragColor.rgb = pow(gl_FragColor.rgb, vec3(power));
          }`, blending: THREE.NoBlending}), -3.5);
    }
    await Promise.all((shader.stages || []).map(async (stage, index) => {
      const texture = stage.map && await loadTexture(base + 'textures/' + stage.map, true);
      if (!texture) return;
      // ponytail: scroll, scale, transform and rotate only; stretch and turb (rare on skies) are left still.
      const mods = (stage.tcMods || []).map(([kind, ...values]) => { const v = values.map(number); return {
        scroll: `st += fract(uTime * vec2(${v[0]}, ${v[1]}));`, scale: `st *= vec2(${v[0]}, ${v[1]});`,
        transform: `st = vec2(st.x * ${v[0]} + st.y * ${v[2]} + ${v[4]}, st.x * ${v[1]} + st.y * ${v[3]} + ${v[5]});`,
        rotate: `{ float a = -${v[0]} * uTime * .01745329, c = cos(a), s = sin(a); st = vec2(st.x * c - st.y * s + .5 - .5 * c + .5 * s, st.x * s + st.y * c + .5 - .5 * s - .5 * c); }`}[kind] || ''; }).join(' ');
      const colour = stage.rgbGen && stage.rgbGen[0] === 'const' ? `vec3(${stage.rgbGen.slice(1).map(number).join(', ')})` : 'vec3(1.)';
      const alpha = stage.alphaGen && stage.alphaGen[0] === 'const' ? number(stage.alphaGen[1]) : '1.';
      const test = {gt0: 'if (c.a <= 0.) discard;', lt128: 'if (c.a >= .5) discard;', ge128: 'if (c.a < .5) discard;'}[stage.alphaFunc] || '';
      add(new THREE.ShaderMaterial({uniforms: {map: {value: texture}, uTime: skyTime}, vertexShader,
        fragmentShader: `uniform sampler2D map; uniform float uTime; varying vec3 vDir;
          void main() {
            vec3 P = vDir;
            float height = ${number(shader.sky.cloudHeight ?? 128)}, squared = dot(P, P);
            float p = (-2. * P.z * 4096. + 2. * sqrt(max(P.z * P.z * 16777216. + squared * (8192. * height + height * height), 0.))) / (2. * squared);
            vec3 onSphere = normalize(P * p + vec3(0., 0., 4096.));
            vec2 st = vec2(acos(clamp(onSphere.x, -1., 1.)), acos(clamp(onSphere.y, -1., 1.)));
            ${mods}
            vec4 c = texture2D(map, st) * vec4(${colour}, ${alpha});
            ${test}
            gl_FragColor = c;
          }`,  // Opaque pass, so the world draws after it whatever the blend.
        blending: stage.blend ? THREE.CustomBlending : THREE.NoBlending,
        blendSrc: stage.blend ? FACTORS[stage.blend[0]] || THREE.OneFactor : THREE.OneFactor,
        blendDst: stage.blend ? FACTORS[stage.blend[1]] || THREE.OneFactor : THREE.ZeroFactor}), -3.4 + index * .01);
    }));
  }
  // A swapped sky's haze: fog and background take the average colour of its band from the horizon to 10° above, all the
  // way round, read back from a small render of that sky alone. The mission's own sky keeps the mission's haze.
  const probe = {target: new THREE.WebGLRenderTarget(96, 8), eye: new THREE.PerspectiveCamera(10, 12, .01, 100), pixels: new Uint8Array(96 * 8 * 4)};
  function horizonHaze() {
    const {target, eye, pixels} = probe, sum = [0, 0, 0];
    eye.position.copy(sky.position);
    for (let quarter = 0; quarter < 4; quarter++) {  // Each view is 93° wide.
      eye.rotation.set(THREE.MathUtils.degToRad(5), quarter * Math.PI / 2, 0, 'YXZ');
      eye.updateMatrixWorld();
      renderer.setRenderTarget(target);
      if (skyPass?.render) skyPass.render(renderer, eye, skyTime.value); else renderer.render(sky, eye);
      renderer.readRenderTargetPixels(target, 0, 0, 96, 8, pixels);
      pixels.forEach((value, index) => { if (index % 4 < 3) sum[index % 4] += value; });
    }
    renderer.setRenderTarget(null);
    haze.setRGB(...sum.map(total => total / (4 * 96 * 8 * 255)));
  }
  // skyPass: a sky drawn in its own pass behind the world (skies.js unreal: render), or one with work before each frame
  // and a height fog over it (skies.js skinner: update, fog).
  let skies = [], skySerial = 0, skyPass = null;
  // A Sky or Weather list entry's label: its pack section, name, and the first map with it.
  const listText = entry => [entry.section, entry.name, entry.maps[0] + (entry.maps.length > 1 ? ` +${entry.maps.length - 1}` : '')].filter(Boolean).join(' · ');
  async function showSky(id) {
    const chosen = skies.find(entry => entry.id === id), visible = chosen && chosen.visible || map.terrain.visibleDistance, radius = visible * 1.05;
    // Each entry names its renderer and its pack's data path (/t1-map-skies); a dome is a T1-style mission sky.
    const kind = chosen && (chosen.renderer || (chosen.game === 'q3' ? 'q3' : 'dome'));
    const serial = ++skySerial, group = new THREE.Group(), dome = chosen ? kind === 'dome' && chosen.sky : map.sky;
    scene.background = haze.set(`rgb(${map.haze.join(',')})`);
    if (kind === 'q3') await buildQ3Sky(chosen.sky, radius, group, chosen.base, chosen.power || 1);
    else if (dome) await buildSky(dome, visible, group, chosen ? chosen.base || '/t1-map-data/' : data);  // Its own pack's textures.
    const pass = window.t1Skies?.[kind] && !dome && kind !== 'q3' ? await window.t1Skies[kind]({entry: chosen, radius, group, loadTexture, time: skyTime,
      ground: map.viewpoints?.[0]?.[13] ?? 0}) : null;  // skies.js; ground: the start's height, for a Skinner Sky's height fog.
    const drop = old => old.traverse(mesh => { mesh.geometry?.dispose(); [].concat(mesh.material || []).forEach(material => material.dispose()); });  // Textures stay cached.
    if (serial !== skySerial) { pass?.dispose?.(); return drop(group); }  // A later choice is being built.
    skyPass?.dispose();
    skyPass = pass?.render || pass?.update ? pass : null;
    skyPass?.update?.(renderer);  // Its whole sky texture, before the haze probe reads it.
    if (skyPass?.render) scene.background = null;  // Otherwise the haze set above, or the colour an untextured dome set.
    drop(panels);
    sky.remove(panels);
    sky.add(panels = group);
    own.visible = !chosen;
    sky.scale.setScalar(10 / Math.max(radius, dome && dome.size || 0));
    if (chosen && scene.background?.isColor && scene.background !== haze) haze.copy(scene.background);  // An untextured dome's colour.
    else if (chosen) horizonHaze();
    scene.fog?.color.copy(haze);
    skyFill.image.data.set(haze.toArray().map(value => Math.round(value * 255)));  // The probe (0-10° up) never sees what it fills.
    skyFill.needsUpdate = true;
  }

  // Toward a planet: azimuth + 90° from file x, incidence up, as the importer's sun direction.
  function toward(azimuth, incidence) {
    const turn = THREE.MathUtils.degToRad(azimuth), climb = THREE.MathUtils.degToRad(Math.max(-89, Math.min(89, incidence)));
    return new THREE.Vector3(-Math.sin(turn) * Math.cos(climb), Math.sin(climb), -Math.cos(turn) * Math.cos(climb));
  }
  const behind = order => ({depthTest: false, depthWrite: false, fog: false, transparent: false, renderOrder: order});

  // Stars and planets ride in the sky group, in front of its panels and behind the world.
  async function buildSkyObjects(radius) {
    if (map.stars) {
      // The game's generator: 3000 points in a ball from a linear congruential sequence, those above the horizon
      // drawn. 9 in 30 take the first colour, 14 the second, 7 the third, and 1 in 30 is two pixels wide.
      let state = 1;
      const next = () => { state = Math.imul(state, 214013) + 2531011 >>> 0; return state >>> 16 & 32767; };
      const positions = [[], []], colours = [[], []];
      for (let star = 0; star < 3000; star++) {
        let x, y, z, length;
        do { x = next() % 2000 - 1000; y = next() % 2000 - 1000; z = next() % 2000 - 1000; length = Math.hypot(x, y, z); }
        while ((!x && !y) || length > 1000);
        const brightness = next() % 30, wide = brightness ? 0 : 1;
        if (z < 0) continue;
        positions[wide].push(x / length * radius, z / length * radius, -y / length * radius);
        colours[wide].push(...map.stars[brightness < 9 ? 0 : brightness < 23 ? 1 : 2]);
      }
      positions.forEach((list, wide) => {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(list, 3));
        geometry.setAttribute('color', new THREE.Float32BufferAttribute(colours[wide], 3));
        const {renderOrder, ...draw} = behind(-2);
        const points = new THREE.Points(geometry, new THREE.PointsMaterial({size: wide + 1, sizeAttenuation: false, vertexColors: true, ...draw}));
        points.renderOrder = renderOrder;
        points.frustumCulled = false;
        own.add(points);
      });
    }
    for (const planet of map.planets || []) {
      const texture = await loadTexture(data + 'textures/' + planet.texture);
      if (!texture) continue;
      texture.flipY = true;  // Sprites map the image upright.
      texture.magFilter = THREE.NearestFilter;  // The game magnifies planet bitmaps unfiltered,
      const {renderOrder, ...draw} = behind(-1);
      // ponytail: a sprite stays upright on screen; the game keeps the bitmap's top toward the zenith instead.
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map: texture, alphaTest: .65, ...draw}));  // and keys them at 0.65.
      sprite.position.copy(toward(planet.azimuth, planet.incidence)).multiplyScalar(radius);
      sprite.scale.setScalar(2 * planet.size * radius / planet.distance);
      sprite.renderOrder = renderOrder;
      own.add(sprite);
    }
  }

  // Lens flare, after ArenaPrototype's TribesPlanetFlare: eleven bitmaps strung from the sun through the screen centre
  // and a wash of the sun's colour, both fading as the sun leaves the centre and gone when something stands in front.
  const overlay = new THREE.Scene(), overlayCamera = new THREE.OrthographicCamera(0, 1, 1, 0, -1, 1), occluders = [];  // Placed objects.
  let flare = null;
  async function buildFlare(planet, files) {
    const textures = await Promise.all(files.map(file => loadTexture(data + 'textures/' + file)));
    if (!textures.every(Boolean)) return;
    for (const texture of textures) texture.flipY = true;
    // Bitmap, place along the line (0 the sun, 1 its mirror through the centre), smallest and largest size, turns with the line.
    const parts = [[1, -.15, .9, .9, 1], [0, 0, .001, .8, 0], [1, .136, 1.2, 1.2, 1], [2, .182, 1, 1, 1], [4, .304, 1, 1, 1], [4, .405, 1, 1, 1],
      [3, .606, 1, 1, 1], [3, .682, .7, .7, 1], [5, .841, .7, .7, 1], [1, .932, .7, .7, 1], [1, 1, 1, 1, 1]];
    const quad = new THREE.PlaneGeometry(2, 2);
    const place = (material, order) => {
      const mesh = new THREE.Mesh(quad, Object.assign(material, {transparent: true, depthTest: false}));
      mesh.matrixAutoUpdate = false;
      mesh.renderOrder = order;
      overlay.add(mesh);
      return mesh;
    };
    const meshes = parts.map(([bitmap], order) => place(new THREE.MeshBasicMaterial({map: textures[bitmap]}), order));
    const wash = place(new THREE.MeshBasicMaterial({color: new THREE.Color(...planet.intensity)}), parts.length);
    const set = (mesh, a, b, c, d, x, y) => { mesh.matrix.set(a, b, 0, x, c, d, 0, y, 0, 0, 1, 0, 0, 0, 0, 1); mesh.matrixWorldNeedsUpdate = true; };
    const direction = toward(planet.azimuth, planet.incidence), ray = new THREE.Raycaster(), point = new THREE.Vector3();
    let frame = 0, blocked = false;
    flare = (width, height) => {
      overlay.visible = false;
      if (camera.getWorldDirection(point).dot(direction) <= 0) return;
      point.copy(camera.position).add(direction).project(camera);
      const sunX = (point.x + 1) / 2 * width, sunY = (point.y + 1) / 2 * height, dx = width - 2 * sunX, dy = height - 2 * sunY;
      const fraction = 2 * Math.hypot(dx, dy) / Math.min(width, height);
      if (fraction >= 1) return;
      if (frame++ % 15 === 0) {  // The game also checks the line of sight only now and then.
        ray.set(camera.position, direction);
        blocked = ray.intersectObjects(occluders, false).length > 0;
        // Terrain is checked by walking the height field: a ray test of its 131,072 triangles per tile takes 30-50 ms.
        point.copy(camera.position);
        for (let reach = 4; !blocked && reach < 9000 && (direction.y <= 0 || point.y < highest); reach += 4) {
          point.copy(camera.position).addScaledVector(direction, reach);
          blocked = ground(point.x, point.z) > point.y;
        }
      }
      if (blocked) return;
      const angle = Math.atan2(-dy, dx) - Math.PI / 2;
      parts.forEach(([bitmap, along, least, most, turns], index) => {
        const half = .5 * (least + (1 - fraction) * (most - least)), image = textures[bitmap].image;
        const across = image.width * width / 640 * half, up = image.height * height / 480 * half;
        const cos = turns ? Math.cos(angle) : 1, sin = turns ? Math.sin(angle) : 0;
        set(meshes[index], across * cos, across * sin, -up * sin, up * cos, sunX + dx * along, sunY + dy * along);
        meshes[index].material.opacity = .5 * (1 - fraction);
      });
      wash.material.opacity = Math.max(0, 3.3 * (.3 - fraction));
      set(wash, width / 2, 0, 0, height / 2, width / 2, height / 2);
      overlayCamera.right = width;
      overlayCamera.top = height;
      overlayCamera.updateProjectionMatrix();
      overlay.visible = true;
    };
  }

  // Rain or snow: particles in a box around the camera that wrap as they or the camera leave it. Counts, box size
  // and fall speeds follow ArenaPrototype's precipitation. The Weather list (/t1-map-weather) swaps in another map's:
  // a T1-family mission's or a Quake III family map's through the same box, a Tribes 2 mission's as the T2 page draws it.
  let weather = null, weathers = [];
  const weatherGroup = new THREE.Group();
  scene.add(weatherGroup);
  const shelters = [], shelterFiles = new Map(), shelterQueue = [];  // Buildings that keep it out, their files by name, and every placed one.
  async function addShelter(building) {
    if (building.read) return;
    building.read = true;
    try {
      if (!shelterFiles.has(building.shelter)) shelterFiles.set(building.shelter, get(data + 'textures/' + building.shelter).then(response => response.arrayBuffer()));
      // The layout is in the importer's pack_shelter: box, plane and node counts, planes, nodes, a byte per leaf.
      const buffer = await shelterFiles.get(building.shelter), [planes, nodes] = new Int32Array(buffer, 24, 2);
      shelters.push({inverse: building.matrix.clone().invert(), box: new Float32Array(buffer, 0, 6),
        planes: new Float32Array(buffer, 32, planes * 4), nodes: new Int16Array(buffer, 32 + planes * 16, nodes * 3),
        leaves: new Uint8Array(buffer, 32 + planes * 16 + nodes * 6)});
    } catch (_) { /* Rain simply falls through this building. */ }
  }
  const local = new THREE.Vector3(), ahead = new THREE.Vector3();
  function shelterNear() {
    // Buildings keep it out as in the game (Snowfall::processQuery and InteriorShape::getWeatherDistance, which
    // ArenaPrototype's TribesWeatherInterior ports): with the camera inside a building's box, nothing falls unless the
    // BSP leaf it is in sees a face of the box, and then only from `near` ahead, the way out the camera looks toward.
    let near = 2;
    camera.getWorldDirection(ahead);
    for (const {inverse, box, planes, nodes, leaves} of shelters) {
      const [x, up, back] = local.copy(camera.position).applyMatrix4(inverse).toArray(), point = [x, -back, up];  // The building's own axes, z up.
      if (point.some((value, axis) => value < box[axis] || value > box[axis + 3])) continue;
      let node = nodes.length ? 0 : 1;
      for (let visits = nodes.length; node >= 0 && visits; visits--) {
        const plane = (nodes[node * 3] & 0xffff) * 4;
        node = nodes[node * 3 + (planes[plane] * point[0] + planes[plane + 1] * point[1] + planes[plane + 2] * point[2] > -planes[plane + 3] ? 1 : 2)];
      }
      const faces = node < 0 && leaves[-node - 1];
      if (!faces) return Infinity;
      const [turnX, turnUp, turnBack] = local.copy(ahead).transformDirection(inverse).toArray(), facing = [turnX, -turnBack, turnUp];
      // The game's own arithmetic, slips included: it measures from the box's minimum x on every axis.
      let reach = -1;
      for (let axis = 0; axis < 3; axis++) {
        if (faces & 0x80 >> axis && facing[axis] <= 0 && point[axis] - box[axis] > reach) reach = point[axis] - box[0];
        if (faces & 0x10 >> axis && facing[axis] >= 0 && box[axis] - point[axis] > reach) reach = box[axis + 3] - point[axis];
      }
      if (reach > 0) near = Math.max(near, reach);
    }
    return near;
  }
  // A T1 mission's weather as the box draws it: rain a short streak along its fall, snow a flake.
  const t1Box = ({rain, intensity, wind}) => ({rain, count: Math.floor(intensity * (rain ? 2048 : 1024)), radius: rain ? 110 : 200,
    step: [wind[0] * (rain ? 2 : 1), rain ? -150 : -35, -wind[1] * (rain ? 2 : 1)], streak: .03,
    material: rain ? new THREE.LineBasicMaterial({color: 0xaaaaaa, transparent: true, opacity: .55}) : new THREE.PointsMaterial({color: 0xffffff, size: .5})});
  // A Quake III family map's (q3-maps/viewer.js addWeather): its counts, speeds, streak lengths and colours, falling in
  // the box rather than between its sky and ground. ponytail: one game unit taken as 1/32 m (a 56-unit player is
  // about 1.8 m tall), and its 1000-unit reach as the box; a look, not parity.
  const Q3_UNIT = 1 / 32;
  const q3Box = ({type, count, speed, length, wind}, identity) => ({rain: type === 'rain', count: Math.min(count, 4000), radius: 1000 * Q3_UNIT,
    step: [wind[0] * Q3_UNIT, -speed * Q3_UNIT, -wind[1] * Q3_UNIT], streak: length / speed,
    material: type === 'rain' ? new THREE.LineBasicMaterial({color: new THREE.Color().setScalar(identity * .5), transparent: true, opacity: .4})
      : new THREE.PointsMaterial({color: new THREE.Color().setScalar(identity * 1.5), size: length * Q3_UNIT, transparent: true, opacity: .8})});
  function buildBox({rain, count, radius, step, streak, material}) {
    const size = radius * 2, drops = Float32Array.from({length: count * 3}, () => Math.random() * size);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * (rain ? 6 : 3)), 3));
    const particles = rain ? new THREE.LineSegments(geometry, material) : new THREE.Points(geometry, material);
    particles.frustumCulled = false;
    weatherGroup.add(particles);
    return (delta, near) => {
      particles.visible = near < radius;
      if (!particles.visible) return;
      const positions = geometry.attributes.position.array, centre = camera.position.toArray(), view = ahead.toArray();
      for (let drop = 0; drop < count; drop++) {
        let depth = 0;
        for (let axis = 0; axis < 3; axis++) {
          const index = drop * 3 + axis, offset = drops[index] + step[axis] * delta - centre[axis] + radius;
          drops[index] = centre[axis] + (offset % size + size) % size - radius;
          depth += (drops[index] - centre[axis]) * view[axis];
        }
        for (let axis = 0; axis < 3; axis++) {
          // A drop nearer than that is not drawn: it is put behind the camera.
          const at = rain ? drop * 6 + axis : drop * 3 + axis;
          positions[at] = depth < near ? centre[axis] - view[axis] : drops[drop * 3 + axis];
          if (rain) positions[at + 3] = positions[at] - (depth < near ? 0 : step[axis] * streak);
        }
      }
      geometry.attributes.position.needsUpdate = true;
    };
  }

  // Tribes 2 precipitation, transcribed from the T2 page (map-client/environment.tsx Precipitation, after V12's
  // game/precipitation.cc renderPrecip for an observer camera): camera-facing quads from a 4x4 atlas of drops, in a box
  // about the camera, each moved by its speed once per game frame. Torque and Tribes share axes and metres, so a
  // Torque (x, y, z) here is Three (x, z, -y). The shelter test hides drops as it does the box's.
  const T2_FRAME_RATE = 290;
  const T2_VERTEX = `attribute vec4 dropColor; varying vec4 vColor; varying vec2 vUv;
    void main() { vColor = dropColor; vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
  const T2_FRAGMENT = `uniform sampler2D map; varying vec4 vColor; varying vec2 vUv;
    void main() { vec4 c = texture2D(map, vUv) * vColor; if (c.a <= 0.1) discard; gl_FragColor = c; }`;
  function buildPrecipitation({object, datablock: db, sky}, base) {
    const num = (obj, name, fallback) => { const value = parseFloat(obj?.[name]); return Number.isFinite(value) ? value : fallback; };
    const truthy = value => value !== undefined && !/^(0|false|)$/i.test(String(value));
    const vec = (text, fallback) => { const parts = String(text ?? '').trim().split(/\s+/).map(Number); return fallback.map((value, i) => Number.isFinite(parts[i]) ? parts[i] : value); };
    const random = (min, max) => min + Math.random() * (max - min);
    const toTorque = v => [v.x, -v.z, v.y];
    const type = num(db, 'type', 0);
    const colors = [1, 2, 3].map(i => vec(object['color' + i], [-1, 0, 0, 1])).filter((c, i, all) => all.slice(0, i + 1).every(item => item[0] >= 0));
    if (!colors.length) colors.push(type === 0 ? [0.6, 0.6, 0.6, 1] : type === 1 ? [1, 1, 1, 1] : [0.9, 0.8, 0.5, 1]);
    const maxDrops = Math.min(2000, Math.max(0, num(object, 'maxNumDrops', -1) < 0 ? 2000 : num(object, 'maxNumDrops', 2000)));
    const percentage = Math.min(1, Math.max(0, num(object, 'percentage', 1)));
    const minVelocity = num(object, 'minVelocity', -1) >= 0 ? num(object, 'minVelocity', 0) : type === 0 ? 1.25 : 0.25;
    const maxVelocity = num(object, 'maxVelocity', -1) >= 0 ? num(object, 'maxVelocity', 0) : type === 0 ? 4 : type === 1 ? 1.5 : 1;
    const radius = num(object, 'maxRadius', -1) >= 0 ? num(object, 'maxRadius', 0) : type === 1 ? 125 : 80;
    const atlas = String(db.materialList ?? '').toLowerCase().startsWith('snow') ? 'precipitation/snowflakes' : 'precipitation/raindrops';
    const wind = vec(sky.windVelocity, [0, 0, 0]), windLength = Math.hypot(wind[0], wind[1]);
    const offsetSpeed = num(object, 'offsetSpeed', 0.25);
    const offset = truthy(sky.windEffectPrecipitation) && windLength > 0 ? [wind[0] / windLength * offsetSpeed, wind[1] / windLength * offsetSpeed, 1] : [0, 0, 1];
    const count = Math.round(maxDrops * percentage), sizeX = num(db, 'sizeX', 1), sizeY = num(db, 'sizeY', 1), divHeight = num(db, 'divHeightVal', 1.5) || 1.5;
    const geometry = new THREE.BufferGeometry(), index = new Uint32Array(count * 6);
    for (let i = 0; i < count; i++) index.set([i * 4, i * 4 + 1, i * 4 + 2, i * 4, i * 4 + 2, i * 4 + 3], i * 6);
    geometry.setIndex(new THREE.BufferAttribute(index, 1));
    for (const [name, size] of [['position', 3], ['uv', 2], ['dropColor', 4]])
      geometry.setAttribute(name, new THREE.BufferAttribute(new Float32Array(count * 4 * size), size).setUsage(THREE.DynamicDrawUsage));
    const material = new THREE.ShaderMaterial({vertexShader: T2_VERTEX, fragmentShader: T2_FRAGMENT, uniforms: {map: {value: null}},
      transparent: true, depthWrite: false, blending: THREE.NormalBlending, side: THREE.DoubleSide});
    const mesh = new THREE.Mesh(geometry, material);
    mesh.frustumCulled = false;
    mesh.renderOrder = 5;
    mesh.visible = false;  // Until its atlas arrives.
    weatherGroup.add(mesh);
    let ready = false;
    loadTexture(base + 'textures/' + atlas + '.png').then(texture => { material.uniforms.map.value = texture; ready = Boolean(texture); });
    const drops = Array.from({length: count}, () => ({pos: [0, 0, 0], speed: 0, color: 0, tex: 0, startDiff: 1, valid: false}));
    const last = {camera: null, first: true}, right = new THREE.Vector3(), up = new THREE.Vector3(), z = new THREE.Vector3();
    return (delta, near) => {
      mesh.visible = ready && near < radius;
      if (!mesh.visible) return;
      const cam = toTorque(camera.position), view = ahead.toArray();
      const frames = Math.min(delta, 0.25) * T2_FRAME_RATE;
      // An observer camera has no player velocity: the box is the camera's, shifted upwind by the drift.
      const shift = last.camera && Math.hypot(...cam.map((v, i) => v - last.camera[i])) > radius / 2 ? cam.map((v, i) => v - last.camera[i]) : [0, 0, 0];
      last.camera = cam;
      const min = [cam[0] - radius, cam[1] - radius, cam[2] - radius / divHeight];
      const max = [cam[0] + radius, cam[1] + radius, cam[2] + radius / divHeight];
      const center = [offset[0] * radius, offset[1] * radius];
      camera.matrixWorld.extractBasis(right, up, z);
      const r = toTorque(right).map(v => v * sizeX), u = toTorque(up).map(v => v * sizeY);
      // Quad corners around a drop: snow is a camera-facing square; a raindrop stretches 2 * sizeY along its fall.
      const c1 = r.map((v, k) => v - u[k]), c2 = r.map((v, k) => -v - u[k]);
      const corners = type === 1 ? [r.map((v, k) => v + u[k]), c1, c2, r.map((v, k) => -v + u[k])]
        : [c1.map((v, k) => v + offset[k] * sizeY * 2), c1, c2, c2.map((v, k) => v + offset[k] * sizeY * 2)];
      const p = geometry.attributes.position.array, t = geometry.attributes.uv.array, c = geometry.attributes.dropColor.array;
      drops.forEach((drop, i) => {
        for (let k = 0; k < 3; k++) drop.pos[k] += shift[k] + (k < 2 ? offset[k] * drop.speed : drop.speed) * frames;
        const inside = drop.pos.every((v, k) => v >= min[k] && v <= max[k]);
        if (!drop.valid || !inside) {
          const r1 = Math.random(), r2 = Math.random(), r3 = Math.random();
          const angle = Math.random() * 2 * Math.PI;
          const along = radius * Math.min(1, Math.max(0.01, r2 < 0.2 ? r3 : r2));
          drop.speed = -minVelocity - (maxVelocity - minVelocity) * r3;
          // The first frame fills the whole box, as V12's mRandomHeight intends, rather than starting every drop at the top.
          const height = last.first ? random(min[2], max[2] - 5) : max[2] - 5;
          drop.pos = [cam[0] + center[0] + Math.cos(angle) * along, cam[1] + center[1] + Math.sin(angle) * along, height];
          drop.startDiff = height - cam[2] + 20;
          drop.color = Math.max(0, Math.ceil(colors.length * r1) - 1);
          const pick = Math.random();
          drop.tex = Math.max(0, Math.ceil(15 * (pick > 0.99995 && pick < 0.99999 ? 1.01 : pick)) - 1);
          drop.valid = true;
        }
        // Torque (x, y, z) is written as Three (x, z, -y).
        for (let k = 0; k < 4; k++) {
          const o = (i * 4 + k) * 3;
          p[o] = drop.pos[0] + corners[k][0]; p[o + 1] = drop.pos[2] + corners[k][2]; p[o + 2] = -(drop.pos[1] + corners[k][1]);
        }
        // loadTexture leaves flipY off, so v = 0 is the image's top row, as GL's t = 0 is.
        const cx = (drop.tex % 4) * 0.25, cy = Math.floor(drop.tex / 4) * 0.25, o = i * 8;
        t[o] = cx; t[o + 1] = cy; t[o + 2] = cx; t[o + 3] = cy + 0.25; t[o + 4] = cx + 0.25; t[o + 5] = cy + 0.25; t[o + 6] = cx + 0.25; t[o + 7] = cy;
        const color = colors[drop.color];
        // Nearer than the shelter's `near` along the view, not drawn.
        const depth = (drop.pos[0] - cam[0]) * view[0] + (drop.pos[2] - cam[2]) * view[1] - (drop.pos[1] - cam[1]) * view[2];
        const alpha = depth < near ? 0 : 1 - (drop.pos[2] - cam[2]) / drop.startDiff;
        for (let k = 0; k < 4; k++) { const q = (i * 4 + k) * 4; c[q] = color[0]; c[q + 1] = color[1]; c[q + 2] = color[2]; c[q + 3] = alpha; }
      });
      last.first = false;
      geometry.attributes.position.needsUpdate = geometry.attributes.uv.needsUpdate = geometry.attributes.dropColor.needsUpdate = true;
    };
  }

  // The chosen weather: '' the map's own, 'none', or a Weather list entry's id.
  function showWeather(id) {
    for (const mesh of weatherGroup.children) { mesh.geometry.dispose(); mesh.material.dispose(); }  // Textures stay cached.
    weatherGroup.clear();
    weather = null;
    const chosen = weathers.find(entry => entry.id === id), steps = [];
    if (!id && map.weather) steps.push(buildBox(t1Box(map.weather)));
    else if (chosen?.renderer === 't1') steps.push(buildBox(t1Box(chosen.weather)));
    else if (chosen?.renderer === 'q3') steps.push(...chosen.weather.specs.map(spec => buildBox(q3Box(spec, chosen.weather.identity))));
    else if (chosen?.renderer === 't2') steps.push(buildPrecipitation(chosen.weather, chosen.base));
    if (!steps.length) return;
    for (const building of shelterQueue) addShelter(building);
    weather = delta => { const near = shelterNear(); for (const step of steps) step(delta, near); };
  }

  function applySettings() {
    for (const id of ['fog', 'lights', 'invertX', 'invertY']) $(id).checked = settings[id];
    $('fov').value = settings.fov;
    const main = canvas.parentElement, aspect = main.clientWidth / Math.max(main.clientHeight, 1);
    renderer.setSize(main.clientWidth, main.clientHeight, false);
    camera.aspect = aspect;
    // Tribes field of view is horizontal; Three's is vertical.
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2) / aspect));
    if (map) {
      // DarkStar (SimTerrain::applyUserPrefs) starts the haze at h²/v, at most 0.75 v; other packs at h, or with no haze
      // band when a mission sets h beyond v.
      const {visibleDistance: visible, hazeDistance: hazeAt} = map.terrain;
      const start = map.terrain.hazeModel === 'darkstar' ? Math.min(hazeAt * hazeAt / visible, visible * .75) : Math.min(hazeAt, visible - 1);
      scene.fog = settings.fog ? new THREE.Fog(haze, start, visible) : null;
      camera.far = settings.fog ? map.terrain.visibleDistance : (adapter ? 25000 : 9000);
    }
    camera.updateProjectionMatrix();
  }
  function showViewpoint(index) {
    if (!map || !map.viewpoints[index]) return;
    const matrix = new THREE.Matrix4().fromArray(map.viewpoints[index]);
    camera.position.setFromMatrixPosition(matrix);
    camera.rotation.setFromRotationMatrix(matrix, 'YXZ');
    camera.rotation.z = 0;
  }

  $('fov').addEventListener('change', event => { settings.fov = Math.max(30, Math.min(110, Number(event.target.value) || 90)); save(); applySettings(); });
  for (const id of ['fog', 'lights', 'invertX', 'invertY']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
  $('reset').addEventListener('click', () => { speed = 40; showViewpoint(0); if (ready) showReady(); });
  new ResizeObserver(applySettings).observe(canvas.parentElement);

  // Captured mouse and plain drag share one look function: mouse right looks right, mouse up looks up.
  const keys = new Set();
  let dragging = false;
  canvas.addEventListener('mousedown', () => {
    dragging = true;
    try { Promise.resolve(canvas.requestPointerLock()).catch(() => { /* Drag-to-look still works. */ }); } catch (_) { /* Same. */ }
  });
  window.addEventListener('mouseup', () => { dragging = false; });
  window.addEventListener('blur', () => { dragging = false; keys.clear(); });
  document.addEventListener('mousemove', event => {
    if (!dragging && document.pointerLockElement !== canvas) return;
    camera.rotation.y -= event.movementX * .0025 * (settings.invertX ? -1 : 1);
    camera.rotation.x = Math.max(-1.55, Math.min(1.55, camera.rotation.x - event.movementY * .0025 * (settings.invertY ? -1 : 1)));
  });
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    speed = Math.max(2, Math.min(2000, speed * (event.deltaY < 0 ? 1.2 : 1 / 1.2)));
    if (ready) showReady();
  }, {passive: false});
  document.addEventListener('keydown', event => {
    if (event.target.matches('input:not([type=checkbox]), select')) return;
    if (/^Digit[1-9]$/.test(event.code)) showViewpoint(Number(event.code.slice(5)) - 1);
    if (/^(Key[WASD]|Space|Shift(Left|Right))$/.test(event.code)) { keys.add(event.code); event.preventDefault(); }
  });
  document.addEventListener('keyup', event => keys.delete(event.code));

  const clock = new THREE.Clock(), forward = new THREE.Vector3(), right = new THREE.Vector3(), move = new THREE.Vector3();
  let lightTime = 0;
  function drawFrame() {
    // A Skinner Sky's height fog: sky and world into its target, then through the fog onto the screen.
    skyPass?.update?.(renderer);
    const fogged = settings.fog && skyPass?.fog;
    if (fogged) renderer.setRenderTarget(skyPass.fog.target(renderer));
    if (skyPass?.render) {  // The other game's sky first, then the world over it without clearing.
      skyPass.render(renderer, camera, skyTime.value);
      renderer.clearDepth();
      renderer.autoClear = false;
      renderer.render(scene, camera);
      renderer.autoClear = true;
    } else renderer.render(scene, camera);
    if (fogged) skyPass.fog.draw(renderer, camera);
  }
  // For checks in a script or a hidden page, as the other Maps pages have: draw a frame and pick a sky by id.
  window.skinnerT1Maps = {renderer, scene, camera, drawFrame, showSky: id => showSky(id), skies: () => skies, skyPass: () => skyPass};
  renderer.setAnimationLoop(() => {
    const delta = Math.min(clock.getDelta(), .1), held = code => Number(keys.has(code));
    // The game steps building lights 67 ms at a time (InteriorShape::sm_minLightUpdateMS); switched off, they hold still.
    if (settings.lights) for (lightTime += delta; lightTime >= .067; lightTime -= .067) for (const step of lightSteps) step(.067);
    camera.getWorldDirection(forward);
    right.crossVectors(forward, camera.up).normalize();
    move.copy(forward).multiplyScalar(held('KeyW') - held('KeyS')).addScaledVector(right, held('KeyD') - held('KeyA'));
    move.y += held('Space') - held('ShiftLeft') - held('ShiftRight');
    if (move.lengthSq()) camera.position.addScaledVector(move.normalize(), speed * delta);
    sky.position.copy(camera.position);
    skyTime.value += delta;
    if (weather) weather(delta);
    if (pointLights.length) stepPointLights(clock.elapsedTime);
    for (const step of animators) step(clock.elapsedTime);
    for (const mesh of spinners) mesh.rotateOnWorldAxis(up, 3 * delta);
    if (streamFrame) streamFrame(delta);
    drawFrame();
    if (flare && own.visible) flare(canvas.clientWidth, canvas.clientHeight);
    if (flare && own.visible && overlay.visible) {
      renderer.autoClear = false;
      renderer.render(overlay, overlayCamera);
      renderer.autoClear = true;
    }
  });

  $('map').addEventListener('change', event => { location.search = '?map=' + encodeURIComponent(event.target.value); });
  if (!adapter) $('sky').addEventListener('change', event => { settings.sky = event.target.value; event.target.blur(); save(); if (map) showSky(settings.sky); });
  $('import').addEventListener('click', async () => {
    const game = $('gamePath').value.trim();
    if (!game) { $('importStatus').textContent = adapter ? 'Enter your Red Baron 3D folder.' : `Enter your ${site.folder} folder.`; return; }
    $('import').disabled = true;
    $('importStatus').textContent = 'Importing maps… a full install can take a few minutes.';
    try {
      const response = await fetch(adapter ? '/import_rb3d_maps' : site.importRoute, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({game, missions: $('missionPath').value.trim(), replace: $('replace').checked})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      try { localStorage.setItem(storageKey + '.game', game); } catch (_) { /* Path is simply not remembered. */ }
      const failed = Object.entries(result.failed).map(([name, reason]) => `${name}: ${reason}`);
      $('importStatus').textContent = `Imported ${result.imported.length}, skipped ${result.skipped.length} already imported` +
        (failed.length ? `, failed ${failed.length} (${failed.join('; ')})` : '') + '.';
      if (adapter && result.imported.length && failed.length) {
        const link = document.createElement('a');
        link.href = '?map=' + encodeURIComponent(result.imported[0]);
        link.textContent = ' Open imported maps';
        $('importStatus').append(link);
      }
      if (result.imported.length && !failed.length) location.search = '?map=' + encodeURIComponent(result.imported[0].toLowerCase().replace(/[^a-z0-9_-]/g, '_'));
    } catch (error) { $('importStatus').textContent = error.message; }
    finally { $('import').disabled = false; }
  });
  try { $('gamePath').value = localStorage.getItem(storageKey + '.game') || ''; } catch (_) { /* Field stays empty. */ }

  try {
    let maps = [];
    try { maps = await (await get(data + 'index.json')).json(); } catch (_) { /* No pack yet. */ }
    if (!maps.length) { $('importPanel').open = true; throw new Error('no maps imported yet. Use Import maps above.'); }
    mapId = new URLSearchParams(location.search).get('map') || '';
    if (!maps.some(item => item.id === mapId)) mapId = (maps.find(item => item.id === site.firstMap) || maps[0]).id;
    $('map').replaceChildren(...maps.map(item => new Option(item.type ? `${item.name} · ${item.type}` : item.name, item.id)));
    $('map').value = mapId;
    const entry = maps.find(item => item.id === mapId);
    if (adapter && entry.error) throw new Error(entry.error + '. See the import report.');
    if (adapter && entry.pack) data += entry.pack + '/';
    map = await (await get(`${data}maps/${mapId}/scene.json`)).json();
    document.title = `${map.mission} — ${adapter ? 'Red Baron 3D Maps' : site.title}`;
    scene.background = haze.set(`rgb(${map.haze.join(',')})`);
    if (map.terrain.hazeModel === 'darkstar') {
      // GridRender::getHaze: by distance from the eye, not depth, along 1 - (t - 1)² = t(2 - t), with t reaching
      // 1 at 1.02 times the band. Set before anything fogged compiles; every material on the page shares these chunks.
      THREE.ShaderChunk.fog_vertex = '#ifdef USE_FOG\n\tvFogDepth = length(mvPosition.xyz);\n#endif';
      THREE.ShaderChunk.fog_fragment = `#ifdef USE_FOG
        float hazeT = clamp((vFogDepth - fogNear) / (1.02 * (fogFar - fogNear)), 0., 1.);
        gl_FragColor.rgb = mix(gl_FragColor.rgb, fogColor, hazeT * (2. - hazeT));
      #endif`;
    }
    if (adapter) {
      applySettings();
      showViewpoint(0);
      streamFrame = await adapter.start({scene, camera, map, data, get, loadTexture, showStatus});
      $('notes').textContent = [map.briefing || '', ...map.warnings].filter(Boolean).join('\n\n');
      ready = true;
      showReady();
      return;
    }
    // Packs imported before the sky and its objects were added lack these keys.
    const radius = map.terrain.visibleDistance * 1.05;
    await showSky('');
    await buildSkyObjects(radius);
    const flared = (map.planets || []).find(planet => planet.flare);
    if (flared && map.flare) await buildFlare(flared, map.flare);
    scene.add(sky);
    // The list of other skies comes after the map's own: the first scan reads every imported pack /t1-map-skies lists.
    // Skinner Sky presets (static/skinner-sky) join them as one more group.
    const own = get('/static/skinner-sky/presets.json').then(response => response.json()).then(presets => presets.map(sky => ({id: 'skinner-' + sky.id,
      game: 'skinner', group: 'Skinner Sky', name: sky.name, maps: ['procedural'], renderer: 'skinner', base: '/static/skinner-sky/', sky})), () => []);
    Promise.all([get('/t1-map-skies').then(response => response.json()), own]).then(([found, presets]) => {
      const list = skies = [...found, ...presets];
      // One group per game, games and their skies in alphabetical order.
      const groups = [...Map.groupBy(list, entry => entry.group)].sort(([a], [b]) => a.localeCompare(b));
      // The game first, then its skies: one list of 900+ skies is slow to open and some browsers leave it half drawn.
      const game = $('skyGame') || $('sky').insertAdjacentElement('beforebegin', Object.assign(document.createElement('select'),
        {id: 'skyGame', title: 'Game the sky comes from'}));
      game.setAttribute('aria-label', 'Sky source game');
      game.replaceChildren(new Option(`${map.mission}'s own (default)`, ''), ...groups.map(([group, entries]) => new Option(`${group} (${entries.length})`, group)));
      const fill = group => {
        const entries = groups.find(([name]) => name === group)?.[1] || [];
        $('sky').replaceChildren(...entries.map(entry => [listText(entry), entry.id]).sort(([a], [b]) => a.localeCompare(b)).map(([label, id]) => new Option(label, id)));
        $('sky').hidden = !group;
      };
      game.addEventListener('change', () => {
        fill(game.value);
        settings.sky = game.value ? $('sky').value : '';  // A game's first sky, or the map's own.
        game.blur();
        save();
        showSky(settings.sky);
      });
      const saved = skies.find(entry => entry.id === settings.sky);
      game.value = saved ? saved.group : '';
      fill(game.value);
      if (saved) { $('sky').value = settings.sky; return showSky(settings.sky); }
    }).catch(() => { $('sky').disabled = true; });
    scene.add(new THREE.AmbientLight(new THREE.Color(...map.sun.ambient)));
    if (map.sun.ramp) {
      rampUniforms.t1Toward.value.copy(toward(map.sun.azimuth, map.sun.incidence));
      rampUniforms.t1Ramp.value = map.sun.ramp.map(([row, light]) => new THREE.Vector2(row, light));
    }
    pointLights = (map.lights || []).map(light => {  // Packs imported before point lights have none.
      const shape = new THREE.PointLight(new THREE.Color(...light.color), 1, light.range, 1);
      shape.position.fromArray(light.position);
      scene.add(shape);
      const uniform = light.static ? null : new THREE.Color(...light.color);
      if (uniform) {
        lightUniforms.t1LightPos.value.push(new THREE.Vector3(...light.position));
        lightUniforms.t1LightColor.value.push(uniform);
        lightUniforms.t1LightRange.value.push(light.range);
      }
      return {...light, shape, uniform};
    });
    dynamicLights = pointLights.filter(light => light.uniform);
    const sun = new THREE.DirectionalLight(new THREE.Color(...map.sun.intensity));
    sun.position.copy(toward(map.sun.azimuth, map.sun.incidence));  // Shades only what has no lightmap.
    scene.add(sun);
    // Weather: the map's own (most missions have none) or the saved choice, then the list of other maps' and games'
    // from the Sky list's scan, as two selects like the sky's: the game, then its weathers.
    const weatherGame = $('weatherGame') || $('weather').insertAdjacentElement('beforebegin', Object.assign(document.createElement('select'),
      {id: 'weatherGame', title: 'Game the weather comes from'}));
    weatherGame.setAttribute('aria-label', 'Weather source game');
    weatherGame.replaceChildren(new Option(`${map.mission}'s own (${map.weather ? map.weather.rain ? 'rain' : 'snow' : 'none'})`, ''), new Option('None', 'none'));
    weatherGame.value = settings.weather === 'none' ? 'none' : '';
    $('weather').hidden = true;
    if (settings.weather === 'none' || !settings.weather) showWeather(settings.weather);
    let groups = [];  // Own and None work before the list arrives, or without it.
    const fill = group => {
      const entries = groups.find(([name]) => name === group)?.[1] || [];
      $('weather').replaceChildren(...entries.map(entry => [listText(entry), entry.id]).sort(([a], [b]) => a.localeCompare(b)).map(([label, id]) => new Option(label, id)));
      $('weather').hidden = !entries.length;
    };
    weatherGame.addEventListener('change', () => {
      fill(weatherGame.value);
      settings.weather = weatherGame.value === 'none' ? 'none' : weatherGame.value ? $('weather').value : '';  // A game's first, or the map's own.
      weatherGame.blur();
      save();
      showWeather(settings.weather);
    });
    $('weather').addEventListener('change', event => { settings.weather = event.target.value; event.target.blur(); save(); showWeather(settings.weather); });
    get('/t1-map-weather').then(response => response.json()).then(list => {
      weathers = list;
      groups = [...Map.groupBy(list, entry => entry.group)].sort(([a], [b]) => a.localeCompare(b));
      weatherGame.append(...groups.map(([group, entries]) => new Option(`${group} (${entries.length})`, group)));
      const saved = weathers.find(entry => entry.id === settings.weather);
      if (saved) {
        weatherGame.value = saved.group;
        fill(saved.group);
        $('weather').value = saved.id;
        showWeather(saved.id);
      } else if (settings.weather !== 'none' && settings.weather) showWeather('');  // No longer imported: the map's own.
    }).catch(() => { if (settings.weather !== 'none' && settings.weather) showWeather(''); });
    applySettings();
    showViewpoint(0);
    showStatus('Loading terrain…');
    await buildTerrain(map.terrain);
    let loaded = 0;
    await Promise.all(map.objects.map(async object => { await addObject(object); showStatus(`Loading map objects ${++loaded}/${map.objects.length}`); }));
    if (npcToggle) npcToggle.setCount(npcIDs.size);
    $('lights').disabled = !lightSteps.length;  // Most buildings have no lights that change.
    $('notes').textContent += map.warnings.length ? ' This map: ' + map.warnings.join('. ') + '.' : '';
    ready = true;
    showReady();
  } catch (error) { if (npcToggle) npcToggle.setCount(0); showStatus('Map could not load: ' + error.message); }
});

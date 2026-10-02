/* Vanilla Three.js free-flight viewer for the pack written by tools/import_t1_map.py.
   The pack is already in viewer space: Tribes (x, y, z-up) -> Three (x, z, -y). */
'use strict';
window.addEventListener('DOMContentLoaded', async () => {
  const $ = id => document.getElementById(id);
  const storageKey = 'skinner.t1maps';
  const settings = {fov: 90, fog: true, weather: true, invertX: false, invertY: false};
  try { Object.assign(settings, JSON.parse(localStorage.getItem(storageKey) || '{}')); } catch (_) { /* Defaults remain usable. */ }
  const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(settings)); } catch (_) { /* Storage may be unavailable. */ } };

  const canvas = $('c');
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 1, .2, 9000);
  camera.rotation.order = 'YXZ';
  const data = '/t1-map-data/';
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
    if (!textures.has(url)) textures.set(url, new Promise(resolve => loader.load(url, texture => {
      texture.flipY = false;
      texture.anisotropy = 8;
      if (repeat) texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      resolve(texture);
    }, undefined, () => { missing++; resolve(null); })));
    return textures.get(url);
  }

  let ground = () => -Infinity, highest = -Infinity;  // Terrain height under a point and its peak, for sight lines.
  async function buildTerrain(terrain) {
    const [heightData, squareData, lightData] = await Promise.all(['heights.bin', 'materials.bin', 'light.bin']
      .map(async name => (await get(`${data}maps/${mapId}/${name}`)).arrayBuffer()));
    const heights = new Float32Array(heightData), squares = new Uint8Array(squareData), light = new Uint16Array(lightData);
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
        uvs.set(corners[squares[square * 2] & 7][corner], n * 8 + corner * 2);
        const packed = light[py * lightStep * terrain.lightWidth + px * lightStep];
        colors.set([(packed >> 8 & 15) / 15, (packed >> 4 & 15) / 15, (packed & 15) / 15], n * 12 + corner * 3);
      });
      // DarkStar alternates the split diagonal in a checkerboard.
      indices.set(((x ^ y) & 1 ? [0, 1, 3, 1, 2, 3] : [0, 1, 2, 0, 2, 3]).map(corner => n * 4 + corner), n * 6);
      if (slots[slots.length - 1] !== slot) { slots.push(slot); geometry.addGroup(n * 6, 0, slots.length - 1); }
      geometry.groups[geometry.groups.length - 1].count += 6;
    });
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    geometry.computeBoundingSphere();
    const materials = await Promise.all(slots.map(async slot => {
      const texture = terrain.textures[slot] && await loadTexture(data + 'textures/' + terrain.textures[slot]);
      return new THREE.MeshBasicMaterial(texture ? {map: texture, vertexColors: true} : {color: 0xcc00cc});
    }));
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
      const materials = await Promise.all(model.material_textures.map(async file => {
        const blank = file.startsWith('[');
        const texture = blank || !file ? null : await loadTexture(pack ? data + 'textures/' + file : `/texture/${encodeURIComponent(file)}?game=t1`, true);
        return new THREE.MeshLambertMaterial({map: texture, color: texture ? 0xffffff : blank ? 0x999999 : 0xcc00cc, side: THREE.DoubleSide});
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

  const placeholder = new THREE.BoxGeometry(4, 4, 4).translate(0, 2, 0), magenta = new THREE.MeshBasicMaterial({color: 0xcc00cc});
  async function addObject(object) {
    let mesh;
    try {
      if (!object.model) throw new Error('No preview model');
      const model = await loadModel(object.model, object.source === 'pack', object.light && object.light.uv);
      const lightMap = object.light && await loadLightMap(object.light);
      // The mission lightmap already holds the sun and the building's own lights: texture × lightmap, no scene lights.
      mesh = new THREE.Mesh(model.geometry, !lightMap ? model.materials : model.materials.map(material => new THREE.MeshBasicMaterial(
        {map: material.map, color: material.color, side: THREE.DoubleSide, lightMap})));
    } catch (_) { missing++; mesh = new THREE.Mesh(placeholder, magenta); }
    mesh.name = object.name;
    mesh.applyMatrix4(new THREE.Matrix4().fromArray(object.matrix));
    scene.add(mesh);
    occluders.push(mesh);
  }

  // Sky: sixteen textured panels around the camera with flat caps above and below, drawn behind everything.
  // The dome is scaled down to sit inside the clip range; centred on the camera, its size does not show.
  const sky = new THREE.Group(), haze = new THREE.Color();
  async function buildSky(dome, visible) {
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
      sky.add(mesh);
    };
    await Promise.all(dome.textures.map(async (file, slot) => {
      const texture = file && await loadTexture(data + 'textures/' + file);
      if (texture) add([point(slot, false), point(slot + 1, false), point(slot + 1, true), point(slot, true)],
        [0, 1, 1, 1, 1, 0, 0, 0], [0, 1, 2, 0, 2, 3], new THREE.MeshBasicMaterial({map: texture}));
    }));
    for (const upper of [true, false]) add(
      [[0, upper ? dome.size : -dome.size, 0], ...Array.from({length: 16}, (_, slot) => point(slot, upper))], null,
      Array.from({length: 16}, (_, slot) => [0, slot + 1, (slot + 1) % 16 + 1]).flat(),
      new THREE.MeshBasicMaterial({color: `rgb(${(upper ? dome.top : dome.bottom).join(',')})`}));
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
        sky.add(points);
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
      sky.add(sprite);
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
  // and fall speeds follow ArenaPrototype's precipitation. Buildings do not shelter from it.
  let weather = null;
  function buildWeather({rain, intensity, wind}) {
    const count = Math.floor(intensity * (rain ? 2048 : 1024)), radius = rain ? 110 : 200, size = radius * 2;
    const step = [wind[0] * (rain ? 2 : 1), rain ? -150 : -35, -wind[1] * (rain ? 2 : 1)];
    const drops = Float32Array.from({length: count * 3}, () => Math.random() * size);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * (rain ? 6 : 3)), 3));
    const particles = rain  // Rain is a short streak along its fall, snow a flake.
      ? new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({color: 0xaaaaaa, transparent: true, opacity: .55}))
      : new THREE.Points(geometry, new THREE.PointsMaterial({color: 0xffffff, size: .5}));
    particles.frustumCulled = false;
    scene.add(particles);
    weather = delta => {
      particles.visible = settings.weather;
      if (!settings.weather) return;
      const positions = geometry.attributes.position.array, centre = camera.position.toArray();
      for (let index = 0; index < drops.length; index++) {
        const axis = index % 3, offset = drops[index] + step[axis] * delta - centre[axis] + radius;
        drops[index] = centre[axis] + (offset % size + size) % size - radius;
        const at = rain ? (index - axis) * 2 + axis : index;
        positions[at] = drops[index];
        if (rain) positions[at + 3] = drops[index] - step[axis] * .03;
      }
      geometry.attributes.position.needsUpdate = true;
    };
  }

  function applySettings() {
    for (const id of ['fog', 'weather', 'invertX', 'invertY']) $(id).checked = settings[id];
    $('fov').value = settings.fov;
    const main = canvas.parentElement, aspect = main.clientWidth / Math.max(main.clientHeight, 1);
    renderer.setSize(main.clientWidth, main.clientHeight, false);
    camera.aspect = aspect;
    // Tribes field of view is horizontal; Three's is vertical.
    camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(settings.fov) / 2) / aspect));
    if (map) {
      // Some missions set haze beyond the visible distance: no haze band, just the far limit.
      const visible = map.terrain.visibleDistance;
      scene.fog = settings.fog ? new THREE.Fog(haze, Math.min(map.terrain.hazeDistance, visible - 1), visible) : null;
      camera.far = settings.fog ? map.terrain.visibleDistance : 9000;
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
  for (const id of ['fog', 'weather', 'invertX', 'invertY']) $(id).addEventListener('change', event => { settings[id] = event.target.checked; event.target.blur(); save(); applySettings(); });
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
  renderer.setAnimationLoop(() => {
    const delta = Math.min(clock.getDelta(), .1), held = code => Number(keys.has(code));
    // The game steps building lights 67 ms at a time (InteriorShape::sm_minLightUpdateMS).
    for (lightTime += delta; lightTime >= .067; lightTime -= .067) for (const step of lightSteps) step(.067);
    camera.getWorldDirection(forward);
    right.crossVectors(forward, camera.up).normalize();
    move.copy(forward).multiplyScalar(held('KeyW') - held('KeyS')).addScaledVector(right, held('KeyD') - held('KeyA'));
    move.y += held('Space') - held('ShiftLeft') - held('ShiftRight');
    if (move.lengthSq()) camera.position.addScaledVector(move.normalize(), speed * delta);
    sky.position.copy(camera.position);
    if (weather) weather(delta);
    renderer.render(scene, camera);
    if (flare) flare(canvas.clientWidth, canvas.clientHeight);
    if (flare && overlay.visible) {
      renderer.autoClear = false;
      renderer.render(overlay, overlayCamera);
      renderer.autoClear = true;
    }
  });

  $('map').addEventListener('change', event => { location.search = '?map=' + encodeURIComponent(event.target.value); });
  $('import').addEventListener('click', async () => {
    const game = $('gamePath').value.trim();
    if (!game) { $('importStatus').textContent = 'Enter your Tribes folder.'; return; }
    $('import').disabled = true;
    $('importStatus').textContent = 'Importing maps… a full install can take a few minutes.';
    try {
      const response = await fetch('/import_t1_maps', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({game, missions: $('missionPath').value.trim(), replace: $('replace').checked})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import failed');
      try { localStorage.setItem(storageKey + '.game', game); } catch (_) { /* Path is simply not remembered. */ }
      const failed = Object.entries(result.failed).map(([name, reason]) => `${name}: ${reason}`);
      $('importStatus').textContent = `Imported ${result.imported.length}, skipped ${result.skipped.length} already imported` +
        (failed.length ? `, failed ${failed.length} (${failed.join('; ')})` : '') + '.';
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
    if (!maps.some(item => item.id === mapId)) mapId = (maps.find(item => item.id === 'raindance') || maps[0]).id;
    $('map').replaceChildren(...maps.map(item => new Option(item.type ? `${item.name} · ${item.type}` : item.name, item.id)));
    $('map').value = mapId;
    map = await (await get(`${data}maps/${mapId}/scene.json`)).json();
    document.title = `${map.mission} — T1 Maps`;
    scene.background = haze.set(`rgb(${map.haze.join(',')})`);
    // Packs imported before the sky and its objects were added lack these keys.
    const radius = map.terrain.visibleDistance * 1.05;
    if (map.sky) await buildSky(map.sky, map.terrain.visibleDistance);
    await buildSkyObjects(radius);
    const flared = (map.planets || []).find(planet => planet.flare);
    if (flared && map.flare) await buildFlare(flared, map.flare);
    sky.scale.setScalar(10 / Math.max(radius, map.sky && map.sky.size || 0));
    scene.add(sky);
    scene.add(new THREE.AmbientLight(new THREE.Color(...map.sun.ambient)));
    const sun = new THREE.DirectionalLight(new THREE.Color(...map.sun.intensity));
    sun.position.copy(toward(map.sun.azimuth, map.sun.incidence));  // Shades only what has no lightmap.
    scene.add(sun);
    if (map.weather) buildWeather(map.weather);
    $('weather').disabled = !map.weather;  // Most missions have no rain or snow to switch.
    applySettings();
    showViewpoint(0);
    showStatus('Loading terrain…');
    await buildTerrain(map.terrain);
    let loaded = 0;
    await Promise.all(map.objects.map(async object => { await addObject(object); showStatus(`Loading map objects ${++loaded}/${map.objects.length}`); }));
    $('notes').textContent += map.warnings.length ? ' This map: ' + map.warnings.join('. ') + '.' : '';
    ready = true;
    showReady();
  } catch (error) { showStatus('Map could not load: ' + error.message); }
});

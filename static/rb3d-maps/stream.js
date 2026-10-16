/* RB3D adapter for the T1 flight viewer. One region, at most 25 terrain chunks,
   two in-flight chunk requests. Evicted geometry/materials/textures are disposed. */
'use strict';
window.rb3dMaps = {
  fogRange(height, lighting) {
    // 0043EF20..0043F2DB. Visibility slider defaults to 100/100.
    const scale = 2 ** lighting.terrainScale;
    const base = Math.trunc(.35 * 52800 * 8) >> lighting.terrainScale;
    const cells = Math.trunc(Math.trunc(base * (.65 + .35 * lighting.visibility)) *
      (1 + Math.max(0, height / .0381 * .000005)));
    const distance = cells * scale;
    const weather = {3: [.75, .9], 4: [.5, .75], 5: [.25, .5]}[lighting.weather] || [1, 1];
    return [Math.trunc(.7 * weather[0] * distance) * .0381,
      Math.trunc(weather[1] * distance) * .0381];
  },
  async start({scene, camera, map, data, get}) {
    const $ = id => document.getElementById(id);
    const loaded = new Map(), pending = new Set(), errors = new Map();
    let region, generation = 0, elapsed = 1, wanted = new Set();
    let playerMesh = null;
    let lighting = map.lighting;
    const nativeLight = lighting?.model === 'baron-glide';
    const uniforms = {rbToward: {value: new THREE.Vector3()}, rbIntensity: {value: 1},
      rbAmbient: {value: .5}, rbFogRange: {value: new THREE.Vector2()}};
    function environment() {
      if (!nativeLight) return;
      uniforms.rbToward.value.fromArray(lighting.toward);
      uniforms.rbIntensity.value = lighting.intensity;
      uniforms.rbAmbient.value = lighting.ambient;
      const range = window.rb3dMaps.fogRange(camera.position.y, lighting);
      uniforms.rbFogRange.value.fromArray(range);
      // The shared viewer owns the haze toggle. Its normal fog shader is
      // replaced below, while retaining that toggle and the accepted colour.
      if (scene.fog && camera.far !== range[1]) {
        camera.far = range[1];
        camera.updateProjectionMatrix();
      }
    }
    environment();
    // The Glide clear colour (ground bank index 39); the fog keeps index 47. Packs before it lack the key.
    if (map.skyColour) scene.background = new THREE.Color(`rgb(${map.skyColour.join(',')})`);
    const textures = new Map(), models = new Map();
    async function texture(name) {
      if (!textures.has(name)) textures.set(name, {refs: 0, promise: new Promise((resolve, reject) => {
        new THREE.TextureLoader().load(data + 'textures/' + name, t => {
          t.flipY = false;
          // Point sampling matches the reviewed game capture. Native Glide
          // uses wrapping and one LOD; see docs/redbaron-maps.md for the
          // discrepancy between its filter default and the observed result.
          t.magFilter = t.minFilter = THREE.NearestFilter;
          t.wrapS = t.wrapT = THREE.RepeatWrapping;
          t.generateMipmaps = false;
          t.anisotropy = 1;
          resolve(t);
        }, undefined, reject);
      })});
      const item = textures.get(name);
      item.refs++;
      try { return await item.promise; }
      catch (error) { releaseTexture(name); throw error; }
    }
    function releaseTexture(name) {
      const item = textures.get(name);
      if (item && --item.refs === 0) {
        textures.delete(name);
        item.promise.then(t => t.dispose(), () => {});
      }
    }
    function dispose(group) {
      group.traverse(obj => {
        if (obj.geometry) obj.geometry.dispose();
        for (const mat of (Array.isArray(obj.material) ? obj.material : obj.material ? [obj.material] : [])) {
          if (mat.userData.texture) releaseTexture(mat.userData.texture);
          mat.dispose();
        }
      });
      scene.remove(group);
    }
    async function material(name, shade = false) {
      const tex = await texture(name);
      const mat = new THREE.MeshBasicMaterial({map: tex, side: THREE.DoubleSide, alphaTest: .5});
      mat.userData.texture = name;
      if (nativeLight) {
        mat.onBeforeCompile = shader => {
          Object.assign(shader.uniforms, uniforms);
          shader.vertexShader = `uniform vec3 rbToward;
uniform float rbIntensity, rbAmbient;
uniform vec2 rbFogRange;
varying float rbLight, rbFog, rbW;
` + shader.vertexShader;
          shader.vertexShader = shader.vertexShader.replace('#include <fog_vertex>', `
// 004C5040 -> 004ED3BA: radial distance, fixed-point ramp, >>7.
float haze = clamp((length(mvPosition.xyz) - rbFogRange.x) /
  (rbFogRange.y - rbFogRange.x), 0.0, 1.0);
rbFog = min(255.0, floor((haze * 65535.0 + .5) / 128.0)) / 255.0;
${shade ? `vec3 n = normalize(mat3(modelMatrix) * normal);
// 0051B6E0, 004D3900, 004ED100: ambient fixed16 + diffuse fixed16.
rbLight = min(1.0, floor(rbAmbient * 65536.0 +
  max(0.0, dot(n, rbToward)) * rbIntensity * 65535.0 + .5) / 65280.0);` : 'rbLight = 1.0;'}
// Glide iterates RGB/alpha in screen space; only texture coordinates use W.
rbW = gl_Position.w;
rbLight *= rbW;
rbFog *= rbW;
`);
          shader.fragmentShader = 'varying float rbLight, rbFog, rbW;\n' + shader.fragmentShader;
          shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>',
            '#include <color_fragment>\ndiffuseColor.rgb *= rbLight / rbW;');
          shader.fragmentShader = shader.fragmentShader.replace('#include <fog_fragment>', `
#ifdef USE_FOG
  gl_FragColor.rgb = mix(gl_FragColor.rgb, fogColor, rbFog / rbW);
#endif
`);
        };
        mat.customProgramCacheKey = () => 'rb3d-glide-' + Number(shade);
      }
      return mat;
    }
    async function object(obj, parent) {
      // Native class 08/type 0a has no drawable. Missing resources likewise
      // remain in the import report, without invented geometry in the scene.
      if (!obj.model) return;
      // Cache only the small JSON fetch while it is in progress. Instances own
      // their GPU resources, so a jump cannot leave a region resident forever.
      if (!models.has(obj.model)) {
        const request = get(data + 'models/' + obj.model).then(r => r.json());
        models.set(obj.model, request);
        request.finally(() => models.delete(obj.model)).catch(() => {});
      }
      const model = await models.get(obj.model);
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(model.vertices, 3));
      geometry.setAttribute('uv', new THREE.Float32BufferAttribute(model.uvs, 2));
      if (model.normals) geometry.setAttribute('normal', new THREE.Float32BufferAttribute(model.normals, 3));
      geometry.setIndex(model.indices);
      const mats = [];
      // Register resources on the group before awaiting so a failed material
      // cannot strand previously-created resources.
      const mesh = new THREE.Mesh(geometry, mats);
      parent.add(mesh);
      for (const slot of model.groups) geometry.addGroup(slot.start, slot.count, slot.materialIndex);
      for (let i = 0; i < model.material_textures.length; i++) {
        const shade = model.groups.some(g => g.materialIndex === i && g.shade > 0);
        mats.push(await material(model.material_textures[i], shade && !!model.normals));
      }
      mesh.matrix.fromArray(obj.matrix);
      mesh.matrixAutoUpdate = false;
      if (obj.player) playerMesh = mesh;
    }
    async function load(chunk, token) {
      const key = chunk.path;
      pending.add(key);
      const group = new THREE.Group();
      try {
        const desc = await (await get(data + key + '.json')).json();
        const binary = await (await get(data + key + '.bin')).arrayBuffer();
        const stride = desc.stride || 5; // Older packs still load, without invented normals.
        if (![5, 8].includes(stride) || binary.byteLength !== desc.vertices * stride * 4) throw new Error('Invalid terrain buffer size');
        const geometry = new THREE.BufferGeometry();
        const vertices = new THREE.InterleavedBuffer(new Float32Array(binary), stride);
        geometry.setAttribute('position', new THREE.InterleavedBufferAttribute(vertices, 3, 0));
        geometry.setAttribute('uv', new THREE.InterleavedBufferAttribute(vertices, 2, 3));
        if (stride === 8) geometry.setAttribute('normal', new THREE.InterleavedBufferAttribute(vertices, 3, 5));
        const mats = [];
        group.add(new THREE.Mesh(geometry, mats));
        for (const slot of desc.groups) {
          geometry.addGroup(slot.start, slot.count, mats.length);
          mats.push(await material(slot.texture, stride === 8));
        }
        // Sequential object work yields to the browser; no thousands-wide promise fanout.
        for (let i = 0; i < desc.objects.length; i++) {
          if (token !== generation || !wanted.has(key)) break;
          await object(desc.objects[i], group);
          if (i % 16 === 15) await new Promise(resolve => setTimeout(resolve));  // Yield; unlike rAF this also runs in a hidden tab.
        }
        if (token === generation && wanted.has(key)) {
          loaded.set(key, group);
          scene.add(group);
        } else dispose(group);
      } catch (error) {
        dispose(group);
        if (token === generation) errors.set(key, error.message || String(error));
      } finally { pending.delete(key); elapsed = 1; }
    }
    function view(matrix) {
      const m = new THREE.Matrix4().fromArray(matrix);
      camera.position.setFromMatrixPosition(m);
      camera.rotation.setFromRotationMatrix(m, 'YXZ');
      camera.rotation.z = 0;
      elapsed = 1;
    }
    async function selectRegion(id, reset) {
      const token = ++generation;
      region = null;
      wanted.clear();
      for (const group of loaded.values()) dispose(group);
      loaded.clear(); errors.clear();
      $('streamStatus').textContent = 'Loading region…';
      try {
        const next = await (await get(data + `regions/${id}/index.json`)).json();
        if (token !== generation) return;
        region = next;
        if (nativeLight) {
          if (!map.world && next.lighting) lighting = next.lighting;
          lighting.terrainScale = next.terrainScale ?? lighting.terrainScale;
          environment();
        }
        const types = {0: 'Airfield', 1: 'Town', 2: 'Military depot', 3: 'Rail yard',
          4: 'Bridge', 5: 'Factory', 7: 'Village', 8: 'Village'};
        $('jump').replaceChildren(new Option('Jump to a landmark…', ''), ...region.jumps.map((j, i) =>
          new Option(`${j.name} (${j.type || types[j.kind] || 'Landmark'})`, String(i))));
        if (reset) {
          map.viewpoints[0] = region.viewpoint;
          view(region.viewpoint);
        }
        elapsed = 1;
      } catch (error) { if (token === generation) $('streamStatus').textContent = error.message; }
    }
    $('region').replaceChildren(...map.regions.map(r => new Option(r.name, String(r.id))));
    $('region').value = String(map.region);
    $('region').addEventListener('change', event => selectRegion(Number(event.target.value), true));
    $('jump').addEventListener('change', event => {
      if (event.target.value !== '' && region) view(region.jumps[Number(event.target.value)].matrix);
    });
    const mission = new THREE.Group();
    scene.add(mission);
    try {
      for (let i = 0; i < map.objects.length; i++) {
        await object(map.objects[i], mission);
        if (i % 16 === 15) await new Promise(resolve => setTimeout(resolve));  // Yield; unlike rAF this also runs in a hidden tab.
      }
    } catch (error) { dispose(mission); throw error; }
    await selectRegion(map.region, false);
    // The Glide sky (render mode 4; the software renderer's tiled deck is not drawn there). Native (x, y, z up) maps to
    // the viewer as (-x, z, y), as the terrain chunks and placements do.
    const native = .0381, at = (x, y, z) => [-x * native, z * native, y * native];
    let skyWeather = null;
    // The horizon cloud band (004B40F0, 004B41E0, 004B4440): sixteen upright panels π/8 apart on a ring of radius
    // 2500 × 8 native units round the eye, each centred 2500 units above it, one chord wide plus 2% and 2500 units tall
    // (5000 in weathers 3-5), showing tile n mod 8 of hz-cld<weather> (four across, two down, from v .01). The ring
    // moves with the eye, never turns, and sits behind the world.
    const ring = new THREE.Group();
    async function buildRing(weather) {
      const file = map.sky?.ring?.[weather] || map.sky?.ring?.[0];
      if (!file) return;
      const image = await texture(file);
      if (skyWeather !== weather) { releaseTexture(file); return; }
      image.magFilter = image.minFilter = THREE.LinearFilter;
      const radius = 2500 * 8, half = Math.sin(Math.PI / 16) * radius * 1.02;
      const height = (weather >= 3 ? 2 : 1) * 2500, centre = 2500;
      const positions = [], uvs = [], indices = [];
      for (let n = 0; n < 16; n++) {
        const angle = n * Math.PI / 8, c = Math.cos(angle), s = Math.sin(angle);
        const u = (n & 3) * .25, v = (n >> 2 & 1) * .5 + .01, first = positions.length / 3;
        // Ends along the tangent, toward the previous panel then the next, whose tiles continue the image.
        for (const [side, z] of [[-1, centre + height / 2], [1, centre + height / 2], [1, centre - height / 2], [-1, centre - height / 2]]) {
          positions.push(...at(radius * c - side * half * s, radius * s + side * half * c, z));
          uvs.push(u + (side + 1) / 2 * .25, v + (z < centre ? .5 : 0));
        }
        indices.push(first, first + 1, first + 2, first, first + 2, first + 3);
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
      geometry.setIndex(indices);
      const mat = new THREE.MeshBasicMaterial({map: image, side: THREE.DoubleSide, transparent: true, depthTest: false, depthWrite: false, fog: false});
      mat.userData.texture = file;
      const mesh = new THREE.Mesh(geometry, mat);
      mesh.renderOrder = -3;
      mesh.frustumCulled = false;
      ring.add(mesh);
    }
    // Cloud sheets (004BDA46 by weather, 004BD710 per layer, 004BDFD0): flat csheet<weather> squares 80000 native units
    // across at each layer's height (weather 1: 160000; 2: 120000, 64000; 3-5: 120000, 80000, 56000), laid out over a
    // 4 x 4 grid of 131072-unit cells from three cells below the eye's cell. Each cell draws its own random stream: no
    // cloud when the first number is at most .25 (in weathers below 3), else one, two or three (next number below .75,
    // below .95, else); the first at the cell corner, each further one moved up to ±65536 across and ±20000 up from the
    // last; each turned a random yaw. ponytail: the game seeds each cell's stream from a terrain/weather field at the
    // cell (0043FD10, not decoded); this seeds it from the cell and layer, so the layout is the game's kind, not its copy.
    const LAYERS = {1: [160000], 2: [120000, 64000], 3: [120000, 80000, 56000], 4: [120000, 80000, 56000], 5: [120000, 80000, 56000]};
    const clouds = new THREE.Group();
    let cloudMaterial = null, cloudCell = '';
    function stream(a, b, c) {  // 004BD640 seeds, 004BD5A0 draws: three congruential streams summed mod 65536.
      let s1 = a % 0x8003, s2 = b % 0x800b, s3 = c % 0x800f;
      return () => {
        s1 = 179 * s1 % 0x8003; s2 = 183 * s2 % 0x800b; s3 = 182 * s3 % 0x800f;
        return Math.abs(s1 + s2 + s3) % 65536 / 65536;
      };
    }
    const sheet = new THREE.PlaneGeometry(80000 * native, 80000 * native).rotateX(-Math.PI / 2);
    function buildClouds(weather, x, y) {
      clouds.clear();
      if (!cloudMaterial || !LAYERS[weather]) return;
      const baseX = (Math.trunc(x) & ~0x1ffff) - 0x30000, baseY = (Math.trunc(y) & ~0x1ffff) - 0x30000;
      for (const height of LAYERS[weather]) for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) {
        const cellX = baseX + j * 0x20000, cellY = baseY + i * 0x20000;
        const random = stream(Math.abs(cellX >> 17) + (height >> 10) + 1, Math.abs(cellY >> 17) + 1, height + 1);
        if (random() <= .25 && weather < 3) continue;
        const pick = random(), count = pick < .75 ? 1 : pick < .95 ? 2 : 3;
        let px = cellX, py = cellY, pz = height;
        for (let n = 0; n < count; n++) {
          if (n) { px += random() * 131072 - 65536; py += random() * 131072 - 65536; pz += random() * 40000 - 20000; }
          const mesh = new THREE.Mesh(sheet, cloudMaterial);
          mesh.position.fromArray(at(px, py, pz));
          mesh.rotation.y = random() * 2 * Math.PI;
          clouds.add(mesh);
        }
      }
    }
    // The sun (004D05B0, drawn by 004AB700): Sun.bmp blitted unscaled, 32 pixels of the game's 480-line screen, at the
    // sun's projected point, behind everything, while its elevation + 3 is above -2 degrees.
    let sun = null;
    if (map.sky?.sun) texture(map.sky.sun).then(image => {
      image.magFilter = image.minFilter = THREE.LinearFilter;
      const material = new THREE.SpriteMaterial({map: image, sizeAttenuation: false, depthTest: false, depthWrite: false, fog: false, transparent: true});
      material.userData.texture = map.sky.sun;
      sun = new THREE.Sprite(material);
      sun.renderOrder = -4;
      sun.frustumCulled = false;
      scene.add(sun);
    }, () => {});
    function placeSun() {
      if (!sun) return;
      const elevation = lighting?.sunElevation ?? -90, azimuth = THREE.MathUtils.degToRad(lighting?.sunAzimuth ?? 0);
      sun.visible = elevation + 3 > -2;
      const climb = THREE.MathUtils.degToRad(elevation);
      sun.position.set(-Math.sin(azimuth) * Math.cos(climb), Math.sin(climb), Math.cos(azimuth) * Math.cos(climb))
        .multiplyScalar(1000).add(camera.position);
      sun.scale.setScalar(64 / 480 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2));
    }
    async function buildSky(weather) {
      skyWeather = weather;
      if (!map.sky) return;  // Packs imported before the sky.
      dispose(ring); ring.clear(); scene.add(ring);
      clouds.clear(); scene.add(clouds);
      if (cloudMaterial) { releaseTexture(cloudMaterial.userData.texture); cloudMaterial.dispose(); cloudMaterial = null; }
      cloudCell = '';
      buildRing(weather);
      const file = map.sky?.clouds?.[weather];
      if (!file) return;
      const image = await texture(file);
      if (skyWeather !== weather) { releaseTexture(file); return; }
      image.magFilter = image.minFilter = THREE.LinearFilter;
      cloudMaterial = new THREE.MeshBasicMaterial({map: image, side: THREE.DoubleSide, transparent: true, depthWrite: false});
      cloudMaterial.userData.texture = file;
    }
    return delta => {
      environment();
      const weather = lighting?.weather ?? 0;
      if (weather !== skyWeather) buildSky(weather);
      ring.position.copy(camera.position);
      placeSun();
      // The cloud grid follows the eye's cell: rebuilt when it changes or the sheets' texture has arrived.
      const x = -camera.position.x / native, y = camera.position.z / native;
      const cell = `${Math.trunc(x) >> 17},${Math.trunc(y) >> 17},${Boolean(cloudMaterial)}`;
      if (cell !== cloudCell) { cloudCell = cell; buildClouds(weather, x, y); }
      if (playerMesh) {
        const e = playerMesh.matrix.elements;
        playerMesh.visible = Math.hypot(camera.position.x - e[12], camera.position.y - e[13], camera.position.z - e[14]) > 8;
      }
      elapsed += delta;
      if (!region || elapsed < .3) return;
      elapsed = 0;
      const distance = chunk => {
        const [x0, z0, x1, z1] = chunk.bounds;
        return Math.hypot(Math.max(x0 - camera.position.x, 0, camera.position.x - x1),
                          Math.max(z0 - camera.position.z, 0, camera.position.z - z1));
      };
      const nearby = region.chunks.map(chunk => ({chunk, distance: distance(chunk)}))
        .filter(c => c.distance < camera.far).sort((a, b) => a.distance - b.distance).slice(0, 25);
      wanted = new Set(nearby.map(c => c.chunk.path));
      for (const [key, group] of loaded) if (!wanted.has(key)) { dispose(group); loaded.delete(key); }
      for (const {chunk} of nearby) {
        if (pending.size >= 2) break;
        if (!loaded.has(chunk.path) && !pending.has(chunk.path) && !errors.has(chunk.path)) load(chunk, generation);
      }
      $('streamStatus').textContent = `${region.name}: ${loaded.size}/${nearby.length} chunks` +
        (errors.size ? ` · ${errors.size} failed: ${errors.values().next().value}` : '');
    };
  }
};

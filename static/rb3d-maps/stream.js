/* RB3D adapter for the T1 flight viewer. One region, at most 25 terrain chunks,
   two in-flight chunk requests. Evicted geometry/materials/textures are disposed. */
'use strict';
window.rb3dMaps = {
  async start({scene, camera, map, data, get}) {
    const $ = id => document.getElementById(id);
    const loaded = new Map(), pending = new Set(), errors = new Map();
    let region, generation = 0, elapsed = 1, wanted = new Set();
    let playerMesh = null;
    const textures = new Map(), models = new Map();
    async function texture(name) {
      if (!textures.has(name)) textures.set(name, {refs: 0, promise: new Promise((resolve, reject) => {
        new THREE.TextureLoader().load(data + 'textures/' + name, t => {
          t.flipY = false;
          t.anisotropy = 4;
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
    async function material(name) {
      const tex = await texture(name);
      const mat = new THREE.MeshBasicMaterial({map: tex, side: THREE.DoubleSide, alphaTest: .5});
      mat.userData.texture = name;
      return mat;
    }
    async function object(obj, parent) {
      let geometry, mesh;
      if (obj.model) {
        // Cache only the small JSON fetch while it is in progress. Instances own
        // their GPU resources, so a jump cannot leave a region resident forever.
        if (!models.has(obj.model)) {
          const request = get(data + 'models/' + obj.model).then(r => r.json());
          models.set(obj.model, request);
          request.finally(() => models.delete(obj.model)).catch(() => {});
        }
        const model = await models.get(obj.model);
        geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(model.vertices, 3));
        geometry.setAttribute('uv', new THREE.Float32BufferAttribute(model.uvs, 2));
        geometry.setIndex(model.indices);
        const mats = [];
        // Register resources on the group before awaiting so a failed material
        // cannot strand previously-created resources.
        mesh = new THREE.Mesh(geometry, mats);
        parent.add(mesh);
        for (const slot of model.groups) geometry.addGroup(slot.start, slot.count, slot.materialIndex);
        for (const name of model.material_textures) mats.push(await material(name));
      } else {
        geometry = new THREE.BoxGeometry(12, 12, 12);
        mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({color: 0xff00ff, wireframe: true}));
        parent.add(mesh);
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
        if (binary.byteLength !== desc.vertices * 20) throw new Error('Invalid terrain buffer size');
        const geometry = new THREE.BufferGeometry();
        const vertices = new THREE.InterleavedBuffer(new Float32Array(binary), 5);
        geometry.setAttribute('position', new THREE.InterleavedBufferAttribute(vertices, 3, 0));
        geometry.setAttribute('uv', new THREE.InterleavedBufferAttribute(vertices, 2, 3));
        const mats = [];
        group.add(new THREE.Mesh(geometry, mats));
        for (const slot of desc.groups) {
          geometry.addGroup(slot.start, slot.count, mats.length);
          mats.push(await material(slot.texture));
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
        $('jump').replaceChildren(new Option('Jump to a landmark…', ''), ...region.jumps.map((j, i) => new Option(j.name, String(i))));
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
    return delta => {
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

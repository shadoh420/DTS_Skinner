/* Reads the baked light of a Reflex Arena map (maps/<name>.light beside the .map, written by the editor's Build
   Lighting), which the game draws its map with in play: Reflex has no lightmaps for brushes. Node runs it too
   (tests/reflex_maps.test.cjs). Little-endian, two parts:

   Light probes, a grid with one probe every `cell` units: u16 2, u16 0x1323, f32 cell (64), u32 nx, ny, nz, u32 a
   hash of the map, f32 origin x y z, f32 scale x y z, f32 offset x y z (60 bytes). Then seven planes of nx × ny × nz
   probes, x fastest, each probe four float16: the light arriving from every direction as order-2 spherical
   harmonics, packed as the game's shaders read them (probes_cAr, cAg, cAb, cBr, cBg, cBb, cC). Then one byte per
   probe, the same order: which reflection probe that cell uses, counted from 1 (reflectionProbeIndices). The game
   looks a point up at point × scale + offset, a texture coordinate of the grid (probe i at origin + i × cell).

   Reflection probes: u16 2, u16 0x1324, u32 count, u32 size (64), u32 mip count (5); per probe f32 x y z, a byte
   (1), then the six faces of a cube map (+x, −x, +y, −y, +z, −z), each with its mips, largest first, BC1. */
(function (exports) {
  'use strict';
  const PROBE_MAGIC = 0x1323, CUBE_MAGIC = 0x1324;

  function half(bits) {
    const exponent = (bits >> 10) & 31, fraction = bits & 1023, sign = bits & 32768 ? -1 : 1;
    if (exponent === 0) return sign * fraction * 2 ** -24;
    if (exponent === 31) return fraction ? NaN : sign * Infinity;
    return sign * (1 + fraction / 1024) * 2 ** (exponent - 15);
  }

  function parse(buffer) {
    const bytes = new Uint8Array(buffer.buffer || buffer, buffer.byteOffset || 0, buffer.byteLength);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (bytes.length < 60 || view.getUint16(0, true) !== 2 || view.getUint16(2, true) !== PROBE_MAGIC) throw new Error('not a Reflex light file');
    const f = at => view.getFloat32(at, true), u = at => view.getUint32(at, true);
    const size = [u(8), u(12), u(16)], count = size[0] * size[1] * size[2];
    const light = {cell: f(4), size, hash: u(20), origin: [f(24), f(28), f(32)], scale: [f(36), f(40), f(44)], offset: [f(48), f(52), f(56)]};
    let at = 60;
    if (at + count * 57 + 16 > bytes.length) throw new Error('light file is cut short');
    // The seven planes as float16 bits (four per probe), uploaded as they are.
    light.planes = [];
    for (let plane = 0; plane < 7; plane++, at += count * 8) light.planes.push(new Uint16Array(bytes.slice(at, at + count * 8).buffer));
    light.indices = bytes.slice(at, at + count);
    at += count;
    if (view.getUint16(at, true) !== 2 || view.getUint16(at + 2, true) !== CUBE_MAGIC) throw new Error('reflection probes missing');
    const cubes = u(at + 4), side = u(at + 8), mips = u(at + 12);
    at += 16;
    const faceBytes = Array.from({length: mips}, (_, m) => Math.max(1, (side >> m) / 4) ** 2 * 8).reduce((a, b) => a + b, 0);
    light.cubeSize = side; light.cubeMips = mips; light.cubes = [];
    for (let i = 0; i < cubes; i++) {
      if (at + 13 + 6 * faceBytes > bytes.length) throw new Error('light file is cut short');
      const faces = [];
      for (let face = 0; face < 6; face++) {
        const levels = [];
        let from = at + 13 + face * faceBytes;
        for (let m = 0; m < mips; m++) {
          const s = Math.max(1, side >> m), length = Math.max(1, s / 4) ** 2 * 8;
          levels.push({size: s, data: bytes.subarray(from, from + length)});
          from += length;
        }
        faces.push(levels);
      }
      light.cubes.push({position: [f(at), f(at + 4), f(at + 8)], faces});
      at += 13 + 6 * faceBytes;
    }
    return light;
  }

  // The light arriving at a surface of normal n from probe `index` (x + nx (y + ny z)), as the game's shader sums it.
  function irradiance(light, index, n) {
    const c = light.planes.map(plane => [0, 1, 2, 3].map(k => half(plane[index * 4 + k])));
    const b = [n[0] * n[1], n[1] * n[2], n[2] * n[2], n[2] * n[0]], q = n[0] * n[0] - n[1] * n[1];
    return [0, 1, 2].map(ch => c[ch][0] * n[0] + c[ch][1] * n[1] + c[ch][2] * n[2] + c[ch][3] +
      c[ch + 3][0] * b[0] + c[ch + 3][1] * b[1] + c[ch + 3][2] * b[2] + c[ch + 3][3] * b[3] + c[6][ch] * q);
  }

  // RGBA bytes of a BC1 image (`size` × `size`, at least one 4 × 4 block).
  function decodeBC1(data, size) {
    const out = new Uint8Array(size * size * 4), blocks = Math.max(1, size / 4);
    const colour = (value, to) => { to[0] = ((value >> 11) & 31) * 255 / 31; to[1] = ((value >> 5) & 63) * 255 / 63; to[2] = (value & 31) * 255 / 31; };
    const palette = [[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]];
    for (let by = 0; by < blocks; by++) for (let bx = 0; bx < blocks; bx++) {
      const at = (by * blocks + bx) * 8, c0 = data[at] | data[at + 1] << 8, c1 = data[at + 2] | data[at + 3] << 8;
      colour(c0, palette[0]); colour(c1, palette[1]);
      for (let k = 0; k < 3; k++) {
        if (c0 > c1) { palette[2][k] = (2 * palette[0][k] + palette[1][k]) / 3; palette[3][k] = (palette[0][k] + 2 * palette[1][k]) / 3; }
        else { palette[2][k] = (palette[0][k] + palette[1][k]) / 2; palette[3][k] = 0; }
      }
      for (let y = 0; y < 4; y++) {
        const row = data[at + 4 + y];
        for (let x = 0; x < 4; x++) {
          const px = bx * 4 + x, py = by * 4 + y;
          if (px >= size || py >= size) continue;
          const p = palette[(row >> (x * 2)) & 3], o = (py * size + px) * 4;
          out[o] = p[0]; out[o + 1] = p[1]; out[o + 2] = p[2]; out[o + 3] = 255;
        }
      }
    }
    return out;
  }

  /* The map's lights as the game's lighting shaders (gbuffer_light_point, gbuffer_light_spot) take them, and a grid of
     `cell`-unit cells over them, each listing the lights that reach into it, so a point of a surface sums only those.
     A light: {position, colour (linear, the strength included), near, far, and for a spot direction (unit, the way
     it shines), cosInner and cosOuter}. Its light falls off as clamp(1 - (distance - near) / (far - near))², and a
     spot's also as clamp((cos - cosOuter) / (cosInner - cosOuter))² across its edge.

     Packed for textures: `data`, four RGBA floats per light (position, near; colour, far; direction, cosOuter; 1 /
     (cosInner - cosOuter), spot or not, 0, 0); `cells`, per cell of the grid (x fastest) the first entry of its list
     and its count; `list`, the light numbers. */
  function lightGrid(lights, cell = 128) {
    const data = new Float32Array(Math.max(1, lights.length) * 16);
    lights.forEach((light, i) => {
      const spot = !!light.direction, d = light.direction || [0, -1, 0];
      data.set([...light.position, light.near, ...light.colour, light.far, ...d, spot ? light.cosOuter : -2,
        spot ? 1 / Math.max(light.cosInner - light.cosOuter, 1e-4) : 0, spot ? 1 : 0, 0, 0], i * 16);
    });
    if (!lights.length) return {data, count: 0, origin: [0, 0, 0], size: [1, 1, 1], cell, cells: new Float32Array(2), list: new Float32Array(1)};
    const low = [0, 1, 2].map(k => Math.min(...lights.map(l => l.position[k] - l.far)));
    const high = [0, 1, 2].map(k => Math.max(...lights.map(l => l.position[k] + l.far)));
    const origin = low.map(v => Math.floor(v / cell) * cell), size = [0, 1, 2].map(k => Math.max(1, Math.ceil((high[k] - origin[k]) / cell)));
    const lists = Array.from({length: size[0] * size[1] * size[2]}, () => []);
    lights.forEach((light, i) => {
      const from = [0, 1, 2].map(k => Math.max(0, Math.floor((light.position[k] - light.far - origin[k]) / cell)));
      const to = [0, 1, 2].map(k => Math.min(size[k] - 1, Math.floor((light.position[k] + light.far - origin[k]) / cell)));
      for (let z = from[2]; z <= to[2]; z++) for (let y = from[1]; y <= to[1]; y++) for (let x = from[0]; x <= to[0]; x++) {
        // Only cells the light's sphere reaches: the nearest point of the cell within its far distance.
        const box = [x, y, z].map((c, k) => origin[k] + c * cell), nearest = [0, 1, 2].map(k => Math.max(box[k], Math.min(light.position[k], box[k] + cell)));
        if (Math.hypot(...nearest.map((v, k) => v - light.position[k])) <= light.far) lists[x + size[0] * (y + size[1] * z)].push(i);
      }
    });
    const cells = new Float32Array(lists.length * 2), list = [];
    lists.forEach((entries, i) => { cells[i * 2] = list.length; cells[i * 2 + 1] = entries.length; list.push(...entries); });
    return {data, count: lights.length, origin, size, cell, cells, list: Float32Array.from(list.length ? list : [0])};
  }

  exports.ReflexLight = {parse, half, irradiance, decodeBC1, lightGrid};
})(typeof module !== 'undefined' ? module.exports : window);

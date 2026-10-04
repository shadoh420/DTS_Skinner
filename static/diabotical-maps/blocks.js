/* Block meshes for the Diabotical map page, from the 16-byte blocks tools/import_diabotical_map.py writes (int16 x, y,
   z, u8 shape, quarter turn, open faces, 0, six u8 face materials; faces +z, -x, -z, +x, top, bottom). Node runs it
   too (tests/diabotical_maps.test.cjs).

   Positions are the game's /export's: block (x, y, z) fills 40x..40x+40, 20y..20y+20, -40z-40..-40z (z mirrored, so the
   file's +z face points to -Z here). Texture coordinates are the export's too: per world unit a material's texture
   moves by its uv_scale / 40, along the axes FACE lists for each face. Shape 1 is a cube, shape 3 half of one cut
   along a vertical diagonal: turn 0 has lost its corner at (40x, -40z-40), and each turn moves that corner on to
   the next one about the vertical, toward +Z first. Its sloped side takes the material of the side it replaced
   that runs from the lost corner to the next (a guess: not yet checked in the game). Other shapes draw nothing. */
(function (exports) {
  'use strict';
  const SIZE = 16, WIDTH = 40, HEIGHT = 20, CUBE = 1, HALF = 3;
  // Per stored face: its outward normal here, and the world axes (with signs) its u and v follow.
  const FACE = [
    {normal: [0, 0, -1], u: [-1, 0, 0], v: [0, 1, 0]},
    {normal: [-1, 0, 0], u: [0, 0, 1], v: [0, 1, 0]},
    {normal: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0]},
    {normal: [1, 0, 0], u: [0, 0, -1], v: [0, 1, 0]},
    {normal: [0, 1, 0], u: [1, 0, 0], v: [0, 0, -1]},
    {normal: [0, -1, 0], u: [1, 0, 0], v: [0, 0, 1]},
  ];
  // A half block's footprint corners (fractions of the cell, x then Z) in turn order, and the stored face along
  // the cell side from each corner to the next.
  const CORNERS = [[0, 0], [0, 1], [1, 1], [1, 0]], SIDE = [1, 2, 3, 0];

  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

  /* The surfaces of `buffer`'s blocks, one per material index: {positions, normals, uvs} (Float32Array, three
     corners a triangle). `scales` is each material's uv_scale (1 where missing). */
  function buildBlocks(buffer, scales) {
    // Twice over the blocks: counting each material's triangles, then writing them into arrays of that size (a
    // big map has millions; growing plain arrays held several times their memory).
    const triangles = new Map();
    eachPolygon(buffer, (material, corners) => triangles.set(material, (triangles.get(material) || 0) + corners.length - 2));
    const out = new Map(), filled = new Map();
    for (const [material, count] of triangles) {
      out.set(material, {positions: new Float32Array(count * 9), normals: new Float32Array(count * 9), uvs: new Float32Array(count * 6)});
      filled.set(material, 0);
    }
    // One flat polygon (corners in any order around it), turned to face `normal`, as a fan of triangles.
    eachPolygon(buffer, (material, corners, normal, axes) => {
      if (dot(cross(sub(corners[1], corners[0]), sub(corners[2], corners[0])), normal) < 0) corners = corners.slice().reverse();
      const {positions, normals, uvs} = out.get(material), scale = (scales[material] ?? 1) / WIDTH;
      let at = filled.get(material);
      for (let i = 1; i + 1 < corners.length; i++) for (const corner of [corners[0], corners[i], corners[i + 1]]) {
        positions.set(corner, at * 3);
        normals.set(normal, at * 3);
        uvs[at * 2] = dot(corner, axes.u) * scale;
        uvs[at * 2 + 1] = dot(corner, axes.v) * scale;
        at++;
      }
      filled.set(material, at);
    });
    return out;
  }

  // Calls polygon(material, corners, outward normal, texture axes) for each face of `buffer`'s blocks.
  function eachPolygon(buffer, polygon) {
    const view = new DataView(buffer), count = Math.floor(buffer.byteLength / SIZE);
    for (let i = 0; i < count; i++) {
      const at = i * SIZE, shape = view.getUint8(at + 6), turn = view.getUint8(at + 7) & 3, open = view.getUint8(at + 8);
      if (shape !== CUBE && shape !== HALF) continue;
      const x0 = view.getInt16(at, true) * WIDTH, y0 = view.getInt16(at + 2, true) * HEIGHT, z0 = -view.getInt16(at + 4, true) * WIDTH - WIDTH;
      const faces = Array.from({length: 6}, (_, face) => view.getUint8(at + 10 + face));
      const point = ([fx, fz], top) => [x0 + fx * WIDTH, y0 + (top ? HEIGHT : 0), z0 + fz * WIDTH];
      if (shape === CUBE) {
        for (let face = 0; face < 6; face++) {
          if (!(open & 1 << face)) continue;
          const {normal} = FACE[face], axis = normal.findIndex(value => value !== 0), far = normal[axis] > 0;
          const corners = [[0, 0], [1, 0], [1, 1], [0, 1]].map(([a, b]) => {
            const fractions = [0, 0, 0], others = [0, 1, 2].filter(other => other !== axis);
            fractions[axis] = far ? 1 : 0;
            fractions[others[0]] = a;
            fractions[others[1]] = b;
            return [x0 + fractions[0] * WIDTH, y0 + fractions[1] * HEIGHT, z0 + fractions[2] * WIDTH];
          });
          polygon(faces[face], corners, normal, FACE[face]);
        }
        continue;
      }
      // Half block: the three corners left, the two whole sides between them, and the sloped side.
      const left = [1, 2, 3].map(step => CORNERS[(turn + step) & 3]);
      polygon(faces[4], left.map(corner => point(corner, true)), FACE[4].normal, FACE[4]);
      polygon(faces[5], left.map(corner => point(corner, false)), FACE[5].normal, FACE[5]);
      for (const step of [1, 2]) {
        const face = SIDE[(turn + step) & 3], [a, b] = [CORNERS[(turn + step) & 3], CORNERS[(turn + step + 1) & 3]];
        polygon(faces[face], [point(a, false), point(b, false), point(b, true), point(a, true)], FACE[face].normal, FACE[face]);
      }
      const [a, b] = [left[2], left[0]], lost = CORNERS[turn];
      const outward = [lost[0] - (a[0] + b[0]) / 2, 0, lost[1] - (a[1] + b[1]) / 2], length = Math.hypot(outward[0], outward[2]);
      const normal = [outward[0] / length, 0, outward[2] / length];
      polygon(faces[SIDE[turn]], [point(a, false), point(b, false), point(b, true), point(a, true)], normal,
        {u: [-normal[2], 0, normal[0]], v: [0, 1, 0]});
    }
  }

  exports.DiaboticalBlocks = {buildBlocks, SIZE, WIDTH, HEIGHT, FACE};
})(typeof module !== 'undefined' ? module.exports : window);

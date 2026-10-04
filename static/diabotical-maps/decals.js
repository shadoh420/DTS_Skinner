/* Decals for the Diabotical map page, as the game's tiledecal.ps draws them: a decal's box is the unit cube centred on
   it, under its page matrix (12 floats, the top three rows); the surfaces in the box that face its local +z here
   (the game's -z; the shader drops normals more than about 84° off: dot < 0.1) take its texture, across local x and
   y, inside a second box the game cuts it to (turned otherwise: see the importer's decal_matrix). The page clips each
   surface triangle to both boxes and draws the pieces, lit as the surface. Node runs it too
   (tests/diabotical_maps.test.cjs). */
(function (exports) {
  'use strict';
  const CELL = 128, FACING = .1;
  const cell = value => Math.floor(value / CELL) & 1023;

  // The inverse of a 3x4 matrix (rows), as 12 floats.
  function invert(m) {
    const [a, b, c, x, d, e, f, y, g, h, i, z] = m;
    const A = e * i - f * h, B = f * g - d * i, C = d * h - e * g, det = a * A + b * B + c * C;
    const r = [A, c * h - b * i, b * f - c * e, B, a * i - c * g, c * d - a * f, C, b * g - a * h, a * e - b * d].map(v => v / det);
    return [r[0], r[1], r[2], -(r[0] * x + r[1] * y + r[2] * z), r[3], r[4], r[5], -(r[3] * x + r[4] * y + r[5] * z),
      r[6], r[7], r[8], -(r[6] * x + r[7] * y + r[8] * z)];
  }

  /* Projects decal boxes (page matrices) onto triangles given to add(), cut to the boxes `cuts` (the same where
     left out): out[i] gathers box i's pieces as {positions, normals, uvs} (plain arrays, three corners a triangle;
     uv (0, 0) at the box's local -x, -y). */
  function createProjector(boxes, cuts = boxes) {
    const bins = new Map(), stamp = new Int32Array(boxes.length).fill(-1);
    const decals = boxes.map((m, id) => {
      const extent = [0, 1, 2].map(row => Math.abs(m[row * 4]) + Math.abs(m[row * 4 + 1]) + Math.abs(m[row * 4 + 2]));
      const min = extent.map((e, row) => m[row * 4 + 3] - e / 2), max = extent.map((e, row) => m[row * 4 + 3] + e / 2);
      for (let x = Math.floor(min[0] / CELL); x <= Math.floor(max[0] / CELL); x++)
        for (let y = Math.floor(min[1] / CELL); y <= Math.floor(max[1] / CELL); y++)
          for (let z = Math.floor(min[2] / CELL); z <= Math.floor(max[2] / CELL); z++) {
            const key = cell(x * CELL) | cell(y * CELL) << 10 | cell(z * CELL) << 20;
            if (!bins.has(key)) bins.set(key, []);
            bins.get(key).push(id);
          }
      const length = Math.hypot(m[2], m[6], m[10]);
      return {inverse: invert(m), cut: invert(cuts[id]), direction: [m[2] / length, m[6] / length, m[10] / length], min, max};
    });
    const out = boxes.map(() => ({positions: [], normals: [], uvs: []}));
    let visit = 0;
    // Each decal whose box's bounds meet the bounds min..max, once.
    function* near(min, max) {
      visit++;
      for (let x = Math.floor(min[0] / CELL); x <= Math.floor(max[0] / CELL); x++)
        for (let y = Math.floor(min[1] / CELL); y <= Math.floor(max[1] / CELL); y++)
          for (let z = Math.floor(min[2] / CELL); z <= Math.floor(max[2] / CELL); z++) {
            for (const id of bins.get(cell(x * CELL) | cell(y * CELL) << 10 | cell(z * CELL) << 20) || []) {
              if (stamp[id] === visit) continue;
              stamp[id] = visit;
              const decal = decals[id];
              if (!decal.min.some((low, k) => max[k] < low) && !decal.max.some((high, k) => min[k] > high)) yield id;
            }
          }
    }
    // Whether any decal box's bounds meet min..max (to skip a prop far from all).
    const touches = (min, max) => !near(min, max).next().done;

    // A world triangle (corners a, b, c counter-clockwise from its front; corner normals na, nb, nc).
    function add(a, b, c, na, nb, nc) {
      const min = [0, 1, 2].map(k => Math.min(a[k], b[k], c[k])), max = [0, 1, 2].map(k => Math.max(a[k], b[k], c[k]));
      let face = null, area = 0;
      for (const id of near(min, max)) {
        if (!face) {
          const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
          face = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
          area = Math.hypot(...face);
        }
        const {direction, inverse, cut} = decals[id];
        if (area && (face[0] * direction[0] + face[1] * direction[1] + face[2] * direction[2]) / area >= FACING) clip(inverse, cut, [a, b, c], [na, nb, nc], out[id]);
      }
    }
    return {add, touches, out};
  }

  // Sutherland-Hodgman against both boxes' six faces, in their local spaces (corner values 0-2 and 9-11); each
  // corner keeps its world position and normal (3-8).
  function clip(inverse, cut, corners, normals, out) {
    const into = (m, p) => [0, 1, 2].map(row => m[row * 4] * p[0] + m[row * 4 + 1] * p[1] + m[row * 4 + 2] * p[2] + m[row * 4 + 3]);
    let polygon = corners.map((p, i) => into(inverse, p).concat(p, normals[i], into(cut, p)));
    for (const k of [0, 1, 2, 9, 10, 11]) {
      for (const side of [1, -1]) {
        const next = [];
        for (let i = 0; i < polygon.length; i++) {
          const p = polygon[i], q = polygon[(i + 1) % polygon.length], dp = side * p[k] - .5, dq = side * q[k] - .5;
          if (dp <= 0) next.push(p);
          if ((dp <= 0) !== (dq <= 0)) next.push(p.map((value, j) => value + (q[j] - value) * dp / (dp - dq)));
        }
        polygon = next;
        if (!polygon.length) return;
      }
    }
    for (let i = 1; i + 1 < polygon.length; i++) {
      for (const p of [polygon[0], polygon[i], polygon[i + 1]]) {
        out.positions.push(p[3], p[4], p[5]);
        const length = Math.hypot(p[6], p[7], p[8]) || 1;
        out.normals.push(p[6] / length, p[7] / length, p[8] / length);
        out.uvs.push(p[0] + .5, p[1] + .5);
      }
    }
  }

  exports.DiaboticalDecals = {createProjector, invert};
})(typeof module !== 'undefined' ? module.exports : window);

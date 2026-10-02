/* Convex brush geometry and CSG for the Reflex map page; no Three.js, so Node runs it too (tests/reflex_maps.test.cjs).

   A brush is what a Reflex map file stores: {vertices: [[x, y, z], …], faces: [{indices: […], …}, …]}. A face's
   other fields (material, texture offset, scale, rotation, colour) are carried through every operation untouched.
   Operations work on planes, as Quake's and Radiant's brush CSG does: a convex brush is where every face plane has
   it behind, and new faces are found by clipping a large square on each plane by all the others
   (BaseWindingForPlane, ChopWinding). A plane is {normal, distance, face} with normal · p = distance on it and the
   normal pointing out of the brush.

   Polygons come out wound counter-clockwise seen from outside (the normal by the right-hand rule); readers
   and writers of a file turn them to its own order. */
(function (exports) {
  'use strict';
  const EPSILON = 1e-3;     // Distance within which a point is on a plane.
  const WELD = 1e-3;        // Vertices closer than this are one vertex.
  const MIN_VOLUME = 1e-3;  // A brush with less volume than this is empty.
  const HUGE = 1 << 20;     // Half the size of the square a new face starts from: beyond any map.

  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const length = a => Math.hypot(a[0], a[1], a[2]);
  const normalize = a => { const l = length(a); return l ? scale(a, 1 / l) : [0, 0, 0]; };
  const lerp = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  // Coordinates are written to six places; rounding near-integers keeps grid-aligned work on the grid.
  const tidy = value => { const near = Math.round(value); return Math.abs(value - near) < 1e-6 ? near + 0 : Math.round(value * 1e6) / 1e6 + 0; };

  const polygonOf = (brush, face) => face.indices.map(index => brush.vertices[index]);
  const centroid = points => scale(points.reduce(add, [0, 0, 0]), 1 / Math.max(points.length, 1));
  // Newell's normal: right-handed for the winding, and sound for slightly bent polygons.
  function newell(points) {
    const n = [0, 0, 0];
    for (let i = 0; i < points.length; i++) {
      const a = points[i], b = points[(i + 1) % points.length];
      n[0] += (a[1] - b[1]) * (a[2] + b[2]);
      n[1] += (a[2] - b[2]) * (a[0] + b[0]);
      n[2] += (a[0] - b[0]) * (a[1] + b[1]);
    }
    return n;
  }
  function area(points) { return length(newell(points)) / 2; }

  // The planes of a brush, pointing out of it whichever way its faces are wound. Faces with no area are passed over.
  function planesOf(brush) {
    const middle = centroid(brush.vertices);
    const planes = [];
    for (const face of brush.faces) {
      const points = polygonOf(brush, face), raw = newell(points);
      if (length(raw) < EPSILON) continue;
      let normal = normalize(raw), distance = dot(normal, centroid(points));
      if (dot(normal, middle) > distance) { normal = scale(normal, -1); distance = -distance; }
      planes.push({normal, distance, face});
    }
    return planes;
  }
  const flip = plane => ({normal: scale(plane.normal, -1), distance: -plane.distance, face: plane.face});
  const samePlane = (a, b) => dot(a.normal, b.normal) > 1 - 1e-6 && Math.abs(a.distance - b.distance) < EPSILON;
  // A plane through three points, facing the right-hand way round them.
  function planeFromPoints(a, b, c, face) {
    const normal = normalize(cross(sub(b, a), sub(c, a)));
    return {normal, distance: dot(normal, a), face};
  }

  // A square on the plane, larger than any map (BaseWindingForPlane), wound counter-clockwise seen from the front.
  function baseWinding(plane) {
    const n = plane.normal, major = Math.abs(n[2]) > Math.abs(n[0]) && Math.abs(n[2]) > Math.abs(n[1]) ? 2 : Math.abs(n[0]) > Math.abs(n[1]) ? 0 : 1;
    const up = major === 2 ? [1, 0, 0] : [0, 0, 1];
    const v = normalize(sub(up, scale(n, dot(up, n)))), u = cross(v, n), origin = scale(n, plane.distance);
    const across = scale(u, HUGE), along = scale(v, HUGE);
    return [sub(sub(origin, across), along), add(sub(origin, across), along), add(add(origin, across), along), sub(add(origin, across), along)].reverse();
  }
  // The part of a polygon behind a plane (Sutherland–Hodgman; ChopWinding), points on it kept.
  function clipPolygon(points, plane) {
    const out = [], sides = points.map(p => dot(plane.normal, p) - plane.distance);
    for (let i = 0; i < points.length; i++) {
      const a = points[i], b = points[(i + 1) % points.length], da = sides[i], db = sides[(i + 1) % points.length];
      if (da <= EPSILON) out.push(a);
      if ((da > EPSILON && db < -EPSILON) || (da < -EPSILON && db > EPSILON)) out.push(lerp(a, b, da / (da - db)));
    }
    return out;
  }
  // Points within WELD of the one before them are one point.
  function dedupe(points) {
    const out = [];
    for (const p of points) if (!out.length || length(sub(p, out[out.length - 1])) > WELD) out.push(p);
    while (out.length > 1 && length(sub(out[0], out[out.length - 1])) <= WELD) out.pop();
    return out;
  }

  // The convex brush the planes bound, each face taking the fields of its plane's face; null where they bound nothing.
  function fromPlanes(planes) {
    const kept = [];
    for (const plane of planes) if (!kept.some(other => samePlane(other, plane))) kept.push(plane);
    const vertices = [], faces = [];
    const weld = p => {
      const point = p.map(tidy);
      let index = vertices.findIndex(v => Math.abs(v[0] - point[0]) <= WELD && Math.abs(v[1] - point[1]) <= WELD && Math.abs(v[2] - point[2]) <= WELD);
      if (index < 0) { index = vertices.length; vertices.push(point); }
      return index;
    };
    for (const plane of kept) {
      let points = baseWinding(plane);
      for (const other of kept) {
        if (other === plane) continue;
        points = clipPolygon(points, other);
        if (points.length < 3) break;
      }
      points = dedupe(points);
      if (points.length < 3 || area(points) < EPSILON) continue;
      const indices = [];
      for (const index of points.map(weld)) if (indices[indices.length - 1] !== index && indices[0] !== index) indices.push(index);
      if (indices.length >= 3) faces.push({...plane.face, indices});
    }
    const brush = {vertices, faces};
    return faces.length >= 4 && volume(brush) > MIN_VOLUME ? brush : null;
  }

  // Signed volume from outward-wound faces (sum of tetrahedra to the origin); for any winding, use Math.abs.
  function volume(brush) {
    let total = 0;
    const middle = centroid(brush.vertices);
    for (const face of brush.faces) {
      const p = polygonOf(brush, face).map(v => sub(v, middle));
      for (let i = 1; i + 1 < p.length; i++) total += dot(p[0], cross(p[i], p[i + 1]));
    }
    return Math.abs(total) / 6;
  }
  function bounds(brush) {
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (const v of brush.vertices) for (let axis = 0; axis < 3; axis++) { min[axis] = Math.min(min[axis], v[axis]); max[axis] = Math.max(max[axis], v[axis]); }
    return {min, max};
  }

  // What a loaded brush is: convex with flat faces, which CSG needs, or not, which it can still be drawn as.
  function check(brush) {
    const problems = [];
    if (brush.vertices.length < 4 || brush.faces.length < 4) problems.push('fewer than four faces');
    const planes = planesOf(brush);
    if (planes.length < brush.faces.length) problems.push('a face with no area');
    for (const plane of planes) {
      const points = polygonOf(brush, plane.face);
      if (points.some(p => Math.abs(dot(plane.normal, p) - plane.distance) > EPSILON * 10)) { problems.push('a bent face'); break; }
    }
    for (const plane of planes) if (brush.vertices.some(v => dot(plane.normal, v) - plane.distance > EPSILON * 10)) { problems.push('not convex'); break; }
    return problems;
  }
  const convex = brush => !check(brush).length;

  // Rebuilds a brush from its own planes: welds its vertices, drops faces with no area, winds faces outward.
  const rebuild = brush => fromPlanes(planesOf(brush));

  function box(min, max, face) {
    const [x0, y0, z0] = min, [x1, y1, z1] = max;
    const vertices = [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]];
    const quads = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [3, 0, 4, 7]];
    return {vertices, faces: quads.map(indices => ({...face, indices}))};
  }

  function translate(brush, offset) {
    return {vertices: brush.vertices.map(v => add(v, offset).map(tidy)), faces: brush.faces.map(face => ({...face, indices: [...face.indices]}))};
  }
  const clone = brush => translate(brush, [0, 0, 0]);

  // Every face moved out along its normal by `distance` (inward when negative); null when that leaves nothing.
  function offset(brush, distance) {
    return fromPlanes(planesOf(brush).map(plane => ({...plane, distance: plane.distance + distance})));
  }

  // The parts of a brush behind and in front of a plane; the cut faces take `face` (default: the plane's own).
  function split(brush, plane, face) {
    const planes = planesOf(brush), cut = {...plane, face: face || plane.face || brush.faces[0]};
    return {back: fromPlanes([...planes, cut]), front: fromPlanes([...planes, flip(cut)])};
  }
  // Clipper tool: keep what is behind the plane through a, b, c (right-hand order), or both halves when `both`.
  function clip(brush, a, b, c, face, both) {
    const parts = split(brush, planeFromPoints(a, b, c), face);
    return [parts.back, both ? parts.front : null].filter(Boolean);
  }

  const intersection = (a, b) => fromPlanes([...planesOf(a), ...planesOf(b)]);
  const intersects = (a, b) => !!intersection(a, b);

  // a without b, as convex pieces (Radiant's CSG Subtract, Quake's SubtractBrush): a is cut by each of b's planes in
  // turn, the part outside kept, the part inside carried on and finally dropped. Cut faces take b's face fields.
  function subtract(a, b) {
    if (!intersects(a, b)) return [a];
    const pieces = [];
    let rest = a;
    for (const plane of planesOf(b)) {
      const parts = split(rest, plane, plane.face);
      if (parts.front) pieces.push(parts.front);
      rest = parts.back;
      if (!rest) break;
    }
    return pieces;
  }
  // Walls `thickness` thick inside the brush, as pieces that do not overlap: the brush without itself shrunk.
  // The inner faces take the fields of the faces they stand behind.
  function hollow(brush, thickness) {
    const inner = offset(brush, -thickness);
    return inner ? subtract(brush, inner) : [brush];
  }

  // The convex hull of points, with each face from the first plane that holds it (brute force; brushes are small).
  function hull(points, planesFor) {
    const unique = [];
    for (const p of points) if (!unique.some(q => length(sub(p, q)) <= WELD)) unique.push(p);
    const planes = [];
    for (let i = 0; i < unique.length; i++) for (let j = i + 1; j < unique.length; j++) for (let k = j + 1; k < unique.length; k++) {
      let plane = planeFromPoints(unique[i], unique[j], unique[k]);
      if (!length(plane.normal)) continue;
      const sides = unique.map(p => dot(plane.normal, p) - plane.distance);
      if (sides.every(s => s <= EPSILON)) { /* Faces out already. */ } else if (sides.every(s => s >= -EPSILON)) plane = flip(plane); else continue;
      if (planes.some(other => samePlane(other, plane))) continue;
      plane.face = planesFor(plane);
      planes.push(plane);
    }
    return fromPlanes(planes);
  }
  // One brush covering exactly a and b (Radiant's CSG Merge); null when their union is not convex.
  function merge(a, b) {
    const sources = [...planesOf(a), ...planesOf(b)];
    const result = hull([...a.vertices, ...b.vertices], plane => (sources.find(source => samePlane(source, plane)) || sources[0]).face);
    if (!result) return null;
    const shared = intersection(a, b), union = volume(a) + volume(b) - (shared ? volume(shared) : 0);
    return Math.abs(volume(result) - union) <= Math.max(MIN_VOLUME, union * 1e-6) ? result : null;
  }

  // Convex polygons of a face as triangles; a bent or concave face is triangulated by `triangulate` when given.
  function triangles(brush, face, triangulate) {
    const n = face.indices.length;
    if (n < 3) return [];
    const points = polygonOf(brush, face), normal = normalize(newell(points));
    let fan = true;
    for (let i = 0; i < n && fan; i++) {
      const a = points[i], b = points[(i + 1) % n], c = points[(i + 2) % n];
      if (dot(cross(sub(b, a), sub(c, b)), normal) < -EPSILON) fan = false;
    }
    if (fan || !triangulate) {
      const out = [];
      for (let i = 1; i + 1 < n; i++) out.push([face.indices[0], face.indices[i], face.indices[i + 1]]);
      return out;
    }
    return triangulate(points, normal).map(tri => tri.map(i => face.indices[i]));
  }

  // The first face a ray meets from outside, {distance, face}; null when it misses (brushes are convex).
  function raycast(brush, origin, direction) {
    let near = -Infinity, far = Infinity, hit = null;
    for (const plane of planesOf(brush)) {
      const along = dot(plane.normal, direction), from = plane.distance - dot(plane.normal, origin);
      if (Math.abs(along) < 1e-12) { if (from < 0) return null; continue; }
      const t = from / along;
      if (along < 0) { if (t > near) { near = t; hit = plane.face; } } else far = Math.min(far, t);
      if (near > far) return null;
    }
    return hit && near >= 0 ? {distance: near, face: hit} : null;
  }

  exports.ReflexBrush = {
    EPSILON, planesOf, planeFromPoints, fromPlanes, rebuild, volume, bounds, check, convex, box, translate, clone, offset,
    split, clip, intersection, intersects, subtract, hollow, hull, merge, triangles, raycast, newell, normalize,
  };
})(typeof module !== 'undefined' ? module.exports : window);

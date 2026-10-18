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
  /* What would stop a brush from being a brush, as fixed codes for the editor's checks before saving: 'too-few-faces'
     (fewer than four faces with area), 'zero-normal' (a face whose corners make no direction: no area, or fewer than
     three), 'duplicate-plane' (two faces on one plane facing one way: harmless, fromPlanes keeps one) and
     'encloses-nothing' (the face planes bound no volume: empty, flat or turned inside out). Bent and concave brushes
     are left to check(): the game draws them. Each issue is {code, face} with the index of the face it is about, or -1. */
  function validate(brush) {
    const issues = [], planes = planesOf(brush), index = plane => brush.faces.indexOf(plane.face);
    brush.faces.forEach((face, i) => { if (!planes.some(plane => plane.face === face)) issues.push({code: 'zero-normal', face: i}); });
    let distinct = 0;
    planes.forEach((plane, i) => { if (planes.slice(0, i).some(other => samePlane(other, plane))) issues.push({code: 'duplicate-plane', face: index(plane)}); else distinct++; });
    if (distinct < 4) issues.push({code: 'too-few-faces', face: -1});
    else if (!fromPlanes(planes)) issues.push({code: 'encloses-nothing', face: -1});
    return issues;
  }
  // The issues that leave no brush. A face with no area in a brush that is otherwise sound is not one: the game ships
  // three in ironguard and loads it.
  const FATAL = new Set(['too-few-faces', 'encloses-nothing']);

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

  // Faces wound outward: each face whose normal points into the brush is turned round. For brushes built corner by
  // corner, whose faces may be bent.
  function orient(brush) {
    const middle = centroid(brush.vertices);
    return {vertices: brush.vertices, faces: brush.faces.map(face => {
      const points = polygonOf(brush, face);
      return dot(newell(points), sub(centroid(points), middle)) < 0 ? {...face, indices: [...face.indices].reverse()} : face;
    })};
  }
  // The turn taking unit vector u onto unit vector v (Rodrigues); half a turn when they are opposite.
  function rotation(u, v) {
    let axis = cross(u, v), s = length(axis);
    const c = dot(u, v);
    if (s < 1e-9) {
      if (c > 0) return x => x;
      axis = normalize(cross(u, Math.abs(u[0]) < .9 ? [1, 0, 0] : [0, 1, 0]));
      return x => sub(scale(axis, 2 * dot(axis, x)), x);
    }
    const k = scale(axis, 1 / s);
    return x => add(add(scale(x, c), scale(cross(k, x), s)), scale(k, dot(k, x) * (1 - c)));
  }
  /* The game's bridge tool (me_startbridge): brushes joining face a to face b in `segments` steps. a and b are the
     faces' corners, wound outward, with their outward normals; they must have as many corners. Each corner of a runs
     to its corner of b along a cubic curve that leaves a along a's normal and comes into b against b's, so faces
     looking at each other make a straight run and faces at an angle an arch or a curved ramp. Which corner of b is
     which corner of a's is found by turning b round as the curve turns, then taking the nearest match. The cross
     sections between are not on the grid, and the side faces of a curved run may bend slightly, as the game allows.
     Every face takes `fields`. Null when the faces cannot be joined. */
  function bridge(a, normalA, b, normalB, segments, fields) {
    const n = a.length;
    if (b.length !== n || n < 3 || segments < 1) return null;
    const ca = centroid(a), cb = centroid(b), back = [...b].reverse();
    const turn = rotation(scale(normalB, -1), normalA);
    let best = 0, cost = Infinity;
    for (let shift = 0; shift < n; shift++) {
      let total = 0;
      for (let i = 0; i < n; i++) total += length(sub(sub(a[i], ca), turn(sub(back[(i + shift) % n], cb))));
      if (total < cost - 1e-9) { cost = total; best = shift; }
    }
    const to = a.map((_, i) => back[(i + best) % n]);
    const reach = length(sub(cb, ca)) * .45;
    const along = (p0, p3, t) => {
      const p1 = add(p0, scale(normalA, reach)), p2 = add(p3, scale(normalB, reach)), u = 1 - t;
      return [0, 1, 2].map(k => tidy(u * u * u * p0[k] + 3 * u * u * t * p1[k] + 3 * u * t * t * p2[k] + t * t * t * p3[k]));
    };
    const sections = [];
    for (let k = 0; k <= segments; k++) sections.push(a.map((p, i) => k === 0 ? p : k === segments ? to[i] : along(p, to[i], k / segments)));
    const brushes = [], ring = [...Array(n).keys()];
    for (let k = 0; k < segments; k++) {
      const faces = [{...fields, indices: ring}, {...fields, indices: ring.map(i => i + n)}];
      for (let i = 0; i < n; i++) faces.push({...fields, indices: [i, (i + 1) % n, (i + 1) % n + n, i + n]});
      const brush = orient({vertices: [...sections[k], ...sections[k + 1]], faces});
      if (volume(brush) <= MIN_VOLUME) return null;
      brushes.push(brush);
    }
    return brushes;
  }

  /* Texture coordinates of a face's corners, [u, v] in world units, as the game maps them (measured in the game):
     u = (p · U + offset u / 16) / scaleU, the same for v, so the texture is anchored to the world and an offset of 16
     moves it one unit toward −U whatever the scale. The frame comes from the face's outward normal n by the axis n is
     nearest: U = (0, 0, 1) × n and V = n × U, except on faces looking mostly along z, where U = +x × n looking along −z
     and +y × n along +z (U = +y, V = +x on a −z face; U = +x, V = +y on +z). So U flips where the nearest axis
     changes (+y on a −z face turned 44° toward −x, −y at 46°), as it does in the game. `rotation` degrees turn both
     right-handed about n. A scale of zero is taken as one.
     `volume`: the frame the game's editor draws a volume's texture in, U = n × +y and V = n × U, so its labels stand
     upright and read left to right on every wall (measured on ±z and +x faces); on a floor or ceiling U = (0, 0, 1) × n
     (not measured: the editor's eye never saw one). */
  function texcoords(brush, face, volume = false) {
    const points = polygonOf(brush, face);
    return uvAt(face, normalize(newell(points)), points, volume);
  }
  // The texture coordinates `face`'s fields give `points` on a plane with outward unit normal n (see texcoords).
  function uvAt(face, n, points, volume = false) {
    const [U0, V0] = textureFrame(n, volume);
    const radians = (face.rotation || 0) * Math.PI / 180, c = Math.cos(radians), s = Math.sin(radians);
    const U = add(scale(U0, c), scale(V0, s)), V = add(scale(V0, c), scale(U0, -s));  // n × U0 = V0, n × V0 = −U0.
    return points.map(p => [(dot(p, U) + (face.u || 0) / 16) / (face.scaleU || 1), (dot(p, V) + (face.v || 0) / 16) / (face.scaleV || 1)]);
  }
  // [U0, V0], the texture axes of a face with outward unit normal n before its rotation (see texcoords).
  function textureFrame(n, volume = false) {
    const reference = volume ? (Math.abs(n[1]) >= Math.abs(n[0]) && Math.abs(n[1]) >= Math.abs(n[2]) ? [0, 0, 1] : [0, -1, 0])
      : Math.abs(n[2]) >= Math.abs(n[0]) && Math.abs(n[2]) >= Math.abs(n[1]) ? (n[2] < 0 ? [1, 0, 0] : [0, 1, 0]) : [0, 0, 1];
    const U0 = normalize(cross(reference, n));
    return [U0, cross(n, U0)];
  }

  /* Texture lock. A copy of `face`, on a plane with outward normal `normal`, with the offsets and rotation that put
     the texture coordinates `uvs` (as texcoords gives them) at `points`, its scale kept: the rigid fit in the face's
     plane (2D Procrustes). Exact when the face only moved or turned; the nearest fit when it was reshaped. With
     q = (p · U0, p · V0) and w = (u × scaleU, v × scaleV), texcoords says w = [[c, s], [−s, c]] q + (offset u, offset v) / 16. */
  function refit(face, normal, points, uvs, volume = false) {
    const [U0, V0] = textureFrame(normalize(normal), volume), su = face.scaleU || 1, sv = face.scaleV || 1;
    const q = points.map(p => [dot(p, U0), dot(p, V0)]), w = uvs.map(([u, v]) => [u * su, v * sv]);
    const mean = list => [0, 1].map(k => list.reduce((sum, x) => sum + x[k], 0) / list.length), mq = mean(q), mw = mean(w);
    let along = 0, across = 0;
    for (let i = 0; i < q.length; i++) {
      const qx = q[i][0] - mq[0], qy = q[i][1] - mq[1], wu = w[i][0] - mw[0], wv = w[i][1] - mw[1];
      along += wu * qx + wv * qy; across += wu * qy - wv * qx;
    }
    const theta = Math.atan2(across, along), c = Math.cos(theta), s = Math.sin(theta), degrees = tidy((theta * 180 / Math.PI + 360) % 360);
    return {...face, u: tidy(16 * (mw[0] - c * mq[0] - s * mq[1])), v: tidy(16 * (mw[1] + s * mq[0] - c * mq[1])), rotation: degrees >= 360 ? 0 : degrees};
  }
  /* `after`, the brush `before` became by a move, turn or reshape, face for face, with each face's texture put back
     where it was on `before` (texture lock). A face whose corners are all where they were is left as it is; where two or
     more stayed, the fit is to those, so a dragged corner leaves the rest of the face's texture pinned. Faces whose
     corners no longer match one for one (a corner welded onto another) keep their fields. Clips, splits and face
     pushes need none of this: the faces they keep stay on their planes, where texture coordinates are fixed to the world. */
  function lock(before, after, volume = false) {
    if (before.faces.length !== after.faces.length) return after;
    return {...after, faces: after.faces.map((face, i) => {
      const old = before.faces[i];
      if (old.indices.length !== face.indices.length) return face;
      const from = polygonOf(before, old), to = polygonOf(after, face), uvs = texcoords(before, old, volume);
      const still = to.map((p, k) => length(sub(p, from[k])) <= WELD ? k : -1).filter(k => k >= 0);
      if (still.length === to.length) return face;
      const use = still.length >= 2 ? still : to.map((_, k) => k);
      return refit(face, newell(to), use.map(k => to[k]), use.map(k => uvs[k]), volume);
    })};
  }

  /* Edges bevelled: each edge [a, b] (two corners where two faces meet) cut back `width` along both faces and rounded
     in `segments` flat steps, as new planes in the brush's set, so it works on any convex brush and on several edges
     at once. The steps' corners lie on the arc tangent to both faces at `width` from the edge: of radius
     width / tan(α / 2), α the angle between the faces' normals. One step is the chamfer through those two lines. A new
     face takes the fields of the face it leans nearer to, its texture refit to run on from that face across the seam
     (exactly along the seam where it meets that face). Null when an edge is not one of the brush's or nothing is left. */
  function bevel(brush, edges, width, segments = 1, volume = false) {
    if (!(width > 0) || !(segments >= 1)) return null;
    const planes = planesOf(brush), added = [];
    for (const [a, b] of edges) {
      const on = planes.filter(plane => [a, b].every(p => Math.abs(dot(plane.normal, p) - plane.distance) <= EPSILON * 10));
      const sides = on.filter((plane, i) => !on.slice(0, i).some(other => samePlane(other, plane)));
      if (length(sub(a, b)) <= WELD || sides.length !== 2) return null;
      const [one, two] = sides, c = dot(one.normal, two.normal);
      if (Math.abs(c) > 1 - 1e-6) return null;
      const alpha = Math.acos(c), radius = width / Math.tan(alpha / 2);
      const centre = sub(a, scale(add(one.normal, two.normal), radius / (1 + c))), across = normalize(sub(two.normal, scale(one.normal, c)));
      for (let i = 0; i < segments; i++) {
        const turn = alpha * (i + .5) / segments, normal = add(scale(one.normal, Math.cos(turn)), scale(across, Math.sin(turn)));
        const from = 2 * i + 1 < segments ? one : two;
        added.push({normal, distance: dot(normal, centre) + radius * Math.cos(alpha / (2 * segments)), face: {...from.face, bevelOf: added.length}, from});
      }
    }
    const result = fromPlanes([...planes, ...added]);
    if (!result) return null;
    return {...result, faces: result.faces.map(face => {
      if (face.bevelOf === undefined) return face;
      const {bevelOf, ...fields} = face, from = added[bevelOf].from, points = polygonOf(result, face);
      const seam = points.filter(p => Math.abs(dot(from.normal, p) - from.distance) <= EPSILON * 10), use = seam.length >= 2 ? seam : points;
      return refit(fields, added[bevelOf].normal, use, uvAt(from.face, from.normal, use, volume), volume);
    })};
  }

  /* Texture wrap: the texture of face `start` ({brush, face}, indices into `brushes`) carried on to the faces beside it,
     and on from each of those, so it runs on unbroken round a pillar or along an arch. A face reached takes the whole
     look of the face it is reached from (material, scale, colour) and the offsets and turn that unroll it about their
     shared edge into that face's plane: the fit to the edge's two corners, which fixes a turn and an offset exactly.
     Faces meet where two of their corners are at the same places, so the faces of separate brushes (an arch's
     segments) join as one brush's do. Across each edge the walk goes on to the one face that turns least from the
     face it comes from, and only when that turn is less than a right angle (so it stays on a pillar's sides, off its
     caps); with `only` (a list of {brush, face}) it goes onto those faces at any angle. It is breadth first, so on a
     closed loop the walks round either way meet opposite the start, where any seam is. Returns `brushes` with the
     brushes it changed replaced. */
  function wrap(brushes, start, only = null, volume = false) {
    // ponytail: corners a hair either side of a rounding step miss each other; generated and grid brushes do not.
    const key = p => p.map(x => Math.round(x / WELD)).join(), edgeKey = (p, q) => [key(p), key(q)].sort().join('|');
    const normals = brushes.map(brush => new Map(planesOf(brush).map(plane => [plane.face, plane.normal])));
    const byEdge = new Map(), corners = (brush, face) => face.indices.map((index, k) => [brush.vertices[index], brush.vertices[face.indices[(k + 1) % face.indices.length]]]);
    brushes.forEach((brush, b) => brush.faces.forEach((face, f) => {
      if (normals[b].has(face)) for (const [p, q] of corners(brush, face)) { const edge = edgeKey(p, q); byEdge.set(edge, [...(byEdge.get(edge) || []), [b, f]]); }
    }));
    const allowed = only && new Set(only.map(({brush, face}) => `${brush}:${face}`));
    const out = brushes.map(brush => ({...brush, faces: [...brush.faces]})), done = new Set([`${start.brush}:${start.face}`]), queue = [[start.brush, start.face]];
    while (queue.length) {
      const [b, f] = queue.shift(), face = out[b].faces[f], n = normals[b].get(brushes[b].faces[f]);
      for (const [p, q] of corners(brushes[b], brushes[b].faces[f])) {
        let best = null;
        for (const [c, g] of byEdge.get(edgeKey(p, q)) || []) {
          const id = `${c}:${g}`, turn = dot(n, normals[c].get(brushes[c].faces[g]));
          if ((c === b && g === f) || (allowed ? !allowed.has(id) : turn <= EPSILON)) continue;
          if (!best || turn > best.turn) best = {c, g, id, turn};
        }
        // The way on already taken from elsewhere ends the walk here, rather than turning onto the next best face.
        if (!best || done.has(best.id)) continue;
        const {c, g, id} = best, {indices, ...look} = face;
        done.add(id);
        out[c].faces[g] = refit({...look, indices: out[c].faces[g].indices}, normals[c].get(brushes[c].faces[g]), [p, q], uvAt(face, n, [p, q], volume), volume);
        queue.push([c, g]);
      }
    }
    return out.map((brush, b) => brush.faces.every((face, f) => face === brushes[b].faces[f]) ? brushes[b] : brush);
  }

  /* ---- Shapes: groups of convex brushes made from a few numbers (the parameters an editor keeps to remake them), in
     their own frame with y up as Reflex and Unity have it: x across, z along. Every face takes `fields`. Each returns
     the brushes, or null when the numbers make none. Geometry only: no texture assumptions. ---- */
  // The brush between a convex polygon `points` (either winding) and the same moved by `along`; null when flat.
  function extrude(points, along, fields = {}) {
    const n = points.length, ring = [...Array(n).keys()];
    const faces = [{...fields, indices: ring}, {...fields, indices: ring.map(i => i + n)}];
    for (let i = 0; i < n; i++) faces.push({...fields, indices: [i, (i + 1) % n, (i + 1) % n + n, i + n]});
    const brush = orient({vertices: [...points, ...points.map(p => add(p, along))].map(p => p.map(tidy)), faces});
    return n >= 3 && volume(brush) > MIN_VOLUME ? brush : null;
  }
  const every = list => list.every(Boolean) ? list : null;
  /* `sides` pieces of a ring round axis `axis` (0 x, 1 y, 2 z) through the origin, between radii `inner` (0: wedges
     meeting on the axis) and `outer`, from `from` to `to` along the axis, sweeping `degrees` from `start`. Angle 0 is
     along the next axis round (z for y, x for z) and 90 along the one after, so round y, +z turns toward +x as a yaw
     does. Each piece is the quad of the chords at its two angles; neighbours share those corners exactly. */
  function ring({axis, inner, outer, from, to, sides, degrees, start = 0}, fields = {}) {
    if (!(inner >= 0 && outer > inner && to > from && sides >= 1 && sides <= 256 && degrees > 0 && degrees <= 360 && degrees / sides < 180)) return null;
    const point = (radius, k) => {
      const angle = (start + degrees * k / sides) * Math.PI / 180, p = [0, 0, 0];
      p[(axis + 1) % 3] = radius * Math.cos(angle); p[(axis + 2) % 3] = radius * Math.sin(angle); p[axis] = from;
      return p;
    };
    const along = [0, 0, 0], pieces = [];
    along[axis] = to - from;
    for (let k = 0; k < sides; k++) pieces.push(extrude(inner > 0 ? [point(outer, k), point(outer, k + 1), point(inner, k + 1), point(inner, k)] : [point(outer, k), point(outer, k + 1), point(0, k)], along, fields));
    return every(pieces);
  }
  // Solid stairs: `steps` boxes `width` wide, centred on x, each `run` deep along +z and reaching `rise` higher than the
  // one before, from the floor (y 0) up.
  function stairs({steps, rise, run, width}, fields = {}) {
    if (!(steps >= 1 && steps <= 256 && rise > 0 && run > 0 && width > 0)) return null;
    return [...Array(Math.round(steps)).keys()].map(i => box([-width / 2, 0, i * run], [width / 2, (i + 1) * rise, (i + 1) * run], fields));
  }
  // An arch across x: `segments` pieces of a band `thickness` deep inside an outer `span`, `depth` along z (centred), its
  // springing line on y 0, sweeping `degrees` (180 a half circle, 360 a ring) centred on the top.
  function arch({span, thickness, depth, segments, degrees = 180}, fields = {}) {
    return ring({axis: 2, inner: span / 2 - thickness, outer: span / 2, from: -depth / 2, to: depth / 2, sides: segments, degrees, start: 90 - degrees / 2}, fields);
  }
  // A doorway frame: two legs `thickness` wide and a half-circle arch on them, `width` across outside, `height` to the
  // top of the arch, `depth` along z (centred), standing on y 0.
  function doorArch({width, height, thickness, depth, segments}, fields = {}) {
    const r = width / 2, leg = height - r;
    const top = leg >= 0 && thickness < r ? arch({span: width, thickness, depth, segments}, fields) : null;
    if (!top) return null;
    const legs = leg > 0 ? [box([-r, 0, -depth / 2], [-r + thickness, leg, depth / 2], fields), box([r - thickness, 0, -depth / 2], [r, leg, depth / 2], fields)] : [];
    return [...legs, ...top.map(brush => translate(brush, [0, leg, 0]))];
  }
  // A hallway curving round the y axis, sweeping `degrees` from +z toward +x in `segments` pieces: its walk `width`
  // wide from `radius` out, a floor and a ceiling `floor` thick over the walk, walls `wall` thick either side,
  // `height` from the floor's underside to the ceiling's top. Four rings, sharing their radial planes.
  function hallway({radius, width, wall, floor, height, segments, degrees}, fields = {}) {
    if (!(radius - wall >= 0 && width > 0 && wall > 0 && floor > 0 && height > 2 * floor)) return null;
    const part = (inner, outer, from, to) => ring({axis: 1, inner, outer, from, to, sides: segments, degrees}, fields);
    const parts = [part(radius, radius + width, 0, floor), part(radius, radius + width, height - floor, height),
      part(radius - wall, radius, 0, height), part(radius + width, radius + width + wall, 0, height)];
    return parts.every(Boolean) ? parts.flat() : null;
  }
  // Radial array: copies of `brushes` turned about the vertical through `centre`, `count` in all with the originals
  // (which are not returned), each `angle` degrees on from the one before, +z toward +x (as a yaw turns).
  function radial(brushes, centre, count, angle) {
    const copies = [];
    for (let k = 1; k < count; k++) {
      const a = k * angle * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
      const turn = ([x, y, z]) => [centre[0] + (x - centre[0]) * c + (z - centre[2]) * s, y, centre[2] - (x - centre[0]) * s + (z - centre[2]) * c].map(tidy);
      for (const brush of brushes) copies.push({vertices: brush.vertices.map(turn), faces: brush.faces.map(face => ({...face, indices: [...face.indices]}))});
    }
    return copies;
  }

  // Two faces look the same when every field but their corners is the same.
  const sameLook = (f, g) => [...new Set([...Object.keys(f), ...Object.keys(g)])].every(key => key === 'indices' || f[key] === g[key]);
  // Brushes joined pairwise wherever two together are convex and their faces on a shared plane look the same (the
  // pieces subtract leaves, put back together where they can be). With `looks` false, faces need not look the same:
  // a cutter of several brushes joined into the one convex brush they make, where they make one, before carving.
  function mergeAll(brushes, looks = true) {
    const left = [...brushes];
    for (let merged = true; merged;) {
      merged = false;
      for (let i = 0; i < left.length && !merged; i++) for (let j = i + 1; j < left.length && !merged; j++) {
        const a = planesOf(left[i]), b = planesOf(left[j]);
        if (looks && a.some(pa => b.some(pb => samePlane(pa, pb) && !sameLook(pa.face, pb.face)))) continue;
        const one = merge(left[i], left[j]);
        if (one) { left[i] = one; left.splice(j, 1); merged = true; }
      }
    }
    return left;
  }

  // The convex pieces of a convex polygon outside the convex brush `planes` bound; a point on a plane counts as inside.
  function outside(points, planes) {
    // A brush that covers none of the polygon leaves it whole rather than cut along its planes.
    let inside = points;
    for (const plane of planes) if ((inside = dedupe(clipPolygon(inside, plane))).length < 3) break;
    if (inside.length < 3 || area(inside) < EPSILON) return [points];
    const out = [];
    let rest = points;
    for (const plane of planes) {
      const sides = rest.map(p => dot(plane.normal, p) - plane.distance);
      if (Math.max(...sides) <= EPSILON) continue;
      if (Math.min(...sides) >= -EPSILON) { out.push(rest); break; }
      out.push(dedupe(clipPolygon(rest, flip(plane))));
      rest = dedupe(clipPolygon(rest, plane));
      if (rest.length < 3) break;
    }
    return out.filter(piece => piece.length >= 3 && area(piece) >= EPSILON);
  }
  /* What of each face no solid brush hides, for a map exported as a mesh (as qbsp's CSG drops what brushes bury): for
     brush i and face f, exposed(…)[i][f] lists convex polygons, each face cut by every other solid brush whose bounds
     reach it and what is inside dropped. Inside takes in points on a plane, so where two brushes press faces together
     both faces go. Of two faces on one plane facing one way the later brush's is kept. `solid[i]` says whether brush i
     hides what is in it (not glass, clip or a volume); a brush that is not convex hides nothing and is not cut. With
     `only` (a Set of indices), brushes not in it are hidden by but not cut themselves, and come out null. */
  function exposed(brushes, solid, only = null) {
    const boxes = brushes.map(bounds), sound = brushes.map(convex), planes = brushes.map(brush => planesOf(brush));
    const reach = (a, b) => [0, 1, 2].every(k => a.min[k] <= b.max[k] + EPSILON && b.min[k] <= a.max[k] + EPSILON);
    return brushes.map((brush, i) => {
      if (only && !only.has(i)) return null;
      const near = sound[i] ? brushes.map((_, j) => j).filter(j => j !== i && solid[j] && sound[j] && reach(boxes[i], boxes[j])) : [];
      return brush.faces.map(face => {
        const own = planes[i].find(plane => plane.face === face);
        let pieces = [polygonOf(brush, face)];
        if (own) for (const j of near) {
          if (j < i && planes[j].some(plane => samePlane(plane, own))) continue;
          pieces = pieces.flatMap(piece => outside(piece, planes[j]));
          if (!pieces.length) break;
        }
        return pieces;
      });
    });
  }

  // For each brush, the indices of its faces solid brushes bury wholly (exposed leaves nothing of them), or [] for a
  // brush in `only` (a Set of indices) left out; faces only partly buried are not listed.
  function buried(brushes, solid, only = null) {
    const parts = exposed(brushes, solid, only);
    return parts.map(faces => faces ? faces.flatMap((pieces, f) => pieces.length ? [] : [f]) : []);
  }

  exports.ReflexBrush = {
    EPSILON, planesOf, planeFromPoints, fromPlanes, rebuild, volume, bounds, check, convex, box, translate, clone, offset,
    split, clip, intersection, intersects, subtract, hollow, hull, merge, mergeAll, triangles, raycast, newell, normalize,
    orient, bridge, texcoords, uvAt, textureFrame, refit, lock, bevel, exposed, buried, validate, FATAL, wrap,
    extrude, ring, stairs, arch, doorArch, hallway, radial,
  };
})(typeof module !== 'undefined' ? module.exports : window);

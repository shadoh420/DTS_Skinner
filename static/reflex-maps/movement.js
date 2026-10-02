/* Play mode for the Reflex map page: a player box moved through the map's brushes as Quake 3 moves one (pmove's
   friction, acceleration, gravity, jumping, sliding along walls and stepping up stairs), not Reflex's own movement,
   which is CPMA's and is not copied. Node runs it too (tests/reflex_maps.test.cjs).

   Collision is Quake 3's box trace (CM_TraceThroughBrush): each brush is its face planes, pushed out by the box's
   extent along each normal, plus a plane square to each axis where the brush has none (its axial bevels), and a box
   moving from start to end stops at the first plane it would cross into the brush. Axes are the map's: y up.
   Quake 3's numbers: a box 30 wide and 56 tall with the eye 26 above its middle, 320 units a second, gravity 800, a
   jump of 270 units a second, steps up to 18 units, walkable slopes up to about 45 degrees. */
(function (exports) {
  'use strict';
  const B = typeof module !== 'undefined' ? require('./brush.js').ReflexBrush : window.ReflexBrush;
  const PLAYER = {mins: [-15, -24, -15], maxs: [15, 32, 15], eye: 26};
  const SPEED = 320, ACCELERATE = 10, AIR_ACCELERATE = 1, FRICTION = 6, STOP_SPEED = 100, GRAVITY = 800, JUMP = 270, STEP = 18;
  const SURFACE_CLIP = .125, OVERCLIP = 1.001, WALKABLE = .7, CELL = 256;

  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const add = (a, b, s = 1) => [a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const length = a => Math.hypot(a[0], a[1], a[2]);

  // A brush's planes for collision: its faces' and its missing axial bevels.
  function solidOf(brush, tag) {
    const planes = B.planesOf(brush).map(({normal, distance}) => ({normal, distance}));
    const {min, max} = B.bounds(brush);
    for (let axis = 0; axis < 3; axis++) for (const sign of [1, -1]) {
      if (planes.some(plane => plane.normal[axis] * sign > 1 - 1e-6)) continue;
      const normal = [0, 0, 0];
      normal[axis] = sign;
      planes.push({normal, distance: sign > 0 ? max[axis] : -min[axis]});
    }
    return {planes, min, max, tag};
  }

  /* The brushes to collide with, in a grid of 256-unit cells so a trace looks only at those near it. `brushes` is a
     list of {brush, tag}; a trace reports the tag of what it hit. */
  function createWorld(brushes) {
    const solids = brushes.filter(entry => entry.brush.vertices.length >= 4).map(entry => solidOf(entry.brush, entry.tag));
    const cells = new Map(), big = [], key = (x, y, z) => `${x},${y},${z}`;
    const range = (min, max) => [Math.floor(min / CELL), Math.floor(max / CELL)];
    solids.forEach((solid, index) => {
      const [x0, x1] = range(solid.min[0], solid.max[0]), [y0, y1] = range(solid.min[1], solid.max[1]), [z0, z1] = range(solid.min[2], solid.max[2]);
      // A brush over thousands of cells (a skybox wall) is kept aside and always looked at.
      if ((x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1) > 4096) { big.push(index); return; }
      for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) for (let z = z0; z <= z1; z++) {
        const k = key(x, y, z);
        if (!cells.has(k)) cells.set(k, []);
        cells.get(k).push(index);
      }
    });
    let stamp = 0;
    const seen = new Uint32Array(solids.length);
    function near(min, max) {
      stamp++;
      const out = [];
      const take = index => { if (seen[index] !== stamp) { seen[index] = stamp; out.push(solids[index]); } };
      big.forEach(take);
      const [x0, x1] = range(min[0], max[0]), [y0, y1] = range(min[1], max[1]), [z0, z1] = range(min[2], max[2]);
      for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) for (let z = z0; z <= z1; z++) (cells.get(key(x, y, z)) || []).forEach(take);
      return out;
    }
    // Where a box of `mins` to `maxs` about its origin, moving from `start` to `end`, stops: {fraction, end,
    // normal, tag, startSolid, allSolid}.
    function trace(start, end, mins = PLAYER.mins, maxs = PLAYER.maxs) {
      const result = {fraction: 1, end, normal: null, tag: null, startSolid: false, allSolid: false};
      const low = [0, 1, 2].map(i => Math.min(start[i], end[i]) + mins[i] - 1), high = [0, 1, 2].map(i => Math.max(start[i], end[i]) + maxs[i] + 1);
      for (const solid of near(low, high)) {
        if (solid.max[0] < low[0] || solid.min[0] > high[0] || solid.max[1] < low[1] || solid.min[1] > high[1] || solid.max[2] < low[2] || solid.min[2] > high[2]) continue;
        let enter = -1, leave = 1, startOut = false, getOut = false, hit = null, out = false;
        for (const plane of solid.planes) {
          const n = plane.normal;
          // The plane pushed out by the corner of the box that meets it first.
          const distance = plane.distance - (n[0] * (n[0] < 0 ? maxs[0] : mins[0]) + n[1] * (n[1] < 0 ? maxs[1] : mins[1]) + n[2] * (n[2] < 0 ? maxs[2] : mins[2]));
          const d1 = dot(start, n) - distance, d2 = dot(end, n) - distance;
          if (d2 > 0) getOut = true;
          if (d1 > 0) startOut = true;
          if (d1 > 0 && (d2 >= SURFACE_CLIP || d2 >= d1)) { out = true; break; }
          if (d1 <= 0 && d2 <= 0) continue;
          if (d1 > d2) {
            const f = Math.max(0, (d1 - SURFACE_CLIP) / (d1 - d2));
            if (f > enter) { enter = f; hit = n; }
          } else leave = Math.min(leave, (d1 + SURFACE_CLIP) / (d1 - d2));
        }
        if (out) continue;
        if (!startOut) {
          result.startSolid = true;
          if (!getOut) { result.allSolid = true; result.fraction = 0; result.tag = solid.tag; }
          continue;
        }
        if (enter < leave && enter > -1 && enter < result.fraction) { result.fraction = Math.max(0, enter); result.normal = hit; result.tag = solid.tag; }
      }
      result.end = result.fraction === 1 ? end : add(start, [end[0] - start[0], end[1] - start[1], end[2] - start[2]], result.fraction);
      return result;
    }
    return {trace, count: solids.length};
  }

  const clipVelocity = (velocity, normal, overbounce = OVERCLIP) => {
    let back = dot(velocity, normal);
    back = back < 0 ? back * overbounce : back / overbounce;
    return add(velocity, normal, -back);
  };

  // Moves the player through the world for `time` seconds along its velocity, sliding along what it meets (Quake 3's
  // PM_SlideMove). Returns whether it met anything.
  function slideMove(player, world, time) {
    const planes = player.ground ? [player.ground] : [];
    planes.push(length(player.velocity) ? player.velocity.map(v => v / length(player.velocity)) : [0, 0, 0]);
    const original = player.velocity;
    let left = time, bumped = false;
    for (let bump = 0; bump < 4; bump++) {
      const end = add(player.origin, player.velocity, left), trace = world.trace(player.origin, end);
      if (trace.allSolid) { player.velocity = [player.velocity[0], 0, player.velocity[2]]; return true; }
      if (trace.fraction > 0) player.origin = trace.end;
      if (trace.fraction === 1) break;
      bumped = true;
      left -= left * trace.fraction;
      planes.push(trace.normal);
      // Clip the velocity to every plane met, and where two meet, run along their crease; stop in a corner of three.
      let found = false;
      for (let i = 0; i < planes.length && !found; i++) {
        if (dot(player.velocity, planes[i]) >= .1) continue;
        let clipped = clipVelocity(player.velocity, planes[i]);
        let stuck = false;
        for (let j = 0; j < planes.length; j++) {
          if (j === i || dot(clipped, planes[j]) >= .1) continue;
          clipped = clipVelocity(clipped, planes[j]);
          if (dot(clipped, planes[i]) >= 0) continue;
          const crease = cross(planes[i], planes[j]), size = length(crease);
          if (size < 1e-6) { stuck = true; break; }
          const direction = crease.map(c => c / size);
          clipped = direction.map(c => c * dot(direction, player.velocity));
          for (let k = 0; k < planes.length; k++) if (k !== i && k !== j && dot(clipped, planes[k]) < .1) stuck = true;
          break;
        }
        if (stuck) { player.velocity = [0, 0, 0]; return true; }
        player.velocity = clipped;
        found = true;
      }
      if (dot(player.velocity, original) <= 0) { player.velocity = [0, 0, 0]; break; }
    }
    return bumped;
  }
  // PM_StepSlideMove: a move that meets something on the ground is tried again from a step up, then set down.
  function stepSlideMove(player, world, time) {
    const startOrigin = player.origin, startVelocity = player.velocity;
    if (!slideMove(player, world, time)) return;
    // Never step up while still going up, unless standing on something.
    const down = world.trace(startOrigin, add(startOrigin, [0, -STEP, 0]));
    if (player.velocity[1] > 0 && (down.fraction === 1 || !down.normal || down.normal[1] < WALKABLE)) return;
    const slid = {origin: player.origin, velocity: player.velocity};
    const up = world.trace(startOrigin, add(startOrigin, [0, STEP, 0]));
    if (up.allSolid) return;
    const height = up.end[1] - startOrigin[1];
    player.origin = up.end; player.velocity = startVelocity;
    slideMove(player, world, time);
    const settle = world.trace(player.origin, add(player.origin, [0, -height, 0]));
    if (!settle.allSolid) player.origin = settle.end;
    if (settle.fraction < 1) player.velocity = clipVelocity(player.velocity, settle.normal);
    // Keep the step only where it got further across than sliding did.
    const across = (a, b) => Math.hypot(a[0] - b[0], a[2] - b[2]);
    if (across(player.origin, startOrigin) + 1e-6 < across(slid.origin, startOrigin) || settle.normal && settle.normal[1] < WALKABLE && settle.fraction < 1) {
      player.origin = slid.origin; player.velocity = slid.velocity;
    }
  }
  // Whether the player stands on something walkable; sets player.ground to its normal.
  function groundCheck(player, world) {
    const trace = world.trace(player.origin, add(player.origin, [0, -.25, 0]));
    // Moving up off a surface (a jump, a jump pad) leaves the ground.
    player.ground = trace.fraction < 1 && trace.normal && trace.normal[1] >= WALKABLE && !(player.velocity[1] > 0 && dot(player.velocity, trace.normal) > 10) ? trace.normal : null;
    return trace;
  }
  function accelerate(player, direction, wishSpeed, rate, time) {
    const add_ = Math.min(wishSpeed - dot(player.velocity, direction), rate * time * wishSpeed);
    if (add_ > 0) player.velocity = add(player.velocity, direction, add_);
  }
  /* One step of `time` seconds. `input`: {forward, right (−1 to 1), jump (held), yaw (radians; 0 looks along +z,
     turning toward +x)}. The player is {origin (the box's middle), velocity, ground}. */
  function move(player, input, world, time) {
    groundCheck(player, world);
    const forward = [Math.sin(input.yaw), 0, Math.cos(input.yaw)], right = [Math.cos(input.yaw), 0, -Math.sin(input.yaw)];
    let wish = add(forward.map(v => v * input.forward), right, input.right);
    const size = Math.hypot(wish[0], wish[2]);
    const wishSpeed = Math.min(size, 1) * SPEED;
    wish = size ? wish.map(v => v / size) : wish;
    if (player.ground && input.jump) {
      player.velocity = [player.velocity[0], JUMP, player.velocity[2]];
      player.ground = null;
    }
    if (player.ground) {
      // Friction, then acceleration along the ground.
      const speed = length(player.velocity);
      if (speed > 0) {
        const drop = Math.max(speed, STOP_SPEED) * FRICTION * time, scale = Math.max(speed - drop, 0) / speed;
        player.velocity = player.velocity.map(v => v * scale);
      }
      accelerate(player, wish, wishSpeed, ACCELERATE, time);
      const speedNow = length(player.velocity);
      player.velocity = clipVelocity(player.velocity, player.ground);
      const clippedSpeed = length(player.velocity);
      if (clippedSpeed > 0) player.velocity = player.velocity.map(v => v * speedNow / clippedSpeed);
    } else {
      accelerate(player, wish, wishSpeed, AIR_ACCELERATE, time);
      player.velocity = [player.velocity[0], player.velocity[1] - GRAVITY * time, player.velocity[2]];
    }
    stepSlideMove(player, world, time);
    groundCheck(player, world);
    if (player.ground && player.velocity[1] < 0) player.velocity = [player.velocity[0], 0, player.velocity[2]];
  }
  // The velocity that takes a player from `from` to land on `to` at the top of its arc (Quake 3's jump pad,
  // AimAtTarget); a target below gets a hop that carries it across.
  function launch(from, to) {
    const height = Math.max(to[1] - from[1], 32), time = Math.sqrt(height / (.5 * GRAVITY));
    const across = [to[0] - from[0], 0, to[2] - from[2]], distance = Math.hypot(across[0], across[2]);
    const speed = time ? distance / time : 0;
    return [distance ? across[0] / distance * speed : 0, time * GRAVITY, distance ? across[2] / distance * speed : 0];
  }
  // A place for the box near `origin` that is not inside a brush: as it is, or raised up to 128 units.
  function free(world, origin) {
    for (let up = 0; up <= 128; up += 8) {
      const at = add(origin, [0, up, 0]);
      if (!world.trace(at, at).startSolid) return at;
    }
    return null;
  }

  exports.ReflexMovement = {PLAYER, SPEED, GRAVITY, JUMP, STEP, createWorld, move, launch, free, clipVelocity};
})(typeof module !== 'undefined' ? module.exports : window);

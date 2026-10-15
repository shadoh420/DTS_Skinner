// What moves on the Reflex Maps page, kept apart from the page so Node can test it: a mesh's looping animation
// (tools/reflex_models.py writes it) posed at a time, and a particle's age in its emitter's cycle.
(function (exports) {
  'use strict';

  // Row-major 4 × 4 matrices (a vertex is a row): a × b.
  function multiply4(a, b) {
    const out = new Array(16);
    for (let i = 0; i < 16; i++) {
      const r = i & 12, c = i & 3;
      out[i] = a[r] * b[c] + a[r + 1] * b[4 + c] + a[r + 2] * b[8 + c] + a[r + 3] * b[12 + c];
    }
    return out;
  }

  // Each bone's skin matrix at `time` (seconds, looping): its records (position, quaternion x y z w, scale; in its
  // parent's frame) at the two frames around time × fps blended (the quaternion the short way round, normalised;
  // after the last frame comes the first), then down its parents, then its bind before it: a vertex weighted to the
  // bone is moved by it.
  function skinMatrices({bones, frames, fps, parents, binds, records}, time) {
    const at = ((time * fps) % frames + frames) % frames, k0 = Math.floor(at), k1 = (k0 + 1) % frames, t = at - k0;
    const world = [], skins = [];
    for (let b = 0; b < bones; b++) {
      const r0 = (b * frames + k0) * 10, r1 = (b * frames + k1) * 10;
      let dot = 0;
      for (let k = 3; k < 7; k++) dot += records[r0 + k] * records[r1 + k];
      const v = [];
      for (let k = 0; k < 10; k++) v.push(records[r0 + k] + ((k >= 3 && k < 7 && dot < 0 ? -1 : 1) * records[r1 + k] - records[r0 + k]) * t);
      const l = Math.hypot(v[3], v[4], v[5], v[6]) || 1, x = v[3] / l, y = v[4] / l, z = v[5] / l, w = v[6] / l;
      const local = [(1 - 2 * (y * y + z * z)) * v[7], 2 * (x * y + z * w) * v[7], 2 * (x * z - y * w) * v[7], 0,
        2 * (x * y - z * w) * v[8], (1 - 2 * (x * x + z * z)) * v[8], 2 * (y * z + x * w) * v[8], 0,
        2 * (x * z + y * w) * v[9], 2 * (y * z - x * w) * v[9], (1 - 2 * (x * x + y * y)) * v[9], 0, v[0], v[1], v[2], 1];
      world.push(parents[b] >= 0 && parents[b] < b ? multiply4(local, world[parents[b]]) : local);
      skins.push(multiply4(Array.from(binds.slice(b * 16, b * 16 + 16)), world[b]));
    }
    return skins;
  }

  // A particle's age at `time`: its slot's offset on, round its emitter's period; null once past its life (not drawn).
  // The page's particle shader does the same.
  function particleAge(time, offset, period, life) {
    const age = ((time + offset) % period + period) % period;
    return age > life ? null : age;
  }

  // The looping animation tools/reflex_models.py write_mesh puts after a model's parts, when there is one ('ANIM'):
  // u32 bones, u32 frames, f32 frames a second, i32 parents, 16 floats of bind per bone, 10 floats per bone per frame,
  // then u32 the count of moving parts and each one's number, per vertex four float weights and four bone bytes, and its
  // float positions and normals before posing. Each moving part gets `moving` {animation, weights, joints, positions,
  // normals}.
  function readAnimation(buffer, at, parts) {
    const view = new DataView(buffer);
    if (at + 16 > buffer.byteLength || view.getUint32(at, true) !== 0x4d494e41) return null;
    const bones = view.getUint32(at + 4, true), frames = view.getUint32(at + 8, true), fps = view.getFloat32(at + 12, true);
    at += 16;
    const parents = new Int32Array(buffer, at, bones); at += bones * 4;
    const binds = new Float32Array(buffer, at, bones * 16); at += bones * 64;
    const records = new Float32Array(buffer, at, bones * frames * 10); at += bones * frames * 40;
    const animation = {bones, frames, fps, parents, binds, records}, moving = view.getUint32(at, true);
    at += 4;
    for (let k = 0; k < moving; k++) {
      const part = parts[view.getUint32(at, true)], count = part.positions.length / 3;
      at += 4;
      const weights = new Float32Array(count * 4), joints = new Uint8Array(count * 4);
      for (let v = 0; v < count; v++, at += 20) {
        for (let c = 0; c < 4; c++) { weights[v * 4 + c] = view.getFloat32(at + c * 4, true); joints[v * 4 + c] = view.getUint8(at + 16 + c); }
      }
      const positions = new Float32Array(buffer, at, count * 3); at += count * 12;
      const normals = new Float32Array(buffer, at, count * 3); at += count * 12;
      part.moving = {animation, weights, joints, positions, normals};
    }
    return animation;
  }

  exports.ReflexMotion = {multiply4, skinMatrices, particleAge, readAnimation};
})(typeof module !== 'undefined' ? module.exports : window);

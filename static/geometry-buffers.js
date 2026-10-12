/* The write_model_data container, also used for JA map prop surfaces. */
(function (root) {
  'use strict';
  function filename(description) {
    if (!/^[a-f0-9]{64}\.bin$/.test(description.file)) throw Error('Invalid geometry filename');
    return description.file;
  }
  function surfaces(model, binary) {
    filename(model.geometry_buffers);
    const view = new DataView(binary), arrays = {};
    let end = 0;
    for (const [key, item] of Object.entries(model.geometry_buffers.arrays)) {
      const size = item.dtype === '<f8' ? 8 : 4;
      if (!['<f4', '<f8', '<u4'].includes(item.dtype) || !Number.isSafeInteger(item.count) || item.count < 0 ||
          item.offset !== end || end + item.count * size > binary.byteLength) throw Error('Invalid geometry range');
      const values = arrays[key] = new Array(item.count);
      for (let i = 0; i < item.count; i++) {
        const offset = end + i * size;
        values[i] = item.dtype === '<u4' ? view.getUint32(offset, true) :
          size === 8 ? view.getFloat64(offset, true) : view.getFloat32(offset, true);
        if (!Number.isFinite(values[i])) throw Error('Non-finite geometry');
      }
      end += item.count * size;
    }
    if (end !== binary.byteLength) throw Error('Trailing geometry bytes');
    return model.groups.map(group => {
      const first = group.vertexStart, last = first + group.vertexCount;
      if (![first, group.vertexCount, group.start, group.count].every(x => Number.isSafeInteger(x) && x >= 0) ||
          last * 3 > arrays.vertices.length || last * 3 > arrays.normals.length || last * 2 > arrays.uvs.length ||
          group.start + group.count > arrays.indices.length) throw Error('Invalid surface range');
      const indices = arrays.indices.slice(group.start, group.start + group.count).map(i => i - first);
      if (indices.some(i => !Number.isInteger(i) || i < 0 || i >= group.vertexCount)) throw Error('Invalid surface index');
      return {vertices: arrays.vertices.slice(first * 3, last * 3), normals: arrays.normals.slice(first * 3, last * 3),
        uvs: arrays.uvs.slice(first * 2, last * 2), indices};
    });
  }
  const api = {filename, surfaces};
  if (typeof module !== 'undefined') module.exports = api;
  else root.GeometryBuffers = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);

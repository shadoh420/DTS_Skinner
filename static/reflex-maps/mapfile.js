/* Reads and writes Reflex Arena map files (.map, "reflex map version 8" and the older 6), which the game draws as
   they are: no compile step, every brush stored as its own vertices and faces. Node runs it too
   (tests/reflex_maps.test.cjs). Writing what was read gives the same file, byte for byte.

   The file is indented with tabs and lines end CR LF. Version 8:

     reflex map version 8
     prefab <name>            a group of entities and brushes in its own coordinates, placed by Prefab entities
     	entity
     		type <Type>
     		<PropertyType> <name> <value…>
     	brush
     		vertices
     			<x> <y> <z>
     		faces
     			<offset u> <offset v> <scale u> <scale v> <rotation> <vertex index…> <0xAARRGGBB> <material>
     global                   the map itself, laid out as a prefab is

   Entities and brushes of a group come in the order they were made, so they are kept as one list. A face's
   material may be empty (the line then ends in a space after the colour), and version 6 has no colour. Version 6
   files have no group lines: entities and brushes stand at the top level, with one less tab. Coordinates are y up
   and faces are wound counter-clockwise seen from outside (normal by the right-hand rule on the numbers as
   written); see docs/reflex-map-viewer.md. */
(function (exports) {
  'use strict';
  const NUMBER = /^-?\d+(\.\d+)?([eE][-+]?\d+)?$/, INDEX = /^\d+$/, COLOUR = /^0x[0-9a-fA-F]{1,8}$/;
  // How each property type is held and written. Strings are the rest of the line and may hold spaces.
  const PROPERTY = {
    Vector3: {read: words => words.slice(0, 3).map(Number), write: value => value.map(fixed).join(' ')},
    Float: {read: words => Number(words[0]), write: fixed},
    UInt8: {read: words => Number(words[0]), write: String},
    UInt16: {read: words => Number(words[0]), write: String},
    UInt32: {read: words => Number(words[0]), write: String},
    Int32: {read: words => Number(words[0]), write: String},
    Bool8: {read: words => Number(words[0]), write: String},
  };
  // Numbers are written as the game writes them: six places, keeping the sign of a negative zero. From 10^17 up the
  // game's C runtime gives 17 significant digits and then zeros (AbandonedShelter has a face turned by
  // 1602806319568810500000000.000000 degrees), where toFixed would give every digit or, past 10^21, an exponent.
  function fixed(value) {
    value = Number(value);
    if (Math.abs(value) >= 1e17 && Number.isFinite(value)) {
      const [digits, exponent] = Math.abs(value).toExponential(16).split('e');
      return (value < 0 ? '-' : '') + digits.replace('.', '').padEnd(Number(exponent) + 1, '0') + '.000000';
    }
    const text = value.toFixed(6);
    return Object.is(value, -0) && text[0] !== '-' ? '-' + text : text;
  }

  class MapError extends Error {
    constructor(line, message) { super(`line ${line}: ${message}`); this.line = line; }
  }

  function parse(text) {
    const lines = text.split(/\r?\n/);
    const crlf = /\r\n/.test(text);
    while (lines.length && lines[lines.length - 1] === '') lines.pop();
    const header = /^reflex map version (\d+)\s*$/.exec(lines[0] || '');
    if (!header) throw new MapError(1, 'not a Reflex map (expected "reflex map version N")');
    const version = Number(header[1]);
    const map = {version, crlf, groups: []};
    let at = 1;
    const depthOf = line => line.length - line.replace(/^\t+/, '').length;
    const wordsOf = line => line.trim().split(/ +/);

    function readEntity(depth) {
      const entity = {kind: 'entity', type: '', properties: []};
      for (; at < lines.length && depthOf(lines[at]) > depth; at++) {
        const line = lines[at], words = line.trim().split(' ');
        if (words[0] === 'type') { entity.type = words.slice(1).join(' '); continue; }
        const [type, name, ...rest] = words, kind = PROPERTY[type];
        if (!name) throw new MapError(at + 1, `property without a name: "${line.trim()}"`);
        const value = kind ? kind.read(rest) : line.trim().slice(type.length + name.length + 2);
        if (kind && (type === 'Vector3' ? value.length !== 3 || value.some(Number.isNaN) : Number.isNaN(value))) {
          throw new MapError(at + 1, `bad ${type} value for ${name}`);
        }
        entity.properties.push({type, name, value});
      }
      return entity;
    }
    function readBrush(depth) {
      const brush = {kind: 'brush', vertices: [], faces: []};
      while (at < lines.length && depthOf(lines[at]) > depth) {
        const section = lines[at].trim();
        if (section !== 'vertices' && section !== 'faces') throw new MapError(at + 1, `unexpected "${section}" in a brush`);
        for (at++; at < lines.length && depthOf(lines[at]) > depth + 1; at++) {
          const words = wordsOf(lines[at]);
          if (section === 'vertices') {
            const point = words.slice(0, 3).map(Number);
            if (words.length < 3 || point.some(Number.isNaN)) throw new MapError(at + 1, 'bad vertex');
            brush.vertices.push(point);
            continue;
          }
          if (words.length < 8 || !words.slice(0, 5).every(word => NUMBER.test(word))) throw new MapError(at + 1, 'bad face');
          const [u, v, scaleU, scaleV, rotation] = words.slice(0, 5).map(Number), indices = [];
          let next = 5;
          for (; next < words.length && INDEX.test(words[next]); next++) indices.push(Number(words[next]));
          let colour = null;
          if (next < words.length && COLOUR.test(words[next])) colour = words[next++];
          const material = words.slice(next).join(' ');
          if (indices.length < 3) throw new MapError(at + 1, 'face with fewer than three vertices');
          brush.faces.push({u, v, scaleU, scaleV, rotation, indices, colour, material});
        }
      }
      for (const face of brush.faces) {
        if (face.indices.some(index => index >= brush.vertices.length)) throw new MapError(at, 'face names a vertex the brush does not have');
      }
      return brush;
    }
    function readItems(group, depth) {
      while (at < lines.length && depthOf(lines[at]) === depth) {
        const keyword = lines[at].trim();
        at++;
        if (keyword === 'entity') group.items.push(readEntity(depth));
        else if (keyword === 'brush') group.items.push(readBrush(depth));
        else throw new MapError(at, `expected entity or brush, found "${keyword}"`);
      }
    }

    if (version < 8) {
      // Version 6: no groups; the top level is the map.
      const group = {kind: 'global', name: '', items: []};
      readItems(group, 0);
      map.groups.push(group);
    } else {
      while (at < lines.length) {
        const words = lines[at].trim().split(' ');
        if (depthOf(lines[at]) !== 0 || (words[0] !== 'global' && words[0] !== 'prefab')) throw new MapError(at + 1, `expected global or prefab, found "${lines[at].trim()}"`);
        const group = {kind: words[0], name: words.slice(1).join(' '), items: []};
        at++;
        readItems(group, 1);
        map.groups.push(group);
      }
    }
    if (at < lines.length) throw new MapError(at + 1, `unexpected "${lines[at].trim()}"`);
    return map;
  }

  function write(map) {
    const out = [`reflex map version ${map.version}`], legacy = map.version < 8;
    for (const group of map.groups) {
      if (!legacy) out.push(group.kind === 'global' ? 'global' : `prefab ${group.name}`);
      const tab = legacy ? '' : '\t';
      for (const item of group.items) {
        if (item.kind === 'entity') {
          out.push(`${tab}entity`, `${tab}\ttype ${item.type}`);
          for (const {type, name, value} of item.properties) {
            out.push(`${tab}\t${type} ${name} ${PROPERTY[type] ? PROPERTY[type].write(value) : value}`);
          }
        } else {
          out.push(`${tab}brush`, `${tab}\tvertices`);
          for (const vertex of item.vertices) out.push(`${tab}\t\t${vertex.map(fixed).join(' ')}`);
          out.push(`${tab}\tfaces`);
          for (const face of item.faces) {
            const params = [face.u, face.v, face.scaleU, face.scaleV, face.rotation].map(fixed).join(' ');
            const tail = face.colour ? ` ${face.colour} ${face.material || ''}` : face.material ? ` ${face.material}` : '';
            out.push(`${tab}\t\t${params} ${face.indices.join(' ')}${tail}`);
          }
        }
      }
    }
    const newline = map.crlf === false ? '\n' : '\r\n';
    return out.join(newline) + newline;
  }

  // An empty version 8 map with a WorldSpawn, as the editor's new map starts.
  function empty() {
    return {version: 8, crlf: true, groups: [{kind: 'global', name: '', items: [
      {kind: 'entity', type: 'WorldSpawn', properties: [
        {type: 'String32', name: 'targetGameOverCamera', value: 'end'},
        {type: 'UInt8', name: 'playersMin', value: 1},
        {type: 'UInt8', name: 'playersMax', value: 16},
      ]},
    ]}]};
  }

  const global = map => map.groups.find(group => group.kind === 'global');
  // Prefabs are named without regard to case, as the game finds them: SkyTemples defines tower_1 and places Tower_1.
  const prefab = (map, name) => {
    const wanted = String(name).toLowerCase();
    return map.groups.find(group => group.kind === 'prefab' && group.name === name) ||
      map.groups.find(group => group.kind === 'prefab' && group.name.toLowerCase() === wanted);
  };
  const property = (entity, name) => { const found = entity.properties.find(p => p.name === name); return found ? found.value : undefined; };
  // A face colour as [r, g, b, a] from 0 to 1; null when the face has none or its alpha is zero (the material's own).
  function colourOf(face) {
    if (!face.colour) return null;
    const value = parseInt(face.colour.slice(2), 16) >>> 0, alpha = (value >>> 24) / 255;
    return alpha ? [(value >>> 16 & 255) / 255, (value >>> 8 & 255) / 255, (value & 255) / 255, alpha] : null;
  }

  /* Every brush and entity of the map where it stands, prefabs placed (recursively) by the Prefab entities that
     name them. Each comes with the group it is kept in and, inside a prefab, the placement path; the brushes of
     a placed prefab are copies moved into the map's coordinates. Angles are degrees: yaw about y, then pitch about x
     and roll about z, turned as reflex-map's export-prefab turns them once its swap of y and z is undone (yaw,
     then roll, then pitch). Stock maps only turn prefabs by yaw; the order of the other two is unconfirmed. */
  function flatten(map, limit = 16) {
    const brushes = [], entities = [], missing = new Set();
    function place(group, transform, path) {
      for (const item of group.items) {
        if (item.kind === 'brush') {
          brushes.push({brush: transform ? moveBrush(item, transform) : item, source: item, group, path});
          continue;
        }
        const position = property(item, 'position');
        entities.push({entity: item, group, path, position: position && transform ? apply(transform, position) : position});
        if (item.type !== 'Prefab') continue;
        const name = property(item, 'prefabName'), inner = name !== undefined && prefab(map, name);
        if (!inner) { if (name !== undefined) missing.add(name); continue; }
        if (path.length >= limit || path.includes(item)) continue;
        const local = compose(position || [0, 0, 0], property(item, 'angles') || [0, 0, 0]);
        place(inner, transform ? multiply(transform, local) : local, [...path, item]);
      }
    }
    const root = global(map);
    if (root) place(root, null, []);
    return {brushes, entities, missing: [...missing]};
  }

  // 3 × 4 transforms, rows of [rotation | translation].
  function compose(position, angles) {
    const [yaw, pitch, roll] = angles.map(degrees => degrees * Math.PI / 180);
    const rotateY = [[Math.cos(yaw), 0, Math.sin(yaw)], [0, 1, 0], [-Math.sin(yaw), 0, Math.cos(yaw)]];
    const rotateX = [[1, 0, 0], [0, Math.cos(pitch), -Math.sin(pitch)], [0, Math.sin(pitch), Math.cos(pitch)]];
    const rotateZ = [[Math.cos(roll), -Math.sin(roll), 0], [Math.sin(roll), Math.cos(roll), 0], [0, 0, 1]];
    const turn = times(times(rotateX, rotateZ), rotateY);
    return turn.map((row, index) => [...row.map(snap), position[index]]);
  }
  // Right angles give exact zeros and ones, so grid-aligned prefabs stay on the grid.
  const snap = value => Math.abs(value - Math.round(value)) < 1e-12 ? Math.round(value) + 0 : value;
  const times = (a, b) => a.map(row => [0, 1, 2].map(column => row[0] * b[0][column] + row[1] * b[1][column] + row[2] * b[2][column]));
  const multiply = (a, b) => a.map(row => [0, 1, 2, 3].map(column => row[0] * b[0][column] + row[1] * b[1][column] + row[2] * b[2][column] + (column === 3 ? row[3] : 0)));
  const apply = (m, p) => m.map(row => row[0] * p[0] + row[1] * p[1] + row[2] * p[2] + row[3]);
  function moveBrush(brush, transform) {
    return {kind: 'brush', vertices: brush.vertices.map(vertex => apply(transform, vertex)), faces: brush.faces};
  }

  exports.ReflexMap = {parse, write, empty, global, prefab, property, colourOf, flatten, compose, apply, fixed, MapError};
})(typeof module !== 'undefined' ? module.exports : window);

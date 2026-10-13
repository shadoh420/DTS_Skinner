"""Read-only Tribes: Vengeance packages and reference-pose geometry.

Layout references: installed 99..130 packages and UEViewer's UE2/Vengeance
serializers (reference only; no third-party implementation copied). UE1 readers
remain unchanged. Object names include the package and every outer group.
"""
import mmap
from collections import OrderedDict
from pathlib import Path
import struct

import numpy as np

from tools.import_unreal import SCALE, rotation as rotation_matrix


class Package:
    def __init__(self, path):
        self.path = Path(path)
        self.paths = None
        self.at = 0
        self._file = self.path.open('rb')
        try:
            self.data = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            self.limit = len(self.data)
            magic, self.version, self.licensee, self.flags, n, no, e, eo, m, mo = self.unpack('IHHI6I')
            if magic != 0x9e2a83c1 or not 99 <= self.version <= 130:
                raise ValueError('Not a supported UE2/Vengeance package')
            self.names = []
            self.seek(no)
            for _ in range(self.count(n)):
                self.names.append(self.text())
                self.take(4)
            self.imports = []
            self.seek(mo)
            for _ in range(self.count(m)):
                package, cls, outer, name = self.index(), self.index(), self.i32(), self.index()
                self.imports.append(dict(class_name=self.name(cls), outer=outer, name=self.name(name)))
            self.exports = []
            self.seek(eo)
            for _ in range(self.count(e)):
                cls, base, outer, name = self.index(), self.index(), self.i32(), self.index()
                flags, size = self.unpack('I')[0], self.index()
                offset = self.index() if size else 0
                if size < 0 or offset < 0 or offset + size > len(self.data):
                    raise ValueError('Export outside package')
                self.exports.append(dict(cls=cls, base=base, outer=outer, name=self.name(name),
                                         flags=flags, size=size, offset=offset))
        except Exception:
            self.close()
            raise

    def close(self):
        if hasattr(self, 'data'):
            self.data.close()
        self._file.close()

    def take(self, size):
        if size < 0 or self.at + size > self.limit:
            raise ValueError(f'Truncated {self.path.name} at {self.at:#x} ({size} bytes)')
        result = self.data[self.at:self.at + size]
        self.at += size
        return result

    def seek(self, at):
        if not 0 <= at <= self.limit:
            raise ValueError('Offset outside object/package')
        self.at = at

    def unpack(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))

    def i32(self):
        return self.unpack('i')[0]

    def index(self):
        # Same compact-index encoding as UnrealPackage; bounded to five bytes.
        first = self.unpack('B')[0]
        value, shift, more = first & 63, 6, first & 64
        while more:
            if shift > 27:
                raise ValueError('Invalid compact index')
            b = self.unpack('B')[0]
            value |= (b & 127) << shift
            shift += 7
            more = b & 128
        return -value if first & 128 else value

    def count(self, value=None):
        n = self.index() if value is None else value
        if not 0 <= n <= self.limit - self.at:
            raise ValueError(f'Invalid array count {n}')
        return n

    def name(self, n):
        if not 0 <= n < len(self.names):
            raise ValueError(f'Invalid name index {n}')
        return self.names[n]

    def text(self):
        n = self.index()
        raw = self.take(abs(n) * (2 if n < 0 else 1))
        return raw.decode('utf-16le' if n < 0 else 'latin1').rstrip('\0')

    def ref_path(self, ref):
        seen, parts, local = set(), [], ref > 0
        while ref:
            if ref in seen:
                raise ValueError('Circular object outer')
            seen.add(ref)
            entries, i = (self.exports, ref-1) if ref > 0 else (self.imports, -ref-1)
            if not 0 <= i < len(entries):
                raise ValueError('Invalid object reference')
            entry = entries[i]
            parts.append(entry['name'])
            ref = entry['outer']
        if local:
            parts.append(self.path.stem)
        return tuple(reversed(parts))

    def qualified(self, ref):
        return '.'.join(self.ref_path(ref)) if ref else None

    def ref_name(self, ref):
        if not ref:
            return 'None'
        entries, i = (self.exports, ref-1) if ref > 0 else (self.imports, -ref-1)
        if not 0 <= i < len(entries):
            raise ValueError('Invalid object reference')
        return entries[i]['name']

    def header(self, since):
        if self.licensee < since:
            return 0, 0
        check, version, subversion = self.unpack('3i')
        if check != 3 or version < 0 or subversion < 0:
            raise ValueError(f'Invalid Vengeance header at {self.at-12:#x}')
        return version, subversion

    def begin(self, ref):
        e = self.exports[ref-1]
        self.limit = e['offset'] + e['size']
        self.seek(e['offset'])
        self.header(25)
        return e

    def properties(self, ref):
        e = self.begin(ref)
        if e['flags'] & 0x02000000:
            raise ValueError('Vengeance actor stack is deferred to the maps reader')
        return self.tagged()

    def tagged(self, schema=None):
        """UE1 property tags, with explicit bounds and decoded struct/array payloads."""
        props = {}
        while True:
            name = self.name(self.index())
            if name == 'None':
                return props
            info = self.unpack('B')[0]
            kind, sc = info & 15, (info >> 4) & 7
            if not 1 <= kind <= 15:
                raise ValueError('Invalid property type')
            if schema is not None and schema.get(name) != kind:
                raise ValueError('Property not declared by this class')
            struct_name = self.name(self.index()) if kind == 10 else None
            size = (1, 2, 4, 12, 16)[sc] if sc < 5 else self.unpack(('B', 'H', 'I')[sc-5])[0]
            element = 0
            if info & 128 and kind != 3:
                b = self.unpack('B')[0]
                element = b if b < 128 else ((b & 127) << 8 | self.unpack('B')[0]) if b < 192 else ((b & 63) << 24 | int.from_bytes(self.take(3), 'big'))
            start = self.at
            if kind == 3:
                value, size = bool(info & 128), 0
            elif kind in (1, 2, 4):
                value = self.unpack({1: 'B', 2: 'i', 4: 'f'}[kind])[0]
            elif kind in (5, 6):
                value = self.index()
                if kind == 6:
                    value = self.name(value)
                else:
                    self.qualified(value)  # validate the reference without changing the cursor
            elif kind == 13:
                value = self.text()
            elif kind == 10 and struct_name in ('Vector', 'Rotator', 'Color'):
                value = self.unpack({'Vector': '3f', 'Rotator': '3i', 'Color': '4B'}[struct_name])
            else:
                value = self.take(size)
            if self.at > start + size:
                raise ValueError('Property exceeds declared size')
            self.seek(start + size)
            props[name if not element else f'{name}[{element}]'] = value

    def class_defaults(self, ref, schema):
        """Locate the terminal typed defaults using the class's inherited field schema.

        Like the existing UE1 scripted-class reader, this does not interpret bytecode.
        ponytail: native UClass tails differ across the installed licensees; require
        declared property types AND an exact export-end match. A bytecode decoder is
        only needed for executable script state, outside the static-pose import.
        """
        e = self.begin(ref)
        for _ in range(5):
            self.index()  # UField/UStruct references and friendly name
        self.take(12)  # line, text position, bytecode size (not a byte seek)
        first = self.at + 22  # at least the UState fields precede defaults
        for start in range(first, self.limit):
            self.at = start
            try:
                if self.name(self.index()) not in schema:
                    continue
                self.at = start
                props = self.tagged(schema=schema)
                if props and self.at == self.limit:
                    return props
            except (ValueError, UnicodeError):
                continue
        self.at = self.limit - 1
        if self.name(self.index()) == 'None':
            return {}
        raise ValueError('No terminal class defaults')

    def array(self, fmt):
        n = self.count()
        stride = struct.calcsize('<' + fmt)
        return list(struct.iter_unpack('<' + fmt, self.take(n * stride)))

    def skip_array(self, stride, lazy=False):
        if lazy:
            end = self.i32()
        n = self.count()
        self.take(n * stride)
        if lazy and end and end != self.at:
            raise ValueError('Lazy array end mismatch')
        return n

    def lazy_array(self, fmt):
        end = self.i32()
        result = self.array(fmt)
        if end and end != self.at:
            raise ValueError('Lazy array end mismatch')
        return result

    def refs(self, raw=None):
        if raw is None:
            return [self.index() for _ in range(self.count())]
        at, data, limit = self.at, self.data, self.limit
        try:
            self.data, self.at, self.limit = raw, 0, len(raw)
            result = self.refs()
            if self.at != self.limit:
                raise ValueError('Object-reference array has trailing bytes')
            return result
        finally:
            self.at, self.data, self.limit = at, data, limit

    def struct_array(self, raw):
        at, data, limit = self.at, self.data, self.limit
        try:
            self.data, self.at, self.limit = raw, 0, len(raw)
            result = [self.tagged() for _ in range(self.count())]
            if self.at != self.limit:
                raise ValueError('Struct array has trailing bytes')
            return result
        finally:
            self.at, self.data, self.limit = at, data, limit

    def texture(self, ref):
        props = self.properties(ref)
        mips = []
        for _ in range(self.count()):
            _, sub = self.header(26)
            if sub == 1:
                self.skip_array(1, lazy=True)
            end = self.i32()
            pixels = self.take(self.count())
            if end and end != self.at:
                raise ValueError('Mip array end mismatch')
            w, h, ub, vb = self.unpack('iiBB')
            if not 0 < w <= 32768 or not 0 < h <= 32768:
                raise ValueError('Invalid texture dimensions')
            mips.append((w, h, pixels))
        if not mips:
            raise ValueError('Texture has no stored mipmaps')
        return props, mips

    def mesh(self, ref):
        props = self.properties(ref)
        self.take(41)  # UPrimitive box + sphere
        cls = self.ref_name(self.exports[ref-1]['cls'])
        if cls == 'StaticMesh':
            if self.version < 112:
                raise ValueError('Pre-112 static mesh layout is unsupported')
            self.header(3)
            sections = self.array('i5H')
            self.take(25)  # repeated box
            _, sub = self.header(13)
            rows = self.array('6f')
            self.take(4)
            if sub >= 1:
                self.skip_array(24)  # tangent basis
            for _ in range(2):
                self.skip_array(4)
                self.take(4)
            uvsets = []
            for _ in range(self.count()):
                uvsets.append(self.array('2f'))
                self.take(8)
            indices = [x[0] for x in self.array('H')]
            self.take(4)
            materials = [x.get('Material', 0) for x in self.struct_array(props.get('Materials', b'\0'))]
            groups, triangles = [], []
            for slot, (_, first, lo, hi, unused, count) in enumerate(sections):
                if not count:
                    continue
                part = indices[first:first+count*3]
                if len(part) != count*3:
                    raise ValueError('Static section outside index buffer')
                groups.append(dict(start=len(triangles), count=len(part), materialIndex=slot))
                triangles.extend(part)
            if not rows or not uvsets or len(uvsets[0]) != len(rows):
                raise ValueError('Static mesh has no matching vertex/UV stream')
            return geometry([r[:3] for r in rows], uvsets[0], triangles, groups,
                            materials, normals=[r[3:] for r in rows])
        if cls != 'SkeletalMesh':
            raise ValueError('Unsupported mesh class ' + cls)
        return self.skeletal_mesh()

    def skeletal_mesh(self):
        version, vertex_count = self.unpack('ii')
        if not 2 <= version <= 4:
            raise ValueError(f'Unsupported LodMesh version {version}')
        self.skip_array(4)
        materials = self.refs()
        scale, origin, rotation = self.unpack('3f'), self.unpack('3f'), self.unpack('3i')
        for stride in (2, 8, 2, 10, 8):
            self.skip_array(stride)
        self.take(24)  # LOD settings
        if version >= 3:
            self.take(4)
            self.index()
            self.take(52)  # impostor location/rotation/scale/color/modes
        if version >= 4:
            self.take(4)
        self.header(4)
        self.skip_array(12)  # old reference points
        bones = []
        for _ in range(self.count()):
            name = self.name(self.index())
            flags = self.unpack('I')[0]
            quat, pos = self.unpack('4f'), self.unpack('3f')
            length, sx, sy, sz, children, parent = self.unpack('4fii')
            bones.append(dict(name=name, parent=parent))
        animation = self.index()
        self.take(4)  # skeletal depth
        for _ in range(self.count()):
            self.skip_array(2)
            self.take(4)
        self.skip_array(4)
        self.refs()  # attachment names use the same compact encoding
        self.refs()
        self.skip_array(48)
        first_lod = None
        for lod in range(self.count()):
            _, sub = self.header(9)
            self.skip_array(4)
            self.skip_array(16)
            self.take(4)
            self.skip_array(18)
            self.skip_array(18)
            for _ in range(2):
                self.skip_array(2)
                self.take(4)
            _, stream_sub = self.header(17)
            self.take(12)
            self.skip_array(32)
            if stream_sub >= 1:
                self.take(4)
                self.skip_array(24)
            self.skip_array(8, lazy=True)  # influences; reference pose needs no animation weights
            if lod == 0:
                lod_wedges = self.lazy_array('H2f')
                lod_faces = self.lazy_array('4H')
                lod_points = self.lazy_array('3f')
                if lod_points and lod_wedges and lod_faces:
                    first_lod = lod_points, lod_wedges, lod_faces
            else:
                for stride in (10, 8, 12):
                    self.skip_array(stride, lazy=True)
            self.take(24)
            if sub >= 1:
                self.skip_array(24, lazy=True)
                self.skip_array(24)
        self.index()  # optional object following LODs
        points = self.lazy_array('3f')
        wedges = self.lazy_array('H2f')
        faces = self.lazy_array('3HBBI')
        # Cooked packages can omit the LOD's raw mesh. The full-detail reference
        # arrays then provide the static pose; record that fallback explicitly.
        if first_lod:
            points, wedges, faces = first_lod
        indices, groups = [], []
        for slot in sorted({f[3] for f in faces}):
            part = [v for f in faces if f[3] == slot for v in f[:3]]
            groups.append(dict(start=len(indices), count=len(part), materialIndex=slot))
            indices.extend(part)
        if any(w[0] >= len(points) for w in wedges):
            raise ValueError('Skeletal wedge outside point array')
        result = geometry([points[w[0]] for w in wedges], [w[1:] for w in wedges], indices,
                          groups, materials, scale=scale, origin=origin, rotation=rotation)
        result['metadata'].update(bones=bones, animation=self.qualified(animation),
                                  pose='reference', mesh_version=version,
                                  geometry_source='LOD0 raw mesh' if first_lod else 'full-detail reference arrays (LOD0 raw arrays absent)')
        return result


def geometry(points, uv, indices, groups, materials, normals=None,
             scale=(1, 1, 1), origin=(0, 0, 0), rotation=(0, 0, 0)):
    """Native local coordinates retained through one documented basis/unit conversion."""
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    t = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
    if not len(p) or not len(t) or np.any(t < 0) or np.any(t >= len(p)):
        raise ValueError('Empty mesh or index outside vertex array')
    if not groups or max(g['materialIndex'] for g in groups) >= len(materials):
        raise ValueError('Mesh material slot outside material array')
    # Unreal mesh origin/rotation are part of the asset, not actor placement.
    matrix = np.asarray(rotation_matrix(*rotation))
    p = ((p - np.asarray(origin)) * np.asarray(scale)) @ matrix.T
    # UE2 render triangles are clockwise relative to their outward normals. The
    # reflected viewer basis makes them counter-clockwise without reversing them.
    p = p[:, [1, 2, 0]] * SCALE
    p[:, 0] *= -1
    if np.prod(scale) < 0:
        t = t[:, [0, 2, 1]]
    if normals is None:
        n = np.zeros_like(p)
        face = np.cross(p[t[:, 1]]-p[t[:, 0]], p[t[:, 2]]-p[t[:, 0]])
        for c in range(3):
            np.add.at(n, t[:, c], face)
    else:
        n = (np.asarray(normals, dtype=np.float64) / np.asarray(scale)) @ matrix.T
        n = n[:, [1, 2, 0]]
        n[:, 0] *= -1
    lengths = np.linalg.norm(n, axis=1)
    n /= np.maximum(lengths[:, None], 1e-20)
    if not np.isfinite(p).all() or not np.isfinite(n).all() or not np.isfinite(uv).all():
        raise ValueError('Non-finite mesh data')
    return dict(winding='ccw', vertices=p.ravel().tolist(), normals=n.ravel().tolist(), uvs=np.asarray(uv).ravel().tolist(),
                indices=t.ravel().tolist(), groups=groups, material_refs=materials,
                metadata=dict(pose='static', lod=0, coordinate_system='Unreal (x,y,z) -> viewer (-y,z,x), units /52.5',
                              mesh_scale=scale, mesh_origin=origin, mesh_rotation=rotation))


PROPERTY_TYPES = {'ByteProperty': 1, 'IntProperty': 2, 'BoolProperty': 3, 'FloatProperty': 4,
                  'ObjectProperty': 5, 'ClassProperty': 5, 'NameProperty': 6, 'StrProperty': 13,
                  'ArrayProperty': 9, 'StructProperty': 10, 'DelegateProperty': 15}


class Library:
    """Package-qualified lookup with a bounded read-only package cache, also used by job 2."""
    def __init__(self, install):
        self.root = Path(install).resolve()
        if not (self.root/'Content').is_dir() or not (self.root/'Program'/'Bin').is_dir():
            raise ValueError('Expected the Tribes Vengeance folder containing Content and Program/Bin')
        self.files, self.objects, self.classes, self.fields = {}, {}, {}, {}
        self.cache, self.defaults_cache, self.schemas = OrderedDict(), {}, {}
        self.inventory, self.failures = [], []
        paths = sorted(p for folder in ('Content', 'Program/Bin') for p in (self.root/folder).rglob('*')
                       if p.suffix.lower() in ('.pkg', '.u', '.tvm'))
        for path in paths:
            key = path.stem.lower()
            if key in self.files:
                raise ValueError(f'Ambiguous package name: {path} and {self.files[key]}')
            self.files[key] = path
            try:
                p = Package(path)
            except (ValueError, OSError) as exc:
                self.failures.append(dict(package=str(path.relative_to(self.root)), reason=str(exc)))
                continue
            try:
                counts = {}
                for ref, e in enumerate(p.exports, 1):
                    cls = p.ref_name(e['cls']) if e['cls'] else 'Class'
                    counts[cls] = counts.get(cls, 0) + 1
                    if cls in ('StaticMesh', 'SkeletalMesh', 'HkMeshProxy'):
                        name = p.qualified(ref)
                        self.objects[cls.lower()+':'+name.lower()] = dict(source=name, package=key, ref=ref, cls=cls,
                                                         file=str(path.relative_to(self.root)))
                    if cls == 'Class':
                        self.classes[p.qualified(ref).lower()] = dict(package=key, ref=ref,
                                                                    base=p.qualified(e['base']))
                    if cls in PROPERTY_TYPES and e['outer']:
                        self.fields.setdefault(p.qualified(e['outer']).lower(), {})[e['name']] = PROPERTY_TYPES[cls]
                self.inventory.append(dict(file=str(path.relative_to(self.root)), version=p.version,
                                           licensee=p.licensee, bytes=path.stat().st_size, objects=counts))
            finally:
                p.close()

    def close(self):
        for p in self.cache.values():
            p.close()
        self.cache.clear()

    def package(self, name):
        key = name.lower()
        if key not in self.files:
            raise ValueError('Missing package ' + name)
        if key not in self.cache:
            self.cache[key] = Package(self.files[key])
            if len(self.cache) > 8:
                self.cache.popitem(last=False)[1].close()
        self.cache.move_to_end(key)
        return self.cache[key]

    def resolve(self, name):
        if not name or '.' not in name:
            raise ValueError('Expected a package-qualified object: ' + str(name))
        p = self.package(name.split('.')[0])
        if p.paths is None:
            p.paths = {}
            for i in range(1, len(p.exports)+1):
                p.paths.setdefault(p.qualified(i).lower(), []).append(i)
        refs = p.paths.get(name.lower(), [])
        # Texture/Palette and SkeletalMesh/MeshAnimation can share a full name.
        return p, refs

    def object(self, name, classes):
        p, refs = self.resolve(name)
        refs = [r for r in refs if (p.ref_name(p.exports[r-1]['cls']) if p.exports[r-1]['cls'] else 'Class') in classes]
        if len(refs) != 1:
            raise ValueError(f'Expected one {classes} object: {name}; found {len(refs)}')
        return p, refs[0]

    def schema(self, name, trail=()):
        key = name.lower()
        if key in trail:
            raise ValueError('Circular class inheritance: ' + name)
        if key not in self.schemas:
            entry = self.classes.get(key)
            parent = self.schema(entry['base'], trail+(key,)) if entry and entry['base'] else {}
            self.schemas[key] = dict(parent, **self.fields.get(key, {}))
        return self.schemas[key]

    def class_info(self, name, trail=()):
        key = name.lower()
        if key in trail:
            raise ValueError('Circular class inheritance: ' + name)
        if key not in self.defaults_cache:
            e = self.classes.get(key)
            if not e:
                raise ValueError('Missing class ' + name)
            schema = self.schema(name)
            p = self.package(e['package'])
            own = p.class_defaults(e['ref'], schema)
            # Retain property owners: integer references/struct bytes belong to their package.
            own = {k: (e['package'], v) for k, v in own.items()}
            parent, ancestry = self.class_info(e['base'], trail+(key,)) if e['base'] else ({}, [])
            self.defaults_cache[key] = dict(parent, **own), [key] + ancestry
        return self.defaults_cache[key]

    def value_ref(self, owned):
        package, value = owned
        return self.package(package).qualified(value) if value else None

    def value_refs(self, owned):
        package, value = owned
        p = self.package(package)
        return [p.qualified(r) for r in p.refs(value)]

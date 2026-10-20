"""Read-only UE 4.22 pak files and cooked, unversioned packages (Spellbreak).

Pak version 8 (FName compression methods, one-byte method index), unencrypted,
uncompressed or zlib entries. Package summaries carry FileVersionUE4 = 0, so
every version gate below is pinned to UE 4.22 (object version 518).
Field order follows the public UE4 headers as documented by UE Viewer (MIT)
and CUE4Parse (Apache-2.0); no code is copied from either.
"""
from collections import OrderedDict, defaultdict
from pathlib import Path
import struct
import zlib

PAK_MAGIC = 0x5A6F12E1
PACKAGE_TAG = 0x9E2A83C1
MAX_DATA = 1 << 31


class Reader:
    def __init__(self, data, names=None, at=0):
        self.data, self.names, self.at = data, names or [], at

    def take(self, n):
        if n < 0 or self.at+n > len(self.data):
            raise ValueError('Read past end of object')
        raw = self.data[self.at:self.at+n]; self.at += n
        return raw

    def unpack(self, fmt):
        size = struct.calcsize('<'+fmt)
        return struct.unpack('<'+fmt, self.take(size))

    def i32(self): return self.unpack('i')[0]
    def u32(self): return self.unpack('I')[0]
    def i64(self): return self.unpack('q')[0]
    def f32(self): return self.unpack('f')[0]
    def u8(self): return self.take(1)[0]
    def bool32(self):
        value = self.i32()
        if value not in (0, 1):
            raise ValueError(f'Invalid bool {value}')
        return bool(value)

    def count(self, item=1, limit=50_000_000):
        n = self.i32()
        if not 0 <= n <= limit or n*item > len(self.data)-self.at:
            raise ValueError(f'Invalid array count {n}')
        return n

    def text(self):
        n = self.i32()
        if n == 0:
            return ''
        if n > 0:
            if n > 65536: raise ValueError('String too long')
            return self.take(n)[:-1].decode('latin1')
        if -n > 65536: raise ValueError('String too long')
        return self.take(-2*n)[:-2].decode('utf-16le')

    def name(self):
        index, number = self.unpack('ii')
        if not 0 <= index < len(self.names):
            raise ValueError(f'Name index {index} out of range')
        name = self.names[index]
        return f'{name}_{number-1}' if number else name

    def bulk_array(self):
        """TArray::BulkSerialize: element size, count, payload."""
        size, n = self.unpack('ii')
        if size <= 0 or n < 0 or size*n > len(self.data)-self.at:
            raise ValueError('Invalid bulk array')
        return size, n, self.take(size*n)


class Pak:
    def __init__(self, path):
        self.path = Path(path); self.file = self.path.open('rb')
        size = self.path.stat().st_size
        for footer in (221, 189, 61):  # v8B/v9 (5 methods), v8A (4 methods), pre-v8
            if size < footer: continue
            self.file.seek(size-footer); raw = self.file.read(footer)
            if struct.unpack_from('<I', raw, 17)[0] == PAK_MAGIC:
                break
        else:
            self.close(); raise ValueError('Not a UE4 pak file')
        encrypted = raw[16]
        magic, self.version, offset, index_size = struct.unpack_from('<IIqq', raw, 17)
        if encrypted:
            self.close(); raise ValueError('Encrypted pak index is unsupported')
        if self.version != 8:
            self.close(); raise ValueError(f'Unsupported pak version {self.version}')
        methods = raw[61:]
        self.methods = [methods[i:i+32].rstrip(b'\0').decode('ascii') for i in range(0, len(methods), 32)]
        self.method_bytes = 4 if footer == 221 else 1
        self.file.seek(offset); r = Reader(self.file.read(index_size))
        self.mount = r.text()
        self.entries = {}
        for _ in range(r.count(30)):
            name = r.text()
            self.entries[name.lower()] = (name,)+self.entry(r)

    def entry(self, r):
        offset, packed, size = r.unpack('qqq')
        method = r.u8() if self.method_bytes == 1 else r.u32()
        r.take(20)  # SHA1
        blocks = [r.unpack('qq') for _ in range(r.count(16))] if method else []
        encrypted, block_size = r.u8(), r.u32()
        return offset, packed, size, method, blocks, encrypted

    def read(self, name):
        try:
            _, offset, packed, size, method, blocks, encrypted = self.entries[name.lower()]
        except KeyError:
            raise ValueError('Missing pak file: '+name) from None
        if encrypted:
            raise ValueError('Encrypted pak entry is unsupported: '+name)
        if size > MAX_DATA:
            raise ValueError('Pak entry too large for one read: '+name)
        if not method:
            self.file.seek(offset); r = Reader(self.file.read(50))
            if self.entry(r)[2] != size:
                raise ValueError('Pak entry header disagrees with index: '+name)
            raw = self.file.read(size)
        else:
            if self.methods[method-1].lower() != 'zlib':
                raise ValueError(f'Unsupported pak compression {self.methods[method-1]}')
            parts = []
            for start, end in blocks:
                self.file.seek(offset+start); parts.append(zlib.decompress(self.file.read(end-start)))
            raw = b''.join(parts)
        if len(raw) != size:
            raise ValueError('Truncated pak entry: '+name)
        return raw

    def read_head(self, name, size):
        """First bytes of an uncompressed entry (header scans without reading whole files)."""
        _, offset, packed, total, method, blocks, encrypted = self.entries[name.lower()]
        if method or encrypted:
            return self.read(name)[:size]
        self.file.seek(offset+50); return self.file.read(min(size, total))

    def close(self):
        self.file.close()


class Package:
    """One cooked asset: .uasset header plus its .uexp (and .ubulk) payloads."""

    def __init__(self, pak, path, header=None):
        self.pak, self.path = pak, path  # path like g3/Content/Maps/X.umap
        self.object_path = mounted(path) or path
        self.stem = path.rsplit('/', 1)[-1].rsplit('.', 1)[0]
        head = header if header is not None else pak.read(path)
        r = Reader(head)
        tag, legacy = r.unpack('Ii')
        if tag != PACKAGE_TAG or legacy != -7:
            raise ValueError('Not a UE4 (legacy -7) package')
        r.take(4)  # LegacyUE3Version
        self.ue4_version, self.licensee = r.unpack('ii')
        if self.ue4_version:
            raise ValueError('Versioned package: this reader is pinned to unversioned UE 4.22 cooks')
        r.take(r.count(20)*20)  # custom versions
        self.header_size = r.i32(); r.text()  # FolderName
        self.flags = r.u32()
        name_count, name_offset = r.unpack('ii')
        r.take(8)  # gatherable text
        export_count, export_offset, import_count, import_offset, depends_offset = r.unpack('5i')
        r.take(8+4+4+16)  # soft package refs, searchable names, thumbnails, guid
        r.take(r.count(8)*8)  # generations
        for _ in range(2):  # saved-by and compatible engine versions
            r.take(10); r.text()
        if r.u32() or r.count(16):
            raise ValueError('Compressed package chunks are unsupported')
        r.take(4)  # package source
        for _ in range(r.count(4)):
            r.text()
        r.take(4)  # asset registry offset
        self.bulk_start = r.i64(); self.tile_info_offset = r.i32()
        r.take(r.count(4)*4)  # chunk ids
        r.take(8)  # preload dependencies
        if header is not None and len(head) < self.header_size:
            head = pak.read_head(path, self.header_size)
        self.header = head
        r = Reader(head, at=name_offset); self.names = []
        for _ in range(name_count):
            self.names.append(r.text()); r.take(4)
        r = Reader(head, self.names, import_offset); self.imports = []
        for _ in range(import_count):
            class_package, class_name, outer, name = r.name(), r.name(), r.i32(), r.name()
            self.imports.append(dict(class_package=class_package, class_name=class_name, outer=outer, name=name))
        r = Reader(head, self.names, export_offset); self.exports = []
        for _ in range(export_count):
            cls, sup, template, outer = r.unpack('4i'); name = r.name(); flags = r.u32()
            size, offset = r.unpack('qq'); r.take(12+16+4+8+20)
            self.exports.append(dict(cls=cls, super=sup, template=template, outer=outer, name=name,
                                     flags=flags, size=size, offset=offset))
        self.paths = {}
        for i, e in enumerate(self.exports, 1):
            e['class'] = self.ref_name(e['cls']) if e['cls'] else 'Class'
        self._uexp = self._ubulk = self.by_path = None

    @property
    def uexp(self):
        if self._uexp is None:
            self._uexp = self.pak.read(self.path.rsplit('.', 1)[0]+'.uexp')
        return self._uexp

    def ubulk(self):
        if self._ubulk is None:
            self._ubulk = self.pak.read(self.path.rsplit('.', 1)[0]+'.ubulk')
        return self._ubulk

    def entry(self, ref):
        entries, index = (self.exports, ref-1) if ref > 0 else (self.imports, -ref-1)
        if not ref or not 0 <= index < len(entries):
            raise ValueError(f'Invalid object reference {ref}')
        return entries[index]

    def ref_name(self, ref):
        return self.entry(ref)['name'] if ref else None

    def ref_class(self, ref):
        e = self.entry(ref)
        return e['class_name'] if ref < 0 else e['class']

    def qualified(self, ref):
        """'/Game/Path/Package.Object[:Sub]' for exports and imports alike."""
        if not ref:
            return None
        if ref in self.paths:
            return self.paths[ref]
        parts, n, seen = [], ref, 0
        while n:
            e = self.entry(n); parts.append(e['name']); top = n; n = e['outer']; seen += 1
            if seen > 64: raise ValueError('Circular object outer')
        parts.reverse()
        if top > 0:
            parts.insert(0, self.object_path)
        value = parts[0]+('.'+parts[1] if len(parts) > 1 else '')+''.join(':'+p for p in parts[2:])
        self.paths[ref] = value
        return value

    def export_data(self, ref):
        e = self.entry(ref)
        start = e['offset']-self.header_size
        if start < 0 or start+e['size'] > len(self.uexp):
            raise ValueError('Export outside .uexp')
        return self.uexp[start:start+e['size']]

    def reader(self, ref):
        return Reader(self.export_data(ref), self.names)

    def properties(self, ref):
        """Tagged properties, then the object GUID flag; returns (props, reader at native data)."""
        r = self.reader(ref)
        props = read_properties(r, self)
        if not self.entry(ref)['flags'] & 0x10 and r.at+4 <= len(r.data):  # not a class default object
            if r.bool32():
                r.take(16)
        return props, r

    def bulk(self, r):
        """FByteBulkData (UE 4.22 cooked): inline, end of .uexp, or .ubulk."""
        flags, count, size, offset = r.unpack('Iiiq')
        if flags & 0x20 or count == 0:  # unused
            return None
        if flags & 0x02:
            raise ValueError(f'Compressed bulk data is unsupported ({flags:#x})')
        if flags & 0x800:
            raise ValueError('Optional (.uptnl) bulk payload is unsupported')
        if not flags & 0x10000:  # offsets count from BulkDataStartOffset unless NoOffsetFixUp
            offset += self.bulk_start
        if flags & 0x100:  # separate file (.ubulk)
            raw = self.ubulk()[offset:offset+size]
        elif flags & 0x1:  # end of the .uexp
            start = offset-self.header_size
            raw = self.uexp[start:start+size]
        else:  # inline
            return r.take(size)
        if len(raw) != size:
            raise ValueError('Truncated bulk data')
        return raw


def mounted(path):
    """Pak file path -> object package path: g3/Content -> /Game, Engine/Content -> /Engine, plugins by name."""
    if '/Content/' not in path:
        return None
    root, rest = path.split('/Content/', 1)
    mount = 'Game' if root == 'g3' else 'Engine' if root == 'Engine' else root.rsplit('/', 1)[-1]
    return '/'+mount+'/'+rest.rsplit('.', 1)[0]


# ---- tagged properties ------------------------------------------------------------------

def _vector(r): return list(r.unpack('3f'))


NATIVE_STRUCTS = {
    'Vector': _vector, 'Rotator': _vector,  # Rotator is pitch, yaw, roll in degrees
    'Vector2D': lambda r: list(r.unpack('2f')), 'Vector4': lambda r: list(r.unpack('4f')),
    'Quat': lambda r: list(r.unpack('4f')), 'Plane': lambda r: list(r.unpack('4f')),
    'LinearColor': lambda r: list(r.unpack('4f')),
    'Color': lambda r: list(r.unpack('4B')),  # B, G, R, A
    'IntPoint': lambda r: list(r.unpack('2i')), 'IntVector': lambda r: list(r.unpack('3i')),
    'Guid': lambda r: r.take(16).hex(), 'Box': lambda r: [list(r.unpack('3f')), list(r.unpack('3f')), r.u8()],
    'Box2D': lambda r: [list(r.unpack('2f')), list(r.unpack('2f')), r.u8()],
    'Matrix': lambda r: list(r.unpack('16f')),
    'SoftObjectPath': lambda r: _soft(r), 'SoftClassPath': lambda r: _soft(r),
    'PerPlatformFloat': lambda r: (r.u8(), r.f32())[1], 'PerPlatformInt': lambda r: (r.u8(), r.i32())[1],
    'FrameNumber': lambda r: r.i32(), 'DateTime': lambda r: r.i64(), 'Timespan': lambda r: r.i64(),
}


def _soft(r):
    path = r.name(); sub = r.text()
    return path+(':'+sub if sub else '') if path != 'None' else None


def _object(r, package):
    ref = r.i32()
    return package.qualified(ref) if ref else None


def read_value(r, kind, tag, package, size):
    if kind == 'BoolProperty':
        return tag['bool']
    if kind == 'IntProperty': return r.i32()
    if kind == 'FloatProperty': return r.f32()
    if kind == 'DoubleProperty': return r.unpack('d')[0]
    if kind in ('UInt32Property',): return r.u32()
    if kind in ('Int64Property',): return r.i64()
    if kind in ('UInt64Property',): return r.unpack('Q')[0]
    if kind in ('Int16Property',): return r.unpack('h')[0]
    if kind in ('UInt16Property',): return r.unpack('H')[0]
    if kind == 'Int8Property': return r.unpack('b')[0]
    if kind in ('ObjectProperty', 'ClassProperty', 'WeakObjectProperty', 'LazyObjectProperty', 'InterfaceProperty'):
        return _object(r, package)
    if kind in ('SoftObjectProperty', 'SoftClassProperty'): return _soft(r)
    if kind == 'NameProperty': return r.name()
    if kind == 'StrProperty': return r.text()
    if kind == 'EnumProperty': return r.name()
    if kind == 'ByteProperty':
        return r.name() if size == 8 or (size is None and tag.get('enum') not in (None, 'None')) else r.u8()
    if kind == 'StructProperty':
        struct_name = tag['struct']
        if struct_name in NATIVE_STRUCTS:
            return NATIVE_STRUCTS[struct_name](r)
        return read_properties(r, package)
    raise ValueError('Unsupported inline property '+kind)


def read_properties(r, package):
    props = {}
    while True:
        name = r.name()
        if name == 'None':
            return props
        kind = r.name(); size, index = r.unpack('ii')
        tag = {}
        if kind == 'StructProperty':
            tag['struct'] = r.name(); r.take(16)
        elif kind == 'BoolProperty':
            tag['bool'] = bool(r.u8())
        elif kind in ('ByteProperty', 'EnumProperty'):
            tag['enum'] = r.name()
        elif kind in ('ArrayProperty', 'SetProperty'):
            tag['inner'] = r.name()
        elif kind == 'MapProperty':
            tag['inner'] = r.name(); tag['value'] = r.name()
        if r.u8():
            r.take(16)
        start = r.at
        try:
            value = read_tagged(r, kind, tag, package, size)
        except (ValueError, struct.error, UnicodeDecodeError):
            value = None  # unknown layout: skipped by the tag size
        r.at = start+size
        if index:
            existing = props.get(name)
            if not isinstance(existing, dict) or '__indexed__' not in existing:
                existing = {'__indexed__': True, 0: existing}
                props[name] = existing
            existing[index] = value
        else:
            props[name] = value


def read_tagged(r, kind, tag, package, size):
    if kind == 'ArrayProperty':
        inner = tag['inner']; n = r.count()
        if inner == 'StructProperty':
            r.name(); r.name(); r.unpack('ii'); inner_tag = dict(struct=r.name()); r.take(16)
            if r.u8(): r.take(16)
            return [read_value(r, inner, inner_tag, package, None) for _ in range(n)]
        if inner == 'BoolProperty':
            return [bool(r.u8()) for _ in range(n)]
        if inner == 'ByteProperty':
            per = (size-4)//n if n else 1
            return [r.name() if per == 8 else r.u8() for _ in range(n)]
        return [read_value(r, inner, {}, package, None) for _ in range(n)]
    if kind == 'TextProperty':
        return None
    if kind in ('MapProperty', 'SetProperty'):
        return None
    return read_value(r, kind, tag, package, size)


# ---- library -----------------------------------------------------------------------------

class Library:
    """Spellbreak install: one pak, object lookup by '/Game/...Package.Object' path."""
    game = 'sb'
    pak_path = 'g3/Content/Paks/g3-WindowsNoEditor.pak'

    def __init__(self, install, classes=('StaticMesh', 'SkeletalMesh')):
        self.install = Path(install).resolve()
        path = self.install/self.pak_path
        if not path.is_file():
            raise ValueError('Expected the Spellbreak client folder containing '+self.pak_path)
        self.pak = Pak(path)
        self.packages = OrderedDict(); self.failures = []; self.inventory = []
        self.rows = defaultdict(list)
        self.files = {}
        for key, (name, *_rest) in self.pak.entries.items():
            if key.endswith(('.uasset', '.umap')) and mounted(name):
                self.files[mounted(name).lower()] = name
        self.inventory.append(dict(file=self.pak_path, version=self.pak.version, packages=len(self.files)))
        if classes:
            self.scan(classes)

    def scan(self, classes):
        """Header-only pass: package tables, no export payloads."""
        for object_path, name in sorted(self.files.items()):
            if name.lower().endswith('.umap'):
                continue
            try:
                p = Package(self.pak, name, header=self.pak.read_head(name, 65536))
            except (ValueError, OSError, struct.error) as exc:
                self.failures.append(dict(file=name, reason=str(exc))); continue
            for ref, e in enumerate(p.exports, 1):
                if e['class'] in classes and e['outer'] == 0:
                    source = p.qualified(ref)
                    self.rows[(source.lower(), e['class'])].append(dict(source=source, cls=e['class'], file=name, ref=ref,
                                                                        size=e['size'], section=section(name)))

    def package(self, path):
        if path not in self.packages:
            self.packages[path] = Package(self.pak, path)
            if len(self.packages) > 48:
                self.packages.popitem(last=False)
        self.packages.move_to_end(path)
        return self.packages[path]

    def package_for(self, object_path):
        """Package from an object path ('/Game/A/B.B' or '/Game/A/B')."""
        name = self.files.get(object_path.split('.', 1)[0].split(':', 1)[0].lower())
        if not name:
            raise ValueError('Missing package for '+object_path)
        return self.package(name)

    def object(self, source, classes=None):
        p = self.package_for(source)
        if '.' not in source:
            source = source+'.'+p.stem
        if p.by_path is None:
            p.by_path = {p.qualified(ref).lower(): ref for ref in range(1, len(p.exports)+1)}
        ref = p.by_path.get(source.lower())
        if not ref or (classes and p.exports[ref-1]['class'] not in classes):
            raise ValueError('Missing object: '+source)
        return p, ref

    def resolve(self, package, ref):
        """Follow an import to its export in the owning package."""
        if ref > 0:
            return package, ref
        return self.object(package.qualified(ref))

    def state(self, source):
        p, ref = self.object(source)
        props, _ = p.properties(ref)
        return props, p.entry(ref)['class']

    def stats(self):
        return {}

    def close(self):
        self.packages.clear(); self.pak.close()


def section(path):
    """Models list grouping from the content folder."""
    parts = path.split('/Content/', 1)[-1].split('/')
    return ' / '.join(p.replace('_', ' ') for p in parts[:2]) if len(parts) > 2 else parts[0]

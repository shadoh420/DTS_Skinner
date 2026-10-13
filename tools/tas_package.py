"""Bounded UE3 PC reader for Ascend 805/2 and community UDK 805/0.

Layouts checked against UEViewer's UE3 serializers (reference only). No third
party implementation is included. Package/TFC files are always opened read-only.
"""
from bisect import bisect_right
from collections import OrderedDict, defaultdict
from pathlib import Path
import struct
import time


MAGIC = 0x9E2A83C1
MAX_DATA = 512 * 1024 * 1024


def lzo1x(data, expected):
    """Decode an LZO1X block, including overlapping matches; no native dependency."""
    if not 0 <= expected <= MAX_DATA or not data:
        raise ValueError('Invalid LZO block size')
    out = bytearray()
    at, state = 0, 0

    def byte():
        nonlocal at
        if at >= len(data):
            raise ValueError('Truncated LZO instruction')
        value = data[at]; at += 1
        return value

    def extended(base):
        value = byte()
        while value == 0:
            base += 255
            if base > expected:
                raise ValueError('LZO length exceeds output')
            value = byte()
        return base + value

    def literals(n):
        nonlocal at
        if n < 0 or at+n > len(data) or len(out)+n > expected:
            raise ValueError('LZO literal outside block')
        out.extend(data[at:at+n]); at += n

    if data[0] > 17:
        n = byte()-17
        literals(n); state = min(n, 4)
    while True:
        control = byte()
        if control < 16 and state == 0:
            literals(control+3 if control else extended(15)+3)
            state = 4
            continue
        if control < 16:
            distance = 1 + (control >> 2) + (byte() << 2)
            length = 2
            if state == 4:
                distance += 2048; length = 3
            tail = control & 3
        elif control >= 64:
            length = (control >> 5)+1
            distance = 1 + ((control >> 2) & 7) + (byte() << 3)
            tail = control & 3
        else:
            mask = 31 if control >= 32 else 7
            length = control & mask
            if not length:
                length = extended(mask)
            length += 2
            word = byte() | (byte() << 8)
            tail = word & 3
            distance = word >> 2
            if control >= 32:
                distance += 1
            else:
                distance += (control & 8) << 11
                if distance == 0:
                    if control != 17 or word != 0 or at != len(data) or len(out) != expected:
                        raise ValueError('Invalid LZO end marker/length')
                    return bytes(out)
                distance += 16384
        if distance > len(out) or len(out)+length > expected:
            raise ValueError('LZO match outside output')
        start = len(out)-distance
        if length <= distance:
            out.extend(out[start:start+length])
        else:
            # A match repeats its already-produced period; no byte-at-a-time loop.
            period = out[start:]
            out.extend(period * (length // distance) + period[:length % distance])
        literals(tail)
        state = tail


class Reader:
    def __init__(self, data, names=(), at=0):
        self.data, self.names, self.at = data, names, at

    def take(self, size):
        if size < 0 or self.at+size > len(self.data):
            raise ValueError(f'Truncated data at {self.at:#x}, need {size}')
        raw = self.data[self.at:self.at+size]; self.at += size
        return raw

    def unpack(self, fmt):
        return struct.unpack('<'+fmt, self.take(struct.calcsize('<'+fmt)))

    def i32(self):
        return self.unpack('i')[0]

    def count(self, stride=1, maximum=10_000_000):
        n = self.i32()
        if not 0 <= n <= maximum or n*stride > len(self.data)-self.at:
            raise ValueError(f'Invalid array count {n} at {self.at-4:#x}')
        return n

    def text(self):
        n = self.i32()
        if abs(n) > 1_000_000:
            raise ValueError('Invalid FString length')
        raw = self.take(abs(n)*(2 if n < 0 else 1))
        if n and not raw.endswith(b'\0\0' if n < 0 else b'\0'):
            raise ValueError('Unterminated FString')
        return raw.decode('utf-16le' if n < 0 else 'latin1').rstrip('\0')

    def name(self):
        index, number = self.unpack('2i')
        if not 0 <= index < len(self.names) or number < 0:
            raise ValueError('Invalid FName')
        return self.names[index] + (f'_{number-1}' if number else '')

    def array(self, fmt):
        size = struct.calcsize('<'+fmt)
        return list(struct.iter_unpack('<'+fmt, self.take(self.count(size)*size)))

    def bulk_array(self, expected_stride=None):
        stride = self.i32()
        if not 0 < stride <= 4096 or expected_stride is not None and stride != expected_stride:
            raise ValueError(f'Unsupported bulk array stride {stride}, expected {expected_stride}')
        n = self.count(stride)
        return n, self.take(n*stride)

    def properties(self, depth=0):
        if depth > 32:
            raise ValueError('Property nesting too deep')
        result = {}
        while True:
            name = self.name()
            if name == 'None':
                return result
            kind = self.name()
            size, index = self.unpack('2i')
            if size < 0 or index < 0:
                raise ValueError('Invalid property tag')
            extra = self.name() if kind in ('StructProperty', 'ByteProperty') else None
            boolean = self.unpack('B')[0] if kind == 'BoolProperty' else None
            raw = self.take(size); r = Reader(raw, self.names)
            if kind in ('ObjectProperty', 'ComponentProperty', 'IntProperty'):
                value = r.i32()
            elif kind == 'FloatProperty':
                value = r.unpack('f')[0]
            elif kind == 'BoolProperty':
                value = bool(boolean)
            elif kind == 'NameProperty' or kind == 'ByteProperty' and size == 8:
                value = r.name()
            elif kind == 'ByteProperty' and size == 1:
                value = r.unpack('B')[0]
            elif kind == 'StrProperty':
                value = r.text()
            elif kind == 'StructProperty':
                fmt = {'Vector':'3f', 'Vector2D':'2f', 'Rotator':'3i', 'LinearColor':'4f',
                       'Color':'4B', 'Guid':'4I', 'Quat':'4f'}.get(extra)
                value = r.unpack(fmt) if fmt else r.properties(depth+1)
            else:
                value = raw; r.at = len(raw)
            if r.at != len(raw):
                raise ValueError(f'Property size mismatch: {name}/{kind}')
            result[name if not index else f'{name}[{index}]'] = value

    def struct_array(self):
        return [self.properties() for _ in range(self.count(8))]


def compressed_blocks(read, offset, size, expected=None):
    """Describe UE3 compressed block framing without reading payloads."""
    if size < 16:
        raise ValueError('Short compressed chunk')
    magic, block_size, packed, unpacked = struct.unpack('<4I', read(offset, 16))
    if magic != MAGIC or not 0 < block_size <= MAX_DATA or not 0 <= unpacked <= MAX_DATA:
        raise ValueError('Invalid UE3 chunk header')
    if expected is not None and unpacked != expected:
        raise ValueError('Chunk summary length mismatch')
    n = (unpacked+block_size-1)//block_size
    if n > 1_000_000 or 16+n*8 > size:
        raise ValueError('Invalid chunk block count')
    desc = struct.iter_unpack('<2I', read(offset+16, n*8))
    physical, logical, result = offset+16+n*8, 0, []
    for cs, us in desc:
        if not cs or us != min(block_size, unpacked-logical) or physical+cs > offset+size:
            raise ValueError('Invalid compressed block extent')
        result.append((logical, us, physical, cs)); logical += us; physical += cs
    if physical != offset+size or packed != size-16-n*8 or logical != unpacked:
        raise ValueError('Compressed chunk totals disagree')
    return result


class Package:
    def __init__(self, path):
        self.path = Path(path)
        self.file = self.path.open('rb')
        self.size = self.path.stat().st_size
        self.cache, self.blocks = OrderedDict(), {}
        self.lzo_bytes = 0; self.lzo_seconds = 0
        try:
            r = Reader(self.physical(0, min(self.size, 1_048_576)))
            magic, self.version, self.licensee, self.header_size = r.unpack('IHHI')
            if magic != MAGIC or (self.version, self.licensee) not in ((805, 2), (805, 0), (770, 0)):
                raise ValueError('Not a supported Ascend/UDK package')
            self.folder = r.text(); self.flags = r.unpack('I')[0]
            nc, no, ec, eo, ic, io, self.depends = r.unpack('7i')
            r.take(32)  # import/export GUID offsets, thumbnail offset, package GUID
            r.take(r.count(12, 1024)*12)
            self.engine, self.cooker, self.compression = r.unpack('3i')
            self.chunks = [r.unpack('4i') for _ in range(r.count(16, 100_000))]
            if self.compression not in (0, 2) or bool(self.chunks) != bool(self.compression):
                raise ValueError(f'Unsupported package compression {self.compression}')
            last = 0
            for uo, us, co, cs in self.chunks:
                if uo < last or min(us, cs) <= 0 or co < 0 or co+cs > self.size:
                    raise ValueError('Invalid package chunk extent')
                last = uo+us
            self.virtual_size = last if self.chunks else self.size
            if not 0 < self.header_size <= min(self.virtual_size, MAX_DATA):
                raise ValueError('Invalid package header size')
            for count, offset, stride in ((nc,no,13), (ic,io,28), (ec,eo,68)):
                if not 0 <= count <= 2_000_000 or not 0 <= offset <= self.header_size or count*stride > self.header_size-offset:
                    raise ValueError('Invalid package table extent')
            self.chunk_starts = [x[0] for x in self.chunks]
            r = Reader(self.read(no, self.header_size-no)); self.names = []
            for _ in range(nc):
                self.names.append(r.text()); r.take(8)
            r.names = self.names
            r.at = io-no; self.imports = []
            if r.at < 0:
                r = Reader(self.read(io, self.header_size-io), self.names)
            for _ in range(ic):
                cp, cl, outer, name = r.name(), r.name(), r.i32(), r.name()
                self.imports.append(dict(name=name, outer=outer, class_name=cl, class_package=cp))
            r = Reader(self.read(eo, self.header_size-eo), self.names); self.exports = []
            for _ in range(ec):
                cls, base, outer = r.unpack('3i'); name = r.name(); archetype = r.i32()
                flags, size, offset, export_flags = r.unpack('Q3i')
                r.take(r.count(4)*4); r.take(20)
                if size < 0 or offset < 0 or offset+size > self.virtual_size:
                    raise ValueError('Export outside virtual package')
                self.exports.append(dict(cls=cls, base=base, outer=outer, name=name, archetype=archetype,
                                         flags=flags, size=size, offset=offset, export_flags=export_flags))
            self.paths = {}
            for i, e in enumerate(self.exports, 1):
                e['class'] = self.ref_name(e['cls']) if e['cls'] else 'Class'
                e['source'] = self.qualified(i)
        except Exception:
            self.close(); raise

    def close(self):
        self.file.close()

    def physical(self, offset, size):
        if offset < 0 or size < 0 or offset+size > self.size:
            raise ValueError('Read outside package')
        self.file.seek(offset); raw = self.file.read(size)
        if len(raw) != size:
            raise ValueError('Truncated package read')
        return raw

    def decode(self, raw, size):
        start = time.perf_counter(); result = lzo1x(raw, size)
        self.lzo_seconds += time.perf_counter()-start; self.lzo_bytes += size
        return result

    def read(self, offset, size):
        if offset < 0 or size < 0 or size > MAX_DATA or offset+size > self.virtual_size:
            raise ValueError('Read outside virtual package')
        if not self.chunks:
            return self.physical(offset, size)
        result = bytearray(); end = offset+size
        while offset < end:
            i = bisect_right(self.chunk_starts, offset)-1
            if i < 0:
                n = min(end, self.chunk_starts[0])-offset
                result.extend(self.physical(offset, n)); offset += n; continue
            uo, us, co, cs = self.chunks[i]
            if offset >= uo+us:
                raise ValueError('Read in unmapped package gap')
            if i not in self.blocks:
                self.blocks[i] = compressed_blocks(self.physical, co, cs, us)
            for rel, count, at, length in self.blocks[i]:
                lo, hi = max(offset, uo+rel), min(end, uo+rel+count)
                if lo >= hi:
                    continue
                if at not in self.cache:
                    self.cache[at] = self.decode(self.physical(at, length), count)
                    # 64 standard 128 KiB blocks per open package; bounded working set.
                    if len(self.cache) > 64:
                        self.cache.popitem(last=False)
                self.cache.move_to_end(at)
                result.extend(self.cache[at][lo-uo-rel:hi-uo-rel]); offset = hi
        return bytes(result)

    def entry(self, ref):
        entries, index = (self.exports, ref-1) if ref > 0 else (self.imports, -ref-1)
        if not ref or not 0 <= index < len(entries):
            raise ValueError(f'Invalid object reference {ref}')
        return entries[index]

    def ref_name(self, ref):
        return self.entry(ref)['name'] if ref else 'None'

    def qualified(self, ref):
        if not ref:
            return None
        if ref in self.paths:
            return self.paths[ref]
        seen, parts, n = set(), [], ref
        while n:
            if n in seen or len(seen) > 128:
                raise ValueError('Circular/deep object outer')
            seen.add(n); e = self.entry(n); parts.append(e['name']); top = n; n = e['outer']
        # Seekfree exports retain their original package as an outer Package.
        if top > 0 and (not self.flags & 8 or self.ref_name(self.entry(top)['cls']) != 'Package'):
            parts.append(self.path.stem)
        value = '.'.join(reversed(parts)); self.paths[ref] = value
        return value

    def object_reader(self, ref):
        if ref <= 0:
            raise ValueError('Object is not a local export')
        e = self.entry(ref)
        return Reader(self.read(e['offset'], e['size']), self.names)

    def properties(self, ref):
        r = self.object_reader(ref); e = self.entry(ref)
        if e['flags'] & 0x0200000000000000:
            # FStateFrame: node/state, probe mask, latent action, state stack.
            node, state = r.unpack('2i'); r.take(10); r.take(r.count(12)*12)
            if node:
                r.take(4)
        r.take(4)  # NetIndex
        props = r.properties()
        return props, r

    def bulk(self, r, cache_path=None, element_size=1, load=True):
        flags, count, size, offset = r.unpack('4i')
        expected = count*element_size
        if min(count, size) < 0 or expected > MAX_DATA or size > MAX_DATA:
            raise ValueError('Invalid BulkData size')
        if flags & 0x20:
            return None
        external = bool(flags & 1)
        separate = bool(flags & 0x40)
        if not external and not separate:
            raw = r.take(size)
        elif not load:
            return None
        elif external:
            if cache_path is None or offset < 0:
                raise ValueError('Missing external BulkData cache')
            with Path(cache_path).open('rb') as f:
                f.seek(0, 2)
                if offset+size > f.tell():
                    raise ValueError('BulkData outside TFC')
                f.seek(offset); raw = f.read(size)
        else:
            raw = self.read(offset, size)
        if not load:
            return None
        if flags & ~(1 | 2 | 8 | 16 | 32 | 64 | 128):
            raise ValueError(f'Unsupported BulkData flags {flags:#x}')
        if flags & (2 | 128):
            raise ValueError(f'Unsupported non-LZO BulkData compression {flags:#x}')
        if flags & 16:
            blocks = compressed_blocks(lambda o,n: raw[o:o+n], 0, len(raw), expected)
            return b''.join(self.decode(raw[o:o+n], us) for _,us,o,n in blocks)
        if len(raw) != expected:
            raise ValueError('BulkData byte count mismatch')
        return raw

    def texture(self, ref, cache_files):
        props, r = self.properties(ref)
        self.bulk(r, load=False)  # editor source art
        cache_name = props.get('TextureFileCacheName', 'None')
        cache = cache_files.get(cache_name.lower()) if cache_name != 'None' else self.path
        mips = []
        for level in range(r.count(24, 32)):
            raw = self.bulk(r, cache)
            width, height = r.unpack('2i')
            if not 0 < width <= 16384 or not 0 < height <= 16384:
                raise ValueError('Invalid texture dimensions')
            if raw is not None:
                # UE3 stores sub-block DXT mip dimensions as 4x4; logical size still halves.
                width = max(1, props.get('SizeX', width) >> level)
                height = max(1, props.get('SizeY', height) >> level)
                mips.append((width, height, raw))
        if not mips:
            raise ValueError('No resident texture mips (RTC-only is unsupported)')
        return props, mips


ASSET_CLASSES = {'StaticMesh', 'SkeletalMesh', 'SpeedTree', 'Texture2D', 'Material',
                 'MaterialInstanceConstant', 'ObjectRedirector'}


class Library:
    """Deterministic object resolution. All candidate locations remain auditable."""
    def __init__(self, install):
        self.install = Path(install).resolve()
        if not (self.install/'TribesGame'/'CookedPC').is_dir():
            raise ValueError('Expected Tribes Ascend install containing TribesGame/CookedPC')
        self.rows = defaultdict(list); self.inventory = []; self.failures = []
        self.packages = OrderedDict(); self.lzo_bytes = 0; self.lzo_seconds = 0
        files = sorted((f for root in ('Engine', 'TribesGame') for f in (self.install/root).rglob('*')
                        if f.suffix.lower() in ('.upk', '.udk', '.fmap', '.u')), key=lambda p: str(p).lower())
        self.cache_files = {}
        for f in sorted((self.install/'TribesGame').rglob('*.tfc')):
            if f.stem.lower() in self.cache_files:
                raise ValueError('Ambiguous texture cache: '+f.name)
            self.cache_files[f.stem.lower()] = f
        for f in files:
            rel = f.relative_to(self.install).as_posix()
            try:
                p = self.package(f)
                section = 'Community' if p.licensee == 0 and p.version == 805 else 'Retail'
                self.inventory.append(dict(file=rel, version=p.version, licensee=p.licensee,
                                           compression=p.compression, chunks=len(p.chunks), section=section))
                for ref, e in enumerate(p.exports, 1):
                    if e['class'] not in ASSET_CLASSES and not e['class'].startswith('MaterialExpression'):
                        continue
                    row = dict(source=e['source'], cls=e['class'], file=rel, ref=ref, size=e['size'],
                               section=section, flags=e['flags'])
                    self.rows[(e['source'].lower(), e['class'])].append(row)
            except (ValueError, OSError) as exc:
                self.failures.append(dict(file=rel, reason=str(exc)))
        for rows in self.rows.values():
            # Original standalone package first, then cooked source package, then maps.
            rows.sort(key=lambda r: (r['source'].split('.')[0].lower() != Path(r['file']).stem.lower(),
                                     Path(r['file']).suffix.lower() in ('.fmap', '.udk'), r['file'].lower(), r['ref']))
        self.by_name = defaultdict(list)
        for (name, cls), rows in self.rows.items():
            self.by_name[name].append((cls, rows))

    def package(self, path):
        path = Path(path)
        if not path.is_absolute():
            path = self.install/path
        if path not in self.packages:
            self.packages[path] = Package(path)
            if len(self.packages) > 3:
                _, old = self.packages.popitem(last=False)
                self.lzo_bytes += old.lzo_bytes; self.lzo_seconds += old.lzo_seconds; old.close()
        self.packages.move_to_end(path)
        return self.packages[path]

    def object(self, source, classes=None):
        matches = [(cls,rows) for cls,rows in self.by_name.get(source.lower(), ()) if classes is None or cls in classes]
        concrete = [item for item in matches if item[0] != 'ObjectRedirector']
        matches = concrete or matches
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous object: '+source)
        row = matches[0][1][0]
        return self.package(row['file']), row['ref']

    def resolve(self, package, ref):
        if ref > 0:
            return package, ref
        return self.object(package.qualified(ref), {package.entry(ref)['class_name']})

    def stats(self):
        size = self.lzo_bytes + sum(p.lzo_bytes for p in self.packages.values())
        seconds = self.lzo_seconds + sum(p.lzo_seconds for p in self.packages.values())
        return dict(decoded_bytes=size, seconds=seconds, mib_per_second=size/1048576/seconds if seconds else 0)

    def close(self):
        for p in self.packages.values():
            self.lzo_bytes += p.lzo_bytes; self.lzo_seconds += p.lzo_seconds; p.close()
        self.packages.clear()

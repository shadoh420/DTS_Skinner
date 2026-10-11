"""Azrael's Tear static XAF rigs and reskinnable RES textures, as game azrael.

python tools/import_azrael.py --install "C:/Program Files (x86)/GOG Galaxy/Games/Azrael's Tear"

CD entries are read on demand, never extracted. The CD, scene, image and ANI
readers are also usable by map importers. Characters use their ANI resting pose;
other rigs use their earliest key time. Existing PNG edits survive.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import struct

from PIL import Image

try:
    from tools.import_sin import checked_name
    from tools.import_quake2 import safe
    from tools.local_data import LOCAL_DATA
except ImportError:
    from import_sin import checked_name
    from import_quake2 import safe
    from local_data import LOCAL_DATA

MAX_BYTES = 32 * 1024 * 1024
IDENTITY = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]


def read_file(path, max_bytes=MAX_BYTES):
    with Path(path).open('rb') as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('Asset exceeds bounded read limit')
    return data


def child(folder, name):
    """Case insensitive lookup for DOS names, also on case sensitive hosts."""
    folder = Path(folder)
    return next((p for p in folder.iterdir() if p.name.lower() == name.lower()), None) if folder.is_dir() else None


class CDReader:
    """ISO 9660 on one MODE1/2352 BIN track, or a copied CD directory.

    files maps normalized names to (extent, size) or loose paths. Payload reads
    are bounded separately from directory reads. No persistent file handles.
    """
    def __init__(self, source):
        self.source = Path(source)
        self.files = {}
        if self.source.is_dir():
            root = self.source.resolve()
            for path in root.rglob('*'):
                if path.is_file():
                    if not path.resolve().is_relative_to(root):
                        raise ValueError('CD folder link escapes source directory')
                    name = checked_name(path.relative_to(root).as_posix())
                    if name in self.files:
                        raise ValueError('Duplicate CD path: ' + name)
                    self.files[name] = path
                    if len(self.files) > 100000:
                        raise ValueError('CD directory entry limit exceeded')
            return
        if self.source.suffix.lower() == '.cue':
            cue = read_file(self.source, 65536).decode('ascii')
            tracks = re.findall(r'^\s*TRACK\s+\d+\s+(\S+)', cue, re.M | re.I)
            names = re.findall(r'^\s*FILE\s+"([^"]+)"\s+BINARY\s*$', cue, re.M | re.I)
            if [t.upper() for t in tracks] != ['MODE1/2352'] or len(names) != 1 or not re.search(r'INDEX\s+01\s+00:00:00', cue, re.I):
                raise ValueError('Expected a single MODE1/2352 track at INDEX 01 00:00:00')
            name = checked_name(names[0])
            if '/' in name:
                raise ValueError('CUE BIN must be beside CUE')
            self.source = child(self.source.parent, name)
            if self.source is None:
                raise ValueError('CUE BIN file is missing')
        self.size = self.source.stat().st_size
        if self.size % 2352:
            raise ValueError('Truncated MODE1/2352 image')
        pvd = None
        for sector in range(16, 80):
            block = self._read(sector, 2048, 2048)
            if block[1:7] != b'CD001\x01':
                raise ValueError('Invalid ISO 9660 volume descriptor')
            if block[0] == 1:
                pvd = block
                break
            if block[0] == 255:
                break
        if pvd is None or struct.unpack_from('<H', pvd, 128)[0] != 2048:
            raise ValueError('Missing ISO 9660 primary volume or unsupported block size')
        volume = struct.unpack_from('<I', pvd, 80)[0]
        if volume * 2352 > self.size:
            raise ValueError('ISO volume exceeds BIN')
        self.size = volume * 2352
        seen = set()

        def walk(lba, size, prefix='', depth=0):
            if depth > 16 or len(seen) >= 2048 or (lba, size) in seen:
                raise ValueError('Cyclic or excessive ISO directories')
            seen.add((lba, size))
            data = self._read(lba, size, 4 * 1024 * 1024)
            at = 0
            while at < len(data):
                length = data[at]
                if not length:
                    at = (at // 2048 + 1) * 2048
                    continue
                if length < 34 or at + length > len(data) or at % 2048 + length > 2048:
                    raise ValueError('Invalid ISO directory record')
                row = data[at:at + length]
                at += length
                n = row[32]
                if not n or 33 + n > length or row[1] or row[26] or row[27] or row[25] & 128:
                    raise ValueError('Unsupported ISO record/extent')
                extent, size = struct.unpack_from('<I', row, 2)[0], struct.unpack_from('<I', row, 10)[0]
                if extent * 2048 + size > self.size // 2352 * 2048:
                    raise ValueError('ISO extent outside BIN')
                raw = row[33:33 + n]
                if raw in (b'\0', b'\1'):
                    continue
                name = checked_name(prefix + raw.decode('ascii').split(';')[0])
                if row[25] & 2:
                    walk(extent, size, name + '/', depth + 1)
                else:
                    if name in self.files or len(self.files) >= 100000:
                        raise ValueError('Duplicate or excessive ISO entries')
                    self.files[name] = (extent, size)
        walk(struct.unpack_from('<I', pvd, 158)[0], struct.unpack_from('<I', pvd, 166)[0])

    def _read(self, lba, size, limit):
        if size < 0 or size > limit:
            raise ValueError('Asset exceeds bounded read limit')
        if lba < 0 or (lba + (size + 2047) // 2048) * 2352 > self.size:
            raise ValueError('ISO extent outside BIN')
        out = bytearray()
        with self.source.open('rb') as stream:
            for sector in range(lba, lba + (size + 2047) // 2048):
                stream.seek(sector * 2352 + 16)
                block = stream.read(min(2048, size - len(out)))
                if len(block) != min(2048, size - len(out)):
                    raise ValueError('Truncated BIN payload')
                out.extend(block)
        return bytes(out)

    def read(self, name, max_bytes=MAX_BYTES):
        entry = self.files[checked_name(name)]
        return read_file(entry, max_bytes) if isinstance(entry, Path) else self._read(*entry, max_bytes)


def read_install(install):
    root = Path(install).expanduser()
    game = child(root, 'GAME')
    game = child(game, 'AZRAEL') if game else None
    game = game or child(root, 'AZRAEL') or root
    # A copied texture directory takes precedence only when explicitly configured.
    cfg = child(game, 'RESOURCE.CFG')
    if cfg:
        lines = read_file(cfg, 65536).decode('latin1').splitlines()
        line = lines[0].strip().strip('"') if lines else ''
        configured = Path(line.replace('\\', '/'))
        if not configured.is_absolute():
            configured = game / configured
        if line and configured.is_dir():
            cd = CDReader(configured)
            cd.files = {'textures/' + n: p for n, p in cd.files.items()}
            return game, cd
    for folder in dict.fromkeys((root, game, game.parent, game.parent.parent)):
        for name in ('AZT.CUE', 'AZT.BIN'):
            source = child(folder, name)
            if source:
                return game, CDReader(source)
        textures = child(folder, 'TEXTURES')
        if textures:
            cd = CDReader(textures)
            cd.files = {'textures/' + n: p for n, p in cd.files.items()}
            return game, cd
    raise ValueError('No AZT.CUE/AZT.BIN or copied TEXTURES directory found')


# The generic tree retains unknown blocks for future map research. The normalized
# views below validate geometry, but do not discard specular data or animation.
TOKEN = re.compile(r'\s+|[,;]|//[^\n]*|/\*.*?\*/|"[^"\x00]*"|[{}]|[^\s,;{}"]+', re.S)


def parse_xaf(data, *, allow_identical_materials=False):
    """Keep hierarchy and keys; face pairs are vertex/normal, UVs per vertex.

    Confirmed by R.EXE's sequential UV setter loop at 0x298a0..0x298d6 and
    0x478bc, plus Lurka's normal indices exceeding its UV count. Maps may opt
    into repeated identical declarations (ENTRY_D declares 1ROPE twice).
    """
    if len(data) > MAX_BYTES:
        raise ValueError('XAF exceeds bounded read limit')
    text = data.decode('ascii').rstrip('\x1a \r\n\t') if isinstance(data, bytes) else data.rstrip('\x1a \r\n\t')
    tokens, end = [], 0
    for match in TOKEN.finditer(text):
        if match.start() != end:
            raise ValueError('Invalid XAF token')
        end = match.end()
        t = match.group()
        if not t.isspace() and t not in ',;' and not t.startswith(('//', '/*')):
            tokens.append(t)
            if len(tokens) > 2000000:
                raise ValueError('XAF token limit exceeded')
    if end != len(text):
        raise ValueError('Invalid XAF token')
    at = 0

    def block(depth=0):
        nonlocal at
        if depth > 64:
            raise ValueError('XAF nesting limit exceeded')
        out = []
        while at < len(tokens):
            t = tokens[at]
            at += 1
            if t == '}':
                if not depth:
                    raise ValueError('Unexpected XAF closing brace')
                return out
            if t == '{':
                out.append(block(depth + 1))
            else:
                out.append(t)
        if depth:
            raise ValueError('Unclosed XAF block')
        return out
    tree = block()
    if not tree:
        raise ValueError('Empty XAF')

    def fields(items):
        if not isinstance(items, list):
            raise ValueError('Expected XAF block')
        i = 0
        while i < len(items):
            key = items[i]
            if not isinstance(key, str) or key.startswith('"') or i + 1 >= len(items):
                raise ValueError('Invalid XAF field')
            value, name = items[i + 1], None
            i += 2
            if isinstance(value, str) and value.startswith('"') and i < len(items) and isinstance(items[i], list):
                name, value = value[1:-1], items[i]
                i += 1
            yield key.lower(), name, value

    def string(v):
        if not isinstance(v, str) or not v.startswith('"'):
            raise ValueError('Expected XAF string')
        return v[1:-1]

    def numbers(v, width):
        if not isinstance(v, list) or len(v) % width:
            raise ValueError('Invalid XAF numeric block length')
        try:
            values = [float(x) for x in v]
        except (TypeError, ValueError):
            raise ValueError('Invalid XAF number') from None
        if not all(math.isfinite(x) for x in values):
            raise ValueError('Non-finite XAF number')
        return [values[i:i + width] for i in range(0, len(values), width)]

    materials, textures, objects, animations = {}, [], [], []

    def mesh(body):
        f = {k: v for k, _, v in fields(body)}
        vertices = numbers(f.get('vertices', []), 3)
        uvs = numbers(f.get('texture_coordinates', []), 2)
        normals = numbers(f.get('normals', []), 3)
        faces, seq, i = [], f.get('faces', []), 0
        while i < len(seq):
            try:
                n = int(seq[i])
                i += 1
                if n == 0:
                    break
                if n != 3 or i + n * 2 > len(seq):
                    raise ValueError('Expected XAF triangles')
                face = [(int(seq[j]), int(seq[j + 1])) for j in range(i, i + n * 2, 2)]
                if any(v < 0 or v >= len(vertices) or t < 0 or (normals and t >= len(normals)) for v, t in face):
                    raise ValueError('XAF face vertex/normal index outside array')
                faces.append(face)
                i += n * 2
            except (TypeError, ValueError) as exc:
                raise ValueError('Invalid XAF faces: ' + str(exc)) from exc
        if not seq or seq[-1] != '0' or i != len(seq):
            raise ValueError('Missing or misplaced XAF face terminator')
        mats = [string(v) for v in f['face_materials']] if 'face_materials' in f else [string(f['material']) if 'material' in f else None] * len(faces)
        if len(mats) != len(faces):
            raise ValueError('XAF face_materials count mismatch')
        if uvs and len(uvs) != len(vertices):
            raise ValueError('XAF texture_coordinates must be per vertex')
        return dict(vertices=vertices, uvs=uvs, normals=normals, faces=faces, materials=mats)

    def obj(name, body):
        if name is None:
            raise ValueError('Unnamed XAF object')
        result = dict(name=name, matrix=list(IDENTITY), meshes=[], children=[])
        for key, subname, value in fields(body):
            if key == 'matrix':
                rows = numbers(value, 16)
                if len(rows) != 1 or any(abs(rows[0][i] - IDENTITY[i]) > 1e-5 for i in (3, 7, 11, 15)):
                    raise ValueError('Expected affine XAF matrix')
                result['matrix'] = rows[0]
            elif key == 'mesh':
                result['meshes'].append(mesh(value))
            elif key == 'object':
                result['children'].append(obj(subname, value))
        return result

    for key, name, body in fields(tree):
        if key == 'material':
            if name is None:
                raise ValueError('Unnamed XAF material')
            if name in materials and not allow_identical_materials:
                raise ValueError('Duplicate XAF material: ' + str(name))
            mat = dict(colour=[1., 1., 1., 1.], texture=None, specular=[])
            for k, _, v in fields(body):
                if k == 'texture':
                    mat[k] = checked_name(string(v))
                    if mat[k] not in textures:
                        textures.append(mat[k])
                elif k in ('colour', 'specular'):
                    mat[k] = numbers(v, 1)
                    mat[k] = [x[0] for x in mat[k]]
            if len(mat['colour']) != 4 or any(not 0 <= x <= 1 for x in mat['colour']):
                raise ValueError('Invalid XAF colour')
            if name in materials and materials[name] != mat:
                raise ValueError('Conflicting XAF material: ' + str(name))
            materials[name] = mat
        elif key == 'object':
            objects.append(obj(name, body))
        elif key == 'animation_set':
            for k, n, b in fields(body):
                if k != 'animation':
                    continue
                ani = dict(name=n)
                for kind, _, values in fields(b):
                    if kind == 'object_name':
                        ani[kind] = string(values)
                    elif kind in ('rotate_keys', 'position_keys', 'scale_keys'):
                        ani[kind] = numbers(values, 5 if kind == 'rotate_keys' else 4)
                        if any(a[0] > b[0] for a, b in zip(ani[kind], ani[kind][1:])):
                            raise ValueError('Unsorted XAF animation keys')
                animations.append(ani)
    return dict(materials=materials, textures=textures, objects=objects, animations=animations, tree=tree)


def res_records(data):
    """Yield validated metadata and bounded compressed slices, without decoding."""
    if len(data) > MAX_BYTES:
        raise ValueError('RES exceeds bounded read limit')
    at, index = 0, 0
    while at < len(data):
        if at + 64 > len(data):
            raise ValueError('Truncated RES header')
        length = struct.unpack_from('<I', data, at)[0]
        end = at + 4 + length
        if end > len(data) or length < 60:
            raise ValueError('Invalid RES record length')
        h = struct.unpack_from('<15I', data, at + 4)
        width, height, stride, count = h[0], h[1], h[6], h[13]
        if not (0 < width <= 4096 and 0 < height <= 4096 and width <= stride <= 8192 and stride * height <= 16 * 1024 * 1024 and 0 < count <= 256) or h[4:6] != (8, 0):
            raise ValueError('Unsupported RES image header')
        start = at + 64 + count * 4
        if start + 2056 > end:
            raise ValueError('Truncated RES palette/Huffman tree')
        size, root = struct.unpack_from('<II', data, start)
        if size != stride * height or root >= 512:
            raise ValueError('Invalid RES output size/root')
        yield dict(index=index, offset=at, length=length, width=width, height=height, stride=stride,
                   palette=data[at + 64:start], packed=memoryview(data)[start:end])
        at, index = end, index + 1
        if index > 4096:
            raise ValueError('RES record limit exceeded')


def decode_res(record, name=''):
    packed = record['packed']
    size, root = struct.unpack_from('<II', packed)
    tree = struct.unpack_from('<1024H', packed, 8)
    # Check reachable nodes once: no cycles, bad children or exponential walks.
    visited, active = set(), set()

    def check(node):
        if node < 256 or node in visited:
            return
        if node >= 512 or node in active:
            raise ValueError('Invalid or cyclic RES Huffman tree')
        active.add(node)
        check(tree[node * 2])
        check(tree[node * 2 + 1])
        active.remove(node)
        visited.add(node)
    check(root)
    pixels, at, word, bits = bytearray(), 2056, 0, 0
    for _ in range(size):
        node = root
        while node >= 256:
            if not bits:
                if at >= len(packed):
                    raise ValueError('Truncated RES Huffman bits')
                chunk = packed[at:at + 4]
                word, bits = int.from_bytes(chunk, 'little'), len(chunk) * 8
                at += len(chunk)
            node = tree[node * 2 + (word & 1)]
            word >>= 1
            bits -= 1
        pixels.append(node)
    palette = record['palette']
    if pixels and max(pixels) >= len(palette) // 4:
        raise ValueError('RES pixel outside palette')
    image = Image.frombytes('P', (record['stride'], record['height']), bytes(pixels))
    image.putpalette(bytes(c for i, c in enumerate(palette) if i % 4 != 3))
    alpha = bytes(0 if '#' in name and palette[i:i + 3] == b'\xff\0\xff' else 255 for i in range(0, len(palette), 4))
    image.info['transparency'] = alpha
    return image.convert('RGBA').crop((0, 0, record['width'], record['height']))


def parse_ani(data):
    if len(data) > 1024 * 1024:
        raise ValueError('ANI exceeds bounded read limit')
    text = data.decode('ascii').rstrip('\x1a \r\n\t')
    result = dict(files=[], clips={}, triggers=[], properties={})
    current = None
    for line in text.splitlines():
        line = line.split('//', 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        key, value = parts[0].upper(), parts[1].strip() if len(parts) > 1 else ''
        if key == 'FILE':
            current = checked_name(value)
            if not current.endswith('.xaf'):
                raise ValueError('ANI FILE is not XAF')
            result['files'].append(current)
            result['clips'][current] = {}
        elif re.fullmatch(r'\d+\s*-\s*\d+', value):
            if current is None:
                raise ValueError('ANI clip before FILE')
            start, end = map(int, value.split('-'))
            if end < start:
                raise ValueError('Reversed ANI frame range')
            result['clips'][current][key] = [start, end]
        elif key == 'TRIGGER':
            result['triggers'].append(value.split())
        else:
            values = value.replace(',', ' ').split()
            try:
                nums = [float(x) for x in values]
                if not all(math.isfinite(x) for x in nums):
                    raise ValueError('Non-finite ANI number')
                result['properties'][key] = nums[0] if len(nums) == 1 else nums if nums else True
            except ValueError:
                raise ValueError('Invalid ANI property: ' + line) from None
    if not result['files'] or result['properties'].get('NUM_FILES', len(result['files'])) != len(result['files']):
        raise ValueError('ANI file count mismatch')
    return result


def read_rooms(data):
    """81 retail records: name[60], XAF[60], u16 zones + names[60],
    u16 objects + names[60], u16 zero. Bytes after each NUL are heap garbage.
    The last field is always zero in this install; nonzero is unsupported.
    """
    if len(data) < 2 or len(data) > 4 * 1024 * 1024:
        raise ValueError('Invalid ROOMS.DAT length')
    at = 0

    def take(n):
        nonlocal at
        if at + n > len(data):
            raise ValueError('Truncated ROOMS.DAT record')
        value = data[at:at + n]
        at += n
        return value

    def count():
        return struct.unpack('<H', take(2))[0]

    def string():
        value = take(60)
        if b'\0' not in value:
            raise ValueError('Unterminated ROOMS.DAT name')
        return value.split(b'\0')[0].decode('ascii').strip()

    declared, rows = count(), []
    for _ in range(declared):
        start, name, xaf = at, string(), string()
        zones = [string() for _ in range(count())]
        objects = [string() for _ in range(count())]
        if count() != 0:
            raise ValueError('Unsupported nonzero ROOMS.DAT trailing field')
        rows.append(dict(offset=start, name=name, xaf='textures/' + checked_name(xaf) + '.xaf' if xaf else '', zones=zones, objects=objects))
    if at != len(data):
        raise ValueError('Trailing ROOMS.DAT data')
    return dict(declared_count=declared, headers=rows, method='Sequential records; exact EOF')


def multiply(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def iter_objects(scene):
    """Yield (index path, object, parent path) in parent-first order.

    Tuple paths, e.g. (0, 2), identify objects even when names repeat.
    """
    def visit(objects, parent):
        for index, obj in enumerate(objects):
            path = parent + (index,)
            yield path, obj, parent
            yield from visit(obj['children'], path)
    yield from visit(scene['objects'], ())


def _sample_keys(keys, frame, quaternion=False):
    a = b = keys[0][1:]
    fraction = 0.
    if frame >= keys[-1][0]:
        a = b = keys[-1][1:]
    elif frame > keys[0][0]:
        for left, right in zip(keys, keys[1:]):
            if left[0] <= frame <= right[0]:
                a, b = left[1:], right[1:]
                fraction = (frame - left[0]) / (right[0] - left[0]) if right[0] > left[0] else 0.
                break
    if not quaternion:
        return [x + fraction * (y - x) for x, y in zip(a, b)]

    def normalize(q):
        length = math.hypot(*q)
        if not math.isfinite(length) or length < 1e-12:
            raise ValueError('Invalid animation quaternion')
        return [v / length for v in q]

    a, b = normalize(a), normalize(b)
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:
        b, dot = [-v for v in b], -dot
    if dot > .9995:
        return normalize([x + fraction * (y - x) for x, y in zip(a, b)])
    angle = math.acos(max(-1., min(1., dot)))
    return normalize([(math.sin((1 - fraction) * angle) * x + math.sin(fraction * angle) * y) / math.sin(angle)
                      for x, y in zip(a, b)])


def evaluate_world_matrices(scene, frame):
    """Return {object index-path: row-major world matrix} in XAF coordinates.

    frame is a key time (not seconds); None explicitly selects stored matrices.
    No scene data is mutated. Paths are those from iter_objects(), so repeated
    object names cannot overwrite output matrices. As in the verified pose probe,
    the last track for a name applies to all objects with that name (CL rigs
    contain duplicates). This binding policy is not yet verified in R.EXE.

    R.EXE preferred LE addresses: SetTime 0x44d7c -> evaluator 0x45cfc;
    rotation 0x45a38 uses w,x,y,z slerp (0x449f4), quaternion matrix
    (0x44ae8), and replace mode 0 (0x499c0). Scale 0x45c18 combines
    before rotation (mode 1, 0x5e850); position 0x45b30 sets parent-relative
    translation via 0x3b220 -> 0x3ec20. Missing position preserves stored
    translation. Endpoints hold; world = local * parent. Verified against
    Claude's character pose probe; this does not establish an animation clock.
    """
    if frame is not None and not math.isfinite(frame):
        raise ValueError('Non-finite animation frame')
    objects = list(iter_objects(scene))
    tracks = {}
    if frame is not None:
        names = Counter(obj['name'] for _, obj, _ in objects)
        for animation in scene['animations']:
            target = animation.get('object_name') or animation['name']
            if not names[target]:
                raise ValueError('Missing animation target: ' + target)
            tracks[target] = animation
    worlds = {}
    for path, obj, parent in objects:
        local = list(obj['matrix'])
        track = tracks.get(obj['name'], {})
        if track.get('rotate_keys'):
            w, x, y, z = _sample_keys(track['rotate_keys'], frame, quaternion=True)
            local = [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), 0.,
                     2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), 0.,
                     2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), 0.,
                     *local[12:15], 1.]
        if track.get('scale_keys'):
            scale = _sample_keys(track['scale_keys'], frame)
            for row in range(3):
                for col in range(3):
                    local[row * 4 + col] *= scale[row]
        if track.get('position_keys'):
            local[12:15] = _sample_keys(track['position_keys'], frame)
        world = multiply(local, worlds[parent] if parent else IDENTITY)
        if not all(math.isfinite(v) for v in world):
            raise ValueError('Non-finite world matrix: ' + obj['name'])
        worlds[path] = world
    return worlds


def canonical_alias(source, aliases):
    preferred = next((a for a in aliases if a.lower() == Path(source).stem), None)
    preferred = preferred or next((a for a in aliases if a.lower() in ('eugene', 'scorp', 'kurt')), None)
    return preferred or (aliases[0] if aliases else Path(source).stem.upper())


def choose_preview_pose(scene, source, aliases, anis):
    """Choose the same canonical alias as the display title; retain alternatives."""
    title = canonical_alias(source, aliases)
    choices = []
    for ani in anis:
        if ani['status'] != 'ready' or ani['alias'] not in aliases:
            continue
        clips = ani['data']['clips'].get(Path(source).name, {})
        clip = 'REST' if 'REST' in clips else next(iter(clips), None)
        choices.append(dict(alias=ani['alias'], source=ani['source'], clip=clip,
                            frame=clips[clip][0] if clip else None))
    chosen = next((c for c in choices if c['alias'] == title), None)
    if aliases:
        frame = chosen['frame'] if chosen else None
        reason = ('ANI REST start' if chosen['clip'] == 'REST' else 'ANI first clip start') if frame is not None else 'Canonical ANI has no clips'
    else:
        times = [key[0] for a in scene['animations'] for kind in ('rotate_keys', 'position_keys', 'scale_keys') for key in a.get(kind, [])]
        frame = min(times) if times else 0.
        reason = 'Earliest animation key' if times else 'No animation keys; frame 0'
    return dict(frame=frame, reason=reason, canonical_alias=title if aliases else None, ani_choices=choices)


def measure_parts(scene, worlds):
    """Advisory distances in XAF units; never remove parts.

    Body center is the componentwise median of visible mesh vertices (each used
    vertex once per mesh); radius is their median distance from that center.
    Flag object centers farther than 3 radii. Multi-figure scenes can inflate
    the radius, and weapons/effects can legitimately be flagged. Hidden geometry
    is excluded just as in build_model. Bounds expose spread even without flags.
    """
    parts, all_points = [], []
    for path, obj, _ in iter_objects(scene):
        points, world = [], worlds[path]
        for mesh in obj['meshes']:
            used = set()
            for face, name in zip(mesh['faces'], mesh['materials']):
                mat = scene['materials'].get(name)
                if mat is not None and mat['colour'][3] != 0:
                    used.update(v for v, _ in face)
            for index in sorted(used):
                vertex = mesh['vertices'][index] + [1.]
                points.append([sum(vertex[k] * world[k * 4 + c] for k in range(4)) for c in range(3)])
        if points:
            parts.append(dict(path=list(path), name=obj['name'], vertices=len(points),
                              center=[sum(p[c] for p in points) / len(points) for c in range(3)]))
            all_points.extend(points)
    if not all_points:
        return dict(center=None, median_radius=0., bounds=None, parts=[], far_parts=[])
    center = [median(p[c] for p in all_points) for c in range(3)]
    radius = median(math.dist(p, center) for p in all_points)
    for part in parts:
        part['distance'] = math.dist(part['center'], center)
        part['far'] = part['distance'] > 3 * max(radius, 1e-9)
    return dict(center=center, median_radius=radius, threshold=3 * max(radius, 1e-9),
                bounds=[[min(p[c] for p in all_points) for c in range(3)],
                        [max(p[c] for p in all_points) for c in range(3)]],
                parts=parts, far_parts=[p for p in parts if p['far']])


def prepare_preview(scene, source, aliases, anis):
    pose = choose_preview_pose(scene, source, aliases, anis)
    names = Counter(obj['name'] for _, obj, _ in iter_objects(scene))
    targets = Counter(a.get('object_name') or a['name'] for a in scene['animations'])
    pose['duplicate_bindings'] = [dict(name=name, objects=names[name], tracks=count,
                                       policy='Last matching track applies to every object with this name')
                                  for name, count in targets.items() if count > 1 or names[name] > 1]
    stored = evaluate_world_matrices(scene, None)
    try:
        if pose['frame'] is None:
            raise ValueError(pose['reason'])
        worlds = evaluate_world_matrices(scene, pose['frame'])
        pose['status'] = 'evaluated'
    except (ValueError, OverflowError) as exc:
        worlds = stored
        pose.update(status='stored-fallback', requested_frame=pose['frame'], frame=None, fallback_reason=str(exc))
    pose['distances'] = dict(stored=measure_parts(scene, stored), preview=measure_parts(scene, worlds))
    return worlds, pose


def build_model(scene, textures, world_matrices=None):
    """Build static geometry; omitted matrices retain the original stored pose."""
    result = dict(game='azrael', winding='ccw', vertices=[], uvs=[], indices=[], groups=[],
                  material_names=[], material_textures=[], material_settings=[], metadata={})
    batches, omitted = defaultdict(list), Counter()

    worlds = world_matrices if world_matrices is not None else evaluate_world_matrices(scene, None)
    for path, obj, _ in iter_objects(scene):
        world = worlds[path]
        for mesh in obj['meshes']:
            for face, name in zip(mesh['faces'], mesh['materials']):
                mat = scene['materials'].get(name)
                if mat is None or mat['colour'][3] == 0:
                    omitted['material-less' if mat is None else 'alpha-zero'] += 1
                    continue
                corners = []
                # Row-vector local * parent, then reflect Z and reverse winding.
                for vi, normal_index in reversed(face):
                    p = mesh['vertices'][vi] + [1.]
                    q = [sum(p[k] * world[k * 4 + c] for k in range(4)) for c in range(3)]
                    q[2] = -q[2]
                    corners.append((q, mesh['uvs'][vi] if mesh['uvs'] else [0., 0.]))
                batches[name].append(corners)
    for name, triangles in batches.items():
        mat = scene['materials'][name]
        slot = len(result['material_names'])
        result['material_names'].append(name)
        result['material_textures'].append(textures[name])
        settings = dict(unlit=True)
        if mat['texture'] and '#' in mat['texture']:
            settings['alphaFunc'] = 'GE128'
        if not mat['texture'] and 0 < mat['colour'][3] < 1:
            settings.update(blend=['gl_src_alpha', 'gl_one_minus_src_alpha'], depthWrite=False)
        result['material_settings'].append(settings)
        result['groups'].append(dict(start=len(result['indices']), count=len(triangles) * 3, materialIndex=slot))
        for tri in triangles:
            for point, uv in tri:
                result['indices'].append(len(result['vertices']) // 3)
                result['vertices'].extend(point)
                result['uvs'].extend(uv)
    if not result['indices']:
        raise ValueError('No visible triangles')
    result['metadata'].update(pose='Supplied world matrices' if world_matrices is not None else 'Stored XAF matrices', omitted_faces=dict(omitted),
                              format='Reality Lab XAF/RES', warnings=[])
    return result


def inventory(install):
    """Metadata-only catalog planning; no texture decoding or model output."""
    game, cd = read_install(install)
    xafs = sorted(n for n in cd.files if n.startswith('textures/') and n.count('/') == 1 and n.endswith('.xaf'))
    room_path = child(game, 'ROOMS.DAT')
    if not room_path:
        raise ValueError('ROOMS.DAT required to exclude room scenes')
    rooms = read_rooms(read_file(room_path))
    used = {r['xaf'] for r in rooms['headers']}
    aliases, anis = defaultdict(list), []
    for path in sorted(game.iterdir()):
        if path.suffix.lower() != '.ani':
            continue
        row = dict(source=path.name, alias=path.stem.title())
        try:
            row.update(status='ready', data=parse_ani(read_file(path, 1024 * 1024)))
            for name in row['data']['files']:
                aliases['textures/' + name].append(row)
        except (ValueError, OSError) as exc:
            row.update(status='skipped', reason=str(exc))
        anis.append(row)
    rows = []
    for name in xafs:
        row = dict(source=name, res_source=name[:-4] + '.res', aliases=[r['alias'] for r in aliases[name]])
        try:
            raw = cd.read(name)
            # Metadata scan only; full validation happens for selected models.
            animated = bool(re.search(rb'\banimation_set\s*\{', raw))
            row.update(xaf_sha256=hashlib.sha256(raw).hexdigest(), animated=animated)
            if not raw.strip(b'\x1a \r\n\t'):
                raise ValueError('Empty XAF')
            if name == 'textures/sailor_a.xaf':
                raise ValueError('Sailor placeholder box; real rig not located')
            if not re.search(rb'\bmaterial\s+"', raw):
                raise ValueError('No materials (camera dummy/non-drawable scene)')
            if row['aliases']:
                row.update(category='Characters', status='pending')
            elif name in used:
                raise ValueError('Room scene (ROOMS.DAT); reserved for Maps')
            elif animated:
                row.update(category='Other rigs', status='pending')
            else:
                raise ValueError('Static scene or camera dummy; reserved for Maps/extras')
        except (ValueError, OSError) as exc:
            row.update(status='skipped', reason=str(exc))
        rows.append(row)
    for source, entries in aliases.items():
        if source not in cd.files:
            rows.append(dict(source=source, aliases=[r['alias'] for r in entries], status='skipped', reason='ANI references missing XAF'))
    return cd, rows, anis, rooms


def import_catalog(install, output, model_filter=None):
    cd, rows, anis, rooms = inventory(install)
    output = Path(output)
    for folder in ('textures', 'model_json'):
        (output / folder).mkdir(parents=True, exist_ok=True)
    catalog, seen, texture_rows = [], {}, []
    for row in sorted(rows, key=lambda r: (r.get('category') != 'Characters', r['source'])):
        if row['status'] != 'pending':
            continue
        name = row['source']
        if model_filter and model_filter.lower() not in name and not any(model_filter.lower() in a.lower() for a in row['aliases']):
            row.update(status='filtered', reason='Excluded by requested model filter')
            continue
        try:
            scene = parse_xaf(cd.read(name))
            worlds, pose = prepare_preview(scene, name, row['aliases'], anis)
            row['pose'] = pose
            res_name = name[:-4] + '.res'
            if res_name not in cd.files:
                raise ValueError('Missing RES bundle: ' + res_name)
            raw = cd.read(res_name)
            records = list(res_records(raw))
            if len(records) != len(scene['textures']):
                raise ValueError('XAF texture/RES record count mismatch')
            identity = (row['xaf_sha256'], hashlib.sha256(raw).hexdigest(), pose['frame'])
            if identity in seen:
                item = seen[identity]
                item['aliases'].extend(a for a in row['aliases'] if a not in item['aliases'])
                item['source_aliases'].append(name)
                row.update(status='duplicate', model=item['model_name'], reason='Byte-identical XAF and RES, same preview frame; listed once')
                continue
            textures = {}
            for texture_name, record in zip(scene['textures'], records):
                png = safe(Path(name).stem + '_' + Path(texture_name).stem) + '_' + hashlib.sha256((name + ':' + texture_name).encode()).hexdigest()[:10] + '.png'
                tr = dict(source=res_name, index=record['index'], name=texture_name, texture=png)
                texture_rows.append(tr)
                try:
                    image = decode_res(record, texture_name)
                    target = output / 'textures' / png
                    if not target.is_file():
                        image.save(target)
                    textures[texture_name] = png
                    tr.update(status='ready', width=image.width, height=image.height)
                except (ValueError, OSError) as exc:
                    tr.update(status='skipped', reason=str(exc))
                    raise ValueError('Texture decode failed: ' + texture_name + ': ' + str(exc)) from exc
            slots = {}
            for mat_name, mat in scene['materials'].items():
                if mat['texture']:
                    slots[mat_name] = textures[mat['texture']]
                else:
                    # Solid materials are editable PNGs too; alpha survives GLB/OBJ.
                    rgba = tuple(round(x * 255) for x in mat['colour'])
                    png = 'colour_' + bytes(rgba).hex() + '.png'
                    target = output / 'textures' / png
                    if not target.is_file():
                        Image.new('RGBA', (1, 1), rgba).save(target)
                    slots[mat_name] = png
            data = build_model(scene, slots, worlds)
            model = 'azrael_' + safe(Path(name).stem) + '_' + hashlib.sha256(name.encode()).hexdigest()[:8]
            alias_list = row['aliases']
            title = canonical_alias(name, alias_list)
            item = dict(model_name=model, display_name=title + (' (' + ', '.join(a for a in alias_list if a != title) + ')' if len(alias_list) > 1 else ''),
                        game='azrael', category=row['category'], texture_name=data['material_textures'][0],
                        source=name, aliases=list(alias_list), source_aliases=[], status='ready')
            data['metadata'].update(source=name, aliases=alias_list, pose=pose,
                ani=[a for a in anis if a['status'] == 'ready' and Path(name).name in a['data']['files']])
            (output / 'model_json' / (model + '.json')).write_text(json.dumps(data, separators=(',', ':'), allow_nan=False), encoding='utf-8')
            seen[identity] = item
            catalog.append(item)
            row.update(status='ready', model=model, triangles=len(data['indices']) // 3, omitted_faces=data['metadata']['omitted_faces'])
        except (ValueError, OSError) as exc:
            row.update(status='skipped', reason=str(exc))
    report = dict(entries=len(catalog), ready=len(catalog), duplicates=sum(r['status'] == 'duplicate' for r in rows),
                  skipped=sum(r['status'] == 'skipped' for r in rows), textures=sum(r['status'] == 'ready' for r in texture_rows),
                  categories=dict(Counter(r['category'] for r in catalog)), results=rows, texture_results=texture_rows,
                  ani_results=anis, rooms=rooms, pose='Canonical ANI REST/first clip; other rigs earliest key',
                  pose_counts=dict(evaluated=sum(r['status'] == 'ready' and r['pose']['status'] == 'evaluated' for r in rows),
                                   fallback=sum(r['status'] == 'ready' and r['pose']['status'] == 'stored-fallback' for r in rows),
                                   stored_far=sum(r['status'] == 'ready' and bool(r['pose']['distances']['stored']['far_parts']) for r in rows),
                                   still_far=sum(r['status'] == 'ready' and bool(r['pose']['distances']['preview']['far_parts']) for r in rows)),
                  res_results=[dict(source=r['res_source'], scene=r['source'], status=r['status'],
                                    reason=r.get('reason', 'Decoded for model')) for r in rows if 'res_source' in r])
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--filter', help='Case-insensitive XAF path or character name substring')
    args = parser.parse_args()
    print(json.dumps(import_catalog(args.install, args.output or LOCAL_DATA / 'azrael', args.filter), indent=1))

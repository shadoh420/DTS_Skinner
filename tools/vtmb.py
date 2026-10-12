"""Bloodlines readers shared by model and future BSP imports.

Layouts were checked against local Troika files, not Valve VPK/HL2 MDL layouts.
All paths are virtual, case-insensitive names. Archives are indexed, never extracted.
"""
from dataclasses import dataclass
from pathlib import Path
import math
import re
import struct
import zlib

import numpy as np
from PIL import Image


def asset_name(name):
    parts = name.replace('\\', '/').lower().split('/')
    if not name or any(p in ('', '.', '..') or ':' in p or '\0' in p for p in parts):
        raise ValueError(f'Unsafe asset name: {name!r}')
    return '/'.join(parts)


def asset_reference(name):
    """In-file virtual references may have leading/repeated separators.

    These only address the asset dictionary. Archive/file names still go
    through asset_name unchanged; traversal and drive names remain errors.
    """
    return asset_name('/'.join(p for p in name.replace('\\', '/').split('/') if p))


def span(data, offset, count, size=1):
    if offset < 0 or count < 0 or size < 1 or offset + count * size > len(data):
        raise ValueError(f'Invalid binary range: {offset}+{count}*{size} / {len(data)}')


def unpack(data, fmt, offset=0):
    span(data, offset, struct.calcsize('<' + fmt))
    return struct.unpack_from('<' + fmt, data, offset)


def string(data, offset):
    span(data, offset, 1)
    end = data.find(b'\0', offset)
    if end < 0:
        raise ValueError('Unterminated string')
    return data[offset:end].decode('cp1252')


@dataclass(frozen=True)
class Asset:
    path: Path
    offset: int
    size: int
    origin: str
    directory: bool = False

    def read(self):
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            data = stream.read(self.size)
        if len(data) != self.size:
            raise ValueError(f'Truncated asset in {self.path.name}')
        return data


def read_vpk(path):
    """Troika: payloads, directory [u32 length, name bytes, u32 offset,size],
    then a nine-byte [u32 count, u32 directory offset, u8 directory-only flag] footer.
    Flag 1 (pack011) denotes directories. Flag 0 can contain empty real files.
    """
    path = Path(path)
    size = path.stat().st_size
    if size < 9:
        raise ValueError('Truncated Troika VPK footer')
    with path.open('rb') as stream:
        stream.seek(size - 9)
        count, directory, marker = struct.unpack('<IIB', stream.read(9))
        if marker not in (0, 1) or directory > size - 9 or count > (size - 9 - directory) // 13:
            raise ValueError('Invalid Troika VPK directory/footer')
        stream.seek(directory)
        raw = stream.read(size - 9 - directory)
    entries, at = [], 0
    for _ in range(count):
        length, = unpack(raw, 'I', at)
        at += 4
        if not 1 <= length <= 4096:
            raise ValueError('Invalid Troika VPK name length')
        span(raw, at, length)
        name = asset_name(raw[at:at + length].decode('cp1252'))
        at += length
        offset, length = unpack(raw, 'II', at)
        at += 8
        if offset + length > directory or marker == 1 and length:
            raise ValueError(f'VPK payload overlaps directory: {name}')
        entries.append((name, Asset(path, offset, length, path.name, bool(marker))))
    if at != len(raw):
        raise ValueError('Trailing bytes in Troika VPK directory')
    return entries


def read_install(install):
    """Metadata-only index: numbered stock VPKs, stock loose, patch loose.
    Only models/materials/maps are traversed; save/screenshots are never visited.
    Returns winners and a provenance/count receipt, without reading payloads.
    """
    root = Path(install).expanduser().resolve()
    if root.name.lower() in ('vampire', 'unofficial_patch'):
        root = root.parent
    stock, patch = root / 'Vampire', root / 'Unofficial_Patch'
    packs = sorted(stock.glob('pack*.vpk'), key=lambda p: int(p.stem[4:])
                   if p.stem[4:].isdigit() else -1)
    packs = [p for p in packs if re.fullmatch(r'pack\d+', p.stem, re.I)]
    if not packs:
        raise ValueError(f'No Bloodlines Vampire/pack*.vpk in {root}')
    files, archives, overrides = {}, {}, []
    for pack in packs:
        entries = read_vpk(pack)
        archives[pack.name] = dict(entries=len(entries), directories=sum(a.directory for _, a in entries),
                                   empty_files=sum(a.size == 0 and not a.directory for _, a in entries))
        for name, asset in entries:
            if asset.directory:
                continue
            if name in files:
                overrides.append(dict(source=name, previous=files[name].origin, winner=asset.origin))
            files[name] = asset
    patch_counts = {suffix: dict(added=0, replaced=0) for suffix in ('.mdl', '.vmt', '.vtf', '.tth', '.ttz', '.bsp')}
    for folder in (stock, patch):
        for sub in ('models', 'materials', 'maps'):
            for path in sorted((folder / sub).rglob('*')):
                if not path.is_file():
                    continue
                name = asset_name(path.relative_to(folder).as_posix())
                origin = folder.name + '/loose'
                if folder == patch and path.suffix.lower() in patch_counts:
                    patch_counts[path.suffix.lower()]['replaced' if name in files else 'added'] += 1
                if name in files:
                    overrides.append(dict(source=name, previous=files[name].origin, winner=origin))
                files[name] = Asset(path, 0, path.stat().st_size, origin)
    return files, dict(archives=archives, patch=patch_counts, overrides=overrides,
                      search_order=['Unofficial_Patch/loose', 'Vampire/loose', 'Vampire/packNNN.vpk descending'])


def read_vmt(data):
    """Read KeyValues, preserving nested proxies for explicit approximation notes."""
    text = data.decode('utf-8-sig', errors='replace')
    tokens = re.findall(r'"([^"\r\n]*)"|(/\*[\s\S]*?\*/|//[^\r\n]*|[{}]|[^\s{}"]+)', text)
    tokens = [a if a else b for a, b in tokens if not b.startswith(('//', '/*'))]
    at = 0

    def block():
        nonlocal at
        result = {}
        while at < len(tokens) and tokens[at] != '}':
            key = tokens[at].lower(); at += 1
            if at >= len(tokens):
                raise ValueError('Missing VMT value')
            value = tokens[at]; at += 1
            if value == '{':
                value = block()
            elif value == '}':
                raise ValueError('Missing VMT value before closing brace')
            result[key] = value
        if at >= len(tokens):
            raise ValueError('Unclosed VMT block')
        at += 1
        return result

    if len(tokens) < 3 or tokens[1] != '{':
        raise ValueError('Expected VMT shader and block')
    shader = tokens[0]; at = 2
    params = block()
    if at != len(tokens):
        raise ValueError('Trailing VMT tokens')
    return dict(shader=shader, params=params)


def material_settings(vmt):
    p = vmt['params']
    enabled = lambda key: str(p.get(key, '0')).lower() not in ('0', '0.0', 'false', '')
    blend = ['gl_src_alpha', 'gl_one'] if enabled('$additive') else (
        ['gl_src_alpha', 'gl_one_minus_src_alpha'] if enabled('$translucent') else [])
    notes = []
    for key in ('$envmap', '$bumpmap', '$detail', '$basetexturetransform', 'proxies'):
        if key in p:
            notes.append(f'{key} is recorded but not evaluated')
    if enabled('$selfillum'):
        notes.append('$selfillum recorded; shared preview/export lighting does not reproduce its alpha mask')
    if vmt['shader'].lower() not in ('vertexlitgeneric', 'lightmappedgeneric', 'unlitgeneric'):
        notes.append(f'{vmt["shader"]}: base-texture approximation')
    return dict(alphaFunc='GE128' if enabled('$alphatest') else '', blend=blend,
                depthWrite=not bool(blend), cull='none' if enabled('$nocull') else 'back',
                shader=vmt['shader'], unlit=vmt['shader'].lower() == 'unlitgeneric',
                selfillum=enabled('$selfillum'), parameters=p), notes


def image_size(fmt, width, height):
    if fmt in (13, 14, 15, 20):
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * (8 if fmt in (13, 20) else 16)
    sizes = {0: 4, 1: 4, 2: 3, 3: 3, 4: 2, 5: 1, 6: 2, 8: 1, 9: 3, 10: 3,
             11: 4, 12: 4, 16: 4, 17: 2, 18: 2, 19: 2, 21: 2, 22: 4, 24: 8, 25: 8}
    if fmt not in sizes:
        raise ValueError(f'Unsupported VTF image format {fmt}')
    return width * height * sizes[fmt]


def read_vtf(data):
    if data[:4] != b'VTF\0':
        raise ValueError('Expected VTF signature')
    major, minor, header = unpack(data, 'III', 4)
    if major != 7 or minor not in range(6):
        raise ValueError(f'Unsupported VTF version {major}.{minor}')
    span(data, 0, max(header, 64))
    w, h, flags, frames, first = unpack(data, 'HHIHH', 16)
    fmt, mips, lowfmt, lw, lh = unpack(data, 'IBIBB', 52)
    depth = unpack(data, 'H', 63)[0] if minor >= 2 else 1
    if not (0 < w <= 16384 and 0 < h <= 16384 and 0 < frames <= 4096 and 0 < mips <= 15 and depth > 0):
        raise ValueError('Invalid VTF dimensions/frames/mips')
    faces = (7 if minor < 5 and first != 65535 else 6) if flags & 0x4000 else 1
    offset = header + (image_size(lowfmt, lw, lh) if lw and lh else 0)
    if minor >= 3:
        resources, = unpack(data, 'I', 68)
        if header < 80 + resources * 8:
            raise ValueError('VTF resource directory exceeds header')
        span(data, 80, resources, 8)
        found = False
        for i in range(resources):
            at = 80 + i * 8
            if data[at:at + 3] == b'\x30\0\0':
                if data[at + 3] & 2:
                    raise ValueError('VTF image resource cannot be inline')
                offset, = unpack(data, 'I', at + 4); found = True
        if not found:
            raise ValueError('VTF has no high-resolution image resource')
    for mip in range(mips - 1, 0, -1):
        offset += image_size(fmt, max(1, w >> mip), max(1, h >> mip)) * frames * faces * max(1, depth >> mip)
    size = image_size(fmt, w, h)
    span(data, offset, size * frames * faces * depth)
    pixels = data[offset:offset + size]
    if fmt in (13, 14, 15, 20):
        # Pillow's native BC decoder also underlies the existing DDS importer.
        image = Image.frombytes('RGBA', (w, h), pixels, 'bcn', (1 if fmt in (13, 20) else fmt - 12, 'DXT1' if fmt in (13, 20) else 'DXT3' if fmt == 14 else 'DXT5'))
    elif fmt in (0, 1, 2, 3, 11, 12, 16, 5, 8, 6):
        mode, rawmode = {0: ('RGBA', 'RGBA'), 1: ('RGBA', 'ABGR'), 2: ('RGB', 'RGB'),
                        3: ('RGB', 'BGR'), 11: ('RGBA', 'ARGB'), 12: ('RGBA', 'BGRA'),
                        16: ('RGB', 'BGRX'), 5: ('L', 'L'), 8: ('L', 'L'), 6: ('LA', 'LA')}[fmt]
        image = Image.frombytes(mode, (w, h), pixels, 'raw', rawmode).convert('RGBA')
        if fmt == 8:
            alpha = image.getchannel('R'); image = Image.new('RGBA', (w, h), 'white'); image.putalpha(alpha)
    elif fmt in (4, 17, 18, 19):
        colors = []
        for v, in struct.iter_unpack('<H', pixels):
            if fmt in (4, 17):
                r, g, b, a = (v >> 11) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31, 255
                if fmt == 17: r, b = b, r
            elif fmt == 18:
                b, g, r, a = (v & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31, ((v >> 10) & 31) * 255 // 31, 255
            else:
                b, g, r, a = (v & 15) * 17, ((v >> 4) & 15) * 17, ((v >> 8) & 15) * 17, (v >> 12) * 17
            colors.append((r, g, b, a))
        image = Image.new('RGBA', (w, h)); image.putdata(colors)
    else:
        raise ValueError(f'Unsupported VTF pixel decoder {fmt}')
    return image, dict(version=f'{major}.{minor}', format=fmt, width=w, height=h, frames=frames, faces=faces, depth=depth, mipmaps=mips,
                       selection='largest mip, frame 0, face 0, depth slice 0')


def read_texture(files, name):
    base = asset_reference(name).removeprefix('materials/')
    base = re.sub(r'\.(vtf|tth|ttz)$', '', base)
    base = 'materials/' + base
    if base + '.vtf' in files:
        image, info = read_vtf(files[base + '.vtf'].read())
        info['sources'] = [dict(path=base+'.vtf', origin=files[base+'.vtf'].origin)]
        return image, info
    if base + '.tth' not in files:
        raise ValueError(f'Missing texture: {base}.vtf or .tth')
    head = files[base + '.tth'].read()
    if head[:4] != b'TTH\0':
        raise ValueError('Expected Troika TTH signature')
    # TTH v1: u16 version, u8 mip count, u8 resident count, u32 resident end;
    # followed by mip offset/compressed-length pairs and the embedded VTF.
    version, levels, resident, end = unpack(head, 'HBBI', 4)
    at = 12 + (levels + 1) * 8
    # effects/black has zero resident high-res mips: only the VTF header and
    # thumbnail are in TTH; the complete high-res mip chain lives in TTZ.
    if version != 1 or not 1 <= levels <= 15 or not 0 <= resident <= levels or head[at:at + 4] != b'VTF\0':
        raise ValueError('Unsupported TTH layout')
    if end != len(head) - at:
        raise ValueError('Invalid TTH resident length')
    tail = b''
    if resident < levels:
        if base + '.ttz' not in files:
            raise ValueError(f'Missing TTZ companion: {base}.ttz')
        compressed = files[base + '.ttz'].read()
        expected, compressed_size = unpack(head, 'II', 12 + levels * 8)
        if compressed_size != len(compressed) or not end <= expected <= 256 * 1024 * 1024:
            raise ValueError('Invalid TTH/TTZ size table')
        decoder = zlib.decompressobj()
        try:
            tail = decoder.decompress(compressed, expected - end + 1)
        except zlib.error as exc:
            raise ValueError(f'Invalid TTZ zlib data: {exc}') from exc
        if len(tail) != expected - end or not decoder.eof or decoder.unused_data:
            raise ValueError('TTZ decompressed size/stream mismatch')
    image, info = read_vtf(head[at:] + tail)
    info['container'] = 'TTH/TTZ'
    info['sources'] = [dict(path=base+suffix, origin=files[base+suffix].origin)
                       for suffix in (('.tth', '.ttz') if resident < levels else ('.tth',))]
    return image, info


def _matrix(pos, quat):
    q = np.array(quat, dtype=float)
    norm = np.linalg.norm(q)
    if not math.isfinite(norm) or norm < 1e-8:
        raise ValueError('Invalid MDL bone quaternion')
    x, y, z, w = q / norm
    out = np.eye(4)
    out[:3, :3] = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                   [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                   [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]
    out[:3, 3] = pos
    return out


def mdl_pose(data):
    nb, bones = unpack(data, 'ii', 240)
    na, anims, ns, sequences = unpack(data, '4i', 264)
    if not 0 <= nb <= 4096 or not 0 <= ns <= 65536 or not 0 <= na <= 65536:
        raise ValueError('Invalid MDL bone/sequence counts')
    span(data, bones, nb, 160); span(data, sequences, ns, 764); span(data, anims, na, 72)
    choices = [(i, string(data, sequences + i * 764 + unpack(data, 'i', sequences + i * 764)[0])) for i in range(ns)]
    chosen = next(((i, n) for i, n in choices if 'idle' in n.lower()), None)
    chosen = chosen or next(((i, n) for i, n in choices if n.lower() in ('reference', 'ref', 'bind', 'bindpose')), None)
    animation = None
    if chosen:
        seq = sequences + chosen[0] * 764
        blends, index = unpack(data, 'ii', seq + 52)
        if blends != 1:
            raise ValueError(f'Sequence {chosen[1]} has {blends} blends; pose evaluation unresolved')
        if not 0 <= index < na:
            raise ValueError('Invalid MDL sequence animation index')
        desc = anims + index * 72
        flags, frames = unpack(data, 'ii', desc + 8)
        if flags & ~1 or frames < 1:
            raise ValueError(f'Unsupported MDL animation flags/count: {flags}/{frames}')
        relative, = unpack(data, 'i', desc + 48)
        animation = desc + relative
        span(data, animation, nb, 32)
    matrices, inverse = [], []
    for i in range(nb):
        at = bones + i * 160
        parent, = unpack(data, 'i', at + 4)
        if parent < -1 or parent >= i:
            raise ValueError('MDL bones must have preceding parents')
        values = list(unpack(data, '7f', at + 32))
        if animation is not None:
            sample = animation + i * 32
            weight, = unpack(data, 'f', sample)
            if weight not in (0, 1):
                raise ValueError(f'Unsupported MDL animation bone weight {weight}')
            scales = unpack(data, '7f', at + 60)
            for c, off in enumerate(unpack(data, '7i', sample + 4)):
                if off:
                    valid, total, value = unpack(data, 'BBh', sample + off)
                    if not 0 < valid <= total:
                        raise ValueError('Invalid MDL animation RLE run')
                    # Position tracks are deltas; quaternion channels are absolute.
                    values[c] = (values[c] if c < 3 else 0) + scales[c] * value
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Nonfinite MDL bone pose')
        local = _matrix(values[:3], values[3:])
        matrices.append(local if parent == -1 else matrices[parent] @ local)
        inv = np.eye(4); inv[:3] = np.array(unpack(data, '12f', at + 88)).reshape(3, 4)
        if not np.isfinite(inv).all():
            raise ValueError('Nonfinite MDL inverse bind matrix')
        inverse.append(inv)
    transforms = [a @ b for a, b in zip(matrices, inverse)] if chosen else [np.eye(4) for _ in matrices]
    return transforms, dict(pose=chosen[1] if chosen else 'bind', frame=0,
                           reason='first local idle sequence, then reference; otherwise embedded bind vertices',
                           sequences=[n for _, n in choices])


def mdl_companions(data, name, files):
    """Named VTX first, then the MDL's authored path for renamed copies.

    A candidate is not trusted here: read_mdl must still verify checksum, body,
    mesh, vertex and strip ranges. Never search by basename or bypass layering.
    """
    stems = [asset_name(name).removesuffix('.mdl')]
    if len(data) >= 140 and data[:4] == b'IDST':
        authored = data[12:140].split(b'\0')[0].decode('cp1252').replace('\\', '/').lower()
        if authored.endswith('.mdl'):
            authored = asset_name(authored if authored.startswith('models/') else 'models/' + authored)
            if authored[:-4] not in stems:
                stems.append(authored[:-4])
    return [stem + suffix for stem in stems for suffix in ('.dx90.vtx', '.dx80.vtx', '.dx7_2bone.vtx', '.vtx')
            if stem + suffix in files]


def read_mdl(data, vtx, skin=0):
    """2531 MDL embedded full/quantized vertices + 107 VTX, LOD 0, bodygroup 0.
    Returns Skinner geometry and all skin tables/material search directories.
    No VVD is used by this layout. Unknown versions are explicit errors.
    """
    span(data, 0, 424)
    version, checksum = unpack(data, 'iI', 4)
    if data[:4] != b'IDST' or version != 2531:
        raise ValueError(f'Unsupported Bloodlines MDL signature/version {data[:4]!r}/{version}')
    length, = unpack(data, 'i', 140)
    # A patched taine.mdl has one text-editor LF after its declared binary image.
    # Never let a trailer satisfy offsets: validate all following reads against length.
    trailer = data[length:] if 424 <= length <= len(data) else b''
    if length != len(data) and trailer != b'\n':
        raise ValueError('MDL declared length mismatch')
    data = data[:length]
    vv, = unpack(vtx, 'i')
    vc, lods, _, vb, vbo = unpack(vtx, 'I4i', 16)
    if vv != 107 or vc != checksum or lods < 1:
        raise ValueError(f'Unsupported/mismatched VTX: version={vv}, checksum={vc}, LODs={lods}')
    nt, textures, nd, directories, refs, families, skins, nb, bodies = unpack(data, '9i', 292)
    if min(nt, nd, refs, families, nb, vb) < 0:
        raise ValueError('Negative MDL/VTX count')
    for off, n, stride in ((textures, nt, 20), (directories, nd, 4), (skins, refs * families, 2), (bodies, nb, 16)):
        span(data, off, n, stride)
    span(vtx, vbo, vb, 8)
    if vb != nb or not 0 <= skin < max(1, families) or refs < 0 or families < 0:
        raise ValueError('Invalid MDL skin/body counts')
    names = [string(data, textures+i*20+unpack(data, 'i', textures+i*20)[0]) for i in range(nt)]
    dirs = [string(data, unpack(data, 'i', directories+i*4)[0]) for i in range(nd)]
    table = [list(unpack(data, f'{refs}H', skins+i*refs*2)) for i in range(families)] or [list(range(nt))]
    if any(v >= nt for row in table for v in row):
        raise ValueError('Invalid MDL skin material index')
    transforms, pose = mdl_pose(data)
    vertices, normals, uvs, indices, groups, skin_refs = [], [], [], [], [], []
    defaults, formats, vertex_layouts = [], [], set()
    for body in range(nb):
        bp = bodies + body * 16
        _, models, _, mo = unpack(data, '4i', bp)
        vp = vbo + body * 8
        vm, vmo = unpack(vtx, 'ii', vp)
        if models < 1 or vm != models:
            raise ValueError('MDL/VTX bodygroup model count mismatch')
        span(data, bp + mo, models, 224); span(vtx, vp + vmo, vm, 8)
        m = bp + mo  # body value zero chooses the first model of every bodypart
        defaults.append(string(data, m))
        model_type, = unpack(data, 'i', m + 128)
        if model_type != 0:
            raise ValueError(f'Unsupported MDL model type {model_type}; expected type 0')
        nm, meshes, nv, vo = unpack(data, '4i', m + 136)
        normal_offset, encoding = unpack(data, '2i', m + 152)
        if encoding not in (0, 1, 2):
            raise ValueError(f'Unsupported MDL vertex encoding {encoding}')
        stride = (44, 12, 8)[encoding]
        formats.append(encoding)
        span(data, m + meshes, nm, 60); span(data, m + vo, nv, stride)
        if encoding:
            span(data, m + normal_offset, nv, 16)
            bias = np.array(unpack(data, '3f', m + 160))
            scale = np.array(unpack(data, '3f', m + 172))
            if not np.isfinite(bias).all() or not np.isfinite(scale).all() or (scale < 0).any():
                raise ValueError('Invalid MDL quantization parameters')
            if len(transforms) > 1:
                raise ValueError('Compressed MDL vertices with multiple bones unresolved')
        vmodel = vp + vmo
        nl, lo = unpack(vtx, 'ii', vmodel)
        if nl < 1: raise ValueError('VTX model has no LODs')
        lod = vmodel + lo
        vn, vmesh = unpack(vtx, 'ii', lod)
        if vn != nm: raise ValueError('MDL/VTX mesh count mismatch')
        span(vtx, lod + vmesh, nm, 8)
        start_vertex = len(vertices) // 3
        for i in range(nv):
            at = m + vo + i * stride
            if encoding == 0:
                weights = list(data[at:at+3])
                weights.append(255 - sum(weights))
                if weights[-1] < 0: raise ValueError('Invalid MDL vertex weights')
                # Byte 3 is padding. Four uint16 bones at 4; weight 4 is implicit.
                bones = unpack(data, '4H', at + 4)
                vals = unpack(data, '8f', at + 12)
                position, normal, uv = vals[:3], vals[3:6], vals[6:8]
                transform = np.zeros((4, 4))
                for weight, bone in zip(weights, bones):
                    if not weight: continue
                    if bone >= len(transforms): raise ValueError('Invalid MDL vertex bone')
                    transform += transforms[bone] * (weight / 255)
            else:
                # Type 1: uint16 xyz, two padding bytes, UNORM16 uv.
                # Type 2: UNORM8 xyz, one padding byte, UNORM16 uv.
                quantized = np.array(unpack(data, '3H' if encoding == 1 else '3B', at))
                position = bias + scale * quantized / (255 if encoding == 2 else 1)
                normal = unpack(data, '3f', m + normal_offset + i*16)
                uv = np.array(unpack(data, '2H', at + stride - 4)) / 65535
                transform = transforms[0] if transforms else np.eye(4)
            if not all(math.isfinite(v) for v in (*position, *normal, *uv)):
                raise ValueError('Nonfinite MDL vertex')
            point = transform @ [*position, 1]
            normal = transform[:3, :3] @ normal
            norm = np.linalg.norm(normal)
            if norm: normal /= norm
            # Proper Z-up -> Y-up rotation, preserves handedness.
            vertices.extend((float(point[1]), float(point[2]), float(point[0])))
            normals.extend((float(normal[1]), float(normal[2]), float(normal[0])))
            uvs.extend(float(v) for v in uv)
        for mesh in range(nm):
            mat, _, count, first = unpack(data, '4i', m + meshes + mesh * 60)
            if mat < 0 or mat >= len(table[skin]) or count < 0 or first < 0 or first+count > nv:
                raise ValueError('Invalid MDL mesh material/vertex range')
            vm = lod + vmesh + mesh * 8
            ng, mesh_flags, go = unpack(vtx, 'HHi', vm)
            span(vtx, vm+go, ng, 20)
            begin = len(indices)
            for g in range(ng):
                sg = vm + go + g * 20
                nverts, nindices, nstrips, flags, _, verts_at, idx_at, strips_at = unpack(vtx, '3H2B3i', sg)
                layout = flags & 0x18
                if layout not in (0x08, 0x10):
                    raise ValueError(f'Unsupported VTX vertex layout flags {flags:#x}')
                vertex_stride, original_at = (2, 0) if layout == 0x10 else (12, 10)
                vertex_layouts.add(vertex_stride)
                span(vtx, sg+verts_at, nverts, vertex_stride); span(vtx, sg+idx_at, nindices, 2); span(vtx, sg+strips_at, nstrips, 16)
                remap = [unpack(vtx, 'H', sg+verts_at+i*vertex_stride+original_at)[0] for i in range(nverts)]
                if any(v >= count for v in remap): raise ValueError('Invalid VTX original mesh vertex')
                ix = unpack(vtx, f'{nindices}H', sg+idx_at)
                if any(v >= nverts for v in ix): raise ValueError('Invalid VTX vertex index')
                for s in range(nstrips):
                    st = sg + strips_at + s * 16
                    ni, io, snv, svo, bones_used, flags, changes, change_at = unpack(vtx, '4HBBHi', st)
                    if io+ni > nindices or svo+snv > nverts: raise ValueError('Invalid VTX strip range')
                    seq = ix[io:io+ni]
                    if flags == 1:
                        if ni % 3: raise ValueError('VTX triangle-list size is not a multiple of three')
                        triangles = [seq[i:i+3] for i in range(0, ni, 3)]
                    elif flags == 2:
                        triangles = [(seq[i+1], seq[i], seq[i+2]) if i % 2 else seq[i:i+3] for i in range(ni-2)]
                    else: raise ValueError(f'Unsupported VTX primitive flags {flags}')
                    for tri in triangles:
                        if len(set(tri)) == 3:
                            # VTX triangles face clockwise; preview/GLB use CCW.
                            indices.extend(start_vertex + first + remap[v] for v in (tri[0], tri[2], tri[1]))
            if len(indices) > begin:
                groups.append(dict(start=begin, count=len(indices)-begin, materialIndex=table[skin][mat]))
                skin_refs.append(mat)
    if not indices: raise ValueError('No triangles in default bodygroups / LOD 0')
    pose.update(version=version, vtx_version=vv, skin_family=skin, skin_families=len(table), bodygroups=defaults,
                lod=0, material_directories=dirs, vertex_encodings=formats, vtx_vertex_strides=sorted(vertex_layouts),
                trailing_lf=bool(trailer))
    return dict(game='vtmb', winding='ccw', vertices=vertices, normals=normals, uvs=uvs, indices=indices,
                groups=groups, material_names=names, metadata=pose, skin_table=table, skin_refs=skin_refs)

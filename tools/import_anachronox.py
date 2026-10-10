"""Anachronox first-frame models and model skins, as game anachronox.

python tools/import_anachronox.py --install "C:/Program Files (x86)/Steam/steamapps/common/Anachronox"

ADAT directories, layered asset reads and Pillow texture decoding are also usable
by map importers. No installed files are changed. Existing PNG edits survive.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import zipfile
import zlib

from PIL import Image

try:
    from tools.import_quake2 import asset_name, build_model as build_md2_model, safe
    from tools.local_data import LOCAL_DATA
except ImportError:
    from import_quake2 import asset_name, build_model as build_md2_model, safe
    from local_data import LOCAL_DATA

IMAGE_SUFFIXES = ('.tga', '.png')
TEXTURE_SUFFIXES = IMAGE_SUFFIXES + ('.atd',)
MODEL_SUFFIXES = ('.md2', '.mda')


def adat_directory(stream):
    """Read the v9 directory, leaving payloads on disk; names are mount-relative."""
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    header = stream.read(16)
    if len(header) != 16:
        raise ValueError('Truncated ADAT header')
    magic, offset, length, version = struct.unpack('<4s3i', header)
    if magic != b'ADAT' or version != 9:
        raise ValueError('Expected ADAT version 9')
    if offset < 16 or length < 0 or length % 144 or offset + length != size:
        raise ValueError('Invalid ADAT directory range')
    stream.seek(offset)
    result = []
    for raw, start, length, compressed, unknown in struct.iter_unpack('<128s4i', stream.read(length)):
        name = asset_name(raw.split(b'\0')[0].decode('latin1'))
        stored = compressed or length
        if name in ('', '.', '..') or name.startswith(('/', '../')) or ':' in name:
            raise ValueError('Invalid ADAT member name')
        if min(length, compressed) < 0 or start < 16 or start + stored > offset:
            raise ValueError(f'Invalid ADAT payload range: {name}')
        result.append(dict(name=name, offset=start, length=length, stored=stored,
                           compressed=bool(compressed), unknown=unknown))
    return result


def read_entry(stream, entry):
    stream.seek(entry['offset'])
    raw = stream.read(entry['stored'])
    if len(raw) != entry['stored']:
        raise ValueError('Truncated ADAT payload')
    if entry['compressed']:
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(raw, entry['length'] + 1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError('Invalid ADAT zlib stream boundary')
        except zlib.error as exc:
            raise ValueError(f'Invalid ADAT zlib stream: {exc}') from exc
    if len(raw) != entry['length']:
        raise ValueError('ADAT decompressed length mismatch')
    return raw


def read_asset(entry):
    if 'member' in entry:
        try:
            with zipfile.ZipFile(entry['path']) as archive:
                return archive.read(entry['member'])
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise ValueError(f'Invalid patch ZIP: {exc}') from exc
    with entry['path'].open('rb') as stream:
        return read_entry(stream, entry) if 'offset' in entry else stream.read()


def read_install(install, *, maps=False):
    """Low to high priority: stem-mounted DATs, anox0..9.zip, loose anoxdata.

    Retail anox.exe 00440189..004401AF mounts in this order; anoxaux
    100035EC..10003611 inserts each mount at the front of the search list.
    """
    root = Path(install).expanduser()
    if (root / 'anoxdata').is_dir():
        root /= 'anoxdata'
    archives = sorted(root.glob('*.dat'), key=lambda p: p.name.lower()) if root.is_dir() else []
    if not archives:
        raise ValueError(f'No Anachronox DAT archives in {root}')
    files, records, counts = {}, [], {}

    def layer(entries, origin):
        counts[origin] = dict(directory=len(entries), extensions=dict(Counter(PurePosixPath(e['name']).suffix for e in entries)))
        for entry in entries:
            name = entry['name']
            row = dict(source=name, pak=origin, status='pending')
            if name in files:
                files[name]['record'].update(status='overridden', reason=f'Overridden by {origin}:{name}')
            entry.update(record=row, pak=origin)
            files[name] = entry
            if name.endswith(MODEL_SUFFIXES + (('.bsp',) if maps else ())):
                records.append(row)

    for path in archives:
        with path.open('rb') as stream:
            entries = adat_directory(stream)
        layer([dict(e, name=path.stem.lower() + '/' + e['name'], path=path) for e in entries], path.name.lower())
    patches = sorted((p for p in root.iterdir() if re.fullmatch(r'anox[0-9]\.zip', p.name.lower())),
                     key=lambda p: int(p.stem[-1]))
    for path in patches:
        try:
            with zipfile.ZipFile(path) as archive:
                layer([dict(name=asset_name(e.filename), member=e, path=path)
                       for e in archive.infolist() if not e.is_dir()], path.name.lower())
        except zipfile.BadZipFile as exc:
            raise ValueError(f'Invalid patch ZIP {path.name}: {exc}') from exc
    # Only metadata is read here, including loose map assets for later callers.
    layer([dict(name=asset_name(p.relative_to(root).as_posix()), path=p)
           for p in sorted(root.rglob('*')) if p.is_file()
           and not (p.parent == root and (p.suffix.lower() == '.dat' or re.fullmatch(r'anox[0-9]\.zip', p.name.lower())))], 'loose')
    return files, records, counts


def md2_layout(header, length):
    """Measure the layout independently of the version's precision bits."""
    if len(header) < 96 or header[:4] != b'IDP2':
        raise ValueError('Expected Anachronox IDP2 header')
    h = struct.unpack_from('<24i', header)
    if h[1] not in (14, 15, 0x1000e, 0x1000f, 0x2000f, 0x1000c):
        raise ValueError(f'Unsupported Anachronox IDP2 version {h[1]:#x}')
    if h[11] != 96 or min(h[2:11]) <= 0 or h[17] != h[5] or h[22] < 0:
        raise ValueError('Invalid Anachronox MD2 header or counts')
    frame_size, rem = divmod(h[15] - h[14], h[10])
    stride, vertex_rem = divmod(frame_size - 40, h[6])
    if rem or vertex_rem or stride not in (5, 6, 8) or frame_size != h[4]:
        raise ValueError('MD2 frame offsets/counts do not define a supported vertex stride')
    warnings = []
    # Thirty stock exporters count the final uint16 as one byte, with no tags.
    # Accept ONLY this exact boundary defect; never truncate the surface table.
    if h[16] != length:
        if h[16] + 1 == length == h[18] + 2 * h[17] and h[22] == h[23] == 0:
            warnings.append('Declared end is one byte short; complete final surface uint16 ends at file length')
        else:
            raise ValueError('MD2 end offset does not match file length')
    previous = h[11]
    sections = [(h[11], h[5] * 64), (h[12], h[7] * 4), (h[13], h[8] * 12),
                (h[14], h[10] * frame_size), (h[15], h[9] * 4), (h[18], h[17] * 2)]
    if h[22]:
        sections.append((h[23], h[22] * 12))
    for offset, size in sections:
        if offset != previous or offset + size > length:
            raise ValueError('Invalid MD2 section boundary')
        previous = offset + size
    if previous != length:
        raise ValueError('MD2 sections do not end at file length')
    return dict(version=h[1], header_size=h[11], frame_size=frame_size, vertex_stride=stride, warnings=warnings)


def read_md2(data):
    layout = md2_layout(data, len(data))
    h = struct.unpack_from('<24i', data)
    skins, verts, sts, tris, commands, frames = h[5:11]
    skin_at, st_at, tri_at, frame_at, cmd_at = h[11:16]
    names = [asset_name(data[i:i + 64].split(b'\0')[0].decode('latin1')) for i in range(skin_at, st_at, 64)]
    table = list(struct.iter_unpack('<6H', data[tri_at:frame_at]))
    if any(max(t[:3]) >= verts or max(t[3:]) >= sts for t in table):
        raise ValueError('Invalid MD2 triangle index')
    values = struct.unpack_from('<6f', data, frame_at)
    if not all(math.isfinite(v) for v in values) or min(values[:3]) < 0:
        raise ValueError('Invalid MD2 first-frame transform')
    points, normals = [], []
    stride = layout['vertex_stride']
    # The version's high bits pick the position encoding; a longer stride is padding after the normal
    # (sphere.md2, 0x1000C: packed position + normal + 2 bytes, measured against its frame scale).
    packed_xyz, size = (True, 4) if h[1] & 0x10000 else (False, 6) if h[1] & 0x20000 else (False, 3)
    if size + 2 > stride:
        raise ValueError('MD2 vertex stride too short for its version')
    for at in range(frame_at + 40, frame_at + h[4], stride):
        if packed_xyz:
            packed = struct.unpack_from('<I', data, at)[0]
            xyz = packed & 2047, (packed >> 11) & 1023, packed >> 21
        elif size == 6:
            xyz = struct.unpack_from('<3H', data, at)
        else:
            xyz = data[at:at + 3]
        points.append(tuple(xyz[k] * values[k] + values[k + 3] for k in range(3)))
        normals.append(struct.unpack_from('<H', data, at + size)[0])
    primitives = struct.unpack_from(f'<{h[17]}H', data, h[18])
    surfaces, at, end = [], cmd_at, cmd_at + commands * 4
    for index, count in enumerate(primitives):
        st, triangles = [], []
        for _ in range(count):
            if at + 4 > end:
                raise ValueError('Truncated MD2 surface command')
            n = struct.unpack_from('<i', data, at)[0]
            at += 4
            if abs(n) < 3 or at + abs(n) * 12 > end:
                raise ValueError('Invalid MD2 strip/fan length')
            corners = list(struct.iter_unpack('<ffi', data[at:at + abs(n) * 12]))
            at += abs(n) * 12
            if any(not 0 <= v < verts or not math.isfinite(s) or not math.isfinite(t) for s, t, v in corners):
                raise ValueError('Invalid MD2 command vertex/UV')
            base = len(st)
            # Cancel Q2 builder's texel-centre offset: these UVs are normalized.
            st.extend((s - .5, t - .5) for s, t, v in corners)
            for i in range(2, abs(n)):
                order = (0, i - 1, i) if n < 0 else ((i - 2, i - 1, i) if i % 2 == 0 else (i - 1, i - 2, i))
                triangles.append(tuple(corners[j][2] for j in order) + tuple(base + j for j in order))
        surfaces.append(dict(name=f'surface_{index}', primitives=count, triangles=triangles, st=st))
    if at + 4 != end or struct.unpack_from('<i', data, at)[0] != 0:
        raise ValueError('MD2 commands do not end in a single terminator')
    if sum(len(s['triangles']) for s in surfaces) != tris:
        raise ValueError('MD2 surface triangle counts do not sum to the header count')
    return dict(layout, points=points, frame_translate=values[3:], normal_indices=normals, skin_names=names, surfaces=surfaces,
                frames=frames, pose=data[frame_at + 24:frame_at + 40].split(b'\0')[0].decode('latin1'))


def read_mda(data):
    """Parse only the authored text before the opaque editor data; never execute it."""
    if not data.startswith(b'MDA1'):
        raise ValueError('Expected MDA1')
    text = re.split(r'do not modify|^[ \t]*[$&]', data.decode('latin1'), maxsplit=1,
                    flags=re.I | re.M)[0]
    text = re.sub(r'#[^\r\n]*|//[^\r\n]*', '', text)
    tokens = re.findall(r'"[^"\r\n]*"|[{}]|[^\s{}=]+', text[4:])
    tokens = [t.strip('"') for t in tokens]
    at = 0

    def block(nested=False, depth=0):
        nonlocal at
        if depth > 16:
            raise ValueError('MDA nesting too deep')
        out = []
        while at < len(tokens):
            token = tokens[at]
            at += 1
            if token == '}':
                if not nested:
                    raise ValueError('Unexpected MDA closing brace')
                return out
            out.append(block(True, depth + 1) if token == '{' else token)
        if nested:
            raise ValueError('Unclosed MDA block')
        return out

    tree = block()

    def value(nodes, key, default=''):
        return next((nodes[i + 1] for i, n in enumerate(nodes[:-1])
                     if isinstance(n, str) and n.lower() == key and isinstance(nodes[i + 1], str)), default)

    def blocks(nodes, key):
        found = []
        for i, n in enumerate(nodes[:-1]):
            if not isinstance(n, str) or n.lower() != key:
                continue
            if isinstance(nodes[i + 1], list):
                found.append(('', nodes[i + 1]))
            elif i + 2 < len(nodes) and isinstance(nodes[i + 2], list):
                found.append((nodes[i + 1], nodes[i + 2]))
        return found

    base = value(tree, 'basemodel')
    if not base or not base.lower().endswith('.md2'):
        raise ValueError('MDA has no MD2 basemodel')
    profiles = blocks(tree, 'profile')
    chosen = next(((tag, nodes) for tag, nodes in profiles if tag.upper() == 'DFLT'), None)
    chosen = chosen or next(((tag, nodes) for tag, nodes in profiles if not tag), None)
    warnings = []
    if chosen is None and profiles:
        chosen = next(((tag, nodes) for tag, nodes in profiles if not value(nodes, 'evaluate')), profiles[0])
        warnings.append('No DFLT/unnamed profile; using first unconditional profile, or first if all conditional')
    skins = []
    for _, nodes in blocks(chosen[1] if chosen else [], 'skin'):
        passes = []
        for _, fields in blocks(nodes, 'pass'):
            texture = value(fields, 'map') or value(fields, 'clampmap')
            passes.append(dict(map=asset_name(texture) if texture else '',
                               **{key: value(fields, key) for key in ('alphafunc', 'blendmode', 'cull', 'depthwrite', 'uvgen', 'uvmod', 'rgbgen', 'depthfunc')},
                               clamp=bool(value(fields, 'clampmap'))))
        skins.append(passes)
    if any(len(passes) > 1 for passes in skins):
        warnings.append('Static preview uses the first pass per surface; additional MDA passes are retained as metadata')
    return dict(basemodel=asset_name(base), profile=(chosen[0] or 'DFLT') if chosen else '',
                skins=skins, warnings=warnings)


def mda_index(files):
    by_model, results = defaultdict(list), []
    for name in sorted(n for n in files if n.endswith('.mda')):
        row = dict(source=name, pak=files[name]['pak'])
        try:
            mda = read_mda(read_asset(files[name]))
            mda['source'] = name
            by_model[mda['basemodel']].append(mda)
            row.update(status='resolved' if mda['basemodel'] in files else 'missing_base',
                       basemodel=mda['basemodel'], profile=mda['profile'], warnings=mda['warnings'])
            if row['status'] == 'missing_base':
                row['reason'] = 'MDA basemodel is absent from the layered game files'
        except (ValueError, OSError) as exc:
            row.update(status='skipped', reason=str(exc))
        results.append(row)
    return by_model, results


def choose_mda(name, by_model):
    candidates = by_model.get(name, [])
    # Same-stem companion wins over alternate authoring files (e.g. abbot/z.mda).
    return min(candidates, key=lambda m: (PurePosixPath(m['source']).stem != PurePosixPath(name).stem, m['source'])) if candidates else None


def skin_candidates(model, named, relative=False):
    if not named:
        return []
    named = asset_name(named)
    if relative and not named.startswith(('models/', 'textures/', 'graphics/')):
        named = asset_name(str(PurePosixPath(model).parent / named))
    base = str(PurePosixPath(named).with_suffix(''))
    # MD2 contains exporter BMP names; retail images use the same stem in TGA/PNG.
    candidates = ([named] if named.endswith(TEXTURE_SUFFIXES) else []) + [base + ext for ext in TEXTURE_SUFFIXES]
    return list(dict.fromkeys(candidates))


def resolve_skins(name, skin_names, files, mda=None):
    rows = []
    for index, original in enumerate(skin_names):
        passes = mda['skins'][index] if mda and index < len(mda['skins']) else []
        named = passes[0]['map'] if passes else ''
        tried, missing = [], []
        source, method = '', 'missing'
        for ref, how in ((named, 'mda'), (original, 'md2')):
            if not ref:
                continue
            candidates = skin_candidates(name, ref, relative=how == 'md2')
            tried.extend(candidates)
            source = next((candidate for candidate in candidates if candidate in files), '')
            if source:
                method = how
                break
            missing.append(ref)
        rows.append(dict(named=named or original, md2_name=original, source=source, method=method,
                         missing_references=missing, candidates=tried, passes=passes,
                         reason='' if source else 'No supplied TGA/PNG/ATD skin; missing-skin checker fallback'))
    return rows


def read_atd(data):
    """Initial animation image recipe; procedural ATDs are explicitly unsupported.

    Start at frame zero, applying initialization frames (negative wait) until the
    first timed frame. Images are still decoded/composited exclusively by Pillow.
    """
    if not data.startswith(b'ATD1'):
        raise ValueError('Expected ATD1')
    text = re.sub(r'#[^\r\n]*', '', data.decode('latin1'))
    sections = re.split(r'!\s*(bitmap|frame)\b', text, flags=re.I)

    def fields(text):
        return {k.lower(): v.strip().strip('"') for k, v in re.findall(r'(\w+)\s*=\s*([^\r\n]+)', text)}

    header = fields(sections[0])
    if header.get('type', '').lower() != 'animation':
        raise ValueError(f"Unsupported procedural ATD type: {header.get('type', 'unspecified')}")
    width, height = int(header.get('width', 1)), int(header.get('height', 1))
    if min(width, height) < 1 or max(width, height) > 16384 or width * height > 67108864:
        raise ValueError('Invalid ATD dimensions')
    bitmaps, frames = [], []
    for kind, text in zip(sections[1::2], sections[2::2]):
        (bitmaps if kind.lower() == 'bitmap' else frames).append(fields(text))
    draws, visited, index = [], set(), 0
    while index not in visited:
        if not 0 <= index < len(frames):
            raise ValueError('Invalid ATD frame index')
        visited.add(index)
        frame = frames[index]
        bitmap = int(frame.get('bitmap', -1))
        if not 0 <= bitmap < len(bitmaps) or not bitmaps[bitmap].get('file'):
            raise ValueError('Invalid ATD bitmap index/reference')
        draws.append(dict(source=asset_name(bitmaps[bitmap]['file']), x=int(frame.get('x', 0)), y=int(frame.get('y', 0))))
        wait = float(frame.get('wait', 0))
        if not math.isfinite(wait):
            raise ValueError('Invalid ATD wait')
        next_frame = int(frame.get('next', -1))
        if wait >= 0 or next_frame < 0:
            return dict(width=width, height=height, draws=draws)
        index = next_frame
    raise ValueError('ATD initialization cycle has no timed frame')


def read_texture(name, files):
    """Pillow decodes TGA/PNG and composites the initial ATD animation image."""
    if name.endswith('.atd'):
        recipe = read_atd(read_asset(files[name]))
        image = Image.new('RGBA', (recipe['width'], recipe['height']))
        for draw in recipe['draws']:
            source = next((n for n in skin_candidates(name, draw['source']) if n.endswith(IMAGE_SUFFIXES) and n in files), '')
            if not source:
                raise ValueError(f"Missing ATD bitmap: {draw['source']}")
            bitmap = read_texture(source, files)
            if min(draw['x'], draw['y']) < 0 or draw['x'] + bitmap.width > image.width or draw['y'] + bitmap.height > image.height:
                raise ValueError('ATD bitmap lies outside its canvas')
            # Native glTexSubImage2D copies rows at x/y without alpha blending.
            # Keep the same top-first image rows used by Skinner's flipY=false UVs.
            image.paste(bitmap, (draw['x'], draw['y']))
        return image
    if PurePosixPath(name).suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f'Unsupported Anachronox texture: {name}')
    try:
        with Image.open(io.BytesIO(read_asset(files[name]))) as image:
            image.load()
            return image.convert('RGBA')
    except (OSError, ValueError) as exc:
        raise ValueError(f'Invalid Anachronox image {name}: {exc}') from exc


def material_settings(passes):
    if not passes:
        return {}
    p = passes[0]
    result = {}
    if p['cull'] in ('disable', 'none'):
        result['cull'] = 'none'
    if p['alphafunc']:
        result['alphaFunc'] = p['alphafunc'].upper()
    blend = {'normal': ['gl_src_alpha', 'gl_one_minus_src_alpha'],
             'add': ['gl_one', 'gl_one'], 'multiply': ['gl_dst_color', 'gl_one_minus_src_alpha']}
    if p['blendmode'] in blend:
        result['blend'] = blend[p['blendmode']]
    if p['depthwrite']:
        result['depthWrite'] = p['depthwrite'] != '0'
    return result


def build_model(mesh, skins):
    result = dict(game='anachronox', winding='ccw', vertices=[], uvs=[], indices=[], groups=[],
                  material_names=[], material_textures=[], material_settings=[],
                  metadata={k: mesh[k] for k in ('version', 'header_size', 'frame_size', 'vertex_stride', 'frames', 'pose', 'skin_names', 'warnings')})
    for surface, skin in zip(mesh['surfaces'], skins):
        if not surface['triangles']:
            continue
        part = build_md2_model(dict(mesh, st=surface['st'], triangles=surface['triangles'], width=1, height=1), skin['texture'])
        base = len(result['vertices']) // 3
        result['groups'].append(dict(start=len(result['indices']), count=len(part['indices']), materialIndex=len(result['material_names'])))
        result['vertices'].extend(part['vertices'])
        result['uvs'].extend(part['uvs'])
        result['indices'].extend(i + base for i in part['indices'])
        result['material_names'].append(surface['name'])
        result['material_textures'].append(skin['texture'])
        result['material_settings'].append(material_settings(skin['passes']))
    result['metadata'].update(skins=skins, surface_triangles=[len(s['triangles']) for s in mesh['surfaces']])
    return result


def category(name):
    parts = name.split('/')
    folder = parts[1] if len(parts) > 2 and parts[0] == 'models' else parts[0]
    party = ('boots', 'pal', 'paco', 'rho', 'stiletto', 'grumpos', 'fatima', 'democratus')
    if folder in party or folder == 'newface' and len(parts) > 2 and parts[2] in party:
        return 'Party'
    return {'npcs': 'NPCs', 'newface': 'NPCs', 'monsters': 'Monsters', 'mystech': 'MysTech',
            'cine': 'Cinematic', 'interface': 'Interface'}.get(folder, 'Objects')


def import_catalog(install, output, model_filter=None):
    files, records, counts = read_install(install)
    by_model, mda_results = mda_index(files)
    output = Path(output)
    for sub in ('textures', 'model_json'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    catalog, seen, texture_records, resolution, decoded_resolution = [], {}, {}, Counter(), Counter()
    layouts = Counter()

    def texture(name):
        if not name:
            return 'missing_skin.png'
        if name not in texture_records:
            row = dict(source=name, pak=files[name]['pak'])
            texture_records[name] = row
            try:
                image = read_texture(name, files)
                png = safe(str(PurePosixPath(name).with_suffix(''))) + '_' + hashlib.sha256(name.encode()).hexdigest()[:8] + '.png'
                target = output / 'textures' / png
                if not target.is_file():
                    image.save(target)
                row.update(status='ready', texture=png)
            except (ValueError, OSError) as exc:
                row.update(status='skipped', reason=str(exc))
        return texture_records[name].get('texture', 'missing_skin.png')

    for row in sorted(records, key=lambda r: (not bool(by_model.get(r['source'])), r['source'], r['pak'])):
        if row['status'] != 'pending':
            continue
        name = row['source']
        if name.endswith('.mda'):
            info = next(r for r in mda_results if r['source'] == name)
            row.update(info)
            continue
        if model_filter and model_filter.lower() not in name:
            row.update(status='skipped', reason='Excluded by requested model filter')
            continue
        model = safe(str(PurePosixPath(name).with_suffix(''))) + '_' + hashlib.sha256(name.encode()).hexdigest()[:8]
        item = dict(model_name=model, display_name=PurePosixPath(name).stem, game='anachronox',
                    category=category(name), texture_name='', status='ready', source=f"{row['pak']}:{name}")
        row['model'] = model
        try:
            raw = read_asset(files[name])
            digest = hashlib.sha256(raw).hexdigest()
            mesh = read_md2(raw)
            layouts[(mesh['version'], mesh['vertex_stride'])] += 1
            mda = choose_mda(name, by_model)
            skins = resolve_skins(name, mesh['skin_names'], files, mda)
            method = 'mda' if any(s['method'] == 'mda' for s in skins) else 'md2' if any(s['method'] == 'md2' for s in skins) else 'missing'
            resolution[method] += 1
            row.update(version=mesh['version'], vertex_stride=mesh['vertex_stride'], skins=skins,
                       skin_resolution=method, mda=mda['source'] if mda else '',
                       mda_candidates=[m['source'] for m in by_model.get(name, [])])
            if digest in seen:
                row.update(status='duplicate', model=seen[digest], reason='Byte-identical MD2; listed once')
                continue
            for skin in skins:
                skin['texture'] = texture(skin['source'])
                if skin['source'] and skin['texture'] == 'missing_skin.png':
                    skin['reason'] = texture_records[skin['source']]['reason'] + '; checker fallback'
                    if skin['method'] == 'mda':
                        fallback = resolve_skins(name, [skin['md2_name']], files)[0]
                        if fallback['source'] and fallback['source'] != skin['source']:
                            png = texture(fallback['source'])
                            if png != 'missing_skin.png':
                                skin.update(source=fallback['source'], texture=png, method='md2',
                                            reason=skin['reason'].replace('; checker fallback', '; MD2 skin fallback'))
            data = build_model(mesh, skins)
            methods = {s['method'] for s in skins if s['texture'] != 'missing_skin.png'}
            row['skin_resolution'] = 'mda' if 'mda' in methods else 'md2' if 'md2' in methods else 'missing'
            decoded_resolution[row['skin_resolution']] += 1
            if mda:
                data['metadata']['mda'] = mda
                data['metadata']['warnings'] = mesh['warnings'] + mda['warnings']
                if mda['skins'] and len(mda['skins']) != len(skins):
                    data['metadata']['warnings'].append('MDA skin count differs from MD2 surfaces; unmatched surfaces use MD2 skins')
            data['metadata']['source'] = item['source']
            if any(s['texture'] == 'missing_skin.png' for s in skins):
                data['metadata']['warnings'].append('One or more surfaces use a missing-skin checker; see skins and texture report')
            if 'missing_skin.png' in data['material_textures']:
                target = output / 'textures' / 'missing_skin.png'
                if not target.is_file():
                    checker = Image.new('RGB', (2, 2))
                    checker.putdata([(255, 0, 255), (32, 32, 32), (32, 32, 32), (255, 0, 255)])
                    checker.save(target)
            (output / 'model_json' / (model + '.json')).write_text(json.dumps(data, separators=(',', ':'), allow_nan=False), encoding='utf-8')
            item.update(texture_name=data['material_textures'][0], warnings=data['metadata']['warnings'])
            row.update(status='ready', triangles=len(data['indices']) // 3, warnings=data['metadata']['warnings'])
            seen[digest] = model
        except (ValueError, OSError) as exc:
            row.update(status='skipped', reason=str(exc))
            item.update(status='skipped', reason=str(exc))
        catalog.append(item)
    # Include alternate model skins, plus external textures actually selected above.
    for name in sorted(files):
        if name.startswith('models/') and name.endswith(TEXTURE_SUFFIXES) and (not model_filter or model_filter.lower() in name):
            texture(name)
    models = [r for r in records if r['source'].endswith('.md2')]
    report = dict(entries=len(catalog), ready=sum(r['status'] == 'ready' for r in models),
                  model_entries=len(models), md2_skipped=sum(r['status'] == 'skipped' for r in models),
                  duplicates=sum(r['status'] == 'duplicate' for r in models),
                  overridden=sum(r['status'] == 'overridden' for r in models),
                  textures=sum(r['status'] == 'ready' for r in texture_records.values()),
                  skin_reference_resolution=dict(resolution), skin_resolution=dict(decoded_resolution),
                  version_strides=[dict(version=v, stride=s, count=n) for (v, s), n in sorted(layouts.items())],
                  missing_skin_references=sorted({ref for r in models for s in r.get('skins', []) for ref in s['missing_references']}),
                  archive_counts=counts, search_order=list(counts), results=records,
                  mda_results=mda_results, texture_results=list(texture_records.values()))
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--filter', help='Case-insensitive model/texture path substring')
    args = parser.parse_args()
    print(json.dumps(import_catalog(args.install, args.output or LOCAL_DATA / 'anachronox', args.filter), indent=1))

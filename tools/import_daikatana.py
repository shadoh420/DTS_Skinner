"""Daikatana first-pose DKM models, as game daikatana.

python tools/import_daikatana.py --install "C:/Program Files (x86)/Steam/steamapps/common/Daikatana/data"

Optional --output and --filter (case-insensitive path substring) support bounded imports.
Numbered pak0..pak9 load ascending, then loose files, matching the retail engine.
Skin lookup follows ref_gl: TGA directly; otherwise .wal, then the original
path. Every substitution, missing skin and palette is reported.
Existing PNG edits survive. Animation ranges are metadata; exports are static.
"""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct

from PIL import Image

try:
    from tools.import_quake2 import asset_name, build_model as build_md2_model, read_pcx, safe
    from tools.import_quake2_map import read_wal as read_q2_wal
    from tools.local_data import LOCAL_DATA
except ImportError:
    from import_quake2 import asset_name, build_model as build_md2_model, read_pcx, safe
    from import_quake2_map import read_wal as read_q2_wal
    from local_data import LOCAL_DATA

MODEL_SUFFIXES = ('.dkm', '.dkm2', '.dkn')
IMAGE_SUFFIXES = ('.wal', '.tga', '.bmp', '.pcx')


def decompress(data, length):
    """Retail PACK bytecode; overlapping back references are intentional."""
    if length < 0:
        raise ValueError('Negative decompressed length')
    out, at = bytearray(), 0
    while at < len(data):
        code = data[at]
        at += 1
        if code == 255:
            break
        count = code + 1 if code < 64 else code - (62 if code < 128 else 126 if code < 192 else 190)
        needed = count if code < 64 else 0 if code < 128 else 1
        if at + needed > len(data) or len(out) + count > length:
            raise ValueError('Invalid compressed PACK run')
        if code < 64:
            out.extend(data[at:at + count])
        elif code < 128:
            out.extend(bytes(count))
        elif code < 192:
            out.extend(bytes([data[at]]) * count)
        else:
            back = data[at] + 2
            if back > len(out):
                raise ValueError('Invalid PACK back reference')
            for _ in range(count):
                out.append(out[-back])
        at += needed
    if len(out) != length or at != len(data):
        raise ValueError('PACK decompressed length or terminator mismatch')
    return bytes(out)


def pak_directory(stream):
    """Read only the directory; payloads remain on disk until requested."""
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    header = stream.read(12)
    if len(header) != 12 or header[:4] != b'PACK':
        raise ValueError('Expected Daikatana PACK header')
    offset, length = struct.unpack_from('<ii', header, 4)
    if offset < 12 or length < 0 or length % 72 or offset + length > size:
        raise ValueError('Invalid Daikatana PACK directory')
    stream.seek(offset)
    result = []
    for raw, start, unpacked, packed, flag in struct.iter_unpack('<56s4i', stream.read(length)):
        name = asset_name(raw.split(b'\0')[0].decode('latin1'))
        stored = packed if flag else unpacked
        if flag not in (0, 1) or min(unpacked, packed) < 0 or start < 12 or start + stored > offset:
            raise ValueError(f'Invalid PACK entry: {name}')
        result.append(dict(name=name, offset=start, length=unpacked, stored=stored, compressed=bool(flag)))
    return result


def read_entry(stream, entry):
    stream.seek(entry['offset'])
    data = stream.read(entry['stored'])
    if len(data) != entry['stored']:
        raise ValueError('Truncated PACK payload')
    return decompress(data, entry['length']) if entry['compressed'] else data


def read_pak(data):
    stream = io.BytesIO(data)
    return [(e['name'], read_entry(stream, e)) for e in pak_directory(stream)]


def read_asset(entry):
    with entry['path'].open('rb') as stream:
        return read_entry(stream, entry) if 'offset' in entry else stream.read()


def read_install(install, maps=False):
    root = Path(install).expanduser()
    if (root / 'data').is_dir():
        root /= 'data'
    paks = sorted((p for p in root.iterdir() if re.fullmatch(r'pak[0-9]\.pak', p.name.lower())) if root.is_dir() else [],
                  key=lambda p: int(p.stem[3:]))
    if not paks:
        raise ValueError(f'No Daikatana numbered paks in {root}')
    files, records, counts = {}, [], {}

    def layer(entries, origin):
        counts[origin] = dict(directory=len(entries), extensions=dict(Counter(PurePosixPath(e['name']).suffix for e in entries)))
        for entry in entries:
            name = entry['name']
            row = dict(pak=origin, source=name, status='pending')
            if name in files:
                identical = name.endswith(MODEL_SUFFIXES) and read_asset(files[name]) == read_asset(entry)
                files[name]['record'].update(status='duplicate' if identical else 'overridden',
                                            reason=f'{"Byte-identical to" if identical else "Overridden by"} {origin}:{name}')
            entry.update(record=row, pak=origin)
            files[name] = entry
            if name.startswith('models/') or name.endswith(MODEL_SUFFIXES + ('.sp2',)) or (maps and name.endswith('.bsp')):
                records.append(row)

    for pak in paks:
        with pak.open('rb') as stream:
            entries = pak_directory(stream)
        layer([dict(e, path=pak) for e in entries], pak.name.lower())
    loose = sorted(p for folder in ('models', 'skins', 'pics', 'textures') + (('maps', 'env') if maps else ())
                   for p in (root / folder).rglob('*') if p.is_file()
                   and (folder == 'models' or p.suffix.lower() in IMAGE_SUFFIXES + MODEL_SUFFIXES + ('.pal', '.sp2') + (('.bsp',) if maps else ())))
    if maps:
        loose += sorted(p for p in root.iterdir() if p.is_file() and p.name.lower() in ('aidata.vsc', 'aidata.cs2', 'aidata.csv'))
    layer([dict(name=asset_name(p.relative_to(root).as_posix()), path=p) for p in loose], 'loose')
    return files, records, counts


def read_dkm(data, frame_number=0):
    if len(data) < 80 or data[:4] != b'DKMD':
        raise ValueError('Expected DKMD header')
    h = struct.unpack_from('<20i', data)
    version = h[1]
    origin = struct.unpack_from('<3f', data, 8)
    frame_size, skins, verts, sts, tris, commands, frames, surfaces = h[5:13]
    skin_at, st_at, tri_at, frame_at, cmd_at, surface_at, end = h[13:20]
    if version not in (1, 2):
        raise ValueError(f'Unsupported DKM version {version}')
    stride = 4 if version == 1 else 5
    if min(verts, sts, tris, frames, surfaces) <= 0 or min(skins, commands) < 0 or frame_size < 40 + verts * stride:
        raise ValueError('Invalid DKM counts or frame size')
    if not 0 <= frame_number < frames:
        frame_number = 0  # Retail alias renderer falls back to frame zero.
    if not all(math.isfinite(v) for v in origin) or end > len(data) or end < 80:
        raise ValueError('Invalid DKM origin or end')
    # The older .dkm2 has an 80-byte header and no sequence table.
    sequence_count, sequence_at = (0, end)
    if skin_at >= 88:
        if len(data) < 88:
            raise ValueError('Truncated DKM sequence header')
        sequence_count, sequence_at = struct.unpack_from('<2i', data, 80)
    if sequence_count < 0:
        raise ValueError('Invalid DKM sequence count')
    if sequence_count == 0 and sequence_at == 0:
        sequence_at = end
    previous = 88 if skin_at >= 88 else 80
    for offset, size in ((skin_at, skins * 64), (st_at, sts * 4), (tri_at, tris * 16),
                         (frame_at, frames * frame_size), (cmd_at, commands * 4),
                         (surface_at, surfaces * 52), (sequence_at, sequence_count * 24)):
        if offset < previous or offset + size > end:
            raise ValueError('Invalid DKM section range')
        previous = offset + size
    names = [asset_name(data[i:i + 64].split(b'\0')[0].decode('latin1')) for i in range(skin_at, skin_at + skins * 64, 64)]
    surface_rows = []
    for name, flags, skin, width, height, reserved in struct.iter_unpack('<32s5i', data[surface_at:surface_at + surfaces * 52]):
        if not commands and (skin < 0 or skin >= max(1, skins) or min(width, height) <= 0):
            raise ValueError('Invalid DKM surface skin or dimensions')
        surface_rows.append(dict(name=name.split(b'\0')[0].decode('latin1'), flags=flags, skin=skin,
                                 width=width, height=height, reserved=reserved))
    triangles, at = [], tri_at
    for _ in range(tris):
        if at + 16 > frame_at:
            raise ValueError('Truncated DKM triangle')
        surface, uv_frames, a, b, c = struct.unpack_from('<5H', data, at)
        size = 10 + 6 * uv_frames
        if uv_frames == 0 or at + size > frame_at:
            raise ValueError('Invalid DKM triangle UV frames')
        uv_indices = struct.unpack_from(f'<{uv_frames * 3}H', data, at + 10)
        if not commands and (surface >= surfaces or max(a, b, c) >= verts or max(uv_indices) >= sts):
            raise ValueError('Invalid DKM triangle index')
        triangles.append((surface, uv_frames, a, b, c, *uv_indices[:3]))
        at += size
    # ref_gl draws these strips/fans, not the triangle table or surface skin
    # fields. Retail contains stale indices in both of those unused tables.
    draws, at, command_end, undefined_uvs = [], cmd_at, cmd_at + commands * 4, 0
    terminated = False
    while at < command_end:
        count = struct.unpack_from('<i', data, at)[0]
        at += 4
        if count == 0:
            terminated = True
            break
        if abs(count) < 3 or at + 8 + abs(count) * 12 > command_end:
            raise ValueError('Invalid DKM GL command range')
        skin, surface = struct.unpack_from('<2i', data, at)
        at += 8
        if not 0 <= skin < max(1, skins) or not 0 <= surface < surfaces:
            raise ValueError('Invalid DKM GL command skin or surface')
        corners = list(struct.iter_unpack('<iff', data[at:at + abs(count) * 12]))
        at += abs(count) * 12
        if any(not 0 <= v < verts for v, s, t in corners):
            raise ValueError('Invalid DKM GL command vertex')
        invalid = sum(not math.isfinite(s) or not math.isfinite(t) for v, s, t in corners)
        undefined_uvs += invalid
        draws.append(dict(count=count, skin=skin, surface=surface, corners=corners, undefined_uvs=invalid))
    if commands and (not terminated or not draws):
        raise ValueError('Empty or unterminated DKM GL command stream')
    points, normal_indices, pose = [], [], ''
    for frame in range(frames):
        at = frame_at + frame * frame_size
        transform = struct.unpack_from('<6f', data, at)
        if not all(math.isfinite(v) for v in transform) or min(transform[:3]) < 0:
            raise ValueError('Invalid DKM frame scale/translate')
        if frame != frame_number:
            continue
        pose = data[at + 24:at + 40].split(b'\0')[0].decode('latin1')
        for i in range(verts):
            start = at + 40 + i * stride
            normal_indices.append(data[start + stride - 1])
            if version == 1:
                xyz = data[start:start + 3]
            else:
                packed = struct.unpack_from('<I', data, start)[0]
                xyz = packed >> 21, (packed >> 11) & 1023, packed & 2047
            # Frame translation already includes the authoring origin. Retail ref_gl
            # uses scale * xyz + translate, without adding the header origin again.
            points.append(tuple(xyz[k] * transform[k] + transform[k + 3] for k in range(3)))
    sequences = []
    for name, first, last in struct.iter_unpack('<16s2i', data[sequence_at:sequence_at + sequence_count * 24]):
        sequences.append(dict(name=name.split(b'\0')[0].decode('latin1'), first=first, last=last))
    return dict(points=points, normal_indices=normal_indices, st=list(struct.iter_unpack('<2h', data[st_at:st_at + sts * 4])),
                triangles=triangles, surfaces=surface_rows, skin_names=names, version=version,
                frames=frames, pose=pose, origin=origin, sequences=sequences,
                triangle_uv_frames=sorted(set(t[1] for t in triangles)), draws=draws,
                geometry_source='GL commands' if commands else 'triangle table',
                undefined_uvs=undefined_uvs)


def build_model(mesh, textures, include_normals=False, visible_only=False):
    result = dict(game='daikatana', winding='ccw', vertices=[], uvs=[], indices=[], groups=[],
                  material_names=[], material_textures=[], material_settings=[],
                  metadata={k: mesh[k] for k in ('version', 'pose', 'frames', 'origin', 'skin_names', 'surfaces', 'sequences', 'triangle_uv_frames', 'geometry_source', 'undefined_uvs')})
    parts = {}
    for draw in mesh['draws']:
        key = draw['surface'], draw['skin'], bool(draw['undefined_uvs'])
        part = parts.setdefault(key, dict(st=[], triangles=[], width=1, height=1))
        base = len(part['st'])
        # Reuse Q2's coordinate/winding conversion, cancelling its texel-centre
        # offset because the command stream already stores normalized UVs.
        part['st'].extend((s - .5, t - .5) if math.isfinite(s) and math.isfinite(t) else (0, 0)
                          for v, s, t in draw['corners'])
        for i in range(2, abs(draw['count'])):
            order = (0, i - 1, i) if draw['count'] < 0 else ((i - 2, i - 1, i) if i % 2 == 0 else (i - 1, i - 2, i))
            part['triangles'].append(tuple(draw['corners'][j][0] for j in order) + tuple(base + j for j in order))
    if not mesh['draws']:
        for index, surface in enumerate(mesh['surfaces']):
            parts[index, surface['skin'], False] = dict(st=mesh['st'], width=surface['width'], height=surface['height'],
                                                       triangles=[t[2:] for t in mesh['triangles'] if t[0] == index])
    for (index, skin, undefined), part_mesh in parts.items():
        surface = mesh['surfaces'][index]
        if visible_only and surface['flags'] & 1:
            continue
        triangles = part_mesh['triangles']
        if not triangles:
            continue
        texture = textures[skin] if textures and not undefined else 'missing_skin.png'
        part = build_md2_model(dict(mesh, **part_mesh), texture)
        if include_normals:
            corners = dict.fromkeys((t[c], t[c + 3]) for t in triangles for c in (0, 2, 1))
            result.setdefault('normal_indices', []).extend(mesh['normal_indices'][v] for v, uv in corners)
        vertex_base = len(result['vertices']) // 3
        result['groups'].append(dict(start=len(result['indices']), count=len(part['indices']), materialIndex=len(result['material_names'])))
        result['vertices'].extend(part['vertices'])
        result['uvs'].extend(part['uvs'])
        result['indices'].extend(i + vertex_base for i in part['indices'])
        result['material_names'].append(surface['name'])
        result['material_textures'].append(texture)
        result['material_settings'].append({})
    if mesh['undefined_uvs']:
        result['metadata']['warnings'] = [f"{mesh['undefined_uvs']} GL command vertices have non-finite UVs; "
                                          'native passes them to GL with undefined sampling. Geometry retained; '
                                          'affected draws use a checker and finite placeholder UVs.']
    return result


def read_wal(data, palette=None):
    """Decode v3 local-palette WAL, v2 shared-palette WAL, or Quake II WAL."""
    if not data:
        raise ValueError('Empty WAL')
    version = data[0] if data[0] in (2, 3) else 0
    if not version:
        texture = read_q2_wal(data)
        offsets = struct.unpack_from('<4I', data, 40)
        if offsets[-1] + (texture['width'] >> 3) * (texture['height'] >> 3) != len(data):
            raise ValueError('WAL mip chain does not end at file length')
    else:
        minimum = 892 if version == 3 else 124
        if len(data) < minimum:
            raise ValueError('Truncated Daikatana WAL header')
        width, height = struct.unpack_from('<2I', data, 36)
        offsets = struct.unpack_from('<9I', data, 44)
        if min(width, height) == 0 or offsets[0] != minimum:
            raise ValueError('Invalid Daikatana WAL dimensions or first mip')
        # ref_gl 100082D0 reads only mip0 and regenerates GPU mips. Lower
        # stored levels can have clamped dimensions and irregular tails.
        if offsets[0] + width * height > len(data):
            raise ValueError('Truncated Daikatana WAL first mip')
        texture = dict(name=data[1:33].split(b'\0')[0].decode('latin1'), width=width, height=height,
                       pixels=data[minimum:minimum + width * height],
                       flags=struct.unpack_from('<I', data, 112)[0], contents=struct.unpack_from('<I', data, 116)[0],
                       animation=data[80:112].split(b'\0')[0].decode('latin1'))
        if version == 3:
            # Native palette conversion receives raw + 0x78, not mip0 - 768.
            palette = data[120:888]
    if palette is None or len(palette) != 768:
        raise ValueError('WAL needs a 256-colour palette')
    image = Image.frombytes('P', (texture['width'], texture['height']), texture['pixels'])
    image.putpalette(palette)
    return image.convert('RGB')


def skin_candidates(named):
    """ref_gl 10008710: TGA directly; otherwise strip at first dot, try WAL.

    Its second load receives the original path, not the unused BMP buffer.
    Directory components are never relocated, even for extensionless names.
    """
    named = asset_name(named) if named else ''
    if not named:
        return []
    candidates = [named] if named.endswith('.tga') else [named.split('.', 1)[0] + '.wal', named]
    return list(dict.fromkeys(c for c in candidates if PurePosixPath(c).suffix in IMAGE_SUFFIXES))


def choose_skin(model, named, files):
    named = asset_name(named) if named else ''
    for candidate in skin_candidates(named):
        if candidate in files:
            return candidate, '' if candidate == named else f'Renderer WAL substitution: {named} -> {candidate}'
    return '', f'No renderer skin candidate for {named or "unnamed skin"}; missing-skin checker fallback'


def read_texture(name, files):
    raw = read_asset(files[name])
    suffix = PurePosixPath(name).suffix
    if suffix == '.pcx':
        return read_pcx(raw), f'{name}:embedded PCX palette'
    if suffix == '.wal':
        if raw and raw[0] == 3:
            return read_wal(raw), f'{name}:embedded WAL v3 palette'
        local = str(PurePosixPath(name).parent / 'colormap.bmp')
        palette_name = local if name.startswith('textures/') and local in files else 'pics/colormap.bmp'
        if palette_name not in files:
            raise ValueError(f'Missing WAL palette {palette_name}')
        with Image.open(io.BytesIO(read_asset(files[palette_name]))) as source:
            palette = source.getpalette()
        return read_wal(raw, bytes(palette or [])), palette_name
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            palette = f'{name}:embedded palette' if image.mode == 'P' else f'{name}:truecolour'
            return image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB'), palette
    except (OSError, ValueError) as exc:
        raise ValueError(f'Invalid {suffix} image: {exc}') from exc


def decorations(files):
    result = {}
    for name, entry in files.items():
        if name.endswith('decoinfo.csv'):
            for row in csv.reader(io.StringIO(read_asset(entry).decode('latin1'))):
                if len(row) >= 13 and not row[0].lstrip().startswith(';'):
                    result[asset_name(row[1].strip())] = dict(name=row[0].strip(), fields=[s.strip() for s in row[2:]])
    return result


def category(name, deco):
    parts = name.split('/')
    folder = parts[1] if parts[0] == 'models' and len(parts) > 2 else 'global'
    if folder in ('characters', 'cinematic', 'interface'):
        return folder.title()
    prefix = f'Episode {folder[1]}' if re.fullmatch('e[1-4]', folder) else 'Global'
    stem = PurePosixPath(name).stem
    kind = ('Decorations' if name in deco or re.match(r'd[1-4]?_', stem) else
            'Monsters' if stem.startswith(('m_', 'zm_')) else
            'Weapons' if stem.startswith(('w_', 'wa_', 'ws_')) else
            'Items' if re.match(r'a[1-4]?_', stem) else
            'Cinematic' if stem.startswith(('c_', 'cin_')) else
            'Effects' if stem.startswith(('e_', 'me_', 'we_')) else 'Objects')
    return prefix + '/' + kind


def import_catalog(install, output, model_filter=None):
    files, records, counts = read_install(install)
    output = Path(output)
    for sub in ('textures', 'model_json'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    deco = decorations(files)
    catalog, seen, taken, texture_records = [], {}, set(), {}

    def texture(name):
        if name in texture_records:
            return texture_records[name].get('texture', 'missing_skin.png')
        row = dict(source=name, pak=files[name]['pak'])
        texture_records[name] = row
        try:
            image, palette = read_texture(name, files)
            png = safe(str(PurePosixPath(name).with_suffix(''))) + '_' + hashlib.sha256(name.encode()).hexdigest()[:8] + '.png'
            target = output / 'textures' / png
            if not target.is_file():
                image.save(target)
            row.update(status='ready', texture=png, palette=palette)
            return png
        except (ValueError, OSError) as exc:
            row.update(status='skipped', reason=str(exc))
            return 'missing_skin.png'

    for row in records:
        if row['status'] != 'pending':
            continue
        name = row['source']
        if not name.endswith(MODEL_SUFFIXES):
            reason = ('Sprite (.sp2), not a polygon model' if name.endswith('.sp2') else
                      'Image asset; included when referenced by a model' if name.endswith(IMAGE_SUFFIXES) else
                      'Decoration metadata, retained for model names' if name.endswith('decoinfo.csv') else
                      'Auxiliary authoring/directory metadata, not a polygon model')
            row.update(status='skipped', reason=reason)
            continue
        if model_filter and model_filter.lower() not in name:
            row.update(status='skipped', reason='Excluded by requested model filter')
            continue
        model = safe(str(PurePosixPath(name).with_suffix('')))
        if model in taken:
            model += '_' + hashlib.sha256(name.encode()).hexdigest()[:12]
        taken.add(model)
        row['model'] = model
        item = dict(model_name=model, display_name=deco.get(name, {}).get('name', PurePosixPath(name).stem),
                    texture_name='', game='daikatana', category=category(name, deco), status='ready', source=f"{row['pak']}:{name}")
        try:
            raw = read_asset(files[name])
            digest = hashlib.sha256(raw).hexdigest()
            mesh = read_dkm(raw)
            skins, textures = [], []
            for named in mesh['skin_names']:
                skin, reason = choose_skin(name, named, files)
                png, failures = 'missing_skin.png', []
                for candidate in skin_candidates(named):
                    if candidate not in files:
                        continue
                    png = texture(candidate)
                    if png != 'missing_skin.png':
                        skin = candidate
                        reason = '' if skin == named else f'Renderer WAL substitution: {named} -> {skin}'
                        break
                    failures.append(f'Invalid {candidate}: {texture_records[candidate]["reason"]}')
                if failures:
                    reason = '; '.join(failures + [reason if png != 'missing_skin.png' else 'missing-skin checker fallback'])
                skins.append(dict(named=named, source=skin, reason=reason, texture=png))
                textures.append(png)
                # Keep other supplied formats of the named skin in the library too.
                if skin:
                    for ext in IMAGE_SUFFIXES:
                        alternate = str(PurePosixPath(skin).with_suffix(ext))
                        if alternate in files:
                            texture(alternate)
            row.update(version=mesh['version'], skins=skins)
            if digest in seen:
                row.update(status='duplicate', model=seen[digest], reason='Byte-identical DKM; listed once')
                continue
            data = build_model(mesh, textures)
            row.update(geometry_source=mesh['geometry_source'], triangles=len(data['indices']) // 3,
                       warnings=data['metadata'].get('warnings', []))
            data['metadata'].update(source=item['source'], skins=skins)
            if name in deco:
                data['metadata']['decoration'] = deco[name]
            if 'missing_skin.png' in data['material_textures']:
                target = output / 'textures' / 'missing_skin.png'
                if not target.is_file():
                    checker = Image.new('RGB', (2, 2))
                    checker.putdata([(255, 0, 255), (32, 32, 32), (32, 32, 32), (255, 0, 255)])
                    checker.save(target)
            (output / 'model_json' / (model + '.json')).write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
            item['texture_name'] = data['material_textures'][0]
            row['status'] = 'ready'
            seen[digest] = model
        except (ValueError, OSError) as exc:
            row.update(status='skipped', reason=str(exc))
            item.update(status='skipped', reason=str(exc))
        catalog.append(item)
    models = [r for r in records if r['source'].endswith(MODEL_SUFFIXES)]
    report = dict(entries=len(catalog), ready=sum(r['status'] == 'ready' for r in models),
                  dkm_entries=sum(r['source'].endswith('.dkm') for r in models), model_entries=len(models),
                  dkm_skipped=sum(r['source'].endswith('.dkm') and r['status'] == 'skipped' for r in models),
                  duplicates=sum(r['status'] == 'duplicate' for r in models),
                  overridden=sum(r['status'] == 'overridden' for r in models),
                  textures=sum(r['status'] == 'ready' for r in texture_records.values()),
                  pak_counts=counts, results=records, texture_results=list(texture_records.values()),
                  search_order=list(counts), skin_lookup='TGA directly; otherwise same path through first dot + .wal, then original path')
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--filter')
    args = parser.parse_args()
    print(json.dumps(import_catalog(args.install, args.output or LOCAL_DATA / 'daikatana', args.filter), indent=1))

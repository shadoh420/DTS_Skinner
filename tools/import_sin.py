"""SiN, Wages of Sin and CTF static models, as game sin.

python tools/import_sin.py --install "C:/Program Files (x86)/Steam/steamapps/common/SiN 1"

SPAK payloads stay on disk; bounded asset reads and SWL decoding are reusable by
map importers. Each expansion independently overlays base. Existing PNG edits
survive. SBM has no positions: previews use frame zero of an idle/first SAM.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct

from PIL import Image

try:
    from tools.import_quake2 import asset_name, build_model as build_md2_model, safe
    from tools.local_data import LOCAL_DATA
except ImportError:
    from import_quake2 import asset_name, build_model as build_md2_model, safe
    from local_data import LOCAL_DATA

GAMES = {'sin': 'SiN', '2015': 'Wages of Sin', 'ctf': 'CTF'}
TEXTURE_SUFFIXES = ('.tga', '.swl')
MAX_ASSET_BYTES = 256 * 1024 * 1024


def checked_name(name):
    """Archive/DEF references are keys, never paths to extract or open."""
    if not name or '\0' in name or ':' in name or name.replace('\\', '/').startswith('/'):
        raise ValueError('Invalid SiN asset name')
    if '..' in name.replace('\\', '/').split('/'):
        raise ValueError('Parent traversal in SiN asset name')
    name = asset_name(name)
    if name == '.':
        raise ValueError('Empty SiN asset name')
    return name


def spak_directory(stream):
    """SPAK: 12-byte header; name[120], signed offset/length directory rows.

    sin.exe 00454200..00454410; no compression. Validate all ranges without
    reading payloads. Duplicate names remain in directory order (first wins).
    """
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    header = stream.read(12)
    if len(header) != 12:
        raise ValueError('Truncated SPAK header')
    magic, offset, length = struct.unpack('<4sii', header)
    if magic != b'SPAK':
        raise ValueError('Expected SPAK magic')
    if offset < 12 or length < 0 or length % 128 or length > 128 * 131072 or offset + length > size:
        raise ValueError('Invalid SPAK directory range')
    stream.seek(offset)
    result = []
    for _ in range(length // 128):
        raw = stream.read(128)
        if len(raw) != 128:
            raise ValueError('Truncated SPAK directory')
        raw_name, start, stored = struct.unpack('<120sii', raw)
        name = checked_name(raw_name.split(b'\0')[0].decode('latin1'))
        if start < 12 or stored < 0 or start + stored > size or (start < offset + length and start + stored > offset):
            raise ValueError(f'Invalid SPAK payload range: {name}')
        result.append(dict(name=name, offset=start, length=stored))
    return result


def read_entry(stream, entry, *, max_bytes=MAX_ASSET_BYTES):
    start, length = entry['offset'], entry['length']
    if start < 0 or length < 0 or length > max_bytes:
        raise ValueError('SiN asset exceeds bounded read range')
    stream.seek(0, 2)
    if start + length > stream.tell():
        raise ValueError('Truncated SiN asset range')
    stream.seek(start)
    raw = stream.read(length)
    if len(raw) != length:
        raise ValueError('Truncated SiN asset')
    return raw


def read_asset(entry, *, max_bytes=MAX_ASSET_BYTES):
    with entry['path'].open('rb') as stream:
        if 'offset' in entry:
            return read_entry(stream, entry, max_bytes=max_bytes)
        stream.seek(0, 2)
        return read_entry(stream, dict(offset=0, length=stream.tell()), max_bytes=max_bytes)


def base_folder(install):
    root = Path(install).expanduser()
    return root / 'base' if (root / 'base').is_dir() else root


def available_games(install):
    root = base_folder(install)
    return ['sin'] + [g for g in GAMES if g != 'sin' and (root.parent / g / 'pak0.sin').is_file()]


def read_install(install, maps=False, game='sin', base=None):
    """Return lazy layered assets and this section's own model/map inventory.

    Low to high: base pak0..31, base loose, game pak0..31, game loose.
    sin.exe 00454420..004545EE prepends numbered packs then loose directory;
    00453B60 walks from the head. Init mounts base at 00455D71, then calls
    00454C40 for game; 00454EF2/00454F0D mount it through 004545F0.
    """
    if game not in GAMES:
        raise ValueError(f'Unknown SiN section: {game}')
    root = base_folder(install)
    files = {} if game == 'sin' else dict(base if base is not None else read_install(install, maps)[0])
    if game != 'sin':
        root = root.parent / game
    if not (root / 'pak0.sin').is_file():
        raise ValueError(f'No SiN {game} pak0.sin in {root}')
    records, counts = [], {}

    def layer(entries, origin):
        counts[origin] = dict(directory=len(entries), extensions=dict(Counter(PurePosixPath(e['name']).suffix for e in entries)))
        names = set()
        for entry in entries:
            name = entry['name']
            row = dict(game=game, source=name, pak=origin, status='pending')
            if name.endswith(('.bsp',) if maps else ('.def', '.sbm', '.spr')):
                records.append(row)
            if name in names:
                row.update(status='shadowed', reason='Earlier same-name directory entry wins within this mount')
                continue
            names.add(name)
            previous = files.get(name)
            if previous and previous['game'] == game:
                previous['record'].update(status='overridden', reason=f'Overridden by {origin}:{name}')
            files[name] = dict(entry, pak=origin, game=game, record=row)

    for number in range(32):
        path = root / f'pak{number}.sin'
        if path.is_file():
            with path.open('rb') as stream:
                entries = spak_directory(stream)
            layer([dict(e, path=path) for e in entries], path.name)
    # Limit traversal to game asset trees; do not index SDKs, sibling games or binaries.
    loose = []
    for folder in ('models', 'textures', 'maps', 'sprites', 'pics', 'env'):
        for path in sorted((root / folder).rglob('*')):
            if path.is_file() and path.resolve().is_relative_to(root.resolve()):
                loose.append(dict(name=checked_name(path.relative_to(root).as_posix()), path=path))
    layer(loose, 'loose')
    return files, records, counts


def read_swl(data):
    """Decode mip zero with its RGBX palette; magenta is transparent.

    ref_gl 10008360..10008458: dimensions +64/+68, palette +72, mips +1100.
    The fourth stored palette byte is ignored, not alpha. No external PAL needed.
    """
    if len(data) < 1236:
        raise ValueError('Truncated SWL header')
    width, height = struct.unpack_from('<2i', data, 64)
    if min(width, height) < 1 or max(width, height) > 16384 or width * height > 67108864:
        raise ValueError('Invalid SWL dimensions')
    offsets = struct.unpack_from('<4i', data, 1100)
    end = 1236
    for level, offset in enumerate(offsets):
        length = (width >> level) * (height >> level)
        if offset < end or offset + length > len(data):
            raise ValueError('Invalid SWL mip range')
        end = offset + length
    palette = [tuple(data[i:i + 3]) for i in range(72, 1096, 4)]
    image = Image.frombytes('P', (width, height), data[offsets[0]:offsets[0] + width * height])
    image.putpalette(bytes(c for rgb in palette for c in rgb))
    image.info['transparency'] = bytes(0 if rgb == (255, 0, 255) else 255 for rgb in palette)
    return image.convert('RGBA')


def read_texture(name, files):
    raw = read_asset(files[name])
    if name.endswith('.swl'):
        return read_swl(raw)
    if not name.endswith('.tga'):
        raise ValueError(f'Unsupported SiN texture: {name}')
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'TGA' or image.width * image.height > 67108864:
                raise ValueError('Invalid TGA format/dimensions')
            image.load()
            return image.convert('RGBA')
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(f'Invalid SiN TGA: {exc}') from exc


def skin_candidates(named):
    # GL_FindImage 100086A4..10008854: exact TGA, exact SWL then TGA if absent.
    if not named:
        return []
    named = checked_name(named)
    return [named, named[:-4] + '.tga'] if named.endswith('.swl') else [named] if named.endswith('.tga') else []


def read_def(data, source='models/unnamed.def'):
    """Read SDEF declarations and safe init metadata; never execute scripts.

    Declarations: path, scale, origin, [alias] filename [weight], id N group
    NAME flags. DEF IDs are one-based; SBM IDs are zero-based (004878CD).
    Default skin is declaration zero plus the surface's skin1/2/3 offset.
    """
    if len(data) > 1024 * 1024:
        raise ValueError('DEF exceeds text limit')
    text = data.decode('latin1')
    # Tokenize comments separately so comment markers in quoted strings survive.
    pattern = r'"[^"\r\n]*"|/\*[\s\S]*?\*/|//[^\r\n]*|[^\s"]+'
    lines = []
    clean = re.sub(r'"[^"\r\n]*"|/\*[\s\S]*?\*/|//[^\r\n]*',
                   lambda m: m[0] if m[0].startswith('"') else '\n' * m[0].count('\n'), text)
    for line in clean.splitlines():
        tokens = [t.strip('"') for t in re.findall(pattern, line)]
        if tokens:
            lines.append(tokens)
    if not lines or lines[0] != ['SDEF']:
        raise ValueError('Expected SDEF header')
    result = dict(basemodel='', skins=[], animations=[], surfaces={}, scale=1.0,
                  origin=[0.0, 0.0, 0.0], classname='', warnings=[], init_commands=[])
    # Native 00487DEE -> 00486190 starts with the DEF's containing directory.
    folder, section = str(PurePosixPath(checked_name(source)).parent), ''
    for tokens in lines[1:]:
        command = tokens[0].lower()
        if command.startswith('!'):
            section = command
            continue
        if section:
            if section == '!init:':
                if command == 'server' and len(tokens) >= 3 and tokens[1].lower() == 'classname':
                    result['classname'] = tokens[2]
                if len(tokens) > 1 and tokens[1].lower() in ('skin', 'group', 'surface'):
                    result['init_commands'].append(tokens)
            continue
        try:
            if command == 'sdef':  # Three retail files repeat the header in declarations.
                continue
            if command == 'path':
                folder = checked_name(tokens[1])
            elif command == 'scale':
                value = float(tokens[1])
                if not math.isfinite(value):
                    raise ValueError('Non-finite DEF scale')
                result['scale'] = value if value > 0 else 1.0  # Native clamp.
            elif command == 'origin':
                coordinates = ' '.join(tokens[1:]).replace(',', ' ').split()
                result['origin'] = [float(v) for v in coordinates]
                if len(result['origin']) != 3 or not all(math.isfinite(v) for v in result['origin']):
                    raise ValueError('Invalid DEF origin')
                if ',' in ' '.join(tokens):
                    result['warnings'].append('Comma-separated DEF origin accepted as three coordinates (exporter repair)')
            elif command == 'id':
                number = int(tokens[1]) - 1
                if number < 0 or 'group' not in tokens:
                    raise ValueError('Invalid DEF surface ID/group')
                group = tokens[tokens.index('group') + 1]
                previous = result['surfaces'].get(number, dict(flags=[], skin=0))
                slot = previous['skin']
                for token in tokens:
                    if token.lower() in ('skin1', 'skin2', 'skin3'):
                        slot |= int(token[-1])  # Native ORs flags from every matching group.
                result['surfaces'][number] = dict(name=group,
                    flags=previous['flags'] + [t.lower() for t in tokens[2:]], skin=slot)
            elif command == 'group':
                continue  # Damage multiplier, not a material assignment.
            else:
                named = tokens[0] if len(tokens) == 1 else tokens[1]
                suffix = PurePosixPath(named.lower()).suffix
                if suffix not in ('.sbm', '.sam') + TEXTURE_SUFFIXES:
                    result['warnings'].append('Ignored declaration: ' + ' '.join(tokens))
                    continue
                source = checked_name(folder + '/' + named if folder else named)
                alias = tokens[0] if len(tokens) > 1 else PurePosixPath(named).stem
                if suffix == '.sbm':
                    result['basemodel'] = source
                else:
                    result['animations' if suffix == '.sam' else 'skins'].append(dict(name=alias, source=source))
        except (IndexError, ValueError) as exc:
            raise ValueError(f'Invalid DEF declaration {" ".join(tokens)}: {exc}') from exc
    if not result['basemodel']:
        raise ValueError('DEF has no SBM base model')
    if result['init_commands']:
        result['warnings'].append('Scripted init skin/group commands retained but not executed; declaration defaults shown')
    return result


def section_range(offset, count, stride, length, minimum, label):
    if count < 0 or offset < minimum or offset + count * stride > length:
        raise ValueError(f'Invalid {label} range')
    return offset + count * stride


def read_sbm(data):
    if len(data) < 28:
        raise ValueError('Truncated SBM header')
    magic, version, verts, sts, surfaces, st_at, end = struct.unpack_from('<4s6i', data)
    if magic != b'SBM ' or version != 1:
        raise ValueError('Expected SBM version 1')
    if not 0 < verts <= 2048 or not 0 < surfaces <= 256 or not 0 < sts <= 65536 or end != len(data):
        raise ValueError('Invalid SBM counts/end')
    table_end = section_range(28, surfaces, 24, end, 28, 'SBM surfaces')
    payload_end = section_range(st_at, sts, 8, end, table_end, 'SBM UV')
    st = list(struct.iter_unpack('<2f', data[st_at:payload_end]))
    if not all(math.isfinite(v) for uv in st for v in uv):
        raise ValueError('Non-finite SBM UV')
    result = []
    for index in range(surfaces):
        at = 28 + index * 24
        ident, triangles, words, cmd_rel, tri_rel, end_rel = struct.unpack_from('<6i', data, at)
        if not 0 < triangles <= 131072 or not 0 <= words <= 10 * triangles + 1:
            raise ValueError('Invalid SBM triangle/command counts')
        surface_end = at + end_rel
        cmd_at, tri_at = at + cmd_rel, at + tri_rel
        cmd_end = section_range(cmd_at, words, 4, end, payload_end, 'SBM commands')
        tri_end = section_range(tri_at, triangles, 16, end, cmd_end, 'SBM triangles')
        if ident < 0 or surface_end != tri_end or triangles < 1:
            raise ValueError('Invalid SBM surface end/count/ID')
        table = list(struct.iter_unpack('<6HI', data[tri_at:tri_end]))
        if any(max(t[:3]) >= verts or max(t[3:6]) >= sts for t in table):
            raise ValueError('Invalid SBM triangle index')
        # Native GL draws per-surface MD2-style strips/fans with normalized UVs.
        uv, tris, cursor = [], [], cmd_at
        while cursor < cmd_end:
            count = struct.unpack_from('<i', data, cursor)[0]
            cursor += 4
            if count == 0:
                break
            if abs(count) < 3 or cursor + abs(count) * 12 > cmd_end:
                raise ValueError('Invalid SBM strip/fan range')
            corners = list(struct.iter_unpack('<ffi', data[cursor:cursor + abs(count) * 12]))
            cursor += abs(count) * 12
            if any(not 0 <= v < verts or not math.isfinite(s) or not math.isfinite(t) for s, t, v in corners):
                raise ValueError('Invalid SBM command vertex/UV')
            base = len(uv)
            uv.extend((s - .5, t - .5) for s, t, v in corners)  # Cancel Q2 texel-centre offset.
            for i in range(2, abs(count)):
                order = (0, i - 1, i) if count < 0 else ((i - 2, i - 1, i) if i % 2 == 0 else (i - 1, i - 2, i))
                tris.append(tuple(corners[j][2] for j in order) + tuple(base + j for j in order))
        if words and (cursor != cmd_end or count != 0 or len(tris) != triangles):
            raise ValueError('Invalid SBM command terminator/triangle count')
        if not words:
            uv = [(min(.995, max(0, s)) - .5, min(.995, max(0, t)) - .5) for s, t in st]
            tris = [t[:6] for t in table]
        result.append(dict(id=ident, st=uv, triangles=tris, geometry_source='GL commands' if words else 'triangle table'))
        payload_end = surface_end
    if payload_end != end:
        raise ValueError('SBM payload does not end at declared length')
    return dict(version=version, vertex_count=verts, surfaces=result)


def read_sam(data, vertex_count=None):
    if len(data) < 128:
        raise ValueError('Truncated SAM header')
    magic, version = struct.unpack_from('<4si', data)
    verts, frames, frame_at, end = struct.unpack_from('<4i', data, 112)
    if magic != b'SAM ' or version != 1:
        raise ValueError('Expected SAM version 1')
    if not 0 < verts <= 2048 or frames < 1 or end != len(data):
        raise ValueError('Invalid SAM counts/end')
    if vertex_count is not None and verts != vertex_count:
        raise ValueError(f'SAM/SBM vertex count mismatch ({verts} != {vertex_count})')
    minimum = section_range(frame_at, frames, 44, end, 128, 'SAM frame table')
    points, normals = [], []
    for frame in range(frames):
        at = frame_at + frame * 44
        values = struct.unpack_from('<10fi', data, at)
        if not all(math.isfinite(v) for v in values[:10]):
            raise ValueError('Non-finite SAM frame transform')
        start = at + values[10]  # Offset is relative to THIS frame descriptor.
        minimum = section_range(start, verts, 4, end, minimum, 'SAM vertices')
        if frame == 0:
            for x, y, z, normal in struct.iter_unpack('<4B', data[start:minimum]):
                # Negative scale is intentional (retail X commonly is negative).
                points.append(tuple(v * values[4 + k] + values[7 + k] for k, v in enumerate((x, y, z))))
                normals.append(normal)
    return dict(points=points, normal_indices=normals, frames=frames,
                pose=data[8:72].split(b'\0')[0].decode('latin1') + ':0')


def choose_animation(definition, mesh, files):
    """Prefer declared idle, then declaration order; orphans try adjacent SAMs."""
    declared = definition['animations']
    ordered = sorted(enumerate(declared), key=lambda p: (not p[1]['name'].lower().startswith('idle'),
                                                        'idle' not in p[1]['name'].lower(), p[0]))
    candidates = [a['source'] for _, a in ordered]
    folder = PurePosixPath(definition['basemodel']).parent
    candidates += sorted((n for n in files if n.endswith('.sam') and PurePosixPath(n).parent == folder),
                         key=lambda n: ('idle' not in PurePosixPath(n).stem, n))
    failures = []
    for name in dict.fromkeys(candidates):
        if name not in files:
            failures.append(dict(source=name, reason='Missing SAM'))
            continue
        try:
            raw = read_asset(files[name])
            pose = read_sam(raw, mesh['vertex_count'])
            reason = ('Declared idle/first available SAM, frame zero' if any(a['source'] == name for a in declared)
                      else 'No usable declared SAM; adjacent matching-vertex-count SAM, frame zero (approximate)')
            return pose, dict(source=name, reason=reason, failures=failures), hashlib.sha256(raw).hexdigest()
        except (ValueError, OSError) as exc:
            failures.append(dict(source=name, reason=str(exc)))
    raise ValueError('No readable SAM for SBM (SBM contains no positions): ' + json.dumps(failures))


def resolve_skins(definition, mesh, files):
    skins = []
    declarations = definition['skins']
    for surface in mesh['surfaces']:
        info = definition['surfaces'].get(surface['id'], {})
        index = info.get('skin', 0)
        named = declarations[index]['source'] if index < len(declarations) else ''
        candidates = skin_candidates(named)
        source = next((n for n in candidates if n in files), '')
        reason = '' if source == named and source else 'Renderer SWL-to-TGA fallback' if source else (
            'No declared skin for surface slot' if not named else 'Declared skin absent')
        # Sensible, explicit fallback: skin zero, then an adjacent same-stem TGA.
        fallback = skin_candidates(declarations[0]['source']) if declarations and index else []
        base = definition['basemodel'][:-4]
        fallback += [base + ext for ext in TEXTURE_SUFFIXES]
        candidates += [n for n in fallback if n not in candidates]
        if not source:
            source = next((n for n in candidates if n in files), '')
            reason += '; ' + ('default/same-stem skin fallback' if source else 'missing-skin checker fallback')
        skins.append(dict(surface=surface['id'], slot=index, named=named, source=source,
                          candidates=candidates, reason=reason))
    return skins


def build_model(mesh, definition, skins):
    result = dict(game='sin', winding='ccw', vertices=[], uvs=[], indices=[], groups=[],
                  material_names=[], material_textures=[], material_settings=[], metadata={})
    skipped = []
    scale, origin = definition['scale'], definition['origin']
    points = [tuple((p[k] + origin[k]) * scale for k in range(3)) for p in mesh['points']]
    for surface, skin in zip(mesh['surfaces'], skins):
        info = definition['surfaces'].get(surface['id'], {})
        flags = info.get('flags', [])
        if 'nodraw' in flags:
            skipped.append(dict(id=surface['id'], reason='Hidden DEF surface'))
            continue
        part = build_md2_model(dict(mesh, points=points, st=surface['st'], triangles=surface['triangles'],
                                    width=1, height=1, skin_names=[s['source'] for s in skins]), skin['texture'])
        base = len(result['vertices']) // 3
        result['groups'].append(dict(start=len(result['indices']), count=len(part['indices']), materialIndex=len(result['material_names'])))
        result['vertices'].extend(part['vertices'])
        result['uvs'].extend(part['uvs'])
        result['indices'].extend(i + base for i in part['indices'])
        result['material_names'].append(info.get('name', f"surface_{surface['id'] + 1}"))
        result['material_textures'].append(skin['texture'])
        settings = {}
        if 'twosided' in flags:
            settings['cull'] = 'none'
        if 'masked' in flags:
            settings['alphaFunc'] = 'GE128'
        if 'translucent33' in flags or 'translucent66' in flags:
            settings.update(blend=['gl_src_alpha', 'gl_one_minus_src_alpha'],
                            opacity=1.0 if 'translucent33' in flags and 'translucent66' in flags else
                            .33 if 'translucent33' in flags else .66, depthWrite=False)
        result['material_settings'].append(settings)
    result['metadata'].update(pose=mesh['pose'], frames=mesh['frames'], skins=skins, definition=definition,
                              format='SBM/SAM version 1', vertex_count=mesh['vertex_count'],
                              omitted_surfaces=skipped, warnings=list(definition['warnings']))
    missing_ids = sorted(set(definition['surfaces']) - {s['id'] for s in mesh['surfaces']})
    if missing_ids:
        result['metadata']['warnings'].append(f'DEF surface IDs absent from SBM: {[i + 1 for i in missing_ids]}')
    if any(flag in s.get('flags', []) for s in definition['surfaces'].values() for flag in ('envmapped', 'goldenvmapped', 'fullbright')):
        result['metadata']['warnings'].append('Environment mapping/fullbright use ordinary static skin shading')
    if not result['indices']:
        raise ValueError('Model has no visible triangles after DEF surface flags')
    return result


def category(name, definition):
    """Authored folder/purpose; specific creature/vehicle folders before objects."""
    parts = definition['basemodel'].split('/')
    folder = parts[1] if len(parts) > 2 else ''
    if name.startswith('models/view_') or folder.removesuffix('_w') in {
        'asrifle', 'magnum', 'shotgun', 'rlaunch', 'srifle', 'spgun', 'hvgun', 'heligun', 'fist',
        'punch', 'quantum', 'pulse', 'mines', 'silencer', 'stun', 'spear', 'sniper', 'spraygun',
        'crossbow', 'concussion', 'dualmagnum', 'flamethrower', 'informgun', 'missile', 'nuke2', 'pack', 'grap'}:
        return 'Weapons'
    if folder.startswith('pl_'):
        return 'Players'
    if folder in {'bacrodai', 'creeper', 'eon', 'lamprey', 'manumit', 'nautic', 'thrall', 'securton',
                  'recbio', 'reconah', 'vultorn', 'crawler', 'goliath', 'grogg', 'splitter'}:
        return 'Monsters'
    if folder in {'bum', 'chemsuit', 'chemworker', 'civfemale', 'civmale', 'dark', 'grunt', 'grunt_lowpoly',
                  'guerilla', 'hcofficer', 'labworker', 'mancini', 'peon', 'pinpal', 'pinphat', 'sarge',
                  'scientist', 'tech', 'worker', 'bob', 'jessica', 'mdancer', 'mortician', 'priest', 'slim', 'thug'}:
        return 'Characters/NPCs'
    if folder in {'atv', 'buggy', 'bulldozer', 'car', 'crane', 'forklift', 'hcboat', 'humv', 'jeep',
                  'minitrucklow', 'sub', 'truck', 'van', 'bike_prototype', 'hoverbike_v', 'hb_dash', 'cruise', 'car_door', 'car_tyre'}:
        return 'Vehicles'
    if folder in {'bat', 'batlow', 'bee', 'bug', 'doggie', 'fish', 'pigeon', 'rat'}:
        return 'Animals'
    if folder in {'powerups', 'runes', 'keys', 'flag'}:
        return 'Items'
    if folder in {'gibs', 'xeno_bodies', 'deadguy'}:
        return 'Gibs/Bodies'
    if folder.startswith(('pulsefx', 'quantum_fx')) or folder in {'beam', 'explosion', 'muzflash', 'lightning', 'goo', 'crawlergoo'}:
        return 'Effects'
    return 'Objects'  # Retail models/items contains furniture/props, not just pickups.


def import_catalog(install, output, model_filter=None):
    output = Path(output)
    base = read_install(install)
    campaigns = {'sin': base}
    for game in available_games(install)[1:]:
        campaigns[game] = read_install(install, game=game, base=base[0])
    for sub in ('textures', 'model_json'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    catalog, seen, textures = [], {}, {}

    def texture(name, files):
        if not name:
            return 'missing_skin.png'
        entry = files[name]
        key = entry['game'], name
        if key not in textures:
            row = dict(game=entry['game'], source=name, pak=entry['pak'])
            textures[key] = row
            try:
                image = read_texture(name, files)
                png = entry['game'] + '_' + safe(name[:-4]) + '_' + hashlib.sha256(name.encode()).hexdigest()[:8] + '.png'
                target = output / 'textures' / png
                if not target.is_file():
                    image.save(target)
                row.update(status='ready', texture=png)
            except (ValueError, OSError) as exc:
                row.update(status='skipped', reason=str(exc))
        return textures[key].get('texture', 'missing_skin.png')

    for game, (files, records, counts) in campaigns.items():
        definitions, referenced = {}, {}
        # Parse own DEFs before considering unreferenced raw SBMs. Keep each skin/pose variant.
        for row in records:
            name = row['source']
            if row['status'] != 'pending' or not name.endswith('.def'):
                continue
            try:
                definition = read_def(read_asset(files[name], max_bytes=1024 * 1024), name)
                definitions[name] = definition
                referenced.setdefault(definition['basemodel'], []).append(name)
                row['skin_declarations'] = []
                for skin in definition['skins']:
                    candidates = skin_candidates(skin['source'])
                    source = next((n for n in candidates if n in files), '')
                    row['skin_declarations'].append(dict(skin, resolved=source, candidates=candidates,
                        reason='' if source else 'Declared skin absent from layered game files'))
            except (ValueError, OSError) as exc:
                row.update(status='skipped', reason=str(exc))
        for row in sorted(records, key=lambda r: (not r['source'].endswith('.def'), r['source'], r['pak'])):
            name = row['source']
            if row['status'] in ('overridden', 'shadowed'):
                continue
            model = game + '_' + safe(name[:-4]) + '_' + hashlib.sha256(name.encode()).hexdigest()[:8]
            item = dict(model_name=model, display_name=GAMES[game] + ': ' + PurePosixPath(name).stem,
                        game='sin', category=GAMES[game] + ': Objects', texture_name='',
                        source=f"{game}/{row['pak']}:{name}", status='ready')
            if name.endswith('.sbm') and name in referenced:
                row.update(status='referenced', definitions=referenced[name], reason='Represented by DEF model variants')
                continue
            row['model'] = model
            try:
                if row['status'] == 'skipped':
                    raise ValueError(row['reason'])
                if model_filter and model_filter.lower() not in name:
                    row.update(status='filtered', reason='Excluded by requested model filter')
                    continue
                if name.endswith('.spr'):
                    raise ValueError('Sprite (.spr), not an SBM polygon model')
                definition = definitions.get(name) or dict(basemodel=name, skins=[], animations=[], surfaces={},
                    scale=1.0, origin=[0, 0, 0], classname='', init_commands=[],
                    warnings=['Unreferenced SBM: adjacent SAM and same-stem skin fallback; authored DEF transform unavailable'])
                source = definition['basemodel']
                if source not in files:
                    raise ValueError(f'Missing SBM base model: {source}')
                raw = read_asset(files[source])
                mesh = read_sbm(raw)
                pose, animation, sam_hash = choose_animation(definition, mesh, files)
                mesh.update(pose)
                skins = resolve_skins(definition, mesh, files)
                row.update(basemodel=source, animation=animation, skins=skins)
                for skin in skins:
                    skin['texture'], skin['decode_failures'] = 'missing_skin.png', []
                    for candidate in skin['candidates']:
                        if candidate not in files:
                            continue
                        png = texture(candidate, files)
                        if png != 'missing_skin.png':
                            if candidate != skin['source']:
                                skin['reason'] += '; unreadable skin replaced with ' + candidate
                            skin.update(source=candidate, texture=png)
                            break
                        entry = files[candidate]
                        skin['decode_failures'].append(dict(source=candidate, reason=textures[entry['game'], candidate]['reason']))
                    if skin['decode_failures']:
                        skin['reason'] = '; '.join(f"{r['source']}: {r['reason']}" for r in skin['decode_failures']) + '; ' + skin['reason']
                        if skin['texture'] == 'missing_skin.png':
                            skin['reason'] += '; missing-skin checker fallback'
                # Byte-identical mesh/animation plus the same presentation collapses aliases,
                # while identical meshes with different skins/transforms remain useful variants.
                identity = json.dumps([hashlib.sha256(raw).hexdigest(), sam_hash, definition['scale'], definition['origin'],
                                       definition['surfaces'], [s['texture'] for s in skins]], sort_keys=True)
                if identity in seen:
                    row.update(status='duplicate', model=seen[identity], reason='Byte-identical SBM/SAM with identical skin/transform defaults; listed once')
                    continue
                data = build_model(mesh, definition, skins)
                data['metadata'].update(source=item['source'], animation=animation)
                data['metadata']['warnings'].extend(s['reason'] for s in skins if s['reason'])
                (output / 'model_json' / (model + '.json')).write_text(json.dumps(data, separators=(',', ':'), allow_nan=False), encoding='utf-8')
                item.update(texture_name=data['material_textures'][0], category=GAMES[game] + ': ' + category(name, definition),
                            warnings=data['metadata']['warnings'])
                row.update(status='ready', triangles=len(data['indices']) // 3, warnings=data['metadata']['warnings'])
                seen[identity] = model
            except (ValueError, OSError) as exc:
                row.update(status='skipped', reason=str(exc))
                item.update(status='skipped', reason=str(exc))
            catalog.append(item)
        # Inventory every declared skin, including alternates and DEFs whose mesh failed.
        for row in records:
            for skin in row.get('skin_declarations', []):
                if not skin['resolved']:
                    skin['status'] = 'missing'
                elif model_filter and model_filter.lower() not in row['source']:
                    skin['status'] = 'not_decoded_filtered'
                else:
                    skin['texture'] = texture(skin['resolved'], files)
                    skin['status'] = 'ready' if skin['texture'] != 'missing_skin.png' else 'skipped'
                    if skin['status'] == 'skipped':
                        entry = files[skin['resolved']]
                        skin['reason'] = textures[entry['game'], skin['resolved']]['reason']
        for name, entry in sorted(files.items()):
            if entry['game'] == game and name.startswith('models/') and name.endswith(TEXTURE_SUFFIXES) and (not model_filter or model_filter.lower() in name):
                texture(name, files)
    checker = output / 'textures' / 'missing_skin.png'
    if not checker.is_file():
        image = Image.new('RGB', (2, 2))
        image.putdata([(255, 0, 255), (32, 32, 32), (32, 32, 32), (255, 0, 255)])
        image.save(checker)
    records = [r for _, rows, _ in campaigns.values() for r in rows]
    report = dict(entries=len(catalog), ready=sum(r['status'] == 'ready' for r in records),
                  duplicates=sum(r['status'] == 'duplicate' for r in records),
                  skipped=sum(r['status'] == 'skipped' for r in records),
                  textures=sum(r['status'] == 'ready' for r in textures.values()),
                  results=records, texture_results=list(textures.values()),
                  model_entries=sum(r['source'].endswith(('.def', '.sbm')) for r in records),
                  skin_declaration_resolution=dict(Counter(s['status'] for r in records for s in r.get('skin_declarations', []))),
                  missing_skin_references=sorted({s['source'] for r in records for s in r.get('skin_declarations', []) if not s['resolved']}),
                  campaigns={g: dict(archive_counts=counts, statuses=dict(Counter(r['status'] for r in rows)))
                             for g, (_, rows, counts) in campaigns.items()},
                  search_order={g: list(counts) for g, (_, _, counts) in campaigns.items()},
                  skin_resolution=dict(Counter('resolved' if s['texture'] != 'missing_skin.png' else s['reason']
                                               for r in records for s in r.get('skins', []))))
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--filter', help='Case-insensitive model/texture path substring')
    args = parser.parse_args()
    print(json.dumps(import_catalog(args.install, args.output or LOCAL_DATA / 'sin', args.filter), indent=1))

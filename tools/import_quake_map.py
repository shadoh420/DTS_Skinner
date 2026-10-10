"""Classic Quake BSP29 maps and BSP item boxes. No placed MDLs or gameplay simulation.

python tools/import_quake_map.py --install "C:/Program Files (x86)/Steam/steamapps/common/Quake" [--replace]

The pack uses the Unreal page's position/UV/UV2/index binary layout. Quake materials instead sample indexed
textures through the actual software colormap: no fitted RGB multiplier or display gamma. Source units map to
viewer metres at 1/32, with (x,y,z) -> (y,z,x). Model exports keep native units, as the MDL importer does.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct

import numpy as np
from PIL import Image

try:
    from tools.import_quake import item_box, palette_image, read_install, safe
    from tools.import_unreal_map import floor_below, pack
    from tools.local_data import LOCAL_DATA
except ImportError:
    from import_quake import item_box, palette_image, read_install, safe
    from import_unreal_map import floor_below, pack
    from local_data import LOCAL_DATA

SCALE = 1 / 32


def entities(data):
    tokens = re.findall(r'"([^"\r\n]*)"|([{}])', data.rstrip(b'\0').decode('latin1'))
    result, current, key = [], None, None
    for text, brace in tokens:
        if brace == '{':
            if current is not None:
                raise ValueError('Nested entity')
            current = {}
        elif brace == '}':
            if current is None or key is not None:
                raise ValueError('Incomplete entity')
            result.append(current)
            current = None
        elif current is None:
            raise ValueError('Entity property outside braces')
        elif key is None:
            key = text
        else:
            current[key] = text.replace('\\n', '\n')
            key = None
    if current is not None:
        raise ValueError('Unclosed entity')
    return result


def read_bsp(data):
    if len(data) < 124 or struct.unpack_from('<i', data)[0] != 29:
        raise ValueError('Expected Quake BSP version 29')
    lumps = []
    for offset, size in struct.iter_unpack('<ii', data[4:124]):
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('Invalid BSP lump range')
        lumps.append(data[offset:offset + size])

    def rows(index, fmt):
        if len(lumps[index]) % struct.calcsize('<' + fmt):
            raise ValueError(f'Incomplete BSP lump {index}')
        return list(struct.iter_unpack('<' + fmt, lumps[index]))

    bsp = dict(entities=entities(lumps[0]), planes=rows(1, '4fi'), points=rows(3, '3f'),
               texinfo=rows(6, '8fii'), faces=rows(7, 'Hhihh4Bi'), lighting=lumps[8],
               edges=rows(12, '2H'), surfedges=[r[0] for r in rows(13, 'i')], models=rows(14, '9f7i'))
    if not bsp['models']:
        raise ValueError('BSP has no world model')
    for key in ('planes', 'points', 'texinfo', 'models'):
        if any(not math.isfinite(v) for row in bsp[key] for v in row):
            raise ValueError(f'Non-finite BSP {key}')
    textures, blob = [], lumps[2]
    if len(blob) < 4:
        raise ValueError('BSP has no miptex directory')
    count = struct.unpack_from('<i', blob)[0]
    if count < 0 or 4 + 4 * count > len(blob):
        raise ValueError('Invalid miptex count')
    for (offset,) in struct.iter_unpack('<i', blob[4:4 + 4 * count]):
        if offset == -1:
            textures.append(None)
            continue
        if offset < 4 + 4 * count or offset + 40 > len(blob):
            raise ValueError('Invalid miptex header')
        name, width, height, first, _, _, _ = struct.unpack_from('<16s6I', blob, offset)
        if not width or not height or first < 40 or offset + first + width * height > len(blob):
            raise ValueError('Invalid miptex pixels')
        textures.append(dict(name=name.split(b'\0')[0].decode('latin1').lower(), width=width, height=height,
                             pixels=blob[offset + first:offset + first + width * height]))
    bsp['textures'] = textures
    return bsp


def surface_kind(name):
    if name in ('clip', 'trigger'):
        return 'hidden'
    if name.startswith('sky'):
        return 'sky'
    if name.startswith('*'):
        return 'turbulent'
    return 'normal'


def face_data(bsp, number):
    plane, side, first, count, info, *tail = bsp['faces'][number]
    if side not in (0, 1) or count < 3 or first < 0 or first + count > len(bsp['surfedges']):
        raise ValueError(f'Invalid face {number} edges')
    if not 0 <= plane < len(bsp['planes']) or not 0 <= info < len(bsp['texinfo']):
        raise ValueError(f'Invalid face {number} plane/texinfo')
    points = []
    for edge in bsp['surfedges'][first:first + count]:
        if abs(edge) >= len(bsp['edges']):
            raise ValueError('Invalid surfedge')
        vertex = bsp['edges'][abs(edge)][0 if edge >= 0 else 1]
        if vertex >= len(bsp['points']):
            raise ValueError('Invalid edge vertex')
        points.append(bsp['points'][vertex])
    points = np.asarray(points, dtype=np.float64)
    normal = np.array(bsp['planes'][plane][:3]) * (-1 if side else 1)
    area = np.cross(points - points[0], np.roll(points, -1, axis=0) - points[0]).sum(axis=0)
    if np.dot(area, normal) < 0:
        points = points[::-1].copy()
    texinfo = bsp['texinfo'][info]
    if not 0 <= texinfo[8] < len(bsp['textures']):
        raise ValueError(f'Face {number} has invalid miptex {texinfo[8]}')
    texture = bsp['textures'][texinfo[8]]
    if texture is None:
        # The registered e2m3 has a missing miptex on a button. Keep the face and report the engine-style fallback.
        texture = dict(name=f'__missing_miptex_{texinfo[8]}', width=16, height=16, missing=True,
                       pixels=bytes(0 if (x < 8) != (y < 8) else 255 for y in range(16) for x in range(16)))
    # Animation chains start at +0 (normal) or +a (alternate); this preview holds that first frame.
    if texture['name'].startswith('+') and len(texture['name']) > 2:
        first_name = '+' + ('0' if texture['name'][1].isdigit() else 'a') + texture['name'][2:]
        texture = next((t for t in bsp['textures'] if t and t['name'] == first_name), texture)
    vectors = np.array(texinfo[:8]).reshape(2, 4)
    st = points @ vectors[:, :3].T + vectors[:, 3]
    # CalcSurfaceExtents: floor/ceil texture-space bounds at 16 units, including both endpoint luxels.
    lo, hi = np.floor(st.min(axis=0) / 16).astype(int), np.ceil(st.max(axis=0) / 16).astype(int)
    return dict(number=number, points=points, normal=normal, st=st, mins=lo * 16, size=hi - lo + 1,
                texture=texture, kind=surface_kind(texture['name']), styles=tail[:4], lightofs=tail[4])


def light_codes(face, lighting):
    """Software R_BuildLightMap's fixed-point colormap coordinate, before luxel interpolation.

    All present styles use 'm': (ord('m')-ord('a'))*22 = 264. VID_CBITS=6, hence the >>2 conversion;
    the high byte selects one of colormap.lmp's 64 rows. An entirely unlit BSP is fullbright.
    """
    w, h = map(int, face['size'])
    if face['kind'] != 'normal' or not lighting:
        return np.full((1, 1), 32 * 256, dtype=np.uint16)
    if w <= 0 or h <= 0 or w * h > 1_000_000:
        raise ValueError('Invalid lightmap extent')
    total = np.zeros(w * h, dtype=np.int64)
    offset = face['lightofs']
    if offset >= 0:
        for style in face['styles']:
            if style == 255:
                break
            if offset + w * h > len(lighting):
                raise ValueError(f"Face {face['number']} lightmap exceeds lighting lump")
            total += np.frombuffer(lighting[offset:offset + w * h], np.uint8).astype(np.int64) * 264
            offset += w * h
    return np.maximum(64, (255 * 256 - total) >> 2).astype(np.uint16).reshape(h, w)


def colormap_image(data, palette):
    if len(data) < 64 * 256:
        raise ValueError('Missing or truncated gfx/colormap.lmp')
    return palette_image(data[:64 * 256], (256, 64), palette)


def brightness_curve(data, palette):
    colors = np.frombuffer(palette, np.uint8).reshape(256, 3)
    table = np.frombuffer(data[:64 * 256], np.uint8).reshape(64, 256)
    luminance = colors @ np.array([.2126, .7152, .0722])
    ratios = (luminance[table[:, :224]].sum(axis=1) / luminance[:224].sum()).tolist()
    return dict(method='Exact 256 x 64 palette-index/light-row RGB lookup; ratios are diagnostics, not a fit',
                mean_luminance_ratio_by_row=ratios, fullbrights_unchanged=bool((table[:, 224:] == np.arange(224, 256)).all()),
                normal_style=264, code='max(64, (65280 - sum(samples) * 264) >> 2)', row='floor(interpolated_code / 256)',
                colormap_sha256=hashlib.sha256(data).hexdigest())


def vector(entity, key='origin'):
    values = [float(x) for x in entity.get(key, '0 0 0').split()]
    if len(values) != 3 or not all(math.isfinite(v) for v in values):
        raise ValueError(f'Invalid entity {key}')
    return np.array(values)


def instances(bsp):
    """Normal-skill single player, no runes; brush spawn positions after train initialization."""
    result = [dict(model=0, entity=0, classname='worldspawn', offset=[0., 0., 0.], state='world')]
    skipped = []
    for index, entity in enumerate(bsp['entities']):
        model = entity.get('model', '')
        if not model.startswith('*'):
            continue
        classname = entity.get('classname', '')
        flags = int(entity.get('spawnflags', '0'))
        if classname.startswith('trigger_') or flags & 512:
            skipped.append(dict(entity=index, classname=classname, reason='trigger volume' if classname.startswith('trigger_') else 'excluded on normal skill'))
            continue
        number = int(model[1:])
        if not 0 < number < len(bsp['models']):
            raise ValueError(f'Invalid brush model {model}')
        bounds = bsp['models'][number]
        mins, size = np.array(bounds[:3]) - 1, np.array(bounds[3:6]) - np.array(bounds[:3]) + 2
        offset, state = vector(entity), 'authored origin; movement angles cleared'
        if classname == 'func_plat' and not entity.get('targetname'):
            offset[2] -= float(entity.get('height', '0')) or size[2] - 8
            state = 'bottom (untargeted plat); height or size_z - 8'
        elif classname == 'func_train':
            target = next((e for e in bsp['entities'] if e.get('targetname') == entity.get('target') and entity.get('target')), None)
            if target is None:
                raise ValueError(f'func_train {index} has no first path corner')
            offset = vector(target) - mins
            state = 'first path corner minus model mins (after initialization, before travel)'
        elif classname == 'func_door' and flags & 1:
            angle = float(entity.get('angle', '0'))
            direction = np.array([0, 0, 1 if angle == -1 else -1]) if angle in (-1, -2) else np.array([math.cos(math.radians(angle)), math.sin(math.radians(angle)), 0])
            lip = float(entity.get('lip', '0')) or 8
            offset += direction * (abs(float(direction @ size)) - lip)
            state = 'DOOR_START_OPEN; moved by projected size minus lip'
        elif classname == 'func_door':
            state = 'closed'
        result.append(dict(model=number, entity=index, classname=classname, offset=offset.tolist(), state=state))
    return result, skipped


def viewpoints(bsp):
    views = []
    for classname in ('info_player_start', 'info_player_deathmatch', 'info_intermission'):
        for entity in bsp['entities']:
            if entity.get('classname') != classname:
                continue
            origin = vector(entity)
            origin[2] += 0 if classname == 'info_intermission' else 22
            angle = float(entity.get('angle', '0'))
            pitch = 0
            if classname == 'info_intermission' and 'mangle' in entity:
                angles = vector(entity, 'mangle')
                pitch, angle = -float(angles[0]), float(angles[1])
            views.append(dict(origin=(origin[[1, 2, 0]] * SCALE).tolist(), native_origin=origin.tolist(),
                              yaw=-angle, pitch=pitch, classname=classname))
    if not views:
        raise ValueError('No player start, deathmatch start or intermission viewpoint')
    return views


def geometry(bsp, placed, light=True):
    faces, tiles, audit, floors = [], [], [], []
    for instance in placed:
        model = bsp['models'][instance['model']]
        first, count = model[14:16]
        if first < 0 or count < 0 or first + count > len(bsp['faces']):
            raise ValueError('Invalid model face range')
        for number in range(first, first + count):
            face = face_data(bsp, number)
            if face['kind'] == 'hidden':
                continue
            points = face['points'] + instance['offset']
            face['placed_points'] = points
            if light:
                tiles.append(light_codes(face, bsp['lighting']))
            faces.append(face)
            audit.append(dict(face=number, entity=instance['entity'], texture=face['texture']['name'], kind=face['kind'],
                              texturemins=face['mins'].tolist(), luxels=face['size'].tolist(), lightofs=face['lightofs'],
                              styles=face['styles'], missing=face['texture'].get('missing', False)))
            if face['kind'] == 'normal' and face['normal'][2] > 0:
                floors.extend((points[0], points[k], points[k + 1]) for k in range(1, len(points) - 1))
    if not faces:
        raise ValueError('No visible BSP faces')
    width, height, places = pack([(t.shape[1], t.shape[0]) for t in tiles]) if light else (1, 1, [])
    atlas = np.zeros((height, width, 3), np.uint8)
    positions, uvs, uv2, groups, images = [], [], [], {}, {}
    for index, face in enumerate(faces):
        tex = face['texture']
        png = hashlib.sha256(struct.pack('<II', tex['width'], tex['height']) + tex['pixels']).hexdigest()[:24] + '.png'
        images[png] = tex
        group = groups.setdefault((png, face['kind']), dict(texture=png, kind=face['kind'], size=[tex['width'], tex['height']], flags=0, indices=[]))
        base = len(positions)
        positions.extend(face['placed_points'])
        uvs.extend(face['st'] / [tex['width'], tex['height']])
        for k in range(1, len(face['points']) - 1):
            group['indices'].extend((base, base + k, base + k + 1))
        if light:
            x, y = places[index]
            tile = tiles[index]
            rgb = np.stack((tile >> 8, tile & 255, np.zeros_like(tile)), axis=-1).astype(np.uint8)
            h, w = tile.shape
            atlas[y:y + h + 2, x:x + w + 2] = np.pad(rgb, ((1, 1), (1, 1), (0, 0)), mode='edge')
            coords = (face['st'] - face['mins']) / 16 if (w, h) != (1, 1) else np.zeros_like(face['st'])
            uv2.extend((coords + [x + 1.5, y + 1.5]) / [width, height])
    indices, table = [], []
    for group in groups.values():
        part = group.pop('indices')
        table.append(dict(group, start=len(indices), count=len(part)))
        indices.extend(part)
    return dict(points=np.array(positions), uvs=np.array(uvs), uv2=np.array(uv2), indices=indices, groups=table,
                textures=images, atlas=Image.fromarray(atlas), audit=audit, floors=np.array(floors))


def build_item(raw, palette, name):
    bsp = read_bsp(raw)
    data = geometry(bsp, [dict(model=0, entity=0, offset=[0, 0, 0])], light=False)
    textures, names = {}, []
    for group in data['groups']:
        tex = data['textures'][group['texture']]
        png = f"{name}_{safe(tex['name'])}.png"
        textures[png] = palette_image(tex['pixels'], (tex['width'], tex['height']), palette)
        names.append(png)
    return dict(game='quake', winding='ccw', vertices=data['points'][:, [1, 2, 0]].ravel().tolist(),
                uvs=data['uvs'].ravel().tolist(), indices=data['indices'], material_names=names, material_textures=names,
                material_settings=[{'unlit': True} for _ in names], groups=[dict(start=g['start'], count=g['count'], materialIndex=i)
                for i, g in enumerate(data['groups'])], metadata=dict(pose='BSP item box', lighting='fullbright texture')), textures


def build_map(raw, name):
    bsp = read_bsp(raw)
    placed, skipped = instances(bsp)
    data = geometry(bsp, placed)
    points = data['points'][:, [1, 2, 0]] * SCALE
    views = viewpoints(bsp)
    bounds = np.array(bsp['models'][0][:6]).reshape(2, 3)
    checks = []
    for view in views:
        origin = view['native_origin']
        floor = floor_below(data['floors'], *origin)
        checks.append(dict(classname=view['classname'], inside_bounds=bool(((origin >= bounds[0]) & (origin <= bounds[1])).all()),
                           floor_z=floor, eye_above_floor=origin[2] - floor if floor is not None else None))
    blob = b''.join((points.astype('<f4').tobytes(), data['uvs'].astype('<f4').tobytes(),
                     data['uv2'].astype('<f4').tobytes(), np.array(data['indices'], '<u4').tobytes()))
    info = next((e for e in bsp['entities'] if e.get('classname') == 'worldspawn'), {})
    scene = dict(name=name, title=info.get('message', ''), format='quake-bsp29', vertices=len(points), indices=len(data['indices']),
                 lightmap=list(data['atlas'].size), groups=data['groups'], viewpoints=views, bounds=bounds.tolist(),
                 instances=placed, skipped_entities=skipped, faces=data['audit'], viewpoint_checks=checks, missing=sorted({f['texture'] for f in data['audit'] if f['missing']}),
                 assumptions=['normal skill, single player, no runes', 'all light styles held at m (264)',
                              'brushes at spawn; trains initialized at first corner, before travel', 'animated textures held at +0/+a'])
    return scene, blob, data['textures'], data['atlas']


def import_maps(install, output, replace=False):
    files, records, counts = read_install(install)
    output = Path(output)
    palette = files['gfx/palette.lmp'][0]
    colormap = files.get('gfx/colormap.lmp', (b'',))[0]
    lut = colormap_image(colormap, palette)
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    lut.save(output / 'textures/colormap.png')
    curve = brightness_curve(colormap, palette)
    (output / 'brightness.json').write_text(json.dumps(curve, indent=2), encoding='utf-8')
    records = [r for r in records if r['source'].endswith('.bsp')]
    result = dict(imported=[], skipped=[], failed={}, pak_counts=counts, bsp_entries=len(records), results=records)
    index = []
    for record in records:
        name = record['source']
        if record['status'] == 'overridden':
            continue
        if item_box(name) or not name.startswith('maps/'):
            record.update(status='skipped', reason='BSP item box; imported by the quake models importer' if item_box(name) else 'Not a maps/*.bsp level')
            result['skipped'].append(name)
            continue
        ident = safe(Path(name).stem)
        folder = output / 'maps' / ident
        try:
            if not replace and all((folder / f).is_file() for f in ('scene.json', 'geometry.bin', 'lightmap.png')):
                scene = json.loads((folder / 'scene.json').read_text(encoding='utf-8'))
                record.update(status='skipped', reason='Already imported; use --replace to rebuild')
                result['skipped'].append(name)
            else:
                scene, blob, textures, atlas = build_map(files[name][0], ident)
                for png, tex in textures.items():
                    target = output / 'textures' / png
                    if not target.is_file() or replace:
                        # Store the palette index, not RGB: the fragment shader looks up the original colormap.
                        Image.frombytes('L', (tex['width'], tex['height']), tex['pixels']).save(target)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'geometry.bin').write_bytes(blob)
                atlas.save(folder / 'lightmap.png')
                (folder / 'scene.json').write_text(json.dumps(scene, separators=(',', ':')), encoding='utf-8')
                record['status'] = 'imported'
                result['imported'].append(ident)
            if scene['missing']:
                record['warnings'] = scene['missing']
            index.append(dict(id=ident, name=ident, title=scene['title'], group='Quake (1996)'))
        except (ValueError, IndexError, struct.error) as exc:
            record.update(status='skipped', reason=str(exc))
            result['failed'][name] = str(exc)
    (output / 'index.json').write_text(json.dumps(sorted(index, key=lambda e: (e['id'] != 'start', e['id'])), indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    print(json.dumps(import_maps(args.install, args.output or LOCAL_DATA / 'quake-maps', args.replace), indent=1))

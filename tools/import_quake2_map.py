"""Quake II and installed xatrix/rogue/ctf BSP38 maps, ref_gl 3.20 lighting.

python tools/import_quake2_map.py --install "C:/Program Files (x86)/Steam/steamapps/common/Quake 2/baseq2" [--replace] [--game all|quake2|xatrix|rogue|ctf]

Data defaults to SKINNER_DATA_DIR/quake2-maps. No placed MD2s or gameplay simulation.
Reference settings: intensity=2, gl_modulate=1, vid_gamma=1; light styles held at m,
except switchable START_OFF lights at a. Source coordinates use the Quake map basis.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path
import struct

import numpy as np
from PIL import Image

try:
    from tools.import_quake2 import GAMES, available_games, asset_name, read_install, read_pcx, safe
    from tools.import_quake_map import SCALE, entities, face_data as quake_face_data, floor_below, pack, rotation, vector
    from tools.import_quake import palette_image
    from tools.local_data import LOCAL_DATA
except ImportError:
    from import_quake2 import GAMES, available_games, asset_name, read_install, read_pcx, safe
    from import_quake_map import SCALE, entities, face_data as quake_face_data, floor_below, pack, rotation, vector
    from import_quake import palette_image
    from local_data import LOCAL_DATA

SKY, WARP, TRANS33, TRANS66, FLOWING, NODRAW = 4, 8, 16, 32, 64, 128
INTENSITY = 2


def read_wal(data):
    if len(data) < 100:
        raise ValueError('Truncated WAL header')
    name, width, height, *offsets = struct.unpack_from('<32s6I', data)
    if not width or not height:
        raise ValueError('Invalid WAL dimensions')
    previous = 100
    for level, offset in enumerate(offsets):
        size = (width >> level) * (height >> level)
        if not size or offset < previous or offset + size > len(data):
            raise ValueError('Invalid WAL mip range')
        previous = offset + size
    return dict(name=name.split(b'\0')[0].decode('latin1'), width=width, height=height,
                pixels=data[offsets[0]:offsets[0] + width * height])


def upload_texture(texture, palette):
    # gl_image.c GL_LightScaleTexture: gamma[intensity[channel]] on mipmapped textures.
    # intensitytable[i] = min(int(i * intensity), 255); gamma=1 gives an identity table.
    rgb = np.asarray(palette_image(texture['pixels'], (texture['width'], texture['height']), palette), dtype=np.uint16)
    return Image.fromarray(np.minimum(rgb * INTENSITY, 255).astype(np.uint8))


def read_bsp(data, files):
    if len(data) < 160 or data[:4] != b'IBSP' or struct.unpack_from('<i', data, 4)[0] != 38:
        raise ValueError('Expected IBSP version 38')
    lumps = []
    for offset, size in struct.iter_unpack('<ii', data[8:160]):
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('Invalid BSP38 lump range')
        lumps.append(data[offset:offset + size])

    def rows(index, fmt):
        if len(lumps[index]) % struct.calcsize('<' + fmt):
            raise ValueError(f'Incomplete BSP38 lump {index}')
        return list(struct.iter_unpack('<' + fmt, lumps[index]))

    bsp = dict(entities=entities(lumps[0]), planes=rows(1, '4fi'), points=rows(2, '3f'),
               faces=rows(6, 'Hhihh4Bi'), lighting=lumps[7], edges=rows(11, '2H'),
               surfedges=[r[0] for r in rows(12, 'i')], models=rows(13, '9f3i'))
    if not bsp['models']:
        raise ValueError('BSP38 has no world model')
    for key in ('planes', 'points', 'models'):
        if any(not math.isfinite(v) for row in bsp[key] for v in row):
            raise ValueError(f'Non-finite BSP38 {key}')
    textures, texinfo, chains = [], [], []
    cache = {}
    for i, row in enumerate(rows(5, '8fii32si')):
        if not all(math.isfinite(v) for v in row[:8]):
            raise ValueError('Non-finite BSP38 texinfo')
        name = asset_name(row[10].split(b'\0')[0].decode('latin1'))
        path = 'textures/' + name + '.wal'
        if path not in cache:
            if path in files:
                cache[path] = dict(read_wal(files[path][0]), name=name)
            else:
                cache[path] = dict(name=name, width=16, height=16, missing=True,
                                   pixels=bytes(0 if (x < 8) != (y < 8) else 255 for y in range(16) for x in range(16)))
        textures.append(cache[path])
        texinfo.append((*row[:8], i, row[8]))  # Adapt to the shared Quake polygon/extents helper.
        chains.append(row[11])
    if any(n < -1 or n >= len(texinfo) for n in chains):
        raise ValueError('Invalid nexttexinfo index')
    bsp.update(textures=textures, texinfo=texinfo, nexttexinfo=chains)
    return bsp


def face_data(bsp, number):
    face = quake_face_data(bsp, number)
    info = bsp['faces'][number][4]
    flags = bsp['texinfo'][info][9]
    # SKY takes precedence: real sky texinfos commonly have both SKY and NODRAW.
    kind = 'sky' if flags & SKY else 'hidden' if flags & NODRAW else 'turbulent' if flags & WARP else 'normal'
    face.update(flags=flags, kind=kind, unlit=bool(flags & (SKY | WARP | TRANS33 | TRANS66)),
                alpha=.33 if flags & TRANS33 else .66 if flags & TRANS66 else 1,
                nexttexinfo=bsp['nexttexinfo'][info])
    return face


def lightmap_rgb(face, lighting, dark=frozenset()):
    """gl_rsurf.c R_BuildLightMap, RGB mode, gl_modulate=1 and style m=12/12=1.

    Negative components clamp to zero. If max(r,g,b)>255 all components scale by
    255/max before truncation, preserving hue rather than clipping channels separately.
    TRANS33/66 and WARP never receive a ref_gl surface lightmap.
    """
    if face['unlit']:
        return np.full((1, 1, 3), 255, np.uint8)
    w, h = map(int, face['size'])
    if min(w, h) <= 0 or w * h > 1_000_000:
        raise ValueError('Invalid RGB lightmap extent')
    offset = face['lightofs']
    if offset == -1 or not lighting:
        return np.full((h, w, 3), 255, np.uint8)
    if offset < 0:
        raise ValueError('Invalid RGB lightmap offset')
    total = np.zeros((h * w, 3), np.float32)  # Match blocklights and normalization scale's C float precision.
    for style in face['styles']:
        if style == 255:
            break
        size = w * h * 3
        if offset + size > len(lighting):
            raise ValueError(f"Face {face['number']} RGB lightmap exceeds lighting lump")
        if style not in dark:
            total += np.frombuffer(lighting[offset:offset + size], np.uint8).reshape(-1, 3)
        offset += size
    total = np.maximum(total, 0)
    maximum = total.max(axis=1)
    bright = maximum > 255
    total[bright] *= (255 / maximum[bright])[:, None]
    return total.astype(np.uint8).reshape(h, w, 3)


def angles(entity):
    return vector(entity, 'angles') if 'angles' in entity else np.array([0., float(entity.get('angle', '0')), 0.])


def movedir(entity):
    a = angles(entity)
    if a[1] in (-1, -2):
        return np.array([0., 0., 1. if a[1] == -1 else -1.])
    return rotation(a)[:, 0]


def excluded(entity, game):
    return bool(int(entity.get('spawnflags') or 0) & (2048 if game == 'ctf' else 512))


def ordered_starts(bsp, game):
    starts = [e for e in bsp['entities'] if e.get('classname') == 'info_player_start']
    ordered = [e for e in starts if not e.get('targetname')] + [e for e in starts if e.get('targetname')]
    if game == 'ctf':
        # CTFSelectSpawnPoint chooses a team spot on entry; make the red team's first
        # authored spot deterministic, with all other team/DM spots available to inspect.
        ordered = [e for cls in ('info_player_team1', 'info_player_team2')
                   for e in bsp['entities'] if e.get('classname') == cls]
    for cls in ('info_player_deathmatch', 'info_player_intermission'):
        ordered += [e for e in bsp['entities'] if e.get('classname') == cls]
    return [e for e in ordered if not excluded(e, game)]


def instances(bsp, game='quake2'):
    """Initial brush states: medium SP, or deathmatch filtering for CTF; motion frozen."""
    result = [dict(model=0, entity=0, classname='worldspawn', offset=[0., 0., 0.], angles=[0., 0., 0.], state='world', guess=False)]
    skipped = []
    invisible = {'func_areaportal', 'func_killbox', 'func_timer'}
    known = {'func_door', 'func_door_rotating', 'func_door_secret', 'func_plat', 'func_train', 'func_rotating',
             'func_wall', 'func_explosive', 'func_object', 'func_water', 'func_conveyor', 'func_button',
             'turret_base', 'turret_breach', 'target_character'}
    for index, entity in enumerate(bsp['entities']):
        cls, model = entity.get('classname', ''), entity.get('model', '')
        if not model.startswith('*') and not cls.startswith('func_') and cls != 'rotating_light':
            continue
        flags = int(entity.get('spawnflags') or 0)
        reason = ('excluded in deathmatch/CTF' if game == 'ctf' else 'excluded on medium skill') if excluded(entity, game) else 'trigger volume' if cls.startswith('trigger_') else ''
        if cls in invisible:
            reason = 'non-rendered game logic'
        if cls == 'func_wall' and flags & 7 and not flags & 4:
            reason = 'trigger-spawn wall without START_ON; SVF_NOCLIENT'
        if cls in ('func_explosive', 'func_object') and flags & 1:
            reason = 'TRIGGER_SPAWN; SVF_NOCLIENT until used'
        if not reason and not model.startswith('*'):
            reason = 'no inline brush model; point entities/placed MD2s are outside this preview'
        if reason:
            skipped.append(dict(entity=index, classname=cls, reason=reason, guess=False))
            continue
        number = int(model[1:])
        if not 0 < number < len(bsp['models']):
            raise ValueError(f'Invalid brush model {model}')
        bounds = bsp['models'][number]
        # CM inline-model bounds expand compiled mins/maxs by one unit, as in Quake.
        mins, size = np.array(bounds[:3]) - 1, np.array(bounds[3:6]) - np.array(bounds[:3]) + 2
        offset, turn = vector(entity), angles(entity)
        if cls in ('func_plat', 'func_train', 'func_door', 'func_water', 'func_button',
                   'func_door_secret', 'func_door_rotating', 'func_rotating'):
            turn = np.zeros(3)
        state, guess = 'authored origin before use/motion', cls not in known
        if cls == 'func_plat':
            if not entity.get('targetname'):
                offset[2] -= float(entity.get('height', '0')) or size[2] - (float(entity.get('lip', '0')) or 8)
                state = 'untargeted plat at bottom'
            else:
                state = 'targeted plat at top until used'
        elif cls == 'func_train':
            targets = [e for e in bsp['entities'] if entity.get('target') and e.get('targetname') == entity['target']
                       and not excluded(e, game)]
            if targets:
                offset = vector(targets[0]) - mins
                state, guess = 'first path corner minus inline model mins, before travel', len(targets) > 1
            else:
                state, guess = 'no first path corner: authored origin', True
        elif cls in ('func_door', 'func_water'):
            state = 'closed'
            if flags & 1:
                direction = movedir(entity)
                lip = float(entity.get('lip', '0')) or (8 if cls == 'func_door' else 0)
                offset += direction * (np.abs(direction) @ size - lip)
                state = 'START_OPEN: moved along movedir by projected size minus lip'
        elif cls == 'func_door_rotating':
            state = 'closed, zero angles'
            if flags & 1:
                axis = 2 if flags & 64 else 0 if flags & 128 else 1
                turn[axis] = (float(entity.get('distance', '0')) or 90) * (-1 if flags & 2 else 1)
                state = 'START_OPEN: distance around selected axis, REVERSE honored'
        elif cls == 'func_rotating':
            state = 'zero angle at spawn; continuous angular motion frozen'
        elif cls in ('turret_base', 'turret_breach'):
            turn = angles(entity)
            state = 'authored turret angles; no tracking or firing'
        elif cls == 'func_object':
            state = 'visible at spawn, before delayed gravity release'
        elif cls == 'target_character':
            state = 'digit brush; game starts at blank frame 12, preview holds first texture frame'
            guess = True
        elif cls == 'func_plat2':
            state = 'authored origin; expansion plat2 activation/top state not verified'
            guess = True
        result.append(dict(model=number, entity=index, classname=cls, offset=offset.tolist(), angles=turn.tolist(), state=state, guess=guess))
    open_doors_at_start(bsp, result, game)
    return result, skipped


def open_doors_at_start(bsp, result, game='quake2'):
    """Doors without targetname or health get a trigger field (their team's bounds + 60 units sideways,
    Think_SpawnDoorTrigger): a player who starts inside it opens the whole team at once (ware1, checked in
    Yamagi Quake II's gl1)."""
    starts = ordered_starts(bsp, game)
    if game != 'ctf':
        starts = [e for e in starts if e['classname'] == 'info_player_start']
    start = starts[0] if starts and starts[0]['classname'] != 'info_player_intermission' else None
    if start is None:
        return
    low, high = vector(start) + (-16, -16, -24), vector(start) + (16, 16, 32)  # The player's box.
    teams = {}
    for item in result:
        entity = bsp['entities'][item['entity']]
        if (item['classname'] in ('func_door', 'func_door_rotating') and item['state'].startswith('closed')
                and not entity.get('targetname') and not float(entity.get('health', '0'))):
            teams.setdefault(entity.get('team') or item['entity'], []).append(item)
    for team in teams.values():
        boxes = np.array([np.array(bsp['models'][item['model']][:6], float).reshape(2, 3) + item['offset'] for item in team])
        field_low, field_high = boxes[:, 0].min(axis=0) - (60, 60, 0), boxes[:, 1].max(axis=0) + (60, 60, 0)
        if not ((low < field_high) & (high > field_low)).all():
            continue
        for item in team:
            entity, bounds = bsp['entities'][item['entity']], bsp['models'][item['model']]
            flags, size = int(entity.get('spawnflags') or 0), np.array(bounds[3:6]) - np.array(bounds[:3]) + 2
            if item['classname'] == 'func_door':
                direction = movedir(entity)
                lip = float(entity.get('lip', '0')) or 8
                item['offset'] = (np.array(item['offset']) + direction * (np.abs(direction) @ size - lip)).tolist()
            else:
                axis = 2 if flags & 64 else 0 if flags & 128 else 1
                item['angles'][axis] = (float(entity.get('distance', '0')) or 90) * (-1 if flags & 2 else 1)
            item['state'] = 'open: the player starts inside its trigger field'


def viewpoints(bsp, floors, game='quake2'):
    ordered = ordered_starts(bsp, game)
    views = []
    for entity in ordered:
        origin, turn = vector(entity), angles(entity)
        start = origin.copy()
        camera = entity['classname'] == 'info_player_intermission'
        floor = None if camera else floor_below(floors, *origin)
        if not camera:
            origin[2] += 22
            if floor is not None:
                origin[2] = min(origin[2], floor + 46)
        views.append(dict(origin=(origin[[1, 2, 0]] * SCALE).tolist(), native_origin=origin.tolist(),
                          authored_origin=start.tolist(), yaw=-float(turn[1]), pitch=-float(turn[0]), roll=float(turn[2]),
                          classname=entity['classname'], targetname=entity.get('targetname', ''), floor_z=floor,
                          eye_above_floor=float(origin[2] - floor) if floor is not None else None))
    if not views:
        raise ValueError('No player/deathmatch/intermission viewpoint')
    return views


def warp_polygons(points):
    """gl_warp.c SubdividePolygon's 64-unit cuts, keeping eight units off an edge."""
    for axis in range(3):
        low, high = points[:, axis].min(), points[:, axis].max()
        middle = 64 * math.floor((low + high) / 128 + .5)
        if high - middle < 8 or middle - low < 8:
            continue
        front, back = [], []
        for a, b in zip(points, np.roll(points, -1, axis=0)):
            da, db = a[axis] - middle, b[axis] - middle
            if da >= 0:
                front.append(a)
            if da <= 0:
                back.append(a)
            if da * db < 0:
                cut = a + (b - a) * (da / (da - db))
                front.append(cut)
                back.append(cut)
        return warp_polygons(np.array(front)) + warp_polygons(np.array(back))
    # Ref_gl's fan has a centre vertex (whose texture coordinate also gets warped).
    center = points.mean(axis=0)
    return [np.array([center, a, b]) for a, b in zip(points, np.roll(points, -1, axis=0))]


def geometry(bsp, placed, game='quake2'):
    dark = frozenset(int(e['style']) for e in bsp['entities'] if e.get('classname', '').startswith('light')
                     and int(e.get('style', '0')) >= 32 and int(e.get('spawnflags') or 0) & 1
                     and not excluded(e, game) and game != 'ctf')  # SP_light frees lights in deathmatch.
    faces, tiles, audit, floors = [], [], [], []
    for instance in placed:
        first, count = bsp['models'][instance['model']][10:12]
        if first < 0 or count < 0 or first + count > len(bsp['faces']):
            raise ValueError('Invalid BSP38 model face range')
        matrix = rotation(instance['angles'])
        for number in range(first, first + count):
            face = face_data(bsp, number)
            if face['kind'] == 'hidden':
                continue
            points = face['points'] @ matrix.T + instance['offset']
            if face['kind'] == 'turbulent':
                vectors = np.array(bsp['texinfo'][bsp['faces'][number][4]][:8]).reshape(2, 4)
                for polygon in warp_polygons(face['points']):
                    faces.append(dict(face, points=polygon, placed_points=polygon @ matrix.T + instance['offset'],
                                      st=polygon @ vectors[:, :3].T))  # SubdividePolygon omits texinfo offsets for warp ST.
                    tiles.append(lightmap_rgb(face, bsp['lighting'], dark))
            else:
                faces.append(dict(face, placed_points=points))
                tiles.append(lightmap_rgb(face, bsp['lighting'], dark))
            audit.append(dict(face=number, entity=instance['entity'], texture=face['texture']['name'], kind=face['kind'],
                              flags=face['flags'], unlit=face['unlit'], alpha=face['alpha'], styles=face['styles'],
                              texturemins=face['mins'].tolist(), luxels=face['size'].tolist(), lightofs=face['lightofs'],
                              nexttexinfo=face['nexttexinfo'], missing=face['texture'].get('missing', False)))
            if face['kind'] == 'normal' and (matrix @ face['normal'])[2] > .7:
                floors.extend((points[0], points[k], points[k + 1]) for k in range(1, len(points) - 1))
    if not faces:
        raise ValueError('No visible BSP38 faces')
    width, height, places = pack([(t.shape[1], t.shape[0]) for t in tiles])
    atlas = np.zeros((height, width, 3), np.uint8)
    positions, uvs, uv2, groups, textures = [], [], [], {}, {}
    for index, face in enumerate(faces):
        tex = face['texture']
        png = hashlib.sha256(struct.pack('<II', tex['width'], tex['height']) + tex['pixels']).hexdigest()[:24] + '.png'
        textures[png] = tex
        group = groups.setdefault((png, face['flags']), dict(texture=png, kind=face['kind'], flags=face['flags'],
            size=[tex['width'], tex['height']], unlit=face['unlit'], alpha=face['alpha'], indices=[]))
        base = len(positions)
        positions.extend(face['placed_points'])
        uvs.extend(face['st'] / [tex['width'], tex['height']])
        for k in range(1, len(face['points']) - 1):
            group['indices'].extend((base, base + k, base + k + 1))
        x, y = places[index]
        tile = tiles[index]
        h, w = tile.shape[:2]
        atlas[y:y + h + 2, x:x + w + 2] = np.pad(tile, ((1, 1), (1, 1), (0, 0)), mode='edge')
        coords = (face['st'] - face['mins']) / 16 if not face['unlit'] else np.zeros_like(face['st'])
        uv2.extend((coords + [x + 1.5, y + 1.5]) / [width, height])
    indices, table = [], []
    for group in groups.values():
        part = group.pop('indices')
        table.append(dict(group, start=len(indices), count=len(part)))
        indices.extend(part)
    return dict(points=np.array(positions), uvs=np.array(uvs), uv2=np.array(uv2), indices=indices, groups=table,
                textures=textures, atlas=Image.fromarray(atlas), audit=audit, floors=np.array(floors), dark=sorted(dark))


def skybox(info, files):
    name = info.get('sky') or 'unit1_'
    images, sources, missing = {}, {}, []
    for suffix in ('rt', 'bk', 'lf', 'ft', 'up', 'dn'):
        # Default ref_gl uses TGA; PCX is the paletted-texture extension path, also a useful fallback.
        candidates = [asset_name(f'env/{name}{suffix}.{ext}') for ext in ('tga', 'pcx')]
        path = next((p for p in candidates if p in files), '')
        if not path:
            image = Image.new('RGB', (16, 16), '#ff00ff')
            missing.append(candidates[0])
        elif path.endswith('.pcx'):
            image = read_pcx(files[path][0])
        else:
            try:
                with Image.open(io.BytesIO(files[path][0])) as src:
                    image = src.convert('RGB')
            except (OSError, ValueError) as exc:
                raise ValueError(f'Invalid sky TGA {path}: {exc}') from exc
        png = 'sky_' + hashlib.sha256(image.tobytes() + struct.pack('<II', *image.size)).hexdigest()[:24] + '.png'
        images[png] = image
        sources[suffix] = dict(texture=png, source=path, fallback=bool(path and path.endswith('.pcx')))
    axis = vector(info, 'skyaxis') if 'skyaxis' in info else np.array([0., 0., 1.])
    return dict(name=name, rotate=float(info.get('skyrotate', '0')), axis=axis.tolist(), faces=sources, missing=missing), images


def build_map(raw, name, files, palette, game='quake2'):
    bsp = read_bsp(raw, files)
    placed, skipped = instances(bsp, game)
    data = geometry(bsp, placed, game)
    points = data['points'][:, [1, 2, 0]] * SCALE
    views = viewpoints(bsp, data['floors'], game)
    info = next((e for e in bsp['entities'] if e.get('classname') == 'worldspawn'), {})
    sky, images = skybox(info, files)
    for png, tex in data['textures'].items():
        images[png] = upload_texture(tex, palette)
    blob = b''.join((points.astype('<f4').tobytes(), data['uvs'].astype('<f4').tobytes(),
                     data['uv2'].astype('<f4').tobytes(), np.array(data['indices'], '<u4').tobytes()))
    scene = dict(name=name, title=info.get('message', ''), game=game, format='quake2-bsp38',
                 vertices=len(points), indices=len(data['indices']), lightmap=list(data['atlas'].size),
                 groups=data['groups'], viewpoints=views, bounds=np.array(bsp['models'][0][:6]).reshape(2, 3).tolist(),
                 instances=placed, skipped_entities=skipped, faces=data['audit'], skybox=sky,
                 entity_classes=dict(Counter(e.get('classname', '') for e in bsp['entities'])), dark_styles=data['dark'],
                 missing=sorted({f['texture'] for f in data['audit'] if f['missing'] and f['kind'] != 'sky'} | set(sky['missing'])),
                 lighting=dict(renderer='ref_gl 3.20', intensity=2, gl_modulate=1, vid_gamma=1, normal_style=1),
                 assumptions=[('CTF deathmatch filtering; first authored red team start' if game == 'ctf' else 'medium skill single player'),
                              ('styles m; SP_light removed in deathmatch' if game == 'ctf' else 'styles m except START_OFF styles a'),
                              'initial brush states, trains at first corner before travel', 'animated textures at first texinfo frame'])
    return scene, blob, images, data['atlas']


def import_maps(install, output, replace=False, game='quake2'):
    files, records, counts = read_install(install, maps=True, game=game)
    colormap = files.get('pics/colormap.pcx', (b'',))[0]
    read_pcx(colormap)  # Validate the palette source before slicing its trailing palette.
    palette = colormap[-768:]
    output = Path(output)
    if game != 'quake2':
        output /= game
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    result = dict(game=game, imported=[], skipped=[], failed={}, bsp_entries=len(records), pak_counts=counts, results=records)
    index = []
    for record in records:
        name = record['source']
        if record['status'] != 'pending':
            continue
        if not name.startswith('maps/'):
            record.update(status='skipped', reason='Not a maps/*.bsp level')
            result['skipped'].append(name)
            continue
        ident = safe(name.removeprefix('maps/').removesuffix('.bsp'))
        folder = output / 'maps' / ident
        try:
            if not replace and all((folder / f).is_file() for f in ('scene.json', 'geometry.bin', 'lightmap.png')):
                scene = json.loads((folder / 'scene.json').read_text(encoding='utf-8'))
                record.update(status='skipped', reason='Already imported; use --replace to rebuild')
                result['skipped'].append(name)
            else:
                scene, blob, images, atlas = build_map(files[name][0], ident, files, palette, game)
                for png, image in images.items():
                    target = output / 'textures' / png
                    if replace or not target.is_file():
                        image.save(target)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'geometry.bin').write_bytes(blob)
                atlas.save(folder / 'lightmap.png')
                (folder / 'scene.json').write_text(json.dumps(scene, separators=(',', ':')), encoding='utf-8')
                record['status'] = 'imported'
                result['imported'].append(ident)
            if scene['missing']:
                record['warnings'] = scene['missing']
            index.append(dict(id=ident, name=ident, title=scene['title'], group=GAMES[game]))
        except (ValueError, IndexError, struct.error) as exc:
            record.update(status='skipped', reason=str(exc))
            result['failed'][name] = str(exc)
    (output / 'index.json').write_text(json.dumps(sorted(index, key=lambda e: (e['id'] != 'base1', e['id'])), indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--game', choices=['all', *GAMES], default='all', help='Default: baseq2 and every installed classic pack')
    args = parser.parse_args()
    output = args.output or LOCAL_DATA / 'quake2-maps'
    if args.game == 'all':
        result = {game: import_maps(args.install, output, args.replace, game) for game in available_games(args.install)}
        (output / 'import-packs-report.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
    else:
        result = import_maps(args.install, output, args.replace, args.game)
    print(json.dumps(result, indent=1))

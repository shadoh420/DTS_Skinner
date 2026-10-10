"""Quake BSP29/BSP2/2PSB maps and BSP item boxes. No placed MDLs or gameplay simulation.

python tools/import_quake_map.py --install "C:/Program Files (x86)/Steam/steamapps/common/Quake" [--replace]
Use --game qextras|dopa|mg1|mg3|qctf for rerelease maps; default all includes installed classic and rerelease games.

The pack uses the Unreal page's position/UV/UV2/index binary layout. Classic maps sample indexed textures through
the software colormap; rerelease QLIT maps use RGB light / 128, with fullbrights unlit. Source units map to
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
    from tools.import_quake import GAMES, RERELEASE, available_games, item_box, palette_image, read_install, safe
    from tools.import_unreal_map import floor_below, pack
    from tools.local_data import LOCAL_DATA
except ImportError:
    from import_quake import GAMES, RERELEASE, available_games, item_box, palette_image, read_install, safe
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
    if len(data) < 124 or data[:4] not in (struct.pack('<i', 29), b'BSP2', b'2PSB'):
        raise ValueError('Expected Quake BSP29, BSP2 or 2PSB')
    extended = data[:4] in (b'BSP2', b'2PSB')
    bounds = '6f' if data[:4] == b'BSP2' else '6h'
    lumps = []
    end = 124
    for offset, size in struct.iter_unpack('<ii', data[4:124]):
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('Invalid BSP lump range')
        lumps.append(data[offset:offset + size])
        end = max(end, offset + size)

    def rows(index, fmt):
        if len(lumps[index]) % struct.calcsize('<' + fmt):
            raise ValueError(f'Incomplete BSP lump {index}')
        return list(struct.iter_unpack('<' + fmt, lumps[index]))

    bsp = dict(entities=entities(lumps[0]), planes=rows(1, '4fi'), points=rows(3, '3f'),
               texinfo=rows(6, '8fii'), faces=rows(7, '5i4Bi' if extended else 'Hhihh4Bi'), lighting=lumps[8],
               nodes=rows(5, '3i' + bounds + '2I' if extended else 'i2h6h2H'),
               clipnodes=rows(9, '3i' if extended else 'i2h'),
               leaves=rows(10, '2i' + bounds + '2I4B' if extended else '2i6h2H4B'),
               marksurfaces=[r[0] for r in rows(11, 'I' if extended else 'H')],
               edges=rows(12, '2I' if extended else '2H'), surfedges=[r[0] for r in rows(13, 'i')], models=rows(14, '9f7i'),
               format='quake-' + (data[:4].decode('ascii').lower() if extended else 'bsp29'), bspx={})
    end = (end + 3) & ~3
    if data[end:end + 4] == b'BSPX':
        if end + 8 > len(data):
            raise ValueError('Truncated BSPX header')
        count = struct.unpack_from('<i', data, end + 4)[0]
        if count < 0 or end + 8 + count * 32 > len(data):
            raise ValueError('Invalid BSPX directory')
        for name, offset, size in struct.iter_unpack('<24sii', data[end + 8:end + 8 + count * 32]):
            name = name.split(b'\0')[0].decode('ascii')
            if name in bsp['bspx'] or offset < 0 or size < 0 or offset + size > len(data):
                raise ValueError('Invalid BSPX lump')
            bsp['bspx'][name] = data[offset:offset + size]
    if not bsp['models']:
        raise ValueError('BSP has no world model')
    for key in ('planes', 'points', 'texinfo', 'models', 'nodes', 'leaves'):
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


def colored_lighting(bsp, lit=None):
    """QLIT v1 uses three RGB bytes for each byte of the mono lighting lump, including style planes."""
    rgb = None
    source = 'colormap'
    if lit is not None:
        if len(lit) < 8 or lit[:8] != b'QLIT\x01\0\0\0':
            raise ValueError('Expected QLIT version 1')
        rgb, source = lit[8:], '.lit'
    elif 'RGBLIGHTING' in bsp['bspx']:
        rgb, source = bsp['bspx']['RGBLIGHTING'], 'BSPX RGBLIGHTING'
    if rgb is not None:
        if len(rgb) != 3 * len(bsp['lighting']):
            raise ValueError('RGB lighting length must be three times the BSP lighting length')
        bsp['rgb_lighting'] = rgb
    return source


def light_rgb(face, lighting, dark=frozenset()):
    """RGB analogue of normal style 264/256, capped to 255; shader applies texel * light / 128."""
    w, h = map(int, face['size'])
    if face['kind'] != 'normal' or not lighting:
        return np.full((1, 1, 3), 128, dtype=np.uint8)
    if w <= 0 or h <= 0 or w * h > 1_000_000:
        raise ValueError('Invalid lightmap extent')
    total = np.zeros(w * h * 3, dtype=np.int64)
    offset = face['lightofs'] * 3
    if offset >= 0:
        for style in face['styles']:
            if style == 255:
                break
            if offset + w * h * 3 > len(lighting):
                raise ValueError(f"Face {face['number']} exceeds RGB lighting lump")
            if style not in dark:
                total += np.frombuffer(lighting[offset:offset + w * h * 3], np.uint8).astype(np.int64) * 264
            offset += w * h * 3
    return np.minimum(255, total >> 8).astype(np.uint8).reshape(h, w, 3)


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
    if bsp.get('rerelease'):
        # Native float arithmetic matters at exact luxel boundaries: mgdm3's -0.8 * 440 rounds to -352,
        # whereas promoting the stored float to double invents an extra column at -352.000005.
        p, v = points.astype(np.float32), vectors.astype(np.float32)
        st = ((p[:, 0, None] * v[:, 0] + p[:, 1, None] * v[:, 1]) + p[:, 2, None] * v[:, 2]) + v[:, 3]
    # CalcSurfaceExtents: floor/ceil texture-space bounds at 16 units, including both endpoint luxels.
    lo, hi = np.floor(st.min(axis=0) / 16).astype(int), np.ceil(st.max(axis=0) / 16).astype(int)
    return dict(number=number, points=points, normal=normal, st=st, mins=lo * 16, size=hi - lo + 1,
                texture=texture, kind=surface_kind(texture['name']), styles=tail[:4], lightofs=tail[4])


def light_codes(face, lighting, dark=frozenset()):
    """Software R_BuildLightMap's fixed-point colormap coordinate, before luxel interpolation.

    Present styles use 'm': (ord('m')-ord('a'))*22 = 264, except `dark` ones, switchable lights that start off
    ('a' = 0; hipend's start, checked in WinQuake). VID_CBITS=6, hence the >>2 conversion;
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
            if style not in dark:
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
    values = [float(x.rstrip(',')) for x in (entity.get(key) or '0 0 0').split()]
    if not 1 <= len(values) <= 3 or not all(math.isfinite(v) for v in values):
        raise ValueError(f'Invalid entity {key}')
    # ED_ParseEpair leaves omitted vector components zero (r1m4 has an intermission mangle of "20").
    return np.array(values + [0.] * (3 - len(values)))


def rotation(angles):
    pitch, yaw, roll = np.radians(angles)
    cp, sp, cy, sy, cr, sr = math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw), math.cos(roll), math.sin(roll)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def instances(bsp, game='quake'):
    """Normal-skill single player, no runes; brush spawn positions after train initialization."""
    result = [dict(model=0, entity=0, classname='worldspawn', offset=[0., 0., 0.], state='world')]
    skipped = []
    for index, entity in enumerate(bsp['entities']):
        model = entity.get('model', '')
        if not model.startswith('*'):
            continue
        classname = entity.get('classname', '')
        flags = int(float(entity.get('spawnflags') or '0'))
        reason = 'trigger volume' if classname.startswith('trigger_') else 'excluded on normal skill' if flags & 512 else None
        if game == 'qctf':
            reason = 'trigger volume' if classname.startswith('trigger_') else 'excluded in deathmatch' if flags & 2048 else None
        if game in RERELEASE and not classname:
            reason = 'entity without classname; engine discards it'
        if game in ('dopa', 'mg1', 'mg3') and classname == 'hub_trigger_changelevel':
            reason = 'hub changelevel trigger volume'
        if game == 'mg3' and classname in ('func_door', 'func_door_secret') and flags & 32768:
            reason = 'RemovedOutsideCoop: COOP_ONLY (32768); single player'
        if game in ('dopa', 'mg1') and classname == 'func_bossgate' and flags & 64:
            reason = 'misc.qc: inverse boss gate (64) removed with no runes'
        if classname.startswith('monster_'):
            reason = 'QuakeC replaces the authored brush with an MDL; placed monsters are not included'
        if game == 'hipnotic':
            if classname in ('func_particlefield', 'func_multi_exploder', 'path_follow', 'func_togglewall'):
                reason = 'Hipnotic QuakeC clears model/modelindex; non-rendered volume'
            if classname == 'func_movewall' and not flags & 1:
                reason = 'Hipnotic collision helper without VISIBLE (1); QuakeC clears model'
        if game == 'rogue' and classname == 'func_ctf_wall':
            reason = 'Rogue QuakeC removes CTF walls outside CTF; preview uses single player'
        if reason:
            skipped.append(dict(entity=index, classname=classname, reason=reason))
            continue
        number = int(model[1:])
        if not 0 < number < len(bsp['models']):
            raise ValueError(f'Invalid brush model {model}')
        bounds = bsp['models'][number]
        mins, size = np.array(bounds[:3]) - 1, np.array(bounds[3:6]) - np.array(bounds[:3]) + 2
        offset, state = vector(entity), 'authored origin; movement angles cleared'
        angles = np.zeros(3)
        guess = classname not in ('func_wall', 'func_button', 'func_door', 'func_door_secret', 'func_plat', 'func_train', 'func_illusionary')
        if classname == 'func_plat' and not entity.get('targetname'):
            offset[2] -= float((entity.get('height') or '0')) or size[2] - 8
            state = 'bottom (untargeted plat); height or size_z - 8'
        elif classname == 'func_train' or (game == 'hipnotic' and classname == 'func_train2'):
            target = next((e for e in bsp['entities'] if e.get('targetname') == entity.get('target') and entity.get('target')), None)
            if target is None:
                raise ValueError(f'func_train {index} has no first path corner')
            offset = vector(target) - mins
            state = 'first path corner minus model mins (after initialization, before travel)'
            guess = False
        elif classname == 'func_door' and flags & 1:
            angle = float(entity.get('angle') or '0')
            direction = np.array([0, 0, 1 if angle == -1 else -1]) if angle in (-1, -2) else np.array([math.cos(math.radians(angle)), math.sin(math.radians(angle)), 0])
            lip = float((entity.get('lip') or '0')) or 8
            offset += direction * (abs(float(direction @ size)) - lip)
            state = 'DOOR_START_OPEN; moved by projected size minus lip'
        elif classname == 'func_door':
            state = 'closed'
        elif game in ('dopa', 'mg1') and classname == 'func_bossgate':
            state, guess = 'misc.qc: boss gate at authored origin with no runes', False
        elif game in ('dopa', 'mg1', 'mg3') and classname == 'rotate_object_continuously':
            if np.any(vector(entity, 'pos2')):
                offset = vector(entity, 'pos2')
            state, guess = 'rotate.qc: pos2 if nonzero, otherwise compiled origin; zero angles before rotation', False
        elif game in ('dopa', 'mg1', 'mg3') and classname in ('func_explode', 'func_hurt', 'func_toss', 'func_bob', 'func_breakable'):
            # The spawn functions setmodel/setorigin at the authored position. Bob/toss movement is frozen before ticks.
            state, guess = 'progs.dat: authored origin before bob/toss/break/explode; movement frozen', False
            if classname in ('func_hurt', 'func_toss', 'func_bob', 'func_breakable'):
                angles = vector(entity, 'angles')
        elif game in ('dopa', 'mg1', 'mg3') and classname in ('mge2m2_electrode_button', 'func_axe_button'):
            state, guess = 'progs.dat: calls func_button; unpressed at authored origin', False
        elif game == 'rogue' and classname == 'func_new_plat':
            height = float((entity.get('height') or '0'))
            if (flags & 3 or (not flags & 4 and flags & 16)) and height <= 0:
                offset[2] -= abs(height) or size[2] - 8
                state = 'newplats.qc: negative/default height starts at pos2 (bottom)'
            else:
                state = 'newplats.qc: positive height or elevator stays at authored origin'
            guess = False
        elif game == 'rogue' and classname == 'func_elvtr_button':
            state, guess = 'elevator button unpressed at authored origin', False
        elif game == 'hipnotic' and classname in ('func_breakawaywall', 'func_bobbingwater'):
            state, guess = 'authored origin before breaking/bobbing', False
        elif game == 'hipnotic' and classname in ('rotate_object', 'func_movewall'):
            # Hipnotic QBSP has already made rotate_object vertices relative to info_rotate.
            # Adding its compiled origin once restores the authored rest position; do not subtract the pivot again.
            state, guess = 'compiled pivot origin, zero rotation at rest', False
            controller = next((e for e in bsp['entities'] if e.get('classname', '').startswith('func_rotate_')
                               and e.get('target') and e['target'] == entity.get('targetname')
                               and not int(float(e.get('spawnflags') or '0')) & 512), None)
            if controller and controller['classname'] == 'func_rotate_train':
                target = next((e for e in bsp['entities'] if e.get('targetname') == controller.get('path') and controller.get('path')), None)
                if target is None:
                    state, guess = 'authored origin; rotating train has no first path_rotate', True
                else:
                    offset += vector(target) - vector(controller)
                    if classname == 'rotate_object':
                        angles = vector(target, 'angles') if int(float(target.get('spawnflags') or '0')) & 2 else vector(controller, 'angles')
                    state = 'hiprot.qc: first path_rotate translation; rotate_object uses initial path/controller angles'
            elif classname == 'rotate_object':
                angles = vector(entity, 'angles')
            else:
                state = 'VISIBLE collision helper at authored origin'
        result.append(dict(model=number, entity=index, classname=classname, offset=offset.tolist(), angles=angles.tolist(), state=state, guess=guess))
    return result, skipped


def viewpoints(bsp, game='quake'):
    views = []
    classes = ('info_player_start', 'info_player_deathmatch', 'info_intermission')
    if game == 'qctf':
        classes = ('info_player_team1', 'info_player_team2', 'info_player_deathmatch', 'info_player_start', 'info_intermission')
    for classname in classes:
        for entity in bsp['entities']:
            if entity.get('classname') != classname:
                continue
            if game in RERELEASE and int(float(entity.get('spawnflags') or '0')) & (2048 if game == 'qctf' else 512):
                continue
            origin = vector(entity)
            origin[2] += 0 if classname == 'info_intermission' else 22
            angle = float(entity.get('angle') or '0')
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
    # QuakeC's light spawn: a targeted light flagged START_OFF (1) sets its style to 'a' until triggered.
    dark = frozenset(int(float(e['style'])) for e in bsp['entities'] if e.get('classname', '').startswith('light')
                     and int(float(e.get('style') or '0')) >= 32 and int(float(e.get('spawnflags') or '0')) & 1)
    for instance in placed:
        model = bsp['models'][instance['model']]
        first, count = model[14:16]
        if first < 0 or count < 0 or first + count > len(bsp['faces']):
            raise ValueError('Invalid model face range')
        matrix = rotation(instance.get('angles', [0, 0, 0]))
        for number in range(first, first + count):
            face = face_data(bsp, number)
            if face['kind'] == 'hidden':
                continue
            points = face['points'] @ matrix.T + instance['offset']
            face['placed_points'] = points
            if light:
                tiles.append(light_rgb(face, bsp['rgb_lighting'], dark) if 'rgb_lighting' in bsp
                             else light_codes(face, bsp['lighting'], dark))
            faces.append(face)
            audit.append(dict(face=number, entity=instance['entity'], texture=face['texture']['name'], kind=face['kind'],
                              texturemins=face['mins'].tolist(), luxels=face['size'].tolist(), lightofs=face['lightofs'],
                              styles=face['styles'], missing=face['texture'].get('missing', False)))
            if face['kind'] == 'normal' and (matrix @ face['normal'])[2] > 0:
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
            rgb = tile if tile.ndim == 3 else np.stack((tile >> 8, tile & 255, np.zeros_like(tile)), axis=-1).astype(np.uint8)
            h, w = tile.shape[:2]
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


def hull_landing(bsp, origin, hull=1):
    """Origin height where the player's box (the world's hull 1, clip brushes included) comes to rest falling from
    `origin`, or None when it starts in solid. Faces alone miss invisible clip floors (mg1's mge2m1 start)."""
    def contents(point):
        node = bsp['models'][0][9 + hull]
        while node >= 0:
            plane, front, back = bsp['clipnodes'][node]
            *normal, dist, kind = bsp['planes'][plane]
            side = (point[kind] if kind < 3 else np.dot(normal, point)) - dist
            node = front if side >= 0 else back
        return node
    point = np.array(origin, float)
    if not 0 <= bsp['models'][0][9 + hull] < len(bsp['clipnodes']) or contents(point) == -2:  # No hull, or CONTENTS_SOLID.
        return None
    while point[2] > -32768 and contents(point - (0, 0, 1)) != -2:
        point[2] -= 1  # ponytail: 1-unit march, a few thousand node walks at most per start.
    return point[2]


def build_map(raw, name, game='quake', lit=None):
    bsp = read_bsp(raw)
    bsp['rerelease'] = game in RERELEASE
    lighting = colored_lighting(bsp, lit)
    placed, skipped = instances(bsp, game)
    data = geometry(bsp, placed)
    points = data['points'][:, [1, 2, 0]] * SCALE
    views = viewpoints(bsp, game)

    def floor_under(eye):
        """Brush entities' floors come from faces; the world's own, clip brushes included, from hull 1."""
        floor = floor_below(data['floors'], *eye)
        landing = hull_landing(bsp, np.array(eye) - (0, 0, 22)) if floor is not None else None
        return max(floor, landing - 24) if landing is not None else floor
    for view in views:
        # The game drops a spawned player onto the floor below (standing at floor + 24, eyes 22 higher): e1m8's and
        # hipend's starts float well above it (checked in WinQuake). Intermission cameras stay where they are.
        origin = np.array(view['native_origin'])
        floor = floor_under(origin) if view['classname'] != 'info_intermission' else None
        if floor is not None and origin[2] > floor + 46:
            origin[2] = floor + 46
            view.update(origin=(origin[[1, 2, 0]] * SCALE).tolist(), native_origin=origin.tolist())
    bounds = np.array(bsp['models'][0][:6]).reshape(2, 3)
    checks = []
    for view in views:
        origin = view['native_origin']
        floor = floor_under(origin)
        checks.append(dict(classname=view['classname'], inside_bounds=bool(((origin >= bounds[0]) & (origin <= bounds[1])).all()),
                           floor_z=floor, eye_above_floor=origin[2] - floor if floor is not None else None))
    blob = b''.join((points.astype('<f4').tobytes(), data['uvs'].astype('<f4').tobytes(),
                     data['uv2'].astype('<f4').tobytes(), np.array(data['indices'], '<u4').tobytes()))
    info = next((e for e in bsp['entities'] if e.get('classname') == 'worldspawn'), {})
    scene = dict(name=name, game=game, title=info.get('message', ''), format=bsp['format'], vertices=len(points), indices=len(data['indices']),
                 lighting='rgb' if 'rgb_lighting' in bsp else 'colormap', lighting_source=lighting,
                 bspx=[dict(name=n, bytes=len(d), handled=n == 'RGBLIGHTING' and lighting == 'BSPX RGBLIGHTING',
                            reason='external .lit takes precedence' if n == 'RGBLIGHTING' and lit is not None else
                            'RGB lighting' if n == 'RGBLIGHTING' else 'not handled') for n, d in bsp['bspx'].items()],
                 lightmap=list(data['atlas'].size), groups=data['groups'], viewpoints=views, bounds=bounds.tolist(),
                 instances=placed, skipped_entities=skipped, faces=data['audit'], viewpoint_checks=checks, missing=sorted({f['texture'] for f in data['audit'] if f['missing']}),
                 assumptions=['CTF deathmatch filtering; first authored team 1 start' if game == 'qctf' else 'normal skill, single player, no runes', 'light styles held at m (264), switchable lights that start off at a (0)',
                              'brushes at spawn; trains initialized at first corner, before travel', 'animated textures held at +0/+a'])
    return scene, blob, data['textures'], data['atlas']


def rerelease_sky(info, files):
    """Worldspawn _sky names the gfx/env/<prefix>{rt,bk,lf,ft,up,dn}.tga set in mg1/mg3."""
    name = info.get('_sky') or info.get('sky')
    if not name:
        return None, {}
    if __package__:
        from .import_quake2_map import skybox
    else:
        from import_quake2_map import skybox
    sky, images = skybox(dict(sky=name), {n.removeprefix('gfx/'): d for n, d in files.items() if n.startswith('gfx/env/')})
    for face in sky['faces'].values():
        if face['source']:
            face['source'] = 'gfx/' + face['source']
    sky['lookup'] = 'worldspawn _sky/sky; gfx/env/<prefix><suffix>.tga'
    return sky, images


def import_maps(install, output, replace=False, game='quake'):
    files, records, counts = read_install(install, game)
    output = Path(output)
    if game != 'quake':
        output /= game
    palette = files['gfx/palette.lmp'][0]
    colormap = files.get('gfx/colormap.lmp', (b'',))[0]
    lut = colormap_image(colormap, palette)
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    lut.save(output / 'textures/colormap.png')
    curve = brightness_curve(colormap, palette)
    (output / 'brightness.json').write_text(json.dumps(curve, indent=2), encoding='utf-8')
    external_assets = [dict(source=r['source'], kind='skybox face' if r['source'].startswith('gfx/env/') else
                            'model skin; not applied to BSP miptex or embedded MDL skin')
                       for r in records if r['source'].endswith('.tga')]
    records = [r for r in records if r['source'].endswith('.bsp')]
    result = dict(game=game, imported=[], skipped=[], failed={}, pak_counts=counts, bsp_entries=len(records), results=records,
                  palette_source=files['gfx/palette.lmp'][2]['game'], colormap_source=files['gfx/colormap.lmp'][2]['game'],
                  external_assets=external_assets)
    index = []
    for record in records:
        name = record['source']
        raw = files[name][0]
        record['format'] = {b'BSP2': 'BSP2', b'2PSB': '2PSB', b'\x1d\0\0\0': 'BSP29'}.get(raw[:4], 'unknown')
        if record['status'] == 'overridden':
            continue
        reason = None
        if item_box(name) or not name.startswith('maps/'):
            reason = 'BSP item box; imported by the quake models importer' if item_box(name) else 'Not a maps/*.bsp level (archived source/test asset)'
        elif game in RERELEASE and (name.startswith(('maps/test/', 'maps/bmodel/')) or name == 'maps/test_ctf.bsp'):
            reason = 'Test map' if not name.startswith('maps/bmodel/') else 'External brush model, not a level'
        elif game == 'qextras' and not (name.startswith('maps/vault/') or name in ('maps/dm7.bsp', 'maps/dm8.bsp', 'maps/base32b.bsp', 'maps/death32c.bsp')):
            reason = 'Classic level already represented by the classic Quake group'
        if reason:
            record.update(status='skipped', reason=reason)
            result['skipped'].append(name)
            continue
        ident = safe(name[5:-4].replace('/', '_'))
        folder = output / 'maps' / ident
        try:
            if not replace and all((folder / f).is_file() for f in ('scene.json', 'geometry.bin', 'lightmap.png')):
                scene = json.loads((folder / 'scene.json').read_text(encoding='utf-8'))
                record.update(status='skipped', reason='Already imported; use --replace to rebuild')
                result['skipped'].append(name)
            else:
                lit = files.get(name[:-4] + '.lit') if game in RERELEASE else None
                scene, blob, textures, atlas = build_map(raw, ident, game, lit[0] if lit else None)
                if game in RERELEASE:
                    bsp = read_bsp(raw)
                    info = next((e for e in bsp['entities'] if e.get('classname') == 'worldspawn'), {})
                    sky, images = rerelease_sky(info, files)
                    scene['skybox'] = sky if sky and not sky['missing'] else None
                    scene['external_textures'] = dict(sky=sky, surfaces='embedded miptex; no established external replacement lookup')
                    if sky and sky['missing']:
                        scene['missing'].extend(sky['missing'])
                    for png, image in images.items():
                        image.save(output / 'textures' / png)
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
            record.update(bspx=scene.get('bspx', []), lighting=scene.get('lighting_source', 'colormap'),
                          brush_guesses=sum(p.get('guess', False) for p in scene['instances']))
            index.append(dict(id=ident, name=ident, title=scene['title'], group=GAMES[game]))
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
    parser.add_argument('--game', choices=['all', *GAMES], default='all', help='Default: all installed classic and rerelease campaigns')
    args = parser.parse_args()
    output = args.output or LOCAL_DATA / 'quake-maps'
    if args.game == 'all':
        result = {game: import_maps(args.install, output, args.replace, game) for game in available_games(args.install)}
        (output / 'import-packs-report.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
    else:
        result = import_maps(args.install, output, args.replace, args.game)
    print(json.dumps(result, indent=1))

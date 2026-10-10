"""Daikatana BSP41 maps and static scenery, using the installed retail assets.

python tools/import_daikatana_map.py --install "C:/Program Files (x86)/Steam/steamapps/common/Daikatana/data" [--replace]

Optional --output and --filter select an output directory and map-name substring.
The preview holds spawn state, medium skill, single player, gl_modulate=1.
Native evidence and remaining rendering differences: build/daikatana/HANDOFF-maps.md.
"""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import struct

import numpy as np
from PIL import Image

try:
    from tools import import_daikatana as dk, import_quake2_map as q2
    from tools.import_quake_map import SCALE, entities, face_data as polygon, floor_below, rotation, vector
    from tools.local_data import LOCAL_DATA
except ImportError:
    import import_daikatana as dk, import_quake2_map as q2
    from import_quake_map import SCALE, entities, face_data as polygon, floor_below, rotation, vector
    from local_data import LOCAL_DATA

FULLBRIGHT, SKY, WARP, TRANS33, TRANS66, FLOWING, NODRAW = 2, 4, 8, 16, 32, 64, 128
ALPHA, MASKED, FOG = 0x40000, 0x80000, 0x1000000
DRAW_FLAGS = FULLBRIGHT | SKY | WARP | TRANS33 | TRANS66 | FLOWING | NODRAW | ALPHA | MASKED | FOG
VIEW_HEIGHT, FOOT = 22, 24
GAMMA = .799
AMBIENT_ANIMALS = ('e_seagull', 'e_goldfish', 'e_greyfish', 'e_grayfish', 'e_guppy', 'e_guppy2')
LUMPS = ('entities', 'planes', 'vertices', 'visibility', 'nodes', 'texinfo', 'faces', 'lighting',
         'leaves', 'leaffaces', 'leafbrushes', 'edges', 'surfedges', 'models', 'brushes',
         'brushsides', 'pop', 'areas', 'areaportals', 'extended_texinfo', 'plane_polys')


def number(entity, key, default=0):
    value = float(entity.get(key) or default)
    if not math.isfinite(value):
        raise ValueError(f'Non-finite entity {key}')
    return value


def excluded(entity):
    # physics.dll 1000996D..100099CD: 0x1000 easy, 0x2000 medium,
    # 0x4000 hard, 0x8000 not deathmatch. These are NOT Q2's bits.
    return bool(int(number(entity, 'spawnflags')) & 0x2000)


def checker():
    image = Image.new('RGBA', (16, 16))
    image.putdata([(255, 0, 255, 255) if (x // 8) ^ (y // 8) else (32, 32, 32, 255)
                   for y in range(16) for x in range(16)])
    return image


def palette_for(info, files):
    requested = dk.asset_name(f"textures/{info['palette']}/colormap.bmp") if info.get('palette') else 'pics/colormap.bmp'
    path = requested if requested in files else 'pics/colormap.bmp'
    if path not in files:
        return None, path
    with Image.open(io.BytesIO(dk.read_asset(files[path]))) as image:
        palette = bytes(image.getpalette() or [])
    if len(palette) != 768:
        raise ValueError(f'Invalid map palette {path}')
    return palette, path


def wal_image(raw, palette=None):
    image = dk.read_wal(raw, palette).convert('RGBA')
    # ref_gl's indexed upload makes palette entry 255 transparent.
    at = struct.unpack_from('<I', raw, 44 if raw[0] in (2, 3) else 40)[0]
    indices = np.frombuffer(raw, np.uint8, image.width * image.height, at).reshape(image.height, image.width)
    pixels = np.array(image)
    # MOD_LoadTexinfo passes it_wall=2 (1000F434). GL_LoadWal 1000834A
    # uses the map palette for non-skin images, including version 3 WALs.
    colors = palette if palette is not None else raw[120:888]
    if palette is not None:
        pixels[:, :, :3] = np.frombuffer(palette, np.uint8).reshape(256, 3)[indices]
    pixels[:, :, 3] = np.where(indices == 255, 0, 255)
    # GL_Upload8 10007DA2..10007E27 bleeds an opaque neighbour's RGB into
    # transparent texels before filtering: above, below, previous, next, index 0.
    flat, width = indices.ravel(), image.width
    transparent = np.flatnonzero(flat == 255)
    chosen, found = np.zeros(len(transparent), int), np.zeros(len(transparent), bool)
    for offset, valid in ((-width, transparent > width), (width, transparent < len(flat) - width),
                          (-1, transparent > 0), (1, transparent < len(flat) - 1)):
        neighbor = flat[np.clip(transparent + offset, 0, len(flat) - 1)]
        take = valid & ~found & (neighbor != 255)
        chosen[take], found[take] = neighbor[take], True
    if len(transparent):
        pixels.reshape(-1, 4)[transparent, :3] = np.frombuffer(colors, np.uint8).reshape(256, 3)[chosen]
    return Image.fromarray(pixels)


def read_bsp(raw, files):
    if len(raw) < 176 or raw[:4] != b'IBSP' or struct.unpack_from('<i', raw, 4)[0] != 41:
        raise ValueError('Expected IBSP version 41')
    lumps = []
    for offset, size in struct.iter_unpack('<ii', raw[8:176]):
        if offset < 0 or size < 0 or offset + size > len(raw):
            raise ValueError('Invalid BSP41 lump range')
        lumps.append(raw[offset:offset + size])

    def rows(index, fmt):
        if len(lumps[index]) % struct.calcsize('<' + fmt):
            raise ValueError(f'Incomplete BSP41 lump {index}')
        return list(struct.iter_unpack('<' + fmt, lumps[index]))

    bsp = dict(entities=entities(lumps[0]), planes=rows(1, '4fi'), points=rows(2, '3f'),
               nodes=rows(4, '3i6h2H'), faces=rows(6, 'Hhihh4Bi'), lighting=lumps[7],
               leaves=rows(8, 'i2h6h4Hi'), leaffaces=[r[0] for r in rows(9, 'H')],
               leafbrushes=[r[0] for r in rows(10, 'H')], edges=rows(11, '2H'),
               surfedges=[r[0] for r in rows(12, 'i')], models=rows(13, '9f3i'),
               brushes=rows(14, '3i'), brushsides=rows(15, 'Hh'), areas=rows(17, '2i'),
               areaportals=rows(18, '2i'), extended_texinfo=rows(19, '3f'),
               lump_sizes={name: len(data) for name, data in zip(LUMPS, lumps)})
    if not bsp['models']:
        raise ValueError('BSP41 has no world model')
    for key in ('planes', 'points', 'models', 'extended_texinfo'):
        if any(not math.isfinite(v) for row in bsp[key] for v in row):
            raise ValueError(f'Non-finite BSP41 {key}')
    info = next((e for e in bsp['entities'] if e.get('classname') == 'worldspawn'), {})
    palette, palette_name = palette_for(info, files)
    cache, textures, texinfo, chains = {}, [], [], []
    for i, row in enumerate(rows(5, '8fii32si')):
        if not all(math.isfinite(v) for v in row[:8]):
            raise ValueError('Non-finite BSP41 texinfo')
        name = dk.asset_name(row[10].split(b'\0')[0].decode('latin1'))
        path = 'textures/' + name + '.wal'
        if path not in cache:
            raw_tex = dk.read_asset(files[path]) if path in files else b''
            image = wal_image(raw_tex, palette) if raw_tex else checker()
            cache[path] = dict(name=name, source=path, image=image, width=image.width, height=image.height,
                               pixels=image.tobytes(), missing=not raw_tex,
                               palette=palette_name)
        textures.append(cache[path])
        texinfo.append((*row[:8], i, row[8]))
        chains.append(row[11])
    if any(n < -1 or n >= len(texinfo) for n in chains):
        raise ValueError('Invalid BSP41 animation chain')
    if len(bsp['extended_texinfo']) not in (0, len(texinfo)):
        raise ValueError('BSP41 extended texinfo count mismatch')
    plane_polys, at = [], 0
    words = [r[0] for r in rows(20, 'i')]
    if words:
        for _ in bsp['planes']:
            if at >= len(words) or words[at] < 0 or at + 1 + words[at] > len(words):
                raise ValueError('Invalid BSP41 plane polygon list')
            count = words[at]
            indices = words[at + 1:at + 1 + count]
            if any(i < 0 or i >= len(bsp['faces']) for i in indices):
                raise ValueError('Invalid BSP41 plane polygon face')
            plane_polys.append(indices)
            at += count + 1
        if at != len(words):
            raise ValueError('Trailing BSP41 plane polygon data')
    bsp.update(info=info, palette=palette, palette_name=palette_name, textures=textures,
               texinfo=texinfo, nexttexinfo=chains, plane_polys=plane_polys)
    return bsp


def face_data(bsp, index):
    face = polygon(bsp, index)
    # CalcSurfaceExtents 1000F589/1000F598 stores extrema as float32 before
    # floor/ceil. Promoting them to double invents extra luxels at boundaries.
    lo = np.floor(face['st'].min(axis=0).astype(np.float32) / 16).astype(int)
    hi = np.ceil(face['st'].max(axis=0).astype(np.float32) / 16).astype(int)
    face.update(mins=lo * 16, size=hi - lo + 1)
    ti = bsp['faces'][index][4]
    flags = bsp['texinfo'][ti][9]
    kind = ('hidden' if flags & NODRAW or flags == FOG else 'sky' if flags & SKY else
            'turbulent' if flags & WARP else 'normal')
    # Without SGIS multitexture, 10017E9B..10017EAA skips the masked
    # surface's lightmap. This is the retail fallback, even with gl_ext_multitexture=1.
    face.update(flags=flags, kind=kind, unlit=bool(flags & (FULLBRIGHT | SKY | MASKED)),
                alpha=.33 if flags & TRANS33 else .66 if flags & TRANS66 else 1,
                nexttexinfo=bsp['nexttexinfo'][ti])
    return face


def dark_styles(bsp):
    return sorted({int(number(e, 'style')) for e in bsp['entities'] if e.get('classname', '').startswith('light')
                   and e.get('classname') not in EPISODE_LIGHTS
                   and number(e, 'style') >= 32 and int(number(e, 'spawnflags')) & 1 and not excluded(e)})


def fog_settings(info):
    color = [0, 0, 0]
    for key in info:  # Both native keys write the same field, in entity order.
        if key in ('_color', 'fog_color'):
            color = (vector(info, key) / (255 if key == 'fog_color' else 1)).tolist()
    return dict(enabled=bool(number(info, 'fog_value')), mode='linear', color=color,
                start=number(info, 'fog_start'), end=number(info, 'fog_end', 2048),
                sky_end=number(info, 'fog_skyend', number(info, 'fog_end', 2048)))


def upload_image(image, mipmap=True, gamma=GAMMA):
    # GL_InitImages 10008B86: the software upload table is independent of
    # gl_ext_gamma. Engine 0043CBCC executes current.cfg AFTER consuming +set.
    # Thus the host's two +set vid_gamma captures both used the config's .799.
    pixels = np.array(image.convert('RGBA'))
    # GL_Upload32 10007554..10007617: gl_round_down=1, gl_picmip=0,
    # power-of-two dimensions capped at 256. GL_ResampleTexture 10006E40
    # averages four quarter-pixel taps, including alpha.
    width, height = image.size
    target = [min(256, 1 << ((size.bit_length() - 1) if mipmap else (size - 1).bit_length())) for size in image.size]
    resized = target != [width, height]
    if resized:
        w, h = target
        step = (width << 16) // w
        x1, x2 = ((np.arange(w) * step + step // 4) >> 16), ((np.arange(w) * step + 3 * (step // 4)) >> 16)
        y1, y2 = ((np.arange(h) + .25) * height / h).astype(int), ((np.arange(h) + .75) * height / h).astype(int)
        wide = pixels.astype(np.uint16)
        pixels = ((wide[y1[:, None], x1] + wide[y1[:, None], x2] +
                   wide[y2[:, None], x1] + wide[y2[:, None], x2]) // 4).astype(np.uint8)
    # Same-size non-mipmapped uploads bypass LightScaleTexture (100076CA).
    # Alpha and the separately uploaded lightmap are never gamma transformed.
    if gamma != 1 and (mipmap or resized):
        table = np.clip(np.floor(255 * ((np.arange(256) + .5) / 255.5) ** gamma + .5), 0, 255).astype(np.uint8)
        pixels[:, :, :3] = table[pixels[:, :, :3]]
    return Image.fromarray(pixels)


def alias_normals():
    # ref_gl 1000A510: two poles then 23 longitudes, 11 latitudes (-75..75).
    normals = [(0, 0, 1), (0, 0, -1)]
    for longitude in range(23):
        yaw = math.radians(longitude * float(np.float32(360 / 23)))
        for latitude in range(-5, 6):
            pitch = math.radians(latitude * 15)
            normals.append((math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)))
    return np.array(normals + [(0, 0, 0)])


def alias_colors(normal_indices, light, yaw):
    # 1000D32D..D3CE default direction (-1,-1,-1), integer Euler angles;
    # 1000A760 clamps negative dot, 1000A820 adds it to sampled RGB, caps at 1.
    heading, pitch = math.radians(225 - yaw), math.radians(35)
    direction = np.array([math.cos(pitch) * math.cos(heading), math.cos(pitch) * math.sin(heading), -math.sin(pitch)])
    dot = np.maximum(0, alias_normals()[normal_indices] @ direction)
    return np.minimum(1, np.asarray(light) + dot[:, None])


def instances(bsp):
    placed = [dict(model=0, entity=0, classname='worldspawn', offset=[0, 0, 0], angles=[0, 0, 0], state='world', guess=False)]
    skipped = []
    for index, entity in enumerate(bsp['entities']):
        cls, model = entity.get('classname', ''), entity.get('model', '')
        if not model.startswith('*'):
            if cls.startswith('func_'):
                skipped.append(dict(entity=index, classname=cls, model=model, reason='No inline brush model'))
            continue
        reason = 'Excluded on medium skill' if excluded(entity) else ''
        if not cls.startswith('func_') or cls in ('func_areaportal', 'func_event_generator', 'func_dynalight'):
            reason = 'Logical/trigger brush, not a visible model'
        flags = int(number(entity, 'spawnflags'))
        if cls == 'func_wall' and flags & 7 and not flags & 4:
            reason = 'Trigger-spawn wall starts hidden'
        if cls == 'func_wall' and flags & 7 and flags & 64:
            reason = 'CTF-only triggered wall removed in single player'
        if cls == 'func_explosive' and flags & 1:
            reason = 'Trigger-spawn brush starts hidden'
        if cls == 'func_debris':
            reason = 'Debris starts hidden until targeted; func_debris_visible is the visible variant'
        row = dict(entity=index, classname=cls, model=model)
        if reason:
            skipped.append(dict(row, reason=reason))
            continue
        number_ = int(model[1:])
        if not 0 < number_ < len(bsp['models']):
            skipped.append(dict(row, reason='Invalid inline model index'))
            continue
        bounds = bsp['models'][number_]
        offset, turn = vector(entity), np.zeros(3)
        state, guess = 'authored spawn position', False
        # Engine CMod_LoadSubmodels 0045A5A8..0045A5FB expands each bound by 1.
        size = np.array(bounds[3:6]) - bounds[:3] + 2
        if cls in ('func_door', 'func_water'):
            guess = cls == 'func_water'  # Door endpoint rule; water-specific spawn not established.
            state = 'closed'
            if flags & 1:
                direction = q2.movedir(entity)
                offset += direction * (np.abs(direction) @ size - number(entity, 'lip', 8))
                state = 'START_OPEN endpoint'
        elif cls in ('func_door_rotate', 'func_door_rotating'):
            state = 'closed, authored pivot'
            if flags & 1:
                axis = 2 if flags & 128 else 0 if flags & 256 else 1
                turn[axis] = number(entity, 'distance', 90) * (-1 if flags & 2 else 1)
                state = 'START_OPEN rotation'
        elif cls == 'func_plat':
            if not flags & 1:
                offset[2] -= number(entity, 'height', size[2] - number(entity, 'lip', 8))
            state = 'START_UP' if flags & 1 else 'lower endpoint'
        elif cls == 'func_train':
            corner = next((e for e in bsp['entities'] if e.get('classname') == 'path_corner_train'
                           and e.get('targetname') == entity.get('target')), None)
            if corner:
                offset = vector(corner)  # world.dll 100E26DF: origin at the corner, not Q2's bounds-min offset.
                state = 'first path corner, before travel'
            else:
                state, guess = 'authored position; no first path corner', True
        elif cls not in ('func_wall', 'func_button', 'func_rotate', 'func_explosive', 'func_debris_visible', 'func_door_secret', 'func_wall_explode'):
            guess = True
        placed.append(dict(row, model=number_, offset=offset.tolist(), angles=turn.tolist(), state=state, guess=guess))
    return placed, skipped


def hull_floor(bsp, start, mins=(-16, -16, -24), maxs=(16, 16, 32)):
    """Sweep a player AABB down through BSP convex brushes; never move it up.

    Unlike Q1 clipnodes, BSP41 stores brush planes. Only leaves reachable from
    the world's headnode participate, so unplaced inline brushes cannot catch us.
    """
    if not bsp['nodes'] or not bsp['leaves']:
        return None
    nodes, brush_ids, visited = [bsp['models'][0][9]], set(), set()
    while nodes:
        node = nodes.pop()
        if node in visited:
            continue
        visited.add(node)
        if node < 0:
            leaf = bsp['leaves'][-1 - node]
            brush_ids.update(bsp['leafbrushes'][leaf[11]:leaf[11] + leaf[12]])
        else:
            nodes.extend(bsp['nodes'][node][1:3])
    closest = math.inf
    for i in brush_ids:
        first, count, contents = bsp['brushes'][i]
        if not contents & (1 | 0x10000):  # solid or playerclip
            continue
        near, far = -math.inf, math.inf
        for plane_index, _ in bsp['brushsides'][first:first + count]:
            plane = bsp['planes'][plane_index]
            n = np.array(plane[:3])
            distance = plane[3] - np.where(n >= 0, n * mins, n * maxs).sum()
            delta = n @ start - distance
            if abs(n[2]) < 1e-9:
                if delta > 0:
                    far = -math.inf
                    break
            elif n[2] > 0:
                near = max(near, delta / n[2])
            else:
                far = min(far, delta / n[2])
        if 0 <= near <= far and near < closest:
            closest = near
    return float(start[2] - closest + mins[2]) if math.isfinite(closest) else None


def viewpoints(bsp, floors):
    order = ['info_player_start', 'info_player_deathmatch', 'info_player_team1', 'info_player_team2', 'info_player_coop', 'info_player_intermission']
    starts = [e for e in bsp['entities'] if e.get('classname') in order and not excluded(e)]
    # physics.dll 10005C60: empty transition name -> unnamed start, then first
    # matching classname in entity order. "primary" has no special engine meaning.
    starts.sort(key=lambda e: (order.index(e['classname']), bool(e.get('targetname'))))
    result = []
    for entity in starts:
        start, turn = vector(entity), q2.angles(entity)
        eye = start.copy()
        floor = None
        if entity['classname'] != 'info_player_intermission':
            floor = hull_floor(bsp, start)
            if floor is None:
                floor = floor_below(floors, *start)
            eye[2] = min(start[2] + VIEW_HEIGHT, floor + FOOT + VIEW_HEIGHT) if floor is not None else start[2] + VIEW_HEIGHT
        result.append(dict(origin=(eye[[1, 2, 0]] * SCALE).tolist(), native_origin=eye.tolist(), authored_origin=start.tolist(),
                           yaw=-float(turn[1]), pitch=-float(turn[0]), roll=float(turn[2]), classname=entity['classname'],
                           targetname=entity.get('targetname', ''), floor_z=floor,
                           eye_above_floor=float(eye[2] - floor) if floor is not None else None))
    if not result:
        raise ValueError('No player/team/intermission viewpoint')
    return result


def decoration_table(files):
    table = {}
    for path, entry in files.items():
        if not path.endswith('decoinfo.csv'):
            continue
        episode = next((p for p in path.split('/') if p in ('e1', 'e2', 'e3', 'e4')), '')
        for row in csv.reader(io.StringIO(dk.read_asset(entry).decode('latin1'))):
            if len(row) >= 13 and not row[0].lstrip().startswith(';'):
                row = [s.strip() for s in row]
                table['deco_' + episode, row[0].lower()] = dict(path=dk.asset_name(row[1]), fields=row, csv=path)
    # world 10111600 prefers CVSC: four-byte header followed by CSV bytes XOR 0x96
    # (1011262A). AI model lookup 10047B60 returns the CSV's model-name column.
    source = next((p for p in ('aidata.vsc', 'aidata.cs2', 'aidata.csv') if p in files), '')
    if source:
        raw = dk.read_asset(files[source])
        if source.endswith('.vsc'):
            if raw[:4] != b'CVSC':
                raise ValueError('Invalid AIData CVSC header')
            raw = bytes(x ^ 0x96 for x in raw[4:])
        for row in csv.reader(io.StringIO(raw.decode('latin1'))):
            if len(row) > 14 and row[0].strip().lower() in AMBIENT_ANIMALS:
                cls = row[0].strip().lower()
                table[cls, ''] = dict(path=dk.asset_name(row[1]), scale=row[14].strip(), csv=source)
                if cls == 'e_greyfish':
                    table['e_grayfish', ''] = table[cls, '']
    return table


PROP_MODELS = {'misc_healthtree': 'models/e1/healthtree.dkm', 'misc_fountain': 'models/e2/a2_hlthfnt.dkm',
               'misc_drugbox': 'models/e4/a4_dbox.dkm', 'light_flare': 'models/global/e_flare1.sp2'}
EPISODE_LIGHTS = {f'light_e{n}': f'models/global/e{max(2, n)}_firea.sp2' for n in range(1, 5)}
LIGHT_WITHOUT_MODEL = {
    'light_spot': 'Native light_spot 100F417C sets light styles and sound, no model',
    'light_strobe': 'Native light_strobe 100F43DF sets light styles and sound, no model',
    'light_walltorch': 'Native light_walltorch 100F4642 removes the entity',
    'light_flame': 'No light_flame spawn export; physics 10009802 logs missing spawn and continues without a model',
}


def scenery(bsp, table):
    drawn, skipped = [], []
    for index, e in enumerate(bsp['entities']):
        cls, named = e.get('classname', ''), e.get('model', '')
        if named.startswith('*'):
            continue
        relevant = named or cls.startswith(('deco_', 'misc_', 'light_')) or cls in PROP_MODELS or cls in AMBIENT_ANIMALS
        if not relevant:
            continue
        row = dict(entity=index, classname=cls, named=named)
        reason = 'Excluded on medium skill' if excluded(e) else ''
        if cls.startswith(('monster_', 'weapon_', 'ammo_', 'item_')):
            reason = 'Monster/item/weapon: outside static scenery scope'
        elif not cls.startswith(('deco_', 'misc_', 'light', 'e_')):
            reason = 'Gameplay/cinematic/particle entity: outside static scenery scope'
        entry = table.get((cls, named.lower())) if cls.startswith('deco_') else table.get((cls, ''))
        path = entry['path'] if entry else EPISODE_LIGHTS.get(cls, PROP_MODELS.get(cls, dk.asset_name(named) if named else ''))
        if cls == 'light_flare' and named:
            path = dk.asset_name(named)
        if cls == 'light_flare' and int(number(e, 'spawnflags')) & 1:
            reason = 'START_OFF light model starts hidden'
        if cls in LIGHT_WITHOUT_MODEL:
            reason = reason or LIGHT_WITHOUT_MODEL[cls]
        if cls == 'misc_hosportal':
            path = f"models/e1/hosportal{min(2, max(0, int(number(e, 'style')))) + 1}.dkm"
        if cls.startswith('deco_') and entry is None:
            reason = 'Native decoration CSV lookup fails; spawn returns without assigning a model'
        if not path:
            reason = reason or 'No static model named by entity or verified spawn code'
        if reason:
            skipped.append(dict(row, reason=reason))
            continue
        frame = max(0, int(number(e, 'frame')))
        if entry and 'fields' in entry and len(entry['fields']) > 14 and int(entry['fields'][14] or 0) > 0 and number(e, 'animseq') != -1:
            sequence = int(number(e, 'animseq'))
            if not 0 <= sequence < min(5, int(entry['fields'][14])):
                sequence = 0
            if len(entry['fields']) > 15 + sequence:
                frame = int(entry['fields'][15 + sequence].split('~')[0] or 0)
        raw_scale = e.get('scale', entry.get('scale', '1') if entry else '1').split()
        scale = [float(raw_scale[0])] * 3 if len(raw_scale) == 1 else [float(v) for v in raw_scale]
        if cls == 'light_flare' or cls in EPISODE_LIGHTS:
            # Native sscanf reads three values; omitted/zero components become 1.
            scale = [float(v) or 1 for v in (raw_scale + ['1', '1'])[:3]]
        if len(scale) != 3 or not all(math.isfinite(v) for v in scale):
            raise ValueError('Invalid scenery scale')
        flags = int(number(e, 'spawnflags'))
        # Decoration spawn parses "alpha", not the editor's frequent "alfa" typo.
        alpha = number(e, 'alpha', 1) if flags & 256 or not cls.startswith('deco_') else 1
        if cls in EPISODE_LIGHTS:
            frame, alpha = 0, 1  # Shared spawn 100F46EF does not parse frame/alpha.
        drawn.append(dict(row, path=path, frame=frame, origin=vector(e).tolist(), angles=q2.angles(e).tolist(),
                          scale=scale, alpha=float(np.clip(alpha, 0, 1)), csv=entry['csv'] if entry else '',
                          state='authored spawn pose', guess=bool(e.get('parenttarget')),
                          ignored_alfa=e.get('alfa'), parenttarget=e.get('parenttarget', '')))
        if cls in EPISODE_LIGHTS:
            drawn[-1].update(sprite_copies=2, oriented=True, additive=True)
        elif cls == 'light_flare':
            drawn[-1]['additive'] = True
    return drawn, skipped


def light_point(bsp, origin, dark):
    # ref_gl 100093B0: near child, node surfaces, far child. Native tests
    # integer texture coordinates against extents, not polygon containment.
    if not bsp['lighting']:
        return np.ones(3)
    cache = bsp.setdefault('light_faces', {})

    def sample(first, count, point):
        for i in range(first, first + count):
            if i not in cache:
                cache[i] = face_data(bsp, i)
            face = cache[i]
            if face['flags'] & (SKY | WARP) or face['flags'] == FOG | FULLBRIGHT:
                continue
            vectors = np.array(bsp['texinfo'][bsp['faces'][i][4]][:8]).reshape(2, 4)
            st = (vectors[:, :3] @ point + vectors[:, 3]).astype(int) - face['mins']
            if np.any(st < 0) or np.any(st > (face['size'] - 1) * 16):
                continue
            color = np.zeros(3)
            if face['lightofs'] >= 0:
                w, h = map(int, face['size'])
                xy = st // 16
                at = face['lightofs'] + (int(xy[1]) * w + int(xy[0])) * 3
                for style in face['styles']:
                    if style == 255:
                        break
                    if at + 3 > len(bsp['lighting']):
                        raise ValueError('Alias sample exceeds lighting lump')
                    if style not in dark:
                        color += np.frombuffer(bsp['lighting'], np.uint8, 3, at) / 255.
                    at += w * h * 3
            return color
        return None

    # Stack entries keep native traversal order without Python recursion limits.
    start = np.asarray(origin, float)
    stack = [('node', bsp['models'][0][9], start, start - [0, 0, 2048])]
    while stack:
        kind, index, start, end = stack.pop()
        if kind == 'surface':
            node = bsp['nodes'][index]
            color = sample(node[9], node[10], start)
            if color is not None:
                return color
            continue
        if index < 0:
            continue
        node = bsp['nodes'][index]
        plane = bsp['planes'][node[0]]
        front, back = np.array([start, end]) @ plane[:3] - plane[3]
        side = int(front < 0)
        if (back < 0) == bool(side):
            stack.append(('node', node[1 + side], start, end))
            continue
        mid = start + (end - start) * (front / (front - back))
        stack.extend([('node', node[2 - side], mid, end), ('surface', index, mid, mid),
                      ('node', node[1 + side], start, mid)])
    return np.zeros(3)


def read_sprite(raw, frame=0):
    if len(raw) < 12 or raw[:4] != b'IDS2':
        raise ValueError('Expected IDS2 sprite')
    version, count = struct.unpack_from('<2i', raw, 4)
    if version != 2 or count <= 0 or 12 + count * 80 > len(raw):
        raise ValueError('Invalid IDS2 sprite frames')
    width, height, x, y, path = struct.unpack_from('<4i64s', raw, 12 + (frame % count) * 80)
    if min(width, height) <= 0:
        raise ValueError('Invalid sprite dimensions')
    return dict(size=[width, height], pivot=[x, y], path=dk.asset_name(path.split(b'\0')[0].decode('latin1')))


def append_scenery(data, bsp, files, placements):
    images, cache, failures = {}, {}, []
    points, uvs, uv2 = [data['points']], [data['uvs']], [data['uv2']]
    colors = [np.full((len(data['points']), 4), 255, np.uint8)]
    vertex_count = len(data['points'])
    for item in placements:
        key = item['path'], item['frame']
        try:
            if item['path'].endswith('.sp2'):
                if item['path'] not in files:
                    raise ValueError('Sprite absent from install')
                sprite = read_sprite(dk.read_asset(files[item['path']]), item['frame'])
                path, fallback = dk.choose_skin(item['path'], sprite['path'], files)
                image = wal_image(dk.read_asset(files[path]), bsp['palette']) if path and path.endswith('.wal') else dk.read_texture(path, files)[0] if path else checker()
                png = 'sprite_' + hashlib.sha256(image.tobytes() + struct.pack('<II', *image.size)).hexdigest()[:24] + '.png'
                images[png] = image
                copies = item.get('sprite_copies', 1)
                oriented = item.get('oriented', False)
                data['groups'].append(dict(texture=png, kind='sprite_oriented' if oriented else 'sprite', flags=0, size=list(image.size), unlit=True,
                    alpha=item['alpha'], additive=item.get('additive', False), start=len(data['indices']), count=6 * copies,
                    sprite_size=(np.array(sprite['size']) * item['scale'][:2] * SCALE).tolist(),
                    sprite_pivot=(np.array(sprite['pivot']) * item['scale'][:2] * SCALE).tolist()))
                texcoords = np.array([[0, 0], [1, 0], [1, 1], [0, 1]])
                for copy in range(copies):
                    placed = np.tile(np.asarray(item['origin'], float), (4, 1))
                    if oriented:
                        # RF_SPRITE_ORIENTED=8 (ref_gl 1001285F); native AngleVectors
                        # right=-local Y, up=local Z. Child yaw is parent yaw+90.
                        matrix = rotation(np.array(item['angles']) + [0, copy * 90, 0])
                        xy = (texcoords * [1, -1] + [0, 1]) * sprite['size'] - sprite['pivot']
                        xy = xy * np.array(item['scale'][:2])
                        placed += xy[:, :1] * -matrix[:, 1] + xy[:, 1:] * matrix[:, 2]
                    data['indices'].extend(vertex_count + v for v in (0, 2, 1, 0, 3, 2))
                    points.append(placed)
                    uvs.append(texcoords)
                    uv2.append(np.zeros((4, 2)))
                    colors.append(np.full((4, 4), 255, np.uint8))
                    vertex_count += 4
                item.update(status='drawn', vertices=4 * copies, sprite=sprite,
                            skins=[dict(named=sprite['path'], source=path, fallback=fallback)])
                continue
            if key not in cache:
                if item['path'] not in files:
                    raise ValueError('Model path absent from install')
                mesh = dk.read_dkm(dk.read_asset(files[item['path']]), item['frame'])
                textures, skin_audit = [], []
                for named in mesh['skin_names']:
                    path, fallback = dk.choose_skin(item['path'], named, files)
                    if path and path.endswith('.wal'):
                        raw_texture = dk.read_asset(files[path])
                        # RegisterSkin passes it_skin=0 (100087A7): GL_LoadWal
                        # uses raw+120 for v3 skins, not the world's palette.
                        image = wal_image(raw_texture, None if raw_texture[0] == 3 else bsp['palette'])
                        palette = 'embedded WAL v3' if raw_texture[0] == 3 else bsp['palette_name']
                    else:
                        image, palette = dk.read_texture(path, files) if path else (checker(), '')
                    png = 'skin_' + hashlib.sha256(image.tobytes() + struct.pack('<II', *image.size)).hexdigest()[:24] + '.png'
                    images[png] = image
                    textures.append(png)
                    skin_audit.append(dict(named=named, source=path, fallback=fallback, palette=palette))
                images['missing_skin.png'] = checker()
                cache[key] = dk.build_model(mesh, textures, include_normals=True, visible_only=True), skin_audit
            model, skin_audit = cache[key]
            local = np.array(model['vertices']).reshape(-1, 3)[:, [2, 0, 1]]
            if not len(local):
                raise ValueError('All DKM surfaces hidden by native surface flag 1')
            matrix = rotation(item['angles'])
            # R_RotateForEntity 1000CC2D..1000CCC3 translates before rotating:
            # origin - header_origin * (scale - 1), then rotation and scale.
            offset = np.asarray(model['metadata']['origin']) * (np.asarray(item['scale']) - 1)
            placed = (local * item['scale']) @ matrix.T + item['origin'] - offset
            lighting = light_point(bsp, np.array(item['origin']), data['dark'])
            vertex_colors = np.ones((len(local), 4))
            vertex_colors[:, :3] = alias_colors(model['normal_indices'], lighting, item['angles'][1])
            for group in model['groups']:
                png = model['material_textures'][group['materialIndex']]
                part = model['indices'][group['start']:group['start'] + group['count']]
                data['groups'].append(dict(texture=png, kind='model', flags=0, size=list(images[png].size),
                                           unlit=True, alpha=item['alpha'], start=len(data['indices']), count=len(part)))
                data['indices'].extend(i + vertex_count for i in part)
            points.append(placed)
            uvs.append(np.array(model['uvs']).reshape(-1, 2))
            uv2.append(np.zeros((len(local), 2)))
            colors.append(np.clip(vertex_colors * 255, 0, 255).astype(np.uint8))
            vertex_count += len(local)
            item.update(status='drawn', skins=skin_audit, light=lighting.tolist(), vertices=len(local),
                        warnings=model['metadata'].get('warnings', []))
        except (ValueError, IndexError, struct.error, OSError) as exc:
            item.update(status='not drawn', reason=str(exc))
            failures.append(dict(item))
    data.update(points=np.concatenate(points), uvs=np.concatenate(uvs), uv2=np.concatenate(uv2), colors=np.concatenate(colors))
    return images, failures


def skybox(info, files):
    name = info.get('sky', '')
    images, faces, missing = {}, {}, []
    for suffix in ('rt', 'bk', 'lf', 'ft', 'up', 'dn'):
        candidates = [dk.asset_name(f'env/{folder}/{name}{suffix}.tga') for folder in ('32bit', name)]
        path = next((p for p in candidates if p in files), '') if name else ''
        image = dk.read_texture(path, files)[0] if path else Image.new('RGB', (16, 16), (0, 0, 0))
        if name and not path:
            missing.append(candidates[0])
        png = 'sky_' + hashlib.sha256(image.tobytes() + struct.pack('<II', *image.size)).hexdigest()[:24] + '.png'
        images[png] = image
        faces[suffix] = dict(texture=png, source=path)
    clouds = dict(name=info.get('cloudname', ''), layers=[])
    if clouds['name']:
        candidates = [dk.asset_name(f"env/{folder}/{clouds['name']}.tga") for folder in ('32bit', name)]
        path = next((p for p in candidates if p in files), '')
        if path:
            image = dk.read_texture(path, files)[0]
            png = 'cloud_' + hashlib.sha256(image.tobytes() + struct.pack('<II', *image.size)).hexdigest()[:24] + '.png'
            images[png] = image
            for i, tile, alpha in ((1, 8, 1), (2, 2, .7)):
                clouds['layers'].append(dict(texture=png, source=path, tile=number(info, f'cloud{i}tile', tile),
                                             alpha=number(info, 'cloud2alpha', alpha) if i == 2 else 1))
        else:
            missing.append(candidates[0])
    return dict(name=name, rotate=0, axis=[0, 0, 1], faces=faces, clouds=clouds, missing=missing), images


def build_map(raw, name, files, deco=None):
    bsp = read_bsp(raw, files)
    placed, skipped = instances(bsp)
    data = q2.geometry(bsp, placed, face_reader=face_data, dark_styles=dark_styles(bsp))
    views = viewpoints(bsp, data['floors'])
    models, omitted = scenery(bsp, decoration_table(files) if deco is None else deco)
    images, failures = append_scenery(data, bsp, files, models)
    sky, sky_images = skybox(bsp['info'], files)
    images.update(sky_images)
    images.update({png: tex['image'] for png, tex in data['textures'].items()})
    # Sky faces and clouds both load as it_sky=4 (1001FE28/1001FED7).
    images = {png: upload_image(image, mipmap=not png.startswith(('sky_', 'cloud_'))) for png, image in images.items()}
    points = data['points'][:, [1, 2, 0]] * SCALE
    blob = b''.join((points.astype('<f4').tobytes(), data['uvs'].astype('<f4').tobytes(), data['uv2'].astype('<f4').tobytes(),
                     data['colors'].tobytes(), np.array(data['indices'], '<u4').tobytes()))
    flags = Counter(t[9] for t in bsp['texinfo'])
    gaps = ['Volumetric leaf fog and reflective surfaces are not simulated.',
            'Parented scenery holds its authored pose; runtime attachment and physics settling are not simulated.',
            'Brush rows marked guess retain authored spawn placement.']
    scene = dict(name=name, title=bsp['info'].get('mapname', name), game='daikatana', format='daikatana-bsp41',
                 vertices=len(points), indices=len(data['indices']), colors=True, lightmap=list(data['atlas'].size),
                 groups=data['groups'], viewpoints=views, bounds=np.array(bsp['models'][0][:6]).reshape(2, 3).tolist(),
                 instances=placed, skipped_entities=skipped, models=models, omitted_models=omitted,
                 model_failures=failures, faces=data['audit'], skybox=sky, fog=fog_settings(bsp['info']),
                 entity_classes=dict(Counter(e.get('classname', '') for e in bsp['entities'])), dark_styles=data['dark'],
                 texture_flags={hex(k): v for k, v in flags.items()},
                 unknown_texture_bits=[hex(1 << b) for b in range(32) if any(k & (1 << b) & ~DRAW_FLAGS for k in flags)],
                 palettes={t['source']: t['palette'] for t in bsp['textures']},
                 lump_sizes=bsp['lump_sizes'], extended_texinfo=bsp['extended_texinfo'],
                 plane_polygon_counts=[len(p) for p in bsp['plane_polys']],
                 missing=sorted({f['texture'] for f in data['audit'] if f['missing'] and f['kind'] != 'sky'} | set(sky['missing']) |
                                {skin['named'] for model in models for skin in model.get('skins', []) if not skin['source']}),
                 lighting=dict(renderer='Daikatana ref_gl', gl_modulate=1, intensity=1, vid_gamma=GAMMA,
                               gl_ext_gamma=1, effective_gamma=GAMMA, display_gamma=1,
                               gamma_stage='software texture upload; current.cfg overrides early +set',
                               multitexture=False, fog_passes=2, normal_style=1),
                 assumptions=['medium single player; still spawn state', 'first animated texture frame', 'START_OFF styles dark',
                              'retail SGIS-unavailable fallback; inferred from game captures'], gaps=gaps)
    return scene, blob, images, data['atlas']


def import_maps(install, output, replace=False, map_filter=None):
    files, rows, counts = dk.read_install(install, maps=True)
    records = [r for r in rows if r['source'].endswith('.bsp')]
    output = Path(output)
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    report = dict(game='daikatana', imported=[], skipped=[], failed={}, bsp_entries=len(records), pak_counts=counts,
                  results=records, entity_classes={}, brush_classes={}, model_classes={})
    index, deco, class_counts = [], decoration_table(files), Counter()
    for record in records:
        name = record['source']
        if record['status'] != 'pending':
            continue
        if map_filter and map_filter.lower() not in name:
            record.update(status='skipped', reason='Excluded by requested filter')
            report['skipped'].append(name)
            continue
        ident = dk.safe(name.removeprefix('maps/').removesuffix('.bsp'))
        folder = output / 'maps' / ident
        try:
            if not replace and all((folder / f).is_file() for f in ('scene.json', 'geometry.bin', 'lightmap.png')):
                scene = json.loads((folder / 'scene.json').read_text(encoding='utf-8'))
                record.update(status='skipped', reason='Already imported; replace to rebuild')
                report['skipped'].append(name)
            else:
                scene, blob, images, atlas = build_map(dk.read_asset(files[name]), ident, files, deco)
                for png, image in images.items():
                    target = output / 'textures' / png
                    if replace or not target.is_file():
                        image.save(target)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'geometry.bin').write_bytes(blob)
                atlas.save(folder / 'lightmap.png')
                (folder / 'scene.json').write_text(json.dumps(scene, separators=(',', ':')), encoding='utf-8')
                record['status'] = 'imported'
                report['imported'].append(ident)
            record.update(model_count=sum(m['status'] == 'drawn' for m in scene['models']),
                          model_failures=scene['model_failures'], warnings=scene['missing'], unknown_texture_bits=scene['unknown_texture_bits'])
            class_counts.update(scene['entity_classes'])
            for key, items in (('brush_classes', scene['instances'] + scene['skipped_entities']),
                               ('model_classes', scene['models'] + scene['omitted_models'])):
                for item in items:
                    decisions = report[key].setdefault(item['classname'], Counter())
                    decision = item.get('reason') or item.get('state', 'drawn')
                    decisions[decision + (' (guess)' if item.get('guess') else '')] += 1
            index.append(dict(id=ident, name=ident, title=scene['title'], group='Daikatana'))
        except (ValueError, IndexError, struct.error, OSError) as exc:
            record.update(status='failed', reason=str(exc))
            report['failed'][name] = str(exc)
    report['entity_classes'] = dict(class_counts)
    (output / 'index.json').write_text(json.dumps(sorted(index, key=lambda e: (e['id'] != 'e1m1a', e['id'])), indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'daikatana-maps')
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--filter')
    args = parser.parse_args()
    print(json.dumps(import_maps(args.install, args.output, args.replace, args.filter), indent=1))

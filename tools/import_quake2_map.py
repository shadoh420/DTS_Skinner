"""Quake II and installed xatrix/rogue/ctf BSP38 maps, ref_gl 3.20 lighting.

python tools/import_quake2_map.py --install "C:/Program Files (x86)/Steam/steamapps/common/Quake 2/baseq2" [--replace] [--game all|quake2|xatrix|rogue|ctf]

Data defaults to SKINNER_DATA_DIR/quake2-maps. Placed monsters use standing MD2 previews; no gameplay simulation.
Reference settings: intensity=2, gl_modulate=1, vid_gamma=1; light styles 1-11 animate, switchable ones held at m,
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
    from tools.import_quake_map import (LIGHT_STYLES, SCALE, animated_styles, content_regions, entities, face_data as quake_face_data,
                                        floor_below, pack, rotation, style_atlas, style_sample, train_path, vector)
    from tools.source_effects import rotating
    from tools.import_quake import palette_image
    from tools.local_data import LOCAL_DATA
except ImportError:
    from import_quake2 import GAMES, available_games, asset_name, read_install, read_pcx, safe
    from import_quake_map import (LIGHT_STYLES, SCALE, animated_styles, content_regions, entities, face_data as quake_face_data,
                                  floor_below, pack, rotation, style_atlas, style_sample, train_path, vector)
    from source_effects import rotating
    from import_quake import palette_image
    from local_data import LOCAL_DATA

SKY, WARP, TRANS33, TRANS66, FLOWING, NODRAW = 4, 8, 16, 32, 64, 128
INTENSITY = 2
# p_view.c SV_CalcBlend: lava, then slime, then water (CONTENTS_LAVA 8, SLIME 16, WATER 32); the camera inside solid
# also gets the lava blend there, left out of this free-flying preview.
CONTENT_BLENDS = {8: [1., .3, 0., .6], 16: [0., .1, .05, .6], 32: [.5, .3, .2, .4]}
# Brush entities that animate their texture chains: EF_ANIM_ALL (autoanim = 2 * cl.time) and EF_ANIM_ALLFAST
# (cl.time / 100) spawnflags from g_func.c SP_func_rotating/door/door_rotating and g_misc.c SP_func_wall/object/explosive;
# func_button keeps EF_ANIM01 (autoanim & 1) unpressed; everything else draws s.frame 0 (cl_ents.c CL_AddPacketEntities).
ANIMATED = {'func_wall': (8, 16), 'func_object': (2, 4), 'func_explosive': (2, 4), 'func_door': (16, 64),
            'func_door_rotating': (16, 0), 'func_rotating': (64, 128)}


def frame_rate(bsp, instance, info):
    """(frames per second, frame limit) of a face's nexttexinfo chain, or None to hold its first frame. The world's
    frame is (int)(time * 2) (gl_rsurf.c R_DrawWorld) and R_TextureAnimation steps frame % numframes along the chain."""
    if instance['model'] == 0:
        return 2, 0
    flags = int(bsp['entities'][instance['entity']].get('spawnflags') or 0)
    if instance['classname'] == 'func_button':
        return 2, 2
    slow, fast = ANIMATED.get(instance['classname'], (0, 0))
    return (2, 0) if flags & slow else (10, 0) if flags & fast else None


def world_frame_rate(bsp, instance, info):
    # Hypothesis: these Quake II-derived renderers keep R_DrawWorld's time * 2; brush entities hold frame 0.
    return (2, 0) if instance['model'] == 0 else None


def texinfo_chain(bsp, info, limit=0):
    """Mod_LoadTexinfo's chain from one texinfo: next until -1 or back to the start (numframes)."""
    chain, step = [info], bsp['nexttexinfo'][info]
    while step not in (-1, info) and len(chain) < 64:
        chain.append(step)
        step = bsp['nexttexinfo'][step]
    return chain[:limit] if limit else chain


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


def read_bsp(data, files, *, texture_loader=None, header=(b'IBSP', 38, 19),
             face_format='Hhihh4Bi', texinfo_format='8fii32si', texinfo_fields=(10, 11)):
    """Shared Q2 topology; alternate on-disk layouts keep the default BSP38 contract."""
    magic, version, count = header
    header_size = 8 + count * 8
    if len(data) < header_size or data[:4] != magic or struct.unpack_from('<i', data, 4)[0] != version:
        raise ValueError(f'Expected {magic.decode()} version {version}')
    lumps = []
    for offset, size in struct.iter_unpack('<ii', data[8:header_size]):
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('Invalid BSP38 lump range')
        lumps.append(data[offset:offset + size])

    def rows(index, fmt):
        if len(lumps[index]) % struct.calcsize('<' + fmt):
            raise ValueError(f'Incomplete BSP38 lump {index}')
        return list(struct.iter_unpack('<' + fmt, lumps[index]))

    bsp = dict(entities=entities(lumps[0]), planes=rows(1, '4fi'), points=rows(2, '3f'),
               nodes=rows(4, '3i6h2H'), leaves=rows(8, 'ihh6h4H'),
               faces=rows(6, face_format), lighting=lumps[7], edges=rows(11, '2H'),
               surfedges=[r[0] for r in rows(12, 'i')], models=rows(13, '9f3i'))
    if not bsp['models']:
        raise ValueError('BSP38 has no world model')
    for key in ('planes', 'points', 'models'):
        if any(not math.isfinite(v) for row in bsp[key] for v in row):
            raise ValueError(f'Non-finite BSP38 {key}')
    textures, texinfo, chains = [], [], []
    cache = {}
    for i, row in enumerate(rows(5, texinfo_format)):
        if not all(math.isfinite(v) for v in row[:8]):
            raise ValueError('Non-finite BSP38 texinfo')
        name = asset_name(row[texinfo_fields[0]].split(b'\0')[0].decode('latin1'))
        path = 'textures/' + name + '.wal'
        if path not in cache:
            if texture_loader is not None:
                cache[path] = texture_loader(name)
            elif path in files:
                cache[path] = dict(read_wal(files[path][0]), name=name)
            else:
                cache[path] = dict(name=name, width=16, height=16, missing=True,
                                   pixels=bytes(0 if (x < 8) != (y < 8) else 255 for y in range(16) for x in range(16)))
        textures.append(cache[path])
        texinfo.append((*row[:8], i, row[8]))  # Adapt to the shared Quake polygon/extents helper.
        chains.append(row[texinfo_fields[1]])
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


# Maps with only named starts: a fresh `map NAME` takes the first start G_Find meets, and edict slots freed while
# spawning are reused, so it is not always the first in the file. Measured with viewpos in Yamagi Quake II 8.70a
# on every such map of baseq2, xatrix and rogue; the rest take the first named start.
FRESH_START = {('quake2', 'mine1'): 'mintro', ('quake2', 'mine2'): 'mine1', ('quake2', 'mine3'): 'mine2a',
               ('quake2', 'mine4'): 'mine3', ('quake2', 'power2'): 'power1', ('quake2', 'waste1'): 'power2',
               ('quake2', 'waste2'): 'waste1', ('quake2', 'city2'): 'city2NL'}


def ordered_starts(bsp, game):
    starts = [e for e in bsp['entities'] if e.get('classname') == 'info_player_start']
    fresh = FRESH_START.get((game, bsp.get('name')))
    ordered = sorted(starts, key=lambda e: (bool(e.get('targetname')), e.get('targetname') != fresh))
    if game == 'ctf':
        # CTFSelectSpawnPoint chooses a team spot on entry; make the red team's first
        # authored spot deterministic, with all other team/DM spots available to inspect.
        ordered = [e for cls in ('info_player_team1', 'info_player_team2')
                   for e in bsp['entities'] if e.get('classname') == cls]
    for cls in ('info_player_deathmatch', 'info_player_intermission'):
        ordered += [e for e in bsp['entities'] if e.get('classname') == cls]
    return [e for e in ordered if not excluded(e, game)]


def instances(bsp, game='quake2', *, open_at_start=True):
    """Initial brush states: medium SP, or deathmatch filtering for CTF; motion frozen."""
    result = [dict(model=0, entity=0, classname='worldspawn', offset=[0., 0., 0.], angles=[0., 0., 0.], state='world', guess=False)]
    skipped, moving = [], {}
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
                path = train_path(bsp, entity, quake2=True)
                if path:
                    state, moving[index] = 'travels its path_corner loop (g_func.c train_next)', dict(path, pivot=[0, 0, 0])
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
            spin = rotating(entity, 'speed')  # START_ON: turning from the start (SP_func_rotating)
            if spin:
                state = 'turns from the start at speed degrees a second (START_ON)'
                moving[index] = dict(pivot=[round(float(c), 4) for c in offset[[1, 2, 0]] * SCALE],
                                     spin=[*(round(float(c), 6) for c in np.array(spin[:3])[[1, 2, 0]]), round(spin[3], 6)])
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
        result.append(dict(model=number, entity=index, classname=cls, offset=offset.tolist(), angles=turn.tolist(), state=state, guess=guess,
                           **({'motion': moving[index]} if index in moving else {})))
    if open_at_start:
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


def geometry(bsp, placed, game='quake2', *, face_reader=face_data, dark_styles=None, lightmap_reader=lightmap_rgb, frames=frame_rate):
    dark = frozenset(dark_styles) if dark_styles is not None else frozenset(int(e['style']) for e in bsp['entities'] if e.get('classname', '').startswith('light')
                     and int(e.get('style', '0')) >= 32 and int(e.get('spawnflags') or 0) & 1
                     and not excluded(e, game) and game != 'ctf')  # SP_light frees lights in deathmatch.
    faces, tiles, extras, audit, floors = [], [], [], [], []

    def light(face):
        # One animated style per face leaves the base tile for a raw sample the shader scales each frame (further
        # animated styles stay folded in at 'm'); unlit and unlightmapped faces have none.
        styles = animated_styles(face, 1) if not face['unlit'] and face['lightofs'] != -1 and bsp['lighting'] else []
        tiles.append(lightmap_reader(face, bsp['lighting'], dark | set(styles)))
        extras.append(style_sample(face, bsp['lighting'], styles[0], 3) if styles else None)
        return styles
    for instance in placed:
        first, count = bsp['models'][instance['model']][10:12]
        if first < 0 or count < 0 or first + count > len(bsp['faces']):
            raise ValueError('Invalid BSP38 model face range')
        matrix = rotation(instance['angles'])
        for number in range(first, first + count):
            face = face_reader(bsp, number)
            if face['kind'] == 'hidden':
                continue
            points = face['points'] @ matrix.T + instance['offset']
            info = bsp['faces'][number][4]
            rate = frames(bsp, instance, info)
            chain = texinfo_chain(bsp, info, rate[1]) if rate else [info]
            face = dict(face, frames=[bsp['textures'][i] for i in chain], fps=rate[0] if rate and len(chain) > 1 else 0)
            if face['kind'] == 'turbulent':
                vectors = np.array(bsp['texinfo'][info][:8]).reshape(2, 4)
                for polygon in warp_polygons(face['points']):
                    faces.append(dict(face, points=polygon, placed_points=polygon @ matrix.T + instance['offset'],
                                      st=polygon @ vectors[:, :3].T,
                                      light_st=polygon @ vectors[:, :3].T + vectors[:, 3]))  # Warp texture ST omits offsets; lightmaps do not.
                    faces[-1]['styles_animated'] = light(face)
                    faces[-1]['mover'] = instance.get('mover')
            else:
                faces.append(dict(face, placed_points=points))
                faces[-1]['styles_animated'] = light(face)
            faces[-1]['mover'] = instance.get('mover')
            audit.append(dict(face=number, entity=instance['entity'], texture=face['texture']['name'], kind=face['kind'],
                              flags=face['flags'], unlit=face['unlit'], alpha=face['alpha'], styles=face['styles'],
                              texturemins=face['mins'].tolist(), luxels=face['size'].tolist(), lightofs=face['lightofs'],
                              nexttexinfo=face['nexttexinfo'], missing=face['texture'].get('missing', False)))
            if face['kind'] == 'normal' and (matrix @ face['normal'])[2] > .7:
                floors.extend((points[0], points[k], points[k + 1]) for k in range(1, len(points) - 1))
                faces[-1]['floor'] = True
    if not faces:
        raise ValueError('No visible BSP38 faces')
    width, height, places, atlas, offset = style_atlas(tiles, extras)
    positions, uvs, uv2, groups, textures, floor_uv = [], [], [], {}, {}, []
    png_of = lambda tex: hashlib.sha256(struct.pack('<II', tex['width'], tex['height']) + tex['pixels']).hexdigest()[:24] + '.png'
    for index, face in enumerate(faces):
        tex = face['texture']
        png = png_of(tex)
        textures[png] = tex
        anim = [png_of(t) for t in face['frames']] if face['fps'] else []
        textures.update({png_of(t): t for t in face['frames']} if anim else {})
        styles = face['styles_animated']
        group = groups.setdefault((png, face['flags'], face.get('material_key'), tuple(anim), face['fps'], tuple(styles), face.get('mover')), dict(texture=png, name=tex['name'], kind=face['kind'], flags=face['flags'],
            size=[tex['width'], tex['height']], unlit=face['unlit'], alpha=face['alpha'], indices=[], **({'mover': face['mover']} if face.get('mover') is not None else {})))
        if 'settings' in face:
            group['settings'] = face['settings']
        if anim:
            group['anim'] = dict(frames=anim, fps=face['fps'])
        if styles:
            group['styles'] = styles
        base = len(positions)
        positions.extend(face['placed_points'])
        uvs.extend(face['st'] / [tex['width'], tex['height']])
        for k in range(1, len(face['points']) - 1):
            group['indices'].extend((base, base + k, base + k + 1))
        x, y = places[index]
        h, w = tiles[index].shape[:2]
        coords = (face.get('light_st', face['st']) - face['mins']) / 16 if not face['unlit'] else np.zeros_like(face['st'])
        uv2.extend(at := (coords + [x + 1.5, y + 1.5]) / [width, height])
        if face.get('floor'):  # in the order of `floors`: where a model standing on it reads the light below it
            floor_uv.extend(at[[0, k, k + 1]] for k in range(1, len(face['points']) - 1))
    indices, table = [], []
    for group in groups.values():
        part = group.pop('indices')
        table.append(dict(group, start=len(indices), count=len(part)))
        indices.extend(part)
    return dict(points=np.array(positions), uvs=np.array(uvs), uv2=np.array(uv2), indices=indices, groups=table,
                textures=textures, atlas=Image.fromarray(atlas), audit=audit, floors=np.array(floors), floor_uv=floor_uv,
                dark=sorted(dark), style_offset=offset)


# The mean of ref_gl's r_avertexnormal_dots (anormtab.h) over the 162 vertex normals, every yaw row: what a lit alias
# model's vertex colour (shade dot x shadelight) averages. ponytail: per-vertex dots if a model's shading needs it.
SHADE_DOTS = 1.175


def light_below(data, origin):
    """The lightmap coordinate (uv2) of the floor straight below `origin` (R_LightPoint, gl_light.c: the light of the
    surface the line down meets), or None: a model drawn there reads the world's own lightmap, styles included."""
    found = floor_below(data['floors'], origin[0], origin[1], origin[2] + 1, which=True) if len(data['floors']) else None
    if found is None or found[1] >= len(data.get('floor_uv', ())):
        return None
    a, b, c = data['floors'][found[1]][:, :2]
    try:
        u, v = np.linalg.solve(np.column_stack([b - a, c - a]), np.asarray(origin[:2], float) - a)
    except np.linalg.LinAlgError:
        return None
    p, q, r = data['floor_uv'][found[1]]
    return p + u * (q - p) + v * (r - p)


def scene_extras(bsp, data, blends=CONTENT_BLENDS, scale=1 / 12):
    """Optional scene keys: light_styles (cl_fx.c CL_SetLightstyle: value = (char - 'a') / ('m' - 'a'); gl_rsurf.c
    R_BuildLightMap adds sample * value) and map.regions/region_tree from the world's leaf contents (point contents,
    brush entities such as func_water left out)."""
    extra = {}
    used = sorted({s for g in data['groups'] for s in g.get('styles', [])})
    if used:
        extra['light_styles'] = dict(offset=data['style_offset'], scale=scale, patterns={str(s): LIGHT_STYLES[s] for s in used})
    if blends and bsp.get('nodes'):
        regions, tree = content_regions(bsp['planes'], bsp['nodes'], bsp['models'][0][9], lambda leaf: bsp['leaves'][leaf][0], blends)
        if regions:
            extra.update(regions=regions, region_tree=tree)
    return extra


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


# id-Software/quake2-rerelease-dll/original/{baseq2,xatrix,rogue}: SP_monster_*.
# Tanks, boss vehicles and sentry monsters act as characters. No target_spawner offspring.
MONSTER_MODELS = {
    'berserk': 'berserk', 'gladiator': 'gladiatr', 'gunner': 'gunner', 'infantry': 'infantry',
    'soldier_light': 'soldier', 'soldier': 'soldier', 'soldier_ss': 'soldier',
    'tank': 'tank', 'tank_commander': 'tank', 'medic': 'medic', 'flipper': 'flipper',
    'chick': 'bitch', 'chick_heat': 'bitch', 'parasite': 'parasite', 'flyer': 'flyer', 'kamikaze': 'flyer', 'brain': 'brain', 'floater': 'float',
    'hover': 'hover', 'mutant': 'mutant', 'supertank': 'boss1', 'boss2': 'boss2',
    'jorg': 'boss3/jorg', 'makron': 'boss3/rider', 'boss3_stand': 'boss3/rider',
    'gekk': 'gekk', 'gladb': 'gladb', 'boss5': 'boss5', 'fixbot': 'fixbot',
    'soldier_hypergun': 'soldierh', 'soldier_lasergun': 'soldierh', 'soldier_ripper': 'soldierh',
    'carrier': 'carrier', 'stalker': 'stalker', 'turret': 'turret', 'widow': 'blackwidow',
    'widow2': 'blackwidow2', 'medic_commander': 'medic', 'daedalus': 'hover',
}
MONSTER_SKINS = {'soldier': 2, 'soldier_ss': 4, 'tank_commander': 2, 'medic_commander': 2,
                 'daedalus': 2, 'soldier_hypergun': 2, 'soldier_lasergun': 4, 'chick_heat': 3}


def append_monsters(data, bsp, files, game):
    if __package__:
        from .import_quake2 import read_md2, build_model, choose_skin
        from .import_quake_map import append_alias
    else:
        from import_quake2 import read_md2, build_model, choose_skin
        from import_quake_map import append_alias
    records, images, cache = [], {}, {}
    for entity, e in enumerate(bsp['entities']):
        cls = e.get('classname', '')
        if not cls.startswith('monster_'):
            continue
        row = dict(entity=entity, classname=cls, npc=True, parts=[])
        records.append(row)
        name = cls.removeprefix('monster_')
        path = 'models/monsters/' + MONSTER_MODELS[name] + '/tris.md2' if name in MONSTER_MODELS else ''
        reason = ('Monsters removed in deathmatch' if game == 'ctf' else
                  'Excluded on medium skill' if excluded(e, game) else
                  'No verified placed model mapping (helper/corpse)' if not path else '')
        if reason:
            row.update(status='omitted', reason=reason)
            continue
        if name == 'turret' and int(e.get('spawnflags') or 0) & 128 and not e.get('targetname'):
            row.update(status='omitted', reason='Wall turret without targetname is removed by spawn')
            continue
        try:
            # Triggered monsters are authored previews, not new populations. Keep their identity.
            skin = MONSTER_SKINS.get(name, int(float(e.get('skin') or 0)))
            flags = int(e.get('spawnflags') or 0)
            if name == 'turret':
                skin = 1 if flags & 16 else 2 if flags & 32 else 0
            origin, turn = vector(e), angles(e)
            if name == 'turret':
                if turn[1] in (-1, -2):
                    turn = np.array([270. if turn[1] == -1 else 90., 0., 0.])
                origin += rotation(turn)[:, 0] * 2
            paths = [path]
            if name == 'jorg':
                paths.append('models/monsters/boss3/rider/tris.md2')
            if name == 'turret' and flags & 128:
                paths.append('models/monsters/turretbase/tris.md2')
            for part_index, path in enumerate(paths):
                part_skin = skin if part_index == 0 else 0
                key = path, part_skin
                if key not in cache:
                    if path not in files:
                        raise ValueError('Missing monster model ' + path)
                    mesh = read_md2(files[path][0], standing=True)
                    skin_path, fallback = choose_skin(path, mesh, files, part_skin)
                    if not skin_path:
                        raise ValueError(fallback)
                    image = read_pcx(files[skin_path][0])
                    # Alias previews use an even .5 light multiplier, like other unlit groups.
                    image = image.point([min(255, i * INTENSITY) for i in range(256)] * 3)
                    png = 'md2_' + hashlib.sha256(image.tobytes() + struct.pack('<II', *image.size)).hexdigest()[:24] + '.png'
                    images[png] = image
                    cache[key] = mesh, build_model(mesh, png), png, skin_path, fallback
                mesh, model, png, skin_path, fallback = cache[key]
                append_alias(data, model, origin, turn,
                             dict(texture=png, kind='model', flags=0, size=list(images[png].size), unlit=True, alpha=1,
                                  npc=True, entity=entity, classname=cls))
                part = dict(model=path, skin=skin_path, skin_number=part_skin, frame=mesh['frame'],
                            pose=mesh['pose'], fallback=fallback)
                row['parts'].append(part)
                if part_index == 0:
                    row.update(part)
            row.update(status='drawn', triggered=bool(flags & 2))
        except (ValueError, IndexError, struct.error) as exc:
            row.update(status='partial' if row['parts'] else 'failed', reason=str(exc))
    return records, images


# id-Software/quake2-rerelease-dll original/{baseq2,xatrix,rogue}/g_items.c itemlist: each item's world model, and 1
# where its world_model_flags hold EF_ROTATE (SP_item_health* set their models themselves, none turning).
ITEM_MODELS = {
    'item_armor_body': ('models/items/armor/body/tris.md2', 1),
    'item_armor_combat': ('models/items/armor/combat/tris.md2', 1),
    'item_armor_jacket': ('models/items/armor/jacket/tris.md2', 1),
    'item_armor_shard': ('models/items/armor/shard/tris.md2', 1),
    'item_power_screen': ('models/items/armor/screen/tris.md2', 1),
    'item_power_shield': ('models/items/armor/shield/tris.md2', 1),
    'weapon_shotgun': ('models/weapons/g_shotg/tris.md2', 1),
    'weapon_supershotgun': ('models/weapons/g_shotg2/tris.md2', 1),
    'weapon_machinegun': ('models/weapons/g_machn/tris.md2', 1),
    'weapon_chaingun': ('models/weapons/g_chain/tris.md2', 1),
    'ammo_grenades': ('models/items/ammo/grenades/medium/tris.md2', 0),
    'weapon_grenadelauncher': ('models/weapons/g_launch/tris.md2', 1),
    'weapon_rocketlauncher': ('models/weapons/g_rocket/tris.md2', 1),
    'weapon_hyperblaster': ('models/weapons/g_hyperb/tris.md2', 1),
    'weapon_railgun': ('models/weapons/g_rail/tris.md2', 1), 'weapon_bfg': ('models/weapons/g_bfg/tris.md2', 1),
    'ammo_shells': ('models/items/ammo/shells/medium/tris.md2', 0),
    'ammo_bullets': ('models/items/ammo/bullets/medium/tris.md2', 0),
    'ammo_cells': ('models/items/ammo/cells/medium/tris.md2', 0),
    'ammo_rockets': ('models/items/ammo/rockets/medium/tris.md2', 0),
    'ammo_slugs': ('models/items/ammo/slugs/medium/tris.md2', 0), 'item_quad': ('models/items/quaddama/tris.md2', 1),
    'item_invulnerability': ('models/items/invulner/tris.md2', 1),
    'item_silencer': ('models/items/silencer/tris.md2', 1), 'item_breather': ('models/items/breather/tris.md2', 1),
    'item_enviro': ('models/items/enviro/tris.md2', 1), 'item_ancient_head': ('models/items/c_head/tris.md2', 1),
    'item_adrenaline': ('models/items/adrenal/tris.md2', 1), 'item_bandolier': ('models/items/band/tris.md2', 1),
    'item_pack': ('models/items/pack/tris.md2', 1), 'key_data_cd': ('models/items/keys/data_cd/tris.md2', 1),
    'key_power_cube': ('models/items/keys/power/tris.md2', 1),
    'key_pyramid': ('models/items/keys/pyramid/tris.md2', 1),
    'key_data_spinner': ('models/items/keys/spinner/tris.md2', 1),
    'key_pass': ('models/items/keys/pass/tris.md2', 1), 'key_blue_key': ('models/items/keys/key/tris.md2', 1),
    'key_red_key': ('models/items/keys/red_key/tris.md2', 1),
    'key_commander_head': ('models/monsters/commandr/head/tris.md2', 0),
    'key_airstrike_target': ('models/items/keys/target/tris.md2', 1),
    'item_health': ('models/items/healing/medium/tris.md2', 0),
    'item_health_small': ('models/items/healing/stimpack/tris.md2', 0),
    'item_health_large': ('models/items/healing/large/tris.md2', 0),
    'item_health_mega': ('models/items/mega_h/tris.md2', 0), 'ammo_trap': ('models/weapons/g_trap/tris.md2', 1),
    'weapon_boomer': ('models/weapons/g_boom/tris.md2', 1), 'weapon_phalanx': ('models/weapons/g_shotx/tris.md2', 1),
    'ammo_magslug': ('models/objects/ammo/tris.md2', 0), 'item_quadfire': ('models/items/quadfire/tris.md2', 1),
    'key_green_key': ('models/items/keys/green_key/tris.md2', 1),
    'weapon_etf_rifle': ('models/weapons/g_etf_rifle/tris.md2', 1),
    'weapon_proxlauncher': ('models/weapons/g_plaunch/tris.md2', 1),
    'weapon_plasmabeam': ('models/weapons/g_beamer/tris.md2', 1),
    'weapon_chainfist': ('models/weapons/g_chainf/tris.md2', 1),
    'weapon_disintegrator': ('models/weapons/g_dist/tris.md2', 1),
    'ammo_flechettes': ('models/ammo/am_flechette/tris.md2', 0), 'ammo_prox': ('models/ammo/am_prox/tris.md2', 0),
    'ammo_tesla': ('models/weapons/g_tesla/tris.md2', 0), 'ammo_nuke': ('models/weapons/g_nuke/tris.md2', 1),
    'ammo_disruptor': ('models/ammo/am_disr/tris.md2', 0), 'item_ir_goggles': ('models/items/goggles/tris.md2', 1),
    'item_double': ('models/items/ddamage/tris.md2', 1), 'item_torch': ('models/objects/fire/tris.md2', 1),
    'item_compass': ('models/objects/fire/tris.md2', 1),
    'item_sphere_vengeance': ('models/items/vengnce/tris.md2', 1),
    'item_sphere_hunter': ('models/items/hunter/tris.md2', 1),
    'item_sphere_defender': ('models/items/defender/tris.md2', 1),
}
# g_misc.c spawn functions that draw a model of their own: (model, frame); misc_explobox and the gibs leave deathmatch,
# misc_deadsoldier's frame follows its spawnflags (2, 4, 8, 16, 32: frames 1-5). misc_banner animates (first frame here);
# misc_viper and misc_strogg_ship stay hidden until triggered, so they are left out.
MISC_MODELS = {
    'misc_explobox': ('models/objects/barrels/tris.md2', 0), 'misc_banner': ('models/objects/banner/tris.md2', 0),
    'misc_satellite_dish': ('models/objects/satellite/tris.md2', 0), 'light_mine1': ('models/objects/minelite/light1/tris.md2', 0),
    'light_mine2': ('models/objects/minelite/light2/tris.md2', 0), 'misc_bigviper': ('models/ships/bigviper/tris.md2', 0),
    'misc_blackhole': ('models/objects/black/tris.md2', 0), 'misc_deadsoldier': ('models/deadbods/dude/tris.md2', 0),
    'misc_gib_arm': ('models/objects/gibs/arm/tris.md2', 0), 'misc_gib_leg': ('models/objects/gibs/leg/tris.md2', 0),
    'misc_gib_head': ('models/objects/gibs/head/tris.md2', 0), 'misc_teleporter_dest': ('models/objects/dmspot/tris.md2', 0),
    'misc_eastertank': ('models/monsters/tank/tris.md2', 254), 'misc_easterchick': ('models/monsters/bitch/tris.md2', 208),
    'misc_easterchick2': ('models/monsters/bitch/tris.md2', 248),
}
DEATHMATCH_GONE = {'misc_explobox', 'misc_gib_arm', 'misc_gib_leg', 'misc_gib_head'}


def append_items(data, bsp, files, game, first_mover=0):
    """Placed items, weapons and keys (ITEM_MODELS) and misc models (MISC_MODELS) as their spawn functions draw them:
    items drop to the floor (droptofloor: a 30-unit box traced down 128 units, so its origin rests 15 over the floor),
    barrels rest on theirs (M_droptofloor, mins z 0); EF_ROTATE items turn at 100 degrees a second whatever their angle
    (CL_AddPacketEntities: autorotate = anglemod(cl.time / 10)). Returns (records, images, movers)."""
    if __package__:
        from .import_quake2 import read_md2, build_model, choose_skin
        from .import_quake_map import append_alias
    else:
        from import_quake2 import read_md2, build_model, choose_skin
        from import_quake_map import append_alias
    records, images, movers, cache = [], {}, [], {}
    for entity, e in enumerate(bsp['entities']):
        cls = e.get('classname', '')
        if cls not in ITEM_MODELS and cls not in MISC_MODELS:
            continue
        row = dict(entity=entity, classname=cls)
        records.append(row)
        flags = int(float(e.get('spawnflags') or 0))
        reason = ('Excluded on medium skill' if excluded(e, game) else
                  'Removed in deathmatch' if game == 'ctf' and (cls in DEATHMATCH_GONE or cls.startswith('key_')) else '')
        if reason:
            row.update(status='omitted', reason=reason)
            continue
        try:
            path, turning = ITEM_MODELS[cls] if cls in ITEM_MODELS else (MISC_MODELS[cls][0], 0)
            pick = MISC_MODELS[cls][1] if cls in MISC_MODELS else 0
            if cls == 'misc_deadsoldier':
                pick = next((n for n, bit in enumerate((2, 4, 8, 16, 32), 1) if flags & bit), 0)
            key = path, pick
            if key not in cache:
                if path not in files:
                    raise ValueError('Missing model ' + path)
                mesh = read_md2(files[path][0], pick=pick)
                skin_path, fallback = choose_skin(path, mesh, files, 0)
                if not skin_path:
                    raise ValueError(fallback)
                image = read_pcx(files[skin_path][0]).point([min(255, i * INTENSITY) for i in range(256)] * 3)
                png = 'md2_' + hashlib.sha256(image.tobytes() + struct.pack('<II', *image.size)).hexdigest()[:24] + '.png'
                images[png] = image
                cache[key] = build_model(mesh, png), png
            model, png = cache[key]
            origin = vector(e)
            rest = 15 if cls in ITEM_MODELS else 0 if cls == 'misc_explobox' else None
            if rest is not None:
                floor = floor_below(data['floors'], origin[0], origin[1], origin[2] + 1) if len(data['floors']) else None
                if floor is not None and origin[2] - rest - floor <= (128 if rest else 256):
                    origin = np.array([origin[0], origin[1], floor + rest])
            # Lit by the light below it (R_LightPoint; Yamagi gl1: jail1's explobox and fact1's ammo in dim rooms showed
            # 1.6-2.8x darker than an even light), else the even preview light.
            below = light_below(data, origin)
            group = dict(texture=png, kind='model', flags=0, size=list(images[png].size), unlit=below is None, alpha=1, entity=entity, classname=cls,
                         **({} if below is None else dict(shade=SHADE_DOTS, glow=cls in ITEM_MODELS)))
            if turning:
                group['mover'] = first_mover + len(movers)
                movers.append(dict(pivot=[round(float(c), 4) for c in origin[[1, 2, 0]] * SCALE], spin=[0, 1, 0, round(math.radians(100), 6)]))
            append_alias(data, model, origin, np.zeros(3) if turning else angles(e), group)
            if below is not None:
                data['uv2'][len(data['uv2']) - len(np.asarray(model['vertices']).reshape(-1, 3)):] = below
            row.update(status='drawn', model=path, frame=pick, origin=origin.tolist(), turning=bool(turning))
        except (ValueError, IndexError, struct.error) as exc:
            row.update(status='failed', reason=str(exc))
    return records, images, movers


def build_map(raw, name, files, palette, game='quake2'):
    bsp = read_bsp(raw, files)
    bsp['name'] = name
    placed, skipped = instances(bsp, game)
    movers = []
    for instance in placed:  # trains and rotating brushes that move on their own: their own groups, moved by the page
        if instance.get('motion'):
            instance['mover'] = len(movers)
            movers.append(instance['motion'])
    data = geometry(bsp, placed, game)
    models, model_images = append_monsters(data, bsp, files, game)
    items, item_images, item_movers = append_items(data, bsp, files, game, len(movers))
    movers += item_movers
    model_images.update(item_images)
    points = data['points'][:, [1, 2, 0]] * SCALE
    views = viewpoints(bsp, data['floors'], game)
    info = next((e for e in bsp['entities'] if e.get('classname') == 'worldspawn'), {})
    sky, images = skybox(info, files)
    images.update(model_images)
    for png, tex in data['textures'].items():
        images[png] = upload_texture(tex, palette)
    blob = b''.join((points.astype('<f4').tobytes(), data['uvs'].astype('<f4').tobytes(),
                     data['uv2'].astype('<f4').tobytes(), np.array(data['indices'], '<u4').tobytes()))
    scene = dict(name=name, title=info.get('message', ''), game=game, format='quake2-bsp38', **scene_extras(bsp, data),
                 vertices=len(points), indices=len(data['indices']), lightmap=list(data['atlas'].size),
                 groups=data['groups'], viewpoints=views, bounds=np.array(bsp['models'][0][:6]).reshape(2, 3).tolist(),
                 instances=placed, models=models, model_failures=[r for r in models + items if r['status'] in ('failed', 'partial')],
                 items=items, **({'movers': movers} if movers else {}), skipped_entities=skipped, faces=data['audit'], skybox=sky,
                 entity_classes=dict(Counter(e.get('classname', '') for e in bsp['entities'])), dark_styles=data['dark'],
                 missing=sorted({f['texture'] for f in data['audit'] if f['missing'] and f['kind'] != 'sky'} | set(sky['missing'])),
                 lighting=dict(renderer='ref_gl 3.20', intensity=2, gl_modulate=1, vid_gamma=1, normal_style=1),
                 assumptions=[('CTF deathmatch filtering; first authored red team start' if game == 'ctf' else 'medium skill single player'),
                              ('styles 1-11 animate at 10 Hz (one per face, others at m); SP_light removed in deathmatch' if game == 'ctf' else
                               'styles 1-11 animate at 10 Hz (one per face, others at m); switchable styles m except START_OFF styles a'),
                              'initial brush states, trains at first corner before travel',
                              'texture chains: world at 2 frames a second, brush entities by their ANIMATED spawnflags, others first frame',
                              'placed monster standing/idle frame (first pose fallback), even lighting, authored origin; triggered previews included; no runtime summons',
                              'items, weapons, keys and misc models in their first (or spawn-set) frame, lit by the light below them, dropped to the face floors below; EF_ROTATE items turn at 100 degrees a second'])
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

"""Anachronox BSP38 maps, with static brush entities and first-pose NPCs/props.

python -m tools.import_anachronox_map --install "C:/Program Files (x86)/Steam/steamapps/common/Anachronox" --replace
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct

import numpy as np
from PIL import Image

from tools import import_anachronox as anox, import_quake2_map as q2
from tools.import_daikatana_map import hull_floor, light_point
from tools.import_quake_map import SCALE, floor_below, rotation, vector
from tools.local_data import LOCAL_DATA

ALPHA_BANNER, ALPHA_TEST, FOG = 0x10000, 0x20000, 0x40000000
SURFACE_PROPERTIES = dict(zip(('wood', 'metal', 'stone', 'carpet', 'ice', 'snow', 'alphabanner', 'alphatest'),
                             (1 << i for i in range(10, 18))))
SURFACE_PROPERTIES.update(dict(zip(('hollow', 'puddle', 'gravel', 'leaves', 'grass', 'sand', 'water'),
                                  (1 << i for i in (19, 20, 22, 23, 24, 25, 26)))))
LUMP_FORMATS = {1: '4fi', 2: '3f', 4: '3i6h2H', 5: '8fii32si', 6: 'Hhihh4Bi',
                8: 'ihh6h4H', 9: 'H', 10: 'H', 11: '2H', 12: 'i', 13: '9f3i',
                14: '3i', 15: 'Hh', 17: '2i', 18: '2i'}


def number(entity, key, default=0):
    value = float(entity.get(key, default) or default)
    if not math.isfinite(value):
        raise ValueError(f'Non-finite entity {key}')
    return value


def world_fog(text):
    """Settled gl_fog target: density [RGB [transition milliseconds]].

    ref_gl 10019301 uses atof/atoi, retains colour when RGB is omitted and
    treats negatives as restore-previous. A fresh direct load has zero history.
    Do not reject a map for a malformed console-style fog argument.
    """
    tokens = text.split()
    warnings = []

    def value(index, integer=False):
        if index >= len(tokens):
            return 0
        pattern = r'^[+-]?\d+' if integer else r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
        match = re.match(pattern, tokens[index])
        v = float(match[0]) if match else 0
        if not match or not math.isfinite(v):
            warnings.append('Invalid fog argument replaced with zero: ' + tokens[index])
            return 0
        return max(0, int(v) if integer else v)

    return dict(density=value(0), color=[value(i) for i in (1, 2, 3)] if len(tokens) >= 4 else [0, 0, 0],
                mode='exp', transition_ms=value(4, integer=True), warnings=warnings)


def checker():
    y, x = np.indices((16, 16))
    pixels = np.where(((x // 8 + y // 8) % 2)[..., None], [255, 0, 255, 255], [24, 24, 24, 255])
    return Image.fromarray(pixels.astype(np.uint8))


def texture_info(files):
    """Textureinfo is a sectioned list of surface properties, not an image index."""
    path = 'textures/textureinfo.dat'
    result, section = {}, ''
    if path not in files:
        return result
    text = re.sub(r'//[^\r\n]*', '', anox.read_asset(files[path]).decode('latin1'))
    for token in re.findall(r'"[^"]*"|\S+', text):
        token = token.strip('"').lower()
        if token.startswith('#'):
            section = token[1:]
        else:
            name = str(PurePosixPath(anox.asset_name(token)).with_suffix('')).removeprefix('textures/')
            result.setdefault(name, []).append(section)
    return result


def texture(name, files, properties=None):
    path = name if name.startswith(('textures/', 'graphics/')) else 'textures/' + name
    candidates = anox.skin_candidates('', path)
    source = next((p for p in candidates if p in files), '')
    reason = ''
    try:
        if not source:
            raise ValueError('No supplied TGA/PNG/ATD: ' + ', '.join(candidates))
        image = anox.read_texture(source, files)
    except (ValueError, OSError, KeyError) as exc:
        image, reason = checker(), str(exc)
    alpha_min, alpha_max = image.getchannel('A').getextrema()
    return dict(name=name, source=source, width=image.width, height=image.height, pixels=image.tobytes(),
                texture_alpha=alpha_min < 255, blank=alpha_max == 0,
                image=image, missing=bool(reason), reason=reason, properties=(properties or {}).get(name, []))


def read_bsp(raw, files):
    properties = texture_info(files)
    bsp = q2.read_bsp(raw, files, texture_loader=lambda name: texture(name, files, properties))
    ranges = list(struct.iter_unpack('<ii', raw[8:160]))
    for i, fmt in LUMP_FORMATS.items():
        if ranges[i][1] % struct.calcsize('<' + fmt):
            raise ValueError(f'Incomplete Anachronox lump {i}')
    for key, i in (('nodes', 4), ('leaves', 8), ('leafbrushes', 10), ('brushes', 14), ('brushsides', 15)):
        offset, size = ranges[i]
        rows = list(struct.iter_unpack('<' + LUMP_FORMATS[i], raw[offset:offset + size]))
        bsp[key] = [r[0] for r in rows] if key == 'leafbrushes' else rows
    for row in bsp['nodes']:
        if not 0 <= row[0] < len(bsp['planes']) or any(n >= len(bsp['nodes']) or -n - 1 >= len(bsp['leaves']) for n in row[1:3]):
            raise ValueError('Invalid BSP node reference')
        if row[9] + row[10] > len(bsp['faces']):
            raise ValueError('Invalid node face range')
    # Light sampling walks the tree. Reject cycles before it can loop forever.
    visited, active = set(), set()
    for root in range(len(bsp['nodes'])):
        stack = [(root, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                active.remove(node)
                visited.add(node)
            elif node >= 0 and node not in visited:
                if node in active:
                    raise ValueError('Cyclic BSP nodes')
                active.add(node)
                stack.append((node, True))
                stack.extend((child, False) for child in bsp['nodes'][node][1:3])
    if bsp['nodes'] and any(m[9] >= len(bsp['nodes']) or -m[9] - 1 >= len(bsp['leaves']) for m in bsp['models']):
        raise ValueError('Invalid model headnode')
    for row in bsp['leaves']:
        if row[11] + row[12] > len(bsp['leafbrushes']):
            raise ValueError('Invalid leaf brush range')
    if any(i >= len(bsp['brushes']) for i in bsp['leafbrushes']):
        raise ValueError('Invalid leaf brush index')
    for first, count, _ in bsp['brushes']:
        if first < 0 or count < 0 or first + count > len(bsp['brushsides']):
            raise ValueError('Invalid brush side range')
    if any(p >= len(bsp['planes']) for p, _ in bsp['brushsides']):
        raise ValueError('Invalid brush plane')
    for i, row in enumerate(bsp['texinfo']):
        props = bsp['textures'][i]['properties']
        # Retail replaces stale editor material bits before applying textureinfo.
        flags = row[9] & 0xF82403FF
        for prop in props:
            flags |= SURFACE_PROPERTIES.get(prop, 0)
        if flags & ALPHA_TEST:
            flags &= ~(q2.TRANS33 | q2.TRANS66)  # gl_test=0, ref_gl 1000F025.
        elif bsp['textures'][i]['texture_alpha']:
            flags |= q2.TRANS33  # ref_gl 1000F03D: image alpha enters the alpha chain.
        if bsp['textures'][i]['blank']:
            flags |= q2.NODRAW  # TEX_IsTexBlank, ref_gl 1000EF21..78.
        bsp['texinfo'][i] = (*row[:9], flags)
    bsp.update(lump_sizes=[n for _, n in ranges], info=next((e for e in bsp['entities'] if e.get('classname') == 'worldspawn'), {}))
    return bsp


def face_data(bsp, index):
    face = q2.face_data(bsp, index)
    flags = face['flags']
    # Retail R_BuildLightMap rejects mask 0x2c, not Q2's 0x3c (10006223).
    face['unlit'] = bool(flags & (q2.SKY | q2.WARP | q2.TRANS66 | ALPHA_TEST))
    if face['texture']['texture_alpha']:
        face['alpha'] = 1  # Native image alpha replaces the fixed TRANS33/66 multiplier.
    if flags & FOG:
        face['kind'] = 'hidden'
    return face


def entity_table(files):
    path = 'models/entity.dat'
    result = {}
    if path not in files:
        return result
    for line in anox.read_asset(files[path]).decode('latin1').splitlines():
        fields = [v.strip() for v in line.split('//', 1)[0].split('|')]
        if len(fields) < 24 or not fields[0]:
            continue
        scale = [float(v) for v in fields[2:5]]
        if not all(math.isfinite(v) for v in scale):
            raise ValueError('Non-finite entity.dat scale')
        result[fields[0].lower()] = dict(path=anox.asset_name(fields[1]), scale=scale, type=fields[5].lower(),
            lighting=fields[17], blending=fields[18], spawn_sequence=fields[22], description=fields[23])
    return result


def hidden_reason(entity, definition=None):
    # Runtime APE conditions need a save/script state. Never silently assume true.
    if entity.get('spawncondition', '').strip() not in ('', '1'):
        return 'Conditional spawn requires game/script state: ' + entity['spawncondition']
    if int(number(entity, 'svflags')) & 1 or number(entity, 'hidden'):
        return 'Explicitly hidden at spawn'
    if definition and definition['type'] in ('char', 'charfly', 'playerchar') and int(number(entity, 'spawnflags')) & 2:
        return 'Triggered character: spawnflags 2 (gamex86 1000BEB6)'
    return ''


def instances(bsp):
    # Q2 door/plat/train arithmetic is retained, but no difficulty filtering or
    # player-trigger simulation: Anachronox uses its own spawn-condition system.
    work = dict(bsp, entities=[dict(e) for e in bsp['entities']])
    skipped = []
    for i, e in enumerate(work['entities']):
        if not e.get('model', '').startswith('*'):
            # Point entities are accounted for once, by scenery(). Preserve
            # target/origin fields for the shared train path lookup.
            if e.get('classname', '').startswith('func_') or e.get('classname') == 'rotating_light':
                e['classname'] = ''
            continue
        cls, flags = e.get('classname', ''), int(number(e, 'spawnflags'))
        reason = hidden_reason(e)
        if cls in ('func_fog', 'func_particle', 'func_areaportal', 'func_group') or cls.startswith('trigger_'):
            reason = 'Invisible volume/control brush'
        elif cls == 'func_wall' and flags & 1 and not flags & 4:
            reason = 'TRIGGER_SPAWN wall without START_ON'
        if reason:
            skipped.append(dict(entity=i, classname=cls, model=e.pop('model'), reason=reason))
        # Shared Q2 medium-skill bit is not Anachronox filtering. Wall flag 2
        # alone is visible here (native tests only bit 1, 1000AFCF).
        e['spawnflags'] = str(flags & ~512 & (~2 if cls == 'func_wall' and not flags & 1 else -1))
    placed, ignored = q2.instances(work, game='anachronox', open_at_start=False)
    omitted = {item['entity'] for item in skipped}
    return placed, skipped + [item for item in ignored if item['entity'] not in omitted]


def scenery(bsp, table):
    drawn, skipped = [], []
    for index, entity in enumerate(bsp['entities']):
        cls, model = entity.get('classname', ''), entity.get('model', '')
        if cls == 'worldspawn' or model.startswith('*'):
            continue
        entry = table.get(cls.lower())
        row = dict(entity=index, classname=cls)
        reason = hidden_reason(entity, entry)
        path = entry['path'] if entry else anox.asset_name(model)
        if cls.startswith(('trigger_', 'target_', 'info_', 'light', 'path_')):
            reason = 'Control, start, light or trigger entity; no spawn model'
        if cls in ('func_fog', 'func_particle', 'func_areaportal', 'func_group', 'func_timer', 'func_killbox'):
            reason = 'Invisible volume/control entity'
        if not path:
            reason = reason or 'No entity.dat definition or explicit model path'
        if reason:
            skipped.append(dict(row, reason=reason))
            continue
        base, _, requested_profile = path.partition('!')
        if not base.endswith(('.md2', '.mda')):
            skipped.append(dict(row, path=path, reason='Unsupported spawn representation (not MDA/MD2)'))
            continue
        scale = entity.get('scale', '').split()
        scale = [float(v) for v in scale] if scale else [1, 1, 1]
        if len(scale) == 1:
            scale *= 3
        if len(scale) != 3 or not all(math.isfinite(v) for v in scale):
            skipped.append(dict(row, reason='Invalid entity scale'))
            continue
        if entry:
            scale = [(v if v != -1 else 1) * base for v, base in zip(scale, entry['scale'])]
        drawn.append(dict(row, path=base, origin=vector(entity).tolist(), offset=vector(entity, 'offset').tolist(),
                          rgb=vector(entity, 'rgb').tolist() if 'rgb' in entity else None,
                          renderfx=int(number(entity, 'renderfx')), angles=q2.angles(entity).tolist(), scale=scale,
                          requested_profile=requested_profile, profile='DFLT', alpha=number(entity, 'alpha', 1),
                          alphaflags=int(number(entity, 'alphaflags')),
                          definition=entry, state='first frame, default MDA profile; authored spawn transform'))
    return drawn, skipped


def camera_trace(bsp, start, end):
    """Stop a camera point just before the first solid world leaf on a segment."""
    start, end = np.asarray(start, float), np.asarray(end, float)
    if not bsp['nodes'] or not bsp['leaves']:
        return end
    delta = end - start
    stack = [(bsp['models'][0][9], 0., 1.)]
    while stack:
        node, lo, hi = stack.pop()
        if node < 0:
            if bsp['leaves'][-1 - node][0] & 1:
                # One native unit of clearance, including at a solid endpoint.
                return start + delta * max(0., lo - 1 / max(np.linalg.norm(delta), 1))
            continue
        row = bsp['nodes'][node]
        plane = bsp['planes'][row[0]]
        a, b = np.array([start + lo * delta, start + hi * delta]) @ plane[:3] - plane[3]
        side = int(a < 0)
        if (b < 0) == bool(side):
            stack.append((row[1 + side], lo, hi))
        else:
            mid = lo + (hi - lo) * a / (a - b)
            # Visit near first; a clear endpoint beyond a thin wall is unsafe too.
            stack.extend([(row[2 - side], mid, hi), (row[1 + side], lo, mid)])
    return end


def viewpoints(bsp, floors):
    starts = [e for e in bsp['entities'] if e.get('classname') == 'info_player_start']
    # gamex86 1001A832..1001A8B0: unnamed start, otherwise first start.
    chosen = next((e for e in starts if not e.get('targetname')), starts[0] if starts else None)
    reason = 'Direct-map unnamed start, otherwise first info_player_start'
    floor = None
    if chosen:
        start, turn = vector(chosen), q2.angles(chosen)
        # gamex86 1001AA52: mins(-16,-16,0), maxs(16,16,56); origin raised 1.
        floor = hull_floor(bsp, start + [0, 0, 1], mins=(-16, -16, 0), maxs=(16, 16, 56))
        if floor is None:
            floor = floor_below(floors, *start)
        # Effective camkill view measured in Ballotine, Tenements and Rowdys.
        # Native cam_fardist/lift are owner-relative (64/72 before user settings),
        # not the final displacement from the authored start and collision floor.
        base_z = floor if floor is not None else start[2]
        anchor = camera_trace(bsp, [*start[:2], base_z + 1], [*start[:2], base_z + 88])
        yaw = math.radians(turn[1])
        eye = camera_trace(bsp, anchor, anchor - np.array([math.cos(yaw), math.sin(yaw), 0]) * 62)
        turn = np.array([0., turn[1], 0.])
        reason += '; measured AnoxCam view, 62 back and 88 above floor, clipped to solid world'
    else:
        chosen = next((e for e in bsp['entities'] if e.get('classname') in ('info_intermission', 'info_player_intermission', 'info_camera')), None)
        if chosen:
            eye, turn = vector(chosen), q2.angles(chosen)
            reason = 'No player start: first authored camera/intermission'
        else:
            # Pick a real upward-facing polygon rather than the often solid box centre.
            bounds = np.array(bsp['models'][0][:6]).reshape(2, 3)
            eye = floors[0].mean(axis=0) + [0, 0, 64] if len(floors) else bounds.mean(axis=0)
            turn = np.zeros(3)
            reason = 'No start/camera: 64 units above first visible floor triangle, otherwise world bounds centre'
        start = eye.copy()
    return [dict(origin=(eye[[1, 2, 0]] * SCALE).tolist(), native_origin=eye.tolist(), authored_origin=start.tolist(),
                 yaw=-float(turn[1]), pitch=-float(turn[0]), roll=float(turn[2]), floor_z=floor,
                 classname=chosen.get('classname') if chosen else 'fallback_camera', reason=reason)]


def image_name(image, prefix=''):
    return prefix + hashlib.sha256(struct.pack('<II', *image.size) + image.tobytes()).hexdigest()[:24] + '.png'


def model_positions(local, frame_translate, item):
    # Retail MDA path (anoxgfx 1000CE50): scale quantized frame coordinates,
    # keeping the frame translation unless RF_CORRECT_SCALING is requested.
    # Undo Skinner's y/z/x storage before scaling; swap back only for export.
    pivot = np.zeros(3) if item['renderfx'] & 0x4000000 else np.asarray(frame_translate)
    angles = np.asarray(item['angles']) * [1, 1, -1]  # ref_gl 10006B47: negative roll
    return ((local - pivot) * item['scale'] + pivot) @ rotation(angles).T + item['origin'] + np.asarray(item['offset'])


def append_models(data, bsp, files, placements):
    images, cache, failures = {}, {}, []
    points, uvs, uv2 = [data['points']], [data['uvs']], [data['uv2']]
    count = len(data['points'])
    for item in placements:
        try:
            path = item['path']
            if path not in cache:
                if path not in files:
                    raise ValueError('Spawn model is absent from layered files: ' + path)
                mda = anox.read_mda(anox.read_asset(files[path])) if path.endswith('.mda') else None
                base = mda['basemodel'] if mda else path
                if base not in files:
                    raise ValueError('MDA base model is absent: ' + base)
                mesh = anox.read_md2(anox.read_asset(files[base]))
                skins = anox.resolve_skins(base, mesh['skin_names'], files, mda)
                for skin in skins:
                    tex = dict(image=checker(), missing=True, reason=skin['reason'])
                    if skin['source']:
                        try:
                            tex = dict(image=anox.read_texture(skin['source'], files), missing=False)
                        except (ValueError, OSError) as exc:
                            tex = dict(image=checker(), missing=True, reason=str(exc))
                    png = image_name(tex['image'], 'model_')
                    images[png] = tex['image']
                    skin.update(texture=png, missing=tex['missing'], reason=tex.get('reason', ''))
                cache[path] = anox.build_model(mesh, skins), skins, mesh['frame_translate']
            model, skins, frame_translate = cache[path]
            local = np.asarray(model['vertices']).reshape(-1, 3)[:, [2, 0, 1]]
            placed = model_positions(local, frame_translate, item)
            # Table lighting 0/1/2 selects uniform/directional/mixed lighting.
            # Fullbright is RF_FULLBRIGHT (8); rgb explicitly overrides the sample.
            if item['renderfx'] & 8:
                light = np.ones(3)
            elif item['rgb'] is not None:
                light = np.array(item['rgb'])
            else:
                light = light_point(bsp, np.array(item['origin']), data['dark'], face_reader=face_data) if bsp['nodes'] else np.ones(3)
                # Retail R_LightPoint clamps each channel, ref_gl 10005E89..10005ED7.
                # Explicit rgb/fullbright bypass that function and its ambient floor.
                light = np.clip(light, .15, 1.)
            for group in model['groups']:
                material = group['materialIndex']
                png = model['material_textures'][material]
                part = model['indices'][group['start']:group['start'] + group['count']]
                settings = dict(model['material_settings'][material])
                texture_alpha = images[png].getchannel('A').getextrema()[0] < 255
                # The direct-MD2 wrapper generates blendmode normal for alpha skins.
                if path.endswith('.md2') and texture_alpha:
                    settings.setdefault('blend', ['gl_src_alpha', 'gl_one_minus_src_alpha'])
                # Retail entity alphaflags overrides a profile's blend (anoxgfx 1000E60E).
                if item['alphaflags']:
                    settings.update(blend=['gl_src_alpha', 'gl_one' if item['alphaflags'] & 1 else 'gl_one_minus_src_alpha'], depthWrite=False)
                data['groups'].append(dict(texture=png, kind='model', flags=0, size=list(images[png].size), unlit=True,
                    alpha=item['alpha'], start=len(data['indices']), count=len(part), settings=settings, model_light=light.tolist()))
                data['indices'].extend(count + i for i in part)
            points.append(placed)
            uvs.append(np.asarray(model['uvs']).reshape(-1, 2))
            uv2.append(np.zeros((len(local), 2)))
            count += len(local)
            item.update(status='drawn', skins=skins, vertices=len(local), warnings=model['metadata'].get('warnings', []))
        except (ValueError, KeyError, OSError, IndexError, struct.error) as exc:
            item.update(status='not drawn', reason=str(exc))
            failures.append(dict(item))
    data.update(points=np.concatenate(points), uvs=np.concatenate(uvs), uv2=np.concatenate(uv2))
    return images, failures


def skybox(info, files):
    name = info.get('sky') or 'unit1_'
    faces, images, missing = {}, {}, []
    for suffix in ('rt', 'bk', 'lf', 'ft', 'up', 'dn'):
        tex = texture(f'graphics/sky/{name}{suffix}.tga', files)
        png = image_name(tex['image'], 'sky_')
        images[png] = tex['image']
        faces[suffix] = dict(texture=png, source=tex['source'])
        if tex['missing']:
            missing.append(dict(source=tex['name'], reason=tex['reason']))
    return dict(name=name, rotate=number(info, 'skyrotate'), axis=vector(info, 'skyaxis').tolist() if 'skyaxis' in info else [0, 0, 1],
                faces=faces, missing=missing), images


def build_map(raw, name, files, table=None):
    bsp = read_bsp(raw, files)
    placed, skipped = instances(bsp)
    dark = [int(number(e, 'style')) for e in bsp['entities'] if e.get('classname') == 'light' and
            number(e, 'style') >= 32 and int(number(e, 'spawnflags')) & 1]
    data = q2.geometry(bsp, placed, face_reader=face_data, dark_styles=dark)
    for group in data['groups']:
        group['texture_alpha'] = data['textures'][group['texture']]['texture_alpha']
    views = viewpoints(bsp, data['floors'])
    models, omitted = scenery(bsp, entity_table(files) if table is None else table)
    images, failures = append_models(data, bsp, files, models)
    sky, skies = skybox(bsp['info'], files)
    images.update(skies)
    images.update({png: tex['image'] for png, tex in data['textures'].items()})
    points = data['points'][:, [1, 2, 0]] * SCALE
    fog = world_fog(bsp['info'].get('fog', '0'))
    missing = [dict(source=t['name'], reason=t['reason']) for t in bsp['textures'] if t['missing']]
    missing += sky['missing']
    missing += [dict(source=s['named'], reason=s['reason']) for m in models for s in m.get('skins', []) if s['missing']]
    blob = b''.join((points.astype('<f4').tobytes(), data['uvs'].astype('<f4').tobytes(), data['uv2'].astype('<f4').tobytes(),
                     np.asarray(data['indices'], '<u4').tobytes()))
    scene = dict(name=name, title=bsp['info'].get('message', name), game='anachronox', format='anachronox-bsp38',
        vertices=len(points), indices=len(data['indices']), lightmap=list(data['atlas'].size), groups=data['groups'], viewpoints=views,
        bounds=np.array(bsp['models'][0][:6]).reshape(2, 3).tolist(), instances=placed, skipped_entities=skipped,
        models=models, omitted_models=omitted, model_failures=failures, faces=data['audit'], skybox=sky,
        fog=fog, lump_sizes=bsp['lump_sizes'],
        entity_classes=dict(Counter(e.get('classname', '') for e in bsp['entities'])), dark_styles=dark,
        missing=sorted({r['source'] for r in missing}), missing_reasons=missing,
        lighting=dict(renderer='Anachronox ref_gl build 46', gl_modulate=1, gl_lightmap_modulate=1,
                      vid_gamma=1, gl_gamma=1.4, display_gamma=1, normal_style=1, fog_passes=2,
                      multitexture=False, profile='Generic OpenGL without SGIS multitexture'),
        assumptions=['Direct map, no save state or APE execution', 'First MD2 frame and default MDA profile',
                     'Initial texture animation frame; motion frozen'],
        gaps=['Scripted appearances and particles are omitted',
              'Model shading is simplified; local fog volumes and curved surfaces are omitted'])
    return scene, blob, images, data['atlas']


def import_maps(install, output, replace=False, map_filter=None):
    files, rows, counts = anox.read_install(install, maps=True)
    records = [r for r in rows if r['source'].endswith('.bsp')]
    output = Path(output)
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    report = dict(game='anachronox', imported=[], skipped=[], failed={}, bsp_entries=len(records), archive_counts=counts,
                  results=records, entity_classes={}, brush_classes={}, model_classes={})
    table, index = entity_table(files), []
    for row in records:
        if row['status'] != 'pending':
            continue
        name = row['source']
        if not name.startswith('maps/') or map_filter and map_filter.lower() not in name:
            row.update(status='skipped', reason='Outside maps/ or excluded by requested filter')
            report['skipped'].append(name)
            continue
        ident = anox.safe(name.removeprefix('maps/').removesuffix('.bsp'))
        folder = output / 'maps' / ident
        try:
            if not replace and all((folder / f).is_file() for f in ('scene.json', 'geometry.bin', 'lightmap.png')):
                scene = json.loads((folder / 'scene.json').read_text(encoding='utf-8'))
                row.update(status='skipped', reason='Already imported; use --replace to rebuild')
                report['skipped'].append(name)
            else:
                scene, blob, images, atlas = build_map(anox.read_asset(files[name]), ident, files, table)
                for png, image in images.items():
                    target = output / 'textures' / png
                    if replace or not target.is_file():
                        image.save(target)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'geometry.bin').write_bytes(blob)
                atlas.save(folder / 'lightmap.png')
                (folder / 'scene.json').write_text(json.dumps(scene, separators=(',', ':')), encoding='utf-8')
                row['status'] = 'imported'
                report['imported'].append(ident)
            row.update(warnings=scene['missing_reasons'], model_failures=scene['model_failures'])
            for cls, count in scene['entity_classes'].items():
                report['entity_classes'][cls] = report['entity_classes'].get(cls, 0) + count
            for key, items in (('brush_classes', scene['instances'] + scene['skipped_entities']),
                               ('model_classes', scene['models'] + scene['omitted_models'])):
                for item in items:
                    decisions = report[key].setdefault(item['classname'], Counter())
                    decisions[item.get('reason') or item.get('status', 'drawn')] += 1
            index.append(dict(id=ident, name=ident, title=scene['title'], group='Cinematics' if ident.startswith('cine') else 'Anachronox'))
        except (ValueError, OSError, IndexError, KeyError, struct.error) as exc:
            row.update(status='skipped', reason=str(exc))
            report['failed'][name] = str(exc)
    (output / 'index.json').write_text(json.dumps(sorted(index, key=lambda e: e['id']), indent=1), encoding='utf-8')
    (output / 'import-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'anachronox-maps')
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--filter', help='Import only map paths containing this text')
    args = parser.parse_args()
    print(json.dumps(import_maps(args.install, args.output, args.replace, args.filter), indent=1))

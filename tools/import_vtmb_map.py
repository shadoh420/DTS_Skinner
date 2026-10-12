"""Bloodlines BSP17 maps; static start-state preview, no game/Python execution.

python -m tools.import_vtmb_map --install "C:/GOG Games/VtMB" [--filter sm_pier] [--replace]
Uses the Quake-family scene layout and shared free-flight viewer. Retail format
observations and deliberately approximate rendering are recorded in each scene.
"""
import argparse
from collections import Counter
from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
import struct
import zipfile

import numpy as np
from PIL import Image

from tools import vtmb
from tools.import_vtmb import key, resolve_material
from tools.import_quake_map import entities, rotation, vector, SCALE
from tools.import_unreal_map import pack
from tools.local_data import LOCAL_DATA
from tools.texture_workshop import write_json_atomic


LUMPS = {0: 'entities', 1: 'planes', 2: 'texdata', 3: 'vertices', 6: 'texinfo',
         7: 'faces', 8: 'lighting', 12: 'edges', 13: 'surfedges', 14: 'models',
         26: 'dispinfo', 33: 'dispverts', 35: 'game', 40: 'pakfile',
         43: 'texdata_string_data', 44: 'texdata_string_table', 48: 'disptris'}
SKIPPED_LUMPS = {4: 'visibility', 5: 'nodes', 9: 'occlusion', 10: 'leaves', 11: 'faceids',
                 15: 'worldlights', 16: 'leaffaces', 17: 'leafbrushes', 18: 'brushes', 19: 'brushsides',
                 20: 'areas', 21: 'areaportals', 27: 'originalfaces', 28: 'physdisp', 29: 'physcollide',
                 30: 'vertnormals', 31: 'vertnormalindices', 32: 'displightmapalphas',
                 34: 'displightmapsamplepositions', 36: 'leafwaterdata', 37: 'primitives', 38: 'primverts',
                 39: 'primindices', 41: 'clipportalverts', 42: 'cubemaps', 45: 'overlays',
                 46: 'leafmindisttowater', 47: 'face_macrotexture_info'}


def rows(data, fmt):
    if len(data) % struct.calcsize('<' + fmt):
        raise ValueError(f'Incomplete BSP records ({fmt})')
    return list(struct.iter_unpack('<' + fmt, data))


def read_bsp(raw):
    if raw[:8] != b'VBSP\x11\0\0\0':
        raise ValueError('Expected Bloodlines VBSP version 17')
    vtmb.span(raw, 0, 1036)
    lumps, audit = [], []
    for i in range(64):
        offset, size, version, code = vtmb.unpack(raw, '4i', 8 + i * 16)
        vtmb.span(raw, offset, size)
        if size and (offset < 1036 or code):
            raise ValueError(f'Unsupported compressed/overlapping BSP lump {i}')
        lumps.append(raw[offset:offset + size])
        audit.append(dict(id=i, name=LUMPS.get(i, SKIPPED_LUMPS.get(i, 'reserved/unknown')), offset=offset, size=size, version=version,
                          used=i in LUMPS, reason='Read for preview' if i in LUMPS else
                          'Empty' if not size else 'Not needed for static surfaces; collision, visibility, AI, normals or runtime data'))
    result = dict(raw=raw, lumps=lumps, audit=audit, revision=vtmb.unpack(raw, 'i', 1032)[0],
                  entities=entities(lumps[0]), planes=rows(lumps[1], '4fi'),
                  texdata=rows(lumps[2], '3f5i'), points=rows(lumps[3], '3f'),
                  texinfo=rows(lumps[6], '16f2i'), edges=rows(lumps[12], '2H'),
                  surfedges=[r[0] for r in rows(lumps[13], 'i')], models=rows(lumps[14], '9f3i'),
                  dispverts=rows(lumps[33], '5f'), disptris=[r[0] for r in rows(lumps[48], 'H')])
    result['names'] = [vtmb.string(lumps[43], r[0]) for r in rows(lumps[44], 'i')]
    result['faces'] = []
    for (record,) in rows(lumps[7], '104s'):
        plane, side, onnode, first, count, texinfo, disp, fog = vtmb.unpack(record, 'HBBihhhh', 32)
        light, area, u, v, w, h, original, smoothing = vtmb.unpack(record, 'if4i2I', 72)
        result['faces'].append(dict(plane=plane, side=side, first=first, count=count, texinfo=texinfo,
                                    disp=disp, styles=list(record[48:56]), light=light, mins=[u, v], size=[w+1, h+1]))
    result['displacements'] = []
    for (record,) in rows(lumps[26], '176s'):
        start = vtmb.unpack(record, '3f', 0)
        vertex, triangle, power = vtmb.unpack(record, '3i', 12)
        result['displacements'].append(dict(start=start, vertex=vertex, triangle=triangle, power=power,
                                           face=vtmb.unpack(record, 'H', 36)[0]))
    if not result['models']:
        raise ValueError('BSP has no world model')
    for name in ('planes', 'points', 'texdata', 'texinfo', 'models', 'dispverts'):
        if any(not np.isfinite(value) for row in result[name] for value in row):
            raise ValueError('Non-finite BSP ' + name)
    return result


def static_props(bsp):
    """sprp v4: 128-byte names, uint16 leaves, 56-byte placements. Offsets are absolute."""
    game, props, audit = bsp['lumps'][35], [], []
    if not game:
        return props, audit
    count, = vtmb.unpack(game, 'i')
    vtmb.span(game, 4, count, 16)
    for i in range(count):
        ident, flags, version, offset, length = vtmb.unpack(game, '4sHHii', 4 + i*16)
        vtmb.span(bsp['raw'], offset, length)
        name = ident[::-1].decode('ascii', errors='replace')
        receipt = dict(name=name, version=version, flags=flags, size=length, used=False)
        audit.append(receipt)
        if ident != b'prps':
            receipt['reason'] = 'Detail props/unknown game lump not rendered'
            continue
        if version != 4 or flags:
            receipt['reason'] = f'Unsupported sprp version/flags {version}/{flags}'
            continue
        data = bsp['raw'][offset:offset+length]
        n, = vtmb.unpack(data, 'i'); vtmb.span(data, 4, n, 128)
        names = [vtmb.asset_name(data[4+j*128:4+(j+1)*128].split(b'\0')[0].decode('cp1252')) for j in range(n)]
        at = 4 + n*128
        n, = vtmb.unpack(data, 'i', at); at += 4
        leaves = vtmb.unpack(data, f'{n}H', at); at += n*2
        n, = vtmb.unpack(data, 'i', at); at += 4
        vtmb.span(data, at, n, 56)
        if at + n*56 != len(data):
            raise ValueError('Unexpected sprp v4 record size')
        for j in range(n):
            row = vtmb.unpack(data, '6f3H2Bi2f', at+j*56)
            model, first, num = row[6:9]
            if model >= len(names) or first+num > len(leaves):
                raise ValueError('Invalid static prop dictionary/leaf index')
            props.append(dict(classname='prop_static', model=names[model], origin=list(row[:3]), angles=list(row[3:6]),
                              skin=row[11], solid=row[9], flags=row[10], leaves=list(leaves[first:first+num]),
                              fade=list(row[12:14]), lighting_origin=list(vtmb.unpack(data, '3f', at+j*56+44))
                              if row[10] & 2 else None, source=f'sprp:{j}'))
        receipt.update(used=True, models=names, leaf_count=len(leaves), count=n,
                       reason='LOD0; fade/leaf visibility recorded, not applied in free flight')
    return props, audit


@dataclass
class EmbeddedAsset:
    data: bytes
    origin: str

    def read(self):
        return self.data


def map_files(bsp, files):
    """Map-local ZIP assets fill missing virtual paths; explicit loose/pack winners stay authoritative."""
    result, audit = dict(files), []
    if not bsp['lumps'][40]:
        return result, audit
    with zipfile.ZipFile(io.BytesIO(bsp['lumps'][40])) as archive:
        total = 0
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = vtmb.asset_name(info.filename)
            total += info.file_size
            if total > 256*1024*1024:
                raise ValueError('BSP embedded assets exceed 256 MiB')
            used = name not in result and name.startswith('materials/')
            audit.append(dict(name=name, used=used, reason='Map-local material' if used else 'External winner or non-material'))
            if used:
                result[name] = EmbeddedAsset(archive.read(info), 'BSP pakfile')
    return result, audit


def indexed(seq, index, name):
    if not 0 <= index < len(seq):
        raise ValueError(f'Invalid {name} index {index}')
    return seq[index]


def face_geometry(bsp, number):
    face = indexed(bsp['faces'], number, 'face')
    first, count = face['first'], face['count']
    if count < 3 or first < 0 or first+count > len(bsp['surfedges']):
        raise ValueError('Invalid face surfedges')
    corners = np.array([indexed(bsp['points'], indexed(bsp['edges'], abs(e), 'edge')[0 if e >= 0 else 1], 'vertex')
                        for e in bsp['surfedges'][first:first+count]], dtype=float)
    normal = np.array(indexed(bsp['planes'], face['plane'], 'plane')[:3]) * (-1 if face['side'] else 1)
    # BSP edge order is not assumed; orient triangles using the authored plane.
    points = corners
    triangles = [(0, i, i+1) for i in range(1, count-1)]
    base = points
    if face['disp'] >= 0:
        disp = indexed(bsp['displacements'], face['disp'], 'displacement')
        if count != 4 or disp['power'] not in (2, 3, 4) or disp['face'] != number:
            raise ValueError('Unsupported displacement power/face')
        start = np.argmin(np.linalg.norm(corners-np.array(disp['start']), axis=1))
        corners = np.roll(corners, -int(start), axis=0)
        size = (1 << disp['power'])+1
        # Source grid: start, next corner along v, opposite, last along u.
        base = np.array([(1-v)*(1-u)*corners[0] + v*(1-u)*corners[1] + v*u*corners[2] + (1-v)*u*corners[3]
                         for v in np.linspace(0, 1, size) for u in np.linspace(0, 1, size)])
        at = disp['vertex']; n = size*size
        if at < 0 or at+n > len(bsp['dispverts']):
            raise ValueError('Invalid displacement vertex range')
        offsets = np.array(bsp['dispverts'][at:at+n])
        points = base + offsets[:, :3]*offsets[:, 3:4]
        triangles = []
        for y in range(size-1):
            for x in range(size-1):
                a = y*size+x; b, c, d = a+1, a+size, a+size+1
                triangles.extend([(a, c, b), (b, c, d)] if (x+y) % 2 == 0 else [(a, c, d), (a, d, b)])
        tri_at = disp['triangle']
        if bsp['disptris']:
            if tri_at < 0 or tri_at+len(triangles) > len(bsp['disptris']):
                raise ValueError('Invalid displacement triangle range')
            # SURFACE=1; retail samples use 2/6 (walkable/buildable) without bit 1.
            # Tags annotate collision suitability, not visibility; retain all triangles.
    triangles = np.array(triangles, dtype=np.uint32)
    flip = np.einsum('ij,j->i', np.cross(points[triangles[:, 1]]-points[triangles[:, 0]],
                                      points[triangles[:, 2]]-points[triangles[:, 0]]), normal) < 0
    triangles[flip] = triangles[flip][:, [0, 2, 1]]
    info = indexed(bsp['texinfo'], face['texinfo'], 'texinfo')
    tex = indexed(bsp['texdata'], info[17], 'texdata')
    if min(tex[4:6]) <= 0:
        raise ValueError('Invalid BSP texture dimensions')
    uv = (base @ np.array(info[:8]).reshape(2, 4)[:, :3].T + np.array(info[:8]).reshape(2, 4)[:, 3]) / tex[4:6]
    lm = base @ np.array(info[8:16]).reshape(2, 4)[:, :3].T + np.array(info[8:16]).reshape(2, 4)[:, 3] - face['mins']
    return points, uv, lm, triangles, info[16], indexed(bsp['names'], tex[3], 'material name')


def light_pixels(bsp, face, flags, gain=2):
    if face['light'] < 0 or flags & (6 | 1024):
        return np.full((1, 1, 3), 255, np.uint8)
    w, h = face['size']
    if min(w, h) <= 0 or w*h > 4_000_000:
        raise ValueError('Invalid face lightmap extent')
    total = np.zeros((w*h, 3))
    at = face['light']
    # Each style has a base plane and, for SURF_BUMPLIGHT, three directional planes.
    stride = w*h*4*(4 if flags & 2048 else 1)
    for style in face['styles']:
        if style == 255:
            break
        vtmb.span(bsp['lumps'][8], at, stride)
        values = np.frombuffer(bsp['lumps'][8][at:at+w*h*4], np.uint8).reshape(-1, 4)
        if style not in bsp.get('dark_styles', ()):
            total += values[:, :3] * np.exp2(values[:, 3].view(np.int8).astype(float))[:, None]
        at += stride
    # Approximate LDR upload; gamma-encoded atlas gives useful filtering and avoids
    # interpolating the RGBExp exponent. Native overbright/display ramp awaits comparison.
    return np.rint(np.clip(total * gain / 255, 0, 1)**(1/2.2)*255).astype(np.uint8).reshape(h, w, 3)


def number(entity, field, default=0):
    value = float(entity.get(field, default))
    if not np.isfinite(value):
        raise ValueError('Non-finite entity ' + field)
    return value


def hidden(entity):
    return number(entity, 'starthidden') != 0 or number(entity, 'renderamt', 255) == 0


def brush_transform(entity, model):
    cls = entity['classname']; origin = vector(entity); angles = vector(entity, 'angles')
    flags = int(number(entity, 'spawnflags'))
    if cls in ('func_door', 'func_movelinear', 'func_button', 'func_water'):
        direction = vector(entity, 'movedir') if 'movedir' in entity else angles
        direction = np.array([0, 0, 1 if direction[0] == -1 else -1]) if direction[0] in (-1, -2) else rotation(direction)[:, 0]
        distance = number(entity, 'movedistance', np.dot(np.abs(direction), np.array(model[3:6])-model[:3])-number(entity, 'lip'))
        fraction = number(entity, 'startposition')
        if cls in ('func_door', 'func_water') and flags & 1:
            fraction = 1
        origin += direction*distance*fraction
        angles = np.zeros(3)  # Sliding door angles encode movement, not brush rotation.
    elif cls == 'func_door_rotating' and flags & 1:
        axis = 2 if flags & 64 else 0 if flags & 128 else 1
        angles[axis] += number(entity, 'distance', 90) * (-1 if flags & 2 else 1)
    return rotation(angles), origin


def section(name):
    prefix = name.split('_', 1)[0].lower()
    return {'sm': 'Santa Monica', 'la': 'Downtown', 'hw': 'Hollywood', 'ch': 'Chinatown',
            'sp': 'Special areas'}.get(prefix, 'Misc / test')


def lighting_profile(root):
    settings, sources = {}, []
    for folder in ('Vampire', 'Unofficial_Patch'):
        for name in ('config.cfg', 'autoexec.cfg', 'user.cfg'):
            path = root / folder / 'cfg' / name
            if not path.is_file():
                continue
            found = {}
            for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
                match = re.fullmatch(r'\s*(mat_\w+|cl_v_gamma|gamma|brightness)\s+"?([-+.\d]+)"?\s*(?://.*)?', line)
                if match:
                    value = float(match[2])
                    if np.isfinite(value):
                        found[match[1]] = value
            settings.update(found); sources.append(dict(file=str(path), values=found))
    return dict(settings=settings, sources=sources, light_gain=max(.1, min(8, settings.get('mat_overbright', 2))),
                brightness=max(.1, min(4, settings.get('brightness', 1))),
                display_power=1/max(.5, min(3, settings.get('cl_v_gamma', 1))),
                approximation='LDR RGBExp32 /255, overbright gain, gamma 2.2 lightmap upload; brightness multiplier and cl_v_gamma display power. No native renderer calibration.')


def build_map(raw, name, files, lighting=None):
    bsp = read_bsp(raw)
    files, embedded = map_files(bsp, files)
    lighting = lighting or dict(light_gain=2, brightness=1, display_power=1)
    ents = [{k.lower(): v for k, v in e.items()} for e in bsp['entities']]
    world = next((e for e in ents if e.get('classname') == 'worldspawn'), {})
    bsp['dark_styles'] = [int(number(e, 'style')) for e in ents if e.get('classname', '').startswith('light')
                          and number(e, 'style') >= 32 and int(number(e, 'spawnflags')) & 1]
    images = {'missing_vtmb.png': Image.new('RGBA', (2, 2), (255, 0, 255, 255))}
    materials, missing = {}, []

    def material(name, directories=()):
        cache = name, tuple(directories)
        if cache in materials:
            return materials[cache]
        record = dict(name=name, texture='missing_vtmb.png', settings={}, notes=[])
        materials[cache] = record
        try:
            vmt, path = resolve_material(files, name, directories)
            settings, notes = vtmb.material_settings(vmt)
            image, info = vtmb.read_texture(files, vmt['params'].get('$basetexture', ''))
            png = key(path) + '.png'; images[png] = image
            notes = [n for n in notes if not n.startswith('$selfillum recorded;')]
            record.update(texture=png, settings=settings, notes=notes, source=path, origin=files[path].origin)
        except (ValueError, OSError, KeyError) as exc:
            record['reason'] = str(exc); missing.append(dict(source=name, reason=str(exc)))
        return record

    points, uvs, lmuv, indices, groups, tiles = [], [], [], [], [], []
    faces_audit, placed, omitted, failures = [], [], [], []

    def append(p, uv, lm, tri, mat, kind, tile, **extra):
        base = len(points); start = len(indices)
        points.extend(p); uvs.extend(uv); lmuv.extend(lm); indices.extend((np.asarray(tri).reshape(-1)+base).tolist())
        groups.append(dict(start=start, count=len(indices)-start, kind=kind, flags=0, **mat, **extra))
        tiles.append((len(points)-len(p), len(points), tile))

    brushes = [(0, dict(classname='worldspawn'), np.eye(3), np.zeros(3))]
    models, game_audit = static_props(bsp)
    for i, e in enumerate(ents[1:], 1):
        cls = e.get('classname', '').lower(); model = e.get('model', '')
        record = dict(entity=i, classname=cls, model=model)
        if cls.startswith('npc_') or 'models/character/' in model.replace('\\', '/').lower():
            omitted.append(dict(**record, reason='NPC / character actor excluded')); continue
        if hidden(e):
            omitted.append(dict(**record, reason='StartHidden or renderamt zero')); continue
        if model.startswith('*') and cls.startswith('func_'):
            index = int(model[1:]); row = indexed(bsp['models'], index, 'brush model')
            if cls in ('func_areaportal', 'func_areaportalwindow', 'func_occluder', 'func_viscluster'):
                omitted.append(dict(**record, reason='Visibility/control brush')); continue
            turn, offset = brush_transform(e, row)
            brushes.append((index, e, turn, offset)); placed.append(record)
        elif cls.startswith(('prop_', 'item_')):
            try:
                source = 'entity model'
                if not model and cls.startswith('item_'):
                    source = 'vdata/items/' + vtmb.asset_name(cls) + '.txt'
                    if source not in files:
                        raise ValueError('Missing pickup definition ' + source)
                    model = vtmb.read_vmt(files[source].read())['params'].get('playermodel', '')
                if not model:
                    raise ValueError('No model or pickup playermodel')
                models.append(dict(**{**record, 'model': vtmb.asset_name(model)}, origin=vector(e).tolist(),
                                   angles=vector(e, 'angles').tolist(), skin=int(number(e, 'skin')), source=source,
                                   sequence=e.get('defaultanim', e.get('demo_sequence', '')), scale=number(e, 'modelscale', 1),
                                   tint=(vector(e, 'rendercolor')/255).tolist() if 'rendercolor' in e else [1, 1, 1],
                                   alpha=number(e, 'renderamt', 255)/255))
            except (ValueError, OSError) as exc:
                failures.append(dict(**record, reason=str(exc)))
        else:
            omitted.append(dict(**record, reason='Non-prop/pickup entity; gameplay, decals, lights or effects'))

    for model, entity, turn, origin in brushes:
        row = bsp['models'][model]; first, count = row[10:12]
        if first < 0 or count < 0 or first+count > len(bsp['faces']):
            raise ValueError('Invalid brush face range')
        for n in range(first, first+count):
            face = bsp['faces'][n]
            p, uv, lm, tri, flags, texture = face_geometry(bsp, n)
            if flags & (128 | 256 | 512):
                faces_audit.append(dict(face=n, model=model, reason='Nodraw/hint/skip')); continue
            tile = light_pixels(bsp, face, flags, lighting['light_gain'])
            if tile.shape[:2] == (1, 1):
                lm[:] = 0
            mat = dict(texture='missing_vtmb.png', settings={}) if flags & 6 else material(texture)
            append(p @ turn.T + origin, uv, lm, tri, mat, 'sky' if flags & 6 else 'surface', tile,
                   face=n, model=model, displacement=face['disp'],
                   tint=(vector(entity, 'rendercolor')/255).tolist() if 'rendercolor' in entity else [1, 1, 1],
                   alpha=number(entity, 'renderamt', 255)/255)

    model_cache = {}
    for record in models:
        try:
            model = vtmb.asset_name(record['model'])
            if model.startswith('models/character/'):
                omitted.append(dict(**record, reason='NPC / character actor excluded')); continue
            cache = model, record['skin']
            if cache not in model_cache:
                if model not in files:
                    raise ValueError('Missing MDL ' + model)
                errors = []; mesh = None
                raw_model = files[model].read()
                for companion in vtmb.mdl_companions(raw_model, model, files):
                    try:
                        mesh = vtmb.read_mdl(raw_model, files[companion].read(), record['skin'])
                        mesh['metadata']['vtx'] = companion
                        break
                    except (ValueError, OSError) as exc:
                        errors.append(str(exc))
                if mesh is None:
                    raise ValueError('; '.join(errors) or 'Missing VTX companion')
                model_cache[cache] = mesh
            mesh = model_cache[cache]
            p = np.asarray(mesh['vertices']).reshape(-1, 3)[:, [2, 0, 1]] * record.get('scale', 1)
            p = p @ rotation(record['angles']).T + np.array(record['origin'])
            if not np.isfinite(p).all():
                raise ValueError('Non-finite prop position')
            uv = np.asarray(mesh['uvs']).reshape(-1, 2)
            for g in mesh['groups']:
                mat = material(mesh['material_names'][g['materialIndex']], mesh['metadata']['material_directories'])
                tri = mesh['indices'][g['start']:g['start']+g['count']]
                # Keep only this material's referenced vertices; avoid repeating entire MDLs per group.
                unique, remap = np.unique(tri, return_inverse=True)
                append(p[unique], uv[unique], np.zeros((len(unique), 2)), remap, mat, 'model', np.full((1, 1, 3), 255, np.uint8),
                       model=model, tint=record.get('tint', [1, 1, 1]), alpha=record.get('alpha', 1))
            record.update(status='drawn', pose=mesh['metadata'])
        except (ValueError, OSError, KeyError, IndexError, struct.error) as exc:
            record.update(status='failed', reason=str(exc)); failures.append(dict(record))

    width, height, positions = pack([(t.shape[1], t.shape[0]) for _, _, t in tiles])
    atlas = np.full((max(1, height), width, 3), 255, np.uint8)
    lmuv = np.array(lmuv).reshape(-1, 2)
    for (begin, end, tile), (x, y) in zip(tiles, positions):
        h, w = tile.shape[:2]
        atlas[y:y+h+2, x:x+w+2] = np.pad(tile, ((1, 1), (1, 1), (0, 0)), mode='edge')
        lmuv[begin:end] = (lmuv[begin:end] + [x+1.5, y+1.5]) / [width, max(1, height)]
    sky = dict(name=world.get('skyname', ''), rotate=0, axis=[0, 0, 1], faces={})
    for suffix in ('rt', 'lf', 'bk', 'ft', 'up', 'dn'):
        sky['faces'][suffix] = material('skybox/' + sky['name'] + suffix)
    views = []
    for e in ents:
        if e.get('classname', '').startswith(('info_player_', 'info_landmark')):
            origin = vector(e) + [0, 0, 64]; angles = vector(e, 'angles')
            views.append(dict(origin=(origin[[1, 2, 0]]*SCALE).tolist(), yaw=-float(angles[1]), pitch=-float(angles[0]), roll=float(angles[2])))
    if not views:
        origin = (np.array(bsp['models'][0][:3])+bsp['models'][0][3:6])/2
        views = [dict(origin=(origin[[1, 2, 0]]*SCALE).tolist(), yaw=0, pitch=0)]
    # Atlas coordinates are per vertex: compatible faces/props can share a draw
    # call without changing their lightmaps or positions. Large hubs have thousands
    # of faces but only hundreds of materials.
    batches = {}
    for group in groups:
        signature = (group['texture'], group['kind'], group.get('alpha', 1), tuple(group.get('tint', [1, 1, 1])),
                     json.dumps(group['settings'], sort_keys=True))
        if signature not in batches:
            batches[signature] = (dict((k, v) for k, v in group.items() if k not in ('face', 'model', 'displacement')), [])
        batches[signature][1].extend(indices[group['start']:group['start']+group['count']])
    drawn_faces = sum('face' in g for g in groups)
    drawn_displacements = sum(g.get('displacement', -1) >= 0 for g in groups)
    groups, indices = [], []
    for group, triangles in batches.values():
        group.update(start=len(indices), count=len(triangles))
        indices.extend(triangles); groups.append(group)
    points = np.array(points).reshape(-1, 3)[:, [1, 2, 0]]*SCALE
    blob = b''.join((points.astype('<f4').tobytes(), np.asarray(uvs, '<f4').tobytes(), lmuv.astype('<f4').tobytes(),
                     np.array(indices, '<u4').tobytes()))
    gaps = ['No NPCs, gameplay/Python, particles, decals, fog, dynamic lights or 3D sky camera',
            'Props use model idle/reference frame zero and default bodygroups, with even lighting; entity animation requests are recorded',
            'Materials use their base texture; displacement texture blending, bump/detail/environment and water reflection effects are not evaluated',
            'Brightness is provisional until compared with the game; light styles are held at initial full intensity except start-off lights']
    gaps += [r['reason'] for r in game_audit if not r['used'] and r['size']]
    scene = dict(name=name, title=world.get('message', name), game='vtmb', format='bloodlines-bsp17', revision=bsp['revision'],
                 vertices=len(points), indices=len(indices), lightmap=[width, max(1, height)], groups=groups, viewpoints=views,
                 bounds=np.array(bsp['models'][0][:6]).reshape(2, 3).tolist(), instances=placed, models=models,
                 omitted_models=omitted, model_failures=failures, faces_omitted=faces_audit, skybox=sky,
                 faces_drawn=drawn_faces, displacements_drawn=drawn_displacements,
                 lump_audit=bsp['audit'], game_lumps=game_audit, embedded_assets=embedded, lighting=lighting,
                 entity_classes=dict(Counter(e.get('classname', '') for e in ents)),
                 skipped_by_reason=dict(Counter(e['reason'] for e in omitted)),
                 materials=list(materials.values()), missing=sorted({r['source'] for r in missing}), missing_reasons=missing, gaps=gaps)
    return scene, blob, images, Image.fromarray(atlas)


def import_maps(install, output, replace=False, map_filter=None):
    root, output = Path(install).expanduser().resolve(), Path(output).resolve()
    if root.name.lower() in ('vampire', 'unofficial_patch'):
        root = root.parent
    if output == root or root in output.parents:
        raise ValueError('Import output must be outside the read-only game install')
    files, inventory = vtmb.read_install(root)
    # Reuse the stock archive index, layering only the item definitions needed here.
    for folder in ('Vampire', 'Unofficial_Patch'):
        for path in sorted((root/folder/'vdata'/'items').glob('*.txt')):
            files[vtmb.asset_name('vdata/items/'+path.name)] = vtmb.Asset(path, 0, path.stat().st_size, folder+'/loose')
    maps = sorted(n for n in files if n.startswith('maps/') and n.endswith('.bsp'))
    lighting = lighting_profile(root)
    (output/'textures').mkdir(parents=True, exist_ok=True)
    index, results = [], []
    report = dict(game='vtmb', imported=[], skipped=[], failed={}, inventory=inventory, results=results,
                  effective_maps=len(maps), sections=dict(Counter(section(Path(n).stem) for n in maps)), lighting=lighting)
    for path in maps:
        name = path[5:-4]; ident = key(name); folder = output/'maps'/ident
        receipt = dict(source=path, origin=files[path].origin); results.append(receipt)
        try:
            exists = all((folder/f).is_file() for f in ('scene.json', 'geometry.bin', 'lightmap.png'))
            excluded = map_filter and map_filter.lower() not in path
            if excluded or exists and not replace:
                receipt.update(status='skipped', reason='Filter' if excluded else 'Already imported')
                report['skipped'].append(path)
                if not exists:
                    continue
                scene = json.loads((folder/'scene.json').read_text(encoding='utf-8'))
            else:
                scene, blob, images, atlas = build_map(files[path].read(), name, files, lighting)
                for png, image in images.items():
                    if replace or not (output/'textures'/png).exists():
                        image.save(output/'textures'/png)
                folder.mkdir(parents=True, exist_ok=True)
                (folder/'geometry.bin').write_bytes(blob); atlas.save(folder/'lightmap.png')
                write_json_atomic(folder/'scene.json', scene)
                receipt['status'] = 'imported'; report['imported'].append(name)
            receipt.update(missing=scene['missing_reasons'], model_failures=scene['model_failures'], skipped=scene['skipped_by_reason'])
            index.append(dict(id=ident, name=name, title=scene['title'], group=section(name)))
        except (ValueError, OSError, KeyError, IndexError, struct.error, zipfile.BadZipFile) as exc:
            receipt.update(status='failed', reason=str(exc)); report['failed'][path] = str(exc)
    write_json_atomic(output/'index.json', index)
    write_json_atomic(output/'import-report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA/'vtmb-maps')
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--filter', help='Import only map paths containing this text')
    args = parser.parse_args()
    print(json.dumps(import_maps(args.install, args.output, args.replace, args.filter), indent=1))

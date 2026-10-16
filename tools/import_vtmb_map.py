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

try:
    from tools import source_effects, source_light, vtmb, vtmb_particles
    from tools.import_vtmb import key, resolve_material, prepare_texture
    from tools.texture_storage import game_texture_dir, write_texture
    from tools.import_quake_map import rotation, vector, SCALE
    from tools.import_unreal_map import pack
    from tools.local_data import LOCAL_DATA
    from tools.texture_workshop import write_json_atomic
except ImportError:
    import source_effects
    import source_light
    import vtmb
    import vtmb_particles
    from import_vtmb import key, resolve_material, prepare_texture
    from texture_storage import game_texture_dir, write_texture
    from import_quake_map import rotation, vector, SCALE
    from import_unreal_map import pack
    from local_data import LOCAL_DATA
    from texture_workshop import write_json_atomic


LUMPS = {0: 'entities', 1: 'planes', 2: 'texdata', 3: 'vertices', 5: 'nodes', 6: 'texinfo', 15: 'worldlights',
         7: 'faces', 8: 'lighting', 10: 'leaves', 12: 'edges', 13: 'surfedges', 14: 'models', 16: 'leaffaces',
         26: 'dispinfo', 33: 'dispverts', 35: 'game', 36: 'leafwaterdata', 40: 'pakfile',
         43: 'texdata_string_data', 44: 'texdata_string_table', 48: 'disptris'}
SKIPPED_LUMPS = {4: 'visibility', 9: 'occlusion', 11: 'faceids',
                 17: 'leafbrushes', 18: 'brushes', 19: 'brushsides',
                 20: 'areas', 21: 'areaportals', 27: 'originalfaces', 28: 'physdisp', 29: 'physcollide',
                 30: 'vertnormals', 31: 'vertnormalindices', 32: 'displightmapalphas',
                 34: 'displightmapsamplepositions', 37: 'primitives', 38: 'primverts',
                 39: 'primindices', 41: 'clipportalverts', 42: 'cubemaps', 45: 'overlays',
                 46: 'leafmindisttowater', 47: 'face_macrotexture_info'}


def entities(data, repairs=None):
    """BSP quoted key/value pairs; Bloodlines outputs contain escaped quotes.

    Keep token kinds distinct so quoted braces/empty strings remain values.
    Duplicate output keys retain the last value, as before; outputs aren't run.
    """
    text = data.rstrip(b'\0').decode('latin1')
    # The patched Giovanni 2b lump has this literal editor typo. Repair only
    # this exact output-line context; retain offsets and reject other bare junk.
    def repair(match):
        if repairs is not None:
            repairs.append(dict(offset=match.end()-3, reason='Removed stray mop after OnUseBegin output'))
        return match[1] + '   '
    text = re.sub(r'(?m)(^"OnUseBegin" ",,,0,-1,OnUseBegin\(\),")mop(?=\r?\n"OnUseEnd" )', repair, text)
    token = re.compile(r'\s+|//[^\r\n\0]*|"(?P<value>(?:\\[^\0]|[^"\\\0])*)"|(?P<brace>[{}])')
    result, entity, key, at = [], None, None, 0
    while at < len(text):
        match = token.match(text, at)
        if match is None:
            raise ValueError(f'Invalid entity token at byte {at}')
        at = match.end()
        value, brace = match.group('value', 'brace')
        if brace == '{':
            if entity is not None:
                raise ValueError('Nested entity block')
            entity = {}
        elif brace == '}':
            if entity is None or key is not None:
                raise ValueError('Incomplete entity')
            result.append(entity); entity = None
        elif value is not None:
            if entity is None:
                raise ValueError('Entity key/value outside block')
            value = re.sub(r'\\(["\\n])', lambda m: '\n' if m[1] == 'n' else m[1], value)
            if key is None:
                key = value
            else:
                entity[key] = value; key = None
    if entity is not None:
        raise ValueError('Unclosed entity block')
    return result


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
    repairs = []
    result = dict(raw=raw, lumps=lumps, audit=audit, revision=vtmb.unpack(raw, 'i', 1032)[0],
                  entities=entities(lumps[0], repairs), entity_repairs=repairs, planes=rows(lumps[1], '4fi'),
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
        names = [vtmb.model_reference(data[4+j*128:4+(j+1)*128].split(b'\0')[0].decode('cp1252')) for j in range(n)]
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


class Tree:
    """World BSP for point queries. dnode_t / dleaf_t (bspflags.h, bspfile.h): leaf area is the low 9 bits of the short at 6,
    firstleafface/numleaffaces at 20, leafWaterDataID at 28 (version 0 keeps these offsets before its ambient cube).
    Bloodlines BSP17 leaves are the same 32 bytes. ok is False when the lumps are absent (synthetic maps)."""
    def __init__(self, lumps, planes, head, leaf_stride=32):
        self.nodes = [r[:3] for r in rows(lumps[5], '3i20x')]
        if len(lumps[10]) % leaf_stride:
            raise ValueError('Incomplete BSP leaves')
        self.leaves = [struct.unpack_from('<i', lumps[10], at) + (struct.unpack_from('<h', lumps[10], at+6)[0] & 0x1FF,)
                       + struct.unpack_from('<2H', lumps[10], at+20) + struct.unpack_from('<h', lumps[10], at+28)
                       for at in range(0, len(lumps[10]), leaf_stride)]
        self.leaffaces = [r[0] for r in rows(lumps[16], 'H')]
        self.water = rows(lumps[36], '2fh2x')  # dleafwaterdata_t: surfaceZ, minZ, surfaceTexInfoID
        self.planes, self.head, self.ok = planes, head, bool(self.nodes and self.leaves)

    def leaf(self, point):
        node, steps = self.head, 0
        while node >= 0:
            steps += 1
            if steps > len(self.nodes):
                raise ValueError('Cyclic BSP nodes')
            plane, front, back = indexed(self.nodes, node, 'node')
            q = indexed(self.planes, plane, 'plane')
            node = front if np.dot(point, q[:3]) >= q[3] else back
        return indexed(self.leaves, -1-node, 'leaf')

    def face_areas(self):
        areas = {}
        for leaf in self.leaves:
            for i in range(leaf[2], leaf[2]+leaf[3]) if not leaf[0] & 1 else ():  # CONTENTS_SOLID leaves have no area
                areas.setdefault(indexed(self.leaffaces, i, 'leaf face'), leaf[1])
        return areas

    def regions(self, water_fog):
        """map.regions / map.region_tree (page axes) for the eye in water: one region per leafwaterdata whose surface
        material has fog. Subtrees that end in one region collapse to it. A page plane is [n1, n2, n0, d*SCALE]."""
        regions, ids = [None], {}
        for i, (_, _, texinfo) in enumerate(self.water):
            fog = water_fog(texinfo) if texinfo >= 0 else None  # No surface texinfo (hw_warrens_2): no water material.
            if fog:
                ids[i] = len(regions); regions.append(fog)
        nodes = []

        def walk(node, depth):
            if node < 0:
                return -1 - ids.get(indexed(self.leaves, -1-node, 'leaf')[4], 0)
            if depth > 4096:
                raise ValueError('BSP too deep or cyclic')
            plane, front, back = indexed(self.nodes, node, 'node')
            front, back = walk(front, depth+1), walk(back, depth+1)
            if front == back and front < 0:
                return front
            q = indexed(self.planes, plane, 'plane')
            nodes.append([q[1], q[2], q[0], q[3]*SCALE, front, back])
            return len(nodes) - 1
        root = walk(self.head, 0) if self.ok and ids else -1
        return (regions, dict(root=root, nodes=nodes)) if root != -1 else (None, None)


def fog_params(entity, scale=1):
    """Fog keys of env_fog_controller and sky_camera (fogcontroller.cpp, SkyCamera.cpp DATADESC), page units; None when
    fogenable is off (its default). use_angles replaces fogdir by -forward(angles) (CFogController/CSkyCamera::Activate);
    the sky camera's distances are divided by its scale (viewrender.cpp CSkyboxView::Enable3dSkyboxFog).
    Bloodlines puts the same keys on worldspawn. Hypothesis: Troika reads them like env_fog_controller."""
    if not number(entity, 'fogenable'):
        return None
    direction = -rotation(vector(entity, 'angles'))[:, 0] if number(entity, 'use_angles') else vector(entity, 'fogdir')
    length = np.linalg.norm(direction)
    return dict(color=(vector(entity, 'fogcolor')/255).tolist(), color2=(vector(entity, 'fogcolor2')/255).tolist(),
                dir=(direction[[1, 2, 0]]/(length or 1)).tolist(), blend=bool(number(entity, 'fogblend')),
                start=number(entity, 'fogstart')*SCALE/scale, end=number(entity, 'fogend')*SCALE/scale,
                max=number(entity, 'fogmaxdensity', 1))


def water_fog(params):
    """Fog seen from inside a water volume: the fog volume material's $fogcolor/$fogstart/$fogend (viewrender.cpp
    CUnderWaterView::Draw -> SetFogVolumeState; water.cpp sends $fogcolor gamma-to-linear). Hypothesis: the engine side
    (not public) sets linear range fog from them, as noclip's Material_Water reads them. None without those keys."""
    if not all(k in params for k in ('$fogcolor', '$fogstart', '$fogend')):
        return None
    text = str(params['$fogcolor']); values = np.array([atof(v) for v in text.strip('[]{} ').split()])
    if len(values) != 3 or not np.isfinite(values).all():
        raise ValueError('Invalid water $fogcolor')
    if '{' in text:
        values = values/255
    start, end = atof(params['$fogstart']), atof(params['$fogend'])
    if not np.isfinite([start, end]).all():
        raise ValueError('Invalid water fog range')
    return dict(fog=dict(color=np.clip(values, 0, 1).tolist(), start=start*SCALE, end=end*SCALE, max=1))


def sky_camera(tree, entities, start):
    """The current sky_camera is the last spawned (SkyCamera.cpp GetCurrentSkyCamera; CEntityClassList::Insert prepends);
    its area is the one holding its origin (CSkyCamera::Spawn). start: the first viewpoint, page axes.
    Returns (camera, area, reason): no split when the camera shares the start's area (the level would be hidden)."""
    cameras = [e for e in entities if e.get('classname') == 'sky_camera']
    if not cameras or not tree.ok:
        return None, None, 'no sky_camera' if not cameras else 'no BSP tree'
    camera = cameras[-1]; area = tree.leaf(vector(camera))[1]
    if area == tree.leaf(np.asarray(start)[[2, 0, 1]]/SCALE)[1]:
        return None, None, 'sky_camera shares the start area'
    return camera, area, ''


def sky3d(camera):
    scale = number(camera, 'scale', 16) or 16
    return dict(origin=(vector(camera)[[1, 2, 0]]*SCALE).tolist(), scale=scale, fog=fog_params(camera, scale))


def proxy_gap(materials):
    """Proxies the page does not draw, counted by material: TextureScroll on $basetexturetransform is drawn by the page
    and AnimatedTexture on $basetexture by group.anim."""
    found = Counter()
    for m in materials:
        proxies = (m.get('settings') or {}).get('parameters', {}).get('proxies')
        for name, block in (proxies.items() if isinstance(proxies, dict) else ()):
            if name == 'texturescroll' and isinstance(block, dict) and str(block.get('texturescrollvar', '')).lower() == '$basetexturetransform':
                continue
            if name == 'animatedtexture' and m.get('anim'):
                continue
            found[name] += 1
    return 'Material proxies not drawn (materials): ' + ', '.join(f'{k} {v}' for k, v in found.most_common()) if found else ''


def face_geometry(bsp, number):
    face = indexed(bsp['faces'], number, 'face')
    first, count = face['first'], face['count']
    if count < 3 or first < 0 or first+count > len(bsp['surfedges']):
        raise ValueError('Invalid face surfedges')
    corners = np.array([indexed(bsp['points'], indexed(bsp['edges'], abs(e), 'edge')[0 if e >= 0 else 1], 'vertex')
                        for e in bsp['surfedges'][first:first+count]], dtype=float)
    # BSP17 indexes the oriented member of an opposite-plane pair. The side
    # byte repeats that orientation; applying it again reverses negative sides.
    normal = np.array(indexed(bsp['planes'], face['plane'], 'plane')[:3])
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
    total = face_light(bsp, face, flags)
    if total is None:
        return np.full((1, 1, 3), 255, np.uint8)
    return encode_light(total, gain)


def encode_light(total, gain=2):
    """Linear light (lightmap units, 255 a full step) as the atlas stores it."""
    # Approximate LDR upload; gamma-encoded atlas gives useful filtering and avoids
    # interpolating the RGBExp exponent. Native overbright/display ramp awaits comparison.
    return np.rint(np.clip(total * gain / 255, 0, 1)**(1/2.2)*255).astype(np.uint8)


def face_light(bsp, face, flags):
    """A face's lightmap as linear (h, w, 3) colours, its styles summed (start-off styles dark); None without one."""
    if face['light'] < 0 or flags & (6 | 1024):
        return None
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
    return total.reshape(h, w, 3)


def atof(text):
    """C atof, as the engine reads keyvalues: the leading number, else 0 ('1.00`' is 1, a name is 0)."""
    match = re.match(r'\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?', str(text))
    return float(match[0]) if match else 0.


def number(entity, field, default=0):
    value = atof(entity.get(field, default))
    if not np.isfinite(value):
        raise ValueError('Non-finite entity ' + field)
    return value


def hidden(entity):
    return number(entity, 'starthidden') != 0 or number(entity, 'renderamt', 255) == 0


def pickup_model(data):
    # Only the line-oriented playermodel field is needed. Item descriptions
    # have Troika-specific quoting (even doubled backslashes before quotes),
    # so the material KeyValues parser is inappropriate for the whole file.
    fields = re.findall(r'^\s*"playermodel"\s+"([^"\r\n]*)"[^\S\r\n]*(?://[^\r\n]*)?\r?$',
                        data.decode('cp1252'), re.MULTILINE | re.IGNORECASE)
    if len(fields) != 1:
        raise ValueError('Expected one quoted pickup playermodel field')
    return vtmb.model_reference(fields[0])


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


NPC_HELPERS = {'npc_vcamera', 'npc_maker', 'npc_template_maker', 'npc_enemyfinder', 'npc_bullseye'}
DRAWN_EFFECTS = {'move_rope', 'keyframe_rope', 'infodecal', 'env_sprite', 'env_lightglow', 'point_spotlight'}


def decal_faces(bsp):
    """[(face, corners, normal)] of the world faces that take decals: not sky, nodraw, hint/skip, nodecals or displacements."""
    found = []
    first, count = bsp['models'][0][10:12]
    for n in range(first, first + count):
        face = bsp['faces'][n]
        info = indexed(bsp['texinfo'], face['texinfo'], 'texinfo')
        if face['disp'] >= 0 or info[16] & (2 | 4 | 128 | 256 | 512 | source_effects.SURF_NODECALS):
            continue
        p = face_geometry(bsp, n)[0]
        found.append((n, p, np.array(indexed(bsp['planes'], face['plane'], 'plane')[:3], float)))
    return found


def sprite_frames(files, record, images):
    """Every frame of a sprite material's texture (env_sprite animates through them at its framerate)."""
    frames = [record['texture']]
    if record.get('frames', 1) > 1 and record.get('source'):
        vmt, _ = resolve_material(files, record['name'], ())
        stem, ext = record['texture'].rsplit('.', 1)
        for k in range(1, record['frames']):
            images[f'{stem}_f{k}.{ext}'] = vtmb.material_texture(files, vmt, compressed=True, frame=k)[0]
            frames.append(f'{stem}_f{k}.{ext}')
    return frames


def add_effects(bsp, ents, material, append, face_tile, tiles, sampler, images, files, lighting, in_sky):
    """Ropes, infodecals and sprites (tools/source_effects.py) into the map's geometry and sprite batches."""
    counts, batches = dict(ropes=0, decals=0, decal_pieces=0, sprites=0), {}
    gain = lighting['light_gain']
    for name, rope, width, scale in source_effects.ropes(ents):
        mat = material(name)
        size = mat.get('size') or [1, 1]
        positions, uv, tri = source_effects.rope_mesh(rope, width, scale, size[1] / max(size[0], 1))
        # Lit as the engine lights rope segments (C_RopeKeyframe::CalcLightValues: the light at the rope), one colour
        # from its middle, averaged over directions.
        light = encode_light(sampler.light(rope[len(rope) // 2] + [0, 0, 1], source_light.AXES).mean(0, keepdims=True), gain)
        append(positions, uv, np.zeros((len(positions), 2)), tri, mat, 'model', np.full((1, 1, 3), 255, np.uint8),
               color=np.repeat(light, len(positions), 0), **({'sky3d': True} if in_sky(rope[len(rope) // 2]) else {}))
        counts['ropes'] += 1
    decals = [e for e in ents if e.get('classname') == 'infodecal' and not number(e, 'starthidden') and e.get('texture')]
    faces = decal_faces(bsp) if decals else []
    for e in decals:
        mat = material(e['texture'])
        if not mat.get('size') or mat['texture'] == 'missing_vtmb.png':
            continue
        scale = atof(mat['settings'].get('parameters', {}).get('$decalscale', 1)) or 1
        width, height = mat['size'][0] * scale, mat['size'][1] * scale
        for n, piece, st in source_effects.decal_pieces(faces, vector(e), width, height):
            if n not in face_tile:
                continue
            index, sky = face_tile[n]
            info = indexed(bsp['texinfo'], bsp['faces'][n]['texinfo'], 'texinfo')
            lm = piece @ np.array(info[8:16]).reshape(2, 4)[:, :3].T + np.array(info[8:16]).reshape(2, 4)[:, 3] - bsp['faces'][n]['mins']
            if tiles[index][2].shape[:2] == (1, 1):
                lm[:] = 0
            append(piece, st, lm, [(0, k, k+1) for k in range(1, len(piece)-1)], mat, 'surface', None, share=index, decal=True,
                   **({'sky3d': True} if sky else {}))
            counts['decal_pieces'] += 1
        counts['decals'] += 1
    for name, origin, width, scale_or_aspect, rgba, blend, glow, rate in source_effects.sprites(ents):
        mat = material(name)
        if mat['texture'] == 'missing_vtmb.png' or not mat.get('size'):
            continue
        w, h = mat['size']
        full, aspect = (width, scale_or_aspect) if width is not None else (w * scale_or_aspect, h / max(w, 1))
        key_ = (mat['texture'], blend, glow)
        if key_ not in batches:
            frames = sprite_frames(files, mat, images) if width is None and rate > 0 else [mat['texture']]
            batches[key_] = dict(texture=mat['texture'], blend=blend, aspect=round(aspect, 4), items=[],
                                 **({'depth': False, 'occlude': True} if glow else {}),
                                 **({'frames': frames, 'fps': rate} if len(frames) > 1 else {}))
        batches[key_]['items'].append([*(round(float(c), 4) for c in origin[[1, 2, 0]]*SCALE), round(full*SCALE, 4), *(round(float(c), 4) for c in rgba)])
        counts['sprites'] += 1
    def bounds(e):
        model = e.get('model', '')
        if not model.startswith('*') or not model[1:].isdigit() or int(model[1:]) >= len(bsp['models']):
            return None
        box = np.array(bsp['models'][int(model[1:])][:6], float).reshape(2, 3)
        return box + vector(e)
    # The steam's colour: rendercolor x the light there, as the ropes' (encoded as the atlas stores light).
    light = lambda p: encode_light(sampler.light(p + [0, 0, 1], source_light.AXES).mean(0), gain) / 255
    found = source_effects.particles(ents, bounds, light)
    counts['particles'] = len(found)
    # Bloodlines' own particles (tools/vtmb_particles.py): sprites are particles/<name>.tga, drawn ONE, ONE_MINUS_SRC_ALPHA.
    sprites = {}

    def sprite(name):
        if name not in sprites:
            path = vtmb.asset_name(f'particles/{name}.tga')
            sprites[name] = None
            if path in files:
                try:
                    image = Image.open(io.BytesIO(files[path].read())).convert('RGBA')
                except (OSError, ValueError):
                    return None
                png = key(f'particles/{name}') + '.png'
                images[png] = image
                w, h = image.size
                sprites[name] = dict(texture=png, settings=dict(blend=['gl_one', 'gl_one_minus_src_alpha']),
                                     aspect=[.5, .5 * h / w] if w >= h else [.5 * w / h, .5])
        return sprites[name]

    def floor(origin):
        hit = sampler.trace(np.array(origin, float) + [0, 0, 1], np.array(origin, float) - [0, 0, 4096])
        return None if hit is None else origin[2] + 1 - 4097 * hit[0]
    read = lambda name: files[name].read() if name in files else None
    troika, outcomes = vtmb_particles.emitters(ents, read, bounds, floor, lambda name: (sprite(name) or {}).get('aspect'))
    # Emitters in the 3D sky area draw in the viewer's sky pass (batches of their own, flagged sky3d).
    troika = [(name + '#sky' if in_sky(np.array(emitter['origin'])) else name, template, emitter) for name, template, emitter in troika]
    counts.update(troika_particles=len(troika), troika_outcomes=outcomes, troika_in_sky3d=sum(name.endswith('#sky') for name, _, _ in troika))

    def lookup(name):
        if not name.startswith('particles/'):
            return material(name)
        found = sprites[name[len('particles/'):].removesuffix('#sky')]
        return dict(found, sky3d=True) if name.endswith('#sky') else found
    return dict(counts=counts, sprites=list(batches.values()), particles=source_effects.particle_data(found + troika, lookup, 'missing_vtmb.png', SCALE))


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
            png, image, info = prepare_texture(files, vmt, path)
            images[png] = image
            rate = vtmb.animated_texture_rate(vmt['params'])
            if rate and info.get('frames', 1) > 1:  # AnimatedTexture: every VTF frame, swapped by the viewer at the proxy's rate.
                stem, ext = png.rsplit('.', 1); frames = [png] + [f'{stem}_f{k}.{ext}' for k in range(1, info['frames'])]
                for k, frame in enumerate(frames[1:], 1):
                    images[frame] = vtmb.material_texture(files, vmt, compressed=True, frame=k)[0]
                record['anim'] = dict(frames=frames, fps=rate)
            notes = [n for n in notes if not n.startswith('$selfillum recorded;')]
            record.update(texture=png, settings=settings, notes=notes, source=path, origin=files[path].origin,
                          frames=info.get('frames', 1), size=[info['width'], info['height']] if 'width' in info else None)
        except (ValueError, OSError, KeyError) as exc:
            record['reason'] = str(exc); missing.append(dict(source=name, reason=str(exc)))
        return record

    points, uvs, lmuv, colors, indices, groups, tiles = [], [], [], [], [], [], []
    faces_audit, placed, omitted, failures = [], [], [], []

    def append(p, uv, lm, tri, mat, kind, tile, color=None, share=None, **extra):
        """share: the index in `tiles` of a face whose lightmap tile these vertices read (decals)."""
        base = len(points); start = len(indices)
        points.extend(p); uvs.extend(uv); lmuv.extend(lm); indices.extend((np.asarray(tri).reshape(-1)+base).tolist())
        colors.append(np.full((len(p), 3), 255, np.uint8) if color is None else color)
        groups.append(dict(start=start, count=len(indices)-start, kind=kind, flags=0, **mat, **extra))
        (tiles if share is None else shared).append((len(points)-len(p), len(points), tile if share is None else share))

    brushes, shared, movers, face_tile = [(0, dict(classname='worldspawn'), np.eye(3), np.zeros(3), None)], [], [], {}
    models, game_audit = static_props(bsp)
    for i, e in enumerate(ents[1:], 1):
        cls = e.get('classname', '').lower(); model = e.get('model', '')
        record = dict(entity=i, classname=cls, model=model)
        # Camera/maker controls are not characters, even if an editor model is present.
        npc = cls.startswith('npc_') and cls not in NPC_HELPERS
        if cls in NPC_HELPERS or (npc and not model):
            omitted.append(dict(**record, reason='NPC helper/spawner or no placed model')); continue
        if hidden(e):
            omitted.append(dict(**record, reason='StartHidden or renderamt zero')); continue
        if model.startswith('*') and cls.startswith('func_'):
            index = int(model[1:]); row = indexed(bsp['models'], index, 'brush model')
            if cls in ('func_areaportal', 'func_areaportalwindow', 'func_occluder', 'func_viscluster'):
                omitted.append(dict(**record, reason='Visibility/control brush')); continue
            turn, offset = brush_transform(e, row)
            spin = source_effects.rotating(e)  # func_rotating that starts on: a page mover turning about its origin
            if spin:
                movers.append(dict(pivot=(vector(e)[[1, 2, 0]]*SCALE).round(4).tolist(), spin=[*np.round(np.array(spin[:3])[[1, 2, 0]], 6), round(spin[3], 6)]))
            brushes.append((index, e, turn, offset, len(movers)-1 if spin else None)); placed.append(record)
        elif npc or cls.startswith(('prop_', 'item_')):
            try:
                source = 'entity model'
                if not model and cls.startswith('item_'):
                    source = 'vdata/items/' + vtmb.asset_name(cls) + '.txt'
                    if source not in files:
                        raise ValueError('Missing pickup definition ' + source)
                    model = pickup_model(files[source].read())
                if not model:
                    raise ValueError('No model or pickup playermodel')
                models.append(dict(**{**record, 'model': vtmb.model_reference(model)}, npc=npc, origin=vector(e).tolist(),
                                   angles=vector(e, 'angles').tolist(), skin=int(number(e, 'skin')), source=source,
                                   sequence=e.get('defaultanim', e.get('demo_sequence', '')), scale=number(e, 'modelscale', 1),
                                   tint=(vector(e, 'rendercolor')/255).tolist() if 'rendercolor' in e else [1, 1, 1],
                                   alpha=number(e, 'renderamt', 255)/255))
            except (ValueError, OSError) as exc:
                failures.append(dict(**record, reason=str(exc)))
        else:
            omitted.append(dict(**record, reason='Drawn as a rope, decal or sprite' if cls in DRAWN_EFFECTS else
                                'Non-prop/pickup entity; gameplay, particles, lights or sounds'))

    views = []
    for e in ents:
        if e.get('classname', '').startswith(('info_player_', 'info_landmark')):
            origin = vector(e) + [0, 0, 64]; angles = vector(e, 'angles')
            views.append(dict(origin=(origin[[1, 2, 0]]*SCALE).tolist(), yaw=-float(angles[1]), pitch=-float(angles[0]), roll=float(angles[2])))
    if not views:
        origin = (np.array(bsp['models'][0][:3])+bsp['models'][0][3:6])/2
        views = [dict(origin=(origin[[1, 2, 0]]*SCALE).tolist(), yaw=0, pitch=0)]
    tree = Tree(bsp['lumps'], bsp['planes'], bsp['models'][0][9])
    camera, sky_area, sky_reason = sky_camera(tree, ents, views[0]['origin'])
    face_area = tree.face_areas() if camera else {}
    in_sky = lambda point: camera is not None and tree.leaf(point)[1] == sky_area
    for model, entity, turn, origin, mover in brushes:
        row = bsp['models'][model]; first, count = row[10:12]
        if first < 0 or count < 0 or first+count > len(bsp['faces']):
            raise ValueError('Invalid brush face range')
        brush_sky = model and in_sky(origin + turn @ ((np.array(row[:3])+row[3:6])/2))
        for n in range(first, first+count):
            face = bsp['faces'][n]
            p, uv, lm, tri, flags, texture = face_geometry(bsp, n)
            # 3D sky area: world faces by their leaves (leaffaces), else by a point just in front of the face.
            sky = brush_sky if model else camera is not None and (
                face_area[n] == sky_area if n in face_area else in_sky(p.mean(0) + bsp['planes'][face['plane']][:3]))
            if flags & (128 | 256 | 512):
                faces_audit.append(dict(face=n, model=model, reason='Nodraw/hint/skip')); continue
            tile = light_pixels(bsp, face, flags, lighting['light_gain'])
            if tile.shape[:2] == (1, 1):
                lm[:] = 0
            mat = dict(texture='missing_vtmb.png', settings={}) if flags & 6 else material(texture)
            append(p @ turn.T + origin, uv, lm, tri, mat, 'sky' if flags & 6 else 'surface', tile,
                   face=n, model=model, displacement=face['disp'],
                   tint=(vector(entity, 'rendercolor')/255).tolist() if 'rendercolor' in entity else [1, 1, 1],
                   alpha=number(entity, 'renderamt', 255)/255, **({'sky3d': True} if sky else {}), **({'mover': mover} if mover is not None else {}))
            face_tile[n] = len(tiles) - 1, sky

    model_cache = {}
    # Models are lit at their vertices as the engine lights them (tools/source_light.py), stored as vertex colours in
    # the lightmap atlas's encoding, so props and NPCs keep the walls' relative brightness.
    sampler = source_light.Sampler(bsp, lambda face: face_light(bsp, face, indexed(bsp['texinfo'], face['texinfo'], 'texinfo')[16]))
    effects = add_effects(bsp, ents, material, append, face_tile, tiles, sampler, images, files, lighting, in_sky)
    for record in models:
        try:
            model = vtmb.model_reference(record['model'])
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
            normals = np.asarray(mesh['normals']).reshape(-1, 3)[:, [2, 0, 1]] @ rotation(record['angles']).T
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
            centre = (p.min(0) + p.max(0)) / 2
            vertex_light = encode_light(sampler.light(centre, normals), lighting['light_gain'])
            record['lighting_origin'] = centre.round(2).tolist()
            sky = camera is not None and (indexed(tree.leaves, record['leaves'][0], 'leaf')[1] == sky_area if record.get('leaves')
                                          else in_sky(np.array(record['origin'])))
            for g in mesh['groups']:
                mat = material(mesh['material_names'][g['materialIndex']], mesh['metadata']['material_directories'])
                tri = mesh['indices'][g['start']:g['start']+g['count']]
                # Keep only this material's referenced vertices; avoid repeating entire MDLs per group.
                unique, remap = np.unique(tri, return_inverse=True)
                append(p[unique], uv[unique], np.zeros((len(unique), 2)), remap, mat, 'model', np.full((1, 1, 3), 255, np.uint8),
                       color=vertex_light[unique], model=model, tint=record.get('tint', [1, 1, 1]), alpha=record.get('alpha', 1), **({'sky3d': True} if sky else {}),
                       **({k: record[k] for k in ('npc', 'entity', 'classname')} if record.get('npc') else {}))
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
    for begin, end, index in shared:  # decals read their face's tile
        x, y = positions[index]
        lmuv[begin:end] = (lmuv[begin:end] + [x+1.5, y+1.5]) / [width, max(1, height)]
    sky = dict(name=world.get('skyname', ''), rotate=0, axis=[0, 0, 1], faces={})
    for suffix in ('rt', 'lf', 'bk', 'ft', 'up', 'dn'):
        sky['faces'][suffix] = material('skybox/' + sky['name'] + suffix)
    # Atlas coordinates are per vertex: compatible faces/props can share a draw
    # call without changing their lightmaps or positions. Large hubs have thousands
    # of faces but only hundreds of materials.
    batches = {}
    for group in groups:
        signature = (group['texture'], group['kind'], group.get('alpha', 1), tuple(group.get('tint', [1, 1, 1])),
                     json.dumps(group['settings'], sort_keys=True), group.get('entity') if group.get('npc') else None, group.get('sky3d', False),
                     group.get('mover'), group.get('decal', False))
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
    colors = np.concatenate(colors) if colors else np.zeros((0, 3), np.uint8)
    blob = b''.join((points.astype('<f4').tobytes(), np.asarray(uvs, '<f4').tobytes(), lmuv.astype('<f4').tobytes(),
                     np.column_stack([colors, np.full(len(colors), 255, np.uint8)]).astype(np.uint8).tobytes(),
                     np.array(indices, '<u4').tobytes()))
    def water(texinfo):
        name = bsp['names'][indexed(bsp['texdata'], indexed(bsp['texinfo'], texinfo, 'texinfo')[17], 'texdata')[3]]
        return water_fog(material(name)['settings'].get('parameters', {}))
    regions, region_tree = tree.regions(water)
    proxies = proxy_gap(materials.values())
    gaps = ['No gameplay/Python, beams or dynamic lights; ropes hang still as parabolas; decals skip displacements',
            'Particles: env_particle/func_particle from the particles/*.txt rules read in engine.dll (not game-checked): no collisions beyond a floor below each dripping emitter, no refraction sprites, no moving or triggered emitters; see effects.troika_outcomes',
            'Fog is the worldspawn fog at spawn (fogenable); underwater fog only where the water material sets $fogcolor/$fogstart/$fogend',
            *([f'3D sky not drawn: {sky_reason}'] if sky_reason not in ('', 'no sky_camera') else []), *([proxies] if proxies else []),
            'Props and NPCs use model idle/reference frame zero and default bodygroups; lit at their vertices from the worldlights lump and a six-ray ambient cube of the lightmaps around them; entity animation requests are recorded',
            'Materials use their base texture; displacement texture blending, bump/detail/environment and water reflection effects are not evaluated',
            'Brightness is provisional until compared with the game; light styles are held at initial full intensity except start-off lights']
    gaps += [r['reason'] for r in game_audit if not r['used'] and r['size']]
    scene = dict(name=name, title=world.get('message', name), game='vtmb', format='bloodlines-bsp17', revision=bsp['revision'],
                 vertices=len(points), indices=len(indices), colors=True, lightmap=[width, max(1, height)], groups=groups, viewpoints=views,
                 bounds=np.array(bsp['models'][0][:6]).reshape(2, 3).tolist(), instances=placed, models=models,
                 omitted_models=omitted, model_failures=failures, faces_omitted=faces_audit, skybox=sky,
                 faces_drawn=drawn_faces, displacements_drawn=drawn_displacements,
                 lump_audit=bsp['audit'], entity_repairs=bsp['entity_repairs'], game_lumps=game_audit, embedded_assets=embedded, lighting=lighting,
                 entity_classes=dict(Counter(e.get('classname', '') for e in ents)),
                 skipped_by_reason=dict(Counter(e['reason'] for e in omitted)),
                 materials=list(materials.values()), missing=sorted({r['source'] for r in missing}), missing_reasons=missing, gaps=gaps,
                 fog=fog_params(world), sky3d=sky3d(camera) if camera else None, regions=regions, region_tree=region_tree,
                 effects=effects['counts'], **({'sprites': effects['sprites']} if effects['sprites'] else {}),
                 **({'particles': effects['particles']} if effects['particles'] else {}),
                 **({'movers': movers} if movers else {}))
    return scene, blob, images, Image.fromarray(atlas)


def import_maps(install, output, replace=False, map_filter=None):
    root, output = Path(install).expanduser().resolve(), Path(output).resolve()
    if root.name.lower() in ('vampire', 'unofficial_patch'):
        root = root.parent
    if output == root or root in output.parents:
        raise ValueError('Import output must be outside the read-only game install')
    textures = game_texture_dir(output.parent, 'vtmb').resolve()
    if textures == root or root in textures.parents:
        raise ValueError('Shared textures must be outside the read-only game install')
    files, inventory = vtmb.read_install(root)
    # Reuse the stock archive index, layering only the item definitions needed here.
    for folder in ('Vampire', 'Unofficial_Patch'):
        for path in sorted((root/folder/'vdata'/'items').glob('*.txt')):
            files[vtmb.asset_name('vdata/items/'+path.name)] = vtmb.Asset(path, 0, path.stat().st_size, folder+'/loose')
    maps = sorted(n for n in files if n.startswith('maps/') and n.endswith('.bsp'))
    lighting = lighting_profile(root)
    textures.mkdir(parents=True, exist_ok=True)
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
                    write_texture(textures, png, image)
                folder.mkdir(parents=True, exist_ok=True)
                (folder/'geometry.bin').write_bytes(blob); atlas.save(folder/'lightmap.png')
                write_json_atomic(folder/'scene.json', scene)
                receipt['status'] = 'imported'; report['imported'].append(name)
            receipt.update(missing=scene['missing_reasons'], model_failures=scene['model_failures'], skipped=scene['skipped_by_reason'],
                           entity_repairs=scene.get('entity_repairs', []))
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

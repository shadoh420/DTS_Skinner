"""Import Vengeance maps without running the game. Import the tvg models first.

--output is a data root: maps go in tvg-maps, textures in tvg/textures. Repeat
--map NAME for a bounded sample. Geometry remains in the shared model library.
"""
import argparse
from collections import Counter
import hashlib
import gzip
import json
from pathlib import Path
import re
import struct

import numpy as np
from PIL import Image

from tools.import_tvg import Materials, key
from tools.local_data import LOCAL_DATA
from tools.texture_storage import game_texture_dir, write_texture
from tools.texture_workshop import write_json_atomic
from tools.tvg_package import Library, rotation_matrix
from tools.tvg_map import MapPackage, BASIS, SCALE, viewer, transform, viewpoint, is_npc

LAYER_LIMIT = 4
# Program/Bin/Default.ini [Gameplay.MapList], also present in TV_CD_DVD.ini.
STOCK_MP = set('arid cavern detritus emerald fort garudagorge highlands isle junk royalty spiridian surfacetension tropics utopia winterlake'.split())


def campaign_sections(path):
    result, chapter = {}, 'Campaign'
    for line in Path(path).read_text(encoding='latin1').splitlines():
        match = re.match(r'\s*;\s*(\d\d-.+)', line)
        if match:
            chapter = 'Campaign / ' + match[1].strip()
        if line.lstrip().lower().startswith('missions='):
            match = re.search(r'\bmapName="([^"]+)"', line, re.I)
            if match:
                result[match[1].lower()] = chapter
    return result


def section(name, campaign):
    low = name.lower()
    if low in campaign:
        return campaign[low]
    if low.startswith('sp-'):
        return 'Campaign / unassigned'
    if low.startswith('cs-'):
        return 'Cutscenes'
    if low.startswith('mp-'):
        if low[3:] in STOCK_MP:
            return 'Multiplayer / Stock'
        pack = re.match(r'mp-(x2|beml|rev|tis|vm|tvrl)(?:[-_]|\d)', low)
        return 'Multiplayer / Community / ' + (pack[1].upper() if pack else 'Other')
    return 'Utility and prefab'


class Models:
    """Read-only job-1 buffers and looks. Per-map material overrides share buffers."""
    def __init__(self, root, materials):
        self.root, self.materials = Path(root).resolve(), materials
        self.index = json.loads((self.root/'model-index.json').read_text(encoding='utf-8'))
        self.cache = {}

    def get(self, source, cls, aliases=(), skins=()):
        entry = self.index.get(source.lower(), {}).get('by_class', {}).get(cls)
        if not entry or entry['status'] != 'ready':
            raise ValueError('Model index: ' + (entry or {}).get('reason', 'missing '+source+' ['+cls+']'))
        look = entry['looks'][0]
        for alias in aliases:
            found = next((v for v in entry['variants'] if alias.lower() in [a.lower() for a in v['assignments']]), None)
            if found:
                look = found['id']
                break
        if look not in self.cache:
            data = json.loads((self.root/'model_json'/(look+'.json')).read_text(encoding='utf-8'))
            self.cache[look] = data
        data = self.cache[look]
        # Re-resolved here, as Ascend's map import does: a map import picks up material settings (emissive, panners)
        # without re-importing the model library.
        materials = [self.materials.material(m['source']) if m.get('source') else m for m in data['metadata']['materials']]
        for slot, source in enumerate(skins):
            if source and slot < len(materials):
                materials[slot] = self.materials.material(source)
        signature = json.dumps([(m.get('source'), m.get('texture')) for m in materials])
        ident = look + '_' + hashlib.sha256(signature.encode()).hexdigest()[:10]
        groups = [dict(g, **{k: materials[g['materialIndex']][k] for k in ('texture', 'settings')}, kind='model') for g in data['groups']]
        return ident, dict(source=data['metadata']['source'], geometry=data['geometry_buffers'], groups=groups)


def bitmap(package, raw):
    if raw is None:
        return b''
    saved = package.data, package.at, package.limit
    try:
        package.data, package.at, package.limit = raw, 0, len(raw)
        data = package.take(package.count()*4)
        if package.at != package.limit:
            raise ValueError('Terrain bitmap length mismatch')
        return data
    finally:
        package.data, package.at, package.limit = saved


def terrain_matrix(package, raw):
    planes = package.structure(raw)
    return [[package.structure(planes[name]).get(axis, 0) for axis in ('X', 'Y', 'Z', 'W')]
            for name in ('XPlane', 'YPlane', 'ZPlane', 'WPlane')]


def package_range(package, raw, default):
    value = package.structure(raw) if raw else {}
    return [value.get('Min', default[0]), value.get('Max', default[1])]


def tag_attachments(instances, actors, package):
    """Held weapons/jetpacks share a rendered NPC's toggle identity, never vehicles."""
    draws = {r['entity']: r for r in instances}
    for row in instances:
        actor = actors[row['entity']]
        chain, props = actor['ancestry'], actor['props']
        if row['npc'] or any(c in chain for c in ('gameplay.vehicle','gameplay.pickup','gameplay.basedevice')):
            continue
        if not (props.get('bHardAttach') or props.get('AttachmentBone') not in (None,'None','') or
                any(c in chain for c in ('gameplay.weapon','gameplay.jetpack'))):
            continue
        for field in ('Owner','Base'):
            owner = actor['owned'].get(field)
            parent = draws.get(owner[1]) if owner and owner[0].lower() == package.lower() else None
            if parent and parent['npc']:
                row.update(actor=row['entity'], entity=parent['entity'], npc=True)
                break


def terrain(package, ref, props, library, materials, chunks):
    # Called immediately after reading actor properties: native terrain follows.
    native = package.terrain_native()
    if not props.get('TerrainMap'):
        raise ValueError('TerrainInfo has no heightmap')
    source = package.qualified(props['TerrainMap'])
    p, r = library.object(source, {'Texture'})
    texture, mips = p.texture(r)
    w, h, heights = mips[0]
    if texture.get('Format') != 10 or len(heights) != w*h*2 or (w, h) != (native['width'], native['height']):
        raise ValueError('Terrain requires a matching G16 heightmap')
    layers = []
    for k in sorted((k for k in props if k == 'Layers' or k.startswith('Layers[')),
                    key=lambda k: int(re.search(r'\d+', k)[0]) if '[' in k else 0):
        layer = package.structure(props[k])
        if not layer.get('Texture'):
            continue
        mat = materials.material(package.qualified(layer['Texture']))
        alpha = materials.texture(package.qualified(layer['AlphaMap'])) if layer.get('AlphaMap') else None
        layers.append(dict(source=mat['source'], texture=mat['texture'], alpha=alpha['texture'] if alpha else None,
                           matrix=terrain_matrix(package, layer['TerrainMatrix']) if 'TerrainMatrix' in layer else None,
                           scale=[layer.get('UScale', 1), layer.get('VScale', 1)],
                           pan=[layer.get('UPan', 0), layer.get('VPan', 0)],
                           rotation=layer.get('TextureRotation', 0), axis=layer.get('TextureMapAxis', 0)))
    visible = bitmap(package, props.get('QuadVisibilityBitmap'))
    turned = bitmap(package, props.get('EdgeTurnBitmap'))
    for bits in (visible, turned):
        if bits and len(bits)*8 < w*h:
            raise ValueError('Terrain quad bitmap too short')
    offset = sum(len(c) for c in chunks)
    chunks.extend((heights, visible, turned))
    color_name = key(package.qualified(ref)+'_terrain_vertex_light')+'.png'
    colors = np.frombuffer(native.pop('colors'), np.uint8).reshape(h, w, 4)[:, :, [2, 1, 0, 3]]
    write_texture(materials.directory, color_name, Image.fromarray(colors))
    textures = {}
    for field in ('LightmapTexture', 'RenderMacroTexture', 'FinalisedMacroTexture', 'MacroTexture'):
        if props.get(field):
            textures[field] = materials.texture(package.qualified(props[field]))['texture']
    row = dict(entity=ref, width=w, height=h, offset=offset, bytes=len(heights),
               visibility_bytes=len(visible), edge_bytes=len(turned), to_world=native['to_world'],
               layers=layers[:LAYER_LIMIT], omitted_layers=layers[LAYER_LIMIT:],
               vertex_light=color_name, textures=textures, inverted=bool(props.get('Inverted')))
    return row, np.frombuffer(heights, '<u2').reshape(h, w)


def decoration_matrices(package, layer, terrain_row, heights, materials, visibility=b'', offset=0):
    """Deterministic density-map preview. Native procedural RNG is not simulated."""
    if not layer.get('ShowOnTerrain', 1) or not layer.get('StaticMesh') or not layer.get('DensityMap'):
        return np.empty((0, 16), '<f4')
    w, h = terrain_row['width'], terrain_row['height']
    density_row = materials.texture(package.qualified(layer['DensityMap']))
    density = np.asarray(materials.image(density_row).resize((w, h)).getchannel('R'), dtype=float)/255
    def limits(raw, default):
        d = package.structure(raw) if raw else {}
        return d.get('Min', default[0]), d.get('Max', default[1])
    rng = np.random.default_rng(int(layer.get('Seed', 0)) & 0xffffffff)
    lo, hi = limits(layer.get('DensityMultiplier'), (1, 1))
    expected = density[:-1, :-1] * max(0, layer.get('MaxPerQuad', 1)) * rng.uniform(lo, hi, (h-1, w-1))
    counts = np.floor(expected + rng.random(expected.shape)).astype(int)
    if visibility and not layer.get('ShowOnInvisibleTerrain'):
        bits = np.unpackbits(np.frombuffer(visibility, np.uint8), bitorder='little')[:w*h].reshape(h,w)
        counts *= bits[:-1,:-1]
    if np.any(counts < 0) or counts.sum() > 2_000_000:
        raise ValueError('Decoration density outside preview safety bounds')
    yy, xx = np.nonzero(counts)
    yy, xx = np.repeat(yy, counts[yy, xx]), np.repeat(xx, counts[yy, xx])
    count = len(xx)
    x, y = xx+rng.random(count), yy+rng.random(count)
    dx, dy = x-xx, y-yy
    z = heights[yy, xx]*(1-dx)*(1-dy)+heights[yy, xx+1]*dx*(1-dy)+heights[yy+1, xx]*(1-dx)*dy+heights[yy+1, xx+1]*dx*dy
    coords = np.asarray(terrain_row['to_world']).reshape(4, 3)
    world = (np.column_stack((x, y, z))-coords[0]) @ coords[1:].T
    scales = np.ones((count, 3))
    ranges = package.structure(layer['ScaleMultiplier']) if layer.get('ScaleMultiplier') else {}
    for i, axis in enumerate(('X', 'Y', 'Z')):
        scales[:, i] = rng.uniform(*limits(ranges.get(axis), (1, 1)), count)
    scales *= rng.uniform(*limits(layer.get('ScaleMultiplier3D'), (1, 1)), count)[:, None]
    if layer.get('ScaleMap'):
        tex = materials.texture(package.qualified(layer['ScaleMap']))
        scale_map = np.asarray(materials.image(tex).resize((w,h)).getchannel('R'))/255
        scales *= scale_map[yy,xx,None]
    world[:, 2] += offset
    angles = rng.uniform(0, 2*np.pi, count) if layer.get('RandomYaw') else np.zeros(count)
    turn = np.zeros((count, 3, 3))
    turn[:, 0, 0] = turn[:, 1, 1] = np.cos(angles)
    turn[:, 1, 0], turn[:, 0, 1], turn[:, 2, 2] = np.sin(angles), -np.sin(angles), 1
    if layer.get('AlignToTerrain') and count:
        gy, gx = np.gradient(heights.astype(float))
        normal = np.column_stack((-gx[yy, xx]*coords[3, 2]/coords[1, 0], -gy[yy, xx]*coords[3, 2]/coords[2, 1], np.ones(count)))
        normal /= np.linalg.norm(normal, axis=1)[:, None]
        right = np.cross(turn[:, :, 0], normal)
        right /= np.maximum(np.linalg.norm(right, axis=1)[:, None], 1e-8)
        turn = np.stack((np.cross(normal, right), -right, normal), axis=2)
    matrices = np.zeros((count, 4, 4), dtype='<f4')
    matrices[:, :3, :3] = BASIS @ (turn * scales[:, None, :]) @ BASIS.T
    matrices[:, :3, 3] = viewer(world)
    matrices[:, 3, 3] = 1
    return matrices.transpose(0, 2, 1).reshape(count, 16)


def distance_fog(props):
    """A ZoneInfo's or PhysicsVolume's distance fog in page units, or None. Colours are FColor B, G, R, A."""
    if not props.get('bDistanceFog') or props.get('DistanceFogEnd', 8000) <= props.get('DistanceFogStart', 3000):
        return None
    c = props.get('DistanceFogColor', (128, 128, 128, 0))
    fog = dict(color=[c[2]/255, c[1]/255, c[0]/255], start=props.get('DistanceFogStart', 3000)*SCALE,
               end=props.get('DistanceFogEnd', 8000)*SCALE, blend_time=props.get('DistanceFogBlendTime', 0))
    if props.get('DistanceFogType'):  # FG_Exponential (Vengeance's IG_FOG): drawn linear; native SetExpDistanceFog not read.
        fog.update(type='exponential', exp_bias=props.get('DistanceFogExpBias', 1))
    return fog


def zone_regions(world, actors, level_info):
    """Camera fog regions from the BSP: map.regions per zone number, map.region_tree from the nodes.

    The walk is UModel::FindRegion's (SurrealEngine UModel.cpp): front where plane.dot(p) - W >= 0; a missing child
    ends in iZone[1] (front) or iZone[0] (back). Zone numbers index the model's zone table of ZoneInfo actors; an
    empty slot is the LevelInfo (itself a ZoneInfo).
    """
    if not world['nodes'] or not world.get('zones'):
        return None, None
    zone_props = [actors.get(ref, {}).get('props', level_info) if ref else level_info for ref in world['zones']]
    regions = [dict(fog=distance_fog(z)) for z in zone_props]
    leaf = lambda zone: -1 - min(zone, len(regions)-1)
    nodes = []
    for n in world['nodes']:
        normal = BASIS @ np.asarray(n['plane'][:3])
        nodes.append([*np.round(normal, 6).tolist(), round(n['plane'][3]*SCALE, 4), n['front'] if n['front'] >= 0 else leaf(n['zones'][1]),
                      n['back'] if n['back'] >= 0 else leaf(n['zones'][0])])
    return regions, dict(root=0, nodes=nodes)


def volume_brush(p, props):
    """Page-space planes [a, b, c, d] (inside a.x <= d) of a convex volume brush, or None when not convex."""
    brush = p.model(props['Brush'])
    m = np.asarray(transform(props)).reshape(4, 4).T
    planes, corners = [], []
    for poly in p.polys(brush['polys']):
        points = viewer(poly['points']) @ m[:3, :3].T + m[:3, 3]
        normal = np.linalg.inv(m[:3, :3]).T @ viewer(poly['normal'])
        normal /= np.linalg.norm(normal) or 1
        planes.append([*np.round(normal, 6).tolist(), round(float(normal @ points[0]), 4)]); corners.extend(points)
    if len(planes) < 4:
        return None
    a = np.asarray(planes)
    # Every corner on or inside every plane: convex, normals outward (the editor's volume brushes are).
    if np.any(np.asarray(corners) @ a[:, :3].T - a[:, 3] > 1e-3):
        return None
    return planes


def build_map(path, library, models, materials):
    p = MapPackage(path)
    try:
        level_ref = next(i+1 for i, e in enumerate(p.exports) if p.ref_name(e['cls']) == 'Level')
        level = p.level(level_ref)
        actors, omitted, failures, terrains, terrain_chunks = {}, [], [], [], []
        for ref in level['actors']:
            if not ref:
                continue
            cls = p.qualified(p.exports[ref-1]['cls'])
            try:
                defaults, ancestry = library.class_info(cls)
                own = p.properties(ref)
                if 'engine.terraininfo' in ancestry:
                    row, heights = terrain(p, ref, own, library, materials, terrain_chunks)
                    terrains.append((row, heights, p.struct_array(own.get('DecoLayers', b'\0'))))
                owned = dict(defaults, **{k: (p.path.stem, v) for k, v in own.items()})
                actors[ref] = dict(classname=cls, ancestry=ancestry, owned=owned, props={k:v[1] for k,v in owned.items()})
            except (ValueError, OSError, KeyError, struct.error) as exc:
                failures.append(dict(entity=ref, classname=cls, reason=str(exc)))
        world = p.model(level['model']) if level['model'] else dict(nodes=[], surfaces=[], points=[], vectors=[], verts=[])
        points, uv, indices, groups, batches = [], [], [], [], {}
        surface_omissions = Counter()
        def polygon(corners, base, normal, u, v, material, flags, matrix=None):
            if flags & (1 | 0x4000000):  # PF_Invisible / PF_Portal
                surface_omissions['Invisible/portal BSP'] += 1
                return
            if len(corners) < 3:
                return
            source = p.qualified(material)
            mat = materials.material(source)
            tex = materials.textures.get((source or '').lower(), {})
            width, height = tex.get('width', 256), tex.get('height', 256)
            # Shader dimensions come from its resolved diffuse texture.
            for t in materials.textures.values():
                if t.get('texture') == mat['texture']:
                    width, height = t.get('width', width), t.get('height', height)
                    break
            coords = np.asarray(corners, dtype=float)
            texuv = (coords-np.asarray(base)) @ np.asarray((u, v)).T / [width, height]
            positions = viewer(coords)
            if matrix is not None:
                m = np.asarray(matrix).reshape(4, 4).T
                positions = positions @ m[:3,:3].T + m[:3,3]
            desired_normal = viewer(normal)
            if matrix is not None:
                desired_normal = np.linalg.inv(m[:3,:3]).T @ desired_normal
            reverse = np.dot(np.cross(positions, np.roll(positions, -1, axis=0)).sum(axis=0), desired_normal) < 0
            first = len(points)
            points.extend(positions); uv.extend(texuv)
            settings = dict(mat['settings'])
            if flags & 0x100:
                settings['cull'] = 'none'
            if flags & 2:
                settings['alphaTest'] = .5
            if flags & 4:
                settings['blend'] = ['gl_one','gl_one_minus_src_color']
            elif flags & 0x40:
                settings['blend'] = ['gl_dst_color','gl_src_color']
            if flags & 0x400000:
                settings['unlit'] = True
            state = json.dumps([mat['texture'], settings, bool(flags & 0x80)],sort_keys=True)
            if state not in batches:
                batches[state] = (dict(texture=mat['texture'], settings=settings, kind='sky' if flags & 0x80 else 'surface'), [])
            batches[state][1].extend(first+i for j in range(1, len(corners)-1) for i in ((0,j+1,j) if reverse else (0,j,j+1)))
        for node in world['nodes']:
            s = world['surfaces'][node['surface']]
            corner = [world['points'][world['verts'][i][0]] for i in range(node['pool'], node['pool']+node['count'])]
            polygon(corner, world['points'][s['base']], world['vectors'][s['normal']], world['vectors'][s['u']], world['vectors'][s['v']], s['material'], s['flags'])
        model_library, instances, lighting, decorations, deco_chunks = {}, [], [], [], []
        views, sky, zones = [], None, []
        def reference(owned, field):
            value = owned.get(field)
            return library.value_ref(value) if value and value[1] else None
        for ref, actor in actors.items():
            props, owned, ancestry, cls = actor['props'], actor['owned'], actor['ancestry'], actor['classname']
            short = cls.rsplit('.', 1)[-1]
            record = dict(entity=ref, classname=cls, name=p.ref_name(ref))
            if 'engine.playerstart' in ancestry:
                views.append(viewpoint(props))
            if 'engine.zoneinfo' in ancestry:
                zones.append(dict(record, **{k:v for k,v in props.items() if k in ('AmbientHue','AmbientSaturation','AmbientBrightness','bDistanceFog','DistanceFogColor','DistanceFogStart','DistanceFogEnd','DistanceFogBlendTime','DistanceFogType','DistanceFogExpBias')}))
            if 'engine.skyzoneinfo' in ancestry:
                rot = props.get('Rotation', (0,0,0))
                sky = dict(origin=viewer(props.get('Location', (0,0,0))).tolist(), rotation=(BASIS@rotation_matrix(*rot)@BASIS.T).reshape(-1).tolist(), rotator=rot)
            reason = None
            if props.get('bHidden') or 'talkinghead' in short.lower():
                reason = 'Hidden/control or talking-head actor'
            elif props.get('DrawType') == 0:
                reason = 'Non-rendering DrawType'
            elif any(x in ancestry for x in ('engine.emitter','engine.projector')) or any(s in short.lower() for s in ('emitter','projector','igeffect')):
                reason = 'Emitter/projector/effect omitted'
            elif 'engine.terraininfo' in ancestry:
                continue
            if reason:
                omitted.append(dict(record, reason=reason)); continue
            mesh = reference(owned, 'StaticMesh') if props.get('DrawType') == 8 else reference(owned, 'Mesh')
            mesh_cls = 'StaticMesh' if props.get('DrawType') == 8 else 'SkeletalMesh'
            if not mesh:
                mesh = reference(owned, 'StaticMesh') or reference(owned, 'Mesh')
                mesh_cls = 'StaticMesh' if reference(owned, 'StaticMesh') else 'SkeletalMesh'
            if mesh:
                try:
                    skins = library.value_refs(owned['Skins']) if 'Skins' in owned else []
                    aliases = [cls]
                    team = actors.get(props.get('m_team'))
                    if team:
                        aliases += [team['classname']+'.'+field for field in ('tankSkin','buggySkin','podSkin','assaultShipSkin')]
                    look, model = models.get(mesh, mesh_cls, aliases, skins)
                    model_library[look] = model
                    row = dict(record, look=look, matrix=transform(props), npc=is_npc(ancestry))
                    if props.get('StaticMeshInstance'):
                        try:
                            colors = p.vertex_colors(props['StaticMeshInstance'])
                            if len(colors)//4 != model['geometry']['arrays']['vertices']['count']//3:
                                raise ValueError('Placement colors do not match LOD0 vertex count')
                            row['colors'] = dict(offset=sum(len(c) for c in lighting), count=len(colors)//4)
                            lighting.append(colors)
                        except (ValueError, IndexError) as exc:
                            row['lighting_fallback'] = str(exc)
                    instances.append(row)
                except (ValueError, OSError, KeyError) as exc:
                    failures.append(dict(record, source=mesh, reason=str(exc)))
                continue
            if props.get('Brush') and 'engine.mover' in ancestry:
                try:
                    brush = p.model(props['Brush'])
                    for poly in p.polys(brush['polys']):
                        polygon(poly['points'],poly['base'],poly['normal'],poly['u'],poly['v'],poly['material'],poly['flags'],transform(props))
                except (ValueError, IndexError) as exc:
                    failures.append(dict(record, reason='Brush start pose: '+str(exc)))
            else:
                omitted.append(dict(record, reason='Compiled BSP brush (already in UModel)' if props.get('Brush') else 'No drawable mesh (control, light, trigger or runtime spawn)'))
        tag_attachments(instances, actors, p.path.stem)
        level_info = next((a['props'] for a in actors.values() if 'engine.levelinfo' in a['ancestry']), {})
        regions, region_tree = zone_regions(world, actors, level_info)
        regions, region_brushes, volume_skips = regions or [], [], Counter()
        # PhysicsVolume distance fog overrides the zone's while the camera is inside (the water tint: negative starts).
        for ref, actor in actors.items():
            fog = distance_fog(actor['props']) if 'engine.physicsvolume' in actor['ancestry'] else None
            if not fog or not actor['props'].get('Brush'):
                continue
            try:
                planes = volume_brush(p, actor['props'])
            except (ValueError, IndexError) as exc:
                volume_skips['unreadable brush: '+str(exc)] += 1; continue
            if planes is None:
                volume_skips['non-convex brush'] += 1; continue
            region_brushes.append(dict(planes=planes, region=len(regions), entity=ref)); regions.append(dict(fog=fog, volume=ref))
        for row, heights, layers in terrains:
            for number, layer in enumerate(layers):
                source = p.qualified(layer.get('StaticMesh', 0))
                if not source:
                    continue
                try:
                    look, model = models.get(source, 'StaticMesh')
                    terrain_props = actors[row['entity']]['props']
                    matrices = decoration_matrices(p, layer, row, heights, materials,
                        bitmap(p, terrain_props.get('QuadVisibilityBitmap')), terrain_props.get('DecoLayerOffset',0))
                    if len(matrices):
                        model_library[look] = model
                        # Spatial batches let Three cull distant/off-screen grass as a group.
                        cells = np.floor(matrices[:, [12,14]]/64).astype(int)
                        order = np.lexsort((cells[:,1], cells[:,0]))
                        matrices, cells = matrices[order], cells[order]
                        cuts = np.r_[0, np.flatnonzero(np.any(cells[1:] != cells[:-1],axis=1))+1, len(cells)]
                        for start, end in zip(cuts[:-1],cuts[1:]):
                            decorations.append(dict(entity=row['entity'], layer=number, look=look,
                                offset=sum(len(c) for c in deco_chunks), count=int(end-start), npc=False,
                                fade=package_range(p, layer.get('FadeoutRadius'), (0,0))))
                            deco_chunks.append(matrices[start:end].tobytes())
                except (ValueError, OSError, KeyError) as exc:
                    failures.append(dict(entity=row['entity'], layer=number, source=source, reason=str(exc)))
        for group, ix in batches.values():
            groups.append(dict(group, start=len(indices), count=len(ix))); indices.extend(ix)
        vertices = np.asarray(points, '<f4').reshape(-1, 3)
        blob = b''.join((vertices.tobytes(), np.asarray(uv, '<f4').tobytes(), np.asarray(indices, '<u4').tobytes()))
        if not views:
            origin = vertices.mean(axis=0).tolist() if len(vertices) else (instances[0]['matrix'][12:15] if instances else [0,5,0])
            views = [dict(origin=origin, yaw=0, pitch=0)]
        scene = dict(game='tvg', name=path.stem, title=level_info.get('Title', ''), format='vengeance-130/27',
                     vertices=len(vertices), indices=len(indices), groups=groups, model_library=model_library,
                     instances=instances, decorations=decorations, terrain=[r for r,_,_ in terrains], viewpoints=views,
                     sky=sky, zones=zones, regions=regions, region_tree=region_tree, region_brushes=region_brushes,
                     region_skips=dict(volume_skips), actors=len(actors), actor_slots=len(level['actors']), npcs=len({r['entity'] for r in instances if r['npc']}),
                     model_failures=failures, missing=sorted({r.get('source',r.get('classname','unknown')) for r in failures}),
                     omitted=omitted, surface_omissions=dict(surface_omissions),
                     lighting=dict(display_power=1, source='Stored placement vertex colors; terrain light texture; fixed ambient + sun for BSP/unbaked props'),
                     gaps=['Lighting parity deferred; BSP lightmaps, normal/specular shading and dynamic lights are not decoded',
                           'Terrain blends at most four authored layers; remaining layers are listed; macro texture is the base fallback',
                           'Decoration density/scale/seed maps create a deterministic approximation; native RNG, animation and distance fading are not simulated',
                           'Actors use inherited serialized start state and model-library reference poses; no scripts, Mojo, spawners, particles or projectors',
                           'Distance fog follows the camera BSP zone and fog volumes; exponential zone fog is drawn linear; no BSP zone/PVS clipping'])
        return scene, dict(geometry=blob, terrain=b''.join(terrain_chunks), lighting=b''.join(lighting), decorations=b''.join(deco_chunks))
    finally:
        p.close()


def import_maps(install, output=LOCAL_DATA, replace=False, map_names=None, model_root=None):
    install, output = Path(install).resolve(), Path(output).resolve()
    if output == install or install in output.parents:
        raise ValueError('Output must be outside the read-only install')
    root = output/'tvg-maps'
    library = Library(install)
    try:
        materials = Materials(library, game_texture_dir(output, 'tvg'))
        models = Models(model_root or output/'tvg', materials)
        campaign = campaign_sections(install/'Content/System/Campaign.ini')
        maps = sorted(p for p in library.files.values() if p.suffix.lower() == '.tvm')
        selected = {n.lower().removesuffix('.tvm') for n in map_names} if map_names else None
        if selected and selected - {p.stem.lower() for p in maps}:
            raise ValueError('Unknown map selection: '+str(selected - {p.stem.lower() for p in maps}))
        report = dict(game='tvg', installed_maps=len(maps),
                      sections=dict(Counter(section(p.stem,campaign) for p in maps)),
                      imported=[], skipped=[], failed={}, results=[])
        index = []
        for path in maps:
            ident = path.stem.lower()
            if selected is not None and ident not in selected:
                continue
            entry = dict(id=ident, name=path.stem, group=section(path.stem, campaign))
            folder = root/'maps'/ident
            try:
                if not replace and (folder/'scene.json').is_file():
                    scene = json.loads((folder/'scene.json').read_text(encoding='utf-8'))
                    report['skipped'].append(path.stem)
                else:
                    scene, blobs = build_map(path, library, models, materials)
                    folder.mkdir(parents=True, exist_ok=True)
                    for name, blob in blobs.items():
                        if name == 'decorations':
                            (folder/'decorations.bin.gz').write_bytes(gzip.compress(blob, compresslevel=6, mtime=0))
                        else:
                            (folder/(name+'.bin')).write_bytes(blob)
                    write_json_atomic(folder/'scene.json', scene)
                    report['imported'].append(path.stem)
                drawable = bool(scene['indices'] or scene['instances'] or scene['terrain'] or scene['decorations'])
                entry.update(status='ready' if drawable else 'skipped', title=scene['title'])
                if not drawable:
                    entry['reason'] = 'No drawable BSP, terrain or model instances'
                report['results'].append(dict(name=path.stem, actors=scene['actors'], instances=len(scene['instances']),
                    decorations=sum(d['count'] for d in scene['decorations']), npcs=scene['npcs'], missing=len(scene['model_failures']),
                    terrain_bytes=sum(t['bytes']+t['visibility_bytes']+t['edge_bytes'] for t in scene['terrain']),
                    omitted=dict(Counter(o['reason'] for o in scene['omitted'])),
                    sizes={f.name:f.stat().st_size for f in folder.iterdir() if f.is_file()}))
            except (ValueError, OSError, KeyError, IndexError, StopIteration, struct.error) as exc:
                report['failed'][path.stem] = str(exc)
                entry.update(status='failed', reason=str(exc))
            index.append(entry)
        if (root/'index.json').is_file():
            present = {r['id'] for r in index}
            index.extend(r for r in json.loads((root/'index.json').read_text()) if r['id'] not in present)
        # Keep shader references and explicit DXT5N encodings for map-only assets,
        # just as the model library does; the viewer does not render normal/spec maps.
        previous = json.loads((root/'import-report.json').read_text(encoding='utf-8')) if (root/'import-report.json').is_file() else {}
        for field, current in (('material_results',materials.materials),('texture_results',materials.textures)):
            retained = {r['source'].lower():r for r in previous.get(field,[])}
            retained.update(current)
            report[field] = list(retained.values())
        report['unresolved_materials'] = [r for r in report['material_results'] if r['status'] != 'ready']
        report['unresolved_textures'] = [r for r in report['texture_results'] if r['status'] != 'ready']
        write_json_atomic(root/'index.json', sorted(index, key=lambda r:(r['group'],r['name'])))
        write_json_atomic(root/'model-library.json', dict(root=str(models.root)))
        write_json_atomic(root/'import-report.json', report)
        return report
    finally:
        library.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA)
    parser.add_argument('--models', type=Path)
    parser.add_argument('--map', action='append')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    result = import_maps(args.install, args.output, args.replace, args.map, args.models)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result['failed']))

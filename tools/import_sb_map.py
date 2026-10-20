"""Spellbreak Maps: world-composition tiles, stitched landscape and instanced meshes.

python -m tools.import_sb_map --install C:\\Games\\Spellbreak\\client --output build/sb-data --models build/sb-data/sb
Repeat --map NAME for a small sample. Placed meshes reference the models import
(model-index.json); meshes it lacks go to the map output's map-model-index.json.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import PurePosixPath
import re
import struct

import numpy as np
from PIL import Image

from tools import import_tas_map as ue3
from tools.import_tas import key
from tools.local_data import LOCAL_DATA
from tools.sb_materials import Materials, texture_mips
from tools.texture_storage import write_texture
from tools.tvg_map import BASIS, viewer
from tools.tvg_package import rotation_matrix
from tools.ue4_mesh import mesh as ue4_mesh
from tools.ue4_package import Library, Reader

INSTANCED = ('InstancedStaticMeshComponent', 'HierarchicalInstancedStaticMeshComponent', 'FoliageInstancedStaticMeshComponent')
SKIPPED_COMPONENTS = {'XInstancedSplineMeshComponent': 'Spline-deformed instances (custom component, no mesh reference)'}
SKIPPED_ACTORS = {'LODActor': 'Hierarchical LOD proxy (the full-detail meshes are drawn)'}
TERRAIN_STEP = 4  # ponytail: one landscape vertex per 4 quads (1.86 m on Hollowmere); full 5081^2 is ~26 M vertices.
LAYER_LIMIT = 5


class MapLibrary(Library):
    game = 'sb'

    def __init__(self, install):
        super().__init__(install, classes=None)
        self.templates = {}

    def mesh(self, p, ref):
        return ue4_mesh(p, ref)

    def sky_mesh(self, source, materials):
        return bool(re.search(r'sky ?sphere|skydome|sky_?dome|bp_sky', source, re.I))

    def resolved(self, p, ref):
        """Own tagged properties over the archetype's (cooked levels store deltas against templates)."""
        props, _ = p.properties(ref)
        template = p.entry(ref)['template']
        if not template:
            return props
        source = p.qualified(template)
        if source.startswith('/Script/'):
            return props
        if source not in self.templates:
            self.templates[source] = {}
            try:
                tp, tr = self.resolve(p, template)
                self.templates[source] = self.resolved(tp, tr)
            except (ValueError, OSError, struct.error):
                pass
        base = {k: v for k, v in self.templates[source].items() if k not in ('AttachParent',)}
        return {**base, **props}


def map_section(path):
    path = str(path)
    if '/MainMenu/' in path:
        return 'Main menu scenes'
    if '/Test/' in path or path.endswith(('Test.umap', 'Prototype')):
        return 'Test maps'
    return 'Hollowmere' if 'Alpha_Resculpt' in path else 'Lobby and practice'


def entry_maps(library):
    names = [n for n in library.files.values() if n.lower().endswith('.umap') and '/Maps/' in n]
    return sorted((PurePosixPath(n) for n in names if '/Tiles/' not in n and not re.search(r'LOD\d*/|_LOD\d+\.umap$', n)
                   and '/Cinematics/' not in n), key=lambda p: p.stem.lower())


def quaternion(q):
    x, y, z, w = q
    return np.array(((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)), (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)),
                     (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))))


def level_packages(path, library):
    """[(package, level matrix)]: the map, its streamed levels (with LevelTransform) and, for the
    world-composition map, every tile in its Tiles folder (placed by each tile's own summary)."""
    result, pending, seen, missing = [], [(str(path), np.eye(4))], set(), []
    if path.stem == 'Alpha_Resculpt':
        folder = str(path.parent)+'/Tiles/'
        pending += [(n, np.eye(4)) for n in sorted(library.files.values()) if n.startswith(folder) and n.endswith('.umap')
                    and not re.search(r'LOD\d*/|_LOD\d+\.umap$', n)]
    while pending:
        file, matrix = pending.pop(0)
        if file.lower() in seen:
            continue
        seen.add(file.lower()); result.append((file, matrix))
        try:
            p = library.package(file)
        except (ValueError, OSError):
            continue
        for ref, e in enumerate(p.exports, 1):
            if not e['class'].startswith('LevelStreaming') or e['name'].startswith('Default__'):
                continue
            props, _ = p.properties(ref)
            asset = (props.get('WorldAsset') or '').split('.', 1)[0].lower()
            if asset not in library.files:
                missing.append(asset); continue
            t = props.get('LevelTransform') or {}
            m = np.eye(4); m[:3, :3] = quaternion(t.get('Rotation') or (0, 0, 0, 1))@np.diag(t.get('Scale3D') or (1, 1, 1))
            m[:3, 3] = t.get('Translation') or (0, 0, 0)
            pending.append((library.files[asset], matrix@m))
    return result


def rotator(degrees):
    return rotation_matrix(*(a*65536/360 for a in degrees))


def local_matrix(props):
    m = np.eye(4)
    m[:3, :3] = np.asarray(rotator(props.get('RelativeRotation') or (0, 0, 0)))@np.diag(props.get('RelativeScale3D') or (1, 1, 1))
    m[:3, 3] = props.get('RelativeLocation') or (0, 0, 0)
    return m


def to_viewer(native):
    m = np.eye(4); m[:3, :3] = BASIS@native[:3, :3]@BASIS.T; m[:3, 3] = viewer(native[:3, 3])
    if not np.isfinite(m).all():
        raise ValueError('Non-finite transform')
    return m.T.reshape(-1).tolist()


def instance_matrices(r):
    """PerInstanceSMData: a bulk array of 64-byte FMatrix rows after the component's LOD data.

    ponytail: located by scanning for (64, count) whose first matrix has a (0,0,0,1) last column; the
    LOD-data layout before it varies with painted vertex colours.
    """
    tail = r.data[r.at:]
    for i in range(0, min(len(tail)-8, 4096), 4):
        size, n = struct.unpack_from('<ii', tail, i)
        if size == 64 and 0 < n and i+8+64*n <= len(tail):
            m = np.frombuffer(tail, '<f4', 16*n, i+8).reshape(n, 4, 4).astype(np.float64)
            if np.allclose(m[:, :, 3], (0, 0, 0, 1)):
                return np.transpose(m, (0, 2, 1))  # row-vector FMatrix -> column-vector matrices
    return np.zeros((0, 4, 4))


def spline_segments(segments, bounds, axis=None, up=None):
    """Native matrices taking a spline mesh's [min, max] along its forward axis onto each segment's chord.

    ponytail: straight chords, no bending, roll, offset or end scaling; curved walls and cracks show as
    straight pieces between their authored points. Bending at import would need one geometry per piece.
    """
    forward = {'ESplineMeshAxis::Y': 1, 'ESplineMeshAxis::Z': 2}.get(axis, 0)
    low, high = np.asarray(bounds, dtype=float)
    native_low, native_high = (np.array((v[2], -v[0], v[1]))*52.5 for v in (low, high))  # viewer -> native
    start, length = min(native_low[forward], native_high[forward]), abs(native_high[forward]-native_low[forward])
    up = np.asarray(up or (0, 0, 1), dtype=float)
    result = []
    for s in segments:
        a, b = np.asarray(s.get('StartPos') or (0, 0, 0), float), np.asarray(s.get('EndPos') or (100, 0, 0), float)
        chord = b-a; distance = np.linalg.norm(chord)
        if distance < 1e-3 or length < 1e-3:
            continue
        x = chord/distance
        y = np.cross(up, x); y = y/np.linalg.norm(y) if np.linalg.norm(y) > 1e-6 else np.array((0., 1, 0))
        z = np.cross(x, y)
        scale = np.ones(3); scale[0] = distance/length
        axes = np.stack((x, y, z), axis=1)@np.diag(scale)  # local forward, side, up -> world
        swap = np.roll(np.eye(3), -forward, axis=0)  # mesh's forward axis first
        m = np.eye(4); m[:3, :3] = axes@swap
        m[:3, 3] = a-m[:3, :3]@(np.eye(3)[forward]*start)
        result.append(m)
    return result


def tile_offset(p):
    if not p.tile_info_offset:
        return np.zeros(3)
    return np.asarray(Reader(p.header, p.names, p.tile_info_offset).unpack('3i'), dtype=float)


def landscape(p, library, materials, chunks):
    """Stitch every LandscapeComponent's heightmap into one grid; bake layer weights."""
    comps = []
    for ref, e in enumerate(p.exports, 1):
        if e['class'] == 'LandscapeComponent':
            comps.append(p.properties(ref)[0])
    if not comps:
        return None
    root = owner = None
    for ref, e in enumerate(p.exports, 1):
        if e['class'] in ('Landscape', 'LandscapeProxy', 'LandscapeStreamingProxy'):
            owner = p.properties(ref)[0]
            root_source = owner.get('RootComponent')
            if root_source:
                rp, rr = library.object(root_source); root = rp.properties(rr)[0]
            break
    root = root or {}
    size = comps[0]['ComponentSizeQuads']; sub = comps[0].get('SubsectionSizeQuads', size); subs = comps[0].get('NumSubsections', 1)
    xs = [c.get('SectionBaseX', 0) for c in comps]; ys = [c.get('SectionBaseY', 0) for c in comps]
    x0, y0 = min(xs), min(ys); w, h = max(xs)-x0+size+1, max(ys)-y0+size+1
    heights = np.zeros((h, w), np.uint16); have = np.zeros((h, w), bool)
    weights = defaultdict(lambda: np.zeros((h, w), np.uint8))
    vertex = np.arange(size+1); s_index = np.minimum(vertex//sub, subs-1); texel = s_index*(sub+1)+vertex-s_index*sub
    images = {}

    def pixels(source):
        if source not in images:
            props, fmt, mips = texture_mips(*library.object(source, {'Texture2D'}))
            mw, mh, raw = mips[0]
            if fmt != 'PF_B8G8R8A8':
                raise ValueError('Unexpected landscape texture format '+fmt)
            images[source] = np.frombuffer(raw[:mw*mh*4], np.uint8).reshape(mh, mw, 4)
        return images[source]

    for c in comps:
        image = pixels(c['HeightmapTexture'])
        bias = c.get('HeightmapScaleBias') or (1, 1, 0, 0)
        ox, oy = int(round(bias[2]*image.shape[1])), int(round(bias[3]*image.shape[0]))
        block = image[oy+texel[:, None], ox+texel[None, :]]
        gx, gy = c.get('SectionBaseX', 0)-x0, c.get('SectionBaseY', 0)-y0
        heights[gy:gy+size+1, gx:gx+size+1] = block[..., 2].astype(np.uint16) << 8 | block[..., 1]  # BGRA: R high, G low
        have[gy:gy+size+1, gx:gx+size+1] = True
        textures = c.get('WeightmapTextures') or []
        wbias = c.get('WeightmapScaleBias') or (1, 1, 0, 0)
        for allocation in c.get('WeightmapLayerAllocations') or []:
            index = allocation.get('WeightmapTextureIndex', 0)
            if index >= len(textures):
                continue
            wimage = pixels(textures[index])
            wx, wy = int(round(wbias[2]*wimage.shape[1])), int(round(wbias[3]*wimage.shape[0]))
            channel = (2, 1, 0, 3)[allocation.get('WeightmapTextureChannel', 0)]  # RGBA channel in BGRA bytes
            layer = weights[allocation['LayerInfo']]
            layer[gy:gy+size+1, gx:gx+size+1] = np.maximum(layer[gy:gy+size+1, gx:gx+size+1],
                                                          wimage[wy+texel[:, None], wx+texel[None, :], channel])
    step = TERRAIN_STEP if max(w, h) > 2049 else 1
    heights = heights[::step, ::step]; visible = have[::step, ::step]
    gh, gw = heights.shape
    scale = np.asarray(root.get('RelativeScale3D') or (100, 100, 100), dtype=float)
    location = np.asarray(root.get('RelativeLocation') or (0, 0, 0), dtype=float)
    linear = np.asarray(rotator(root.get('RelativeRotation') or (0, 0, 0)))@np.diag(scale)
    grid = linear@np.diag((step, step, 1/128))
    # world = location + linear @ (x0 + gx*step, y0 + gy*step, (h-32768)/128)
    origin = -np.linalg.solve(grid, location+linear@np.array((x0, y0, 0.)))+np.array((0, 0, 32768))
    layers, omitted = layer_textures(owner, weights, step, library, materials)
    offset = sum(map(len, chunks))
    if offset % 2:
        chunks.append(b'\0'); offset += 1
    vis = np.packbits(visible.ravel(), bitorder='little').tobytes()  # quad (x, y) drawn when its first corner is covered
    chunks.extend((heights.astype('<u2').tobytes(), vis))
    macro = macro_texture(weights, step, layers['colors'], materials)
    return dict(entity=p.qualified(next(i for i, e in enumerate(p.exports, 1) if e['class'] == 'LandscapeComponent')),
                width=gw, height=gh, offset=offset, bytes=heights.size*2, visibility_bytes=len(vis), edge_bytes=0,
                to_world=[*origin.tolist(), *grid.reshape(-1).tolist()], layers=layers['rows'], omitted_layers=omitted,
                textures={'MacroTexture': macro}, vertex_light=None, inverted=bool(np.linalg.det(linear) < 0),
                native=dict(quads=[w-1, h-1], step=step, components=len(comps), scale=scale.tolist()))


def layer_textures(owner, weights, step, library, materials):
    """Each paint layer's base-colour texture from the landscape material's '<Layer>Albedo...' parameters."""
    params, scalars = {}, {}
    source = (owner or {}).get('LandscapeMaterial')
    try:
        for _, _, props, _ in materials.chain(source) if source else []:
            for v in props.get('TextureParameterValues') or []:
                params.setdefault(((v.get('ParameterInfo') or {}).get('Name') or '').lower(), v.get('ParameterValue'))
            for v in props.get('ScalarParameterValues') or []:
                scalars.setdefault(((v.get('ParameterInfo') or {}).get('Name') or '').lower(), v.get('ParameterValue'))
    except (ValueError, OSError):
        pass
    coverage = sorted(weights, key=lambda k: -int(weights[k][::step, ::step].sum(dtype=np.int64)))
    rows, omitted, colors = [], [], {}
    for info in coverage:
        try:
            name = library.state(info)[0].get('LayerName') or info.rsplit('.', 1)[-1].replace('_LayerInfo', '')
        except (ValueError, OSError):
            name = info.rsplit('.', 1)[-1].replace('_LayerInfo', '')
        flat = name.lower().replace(' ', '')
        texture = next((v for k, v in params.items() if v and k.replace(' ', '').startswith(flat)
                        and re.search(r'albedo|basecolou?r', k) and 'far' not in k), None)
        if not texture:
            omitted.append(dict(source=info, layer=name, reason='No base-colour parameter for this layer; drawn from the macro colour only'))
            continue
        row = materials.texture(texture)
        if row['status'] != 'ready':
            omitted.append(dict(source=info, layer=name, reason=row.get('reason'))); continue
        colors[info] = average_color(materials.directory/row['texture'])
        tile = scalars.get(flat+' near texture scale') or scalars.get(name.lower()+' near texture scale') or 500
        uv = np.eye(4); uv[0, 0] = uv[1, 1] = 1/tile
        alpha = None
        if len(rows) < LAYER_LIMIT:
            raw = weights[info][::step, ::step]
            alpha = 'terrain_alpha_'+hashlib.sha256(f'{raw.shape}'.encode()+raw.tobytes()).hexdigest()[:16]+'.png'
            write_texture(materials.directory, alpha, Image.fromarray(np.ascontiguousarray(raw), 'L'))
            rows.append(dict(source=info, layer=name, material=texture, texture=row['texture'], alpha=alpha,
                             matrix=uv.T.tolist(), scale=[tile, tile], rotation=0, pan=[0, 0]))
        else:
            omitted.append(dict(source=info, layer=name, reason=f'Beyond the {LAYER_LIMIT} drawn layers; in the macro colour only'))
    return dict(rows=rows, colors=colors), omitted


def average_color(path):
    from tools.texture_storage import open_texture
    image = open_texture(path).convert('RGB').resize((1, 1), Image.BOX)
    return np.asarray(image.getpixel((0, 0)), dtype=float)


def macro_texture(weights, step, colors, materials):
    """Weight-blended layer colours at grid resolution (layers without a texture keep a neutral earth tone)."""
    total = None; rgb = None
    for info, layer in weights.items():
        weight = layer[::step, ::step].astype(np.float32)[..., None]
        color = colors.get(info, np.array((96., 88., 70.)))
        rgb = weight*color if rgb is None else rgb+weight*color
        total = weight if total is None else total+weight
    if rgb is None:
        return None
    image = np.where(total > 0, rgb/np.maximum(total, 1), 90).clip(0, 255).astype(np.uint8)
    name = 'terrain_macro_'+hashlib.sha256(image.tobytes()).hexdigest()[:16]+'.png'
    write_texture(materials.directory, name, Image.fromarray(image, 'RGB'))
    return name


def build_map(path, library, models, materials):
    packages = level_packages(path, library)
    model_library, decorations, failures, omitted, views, terrains, chunks = {}, [], [], [], [], [], []
    batches = defaultdict(list); sun = None; zones = []; actors = 0; instances = []

    def place(source, overrides, matrices, record, fade):
        try:
            look, model = models.get(source, 'StaticMesh' if not record.get('skeletal') else 'SkeletalMesh', overrides)
        except (ValueError, OSError, KeyError) as exc:
            failures.append(dict(record, source=source, reason=str(exc))); return
        model_library[look] = model
        if model.get('sky'):  # drawn first through the page's sky path, not as an instanced batch
            instances.extend(dict(record, look=look, matrix=to_viewer(m)) for m in matrices); return
        cell = 512 if not record.get('foliage') else 128
        for m in matrices:
            v = to_viewer(m)
            batches[(look, tuple(np.floor(np.asarray(v)[12:15:2]/cell).astype(int)), record.get('foliage', False), fade)].append((v, record))

    for file, streamed in packages:
        try:
            p = library.package(file)
        except (ValueError, OSError) as exc:
            failures.append(dict(source=file, reason=str(exc))); continue
        level = streamed.copy(); level[:3, 3] += tile_offset(p)
        exports = {p.qualified(i): i for i in range(1, len(p.exports)+1)}
        props_cache = {}

        def props(ref):
            if ref not in props_cache:
                props_cache[ref] = library.resolved(p, ref)
            return props_cache[ref]

        def world(ref, depth=0):
            pr = props(ref); m = local_matrix(pr)
            parent = pr.get('AttachParent')
            if parent is None and depth == 0:
                owner = p.entry(ref)['outer']
                root = props(owner).get('RootComponent') if owner > 0 else None
                parent = root if root and exports.get(root) != ref else None
            if parent and parent in exports and depth < 32:
                return world(exports[parent], depth+1)@m
            return m

        if 'Tiles/' not in file:
            row = landscape(p, library, materials, chunks) if not terrains else None
            if row:
                terrains.append(row)
        for ref, e in enumerate(p.exports, 1):
            cls = e['class']
            if 'PlayerStart' in cls and not e['name'].startswith('Default__'):
                try:
                    pr = props(ref); root = pr.get('RootComponent')
                    if root in exports:
                        m = level@world(exports[root])
                        yaw = (props(exports[root]).get('RelativeRotation') or (0, 0, 0))[1]
                        views.append(dict(origin=viewer(m[:3, 3]+(0, 0, 170)).tolist(), yaw=yaw, pitch=0))
                except (ValueError, OSError, struct.error):
                    pass
            if cls == 'DirectionalLightComponent' and sun is None:
                try:
                    pr = props(ref); m = level@world(ref)
                    color = pr.get('LightColor') or (255, 255, 255, 255)
                    sun = dict(direction=(BASIS@m[:3, :3]@np.array((-1., 0, 0))).tolist(),
                               color=[color[2]/255, color[1]/255, color[0]/255], brightness=pr.get('Intensity', 3.14)/3.14)
                except (ValueError, OSError, struct.error):
                    pass
            if cls == 'ExponentialHeightFogComponent':
                try:
                    pr = props(ref)
                    if pr.get('bVisible', True):
                        color = pr.get('FogInscatteringColor') or [.45, .5, .6, 1]
                        zones.append(dict(kind=cls, color=[c*255 for c in color[2::-1]]+[255], density=pr.get('FogDensity', .02),
                                          start=pr.get('StartDistance', 0), height=(level@world(ref))[2, 3],
                                          height_falloff=pr.get('FogHeightFalloff', .2), FogMaxOpacity=pr.get('FogMaxOpacity', 1)))
                except (ValueError, OSError, struct.error):
                    pass
            mesh_class = cls in INSTANCED or cls in ('StaticMeshComponent', 'SkeletalMeshComponent', 'SplineMeshComponent') or cls in SKIPPED_COMPONENTS
            if not mesh_class:
                if cls.endswith('Actor') or cls.endswith('_C'):
                    actors += 1
                continue
            owner = e['outer']; owner_cls = p.entry(owner)['class'] if owner > 0 else ''
            record = dict(entity=p.qualified(owner) if owner > 0 else p.qualified(ref), classname=owner_cls, component=e['name'], npc=False)
            try:
                if owner_cls in SKIPPED_ACTORS:
                    omitted.append(dict(record, reason=SKIPPED_ACTORS[owner_cls])); continue
                if cls in SKIPPED_COMPONENTS:
                    omitted.append(dict(record, reason=SKIPPED_COMPONENTS[cls])); continue
                pr = props(ref)
                source = pr.get('StaticMesh') or pr.get('SkeletalMesh')
                if not source:
                    continue
                if pr.get('bHiddenInGame') or pr.get('bVisible') is False or (owner > 0 and props(owner).get('bHidden')):
                    omitted.append(dict(record, source=source, reason='Hidden component or actor')); continue
                m = level@world(ref)
                fade = float(pr.get('InstanceEndCullDistance') or pr.get('CachedMaxDrawDistance') or pr.get('LDMaxDrawDistance') or 0)
                if cls in INSTANCED:
                    _, r = p.properties(ref)
                    matrices = [m@i for i in instance_matrices(r)]
                    record['foliage'] = cls.startswith('Foliage')
                elif cls == 'SplineMeshComponent':
                    _, model = models.get(source, 'StaticMesh', tuple(pr.get('OverrideMaterials') or ()))
                    segments = pr.get('SplineInstanceDataList') or [pr.get('SplineParams') or {}]
                    matrices = [m@s for s in spline_segments(segments, model['bounds'], pr.get('ForwardAxis'), pr.get('SplineUpDir'))]
                    record['spline_approximation'] = 'straight segment from start to end point'
                else:
                    matrices = [m]
                record['skeletal'] = cls == 'SkeletalMeshComponent'
                if matrices:
                    place(source, tuple(pr.get('OverrideMaterials') or ()), matrices, record, fade)
            except (ValueError, OSError, KeyError, struct.error) as exc:
                failures.append(dict(record, reason=str(exc)))
    deco_chunks = []
    for (look, cell, foliage, fade), rows in batches.items():
        name = model_library[look]['source'].rsplit('.', 1)[-1]
        decorations.append(dict(look=look, entity=rows[0][1]['entity'], offset=sum(map(len, deco_chunks)), count=len(rows), npc=False, placed=not foliage,
                                decorationType=('Foliage / '+name) if foliage else 'Placed meshes', **({'fade': [0, fade]} if fade > 0 else {})))
        deco_chunks.append(np.asarray([v for v, _ in rows], '<f4').tobytes())
    if not views:
        views = [dict(origin=[0, 150, 0], yaw=0, pitch=-20)]
    span = 3000.
    for t in terrains:
        span = max(span, t['width']*t['native']['step']*t['native']['scale'][0]/52.5)
    placements = sum(d['count'] for d in decorations)
    scene = dict(game='sb', name=path.stem, title='Hollowmere' if path.stem == 'Alpha_Resculpt' else path.stem.replace('_', ' '), format='ue4.22', actors=actors, preview_extent=span,
                 vertices=0, indices=0, groups=[], model_library=model_library, instances=instances, decorations=decorations,
                 terrain=terrains, viewpoints=views[:9], sky=None, zones=zones, sun=sun, speedtrees=0, npcs=0, placements=placements,
                 streamed_packages=[f for f, _ in packages], lightmap_references=[], omitted=omitted, model_failures=failures,
                 missing=sorted({r.get('source', r.get('entity', 'unknown')) for r in failures}),
                 lighting=dict(display_power=1, source='Fixed ambient plus the level\'s directional light; baked lighting not evaluated'),
                 coordinate_system='Unreal 4 (x,y,z) cm -> viewer (-y,z,x), units /52.5',
                 gaps=['Lighting is approximate: no baked lightmaps or the toon post-process',
                       f'Landscape drawn at one vertex per {TERRAIN_STEP} quads with up to {LAYER_LIMIT} textured layers over a baked colour map',
                       'Spline meshes (walls, cracks) are straight pieces between their points, not bent',
                       'HLOD proxies, effects, decals, water and gameplay actors are omitted'])
    blobs = dict(geometry=b'', terrain=b''.join(chunks), lighting=b'', decorations=b''.join(deco_chunks))
    models.cache.clear(); models.variants.clear()
    return scene, blobs


def import_maps(install, output=LOCAL_DATA, replace=False, map_names=None, model_root=None):
    return ue3.import_maps(install, output, replace, map_names, model_root, game='sb', library_factory=MapLibrary,
                           materials_factory=Materials, entries=entry_maps, section=map_section,
                           name_for=lambda p: p.stem, scene_builder=build_map)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', required=True); parser.add_argument('--output', default=LOCAL_DATA)
    parser.add_argument('--models'); parser.add_argument('--map', action='append'); parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    result = import_maps(args.install, args.output, args.replace, args.map, args.models)
    print(json.dumps({k: v for k, v in result.items() if k not in ('material_results', 'texture_results')}, indent=2, default=str))
    raise SystemExit(bool(result['failed']))

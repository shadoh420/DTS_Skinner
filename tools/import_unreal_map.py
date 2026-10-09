"""Unreal maps (Maps/**.unr) for the Unreal Maps page.

python tools/import_unreal_map.py --install C:/Unreal [--replace]

A map is a package (read by tools/import_unreal.py) whose Level export holds the placed actors and a Model, the
level's BSP: points and vectors, nodes (each a convex polygon of NumVertices corners from Verts[VertPool...], on one
surface), surfaces (texture, poly flags, base point, normal, U and V vectors, pan U and V) and the lighting data.
Every node with corners is drawn, on its surface; the game draws the same polygons (SurrealEngine, read as a format
reference only, walks the same node list). Invisible surfaces and zone portals are left out: the game never draws them.

- Texture coordinates: u = ((P - base) . U + PanU) / (USize * DrawScale), v likewise; row 0 is the image's top.
- Poly flags (SurrealEngine's precedence): the texture's own PolyFlags join the surface's, and a bMasked texture masks
  even surfaces not flagged masked (Glathriel2's cobwebs in 227); translucent drops masked. A masked surface cuts out
  palette index 0 whether or not its texture is bMasked (NyLeve's sky panorama in 227), so every palette texture of
  the pack is written with index 0 clear; opaque surfaces ignore alpha. Auto-panning surfaces move 64 texels a second times their zone's TexUPanSpeed or
  TexVPanSpeed (1 unless the ZoneInfo sets it; the zone on the surface's front side, the LevelInfo for zone 0).
- Sky: fake-backdrop surfaces show the sky zone, the level seen from the SkyZoneInfo every zone links to
  (ZoneInfo.LinkToSkybox: the last one in the map, or the last high-detail one) with the view turned by its
  Rotation. With no SkyZoneInfo the game draws them as ordinary surfaces (Inter3, Inter4, Inter14).
- Placement as the model importer's: Unreal is x forward, y right, z up; the page's (x, y, z) is (-y, z, x) / 52.5,
  in metres. Each polygon is wound so that it faces along its surface's normal, counter-clockwise seen from the front.
- Viewpoints: the cutscene maps' CS_Camera actors, the PlayerStarts (at the eye of a player standing on the solid
  floor below, HEIGHT + EYE over it), the first point of each InterpolationPoint camera path; yaw and pitch are 65536
  to a turn, yaw 0 along x.

Written under local-data/unreal-maps/GAME: index.json (one entry per map), maps/ID/scene.json (texture groups,
viewpoints, counts) with maps/ID/geometry.bin (float32 positions xyz, float32 texture uv, uint32 triangle indices,
one after the other), and textures/*.png shared by every map (kept when already there, rewritten by --replace).
"""
import argparse
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image

try:
    from tools.import_diabotical_models import safe
    from tools.import_unreal import GAMES, SCALE, Library, Package, rotation
    from tools.local_data import LOCAL_DATA
except ImportError:  # Run as a script from tools/.
    from import_diabotical_models import safe
    from import_unreal import GAMES, SCALE, Library, Package, rotation
    from local_data import LOCAL_DATA

INVISIBLE, MASKED, TRANSLUCENT, NOT_SOLID, MODULATED, FAKE_BACKDROP, TWO_SIDED = 0x1, 0x2, 0x4, 0x8, 0x40, 0x80, 0x100
AUTO_U_PAN, AUTO_V_PAN, UNLIT, PORTAL, MIRRORED = 0x200, 0x400, 0x400000, 0x4000000, 0x8000000
# What the page needs of a surface's poly flags to draw it; surfaces differing only in other flags share a group.
DRAWN_FLAGS = MASKED | TRANSLUCENT | MODULATED | FAKE_BACKDROP | TWO_SIDED | AUTO_U_PAN | AUTO_V_PAN | UNLIT | MIRRORED
GROUPS = {'upak': 'Return to Na Pali'}
# A player stands on the floor below its PlayerStart, its centre CollisionHeight over it and its eye BaseEyeHeight over
# that (UnrealShare's Human: 39 and 23; the game's eye, checked on NyLeve's start, 2026-10-09).
HEIGHT, EYE = 39, 23
# ponytail: movers and decorations are no floors yet, so a start more than this over the BSP floor (SkyCaves' 1080, on
# something not drawn) keeps its own height; drop onto actors too once they are placed.
MAX_DROP = 200


def viewer(points):
    """Unreal points (n, 3) in the page's axes and metres."""
    points = np.asarray(points, np.float64)
    return np.stack([-points[:, 1], points[:, 2], points[:, 0]], 1) * SCALE


def actors_of(level, refs, classes):
    """{reference: properties} of the placed actors in `refs` whose class name is in `classes`."""
    return {ref: level.properties(ref) for ref in refs if ref > 0 and level.ref_name(level.exports[ref - 1]['cls']) in classes}


def vector(props, name, fmt='3f'):
    raw = props.get(name)
    return struct.unpack('<' + fmt, raw) if isinstance(raw, bytes) and len(raw) == 12 else (0, 0, 0)


def floor_below(floors, x, y, z):
    """Height of the highest of the upward triangles `floors` (n, 3, 3: Unreal points) under (x, y) below z, or None."""
    if not len(floors):
        return None
    a, b, c = floors[:, 0], floors[:, 1], floors[:, 2]
    side = lambda p, q: (q[:, 0] - p[:, 0]) * (y - p[:, 1]) - (q[:, 1] - p[:, 1]) * (x - p[:, 0])
    d1, d2, d3 = side(a, b), side(b, c), side(c, a)
    normal = np.cross(b - a, c - a)
    hit = (((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))) & (np.abs(normal[:, 2]) > 1e-9)
    heights = a[hit, 2] - (normal[hit, 0] * (x - a[hit, 0]) + normal[hit, 1] * (y - a[hit, 1])) / normal[hit, 2]
    heights = heights[heights < z]
    return float(heights.max()) if len(heights) else None


def build_map(library, level, default):
    """(scene, geometry bytes, {png: image}) of map package `level`."""
    found = level.find({'Level'})
    if not found:
        raise ValueError('no Level export')
    layout = level.level(found[0])
    model = level.model(layout['model'])
    points, vectors, verts = np.array(model['points']), np.array(model['vectors']), model['verts']
    info = next(iter(actors_of(level, layout['actors'], {'LevelInfo'}).values()), {})
    if info.get('DefaultTexture'):  # What the level draws where a surface has no texture; Engine's otherwise.
        default = level, info['DefaultTexture']
    zones = {}

    def zone(index):
        """Properties of zone `index`'s ZoneInfo: the LevelInfo's for zone 0 or a zone without one."""
        if index not in zones:
            ref = model['zones'][index][0] if 0 < index < len(model['zones']) else 0
            zones[index] = level.properties(ref) if ref > 0 else info
        return zones[index]

    # ponytail: one sky for the whole map, as every zone of the install's maps links to the same one (227's per-zone
    # SkyZoneInfoTag is set nowhere); pick per zone if a map ever sets it.
    skies = list(actors_of(level, layout['actors'], {'SkyZoneInfo'}).values())
    skies = [props for props in skies if props.get('bHighDetail')] or skies
    sky = skies[-1] if skies else None

    textures, images, missing = {}, {}, []

    def texture_of(ref):
        """(png name, width, height, the texture's own poly flags) of surface texture reference `ref`; none draws the
        default texture."""
        if ref not in textures:
            pick = (level, ref) if ref else default
            # Index 0 clear on every palette texture: where a surface is masked the game cuts it out (even where
            # the texture is not bMasked: the sky panoramas), and opaque surfaces ignore alpha.
            image = pick and library.texture(*pick, masked=True)
            if not image:
                textures[ref] = ('missing.png', 64, 64, 0)
                images['missing.png'] = None
                missing.append('.'.join(pick[0].ref_path(pick[1])) if pick else 'Engine.DefaultTexture')
            else:
                package, at = library.resolve(*pick)
                props = package.properties(at)
                scale = props.get('DrawScale', 1.0) or 1.0
                png = safe(image[0]) + '.png'
                images[png] = image[1]
                own = props.get('PolyFlags', 0) | (MASKED if props.get('bMasked') else 0)
                textures[ref] = (png, image[1].width * scale, image[1].height * scale, own)
        return textures[ref]

    groups, flipped, polygons, floors = {}, 0, 0, []
    for node in model['nodes']:
        if node['count'] < 3 or not 0 <= node['surf'] < len(model['surfs']):
            continue
        surf = model['surfs'][node['surf']]
        if surf['flags'] & (INVISIBLE | PORTAL):
            continue
        png, width, height, own = texture_of(surf['texture'])
        flags = surf['flags'] | own  # The game adds the texture's own flags to the surface's.
        if flags & INVISIBLE:
            continue
        if flags & TRANSLUCENT:
            flags &= ~MASKED
        if not sky:
            flags &= ~FAKE_BACKDROP
        pan = (0, 0)
        if flags & (AUTO_U_PAN | AUTO_V_PAN):
            front = zone(node['zones'][1])
            pan = (round(64 * front.get('TexUPanSpeed', 1.0) / width, 6) if flags & AUTO_U_PAN else 0,
                   round(64 * front.get('TexVPanSpeed', 1.0) / height, 6) if flags & AUTO_V_PAN else 0)
        corners = points[[verts[node['pool'] + n][0] for n in range(node['count'])]]
        if not flags & NOT_SOLID and vectors[surf['normal']][2] > 0:
            floors.extend(corners[[0, k, k + 1]] for k in range(1, len(corners) - 1))
        base, u, v = points[surf['base']], vectors[surf['u']], vectors[surf['v']]
        uv = np.stack([((corners - base) @ u + surf['pan'][0]) / width, ((corners - base) @ v + surf['pan'][1]) / height], 1)
        placed = viewer(corners)
        # Newell's normal of the corners in the page's axes, counter-clockwise; reverse them when it points away
        # from the surface's normal.
        nxt = np.roll(placed, -1, 0)
        newell = np.cross(placed, nxt).sum(0)
        if newell @ viewer([vectors[surf['normal']]])[0] < 0:
            placed, uv = placed[::-1], uv[::-1]
            flipped += 1
        polygons += 1
        groups.setdefault((png, flags & DRAWN_FLAGS, pan), []).append((placed, uv))

    positions, uvs, indices, table = [], [], [], []
    count = 0
    for (png, flags, pan), polys in sorted(groups.items()):
        start = len(indices)
        for placed, uv in polys:
            n = len(placed)
            positions.append(placed)
            uvs.append(uv)
            indices.extend(x for k in range(1, n - 1) for x in (count, count + k, count + k + 1))
            count += n
        table.append(dict(texture=png, flags=flags, start=start, count=len(indices) - start, **({'pan': pan} if any(pan) else {})))
    positions = np.concatenate(positions).astype('<f4') if positions else np.zeros((0, 3), '<f4')
    uvs = np.concatenate(uvs).astype('<f4') if uvs else np.zeros((0, 2), '<f4')
    geometry = positions.tobytes() + uvs.tobytes() + np.asarray(indices, '<u4').tobytes()

    # The cutscene maps (Intro1, Intro2, End) are seen through their CS_Camera actors (their PlayerStart sees
    # nothing), then PlayerStarts at a standing player's eye, then where each InterpolationPoint camera path starts.
    views, floors = [], np.array(floors).reshape(-1, 3, 3)
    for classes in ({'CS_Camera'}, {'PlayerStart'}, {'InterpolationPoint'}):
        for props in actors_of(level, layout['actors'], classes).values():
            if props.get('Position', 0):
                continue
            pitch, yaw, _roll = vector(props, 'Rotation', '3i')
            x, y, z = vector(props, 'Location')
            if 'PlayerStart' in classes:
                floor = floor_below(floors, x, y, z)
                z = floor + HEIGHT + EYE if floor is not None and z - floor <= MAX_DROP else z + EYE
            views.append(dict(origin=[round(float(c), 3) for c in viewer([(x, y, z)])[0]],
                              yaw=yaw % 65536 * 360 / 65536, pitch=(pitch + 32768) % 65536 / 65536 * 360 - 180))
    if not views:
        middle = viewer(points).mean(0) if len(points) else np.zeros(3)
        views.append(dict(origin=[round(float(x), 3) for x in middle], yaw=0, pitch=0))
    if not any(group['flags'] & FAKE_BACKDROP for group in table):
        sky = None  # Nothing shows it.
    if sky:  # Its place, and its turn in the page's axes (the reflection M of viewer() on both sides).
        axes = np.array([[0, -1, 0], [0, 0, 1], [1, 0, 0]])
        turn = axes @ np.array(rotation(*vector(sky, 'Rotation', '3i'))) @ axes.T
        sky = dict(origin=[round(float(c), 3) for c in viewer([vector(sky, 'Location')])[0]],
                   rotation=[round(float(c), 6) for c in turn.flatten()])
    scene = dict(name=level.path.stem, title=info.get('Title', ''), author=info.get('Author', ''), version=level.version,
                 vertices=len(positions), indices=len(indices), groups=table, viewpoints=views, sky=sky,
                 polygons=polygons, flipped=flipped, missing=sorted(missing))
    return scene, geometry, images


def import_maps(install, output, replace=False, game='unreal'):
    """Import every map of the Unreal (game unreal) or Unreal Tournament (ut) install `install` into `output`."""
    install, output = Path(install).expanduser(), Path(output)
    library = Library(install)
    if not library.package(GAMES[game]) or not library.maps:
        title = 'Unreal Tournament' if game == 'ut' else 'Unreal'
        raise ValueError(f'No {title} install with maps (System/{GAMES[game]}.u, Maps/) in {install}')
    engine = library.package('engine')
    default = engine and engine.export_by_path(('Engine', 'DefaultTexture'), 'Texture')
    default = default and (engine, default)
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    index_path = output / 'index.json'
    index = {item['id']: item for item in json.loads(index_path.read_text(encoding='utf-8'))} if index_path.is_file() else {}
    result, written = dict(imported=[], skipped=[], failed={}), set()
    taken = set()
    for path in library.maps:
        ident = safe(path.stem.lower())
        if ident in taken:
            ident = safe(f'{path.parent.name}_{path.stem}'.lower())
        taken.add(ident)
        folder = output / 'maps' / ident
        if ident in index and (folder / 'scene.json').is_file() and not replace:
            result['skipped'].append(ident)
            continue
        try:
            scene, geometry, images = build_map(library, Package(path), default)
        except (OSError, ValueError, struct.error, IndexError, KeyError) as exc:
            result['failed'][path.stem] = str(exc)
            continue
        for png, image in images.items():
            target = output / 'textures' / png
            if png not in written and (replace or not target.is_file()):  # A re-import rewrites them once.
                (image or Image.new('RGB', (8, 8), '#808080')).save(target)
                written.add(png)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'geometry.bin').write_bytes(geometry)
        (folder / 'scene.json').write_text(json.dumps(scene, separators=(',', ':')), encoding='utf-8')
        group = GROUPS.get(path.parent.name.lower(), 'Unreal Tournament' if game == 'ut' else 'Unreal')
        index[ident] = dict(id=ident, name=path.stem, title=scene['title'], group=group)
        result['imported'].append(ident)
    temporary = index_path.with_suffix('.tmp')
    # The game's own maps first, then the expansion's.
    order = lambda item: (item['group'] in GROUPS.values(), item['group'], item['name'].lower())
    temporary.write_text(json.dumps(sorted(index.values(), key=order),
                                    separators=(',', ':')), encoding='utf-8')
    temporary.replace(index_path)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--game', choices=GAMES, default='unreal')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    result = import_maps(args.install, args.output or LOCAL_DATA / 'unreal-maps' / args.game, args.replace, args.game)
    print(json.dumps(dict(imported=len(result['imported']), skipped=len(result['skipped']), failed=result['failed'])))

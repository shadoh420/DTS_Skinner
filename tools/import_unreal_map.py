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

- Lighting: each lit surface's lightmap is rebuilt from the lights its lightmap entry lists, their shadow bits and the
  zone's ambient light (see lightmap(); docs/unreal-map-viewer.md has the rules and how they were checked).
- Placed actors: movers (their brush's polygons at their start key, with the brush's own lightmaps) and actors drawn as
  meshes (lit at their vertices: vertex colours, their lightmap texel white).

Written under local-data/unreal-maps/GAME: index.json (one entry per map), maps/ID/scene.json (texture groups,
viewpoints, counts, the lightmap atlas's size) with maps/ID/geometry.bin (float32 positions xyz, float32 texture uv,
float32 lightmap uv, uint8 RGBA vertex colours, uint32 triangle indices, one after the other) and maps/ID/lightmap.png,
and textures/*.png shared by every map (kept when already there, rewritten by --replace).
"""
import argparse
import itertools
import json
import math
from pathlib import Path
import struct

import numpy as np
from PIL import Image

try:
    from tools.import_diabotical_models import safe
    from tools.import_unreal import GAMES, POSES, SCALE, STYLE_FLAGS, Library, Package, mesh_faces, mesh_points, rotation
    from tools.local_data import LOCAL_DATA
except ImportError:  # Run as a script from tools/.
    from import_diabotical_models import safe
    from import_unreal import GAMES, POSES, SCALE, STYLE_FLAGS, Library, Package, mesh_faces, mesh_points, rotation
    from local_data import LOCAL_DATA

INVISIBLE, MASKED, TRANSLUCENT, NOT_SOLID, MODULATED, FAKE_BACKDROP, TWO_SIDED = 0x1, 0x2, 0x4, 0x8, 0x40, 0x80, 0x100
AUTO_U_PAN, AUTO_V_PAN, UNLIT, PORTAL, MIRRORED = 0x200, 0x400, 0x400000, 0x4000000, 0x8000000
# What the page needs of a surface's poly flags to draw it; surfaces differing only in other flags share a group.
DRAWN_FLAGS = MASKED | TRANSLUCENT | MODULATED | FAKE_BACKDROP | TWO_SIDED | AUTO_U_PAN | AUTO_V_PAN | UNLIT | MIRRORED
GROUPS = {'upak': 'Return to Na Pali'}
# A player stands on the floor below its PlayerStart, its centre CollisionHeight over it and its eye BaseEyeHeight over
# that (UnrealShare's Human: 39 and 23; the game's eye, checked on NyLeve's start, 2026-10-09).
HEIGHT, EYE, RADIUS = 39, 23, 17  # and its CollisionRadius
# The floors are the BSP's and the movers' (SkyCaves' starts stand on a mover). ponytail: meshes are no floors (the game
# collides with their cylinders), so a start more than this over a floor (two of DmRadikus') keeps its own height.
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


def light_color(hue, saturation, brightness):
    """(r, g, b) of a light's or zone's hue, saturation and brightness (0-255 each; saturation 255 is white): the
    brightest channel brightness / 255 / 2, so a white light of 255 lights a fully lit texel (shadow weights summing to
    2) to exactly 1. Linear, not SurrealEngine's 6.512735 * sqrt(brightness), which lit Glathriel2's dim lights (brightness
    80 and under) 2.7 times too strongly against 227 (fitted on ten views, 2026-10-09); hue and saturation as it reads them."""
    if brightness <= 0:
        return np.zeros(3)
    value = 127.5 * brightness / 255
    if saturation >= 250:
        return np.full(3, value / 255)
    grey = saturation / 2.5 + (2 if saturation / 2.5 > 32 else 0)
    sector, part = divmod(hue / 85, 1)
    low = grey * value / 104
    rise, fall = part * value + low * (1 - part), (1 - part) * value + low * part
    return np.array(((fall, rise, low), (low, fall, rise), (rise, low, fall))[min(int(sector), 2)]) / 255


LIGHT_AVERAGE = {2: .65, 3: .5, 4: .5, 5: .5, 7: .8}  # LT_Pulse, LT_Blink, LT_Flicker, LT_Strobe, LT_SubtlePulse


def light_of(props):
    """What a light actor's (class defaults merged) properties light with, or None when it gives none: location,
    radius, colour, effect, spot axis and cone."""
    kind = props.get('LightType', 0)
    # A changing light is drawn at its average brightness (a page shows one still): pulse 0.65 + 0.35 sin, subtle
    # pulse 0.8 + 0.2 sin, blink, strobe and flicker on half the time. ponytail: the palette ones as steady.
    brightness = props.get('LightBrightness', 0) * LIGHT_AVERAGE.get(kind, 1)
    # A TriggerLight (bInitiallyOn False by default) that starts off gives no light until triggered.
    if not kind or brightness <= 0 or not props.get('bInitiallyOn', True):
        return None
    pitch, yaw, roll = vector(props, 'Rotation', '3i')
    return dict(location=np.array(vector(props, 'Location')), radius=(int(props.get('LightRadius', 0)) + 1) * 25,
                color=light_color(props.get('LightHue', 0), props.get('LightSaturation', 0), brightness),
                effect=props.get('LightEffect', 0), axis=np.array(rotation(pitch, yaw, roll))[:, 0],
                cone=props.get('LightCone', 0))


# Light effects (ELightEffect) drawn other than the plain one: static spot and spotlight, non-incidence, shell, omni
# bump map (none), cylinder. ponytail: the waving, flickering and turning ones light as the plain one, still.
STATIC_SPOT, SPOTLIGHT, NON_INCIDENCE, SHELL, OMNI_BUMP_MAP, CYLINDER = 8, 12, 13, 14, 15, 17


def lightmap(model, entry, base, u, v, normal, ambient, lights, place=None):
    """Light (h, w, 3), 0 to 1, of lightmap entry `entry` on a surface (base point, U and V vectors, normal): the
    zone's ambient colour plus, for each light the entry lists, its shadow bits (blurred 3 by 3 with weights summing
    to 2) times its falloff and incidence times its colour, each light at most 1 a channel. Texel (i, j) is the point
    of the surface's plane where U.(P - base) = PanX + i UScale and V.(P - base) = PanY + j VScale. `place` (a mover's
    surfaces: matrix, offset) takes those points and the normal from the surface's own space into the world."""
    width, height = entry['size']
    pitch = (width + 7) // 8
    solve = np.linalg.pinv(np.array([u, v, normal], np.float64))  # Pseudo: Crashsite has U along V.
    cu, cv = np.meshgrid(entry['pan'][0] + np.arange(width) * entry['scale'][0],
                         entry['pan'][1] + np.arange(height) * entry['scale'][1])
    points = np.asarray(base) + np.stack([cu, cv, np.zeros_like(cu)], -1) @ solve.T
    unit = np.asarray(normal, np.float64)
    if place:
        matrix, offset = place
        points = points @ matrix.T + offset
        unit = np.linalg.inv(matrix).T @ unit
    unit = unit / np.linalg.norm(unit)
    light = np.broadcast_to(ambient, (height, width, 3)).copy()
    for index in itertools.count() if entry['lights'] >= 0 else ():
        found = lights[entry['lights'] + index]
        if found == 'end':
            break
        start = entry['offset'] + index * pitch * height
        if start + pitch * height > len(model['light_bits']):
            raise ValueError('a lightmap reads past the light bits')
        if found is None:
            continue
        bits = np.frombuffer(model['light_bits'], np.uint8, pitch * height, start).reshape(height, pitch)
        lit = np.pad(np.unpackbits(bits, 1, bitorder='little')[:, :width].astype(np.float64), 1, mode='edge')
        shadow = sum(weight * lit[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
                     for dy in (-1, 0, 1) for dx in (-1, 0, 1) for weight in [(.5, .25, .125)[abs(dx) + abs(dy)]])
        to_light = found['location'] - points
        distance = np.maximum(np.linalg.norm(to_light, axis=-1), 1e-6)
        near = distance / found['radius']
        effect = found['effect']
        if effect == NON_INCIDENCE:
            strength = np.maximum(1 - near, 0)
        elif effect == SHELL:
            strength = np.where((near > .8) & (near < 1), 1 - 10 * np.abs(near - .9), 0)
        elif effect == CYLINDER:
            strength = np.maximum(1 - (to_light[..., 0] ** 2 + to_light[..., 1] ** 2) / found['radius'] ** 2, 0)
        elif effect == OMNI_BUMP_MAP:
            strength = np.zeros_like(near)
        else:
            facing = np.abs(to_light @ unit) / distance
            strength = np.where(near < 1, np.minimum((1 + 2 * near ** 3 - 3 * near ** 2) / near, 1), 0) * facing
            if effect in (STATIC_SPOT, SPOTLIGHT):
                edge = 1 - found['cone'] / 255
                along = -(to_light @ found['axis']) / distance
                spot = 1 - np.minimum((1 - along) / (1 - edge), 1) if edge < 1 else np.zeros_like(near)
                strength = strength * spot ** 2
        light += np.minimum(shadow[..., None] * strength[..., None] * found['color'], 1)
    return np.minimum(light, 1)


def vertex_light(points, normals, two_sided, base, lights, scale_glow=1.0):
    """Light (n, 3), 0 to 1, of a mesh's points (Unreal, with unit normals): `base` (the zone's ambient light and the
    actor's glow) plus, for each light, its colour times 2 (a fully lit texel's shadow weight) times its falloff and the
    incidence (|cos| where the point is two-sided, else only the lit side), each light at most 1 a channel, times
    ScaleGlow. As the lightmaps without shadows; the game lights meshes at their vertices (SurrealEngine's reading)."""
    light = np.broadcast_to(base, points.shape).astype(np.float64)
    for found in lights:
        to_light = found['location'] - points
        distance = np.maximum(np.linalg.norm(to_light, axis=-1), 1e-6)
        near = distance / found['radius']
        facing = np.einsum('ij,ij->i', to_light, normals) / distance
        facing = np.where(two_sided, np.abs(facing), np.maximum(facing, 0))
        strength = np.where(near < 1, np.minimum((1 + 2 * near ** 3 - 3 * near ** 2) / near, 1), 0) * facing
        light = light + scale_glow * np.minimum(2 * strength[:, None] * found['color'], 1)
    return np.minimum(light, 1)


def pack(sizes):
    """(atlas width, height, [(x, y) of each (w, h) size]): shelves of tiles, tallest first, each with a 1-texel
    border."""
    area = sum((w + 2) * (h + 2) for w, h in sizes)
    width = max(64, max((w + 2 for w, _ in sizes), default=0), 1 << math.ceil(math.log2(max(math.sqrt(area * 1.15), 1))))
    places, x, y, shelf = [None] * len(sizes), 0, 0, 0
    for n in sorted(range(len(sizes)), key=lambda n: -sizes[n][1]):
        w, h = sizes[n][0] + 2, sizes[n][1] + 2
        if x + w > width:
            x, y, shelf = 0, y + shelf, 0
        places[n] = x, y
        x, shelf = x + w, max(shelf, h)
    return width, y + shelf, places


def floor_below(floors, x, y, z, which=False):
    """Height of the highest of the upward triangles `floors` (n, 3, 3: Unreal points) under (x, y) below z, or None;
    `which`: (height, the triangle's index) or None."""
    if not len(floors):
        return None
    a, b, c = floors[:, 0], floors[:, 1], floors[:, 2]
    side = lambda p, q: (q[:, 0] - p[:, 0]) * (y - p[:, 1]) - (q[:, 1] - p[:, 1]) * (x - p[:, 0])
    d1, d2, d3 = side(a, b), side(b, c), side(c, a)
    normal = np.cross(b - a, c - a)
    hit = (((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))) & (np.abs(normal[:, 2]) > 1e-9)
    heights = a[hit, 2] - (normal[hit, 0] * (x - a[hit, 0]) + normal[hit, 1] * (y - a[hit, 1])) / normal[hit, 2]
    under = heights < z
    if not under.any():
        return None
    best = np.flatnonzero(hit)[under][np.argmax(heights[under])]
    return (float(heights[under].max()), int(best)) if which else float(heights[under].max())


def build_map(library, level, default):
    """(scene, geometry bytes, {png: image}, lightmap atlas image) of map package `level`."""
    found = level.find({'Level'})
    if not found:
        raise ValueError('no Level export')
    layout = level.level(found[0])
    model = level.model(layout['model'])
    points, vectors, verts = np.array(model['points']), np.array(model['vectors']), model['verts']
    info = next(iter(actors_of(level, layout['actors'], {'LevelInfo'}).values()), {})
    if info.get('DefaultTexture'):  # What the level draws where a surface has no texture; Engine's otherwise.
        default = level, info['DefaultTexture']
    zones, actors = {}, {}

    def actor(ref):
        """Placed actor `ref`'s properties over its class's defaults."""
        if ref not in actors:
            actors[ref] = {**library.class_properties(level.ref_path(level.exports[ref - 1]['cls'])), **level.properties(ref)}
        return actors[ref]

    def zone(index):
        """Properties of zone `index`'s ZoneInfo: the LevelInfo's for zone 0 or a zone without one."""
        if index not in zones:
            ref = model['zones'][index][0] if 0 < index < len(model['zones']) else 0
            ref = ref if ref > 0 else next((r for r in layout['actors'] if r > 0 and
                                            level.ref_name(level.exports[r - 1]['cls']) == 'LevelInfo'), 0)
            zones[index] = actor(ref) if ref > 0 else {}
        return zones[index]

    # ponytail: one sky for the whole map, as every zone of the install's maps links to the same one (227's per-zone
    # SkyZoneInfoTag is set nowhere); pick per zone if a map ever sets it.
    skies = list(actors_of(level, layout['actors'], {'SkyZoneInfo'}).values())
    skies = [props for props in skies if props.get('bHighDetail')] or skies
    sky = skies[-1] if skies else None

    textures, images, missing = {}, {}, []

    def texture_of(package, ref):
        """(png name, width, height, the texture's own poly flags) of texture reference `ref` in `package`; none draws
        the default texture."""
        key = package.path, ref
        if key not in textures:
            pick = (package, ref) if ref else default
            # Index 0 clear on every palette texture: where a surface is masked the game cuts it out (even where
            # the texture is not bMasked: the sky panoramas), and opaque surfaces ignore alpha.
            image = pick and library.texture(*pick, masked=True)
            if not image:
                textures[key] = ('missing.png', 64, 64, 0)
                images['missing.png'] = None
                missing.append('.'.join(pick[0].ref_path(pick[1])) if pick else 'Engine.DefaultTexture')
            else:
                package, at = library.resolve(*pick)
                props = package.properties(at)
                scale = props.get('DrawScale', 1.0) or 1.0
                png = safe(image[0]) + '.png'
                images[png] = image[1]
                own = props.get('PolyFlags', 0) | (MASKED if props.get('bMasked') else 0)
                textures[key] = (png, image[1].width * scale, image[1].height * scale, own)
        return textures[key]

    groups, flipped, polygons, floors, floor_lights = {}, 0, 0, [], []

    def listed(owner, entry):
        """The light actors lightmap `entry` of Model `owner` lists (what the editor found reaching it)."""
        out, k = set(), entry['lights']
        while 0 <= k < len(owner['lights']) and owner['lights'][k] != 0:
            out.add(owner['lights'][k])
            k += 1
        return frozenset(out)
    for node in model['nodes']:
        if node['count'] < 3 or not 0 <= node['surf'] < len(model['surfs']):
            continue
        surf = model['surfs'][node['surf']]
        if surf['flags'] & (INVISIBLE | PORTAL):
            continue
        png, width, height, own = texture_of(level, surf['texture'])
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
            reach = listed(model, model['lightmaps'][surf['lightmap']]) if 0 <= surf['lightmap'] < len(model['lightmaps']) else None
            floor_lights.extend([reach] * (len(corners) - 2))
        base, u, v = points[surf['base']], vectors[surf['u']], vectors[surf['v']]
        uv = np.stack([((corners - base) @ u + surf['pan'][0]) / width, ((corners - base) @ v + surf['pan'][1]) / height], 1)
        # Lightmap texels (s, t), texel i's centre at s = i + 0.5; unlit surfaces and those without a lightmap take
        # the texture as it is (the neutral tile, half of the 2x the game multiplies lightmaps by).
        tile, st = None, np.zeros((len(corners), 2))
        if not flags & UNLIT and 0 <= surf['lightmap'] < len(model['lightmaps']):
            entry = model['lightmaps'][surf['lightmap']]
            tile = surf['lightmap'], node['zones'][1]  # The front zone's ambient light.
            st = np.stack([((corners - base) @ u - entry['pan'][0]) / entry['scale'][0] + .5,
                           ((corners - base) @ v - entry['pan'][1]) / entry['scale'][1] + .5], 1)
        placed = viewer(corners)
        # Newell's normal of the corners in the page's axes, counter-clockwise; reverse them when it points away
        # from the surface's normal.
        nxt = np.roll(placed, -1, 0)
        newell = np.cross(placed, nxt).sum(0)
        if newell @ viewer([vectors[surf['normal']]])[0] < 0:
            placed, uv, st = placed[::-1], uv[::-1], st[::-1]
            flipped += 1
        polygons += 1
        groups.setdefault((png, flags & DRAWN_FLAGS, pan), []).append((placed, uv, st, tile, None))

    def ambient_of(number):
        found = zone(number)
        return light_color(found.get('AmbientHue', 0), found.get('AmbientSaturation', 255), found.get('AmbientBrightness', 0))

    # The lightmaps, rebuilt from the lights and shadow bits the map keeps: None is the neutral tile (the texture as it
    # is), 'white' the one meshes take (their light is in their vertex colours).
    lights = ['end' if ref == 0 else ref > 0 and light_of(actor(ref)) or None for ref in model['lights']]
    surf_of = {s['lightmap']: s for s in reversed(model['surfs'])}
    tiles = {None: np.full((1, 1, 3), .5), 'white': np.ones((1, 1, 3))}
    for index, front in sorted({record[3] for polys in groups.values() for record in polys if record[3]}):
        surf = surf_of[index]
        tiles[index, front] = lightmap(model, model['lightmaps'][index], points[surf['base']], vectors[surf['u']],
                                       vectors[surf['v']], vectors[surf['normal']], ambient_of(front), lights)

    # Placed actors: movers at their start key, and actors drawn as meshes. The game finds an actor's zone as it
    # starts (the saved Region is often stale): down the BSP to the leaf its location falls in.
    nodes = model['nodes']

    def zone_at(point):
        index = 0
        while nodes:
            node = nodes[index]
            front = int(np.dot(node['plane'][:3], point) >= node['plane'][3])
            following = (node['back'], node['front'])[front]
            if following < 0:
                return node['zones'][front]
            index = following
        return 0

    static_lights = {ref: found for ref in sorted({r for r in model['lights'] if r > 0}) for found in [light_of(actor(ref))] if found}
    floor_array = None

    def surface_flags(flags, own):
        """A surface's poly flags as drawn: the texture's own added, translucent dropping masked; None when invisible."""
        flags |= own
        if flags & (INVISIBLE | PORTAL):
            return None
        if flags & TRANSLUCENT:
            flags &= ~MASKED
        return flags if sky else flags & ~FAKE_BACKDROP

    def add_mover(ref, props):
        """A mover's brush polygons where it starts (BasePos + KeyPos[KeyNum], BaseRot + KeyRot[KeyNum], as Mover's
        BeginPlay puts it): Location + Rotation x MainScale x (point - PrePivot). Its lightmaps are the brush Model's,
        one per brush poly, lit where the editor raytraced it (key BrushRaytraceKey)."""
        brush = level.model(props['Brush'])
        found = level.polys(brush['polys']) if brush['polys'] > 0 else []
        raw = props.get('MainScale')
        scale = np.diag(struct.unpack('<3f', raw[:12]) if isinstance(raw, bytes) and len(raw) >= 12 else (1, 1, 1))
        pivot = np.array(vector(props, 'PrePivot'))

        def pose(key):
            name = lambda base: base if key == 0 else f'{base}[{key}]'
            if 'BasePos' not in props:
                location, rotator = vector(props, 'Location'), vector(props, 'Rotation', '3i')
            else:
                location = np.add(vector(props, 'BasePos'), vector(props, name('KeyPos')))
                rotator = np.add(vector(props, 'BaseRot', '3i'), vector(props, name('KeyRot'), '3i'))
            matrix = np.array(rotation(*rotator)) @ scale
            return matrix, np.asarray(location, np.float64) - matrix @ pivot

        key = props.get('KeyNum', 0)
        if props.get('InitialState') == 'TriggerToggle' and str(props.get('Tag')).lower() in spawn_events:
            key = props.get('NumKeys', 2) - 1  # Toggled open by the player landing at the start, and stays open.
        start, traced = pose(key), pose(props.get('BrushRaytraceKey', 0))
        number = zone_at(start[1] + start[0] @ pivot)
        front = zone(number)
        brush_lights = ['end' if r == 0 else r > 0 and light_of(actor(r)) or None for r in brush['lights']]
        normal_matrix = np.linalg.inv(start[0]).T
        for poly in found:
            png, width, height, own = texture_of(level, poly['texture'])
            flags = surface_flags(poly['flags'], own)
            if flags is None or len(poly['points']) < 3:
                continue
            pan = (0, 0)
            if flags & (AUTO_U_PAN | AUTO_V_PAN):
                pan = (round(64 * front.get('TexUPanSpeed', 1.0) / width, 6) if flags & AUTO_U_PAN else 0,
                       round(64 * front.get('TexVPanSpeed', 1.0) / height, 6) if flags & AUTO_V_PAN else 0)
            local, base = np.array(poly['points']), np.array(poly['base'])
            u, v, normal = np.array(poly['u']), np.array(poly['v']), np.array(poly['normal'])
            uv = np.stack([((local - base) @ u + poly['pan'][0]) / width, ((local - base) @ v + poly['pan'][1]) / height], 1)
            tile, st = None, np.zeros((len(local), 2))
            if not flags & UNLIT and 0 <= poly['brush_poly'] < len(brush['lightmaps']):
                entry = brush['lightmaps'][poly['brush_poly']]
                tile = 'mover', ref, poly['brush_poly']
                if tile not in tiles:
                    tiles[tile] = lightmap(brush, entry, base, u, v, normal, ambient_of(number), brush_lights, traced)
                st = np.stack([((local - base) @ u - entry['pan'][0]) / entry['scale'][0] + .5,
                               ((local - base) @ v - entry['pan'][1]) / entry['scale'][1] + .5], 1)
            world = local @ start[0].T + start[1]
            facing = normal_matrix @ normal
            if not flags & NOT_SOLID and facing[2] > 0:
                floors.extend(world[[0, k, k + 1]] for k in range(1, len(world) - 1))
                reach = frozenset(r for r in brush['lights'] if r > 0) or None
                floor_lights.extend([reach] * (len(world) - 2))
            placed = viewer(world)
            if np.cross(placed, np.roll(placed, -1, 0)).sum(0) @ viewer([facing])[0] < 0:
                placed, uv, st = placed[::-1], uv[::-1], st[::-1]
            groups.setdefault((png, flags & DRAWN_FLAGS, pan), []).append((placed, uv, st, tile, None))

    def add_mesh(props, obj):
        """An actor drawn as a mesh, at Location + PrePivot + Rotation x DrawScale x the mesh's points (its
        AnimSequence's frame), each slot's texture as the game picks it, lit at its vertices; False when it shows nothing."""
        package, ref = obj('Mesh')
        if package.ref_name(package.exports[ref - 1]['cls']) not in ('Mesh', 'LodMesh', 'SkeletalMesh'):
            counts['unread'] += 1  # 227's StaticMesh (DmRiot) is not decoded.
            return False
        key = package.path, ref
        if key not in meshes:
            meshes[key] = package.mesh(ref)
        mesh = meshes[key]
        seqs = {seq['name'].lower(): seq for seq in mesh['seqs']}
        seq = seqs.get(str(props.get('AnimSequence', '')).lower()) or next((seqs[n] for n in POSES if n in seqs), None)
        frame = seq['start'] + min(int(props.get('AnimFrame', 0.0) * seq['frames']), max(seq['frames'] - 1, 0)) if seq else 0
        local = np.array(mesh_points(mesh, frame if frame < mesh['frames'] else 0), np.float64)
        matrix = np.array(rotation(*vector(props, 'Rotation', '3i'))) * props.get('DrawScale', 1.0)
        world = local @ matrix.T + np.add(vector(props, 'Location'), vector(props, 'PrePivot'))
        style = STYLE_FLAGS.get(props.get('Style', 1), 0)

        def slot(index):
            """The game's pick for texture slot `index`: MultiSkins, then Skin (slot 0, or a slot the mesh leaves
            empty), the mesh's own, Texture (not Engine's editor icons), the last MultiSkins set, Engine's default."""
            own = mesh['textures'][index] if index < len(mesh['textures']) else 0
            texture = obj('Texture')
            picks = [obj('MultiSkins' if index == 0 else f'MultiSkins[{index}]'),
                     obj('Skin') if index == 0 or not own else None, own and library.resolve(package, own),
                     texture if texture and texture[0].path.stem.lower() != 'engine' else None]
            picks += [obj('MultiSkins' if j == 0 else f'MultiSkins[{j}]') for j in reversed(range(len(mesh['textures'])))]
            return next((pick for pick in picks if pick), default)

        faces = []
        for a, b, c, (face_flags, index) in mesh_faces(mesh):
            if max(a[0], b[0], c[0]) >= len(world):
                continue
            pick = slot(index)
            png, _, _, own = texture_of(*pick) if pick else texture_of(level, 0)
            flags = surface_flags(face_flags | style, own)
            if flags is not None:
                faces.append((a, b, c, png, flags & ~FAKE_BACKDROP))
        if not faces:
            return False
        # Normals from the faces, turned outward (Unreal's axes are left-handed, so the corners' order says little).
        corners = np.array([[a[0], b[0], c[0]] for a, b, c, _, _ in faces])
        face_normals = np.cross(world[corners[:, 1]] - world[corners[:, 0]], world[corners[:, 2]] - world[corners[:, 0]])
        middle = world.mean(0)
        if np.einsum('ij,ij->', face_normals, world[corners].mean(1) - middle) < 0:
            face_normals = -face_normals
        normals = np.zeros_like(world)
        two_sided = np.zeros(len(world), bool)
        for k in range(3):
            np.add.at(normals, corners[:, k], face_normals)
            np.logical_or.at(two_sided, corners[:, k], np.array([f & TWO_SIDED > 0 for *_, f in faces]))
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
        number = zone_at(world.mean(0))
        if props.get('bUnlit') or number == 0:  # The game draws an actor outside every zone unlit.
            light = np.full((len(world), 3), .5)
        else:
            glow = props.get('AmbientGlow', 0)
            glow = .3 if glow == 255 else glow / 255  # 255 pulses, 0.3 on average (SurrealEngine's reading).
            reach = np.linalg.norm(world - middle, axis=1).max()
            # ponytail: the game lights actors only with the lights that reach them; the editor's light list of the
            # floor surface beneath stands in for that (MarineBox6 on Glathriel2 took half its light through walls);
            # with no floor below, every light in range. Per-vertex visibility would need line checks.
            below = floor_below(floor_array, *middle, which=True)
            reaching = floor_lights[below[1]] if below else None
            near = [found for ref, found in static_lights.items() if (reaching is None or ref in reaching)
                    and np.linalg.norm(found['location'] - middle) < found['radius'] + reach]
            light = vertex_light(world, normals, two_sided, ambient_of(number) + glow, near, props.get('ScaleGlow', 1.0))
        batches = {}
        for a, b, c, png, flags in faces:
            batch = batches.setdefault((png, flags & DRAWN_FLAGS, (0, 0)), ({}, []))
            for corner in (a, c, b):  # Unreal's corners wind clockwise seen from the front.
                key = corner + (bool(flags & UNLIT),)
                batch[1].append(batch[0].setdefault(key, len(batch[0])))
        for group, (corners, triangles) in batches.items():
            keys = list(corners)
            ids = [key[0] for key in keys]
            color = np.where(np.array([key[3] for key in keys])[:, None], .5, light[ids])
            uv = np.array([(key[1] / 255, key[2] / 255) for key in keys])
            groups.setdefault(group, []).append((viewer(world[ids]), uv, np.zeros((len(keys), 2)), 'white', color, triangles))
        return True

    def events_at_spawn():
        """Events of the active proximity Triggers a player touches on landing below a PlayerStart (SpireVillage's
        arrival force field and Nalic2's and VeloraEnd's arrival doors open so, checked in 227 on SpireVillage)."""
        found = {}
        for ref in layout['actors']:
            if ref > 0:
                info = library.class_info(level.ref_path(level.exports[ref - 1]['cls']))
                found[ref] = info, {**info[0], **level.properties(ref)}
        starts = [np.array(vector(props, 'Location')) for (_, _, names), props in found.values() if 'PlayerStart' in names]
        events = set()
        for (_, _, names), props in found.values():
            if 'Trigger' in names and props.get('Event') and props.get('TriggerType', 0) in (0, 1) and props.get('bInitiallyActive', True):
                d = np.array(vector(props, 'Location'))
                radius, height = props.get('CollisionRadius', 40), props.get('CollisionHeight', 40)
                if any(np.hypot(*(s - d)[:2]) < radius + RADIUS and -height - HEIGHT < (s - d)[2] < height + HEIGHT + MAX_DROP for s in starts):
                    events.add(str(props['Event']).lower())
        return events

    spawn_events = events_at_spawn()
    meshes, counts = {}, dict(meshes=0, movers=0, unread=0, failed=[])
    def placed_order():
        movers = [r for r in layout['actors'] if r > 0 and 'Mover' in library.class_info(level.ref_path(level.exports[r - 1]['cls']))[2]]
        return movers + [r for r in layout['actors'] if r > 0 and r not in set(movers)]

    for ref in placed_order():
        if floor_array is None and 'Mover' not in library.class_info(level.ref_path(level.exports[ref - 1]['cls']))[2]:
            floor_array = np.array(floors).reshape(-1, 3, 3)
        defaults, sources, names = library.class_info(level.ref_path(level.exports[ref - 1]['cls']))
        placed = level.properties(ref)
        props = {**defaults, **placed}
        if props.get('bHidden'):
            continue

        def obj(name):
            """(package, export) of object property `name` (the map's, or the class default's package), or None."""
            value, package = props.get(name), level if name in placed else sources.get(name)
            return library.resolve(package, value) if isinstance(value, int) and value and package else None

        name = level.exports[ref - 1]['name']
        try:
            if 'Mover' in names and isinstance(props.get('Brush'), int) and props['Brush'] > 0:
                add_mover(ref, props)
                counts['movers'] += 1
            elif props.get('DrawType') == 2 and obj('Mesh') and add_mesh(props, obj):
                counts['meshes'] += 1
        except (struct.error, ValueError, IndexError, KeyError) as exc:
            counts['failed'].append(f'{name}: {exc}')

    atlas_keys = list(tiles)
    atlas_width, atlas_height, places = pack([(tiles[k].shape[1], tiles[k].shape[0]) for k in atlas_keys])
    atlas = np.zeros((atlas_height, atlas_width, 3))
    for (x, y), key in zip(places, atlas_keys):
        t = tiles[key]
        atlas[y:y + t.shape[0] + 2, x:x + t.shape[1] + 2] = np.pad(t, ((1, 1), (1, 1), (0, 0)), mode='edge')
    corner = {key: (x + 1, y + 1) for key, (x, y) in zip(atlas_keys, places)}

    positions, uvs, uv2s, colors, indices, table = [], [], [], [], [], []
    count = 0
    for (png, flags, pan), polys in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
        start = len(indices)
        for placed, uv, st, tile, color, *triangles in polys:
            n = len(placed)
            positions.append(placed)
            uvs.append(uv)
            if tile is None or tile == 'white':  # One texel: its centre.
                st = np.full((n, 2), .5)
            uv2s.append((st + corner[tile]) / (atlas_width, atlas_height))
            colors.append(np.full((n, 3), 1.0) if color is None else color)
            if triangles:  # A mesh's batch: its own triangles over shared corners.
                indices.extend(count + x for x in triangles[0])
            else:  # A convex polygon: a fan.
                indices.extend(x for k in range(1, n - 1) for x in (count, count + k, count + k + 1))
            count += n
        table.append(dict(texture=png, flags=flags, start=start, count=len(indices) - start, **({'pan': pan} if any(pan) else {})))
    positions = np.concatenate(positions).astype('<f4') if positions else np.zeros((0, 3), '<f4')
    uvs = np.concatenate(uvs).astype('<f4') if uvs else np.zeros((0, 2), '<f4')
    uv2s = np.concatenate(uv2s).astype('<f4') if uv2s else np.zeros((0, 2), '<f4')
    colors = np.concatenate(colors) if colors else np.zeros((0, 3))
    colors = np.concatenate([np.round(colors * 255), np.full((len(colors), 1), 255)], 1).astype(np.uint8)
    geometry = positions.tobytes() + uvs.tobytes() + uv2s.tobytes() + colors.tobytes() + np.asarray(indices, '<u4').tobytes()
    atlas = Image.fromarray(np.round(atlas * 255).astype(np.uint8), 'RGB')

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
                 vertices=len(positions), indices=len(indices), lightmap=[atlas_width, atlas_height], colors=True,
                 actors=counts, groups=table, viewpoints=views, sky=sky,
                 polygons=polygons, flipped=flipped, missing=sorted(missing))
    return scene, geometry, images, atlas


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
            scene, geometry, images, atlas = build_map(library, Package(path), default)
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
        atlas.save(folder / 'lightmap.png')
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

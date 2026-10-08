"""Earthsiege 1 and 2 (Dynamix 3Space 2.0, 1994-96): an install's shapes into the model browser as games es1 and es2.

python tools/import_earthsiege.py --game es1 --install "C:/Games/Earthsiege"

Read from the install's VOLN volumes (*.vol, every entry stored; SIMPATCH volumes win a name clash):

- Shapes (.DTS): tagged objects, each tag (u16 type, u16 class), u32 size. Root 0 is the finest of a shape's levels
  of detail and is the one imported; a detail part shows its finest level and a cell-animation part its first cell.
  Groups are drawn through their node's rest pose: the ANAnimList's default transforms composed up its relation
  list (euler shorts in z, x, y order, then int16 translation). Points are int16; a poly names its corners through
  the group's index list and its normal as one more point, and its corners wind against that normal.
- Colours: a poly's colour field is a dword index into its group's surface table: front fill, front line, back fill,
  back line. A Texture4Poly's value is a frame of the shape's texture bank (-1 none: a front of -1 draws the back
  frame from the front too), its frame's corners in vertex order; a SolidPoly's a palette index; a Shaded or
  Gouraud poly's a shade ramp of the palette (.DPL: colours, then int32 ramp count and {int16 length,
  int16 index[length]} darkest first). A side whose fill and line both carry 0x14 in their top byte is not drawn.
- Texture banks: the u16 at offset 0x94 of the shape's .DAT picks one (BANKS); debris (XXXX_DEB) takes its chassis'.
- Weapons: a chassis' .GL lists its hardpoints. A drawn one's part is a placeholder, left out; an ES2 player chassis
  shows its shell stock fit there instead (STOCK_FITS), each weapon the MECHWPNS.DTS root its sim WEAPONS.DAT
  template names for the hardpoint's mounting code, in the WPNTEX bank.

Sources: disassembly of Earthsiege 1's DBSIM.EXE (texture poly render 0x5A1E8, solid 0x5A118, bank names at
0x989A0) and Kevin Foley's Herculan documentation of Earthsiege 2 (github.com/kevinfoley/Herculan,
docs/retail/formats/dts-texture-binding.md, mech-shape-drawing.md, dts-node-posing.md, gun-layout-gl.md,
weapons-dat-sim.md, herc-catalogs.md and simulation/weapon-mounts.md; MIT).

Written (under local-data/<game>): catalog.json, model_json/MODEL.json and textures/*.png (frames as BANK_NN.png,
flat colours as rgb_rrggbb.png; kept when already there, so edits survive).
"""
import argparse
import json
import math
from pathlib import Path
import struct

from PIL import Image

try:
    from tools.import_diabotical_models import safe
    from tools.local_data import LOCAL_DATA
except ImportError:  # Run as a script from tools/.
    from import_diabotical_models import safe
    from local_data import LOCAL_DATA

# Texture banks by the .DAT selector: ES1 from DBSIM.EXE's name table, ES2 from Herculan's MechType_InitOne.
BANKS = {'es1': ('light', 'medium', 'heavy', 'enemy', 'apoco'),
         'es2': ('light', 'medium', 'heavy', 'enemy', 'apocatex', 'razortex', 'newhercs')}
# ES1 draws a frame inset 3 texels (its bitmap brush); ES2 its whole atlas rectangle.
INSET = {'es1': 3, 'es2': 0}
PALETTE = 'WORLD0.DPL'  # ponytail: one theatre's palette; each WORLDn.DPL tints the banks and ramps its own way.
SCALE = 0.006  # Herculan: a 200,000-unit map grid square is 1200 m.
# ponytail: one fixed light level (0..255) for ramp colours; the game lights each face from the sun.
SHADE = 192
CATEGORY = {3: 'Cybrids'}  # Any other bank: HERCs.
# Shapes with no .DAT selector. Herculan: every flyer draws from ENEMY. ponytail: the structure and weapon banks are
# the ones whose frame counts fit (BASETEX, WPNTEX); BASES.DAT's per-type selector is not read.
FIXED_BANKS = {'SKIMMER': 'enemy', 'BASES_AE': 'basetex', 'BASES_AN': 'basetex', 'MECHWPNS': 'wpntex', 'MECHWPN2': 'wpntex'}
# Earthsiege 2's player chassis and the shell's stock fit for each (Herculan, herc-catalogs.md: SHELL0 GAM\INI_*.DAT,
# types 0-8). Cybrid fits live in the missions and Earthsiege 1's source is not found, so those show bare hardpoints.
STOCK_FITS = {'OUTLAW': 'INI_OUTL.DAT', 'RAPTOR2': 'INI_RAPT.DAT', 'TOMAHAWK': 'INI_TOMA.DAT', 'SAMSON': 'INI_SAMS.DAT',
              'COLOSSUS': 'INI_COLO.DAT', 'APOCA': 'INI_APOC.DAT', 'OGRE': 'INI_OGRE.DAT', 'MAVERICK': 'INI_MAVR.DAT',
              'RAZOR': 'INI_RAZR.DAT'}
TAGS = {0x08: 'shape', 0x07: 'part_list', 0x05: 'base_part', 0x15: 'bsp_part', 0x0b: 'cell_anim_part',
        0x0c: 'detail_part', 0x13: 'bitmap_part', 0x14: 'group', 0x0a: 'bsp_group', 0x01: 'poly', 0x02: 'solid_poly',
        0x03: 'shaded_poly', 0x09: 'gouraud_poly', 0x0f: 'texture_poly', 0x10: 'solid_poly', 0x11: 'shaded_poly',
        0x12: 'gouraud_poly', 0x0e: 'null'}  # Class 0x14; 0x10-0x12 are the alias variants.
PART_LISTS = {'part_list', 'bsp_part', 'cell_anim_part', 'detail_part', 'shape', 'an_shape'}
HIDDEN = 0x14


def read_volumes(install):
    """Every stored entry of the install's VOLN volumes as {NAME: bytes} and {FOLDER/NAME: bytes} (ES2 has a shell
    GAM/WEAPONS.DAT and a sim DAT/WEAPONS.DAT); SIMPATCH volumes first, so they win."""
    volumes = sorted((p for p in Path(install).rglob('*') if p.suffix.lower() == '.vol' and p.is_file()),
                     key=lambda p: ('patch' not in p.name.lower(), str(p).lower()))
    files = {}
    for volume in volumes:
        data = volume.read_bytes()
        if data[:4] != b'VOLN':
            continue
        chars = struct.unpack_from('<H', data, 10)[0]
        folders = data[12:12 + chars].decode('latin1').upper().split('\0')
        at = 12 + chars
        count = struct.unpack_from('<H', data, at)[0]
        at += 6
        for _ in range(count):
            name = data[at:at + 13].split(b'\0')[0].decode('latin1').upper()
            folder = folders[data[at + 13]].strip('\\') if data[at + 13] < len(folders) else ''
            offset = struct.unpack_from('<I', data, at + 14)[0]
            at += 18
            kind, size = struct.unpack_from('<BI', data, offset)
            if kind == 2:  # Herculan: every retail entry is stored (type 2).
                entry = data[offset + 9:offset + 9 + size]
                files.setdefault(name, entry)
                files.setdefault(f'{folder}/{name}', entry)
    return files


class Reader:
    def __init__(self, data):
        self.data, self.at = data, 0

    def read(self, fmt):
        values = struct.unpack_from('<' + fmt, self.data, self.at)
        self.at += struct.calcsize('<' + fmt)
        return values


def read_object(reader):
    """One tagged object and its children; every object carries its size, so reading resyncs on it."""
    kind, family, size = reader.read('HHI')
    start = reader.at
    name = 'an_shape' if (kind, family) == (3, 0x1e) else 'an_anim_list' if (kind, family) == (2, 0x1e) else \
        TAGS.get(kind, '?') if family == 0x14 else '?'
    node = dict(type=name, children=[])
    if name in PART_LISTS or name in ('base_part', 'group', 'bsp_group', 'bitmap_part'):
        node['transform'], node['id'] = reader.read('hhh3h')[:2]
    if name in PART_LISTS:
        node['children'] = [read_object(reader) for _ in range(reader.read('H')[0])]
        if name == 'bsp_part':  # Nodes: int16 normal[3], int32 constant, int16 front, back (a child when & 0x4000).
            count = reader.read('h')[0]
            node['nodes'] = [reader.read('3hihh')[4:] for _ in range(count)]
        if name in ('shape', 'an_shape'):
            transforms, sequences = reader.read('HH')
            reader.read(f'{sequences + transforms}h')
            while reader.at < start + size:
                node['children'].append(read_object(reader))
    elif name in ('group', 'bsp_group'):
        indexes, points, colours, items = reader.read('HHHH')
        node['indexes'] = reader.read(f'{indexes}H')
        flat = reader.read(f'{points * 3}h')
        node['points'] = [flat[i:i + 3] for i in range(0, len(flat), 3)]
        node['surfaces'] = reader.read(f'{colours}i')
        node['children'] = [read_object(reader) for _ in range(items)]
    elif name.endswith('poly'):
        node['normal'], _, node['count'], node['first'] = reader.read('HHHH')
        node['colour'] = reader.read('H')[0] if name != 'poly' else None
    elif name == 'an_anim_list':
        for _ in range(reader.read('h')[0]):
            reader.read('HHIhhh')
            frames = reader.read('h')[0]
            reader.read(f'{frames * 3}H')
            parts = reader.read('H')[0]
            reader.read(f'{parts + frames * parts}H')
        reader.read(f'{reader.read("h")[0] * 4}H')
        flat = reader.read(f'{reader.read("h")[0] * 6}h')
        node['transforms'] = [flat[i:i + 6] for i in range(0, len(flat), 6)]
        node['defaults'] = reader.read(f'{reader.read("h")[0]}H')
        flat = reader.read(f'{reader.read("h")[0] * 2}h')
        node['parents'] = {flat[i + 1]: flat[i] for i in range(0, len(flat), 2)}
    reader.at = start + size
    return node


def rotation(record):
    """A transform record's euler shorts (full turn 0x10000) as a matrix, applied in z, x, y order."""
    matrix = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for axis in (2, 0, 1):
        angle = (record[axis] & 0xffff) / 0x10000 * 2 * math.pi
        c, s = math.cos(angle), math.sin(angle)
        turn = {0: [[1, 0, 0], [0, c, -s], [0, s, c]], 1: [[c, 0, s], [0, 1, 0], [-s, 0, c]],
                2: [[c, -s, 0], [s, c, 0], [0, 0, 1]]}[axis]
        matrix = [[sum(matrix[i][k] * turn[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return matrix


def apply(matrix, vector):
    return [sum(matrix[i][k] * vector[k] for k in range(3)) for i in range(3)]


def rest_pose(root):
    """{node: (rotation, translation)} in the shape's frame from the ANAnimList's default transforms."""
    anim = next((child for child in root['children'] if child['type'] == 'an_anim_list'), None)
    world = {}
    if anim is None:
        return world

    def place(node, depth=0):
        if node not in world:
            record = anim['transforms'][anim['defaults'][node]]
            matrix, offset = rotation(record), list(record[3:])
            parent = anim['parents'].get(node, -1)
            if 0 <= parent < len(anim['defaults']) and depth < 64:
                above, shift = place(parent, depth + 1)
                matrix = [[sum(above[i][k] * matrix[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
                offset = [a + b for a, b in zip(apply(above, offset), shift)]
            world[node] = matrix, offset
        return world[node]

    for node in range(len(anim['defaults'])):
        if anim['defaults'][node] < len(anim['transforms']):
            place(node)
    return world


def reached(part):
    """The children of a BSP part its tree reaches from node 0, in file order; the game draws no other
    (Herculan, dts-texture-binding.md, "TSBSPPart child selection")."""
    nodes, seen, found, pending = part.get('nodes', []), set(), set(), [0]
    while pending:
        index = pending.pop()
        if not 0 <= index < len(nodes) or index in seen:
            continue
        seen.add(index)
        for link in nodes[index]:
            if link >= 0 and link & 0x4000:
                found.add(link & 0x3fff)
            elif link >= 0:
                pending.append(link)
    return sorted(i for i in found if i < len(part['children']))


def groups_of(node, out, hardpoints=()):
    """The groups drawn at full detail: a detail part's finest (last) level, a cell-animation part's first cell; not
    the parts whose id is a visible hardpoint, placeholders the game swaps for the fitted weapon's shape."""
    if node['type'] in ('group', 'bsp_group'):
        out.append(node)
        return out
    children = [child for child in node['children'] if child['type'] != 'an_anim_list' and child.get('id') not in hardpoints]
    if node['type'] == 'bsp_part':
        children = [node['children'][i] for i in reached(node) if node['children'][i] in children]
    elif node['type'] == 'detail_part':
        children = children[-1:]
    elif node['type'] == 'cell_anim_part':
        children = children[:1]
    for child in children:
        groups_of(child, out, hardpoints)
    return out


def palette(data):
    """A .DPL's colours (6-bit, as 8-bit RGB) and its shade ramps."""
    count = struct.unpack_from('<I', data, 8)[0]
    colours = [tuple(min(255, c * 255 // 63) for c in data[12 + i * 4:15 + i * 4]) for i in range(count)]
    reader = Reader(data)
    reader.at = 12 + count * 4
    ramps = []
    for _ in range(reader.read('i')[0]):
        ramps.append(reader.read(f'{reader.read("h")[0]}h'))
    return colours, ramps


def frames(data):
    """A .DBA's frames as (width, height, palette indexes)."""
    count = struct.unpack_from('<I', data, 8)[0]
    at, out = 12, []
    for _ in range(count):
        size, height, width, _, _, _, pixels = struct.unpack_from('<IHHBBBI', data, at + 4)
        out.append((width, height, data[at + 21:at + 21 + pixels]))
        at += 8 + size
        at += (at - 12) % 2  # Objects are padded to an even size.
    return out


def surface_colour(value, kind, colours, ramps):
    index = value & 0xffff
    if kind != 'solid_poly':  # Shaded and Gouraud: a ramp, lit at SHADE.
        ramp = ramps[index & 0xff] if (index & 0xff) < len(ramps) else ramps[0]
        index = ramp[min(SHADE * len(ramp) >> 8, len(ramp) - 1)]
    return colours[index & 0xff]


def part_transforms(node, out):
    """{part id: transform} over a shape, first part of an id winning (Herculan, ShapeAnimation.CollectPartTransforms)."""
    if node.get('transform', -1) >= 0:
        out.setdefault(node['id'], node['transform'])
    for child in node['children']:
        part_transforms(child, out)
    return out


def build_model(root, game, bank, colours, ramps, textures, hardpoints=(), weapons=()):
    """Geometry, UVs and material slots of a shape's root 0; slot names are PNGs written to `textures`.
    `weapons` are (weapon root, its bank, bone part id, mount point): each drawn at the posed bone, its points moved by
    the mount point and its own transforms ignored, as the game stamps the bone's transform onto every weapon part."""
    pose = rest_pose(root)
    corners = INSET[game]
    vertices, normals, uvs, slots, skipped = [], [], [], {}, 0
    identity = ([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0])
    bones = part_transforms(root, {}) if weapons else {}
    drawn = [(group, pose.get(group['transform'], identity), bank, (0, 0, 0)) for group in groups_of(root, [], hardpoints)]
    for weapon, weapon_bank, bone, mount in weapons:
        drawn += [(group, pose.get(bones.get(bone), identity), weapon_bank, mount) for group in groups_of(weapon, [])]
    for group, (matrix, offset), bank, mount in drawn:
        points, surfaces = group['points'], group['surfaces']
        for poly in group['children']:
            kind = poly['type']
            if not kind.endswith('poly') or kind == 'poly' or poly['count'] < 3:
                continue  # Plain polys carry no colour; one and two corners are a pixel or a line.
            ring = [group['indexes'][i] for i in range(poly['first'], poly['first'] + poly['count'])]
            colour = poly['colour']
            if colour + 1 >= len(surfaces) or max(ring) >= len(points) or poly['normal'] >= len(points):
                skipped += 1
                continue
            sides = []
            if kind == 'texture_poly':
                front = surfaces[colour]
                back = surfaces[colour + 2] if colour + 2 < len(surfaces) else -1
                if front != -1 or back != -1:
                    sides.append(front if front != -1 else back)
                if back != -1 and front != -1:
                    sides.append(back)
            else:
                for at in (colour, colour + 2):
                    pair = surfaces[at:at + 2]
                    sides.append(None if len(pair) < 2 or all(v >> 24 & 0xff == HIDDEN for v in pair) else pair[0])
                if sides[0] is None and sides[1] is None:
                    continue
            normal = apply(matrix, points[poly['normal']])
            corner_points = [[a + b for a, b in zip(apply(matrix, [p + m for p, m in zip(points[i], mount)]), offset)] for i in ring]
            for side, value in enumerate(sides):
                if value is None:
                    continue
                if kind == 'texture_poly':
                    if bank is None or not 0 <= value < len(bank[1]) or poly['count'] > 4:
                        skipped += 1
                        continue
                    width, height, _ = bank[1][value]
                    slot = f'{bank[0]}_{value:02d}.png'
                    inset_u, inset_v = corners / width, corners / height
                    square = [(inset_u, inset_v), (1 - inset_u, inset_v), (1 - inset_u, 1 - inset_v), (inset_u, 1 - inset_v)]
                    if slot not in textures:
                        textures[slot] = ('frame', bank, value)
                else:
                    slot = 'rgb_%02x%02x%02x.png' % surface_colour(value, kind, colours, ramps)
                    square = [(0.5, 0.5)] * len(ring)
                    textures.setdefault(slot, ('colour', slot))
                order = list(range(len(ring)))
                # The corners wind against the stored normal: reversed, a front side winds anticlockwise.
                order = order[::-1] if side == 0 else order
                facing = [-c for c in normal] if side else normal
                length = math.hypot(*facing) or 1
                triangles = slots.setdefault(slot, [])
                for k in range(1, len(order) - 1):
                    for corner in (order[0], order[k], order[k + 1]):
                        x, y, z = corner_points[corner]
                        triangles.append(len(vertices) // 3)
                        vertices.extend((-x * SCALE, z * SCALE, y * SCALE))  # Z-up, +y forward -> y-up, +z forward.
                        normals.extend((-facing[0] / length, facing[2] / length, facing[1] / length))
                        uvs.extend(square[corner])
    # ponytail: each textured quad is two affine triangles; the game maps the whole quad screen-linearly.
    names = sorted(slots)
    indices, groups = [], []
    for number, name in enumerate(names):
        groups.append(dict(start=len(indices), count=len(slots[name]), materialIndex=number))
        indices.extend(slots[name])
    return dict(game=game, winding='ccw', vertices=[round(v, 4) for v in vertices], normals=[round(v, 4) for v in normals],
                uvs=[round(v, 5) for v in uvs], indices=indices, groups=groups, material_names=names,
                material_textures=names), skipped


def import_catalog(install, output, game):
    """Import every shape of the Earthsiege (game es1) or Earthsiege 2 (es2) install `install` into `output`.
    Returns counts of entries, previews and polys not drawn for want of a frame."""
    if game not in BANKS:
        raise ValueError(f'Unknown Earthsiege game {game}')
    install, output = Path(install).expanduser(), Path(output)
    files = read_volumes(install) if install.is_dir() else {}
    shapes = sorted(name for name in files if name.endswith('.DTS') and '/' not in name)
    if not shapes or PALETTE not in files:
        raise ValueError(f'No Earthsiege volumes (.vol with shapes and {PALETTE}) in {install}')
    colours, ramps = palette(files[PALETTE])
    banks = {}

    def bank(name):
        if name is None:
            return None
        if name not in banks:
            banks[name] = (name, frames(files[name.upper() + '.DBA'])) if name.upper() + '.DBA' in files else None
        return banks[name]

    def hardpoints(stem):
        """Herculan (gun-layout-gl.md, mech-shape-drawing.md): a chassis' .GL is int16 count, 26-byte records of
        int16 part id (bone), at +6 the mounting code (below 4 drawn, so the bone's part is a placeholder), at +0x10
        the mount point and at +0x17 the fit slot. As (bone, code, mount point, slot), the drawn ones only."""
        data = files.get(stem + '.GL', b'')
        count = struct.unpack_from('<h', data)[0] if len(data) >= 2 else 0
        records = [struct.unpack_from('<h4xB9x3h1xB', data, 2 + 26 * i) for i in range(count) if 2 + 26 * (i + 1) <= len(data)]
        return [(r[0], r[1], r[2:5], r[5]) for r in records if r[1] < 4]

    # The weapon each fit slot of a player chassis' stock fit carries, drawn as the MECHWPNS.DTS root its sim
    # template names for the hardpoint's mounting code (Herculan: herc-catalogs.md, weapons-dat-sim.md).
    templates, weapon_roots = [], []
    if 'DAT/WEAPONS.DAT' in files and 'MECHWPNS.DTS' in files and any(STOCK_FITS.get(s[:-4]) in files for s in shapes):
        table, at = files['DAT/WEAPONS.DAT'], 2
        for _ in range(struct.unpack_from('<H', table)[0]):  # A .DMG piece, a .COL cluster, then a 48-byte tail.
            at += 8 + 4 * max(struct.unpack_from('<h', table, at + 6)[0], 0)
            spheres = struct.unpack_from('<h', table, at + 2)[0]
            at += 4 + (8 * spheres if spheres & 0x1fff else 0)
            templates.append(struct.unpack_from('<4h', table, at))
            at += 0x30
        reader = Reader(files['MECHWPNS.DTS'])
        while reader.at + 8 <= len(reader.data):
            weapon_roots.append(read_object(reader))

    def weapons(stem):
        data = files.get(STOCK_FITS.get(stem, ''), b'')
        if len(data) < 8:
            return []
        fit = {slot: weapon for slot, weapon, _, _ in struct.iter_unpack('<4h', data[8:8 + 8 * struct.unpack_from('<h', data, 6)[0]])}
        out = []
        for bone, code, mount, slot in hardpoints(stem):
            weapon = fit.get(slot, 0)  # 0: an empty hardpoint.
            if 0 < weapon < len(templates) and 0 <= templates[weapon][code] < len(weapon_roots):
                out.append((weapon_roots[templates[weapon][code]], bank('wpntex'), bone, mount))
        return out

    selector = {}
    for name in shapes:
        data = files.get(name[:-4] + '.DAT', b'')
        if len(data) >= 0x96:
            selector[name[:-4]] = struct.unpack_from('<h', data, 0x94)[0]
    for sub in ('model_json', 'textures'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    catalog, textures, skipped = [], {}, 0
    for name in shapes:
        stem = name[:-4]
        # Wreckage is painted in its chassis' bank: ACHI_DEB is ACHILLES's.
        owner = stem if stem in selector else next((s for s in sorted(selector) if stem.endswith('_DEB') and s[:4] == stem[:4]), None)
        number = selector.get(owner, -1)
        chosen = FIXED_BANKS.get(stem) or (BANKS[game][number] if 0 <= number < len(BANKS[game]) else None)
        model = safe(stem)
        item = dict(model_name=model, display_name=stem, texture_name='', game=game, category='Objects', status='ready')
        try:
            data, lost = build_model(read_object(Reader(files[name])), game, bank(chosen), colours, ramps, textures,
                                     {bone for bone, _, _, _ in hardpoints(stem)}, weapons(stem))
        except (struct.error, IndexError, KeyError, RecursionError) as exc:
            catalog.append(dict(item, status=f'failed: {exc}'))
            continue
        skipped += lost
        if not data['indices']:
            catalog.append(dict(item, status='no visible geometry'))
            continue
        textured = bank(chosen) is not None and any(t.startswith(chosen + '_') for t in data['material_textures'])
        # The .DAT selector only means a bank for shapes drawing from one; other .DATs (BULLETS, EXPLOS) differ.
        item['category'] = 'Debris' if stem.endswith('_DEB') else CATEGORY.get(number, 'HERCs') if textured and owner == stem and stem not in FIXED_BANKS else 'Objects'
        data['metadata'] = dict(source=name, bank=chosen if textured else None)
        (output / 'model_json' / f'{model}.json').write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
        catalog.append(dict(item, texture_name=data['material_textures'][0]))
    for slot, source in textures.items():
        target = output / 'textures' / slot
        if target.is_file():
            continue
        if source[0] == 'colour':
            Image.new('RGB', (8, 8), '#' + slot[4:10]).save(target)
        else:
            width, height, pixels = source[1][1][source[2]]
            image = Image.new('RGB', (width, height))
            image.putdata([colours[p] for p in pixels[:width * height]])
            image.save(target)
    (output / 'catalog.json').write_text(json.dumps(catalog, indent=1), encoding='utf-8')
    return dict(entries=len(catalog), ready=sum(e['status'] == 'ready' for e in catalog), skipped=skipped)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game', choices=sorted(BANKS), required=True)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(import_catalog(args.install, args.output or LOCAL_DATA / args.game, args.game))

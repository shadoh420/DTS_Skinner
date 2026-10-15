"""Earthsiege 2 missions as Maps packs the T1 Maps viewer draws (/maps/es2/). Earthsiege 1: see es1_* below.

python tools/import_es_map.py --game es2 --install "C:/Games/Earthsiege 2"

Every .MSN in the install's VOLN volumes is read the way the shell's mission generator reads it (rows, condition
gates, variants, repeated GUIDs; campaign flags set to the mission's own place in the career and every random draw
taken as 0, so a mission shows its first authored alternative). What follows is DBSIM's own world build:

- Terrain: the zone the header patch names. dat\\zoneN is cell shift and height scale, dba\\zoneN an 8-bit bitmap
  whose pixels are raw heights (image row 0 the north edge). Each cell takes ground frame 0, or 1 on a sparse 30%
  roll of the simulation's random stream, through mat0's tiling (one 256-texel frame repeats every 2 or 4 cells, v
  falling with y), and a base group that paints ground covers its tile with its formation's pad frame. Structures
  level the ground under them (flood-filled averages, then each region's east, north and north-east neighbour).
  Diagonals follow the per-cell selector; light is the mission sun's terrain curve, min(255, 512 cos), as the ramp
  multiplier for its row.
- Placement: each group stands at its point, or its first order's route's first waypoint, facing its heading or that
  route's first leg; members spread by MFORMS/BFORMS/FFORMS rotated by the group heading (base slots add their own
  turn), base groups that paint ground snap to their pad's anchor; machines and structures stand on the ground,
  flyers at their spawn height (5000 units when none). Group 0 is the player's squad: the first viewpoint. Groups
  that arrive later in the mission (a deployment action) are left out and counted in the warnings.
- Look: the theatre's WORLD<n>.DPL for terrain, sky and the exported shapes; sky = the hzline gradient (palette 208
  up to the horizon colour); fog from half the far terrain-detail range (14 cells) to all of it, in the horizon
  colour.

Sources: Kevin Foley's Herculan documentation of Earthsiege 2 (github.com/kevinfoley/Herculan, MIT):
docs/retail/formats/zone-terrain.md, msn-mission-file.md, script-dat.md, wld-world.md, bases-dat.md,
dgs-hd0-notes.md; rendering/terrain-texturing.md, terrain-lighting.md, distance-fog-and-sky.md; simulation/
terrain-heightmap.md, structure-behaviour.md, random-generator.md. The random table is the retail one Herculan
documents (src/Herculan.Engine/Numerics/SimRandom.cs).

Written under <output>: index.json, maps/<id>/{scene.json,heights.bin,materials.bin,light.bin}, models/*.json and
textures/*.png shared by every map. The install is only read.
"""
import argparse
import hashlib
import json
import math
import operator
from pathlib import Path
import shutil
import struct
import sys
import tempfile

from PIL import Image

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.import_earthsiege import (BANKS, SCALE, Reader, build_model, chassis_hardpoints, fitted_weapons,  # noqa: E402
                                     frames, palette, read_object, read_volumes, weapon_shapes)
from tools.local_data import LOCAL_DATA  # noqa: E402

# Rows 3-16 by number: record words (None: a waypoint list, 5 words and a word per entry; 'skip': 64 bytes unread).
ES2_ROWS = {3: 4, 4: 72, 5: 'skip', 6: 11, 7: 5, 8: None, 9: 6, 10: 41, 11: 15, 12: 72, 13: 51, 14: 31, 15: 11, 16: 82}
KEPT = (6, 7, 8, 12, 13, 14, 15, 16)  # The rows a map reads; the rest are only walked past.
THEATRES = ('urban', 'bsnow', 'volcan', 'ice', 'moon')  # wld\world<2 theatre + variant>.wld, day variants.
ANIMATED = {5, 6, 0x1d, 0x1e, 8, 0x0b, 0x20, 0x23}  # Radar and armed structures draw from BASES_AN.DTS.
UNBUILT = {0x0a, 0x35, 0x36, 0x3e, 0x3f, 0x40}  # Structure types no constructor case builds.
STRUCTURE_BANKS = ('basetex', 'vehtex')  # BASES.DAT +0x32.
FLYER_SHAPES = {'SKIMMER'}  # FLYERS.NAM's only flyer with a shape.
# Ramp row multipliers measured on WORLD0.RMP (terrain-lighting.md): row -> brightness against the texel.
RAMP = ((0, .36), (8, .62), (15, .79), (23, 1.0), (30, 1.15), (31, 1.16))
DETAIL = 14  # Cells of terrain in view at the highest detail setting.
EYE = 3.5  # Viewpoint height above the ground, metres (a HERC cockpit).


class SimRandom:
    """DBSIM's random stream (Herculan random-generator.md): a 56-entry additive table, retail start state."""
    TABLE = (-8925, -27341, -3123, 19394, -2078, -23841, -21904, 24746, 5195, -30392, 6442, 27405, -25077, -1039,
             -31225, 18013, -5388, 27133, 27716, 6951, -9359, -7281, -21580, -9145, 4062, 19854, -899, -29384,
             22856, -7313, -21982, 12070, 17402, -19035, 8427, -4731, -2621, -32353, 11676, -19859, -5845, -26667,
             15777, -30227, -7180, 29877, 44, -11425, 15112, 3573, -3458, -15760, 9609, 11915, 25426, -9683)

    def __init__(self):
        self.table, self.i, self.j = list(self.TABLE), 0x37, 0x18

    def next(self):
        value = (self.table[self.i] + self.table[self.j] + 0x8000) % 0x10000 - 0x8000
        self.table[self.i] = value
        self.i, self.j = (self.i + 1) % 56, (self.j + 1) % 56
        return value


def words(data, at, count):
    return list(struct.unpack_from(f'<{count}h', data, at))


def read_mission(data, flags, layout=None):
    """An Earthsiege 2 .MSN (or, with ES1_LAYOUT, an Earthsiege 1 one) as the shell's generator leaves it:
    {'header': ten words, row number: {GUID: record}, 'groups': [records]}, records as word lists with refs left as
    GUIDs. Only the rows a map needs are kept. `flags` is the campaign flag array (1000 words) the conditions read;
    every draw is 0."""
    layout = layout or ES2_LAYOUT
    rows, patch_words = layout['rows'], layout['patch']
    if data[:2] != b'\x05\x00':
        raise ValueError('Not a revision 5 mission')
    at, conditions = 2, []

    def gate(condition):
        return condition == -1 or any(c[0] == condition for c in conditions)

    def parent_value(condition):
        for c in conditions:
            if c[0] == condition and c[2] in (1, 3):
                return c[4] if c[2] == 1 else -99
        return -1

    def pick(key):  # Msn_PickVariant: the type-2 child of the key's type-3 record whose range holds the draw (0).
        for parent in conditions:
            if parent[2] == 3 and parent[3] == key:
                for child in conditions:
                    if child[2] == 2 and child[1] == parent[0] and child[3] & 0xffff <= 0 <= child[4] & 0xffff:
                        return child[0]
        return None

    def source(row, key):  # The first earlier record of the row whose condition is the picked variant.
        guid = pick(key)
        return next((r for r in row if r[1] == guid), None) if guid is not None else None

    count = struct.unpack_from('<H', data, at)[0]
    at += 2
    for _ in range(count):  # Row 1: the conditions.
        record = words(data, at, 7)
        at += 14
        kind = record[2]
        if kind == 0 and gate(record[1]):
            flag = flags[record[3]] if 0 <= record[3] < len(flags) else 0
            if record[4] in layout['ops'] and layout['ops'][record[4]](flag, record[5]):
                conditions.append(record)
        elif kind == 1 and gate(record[1]):
            record[4] = 0  # The draw.
            conditions.append(record)
        elif kind == 2:
            value = parent_value(record[1])
            if value == -99 or record[3] & 0xffff <= value <= record[4] & 0xffff:
                conditions.append(record)
        elif kind == 3 and gate(record[1]):
            conditions.append(record)
    header = [1] + [0] * 9
    count = struct.unpack_from('<H', data, at)[0]
    at += 2
    for _ in range(count):  # Row 2: header patches.
        patch = words(data, at, patch_words)
        at += patch_words * 2  # Earthsiege 1: a condition and the ten words; 2 adds thirty flag indices.
        if gate(patch[0]):
            header[0] = patch[1] if patch[1] != 1 else header[0]
            for word in range(1, 10):
                if patch[1 + word]:
                    header[word] = patch[1 + word]
    lists = {}
    for number, size in rows.items():
        count = struct.unpack_from('<H', data, at)[0]
        at += 2
        if size == 'skip':
            at += count * 64
            continue
        row = lists.setdefault(number, [])
        for _ in range(count):
            if size is None:
                record = words(data, at, 5)
                record.append(words(data, at + 10, max(record[4], 0)))
                at += 10 + 2 * max(record[4], 0)
            else:
                record = words(data, at, size)
                at += size * 2
            condition = record[0] if number in layout['cond0'] else record[1]
            if number not in layout['kept'] or not gate(condition):
                continue
            key = record[2] if number in layout['spans'] else -1
            if key != -1 and (origin := source(row, key)) is not None:
                keep = layout['keep'].get(number, ())  # ES2's pair counts are not copied.
                for word in range(*layout['spans'][number]):
                    if word not in keep:
                        record[word] = origin[word]
            if number == layout['points'] and record[4]:  # The sum of two earlier points, named by the low words of X and Y.
                a, b = (next((r for r in row if r[0] == guid != -1), None) for guid in (record[5], record[7]))
                if a and b:
                    for axis in (5, 7, 9):
                        total = point_word(a, axis) + point_word(b, axis)
                        record[axis], record[axis + 1] = (total & 0xffff) - (0x10000 if total & 0x8000 else 0), total >> 16
            earlier = next((r for r in row if r[0] == record[0] != -1), None)
            if earlier is None:
                row.append(record)
            elif number in layout['replace']:
                earlier[:] = record
            else:  # The roster and group rows overlay the fields that are set (Msn_MergeRow10 and siblings).
                for first, length, unset in layout['overlays'].get(number, ()):
                    for word in range(first, first + length):
                        if record[word] != unset:
                            earlier[word] = record[word]
    return {'header': header, **{number: {r[0]: r for r in row if r[0] != -1} for number, row in lists.items()},
            'groups': [r for r in lists.get(layout['groups'], []) if r[0] != -1]}


OVERLAYS = {12: ((4, 20, 0), (25, 10, -1), (58, 10, 5), (24, 1, -1), (35, 2, -1), (38, 20, -1), (68, 1, 2), (69, 2, -1), (71, 1, 100)),
            13: ((4, 20, 0), (24, 3, -1), (28, 22, -1), (50, 1, 100)),
            14: ((4, 3, -1), (8, 22, -1), (30, 1, 100)),
            15: ((4, 7, -1),),
            16: ((3, 20, 0), (28, 30, -1), (23, 5, -1), (58, 2, -1), (60, 21, -1))}
# Per game: record words per row, the rows kept, the rows whose condition is word 0, the points row, the rows a repeated
# GUID replaces (the rest overlay), the groups row, variant-key rows and the words a variant copies, flag tests.
ES2_LAYOUT = dict(patch=41, rows=ES2_ROWS, kept=KEPT, cond0=(4,), points=6, replace=(3, 6, 7, 8, 9), groups=16,
                  spans={6: (5, 11), 7: (4, 5), 8: (4, 6), 12: (4, 72), 13: (4, 51), 14: (4, 31), 15: (4, 11)},
                  keep={12: (37,), 13: (27,), 14: (7,)}, overlays=OVERLAYS,
                  ops={0x119: operator.eq, 0x11a: operator.ne, 0x11b: operator.lt, 0x11c: operator.le, 0x11d: operator.gt,
                       0x11e: operator.ge})
# Earthsiege 1, from GO.EXE's generator (0x30120; X-32 image, address = file offset - 0x491d) and checked against the
# data\script.dat it wrote for S2_ASS01: rows 3 points, 4 headings, 5 routes, 6 areas, 7 triggers, 8-10 HERC/flyer/
# structure rosters, 11 groups. A variant copies the whole record (0x31060); merges 0x2f924, 0x2fb4c, 0x2fce4, 0x2fe28.
ES1_LAYOUT = dict(patch=11, rows={3: 11, 4: 5, 5: None, 6: 6, 7: 15, 8: 53, 9: 32, 10: 12, 11: 44},
                  kept=(3, 4, 5, 8, 9, 10, 11), cond0=(), points=3, replace=(3, 4, 5, 6, 7), groups=11,
                  spans={3: (5, 11), 4: (4, 5), 5: (4, 6), 8: (4, 53), 9: (4, 32), 10: (4, 12)}, keep={},
                  overlays={8: ((4, 20, 0), (25, 10, -1), (39, 10, 5), (24, 1, -1), (35, 4, -1), (49, 1, 2), (50, 2, -1), (52, 1, 100)),
                            9: ((4, 20, 0), (24, 7, -1), (31, 1, 100)),
                            10: ((4, 7, -1), (11, 1, 100)),
                            11: ((3, 20, 0), (29, 8, -1), (23, 6, -1), (37, 7, -1))},
                  ops={0x114: operator.eq, 0x115: operator.ne, 0x116: operator.lt, 0x117: operator.gt})


def point_word(record, word):
    return (record[word] & 0xffff) | (record[word + 1] << 16)


def point(mission, guid, row=6):
    record = mission.get(row, {}).get(guid)
    return tuple(point_word(record, word) for word in (5, 7, 9)) if record else None


class Zone:
    """A zone's height grid: raw heights (row 0 south), the per-cell material and diagonal selector."""

    def __init__(self, files, number):
        header = files.get(f'DAT/ZONE{number}.DAT')
        bitmap = files.get(f'DBA/ZONE{number}.DBA')
        if not header or not bitmap:
            raise ValueError(f'Zone {number} not in the volumes')
        _, _, self.shift, self.scale = struct.unpack_from('<4i', header)
        width, height, pixels = frames(bitmap)[0]
        self.size = 1 << max(1, (width - 1).bit_length())
        if width != height or width != self.size or len(pixels) < width * height:
            raise ValueError(f'Zone {number} heightmap is {width}x{height}')
        size = self.size
        self.raw = [pixels[(size - 1 - y) * size:(size - y) * size] for y in range(size)]
        self.raw = [list(row) for row in self.raw]
        self.material = [[0] * size for _ in range(size)]

    def roll_materials(self, block_shift):
        """The bitmap loader's sparse roll: cells on a block boundary draw (30% material 1), the rest copy their block."""
        random, mask = SimRandom(), max((1 << (0x15 - block_shift - self.shift)) - 1, 0)
        size = self.size
        for row in range(size):  # In image order, as the loader walks it.
            y = size - 1 - row
            for x in range(size):
                if not row & mask and not x & mask:
                    self.material[y][x] = 1 if random.next() & 0xfff < 0x4ce else 0
                else:
                    self.material[y][x] = self.material[size - 1 - (row & ~mask)][x & ~mask]

    def selector(self, x, y):
        h = self.raw
        if x + 1 >= self.size or y + 1 >= self.size:
            return 0
        h00, h10, h01, h11 = h[y][x], h[y][x + 1], h[y + 1][x], h[y + 1][x + 1]
        return 1 if h00 + h11 == h01 + h10 else 2 if h00 + h11 - (h01 + h10) < 1 else 0

    def height(self, x, y):
        """Terrain_HeightQuery: world units at world (x, y), on the triangle the cell's diagonal puts it in."""
        cell = 1 << self.shift
        cx, cy = int(x) >> self.shift, int(y) >> self.shift
        if not (0 <= cx < self.size - 1 and 0 <= cy < self.size - 1):
            cx, cy = min(max(cx, 0), self.size - 2), min(max(cy, 0), self.size - 2)
        fx, fy = min(max((x - (cx << self.shift)) / cell, 0), 1), min(max((y - (cy << self.shift)) / cell, 0), 1)
        h = self.raw
        h00, h10, h01, h11 = h[cy][cx], h[cy][cx + 1], h[cy + 1][cx], h[cy + 1][cx + 1]
        if self.selector(cx, cy) == 0:  # Split along (1,0)-(0,1).
            raw = h00 + fx * (h10 - h00) + fy * (h01 - h00) if fx + fy <= 1 else h11 + (1 - fx) * (h01 - h11) + (1 - fy) * (h10 - h11)
        else:  # Along (0,0)-(1,1).
            raw = h00 + fx * (h10 - h00) + fy * (h11 - h10) if fx >= fy else h00 + fy * (h01 - h00) + fx * (h11 - h01)
        return raw * self.scale

    def flatten(self, marked):
        """Terrain_FlattenStructureFootprints: each 8-connected marked region takes its average raw height, written
        over the region and forced onto every member's east, north and north-east neighbour."""
        size, done = self.size, set()
        for start in sorted(marked):
            if start in done:
                continue
            region, pending = [], [start]
            done.add(start)
            while pending:
                x, y = pending.pop()
                region.append((x, y))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        near = (x + dx, y + dy)
                        if near in marked and near not in done:
                            done.add(near)
                            pending.append(near)
            average = sum(self.raw[y][x] for x, y in region) // len(region)
            for x, y in region:
                for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
                    if x + dx < size and y + dy < size:
                        self.raw[y + dy][x + dx] = average


def base_formations(data):
    """BFORMS.DAT: per formation its slots (x, y, heading turn) and pad (material, anchor across, anchor down, map)."""
    reader, out = Reader(data), []
    for _ in range(reader.read('i')[0]):
        slots = [reader.read('iih') for _ in range(reader.read('i')[0])]
        material, across, down, dim = reader.read('4i')
        rows = []
        for _ in range(dim):
            length = reader.read('B')[0]
            rows.append(reader.data[reader.at:reader.at + length])
            reader.at += length
        out.append(dict(slots=slots, material=material, across=across, down=down, map=rows))
    return out


def structure_types(data):
    """BASES.DAT: per type (shape index, texture bank selector)."""
    at, out = 2, []
    for _ in range(struct.unpack_from('<h', data)[0]):
        shape, parts = struct.unpack_from('<h', data, at + 2)[0], struct.unpack_from('<h', data, at + 0x12)[0]
        at += 0x14 + 30 * parts
        out.append((shape, struct.unpack_from('<h', data, at + 0x32 - 0x18)[0]))
        at += 0x1c
    return out


def dgs_shapes(data):
    """BASES.DGS: per record (bounding radius, a root holding its one DTS chunk)."""
    at, out = 0, []
    while at + 8 <= len(data):
        _, size = struct.unpack_from('<iI', data, at)
        radius = struct.unpack_from('<h', data, at + 12)[0]
        reader = Reader(data[at + 8:at + 8 + size])
        reader.at = 12
        children = [read_object(reader) for _ in range(reader.read('h')[0])]
        out.append((radius, dict(type='shape', children=children)))
        at += 8 + size
        at += at % 2
    return out


def dts_roots(data):
    """A .DTS of several roots (BASES_AN.DTS): per root (bounding radius, root)."""
    reader, out = Reader(data), []
    while reader.at + 8 <= len(data):
        radius = struct.unpack_from('<h', data, reader.at + 12)[0]
        out.append((radius, read_object(reader)))
    return out


def turn(dx, dy, bam):
    angle = bam / 0x10000 * 2 * math.pi
    c, s = math.cos(angle), math.sin(angle)
    return dx * c - dy * s, dx * s + dy * c


def placement_matrix(x, y, z, bam):
    """Viewer matrix (column-major) for a shape at world (x, y, z) units turned `bam`: the shape's model JSON is
    (-x, z, y) of its own frame, the viewer (x, z, -y) of the world's."""
    angle = bam / 0x10000 * 2 * math.pi
    c, s = math.cos(angle), math.sin(angle)
    return [round(v, 5) for v in (-c, 0, s, 0, 0, 1, 0, 0, -s, 0, -c, 0, x * SCALE, z * SCALE, -y * SCALE, 1)]


def view_matrix(x, y, z, bam, pitch=0.0):
    """A viewpoint at world (x, y, z) units looking along heading `bam` (forward = model +y turned)."""
    yaw = bam / 0x10000 * 2 * math.pi
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    # Rotation Y(yaw) * X(pitch), column-major.
    return [round(v, 5) for v in (cy, 0, -sy, 0, sy * sp, cp, cy * sp, 0, sy * cp, -sp, cy * cp, 0, x * SCALE, z * SCALE, -y * SCALE, 1)]


class Pack:
    """The shared models/ and textures/ of an output folder; a shape is exported once per theatre palette."""

    def __init__(self, files, output, game):
        self.files, self.output, self.game = files, Path(output), game
        self.models, self.palettes, self.banks = {}, {}, {}
        for sub in ('models', 'textures'):
            (self.output / sub).mkdir(parents=True, exist_ok=True)
        self.weapons = weapon_shapes(files)
        self.dgs = dgs_shapes(files['DGS/BASES.DGS']) if 'DGS/BASES.DGS' in files else []
        self.animated = dts_roots(files['DTS/BASES_AN.DTS'] if 'DTS/BASES_AN.DTS' in files else files.get('BASES_AN.DTS', b''))
        # Earthsiege 1's Cybrid structures (a group of side 1): BASES_E.DGS and BASES_AE.DTS.
        self.enemy_dgs = dgs_shapes(files['DGS/BASES_E.DGS']) if 'DGS/BASES_E.DGS' in files else []
        self.enemy_animated = dts_roots(files.get('DTS/BASES_AE.DTS', b''))

    def palette(self, world):
        if world not in self.palettes:
            self.palettes[world] = palette(self.files[f'DPL/WORLD{world}.DPL'])
        return self.palettes[world]

    def bank(self, name, world):
        key = (name, world)
        if key not in self.banks:
            data = self.files.get(name.upper() + '.DBA')
            self.banks[key] = (f'{self.game}w{world}-{name}', frames(data)) if data else None
        return self.banks[key]

    def texture(self, name, image):
        target = self.output / 'textures' / name
        if not target.is_file():
            image.save(target)
        return name

    def frame_image(self, bank, frame, colours, clear=False):
        width, height, pixels = bank[1][frame]
        image = Image.new('RGBA', (width, height))
        image.putdata([(*colours[p], 0 if clear and p == 0 else 255) for p in pixels[:width * height]])
        return image

    def model(self, key, root, bank_name, world, weapons=(), hidden=()):
        """Export `root` (in bank `bank_name`) under the world's palette once; the model file name."""
        name = f'{self.game}w{world}-{key}.json'
        if name in self.models:
            return self.models[name]
        colours, ramps = self.palette(world)
        textures = {}
        try:
            data, _ = build_model(root, self.game, self.bank(bank_name, world), colours, ramps, textures, hidden,
                                  [(w, self.bank('wpntex', world), bone, mount) for w, _, bone, mount in weapons])
        except (struct.error, IndexError, KeyError, RecursionError):
            data = None
        if not data or not data['indices']:
            self.models[name] = None
            return None
        for slot, source in textures.items():
            if source[0] == 'colour':
                self.texture(slot, Image.new('RGB', (8, 8), '#' + slot[4:10]))
            else:  # Structure lattices show the sky through palette index 0 (dts-texture-binding.md).
                self.texture(slot, self.frame_image(source[1], source[2], colours, clear=bank_name == 'basetex'))
        (self.output / 'models' / name).write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
        self.models[name] = name
        return name

    def herc(self, stem, fit, world):
        """HERC chassis `stem` carrying `fit` ({fit slot: weapon id}), in the bank its .DAT names; None without one."""
        files = self.files
        selector = files.get(stem + '.DAT', b'')
        number = struct.unpack_from('<h', selector, 0x94)[0] if len(selector) >= 0x96 else -1
        if stem + '.DTS' not in files or not 0 <= number < len(BANKS[self.game]):
            return None
        weapons = fitted_weapons(files, stem, fit, *self.weapons, None)
        key = stem + ('-' + hashlib.sha1(repr(sorted(fit.items())).encode()).hexdigest()[:8] if weapons else '')
        return self.model(key, read_object(Reader(files[stem + '.DTS'])), BANKS[self.game][number], world, weapons,
                          {bone for bone, _, _, _ in chassis_hardpoints(files, stem)})


def formation_table(data, slots, fmt):
    """MFORMS/FFORMS.DAT (and Earthsiege 1's BFORMS.DAT): int16 count, then per formation `slots` offsets `fmt`."""
    size = struct.calcsize(fmt)
    count = struct.unpack_from('<h', data)[0] if len(data) >= 2 else 0
    return [[struct.unpack_from(fmt, data, 2 + (f * slots + s) * size) for s in range(slots)] for f in range(count)]


def es2_scene(pack, stem, stage, number):
    """Build one Earthsiege 2 mission's map into a staging folder; returns (scene, staging files)."""
    files, warnings = pack.files, []
    flags = [0] * 1000
    if stage:
        flags[1], flags[2] = stage, number
    mission = read_mission(files[f'MSN/{stem}.MSN'], flags)
    header = mission['header']
    theatre = stage - 1 if stage else 1  # Practice and instant-action missions fly theatre 1 (script-dat.md).
    world = theatre * 2  # Day variant.
    wld = files[f'WLD/WORLD{world}.WLD']
    hzline = struct.unpack_from('<8h', wld)
    bank_name = wld.rstrip(b'\0').split(b'\0')[-2].decode('latin1').lower()
    colours, _ = pack.palette(world)
    mat0 = files['DAT/MAT0.DAT']
    mat0 = [struct.unpack_from('<2i', mat0, 4 + 8 * i) for i in range(struct.unpack_from('<i', mat0)[0])]
    zone = Zone(files, header[1])
    zone.roll_materials(mat0[0][1])
    types = structure_types(files['DAT/BASES.DAT'])
    bforms = base_formations(files['DAT/BFORMS.DAT'])
    mforms = formation_table(files['DAT/MFORMS.DAT'], 7, '<2h')
    fforms = formation_table(files['DAT/FFORMS.DAT'], 3, '<3h')
    mechs = files['MECHS.NAM'].decode('latin1').split('\0')
    flyers = files.get('FLYERS.NAM', b'').decode('latin1').split('\0')

    def route_start(group):
        """A group's first order's route: its first two points, for the spawn and the first leg's heading."""
        order = mission.get(15, {}).get(group[48])
        route = mission.get(8, {}).get(order[7]) if order else None
        return [p for p in (point(mission, guid) for guid in (route[5] if route else [])) if p][:2]

    def where(group):
        start = point(mission, group[25])
        route = route_start(group)
        start = start or (route[0] if route else None)
        heading = mission.get(7, {}).get(group[26])
        if heading:
            bam = heading[4] * 182
        elif len(route) > 1:  # atan2 of the first leg, less a quarter turn: forward is model +y.
            bam = int(math.atan2(route[1][1] - route[0][1], route[1][0] - route[0][0]) / (2 * math.pi) * 0x10000) - 0x4000
        else:
            bam = 0
        return start, bam

    objects, marked, later, unplaced, viewpoints = [], set(), 0, 0, []
    pending_bases, base_views = [], []
    for index, group in enumerate(mission['groups']):
        start, bam = where(group)
        if index == 0:  # The player's squad.
            if start:
                viewpoints.append(view_matrix(start[0], start[1], zone.height(start[0], start[1]) + EYE / SCALE, bam))
            continue
        if group[59] != -1:  # Arrives later (deployment action): its point is a placeholder.
            later += 1
            continue
        if not start:
            unplaced += 1
            continue
        kind, formation, x, y = group[23], group[24], start[0], start[1]
        layout = bforms[formation] if kind == 2 and 0 <= formation < len(bforms) else None
        if layout and group[3] and layout['material'] >= 0:  # Paints ground: the pad tile and its anchor.
            dim = len(layout['map'])
            tile = dim << 13
            tx, ty = x & ~(tile - 1), y & ~(tile - 1)
            x, y = tx + layout['across'] * (tile >> 8), ty + tile - layout['down'] * (tile >> 8)
            material = layout['material']
            per_cell = 1 << max(zone.shift - 13, 0)
            for cy in range(ty >> zone.shift, (ty + tile) >> zone.shift):
                for cx in range(tx >> zone.shift, (tx + tile) >> zone.shift):
                    if 0 <= cx < zone.size and 0 <= cy < zone.size:
                        zone.material[cy][cx] = material
                        mx, my = ((cx << zone.shift) - tx) >> 13, ((cy << zone.shift) - ty) >> 13
                        row = dim - 1 - my - (per_cell - 1)
                        if any(layout['map'][r][c] for r in range(max(row, 0), min(row + per_cell, dim))
                               for c in range(mx, min(mx + per_cell, dim))):
                            marked.add((cx, cy))
        ground = zone.height(x, y)
        if kind == 2:
            base_views.append((not (layout and group[3]), len(base_views), x, y))  # Pad bases first.
        for slot in range(20):
            member = group[28 + slot]
            if member == -1:
                continue
            roster = mission.get({0: 12, 1: 13, 2: 14}.get(kind, -1), {}).get(member)
            if roster is None:
                continue
            own = point(mission, roster[{0: 35, 1: 24, 2: 5}[kind]])
            dx = dy = dz = 0
            turn_extra = 0
            if slot:
                if kind == 0 and 0 <= formation < len(mforms) and slot <= 7:
                    dx, dy = mforms[formation][slot - 1]
                elif kind == 1 and 0 <= formation < len(fforms) and slot <= 3:
                    dx, dy, dz = fforms[formation][slot - 1]
                elif kind == 2 and layout and slot <= len(layout['slots']):
                    dx, dy, turn_extra = layout['slots'][slot - 1]
            rx, ry = turn(dx, dy, bam)
            px, py = (own[0], own[1]) if own else (x + rx, y + ry)
            own_heading = mission.get(7, {}).get(roster[{0: 36, 1: 25, 2: 6}[kind]])
            heading = own_heading[4] * 182 if own_heading else (bam + turn_extra if kind == 2 else bam)
            if kind == 0:
                stem_name = mechs[roster[24]].strip().upper() if 0 <= roster[24] < len(mechs) else ''
                if not stem_name:
                    continue
                model = pack.herc(stem_name, {s: roster[25 + s] for s in range(10) if roster[25 + s] >= 0}, world)
                objects.append(dict(name=stem_name or 'HERC', model=model, z=ground, x=px, y=py, bam=heading, npc=True))
            elif kind == 1:
                name = flyers[roster[26]].strip().upper() if 0 <= roster[26] < len(flyers) else ''
                spawn = own or start  # Flyers hold the spawn point's height, 5000 units when it has none.
                height = (spawn[2] or 5000) + dz
                model = pack.model(name, read_object(Reader(files[name + '.DTS'])), 'enemy', world) \
                    if name in FLYER_SHAPES and name + '.DTS' in files else None
                objects.append(dict(name=name or 'flyer', model=model, z=height, x=px, y=py, bam=heading, npc=True))
            else:
                number = roster[4]
                if not 0 <= number < len(types) or number in UNBUILT:
                    continue
                shape, selector = types[number]
                library = pack.animated if number in ANIMATED else pack.dgs
                if not 0 <= shape < len(library):
                    warnings.append(f'Structure type {number} names missing shape {shape}')
                    continue
                radius, root = library[shape]
                bank = STRUCTURE_BANKS[selector] if 0 <= selector < len(STRUCTURE_BANKS) else 'basetex'
                model = pack.model(('an' if number in ANIMATED else 'dgs') + f'{shape:02d}-{bank}', root, bank, world)
                cx, cy = int(px) >> zone.shift, int(py) >> zone.shift
                marked.add((cx, cy))
                for gx in range(cx - 8, cx + 10):  # Corners within the shape's radius, and the cells meeting them.
                    for gy in range(cy - 8, cy + 10):
                        if math.hypot((gx << zone.shift) - px, (gy << zone.shift) - py) <= radius:
                            marked.update({(gx, gy), (gx - 1, gy), (gx, gy - 1), (gx - 1, gy - 1)})
                pending_bases.append(dict(name=f'structure {number}', model=model, x=px, y=py, bam=heading, npc=False))
    marked = {(x, y) for x, y in marked if 0 <= x < zone.size and 0 <= y < zone.size}
    zone.flatten(marked)
    for item in pending_bases:  # Structures re-settle on the levelled ground.
        objects.append(dict(item, z=zone.height(item['x'], item['y'])))
    if later:
        warnings.append(f'{later} group(s) arrive later in the mission and are not shown')
    if unplaced:
        warnings.append(f'{unplaced} group(s) have no point or route and are not shown')
    missing = sum(1 for item in objects if not item['model'])
    if missing:
        warnings.append(f'{missing} object(s) have no drawable shape (magenta boxes)')
    warnings.append('Random mission variants are shown as their first alternative; campaign flags as on first play')

    # Terrain pack files.
    size = zone.size
    squares = size - 1
    heights = struct.pack(f'<{size * size}f', *(zone.raw[y][x] * zone.scale * SCALE for y in range(size) for x in range(size)))
    slots, textures, materials = {}, [], bytearray(squares * squares * 2)
    pieces, divisions = bytearray(squares * squares), []
    bank = pack.bank(bank_name, world)
    if not bank:
        raise ValueError(f'Terrain bank {bank_name}.DBA not in the volumes')
    for y in range(squares):
        for x in range(squares):
            frame, block = mat0[zone.material[y][x]] if zone.material[y][x] < len(mat0) else mat0[0]
            shift = zone.shift + block - 13
            span = 1 << shift  # Texels per cell; u and v wrap at 256 (terrain-texturing.md), v falling with y.
            u0, v0 = (x << shift) & 0xff, (y << shift) & 0xff
            if (frame, span) not in slots:
                slots[(frame, span)] = len(slots)
                image = pack.frame_image(bank, frame, colours).convert('RGB')
                image = image.point(lambda value: min(255, round(value * RAMP[-1][1])))  # Lit ground is 1.16x its texels.
                textures.append(pack.texture(f'{pack.game}w{world}-{bank_name}-{frame:02d}.png', image))
                divisions.append(256 // span)
            pieces[y * squares + x] = ((-(v0 + span)) & 0xff) // span * (256 // span) + u0 // span
            diagonal = 0 if zone.selector(x, y) == 0 else 1  # 1: split along (0,0)-(1,1).
            flip = (1 if diagonal == 0 else 0) ^ ((x ^ y) & 1)  # The viewer's checkerboard, corrected per square.
            materials[(y * squares + x) * 2] = flip << 6
            materials[(y * squares + x) * 2 + 1] = slots[(frame, span)]
    # Light: the mission sun's terrain shade per triangle, as the ramp row's multiplier, averaged at each vertex.
    sun = mission_sun()
    levels = [[[] for _ in range(size)] for _ in range(size)]
    cell = 1 << zone.shift
    for y in range(squares):
        for x in range(squares):
            h = [[zone.raw[y + j][x + i] * zone.scale for i in (0, 1)] for j in (0, 1)]
            if zone.selector(x, y) == 0:
                faces = (((0, 0), (1, 0), (0, 1)), ((1, 0), (1, 1), (0, 1)))
            else:
                faces = (((0, 0), (1, 0), (1, 1)), ((0, 0), (1, 1), (0, 1)))
            for face in faces:
                a, b, c = ([i * cell, j * cell, h[j][i]] for i, j in face)
                u, v = [b[k] - a[k] for k in range(3)], [c[k] - a[k] for k in range(3)]
                normal = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
                length = math.hypot(*normal) or 1
                cos = -sum(n * s for n, s in zip(normal, sun)) / length
                shade = min(255, int(512 * max(cos, 0)))
                level = ramp(shade * 31 >> 8) / RAMP[-1][1]
                for i, j in face:
                    levels[y + j][x + i].append(level)
    light = bytearray()
    for y in range(size):
        for x in range(size):
            grey = round(15 * min(1, sum(levels[y][x]) / len(levels[y][x]))) if levels[y][x] else 15
            light += struct.pack('<H', grey << 8 | grey << 4 | grey)
    # Sky, haze and range.
    zenith, horizon = hzline[1], hzline[1] + hzline[3] - 1
    visible = (DETAIL << zone.shift) * SCALE
    objects_out = []
    for number, item in enumerate(objects):
        entry = dict(name=item['name'], matrix=placement_matrix(item['x'], item['y'], item['z'], item['bam']),
                     model=item['model'], source='pack')
        if item['npc']:
            entry.update(npc=True, entity=f'{item["name"]}#{number}')
        objects_out.append(entry)
    centre = (size << zone.shift) / 2
    viewpoints.append(view_matrix(centre * .5, centre * .5, zone.raw[size // 4][size // 4] * zone.scale + 120 / SCALE, -0x2000, -.35))
    for *_, x, y in sorted(base_views)[:7]:  # Base groups from 250 m south, 120 m up.
        viewpoints.append(view_matrix(x, y - 250 / SCALE, zone.height(x, y) + 120 / SCALE, 0, -.45))
    azimuth = math.degrees(math.atan2(sun[0], -sun[1]))  # Toward the sun, as the viewer's toward() reads it.
    scene = dict(
        id=stem.lower(), mission=stem, type='', theatre=bank_name, zone=header[1],
        terrain=dict(squares=squares, unit=cell * SCALE, columns=1, rows=1, position=[0, 0, 0], lightWidth=size,
                     textures=textures, pieces=divisions, visibleDistance=visible, hazeDistance=visible / 2, splitBit=True),
        sun=dict(ambient=[.45, .45, .45], intensity=[.75, .75, .75], azimuth=azimuth,
                 incidence=math.degrees(math.asin(-sun[2]))),
        haze=list(colours[horizon]),
        sky=sky_dome(pack, f'{pack.game}w{world}', colours, hzline, visible),
        planets=[], stars=None, flare=None, weather=None, objects=objects_out, viewpoints=viewpoints, warnings=warnings)
    return scene, dict(heights=heights, materials=bytes(materials), light=bytes(light), pieces=bytes(pieces))


def mission_sun():
    """Light_CreateMissionSun: (0, 1, 0) turned by x -6000 then z 21000 (0x10000 a turn): the way the light travels."""
    x, z = -6000 / 0x10000 * 2 * math.pi, 21000 / 0x10000 * 2 * math.pi
    vy, vz = math.cos(x), math.sin(x)
    return (-math.sin(z) * vy, math.cos(z) * vy, vz)


def ramp(row):
    for (a, low), (b, high) in zip(RAMP, RAMP[1:]):
        if row <= b:
            return low + (high - low) * (row - a) / (b - a)
    return RAMP[-1][1]


def es2_missions(files):
    """(stem, stage, number within stage, section) for every mission, in the career's order where it has one."""
    out = []
    for name in sorted(k for k in files if k.startswith('MSN/') and k.endswith('.MSN')):
        stem = name[4:-4]
        if stem[:1] == 'C' and stem[1:2].isdigit() and '_' in stem:
            stage, number = int(stem[1]), int(stem.split('_')[1]) - 1
            out.append((stem, stage, number, f'Campaign {stage} · {THEATRES[stage - 1]}'))
        else:
            out.append((stem, 0, 0, 'Training' if stem.startswith('TRAIN') else 'Instant action'))
    return out


# Earthsiege 1 (DBSIM.EXE, X-32 image; addresses linear): the script header's first two words are the theatre and the
# zone (zone loader 0x228d0); the palette is world<6 theatre + 2 word9>, the world file that plus word8 (0x280b8); the
# ground is the world file's fourth name as a raw 256x256 dba\<name>.64k (gndtex.cpp, 0x2c150).
ES1_THEATRES = ('desert', 'temper', 'ice', 'urban', 'waste', 'ruins')  # Name table at 0x9910c.
ES1_TILE = 32768  # ponytail: world units one ground repeat covers (ES2's 128 units a texel); the .64k tiling is not decoded.
# A group (row 11; DBSIM's script loader 0x20c88, group set-up 0x1f628): word 23 kind (0 HERCs, 1 flyers,
# 2 structures), 24 formation, 26 point, 27 heading, 28 route, 29-36 members, 37 side (0 Terran, else Cybrid).
# Per kind: the roster row, its own point and heading words, and the formation table's slots per formation.
ES1_ROSTERS = {0: (8, 35, 36, 7), 1: (9, 24, 25, 3), 2: (10, 5, 6, 7)}


def es1_heading(mission, guid):
    """DBSIM keeps a heading word as (value << 14) & 0xffff (0x203ba): its low two bits as quarter turns, so the
    missions' 90 reads as a half turn. Replicated, not corrected."""
    record = mission.get(4, {}).get(guid)
    return (record[4] << 14) & 0xffff if record else None


def es1_placements(mission, forms):
    """Where DBSIM stands each group member at mission start: ([(kind, side, roster, x, y, z, bam)], groups with
    no point or route, the player's squad as (x, y, bam) or None). `forms` holds the MFORMS, FFORMS and BFORMS
    offsets by kind. Every member of groups 1.. is built at the start (0x1feb4); group 0's HERCs are the player's,
    from the shell's player.mec, so only its point is taken."""
    units, unplaced, player = [], 0, None
    for index, group in enumerate(mission['groups']):
        route = mission.get(5, {}).get(group[28])
        route = [p for p in (point(mission, guid, 3) for guid in (route[5] if route else [])) if p][:2]
        start = point(mission, group[26], 3) or (route[0] if route else None)
        bam = es1_heading(mission, group[27])
        if bam is None:  # The route's first leg (0x6b784): atan2 less a quarter turn, forward being model +y.
            bam = int(math.atan2(route[1][1] - route[0][1], route[1][0] - route[0][0]) / (2 * math.pi) * 0x10000) - 0x4000 \
                if len(route) > 1 else 0
        if index == 0:
            player = (start[0], start[1], bam) if start else None
            continue
        if group[23] not in ES1_ROSTERS:
            continue
        row, own_point, own_heading, slots = ES1_ROSTERS[group[23]]
        members = [(slot, mission.get(row, {}).get(group[29 + slot])) for slot in range(8)]
        members = [(slot, roster) for slot, roster in members if roster]
        if not members:
            continue
        # Offsets turn with the group's first member (0xf360): its own heading, else the group's.
        lead = es1_heading(mission, members[0][1][own_heading])
        lead = bam if lead is None else lead
        offsets = [o for formation in forms.get(group[23], []) for o in formation]
        missing = False
        for slot, roster in members:
            heading = es1_heading(mission, roster[own_heading])
            own = point(mission, roster[own_point], 3)
            if own:
                x, y, z = own
            elif start:  # Slot n takes formation entry n-1, counted on through the table as DBSIM does.
                at = group[24] * slots + slot - 1
                dx, dy, *dz = offsets[at] if slot and 0 <= at < len(offsets) else (0, 0)
                dx, dy = turn(dx, dy, lead)
                x, y, z = start[0] + dx, start[1] + dy, start[2] + sum(dz)
            else:
                missing = True
                continue
            if group[23] == 1 and z == 0:  # Flyers at ground level climb to 5000 units (0x1d898).
                z = 5000
            units.append((group[23], group[37], roster, x, y, z, bam if heading is None else heading))
        unplaced += missing
    return units, unplaced, player


def es1_structure_types(data):
    """Earthsiege 1's BASES.DAT (base.cpp 0x5000): int16 count, 70-byte records; +2 shape, +6 non-zero when the
    shape is a BASES_AN/AE.DTS root rather than a BASES/BASES_E.DGS record."""
    count = struct.unpack_from('<h', data)[0] if len(data) >= 2 else 0
    return [struct.unpack_from('<2xh2xh', data, 2 + 70 * i) for i in range(count)]


def es1_units(pack, mission, world):
    """The mission's HERCs, flyers and structures as scene objects, the player's squad (x, y, bam) and warnings."""
    files, objects, warnings = pack.files, [], []
    mechs = files.get('MECHS.NAM', b'').decode('latin1').split('\0')
    flyers = files.get('FLYERS.NAM', b'').decode('latin1').split('\0')
    forms = {0: formation_table(files.get('DAT/MFORMS.DAT', b''), 7, '<2h'),
             1: formation_table(files.get('DAT/FFORMS.DAT', b''), 3, '<3h'),
             2: formation_table(files.get('DAT/BFORMS.DAT', b''), 7, '<2i')}
    types = es1_structure_types(files.get('DAT/BASES.DAT', b''))
    units, unplaced, player = es1_placements(mission, forms)
    for kind, side, roster, x, y, z, bam in units:
        if kind == 0:
            name = mechs[roster[24]].strip().upper() if 0 <= roster[24] < len(mechs) else ''
            model = pack.herc(name, {s: roster[25 + s] for s in range(10) if roster[25 + s] >= 0}, world) if name else None
        elif kind == 1:
            name = flyers[roster[26]].strip().upper() if 0 <= roster[26] < len(flyers) else ''
            model = pack.model(name, read_object(Reader(files[name + '.DTS'])), 'enemy', world) \
                if name in FLYER_SHAPES and name + '.DTS' in files else None
        else:
            number, model = roster[4], None
            name = f'structure {number}'
            if 0 <= number < len(types):
                shape, animated = types[number]
                library = (pack.enemy_animated if side else pack.animated) if animated else (pack.enemy_dgs if side else pack.dgs)
                if 0 <= shape < len(library):
                    key = ('an' if animated else 'dgs') + ('e' if side else '') + f'{shape:02d}-basetex'
                    model = pack.model(key, library[shape][1], 'basetex', world)
        entry = dict(name=name or ('HERC', 'flyer')[kind], matrix=placement_matrix(x, y, z, bam), model=model, source='pack')
        if kind < 2:
            entry.update(npc=True, entity=f'{entry["name"]}#{len(objects)}')
        objects.append(entry)
    if unplaced:
        warnings.append(f'{unplaced} group(s) have no point or route and are not shown')
    missing = sum(1 for item in objects if not item['model'])
    if missing:
        warnings.append(f'{missing} unit(s) have no drawable shape (magenta boxes)')
    return objects, player, warnings


def es1_scene(pack, stem):
    """An Earthsiege 1 mission: ground, the zone's terrain features and the mission's units. The zone (dat\\zoneN:
    u16, u16 count, then records of u16 shape, int32 x, y, z, int16 x, y, z turn) places shapes of the theatre's
    grid-shape library (dgs\\<theatre>, textured from dba\\terrain) on flat ground; units as es1_placements."""
    files, warnings = pack.files, []
    try:
        mission = read_mission(files[f'MSN/{stem}.MSN'], [0] * 1000, ES1_LAYOUT)
    except struct.error:
        raise ValueError('Mission file ends inside its rows (truncated, or not the retail layout)') from None
    header = mission['header']
    theatre, zone_number = header[0], header[1]
    if not 0 <= theatre < len(ES1_THEATRES):
        raise ValueError(f'Theatre {theatre} out of range')
    world = 6 * theatre + 2 * header[9]
    wld = files.get(f'WLD/WORLD{world + header[8]}.WLD') or files[f'WLD/WORLD{world}.WLD']
    hzline = struct.unpack_from('<8h', wld)
    ground_name = wld.rstrip(b'\0').split(b'\0')[-2].decode('latin1').upper()
    colours, _ = pack.palette(world)
    zone = files.get(f'DAT/ZONE{zone_number}.DAT')
    if zone is None:
        raise ValueError(f'Zone {zone_number} not in the volumes')
    library = dgs_shapes(files[f'DGS/{ES1_THEATRES[theatre].upper()}.DGS'])
    objects = []
    for i in range(struct.unpack_from('<H', zone, 2)[0]):
        shape, x, y, z, _, _, turn_z = struct.unpack_from('<h3i3h', zone, 4 + 20 * i)
        if not 0 <= shape < len(library):
            warnings.append(f'Zone feature {i} names missing shape {shape}')
            continue
        model = pack.model(f'{ES1_THEATRES[theatre]}{shape:02d}', library[shape][1], 'terrain', world)
        objects.append(dict(name=f'{ES1_THEATRES[theatre]} feature {shape}', matrix=placement_matrix(x, y, z, turn_z),
                            model=model, source='pack'))
    units, player, notes = es1_units(pack, mission, world)
    objects += units
    warnings += notes
    warnings.append("The player's squad (the shell's player.mec) is not shown; viewpoint 1 stands at its point")
    warnings.append("Units stand at their points' height (ground level); settling onto terrain features is not checked")
    warnings.append('Random mission variants are shown as their first alternative; campaign flags as on first play')
    warnings.append('Ground texture tiling and draw distance are estimates, not read from the game')
    raw = files.get(f'DBA/{ground_name}.64K')
    squares, unit = 128, ES1_TILE * SCALE
    if raw and len(raw) >= 65536:
        image = Image.new('RGB', (256, 256))
        image.putdata([colours[p] for p in raw[:65536]])
        ground = pack.texture(f'es1w{world}-{ground_name.lower()}.png', image)
    else:
        warnings.append(f'Ground bitmap {ground_name}.64K not found: flat ground colour')
        ground = pack.texture(f'es1w{world}-ground-{hzline[4]}.png', Image.new('RGB', (8, 8), colours[hzline[4]]))
    horizon = hzline[1] + hzline[3] - 1
    visible = (DETAIL << 14) * SCALE
    sun = mission_sun()
    scene = dict(
        id=stem.lower(), mission=stem, type='', theatre=ES1_THEATRES[theatre], zone=zone_number,
        terrain=dict(squares=squares, unit=unit, columns=1, rows=1, position=[-squares * unit / 2, -squares * unit / 2, 0],
                     lightWidth=squares + 1, textures=[ground], visibleDistance=visible, hazeDistance=visible / 2),
        sun=dict(ambient=[.45, .45, .45], intensity=[.75, .75, .75], azimuth=math.degrees(math.atan2(sun[0], -sun[1])),
                 incidence=math.degrees(math.asin(-sun[2]))),
        haze=list(colours[horizon]), sky=sky_dome(pack, f'es1w{world}', colours, hzline, visible), planets=[], stars=None,
        flare=None, weather=None, objects=objects,
        viewpoints=[view_matrix(*(player or (0, 0, 0))[:2], EYE / SCALE, player[2] if player else 0),
                    view_matrix(-60000, -60000, 200 / SCALE, -0x2000, -.35)],
        warnings=warnings)
    return scene, dict(heights=bytes((squares + 1) ** 2 * 4), materials=bytes(squares * squares * 2),
                       light=struct.pack('<H', 0xfff) * (squares + 1) ** 2)


def sky_dome(pack, prefix, colours, hzline, visible):
    """The hzline backdrop as the viewer's sixteen-panel dome: palette zenith..horizon bands over the horizon colour."""
    zenith, horizon = hzline[1], hzline[1] + hzline[3] - 1
    radius = visible * 1.05
    top = math.atan(hzline[3] * hzline[2] / 320)  # The bands' height over a 640-wide 90-degree view.
    gradient = Image.new('RGB', (1, 112))
    horizon_row = round(112 * .75 / .875)
    for row in range(112):
        band = (horizon_row - row) * hzline[3] // horizon_row if row < horizon_row else 0
        gradient.putpixel((0, row), colours[max(zenith, horizon - band) if row < horizon_row else horizon])
    texture = pack.texture(f'{prefix}-sky-{zenith}-{horizon}-{hzline[2]}.png', gradient)
    return dict(textures=[texture] * 16, feature=0, size=radius * math.tan(top) / .75, top=list(colours[zenith]),
                bottom=list(colours[horizon]))


def es1_section(stem):
    if stem[:2] in ('SM', 'S2'):
        return 'Simulator'
    return f'Campaign {stem[1]} · chapter {stem[4]}' if stem[:1] == 'B' and len(stem) > 4 else 'Other'


def import_es1(files, install, output, missions=None, replace=False):
    """Import every Earthsiege 1 mission (ground, terrain features and units), or the named ones."""
    if 'DGS/DESERT.DGS' not in files or not any(k.startswith('MSN/') for k in files):
        raise ValueError(f'Not an Earthsiege folder (no VOL volumes with missions and terrain shapes): {install}')
    pack = Pack(files, output, 'es1')
    report = {'imported': [], 'skipped': [], 'failed': {}, 'warnings': {}}
    wanted = {m.upper() for m in missions} if missions else None
    for name in sorted(k for k in files if k.startswith('MSN/') and k.endswith('.MSN')):
        stem = name[4:-4]
        if wanted and stem.upper() not in wanted:
            continue
        if not replace and (output / 'maps' / stem.lower() / 'scene.json').is_file():
            report['skipped'].append(stem)
            continue
        try:
            scene, binaries = es1_scene(pack, stem)
        except (ValueError, KeyError, IndexError, struct.error) as error:
            report['failed'][stem] = str(error) or type(error).__name__
            continue
        scene['type'] = es1_section(stem)
        write_map(output, scene, binaries)
        report['imported'].append(stem)
        report['warnings'][stem] = scene['warnings']
    write_index(output)
    return report


def write_map(output, scene, binaries):
    target = output / 'maps' / scene['id']
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='importing-', dir=target.parent))
    for name, data in binaries.items():
        (staging / f'{name}.bin').write_bytes(data)
    (staging / 'scene.json').write_text(json.dumps(scene), encoding='utf-8')
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)


def import_maps(install, output, game='es2', missions=None, replace=False):
    """Import every mission of an Earthsiege 2 install, or the named ones (file stems such as C1_01)."""
    install, output = Path(install).expanduser(), Path(output)
    files = read_volumes(install) if install.is_dir() else {}
    if game == 'es1':
        return import_es1(files, install, output, missions, replace)
    if 'DAT/MAT0.DAT' not in files or not any(k.startswith('MSN/') for k in files):
        raise ValueError(f'Not an Earthsiege 2 folder (no VOL\\*.VOL with missions and zones): {install}')
    pack = Pack(files, output, 'es2')
    report = {'imported': [], 'skipped': [], 'failed': {}, 'warnings': {}}
    wanted = {m.upper() for m in missions} if missions else None
    for stem, stage, number, section in es2_missions(files):
        if wanted and stem.upper() not in wanted:
            continue
        if not replace and (output / 'maps' / stem.lower() / 'scene.json').is_file():
            report['skipped'].append(stem)
            continue
        try:
            scene, binaries = es2_scene(pack, stem, stage, number)
        except (ValueError, KeyError, IndexError, struct.error) as error:
            report['failed'][stem] = str(error) or type(error).__name__
            continue
        scene['type'] = section
        write_map(output, scene, binaries)
        report['imported'].append(stem)
        report['warnings'][stem] = scene['warnings']
    write_index(output)
    return report


def write_index(output):
    maps = []
    for path in sorted((output / 'maps').glob('*/scene.json')) if (output / 'maps').is_dir() else []:
        scene = json.loads(path.read_text(encoding='utf-8'))
        maps.append({'id': scene['id'], 'name': scene['mission'], 'type': scene['type'], 'warnings': len(scene['warnings'])})
    output.mkdir(parents=True, exist_ok=True)
    (output / 'index.json').write_text(json.dumps(sorted(maps, key=lambda m: (m['type'], m['name']))), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game', choices=('es1', 'es2'), default='es2')
    parser.add_argument('--install', type=Path, required=True, help='the game folder (its VOL volumes are read)')
    parser.add_argument('--mission', action='append', help='mission stem, e.g. C1_01; default: every mission')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--replace', action='store_true', help='re-import maps that already exist in the output')
    args = parser.parse_args()
    out = args.output or LOCAL_DATA / f'{args.game}-maps'
    result = import_maps(args.install, out, args.game, args.mission, args.replace)
    print('Imported %d, skipped %d existing, failed %d into %s' % (len(result['imported']), len(result['skipped']), len(result['failed']), out))
    for name, reason in result['failed'].items():
        print('Failed:', name, '-', reason)

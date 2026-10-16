"""Red Baron 3D terrain and single missions, from a local install (read only).

python tools/import_redbaron_map.py --install "C:/path/to/Red Baron 3D"

Writes local-data/rb3d-maps. See docs/redbaron-maps.md for the measured formats
and the preview's limits. Model/VOL/LZH/material/bitmap readers are shared with
the model importer; this importer never changes its catalog or edited textures.
"""
import argparse
from array import array
from collections import Counter
import hashlib
import io
import json
import math
from functools import lru_cache
from pathlib import Path
import re
import struct
import sys
import uuid

from PIL import Image

try:
    from tools.import_redbaron import SCALE, PARTS, read_volume, read_volumes, materials, build_model
    from tools.import_t1_map import bitmap_png, lzh_expand
    from tools.local_data import LOCAL_DATA
except ImportError:
    from import_redbaron import SCALE, PARTS, read_volume, read_volumes, materials, build_model
    from import_t1_map import bitmap_png, lzh_expand
    from local_data import LOCAL_DATA

# Baron.exe 00524190, origin arrays 0058A510/0058A528; IEEE float values,
# not rounded map coordinates. Native world units are the model's 1.5 inches.
ORIGINS = [(48.42499923706055, 2.516666889190674), (49.86344528198242, 1.8193249702453613),
           (48.33509826660156, 4.545241832733154), (47.70577621459961, 5.856603145599365),
           (48.42499923706055, 2.516666889190674)]
DEGREE_UNITS = 2915860.391  # double at 0056C8D8
REGIONS = ('Marne', 'Flanders', 'Verdun', 'Alsace', 'Multiplayer')
INSTALL = Path(r'C:\Program Files (x86)\Steam\steamapps\common\Red Baron\Red Baron 3D')


class Reader:
    def __init__(self, data):
        self.data, self.at = data, 0

    def take(self, size):
        if size < 0 or self.at + size > len(self.data):
            raise ValueError(f'Truncated record at {self.at:#x}, need {size} bytes')
        result = self.data[self.at:self.at + size]
        self.at += size
        return result

    def unpack(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))

    def count(self, limit=65536):
        value, = self.unpack('I')
        if value > limit:
            raise ValueError(f'Count {value} exceeds {limit} at {self.at - 4:#x}')
        return value

    def end(self):
        if self.at != len(self.data):
            raise ValueError(f'{len(self.data) - self.at} unparsed bytes at {self.at:#x}')


def dat_rows(data, width):
    """Dynamix table: 100-byte header, u16 count/stride at 20/22."""
    if len(data) < 100:
        raise ValueError('Truncated DAT header')
    count, stride = struct.unpack_from('<HH', data, 20)
    if stride != width or len(data) != 100 + count * stride:
        raise ValueError(f'Expected DAT rows of {width} bytes, found {count} x {stride}')
    return [data[100 + i * width:100 + (i + 1) * width] for i in range(count)]


def resource_aliases(files, data):
    """EXCEPT.DAT maps numeric resource IDs; fill absent named resources only."""
    aliases = dict(struct.unpack('<II', row) for row in dat_rows(data, 8))
    count = 0
    for source in aliases:
        target, seen = source, set()
        while target in aliases and aliases[target] != target:
            if target in seen:
                raise ValueError('Cyclic EXCEPT.DAT resource alias')
            seen.add(target)
            target = aliases[target]
        for extension in ('.bmp', '.dts', '.dml', '.pab'):
            name, other = f'{source:08x}{extension}', f'{target:08x}{extension}'
            if name not in files and other in files:
                files[name] = files[other]
                count += 1
    return count


def terrain_material(name, season='s'):
    """0043F39B..0043F41A: replace an s/f/w prefix, otherwise prepend it."""
    name = name.lower()
    return season + (name[1:] if name[:1] in ('s', 'f', 'w') else name)


def multiplayer_files(install, files):
    """BARON4 uses Baronmmp's bank, without changing the single-player mount.

    Baronmmp.exe 00480F32..00480F5A mounts rb.vol in slot 6, rbmulti in 5,
    rbmmulti in 4. Lower slots win; the 3D patch still has higher priority.
    """
    found = {p.name.lower(): p for p in Path(install).rglob('*.vol') if p.is_file()}
    result = dict(files)
    for name in ('rbmulti.vol', 'rbmmulti.vol', '3dpatch.vol'):
        if name in found:
            result.update(read_volume(found[name]))
    return result


class Ted:
    """Numeric VOL directory. Read only the requested block, never treat vols as names."""
    def __init__(self, path):
        self.path = Path(path)
        self.entries, self.blocks = {}, {}
        length = self.path.stat().st_size
        with self.path.open('rb') as f:
            head = f.read(24)
            if len(head) != 24 or head[:4] != b'VOL ' or head[8:16] != b'volh\0\0\0\x80' or head[16:20] != b'vols':
                raise ValueError('Not a numeric Red Baron TED volume')
            names_size = int.from_bytes(head[20:23], 'little')
            if names_size < 4 or names_size + 32 > length:
                raise ValueError('Invalid vols size')
            f.seek(24 + names_size)
            chunk = f.read(8)
            index_size = int.from_bytes(chunk[4:7], 'little')
            if chunk[:4] != b'voli' or index_size % 14 or index_size > 14 * 65536:
                raise ValueError('Invalid numeric volume index')
            directory = f.read(index_size)
            if len(directory) != index_size:
                raise ValueError('Truncated numeric volume index')
            for ident, offset, size, compression, named in struct.iter_unpack('<IIIBB', directory):
                if not offset:
                    continue
                if ident in self.entries or named or compression not in (0, 3) or offset + 8 > length or size > 32 * 1024 * 1024:
                    raise ValueError(f'Unsupported numeric volume entry {ident}')
                self.entries[ident] = (offset, size, compression)
        h = self.read(100)
        if len(h) != 52 or struct.unpack_from('<I', h)[0] != 100:
            raise ValueError('Unsupported TED world header')
        self.scale = struct.unpack_from('<I', h, 8)[0]
        self.bounds = struct.unpack_from('<4i', h, 20)
        if not 0 <= self.scale <= 20 or self.bounds[2] <= self.bounds[0] or self.bounds[3] <= self.bounds[1]:
            raise ValueError('Invalid TED world dimensions')
        r = Reader(self.read(200))
        if r.count() != 100:
            raise ValueError('Unsupported TED directory version')
        rows = [r.unpack('12I') for _ in range(r.count(4096))]
        self.info = {}
        for row in rows:
            names = []
            for flag in (row[0], row[10]):
                raw = r.take(r.count(4096)) if flag else b''
                names.append(raw.split(b'\0')[0].decode('cp1252').replace('\\', '/').rsplit('/', 1)[-1].lower())
            if row[1] in self.info:
                raise ValueError('Duplicate terrain block ID')
            self.info[row[1]] = dict(name=names[0], material=names[1])
        r.end()
        self.root = self.block(300)

    def read(self, ident):
        if ident not in self.entries:
            raise ValueError(f'Missing TED block {ident:#x}')
        offset, size, compression = self.entries[ident]
        with self.path.open('rb') as f:
            f.seek(offset)
            chunk = f.read(8)
            packed = int.from_bytes(chunk[4:7], 'little')
            if chunk[:4] != b'VBLK' or packed > 32 * 1024 * 1024:
                raise ValueError(f'Bad VBLK {ident:#x}')
            data = f.read(packed)
        if len(data) != packed:
            raise ValueError(f'Truncated VBLK {ident:#x}')
        if compression:
            try:
                data = lzh_expand(data, 0, size)[0]
            except (IndexError, struct.error) as exc:
                raise ValueError(f'Truncated compressed TED block {ident:#x}') from exc
        if len(data) != size:
            raise ValueError(f'Incorrect VBLK size {ident:#x}')
        return data

    def block(self, ident):
        if ident in self.blocks:
            return self.blocks[ident]
        r = Reader(self.read(ident))
        version, stored_id, selected, _, base, top, low, high, shared = r.unpack('9I')
        if version != 100 or stored_id != ident or not 0 <= base <= top <= 16 or shared not in (0, 1):
            raise ValueError(f'Unsupported terrain block header {ident}')
        heights = None if shared else r.unpack('256i')
        inherit_material, count = r.unpack('2I')
        r.end()
        if count != top - base + 1 or selected >= count:
            raise ValueError(f'Invalid level count {ident}')
        r = Reader(self.read(ident | 0x40000))
        if r.count() != 100:
            raise ValueError(f'Unsupported terrain data version {ident}')
        descriptors = [r.unpack('6I') for _ in range(count)]
        levels = []
        for _, nodes, sx, sy, regular, stride in descriptors:
            if nodes > 65536 or sx > 16 or sy > 16 or regular not in (0, 1):
                raise ValueError(f'Invalid terrain level {ident}')
            levels.append(dict(data=r.take(nodes * 32), nodes=nodes, sx=sx, sy=sy, regular=regular))
        r.end()
        block = dict(id=ident, base=base, levels=levels, heights=heights, selected=selected,
                     inheritMaterial=bool(inherit_material),
                     material=self.info.get(ident, {}).get('material', ''))
        self.blocks[ident] = block
        return block

    def root_cells(self):
        """Top-level cells as (x, y, native width, block, level, record offset)."""
        b = self.root
        level = len(b['levels']) - 1
        desc = b['levels'][level]
        if not desc['regular']:
            raise ValueError('Sparse root terrain level')
        width, height = 2 << desc['sx'], 2 << desc['sy']
        step = 1 << (self.scale + b['base'] + level)
        for y in range(height):
            for x in range(width):
                offset = ((y // 2) * (width // 2) + x // 2) * 32 + ((y & 1) * 2 + (x & 1)) * 8
                yield self.bounds[0] + x * step, self.bounds[1] + y * step, step, b, level, offset

    def leaves(self, x, y, step, block, level, offset, material='sbaron.dml', ancestry=(),
               binding=None, height_table=None, vertices=None, parent=None):
        """Finest squares: xy/width, heights, DML/slot, texture square, flags.

        004FA901..004FAA0A: byte 6 selects a material; FF inherits the ancestor
        texture square. The low 14 reference bits are NOT a material index.
        004C4AE0/004C4F30: preserve coarse vertices; INT_MAX midpoints use the
        endpoint average (centre uses the lower diagonal), truncated toward zero.
        """
        key = (block['id'], level, offset)
        if key in ancestry or len(ancestry) > 40:
            raise ValueError('Cyclic terrain reference')
        desc = block['levels'][level]
        if offset < 0 or offset + 8 > len(desc['data']):
            raise ValueError(f'Terrain node outside block {block["id"]}')
        value, a, b, c, d, slot, flags = struct.unpack_from('<H6B', desc['data'], offset)
        # 0043F799 skips the literal default name; that block inherits its bank.
        own_material = block['material'] and block['material'] != 'baron.dml'
        material = terrain_material(block['material']) if own_material else material
        height_table = block['heights'] if block['heights'] is not None else height_table
        if height_table is None:
            raise ValueError('No inherited height table')
        if vertices is None:
            vertices = {}
        coords = [(x, y), (x, y + step), (x + step, y + step), (x + step, y)]
        heights = []
        for point, code in zip(coords, (a, b, c, d)):
            if point not in vertices:
                h = height_table[code]
                if h == 0x7fffffff:
                    if parent is None:
                        raise ValueError('Unresolved root height sentinel')
                    px, py, size, ph = parent
                    dx, dy = point[0] - px, point[1] - py
                    # Only subdivision midpoints may introduce this sentinel.
                    halves = {(0, size // 2): ph[0] + ph[1],
                              (size // 2, size): ph[1] + ph[2],
                              (size, size // 2): ph[2] + ph[3],
                              (size // 2, 0): ph[3] + ph[0],
                              (size // 2, size // 2): min(ph[0] + ph[2], ph[1] + ph[3])}
                    if (dx, dy) not in halves:
                        raise ValueError('Height sentinel is not a subdivision midpoint')
                    total = halves[dx, dy]
                    h = (abs(total) // 2) * (-1 if total < 0 else 1)
                vertices[point] = h
            heights.append(vertices[point])
        kind, index = value >> 14, value & 0x3fff
        if slot != 255 and kind != 2:
            binding = (material, slot, (x, y, step))
        if kind == 0:
            if binding is None:
                raise ValueError('Surface has no ancestor material')
            yield (x, y, step, heights, binding[0], binding[1], binding[2], flags)
        elif kind == 1:
            if level == 0 or step < 2:
                raise ValueError('Terrain subdivides past its finest level')
            for q in range(4):
                yield from self.leaves(x + (q & 1) * step // 2, y + (q >> 1) * step // 2, step // 2,
                                       block, level - 1, index * 32 + q * 8, material, ancestry + (key,),
                                       binding, height_table, vertices, (x, y, step, heights))
        elif kind == 2:
            child = self.block(index)
            lev = len(child['levels']) - 1
            grid = child['levels'][lev]
            width, height = 2 << grid['sx'], 2 << grid['sy']
            child_step = 1 << (self.scale + child['base'] + lev)
            if not grid['regular'] or width * child_step != step or height * child_step != step:
                raise ValueError(f'Block {index} does not fit its parent square')
            # 004F9140: a block with its own DML resets inherited material state.
            if child['material'] and child['material'] != 'baron.dml':
                binding = None
            for cy in range(height):
                for cx in range(width):
                    at = ((cy // 2) * (width // 2) + cx // 2) * 32 + ((cy & 1) * 2 + (cx & 1)) * 8
                    yield from self.leaves(x + cx * child_step, y + cy * child_step, child_step,
                                           child, lev, at, material, ancestry + (key,), binding,
                                           height_table, vertices, (x, y, step, heights))


def parse_mis(data):
    r = Reader(data)
    reserved, player, slot, count = r.unpack('8sIBI')
    if reserved != bytes(8) or count > 200 or slot >= 12:
        raise ValueError('Unsupported MIS header')
    flights = []
    for _ in range(count):
        h = r.take(216)
        fields = struct.unpack_from('<8I', h)
        waypoints = struct.unpack_from('<I', h, 192)[0]
        if waypoints > 30 or fields[7] > 12:
            raise ValueError('Invalid MIS flight count')
        link = r.unpack('i')[0] if struct.unpack_from('<I', h, 204)[0] else None
        points = []
        for _ in range(waypoints):
            w = r.take(46)
            ident, _, _, x, y, ground, time, altitude = struct.unpack_from('<8i', w)
            # 0052F81D..0052F838 and 0042B4E6 load altitude from +28, not +20.
            points.append(dict(id=ident, position=[x, y, altitude], groundHeight=ground, time=time,
                               formation=w[34], nearFormation=w[40], action=w[41],
                               target=struct.unpack_from('<i', w, 42)[0]))
        selected, = r.unpack('i')
        aircraft = r.unpack('12i')
        pilots = r.unpack('12i')
        states = [list(r.take(3)) if r.unpack('i')[0] >= 0 else None for _ in range(12)]
        flights.append(dict(id=fields[0], squadron=fields[1], kind=fields[5], time=fields[6], count=fields[7],
                            header180=struct.unpack_from('<I', h, 180)[0], link=link, waypoints=points,
                            selected=selected, pilots=list(pilots), aircraft=list(aircraft), states=states))
    if not any(f['id'] == player for f in flights):
        raise ValueError('MIS player flight is missing')
    # 0043E0E5..0043E1F7 stops after the declared flights and resolves links;
    # it does not require EOF. Retail MISSION9/C/D contain unused suffix bytes.
    result = dict(player=player, playerSlot=slot, flights=flights,
                  parsedBytes=r.at, trailingBytes=len(data) - r.at)
    if result['trailingBytes']:
        result['trailingDataSha256'] = hashlib.sha256(data[r.at:]).hexdigest()
    return result


def read_pilot(r):
    """Serialized pilot, 004E69B0/004E6D70/004E7140; variable dated histories."""
    head = r.take(79)
    state, date_value = r.unpack('2I')
    r.take(39)
    squadron, = r.unpack('I')
    r.take(5)
    for _ in range(2):
        r.take(r.count(10000) * 16)
    r.take(29)
    return dict(id=struct.unpack_from('<I', head)[0], squadron=squadron, state=state, date=date_value)


def parse_dat(data, brigade_counts=None):
    r = Reader(data)
    active, day, time = r.unpack('BII')
    pilot = read_pilot(r)
    region, weather = r.unpack('IB')
    if active not in (0, 1) or region >= len(ORIGINS) or time >= 86400000:
        raise ValueError('Unsupported mission DAT world settings')
    result = dict(savedState=bool(active), day=day, time=time, region=region, weather=weather, pilot=pilot, stateOffset=r.at,
                  stateBytes=len(data) - r.at)
    if brigade_counts is None:
        return result
    # 004C8C00/004C9680, 004C2170, 004EF710, 004CF090. Each present
    # layout serializes exactly one state record per immediate child.
    states = []
    for _ in range(r.count(4096) if active else 0):
        ident, active, flag1, flag2, day_value = r.unpack('IBBBI')
        children = []
        if active and r.unpack('B')[0]:
            if ident not in brigade_counts:
                raise ValueError(f'DAT state references unknown brigade {ident}')
            for _ in range(brigade_counts[ident]):
                state, value, blob = r.unpack('IIB')
                raw = r.take(r.count(262144)) if blob else b''
                children.append(dict(state=state, value=value, data=raw.hex()))
        states.append(dict(id=ident, active=active, flags=[flag1, flag2], day=day_value, children=children))
    result['brigadeStates'] = states
    result['pilots'] = [read_pilot(r) for _ in range(r.count(4096) if result['savedState'] else 0)]
    # 004EFB44/004E4AE9 stop after declared records; MISSION1.DAT contains
    # 516 trailing bytes despite a zero extra-pilot count. Do not spawn them.
    result['stateBytes'] = len(data) - r.at
    if result['stateBytes']:
        result['trailingDataSha256'] = hashlib.sha256(data[r.at:]).hexdigest()
    return result


def geographic(latitude, longitude, region):
    lat, lon = ORIGINS[region]
    return ((longitude - lon) * math.cos(latitude * 0.017453292522222223) * DEGREE_UNITS,
            (latitude - lat) * DEGREE_UNITS)


def landmarks(data, region, category=2):
    result = []
    for row in dat_rows(data, 63):
        ident, kind, variant, side, lat, lon, altitude, heading = struct.unpack_from('<iBBB4f', row)
        if not all(math.isfinite(v) for v in (lat, lon, altitude, heading)):
            raise ValueError('Non-finite landmark coordinate')
        x, y = geographic(lat, lon, region)
        result.append(dict(id=ident, kind=kind, variant=variant, side=side,
                           name=row[23:63].split(b'\0')[0].decode('cp1252').strip(),
                           position=[x, y, altitude * 8], heading=heading,
                           layout=f'lo{category:02x}{kind:02x}{variant:02x}.dat'))
    return result


def layout(data):
    result = []
    for row in dat_rows(data, 20):
        kind, index, variant, x, y, z, heading, flags = struct.unpack('<BBB4fB', row)
        if not all(math.isfinite(v) for v in (x, y, z, heading)):
            raise ValueError('Non-finite layout coordinate')
        result.append(dict(shape=f'{kind:02x}{index:02x}80{variant:02x}.dts',
                           layout=f'lo{kind:02x}{index:02x}{variant:02x}.dat',
                           position=[x * 8, y * 8, z * 8], heading=heading, flags=flags))
    return result


def formations(data):
    """004D1860: four bytes, eleven (1-based parent, bearing, elevation, feet)."""
    return [dict(minimum=row[0], maximum=row[1], preferred=row[2], pairsOnly=bool(row[3]),
                 offsets=[struct.unpack_from('<B3f', row, 4 + i * 13) for i in range(11)])
            for row in dat_rows(data, 147)]


def formation_offset(formation, slot, ancestry=()):
    # 0052F7D2 initializes spacing index 2. 00585CD0 = .75,1,2,3,4.
    # 004D17A0/004D17E0 resolve parents, 00523A00 converts spherical feet;
    # 0042B896 converts the rotated result to native units by multiplying by 8.
    if slot == 0:
        return [0, 0, 0]
    if not 0 < slot <= 11 or slot in ancestry:
        raise ValueError('Invalid/cyclic aircraft formation')
    parent, bearing, elevation, distance = formation['offsets'][slot - 1]
    if not 1 <= parent <= 12 or not all(math.isfinite(v) for v in (bearing, elevation, distance)):
        raise ValueError('Invalid formation offset')
    base = formation_offset(formation, parent - 1, ancestry + (slot,))
    az, el = math.radians(bearing), math.radians(elevation)
    radius = distance * 2 * 8
    return [base[0] + math.sin(az) * math.cos(el) * radius,
            base[1] + math.cos(az) * math.cos(el) * radius, base[2] + math.sin(el) * radius]


def flight_position(flight, time):
    points = flight['waypoints']
    if not points:
        raise ValueError(f'Flight {flight["id"]} has no waypoints')
    a, b = points[0], points[min(1, len(points) - 1)]
    for left, right in zip(points, points[1:]):
        a, b = left, right
        if time <= right['time']:
            break
    t = max(0, min(1, (time - a['time']) / (b['time'] - a['time']))) if b['time'] > a['time'] else 0
    position = [p + (q - p) * t for p, q in zip(a['position'], b['position'])]
    heading = math.atan2(b['position'][0] - a['position'][0], b['position'][1] - a['position'][1])
    return position, heading


def matrix(position, heading=0, camera=False):
    """Native x/y/z -> model importer's -x/z/y; heading clockwise from north.

    A camera looks down -Z, a model's nose is +Z.
    """
    angle = -heading + (math.pi if camera else 0)
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = position
    return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, -x * SCALE, z * SCALE, y * SCALE, 1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(',', ':'), ensure_ascii=True), encoding='utf-8')


def palette(data):
    if data[:4] == b'RIFF' and data[8:16] == b'PAL data':
        rgba = data[24:1048]
    elif data[:4] == b'PPAL':
        r = Reader(data[8:])
        rgba = b''
        while r.at < len(r.data):
            tag, size = r.unpack('4sI')
            body = r.take(size)
            if tag == b'data':
                rgba = body
                break
    else:
        raise ValueError('Unsupported RB3D palette')
    if len(rgba) != 1024:
        raise ValueError('Expected 256 palette colours')
    return [value for i, value in enumerate(rgba) if i % 4 != 3]


class Assets:
    def __init__(self, files, output, report):
        self.files, self.output, self.report = files, output, report
        self.colours = palette(files['summer.pal']())
        # 00444BB7/004ED33D: fog palette index from 005830A4, all five banks 47.
        fog_palette = palette(files['rb3dgnd.spl']()) if 'rb3dgnd.spl' in files else self.colours
        self.haze = fog_palette[47 * 3:48 * 3]
        self.pngs, self.models, self.dmls = {}, {}, {}

    def texture(self, name, alpha=False):
        key = (name, alpha)
        if key in self.pngs:
            return self.pngs[key]
        if name not in self.files:
            self.report['missingTextures'].add(name)
            self.pngs[key] = None
            return None
        try:
            data = bytearray(self.files[name]())
            if alpha and data[:4] == b'PBMP' and data[8:12] == b'head':
                data[32:36] = struct.pack('<i', 1)
            png = bitmap_png(bytes(data), {'summer': (self.colours, [255] * 256)}, alpha=alpha)
            if alpha and data[:2] == b'BM':
                with Image.open(io.BytesIO(data)) as source:
                    if source.mode == 'P':
                        mask = Image.frombytes('L', source.size, source.tobytes()).point([0] + [255] * 255)
                        image = source.convert('RGBA')
                        image.putalpha(mask)
                        out = io.BytesIO()
                        image.save(out, format='PNG')
                        png = out.getvalue()
            target = hashlib.sha256(png).hexdigest()[:24] + '.png'
            (self.output / 'textures').mkdir(parents=True, exist_ok=True)
            (self.output / 'textures' / target).write_bytes(png)
            self.pngs[key] = target
        except (ValueError, OSError, struct.error, IndexError) as exc:
            self.report['missingTextures'].add(f'{name}: {exc}')
            self.pngs[key] = None
        return self.pngs[key]

    def sky_ring(self):
        """The horizon cloud band per weather, hz-cld<n>.bmp (its own cloud palette) with hz-cld<n>.aph as alpha. 004B41E0
        loads hz-cld<weather>, else hz-cld0; the 3D patch ships 0-5. None when the install has neither."""
        if not hasattr(self, '_sky_ring'):
            names = []
            for number in range(6):
                bmp, aph = self.files.get(f'hz-cld{number}.bmp'), self.files.get(f'hz-cld{number}.aph')
                if not bmp or not aph:
                    names.append(names[0] if names else None)
                    continue
                with Image.open(io.BytesIO(bmp())) as colour, Image.open(io.BytesIO(aph())) as alpha:
                    image = colour.convert('RGBA')
                    image.putalpha(alpha.convert('L'))
                out = io.BytesIO()
                image.save(out, format='PNG')
                target = hashlib.sha256(out.getvalue()).hexdigest()[:24] + '.png'
                (self.output / 'textures').mkdir(parents=True, exist_ok=True)
                (self.output / 'textures' / target).write_bytes(out.getvalue())
                names.append(target)
            self._sky_ring = names
        return self._sky_ring

    def solid(self, rgb):
        name = 'rgb_' + ''.join(f'{c:02x}' for c in rgb) + '.png'
        path = self.output / 'textures' / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (1, 1), tuple(rgb)).save(path)
        return name

    def material(self, name):
        if name not in self.dmls:
            if name not in self.files:
                raise ValueError('Missing material list ' + name)
            self.dmls[name] = materials(self.files[name]())
        return self.dmls[name]

    def slot(self, mat):
        kind, value, flags = mat
        if kind == 1:
            if not 0 <= value < 256:
                raise ValueError('Palette index outside 0..255')
            return self.solid(self.colours[value * 3:value * 3 + 3])
        if kind == 2:
            return self.solid((value & 255, value >> 8 & 255, value >> 16 & 255))
        if kind != 3:
            raise ValueError(f'Unsupported material kind {kind}')
        return self.texture(value, bool(flags & 1))

    def model(self, name, squadron=None):
        key = name, squadron
        if key in self.models:
            return self.models[key]
        try:
            if name not in self.files:
                raise ValueError('not in volumes')
            dml_name = name[:4] + '9' + name[5:-4] + '.dml'
            mats = self.material(dml_name)
            modes = material_shading(self.files[dml_name]())
            def slot(index):
                if not 0 <= index < len(mats):
                    raise ValueError('Shape material index out of range')
                kind, value, flags = mats[index]
                if kind == 3:
                    if flags & 2:
                        return None
                    part = value.rsplit('.', 1)[0]
                    if part in PARTS and name.startswith('03'):
                        paint = f'03{name[2:4]}{PARTS[part]:02x}'
                        preferred = paint + f'{squadron:02x}.bmp' if squadron is not None else ''
                        candidates = sorted(n for n in self.files if re.fullmatch(paint + r'[0-9a-f]{2}\.bmp', n))
                        value = preferred if preferred in self.files else next(iter(candidates), preferred or paint + '00.bmp')
                        if preferred and value != preferred:
                            self.report['warnings'].add(f'{name}: paint {preferred} absent; using {value}')
                    return self.slot((kind, value, flags)) or self.solid((255, 0, 255))
                return self.slot(mats[index])
            def shaded_slot(index):
                texture = slot(index)
                return f'{modes[index]}:{texture}' if texture else None
            model = build_model(self.files[name](), shaded_slot, lambda index: modes[index] == 2)
            for group in model['groups']:
                group['shade'] = int(model['material_textures'][group['materialIndex']].split(':', 1)[0])
            for field in ('material_names', 'material_textures'):
                model[field] = [n.split(':', 1)[1] for n in model[field]]
            if not model['indices']:
                raise ValueError('no supported visible geometry')
            target = name[:-4] + (f'_{squadron:02x}' if squadron is not None else '') + '.json'
            write_json(self.output / 'models' / target, model)
            self.models[key] = target
        except (ValueError, IndexError, struct.error, OSError) as exc:
            self.report['missingShapes'].add(f'{name}: {exc}')
            self.models[key] = None
        return self.models[key]


def placed_objects(landmark, assets, ancestry=()):
    """Expand geographic landmarks through their LO composite layouts."""
    name = landmark['layout']
    if name in ancestry or len(ancestry) > 12:
        raise ValueError('Cyclic object layout ' + name)
    if name not in assets.files:
        assets.report['missingShapes'].add('layout ' + name)
        return []
    out = []
    angle = math.radians(landmark['heading'])
    c, s = math.cos(angle), math.sin(angle)
    for child in layout(assets.files[name]()):
        x, y, z = child['position']
        parent = landmark['position']
        child['position'] = [parent[0] + x * c + y * s, parent[1] + y * c - x * s, parent[2] + z]
        child['heading'] += landmark['heading']
        if child['layout'] in assets.files:
            out.extend(placed_objects(child, assets, ancestry + (name,)))
        else:
            # 004FCDA1..004FCDA3: class 08/type 0a never creates a drawable,
            # even if a matching resource exists. These are simulation objects.
            no_drawable = child['shape'].startswith('080a80')
            if no_drawable:
                model = None
                assets.report['warnings'].add(child['shape'] + ': native class 08/type 0a has no static drawable')
            else:
                model = assets.model(child['shape'])
            out.append(dict(name=landmark.get('name', child['shape']), model=model,
                            matrix=matrix(child['position'], math.radians(child['heading'])),
                            sourceShape=child['shape'], flags=child['flags'], nativeNoDrawable=no_drawable))
    return out


LANDMARK_TYPES = {0: 'Airfield', 1: 'Town', 2: 'Military depot', 3: 'Rail yard',
                  4: 'Bridge', 5: 'Factory', 7: 'Village', 8: 'Village'}


def landmark_jump(place, objects):
    """Frame the placed layout from above and behind, looking at its centre.

    Placement extents plus a 100 m margin accommodate buildings around the
    origin (airfield origins can be in the middle of an empty runway).
    """
    points = [o['matrix'][12:15] for o in objects if o['model']]
    if not points:
        points = [matrix(place['position'])[12:15]]
    low = [min(p[i] for p in points) for i in range(3)]
    high = [max(p[i] for p in points) for i in range(3)]
    target = [(a + b) / 2 for a, b in zip(low, high)]
    radius = max(250, math.dist(low, high) / 2 + 100)
    heading = math.radians(place['heading'])
    offset = [2 * math.sin(heading), 1.2, -2 * math.cos(heading)]
    eye = [p + radius * d for p, d in zip(target, offset)]
    length = math.sqrt(sum(d * d for d in offset))
    back = [d / length for d in offset]
    horizontal = math.hypot(back[0], back[2])
    right = [back[2] / horizontal, 0, -back[0] / horizontal]
    up = [back[1] * right[2], back[2] * right[0] - back[0] * right[2], -back[1] * right[0]]
    return dict(name=place['name'], id=place['id'], kind=place['kind'],
                type=LANDMARK_TYPES.get(place['kind'], f"Landmark type {place['kind']}"),
                target=target, radius=radius,
                matrix=[*right, 0, *up, 0, *back, 0, *eye, 1])


def material_shading(data):
    """Serialized DML +16 -> material +1C: 0 unlit, 1 flat, 2 smooth."""
    at, modes = 16, []
    for _ in range(struct.unpack_from('<I', data, 8)[0]):
        modes.append(struct.unpack_from('<I', data, at + 16)[0])
        size = struct.unpack_from('<I', data, at + 4)[0]
        at += 8 + size + (size & 1)
    return modes


def terrain_triangles(heights):
    """005437D0: split across the lower pair of opposite corners."""
    return ((0, 1, 3), (1, 2, 3)) if heights[0] + heights[2] > heights[1] + heights[3] else ((0, 1, 2), (0, 2, 3))


class TerrainHeights:
    """Bounded, lazy height queries including the adjacent root cells.

    Retain nine decoded cells, not a second copy of a whole front. Interpolate
    across coarse squares when a finer neighbour asks for a midpoint normal.
    """
    def __init__(self, ted):
        self.ted = ted
        self.cells = {(c[0], c[1]): c for c in ted.root_cells()}
        self.size = next(iter(self.cells.values()))[2]
        self.cell = lru_cache(maxsize=9)(self._cell)

    def _cell(self, key):
        leaves = list(self.ted.leaves(*self.cells[key]))
        squares = {(v[0], v[1], v[2]): v[3] for v in leaves}
        return leaves, squares

    def height(self, x, y):
        x0, y0, x1, y1 = self.ted.bounds
        if not x0 <= x <= x1 or not y0 <= y <= y1:
            return None
        cx = x0 + min((x - x0) // self.size, (x1 - x0) // self.size - 1) * self.size
        cy = y0 + min((y - y0) // self.size, (y1 - y0) // self.size - 1) * self.size
        if (cx, cy) not in self.cells:
            return None
        _, squares = self.cell((cx, cy))
        step, sx, sy = self.size, cx, cy
        while step:
            h = squares.get((sx, sy, step))
            if h is not None:
                u, v = (x - sx) / step, (y - sy) / step
                if h[0] + h[2] > h[1] + h[3]:
                    return (h[0] + u * (h[3] - h[0]) + v * (h[1] - h[0]) if u + v <= 1 else
                            h[2] + (1-u) * (h[1] - h[2]) + (1-v) * (h[3] - h[2]))
                return (h[0] + v * (h[1] - h[0]) + u * (h[2] - h[1]) if v >= u else
                        h[0] + u * (h[3] - h[0]) + v * (h[2] - h[3]))
            step //= 2
            if x >= sx + step:
                sx += step
            if y >= sy + step:
                sy += step
        return None


def terrain_normals(leaves, height_at=None, spacing=None):
    """004C42D0: central height differences, missing neighbour = this height."""
    vertices = {}
    for x, y, step, heights, *_ in leaves:
        for point, height in zip(((x, y), (x, y+step), (x+step, y+step), (x+step, y)), heights):
            old = vertices.get(point)
            if old is None or step < old[1]:
                vertices[point] = height, step
    def sample(x, y, fallback):
        value = height_at(x, y) if height_at else vertices.get((x, y), (None,))[0]
        return fallback if value is None else value
    result = {}
    for (x, y), (h, step) in vertices.items():
        step = spacing or step
        dx = (sample(x-step, y, h) - sample(x+step, y, h)) / 2
        dy = (sample(x, y-step, h) - sample(x, y+step, h)) / 2
        length = math.sqrt(dx*dx + dy*dy + step*step)
        result[x, y] = (-dx / length, step / length, dy / length)
    return result


def terrain_chunk(leaves, assets, path, height_at=None, spacing=None):
    """One bounded chunk: float32 xyz/uv/normal, six vertices per square.

    DML variants use mirror/rotate bits 3/4/5. FF material inheritance keeps
    the ancestor's texture footprint rather than repeating it on every leaf.
    """
    leaves = list(leaves)
    normals = terrain_normals(leaves, height_at, spacing)
    groups = {}
    flags_seen = Counter()
    for x, y, step, heights, dml, index, texture_square, flags in leaves:
        mats = assets.material(dml)
        if index >= len(mats):
            raise ValueError(f'{dml}: square material {index} outside {len(mats)} slots')
        mat = mats[index]
        texture = assets.slot(mat) or assets.solid((255, 0, 255))
        orientation = mat[2] if mat[0] == 3 else 0
        coords = [(x, y), (x, y + step), (x + step, y + step), (x + step, y)]
        tx, ty, width = texture_square
        uv = [((px - tx) / width, 1 - (py - ty) / width) for px, py in coords]
        if orientation & 8:
            uv = [(1 - u, v) for u, v in uv]
        if orientation & 16:
            uv = [(u, 1 - v) for u, v in uv]
        if orientation & 32:
            uv = [(v, 1 - u) for u, v in uv]
        values = groups.setdefault(texture, array('f'))
        for corner in (c for triangle in terrain_triangles(heights) for c in triangle):
            px, py = coords[corner]
            values.extend((-px * SCALE, heights[corner] * SCALE, py * SCALE, *uv[corner], *normals[px, py]))
        flags_seen[flags] += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    slots, start = [], 0
    with path.open('wb') as f:
        for texture, values in sorted(groups.items()):
            count = len(values) // 8
            if sys.byteorder != 'little':
                values.byteswap()
            f.write(values.tobytes())
            slots.append(dict(texture=texture, start=start, count=count))
            start += count
    return dict(groups=slots, vertices=start, squares=start // 6, stride=8,
                squareFlags=dict(flags_seen))


PREVIEW_LIMITS = [
    'Summer only: fall and winter replace ground palettes and shading tables as well as bitmaps',
    'Native sunlight and distance haze use the default maximum visibility; animated sky and weather effects are not reproduced',
    'Terrain lighting uses the exported finest height field; native distance-dependent mesh LOD still needs comparison',
    'Landmark layouts are placed; procedural ground clutter and date-dependent front-line objects are not yet reproduced',
]


def native_lighting(world, start_time=None):
    """Baron.exe's celestial model and Glide light, not a modern sun ephemeris.

    004AAF90 (clock), 004D0A10 / 00486390 (sun/moon), 004ABAC0
    (latitude rotation), 0049E9C0 (brightness), 00409CF0 (light intensity).
    Keep the single-precision epoch store: at Julian-day magnitudes it matters.
    """
    f32 = lambda n: struct.unpack('<f', struct.pack('<f', n))[0]
    latitude, longitude = ORIGINS[world['region']]
    local = world['time'] * f32(.001) + longitude * 240
    total = world['day'] * 86400 + local
    epoch = f32(total)
    year_rate = f32(3.1688763613146875e-8)
    rotation = ((total * year_rate - local * f32(1 / 86400) + f32(.536916)) % 1) * 360
    if start_time is not None:
        rotation -= (start_time - world['time']) / 1000 / 240
    phase = (epoch * year_rate + f32(.5285826921463013)) % 1
    angle = f32(phase * 360)
    declination = math.cos(angle * .017453292522222223) * 23.5

    def body(azimuth, elevation):
        # 00523AD0 / 00523C70, specialized to (0, latitude-90, rotation).
        a, b, d = map(math.radians, (azimuth-rotation, latitude-90, elevation))
        x = math.sin(a) * math.cos(d)
        y = math.cos(b) * math.cos(a) * math.cos(d) - math.sin(b) * math.sin(d)
        z = math.sin(b) * math.cos(a) * math.cos(d) + math.cos(b) * math.sin(d)
        altitude = math.degrees(math.asin(max(-1, min(1, z))))
        az = math.atan2(x, y)
        return az, altitude

    azimuth, elevation = body(angle, declination)
    moon_angle = ((epoch * f32(4.236246979871794e-7) + .0625) % 1) * 360
    _, moon_elevation = body(moon_angle, 0)

    def brightness(altitude):
        h = altitude + 3
        if h > 2:
            return .75 + .25 * math.sin((h-2) / 91 * math.pi/2)
        if h > -2:
            return .5 + .25 * math.sin((h+2) / 4 * math.pi/2)
        if h > -11.25:
            return .5 * math.sin((h+11.25) / 9.25 * math.pi/2)
        return 0

    intensity = min(1, brightness(elevation) + .2 * brightness(moon_elevation) + .05)
    # The light direction stops descending at 11.25 degrees (0049EADC).
    altitude = math.radians(max(11.25, elevation))
    toward = [-math.sin(azimuth) * math.cos(altitude), math.sin(altitude), math.cos(azimuth) * math.cos(altitude)]
    return dict(model='baron-glide', toward=toward, ambient=.5, intensity=intensity,
                sunElevation=elevation, sunAzimuth=math.degrees(azimuth) % 360,
                day=world['day'], time=world['time'] if start_time is None else start_time,
                weather=world['weather'], visibility=1, terrainScale=11,
                fogColourIndex=47, fogColourSource='ground SPL palette',
                distanceModel='radial per-vertex 16-bit ramp, >>7, saturated at 255',
                shading='Glide smooth terrain normals; DML unlit/flat/smooth shapes')


def scene_base(name, assets):
    return dict(mission=name, haze=assets.haze,
                terrain=dict(visibleDistance=147456*SCALE, hazeDistance=103219*SCALE),
                sun=dict(ambient=[1, 1, 1], intensity=[0, 0, 0], azimuth=0, incidence=0),
                viewpoints=[], objects=[], warnings=list(PREVIEW_LIMITS), season='summer', skyRing=assets.sky_ring(),
                lighting=native_lighting(dict(day=2421745, time=43200000, region=0, weather=1)))


def export_region(path, region, assets):
    ted = Ted(path)
    lnd_name = f'lndmrks{region}.dat'
    if lnd_name not in assets.files:
        raise ValueError('Missing geographic placements ' + lnd_name)
    places = landmarks(assets.files[lnd_name](), region)
    brigade_name = f'brigade{region}.dat'
    brigades = landmarks(assets.files[brigade_name](), region, 1) if brigade_name in assets.files else []
    objects, jumps = [], []
    for place in places:
        placed = placed_objects(place, assets)
        objects.extend(placed)
        jumps.append(landmark_jump(place, placed))
    chunks, total, assigned = [], 0, set()
    heights = TerrainHeights(ted)
    for number, cell in enumerate(ted.root_cells()):
        x, y, step = cell[:3]
        name = f'regions/{region}/{number}'
        geometry = terrain_chunk(heights.cell((x, y))[0], assets, assets.output / (name + '.bin'),
                                 heights.height, 1 << ted.scale)
        chunk_objects = [o for o in objects if x <= -o['matrix'][12] / SCALE < x + step
                         and y <= o['matrix'][14] / SCALE < y + step]
        geometry['objects'] = chunk_objects
        assigned.update(id(obj) for obj in chunk_objects)
        write_json(assets.output / (name + '.json'), geometry)
        chunks.append(dict(path=name, bounds=[-(x + step) * SCALE, y * SCALE, -x * SCALE, (y + step) * SCALE]))
        total += geometry['squares']
    outside = [obj for obj in objects if id(obj) not in assigned]
    if outside:
        assets.report['warnings'].add(f'BARON{region}.TED: {len(outside)} objects outside root cells; retained in region manifest')
    centre = [(ted.bounds[0] + ted.bounds[2]) / 2, (ted.bounds[1] + ted.bounds[3]) / 2, 1500 / SCALE]
    result = dict(id=region, name=REGIONS[region], chunks=chunks, jumps=jumps, terrainScale=ted.scale,
                  lighting=native_lighting(dict(day=2421745, time=43200000, region=region, weather=1)),
                  origin=ORIGINS[region], bounds=ted.bounds,
                  brigades=brigades, outsideObjects=outside,
                  viewpoint=jumps[0]['matrix'] if jumps else matrix(centre, camera=True),
                  counts=dict(squares=total, chunks=len(chunks), landmarks=len(places), objects=len(objects),
                              nonDrawableObjects=sum(o['nativeNoDrawable'] for o in objects),
                              outsideObjects=len(outside), volumeEntries=len(ted.entries), decodedBlocks=len(ted.blocks)))
    write_json(assets.output / f'regions/{region}/index.json', result)
    return result


def export_mission(mis_path, companions, regions, assets, formation_data):
    mission = parse_mis(mis_path.read_bytes())
    stem = mis_path.stem.lower()
    dat_path = companions.get(stem + '.dat')
    if not dat_path:
        raise ValueError('Missing companion .DAT')
    world = parse_dat(dat_path.read_bytes())
    region = world['region']
    if region not in regions:
        raise ValueError(f'BARON{region}.TED was not imported')
    brigades = regions[region].get('brigades', [])
    counts = {b['id']: len(dat_rows(assets.files[b['layout']](), 20)) for b in brigades if b['layout'] in assets.files}
    world = parse_dat(dat_path.read_bytes(), counts)
    shapes = formations(formation_data)
    player = next(f for f in mission['flights'] if f['id'] == mission['player'])
    # 0052F350 advances from the earliest flight until the player's birth.
    # Preview the authored birth instant; the simulation's first tick and
    # special takeoff/linked-flight paths remain a game-comparison gate.
    start_time = player['time']
    result = scene_base(mis_path.stem, assets)
    result['lighting'] = native_lighting(world, start_time)
    result['lighting']['terrainScale'] = regions[region].get('terrainScale', 11)
    result.update(region=region, regions=[dict(id=region, name=REGIONS[region])], world=world, flights=mission['flights'])
    result['missionSource'] = {k: v for k, v in mission.items() if k not in ('flights', 'player', 'playerSlot')}
    result['warnings'].extend([
        'Opens at the authored player flight start; simulation warm-up, takeoff and linked-flight adjustments remain unverified',
        'Brigade placements use their geographic layouts; serialized damage and campaign movement are retained but not applied',
    ])
    result['startTime'] = start_time
    if mission['trailingBytes']:
        result['warnings'].append(f'MIS has {mission["trailingBytes"]} trailing bytes beyond its declared flights; ignored by the native loader')
    if world['stateBytes']:
        result['warnings'].append(f'DAT has {world["stateBytes"]} trailing bytes beyond its declared records; not applied')
    result['objects'].extend(obj for brigade in brigades for obj in placed_objects(brigade, assets))
    result['scheduledFlights'] = []
    for flight in mission['flights']:
        if flight['time'] > start_time:
            result['scheduledFlights'].append(flight['id'])
            continue
        pos, heading = flight_position(flight, start_time)
        point = next((p for p in flight['waypoints'] if p['time'] >= start_time), flight['waypoints'][-1])
        form = point['nearFormation'] if math.dist(pos[:2], point['position'][:2]) < 1 else point['formation']
        for slot in range(flight['count']):
            aircraft = flight['aircraft'][slot]
            model = assets.model(f'03{aircraft:02x}8000.dts', flight['squadron']) if 0 <= aircraft <= 255 else None
            if model is None:
                assets.report['missingShapes'].add(f'{mis_path.name}: flight {flight["id"]} slot {slot} aircraft {aircraft}')
            if slot and form >= len(shapes):
                raise ValueError(f'Unsupported formation {form} in flight {flight["id"]}')
            offset = formation_offset(shapes[form], slot) if slot else [0, 0, 0]
            c, s = math.cos(heading), math.sin(heading)
            position = [pos[0] + offset[0] * c + offset[1] * s,
                        pos[1] + offset[1] * c - offset[0] * s, pos[2] + offset[2]]
            result['objects'].append(dict(name=f'Flight {flight["id"]}, aircraft {slot + 1}', model=model,
                                          matrix=matrix(position, heading), flight=flight['id'], slot=slot,
                                          player=flight['id'] == mission['player'] and slot == mission['playerSlot']))
            if flight['id'] == mission['player'] and slot == mission['playerSlot']:
                result['viewpoints'].insert(0, matrix(position, heading, camera=True))
        if flight['id'] != mission['player']:
            result['viewpoints'].append(matrix(pos, heading, camera=True))
    if not result['viewpoints'] or mission['playerSlot'] >= player['count']:
        raise ValueError('Player aircraft slot is outside the flight')
    dsc = companions.get(stem + '.dsc')
    if dsc:
        text = dsc.read_bytes().split(b'\0', 1)[0].decode('cp1252').strip()
        result['briefing'] = text[:4000] + ('\n[Briefing truncated]' if len(text) > 4000 else '')
    return result


def import_maps(install, output=None, replace=False):
    """Publish a new, versioned pack only after writing its report and manifests.

    A failed refresh cannot overwrite files used by the currently visible pack.
    Old generations remain available to already-open pages; no automatic cleanup.
    """
    install = Path(install).resolve()
    output = Path(output or LOCAL_DATA / 'rb3d-maps').resolve()
    if not install.is_dir():
        raise ValueError('Red Baron 3D install folder does not exist')
    if output == install or install in output.parents or output in install.parents:
        raise ValueError('The map output must be separate from the read-only install')
    if (output / 'index.json').exists() and not replace:
        return dict(imported=[], skipped=['Western Front and single missions'], failed={})
    source_data = next((p for p in install.iterdir() if p.is_dir() and p.name.lower() == 'data'), None)
    if source_data is None:
        raise ValueError('Missing Data folder')
    sources = {p.name.lower(): p for p in source_data.iterdir() if p.is_file()}
    single = next((p for p in install.iterdir() if p.is_dir() and p.name.lower() == 'single'), None)
    companions = {p.name.lower(): p for p in single.iterdir() if p.is_file()} if single else {}
    generation = 'packs/' + uuid.uuid4().hex
    pack = output / generation
    report = dict(teds={}, missions={}, missingShapes=set(), missingTextures=set(), warnings=set(),
                  campaign='Skipped: campaign missions are generated at runtime',
                  seasons='Summer only; fall/winter also replace rb3dgnd.pal and rb3dgnd.spl',
                  limitations=PREVIEW_LIMITS, complete=False)
    teds = {f'baron{i}.ted' for i in range(5)} | {n for n in sources if n.endswith('.ted')}
    regions, failed = {}, {}
    startup_error = None
    try:
        files = read_volumes(install)
        if 'summer.pal' not in files:
            raise ValueError('Missing summer.pal in the model volume mount order')
        report['resourceAliases'] = resource_aliases(files, sources['except.dat'].read_bytes()) if 'except.dat' in sources else 0
        assets = Assets(files, pack, report)
    except (ValueError, OSError, struct.error, IndexError, KeyError) as exc:
        startup_error = 'Shared asset setup failed: ' + str(exc)
    for name in sorted(teds):
        try:
            if name not in sources:
                raise ValueError('File absent')
            if startup_error:
                raise ValueError(startup_error)
            match = re.fullmatch(r'baron([0-4])\.ted', name)
            if not match:
                raise ValueError('Unknown TED region; no measured geographic origin')
            region = int(match[1])
            region_assets = Assets(multiplayer_files(install, files), pack, report) if region == 4 else assets
            data = export_region(sources[name], region, region_assets)
            regions[region] = data
            report['teds'][name] = dict(status='imported', region=REGIONS[region], **data['counts'])
        except (ValueError, OSError, struct.error, IndexError, KeyError) as exc:
            failed[name] = str(exc)
            report['teds'][name] = dict(status='failed', reason=str(exc))
    index, imported = [], []
    if regions:
        scene = scene_base('Western Front', assets)
        scene.update(regions=[dict(id=i, name=r['name']) for i, r in regions.items()], region=next(iter(regions)))
        scene['viewpoints'] = [regions[scene['region']]['viewpoint']]
        write_json(pack / 'maps/western_front/scene.json', scene)
        index.append(dict(id='western_front', name='Western Front', type='summer preview', pack=generation))
        imported.append('western_front')
    for name, path in sorted(companions.items()):
        if not name.endswith('.mis'):
            continue
        ident = re.sub('[^a-z0-9_-]', '_', path.stem.lower())
        try:
            if startup_error:
                raise ValueError(startup_error)
            if 'formatns.dat' not in sources:
                raise ValueError('Missing Data/FORMATNS.DAT')
            scene = export_mission(path, companions, regions, assets, sources['formatns.dat'].read_bytes())
            write_json(pack / f'maps/{ident}/scene.json', scene)
            index.append(dict(id=ident, name=path.stem, type='mission preview', pack=generation))
            imported.append(ident)
            report['missions'][name] = dict(status='imported', fidelity='incomplete', region=scene['region'],
                                           flights=len(scene['flights']), aircraft=sum('flight' in o for o in scene['objects']),
                                           groundObjects=sum('flight' not in o for o in scene['objects']),
                                           nonDrawableObjects=sum(o.get('nativeNoDrawable', False) for o in scene['objects']),
                                           scheduledFlights=scene['scheduledFlights'],
                                           missionSource=scene['missionSource'],
                                           unresolvedStateBytes=scene['world']['stateBytes'])
        except (ValueError, OSError, struct.error, IndexError, KeyError, OverflowError) as exc:
            failed[name] = str(exc)
            report['missions'][name] = dict(status='failed', reason=str(exc))
            index.append(dict(id=ident, name=path.stem, type='failed', error=str(exc), pack=generation))
    if not single:
        report['warnings'].add('Single folder absent; no missions found')
    for key in ('warnings', 'missingShapes', 'missingTextures'):
        report[key] = sorted(report[key])
    write_json(pack / 'report.json', report)
    write_json(output / 'report.json', dict(report, pack=generation))
    if imported:
        write_json(output / 'index.new.json', index)
        (output / 'index.new.json').replace(output / 'index.json')
    return dict(imported=imported, skipped=[], failed=failed, report='report.json', complete=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', type=Path, default=INSTALL)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'rb3d-maps')
    parser.add_argument('--replace', action='store_true', help='Publish a new generation; keep the old pack intact')
    args = parser.parse_args()
    try:
        result = import_maps(args.install, args.output, args.replace)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(result, indent=2))
    return int(bool(result['failed']))


if __name__ == '__main__':
    sys.exit(main())

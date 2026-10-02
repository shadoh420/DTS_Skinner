"""Import Tribes 1 missions into local map packs for the T1 map viewer; never modifies the install.

python tools/import_t1_map.py --game-base C:/Tribes/base                    # every mission in the install
python tools/import_t1_map.py --game-base C:/Tribes/base --mission My.mis   # a custom mission file or folder

Reads both zip volumes with PNG textures and classic PVOL volumes with palettised PBMP bitmaps.
Format reading follows the DarkStar layouts used by ArenaPrototype's Tribes map compatibility
layer (TribesLzh, TribesTerrainBlock, TribesMissionRotation, TribesResourceCatalog) and, for
PBMP, SurfaceLevel2's loader.
"""
import argparse
import contextlib
import functools
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_interior  # noqa: E402
from interior_module import dml as interior_dml, interiorshape  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# LZH static position prefixes: code length -> (first code, last code, subtract for upper 6 bits).
POSITION_CODES = {3: (0, 0, 0), 4: (2, 4, 1), 5: (10, 17, 6), 6: (36, 47, 24), 7: (96, 119, 72), 8: (240, 255, 192)}


def lzh_expand(data, offset, length):
    """DarkStar LZH block (LZHUF: 4096 window, adaptive Huffman). Returns (bytes, bytes consumed)."""
    window_size, lookahead, threshold = 4096, 60, 2
    symbols = 256 - threshold + lookahead
    table = symbols * 2 - 1
    root = table - 1
    frequency = [1] * symbols + [0] * (table + 1 - symbols)
    child = [index + table for index in range(symbols)] + [0] * (table - symbols)
    parent = [0] * (table + symbols)
    for index in range(symbols):
        parent[index + table] = index
    leaf = 0
    for node in range(symbols, table):
        frequency[node] = frequency[leaf] + frequency[leaf + 1]
        child[node] = leaf
        parent[leaf] = parent[leaf + 1] = node
        leaf += 2
    frequency[table] = 0xffff
    parent[root] = 0
    position, buffer, bits = offset, 0, 0

    def bit():
        nonlocal position, buffer, bits
        while bits <= 8:
            buffer |= data[position] << (8 - bits)
            position += 1
            bits += 8
        value = buffer >> 15
        buffer = (buffer << 1) & 0xffff
        bits -= 1
        return value

    def rebuild():
        count = 0
        for index in range(table):
            if child[index] >= table:
                frequency[count] = (frequency[index] + 1) // 2
                child[count] = child[index]
                count += 1
        leaf = 0
        for node in range(symbols, table):
            total = frequency[leaf] + frequency[leaf + 1]
            insert = node
            while insert > 0 and total < frequency[insert - 1]:
                insert -= 1
            frequency[insert + 1:node + 1] = frequency[insert:node]
            child[insert + 1:node + 1] = child[insert:node]
            frequency[insert] = total
            child[insert] = leaf
            leaf += 2
        for node in range(table):
            parent[child[node]] = node
            if child[node] < table:
                parent[child[node] + 1] = node

    output = bytearray()
    window = bytearray(b' ' * window_size)
    write = window_size - lookahead
    try:
        while len(output) < length:
            node = child[root]
            while node < table:
                node = child[node + bit()]
            symbol = node - table
            if frequency[root] == 0x8000:
                rebuild()
            node = parent[symbol + table]
            while True:
                frequency[node] += 1
                value = frequency[node]
                swap = node + 1
                if value > frequency[swap]:
                    swap += 1
                    while value > frequency[swap]:
                        swap += 1
                    swap -= 1
                    frequency[node], frequency[swap] = frequency[swap], value
                    first, second = child[node], child[swap]
                    parent[first] = swap
                    if first < table:
                        parent[first + 1] = swap
                    child[swap] = first
                    parent[second] = node
                    if second < table:
                        parent[second + 1] = node
                    child[node] = second
                    node = swap
                node = parent[node]
                if not node:
                    break
            if symbol < 256:
                output.append(symbol)
                window[write] = symbol
                write = (write + 1) & (window_size - 1)
                continue
            code = 0
            for size in range(1, 9):
                code = (code << 1) | bit()
                low, high, base = POSITION_CODES.get(size, (1, 0, 0))
                if low <= code <= high:
                    upper = code - base
                    break
            else:
                raise ValueError('Invalid LZH position code')
            for _ in range(6):
                upper = (upper << 1) | bit()
            read = write - upper - 1
            for index in range(symbol - 255 + threshold):
                if len(output) == length:
                    break
                value = window[(read + index) & (window_size - 1)]
                output.append(value)
                window[write] = value
                write = (write + 1) & (window_size - 1)
    except IndexError:
        raise ValueError('Truncated LZH block') from None
    return bytes(output), position - offset


def open_volume(path):
    """Members of a zip or PVOL volume as {lowercase file name: function returning its bytes}."""
    if zipfile.is_zipfile(path):
        archive = zipfile.ZipFile(path)
        return {Path(entry.filename).name.lower(): (lambda entry=entry: archive.read(entry))
                for entry in archive.infolist() if not entry.is_dir()}
    data = path.read_bytes()
    try:
        if data[:4] != b'PVOL':
            raise ValueError
        names_at = struct.unpack_from('<I', data, 4)[0]
        if data[names_at:names_at + 4] != b'vols':
            raise ValueError
        names_size = struct.unpack_from('<I', data, names_at + 4)[0]
        names = data[names_at + 8:names_at + 8 + names_size].split(b'\0')[:-1]
        index_at = names_at + 8 + names_size + (names_size & 1)
        if data[index_at:index_at + 4] != b'voli' or struct.unpack_from('<I', data, index_at + 4)[0] != len(names) * 17:
            raise ValueError
        members = {}
        for number, name in enumerate(names):
            offset, size, compression = struct.unpack_from('<IIB', data, index_at + 8 + number * 17 + 8)
            if data[offset:offset + 4] != b'VBLK':
                raise ValueError
            members[name.decode('cp1252').lower()] = lambda entry=(offset + 8, size, compression): read_block(data, *entry)
        return members
    except (ValueError, struct.error):
        raise ValueError('Unsupported volume: ' + path.name) from None


def read_block(data, offset, size, compression):
    """One PVOL member: stored, run-length or LZH compressed."""
    if compression == 0:
        return data[offset:offset + size]
    if compression == 3:
        return lzh_expand(data, offset, size)[0]
    if compression != 1:
        raise ValueError('Unsupported volume compression %d' % compression)
    output = bytearray()
    while len(output) < size:
        control, count = data[offset], data[offset] & 0x7f
        if control & 0x80:
            output += data[offset + 1:offset + 2] * count
            offset += 2
        else:
            output += data[offset + 1:offset + 1 + count]
            offset += 1 + count
    return bytes(output)


def read_terrain_block(data):
    """GBLK terrain block: heights, material (flags, index) pairs and 4-bit RGB lightmap.

    Version 5 is the game's own (LZH-compressed arrays); version 0, written by newer editors, stores them raw.
    """
    magic, _, version, _, _, light_scale, lowest, highest, size_x, size_y = struct.unpack_from('<4sIi16siiffii', data)
    if magic != b'GBLK' or version not in (0, 5) or size_x != size_y or light_scale < 0:
        raise ValueError('Unsupported terrain block')
    offset = 52

    def compressed(expected):
        nonlocal offset
        if not version:
            offset += expected
            if offset > len(data):
                raise ValueError('Truncated terrain block')
            return data[offset - expected:offset]
        if struct.unpack_from('<i', data, offset)[0] != expected:
            raise ValueError('Unexpected terrain block layout')
        expanded, consumed = lzh_expand(data, offset + 4, expected)
        offset += 4 + consumed
        return expanded

    heights = compressed((size_x + 1) ** 2 * 4)
    values = struct.unpack('<%df' % (len(heights) // 4), heights)
    if abs(min(values) - lowest) > .01 or abs(max(values) - highest) > .01:
        raise ValueError('Decoded terrain heights do not match the block height range')
    materials = compressed(size_x ** 2 * 2)
    for _ in range(11 if version else 0):  # Pin maps: level-of-detail hints, unused here.
        offset += 2 + struct.unpack_from('<H', data, offset)[0]
    light_width = (size_x << light_scale) + 1
    light = compressed(light_width ** 2 * 2)
    if not version and offset != len(data):  # A raw block is exactly its three arrays.
        raise ValueError('Unexpected terrain block layout')
    if not version and not any(light[1::2]):  # Raw blocks light with one 8-bit level per word; widen it to 4:4:4:4 grey.
        light = b''.join(struct.pack('<H', (level >> 4) * 0x1111) for level in light[::2])
    return {'size': size_x, 'heights': heights, 'materials': materials, 'lightWidth': light_width, 'light': light}


def read_terrain_index(data):
    """GFIL terrain index (.dtf): material list name, square scale and block grid."""
    if data[:4] != b'GFIL':
        raise ValueError('Unsupported terrain index')
    version, length = struct.unpack_from('<ii', data, 8)
    name = data[16:16 + length].decode('ascii')
    _, detail_count, scale = struct.unpack_from('<iii', data, 16 + length)
    columns, rows = struct.unpack_from('<ii', data, 16 + length + 12 + 24 + 8 + 8)
    offset = 16 + length + 60 + (4 if version == 1 else 0)
    block_map = struct.unpack_from('<%di' % (columns * rows), data, offset)
    return {'materialList': name, 'squares': 1 << (detail_count - 1), 'unit': 1 << scale,
            'columns': columns, 'rows': rows, 'blockMap': block_map}


def read_palettes(data):
    """PL98 palette set: ({palette id: flat RGB list}, haze RGB or None)."""
    if data[:4] != b'PL98':
        raise ValueError('Unsupported palette')
    count, _, _, haze = struct.unpack_from('<iiii', data, 4)
    palettes, colours = {}, b''
    for number in range(count):
        record = 52 + number * 1032
        rgba = data[record:record + 1024]
        colours = colours or rgba
        palettes[struct.unpack_from('<I', data, record + 1024)[0]] = [value for index, value in enumerate(rgba) if index % 4 != 3]
    return palettes, list(colours[haze * 4:haze * 4 + 3]) if 0 <= haze < 256 and colours else None


def bitmap_png(data, palettes):
    """PNG bytes for a PNG, Windows bitmap or DarkStar PBMP; indexed PBMPs take colours from the mission palette."""
    if data[:4] != b'PBMP':
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert('RGB')
    else:
        chunks, offset = {}, 8
        while offset + 8 <= len(data):
            size = struct.unpack_from('<I', data, offset + 4)[0]
            chunks[data[offset:offset + 4]] = data[offset + 8:offset + 8 + size]
            offset += 8 + size
        _, width, height, depth = struct.unpack_from('<4i', chunks[b'head'])
        palette = palettes.get(struct.unpack('<I', chunks[b'PiDX'])[0]) if b'PiDX' in chunks else None
        palette = palette or next(iter(palettes.values()), None)
        if depth != 8 or not palette:
            raise ValueError('Unsupported bitmap: %d-bit%s' % (depth, '' if palette else ', no palette'))
        # Top-down rows padded to four bytes; any further mip levels follow and are ignored.
        stride = (width + 3) & ~3
        image = Image.frombytes('P', (width, height), chunks[b'data'][:stride * height], 'raw', 'P', stride)
        image.putpalette(palette)
        image = image.convert('RGB')
    output = io.BytesIO()
    image.save(output, 'PNG')
    return output.getvalue()


@functools.lru_cache(maxsize=64)
def read_lighting(data):
    """ITRLighting or ITRMissionLighting (.dil, version 6 or 7), after ArenaPrototype's TribesInteriorLighting."""
    try:
        size = struct.unpack_from('<H', data, 8)[0]
        kind, offset = data[10:10 + size], 10 + size + (size & 1)
        if data[:4] != b'PERS' or kind not in (b'ITRLighting', b'ITRMissionLighting') or struct.unpack_from('<i', data, offset)[0] not in (6, 7):
            raise ValueError
        build, shift, _, *counts = struct.unpack_from('<8i', data, offset + 4)
        offset += 36
        tables = []
        # Light states (colour, time, data range), state data (surface, slot, intensity map), lights, surfaces.
        for layout, count in zip(('<4Hf2h', '<2hi', '<4ifI', '<i2h4B'), counts):
            size = struct.calcsize(layout) * count
            tables.append(list(struct.iter_unpack(layout, data[offset:offset + size])))
            offset += size
        maps = data[offset:offset + counts[4]]
        offset += counts[4]
        offset += 4 + struct.unpack_from('<I', data, offset)[0]  # Light names.
        nodes, leaves = [], ()
        offset += 1
        if data[offset - 1]:  # Huffman tree for compressed maps: (branch on 1, branch on 0), negative = leaf.
            node_count, leaf_count = struct.unpack_from('<2i', data, offset)
            nodes = list(struct.iter_unpack('<2i', data[offset + 8:offset + 8 + node_count * 8]))
            leaves = struct.unpack_from('<%dI' % leaf_count, data, offset + 8 + node_count * 8)
            offset += 8 + node_count * 8 + leaf_count * 4
        replaced = None
        if kind == b'ITRMissionLighting':  # Base map offset -> this file's map offset, LZH-compressed pairs.
            count = struct.unpack_from('<i', data, offset)[0]
            pairs, consumed = lzh_expand(data, offset + 4, count * 8)
            offset += 4 + consumed + (not count)  # An empty LZH stream is still one byte.
            replaced = dict(struct.iter_unpack('<2i', pairs))
        if offset != len(data):
            raise ValueError
    except (ValueError, IndexError, struct.error):
        raise ValueError('Unsupported interior lighting') from None
    return {'build': build & 0xffffffff, 'shift': shift, 'states': tables[0], 'stateData': tables[1], 'lights': tables[2],
            'surfaces': tables[3], 'maps': maps, 'nodes': nodes, 'leaves': leaves, 'replaced': replaced}


def light_colours(lighting, index):
    """4:4:4:4 texels of one surface light map: a packed colour, raw words or Huffman-coded words."""
    value, _, _, width, height, _, _ = lighting['surfaces'][index]
    if value < 0:
        return [value & 0xffff] * (width * height)
    start = value & ~0x40000000
    if not value & 0x40000000:
        return list(struct.unpack_from('<%dH' % (width * height), lighting['maps'], start))
    maps, nodes, leaves, bit, colours = lighting['maps'], lighting['nodes'], lighting['leaves'], start * 8, []
    for _ in range(width * height):
        node = 0
        while node >= 0:
            node = nodes[node][not maps[bit >> 3] >> (bit & 7) & 1]
            bit += 1
        colours.append(leaves[-node - 1] & 0xffff)
    return colours


@functools.lru_cache(maxsize=64)
def read_geometry(data):
    geometry = interiorshape.dig()
    geometry.load_binary(data)
    return geometry


def add_light(colour, red, green, blue):
    """Saturating add of 4-bit channels to a 4:4:4 light map texel."""
    return min(15, (colour >> 8 & 15) + red) << 8 | min(15, (colour >> 4 & 15) + green) << 4 | min(15, (colour & 15) + blue)


def bake_lightmap(geometry, base, mission=None, sunlight=None):
    """(atlas PNG, float32 atlas coordinates per exported vertex) for one placed interior.

    As ArenaPrototype's TribesUnityInteriorLightmaps: the mission's replacement maps over the base
    lighting, state 0 of every authored light added, one-texel gutters, coordinates from the
    geometry's native UV * texture size + map offset. Vertex order matches export_interior.
    A building the mission was never relit with has no replacement maps; the game then adds the sun
    to the faces flagged as visible from outside: sunlight(face normal) gives those 4-bit channels.
    """
    if geometry.build_id != base['build'] or mission and mission['build'] != base['build'] or len(geometry.surfaces) != len(base['surfaces']):
        raise ValueError('Interior lighting does not match its geometry')
    targets, replaced = {}, mission['replaced'] if mission else {}
    for index, surface in enumerate(mission['surfaces'] if mission else ()):
        if surface[0] >= 0:
            targets.setdefault(surface[0] & ~0x40000000, index)
    slots = {}  # Later state data replaces earlier in the same light-map slot.
    for _, _, count, first, _, _ in base['lights']:
        if count:
            *colour, _, _, data_count, data_index = base['states'][first]
            for surface, slot, start in base['stateData'][data_index:data_index + data_count]:
                slots.setdefault(surface, {})[slot] = ([(value + 128) >> 8 & 255 for value in colour[:3]], start)
    cells = []
    for index, surface in enumerate(geometry.surfaces):
        if surface.num_verts < 3:
            continue  # Not exported.
        value, _, _, width, height, _, _ = base['surfaces'][index]
        if not width * height:  # Surfaces the game does not draw.
            cells.append((index, 1, 1, [0xfff]))
            continue
        source, at = base, index
        if value >= 0 and value & ~0x40000000 in replaced:
            source, at = mission, targets[replaced[value & ~0x40000000]]
            if mission['surfaces'][at][3:5] != (width, height):
                raise ValueError('Mission light map does not match its surface')
        colours = light_colours(source, at)
        for (red, green, blue), start in slots.get(index, {}).values():
            for pixel, intensity in enumerate(base['maps'][start:start + len(colours)] if start >= 0 else ()):
                if intensity >= 16:
                    colours[pixel] = add_light(colours[pixel], red * intensity >> 12, green * intensity >> 12, blue * intensity >> 12)
        if sunlight and surface.flags & 0x40:
            plane, side = geometry.planes[surface.plane_id], 1 if surface.flags & 0x80 else -1
            sun = sunlight((plane.x * side, plane.y * side, plane.z * side))
            colours = [add_light(colour, *sun) for colour in colours]
        cells.append((index, width, height, colours))
    if not cells:
        raise ValueError('Interior has no surfaces')
    # Shelf packing, tallest first, into the smallest power-of-two width that is not taller than wide.
    cells.sort(key=lambda cell: -cell[2])
    width = 1 << (max(max(cell[1] for cell in cells) + 2, math.isqrt(sum((cell[1] + 2) * (cell[2] + 2) for cell in cells))) - 1).bit_length()
    while True:
        x = y = row = 0
        placed = {}
        for index, cell_width, cell_height, _ in cells:
            if x + cell_width + 2 > width:
                x, y, row = 0, y + row, 0
            placed[index] = (x + 1, y + 1)
            x += cell_width + 2
            row = max(row, cell_height + 2)
        height = y + row
        if height <= width:
            break
        width *= 2
    atlas = Image.new('RGB', (width, height))
    for index, cell_width, cell_height, colours in cells:
        cell = Image.frombytes('RGB', (cell_width, cell_height), bytes(
            channel * 17 for colour in colours for channel in (colour >> 8 & 15, colour >> 4 & 15, colour & 15)))
        # Pasting at the eight neighbours first leaves a gutter of repeated edge texels around the map.
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, -1), (0, 1), (-1, 0), (1, 0), (0, 0)):
            atlas.paste(cell, (placed[index][0] + dx, placed[index][1] + dy))
    scale, coordinates = 1 << base['shift'], []
    for index, surface in enumerate(geometry.surfaces):
        if surface.num_verts >= 3:
            _, _, _, map_width, map_height, left, top = base['surfaces'][index]
            for _, texture in geometry.verts[surface.vert_id:surface.vert_id + surface.num_verts]:
                u, v = geometry.points2f[texture]
                if not map_width * map_height:
                    u = v = left = top = 0
                coordinates += [(placed[index][0] + (u * (surface.tsx + 1) + left) / scale + .5) / width,
                                (placed[index][1] + (v * (surface.tsy + 1) + top) / scale + .5) / height]
    output = io.BytesIO()
    atlas.save(output, 'PNG', optimize=True)
    return output.getvalue(), struct.pack('<%df' % len(coordinates), *coordinates)


@functools.lru_cache(maxsize=None)
def vertex_count(model_path):
    return len(json.loads(model_path.read_text(encoding='utf-8'))['vertices']) // 3


def parse_mission(text):
    """Nested `instant Class "name" { key = "value"; };` objects up to the export end marker.

    DarkStar class and field names are case-insensitive, so both are stored lowercase.
    """
    root = {'class': 'root', 'name': '', 'fields': {}, 'children': []}
    stack = [root]
    for line in text.split('//--- export object end ---//')[0].splitlines():
        line = line.strip()
        opened = re.fullmatch(r'instant\s+(\w+)(?:\s+"([^"]*)")?\s*\{', line)
        field = re.fullmatch(r'([\w\[\]]+)\s*=\s*"(.*)";', line)
        if opened:
            node = {'class': opened[1].lower(), 'name': opened[2] or '', 'fields': {}, 'children': []}
            stack[-1]['children'].append(node)
            stack.append(node)
        elif line == '};':
            if len(stack) == 1:
                raise ValueError('Unbalanced mission file')
            stack.pop()
        elif field:
            stack[-1]['fields'][field[1].lower()] = field[2]
    if len(stack) != 1:
        raise ValueError('Unbalanced mission file')
    return root


def walk(node, groups=()):
    yield node, groups
    for item in node['children']:
        yield from walk(item, groups + (node['name'].lower(),))


def floats(text, count=3):
    values = [float(value) for value in text.split()]
    if len(values) != count or not all(math.isfinite(value) for value in values):
        raise ValueError('Expected %d finite numbers: %s' % (count, text))
    return values


def rotation_rows(rotation):
    """DarkStar Euler matrix: row k is where the object's k axis points in file space."""
    sx, sy, sz = (math.sin(value) for value in rotation)
    cx, cy, cz = (math.cos(value) for value in rotation)
    return ((cy * cz - sy * sz * sx, cy * sz + sy * cz * sx, -cx * sy),
            (-cx * sz, cx * cz, sx),
            (sy * cz + cy * sz * sx, sy * sz - cy * cz * sx, cx * cy))


def placement(position, rotation):
    """Three.js column-major matrix for a mission position and DarkStar Euler rotation (radians).

    DarkStar multiplies row vectors by its Euler matrix, so the file-space axes are the matrix
    rows. File (x, y, z-up) maps to viewer (x, z, -y), the basis the model JSON already uses.
    """
    rows = rotation_rows(rotation)
    viewer = lambda v: (v[0], v[2], -v[1])  # noqa: E731
    x_axis, y_axis, z_axis = viewer(rows[0]), viewer(rows[2]), [-value for value in viewer(rows[1])]
    return [*x_axis, 0, *y_axis, 0, *z_axis, 0, *viewer(position), 1]


class Install:
    """Read-only view of a Tribes `base` folder: loose files, volumes and script datablocks."""

    def __init__(self, game_base):
        game_base = Path(game_base)
        if (game_base / 'base').is_dir():
            game_base = game_base / 'base'
        if not game_base.is_dir():
            raise ValueError('Game folder not found: %s' % game_base)
        self.base = game_base
        self._folders, self._volumes, self._hashes, self._scripts, self._everything = {}, {}, {}, {}, None
        self.converted = {}  # (interior bytes hash, palette name) -> exported model file

    def files(self, folder):
        if folder not in self._folders:
            self._folders[folder] = {path.name.lower(): path for path in sorted(folder.iterdir()) if path.is_file()} if folder.is_dir() else {}
        return self._folders[folder]

    def find(self, name, folders):
        """A loose file by case-insensitive name; newer installs ship `.zip` where missions say `.vol`."""
        name = Path(name).name.lower()
        swapped = {'.vol': '.zip', '.zip': '.vol'}.get(Path(name).suffix)
        for folder in folders:
            for candidate in (name, Path(name).stem + swapped if swapped else name):
                if candidate in self.files(folder):
                    return self.files(folder)[candidate]
        return None

    def volume(self, path):
        if path not in self._volumes:
            self._volumes[path] = open_volume(path)
        return self._volumes[path]

    def sha256(self, path):
        if path not in self._hashes:
            with path.open('rb') as stream:
                self._hashes[path] = hashlib.file_digest(stream, 'sha256').hexdigest()
        return self._hashes[path]

    def everything(self):
        """Every member of every volume directly in the base folder, for resources a mission uses without mounting."""
        if self._everything is None:
            self._everything = {}
            for path in self.files(self.base).values():
                if path.suffix.lower() in ('.vol', '.zip'):
                    try:
                        for name, reader in self.volume(path).items():
                            self._everything.setdefault(name, reader)
                    except (ValueError, OSError, zipfile.BadZipFile):
                        pass
        return self._everything

    def shapes(self, mission_folder):
        """Datablock name -> shape file, read from every script the install and the mission folder carry."""
        def scan(text, found):
            for block in re.finditer(r'^[ \t]*\w+Data[ \t]+(\w+)\s*\{(.*?)^[ \t]*\};', text, re.S | re.M):
                shape = re.search(r'shapeFile\s*=\s*"([^"]+)"', block[2])
                if shape:
                    found[block[1].lower()] = Path(shape[1]).stem

        if self.base not in self._scripts:
            found = {}
            for name, reader in self.everything().items():
                if name.endswith('.cs'):
                    scan(reader().decode('cp1252', 'replace'), found)
            for path in sorted(self.base.rglob('*.cs')):
                scan(path.read_text(encoding='cp1252', errors='replace'), found)
            self._scripts[self.base] = found
        if mission_folder not in self._scripts:
            found = {}
            if self.base not in mission_folder.parents and mission_folder != self.base:
                for path in sorted(mission_folder.glob('*.cs')):
                    scan(path.read_text(encoding='cp1252', errors='replace'), found)
            self._scripts[mission_folder] = found
        return {**self._scripts[self.base], **self._scripts[mission_folder]}


def map_id(name):
    return re.sub(r'[^a-z0-9_-]', '_', name.lower())


def import_mission(install, mission_path, root, model_dir=ROOT / 'static/model_json'):
    """Write one mission's pack to root/maps/<id>; returns its scene. Shared textures go to root/textures."""
    mission = parse_mission(mission_path.read_text(encoding='cp1252', errors='replace'))
    nodes = list(walk(mission))
    folders = [mission_path.parent, install.base / 'missions', install.base]
    resources, provenance, warnings = {}, [], []
    for node, _ in nodes:
        if node['class'] != 'simvolume' or not node['fields'].get('filename'):
            continue
        path = install.find(node['fields']['filename'], folders)
        if not path:
            warnings.append('Volume not found: ' + node['fields']['filename'])
            continue
        resources.update(install.volume(path))  # Later volumes win.
        provenance.append({'volume': path.name, 'sha256': install.sha256(path)})

    def read(name):
        name = Path(name).name.lower()
        reader = resources.get(name) or install.everything().get(name)
        if reader:
            return reader()
        path = install.find(name, folders)
        if not path:
            raise ValueError('Required map resource missing: ' + name)
        return path.read_bytes()

    def first(kind):
        return next((node['fields'] for node, _ in nodes if node['class'] == kind), {})

    palettes, haze = {}, None
    try:
        palettes, haze = read_palettes(read(first('simpalette')['filename']))
    except (KeyError, ValueError, struct.error):
        pass  # Installs with PNG textures ship no palette.

    def store(data, suffix):
        """Shared, content-named file under root/textures, so identical data is stored once."""
        stored = hashlib.sha1(data).hexdigest()[:20] + suffix
        (root / 'textures').mkdir(parents=True, exist_ok=True)
        if not (root / 'textures' / stored).exists():
            (root / 'textures' / stored).write_bytes(data)
        return stored

    def texture(name):
        """Stored PNG for a material bitmap; None when the install has no such bitmap."""
        for candidate in (Path(name).stem + '.png', Path(name).stem + '.bmp'):
            try:
                return store(bitmap_png(read(candidate), palettes), '.png')
            except (ValueError, KeyError, OSError, struct.error):
                continue
        return None

    def material_names(name):
        materials = interior_dml.dml()
        materials.load_binary(read(name))
        return [material.name for material in materials.materials]

    terrain_node = first('simterrain')
    if 'tedfilename' not in terrain_node:
        raise ValueError('Mission has no terrain')
    # Missions mount their terrain volume by convention; one that does not still gets <terrain>.ted from beside it.
    terrain_volume = install.find(Path(terrain_node['tedfilename']).stem + '.ted', folders)
    if Path(terrain_node['tedfilename']).name.lower() not in resources and terrain_volume:
        resources.update(install.volume(terrain_volume))
        provenance.append({'volume': terrain_volume.name, 'sha256': install.sha256(terrain_volume)})
    index = read_terrain_index(read(terrain_node['tedfilename']))
    if len(set(index['blockMap'])) != 1:
        raise ValueError('Only terrains that repeat a single block are supported')
    block = read_terrain_block(read('%s#%d.dtb' % (Path(terrain_node['tedfilename']).stem, index['blockMap'][0])))
    # The block's own size is used: its arrays prove it, while some editors write a wrong detail count in the index.
    terrain_materials, textures = material_names(index['materialList']), {}
    for slot in sorted(set(block['materials'][1::2])):
        stored = texture(terrain_materials[slot]) if slot < len(terrain_materials) else None
        if stored:
            textures[slot] = stored
        else:
            warnings.append('Missing terrain texture: %s' % (terrain_materials[slot] if slot < len(terrain_materials) else 'slot %d' % slot))

    def convert(stem):
        """Export an interior the catalog lacks (custom map buildings) from the install into root/models."""
        try:
            source = read(stem + '.dis')
        except ValueError:
            return None
        key = (hashlib.sha1(source).hexdigest(), first('simpalette').get('filename', '').lower())
        if key not in install.converted:
            install.converted[key] = None
            with tempfile.TemporaryDirectory() as work:
                work = Path(work)
                try:
                    shape = interiorshape.interiorshape()
                    shape.load_binary(source)
                    (work / (stem + '.dis')).write_bytes(source)
                    stored = {}
                    for name in [item.decode('cp1252') for item in shape.get_dml_list()[:1] + shape.get_dig_list()]:
                        (work / name).write_bytes(read(name))
                    for name in material_names(shape.get_dml_list()[0].decode('cp1252')):
                        png = texture(name) if name.strip() else None
                        if png:  # The exporter reads texture sizes from PNG files beside the geometry.
                            stored[Path(name).stem + '.png'] = png
                            shutil.copyfile(root / 'textures' / png, work / (Path(name).stem + '.png'))
                    with contextlib.redirect_stdout(io.StringIO()):
                        export_interior.main(str(work / (stem + '.dis')), str(work), str(work), str(work))
                    model = json.loads((work / (stem + '.json')).read_text(encoding='utf-8'))
                    model['material_textures'] = [name if name.startswith('[') else stored.get(name, '') for name in model['material_textures']]
                    data = json.dumps(model).encode()
                    name = '%s-%s.json' % (map_id(stem), hashlib.sha1(data).hexdigest()[:12])
                    (root / 'models').mkdir(parents=True, exist_ok=True)
                    (root / 'models' / name).write_bytes(data)
                    install.converted[key] = name
                except Exception:  # The exporter is tolerant of stock files only; any failure just leaves a placeholder.
                    pass
        return install.converted[key]

    sun = next((node['fields'] for node, _ in nodes if node['class'] == 'planet' and node['fields'].get('castshadows', '').lower() == 'true'), {})
    sun = {'azimuth': float(sun.get('azimuth', 0)), 'incidence': float(sun.get('incidence', 45)),
           'intensity': floats(sun.get('intensity', '0.6 0.6 0.6')), 'ambient': floats(sun.get('ambient', '0.4 0.4 0.4'))}
    # Toward the sun in file space, as ArenaPrototype's PlanetPosition.
    turn, climb = math.radians(sun['azimuth'] + 90), math.radians(max(-89, min(89, sun['incidence'])))
    sun_direction = (math.cos(climb) * math.cos(turn), math.cos(climb) * math.sin(turn), math.sin(climb))

    def lightmap(instance, stem, rotation, model_path):
        """Atlas and its coordinates for a placed building; None keeps the plain mission-sun shading.

        The mission's lit instance (name.N.dis) carries the sun and shadows. Without one (lighting volume
        missing, or a building placed without relighting) the building's own lighting plus the sun is used.
        """
        def sunlight(normal):
            rows = rotation_rows(rotation)
            facing = (1 + sum(sum(normal[k] * rows[k][axis] for k in range(3)) * sun_direction[axis] for axis in range(3))) / 2
            return [min(15, int(max(0, ambient + intensity * facing) * 16)) if facing > 0 else 0
                    for ambient, intensity in zip(sun['ambient'], sun['intensity'])]

        for candidate in (instance, stem + '.dis'):
            try:
                shape = interiorshape.interiorshape()
                shape.load_binary(read(candidate))
                lod = max(shape.lods, key=lambda lod: lod.min_pixels)  # The detail level export_interior draws.
                name = lambda offset: shape.name_buffer[offset:shape.name_buffer.index(b'\0', offset)].decode('cp1252')  # noqa: E731
                lit = name(shape.lod_lightstate_offset[lod.light_state_index])
                base, mission = read_lighting(read(lit)), None
                if base['replaced'] is not None:
                    # name-<state><lod><light state>-<instance>.dil replaces maps of the building's own name-<...>.dil.
                    base, mission = read_lighting(read(re.sub(r'(-\d+)-[^-]*$', r'\1.dil', lit))), base
                png, coordinates = bake_lightmap(read_geometry(read(name(lod.geometry_file_offset))), base, mission,
                                                 None if mission or shape.linked_interior else sunlight)
                if len(coordinates) // 8 == vertex_count(model_path):  # Else the preview model is of other geometry.
                    return {'map': store(png, '.png'), 'uv': store(coordinates, '.uv')}
            except Exception:  # The interior readers are tolerant of stock files only; any failure tries the next source.
                pass
        return None

    models = {path.stem.lower(): path.stem for path in model_dir.glob('*.json')}
    shapes = install.shapes(mission_path.parent)
    objects, viewpoints, missing, unlit = [], [], set(), set()
    for node, groups in nodes:
        fields = node['fields']
        if 'position' not in fields or node['class'] == 'simterrain':
            continue
        matrix = placement(floats(fields['position']), floats(fields.get('rotation', '0 0 0')))
        if node['class'] == 'marker':
            if 'observerdroppoints' in groups:
                viewpoints.append(matrix)
            continue
        if node['class'] == 'interiorshape':
            shape, kind = re.sub(r'(\.\d+)?\.dis$', '', fields.get('filename', ''), flags=re.I), 'interior'
        else:
            shape, kind = shapes.get(fields.get('datablock', '').lower()), 'shape'
        if not shape:
            continue
        model, source = models.get(shape.lower()), 'catalog'
        if not model and kind == 'interior':
            model, source = convert(shape), 'pack'
        if not model:
            missing.add('%s %s' % (kind, shape.lower()))
        objects.append({'name': fields.get('name') or node['name'] or shape, 'model': model, 'source': source, 'matrix': matrix})
        if model and kind == 'interior':
            light = lightmap(fields['filename'], shape, floats(fields.get('rotation', '0 0 0')),
                             root / 'models' / model if source == 'pack' else model_dir / (model + '.json'))
            if light:
                objects[-1]['light'] = light
            else:
                unlit.add(shape.lower())
    warnings += ['No preview model for ' + item for item in sorted(missing)]
    if unlit:
        warnings.append('No lightmap, plain sun shading instead: ' + ', '.join(sorted(unlit)))

    if not viewpoints:  # Training and some custom missions have no observer cameras: look down on the placed objects.
        centre = first('missioncenterpos')
        x, y = (float(centre.get('x', 0)) + float(centre.get('w', 0)) / 2, float(centre.get('y', 0)) + float(centre.get('h', 0)) / 2)
        if objects:
            x, y = (sum(item['matrix'][12] for item in objects) / len(objects), -sum(item['matrix'][14] for item in objects) / len(objects))
        top = max(struct.unpack('<%df' % (len(block['heights']) // 4), block['heights']))
        viewpoints.append(placement((x, y - 150, top + 60), (-.5, 0, 0)))

    # Sky, as ArenaPrototype's TribesUnityEnvironment draws it: sixteen panels of the sky material list's textures
    # with caps in corner texels of the first one, or a plain sky colour for a mission without a material list.
    # Fog uses the palette haze colour, else the first texture's bottom-left texel, else the sky colour.
    sky = first('sky')
    try:
        dome = {'color': [round(max(0, min(1, value)) * 255) for value in floats(sky.get('skycolor', ''))]}
    except ValueError:
        dome = {'color': [128, 140, 150]}
    try:
        stored = [texture(name) for name in material_names(sky['dmlname'])]
        with Image.open(root / 'textures' / stored[0]) as image:
            image = image.convert('RGB')
            slots = [int(sky.get('textures[%d]' % slot, slot % 2)) for slot in range(16)]
            dome = {'textures': [None if value < 0 else stored[value % len(stored)] for value in slots],
                    'size': float(sky.get('size', 600)), 'feature': float(sky.get('featureposition', 0)),
                    'top': list(image.getpixel((0, 0))), 'bottom': list(image.getpixel((image.width - 1, image.height - 1)))}
            haze = haze or list(image.getpixel((0, image.height - 1)))
    except (KeyError, ValueError, IndexError, TypeError, OSError, struct.error):
        haze = haze or dome['color']
    weather = first('snowfall')  # Rain or snow; the game leaves it hidden unless the mission says otherwise.
    try:
        weather = {'rain': weather.get('rain', '').lower() == 'true', 'intensity': max(0, min(1, float(weather['intensity']))),
                   'wind': floats(weather.get('wind', '0 0 0'))} if weather.get('suspendrendering', '').lower() == 'false' else None
    except (KeyError, ValueError):
        weather = None

    description = install.find(mission_path.stem + '.dsc', folders)
    kind = re.search(r'\$MDESC::Type\s*=\s*"([^"]*)"', description.read_text(encoding='cp1252', errors='replace')) if description else None
    scene = {
        'id': map_id(mission_path.stem), 'mission': mission_path.stem, 'type': kind[1] if kind else '',
        'terrain': {'squares': block['size'], 'unit': index['unit'], 'columns': index['columns'], 'rows': index['rows'],
                    'position': floats(terrain_node.get('position', '0 0 0')), 'lightWidth': block['lightWidth'],
                    'textures': textures,
                    'visibleDistance': float(terrain_node.get('visibledistance', 500)),
                    'hazeDistance': float(terrain_node.get('hazedistance', 250))},
        'sun': sun,
        'haze': haze, 'sky': dome, 'weather': weather, 'objects': objects, 'viewpoints': viewpoints, 'warnings': warnings}

    # Build beside the target, then swap, so a failed import never leaves a half-written map.
    target = root / 'maps' / scene['id']
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='importing-', dir=target.parent))
    (staging / 'heights.bin').write_bytes(block['heights'])
    (staging / 'materials.bin').write_bytes(block['materials'])
    (staging / 'light.bin').write_bytes(block['light'])
    (staging / 'scene.json').write_text(json.dumps(scene), encoding='utf-8')
    (staging / 'SOURCES.json').write_text(json.dumps({
        'mission': {'file': mission_path.name, 'sha256': hashlib.sha256(mission_path.read_bytes()).hexdigest()},
        'policy': 'Volumes mounted by the mission, later volumes win; then other base volumes; then loose files.',
        'volumes': provenance}, indent=2), encoding='utf-8')
    if (target / 'scene.json').is_file():
        shutil.rmtree(target)
    staging.rename(target)
    return scene


def import_maps(game_base, output, missions=None, replace=False, model_dir=ROOT / 'static/model_json'):
    """Import every mission of an install, or the given mission files/folders. Returns a report."""
    install = Install(game_base)
    paths = []
    for item in [Path(item) for item in missions or [install.base / 'missions']]:
        found = sorted(item.glob('*.[mM][iI][sS]')) if item.is_dir() else [item] if item.is_file() and item.suffix.lower() == '.mis' else []
        if not found:
            raise ValueError('No mission files found: %s' % item)
        paths += found
    output = Path(output)
    report = {'imported': [], 'skipped': [], 'failed': {}, 'warnings': {}}
    for path in paths:
        if not replace and (output / 'maps' / map_id(path.stem) / 'scene.json').is_file():
            report['skipped'].append(path.stem)
            continue
        try:
            scene = import_mission(install, path, output, model_dir)
        except (ValueError, KeyError, IndexError, OSError, struct.error, zipfile.BadZipFile) as error:
            report['failed'][path.stem] = str(error) or type(error).__name__
            for leftover in (output / 'maps').glob('importing-*'):
                shutil.rmtree(leftover, ignore_errors=True)
            continue
        report['imported'].append(path.stem)
        if scene['warnings']:
            report['warnings'][path.stem] = scene['warnings']
    maps = []
    for scene_path in sorted((output / 'maps').glob('*/scene.json')) if (output / 'maps').is_dir() else []:
        scene = json.loads(scene_path.read_text(encoding='utf-8'))
        maps.append({'id': scene['id'], 'name': scene['mission'], 'type': scene['type'], 'warnings': len(scene['warnings'])})
    output.mkdir(parents=True, exist_ok=True)
    (output / 'index.json').write_text(json.dumps(sorted(maps, key=lambda item: item['name'].lower())), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game-base', type=Path, required=True, help='Tribes folder or its base folder')
    parser.add_argument('--mission', type=Path, action='append', help='mission file or folder of missions; default: every mission in the install')
    parser.add_argument('--output', type=Path, default=Path('local-data/t1-maps'))
    parser.add_argument('--replace', action='store_true', help='re-import maps that already exist in the output')
    args = parser.parse_args()
    result = import_maps(args.game_base, args.output, args.mission, args.replace)
    print('Imported %d, skipped %d existing, failed %d into %s' % (
        len(result['imported']), len(result['skipped']), len(result['failed']), args.output))
    for name, reason in result['failed'].items():
        print('Failed:', name, '-', reason)
    for name, items in result['warnings'].items():
        for item in items:
            print('Warning:', name, '-', item)

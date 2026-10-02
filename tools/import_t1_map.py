"""Prepare the stock Raindance map pack from a Tribes 1 install; never modify the install.

python tools/import_t1_map.py --game-base C:/realstocktribe/base
Format reading follows the DarkStar layouts used by ArenaPrototype's Tribes map
compatibility layer (TribesLzh, TribesTerrainBlock, TribesMissionRotation).
"""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import re
import struct
import sys
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from interior_module import dml as interior_dml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MISSION = 'Raindance'
# ponytail: stock base datablocks only; scan the install's scripts for shapeFile when modded maps are added.
DATABLOCK_SHAPES = {
    'flag': 'flag', 'repairpack': 'armorPack', 'pulsesensor': 'radar', 'generator': 'generator',
    'ammostation': 'ammounit', 'commandstation': 'cmdpnl', 'inventorystation': 'inventory_sta',
    'vehiclestation': 'vehi_pur_pnl', 'vehiclepad': 'vehi_pur_poles', 'plasmaturret': 'hellfiregun',
    'rocketturret': 'missileturret'}
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


def read_terrain_block(data):
    """GBLK version 5 terrain block: heights, material (flags, index) pairs and 4-bit RGB lightmap."""
    magic, _, version, _, _, light_scale, lowest, highest, size_x, size_y = struct.unpack_from('<4sIi16siiffii', data)
    if magic != b'GBLK' or version != 5 or size_x != size_y or light_scale < 0:
        raise ValueError('Unsupported terrain block')
    offset = 52

    def compressed(expected):
        nonlocal offset
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
    for _ in range(11):  # Pin maps: level-of-detail hints, unused here.
        offset += 2 + struct.unpack_from('<H', data, offset)[0]
    light_width = (size_x << light_scale) + 1
    return {'size': size_x, 'heights': heights, 'materials': materials,
            'lightWidth': light_width, 'light': compressed(light_width ** 2 * 2)}


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


def parse_mission(text):
    """Nested `instant Class "name" { key = "value"; };` objects up to the export end marker."""
    root = {'class': 'root', 'name': '', 'fields': {}, 'children': []}
    stack = [root]
    for line in text.split('//--- export object end ---//')[0].splitlines():
        line = line.strip()
        opened = re.fullmatch(r'instant\s+(\w+)(?:\s+"([^"]*)")?\s*\{', line)
        field = re.fullmatch(r'([\w\[\]]+)\s*=\s*"(.*)";', line)
        if opened:
            node = {'class': opened[1], 'name': opened[2] or '', 'fields': {}, 'children': []}
            stack[-1]['children'].append(node)
            stack.append(node)
        elif line == '};':
            if len(stack) == 1:
                raise ValueError('Unbalanced mission file')
            stack.pop()
        elif field:
            stack[-1]['fields'][field[1]] = field[2]
    if len(stack) != 1:
        raise ValueError('Unbalanced mission file')
    return root


def walk(node, groups=()):
    yield node, groups
    for item in node['children']:
        yield from walk(item, groups + (node['name'],))


def floats(text, count=3):
    values = [float(value) for value in text.split()]
    if len(values) != count or not all(math.isfinite(value) for value in values):
        raise ValueError('Expected %d finite numbers: %s' % (count, text))
    return values


def placement(position, rotation):
    """Three.js column-major matrix for a mission position and DarkStar Euler rotation (radians).

    DarkStar multiplies row vectors by its Euler matrix, so the file-space axes are the matrix
    rows. File (x, y, z-up) maps to viewer (x, z, -y), the basis the model JSON already uses.
    """
    sx, sy, sz = (math.sin(value) for value in rotation)
    cx, cy, cz = (math.cos(value) for value in rotation)
    rows = ((cy * cz - sy * sz * sx, cy * sz + sy * cz * sx, -cx * sy),
            (-cx * sz, cx * cz, sx),
            (sy * cz + cy * sz * sx, sy * sz - cy * cz * sx, cx * cy))
    viewer = lambda v: (v[0], v[2], -v[1])  # noqa: E731
    x_axis, y_axis, z_axis = viewer(rows[0]), viewer(rows[2]), [-value for value in viewer(rows[1])]
    return [*x_axis, 0, *y_axis, 0, *z_axis, 0, *viewer(position), 1]


def import_map(game_base, output, model_dir=ROOT / 'static/model_json'):
    if output.exists():
        raise ValueError('Choose a new output directory; existing map packs are never overwritten')
    folders = [game_base, game_base / 'missions']
    files = {path.name.lower(): path for folder in folders if folder.is_dir() for path in folder.iterdir() if path.is_file()}
    mission_path = files.get(MISSION.lower() + '.mis')
    if not mission_path:
        raise ValueError('Missing stock mission: %s.mis' % MISSION)
    mission = parse_mission(mission_path.read_text(encoding='cp1252'))

    # Resources come only from the volumes the mission itself mounts; later volumes win.
    resources, provenance = {}, []
    for node, _ in walk(mission):
        if node['class'] != 'SimVolume':
            continue
        path = files.get(node['fields']['fileName'].lower())
        if not path or not zipfile.is_zipfile(path):
            continue  # ponytail: zip volumes only; add a PVOL reader for installs that still ship .vol.
        with path.open('rb') as stream:
            provenance.append({'volume': path.name, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()})
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if not entry.is_dir():
                    resources[Path(entry.filename).name.lower()] = (path, entry.filename)

    def read(name):
        if name.lower() not in resources:
            raise ValueError('Required map resource missing: ' + name)
        path, member = resources[name.lower()]
        with zipfile.ZipFile(path) as archive:
            return archive.read(member)

    nodes = list(walk(mission))
    terrain_node = next(node for node, _ in nodes if node['class'] == 'SimTerrain')
    index = read_terrain_index(read(terrain_node['fields']['tedFileName']))
    if len(set(index['blockMap'])) != 1:
        raise ValueError('Only terrains that repeat a single block are supported')
    stem = Path(terrain_node['fields']['tedFileName']).stem
    block = read_terrain_block(read('%s#%d.dtb' % (stem, index['blockMap'][0])))
    if block['size'] != index['squares']:
        raise ValueError('Terrain block does not match its index')
    materials = interior_dml.dml()
    materials.load_binary(read(index['materialList']))
    used = sorted(set(block['materials'][1::2]))
    textures, warnings = {}, []
    for slot in used:
        name = Path(materials.materials[slot].name).stem.lower() + '.png'
        if name in resources:
            textures[slot] = name
        else:
            warnings.append('Missing terrain texture: ' + name)

    models = {path.stem.lower(): path.stem for path in model_dir.glob('*.json')}
    objects, viewpoints = [], []
    for node, groups in nodes:
        fields = node['fields']
        if 'position' not in fields or node['class'] == 'SimTerrain':
            continue
        matrix = placement(floats(fields['position']), floats(fields.get('rotation', '0 0 0')))
        if node['class'] == 'Marker':
            if 'ObserverDropPoints' in groups:
                viewpoints.append(matrix)
            continue
        if node['class'] == 'InteriorShape':
            shape = re.sub(r'(\.\d+)?\.dis$', '', fields['fileName'], flags=re.I)
        else:
            shape = DATABLOCK_SHAPES.get(fields.get('dataBlock', '').lower())
        if not shape:
            continue
        model = models.get(shape.lower())
        if not model:
            warnings.append('No preview model for %s (%s)' % (shape, node['name'] or node['class']))
        objects.append({'name': fields.get('name') or node['name'] or shape, 'model': model, 'matrix': matrix})

    # The sky dome is not drawn; its first texel stands in as the haze/background colour.
    sky = next((node['fields'] for node, _ in nodes if node['class'] == 'Sky'), {})
    haze = [128, 140, 150]
    try:
        sky_list = interior_dml.dml()
        sky_list.load_binary(read(sky['dmlName']))
        with Image.open(io.BytesIO(read(Path(sky_list.materials[0].name).stem + '.png'))) as image:
            haze = list(image.convert('RGB').getpixel((0, 0)))
    except (KeyError, ValueError, IndexError, OSError):
        warnings.append('Sky colour unavailable; using neutral haze')

    sun = next((node['fields'] for node, _ in nodes if node['class'] == 'Planet' and node['fields'].get('castShadows') == 'True'), {})
    scene = {
        'mission': MISSION,
        'terrain': {'squares': block['size'], 'unit': index['unit'], 'columns': index['columns'], 'rows': index['rows'],
                    'position': floats(terrain_node['fields']['position']), 'lightWidth': block['lightWidth'],
                    'textures': textures,
                    'visibleDistance': float(terrain_node['fields']['visibleDistance']),
                    'hazeDistance': float(terrain_node['fields']['hazeDistance'])},
        'sun': {'azimuth': float(sun.get('azimuth', 0)), 'incidence': float(sun.get('incidence', 45)),
                'intensity': floats(sun.get('intensity', '0.6 0.6 0.6')), 'ambient': floats(sun.get('ambient', '0.4 0.4 0.4'))},
        'haze': haze, 'objects': objects, 'viewpoints': viewpoints, 'warnings': warnings}

    output.mkdir(parents=True)
    (output / 'heights.bin').write_bytes(block['heights'])
    (output / 'materials.bin').write_bytes(block['materials'])
    (output / 'light.bin').write_bytes(block['light'])
    (output / 'terrain').mkdir()
    for name in textures.values():
        (output / 'terrain' / name).write_bytes(read(name))
    (output / 'scene.json').write_text(json.dumps(scene), encoding='utf-8')
    (output / 'SOURCES.json').write_text(json.dumps({
        'mission': MISSION, 'policy': 'Only volumes mounted by the mission; later volumes win. No loose or mod overrides.',
        'missionFile': {'name': mission_path.name, 'sha256': hashlib.sha256(mission_path.read_bytes()).hexdigest()},
        'volumes': provenance}, indent=2), encoding='utf-8')
    return scene


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-base', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('local-data/t1-maps'))
    args = parser.parse_args()
    result = import_map(args.game_base, args.output)
    print('Imported %s: %d objects, %d viewpoints, %d terrain textures into %s' % (
        MISSION, len(result['objects']), len(result['viewpoints']), len(result['terrain']['textures']), args.output))
    for warning in result['warnings']:
        print('Warning:', warning)

"""Local Diabotical map import for the Diabotical Maps page. Retail data stays outside source and builds.

python tools/import_diabotical_map.py --game-base "C:/Program Files/Epic Games/Diabotical" [--replace]

The game's maps are in packs/maps.dbp; maps made in its editor are in %APPDATA%/Diabotical/Maps (read too when
present). A map is a grid of blocks: the import keeps the blocks the page can draw, with the faces of each that are
open to the air, and the colour texture of each material they use.

Pack (.dbp; little-endian): "DBP1", u32 0, u32 count, then per file {u32 name length, name (backslash path),
u32 offset, u32 size}; offsets count from the end of that list and files are stored as they are.

Map (.rbe): "REBM", u32 version (21 to 27), u32 hash of the content (not checked by the game), u32, author (u32
length + bytes), u32, u32, then the body, gzipped from version 24. The body starts with the materials (u8 count,
then u32 length + name each; the last is empty and drawn as default), a u32 block count and the blocks, then the
map's other parts (entities, a grid, baked light), not read here. A block is a fixed record: 53 bytes in versions 26
and 27, 52 in 25, 46 in 24, with int32 x, y, z at 0 (y up), the shape at 12, the material of each face at 25 to 30
(faces +z, -x, -z, +x, top, bottom) and a quarter turn 0 to 3 at 50 (44 in version 24). Found by saving the same
map in several versions and by the game's own /export: shape 1 is a cube, 3 a half cube cut along a vertical
diagonal (the turn moves its missing corner), 2, 4, 5 and 6 draw nothing. A block is 40 units wide and deep and 20
tall; the export (and the page) puts block (x, y, z) at 40x, 20y, -40z - 40 (z mirrored).

After the blocks: u32 count and 16-byte cells of a grid (not read), u32 entity count, then each entity: name (u32
length + bytes; its type is the start: prop_..., light_..., spawn, hpt1, jumppad...), float32 position, rotation
(radians about x, y, z) and scale, u32 field count and that many name, value string pairs (model, material, color,
no_show...). Entity positions are in the blocks' units, z not mirrored: block (x, y, z) fills 40x..40x+40,
20y..20y+20, 40z..40z+40 (jump pads stand at 20y + 20 on the blocks under them). A prop's model is a path under
models/ (.fbx; the game's compiled .dbm is the same with z negated) or an asset: asset NAME { model PATH material
MATERIAL }, or a dynamic one, built of 40-unit cells from the entity on (its scale is its size in cells) whose models its
dynamic_rule blocks pick: { channel N, if CONDITION (on the cell's offset_left/right/bottom/top/front/back), select
or pick A,B,... }, the last rule that holds in each channel wins. A model PATH_flipx is PATH mirrored in x.

Materials: an asset (scripts/*.assets: asset NAME { type surface_material material MATERIAL }) names a material,
defined in a .shader file (NAME { { map colour, map normal, ...  uv_scale s } }). The page draws a face with the
material's first map, its texture repeating every 40 / s units, as the export's texture coordinates do. A name
defined more than once takes the first definition (packs in name order) whose texture is found, as the game does
for black: its black blocks have models_theme.dbp's uv_scale 0.5, not scripts.dbp's 1 (measured in its /export).

Written (under local-data/diabotical-maps): index.json, maps/ID-HASH.bin per map (16-byte blocks: int16 x, y, z,
u8 shape, turn, open faces (bit per face, in the order above), 0, then six u8 face materials), maps/ID-HASH.ent per
map (props, markers and liquids: static/diabotical-maps/entities.js reads it), the models the maps place in
models.json with models/HASH.bin, and the materials of the maps and models in materials.json, with
textures/HASH.png.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import zlib

import numpy as np

try:
    from tools.fbx_mesh import fbx_mesh
    from tools.reflex_textures import decode_dds
except ImportError:  # Run as a script from tools/.
    from fbx_mesh import fbx_mesh
    from reflex_textures import decode_dds

RECORD = {24: 46, 25: 52, 26: 53, 27: 53}
TURN = {24: 44, 25: 50, 26: 50, 27: 50}
CUBE, HALF = 1, 3
# Neighbours across each face, in the order the faces' materials are stored (+z, -x, -z, +x, top, bottom).
NEIGHBOURS = ((0, 0, 1), (-1, 0, 0), (0, 0, -1), (1, 0, 0), (0, 1, 0), (0, -1, 0))
OUT = np.dtype([('x', '<i2'), ('y', '<i2'), ('z', '<i2'), ('shape', 'u1'), ('turn', 'u1'), ('open', 'u1'), ('pad', 'u1'), ('faces', 'u1', 6)])
TEXTURE_SIZE = 512
ASSET = re.compile(r'\basset\s+(\S+)\s*\{([^{}]*)\}')
SHADER = re.compile(r'(?:^|\n)\s*([^\s{}]+)\s*\{\s*\{(.*?)(?:\}|\Z)', re.S)  # Some files end mid-shader.


def map_id(name):
    return re.sub(r'[^a-z0-9_-]', '_', name.lower())


class Pack:
    """A .dbp archive: its file list, read once; files are read when asked for."""

    def __init__(self, path):
        self.path = Path(path)
        with open(self.path, 'rb') as file:
            if file.read(4) != b'DBP1':
                raise ValueError(f'{self.path.name} is not a Diabotical pack')
            _, count = struct.unpack('<II', file.read(8))
            entries = []
            for _ in range(count):
                length, = struct.unpack('<I', file.read(4))
                name = file.read(length).decode('latin-1')
                offset, size = struct.unpack('<II', file.read(8))
                entries.append((name, offset, size))
            base = file.tell()
        self.files = {name.lower(): (base + offset, size) for name, offset, size in entries}

    def read(self, name):
        offset, size = self.files[name.lower()]
        with open(self.path, 'rb') as file:
            file.seek(offset)
            return file.read(size)


def read_map(raw):
    """A .rbe map's version, author, materials and blocks (a numpy array with x, y, z, shape, turn, faces)."""
    if raw[:4] != b'REBM':
        raise ValueError('not a Diabotical map')
    version, = struct.unpack_from('<I', raw, 4)
    if version not in RECORD:
        raise ValueError(f'map version {version} is not read yet')
    length, = struct.unpack_from('<I', raw, 16)
    author = raw[20:20 + length].decode('utf-8', 'replace')
    start = 20 + length + 8
    body = gzip.decompress(raw[start:]) if raw[start:start + 2] == b'\x1f\x8b' else raw[start:]
    count, at, materials = body[0], 1, []
    for _ in range(count):
        size, = struct.unpack_from('<I', body, at)
        materials.append(body[at + 4:at + 4 + size].decode('utf-8', 'replace'))
        at += 4 + size
    blocks, = struct.unpack_from('<I', body, at)
    size = RECORD[version]
    records = np.frombuffer(body, np.uint8, blocks * size, at + 4).reshape(blocks, size)
    return dict(version=version, author=author, materials=materials, entities=read_entities(body, at + 4 + blocks * size), blocks=dict(
        xyz=records[:, :12].copy().view('<i4'), shape=records[:, 12].copy(), turn=records[:, TURN[version]] & 3, faces=records[:, 25:31].copy()))


def read_entities(body, at):
    """The entities after the blocks: [(name, position, rotation, scale, {field: value})]."""
    def text():
        nonlocal at
        size, = struct.unpack_from('<I', body, at)
        at += 4 + size
        return body[at - size:at].decode('utf-8', 'replace')
    cells, = struct.unpack_from('<I', body, at)
    at += 4 + 16 * cells
    count, = struct.unpack_from('<I', body, at)
    at += 4
    entities = []
    for _ in range(count):
        name = text()
        values = struct.unpack_from('<9f', body, at)
        fields, = struct.unpack_from('<I', body, at + 36)
        at += 40
        entities.append((name, values[:3], values[3:6], values[6:], {text().lower(): text() for _ in range(fields)}))
    return entities


def visible_blocks(blocks):
    """The blocks the page draws (cubes and half cubes), each with a bit per face open to the air: a cube's face
    against another cube is closed; cubes closed on every side are left out."""
    xyz, shape = blocks['xyz'].astype(np.int64), blocks['shape']
    key = lambda p: ((p[:, 0] + 32768) << 32) | ((p[:, 1] + 32768) << 16) | (p[:, 2] + 32768)
    cubes = np.sort(key(xyz[shape == CUBE]))
    open_faces = np.zeros(len(xyz), np.uint8)
    for bit, step in enumerate(NEIGHBOURS):
        near = key(xyz + step)
        at = np.minimum(np.searchsorted(cubes, near), max(len(cubes) - 1, 0))
        closed = (cubes[at] == near) if len(cubes) else np.zeros(len(xyz), bool)
        open_faces |= np.where(closed, 0, 1 << bit).astype(np.uint8)
    open_faces[shape == HALF] = 0x3f
    keep = ((shape == CUBE) & (open_faces != 0)) | (shape == HALF)
    if len(xyz) and (np.abs(xyz[keep]) > 32767).any():
        raise ValueError('blocks lie outside the range the page reads')
    out = np.zeros(int(keep.sum()), OUT)
    out['x'], out['y'], out['z'] = xyz[keep, 0], xyz[keep, 1], xyz[keep, 2]
    out['shape'], out['turn'], out['open'], out['faces'] = shape[keep], blocks['turn'][keep], open_faces[keep], blocks['faces'][keep]
    return out


def read_materials(packs):
    """{asset or material name: [(colour map path, uv_scale, shader file's folder, flags), ...]} from every pack's
    .assets and .shader files: every definition of a name, in the order read, as some are defined more than once.
    Flags: cutout (alpha tested and two-sided, as foliage: its shadow shader is shadow_at_...), blend, hidden."""
    assets, shaders = {}, {}
    for pack in packs:
        for name in pack.files:
            if not name.endswith(('.assets', '.shader')):
                continue
            text = re.sub(r'//[^\n]*', '', pack.read(name).decode('latin-1'))
            if name.endswith('.assets'):
                for asset, fields in ASSET.findall(text):
                    found = re.search(r'\bmaterial\s+(\S+)', fields)
                    if found and re.search(r'\btype\s+surface_material\b', fields):
                        assets.setdefault(asset.lower(), found.group(1).lower())
            else:
                for shader, stage in SHADER.findall(text):
                    found = re.search(r'\bmap\s+(\S+)', stage)
                    scale = re.search(r'\buv_scale\s+([-\d.]+)', stage)
                    flags = dict(cutout=bool(re.search(r'shadow_at_|culling\s+off', stage)), blend=bool(re.search(r'blendfunc\s+blend', stage)),
                                 hidden=bool(re.search(r'visible\s+false', stage)))
                    if found:
                        shaders.setdefault(shader.lower(), []).append((found.group(1), float(scale.group(1)) if scale else 1.0, name.rsplit('\\', 1)[0], flags))
    return {name: shaders[material] for name, material in assets.items() if material in shaders} | shaders


def read_assets(packs):
    """{asset name: fields} of every pack's .assets (first definition wins), a dynamic asset's rules as
    [(channel, conditions, choices)]."""
    assets = {}
    for pack in packs:
        for name in pack.files:
            if not name.endswith('.assets'):
                continue
            text = re.sub(r'//[^\n]*', '', pack.read(name).decode('latin-1'))
            for found in re.finditer(r'\basset\s+(\S+)\s*\{', text):
                depth, end = 1, found.end()
                while depth and end < len(text):
                    depth += {'{': 1, '}': -1}.get(text[end], 0)
                    end += 1
                inner = text[found.end():end - 1]
                fields = {}
                for line in re.sub(r'\{[^{}]*\}', '', inner).splitlines():
                    words = line.split(None, 1)
                    if words:
                        fields.setdefault(words[0].lower(), words[1].strip() if len(words) > 1 else '')
                fields['rules'] = []
                for rule in re.findall(r'dynamic_rule\s*\{([^{}]*)\}', inner):
                    channel = re.search(r'^\s*channel\s+(\d+)', rule, re.M)
                    # A choice may carry a word on how to pick (serial_horizontal, serial_rand): not followed.
                    choices = [c.split()[0].lower() for line in re.findall(r'^\s*(?:select|pick)\s+(.+)$', rule, re.M) for c in line.split(',') if c.strip()]
                    fields['rules'].append((int(channel.group(1)) if channel else 0, re.findall(r'^\s*if\s+(.+?)\s*$', rule, re.M), choices))
                assets.setdefault(found.group(1).lower(), fields)
    return assets


def rule_holds(condition, cell):
    """One `if` line of a dynamic rule, for a cell's offsets from each end and the prop's size."""
    words = re.sub(r'\s*(%|==|>|<|-|/)\s*', r' \1 ', condition.replace('offset right', 'offset_right')).split()
    value = lambda word: cell[word] if word in cell else int(word) if re.fullmatch(r'-?\d+', word) else None
    if any(value(word) is None for word in words if word not in ('is', '==', '%', '<', '>', '-', '/')):
        return False
    try:
        if len(words) == 2 or (len(words) == 3 and words[1] in ('is', '==')):
            return value(words[0]) == value(words[-1])
        if len(words) >= 4 and words[1] == '%':
            return value(words[0]) % value(words[2]) == value(words[3])
        if len(words) == 3 and words[1] in '<>':
            return value(words[0]) > value(words[2]) if words[1] == '>' else value(words[0]) < value(words[2])
        if len(words) == 4 and words[1] == '-':
            return value(words[0]) - value(words[2]) == value(words[3])
    except (TypeError, ZeroDivisionError):
        pass
    return False  # ponytail: `/`, `when`, `left empty` (neighbour tests) are not understood and never hold.


def game_matrix(position, rotation, scale):
    """Entity transform in the game's axes: scale, then rotation (roll z, pitch x, yaw y), then position."""
    x, y, z = rotation
    rx = np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]])
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rz = np.array([[np.cos(z), -np.sin(z), 0], [np.sin(z), np.cos(z), 0], [0, 0, 1]])
    out = np.eye(4)
    out[:3, :3] = ry @ rx @ rz @ np.diag(scale)
    out[:3, 3] = position
    return out


MIRROR = np.diag([1.0, 1.0, -1.0, 1.0])  # Game axes <-> page (and FBX) axes.
CELL = 40  # A dynamic prop's cell, in units.
PICKUPS = re.compile(r'(spawn|hpt|armort|weapon|ammo|jumppad|jp|teleport|tpexit|flag|coin|crystal|doubledamage|tripledamage)')


def placements(entities, assets):
    """A map's props as {"model|material|m": float32 array of page matrices' top three rows} (material empty for the
    model's own, m when mirrored), and its spawns, pickups and other markers, and liquids. A dynamic prop's scale is
    its size in 40-unit cells, each cell a model its asset's rules pick by the cell's offsets from the prop's ends."""
    props, markers, liquids = {}, [], []
    for number, (name, position, rotation, scale, fields) in enumerate(entities):
        base = game_matrix(position, rotation, (1, 1, 1))
        if name.startswith('liquid'):
            liquids.append([*np.round(position, 2).tolist(), *np.round(scale, 2).tolist(), fields.get('material', '')])
            continue
        kind = PICKUPS.match(name)
        if kind and 'model' not in fields:
            markers.append([re.sub(r'_?\d*$', '', name.split('_')[0]) or kind.group(1), *np.round(position, 2).tolist()])
            continue
        model = fields.get('model', '').lower()
        asset = assets.get(model, {})
        if not model or fields.get('no_show') in ('1', 'true') or 'no_show true' in asset.get('entity_property', ''):
            continue
        pieces = []
        if asset.get('dynamic') == 'true':
            size = [max(1, int(round(s))) for s in scale]
            for i, j, k in np.ndindex(*size):
                cell = dict(offset_left=i, offset_right=size[0] - 1 - i, offset_bottom=j, offset_top=size[1] - 1 - j,
                            offset_front=k, offset_back=size[2] - 1 - k, size_x=size[0], size_y=size[1], size_z=size[2],
                            min_size_xz=min(size[0], size[2]))
                chosen = {}
                for channel, conditions, choices in asset['rules']:
                    if choices and all(rule_holds(c, cell) for c in conditions):
                        chosen[channel] = choices[(number * 7919 + i * 31 + j * 17 + k * 13 + channel) % len(choices)]
                offset = np.eye(4)
                offset[:3, 3] = ((i + .5) * CELL, (j + .5) * CELL, (k + .5) * CELL)  # The entity is the prop's corner.
                pieces += [(choice, base @ offset) for choice in chosen.values()]
        else:
            pieces.append((model, game_matrix(position, rotation, scale)))
        for piece, matrix in pieces:
            piece_asset = assets.get(piece, {})
            if piece_asset.get('dynamic') == 'true':
                continue
            # PATH_flipx (and _flipy, _flipz) is PATH mirrored: no such file.
            model, flips = re.match(r'(.*?)((?:_flip[xyz])*)$', piece_asset.get('model', piece).lower()).groups()
            matrix = MIRROR @ matrix @ np.diag([-1.0 if f'flip{axis}' in flips else 1.0 for axis in 'xyz'] + [1.0]) @ MIRROR
            material = (fields.get('material') or piece_asset.get('material') or asset.get('material') or '').lower()
            key = f"{model}|{material}|{'m' if np.linalg.det(matrix[:3, :3]) < 0 else ''}"
            props.setdefault(key, []).append(matrix[:3].ravel())
    return {key: np.array(value, np.float32) for key, value in props.items()}, markers, liquids


def material_textures(packs, names, materials, output, replace=False):
    """Each named material's texture (scaled to TEXTURE_SIZE at most) and scale for materials.json; the names
    with no material or texture in the game files. A name with a variant (metalwall_heat01:3) is drawn as its
    material; a texture is in the packs as PATH.dds, or as PATH itself for some plain .png ones, or else under its
    file name beside the shader file (models/theme/simple/textures/black.png is in .../textures/colors/, with
    colors.shader)."""
    where = {}
    for pack in packs:
        for name in pack.files:
            if name.endswith(('.dds', '.png')):
                where.setdefault(name, pack)
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    entries, missing = {}, []
    for name in sorted(names):
        files = [(path + suffix, scale, flags) for path, scale, folder, flags in materials.get((name or 'default').lower().split(':')[0], [])
                 for path in [path.lower().replace('/', '\\')] for path in (path, folder + '\\' + path.rsplit('\\', 1)[-1])
                 for suffix in ('.dds', '') if path + suffix in where]
        if not files:
            missing.append(name or 'default')
            continue
        file, scale, flags = files[0]
        if flags['hidden']:
            entries[name] = dict(hidden=True)
            continue
        raw = where[file].read(file)
        alpha = flags['cutout'] or flags['blend']
        target = output / 'textures' / f"{hashlib.sha256(raw).hexdigest()[:16]}{'-a' if alpha else ''}.png"
        if replace or not target.is_file():
            try:
                image = decode_dds(raw).convert('RGBA' if alpha else 'RGB')
            except (OSError, ValueError):
                missing.append(name or 'default')
                continue
            if max(image.size) > TEXTURE_SIZE:
                image.thumbnail((TEXTURE_SIZE, TEXTURE_SIZE))
            data = io.BytesIO()
            image.save(data, 'PNG')
            target.write_bytes(data.getvalue())
        entries[name] = dict(texture=target.name, scale=scale) | {key: True for key in ('cutout', 'blend') if flags[key]}
    return entries, sorted(set(missing))


def convert_models(packs, paths, materials, output, replace=False, previous=None):
    """Each model path's FBX (models/PATH.fbx in the packs) as models/HASH.bin, triangles of 8 float32 each corner
    (position, normal, uv in the page's axes) grouped by material, for models.json: {path: {file, groups: [[material,
    corners]]}}. A group's material is the first defined of PATH_MATERIAL, MATERIAL and PATH (the FBX's own material
    names say little: "1024"), else the material named most like the model (longest common start) in the nearest
    .shader file at or above the model's folder (many pieces of a dynamic prop share one: trim01b takes trim01a's).
    Models of the `previous` models.json whose file is there are kept unless replacing. Also returns the paths with
    no readable FBX."""
    where = {}
    for pack in packs:
        for name in pack.files:
            if name.endswith('.fbx'):
                where.setdefault(name, pack)
    folders = {}
    for name, found in materials.items():
        for definition in found:
            folders.setdefault(definition[2], []).append(name)
    alike = lambda path, names: max(names, key=lambda name: len(os.path.commonprefix([path, name])))  # First of the longest.
    (output / 'models').mkdir(parents=True, exist_ok=True)
    entries, missing = {}, []
    for path in sorted(paths):
        if not replace and path in (previous or {}) and (output / 'models' / previous[path]['file']).is_file():
            entries[path] = previous[path]
            continue
        file = 'models\\' + path.replace('/', '\\') + '.fbx'
        try:
            raw = where[file].read(file)
            target = output / 'models' / f'{hashlib.sha256(raw).hexdigest()[:16]}.bin'
            groups = fbx_mesh(raw)
        except (KeyError, ValueError, IndexError, struct.error, zlib.error):
            missing.append(path)
            continue
        # Else the material most like the model's name in the nearest .shader beside it or in a folder above it.
        parts = file.split('\\')[:-1]
        own = next((alike(path, folders[f]) for f in ('\\'.join(parts[:n]) for n in range(len(parts), 1, -1)) if f in folders), path)
        names = [next((m for m in (f'{path}_{name}'.lower(), name.lower(), path) if m in materials), own) for name in groups]
        if replace or not target.is_file():
            target.write_bytes(b''.join(np.concatenate(parts, 2).tobytes() for parts in groups.values()))
        entries[path] = dict(file=target.name, groups=[[name, 3 * len(parts[0])] for name, parts in zip(names, groups.values())])
    return entries, missing


def user_maps():
    folder = Path(os.environ.get('APPDATA', '')) / 'Diabotical' / 'Maps'
    return sorted(folder.glob('*.rbe')) if os.environ.get('APPDATA') and folder.is_dir() else []


def import_maps(game, output, replace=False, extra=None):
    """Import every map of the Diabotical folder `game` and the editor's maps (`extra`, by default those in
    %APPDATA%/Diabotical/Maps). Returns imported, skipped and failed maps and the materials with no texture."""
    game, output = Path(game).expanduser(), Path(output)
    if not (game / 'packs' / 'maps.dbp').is_file():
        raise ValueError('No packs/maps.dbp here. Enter the Diabotical folder.')
    packs = [Pack(path) for path in sorted((game / 'packs').glob('*.dbp')) if not path.name.startswith('audio')]
    stock = next(pack for pack in packs if pack.path.name == 'maps.dbp')
    sources = [(name.rsplit('\\', 1)[-1][:-4], 'Diabotical', lambda name=name: stock.read(name))
               for name in sorted(stock.files) if name.endswith('.rbe')]
    sources += [(path.stem, 'Your maps', path.read_bytes) for path in (user_maps() if extra is None else extra)]
    (output / 'maps').mkdir(parents=True, exist_ok=True)
    index_path = output / 'index.json'
    index = {item['id']: item for item in json.loads(index_path.read_text(encoding='utf-8'))} if index_path.is_file() else {}
    result = dict(imported=[], skipped=[], failed={}, untextured=[], unconverted=[])
    assets = read_assets(packs)
    for name, group, read in sources:
        ident = map_id(name if group == 'Diabotical' else 'user__' + name)
        try:
            raw = read()
            digest = hashlib.sha256(raw).hexdigest()[:12]
            if not replace and index.get(ident, {}).get('hash') == digest and all(
                    (output / 'maps' / index[ident].get(key, '-')).is_file() for key in ('file', 'entities')):
                result['skipped'].append(name)
                continue
            parsed = read_map(raw)
            blocks = visible_blocks(parsed['blocks'])
            props, markers, liquids = placements(parsed['entities'], assets)
        except (OSError, ValueError, EOFError, struct.error) as exc:
            result['failed'][name] = str(exc)
            index.pop(ident, None)
            continue
        head = json.dumps(dict(props=[[key, len(value)] for key, value in props.items()], markers=markers, liquids=liquids),
                          separators=(',', ':')).encode()
        head += b' ' * (-len(head) % 4)
        placed = struct.pack('<I', len(head)) + head + b''.join(value.tobytes() for value in props.values())
        # Named by content: served as immutable, and a newer import can place the same map's props differently.
        file, entities = f'{ident}-{digest}.bin', f'{ident}-{hashlib.sha256(placed).hexdigest()[:12]}.ent'
        for old in (index.get(ident, {}).get(key) for key in ('file', 'entities')):
            if old and old not in (file, entities):
                (output / 'maps' / old).unlink(missing_ok=True)
        (output / 'maps' / file).write_bytes(blocks.tobytes())
        (output / 'maps' / entities).write_bytes(placed)
        index[ident] = dict(id=ident, name=name, group=group, file=file, entities=entities, hash=digest, version=parsed['version'],
                            author=parsed['author'], materials=parsed['materials'], blocks=len(blocks))
        result['imported'].append(name)
    # Maps since deleted from the game or the editor's folder leave the list.
    present = {map_id(name if group == 'Diabotical' else 'user__' + name) for name, group, _ in sources}
    for ident in [ident for ident in index if ident not in present]:
        gone = index.pop(ident)
        for key in ('file', 'entities'):
            (output / 'maps' / gone.get(key, '-')).unlink(missing_ok=True)
    found = read_materials(packs)
    keys = set()
    for item in index.values():
        with open(output / 'maps' / item['entities'], 'rb') as file:
            length, = struct.unpack('<I', file.read(4))
            keys |= {key for key, _ in json.loads(file.read(length))['props']}
    models_path = output / 'models.json'
    previous = json.loads(models_path.read_text(encoding='utf-8')) if models_path.is_file() else {}
    models, result['unconverted'] = convert_models(packs, {key.split('|')[0] for key in keys}, found, output, replace, previous)
    models_path.write_text(json.dumps(models, separators=(',', ':'), sort_keys=True), encoding='utf-8')
    used = ({name for item in index.values() for name in item['materials']} | {key.split('|')[1] for key in keys if key.split('|')[1]} |
            {name for model in models.values() for name, _ in model['groups']})
    materials, result['untextured'] = material_textures(packs, used, found, output, replace)
    (output / 'materials.json').write_text(json.dumps(materials, separators=(',', ':'), sort_keys=True), encoding='utf-8')
    temporary = index_path.with_suffix('.tmp')
    rank = {'Diabotical': 0, 'Your maps': 1}
    temporary.write_text(json.dumps(sorted(index.values(), key=lambda item: (rank[item['group']], item['name'].lower())),
                                    separators=(',', ':')), encoding='utf-8')
    temporary.replace(index_path)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-base', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'local-data/diabotical-maps')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    done = import_maps(args.game_base, args.output, args.replace)
    print(f"Imported {len(done['imported'])}, skipped {len(done['skipped'])}, failed {len(done['failed'])}")
    for name, reason in done['failed'].items(): print('FAILED', name, reason)
    if done['untextured']: print('NO TEXTURE FOR', ', '.join(done['untextured']))

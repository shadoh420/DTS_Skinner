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
map in several versions and by the game's own /export (version 21, the five oldest stock maps: no author and no
two words after it, the body from byte 16, blocks as in version 24): shape 1 is a cube, 3 a half cube cut along a vertical
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

Terrain: a map with a `terrain` entity has a heightmap beside it, NAME-h.png (512 x 512, height in red), and
NAME-b.png (its dirt mask in red). Pixel (column, row) is the vertex at x = (column - 256) * 40, z = (row - 256) * 40
in entity coordinates, y = offset_y + 8 * red (fitted to the grass and flowers standing on it in 64 stock maps, then
measured in the game; the entity's own position is not used); the mask's texel (column, row) is the cell from there
to the next. Its material (the entity's material or shader, else core_ter) is drawn by
tileter.ps: map 0 where flat, mixed with map 5 by the dirt mask, map 3 on slopes (normal y below 0.8, blended to
0.85).

Materials: an asset (scripts/*.assets: asset NAME { type surface_material material MATERIAL }) names a material,
defined in a .shader file (NAME { { map colour, map normal, ...  uv_scale s } }). The page draws a face with the
material's first map, its texture repeating every 40 / s units, as the export's texture coordinates do. A name
defined more than once takes the first definition (packs in name order) whose texture is found, as the game does
for black: its black blocks have models_theme.dbp's uv_scale 0.5, not scripts.dbp's 1 (measured in its /export).

Tints: a material drawn by tilemask.ps has a colour mask (its map 4) and up to three accent colours (pixel_shader_param
accentN RRGGBB), which a prop's color, color2 and color3 replace (RRGGBB, #RRGGBB, or accentN: the map's palette, the
accentN fields of its global entity). The shader turns the colour texture d toward accent * mean(d.rgb) by the mask's
red, then green, then blue, for the accents 1, 2, 3 (read from the compiled shader).

Decals: a `decal...` entity projects its material (drawn by tiledecal.ps: map 0, with alpha) onto the surfaces in
a box, the unit cube centred on the entity under its rotation and scale (models/decal_volume.dbm is that cube), onto
those facing its local -z (the shader drops surfaces whose normal is more than about 84 degrees off it); in 2,700
flat stock decals the surface under one lies at its centre and faces local -z, with or without v2 or v3. The
texture runs across local x and y (turned round on floors and past 90 degrees) and is cut to a second box turned
by the yaw the other way (see decal_matrix), times `color` (RRGGBB or AARRGGBB, taken as sRGB: 808080 halves the
picture's value; run 13). `mirrored` changed nothing seen; v1, v2 and v3 differ only in v3's scale; the box's depth
fades nothing. `order` (-10000 to 10000) orders them.

Lights: `light...` entities by their `type` (see read_lights), measured in the game on a grey floor (runs 16 to 18):
the game lights with point, spot and capsule lights (unshadowed), one sun (shadowed), an ambient and a shadow
ambient colour, and a 3D ambient grid it builds from ambient nodes when the map loads (tile.cs.cso); its picture is
that light times the texture's bytes, with no further curve. Point, spot, capsule and sun lights (not diffuse ones) add
GGX specular light and the material reflects the map's envmap (a cube in packs/textures_cubemaps.dbp, by default
default_envmap), by the material's specular map (its map 2: red gloss, green strength) and material id (its map 3's
red where not black, else its material_id): see static/diabotical-maps/lighting.js.

Written (under local-data/diabotical-maps): index.json, maps/ID-HASH.bin per map (16-byte blocks: int16 x, y, z,
u8 shape, turn, open faces (bit per face, in the order above), 0, then six u8 face materials), maps/ID-HASH.ent per
map (props with their tints, decals, markers, liquids, billboards, particle emitters and lights: static/diabotical-maps/entities.js reads it), maps/ID-HASH.png for a map with
terrain (heights in red, dirt mask in green; the index item's `terrain` says how to place it), the models the maps place in
models.json with models/HASH.bin, the particle systems the maps use in particles.json, the materials of the maps and models in materials.json, with
textures/HASH.png (specular maps HASH-s.png, red and green), and the maps' envmaps in envmaps/NAME.png (the six faces of
the cube's third mip, 256 square, side by side in D3D order: +x, -x, +y, -y, +z, -z).
"""
import argparse
import functools
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
    from tools.reflex_textures import _dds, decode_dds
    from tools.local_data import LOCAL_DATA
except ImportError:  # Run as a script from tools/.
    from fbx_mesh import fbx_mesh
    from local_data import LOCAL_DATA
    from reflex_textures import _dds, decode_dds

FORMAT = 24  # Of the files written per map: maps imported with another are read again.
RECORD = {21: 46, 24: 46, 25: 52, 26: 53, 27: 53}
TURN = {21: 44, 24: 44, 25: 50, 26: 50, 27: 50}
CUBE, HALF = 1, 3
# Neighbours across each face, in the order the faces' materials are stored (+z, -x, -z, +x, top, bottom).
NEIGHBOURS = ((0, 0, 1), (-1, 0, 0), (0, 0, -1), (1, 0, 0), (0, 1, 0), (0, -1, 0))
OUT = np.dtype([('x', '<i2'), ('y', '<i2'), ('z', '<i2'), ('shape', 'u1'), ('turn', 'u1'), ('open', 'u1'), ('pad', 'u1'), ('faces', 'u1', 6)])
TEXTURE_SIZE = 512
ASSET = re.compile(r'\basset\s+(\S+)\s*\{([^{}]*)\}')
# A shader's own lines (`visible false`: the jump pads' ring band) may come before its stage. Some files end mid-shader.
SHADER = re.compile(r'(?:^|\n)\s*([^\s{}]+)\s*\{([^{}]*)\{(.*?)(?:\}|\Z)', re.S)


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
    if version == 21:  # No author or the two words after it: the body follows the hash and a zero word.
        author, start = '', 16
    else:
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


def colour(value, palette=None):
    """A colour field (ffaa00, #FFAA00, AARRGGBB, or accentN: the map's palette, in its global entity) as 0xRRGGBB, or
    None. Eight digits are alpha first (run 13: a decal coloured ff000080 came out dark blue)."""
    value = (value or '').strip().lower()
    word = value.split()[0].lstrip('#') if value else ''
    if palette is not None and re.fullmatch(r'accent\d+', word):
        return colour(palette.get(word))
    return int(word[-6:], 16) if re.fullmatch(r'[0-9a-f]{6}|[0-9a-f]{8}', word) else None


def read_materials(packs):
    """{asset or material name: [(colour map path, uv_scale, shader file's folder, flags), ...]} from every pack's
    .assets and .shader files: every definition of a name, in the order read, as some are defined more than once.
    Flags: cutout (alpha tested and two-sided, as foliage: its shadow shader is shadow_at_...), blend, hidden; and
    for the tile shaders the specular map (map 2), id map (map 3) and material_id."""
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
                for shader, outer, stage in SHADER.findall(text):
                    stage = outer + stage
                    maps = re.findall(r'\bmap\s+(\S+)', stage)
                    scale = re.search(r'\buv_scale\s+([-\d.]+)', stage)
                    flags = dict(cutout=bool(re.search(r'shadow_at_|culling\s+off', stage)), blend=bool(re.search(r'blendfunc\s+blend', stage)),
                                 hidden=bool(re.search(r'visible\s+false', stage)), decal='tiledecal' in stage,
                                 accents=[colour(next(iter(re.findall(rf'pixel_shader_param\s+accent{n}\s+(\S+)', stage)), None)) for n in (1, 2, 3)])
                    lit = re.search(r'pixel_shader\s+tile', stage) and 'tiledecal' not in stage
                    found = re.search(r'\bmaterial_id\s+(\d+)', stage)
                    flags.update(lit=bool(lit), spec=maps[2] if lit and len(maps) > 2 else None, ids=maps[3] if lit and len(maps) > 3 else None,
                                 id=int(found.group(1)) if found else 0)
                    # The efferv glass (health bubbles): its base and edge (fresnel) colours, linear RGBA; no texture.
                    params = dict(re.findall(r'pixel_shader_param\s+(accent[12])\s+(\S+\s+\S+\s+\S+\s+\S+)', stage))
                    flags['glass'] = [[float(v) for v in params[k].split()] for k in ('accent1', 'accent2')] if 'efferv' in stage and len(params) == 2 else None
                    # A terrain's (pixel shader tileter) cliff and dirt textures are its maps 3 and 5: NAME#3, NAME#5;
                    # a tinted material's (tilemask) colour mask is its map 4: NAME#4.
                    for slot in (0, 3, 5) if 'tileter' in stage else (0, 4) if 'tilemask' in stage else (0,):
                        if slot < len(maps):
                            shaders.setdefault(shader.lower() + (f'#{slot}' if slot else ''), []).append(
                                (maps[slot], float(scale.group(1)) if scale else 1.0, name.rsplit('\\', 1)[0], flags))
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
                fields['channels'] = {int(n): shader.lower() for n, shader in re.findall(r'^\s*channel_material\s+(\d+)\s+(\S+)', inner, re.M)}
                assets.setdefault(found.group(1).lower(), fields)
    return assets


def piece_shaders(assets, known):
    """{model path: shader} for the pieces of dynamic assets, whose own model files name no shader (Maya's lambert1):
    the channel's `channel_material` (offshore pipes), else the first shader in `known` named after a model the channel's
    rules list, in order, as listed or without its _flipx (temple wallbars draw as dp_wallbars_mid_mid, castle
    woodexterior_y as woodexterior, scaffold pieces as dp_scaffold_test_corner_mid_x_flipz). The first asset read wins."""
    raw = lambda choice: assets.get(choice, {}).get('model', choice).lower()
    model = lambda choice: re.sub(r'(_flip[xyz])+$', '', raw(choice))
    heads = {}
    for asset in assets.values():
        if asset.get('dynamic') != 'true':
            continue
        first = dict(asset['channels'])
        for channel, _, choices in asset['rules']:
            for name in (name for choice in choices for name in (raw(choice), model(choice)) if name in known):
                first.setdefault(channel, name)
        for channel, _, choices in asset['rules']:
            for choice in choices:
                if channel in first:
                    heads.setdefault(model(choice), first[channel])
    return heads


@functools.lru_cache(maxsize=None)
def rule_words(condition):
    """A rule line's words and {word: int} for its numbers, read once: the import asks tens of millions of times."""
    words = tuple(re.sub(r'\s*(%|==|>|<|-|/)\s*', r' \1 ', condition.replace('offset right', 'offset_right')).split())
    return words, {word: int(word) for word in words if re.fullmatch(r'-?\d+', word)}


def rule_holds(condition, cell):
    """One `if` line of a dynamic rule, for a cell's offsets from each end and the prop's size."""
    words, numbers = rule_words(condition)
    value = lambda word: cell[word] if word in cell else numbers.get(word)
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
        # A slope (roofs, diagonal walls, stair fences: 68 assets): `a / b c` is a = b x c, one cell up every c
        # cells along (a_bazaar's tile roofs: front / bottom 3, pieces 3 cells deep and 1 high; run 41).
        if len(words) == 4 and words[1] == '/':
            return value(words[0]) == value(words[2]) * value(words[3])
    except (TypeError, ZeroDivisionError):
        pass
    return False  # ponytail: `when`, `left empty` (neighbour tests) are not understood and never hold.


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


def decal_matrix(position, rotation, scale, v3=False):
    """A decal's box in game axes (the one its texture is projected from) and the box it is cut to, which the game
    turns by the yaw the other way and not by the roll (run 15: banner decals turned by yaw 30, 45 and 60 come out
    as parallelograms, one turned by roll 45 as cut by its unturned box; a square box at yaw 45 whole).
    The first box's local x and y are the way its texture's right and top run. A v3 decal's box takes
    |R| s as its scale (the rotation's entries as absolute values, times the scale): the sizes of three test decals
    turned by right angles (run 13), and of one rolled 45 degrees on a wall (run 14: 40 x 40 drawn about 57 x 57,
    where |R s| would have no width). The texture's top is the world's up seen along the box, then turned by the
    roll (z): the rotation's own x and y while cos(rotation x) > 0, turned round past that and where the box faces
    straight up or down (no up to see along it). Runs 13 to 15: walls at rolls 0, 90, 180 and 45, both ways along
    x, floors and a ceiling flat (turned round)
    and tilted 60, 80 and 89 degrees (not)."""
    turn = game_matrix((0, 0, 0), rotation, (1, 1, 1))[:3, :3]
    if v3:
        scale = np.abs(turn) @ np.asarray(scale, float)
    if np.cos(rotation[0]) < 1e-3:
        turn = turn @ np.diag([-1.0, -1.0, 1.0])
    out, cut = np.eye(4), np.eye(4)
    out[:3, :3] = turn @ np.diag(scale)
    cut[:3, :3] = game_matrix((0, 0, 0), (rotation[0], -rotation[1], 0), (1, 1, 1))[:3, :3] @ np.diag(scale)
    out[:3, 3] = cut[:3, 3] = position
    return out, cut


MIRROR = np.diag([1.0, 1.0, -1.0, 1.0])  # Game axes <-> page (and FBX) axes.
CELL = 40  # A dynamic prop's cell, in units.
PICKUPS = re.compile(r'(spawn|hpt|armort|weapon|ammo|jumppad|jp|teleport|tpexit|flag|coin|crystal|doubledamage|tripledamage)')


PICKUP_MODELS = {'flag': 'ctf_flag', 'coin': 'entities/coin/coin'}  # Named in the game's executable, not in an asset.
# A weapon pickup is its first-person model shrunk: run 37's top view showed every weapon 0.35-0.36 of its model's
# length, tilted a little up as it spins (so about 0.4); the health, armour, ammo, coin and flag models matched at 1.
WEAPON_PICKUP_SCALE = 0.4


def pickup_kinds(assets, readable):
    """{kind: (model path, scale, centred)} for the pickups the game draws as a model (run 37): the kinds with an asset
    marked `pickup_size` and coins; the model is the asset's, else PICKUP_MODELS', else models/KIND (weapons), kept
    where `readable(path)` (an FBX, binary or ASCII). Scale is the asset's (1.4 for the melee
    weebles), times WEAPON_PICKUP_SCALE for weapons; a model is centred on its bounding box unless the asset sets a `pivot`."""
    kinds = {}
    for kind in sorted({name for name, fields in assets.items() if 'pickup_size' in fields} | {'coin'}):
        fields = assets.get(kind, {})
        model = (fields.get('model') or PICKUP_MODELS.get(kind) or kind).lower()
        if readable(model):
            scale = float(fields.get('scale') or 1) * (WEAPON_PICKUP_SCALE if kind.startswith('weapon') else 1)
            kinds[kind] = (model, round(scale, 4), 'pivot' not in fields)
    return kinds


def placements(entities, assets, known=(), pickups=None):
    """A map's props as {"model|material|m": float32 array of page matrices' top three rows} (material empty for the
    model's own, m when mirrored), their tints ({key: uint32 (n, 3)}, for the keys with any: each prop's color,
    color2 and color3 as 0x1RRGGBB, 0 if unset), its spawns, pickups and other markers, liquids, and decals ({material:
    (float32 (n, 24) page matrices of their boxes, projected from and cut to, uint32 (n, 3): colour 0xRRGGBBAA, flags (1 mirrored, 2 v2, 4 v3),
    order as int32)}). A prop's material field X names the shader MODEL_X where `known` (the material names) has one
    (bioplant's door frames: corridor_path_..._frame_red), else X. A dynamic prop's scale is its size in 40-unit cells, each cell a model its asset's rules pick by
    the cell's offsets from the prop's ends. A pickup of a kind in `pickups` ({kind: scale}) is the prop pickup/KIND."""
    props, tints, markers, liquids, decals = {}, {}, [], [], {}
    palette = next((fields for name, *_, fields in entities if name == 'global'), {})
    for number, (name, position, rotation, scale, fields) in enumerate(entities):
        base = game_matrix(position, rotation, (1, 1, 1))
        if name.startswith('decal'):
            if fields.get('material'):
                rgb, word = colour(fields.get('color'), palette), (fields.get('color') or '').strip().lstrip('#').lower()
                alpha = int(word[:2], 16) if re.fullmatch(r'[0-9a-f]{8}', word) else 255
                flags = sum(bit for bit, key in ((1, 'mirrored'), (2, 'v2'), (4, 'v3')) if fields.get(key) == 'true')
                order = re.fullmatch(r'\s*(-?\d+)\D*', fields.get('order', ''))  # Some read "01000".
                decals.setdefault(fields['material'].lower(), []).append((
                    np.concatenate([(MIRROR @ box @ MIRROR)[:3].ravel() for box in decal_matrix(position, rotation, scale, fields.get('v3') == 'true')]),
                    [(0xffffff if rgb is None else rgb) << 8 | alpha, flags, int(order.group(1)) % 2 ** 32 if order else 0]))
            continue
        if name.startswith('liquid'):
            if fields.get('no_show') not in ('1', 'true'):
                word = (fields.get('color') or '').strip().lstrip('#').lower()
                ocean = fields.get('shader', '').lower() == 'ocean' or fields.get('material', '').lower() == 'core_ocean'
                liquids.append([*np.round(position, 2).tolist(), *np.round(scale, 2).tolist(), fields.get('material', ''), colour(fields.get('color'), palette),
                                int(word[:2], 16) if re.fullmatch(r'[0-9a-f]{8}', word) else None, int(ocean)])
            continue
        # A pickup the game draws as a model (pickup_kinds: {kind: scale}): the prop pickup/KIND, as placed (the game spins it).
        kind = name.split('_')[0].lower()
        if kind in (pickups or {}) and 'model' not in fields:
            matrix = MIRROR @ game_matrix(position, rotation, np.multiply(scale, pickups[kind])) @ MIRROR
            props.setdefault(f"pickup/{kind}||{'m' if np.linalg.det(matrix[:3, :3]) < 0 else ''}", []).append(matrix[:3].ravel())
            continue
        kind = PICKUPS.match(name)
        if kind and 'model' not in fields:
            markers.append([re.sub(r'_?\d*$', '', name.split('_')[0]) or kind.group(1), *np.round(position, 2).tolist()])
            continue
        model = fields.get('model', '').lower()
        asset = assets.get(model, {})
        if not model or fields.get('no_show') in ('1', 'true') or 'no_show true' in asset.get('entity_property', ''):
            continue
        tint = [0 if found is None else found | 1 << 24 for found in (colour(fields.get(key), palette) for key in ('color', 'color2', 'color3'))]
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
            own = (fields.get('material') or '').lower()
            material = f'{model}_{own}' if own and f'{model}_{own}' in known else own or (piece_asset.get('material') or asset.get('material') or '').lower()
            # Flags: m mirrored, n takes no decals (`no_decals`: b_ancient's snow mounds).
            flags = ('m' if np.linalg.det(matrix[:3, :3]) < 0 else '') + ('n' if fields.get('no_decals') in ('1', 'true') else '')
            key = f"{model}|{material}|{flags}"
            props.setdefault(key, []).append(matrix[:3].ravel())
            tints.setdefault(key, []).append(tint)
    tints = {key: np.array(value, np.uint32) for key, value in tints.items() if any(map(any, value))}
    decals = {key: (np.array([m for m, _ in value], np.float32), np.array([e for _, e in value], np.uint32)) for key, value in decals.items()}
    return {key: np.array(value, np.float32) for key, value in props.items()}, tints, markers, liquids, decals


def read_billboards(entities):
    """A map's billboards (flat panes: glass, light glows, signs): [12 page-matrix floats of a unit square in local
    x, y (the entity's scale x by y, placed as a prop), colour 0xRRGGBB or None, texture (a decal asset or a texture
    path; '' for none), reflection 0/1]."""
    palette = next((fields for name, *_, fields in entities if name == 'global'), {})
    out = []
    for name, position, rotation, scale, fields in entities:
        if not name.startswith('billboard') or fields.get('no_show') in ('1', 'true'):
            continue
        matrix = MIRROR @ game_matrix(position, rotation, (scale[0], scale[1], 1)) @ MIRROR
        texture = (fields.get('texture') or fields.get('material') or '').strip().lower().replace('\\', '/')
        out.append([*np.round(matrix[:3].ravel(), 3).tolist(), colour(fields.get('color'), palette), texture, int(fields.get('reflection') == 'on')])
    return out


def read_pfx(entities, assets=None, pickups=()):
    """A map's particle emitters (pfx entities): [12 page-matrix floats (its place and turn), system name, colour 0xRRGGBB
    or None, size (default 1)]; also the effect of each pickup of a kind in `pickups` whose asset names one (`pfx SYSTEM
    [x y z]`, the offset in its own frame: armour shields' glow 7 up)."""
    palette = next((fields for name, *_, fields in entities if name == 'global'), {})
    out = []
    for name, position, rotation, scale, fields in entities:
        words = (assets or {}).get(name.split('_')[0].lower(), {}).get('pfx', '').split()
        if name.split('_')[0].lower() in pickups and 'model' not in fields and words:
            offset = np.eye(4)
            offset[:3, 3] = [float(word) for word in words[1:4]] if len(words) >= 4 else 0
            matrix = MIRROR @ game_matrix(position, rotation, (1, 1, 1)) @ offset @ MIRROR
            out.append([*np.round(matrix[:3].ravel(), 4).tolist(), words[0].lower(), None, 1.0])
            continue
        if not name.startswith('pfx') or not fields.get('system') or fields.get('no_show') in ('1', 'true'):
            continue
        matrix = MIRROR @ game_matrix(position, rotation, (1, 1, 1)) @ MIRROR
        size = next(iter(re.findall(r'-?\d*\.?\d+', fields.get('size', ''))), None)
        out.append([*np.round(matrix[:3].ravel(), 4).tolist(), fields['system'].strip().lower(), colour(fields.get('color'), palette),
                    float(size) if size else 1.0])
    return out


def particle_systems(packs, names, output, replace=False):
    """The particle systems `names` (scripts/particles/*.particles: blocks NAME [PARENT] { key values }, a child
    taking its parent's keys; a system lists `subsystem EMITTER [delay] [size]`, or is one emitter itself) as
    {name: [emitter]} for particles.json, and their sprite sheets written as textures/HASH-a.png (at most 2048 square).
    An emitter: sheet (texture file), rect [u, v, width, height] of its first frame in the sheet (0-1, v down; a
    region of scripts/particles/effects.atlas: x y w h [frames [per row]]; the whole sheet where it has none), frames,
    row, blend (add or alpha), life, period, max, position (box min x y z, max x y z, local), velocity (min, max
    vectors) or angles (min, max degrees) and speed (start, end), acceleration (min, max), scale (start min, max, end
    min, max), colour (start rgba, end rgba), fade (in, out), animation (speed, start), range, delay, size.
    One-shot emitters (max_emissions not 0) and tubes are left out; names with no definition are left out."""
    scripts = next((pack for pack in packs if pack.path.name == 'scripts.dbp'), None)
    blocks, atlas = {}, {}
    for name in sorted(scripts.files) if scripts else []:
        text = re.sub(r'//[^\n]*', '', scripts.read(name).decode('latin-1'))
        if name.endswith('.particles'):
            for block, parent, body in re.findall(r'(?m)^\s*([\w-]+)(?:[ \t]+([\w-]+))?\s*\{([^{}]*)\}', text):
                blocks.setdefault(block.lower(), (parent.lower() or None, body))
        elif name.endswith('.atlas'):
            for sheet, width, height, body in re.findall(r'([\w.]+)\s+(\d+)\s+(\d+)\s*\{([^{}]*)\}', text):
                for line in body.splitlines():
                    parts = line.split()
                    if len(parts) >= 5:
                        atlas.setdefault((sheet.lower(), parts[0].lower()), (int(width), int(height), *map(float, parts[1:7])))

    def fields(block, seen=()):
        parent, body = blocks[block]
        out = fields(parent, seen + (block,)) if parent in blocks and parent not in seen else {}
        for line in body.splitlines():
            parts = line.split()
            if parts and parts[0] != 'subsystem':
                out[parts[0].lower()] = parts[1:]
        return out

    def floats(values, count, default):
        values = [float(v) for v in values if re.fullmatch(r'-?\d*\.?\d+(?:e-?\d+)?', v)] or list(default)
        return (values * count)[:count] if len(values) < count and count % len(values) == 0 else (values + [0.0] * count)[:count]

    where = {}
    for pack in packs:
        for name in pack.files:
            if name.endswith(('.dds', '.png')):
                where.setdefault(name.rsplit('\\', 1)[-1], (pack, name))
    sheets, systems = {}, {}
    (output / 'textures').mkdir(parents=True, exist_ok=True)

    def sheet_file(sheet):
        if sheet not in sheets:
            found = next((where[sheet + suffix] for suffix in ('.dds', '', ) if sheet + suffix in where), None)
            found = found or where.get(sheet.rsplit('.', 1)[0] + '.dds')
            sheets[sheet] = None
            if found:
                raw = found[0].read(found[1])
                target = output / 'textures' / f'{hashlib.sha256(raw).hexdigest()[:16]}-a.png'
                if replace or not target.is_file():
                    image = decode_dds(raw).convert('RGBA') if found[1].endswith('.dds') else None
                    if image is None:
                        from PIL import Image
                        image = Image.open(io.BytesIO(raw)).convert('RGBA')
                    image.thumbnail((2048, 2048))
                    image.save(target, 'PNG')
                sheets[sheet] = target.name
        return sheets[sheet]

    for system in sorted(names):
        if system not in blocks:
            continue
        subsystems = re.findall(r'(?m)^\s*subsystem\s+(\S+)([^\n]*)', blocks[system][1])
        emitters = []
        for emitter, rest in subsystems or [(system, '')]:
            if emitter.lower() not in blocks:
                continue
            f, extra = fields(emitter.lower()), floats(rest.split(), 2, (0, 1))
            if (f.get('max_emissions') or ['0'])[0] not in ('0', '0.0') or 'tube' in f.get('geometry', []):
                continue
            sheet = (f.get('map') or [''])[0].lower()
            file = sheet and sheet_file(sheet)
            if not file:
                continue
            region = atlas.get((sheet, (f.get('region') or [''])[0].lower()))
            if region:
                width, height, x, y, w, h, *rest_frames = region
                frames, row = (int(rest_frames[0]) if rest_frames else 1), (int(rest_frames[1]) if len(rest_frames) > 1 else 1)
                rect = [x / width, y / height, w / width, h / height]
            else:
                rect, frames, row = [0, 0, 1, 1], 1, 1
            angles = 'velocity_angles' in f
            emitters.append(dict(
                sheet=file, rect=[round(v, 5) for v in rect], frames=max(1, frames), row=max(1, row),
                blend='add' if (f.get('blend') or ['alpha'])[0].lower() == 'add' else 'alpha',
                life=floats(f.get('lifetime', []), 2, (1,)), period=floats(f.get('period', []), 2, (.1,)),
                max=int(floats(f.get('max_particles', []), 1, (10,))[0]), position=floats(f.get('position', []), 6, (0,)),
                velocity=None if angles else floats(f.get('velocity', []), 6, (0,)),
                angles=floats(f['velocity_angles'], 6, (0,)) if angles else None,
                speed=[floats(f.get('speed_start', []), 1, (0,))[0], floats(f.get('speed_end', f.get('speed_start', [])), 1, (0,))[0]],
                acceleration=floats(f.get('acceleration', []), 6, (0,)),
                scale=floats(f.get('scale_start', []), 2, (10,)) + floats(f.get('scale_end', f.get('scale_start', [])), 2, (10,)),
                colour=floats(f.get('color_start', []), 4, (1,)) + floats(f.get('color_end', f.get('color_start', [])), 4, (1,)),
                fade=[floats(f.get('fade_in', []), 1, (0,))[0], floats(f.get('fade_out', []), 1, (0,))[0]],
                animation=[floats(f.get('animation_speed', []), 1, (0,))[0], floats(f.get('animation_start', []), 1, (0,))[0]],
                range=floats(f.get('range', []), 1, (0,))[0], delay=extra[0] + floats(f.get('delay', []), 1, (0,))[0], size=extra[1]))
        if emitters:
            systems[system] = emitters
    return systems


POINT = SUN = 1 / np.pi  # Light per unit of colour x intensity on a white surface (runs 17 to 23, before the LUT step).
LIGHT_KINDS = {'point': 0, 'diffuse': 0, '': 0, 'spot': 1, 'diffuse_spot': 1, 'capsule': 2}
DEFAULT_SHADOW = [.325, .469, .519]  # Run 20: a pillar's shadow over the sunlit floor, with no shadow_color.
BLOCK = np.array([40, 20, 40])  # A block's size in entity units.
SPECULAR = ('point', 'spot', 'capsule')  # tile.cs: diffuse lights (and no type: diffuse) add no specular light.


def read_lights(entities, box=None):
    """A map's lights for the page (run 16 to 23 measurements, in page axes, as light on a white surface,
    before the game's last step, (16 x - 0.5) / 15, which the page applies to every pixel):
    {lights: [[kind (0 point, 1 spot, 2 capsule), x, y, z, dx, dy, dz, r, g, b, radius, inner radius, cos of the
    cone's half angle, softness, length, specular scale]], nodes: [[cubic, x, y, z, radius, r, g, b]], ambient,
    shadow_ambient, shadow_colour: [r, g, b], sun: [dx, dy, dz, r, g, b, specular scale] (the way its light travels)
    or None, envmap, gloss}. The specular scale turns the colour back into colour x intensity, as tile.cs lights its
    GGX specular (0 for diffuse lights); envmap and gloss are the global entity's (by default default_envmap, 1).
    Point lights (point, diffuse, or no type) are colour (hex taken as linear) x intensity (default 4) / pi x N.L,
    fading from falloff (default 0.33) x radius to nothing at radius as ((radius - d) / (radius - inner))^2.2; spots
    take their `angle` as the whole cone and `softness` in cosines; a capsule is the segment from its position along
    its local +z for `length`. The sun travels along its local +z, colour x intensity / pi x N.L, times
    shadow_color (global entity; by default bluish, run 20) in shadow. Ambient and shadow ambient (sunlit and shadowed ground) are their
    hex, linear (0x10, 0x40, 0xa0 measured); the shadow ambient is the ambient where the map has none. Ambient
    nodes ignore intensity and falloff (colour linear too) and replace the ambient where they reach; a node outside `box` (the blocks' bounds, entity axes) does nothing (run 23); volumes,
    fog and animation are not read."""
    hexes = lambda value, default=0xffffff: np.array([(default if colour(value) is None else colour(value)) >> s & 255 for s in (16, 8, 0)]) / 255
    number = lambda fields, key, default: next(iter(re.findall(r'-?\d*\.?\d+(?:e-?\d+)?', fields.get(key, ''))), None) or default
    ambient_level = lambda value: np.round(hexes(value, 0), 4).tolist()
    out = dict(lights=[], nodes=[], ambient=[0, 0, 0], shadow_ambient=None, shadow_colour=DEFAULT_SHADOW, sun=None, envmap='default_envmap', gloss=1.0)
    for name, position, rotation, scale, fields in entities:
        if name == 'global':
            if 'shadow_color' in fields:
                out['shadow_colour'] = np.round(hexes(fields['shadow_color'], 0), 4).tolist()
            out['envmap'] = fields.get('envmap', '').strip().lower() or out['envmap']
            out['gloss'] = float(number(fields, 'gloss', out['gloss']))
        if not name.startswith('light'):
            continue
        kind, (x, y, z) = fields.get('type', '').strip().lower(), position
        if kind in ('ambient', 'shadow_ambient'):
            out[kind] = ambient_level(fields.get('color'))
            continue
        travel = game_matrix((0, 0, 0), rotation, (1, 1, 1))[:3, 2] * (1, 1, -1)
        if kind == 'sun':
            if out['sun'] is None:
                out['sun'] = np.round([*travel, *hexes(fields.get('color')) * float(number(fields, 'intensity', 4)) * SUN, 1 / SUN], 4).tolist()
            continue
        radius = float(number(fields, 'radius', 200))
        if kind in ('ambient_node', 'cubic_ambient_node'):
            if box is not None and ((np.array(position) < box[0]) | (np.array(position) > box[1])).any():
                continue
            out['nodes'].append([int(kind == 'cubic_ambient_node'), *np.round([x, y, -z, radius, *hexes(fields.get('color'))], 4).tolist()])
        elif kind in LIGHT_KINDS and radius > 0:
            rgb = hexes(fields.get('color')) * float(number(fields, 'intensity', 4)) * POINT
            angle = np.radians(float(number(fields, 'angle', 60)))
            out['lights'].append([LIGHT_KINDS[kind], *np.round([x, y, -z, *travel, *rgb, radius, radius * min(1, max(0, float(number(fields, 'falloff', .33)))),
                                  np.cos(angle / 2), float(number(fields, 'softness', 0)), float(number(fields, 'length', 0)),
                                  1 / POINT if kind in SPECULAR else 0], 4).tolist()])
    if out['shadow_ambient'] is None:
        out['shadow_ambient'] = out['ambient']
    return out


def read_terrain(entities, read):
    """The map's heightmap terrain, if it has a `terrain` entity and a NAME-h.png (`read(suffix)` reads the file
    beside the map): (PNG of the heights in red and the dirt in green, {offset, cell, scale, material, no_decals}). A vertex
    per pixel: (column - w/2, row - h/2) * cell from the offset in x and z, y = offset y + 8 * red * scale (scale_y;
    one map, a guess). The dirt mask, NAME-b.png, covers cells (texel c spans x (c - w/2) * cell to the next): each
    vertex gets the mean of the four texels around it."""
    from PIL import Image
    fields = next((fields for name, *_, fields in entities if name == 'terrain'), None)
    if fields is None:
        return None, None
    try:
        heights = Image.open(io.BytesIO(read('-h.png'))).convert('RGB')
    except (OSError, KeyError):
        return None, None
    try:
        texels = np.pad(np.asarray(Image.open(io.BytesIO(read('-b.png'))).convert('RGB').getchannel('R').resize(heights.size), float), ((1, 0), (1, 0)), 'edge')
        mask = Image.fromarray(np.round((texels[:-1, :-1] + texels[:-1, 1:] + texels[1:, :-1] + texels[1:, 1:]) / 4).astype(np.uint8))
    except (OSError, KeyError):
        mask = Image.new('L', heights.size)
    data = io.BytesIO()
    Image.merge('RGB', (heights.getchannel('R'), mask, Image.new('L', heights.size))).save(data, 'PNG')
    number = lambda key, default: float(fields.get(key) or default)
    return data.getvalue(), dict(offset=[number('offset_x', 0), number('offset_y', 0), number('offset_z', 0)], cell=number('cell_size', 40),
                                 scale=number('scale_y', 1), material=(fields.get('material') or fields.get('shader') or 'core_ter').lower(),
                                 no_decals=fields.get('no_decals') in ('1', 'true'))


def material_textures(packs, names, materials, output, replace=False):
    """Each named material's texture (scaled to TEXTURE_SIZE at most) and scale for materials.json; the names
    with no material or texture in the game files. A name with a variant (metalwall_heat01:3) is drawn as its
    material; a texture is in the packs as PATH.dds, or as PATH itself for some plain .png ones, or as PATH.png.dds
    where the shader leaves out .png (snow decals), or else under its file name beside the shader file
    (models/theme/simple/textures/black.png is in .../textures/colors/, with colors.shader)."""
    where = {}
    for pack in packs:
        for name in pack.files:
            if name.endswith(('.dds', '.png')):
                where.setdefault(name, pack)
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    find = lambda path, folder: next((path + suffix for path in [path.lower().replace('/', '\\')] for path in (path, folder + '\\' + path.rsplit('\\', 1)[-1])
                                      for suffix in ('.dds', '', '.png.dds') if path + suffix in where), None)
    decoded = {}

    def pixels(file):
        if file not in decoded:
            try:
                decoded[file] = decode_dds(where[file].read(file)).convert('RGB')
            except (OSError, ValueError):
                decoded[file] = None
        return decoded[file]

    def surface(flags, folder):
        """A lit material's specular map (red gloss, green strength: [r, g] when even, else a texture) and material
        id: its id map's red (the commonest value; ponytail: the game reads it per pixel) where not 0, else its
        material_id, else 40 (run 17's `default`, with no id map or material_id, is a mirror: its id reflects;
        most blocks' id map, textures/metal.png, is 40 too)."""
        if not flags.get('lit'):
            return {}
        file = flags.get('ids') and find(flags['ids'], folder)
        ids = pixels(file) if file else None
        values, counts = np.unique(np.asarray(ids)[..., 0], return_counts=True) if ids is not None else ([0], [1])
        out = {'id': int(values[np.argmax(counts)]) or flags['id'] or 40}
        file = flags.get('spec') and find(flags['spec'], folder)
        image = pixels(file) if file else None
        if image is None:
            return out
        rg = np.asarray(image)[..., :2]
        if rg.max() == 0:
            return out
        if np.ptp(rg[..., 0]) <= 2 and np.ptp(rg[..., 1]) <= 2:
            return out | {'spec': np.round(rg.reshape(-1, 2).mean(0) / 255, 3).tolist()}
        target = output / 'textures' / f"{hashlib.sha256(where[file].read(file)).hexdigest()[:16]}-s.png"
        if replace or not target.is_file():
            image = image.copy()
            if max(image.size) > TEXTURE_SIZE:
                image.thumbnail((TEXTURE_SIZE, TEXTURE_SIZE))
            image.save(target, 'PNG')
        return out | {'spec': target.name}

    entries, missing = {}, []
    for name in sorted(names):
        glass = next((flags['glass'] for *_, flags in materials.get((name or 'default').lower().split(':')[0], [])[:1] if flags.get('glass')), None)
        if glass:
            entries[name] = dict(glass=glass)
            continue
        files = [(found, scale, folder, flags) for path, scale, folder, flags in materials.get((name or 'default').lower().split(':')[0], [])
                 for found in [find(path, folder)] if found]
        if not files:
            missing.append(name or 'default')
            continue
        file, scale, folder, flags = files[0]
        if flags['hidden']:
            entries[name] = dict(hidden=True)
            continue
        raw = where[file].read(file)
        alpha = flags['cutout'] or flags['blend'] or flags['decal']
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
        entries[name] = (dict(texture=target.name, scale=scale) | {key: True for key in ('cutout', 'blend') if flags[key]} |
                         ({'accents': flags['accents']} if any(a is not None for a in flags['accents']) else {}) |
                         ({} if '#' in name else surface(flags, folder)))
    return entries, sorted(set(missing))


def write_envmaps(pack, names, output, replace=False):
    """Each named envmap (textures/cubemaps/NAME.dds in `pack`, textures_cubemaps.dbp: BC7 cubes, 1024 square with
    every mip) as envmaps/NAME.png: its third mip's six faces side by side. Returns the names written or found."""
    done = []
    (output / 'envmaps').mkdir(parents=True, exist_ok=True)
    from PIL import Image
    for name in sorted(names):
        file, target = f'textures\\cubemaps\\{name}.dds', output / 'envmaps' / f'{map_id(name)}.png'
        if file not in pack.files:
            continue
        if replace or not target.is_file():
            raw = pack.read(file)
            height, width, _, _, mips = struct.unpack_from('<5I', raw, 12)
            if raw[84:88] != b'DX10' or struct.unpack_from('<I', raw, 128)[0] not in (98, 99) or mips < 3:
                continue
            sizes = [max(1, (width >> m) + 3 >> 2) * max(1, (height >> m) + 3 >> 2) * 16 for m in range(mips)]
            faces = [Image.open(io.BytesIO(_dds(98, width >> 2, height >> 2, raw[148 + face * sum(sizes) + sizes[0] + sizes[1]:]))).convert('RGB')
                     for face in range(6)]
            strip = Image.new('RGB', (faces[0].width * 6, faces[0].height))
            for face, image in enumerate(faces):
                strip.paste(image, (face * image.width, 0))
            strip.save(target, 'PNG')
        done.append(name)
    return done


def convert_models(packs, paths, materials, output, replace=False, previous=None, heads=None, pickups=None):
    """Each model path's FBX (models/PATH.fbx in the packs) as models/HASH.bin, triangles of 8 float32 each corner
    (position, normal, uv in the page's axes) grouped by material, for models.json: {path: {file, groups: [[material,
    corners]]}}. A group's material is the first defined of PATH_MATERIAL, MATERIAL, PATH and the shader its dynamic
    asset draws it with (`heads`, piece_shaders; the FBX's own material names say little: "1024"), else the material named most like the model (longest common start) in the nearest
    .shader file at or above the model's folder (many pieces of a dynamic prop share one: trim01b takes trim01a's).
    Models of the `previous` models.json (of this FORMAT) whose file is there are kept unless replacing. A path
    pickup/KIND in `pickups` ({path: (model, scale, centred)}, pickup_kinds) is that model, named and centred as a pickup.
    Also returns the paths with no readable FBX."""
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
        if not replace and (previous or {}).get(path, {}).get('format') == FORMAT and (output / 'models' / previous[path]['file']).is_file():
            entries[path] = previous[path]
            continue
        file = 'models\\' + (pickups[path][0] if path in (pickups or {}) else path).replace('/', '\\') + '.fbx'
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
        names = [next((m for m in (f'{path}_{name}'.lower(), name.lower(), path, (heads or {}).get(path)) if m in materials), own) for name in groups]
        triangles = [np.concatenate(parts, 2) for parts in groups.values()]
        if path in (pickups or {}):
            # A pickup's shaders go by the entity's kind (hpt.shader says so): KIND_MATERIAL (after the FBX's
            # namespace:), the model's own, KIND. Groups with none (the melee weebles' arms) are left out.
            kind, (model, _, centred) = path.split('/', 1)[1], pickups[path]
            names = [next((m for m in (f"{kind}_{name.rsplit(':', 1)[-1]}".lower(), model, kind) if m in materials), None) for name in groups]
            triangles = [t for name, t in zip(names, triangles) if name]
            names = [name for name in names if name]
            if centred and triangles:
                corners = np.concatenate([t[..., :3].reshape(-1, 3) for t in triangles])
                middle = (corners.min(0) + corners.max(0)) / 2
                triangles = [np.concatenate([t[..., :3] - middle, t[..., 3:]], -1).astype(np.float32) for t in triangles]
            target = target.with_name(f'{hashlib.sha256(raw + path.encode()).hexdigest()[:16]}.bin')
        if replace or not target.is_file():
            target.write_bytes(b''.join(t.tobytes() for t in triangles))
        entries[path] = dict(format=FORMAT, file=target.name, groups=[[name, 3 * len(t)] for name, t in zip(names, triangles)])
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
    cubes = next((pack for pack in packs if pack.path.name == 'textures_cubemaps.dbp'), None)
    has_cube = lambda name: cubes is not None and f'textures\\cubemaps\\{name}.dds' in cubes.files
    # A map's reader takes the suffix of the file to read beside it: '.rbe', '-h.png'...
    sources = [(name.rsplit('\\', 1)[-1][:-4], 'Diabotical', lambda suffix, name=name[:-4]: stock.read(name + suffix))
               for name in sorted(stock.files) if name.endswith('.rbe')]
    sources += [(path.stem, 'Your maps', lambda suffix, path=path: path.with_name(path.stem + suffix).read_bytes())
                for path in (user_maps() if extra is None else extra)]
    (output / 'maps').mkdir(parents=True, exist_ok=True)
    index_path = output / 'index.json'
    index = {item['id']: item for item in json.loads(index_path.read_text(encoding='utf-8'))} if index_path.is_file() else {}
    files = lambda item: [item.get('file'), item.get('entities'), (item.get('terrain') or {}).get('file')]
    result = dict(imported=[], skipped=[], failed={}, untextured=[], unconverted=[])
    assets, found = read_assets(packs), read_materials(packs)
    fbx = {}
    for pack in packs:
        for name in pack.files:
            if name.endswith('.fbx'):
                fbx.setdefault(name, pack)
    fbx_file = lambda model: 'models\\' + model.replace('/', '\\') + '.fbx'
    is_fbx = lambda raw: raw[:18] == b'Kaydara FBX Binary' or raw.lstrip()[:5] == b'; FBX'
    pickups = pickup_kinds(assets, lambda model: fbx_file(model) in fbx and is_fbx(fbx[fbx_file(model)].read(fbx_file(model))))
    for name, group, read in sources:
        ident = map_id(name if group == 'Diabotical' else 'user__' + name)
        try:
            raw = read('.rbe')
            digest = hashlib.sha256(raw).hexdigest()[:12]
            # Maps imported by an older version of this import (another FORMAT) are read again.
            if not replace and index.get(ident, {}).get('hash') == digest and index[ident].get('format') == FORMAT and all(
                    (output / 'maps' / file).is_file() for file in files(index[ident]) if file):
                result['skipped'].append(name)
                continue
            parsed = read_map(raw)
            blocks = visible_blocks(parsed['blocks'])
            props, tints, markers, liquids, decals = placements(parsed['entities'], assets, found, {kind: scale for kind, (_, scale, _) in pickups.items()})
            heights, terrain = read_terrain(parsed['entities'], read)
            xyz = parsed['blocks']['xyz']
            lights = read_lights(parsed['entities'], (xyz.min(0) * BLOCK, (xyz.max(0) + 1) * BLOCK) if len(xyz) else None)
            if not has_cube(lights['envmap']):  # Not in the game's files: its default.
                lights['envmap'] = 'default_envmap' if has_cube('default_envmap') else None
        except (OSError, ValueError, EOFError, struct.error) as exc:
            result['failed'][name] = str(exc)
            index.pop(ident, None)
            continue
        head = json.dumps(dict(props=[[key, len(value), int(key in tints)] for key, value in props.items()], markers=markers, liquids=liquids,
                               decals=[[key, len(value[0])] for key, value in decals.items()], lights=lights,
                               billboards=read_billboards(parsed['entities']), pfx=read_pfx(parsed['entities'], assets, pickups)),
                          separators=(',', ':')).encode()
        head += b' ' * (-len(head) % 4)
        placed = (struct.pack('<I', len(head)) + head + b''.join(value.tobytes() for value in props.values()) +
                  b''.join(tints[key].tobytes() for key in props if key in tints) +
                  b''.join(value[0].tobytes() for value in decals.values()) + b''.join(value[1].tobytes() for value in decals.values()))
        # Named by content: served as immutable, and a newer import can place the same map's props differently.
        file, entities = f'{ident}-{digest}.bin', f'{ident}-{hashlib.sha256(placed).hexdigest()[:12]}.ent'
        if terrain:
            terrain['file'] = f'{ident}-{hashlib.sha256(heights).hexdigest()[:12]}.png'
        item = dict(id=ident, format=FORMAT, name=name, group=group, file=file, entities=entities, terrain=terrain, hash=digest, version=parsed['version'],
                    author=parsed['author'], materials=parsed['materials'], blocks=len(blocks))
        for old in files(index.get(ident, {})):
            if old and old not in files(item):
                (output / 'maps' / old).unlink(missing_ok=True)
        (output / 'maps' / file).write_bytes(blocks.tobytes())
        (output / 'maps' / entities).write_bytes(placed)
        if terrain:
            (output / 'maps' / terrain['file']).write_bytes(heights)
        index[ident] = item
        result['imported'].append(name)
    # Maps since deleted from the game or the editor's folder leave the list.
    present = {map_id(name if group == 'Diabotical' else 'user__' + name) for name, group, _ in sources}
    for ident in [ident for ident in index if ident not in present]:
        for old in files(index.pop(ident)):
            if old:
                (output / 'maps' / old).unlink(missing_ok=True)
    keys, decal_materials, envmaps, systems = set(), set(), set(), set()
    for item in index.values():
        with open(output / 'maps' / item['entities'], 'rb') as file:
            length, = struct.unpack('<I', file.read(4))
            head = json.loads(file.read(length))
            keys |= {entry[0] for entry in head['props']}
            decal_materials |= {entry[0] for entry in head['decals']} | {entry[13] for entry in head.get('billboards', []) if entry[13]}
            envmaps.add(head['lights']['envmap'])
            systems |= {entry[12] for entry in head.get('pfx', [])}
    if cubes is not None:
        write_envmaps(cubes, envmaps - {None}, output, replace)
    models_path = output / 'models.json'
    previous = json.loads(models_path.read_text(encoding='utf-8')) if models_path.is_file() else {}
    models, result['unconverted'] = convert_models(packs, {key.split('|')[0] for key in keys}, found, output, replace, previous, piece_shaders(assets, found),
                                                         {f'pickup/{kind}': value for kind, value in pickups.items()})
    models_path.write_text(json.dumps(models, separators=(',', ':'), sort_keys=True), encoding='utf-8')
    used = ({name for item in index.values() for name in item['materials']} | {key.split('|')[1] for key in keys if key.split('|')[1]} |
            {name for model in models.values() for name, _ in model['groups']} |
            {name for item in index.values() if item.get('terrain') for name in (item['terrain']['material'], *(
                item['terrain']['material'] + slot for slot in ('#3', '#5') if item['terrain']['material'] + slot in found))})
    # A prop's material X on a model whose group shader is G: the variant G_X where the game has one (viewer.js).
    used |= {f'{name}_{key.split("|")[1]}' for key in keys if key.split('|')[1] for name, _ in models.get(key.split('|')[0], {}).get('groups', [])
             if f'{name}_{key.split("|")[1]}' in found}
    used |= {name + '#4' for name in used if name + '#4' in found}  # Colour masks.
    # A billboard's texture may be a path rather than a material: a texture-only material of that name.
    found |= {name: [(name, 1.0, '', dict(cutout=False, blend=False, hidden=False, decal=True, accents=[None] * 3, lit=False))]
              for name in decal_materials if name not in found}
    used |= decal_materials
    materials, result['untextured'] = material_textures(packs, used, found, output, replace)
    (output / 'materials.json').write_text(json.dumps(materials, separators=(',', ':'), sort_keys=True), encoding='utf-8')
    (output / 'particles.json').write_text(json.dumps(particle_systems(packs, systems, output, replace), separators=(',', ':'), sort_keys=True),
                                           encoding='utf-8')
    temporary = index_path.with_suffix('.tmp')
    rank = {'Diabotical': 0, 'Your maps': 1}
    temporary.write_text(json.dumps(sorted(index.values(), key=lambda item: (rank[item['group']], item['name'].lower())),
                                    separators=(',', ':')), encoding='utf-8')
    temporary.replace(index_path)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-base', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'diabotical-maps')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    done = import_maps(args.game_base, args.output, args.replace)
    print(f"Imported {len(done['imported'])}, skipped {len(done['skipped'])}, failed {len(done['failed'])}")
    for name, reason in done['failed'].items(): print('FAILED', name, reason)
    if done['untextured']: print('NO TEXTURE FOR', ', '.join(done['untextured']))

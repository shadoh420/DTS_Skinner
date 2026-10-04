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

Materials: an asset (scripts/*.assets: asset NAME { type surface_material material MATERIAL }) names a material,
defined in a .shader file (NAME { { map colour, map normal, ...  uv_scale s } }). The page draws a face with the
material's first map, its texture repeating every 40 / s units, as the export's texture coordinates do. A name
defined more than once takes the first definition (packs in name order) whose texture is found, as the game does
for black: its black blocks have models_theme.dbp's uv_scale 0.5, not scripts.dbp's 1 (measured in its /export).

Written (under local-data/diabotical-maps): index.json, maps/ID-HASH.bin per map (16-byte blocks: int16 x, y, z,
u8 shape, turn, open faces (bit per face, in the order above), 0, then six u8 face materials) and the materials of
the maps in materials.json, with textures/HASH.png.
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

import numpy as np

try:
    from tools.reflex_textures import decode_dds
except ImportError:  # Run as a script from tools/.
    from reflex_textures import decode_dds

RECORD = {24: 46, 25: 52, 26: 53, 27: 53}
TURN = {24: 44, 25: 50, 26: 50, 27: 50}
CUBE, HALF = 1, 3
# Neighbours across each face, in the order the faces' materials are stored (+z, -x, -z, +x, top, bottom).
NEIGHBOURS = ((0, 0, 1), (-1, 0, 0), (0, 0, -1), (1, 0, 0), (0, 1, 0), (0, -1, 0))
OUT = np.dtype([('x', '<i2'), ('y', '<i2'), ('z', '<i2'), ('shape', 'u1'), ('turn', 'u1'), ('open', 'u1'), ('pad', 'u1'), ('faces', 'u1', 6)])
TEXTURE_SIZE = 512
ASSET = re.compile(r'\basset\s+(\S+)\s*\{([^{}]*)\}')
SHADER = re.compile(r'(?:^|\n)\s*([^\s{}]+)\s*\{\s*\{(.*?)\}', re.S)


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
    return dict(version=version, author=author, materials=materials, blocks=dict(
        xyz=records[:, :12].copy().view('<i4'), shape=records[:, 12].copy(), turn=records[:, TURN[version]] & 3, faces=records[:, 25:31].copy()))


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
    """{asset or material name: [(colour map path, uv_scale, shader file's folder), ...]} from every pack's .assets
    and .shader files: every definition of a name, in the order read, as some are defined more than once."""
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
                    if found:
                        shaders.setdefault(shader.lower(), []).append((found.group(1), float(scale.group(1)) if scale else 1.0, name.rsplit('\\', 1)[0]))
    return {name: shaders[material] for name, material in assets.items() if material in shaders} | shaders


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
        files = [(path + suffix, scale) for path, scale, folder in materials.get((name or 'default').lower().split(':')[0], [])
                 for path in [path.lower().replace('/', '\\')] for path in (path, folder + '\\' + path.rsplit('\\', 1)[-1])
                 for suffix in ('.dds', '') if path + suffix in where]
        if not files:
            missing.append(name or 'default')
            continue
        file, scale = files[0]
        raw = where[file].read(file)
        target = output / 'textures' / f'{hashlib.sha256(raw).hexdigest()[:16]}.png'
        if replace or not target.is_file():
            try:
                image = decode_dds(raw).convert('RGB')
            except (OSError, ValueError):
                missing.append(name or 'default')
                continue
            if max(image.size) > TEXTURE_SIZE:
                image.thumbnail((TEXTURE_SIZE, TEXTURE_SIZE))
            data = io.BytesIO()
            image.save(data, 'PNG')
            target.write_bytes(data.getvalue())
        entries[name] = dict(texture=target.name, scale=scale)
    return entries, sorted(set(missing))


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
    result = dict(imported=[], skipped=[], failed={}, untextured=[])
    for name, group, read in sources:
        ident = map_id(name if group == 'Diabotical' else 'user__' + name)
        try:
            raw = read()
            digest = hashlib.sha256(raw).hexdigest()[:12]
            if not replace and index.get(ident, {}).get('hash') == digest and (output / 'maps' / index[ident]['file']).is_file():
                result['skipped'].append(name)
                continue
            parsed = read_map(raw)
            blocks = visible_blocks(parsed['blocks'])
        except (OSError, ValueError, EOFError, struct.error) as exc:
            result['failed'][name] = str(exc)
            index.pop(ident, None)
            continue
        file = f'{ident}-{digest}.bin'
        old = index.get(ident, {}).get('file')
        if old and old != file and (output / 'maps' / old).is_file():
            (output / 'maps' / old).unlink()
        (output / 'maps' / file).write_bytes(blocks.tobytes())
        index[ident] = dict(id=ident, name=name, group=group, file=file, hash=digest, version=parsed['version'],
                            author=parsed['author'], materials=parsed['materials'], blocks=len(blocks))
        result['imported'].append(name)
    # Maps since deleted from the game or the editor's folder leave the list.
    present = {map_id(name if group == 'Diabotical' else 'user__' + name) for name, group, _ in sources}
    for ident in [ident for ident in index if ident not in present]:
        (output / 'maps' / index.pop(ident)['file']).unlink(missing_ok=True)
    used = {name for item in index.values() for name in item['materials']}
    materials, result['untextured'] = material_textures(packs, used, read_materials(packs), output, replace)
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

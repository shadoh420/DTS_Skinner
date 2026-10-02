"""Local Reflex Arena map import for the Reflex Maps page. Retail data stays outside source and builds.

python tools/import_reflex_map.py --game-base "C:/Program Files (x86)/Steam/steamapps/common/Reflex Arena" [--replace]

Reflex draws its map files as they are (no compile step), so the pack holds the .map files themselves, read in
the browser by static/reflex-maps/mapfile.js. A file is taken as a map when its first line is "reflex map
version N", wherever it lies in the game folder; Steam Workshop maps are read from the workshop folder beside the
install (steamapps/workshop/content/328070), when there is one.

Material colours: the game's materials are in the zip archives of its base folder (base/common.pak holds
common/materials/stone/concrete.material). A material file names its shader and holds typed parameters; the
colour of each material the maps name is its albedo parameter (diffuseColour in some, and for a textured
material its tintColor, which tints the texture: structural/dev/dev_grey128 is a grid texture tinted 0.5 grey),
written to materials.json with its shader. A face with no colour of its own is drawn in it. Materials without
one keep the page's guess from their name.

Material file (version 0x14, magic 0xd00e; little-endian): u16 version, u16 magic, shader name (128 bytes), u32
flags, u32 parameter count, u32 0, then per parameter 260 bytes: u32 type, name (128 bytes), value (128 bytes).
Types: 0 to 3 are one to four floats (roughness; uvScale; tintColor; albedo), 4 a texture path.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile

WORKSHOP_APP = '328070'
HEADER = re.compile(rb'reflex map version (\d+)\s*$')
MATERIAL_MAGIC = b'\x14\x00\x0e\xd0'
PARAMETER = 260


def map_id(name):
    return re.sub(r'[^a-z0-9_-]', '_', name.lower())


def read_header(path):
    """The map file version, or None when the file is not a Reflex map."""
    try:
        with open(path, 'rb') as handle:
            match = HEADER.match(handle.readline(64))
    except OSError:
        return None
    return int(match.group(1)) if match else None


def describe(text):
    """Title, author and the materials a map's faces name, read from its text."""
    title = author = ''
    group = None
    materials = set()
    for line in text.splitlines():
        if line and not line.startswith('\t'):
            group = line.split(' ', 1)[0]
        elif group == 'global' and line.startswith('\t\tString256 title ') and not title:
            title = line.split(' ', 2)[2].strip()
        elif group == 'global' and line.startswith('\t\tString256 ownerString ') and not author:
            author = line.split(' ', 2)[2].strip()
        if line.startswith('\t\t\t'):
            words = line.split()
            # A face: five numbers, vertex indices, a colour, then the material (empty or absent).
            at = 5
            while at < len(words) and words[at].isdigit():
                at += 1
            if at < len(words) and words[at].startswith('0x'):
                at += 1
            if 5 < at < len(words) and len(words) > 5 and re.fullmatch(r'-?\d+\.\d+', words[0]):
                materials.add(' '.join(words[at:]))
    return title, author, materials


def workshop_folder(game):
    """steamapps/workshop/content/328070 beside steamapps/common/<game>, when it exists."""
    for parent in game.parents:
        if parent.name.lower() == 'steamapps':
            folder = parent / 'workshop' / 'content' / WORKSHOP_APP
            return folder if folder.is_dir() else None
    return None


def find_maps(game):
    """(path, group) for every Reflex map of the install and of the Workshop folder beside it."""
    found = []
    for path in sorted(game.rglob('*.map')):
        if read_header(path) is not None:
            found.append((path, 'Reflex Arena'))
    workshop = workshop_folder(game)
    if workshop:
        for path in sorted(workshop.rglob('*.map')):
            if read_header(path) is not None:
                found.append((path, 'Steam Workshop'))
    return found


def read_material(raw):
    """A material file's shader and parameters: {name: a float, a list of 2 to 4 floats, or a texture path}."""
    if len(raw) < 144 or raw[:4] != MATERIAL_MAGIC:
        raise ValueError('not a Reflex material file')
    text = lambda chunk: chunk.split(b'\0', 1)[0].decode('latin-1')
    count = struct.unpack_from('<I', raw, 136)[0]
    if len(raw) < 144 + count * PARAMETER:
        raise ValueError('material file is cut short')
    parameters = {}
    for index in range(count):
        at = 144 + index * PARAMETER
        kind = struct.unpack_from('<I', raw, at)[0]
        name, value = text(raw[at + 4:at + 132]), raw[at + 132:at + 260]
        parameters[name] = (struct.unpack_from('<f', value)[0] if kind == 0 else list(struct.unpack_from(f'<{kind + 1}f', value)) if kind < 4
                            else text(value) if kind == 4 else value.hex())
    return text(raw[4:132]), parameters


def material_files(game):
    """Readers of every material of the game folder by lower-case name (common/materials/stone/concrete): loose
    .material files under base first, then those in its .pak archives."""
    base = next((folder for folder in game.iterdir() if folder.is_dir() and folder.name.lower() == 'base'), game)
    found = {}
    for path in sorted(base.rglob('*.material')):
        found.setdefault(path.relative_to(base).with_suffix('').as_posix().lower(), (path.read_bytes, str(path.relative_to(game))))
    for pak in sorted(base.glob('*.pak')):
        try:
            archive = zipfile.ZipFile(pak)
        except (OSError, zipfile.BadZipFile):
            continue
        for entry in archive.namelist():
            if entry.lower().endswith('.material'):
                found.setdefault(entry[:-len('.material')].lower(), (lambda archive=archive, entry=entry: archive.read(entry), pak.name))
    return found


def material_colours(game, names):
    """The albedo and shader of each named material the game folder has; and the names it has no colour for."""
    files, colours = material_files(game), {}
    for name in sorted(name for name in names if name):
        if name.lower() not in files:
            continue
        read, source = files[name.lower()]
        try:
            shader, parameters = read_material(read())
        except (OSError, ValueError, struct.error, zipfile.BadZipFile):
            continue
        key = next((key for key in ('albedo', 'diffuseColour', 'tintColor') if isinstance(parameters.get(key), list) and len(parameters[key]) >= 3), None)
        if key:
            colours[name] = dict(colour=[round(channel, 4) for channel in parameters[key][:3]], shader=shader, source=source,
                                 **{key: round(parameters[key], 4) for key in ('metallic', 'roughness') if isinstance(parameters.get(key), float)},
                                 **({'tints': parameters.get('textureAlbedoSpec') or parameters.get('textureDiffuse') or ''} if key == 'tintColor' else {}))
    return colours, sorted(name for name in names if name and name not in colours)


def import_maps(game, output, replace=False):
    """Import every map of the Reflex Arena folder `game` (and its Workshop maps). Returns imported, skipped and
    failed maps, how many material colours were read and which materials have none."""
    game, output = Path(game).expanduser(), Path(output)
    if not game.is_dir():
        raise ValueError('Reflex Arena folder does not exist')
    maps = find_maps(game)
    if not maps:
        raise ValueError('No Reflex map files here. Enter the Reflex Arena folder.')
    (output / 'maps').mkdir(parents=True, exist_ok=True)
    index_path = output / 'index.json'
    index = {item['id']: item for item in json.loads(index_path.read_text(encoding='utf-8'))} if index_path.is_file() else {}
    result = dict(imported=[], skipped=[], failed={}, materials=0, uncoloured=[])
    used = set()
    for path, group in maps:
        name = path.stem
        ident = map_id(name if group == 'Reflex Arena' else f'workshop__{path.parent.name}__{name}')
        if ident in index and (output / 'maps' / index[ident]['file']).is_file() and not replace:
            result['skipped'].append(name)
            used.update(index[ident].get('materials', []))
            continue
        try:
            raw = path.read_bytes()
            text = raw.decode('utf-8')
        except (OSError, UnicodeDecodeError) as exc:
            result['failed'][name] = str(exc)
            continue
        title, author, materials = describe(text)
        file = f'{ident}-{hashlib.sha256(raw).hexdigest()[:12]}.map'
        old = index.get(ident, {}).get('file')
        if old and old != file and (output / 'maps' / old).is_file():
            (output / 'maps' / old).unlink()
        (output / 'maps' / file).write_bytes(raw)
        index[ident] = dict(id=ident, name=name, title=title, author=author, group=group, file=file, version=read_header(path),
                            source=str(path.relative_to(game) if path.is_relative_to(game) else path.name), materials=sorted(materials))
        result['imported'].append(name)
        used.update(materials)
    colours, result['uncoloured'] = material_colours(game, used)
    result['materials'] = len(colours)
    (output / 'materials.json').write_text(json.dumps(colours, separators=(',', ':'), sort_keys=True), encoding='utf-8')
    temporary = index_path.with_suffix('.tmp')
    rank = {'Reflex Arena': 0, 'Steam Workshop': 1}
    listed = sorted(index.values(), key=lambda item: (rank.get(item['group'], 2), item['name'].lower()))
    temporary.write_text(json.dumps(listed, separators=(',', ':')), encoding='utf-8')
    temporary.replace(index_path)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-base', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'local-data/reflex-maps')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    done = import_maps(args.game_base, args.output, args.replace)
    print(f"Imported {len(done['imported'])}, skipped {len(done['skipped'])}, failed {len(done['failed'])}; "
          f"{done['materials']} material colours read")
    for name, reason in done['failed'].items(): print('FAILED', name, reason)
    if done['uncoloured']: print('NO ALBEDO FOR', ', '.join(done['uncoloured']))

"""Local Reflex Arena map import for the Reflex Maps page. Retail data stays outside source and builds.

python tools/import_reflex_map.py --game-base "C:/Program Files (x86)/Steam/steamapps/common/Reflex Arena" [--replace]

Reflex draws its map files as they are (no compile step), so the pack holds the .map files themselves, read in
the browser by static/reflex-maps/mapfile.js. A file is taken as a map when its first line is "reflex map
version N", wherever it lies in the game folder; Steam Workshop maps are read from the workshop folder beside the
install (steamapps/workshop/content/328070), when there is one.

Material colours: for each material the maps name (common/materials/stone/concrete) an image whose path ends in
that name, plus an optional _albedo/_diffuse/_d/_col suffix, is looked for in the game folder and its mean colour
written to materials.json. A face with no colour of its own is drawn in it. Materials with no such image keep the
page's guess from their name.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from PIL import Image

WORKSHOP_APP = '328070'
HEADER = re.compile(rb'reflex map version (\d+)\s*$')
IMAGES = ('.png', '.tga', '.jpg', '.jpeg', '.dds')
SUFFIXES = ('', '_albedo', '_diffuse', '_diff', '_d', '_col', '_color', '_colour', '_basecolor')


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


def material_colours(game, names):
    """Mean colour of an image for each material name, where the game folder has one; and the names without."""
    wanted = {name.lower(): name for name in names if name}
    images = {}
    for path in game.rglob('*'):
        if path.suffix.lower() not in IMAGES or not path.is_file():
            continue
        stem = path.with_suffix('').as_posix().lower()
        for suffix in SUFFIXES:
            if suffix and not stem.endswith(suffix):
                continue
            key = stem[:len(stem) - len(suffix)] if suffix else stem
            for lowered, name in wanted.items():
                if key.endswith('/' + lowered) and (name not in images or SUFFIXES.index(suffix) < images[name][1]):
                    images[name] = (path, SUFFIXES.index(suffix))
    colours = {}
    for name, (path, _) in images.items():
        try:
            with Image.open(path) as image:
                pixel = image.convert('RGB').resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        except (OSError, ValueError):
            continue
        colours[name] = [round(channel / 255, 4) for channel in pixel]
    return colours, sorted(name for name in wanted.values() if name not in colours)


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
    if done['uncoloured']: print('NO IMAGE FOR', ', '.join(done['uncoloured']))

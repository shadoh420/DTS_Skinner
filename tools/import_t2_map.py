"""Prepare the Tribes 2 map pack from an install's stock archives; never modify the game install.

python tools/import_t2_map.py --game-base C:/Dynamix/Tribes2/GameData [--replace]
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import zipfile

# Later archives win. On the install checked, this order and the game's (by archive name) choose the same files.
ARCHIVES = ('base.vl2', 'scripts.vl2', 'missions.vl2', 'shapes.vl2',
            'interiors.vl2', 'textures.vl2', 'skins.vl2', 'badlands.vl2',
            'desert.vl2', 'ice.vl2', 'lava.vl2', 'lush.vl2')
# Map packs a stock install may lack. The TR2 server archive holds the game type scripts TR2 missions run.
OPTIONAL = ('Classic_maps_v1.vl2', 'TR2final105-client.vl2', 'TR2final105-server.vl2')
# Not .bm8: the game's paletted copy of nearly every .png (126 MB of them), which t2-mapper never reads.
EXTENSIONS = {'.cs', '.mis', '.ter', '.dts', '.dsq', '.dif', '.png', '.jpg', '.jpeg', '.bmp', '.ifl', '.dml'}
# Short codes as exogen/t2-mapper's src/mission.ts normalizes them.
MISSION_TYPES = {name.lower(): name for name in (
    'Arena', 'Bounty', 'CnH', 'CTF', 'DM', 'DnD', 'Hunters', 'LakRabbit', 'LakZM', 'LCTF', 'None', 'Rabbit',
    'SCtF', 'Siege', 'SinglePlayer', 'TDM', 'TeamHunters', 'TeamLak', 'TR2')}


def mission_info(text):
    """DisplayName and MissionTypes from the comments outside any BEGIN/END section, as t2-mapper's parseMissionScript."""
    found, section = {}, False
    for comment in re.findall(r'^[ \t]*//(.*)$', text, re.M):
        if re.match(r'[ \t]*-+[ \t]*[A-Z ]+[ \t]+(BEGIN|END)[ \t]*-+$', comment, re.I):
            section = ' begin' in comment.lower()
        elif not section:
            match = re.match(r'[ \t]*(DisplayName|MissionTypes)[ \t]*=[ \t]*(.+)$', comment, re.I)
            if match:
                found[match[1].lower()] = match[2]
    return found.get('displayname'), [MISSION_TYPES.get(name.lower(), name) for name in found.get('missiontypes', '').split()]


def import_maps(game_base, output, replace=False):
    """Import every mission of the install into one shared pack. Returns a report."""
    game_base, output = Path(game_base), Path(output)
    if (game_base / 'base').is_dir():
        game_base = game_base / 'base'
    if not game_base.is_dir():
        raise ValueError('Game folder not found: %s' % game_base)
    for name in ARCHIVES:
        if not (game_base / name).is_file():
            raise ValueError('Missing stock game archive: ' + name)
    existing = output / 'manifest.json'
    if output.exists() and not (existing.is_file() and (output / 'SOURCES.json').is_file()):
        raise ValueError('Choose a new output directory; only a T2 map pack is ever replaced')
    if output.exists() and not replace:
        return {'imported': [], 'skipped': sorted(json.loads(existing.read_text(encoding='utf-8'))['missions']), 'warnings': {}, 'resources': 0}
    resources, missions, provenance = {}, {}, []
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='importing-', dir=output.parent))
    try:
        for name in ARCHIVES + tuple(name for name in OPTIONAL if (game_base / name).is_file()):
            archive_path = game_base / name
            with archive_path.open('rb') as stream:
                provenance.append({'archive': name, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()})
            with zipfile.ZipFile(archive_path) as archive:
                for entry in archive.infolist():
                    original = PurePosixPath(entry.filename.replace('\\', '/'))
                    path = PurePosixPath(str(original).lower())
                    if entry.is_dir() or path.suffix not in EXTENSIONS:
                        continue
                    if path.is_absolute() or '..' in path.parts or ':' in str(path):
                        raise ValueError('Unsafe archive path: ' + entry.filename)
                    data = archive.read(entry)
                    if path.suffix in {'.cs', '.mis', '.ifl', '.dml'}:
                        try:
                            text = data.decode('utf-8-sig')
                        except UnicodeDecodeError:
                            text = data.decode('cp1252')
                        text = text.replace('\r\r\n', '\n').replace('\r\n', '\n')
                        data = text.encode('utf-8')
                        if path.suffix == '.mis' and path.parent.name == 'missions':
                            for stale in [key for key, value in missions.items() if value['resourcePath'] == str(path)]:
                                del missions[stale]
                            display, types = mission_info(text)
                            missions[original.stem] = {'resourcePath': str(path), 'displayName': display, 'missionTypes': types,
                                                       'source': name, 'terrain': re.search(r'terrainFile\s*=\s*"([^"]+)"', text, re.I)}
                    target = staging / 'base' / str(path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    resources[str(path)] = [str(path), ['']]
        if 'scripts/server.cs' not in resources:
            raise ValueError('Required map resource missing: scripts/server.cs')
        if not missions:
            raise ValueError('No missions found in the game archives')
        warnings = {}
        for name, mission in missions.items():
            terrain = mission.pop('terrain')
            if not terrain:
                warnings[name] = ['Mission names no terrain']
            elif 'terrains/' + terrain[1].replace('\\', '/').split('/')[-1].lower() not in resources:
                warnings[name] = ['Terrain not found: ' + terrain[1]]
        # Mount transforms are added by the viewer on first open (mounts.json), with t2-mapper's own shape reader.
        (staging / 'manifest.json').write_text(json.dumps({'resources': resources, 'mounts': {}, 'missions': missions}), encoding='utf-8')
        (staging / 'skins.json').write_text('{}', encoding='utf-8')
        (staging / 'SOURCES.json').write_text(json.dumps({
            'missions': len(missions), 'policy': 'Explicit stock archive order; later archives win. No loose or mod overrides.',
            'archives': provenance}, indent=2), encoding='utf-8')
        if output.exists():
            replaced = Path(tempfile.mkdtemp(prefix='replaced-', dir=output.parent))
            output.rename(replaced / 'pack')
            staging.rename(output)
            shutil.rmtree(replaced, ignore_errors=True)
        else:
            staging.rename(output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {'imported': sorted(missions), 'skipped': [], 'warnings': warnings, 'resources': len(resources)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game-base', type=Path, required=True, help='Tribes 2 GameData folder or its base folder')
    parser.add_argument('--output', type=Path, default=Path('local-data/t2-maps'))
    parser.add_argument('--replace', action='store_true', help='rebuild a map pack that already exists')
    args = parser.parse_args()
    result = import_maps(args.game_base, args.output, args.replace)
    print('Imported %d missions (%d resources), skipped %d already imported, into %s' % (
        len(result['imported']), result['resources'], len(result['skipped']), args.output))
    for name, items in result['warnings'].items():
        for item in items:
            print('Warning:', name, '-', item)

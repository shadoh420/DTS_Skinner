"""Import Starsiege missions into a Maps pack the T1 Maps viewer draws (/maps/ss/).

Missions are binary (tools/starsiege_mission.py); everything after reading them is the T1 map importer's: the
mission's volumes mounted in order, terrain (Starsiege's 12 x 12 grid of blocks stitched into one), sky and planets
from the mission's resource tags, palette, and placed interiors and shapes converted from gameObjects.vol. Drop points
are the viewpoints. The install is only read.
"""
import argparse
import json
from pathlib import Path
import struct
import sys
import tempfile
import zipfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.import_t1_map import Install, import_mission, map_id, walk  # noqa: E402
from tools.local_data import LOCAL_DATA  # noqa: E402
from tools.starsiege_mission import read_mission, read_tags, read_vehicles  # noqa: E402


def mission_kind(path, base):
    """The mission's section on the Maps page, from where the game keeps it."""
    parts = [part.lower() for part in path.relative_to(base).parts[:-1]]
    if parts[:1] == ['multiplayer']:
        prefix = path.stem.split('_')[0].upper()
        return {'CTF': 'Capture the Flag', 'DM': 'Deathmatch', 'WAR': 'War'}.get(prefix, 'Multiplayer')
    if parts[:1] == ['campaign']:
        side = 'Cybrid campaign' if 'cybrid' in parts[1] else 'Human campaign'
        return side + (' (cutscene)' if path.stem.lower().startswith('cin_') else '')
    return 'Training' if parts[:1] == ['training'] else 'Other'


def fit_sky(scene, mission):
    """Starsiege's sky band stands `size` tall at `distance` from the camera, from the horizon up (SimObjects/Code/
    simSky.cpp renderSkyBand: 7500 at 20000); the T1 dome rings the terrain's visible distance and climbs to 0.75 of
    its size. Scale the size so the band's top edge keeps the game's angle."""
    sky = next((node['fields'] for node, _ in walk(mission) if node['class'] == 'sky'), {})
    if scene['sky'].get('textures') and float(sky.get('distance', 0)) > 0:
        radius = scene['terrain']['visibleDistance'] * 1.05
        scene['sky']['size'] = float(sky['size']) / float(sky['distance']) * radius / .75


def import_maps(game, output, missions=None, replace=False):
    """Import every mission of a Starsiege install (multiplayer, campaigns, training), or the given files."""
    install = Install(game)
    base = install.base
    if not (base / 'Starsiege.exe').is_file() and not (base / 'gameObjects.vol').is_file():
        raise ValueError('Not a Starsiege folder: %s' % base)
    scripts = install.volume(base / 'scripts.vol')
    tags = read_tags(scripts['mission.strings.cs']().decode('latin1'))
    vehicles = read_vehicles({name.lower(): reader().decode('latin1') for name, reader in scripts.items()
                              if name.lower() == 'datvehicle.cs' or name.lower().startswith(('datherc', 'dattank', 'datflyer', 'datdrone'))})
    paths = [Path(item) for item in missions] if missions else sorted(
        {path for folder in ('multiplayer', 'campaign', 'training') for path in (base / folder).rglob('*') if path.suffix.lower() == '.mis'})
    output = Path(output)
    report = {'imported': [], 'skipped': [], 'failed': {}, 'warnings': {}}
    with tempfile.TemporaryDirectory() as no_catalog:  # Every shape comes from the install: T1 shares names with it.
        for path in paths:
            if not replace and (output / 'maps' / map_id(path.stem) / 'scene.json').is_file():
                report['skipped'].append(path.stem)
                continue
            try:
                mission = read_mission(path.read_bytes(), tags, vehicles)
                scene = import_mission(install, path, output, Path(no_catalog) / 'model_json', mission)
            except (ValueError, KeyError, IndexError, OSError, struct.error, zipfile.BadZipFile) as error:
                report['failed'][path.stem] = str(error) or type(error).__name__
                continue
            scene['type'] = mission_kind(path, base)
            fit_sky(scene, mission)
            (output / 'maps' / scene['id'] / 'scene.json').write_text(json.dumps(scene), encoding='utf-8')
            report['imported'].append(path.stem)
            if scene['warnings']:
                report['warnings'][path.stem] = scene['warnings']
    maps = []
    for scene_path in sorted((output / 'maps').glob('*/scene.json')) if (output / 'maps').is_dir() else []:
        scene = json.loads(scene_path.read_text(encoding='utf-8'))
        maps.append({'id': scene['id'], 'name': scene['mission'], 'type': scene['type'], 'warnings': len(scene['warnings'])})
    output.mkdir(parents=True, exist_ok=True)
    (output / 'index.json').write_text(json.dumps(sorted(maps, key=lambda item: (item['type'], item['name'].lower()))), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game', type=Path, default=Path('C:/Dynamix/Starsiege'), help='Starsiege folder')
    parser.add_argument('--mission', type=Path, action='append', help='mission file; default: every mission in the install')
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'ss-maps')
    parser.add_argument('--replace', action='store_true', help='re-import maps that already exist in the output')
    args = parser.parse_args()
    result = import_maps(args.game, args.output, args.mission, args.replace)
    print('Imported %d, skipped %d existing, failed %d into %s' % (
        len(result['imported']), len(result['skipped']), len(result['failed']), args.output))
    for name, reason in result['failed'].items():
        print('Failed:', name, '-', reason)
    for name, items in result['warnings'].items():
        for item in items:
            print('Warning:', name, '-', item)

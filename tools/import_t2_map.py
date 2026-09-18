"""Prepare the bounded stock Katabatic map pack; never modify the game install."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ARCHIVES = ('base.vl2', 'scripts.vl2', 'missions.vl2', 'shapes.vl2',
            'interiors.vl2', 'textures.vl2', 'skins.vl2', 'badlands.vl2',
            'desert.vl2', 'ice.vl2', 'lava.vl2', 'lush.vl2')
EXTENSIONS = {'.cs', '.mis', '.ter', '.dts', '.dsq', '.dif', '.png', '.jpg', '.jpeg', '.bmp', '.bm8', '.ifl', '.dml'}


def import_map(game_base, output):
    if output.exists():
        raise ValueError('Choose a new output directory; existing map packs are never overwritten')
    for name in ARCHIVES:
        if not (game_base / name).is_file():
            raise ValueError('Missing stock game archive: ' + name)
    resources, provenance = {}, []
    output.mkdir(parents=True)
    for name in ARCHIVES:
        archive_path = game_base / name
        with archive_path.open('rb') as stream:
            provenance.append({'archive': name, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()})
        with zipfile.ZipFile(archive_path) as archive:
            for entry in archive.infolist():
                path = PurePosixPath(entry.filename.replace('\\', '/').lower())
                if entry.is_dir() or path.suffix not in EXTENSIONS:
                    continue
                if path.is_absolute() or '..' in path.parts or ':' in str(path):
                    raise ValueError('Unsafe archive path: ' + entry.filename)
                if path.suffix in {'.mis', '.ter'} and path.stem != 'katabatic':
                    continue
                data = archive.read(entry)
                if path.suffix in {'.cs', '.mis', '.ifl', '.dml'}:
                    try:
                        text = data.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        text = data.decode('cp1252')
                    data = text.replace('\r\r\n', '\n').replace('\r\n', '\n').encode('utf-8')
                target = output / 'base' / str(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                resources[str(path)] = [str(path), ['']]
    required = ('missions/katabatic.mis', 'terrains/katabatic.ter', 'scripts/server.cs')
    for name in required:
        if name not in resources:
            raise ValueError('Required map resource missing: ' + name)
    manifest = {'resources': resources, 'mounts': {}, 'missions': {
        'Katabatic': {'resourcePath': 'missions/katabatic.mis', 'displayName': 'Katabatic', 'missionTypes': ['CTF']}}}
    (output / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (output / 'skins.json').write_text('{}', encoding='utf-8')
    (output / 'SOURCES.json').write_text(json.dumps({'mission': 'Katabatic', 'policy': 'Explicit stock archive order; later archives win. No loose or mod overrides.', 'archives': provenance}, indent=2), encoding='utf-8')
    return len(resources)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-base', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('local-data/t2-maps'))
    args = parser.parse_args()
    print('Imported', import_map(args.game_base, args.output), 'resources into', args.output)

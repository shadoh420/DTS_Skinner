"""Build the offline map entry from an exact exogen/t2-mapper source checkout.

python tools/build_t2_maps.py --source C:/tmp/t2-mapper-skinner-reference
The first build requires Node/npm and network for npm ci. Runtime is offline.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

REVISION = 'e9b6332aaa48bc79535655d644bacc7e22b6ac79'
ROOT = Path(__file__).resolve().parents[1]


def build(source, map_data=None):
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if revision != REVISION:
        raise ValueError('Expected upstream revision ' + REVISION)
    if subprocess.check_output(['git', 'diff', 'HEAD', '--', 'src', 'generated', 'scripts', 'public', 'package.json', 'package-lock.json'], cwd=source):
        raise ValueError('Upstream tracked inputs must be unchanged')
    npm = shutil.which('npm.cmd') or shutil.which('npm')
    if not (source / 'node_modules/vite').exists():
        subprocess.run([npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=source, check=True)
    adapter = source / 'skinner'
    adapter.mkdir(exist_ok=True)
    for file in (ROOT / 'map-client').iterdir():
        shutil.copy2(file, adapter / file.name)
    if map_data:
        subprocess.run([shutil.which('node'), '--import=tsx/esm', 'skinner/mounts.ts', str(map_data)], cwd=source, check=True)
    output = ROOT / 'static/t2-maps'
    # Vite may remove only this generated bundle directory, never a linked target.
    if output.resolve() != ROOT.resolve() / 'static/t2-maps' or output.is_symlink():
        raise ValueError('Unexpected map bundle output path')
    env = dict(os.environ, SKINNER_MAP_OUTPUT=str(output))
    subprocess.run([npm, 'exec', '--', 'vite', 'build', '--config', 'skinner/vite.config.ts'], cwd=source, env=env, check=True)
    for name in ('white.png', 'black.png', 'magenta.png'):
        shutil.copy2(source / 'public' / name, output / name)
    (output / 'UPSTREAM.json').write_text(json.dumps({'repository': 'https://github.com/exogen/t2-mapper', 'revision': REVISION, 'author': 'Brian Beck (exogen)', 'declaredLicense': 'MIT', 'scope': 'Offline Katabatic map viewer; Skinner adapter in map-client'}, indent=2), encoding='utf-8')
    notices = ['T2 Maps uses exogen/t2-mapper by Brian Beck <exogen@gmail.com>.',
               'https://github.com/exogen/t2-mapper/tree/' + REVISION,
               'Upstream package.json declares MIT; this revision has no root LICENSE file.',
               'Adapter source: map-client/ in DTS Skinner. Game assets are separate.',
               '\nDependency notices (includes production packages beyond the bundled subset):']
    lock = json.loads((source / 'package-lock.json').read_text(encoding='utf-8'))
    for folder, metadata in sorted(lock['packages'].items()):
        if not folder or metadata.get('dev'):
            continue
        package = source / folder
        if not package.is_dir():
            continue
        notices.append('\n--- ' + folder + ' ' + metadata.get('version', '') + ' (' + str(metadata.get('license', 'see package')) + ') ---')
        for file in sorted(package.iterdir()):
            if file.is_file() and file.name.lower().startswith(('license', 'licence', 'notice', 'copying')):
                notices.append(file.name + '\n' + file.read_text(encoding='utf-8', errors='replace'))
    (output / 'THIRD-PARTY-NOTICES.txt').write_text('\n'.join(notices) + '\n', encoding='utf-8')
    print('Map viewer built:', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--map-data', type=Path, help='Imported pack to enrich with authored DTS mounts')
    args = parser.parse_args()
    build(args.source.resolve(), args.map_data.resolve() if args.map_data else None)

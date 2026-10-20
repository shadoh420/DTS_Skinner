"""Import Spellbreak (Community Version) models from the client pak, LOD0 reference poses.

python -m tools.import_sb --install C:\\Games\\Spellbreak\\client --output build/sb-data/sb
Repeat --model '/Game/Path/Package.Object' (quoted wildcards accepted) for a small sample.
The pak is read in place; nothing in the install is written or executed.
"""
import argparse
from collections import Counter
import fnmatch
import json
from pathlib import Path
import re
import time

from tools.import_tas import key
from tools.local_data import LOCAL_DATA
from tools.model_data import write_model_data
from tools.sb_materials import Materials
from tools.texture_storage import game_texture_dir
from tools.texture_workshop import write_json_atomic
from tools.ue4_mesh import mesh
from tools.ue4_package import Library

GAME = 'sb'
IMPOSTOR = re.compile(r'impost[eo]r', re.I)


def category(source):
    """Content folders, two deep ('Environment / Temperate'); characters by name ('Characters / Human / Sam')."""
    parts = source.split('.', 1)[0].split('/')[1:-1]
    if parts and parts[0] == 'Game':
        parts = parts[1:]
    else:
        parts = ['Plugins']+parts
    depth = 3 if parts[:2] == ['Characters', 'Human'] else 2
    return ' / '.join(p.replace('_', ' ').strip() for p in parts[:depth]) or 'Other'


def exclusion(source):
    name = source.rsplit('.', 1)[-1].lower()
    if source.lower().startswith(('/engine/', '/game/editor/', '/game/developers/')):
        return 'Engine/editor content'
    if '/hlod/' in source.lower():
        return 'Hierarchical LOD proxy (merged distant copy of map meshes)'
    if name.startswith(('ucx_', 'ubx_', 'usp_', 'ucp_')):
        return 'Collision-only mesh'
    return None


def import_catalog(install, output, model_paths=None):
    install, output = Path(install).resolve(), Path(output).resolve()
    if output == install or install in output.parents:
        raise ValueError('Output must be outside the read-only install')
    if output.name != GAME:
        raise ValueError(f'Output must be a game folder named {GAME}, e.g. build/{GAME}-data/{GAME}')
    started = time.perf_counter(); lib = Library(install)
    try:
        objects = sorted((rows for rows in lib.rows.values()), key=lambda rows: (rows[0]['source'].lower(), rows[0]['cls']))
        total = len(objects)
        if model_paths is not None:
            for pattern in model_paths:
                if not any(fnmatch.fnmatchcase(rows[0]['source'].lower(), pattern.lower()) for rows in objects):
                    raise ValueError('Unknown model selector: '+pattern)
            objects = [rows for rows in objects if any(fnmatch.fnmatchcase(rows[0]['source'].lower(), p.lower()) for p in model_paths)]
        mats = Materials(lib, game_texture_dir(output.parent, GAME))
        records, catalog, index = [], [], {}
        for rows in objects:
            obj = rows[0]; source, cls = obj['source'], obj['cls']
            row = dict(obj, category=category(source)); records.append(row)
            entry = index.setdefault(source.lower(), dict(source=source, by_class={}))['by_class']
            try:
                reason = exclusion(source)
                if reason:
                    raise ValueError(reason)
                data = mesh(lib.package(obj['file']), obj['ref'])
                if not data['groups']:
                    raise ValueError('Mesh has no drawable sections')
                materials = [mats.material(ref) for ref in data.pop('material_refs')]
                # Trees carry a far-distance impostor card in LOD0 that the game fades in only far away.
                kept = [g for g in data['groups'] if not IMPOSTOR.search(materials[g['materialIndex']].get('source') or '')
                        and not IMPOSTOR.search(materials[g['materialIndex']].get('base_material') or '')]
                if kept and len(kept) < len(data['groups']):
                    data['metadata']['dropped_sections'] = 'impostor billboard card'
                    data['groups'] = kept
                data['metadata'].update(source=source, source_class=cls, package=obj['file'], materials=materials, skin='Default')
                ident = key(cls+':'+source)
                payload = dict(data, game=GAME, material_textures=[m['texture'] for m in materials],
                               material_settings=[m['settings'] for m in materials])
                path = output/'model_json'/(ident+'.json'); write_model_data(path, payload)
                buffers = json.loads(path.read_text(encoding='utf-8'))['geometry_buffers']
                unresolved = sum(m['status'] != 'ready' for m in materials)
                catalog.append(dict(game=GAME, model_name=ident, display_name=source.rsplit('/', 1)[-1].split('.', 1)[0],
                                    source=source, category=row['category'], status='ready',
                                    texture_name=materials[data['groups'][0]['materialIndex']]['texture'],
                                    warnings=[f'{unresolved} unresolved material slots'] if unresolved else []))
                row.update(status='ready', looks=[ident], vertices=len(data['vertices'])//3, triangles=len(data['indices'])//3)
                entry[cls] = dict(status='ready', looks=[ident], variants=[dict(id=ident, label='Default', assignment='mesh material slots')],
                                  geometry=buffers, selected=dict(file=obj['file'], export=obj['ref']),
                                  model_json='model_json/'+ident+'.json', lod=0, coordinate_system=data['metadata']['coordinate_system'])
            except (ValueError, OSError) as exc:
                row.update(status='skipped', reason=str(exc)); entry[cls] = dict(status='skipped', reason=str(exc))
        for entry in index.values():
            if len(entry['by_class']) == 1:
                entry.update(next(iter(entry['by_class'].values())))
        report = dict(game=GAME, sample=model_paths is not None, total_candidates=total, objects=len(records),
                      ready=sum(r['status'] == 'ready' for r in records), entries=len(catalog),
                      skipped=sum(r['status'] == 'skipped' for r in records),
                      skipped_reasons=dict(Counter(r['reason'] for r in records if r['status'] == 'skipped')),
                      textures=len(mats.textures), unresolved_materials=sum(m['status'] != 'ready' for m in mats.materials.values()),
                      unresolved_textures=sum(t['status'] != 'ready' for t in mats.textures.values()),
                      results=records, material_results=list(mats.materials.values()), texture_results=list(mats.textures.values()),
                      material_failures=mats.failures, inventory=lib.inventory, package_failures=lib.failures,
                      elapsed_seconds=time.perf_counter()-started,
                      rules=dict(pose='LOD0 static/reference; no animation',
                                 storage='referenced base-colour textures only; DXT kept as DDS, other formats PNG (<=2048)',
                                 materials='first base-colour texture parameter on the MIC chain, else the base material\'s own texture'))
        report['issues'] = report['skipped']+report['unresolved_materials']+len(lib.failures)
        report['report'] = str(output/'import-report.json')
        write_json_atomic(output/'catalog.json', catalog); write_json_atomic(output/'model-index.json', index)
        write_json_atomic(output/'import-report.json', report)
        return report
    finally:
        lib.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA/GAME)
    parser.add_argument('--model', action='append', help='Object path or wildcard; repeat for samples')
    args = parser.parse_args()
    result = import_catalog(args.install, args.output, args.model)
    print(json.dumps({k: v for k, v in result.items() if k not in ('results', 'material_results', 'texture_results', 'inventory')}, indent=2))

"""Stage UV-only repairs of content-named map interiors; activate with the app stopped.

Reuse the catalog repair's exact DIG/legacy-UV matcher and current exporter UV
calculation. Scene references change only after all replacements are validated.
Old content-named models remain available for rollback; no terrain is reimported.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

from PIL import Image

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.import_t1_map import interiorshape, map_id
from tools.model_data import load_model_data
from tools.repair_t1_interior_uvs import corrected_uvs, source_resources
from tools.texture_workshop import write_json_atomic


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes cache: {relative}')
    return path


def prepare(cache, stage, sources):
    cache, stage = Path(cache).resolve(), Path(stage).resolve()
    if stage == cache or stage.is_relative_to(cache):
        raise ValueError('Staging must be outside the live map cache')
    resources = source_resources(sources)
    dis_names = defaultdict(list)
    for name in resources:
        if name.endswith('.dis'):
            dis_names[map_id(re.sub(r'\.\d+$', '', Path(name).stem))].append(name)
    references, scenes, catalog_refs = defaultdict(list), {}, defaultdict(set)
    for path in sorted((cache / 'maps').glob('*/scene.json')):
        scene = json.loads(path.read_text(encoding='utf-8'))
        scenes[path.parent.name] = (path, scene)
        for i, obj in enumerate(scene['objects']):
            if obj.get('model'):
                if obj.get('source') == 'pack':
                    references[obj['model']].append((path.parent.name, i))
                else:
                    catalog_refs[obj['model']].add(path.parent.name)
    report = dict(cache=str(cache), models=[], maps=[], catalog_references={k: sorted(v) for k, v in sorted(catalog_refs.items())})
    replacements = {}
    for path in sorted((cache / 'models').glob('*.json')):
        model = json.loads(path.read_text(encoding='utf-8'))
        refs = references.get(path.name, [])
        row = dict(model=path.name, maps=sorted({name for name, _ in refs}))
        report['models'].append(row)
        if model.get('interior_uv_version') == 2:
            row['status'] = 'already_current'
            continue
        stem = path.stem.rsplit('-', 1)[0]
        candidates = set()
        for name in dis_names.get(stem, []):
            for _, read in resources[name]:
                shape = interiorshape.interiorshape()
                shape.load_binary(read())
                if shape.lods:
                    lod = max(shape.lods, key=lambda item: item.min_pixels)
                    candidates.add(shape.name_buffer[lod.geometry_file_offset:].split(b'\0')[0].decode('cp1252').lower())
        interior_ref = any(scenes[name][1]['objects'][i].get('classname') == 'interiorshape'
                           or any(key in scenes[name][1]['objects'][i] for key in ('shelter', 'light')) for name, i in refs)
        if not candidates:
            row.update(status='unresolved' if interior_ref else 'non_interior_or_unreferenced', reason='No source DIS/DIG candidate')
            continue
        sizes = []
        for texture in model['material_textures']:
            if not texture or texture.startswith('['):
                sizes.append((256, 256))
            else:
                with Image.open(inside(cache / 'textures', texture)) as image:
                    sizes.append(image.size)
        matches = []
        for name in sorted(candidates):
            for source, read in resources[name]:
                dig = interiorshape.dig()
                dig.load_binary(read())
                fixed = corrected_uvs(model, dig, sizes)
                if fixed is not None:
                    matches.append((fixed, source, name, dig.highest_mip))
        if not matches or any(item[0] != matches[0][0] for item in matches[1:]):
            row.update(status='unresolved', reason='No unique source geometry/legacy-UV match', candidates=sorted(candidates))
            continue
        fixed, source, member, mip = matches[0]
        row.update(source=source, member=member, highest_mip=mip)
        if not refs:
            row['status'] = 'legacy_unreferenced'
            continue
        model.update(uvs=fixed, interior_uv_version=2)
        data = json.dumps(model).encode()
        new_name = f'{stem}-{hashlib.sha1(data).hexdigest()[:12]}.json'
        destination = inside(stage / 'models', new_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        # Map packs use '' for an already missing texture; catalog validation
        # uses a bracketed slot. Adapt only the validation view, never the file.
        load_model_data(destination, material_overrides={str(i): f'[Slot {i}]'
                        for i, texture in enumerate(model['material_textures']) if not texture})
        row.update(status='refreshed', replacement=new_name)
        replacements[path.name] = new_name
    missing = sorted(set(references) - {row['model'] for row in report['models']})
    report['missing_models'] = missing
    for name, (path, scene) in scenes.items():
        changed = 0
        for obj in scene['objects']:
            if obj.get('source') == 'pack' and obj.get('model') in replacements:
                obj['model'] = replacements[obj['model']]
                changed += 1
        row = dict(map=name, replacements=changed, status='refreshed' if changed else 'unchanged')
        report['maps'].append(row)
        if changed:
            backup = inside(stage / 'before', f'{name}/scene.json')
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(path.read_bytes())
            write_json_atomic(inside(stage / 'after', f'{name}/scene.json'), scene, indent=None)
    write_json_atomic(stage / 'inventory.json', report)
    return report


def activate(cache, stage, rollback=False):
    """Caller stops the app first. Preflight every scene, then publish or restore."""
    cache, stage = Path(cache).resolve(), Path(stage).resolve()
    report = json.loads((stage / 'inventory.json').read_text(encoding='utf-8'))
    if Path(report['cache']).resolve() != cache:
        raise ValueError('Inventory belongs to another cache')
    if report['missing_models'] or any(row['status'] == 'unresolved' and row['maps'] for row in report['models']):
        raise ValueError('Resolve referenced models before activation')
    before, after = ('after', 'before') if rollback else ('before', 'after')
    pending = []
    for row in report['maps']:
        if row['status'] != 'refreshed':
            continue
        relative = f"{row['map']}/scene.json"
        target = inside(cache / 'maps', relative)
        original = inside(stage / before, relative).read_bytes()
        if target.read_bytes() != original:
            raise ValueError(f'Changed since staging; refusing to overwrite {target}')
        pending.append((target, original, inside(stage / after, relative).read_bytes()))
    if not rollback:
        for row in report['models']:
            if row['status'] != 'refreshed':
                continue
            source = inside(stage / 'models', row['replacement'])
            data = source.read_bytes()
            if hashlib.sha1(data).hexdigest()[:12] != source.stem.rsplit('-', 1)[1]:
                raise ValueError(f'Staged model content key mismatch: {source}')
            target = inside(cache / 'models', source.name)
            if target.exists() and target.read_bytes() != data:
                raise ValueError(f'Content key collision: {target}')
        for row in report['models']:
            if row['status'] == 'refreshed':
                name = row['replacement']
                inside(cache / 'models', name).write_bytes(inside(stage / 'models', name).read_bytes())
    written = []
    try:
        for target, original, data in pending:
            # Keep the original bytes for rollback, including formatting/user metadata.
            temporary = target.with_suffix('.uv-refresh.tmp')
            temporary.write_bytes(data)
            temporary.replace(target)
            written.append((target, original))
    except BaseException:
        for target, original in reversed(written):
            temporary = target.with_suffix('.uv-refresh.tmp')
            temporary.write_bytes(original)
            temporary.replace(target)
        raise
    return len(pending)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--source', action='append', default=[])
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--activate', action='store_true')
    action.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if args.activate or args.rollback:
        print(f'{activate(args.cache, args.stage, args.rollback)} scene references updated; rollback={args.rollback}')
    else:
        from collections import Counter
        result = prepare(args.cache, args.stage, args.source)
        print(dict(Counter(row['status'] for row in result['models'])))
        print(dict(Counter(row['status'] for row in result['maps'])))

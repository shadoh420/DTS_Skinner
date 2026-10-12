"""Import Bloodlines models, skin families and their textures from a local install.

python -m tools.import_vtmb --install "C:/GOG Games/VtMB"
Unofficial_Patch loose content overlays stock. The install is only read.
Bodygroups default to zero; hands/viewmodels are included. Existing PNG edits survive.
Every effective MDL is ready, duplicate or skipped with a reason in import-report.json.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from PIL import Image

try:
    from tools.vtmb import asset_name, asset_reference, read_install, read_mdl, mdl_companions, material_texture, read_vmt, material_settings
    from tools.local_data import LOCAL_DATA
    from tools.texture_workshop import write_json_atomic
    from tools.model_data import write_model_data
    from tools.texture_storage import write_texture
except ImportError:
    from vtmb import asset_name, asset_reference, read_install, read_mdl, mdl_companions, material_texture, read_vmt, material_settings
    from local_data import LOCAL_DATA
    from texture_workshop import write_json_atomic
    from model_data import write_model_data
    from texture_storage import write_texture


def key(name):
    # Hash the virtual path as well as slugging it: a/b and a_b cannot collide.
    return re.sub(r'[^a-z0-9_.-]', '_', name.lower())[:140] + '_' + hashlib.sha256(name.encode()).hexdigest()[:12]


def category(name):
    parts = name.split('/')
    return parts[1].title() if len(parts) > 2 else 'Other'


def texture_key(vmt, path):
    base = vmt['params'].get('$basetexture')
    if isinstance(base, str) and base:
        return re.sub(r'\.(vtf|tth|ttz)$', '', asset_reference(base).removeprefix('materials/'))
    return path


def prepare_texture(files, vmt, path):
    payload, info = material_texture(files, vmt, compressed=True)
    return key(texture_key(vmt, path)) + ('.dds' if isinstance(payload, bytes) else '.png'), payload, info


def resolve_material(files, name, directories, stack=()):
    name = asset_reference(name).removesuffix('.vmt')
    candidates = [asset_reference('materials/' + d.replace('\\', '/').strip('/') + '/' + name)
                  if d.strip('/\\') else asset_name('materials/' + name) for d in directories]
    candidates.append(asset_name(name if name.startswith('materials/') else 'materials/' + name))
    path = next((p + '.vmt' for p in candidates if p + '.vmt' in files), None)
    # Bloodlines ships this explicit shared NULL material. Honor any local
    # definition first; never infer invisibility from the slot's name alone.
    if path is None and name == 'null' and 'materials/models/null.vmt' in files:
        path = 'materials/models/null.vmt'
    if path is None:
        raise ValueError('Missing VMT: ' + ', '.join(p + '.vmt' for p in candidates))
    if path in stack or len(stack) >= 16:
        raise ValueError('Cyclic/deep VMT patch include: ' + path)
    vmt = read_vmt(files[path].read())
    if vmt['shader'].lower() == 'patch':
        params = vmt['params']
        include = params.get('include')
        if not isinstance(include, str): raise ValueError('VMT patch has no include')
        base, _ = resolve_material(files, include, [], (*stack, path))
        vmt = dict(shader=base['shader'], params=dict(base['params']))
        for operation in ('insert', 'replace'):
            changes = params.get(operation, {})
            if not isinstance(changes, dict): raise ValueError(f'VMT patch {operation} must be a block')
            for k, v in changes.items():
                if operation == 'insert' and k not in vmt['params'] or operation == 'replace' and k in vmt['params']:
                    vmt['params'][k] = v
    return vmt, path


def import_catalog(install, output):
    output = Path(output).resolve()
    install_root = Path(install).resolve()
    if install_root.name.lower() in ('vampire', 'unofficial_patch'):
        install_root = install_root.parent
    if output == install_root or install_root in output.parents:
        raise ValueError('Import output must be outside the read-only game install')
    files, inventory = read_install(install)
    for sub in ('textures', 'model_json'):
        (output / sub).mkdir(parents=True, exist_ok=True)
    missing = output / 'textures' / 'missing_vtmb.png'
    if not missing.exists():
        image = Image.new('RGBA', (2, 2)); image.putdata([(255, 0, 255, 255), (32, 32, 32, 255)] * 2); image.save(missing)
    records, catalog, seen, materials, textures = [], [], {}, {}, {}

    def material(name, directories, used):
        cache = (name, tuple(directories))
        if cache in materials:
            materials[cache]['used_by_exported_geometry'] |= used
            return materials[cache]
        result = dict(name=name, search_directories=directories, texture=missing.name, settings={}, status='unresolved',
                      used_by_exported_geometry=used)
        materials[cache] = result
        try:
            vmt, path = resolve_material(files, name, directories)
            settings, notes = material_settings(vmt)
            result.update(source=path, origin=files[path].origin, settings=settings, notes=notes)
            base = vmt['params'].get('$basetexture')
            base = asset_reference(base) if isinstance(base, str) and base else None
            result['basetexture'] = base
            # Generated shader previews are keyed by VMT, real images by texture.
            base = texture_key(vmt, path)
            if base not in textures:
                record = dict(source=base, status='unresolved')
                textures[base] = record
                try:
                    png, image, info = prepare_texture(files, vmt, path)
                    write_texture(output / 'textures', png, image)
                    record.update(status='ready', texture=png, **info)
                except (ValueError, OSError) as exc:
                    record['reason'] = str(exc)
            tex = textures[base]
            if tex['status'] != 'ready': raise ValueError(tex['reason'])
            result.update(status='ready', texture=tex['texture'])
        except (ValueError, OSError) as exc:
            result['reason'] = str(exc)
        return result

    models = sorted(n for n in files if n.startswith('models/') and n.endswith('.mdl'))
    for name in models:
        record = dict(source=name, origin=files[name].origin, category=category(name), status='pending')
        records.append(record)
        model_key = key(name[:-4])
        item = dict(model_name=model_key, display_name=name, game='vtmb', category=category(name),
                    source=files[name].origin + ':' + name, texture_name=missing.name)
        try:
            raw = files[name].read()
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                record.update(status='duplicate', model=seen[digest], reason='Byte-identical MDL; retained once')
                continue
            companions = mdl_companions(raw, name, files)
            if not companions: raise ValueError('Missing VTX companion (vertices are embedded; no VVD needed for MDL 2531)')
            mesh, failures = None, []
            for companion in companions:
                try:
                    vtx = files[companion].read()
                    mesh = read_mdl(raw, vtx)
                    break
                except (ValueError, OSError) as exc:
                    failures.append(dict(source=companion, reason=str(exc)))
            record['companion_failures'] = failures
            if mesh is None: raise ValueError('No readable VTX: ' + '; '.join(f['reason'] for f in failures))
            dirs = mesh['metadata']['material_directories']
            families = mesh.pop('skin_table')
            skin_refs = mesh.pop('skin_refs')
            used = {family[ref] for family in families for ref in skin_refs}
            slots = [dict(material(n, dirs, i in used), used_by_exported_geometry=i in used)
                     for i, n in enumerate(mesh['material_names'])]
            record.update(status='ready', model=model_key, skins=len(families), vtx=companion,
                          pose=mesh['metadata']['pose'], looks=[], unresolved_materials=sum(r['status'] != 'ready' for r in slots),
                          unresolved_used_materials=sum(slots[i]['status'] != 'ready' for i in used))
            mesh['material_textures'] = [r['texture'] for r in slots]
            mesh['material_settings'] = [r['settings'] for r in slots]
            mesh['metadata'].update(source=item['source'], vtx=companion,
                                    materials=slots, animation='static shown pose; no clips')
            # Geometry/pose does not depend on skin. Retain the original column,
            # since family 0 can repeat a texture index in several columns.
            for skin, family in enumerate(families):
                look = model_key + (f'_skin{skin}' if len(families) > 1 else '')
                groups = [dict(g, materialIndex=family[ref]) for g, ref in zip(mesh['groups'], skin_refs)]
                data = dict(mesh, groups=groups, metadata=dict(mesh['metadata'], skin_family=skin))
                write_model_data(output / 'model_json' / (look + '.json'), data)
                record['looks'].append(look)
                catalog.append(dict(item, model_name=look, display_name=name + (f' [skin {skin}]' if len(families) > 1 else ''),
                                    texture_name=slots[groups[0]['materialIndex']]['texture'], status='ready'))
            record['model'] = seen[digest] = record['looks'][0]
        except (ValueError, OSError) as exc:
            record.update(status='skipped', reason=str(exc))
            catalog.append(dict(item, status='skipped', reason=str(exc)))
    report = dict(game='vtmb', entries=len(catalog), ready=sum(c['status'] == 'ready' for c in catalog),
                  models=len(models), categories=dict(Counter(category(n) for n in models)),
                  duplicates=sum(r['status'] == 'duplicate' for r in records),
                  skipped=sum(r['status'] == 'skipped' for r in records),
                  skins=sum(r.get('skins', 0) for r in records),
                  textures=sum(r['status'] == 'ready' for r in textures.values()),
                  unresolved_materials=sum(r['status'] != 'ready' for r in materials.values()),
                  unresolved_used_materials=sum(r['status'] != 'ready' and r['used_by_exported_geometry'] for r in materials.values()),
                  unresolved_unused_materials=sum(r['status'] != 'ready' and not r['used_by_exported_geometry'] for r in materials.values()),
                  unresolved_textures=sum(r['status'] != 'ready' for r in textures.values()),
                  inventory=inventory, results=records, material_results=list(materials.values()), texture_results=list(textures.values()),
                  scope='All effective models; all their material slots. Unreferenced world materials/textures indexed only.',
                  rules=dict(category='first directory below models/, root files Other', hands='included, including first-person viewmodels',
                             bodygroups='value zero (first model) for each bodypart', pose='first local idle, then reference, else bind'))
    write_json_atomic(output / 'catalog.json', catalog)
    write_json_atomic(output / 'import-report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA / 'vtmb')
    args = parser.parse_args()
    print(json.dumps(import_catalog(args.install, args.output), indent=2))

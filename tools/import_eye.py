"""Import E.Y.E. models into a shared texture library and indexed LOD0 buffers.

python -m tools.import_eye --install "C:/Program Files (x86)/Steam/steamapps/common/EYE"
Use repeated --model virtual paths for a bounded sample. No install writes.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

if not __package__:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image
from tools import source_assets, source_mdl
from tools.import_vtmb import key, category
from tools.local_data import LOCAL_DATA
from tools.model_data import write_model_data
from tools.texture_storage import game_texture_dir, write_texture
from tools.texture_workshop import write_json_atomic


def drop_meshes(mesh, refs, drop):
    """Remove whole meshes: their triangles and their own vertex range. vvd_first still maps kept meshes to VHV light.

    Used for meshes textured from models/crosshairs: the weapons' scope and crosshair overlays, which hang far in front
    of the gun and show only when aiming, and the additive black `inv` helpers. Left in, they set the viewer's framing
    and the gun becomes a dot."""
    keep = np.ones(len(mesh['vertices'])//3, bool)
    for k in drop:
        g = mesh['groups'][k]; keep[g['vertex_start']:g['vertex_start']+g['vertex_count']] = False
    if not keep.any():
        raise ValueError('Only scope/crosshair overlay meshes')
    remap, source = np.cumsum(keep)-1, np.asarray(mesh['indices'])
    groups, indices = [], []
    for k, g in enumerate(mesh['groups']):
        if k in drop:
            continue
        part = source[g['start']:g['start']+g['count']]
        if not keep[part].all():
            raise ValueError('Overlay mesh shares vertices with a kept mesh')
        groups.append(dict(g, start=len(indices), vertex_start=int(remap[g['vertex_start']])))
        indices.extend(remap[part].tolist())
    for name, width in (('vertices', 3), ('normals', 3), ('uvs', 2)):
        mesh[name] = np.asarray(mesh[name]).reshape(-1, width)[keep].ravel().tolist()
    mesh['groups'], mesh['indices'] = groups, indices
    return [ref for k, ref in enumerate(refs) if k not in drop]


def import_catalog(install, output, model_paths=None):
    output = Path(output).resolve(); game = source_assets.game_directory(install)
    if output == game.parent or game.parent in output.parents:
        raise ValueError('Import output must be outside the read-only game install')
    files, inventory = source_assets.read_install(install)
    texture_dir = game_texture_dir(output.parent, output.name)
    missing = 'missing_eye.png'
    image = Image.new('RGBA', (2, 2)); image.putdata([(255, 0, 255, 255), (32, 32, 32, 255)]*2)
    write_texture(texture_dir, missing, image)
    models = sorted(n for n in files if n.endswith('.mdl'))
    if model_paths is not None:
        selected = {source_assets.asset_reference(n) for n in model_paths}
        unknown = selected-set(models)
        if unknown:
            raise ValueError('Unknown sample model paths: '+', '.join(sorted(unknown)))
        models = [n for n in models if n in selected]
    records, catalog, index, seen, materials, textures = [], [], {}, {}, {}, {}

    def material(name, directories):
        cache = (name, tuple(directories))
        if cache in materials:
            return materials[cache]
        row = dict(name=name, search_directories=directories, texture=missing,
                   settings={}, status='unresolved', used_by_exported_geometry=False)
        materials[cache] = row
        try:
            vmt, path = source_assets.resolve_material(files, name, directories)
            settings, notes = source_assets.material_settings(vmt)
            if '$phong' in vmt['params']:
                notes.append('$phong recorded; diffuse preview only')
            if '$hdrcompressedtexture' in vmt['params']:
                notes.append('HDR-compressed sky path deferred to maps; model uses $basetexture')
            row.update(source=path, settings=settings, notes=notes)
            base = source_assets.texture_key(vmt, path)
            if base not in textures:
                tex = dict(source=base, status='unresolved'); textures[base] = tex
                try:
                    filename, payload, info = source_assets.prepare_texture(files, vmt, path)
                    write_texture(texture_dir, filename, payload)
                    tex.update(info, texture=filename, status='ready')
                except (ValueError, OSError) as exc:
                    tex['reason'] = str(exc)
            tex = textures[base]
            if tex['status'] != 'ready':
                raise ValueError(tex['reason'])
            row.update(status='ready', texture=tex['texture'])
        except (ValueError, OSError) as exc:
            row['reason'] = str(exc)
        return row

    # Normal model paths win over misplaced material-folder copies.
    models.sort(key=lambda n: (not n.startswith('models/'), n))
    for name in models:
        ident = key(name[:-4])
        row = dict(source=name, category=category(name), status='pending'); records.append(row)
        item = dict(model_name=ident, display_name=name, game='eye', category=category(name),
                    source=name, texture_name=missing)
        try:
            raw = files[name].read(); digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                row.update(status='duplicate', model=seen[digest], reason='Byte-identical MDL; canonical model retained')
                index[name] = dict(index[seen[digest]], alias_of=seen[digest])
                continue
            mesh = source_mdl.load_model(files, name)
            families, refs = mesh.pop('skin_table'), mesh.pop('skin_refs')
            slots = [material(n, mesh['metadata']['material_directories']) for n in mesh['material_names']]
            overlays = [k for k, ref in enumerate(refs) if slots[families[0][ref]]['settings'].get('parameters', {})
                        .get('$basetexture', '').lower().replace('\\', '/').startswith('models/crosshairs/')]
            if overlays:
                refs = drop_meshes(mesh, refs, overlays)
                mesh['metadata']['dropped_overlay_meshes'] = len(overlays)
            used = {family[ref] for family in families for ref in refs}
            for i in used:
                slots[i]['used_by_exported_geometry'] = True
            mesh['material_textures'] = [s['texture'] for s in slots]
            mesh['material_settings'] = [s['settings'] for s in slots]
            mesh['metadata'].update(materials=slots, animation='static pose, no clips')
            looks = []
            for skin, family in enumerate(families):
                look = ident+(f'_skin{skin}' if len(families) > 1 else '')
                groups = [dict(g, materialIndex=family[ref]) for g, ref in zip(mesh['groups'], refs)]
                data = dict(mesh, groups=groups, metadata=dict(mesh['metadata'], skin_family=skin))
                path = output/'model_json'/(look+'.json'); write_model_data(path, data)
                if not looks:
                    buffers = json.loads(path.read_text(encoding='utf-8'))['geometry_buffers']
                looks.append(look)
                catalog.append(dict(item, model_name=look, status='ready',
                                    display_name=name+(f' [skin {skin}]' if len(families)>1 else ''),
                                    texture_name=slots[groups[0]['materialIndex']]['texture']))
            row.update(status='ready', looks=looks, skins=len(looks), pose=mesh['metadata']['pose'],
                       version=mesh['metadata']['version'], vtx=mesh['metadata']['vtx'],
                       companion_failures=mesh['metadata']['companion_failures'],
                       unresolved_used_materials=sum(slots[i]['status']!='ready' for i in used))
            index[name] = dict(looks=looks, lod=0, bodygroups='default', geometry=buffers,
                               model_json='model_json/'+looks[0]+'.json',
                               coordinate_system=mesh['metadata']['coordinate_system'])
            seen[digest] = name
        except (ValueError, OSError) as exc:
            row.update(status='skipped', reason=str(exc))
            catalog.append(dict(item, status='skipped', reason=str(exc)))
            index[name] = dict(status='skipped', reason=str(exc))
    report = dict(game='eye', sample=model_paths is not None, models=len(models), entries=len(catalog),
                  ready=sum(c['status']=='ready' for c in catalog),
                  skipped=sum(r['status']=='skipped' for r in records),
                  duplicates=sum(r['status']=='duplicate' for r in records),
                  skipped_reasons=dict(Counter(r['reason'] for r in records if r['status']=='skipped')),
                  categories=dict(Counter(r['category'] for r in records)),
                  unresolved_materials=sum(r['status']!='ready' for r in materials.values()),
                  unresolved_used_materials=sum(r['status']!='ready' and r['used_by_exported_geometry'] for r in materials.values()),
                  unresolved_textures=sum(r['status']!='ready' for r in textures.values()),
                  textures=len(textures), inventory=inventory, results=records,
                  material_results=list(materials.values()), texture_results=list(textures.values()),
                  deferred_textures=[n for n in files if n.endswith('.vtf') and ('.hdr.' in n or n.startswith('materials/skybox/'))],
                  rules=dict(category='first folder under models; root models Other',
                             bodygroups='first submodel in each bodypart; LOD0',
                             pose='first supported local idle/reference frame, otherwise audited VVD bind pose',
                             actors='all NPC/player models and first-person weapons included',
                             storage='one indexed float32 geometry per model, shared by skin families; shared game textures'))
    report['issues'] = report['skipped']+report['unresolved_materials']
    report['report'] = str(output/'import-report.json')
    write_json_atomic(output/'catalog.json', catalog)
    write_json_atomic(output/'model-index.json', index)
    write_json_atomic(output/'import-report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=LOCAL_DATA/'eye')
    parser.add_argument('--model', action='append', help='Virtual model path; repeat for a bounded sample')
    args = parser.parse_args()
    report = import_catalog(args.install, args.output, args.model)
    print(json.dumps({k:v for k,v in report.items() if k not in ('results','material_results','texture_results','deferred_textures')}, indent=2))

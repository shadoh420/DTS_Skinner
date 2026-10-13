"""Import Ascend PC models, LOD0 reference poses, without executing the game.

python -m tools.import_tas --install PATH --output build/tas-data/tas
Repeat --model Package.Object (quoted wildcards accepted) for a bounded sample.
"""
import argparse
from collections import Counter
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import time

from tools.local_data import LOCAL_DATA
from tools.model_data import write_model_data
from tools.texture_storage import game_texture_dir
from tools.texture_workshop import write_json_atomic
from tools.tas_package import Library
from tools.tas_mesh import mesh
from tools.tas_materials import Materials


def key(source):
    return re.sub('[^a-z0-9_-]+', '_', source.lower()).strip('_')[:120]+'_'+hashlib.sha256(source.lower().encode()).hexdigest()[:10]


def category(row):
    name = row['source'].lower()
    root = name.split('.')[0]
    if root.startswith('pc_'):
        kind = 'Armour / '+('Heavy' if any(s in name for s in ('heavy','brute','jugger','doombringer')) else
                           'Light' if any(s in name for s in ('light','pathfinder','infiltrator','sentinel')) else 'Medium')
        tokens = set(re.split('[_.]',name))
        roles = [('brute',{'brt','brute'}),('doombringer',{'dmb','dbr','doombringer'}),('juggernaut',{'jgr','jug','juggernaut'}),
                 ('pathfinder',{'pth','path','pathfinder'}),('infiltrator',{'inf','infiltrator'}),('sentinel',{'sen','snt','sentinel'}),
                 ('soldier',{'sld','soldier'}),('raider',{'rdr','raider'}),('technician',{'tcn','tec','technician'})]
        role = next((label.title() for label,codes in roles if tokens & codes),None)
        if role:
            kind += ' / '+role
        elif 'heavy' in tokens:
            kind += ' / Juggernaut (base armour)'
    elif root.startswith('wep_'):
        kind = 'Weapons / '+('First person' if '_1p' in name else 'Third person')
    elif root.startswith('veh_'):
        kind = 'Vehicles'
    elif any(s in root for s in ('generator','station','turret','sensor')) or root.startswith(('gen_','stn_','dev_')):
        kind = 'Base objects'
    elif root.startswith(('dep_','pak_')):
        kind = 'Deployables and packs'
    elif 'flag' in name:
        kind = 'Flags'
    elif 'sky' in name:
        kind = 'Sky'
    else:
        kind = 'Environment props'
    return row['section']+' / '+kind


def exclusion(row):
    name = row['source'].lower()
    if 'default__' in name:
        return 'Class default object, not an asset'
    if name.startswith(('editor.', 'unrealed.','entry.')) or any(s in name.split('.')[-1] for s in ('collision','ucx_','ubx_')):
        return 'Editor/collision-only mesh'
    if row['cls'] == 'SpeedTree':
        return 'SpeedTree procedural native blob: branch/leaf reconstruction unsupported'
    return None


def skin_families(source, refs, mats, by_root, by_preview):
    """Compatible MICs replace one authored slot; never apply a colour tint.

    Parent equality alone is insufficient across unrelated assets. Restrict sibling
    MICs to the same asset namespace, or an explicit PreviewMesh assignment.
    """
    result = [dict(label='Default', refs=refs, assignment='mesh material slots')]
    seen = {tuple(refs)}
    for slot, material in enumerate(refs):
        if not material:
            continue
        try:
            definition = mats.definition(material)
        except (ValueError, OSError):
            continue
        candidates = set(by_preview.get(source.lower(), []))
        if source.lower().startswith(('pc_', 'wep_', 'veh_', 'dep_', 'pak_')):
            candidates.update(c for c in by_root.get(definition['root'].lower(), [])
                              if c.split('.')[0].lower() == material.split('.')[0].lower())
        for candidate in sorted(candidates, key=str.lower):
            if mats.definition(candidate)['root'].lower() != definition['root'].lower():
                continue
            values = list(refs); values[slot] = candidate
            if tuple(values) not in seen:
                seen.add(tuple(values))
                result.append(dict(label=candidate.split('.')[-1], refs=values,
                                   assignment=dict(slot=slot, material=candidate, evidence='MIC parent and asset namespace/PreviewMesh')))
    return result


def provenance(lib, candidates):
    groups = {}
    for candidate in candidates:
        p = lib.package(candidate['file']); entry = p.entry(candidate['ref'])
        digest = hashlib.sha256(p.read(entry['offset'], entry['size'])).hexdigest()
        groups.setdefault(digest, []).append(dict(file=candidate['file'], export=candidate['ref'], section=candidate['section']))
    return [dict(sha256=digest, copies=copies) for digest,copies in groups.items()]


def import_catalog(install, output, model_paths=None, *, game='tas', library_factory=Library,
                   category_fn=category, skins_fn=skin_families, materials_factory=Materials):
    install, output = Path(install).resolve(), Path(output).resolve()
    if output == install or install in output.parents:
        raise ValueError('Output must be outside the read-only install')
    if output.name != game:
        raise ValueError(f'Output must be a game folder named {game}, e.g. build/{game}-data/{game}')
    started = time.perf_counter(); lib = library_factory(install)
    try:
        objects = sorted((rows for (_,cls),rows in lib.rows.items() if cls in ('StaticMesh','SkeletalMesh','SpeedTree')),
                         key=lambda rows:(rows[0]['source'].lower(), rows[0]['cls']))
        total = len(objects)
        if model_paths is not None:
            for pattern in model_paths:
                if not any(fnmatch.fnmatchcase(rows[0]['source'].lower(), pattern.lower()) for rows in objects):
                    raise ValueError('Unknown model selector: '+pattern)
            objects = [rows for rows in objects if any(fnmatch.fnmatchcase(rows[0]['source'].lower(), p.lower()) for p in model_paths)]
        mats = materials_factory(lib, game_texture_dir(output.parent, game))
        roots = {rows[0]['source'].split('.')[0].lower() for rows in objects}
        by_root, by_preview = mats.variants(roots)
        records, catalog, index = [], [], {}
        for candidates in objects:
            obj = candidates[0]; source, cls = obj['source'], obj['cls']
            row = dict(obj, category=category_fn(obj)); records.append(row)
            entry = index.setdefault(source.lower(), dict(source=source, by_class={}))['by_class']
            try:
                reason = exclusion(row)
                if reason:
                    raise ValueError(reason)
                row['provenance'] = provenance(lib, candidates)
                p = lib.package(obj['file']); data = mesh(p, obj['ref'])
                if not data['groups']:
                    raise ValueError('Mesh has no drawable sections')
                refs = data.pop('material_refs')  # already package-qualified by mesh reader
                data['metadata'].update(source=source, source_class=cls, package_version=p.version, licensee=p.licensee)
                variants = skins_fn(source, refs, mats, by_root, by_preview)
                exported = []
                for number, variant in enumerate(variants):
                    materials = [mats.material(ref) for ref in variant['refs']]
                    ident = key(cls+':'+source) + (('_skin_'+key(variant['label'])) if number else '')
                    payload = dict(data, game=game, material_textures=[m['texture'] for m in materials],
                                   material_settings=[m['settings'] for m in materials],
                                   metadata=dict(data['metadata'], skin=variant['label'], assignment=variant['assignment'], materials=materials))
                    path = output/'model_json'/(ident+'.json'); write_model_data(path, payload)
                    buffers = json.loads(path.read_text(encoding='utf-8'))['geometry_buffers']
                    exported.append(dict(id=ident,label=variant['label'],assignment=variant['assignment']))
                    unresolved = sum(m['status'] != 'ready' for m in materials)
                    catalog.append(dict(game=game,model_name=ident,display_name=source+' / '+variant['label'],
                                        source=source,category=row['category'],status='ready',
                                        texture_name=materials[data['groups'][0]['materialIndex']]['texture'],
                                        warnings=[f'{unresolved} unresolved material slots'] if unresolved else []))
                row.update(status='ready',looks=[v['id'] for v in exported],vertices=len(data['vertices'])//3,triangles=len(data['indices'])//3)
                entry[cls] = dict(status='ready',looks=row['looks'],variants=exported,geometry=buffers,provenance=row['provenance'],
                                  selected=dict(file=obj['file'],export=obj['ref']),model_json='model_json/'+exported[0]['id']+'.json',
                                  lod=0,coordinate_system=data['metadata']['coordinate_system'])
            except (ValueError, OSError) as exc:
                row.update(status='skipped',reason=str(exc)); entry[cls] = dict(status='skipped',reason=str(exc))
        for entry in index.values():
            if len(entry['by_class']) == 1:
                entry.update(next(iter(entry['by_class'].values())))
        report = dict(game=game,sample=model_paths is not None,total_candidates=total,objects=len(records),
                      ready=sum(r['status']=='ready' for r in records),entries=len(catalog),
                      skipped=sum(r['status']=='skipped' for r in records),
                      skipped_reasons=dict(Counter(r['reason'] for r in records if r['status']=='skipped')),
                      textures=len(mats.textures),unresolved_materials=sum(m['status']!='ready' for m in mats.materials.values()),
                      unresolved_textures=sum(m['status']!='ready' for m in mats.textures.values()),
                      results=records,material_results=list(mats.materials.values()),texture_results=list(mats.textures.values()),
                      material_failures=mats.failures,inventory=lib.inventory,package_failures=lib.failures,lzo=lib.stats(),
                      elapsed_seconds=time.perf_counter()-started,
                      rules=dict(pose='LOD0 static/reference; no animation',storage='shared textures, original DXT DDS, float32 binary geometry',
                                 duplicates='original physical package, then non-map, then filename/export; byte-identical copies grouped by SHA256',
                                 index='case-insensitive package-qualified object; by_class resolves class collisions',
                                 sections=('BCMPMod* and 860/0 Community; other installed packages Retail' if game == 'nos' else
                                           '805/0 uncooked additions Community; Hi-Rez cooked 805/2 Retail')))
        report['issues'] = report['skipped']+report['unresolved_materials']+len(lib.failures)+len(mats.failures)
        report['report'] = str(output/'import-report.json')
        write_json_atomic(output/'catalog.json',catalog); write_json_atomic(output/'model-index.json',index)
        write_json_atomic(output/'import-report.json',report)
        return report
    finally:
        lib.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=LOCAL_DATA/'tas')
    parser.add_argument('--model',action='append',help='Package-qualified name/wildcard; repeat for samples')
    args = parser.parse_args()
    result = import_catalog(args.install,args.output,args.model)
    print(json.dumps({k:v for k,v in result.items() if k not in ('results','material_results','texture_results','inventory')},indent=2))

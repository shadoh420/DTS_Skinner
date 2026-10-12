"""Import Doom 3 / Resurrection of Evil or Quake 4 from read-only PK4 archives.

python tools/import_doom3.py --install "C:/Games/Doom 3" [--output local-data/doom3]
python -m tools.import_doom3 --game quake4 --install "C:/Games/Quake 4"
Use --inventory for a cheap dependency inventory (no geometry/image decoding),
or --sample 40 for a bounded, representative import. --select CAMPAIGN:NAME
may be repeated to import exact inventory IDs instead.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct

import numpy as np
from PIL import Image

if __package__:
    from .doom3_assets import Install, Declarations, Textures, Materials, path_name
    from .doom3_mesh import read_md5, read_lwo, read_ase, read_ma, copy_head_joints
    from .model_data import write_model_data
    from .texture_workshop import write_json_atomic
    from .texture_storage import write_texture
    from .local_data import LOCAL_DATA
else:
    from doom3_assets import Install, Declarations, Textures, Materials, path_name
    from doom3_mesh import read_md5, read_lwo, read_ase, read_ma, copy_head_joints
    from model_data import write_model_data
    from texture_workshop import write_json_atomic
    from texture_storage import write_texture
    from local_data import LOCAL_DATA


FORMATS={'.md5mesh','.lwo','.ase','.ma'}
READERS={'.lwo':read_lwo,'.ase':read_ase,'.ma':read_ma}


def category(name, variant='model'):
    if variant=='model_view': return 'Weapons: View models'
    if variant=='model_world': return 'Weapons: World models'
    prefix=name.split('_')[0]
    return {'monster':'Monsters','character':'Characters','char':'Characters','weapon':'Weapons: Pickups',
            'item':'Items','ammo':'Ammo','moveable':'Moveables','env':'Environment','func':'Props',
            'player':'Characters'}.get(prefix,'Other entities')


def idle_animation(entity, model):
    anims=model.get('anims',{})
    requested=entity.get('anim','idle').lower()
    # Do not turn an entity's attack/open animation into the browser's resting pose.
    if requested.startswith('idle') and requested in anims: return anims[requested]
    if 'idle' in anims: return anims['idle']
    return next((v for k,v in anims.items() if re.fullmatch(r'idle\d+',k)),None)


def inventory(install, policy=None):
    rows, declarations = [],{}
    for campaign,files in install.files.items():
        if campaign=='d3xp' and not any(a.campaign=='d3xp' for a in files.values()): continue
        defs=declarations[campaign]=Declarations(files,policy)
        prefix='Resurrection of Evil: ' if campaign=='d3xp' else ''
        for name in sorted(defs.entities):
            if campaign=='d3xp' and defs.sources[('entity',name)][1]!='d3xp': continue
            try: entity=defs.entity(name)
            except ValueError as exc:
                rows.append(dict(id=campaign+':'+name,campaign=campaign,name=name,category=prefix+(policy.category(name) if policy else category(name)),status='failed',reason=str(exc)))
                continue
            for variant in ('model','model_world','model_view'):
                reference=entity.get(variant)
                if not reference: continue
                model=defs.model(reference)
                mesh=path_name(model.get('mesh',''))
                if Path(mesh).suffix not in FORMATS:
                    if policy:
                        reason='Not a model: ' if Path(mesh).suffix in ('.fx','.prt','.gui') or mesh.startswith('_') else 'Unresolved model reference: '
                        rows.append(dict(id=campaign+':'+name+(':'+variant if variant!='model' else ''),campaign=campaign,name=name,category=policy.category(name,variant),status='failed',reason=reason+mesh))
                    continue
                ident=campaign+':'+name+(':'+variant if variant!='model' else '')
                row=dict(id=ident,campaign=campaign,name=name,variant=variant,category=prefix+(policy.category(name,variant) if policy else category(name,variant)),
                         reference=reference,mesh=mesh,entity=entity,model=model,
                         skin=entity.get('skin',model.get('skin','')),animation=(policy.idle_animation(entity,model) if policy else idle_animation(entity,model)),
                         definition=defs.sources[('entity',name)][0],status='ready')
                dependencies=[mesh]
                if row['animation']: dependencies.append(row['animation'])
                if policy:
                    try: policy.prepare(row,defs,dependencies)
                    except ValueError as exc: row.update(status='failed',reason=str(exc))
                if not policy and variant=='model' and entity.get('def_head'):
                    head=defs.model(entity['def_head']); row['head']=head
                    dependencies.append(path_name(head.get('mesh','')))
                    headanim=idle_animation({},head)
                    if headanim: dependencies.append(headanim)
                missing=[n for n in dependencies if n not in files]
                if missing: row.update(status='failed',reason='Missing dependency: '+', '.join(missing))
                rows.append(row)
        # Include static standalone props in all authored folders (including RoE's
        # models/david Maya files), even when no spawnable entity names them.
        for name,asset in sorted(files.items()):
            if not name.startswith('models/') or Path(name).suffix not in READERS: continue
            if campaign=='d3xp' and asset.campaign!='d3xp': continue
            folder=str(Path(name).parent).replace('\\','/').removeprefix('models/')
            rows.append(dict(id=campaign+':'+name,campaign=campaign,name=name,mesh=name,reference=name,
                             category=prefix+'Mapobjects: '+folder,entity={},model={},skin='',animation=None,status='ready'))
    return rows,declarations


def summary(rows):
    return dict(entries=len(rows),ready=sum(r['status']=='ready' for r in rows),failed=sum(r['status']=='failed' for r in rows),
                campaigns=dict(Counter(r['campaign'] for r in rows)),formats=dict(Counter(Path(r.get('mesh','')).suffix for r in rows)),
                failures=[{k:r[k] for k in ('id','reason')} for r in rows if r['status']=='failed'])


def choose_sample(rows, count):
    wanted=['monster_demon_imp','monster_demon_hellknight','monster_zsec_machinegun','monster_zombie_maint',
            'monster_demon_revenant','weapon_shotgun','weapon_shotgun:model_view','weapon_shotgun:model_world',
            'item_medkit','item_armor_shard','ammo_shells','moveable_barrel3','env_storagecabinet',
            'models/mapobjects/delta2/demonpork/puddle.lwo',
            'models/david/cavsolar_lamp.ma','monster_hunter_helltime','weapon_shotgun_double:model_view']
    ready=[r for r in rows if r['status']=='ready']; selected=[]; used=set()
    def add(row):
        if row['id'] not in used and len(selected)<count: selected.append(row); used.add(row['id'])
    for name in wanted:
        row=next((r for r in ready if r['id'].split(':',1)[1]==name),None)
        if row: add(row)
    # Round robin across campaign/category/format, rather than the first 40 names.
    buckets={}
    for row in ready:
        family=row['category'].removeprefix('Resurrection of Evil: ').split(':')[0]
        buckets.setdefault((row['campaign'],family,Path(row['mesh']).suffix),[]).append(row)
    while len(selected)<min(count,len(ready)):
        for bucket in buckets.values():
            if bucket: add(bucket.pop(0))
    return selected


def load_parts(row, files, defs, pose_callback=None, policy=None):
    mesh=row['mesh']; suffix=Path(mesh).suffix
    if suffix=='.md5mesh':
        animation=files[row['animation']].read() if row.get('animation') else None
        parts,joints=read_md5(files[mesh].read(),animation,pose_callback)
    else: parts,joints=READERS[suffix](files[mesh].read()),[]
    if policy: parts=policy.surfaces(parts,row.get('entity',{}))
    offset=row.get('model',{}).get('offset',(0,0,0))
    for part in parts:
        part['shader']=defs.remap(part['shader'],row.get('skin'))
        part['vertices']=[np.add(p,offset) for p in part['vertices']]
    if policy:
        policy.assemble(parts,joints,row,files,defs)
    if not policy and row.get('head'):
        entity,head=row['entity'],row['head']
        joint=next((j for j in joints if j['name'].lower()==entity.get('head_joint','').lower()),None)
        if joint is None: raise ValueError('Missing head attachment joint: '+entity.get('head_joint',''))
        headrow=dict(mesh=path_name(head['mesh']),model=head,skin=head.get('skin',''),animation=idle_animation({},head))
        headparts,_=load_parts(headrow,files,defs,
                              lambda headjoints:copy_head_joints(headjoints,joints,entity,np.array(joint['position'])))
        # SetupHead places the attachment at the body joint with the body's axis.
        for part in headparts:
            part['vertices']=[np.add(p,np.add(joint['position'],offset)) for p in part['vertices']]
        parts.extend(headparts)
    return parts,joints


def build_model(row, files, defs, materials, policy=None):
    game=policy.game if policy else 'doom3'
    parts,_=load_parts(row,files,defs,policy=policy)
    data=dict(game=game,winding='ccw',vertices=[],uvs=[],normals=[],colors=[],indices=[],groups=[],
              material_names=[],material_textures=[],material_settings=[],
              metadata=dict(format=Path(row['mesh']).suffix[1:],campaign=row['campaign'],entity=row['name'],
                            source=files[row['mesh']].origin+':'+row['mesh'],mesh=row['mesh'],skin=row.get('skin',''),
                            pose='idle frame 0' if row.get('animation') else 'bind pose',animation=row.get('animation'),
                            head=row.get('head'),spawnargs=row.get('entity',{}),materials=[],scale=1))
    if policy:
        data['metadata']['idle_snapshot']=row.get('idle_snapshot')
        data['metadata']['attachments']=[dict(name=p['name'],mesh=p['mesh'],joint=p['entity'].get('joint','')) for p in row.get('attachments',[])]
        data['metadata']['vehicle_parts']=[dict(name=p['name'],position=p['position'],mesh=p['mesh'],kind='attached mesh' if p['mesh'] else 'vehicle control (no mesh)',joint=p['entity'].get('joint','')) for p in row.get('vehicle_parts',[])]
    for part in parts:
        if not part['indices']: continue
        material=materials.resolve(part['shader'])
        if not material['visible']: continue
        offset,start=len(data['vertices'])//3,len(data['indices'])
        # Native Doom Z-up -> Skinner Y-up; a rotation, not a reflection.
        data['vertices'].extend(float(v) for p in part['vertices'] for v in (p[1],p[2],p[0]))
        data['normals'].extend(float(v) for p in part['normals'] for v in (p[1],p[2],p[0]))
        data['uvs'].extend(float(v) for uv in part['uvs'] for v in uv)
        data['colors'].extend(float(v) for c in part['colors'] for v in c[:3])
        data['indices'].extend(offset+i for i in part['indices'])
        data['groups'].append(dict(start=start,count=len(data['indices'])-start,materialIndex=len(data['material_names'])))
        data['material_names'].append(material['name'])
        diffuse=material['images']['diffuse']
        data['material_textures'].append(diffuse['texture'] if diffuse and diffuse['status']=='ready' else 'missing_'+game+'.png')
        data['material_settings'].append(material['settings'])
        data['metadata']['materials'].append(material)
    if not data['indices']: raise ValueError('No visible renderable triangles')
    for key in ('vertices','normals','uvs','colors'):
        if not np.isfinite(data[key]).all(): raise ValueError('Non-finite geometry '+key)
    return data


def import_catalog(install, output, *, sample=None, select=None, inventory_only=False, policy=None):
    output,root=Path(output).resolve(),Path(install).resolve()
    if output==root or root in output.parents: raise ValueError('Import output must be outside the read-only game install')
    game=policy.game if policy else 'doom3'
    missing_texture='missing_'+game+'.png'
    with Install(root,policy) as archive:
        rows,defs=inventory(archive,policy)
        report=dict(game=game,inventory=summary(rows),inventory_scope='Definition/dependency readiness only; geometry and images not decoded')
        if policy:
            report['declaration_warnings']=[w for d in defs.values() for w in d.warnings]
            report['excluded_assets']=policy.excluded_assets(archive.files)
            report['inventory']['categories']={c:dict(ready=sum(r['status']=='ready' for r in rows if r['category']==c),skipped=sum(r['status']!='ready' for r in rows if r['category']==c)) for c in sorted({r['category'] for r in rows})}
        if inventory_only:
            report['results']=[{k:v for k,v in r.items() if k not in ('entity','model','head')} for r in rows]
            return report
        if select:
            found={r['id'] for r in rows}
            missing=set(select)-found
            if missing: raise ValueError('Unknown inventory IDs: '+', '.join(sorted(missing)))
            rows=[r for r in rows if r['id'] in select]
        elif sample is not None:
            if sample<1: raise ValueError('--sample must be positive')
            rows=choose_sample(rows,sample)
        textures={c:Textures(files,output/'textures',policy) for c,files in archive.files.items()}
        materials={c:Materials(defs[c],textures[c]) for c in defs}
        write_texture(output/'textures',missing_texture,Image.new('RGBA',(2,2),(255,0,255,255)))
        catalog,results=[],[]
        for row in rows:
            ident=row['id']; key=re.sub(r'[^a-z0-9_-]+','_',ident.lower())[:120]+'_'+hashlib.sha256(ident.encode()).hexdigest()[:10]
            item=dict(model_name=key,display_name=row['name']+(' [view]' if row.get('variant')=='model_view' else ' [world]' if row.get('variant')=='model_world' else ''),
                      game=game,campaign=row['campaign'],category=row['category'],source=ident,texture_name=missing_texture)
            result=dict(id=ident,model=key,status='failed')
            if policy: result['category']=row['category']
            try:
                if row['status']=='failed': raise ValueError(row['reason'])
                data=build_model(row,archive.files[row['campaign']],defs[row['campaign']],materials[row['campaign']],policy)
                write_model_data(output/'model_json'/(key+'.json'),data)
                item.update(status='ready',texture_name=data['material_textures'][0])
                result.update(status='ready',vertices=len(data['vertices'])//3,triangles=len(data['indices'])//3,
                              slots=len(data['groups']),pose=data['metadata']['pose'],head=bool(row.get('head')),
                              missing_diffuse=sum(n==missing_texture for n in data['material_textures']))
            except (ValueError,OSError,KeyError,IndexError,struct.error) as exc:
                item.update(status='skipped',reason=str(exc)); result['reason']=str(exc)
            catalog.append(item); results.append(result)
        report.update(entries=len(rows),ready=sum(r['status']=='ready' for r in results),failed=sum(r['status']=='failed' for r in results),results=results,
                      material_results={c:list(m.records.values()) for c,m in materials.items()},
                      texture_results={c:list(t.records.values()) for c,t in textures.items()})
        if policy:
            report['categories']={c:dict(ready=sum(r['status']=='ready' for r in results if r['category']==c),skipped=sum(r['status']!='ready' for r in results if r['category']==c)) for c in sorted({r['category'] for r in results})}
        report['textures']=len({r['texture'] for t in textures.values() for r in t.records.values() if r['status']=='ready'})
        write_json_atomic(output/'catalog.json',catalog)
        write_json_atomic(output/'import-report.json',report)
        return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install',required=True,type=Path)
    parser.add_argument('--game',choices=('doom3','quake4'),default='doom3')
    parser.add_argument('--output',type=Path)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--sample',type=int)
    mode.add_argument('--select',action='append',help='Exact campaign:name inventory ID; repeat for several entries')
    mode.add_argument('--inventory',action='store_true',help='Read definitions and ZIP directories only; write nothing')
    args=parser.parse_args()
    policy=None
    if args.game=='quake4':
        if not __package__:
            import sys
            sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
        from tools import quake4_policy as policy
    report=import_catalog(args.install,args.output or LOCAL_DATA/args.game,sample=args.sample,select=args.select,inventory_only=args.inventory,policy=policy)
    print(json.dumps(report if args.inventory else {k:v for k,v in report.items() if k not in ('material_results','texture_results')},indent=2))

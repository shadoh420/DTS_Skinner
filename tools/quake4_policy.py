"""Raven rules for the shared id Tech 4 importer (read-only q4base PK4s)."""
from collections import Counter
from pathlib import Path
import re

import numpy as np

from .doom3_assets import compact, declarations, lex, path_name
from .doom3_mesh import joint_matrices, doom_quaternion, doom_rotate
from . import import_quake_md5 as md5

game = 'quake4'
campaigns = ('q4base',)
programs = {'downsize'}


def archives(folder):
    return sorted(p for p in folder.glob('*.pk4') if re.fullmatch(
        r'game\d+|pak\d{3}|q4cmp_pak001|zpak_english.*', p.stem.lower()))


def dds_path(expression):
    expression=path_name(expression).replace('.tga','')
    if '(' not in expression: return expression.removesuffix('.dds')+'.dds'
    # Raven retains the first image's directories; subsequent separators are
    # underscores. Parentheses/commas disappear, spaces become underscores.
    first,rest=expression.split('(',1)
    rest=rest.strip()
    image,_,tail=rest.partition(',')
    if not tail: image,_,tail=rest.partition(')')
    tail=tail.replace('/','_').replace('\\','_')
    value=first+'/'+image+(' '+tail.lstrip() if tail else '')
    value=value.replace('(','_').replace(')','').replace(',','')
    return 'progimg/'+re.sub(r'\s','_',value).rstrip('_')+'.dds'


def guides(files):
    result={}
    for name,asset in sorted(files.items()):
        if not name.endswith('.guide'): continue
        for header,body in declarations(lex(asset.read().decode('latin1'))):
            if len(header)>3:
                result[header[1].lower()]=([t for t in header[3:-1] if t!=','],body)
    return result


def tokens(t, ext, filename, templates, warnings):
    if ext=='.def':
        depth=t.count('{')-t.count('}')
        if depth==1:
            warnings.append(dict(file=filename,reason='Recovered missing final declaration brace at EOF'))
            t=t+['}']
    if ext!='.mtr': return t
    result=[]; i=0
    while i<len(t):
        kind=t[i].lower()
        if kind not in ('guide','inlineguide'):
            result.append(t[i]); i+=1; continue
        inline=kind=='inlineguide'
        name=None if inline else t[i+1]
        j=i+1 if inline else i+2
        template=t[j].lower(); j+=1
        while j<len(t) and t[j]=='\n': j+=1
        if j>=len(t) or t[j]!='(': raise ValueError('Invalid guide in '+filename)
        j+=1; args=[]; arg=[]; depth=1
        while j<len(t) and depth:
            token=t[j]; j+=1
            depth+=(token=='(')-(token==')')
            if (token==',' and depth==1) or depth==0:
                args.append(' '.join(compact(arg))); arg=[]
            else: arg.append(token)
        if template not in templates: raise ValueError('Missing guide '+template+' in '+filename)
        params,body=templates[template]
        if len(params)!=len(args): raise ValueError('Guide argument count: '+template)
        replacements=dict(zip(params,args))
        pattern=re.compile('|'.join(re.escape(p) for p in sorted(params,key=len,reverse=True)))
        expanded=[pattern.sub(lambda m:replacements[m[0]],v) for v in body]
        result.extend(expanded if inline else [name,'{',*expanded,'}'])
        i=j
    return result


def inherit(defs, collection, name, stack=()):
    name=path_name(name)
    if name in stack: raise ValueError('Cyclic definition: '+' -> '.join((*stack,name)))
    if name not in collection: raise ValueError('Missing definition: '+name)
    raw=collection[name]; result=dict(raw)
    if collection is defs.models:
        parent=raw.get('inherit')
        result=inherit(defs,collection,parent,(*stack,name)) if parent else {}
        anims={**result.get('anims',{}),**raw.get('anims',{})}
        result.update(raw); result['anims']=anims
    else:
        # idDeclEntityDef: MatchPrefix in declaration order, then SetDefaults.
        # Own values win; the first listed parent supplies each missing key.
        for key,parent in raw.items():
            if key.startswith('inherit') and parent:
                for k,v in inherit(defs,collection,parent,(*stack,name)).items():
                    if not k.startswith('inherit'): result.setdefault(k,v)
    return result


def category(name, variant='model'):
    from .import_doom3 import category as common
    return 'Vehicles' if name.startswith('vehicle_') else common(name,variant)


def idle_snapshot(entity, model):
    anims=model.get('anims',{})
    names=[entity.get(k,'').lower() for k in ('anim_idle','anim_idle1')]
    if 'idle' in entity.get('anim','').lower(): names.append(entity['anim'].lower())
    names+=['idle','idle1','stand','machinegun_idle']
    names += [n for n in anims if re.fullmatch(r'idle\d+',n)]
    for name in names:
        if name not in anims: continue
        clips=anims[name]
        if isinstance(clips,str): clips=[clips]
        # Player::UpdateAnimationControllers: pitch=0 -> weights (0,1,0).
        # idAnimBlend::CycleAnim defaults all other synced clips to (1,0,...).
        chosen=1 if len(clips)==3 and '_aim_down' in clips[0] else 0
        return dict(name=name,clips=clips,weights=[int(i==chosen) for i in range(len(clips))],clip=clips[chosen])
    return None


def idle_animation(entity, model):
    snapshot=idle_snapshot(entity,model)
    return snapshot['clip'] if snapshot else None


def prepare(row, defs, dependencies):
    row['idle_snapshot']=idle_snapshot(row['entity'],row['model'])
    entity=row['entity']=dict(row['entity'])
    if row['variant']=='model_view':
        # Weapon::GetWeaponDef: an unmodified weapon hides mod_showsurface.
        for key,name in list(entity.items()):
            if re.fullmatch(r'def_mod\d+',key) and name:
                for surfacekey,surface in defs.entity(name).items():
                    if surfacekey.startswith('mod_showsurface'):
                        entity['hidesurface_'+key+'_'+surfacekey]=surface
    if row['variant']!='model': return
    headname=entity.get('override_head',entity.get('def_head',''))
    if headname:
        headentity=defs.entity(headname)
        headmodel=defs.model(headentity.get('model',''))
        row['head']=dict(mesh=path_name(headmodel.get('mesh','')),model=headmodel,entity=headentity,
                         skin=headentity.get('skin',headmodel.get('skin','')),animation=idle_animation(headentity,headmodel))
        dependencies.extend(n for n in (row['head']['mesh'],row['head']['animation']) if n)
    row['vehicle_parts']=[]
    row['attachments']=[]
    for key,name in list(entity.items()):
        if key.startswith('def_attach') and name:
            attached=defs.entity(name); model=defs.model(attached.get('model',''))
            entry=dict(name=name,entity=attached,model=model,mesh=path_name(model.get('mesh','')),
                       skin=attached.get('skin',model.get('skin','')),animation=idle_animation(attached,model))
            if not entry['mesh']: raise ValueError('Attachment has no mesh: '+name)
            row['attachments'].append(entry)
            dependencies.extend(n for n in (entry['mesh'],entry['animation']) if n)
        if not key.startswith('def_position') or not name: continue
        position=defs.entity(name)
        # Parked external view, rvVehiclePosition::UpdateInternalView.
        if position.get('internalsurface'): entity['hidesurface_'+key]=position['internalsurface']
        for partkey,partname in position.items():
            if not partkey.startswith('def_part') or not partname: continue
            part=defs.entity(partname); model=defs.model(part.get('model',''))
            mesh=path_name(model.get('mesh',''))
            entry=dict(name=partname,position=name,entity=part,model=model,mesh=mesh,
                       skin=part.get('skin',model.get('skin','')),animation=idle_animation(part,model))
            row['vehicle_parts'].append(entry)
            if mesh: dependencies.extend(n for n in (mesh,entry['animation']) if n)


def surfaces(parts, entity):
    hidden={path_name(v) for k,v in entity.items() if k.startswith('hidesurface')}
    hidden.difference_update(path_name(v) for k,v in entity.items() if k.startswith('showsurface'))
    return [p for p in parts if path_name(p['shader']) not in hidden]


def transform(parts, matrix):
    for part in parts:
        part['vertices']=[(matrix @ np.r_[p,1])[:3] for p in part['vertices']]
        part['normals']=[matrix[:3,:3] @ n for n in part['normals']]


def angles_matrix(value):
    pitch,yaw,roll=np.radians(list(map(float,value.split())))
    sp,sy,sr=np.sin([pitch,yaw,roll]); cp,cy,cr=np.cos([pitch,yaw,roll])
    return np.array([[cp*cy,sr*sp*cy-cr*sy,cr*sp*cy+sr*sy],
                     [cp*sy,sr*sp*sy+cr*cy,cr*sp*sy-sr*cy],[-sp,sr*cp,cr*cp]])


def head_pose(head, body, headentity, bodyentity, attachment):
    bw,hw=joint_matrices(body),joint_matrices(head)
    def local(joints,world,i):
        parent=joints[i]['parent']
        return np.linalg.inv(world[parent]) @ world[i] if parent>=0 else world[i]
    locals_=[local(head,hw,i) for i in range(len(head))]; overrides={}
    names={j['name'].lower():i for i,j in enumerate(body)}
    targets={j['name'].lower():i for i,j in enumerate(head)}
    for key,target in headentity.items():
        if not key.startswith('copy_joint') or not target: continue
        worldkey='copy_joint_world '+target.lower()
        source=bodyentity.get(worldkey,bodyentity.get('copy_joint '+target.lower(),target)).lower()
        if source not in names or target.lower() not in targets:
            raise ValueError('Missing copy joint: '+source+' -> '+target)
        src,dst=names[source],targets[target.lower()]
        if worldkey in bodyentity: overrides[dst]=np.linalg.inv(attachment) @ bw[src]
        else: locals_[dst]=local(body,bw,src)
    result=[]
    for i,joint in enumerate(head):
        result.append(overrides[i] if i in overrides else result[joint['parent']] @ locals_[i] if joint['parent']>=0 else locals_[i])
    return result


def assemble(parts, joints, row, files, defs):
    from .import_doom3 import load_parts
    policy=defs.policy
    matrices=dict(zip((j['name'].lower() for j in joints),joint_matrices(joints)))
    offset=np.array(row.get('model',{}).get('offset',(0,0,0)))
    if row.get('head'):
        joint=row['entity'].get('joint_head','').lower()
        if joint not in matrices: raise ValueError('Missing head attachment joint: '+joint)
        attachment=matrices[joint].copy()
        head=row['head']
        headparts,_=load_parts(head,files,defs,lambda h:head_pose(h,joints,head['entity'],row['entity'],attachment),policy)
        attachment[:3,3]+=offset
        transform(headparts,attachment); parts.extend(headparts)
    for part in row.get('vehicle_parts',[]):
        # Turrets/weapons/wheels are controls on the parent skeleton, not meshes.
        if not part['mesh']: continue
        joint=part['entity'].get('joint','').lower()
        if joint and joint not in matrices: raise ValueError('Missing vehicle part joint: '+joint)
        attachment=matrices[joint].copy() if joint else np.eye(4)
        attachment[:3,3]+=offset+attachment[:3,:3] @ np.array(list(map(float,part['entity'].get('offset','0 0 0').split())))
        child,_=load_parts(part,files,defs,policy=policy)
        transform(child,attachment); parts.extend(child)
    if row.get('attachments'):
        # Actor::Spawn attaches equipment while IK_ANIM is set, then changes
        # to idle. The authored origin is in body axes at that binding pose.
        bind=md5.read_mesh(files[row['mesh']].read(),quaternion_fn=doom_quaternion)['joints']
        ik=row['model'].get('anims',{}).get('ik_pose')
        if ik:
            bind=md5.read_anim(files[ik[0]].read(),bind,quaternion_fn=doom_quaternion,rotate_fn=doom_rotate)['joints']
        bind_matrices=dict(zip((j['name'].lower() for j in bind),joint_matrices(bind)))
        for entry in row['attachments']:
            entity=entry['entity']; joint=entity.get('joint','').lower()
            if joint not in matrices or joint not in bind_matrices:
                raise ValueError('Missing equipment attachment joint: '+joint)
            local=np.eye(4); local[:3,:3]=angles_matrix(entity.get('angles','0 0 0'))
            local[:3,3]=bind_matrices[joint][:3,:3].T @ np.array(list(map(float,entity.get('origin','0 0 0').split())))
            attachment=matrices[joint] @ local; attachment[:3,3]+=offset
            child,_=load_parts(entry,files,defs,policy=policy)
            transform(child,attachment); parts.extend(child)


def excluded_assets(campaign_files):
    counts=Counter(Path(n).suffix for files in campaign_files.values() for n in files if Path(n).suffix in ('.fx','.gui','.prt'))
    return {ext:counts[ext] for ext in ('.fx','.gui','.prt')}

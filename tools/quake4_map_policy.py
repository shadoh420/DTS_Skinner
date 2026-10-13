"""Raven map snapshot rules; no game scripts, physics or random wave simulation.

Native references: Game_local.cpp InhibitEntitySpawn, spawner.cpp FindSpawnTypes/
SpawnEntity, Entity.cpp Event_SpawnBind, Mover.cpp idPusherMover::Think.
"""
from collections import Counter
import numpy as np

from . import quake4_policy as policy
from .doom3_assets import path_name
from .doom3_mesh import joint_matrices, read_md5


def enabled(entity, mode='dm'):
    return not any(k.startswith('filter_') for k in entity) or entity.get('filter_'+mode,'0')!='0'


def prepare_entities(entities, defs, name):
    notes=dict(layout='deathmatch' if name.startswith('mp/') else 'campaign', otherLayouts={}, filtered=[], spawners=[])
    # Persona overlay happens after the map spawnargs have been supplied.
    entities=[dict(e) for e in entities]
    for entity in entities:
        if entity.get('def_persona'):
            try:entity.update(defs.inherit(defs.entities,entity['def_persona']))
            except ValueError as exc:notes.setdefault('personaFallbacks',[]).append(dict(name=entity.get('name'),reason=str(exc)))
    if name.startswith('mp/'):
        modes=sorted({k[7:] for e in entities for k in e if k.startswith('filter_')})
        for mode in modes:
            if mode=='dm':continue
            added=[e for e in entities if enabled(e,mode) and not enabled(e)]
            notes['otherLayouts'][mode]=dict(adds=[e.get('name',e.get('classname')) for e in added], classes=dict(Counter(e.get('classname') for e in added)))
        notes['filtered']=[e.get('name',e.get('classname')) for e in entities if not enabled(e)]
        entities=[e for e in entities if enabled(e)]
    named={e.get('name'):e for e in entities}
    for entity in list(entities):
        if entity.get('classname')!='func_spawner' and 'spawner' not in entity.get('spawnclass','').lower():continue
        types=[v for k,v in entity.items() if k.startswith('def_spawn') and v]
        if not types:continue
        targets=[named[v] for k,v in entity.items() if k.startswith('target') and v in named and named[v].get('classname')=='target_null']
        point=targets[0] if targets else entity
        choices=[v for k,v in point.items() if k.startswith('def_spawn') and v] or types
        record=dict(spawner=entity.get('name'),point=point.get('name'),definition=choices[0],alternatives=choices[1:])
        try:
            spawned=defs.entity(choices[0])
            spawned.update({k[6:]:v for k,v in entity.items() if k.startswith('spawn_')})
            spawned.update(classname=choices[0],name=entity.get('name','spawner')+'__preview',origin=point.get('origin','0 0 0'),angle=point.get('angle','0'),_provenance=record)
            entities.append(spawned);record['status']='instantiated'
        except ValueError as exc:record.update(status='skipped',reason=str(exc))
        notes['spawners'].append(record)
    return entities,notes


def matrix(entity,parts=None):
    from .import_doom3_map import entity_transform, rotation, vector
    m=np.eye(4)
    if parts is None:m[:3,3]=vector(entity.get('origin'));m[:3,:3]=rotation(entity)
    else:m[:3,3],m[:3,:3]=entity_transform(entity,parts)
    return m


def compiled_static(entity,world):
    """idStaticEntity::Spawn hides dmap-inlined geometry, not a second model."""
    return (entity.get('classname')=='func_static' or entity.get('spawnclass')=='idStaticEntity') and (
        entity.get('inline','0')!='0' or world.get('inlineallstatics','0')!='0')


def attachment_transforms(entities,defs,files,inline):
    """World-space deltas, applied once *after* the authored entity transform.

    Ordinary binds preserve the child's authored world pose. Joint binds move
    from the parent's bindanim frame to the selected idle. Pusher movers snap
    their origin to attachedBone, retaining their own axis, as native Think does.
    """
    named={e['name']:e for e in entities if e.get('name')};result={};notes=[];active=set();poses={}
    def joints(parent,bind=False):
        model=defs.model(parent.get('model',''));mesh=path_name(model.get('mesh',''))
        anim=policy.idle_animation(parent,model)
        if bind:
            clips=model.get('anims',{}).get(parent.get('bindanim',parent.get('anim','')).lower(),[])
            anim=clips[0] if clips else anim
        key=(mesh,anim)
        if key not in poses:
            if not mesh.endswith('.md5mesh'):raise ValueError('Attachment parent is not skeletal: '+mesh)
            _,js=read_md5(files[mesh].read(),files[anim].read() if anim else None)
            poses[key]=dict(zip((j['name'].lower() for j in js),joint_matrices(js)))
        offset=np.array(model.get('offset',(0,0,0)))
        return {k:np.array(m)+np.column_stack([np.zeros((4,3)),np.r_[offset,0]]) for k,m in poses[key].items()}
    def visit(name):
        if name in result:return result[name]
        if name in active:raise ValueError('Cyclic bind: '+name)
        active.add(name);entity=named[name];delta=np.eye(4)
        try:
            parentname=entity.get('bind',entity.get('attachedentity'))
            if parentname:
                if parentname not in named:raise ValueError('Missing bind parent: '+parentname)
                parent=named[parentname];parentdelta=visit(parentname)
                old=matrix(parent);new=parentdelta @ matrix(parent,inline.get(path_name(parent.get('model','')),[]))
                joint=entity.get('bindtojoint',entity.get('attachedbone','')).lower()
                if entity.get('attachedentity'):
                    if not joint:raise ValueError('Missing attachedBone')
                    target=new @ joints(parent)[joint]
                    delta[:3,3]=target[:3,3]-matrix(entity)[:3,3]
                else:
                    if joint:old=old @ joints(parent,True)[joint];new=new @ joints(parent)[joint]
                    delta=new @ np.linalg.inv(old)
                    if entity.get('bindorientated')=='0':
                        origin=matrix(entity)[:3,3];shift=(delta @ np.r_[origin,1])[:3]-origin
                        delta=np.eye(4);delta[:3,3]=shift
                    if entity.get('bindtobody'):notes.append(dict(name=name,reason='bindToBody uses parent frame; articulated physics is not simulated'))
                notes.append(dict(name=name,parent=parentname,joint=joint,status='evaluated'))
        except (ValueError,KeyError) as exc:
            notes.append(dict(name=name,status='authored fallback',reason=str(exc)))
        finally:active.remove(name)
        result[name]=delta
        return delta
    for name,entity in named.items():
        if entity.get('bind') or entity.get('attachedentity'):visit(name)
    return result,notes


def transformed_light(entity,delta):
    from .import_doom3_map import rotation, vector
    result=dict(entity)
    origin=(delta @ np.r_[vector(entity.get('light_origin',entity.get('origin'))),1])[:3]
    axis=delta[:3,:3] @ rotation(entity,'light_rotation' if entity.get('light_rotation') else 'rotation')
    result['light_origin']=' '.join(map(str,origin));result['light_rotation']=' '.join(map(str,axis.T.ravel()))
    return result

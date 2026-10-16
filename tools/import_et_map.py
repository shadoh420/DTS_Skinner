"""Local ET BSP47 maps, sharing RTCW geometry, materials and placed-model export.

ET: Legacy classic renderer/game files are format/rule references only.
"""
import argparse
from collections import Counter
import copy
import hashlib
import heapq
import io
import json
import math
from pathlib import Path
import re
import struct
import sys
import zipfile

from PIL import Image
import numpy as np

try:
    from tools import import_et as et, import_rtcw_map as base
except ImportError:
    import import_et as et
    import import_rtcw_map as base
qmap,rtcw=base.qmap,et.rtcw
vector,number=base.vector,base.number
# SetFarClip ends the view at the global fog's depth for opaque.
GLOBAL_FOG_CLIPS=True
NAMED_SUN=True  # tr_shader.c: ET draws the sunshader it names; RTCW always 'sun'.
SMOKE_IN_MP=True  # ET's target_smoke is a client ET_SMOKER (CG_Smoker); RTCW frees it outside SP.
DLIGHT_NOTE='dynamic light: STARTOFF (spawnflags 2) until a script switches it on (SP_dlight)'
MAPS=('battery','fueldump','goldrush','oasis','radar','railgun')
# Stock maps place only the objective item, whose model is supplied by its entity.
# bg_misc.c world-model facts for the health/ammo pickups retained by ET.
ITEMS={'et':{
    'team_ctf_redflag':('team','','',False),'team_ctf_blueflag':('team','','',False),
    'item_health':('health','models/multiplayer/medpack/medpack_pickup.md3','',False),
}}
PROP_MODELS={**base.PROP_MODELS,
    'team_wolf_checkpoint':'models/multiplayer/flagpole/flagpole.md3',
    'misc_cabinet_health':'models/mapobjects/supplystands/stand_health.md3',
    'misc_cabinet_supply':'models/mapobjects/supplystands/stand_ammo.md3'}


class Game(et.Game):
    def __init__(self,install):
        super().__init__(install)
        self.files={n:(a['source'],a['read']) for n,a in self.assets.items()}
        self.definitions=rtcw.shader_definitions(self)
        self.shaders={n:(b,[[(['map']+r[1:] if r[0]=='lightmap' else r) for r in stage] for stage in stages]) for n,(b,stages,_) in self.definitions.items()}
        self.shader_label={n:self.assets[p]['source'] for n,(_,_,p) in self.definitions.items()}
        self.outside,self.images,self.described,self.models=set(),{},{},{}
        self.lighting=base.lighting_profile(install,'et',self,directory='etmain',
            configs=('default.cfg','etconfig.cfg','autoexec.cfg'),defaults=dict(r_gamma=1.3,r_texturemode='GL_LINEAR_MIPMAP_NEAREST',r_drawfoliage=1),
            device_supports_gamma=False)
        # Retail 2.60b captures on the target PC confirm the texture-gamma path
        # even with r_ignorehwgamma=0: its display cannot supply a gamma ramp.
        self.lighting['source']='ET classic renderer, no hardware gamma on the measured retail 2.60b PC; gamma baked into texture uploads, no display ramp'
        self.built=False;self.remaps={}


class ModelTextures(base.ModelTextures):
    def __init__(self,game,output):
        super().__init__(game,output);et.et_shader_settings(self.shaders)

    def resolve(self,shader):
        return super().resolve(self.game.remaps.get(qmap.shader_name(shader),shader))


def bsp_bytes(lumps):
    data=bytearray(144);struct.pack_into('<4si',data,0,b'IBSP',47)
    for i,lump in enumerate(lumps):
        struct.pack_into('<2i',data,8+i*8,len(data),len(lump));data.extend(lump)
    return bytes(data)


def read_bsp(raw,diagnostics=None):
    """Expand MST_FOLIAGE into vertex-lit triangles before shared validation."""
    notes=diagnostics if diagnostics is not None else []
    lumps=qmap.read_bsp(raw,version=47)
    for off,size in struct.iter_unpack('<2i',raw[8:144]):
        if size and off<144:raise ValueError('Lump overlaps header')
    if len(lumps[10])%44 or len(lumps[11])%4 or len(lumps[13])%104:raise ValueError('Partial ET draw record')
    vertices,indices,faces=bytearray(lumps[10]),bytearray(lumps[11]),bytearray(lumps[13])
    for at in range(0,len(faces),104):
        s=list(struct.unpack_from('<12i12f2i',faces,at))
        if s[2]!=5:continue
        first,total,start,count,instances,nv=s[3],s[4],s[5],s[6],s[24],s[25]
        if min(first,start,count,instances,nv)<0 or nv==0 or first+nv+instances>len(lumps[10])//44 or nv+instances>total or start+count>len(lumps[11])//4:
            raise ValueError('Invalid foliage vertex/instance range')
        if len(vertices)//44+instances*nv>4000000 or len(indices)//4+instances*count>12000000:raise ValueError('Foliage expansion exceeds preview bound')
        triangles=struct.unpack_from('<'+'i'*count,lumps[11],start*4)
        if any(i<0 or i>=nv for i in triangles):raise ValueError('Invalid foliage index')
        s[2],s[3],s[4],s[5],s[6],s[7]=3,len(vertices)//44,instances*nv,len(indices)//4,instances*count,-3
        for instance in range(instances):
            p=struct.unpack_from('<10f4B',lumps[10],(first+nv+instance)*44);rtcw.finite(p[:3])
            for j in range(nv):
                v=list(struct.unpack_from('<10f4B',lumps[10],(first+j)*44));v[:3]=[a+b for a,b in zip(v[:3],p[:3])];v[10:]=p[10:]
                vertices.extend(struct.pack('<10f4B',*v))
            indices.extend(struct.pack('<'+'i'*count,*(i+instance*nv for i in triangles)))
        struct.pack_into('<12i12f2i',faces,at,*s)
        notes.append(dict(surface=at//104,action='foliage instances expanded',instances=instances,vertices=nv,sourceVertex=first+nv,reason='ParseFoliage: translated base mesh and per-instance vertex color'))
    lumps[10],lumps[11],lumps[13]=bytes(vertices),bytes(indices),bytes(faces)
    return base.read_bsp(bsp_bytes(lumps),notes)


def describe(game,name,textures):
    name=game.remaps.get(name,name)
    body,stages=game.shaders.get(name,([],[]))
    implicit=next((r for r in body if r[0].startswith('implicit')),None)
    if implicit and not stages:
        target=implicit[1] if len(implicit)>1 and implicit[1]!='-' else name
        words={r[0] for r in body};textures.mipmap='nomipmaps' not in words;textures.picmip=not words.intersection(('nomipmaps','nopicmip'))
        image=textures.resolve(game,target)
        cull=next((r[1].lower() for r in body if r[0]=='cull' and len(r)>1),'front' if implicit[0]=='implicitmap' else 'none')
        cull='none' if cull in ('none','twosided','disable') else 'back' if cull in ('back','backside','backsided') else 'front'
        return (dict(name=name,implicit=True,implicitType=implicit[0],map=image,cull=cull,sort=3,noMipmaps='nomipmaps' in words,outside=[]) if image else dict(name=name,default=True,cull='front',sort=3)),([] if image else ['texture '+target]),[]
    result,missing,limits=base.describe(game,name,textures)
    limits=[n for n in limits if not n.startswith('implicit directive compatibility')]
    for stage,rows in zip(result.get('stages',[]),stages):
        if stage.get('rgbGen')==['vertex'] and not any(r[0]=='alphagen' for r in rows):stage['alphaGen']=['vertex']
        vectors=next((r[2:] for r in rows if r[0] in ('tcgen','texgen') and len(r)>1 and r[1].lower()=='vector'),None)
        if vectors:
            values=[float(n) for n in re.findall(r'[-+]?(?:\d*\.)?\d+(?:[eE][-+]?\d+)?',' '.join(vectors))]
            if len(values)==6:stage['tcGenVectors']=rtcw.finite(values);limits=[n for n in limits if n!='texture coordinates from vectors']
    distance=next((r[1:] for r in body if r[0]=='distancecull'),None)
    if distance and len(distance)==3:
        values=rtcw.finite([float(x) for x in distance])
        if values[1]>values[0]:result['distanceCull']=values
    return result,missing,limits


def scripts(raw):
    result={}
    for name,(headers,blocks) in qmap.parse_shaders(et.q3.read_text(raw)).items():
        events={}
        for header,commands in zip(headers,blocks):events.setdefault(' '.join(header).lower(),commands)
        result[name]=events
    return result


def path_points(nodes,name):
    e=nodes[name]
    controls=[e[k] for k in ('control',)+tuple('control'+str(i) for i in range(2,17)) if k in e]
    return np.array([vector(e.get('origin'))]+[vector(nodes[c].get('origin')) for c in controls]+[vector(nodes[e['target']].get('origin'))])


class LinearPaths:
    """ET's 16-chord paths and fixed-radius trailer joints, for a static snapshot."""
    def __init__(self,things):
        self.nodes={e['targetname']:e for e in things if e.get('targetname')};self.cache={}

    def points(self,name):
        if name not in self.cache:
            controls=path_points(self.nodes,name);points=[]
            for t in np.linspace(0,1,17):
                p=controls.copy()
                while len(p)>1:p=p[:-1]*(1-t)+p[1:]*t
                points.append(p[0])
            self.cache[name]=np.array(points)
        return self.cache[name]

    def sample(self,name,t):
        p=self.points(name);i=min(15,int(t*16));f=t*16-i
        return p[i]*(1-f)+p[i+1]*f

    def advance(self,name,t,radius):
        origin=self.sample(name,t);original=(name,t,origin);direction=1 if radius>=0 else -1
        if not radius:return name,t,origin
        index=min(15,int(t*16));fraction=t*16-index
        for _ in range(4096):
            p=self.points(name);start=p[index];delta=p[index+1]-start;d=start-origin
            a=float(delta@delta);b=2*float(d@delta);c=float(d@d)-radius*radius
            if a and b*b>=4*a*c:
                roots=sorted(((-b-math.sqrt(max(0,b*b-4*a*c)))/(2*a),(-b+math.sqrt(max(0,b*b-4*a*c)))/(2*a)),reverse=direction<0)
                for r in roots:
                    if 0<=r<=1 and (r-fraction)*direction>1e-7:return name,(index+r)/16,start+delta*r
            index+=direction;fraction=0 if direction>0 else 1
            if not 0<=index<16:
                following=self.nodes[name].get('target') if direction>0 else next((k for k,v in self.nodes.items() if v.get('target')==name and v['classname']=='info_train_spline_main'),None)
                if not following or not self.nodes.get(following,{}).get('target'):return original
                name=following;index=0 if direction>0 else 15
        raise ValueError('Path traversal exceeds bound')

    def pose(self,name,t,length):
        origin=self.sample(name,t);back=self.advance(name,t,length)
        direction=(origin-back[2])*(1 if length>0 else -1) if length else self.points(name)[min(16,int(t*16)+1)]-self.points(name)[min(15,int(t*16))]
        return origin,direction_angles(direction),back


def spline_pose(things,name,backward,length=0):
    """End of a startup spline move: Bezier position and length-offset heading."""
    nodes={e.get('targetname'):e for e in things if e.get('targetname')}
    cache={}
    def curve(name):
        if name not in cache:
            e=nodes[name];points=path_points(nodes,name)
            def sample(t):
                work=points.copy()
                while len(work)>2:work=work[:-1]*(1-t)+work[1:]*t
                return work[0]*(1-t)+work[1]*t,work[1]-work[0]
            samples=[sample(i/100)[0] for i in range(101)]
            size=sum(float(np.linalg.norm(b-a)) for a,b in zip(samples,samples[1:]))
            cache[name]=(e,sample,size)
        return cache[name]
    e,sample,size=curve(name);t=0 if backward else 1;origin,direction=sample(t)
    if length and size:
        n=name;u=t+length/size
        for _ in range(1024):
            node,fn,span=curve(n)
            if 0<=u<=1:
                point,_=fn(u);direction=(point-origin)*(1 if length>0 else -1);break
            following=node.get('target') if u>1 else next((k for k,v in nodes.items() if v.get('target')==n and v['classname']=='info_train_spline_main'),None)
            if not following or not nodes.get(following,{}).get('target'):break
            distance=(u-1)*span if u>1 else -u*span
            _,_,next_size=curve(following)
            if not next_size:break
            u=distance/next_size if u>1 else 1-distance/next_size;n=following
    return list(origin),direction_angles(direction),size


def direction_angles(direction):
    x,y,z=direction
    return [-math.degrees(math.atan2(z,math.hypot(x,y))),math.degrees(math.atan2(y,x)),0]


def spawn_states(things,program,built=False):
    """Bounded spawn-event evaluator; only static presentation/state is interpreted.

    No game code runs. Timed initialization waits are honored; ongoing movement
    and gameplay are frozen/reported. Construction completion is a second pass.
    """
    things=copy.deepcopy(things);notes=[];autospawn={};queue=[];ticket=0;acc={};glob={};versions={};steps=0;ignored=Counter();pending_remaps={};remaps={};linear=LinearPaths(things)
    for i,e in enumerate(things):
        e['classname']=e.get('classname','').lower();e['_entity']=i;e['_reason']='authored spawn'
        cls=e['classname'];flags=int(number(e,'spawnflags'))
        if cls=='func_constructible':e['_unbuilt']=not bool(flags&1)
        if cls in ('func_brushmodel','func_debris'):e['_removed']='server brush template/debris, freed after spawn'
        if cls in ('func_static','func_explosive','script_mover') and flags&1:e['_state']='invisible'
    def targets(name,field='targetname'):
        return [i for i,e in enumerate(things) if e.get(field,'').lower()==name.lower() and not e.get('_removed')]
    def schedule(i,event,time):
        nonlocal ticket
        rows=program.get(things[i].get('scriptname','').lower(),{}).get(event.lower(),[])
        if rows:ticket+=1;heapq.heappush(queue,(time,ticket,i,event,0,rows,versions.get(i,0)))
    def run(i,event,rows,at,time,depth=0):
        nonlocal ticket,steps
        if depth>64:notes.append(dict(entity=i,reason='script recursion bound reached'));return False
        previous=versions.get(i,0);versions[i]=previous+1;e=things[i]
        while at<len(rows) and not e.get('_removed'):
            steps+=1
            if steps>20000:notes.append(dict(reason='script instruction bound reached'));queue.clear();return False
            row=rows[at];at+=1;cmd=row[0].lower();a=row[1:];why=f'{e.get("scriptname",i)} / {event}: '+ ' '.join(row)
            if cmd in ('wait','resetscript'):
                try:delay=50 if cmd=='resetscript' else max(50,int(a[0]))
                except (ValueError,IndexError):delay=3000
                ticket+=1;heapq.heappush(queue,(time+delay,ticket,i,event,at,rows,versions[i]));return False
            if cmd=='trigger' and len(a)>1:
                for t in ([i] if a[0].lower()=='self' else targets(a[0],'scriptname')):
                    nested=program.get(things[t].get('scriptname','').lower(),{}).get('trigger '+a[1].lower(),[])
                    if nested:
                        finished=run(t,'trigger '+a[1],nested,0,time,depth+1)
                        if t==i and not finished:return False
            elif cmd=='setstate' and len(a)>1:
                for t in targets(a[0]):things[t].update(_state=a[1].lower(),_reason=why)
            elif cmd=='remapshader' and len(a)>1:pending_remaps[qmap.shader_name(a[0])]=qmap.shader_name(a[1])
            elif cmd=='remapshaderflush':remaps.update(pending_remaps)
            elif cmd=='remove':e.update(_removed='script remove',_reason=why)
            elif cmd in ('accum','globalaccum') and len(a)>=2:
                store=glob if cmd=='globalaccum' else acc.setdefault(i,{})
                idx=int(a[0]);value=store.get(idx,0);op=a[1].lower();arg=int(a[2]) if len(a)>2 else 0
                if op=='set':store[idx]=arg
                elif op=='inc':store[idx]=value+arg
                elif op=='bitset' and 0<=arg<32:store[idx]=value|(1<<arg)
                elif op=='bitreset' and 0<=arg<32:store[idx]=value&~(1<<arg)
                elif op.startswith('abort_if_'):
                    bit=bool(value&(1<<arg)) if 0<=arg<32 else False
                    condition={'abort_if_equal':value==arg,'abort_if_not_equal':value!=arg,'abort_if_less_than':value<arg,'abort_if_greater_than':value>arg,'abort_if_bitset':bit,'abort_if_not_bitset':not bit}.get(op)
                    if condition:break
                    if condition is None:notes.append(dict(entity=i,action=row,reason='unsupported condition; event stopped'));break
                elif op in ('abort','stop'):break
            elif cmd=='setautospawn' and len(a)>1:autospawn[int(a[1])]=a[0]
            elif cmd=='setmodel' and a:e['model2' if e.get('model','').startswith('*') else 'model']=a[0];e['_reason']=why
            elif cmd=='setposition' and a:
                t=targets(a[0]);e['origin']=things[t[0]].get('origin','0 0 0') if t else e.get('origin','0 0 0');e['_reason']=why
            elif cmd=='faceangles' and len(a)>=3:e['angles']=' '.join(a[:3]);e['_reason']=why
            elif cmd=='startanimation' and a:e['frame']=a[0]
            elif cmd=='attachtotag' and len(a)>1:e['_attachment']=[a[0],a[1]];e['_reason']=why
            elif cmd=='attatchtotrain' and len(a)>1:e['_train']=[a[0],float(a[1])];e['_reason']=why
            elif cmd.startswith('constructible_'):e[cmd]=' '.join(a)
            elif cmd=='kill' and a:
                for t in targets(a[0],'scriptname'):schedule(t,'death',time)
            elif cmd=='followspline' and len(a)>=3:
                try:
                    if a[0] in ('accum','globalaccum'):
                        backward=(glob if a[0]=='globalaccum' else acc.setdefault(i,{})).get(int(a[1]),0)!=0;args=a[2:]
                    else:backward=int(a[0])!=0;args=a[1:]
                    length=float(args[args.index('length')+1]) if 'length' in args else 0
                    pos,angles,distance=spline_pose(things,args[0],backward,length)
                    duration=max(50,int(distance/max(.001,float(args[1]))*1000))
                    if time+duration<=2000:
                        e.update(origin=' '.join(map(str,pos)),angles=' '.join(map(str,angles)),_reason=why)
                        notes.append(dict(entity=i,action=row,reason='startup spline completed',origin=pos,angles=angles))
                        if 'wait' in args:
                            ticket+=1;heapq.heappush(queue,(time+duration,ticket,i,event,at,rows,versions[i]));return False
                    else:notes.append(dict(entity=i,action=row,reason='startup movement exceeds initialization window; authored pose retained'));break
                except (ValueError,KeyError,IndexError) as exc:
                    notes.append(dict(entity=i,action=row,reason='unresolved startup spline: '+str(exc)));break
            elif cmd=='gotomarker' and len(a)>1:
                t=targets(a[0]);start=np.array(vector(e.get('origin')))
                if not t:notes.append(dict(entity=i,action=row,reason='missing movement target'));return False
                end=np.array(vector(things[t[0]].get('origin')));duration=max(50,int(np.linalg.norm(end-start)/max(.001,float(a[1]))*1000))
                progress=min(1,max(0,2000-time)/duration);pos=start+(end-start)*progress
                e.update(origin=' '.join(map(str,pos)),_reason=why)
                notes.append(dict(entity=i,action=row,reason='startup marker move sampled at initialization boundary',origin=list(pos)))
                if 'wait' in a:
                    if progress<1:return False
                    ticket+=1;heapq.heappush(queue,(time+duration,ticket,i,event,at,rows,versions[i]));return False
            elif cmd=='followpath' and len(a)>=3:
                name=a[1];length=float(a[a.index('length')+1]) if 'length' in a else 0
                points=linear.points(name);distance=sum(float(np.linalg.norm(b-a)) for a,b in zip(points,points[1:]))
                duration=max(50,int(distance/max(.001,float(a[2]))*1000));progress=min(1,max(0,2000-time)/duration)
                t=1-progress if int(a[0]) else progress;pos,rot,back=linear.pose(name,t,length)
                e.update(origin=' '.join(map(str,pos)),angles=' '.join(map(str,rot)),_path=[name,t,length],_reason=why)
                notes.append(dict(entity=i,action=row,reason='startup linear path sampled at initialization boundary',origin=list(pos),angles=rot))
                if 'wait' in a:
                    if progress<1:return False
                    ticket+=1;heapq.heappush(queue,(time+duration,ticket,i,event,at,rows,versions[i]));return False
            elif cmd in ('setrotation','alertentity','set','cvar'):
                notes.append(dict(entity=i,action=row,reason='runtime motion/control frozen at authored spawn'))
                if cmd in ('gotomarker','followpath') and 'wait' in a:return False
            else:ignored[cmd]+=1
        versions[i]=previous
        return True
    def execute():
        while queue:
            time,_,i,event,at,rows,version=heapq.heappop(queue)
            if version!=versions.get(i,0):continue  # superseded by a blocking trigger
            if time>2000:notes.append(dict(entity=i,event=event,reason='spawn wait beyond 2 seconds; later gameplay frozen'));continue
            run(i,event,rows,at,time)
    for i in range(len(things)):schedule(i,'spawn',0)
    execute()
    spawn=copy.deepcopy(things);spawn_autospawn=dict(autospawn)
    if built:
        choices={}
        for e in things:
            if e['classname']=='func_constructible' and not e.get('_removed') and e.get('targetname'):
                choices.setdefault(e['targetname'],[]).append(e)
        for i,e in enumerate(things):
            if e['classname']!='func_constructible' or not e.get('_unbuilt') or e.get('_removed') or number(e,'notteam'):continue
            alternatives=choices.get(e.get('targetname'),[e])
            winner=min(alternatives,key=lambda t:(bool(t.get('_unbuilt')),not bool(int(number(t,'spawnflags'))&4),t['_entity']))
            if winner is not e:
                notes.append(dict(entity=i,reason='mutually exclusive constructible: finished view uses existing built owner, otherwise Axis',selected=winner['_entity']));continue
            e['_unbuilt']=False;e['_state']='default';e['_reason']='finished constructible: final brush model'
            stages=[x for x in e.get('constages','').split(';') if x]
            for stage in range(1,len(stages)+1):schedule(i,'built stage'+str(stage),0)
            schedule(i,'built final',0)
            for marker in things:
                if marker['classname']=='misc_constructiblemarker' and marker.get('target')==e.get('targetname'):
                    marker['_state']='invisible';marker['_reason']='construction marker hidden when target is built'
        execute()
    for e in things:e['_autospawn']=spawn_autospawn
    changes=[dict(entity=i,classname=e['classname'],targetname=e.get('targetname'),scriptname=e.get('scriptname'),start=dict(state=spawn[i].get('_state','default'),unbuilt=spawn[i].get('_unbuilt',False),reason=spawn[i]['_reason']),built=dict(state=e.get('_state','default'),unbuilt=e.get('_unbuilt',False),reason=e['_reason'])) for i,e in enumerate(things) if (e.get('_state'),e.get('_unbuilt'),e.get('model'),e.get('model2'))!=(spawn[i].get('_state'),spawn[i].get('_unbuilt'),spawn[i].get('model'),spawn[i].get('model2'))]
    return things,dict(changes=changes,scriptNotes=notes,autoSpawn=autospawn,ignoredActions=dict(ignored),shaderRemaps=remaps)


def prepare_entities(game,name,things):
    path='maps/'+name+'.script';program=scripts(game.read(path)) if path in game.assets else {}
    things,game.state_report=spawn_states(things,program,game.built)
    game.remaps=game.state_report['shaderRemaps']
    for e in things:
        if not e.get('skin') and e.get('aiskin'):e['skin']=e['aiskin']
        if e['classname'] in ITEMS['et'] and e.get('model2'):e['model']=e['model2']
        if e['classname']=='misc_gamemodel' and 'start' in e:e['frame']=e['start']
    position_trains(things,game.state_report['scriptNotes'])
    position_attachments(game,things)
    game.prepared=things
    return things


def mounted_paths(e):
    return ['models/multiplayer/mg42/mg42.md3']+([] if int(number(e,'spawnflags'))&2 else ['models/mapobjects/weapons/mg42b.md3'])


def matrix(e):
    m=np.eye(4);m[:3,:3]=np.array(rtcw.axis([math.radians(v) for v in base.angles(e)])).T@np.diag(base.scale(e));m[:3,3]=vector(e.get('origin'));return m


def tag_matrix(tag):
    m=np.eye(4);m[:3,:3]=np.array(tag[3:]).reshape(3,3).T;m[:3,3]=tag[:3];return m


def model_tags(game,path,frame=0):
    path=game.model_path(path);key=(path,frame)
    if key not in game.models:game.models[key]=rtcw.read_model(game.read(path),frame)
    return game.models[key]['tags']


def position_trains(things,notes):
    paths=LinearPaths(things);done={};active=set()
    def place(i):
        if i in done:return done[i]
        if i in active:raise ValueError('Cyclic train attachment')
        active.add(i);e=things[i]
        if e.get('_path'):
            name,t,length=e['_path'];_,_,back=paths.pose(name,t,length)
        elif e.get('_train'):
            name,length=e['_train'];parent=next((j for j,p in enumerate(things) if p.get('targetname')==name or p.get('scriptname')==name),None)
            if parent is None:raise ValueError('Missing linked train '+name)
            n,t,origin=place(parent);back=paths.advance(n,t,length)
            rot=direction_angles((origin-back[2])*(1 if length>0 else -1))
            e.update(origin=' '.join(map(str,origin)),angles=' '.join(map(str,rot)))
        else:raise ValueError('Linked train has no startup path')
        active.remove(i);done[i]=back;return back
    for i,e in enumerate(things):
        if not e.get('_train') or filtered(e,'et'):continue
        try:place(i)
        except (ValueError,KeyError) as exc:
            notes.append(dict(entity=i,reason=str(exc)));e['_removed']='unresolved train attachment: '+str(exc);active.clear()


def position_attachments(game,things):
    done={};active=set()
    def place(i):
        if i in done:return done[i]
        if i in active:raise ValueError('Cyclic map model attachment')
        active.add(i);e=things[i];m=matrix(e)
        if e.get('_attachment'):
            name,tag=e['_attachment'];parent=next((j for j,p in enumerate(things) if p.get('targetname')==name or p.get('scriptname')==name),None)
            if parent is None:raise ValueError('Missing attachment parent '+name)
            p=things[parent];pm=place(parent);path=p.get('model2') or p.get('model')
            tags=model_tags(game,path,int(number(p,'frame')))
            transform=next((v for n,v in tags.items() if n.lower()==tag.lower()),None)
            if transform is None:raise ValueError('Missing model tag '+tag+' in '+path)
            m[:3,3]=0;m=pm@tag_matrix(transform)@m
        done[i]=m;active.remove(i);return m
    for i,e in enumerate(things):
        if not e.get('_attachment') or filtered(e,'et') or e['classname'].startswith(('trigger_','target_')):continue
        try:
            m=place(i);s=np.linalg.norm(m[:3,:3],axis=0);a=m[:3,:3]/s
            angle=[math.degrees(math.asin(max(-1,min(1,-a[2,0])))),math.degrees(math.atan2(a[1,0],a[0,0])),math.degrees(math.atan2(a[2,1],a[2,2]))]
            e.update(origin=' '.join(map(str,m[:3,3])),angles=' '.join(map(str,angle)),modelscale_vec=' '.join(map(str,s)))
        except (ValueError,KeyError,OSError) as exc:
            game.state_report['scriptNotes'].append(dict(entity=i,reason=str(exc)));e['_removed']='unresolved attachment: '+str(exc);active.clear()


def filtered(e,section):
    if number(e,'notteam'):return 'excluded by ET team gametype'
    if e.get('_removed'):return e['_removed']+'; '+e.get('_reason','')
    if e.get('_unbuilt'):return 'unbuilt constructible at map start; '+e.get('_reason','')
    if e.get('_state') in ('invisible','underconstruction'):return e['_state']+'; '+e.get('_reason','')
    return ''


def brush_state(e,model,things):
    state=base.brush_state(e,model,things)
    if e.get('_state')=='default' and state['hidden']=='START_INVIS':state['hidden']=''
    if e['classname']=='func_constructible':state['hidden']=''  # bit 1 means START_BUILT here
    state['hidden']=filtered(e,'et') or state['hidden']
    return state


def viewpoints(things,collision,section):
    candidates=[e for e in things if e['classname']=='team_ctf_redspawn' and int(number(e,'spawnflags'))&2 and not filtered(e,'et')]
    if not candidates:candidates=[e for e in things if e['classname']=='team_ctf_bluespawn' and int(number(e,'spawnflags'))&2 and not filtered(e,'et')]
    team=0 if candidates and candidates[0]['classname']=='team_ctf_redspawn' else 1
    objectives=[e for e in things if e['classname']=='team_wolf_objective' and not filtered(e,'et')]
    objective=next((e for e in objectives if e.get('description')==things[0].get('_autospawn',{}).get(team)),None)
    if objective is None:objective=next((e for e in objectives if int(number(e,'spawnflags'))&(1<<team)),objectives[0] if objectives else None)
    if objective:
        origin=vector(objective.get('origin'));candidates.sort(key=lambda e:sum((a-b)**2 for a,b in zip(vector(e.get('origin')),origin)))
    views=[]
    for e in candidates:
        pos=vector(e.get('origin'));pos[2]+=9;pos,reason=collision.drop(pos,base.PLAYER_MINS,base.PLAYER_MAXS);pos[2]+=base.EYE_HEIGHT
        a=base.angles(e)
        target=next((t for t in things if e.get('target') and t.get('targetname')==e['target']),None)
        if target:a=direction_angles(np.array(vector(target.get('origin')))-vector(e.get('origin')))
        views.append(dict(origin=pos,pitch=a[0],yaw=a[1],floor=reason,classname=e['classname'],entity=e['_entity']))
    return views


def lightmap_atlas(game,name,lumps,output):
    indices=sorted({s[7] for s in struct.iter_unpack('<12i12f2i',lumps[13]) if s[2] in (1,2,3) and s[7]>=0})
    if not indices:return None,[]
    if max(indices)>4095:raise ValueError('Lightmap index exceeds preview bound')
    images={};missing=[]
    for i in indices:
        if (i+1)*49152<=len(lumps[14]):image=Image.frombytes('RGB',(128,128),lumps[14][i*49152:(i+1)*49152])
        else:
            path=f'maps/{name}/lm_{i:04d}.tga'
            if path not in game.assets:missing.append(path);continue
            with Image.open(io.BytesIO(game.read(path))) as source:
                if max(source.size)>4096:raise ValueError('External lightmap exceeds size bound')
                image=source.convert('RGB')
        rgb=np.asarray(image,dtype=np.float32)*game.lighting['lightScale'];rgb*=np.minimum(1,255/np.maximum(1,rgb.max(axis=2)))[:,:,None]
        images[i]=Image.fromarray(rgb.astype('uint8'),'RGB')
    if not images:return None,missing
    size=max(max(im.size) for im in images.values());columns=math.ceil(math.sqrt(max(indices)+1));rows=math.ceil((max(indices)+1)/columns)
    if columns*rows*size*size>64000000:raise ValueError('External lightmap atlas exceeds preview bound')
    atlas=Image.new('RGB',(columns*size,rows*size),'white')
    for i,im in images.items():atlas.paste(im.resize((size,size),Image.Resampling.BILINEAR),(i%columns*size,i//columns*size))
    data=io.BytesIO();atlas.save(data,'PNG');raw=data.getvalue();file=hashlib.sha256(raw).hexdigest()[:24]+'.png';(output/'textures'/file).write_bytes(raw)
    return dict(file=file,size=size,columns=columns,rows=rows,count=max(indices)+1,valid=sorted(images)),missing


def add_components(game,scene,output,textures,model_textures):
    """CG_Cabinet and CG_AttachBitsToTank's visible model/tag chains."""
    def add(path,holder,chain,serial,label):
        transform=np.eye(4)
        for tag in chain:transform=transform@tag_matrix(tag)
        tag=tuple(transform[:3,3])+tuple(transform[:3,:3].T.flat)
        key=hashlib.sha256(json.dumps([path,tag]).encode()).hexdigest()[:24]
        if key not in scene['itemModels']:
            desc,missing=base.placed_model(game,path,'',0,output,textures,model_textures,tag)
            scene['itemModels'][key]=desc;scene['unresolved']+=missing;scene.setdefault('limits',[]).extend(desc.get('limitations',[]))
        scene['placed'].append(dict(models=[key],origin=holder['origin'],angles=holder['angles'],scale=holder['scale'],spin=False,entity=serial,part=label))
    def tag(path,name):
        tags=model_tags(game,path)
        result=next((v for n,v in tags.items() if n.lower()==name.lower()),None)
        if result is None:raise ValueError('Missing tag '+name+' in '+path)
        return result
    holders={p['entity']:p for p in scene['placed']}
    for i,e in enumerate(game.prepared):
        if i not in holders:continue
        try:
            if e['classname'].startswith('misc_cabinet_'):
                kind='health' if e['classname'].endswith('health') else 'ammo';parent=next((p for p in game.prepared if e.get('targetname') and p.get('target')==e['targetname'] and p['classname'] in ('trigger_heal','trigger_ammo') and not p.get('_removed')),None)
                if parent:
                    path=PROP_MODELS[e['classname']]
                    for n in range(1,7):add('models/multiplayer/supplies/'+('healthbox_wm.md3' if kind=='health' else 'ammobox_wm.md3'),holders[i],[tag(path,('tag_Medikit_' if kind=='health' else 'tag_ammo')+f'{n:02}')],i,'cabinet supply')
            if e['classname']=='script_mover' and int(number(e,'spawnflags'))&128:
                parent=next((j for j,p in enumerate(game.prepared) if e.get('tagent') and p.get('targetname')==e['tagent']),i)
                if parent not in holders:continue
                p=game.prepared[parent];path=p.get('model2') or p.get('model');chain=[model_tags(game,path,int(number(p,'frame')))['tag_player']]
                nest='models/mapobjects/tanks_sd/mg42nestbase.md3';add(nest,holders[parent],chain,i,'mounted gun base')
                chain=chain+[tag(nest,'tag_mg42nest')];nest='models/mapobjects/tanks_sd/mg42nest.md3';add(nest,holders[parent],chain,i,'mounted gun nest')
                chain=chain+[tag(nest,'tag_mg42')];add('models/multiplayer/browning/thirdperson.md3' if e.get('gun')=='browning' else 'models/mapobjects/tanks_sd/mg42.md3',holders[parent],chain,i,'mounted gun')
        except (ValueError,KeyError,OSError,zipfile.BadZipFile) as exc:
            scene['unresolved'].append(f'entity {i} component: {exc}');scene['entityResults'].append(dict(entity=i,classname=e['classname'],status='failed',reason='model component: '+str(exc)))


def import_maps(install,output,replace=False):
    output=Path(output)
    for folder in ('maps','models','bsp','textures'):(output/folder).mkdir(parents=True,exist_ok=True)
    game=Game(install);textures=base.Textures(output/'textures',game);model_textures=ModelTextures(game,output/'textures')
    report=dict(imported=[],skipped=[],failed={},maps=[],unresolved={},search_order=game.search_order,lighting=game.lighting);index=[]
    for path in sorted(n for n in game.assets if re.fullmatch(r'maps/[^/]+\.bsp',n)):
        name=Path(path).stem;ident=qmap.map_id(name);source=game.assets[path]['source'];dest=output/'maps'/ident
        try:
            if (dest/'scene.json').is_file() and (dest/'scene-built.json').is_file() and not replace:
                scene=json.loads((dest/'scene.json').read_text());report['skipped'].append(ident)
            else:
                raw=game.read(path);lumps=qmap.read_bsp(raw,version=47);atlas,missing=lightmap_atlas(game,name,lumps,output)
                variants=[]
                for built in (False,True):
                    game.built=built
                    scene=base.import_map(game,name,ident,raw,source,output,textures,model_textures,rules=sys.modules[__name__])
                    add_components(game,scene,output,textures,model_textures)
                    scene.update(game='et',lightmapAtlas=atlas,construction=game.state_report,mode='built' if built else 'spawn')
                    scene['unresolved']+=['external lightmap missing; vertex lighting fallback: '+p for p in missing]
                    scene['limits']=[n for n in scene['limits'] if n not in ('AI characters','script actions and moving/animated model poses after spawn')]
                    scene['limits']+=['spawn scripts interpreted within a bounded 2-second initialization window; ongoing motion and later gameplay frozen','first eligible Axis spawn nearest the scripted default objective; Legacy selection may differ from retail','constructibles: map-start state by default; finished mode applies final construction stages, with Axis chosen for unowned mutually exclusive builds']
                    scene['limits']=sorted(set(scene['limits']))
                    variants.append(scene)
                scene=variants[0];rtcw.q3.json_write(dest/'scene-built.json',variants[1]);rtcw.q3.json_write(dest/'scene.json',scene)
                report['imported'].append(ident)
            index.append(dict(id=ident,name=name,longname=scene['longname'],group='Enemy Territory',source=source,unresolved=len(scene['unresolved'])))
            built_scene=json.loads((dest/'scene-built.json').read_text())
            report['maps'].append(dict(map=name,source=source,status='imported' if ident in report['imported'] else 'skipped',entityCounts={mode:dict(Counter(r['classname']+': '+r['status'] for r in result['entityResults'])) for mode,result in [('spawn',scene),('built',built_scene)]},construction=built_scene['construction'],entityResults=scene['entityResults'],builtEntityResults=built_scene['entityResults'],bspNotes=scene['bspNotes']))
            if scene['unresolved'] or built_scene['unresolved']:report['unresolved'][name]=sorted(set(scene['unresolved']+built_scene['unresolved']))
        except (ValueError,OSError,struct.error,KeyError,zipfile.BadZipFile) as exc:
            report['failed'][name]=str(exc);report['maps'].append(dict(map=name,source=source,status='failed',reason=str(exc)))
    report['issues']=game.issues
    report['defaultGametype']=4;report['defaultTeam']='Axis'
    rtcw.q3.json_write(output/'index.json',index);rtcw.q3.json_write(output/'import-report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install',required=True);parser.add_argument('--output',type=Path,default=qmap.LOCAL_DATA/'et-maps');parser.add_argument('--replace',action='store_true')
    args=parser.parse_args();print(json.dumps(import_maps(args.install,args.output,args.replace),indent=2))

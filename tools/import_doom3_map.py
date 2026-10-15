"""Doom 3/RoE PROC maps, live-light inputs and authored start-state entities.

python -m tools.import_doom3_map --install "C:/Games/Doom 3" --data-root local-data
--map base:game/admin may be repeated for bounded imports. --game quake4 uses
q4base:game/airdefense1. --models-root points to an existing SKINNER_DATA_DIR.
Quake 4 textures join that root's quake4/textures (or data-root if omitted).
"""
import argparse
from collections import Counter
import itertools
import json
import math
from pathlib import Path
import re
import time

import numpy as np

try:
    from tools.doom3_assets import Install,Declarations,Textures,Materials,path_name
    from tools.doom3_map_materials import compile_material,tables
    from tools.doom3_map_sounds import Sounds
    from tools.doom3_particles import particle_decls,fx_decls,fx_timing,particle_reference,extent as particle_extent
    from tools.doom3_gui import GuiImages,gui_texture
    from tools.doom3_movers import mover_records,swept_bounds
    from tools.quake4_bse import effect_decls,extent as bse_extent
    from tools.import_doom3 import load_parts,idle_animation,FORMATS
    from tools.model_data import write_model_data,load_model_data
    from tools.texture_storage import game_texture_dir
    from tools.texture_workshop import write_json_atomic
    from tools.local_data import LOCAL_DATA
except ImportError:
    from doom3_assets import Install,Declarations,Textures,Materials,path_name
    from doom3_map_materials import compile_material,tables
    from doom3_map_sounds import Sounds
    from doom3_particles import particle_decls,fx_decls,fx_timing,particle_reference,extent as particle_extent
    from doom3_gui import GuiImages,gui_texture
    from doom3_movers import mover_records,swept_bounds
    from quake4_bse import effect_decls,extent as bse_extent
    from import_doom3 import load_parts,idle_animation,FORMATS
    from model_data import write_model_data,load_model_data
    from texture_storage import game_texture_dir
    from texture_workshop import write_json_atomic
    from local_data import LOCAL_DATA


class ProcTokens:
    def __init__(self,data):
        self.pending=None
        self.tokens=iter(m[0] for m in re.finditer(r'/\*[\s\S]*?\*/|//[^\r\n]*|"[^"\r\n]*"|[^\s{}()]+|[{}()]',data.decode('latin1')) if not m[0].startswith(('/*','//')))
    def take(self):
        if self.pending is not None:
            result=self.pending;self.pending=None;return result
        try:return next(self.tokens).strip('"')
        except StopIteration:raise ValueError('Truncated PROC') from None
    def peek(self):
        if self.pending is None:
            self.pending=next(self.tokens,None)
            if self.pending is not None:self.pending=self.pending.strip('"')
        return self.pending
    def vertex(self):
        self.expect('(');v=[]
        while self.peek()!=')':v.append(float(self.take()))
        self.expect(')')
        if len(v) not in (8,12) or not np.isfinite(v).all():raise ValueError('Invalid PROC vertex')
        return v[:8],np.array(v[8:] if len(v)==12 else [255]*4)/255
    def expect(self,t):
        actual=self.take()
        if actual!=t:raise ValueError('Expected PROC '+t+', got '+actual)
    def integer(self):return int(self.take())
    def count(self):
        n=self.integer()
        if not 0<=n<=10000000:raise ValueError('Invalid PROC count')
        return n
    def vector(self,n):
        self.expect('(');v=[float(self.take()) for _ in range(n)];self.expect(')')
        if not np.isfinite(v).all():raise ValueError('Nonfinite PROC vector')
        return v


def read_proc(data,policy=None):
    t=ProcTokens(data);header=None;sky={}
    if policy:
        t.expect('PROC');t.expect('4');header=t.integer()
    else:t.expect('mapProcFile003')
    models={};portals=[];nodes=[];areas=0;shadows=0
    while t.peek() is not None:
        kind=t.take()
        t.expect('{')
        if kind=='model':
            name=path_name(t.take());parts=[]
            count=t.count()
            if policy and re.fullmatch(r'_area\d+',name):sky[name]=bool(t.integer())
            for _ in range(count):
                t.expect('{');shader=t.take();nv,ni=t.count(),t.count()
                if policy:
                    rows=[t.vertex() for _ in range(nv)]
                    vertices=np.array([v for v,c in rows],dtype=np.float32).reshape(-1,8)
                    colors=np.array([c for v,c in rows],dtype=np.float32).reshape(-1,4)
                else:
                    vertices=np.array([t.vector(8) for _ in range(nv)],dtype=np.float32).reshape(-1,8)
                    colors=np.ones((nv,4),dtype=np.float32)
                indices=np.array([t.integer() for _ in range(ni)],dtype=np.int64)
                if ni%3 or (ni and (indices.min()<0 or indices.max()>=nv)):raise ValueError('Invalid PROC indices')
                t.expect('}')
                # Doom CW -> Three CCW, preserving normals and upper-left STs.
                indices=indices.reshape(-1,3)[:,[0,2,1]].ravel()
                parts.append(dict(shader=shader,vertices=vertices[:,:3],uvs=vertices[:,3:5],normals=vertices[:,5:8],indices=indices,
                                  colors=colors))
            models[name]=parts
        elif kind=='interAreaPortals':
            areas=t.count()
            for _ in range(t.count()):
                count,a,b=t.count(),t.integer(),t.integer()
                if min(a,b)<0 or max(a,b)>=areas:raise ValueError('Invalid portal areas')
                portal=dict(areas=[a,b],points=[t.vector(3) for _ in range(count)])
                if policy and t.peek()=='(':
                    t.expect('(');portal['fade']=[t.take(),float(t.take()),float(t.take())];t.expect(')')
                portals.append(portal)
        elif kind=='nodes':
            for _ in range(t.count()):nodes.append(dict(plane=t.vector(4),children=[t.integer(),t.integer()]))
        elif kind=='shadowModel':
            shadows+=1;depth=1
            while depth:
                value=t.take();depth+=(value=='{')-(value=='}')
            continue
        else:raise ValueError('Unsupported PROC block: '+kind)
        t.expect('}')
    for i,node in enumerate(nodes):
        if any(c>0 and (c>=len(nodes) or c==i) for c in node['children']):raise ValueError('Invalid PROC node child')
    return dict(models=models,portals=portals,nodes=nodes,areas=areas or 1,shadowModels=shadows,header=header,areaSky=sky)


def read_entities(data,policy=None):
    """Only quoted pairs at entity depth; brush/patch geometry never enters output."""
    text=data.decode('latin1')
    version=3 if policy else 2
    if not re.match(r'\s*Version\s+'+str(version)+r'\b',text):raise ValueError('Expected MAP Version '+str(version))
    depth=0;result=[];entity=None;key=None
    for m in re.finditer(r'//[^\r\n]*|/\*[\s\S]*?\*/|"((?:\\.|[^"\\])*)"|[{}]',text):
        token=m[0]
        if token.startswith(('//','/*')):continue
        if token=='{':
            depth+=1
            if depth==1:entity={};key=None
        elif token=='}':
            if depth==1:
                if key is not None:raise ValueError('Unpaired MAP key')
                result.append(entity)
            depth-=1
            if depth<0:raise ValueError('Unexpected MAP brace')
        elif depth==1:
            value=re.sub(r'\\(["\\])',r'\1',m[1])
            if key is None:key=value.lower()
            else:entity[key]=value;key=None
    if depth:raise ValueError('Unclosed MAP entity')
    return result


def vector(value,default=(0,0,0)):
    values=list(map(float,value.split())) if isinstance(value,str) and value.strip() else list(default)
    if len(values)!=len(default) or not np.isfinite(values).all():raise ValueError('Invalid entity vector: '+str(value))
    return np.array(values,dtype=float)


def rotation(entity,key='rotation'):
    if entity.get(key):return vector(entity[key],np.eye(3).ravel()).reshape(3,3).T
    angle=math.radians(float(entity.get('angle',0)))
    return np.array([[math.cos(angle),-math.sin(angle),0],[math.sin(angle),math.cos(angle),0],[0,0,1]])


def entity_transform(entity,parts):
    """Authored world transform, including idDoor's angle-as-movedir/start_open."""
    origin=vector(entity.get('origin'));axis=rotation(entity)
    if entity.get('classname')=='func_door' or entity.get('spawnclass')=='idDoor':
        direction=float(entity.get('movedir',entity.get('angle',0)))
        if 'movedir' not in entity:axis=np.eye(3)
        if entity.get('start_open')=='1' and parts:
            movedir=np.array([0,0,1 if direction==-1 else -1]) if direction in (-1,-2) else np.array([math.cos(math.radians(direction)),math.sin(math.radians(direction)),0])
            points=np.concatenate([np.asarray(p['vertices']) for p in parts]) @ axis.T
            distance=np.abs(movedir)@(points.max(axis=0)-points.min(axis=0))-float(entity.get('lip',8))
            origin+=distance*movedir
    return origin,axis


def area_for_point(point,nodes):
    if not nodes:return 0
    index=0
    for _ in range(len(nodes)+1):
        node=nodes[index];p=node['plane'];child=node['children'][0 if np.dot(p[:3],point)+p[3]>=0 else 1]
        if child<=0:return -1-child
        index=child
    raise ValueError('Cyclic PROC area tree')


def bounds_areas(bounds,nodes):
    if not nodes:return [0]
    lo,hi=np.asarray(bounds);center=(lo+hi)/2;extent=(hi-lo)/2;result=set();todo=[0];seen=set()
    while todo:
        index=todo.pop()
        if index in seen:continue
        seen.add(index);node=nodes[index];p=np.array(node['plane']);d=p[:3]@center+p[3];r=np.abs(p[:3])@extent
        for side in range(2):
            if (side==0 and d+r<0) or (side==1 and d-r>0):continue
            child=node['children'][side]
            if child<0:result.add(-1-child)
            elif child>0:todo.append(child)
    return sorted(result)


def parms(entity):
    color=vector(entity.get('_color'),(1,1,1))
    result=[*color,1,0,0,0,0,0,0,0,0]
    for i in range(12):
        if 'shaderparm'+str(i) in entity:result[i]=float(entity['shaderparm'+str(i)])
    return result


def light_projection(entity):
    """R_SetLightProject/R_DeriveLightData. Plane rows are S,T,Q,falloff."""
    origin=vector(entity.get('light_origin',entity.get('origin')))
    axis=rotation(entity,'light_rotation' if entity.get('light_rotation') else 'rotation')
    projected='light_target' in entity
    if projected:
        if not all(k in entity for k in ('light_right','light_up')):raise ValueError('Projected light lacks right/up')
        target=vector(entity['light_target']);right=vector(entity['light_right']);up=vector(entity['light_up'])
        rl,ul=np.linalg.norm(right),np.linalg.norm(up)
        if min(rl,ul)<1e-8:raise ValueError('Degenerate light projection')
        normal=np.cross(up,right);normal/=np.linalg.norm(normal);distance=target@normal
        if distance<0:normal=-normal;distance=-distance
        if distance<1e-8:raise ValueError('Zero light projection distance')
        right*=.5*distance/(rl*rl);up*=-.5*distance/(ul*ul)
        rows=np.zeros((4,4));rows[2,:3]=normal
        rows[0,:3]=right+(.5-target@right/distance)*normal
        rows[1,:3]=up+(.5-target@up/distance)*normal
        start=vector(entity.get('light_start'));end=vector(entity.get('light_end'),target)
        delta=end-start;length=np.linalg.norm(delta)
        if length<1e-8:raise ValueError('Degenerate projected falloff')
        rows[3,:3]=delta/(length*length);rows[3,3]=-start@rows[3,:3]
    else:
        radius=vector(entity.get('light_radius'),(float(entity.get('light',300)),)*3)
        if np.any(radius<=0):raise ValueError('Nonpositive light radius')
        rows=np.zeros((4,4));rows[0,0]=.5/radius[0];rows[1,1]=.5/radius[1];rows[3,2]=.5/radius[2]
        rows[[0,1,3],3]=.5;rows[2,3]=1
    world=np.eye(4);world[:3,:3]=axis;world[:3,3]=origin
    rows=rows @ np.linalg.inv(world)
    # Positive halfspaces inside the six-plane volume.
    planes=np.array([rows[0],rows[1],rows[2]-rows[0],rows[2]-rows[1],rows[3],np.array([0,0,0,1])-rows[3]])
    corners=[]
    for ids in itertools.combinations(range(6),3):
        p=planes[list(ids)]
        # Projection planes shrink with light radius. Test angular degeneracy,
        # not their absolute determinant (which rejected large outdoor lights).
        scale=np.prod(np.linalg.norm(p[:,:3],axis=1))
        if not scale or abs(np.linalg.det(p[:,:3]))<1e-12*scale:continue
        point=np.linalg.solve(p[:,:3],-p[:,3])
        if np.all(planes[:,:3]@point+planes[:,3]>=-1e-4):corners.append(point)
    if not corners:raise ValueError('Empty light volume')
    center=vector(entity.get('light_center'))
    light_origin=origin+axis@center
    if entity.get('parallel')=='1':
        light_origin=origin+(center/np.linalg.norm(center) if np.linalg.norm(center) else np.array([0,0,1]))*100000
    return dict(projected=projected,projection=rows.tolist(),planes=planes.tolist(),origin=light_origin.tolist(),
                bounds=[np.min(corners,axis=0).tolist(),np.max(corners,axis=0).tolist()])


def volume_faces(planes):
    """Triangles of the convex light volume (positive half-spaces inside), wound to face outward.

    RB_FogPass draws the light frustum's back faces so fog also covers what lies beyond its far walls."""
    planes=np.asarray(planes,dtype=float);corners=[]
    for ids in itertools.combinations(range(len(planes)),3):
        p=planes[list(ids)]
        if abs(np.linalg.det(p[:,:3]))<1e-12*np.prod(np.linalg.norm(p[:,:3],axis=1)):continue
        point=np.linalg.solve(p[:,:3],-p[:,3])
        if np.all(planes[:,:3]@point+planes[:,3]>=-1e-4*np.linalg.norm(planes[:,:3],axis=1)) and not any(np.allclose(point,c) for c in corners):corners.append(point)
    triangles=[]
    for plane in planes:
        length=np.linalg.norm(plane[:3]);n=plane[:3]/length
        on=[c for c in corners if abs(n@c+plane[3]/length)<1e-3*max(1,np.abs(c).max())]
        if len(on)<3:continue
        mid=np.mean(on,axis=0);u=on[0]-mid;u/=np.linalg.norm(u);v=np.cross(n,u)
        # n points inside, so clockwise about n (counter-clockwise from outside) faces outward.
        ring=sorted(on,key=lambda c:-np.arctan2((c-mid)@v,(c-mid)@u))
        for i in range(1,len(ring)-1):triangles+=[*ring[0],*ring[i],*ring[i+1]]
    return [round(float(x),3) for x in triangles]


def light_intersects(bounds,light):
    lo,hi=np.asarray(bounds);planes=np.asarray(light['planes']);p=(lo+hi)/2;extent=(hi-lo)/2
    return bool(np.all(planes[:,:3]@p+planes[:,3]+np.abs(planes[:,:3])@extent>=-1e-3))


class ModelCache:
    def __init__(self,root,policy=None):
        self.policy=policy
        self.root=Path(root)/(policy.game if policy else 'doom3');self.entries={};self.loaded={}
        path=self.root/'catalog.json'
        if path.is_file():self.entries={r['source']:r for r in json.loads(path.read_text(encoding='utf8')) if r['status']=='ready'}
    def get(self,campaign,entity,reference,skin,animation=None):
        for key in (campaign+':'+entity.get('classname',''),campaign+':'+reference):
            entry=self.entries.get(key)
            if not entry:continue
            name=entry['model_name']
            if name not in self.loaded:self.loaded[name]=load_model_data(self.root/'model_json'/(name+'.json'))
            model=self.loaded[name];meta=model['metadata']
            if meta.get('skin','')!=skin or meta.get('animation')!=animation:continue
            spawnargs=meta.get('spawnargs',{})
            keys={k for e in (spawnargs,entity) for k in e if k in ('def_head','head_joint','joint_head','override_head','def_persona') or k.startswith(('copy_joint','hidesurface','showsurface','def_attach','def_position'))}
            if any(spawnargs.get(k)!=entity.get(k) for k in keys):continue
            original=meta.get('spawnargs',{}).get('model',meta['mesh'])
            if path_name(original)!=path_name(reference):continue
            return model
        return None


def model_parts(model):
    vertices=np.array(model['vertices']).reshape(-1,3)[:,[2,0,1]]
    normals=np.array(model['normals']).reshape(-1,3)[:,[2,0,1]]
    uvs=np.array(model['uvs']).reshape(-1,2);colors=np.array(model.get('colors',[1]*len(model['vertices']))).reshape(-1,3)
    indices=np.array(model['indices']);result=[]
    for group in model['groups']:
        ids=indices[group['start']:group['start']+group['count']];unique,inverse=np.unique(ids,return_inverse=True)
        result.append(dict(shader=model['material_names'][group['materialIndex']],vertices=vertices[unique],normals=normals[unique],
                           uvs=uvs[unique],colors=colors[unique],indices=inverse))
    return result


def strings(files):
    """The language table, every strings/english*.lang like the engine: '#str_04352' -> 'Administration Lobby'."""
    return {k:v for n in sorted(files) if re.fullmatch(r'strings/english[^/]*\.lang',n)
            for k,v in re.findall(r'"(#str_\d+)"\s+"([^"]*)"',files[n].read().decode('latin-1'))}


def startup_shader_parms(source,entry='main'):
    """Literal render parameters before the first yield/control flow, not a script VM.

    Follow synchronous zero-argument helpers in this file only. Event handlers,
    threads, loops and delayed effects must not leak into the startup snapshot.
    """
    source=re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*[\s\S]*?\*/|//[^\n]*',
                  lambda m:'""' if m[0][0] in '\"\'' else '',source)
    functions={}
    for match in re.finditer(r'\bvoid\s+(\w+)\s*\(\s*\)\s*\{',source):
        at=match.end();depth=1;end=at
        while end<len(source) and depth:
            depth+=(source[end]=='{')-(source[end]=='}');end+=1
        if not depth:functions[match[1]]=source[at:end-1]
    globals_=[0.0]*12;entities={};active=set()
    number=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
    def run(name):
        if name not in functions or name in active or len(active)>32:return False
        active.add(name)
        try:
            for statement in functions[name].split(';'):
                s=statement.strip()
                if not s:continue
                assignment=re.fullmatch(r'(sys|\$\w+)\.setShaderParm\s*\(\s*(\d+)\s*,\s*('+number+r')\s*\)',s)
                color=re.fullmatch(r'\$(\w+)\.setColor\s*\(\s*('+number+r')\s*,\s*('+number+r')\s*,\s*('+number+r')\s*\)',s)
                call=re.fullmatch(r'(\w+)\s*\(\s*\)',s)
                if assignment:
                    target,index,value=assignment.groups();index=int(index)
                    if index<12:
                        if target=='sys':globals_[index]=float(value)
                        else:entities.setdefault(target[1:],{})['shaderparm'+str(index)]=value
                elif color:entities.setdefault(color[1],{})['_color']=' '.join(color.groups()[1:])
                elif call:
                    if not run(call[1]):return False
                # Non-render entity events do not change shader parameters.
                elif re.fullmatch(r'\$\w+\.(?!setShaderParm\b|setColor\b)\w+\s*\([^;{}]*\)',s):continue
                else:return False
            return True
        finally:active.remove(name)
    run(entry.rsplit('::',1)[-1])
    return globals_,entities


def import_scene(name,campaign,files,defs,material_cache,model_cache,directory,policy=None):
    started=time.monotonic();proc=read_proc(files['maps/'+name+'.proc'].read(),policy)
    entities=read_entities(files['maps/'+name+'.map'].read(),policy);source_entities=entities
    entry=entities[0].get('call','main');script='maps/'+name+'.script'
    if '::' in entry:script='script/'+entry.split('::')[0]+'.script'
    global_parms,startup_entities=startup_shader_parms(files[script].read().decode('latin1'),entry) if script in files else ([0.0]*12,{})
    resolved=[];skipped=[];lights=[];skipped_lights=[];used_materials=set();parts_cache={};emitters=[];used_particles=set();used_effects=set()
    geometry=dict(vertices=[],normals=[],uvs=[],colors=[],indices=[]);surfaces=[];entity_records=[]
    def material(name):
        name=policy.material_name(name) if policy else path_name(name).removesuffix('.tga')
        if name not in material_cache['records']:
            material_cache['records'][name]=compile_material(name,defs,material_cache['materials'],material_cache['textures'])
        used_materials.add(name);return material_cache['records'][name]
    def gui_material(shader,entity):
        # guiSurf entity/entity2/entity3 read the entity's gui/gui2/gui3; anything else names the .gui itself.
        # The rasterised start state is one more fullbright stage, drawn at sort gui like R_RenderGuiSurf.
        which=shader['guiSurf'];path=entity.get({'entity':'gui','entity2':'gui2','entity3':'gui3'}.get(which,''),which if which.endswith('.gui') else '')
        if not path:return shader
        name='gui:'+path_name(path)
        if name not in material_cache['records']:
            guis=material_cache['guis']
            if path_name(path) not in guis:
                guis[path_name(path)]=gui_texture(path,files,material_cache['gui_images'],material_cache['strings'],material_cache['textures'].output)
            image=guis[path_name(path)];record=dict(shader,name=name,translucent=True,sort=-2,approximations=sorted(set(shader['approximations'])-{'guisurf'}|{'gui start state only'}))
            if image['status']=='ready':
                record['stages']=shader['stages']+[dict(role='ambient',color=[1,1,1,1],condition=1,uv=[],vertexColor=0,alphaTest=0,clamp=True,
                                                         blend=['gl_one','gl_one_minus_src_alpha'],image=image)]
            else:record['approximations']=sorted(set(record['approximations'])|{'missing gui: '+path_name(path)})
            material_cache['records'][name]=record
        used_materials.add(name);return material_cache['records'][name]
    for entity in entities:
        classname=path_name(entity.get('classname',''));base={}
        if classname in defs.entities:
            try:base=defs.inherit(defs.entities,classname) if policy else defs.entity(classname)
            except ValueError as exc:skipped.append(dict(name=entity.get('name',classname),reason=str(exc)))
        resolved.append(dict(dict(base,**entity),**startup_entities.get(entity.get('name'),{})))
    map_notes={}
    if policy:
        from tools.quake4_map_policy import prepare_entities, attachment_transforms
        resolved,map_notes=prepare_entities(resolved,defs,name)
        transforms,attachment_notes=attachment_transforms(resolved,defs,files,proc['models'])
        map_notes['attachments']=attachment_notes
    def moved_frame(entity):
        origin=vector(entity.get('origin'));axis=rotation(entity)
        if policy and entity.get('name') in transforms:
            delta=transforms[entity['name']];origin=(delta @ np.r_[origin,1])[:3];axis=delta[:3,:3] @ axis
        return origin,axis
    def local_min_z(entity):
        parts=proc['models'].get(path_name(entity.get('model','')))
        return min(float(np.asarray(p['vertices'])[:,2].min()) for p in parts) if parts else None
    movers,mover_of=mover_records(resolved,lambda e:moved_frame(e)[0].tolist(),lambda e:moved_frame(e)[1],local_min_z)
    def aabb_planes(bounds):
        lo,hi=bounds;return [[1,0,0,-lo[0]],[0,1,0,-lo[1]],[0,0,1,-lo[2]],[-1,0,0,hi[0]],[0,-1,0,hi[1]],[0,0,-1,hi[2]]]
    for index,entity in enumerate(resolved):
        if entity.get('classname')!='light' and entity.get('spawnclass')!='idLight':continue
        label=entity.get('name','light'+str(index))
        try:
            light_entity=entity
            if policy and entity.get('name') in transforms:
                from tools.quake4_map_policy import transformed_light
                light_entity=transformed_light(entity,transforms[entity['name']])
            light=light_projection(light_entity)
            if policy and float(entity.get('detaillevel',10))<=0:
                skipped_lights.append(dict(name=label,reason='detailLevel <= r_lightDetailLevel 0'));continue
            default='lights/squarelight1' if policy else 'lights/defaultprojectedlight' if light['projected'] else 'lights/defaultpointlight'
            shader=material(entity.get('texture') or default)
            # RB_FogAllLights: fog and blend lights draw after the shader passes instead of interacting.
            light.update(fog=shader['fogLight'],blend=shader['blendLight'])
            if shader['fogLight']:light['volume']=volume_faces(light['planes'])
            falloff_default='lights/defaultprojectedlight' if light['projected'] else 'lights/defaultpointlight'
            falloff=shader['falloff'] or material(falloff_default if policy else default)['falloff']
            sound=material_cache['sounds'].resolve(entity) if shader['soundReactive'] else None
            light.update(name=label,shader=shader['name'],falloff=falloff,parms=parms(entity),ambient=shader['ambientLight'],spectrum=shader['spectrum'],sound=sound,
                         areas=bounds_areas(light['bounds'],proc['nodes']),authoredOff=entity.get('start_off')=='1',noSpecular=entity.get('nospecular')=='1',
                         noDiffuse=policy is not None and entity.get('nodiffuse')=='1',globalLight=policy is not None and entity.get('globallight')=='1')
            # Start-off is an authored light state; unlike hidden monsters, don't switch it on.
            if light['authoredOff']:light['parms'][:3]=[0,0,0]
            if entity.get('name') in mover_of:
                # A light riding a mover lights whatever its volume sweeps over.
                light['mover']=mover_of[entity['name']];swept=swept_bounds(light['bounds'],movers,light['mover'])
                light.update(matchPlanes=aabb_planes(swept),areas=bounds_areas(swept,proc['nodes']))
            lights.append(light)
        except (ValueError,KeyError) as exc:skipped_lights.append(dict(name=label,reason=str(exc)))
    light_planes=np.array([l.get('matchPlanes',l['planes']) for l in lights])
    light_by_area={}
    for i,light in enumerate(lights):
        for area in (range(proc['areas']) if light['globalLight'] else light['areas']):light_by_area.setdefault(area,set()).add(i)
    def add_parts(parts,entity,area=None):
        origin,axis=entity_transform(entity,parts);count=0
        if policy and entity.get('name') in transforms:
            delta=transforms[entity['name']];origin=(delta @ np.r_[origin,1])[:3];axis=delta[:3,:3] @ axis
        for part in parts:
            shader=material(entity.get('shader') or defs.remap(part['shader'],entity.get('skin','')))
            if shader.get('guiSurf'):shader=gui_material(shader,entity)
            if policy and 'particle deform skipped' in shader['approximations']:
                skipped.append(dict(name=entity.get('name','worldspawn'),material=shader['name'],reason='particle surface'))
            if not shader['visible'] or shader['name'].startswith(('textures/common/trigger','textures/common/clip','textures/common/nodraw','textures/common/collision')):continue
            positions=np.asarray(part['vertices']) @ axis.T+origin
            if not len(positions) or not len(part['indices']):continue
            normals=np.asarray(part['normals']) @ np.linalg.inv(axis)
            normals/=np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-12)
            bounds=[positions.min(axis=0).tolist(),positions.max(axis=0).tolist()]
            mover=mover_of.get(entity.get('name'))
            reach=swept_bounds(bounds,movers,mover) if mover is not None else bounds
            areas=[area] if area is not None else bounds_areas(reach,proc['nodes'])
            candidates=sorted(set().union(*(light_by_area.get(a,set()) for a in areas))) if areas else list(range(len(lights)))
            candidates=[i for i in candidates if lights[i]['spectrum']==shader['spectrum']]
            matched=[]
            if candidates:
                p=light_planes[candidates];center=(np.asarray(reach[0])+reach[1])/2;extent=(np.asarray(reach[1])-reach[0])/2
                inside=np.all(p[:,:,:3]@center+p[:,:,3]+np.abs(p[:,:,:3])@extent>=-1e-3,axis=1)
                matched=np.array(candidates)[inside].tolist()
            start=len(geometry['indices']);offset=len(geometry['vertices'])//3
            geometry['vertices'].extend(positions.ravel().tolist());geometry['normals'].extend(normals.ravel().tolist())
            geometry['uvs'].extend(np.asarray(part['uvs']).ravel().tolist())
            colors=np.asarray(part['colors'])
            if policy and colors.shape[1]==3:colors=np.column_stack([colors,np.ones(len(colors))])
            geometry['colors'].extend(colors[:,:4 if policy else 3].ravel().tolist())
            geometry['indices'].extend((np.asarray(part['indices'])+offset).tolist())
            surfaces.append(dict(start=start,count=len(geometry['indices'])-start,material=shader['name'],bounds=bounds,areas=areas,
                                 sound=material_cache['sounds'].resolve(entity) if shader['soundReactive'] else None,
                                 lights=matched,entity=entity.get('name','worldspawn'),parms=parms(entity)))
            if any(s.get('cube') for s in shader['stages']):surfaces[-1]['cubeAxis']=np.linalg.inv(axis).ravel().tolist()
            if mover is not None:surfaces[-1].update(mover=mover,moverBounds=reach)
            count+=1
        return count
    for model,parts in proc['models'].items():
        if re.fullmatch(r'_area\d+',model):add_parts(parts,{},int(model[5:]))
    for entity in resolved[1:]:
        label=entity.get('name',entity.get('classname',''));reference=path_name(entity.get('model',''));record=dict(name=label,classname=entity.get('classname'),model=reference)
        if policy:
            from tools.quake4_map_policy import compiled_static
            if compiled_static(entity,resolved[0]):
                skipped.append(dict(record,reason='Static already compiled into world PROC'));continue
        if policy and entity.get('classname')=='func_group':
            skipped.append(dict(record,reason='Editor group compiled into world PROC'));continue
        def add_emitter(prt,origin,shader_parms,fx=None,effect=None):
            if effect:radius=bse_extent(material_cache['effects'][effect]);names=[s['particle']['material'] for s in material_cache['effects'][effect]['segments']]
            else:decl=material_cache['particles'][prt];radius=particle_extent(decl);names=[stage['material'] for stage in decl['stages']]
            origin,axis=moved_frame(dict(entity,origin=' '.join(map(str,origin))))
            bounds=[(origin-radius).tolist(),(origin+radius).tolist()];mover=mover_of.get(entity.get('name'))
            reach=swept_bounds(bounds,movers,mover) if mover is not None else bounds;areas=bounds_areas(reach,proc['nodes'])
            candidates=sorted(set().union(*(light_by_area.get(a,set()) for a in areas))) if areas else []
            emitters.append(dict(name=label,particle=prt,effect=effect,origin=origin.tolist(),axis=axis.ravel().tolist(),parms=shader_parms,bounds=reach,areas=areas,fx=fx,
                                 lights=[i for i in candidates if light_intersects(reach,lights[i])],entity=label,materials=[material(n)['name'] for n in names]))
            if mover is not None:emitters[-1]['mover']=mover
            if effect:used_effects.add(effect)
            else:used_particles.add(prt)
        if policy and entity.get('classname')=='func_fx':
            # Quake 4 func_fx: start_on and not triggered plays at spawn; loop repeats it. Only drawable particle segments.
            effect=path_name(entity.get('fx','')).removesuffix('.fx');decl=material_cache['effects'].get(effect)
            if not decl or not decl['segments']:skipped.append(dict(record,reason='BSE effect without drawable particles' if decl else 'Missing BSE effect: '+effect));continue
            if entity.get('start_on','0')=='0' or entity.get('triggered','0')!='0':skipped.append(dict(record,reason='BSE effect waits for a trigger'));continue
            add_emitter(None,vector(entity.get('origin')),parms(entity),dict(loop=entity.get('loop','0')!='0'),effect);continue
        if policy and reference.endswith(('.fx','.prt')):
            skipped.append(dict(record,reason='BSE effect' if reference.endswith('.fx') else 'particle'));continue
        if not policy and entity.get('classname')=='func_fx':
            # idEntityFx: only the particle actions of untriggered start/restart effects are drawn.
            actions=material_cache['fx'].get(path_name(entity.get('fx','')).removesuffix('.fx'),[])
            timing=fx_timing(entity,actions)
            missing=[a['particle'] for a in actions if a['particle'] not in material_cache['particles']]
            if not actions or not timing or missing:
                skipped.append(dict(record,reason='Missing particle decl: '+missing[0] if missing else 'triggered fx' if actions else 'fx without particle actions'));continue
            for action in actions:
                # Particle actions: origin + offset, entity axis, renderEntity parms 1,1,1,1.
                add_emitter(action['particle'],vector(entity.get('origin'))+action['offset'],[1.0]*4+[0.0]*8,
                            dict(timing,delay=action['delay'],length=action['duration']))
            continue
        prt=None if policy else particle_reference(entity,defs)
        if prt:
            if prt not in material_cache['particles']:skipped.append(dict(record,reason='Missing particle decl: '+prt));continue
            if entity.get('start_off')=='1':skipped.append(dict(record,reason='start_off emitter (stays off, like start_off lights)'));continue
            add_emitter(prt,vector(entity.get('origin')),parms(entity));continue
        if not reference:
            if entity.get('classname')=='func_fx' or any(x in entity.get('classname','') for x in ('particle','emitter')):skipped.append(dict(record,reason='particle'))
            continue
        try:
            if reference in proc['models']:
                n=add_parts(proc['models'][reference],entity);kind='inline'
            else:
                definition=defs.model(reference);mesh=path_name(definition.get('mesh',''))
                if policy and Path(mesh).suffix in ('.fx','.prt'):
                    skipped.append(dict(record,reason='BSE effect' if mesh.endswith('.fx') else 'particle'));continue
                if Path(mesh).suffix not in FORMATS:
                    skipped.append(dict(record,reason='Unsupported render model: '+mesh));continue
                skin=entity.get('skin',definition.get('skin',''));animation=policy.idle_animation(entity,definition) if policy else idle_animation(entity,definition)
                cache=(reference,skin,animation,tuple(sorted((k,v) for k,v in entity.items() if k in ('def_head','head_joint','joint_head','override_head','def_persona') or k.startswith(('copy_joint','hidesurface','showsurface','def_attach','def_position')))))
                if cache not in parts_cache:
                    cached=model_cache.get(campaign,entity,reference,skin,animation)
                    if cached:parts_cache[cache]=model_parts(cached)
                    else:
                        row=dict(mesh=mesh,model=definition,skin=skin,entity=entity,animation=animation,variant='model')
                        if policy:policy.prepare(row,defs,[])
                        elif entity.get('def_head'):row['head']=defs.model(entity['def_head'])
                        parts_cache[cache]=load_parts(row,files,defs,policy=policy)[0]
                # load_parts/cached catalog already applied the skin.
                n=add_parts(parts_cache[cache],dict(entity,skin=''));kind='external'
            if n:entity_records.append(dict(record,kind=kind,surfaces=n,provenance=entity.get('_provenance'),hidden=entity.get('hide','0')!='0' or entity.get('hide_on_spawn')=='1',
                                           origin=vector(entity.get('origin')).tolist(),idle=kind=='external'))
            else:skipped.append(dict(record,reason='No visible material surfaces'))
        except (ValueError,OSError,KeyError,IndexError) as exc:skipped.append(dict(record,reason=str(exc)))
    viewpoints=[];text=strings(files)
    candidates=[e for e in source_entities if e.get('classname') in ('info_player_start','info_player_deathmatch')]
    candidates += [e for e in source_entities if e.get('classname') in ('info_location','info_player_teleport')]
    if policy:
        landmarks=[e for e in resolved if e.get('npc_name') and e.get('model')]
        landmarks += [e for e in resolved if e.get('classname','').startswith(('vehicle_','monster_','item_')) and e.get('model')]
        for e in landmarks:
            point=vector(e.get('origin'));angle=math.radians(float(e.get('angle',0)))
            point+=np.array([-160*math.cos(angle),-160*math.sin(angle),64])
            candidates.append(dict(e,origin=' '.join(map(str,point)),_landmark=True))
    for e in candidates:
        origin=vector(e.get('origin'));origin[2]+=(68 if policy else 64) if e.get('classname','').startswith('info_player') else 0
        if viewpoints and any(np.linalg.norm(origin-np.array(v['origin']))<160 for v in viewpoints):continue
        label=next((e[k] for k in ('location','npc_name','inv_name','name') if e.get(k)),e['classname'])
        yaw=float(e.get('angle',0));viewpoints.append(dict(name=text.get(label,label),origin=origin.tolist(),yaw=yaw,pitch=0,
            setviewpos='setviewpos '+' '.join(f'{v:g}' for v in [*origin,yaw])))
        if len(viewpoints)==6:break
    if not viewpoints:
        p=next((e for e in source_entities if 'origin' in e),{});origin=vector(p.get('origin')).tolist()
        viewpoints=[dict(name='Map origin',origin=origin,yaw=0,pitch=0,setviewpos='setviewpos '+' '.join(map(str,[*origin,0])))]
    shader_records={n:material_cache['records'][n] for n in sorted(used_materials)}
    approximations=Counter()
    for s in surfaces:
        approximations.update(shader_records[s['material']]['approximations'])
    for l in lights:approximations.update(shader_records[l['shader']]['approximations'])
    for item in surfaces+lights:
        if item.get('sound') and item['sound'].get('approximation'):approximations[item['sound']['approximation']]+=1
    stats=dict(sourceEntities=len(entities),renderedEntities=len(entity_records),inlineEntities=sum(e['kind']=='inline' for e in entity_records),
               externalEntities=sum(e['kind']=='external' for e in entity_records),hiddenEntitiesShown=sum(e['hidden'] for e in entity_records),
               sourceLights=len(lights)+len(skipped_lights),lights=len(lights),projectedLights=sum(l['projected'] for l in lights),ambientLights=sum(l['ambient'] for l in lights),
               startOffLights=sum(l['authoredOff'] for l in lights),skippedLights=len(skipped_lights),shadowModelsSkipped=proc['shadowModels'],
               areas=proc['areas'],portals=len(proc['portals']),surfaces=len(surfaces),vertices=len(geometry['vertices'])//3,triangles=len(geometry['indices'])//3,
               emitters=len(emitters),lightInteractions=sum(len(s['lights']) for s in surfaces),maxSurfaceLights=max((len(s['lights']) for s in surfaces),default=0),
               approximations=dict(approximations),seconds=round(time.monotonic()-started,2))
    if policy:
        stats['excluded']=dict(Counter(s['reason'] for s in skipped))
        stats['effectReferences']=sum(isinstance(v,str) and v.lower().endswith('.fx') for e in resolved for v in e.values())
        stats['particleReferences']=sum(isinstance(v,str) and v.lower().endswith('.prt') for e in resolved for v in e.values())
        stats['guiEntities']=sum(any(k.startswith('gui') and v for k,v in e.items()) for e in resolved)
        stats['guiSurfaces']=sum('guisurf' in shader_records[s['material']]['approximations'] for s in surfaces)
        stats['portalFades']=sum('fade' in p for p in proc['portals'])
        stats['mapRules']=map_notes
    scene=dict(game=policy.game if policy else 'doom3',sourceArchive=files['maps/'+name+'.proc'].origin,colorSize=4 if policy else 3,areaSky=proc['areaSky'],name=name,campaign=campaign,coordinateSystem='Doom native XYZ, Z up',lightScale=2,
               globalShaderParms=global_parms,startupShaderEntities=startup_entities,
               surfaces=surfaces,materials=shader_records,lights=lights,nodes=proc['nodes'],portals=proc['portals'],areaCount=proc['areas'],
               movers=movers,emitters=emitters,particles={n:material_cache['particles'][n] for n in sorted(used_particles)},
               effects={n:material_cache['effects'][n] for n in sorted(used_effects)},
               viewpoints=viewpoints,entities=entity_records,skippedEntities=skipped,skippedLights=skipped_lights,stats=stats,
               notes=['No shadows. Fog and blend lights draw after the shader passes (RB_FogAllLights). Doors/movers stay at authored start transforms; scripts and binds are not simulated.',
                      'Literal startup shader parameters are captured before waits/control flow; later scripted lighting changes are not simulated.',
                      'Sound-reactive stages use the sample envelope without audio playback. Sample phase starts at map load; triggered sounds, random choices, leadins and multiple channels are not simulated.',
                      'All renderable monsters/items are shown, including hidden/triggered entities. Start-off lights stay off.',
                      'Sky cubemaps and the authored portal-sky camera are rendered. Flare, sprite, tube, turbulent, expand and move deforms are rebuilt per view. Doom 3 .prt particles (func_emitter, func_smoke, start/restart func_fx) run the engine particle model; Quake 4 BSE sprite/line/oriented segments of start_on func_fx are simulated (sound, light, decal, model, debris and electricity segments are not). Reflection cubemaps (texgen reflect/normal, bumpy when the material has a bump stage) and mirrors are drawn. GUI surfaces show a raster of the .gui start state (no scripts, gui:: values or animation). Heat-haze programs distort a copy of the frame. Remote render views, other vertex/fragment programs, video, eyeball and particle deforms remain skipped/approximated; see per-material and per-entity details.',
                      'r_lightScale 2 is applied once to interactions. No display gamma or second overbright multiplier.',
                      'Analytic light normalization/specular lookup approximates the original quantized lookup textures. Portal culling is conservative; door portal blocking is not simulated.'])
    # rvCameraPortalSky::GetViewParms / RoE idPlayerView::SingleView replace only the
    # eye position; the player's orientation/FOV are retained. Q4 PROC sky flags or
    # RoE `sort portalSky` surfaces (CheckAreaForPortalSky) enable this view.
    skies=[e for e in resolved if e.get('classname','').lower() in ('func_camera_portalsky','info_portalsky') or e.get('spawnclass') in ('rvCameraPortalSky','idPortalSky')]
    sky=next((e for e in skies if e.get('triggered','0')=='0'),skies[0] if skies else None)
    if sky:
        origin=vector(sky.get('origin'))
        if policy and sky.get('name') in transforms:origin=(transforms[sky['name']] @ np.r_[origin,1])[:3]
        scene['portalSky']=dict(name=sky.get('name'),origin=origin.tolist())
    if not policy:
        for s in surfaces:
            if shader_records[s['material']]['portalSky']:scene['areaSky'].update({'_area'+str(a):True for a in s['areas']})
    if policy:
        scene['notes'][0]='No shadows. Fog and blend lights draw after the shader passes (RB_FogAllLights). Authored movers and static attachments are evaluated once; no physics or script VM.'
        scene['notes'] += ['Quake 4: r_lightDetailLevel 0; only detailLevel > 0 lights. falloff spawnarg is ignored (not read by SDK ParseSpawnArgsToRenderLight).',
                           'Deathmatch filters; one deterministic spawner appearance. See stats.mapRules for provenance and other layouts.',
                           'GLSL capability conditions use the non-GLSL fallback. Portal fades draw black over the portal by viewer distance to its centre and close it at the far distance (hypothesis for the PROC fade tuple).']
    write_model_data(directory/'scene.json',dict(scene,**geometry))
    saved=json.loads((directory/'scene.json').read_text(encoding='utf8'))
    stats['geometryBytes']=(directory/saved['geometry_buffers']['file']).stat().st_size
    write_json_atomic(directory/'report.json',dict(stats=stats,skippedEntities=skipped,skippedLights=skipped_lights,viewpoints=viewpoints,notes=scene['notes']))
    return stats,viewpoints


def import_maps(install,data_root=LOCAL_DATA,*,maps=None,models_root=None,replace=False,policy=None):
    root=Path(data_root).resolve();game=Path(install).resolve()
    if any(p==game or game in p.parents for p in (root,Path(models_root).resolve() if policy and models_root else root)):raise ValueError('Output must be outside read-only install')
    game_id=policy.game if policy else 'doom3'
    output=root/(game_id+'-maps');output.mkdir(parents=True,exist_ok=True)
    path=output/'catalog.json';catalog=json.loads(path.read_text(encoding='utf8')) if path.is_file() else []
    by_id={m['id']:m for m in catalog};results=[];cache=ModelCache(models_root or root,policy)
    with Install(game,policy) as archive:
        available={c+':'+n[5:-5]:(c,n) for c,files in archive.files.items() for n,a in files.items() if n.endswith('.proc') and a.campaign==c}
        if maps and set(maps)-available.keys():raise ValueError('Unknown maps: '+', '.join(set(maps)-available.keys()))
        for campaign in (policy.campaigns if policy else ('base','d3xp')):
            selected=[(key,n) for key,(c,n) in sorted(available.items()) if c==campaign and (not maps or key in maps)]
            if not selected:continue
            files=archive.files[campaign];defs=Declarations(files,policy)
            textures=Textures(files,game_texture_dir(Path(models_root or root) if policy else root,game_id),policy)
            materials=Materials(defs,textures);context=dict(materials=materials,textures=textures,records={},sounds=Sounds(files,output/'sounds',policy),
                                                            particles={} if policy else particle_decls(files),fx={} if policy else fx_decls(files),
                                                            effects=effect_decls(files) if policy else {},
                                                            guis={},gui_images=GuiImages(files,defs,policy),strings=strings(files))
            table_data=tables(files)
            for key,n in selected:
                ident=re.sub('[^a-z0-9_-]','_',key);name=n[5:-5];directory=output/'maps'/ident
                if not replace and (directory/'scene.json').is_file() and ident in by_id:
                    results.append(dict(id=ident,status='existing'));continue
                stats,views=import_scene(name,campaign,files,defs,context,cache,directory,policy)
                write_json_atomic(directory/'tables.json',table_data)
                # Geometry files are content-named: a re-import with changed geometry leaves the old one unreferenced.
                scene_text=(directory/'scene.json').read_text(encoding='utf8')
                for old in directory.glob('*.bin'):
                    if old.name not in scene_text:old.unlink()
                item=dict(id=ident,name=name,campaign=campaign,label=('RoE: ' if campaign=='d3xp' else '')+name,
                          scene='maps/'+ident+'/scene.json',stats=stats)
                by_id[ident]=item;results.append(dict(item,status='ready'))
    write_json_atomic(path,sorted(by_id.values(),key=lambda r:r['id']))
    report=dict(game=game_id,maps=len(results),ready=sum(r['status']=='ready' for r in results),results=results)
    write_json_atomic(output/'import-report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game',choices=('doom3','quake4'),default='doom3')
    parser.add_argument('--install',required=True,type=Path)
    parser.add_argument('--data-root',type=Path,default=LOCAL_DATA)
    parser.add_argument('--models-root',type=Path)
    parser.add_argument('--map',action='append',dest='maps',help='Campaign:path, e.g. base:game/admin or d3xp:game/erebus1')
    parser.add_argument('--replace',action='store_true')
    args=parser.parse_args();policy=None
    if args.game=='quake4':
        from tools import quake4_policy as policy
    print(json.dumps(import_maps(args.install,args.data_root,maps=args.maps,models_root=args.models_root,replace=args.replace,policy=policy),indent=2))

"""Ascend Maps: streamed levels, compact terrain and shared model instances.

python -m tools.import_tas_map --install PATH --output build/tas-data --models build/tas-data/tas
Repeat --map NAME for a small sample. Missing/map-embedded models are added to
the output's shared tas store, with a separate map-model-index.json; job-1's
model-index.json and catalog are never rewritten.
"""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import time

import numpy as np
from PIL import Image

from tools.import_tas import key,exclusion
from tools.local_data import LOCAL_DATA
from tools.model_data import write_model_data
from tools.tas_materials import Materials
from tools.tas_mesh import mesh
from tools.tas_map import MapLibrary,object_properties,level,transform,terrain_native,bsp,is_npc,viewer,viewpoint,speedtree_bounds
from tools.tvg_package import geometry,rotation_matrix
from tools.texture_storage import game_texture_dir,write_texture
from tools.texture_workshop import write_json_atomic


def map_section(path):
    name=path.stem.lower()
    mode=next((label for prefix,label in [('trctfblitz-','Blitz'),('trctf-','CTF'),('trteamrabbit-','Team Rabbit'),
             ('trrabbit-','Rabbit'),('trtdm-','TDM'),('trarena-','Arena'),('trcah-','CaH'),('trtraining-','Training')] if name.startswith(prefix)),'Frontend / transition')
    return mode+' / '+('Community' if path.suffix.lower()=='.udk' else 'Retail')


def entry_maps(library):
    files=[library.install/r['file'] for r in library.inventory if Path(r['file']).suffix.lower() in ('.udk','.fmap')]
    return sorted([p for p in files if p.stem.lower().startswith(('trctf-','trctfblitz-','trrabbit-','trteamrabbit-','trtdm-','trarena-','trcah-','trtraining-'))
                   and not re.search(r'[_-](sound|ter|cameras|visuals)$',p.stem,re.I) or p.stem.lower() in ('tranentry','tribesmainentry')],key=lambda p:p.stem.lower())


def streamed_packages(path,library):
    files={Path(r['file']).stem.lower():library.install/r['file'] for r in library.inventory if Path(r['file']).suffix.lower() in ('.fmap','.udk')}
    result=[];pending=[path];seen=set();missing=[]
    while pending:
        current=pending.pop(0)
        if current in seen:continue
        seen.add(current);result.append(current);p=library.package(current)
        # Streaming objects belong to UWorld, not the ULevel actor array.
        streams=[i for i,e in enumerate(p.exports,1) if e['class'].startswith('LevelStreaming') and 'default__' not in e['source'].lower()]
        for ref in streams:
            props,_=object_properties(p,ref);name=props.get('PackageName')
            if name and name.lower() in files:pending.append(files[name.lower()])
            elif name:missing.append(dict(source=name,reason='Streamed package absent'))
    return result,missing


def tree_standin(bounds):
    """Thin trunk and ellipsoid crown in asset-local units; placement scales once."""
    low,high=np.asarray(bounds,dtype=float);span=high-low;center=(low+high)/2
    if not np.isfinite([low,high]).all() or np.any(span<=0):raise ValueError('Invalid stand-in bounds')
    points=[];indices=[];groups=[]
    radius=min(span[:2])*.025
    for z in [low[2],low[2]+span[2]*.7]:
        points.extend((center[0]+radius*np.cos(i*np.pi/6),center[1]+radius*np.sin(i*np.pi/6),z) for i in range(12))
    for i in range(12):
        a=i;b=(i+1)%12;indices.extend((a,a+12,b,b,a+12,b+12))
    groups.append(dict(start=0,count=len(indices),materialIndex=0))
    start=len(indices);first=len(points)
    for ring in range(9):
        angle=np.pi*ring/8
        for i in range(12):
            turn=i*np.pi/6
            points.append((center[0]+span[0]*.5*np.sin(angle)*np.cos(turn),center[1]+span[1]*.5*np.sin(angle)*np.sin(turn),low[2]+span[2]*(.65+.35*np.cos(angle))))
    for ring in range(8):
        for i in range(12):
            a=first+ring*12+i;b=first+ring*12+(i+1)%12;c=a+12;d=b+12
            indices.extend((a,b,c,b,d,c))
    groups.append(dict(start=start,count=len(indices)-start,materialIndex=1))
    return geometry(points,np.zeros((len(points),2)),indices,groups,['standin.trunk','standin.crown'])


def sky_mesh(source,materials):
    # SkyBoxLevel/Depot are lit architectural backdrops, not sky surfaces.
    return bool(re.search(r'sky(?:dome|sphere|_?hemi)|skybox(?:[_\d]*)$',source,re.I)) or any(
        re.search(r'sky(?:dome|sphere|box)',m.get('source') or '',re.I) for m in materials)


class Models:
    def __init__(self,root,output,library,materials):
        self.root=Path(root).resolve();self.output=Path(output).resolve();self.library=library;self.materials=materials
        path=self.root/'model-index.json'
        self.index=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        self.extra_path=self.output/'map-model-index.json'
        self.extra=json.loads(self.extra_path.read_text(encoding='utf-8')) if self.extra_path.is_file() else {}
        self.cache={};self.variants={};self.used_materials={}

    def get(self,source,cls,overrides=()):
        signature=(source.lower(),cls,tuple(overrides))
        if signature in self.variants:return self.variants[signature]
        object_key=source.lower();cache_key=(object_key,cls)
        if cache_key not in self.cache:
            base=self.index.get(object_key,{})
            entry=base.get('by_class',{}).get(cls,base)
            extra=self.extra.get(object_key,{}).get('by_class',{}).get(cls,{})
            if entry.get('status')=='ready':
                data=json.loads((self.root/entry['model_json']).read_text(encoding='utf-8'))
                data['_store']='models'
            elif extra.get('status')=='ready' and cls!='SpeedTree':
                data=json.loads((self.output/extra['model_json']).read_text(encoding='utf-8'));data['_store']='map-models'
            else:
                ident='map_'+key(cls+':'+source)
                if cls=='SpeedTree':
                    try:
                        p,ref=self.library.object(source,{cls});bounds=speedtree_bounds(p,ref);bounds_source='serialized asset-local FBoxSphereBounds'
                    except (ValueError,OSError) as exc:
                        bounds=[[-8,-8,0],[8,8,25]];bounds_source='Generic local bounds: '+str(exc)
                    data=tree_standin(bounds);data.pop('material_refs')
                    materials=[]
                    for label,color in [('trunk',(94,66,42)),('crown',(60,105,58))]:
                        filename='tas_tree_standin_'+label+'.png';write_texture(self.materials.directory,filename,Image.new('RGB',(1,1),color))
                        materials.append(dict(source='standin.'+label,texture=filename,settings={'unlit':True},status='ready'))
                    data['metadata'].update(approximate=True,local_bounds=bounds,bounds_source=bounds_source,reason='SpeedTree geometry approximated by trunk/ellipsoid within local bounds')
                else:
                    reason=exclusion(dict(source=source,cls=cls))
                    if reason:raise ValueError(reason)
                    p,ref=self.library.object(source,{cls});data=mesh(p,ref)
                    refs=data.pop('material_refs');materials=[self.materials.material(s) for s in refs]
                data.update(game='tas',material_textures=[m['texture'] for m in materials],material_settings=[m['settings'] for m in materials])
                data['metadata'].update(source=source,source_class=cls,materials=materials)
                file=self.output/'model_json'/(ident+'.json');write_model_data(file,data)
                data=json.loads(file.read_text(encoding='utf-8'));data['_store']='map-models'
                self.extra.setdefault(object_key,dict(source=source,by_class={}))['by_class'][cls]=dict(status='ready',looks=[ident],geometry=data['geometry_buffers'],model_json='model_json/'+ident+'.json')
            store=self.root if data['_store']=='models' else self.output
            desc=data['geometry_buffers'];vertices=desc['arrays']['vertices']
            with (store/'model_json'/desc['file']).open('rb') as stream:
                stream.seek(vertices['offset']);raw=stream.read(vertices['count']*4)
            if len(raw)!=vertices['count']*4:raise ValueError('Truncated model vertex buffer')
            points=np.frombuffer(raw,'<f4').reshape(-1,3)
            data['_bounds']=[points.min(axis=0).tolist(),points.max(axis=0).tolist()]
            self.cache[cache_key]=data
        data=self.cache[cache_key];materials=list(data['metadata']['materials'])
        if cls!='SpeedTree':materials=[self.materials.material(m['source']) for m in materials]
        for slot,material in enumerate(overrides):
            if slot<len(materials) and material:materials[slot]=self.materials.material(material)
        for material in materials:self.used_materials[(material.get('source') or '').lower()]=material
        ident=key(cls+':'+source+':'+json.dumps([m['source'] for m in materials]))
        groups=[dict(g,texture=materials[g['materialIndex']]['texture'],settings=materials[g['materialIndex']]['settings'],kind='model') for g in data['groups']]
        sky=sky_mesh(source,materials)
        if sky:
            for group in groups:group['kind']='sky'
        sky_order=-10002 if any(re.search(r'skydomebg',m.get('source') or '',re.I) for m in materials) else -10001
        result=ident,dict(source=source,geometry=data['geometry_buffers'],store=data['_store'],groups=groups,bounds=data['_bounds'],approximate=cls=='SpeedTree',sky=sky,sky_order=sky_order)
        self.variants[signature]=result;return result

    def save(self):
        write_json_atomic(self.extra_path,self.extra)


def terrain(p,ref,props,library,materials,chunks):
    _,heights,info,alphas=terrain_native(p,ref);w,h=props['NumVerticesX'],props['NumVerticesY']
    scale=np.asarray(props.get('DrawScale3D',(256,256,256)))*props.get('DrawScale',1)
    linear=rotation_matrix(*props.get('Rotation',(0,0,0)))@np.diag(scale)
    location=np.asarray(props.get('Location',(0,0,0)))-linear@np.asarray(props.get('PrePivot',(0,0,0)))
    height_linear=linear@np.diag((1,1,1/128))
    origin=np.array((0,0,32768))-np.linalg.solve(height_linear,location)
    world_to_local=np.eye(4);world_to_local[:3,:3]=np.linalg.inv(linear);world_to_local[:3,3]=-np.linalg.solve(linear,location)
    layers=[];omitted=[]
    for layer in props.get('Layers',[]):
        if layer.get('Hidden'):continue
        try:
            setup,_=library.state(layer['Setup']);options=setup.get('Materials',[])
            if not options:raise ValueError('Empty terrain layer setup')
            tm_source=options[0]['Material'];tm,_=library.state(tm_source);mat=materials.material(tm.get('Material'))
            if len(options)>1:omitted.append(dict(source=layer['Setup'],reason='Additional slope/noise-filtered materials not evaluated',count=len(options)-1))
            alpha_index=layer.get('AlphaMapIndex',-1);alpha=None
            if alpha_index>=0:
                if alpha_index>=len(alphas):raise ValueError('AlphaMapIndex outside terrain arrays')
                raw=alphas[alpha_index];alpha='terrain_alpha_'+hashlib.sha256(f'{w}x{h}:'.encode()+raw).hexdigest()[:16]+'.png'
                write_texture(materials.directory,alpha,Image.frombytes('L',(w,h),raw))
            angle=np.deg2rad(tm.get('MappingRotation',0));s=tm.get('MappingScale',4)
            if not s:raise ValueError('Zero terrain mapping scale')
            uv=np.eye(4);uv[:2,:2]=np.array(((np.cos(angle),-np.sin(angle)),(np.sin(angle),np.cos(angle))))/s
            axes={'TMT_XZ':(0,2),'TMT_YZ':(1,2)}.get(tm.get('MappingType'),(0,1))
            projection=np.eye(4);projection[0,:3]=np.eye(3)[axes[0]];projection[1,:3]=np.eye(3)[axes[1]]
            uv=uv@projection@world_to_local;uv[:2,3]+=np.array((tm.get('MappingPanU',0),tm.get('MappingPanV',0)))
            layers.append(dict(source=tm_source,material=mat['source'],texture=mat['texture'],alpha=alpha,alpha_index=alpha_index,
                               matrix=uv.T.tolist(),scale=[s,s],rotation=tm.get('MappingRotation',0),pan=[tm.get('MappingPanU',0),tm.get('MappingPanV',0)]))
        except (ValueError,KeyError,OSError) as exc:omitted.append(dict(source=layer.get('Setup'),reason=str(exc)))
    flags=np.frombuffer(info,np.uint8)
    visible=np.packbits((flags&1)==0,bitorder='little').tobytes();edges=np.packbits((flags&2)!=0,bitorder='little').tobytes()
    offset=sum(map(len,chunks))
    if offset%2:chunks.append(b'\0');offset+=1
    chunks.extend((heights,visible,edges))
    row=dict(entity=p.qualified(ref),width=w,height=h,offset=offset,bytes=len(heights),visibility_bytes=len(visible),edge_bytes=len(edges),
             to_world=[*origin.tolist(),*height_linear.reshape(-1).tolist()],layers=layers[:5],omitted_layers=layers[5:]+omitted,
             textures={},vertex_light=None,inverted=False)
    return row,np.frombuffer(heights,'<u2').reshape(h,w),alphas


def decoration_placements(props,row,heights,alphas,library):
    """Prefer serialized decoration instances; a seeded density preview is explicit."""
    w,h=row['width'],row['height'];coords=np.asarray(row['to_world']).reshape(4,3)
    for layer in props.get('DecoLayers',[]):
        ai=layer.get('AlphaMapIndex',-1)
        for deco in layer.get('Decorations',[]):
            factory,_=library.state(deco['Factory']);source=factory.get('StaticMesh') or factory.get('SpeedTree')
            cls='SpeedTree' if factory.get('SpeedTree') else 'StaticMesh'
            instances=deco.get('Instances',[]);approximate=False
            if not instances and deco.get('Density',0)>0:
                # ponytail: deterministic density preview, not native terrain RNG/slope filters.
                rng=np.random.default_rng(int(deco.get('RandSeed',0))&0xffffffff)
                weight=np.frombuffer(alphas[ai],np.uint8).reshape(h,w)[:-1,:-1]/255 if ai>=0 else np.ones((h-1,w-1))
                density=np.maximum(0,weight*deco['Density']);counts=np.floor(density+rng.random(density.shape)).astype(int)
                if counts.sum()>200000:raise ValueError('Terrain decoration preview exceeds 200000 instances')
                y,x=np.nonzero(counts);repeat=counts[y,x];x=np.repeat(x,repeat);y=np.repeat(y,repeat)
                instances=[dict(X=float(xx+rng.random()),Y=float(yy+rng.random()),Scale=float(rng.uniform(deco.get('MinScale',1),deco.get('MaxScale',1))),Yaw=int(rng.integers(65536))) for xx,yy in zip(x,y)]
                approximate=True
            for item in instances:
                if item.get('Component'):
                    cp,_=library.state(item['Component']);source=cp.get('StaticMesh') or cp.get('SpeedTree') or source
                x,y=float(item.get('X',0)),float(item.get('Y',0))
                if not 0<=x<w-1 or not 0<=y<h-1:continue
                ix,iy=int(x),int(y);dx,dy=x-ix,y-iy
                z=(heights[iy,ix]*(1-dx)+heights[iy,ix+1]*dx)*(1-dy)+(heights[iy+1,ix]*(1-dx)+heights[iy+1,ix+1]*dx)*dy
                native=(np.array((x,y,z))-coords[0])@coords[1:].T
                yield source,cls,transform(dict(Location=native,DrawScale=item.get('Scale',1),Rotation=(0,item.get('Yaw',0),0))),approximate


def build_map(path,library,models,materials):
    paths,failures=streamed_packages(path,library);actors={};worlds=[];terrains=[];terrain_chunks=[];omitted=[];lightmaps=[];owned_components=defaultdict(list)
    for file in paths:
        p=library.package(file)
        for e in p.exports:
            if e['outer']>0 and e['class'].endswith('Component'):
                owned_components[p.qualified(e['outer'])].append(e['source'])
        levels=[i for i,e in enumerate(p.exports,1) if e['class']=='Level']
        for level_ref in levels:
            world=level(p,level_ref);worlds.append((file,world['model']))
            sources=[p.qualified(ref) for ref in world['actors'] if ref]
            for source in sources:
                try:
                    props,cls=library.state(source);actors[source]=dict(props=props,classname=cls,ancestry=library.ancestry(cls))
                except (ValueError,OSError) as exc:failures.append(dict(entity=source,reason=str(exc)))
        lightmaps.extend(dict(source=e['source'],package=str(file.relative_to(library.install)),export=i) for i,e in enumerate(p.exports,1) if e['class']=='LightMapTexture2D')
    model_library={};instances=[];decoration_rows=[];views=[];zones=[];sun=None;trees=0
    def instance(source,cls,matrix,record,overrides=(),decoration=False):
        nonlocal trees
        look,model=models.get(source,cls,overrides);model_library[look]=model
        row=dict(record,look=look,matrix=matrix)
        if cls=='SpeedTree':
            trees+=1;decoration=True;row.update(standin=True,label='SpeedTree stand-in: '+source)
        if decoration:decoration_rows.append(row)
        else:instances.append(row)
    for source,actor in actors.items():
        props,cls,chain=actor['props'],actor['classname'],actor['ancestry'];short=cls.rsplit('.',1)[-1]
        record=dict(entity=source,classname=cls,npc=is_npc(chain));reason=None
        if any(c.endswith('.playerstart') for c in chain):views.append(viewpoint(props))
        if short=='WorldInfo' and props.get('Title'):title=props['Title']
        if 'engine.terrain' in chain:
            try:
                p,ref=library.locate(source);row,heights,alphas=terrain(p,ref,props,library,materials,terrain_chunks);terrains.append(row)
                for mesh_source,mesh_cls,matrix,approximate in decoration_placements(props,row,heights,alphas,library):
                    instance(mesh_source,mesh_cls,matrix,dict(record,npc=False,procedural_approximation=approximate),decoration=True)
            except (ValueError,KeyError,OSError) as exc:failures.append(dict(record,reason='Terrain: '+str(exc)))
            continue
        if any(c.endswith('volume') for c in chain):reason='Volume/collision-only actor'
        elif 'engine.brush' in chain:reason='Editor brush; compiled BSP rendered once'
        elif 'engine.emitter' in chain or 'emitter' in short.lower():reason='Emitter omitted'
        if reason:omitted.append(dict(record,reason=reason));continue
        owned=owned_components[source]
        # Replace inherited component templates by the placed component's archetype/name.
        components={c.rsplit('.',1)[-1].lower():c for c in props.get('Components',[]) if c}
        for c in owned:
            cp,cr=library.locate(c);arch=cp.qualified(cp.entry(cr)['archetype'])
            if arch:
                for k,v in list(components.items()):
                    if v.lower()==arch.lower():del components[k]
            components[c.rsplit('.',1)[-1].lower()]=c
        rendered=False
        for component in components.values():
            try:
                package,component_ref=library.locate(component)
                component_chain=library.ancestry(package.qualified(package.entry(component_ref)['cls']))
                if not any(c.rsplit('.',1)[-1] in ('staticmeshcomponent','skeletalmeshcomponent','speedtreecomponent',
                           'directionallightcomponent','dominantdirectionallightcomponent','heightfogcomponent','exponentialheightfogcomponent') for c in component_chain):
                    continue
                cp,ccls=library.state(component);kind=ccls.rsplit('.',1)[-1]
                if kind in ('DominantDirectionalLightComponent','DirectionalLightComponent') and cp.get('bEnabled',True):
                    direction=viewer(rotation_matrix(*props.get('Rotation',(0,0,0)))@np.array((-1.,0,0)))
                    color=cp.get('LightColor',(255,255,255,255));sun=dict(direction=direction.tolist(),color=[color[2]/255,color[1]/255,color[0]/255],brightness=cp.get('Brightness',1))
                if kind in ('HeightFogComponent','ExponentialHeightFogComponent'):
                    color=cp.get('LightInscatteringColor',cp.get('FogInscatteringColor',cp.get('LightColor',(128,128,128,255))))
                    zones.append(dict(kind=kind,color=color,density=cp.get('FogDensity',cp.get('Density',.001)),start=cp.get('StartDistance',0),
                                      height=cp.get('FogHeight',props.get('Location',(0,0,0))[2]),height_falloff=cp.get('FogHeightFalloff')))
                asset=cp.get('StaticMesh') or cp.get('SkeletalMesh') or cp.get('SpeedTree')
                if not asset:continue
                if cp.get('HiddenGame') or cp.get('bHiddenInGame') or cp.get('bIsEditorOnly') or 'collision' in component.rsplit('.',1)[-1].lower():
                    omitted.append(dict(record,component=component,reason='Hidden/editor/collision component'));continue
                if props.get('bHidden') and not record['npc']:
                    omitted.append(dict(record,component=component,reason='Hidden actor'));continue
                asset_cls='SpeedTree' if cp.get('SpeedTree') else 'StaticMesh' if cp.get('StaticMesh') else 'SkeletalMesh'
                instance(asset,asset_cls,transform(props,cp),dict(record,component=component),cp.get('Materials',[]));rendered=True
            except (ValueError,KeyError,OSError) as exc:failures.append(dict(record,component=component,reason=str(exc)))
        if not rendered:omitted.append(dict(record,reason='No drawable mesh component (control, light or runtime spawn)'))
    # Rendered attachments share the owner identity; generators/vehicles/scenery stay independent.
    npc_entities={r['entity'] for r in instances if r['npc']}
    for row in instances:
        props=actors[row['entity']]['props'];owner=props.get('Owner') or props.get('Base')
        if owner in npc_entities and (props.get('bHardAttach') or props.get('BaseBoneName') or 'weapon' in row['classname'].lower()):
            row.update(actor=row['entity'],entity=owner,npc=True)
    points=[];uv=[];indices=[];groups=[]
    for file,ref in worlds:
        if not ref:continue
        try:
            p=library.package(file);world=bsp(p,ref)
            for node in world['nodes']:
                s=world['surfaces'][node['surface']]
                if s['flags']&(1|0x4000000):continue
                corner=world['points'][world['verts'][node['pool']:node['pool']+node['count']]]
                if len(corner)<3:continue
                mat=materials.material(s['material']);v=viewer(corner);normal=viewer(world['vectors'][s['normal']])
                base=len(points);start=len(indices);points.extend(v.tolist())
                texture=next((t for t in materials.textures.values() if t['texture']==mat['texture']),{})
                dimensions=[texture.get('width',256),texture.get('height',256)]
                uv.extend(((corner-world['points'][s['base']])@world['vectors'][[s['u'],s['v']]].T/dimensions).tolist())
                for i in range(1,len(corner)-1):
                    tri=[base,base+i,base+i+1]
                    if np.dot(np.cross(v[i]-v[0],v[i+1]-v[0]),normal)<0:tri[1],tri[2]=tri[2],tri[1]
                    indices.extend(tri)
                groups.append(dict(start=start,count=len(indices)-start,texture=mat['texture'],settings=mat['settings'],kind='bsp',npc=False))
        except (ValueError,IndexError,OSError) as exc:failures.append(dict(source=str(file),reason='BSP: '+str(exc)))
    decorations=[];deco_chunks=[];batches=defaultdict(list)
    for row in decoration_rows:
        cell=tuple(np.floor(np.asarray(row['matrix'])[12:15:2]/64).astype(int));batches[(row['look'],cell)].append(row)
    for (look,cell),rows in batches.items():
        decorations.append(dict(look=look,entity=rows[0]['entity'],offset=sum(map(len,deco_chunks)),count=len(rows),npc=False,
                                procedural_approximation=any(r.get('procedural_approximation') for r in rows),
                                decorationType=('SpeedTree stand-in / ' if rows[0].get('standin') else '')+model_library[look]['source']))
        deco_chunks.append(np.asarray([r['matrix'] for r in rows],'<f4').tobytes())
    if not views:
        origin=instances[0]['matrix'][12:15] if instances else [0,10,0]
        if terrains:
            t=terrains[0];c=np.asarray(t['to_world']).reshape(4,3);origin=viewer((np.array((t['width']/2,t['height']/2,32768))-c[0])@c[1:].T).tolist();origin[1]+=100
        views=[dict(origin=origin,yaw=0,pitch=0)]
    extent_points=[]
    if not terrains:
        extent_points.extend(points)
        for i in instances:
            model=model_library[i['look']]
            if model.get('sky'):continue
            low,high=model['bounds'];matrix=np.asarray(i['matrix']).reshape(4,4).T
            extent_points.extend((matrix@np.array((x,y,z,1)))[:3].tolist() for x in (low[0],high[0]) for y in (low[1],high[1]) for z in (low[2],high[2]))
    for t in terrains:
        c=np.asarray(t['to_world']).reshape(4,3)
        extent_points.extend(viewer((np.array((x,y,32768))-c[0])@c[1:].T).tolist() for x in (0,t['width']-1) for y in (0,t['height']-1))
    span=float(np.ptp(np.asarray(extent_points),axis=0)[[0,2]].max()) if extent_points else 3000
    scene=dict(game='tas',name=path.stem,title=locals().get('title',''),format='ascend-805',actors=len(actors),preview_extent=span,
               vertices=len(points),indices=len(indices),groups=groups,model_library=model_library,instances=instances,decorations=decorations,
               terrain=terrains,viewpoints=views,sky=None,zones=zones,sun=sun,speedtrees=trees,npcs=len({r['entity'] for r in instances if r['npc']}),
               streamed_packages=[str(p.relative_to(library.install)) for p in paths],lightmap_references=lightmaps,
               omitted=omitted,model_failures=failures,missing=sorted({r.get('source',r.get('component',r.get('entity','unknown'))) for r in failures}),
               lighting=dict(display_power=1,source='Fixed ambient plus authored dominant/directional light; lightmap references retained'),
               gaps=['Lighting parity deferred; no baked-lightmap evaluation, dynamic effects or gameplay scripts',
                     'Up to five authored terrain layers; slope/noise submaterials are listed when approximated',
                     'SpeedTrees use labelled trunk/ellipsoid stand-ins within local bounds; foliage shape is approximate',
                     'Light distance haze uses authored fog colour/start and map extent, not native height integration; emitters and collision volumes are omitted'])
    blobs=dict(geometry=np.asarray(points,'<f4').tobytes()+np.asarray(uv,'<f4').tobytes()+np.asarray(indices,'<u4').tobytes(),
               terrain=b''.join(terrain_chunks),lighting=b'',decorations=b''.join(deco_chunks))
    return scene,blobs


def import_maps(install,output=LOCAL_DATA,replace=False,map_names=None,model_root=None):
    install,output=Path(install).resolve(),Path(output).resolve()
    if output==install or install in output.parents:raise ValueError('Output must be outside the read-only install')
    started=time.perf_counter();library=MapLibrary(install)
    try:
        maps=entry_maps(library);selected={Path(n).stem.lower() for n in map_names} if map_names else None
        if selected and selected-{p.stem.lower() for p in maps}:raise ValueError('Unknown map selection: '+str(selected-{p.stem.lower() for p in maps}))
        materials=Materials(library,game_texture_dir(output,'tas'));models=Models(model_root or output/'tas',output/'tas',library,materials)
        root=output/'tas-maps';report=dict(game='tas',entry_maps=len(maps),imported=[],skipped=[],failed={},results=[]);index=[]
        for path in maps:
            if selected and path.stem.lower() not in selected:continue
            ident=path.stem.lower();folder=root/'maps'/ident;entry=dict(id=ident,name=path.stem,group=map_section(path))
            try:
                if not replace and (folder/'scene.json').is_file():
                    scene=json.loads((folder/'scene.json').read_text());report['skipped'].append(path.stem)
                else:
                    scene,blobs=build_map(path,library,models,materials);folder.mkdir(parents=True,exist_ok=True)
                    for name,blob in blobs.items():
                        (folder/(name+('.bin.gz' if name=='decorations' else '.bin'))).write_bytes(gzip.compress(blob,compresslevel=3,mtime=0) if name=='decorations' else blob)
                    write_json_atomic(folder/'scene.json',scene);report['imported'].append(path.stem)
                drawable=bool(scene['indices'] or scene['instances'] or scene['terrain'] or scene['decorations'])
                entry.update(status='ready' if drawable else 'skipped',title=scene['title'])
                if not drawable:entry['reason']='No drawable BSP, terrain or mesh components'
                report['results'].append(dict(name=path.stem,actors=scene['actors'],instances=len(scene['instances']),speedtrees=scene['speedtrees'],
                    decorations=sum(d['count'] for d in scene['decorations'])-scene['speedtrees'],missing=len(scene['model_failures']),npcs=scene['npcs'],
                    terrain=[dict(width=t['width'],height=t['height'],layers=len(t['layers']),bytes=t['bytes']+t['visibility_bytes']+t['edge_bytes']) for t in scene['terrain']],
                    sizes={f.name:f.stat().st_size for f in folder.iterdir() if f.is_file()},streamed_packages=scene['streamed_packages']))
            except (ValueError,OSError,KeyError,IndexError) as exc:report['failed'][path.stem]=str(exc);entry.update(status='failed',reason=str(exc))
            index.append(entry);models.save()
        if (root/'index.json').is_file():
            current={r['id'] for r in index};index.extend(r for r in json.loads((root/'index.json').read_text()) if r['id'] not in current)
        used_materials=dict(models.used_materials,**materials.materials)
        report.update(material_results=list(used_materials.values()),texture_results=list(materials.textures.values()),package_failures=library.failures,
                      elapsed_seconds=time.perf_counter()-started,lzo=library.stats())
        report['unresolved_materials']=sum(m['status']!='ready' for m in used_materials.values())
        write_json_atomic(root/'index.json',sorted(index,key=lambda r:(r['group'],r['name'])))
        write_json_atomic(root/'model-library.json',dict(root=str(models.root),map_root=str(models.output)))
        write_json_atomic(root/'import-report.json',report);return report
    finally:library.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',required=True,type=Path);parser.add_argument('--output',type=Path,default=LOCAL_DATA)
    parser.add_argument('--models',type=Path);parser.add_argument('--map',action='append');parser.add_argument('--replace',action='store_true')
    args=parser.parse_args();result=import_maps(args.install,args.output,args.replace,args.map,args.models)
    print(json.dumps({k:v for k,v in result.items() if k not in ('material_results','texture_results')},indent=2))
    raise SystemExit(bool(result['failed']))

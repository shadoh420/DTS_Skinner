"""E.Y.E. BSP20 maps with HDR lightmaps and shared model instances.

python -m tools.import_eye_map --install PATH --output build/eye-data/eye-maps
Use --models to read an existing model library; repeated --map selects a sample.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import struct
import zipfile
import numpy as np
from PIL import Image

if not __package__:
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools import source_bsp as bspreader, source_assets, source_effects, vtmb
from tools.import_vtmb import key
from tools.import_vtmb_map import number, brush_transform, indexed, Tree, fog_params, water_fog, sky_camera, sky3d, proxy_gap
from tools.import_quake_map import rotation, vector, SCALE
from tools.import_unreal_map import pack
from tools.model_data import read_geometry_buffers
from tools.texture_storage import game_texture_dir, write_texture
from tools.texture_workshop import write_json_atomic
from tools.local_data import LOCAL_DATA


def section(name):
    return {'cc':'Campaign / hubs','cm':'Mission rotation','br':'Battle Royal','tm':'Team Artifact'}.get(name.split('_')[0],'Other')


def fgd_models(path):
    """Read editor model defaults and NPCClass tags; never execute game definitions."""
    if not path.is_file():return {}
    text=re.sub(r'//[^\n]*','',path.read_text(encoding='cp1252'))
    result={}
    for block in re.split(r'(?m)(?=^@\w+Class\b)',text):
        match=re.match(r'@(\w+Class)\s+([^=]+)=\s*(\w+)\s*:',block)
        if not match:continue
        model=re.search(r'studio(?:prop)?\(\s*"([^"]+)"\s*\)',match[2],re.I)
        if not model:model=re.search(r'\bmodel\((?:studio|choices)\)\s*:\s*"[^"]*"\s*:\s*"([^"]+)"',block,re.I)
        result[match[3].lower()]=dict(npc=match[1]=='NPCClass',model=model[1] if model else '',source='modding/eye.fgd editor default')
    return result


def classify(e,definitions):
    cls=e.get('classname','').lower();definition=definitions.get(cls,{})
    if 'maker' in cls or 'spawner' in cls or cls.startswith('info_spawn'):
        return False,'Spawner/maker: no preview spawned'
    if cls in ('npc_bullseye','npc_enemyfinder','npc_enemyfinder_combinecannon'):
        return False,'Invisible AI target/controller'
    if cls.startswith(('prop_ragdoll','vehicle_')) or 'ragdoll' in cls or cls.endswith('_dead'):
        return False,''
    return cls.startswith('npc_') or definition.get('npc',False),''


class ModelLibrary:
    def __init__(self,root,files):
        self.root=Path(root).resolve();self.files=files
        self.index=json.loads((self.root/'model-index.json').read_text(encoding='utf-8'))
        self.cache={};self.hardware={}
    def get(self,name,skin):
        name=vtmb.model_reference(name);entry=self.index.get(name)
        if not entry:raise ValueError('Model absent from model-index.json: '+name)
        if entry.get('status')=='skipped':raise ValueError('Model library skipped: '+entry['reason'])
        looks=entry['looks']
        if skin<0:raise ValueError(f'Model skin {skin} outside {len(looks)} looks: {name}')
        if skin>=len(looks):skin=0  # The studio renderer draws skin family 0 when the skin is out of range.
        look=looks[skin]
        if not re.fullmatch(r'[a-z0-9_.-]+',look):raise ValueError('Unsafe model look')
        if look not in self.cache:
            path=self.root/'model_json'/(look+'.json');m=json.loads(path.read_text(encoding='utf-8'))
            arrays=read_geometry_buffers(path,m['geometry_buffers'])
            m['_normals']=np.asarray(arrays['normals']).reshape(-1,3)
            self.cache[look]=m
        return look,self.cache[look]
    def hardware_maps(self,model):
        name=model['metadata']['vtx']
        if name not in self.hardware:self.hardware[name]=bspreader.vtx_maps(self.files[name].read())
        return self.hardware[name]


def instance_light(record,model,library,files,ambient):
    normals=model['_normals'][:,[2,0,1]]@rotation(record['angles']).T
    origin=np.array(record.get('lighting_origin') or record['origin'])
    cube,leaf=ambient.cube(origin)
    light=sum(cube[(normals[:,axis]<0).astype(int)+axis*2]*normals[:,axis:axis+1]**2 for axis in range(3))
    note=dict(method='nearest ambient cube in containing leaf',leaf=leaf)
    if 'detail_light' in record:
        return np.broadcast_to(record['detail_light'],normals.shape).copy(),dict(method='dprp/dplh authored detail light')
    filename=record.get('vhv')
    if filename and filename in files:
        try:
            colors=bspreader.vhv(files[filename].read(),model['metadata']['checksum'])
            if not colors:raise ValueError('Empty authored VHV; ambient fallback')
            maps=library.hardware_maps(model)
            if len(colors)!=len(maps):raise ValueError('VHV / default VTX mesh count mismatch')
            groups={(g['source_bodypart'],g['source_model'],g['source_mesh']):g for g in model['groups']}
            used=0;candidate=light.copy()
            for color,(identity,originals) in zip(colors,maps):
                if len(color)!=len(originals):raise ValueError('VHV / VTX hardware vertex count mismatch')
                if identity not in groups:continue  # job1 deliberately removed crosshair meshes
                g=groups[identity]
                if (originals>=g['vertex_count']).any():raise ValueError('VHV original mesh vertex outside model range')
                counts=np.bincount(originals,minlength=g['vertex_count']);sums=np.zeros((g['vertex_count'],3))
                np.add.at(sums,originals,color);valid=counts>0;start=g['vertex_start']
                candidate[start:start+g['vertex_count']][valid]=sums[valid]/counts[valid,None];used+=int(valid.sum())
            light=candidate
            note=dict(method='VHV2 BGRA via VTX originals',source=filename,vertices=used)
        except (ValueError,KeyError) as exc:note['vhv_fallback']=str(exc)
    elif filename:note['vhv_fallback']='Missing '+filename
    return light,note


DRAWN_EFFECTS={'move_rope','keyframe_rope','infodecal','info_overlay','env_sprite','env_lightglow','point_spotlight'}
OVERLAY=struct.Struct('<ihH64i2f2f12f3f3f')  # doverlay_t, 352 bytes


def overlays(raw):
    """The overlays lump: [{material texinfo, faces, u, v, uv_points, origin, normal}] (bspfile.h doverlay_t)."""
    if len(raw)%OVERLAY.size:raise ValueError('Incomplete overlays lump')
    found=[]
    for at in range(0,len(raw),OVERLAY.size):
        v=OVERLAY.unpack_from(raw,at);count=v[2]&0x3FFF
        if count>64:raise ValueError('Overlay with too many faces')
        found.append(dict(texinfo=v[1],faces=list(v[3:3+count]),u=v[67:69],v=v[69:71],uv_points=np.array(v[71:83]).reshape(4,3),origin=v[83:86],normal=v[86:89]))
    return found


def add_effects(b,entities,material,face_tile,tiles,shared,points,uvs,lmuv,colors,indices,groups,images,files,exposure,in_sky):
    """Ropes, infodecals, overlays and sprites (tools/source_effects.py) into E.Y.E.'s geometry (page axes, RGBA colours,
    lightmap tiles) and sprite batches."""
    counts,batches=dict(ropes=0,decals=0,overlays=0,pieces=0,sprites=0),{}
    def add(p,uv,lm,tri,mat,tile=None,share=None,**extra):
        base=len(points);start=len(indices);points.extend(np.asarray(p)[:,[1,2,0]]*SCALE);uvs.extend(uv);lmuv.extend(lm)
        colors.extend(np.column_stack([np.full((len(p),3),255),np.zeros(len(p))]));indices.extend((np.asarray(tri).reshape(-1)+base).tolist())
        groups.append(dict(mat,start=start,count=len(indices)-start,kind='surface',entity=0,npc=False,classname='worldspawn',alpha=1,tint=[1,1,1],**extra))
        if share is None:tiles.append((base,len(points),tile))
        else:shared.append((base,len(points),share))
    ambient=bspreader.Ambient(b)
    for name,rope,width,scale in source_effects.ropes(entities):
        mat=material(name);size=mat.get('size') or [1,1]
        positions,uv,tri=source_effects.rope_mesh(rope,width,scale,size[1]/max(size[0],1))
        # Lit with the light at the rope (C_RopeKeyframe::CalcLightValues): its middle's ambient cube, averaged.
        try:cube=ambient.cube(rope[len(rope)//2])[0]
        except (ValueError,IndexError):cube=np.full((6,3),.25)
        tile=np.rint(np.clip(cube.mean(0)/255*exposure,0,1)**(1/2.2)*255).astype(np.uint8).reshape(1,1,3)
        add(positions,uv,np.zeros((len(positions),2)),tri,mat,tile,**({'sky3d':True} if in_sky(rope[len(rope)//2]) else {}));counts['ropes']+=1
    def piece(n,points3,st,mat,**extra):
        index,sky,_=face_tile[n];info=b['texinfo'][b['faces'][n]['texinfo']]
        lm=points3@np.array(info[8:16]).reshape(2,4)[:,:3].T+np.array(info[8:16]).reshape(2,4)[:,3]-b['faces'][n]['mins']
        if tiles[index][2].shape[:2]==(1,1):lm[:]=0
        add(points3,st,lm,[(0,k,k+1) for k in range(1,len(points3)-1)],mat,share=index,decal=True,**({'sky3d':True} if sky else {}),**extra);counts['pieces']+=1
    def takes_decals(n):
        face=b['faces'][n];return face['disp']<0 and not b['texinfo'][face['texinfo']][16]&(2|4|128|256|512|source_effects.SURF_NODECALS)
    candidates=[(n,p3,np.array(b['planes'][b['faces'][n]['plane']][:3],float)) for n,(_,_,p3) in face_tile.items() if takes_decals(n)]
    for e in entities:
        if e.get('classname')!='infodecal' or number(e,'starthidden') or not e.get('texture'):continue
        mat=material(e['texture'])
        if not mat.get('size') or mat['texture']=='missing_eye.png':continue
        scale=number(dict(s=mat['settings'].get('parameters',{}).get('$decalscale',1)),'s',1) or 1
        for n,p3,st in source_effects.decal_pieces(candidates,vector(e),mat['size'][0]*scale,mat['size'][1]*scale):piece(n,p3,st,mat)
        counts['decals']+=1
    faces={n:(p3,np.array(b['planes'][b['faces'][n]['plane']][:3],float)) for n,(_,_,p3) in face_tile.items()}
    for overlay in overlays(b['lumps'][45]):
        info=bspreader.indexed(b['texinfo'],overlay['texinfo'],'texinfo');mat=material(b['names'][bspreader.indexed(b['texdata'],info[17],'texdata')[3]])
        if mat['texture']=='missing_eye.png':continue
        for n,p3,st in source_effects.overlay_pieces(overlay,faces):piece(n,p3,st,mat)
        counts['overlays']+=1
    for name,origin,width,scale_or_aspect,rgba,blend,glow,rate in source_effects.sprites(entities):
        mat=material(name)
        if mat['texture']=='missing_eye.png' or not mat.get('size'):continue
        w,h=mat['size'];full,aspect=(width,scale_or_aspect) if width is not None else (w*scale_or_aspect,h/max(w,1))
        sig=(mat['texture'],blend,glow)
        if sig not in batches:
            frames=[mat['texture']]
            if width is None and rate>0 and mat.get('frames',1)>1:
                stem,ext=mat['texture'].rsplit('.',1)
                for k in range(1,mat['frames']):images[f'{stem}_f{k}.{ext}']=vtmb.material_texture(files,mat['vmt'],compressed=True,frame=k)[0];frames.append(f'{stem}_f{k}.{ext}')
            batches[sig]=dict(texture=mat['texture'],blend=blend,aspect=round(aspect,4),items=[],**({'depth':False,'occlude':True} if glow else {}),
                              **({'frames':frames,'fps':rate} if len(frames)>1 else {}))
        batches[sig]['items'].append([*(round(float(c),4) for c in np.asarray(origin)[[1,2,0]]*SCALE),round(full*SCALE,4),*(round(float(c),4) for c in rgba)]);counts['sprites']+=1
    for m in (g for g in groups if 'vmt' in g):m.pop('vmt')
    return dict(counts=counts,sprites=list(batches.values()))


def build_map(raw,name,loose,library,definitions,exposure=1):
    b=bspreader.read_bsp(raw);files,pak=bspreader.pak_files(b,loose);gamelumps,gameaudit=bspreader.game_lumps(b)
    entities=[{k.lower():v for k,v in pairs} for pairs in b['entities']];world=next((e for e in entities if e.get('classname')=='worldspawn'),{})
    b['dark_styles']=[int(number(e,'style')) for e in entities if e.get('classname','').startswith('light') and int(number(e,'spawnflags'))&1]
    images={'missing_eye.png':Image.new('RGBA',(2,2),(255,0,255,255)),'eye_sky_plain.png':Image.new('RGB',(1,1),(70,85,105))}
    materials={};missing=[]
    def material(name,dirs=(),sky=False):
        cache=(name,tuple(dirs),sky)
        if cache in materials:return materials[cache]
        out=dict(name=name,texture='eye_sky_plain.png' if sky else 'missing_eye.png',settings={})
        materials[cache]=out
        try:
            vmt,path=source_assets.resolve_material(files,name,dirs);p=vmt['params'];settings,mnotes=vtmb.material_settings(vmt)
            out.update(settings=settings,notes=mnotes,source=path)
            # HDR sky RGB is a mantissa with alpha scale, never ordinary diffuse: Valve's sky_hdr_compressed_rgbs shader
            # draws rgb*a*8 (linear, any texture format), here at the fixed exposure, clamped, gamma 2.2. That reproduces
            # Valve's own LDR sky_day01_01 faces from their _hdr twins to 0.35/255.
            # noctis_hdrft spells its key "$hdrcompressedtexture<tab>"; read it as meant (the game binds no HDR texture there).
            hdr=next((v for k,v in p.items() if k.strip()=='$hdrcompressedtexture' and isinstance(v,str) and v),None)
            if hdr:
                rgba,info=vtmb.read_texture(files,hdr,linear=True)
                rgb=np.clip(rgba[...,:3]*rgba[...,3:]*8*exposure,0,1)**(1/2.2)
                scale=re.search(r'scale\s+(\S+)\s+(\S+)',str(p.get('$basetexturetransform','')))
                if scale and float(scale[1])==1 and float(scale[2])>1:  # Half-height sides: the image on top, its clamped last row below.
                    rgb=np.pad(rgb,((0,round(len(rgb)*(float(scale[2])-1))),(0,0),(0,0)),mode='edge')
                filename=key(f'{path} hdr {exposure}')+'_sky.png'
                if any(s['origin']=='BSP pakfile' for s in info['sources']):filename=key(name_map)+'_'+filename
                images[filename]=Image.fromarray(np.rint(rgb*255).astype(np.uint8))
                out['texture']=filename;out['notes'].append(f'HDR sky: $hdrcompressedtexture rgb*alpha*8, exposure {exposure}, clamped, gamma 2.2');return out
            if sky and '$hdrbasetexture' in p:
                out['texture']='eye_sky_plain.png';out['notes'].append('Float HDR sky replaced with fixed plain colour');return out
            if vmt['shader'].lower()=='water' and not p.get('$basetexture'):
                tint=str(p.get('$refracttint',p.get('$fogcolor','[.25 .35 .4]')))
                values=np.asarray([float(v) for v in tint.strip('[]{} ').split()])
                if len(values)!=3 or not np.isfinite(values).all():raise ValueError('Invalid water tint')
                if '{' in tint or max(values)>1:values/=255
                color=tuple(np.rint(np.clip(values,0,1)*255).astype(int))+(96,)
                filename=key(path+str(color))+'_water.png';images[filename]=Image.new('RGBA',(1,1),color)
                out['texture']=filename;out['notes']=['Water tint preview, 38% opacity; byte-valued tint normalized; no reflection/refraction']
                return out
            def texture(vm):
                filename,payload,info=source_assets.prepare_texture(files,vm,path)
                if vm is vmt:out.update(size=[info['width'],info['height']] if 'width' in info else None,frames=info.get('frames',1),vmt=vm)
                if any(s['origin']=='BSP pakfile' for s in info.get('sources',[])) or (not vm['params'].get('$basetexture') and files[path].origin=='BSP pakfile'):
                    filename=key(name_map)+'_'+filename
                images[filename]=payload
                rate=vtmb.animated_texture_rate(p) if vm is vmt else None
                if rate and info.get('frames',1)>1:  # AnimatedTexture: every VTF frame, swapped by the viewer at the proxy's rate.
                    stem,ext=filename.rsplit('.',1);frames=[filename]+[f'{stem}_f{k}.{ext}' for k in range(1,info['frames'])]
                    for k,frame in enumerate(frames[1:],1):images[frame]=vtmb.material_texture(files,vm,compressed=True,frame=k)[0]
                    out['anim']=dict(frames=frames,fps=rate)
                return filename
            out['texture']=texture(vmt)
            if vmt['shader'].lower()=='worldvertextransition' and p.get('$basetexture2'):
                out['texture2']=texture(dict(vmt,params=dict(p,**{'$basetexture':p['$basetexture2']})))
        except (ValueError,OSError) as exc:
            out['reason']=str(exc);missing.append(dict(source=name,reason=str(exc)))
        return out
    name_map=name
    placements,omitted=bspreader.props(gamelumps);failures=[];brushes=[(0,{},np.eye(3),np.zeros(3),0)];movers=[];mover_of={}
    for i,e in enumerate(entities):
        cls=e.get('classname','').lower();model=e.get('model','');npc,skip=classify(e,definitions)
        audit=dict(entity=i,classname=cls,model=model,npc=npc)
        if skip:omitted.append(dict(audit,reason=skip));continue
        if model.startswith('*'):
            if not cls.startswith('func_') or cls in ('func_areaportal','func_areaportalwindow','func_occluder','func_viscluster','func_clip_vphysics','func_smokevolume'):
                omitted.append(dict(audit,reason='Control/trigger/volume brush'));continue
            if number(e,'startdisabled') or number(e,'renderamt',255)==0:
                omitted.append(dict(audit,reason='Brush disabled/invisible at start'));continue
            m=int(model[1:]);turn,offset=brush_transform(e,bspreader.indexed(b['models'],m,'brush model'))
            spin=source_effects.rotating(e)  # func_rotating that starts on: a page mover turning about its origin
            if spin:mover_of[i]=len(movers);movers.append(dict(pivot=(vector(e)[[1,2,0]]*SCALE).round(4).tolist(),spin=[*np.round(np.array(spin[:3])[[1,2,0]],6),round(spin[3],6)]))
            brushes.append((m,e,turn,offset,i));continue
        if not model and (npc or cls.startswith(('item_','weapon_','prop_'))):
            definition=definitions.get(cls,{})
            model=definition.get('model','');audit['model_rule']=definition.get('source','No model/default definition')
            if model.startswith('models/editor/') :model=''
        if model.lower().endswith('.mdl'):
            if not npc and (number(e,'starthidden') or number(e,'renderamt',255)==0):
                omitted.append(dict(audit,reason='Non-NPC model hidden at start'));continue
            placements.append(dict(audit,model=model,origin=vector(e).tolist(),angles=vector(e,'angles').tolist(),skin=int(number(e,'skin')),
                scale=number(e,'modelscale',1),alpha=number(e,'renderamt',255)/255,tint=(vector(e,'rendercolor')/255).tolist() if 'rendercolor' in e else [1,1,1],
                authored_state={k:e[k] for k in ('armorchoice','body','defaultanim','parentname','additionalequipment','startdisabled') if k in e}))
        elif npc or cls.startswith(('item_','weapon_','prop_')):
            failures.append(dict(audit,reason='No placed model or FGD model default'))
        elif i:omitted.append(dict(audit,reason='Drawn as a rope, decal or sprite' if cls in DRAWN_EFFECTS else 'Logic/particles/lights; not geometry'))

    views=[]
    for e in entities:
        if e.get('classname','').startswith('info_player_'):
            p=vector(e)+[0,0,64];a=vector(e,'angles');views.append(dict(origin=(p[[1,2,0]]*SCALE).tolist(),yaw=-float(a[1]),pitch=-float(a[0])))
    if not views:
        p=(np.asarray(b['models'][0][:3])+b['models'][0][3:6])/2;views=[dict(origin=(p[[1,2,0]]*SCALE).tolist(),yaw=0,pitch=0)]
    # Fog at spawn: the last env_fog_controller flagged Master (spawnflags 1), else the first (fogcontroller.cpp CFogSystem::LevelInitPostEntity).
    controllers=[e for e in entities if e.get('classname')=='env_fog_controller']
    master=next((e for e in reversed(controllers) if int(number(e,'spawnflags'))&1),controllers[0] if controllers else None)
    tree=Tree(b['lumps'],b['source_planes'],b['models'][0][9],56 if b['audit'][10]['version']==0 else 32)
    camera,sky_area,sky_reason=sky_camera(tree,entities,views[0]['origin'])
    face_area=tree.face_areas() if camera else {}
    in_sky=lambda point:camera is not None and tree.leaf(point)[1]==sky_area
    points=[];uvs=[];lmuv=[];colors=[];indices=[];groups=[];tiles=[];face_skips=[];faces=disps=0;shared=[];face_tile={}
    for model,e,turn,origin,entity in brushes:
        row=b['models'][model];first,count=row[10:12]
        if first<0 or count<0 or first+count>len(b['faces']):raise ValueError('Invalid brush face range')
        brush_sky=model and in_sky(origin+turn@((np.asarray(row[:3])+row[3:6])/2))
        for n in range(first,first+count):
            face=b['faces'][n];p,uv,lm,tri,flags,tex=bspreader.face_geometry(b,n)
            # 3D sky area: world faces by their leaves (leaffaces), else by a point just in front of the face.
            sky=brush_sky if model else camera is not None and (face_area[n]==sky_area if n in face_area else in_sky(p.mean(0)+b['planes'][face['plane']][:3]))
            if flags&(128|256|512):face_skips.append(dict(face=n,reason='Nodraw/hint/skip'));continue
            mat=material(tex) if not flags&6 else dict(texture='eye_sky_plain.png',settings={})
            tile=bspreader.light_tile(b,face,flags,exposure)
            if tile.shape[:2]==(1,1):lm[:]=0
            base=len(points);start=len(indices);points.extend((p@turn.T+origin)[:,[1,2,0]]*SCALE);uvs.extend(uv);lmuv.extend(lm)
            blend=np.zeros(len(p))
            if face['disp']>=0:
                d=b['displacements'][face['disp']];blend=np.array(b['dispverts'][d['vertex']:d['vertex']+len(p)])[:,4];disps+=1
            colors.extend(np.column_stack([np.full((len(p),3),255),np.clip(blend,0,255)]));indices.extend((tri.ravel()+base).tolist());faces+=1
            groups.append(dict(mat,start=start,count=len(indices)-start,kind='sky' if flags&6 else 'surface',entity=entity,npc=False,
                classname=e.get('classname','worldspawn'),alpha=number(e,'renderamt',255)/255,tint=(vector(e,'rendercolor')/255).tolist() if 'rendercolor' in e else [1,1,1],
                **({'sky3d':True} if sky else {}),**({'mover':mover_of[entity]} if entity in mover_of else {})))
            tiles.append((base,len(points),tile))
            if not model:face_tile[n]=(len(tiles)-1,sky,p)
    effects=add_effects(b,entities,material,face_tile,tiles,shared,points,uvs,lmuv,colors,indices,groups,images,files,exposure,in_sky)
    for m in materials.values():m.pop('vmt',None)  # kept only for sprite frames
    width,height,places=pack([(t.shape[1],t.shape[0]) for _,_,t in tiles]);height=max(height,1)
    atlas=np.full((height,width,3),255,np.uint8);lmuv=np.array(lmuv).reshape(-1,2)
    for (a,z,t),(x,y) in zip(tiles,places):
        h,w=t.shape[:2];atlas[y:y+h+2,x:x+w+2]=np.pad(t,((1,1),(1,1),(0,0)),mode='edge')
        lmuv[a:z]=(lmuv[a:z]+[x+1.5,y+1.5])/[width,height]
    for a,z,k in shared:x,y=places[k];lmuv[a:z]=(lmuv[a:z]+[x+1.5,y+1.5])/[width,height]  # decals and overlays read their face's tile
    # Batch world/brush surfaces by full draw state; entity/NPC identity remains intact.
    batches={}
    for g in groups:
        sig=json.dumps({k:v for k,v in g.items() if k not in ('start','count')},sort_keys=True)
        if sig not in batches:batches[sig]=(g,[])
        batches[sig][1].extend(indices[g['start']:g['start']+g['count']])
    groups=[];indices=[]
    for g,ix in batches.values():groups.append(dict(g,start=len(indices),count=len(ix)));indices.extend(ix)
    blob=b''.join((np.asarray(points,'<f4').tobytes(),np.asarray(uvs,'<f4').tobytes(),lmuv.astype('<f4').tobytes(),
                   np.asarray(colors,np.uint8).tobytes(),np.asarray(indices,'<u4').tobytes()))
    models={};instances=[];light_chunks=[];light_offset=0;ambient=bspreader.Ambient(b)
    for record in placements:
        try:
            look,m=library.get(record['model'],record['skin'])
            if look not in models:
                mats={i:material(m['material_names'][i],m['metadata']['material_directories']) for i in {g['materialIndex'] for g in m['groups']}}
                models[look]=dict(geometry=m['geometry_buffers'],groups=[dict(g,**mats[g['materialIndex']],kind='model') for g in m['groups']])
            light,receipt=instance_light(record,m,library,files,ambient)
            if not np.isfinite(light).all():raise ValueError('Nonfinite model lighting')
            light_chunks.append(light.astype('<f4').tobytes());turn=rotation(record['angles']);basis=np.eye(3)[[1,2,0]]
            matrix=np.eye(4);matrix[:3,:3]=basis@turn@basis.T*SCALE*record.get('scale',1);matrix[:3,3]=np.asarray(record['origin'])[[1,2,0]]*SCALE
            leaf=(record.get('leaves') or [record.get('leaf')])[0]
            sky=camera is not None and (indexed(tree.leaves,leaf,'leaf')[1]==sky_area if leaf is not None else in_sky(np.asarray(record['origin'])))
            instances.append(dict(record,look=look,matrix=matrix.T.ravel().tolist(),lighting=dict(receipt,offset=light_offset,count=len(light)),**({'sky3d':True} if sky else {})))
            light_offset+=len(light)*12
        except (ValueError,OSError,KeyError) as exc:failures.append(dict(record,reason=str(exc)))
    sky=dict(name=world.get('skyname',''),rotate=0,axis=[0,0,1],faces={})
    for suffix in ('rt','lf','bk','ft','up','dn'):sky['faces'][suffix]=material('skybox/'+sky['name']+suffix,sky=True)
    def water(texinfo):
        name=b['names'][indexed(b['texdata'],indexed(b['texinfo'],texinfo,'texinfo')[17],'texdata')[3]]
        return water_fog(material(name)['settings'].get('parameters',{}))
    regions,region_tree=tree.regions(water)
    proxies=proxy_gap(materials.values())
    scene=dict(game='eye',name=name,title=world.get('message',name),format='source-bsp20',vertices=len(points),indices=len(indices),colors=True,
        lightmap=[width,height],groups=groups,model_library=models,instances=instances,viewpoints=views,skybox=sky,faces_drawn=faces,displacements_drawn=disps,
        faces_omitted=face_skips,model_failures=failures,omitted=omitted,npcs=len({r['entity'] for r in instances if r['npc']}),
        entity_data=b['entities'],entity_classes=dict(Counter(e.get('classname','') for e in entities)),lump_audit=b['audit'],game_lumps=gameaudit,pakfile=pak,
        missing=sorted({r['source'] for r in missing}),missing_reasons=missing,materials=list(materials.values()),
        lighting=dict(exposure=exposure,display_power=1,source='HDR lump53; VHV2; nearest leaf ambient cube',curve='fixed exposure, clamp to LDR, gamma2.2 (lightmaps and HDR sky faces)'),
        fog=fog_params(master) if master else None,sky3d=sky3d(camera) if camera else None,regions=regions,region_tree=region_tree,
        gaps=[*([f'3D sky not drawn: {sky_reason}'] if sky_reason not in ('','no sky_camera') else []),
              'Fog is the master env_fog_controller at spawn; fog inputs, lerps and trigger_fog are not run; underwater fog only where the water material sets $fogcolor/$fogstart/$fogend',
              *([proxies] if proxies else []), 'Water is a base texture/tint; particles (info_particle_system), spotlight beams and dynamic lights omitted; ropes hang still; decals skip displacements',
              'Terrain blends two base textures using displacement alpha; blend masks, detail textures and bump lighting are not evaluated',
              'NPCs use model-library pose/default bodygroups; FGD editor defaults when entity has no model; armor choices, attachments and scripts not simulated',
              'Spawners/makers create no preview; all omissions/missing models are listed', 'HDR fixed exposure; no game parity, automatic tonemapping, bump lighting or visibility/PVS culling'],
        sky_cameras=[e for e in entities if e.get('classname')=='sky_camera'],effects=effects['counts'],
        **({'sprites':effects['sprites']} if effects['sprites'] else {}),**({'movers':movers} if movers else {}))
    return scene,blob,images,Image.fromarray(atlas),b''.join(light_chunks)


def import_maps(install,output,replace=False,map_names=None,model_root=None,exposure=1):
    game=source_assets.game_directory(install);output=Path(output).resolve();textures=game_texture_dir(output.parent,'eye').resolve()
    if any(p==game.parent or game.parent in p.parents for p in (output,textures)):raise ValueError('Output must be outside read-only install')
    if not np.isfinite(exposure) or not 0<exposure<=16:raise ValueError('Exposure must be in (0,16]')
    files,inventory=source_assets.read_install(install);library=ModelLibrary(model_root or output.parent/'eye',files)
    definitions=fgd_models(game.parent/'modding/eye.fgd')
    maps=sorted(n for n in files if n.startswith('maps/') and n.endswith('.bsp'));selected={Path(n).stem.lower() for n in map_names} if map_names else None
    if selected and selected-{Path(n).stem for n in maps}:raise ValueError('Unknown selected map')
    index=[];results=[];report=dict(game='eye',imported=[],skipped=[],failed={},results=results,effective_maps=len(maps),
        sections=dict(Counter(section(Path(n).stem) for n in maps)),inventory=inventory,model_library=str(library.root))
    for path in maps:
        name=Path(path).stem;folder=output/'maps'/name
        if selected and name not in selected:continue
        row=dict(source=path);results.append(row)
        try:
            if not replace and (folder/'scene.json').is_file():
                scene=json.loads((folder/'scene.json').read_text());report['skipped'].append(name)
            else:
                scene,blob,images,atlas,lights=build_map(files[path].read(),name,files,library,definitions,exposure)
                for filename,image in images.items():write_texture(textures,filename,image)
                folder.mkdir(parents=True,exist_ok=True);(folder/'geometry.bin').write_bytes(blob);(folder/'lighting.bin').write_bytes(lights);atlas.save(folder/'lightmap.png')
                write_json_atomic(folder/'scene.json',scene);report['imported'].append(name)
            row.update(faces=scene['faces_drawn'],displacements=scene['displacements_drawn'],instances=len(scene['instances']),npcs=scene['npcs'],
                kinds=dict(Counter(r['classname'] for r in scene['instances'])),missing=scene['missing_reasons'],model_failures=scene['model_failures'],
                lighting=dict(Counter(r['lighting']['method'] for r in scene['instances'])),
                lighting_fallbacks=[dict(entity=r['entity'],reason=r['lighting']['vhv_fallback']) for r in scene['instances'] if 'vhv_fallback' in r['lighting']],
                skipped=dict(Counter(r['reason'] for r in scene['omitted'])),sizes={p.name:p.stat().st_size for p in folder.iterdir() if p.is_file()})
            index.append(dict(id=name,name=name,title=scene['title'],group=section(name)))
        except (ValueError,OSError,KeyError,IndexError,struct.error,zipfile.BadZipFile) as exc:row['error']=str(exc);report['failed'][path]=str(exc)
    if (output/'index.json').is_file():
        present={r['id'] for r in index};index.extend(r for r in json.loads((output/'index.json').read_text()) if r['id'] not in present)
    write_json_atomic(output/'index.json',sorted(index,key=lambda r:r['name']));write_json_atomic(output/'model-library.json',dict(root=str(library.root)))
    write_json_atomic(output/'import-report.json',report);return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',required=True,type=Path);parser.add_argument('--output',type=Path,default=LOCAL_DATA/'eye-maps')
    parser.add_argument('--models',type=Path);parser.add_argument('--map',action='append');parser.add_argument('--replace',action='store_true')
    parser.add_argument('--exposure',type=float,default=1)
    a=parser.parse_args();print(json.dumps(import_maps(a.install,a.output,a.replace,a.map,a.models,a.exposure),indent=2))

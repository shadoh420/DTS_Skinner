"""Local Jedi Outcast models; Ghoul2 v6 evaluated at one standing frame.

OpenJK (JK2 mode) and JK2MV are rules-only references, not bundled code.
python tools/import_jk2.py --install C:/Games/JediOutcast/GameData
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import zipfile

import numpy as np

try:
    from tools import import_rtcw as base
except ImportError:
    import import_rtcw as base
q3 = base.q3


class Game(base.Game):
    game_id, title = 'jk2', 'Jedi Outcast'
    sections, directory, mode_paks = ('jk2',), 'base', False
    shader_directory = 'shaders'
    loose_folders = base.Game.loose_folders+('shaders', 'ext_data')

    def __init__(self, install):
        root = Path(install)
        if (root/'GameData/base').is_dir(): root = root/'GameData'
        if not root.is_dir(): raise ValueError(self.title+' GameData/base folder not found')
        super().__init__(root, self.game_id)
        # Both retail modes prepend ascending PK3s after adding the directory.
        self.sources.sort(key=lambda source: source[0] == 'loose')
        self.search_order = [label for label, _ in self.sources]
        self.assets = {}
        for _, files in self.sources:
            for name, asset in files.items(): self.assets.setdefault(name, asset)

    def model_path(self, name):
        return base.checked_name(name)  # JK2 has MD3/GLM, not MDC preference.


def shader_maps(game):
    definitions = base.shader_definitions(game)
    adapted = {}
    for name, (body, stages, source) in definitions.items():
        adapted[name] = (body, [[['rgbgen', 'lightingdiffuse'] if
            [t.lower() for t in row] == ['rgbgen', 'lightingdiffuseentity'] else row
            for row in stage] for stage in stages], source)
    result = base.shader_maps(game, adapted)
    for name, entry in result.items():
        body, stages, _ = definitions[name]
        words = {row[0] for stage in stages for row in stage}
        if 'glow' in words: entry['limitations'].append('glow bloom omitted')
        if any([t.lower() for t in row] == ['rgbgen','lightingdiffuseentity'] for stage in stages for row in stage):
            entry['limitations'].append('lightingDiffuseEntity uses neutral entity color')
        for mode in sorted({row[1].lower() for stage in stages for row in stage if row[0]=='alphagen' and len(row)>1} - {'identity'}):
            entry['limitations'].append('alphaGen '+mode+' not evaluated')
    return result


class Textures(base.Textures):
    def __init__(self, game, output):
        super().__init__(game, output, shaders=shader_maps(game))


class CatalogTextures(base.CatalogTextures):
    def __init__(self, game, library):
        super().__init__(game, library, shaders=shader_maps(game))


def read_skin(raw):
    # JK2 retains _off suffixes (OpenJK's stripping is !JK2_MODE).
    return base.read_skin(raw)


def _range(offset, count, stride, end, minimum=0):
    base.span(offset, count, stride, end, minimum)


def _tree(parents, children):
    n = len(parents)
    for i, (parent, kids) in enumerate(zip(parents, children)):
        if parent < -1 or parent >= n or parent == i: raise ValueError('Invalid hierarchy parent')
        if len(set(kids)) != len(kids) or any(k < 0 or k >= n or parents[k] != i for k in kids):
            raise ValueError('Invalid hierarchy children')
        if parent >= 0 and i not in children[parent]: raise ValueError('Missing hierarchy child')
    # Iterative traversal also rejects cycles without risking Python recursion.
    ordered = []; pending = [i for i, p in enumerate(parents) if p == -1]
    while pending:
        i = pending.pop(); ordered.append(i); pending.extend(children[i])
        if len(ordered) > n: raise ValueError('Cyclic hierarchy')
    if len(ordered) != n: raise ValueError('Cyclic hierarchy')
    return ordered


def _matrix(values):
    base.finite(values)
    out = np.eye(4); out[:3] = np.asarray(values).reshape(3,4)
    return out


def _quat_matrix(packed):
    # Seven unsigned shorts encode wxyz in [-2,2], then xyz in [-512,512).
    w, x, y, z = (value/16383-2 for value in packed[:4])
    matrix = np.eye(4)
    cross = np.array([[0,-z,y],[z,0,-x],[-y,x,0]])
    matrix[:3,:3] += 2*(w*cross+cross@cross)
    matrix[:3,3] = np.array(packed[4:])/64-512
    return matrix


def _root_rotation():
    # G2's model-space root is a quarter-turn about Z, despite its identity name.
    matrix = np.eye(4); matrix[:2,:2] = [[0,-1],[1,0]]
    return matrix


def default_skeleton():
    # *default.gla is a program-provided, one-bone identity animation, not a
    # fallback for arbitrary missing skeletons. No engine byte array is copied.
    return dict(frame_count=1, frame=0, names=['root'], matrices=[_root_rotation()],
                bone_poses=[_root_rotation()], source='*default')


def read_gla(raw, frame=0):
    if len(raw) < 100 or raw[:4] != b'2LGA' or struct.unpack_from('<i',raw,4)[0] != 6:
        raise ValueError('Expected GLA 2LGA version 6')
    scale, nf, of, nb, pool, skel, end = struct.unpack_from('<f6i',raw,72)
    if not (math.isfinite(scale) and 0 < nf <= 1000000 and 0 < nb <= 1024 and 0 <= frame < nf and 100 <= end <= len(raw)):
        raise ValueError('Invalid GLA header/frame')
    _range(100,nb,4,end); _range(of,nf*nb,3,end,100+4*nb)
    if not (100+4*nb <= skel <= of and of+nf*nb*3 <= pool <= end):
        raise ValueError('Invalid GLA block ordering')
    parents=[]; children=[]; names=[]; poses=[]; local=[]
    for i, (offset,) in enumerate(struct.iter_unpack('<i',raw[100:100+nb*4])):
        at = 100+offset; _range(at,1,172,of,skel)
        names.append(base.string(raw[at:at+64]).lower())
        parent = struct.unpack_from('<i',raw,at+68)[0]; parents.append(parent)
        poses.append(_matrix(struct.unpack_from('<12f',raw,at+72)))
        _matrix(struct.unpack_from('<12f',raw,at+120))  # Validate the stored inverse too.
        nc = struct.unpack_from('<i',raw,at+168)[0]
        if not 0 <= nc < nb: raise ValueError('Invalid GLA child count')
        _range(at+172,nc,4,of)
        children.append(list(struct.unpack_from('<'+'i'*nc,raw,at+172)))
        ref = int.from_bytes(raw[of+(frame*nb+i)*3:of+(frame*nb+i+1)*3],'little')
        _range(pool+ref*14,1,14,end,pool)
        local.append(_quat_matrix(struct.unpack_from('<7H',raw,pool+ref*14)))
    matrices = [None]*nb
    for i in _tree(parents,children):
        matrices[i] = (_root_rotation() if parents[i] == -1 else matrices[parents[i]]) @ local[i]
        base.finite(matrices[i].ravel().tolist())
    # GLA animation matrices already act on mesh bind-space vertices. The base
    # pose is used to locate a bone bolt, not inverted again during skinning.
    bone_poses=[a@b for a,b in zip(matrices,poses)]
    for pose in bone_poses:base.finite(pose.ravel().tolist())
    return dict(frame_count=nf, frame=frame, names=names, matrices=matrices,
                bone_poses=bone_poses, scale=scale)


def glm_header(raw):
    if len(raw)<164 or raw[:4]!=b'2LGM' or struct.unpack_from('<i',raw,4)[0]!=6:
        raise ValueError('Expected GLM 2LGM version 6')
    _, nb, nl, ol, ns, oh, end = struct.unpack_from('<7i',raw,136)
    if not (0<nb<=1024 and 0<nl<=16 and 0<ns<=4096 and 164<=end<=len(raw)):
        raise ValueError('Invalid GLM header')
    _range(164,ns,4,end); _range(oh,ns,144,end,164+ns*4)
    _range(ol,1,4+ns*4,end,oh+ns*144)
    return dict(animation=base.string(raw[72:136]),bones=nb,lods=nl,lod_offset=ol,
                surfaces=ns,hierarchy=oh,end=end)


def read_glm(raw, skeleton, skin=None, surface_flags=None, *, bone_remap=None,
             surface_name=None, surface_bolt=None):
    h=glm_header(raw); ns=h['surfaces']; end=h['end']; skin=skin or {}; surface_flags=surface_flags or {}
    if bone_remap is None:
        if h['bones'] != len(skeleton['matrices']): raise ValueError('GLM/GLA bone count mismatch')
        bone_remap=range(h['bones'])
    if len(bone_remap)!=h['bones'] or any(i<0 or i>=len(skeleton['matrices']) for i in bone_remap):
        raise ValueError('Invalid GLM bone remap')
    hierarchy=[];parents=[];children=[]
    for offset, in struct.iter_unpack('<i',raw[164:164+ns*4]):
        at=164+offset;_range(at,1,144,h['lod_offset'],h['hierarchy'])
        name=base.string(raw[at:at+64]).lower();flags=struct.unpack_from('<I',raw,at+64)[0]
        if surface_name:name=surface_name(name)
        shader=base.string(raw[at+68:at+132]);parent,nc=struct.unpack_from('<2i',raw,at+136)
        if not 0<=nc<ns:raise ValueError('Invalid GLM child count')
        _range(at+144,nc,4,h['lod_offset'])
        parents.append(parent);children.append(list(struct.unpack_from('<'+'i'*nc,raw,at+144)))
        hierarchy.append(dict(name=name,flags=surface_flags.get(name,flags),shader=skin.get(name,'') if skin else shader))
    order=_tree(parents,children);blocked=set()
    for i in order:
        p=parents[i]
        if p in blocked or p>=0 and hierarchy[p]['flags']&256:blocked.add(i)
    # Validate each LOD envelope; only LOD 0 geometry is evaluated.
    lod=h['lod_offset']; first_end=None
    for _ in range(h['lods']):
        _range(lod,1,4,end);size=struct.unpack_from('<i',raw,lod)[0]
        _range(lod,1,size,end);_range(lod+4,ns,4,lod+size)
        if size<4+ns*4:raise ValueError('Invalid GLM LOD size')
        if first_end is None:first_end=lod+size
        lod+=size
    surfaces=[];hidden=[];warnings=[];seen=set();bolts={};lod=h['lod_offset']
    for off, in struct.iter_unpack('<i',raw[lod+4:lod+4+ns*4]):
        at=lod+4+off;_range(at,1,40,first_end,lod+4+ns*4)
        _,idx,back,nv,ov,nt,ot,nr,ob,size=struct.unpack_from('<10i',raw,at)
        if not (0<=idx<ns and idx not in seen and back==-at and 0<=nv<=65536 and 0<=nt<=131072 and 0<=nr<=32 and size>=40):
            raise ValueError('Invalid GLM surface header')
        seen.add(idx);_range(at,1,size,first_end)
        for offset,count,stride in ((ov,nv,40),(ot,nt,12),(ob,nr,4)):
            if count:_range(offset,count,stride,size,40)
        refs=struct.unpack_from('<'+'i'*nr,raw,at+ob) if nr else ()
        if any(b<0 or b>=h['bones'] for b in refs):raise ValueError('Invalid GLM bone reference')
        indices=list(struct.unpack_from('<'+'i'*(nt*3),raw,at+ot)) if nt else []
        if any(v<0 or v>=nv for v in indices):raise ValueError('Invalid GLM triangle index')
        info=hierarchy[idx];vertices=[];normals=[]
        for v in range(nv):
            values=struct.unpack_from('<6fI4B',raw,at+ov+v*32);base.finite(values[:6])
            packed=values[6];count=(packed>>30)+1;total=0;matrix=np.zeros((4,4))
            for j in range(count):
                bone=(packed>>(j*5))&31
                if bone>=nr:raise ValueError('Invalid GLM vertex bone index')
                weight=1-total if j==count-1 else (values[7+j]|((packed>>(12+j*2))&768))/1023
                if weight < -1e-6:raise ValueError('Invalid GLM vertex weights')
                total+=weight;matrix+=skeleton['matrices'][bone_remap[refs[bone]]]*weight
            point=matrix@np.array([*values[3:6],1]);normal=matrix[:3,:3]@values[:3]
            length=np.linalg.norm(normal)
            if length:normal/=length
            vertices.extend(base.finite(point[:3].tolist()));normals.extend(base.finite(normal.tolist()))
        uvs=list(struct.unpack_from('<'+'f'*(nv*2),raw,at+ov+nv*32)) if nv else [];base.finite(uvs)
        if surface_bolt and info['flags']&1 and nv>=3:
            try:bolts[info['name']]=surface_bolt(vertices[:9])
            except ValueError as exc:warnings.append('Unavailable surface bolt '+info['name']+': '+str(exc))
        reason='ancestor suppresses descendants' if idx in blocked else 'surface flags '+str(info['flags']) if info['flags'] else 'skin hides surface' if info['shader'].lower() in ('off','*off','nodraw') else 'empty surface' if not nv or not nt else None
        if reason:hidden.append(dict(surface=info['name'],reason=reason));continue
        if skin and info['name'] not in skin:
            warnings.append('Skin omits surface '+info['name']+'; renderer default material')
        surfaces.append(dict(name=info['name'],shaders=[info['shader']],vertices=vertices,normals=normals,uvs=uvs,indices=indices))
    tags={n:tuple(m[:3,3])+tuple(m[:3,:3].T.flat) for n,m in zip(skeleton['names'],skeleton['bone_poses'])}
    tags.update(bolts)
    return dict(surfaces=surfaces,tags=tags,frame_count=skeleton['frame_count'],hidden=hidden,warnings=warnings)


def idle_pose(game, animation):
    path=str(Path(animation).parent/'animation.cfg').replace('\\','/')
    if path in game.assets:
        raw=game.read(path)
        if len(raw)>1024*1024:raise ValueError('Animation config exceeds limit')
        text=re.sub(r'/\*.*?\*/|//[^\r\n]*','',q3.read_text(raw),flags=re.S)
        match=re.search(r'(?im)^\s*BOTH_STAND1\s+(\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?[\d.]+)',text)
        if match:return int(match[1]),path+': first frame of BOTH_STAND1'
    return 0,'Frame 0: no BOTH_STAND1 in skeleton animation.cfg'


def load_model(game, name, skin=None, frame=None):
    name=game.model_path(name);raw=game.read(name)
    if name.endswith('.md3'):
        model=q3.read_md3(raw,0 if frame is None else frame)
        warnings=[]
        if skin:
            for surface in model['surfaces']:
                if surface['name'] not in skin:
                    surface['shaders']=[''];warnings.append('Skin omits surface '+surface['name']+'; renderer default material')
        return model,dict(frame=0 if frame is None else frame,pose='MD3 frame',warnings=warnings),raw
    h=glm_header(raw);animation=q3.key_name(h['animation'])
    if animation=='*default':
        if frame not in (None,0):raise ValueError('Built-in skeleton only has frame 0')
        skeleton=default_skeleton();frame=0;reason='Built-in *default one-bone skeleton'
    else:
        path=base.checked_name(animation+'.gla')
        chosen,reason=idle_pose(game,path)
        if frame is None:frame=chosen
        else:reason='Explicit frame '+str(frame)
        skeleton=read_gla(game.read(path),frame)
    model=read_glm(raw,skeleton,skin)
    return model,dict(frame=frame,pose=reason,animation=animation,warnings=model['warnings'],hidden=model['hidden']),raw


def category(name):
    if name.startswith('models/players/'):
        folder=name.split('/')[2]
        if folder in ('atst','glider'):return 'Vehicles'
        if folder in ('gonk','mark1','mark2','probe','r2d2','r5d2','protocol','sentry','interrogator','mouse'):return 'Droids'
        if folder in ('minemonster','howler'):return 'Creatures'
        return 'Characters'
    if '/weapons' in name:return 'Weapons'
    if '/chunks/' in name:return 'Chunks / gibs'
    if '/items/' in name or '/force_holocrons/' in name or '/flags/' in name:return 'Items / pickups'
    if '/map_objects/' in name:return 'Map objects'
    return 'Other models'


def model_specs(game):
    for name in sorted(n for n in game.assets if n.endswith(('.glm','.md3'))):
        stem=name.rsplit('.',1)[0]
        skins=sorted(n for n in game.assets if n.endswith('.skin') and
                     (n==stem+'.skin' or n.startswith(stem+'_')) and not Path(n).name.startswith('model_fpls'))
        for skin in skins or ['']:yield name,skin


def catalog_parts(game, source, skin):
    surfaces=read_skin(game.read(skin))['surfaces'] if skin else {}
    model,metadata,raw=load_model(game,source,surfaces)
    if not model['surfaces']:raise ValueError('Model has no visible surfaces (empty/off/bolt only)')
    return [dict(name=source,model=model,skin=surfaces)],metadata,raw


def browser_excluded(name):
    return Path(name).name.startswith('model_fpls')


def import_catalog(install, output, model_filter=None, *, rules=None):
    rules=rules or sys.modules[__name__]
    output=Path(output);(output/'model_json').mkdir(parents=True,exist_ok=True)
    game=rules.Game(install)
    if not any(n.endswith(('.glm','.md3')) for n in game.assets):raise ValueError('No GLM or MD3 models found in the selected '+game.title+' folder')
    library=getattr(rules,'TextureLibrary',base.TextureLibrary)(output,[game]);resolver=rules.CatalogTextures(game,library)
    catalog=[];results=[];seen={};used=set()
    for source,skin in rules.model_specs(game):
        if model_filter and model_filter.lower() not in source:continue
        ident=q3.asset_name(source+('#'+skin if skin else ''));row=dict(source=source,skin=skin,model=ident)
        label=source.removeprefix('models/').rsplit('.',1)[0]+(' / '+Path(skin).stem if skin else '')
        item=dict(game=game.game_id,model_name=ident,display_name=label,category=rules.category(source),source=game.assets[source]['source']+':'+source,texture_name='',status='ready')
        try:
            parts,metadata,raw=rules.catalog_parts(game,source,skin)
            data=q3.combine(parts,resolver);data.update(game=game.game_id,metadata=metadata)
            data['warnings']=sorted(set(data['warnings']+metadata['warnings']))
            digest=hashlib.sha256(json.dumps([hashlib.sha256(raw).hexdigest(),metadata['frame']]+[data[k] for k in ('vertices','normals','uvs','indices','groups','material_textures','material_settings')],sort_keys=True).encode()).hexdigest()
            if digest in seen:
                row.update(status='duplicate',model=seen[digest],reason='Byte-identical model, same pose and materials');results.append(row);continue
            seen[digest]=ident;used.update(data['material_textures'])
            getattr(rules,'write_model_data',q3.json_write)(output/'model_json'/(ident+'.json'),data)
            item.update(texture_name=data['material_textures'][0],warnings=data['warnings']);row.update(status='ready',metadata=metadata)
        except (ValueError,OSError,KeyError,struct.error,zipfile.BadZipFile) as exc:
            row.update(status='skipped',reason=str(exc));item.update(status='skipped',reason=str(exc))
        catalog.append(item);results.append(row)
    skins=[]
    for name in sorted(n for n in game.assets if n.endswith('.skin')):
        row=dict(source=name,browser_excluded=rules.browser_excluded(name))
        try:
            sk=rules.read_skin(game.read(name));refs=[]
            for surface,shader in sk['surfaces'].items():
                if shader in ('off','*off','nodraw'):refs.append(dict(surface=surface,status='hidden'));continue
                resolver.resolve(shader);refs.append(dict(surface=surface,**resolver.results[shader]))
            row.update(status='empty' if sk.get('empty') else 'read',surfaces=refs)
        except (ValueError,OSError,zipfile.BadZipFile) as exc:row.update(status='unreadable',reason=str(exc))
        skins.append(row)
    q3.json_write(output/'catalog.json',catalog)
    report=dict(entries=len(catalog),ready=sum(r['status']=='ready' for r in results),skipped=sum(r['status']=='skipped' for r in results),duplicates=sum(r['status']=='duplicate' for r in results),results=results,search_order=game.search_order,issues=game.issues,skin_results=skins,texture_results=list(resolver.results.values()),texture_library=library.finish(used),categories=dict(Counter(i['category'] for i in catalog)),unresolved_skin_reasons=dict(Counter(w for r in resolver.results.values() if r['status']=='unresolved' for w in r['warnings'])),notes=['NPC-only surfOn/surfOff, scale and RGB looks are not separate catalog entries; character skins are included.', 'Highest LOD, static standing pose; animation clips and surface-bolt attachments are not exported.'])
    report.update(textures=report['texture_library']['after'],skin_resolution=dict(Counter(r['status'] for sk in skins for r in sk.get('surfaces',[]))),skin_file_status=dict(Counter(s['status'] for s in skins)))
    for record in report['texture_results']:record['catalog_used']=record['texture'] in used
    q3.json_write(output/'import-report.json',report);return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install',required=True)
    parser.add_argument('--output',type=Path,default=q3.LOCAL_DATA/'jk2');parser.add_argument('--filter')
    args=parser.parse_args();print(json.dumps(import_catalog(args.install,args.output,args.filter),indent=2))

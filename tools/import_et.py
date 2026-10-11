"""Local Enemy Territory models: original MDM/MDX readers, shared RTCW assets.

Reference rules: ET: Legacy classic renderer; no engine implementation bundled.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import zipfile

try:
    from tools import import_rtcw as rtcw
except ImportError:
    import import_rtcw as rtcw
q3 = rtcw.q3


class Game(rtcw.Game):
    sections, directory, mode_paks = ('et',), 'etmain', False
    loose_folders = rtcw.Game.loose_folders+('characters','animations')

    def __init__(self, install, section='et'):
        super().__init__(install, section)

    def read(self, name):
        name=rtcw.checked_name(name)
        if name.endswith(('.char','.anim','.aninc')) and name in self.assets:
            return self.assets[name]['read'](max_bytes=1024*1024)
        return super().read(name)


def et_shader_settings(shaders):
    for entry in shaders.values():
        if any(w.startswith('implicit directive compatibility') for w in entry['limitations']):
            entry['limitations']=[w for w in entry['limitations'] if not w.startswith('implicit directive compatibility')]
            entry['settings']['depthWrite']=True  # ET ParseShader's implicitBlend writes depth too.
    return shaders


def shader_maps(game):
    return et_shader_settings(rtcw.shader_maps(game))


class Textures(rtcw.Textures):
    def __init__(self,game,output):
        super().__init__(game,output);et_shader_settings(self.shaders)


class CatalogTextures(rtcw.CatalogTextures):
    def __init__(self,game,library):
        super().__init__(game,library);et_shader_settings(self.shaders)


def read_mdx(raw, frame=0):
    if len(raw)<96 or raw[:4]!=b'MDXW' or struct.unpack_from('<i',raw,4)[0]!=2:
        raise ValueError('Expected MDXW version 2')
    nf,nb,of,ob,torso,end=struct.unpack_from('<6i',raw,72)
    if not (0<nf<=65536 and 0<nb<=128 and 0<=frame<nf and 0<=torso<nb and 96<=end<=len(raw)):
        raise ValueError('Invalid MDX header/frame')
    rtcw.span(of,nf,52+nb*12,end,96);rtcw.span(ob,nb,80,end,96)
    at=of+frame*(52+nb*12);rtcw.finite(struct.unpack_from('<13f',raw,at))
    root=struct.unpack_from('<3f',raw,at+40);bones={};active=set()
    def bone(i):
        if i in bones:return bones[i]
        if i in active:raise ValueError('Cyclic MDX bone hierarchy')
        active.add(i)
        parent,weight,distance,flags=struct.unpack_from('<iffi',raw,ob+i*80+64)
        rtcw.finite((weight,distance))
        if not (-1<=parent<nb and parent!=i and 0<=weight<=1 and distance>=0):raise ValueError('Invalid MDX bone')
        a=struct.unpack_from('<6h',raw,at+52+i*12)
        matrix=rtcw.axis([v*math.tau/65536 for v in a[:3]])
        pos=root
        if parent>=0:
            # Classic YD_INGLES offset direction uses the renderer's sine table.
            def sine(v,quarter=0):return math.sin((((v&65535)>>4)+quarter &4095)*math.tau/4095)
            direction=(sine(a[4],1024)*sine(a[5],1024),sine(a[4],1024)*sine(a[5]),-sine(a[4]))
            if 0<weight<1:
                length=math.sqrt(sum(d*d for d in direction));direction=tuple(d/length for d in direction)
            pos=tuple(p+distance*d for p,d in zip(bone(parent)[1],direction))
        bones[i]=(matrix,pos);active.remove(i)
        return bones[i]
    for i in range(nb):bone(i)
    return dict(bones=bones,frame_count=nf,bone_names=[rtcw.string(raw[ob+i*80:ob+i*80+64]) for i in range(nb)])


def read_mdm(raw, skeleton):
    if len(raw)<100 or raw[:4]!=b'MDMW' or struct.unpack_from('<i',raw,4)[0]!=3:
        raise ValueError('Expected MDMW version 3')
    rtcw.finite(struct.unpack_from('<2f',raw,72))
    ns,os,nt,ot,end=struct.unpack_from('<5i',raw,80);bones=skeleton['bones'];nb=len(bones)
    if not (0<=ns<=32 and 0<=nt<=128 and 100<=end<=len(raw)):raise ValueError('Invalid MDM header')
    try:surfaces=rtcw.skeletal_surfaces(raw,os,ns,end,bones,vertex_header=24,header_size=100)
    except ValueError as exc:raise ValueError(str(exc).replace('MDS','MDM')) from exc
    tags={};at=ot
    for _ in range(nt):
        rtcw.span(at,1,128,end,100)
        axis=rtcw.finite(struct.unpack_from('<9f',raw,at+64));bi=struct.unpack_from('<i',raw,at+100)[0]
        offset=rtcw.finite(struct.unpack_from('<3f',raw,at+104));nr,refs,size=struct.unpack_from('<3i',raw,at+116)
        if not (0<=bi<nb and 0<=nr<=nb and size>=128):raise ValueError('Invalid MDM tag')
        rtcw.span(at,1,size,end,100);rtcw.span(refs,nr,4,size,128)
        if any(not 0<=b<nb for b in struct.unpack_from('<'+'i'*nr,raw,at+refs)):raise ValueError('Invalid MDM tag bone reference')
        mat,pos=bones[bi]
        rotate=lambda v:tuple(sum(x*y for x,y in zip(row,v)) for row in mat)
        origin=tuple(x+y for x,y in zip(pos,rotate(offset)))
        transformed=sum((rotate(axis[i:i+3]) for i in (0,3,6)),())
        tags[rtcw.string(raw[at:at+64])]=(*origin,*transformed);at+=size
    return dict(surfaces=surfaces,tags=tags,frame_count=skeleton['frame_count'])


def text(raw):
    return re.sub(r'/\*.*?\*/|//[^\r\n]*','',q3.read_text(raw),flags=re.S)


def read_char(raw):
    tokens=[v.strip('"') for v in re.findall(r'"[^"\r\n]*"|[^\s{}]+|[{}]',text(raw))]
    if len(tokens)<3 or [v.lower() for v in tokens[:2]]!=['characterdef','{'] or tokens[-1]!='}':raise ValueError('Invalid characterDef')
    values=tokens[2:-1]
    if len(values)%2:raise ValueError('Incomplete characterDef field')
    result={k.lower():v for k,v in zip(values[::2],values[1::2])}
    allowed={'mesh','skin','animationgroup','animationscript','undressedcorpsemodel','undressedcorpseskin','hudhead','hudheadskin','hudheadanims'}
    if result.keys()-allowed:raise ValueError('Unknown characterDef field: '+', '.join(sorted(result.keys()-allowed)))
    if not all(result.get(k) for k in ('mesh','skin','animationgroup','animationscript')):raise ValueError('Character lacks mesh/skin/animation definition')
    return {k:rtcw.checked_name(v) for k,v in result.items()}


def block(source, name):
    match=re.search(r'\b'+name+r'\s*\{',source,re.I)
    if not match:return ''
    start=match.end();depth=1
    for i in range(start,len(source)):
        depth+=(source[i]=='{')-(source[i]=='}')
        if depth==0:return source[start:i]
    raise ValueError('Unclosed animation block')


def standing_pose(game, definition):
    script=text(game.read(definition['animationscript']))
    idle=block(block(script,r'state\s+combat'),'idle')
    chosen=block(idle,r'weapons\s+none')
    match=re.search(r'\bboth\s+(\w+)',chosen,re.I)
    animation=match[1].lower() if match else None
    group=text(game.read(definition['animationgroup']));animations={};first=None
    for model,body in re.findall(r'animfile\s+"?([^\s"{}]+)"?\s*\{([^{}]*)\}',group,re.I):
        first=first or model
        for include in re.findall(r'#include\s+"([^"]+)"',body):
            body=body.replace('#include "'+include+'"',text(game.read(include)))
        for name,frame in re.findall(r'(?m)^\s*(\w+)\s+(\d+)\s+-?\d+\s+-?\d+\s+[\d.]+',body):animations[name.lower()]=(model,int(frame))
    if animation in animations:
        model,frame=animations[animation]
        return model,frame,f"{definition['animationscript']}: combat idle / weapons none / both {animation}; first frame {frame}"
    if not first:raise ValueError('Animation group has no MDX files')
    return first,0,'Frame 0 fallback: no recognized whole-body unarmed standing animation'


def character_parts(game, definition, naked=False):
    warnings=[];parts=[];used=[]
    def skin(path):
        if path not in game.assets:
            warnings.append('Missing skin: '+path+'; embedded shaders');return dict(surfaces={},models={})
        used.append(path);return rtcw.read_skin(game.read(path))
    mdx,frame,reason=standing_pose(game,definition);used.append(mdx)
    skeleton=read_mdx(game.read(mdx),frame)
    mesh=definition['undressedcorpsemodel'] if naked else definition['mesh']
    suffix=definition['undressedcorpseskin'] if naked else definition['skin']
    body_skin=skin(mesh.rsplit('.',1)[0]+'_'+suffix+'.skin')
    def part(path,sk,tags=(),at=0):
        path=game.model_path(path);raw=game.read(path);used.append(path)
        model=read_mdm(raw,skeleton) if path.endswith('.mdm') else rtcw.read_model(raw,at)
        parts.append(dict(name=path,model=model,skin=sk,tags=tags,digest=hashlib.sha256(raw).hexdigest(),frame=frame if path.endswith('.mdm') else at))
        return model
    body=part(mesh,body_skin['surfaces']);head=None;chain=()
    if definition.get('hudhead'):
        try:
            chain=(body['tags']['tag_head'],)
            headframe=0
            if definition.get('hudheadanims'):
                rows=re.findall(r'(?m)^\s*(\d+)\s+\d+\s+[\d.]+',text(game.read(definition['hudheadanims'])))
                if rows:headframe=int(rows[0])  # HD_IDLE1.
            head=part(definition['hudhead'],skin(definition.get('hudheadskin',''))['surfaces'],chain,headframe)
        except (ValueError,KeyError,OSError,zipfile.BadZipFile) as exc:warnings.append('Head: '+str(exc))
    if not naked:
        for key,path in body_skin['models'].items():
            if key=='md3_rank':warnings.append('Rank badge omitted at initial rank zero');continue
            tag=rtcw.ACCESSORIES.get(key)
            if not tag:warnings.append('Unknown accessory: '+key);continue
            try:
                tags=(head['tags'][tag],)+chain if key.startswith('md3_hat') else (body['tags'][tag],)
                part(path,skin(path.rsplit('.',1)[0]+'_'+suffix+'.skin')['surfaces'],tags)
            except (ValueError,KeyError,OSError,TypeError,zipfile.BadZipFile) as exc:warnings.append('Accessory '+key+': '+str(exc))
    return parts,dict(pose=reason,frame=frame,mdx=mdx,used=used,warnings=warnings)


def category(name):
    if '/tanks' in name or '/blitz' in name:return 'Vehicles'
    return rtcw.category(name)


def import_catalog(install, output, model_filter=None):
    output=Path(output);(output/'model_json').mkdir(parents=True,exist_ok=True)
    game=Game(install);library=rtcw.TextureLibrary(output,[game]);resolver=CatalogTextures(game,library)
    catalog=[];results=[];seen={};used=set();claimed=set();specs=[];skins=[]
    for name in sorted(n for n in game.assets if n.endswith('.char')):
        try:
            definition=read_char(game.read(name));claimed.add(definition['mesh'])
            specs.append((name,definition,False))
            if definition.get('undressedcorpsemodel'):
                claimed.add(definition['undressedcorpsemodel']);specs.append((name,definition,True))
        except (ValueError,OSError,zipfile.BadZipFile) as exc:results.append(dict(source=name,status='skipped',reason=str(exc)))
    # Retail MDC-first order; Legacy RE_RegisterModel documents its MD3-first
    # change in 2.76. MDX animation files are not standalone meshes.
    for name in sorted(n for n in game.assets if n.endswith(('.md3','.mdc','.mdm'))):
        if name in claimed:continue
        if name.endswith('.md3') and name[:-4]+'.mdc' in game.assets:
            results.append(dict(source=name,status='superseded',reason='MDC sibling wins model lookup'));continue
        specs.append((name,None,False))
    for source,definition,naked in specs:
        if model_filter and model_filter.lower() not in source:continue
        ident=q3.asset_name(source+('#naked' if naked else ''));row=dict(source=source,model=ident)
        cat=('Characters / Undressed' if naked else 'Characters') if definition else category(source)
        item=dict(game='et',model_name=ident,display_name=source.removeprefix('characters/').removesuffix('.char')+(' / undressed' if naked else ''),category=cat,source=game.assets[source]['source']+':'+source,texture_name='',status='ready')
        try:
            if definition:parts,metadata=character_parts(game,definition,naked)
            else:
                if source.endswith('.mdm'):raise ValueError('MDM has no character/MDX association')
                raw=game.read(source);sk=source[:-4]+'.skin'
                parts=[dict(name=source,model=rtcw.read_model(raw),digest=hashlib.sha256(raw).hexdigest(),frame=0,skin=rtcw.read_skin(game.read(sk))['surfaces'] if sk in game.assets else {})]
                metadata=dict(pose='Frame 0',warnings=[])
            data=q3.combine(parts,resolver);data.update(game='et',metadata=metadata)
            data['warnings']=sorted(set(data['warnings']+metadata['warnings']))
            digest=hashlib.sha256(json.dumps([[(p['digest'],p['frame']) for p in parts]]+[data[k] for k in ('vertices','normals','uvs','indices','groups','material_textures','material_settings')],sort_keys=True).encode()).hexdigest()
            if digest in seen:row.update(status='duplicate',model=seen[digest],reason='Identical parts, pose and materials');results.append(row);continue
            seen[digest]=ident;used.update(data['material_textures'])
            q3.json_write(output/'model_json'/(ident+'.json'),data)
            item.update(texture_name=data['material_textures'][0],warnings=data['warnings']);row.update(status='ready',metadata=metadata)
        except (ValueError,OSError,KeyError,struct.error,zipfile.BadZipFile) as exc:
            row.update(status='skipped',reason=str(exc));item.update(status='skipped',reason=str(exc))
        catalog.append(item);results.append(row)
    for name in sorted(n for n in game.assets if n.endswith('.skin')):
        row=dict(source=name)
        try:
            sk=rtcw.read_skin(game.read(name));refs=[]
            for surface,shader in sk['surfaces'].items():
                if shader in ('off','*off','nodraw'):refs.append(dict(surface=surface,status='hidden'));continue
                resolver.resolve(shader);refs.append(dict(surface=surface,**resolver.results[shader]))
            row.update(status='empty' if sk.get('empty') else 'read',surfaces=refs)
        except (ValueError,OSError,zipfile.BadZipFile) as exc:row.update(status='unreadable',reason=str(exc))
        skins.append(row)
    q3.json_write(output/'catalog.json',catalog)
    report=dict(entries=len(catalog),ready=sum(r['status']=='ready' for r in results),skipped=sum(r['status']=='skipped' for r in results),duplicates=sum(r['status']=='duplicate' for r in results),results=results,search_order=game.search_order,issues=game.issues,skin_results=skins,texture_results=list(resolver.results.values()),texture_library=library.finish(used),categories=dict(Counter(i['category'] for i in catalog)),unresolved_skin_reasons=dict(Counter(w for r in resolver.results.values() if r['status']=='unresolved' for w in r['warnings'])))
    report.update(textures=report['texture_library']['after'],
        skin_resolution=dict(Counter(ref['status'] for skin in skins for ref in skin.get('surfaces',[]))),
        skin_file_status=dict(Counter(skin['status'] for skin in skins)))
    for record in report['texture_results']:record['catalog_used']=record['texture'] in used
    q3.json_write(output/'import-report.json',report);return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install',required=True);parser.add_argument('--output',type=Path,default=q3.LOCAL_DATA/'et');parser.add_argument('--filter')
    args=parser.parse_args();print(json.dumps(import_catalog(args.install,args.output,args.filter),indent=2))

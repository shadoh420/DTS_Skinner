"""Shared Doom 3 pak, declaration, material and texture resolution for models/maps."""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
import hashlib
import io
import math
import re
import zipfile

import numpy as np
from PIL import Image

if __package__:
    from .texture_storage import write_texture
else:
    from texture_storage import write_texture


def path_name(value):
    return re.sub('/+', '/', value.replace('\\','/')).lower().strip()


@dataclass
class Asset:
    archive: zipfile.ZipFile
    info: zipfile.ZipInfo
    campaign: str

    def read(self):
        return self.archive.read(self.info)

    @property
    def origin(self):
        return self.campaign+'/'+Path(self.archive.filename).name


class Install:
    """Separate effective namespaces: RoE overlays base, base never sees RoE."""
    def __init__(self, root, policy=None):
        self.root, self.archives, self.files = Path(root), [], {}
        try:
            for campaign in (policy.campaigns if policy else ('base','d3xp')):
                files = dict(self.files.get('base',{})) if campaign=='d3xp' else {}
                for path in (policy.archives(self.root/campaign) if policy else sorted((self.root/campaign).glob('pak*.pk4'))):
                    archive = zipfile.ZipFile(path); self.archives.append(archive)
                    for info in archive.infolist():
                        if not info.is_dir(): files[path_name(info.filename)] = Asset(archive,info,campaign)
                self.files[campaign] = files
            if (not any(self.files.values()) if policy else not self.files['base']):
                raise ValueError('No game PK4 archives in install' if policy else 'No base/pak*.pk4 in Doom 3 install')
        except Exception:
            self.close(); raise

    def close(self):
        for archive in self.archives: archive.close()

    def __enter__(self): return self
    def __exit__(self,*args): self.close()


def lex(text):
    # Preserve newlines for declaration statements; comments inside strings survive.
    # idLexer also starts a comment mid-token: ".75//Parm0" is the number .75.
    return [m[0][1:-1] if m[0].startswith('"') else m[0]
            for m in re.finditer(r'"[^"\r\n]*"|/\*[\s\S]*?\*/|//[^\r\n]*|\n|(?:[^\s{}(),/]|/(?![/*]))+|[{}(),]',text)
            if not m[0].startswith(('//','/*'))]


def declarations(tokens):
    at=0
    while at<len(tokens):
        header=[]
        while at<len(tokens) and tokens[at]!='{':
            if tokens[at]!='\n': header.append(tokens[at])
            at+=1
        if at==len(tokens): break
        start=at+1; depth=1; at+=1
        while at<len(tokens) and depth:
            depth += (tokens[at]=='{')-(tokens[at]=='}'); at+=1
        if depth: raise ValueError('Unclosed declaration')
        yield header,tokens[start:at-1]


def compact(tokens): return [t for t in tokens if t!='\n']


class Declarations:
    def __init__(self, files, policy=None):
        self.policy=policy
        self.warnings=[]
        guides=policy.guides(files) if policy else {}
        self.entities,self.models,self.skins,self.materials = {},{},{},{}
        self.sources = {}
        for filename,asset in sorted(files.items(),key=lambda item:(item[1].campaign=='d3xp',item[0])):
            ext=Path(filename).suffix
            if ext not in ('.def','.skin','.mtr'): continue
            tokens=lex(asset.read().decode('latin1'))
            if policy: tokens=policy.tokens(tokens,ext,filename,guides,self.warnings)
            for header,body in declarations(tokens):
                if not header: continue
                kind=header[0].lower(); name=path_name(header[-1]); t=compact(body)
                if ext=='.def' and kind=='entitydef':
                    value=dict(zip(t[::2],t[1::2])); value={k.lower():v for k,v in value.items()}
                    self.entities[name]=value; self.sources[('entity',name)]=(filename,asset.campaign)
                elif ext=='.def' and kind=='model':
                    value={'anims':{}}
                    i=0
                    while i<len(t):
                        key=t[i].lower(); i+=1
                        if key in ('inherit','mesh','skin','offset') and i<len(t):
                            if key=='offset':
                                if t[i]=='(': value[key]=tuple(map(float,t[i+1:i+4])); i+=5
                            else: value[key]=t[i]; i+=1
                        elif key=='anim' and i+1<len(t):
                            anim_name=t[i].lower(); clips=[path_name(t[i+1])]; i+=2
                            if policy:
                                while i+1<len(t) and t[i]==',': clips.append(path_name(t[i+1])); i+=2
                            value['anims'][anim_name]=clips if policy else clips[0]
                        elif key=='{':
                            depth=1
                            while i<len(t) and depth:
                                depth+=(t[i]=='{')-(t[i]=='}'); i+=1
                    self.models[name]=value
                elif ext=='.skin':
                    pairs=[]; i=0
                    while i+1<len(t):
                        if t[i].lower()!='model': pairs.append((policy.material_name(t[i]) if policy else path_name(t[i]),policy.material_name(t[i+1]) if policy else path_name(t[i+1])))
                        i+=2
                    self.skins[name]=pairs
                elif ext=='.mtr' and kind!='table':
                    self.materials[policy.material_name(name) if policy else name]=body

    def inherit(self, collection, name, stack=()):
        if self.policy: return self.policy.inherit(self,collection,name,stack)
        name=path_name(name)
        if name in stack: raise ValueError('Cyclic definition: '+' -> '.join((*stack,name)))
        if name not in collection: raise ValueError('Missing definition: '+name)
        raw=collection[name]
        parent=raw.get('inherit')
        result=self.inherit(collection,parent,(*stack,name)) if parent else {}
        anims=dict(result.get('anims',{})); anims.update(raw.get('anims',{}))
        result.update(raw)
        if 'anims' in raw: result['anims']=anims
        return result

    def entity(self,name):
        value=self.inherit(self.entities,name)
        if self.policy and value.get('def_persona'):
            value.update(self.inherit(self.entities,value['def_persona']))
        return value

    def model(self,name):
        name=path_name(name)
        return self.inherit(self.models,name) if name in self.models else {'mesh':name,'anims':{}}

    def remap(self, name, skin):
        if self.policy:name=self.policy.material_name(name)
        for source,target in self.skins.get(path_name(skin or ''),[]):
            if source in ('*',path_name(name)): return target
        return name


PROGRAMS = {'addnormals','heightmap','add','scale','invertalpha','invertcolor','makeintensity','makealpha','smoothnormals'}


def image_expression(tokens, at, programs=PROGRAMS):
    """idImage image-program canonical spacing, also used by ImageProgramStringToCompressedFileName."""
    token=tokens[at].replace('.tga',''); at+=1
    if token.lower() in programs and at<len(tokens) and tokens[at]=='(':
        expr=token+'('; at+=1; depth=1
        while at<len(tokens) and depth:
            t=tokens[at].replace('.tga',''); at+=1
            depth+=(t=='(')-(t==')')
            expr+=t if t in ('(',')',',') else ' '+t
        return expr,at
    return token,at


def dds_path(expression):
    # neo/renderer/Image_load.cpp idImage::ImageProgramStringToCompressedFileName.
    result=''; depth=0
    for c in path_name(expression).replace('.tga',''):
        if c in '/\\(':
            c='/' if depth<4 else ' '; depth+=1
        elif c in '<>:|".': c='_'
        elif c in '),': continue
        if c==' ' and result.endswith('/'): continue
        result+=c
    return 'dds/'+result+'.dds'


def texture_filename(source, campaign):
    """Reversible Doom path encoding in the workshop's flat per-game directory.

    Base fallback assets share names with base; only actual d3xp assets are
    prefixed. Long image-program paths retain a readable prefix plus a digest.
    Both model and map imports must call this helper, not flatten with underscores.
    """
    value=quote(path_name(source),safe='._-')
    if len(value)>210: value=value[:160]+'-'+hashlib.sha256(value.encode()).hexdigest()[:32]+Path(source).suffix
    return ('d3xp%2F' if campaign=='d3xp' else '')+value


BLENDS={'blend':['gl_src_alpha','gl_one_minus_src_alpha'],'add':['gl_one','gl_one'],
        'filter':['gl_dst_color','gl_zero'],'modulate':['gl_dst_color','gl_zero'],'none':['gl_zero','gl_one']}


def material_record(name, body, policy=None):
    t=compact(body or []); stages=[]; global_tokens=[]; i=0
    while i<len(t):
        if t[i]=='{':
            start=i+1; depth=1; i+=1
            while i<len(t) and depth: depth+=(t[i]=='{')-(t[i]=='}'); i+=1
            stages.append(t[start:i-1])
        else: global_tokens.append(t[i]); i+=1
    result=dict(name=name,diffuse=None,bump=None,specular=None,alphaTest=0.,blend=[],
                twoSided=False,translucent=False,vertexColor=False,stages=[],notes=[],tokens=body or [])
    lower=[v.lower() for v in global_tokens]
    result['twoSided']='twosided' in lower
    result['translucent']='translucent' in lower
    result['clamp']='clamp' in lower
    result['visible']=not any(v in lower for v in ('nodraw','collision','discretecollision')) and path_name(name) not in ('nodraw','textures/common/nodraw')
    for tokens in [global_tokens,*stages]:
        stage={}; i=0
        while i<len(tokens):
            key=tokens[i].lower(); i+=1
            if key in ('diffusemap','bumpmap','specularmap','map','clampmap') and i<len(tokens):
                expr,i=image_expression(tokens,i,PROGRAMS | policy.programs if policy else PROGRAMS)
                role={'diffusemap':'diffuse','bumpmap':'bump','specularmap':'specular'}.get(key)
                if role: result[role]=result[role] or expr; stage['role']=role
                stage['image']=expr
                if key=='clampmap': stage['clamp']=True
            elif key=='blend' and i<len(tokens):
                mode=tokens[i].lower(); i+=1
                if mode in ('diffusemap','bumpmap','specularmap'): stage['role']=mode.removesuffix('map')
                elif mode in BLENDS: stage['blend']=BLENDS[mode]
                elif mode.startswith('gl_') and i+1<len(tokens) and tokens[i]==',':
                    stage['blend']=[mode,tokens[i+1].lower()]; i+=2
            elif key=='alphatest' and i<len(tokens):
                stage['alphaTestExpression']=tokens[i]
                try: stage['alphaTest']=float(tokens[i])
                except ValueError: stage['alphaTest']=.5; result['notes'].append('Dynamic alphaTest previewed at 0.5')
                i+=1
            elif key in ('vertexcolor','inversevertexcolor'): stage['vertexColor']=key
            elif key in ('scroll','rotate','scale','translate','if','rgb','rgba','red','green','blue','alpha','program','vertexprogram','fragmentprogram','texgen','videomap'):
                result['notes'].append('Stage '+key+' retained in source tokens for Maps')
        if tokens is global_tokens: continue
        stage['tokens']=tokens; result['stages'].append(stage)
        role=stage.get('role')
        if role and stage.get('image'): result[role]=result[role] or stage['image']
    chosen=next((s for s in result['stages'] if s.get('role')=='diffuse'),None)
    if not result['diffuse']:
        chosen=next((s for s in result['stages'] if s.get('image') and not s.get('role')),None)
        if chosen: result['diffuse']=chosen['image']; result['unlit']=True
    if chosen:
        for key in ('alphaTest','alphaTestExpression','blend','vertexColor','clamp'):
            if key in chosen: result[key]=chosen[key]
    if body is None:
        result['diffuse']='_default' if policy and path_name(name)=='default' else name
        if result['diffuse']=='_default':result['notes'].append('Untextured LWO Default surface: native default material, not collision')
    elif not result['diffuse']:
        result['diffuse']='_black'
        result['notes'].append('No diffuse stage; black model preview (specular/GUI belongs to Maps)')
        if not result['bump'] and not result['specular'] and not stages and 'guisurf' not in lower:
            result['visible']=False
    if result['translucent'] and not result['blend']: result['blend']=BLENDS['blend']
    result['notes']=sorted(set(result['notes']))
    return result


class Textures:
    def __init__(self, files, output, policy=None):
        self.policy=policy
        self.files,self.output,self.records=files,Path(output),{}

    def source_image(self, expression):
        t=compact(lex(expression)); at=0
        def read():
            nonlocal at
            name=t[at]; at+=1
            if name.lower() in (PROGRAMS | self.policy.programs if self.policy else PROGRAMS) and at<len(t) and t[at]=='(':
                op=name.lower(); at+=1; images=[read()]; numbers=[]
                while at<len(t) and t[at]!=')':
                    if t[at]==',': at+=1; continue
                    if op in ('heightmap','scale','downsize'): numbers.append(float(t[at])); at+=1
                    else: images.append(read())
                at+=1; a=np.array(images[0].convert('RGBA'),dtype=float)
                if op in ('add','addnormals'):
                    b=np.array(images[1].resize(images[0].size).convert('RGBA'),dtype=float)
                    if op=='add': a=np.clip(a+b,0,255)
                    else:
                        n=(a[:,:,:3]-128)/127; m=(b[:,:,:3]-128)/127
                        n[:,:,0:2]+=m[:,:,0:2]; n/=np.maximum(np.linalg.norm(n,axis=2,keepdims=True),1e-10)
                        a[:,:,:3]=128+127*n
                elif op=='heightmap':
                    h=a[:,:,:3].mean(axis=2)*numbers[0]/256
                    n=np.stack((h-np.roll(h,-1,axis=1),h-np.roll(h,-1,axis=0),np.ones(h.shape)),axis=2)
                    n/=np.linalg.norm(n,axis=2,keepdims=True); a[:,:,:3]=128+127*n
                elif op=='downsize':
                    factor=2**int(numbers[0])
                    return images[0].resize((max(1,images[0].width//factor),max(1,images[0].height//factor)),Image.Resampling.BOX)
                elif op=='scale': a*=np.array((numbers+[1]*4)[:4])
                elif op=='invertalpha': a[:,:,3]=255-a[:,:,3]
                elif op=='invertcolor': a[:,:,:3]=255-a[:,:,:3]
                elif op=='makealpha': a[:,:,3]=a[:,:,:3].mean(axis=2); a[:,:,:3]=255
                elif op=='makeintensity': a[:]=a[:,:,0:1]
                else: raise ValueError('Image program needs precompressed DDS: '+op)
                return Image.fromarray(np.clip(a,0,255).astype('uint8'),'RGBA')
            name=path_name(name)
            builtins={'_white':(255,255,255,255),'_nofalloff':(255,255,255,255),'_black':(0,0,0,255),'_flat':(128,128,255,255),'_default':(32,32,32,255)}
            if name in builtins: return Image.new('RGBA',(2,2),builtins[name])
            if name=='_quadratic':
                # GPL Image_init.cpp R_QuadraticImage (32 x 4).
                line=np.clip((1-(np.abs(np.arange(32)-15.5)-.5)/16)**2*255,0,255).astype('uint8')
                pixels=np.full((4,32,4),255,dtype='uint8');pixels[:,:,:3]=line[None,:,None]
                return Image.fromarray(pixels,'RGBA')
            name=name if Path(name).suffix else name+'.tga'
            if name not in self.files: raise ValueError('Missing image: '+name)
            with Image.open(io.BytesIO(self.files[name].read())) as image: return image.convert('RGBA')
        result=read()
        if at!=len(t): raise ValueError('Trailing image program tokens')
        return result

    def resolve(self, expression):
        if not expression: return None
        expression=path_name(expression)
        if expression in self.records: return self.records[expression]
        record=dict(expression=expression,status='failed'); self.records[expression]=record
        try:
            source=self.policy.dds_path(expression) if self.policy else dds_path(expression)
            # CheckPrecompressedImage bypasses lights/ on the normal 128 MB+
            # machine-spec path. PK4 idFile_InZip timestamps are all zero.
            if source in self.files and (self.policy or not expression.startswith('lights/')):
                asset=self.files[source]; payload=asset.read()
                if payload[:4]!=b'DDS ': raise ValueError('Invalid DDS '+source)
                filename=texture_filename(source,asset.campaign)
                record.update(source=source,origin=asset.origin,encoding=payload[84:88].decode('latin1').strip('\0') or 'RGB',precompressed=True)
            else:
                # Image-program output is named by its canonical expression. Ordinary
                # images retain their original Doom path with only the suffix changed.
                candidates=[v for v in compact(lex(expression)) if v not in PROGRAMS and v+'.tga' in self.files]
                campaign='d3xp' if any(self.files[v+'.tga'].campaign=='d3xp' for v in candidates) else 'base'
                source=expression if Path(expression).suffix else expression+'.tga'
                if source in self.files: campaign=self.files[source].campaign
                payload=self.source_image(expression)
                filename=texture_filename(re.sub(r'\.tga$','',source)+'.png',campaign)
                record.update(source=source,encoding='RGBA',precompressed=False)
            write_texture(self.output,filename,payload)
            record.update(status='ready',texture=filename)
        except (ValueError,OSError,IndexError) as exc: record['reason']=str(exc)
        return record


class Materials:
    def __init__(self, declarations, textures):
        self.declarations,self.textures,self.records=declarations,textures,{}

    def resolve(self,name):
        name=self.declarations.policy.material_name(name) if self.declarations.policy else re.sub(r'\.tga$','',path_name(name))
        if name in self.records: return self.records[name]
        result=material_record(name,self.declarations.materials.get(name),self.declarations.policy)
        result['images']={role:self.textures.resolve(result[role]) for role in ('diffuse','bump','specular')}
        result['settings']=dict(alphaTest=result['alphaTest'],blend=result['blend'],cull='none' if result['twoSided'] else 'back',
                                depthWrite=not(result['translucent'] or result['blend']),vertexColor=result['vertexColor'],
                                unlit=result.get('unlit',False),visible=result['visible'],clamp=result['clamp'])
        self.records[name]=result
        return result

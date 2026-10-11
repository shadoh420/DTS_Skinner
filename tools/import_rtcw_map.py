"""Local RTCW BSP47 previews, using the Q3 draw format and RTCW model readers.

Engine references describe rules only; no engine code or retail data is bundled.
python tools/import_rtcw_map.py --install C:/Games/RTCW [--replace]
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

from PIL import Image

try:
    from tools import import_q3_map as qmap, import_rtcw as rtcw
    from tools.rtcw_map_items import ITEMS
except ImportError:
    import import_q3_map as qmap
    import import_rtcw as rtcw
    from rtcw_map_items import ITEMS

# qfiles.h: the seventeen on-disk records are unchanged from BSP46.
STRIDES = (1, 72, 16, 36, 48, 4, 4, 40, 12, 8, 44, 4, 72, 104, 49152, 8, 1)
PLAYER_MINS, PLAYER_MAXS, EYE_HEIGHT = (-18, -18, -24), (18, 18, 48), 40
NOT_DRAWN = ['global, sky, water and volume fog (by choice)', 'AI characters',
             'gameplay, audio, particles, flares and sun sprites',
             'script actions and moving/animated model poses after spawn',
             'portals, mirrors, skyportal cameras and spotlight camera effects',
             'light-grid lighting on placed models (lit evenly)']
PROP_MODELS = {
    'props_box_32':'models/mapobjects/boxes/box32.md3',
    'props_box_48':'models/mapobjects/boxes/box48.md3',
    'props_box_64':'models/mapobjects/boxes/box64.md3',
    'props_chair':'models/furniture/chair/chair_office3.md3',
    'props_chair_chat':'models/furniture/chair/chair_chat.md3',
    'props_chair_chatarm':'models/furniture/chair/chair_chatarm.md3',
    'props_chair_side':'models/furniture/chair/sidechair3.md3',
    'props_chair_hiback':'models/furniture/chair/hiback5.md3',
    'props_desklamp':'models/furniture/lights/desklamp.md3',
    'props_crate_32':'models/furniture/crate/crate32.md3',
    'props_crate_64':'models/furniture/crate/crate64.md3',
    'props_footlocker':'models/mapobjects/furniture/footlocker.md3',
    'props_me109':'models/mapobjects/vehicles/m109.md3',
    'misc_flak':'models/mapobjects/weapons/flak_a.md3',
    'misc_grabber_trap':'models/misc/grabber/grabber.md3',
}
# InitProp assigns these render replacements to brush-based props.
BRUSH_PROP_MODELS = {
    'props_bench':'models/furniture/bench/bench_sm.md3',
    'props_radio':'models/mapobjects/electronics/radio1.md3',
    'props_locker_tall':'models/furniture/storage/lockertall.md3',
    'props_flippy_table':'models/furniture/table/woodflip.md3',
    'props_crate_32x64':'models/furniture/crate/crate32x64.md3',
    'props_58x112tablew':'models/furniture/table/56x112tablew.md3',
    'props_castlebed':'models/furniture/bed/castlebed.md3',
    'props_radioseven':'models/mapobjects/electronics/radios.md3',
}


def vector(value, default=(0, 0, 0)):
    if value is None: return list(default)
    values = [float(x) for x in str(value).split()]
    if len(values) != 3 or not all(math.isfinite(x) for x in values):
        raise ValueError('Expected three finite coordinates')
    return values


def number(entity, key, default=0):
    value = float(entity.get(key, default))
    if not math.isfinite(value): raise ValueError('Non-finite '+key)
    return value


def angles(entity):
    return vector(entity['angles']) if 'angles' in entity else [0, number(entity,'angle'), 0]


def scale(entity):
    values = vector(entity['modelscale_vec']) if 'modelscale_vec' in entity else [number(entity,'modelscale',1)]*3
    if any(abs(x) < 1e-6 or abs(x) > 1000 for x in values): raise ValueError('Invalid model scale')
    return values


def lighting_profile(install, section, game=None):
    """Classic renderer defaults, then bounded config values; never execute cfg commands."""
    values = dict(r_gamma=1, r_intensity=1, r_mapoverbrightbits=2, r_overbrightbits=0,
                  r_ignorehwgamma=0, r_picmip=1, r_fullscreen=1, r_colorbits=32,
                  r_roundimagesdown=1, r_simplemipmaps=1, r_texturemode='GL_LINEAR_MIPMAP_LINEAR', cg_fov=90)
    game = game or rtcw.Game(install,section)
    root = Path(install); root = root/'Main' if (root/'Main').is_dir() else root
    sources = []
    # common.c Com_ExecuteCfg: default.cfg, user config, autoexec.cfg. Only settings
    # affecting this preview are read. No exec/vstr or console commands are run.
    for name in ('default.cfg','wolfconfig.cfg','autoexec.cfg'):
        raw, source = None, None
        for label, files in game.sources:
            if label=='loose' and (root/name).is_file():
                raw,source=rtcw.read_asset(root/name,max_bytes=1024*1024),'loose';break
            if name in files:
                raw,source=files[name]['read'](max_bytes=1024*1024),label;break
        if raw is None: continue
        sources.append(dict(file=name,source=source,sha256=hashlib.sha256(raw).hexdigest()))
        for key, quoted, bare in re.findall(r'(?im)^\s*set\w*\s+(\w+)\s+(?:"([^"]*)"|([^\s;]+))',rtcw.q3.read_text(raw)):
            key = key.lower()
            if key in values:
                if key=='r_texturemode':values[key]=(quoted or bare).upper();continue
                try:
                    value = float(quoted or bare)
                    if math.isfinite(value): values[key] = value
                except ValueError: pass
    gamma = min(3,max(.5,values['r_gamma']))
    hardware = not values['r_ignorehwgamma']  # Assumes a working desktop ramp if requested.
    bits = min(2 if values['r_colorbits'] > 16 else 1,max(0,int(values['r_overbrightbits']))) if hardware and values['r_fullscreen'] else 0
    return dict(cvars=values,sources=sources,gamma=gamma,intensity=max(1,values['r_intensity']),
                hardwareGamma=hardware,overbrightBits=bits,identityLight=1/(2**bits),
                lightScale=2**(min(4,max(0,int(values['r_mapoverbrightbits'])))-bits),
                displayScale=2**bits if hardware else 1,displayGamma=gamma if hardware else 1,
                picmip=min(8,max(0,int(values['r_picmip']))),textureMode=values['r_texturemode'],fov=min(130,max(30,values['cg_fov'])),
                source='iortcw classic renderer; retail 1.4x platform gamma behavior needs game comparison')


class Game(rtcw.Game):
    def __init__(self, install, section='sp'):
        super().__init__(install,section)
        self.files = {n:(a['source'],a['read']) for n,a in self.assets.items()}
        self.definitions = rtcw.shader_definitions(self)
        self.shaders = {n:(b,s) for n,(b,s,_) in self.definitions.items()}
        self.shader_label = {n:self.assets[p]['source'] for n,(_,_,p) in self.definitions.items()}
        self.outside, self.images, self.described, self.models = set(), {}, {}, {}
        self.lighting = lighting_profile(install,section,self)


def map_names(game):
    return [(n,a['source']) for n,a in sorted(game.assets.items()) if re.fullmatch(r'maps/[^/]+\.bsp',n)
            and (game.section == 'sp' or a['source'].lower().startswith('mp_') or a['source']=='loose')]


def read_bsp(raw):
    lumps = qmap.read_bsp(raw, version=47)
    for i in range(17):
        offset,length=struct.unpack_from('<2i',raw,8+i*8)
        if length and offset<144: raise ValueError('Lump overlaps header')
    for i,(lump,stride) in enumerate(zip(lumps,STRIDES)):
        if len(lump)%stride: raise ValueError(f'Lump {i}: partial {stride}-byte record')
    if len(lumps[0]) > 1024*1024: raise ValueError('Entity lump exceeds limit')
    if len(lumps[16]) and (len(lumps[16])<8 or min(struct.unpack_from('<2i',lumps[16]))<0):
        raise ValueError('Invalid visibility header')
    counts = [len(l)//s for l,s in zip(lumps,STRIDES)]
    def bounds(first,count,total,label):
        if first<0 or count<0 or first+count>total: raise ValueError('Invalid '+label+' range')
    def index(i,total,label):
        if not 0<=i<total: raise ValueError('Invalid '+label+' index')
    if not counts[7]: raise ValueError('No world model')
    for node in struct.iter_unpack('<9i',lumps[3]):
        index(node[0],counts[2],'node plane')
        for child in node[1:3]: index(child if child>=0 else -child-1,counts[3] if child>=0 else counts[4],'node child')
    for leaf in struct.iter_unpack('<12i',lumps[4]):
        bounds(leaf[8],leaf[9],counts[5],'leaf surfaces');bounds(leaf[10],leaf[11],counts[6],'leaf brushes')
    for v, in struct.iter_unpack('<i',lumps[5]): index(v,counts[13],'leaf surface')
    for v, in struct.iter_unpack('<i',lumps[6]): index(v,counts[8],'leaf brush')
    for _,brush,side in struct.iter_unpack('<64sii',lumps[12]):
        if brush>=0: index(brush,counts[8],'fog brush')
        if side < -1: raise ValueError('Invalid fog side')
    if lumps[16]:
        clusters,width=struct.unpack_from('<2i',lumps[16])
        if clusters*width != len(lumps[16])-8: raise ValueError('Invalid visibility size')
    for m in struct.iter_unpack('<6f4i',lumps[7]):
        rtcw.finite(m[:6]); bounds(m[6],m[7],counts[13],'model surfaces'); bounds(m[8],m[9],counts[8],'model brushes')
    for b in struct.iter_unpack('<3i',lumps[8]):
        bounds(b[0],b[1],counts[9],'brush sides'); index(b[2],counts[1],'brush shader')
    for p,s in struct.iter_unpack('<2i',lumps[9]):
        index(p,counts[2],'side plane'); index(s,counts[1],'side shader')
    for at in range(0,len(lumps[13]),104):
        s = struct.unpack_from('<12i12f2i',lumps[13],at)
        index(s[0],counts[1],'surface shader'); bounds(s[3],s[4],counts[10],'surface vertices'); bounds(s[5],s[6],counts[11],'surface indices')
        if s[2] not in (1,2,3,4): raise ValueError('Unsupported surface type '+str(s[2]))
        for v, in struct.iter_unpack('<i',lumps[11][s[5]*4:(s[5]+s[6])*4]): index(v,s[4],'draw vertex')
        if s[2]==2 and (s[-2]<3 or s[-1]<3 or not s[-2]%2 or not s[-1]%2 or s[-2]*s[-1]!=s[4]):
            raise ValueError('Invalid patch grid')
        rtcw.finite(s[12:24])
    for v in struct.iter_unpack('<10f4B',lumps[10]): rtcw.finite(v[:10])
    for p in struct.iter_unpack('<4f',lumps[2]): rtcw.finite(p)
    return lumps


class Textures(qmap.Textures):
    """Q3 decoding/content storage, RTCW path lookup and classic texture lighting."""
    def __init__(self, output, game):
        super().__init__(output)
        self.game, self.mipmap, self.picmip, self.sky_paths = game, True, True, set()

    def process(self, filename, mipmap=True, picmip=True):
        profile = self.game.lighting
        signature = json.dumps([hashlib.sha256((self.output/filename).read_bytes()).hexdigest(),profile,mipmap,picmip],sort_keys=True)
        result = hashlib.sha256(signature.encode()).hexdigest()[:24]+'.png'
        if (self.output/result).exists(): return result
        with Image.open(self.output/filename) as source:
            image = source.convert('RGBA')
            if max(image.size)>8192: raise ValueError('Image dimensions exceed limit')
            original = image.size
            size = [2**int(math.ceil(math.log2(max(1,x)))) for x in original]
            if profile['cvars']['r_roundimagesdown']:
                size = [x//2 if x>y else x for x,y in zip(size,original)]
            if picmip: size = [max(1,x>>profile['picmip']) for x in size]
            if tuple(size)!=original: image=image.resize(tuple(size),Image.Resampling.BOX)
            # Upload32 bypasses LightScale for unchanged non-mipmapped images.
            if mipmap or tuple(size)!=original:
                gamma = 1 if profile['hardwareGamma'] else profile['gamma']
                intensity = profile['intensity'] if mipmap else 1
                table=[min(255,int(255*(min(255,int(i*intensity))/255)**(1/gamma)+.5)) for i in range(256)]
                channels=image.split();image=Image.merge('RGBA',tuple(c.point(table) for c in channels[:3])+(channels[3],))
            image.save(self.output/result)
        return result

    def resolve(self, game, path):
        normalized = rtcw.q3.key_name(path).lstrip('/')
        candidate = next((n for n in rtcw.image_candidates(normalized) if n in game.assets),normalized)
        file = super().resolve(game,candidate)
        sky = normalized in self.sky_paths
        # RTCW ParseSkyParms requests both mipmap and picmip even for the far box.
        return self.process(file,self.mipmap or sky,self.picmip or sky) if file else None


class ModelTextures(rtcw.Textures):
    """Job 1 material resolution with immutable, content-named map images."""
    def __init__(self,game,output):
        super().__init__(game,output)
        self.game=game
        self.raw=qmap.Textures(output)

    def convert(self,key):
        file=self.raw.resolve(self.game,key)
        if file is None: raise ValueError('Cannot decode '+key)
        return file


def describe(game,name,textures):
    if name in game.described: return game.described[name]
    body,stages = game.shaders.get(name,([],[]))
    words = {r[0] for r in body}
    textures.mipmap, textures.picmip = 'nomipmaps' not in words, not words.intersection(('nopicmip','nomipmaps'))
    textures.sky_paths = {rtcw.q3.key_name(r[1]+'_'+side+'.tga') for r in body if r[0]=='skyparms' and len(r)>1 for side in qmap.SKY_SIDES}
    implicit = next((r for r in body if r[0] in ('implicitmap','implicitmask','implicitblend')),None)
    if implicit and not stages:
        target = implicit[1] if len(implicit)>1 and implicit[1]!='-' else name
        stage = [['map',target],['rgbgen','identitylighting']]
        if implicit[0]=='implicitmask': stage += [['alphafunc','ge128']]
        if implicit[0]=='implicitblend': stage += [['blendfunc','blend']]
        game.shaders[name] = (body,[stage])
    result,missing,limits = qmap.build(game,name,textures)  # Deliberately no cross-folder guessing.
    if 'nomipmaps' in words:
        for stage in result.get('stages',[]): stage['noMipmaps']=True
    sky=next((r for r in body if r[0]=='skyparms' and len(r)>1 and r[1]!='-'),None)
    if sky and 'sky' in result:
        sides=[textures.resolve(game,sky[1]+'_'+side+'.tga') for side in qmap.SKY_SIDES]
        # RB_StageIteratorSky suppresses the box when its first image is missing.
        if sides[0]:result['sky']['box']=[s or '$default' for s in sides]
        else:result['sky'].pop('box',None)
    if any([w.lower() for w in r[:2]]==['surfaceparm','fog'] for r in body):
        result=dict(name=name,stages=[],cull='none',sort=3,outside=[])
    if implicit: limits += ['implicit directive compatibility extension (not in supplied renderer)']
    if words.intersection(('fogparms','skyfogvars','waterfogvars','mapfogvars','nofog')): limits += ['fog not drawn']
    if 'sunshader' in words: limits += ['sun sprite not drawn']
    # Compression directives affect storage, not an uncompressed preview's material.
    game.described[name] = result,missing,sorted(set(limits))
    return game.described[name]


def filtered(entity,section):
    if section=='sp' and (number(entity,'notsingle') or number(entity,'notfree')): return 'excluded by SP gametype flags'
    if section=='mp' and number(entity,'notteam'): return 'excluded by default MP gametype 5'
    return ''


def pickup_model(game,item,override=''):
    """CG_Item uses the optional pickup mesh only if RegisterModel succeeded."""
    kind,path,stand,sideways=item
    if kind=='weapon' and stand and game.model_path(path) not in game.assets:
        path=stand.removesuffix('_stand.md3')+'.md3'
        sideways=True
    return override or path,sideways


def brush_state(entity,model,things):
    """Spawn positions from g_mover/g_script, with runtime motion intentionally frozen."""
    cls=entity.get('classname','').lower();flags=int(number(entity,'spawnflags'))
    pos=vector(entity.get('origin'));rot=angles(entity);hidden=''
    if cls.startswith(('trigger_','target_')) or cls in ('func_invisible_user','func_timer','func_areaportal'):
        hidden='server-only trigger/control brush'
    if cls in ('func_static','func_explosive','script_mover','props_decorbrush') and flags&1: hidden='START_INVIS'
    if cls=='func_rotating' and flags&2: hidden='STARTINVIS'
    if cls in ('func_door','func_button','func_plat','func_train','func_secret'):
        rot=[0,0,0]  # movement direction is not a brush rotation
    if cls=='func_door' and flags&1:
        a=number(entity,'angle');direction=[0,0,1 if a==-1 else -1] if a in (-1,-2) else rtcw.axis([math.radians(x) for x in angles(entity)])[0]
        distance=sum(abs(d)*(model[i+3]-model[i]) for i,d in enumerate(direction))-number(entity,'lip',8)
        pos=[p+distance*d for p,d in zip(pos,direction)]
    if cls=='func_plat': pos[2]-=number(entity,'height',model[5]-model[2]-number(entity,'lip',8))
    if cls=='func_bobbing':
        pos[0 if flags&1 else 1 if flags&2 else 2]-=math.sin(2*math.pi*number(entity,'phase'))*number(entity,'height',32)
    if cls=='func_pendulum': rot[2]-=math.sin(2*math.pi*number(entity,'phase'))*number(entity,'speed',30)
    if cls in ('func_train','func_train_rotating'):
        target=next((t for t in things if t.get('targetname')==entity.get('target') and t.get('classname')=='path_corner'),None)
        if target: pos=vector(target.get('origin'))
    return dict(origin=pos,angles=rot,scale=scale(entity),hidden=hidden,model2=BRUSH_PROP_MODELS.get(cls,entity.get('model2','')))


def transformed(point,state,normal=False):
    values=[x/(s if normal else 1) if normal else x*s for x,s in zip(point,state['scale'])]
    basis=rtcw.axis([math.radians(x) for x in state['angles']])
    result=[sum(values[j]*basis[j][i] for j in range(3))+(0 if normal else state['origin'][i]) for i in range(3)]
    if normal:
        length=math.sqrt(sum(x*x for x in result)) or 1
        result=[x/length for x in result]
    return result


def brush_geometry(lumps,states):
    """Bake spawn transforms into the copied draw lumps, keeping Q3's viewer unchanged."""
    result=list(lumps);verts=bytearray(lumps[10]);faces=bytearray(lumps[13])
    models=list(struct.iter_unpack('<6f4i',lumps[7]))
    for i,model in enumerate(models[1:],1):
        state=states.get(i,dict(hidden='unplaced inline model'))
        used=set()
        for face in range(model[6],model[6]+model[7]):
            at=face*104
            if state['hidden'] or state.get('model2'): struct.pack_into('<i',faces,at+8,0);continue
            first,count=struct.unpack_from('<2i',faces,at+12);used.update(range(first,first+count))
        for v in used:
            at=v*44
            struct.pack_into('<3f',verts,at,*transformed(struct.unpack_from('<3f',lumps[10],at),state))
            struct.pack_into('<3f',verts,at+28,*transformed(struct.unpack_from('<3f',lumps[10],at+28),state,True))
    result[10],result[13]=bytes(verts),bytes(faces)
    return result


class Collision:
    """Bounded downward AABB sweep against solid BSP brush planes at spawn state.

    The map's brush ranges include world and placed inline solids. Curved floors
    use the preview's four-step quadratic mesh and a swept triangle/AABB test,
    not the engine's adaptive collision facets and bevel tolerances.
    """
    def __init__(self,lumps,states):
        planes=list(struct.iter_unpack('<4f',lumps[2]));sides=list(struct.iter_unpack('<2i',lumps[9]))
        brushes=list(struct.iter_unpack('<3i',lumps[8]));shaders=list(struct.iter_unpack('<64sii',lumps[1]))
        self.brushes=[];self.patches=[]
        for i,m in enumerate(struct.iter_unpack('<6f4i',lumps[7])):
            state=states.get(i) if i else None
            if i and (not state or state['hidden']): continue
            for first,count,shader in brushes[m[8]:m[8]+m[9]]:
                content=shaders[shader][2]
                if not content & (1|0x10000): continue
                faces=[]
                for plane,_ in sides[first:first+count]:
                    n=planes[plane][:3];d=planes[plane][3]
                    if state:
                        point=transformed([v*d for v in n],state);n=transformed(n,state,True);d=sum(a*b for a,b in zip(n,point))
                    faces.append((*n,d))
                self.brushes.append((content,faces))

            for face in range(m[6],m[6]+m[7]):
                shader,_,kind,first=struct.unpack_from('<4i',lumps[13],face*104)
                content=shaders[shader][2]
                if kind!=2 or not content&(1|0x10000): continue
                width,height=struct.unpack_from('<2i',lumps[13],face*104+96)
                for y in range(0,height-2,2):
                    for x in range(0,width-2,2):
                        control=[[struct.unpack_from('<3f',lumps[10],(first+(y+j)*width+x+k)*44) for k in range(3)] for j in range(3)]
                        grid=[]
                        for v in range(5):
                            for u in range(5):
                                a,b=u/4,v/4;wa=((1-a)**2,2*a*(1-a),a*a);wb=((1-b)**2,2*b*(1-b),b*b)
                                p=[sum(control[j][k][axis]*wa[k]*wb[j] for j in range(3) for k in range(3)) for axis in range(3)]
                                grid.append(transformed(p,state) if state else p)
                        for v in range(4):
                            for u in range(4):
                                at=v*5+u
                                for indices in ((at,at+1,at+5),(at+1,at+6,at+5)):
                                    triangle=[grid[n] for n in indices]
                                    self.patches.append((content,triangle))
                        if len(self.patches)>1000000: raise ValueError('Patch collision exceeds preview limit')

    @staticmethod
    def triangle_drop(triangle,origin,mins,maxs):
        """Swept-box separating-axis intervals; returns downward contact distance."""
        # XY bounds cheaply reject almost every facet before the full hull test.
        if any(origin[i]+maxs[i]<min(p[i] for p in triangle) or origin[i]+mins[i]>max(p[i] for p in triangle) for i in (0,1)):
            return None
        edges=[[triangle[(j+1)%3][i]-triangle[j][i] for i in range(3)] for j in range(3)]
        def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
        basis=((1,0,0),(0,1,0),(0,0,1));normal=cross(edges[0],edges[1])
        if sum(n*n for n in normal)<1e-12:return None
        axes=[*basis,normal]+[cross(edge,axis) for edge in edges for axis in basis]
        centre=[origin[i]+(mins[i]+maxs[i])/2 for i in range(3)];half=[(maxs[i]-mins[i])/2 for i in range(3)]
        near,far=-math.inf,math.inf
        for axis in axes:
            values=[sum(a*p for a,p in zip(axis,point)) for point in triangle]
            radius=sum(abs(a)*h for a,h in zip(axis,half));pos=sum(a*p for a,p in zip(axis,centre))
            low,high=min(values)-radius,max(values)+radius
            if abs(axis[2])<1e-9:
                if pos<low or pos>high:return None
            else:
                a,b=sorted(((pos-low)/axis[2],(pos-high)/axis[2]))
                near,far=max(near,a),min(far,b)
                if near>far:return None
        return near if 0<=near<=min(far,4096) else None

    def drop(self,origin,mins,maxs,mask=1|0x10000):
        best=None;startsolid=False;floor='brush hull floor'
        for content,planes in self.brushes:
            if not content&mask: continue
            near,far=-math.inf,math.inf
            for nx,ny,nz,d in planes:
                n=(nx,ny,nz)
                expanded=d-sum(v*(mins[i] if v>=0 else maxs[i]) for i,v in enumerate(n))
                distance=sum(v*p for v,p in zip(n,origin))-expanded
                if abs(nz)<1e-9:
                    if distance>0: far=-math.inf;break
                elif nz>0: near=max(near,distance/nz)
                else: far=min(far,distance/nz)
            if near<0<far: startsolid=True
            if 0<=near<=far and near<=4096 and (best is None or near<best): best=near
        for content,triangle in self.patches:
            if not content&mask:continue
            hit=self.triangle_drop(triangle,origin,mins,maxs)
            if hit is not None and (best is None or hit<best):best=hit;floor='patch hull floor (preview tessellation)'
        return ([origin[0],origin[1],origin[2]-best+.125] if best is not None else list(origin)), ('startsolid' if startsolid else floor if best is not None else 'no solid floor within 4096 units')


def viewpoints(things,collision,section):
    candidates=[]
    kinds=('info_player_start','info_player_deathmatch') if section=='sp' else ('team_ctf_redplayer','team_ctf_blueplayer','team_ctf_redspawn','team_ctf_bluespawn')
    for kind in kinds:
        for e in things:
            if e.get('classname','').lower()!=kind or filtered(e,section) or number(e,'nohumans'):continue
            flags=int(number(e,'spawnflags'))
            if section=='mp' and ((kind.endswith('player') and flags&4) or (kind.endswith('spawn') and not flags&2)): continue
            candidates.append(e)
    if section=='sp': candidates.sort(key=lambda e:not int(number(e,'spawnflags'))&1)
    if not candidates: candidates=[e for e in things if e.get('classname','').lower() in ('info_player_start','info_player_intermission')]
    views=[]
    for e in candidates:
        pos=vector(e.get('origin'));pos[2]+=9
        origin,reason=collision.drop(pos,PLAYER_MINS,PLAYER_MAXS);origin[2]+=EYE_HEIGHT
        a=angles(e);v=dict(origin=origin,pitch=a[0],yaw=a[1],floor=reason,classname=e.get('classname'))
        target=next((t for t in things if e.get('target') and t.get('targetname')==e['target']),None)
        if target:v['target']=vector(target.get('origin'))
        views.append(v)
    return views


def placed_model(game,path,skin,frame,output,textures,model_textures,tag=None):
    path=game.model_path(path);raw=game.read(path);model=rtcw.read_model(raw,frame)
    skin_index=int(skin) if str(skin).isdigit() else 0
    sk=rtcw.read_skin(game.read(skin))['surfaces'] if skin and not str(skin).isdigit() else {}
    surfaces=[];shaders=[];missing=[];notes=[]
    for surface in model['surfaces']:
        embedded=surface['shaders']
        name=sk.get(surface['name'],embedded[skin_index%len(embedded)] if embedded else '')
        if name.lower() in ('off','*off','nodraw'):continue
        filename,warnings,settings=model_textures.resolve(name)
        missing += [w for w in warnings if 'Missing texture' in w or 'Cannot decode' in w]
        notes += [w for w in warnings if w not in missing]
        # Job 1's resolver chooses the static material, including empty skin and diffuse-stage rules.
        if (model_textures.output/filename).is_file():
            if model_textures.output != textures.output:
                (textures.output/filename).write_bytes((model_textures.output/filename).read_bytes())
            body=game.shaders.get(rtcw.q3.shader_key(name),([],[]))[0]
            words={row[0] for row in body}
            mipmap='nomipmaps' not in words
            filename=textures.process(filename,mipmap=mipmap,picmip=not words.intersection(('nomipmaps','nopicmip')))
            stage=dict(map=filename,rgbGen=['lightingdiffuse'],depthWrite=settings['depthWrite'],clamp=settings['clamp'])
            if not mipmap:stage['noMipmaps']=True
            if settings['alphaFunc']:stage['alphaFunc']=settings['alphaFunc'].lower()
            if settings['blend']:stage['blend']=settings['blend']
            shader=dict(name=name,stages=[stage],cull={'back':'front','front':'back','none':'none'}[settings['cull']],sort=9 if settings['blend'] else 3)
        else:shader=dict(name=name,default=True,cull='front',sort=3)
        v=surface['vertices'];n=surface['normals']
        if tag:
            def native(values,normal=False):
                turned=rtcw.q3.transform(values,(tag,),normal=normal)
                return [value for i in range(0,len(turned),3) for value in (turned[i+2],turned[i],turned[i+1])]
            v=native(v);n=native(n,True)
        surfaces.append(dict(vertices=v,normals=n,uvs=surface['uvs'],indices=surface['indices']))
        shaders.append(shader)
    if not surfaces:raise ValueError('Model has no visible surfaces')
    encoded=json.dumps(surfaces,separators=(',',':')).encode();file='models/'+hashlib.sha256(encoded).hexdigest()[:24]+'.json'
    if not (output/file).exists():(output/file).write_bytes(encoded)
    return dict(file=file,shaders=shaders,middle=[0,0,0],limitations=sorted(set(notes))),missing


def import_map(game,name,ident,raw,source,output,textures,model_textures):
    lumps=read_bsp(raw);things=qmap.entities(lumps[0].decode('latin1'));models=list(struct.iter_unpack('<6f4i',lumps[7]))
    states={};audit=[];unresolved=[]
    for i,e in enumerate(things):
        if not re.fullmatch(r'\*\d+',e.get('model','')):continue
        idx=int(e['model'][1:])
        if not 0<idx<len(models):raise ValueError(f'Entity {i}: invalid inline model')
        state=brush_state(e,models[idx],things);state['hidden']=filtered(e,game.section) or state['hidden'];states[idx]=state
    collision=Collision(lumps,states);draw=brush_geometry(lumps,states)
    names=[(qmap.shader_name(s.split(b'\0')[0].decode('latin1')),flags) for s,flags,_ in struct.iter_unpack('<64sii',lumps[1])]
    used={struct.unpack_from('<i',draw[13],at)[0] for at in range(0,len(draw[13]),104) if struct.unpack_from('<i',draw[13],at+8)[0] in (1,2,3)}
    shaders=[None]*len(names);limits=set(NOT_DRAWN)
    for i in sorted(used):
        if names[i][1]&qmap.SURF_NODRAW:continue
        shaders[i],missing,notes=describe(game,names[i][0],textures);unresolved+=missing;limits.update(notes)
    placed=[];item_models={};teams=set()
    for serial,e in enumerate(things):
        cls=e.get('classname','').lower();reason=filtered(e,game.section);status='omitted';paths=[]
        row=dict(entity=serial,classname=cls)
        try:
            flags=int(number(e,'spawnflags'));model=e.get('model','');item=ITEMS[game.section].get(cls)
            if reason:pass
            elif cls.startswith('ai_'):reason='AI character/control omitted by choice'
            elif model.startswith('*'):
                state=states[int(model[1:])];reason=state['hidden'] or 'inline brush at spawn transform';status='omitted' if state['hidden'] else 'drawn'
                if not state['hidden'] and state['model2']:paths=[state['model2']];reason=''
            elif cls=='misc_model':reason='compile-time model already baked into BSP; runtime frees this entity'
            elif item:
                kind,path,stand,sideways=item
                if e.get('targetname') or (e.get('team') and e['team'] in teams):reason='targeted item or team slave hidden at spawn'
                elif kind=='powerup' and game.section=='mp':reason='MP powerup delayed spawn'
                elif kind=='team' and game.section=='sp':reason='team item excluded from SP gametype'
                else:
                    if e.get('team'):teams.add(e['team'])
                    path,sideways=pickup_model(game,item,e.get('model',''))
                    paths=[path] if path else []
                    reason='item table has no world model' if not paths else ''
            elif cls.startswith(('trigger_','target_','info_','light','dlight','corona','path_')) or cls in ('worldspawn','props_skyportal','misc_vis_dummy','misc_vis_dummy_multiple'):
                reason='world/control/light entity; no standalone visual'
            elif cls in ('script_model_med','props_decoration') and flags&1:reason='START_INVIS'
            elif cls=='misc_spotlight':reason='spotlight camera/effect not reconstructed'
            elif cls=='misc_mg42':
                paths=['models/mapobjects/weapons/mg42a.md3']+([] if flags&2 else ['models/mapobjects/weapons/mg42b.md3']);reason=''
            else:
                path=(e.get('model2') or model) if cls in ('props_decoration','props_statue') else model or e.get('model2') or PROP_MODELS.get(cls)
                if cls=='props_flamebarrel':path='models/furniture/barrel/barrel_'+('c' if flags&4 else 'd' if flags&1 else 'b')+'.md3'
                paths=[path] if path else [];reason='' if paths else 'no renderable model or BSP surfaces'
            if paths and not reason:
                pos=vector(e.get('origin'));rot=angles(e);size=scale(e);frame=int(number(e,'startonframe',number(e,'frame')));skin=e.get('skin','')
                spin=False;floor='authored position';tag=None
                if cls=='misc_grabber_trap':rot[1]-=90;frame=66  # CG_Trap's starting animation.
                if cls=='props_footlocker':pos[2]-=8  # init_locker's model-origin correction.
                if cls=='props_me109':
                    target=next((t for t in things if t.get('targetname')==e.get('target') and t.get('classname')=='plane_waypoint'),None)
                    if target:pos=vector(target.get('origin'))
                if model.startswith('*'):
                    state=states[int(model[1:])];pos,rot,size=state['origin'],state['angles'],state['scale']
                if cls in ('props_crate_32','props_crate_64'):
                    width=16 if cls.endswith('32') else 32
                    pos,floor=collision.drop(pos,(-width,-width,0),(width,width,width*2),mask=1)
                if item:
                    if not flags&1:
                        pos,floor=collision.drop(pos,(-10,-10,0),(10,10,5),mask=1)
                        if floor=='startsolid':
                            pos[2]-=10;pos,floor=collision.drop(pos,(-10,-10,0),(10,10,5),mask=1)
                            if floor=='startsolid':raise ValueError('Item starts inside solid brush')
                    spin=bool(flags&2) and (item[0]!='weapon' or bool(flags&1))
                    if item[0]=='weapon':
                        size=[s*1.5 for s in size]
                        stand=game.model_path(item[2]) if item[2] else ''
                        if stand in game.assets:
                            stand_model=rtcw.read_model(game.read(stand));tag=stand_model['tags'].get('tag_stand'+(str(frame) if frame else ''))
                            if tag is None:raise ValueError('Weapon stand has no requested tag_stand')
                            paths.append(stand)
                        elif sideways:rot[2]+=90
                for which,path in enumerate(paths):
                    # CG_Item's entity frame selects tag_standN, not a mesh animation frame.
                    mesh_frame=0 if item else frame
                    key=hashlib.sha256(json.dumps([path,skin,mesh_frame,tag if which==0 else None],sort_keys=True).encode()).hexdigest()[:24]
                    if key not in item_models:
                        desc,missing=placed_model(game,path,skin,mesh_frame,output,textures,model_textures,tag if which==0 else None)
                        item_models[key]=desc;unresolved+=missing;limits.update(desc.get('limitations',[]))
                    placed_pos=list(pos)
                    if cls=='misc_mg42':placed_pos[2]+=24 if which==0 else -24
                    placed.append(dict(models=[key],origin=placed_pos,angles=rot,scale=size,spin=spin,entity=serial))
                status='drawn';reason='pickup at spawn; '+floor if item else 'model decoration at authored spawn pose'
            row.update(status=status,reason=reason)
        except (ValueError,OSError,struct.error,zipfile.BadZipFile,KeyError) as exc:
            row.update(status='failed',reason=str(exc));unresolved.append(f'entity {serial} {cls}: {exc}')
        audit.append(row)
    views=viewpoints(things,collision,game.section)
    if not views:limits.add('map has no player start; camera at world origin')
    for v in views:
        if v['floor']!='brush hull floor':limits.add('start floor: '+v['floor'])
    limits.add('hull floor sweep uses brush planes and preview patch triangles; curved-floor tolerances may differ from the game')
    if game.section=='mp':limits.add('first eligible Axis initial spawn, then Allies; game randomly chooses among eligible team starts')
    digest=hashlib.sha256(b''.join(draw)).hexdigest()[:24];file='bsp/'+digest+'.bsp'
    if not (output/file).exists():qmap.write_bsp(output/file,draw,version=47)
    scene=dict(name=name,longname=things[0].get('message','') if things else '',source=source,section=game.section,bsp=file,bspVersion=47,
               shaders=shaders,viewpoints=views,models=[],items=[],placed=placed,itemModels=item_models,lighting=game.lighting,
               entityResults=audit,unresolved=sorted(set(unresolved)),outside=[],guessed=[],limits=sorted(limits))
    folder=output/'maps'/ident;folder.mkdir(parents=True,exist_ok=True);rtcw.q3.json_write(folder/'scene.json',scene)
    return scene


def import_maps(install,output,replace=False):
    output=Path(output)
    for folder in ('maps','models','bsp','textures'):(output/folder).mkdir(parents=True,exist_ok=True)
    result=dict(imported=[],skipped=[],failed={},unresolved={},maps=[],sections={});index=[]
    for section,label in rtcw.SECTIONS.items():
        game=Game(install,section);textures=Textures(output/'textures',game);model_textures=ModelTextures(game,output/'textures')
        classes=Counter()
        for path,source in map_names(game):
            name=Path(path).stem;ident=section+'_'+qmap.map_id(name);scene_path=output/'maps'/ident/'scene.json'
            try:
                if scene_path.is_file() and not replace:
                    scene=json.loads(scene_path.read_text());result['skipped'].append(ident);status='skipped'
                else:
                    scene=import_map(game,name,ident,game.read(path),source,output,textures,model_textures)
                    result['imported'].append(ident);status='imported'
                index.append(dict(id=ident,name=name,longname=scene['longname'],group=label,source=source,unresolved=len(scene['unresolved'])))
                if scene['unresolved']:result['unresolved'][ident]=scene['unresolved']
                classes.update((r['classname'],r['status'],r['reason']) for r in scene['entityResults'])
                result['maps'].append(dict(section=section,map=name,source=source,status=status))
            except (ValueError,OSError,struct.error,zipfile.BadZipFile,KeyError) as exc:
                result['failed'][ident]=str(exc);result['maps'].append(dict(section=section,map=name,source=source,status='failed',reason=str(exc)))
        result['sections'][section]=dict(search_order=game.search_order,issues=game.issues,lighting=game.lighting,
            entity_classes=[dict(classname=c,status=s,reason=r,count=n) for (c,s,r),n in sorted(classes.items())])
    rtcw.q3.json_write(output/'index.json',index);rtcw.q3.json_write(output/'import-report.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',required=True);parser.add_argument('--output',type=Path,default=qmap.LOCAL_DATA/'rtcw-maps')
    parser.add_argument('--replace',action='store_true');args=parser.parse_args()
    print(json.dumps(import_maps(args.install,args.output,args.replace),indent=2))

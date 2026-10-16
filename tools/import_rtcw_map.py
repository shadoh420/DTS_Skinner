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
NOT_DRAWN = ['AI characters',
             'gameplay, audio and particles',
             'script actions and moving/animated model poses after spawn',
             'portal and mirror surfaces, sky portal fog and spotlight camera effects']
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


def lighting_profile(install, section, game=None, *, directory='Main', configs=('default.cfg','wolfconfig.cfg','autoexec.cfg'), defaults=None, device_supports_gamma=True):
    """Classic renderer defaults, then bounded config values; never execute cfg commands."""
    values = dict(r_gamma=1, r_intensity=1, r_mapoverbrightbits=2, r_overbrightbits=0,
                  r_ignorehwgamma=0, r_picmip=1, r_fullscreen=1, r_colorbits=32,
                  r_roundimagesdown=1, r_simplemipmaps=1, r_texturemode='GL_LINEAR_MIPMAP_LINEAR', cg_fov=90)
    values.update(defaults or {})
    game = game or rtcw.Game(install,section)
    root = Path(install); root = root/directory if (root/directory).is_dir() else root
    sources = []
    # common.c Com_ExecuteCfg: default.cfg, user config, autoexec.cfg. Only settings
    # affecting this preview are read. No exec/vstr or console commands are run.
    for name in configs:
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
    hardware = device_supports_gamma and not values['r_ignorehwgamma']
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


def read_bsp(raw, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else []
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
    lit_vertices=set()
    surfaces=bytearray(lumps[13])
    shader_flags=[s[1] for s in struct.iter_unpack('<64sii',lumps[1])]
    for at in range(0,len(lumps[13]),104):
        s = struct.unpack_from('<12i12f2i',lumps[13],at)
        index(s[0],counts[1],'surface shader'); bounds(s[3],s[4],counts[10],'surface vertices')
        if s[2] not in (1,2,3,4): raise ValueError('Unsupported surface type '+str(s[2]))
        # ParseMesh / CMod_LoadPatches construct triangles from the control grid;
        # patch index fields in several retail BSPs contain unused compiler data.
        if s[2] in (1,3):
            bounds(s[5],s[6],counts[11],'surface indices')
            for v, in struct.iter_unpack('<i',lumps[11][s[5]*4:(s[5]+s[6])*4]):
                if not 0<=v<s[4]:raise ValueError(f'Surface {at//104}: invalid draw vertex index {v} (count {s[4]})')
        elif s[6]:
            valid_range=0<=s[5]<=s[5]+s[6]<=counts[11]
            invalid=not valid_range or any(not 0<=v<s[4] for v, in struct.iter_unpack('<i',lumps[11][s[5]*4:(s[5]+s[6])*4]))
            if invalid:diagnostics.append(dict(surface=at//104,action='unused index data ignored',reason='Patch/flare loader does not consume draw indices'))
        if s[2] in (1,2) and s[7]>=0:lit_vertices.update(range(s[3],s[3]+s[4]))
        if s[2]==2 and shader_flags[s[0]]&qmap.SURF_NODRAW:
            diagnostics.append(dict(surface=at//104,action='not drawn',reason='ParseMesh SURF_NODRAW; collision retained'))
        if s[2]==2 and (s[-2]<3 or s[-1]<3 or not s[-2]%2 or not s[-1]%2 or s[-2]*s[-1]!=s[4]):
            raise ValueError('Invalid patch grid')
        # Each dsurface union-like float field has different meaning by type.
        # ParseFace only reads its plane normal; ParseMesh reads LOD bounds.
        consumed={1:set(range(21,24)),2:set(range(15,21)),3:set(),4:set(range(12,18))|set(range(21,24))}[s[2]]
        for component in range(12,24):
            if not math.isfinite(s[component]):
                if component in consumed:raise ValueError(f'Surface {at//104}: non-finite consumed field {component}')
                struct.pack_into('<f',surfaces,at+component*4,0)
                diagnostics.append(dict(surface=at//104,component=component,action='unused surface field set to zero',reason='Surface loader does not consume this field'))
    lumps[13]=bytes(surfaces)
    vertices=bytearray(lumps[10])
    for i,v in enumerate(struct.iter_unpack('<10f4B',lumps[10])):
        rtcw.finite(v[:5]+v[7:10])
        for component in (5,6):
            if not math.isfinite(v[component]):
                if i in lit_vertices:raise ValueError(f'Draw vertex {i}: non-finite lightmap coordinate on lightmapped surface')
                # The renderer copies these bytes but an unlightmapped surface
                # does not sample them. Keep the preview buffer finite, not skip geometry.
                struct.pack_into('<f',vertices,i*44+component*4,0)
                diagnostics.append(dict(vertex=i,component=component,action='unused lightmap coordinate set to zero',reason='No lightmapped surface consumes this vertex'))
    lumps[10]=bytes(vertices)
    for p in struct.iter_unpack('<4f',lumps[2]): rtcw.finite(p)
    return lumps


def upload_image(image, profile, mipmap=True, picmip=True):
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
    return image


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
            image = upload_image(image, profile, mipmap, picmip)
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
        self.converted={}

    def convert(self,key):
        file=self.raw.resolve(self.game,key)
        if file is None: raise ValueError('Cannot decode '+key)
        self.converted[self.prefix+rtcw.q3.asset_name(key)+'.png']=file
        return file

    def resolve(self,shader):
        filename,warnings,settings=super().resolve(shader)
        # Q3's editable-model resolver computes its filename independently of
        # convert()'s return value. Map images instead use immutable content names.
        filename=self.converted.get(filename,filename)
        self.cache[shader]=(filename,warnings,settings)
        self.results[shader].update(texture=filename,status='resolved' if (self.output/filename).is_file() else 'unresolved')
        return self.cache[shader]


def describe(game,name,textures,*,draw_fog_stages=False,inner_box=True):
    if name in game.described: return game.described[name]
    body,stages = game.shaders.get(name,([],[]))
    words = {r[0] for r in body}
    textures.mipmap, textures.picmip = 'nomipmaps' not in words, not words.intersection(('nopicmip','nomipmaps'))
    textures.sky_paths = {rtcw.q3.key_name(box+'_'+side+'.tga') for r in body if r[0]=='skyparms' for box in r[1:4:2] for side in qmap.SKY_SIDES}
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
    inner=next((r[3] for r in body if r[0]=='skyparms' and len(r)>3 and r[3]!='-'),None)
    if inner_box and inner and 'sky' in result:
        # RTCW and ET draw the inner box last, alpha-blended over the clouds (RB_StageIteratorSky); Q3, JK2 and JA never do.
        sides=[textures.resolve(game,inner+'_'+side+'.tga') for side in qmap.SKY_SIDES]
        if sides[0]:result['sky']['inner']=[s or '$default' for s in sides]
    if not draw_fog_stages and any([w.lower() for w in r[:2]]==['surfaceparm','fog'] for r in body):
        result=dict(name=name,stages=[],cull='none',sort=7,fog=True,outside=[])  # SS_FOG: drawn by its fog pass alone.
    if implicit: limits += ['implicit directive compatibility extension (not in supplied renderer)']
    if words.intersection(('skyfogvars','waterfogvars')): limits += ['sky and underwater fog not drawn']
    if 'sunshader' in words: limits += ['sun sprite not drawn']
    # Compression directives affect storage, not an uncompressed preview's material.
    game.described[name] = result,missing,sorted(set(limits))
    return game.described[name]


def map_fog(game,shaders):
    """The map-wide fog of the sky shader's fogvars (R_SetFog via r_mapFogColor / FOG_MAP): GL linear fog from
    the eye to v when v > 1, else GL exp fog of density v. Linear fog hides the sky and ends the view there
    (R_SetFrameFog clips zFar); ET also ends it at its global fog's depth (SetFarClip). Returns (fog, far clip)."""
    fog=None
    for shader in shaders:
        if not shader or 'sky' not in shader:continue
        row=next((r for r in game.shaders.get(shader['name'],([],[]))[0] if r[0]=='fogvars'),None)
        if row:
            *color,value=qmap.numbers(row[1:],4)
            fog=dict(color=color,linear=True,end=value) if value>1 else dict(color=color,linear=False,density=value) if value>0 else None
            break
    far=[fog['end']] if fog and fog['linear'] else []
    return fog,far


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


def coronas(things,section,rules):
    """Corona entities as CG_Corona draws them: _color or color (whichever the entity names last; none or black is
    white) and scale (SP_corona); START_OFF ones are never linked."""
    result=[]
    for e in things:
        if e.get('classname','').lower()!='corona' or int(number(e,'spawnflags'))&1 or rules.filtered(e,section):continue
        colour=next((vector(v) for k,v in reversed(list(e.items())) if k in ('_color','color')),[0,0,0])
        result.append(dict(origin=vector(e.get('origin')),colour=colour if any(c>0 for c in colour) else [1,1,1],scale=number(e,'scale',1)))
    return result


SNOW_COLUMNS={'misc_snow32':32,'misc_snow64':64,'misc_snow128':128,'misc_snow256':256}


def weather(things):
    """Rain and snow, each {type, count, speed, length, wind[, column]}: ET's worldspawn atmosphere (CG_EffectParse:
    D drops, BV wind, rain at 1.1 and snow at 0.1 of gravity 800, raindrops 150 long, flakes 3-5), Raven's fx_rain
    and fx_snow world effects (SP_CreateRain/SP_CreateSnow presets of tr_WorldEffects: light rain 500 drops, other
    rain and snow 1000, rain drops 80 long; no count key is read) and RTCW's misc_snow columns (snow_think: `health` flakes over the
    square named, from the entity down to the floor). ponytail: SP presets on MP maps too; no gusts, wind zones,
    lightning or Raven particle physics."""
    result=[]
    for e in things:
        cls=e.get('classname','').lower();flags=int(number(e,'spawnflags'))
        if cls=='worldspawn' and e.get('atmosphere'):
            keys={k.strip().upper():v.split() for k,_,v in (part.partition('=') for part in e['atmosphere'].split(','))}
            kind=(keys.get('T') or [''])[0].lower()
            if kind in ('rain','snow'):
                drops=[float(v) for v in keys.get('D',['300'])] or [300]
                wind=([float(v) for v in keys.get('BV',[])]+[0,0])[:2]
                result.append(dict(type=kind,count=int(max(drops)),speed=880 if kind=='rain' else 80,length=150 if kind=='rain' else 4,wind=wind))
        elif cls=='fx_rain':
            result.append(dict(type='rain',count=500 if flags&1 or not flags&14 else 1000,speed=1000,length=80,wind=[0,0]))
        elif cls=='fx_snow':
            result.append(dict(type='snow',count=1000,speed=80,length=2,wind=[0,0]))
        elif cls in SNOW_COLUMNS:
            size=SNOW_COLUMNS[cls];origin=vector(e.get('origin'))
            result.append(dict(type='snow',count=int(number(e,'health',32)),speed=50,length=2,wind=[0,0],
                               column=dict(min=[origin[0]-size,origin[1]-size],max=[origin[0]+size,origin[1]+size],top=origin[2])))
    return result


def sun(game,shaders,textures,rules):
    """RB_DrawSun's direction and shader, or None: drawn where a used shader names a sunshader (RTCW always draws
    'sun', ET the shader named); the direction is the last q3map_sun's (ParseShader), else tr_bsp's default."""
    rows=[r for s in shaders if s for r in game.shaders.get(s['name'],([],[]))[0]]
    named=next((r for r in rows if r[0]=='sunshader' and len(r)>1),None)
    if not named:return None,[]
    light=[qmap.numbers(r[5:7],2) for r in rows if r[0]=='q3map_sun' and len(r)>6]
    a,b=(math.radians(v) for v in light[-1]) if light else (None,None)
    direction=[math.cos(a)*math.cos(b),math.sin(a)*math.cos(b),math.sin(b)] if light else [.45,.3,.9]
    shader,missing,_=rules.describe(game,qmap.shader_name(named[1] if getattr(rules,'NAMED_SUN',False) else 'sun'),textures)
    return dict(direction=direction,shader=shader),missing


def mover_pose(states,index,entity):
    """The spawn pose brush_geometry baked a mover's faces in, and the angles its motion turns from (qmap.movers)."""
    state=states.get(index)
    if not state or state['hidden'] or state.get('model2'):return None,None
    rest=[0,0,0] if entity.get('classname','').lower() in ('func_door','func_button','func_plat','func_train','func_secret') else angles(entity)
    return dict(origin=state['origin'],angles=state['angles']),rest


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
    def __init__(self,lumps,states, *, contents_mask=1|0x10000):
        planes=list(struct.iter_unpack('<4f',lumps[2]));sides=list(struct.iter_unpack('<2i',lumps[9]))
        brushes=list(struct.iter_unpack('<3i',lumps[8]));shaders=list(struct.iter_unpack('<64sii',lumps[1]))
        self.brushes=[];self.patches=[]
        for i,m in enumerate(struct.iter_unpack('<6f4i',lumps[7])):
            state=states.get(i) if i else None
            if i and (not state or state['hidden']): continue
            for first,count,shader in brushes[m[8]:m[8]+m[9]]:
                content=shaders[shader][2]
                if not content & contents_mask: continue
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
                if kind!=2 or not content&contents_mask: continue
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


def placed_model(game,path,skin,frame,output,textures,model_textures,tag=None, *, model=None):
    if model is None:
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
    encoded=json.dumps(surfaces,separators=(',',':')).encode()
    binary=getattr(game,'write_surface_data',None)
    file='models/'+hashlib.sha256((b'binary-v1' if binary else b'')+encoded).hexdigest()[:24]+'.json'
    if not (output/file).exists():
        if binary:binary(output/file,surfaces)
        else:(output/file).write_bytes(encoded)
    return dict(file=file,shaders=shaders,middle=[0,0,0],limitations=sorted(set(notes))),missing


# RTCW-SP ai_cast_characters.c: class defaults, not an ai_* name heuristic.
# Controls (ai_marker/trigger/effect/etc.) never acquire an actor model.
NPC_DEFAULTS={
    'ai_soldier':'infantryss', 'ai_american':'american', 'ai_zombie':'zombie',
    'ai_warzombie':'warrior/crypt2', 'ai_venom':'venom', 'ai_loper':'loper',
    'ai_eliteguard':'eliteguard', 'ai_blackguard':'blackguard', 'ai_frogman':'frogman',
    'ai_stimsoldier_dual':'stim', 'ai_stimsoldier_rocket':'stim', 'ai_stimsoldier_tesla':'stim',
    'ai_supersoldier':'supersoldier', 'ai_protosoldier':'protosoldier',
    'ai_boss_helga':'beast', 'ai_boss_heinrich':'heinrich',
    'ai_partisan':'partisan', 'ai_civilian':'civilian',
}


def npc_appearance(e):
    model=rtcw.q3.key_name(e.get('model',''))
    body_model=None
    if model.endswith(('.mds','.mdc','.md3')):
        body_model=model if model.startswith('models/') else 'models/players/'+model
    default=NPC_DEFAULTS[e['classname'].lower()]
    model_skin=str(Path(body_model).parent) if body_model and body_model.startswith('models/players/') else default if body_model else model
    value=e.get('skin') or model_skin or default
    value=rtcw.q3.key_name(value).removeprefix('models/players/')
    if value.endswith('.skin'):
        folder='models/players/'+str(Path(value).parent);body_skin='models/players/'+value
    else:
        folder,_,variant=value.partition('/');folder='models/players/'+folder
        body_skin=folder+'/body_'+(variant or 'default')+'.skin'
    head=e.get('head','default') or 'default'
    head_skin=rtcw.q3.key_name(head) if head.lower().endswith('.skin') else folder+'/head_'+head.lower()+'.skin'
    return folder,body_skin,head_skin,body_model


def npc_model(game,appearance):
    folder,body_skin,head_skin,body_model=appearance
    parts,meta=rtcw.character_parts(game,folder,body_skin,head_skin,body_model=body_model)
    if meta['pose'].startswith('Frame 0:'):
        meta['warnings'].append(folder+': no named idle/stand; frame 0 preview')
    surfaces=[]
    for part in parts:
        for original in part['model']['surfaces']:
            surface=dict(original);names=original['shaders']
            shader=part['skin'].get(original['name'],names[0] if names else '')
            if shader.lower() in ('off','*off','nodraw'):continue
            surface['shaders']=[shader]
            # transform() returns viewer axes; placed_model serializes native axes.
            for field,normal in (('vertices',False),('normals',True)):
                turned=rtcw.q3.transform(original[field],part['tags'],normal)
                surface[field]=[v for i in range(0,len(turned),3) for v in (turned[i+2],turned[i],turned[i+1])]
            surfaces.append(surface)
    if not any(s['indices'] for s in surfaces):raise ValueError('NPC assembly has no drawable surfaces')
    return dict(surfaces=surfaces),meta


def import_map(game,name,ident,raw,source,output,textures,model_textures, *, rules=None):
    # RTCW remains the default; ET supplies only rules that differ.
    if rules is None:
        import sys
        rules=sys.modules[__name__]
    bsp_notes=[]
    lumps=rules.read_bsp(raw,bsp_notes);things=qmap.entities(lumps[0].decode('latin1'));models=list(struct.iter_unpack('<6f4i',lumps[7]))
    if hasattr(rules,'prepare_entities'):things=rules.prepare_entities(game,name,things)
    states={};audit=[];unresolved=[]
    for i,e in enumerate(things):
        if not re.fullmatch(r'\*\d+',e.get('model','')):continue
        idx=int(e['model'][1:])
        if not 0<idx<len(models):raise ValueError(f'Entity {i}: invalid inline model')
        state=rules.brush_state(e,models[idx],things);state['hidden']=rules.filtered(e,game.section) or state['hidden'];states[idx]=state
    collision=getattr(rules,'Collision',Collision)(lumps,states);draw=brush_geometry(lumps,states)
    names=[(qmap.shader_name(s.split(b'\0')[0].decode('latin1')),flags) for s,flags,_ in struct.iter_unpack('<64sii',lumps[1])]
    # Raven draws a flare surface with its own shader (FLARE_CARDS); id's renderer with its flare shader.
    kinds=[struct.unpack_from('<i',draw[13],at+8)[0] for at in range(0,len(draw[13]),104)]
    used={struct.unpack_from('<i',draw[13],at*104)[0] for at,kind in enumerate(kinds) if kind in ((1,2,3,4) if getattr(rules,'FLARE_CARDS',False) else (1,2,3))}
    shaders=[None]*len(names);limits=set(NOT_DRAWN)
    if hasattr(rules,'NPC_DEFAULTS'):
        limits.discard('AI characters')
        limits.add('NPCs: authored idle assemblies; scripts, runtime populations and held weapons not reconstructed')
    for i in sorted(used):
        if names[i][1]&qmap.SURF_NODRAW:continue
        shaders[i],missing,notes=rules.describe(game,names[i][0],textures);unresolved+=missing;limits.update(notes)
    # The sky portal's camera (props_skyportal, JA's misc_skyportal): its origin; the view lends it angles and fov.
    portal=next((e for e in things if e.get('classname','').lower() in ('props_skyportal','misc_skyportal')),None)
    effects=dict(weather=weather(things),skyPortal=portal and dict(origin=vector(portal.get('origin'))))
    if getattr(rules,'ID_FLARES',True):
        effects['coronas']=coronas(things,game.section,rules)
        if effects['coronas'] or 4 in kinds:
            effects['flareShader'],missing,_=rules.describe(game,'flareshader',textures);unresolved+=missing
        effects['sun'],missing=sun(game,shaders,textures,rules);unresolved+=missing
    placed=[];item_models={};teams=set()
    for serial,e in (enumerate(things) if not hasattr(rules,'place_entities') else []):
        cls=e.get('classname','').lower();reason=rules.filtered(e,game.section);status='omitted';paths=[]
        row=dict(entity=serial,classname=cls)
        try:
            flags=int(number(e,'spawnflags'));model=e.get('model','');item=rules.ITEMS[game.section].get(cls)
            if reason:pass
            elif cls in getattr(rules,'NPC_DEFAULTS',{}):
                # One authored actor preview, including triggered/scripted actors.
                # Bosses/undead count; controls, vehicles and runtime populations do not.
                appearance=npc_appearance(e)
                key=hashlib.sha256(json.dumps(['npc',appearance]).encode()).hexdigest()[:24]
                if key not in item_models:
                    actor,meta=npc_model(game,appearance)
                    desc,missing=placed_model(game,appearance[0],'',meta['frame'],output,textures,model_textures,model=actor)
                    desc['npcSource']=dict(zip(('folder','bodySkin','headSkin','bodyModel'),appearance),**meta)
                    desc['limitations']+=meta['warnings'];item_models[key]=desc;unresolved+=missing
                    limits.update(desc['limitations'])
                meta=item_models[key]['npcSource']
                placed.append(dict(models=[key],origin=vector(e.get('origin')),angles=angles(e),
                    scale=[v*meta['scale'] for v in scale(e)],spin=False,entity=serial,classname=cls,npc=True))
                row.update(status='drawn',reason='NPC idle preview at authored position; scripts not executed',
                    npc=True,spawnflags=flags,pose=meta['pose'],frame=meta['frame'])
                audit.append(row);continue
            elif cls.startswith('ai_'):reason='AI control or unsupported class; no actor preview'
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
            elif cls=='corona':status,reason=('omitted','START_OFF corona') if flags&1 else ('drawn','corona with the flare shader (CG_Corona)')
            elif cls=='dlight':reason=getattr(rules,'DLIGHT_NOTE','dynamic light: none drawn with r_dynamiclight 0 (wolfconfig.cfg)')
            elif cls=='props_skyportal':reason='sky portal camera: the sky shows the scene from here (CG_DrawSkyBoxPortal)'
            elif cls.startswith(('trigger_','target_','info_','light','path_')) or cls in ('worldspawn','misc_vis_dummy','misc_vis_dummy_multiple'):
                reason='world/control/light entity; no standalone visual'
            elif cls in ('script_model_med','props_decoration') and flags&1:reason='START_INVIS'
            elif cls=='misc_spotlight':reason='spotlight camera/effect not reconstructed'
            elif cls=='misc_mg42':
                paths=rules.mounted_paths(e) if hasattr(rules,'mounted_paths') else ['models/mapobjects/weapons/mg42a.md3']+([] if flags&2 else ['models/mapobjects/weapons/mg42b.md3']);reason=''
            else:
                path=(e.get('model2') or model) if cls in ('props_decoration','props_statue') else model or e.get('model2') or rules.PROP_MODELS.get(cls)
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
                            retry=vector(e.get('origin'));retry[2]-=10
                            pos,floor=collision.drop(retry,(-10,-10,0),(10,10,5),mask=1)
                            if floor=='startsolid':
                                row.update(status='omitted',reason='FinishSpawningItem frees entity: startsolid after retry')
                                audit.append(row);continue
                    spin=bool(flags&2) and (item[0]!='weapon' or bool(flags&1))
                    if item[0]=='weapon':
                        size=[s*1.5 for s in size]
                        stand=game.model_path(item[2]) if item[2] else ''
                        if stand in game.assets:
                            stand_model=rtcw.read_model(game.read(stand));tag=stand_model['tags'].get('tag_stand'+(str(frame) if frame else ''))
                            if tag is None:raise ValueError('Weapon stand has no requested tag_stand')
                            # A valid stand may contain only placement tags. The
                            # engine still attaches the gun, but has no stand mesh to draw.
                            if stand_model['surfaces']:paths.append(stand)
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
                    placed.append(dict(models=[key],origin=placed_pos,angles=rot,scale=size,spin=spin,entity=serial,**({'pickup':True} if item else {})))  # CG_Item gives pickups RF_MINLIGHT.
                status='drawn';reason='pickup at spawn; '+floor if item else 'model decoration at authored spawn pose'
            row.update(status=status,reason=reason)
        except (ValueError,OSError,struct.error,zipfile.BadZipFile,KeyError) as exc:
            row.update(status='failed',reason=str(exc));unresolved.append(f'entity {serial} {cls}: {exc}')
        audit.append(row)
    if hasattr(rules,'place_entities'):
        placed,item_models,audit,extra_missing,extra_limits=rules.place_entities(game,things,states,collision,output,textures,model_textures)
        unresolved+=extra_missing;limits.update(extra_limits)
    views=rules.viewpoints(things,collision,game.section)
    if not views:limits.add('map has no player start; camera at world origin')
    for v in views:
        if v['floor']!='brush hull floor':limits.add('start floor: '+v['floor'])
    limits.add('hull floor sweep uses brush planes and preview patch triangles; curved-floor tolerances may differ from the game')
    if game.section=='mp':limits.add('first eligible Axis initial spawn, then Allies; game randomly chooses among eligible team starts')
    fogs=qmap.fog_volumes(lumps,game.shaders);wolf_fog,far=map_fog(game,shaders)
    if getattr(rules,'GLOBAL_FOG_CLIPS',False):far+=[f['depth'] for f in fogs if f and f.get('global')]
    digest=hashlib.sha256(b''.join(draw)).hexdigest()[:24];file='bsp/'+digest+'.bsp'
    if not (output/file).exists():qmap.write_bsp(output/file,draw,version=47)
    scene=dict(name=name,longname=things[0].get('message','') if things else '',source=source,section=game.section,bsp=file,bspVersion=47,
               shaders=shaders,viewpoints=views,models=[],items=[],placed=placed,itemModels=item_models,lighting=game.lighting,bspCull=qmap.read_visibility(lumps),
               fogs=fogs,**({'mapFog':wolf_fog} if wolf_fog else {}),**({'farClip':max(64,min(far))} if far else {}),
               movers=qmap.movers(things,lumps[7],getattr(rules,'MOVERS','wolf'),lambda index,e:mover_pose(states,index,e)),
               **{key:value for key,value in effects.items() if value},
               entityResults=audit,bspNotes=bsp_notes,unresolved=sorted(set(unresolved)),outside=[],guessed=[],limits=sorted(limits))
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
                result['maps'].append(dict(section=section,map=name,source=source,status=status,bspNotes=scene.get('bspNotes',[])))
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

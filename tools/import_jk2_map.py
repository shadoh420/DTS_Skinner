"""Jedi Outcast RBSP1 maps, converted to the shared static map preview format.

OpenJK JK2 mode and JK2MV are rules-only references; no engine code is included.
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
from types import SimpleNamespace
import zipfile

import numpy as np

try:
    from tools import import_jk2 as jk2, import_ja as ja, import_rtcw_map as base, raven_efx
    from tools.jk2_map_items import MP_ITEMS
    from tools.model_data import read_geometry_buffers
except ImportError:
    import import_jk2 as jk2
    import import_ja as ja
    import import_rtcw_map as base
    import raven_efx
    from jk2_map_items import MP_ITEMS
    from model_data import read_geometry_buffers

qmap,rtcw=base.qmap,jk2.base
number,vector=base.number,base.vector
STRIDES=(1,72,16,36,48,4,4,40,12,12,80,4,72,148,49152,30,1,2)
SECTIONS={'sp':'Single player','mp':'Multiplayer'}
NOT_DRAWN=['NPCs: one authored idle preview; no AI, script populations or held weapons', 'ICARUS scripts and later mover actions',
           'surfaceSprites/ss foliage, weather zones, wind and lightning, and audio',
           'portal cameras and mirrors; sky portal fog', 'dynamic lights on props; light grid sampled at spawn',
           'runtime lightstyle animation (first sample shown)']


def lighting_profile(install,section,game=None,*,device_supports_gamma=False):
    game=game or jk2.Game(install)
    root=Path(install)
    if (root/'GameData/base').is_dir():root=root/'GameData'
    result=base.lighting_profile(root,section,game,directory='base',
        configs=('default.cfg','jk2config.cfg' if section=='sp' else 'jk2mpconfig.cfg','autoexec.cfg'),
        defaults=dict(r_mapoverbrightbits=0,r_overbrightbits=1,cg_fov=80,g_spskill=0,
                      r_texturemode='GL_LINEAR_MIPMAP_NEAREST',r_ambientscale=.5 if section=='sp' else .6,r_directedscale=1),
        device_supports_gamma=device_supports_gamma)
    bits=min(1,result['overbrightBits']);result['overbrightBits']=bits
    result['identityLight']=2**-bits;result['displayScale']=2**bits if result['hardwareGamma'] else 1
    # Retail SP 1.03 has no mapOverBrightBits cvar. The measured 1x world result
    # agrees with JK2MV's shift (1-overbright, only when overbright is enabled).
    result['cvars'].pop('r_mapoverbrightbits',None)
    result['sourceLightScale']=1
    # The saved SP config says trilinear, but retail gfxinfo at the comparison
    # viewpoint reports this mode. Target that measured run, retaining cfg evidence.
    result['configuredTextureMode']=result['textureMode']
    result['textureMode']='GL_LINEAR_MIPMAP_NEAREST'
    result['lightScale']=1  # Raven's four channels are shifted individually before combining.
    result['source']='JK2 retail SP 1.03 measured software gamma and nearest mip level; 1x BSP lighting, no mapOverBrightBits cvar. Hardware gamma remains an optional profile.'
    return result


class Game(jk2.Game):
    profile=staticmethod(lighting_profile)
    mp_items=MP_ITEMS
    model_rules=jk2
    def __init__(self,install,section='sp'):
        super().__init__(install);self.section=section
        self.files={n:(a['source'],a['read']) for n,a in self.assets.items()}
        self.definitions=rtcw.shader_definitions(self)
        self.shaders={n:(b,s) for n,(b,s,_) in self.definitions.items()}
        self.shader_label={n:self.assets[p]['source'] for n,(_,_,p) in self.definitions.items()}
        self.outside,self.images,self.described,self.models,self.effects=set(),{},{},{},{}
        self.lighting=self.profile(install,section,self)
        self.skill=min(2,max(0,int(self.lighting['cvars']['g_spskill'])))
        self.items=sp_items(self.read('ext_data/items.dat')) if section=='sp' and 'ext_data/items.dat' in self.assets else {
            c:dict(kind=k,models=[p],mins=[-15]*3,maxs=[15]*3) for c,(k,p) in self.mp_items.items()}


def sp_items(raw):
    result={}
    text=re.sub(r'//[^\n\r]*','',rtcw.q3.read_text(raw))
    for block in re.findall(r'\{([^{}]*)\}',text):
        values={}
        for line in block.splitlines():
            parts=line.split(None,1)
            if len(parts)==2:values[parts[0].lower()]=parts[1].strip().strip('"')
        if 'classname' not in values:continue
        result[values['classname'].lower()]=dict(kind=values.get('type','').lower().removeprefix('it_'),
            models=[values['worldmodel']] if values.get('worldmodel') else [],
            mins=vector(values.get('min'),(-15,-15,-2)),maxs=vector(values.get('max'),(15,15,15)))
    return result


def section_for(name):
    if name.startswith(('ffa_','ctf_','duel_')):return 'mp','multiplayer prefix and arena/deathmatch entities'
    return 'sp','story or SP test map; info_player_start and SP NPC/script entities'


def read_lumps(raw):
    if len(raw)<152 or raw[:4]!=b'RBSP' or struct.unpack_from('<i',raw,4)[0]!=1:
        raise ValueError('Expected Raven RBSP version 1')
    lumps=[];ranges=[]
    for i,((at,size),stride) in enumerate(zip(struct.iter_unpack('<2i',raw[8:152]),STRIDES)):
        if at<0 or size<0 or at+size>len(raw) or (size and at<152):raise ValueError(f'Lump {i} outside RBSP')
        if size%stride:raise ValueError(f'Lump {i}: partial {stride}-byte record')
        if size:ranges.append((at,at+size,i))
        lumps.append(raw[at:at+size])
    ranges.sort()
    if any(a[1]>b[0] for a,b in zip(ranges,ranges[1:])):raise ValueError('Overlapping RBSP lumps')
    if len(lumps[0])>1024*1024 or len(lumps[10])//80>4000000:raise ValueError('RBSP exceeds preview bounds')
    for index, in struct.iter_unpack('<H',lumps[17]):
        if index>=len(lumps[15])//30:raise ValueError('Invalid light-grid array index')
    return lumps


def light_styles(things,section='sp'):
    # First samples of stock styles, represented as channel amplitudes (a=0,z=1).
    first=[25,12,0,12,12,9,13,12,12,0,12,0,12]
    colors={i:[n/25]*3 for i,n in enumerate(first)};colors[13]=[0,1,0]
    world=things[0] if things else {}
    for i in range(1,32):
        for channel,c in enumerate('rgb'):
            pattern=world.get(f'ls_{i}{c}')
            if pattern:colors.setdefault(i,[1,1,1])[channel]=min(1,max(0,(ord(pattern[0])-97)/25))
    # Unconfigured style colors are white. Switchable lights initialize their
    # assigned style from on/off style or full brightness/darkness.
    if section=='sp':
        for e in things:
            if e.get('classname','').lower()!='light' or not e.get('targetname'):continue
            i=int(number(e,'style'));off=bool(int(number(e,'spawnflags'))&4)
            source=int(number(e,'style_off' if off else 'switch_style'))
            colors[i]=list(colors.get(source,[1,1,1])) if source else [0 if off else 1]*3
    return colors


def ibsp_bytes(lumps):
    data=bytearray(144);struct.pack_into('<4si',data,0,b'IBSP',47)
    for i,lump in enumerate(lumps):
        struct.pack_into('<2i',data,8+i*8,len(data),len(lump));data.extend(lump)
    return bytes(data)


def read_bsp(raw,diagnostics=None,*,section='sp',light_scale=1):
    """Validate Raven records, bake starting light styles, emit Q3 draw records."""
    notes=diagnostics if diagnostics is not None else []
    raven=read_lumps(raw);lumps=list(raven[:17]);things=qmap.entities(lumps[0].decode('latin1'))
    styles=light_styles(things,section)
    surfaces=list(struct.iter_unpack('<7i8B14i12f2i',raven[13]))
    used_styles=sorted({i for s in surfaces for i in s[7:15] if i<64})
    notes.append(dict(action='Raven starting light styles',sampleTime=0,values={i:styles.get(i,[1,1,1]) for i in used_styles}))
    source=np.frombuffer(raven[10],dtype=np.dtype([('f','<f4',16),('c','u1',16)]))
    lightmaps=np.frombuffer(raven[14],dtype='u1').reshape((-1,128,128,3))
    def shifted(rgb):
        rgb=np.asarray(rgb,dtype=float)*light_scale
        return rgb*(255/np.maximum(255,rgb.max(axis=-1,keepdims=True)))
    def color(style):
        if style in (254,255):return [0,0,0]
        if style>=64:raise ValueError('Invalid light style '+str(style))
        return styles.get(style,[1,1,1])
    verts=bytearray();faces=bytearray();tiles=[];cache={}
    for n,s in enumerate(surfaces):
        shader,fog,kind,first,count,start,nindex=s[:7]
        if min(first,count)<0 or first+count>len(source):raise ValueError(f'Surface {n}: invalid vertex range')
        if len(verts)//44+count>4000000:raise ValueError('Expanded draw vertices exceed preview bound')
        f=source['f'][first:first+count];c=source['c'][first:first+count].reshape((-1,4,4))
        lmstyles,vcstyles=s[7:11],s[11:15];lmnums=s[15:19]
        slots=next((j for j,style in enumerate(lmstyles) if style==255),4)
        active=[j for j in range(slots) if lmnums[j]>=0] if kind in (1,2) else []
        for j in active:
            if lmnums[j]>=len(lightmaps):raise ValueError(f'Surface {n}: invalid lightmap {lmnums[j]}')
        lm=-3 if kind==3 or lmnums[0]==-3 else -1
        uv=f[:,5:7].copy()
        if active and count:
            firstslot=active[0];uv=f[:,5+2*firstslot:7+2*firstslot].copy();offsets=[]
            if not np.isfinite(uv).all():raise ValueError(f'Surface {n}: nonfinite lightmap UV')
            for j in active:
                delta=f[:,5+2*j:7+2*j]-uv
                if not np.isfinite(delta).all() or not np.allclose(delta,delta[0],atol=0.0001):
                    raise ValueError(f'Surface {n}: lightstyle UVs do not share a translation')
                offsets.append(tuple(int(round(x*128)) for x in delta[0]))
            key=tuple((lmnums[j],lmstyles[j],offset) for j,offset in zip(active,offsets))
            if key not in cache:
                if len(tiles)>=4096:raise ValueError('Composite lightmaps exceed preview bound')
                total=np.zeros((128,128,3));grid=np.arange(128)
                for j,(dx,dy) in zip(active,offsets):
                    pixels=lightmaps[lmnums[j]][np.clip(grid+dy,0,127)[:,None],np.clip(grid+dx,0,127)[None,:]]
                    total+=shifted(pixels)*color(lmstyles[j])
                cache[key]=len(tiles);tiles.append(np.clip(total,0,255).astype('u1').tobytes())
            lm=cache[key]
        else:uv=np.nan_to_num(uv,nan=0,posinf=0,neginf=0)
        rgb=np.zeros((count,3))
        for j in range(4):
            if vcstyles[j]==255:break
            rgb+=shifted(c[:,j,:3])*color(vcstyles[j])
        rgba=np.column_stack((np.clip(rgb,0,255).astype('u1'),c[:,0,3]))
        newfirst=len(verts)//44
        for v,u,cc in zip(f,uv,rgba):
            verts.extend(struct.pack('<10f4B',*v[:5],*u,*v[13:16],*cc))
        faces.extend(struct.pack('<12i12f2i',shader,fog,kind,newfirst,count,start,nindex,lm,s[19],s[23],s[27],s[28],*s[29:41],*s[41:43]))
        if len(active)>1:notes.append(dict(surface=n,action='starting light styles combined',styles=list(lmstyles),lightmaps=list(lmnums)))
    sides=bytearray()
    for plane,shader,draw in struct.iter_unpack('<3i',raven[9]):
        if draw < -1 or draw>=len(surfaces):notes.append(dict(action='unused brushside drawSurfNum ignored',value=draw,reason='CMod_LoadBrushSides reads only plane and shader'))
        sides.extend(struct.pack('<2i',plane,shader))
    lumps[9],lumps[10],lumps[13],lumps[14],lumps[15]=bytes(sides),bytes(verts),bytes(faces),b''.join(tiles),b''
    # Only translate bits consumed by the shared viewer/collision; all other
    # Raven flags must not acquire unrelated Quake meanings.
    shaders=bytearray(lumps[1])
    for at in range(0,len(shaders),72):
        flags,contents=struct.unpack_from('<2i',shaders,at+64)
        struct.pack_into('<2i',shaders,at+64,qmap.SURF_NODRAW if flags&0x200000 else 0,
                         (contents&1)|(0x10000 if contents&0x10 else 0)|(0x20000 if contents&0x80 else 0))
    lumps[1]=bytes(shaders)
    return base.read_bsp(ibsp_bytes(lumps),notes)


class LightGrid:
    """Raven's indexed 30-byte light samples, evaluated once per placed entity.

    Rules: R_LoadLightGrid[Array], R_SetupEntityLightingGrid/Lighting in the
    classic SP/MP renderers. No dynamic lights or runtime style animation.
    """
    def __init__(self,lumps,lighting,section='sp'):
        things=qmap.entities(lumps[0].decode('latin1'))
        world=things[0] if things else {}
        self.styles=light_styles(things,section)
        self.lighting=lighting
        self.size=np.array(vector(world.get('gridsize'),(64,64,128)))
        if not np.all(np.isfinite(self.size)) or np.any(self.size<=0):raise ValueError('Invalid light-grid size')
        bounds=np.array(struct.unpack_from('<6f',lumps[7]))
        if not np.all(np.isfinite(bounds)):raise ValueError('Non-finite light-grid bounds')
        self.origin=np.ceil(bounds[:3]/self.size)*self.size
        self.bounds=np.floor((np.floor(bounds[3:]/self.size)*self.size-self.origin)/self.size).astype(np.int64)+1
        self.data=np.frombuffer(lumps[15],dtype=np.uint8).reshape(-1,30)
        self.index=np.frombuffer(lumps[17],dtype='<u2')
        if len(self.index) and int(self.index.max())>=len(self.data):raise ValueError('Invalid light-grid array index')
        self.valid=bool(len(self.data) and np.all(self.bounds>0) and math.prod(int(x) for x in self.bounds)==len(self.index))
        self.reason='' if self.valid else 'missing or mismatched light grid; renderer fallback lighting'

    def sample(self,origin):
        identity=self.lighting['identityLight']
        ambient=np.zeros(3);directed=np.zeros(3);direction=np.zeros(3);total=0
        if self.valid:
            cell=(np.array(origin)-self.origin)/self.size
            floor=np.floor(cell);fraction=cell-floor
            pos=np.clip(floor,0,self.bounds-1).astype(np.int64)
            step=[1,int(self.bounds[0]),int(self.bounds[0])*int(self.bounds[1])]
            start=sum(int(p)*s for p,s in zip(pos,step))
            for corner in range(8):
                high=[(corner>>j)&1 for j in range(3)]
                weight=math.prod(fraction[j] if high[j] else 1-fraction[j] for j in range(3))
                at=start+sum(h*s for h,s in zip(high,step))
                if not weight or at>=len(self.index):continue
                sample=self.data[self.index[at]]
                if sample[24]==255:continue  # Inside a wall: renormalize other samples.
                total+=weight
                for j,style in enumerate(sample[24:28]):
                    if style==255:break
                    color=np.array(self.styles.get(int(style),[1,1,1])) if style!=254 else np.zeros(3)
                    ambient+=weight*sample[j*3:j*3+3]*color
                    directed+=weight*sample[12+j*3:15+j*3]*color
                # The engine samples a 1024-entry sine table (denominator 1023).
                lat,lng=int(sample[29])*4,int(sample[28])*4
                sine=lambda i:math.sin((i&1023)*2*math.pi/1023)
                direction+=weight*np.array([sine(lat+256)*sine(lng),sine(lat)*sine(lng),sine(lng+256)])
            if 0<total<.99:ambient/=total;directed/=total
            ambient*=max(0,self.lighting['cvars']['r_ambientscale'])
            directed*=max(0,self.lighting['cvars']['r_directedscale'])
        else:
            ambient[:]=directed[:]=identity*150
            direction=np.array([.45,.3,.9])  # Renderer default sun direction.
        length=np.linalg.norm(direction)
        if length:direction/=length
        ambient=np.minimum(ambient+identity*32,int(identity*255))
        return dict(ambient=ambient.tolist(),directed=directed.tolist(),direction=direction.tolist(),
                    source=self.reason or 'indexed BSP light grid at spawn')


class ModelTextures(base.ModelTextures):
    def __init__(self,game,output):
        super().__init__(game,output);self.shaders=game.model_rules.shader_maps(game)


class Collision(base.Collision):
    def __init__(self,lumps,states):
        super().__init__(lumps,states,contents_mask=1|0x10000|0x20000)

    def mount_mine(self,origin,angles):
        direction=rtcw.axis([math.radians(a) for a in angles])[0]
        nearest=129;normal=None
        for contents,planes in self.brushes:
            if not contents&(1|0x20000):continue
            low,high=0,128;entry=None;inside=True
            for plane in planes:
                distance=sum(a*b for a,b in zip(plane[:3],origin))-plane[3]
                speed=sum(a*b for a,b in zip(plane[:3],direction));inside &= distance<=0
                if abs(speed)<1e-10:
                    if distance>0:high=-1;break
                elif speed<0:
                    at=-distance/speed
                    if at>low:low=at;entry=plane[:3]
                else:high=min(high,-distance/speed)
            if inside:raise ValueError('Trip mine starts inside a solid/shotclip brush')
            if entry is not None and 0<=low<=high and low<nearest:nearest=low;normal=entry
        if normal is None:raise ValueError('Trip mine finds no brush within 128 units; patch/model mounting needs game comparison')
        pos=[a+nearest*b for a,b in zip(origin,direction)]
        return pos,[-math.degrees(math.atan2(normal[2],math.hypot(*normal[:2]))),math.degrees(math.atan2(normal[1],normal[0])),0]


def describe(game,name,textures):
    if name in game.described:return game.described[name]
    body,stages=game.shaders.get(name,([],[]));notes=[];kept=[]
    for stage in stages:
        if any(r[0]=='surfacesprites' for r in stage):notes.append('surfaceSprites/ss foliage not drawn');continue
        kept.append([['rgbgen','lightingdiffuse'] if r[:2]==['rgbgen','lightingdiffuseentity'] else r for r in stage])
    if name in game.shaders:game.shaders[name]=(body,kept)  # Script-less names keep the implicit image + lightmap.
    result,missing,limits=base.describe(game,name,textures,draw_fog_stages=True,inner_box=False)
    sky=next((r for r in body if r[0]=='skyparms' and len(r)>1 and r[1]!='-'),None)
    if sky and 'sky' in result:
        previous='$default';sides=[]
        for side in qmap.SKY_SIDES:
            image=textures.resolve(game,sky[1]+'_'+side+'.tga')
            sides.append(image or previous);previous=sides[-1]
        if sides[0]!='$default':result['sky']['box']=sides
    game.described[name]=(result,missing,sorted(set(limits+notes)))
    return game.described[name]


def filtered(e,section,skill=0):
    cls=e.get('classname','').lower()
    if section=='sp':
        if number(e,'notsingle') or int(number(e,'spawnflags'))&(1<<(8+skill)):return 'excluded by SP difficulty/notsingle'
    else:
        if number(e,'notfree'):return 'notfree: excluded in FFA'
        if e.get('gametype') and 'ffa' not in e['gametype'].lower().split():return 'gametype excludes FFA'
        if cls.startswith('team_ctf_') or cls=='misc_holocron':return 'CTF/holocron entity excluded in FFA'
    return ''


def brush_state(e,model,things):
    cls=e.get('classname','').lower();flags=int(number(e,'spawnflags'))
    # Shared door/plat/train spawn transforms; JK2 visibility has different bits.
    state=base.brush_state(e,model,things);state['hidden']='';state['model2']=''
    if cls.startswith(('trigger_','target_')) or cls in ('func_timer','func_areaportal'):state['hidden']='server-only brush'
    if cls in ('func_usable','func_wall') and flags&1:state['hidden']='START_OFF'
    if cls=='func_rotating':state['angles']=base.angles(e)
    if cls=='func_bobbing' and flags&4:
        state['origin']=vector(e.get('origin'));state['origin'][0 if flags&1 else 1 if flags&2 else 2]+=math.sin(2*math.pi*number(e,'phase'))*number(e,'height',32)
    return state


def viewpoints(things,collision,section):
    cls='info_player_start' if section=='sp' else 'info_player_deathmatch'
    starts=[e for e in things if e.get('classname','').lower()==cls and not e.get('targetname') and not filtered(e,section) and not number(e,'nohumans')]
    if not starts:starts=[e for e in things if e.get('classname','').lower()==cls and not filtered(e,section)]
    if section=='mp':starts.sort(key=lambda e:not int(number(e,'spawnflags'))&1)
    result=[]
    for e in starts:
        pos=vector(e.get('origin'));pos[2]+=9
        pos,floor=collision.drop(pos,(-16,-16,-24),(16,16,40));pos[2]+=36
        a=base.angles(e);result.append(dict(origin=pos,pitch=a[0],yaw=a[1],floor=floor,classname=cls))
    return result


PROP_MODELS={
    'misc_shield_floor_unit':'models/items/a_shield_converter.md3',
    'misc_ammo_floor_unit':'models/items/a_pwr_converter.md3',
    'misc_maglock':'models/map_objects/imp_detention/door_lock.md3',
    'misc_camera':'models/map_objects/kejim/impcam.md3',
    'misc_model_welder':'models/map_objects/cairn/welder.glm',
    'misc_model_jabba_cam':'models/map_objects/nar_shaddar/jabacam/jabacam.glm',
    'misc_exploding_crate':'models/map_objects/nar_shaddar/crate_xplode.md3',
    'misc_gas_tank':'models/map_objects/imp_mine/tank.md3',
    'misc_crystal_crate':'models/map_objects/imp_mine/crate_open.md3',
    'emplaced_gun':'models/map_objects/imp_mine/turret_chair.glm',
    'misc_atst_drivable':'models/players/atst/model.glm',
    'misc_turret':'models/map_objects/imp_mine/turret_canon.glm',
    'misc_ns_turret':'models/map_objects/nar_shaddar/turret/turret.glm',
    'misc_panel_turret':'models/map_objects/imp_mine/ladyluck_gun.md3',
    'misc_ion_cannon':'models/map_objects/imp_mine/ion_cannon.glm',
    'misc_spotlight':'models/map_objects/imp_mine/spotlight.md3',
    'misc_security_panel':'models/map_objects/kejim/sec_panel.md3',
    'misc_model_gun_rack':'models/map_objects/kejim/weaponsrack.md3',
    'misc_model_ammo_rack':'models/map_objects/kejim/weaponsrung.md3',
    'misc_sentry_turret':'models/items/psgun.glm',
    'misc_trip_mine':'models/weapons2/laser_trap/laser_trap_w.glm',
}


def rack_items(e):
    """Spawn-time rack contents, with random jitter fixed at its midpoint."""
    cls=e.get('classname','').lower();flags=int(number(e,'spawnflags'));slots=[]
    if cls=='misc_model_gun_rack':
        kinds=[(name,z) for bit,name,z in [(1,'weapon_blaster',23),(2,'weapon_repeater',24.5),(4,'weapon_rocket_launcher',25.5)] if flags&bit]
        kinds=kinds or [('weapon_blaster',23)]
        kinds += [kinds[0]]*(3-len(kinds))
        slots=[(name,(i-1)*9,z) for i,(name,z) in enumerate(kinds)]
    elif cls=='misc_model_ammo_rack':
        bits=flags&39 or 1
        kinds=[name for bit,name in [(1,'ammo_blaster'),(2,'ammo_metallic_bolts'),(32,'ammo_powercell'),(4,'ammo_rockets')] if bits&bit]
        if not flags&64:kinds += [kinds[0]]*max(0,3-len(kinds))
        slots=[(name,(i-1)*8,7) for i,name in enumerate(kinds)]
        weapons=[(name,z) for bit,name,z in [(1,'weapon_blaster',25.5),(2,'weapon_repeater',27),(4,'weapon_rocket_launcher',28)] if bits&bit]
        if flags&8 and weapons:slots.append((weapons[0][0],7,weapons[0][1]))
        if flags&16:slots.append(('item_medpak_instant',-6 if flags&8 and weapons else 6,24))
    state=dict(origin=vector(e.get('origin')),angles=base.angles(e),scale=[1]*3)
    result=[]
    for cls,right,z in slots:
        pos=base.transformed([0,-right,z],state);a=base.angles(e)
        # GunRackAddItem rotates weapon yaw 180 before the vertical-item pitch.
        a[1]+=180 if cls.startswith('weapon_') else 90
        result.append(dict(classname=cls,origin=' '.join(map(str,pos)),angles=' '.join(map(str,a)),spawnflags=str(17 if cls.startswith('weapon_') else 1)))
    return result


def entity_filter(game,e):
    return filtered(e,game.section,game.skill)


def load_placed(game,path,skin,frame):
    sk=jk2.read_skin(game.read(skin))['surfaces'] if skin and not skin.isdigit() else {}
    return jk2.load_model(game,path,sk,frame=frame)


BAKED_MODELS=('misc_model','misc_model_static')


# OpenJK NPC_spawn.cpp aliases/low spawnflag bits. Random appearance choices
# use one fixed member, never a generated population. Most wrappers honor NPC_type;
# the named-character wrappers below unconditionally assign their own type.
NPC_VARIANTS={
    'stormtrooper':((8,'rockettrooper'),(4,'stofficeralt'),(2,'stcommander'),(1,'stofficer')),
    'imperial':((1,'impofficer'),(2,'impcommander')),
    'galak':((1,'galak_mech'),), 'jedi':((1,'jeditrainer'),),
    'gran':((1,'granshooter'),(2,'granboxer')), 'rodian':((1,'rodian2'),),
    'swamptrooper':((1,'swamptrooper2'),),
    'reborn':((1,'rebornforceuser'),(2,'rebornfencer'),(4,'rebornacrobat'),(8,'rebornboss')),
    'r2d2':((1,'r2d2_imp'),), 'r5d2':((1,'r5d2_imp'),), 'protocol':((1,'protocol_imp'),),
}
JA_NPC_VARIANTS={
    'jedi':((4,'jedi_hf1'),(2,'jedimaster'),(1,'jeditrainer')),
    'human_merc':((1,'human_merc_bow'),(2,'human_merc_rep'),(4,'human_merc_flc'),(8,'human_merc_cnc')),
    'cultist':((1,'cultist_saber_med'),(2,'cultist_grip'),(4,'cultist_lightning'),(8,'cultist_drain')),
    'alora':((1,'alora_dual'),), 'rosh_penin':((1,'rosh_dark'),(2,'rosh_penin_noforce')),
    'tavion_new':((1,'tavion_scepter'),(2,'tavion_sith_sword')), 'jawa':((1,'jawa_armed'),),
    'hazardtrooper':((1,'hazardtrooperofficer'),(2,'hazardtrooperconcussion')),
    'rockettrooper':((1,'rockettrooper2officer'),(0,'rockettrooper2')),
    'kyle':((1,'kyle_boss'),), 'prisoner':((1,'elder'),), 'tusken':((1,'tuskensniper'),),
    'saboteur':((1,'saboteursniper'),(2,'saboteurpistol'),(4,'saboteurcommando')),
    'rancor':((1,'mutant_rancor'),), 'sand_creature':((1,'sand_creature_fast'),),
    'kothos':((1,'vkothos'),(0,'dkothos')), 'saber_droid':((1,'saber_droid_training'),),
    'remote':((0,'remote_sp'),),
}
NPC_ALIASES={'tie_pilot':'stormpilot','stormtrooperofficer':'stofficer',
             'chewbacca':'chewie','bobafett':'boba_fett','assassin':'assassin_droid',
             'saber':'saber_droid','cultist_commando':'cultistcommando'}
NPC_FORCED_TYPES={'kyle','lando','jan','luke','monmothma','tavion','reelo','desann','bartender','morgankatarn',
                 'galak','rosh_penin','tavion_new','alora','rax','bobafett','ragnos','lannik_racto','kothos','chewbacca'}


def npc_definitions(game):
    if not hasattr(game,'npc_definitions'):
        result={}
        paths=sorted(n for n in game.assets if n=='ext_data/npcs.cfg' or re.fullmatch(r'ext_data/npcs/[^/]+\.npc',n))
        for path in paths:
            try:
                for block in ja.definitions(game.read(path)):
                    result.setdefault(block['name'].lower(),dict(block['values'],_source=path))
            except ValueError as exc:game.issues.append(dict(source=path,reason='NPC definition: '+str(exc)))
        game.npc_definitions=result
    return game.npc_definitions


def npc_spec(game,e):
    """Concrete authored NPC or a typed spawner preview; controls stay omitted."""
    cls=e.get('classname','').lower()
    if cls=='npc_vehicle':return None,'vehicle spawner excluded from NPCs'
    kind=next((str(v).lower() for k,v in e.items() if k.lower()=='npc_type' and v),'')
    if not kind or cls.removeprefix('npc_') in NPC_FORCED_TYPES:
        if cls=='npc_spawner':return None,'untyped NPC spawner; no invented actor'
        kind=cls.removeprefix('npc_').removeprefix('droid_').removeprefix('monster_')
        kind=NPC_ALIASES.get(kind,kind);flags=int(number(e,'spawnflags'))
        variants=NPC_VARIANTS.get(kind,())
        if game.game_id=='ja':variants=JA_NPC_VARIANTS.get(kind,variants)
        if game.game_id=='ja' and kind=='human_merc' and e.get('message'):kind='human_merc_key'
        elif game.game_id=='ja' and kind=='reborn_new':
            style=0 if flags&1 else 1 if flags&2 else 2
            kind=(('rebornmasterdual','rebornmasterstaff','rebornmaster') if flags&8 else
                  ('reborn_dual2','reborn_staff2','reborn_new2') if flags&4 else
                  ('reborn_dual','reborn_staff','reborn_new'))[style]
        elif game.game_id=='ja' and kind in ('cultist_saber','cultist_saber_powers'):
            powers=kind.endswith('_powers');kind='cultist_saber'
            kind+='_med' if flags&1 else '_strong' if flags&2 else ''
            if flags&8:kind+='_throw'
            # Preserve the source's fast-throw exception (and unreachable all-style branch).
            if powers and (flags&3 or not flags&8):kind+='2'
        else:kind=next((name for bit,name in variants if not bit or flags&bit),kind)
    row=npc_definitions(game).get(kind)
    if not row:return None,'no concrete NPC definition: '+kind
    if row.get('class','').lower().removeprefix('class_') in ('vehicle','atst'):
        return None,'vehicle NPC definition excluded: '+kind
    if kind in ('nulldriver','rocks'):return None,'script helper/prop NPC definition excluded: '+kind
    model=row.get('playermodel','').lower()
    if model=='player':return None,'player appearance depends on runtime customization'
    if not model:
        # Stock mouse/remote/seeker use a single lower MD3 rather than Ghoul2.
        model=row.get('legsmodel','').lower()
        if not model or model=='none' or any(row.get(k,'none').lower()!='none' for k in ('headmodel','torsomodel')):
            return None,'NPC definition has no supported character mesh: '+kind
        path='models/players/'+model+'/lower.md3';skin='models/players/'+model+'/lower_default.skin'
    else:
        folder='models/players/'+model+'/';path=folder+'model.glm'
        custom=row.get('customskin','default').lower()
        skin=folder+'|'+custom if '|' in custom else folder+'model_'+custom+'.skin'
    size=[number(row,'scale'+axis,number(row,'scale',100))/100 for axis in ('x','y','z')]
    if any(v<=0 or v>100 for v in size):raise ValueError('Invalid NPC definition scale')
    surface_flags={}
    for field,value in (('surfoff',2),('surfon',0)):
        for name in re.split(r'[\s,]+',row.get(field,'').lower()):
            if game.game_id=='ja':name=name.removesuffix('_off')
            if name:surface_flags[name]=value
    return dict(type=kind,definition=row['_source'],model=path,skin=skin,scale=size,surfaceFlags=surface_flags),''


def place_npc(game,e,serial,models,output,textures,model_textures):
    spec,reason=npc_spec(game,e)
    if spec is None:return None,dict(reason=reason),[],[]
    key=hashlib.sha256(json.dumps(['npc',spec],sort_keys=True).encode()).hexdigest()[:24];missing=[]
    if key not in models:
        reader=game.model_rules
        # Several MD3 and GLM droids ship only embedded shaders (no .skin).
        pieces=spec['skin'].split('|')
        skin_paths=[pieces[0]+p+'.skin' for p in pieces[1:]] if len(pieces)>1 else pieces
        absent=[p for p in skin_paths if p not in game.assets]
        skins={} if absent else (ja.skin_surfaces(game,spec['skin']) if reader is ja else jk2.read_skin(game.read(spec['skin']))['surfaces'])
        model,meta,_=reader.load_model(game,spec['model'],skins,surface_flags=spec['surfaceFlags'])
        if absent:meta['warnings'].append('NPC embedded-shader fallback; absent skin: '+', '.join(absent))
        if spec['model'].endswith('.md3') and skins:
            for surface in model['surfaces']:surface['shaders']=[skins.get(surface['name'],'')]
        if not any(s['indices'] for s in model['surfaces']):raise ValueError('NPC has no drawable surfaces')
        desc,missing=base.placed_model(game,spec['model'],'',meta['frame'],output,textures,model_textures,model=model)
        desc['npcSource']=dict(spec,**meta);desc['limitations']+=meta['warnings'];models[key]=desc
    meta=models[key]['npcSource'];pos=vector(e.get('origin'))
    placement=dict(entity=serial,classname=e['classname'].lower(),npc=True,npcType=spec['type'],models=[key],
        origin=pos,angles=base.angles(e),scale=[a*b for a,b in zip(base.scale(e),spec['scale'])],spin=False,bob=None)
    if getattr(game,'light_grid',None):placement['light']=game.light_grid.sample(pos)
    audit=dict(npc=True,npcType=spec['type'],definition=spec['definition'],frame=meta['frame'],pose=meta['pose'],
        spawnflags=int(number(e,'spawnflags')),reason='one NPC idle preview at authored position; scripts not executed')
    return placement,audit,missing,models[key]['limitations']


def place_entities(game,things,states,collision,output,textures,model_textures,*,rules=None):
    rules=rules or sys.modules[__name__]
    placed=[];models={};audit=[];missing=[];limits=[]
    entities=list(enumerate(things))
    if game.section=='sp':
        for serial,e in enumerate(things):
            if not rules.entity_filter(game,e):
                entities += [(serial,dict(child,_rack='1')) for child in rack_items(e)]
    for serial,e in entities:
        cls=e.get('classname','').lower();row=dict(entity=serial,classname=cls,status='omitted');reason=rules.entity_filter(game,e)
        try:
            flags=int(number(e,'spawnflags'));path=e.get('model','');item=game.items.get(cls);paths=[]
            if reason:pass
            elif cls.startswith('npc_'):
                placement,details,unresolved,notes=place_npc(game,e,serial,models,output,textures,model_textures)
                if placement:placed.append(placement);row['status']='drawn'
                row.update(details);missing+=unresolved;limits+=notes;audit.append(row);continue
            elif path.startswith('*'):
                state=states[int(path[1:])];reason=state['hidden'] or 'inline brush at spawn transform';row['status']='omitted' if state['hidden'] else 'drawn'
                if not state['hidden'] and e.get('model2'):paths=[e['model2']]
            elif cls in rules.BAKED_MODELS:reason='compile-time model baked into BSP; no second runtime copy'
            elif item:
                if game.section=='sp' and flags&32:reason='ITMSF_INVISIBLE'
                elif game.section=='mp' and (e.get('targetname') or e.get('team')):reason='targeted/team item hidden at spawn'
                elif game.section=='mp' and (item['kind']=='powerup' or item['kind']=='team' and not getattr(game,'team_items',False)):reason='team item excluded / powerup initial respawn delay'
                else:paths=item['models'];reason='' if paths else 'item has no world model'
            elif cls.startswith(('info_','target_','trigger_','light','fx_','waypoint','point_','ref_','path_')) or cls in ('worldspawn','misc_portal_surface','misc_portal_camera'):
                reason='world/control/effect entity; no standalone visual'
            else:
                path=rules.PROP_MODELS.get(cls,path or e.get('model2',''));paths=[path] if path else []
                reason='' if paths else 'no model key; class-specific effect/model not reconstructed'
            if paths:
                pos=vector(e.get('origin'));rot=base.angles(e);size=base.scale(e);frame=int(number(e,'frame',number(e,'startframe')));spin=False;bob=None;floor='authored position'
                if cls in ('misc_turret','misc_ns_turret') and flags&2:rot[2]+=180;pos[2]-=22
                if cls=='misc_trip_mine':
                    pos,rot=collision.mount_mine(pos,rot)
                    limits.append('trip mines mount to solid/shotclip brushes; patch and dynamic-model mounting and laser beams are not reconstructed')
                if item:
                    if game.section=='sp':pos[2]+=1
                    if not flags&1:
                        pos,floor=collision.drop(pos,item['mins'],item['maxs'],mask=1|0x10000)
                        if floor=='startsolid':row.update(reason='FinishSpawningItem frees startsolid item');audit.append(row);continue
                    if game.section=='sp' and flags&16:rot[0]-=75
                    if item['kind']=='holocron':bob=dict(base=12,rate=.005+serial*.00001);spin=True
                    if game.section=='mp':bob=dict(base=4,rate=.005+serial*.00001);spin=True
                if e.get('model','').startswith('*'):pos,rot,size=state['origin'],state['angles'],state['scale']
                for path in paths:
                    path=rtcw.q3.key_name(path).lstrip('/');skin=e.get('skin',e.get('modelskin',''))
                    key=hashlib.sha256(json.dumps([path,skin,frame]).encode()).hexdigest()[:24]
                    if key not in models:
                        model,meta,_=rules.load_placed(game,path,skin,frame)
                        desc,unresolved=base.placed_model(game,path,'' if getattr(rules,'BOUND_SKINS',False) else skin,frame,output,textures,model_textures,model=model)
                        desc['limitations']+=meta['warnings'];models[key]=desc;missing+=unresolved;limits+=desc['limitations']
                    placement=dict(entity=serial,models=[key],origin=pos,angles=rot,scale=size,spin=spin,bob=bob)
                    if getattr(game,'light_grid',None):placement['light']=game.light_grid.sample(pos)
                    placed.append(placement)
                row['status']='drawn';reason=('rack pickup; deterministic midpoint of random placement' if e.get('_rack') else 'pickup at spawn; '+floor) if item else 'model at authored spawn pose'
            row['reason']=reason
        except (ValueError,OSError,KeyError,struct.error,zipfile.BadZipFile) as exc:
            row.update(status='failed',reason=str(exc));missing.append(f'entity {serial} {cls}: {exc}')
        audit.append(row)
    return placed,models,audit,missing,limits


def efx_curve(group):
    """FxPrimitives.cpp Update*: linear, then exactly one of nonlinear/wave/clamp (a mask compare: two set is none), random."""
    flags=set(group['flags']);kinds=[k for k in ('nonlinear','wave','clamp') if k in flags]
    return dict(linear='linear' in flags,kind=kinds[0] if len(kinds)==1 else None,parm=group['parm'],random='random' in flags)


def efx_template(p):
    """A Particle primitive as a shared particles.js template (seconds; Raven's rotationDelta is 1/100 degree a ms)."""
    spawn,flags=set(p['spawnFlags']),set(p['flags'])
    return dict(count=p['count'],life=[v/1000 for v in p['life']],delay=[v/1000 for v in p['delay']],even='evendistribution' in spawn,
        origin=p['origin'],velocity=p['velocity'],acceleration=p['acceleration'],gravity=p['gravity'],
        rotation=p['rotation'],spin=[v*10 for v in p['rotationDelta']],cullRange=p['cullRange'],
        size=dict(start=p['size']['start'],end=p['size']['end'],curve=efx_curve(p['size'])),
        alpha=dict(start=p['alpha']['start'],end=p['alpha']['end'],curve=efx_curve(p['alpha'])),
        rgb=dict(start=p['rgb']['start'],end=p['rgb']['end'],curve=efx_curve(p['rgb'])),
        rgbTogether='rgbcomponentinterpolation' in spawn,alphaFadesRgb='usealpha' not in flags,
        worldOrigin='cheaporgcalc' in spawn,worldVelocity='absolutevel' in spawn,worldAcceleration='absoluteaccel' in spawn)


def runner_axes(e,things,section):
    """fx_runner's effect axes: toward its target, else SP's `angle` yaw (G_SpawnAngleHack) or MP's angles, else up
    (-90 0 0); right and up by MakeNormalVectors, as CFxScheduler::PlayEffect builds them from the forward."""
    target=next((t for t in things if e.get('target') and t.get('targetname')==e['target']),None)
    if target:
        d=[b-a for a,b in zip(vector(e.get('origin')),vector(target.get('origin')))];length=math.hypot(*d) or 1;forward=[v/length for v in d]
    else:
        if section=='sp':a=[0,number(e,'angle'),0] if 'angle' in e else [-90,0,0]
        else:
            a=base.angles(e)
            if a==[0,0,0]:a=[-90,0,0]
        pitch,yaw=math.radians(a[0]),math.radians(a[1])
        forward=[math.cos(pitch)*math.cos(yaw),math.cos(pitch)*math.sin(yaw),-math.sin(pitch)]
    right=[forward[2],-forward[0],forward[1]];d=sum(r*f for r,f in zip(right,forward))
    right=[r-d*f for r,f in zip(right,forward)];length=math.hypot(*right) or 1
    return forward,[v/length for v in right]


def efx_stage(game,name,textures):
    """The first stage of an efx shader, or None. A name without a script is RegisterShader's implicit 2D image:
    alpha blended, vertex colour (R_FindShader); stages without a map (oneShotAnimMap) are not drawn."""
    if name in game.shaders:
        stages=describe(game,name,textures)[0].get('stages') or []
        return (stages[0] if stages and stages[0].get('map') else None),[]
    image=textures.resolve(game,name)
    if not image:return None,[name]
    return dict(map=image,blend=['gl_src_alpha','gl_one_minus_src_alpha'],rgbGen=['vertex'],alphaGen=['vertex']),[]


def storm(things,game_id):
    """Raven wind and lightning, as g_fx.cpp hands them to tr_WorldEffects (R_WorldEffectCommand), or None. `wind` lists
    the zones the renderer sums each frame: fx_wind's NORMAL (1) random light wind, CONSTANT (2) `speed` (500) along its
    angles, GUSTING (4) gusts; a JA fx_rain with no type flag adds the normal wind, and JK2's fx_rain and fx_snow always do.
    `lightning` is JA fx_rain's LIGHTNING (64, or HEAVY 4): fx_rain_think's fog flash and env/huge_lightning bolt.
    ponytail: retail JK2's renderer is not public, so JA's zones stand in for its; SWIRLING (8) has no renderer
    command; fx_wind_zone (local wind) is on no stock map; thunder and outside shake are not kept."""
    wind,lightning=[],None
    for e in things:
        e={k.lower():v for k,v in e.items()};cls=e.get('classname','').lower();flags=int(number(e,'spawnflags'))
        if cls=='fx_wind':
            if flags&1:wind.append(dict(mode='normal'))
            if flags&2:  # AngleVectors of its angles (a lone `angle` is the yaw).
                pitch,yaw=(math.radians(float(v)) for v in (e.get('angles') or f"0 {e.get('angle',0)} 0").split()[:2])
                speed=number(e,'speed',500)
                wind.append(dict(mode='constant',velocity=[round(v*speed,3)+0. for v in (math.cos(pitch)*math.cos(yaw),math.cos(pitch)*math.sin(yaw),-math.sin(pitch))]))
            if flags&4:wind.append(dict(mode='gusting'))
        elif cls in ('fx_rain','fx_snow') and game_id=='jk2':wind.append(dict(mode='normal'))
        elif cls=='fx_rain':
            if not flags&15:wind.append(dict(mode='normal'))
            if flags&68:
                color=[float(v) for v in (e.get('flashcolor') or '200 200 200').split()[:3]]
                lightning=dict(color=color,delay=int(number(e,'flashdelay',12000)),flicker=int(number(e,'chanceflicker',2)),effect=int(number(e,'chanceeffect',4)))
    return dict(wind=wind,lightning=lightning) if wind or lightning else None


def particles(game,things,audit,textures):
    """Running fx_runners (neither STARTOFF nor ONESHOT) re-fire their .efx every delay + random/2 ms
    (fx_runner_think; ponytail: the mean of its random wait). Each Particle primitive and shader is one emitter of
    the shared particle system. Returns (map.particles or None, unresolved, limits)."""
    templates,keys,batches,emitters,missing,skipped=[],{},{},[],[],Counter()
    rows={r['entity']:r for r in audit if r['classname']=='fx_runner' and r['status']=='omitted' and r['reason'].startswith('world/control')}
    for serial,row in rows.items():
        e={k.lower():v for k,v in things[serial].items()}
        if int(number(e,'spawnflags'))&3:row['reason']='fx_runner STARTOFF/ONESHOT: plays only when used';continue
        path=raven_efx.effect_path(e.get('fxfile',''))
        if path not in game.assets:row['reason']=f'fx_runner effect {path} not in the game files';missing.append(path);continue
        if path not in game.effects:game.effects[path]=raven_efx.parse(game.read(path).decode('latin1'))
        effect=game.effects[path];skipped.update(effect['skipped'])
        forward,right=runner_axes(e,things,game.section);origin=vector(e.get('origin'))
        period=(number(e,'delay',200)+number(e,'random')/2)/1000;drawn=0
        for i,p in enumerate(effect['primitives']):
            if p['type']!='particle':skipped['orientedparticle']+=1;continue
            shaders=[]
            for name in p['shaders']:
                name=re.sub(r'\.(tga|jpg|png)$','',name.lower())
                if name not in batches:
                    batches[name],unresolved=efx_stage(game,name,textures);missing+=unresolved
                if batches[name]:shaders.append(name)
            if not shaders:skipped['unsupported shader']+=1;continue
            if (path,i) not in keys:keys[(path,i)]=len(templates);templates.append(efx_template(p))
            for k,name in enumerate(shaders):
                emitters.append(dict(entity=serial,origin=origin,forward=forward,right=right,template=keys[(path,i)],period=period,
                                     batch=name,**({'share':[k,len(shaders)]} if len(shaders)>1 else {})))
            drawn+=1
        row.update(status='drawn' if drawn else 'omitted',reason=f'fx_runner: {drawn} particle primitive(s) of {path}' if drawn else f'fx_runner: no drawable primitive in {path}')
    limits=[]
    if rows:limits.append('fx_runner effects: Particle primitives only'+(' (not drawn: '+', '.join(f'{k} {n}' for k,n in sorted(skipped.items()))+')' if skipped else '')+
                          '; triggered runners, impacts and physics, orgOnSphere/orgOnCylinder spawn shapes and oneShotAnimMap explosions not reconstructed')
    if not emitters:return None,missing,limits
    return dict(templates=templates,batches={k:v for k,v in batches.items() if v},emitters=emitters),missing,limits


def sweep(output, texture_directory=None, *, texture_stores=()):
    """Audit references; optional stores retire only their own unused new files."""
    output=Path(output);missing=[];defaults=[];scenes=0;used=set()
    for path in sorted((output/'maps').glob('*/scene.json')):
        scene=json.loads(path.read_text());scenes+=1
        def check(name):
            target=output/name
            if texture_directory is not None and name.startswith('textures/'):
                shared=Path(texture_directory)/name.removeprefix('textures/')
                if shared.is_file():target=shared
            if target is None or not target.is_file():missing.append(dict(scene=path.parent.name,file=name))
        check(scene['bsp'])
        for item in scene.get('placed',[]):
            for key in item['models']:
                if key not in scene['itemModels']:missing.append(dict(scene=path.parent.name,model=key))
        shaders=[s for s in scene['shaders'] if s]+[dict(stages=[stage]) for stage in scene.get('particles',{}).get('batches',{}).values()]
        for model in scene['itemModels'].values():
            check(model['file']);shaders+=model['shaders']
            target=output/model['file']
            if target.is_file():
                description=json.loads(target.read_text())
                if isinstance(description,dict) and 'geometry_buffers' in description:
                    try:read_geometry_buffers(target,description['geometry_buffers'])
                    except (ValueError,OSError) as exc:missing.append(dict(scene=path.parent.name,file=model['file'],reason=str(exc)))
        for shader in shaders:
            if shader.get('default'):defaults.append(dict(scene=path.parent.name,shader=shader['name']))
            names=[shader.get('map','')]+shader.get('sky',{}).get('box',[])
            for stage in shader.get('stages',[]):names += [stage.get('map','')]+stage.get('frames',[])
            for name in names:
                if name and not name.startswith('$'):check('textures/'+name);used.add(name)
    for store in texture_stores:store.finish(used)
    return dict(scenes=scenes,missing=missing,defaults=defaults)


def map_paths(game):
    return sorted(n for n in game.assets if re.fullmatch(r'maps/[^/]+\.bsp',n))


def configure_map(game,name):
    game.default_gametype='single player' if game.section=='sp' else 'FFA'


def scene_metadata(game,raw):
    """SP maps that set worldspawn distanceCull end the view at SetFarClip's ceiling, distanceCull * 1.732, as in
    JA (OpenJK's JK2 mode shares it); JK2 MP (JK2MV) has no distanceCull, and the SP default is left unclipped."""
    things=qmap.entities(read_lumps(raw)[0].decode('latin1'))
    value=things[0].get('distancecull') if things and game.section=='sp' else None
    try:distance=float(value) if value is not None else 0
    except ValueError:distance=0
    return dict(distanceCull=distance,farClip=max(2048,distance*1.732),distanceCullSource='worldspawn') if math.isfinite(distance) and distance>0 else {}


def map_viewpoints(game,things,collision):
    return viewpoints(things,collision,game.section)


def import_maps(install,output,replace=False,*,rules=None):
    config=rules or sys.modules[__name__]
    output=Path(output)
    for folder in ('maps','models','bsp','textures'):(output/folder).mkdir(parents=True,exist_ok=True)
    report=dict(imported=[],skipped=[],failed={},maps=[],sections={});index=[];texture_stores=[]
    for section,label in SECTIONS.items():
        game=config.Game(install,section)
        texture_dir=config.texture_directory(output) if hasattr(config,'texture_directory') else output/'textures'
        textures=getattr(config,'Textures',base.Textures)(texture_dir,game)
        mt=getattr(config,'ModelTextures',ModelTextures)(game,texture_dir)
        for writer in (textures,mt.raw):
            if hasattr(writer,'store'):texture_stores.append(writer.store)
        rules=SimpleNamespace(read_bsp=lambda raw,notes:read_bsp(raw,notes,section=section,light_scale=game.lighting['sourceLightScale']),
            brush_state=config.brush_state,filtered=lambda e,s:config.entity_filter(game,e),describe=describe,
            viewpoints=lambda things,collision,s:config.map_viewpoints(game,things,collision),place_entities=config.place_entities,Collision=Collision,
            MOVERS='ravensp' if section=='sp' else 'q3',ID_FLARES=False,FLARE_CARDS=True)
        classes=Counter()
        for path in config.map_paths(game):
            name=path[5:-4];chosen,why=config.section_for(name)
            if chosen!=section:continue
            config.configure_map(game,name)
            ident=section+'_'+qmap.map_id(Path(name).name);source=game.assets[path]['source'];dest=output/'maps'/ident/'scene.json'
            row=dict(map=name,section=section,sectionReason=why,source=source)
            try:
                if dest.is_file() and not replace:scene=json.loads(dest.read_text());status='skipped'
                else:
                    raw=game.read(path)
                    metadata=config.scene_metadata(game,raw) if hasattr(config,'scene_metadata') else {}
                    game.light_grid=config.LightGrid(read_lumps(raw),game.lighting,section)
                    scene=base.import_map(game,name,ident,raw,source,output,textures,mt,rules=rules)
                    things=qmap.entities(read_lumps(raw)[0].decode('latin1'))
                    fx,unresolved,notes=particles(game,things,scene['entityResults'],textures)
                    if fx:scene['particles']=fx
                    weather=storm(things,game.game_id)
                    if weather:scene['storm']=weather  # Drawn by the T1 Maps Weather list.
                    scene['unresolved']+=unresolved;scene['limits']+=notes
                    if game.light_grid.reason:scene['limits'].append(game.light_grid.reason)
                    scene.update(game=game.game_id,sourceFormat='RBSP 1',defaultGametype=game.default_gametype)
                    scene.update(metadata)
                    scene['limits']=[x for x in scene['limits'] if x not in base.NOT_DRAWN and not x.startswith('first eligible Axis')]+config.NOT_DRAWN
                    scene['limits']+=['first eligible player start; runtime selection and level-transition targets may differ','no hardware gamma assumed on this PC; working hardware gamma uses the display ramp instead']
                    rtcw.q3.json_write(dest,scene);status='imported'
                report[status].append(ident);row.update(status=status,entityResults=scene['entityResults'],unresolved=scene['unresolved'],bspNotes=scene['bspNotes'])
                for key in ('distanceCull','farClip','distanceCullSource'):
                    if key in scene:row[key]=scene[key]
                classes.update((r['classname'],r['status'],r['reason']) for r in scene['entityResults'])
                index.append(dict(id=ident,name=name,longname=scene['longname'],group=label,source=source,unresolved=len(scene['unresolved'])))
            except (ValueError,OSError,KeyError,struct.error,zipfile.BadZipFile) as exc:
                report['failed'][ident]=str(exc);row.update(status='failed',reason=str(exc))
            report['maps'].append(row)
        report['sections'][section]=dict(search_order=game.search_order,lighting=game.lighting,issues=game.issues,
            entityClasses=[dict(classname=c,status=s,reason=r,count=n) for (c,s,r),n in sorted(classes.items())])
    report['sweep']=sweep(output,config.texture_directory(output) if hasattr(config,'texture_directory') else None,
                          texture_stores=texture_stores)
    rtcw.q3.json_write(output/'index.json',index);rtcw.q3.json_write(output/'import-report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install',required=True)
    parser.add_argument('--output',type=Path,default=qmap.LOCAL_DATA/'jk2-maps');parser.add_argument('--replace',action='store_true')
    args=parser.parse_args();print(json.dumps(import_maps(args.install,args.output,args.replace),indent=2))

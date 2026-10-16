"""Jedi Academy RBSP maps using the shared Raven preview pipeline.

OpenJK SP and MP are rules-only references; no engine source is included.
"""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import re
import struct
import sys

import numpy as np
from PIL import Image

try:
    from tools import import_jk2_map as jk, import_ja as ja
    from tools.ja_map_items import MP_ITEMS
    from tools.texture_storage import SourceTextureStore, game_texture_dir
    from tools.model_data import write_surface_data
except ImportError:
    import import_jk2_map as jk
    import import_ja as ja
    from ja_map_items import MP_ITEMS
    from texture_storage import SourceTextureStore, game_texture_dir
    from model_data import write_surface_data

base,number,vector=jk.base,jk.number,jk.vector
BAKED_MODELS=('misc_model',)
PROP_MODELS={}  # Authored model keys take priority; defaults are filled below.
BOUND_SKINS=True
NOT_DRAWN=jk.NOT_DRAWN+[
    'NPC_Vehicle and vehicle definitions excluded; ordinary vehicle model props still draw',
    'physics after spawn, scripted objective changes and vehicle simulation',
    'native MP mode by map: CTF, duel, FFA or Siege; first eligible Red/team 1 start',
]


def lighting_profile(install,section,game=None,*,device_supports_gamma=False):
    game=game or ja.Game(install)
    root=Path(install)
    if (root/'GameData/base').is_dir():root=root/'GameData'
    result=base.lighting_profile(root,section,game,directory='base',
        configs=('default.cfg','jaconfig.cfg' if section=='sp' else 'jampconfig.cfg','autoexec.cfg'),
        defaults=dict(r_overbrightbits=0,r_mapoverbrightbits=0,cg_fov=80,g_spskill=0,
                      r_ambientscale=.5 if section=='sp' else .6,r_directedscale=1),
        device_supports_gamma=device_supports_gamma)
    bits=min(1,result['overbrightBits']);result['overbrightBits']=bits
    result['identityLight']=2**-bits;result['displayScale']=2**bits if result['hardwareGamma'] else 1
    result['sourceLightScale']=2**max(0,min(4,int(result['cvars']['r_mapoverbrightbits']))-bits)
    result['lightScale']=1
    result['configuredTextureMode']=result['textureMode'];result['textureMode']='GL_LINEAR_MIPMAP_NEAREST'
    result['textureModeSource']='Retail JA SP v1.0.1.0 gfxinfo, run3-t1_rail-logs/qconsole.log: GL_LINEAR_MIPMAP_NEAREST; saved cfg mode retained separately.'
    result['source']='JA classic renderer: overbright 0, gamma 1 by default; retail JA SP v1.0.1.0 confirmed software gamma, picmip 1 and nearest mip filtering on this PC. MP uses the same filtering profile pending its own measurement.'
    return result


class Game(jk.Game,ja.Game):
    game_id,title,sections='ja','Jedi Academy',('ja',)
    profile=staticmethod(lighting_profile)
    model_rules=ja
    mp_items=MP_ITEMS
    write_surface_data=staticmethod(write_surface_data)


def texture_directory(output):
    return game_texture_dir(Path(output).parent,'ja')


class Textures(base.Textures):
    def __init__(self,output,game,*,uploaded=True):
        super().__init__(output,game)
        self.store=SourceTextureStore(output,game.assets);self.uploaded=uploaded;self.resolved={}

    def process(self,filename,mipmap=True,picmip=True):
        with Image.open(self.output/filename) as source:image=source.convert('RGBA')
        result=base.upload_image(image,self.game.lighting,mipmap,picmip)
        if result.size==image.size and result.tobytes()==image.tobytes():return filename
        encoded=io.BytesIO();result.save(encoded,format='PNG')
        name=filename.rsplit('__',1)[0]+'__'+hashlib.sha256(encoded.getvalue()).hexdigest()[:12]+'.png'
        return self.store.write(name,encoded.getvalue())

    def resolve(self,game,path):
        key=ja.q3.key_name(path).lstrip('/');sky=key in self.sky_paths
        mipmap,picmip=self.mipmap or sky,self.picmip or sky
        cache=(key,mipmap,picmip)
        if cache in self.resolved:return self.resolved[cache]
        for candidate in dict.fromkeys(ja.base.image_candidates(key)):
            if candidate not in game.assets:continue
            raw=game.read(candidate)
            try:
                with Image.open(io.BytesIO(raw)) as source:image=source.convert('RGBA')
                processed=base.upload_image(image,game.lighting,mipmap,picmip) if self.uploaded else image
                if processed.size==image.size and processed.tobytes()==image.tobytes():
                    filename=self.store.convert(None,raw,candidate)
                else:filename=self.store.derived(raw,candidate,processed)
            except (ValueError,OSError):continue
            self.resolved[cache]=filename;return filename
        self.resolved[cache]=None;return None


class ModelTextures(jk.ModelTextures):
    def __init__(self,game,output):
        super().__init__(game,output)
        self.raw=Textures(output,game,uploaded=False)


class LightGrid(jk.LightGrid):
    def __init__(self,lumps,lighting,section='sp'):
        super().__init__(lumps,lighting,section)
        self.data=self.data.copy()
        for offset in range(0,24,3):
            colors=self.data[:,offset:offset+3].astype(float)*lighting['sourceLightScale']
            self.data[:,offset:offset+3]=colors*(255/np.maximum(255,colors.max(axis=1,keepdims=True)))


def map_paths(game):
    return sorted(n for n in game.assets if re.fullmatch(r'maps/(?:[^/]+/)*[^/]+\.bsp',n))


def section_for(name):
    return ('mp','maps/mp namespace, including Siege') if name.startswith('mp/') else ('sp','SP story map outside maps/mp')


def configure_map(game,name):
    leaf=name.rsplit('/',1)[-1]
    game.default_gametype='single player' if game.section=='sp' else next(
        (mode for prefix,mode in (('siege_','siege'),('ctf','ctf'),('duel','duel')) if leaf.startswith(prefix)),'ffa')
    game.team_items=game.default_gametype=='ctf'


def view_distance(world,section):
    """R_LoadEntities defaults and SetFarClip's projection-distance ceiling."""
    value=world.get('distancecull',world.get('distanceCull'))
    distance=12000 if section=='sp' else 6000
    if value is not None:
        try:distance=float(value)
        except (ValueError,TypeError):raise ValueError('Invalid worldspawn distanceCull') from None
    if not math.isfinite(distance) or distance<=0:raise ValueError('Invalid worldspawn distanceCull')
    far=max(2048,distance*1.732)
    if not math.isfinite(far):raise ValueError('Invalid worldspawn distanceCull')
    return dict(distanceCull=distance,farClip=far,
                distanceCullSource='worldspawn' if value is not None else section+' renderer default')


def scene_metadata(game,raw):
    lumps=jk.read_lumps(raw);things=jk.qmap.entities(lumps[0].decode('latin1'))
    result=view_distance(things[0] if things else {},game.section)
    if game.section=='sp':result['bspCull']=bsp_cull(lumps,things)
    return result


def bsp_cull(lumps,things):
    """Compact original tree/leaf membership; draw-surface ordinals are preserved."""
    tree=jk.qmap.read_visibility(lumps,surface_stride=148)
    models=list(struct.iter_unpack('<6f4i',lumps[7]))
    inline=[]
    for index,model in enumerate(models[1:],1):
        ent=next((e for e in things if e.get('model')=='*'+str(index)),{})
        state=brush_state(ent,model,things)
        corners=np.array([base.transformed([model[j+3*((i>>j)&1)] for j in range(3)],state) for i in range(8)])
        inline.append(dict(first=model[6],count=model[7],corners=corners.tolist()))
    tree['inline']=inline
    return tree


def entity_filter(game,e):
    if e.get('_prepare_error'):return 'invalid prop spawn fields: '+e['_prepare_error']
    cls=e.get('classname','').lower();flags=int(number(e,'spawnflags'))
    if cls=='npc_vehicle':return 'vehicle spawner excluded from NPCs'
    if game.section=='sp':
        why=jk.filtered(e,'sp',game.skill)
        if why:return why
        if cls=='misc_model_breakable' and flags&4096:return 'START_OFF (4096)'
        if cls in ('misc_model_beacon','misc_model_bomb_planted') and e.get('targetname'):return 'targeted prop hidden until used'
        if cls=='misc_trip_mine' and e.get('targetname') and flags&4:return 'targeted trip mine START_OFF'
    else:
        # Client-side static models use the same mode filter in cg_spawn.c.
        mode=game.default_gametype
        if number(e,'notteam' if mode in ('ctf','siege') else 'notfree'):return 'excluded from '+mode+' by notteam/notfree'
        if e.get('gametype') and mode not in e['gametype'].lower():return 'gametype excludes '+mode
        item=game.items.get(cls)
        if item:
            if item['kind']=='team' and mode!='ctf':return 'team pickup excluded outside CTF'
            if mode=='siege' and item['kind']=='powerup':return 'powerup excluded in Siege'
            if mode=='duel' and (item['kind'] in ('armor','health') or cls in ('item_medpac','item_medpac_big')):return 'health/armor/medpac excluded in duel'
        if cls=='misc_holocron':return 'holocron excluded outside Holocron mode'
        if cls=='misc_siege_item':
            if mode!='siege':return 'Siege objective excluded outside Siege'
            if e.get('targetname') and not flags&8:return 'targeted Siege objective hidden until used'
    return ''


brush_state=jk.brush_state


def map_viewpoints(game,things,collision):
    mode=game.default_gametype
    kinds=('info_player_start','info_player_deathmatch') if game.section=='sp' else {
        'ctf':('team_ctf_redplayer','team_ctf_redspawn'),
        'siege':('info_player_siegeteam1',),
        'duel':('info_player_duel','info_player_deathmatch','info_player_start'),
        'ffa':('info_player_deathmatch','info_player_start','info_player_siegeteam1','info_player_siegeteam2'),
    }[mode]
    starts=[]
    for kind in kinds:
        candidates=[e for e in things if e.get('classname','').lower()==kind and not entity_filter(game,e)
                    and not number(e,'nohumans') and not (mode=='siege' and number(e,'startoff'))]
        if candidates:starts=candidates;break
    if game.section=='sp':starts.sort(key=lambda e:bool(e.get('targetname')))
    elif mode=='ffa':starts.sort(key=lambda e:not int(number(e,'spawnflags'))&1)
    result=[];half=16 if game.section=='sp' else 15
    for e in starts:
        pos=vector(e.get('origin'));pos[2]+=9
        pos,floor=collision.drop(pos,(-half,-half,-24),(half,half,40));pos[2]+=36
        a=base.angles(e);result.append(dict(origin=pos,pitch=a[0],yaw=a[1],floor=floor,classname=e['classname']))
    return result


def load_placed(game,path,skin,frame):
    sk=ja.skin_surfaces(game,skin) if skin and not skin.isdigit() else {}
    model,meta,raw=ja.load_model(game,path,sk,frame=frame)
    if not hasattr(game,'model_midpoints'):game.model_midpoints={}
    midpoint=[0,0,0]
    if raw[:4]==b'IDP3':
        offset=struct.unpack_from('<i',raw,92)[0]
        bounds=struct.unpack_from('<6f',raw,offset)
        if not np.isfinite(bounds).all():raise ValueError('Non-finite MD3 pickup bounds')
        midpoint=[(bounds[i]+bounds[i+3])/2 for i in range(3)]
    game.model_midpoints[path]=midpoint
    # Bind MD3 skins too, so the RTCW serializer never reinterprets JA skin syntax.
    index=int(skin) if str(skin).isdigit() else 0
    for surface in model['surfaces']:
        shaders=surface['shaders']
        surface['shaders']=[sk.get(surface['name'],shaders[index%len(shaders)] if shaders else '')]
    return model,meta,raw


def prepare_entity(e,section):
    e=dict(e);cls=e.get('classname','').lower();flags=int(number(e,'spawnflags'))
    if not e.get('model') and cls in jk.PROP_MODELS:e['model']=jk.PROP_MODELS[cls]
    # The game sets these models itself, whatever the map's model key says (g_misc_model.cpp).
    defaults={'misc_model_beacon':'models/map_objects/wedge/beacon.md3',
              'misc_model_bomb_planted':'models/map_objects/factory/bomb_new_deact.md3',
              'misc_model_gun_rack':'models/map_objects/kejim/weaponsrack.md3',
              'misc_model_ammo_rack':'models/map_objects/kejim/weaponsrung.md3'}
    if cls in defaults:e['model']=defaults[cls]
    if 'modelscale' in e:
        # G_SpawnFloat uses atof; stock maps occasionally put three numbers here.
        match=re.match(r'^[ \t]*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)',e['modelscale'])
        e['modelscale']=match[1] if match else '0'
        if number(e,'modelscale')==0:e.pop('modelscale')
    if cls=='misc_model_static' and section=='sp' and 'modelscale' in e:
        e.pop('modelscale_vec',None)
    if 'modelscale_vec' in e and vector(e['modelscale_vec'])==[0,0,0]:e.pop('modelscale_vec')
    if cls=='misc_model_breakable' and section=='sp':
        size=base.scale(e);pos=vector(e.get('origin'));mins=vector(e.get('mins'),(-16,-16,-16))
        pos[2]+=mins[2]*(1-size[2]);e['origin']=' '.join(map(str,pos))
    if cls=='misc_turretg2':
        e['model']='models/map_objects/wedge/laser_cannon_model.glm' if flags&8 else 'models/map_objects/imp_mine/turret_canon.glm'
        if flags&8:e['frame']='4'
        if number(e,'customscale'):e['modelscale']=str(min(1023,number(e,'customscale'))/100)
        if flags&2:
            a=base.angles(e);a[2]+=180;e['angles']=' '.join(map(str,a))
            pos=vector(e.get('origin'));pos[2]-=22;e['origin']=' '.join(map(str,pos))
    return e


def place_entities(game,things,states,collision,output,textures,model_textures):
    prepared=[]
    for e in things:
        try:prepared.append(prepare_entity(e,game.section))
        except ValueError as exc:prepared.append(dict(e,_prepare_error=str(exc)))
    result=jk.place_entities(game,prepared,states,collision,output,textures,model_textures,rules=sys.modules[__name__])
    for placed in result[0]:
        e=prepared[placed['entity']]
        if e.get('classname','').lower()=='misc_model_breakable' and number(e,'gravity'):
            size=base.scale(e);mins=vector(e.get('mins'),(-16,-16,-16));maxs=vector(e.get('maxs'),(16,16,16))
            start=list(placed['origin']);start[2]+=1
            pos,reason=collision.drop(start,[v*s for v,s in zip(mins,size)],[v*s for v,s in zip(maxs,size)])
            if reason.endswith('floor'):
                placed['origin']=pos
                if getattr(game,'light_grid',None):placed['light']=game.light_grid.sample(pos)
                for row in result[2]:
                    if row['entity']==placed['entity']:row['reason']='model at spawn; gravity hull drop'
    if game.section=='mp':
        for placed in result[0]:
            cls=prepared[placed['entity']].get('classname','').lower();item=game.items.get(cls)
            if not item:continue
            kind=item['kind'];placed['spin']=kind in ('weapon','powerup')
            if placed['spin']:
                placed['bob'].update(base=20 if kind=='powerup' else 28,amplitude=4)
                if kind=='weapon':
                    placed['scale']=[s*1.5 for s in placed['scale']]
                    placed['pivot']=getattr(game,'model_midpoints',{}).get(item['models'][0],[0,0,0])
            else:
                placed['bob']=None
                placed['origin'][2]+={'item_seeker':5,'item_shield':2,'item_binoculars':2,'item_shield_lrg_instant':7}.get(cls,2 if kind=='health' else 0)
    return result


def import_maps(install,output,replace=False):
    return jk.import_maps(install,output,replace,rules=sys.modules[__name__])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',required=True)
    parser.add_argument('--output',type=Path,default=jk.qmap.LOCAL_DATA/'ja-maps')
    parser.add_argument('--replace',action='store_true')
    args=parser.parse_args();print(json.dumps(import_maps(args.install,args.output,args.replace),indent=2))

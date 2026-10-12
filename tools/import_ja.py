"""Local Jedi Academy models and parked vehicles, using JA Ghoul2 rules.

OpenJK is a rules-only reference. No engine source or retail assets are bundled.
python tools/import_ja.py --install C:/Games/JediAcademy/GameData
"""
import argparse
import json
import math
from pathlib import Path
import re
import sys

import numpy as np

try:
    from tools import import_jk2 as jk
except ImportError:
    import import_jk2 as jk
base, q3 = jk.base, jk.q3
Textures, CatalogTextures, shader_maps = jk.Textures, jk.CatalogTextures, jk.shader_maps
read_gla, glm_header = jk.read_gla, jk.glm_header
CUSTOM_SPECIES = {'jedi_hf','jedi_hm','jedi_kdm','jedi_rm','jedi_tf','jedi_zf'}


class Game(jk.Game):
    game_id, title, sections = 'ja', 'Jedi Academy', ('ja',)

    def read(self, name):
        if name.endswith(('.veh','.npc')):
            name=base.checked_name(name)
            if name not in self.assets:raise ValueError('Missing asset: '+name)
            return self.assets[name]['read'](max_bytes=1024*1024)
        return super().read(name)


def text(raw):
    if len(raw)>1024*1024:raise ValueError('Definition exceeds limit')
    return re.sub(r'/\*.*?\*/|//[^\r\n]*','',q3.read_text(raw),flags=re.S)


def definitions(raw):
    """Bounded, flat named blocks used by both .veh and .npc files."""
    tokens=re.findall(r'"([^"\r\n]*)"|([{}]|[^\s{}"]+)',text(raw))
    tokens=[a or b for a,b in tokens];result=[];i=0
    while i<len(tokens):
        name=tokens[i];i+=1
        if i>=len(tokens) or tokens[i]!='{':raise ValueError('Expected named definition block')
        i+=1;values={}
        while i<len(tokens) and tokens[i]!='}':
            if i+1>=len(tokens) or tokens[i] in ('{','}') or tokens[i+1] in ('{','}'):
                raise ValueError('Invalid definition key/value')
            values[tokens[i].lower()]=tokens[i+1];i+=2
        if i>=len(tokens):raise ValueError('Unclosed definition block')
        i+=1;result.append(dict(name=name,values=values))
        if len(result)>4096:raise ValueError('Too many definitions')
    return result


def read_skin(raw):
    surfaces={}
    for line in text(raw).splitlines():
        if not line.strip():continue
        if ',' not in line:raise ValueError('Skin contains an invalid surface declaration')
        name,shader=(s.strip().strip('"').lower() for s in line.split(',',1))
        if not name or name.startswith('tag_'):continue
        if name.endswith('_off'):
            if shader=='*off':continue
            name=name[:-4]
        if not shader:raise ValueError('Skin surface has no shader')
        # The renderer scans the registered list from the beginning.
        surfaces.setdefault(name,shader)
    return dict(surfaces=surfaces,models={},scale=1,empty=not surfaces)


def skin_surfaces(game, name):
    if not name:return {}
    paths=[name]
    if '|' in name:
        pieces=name.split('|')
        if len(pieces)!=4:raise ValueError('Multipart skin requires base, head, torso and lower')
        paths=[pieces[0]+piece+'.skin' for piece in pieces[1:]]
    surfaces={}
    for path in dict.fromkeys(paths):
        for key,value in read_skin(game.read(base.checked_name(path)))['surfaces'].items():
            surfaces.setdefault(key,value)
    return surfaces


def old_humanoid_remap():
    """JA's compatibility collapse of old toes, metacarpals and finger joints."""
    result=list(range(7))+[6]+list(range(7,11))+[10]+list(range(11,30))+[29]
    for a,b in ((34,35),(30,31),(32,33),(32,33),(34,35)):
        result.extend((a,b,b))
    result.extend(range(36,43));result.append(42)
    for a,b in ((43,44),(43,44),(45,46),(45,46),(47,48)):
        result.extend((a,b,b))
    return result+[52]


def surface_bolt(vertices):
    points=np.asarray(vertices).reshape(3,3)
    long=points[1]-points[0];short=points[0]-points[2]
    def unit(v):
        length=np.linalg.norm(v)
        if length<1e-10:raise ValueError('Degenerate Ghoul2 surface bolt')
        return v/length
    y=unit(short);x=unit(long);x=unit(x-np.dot(x,y)*y);z=unit(np.cross(long,short))
    return tuple(points[2])+tuple(np.array([y,x,-z]).flat)


def read_glm(raw, skeleton, skin=None, surface_flags=None):
    h=glm_header(raw);remap=None
    if h['bones']==72 and '_humanoid' in h['animation'].lower() and len(skeleton['matrices'])==53:
        remap=old_humanoid_remap()
    return jk.read_glm(raw,skeleton,skin,surface_flags,bone_remap=remap,
                       surface_name=lambda n:n.removesuffix('_off'),surface_bolt=surface_bolt)


def animation_rows(game, animation):
    path=str(Path(animation).parent/'animation.cfg').replace('\\','/')
    if path not in game.assets:return path,{}
    rows={}
    for match in re.finditer(r'(?im)^\s*(\w+)\s+(\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?[\d.]+)',text(game.read(path))):
        name,first,count,loop,fps=match.groups()
        if abs(int(count)) and math.isfinite(float(fps)):
            rows.setdefault(name.upper(),(int(first),abs(int(count)),float(fps)))
    return path,rows


def idle_pose(game, animation, vehicle_type=''):
    path,rows=animation_rows(game,animation)
    choices={'vh_fighter':('BOTH_GEARS_OPEN','BOTH_VS_IDLE','BOTH_STAND1'),
             'vh_animal':('BOTH_VT_IDLE','BOTH_STAND1'),
             'vh_speeder':()}.get(vehicle_type.lower(),('BOTH_STAND1',))
    for name in choices:
        if name in rows:
            first,count,fps=rows[name]
            frame=first+count-1 if (name=='BOTH_GEARS_OPEN') == (fps>=0) else first
            return frame,path+': '+('held final frame of ' if name=='BOTH_GEARS_OPEN' else 'first frame of ')+name
    return 0,'Frame 0: '+('speeder AnimateVehicle leaves animation unset' if vehicle_type.lower()=='vh_speeder' else 'no matching idle animation')


def load_model(game, name, skin=None, frame=None, *, vehicle_type=''):
    name=game.model_path(name)
    if name.endswith('.md3'):return jk.load_model(game,name,skin,frame)
    raw=game.read(name);h=glm_header(raw);animation=q3.key_name(h['animation'])
    if animation=='*default':
        if frame not in (None,0):raise ValueError('Built-in skeleton only has frame 0')
        skeleton=jk.default_skeleton();frame=0;reason='Built-in *default one-bone skeleton'
    else:
        path=base.checked_name(animation+'.gla');chosen,reason=idle_pose(game,path,vehicle_type)
        if frame is None:frame=chosen
        else:reason='Explicit frame '+str(frame)
        skeleton=read_gla(game.read(path),frame)
    model=read_glm(raw,skeleton,skin)
    return model,dict(frame=frame,pose=reason,animation=animation,warnings=model['warnings'],hidden=model['hidden']),raw


def vehicle_definitions(game):
    if hasattr(game,'vehicles'):return game.vehicles
    vehicles={};seen={};game.vehicle_issues=[]
    # FS list order is archive directory order, then lower-priority mounts.
    for path in game.assets:
        if not re.fullmatch(r'ext_data/vehicles/[^/]+\.veh',path):continue
        try:
            blocks=definitions(game.read(path))
            if not blocks:game.vehicle_issues.append(dict(source=path,reason='No active definitions (comment-only template)'))
            for block in blocks:
                key=block['name'].lower()
                if key in seen:
                    game.vehicle_issues.append(dict(source=path,name=block['name'],reason='Duplicate vehicle name; first definition wins',winner=seen[key]));continue
                if path in vehicles:raise ValueError('Multiple distinct vehicles in one file are unsupported')
                seen[key]=path;vehicles[path]=block
        except ValueError as exc:game.vehicle_issues.append(dict(source=path,reason=str(exc)))
    game.vehicles=vehicles
    return vehicles


def vehicle_model(values):
    model=values.get('model','')
    if not model:raise ValueError('Vehicle has no model')
    return base.checked_name('models/players/'+model+'/model.glm')


def whole_character_skins(game):
    result=set()
    for path in game.assets:
        if not re.fullmatch(r'ext_data/npcs/[^/]+\.npc',path):continue
        try:
            for block in definitions(game.read(path)):
                row=block['values'];model=row.get('playermodel','').lower();skin=row.get('customskin','').lower()
                if model in CUSTOM_SPECIES and skin and '|' not in skin:
                    result.add('models/players/'+model+'/model_'+skin+'.skin')
        except ValueError as exc:game.issues.append(dict(source=path,reason='NPC definition: '+str(exc)))
    return result


def browser_excluded(name):
    folder=Path(name).parent.name
    return jk.browser_excluded(name) or folder in CUSTOM_SPECIES and Path(name).name!='model_default.skin'


def model_specs(game):
    vehicles=vehicle_definitions(game);bodies=set()
    for block in vehicles.values():
        try:bodies.add(vehicle_model(block['values']))
        except ValueError:pass  # The vehicle entry reports this failure.
    stock=whole_character_skins(game)
    for source,skin in jk.model_specs(game):
        if source in bodies:continue
        if skin and browser_excluded(skin) and skin not in stock:continue
        yield source,skin
    for source in sorted(vehicles):yield source,''


def category(name):
    if name.endswith('.veh'):return 'Vehicles'
    folder=Path(name).parent.name
    if name.startswith('models/players/'):
        if folder in ('rancor','wampa','sand_creature','mutant_rancor','tauntaun'):return 'Creatures'
        if folder in ('remote','r2d2_imp','r5d2_imp','assassin_droid','saber_droid'):return 'Droids'
    return jk.category(name)


def catalog_parts(game, source, skin):
    if not source.endswith('.veh'):
        surfaces=skin_surfaces(game,skin);model,meta,raw=load_model(game,source,surfaces)
        if not model['surfaces']:raise ValueError('Model has no visible surfaces (empty/off/bolt only)')
        return [dict(name=source,model=model,skin=surfaces)],meta,raw
    block=vehicle_definitions(game)[source];values=block['values'];model_path=vehicle_model(values)
    choices=values.get('skin','default').split('|');chosen=choices[0].lower()
    skin=str(Path(model_path).parent/('model_'+chosen+'.skin')).replace('\\','/')
    warnings=[]
    if skin not in game.assets:
        warnings.append('Absent vehicle skin '+skin+'; embedded shaders');skin=''
    surfaces=skin_surfaces(game,skin)
    model,meta,_=load_model(game,model_path,surfaces,vehicle_type=values.get('type',''))
    parts=[dict(name=model_path,model=model,skin=surfaces)]
    # The vehicle field table has no model-scale key. Spawn-entity/NPC scale
    # overrides belong to map instances, not to a .veh catalog definition.
    scale=1
    attachments=[];droid=values.get('droidnpc','').lower()
    if droid:
        selected='r2d2' if droid in ('random','default') else droid
        # Stock droid NPCs use the same player-model names and default skins.
        npcpath='ext_data/npcs/'+selected+'.npc'
        if npcpath not in game.assets:raise ValueError('Missing vehicle droid NPC: '+selected)
        npc=next((b['values'] for b in definitions(game.read(npcpath)) if b['name'].lower()==selected),None)
        if npc is None:raise ValueError('Missing vehicle droid definition: '+selected)
        dpath=base.checked_name('models/players/'+npc.get('playermodel',selected)+'/model.glm')
        dskin=str(Path(dpath).parent/('model_'+npc.get('customskin','default')+'.skin')).replace('\\','/').lower()
        ds=skin_surfaces(game,dskin) if dskin in game.assets else {}
        dm,di,_=load_model(game,dpath,ds)
        if '*droidunit' not in model['tags']:raise ValueError('Vehicle droid attachment has no *droidunit bolt')
        parts.append(dict(name=dpath,model=dm,skin=ds,tags=[model['tags']['*droidunit']]))
        attachments.append(dict(model=dpath,skin=dskin,tag='*droidunit',pose=di['pose'],choice=selected))
        warnings.extend(di['warnings'])
    if len(choices)>1:warnings.append('Random vehicle skin: deterministically chose '+chosen+' from '+', '.join(choices))
    if droid in ('random','default'):warnings.append('Random vehicle droid: deterministically chose R2D2 (MP default attachment)')
    meta['warnings'].extend(warnings)
    meta.update(vehicle=block['name'],vehicle_type=values.get('type',''),model=model_path,skin=skin,scale=scale,attachments=attachments)
    return parts,meta,game.read(source)


def import_catalog(install, output, model_filter=None):
    report=jk.import_catalog(install,output,model_filter,rules=sys.modules[__name__])
    game=Game(install);vehicle_definitions(game)
    report['vehicle_definitions']=[dict(source=k,**v) for k,v in game.vehicles.items()]
    report['vehicle_issues']=game.vehicle_issues
    stock=whole_character_skins(game)
    for row in report['skin_results']:
        if row['source'] in stock:row['browser_excluded']=False
    report['notes']=['Highest LOD, static standing/parked pose; no animation clips.',
        'Customizable species use shipped whole-character defaults only; no head/torso/legs menu combinations or NPC-only looks.',
        'Vehicle definitions use the first named definition; random skins/droids use one deterministic choice. No riders, exhaust effects or saber-definition assemblies.',
        'Ordinary saber model files remain weapon entries. Animation-only _humanoid level folders have no catalog entries.']
    q3.json_write(Path(output)/'import-report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install',required=True)
    parser.add_argument('--output',type=Path,default=q3.LOCAL_DATA/'ja');parser.add_argument('--filter')
    args=parser.parse_args();print(json.dumps(import_catalog(args.install,args.output,args.filter),indent=2))

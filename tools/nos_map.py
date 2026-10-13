"""Nosgoth map additions to the shared UE3 pipeline. Install data is read-only."""
from pathlib import Path
import re
import numpy as np

from tools.nos_package import Library
from tools.tas_package import Reader
from tools.tas_map import MapLibrary as UE3MapLibrary, object_properties, transform


ENTRIES = ('BT_Docks_Art_P','BurningVillage_Art_P','MOP_Art_P','MountainVillage_Art_P',
           'SCT_Art_P','TTM_Art_P','VUC_Art_P','TUT_Test01','TUT_K_Vampire','MPFrontEnd')


def map_name(path):
    return path.stem.removesuffix('_Art_P')


def entry_maps(library):
    files={Path(r['file']).stem.lower():library.install/r['file'] for r in library.inventory}
    return [files[n.lower()] for n in ENTRIES if n.lower() in files]


def map_section(path):
    return 'Tutorial' if path.stem.startswith('TUT_') else 'Multiplayer'


def foliage(p, ref):
    """862 foliage map: mesh -> clusters, 64-byte placement records, settings."""
    _,r=object_properties(p,ref)
    result=[]
    for _ in range(r.count(16,65536)):
        mesh=p.qualified(r.i32())
        if not mesh:raise ValueError('Foliage type has no mesh reference')
        if r.i32():
            raise ValueError('Foliage cluster layout not supported')
        for _ in range(r.count(64,200000)):
            base=r.i32();location=r.unpack('3f');rotation=r.unpack('3i');scale=r.unpack('3f')
            cluster=r.i32();prealign=r.unpack('3i');flags=r.i32();zoffset=r.unpack('f')[0]
            if not np.isfinite([*location,*scale,zoffset]).all():raise ValueError('Non-finite foliage placement')
            result.append(dict(source=mesh,matrix=transform(dict(Location=location,Rotation=rotation,DrawScale3D=scale)),
                               base=p.qualified(base),cluster=cluster,flags=flags,zoffset=zoffset))
        settings=r.i32()
        if settings:p.entry(settings)  # validate optional InstancedFoliageSettings reference
    if r.at!=len(r.data):raise ValueError('Trailing foliage placement data')
    return result


class MapLibrary(UE3MapLibrary,Library):
    map_suffixes=('.upk',)

    @staticmethod
    def sky_mesh(source,materials):
        return bool(re.search(r'sky(?:dome|sphere|mesh|box|_.*(?:sky|dome))',source,re.I))

    def extra_instances(self, paths, actors, instance, failures, omitted):
        for source,actor in actors.items():
            if actor['classname'].rsplit('.',1)[-1]!='InstancedFoliageActor':continue
            try:
                p,ref=self.locate(source)
                for number,item in enumerate(foliage(p,ref)):
                    try:
                        instance(item['source'],'StaticMesh',item['matrix'],dict(entity=source,classname=actor['classname'],
                                 npc=False,foliage=True,placement=number),decoration=True)
                    except (ValueError,OSError) as exc:failures.append(dict(entity=source,source=item['source'],reason=str(exc)))
            except (ValueError,OSError) as exc:failures.append(dict(entity=source,reason='Foliage: '+str(exc)))
        if not any(p.stem in ('TUT_Test01','TUT_K_Vampire') for p in paths):return
        for file in paths:
            p=self.package(file)
            nodes=[e['source'] for e in p.exports if e['class']=='SeqAct_SpawnBot_K' and not e['flags']&0x200]
            for source in nodes:
                try:
                    p,ref=self.locate(source);props,_=object_properties(p,ref)
                    kind=props.get('BotType')
                    if not kind or kind=='None':
                        omitted.append(dict(entity=source,reason='Tutorial spawn without statically resolved BotType'));continue
                    bot='Archetypes.Pawn.Bot.'+kind
                    bp,_=self.state(bot);component=bp.get('Mesh_K') or bp.get('Mesh');cp,_=self.state(component)
                    mesh=cp.get('SkeletalMesh')
                    if not mesh:raise ValueError('Bot archetype has no skeletal mesh')
                    # Resolve arrays in the spawn package's own reference space.
                    p,ref=self.locate(source);props,_=object_properties(p,ref)
                    links=Reader(props.get('VariableLinks',bytes(4)),p.names,resolve=p.qualified).struct_array()
                    targets=[]
                    for link in links:
                        if link.get('PropertyName')!='Transform':continue
                        for (vr,) in Reader(link.get('LinkedVariables',bytes(4))).array('i'):
                            targets.append(p.qualified(vr))
                    if not targets:raise ValueError('Tutorial spawn without Transform link')
                    for variable in targets:
                        vp,_=self.state(variable);target,_=self.state(vp.get('ObjValue'))
                        if 'Location' not in target:raise ValueError('Tutorial spawn transform without location')
                        instance(mesh,'SkeletalMesh',transform(target,cp),dict(entity=source,classname='BCMPGame.SeqAct_SpawnBot_K',
                                 npc=True,archetype=bot,spawn_preview=True),cp.get('Materials',[]))
                except (ValueError,KeyError,OSError) as exc:failures.append(dict(entity=source,reason='Tutorial bot: '+str(exc)))

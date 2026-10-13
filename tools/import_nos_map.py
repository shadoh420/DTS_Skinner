"""Nosgoth Maps: streamed environments, shared instances and tutorial spawn previews.

python -m tools.import_nos_map --install PATH --output build/nos-data --models build/nos-data/nos
Repeat --map NAME for a small sample. Never rewrites job-2's model index/catalog.
"""
import argparse
import json
from pathlib import Path
from tools.local_data import LOCAL_DATA
from tools import import_tas_map as ue3
from tools.nos_map import MapLibrary, entry_maps, map_name, map_section
from tools.nos_materials import Materials


def build_map(path,library,models,materials):
    scene,blobs=ue3.build_map(path,library,models,materials)
    scene.update(game='nos',name=map_name(path),format='nosgoth-862',foliage=sum(r['count'] for r in scene['decorations']))
    scene['unresolved_materials']=sorted({g.get('material_source') or '<empty slot>' for m in scene['model_library'].values()
                                        for g in m['groups'] if g.get('material_status')!='ready'})
    scene['gaps']=['Lighting and stripped cooked shaders are approximate; baked lightmap references retained',
                   'Tutorial NPCs preview linked spawn points, not simultaneous placements; unresolved bot types are listed and skipped',
                   'Ambient crows, emitters and gameplay logic are omitted; translucent effects may use a faint tint',
                   'Depth-of-field blockers and unsupported fog/occlusion helpers are omitted and listed; unresolved opaque materials draw neutral grey']
    # Release per-map JSON/variant caches; geometry stays content-addressed on disk.
    # This keeps the ten-map import bounded while another model job is running.
    models.cache.clear();models.variants.clear()
    return scene,blobs


def import_maps(install,output=LOCAL_DATA,replace=False,map_names=None,model_root=None):
    return ue3.import_maps(install,output,replace,map_names,model_root,game='nos',library_factory=MapLibrary,
                          materials_factory=Materials,entries=entry_maps,section=map_section,name_for=map_name,scene_builder=build_map)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',required=True,type=Path);parser.add_argument('--output',type=Path,default=LOCAL_DATA)
    parser.add_argument('--models',type=Path);parser.add_argument('--map',action='append');parser.add_argument('--replace',action='store_true')
    args=parser.parse_args();report=import_maps(args.install,args.output,args.replace,args.map,args.models)
    print(json.dumps({k:v for k,v in report.items() if k not in ('material_results','texture_results')},indent=2))
    raise SystemExit(bool(report['failed']))

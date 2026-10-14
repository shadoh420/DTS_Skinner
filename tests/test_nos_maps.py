"""Synthetic Nosgoth collections, foliage and tutorial spawn previews."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from tests.test_tas_maps import MapFixture as UE3Fixture
from tests.test_nos_models import Fixture as ModelFixture
from tests.test_tas import pack
from tools.import_nos import import_catalog
from tools.import_nos_map import import_maps
from tools.nos_map import MapLibrary, foliage, helper_reason
from tools.tas_package import Reader


class Fixture(UE3Fixture,ModelFixture):
    pass


def installed(root):
    cooked=root/'BCMPGame/CookedPCConsole'
    f=Fixture();actor=f.add('Actor','Class');component=f.add('StaticMeshComponent','Class')
    for name in ('StaticMeshActor','StaticMeshCollectionActor','InstancedFoliageActor'):
        f.add(name,'Class',base=actor)
    f.add('SkeletalMeshComponent','Class',base=component)
    f.write(cooked/'Engine.upk')
    f=Fixture();mat=f.materials();f.add('Prop','StaticMesh',f.static(mat));f.add('Body','SkeletalMesh',f.skeletal(mat))
    f.write(cooked/'Assets.upk')
    f=Fixture();group=f.add('Materials','Package')
    f.add('DOFBlock_MAT','Material',f.payload(f.prop('LightingModel','ByteProperty',f.name('MLM_Unlit'),'EMaterialLightingModel'),
          pack('3i',1,0,0)+bytes(28)),outer=group)
    f.write(cooked/'SP_Water.upk')
    f=Fixture();pawn=f.add('Pawn','Package');bots=f.add('Bot','Package',outer=pawn)
    bot=f.add('FastVampire_Bot','Pawn_Vampire_Bot_K',f.payload(f.obj('Mesh',len(f.exports)+2)),outer=bots)
    f.component('Mesh0','SkeletalMeshComponent',bot,f.obj('SkeletalMesh',f.external('Assets.Body','SkeletalMesh')))
    f.write(cooked/'Archetypes.upk')
    f=Fixture();prop=f.external('Assets.Prop','StaticMesh')
    collection=f.actor('Collection','StaticMeshCollectionActor',f.vector('Location',(52.5,0,0))+
                       f.refs('StaticMeshComponents',[len(f.exports)+2]))
    f.component('Mesh','StaticMeshComponent',collection,f.obj('StaticMesh',prop)+f.vector('Translation',(0,52.5,0)))
    settings=f.add('GrassSettings','InstancedFoliageSettings')
    rows=b''.join(pack('i3f3i3fi3iIf',0,x,0,0,0,0,0,1,1,1,-1,0,0,0,1,0) for x in (52.5,105))
    f.actor('Grass','InstancedFoliageActor',native=pack('4i',1,prop,0,2)+rows+pack('i',settings))
    marker=f.actor('SpawnPoint','PathNode',f.vector('Location',(105,0,0)))
    variable=f.add('Target','SeqVar_Object',f.payload(f.obj('ObjValue',marker)))
    link=f.prop('PropertyName','NameProperty',f.name('Transform'))+f.refs('LinkedVariables',[variable])+f.name('None')
    links=f.prop('VariableLinks','ArrayProperty',pack('i',1)+link)
    f.add('BotSpawn','SeqAct_SpawnBot_K',f.payload(f.prop('BotType','NameProperty',f.name('FastVampire_Bot'))+links))
    f.add('UnknownSpawn','SeqAct_SpawnBot_K',f.payload(links))
    f.add('Stream','LevelStreamingAlwaysLoaded',f.payload(f.prop('PackageName','NameProperty',f.name('TutorialArt'))))
    helper=f.actor('Helper','StaticMeshActor')
    f.component('HelperMesh','StaticMeshComponent',helper,f.obj('StaticMesh',prop)+
                f.refs('Materials',[f.external('SP_Water.Materials.DOFBlock_MAT','Material')]))
    f.finish(cooked/'TUT_Test01.upk')
    f=Fixture();prop=f.external('Assets.Prop','StaticMesh');a=f.actor('Scenery','StaticMeshActor')
    f.component('Mesh','StaticMeshComponent',a,f.obj('StaticMesh',prop));f.finish(cooked/'TutorialArt.upk')
    # Frontend is listed but has no drawable content.
    Fixture().finish(cooked/'MPFrontEnd.upk')


class NosMapTests(unittest.TestCase):
    def test_collections_foliage_sublevels_npcs_and_shared_index(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);install=root/'install';installed(install)
            models=root/'models/nos';import_catalog(install,models,['Assets.*'])
            original=(models/'model-index.json').read_bytes()
            report=import_maps(install,root/'data',True,model_root=models)
            self.assertFalse(report['failed']);self.assertFalse(report['package_failures'])
            self.assertEqual((models/'model-index.json').read_bytes(),original)
            scene=json.loads((root/'data/nos-maps/maps/tut_test01/scene.json').read_text())
            self.assertEqual((len(scene['instances']),scene['foliage'],scene['npcs']),(3,2,1))
            self.assertEqual(len(scene['streamed_packages']),2)
            collection=next(i for i in scene['instances'] if 'Collection' in i['entity'])
            self.assertEqual(collection['matrix'][12:15],[-1,0,1])
            npc=next(i for i in scene['instances'] if i['npc'])
            self.assertEqual(npc['matrix'][12:15],[0,0,2]);self.assertTrue(npc['spawn_preview'])
            self.assertEqual(npc['archetype'],'Archetypes.Pawn.Bot.FastVampire_Bot')
            self.assertTrue(any('without statically resolved BotType' in r['reason'] for r in scene['omitted']))
            helpers=report['results'][0]['omitted_helpers']
            self.assertEqual(len(helpers),1);self.assertIn('Depth-of-field',helpers[0]['reason'])
            self.assertEqual(helpers[0]['materials'],['SP_Water.Materials.DOFBlock_MAT'])
            self.assertTrue(all(m['store']=='models' for m in scene['model_library'].values()))
            self.assertTrue(all(not d['npc'] for d in scene['decorations']))
            raw=gzip.decompress((root/'data/nos-maps/maps/tut_test01/decorations.bin.gz').read_bytes())
            self.assertEqual(np.frombuffer(raw,'<f4').reshape(-1,16)[:,14].tolist(),[1,2])
            self.assertIn('Assets.Prop',scene['decorations'][0]['decorationType'])
            index=json.loads((root/'data/nos-maps/index.json').read_text())
            self.assertEqual(next(e for e in index if e['id']=='mpfrontend')['status'],'skipped')
            self.assertEqual(next(e for e in index if e['id']=='tut_test01')['group'],'Tutorial')

    def test_helper_usage_does_not_remove_ground_or_black_scenery(self):
        from unittest.mock import Mock
        mats=Mock();mats.definition.return_value=dict(root='FX_General.Mat.Black_MAT',settings={'LightingModel':'MLM_Unlit'})
        model=dict(source='Mesh.Card',bounds=[[0,0,0],[10,0,10]],groups=[{'material_source':'FX_General.Mat.Black_MAT'}])
        self.assertIn('occlusion card',helper_reason(model,mats))
        model['bounds']=[[0,0,0],[10,10,10]]
        self.assertIsNone(helper_reason(model,mats))
        model['bounds']=[[0,0,0],[10,0,10]];model['groups'][0]['material_source']=None
        self.assertIsNone(helper_reason(model,mats))
        model['groups'][0]['material_source']='Art.Floor';mats.definition.return_value=dict(root='Art.Floor',settings={})
        self.assertIsNone(helper_reason(model,mats))
        model['source']='FX_BTEnvironmentEffects.SM.FogVolumeRNormals_SM';model['groups'][0]['material_source']=None
        self.assertIn('Fog-volume shell',helper_reason(model,mats))

    def test_foliage_bounds_and_routes(self):
        import app
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);install=root/'install';installed(install);lib=MapLibrary(install)
            try:
                p,ref=lib.locate('TUT_Test01.Grass');self.assertEqual(len(foliage(p,ref)),2)
                with patch('tools.nos_map.object_properties',return_value=({},Reader(pack('i',1000)))):
                    with self.assertRaises(ValueError):foliage(p,ref)
            finally:lib.close()
            with patch.object(app,'local_data_dir',root),patch.dict(app.pack_dirs,nos=root/'nos'):
                client=app.app.test_client();page=client.get('/maps/nos/')
                self.assertEqual(page.status_code,200);self.assertIn(b'npc-toggle.js',page.data)
                self.assertIn(b'/static/nos-maps/adapter.js',page.data)
                with patch('app.import_nos_maps',return_value={'imported':['TUT_Test01']}) as importer:
                    self.assertEqual(client.post('/import_nos_maps',json={'path':str(install),'replace':True}).status_code,200)
                    importer.assert_called_once_with(str(install),root,True)
                    self.assertEqual(client.post('/import_nos_maps',json={'path':str(install)},headers={'Origin':'https://other.invalid'}).status_code,403)
            with self.assertRaises(ValueError):import_maps(install,install/'output')


if __name__=='__main__':unittest.main()

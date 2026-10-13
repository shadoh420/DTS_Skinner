"""Synthetic UE3 map packages: no retail bytes or machine-specific temp paths."""
import gzip
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from tests.test_tas import Fixture,pack,bulk
from tools.import_tas import import_catalog
from tools.import_tas_map import import_maps,entry_maps,map_section,Models
from tools.tas_map import MapLibrary,object_properties,transform,is_npc,bsp
from tools.tas_package import Package


class MapFixture(Fixture):
    def __init__(self):
        super().__init__();self.external_meta={};self.export_meta={};self.actors=[]

    def external(self,source,cls):
        if source in self.imports:return -self.imports.index(source)-1
        parent=self.external(source.rsplit('.',1)[0],'Package') if '.' in source else 0
        self.imports.append(source);ref=-len(self.imports)
        self.external_meta[ref]=(source.rsplit('.',1)[-1],parent,cls);return ref

    def add(self,name,cls,payload=None,flags=0,outer=0,archetype=0,base=0):
        ref=super().add(name,cls,payload,flags);self.export_meta[ref]=(outer,archetype,base,cls=='Class');return ref

    def refs(self,name,values):
        return self.prop(name,'ArrayProperty',pack('i',len(values))+b''.join(pack('i',i) for i in values))

    def vector(self,name,value):
        return self.prop(name,'StructProperty',pack('3f',*value),'Vector')

    def integer(self,name,value):
        return self.prop(name,'IntProperty',pack('i',value))

    def actor(self,name,cls,props=b'',native=b'',archetype=0):
        payload=pack('iiQHi',-1,-1,0,0,-1)+self.payload(props,native)
        ref=self.add(name,cls,payload,flags=0x0200000000000000,archetype=archetype)
        self.actors.append(ref);return ref

    def component(self,name,cls,owner,props,archetype=0):
        return self.add(name,cls,bytes(4)+self.payload(props),outer=owner,archetype=archetype)

    def finish(self,path):
        owner=self.add('PersistentLevel','Level')
        text=lambda s:pack('i',len(s)+1)+s.encode()+b'\0'
        native=pack('2i',owner,len(self.actors))+b''.join(pack('i',i) for i in self.actors)
        native+=b''.join(text(s) for s in ('unreal','','Example',''))+pack('4i',0,7777,1,0)
        name,cls,_,flags=self.exports[owner-1];self.exports[owner-1]=(name,cls,self.payload(native=native),flags)
        return self.write(path)

    def write(self,path,**kwargs):
        engine=self.external('Engine','Package')
        for name,parent,cls in self.external_meta.values():self.name(name);self.name(cls)
        path=super().write(path,**kwargs);raw=bytearray(path.read_bytes());eo,io=struct.unpack_from('<i',raw,32)[0],struct.unpack_from('<i',raw,40)[0]
        for i,value in enumerate(self.imports,1):
            if -i in self.external_meta:
                name,parent,cls=self.external_meta[-i]
                raw[io+(i-1)*28+8:io+(i-1)*28+16]=self.name(cls)
                raw[io+(i-1)*28+20:io+i*28]=self.name(name)
            else:parent=engine
            struct.pack_into('<i',raw,io+(i-1)*28+16,parent)
        for ref,(outer,archetype,base,is_class) in self.export_meta.items():
            at=eo+(ref-1)*68
            if is_class:struct.pack_into('<i',raw,at,0)
            struct.pack_into('<ii',raw,at+4,base,outer);struct.pack_into('<i',raw,at+20,archetype)
        path.write_bytes(raw);return path


def engine(root):
    f=MapFixture();actor=f.add('Actor','Class');mesh=f.add('StaticMeshComponent','Class')
    for name in ('StaticMeshActor','Terrain','TrDeployable_Turret','TrPlayerPawn','TrPowerGenerator','SpeedTreeActor','WorldInfo'):
        f.add(name,'Class',base=actor)
    f.add('Default__Actor','Actor',f.payload(f.prop('DrawScale','FloatProperty',pack('f',1))+f.vector('DrawScale3D',(1,1,1))),flags=0x200)
    f.add('Default__Terrain','Terrain',f.payload(f.vector('DrawScale3D',(52.5,52.5,128))),flags=0x200)
    f.add('Default__StaticMeshComponent','StaticMeshComponent',f.payload(f.vector('Scale3D',(1,1,1))),flags=0x200)
    f.write(root/'TribesGame/CookedPC/Engine.u')


def fixture(root):
    engine(root);f=MapFixture();mat=f.materials();prop=f.add('Prop','StaticMesh',f.static(mat));tree=f.add('Tree','SpeedTree')
    for name,cls in [('SceneryTurret','StaticMeshActor'),('FunctionalTurret','TrDeployable_Turret'),('Generator','TrPowerGenerator'),('Bot','TrPlayerPawn')]:
        actor=f.actor(name,cls,f.vector('Location',(52.5,0,0)))
        f.component(name+'Mesh','StaticMeshComponent',actor,f.obj('StaticMesh',prop))
    attachment=f.actor('Attachment','StaticMeshActor',f.obj('Owner',f.actors[1])+f.boolean('bHardAttach',True))
    f.component('AttachedMesh','StaticMeshComponent',attachment,f.obj('StaticMesh',prop))
    ta=f.actor('TreeActor','SpeedTreeActor');f.component('TreeComponent','SpeedTreeComponent',ta,f.obj('SpeedTree',tree))
    # A real archetype component adds local translation and scale to actor placement.
    template=f.add('Template','StaticMeshComponent',bytes(4)+f.payload(f.obj('StaticMesh',prop)+f.vector('Translation',(52.5,0,0))),flags=0x400000000)
    actor=f.actor('Archetyped','StaticMeshActor',f.vector('Location',(0,52.5,0)))
    f.component('Inherited','StaticMeshComponent',actor,f.prop('Scale','FloatProperty',pack('f',2)),archetype=template)
    f.add('Stream','LevelStreamingAlwaysLoaded',f.payload(f.prop('PackageName','NameProperty',f.name('Terrain_Sub'))))
    f.finish(root/'TribesGame/CookedPC/Maps/TrCTF-Test.udk')
    f=MapFixture();mat=f.materials();prop=f.add('Grass','StaticMesh',f.static(mat))
    tm=f.add('TerrainMaterial','TerrainMaterial',f.payload(f.obj('Material',mat)+f.prop('MappingScale','FloatProperty',pack('f',4))))
    setup_item=f.obj('Material',tm)+f.name('None')
    setup=f.add('LayerSetup','TerrainLayerSetup',f.payload(f.prop('Materials','ArrayProperty',pack('i',1)+setup_item)))
    layers=[]
    for i in range(5):layers.append(f.obj('Setup',setup)+f.integer('AlphaMapIndex',i)+f.name('None'))
    factory=f.add('Factory','StaticMeshComponentFactory',f.payload(f.obj('StaticMesh',prop)))
    inst=f.prop('X','FloatProperty',pack('f',.25))+f.prop('Y','FloatProperty',pack('f',.5))+f.prop('Scale','FloatProperty',pack('f',1))+f.integer('Yaw',0)+f.name('None')
    deco=f.obj('Factory',factory)+f.prop('Instances','ArrayProperty',pack('i',1)+inst)+f.name('None')
    dl=f.integer('AlphaMapIndex',0)+f.prop('Decorations','ArrayProperty',pack('i',1)+deco)+f.name('None')
    props=f.integer('NumVerticesX',2)+f.integer('NumVerticesY',2)+f.prop('Layers','ArrayProperty',pack('i',5)+b''.join(layers))+f.prop('DecoLayers','ArrayProperty',pack('i',1)+dl)
    native=pack('i4H',4,32768,32768,32800,32800)+pack('i',4)+bytes(4)+pack('i',5)+b''.join(pack('i',4)+bytes([i*50]*4) for i in range(5))
    f.actor('Ground','Terrain',props,native);f.finish(root/'TribesGame/CookedPC/Maps/Terrain_Sub.fmap')


class MapTests(unittest.TestCase):
    def test_cdo_templates_and_dominant_shadow_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            f=MapFixture();owner=f.add('Default__Light','Actor',flags=0x200)
            template=f.add('Template','DominantDirectionalLightComponent',pack('2i',0,0)+f.name('Template')+f.payload(f.integer('Brightness',2)),flags=0x400000000,outer=owner)
            placed=f.add('Placed','DominantDirectionalLightComponent',pack('i3H',3,65535,10,20)+bytes(4)+f.payload(f.integer('Brightness',3)+f.prop('Bounds','StructProperty',pack('6fB',0,0,0,10,20,30,1),'Box')))
            p=Package(f.write(Path(td)/'Test.upk'))
            try:
                self.assertEqual(object_properties(p,template)[0]['Brightness'],2)
                self.assertEqual(object_properties(p,placed)[0]['Brightness'],3)
                self.assertEqual(object_properties(p,placed)[0]['Bounds'],(0,0,0,10,20,30,1))
            finally:p.close()

    def test_transform_and_npc_contract(self):
        a=dict(Location=(105,0,0),PrePivot=(52.5,0,0),DrawScale=2,DrawScale3D=(1,2,1))
        c=dict(Translation=(52.5,0,0),Scale=3,Scale3D=(1,1,1))
        m=np.asarray(transform(a,c)).reshape(4,4).T
        np.testing.assert_allclose(m[:3,3],[0,0,2]);np.testing.assert_allclose(np.diag(m)[:3],[12,6,6])
        self.assertTrue(is_npc(['TribesGame.TrBaseTurret_BloodEagle','TribesGame.TrDeployable_Turret']))
        self.assertTrue(is_npc(['TribesGame.TrPlayerPawn']))
        self.assertFalse(is_npc(['Engine.StaticMeshActor','Scenery.Turret']))
        self.assertFalse(is_npc(['TribesGame.TrPowerGenerator']))

    def test_streamed_terrain_five_layers_decorations_and_models(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);install=root/'install';fixture(install)
            # Model index is imported independently, then held byte-identical during map import.
            models=root/'models/tas';import_catalog(install,models,model_paths=['TrCTF-Test.Prop','Terrain_Sub.Grass'])
            before={p:p.read_bytes() for p in models.rglob('*') if p.is_file()}
            report=import_maps(install,root/'data',map_names=['TrCTF-Test'],model_root=models)
            self.assertEqual(report['failed'],{});self.assertEqual(report['results'][0]['missing'],0)
            scene=json.loads((root/'data/tas-maps/maps/trctf-test/scene.json').read_text())
            self.assertEqual(len(scene['streamed_packages']),2);self.assertEqual(scene['actors'],8)
            self.assertEqual(scene['speedtrees'],1);self.assertEqual(scene['npcs'],2)
            self.assertEqual(len(scene['terrain'][0]['layers']),5);self.assertEqual(scene['terrain'][0]['bytes'],8)
            self.assertEqual(sum(d['count'] for d in scene['decorations']),2)
            body=next(x for x in scene['instances'] if x['entity'].endswith('.FunctionalTurret'))
            self.assertEqual(sum(x['entity']==body['entity'] for x in scene['instances']),2)
            self.assertTrue(all(not x['npc'] for x in scene['instances'] if x['entity'].endswith(('.Generator','.SceneryTurret'))))
            self.assertTrue(any(m['store']=='models' for m in scene['model_library'].values()))
            self.assertTrue(any(m['approximate'] for m in scene['model_library'].values()))
            self.assertEqual({p:p.read_bytes() for p in before},before)
            self.assertFalse((root/'data/tas/model-index.json').exists())
            self.assertTrue((root/'data/tas/map-model-index.json').is_file())
            self.assertEqual(len(gzip.decompress((root/'data/tas-maps/maps/trctf-test/decorations.bin.gz').read_bytes())),128)

    def test_defaults_component_preamble_and_mode_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root);lib=MapLibrary(root)
            try:
                props,cls=lib.state('TrCTF-Test.Archetyped.Inherited')
                self.assertEqual(props['StaticMesh'],'TrCTF-Test.Prop');self.assertEqual(props['Translation'],(52.5,0,0));self.assertEqual(props['Scale'],2)
                self.assertEqual([p.stem for p in entry_maps(lib)],['TrCTF-Test'])
                self.assertEqual(map_section(Path('TrCTFBlitz-Test.fmap')),'Blitz / Retail')
                self.assertEqual(map_section(Path('TrTDM-Test.udk')),'TDM / Community')
            finally:lib.close()

    def test_bsp_native_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            f=MapFixture();vectors=pack('9f',0,0,1,1,0,0,0,1,0);points=pack('9f',0,0,0,52.5,0,0,0,52.5,0)
            node=bytearray(64);struct.pack_into('<ii',node,16,0,0);node[54]=3
            surface=pack('iI6i',0,0,0,0,1,2,-1,0)+bytes(28)
            verts=b''.join(pack('ii4f',i,-1,0,0,0,0) for i in range(3))
            native=bytes(28)+bulk(vectors,12)+bulk(points,12)+bulk(node,64)+pack('2i',1,1)+surface+bulk(verts,24)
            f.add('World','Model',f.payload(native=native));p=Package(f.write(Path(td)/'Test.upk'))
            try:
                world=bsp(p,1);self.assertEqual(world['nodes'][0]['count'],3);self.assertEqual(world['verts'].tolist(),[0,1,2])
            finally:p.close()

    def test_routes_and_output_guard(self):
        import app
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root/'install');out=root/'data'
            report=import_maps(root/'install',out,map_names=['TrCTF-Test'])
            with patch.object(app,'local_data_dir',out),patch.dict(app.pack_dirs,tas=out/'tas'):
                client=app.app.test_client();response=client.get('/maps/tas/')
                self.assertEqual(response.status_code,200);self.assertIn(b'npc-toggle.js',response.data);response.close()
                scene=client.get('/tas-map-data/maps/trctf-test/scene.json').json
                model=next(iter(scene['model_library'].values()));url='/tas-map-data/'+model['store']+'/'+model['geometry']['file']
                response=client.get(url);self.assertEqual(response.status_code,200);response.close()
                self.assertEqual(client.get('/tas-map-data/map-models/invalid.bin').status_code,404)
                with patch('app.import_tas_maps',return_value={'imported':['Test']}) as importer:
                    self.assertEqual(client.post('/import_tas_maps',json={'path':str(root/'install')}).status_code,200)
                    importer.assert_called_once_with(str(root/'install'),out,False)
                    self.assertEqual(client.post('/import_tas_maps',json={'path':str(root/'install')},headers={'Origin':'https://other.invalid'}).status_code,403)
            with self.assertRaises(ValueError):import_maps(root/'install',root/'install/data')


if __name__=='__main__':unittest.main()

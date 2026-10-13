"""Invented packages only. Native layouts, inheritance and shared storage contracts."""
import gzip
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from tests.test_tvg import Fixture, compact, pack, array, install_fixture
from tools.import_tvg import import_catalog, Materials
from tools.import_tvg_map import (Models, import_maps, terrain, decoration_matrices,
                                  campaign_sections, section, tag_attachments, LAYER_LIMIT)
from tools.tvg_package import Library, Package
from tools.tvg_map import MapPackage, transform, viewer, viewpoint, is_npc


def hdr(version, sub):
    return pack('3i', 3, version, sub)


class MapFixture(Fixture):
    def __init__(self):
        super().__init__()
        self.stacks = []

    def actor(self, name, cls, props=b'', native=b'', headers=1):
        ref = len(self.exports)+1
        body = self.hdr(25)+self.hdr(25)*headers+bytes(25)+compact(cls)*2+compact(ref)+bytes(10)+props+b'\0'+native
        self.add(name, cls, body)
        self.stacks.append(ref)
        return ref

    def write(self, path):
        result = super().write(path)
        p = Package(result)
        raw = bytearray(Path(result).read_bytes())
        try:
            p.at = 4096
            for ref in range(1,len(p.exports)+1):
                p.index();p.index();p.take(4);p.index()
                if ref in self.stacks:
                    struct.pack_into('<I',raw,p.at,0x02000000)
                p.take(4)
                if p.index():p.index()
        finally:
            p.close()
        Path(result).write_bytes(raw)
        return result


def model_body(f, texture, polys=0, bad_point=False, flags=0):
    vectors = [(0,0,1),(1,0,0),(0,1,0)]
    points = [(0,0,0),(52.5,0,0),(0,52.5,0)]
    b = f.hdr(25)+b'\0'+bytes(41)+hdr(0,5)+array('3f',vectors)+array('3f',points)
    b += b'\1'+hdr(2,2)+pack('4f',0,0,1,0)+bytes(9)+b'\0'*7+bytes(32)+bytes([0,1,3])+bytes(21)
    b += b'\1'+hdr(1,1)+compact(texture)+pack('I',flags)+bytes([0,0,1,2,0,0])+pack('5f',0,0,1,0,32)
    # The unused fourth entry deliberately points outside the current point pool.
    b += b'\4'+compact(999 if bad_point else 0)+b'\0\1\0\2\0'+compact(999)+b'\0'
    return b+bytes(8)+compact(polys)


def polys_body(f, texture):
    return (f.hdr(25)+b'\0'+pack('ii',1,1)+b'\3'+pack('12f',0,0,0,0,0,1,1,0,0,0,1,0)+
            pack('9f',0,0,0,52.5,0,0,0,52.5,0)+bytes(4)+b'\0'+compact(texture)+b'\0\0\x81'+pack('f',32))


def engine_fixture(root):
    f=Fixture()
    actor=f.add('Actor',0,f.defaults(f.prop('DrawScale',4,pack('f',1))+f.prop('DrawScale3D',10,pack('3f',1,1,1),'Vector')))
    for name,cls in [('DrawScale','FloatProperty'),('DrawScale3D','StructProperty'),('DrawType','ByteProperty'),
                     ('StaticMesh','ObjectProperty'),('Mesh','ObjectProperty'),('bHidden','BoolProperty')]:
        f.add(name,f.imp(cls),outer=actor)
    mesh=f.external('Example','Prop','StaticMesh')
    f.add('StaticMeshActor',0,f.defaults(f.prop('StaticMesh',5,compact(mesh))+f.prop('DrawType',1,b'\10')),base=actor)
    f.add('TerrainInfo',0,f.defaults(),base=actor)
    f.write(root/'Program/Bin/Engine.u')
    f=Fixture();actor=f.external('Engine','Actor','Class')
    skin=f.add('SkinInfo',0,f.defaults())
    f.add('applicableMesh',f.imp('ObjectProperty'),outer=skin);f.add('meshSkins',f.imp('ArrayProperty'),outer=skin)
    ragdoll=f.add('Ragdoll',0,f.defaults(),base=actor)
    mesh=f.external('Example','Hero','SkeletalMesh')
    props=f.prop('Mesh',5,compact(mesh))+f.prop('DrawType',1,b'\2')
    f.add('Character',0,f.defaults(props),base=ragdoll)
    f.add('Turret',0,f.defaults(props),base=actor)
    f.add('Vehicle',0,f.defaults(props),base=actor)
    f.write(root/'Program/Bin/Gameplay.u')


def map_fixture(root, flags=0):
    install_fixture(root);engine_fixture(root)
    system=root/'Content/System';system.mkdir()
    (system/'Campaign.ini').write_text('; 01-Test\nmissions=(mapName="SP-Test",missionName="Test")\n')
    f=MapFixture()
    level=f.add('MyLevel',f.imp('Level'))
    material=f.external('Example','Red','Texture')
    world=f.add('World',f.imp('Model'),model_body(f,material,flags=flags))
    cls=f.external('Engine','StaticMeshActor','Class')
    prop=f.actor('Prop',cls,f.prop('Location',10,pack('3f',52.5,105,0),'Vector'))
    actors=[prop]
    for name in ('Character','Turret','Vehicle'):
        actors.append(f.actor(name,f.external('Gameplay',name,'Class'),headers=2))
    body=f.hdr(25)+b'\0'+pack('ii',len(actors),len(actors))+b''.join(compact(a) for a in actors)
    body+=b'\1\0'*4+b'\0'+pack('ii',7777,1)+hdr(8,6)+bytes(4)+compact(world)
    entry=f.exports[level-1];f.exports[level-1]=(*entry[:2],body,*entry[3:])
    return f.write(root/'Content/Maps/SP-Test.tvm')


class NativeTests(unittest.TestCase):
    def test_held_attachment_identity_does_not_tag_vehicle_or_pickup(self):
        instances=[dict(entity=i,npc=i==1) for i in range(1,6)]
        actors={i:dict(ancestry=[c],props={},owned={'Owner':('Map',1)}) for i,c in
                enumerate(('gameplay.character','gameplay.weapon','gameplay.vehicle','gameplay.pickup','gameplay.jetpack'),1)}
        tag_attachments(instances,actors,'Map')
        self.assertEqual([i['npc'] for i in instances],[True,True,False,False,True])
        self.assertEqual([i['entity'] for i in instances],[1,1,3,4,1])
        self.assertEqual(instances[1]['actor'],2)

    def test_stored_vertex_colors_and_stack_owner_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=MapFixture();color=bytes([1,2,3,255])*3
            ref=f.add('Light',f.imp('StaticMeshInstance'),f.hdr(25)+b'\0'+hdr(2,5)+b'\3'+color)
            actor=f.actor('Actor',f.imp('Actor'))
            # Corrupt the stack owner with another valid object reference.
            row=f.exports[actor-1];body=row[2];f.exports[actor-1]=(*row[:2],body[:51]+b'\1'+body[52:],*row[3:])
            p=MapPackage(f.write(Path(tmp)/'Stack.tvm'))
            try:
                self.assertEqual(p.vertex_colors(ref),color)
                with self.assertRaisesRegex(ValueError,'owner mismatch'):p.properties(actor)
            finally:p.close()

    def test_actor_stack_inheritance_level_and_npc_trap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'install';path=map_fixture(root);p=MapPackage(path);lib=Library(root)
            try:
                level=p.level(1);self.assertEqual(level['model'],2);self.assertEqual(level['actors'],[3,4,5,6])
                self.assertEqual(p.properties(3)['Location'],(52.5,105,0))
                for ref,npc in [(3,False),(4,True),(5,True),(6,False)]:
                    props,chain=lib.class_info(p.qualified(p.exports[ref-1]['cls']))
                    self.assertEqual(props['DrawScale'][1],1)
                    self.assertEqual(is_npc(chain),npc)
                    self.assertEqual(p.properties(ref),{'Location':(52.5,105,0)} if ref==3 else {})
                self.assertFalse(is_npc(['Gameplay.Ragdoll','Gameplay.Rook','Engine.Pawn']))
                self.assertTrue(is_npc(['AI.Glorax','Gameplay.Character','Gameplay.Ragdoll']))
                self.assertTrue(is_npc(['X.DeployedTurret','Gameplay.Turret']))
                self.assertFalse(is_npc(['X.InventoryStation','Gameplay.BaseDevice']))
            finally:p.close();lib.close()

    def test_bsp_used_pool_bounds_polys_and_unknown_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Fixture();tex=f.imp('Texture');poly=f.add('Polys',f.imp('Polys'),polys_body(f,tex))
            model=f.add('Model',f.imp('Model'),model_body(f,tex,poly))
            p=MapPackage(f.write(Path(tmp)/'Map.tvm'))
            try:
                m=p.model(model);self.assertEqual(m['polys'],poly);self.assertEqual(len(m['nodes']),1)
                self.assertEqual(m['surfaces'][0]['u'],1);self.assertEqual(p.polys(poly)[0]['points'][1],(52.5,0,0))
            finally:p.close()
            f=Fixture();f.add('Bad',f.imp('Model'),model_body(f,0,bad_point=True));p=MapPackage(f.write(Path(tmp)/'Bad.tvm'))
            try:
                with self.assertRaisesRegex(ValueError,'point pool'):p.model(1)
            finally:p.close()
            f=Fixture();f.add('Bad',f.imp('Model'),f.hdr(25)+b'\0'+bytes(41)+hdr(99,0));p=MapPackage(f.write(Path(tmp)/'Revision.tvm'))
            try:
                with self.assertRaisesRegex(ValueError,'revision'):p.model(1)
            finally:p.close()

    def test_transforms_prepivot_rotation_and_scale(self):
        props=dict(Location=(105,52.5,157.5),Rotation=(0,16384,0),DrawScale=2,DrawScale3D=(1,2,3),PrePivot=(52.5,0,0))
        m=np.asarray(transform(props)).reshape(4,4).T
        # A model vertex at pre-pivot ends at Location, after nonuniform scale + yaw.
        np.testing.assert_allclose(m[:3,:3]@viewer(props['PrePivot'])+m[:3,3],viewer(props['Location']),atol=1e-6)
        np.testing.assert_allclose(np.linalg.norm(m[:3,:3],axis=0),[4,6,2])
        self.assertEqual(viewpoint(props)['yaw'],90)  # +native Y is viewer -X.
        self.assertEqual(viewpoint(dict(Rotation=(65535,-16384,0)))['yaw'],270)
        with self.assertRaisesRegex(ValueError,'Non-finite'):transform(dict(DrawScale=float('nan')))

    def test_terrain_g16_layers_decorations_and_visibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'Program/Bin').mkdir(parents=True)
            f=MapFixture();tex=f.imp('Texture')
            height=f.add('Heights',tex,f.texture(10,pack('4H',32768,32772,32776,32780),2,2))
            white=f.add('White',tex,f.texture(5,b'\xff'*16,2,2))
            sm=f.add('Grass',f.imp('StaticMesh'),f.static(white))
            layer=f.prop('Texture',5,compact(white))+f.prop('AlphaMap',5,compact(white))+f.prop('UScale',4,pack('f',2))+b'\0'
            dec=f.prop('StaticMesh',5,compact(sm))+f.prop('DensityMap',5,compact(white))+f.prop('MaxPerQuad',2,pack('i',2))+b'\0'
            props=f.prop('TerrainMap',5,compact(height))+f.prop('Layers',10,layer,'TerrainLayer')+f.prop('DecoLayers',9,b'\1'+dec)
            props+=f.prop('QuadVisibilityBitmap',9,b'\1'+pack('I',15))+f.prop('EdgeTurnBitmap',9,b'\1'+bytes(4))
            coords=pack('12f',1,1,32768,52.5,0,0,0,52.5,0,0,0,.25)
            native=hdr(4,3)+b'\0'+pack('2i',1,1)+coords*3+pack('2i',2,2)+b'\4'+b'\xff'*16+b'\0'
            ref=f.actor('Terrain',f.imp('TerrainInfo'),props,native)
            path=f.write(root/'Content/Maps/MP-Test.tvm');p=MapPackage(path);lib=Library(root)
            try:
                mats=Materials(lib,root/'data/tvg/textures');own=p.properties(ref);chunks=[]
                row,heights=terrain(p,ref,own,lib,mats,chunks)
                self.assertEqual(row['bytes'],8);self.assertEqual(len(b''.join(chunks)),16)
                self.assertEqual(heights[1,1],32780);self.assertEqual(row['layers'][0]['scale'],[2,1])
                deco=p.struct_array(own['DecoLayers'])[0]
                a=decoration_matrices(p,deco,row,heights,mats)
                self.assertEqual(a.shape,(2,16));np.testing.assert_array_equal(a,decoration_matrices(p,deco,row,heights,mats))
                self.assertEqual(len(decoration_matrices(p,deco,row,heights,mats,bytes(4))),0)
                self.assertEqual(row['vertex_light'].endswith('.png'),True)
                own=p.properties(ref)
                for number in range(1,6):own[f'Layers[{number}]']=own['Layers']
                limited,_=terrain(p,ref,own,lib,mats,[])
                self.assertEqual(len(limited['layers']),LAYER_LIMIT)
                self.assertEqual(len(limited['omitted_layers']),2)
            finally:p.close();lib.close()


class IntegrationTests(unittest.TestCase):
    def test_bsp_portal_flag_winding_and_masked_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            for flags,expected in [(0x4000000,0),(0x20000|2,3)]:
                root,out=Path(tmp)/str(flags)/'install',Path(tmp)/str(flags)/'data'
                map_fixture(root,flags);import_catalog(root,out/'tvg');report=import_maps(root,out)
                self.assertEqual(report['failed'],{})
                folder=out/'tvg-maps/maps/sp-test';scene=json.loads((folder/'scene.json').read_text())
                self.assertEqual(scene['indices'],expected)
                if expected:
                    self.assertEqual(scene['groups'][0]['settings']['alphaTest'],.5)
                    raw=(folder/'geometry.bin').read_bytes();vertices=np.frombuffer(raw,'<f4',scene['vertices']*3).reshape(-1,3)
                    indices=np.frombuffer(raw,'<u4',expected,scene['vertices']*20)
                    a,b,c=vertices[indices]
                    self.assertGreater(np.cross(b-a,c-a)[1],0)

    def test_import_shared_index_npcs_storage_and_input_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,out=Path(tmp)/'install',Path(tmp)/'data';map_fixture(root)
            import_catalog(root,out/'tvg')
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            model_before={p:p.read_bytes() for p in (out/'tvg/model_json').iterdir()}
            report=import_maps(root,out)
            self.assertEqual(report['failed'],{});r=report['results'][0]
            self.assertEqual((r['actors'],r['instances'],r['npcs'],r['missing']),(4,4,2,0))
            scene=json.loads((out/'tvg-maps/maps/sp-test/scene.json').read_text())
            entries=list(scene['model_library'].values())
            self.assertEqual(len(entries),2)
            self.assertEqual(scene['instances'][0]['matrix'][12:15],[-2,0,1])
            self.assertEqual([i['npc'] for i in scene['instances']],[False,True,True,False])
            self.assertEqual(gzip.decompress((out/'tvg-maps/maps/sp-test/decorations.bin.gz').read_bytes()),b'')
            self.assertFalse((out/'tvg-maps/textures').exists());self.assertFalse((out/'tvg-maps/models').exists())
            shared=next((out/'tvg/textures').glob('example_red*.png'));shared.write_bytes(b'user texture')
            import_maps(root,out,True)
            self.assertEqual(shared.read_bytes(),b'user texture')
            self.assertEqual({p:p.read_bytes() for p in before},before)
            self.assertEqual({p:p.read_bytes() for p in model_before},model_before)
            index=json.loads((out/'tvg/model-index.json').read_text())
            self.assertIn(index['example.prop']['geometry'],[m['geometry'] for m in entries])

    def test_model_skins_share_buffer_and_missing_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,out=Path(tmp)/'install',Path(tmp)/'data';install_fixture(root);import_catalog(root,out/'tvg');lib=Library(root)
            try:
                models=Models(out/'tvg',Materials(lib,out/'tvg/textures'))
                a,one=models.get('Example.Hero','SkeletalMesh')
                b,two=models.get('Example.Hero','SkeletalMesh',['BlueSkin.BlueSkin'])
                self.assertNotEqual(a,b);self.assertEqual(one['geometry'],two['geometry'])
                with self.assertRaisesRegex(ValueError,'missing'):models.get('No.Mesh','StaticMesh')
            finally:lib.close()

    def test_sections_and_safe_routes(self):
        import app
        with tempfile.TemporaryDirectory() as tmp:
            root,out=Path(tmp)/'install',Path(tmp)/'data';map_fixture(root);import_catalog(root,out/'tvg');import_maps(root,out)
            self.assertEqual(section('SP-Test',campaign_sections(root/'Content/System/Campaign.ini')),'Campaign / 01-Test')
            self.assertEqual(section('MP-Isle',{}),'Multiplayer / Stock')
            self.assertEqual(section('MP-BEML4-Test',{}),'Multiplayer / Community / BEML')
            self.assertEqual(section('Entry',{}),'Utility and prefab')
            with patch.object(app,'local_data_dir',out),patch.dict(app.pack_dirs,tvg=out/'tvg'):
                client=app.app.test_client()
                response=client.get('/maps/tvg/')
                self.assertEqual(response.status_code,200);self.assertIn(b'npc-toggle',response.data);response.close()
                response=client.get('/tvg-map-data/maps/sp-test/decorations.bin.gz')
                self.assertEqual(response.status_code,200);self.assertNotIn('Content-Encoding',response.headers);response.close()
                self.assertEqual(client.get('/tvg-map-data/models/not-a-hash.bin').status_code,404)
                self.assertEqual(client.post('/import_tvg_maps',json={'path':str(root)},headers={'Origin':'https://invalid.example'}).status_code,403)
                with patch.object(app,'import_tvg_maps',return_value={'imported':['SP-Test']}) as importer:
                    self.assertEqual(client.post('/import_tvg_maps',json={'path':str(root),'replace':True}).status_code,200)
                    importer.assert_called_once_with(str(root),out,True)
            with self.assertRaisesRegex(ValueError,'outside'):import_maps(root,root/'data')


if __name__=='__main__':
    unittest.main()

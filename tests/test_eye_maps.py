"""Synthetic BSP20/pakfile/prop fixtures. No retail paths or bytes."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import zipfile
import numpy as np
from tools import source_bsp as bsp
from tools import import_eye_map as maps
from tools.import_eye import import_catalog
from tools.source_assets import read_install
from tests.test_eye import install_fixture, vtf_fixture


def vhv_fixture(checksum=123):
    b=bytearray(40+56);struct.pack_into('<6I',b,0,2,checksum,4,4,6,2)
    for i in range(2):
        struct.pack_into('<3I',b,40+i*28,0,3,len(b));b.extend(bytes([16,32,64,255])*3)
    return bytes(b)


def bsp_fixture(text='',displacement=False,pak=None):
    lumps=[b'']*64
    lumps[0]=('{"classname" "worldspawn" "skyname" "test"}'+text).encode()+b'\0'
    lumps[1]=struct.pack('<4fi',0,0,1,0,2)
    lumps[2]=struct.pack('<3f5i',.5,.5,.5,0,16,16,16,16)
    lumps[3]=b''.join(struct.pack('<3f',*p) for p in ((0,0,0),(0,16,0),(16,16,0),(16,0,0)))
    lumps[6]=struct.pack('<16f2i',1,0,0,0,0,1,0,0,.0625,0,0,0,0,.0625,0,0,0,0)
    f=bytearray(56);struct.pack_into('<HBBihhhh',f,0,0,0,1,0,4,0,0 if displacement else -1,0)
    f[16:20]=bytes([0,255,255,255]);struct.pack_into('<if4i',f,20,0,256,0,0,1,1)
    lumps[58]=bytes(f);lumps[53]=bytes([64,32,16,0])*4
    lumps[12]=b''.join(struct.pack('<2H',i,(i+1)%4) for i in range(4));lumps[13]=struct.pack('<4i',0,1,2,3)
    lumps[14]=struct.pack('<9f3i',0,0,0,16,16,16,0,0,0,-1,0,1)
    leaf=bytearray(32);struct.pack_into('<6h',leaf,8,-100,-100,-100,100,100,100);lumps[10]=bytes(leaf)
    lumps[51]=struct.pack('<2H',1,0);lumps[55]=bytes([64,32,16,248])*6+bytes([128,128,128,0])
    lumps[43]=b'test/floor\0';lumps[44]=bytes(4)
    if displacement:
        d=bytearray(176);struct.pack_into('<3f3i',d,0,0,0,0,0,0,2);lumps[26]=bytes(d)
        lumps[33]=b''.join(struct.pack('<5f',0,0,1,4 if i==12 else 0,i*10) for i in range(25))
        lumps[48]=struct.pack('<32H',*([1]*32))
    data={'sp_hdr_0.vhv':vhv_fixture(),**(pak or {})};stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        for name,raw in data.items():z.writestr(name,raw)
    lumps[40]=stream.getvalue()
    out=bytearray(1036);out[:8]=b'VBSP\x14\0\0\0'
    for i,raw in enumerate(lumps):
        if raw:struct.pack_into('<4i',out,8+i*16,len(out),len(raw),1 if i in (10,55,58) else 0,0);out.extend(raw)
    sprp=struct.pack('<i128siHi',1,b'models/props/tiny.mdl',1,0,1)
    sprp+=struct.pack('<6f3H2Bi6f2H',32,0,0,0,90,0,0,0,1,6,0,1,100,200,0,0,0,1,0,0)
    dprp=struct.pack('<i128sii',1,b'models/props/tiny.mdl',0,1)
    detail=bytearray(52);struct.pack_into('<6f2H',detail,0,0,0,0,0,0,0,0,0);detail[28:32]=bytes([64,32,16,0]);struct.pack_into('<f',detail,48,1);dprp+=detail
    at=len(out);g=bytearray(struct.pack('<i',2));offset=at+36
    for tag,v,raw in ((b'prps',6,sprp),(b'prpd',4,dprp)):
        g.extend(struct.pack('<4sHHii',tag,0,v,offset,len(raw)));offset+=len(raw)
    g.extend(sprp+dprp);struct.pack_into('<4i',out,568,at,len(g),0,0);out.extend(g)
    return bytes(out)


def fixture(root):
    game=install_fixture(root/'install');out=root/'data'/'eye';import_catalog(game,out)
    p=game/'maps';p.mkdir()
    (p/'cc_test.bsp').write_bytes(bsp_fixture('''
      {"classname" "npc_test" "model" "models/props/tiny.mdl" "skin" "1" "OnUse" "a" "OnUse" "b"}
      {"classname" "npc_template_maker" "model" "models/props/tiny.mdl"}
      {"classname" "prop_dynamic" "model" "materials/models/props/tiny.mdl"}
      {"classname" "prop_dynamic" "model" "models/absent.mdl"}
      {"classname" "prop_ragdoll" "model" "models/props/tiny.mdl"}
      {"classname" "info_player_start" "origin" "0 0 4"}
    ''',True,{'materials/test/red.vtf':vtf_fixture(pixels=bytes(8))}))
    (game/'materials/test/floor.vmt').write_bytes(b'WorldVertexTransition { "$basetexture" "test/red" "$basetexture2" "test/red" }')
    return game,out


class EyeMapTests(unittest.TestCase):
    def test_entities_keep_duplicate_outputs_and_strict_errors(self):
        self.assertEqual(bsp.entities(b'{"OnUse" "a" "OnUse" "b\\\"}"}\0')[0],[('OnUse','a'),('OnUse','b"}')])
        for data in (b'{"k"}',b'{}junk',b'{"k" "a\0b"}',b'{{}}'):
            with self.assertRaises(ValueError):bsp.entities(data)

    def test_bsp_hdr_displacement_props_ambient_and_vhv(self):
        b=bsp.read_bsp(bsp_fixture(displacement=True));game,audit=bsp.game_lumps(b);props,skip=bsp.props(game)
        self.assertEqual([x['classname'] for x in props],['prop_static','prop_detail'])
        self.assertEqual(props[0]['skin'],1);self.assertEqual(props[0]['dx_levels'],[0,0])
        p,uv,lm,tri,flags,name=bsp.face_geometry(b,0);self.assertEqual((len(p),len(tri)),(25,32));self.assertEqual(p[12,2],4)
        cube,leaf=bsp.Ambient(b).cube(np.array([0,0,0]));np.testing.assert_allclose(cube[0],[.25,.125,.0625])
        tile=bsp.light_tile(b,b['faces'][0],0,1);self.assertEqual(tile.shape,(2,2,3))
        self.assertEqual(tile[0,0,0],round((64/255)**(1/2.2)*255))
        c=bsp.vhv(vhv_fixture(),123);np.testing.assert_allclose(c[0][0],(np.array([64,32,16])/255*2)**2.2)
        with self.assertRaisesRegex(ValueError,'checksum'):bsp.vhv(vhv_fixture(),999)
        with self.assertRaises(ValueError):bsp.read_bsp(bsp_fixture()[:1040])

    def test_source_face_side_and_face7_fallback(self):
        raw=bytearray(bsp_fixture());at=struct.unpack_from('<i',raw,8+58*16)[0];raw[at+2]=1
        raw[8+7*16:8+8*16]=raw[8+58*16:8+59*16];raw[8+58*16:8+59*16]=bytes(16)
        b=bsp.read_bsp(bytes(raw));p,uv,lm,tri,flags,name=bsp.face_geometry(b,0)
        self.assertTrue((np.cross(p[tri[:,1]]-p[tri[:,0]],p[tri[:,2]]-p[tri[:,0]])[:,2]<0).all())
        self.assertEqual(b['audit'][7]['name'],'faces')

    def test_fgd_npc_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'eye.fgd';p.write_text('@NPCClass base(Test) studio("models/props/tiny.mdl") = eye_actor : "Actor" []\n@PointClass studio("models/props/tiny.mdl") = item_test : "Pickup" []')
            d=maps.fgd_models(p);self.assertTrue(maps.classify({'classname':'eye_actor'},d)[0])
            self.assertFalse(maps.classify({'classname':'item_test'},d)[0])
            self.assertIn('Spawner',maps.classify({'classname':'npc_maker'},d)[1])
            self.assertFalse(maps.classify({'classname':'prop_ragdoll'},d)[0])

    def test_vhv_reorders_hardware_vertices_and_falls_back_atomically(self):
        colors=vhv_fixture();ambient=SimpleNamespace(cube=lambda origin:(np.ones((6,3))*.25,0))
        model={'_normals':np.tile([0,1,0],(6,1)),'metadata':{'checksum':123},'groups':[
            dict(source_bodypart=0,source_model=0,source_mesh=i,vertex_start=i*3,vertex_count=3) for i in range(2)]}
        record=dict(origin=[0,0,0],angles=[0,0,0],vhv='test.vhv')
        hardware=[((0,0,0),np.array([2,0,1])),((0,0,1),np.array([0,1,2]))]
        library=SimpleNamespace(hardware_maps=lambda m:hardware)
        raw=bytearray(colors);raw[96:108]=bytes([0,0,255,255,0,255,0,255,255,0,0,255])
        files={'test.vhv':bsp.EmbeddedAsset(bytes(raw),'test')}
        light,note=maps.instance_light(record,model,library,files,ambient)
        np.testing.assert_allclose(light[:3],np.array([[0,1,0],[0,0,1],[1,0,0]])*2**2.2)
        hardware[1]=((0,0,1),np.array([0,1,9]))
        light,note=maps.instance_light(record,model,library,files,ambient)
        np.testing.assert_allclose(light,.25)  # no partial VHV survives a later invalid mesh
        self.assertIn('outside model range',note['vhv_fallback'])
        empty=struct.pack('<6I',2,123,4,4,0,0)+bytes(16)
        light,note=maps.instance_light(record,model,library,{'test.vhv':bsp.EmbeddedAsset(empty,'test')},ambient)
        self.assertIn('Empty authored VHV',note['vhv_fallback'])
        directional=struct.pack('<6I',2,123,2,12,1,1)+bytes(16)+struct.pack('<3I',0,1,68)+bytes(16)+bytes([16,32,64,255])*3
        np.testing.assert_allclose(bsp.vhv(directional,123)[0][0],(np.array([64,32,16])/255*2)**2.2)

    def test_water_byte_tint_and_encoded_hdr_sky_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);game,models=fixture(root);files,_=read_install(game)
            files['materials/test/floor.vmt']=bsp.EmbeddedAsset(b'Water { "$refracttint" "{0 75 150}" }','test')
            for suffix in ('rt','lf','bk','ft','up','dn'):
                files['materials/skybox/test'+suffix+'.vmt']=bsp.EmbeddedAsset(b'UnlitGeneric { "$hdrcompressedtexture" "encoded/mantissa" }','test')
            scene,_,images,_,_=maps.build_map(bsp_fixture(),'cc_test',files,maps.ModelLibrary(models,files),{})
            water=scene['groups'][0]
            self.assertEqual(images[water['texture']].getpixel((0,0)),(0,75,150,96))
            self.assertTrue(water['settings']['blend'])
            self.assertEqual({m['texture'] for m in scene['skybox']['faces'].values()},{'eye_sky_plain.png'})
            self.assertFalse(scene['missing_reasons'])

    def test_instance_library_aliases_pak_namespaces_and_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);game,models=fixture(root);output=root/'data'/'eye-maps'
            before={p.name:p.read_bytes() for p in (models/'model_json').iterdir()}
            report=maps.import_maps(game,output)
            self.assertFalse(report['failed'],report);self.assertEqual(report['imported'],['cc_test'])
            scene=json.loads((output/'maps/cc_test/scene.json').read_text());self.assertEqual(scene['npcs'],1)
            self.assertEqual(len(scene['instances']),5);self.assertEqual(len(scene['model_failures']),1)
            self.assertEqual(scene['vertices'],25)  # models are not in world geometry
            self.assertEqual(scene['instances'][0]['lighting']['method'],'VHV2 BGRA via VTX originals')
            self.assertEqual(len({m['geometry']['file'] for m in scene['model_library'].values()}),1)
            self.assertTrue(scene['groups'][0]['texture'].startswith(maps.key('cc_test')+'_'))
            self.assertIn('texture2',scene['groups'][0])
            self.assertEqual(before,{p.name:p.read_bytes() for p in (models/'model_json').iterdir()})
            import app
            with patch.object(app,'local_data_dir',root/'data'),patch.dict(app.pack_dirs,eye=models):
                c=app.app.test_client()
                with c.get('/maps/eye/') as r:self.assertEqual(r.status_code,200);self.assertIn(b'npc-toggle.js',r.data)
                filename=next(iter(scene['model_library'].values()))['geometry']['file']
                with c.get('/eye-map-data/models/'+filename) as r:self.assertEqual(r.data,before[filename])
                tex=scene['groups'][0]['texture']
                with c.get('/eye-map-data/textures/'+tex) as r:self.assertEqual(r.status_code,200);self.assertEqual(r.mimetype,'image/vnd-ms.dds')
                with c.get('/eye-map-data/models/import-report.json') as r:self.assertEqual(r.status_code,404)
                with patch('app.import_eye_maps',return_value={'imported':['cc_test']}) as importer:
                    self.assertEqual(c.post('/import_eye_maps',json={'path':str(game)}).status_code,200)
                    importer.assert_called_once()

    def test_out_of_range_skin_draws_skin_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'model-index.json').write_text(json.dumps({'models/a.mdl':{'looks':['a_skin0','a_skin1']}}))
            library=maps.ModelLibrary(root,{});library.cache.update(a_skin0='zero',a_skin1='one')
            self.assertEqual(library.get('models/a.mdl',1),('a_skin1','one'))
            self.assertEqual(library.get('models/a.mdl',5),('a_skin0','zero'))
            with self.assertRaises(ValueError):library.get('models/a.mdl',-1)

    def test_pak_overrides_loose_and_escape_rejected(self):
        b=bsp.read_bsp(bsp_fixture(pak={'materials/a.vmt':b'embedded'}))
        files,audit=bsp.pak_files(b,{'materials/a.vmt':bsp.EmbeddedAsset(b'loose','loose')})
        self.assertEqual(files['materials/a.vmt'].read(),b'embedded')
        with self.assertRaises(ValueError):bsp.pak_files(bsp.read_bsp(bsp_fixture(pak={'../escape':b'bad'})),{})


if __name__=='__main__':unittest.main()

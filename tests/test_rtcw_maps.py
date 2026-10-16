"""RTCW map fixtures are invented bytes in plain tempfile directories."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from app import app
from tools import import_rtcw_map as maps
from tools import import_q3_map as q3
from tests.test_rtcw import mdc, pk3, image_bytes
from tests.test_q3 import md3


def bsp(entities='', version=47, patch_surface=False):
    lumps=[b'']*17
    lumps[0]=entities.encode()
    lumps[1]=struct.pack('<64sii',b'textures/test/wall',0,1)
    planes=[(1,0,0,100),(-1,0,0,100),(0,1,0,100),(0,-1,0,100),(0,0,1,0),(0,0,-1,16)]
    lumps[2]=b''.join(struct.pack('<4f',*p) for p in planes)
    lumps[7]=struct.pack('<6f4i',-100,-100,-16,100,100,100,0,1,0,1)+struct.pack('<6f4i',0,0,0,32,8,48,1,1,1,0)
    lumps[8]=struct.pack('<3i',0,6,0);lumps[9]=b''.join(struct.pack('<2i',i,0) for i in range(6))
    points=[(0,0,0),(32,0,0),(0,32,0)]
    if patch_surface:points=[(x,y,(x==1)*4) for y in range(3) for x in range(3)]
    count=len(points);points += [(0,0,0),(32,0,0),(0,8,0)]
    lumps[10]=b''.join(struct.pack('<10f4B',*p,0,0,.5,.5,0,0,1,32,64,96,255) for p in points)
    lumps[11]=struct.pack('<6i',0,1,2,0,1,2)
    def face(first,count,start,kind):return struct.pack('<12i12f2i',0,-1,kind,first,count,start,3,0,0,0,128,128,*([0]*12),3 if kind==2 else 0,3 if kind==2 else 0)
    lumps[13]=face(0,count,0,2 if patch_surface else 1)+face(count,3,3,1)
    lumps[14]=bytes([32,64,96])*16384
    head=b'IBSP'+struct.pack('<i',version);body=b''
    for lump in lumps:head+=struct.pack('<2i',144+len(body),len(lump));body+=lump
    return head+body


def entity(**values):
    return '{ '+ ' '.join('"'+k+'" "'+str(v)+'"' for k,v in values.items())+' }\n'


class RTCWMapTests(unittest.TestCase):
    def test_unused_patch_indices_and_unlightmapped_nan_are_accepted(self):
        raw=bytearray(bsp(patch_surface=True));indices=struct.unpack_from('<i',raw,8+11*8)[0]
        faces=struct.unpack_from('<i',raw,8+13*8)[0];vertices=struct.unpack_from('<i',raw,8+10*8)[0]
        struct.pack_into('<i',raw,indices,1098907648)
        struct.pack_into('<i',raw,faces+7*4,-1)
        struct.pack_into('<f',raw,vertices+6*4,float('nan'))
        struct.pack_into('<f',raw,faces+104+15*4,float('nan')) # Unused planar lightmap vector.
        notes=[];lumps=maps.read_bsp(raw,notes)
        self.assertEqual(struct.unpack_from('<f',lumps[10],6*4)[0],0)
        self.assertEqual(struct.unpack_from('<f',lumps[13],104+15*4)[0],0)
        self.assertEqual(len(notes),3)
        bad=bytearray(raw);struct.pack_into('<i',bad,faces+7*4,0)
        with self.assertRaisesRegex(ValueError,'non-finite lightmap'):maps.read_bsp(bad)
        bad=bytearray(raw);struct.pack_into('<f',bad,vertices,float('nan'))
        with self.assertRaisesRegex(ValueError,'Non-finite'):maps.read_bsp(bad)
        bad=bytearray(raw);struct.pack_into('<f',bad,faces+104+21*4,float('nan'))
        with self.assertRaisesRegex(ValueError,'non-finite consumed'):maps.read_bsp(bad)
        bad=bytearray(raw);struct.pack_into('<i',bad,indices+3*4,999)
        with self.assertRaisesRegex(ValueError,'invalid draw vertex index'):maps.read_bsp(bad)
        nodraw=bytearray(raw);shaders=struct.unpack_from('<i',raw,8+8)[0]
        struct.pack_into('<i',nodraw,shaders+64,q3.SURF_NODRAW)
        notes=[];maps.read_bsp(nodraw,notes)
        self.assertTrue(any(n.get('action')=='not drawn' and n['surface']==0 for n in notes))

    def test_bsp46_default_unchanged_and_47_validated(self):
        raw=bsp(patch_surface=True)
        with self.assertRaisesRegex(ValueError,'not 46'):q3.read_bsp(raw)
        lumps=maps.read_bsp(raw)
        self.assertEqual(len(lumps[10]),12*44)
        with self.assertRaisesRegex(ValueError,'not 47'):maps.read_bsp(bsp(version=46))
        bad=bytearray(raw);struct.pack_into('<i',bad,12+9*8,7)
        with self.assertRaisesRegex(ValueError,'partial'):maps.read_bsp(bad)
        bad=bytearray(raw);offset=struct.unpack_from('<i',raw,8+13*8)[0];struct.pack_into('<i',bad,offset+12,10000)
        with self.assertRaisesRegex(ValueError,'surface vertices'):maps.read_bsp(bad)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'b.bsp';q3.write_bsp(out,lumps)
            self.assertEqual(struct.unpack_from('<i',out.read_bytes(),4)[0],46)

    def test_hull_floor_spawn_flags_and_mover_transforms(self):
        lumps=maps.read_bsp(bsp());collision=maps.Collision(lumps,{})
        views=maps.viewpoints([dict(classname='info_player_start',origin='0 0 100',angle='90')],collision,'sp')
        self.assertAlmostEqual(views[0]['origin'][2],64.125)
        self.assertEqual(views[0]['yaw'],90)
        model=struct.unpack_from('<6f4i',lumps[7],40)
        door=maps.brush_state(dict(classname='func_door',spawnflags='1',angle='0',origin='10 0 0'),model,[])
        self.assertEqual(door['origin'],[34,0,0])
        rotated=maps.brush_state(dict(classname='func_static',origin='10 20 0',angles='0 90 0'),model,[])
        point=maps.transformed([2,0,0],rotated)
        self.assertAlmostEqual(point[0],10);self.assertAlmostEqual(point[1],22)
        rotated['model2']='models/replacement.md3'
        replacement=maps.brush_geometry(lumps,{1:rotated})
        self.assertEqual(struct.unpack_from('<i',replacement[13],104+8)[0],0)
        self.assertTrue(maps.brush_state(dict(classname='func_explosive',spawnflags='1'),model,[])['hidden'])
        bench=maps.brush_state(dict(classname='props_bench',model='*1'),model,[])
        self.assertEqual(bench['model2'],'models/furniture/bench/bench_sm.md3')
        self.assertEqual(maps.brush_state(dict(classname='func_bobbing',phase='.25',height='20'),model,[])['origin'],[0,0,-20])
        self.assertEqual(maps.brush_state(dict(classname='func_pendulum',phase='.25'),model,[])['angles'],[0,0,-30])
        curved=maps.Collision(maps.read_bsp(bsp(patch_surface=True)),{})
        floor,why=curved.drop([0,0,100],maps.PLAYER_MINS,maps.PLAYER_MAXS)
        self.assertAlmostEqual(floor[2],26.125);self.assertIn('patch hull',why)
        # The hull catches an edge even when its centre ray misses the triangle.
        self.assertEqual(maps.Collision.triangle_drop([(10,0,20),(20,0,20),(10,10,20)],[0,0,100],(-18,-18,-24),(18,18,48)),56)
        self.assertIsNone(maps.Collision.triangle_drop([(30,0,20),(40,0,20),(30,10,20)],[0,0,100],(-18,-18,-24),(18,18,48)))
        self.assertTrue(maps.filtered(dict(notsingle='1'),'sp'));self.assertTrue(maps.filtered(dict(notteam='1'),'mp'))
        mp=maps.viewpoints([dict(classname='team_CTF_redplayer',spawnflags='4',origin='1 0 100'),
                           dict(classname='team_CTF_redplayer',origin='2 0 100'),dict(classname='team_CTF_blueplayer',origin='3 0 100')],collision,'mp')
        self.assertEqual([v['origin'][0] for v in mp],[2,3])

    def test_gamma_texture_path_and_stage_keywords(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            script=b'''textures/test/masked
{
 fogparms ( 1 1 1 ) 100
 skyfogvars ( 1 1 1 ) .5
 waterfogvars ( 1 1 1 ) .5
 nofog
 allowcompress
 nocompress
 { map textures/test/wall.tga
   alphaFunc GE128
   rgbGen identityLighting
 }
}
textures/test/glass
{
 { map textures/test/wall.tga
   blendFunc blend
 }
}
textures/test/sky
{
 skyparms env/test 512 -
 sunshader sun
}
'''
            pk3(root/'pak0.pk3',[('scripts/test.shader',script),('textures/test/wall.jpg',image_bytes())]+[(f'env/test_{side}.tga',image_bytes()) for side in q3.SKY_SIDES])
            (root/'wolfconfig.cfg').write_text('seta r_ignorehwgamma "1"\nseta r_gamma "1.3"\nseta r_overBrightBits "1"\n')
            game=maps.Game(root);textures=maps.Textures(root/'textures_out',game)
            self.assertEqual((game.lighting['overbrightBits'],game.lighting['identityLight'],game.lighting['lightScale'],game.lighting['displayScale']),(0,1,4,1))
            shader,missing,limits=maps.describe(game,'textures/test/masked',textures)
            self.assertFalse(missing);self.assertEqual(shader['stages'][0]['alphaFunc'],'ge128');self.assertIn('sky and underwater fog not drawn',limits)
            filename=textures.resolve(game,r'textures\test\wall.psd')
            with Image.open(textures.output/filename) as image:
                self.assertEqual(image.size,(1,1));self.assertGreater(image.getpixel((0,0))[0],20);self.assertEqual(image.getpixel((0,0))[3],255)
            glass=maps.describe(game,'textures/test/glass',textures)[0]
            self.assertEqual(glass['stages'][0]['blend'],['gl_src_alpha','gl_one_minus_src_alpha'])
            sky=maps.describe(game,'textures/test/sky',textures)[0]
            self.assertEqual(len(sky['sky']['box']),6)
            self.assertTrue(maps.describe(game,'textures/elsewhere/wall',textures)[1])  # No same-name guesses.

    def test_weapon_optional_pickup_mesh_falls_back_to_world_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            pk3(root/'pak0.pk3',[('models/weapons2/luger/luger.mdc',mdc())])
            game=maps.Game(root)
            path,sideways=maps.pickup_model(game,maps.ITEMS['sp']['weapon_luger'])
            self.assertEqual(path,'models/weapons2/luger/luger.md3');self.assertTrue(sideways)
            pk3(root/'sp_pak1.pk3',[('models/weapons2/luger/pu_luger.mdc',mdc())])
            path,sideways=maps.pickup_model(maps.Game(root),maps.ITEMS['sp']['weapon_luger'])
            self.assertEqual(path,'models/weapons2/luger/pu_luger.md3');self.assertFalse(sideways)

    def test_weapon_stand_tag_uses_item_frame_without_animating_mesh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            stand=bytearray(md3('tag_stand2'));tags=struct.unpack_from('<i',stand,96)[0]
            struct.pack_into('<3f',stand,tags+64,7,8,9)
            text=entity(classname='worldspawn')+entity(classname='weapon_luger',origin='0 0 80',frame='2',spawnflags='1')
            pk3(root/'pak0.pk3',[('maps/stand.bsp',bsp(text)),('models/weapons2/luger/luger.md3',md3()),
                ('models/weapons2/luger/luger_stand.md3',bytes(stand)),('models/test/skin.tga',image_bytes()),('textures/test/wall.tga',image_bytes())])
            result=maps.import_maps(root,out)
            self.assertFalse(result['failed'])
            scene=json.loads((out/'maps/sp_stand/scene.json').read_text());self.assertEqual(len(scene['placed']),2)
            weapon=scene['placed'][0];self.assertEqual(weapon['scale'],[1.5]*3)
            mesh=json.loads((out/scene['itemModels'][weapon['models'][0]]['file']).read_text())[0]
            self.assertEqual(mesh['vertices'][:3],[7,8,9])

    def test_tag_only_stand_is_transform_not_missing_weapon(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            stand=bytearray(md3('tag_stand'));end=struct.unpack_from('<i',stand,100)[0]
            struct.pack_into('<i',stand,84,0);struct.pack_into('<i',stand,104,end);stand=stand[:end]
            text=entity(classname='worldspawn')+entity(classname='weapon_luger',origin='0 0 80',spawnflags='1')
            pk3(root/'pak0.pk3',[('maps/stand.bsp',bsp(text)),('models/weapons2/luger/luger.md3',md3()),
                ('models/weapons2/luger/luger_stand.md3',bytes(stand)),('models/test/skin.tga',image_bytes()),('textures/test/wall.tga',image_bytes())])
            maps.import_maps(root,out)
            scene=json.loads((out/'maps/sp_stand/scene.json').read_text())
            self.assertEqual(len(scene['placed']),1);self.assertEqual(scene['entityResults'][1]['status'],'drawn')
            shader=next(iter(scene['itemModels'].values()))['shaders'][0]
            self.assertFalse(shader.get('default'));self.assertTrue((out/'textures'/shader['stages'][0]['map']).is_file())

    def test_map_model_texture_content_name_and_cutout_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            image=Image.new('RGBA',(2,1));image.putdata([(10,20,30,0),(40,50,60,255)])
            encoded=io.BytesIO();image.save(encoded,format='TGA')
            pk3(root/'pak0.pk3',[('models/test/diffuse.tga',encoded.getvalue()),('scripts/tree.shader',b'models/tree\n{\n { map models/test/diffuse.tga\n alphaFunc GE128\n }\n}')])
            game=maps.Game(root);textures=maps.ModelTextures(game,root/'textures')
            name,warnings,settings=textures.resolve('models/tree')
            self.assertTrue((textures.output/name).is_file());self.assertEqual(settings['alphaFunc'].lower(),'ge128')
            self.assertEqual(textures.resolve('models/tree')[0],name)
            self.assertEqual(textures.results['models/tree']['status'],'resolved')
            with Image.open(textures.output/name) as decoded:self.assertEqual(decoded.getchannel('A').getextrema(),(0,255))
            self.assertEqual(textures.resolve(r'models\test\diffuse.tga')[0],name) # Embedded MG42-style path.
            missing,warnings,_=textures.resolve('models/missing')
            self.assertFalse((textures.output/missing).exists());self.assertTrue(warnings)
            self.assertEqual(textures.results['models/missing']['status'],'unresolved')

    def test_startsolid_item_is_omitted_in_sp_and_mp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            text=entity(classname='worldspawn')+entity(classname='item_health',origin='0 0 100')
            pk3(root/'pak0.pk3',[('maps/start.bsp',bsp(text)),('textures/test/wall.tga',image_bytes())])
            pk3(root/'mp_pak0.pk3',[('maps/mp_test.bsp',bsp(text))])
            with patch.object(maps.Collision,'drop',return_value=([0,0,999],'startsolid')) as drop:
                maps.import_maps(root,out)
            self.assertEqual(drop.call_args_list[1].args[0],[0,0,90]) # Retry from authored origin, not trace end.
            for ident in ('sp_start','mp_mp_test'):
                scene=json.loads((out/'maps'/ident/'scene.json').read_text())
                self.assertEqual(scene['entityResults'][1]['status'],'omitted')
                self.assertIn('frees entity',scene['entityResults'][1]['reason']);self.assertFalse(scene['placed'])

    def test_synthetic_import_every_map_entities_and_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            text=entity(classname='worldspawn',message='Fixture')+entity(classname='info_player_start',origin='0 0 100')
            text+=entity(classname='func_static',model='*1',spawnflags='1')
            text+=entity(classname='misc_gamemodel',model='models/prop.md3',origin='1 2 3',angles='0 90 0',modelscale_vec='2 3 4',frame='1',skin='models/prop.skin')
            text+=entity(classname='misc_model',model='models/prop.md3')+entity(classname='ai_soldier',model='models/prop.md3')
            text+=entity(classname='item_health',origin='0 0 80',spawnflags='2')
            text+=entity(classname='weapon_luger',origin='0 0 80',targetname='later')
            text+=entity(classname='misc_gamemodel',model='models/absent.md3')
            text+=entity(classname='misc_gamemodel',model='models/prop.md3',notsingle='1')
            pk3(root/'pak0.pk3',[('maps/start.bsp',bsp(text,patch_surface=True)),('maps/cutscene1.bsp',b'bad'),
                ('textures/test/wall.tga',image_bytes()),('models/test/diffuse.tga',image_bytes()),('models/prop.mdc',mdc()),
                ('models/prop.skin',b'part,models/test/diffuse'),('models/powerups/health/health_m.mdc',mdc())])
            pk3(root/'sp_pak4.pk3',[('maps/start.bsp',bsp(text,patch_surface=True))])
            pk3(root/'mp_pak0.pk3',[('maps/mp_test.bsp',bsp(entity(classname='worldspawn')+entity(classname='team_CTF_redplayer',origin='0 0 100')))])
            done=maps.import_maps(root,out)
            self.assertEqual(done['imported'],['sp_start','mp_mp_test']);self.assertIn('sp_cutscene1',done['failed'])
            scene=json.loads((out/'maps/sp_start/scene.json').read_text())
            self.assertEqual(scene['source'],'sp_pak4.pk3');self.assertEqual(len(scene['placed']),2)
            prop,pickup=scene['placed'];self.assertEqual(prop['scale'],[2,3,4]);self.assertTrue(pickup['spin']);self.assertAlmostEqual(pickup['origin'][2],2.125)
            prop_mesh=json.loads((out/scene['itemModels'][prop['models'][0]]['file']).read_text())[0]
            self.assertAlmostEqual(prop_mesh['vertices'][0],.05);self.assertAlmostEqual(prop_mesh['vertices'][2],-.1)
            for placed in scene['placed']:
                for key in placed['models']:
                    definition=scene['itemModels'][key]
                    self.assertTrue((out/definition['file']).is_file())
                    for shader in definition['shaders']:
                        for stage in shader.get('stages',[]):self.assertTrue((out/'textures'/stage['map']).is_file())
            exported=q3.read_bsp((out/scene['bsp']).read_bytes(),version=47)
            self.assertEqual(struct.unpack_from('<i',exported[13],104+8)[0],0)  # Hidden inline surface.
            self.assertEqual(sum(r['status']=='failed' for r in scene['entityResults']),2) # Broken prop + NPC without its character skins.
            self.assertEqual(next(r for r in scene['entityResults'] if r['classname']=='ai_soldier')['status'],'failed')
            with patch('app.local_data_dir',root),patch('app.import_rtcw_maps',return_value=done) as importer:
                client=app.test_client();page=client.get('/maps/rtcw/')
                self.assertEqual(page.status_code,200);self.assertIn(b'Fog and AI',page.data);self.assertIn(b'/static/rtcw-maps/viewer.js',page.data)
                self.assertEqual(client.post('/import_rtcw_maps',json={}).status_code,400)
                self.assertEqual(client.post('/import_rtcw_maps',json={'game':str(root)},headers={'Origin':'https://example.com'}).status_code,403)
                self.assertEqual(client.post('/import_rtcw_maps',json={'game':str(root)}).status_code,200);importer.assert_called_once()
                self.assertEqual(client.get('/rtcw-map-data/../../README.md').status_code,404)
            again=maps.import_maps(root,out);self.assertEqual(again['skipped'],['sp_start','mp_mp_test'])


if __name__=='__main__':unittest.main()

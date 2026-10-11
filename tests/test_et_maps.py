"""Invented ET map fixtures only. No retail imports in the test suite."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from tools import import_et_map as et
from tests.test_rtcw_maps import bsp,entity
from tests.test_rtcw import pk3,image_bytes
from tests.test_q3 import md3


SCRIPT=b'''bridge {
 spawn { wait 100
 constructible_class 2
 remapshader textures/old textures/new
 remapshaderflush
 trigger self setup
 }
 trigger setup { setstate bridge invisible
 setstate pile default
 }
 built stage1 { setstate pile invisible }
 built final { setstate bridge default
 setstate pile invisible
 trigger gun show
 }
}
gun { spawn { setstate gun invisible }
 trigger show { setstate gun default }
}
destroyed { spawn { setstate destroyed invisible } }
removed { spawn { remove } }
manager { spawn { wait 50
 setautospawn AxisHome 0
} }
'''


class ETMapTests(unittest.TestCase):
    def test_spawn_constructible_final_stage_linked_entities_and_exclusions(self):
        things=[dict(classname='worldspawn'),dict(classname='func_constructible',model='*1',scriptname='bridge',targetname='bridge',spawnflags='4',constages='stage1;'),
            dict(classname='misc_gamemodel',targetname='pile'),dict(classname='misc_mg42',targetname='gun',scriptname='gun'),
            dict(classname='func_explosive',targetname='destroyed',scriptname='destroyed'),
            dict(classname='func_brushmodel',targetname='stage1'),dict(classname='func_constructible',scriptname='removed',spawnflags='4')]
        program=et.scripts(SCRIPT)
        spawn,_=et.spawn_states(things,program);built,report=et.spawn_states(things,program,True)
        self.assertTrue(et.filtered(spawn[1],'et'));self.assertFalse(et.filtered(spawn[2],'et'))
        self.assertFalse(et.filtered(built[1],'et'));self.assertTrue(et.filtered(built[2],'et'))
        self.assertFalse(et.filtered(built[3],'et'));self.assertTrue(et.filtered(built[4],'et'))
        self.assertTrue(et.filtered(built[5],'et'));self.assertTrue(et.filtered(built[6],'et'))
        self.assertEqual({r['entity'] for r in report['changes']},{1,2,3})
        self.assertEqual(report['shaderRemaps'],{'textures/old':'textures/new'})
        self.assertTrue(et.filtered(dict(notteam='1'),'et'))
        self.assertEqual(things[1]['model'],'*1')

    def test_foliage_instances_color_and_bad_ranges(self):
        lumps=et.qmap.read_bsp(bsp(),version=47)
        vertices=bytearray(lumps[10][:3*44])
        for x,color in ((100,(12,34,56,78)),(200,(34,56,78,90))):vertices.extend(struct.pack('<10f4B',x,10,20,*([0]*7),*color))
        # Only world face: three base vertices followed by two instance origins/colors.
        lumps[7]=struct.pack('<6f4i',0,0,0,500,500,500,0,1,0,1)
        face=list(struct.unpack_from('<12i12f2i',lumps[13]));face[2]=5;face[4]=5;face[24:]=[2,3]
        lumps[10]=bytes(vertices);lumps[13]=struct.pack('<12i12f2i',*face)
        notes=[];out=et.read_bsp(et.bsp_bytes(lumps),notes);draw=struct.unpack_from('<12i12f2i',out[13])
        self.assertEqual(draw[2],3);self.assertEqual(draw[4],6);self.assertEqual(draw[7],-3)
        first=struct.unpack_from('<10f4B',out[10],draw[3]*44)
        self.assertEqual(first[:3],(100,10,20));self.assertEqual(first[10:],(12,34,56,78))
        self.assertEqual(notes[0]['instances'],2)
        face[25]=99;lumps[13]=struct.pack('<12i12f2i',*face)
        with self.assertRaisesRegex(ValueError,'foliage'):et.read_bsp(et.bsp_bytes(lumps))

    def test_external_lightmaps_native_size_lighting_and_shader_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out';(out/'textures').mkdir(parents=True)
            raw=io.BytesIO();Image.new('RGB',(256,256),(32,64,96)).save(raw,'TGA')
            pk3(root/'pak0.pk3',[('maps/test/lm_0000.tga',raw.getvalue()),('textures/test/wall.tga',image_bytes()),('scripts/et.shader',b'textures/test/wall { implicitMask - }')])
            (root/'etconfig.cfg').write_text('seta r_ignorehwgamma "1"\nseta r_gamma "1.3"\nseta cg_fov "100"')
            game=et.Game(root);lumps=et.qmap.read_bsp(bsp(),version=47);lumps[14]=b''
            atlas,missing=et.lightmap_atlas(game,'test',lumps,out)
            self.assertFalse(missing);self.assertEqual(atlas['size'],256);self.assertEqual(atlas['valid'],[0])
            with Image.open(out/'textures'/atlas['file']) as im:self.assertEqual(im.getpixel((0,0)),(85,170,255))
            self.assertFalse(game.lighting['hardwareGamma']);self.assertEqual(game.lighting['displayGamma'],1);self.assertEqual(game.lighting['fov'],100)
            shader,missing,notes=et.describe(game,'textures/test/wall',et.base.Textures(out/'textures',game))
            self.assertEqual(shader['implicitType'],'implicitmask');self.assertFalse(missing);self.assertFalse(notes)

    def test_nested_trigger_wait_replaces_parent_and_spline_setup(self):
        things=[dict(classname='misc_gamemodel',scriptname='mover',targetname='mover',origin='500 500 0'),
            dict(classname='info_train_spline_main',targetname='a',target='b',origin='0 0 0'),
            dict(classname='info_train_spline_main',targetname='b',origin='100 0 0')]
        program=et.scripts(b"""mover { spawn { accum 0 set -1
 accum 0 abort_if_equal 0
 trigger self setup
 setstate mover invisible
 }
 trigger setup { wait 50
 followspline 0 a 1000 wait length 32
 setstate mover default
 }
 }""")
        prepared,report=et.spawn_states(things,program)
        self.assertEqual(prepared[0]['_state'],'default')
        self.assertEqual(et.vector(prepared[0]['origin']),[100,0,0])
        self.assertEqual(et.vector(prepared[0]['angles']),[0,0,0])
        self.assertIn('startup spline completed',[n['reason'] for n in report['scriptNotes']])

    def test_linear_path_linked_panels_and_marker_initialization(self):
        things=[dict(classname='script_mover',scriptname='door',targetname='door',origin='999 999 999'),
            dict(classname='script_mover',scriptname='panel',targetname='panel',origin='888 888 888'),
            dict(classname='info_train_spline_main',targetname='a',target='b',origin='0 0 0'),
            dict(classname='info_train_spline_main',targetname='b',target='c',origin='100 0 0'),
            dict(classname='info_train_spline_main',targetname='c',origin='200 0 0'),
            dict(classname='script_mover',scriptname='lever',origin='0 0 0'),dict(classname='path_corner',targetname='up',origin='0 0 10')]
        program=et.scripts(b"""door { spawn { followpath 1 a 200 wait length 16 } }
 panel { spawn { attatchtotrain door 16 } }
 lever { spawn { gotomarker up 10 } }""")
        things,report=et.spawn_states(things,program);et.position_trains(things,report['scriptNotes'])
        self.assertEqual(et.vector(things[0]['origin']),[0,0,0]);self.assertEqual(et.vector(things[1]['origin']),[16,0,0])
        self.assertAlmostEqual(abs(et.vector(things[1]['angles'])[1]),180)
        self.assertEqual(et.vector(things[5]['origin']),[0,0,10])
        self.assertFalse(any('unresolved' in n['reason'] for n in report['scriptNotes']))

    def test_contested_constructibles_do_not_overlap_or_rebuild_existing_owner(self):
        things=[dict(classname='func_constructible',targetname='shared',spawnflags=str(f)) for f in (8,4)]
        built,report=et.spawn_states(things,{},True)
        self.assertTrue(et.filtered(built[0],'et'));self.assertFalse(et.filtered(built[1],'et'))
        things[0]['spawnflags']='9'
        built,_=et.spawn_states(things,{},True)
        self.assertFalse(et.filtered(built[0],'et'));self.assertTrue(et.filtered(built[1],'et'))

    def test_attachment_frame_and_mounted_components_reuse_exported_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            for folder in ('models','textures'):(out/folder).mkdir(parents=True)
            pk3(root/'pak0.pk3',[
                ('models/tank.md3',md3('tag_player')),('models/mapobjects/tanks_sd/mg42nestbase.md3',md3('tag_mg42nest')),
                ('models/mapobjects/tanks_sd/mg42nest.md3',md3('tag_mg42')),('models/mapobjects/tanks_sd/mg42.md3',md3()),
                ('models/test/skin.tga',image_bytes())])
            game=et.Game(root);game.state_report={'scriptNotes':[]}
            things=[dict(classname='script_mover',spawnflags='128',model='models/tank.md3',targetname='tank',origin='10 20 30',angles='0 90 0',frame='1'),
                dict(classname='misc_gamemodel',model='models/tank.md3',_attachment=['tank','tag_player'])]
            et.position_attachments(game,things)
            pos=et.vector(things[1]['origin']);self.assertAlmostEqual(pos[0],10);self.assertAlmostEqual(pos[1],22);self.assertAlmostEqual(pos[2],30)
            game.prepared=things
            scene=dict(placed=[dict(entity=0,origin=[10,20,30],angles=[0,90,0],scale=[1,1,1],models=[])],itemModels={},unresolved=[],entityResults=[])
            et.add_components(game,scene,out,et.base.Textures(out/'textures',game),et.ModelTextures(game,out/'textures'))
            self.assertEqual(len(scene['placed']),4);self.assertFalse(scene['unresolved'])
            for model in scene['itemModels'].values():
                self.assertTrue((out/model['file']).is_file())
                self.assertTrue((out/'textures'/model['shaders'][0]['stages'][0]['map']).is_file())

    def test_terrain_lightmap_keyword_and_vertex_alpha(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'textures';out.mkdir()
            pk3(root/'pak0.pk3',[('textures/terrain.tga',image_bytes()),('scripts/test.shader',b"""textures/terrain {
 { map textures/terrain.tga
 rgbGen vertex
 tcGen vector ( 1 0 0 ) ( 0 1 0 )
 }
 { lightmap $lightmap
 blendfunc filter
 }
 }""")])
            game=et.Game(root);game.remaps={'textures/alias':'textures/terrain'};shader,missing,limits=et.describe(game,'textures/alias',et.base.Textures(out,game))
            self.assertFalse(missing);self.assertEqual(len(shader['stages']),2)
            self.assertEqual(shader['stages'][1]['map'],'$lightmap')
            self.assertEqual(shader['stages'][0]['alphaGen'],['vertex'])
            self.assertEqual(shader['stages'][0]['tcGenVectors'],[1,0,0,0,1,0])
            self.assertNotIn('texture coordinates from vectors',limits)

    def test_synthetic_import_both_states_routes_and_references(self):
        from app import app
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'et-maps'
            text='\n'.join([entity(classname='worldspawn',message='Synthetic ET'),entity(classname='func_constructible',model='*1',scriptname='bridge',targetname='bridge',spawnflags='4'),
                entity(classname='script_multiplayer',scriptname='manager'),entity(classname='team_CTF_redspawn',origin='0 0 100',spawnflags='2'),entity(classname='team_CTF_redspawn',origin='80 0 100',spawnflags='2'),
                entity(classname='team_WOLF_objective',description='AxisHome',origin='80 0 100'),entity(classname='misc_gamemodel',model='models/prop.md3',targetname='pile')])
            pk3(root/'pak0.pk3',[('maps/test.bsp',bsp(text)),('maps/test.script',SCRIPT),('models/prop.md3',md3()),('models/test/skin.tga',image_bytes()),('textures/test/wall.tga',image_bytes()),('maps/bad.bsp',b'bad')])
            with patch('app.local_data_dir',root):
                client=app.test_client();r=client.post('/import_et_maps',json={'game':str(root)})
                self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['imported'],['test']);self.assertIn('bad',r.json['failed'])
                page=client.get('/maps/et/').get_data(as_text=True);self.assertIn('id="built"',page);self.assertIn('/static/et-maps/viewer.js',page)
                self.assertNotIn('id="built"',client.get('/maps/rtcw/').get_data(as_text=True))
                self.assertEqual(client.post('/import_et_maps',json={'game':str(root)},headers={'Origin':'https://outside'}).status_code,403)
                self.assertEqual(client.get('/et-map-data/../../app.py').status_code,404)
            spawn=json.loads((out/'maps/test/scene.json').read_text());built=json.loads((out/'maps/test/scene-built.json').read_text())
            self.assertEqual(spawn['viewpoints'][0]['entity'],4)
            self.assertAlmostEqual(spawn['viewpoints'][0]['origin'][2],64.125)
            for scene in (spawn,built):
                self.assertTrue((out/scene['bsp']).is_file())
                for placed in scene['placed']:
                    for key in placed['models']:self.assertTrue((out/scene['itemModels'][key]['file']).is_file())
                for model in scene['itemModels'].values():
                    for shader in model['shaders']:
                        for stage in shader.get('stages',[]):
                            if not stage['map'].startswith('$'):self.assertTrue((out/'textures'/stage['map']).is_file())
            a=et.qmap.read_bsp((out/spawn['bsp']).read_bytes(),version=47);b=et.qmap.read_bsp((out/built['bsp']).read_bytes(),version=47)
            self.assertEqual(struct.unpack_from('<i',a[13],112)[0],0);self.assertEqual(struct.unpack_from('<i',b[13],112)[0],1)
            self.assertEqual(len(spawn['placed']),1);self.assertEqual(len(built['placed']),0)


if __name__=='__main__':unittest.main()

"""Bounded Raven map fixtures; no retail assets, game or GPU required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

from tools import import_doom3_map as maps, quake4_policy as q4
from tools.doom3_assets import Install, Declarations, Textures, Materials, lex
from tools.doom3_map_materials import compile_stage, expression
from tools.doom3_map_sounds import Sounds
from tools.quake4_map_policy import prepare_entities, attachment_transforms, transformed_light
from tests.test_doom3 import MESH, ANIM


PROC=b'''PROC "4" 4294967295
model { "_area0" 1 1 { "textures/test" 3 3
( 0 0 0 0 0 0 0 1 64 128 255 192 )
( 4 0 0 1 0 0 0 1 ) ( 0 4 0 0 1 0 0 1 255 0 0 255 ) 0 2 1 } }
model { "fixture" 1 { "textures/test" 3 3
( 0 0 0 0 0 0 0 1 ) ( 4 0 0 1 0 0 0 1 ) ( 0 4 0 0 1 0 0 1 ) 0 2 1 } }
interAreaPortals { 2 1 3 0 1 ( 0 0 0 ) ( 0 4 0 ) ( 0 0 4 ) ( "gfx/fade" 32 128 ) }
nodes { 1 ( 1 0 0 0 ) -1 -2 }
shadowModel { "ignored" 0 }
'''


class Quake4MapsTest(unittest.TestCase):
    def test_proc4_mixed_vertices_sky_header_portal_fade(self):
        p=maps.read_proc(PROC,q4)
        self.assertEqual(p['header'],4294967295)
        self.assertEqual(p['areaSky'],{'_area0':True})
        np.testing.assert_allclose(p['models']['_area0'][0]['colors'],[[64/255,128/255,1,192/255],[1]*4,[1,0,0,1]])
        self.assertEqual(p['portals'][0]['fade'],['gfx/fade',32,128])
        self.assertEqual(p['shadowModels'],1)
        with self.assertRaises(ValueError):maps.read_proc(PROC)
        with self.assertRaises(ValueError):maps.read_proc(PROC.replace(b'64 128 255 192',b'64 128'),q4)

    def test_map_version3(self):
        data=b'Version 3 { "classname" "worldspawn" { brushDef3 { } } }'
        self.assertEqual(maps.read_entities(data,q4),[{'classname':'worldspawn'}])
        with self.assertRaises(ValueError):maps.read_entities(data)

    def test_raven_boundaries_and_capability_fallback(self):
        class Images:
            policy=q4
            def resolve(self,value):return {'expression':value,'status':'ready'}
        stage,notes=compile_stage(lex('if ( glslPrograms ) == 0 nomips shaderParm 0 parm0 shaderTexture 0 _white blend diffusemap map downsize(foo, 2) vertexColor'),Images())
        self.assertEqual(stage['condition'],['==',0,0])
        self.assertEqual(stage['role'],'diffuse');self.assertEqual(stage['vertexColor'],1)
        self.assertIn('downsize',stage['image']['expression'])
        self.assertEqual(notes,['shaderparm','shadertexture'])
        blended,_=compile_stage(lex('blend blend map _white'),Images())
        self.assertEqual(blended['blend'],['gl_src_alpha','gl_one_minus_src_alpha'])
        with self.assertRaises(ValueError):expression('glslPrograms')

    def test_extensionless_raven_sound_samples(self):
        class Asset:
            campaign='q4base'
            def __init__(self,data):self.data=data
            def read(self):return self.data
        files={'sound/test.sndshd':Asset(b'sound pulse { looping sound/lamp shakeData 0 abcdef }'),
               'sound/lamp.ogg':Asset(b'OggS original')}
        with tempfile.TemporaryDirectory() as tmp:
            sound=Sounds(files,Path(tmp),q4).resolve({'s_shader':'pulse'})
            self.assertEqual(sound['source'],'sound/lamp.ogg')
            self.assertEqual((Path(tmp)/Path(sound['file']).name).read_bytes(),b'OggS original')
            self.assertNotIn('approximation',sound)

    def test_mp_filters_and_spawner_provenance(self):
        defs=Declarations({},q4);defs.entities={'monster':{'model':'m'},'persona':{'skin':'blue'}}
        entities=[{'classname':'worldspawn'}, {'name':'dm','filter_dm':'1'}, {'name':'ctf','filter_ctf':'1'},
                  {'name':'spawner','classname':'func_spawner','target':'point','def_spawn1':'unused','def_spawn2':'monster'},
                  {'name':'point','classname':'target_null','origin':'20 30 40','def_spawn1':'monster'},
                  {'name':'marine','def_persona':'persona','skin':'red'}]
        selected,notes=prepare_entities(entities,defs,'mp/test')
        self.assertEqual(notes['filtered'],['ctf']);self.assertEqual(notes['otherLayouts']['ctf']['adds'],['ctf'])
        spawn=next(e for e in selected if e.get('name')=='spawner__preview')
        self.assertEqual(spawn['origin'],'20 30 40');self.assertEqual(spawn['_provenance']['point'],'point')
        self.assertEqual(next(e for e in selected if e.get('name')=='marine')['skin'],'blue')

    def test_plain_bind_preserves_world_pose_and_door_delta_once(self):
        defs=Declarations({},q4)
        parts=[{'vertices':[[0,0,0],[20,0,0]]}]
        entities=[{'name':'door','classname':'func_door','model':'door','origin':'100 0 0','angle':'0','start_open':'1','lip':'0'},
                  {'name':'child','origin':'105 0 0','bind':'door'}, {'name':'grandchild','origin':'108 0 0','bind':'child'}]
        transforms,notes=attachment_transforms(entities,defs,{}, {'door':parts})
        np.testing.assert_allclose(transforms['child'] @ [105,0,0,1],[125,0,0,1])
        np.testing.assert_allclose(transforms['grandchild'] @ [108,0,0,1],[128,0,0,1])
        entities[0]['start_open']='0'
        transforms,_=attachment_transforms(entities,defs,{}, {'door':parts})
        np.testing.assert_allclose(transforms['child'],np.eye(4))

    def test_joint_bind_and_pusher_attachment(self):
        # Native world-space joint matrices at bind and snapshot; verify the
        # child does not accidentally receive its parent's translation twice.
        defs=Declarations({},q4);defs.models={'body':{'mesh':'body.md5mesh','anims':{'idle':['idle.md5anim'],'bind':['bind.md5anim']}}}
        class Asset:
            def __init__(self,data):self.data=data
            def read(self):return self.data
        parent={'name':'parent','model':'body','origin':'100 0 0','bindanim':'bind'}
        def pose(mesh,anim):return [],[{'name':'root','x':10 if anim==b'bind' else 20}]
        def joint_matrix(js):
            m=np.eye(4);m[0,3]=js[0]['x'];return [m]
        from tools import quake4_map_policy as rules
        with patch.object(rules,'read_md5',side_effect=pose),patch.object(rules,'joint_matrices',side_effect=joint_matrix):
            transforms,_=attachment_transforms([parent,{'name':'child','origin':'112 0 0','bind':'parent','bindtojoint':'root'},
                {'name':'pusher','origin':'120 0 0','attachedentity':'parent','attachedbone':'root'}],defs,{'body.md5mesh':Asset(b'mesh'),'idle.md5anim':Asset(b'idle'),'bind.md5anim':Asset(b'bind')}, {})
        np.testing.assert_allclose(transforms['child'] @ [112,0,0,1],[122,0,0,1])
        np.testing.assert_allclose(transforms['pusher'] @ [120,0,0,1],[120,0,0,1])

    def test_light_attachment_transforms_separate_light_origin(self):
        delta=np.eye(4);delta[0,3]=10
        e=transformed_light({'origin':'1 0 0','light_origin':'2 0 0'},delta)
        np.testing.assert_allclose(maps.vector(e['light_origin']),[12,0,0])

    def test_material_suffix_and_default_are_not_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            defs=Declarations({},q4);defs.materials={'metal':['diffusemap','_white'],'collision':['collision']}
            materials=Materials(defs,Textures({},Path(tmp),q4))
            self.assertEqual(materials.resolve('metal.12')['name'],'metal')
            fallback=materials.resolve('Default')
            self.assertTrue(fallback['visible']);self.assertEqual(fallback['images']['diffuse']['status'],'ready')
            self.assertFalse(materials.resolve('collision')['visible'])

    def test_import_lights_fixture_hidden_rgba_shared_textures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);install=root/'install';(install/'q4base').mkdir(parents=True)
            with zipfile.ZipFile(install/'q4base/pak001.pk4','w') as z:
                z.writestr('maps/game/test.proc',PROC)
                z.writestr('maps/game/test.map','''Version 3
                { "classname" "worldspawn" }
                { "classname" "light" "name" "lamp" "model" "fixture" "origin" "1 0 4" "light_radius" "100 100 100" "noDiffuse" "1" "noSpecular" "1" "globalLight" "1" "start_off" "1" }
                { "classname" "light" "name" "low" "detailLevel" "0" }
                { "classname" "light" "name" "fog" "texture" "fog" }
                { "classname" "func_static" "name" "hidden" "model" "fixture" "hide" "2" }
                { "classname" "func_fx" "name" "effect" }
                { "classname" "info_player_start" "origin" "0 0 0" "name" "#str_1" }
                ''')
                z.writestr('materials/test.mtr','''textures/test { diffusemap _white }
                lights/squarelight1 { { map _white colored } }
                lights/defaultpointlight { lightFalloffImage _quadratic { map _white } }
                fog { fogLight { map _white } }''')
                z.writestr('strings/english.lang','{ "#str_1" "Landing" }')
            report=maps.import_maps(install,root/'maps',maps=['q4base:game/test'],models_root=root/'models',policy=q4)
            self.assertEqual(report['ready'],1)
            scene=json.loads((root/'maps/quake4-maps/maps/q4base_game_test/scene.json').read_text())
            buffers=scene['geometry_buffers'];array=buffers['arrays']['colors']
            binary=(root/'maps/quake4-maps/maps/q4base_game_test'/buffers['file']).read_bytes()
            colors=np.frombuffer(binary,dtype='<f4',count=array['count'],offset=array['offset'])
            self.assertEqual(len(colors),scene['stats']['vertices']*4)
            self.assertAlmostEqual(float(colors[3]),192/255,places=6)
            self.assertEqual(scene['game'],'quake4');self.assertEqual(scene['colorSize'],4)
            self.assertEqual(scene['stats']['lights'],1);self.assertEqual(scene['stats']['skippedLights'],2)
            light=scene['lights'][0]
            self.assertTrue(all(light[k] for k in ('noDiffuse','noSpecular','globalLight','authoredOff')))
            self.assertEqual(light['parms'][:3],[0,0,0]);self.assertIsNotNone(light['falloff'])
            self.assertEqual(scene['stats']['hiddenEntitiesShown'],1)
            self.assertEqual(scene['stats']['renderedEntities'],2)
            self.assertEqual(scene['stats']['excluded']['BSE effect'],1)
            self.assertEqual(scene['viewpoints'][0]['name'],'Landing')
            self.assertTrue((root/'models/quake4/textures').is_dir())
            self.assertFalse((root/'maps/quake4').exists())
            with self.assertRaises(ValueError):maps.import_maps(install,root/'maps',models_root=install,policy=q4)

    def test_shared_viewer_routes_and_game_import(self):
        import app
        client=app.app.test_client()
        self.assertEqual(client.get('/maps/quake4/').data,client.get('/maps/doom3/').data)
        with patch('tools.import_doom3_map.import_maps',return_value={'ready':1}) as run:
            response=client.post('/import_quake4_maps',json={'path':'q4'})
            self.assertEqual(response.status_code,200);self.assertIs(run.call_args.kwargs['policy'],q4)


if __name__=='__main__':unittest.main()

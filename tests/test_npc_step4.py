"""Invented NPC definitions, MDS/MD3/GLM/GLA and BSP bytes; no game installs."""
import json
from pathlib import Path
import tempfile
import unittest

from tools import import_rtcw_map as rtcw, import_jk2_map as jk2, import_ja_map as ja
from tools.model_data import read_geometry_buffers
from tests.test_rtcw import mds, pk3, image_bytes
from tests.test_q3 import md3
from tests.test_rtcw_maps import bsp, entity
from tests.test_jk2 import glm, gla


class NPCStep4Tests(unittest.TestCase):
    def test_rtcw_body_head_skin_assembly_and_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out';folder='models/players/infantryss/'
            things=[dict(classname='worldspawn'),dict(classname='ai_soldier',origin='10 20 30'),
                    dict(classname='ai_soldier',skin='infantryss/red',head='red',origin='40 50 60',spawnflags='1'),
                    dict(classname='ai_marker'),dict(classname='ai_trigger'),dict(classname='ai_effect'),
                    dict(classname='misc_gamemodel',model='models/prop.md3')]
            pk3(root/'pak0.pk3',[
                ('maps/test.bsp',bsp(''.join(entity(**e) for e in things))),
                (folder+'body.mds',mds()),(folder+'head.md3',md3('tag_mouth')),(folder+'hat.md3',md3()),
                (folder+'body_default.skin',b'part,models/test/skin'),
                (folder+'body_red.skin',b'part,models/test/red\nplayerscale,1.25'),
                (folder+'head_default.skin',b'body,models/test/skin\nmd3_hat,hat.md3'),(folder+'head_red.skin',b'body,models/test/red'),
                ('models/prop.md3',md3()),('models/test/skin.tga',image_bytes()),
                ('models/test/red.tga',image_bytes()),('textures/test/wall.tga',image_bytes())])
            report=rtcw.import_maps(root,out)
            self.assertFalse(report['failed'])
            scene=json.loads((out/'maps/sp_test/scene.json').read_text())
            actors=[p for p in scene['placed'] if p.get('npc')]
            self.assertEqual([p['entity'] for p in actors],[1,2]);self.assertEqual(actors[1]['scale'],[1.25]*3)
            self.assertEqual(actors[0]['classname'],'ai_soldier')
            desc=scene['itemModels'][actors[0]['models'][0]]
            surfaces=json.loads((out/desc['file']).read_text())
            self.assertEqual(len(surfaces),3)  # Body, head and attached hat have one placement/ID.
            body=rtcw.rtcw.read_model(mds())
            self.assertEqual(surfaces[1]['vertices'][:3],list(body['tags']['tag_head'][:3]))
            self.assertEqual(surfaces[2]['vertices'][:3],surfaces[1]['vertices'][:3])
            self.assertEqual(desc['npcSource']['frame'],0)
            self.assertTrue(any(not p.get('npc') for p in scene['placed']))
            self.assertTrue(all(scene['entityResults'][i]['status']=='omitted' for i in (3,4,5)))

    def jedi_scene(self,module):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out';out.mkdir()
            for name in ('models','textures'):(out/name).mkdir()
            definitions=b'''test { playerModel test customSkin red scale 125 surfOff part0 surfOn part1 }
                vehicle { playerModel test class CLASS_VEHICLE }
                player { playerModel player }
                mouse { headmodel none torsomodel none legsmodel mouse }
                hidden { playerModel test surfOff part0 surfOff part1 }
                embedded { playerModel test customSkin absent }'''
            pk3(root/'assets0.pk3',[
                ('ext_data/npcs.cfg' if module is jk2 else 'ext_data/npcs/test.npc',definitions),
                ('models/players/test/model.glm',glm(flags=(0,2))),('models/players/test/test.gla',gla()),
                ('models/players/test/animation.cfg',b'BOTH_STAND1 1 1 -1 20'),
                ('models/players/test/model_red.skin',b'part0,models/test/red\npart1,models/test/red'),
                ('models/players/test/model_default.skin',b'part0,models/test/red\npart1,models/test/red'),
                ('models/players/mouse/lower.md3',md3()), # Stock MD3 droids have no skin file.
                ('models/test/skin.tga',image_bytes()),('models/test/red.tga',image_bytes())])
            game=module.Game(root,'sp');module.configure_map(game,'test')
            textures=getattr(module,'Textures',rtcw.Textures)(out/'textures',game)
            mt=module.ModelTextures(game,out/'textures')
            things=[dict(classname='NPC_test',origin='10 20 30'),
                    dict(classname='NPC_spawner',NPC_type='test',count='99',targetname='later'),
                    dict(classname='NPC_spawner'),dict(classname='NPC_control'),dict(classname='NPC_vehicle',NPC_type='test'),
                    dict(classname='NPC_spawner',NPC_type='vehicle'),dict(classname='NPC_Player'),
                    dict(classname='misc_gamemodel',model='models/players/test/model.glm',skin='models/players/test/model_red.skin'),
                    dict(classname='NPC_Droid_Mouse'),dict(classname='NPC_test',spawnflags=str(1<<(8+game.skill))),
                    dict(classname='NPC_spawner',NPC_type='hidden'),dict(classname='NPC_embedded')]
            placed,models,audit,missing,limits=module.place_entities(game,things,{},None,out,textures,mt)
            actors=[p for p in placed if p.get('npc')]
            self.assertEqual([p['entity'] for p in actors],[0,1,8,11])
            self.assertEqual(actors[0]['models'],actors[1]['models']);self.assertEqual(actors[0]['scale'],[1.25]*3)
            self.assertEqual(actors[0]['origin'],[10,20,30]);self.assertEqual(actors[1]['classname'],'npc_spawner')
            desc=models[actors[0]['models'][0]];self.assertEqual(desc['npcSource']['frame'],1)
            self.assertEqual(desc['npcSource']['skin'],'models/players/test/model_red.skin')
            self.assertEqual(audit[1]['npcType'],'test');self.assertIn('one NPC',audit[1]['reason'])
            for i in (2,3,4,5,6,9):self.assertEqual(audit[i]['status'],'omitted',audit[i])
            self.assertEqual(audit[10]['status'],'failed');self.assertIn('no drawable',audit[10]['reason'])
            embedded=models[actors[-1]['models'][0]]
            self.assertTrue(any('embedded-shader fallback' in w for w in embedded['limitations']))
            prop=next(p for p in placed if p['entity']==7);self.assertFalse(prop.get('npc'))
            geometry=json.loads((out/desc['file']).read_text())
            if isinstance(geometry,dict):
                self.assertEqual(len(geometry['groups']),1)
                read_geometry_buffers(out/desc['file'],geometry['geometry_buffers'])
            else:
                self.assertEqual(len(geometry),1)
                expected=game.model_rules.load_model(game,'models/players/test/model.glm',frame=1,surface_flags={'part0':2,'part1':0})[0]
                self.assertEqual(geometry[0]['vertices'],expected['surfaces'][0]['vertices'])
            self.assertEqual(models[prop['models'][0]]['shaders'][0]['name'],desc['shaders'][0]['name'])

    def test_jk2_definition_pose_skin_spawner_and_md3_droid(self):self.jedi_scene(jk2)

    def test_ja_definition_pose_skin_and_vehicle_exclusion(self):self.jedi_scene(ja)

    def test_class_variants_and_rtcw_overrides(self):
        from types import SimpleNamespace
        game=SimpleNamespace(game_id='ja',npc_definitions={k:dict(playermodel='test',_source='test.npc')
            for k in ('stofficer','jedi_hf1','rosh_dark','chewie','cultist_saber_med_throw',
                      'cultist_saber_med_throw2','assassin_droid','saber_droid_training','dkothos','tavion_new')})
        for cls,flags,expected in [('NPC_Stormtrooper',1,'stofficer'),('NPC_Jedi',4,'jedi_hf1'),
                                   ('NPC_Rosh_Penin',1,'rosh_dark'),('NPC_Chewbacca',0,'chewie'),
                                   ('NPC_Cultist_Saber',9,'cultist_saber_med_throw'),
                                   ('NPC_Cultist_Saber_Powers',9,'cultist_saber_med_throw2'),
                                   ('NPC_Droid_Assassin',0,'assassin_droid'),('NPC_Droid_Saber',1,'saber_droid_training'),
                                   ('NPC_Kothos',0,'dkothos')]:
            spec,reason=jk2.npc_spec(game,dict(classname=cls,spawnflags=str(flags)))
            self.assertFalse(reason);self.assertEqual(spec['type'],expected)
        spec,reason=jk2.npc_spec(game,dict(classname='NPC_Tavion_New',NPC_type='tavion_new_dark'))
        self.assertEqual(spec['type'],'tavion_new') # This engine wrapper overwrites NPC_type.
        self.assertEqual(rtcw.npc_appearance(dict(classname='ai_warzombie'))[:2],
                         ('models/players/warrior','models/players/warrior/body_crypt2.skin'))
        self.assertEqual(rtcw.npc_appearance(dict(classname='ai_soldier',skin='officerss/red',model='models/custom/body.mds'))[3],
                         'models/custom/body.mds')
        self.assertEqual(rtcw.npc_appearance(dict(classname='ai_soldier',model='officerss/body.mds')),
                         ('models/players/officerss','models/players/officerss/body_default.skin',
                          'models/players/officerss/head_default.skin','models/players/officerss/body.mds'))
        self.assertEqual(rtcw.npc_appearance(dict(classname='ai_soldier',model='models/custom/body.mds'))[1],
                         'models/players/infantryss/body_default.skin')
        for kind,values in [('atst',dict(playermodel='atst',**{'class':'CLASS_ATST'})),
                            ('nulldriver',dict(playermodel='kyle')),('rocks',dict(playermodel='rocks'))]:
            game.npc_definitions[kind]=dict(values,_source='test.npc')
            spec,reason=jk2.npc_spec(game,dict(classname='NPC_spawner',NPC_type=kind))
            self.assertIsNone(spec);self.assertIn('excluded',reason)


if __name__=='__main__':unittest.main()

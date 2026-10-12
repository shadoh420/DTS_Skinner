"""JA map rules exercised with tiny synthetic PK3/RBSP/model fixtures."""
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import import_ja_map as maps
from tests.test_jk2_maps import fixture,raven,entity,pk3,image_bytes,glm


class JAMapTests(unittest.TestCase):
    def test_view_distance_defaults_overrides_and_validation(self):
        self.assertEqual(maps.view_distance({},'sp')['distanceCull'],12000)
        self.assertEqual(maps.view_distance({},'mp')['distanceCull'],6000)
        profile=maps.view_distance(dict(distancecull='1800'),'sp')
        self.assertEqual(profile['farClip'],3117.6)
        self.assertEqual(profile['distanceCullSource'],'worldspawn')
        self.assertEqual(maps.view_distance(dict(distancecull='100'),'mp')['farClip'],2048)
        for value in ('nan','inf','-1','0','bad'):
            with self.assertRaisesRegex(ValueError,'distanceCull'):maps.view_distance(dict(distancecull=value),'sp')

    def game(self,section='mp',name='mp/ffa1'):
        game=SimpleNamespace(section=section,skill=0,items={k:dict(kind=v[0]) for k,v in maps.MP_ITEMS.items()})
        maps.configure_map(game,name);return game

    def test_native_modes_filters_and_starts(self):
        game=self.game(name='mp/siege_hoth')
        self.assertEqual(maps.section_for('mp/siege_hoth')[0],'mp')
        self.assertEqual(maps.section_for('hoth2')[0],'sp')
        self.assertTrue(maps.entity_filter(game,dict(classname='NPC_Vehicle',model='vehicle.glm')))
        self.assertTrue(maps.entity_filter(game,dict(classname='misc_siege_item',targetname='later')))
        self.assertFalse(maps.entity_filter(game,dict(classname='misc_siege_item',targetname='later',spawnflags='8')))
        self.assertTrue(maps.entity_filter(game,dict(classname='misc_model_static',notteam='1')))
        collision=maps.jk.Collision(maps.jk.read_bsp(raven(fixture())),{})
        starts=[dict(classname='info_player_siegeteam1',origin='0 0 100',startoff='1'),
                dict(classname='info_player_siegeteam1',origin='0 0 100',angle='90')]
        views=maps.map_viewpoints(game,starts,collision)
        self.assertEqual(len(views),1);self.assertAlmostEqual(views[0]['origin'][2],60.125)
        self.assertEqual(views[0]['yaw'],90)
        maps.configure_map(game,'mp/ctf1')
        self.assertFalse(maps.entity_filter(game,dict(classname='team_ctf_redflag')))
        self.assertTrue(game.team_items)
        maps.configure_map(game,'mp/duel1')
        self.assertTrue(maps.entity_filter(game,dict(classname='item_medpak_instant')))
        self.assertTrue(maps.entity_filter(game,dict(classname='item_medpac_big')))
        self.assertFalse(maps.entity_filter(game,dict(classname='ammo_blaster')))
        game=self.game('sp','academy1')
        self.assertTrue(maps.entity_filter(game,dict(classname='misc_model_breakable',spawnflags='4096')))
        self.assertFalse(maps.entity_filter(game,dict(classname='misc_model_breakable',spawnflags='512')))
        prop=maps.prepare_entity(dict(classname='misc_model_breakable',modelscale='2',origin='0 0 10'),'sp')
        self.assertEqual(maps.vector(prop['origin']),[0,0,26])
        prop=maps.prepare_entity(dict(classname='misc_model_static',modelscale='1.5',modelscale_vec='1.25'),'sp')
        self.assertEqual(maps.base.scale(prop),[1.5]*3)
        prop=maps.prepare_entity(dict(classname='misc_model_static',modelscale='1.75 1.75 1.75'),'sp')
        self.assertEqual(maps.base.scale(prop),[1.75]*3)

    def test_profiles_grid_skin_and_synthetic_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            entities=entity(classname='worldspawn',distanceCull='1800')+entity(classname='info_player_start',origin='0 0 100')
            entities+=entity(classname='team_CTF_redplayer',origin='0 0 100')
            entities+=entity(classname='misc_model_static',model='models/props/a.glm',origin='20 0 30',skin='models/props/look.skin')
            entities+=entity(classname='NPC_Vehicle',model='models/props/a.glm')
            entities+=entity(classname='misc_model_breakable',model='models/props/a.glm',spawnflags='4096')
            pk3(root/'assets0.pk3',[
                ('maps/yavin1.bsp',raven(fixture(entities))),('maps/mp/ctf1.bsp',b'overridden'),('maps/bad.bsp',b'bad'),
                ('models/props/a.glm',glm('*default',bones=1)),('models/props/look.skin',b'part0_off,models/test/skin\n'),
                ('models/test/skin.tga',image_bytes()),('textures/test/wall.tga',image_bytes())])
            pk3(root/'assets3.pk3',[('maps/mp/ctf1.bsp',raven(fixture(entities)))])
            (root/'jaconfig.cfg').write_text('seta r_overBrightBits "0"\nseta r_gamma "1"')
            (root/'autoexec.cfg').write_text('seta r_textureMode "GL_LINEAR_MIPMAP_LINEAR"')
            game=maps.Game(root);self.assertEqual(game.game_id,'ja')
            self.assertEqual(game.assets['maps/mp/ctf1.bsp']['source'],'assets3.pk3')
            for hardware in (False,True):
                p=maps.lighting_profile(root,'sp',game,device_supports_gamma=hardware)
                self.assertEqual((p['overbrightBits'],p['sourceLightScale'],p['displayScale'],p['displayGamma']),(0,1,1,1))
                self.assertEqual(p['fov'],80);self.assertEqual(p['textureMode'],'GL_LINEAR_MIPMAP_NEAREST')
                self.assertEqual(p['configuredTextureMode'],'GL_LINEAR_MIPMAP_LINEAR')
                self.assertIn('Retail JA SP v1.0.1.0',p['textureModeSource'])
            model,_,_=maps.load_placed(game,'models/props/a.glm','models/props/look.skin',0)
            self.assertEqual(model['surfaces'][0]['shaders'],['models/test/skin'])
            result=maps.import_maps(root,out)
            self.assertEqual(result['imported'],['sp_yavin1','mp_ctf1']);self.assertIn('sp_bad',result['failed'])
            self.assertFalse(result['sweep']['missing']);self.assertFalse(result['sweep']['defaults'])
            scene=json.loads((out/'maps/sp_yavin1/scene.json').read_text())
            self.assertEqual(scene['game'],'ja');self.assertEqual(len(scene['placed']),1)
            self.assertEqual(scene['distanceCull'],1800);self.assertEqual(scene['farClip'],3117.6)
            imported=next(r for r in result['maps'] if r['map']=='yavin1')
            self.assertEqual(imported['farClip'],3117.6)
            self.assertIn('light',scene['placed'][0]);self.assertEqual(scene['placed'][0]['entity'],3)
            reasons=[r['reason'] for r in scene['entityResults']]
            self.assertIn('START_OFF (4096)',reasons)
            self.assertTrue(any('NPC' in r for r in reasons))
            self.assertEqual(json.loads((out/'maps/mp_ctf1/scene.json').read_text())['defaultGametype'],'ctf')
            self.assertEqual(len(maps.import_maps(root,out)['skipped']),2)
            (out/next(iter(scene['itemModels'].values()))['file']).unlink()
            self.assertTrue(maps.jk.sweep(out)['missing'])

    def test_grid_shift_is_ja_only(self):
        raw=fixture(entity(classname='worldspawn'))
        raw[15]=bytes([40,60,80]+[0]*9+[10,20,30]+[0]*9+[0,255,255,255,0,0])
        profile=dict(identityLight=1,sourceLightScale=2,cvars=dict(r_ambientscale=.5,r_directedscale=1))
        self.assertEqual(maps.LightGrid(raw,profile).data[0,:3].tolist(),[80,120,160])
        self.assertEqual(maps.jk.LightGrid(raw,profile).data[0,:3].tolist(),[40,60,80])

    def test_mp_pickup_display_rules(self):
        game=self.game();game.items={k:dict(kind=v[0],models=[v[1]]) for k,v in maps.MP_ITEMS.items()}
        classes=['weapon_concussion_rifle','item_medpak_instant','team_ctf_redflag','ammo_blaster']
        things=[dict(classname=c) for c in classes]
        placed=[dict(entity=i,origin=[0,0,0],scale=[1,1,1],bob=dict(base=4,rate=.005),spin=True) for i in range(4)]
        with patch.object(maps.jk,'place_entities',return_value=(placed,{},[],[],[])):
            maps.place_entities(game,things,{},None,None,None,None)
        self.assertEqual(placed[0]['scale'],[1.5]*3)
        self.assertEqual(placed[0]['bob']['base'],28);self.assertEqual(placed[0]['bob']['amplitude'],4)
        self.assertEqual(placed[1]['origin'][2],2)
        self.assertTrue(all(not p['spin'] and p['bob'] is None for p in placed[1:]))
        game=self.game('sp','taspir2')
        prop=dict(entity=0,origin=[0,0,100],scale=[1,1,1])
        audit=[dict(entity=0,reason='authored')]
        collision=maps.jk.Collision(maps.jk.read_bsp(raven(fixture())),{})
        with patch.object(maps.jk,'place_entities',return_value=([prop],{},audit,[],[])):
            maps.place_entities(game,[dict(classname='misc_model_breakable',gravity='1')],{},collision,None,None,None)
        self.assertAlmostEqual(prop['origin'][2],16.125)
        self.assertEqual(audit[0]['reason'],'model at spawn; gravity hull drop')

    def test_routes(self):
        import app
        client=app.app.test_client()
        page=client.get('/maps/ja/');self.assertEqual(page.status_code,200)
        self.assertIn(b'Jedi Academy Maps',page.data);self.assertIn(b'/static/ja-maps/viewer.js',page.data)
        self.assertNotIn(b'/static/ja-maps/viewer.js',client.get('/maps/jk2/').data)
        with patch.object(app,'import_ja_maps',return_value={'imported':['sp_test']}) as importer:
            result=client.post('/import_ja_maps',json={'game':'fixture','replace':True})
            self.assertEqual(result.status_code,200);importer.assert_called_once()
        self.assertEqual(client.post('/import_ja_maps',json={}).status_code,400)


if __name__=='__main__':unittest.main()

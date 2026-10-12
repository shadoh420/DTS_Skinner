"""Synthetic Raven BSP1 records and local PK3s; no retail bytes."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools import import_jk2_map as maps
from tests.test_rtcw_maps import bsp,entity
from tests.test_rtcw import pk3,image_bytes
from tests.test_jk2 import glm


def raven(lumps):
    header=bytearray(152);struct.pack_into('<4si',header,0,b'RBSP',1)
    for i,lump in enumerate(lumps):
        struct.pack_into('<2i',header,8+i*8,len(header),len(lump));header.extend(lump)
    return bytes(header)


def fixture(entities='',patch_surface=False):
    q=maps.qmap.read_bsp(bsp(entities,patch_surface=patch_surface),version=47);r=list(q)+[b'']
    r[9]=b''.join(struct.pack('<3i',p,s,-1) for p,s in struct.iter_unpack('<2i',q[9]))
    r[10]=b''.join(struct.pack('<16f16B',*v[:5],*(list(v[5:7])*4),*v[7:10],*(list(v[10:])*4)) for v in struct.iter_unpack('<10f4B',q[10]))
    r[13]=b''.join(struct.pack('<7i8B14i12f2i',*s[:7],0,32,255,255,0,32,255,255,
        0,1,-1,-1,*([0]*8),128,128,*s[12:24],*s[24:]) for s in struct.iter_unpack('<12i12f2i',q[13]))
    r[14]=q[14]+bytes([10,20,30])*16384
    r[15]=bytes(30);r[17]=struct.pack('<H',0)
    return r


class JK2MapTests(unittest.TestCase):
    def test_raven_channels_flags_collision_and_validation(self):
        entities=entity(classname='worldspawn')+entity(classname='light',targetname='switch',style=32,spawnflags=4)
        raw=fixture(entities,True);raw[1]=struct.pack('<64sii',b'textures/test/wall',0x200000,0x10)
        notes=[];out=maps.read_bsp(raven(raw),notes)
        self.assertEqual(struct.unpack_from('<2i',out[1],64),(0x80,0x10000))
        self.assertEqual(out[14][:3],bytes([32,64,96]))
        self.assertEqual(struct.unpack_from('<4B',out[10],40),(32,64,96,255))
        self.assertTrue(any(n.get('action')=='starting light styles combined' for n in notes))
        collision=maps.base.Collision(out,{})
        view=maps.viewpoints([dict(classname='info_player_start',origin='0 0 100')],collision,'sp')[0]
        self.assertAlmostEqual(view['origin'][2],62.125)  # Raised quadratic patch above the brush floor.
        raw[0]=entity(classname='worldspawn').encode();out=maps.read_bsp(raven(raw))
        self.assertEqual(out[14][:3],bytes([42,84,126]))
        bad=list(raw);bad[17]=struct.pack('<H',1)
        with self.assertRaisesRegex(ValueError,'light-grid'):maps.read_bsp(raven(bad))
        bad=list(raw);bad[10]=bad[10][:-1]
        with self.assertRaisesRegex(ValueError,'partial'):maps.read_bsp(raven(bad))
        bad=list(raw);bad[13]=bytearray(bad[13]);struct.pack_into('<i',bad[13],36,99)
        with self.assertRaisesRegex(ValueError,'lightmap'):maps.read_bsp(raven(bad))
        bad=list(raw);bad[11]=struct.pack('<6i',0,1,2,0,1,999)
        with self.assertRaisesRegex(ValueError,'draw vertex'):maps.read_bsp(raven(bad))
        bad=bytearray(raven(raw));struct.pack_into('<i',bad,8,1)
        with self.assertRaisesRegex(ValueError,'outside'):maps.read_bsp(bad)
        with self.assertRaisesRegex(ValueError,'RBSP'):maps.read_bsp(bsp())
        with self.assertRaisesRegex(ValueError,'IBSP'):maps.qmap.read_bsp(raven(raw))

    def test_triangle_soup_keeps_vertex_light_kind_and_first_color_set(self):
        raw=fixture(entity(classname='worldspawn'))
        surfaces=list(struct.iter_unpack('<7i8B14i12f2i',raw[13]))
        s=list(surfaces[0]);s[2]=3;s[11:15]=[0,255,255,255]
        # Even if a compiler left a lightmap number, ParseTriSurf uses vertex light.
        surfaces[0]=s;raw[13]=b''.join(struct.pack('<7i8B14i12f2i',*s) for s in surfaces)
        out=maps.read_bsp(raven(raw))
        self.assertEqual(struct.unpack_from('<i',out[13],28)[0],-3)
        self.assertEqual(struct.unpack_from('<4B',out[10],40),(32,64,96,255))

    def test_spawn_styles_sections_and_brush_rules(self):
        styles=maps.light_styles([dict(classname='worldspawn',ls_2r='z',ls_2g='a',ls_2b='m'),dict(classname='light',targetname='lamp',style='35',switch_style='2')])
        self.assertEqual(styles[35],[1,0,12/25])
        self.assertEqual(maps.section_for('pit')[0],'sp');self.assertEqual(maps.section_for('duel_pit')[0],'mp')
        self.assertTrue(maps.filtered(dict(classname='NPC_Kyle',model='models/test.glm'),'sp'))
        self.assertTrue(maps.filtered(dict(notfree='1'),'mp'));self.assertFalse(maps.filtered(dict(notteam='1'),'mp'))
        self.assertTrue(maps.filtered(dict(spawnflags='512'),'sp',1));self.assertFalse(maps.filtered(dict(spawnflags='512'),'sp',0))
        self.assertTrue(maps.filtered(dict(classname='team_CTF_redflag'),'mp'))
        collision=maps.Collision(maps.read_bsp(raven(fixture())),{})
        position,angles=collision.mount_mine([0,0,32],[90,0,0])
        self.assertAlmostEqual(position[2],0);self.assertEqual(angles,[-90,0,0])
        with self.assertRaisesRegex(ValueError,'inside'):collision.mount_mine([0,0,-4],[90,0,0])
        model=(0,0,0,32,8,48,0,0,0,0)
        self.assertTrue(maps.brush_state(dict(classname='func_usable',spawnflags='1'),model,[])['hidden'])
        self.assertFalse(maps.brush_state(dict(classname='func_static',spawnflags='1'),model,[])['hidden'])
        self.assertEqual(maps.brush_state(dict(classname='func_door',spawnflags='1'),model,[])['origin'],[24,0,0])
        rack=maps.rack_items(dict(classname='misc_model_gun_rack',origin='0 0 0'))
        self.assertEqual(len(rack),3);self.assertTrue(all(e['classname']=='weapon_blaster' and e['spawnflags']=='17' for e in rack))

    def test_profiles_shader_rules_and_import_sweep(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out'
            entities=entity(classname='worldspawn')+entity(classname='info_player_start',origin='0 0 100')+entity(classname='info_player_deathmatch',origin='0 0 100')
            entities+=entity(classname='func_usable',model='*1',spawnflags=1)
            entities+=entity(classname='misc_model_ghoul',model='models/props/a.glm',origin='30 0 30')
            entities+=entity(classname='NPC_Kyle',model='models/props/a.glm')
            entities+=entity(classname='weapon_test',targetname='visible_sp',origin='20 20 40')
            entities+=entity(classname='weapon_test',spawnflags=32,origin='20 20 40')
            shader=b'''textures/test/wall
{
nopicmip
{
map textures/test/wall.tga
rgbGen identity
}
{
map $lightmap
blendFunc filter
}
{
map textures/missing_foliage
surfaceSprites vertical 1 2 3 4
}
}
'''
            pk3(root/'assets0.pk3',[
                ('maps/kejim_test.bsp',raven(fixture(entities))),('maps/ffa_test.bsp',raven(fixture(entities))),
                ('maps/broken.bsp',b'bad'),('models/props/a.glm',glm('*default',bones=1)),
                ('models/test/skin.tga',image_bytes()),('textures/test/wall.tga',image_bytes()),('shaders/test.shader',shader),
                ('ext_data/items.dat',b'{\nclassname weapon_test\nworldmodel models/props/a.glm\ntype IT_WEAPON\n}'),
                ('default.cfg',b'seta r_picmip 1\nseta r_overBrightBits 1')])
            (root/'jk2config.cfg').write_text('seta r_gamma "1.3"\nseta cg_fov "80"\nseta r_mapOverBrightBits "2"')
            game=maps.Game(root);no=game.lighting;yes=maps.lighting_profile(root,'sp',game,device_supports_gamma=True)
            self.assertEqual((no['overbrightBits'],no['sourceLightScale'],no['displayGamma']),(0,1,1))
            self.assertEqual((yes['overbrightBits'],yes['sourceLightScale'],yes['displayScale']),(1,1,2))
            self.assertNotIn('r_mapoverbrightbits',no['cvars'])
            self.assertEqual(no['textureMode'],'GL_LINEAR_MIPMAP_NEAREST')
            mp=maps.lighting_profile(root,'mp',game);self.assertEqual(mp['sourceLightScale'],1)
            textures=maps.base.Textures(root/'textures',game);desc,missing,notes=maps.describe(game,'textures/test/wall',textures)
            self.assertFalse(missing);self.assertEqual(len(desc['stages']),2);self.assertTrue(any('surfaceSprites' in x for x in notes))
            # A name with no script falls back to its image, not to an empty material.
            desc,missing,_=maps.describe(game,'models/test/skin',textures)
            self.assertFalse(missing);self.assertTrue(desc.get('implicit') or desc.get('stages'))
            result=maps.import_maps(root,out)
            self.assertEqual(len(result['imported']),2);self.assertIn('sp_broken',result['failed'])
            scene=json.loads((out/'maps/sp_kejim_test/scene.json').read_text())
            self.assertEqual(len(scene['placed']),2)
            self.assertIn('light',scene['placed'][0])
            self.assertEqual(scene['viewpoints'][0]['origin'][2],60.125)
            self.assertFalse(result['sweep']['missing']);self.assertFalse(result['sweep']['defaults'])
            self.assertEqual(len(maps.import_maps(root,out)['skipped']),2)
            model=next(iter(scene['itemModels'].values()));(out/model['file']).unlink()
            self.assertTrue(maps.sweep(out)['missing'])

    def test_grid_lighting_styles_interpolation_and_wall_samples(self):
        raw=fixture(entity(classname='worldspawn',gridsize='64 64 128',ls_2r='a',ls_2g='a',ls_2b='a'))
        # Two samples along X. The array deliberately reverses the record order.
        raw[7]=struct.pack('<6f4i',0,0,0,64,0,0,0,0,0,0)
        def sample(ambient,directed,styles=(0,255,255,255)):
            return bytes(ambient+[200]*9+directed+[200]*9+list(styles)+[0,0])
        raw[15]=sample([20,40,60],[10,20,30])+sample([40,60,80],[30,40,50])
        raw[17]=struct.pack('<2H',1,0)
        lighting=dict(identityLight=1,cvars=dict(r_ambientscale=.5,r_directedscale=1))
        grid=maps.LightGrid(raw,lighting)
        self.assertTrue(grid.valid)
        result=grid.sample([16,0,0])
        self.assertEqual(result['ambient'],[49.5,59.5,69.5])
        self.assertEqual(result['directed'],[25,35,45])
        self.assertAlmostEqual(result['direction'][2],1)
        # The invalid corner is excluded and the surviving corner renormalized.
        raw[15]=sample([20,40,60],[10,20,30])+sample([40,60,80],[30,40,50],(255,255,255,255))
        self.assertEqual(maps.LightGrid(raw,lighting).sample([16,0,0])['ambient'],[42,52,62])
        # Additional style channels are applied, not unconditionally summed.
        raw[15]=sample([20,40,60],[10,20,30],(0,2,255,255))*2
        self.assertEqual(maps.LightGrid(raw,lighting).sample([16,0,0])['directed'],[10,20,30])
        raw[17]=b''
        self.assertFalse(maps.LightGrid(raw,lighting).valid)
        self.assertEqual(maps.LightGrid(raw,lighting).sample([0,0,0])['ambient'],[182]*3)
        raw[17]=struct.pack('<H',3)
        with self.assertRaisesRegex(ValueError,'light-grid array'):maps.LightGrid(raw,lighting)

    def test_fog_stages_are_visible_only_for_jk2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            shader=b'''textures/test/slime
{
surfaceparm fog
fogparms ( 0.1 0.2 0.3 ) 128
{
map textures/test/slime.tga
blendFunc add
}
{
map $lightmap
blendFunc filter
}
}
textures/test/fog
{
surfaceparm fog
fogparms ( 0 0 0 ) 100
}
'''
            pk3(root/'assets0.pk3',[('shaders/test.shader',shader),('textures/test/slime.tga',image_bytes())])
            game=maps.Game(root);textures=maps.base.Textures(root/'textures',game)
            desc,missing,notes=maps.describe(game,'textures/test/slime',textures)
            self.assertFalse(missing);self.assertEqual(len(desc['stages']),2)
            self.assertTrue(any('fog' in n for n in notes))
            self.assertEqual(maps.describe(game,'textures/test/fog',textures)[0]['stages'],[])
            game.described.clear()
            self.assertEqual(maps.base.describe(game,'textures/test/slime',textures)[0]['stages'],[])

    def test_routes_and_data_validation(self):
        from app import app
        client=app.test_client()
        page=client.get('/maps/jk2/');self.assertEqual(page.status_code,200)
        text=page.get_data(as_text=True)
        self.assertIn('/static/jk2-maps/viewer.js',text);self.assertIn('surface-sprite',text)
        self.assertIn('Jedi Outcast',client.get('/').get_data(as_text=True))
        with patch('app.import_jk2_maps',return_value={'imported':['sp_test']}) as importer:
            response=client.post('/import_jk2_maps',json={'game':'test','replace':True})
            self.assertEqual(response.status_code,200);importer.assert_called_once()
        self.assertEqual(client.post('/import_jk2_maps',json={}).status_code,400)
        self.assertEqual(client.post('/import_jk2_maps',json={'game':'test'},headers={'Origin':'https://foreign.invalid'}).status_code,403)


if __name__=='__main__':unittest.main()

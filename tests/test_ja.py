"""Synthetic JA assets only; optional retail test is a single model probe."""
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from tools import import_ja as ja
from tests.test_jk2 import gla, glm, install
from tests.test_rtcw import pk3, image_bytes


def renamed_glm(names, flags=None, animation='*default'):
    raw=bytearray(glm(animation,flags=flags or (0,)*len(names),bones=1))
    for i,name in enumerate(names):
        at=164+struct.unpack_from('<i',raw,164+i*4)[0]
        raw[at:at+64]=name.encode().ljust(64,b'\0')
    return bytes(raw)


class JATests(unittest.TestCase):
    def test_ja_skin_normalization_multipart_and_specials(self):
        skin=ja.read_skin(b'''// comment
torso_off,models/test/skin
torso,models/ignored
cap_off,*off
head,*off
tag_head,
bolt,$whiteimage
''')
        self.assertEqual(skin['surfaces'],{'torso':'models/test/skin','head':'*off','bolt':'$whiteimage'})
        self.assertTrue(ja.read_skin(b'cap_off,*off')['empty'])
        self.assertIn('torso_off',ja.jk.read_skin(b'torso_off,models/test/skin')['surfaces'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            pk3(root/'assets1.pk3',[(f'models/test/{n}.skin',data) for n,data in
                [('head',b'head,a\nshared,first'),('torso',b'torso,b\nshared,second'),('lower',b'legs,c')]])
            game=ja.Game(root)
            surfaces=ja.skin_surfaces(game,'models/test/|head|torso|lower')
            self.assertEqual(surfaces,dict(head='a',shared='first',torso='b',legs='c'))
            with self.assertRaises(ValueError):ja.skin_surfaces(game,'models/test/|head|torso')
            with self.assertRaises(ValueError):ja.skin_surfaces(game,'../outside.skin')
        with self.assertRaises(ValueError):ja.read_skin(b'broken')

    def test_ja_surface_names_flags_bolts_and_72_bone_compatibility(self):
        raw=renamed_glm(['torso_off','*droidunit','cap_off'],(0,1,2))
        skin={'torso':'models/test/skin','cap':'models/test/skin'}
        mesh=ja.read_glm(raw,ja.jk.default_skeleton(),skin)
        self.assertEqual([s['name'] for s in mesh['surfaces']],['torso'])
        self.assertEqual(mesh['surfaces'][0]['shaders'],['models/test/skin'])
        self.assertEqual(len(mesh['hidden']),2)
        bolt=mesh['tags']['*droidunit'];self.assertEqual(bolt[:3],(-1,0,0))
        axes=np.array(bolt[3:]).reshape(3,3)
        np.testing.assert_allclose(axes@axes.T,np.eye(3),atol=1e-6)
        self.assertAlmostEqual(np.linalg.det(axes),1)
        # Existing JK2 keeps the suffix and never exposes surface-bolt tags.
        old=ja.jk.read_glm(raw,ja.jk.default_skeleton(),skin)
        self.assertEqual(old['surfaces'][0]['name'],'torso_off')
        self.assertNotIn('*droidunit',old['tags'])
        raw=bytearray(glm('models/players/_humanoid/_humanoid',bones=1))
        struct.pack_into('<i',raw,140,72)
        at=struct.unpack_from('<i',raw,148)[0]+8
        refs=struct.unpack_from('<i',raw,at+32)[0]
        struct.pack_into('<i',raw,at+refs,71)
        matrices=[np.eye(4) for _ in range(53)];matrices[52][0,3]=9
        sk=dict(matrices=matrices,bone_poses=matrices,names=list(map(str,range(53))),frame_count=1)
        mesh=ja.read_glm(raw,sk)
        self.assertEqual(mesh['surfaces'][0]['vertices'][:3],[9,0,0])
        with self.assertRaisesRegex(ValueError,'mismatch'):ja.jk.read_glm(raw,sk)
        with self.assertRaises(ValueError):ja.read_glm(raw[:-1],sk)
        self.assertEqual(len(ja.old_humanoid_remap()),72)
        self.assertEqual([ja.old_humanoid_remap()[i] for i in (7,12,32,55,71)],[6,10,29,42,52])

    def test_mounts_shaders_poses_and_definition_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'models').mkdir();(root/'models/value').write_bytes(b'loose')
            for n in range(4):pk3(root/f'assets{n}.pk3',[('models/value',str(n).encode())])
            pk3(root/'patch.pk3',[
                ('shaders/ja.shader',b'''alias {
q3map_material metal
{ map effect.tga }
{ map diffuse.tga
blendFunc blend
rgbGen lightingDiffuseEntity
glow
}
}'''),('diffuse.tga',image_bytes()),
                ('models/players/test/animation.cfg',b'BOTH_STAND1 1 1 -1 20\nBOTH_GEARS_OPEN 2 3 0 20\nBOTH_VT_IDLE 6 2 0 20')])
            game=ja.Game(root)
            self.assertEqual(game.section,'ja');self.assertEqual(game.read('models/value'),b'3')
            self.assertEqual(game.search_order,['patch.pk3','assets3.pk3','assets2.pk3','assets1.pk3','assets0.pk3','loose'])
            self.assertEqual(ja.shader_maps(game)['alias']['static_stage']['index'],1)
            animation='models/players/test/test.gla'
            self.assertEqual(ja.idle_pose(game,animation)[0],1)
            self.assertEqual(ja.idle_pose(game,animation,'VH_FIGHTER')[0],4)
            self.assertEqual(ja.idle_pose(game,animation,'VH_ANIMAL')[0],6)
            self.assertEqual(ja.idle_pose(game,animation,'VH_SPEEDER')[0],0)
            self.assertEqual(ja.read_gla(gla(),1)['frame'],1)
        self.assertEqual(ja.definitions(b'/* template { model no } */'),[])
        self.assertEqual(ja.definitions(b'"Jet" { model jet skin "black|white" }')[0]['values']['skin'],'black|white')
        for raw in (b'jet { model }',b'jet { model a',b'jet { model { nested } }',b'x'*(1024*1024+1)):
            with self.assertRaises(ValueError):ja.definitions(raw)

    def test_catalog_stock_species_vehicles_exports_and_edited_texture(self):
        from app import app
        with tempfile.TemporaryDirectory() as tmp:
            root=install(Path(tmp));out=root/'out'
            pk3(root/'assets1.pk3',[
                ('models/players/jedi_hm/model.glm',renamed_glm(['torso_off'])),
                ('models/players/jedi_hm/model_default.skin',b'torso,models/test/skin'),
                ('models/players/jedi_hm/model_siege.skin',b'torso,models/test/red'),
                ('models/players/jedi_hm/head_a.skin',b'torso,models/test/skin'),
                ('ext_data/npcs/stock.npc',b'hero { playerModel jedi_hm customSkin siege }'),
                ('ext_data/vehicles/jet.veh',b'Jet { model jet droidNPC random }'),
                ('ext_data/vehicles/absent.veh',b'Absent { model absent }'),
                ('ext_data/vehicles/duplicate.veh',b'jet { model missing }'),
                ('ext_data/vehicles/template.veh',b'/* example { model missing } */'),
                ('models/players/jet/model.glm',renamed_glm(['hull','*droidunit'],(0,1))),
                ('models/players/jet/model_default.skin',b'hull,models/test/skin'),
                ('ext_data/npcs/r2d2.npc',b'r2d2 { playerModel r2d2 }'),
                ('models/players/r2d2/model.glm',renamed_glm(['droid'])),
                ('models/players/r2d2/model_default.skin',b'droid,models/test/red'),
                ('models/players/_humanoid_level/level.gla',gla()),
                ('ext_data/sabers/ignored.sab',b'saber { model missing }')])
            game=ja.Game(root);specs=list(ja.model_specs(game))
            self.assertEqual(len([p for p,s in specs if p.endswith('.veh')]),2)
            self.assertEqual(len([p for p,s in specs if '/jedi_hm/' in p]),2)
            self.assertFalse(any(p.endswith('/jet/model.glm') or '/_humanoid_level/' in p or 'head_a' in s for p,s in specs))
            self.assertEqual(len(game.vehicle_issues),2)
            parts,meta,_=ja.catalog_parts(game,'ext_data/vehicles/jet.veh','')
            self.assertEqual(len(parts),2);self.assertEqual(meta['attachments'][0]['choice'],'r2d2')
            self.assertIn('tags',parts[1]);self.assertEqual(meta['scale'],1)
            with patch('app.pack_dirs',{'ja':out}):
                client=app.test_client();response=client.post('/import_ja',json={'path':str(root)})
                self.assertEqual(response.status_code,200,response.json)
                report=response.json;self.assertEqual(report['categories']['Vehicles'],2)
                failed=next(r for r in report['results'] if r['source']=='ext_data/vehicles/absent.veh')
                self.assertEqual(failed['status'],'skipped');self.assertIn('Missing asset',failed['reason'])
                self.assertGreaterEqual(report['duplicates'],1)
                siege=next(r for r in report['skin_results'] if r['source'].endswith('jedi_hm/model_siege.skin'))
                self.assertFalse(siege['browser_excluded'])
                rows=client.get('/list_models?game=ja').json
                vehicle=next(r for r in rows if r['category']=='Vehicles' and r['status']=='ready')
                name=vehicle['model_name'];data=client.get('/model_json/'+name+'?game=ja').json
                self.assertEqual(data['game'],'ja');self.assertEqual(len(data['groups']),2)
                self.assertTrue(client.get('/list_textures?game=ja').json)
                self.assertEqual(client.get('/export_obj/'+name+'?game=ja').status_code,200)
                glb=client.get('/export_glb/'+name+'?game=ja')
                self.assertEqual(glb.status_code,200);self.assertEqual(glb.headers['X-Skinner-Animation-Status'],'static')
                self.assertIn('id="jaImport"',client.get('/').get_data(as_text=True))
                self.assertEqual(client.post('/import_ja',json={'path':str(root)},headers={'Origin':'https://outside'}).status_code,403)
            self.assertEqual(report['texture_library']['after'],2)
            texture=out/'textures'/data['material_textures'][0]
            self.assertFalse(texture.name.startswith(('ja_','image_')))
            Image.new('RGBA',(2,2),'orange').save(texture);edited=texture.read_bytes()
            ja.import_catalog(root,out);self.assertEqual(texture.read_bytes(),edited)
            self.assertTrue((out/'import-report.json').is_file())


INSTALL=Path('C:/Program Files (x86)/Steam/steamapps/common/Jedi Academy/GameData')
@unittest.skipUnless(os.environ.get('JA_INSTALL_TESTS')=='1' and (INSTALL/'base/assets1.pk3').exists(),
                     'Optional single-model JA probe; set JA_INSTALL_TESTS=1')
class InstallTests(unittest.TestCase):
    def test_xwing_parked_model_and_bolt(self):
        game=ja.Game(INSTALL)
        model,meta,_=ja.load_model(game,'models/players/x-wing/model.glm',vehicle_type='VH_FIGHTER')
        self.assertEqual(meta['frame'],14);self.assertTrue(model['surfaces'])
        self.assertIn('*droidunit',model['tags'])


if __name__=='__main__':unittest.main()

"""Small Raven policy fixtures; no game install required."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from tools import quake4_policy as q4
from tools.doom3_assets import Install, Declarations, Textures, Materials, lex, material_record
from tools.doom3_mesh import read_md5, joint_matrices
from tools.import_doom3 import inventory, import_catalog, load_parts
from tools.model_data import load_model_data
from tools.texture_storage import dds_bytes
from tests.test_doom3 import MESH, ANIM, pak


def fixture(root, definitions='', extra=None):
    pak(root,'q4base','pak001.pk4',{
        'def/test.def':definitions,
        'models/body.md5mesh':MESH,'models/idle.md5anim':ANIM,
        'textures/test.dds':dds_bytes(4,4,'DXT1',[bytes(8)]),
        **(extra or {}),
    })


class RavenTests(unittest.TestCase):
    def test_archive_order_and_q4mp_exclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ('zpak_english_05','game100','pak025','q4cmp_pak001','pak001','zpak_english','game000'):
                pak(temp,'q4base',name+'.pk4',{'winner.txt':name})
            pak(temp,'q4mp','game999.pk4',{'winner.txt':'wrong'})
            with Install(temp,q4) as install:
                self.assertEqual([Path(z.filename).stem for z in install.archives],
                                 ['game000','game100','pak001','pak025','q4cmp_pak001','zpak_english','zpak_english_05'])
                self.assertEqual(install.files['q4base']['winner.txt'].read(),b'zpak_english_05')

    def test_eof_brace_recovery_reports_each_file(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp,'entityDef first { "model" "a.lwo" }',{
                'def/mapobjects/tactical_station.def':'entityDef second { "model" "b.lwo"',
                'def/vehicles/dropship_interior.def':'entityDef third { "model" "c.lwo"'})
            with Install(temp,q4) as install:
                defs=Declarations(install.files['q4base'],q4)
                self.assertEqual(len(defs.warnings),2)
                self.assertEqual(defs.entity('third')['model'],'c.lwo')

    def test_numbered_parents_use_authored_order_then_child(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp,'''entityDef a { "x" "a" "y" "a" }
                entityDef b { "x" "b" "z" "b" }
                entityDef child { "inherit2" "b" "inherit1" "a" "y" "child" }''')
            with Install(temp,q4) as install:
                defs=Declarations(install.files['q4base'],q4)
                value=defs.entity('child')
                self.assertEqual((value['x'],value['y'],value['z']),('b','child','b'))
                defs.entities['a']['inherit']='child'
                with self.assertRaisesRegex(ValueError,'Cyclic'): defs.entity('child')

    def test_persona_overlays_spawnargs(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp,'''entityDef persona { "skin" "blue" "override_head" "head_b" }
                entityDef actor { "skin" "red" "model" "body" "def_persona" "persona" }''')
            with Install(temp,q4) as install:
                e=Declarations(install.files['q4base'],q4).entity('actor')
                self.assertEqual((e['model'],e['skin'],e['override_head']),('body','blue','head_b'))

    def test_model_inheritance_idle_override_and_multiclip_forward_blend(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp,'''model parent { mesh models/body.md5mesh
                anim machinegun_idle models/player/machinegun_aim_down.md5anim,
                    models/player/machinegun_aim.md5anim, models/player/machinegun_aim_up.md5anim
                anim idle models/idle.md5anim }
                model child { inherit parent }
                entityDef base { "anim_idle1" "machinegun_idle" }
                entityDef actor { "inherit1" "base" "model" "child" }''')
            with Install(temp,q4) as install:
                d=Declarations(install.files['q4base'],q4); m=d.model('child')
                self.assertEqual(len(m['anims']['machinegun_idle']),3)
                self.assertEqual(q4.idle_snapshot(d.entity('actor'),m)['weights'],[0,1,0])
                self.assertEqual(q4.idle_animation(d.entity('actor'),m),'models/player/machinegun_aim.md5anim')
                self.assertEqual(q4.idle_animation({'anim_idle':'idle'},m),'models/idle.md5anim')

    def test_surface_loadout_show_overrides_hide(self):
        parts=[dict(shader=n) for n in ('gun','other','body')]
        self.assertEqual([p['shader'] for p in q4.surfaces(parts,{'hidesurface1':'gun','hidesurface2':'other','showsurface':'gun'})],['gun','body'])

    def test_unmodified_view_weapon_hides_upgrade_surface(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp,'''entityDef mod { "mod_showsurface" "large_clip" }
                entityDef weapon_test { "model_view" "models/body.md5mesh" "def_mod1" "mod" }''')
            with Install(temp,q4) as install:
                rows,_=inventory(install,q4)
                parts=q4.surfaces([dict(shader='small_clip'),dict(shader='large_clip')],rows[0]['entity'])
                self.assertEqual(parts,[dict(shader='small_clip')])

    def test_head_entity_override_and_attachment_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp,'''model body { mesh models/body.md5mesh anim idle models/idle.md5anim }
                entityDef head_b { "model" "models/body.md5mesh" }
                entityDef char_test { "model" "body" "def_head" "missing" "override_head" "head_b" "joint_head" "head" }''')
            with Install(temp,q4) as install:
                rows,d=inventory(install,q4); row=next(r for r in rows if r['name']=='char_test')
                parts,_=load_parts(row,install.files['q4base'],d['q4base'],policy=q4)
                self.assertEqual(list(parts[1]['vertices'][0]),[0,0,6])
        _,body=read_md5(MESH,ANIM); _,head=read_md5(MESH)
        body[0]['orientation']=(0,0,-2**-.5,-2**-.5)
        attachment=joint_matrices(body)[0]
        pose=q4.head_pose(head,body,{'copy_joint1':'head'},{'copy_joint_world head':'head'},attachment)
        np.testing.assert_allclose(attachment @ pose[0],joint_matrices(body)[0],atol=1e-12)
        local=q4.head_pose(head,body,{'copy_joint1':'head'},{},attachment)
        np.testing.assert_allclose(local[0],joint_matrices(body)[0],atol=1e-12)

    def test_vehicle_position_parts_at_rest_and_missing_part_report(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp,'''entityDef vehicle_test { "model" "models/body.md5mesh" "def_position_driver" "seat" }
                entityDef seat { "def_part_body" "part" "def_part_gun" "gun" "internalSurface" "console" }
                entityDef part { "model" "models/body.md5mesh" "joint" "head" "offset" "1 0 0" }
                entityDef gun { "spawnclass" "rvVehicleWeapon" "joint" "head" }''')
            with Install(temp,q4) as install:
                rows,d=inventory(install,q4); row=next(r for r in rows if r['name']=='vehicle_test')
                self.assertEqual(len(row['vehicle_parts']),2)
                self.assertEqual(q4.surfaces([dict(shader='console')],row['entity']),[])
                parts,_=load_parts(row,install.files['q4base'],d['q4base'],policy=q4)
                self.assertEqual(len(parts),2)
                self.assertEqual(list(parts[1]['vertices'][0]),[1,0,4])
                d['q4base'].entities['seat']['def_part_body']='missing'
                with self.assertRaisesRegex(ValueError,'Missing definition'): q4.prepare(row,d['q4base'],[])

    def test_equipment_uses_joint_and_authored_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp,'''entityDef char_test { "model" "models/body.md5mesh" "def_attach" "tool" }
                entityDef tool { "model" "models/body.md5mesh" "joint" "head" "origin" "3 0 0" "angles" "0 90 0" }''')
            with Install(temp,q4) as install:
                rows,d=inventory(install,q4); row=next(r for r in rows if r['name']=='char_test')
                parts,_=load_parts(row,install.files['q4base'],d['q4base'],policy=q4)
                np.testing.assert_allclose(parts[1]['vertices'][1],[3,1,4],atol=1e-12)

    def test_skin_wildcard_and_nodraw(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp,extra={'skins/test.skin':'skin look { textures/test nodraw * textures/other }'})
            with Install(temp,q4) as install:
                defs=Declarations(install.files['q4base'],q4)
                self.assertEqual(defs.remap('textures/test','look'),'nodraw')
                self.assertEqual(defs.remap('anything','look'),'textures/other')
                self.assertFalse(Materials(defs,Textures(install.files['q4base'],Path(temp)/'out',q4)).resolve('nodraw')['visible'])

    def test_guides_and_inline_guides_substitute_embedded_parameters(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(temp,extra={
                'guides/test.guide':'guide generic(T) { diffusemap textures/T_d } inlineGuide bump(T) { bumpmap textures/T_local }',
                'materials/test.mtr':'guide textures/a generic(foo)\ntextures/b { inlineGuide bump(bar) diffusemap textures/b }'})
            with Install(temp,q4) as install:
                defs=Declarations(install.files['q4base'],q4)
                self.assertEqual(material_record('a',defs.materials['textures/a'])['diffuse'],'textures/foo_d')
                self.assertEqual(material_record('b',defs.materials['textures/b'])['bump'],'textures/bar_local')

    def test_dds_program_and_tga_only_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            image=io.BytesIO(); Image.new('RGBA',(2,2),(10,20,30,40)).save(image,format='TGA')
            program='addnormals( models/a_local, heightmap( models/a_h, 3))'
            source='progimg/addnormals/models/a_local_heightmap__models_a_h_3.dds'
            self.assertEqual(q4.dds_path(program),source)
            raw=dds_bytes(4,4,'DXT5',[bytes(16)]); raw=raw[:84]+b'RXGB'+raw[88:]
            fixture(temp,extra={source:raw,'textures/only.tga':image.getvalue()})
            with Install(temp,q4) as install:
                tex=Textures(install.files['q4base'],Path(temp)/'out',q4)
                for expression,expected in ((program,raw),('textures/test',install.files['q4base']['textures/test.dds'].read())):
                    r=tex.resolve(expression); self.assertEqual((tex.output/r['texture']).read_bytes(),expected)
                r=tex.resolve('textures/only'); self.assertTrue(r['texture'].endswith('.png'))
                (tex.output/r['texture']).write_bytes(b'user edit')
                Textures(tex.files,tex.output,q4).resolve('textures/only')
                self.assertEqual((tex.output/r['texture']).read_bytes(),b'user edit')
            record=material_record('test',lex('{ blend diffusemap map downsize(models/test, 1) }'),q4)
            self.assertEqual(q4.dds_path(record['diffuse']),'progimg/downsize/models/test_1.dds')

    def test_catalog_binary_routes_exclusions_and_missing_dependencies(self):
        import app as server
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as out:
            fixture(temp,'''entityDef monster_test { "model" "models/body.md5mesh" }
                entityDef item_missing { "model" "models/missing.lwo" }''',
                {'effects/test.fx':'', 'guis/test.gui':'', 'particles/test.prt':''})
            output=Path(out); report=import_catalog(temp,output,policy=q4)
            self.assertEqual((report['ready'],report['failed']),(1,1))
            self.assertEqual(report['excluded_assets'],{'.fx':1,'.gui':1,'.prt':1})
            self.assertIn('Missing dependency',report['inventory']['failures'][0]['reason'])
            model=next(r['model'] for r in report['results'] if r['status']=='ready')
            path=output/'model_json'/(model+'.json')
            self.assertIn('geometry_buffers',json.loads(path.read_text()))
            self.assertEqual(load_model_data(path)['game'],'quake4')
            with patch.dict(server.pack_dirs,quake4=output):
                client=server.app.test_client()
                page=client.get('/').get_data(as_text=True)
                self.assertEqual(page.count('id="quake4Import"'),1)
                self.assertIn('value="quake4">Quake 4 textures',page)
                script=(Path(__file__).resolve().parents[1]/'static/skinner.js').read_text(encoding='utf8')
                self.assertEqual(script.count("['quake4', 'importQuake4', 'quake4Path', 'quake4ImportStatus']"),1)
                self.assertEqual(client.get('/list_models?game=quake4').status_code,200)
                self.assertIn('textures%2Ftest.dds',client.get('/list_textures?game=quake4').json)
                with patch.object(server,'import_doom3_catalog',return_value={'ready':1}) as importer:
                    self.assertEqual(client.post('/import_quake4',json={'path':temp}).json,{'ready':1})
                    importer.assert_called_once_with(temp,output,policy=q4)


if __name__=='__main__': unittest.main()

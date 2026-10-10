"""Synthetic map pipeline; select AnachronoxMapsInstallTest explicitly for retail."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

from app import app
from tests.test_anachronox import INSTALL, adat, bitmap, md2, mda
from tests.test_quake2_maps import bsp as bsp38
from tools import import_anachronox as anox, import_anachronox_map as maps


def bsp(flags=0, entity_lump=None, edits=None):
    raw = bsp38(flags=flags, entity_lump=entity_lump)
    lumps = [raw[o:o + n] for o, n in struct.iter_unpack('<ii', raw[8:160])]
    for i, data in (edits or {}).items():
        lumps[i] = data
    header, body = struct.pack('<4si', b'IBSP', 38), b''
    for lump in lumps:
        header += struct.pack('<ii', 160 + len(body), len(lump))
        body += lump
    return header + body


def fixture(root, raw=None):
    (root / 'MAPS.dat').write_bytes(adat([('test.bsp', raw or bsp(), True)]))
    opaque = io.BytesIO(); Image.new('RGB', (2, 2), (19, 47, 113)).save(opaque, format='TGA')
    (root / 'TEXTURES.dat').write_bytes(adat([('test.tga', opaque.getvalue(), True),
        ('textureinfo.dat', b'#alphatest\nTEST.tga\n#metal\ntest\n', False)]))
    (root / 'GRAPHICS.dat').write_bytes(adat([(f'sky/unit1_{s}.tga', bitmap(), False) for s in ('rt', 'bk', 'lf', 'ft', 'up', 'dn')]))
    fields = ['npc_test', 'models/boots/hero.mda', '2', '3', '4', 'char', '-12', '-12', '0', '12', '12', '64',
              'shadow', '1', '68', '160', '8', '1', '0', '0:0', '0', '0', 'none', 'Test NPC']
    (root / 'MODELS.dat').write_bytes(adat([
        ('entity.dat', '|'.join(fields).encode(), False), ('boots/hero.md2', md2(), True),
        ('boots/hero.mda', mda(), True), ('boots/chosen.tga', bitmap(), False), ('boots/body.png', bitmap('PNG'), False)]))
    return anox.read_install(root)[0]


class AnachronoxMapsTest(unittest.TestCase):
    def test_bsp_texture_metadata_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = fixture(Path(tmp))
            world = maps.read_bsp(bsp(), files)
            self.assertEqual(len(world['lump_sizes']), 19)
            self.assertEqual(world['textures'][0]['source'], 'textures/test.tga')
            self.assertEqual(world['texinfo'][0][9], maps.ALPHA_TEST | 0x800)
            self.assertEqual(world['textures'][0]['properties'], ['alphatest', 'metal'])
            self.assertTrue(maps.face_data(world, 0)['unlit'])
            for i in maps.LUMP_FORMATS:
                with self.subTest(lump=i), self.assertRaises(ValueError):
                    maps.read_bsp(bsp(edits={i: b'x'}), files)
            bad = bytearray(bsp()); struct.pack_into('<i', bad, 8, len(bad) + 1)
            with self.assertRaises(ValueError):
                maps.read_bsp(bad, files)
            node = struct.pack('<3i6h2H', 0, 0, 0, 0, 0, 0, 32, 32, 32, 0, 1)
            with self.assertRaisesRegex(ValueError, 'Cyclic BSP nodes'):
                maps.read_bsp(bsp(edits={4: node}), files)
            files.pop('textures/test.tga')
            missing = maps.read_bsp(bsp(), files)['textures'][0]
            self.assertTrue(missing['missing'])
            self.assertIn('No supplied', missing['reason'])
            self.assertEqual(missing['image'].getpixel((8, 0)), (255, 0, 255, 255))

    def test_lightmapped_trans33_warp_sky_and_fog_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = fixture(Path(tmp))
            files.pop('textures/textureinfo.dat')
            for flags, kind, unlit, alpha in ((0, 'normal', False, 1), (16, 'normal', False, .33),
                (32, 'normal', True, .66), (8, 'turbulent', True, 1), (4 | 128, 'sky', True, 1),
                (maps.FOG, 'hidden', False, 1), (128, 'hidden', False, 1)):
                f = maps.face_data(maps.read_bsp(bsp(flags), files), 0)
                self.assertEqual((f['kind'], f['unlit'], f['alpha']), (kind, unlit, alpha))
            world = maps.read_bsp(bsp(), files)
            self.assertEqual(maps.q2.lightmap_rgb(maps.face_data(world, 0), world['lighting'])[0, 0].tolist(), [64, 128, 32])

    def test_npc_first_pose_default_skins_scale_and_skip_audit(self):
        raw = bsp(entity_lump=b'{"classname" "worldspawn"}\n'
            b'{"classname" "info_player_start" "origin" "16 16 96"}\n'
            b'{"classname" "npc_test" "origin" "100 200 300" "angle" "90" "scale" "2 1 0.5"}\n'
            b'{"classname" "npc_test" "spawnflags" "2"}\n'
            b'{"classname" "npc_test" "spawncondition" "story_flag == 1"}\n'
            b'{"classname" "trigger_once"}\n{"classname" "unknown_prop"}\0')
        with tempfile.TemporaryDirectory() as tmp:
            files = fixture(Path(tmp), raw)
            scene, blob, images, atlas = maps.build_map(raw, 'test', files)
            self.assertEqual(len(scene['models']), 1)
            placed = scene['models'][0]
            self.assertEqual(placed['status'], 'drawn', scene['model_failures'])
            self.assertEqual(placed['scale'], [4, 3, 2])
            self.assertEqual([s['method'] for s in placed['skins']], ['mda', 'md2'])
            self.assertEqual(len(scene['omitted_models']), 5)
            positions = np.frombuffer(blob, '<f4', scene['vertices'] * 3).reshape(-1, 3) / maps.SCALE
            # Scale around the frame translation (10,-20,30), then yaw90, then origin.
            np.testing.assert_allclose(positions[4], [226, 370, 111], atol=1e-5)
            self.assertEqual(len(blob), scene['vertices'] * 28 + scene['indices'] * 4)
            self.assertFalse(scene['missing'], scene['missing_reasons'])
            self.assertEqual(scene['lighting']['display_gamma'], 1)
            self.assertTrue(all(not note.endswith('.') for note in scene['gaps']))

    def test_fog_console_forms_never_reject_the_map(self):
        for text, density, color, duration in (
            ('0', 0, [0, 0, 0], 0), ('.0001 .6 .6 .7', .0001, [.6, .6, .7], 0),
            ('.0003 .1 .3 .25 2000', .0003, [.1, .3, .25], 2000),
            ('.0003 .188 .314 .314 2000', .0003, [.188, .314, .314], 2000),
            ('', 0, [0, 0, 0], 0), ('-1 -1 -1 -1', 0, [0, 0, 0], 0),
            ('garbage nan inf 1e999 1e999', 0, [0, 0, 0], 1)):
            with self.subTest(fog=text), tempfile.TemporaryDirectory() as tmp:
                raw = bsp(entity_lump=(' {"classname" "worldspawn" "fog" "' + text + '"}\0').encode())
                scene, *_ = maps.build_map(raw, 'test', fixture(Path(tmp), raw))
                self.assertEqual(scene['fog']['density'], density)
                self.assertEqual(scene['fog']['color'], color)
                self.assertEqual(scene['fog']['transition_ms'], duration)

    def test_image_alpha_without_textureinfo_and_blank_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            (root / 'textures').mkdir()
            (root / 'textures/test.tga').write_bytes(bitmap())
            files = anox.read_install(root)[0]; files.pop('textures/textureinfo.dat')
            world = maps.read_bsp(bsp(), files)
            face = maps.face_data(world, 0)
            self.assertEqual(face['flags'], maps.q2.TRANS33)
            self.assertEqual(face['alpha'], 1)
            scene, _, images, _ = maps.build_map(bsp(), 'test', files)
            group = scene['groups'][0]
            self.assertTrue(group['texture_alpha'])
            self.assertEqual(images[group['texture']].getchannel('A').getextrema(), (0, 128))
            Image.new('RGBA', (2, 2), (231, 101, 26, 0)).save(root / 'textures/test.tga')
            face = maps.face_data(maps.read_bsp(bsp(), anox.read_install(root)[0]), 0)
            self.assertEqual(face['kind'], 'hidden')

    def test_native_model_pivot_offset_negative_roll_and_correct_scaling_flag(self):
        local = np.array([[14., -17., 50.]])
        item = dict(scale=[4, 3, 2], angles=[0, 90, 0], origin=[100, 200, 300], offset=[7, 8, 9], renderfx=0)
        np.testing.assert_allclose(maps.model_positions(local, [10, -20, 30], item), [[118, 234, 379]])
        item['renderfx'] = 0x4000000
        np.testing.assert_allclose(maps.model_positions(local, [10, -20, 30], item), [[158, 264, 409]])
        item.update(scale=[1, 1, 1], angles=[0, 0, 90], origin=[0, 0, 0], offset=[0, 0, 0])
        np.testing.assert_allclose(maps.model_positions(np.array([[0., 1., 0.]]), [0, 0, 0], item), [[0, 0, -1]], atol=1e-8)

    def test_model_lighting_zero_uses_map_sample_rgb_overrides_and_entity_blending(self):
        raw = bsp(entity_lump=b'{"classname" "worldspawn"}\n'
            b'{"classname" "npc_test" "origin" "1 2 3"}\n'
            b'{"classname" "npc_test" "rgb" ".02 .17 .1" "alphaflags" "1"}\0')
        with tempfile.TemporaryDirectory() as tmp:
            files = fixture(Path(tmp), raw); world = maps.read_bsp(raw, files)
            world['nodes'] = [()]  # Mock only the light query, not model decoding or placement.
            table = maps.entity_table(files); table['npc_test']['lighting'] = '0'
            placements, _ = maps.scenery(world, table)
            data = maps.q2.geometry(world, maps.instances(world)[0], face_reader=maps.face_data)
            with patch.object(maps, 'light_point', return_value=np.array([.01, .4, 1.2])) as sample:
                _, failures = maps.append_models(data, world, files, placements)
            self.assertFalse(failures)
            self.assertEqual(sample.call_count, 1)
            groups = [g for g in data['groups'] if g['kind'] == 'model']
            self.assertEqual(groups[0]['model_light'], [.15, .4, 1])
            self.assertEqual(groups[-1]['model_light'], [.02, .17, .1])
            self.assertEqual(groups[-1]['settings']['blend'], ['gl_src_alpha', 'gl_one'])
            self.assertFalse(groups[-1]['settings']['depthWrite'])

    def test_wall_door_plat_and_invisible_brush_states(self):
        world = dict(models=[(0, 0, 0, 100, 100, 100, 0, 0, 0, 0, 0, 0)] * 8, entities=[
            dict(classname='worldspawn'), dict(classname='func_wall', model='*1', spawnflags='2'),
            dict(classname='func_wall', model='*2', spawnflags='1'),
            dict(classname='func_door', model='*3', spawnflags='1', angle='0'),
            dict(classname='func_plat', model='*4', height='40'),
            dict(classname='func_fog', model='*5'),
            dict(classname='func_wall', model='*6', spawnflags='512'),
            dict(classname='func_wall', model='*7', spawncondition='0')])
        drawn, skipped = maps.instances(world)
        self.assertEqual([i['model'] for i in drawn], [0, 1, 3, 4, 6])
        self.assertEqual(drawn[2]['offset'], [94, 0, 0])
        self.assertEqual(drawn[3]['offset'], [0, 0, -40])
        self.assertEqual(len(skipped), 3)

    def test_hull_drop_and_single_direct_start_and_camera_fallback(self):
        # A slab top at z=0 with solid/playerclip brushes and a world leaf.
        planes = [(1,0,0,64,0),(-1,0,0,64,0),(0,1,0,64,1),(0,-1,0,64,1),(0,0,1,0,2),(0,0,-1,16,2)]
        world = dict(models=[(-64,-64,-16,64,64,128,0,0,0,0,0,0)], planes=planes,
            nodes=[(0,-1,-1,0,0,0,64,64,128,0,0)], leaves=[(0,0,0,0,0,0,64,64,128,0,0,0,1)],
            leafbrushes=[0], brushes=[(0,6,0x10000)], brushsides=[(i,0) for i in range(6)],
            entities=[dict(classname='info_player_start', origin='10 10 99', targetname='transition'),
                      dict(classname='info_player_start', origin='0 0 80', angle='45')])
        views = maps.viewpoints(world, np.empty((0,3,3)))
        self.assertEqual(len(views), 1)
        np.testing.assert_allclose(views[0]['native_origin'], [-62 / np.sqrt(2), -62 / np.sqrt(2), 88])
        self.assertEqual(views[0]['yaw'], -45)
        for origin, angle, floor, expected in (
                ('1520 -2518 -369', '0', -397, [1458, -2518, -309]),
                ('-2168 3312 -1480', '', -1504, [-2230, 3312, -1416]),
                ('1448 256 56', '90', 32, [1448, 194, 120])):
            world['entities'] = [dict(classname='info_player_start', origin=origin, targetname='transition')]
            if angle:
                world['entities'][0]['angle'] = angle
            with self.subTest(origin=origin), patch.object(maps, 'hull_floor', return_value=floor):
                view = maps.viewpoints(world, np.empty((0, 3, 3)))[0]
                np.testing.assert_allclose(view['native_origin'], expected)
                self.assertEqual(view['yaw'], -float(angle or 0))
                self.assertEqual(view['pitch'], 0)
                self.assertIn('measured AnoxCam', view['reason'])
        world['entities'] = []
        view = maps.viewpoints(world, np.array([[[0,0,0],[32,0,0],[0,32,0]]]))[0]
        self.assertEqual(view['native_origin'][2], 64)
        self.assertEqual(view['classname'], 'fallback_camera')

    def test_camera_pulls_forward_at_solid_and_does_not_cross_thin_walls(self):
        # A two-unit wall at -32 <= x < -30; both segment endpoints can be clear.
        world = dict(models=[(0,) * 12], planes=[(1,0,0,-30,0), (1,0,0,-32,0)],
                     nodes=[(0,-1,1), (1,-2,-1)], leaves=[(0,), (1,)])
        for x in (-31, -62):
            np.testing.assert_allclose(maps.camera_trace(world, [0,0,88], [x,0,88]), [-29,0,88])
        np.testing.assert_allclose(maps.camera_trace(world, [0,0,88], [62,0,88]), [62,0,88])
        world['entities'] = [dict(classname='info_player_start', origin='0 0 10')]
        with patch.object(maps, 'hull_floor', return_value=0):
            self.assertEqual(maps.viewpoints(world, [])[0]['native_origin'], [-29,0,88])
        # A low ceiling also clips the initial lift instead of leaving the eye in solid.
        world.update(planes=[(0,0,1,70,2)], nodes=[(0,-2,-1)])
        with patch.object(maps, 'hull_floor', return_value=0):
            np.testing.assert_allclose(maps.viewpoints(world, [])[0]['native_origin'], [-62,0,69])

    def test_atd_first_frame_missing_model_and_import_layers_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            (root / 'textures').mkdir()
            (root / 'textures/test.tga').unlink(missing_ok=True)
            (root / 'textures/motion.atd').write_bytes(b'ATD1\ntype=animation\nwidth=2\nheight=2\n!bitmap\nfile=textures/test.tga\n!frame\nbitmap=0\nwait=100\n')
            files = anox.read_install(root)[0]
            self.assertFalse(maps.texture('motion', files)['missing'])
            patch_bsp = bsp(entity_lump=b'{"classname" "worldspawn" "fog" ".001 .1 .2 .3"}\n'
                              b'{"classname" "prop" "model" "models/missing.md2"}\0')
            with zipfile.ZipFile(root / 'anox1.zip', 'w') as z:
                z.writestr('maps/test.bsp', patch_bsp)
            with patch('app.local_data_dir', root / 'out'):
                client = app.test_client()
                self.assertEqual(client.get('/maps/anachronox/').status_code, 200)
                response = client.post('/import_anachronox_maps', json={'path': str(root)})
                self.assertEqual(response.status_code, 200, response.json)
                report = response.json
                self.assertEqual(report['imported'], ['test'])
                self.assertEqual([r['status'] for r in report['results']], ['overridden', 'imported'])
                self.assertEqual(report['model_classes']['prop']['Spawn model is absent from layered files: models/missing.md2'], 1)
                with client.get('/anachronox-map-data/maps/test/scene.json') as response:
                    scene = response.json
                self.assertEqual(scene['fog']['density'], .001)
                self.assertEqual(len(scene['viewpoints']), 1)
                self.assertEqual(len(scene['model_failures']), 1)
                self.assertEqual(client.post('/import_anachronox_maps', json={}).status_code, 400)
                self.assertEqual(client.post('/import_anachronox_maps', json={'path': str(root)}, headers={'Origin':'https://elsewhere.test'}).status_code, 403)


@unittest.skipUnless((INSTALL / 'MAPS.dat').is_file(), 'Anachronox install absent')
class AnachronoxMapsInstallTest(unittest.TestCase):
    def test_all_effective_maps_accounted_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = maps.import_maps(INSTALL, Path(tmp))
            self.assertEqual(report['archive_counts']['maps.dat']['directory'], 98)
            self.assertFalse(report['failed'], report['failed'])
            self.assertTrue(report['imported'])
            self.assertFalse([r for r in report['results'] if r['status'] == 'pending'])
            files = anox.read_install(INSTALL)[0]
            self.assertEqual(len(report['imported']), sum(p.startswith('maps/') and p.endswith('.bsp') for p in files))
            for ident in report['imported']:
                scene = json.loads((Path(tmp) / 'maps' / ident / 'scene.json').read_text())
                self.assertEqual(len(scene['viewpoints']), 1, ident)
                self.assertEqual(len(scene['lump_sizes']), 19, ident)
                self.assertTrue(scene['vertices'], ident)


if __name__ == '__main__':
    unittest.main()

"""BSP38/WAL, ref_gl RGB lighting, brush spawn states and fully counted baseq2 maps."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from app import app
from tests.test_quake import PALETTE, pak
from tests.test_quake2 import INSTALL, pcx
from tools.import_quake2_map import (FLOWING, NODRAW, SKY, TRANS33, TRANS66, WARP, build_map, face_data,
    geometry, import_maps, instances, lightmap_rgb, read_bsp, read_wal, skybox, upload_texture, viewpoints, warp_polygons)


def wal(pixel=42):
    body, offsets = b'', []
    for level in range(4):
        offsets.append(100 + len(body))
        body += bytes([pixel]) * (16 >> level) ** 2
    return struct.pack('<32s6I32s3i', b'test', 16, 16, *offsets, b'', 0, 0, 0) + body


def bsp(flags=0, styles=(0, 255, 255, 255), samples=((64, 128, 32),)):
    lumps = [b''] * 19
    lumps[0] = b'{"classname" "worldspawn" "message" "RGB map"}\n{"classname" "info_player_start" "origin" "16 16 96" "angle" "90"}\0'
    lumps[1] = struct.pack('<4fi', 0, 0, 1, 0, 2)
    lumps[2] = struct.pack('<12f', 0, 0, 0, 32, 0, 0, 32, 32, 0, 0, 32, 0)
    lumps[5] = struct.pack('<8fii32si', 1, 0, 0, 0, 0, 1, 0, 0, flags, 0, b'test', -1)
    lumps[6] = struct.pack('<Hhihh4Bi', 0, 0, 0, 4, 0, *styles, 0)
    lumps[7] = b''.join(bytes(rgb) * 9 for rgb in samples)
    lumps[11] = struct.pack('<10H', 0, 0, 0, 1, 1, 2, 2, 3, 3, 0)
    lumps[12] = struct.pack('<4i', -4, -3, -2, -1)
    lumps[13] = struct.pack('<9f3i', 0, 0, 0, 32, 32, 128, 0, 0, 0, 0, 0, 1)
    header, body = struct.pack('<4si', b'IBSP', 38), b''
    for lump in lumps:
        header += struct.pack('<ii', 160 + len(body), len(lump))
        body += lump
    return header + body


def files():
    stream = io.BytesIO()
    Image.new('RGB', (8, 8), (40, 60, 80)).save(stream, format='TGA')
    entries = [('textures/test.wal', wal()), ('pics/colormap.pcx', pcx())]
    for suffix in ('rt', 'bk', 'lf', 'ft', 'up', 'dn'):
        entries += [(f'env/unit1_{suffix}.tga', stream.getvalue()), (f'env/unit1_{suffix}.pcx', pcx())]
    return {n: (data, 'pak0.pak', {}) for n, data in entries}


class Quake2MapsTest(unittest.TestCase):
    def test_wal_extents_orientation_and_first_texture_frame(self):
        texture = read_wal(wal())
        self.assertEqual((texture['width'], texture['height'], texture['pixels']), (16, 16, bytes([42]) * 256))
        image = upload_texture(texture, PALETTE)
        self.assertEqual(image.getpixel((0, 0)), (84, 255, 14))  # intensity=2, then gamma=1.
        data = read_bsp(bsp(), files())
        face = face_data(data, 0)
        np.testing.assert_array_equal(face['size'], [3, 3])
        self.assertGreater(np.cross(face['points'][1] - face['points'][0], face['points'][2] - face['points'][0])[2], 0)
        data['texinfo'][0] = (1, 0, 0, -17, 0, 1, 0, -1, 0, 0)
        face = face_data(data, 0)
        np.testing.assert_array_equal(face['mins'], [-32, -16])
        np.testing.assert_array_equal(face['size'], [4, 4])
        with self.assertRaisesRegex(ValueError, 'lighting lump'):
            lightmap_rgb(face, data['lighting'])
        data['nexttexinfo'][0] = 1
        data['nexttexinfo'].append(0)
        data['texinfo'].append((*data['texinfo'][0][:8], 1, 0))
        data['textures'].append(dict(texture, name='later'))
        self.assertEqual(face_data(data, 0)['texture']['name'], 'test')  # R_TextureAnimation frame 0.

    def test_rgb_styles_normalization_and_switch_start_off(self):
        raw = bsp(styles=(0, 32, 255, 255), samples=((200, 100, 50), (200, 100, 50)))
        data = read_bsp(raw, files())
        face = face_data(data, 0)
        np.testing.assert_array_equal(lightmap_rgb(face, data['lighting'])[0, 0], [255, 127, 63])
        np.testing.assert_array_equal(lightmap_rgb(face, data['lighting'], {32})[0, 0], [200, 100, 50])
        data['entities'].append(dict(classname='light', style='32', spawnflags='1'))
        rendered = geometry(data, instances(data)[0])
        self.assertEqual(rendered['dark'], [32])
        self.assertIn((200, 100, 50), set(rendered['atlas'].getdata()))
        data['entities'][-1]['spawnflags'] = '513'  # The light itself is excluded on medium.
        self.assertEqual(geometry(data, instances(data)[0])['dark'], [])
        face['lightofs'] = -1
        self.assertTrue((lightmap_rgb(face, data['lighting']) == 255).all())
        raw = bsp(styles=(0, 1, 2, 3), samples=((10, 20, 30),) * 4)
        data = read_bsp(raw, files())
        self.assertEqual(lightmap_rgb(face_data(data, 0), data['lighting'])[0, 0].tolist(), [40, 80, 120])

    def test_surface_flags_warp_subdivision_and_sky_selection(self):
        for flags, kind, unlit, alpha in ((0, 'normal', False, 1), (FLOWING, 'normal', False, 1),
                (SKY | NODRAW, 'sky', True, 1), (WARP | FLOWING, 'turbulent', True, 1),
                (TRANS33, 'normal', True, .33), (TRANS66, 'normal', True, .66),
                (TRANS33 | TRANS66, 'normal', True, .33), (NODRAW, 'hidden', False, 1)):
            face = face_data(read_bsp(bsp(flags=flags), files()), 0)
            self.assertEqual((face['kind'], face['unlit'], face['alpha']), (kind, unlit, alpha))
            if unlit:
                self.assertEqual(lightmap_rgb(face, b'truncated').tolist(), [[[255, 255, 255]]])
        with self.assertRaisesRegex(ValueError, 'No visible'):
            data = read_bsp(bsp(flags=NODRAW), files())
            geometry(data, instances(data)[0])
        polygons = warp_polygons(np.array([[0., 0, 0], [256, 0, 0], [256, 128, 0], [0, 128, 0]]))
        self.assertGreater(len(polygons), 4)
        self.assertTrue(all(np.ptp(p[:, :2], axis=0).max() <= 64 for p in polygons))
        self.assertAlmostEqual(sum(np.linalg.norm(np.cross(p[1]-p[0], p[2]-p[0])) / 2 for p in polygons), 256 * 128)
        data = read_bsp(bsp(flags=WARP), files())
        data['texinfo'][0] = (1, 0, 0, 123, 0, 1, 0, -45, 0, WARP)
        warped = geometry(data, instances(data)[0])
        np.testing.assert_allclose(warped['uvs'], warped['points'][:, :2] / 16)  # Warp ignores texinfo offsets.
        assets = files()
        sky, images = skybox(dict(sky='unit1_', skyrotate='2', skyaxis='0 1 1'), assets)
        self.assertTrue(all(f['source'].endswith('.tga') and not f['fallback'] for f in sky['faces'].values()))
        self.assertEqual(next(iter(images.values())).getpixel((0, 0)), (40, 60, 80))  # Sky: no intensity upload.
        del assets['env/unit1_rt.tga']
        self.assertTrue(skybox({}, assets)[0]['faces']['rt']['fallback'])
        self.assertEqual((sky['rotate'], sky['axis']), (2, [0, 1, 1]))

    def test_brush_spawn_states(self):
        data = read_bsp(bsp(), files())
        data['models'] *= 20
        data['entities'] = [dict(classname='worldspawn')]
        cases = [dict(classname='func_door', model='*1', spawnflags='1', angle='90'),
                 dict(classname='func_door_rotating', model='*2', spawnflags='67', distance='45'),
                 dict(classname='func_plat', model='*3', height='40'),
                 dict(classname='func_plat', model='*4', targetname='lift'),
                 dict(classname='func_train', model='*5', target='corner'),
                 dict(classname='path_corner', targetname='corner', origin='100 200 300'),
                 dict(classname='func_wall', model='*6', spawnflags='1'),
                 dict(classname='func_wall', model='*7', spawnflags='5'),
                 dict(classname='func_explosive', model='*8', spawnflags='1'),
                 dict(classname='func_object', model='*9', spawnflags='1'),
                 dict(classname='func_water', model='*10', spawnflags='1', angle='-1'),
                 dict(classname='func_wall', model='*11', spawnflags='512'),
                 dict(classname='func_wall', model='*12', spawnflags='2048'),
                 dict(classname='trigger_once', model='*13'),
                 dict(classname='func_areaportal'), dict(classname='func_timer'),
                 dict(classname='func_killbox', model='*14'),
                 dict(classname='func_rotating', model='*15', angle='45'),
                 dict(classname='turret_base', model='*16', angle='45'),
                 dict(classname='func_wall', model='*17', angles='0 90 0')]
        data['entities'] += cases
        placed, skipped = instances(data)
        models = {p['model']: p for p in placed}
        self.assertAlmostEqual(models[1]['offset'][1], 26)  # size=34, lip=8.
        self.assertEqual(models[2]['angles'], [0, 0, -45])
        self.assertEqual(models[3]['offset'][2], -40)
        self.assertEqual(models[4]['offset'], [0, 0, 0])
        self.assertEqual(models[5]['offset'], [101, 201, 301])
        self.assertEqual(models[10]['offset'][2], 130)  # water lip=0.
        self.assertIn(12, models)  # NOT_DEATHMATCH has no effect on SP.
        self.assertEqual(models[15]['angles'], [0, 0, 0])
        self.assertEqual(models[16]['angles'], [0, 45, 0])
        self.assertEqual(models[17]['angles'], [0, 90, 0])
        self.assertEqual(len(skipped), 8)
        self.assertTrue(all(s['reason'] for s in skipped))

    def test_door_opens_when_the_start_is_inside_its_trigger_field(self):
        data = read_bsp(bsp(), files())
        data['models'] *= 3
        top = data['models'][1][3]  # Door's max x; the start stands 50 units past it, inside the 60-unit field.
        data['entities'] = [dict(classname='worldspawn'), dict(classname='info_player_start', origin=f'{top + 50} 16 16'),
                            dict(classname='func_door', model='*1', angle='-1'),
                            dict(classname='func_door', model='*2', angle='-1', targetname='button')]
        models = {p['model']: p for p in instances(data)[0]}
        self.assertGreater(models[1]['offset'][2], 0)  # Opened upwards.
        self.assertTrue(models[1]['state'].startswith('open'))
        self.assertEqual(models[2]['offset'], [0, 0, 0])  # A targeted door waits for its button.

    def test_viewpoint_selection_and_floor_drop(self):
        data = read_bsp(bsp(), files())
        data['entities'].insert(1, dict(classname='info_player_start', targetname='return', origin='16 16 200'))
        data['entities'] += [dict(classname='info_player_deathmatch', origin='16 16 20'),
                             dict(classname='info_player_intermission', origin='16 16 300', angles='10 90 5')]
        floors = geometry(data, instances(data)[0])['floors']
        views = viewpoints(data, floors)
        self.assertEqual([v['native_origin'][2] for v in views], [46, 46, 42, 300])
        self.assertEqual(views[0]['targetname'], '')
        self.assertEqual((views[-1]['yaw'], views[-1]['pitch'], views[-1]['roll']), (-90, -10, 5))
        self.assertTrue(all(v['native_origin'][2] <= v['authored_origin'][2] + 22 for v in views[:-1]))

    def test_invalid_data_and_import_routes(self):
        for data in (b'', wal()[:-1], wal()[:32] + struct.pack('<I', 0) + wal()[36:]):
            with self.assertRaises(ValueError):
                read_wal(data)
        for data in (b'', bsp()[:-1], b'IBSP' + struct.pack('<i', 29) + bsp()[8:]):
            with self.assertRaises(ValueError):
                read_bsp(data, files())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / 'baseq2'
            install.mkdir()
            entries = [(n, entry[0]) for n, entry in files().items()]
            (install / 'pak0.pak').write_bytes(pak(entries + [('maps/test.bsp', bsp())]))
            (install / 'pak1.pak').write_bytes(pak([('maps/test.bsp', bsp().replace(b'RGB map', b'PAK map'))]))
            (install / 'maps').mkdir()
            (install / 'maps/test.bsp').write_bytes(bsp().replace(b'RGB map', b'New map'))
            with patch('app.local_data_dir', root / 'out'):
                client = app.test_client()
                response = client.post('/import_quake2_maps', json={'path': str(install)})
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json['bsp_entries'], 3)
                self.assertEqual(response.json['imported'], ['test'])
                self.assertEqual([r['status'] for r in response.json['results']], ['overridden', 'overridden', 'imported'])
                with client.get('/quake2-map-data/maps/test/scene.json') as response:
                    self.assertEqual(response.json['title'], 'New map')
                    self.assertEqual(response.json['viewpoints'][0]['native_origin'][2], 46)
                    count, indices = response.json['vertices'], response.json['indices']
                with client.get('/quake2-map-data/maps/test/geometry.bin') as response:
                    self.assertEqual(len(response.data), count * 28 + indices * 4)
                with client.get('/maps/quake2/') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b'data-game="quake2"', response.data)
                self.assertEqual(client.post('/import_quake2_maps', json={}).status_code, 400)
                self.assertEqual(client.post('/import_quake2_maps', json={'path': str(install)},
                    headers={'Origin': 'https://elsewhere.invalid'}).status_code, 403)
                response = client.post('/import_quake2_maps', json={'path': str(install)})
                self.assertEqual(response.json['skipped'], ['maps/test.bsp'])


@unittest.skipUnless((INSTALL / 'pak0.pak').is_file() and (INSTALL / 'pak1.pak').is_file(), 'needs classic Quake II baseq2')
class Quake2MapsInstallTest(unittest.TestCase):
    def test_all_maps_and_player_floors(self):
        expected = []
        for pak_name in ('pak0.pak', 'pak1.pak', 'pak2.pak'):
            raw = (INSTALL / pak_name).read_bytes()
            offset, size = struct.unpack_from('<ii', raw, 4)
            names = [raw[i:i + 56].split(b'\0')[0].decode().lower() for i in range(offset, offset + size, 64)]
            expected.extend((pak_name, n) for n in names if n.endswith('.bsp'))
        self.assertEqual(len(expected), 47)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = import_maps(INSTALL, root)
            self.assertCountEqual([(r['pak'], r['source']) for r in report['results']], expected)
            self.assertEqual((len(report['imported']), len(report['skipped']), report['failed']), (47, 0, {}))
            count = 0
            for name in report['imported']:
                scene = json.loads((root / 'maps' / name / 'scene.json').read_text())
                self.assertFalse(scene['missing'], name)
                self.assertEqual((root / 'maps' / name / 'geometry.bin').stat().st_size,
                                 scene['vertices'] * 28 + scene['indices'] * 4)
                self.assertTrue(all((root / 'textures' / g['texture']).is_file() for g in scene['groups']))
                self.assertTrue(all((root / 'textures' / f['texture']).is_file() for f in scene['skybox']['faces'].values()))
                for view in scene['viewpoints']:
                    if view['classname'] == 'info_player_intermission':
                        self.assertEqual(view['native_origin'], view['authored_origin'])
                        continue
                    count += 1
                    self.assertIsNotNone(view['floor_z'], (name, view))
                    self.assertLessEqual(view['eye_above_floor'], 46.0001, (name, view))
                    self.assertLessEqual(view['native_origin'][2], view['authored_origin'][2] + 22.0001)
                starts = [v for v in scene['viewpoints'] if v['classname'] == 'info_player_start' and not v['targetname']]
                if starts:
                    self.assertEqual(scene['viewpoints'][0], starts[0])
            self.assertEqual(count, 628)


if __name__ == '__main__':
    unittest.main()

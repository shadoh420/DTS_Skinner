"""BSP29 geometry, software palette lighting, spawn positions and complete installed-map accounting."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app import app
from tools.import_quake import import_catalog
from tools.import_quake_map import (brightness_curve, build_item, build_map, colormap_image, face_data, import_maps,
                                   geometry, instances, light_codes, read_bsp, surface_kind, viewpoints)
from tests.test_quake import INSTALL, PALETTE, pak


def bsp(names=('stone',), styles=(0, 255, 255, 255), values=(64,), side=0):
    lumps = [b''] * 15
    lumps[0] = b'{"classname" "worldspawn" "message" "Synthetic map"}\n{"classname" "info_player_start" "origin" "16 16 24" "angle" "90"}\0'
    lumps[1] = struct.pack('<4fi', 0, 0, 1, 0, 2)
    textures, offsets = b'', []
    for name in names:
        width, height = (256, 128) if name.startswith('sky') else (16, 16)
        offsets.append(4 + 4 * len(names) + len(textures))
        textures += struct.pack('<16s6I', name.encode(), width, height, 40, 0, 0, 0) + bytes([42]) * width * height
    lumps[2] = struct.pack('<i', len(names)) + struct.pack('<' + 'i' * len(names), *offsets) + textures
    lumps[3] = struct.pack('<12f', 0, 0, 0, 32, 0, 0, 32, 32, 0, 0, 32, 0)
    lumps[6] = struct.pack('<8fii', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0)
    lumps[7] = struct.pack('<Hhihh4Bi', 0, side, 0, 4, 0, *styles, 0)
    lumps[8] = b''.join(bytes([value]) * 9 for value in values)
    lumps[12] = struct.pack('<10H', 0, 0, 0, 1, 1, 2, 2, 3, 3, 0)
    lumps[13] = struct.pack('<4i', -4, -3, -2, -1)  # Clockwise: repaired against the plane normal.
    lumps[14] = struct.pack('<9f7i', 0, 0, 0, 32, 32, 64, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1)
    header, body = struct.pack('<i', 29), b''
    for lump in lumps:
        header += struct.pack('<ii', 124 + len(body), len(lump))
        body += lump
    return header + body


class QuakeMapsTest(unittest.TestCase):
    def test_pack_map_overrides_palette_fallback_and_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for game in ('id1', 'hipnotic', 'rogue'):
                (root / game).mkdir()
            table = bytes(range(256)) * 64
            (root / 'id1/PAK0.PAK').write_bytes(pak([('gfx/palette.lmp', PALETTE), ('gfx/colormap.lmp', table),
                                                   ('maps/start.bsp', bsp()), ('maps/base.bsp', bsp())]))
            (root / 'hipnotic/pak0.pak').write_bytes(pak([('maps/start.bsp', bsp().replace(b'Synthetic map', b'Hipnotic test'))]))
            alternate = bytes([19, 27, 35]) * 256
            (root / 'rogue/pak0.pak').write_bytes(pak([('gfx/palette.lmp', alternate), ('gfx/colormap.lmp', table[::-1]),
                                                    ('maps/start.bsp', bsp().replace(b'Synthetic map', b'Rogue testing')),
                                                    ('maps/b_test.bsp', bsp())]))
            with patch('app.local_data_dir', root):
                client = app.test_client()
                for game, title, total, palette_source in (('quake', 'Synthetic map', 2, 'quake'),
                                                          ('hipnotic', 'Hipnotic test', 1, 'quake'),
                                                          ('rogue', 'Rogue testing', 2, 'rogue')):
                    response = client.post('/import_quake_maps', json=dict(path=str(root), game=game))
                    self.assertEqual(response.status_code, 200, response.json)
                    self.assertEqual(response.json['bsp_entries'], total)
                    self.assertEqual(response.json['palette_source'], palette_source)
                    self.assertEqual(response.json['colormap_source'], palette_source)
                    prefix = '' if game == 'quake' else game + '/'
                    with client.get('/quake-map-data/' + prefix + 'maps/start/scene.json') as scene:
                        self.assertEqual(scene.json['title'], title)
                        self.assertEqual(scene.json['game'], game)
                    with client.get('/quake-map-data/' + prefix + 'index.json') as index:
                        self.assertEqual(len(index.json), 2 if game == 'quake' else 1)
                for game in ('rerelease', '../id1', [], None):
                    self.assertEqual(client.post('/import_quake_maps', json=dict(path=str(root), game=game)).status_code, 422)

    def test_mission_pack_brush_spawn_rules(self):
        data = read_bsp(bsp())
        data['models'] *= 10
        data['entities'] += [dict(classname='info_rotate', targetname='pivot', origin='100 200 300'),
                             dict(classname='rotate_object', model='*1', target='pivot', targetname='rot', origin='100 200 300'),
                             dict(classname='func_rotate_train', target='rot', path='first', origin='100 200 300'),
                             dict(classname='path_rotate', targetname='first', origin='110 220 330', angles='0 90 0', spawnflags='2'),
                             dict(classname='func_movewall', model='*2', targetname='rot'),
                             dict(classname='func_movewall', model='*3', targetname='rot', spawnflags='1'),
                             dict(classname='func_togglewall', model='*4'),
                             dict(classname='func_particlefield', model='*5'),
                             dict(classname='func_multi_exploder', model='*6'),
                             dict(classname='path_follow', model='*7'),
                             dict(classname='monster_shambler', model='*8')]
        placed, skipped = instances(data, 'hipnotic')
        self.assertEqual([p['model'] for p in placed], [0, 1, 3])
        self.assertEqual(len(skipped), 6)
        self.assertEqual(placed[1]['offset'], [110, 220, 330])
        self.assertEqual(placed[1]['angles'], [0, 90, 0])
        self.assertEqual(placed[2]['offset'], [10, 20, 30])
        points = geometry(data, placed[1:2], light=False)['points']
        self.assertTrue(((points[:, 0] >= 78) & (points[:, 0] <= 110)).all())
        self.assertTrue(((points[:, 1] >= 220) & (points[:, 1] <= 252)).all())
        data['entities'][4]['classname'] = 'func_rotate_door'
        placed, _ = instances(data, 'hipnotic')
        self.assertEqual(placed[1]['offset'], [100, 200, 300])  # Compiled pivot added exactly once.
        self.assertEqual(placed[1]['angles'], [0, 0, 0])
        data['entities'] = [dict(classname='func_new_plat', model='*1', height=h, spawnflags=f)
                            for h, f in (('40', '16'), ('-40', '16'), ('0', '16'), ('-40', '4'), ('-40', '1'))]
        data['entities'] += [dict(classname='func_ctf_wall', model='*2'), dict(classname='func_elvtr_button', model='*3'),
                             dict(classname='unknown_brush', model='*4', origin='7 8 9')]
        placed, skipped = instances(data, 'rogue')
        self.assertEqual([p['offset'][2] for p in placed[1:6]], [0, -40, -58, 0, -40])
        self.assertEqual(len(skipped), 1)
        self.assertTrue(placed[-1]['guess'])
        self.assertEqual(placed[-1]['offset'], [7, 8, 9])

    def test_extents_winding_and_multiple_styles(self):
        data = read_bsp(bsp(styles=(0, 3, 255, 255), values=(64, 32)))
        face = face_data(data, 0)
        np.testing.assert_array_equal(face['size'], [3, 3])
        self.assertGreater(np.cross(face['points'][1] - face['points'][0], face['points'][2] - face['points'][0])[2], 0)
        codes = light_codes(face, data['lighting'])
        self.assertEqual(codes.shape, (3, 3))
        self.assertTrue((codes == (65280 - 96 * 264) >> 2).all())
        # A switchable light that starts off adds nothing (style 'a').
        self.assertTrue((light_codes(face, data['lighting'], frozenset({3})) == (65280 - 64 * 264) >> 2).all())
        # Off-axis/non-integral minima use floor, maxima use ceil, not rounded sizes.
        data['texinfo'][0] = (1, 0, 0, -17, 0, 1, 0, -1, 0, 0)
        face = face_data(data, 0)
        np.testing.assert_array_equal(face['mins'], [-32, -16])
        np.testing.assert_array_equal(face['size'], [4, 4])
        with self.assertRaisesRegex(ValueError, 'lighting lump'):
            light_codes(face, data['lighting'])
        reverse = face_data(read_bsp(bsp(side=1)), 0)
        self.assertLess(np.cross(reverse['points'][1] - reverse['points'][0], reverse['points'][2] - reverse['points'][0])[2], 0)

    def test_palette_lookup_special_surfaces_and_first_animation_frame(self):
        table = bytearray(bytes(range(256)) * 64)
        table[10 * 256 + 42] = 17
        image = colormap_image(table, PALETTE)
        self.assertEqual(image.getpixel((42, 10)), (17, 238, 7))
        self.assertTrue(brightness_curve(table, PALETTE)['fullbrights_unchanged'])
        for name, expected in (('sky1', 'sky'), ('*water', 'turbulent'), ('*lava', 'turbulent'),
                               ('*slime', 'turbulent'), ('*teleport', 'turbulent'), ('clip', 'hidden'),
                               ('trigger', 'hidden'), ('stone', 'normal')):
            self.assertEqual(surface_kind(name), expected)
            face = face_data(read_bsp(bsp(names=(name,))), 0)
            if expected in ('sky', 'turbulent'):
                self.assertEqual(light_codes(face, b'bad').tolist(), [[8192]])
        self.assertEqual(face_data(read_bsp(bsp(names=('+1foo', '+0foo'))), 0)['texture']['name'], '+0foo')
        self.assertEqual(face_data(read_bsp(bsp(names=('+bfoo', '+afoo'))), 0)['texture']['name'], '+afoo')

    def test_brush_spawn_positions_and_viewpoints(self):
        data = read_bsp(bsp())
        data['models'] *= 7
        data['entities'] += [
            dict(classname='func_plat', model='*1'),
            dict(classname='func_plat', model='*2', targetname='lift'),
            dict(classname='func_plat', model='*3', height='40'),
            dict(classname='func_door', model='*4', angle='90', spawnflags='1'),
            dict(classname='func_train', model='*5', target='first'),
            dict(classname='path_corner', targetname='first', origin='100 200 300'),
            dict(classname='trigger_once', model='*6'),
            dict(classname='func_wall', model='*6', spawnflags='512')]
        placed, skipped = instances(data)
        self.assertEqual([p['offset'][2] for p in placed[:4]], [0, -58, 0, -40])
        np.testing.assert_allclose(placed[4]['offset'], [0, 26, 0], atol=1e-10)
        self.assertEqual(placed[5]['offset'], [101, 201, 301])
        self.assertEqual(len(skipped), 2)
        view = viewpoints(data)[0]
        self.assertEqual((view['native_origin'], view['yaw']), ([16, 16, 46], -90))
        data['entities'] = [dict(classname='info_intermission', origin='1 2 3', mangle='20 240 0'),
                            dict(classname='info_player_deathmatch', origin='4 5 6', angle='180')]
        self.assertEqual(viewpoints(data)[0]['native_origin'], [4, 5, 28])
        self.assertEqual(viewpoints(data)[1]['native_origin'], [1, 2, 3])
        self.assertEqual((viewpoints(data)[1]['pitch'], viewpoints(data)[1]['yaw']), (-20, -240))
        data['entities'][0]['mangle'] = '20'  # The shipped Rogue r1m4 uses a short vector.
        self.assertEqual((viewpoints(data)[1]['pitch'], viewpoints(data)[1]['yaw']), (-20, 0))

    def test_binary_atlas_item_and_routes(self):
        scene, blob, textures, atlas = build_map(bsp(), 'test')
        self.assertEqual(scene['title'], 'Synthetic map')
        self.assertEqual(len(blob), scene['vertices'] * 28 + scene['indices'] * 4)
        self.assertEqual(scene['viewpoint_checks'][0]['eye_above_floor'], 46)
        coords = np.frombuffer(blob, '<f4', scene['vertices'] * 2, scene['vertices'] * 20).reshape(-1, 2)
        self.assertTrue(((coords > 0) & (coords < 1)).all())
        first = (coords[0] * atlas.size).astype(int)
        r, g, _ = atlas.getpixel(tuple(first))
        self.assertEqual(r * 256 + g, (65280 - 64 * 264) >> 2)
        item, images = build_item(bsp(), PALETTE, 'b_test_bsp')
        self.assertEqual(item['vertices'][1], 0)  # Native Z is viewer up.
        self.assertEqual(len(item['indices']), 6)
        self.assertTrue(images)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            install = root / 'id1'
            install.mkdir()
            (install / 'PAK0.PAK').write_bytes(pak([('gfx/palette.lmp', PALETTE), ('gfx/colormap.lmp', bytes(range(256)) * 64),
                                                   ('maps/test.bsp', bsp()), ('maps/b_test.bsp', bsp())]))
            with patch('app.local_data_dir', root):
                client = app.test_client()
                with client.get('/maps/quake/') as page:
                    self.assertEqual(page.status_code, 200)
                    self.assertIn(b'/static/unreal-maps/viewer.js', page.data)
                response = client.post('/import_quake_maps', json={'path': str(install)})
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json['imported'], ['test'])
                self.assertEqual(len(response.json['skipped']), 1)
                with client.get('/quake-map-data/index.json') as index:
                    self.assertEqual(index.json[0]['title'], 'Synthetic map')
                self.assertEqual(client.post('/import_quake_maps', json={'path': ''}).status_code, 400)
                self.assertEqual(client.post('/import_quake_maps', json={'path': str(install)}, headers={'Origin': 'https://foreign.invalid'}).status_code, 403)
            models = import_catalog(install, root / 'models')
            self.assertEqual((models['ready'], models['bsp_items']), (1, 1))
            repeated = import_maps(install, root / 'quake-maps')
            self.assertEqual(repeated['imported'], [])
            self.assertEqual(len(repeated['skipped']), 2)
        for raw in (b'', bsp()[:124], struct.pack('<i', 30) + bsp()[4:]):
            with self.assertRaises(ValueError):
                read_bsp(raw)


@unittest.skipUnless((INSTALL / 'id1/PAK0.PAK').is_file() and (INSTALL / 'id1/PAK1.PAK').is_file(),
                     'needs the classic registered Quake install')
class QuakeMapsInstallTest(unittest.TestCase):
    def check_pack(self, game, count, boxes):
        raw = (INSTALL / game / 'pak0.pak').read_bytes()
        offset, size = struct.unpack_from('<ii', raw, 4)
        expected = [raw[i:i + 56].split(b'\0')[0].decode().lower() for i in range(offset, offset + size, 64)
                    if raw[i:i + 56].split(b'\0')[0].endswith(b'.bsp')]
        self.assertEqual(len(expected), count)
        with tempfile.TemporaryDirectory() as folder:
            result = import_maps(INSTALL, Path(folder), game=game)
            self.assertEqual(result['failed'], {})
            self.assertEqual((len(result['imported']), len(result['skipped'])), (count - boxes, boxes))
            self.assertEqual(result['pak_counts']['pak0.pak']['directory'], size // 64)
            self.assertCountEqual([r['source'] for r in result['results']], expected)
            self.assertTrue(all(r.get('reason') for r in result['results'] if r['status'] != 'imported'))
            for ident in result['imported']:
                root = Path(folder) / game
                scene = json.loads((root / 'maps' / ident / 'scene.json').read_text())
                blob = (root / 'maps' / ident / 'geometry.bin').read_bytes()
                self.assertTrue(np.isfinite(np.frombuffer(blob, '<f4', scene['vertices'] * 7)).all(), ident)
                self.assertTrue(scene['viewpoint_checks'][0]['inside_bounds'], ident)
                self.assertTrue(0 < scene['viewpoint_checks'][0]['eye_above_floor'] <= 46, ident)  # Dropped to the floor as in the game.
                self.assertEqual(scene['missing'], [], ident)
                self.assertTrue(all((root / 'textures' / g['texture']).is_file() for g in scene['groups']), ident)
                self.assertTrue(all(min(f['luxels']) > 0 for f in scene['faces']), ident)

    @unittest.skipUnless((INSTALL / 'hipnotic/pak0.pak').is_file(), 'needs Scourge of Armagon')
    def test_hipnotic_directory_and_geometry(self):
        self.check_pack('hipnotic', 18, 0)

    @unittest.skipUnless((INSTALL / 'rogue/pak0.pak').is_file(), 'needs Dissolution of Eternity')
    def test_rogue_directory_and_geometry(self):
        self.check_pack('rogue', 23, 6)

    def test_full_directory_count_and_geometry(self):
        expected = []
        for name in ('PAK0.PAK', 'PAK1.PAK'):
            raw = (INSTALL / 'id1' / name).read_bytes()
            offset, size = struct.unpack_from('<ii', raw, 4)
            expected.extend((name.lower(), raw[i:i + 56].split(b'\0')[0].decode().lower())
                            for i in range(offset, offset + size, 64) if raw[i:i + 56].split(b'\0')[0].endswith(b'.bsp'))
        self.assertEqual(len(expected), 51)
        self.assertEqual(sum(n.startswith('maps/b_') for _, n in expected), 13)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = import_maps(INSTALL, root)
            self.assertEqual(result['failed'], {})
            self.assertEqual((len(result['imported']), len(result['skipped'])), (38, 13))
            self.assertCountEqual([(r['pak'], r['source']) for r in result['results']], expected)
            for ident in result['imported']:
                scene = json.loads((root / 'maps' / ident / 'scene.json').read_text())
                blob = (root / 'maps' / ident / 'geometry.bin').read_bytes()
                floats = np.frombuffer(blob, '<f4', scene['vertices'] * 7)
                self.assertTrue(np.isfinite(floats).all(), ident)
                self.assertTrue(scene['viewpoint_checks'][0]['inside_bounds'], ident)
                self.assertIsNotNone(scene['viewpoint_checks'][0]['floor_z'], ident)
                self.assertTrue(0 < scene['viewpoint_checks'][0]['eye_above_floor'] <= 46, ident)  # Dropped to the floor as in the game.
                self.assertEqual(scene['missing'], ['__missing_miptex_46'] if ident == 'e2m3' else [], ident)
                self.assertTrue(all((root / 'textures' / g['texture']).is_file() for g in scene['groups']), ident)


if __name__ == '__main__':
    unittest.main()

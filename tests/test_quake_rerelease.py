"""Rerelease format, layering, spawn and complete installed-PAK accounting checks."""
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
from tools.import_quake import RERELEASE, import_catalog, read_install, read_pak
from tools.import_quake_map import (build_map, colored_lighting, face_data, import_maps, instances,
                                   light_rgb, read_bsp, rerelease_sky, viewpoints)
from tests.test_quake import INSTALL, PALETTE, mdl, pak
from tests.test_quake_maps import bsp


def extended_bsp(magic=b'BSP2', high=False, bspx=None):
    raw = bsp()
    lumps = [raw[o:o + s] for o, s in struct.iter_unpack('<ii', raw[4:124])]
    bound_fmt = '6f' if magic == b'BSP2' else '6h'
    bounds = (-1.5, -2.5, -3.5, 32.5, 33.5, 64.5) if magic == b'BSP2' else (-1, -2, -3, 32, 33, 64)
    # Exercise 32-bit node/leaf ranges and negative content children, not just a BSP29 with a new magic.
    lumps[5] = struct.pack('<3i' + bound_fmt + '2I', 0, -1, 70000, *bounds, 70001, 1)
    lumps[9] = struct.pack('<3i', 0, -2, 70002)
    lumps[10] = struct.pack('<2i' + bound_fmt + '2I4B', -1, -1, *bounds, 70003, 1, 0, 0, 0, 0)
    lumps[11] = struct.pack('<I', 70004)
    lumps[7] = struct.pack('<5i4Bi', 0, 0, 0, 4, 0, 0, 255, 255, 255, 0)
    shift = 65536 if high else 0
    lumps[3] = b'\0' * (shift * 12) + lumps[3]
    lumps[12] = struct.pack('<10I', *(v + shift for row in struct.iter_unpack('<2H', lumps[12]) for v in row))
    body, header = b'', magic
    for lump in lumps:
        header += struct.pack('<ii', 124 + len(body), len(lump))
        body += lump
    raw = header + body
    if bspx:
        raw += b'\0' * (-len(raw) % 4)
        directory, payload = b'', b''
        for name, data in bspx.items():
            directory += struct.pack('<24sii', name.encode(), len(raw) + 8 + 32 * len(bspx) + len(payload), len(data))
            payload += data
        raw += b'BSPX' + struct.pack('<i', len(bspx)) + directory + payload
    return raw


class RereleaseTest(unittest.TestCase):
    def test_bsp2_and_2psb_wide_indices_and_bounds(self):
        reference = build_map(bsp(), 'reference')
        for magic in (b'BSP2', b'2PSB'):
            raw = extended_bsp(magic, high=True)
            data = read_bsp(raw)
            self.assertEqual(data['nodes'][0][1:3], (-1, 70000))
            self.assertEqual(data['nodes'][0][-2:], (70001, 1))
            self.assertEqual(data['clipnodes'][0], (0, -2, 70002))
            self.assertEqual(data['leaves'][0][8:10], (70003, 1))
            self.assertEqual(data['marksurfaces'], [70004])
            self.assertEqual(data['nodes'][0][3], -1.5 if magic == b'BSP2' else -1)
            scene, blob, _, atlas = build_map(raw, 'wide')
            self.assertEqual(blob, reference[1])
            self.assertEqual(atlas.tobytes(), reference[3].tobytes())
            self.assertEqual(scene['format'], 'quake-' + magic.decode().lower())
        raw = bytearray(extended_bsp())
        struct.pack_into('<i', raw, 4 + 7 * 8 + 4, 27)
        with self.assertRaisesRegex(ValueError, 'Incomplete BSP lump 7'):
            read_bsp(raw)

    def test_lit_styles_start_off_cap_and_validation(self):
        data = read_bsp(bsp(styles=(0, 32, 255, 255), values=(0, 0)))
        lit = b'QLIT\x01\0\0\0' + bytes([64, 128, 255]) * 9 + bytes([32, 0, 255]) * 9
        self.assertEqual(colored_lighting(data, lit), '.lit')
        face = face_data(data, 0)
        self.assertEqual(light_rgb(face, data['rgb_lighting'])[0, 0].tolist(), [99, 132, 255])
        self.assertEqual(light_rgb(face, data['rgb_lighting'], {32})[0, 0].tolist(), [66, 132, 255])
        data = read_bsp(bsp(styles=(32, 255, 255, 255), values=(64,)))
        data['entities'].append(dict(classname='light', style='32', spawnflags='1'))
        colored_lighting(data, b'QLIT\x01\0\0\0' + bytes([64, 128, 255]) * 9)
        from tools.import_quake_map import geometry
        self.assertEqual(np.asarray(geometry(data, instances(data)[0])['atlas']).max(), 0)
        for bad in (b'', lit[:-1], b'QLIT\x02\0\0\0' + lit[8:], lit + b'\0'):
            with self.assertRaises(ValueError):
                colored_lighting(data, bad)
        scene, _, _, _ = build_map(extended_bsp(), 'rgb', lit=b'QLIT\x01\0\0\0' + bytes([64, 128, 255]) * 9)
        self.assertEqual((scene['lighting'], scene['lighting_source']), ('rgb', '.lit'))
        self.assertEqual(build_map(bsp(), 'mono')[0]['lighting'], 'colormap')

    def test_native_float_extents_and_authored_numeric_values(self):
        from tools.import_quake_map import vector
        data = read_bsp(bsp())
        data['rerelease'] = True
        data['points'] = [(440, -248, -34), (440, 32, -34), (160, 32, -34), (160, -248, -34)]
        data['texinfo'][0] = (*struct.unpack('<8f', struct.pack('<8f', -.8, 0, 0, 0, 0, .8, 0, 0)), 0, 0)
        self.assertEqual(face_data(data, 0)['size'].tolist(), [15, 16])
        np.testing.assert_array_equal(vector({'mangle': '-15, 190, 0'}, 'mangle'), [-15, 190, 0])
        data['models'] *= 2
        for flags in ('', '1.5'):
            data['entities'] = [dict(classname='func_wall', model='*1', spawnflags=flags)]
            self.assertEqual(len(instances(data, 'mg1')[0]), 2)

    def test_bspx_accounting_rgb_and_unhandled(self):
        raw = extended_bsp(bspx={'RGBLIGHTING': bytes([32, 64, 128]) * 9, 'LMSHIFT': b'\x04'})
        scene = build_map(raw, 'bspx')[0]
        self.assertEqual(scene['lighting_source'], 'BSPX RGBLIGHTING')
        self.assertEqual([(b['name'], b['handled']) for b in scene['bspx']], [('RGBLIGHTING', True), ('LMSHIFT', False)])
        lit = b'QLIT\x01\0\0\0' + bytes([128, 64, 32]) * 9
        self.assertEqual(build_map(raw, 'external', lit=lit)[0]['lighting_source'], '.lit')
        with self.assertRaisesRegex(ValueError, 'BSPX'):
            read_bsp(raw[:-1])

    def test_separate_layers_routes_and_addon_model_categories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for path in ('id1', 'rerelease/id1', 'rerelease/mg1', 'rerelease/mg3'):
                (root / path).mkdir(parents=True)
            base = [('gfx/palette.lmp', PALETTE), ('gfx/colormap.lmp', bytes(range(256)) * 64), ('progs/player.mdl', mdl())]
            (root / 'id1/pak0.pak').write_bytes(pak(base + [('progs.dat', b'classic')]))
            rrpalette = bytes([9, 17, 25]) * 256
            (root / 'rerelease/id1/pak0.pak').write_bytes(pak(base + [('gfx/palette.lmp', rrpalette), ('progs.dat', b'rerelease')]))
            (root / 'rerelease/mg1/pak0.pak').write_bytes(pak([('maps/start.bsp', extended_bsp()), ('progs/player.mdl', mdl()), ('progs.dat', b'mg1')]))
            (root / 'rerelease/mg3/pak0.pak').write_bytes(pak([('maps/start.bsp', bsp()), ('progs/axe.mdl', mdl())]))
            for path in (root, root / 'rerelease', root / 'rerelease/mg1'):
                self.assertEqual(read_install(path, 'quake')[0]['progs.dat'][0], b'classic')
                self.assertEqual(read_install(path, 'mg1')[0]['progs.dat'][0], b'mg1')
                self.assertEqual(read_install(path, 'mg3')[0]['progs.dat'][0], b'rerelease')
                self.assertEqual(read_install(path, 'mg1')[0]['gfx/palette.lmp'][0], rrpalette)
            with patch('app.local_data_dir', root / 'output'):
                client = app.test_client()
                for game in ('mg1', 'mg3'):
                    response = client.post('/import_quake_maps', json=dict(path=str(root), game=game))
                    self.assertEqual(response.status_code, 200, response.json)
                    self.assertEqual(response.json['imported'], ['start'])
                    with client.get(f'/quake-map-data/{game}/maps/start/scene.json') as loaded:
                        self.assertEqual(loaded.json['game'], game)
            result = import_catalog(root, root / 'models')
            self.assertEqual(result['ready'], 3)  # Add-on MDLs stay in their own categories, even identical ones.
            rows = json.loads((root / 'models/catalog.json').read_text())
            self.assertEqual({r['campaign'] for r in rows}, {'quake', 'mg1', 'mg3'})
            with Image.open(root / 'models/textures/mg1_player.png') as image:
                self.assertEqual(image.getpixel((0, 0)), (9, 17, 25))

    def test_brush_rules_and_ctf_viewpoint(self):
        data = read_bsp(bsp())
        data['models'] *= 10
        data['entities'] += [dict(classname='rotate_object_continuously', model='*1', origin='1 2 3', pos2='4 5 6'),
                             dict(classname='func_bob', model='*2'), dict(classname='func_toss', model='*3'),
                             dict(classname='func_breakable', model='*4'), dict(classname='func_axe_button', model='*5'),
                             dict(classname='func_door', model='*6', spawnflags='32768'),
                             dict(classname='unknown', model='*7', origin='7 8 9'),
                             dict(classname='func_wall', model='*8', spawnflags='512'),
                             dict(classname='func_wall', model='*9', spawnflags='2048')]
        placed, skipped = instances(data, 'mg3')
        self.assertEqual(placed[1]['offset'], [4, 5, 6])
        self.assertEqual(len(skipped), 2)
        self.assertEqual([p['classname'] for p in placed if p.get('guess')], ['unknown'])
        self.assertIn(6, [p['model'] for p in instances(data, 'mg1')[0]])  # mg1 doors do not call RemovedOutsideCoop.
        placed, skipped = instances(data, 'qctf')
        self.assertIn(8, [p['model'] for p in placed])
        self.assertNotIn(9, [p['model'] for p in placed])
        data['entities'] += [dict(classname='info_player_team2', origin='3 4 24'),
                             dict(classname='info_player_team1', origin='5 6 24', angle='90')]
        views = viewpoints(data, 'qctf')
        self.assertEqual(views[0]['classname'], 'info_player_team1')
        self.assertEqual(views[0]['native_origin'], [5, 6, 46])
        data['entities'] = [dict(classname='func_bossgate', model='*1', spawnflags='95'),
                            dict(classname='func_bossgate', model='*2', spawnflags='31')]
        gates, skipped = instances(data, 'mg1')
        self.assertEqual([p['model'] for p in gates], [0, 2])
        self.assertFalse(gates[1]['guess'])
        self.assertIn('inverse boss gate', skipped[0]['reason'])
        data['entities'] = [dict(classname='info_player_start', origin='1 2 24', angle='')]
        self.assertEqual(viewpoints(data, 'qextras')[0]['yaw'], 0)  # vault/jrbase5 has an empty angle.

    def test_sky_lookup_and_fallback(self):
        blob = io.BytesIO()
        Image.new('RGB', (16, 16), '#123456').save(blob, format='TGA')
        files = {f'gfx/env/start/start_{side}.tga': (blob.getvalue(), '', {}) for side in ('rt', 'bk', 'lf', 'ft', 'up', 'dn')}
        sky, images = rerelease_sky({'_sky': 'start/start_'}, files)
        self.assertEqual(sky['missing'], [])
        self.assertEqual(len(sky['faces']), 6)
        self.assertTrue(all(v['source'].startswith('gfx/env/start/') for v in sky['faces'].values()))
        self.assertEqual(next(iter(images.values())).getpixel((0, 0)), (18, 52, 86))
        self.assertEqual(rerelease_sky({}, files), (None, {}))
        self.assertEqual(len(rerelease_sky({'_sky': 'missing_'}, files)[0]['missing']), 6)


@unittest.skipUnless((INSTALL / 'rerelease/id1/pak0.pak').is_file(), 'needs Quake rerelease')
class RereleaseInstallTest(unittest.TestCase):
    def test_classic_rerelease_pak_counts(self):
        for game, count in (('hipnotic', 18), ('rogue', 23)):
            files = read_pak((INSTALL / 'rerelease' / game / 'pak0.pak').read_bytes())
            maps = [(n, d) for n, d in files if n.endswith('.bsp')]
            self.assertEqual(len(maps), count)
            self.assertTrue(all(read_bsp(d)['format'] == 'quake-bsp29' for _, d in maps))

    def test_all_new_map_pak_entries(self):
        # Counts include archived BSPs and external brush models, not just playable maps.
        for game, total, levels in (('qextras', 81, 15), ('dopa', 13, 13), ('mg1', 25, 25), ('mg3', 22, 20), ('qctf', 31, 9)):
            with self.subTest(game=game), tempfile.TemporaryDirectory() as folder:
                raw = (INSTALL / 'rerelease' / RERELEASE[game] / 'pak0.pak').read_bytes()
                expected = [n for n, _ in read_pak(raw) if n.endswith('.bsp')]
                self.assertEqual(len(expected), total)
                result = import_maps(INSTALL, folder, game=game)
                self.assertEqual(result['failed'], {})
                self.assertEqual(len(result['imported']), levels)
                self.assertEqual(len(result['skipped']), total - levels)
                self.assertCountEqual([r['source'] for r in result['results']], expected)
                self.assertTrue(all(r.get('reason') for r in result['results'] if r['status'] != 'imported'))
                for ident in result['imported']:
                    root = Path(folder) / game
                    scene = json.loads((root / 'maps' / ident / 'scene.json').read_text())
                    blob = (root / 'maps' / ident / 'geometry.bin').read_bytes()
                    self.assertEqual(len(blob), scene['vertices'] * 28 + scene['indices'] * 4)
                    self.assertTrue(np.isfinite(np.frombuffer(blob, '<f4', scene['vertices'] * 7)).all())
                    self.assertTrue(all((root / 'textures' / g['texture']).is_file() for g in scene['groups']))
                    normals = {'boss', 'boss2', 'hub', 'map1', 'map2b', 'map3', 'map4', 'map5', 'map6',
                               'map7', 'map8', 'secret2', 'secret3', 'secret5'}
                    expected_bspx = ['FACENORMALS'] if game == 'mg3' and ident in normals else []
                    self.assertEqual([b['name'] for b in scene['bspx']], expected_bspx)
                    self.assertTrue(all(not b['handled'] and b['reason'] == 'not handled' for b in scene['bspx']))
                    if game == 'qctf':
                        self.assertEqual(scene['viewpoints'][0]['classname'], 'info_player_team1')

    def test_addon_mdls_and_classic_rerelease_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            result = import_catalog(INSTALL, folder)
            for game, count in (('mg1', 8), ('mg3', 54), ('qctf', 4)):
                records = [r for r in result['results'] if r['game'] == game]
                self.assertEqual(len(records), count)
                self.assertTrue(all(r['status'] == 'ready' for r in records))
                self.assertEqual(result['pak_counts'][f'{game}/pak0.pak']['mdl'], count)
            self.assertEqual(result['mdl_skipped'], 0)
            for game, count in (('id1', 79), ('hipnotic', 23), ('rogue', 64)):
                compared = result['rerelease_comparison'][game]
                self.assertEqual(compared['total'], count)
                self.assertEqual(compared['different'] + compared['identical'] + len(compared['added']), count)
            self.assertFalse(any(r['game'] in ('qextras', 'dopa') for r in result['results']))


if __name__ == '__main__':
    unittest.main()

"""Synthetic SiN RBSP1 maps; no retail data or install-wide test."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app import app
from tests.test_quake2_maps import bsp as bsp38, files as q2_files
from tests.test_sin import spak, swl, tga, sbm, sam, definition
from tools import import_sin as sin, import_sin_map as maps, import_quake2_map as q2


def bsp(flags=0, translucence=0., entities=None, styles=None, edits=None):
    raw = bsp38(entity_lump=entities)
    lumps = [raw[o:o + n] for o, n in struct.iter_unpack('<ii', raw[8:160])] + [b'']
    lumps[5] = struct.pack('<' + maps.FORMATS[5], 1, 0, 0, 0, 0, 1, 0, 0, flags, b'test', -1,
                           .5, 0, 0, .1, 1, translucence, 12, 90, 1, 1, 1, b'test_surface')
    styles = styles or [0] + [255] * 15
    lumps[6] = struct.pack('<' + maps.FORMATS[6], 0, 0, 0, 4, 0, *styles, 0, -1)
    lumps[7] = bytes([64, 128, 32]) * 9 * sum(s != 255 for s in styles)
    lumps[19] = struct.pack('<i6f32s', 64, 1, 1, 1, 0, 90, 32, b'test_surface')
    for i, data in (edits or {}).items(): lumps[i] = data
    header, body = struct.pack('<4si', b'RBSP', 1), b''
    for lump in lumps:
        header += struct.pack('<ii', 168 + len(body), len(lump)); body += lump
    return header + body


def fixture(root, raw=None):
    (root / 'base').mkdir()
    entries = [('maps/test.bsp', raw or bsp()), ('maps/ending.bsp', bsp(entities=b'{"classname" "worldspawn"}\0')),
               ('maps/broken.bsp', b'not bsp'), ('textures/test.swl', swl()),
               ('models/hero.def', definition()), ('models/hero/body.sbm', sbm()), ('models/hero/idle.sam', sam()),
               ('models/hero/a.tga', tga()), ('models/hero/b.tga', tga()), ('maps/test.pal', bytes(148224))]
    entries += [(f'env/sky_{s}.tga', tga()) for s in ('rt', 'lf', 'bk', 'ft', 'up', 'dn')]
    (root / 'base/pak0.sin').write_bytes(spak(entries))
    return sin.read_install(root)[0]


class SinMapsTest(unittest.TestCase):
    def test_rbsp_layout_and_invalid_lumps(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = fixture(Path(tmp))
            world = maps.read_bsp(bsp(), files)
            self.assertEqual(len(world['lump_sizes']), 20)
            self.assertEqual(world['textures'][0]['source'], 'textures/test.swl')
            self.assertEqual(world['surfaces'][0][22], b'test_surface' + bytes(20))
            self.assertEqual(maps.face_data(world, 0)['lightofs'], 0)
            for i in maps.FORMATS:
                with self.subTest(lump=i), self.assertRaises(ValueError):
                    maps.read_bsp(bsp(edits={i: b'x'}), files)
            for offset, value in ((4, 38), (8, 1), (12, 99999999)):
                bad = bytearray(bsp()); struct.pack_into('<i', bad, offset, value)
                with self.assertRaises(ValueError): maps.read_bsp(bad, files)
            node = struct.pack('<3i6h2H', 0, 0, 0, 0, 0, 0, 32, 32, 32, 0, 1)
            with self.assertRaisesRegex(ValueError, 'Cyclic'):
                maps.read_bsp(bsp(edits={4: node}), files)
            bad_tex = bytearray(bsp()); at = struct.unpack_from('<i', bad_tex, 48)[0]
            struct.pack_into('<f', bad_tex, at + 124, float('nan'))
            with self.assertRaises(ValueError): maps.read_bsp(bad_tex, files)

    def test_sin_flags_sixteen_styles_and_lighting(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = fixture(Path(tmp))
            for flags, kind, unlit in ((2, 'normal', False), (16, 'normal', True), (32, 'normal', False),
                                       (8, 'turbulent', True), (4 | 128, 'sky', True), (128, 'hidden', False),
                                       (maps.ADD, 'normal', True)):
                face = maps.face_data(maps.read_bsp(bsp(flags), files), 0)
                self.assertEqual((face['kind'], face['unlit']), (kind, unlit))
            world = maps.read_bsp(bsp(styles=[0, 1, 2, 3, 32] + [255] * 11), files)
            face = maps.face_data(world, 0)
            self.assertEqual(face['size'].tolist(), [3, 3])
            self.assertEqual(len(face['styles']), 16)
            self.assertEqual(maps.lightmap(face, world['lighting'], {0, 1, 2, 3})[0, 0].tolist(), [64, 128, 32])
            self.assertEqual(maps.lightmap(face, world['lighting'], {1, 2, 3, 32}, 3)[0, 0].tolist(), [127, 255, 63])
            translucent = maps.face_data(maps.read_bsp(bsp(translucence=.25), files), 0)
            self.assertEqual(translucent['alpha'], .75)
            self.assertTrue(translucent['unlit'])
            self.assertFalse(translucent['settings']['flowing'])
            for value, alpha, unlit in ((5., 0., True), (-2., 1., False)):
                face = maps.face_data(maps.read_bsp(bsp(translucence=value), files), 0)
                self.assertEqual((face['alpha'], face['unlit']), (alpha, unlit))
            scene, _, _, _ = maps.build_map(bsp(), 'test', files)
            self.assertFalse(scene['palette']['applied'])
            self.assertEqual(scene['lighting']['intensity'], 1)
            # Q2 defaults retain its 4-style, TRANS33 and intensity=2 behavior.
            old = q2.read_bsp(bsp38(flags=16), q2_files())
            self.assertEqual(q2.face_data(old, 0)['alpha'], .33)
            self.assertEqual(q2.INTENSITY, 2)
            self.assertEqual(len(q2.face_data(old, 0)['styles']), 4)

    def test_config_gamma_and_texture_missing_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); files = fixture(root)
            (root / 'base/default.cfg').write_text('set vid_gamma .8\nintensity 2\ngl_modulate 1.5\n')
            (root / 'base/autoexec.cfg').write_text('intensity 1\n')
            profile = maps.lighting_profile(root, files)
            self.assertEqual(profile['intensity'], 1)
            self.assertEqual(profile['gl_modulate'], 1.5)
            (root / '2015').mkdir()
            (root / '2015/pak0.sin').write_bytes(spak([('default.cfg', b'intensity 3\n')]))
            expansion = sin.read_install(root, game='2015')[0]
            # Expansion archive wins over base loose, then base autoexec is applied later.
            (root / 'base/autoexec.cfg').write_text('gl_modulate 2\n')
            self.assertEqual(maps.lighting_profile(root, expansion, '2015')['intensity'], 3)
            image = maps.texture('test', files, profile)['image']
            self.assertEqual(image.getpixel((1, 0))[3], 0)
            self.assertEqual(image.getpixel((0, 0))[1], int(255 * ((20.5 / 255.5) ** .8) + .5))
            missing = maps.texture('absent', files, profile)
            self.assertTrue(missing['missing']); self.assertIn('absent.swl', missing['reason'])

    def test_actor_pose_transform_models_and_entity_accounting(self):
        raw = bsp(entities=b'{"classname" "worldspawn"}\n{"classname" "info_player_start" "origin" "16 16 96"}\n'
            b'{"classname" "Monster_Test" "model" "hero.def" "origin" "100 200 300" "scale" "2" "angle" "90"}\n'
            b'{"classname" "Ammo_Test" "model" "hero.def" "spawnflags" "512"}\n'
            b'{"classname" "func_scriptmodel" "model" "missing.def"}\n'
            b'{"classname" "func_spawn" "model" "hero.def"}\n{"classname" "fx_sprite" "model" "test.spr"}\0')
        with tempfile.TemporaryDirectory() as tmp:
            files = fixture(Path(tmp), raw)
            scene, blob, images, atlas = maps.build_map(raw, 'test', files)
            actor = scene['models'][0]
            self.assertEqual(actor['status'], 'drawn', scene['model_failures'])
            self.assertEqual(actor['animation']['source'], 'models/hero/idle.sam')
            points = np.frombuffer(blob, '<f4', scene['vertices'] * 3).reshape(-1, 3) / maps.SCALE
            np.testing.assert_allclose(points[4], [244, 432, 172])
            self.assertEqual(len(scene['model_failures']), 1)
            self.assertEqual(len(scene['omitted_models']), 4)
            self.assertEqual(scene['viewpoints'][0]['floor_z'], 0)
            self.assertEqual(scene['viewpoints'][0]['native_origin'][2], 66)
            self.assertEqual(len(blob), scene['vertices'] * 28 + scene['indices'] * 4)
            self.assertTrue(maps.excluded({'spawnflags': '2048'}, True))
            self.assertFalse(maps.excluded({'spawnflags': '512'}, True))
            self.assertTrue(maps.excluded({'spawnflags': '8192'}))
            self.assertFalse(maps.deathmatch({'entities': [{'classname': 'info_player_start'}]}, 'sin'))
            menu = Path(tmp) / 'dm.mnu'; menu.write_text('menulevel dm_mapinfo1\n')
            self.assertTrue(maps.deathmatch({'entities': [{'classname': 'info_player_start'}]}, 'sin',
                {'maps/arena.mnu': {'path': menu}}, 'arena'))

    def test_hull_floor_brush_state_and_cinematic_fallback(self):
        planes = [(0, 0, 1, 0, 2), (0, 0, -1, 64, 2), (1, 0, 0, 64, 0), (-1, 0, 0, 64, 0),
                  (0, 1, 0, 64, 1), (0, -1, 0, 64, 1)]
        node = struct.pack('<3i6h2H', 0, -1, -2, -64, -64, -64, 64, 64, 64, 0, 1)
        leaves = b''.join(struct.pack('<ihh6h4H', 1 if i else 0, -1, 0, -64, -64, -64, 64, 64, 64, 0, 0, 0, 1) for i in range(2))
        with tempfile.TemporaryDirectory() as tmp:
            files = fixture(Path(tmp))
            raw = bsp(edits={1: b''.join(struct.pack('<4fi', *p) for p in planes), 4: node, 8: leaves,
                10: struct.pack('<H', 0), 14: struct.pack('<3i', 0, 6, 1),
                15: b''.join(struct.pack('<Hhi', i, 0, -1) for i in range(6))})
            world = maps.read_bsp(raw, files)
            self.assertEqual(maps.hull_floor(world, np.array([16., 16., 100.]), mins=(-16, -16, 0), maxs=(16, 16, 72)), 0)
            world['models'] *= 3
            world['entities'] += [{'classname': 'func_scriptobject', 'model': '*1', 'origin': '1 2 3', 'angles': '0 30 0'},
                                  {'classname': 'func_door', 'model': '*2', 'spawnflags': '1', 'angle': '-1'},
                                  {'classname': 'func_rotatingdoor', 'model': '*1', 'spawnflags': '1', 'angle': '30'},
                                  {'classname': 'func_rotatingdoor', 'model': '*1', 'spawnflags': '3', 'openangle': '45'}]
            placed, omitted = maps.instances(world)
            self.assertEqual(placed[1]['offset'], [1, 2, 3]); self.assertEqual(placed[1]['angles'], [0, 30, 0])
            self.assertEqual(placed[2]['offset'][2], 122)
            self.assertEqual(placed[3]['angles'], [0, -60, 0])
            self.assertEqual(placed[4]['angles'], [0, 45, 0])
            scene, _, _, _ = maps.build_map(bsp(entities=b'{"classname" "worldspawn"}\0'), 'credits', files)
            self.assertEqual(scene['viewpoints'][0]['classname'], 'fallback_camera')

    def test_text_light_styles_and_explicit_q2_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = fixture(Path(tmp))
            entities = b'{"classname" "worldspawn"}\n'
            for style in ('flicker_pattern', 'named_light', '  +32suffix'):
                entities += ('{"classname" "light" "style" "%s" "spawnflags" "1"}\n' % style).encode()
            scene, _, _, _ = maps.build_map(bsp(entities=entities), 'test', files)
            self.assertEqual(scene['dark_styles'], [32])
            self.assertEqual(maps.light_style({'style': '-2.5'}), -2)
            self.assertEqual(maps.light_style({}), 0)
            # An explicit empty override must bypass the Q2 int parser, too.
            world = maps.read_bsp(bsp(entities=entities), files)
            result = q2.geometry(world, maps.instances(world)[0], face_reader=maps.face_data, dark_styles=[])
            self.assertEqual(result['dark'], [])

    def test_startup_visibility_stops_at_yields_and_unknown_conditions(self):
        entities = [{'classname': 'worldspawn'},
                    {'classname': 'func_scriptmodel', 'model': 'hero.def', 'targetname': 'standin'},
                    {'classname': 'func_scriptobject', 'model': '*1', 'targetname': 'switch'},
                    {'classname': 'func_scriptmodel', 'model': 'hero.def', 'targetname': 'later'}]
        script = '''parm.index = 1
loop:
thread helper
parm.index += 1
parm.index ifgreater 2 goto done
goto loop
done:
thread intro
$switch hide // hidden brush, not just models
$later hide
$later show
waitForPlayer
$switch show
end
helper:
game.unknown ifequal 1 goto delayed
$later hide
end
intro:
$standin hide
wait 1
$standin show
end
delayed:
$later hide
end
'''
        hidden, limits = maps.startup_visibility(script, entities)
        self.assertEqual(set(hidden), {1, 2})
        self.assertTrue(any('condition' in s for s in limits))
        world = {'entities': entities, 'models': [(0,) * 12] * 2, 'script_hidden': hidden}
        self.assertEqual(len(maps.instances(world)[0]), 1)
        self.assertEqual([m['entity'] for m in maps.scenery(world)[0]], [3])
        self.assertIn('Startup script hide', maps.scenery(world)[1][0]['reason'])
        hidden, limits = maps.startup_visibility('loop:\ngoto loop', entities)
        self.assertEqual(hidden, {})
        self.assertIn('limit', limits[0])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); files = fixture(root)
            source = root / 'setup.scr'; source.write_text(script)
            files['maps/test.scr'] = {'path': source}
            raw = bsp(entities=('\n'.join('{' + ' '.join(f'"{k}" "{v}"' for k, v in e.items()) + '}'
                                       for e in entities)).encode())
            scene, _, _, _ = maps.build_map(raw, 'test', files)
            self.assertTrue(scene['script']['found'])
            self.assertEqual([m['entity'] for m in scene['models']], [3])

    def test_pruned_water_contents_and_world_defaults(self):
        # Right half is water. Two dry children collapse, including solid/lava priority.
        world = dict(models=[(0,) * 9 + (0, 0, 0)], nodes=[(0, -1, 1), (1, -2, -3)],
                     planes=[(1, 0, 0, 64), (0, 0, 1, 10)], leaves=[(32,), (0,), (33,)], info={})
        water = maps.water_volume(world)
        self.assertEqual(water, dict(root=0, nodes=[[1, 0, 0, 64, -2, -1]], color=[0, 0, 1], alpha=.1))
        world['leaves'][0] = (40,)
        self.assertIsNone(maps.water_volume(world))
        world['leaves'] = [(32,)] * 3
        world['info'] = {'watercolor': '.2 .3 .4', 'wateralpha': '.25'}
        self.assertEqual(maps.water_volume(world), dict(root=-2, nodes=[], color=[.2, .3, .4], alpha=.25))

    def test_section_import_routes_and_every_bsp_accounted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            for game in ('2015', 'ctf'):
                (root / game).mkdir()
                (root / game / 'pak0.sin').write_bytes(spak([('maps/credits.bsp', bsp()), ('textures/test.swl', swl())]))
            with patch('app.local_data_dir', root / 'out'):
                client = app.test_client()
                self.assertEqual(client.get('/maps/sin/').status_code, 200)
                self.assertIn(b'/maps/sin/', client.get('/').data)
                for game in ('sin', '2015', 'ctf'):
                    response = client.post('/import_sin_maps', json={'path': str(root), 'game': game})
                    self.assertEqual(response.status_code, 200, response.json)
                    report = response.json
                    self.assertTrue(all(r['status'] != 'pending' for r in report['results']))
                    self.assertEqual(len(report['imported']), 2 if game == 'sin' else 1)
                    self.assertEqual(len(report['failed']), 1 if game == 'sin' else 0)
                    self.assertEqual(client.get(f'/sin-map-data/{game}/index.json').status_code, 200)
                self.assertEqual(client.get('/sin-map-data/../../app.py').status_code, 404)
                for payload in ({}, {'path': []}, {'path': str(root), 'game': 'rogue'}):
                    self.assertEqual(client.post('/import_sin_maps', json=payload).status_code, 400)
                self.assertEqual(client.post('/import_sin_maps', json={'path': str(root)}, headers={'Origin': 'https://foreign.invalid'}).status_code, 403)
                again = client.post('/import_sin_maps', json={'path': str(root)}).json
                self.assertEqual(len(again['skipped']), 2)


if __name__ == '__main__':
    unittest.main()

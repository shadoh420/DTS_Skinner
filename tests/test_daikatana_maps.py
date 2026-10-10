"""BSP41 and static scenery fixtures; retail checks are isolated for the import host."""
import io
from collections import Counter
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from app import app
from tests.test_daikatana import INSTALL, bitmap, dkm, pak, wal
from tests.test_quake2_maps import bsp as bsp38
from tools import import_daikatana as dk, import_daikatana_map as maps


def bsp(flags=0, styles=(0, 255, 255, 255), samples=((64, 128, 32),), entity_lump=None, edits=None):
    raw = bsp38(flags, styles, samples, entity_lump)
    lumps = [raw[o:o + n] for o, n in struct.iter_unpack('<ii', raw[8:160])] + [struct.pack('<3f', 10, 20, 30), struct.pack('<2i', 1, 0)]
    lumps[8] = struct.pack('<i2h6h4Hi', 0, -1, 0, 0, 0, 0, 32, 32, 128, 0, 1, 0, 0, -1)
    lumps[4] = struct.pack('<3i6h2H', 0, -1, -1, 0, 0, 0, 32, 32, 128, 0, 1)
    for index, data in (edits or {}).items():
        lumps[index] = data
    header, body = struct.pack('<4si', b'IBSP', 41), b''
    for lump in lumps:
        header += struct.pack('<ii', 176 + len(body), len(lump))
        body += lump
    return header + body


def files():
    return {name: dict(data=raw, pak='memory') for name, raw in
            [('textures/test.wal', wal()), ('pics/colormap.bmp', bitmap()),
             ('models/e1/test.dkm', dkm()), ('skins/head.wal', wal()), ('skins/body.bmp', bitmap())]}


def deco_table():
    fields = ['test', 'models/e1/test.dkm', '0', '2', '10', '0', '20', '-16', '-16', '-24', '16', '16', '32', '0', '2', '0~1', '1~1']
    return {('deco_e1', 'test'): dict(path=fields[1], fields=fields, csv='models/e1/e1decoinfo.csv')}


class DaikatanaMapsTest(unittest.TestCase):
    def setUp(self):
        read = dk.read_asset
        fixture_reader = patch.object(dk, 'read_asset', side_effect=lambda entry: entry['data'] if 'data' in entry else read(entry))
        fixture_reader.start()
        self.addCleanup(fixture_reader.stop)

    def test_bsp41_extra_lumps_and_reject_corruption(self):
        data = maps.read_bsp(bsp(), files())
        self.assertEqual(data['extended_texinfo'], [(10., 20., 30.)])
        self.assertEqual(data['plane_polys'], [[0]])
        self.assertEqual(len(data['leaves'][0]), 14)
        self.assertEqual(data['leaves'][0][-1], -1)
        for edits in ({19: b'bad'}, {20: struct.pack('<2i', 1, 1)}, {20: struct.pack('<i', 2)},
                      {19: struct.pack('<6f', *([0] * 6))}):
            with self.assertRaises(ValueError):
                maps.read_bsp(bsp(edits=edits), files())
        with self.assertRaisesRegex(ValueError, 'version 41'):
            maps.read_bsp(bsp38(), files())

    def test_wal_embedded_and_map_palette_transparency(self):
        assets = files()
        assets['textures/test.wal']['data'] = wal(version=0)
        assets['textures/episode/colormap.bmp'] = dict(data=bitmap(), pak='memory')
        raw = bsp(entity_lump=b'{"classname" "worldspawn" "palette" "episode"}\0')
        data = maps.read_bsp(raw, assets)
        self.assertEqual(data['textures'][0]['palette'], 'textures/episode/colormap.bmp')
        self.assertEqual(data['textures'][0]['image'].size, (8, 8))
        embedded = bytearray(wal())
        embedded[892] = 255
        image = maps.wal_image(bytes(embedded))
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(image.getpixel((0, 0))[:3], (7, 248, 17))  # Native below-neighbour RGB bleed.
        self.assertEqual(image.getpixel((1, 0)), (7, 248, 17, 255))
        # it_wall uses the map palette even when WAL v3 has a different one;
        # the transparent texel's filtering fringe must use that palette too.
        shared = bytes([90, 40, 20] * 256)
        image = maps.wal_image(bytes(embedded), shared)
        self.assertEqual(image.getpixel((0, 0)), (90, 40, 20, 0))
        self.assertEqual(image.getpixel((1, 0)), (90, 40, 20, 255))
        self.assertEqual(maps.read_bsp(raw, files())['textures'][0]['palette'], 'pics/colormap.bmp')

    def test_native_flags_and_lit_warp(self):
        for flags, kind, unlit, alpha in ((0, 'normal', False, 1), (2, 'normal', True, 1),
                (4, 'sky', True, 1), (4 | 128, 'hidden', True, 1), (8, 'turbulent', False, 1),
                (16, 'normal', False, .33), (32, 'normal', False, .66), (64, 'normal', False, 1),
                (maps.FOG, 'hidden', False, 1), (maps.MASKED, 'normal', True, 1), (maps.ALPHA, 'normal', False, 1)):
            face = maps.face_data(maps.read_bsp(bsp(flags), files()), 0)
            self.assertEqual((face['kind'], face['unlit'], face['alpha']), (kind, unlit, alpha))

    def test_placed_wal_skin_keeps_its_palette(self):
        # A wall and a model can use the same indexed WAL but different palettes.
        # Follow-up 3: e1m1a coffin2 was decoded with the green world palette.
        for version in (3, 0):
            with self.subTest(version=version):
                assets = files()
                raw = bytearray(wal(version=version))
                mip = 892 if version == 3 else 100
                raw[mip] = 255
                assets['skins/head.wal']['data'] = bytes(raw)
                world = maps.read_bsp(bsp(), assets)
                world['palette'] = bytes([90, 40, 20] * 256)
                drawn, _ = maps.scenery(dict(entities=[dict(classname='deco_e1', model='test')]), deco_table())
                rendered = maps.q2.geometry(world, maps.instances(world)[0], face_reader=maps.face_data, dark_styles=[])
                images, failures = maps.append_scenery(rendered, world, assets, drawn)
                self.assertEqual(failures, [])
                skin = images[next(g['texture'] for g in rendered['groups'] if g['kind'] == 'model')]
                rgb = (7, 248, 17) if version == 3 else (90, 40, 20)
                self.assertEqual(skin.getpixel((1, 0)), (*rgb, 255))
                self.assertEqual(skin.getpixel((0, 0)), (*rgb, 0))
                self.assertEqual(maps.wal_image(bytes(raw), world['palette']).getpixel((1, 0)), (90, 40, 20, 255))
        scene, blob, images, atlas = maps.build_map(bsp(flags=0x200), 'test', files(), {})
        self.assertEqual(scene['unknown_texture_bits'], ['0x200'])
        self.assertEqual(len(blob), scene['vertices'] * 32 + scene['indices'] * 4)
        warped = maps.read_bsp(bsp(flags=8), files())
        warped['texinfo'][0] = (1, 0, 0, 16, 0, 1, 0, 16, 0, 8)
        data = maps.q2.geometry(warped, maps.instances(warped)[0], face_reader=maps.face_data, dark_styles=[])
        self.assertTrue(np.isfinite(data['uv2']).all())
        self.assertIn((64, 128, 32), set(data['atlas'].getdata()))

    def test_rgb_lightmaps_gamma_and_start_off_style(self):
        data = maps.read_bsp(bsp(styles=(0, 32, 255, 255), samples=((200, 100, 50),) * 2), files())
        face = maps.face_data(data, 0)
        self.assertEqual(maps.q2.lightmap_rgb(face, data['lighting'])[0, 0].tolist(), [255, 127, 63])
        data['entities'].append(dict(classname='light', style='32', spawnflags='1'))
        self.assertEqual(maps.dark_styles(data), [32])
        self.assertEqual(maps.q2.lightmap_rgb(face, data['lighting'], maps.dark_styles(data))[0, 0].tolist(), [200, 100, 50])
        data['entities'][-1]['spawnflags'] = str(1 | 0x2000)
        self.assertEqual(maps.dark_styles(data), [])
        image = maps.upload_image(Image.new('RGBA', (1, 1), (64, 128, 255, 37)))
        self.assertEqual(image.getpixel((0, 0)), (85, 147, 255, 37))
        original = Image.new('RGBA', (1, 1), (64, 128, 255, 37))
        self.assertEqual(maps.upload_image(original, gamma=1).getpixel((0, 0)), (64, 128, 255, 37))
        self.assertEqual(maps.upload_image(original, mipmap=False).getpixel((0, 0)), (64, 128, 255, 37))
        resized_sky = maps.upload_image(Image.new('RGBA', (3, 3), (64, 128, 255, 37)), mipmap=False)
        self.assertEqual(resized_sky.getpixel((0, 0)), (85, 147, 255, 37))
        # The native half-texel formula lifts black to 2; high-valued snow
        # texels hardly change, despite much larger ratios for dark textures.
        ramp = Image.fromarray(np.array([[[0, 16, 224, 255]]], dtype=np.uint8))
        self.assertEqual(maps.upload_image(ramp).getpixel((0, 0)), (2, 29, 230, 255))
        source = Image.fromarray(np.tile(np.arange(6, dtype=np.uint8)[None, :, None], (3, 1, 4)))
        self.assertEqual(maps.upload_image(source).size, (4, 2))
        self.assertEqual(maps.upload_image(source, mipmap=False).size, (8, 4))
        self.assertEqual(maps.upload_image(Image.new('RGB', (512, 128))).size, (256, 128))
        # R_LightPoint does not normalize overbright RGB like the lightmap builder.
        np.testing.assert_allclose(maps.light_point(data, np.array([16, 16, 80]), []), [400/255, 200/255, 100/255])
        # Native truncates ST before checking extents; a geometric polygon test
        # would reject this point just beyond the edge.
        np.testing.assert_allclose(maps.light_point(data, np.array([32.5, 16, 80]), [32]), [200/255, 100/255, 50/255])
        np.testing.assert_allclose(maps.light_point(data, np.array([34, 16, 80]), []), [0, 0, 0])

    def test_native_float_extents_at_lump_end(self):
        # e3dm1 face 3562: float64 adds a row/column to each of two 5x5 styles.
        points = [(1176, 456, 288), (1128, 456, 288), (1128, 408, 288), (1176, 408, 288)]
        edits = {2: b''.join(struct.pack('<3f', *p) for p in points),
                 5: struct.pack('<8fii32si', 4/3, 0, 0, 32, 0, -4/3, 0, -32, 0, 0, b'test', -1),
                 7: bytes([100, 40, 20]) * 50}
        data = maps.read_bsp(bsp(styles=(0, 0, 255, 255), edits=edits), files())
        face = maps.face_data(data, 0)
        self.assertEqual(face['size'].tolist(), [5, 5])
        self.assertEqual(face['mins'].tolist(), [1536, -640])
        light = maps.q2.lightmap_rgb(face, data['lighting'])
        self.assertEqual(light.shape, (5, 5, 3))
        self.assertEqual(light[-1, -1].tolist(), [200, 80, 40])
        with self.assertRaisesRegex(ValueError, 'exceeds lighting lump'):
            maps.q2.lightmap_rgb(face, data['lighting'][:-1])
        # Timestream sky faces have unused zero light offsets/style slots.
        sky = maps.read_bsp(bsp(flags=5, styles=(0, 0, 0, 0), edits={7: b'\1\2\3'}), files())
        sky_face = maps.face_data(sky, 0)
        self.assertEqual(sky_face['kind'], 'sky')
        self.assertEqual(maps.q2.lightmap_rgb(sky_face, sky['lighting']).shape, (1, 1, 3))

    def test_episode_lights_crossed_sprites_and_native_non_models(self):
        entities = [dict(classname=f'light_e{n}', scale='2 0', origin='10 20 30', angle='30',
                         spawnflags='1', style='32', frame='7', alpha='.1', model='ignored.dkm') for n in range(1, 5)]
        entities += [dict(classname='light_e1', spawnflags='8192')]
        entities += [dict(classname=cls, model='ignored.dkm') for cls in maps.LIGHT_WITHOUT_MODEL]
        drawn, skipped = maps.scenery(dict(entities=entities), {})
        self.assertEqual([p['path'] for p in drawn], [f'models/global/e{n}_firea.sp2' for n in (2, 2, 3, 4)])
        self.assertEqual(len(skipped), 5)
        self.assertEqual(maps.dark_styles(dict(entities=entities)), [])
        for item in drawn:
            self.assertEqual((item['scale'], item['frame'], item['alpha']), ([2, 1, 1], 0, 1))
            self.assertTrue(item['oriented'] and item['additive'])
            self.assertEqual(item['sprite_copies'], 2)
        assets = files()
        assets[drawn[0]['path']] = dict(data=struct.pack('<4s2i4i64s', b'IDS2', 2, 1, 64, 32, 32, 16, b'skins/body.bmp'))
        world = maps.read_bsp(bsp(), assets)
        rendered = maps.q2.geometry(world, maps.instances(world)[0], face_reader=maps.face_data, dark_styles=[])
        before = len(rendered['points'])
        _, failures = maps.append_scenery(rendered, world, assets, drawn[:1])
        self.assertEqual(failures, [])
        self.assertEqual(drawn[0]['vertices'], 8)
        group = rendered['groups'][-1]
        self.assertEqual((group['kind'], group['count'], group['additive']), ('sprite_oriented', 12, True))
        quads = rendered['points'][before:].reshape(2, 4, 3)
        for copy in range(2):
            np.testing.assert_allclose(quads[copy].mean(0), [10, 20, 30])
            matrix = maps.rotation([0, 30 + copy * 90, 0])
            np.testing.assert_allclose(quads[copy, 0], [10, 20, 30] + 64 * matrix[:, 1] + 16 * matrix[:, 2])
        self.assertAlmostEqual(np.dot(quads[0, 1] - quads[0, 0], quads[1, 1] - quads[1, 0]), 0)

    def test_missing_decoration_does_not_switch_episode_csv(self):
        table = deco_table()
        table['deco_e2', 'dojolamp'] = table['deco_e1', 'test']
        drawn, omitted = maps.scenery(dict(entities=[dict(classname='worldspawn', episode='2'),
            dict(classname='deco_e1', model='dojolamp'), dict(classname='deco_e1', model='test')]), table)
        self.assertEqual(len(drawn), 1)
        self.assertEqual(omitted[0]['reason'], 'Native decoration CSV lookup fails; spawn returns without assigning a model')

    def test_medium_filter_and_brush_spawn_placements(self):
        data = maps.read_bsp(bsp(), files())
        data['models'] *= 9
        data['entities'] = [dict(classname='worldspawn'),
            dict(classname='func_wall', model='*1', spawnflags='8192'),
            dict(classname='func_wall', model='*2', spawnflags='512'),
            dict(classname='func_wall', model='*3', spawnflags='1'),
            dict(classname='func_wall', model='*4', spawnflags='5'),
            dict(classname='func_door_rotate', model='*5', origin='10 20 30', spawnflags='131', distance='45'),
            dict(classname='func_plat', model='*6', height='40'),
            dict(classname='func_train', model='*7', target='corner'),
            dict(classname='path_corner_train', targetname='corner', origin='100 200 300'),
            dict(classname='trigger_once', model='*8')]
        placed, skipped = maps.instances(data)
        by_model = {p['model']: p for p in placed}
        self.assertEqual(set(by_model), {0, 2, 4, 5, 6, 7})
        self.assertEqual(by_model[5]['angles'], [0, 0, -45])
        self.assertEqual(by_model[5]['offset'], [10, 20, 30])
        self.assertEqual(by_model[6]['offset'][2], -40)
        self.assertEqual(by_model[7]['offset'], [100, 200, 300])
        self.assertEqual(len(skipped), 3)
        self.assertFalse(maps.excluded(dict(spawnflags='32768')))
        data['entities'] = [dict(classname='worldspawn'), dict(classname='func_door', model='*1', angle='0', spawnflags='1')]
        self.assertEqual(maps.instances(data)[0][1]['offset'], [26, 0, 0])  # 32 + 2 collision expansion - 8 lip.

    def test_decoration_sequence_frame_scale_alpha_and_scope(self):
        data = dict(entities=[dict(classname='deco_e1', model='test', origin='10 20 30', angle='90', scale='2',
                                   frame='1', spawnflags='256', alfa='.2'),
            dict(classname='deco_e1', model='test', animseq='1', alpha='.4', spawnflags='256'),
            dict(classname='deco_e1', model='test', frame='1', animseq='-1', scale='1 2 3'),
            dict(classname='deco_e1', model='test', spawnflags='8192'),
            dict(classname='monster_test', model='models/e1/test.dkm'),
            dict(classname='light_flare', model='custom.sp2', spawnflags='1', style='32')])
        drawn, skipped = maps.scenery(data, deco_table())
        self.assertEqual([p['frame'] for p in drawn], [0, 1, 1])
        self.assertEqual([p['alpha'] for p in drawn], [1, .4, 1])
        self.assertEqual(drawn[0]['scale'], [2, 2, 2])
        self.assertEqual(drawn[0]['angles'], [0, 90, 0])
        self.assertEqual(len(skipped), 3)
        world = maps.read_bsp(bsp(), files())
        rendered = maps.q2.geometry(world, maps.instances(world)[0], face_reader=maps.face_data, dark_styles=[])
        before = len(rendered['points'])
        _, failures = maps.append_scenery(rendered, world, files(), drawn[:1])
        self.assertEqual(failures, [])
        native_point = dk.read_dkm(dkm())['points'][0]
        expected = maps.rotation([0, 90, 0]) @ (np.array(native_point) * 2) + [10, 20, 54]
        np.testing.assert_allclose(rendered['points'][before], expected)

    def test_scaled_model_header_origin_is_world_translation(self):
        for version in (1, 2):
            for scale, expected in (('1', [117, 214, 350]), ('2 3 1.5', [141, 268, 387])):
                with self.subTest(version=version, scale=scale):
                    assets = files()
                    raw = bytearray(dkm(version))
                    struct.pack_into('<3f', raw, 8, 10, -20, -24)
                    assets['models/e1/test.dkm']['data'] = bytes(raw)
                    world = maps.read_bsp(bsp(), assets)
                    drawn, _ = maps.scenery(dict(entities=[dict(classname='deco_e1', model='test',
                        origin='100 200 300', angle='90', scale=scale)]), deco_table())
                    rendered = maps.q2.geometry(world, maps.instances(world)[0], face_reader=maps.face_data, dark_styles=[])
                    before = len(rendered['points'])
                    _, failures = maps.append_scenery(rendered, world, assets, drawn)
                    self.assertEqual(failures, [])
                    # The header offset is outside rotation, not a rotated local pivot.
                    np.testing.assert_allclose(rendered['points'][before], expected)

    def test_dkm_selected_frames_and_native_normals(self):
        for version in (1, 2):
            first = dk.read_dkm(dkm(version), 0)
            later = dk.read_dkm(dkm(version), 1)
            self.assertNotEqual(first['points'], later['points'])
            self.assertEqual(dk.read_dkm(dkm(version), 200)['points'], first['points'])
            model = dk.build_model(first, ['a', 'b'], include_normals=True)
            self.assertEqual(len(model['normal_indices']), len(model['vertices']) // 3)
            first['surfaces'][0]['flags'] = 1
            self.assertEqual(len(dk.build_model(first, ['a', 'b'], visible_only=True)['groups']), 1)
        normals = maps.alias_normals()
        self.assertEqual(normals.shape, (256, 3))
        np.testing.assert_allclose(np.linalg.norm(normals[:255], axis=1), 1)
        np.testing.assert_allclose(maps.alias_colors([0, 1, 255], [.1, .2, .3], 0)[0], [.1, .2, .3])
        self.assertGreater(maps.alias_colors([1], [0, 0, 0], 0)[0, 0], .5)

    def test_native_aidata_scenery_and_hidden_debris(self):
        assets = files()
        row = ['e_seagull', 'models/global/d4_gull.dkm'] + [''] * 12 + ['.5 .5 .5']
        stream = io.StringIO()
        import csv
        csv.writer(stream).writerow(row)
        assets['aidata.vsc'] = dict(data=b'CVSC' + bytes(x ^ 0x96 for x in stream.getvalue().encode()), pak='loose')
        table = maps.decoration_table(assets)
        drawn, skipped = maps.scenery(dict(entities=[dict(classname='e_seagull'), dict(classname='light_flare', spawnflags='1')]), table)
        self.assertEqual(drawn[0]['path'], 'models/global/d4_gull.dkm')
        self.assertEqual(drawn[0]['scale'], [.5, .5, .5])
        self.assertIn('START_OFF', skipped[0]['reason'])
        flare = maps.scenery(dict(entities=[dict(classname='light_flare', scale='2 0 0')]), table)[0][0]
        self.assertEqual(flare['scale'], [2, 1, 1])
        data = maps.read_bsp(bsp(), files())
        data['models'] *= 3
        data['entities'] = [dict(classname='worldspawn'), dict(classname='func_debris', model='*1'),
                            dict(classname='func_debris_visible', model='*2')]
        placed, omitted = maps.instances(data)
        self.assertEqual([p['model'] for p in placed], [0, 2])
        self.assertIn('starts hidden', omitted[0]['reason'])

    def test_floor_hull_never_raises_viewpoint(self):
        data = maps.read_bsp(bsp(), files())
        # Solid box: top z=0, bottom -32; x/y +-128. Expanded AABB catches its top.
        data['planes'] = [(0, 0, 1, 0, 2), (0, 0, -1, 32, 2), (1, 0, 0, 128, 0),
                          (-1, 0, 0, 128, 0), (0, 1, 0, 128, 1), (0, -1, 0, 128, 1)]
        data['brushes'] = [(0, 6, 1)]
        data['brushsides'] = [(i, 0) for i in range(6)]
        data['nodes'] = [(0, -1, -1, 0, 0, 0, 0, 0, 0, 0, 1)]
        data['leaves'][0] = (*data['leaves'][0][:11], 0, 1, -1)
        data['leafbrushes'] = [0]
        self.assertEqual(maps.hull_floor(data, [16, 16, 96]), 0)
        self.assertIsNone(maps.hull_floor(data, [16, 16, 10]))
        data['entities'] = [dict(classname='info_player_start', targetname='return', origin='16 16 96'),
                            dict(classname='info_player_start', targetname='primary', origin='16 16 80'),
                            dict(classname='info_player_intermission', origin='16 16 10')]
        views = maps.viewpoints(data, np.empty((0, 3, 3)))
        self.assertEqual(views[0]['targetname'], 'return')  # All named: native chooses first in file order.
        self.assertEqual([v['native_origin'][2] for v in views], [46, 46, 10])
        self.assertTrue(all(v['native_origin'][2] <= v['authored_origin'][2] + 22 for v in views))
        data['entities'].append(dict(classname='info_player_start', origin='16 16 96'))
        self.assertEqual(maps.viewpoints(data, np.empty((0, 3, 3)))[0]['targetname'], '')

    def test_sky_clouds_fog_and_sprite(self):
        assets = files()
        for suffix in ('rt', 'bk', 'lf', 'ft', 'up', 'dn', 'tile'):
            assets['env/32bit/test' + suffix + '.tga'] = dict(data=bitmap('TGA'), pak='memory')
        sky, images = maps.skybox(dict(sky='test', cloudname='testtile', cloud1tile='8', cloud2tile='2', cloud2alpha='.4'), assets)
        self.assertFalse(sky['missing'])
        self.assertEqual([l['tile'] for l in sky['clouds']['layers']], [8, 2])
        self.assertEqual(sky['clouds']['layers'][1]['alpha'], .4)
        fog = maps.fog_settings(dict(fog_value='1', fog_start='200', fog_end='9600', _color='.1 .2 .3'))
        self.assertEqual((fog['mode'], fog['color'], fog['end']), ('linear', [.1, .2, .3], 9600))
        self.assertEqual(maps.fog_settings(dict(_color='.1 .2 .3', fog_color='255 0 0'))['color'], [1, 0, 0])
        self.assertEqual(maps.fog_settings(dict(fog_color='255 0 0', _color='.1 .2 .3'))['color'], [.1, .2, .3])
        self.assertFalse(maps.fog_settings(dict(fog_value='0'))['enabled'])
        self.assertTrue(maps.fog_settings(dict(fog_value='400'))['enabled'])  # Enable, not density.
        raw = struct.pack('<4s2i4i64s', b'IDS2', 2, 1, 64, 32, 32, 16, b'skins/flame.tga')
        self.assertEqual(maps.read_sprite(raw)['pivot'], [32, 16])
        with self.assertRaises(ValueError):
            maps.read_sprite(raw[:20])

    @unittest.skipUnless(shutil.which('node'), 'Node is not installed')
    def test_page_materials(self):
        run = subprocess.run(['node', '--test', str(Path(__file__).with_name('daikatana_maps.test.cjs'))],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_import_report_and_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = [(n, v['data'], False) for n, v in files().items()]
            entries += [('maps/test.bsp', bsp(entity_lump=b'{"classname" "worldspawn" "mapname" "Title"}\n{"classname" "info_player_start" "origin" "16 16 96"}'), False),
                        ('maps/bad.bsp', b'invalid', False)]
            (root / 'pak2.pak').write_bytes(pak(entries))
            with patch('app.local_data_dir', root / 'out'):
                client = app.test_client()
                response = client.post('/import_daikatana_maps', json=dict(path=str(root), replace=True))
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json['bsp_entries'], 2)
                self.assertEqual(response.json['imported'], ['test'])
                self.assertIn('maps/bad.bsp', response.json['failed'])
                with client.get('/daikatana-map-data/maps/test/scene.json') as response:
                    self.assertEqual(response.json['title'], 'Title')
                with client.get('/maps/daikatana/') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b'data-game="daikatana"', response.data)
                self.assertEqual(client.post('/import_daikatana_maps', json={}).status_code, 400)


@unittest.skipUnless(INSTALL.is_dir(), 'Daikatana retail install not present')
class DaikatanaMapsInstallTest(unittest.TestCase):
    def test_all_83_maps_starts_and_scenery(self):
        assets, records, counts = dk.read_install(INSTALL, maps=True)
        names = [n for n in assets if n.endswith('.bsp')]
        self.assertEqual(len(names), 83)
        self.assertEqual(sum(r['source'].endswith('.bsp') for r in records), 83)
        table = maps.decoration_table(assets)
        authored, drawn, omitted = Counter(), Counter(), Counter()
        missing_csv, imported = [], 0
        for name in names:
            with self.subTest(map=name):
                scene, blob, images, atlas = maps.build_map(dk.read_asset(assets[name]), name, assets, table)
                self.assertTrue(scene['vertices'] and scene['indices'])
                self.assertEqual(len(blob), scene['vertices'] * 32 + scene['indices'] * 4)
                self.assertTrue(scene['viewpoints'])
                self.assertEqual(scene['lighting']['effective_gamma'], maps.GAMMA)
                self.assertEqual(scene['lighting']['display_gamma'], 1)
                self.assertEqual(scene['lighting']['fog_passes'], 2)
                imported += 1
                for view in scene['viewpoints']:
                    if view['classname'] == 'info_player_intermission':
                        continue
                    self.assertLessEqual(view['native_origin'][2], view['authored_origin'][2] + maps.VIEW_HEIGHT + .001)
                    self.assertIsNotNone(view['floor_z'], view)
                    self.assertLessEqual(view['eye_above_floor'], maps.FOOT + maps.VIEW_HEIGHT + .001)
                for item in scene['models']:
                    if item['classname'].startswith('deco_') or item['classname'] in maps.EPISODE_LIGHTS:
                        self.assertEqual(item['status'], 'drawn', item)
                    if item['classname'] in maps.EPISODE_LIGHTS:
                        self.assertEqual(item['vertices'], 8)
                        self.assertEqual(item['sprite_copies'], 2)
                        self.assertTrue(all(s['source'] for s in item['skins']))
                    if item['status'] == 'drawn':
                        drawn[item['classname']] += 1
                for item in scene['omitted_models']:
                    omitted[item['classname']] += 1
                    if item['classname'].startswith('deco_') and item['reason'] != 'Excluded on medium skill':
                        self.assertIn('CSV lookup fails', item['reason'])
                        missing_csv.append((name, item['named']))
                authored.update(scene['entity_classes'])
        self.assertEqual(imported, 83)
        expected_deco = dict(deco_e1=1018, deco_e2=892, deco_e3=711, deco_e4=252)
        expected_lights = dict(light_e1=139, light_e2=1315, light_e3=640, light_e4=86)
        self.assertEqual({k: authored[k] for k in expected_deco}, expected_deco)
        self.assertEqual({k: drawn[k] for k in expected_deco}, dict(expected_deco, deco_e1=1008))
        self.assertEqual(Counter(missing_csv), Counter({('maps/intro.bsp', 'dojolamp'): 4, ('maps/credits.bsp', 'dojolamp'): 4}))
        self.assertEqual({k: authored[k] for k in expected_lights}, expected_lights)
        self.assertEqual({k: drawn[k] for k in expected_lights}, expected_lights)
        expected_omitted = dict(light_spot=24, light_strobe=8, light_flame=4, light_walltorch=0)
        self.assertEqual({k: omitted[k] for k in expected_omitted}, expected_omitted)
        self.assertEqual((authored['light_flare'], drawn['light_flare'], omitted['light_flare']), (447, 403, 44))


if __name__ == '__main__':
    unittest.main()

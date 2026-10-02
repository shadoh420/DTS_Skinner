"""Quake 3 map import and serving boundaries; the real import runs only when Q3_GAME_BASE is set."""
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from app import app
from tools.import_q3_map import Game, Textures, describe, import_maps, map_id, parse_shaders, read_bsp

SCRIPT = '''// Comments and blank lines are skipped.
textures/test/glow
{
	surfaceparm nomarks /* inline */
	cull none
	{
		map $lightmap
		rgbGen identity
	}
	{
		map textures/test/wall.tga
		blendFunc filter
	}
	{
		map textures/test/glow.tga
		blendfunc add
		rgbGen wave sin 0.5 0.25 0 2
		tcMod scroll 1 -0.5
	}
}
textures/test/glow { { map textures/test/second.tga } }
textures/test/sky
{
	surfaceparm sky
	skyparms full 256 -
	{ map textures/test/missing.tga }
}
'''


def image(mode='RGB', kind='TGA'):
    data = io.BytesIO()
    Image.new(mode, (2, 2), 'red').save(data, kind)
    return data.getvalue()


def bsp(shaders, faces, entities='', version=46):
    """A map file: {shader name: surface flags} and faces as (shader, type, lightmap)."""
    lumps = [b''] * 17
    lumps[0] = entities.encode()
    lumps[1] = b''.join(struct.pack('<64sii', name.encode(), flags, 0) for name, flags in shaders.items())
    lumps[13] = b''.join(struct.pack('<8i', shader, -1, kind, 0, 0, 0, 0, lightmap) + bytes(72) for shader, kind, lightmap in faces)
    lumps[14] = bytes(128 * 128 * 3)
    lumps[2] = b'planes the viewer does not read'
    header, body = b'IBSP' + struct.pack('<i', version), b''
    for lump in lumps:
        header += struct.pack('<ii', 8 + 17 * 8 + len(body), len(lump))
        body += lump
    return header + body


def pk3(path, files):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)


class Q3MapTests(unittest.TestCase):
    def test_scripts_are_read_by_line_and_the_first_definition_of_a_name_is_kept(self):
        shaders = parse_shaders(SCRIPT)
        body, stages = shaders['textures/test/glow']
        self.assertEqual(body, [['surfaceparm', 'nomarks'], ['cull', 'none']])
        self.assertEqual(stages[2], [['map', 'textures/test/glow.tga'], ['blendfunc', 'add'],
                                     ['rgbgen', 'wave', 'sin', '0.5', '0.25', '0', '2'], ['tcmod', 'scroll', '1', '-0.5']])
        self.assertEqual(len(stages), 3)  # Not the one-stage definition further down.
        self.assertEqual(shaders['textures/test/sky'][1], [[['map', 'textures/test/missing.tga']]])

    def test_search_order_and_which_of_two_scripts_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseq3/textures').mkdir(parents=True)
            pk3(root / 'baseq3/pak0.pk3', {'textures/a.tga': b'pak0', 'textures/b.tga': b'pak0', 'textures/c.tga': b'pak0',
                                           'scripts/base.shader': 'x { { map textures/base.tga } }', 'scripts/same.shader': 'y { { map textures/base.tga } }'})
            pk3(root / 'baseq3/zmap.pk3', {'textures/b.tga': b'zmap', 'Textures\\C.TGA': b'zmap',
                                           'scripts/custom.shader': 'x { { map textures/custom.tga } }', 'scripts/same.shader': 'y { { map textures/custom.tga } }'})
            (root / 'baseq3/textures/c.tga').write_bytes(b'loose')
            game = Game([root / 'baseq3'])
            try:
                # A loose file is found before any archive, and of two archives the later name.
                self.assertEqual([game.files[f'textures/{name}.tga'][1]() for name in 'abc'], [b'pak0', b'zmap', b'loose'])
                self.assertEqual([label for label, _ in game.sources], ['baseq3 folder', 'zmap.pk3', 'pak0.pk3'])
                # Two scripts defining one shader: the lower source wins, unless the files share a name.
                self.assertEqual(game.shaders['x'][1], [[['map', 'textures/base.tga']]])
                self.assertEqual(game.shaders['y'][1], [[['map', 'textures/custom.tga']]])
            finally:
                game.close()

    def test_stage_defaults_follow_the_game(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseq3').mkdir()
            pk3(root / 'baseq3/pak0.pk3', {'scripts/test.shader': SCRIPT, 'textures/test/wall.tga': image(), 'textures/test/glow.jpg': image(kind='JPEG'),
                                           'textures/test/plain.tga': image('RGBA')})
            game = Game([root / 'baseq3'])
            try:
                textures = Textures(root / 'out')
                shader, unresolved, limits = describe(game, 'textures/test/glow', textures)
                lightmap, wall, glow = shader['stages']
                self.assertEqual((shader['cull'], shader['sort']), ('none', 3))
                self.assertEqual((lightmap['map'], lightmap['tcGen'], lightmap['rgbGen'], lightmap['depthWrite']), ('$lightmap', 'lightmap', ['identity'], True))
                # No rgbGen given: identity under a filter blend, and a blended stage does not write depth.
                self.assertEqual((wall['blend'], wall['rgbGen'], wall['depthWrite']), (['gl_dst_color', 'gl_zero'], ['identity'], False))
                self.assertTrue(wall['map'].endswith('.png') and glow['map'].endswith('.jpg'))  # The .tga name finds the .jpg.
                self.assertEqual((glow['blend'], glow['rgbGen'], glow['tcMods']), (['gl_one', 'gl_one'], ['wave', 'sin', .5, .25, 0, 2], [['scroll', 1, -.5]]))
                self.assertEqual((unresolved, limits), ([], []))
                # A box of which no side exists is not drawn by the game either; a missing stage image is reported.
                sky, unresolved, _ = describe(game, 'textures/test/sky', textures)
                self.assertEqual((sky['sky'], sky['sort'], sky['stages']), ({'cloudHeight': 256}, 2, []))
                self.assertEqual(unresolved, ['texture textures/test/missing.tga (shader textures/test/sky)'])
                plain, unresolved, _ = describe(game, 'textures/test/plain', textures)
                self.assertEqual((plain['implicit'], plain['map'][-4:], unresolved), (True, '.png', []))
                with Image.open(root / 'out' / plain['map']) as converted:
                    self.assertEqual(converted.mode, 'RGBA')
                self.assertEqual(describe(game, 'textures/test/absent', textures)[1], ['shader textures/test/absent (no script and no texture of that name)'])
            finally:
                game.close()

    def test_import_writes_draw_lumps_scenes_and_what_is_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'does not exist'):
                import_maps(root / 'absent', root / 'pack')
            (root / 'baseq3/maps').mkdir(parents=True)
            (root / 'missionpack').mkdir()
            with self.assertRaisesRegex(ValueError, 'No baseq3 folder'):
                import_maps(root, root / 'pack')
            entities = '{\n"classname" "worldspawn"\n}\n{\n"classname" "info_player_deathmatch"\n"origin" "8 16 24"\n"angle" "90"\n}\n' \
                '{\n"classname" "func_door"\n"model" "*1"\n"origin" "1 2 3"\n}\n{\n"classname" "weapon_railgun"\n"origin" "0 0 0"\n}\n'
            shaders = {'textures/test/glow': 0, 'textures/test/absent': 0, 'textures/common/caulk': 0x80, 'textures/test/unused': 0}
            first = bsp(shaders, [(0, 1, 0), (1, 3, -3), (2, 1, -1), (0, 4, -1)], entities)
            pk3(root / 'baseq3/pak0.pk3', {'maps/first.bsp': first, 'maps/broken.bsp': bsp({}, [], version=47), 'scripts/test.shader': SCRIPT,
                                           'scripts/arenas.txt': '{\nmap "first"\nlongname "First Map"\n}', 'textures/test/wall.tga': image(), 'textures/test/glow.tga': image()})
            (root / 'baseq3/maps/My Map.bsp').write_bytes(bsp({'textures/test/glow': 0}, [(0, 1, 0)]))
            pk3(root / 'missionpack/pak0.pk3', {'maps/first.bsp': bsp({'textures/test/glow': 0}, [(0, 1, 0)])})
            report = import_maps(root / 'baseq3', root / 'pack')  # The baseq3 folder itself is accepted too. Names are lower case, as the game compares them.
            self.assertEqual((report['imported'], report['skipped'], report['failed']), (['first', 'my map', 'first'], [], {'broken': 'IBSP version 47, not 46'}))
            self.assertEqual(report['unresolved'], {'first': ['shader textures/test/absent (no script and no texture of that name)']})
            index = json.loads((root / 'pack/index.json').read_text())
            self.assertEqual([(item['id'], item['group'], item['source'], item['longname']) for item in index], [
                ('first', 'Quake III Arena', 'pak0.pk3', 'First Map'), ('missionpack__first', 'Team Arena', 'pak0.pk3', 'First Map'),
                ('my_map', 'Your own maps', 'baseq3 folder', '')])
            scene = json.loads((root / 'pack/maps/first/scene.json').read_text())
            # Only shaders a drawn surface uses are described: not the no-draw one, nor one no face names.
            self.assertEqual([shader and shader['name'] for shader in scene['shaders']], ['textures/test/glow', 'textures/test/absent', None, None])
            self.assertEqual(scene['viewpoints'], [{'origin': [8, 16, 50], 'pitch': 0, 'yaw': 90}])
            self.assertEqual(scene['models'], [{'model': 1, 'origin': [1, 2, 3]}])
            self.assertEqual(scene['limits'], ['1 light flares', '1 pickups and flags (their models are not placed)'])
            lumps = read_bsp((root / 'pack' / scene['bsp']).read_bytes())
            self.assertEqual((lumps[1], lumps[13], lumps[14], lumps[2]), (*read_bsp(first)[1:2], read_bsp(first)[13], read_bsp(first)[14], b''))
            self.assertEqual(len(list((root / 'pack/textures').iterdir())), 1)  # Both textures hold the same image, stored once.
            again = import_maps(root, root / 'pack')
            self.assertEqual((again['imported'], sorted(again['skipped'])), ([], ['first', 'first', 'my map']))
            self.assertEqual(len(import_maps(root, root / 'pack', replace=True)['imported']), 3)
            self.assertEqual(map_id('My Map'), 'my_map')

    def test_routes_are_local_and_confined_to_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'q3-maps/bsp').mkdir(parents=True)
            (root / 'q3-maps/index.json').write_text('[]')
            (root / 'q3-maps/bsp/0123456789abcdef0123.bsp').write_bytes(b'IBSP')
            (root / 'secret.txt').write_text('outside pack')
            client = app.test_client()
            with patch('app.local_data_dir', root):
                with client.get('/maps/q3/') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
                with client.get('/q3-map-data/index.json') as response:
                    self.assertEqual(response.json, [])
                    self.assertNotIn('immutable', response.headers.get('Cache-Control', ''))
                with client.get('/q3-map-data/bsp/0123456789abcdef0123.bsp') as response:
                    self.assertIn('immutable', response.headers['Cache-Control'])
                with client.get('/q3-map-data/../secret.txt') as response:
                    self.assertEqual(response.status_code, 404)
                self.assertEqual(client.post('/import_q3_maps', json={'game': str(root)}, headers={'Origin': 'http://elsewhere.example'}).status_code, 403)
                self.assertEqual(client.post('/import_q3_maps', json={'game': ''}).status_code, 400)
                with client.post('/import_q3_maps', json={'game': str(root / 'absent')}) as response:
                    self.assertEqual((response.status_code, response.json['error']), (422, 'Quake 3 folder does not exist'))

    @unittest.skipUnless(os.environ.get('Q3_GAME_BASE'), 'set Q3_GAME_BASE to a Quake 3 Arena folder to import its maps')
    def test_stock_maps_import_with_nothing_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            report = import_maps(os.environ['Q3_GAME_BASE'], Path(directory) / 'pack')
            self.assertEqual(report['failed'], {})
            index = json.loads((Path(directory) / 'pack/index.json').read_text())
            stock = [item for item in index if item['group'] == 'Quake III Arena']
            self.assertGreaterEqual(len(stock), 30)
            self.assertEqual([item['name'] for item in stock if item['unresolved']], [])
            scene = json.loads((Path(directory) / 'pack/maps/q3dm1/scene.json').read_text())
            self.assertEqual((scene['longname'], len(scene['viewpoints']) > 4), ('Arena Gate', True))


if __name__ == '__main__':
    unittest.main()

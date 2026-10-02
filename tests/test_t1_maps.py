"""Tribes 1 map import and serving boundaries; the full import runs only when T1_GAME_BASE is set."""
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from app import app
from tools.import_t1_map import import_map, lzh_expand, parse_mission, placement, read_terrain_index, walk

MISSION = '''//--- export object begin ---//
instant SimGroup "MissionGroup" {
	instant SimVolume "tedFile" {
		fileName = "Raindance.ted";
	};
	instant InteriorShape {
		fileName = "cube.0.dis";
		position = "1 2 3";
	};
};
//--- export object end ---//
exec("server/game/ctf");
'''


class T1MapTests(unittest.TestCase):
    def test_mission_parser_handles_unnamed_objects_and_stops_at_export_end(self):
        nodes = [(node['class'], node['name'], groups) for node, groups in walk(parse_mission(MISSION))]
        self.assertEqual(nodes[1:], [('SimGroup', 'MissionGroup', ('',)), ('SimVolume', 'tedFile', ('', 'MissionGroup')),
                                     ('InteriorShape', '', ('', 'MissionGroup'))])
        with self.assertRaisesRegex(ValueError, 'Unbalanced'):
            parse_mission('instant SimGroup "open" {')

    def test_placement_maps_z_up_to_viewer_space(self):
        self.assertEqual(placement((1, 2, 3), (0, 0, 0)), [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 3, -2, 1])
        # A positive DarkStar z rotation turns the file x axis toward file +y, which is viewer -z.
        turned = placement((0, 0, 0), (0, 0, math.pi / 2))
        for actual, expected in zip(turned[:3], (0, 0, -1)):
            self.assertAlmostEqual(actual, expected)

    def test_lzh_matches_the_decoder_verified_on_stock_terrain(self):
        output, consumed = lzh_expand(bytes((index * 37 + 11) & 255 for index in range(400)), 0, 300)
        self.assertEqual((len(output), consumed), (300, 65))
        self.assertEqual(hashlib.sha256(output).hexdigest(), 'be7d5874ddcf7b4979f83518f80faa412bea666310d5f9aa4b174379bb18bf51')
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            lzh_expand(b'\x00', 0, 300)

    def test_terrain_index_layout(self):
        data = (b'GFIL' + struct.pack('<iii', 0, 1, 8) + b'lush.dml' + struct.pack('<iii', 1, 9, 3) + bytes(40)
                + struct.pack('<iii', 3, 3, 0) + struct.pack('<9i', *[0] * 9))
        self.assertEqual(read_terrain_index(data), {'materialList': 'lush.dml', 'squares': 256, 'unit': 8, 'columns': 3,
                                                    'rows': 3, 'blockMap': (0,) * 9})

    def test_missing_mission_and_existing_pack_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'Missing stock mission'):
                import_map(root, root / 'pack')
            self.assertFalse((root / 'pack').exists())
            (root / 'pack').mkdir()
            with self.assertRaisesRegex(ValueError, 'never overwritten'):
                import_map(root, root / 'pack')

    def test_routes_are_local_and_confined_to_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 't1-maps').mkdir()
            (root / 't1-maps/scene.json').write_text('{"objects":[]}')
            (root / 'secret.txt').write_text('outside pack')
            client = app.test_client()
            with patch('app.local_data_dir', root):
                with client.get('/maps/t1/') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
                with client.get('/t1-map-data/scene.json') as response:
                    self.assertEqual(response.json, {'objects': []})
                with client.get('/t1-map-data/../secret.txt') as response:
                    self.assertEqual(response.status_code, 404)

    @unittest.skipUnless(os.environ.get('T1_GAME_BASE'), 'set T1_GAME_BASE to a Tribes 1 base folder to import Raindance')
    def test_raindance_objects_resolve_and_spawns_sit_on_the_terrain(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory) / 'pack'
            scene = import_map(Path(os.environ['T1_GAME_BASE']), pack)
            self.assertEqual(scene['warnings'], [])
            self.assertEqual((len(scene['objects']), len(scene['viewpoints'])), (56, 7))
            self.assertEqual(json.loads((pack / 'scene.json').read_text())['objects'], scene['objects'])
            heights = struct.unpack('<66049f', (pack / 'heights.bin').read_bytes())
            mission = parse_mission((Path(os.environ['T1_GAME_BASE']) / 'missions/Raindance.MIS').read_text(encoding='cp1252'))
            spawns = [[float(value) for value in node['fields']['position'].split()]
                      for node, groups in walk(mission) if node['class'] == 'Marker' and 'Random' in groups]

            def ground(x, y):  # Row-major heights, x then y, eight units per square, bilinear.
                column, row = (x + 3072) / 8 % 256, (y + 3072) / 8 % 256
                i, j, u, v = int(column), int(row), column % 1, row % 1
                south = heights[j * 257 + i] * (1 - u) + heights[j * 257 + i + 1] * u
                north = heights[(j + 1) * 257 + i] * (1 - u) + heights[(j + 1) * 257 + i + 1] * u
                return south * (1 - v) + north * v

            above = sorted(z - ground(x, y) for x, y, z in spawns)
            self.assertEqual(len(above), 15)
            # Spawn points stand on the ground; the last one stands on a base roof.
            self.assertTrue(-1 < above[0] and above[-2] < 5, above)


if __name__ == '__main__':
    unittest.main()

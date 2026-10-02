"""Tribes 1 map import and serving boundaries; the real import runs only when T1_GAME_BASE is set."""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app import app
from tools.import_t1_map import (Install, bitmap_png, import_maps, lzh_expand, map_id, open_volume, parse_mission,
                                 placement, read_palettes, read_terrain_index, walk)

MISSION = '''//--- export object begin ---//
instant SimGroup "MissionGroup" {
	instant SimVolume "tedFile" {
		fileName = "Raindance.ted";
	};
	instant InteriorShape {
		filename = "cube.0.dis";
		position = "1 2 3";
	};
};
//--- export object end ---//
exec("server/game/ctf");
'''


def pvol(members):
    """A classic volume: {name: (stored bytes, expanded size, compression)}."""
    blocks, records, names = b'', b'', b''
    for name, (stored, size, compression) in members.items():
        records += struct.pack('<IIIIB', 0, len(names), 8 + len(blocks), size, compression)
        blocks += b'VBLK' + len(stored).to_bytes(3, 'little') + b'\x80' + stored
        names += name.encode() + b'\0'
    return (b'PVOL' + struct.pack('<I', 8 + len(blocks)) + blocks + b'vols' + struct.pack('<I', len(names)) + names
            + b'\0' * (len(names) & 1) + b'voli' + struct.pack('<I', len(records)) + records)


def chunk(tag, body):
    return tag + struct.pack('<I', len(body)) + body


class T1MapTests(unittest.TestCase):
    def test_mission_parser_is_case_insensitive_and_stops_at_export_end(self):
        nodes = list(walk(parse_mission(MISSION)))
        self.assertEqual([(node['class'], node['name'], groups) for node, groups in nodes][1:], [
            ('simgroup', 'MissionGroup', ('',)), ('simvolume', 'tedFile', ('', 'missiongroup')),
            ('interiorshape', '', ('', 'missiongroup'))])
        self.assertEqual((nodes[2][0]['fields']['filename'], nodes[3][0]['fields']['filename']), ('Raindance.ted', 'cube.0.dis'))
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

    def test_classic_volumes_are_read_and_found_under_either_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'classic.vol').write_bytes(pvol({'Plain.TXT': (b'stored', 6, 0), 'runs.txt': (b'\x83a\x02bc', 5, 1)}))
            members = open_volume(root / 'classic.vol')
            self.assertEqual({name: read() for name, read in members.items()}, {'plain.txt': b'stored', 'runs.txt': b'aaabc'})
            (root / 'broken.vol').write_bytes(b'PVOL' + bytes(12))
            with self.assertRaisesRegex(ValueError, 'Unsupported volume'):
                open_volume(root / 'broken.vol')
            # Newer installs ship .zip where missions name .vol and the reverse; either name finds the file.
            self.assertEqual(Install(root).find('Classic.zip', [root]), root / 'classic.vol')

    def test_indexed_bitmap_takes_colours_from_the_palette_it_names(self):
        colours = bytes([255, 0, 0, 0, 0, 255, 0, 0, 0, 0, 255, 0, 255, 255, 255, 0]) + bytes(252 * 4)
        palettes, haze = read_palettes(b'PL98' + struct.pack('<iiii', 1, 0, 0, 2) + bytes(32) + colours + struct.pack('<II', 7, 0))
        self.assertEqual(haze, [0, 0, 255])
        bitmap = (b'PBMP' + bytes(4) + chunk(b'head', struct.pack('<5i', 0, 2, 2, 8, 0))
                  + chunk(b'data', bytes([0, 1, 9, 9, 2, 3, 9, 9])) + chunk(b'DETL', struct.pack('<i', 1)) + chunk(b'PiDX', struct.pack('<I', 7)))
        with Image.open(io.BytesIO(bitmap_png(bitmap, palettes))) as image:
            self.assertEqual([image.getpixel((x, y)) for y in range(2) for x in range(2)], [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])
        with self.assertRaisesRegex(ValueError, 'no palette'):
            bitmap_png(bitmap, {})

    def test_import_reports_bad_input_and_failed_missions_without_writing_maps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'Game folder not found'):
                import_maps(root / 'absent', root / 'pack')
            (root / 'base/missions').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'No mission files found'):
                import_maps(root, root / 'pack')
            (root / 'base/missions/My Map.mis').write_text(MISSION)
            report = import_maps(root, root / 'pack')
            self.assertEqual((report['imported'], report['failed']), ([], {'My Map': 'Mission has no terrain'}))
            self.assertEqual(json.loads((root / 'pack/index.json').read_text()), [])
            self.assertFalse(list((root / 'pack').glob('maps/*')))
            self.assertEqual(map_id('My Map'), 'my_map')

    def test_routes_are_local_and_confined_to_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 't1-maps').mkdir()
            (root / 't1-maps/index.json').write_text('[]')
            (root / 'secret.txt').write_text('outside pack')
            client = app.test_client()
            with patch('app.local_data_dir', root):
                with client.get('/maps/t1/') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
                with client.get('/t1-map-data/index.json') as response:
                    self.assertEqual(response.json, [])
                with client.get('/t1-map-data/../secret.txt') as response:
                    self.assertEqual(response.status_code, 404)
                self.assertEqual(client.post('/import_t1_maps', json={'game': str(root)}, headers={'Origin': 'http://elsewhere.example'}).status_code, 403)
                self.assertEqual(client.post('/import_t1_maps', json={'game': ''}).status_code, 400)
                with client.post('/import_t1_maps', json={'game': str(root / 'absent')}) as response:
                    self.assertEqual((response.status_code, response.json['error'][:21]), (422, 'Game folder not found'))

    @unittest.skipUnless(os.environ.get('T1_GAME_BASE'), 'set T1_GAME_BASE to a Tribes 1 folder to import Raindance')
    def test_raindance_objects_resolve_and_spawns_sit_on_the_terrain(self):
        with tempfile.TemporaryDirectory() as directory:
            pack, install = Path(directory) / 'pack', Install(os.environ['T1_GAME_BASE'])
            mission_file = install.find('Raindance.mis', [install.base / 'missions'])
            report = import_maps(install.base, pack, [mission_file])
            self.assertEqual((report['imported'], report['failed'], report['warnings']), (['Raindance'], {}, {}))
            scene = json.loads((pack / 'maps/raindance/scene.json').read_text())
            # The placed-object count differs slightly between game versions; every one resolves to a model.
            self.assertGreaterEqual(len(scene['objects']), 50)
            self.assertTrue(all(item['model'] for item in scene['objects']))
            self.assertEqual(len(scene['viewpoints']), 7)
            self.assertEqual(json.loads((pack / 'index.json').read_text())[0]['id'], 'raindance')
            for stored in scene['terrain']['textures'].values():
                self.assertTrue((pack / 'textures' / stored).is_file())
            self.assertEqual(import_maps(install.base, pack, [mission_file])['skipped'], ['Raindance'])
            # A custom mission elsewhere that forgets to mount its terrain volume still finds Raindance.ted in the install.
            custom = Path(directory) / 'Unmounted.mis'
            custom.write_text(re.sub(r'fileName = "Raindance\.ted";', 'fileName = "";', mission_file.read_text(encoding='cp1252'), flags=re.I), encoding='cp1252')
            self.assertEqual(import_maps(install.base, pack, [custom])['imported'], ['Unmounted'])
            heights = struct.unpack('<66049f', (pack / 'maps/raindance/heights.bin').read_bytes())
            mission = parse_mission(mission_file.read_text(encoding='cp1252'))
            spawns = [[float(value) for value in node['fields']['position'].split()]
                      for node, groups in walk(mission) if node['class'] == 'marker' and 'random' in groups]

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

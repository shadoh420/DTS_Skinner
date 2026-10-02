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
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from app import app
from tools.import_t1_map import (Install, bake_lightmap, bitmap_png, import_maps, interior_dml, light_colours, lzh_expand, map_id,
                                 open_volume, parse_mission, placement, read_lighting, read_palettes, read_terrain_block,
                                 read_terrain_index, walk)

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
        # Sky sprites keep transparency: a bitmap flagged colour-keyed (1) is clear at palette index 0, one flagged
        # translucent (4) takes each palette entry's fourth byte, and an unflagged one stays opaque.
        colours = bytes([255, 0, 0, 0, 0, 255, 0, 128, 0, 0, 255, 255, 255, 255, 255, 255]) + bytes(252 * 4)
        palettes, _ = read_palettes(b'PL98' + struct.pack('<iiii', 1, 0, 0, 2) + bytes(32) + colours + struct.pack('<II', 7, 2))
        for flags, alphas in ((1, [0, 255, 255, 255]), (4, [0, 128, 255, 255]), (8, [255] * 4)):
            sprite = (b'PBMP' + bytes(4) + chunk(b'head', struct.pack('<5i', 0, 2, 2, 8, flags))
                      + chunk(b'data', bytes([0, 1, 9, 9, 2, 3, 9, 9])) + chunk(b'PiDX', struct.pack('<I', 7)))
            with Image.open(io.BytesIO(bitmap_png(sprite, palettes, True))) as image:
                self.assertEqual((image.mode, [image.getpixel((x, y))[3] for y in range(2) for x in range(2)], image.getpixel((1, 0))[:3]),
                                 ('RGBA', alphas, (0, 255, 0)))

    def test_raw_terrain_blocks_and_version_2_material_lists_are_read(self):
        heights, light = struct.pack('<4f', 1, 2, 3, 4), struct.pack('<4H', 255, 128, 16, 0)  # One 8-bit light level per word.
        block = read_terrain_block(b'GBLK' + struct.pack('<Ii16siiffii', 0, 0, b'block-0', 1, 0, 1, 4, 1, 1) + heights + b'\x00\x07' + light)
        self.assertEqual((block['heights'], block['materials'], block['lightWidth'], block['light']),
                         (heights, b'\x00\x07', 2, struct.pack('<4H', 0xffff, 0x8888, 0x1111, 0)))
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            read_terrain_block(b'GBLK' + struct.pack('<Ii16siiffii', 0, 0, b'block-0', 1, 0, 1, 4, 1, 1) + heights)
        with self.assertRaisesRegex(ValueError, 'Unexpected terrain block layout'):  # A size the data does not fill exactly.
            read_terrain_block(b'GBLK' + struct.pack('<Ii16siiffii', 0, 0, b'block-0', 1, 0, 1, 4, 1, 1) + heights + b'\x00\x07' + light + b'\x00')
        materials = interior_dml.dml()  # Version 2 records end at the 32-byte name.
        materials.load_binary(b'PERS' + bytes(4) + struct.pack('<H', 16) + b'TS::MaterialList' + struct.pack('<3i', 2, 1, 2)
                              + bytes(16) + b'lsky0.BMP'.ljust(32, b'\0') + bytes(16) + b'lsky1.bmp'.ljust(32, b'\0'))
        self.assertEqual([material.name for material in materials.materials], ['lsky0.BMP', 'lsky1.bmp'])

    def test_lightmap_bake_adds_light_states_and_sun_and_maps_vertices_into_the_atlas(self):
        def lighting(kind, surfaces, maps, tail, states=b'', state_data=b'', lights=b''):
            body = (struct.pack('<8i', 77, 0, 1, len(states) // 16, len(state_data) // 8, len(lights) // 24, len(surfaces) // 12, len(maps))
                    + states + state_data + lights + surfaces + maps + struct.pack('<I', 0) + tail)
            return b'PERS' + bytes(4) + struct.pack('<H', len(kind)) + kind + bytes(len(kind) & 1) + struct.pack('<i', 7) + body

        # One 2x1 Huffman-coded surface (bit 1 -> leaf 0, bit 0 -> leaf 1) and one light whose state 0 is full red
        # with an intensity map of 255 and 8: the first texel gains red, the second is below the threshold.
        base = read_lighting(lighting(
            b'ITRLighting', struct.pack('<i2h4B', 0x40000000, 1, 0, 2, 1, 0, 0), b'\x01\x00\xff\x08',
            b'\x01' + struct.pack('<2i', 1, 2) + struct.pack('<2i', -1, -2) + struct.pack('<2I', 0x123, 0x456),
            states=struct.pack('<4Hf2h', 0xff00, 0, 0, 0, 0, 1, 0), state_data=struct.pack('<2hi', 0, 0, 2),
            lights=struct.pack('<4ifI', 0, -1, 1, 0, 0, 0)))
        self.assertEqual((base['replaced'], light_colours(base, 0)), (None, [0x123, 0x456]))
        mission = read_lighting(lighting(b'ITRMissionLighting', b'', b'', b'\x00' + struct.pack('<i', 0) + b'\x00'))
        self.assertEqual(mission['replaced'], {})
        with self.assertRaisesRegex(ValueError, 'Unsupported interior lighting'):
            read_lighting(b'PERS' + bytes(40))

        surface = SimpleNamespace(num_verts=3, vert_id=0, tsx=1, tsy=0, flags=0xc0, plane_id=0)
        geometry = SimpleNamespace(build_id=77, surfaces=[surface], verts=[(0, 0), (0, 1), (0, 2)], points2f=[(0, 0), (1, 0), (1, 1)],
                                   planes=[SimpleNamespace(x=0, y=0, z=1)])
        normals = []
        png, coordinates = bake_lightmap(geometry, base, mission, lambda normal: normals.append(normal) or (0, 1, 2))
        with Image.open(io.BytesIO(png)) as atlas:  # 2x1 map inside a one-texel gutter of its own edge texels.
            self.assertEqual((atlas.size, atlas.getpixel((1, 1)), atlas.getpixel((2, 1)), atlas.getpixel((0, 0)), atlas.getpixel((3, 2))),
                             ((4, 3), (255, 3 * 17, 5 * 17), (4 * 17, 6 * 17, 8 * 17), (255, 3 * 17, 5 * 17), (4 * 17, 6 * 17, 8 * 17)))
        self.assertEqual(normals, [(0, 0, 1)])
        for actual, expected in zip(struct.unpack('<6f', coordinates), (1.5 / 4, .5, 3.5 / 4, .5, 3.5 / 4, 2.5 / 3)):
            self.assertAlmostEqual(actual, expected)
        geometry.build_id = 78
        with self.assertRaisesRegex(ValueError, 'does not match'):
            bake_lightmap(geometry, base)

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
            # Every building carries its mission lightmap: an atlas plus two coordinates per model vertex.
            lit = [item for item in scene['objects'] if 'light' in item]
            self.assertGreaterEqual(len(lit), 30)
            for item in lit:
                with Image.open(pack / 'textures' / item['light']['map']) as atlas:
                    self.assertEqual(atlas.mode, 'RGB')
                model = json.loads((Path(app.static_folder) / 'model_json' / (item['model'] + '.json')).read_text())
                self.assertEqual((pack / 'textures' / item['light']['uv']).stat().st_size, len(model['vertices']) // 3 * 8)
            self.assertEqual((len(scene['sky']['textures']), scene['weather']['rain']), (16, True))
            # Raindance's sun has no bitmap and it has no star field; Blastside's sun has one, with a lens flare.
            self.assertEqual((scene['planets'], scene['stars'], scene['flare']), ([], None, None))
            self.assertEqual(import_maps(install.base, pack, [install.find('Blastside.mis', [install.base / 'missions'])])['failed'], {})
            blastside = json.loads((pack / 'maps/blastside/scene.json').read_text())
            self.assertEqual(([planet['flare'] for planet in blastside['planets']], len(blastside['flare'])), ([True], 6))
            for stored in [blastside['planets'][0]['texture'], *blastside['flare']]:
                with Image.open(pack / 'textures' / stored) as sprite:
                    self.assertEqual((sprite.mode, sprite.getextrema()[3][0]), ('RGBA', 0))
            for stored in [*scene['terrain']['textures'].values(), *scene['sky']['textures']]:
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

"""Starsiege Maps: binary missions (tools/starsiege_mission.py), stitched terrain grids and older terrain blocks
(tools/import_t1_map.py), and the page's routes. Every mission here is built from the block rules, not the install."""
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app import app
from tools.import_t1_map import read_terrain_block, rotation_rows, stitch_terrain, walk
from tools.starsiege_mission import read_mission, read_tags


def block(tag, body):
    tag = tag if isinstance(tag, bytes) else struct.pack('<I', tag)
    return tag + struct.pack('<I', len(body)) + body + b'\0' * (len(body) % 2)


def group(children):
    """A SimGroup: version, count, the children, then a u8-length name each."""
    body = struct.pack('<ii', 0, len(children)) + b''.join(data for data, _ in children)
    return block(b'SIMG', body + b''.join(bytes([len(name)]) + name.encode() for _, name in children))


def text(value):
    return bytes([len(value)]) + value.encode()


def model(header, position, rotation, name):
    """A Starsiege shape (0x100) or interior (0x10c): position and Euler at their offsets, an int32-length file name."""
    body = bytearray(header) + struct.pack('<6f', *position, *rotation) + bytes(20)
    return bytes(body) + struct.pack('<i', len(name) + 1) + name.encode() + b'\0' + bytes(8)


def mission():
    rows = rotation_rows((0.1, -0.2, 1.3))
    return group([
        (group([(block(b'SVol', bytes(24) + text('Europa.Sim.vol')), 'terrainVol')]), 'Volumes'),
        (block(b'STER', bytes(24) + struct.pack('<6f4f2f', -12288, -12288, 0, 0, 0, 0, 1100, 500, 0, 0, 120, 140) + text('DM_X.ted.dtf') + bytes(20)), 'terrain'),
        (block(b'SKYT', bytes(24) + struct.pack('<3fIfff', 0, 0, 0.5, 252031, 120, 7500, 20000) + bytes(range(1, 17))), 'sky'),
        (block(b'SKPL', bytes(24) + struct.pack('<Iff??3f3fff', 252033, -28.6, 17.5, True, False, .6, .6, .6, .1, .1, .1, 2000, 19000)), 'planet'),
        (block(b'ESpt', bytes(24) + text('Europa.t.ppl')), 'terrainPal'),
        (block(b'SNOW', bytes(24) + struct.pack('<4f??', .3, 20, 10, -85, False, True)), 'Rain1'),  # CTF_Stab_n_Grab's bytes, wind changed.
        (block(0x100, model(bytes(48), (191, 521.25, 227.5), (0, 0, 1.5), 'xblastdoor_anim.dts')), 'Door1'),
        (block(0x10c, model(bytes(55), (203.25, 960, 223), (0, 0, 3.1), 'hRuinsT2.dis')), 'Ruins'),
        (block(b'ESNM', bytes(88)), 'navigationMarker1'),  # Not drawn.
        (block(b'ESDP', struct.pack('<ii', 0, 1) + block(b'DPNT', bytes(28) + struct.pack('<9f3f', *rows[0], *rows[1], *rows[2], 10, 20, 30)) + text('dropPoint1')), 'DropPointGroup'),
    ])


class StarsiegeMissionTest(unittest.TestCase):
    def test_blocks_become_t1_nodes(self):
        tags = read_tags('IDDML_SKY_EUROPA_DUSK = 00252031, "europa.t.sky.dml";\nIDBMP_SKY_EUROPA_MOON_DAY = 00252033, "europa.d.moon.bmp";')
        nodes = {node['name']: node for node, _ in walk(read_mission(mission(), tags))}
        self.assertEqual(nodes['terrainVol']['fields'], {'filename': 'Europa.Sim.vol'})
        self.assertEqual(nodes['terrain']['fields']['tedfilename'], 'DM_X.ted.dtf')
        self.assertEqual(nodes['terrain']['fields']['position'], '-12288 -12288 0')
        self.assertEqual((nodes['terrain']['fields']['visibledistance'], nodes['terrain']['fields']['hazedistance']), ('1100', '500'))
        sky = nodes['sky']['fields']
        self.assertEqual((sky['dmlname'], sky['size'], sky['distance'], sky['featureposition'], sky['textures[15]']),
                         ('europa.t.sky.dml', '7500', '20000', '120', '16'))
        planet = nodes['planet']['fields']
        self.assertEqual((planet['filename'], planet['azimuth'], planet['castshadows'], planet['uselensflare'], planet['size']),
                         ('europa.d.moon.bmp', '-28.6', 'true', 'false', '2000'))
        self.assertEqual(nodes['terrainPal']['fields'], {'filename': 'Europa.t.ppl'})
        self.assertEqual((nodes['Rain1']['class'], nodes['Rain1']['fields']),
                         ('snowfall', {'intensity': '0.3', 'wind': '20 10 -85', 'suspendrendering': 'false', 'rain': 'true'}))
        self.assertEqual(nodes['Door1']['fields'], {'shapefile': 'xblastdoor_anim.dts', 'position': '191 521.25 227.5', 'rotation': '0 0 1.5'})
        self.assertEqual((nodes['Ruins']['class'], nodes['Ruins']['fields']['filename'], nodes['Ruins']['fields']['position']),
                         ('interiorshape', 'hRuinsT2.dis', '203.25 960 223'))
        self.assertNotIn('navigationMarker1', nodes)
        drop = nodes['dropPoint1']['fields']
        self.assertEqual((nodes['dropPoint1']['class'], drop['position']), ('droppoint', '10 20 30'))
        # The drop point's rotation rows come back as the Euler angles that make them.
        for got, want in zip(rotation_rows([float(v) for v in drop['rotation'].split()]), rotation_rows((0.1, -0.2, 1.3))):
            self.assertTrue(all(math.isclose(a, b, abs_tol=1e-5) for a, b in zip(got, want)))

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            read_mission(b'instant SimGroup "MissionGroup" {')

    def test_terrain_grid_is_stitched_with_shared_edges(self):
        def piece(value):  # A 2-square block whose heights, materials and light all say which block it is.
            return {'size': 2, 'lightWidth': 3, 'heights': np.full(9, value, '<f4').tobytes(),
                    'materials': np.full(4, value, '<u2').tobytes(), 'light': np.full(9, value, '<u2').tobytes()}
        stitched = stitch_terrain({'columns': 2, 'rows': 2, 'blockMap': [1, 2, 3, 1]}, piece)
        heights = np.frombuffer(stitched['heights'], '<f4').reshape(5, 5)
        self.assertEqual((stitched['size'], stitched['lightWidth']), (4, 5))
        self.assertEqual(heights[0, 0], 1)
        self.assertEqual(heights[0, 4], 2)   # Row 0, column 1: block 2.
        self.assertEqual(heights[4, 0], 3)   # Row 1, column 0: block 3.
        self.assertEqual(np.frombuffer(stitched['materials'], '<u2').reshape(4, 4)[3, 3], 1)

    def test_version_3_block_rows_are_delta_coded(self):
        size, scale = 2, .5
        header = b'GBLK' + struct.pack('<Ii16siiffii', 0, 3, bytes(16), 1, 0, 0.0, 12.0, size, size)
        heights = struct.pack('<3f', 0, 1, 2)                                 # Top row as is.
        heights += struct.pack('<2f', scale, 4) + struct.pack('<b', 6) + struct.pack('<f', 9)  # 4, then 4 + 6 * .5, then 9.
        heights += struct.pack('<3f', 10, 11, 12)                             # Bottom row as is.
        pins = struct.pack('<H', 0) * 11
        data = header + heights + bytes(size * size * 2) + pins + bytes(3 * 3 * 2)
        result = read_terrain_block(data)
        self.assertEqual(struct.unpack('<9f', result['heights']), (0, 1, 2, 4, 7, 9, 10, 11, 12))


class StarsiegeMapRoutesTest(unittest.TestCase):
    def test_page_data_and_import_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ss-maps').mkdir()
            (root / 'ss-maps/index.json').write_text(json.dumps([]))
            (root / 'secret.txt').write_text('outside the pack')
            client = app.test_client()
            with patch('app.local_data_dir', root):
                page = client.get('/maps/ss/')
                self.assertEqual(page.status_code, 200)
                self.assertIn(b'/static/ss-maps/config.js', page.data)
                self.assertEqual(client.get('/ss-map-data/index.json').json, [])
                self.assertEqual(client.get('/ss-map-data/../secret.txt').status_code, 404)
                self.assertEqual(client.post('/import_ss_maps', json={'game': ''}).status_code, 400)
                self.assertEqual(client.post('/import_ss_maps', json={'game': str(root)}).status_code, 422)  # No Starsiege here.


if __name__ == '__main__':
    unittest.main()

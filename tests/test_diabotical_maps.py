"""Diabotical map import (tools/import_diabotical_map.py) and serving boundaries, and the page's JavaScript tests
(tests/diabotical_maps.test.cjs) when Node is installed. Set DIABOTICAL_GAME_BASE to a Diabotical folder to also
import that install."""
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app import app
from tools.import_diabotical_map import OUT, import_maps, map_id, read_map, visible_blocks

RECORD = {24: 46, 25: 52, 26: 53, 27: 53}


def rbe(blocks, materials=('default', 'stone', 'stone:2'), version=27, author='Someone'):
    """A .rbe map as the game writes one: blocks are (x, y, z, shape, turn, six face materials)."""
    size, turn_at = RECORD.get(version, 53), 44 if version == 24 else 50
    body = bytes([len(materials) + 1]) + b''.join(struct.pack('<I', len(name)) + name.encode() for name in (*materials, ''))
    body += struct.pack('<I', len(blocks))
    for x, y, z, shape, turn, faces in blocks:
        record = bytearray(size)
        struct.pack_into('<3i', record, 0, x, y, z)
        record[12], record[turn_at], record[25:31] = shape, turn, bytes(faces)
        body += bytes(record)
    body += b'\0' * 64  # The map's other parts, which the import does not read.
    head = b'REBM' + struct.pack('<III', version, 0x12345678, 0) + struct.pack('<I', len(author)) + author.encode() + bytes(8)
    return head + (gzip.compress(body) if version >= 24 else body)


def dbp(files):
    """A .dbp pack holding {backslash path: bytes}."""
    listing, data = b'', b''
    for name, raw in files.items():
        listing += struct.pack('<I', len(name)) + name.encode() + struct.pack('<II', len(data), len(raw))
        data += raw
    return b'DBP1' + struct.pack('<II', 0, len(files)) + listing + data


def dds(colour):
    from PIL import Image
    output = io.BytesIO()
    Image.new('RGB', (8, 8), colour).save(output, 'DDS', pixel_format='DXT1')
    return output.getvalue()


def install(root, maps):
    (root / 'packs').mkdir(parents=True)
    (root / 'packs/maps.dbp').write_bytes(dbp({f'maps\\{name}.rbe': raw for name, raw in maps.items()} | {'maps\\walk-b.png': b'png'}))
    (root / 'packs/scripts.dbp').write_bytes(dbp({
        'scripts\\walk.assets': b'asset stone\n{\n  type surface_material\n  material stone_floor\n}\n// asset gone { type surface_material material gone }\n',
        'scripts\\walk.shader': b'stone_floor\n{\n {\n\t\tmap textures/walk/missing_d.png\n }\n}\n// default { { map x } }\n',
    }))
    (root / 'packs/textures.dbp').write_bytes(dbp({
        'textures\\walk.shader': b'stone_floor\n{\n {\n\t\tmap textures/walk/stone_d.png\n\t\tmap textures/flat_normal.png\n\t\tuv_scale 0.125\n }\n}\n',
        'textures\\walk\\stone_d.png.dds': dds((200, 100, 50)),
    }))
    (root / 'packs/audio.dbp').write_bytes(b'not a pack: audio packs are not read')


CUBE = (1, 1, 1, 1, 2, 0)


class DiaboticalMapsTest(unittest.TestCase):
    def test_map_records_by_version(self):
        for version in (24, 25, 26, 27):
            parsed = read_map(rbe([(3, -2, 5, 3, 2, (1, 2, 1, 2, 0, 1))], version=version))
            self.assertEqual((parsed['version'], parsed['author'], parsed['materials']), (version, 'Someone', ['default', 'stone', 'stone:2', '']))
            blocks = parsed['blocks']
            self.assertEqual((blocks['xyz'].tolist(), blocks['shape'].tolist(), blocks['turn'].tolist(), blocks['faces'].tolist()),
                             ([[3, -2, 5]], [3], [2], [[1, 2, 1, 2, 0, 1]]))
        with self.assertRaisesRegex(ValueError, 'version 21 is not read yet'):
            read_map(rbe([], version=21))
        with self.assertRaisesRegex(ValueError, 'not a Diabotical map'):
            read_map(b'RIFF' + bytes(40))

    def test_faces_between_cubes_close_and_buried_cubes_are_left_out(self):
        solid = [(x, y, z, 1, 0, CUBE) for x in range(3) for y in range(3) for z in range(3)]
        blocks = visible_blocks(read_map(rbe(solid + [(5, 0, 0, 3, 1, CUBE), (7, 0, 0, 2, 0, CUBE)]))['blocks'])
        self.assertEqual(blocks.dtype, OUT)
        self.assertEqual(OUT.itemsize, 16)
        at = {(int(b['x']), int(b['y']), int(b['z'])): b for b in blocks}
        self.assertNotIn((1, 1, 1), at)  # Closed on every side.
        self.assertNotIn((7, 0, 0), at)  # Draws nothing.
        self.assertEqual(len(blocks), 26 + 1)
        # Faces +z, -x, -z, +x, top, bottom: the corner (0, 0, 0) is open toward -x, -z and below.
        self.assertEqual(at[(0, 0, 0)]['open'], 0b100110)
        self.assertEqual(at[(1, 2, 1)]['open'], 0b010000)
        self.assertEqual((at[(5, 0, 0)]['open'], at[(5, 0, 0)]['turn'], at[(5, 0, 0)]['shape']), (0x3f, 1, 3))

    def test_import_writes_blocks_index_and_material_textures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install(root / 'game', {'walk': rbe([(0, 0, 0, 1, 0, CUBE), (1, 0, 0, 1, 0, (2, 2, 2, 2, 0, 0))]), 'old menu': rbe([], version=21)})
            mine = root / 'Mine.rbe'
            mine.write_bytes(rbe([(0, 0, 0, 3, 0, CUBE)], materials=('default', 'gone')))
            result = import_maps(root / 'game', root / 'pack', extra=[mine])
            self.assertEqual((result['imported'], result['skipped']), (['walk', 'Mine'], []))
            self.assertEqual(result['failed'], {'old menu': 'map version 21 is not read yet'})
            self.assertEqual(result['untextured'], ['default', 'gone'])
            index = json.loads((root / 'pack/index.json').read_text())
            self.assertEqual([(item['id'], item['group'], item['blocks']) for item in index], [('walk', 'Diabotical', 2), ('user__mine', 'Your maps', 1)])
            blocks = np.frombuffer((root / 'pack/maps' / index[0]['file']).read_bytes(), OUT)
            self.assertEqual(blocks['open'].tolist(), [0b110111, 0b111101])
            materials = json.loads((root / 'pack/materials.json').read_text())
            # stone is stone_floor's asset; of its two definitions, the one whose texture is in the packs is used,
            # and stone:2 is a variant of it.
            self.assertEqual(materials['stone']['scale'], .125)
            self.assertEqual(materials['stone:2'], materials['stone'])
            from PIL import Image
            with Image.open(root / 'pack/textures' / materials['stone']['texture']) as image:
                self.assertLess(max(abs(a - b) for a, b in zip(image.getpixel((4, 4)), (200, 100, 50))), 8)  # DXT1 rounds to 5:6:5.
            again = import_maps(root / 'game', root / 'pack', extra=[mine])
            self.assertEqual((again['imported'], again['skipped']), ([], ['walk', 'Mine']))
            mine.write_bytes(rbe([(0, 0, 0, 1, 0, CUBE)]))
            again = import_maps(root / 'game', root / 'pack', extra=[mine])
            self.assertEqual(again['imported'], ['Mine'])
            self.assertEqual(sorted(path.name.split('-')[0] for path in (root / 'pack/maps').iterdir()), ['user__mine', 'walk'])
            with self.assertRaisesRegex(ValueError, 'Enter the Diabotical folder'):
                import_maps(root, root / 'pack')

    def test_map_id(self):
        self.assertEqual(map_id('duel_F1sks House'), 'duel_f1sks_house')

    def test_routes_are_local_and_confined_to_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'diabotical-maps/maps').mkdir(parents=True)
            (root / 'diabotical-maps/index.json').write_text('[]')
            (root / 'diabotical-maps/maps/walk-0123456789ab.bin').write_bytes(bytes(16))
            (root / 'secret.txt').write_text('outside pack')
            client = app.test_client()
            with patch('app.local_data_dir', root):
                with client.get('/maps/diabotical/') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
                with client.get('/diabotical-map-data/index.json') as response:
                    self.assertEqual(response.json, [])
                    self.assertNotIn('immutable', response.headers.get('Cache-Control', ''))
                with client.get('/diabotical-map-data/maps/walk-0123456789ab.bin') as response:
                    self.assertIn('immutable', response.headers['Cache-Control'])
                with client.get('/diabotical-map-data/../secret.txt') as response:
                    self.assertEqual(response.status_code, 404)
                self.assertEqual(client.post('/import_diabotical_maps', json={'game': str(root)}, headers={'Origin': 'http://elsewhere.example'}).status_code, 403)
                self.assertEqual(client.post('/import_diabotical_maps', json={'game': ''}).status_code, 400)
                with client.post('/import_diabotical_maps', json={'game': str(root / 'absent')}) as response:
                    self.assertEqual(response.status_code, 422)
                    self.assertIn('Enter the Diabotical folder', response.json['error'])

    @unittest.skipUnless(shutil.which('node'), 'Node is not installed')
    def test_page_scripts(self):
        """Block meshing (static/diabotical-maps/blocks.js)."""
        test = Path(__file__).with_name('diabotical_maps.test.cjs')
        run = subprocess.run(['node', '--test', str(test)], capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stdout[-4000:] + run.stderr[-2000:])

    @unittest.skipUnless(os.environ.get('DIABOTICAL_GAME_BASE'), 'set DIABOTICAL_GAME_BASE to a Diabotical folder to import its maps')
    def test_install_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            report = import_maps(os.environ['DIABOTICAL_GAME_BASE'], Path(directory) / 'pack', extra=[])
            self.assertTrue(report['imported'])
            self.assertTrue(all('version 21' in reason for reason in report['failed'].values()), report['failed'])


if __name__ == '__main__':
    unittest.main()

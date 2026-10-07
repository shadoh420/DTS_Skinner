"""N64 map import (tools/import_n64_map.py) and serving boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import app
from tools.import_n64_map import import_maps, map_id


class N64MapTests(unittest.TestCase):
    def test_import_copies_map_folders_and_skips_what_is_imported(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory, 'Everything'), Path(directory, 'out')
            (source / 'Dam').mkdir(parents=True)
            (source / 'Dam/Dam.obj').write_text('mtllib Dam.mtl\nv 0 0 0 1 1 1 1\n')
            (source / 'Dam/Dam.mtl').write_text('newmtl m0\nmap_Kd a.png\n')
            (source / 'Dam/a.png').write_bytes(b'png')
            (source / 'Dam/Dam.blend').write_bytes(b'blend')
            (source / 'Notes').mkdir()  # No .obj files: not a map.
            result = import_maps(source, output)
            self.assertEqual(result, dict(imported=['Dam'], skipped=[], failed={}))
            self.assertEqual(sorted(path.name for path in (output / 'maps/dam').iterdir()), ['Dam.mtl', 'Dam.obj', 'a.png'])
            self.assertEqual(json.loads((output / 'index.json').read_text()), [dict(id='dam', name='Dam', game='GoldenEye 007', objs=['Dam.obj'], mtls=['Dam.mtl'])])
            self.assertEqual(import_maps(source, output)['skipped'], ['Dam'])
            self.assertEqual(import_maps(source / 'Dam', output, replace=True)['imported'], ['Dam'])  # One map's own folder.
            with self.assertRaisesRegex(ValueError, 'No map folders'):
                import_maps(source / 'Notes', output)

    def test_map_id(self):
        self.assertEqual(map_id('dataDyne Central'), 'datadyne_central')

    def test_routes_are_local_and_confined_to_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'n64-maps').mkdir()
            (root / 'n64-maps/index.json').write_text('[]')
            (root / 'secret.txt').write_text('outside pack')
            client = app.test_client()
            with patch('app.local_data_dir', root):
                with client.get('/maps/n64/') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
                with client.get('/n64-map-data/index.json') as response:
                    self.assertEqual(response.json, [])
                with client.get('/n64-map-data/../secret.txt') as response:
                    self.assertEqual(response.status_code, 404)
                self.assertEqual(client.post('/import_n64_maps', json={'folder': str(root)}, headers={'Origin': 'http://elsewhere.example'}).status_code, 403)
                self.assertEqual(client.post('/import_n64_maps', json={'folder': ''}).status_code, 400)
                with client.post('/import_n64_maps', json={'folder': str(root / 'absent')}) as response:
                    self.assertEqual(response.status_code, 422)


if __name__ == '__main__':
    unittest.main()

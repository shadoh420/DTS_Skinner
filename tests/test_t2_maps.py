"""Offline map import and serving boundaries; no game installation required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import app
from tools.import_t2_map import ARCHIVES, import_map


class MapTests(unittest.TestCase):
    def archives(self, root, extra=None):
        for name in ARCHIVES:
            with zipfile.ZipFile(root / name, 'w') as archive:
                if name == 'base.vl2':
                    archive.writestr('Scripts/Server.cs', b'// caf\xe9\r\r\n')
                    archive.writestr('Missions/Katabatic.mis', '// stock')
                    archive.writestr('Terrains/Katabatic.ter', b'terrain')
                    archive.writestr('Missions/Other.mis', '// out of scope')
                    archive.writestr('textures/test.png', b'first')
                    if extra:
                        archive.writestr(*extra)
                if name == 'lush.vl2':
                    archive.writestr('textures/test.png', b'last')

    def test_import_scope_encoding_precedence_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.archives(root)
            output = root / 'pack'
            self.assertEqual(import_map(root, output), 4)
            self.assertEqual((output / 'base/scripts/server.cs').read_bytes(), '// café\n'.encode())
            self.assertEqual((output / 'base/textures/test.png').read_bytes(), b'last')
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(list(manifest['missions']), ['Katabatic'])
            self.assertNotIn('missions/other.mis', manifest['resources'])
            provenance = json.loads((output / 'SOURCES.json').read_text())
            self.assertEqual([a['archive'] for a in provenance['archives']], list(ARCHIVES))
            with self.assertRaisesRegex(ValueError, 'never overwritten'):
                import_map(root, output)

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.archives(root, ('../outside.cs', b'unsafe'))
            with self.assertRaisesRegex(ValueError, 'Unsafe archive path'):
                import_map(root, root / 'pack')
            self.assertFalse((root / 'outside.cs').exists())
            self.assertFalse((root / 'pack/manifest.json').exists())

    def test_missing_archive_does_not_create_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'Missing stock game archive'):
                import_map(root, root / 'pack')
            self.assertFalse((root / 'pack').exists())

    def test_routes_are_local_and_confined_to_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 't2-maps').mkdir()
            (root / 't2-maps/manifest.json').write_text('{"missions":{}}')
            (root / 'secret.txt').write_text('outside pack')
            client = app.test_client()
            with patch('app.local_data_dir', root):
                with client.get('/maps/') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("connect-src 'self'", response.headers['Content-Security-Policy'])
                with client.get('/t2-map-data/manifest.json') as response:
                    self.assertEqual(response.json, {'missions': {}})
                with client.get('/t2-map-data/../secret.txt') as response:
                    self.assertEqual(response.status_code, 404)
                with client.get('/t2-map-data/not-present.dts') as response:
                    self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()

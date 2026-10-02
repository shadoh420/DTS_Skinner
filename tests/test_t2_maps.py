"""Offline map import and serving boundaries; no game installation required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import app
from tools.import_t2_map import ARCHIVES, OPTIONAL, import_maps, mission_info


class MapTests(unittest.TestCase):
    def archives(self, root, extra=None, optional=OPTIONAL):
        for name in ARCHIVES + optional:
            with zipfile.ZipFile(root / name, 'w') as archive:
                if name == 'base.vl2':
                    archive.writestr('Scripts/Server.cs', b'// caf\xe9\r\r\n')
                    archive.writestr('Missions/Katabatic.mis', '// MissionTypes = ctf\tHunters\r\nterrainFile = "Katabatic.ter";')
                    archive.writestr('Terrains/Katabatic.ter', b'terrain')
                    archive.writestr('Missions/Other.mis', '// DisplayName = Another Map\nterrainFile = "Absent.ter";')
                    archive.writestr('textures/test.png', b'first')
                    if extra:
                        archive.writestr(*extra)
                if name == 'lush.vl2':
                    archive.writestr('textures/test.png', b'last')
                if name == 'Classic_maps_v1.vl2':
                    archive.writestr('missions/Other.mis', '// MissionTypes = TR2\nterrainFile = "Katabatic.ter";')

    def test_import_takes_every_mission_with_encoding_precedence_and_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.archives(root)
            output = root / 'pack'
            report = import_maps(root, output)
            self.assertEqual((report['imported'], report['resources'], report['warnings']), (['Katabatic', 'Other'], 5, {}))
            self.assertEqual((output / 'base/scripts/server.cs').read_bytes(), '// café\n'.encode())
            self.assertEqual((output / 'base/textures/test.png').read_bytes(), b'last')
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['missions']['Katabatic'], {
                'resourcePath': 'missions/katabatic.mis', 'displayName': None, 'missionTypes': ['CTF', 'Hunters'], 'source': 'base.vl2'})
            # The later archive's copy of a mission wins, as for any other resource.
            self.assertEqual(manifest['missions']['Other'], {
                'resourcePath': 'missions/other.mis', 'displayName': None, 'missionTypes': ['TR2'], 'source': 'Classic_maps_v1.vl2'})
            provenance = json.loads((output / 'SOURCES.json').read_text())
            self.assertEqual([a['archive'] for a in provenance['archives']], list(ARCHIVES + OPTIONAL))
            # An existing pack is kept unless asked to rebuild; anything else is never replaced.
            (output / 'mounts.json').write_text('{}')
            self.assertEqual(import_maps(root, output)['skipped'], ['Katabatic', 'Other'])
            self.assertTrue((output / 'mounts.json').exists())
            self.assertEqual(import_maps(root, output, replace=True)['imported'], ['Katabatic', 'Other'])
            self.assertFalse((output / 'mounts.json').exists())
            self.assertEqual([p.name for p in root.iterdir() if p.is_dir()], ['pack'])
            (root / 'documents').mkdir()
            with self.assertRaisesRegex(ValueError, 'only a T2 map pack is ever replaced'):
                import_maps(root, root / 'documents', replace=True)

    def test_map_pack_archives_are_optional_and_a_missing_terrain_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'base').mkdir()
            self.archives(root / 'base', optional=())
            report = import_maps(root, root / 'pack')  # The GameData folder is accepted as well as its base folder.
            self.assertEqual(report['warnings'], {'Other': ['Terrain not found: Absent.ter']})
            self.assertEqual(json.loads((root / 'pack/manifest.json').read_text())['missions']['Other']['displayName'], 'Another Map')

    def test_mission_info_reads_only_comments_outside_sections(self):
        text = ('// DisplayName = Riverdance\n//--- MISSION BLURB BEGIN ---\n// MissionTypes = DM\n'
                '//--- MISSION BLURB END ---\n// missiontypes = ctf Custom\n')
        self.assertEqual(mission_info(text), ('Riverdance', ['CTF', 'Custom']))

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.archives(root, ('../outside.cs', b'unsafe'))
            with self.assertRaisesRegex(ValueError, 'Unsafe archive path'):
                import_maps(root, root / 'pack')
            self.assertFalse((root / 'outside.cs').exists())
            self.assertEqual([p.name for p in root.iterdir() if p.is_dir()], [])

    def test_missing_archive_does_not_create_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'Missing stock game archive'):
                import_maps(root, root / 'pack')
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
                elsewhere = {'Origin': 'http://elsewhere.example'}
                self.assertEqual(client.post('/import_t2_maps', json={'game': str(root)}, headers=elsewhere).status_code, 403)
                self.assertEqual(client.post('/import_t2_maps', json={'game': ''}).status_code, 400)
                with client.post('/import_t2_maps', json={'game': str(root / 'absent')}) as response:
                    self.assertEqual((response.status_code, response.json['error'][:21]), (422, 'Game folder not found'))
                # The viewer stores the mount transforms it reads from the pack's shapes, and nothing else.
                mounts = {'vehicle_pad': {'mount0': {'position': [0, 1.5, 0], 'rotation': [0, 0, 0, 1]}}}
                self.assertEqual(client.post('/t2_map_mounts', json=mounts, headers=elsewhere).status_code, 403)
                self.assertEqual(client.post('/t2_map_mounts', json={'../x': {'mount0': {'position': [0], 'rotation': 'q'}}}).status_code, 400)
                self.assertEqual(client.post('/t2_map_mounts', json=mounts).status_code, 200)
                with client.get('/t2-map-data/mounts.json') as response:
                    self.assertEqual(response.json, mounts)
            with patch('app.local_data_dir', root / 'empty'):
                self.assertEqual(client.post('/t2_map_mounts', json=mounts).status_code, 404)
                self.assertFalse((root / 'empty').exists())


if __name__ == '__main__':
    unittest.main()

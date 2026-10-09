"""Unreal maps (tools/import_unreal_map.py): the Level and Model of both layouts, drawn polygons, and the page's routes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app import app
from tools.import_unreal import Library, Package
from tools.import_unreal_map import build_map

INSTALL = Path('C:/Unreal')


@unittest.skipUnless((INSTALL / 'Maps' / 'Vortex2.unr').is_file(), 'needs the Unreal install at C:/Unreal')
class UnrealMapInstallTest(unittest.TestCase):
    def test_both_model_layouts(self):
        # Vortex2 is a 1998 file (version 61: the BSP in objects of its own), Abyss a Return to Na Pali one (68: inline).
        library = Library(INSTALL)
        engine = library.package('engine')
        default = engine, engine.export_by_path(('Engine', 'DefaultTexture'), 'Texture')
        for name, version, title in (('Vortex2', 61, 'Vortex Rikers'), ('UPak/Abyss', 68, "Gala's Peak")):
            level = Package(INSTALL / 'Maps' / f'{name}.unr')
            self.assertEqual(level.version, version)
            scene, geometry, images = build_map(library, level, default)  # Reads raise unless they end exactly.
            self.assertEqual(scene['title'], title)
            self.assertGreater(scene['polygons'], 1000)
            # Unreal's corners wind clockwise seen from the front; all but a few semisolid faces are reversed.
            self.assertGreater(scene['flipped'], scene['polygons'] - 10)
            self.assertEqual(len(geometry), scene['vertices'] * 20 + scene['indices'] * 4)
            indices = np.frombuffer(geometry, '<u4', offset=scene['vertices'] * 20)
            self.assertLess(int(indices.max()), scene['vertices'])
            self.assertEqual(sum(group['count'] for group in scene['groups']), scene['indices'])
            self.assertTrue(scene['viewpoints'])
            if name == 'Vortex2':  # Its start stands on the floor at -480: the eye 39 + 23 over it, as in the game.
                self.assertAlmostEqual(scene['viewpoints'][0]['origin'][1], (-480 + 62) / 52.5, places=3)
            self.assertEqual(scene['missing'], [])
            self.assertNotIn('missing.png', images)
            groups = scene['groups']
            # Translucent drops masked; auto-panning surfaces carry their rate.
            self.assertFalse([group for group in groups if group['flags'] & 0x6 == 0x6])
            self.assertTrue([group for group in groups if group.get('pan')])
            if name == 'Vortex2':  # Its grates' texture is bMasked though the surfaces are not flagged masked.
                self.assertTrue(all(group['flags'] & 0x2 for group in groups if 'mgratem' in group['texture']))
            else:  # Abyss shows its sky zone through its backdrops.
                self.assertEqual(len(scene['sky']['rotation']), 9)
                self.assertTrue([group for group in groups if group['flags'] & 0x80])


class UnrealMapRouteTest(unittest.TestCase):
    def test_page_data_and_import_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'unreal-maps' / 'unreal').mkdir(parents=True)
            (Path(folder) / 'unreal-maps' / 'unreal' / 'index.json').write_text('[]')
            with patch('app.local_data_dir', Path(folder)):
                client = app.test_client()
                self.assertEqual(client.get('/maps/unreal/').status_code, 200)
                self.assertEqual(client.get('/unreal-map-data/unreal/index.json').json, [])
                self.assertEqual(client.post('/import_unreal_maps', json={'path': ' '}).status_code, 400)
                self.assertEqual(client.post('/import_unreal_maps', json={'path': 'C:/Unreal'},
                                             headers={'Origin': 'http://elsewhere.test'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()

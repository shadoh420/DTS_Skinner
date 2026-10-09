"""Unreal maps (tools/import_unreal_map.py): the Level and Model of both layouts, drawn polygons, and the page's routes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app import app
from tools.import_unreal import Library, Package
from tools.import_unreal_map import build_map, light_color

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
            scene, geometry, images, atlas = build_map(library, level, default)  # Reads raise unless they end exactly.
            self.assertEqual(scene['title'], title)
            self.assertGreater(scene['polygons'], 1000)
            # Unreal's corners wind clockwise seen from the front; all but a few semisolid faces are reversed.
            self.assertGreater(scene['flipped'], scene['polygons'] - 10)
            self.assertEqual(len(geometry), scene['vertices'] * 32 + scene['indices'] * 4)
            indices = np.frombuffer(geometry, '<u4', offset=scene['vertices'] * 32)
            self.assertLess(int(indices.max()), scene['vertices'])
            # The lightmaps: one atlas, lit and shadowed (not one flat value), every corner inside it.
            self.assertEqual(list(atlas.size), scene['lightmap'])
            self.assertGreater(np.asarray(atlas).std(), 10)
            uv2 = np.frombuffer(geometry, '<f4', scene['vertices'] * 2, scene['vertices'] * 20)
            self.assertTrue(0 <= uv2.min() and uv2.max() <= 1)
            # Placed actors: meshes and movers, none failing; meshes lit at their vertices (not all white).
            actors = scene['actors']
            self.assertEqual(actors['failed'], [])
            self.assertGreater(actors['meshes'], 20)
            self.assertGreater(actors['movers'], 10)
            colors = np.frombuffer(geometry, np.uint8, scene['vertices'] * 4, scene['vertices'] * 28).reshape(-1, 4)
            self.assertGreater((colors[:, :3] < 255).any(1).sum(), 1000)
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

    def test_class_defaults_behind_bytecode(self):
        # Placed lights and zones leave out what equals their class's defaults; scripted classes keep theirs after
        # their bytecode, and 227 moved UnrealI's lights into UnrealShare.
        library = Library(INSTALL)
        light = library.class_properties(('Engine', 'Light'))
        self.assertEqual((light['LightType'], light['LightBrightness'], light['LightSaturation'], light['LightRadius']),
                         (1, 64, 255, 64))
        zone = library.class_properties(('Engine', 'ZoneInfo'))
        self.assertEqual((zone['TexUPanSpeed'], zone['AmbientSaturation']), (1.0, 255))
        self.assertEqual(library.class_properties(('Engine', 'Actor'))['DrawType'], 1)
        lantern = library.class_properties(('UnrealI', 'Lantern'))
        self.assertEqual((lantern['LightBrightness'], lantern['LightHue'], lantern['LightRadius']), (203, 35, 32))
        self.assertIs(library.class_properties(('Engine', 'TriggerLight'))['bInitiallyOn'], False)

    def test_masked_surfaces_cut_out_palette_index_0(self):
        # NyLeve's sky panorama is drawn masked though its texture is not bMasked (checked in 227, 2026-10-09): the
        # map pack's copy is clear at index 0, the model browser's is not.
        library = Library(INSTALL)
        package = library.package('skybox')
        ref = package.export_by_path(('SkyBox', 'lnd_1'), 'Texture')
        self.assertEqual(library.texture(package, ref)[1].getextrema()[3], (255, 255))
        self.assertEqual(library.texture(package, ref, masked=True)[1].getextrema()[3][0], 0)


class LightColorTest(unittest.TestCase):
    def test_hue_saturation_brightness(self):
        self.assertTrue(np.allclose(light_color(0, 255, 255), .5))  # Saturation 255: white; a full light lights 1.
        self.assertTrue(np.allclose(light_color(0, 255, 51), .1))  # Linear in brightness (checked in 227).
        self.assertTrue(np.array_equal(light_color(40, 0, 0), np.zeros(3)))
        red, green, blue = light_color(0, 0, 255)  # Hue 0, full colour: red only.
        self.assertGreater(red, .4)
        self.assertEqual((green, blue), (0, 0))
        self.assertEqual(int(np.argmax(light_color(153, 72, 186))), 2)  # NyLeve's start lights: blue.


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

"""Unreal maps (tools/import_unreal_map.py): the Level and Model of both layouts, drawn polygons, and the page's routes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app import app
from tools.import_unreal import Library, Package
from tools.import_unreal_map import actors_of, build_map, light_color, vector, viewer

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

    def test_mover_opened_at_spawn(self):
        # SpireVillage's start stands in a force field (Mover0, TriggerToggle) that a Trigger under the start lowers
        # 272 units as the player lands (checked in 227, 2026-10-09): drawn open, its top under the start's floor.
        library = Library(INSTALL)
        engine = library.package('engine')
        scene, geometry, _, _ = build_map(library, Package(INSTALL / 'Maps' / 'SpireVillage.unr'),
                                          (engine, engine.export_by_path(('Engine', 'DefaultTexture'), 'Texture')))
        positions = np.frombuffer(geometry, '<f4', scene['vertices'] * 3).reshape(-1, 3)
        indices = np.frombuffer(geometry, '<u4', offset=scene['vertices'] * 32)
        field = [g for g in scene['groups'] if g['texture'] == 'alfafx.lion4.png']
        self.assertEqual(len(field), 1)
        top = positions[indices[field[0]['start']:field[0]['start'] + field[0]['count']], 1].max()
        self.assertLess(top, scene['viewpoints'][0]['origin'][1] - (39 + 23) / 52.5)

    def test_texture_saved_without_its_group(self):
        # UGCredits asks for UGoldCredits.Logos.Legend; the package holds Legend without a group (the only such case).
        library, level = Library(INSTALL), Package(INSTALL / 'Maps' / 'UGCredits.unr')
        ref = next(-(i + 1) for i, found in enumerate(level.imports) if found['name'] == 'Legend')
        self.assertEqual(library.texture(level, ref)[1].size, (256, 256))

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


UT_INSTALL = Path('C:/UnrealTournament')


@unittest.skipUnless((UT_INSTALL / 'Maps' / 'UT-Logo-Map.unr').is_file(), 'needs the UT install at C:/UnrealTournament')
class UTMapInstallTest(unittest.TestCase):
    def test_scripted_texture_and_menu_camera(self):
        library = Library(UT_INSTALL)
        # A ScriptedTexture's pixels are drawn by script as the game runs: its SourceTexture stands in.
        package = library.package('indus7')
        ref = next(n for n, export in enumerate(package.exports, 1) if export['name'] == 'Monitor1')
        self.assertEqual(library.texture(package, ref)[1].size, (128, 128))
        # A map whose game is an intro (UT's menu backdrop) opens on its SpectatorCam, not its PlayerStart.
        engine = library.package('engine')
        level = Package(UT_INSTALL / 'Maps' / 'UT-Logo-Map.unr')
        scene = build_map(library, level, (engine, engine.export_by_path(('Engine', 'DefaultTexture'), 'Texture')))[0]
        layout = level.level(level.find({'Level'})[0])
        camera = next(iter(actors_of(level, layout['actors'], {'SpectatorCam'}).values()))
        self.assertTrue(np.allclose(scene['viewpoints'][0]['origin'], viewer([vector(camera, 'Location')])[0], atol=1e-3))
        self.assertEqual(scene['actors']['failed'], [])


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
                self.assertEqual(client.post('/import_unreal_maps', json={'path': 'C:/UT', 'game': 'q3'}).status_code, 400)
                with patch('app.import_unreal_maps', return_value=dict(imported=[], skipped=[], failed={})) as imported:
                    self.assertEqual(client.post('/import_unreal_maps', json={'path': 'C:/UT', 'game': 'ut'}).status_code, 200)
                self.assertEqual(imported.call_args.args, ('C:/UT', Path(folder) / 'unreal-maps' / 'ut', False, 'ut'))
                self.assertEqual(client.post('/import_unreal_maps', json={'path': 'C:/Unreal'},
                                             headers={'Origin': 'http://elsewhere.test'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()

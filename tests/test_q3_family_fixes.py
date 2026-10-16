"""Q3-family viewer fixes of 2026-10-16: light grid kept, fog volumes and map fog, sprites, tcGen vector, JA racks."""
from pathlib import Path
import struct
import tempfile
import unittest
from types import SimpleNamespace

from tools import import_ja_map, import_q3_map as qmap, import_rtcw_map
from tests.test_q3_maps import image, pk3

SCRIPT = b'''textures/test/vector
{
 { map textures/test/wall.tga
   tcGen vector ( 0.01 0 0 ) ( 0 0.02 0 )
 }
}
textures/test/sprite
{
 deformVertexes autosprite
 { map textures/test/wall.tga
   blendFunc add
 }
}
textures/test/beam
{
 deformVertexes autosprite2
 { map textures/test/wall.tga
   blendFunc add
 }
}
textures/test/fog
{
 surfaceparm fog
 surfaceparm trans
 fogparms ( 0.5 0.25 0 ) 300
}
textures/test/sky
{
 skyparms - 512 env/inner
 { map textures/test/wall.tga
 }
}
'''


class Q3FamilyFixTests(unittest.TestCase):
    def test_shader_stages_keep_vectors_sprites_and_fog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseq3').mkdir()
            pk3(root / 'baseq3/pak0.pk3', {'scripts/test.shader': SCRIPT, 'textures/test/wall.tga': image()})
            game = qmap.Game([root / 'baseq3'])
            try:
                textures = qmap.Textures(root / 'out')
                vector, _, limits = qmap.describe(game, 'textures/test/vector', textures)
                self.assertEqual((vector['stages'][0]['tcGen'], vector['stages'][0]['tcGenVectors'], limits), ('vector', [.01, 0, 0, 0, .02, 0], []))
                self.assertEqual(qmap.describe(game, 'textures/test/sprite', textures)[0]['deforms'], [['autosprite']])
                self.assertEqual(qmap.describe(game, 'textures/test/beam', textures)[2], [])
                # A fog shader has no stages: its fog pass alone draws it, at SS_FOG.
                fog, _, limits = qmap.describe(game, 'textures/test/fog', textures)
                self.assertEqual((fog['stages'], fog['sort'], fog['fog'], limits), ([], 7, True, []))
                # Q3 parses an inner sky box and never draws it, so it is no longer listed as missing.
                self.assertEqual(qmap.describe(game, 'textures/test/sky', textures)[2], [])
            finally:
                game.close()

    def test_fog_volumes_read_bounds_visible_plane_and_global_fog(self):
        planes = [(-1, 0, 0, 10), (1, 0, 0, 20), (0, -1, 0, 30), (0, 1, 0, 40), (0, 0, -1, 50), (0, 0, 1, 60)]
        lumps = [b''] * 17
        lumps[2] = b''.join(struct.pack('<4f', *plane) for plane in planes)
        lumps[8] = struct.pack('<3i', 0, 6, 0)
        lumps[9] = b''.join(struct.pack('<2i', n, 0) for n in range(6))
        name = lambda text: text.encode().ljust(64, b'\0')
        lumps[12] = struct.pack('<64sii', name('textures/test/fog'), 0, 5) + struct.pack('<64sii', name('textures/test/fog'), -1, -1) + \
            struct.pack('<64sii', name('textures/test/nofog'), 0, 5)
        shaders = {'textures/test/fog': ([['fogparms', '(', '0.5', '0.25', '0', ')', '300']], [])}
        box, world, absent = qmap.fog_volumes(lumps, shaders)
        self.assertEqual(box, dict(shader='textures/test/fog', color=[.5, .25, 0], depth=300, bounds=[-10, -30, -50, 20, 40, 60], plane=[0, 0, 1, 60]))
        self.assertTrue(world['global'])
        self.assertNotIn('bounds', world)
        self.assertIsNone(absent)  # No fogparms: nothing to draw.

    def test_light_grid_is_kept_in_the_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'map.bsp'
            qmap.write_bsp(path, [bytes([n]) * 8 for n in range(17)])
            lumps = qmap.read_bsp(path.read_bytes())
            self.assertEqual((lumps[15], lumps[12]), (bytes([15]) * 8, b''))

    def test_rtcw_map_fog_comes_from_the_sky_shader(self):
        game = SimpleNamespace(shaders={'textures/skies/a': ([['fogvars', '(', '.1', '.2', '.3', ')', '2000']], []),
                                        'textures/skies/b': ([['fogvars', '(', '.1', '.1', '.1', ')', '.0005']], [])})
        linear, far = import_rtcw_map.map_fog(game, [None, {'name': 'textures/skies/a', 'sky': {}}])
        self.assertEqual((linear, far), (dict(color=[.1, .2, .3], linear=True, end=2000), [2000]))
        density, far = import_rtcw_map.map_fog(game, [{'name': 'textures/skies/b', 'sky': {}}])
        self.assertEqual((density, far), (dict(color=[.1, .1, .1], linear=False, density=.0005), []))
        self.assertEqual(import_rtcw_map.map_fog(game, [{'name': 'textures/skies/a'}]), (None, []))  # Not a sky.

    def test_ja_racks_use_the_models_the_game_sets(self):
        for classname, model in (('misc_model_gun_rack', 'weaponsrack'), ('misc_model_ammo_rack', 'weaponsrung')):
            entity = import_ja_map.prepare_entity({'classname': classname, 'model': 'models/map_objects/absent.md3', 'origin': '0 0 0'}, 'sp')
            self.assertEqual(entity['model'], f'models/map_objects/kejim/{model}.md3')


if __name__ == '__main__':
    unittest.main()

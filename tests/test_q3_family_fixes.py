"""Q3-family viewer fixes of 2026-10-16: light grid kept, fog volumes and map fog, sprites, tcGen vector, JA racks."""
import math
from pathlib import Path
import struct
import tempfile
import unittest
from types import SimpleNamespace

from tools import import_ja_map, import_q3_map as qmap, import_rtcw_map
from tests.test_q3 import md3
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


class Q3FamilyPartBTests(unittest.TestCase):
    """Movers, game types, barrels, coronas, the sun and weather (2026-10-16, part B)."""

    def test_movers_follow_g_mover(self):
        motion = lambda thing, rules='q3', mins_z=0, things=(): qmap.mover_motion(thing, list(things) or [thing], mins_z, rules)
        self.assertEqual(motion({'classname': 'func_bobbing', 'spawnflags': '1', 'height': '20', 'speed': '2', 'phase': '.25'}),
                         dict(type='sine', delta=[20, 0, 0], period=2, phase=.25))
        self.assertEqual(motion({'classname': 'func_bobbing'})['delta'], [0, 0, 32])
        self.assertIsNone(motion({'classname': 'func_bobbing', 'spawnflags': '4'}, 'ravensp'))  # START_OFF
        self.assertEqual(motion({'classname': 'func_rotating', 'spawnflags': '4', 'speed': '45'})['rate'], [0, 0, 45])
        self.assertEqual(motion({'classname': 'func_rotating'})['rate'], [0, 100, 0])
        self.assertIsNone(motion({'classname': 'func_rotating'}, 'wolf'))  # RTCW and ET turn only with START_ON.
        self.assertEqual(motion({'classname': 'func_rotating', 'spawnflags': '9'}, 'wolf')['rate'], [100, 0, 0])
        swing = motion({'classname': 'func_pendulum'}, mins_z=-96)
        self.assertAlmostEqual(swing['period'], 2 * math.pi / math.sqrt(800 / 288))
        self.assertEqual(motion({'classname': 'func_pendulum'}, mins_z=-2)['period'], motion({'classname': 'func_pendulum'}, mins_z=-8)['period'])
        corners = [{'classname': 'path_corner', 'targetname': 'a', 'target': 'b', 'origin': '0 0 0', 'wait': '2'},
                   {'classname': 'path_corner', 'targetname': 'b', 'target': 'a', 'origin': '300 0 0', 'speed': '50'}]
        train = {'classname': 'func_train', 'target': 'a', 'speed': '150'}
        self.assertEqual(motion(train, things=[train, *corners]), dict(type='path', points=[[0, 0, 0], [300, 0, 0]],
                                                                       legs=[dict(wait=2, duration=2), dict(wait=0, duration=6)]))
        self.assertIsNone(motion(train, things=[train, corners[0]]))  # A corner leads nowhere: the train never sets off.
        self.assertIsNone(motion({**train, 'targetname': 't'}, 'ravensp', things=[train, *corners]))  # Waits to be used.

    def test_movers_keep_the_pose_their_faces_were_written_in(self):
        models = struct.pack('<6f4i', 0, 0, -64, 8, 8, 0, 0, 0, 0, 0) * 2
        things = [{'classname': 'func_pendulum', 'model': '*1', 'origin': '10 20 30'}, {'classname': 'func_static', 'model': '*1'}]
        [mover] = qmap.movers(things, models)
        self.assertEqual((mover['model'], mover['base'], mover['origin'], mover['angles'], mover['type']),
                         (1, dict(origin=[10, 20, 30], angles=[0, 0, 0]), [10, 20, 30], [0, 0, 0], 'swing'))
        self.assertEqual(qmap.movers(things, models, placed=lambda index, thing: (None, None)), [])  # Hidden at spawn.
        # SP_func_pendulum starts its roll from the spawn angles (q3dm15: "angle" 90, swinging across the hall).
        [turned] = qmap.movers([{**things[0], 'angle': '90'}], models)
        self.assertEqual((turned['base']['angles'], turned['angles']), ([0, 0, 0], [0, 90, 0]))
        [rotating] = qmap.movers([{'classname': 'func_rotating', 'model': '*1', 'angle': '90'}], models)
        self.assertEqual(rotating['angles'], [0, 0, 0])  # Other movers never take them.

    def test_pickups_follow_the_game_type(self):
        self.assertFalse(qmap.spawns({'notfree': '1'}, 0))
        self.assertTrue(qmap.spawns({'notfree': '1'}, 4))
        self.assertFalse(qmap.spawns({'notteam': '1'}, 4))
        self.assertFalse(qmap.spawns({'notsingle': '1'}, 2))
        self.assertFalse(qmap.spawns({'notq3a': '1'}, 0))
        self.assertTrue(qmap.spawns({'notq3a': '1'}, 0, team_arena=True))
        self.assertFalse(qmap.spawns({'gametype': 'ctf oneflag'}, 0))
        self.assertTrue(qmap.spawns({'gametype': 'ffa tournament'}, 1))

    def test_team_arena_barrels_sit_at_tag_barrel(self):
        game = SimpleNamespace(team_arena=False, files={})
        self.assertEqual(import_q3_barrel(game), {})
        game = SimpleNamespace(team_arena=True, files={'models/weapons/w.md3': ('pak', lambda: md3('tag_barrel')), 'models/weapons/w_barrel.md3': ('pak', lambda: b'')},
                               models={'models/weapons/w_barrel.md3': (dict(file='b.json', shaders=[], middle=[0, 0, 0]), [])})
        found = {}
        self.assertEqual(qmap.barrel(game, 'weapon', 'models/weapons/w.md3', None, None, found), dict(barrel=dict(model='models/weapons/w_barrel.md3', origin=[0, 0, 0])))
        self.assertIn('models/weapons/w_barrel.md3', found)

    def test_coronas_follow_sp_corona(self):
        rules = SimpleNamespace(filtered=lambda entity, section: '')
        found = import_rtcw_map.coronas([{'classname': 'corona', 'origin': '1 2 3', 'scale': '2', '_color': '1 0 0', 'color': '0 1 0'},
                                         {'classname': 'corona', 'origin': '0 0 0', 'spawnflags': '1'},
                                         {'classname': 'corona', 'origin': '0 0 0', '_color': '0 0 0'}], 'sp', rules)
        self.assertEqual(found, [dict(origin=[1, 2, 3], colour=[0, 1, 0], scale=2), dict(origin=[0, 0, 0], colour=[1, 1, 1], scale=1)])

    def test_sun_comes_from_the_sky_shaders_sunshader_and_q3map_sun(self):
        game = SimpleNamespace(shaders={'textures/skies/s': ([['sunshader', 'gfx/misc/moon'], ['q3map_sun', '1', '1', '1', '100', '90', '0']], [])})
        rules = SimpleNamespace(describe=lambda game, name, textures: ({'name': name}, ['missing'], []))
        sun, missing = import_rtcw_map.sun(game, [{'name': 'textures/skies/s'}], None, rules)
        self.assertEqual((sun['shader'], missing), ({'name': 'sun'}, ['missing']))  # RTCW always draws 'sun'.
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(sun['direction'], [0, 1, 0])))
        rules.NAMED_SUN = True
        self.assertEqual(import_rtcw_map.sun(game, [{'name': 'textures/skies/s'}], None, rules)[0]['shader'], {'name': 'gfx/misc/moon'})
        self.assertEqual(import_rtcw_map.sun(game, [{'name': 'textures/other'}], None, rules), (None, []))

    def test_weather_reads_et_raven_and_rtcw_keys(self):
        found = import_rtcw_map.weather([
            {'classname': 'worldspawn', 'atmosphere': 'T=SNOW,B=5 10,BV=20 30,D=2400'},
            {'classname': 'fx_rain'}, {'classname': 'fx_rain', 'spawnflags': '4'}, {'classname': 'fx_snow'},
            {'classname': 'misc_snow64', 'origin': '100 0 50', 'health': '10'}])
        self.assertEqual([(w['type'], w['count']) for w in found], [('snow', 2400), ('rain', 500), ('rain', 1000), ('snow', 1000), ('snow', 10)])
        self.assertEqual((found[0]['wind'], found[0]['speed']), ([20, 30], 80))
        self.assertEqual(found[4]['column'], dict(min=[36, -64], max=[164, 64], top=50))
        self.assertEqual(import_rtcw_map.weather([{'classname': 'worldspawn', 'atmosphere': 'T=HAIL'}]), [])

    def test_picmip_keeps_the_colour_of_transparent_texels(self):
        from PIL import Image
        image = Image.new('RGBA', (4, 4), (200, 100, 50, 0))
        profile = dict(cvars=dict(r_roundimagesdown=0), picmip=1, hardwareGamma=True, gamma=1, intensity=1)
        self.assertEqual(import_rtcw_map.upload_image(image, profile, mipmap=False).getpixel((0, 0)), (200, 100, 50, 0))


def import_q3_barrel(game):
    return qmap.barrel(game, 'weapon', 'models/weapons/w.md3', None, None, {})


if __name__ == '__main__':
    unittest.main()

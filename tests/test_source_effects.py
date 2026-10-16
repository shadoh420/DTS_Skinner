"""Source map effects (tools/source_effects.py): ropes, decal and overlay clipping, sprite entities, turning brushes."""
import math
import unittest

import numpy as np

from tools import source_effects as fx


class SourceEffectsTest(unittest.TestCase):
    def test_rope_hangs_its_slack_and_meshes(self):
        ents = [dict(classname='move_rope', targetname='a', nextkey='b', origin='0 0 100', slack='50', width='4'),
                dict(classname='keyframe_rope', targetname='b', origin='200 0 100'),
                dict(classname='keyframe_rope', targetname='c', nextkey='missing', origin='0 0 0')]
        (material, points, width, scale), = fx.ropes(ents)
        self.assertEqual((material, width, scale), ('cable/cable', 4, 1))
        self.assertTrue(np.allclose(points[[0, -1]], [[0, 0, 100], [200, 0, 100]]))
        self.assertAlmostEqual(100 - points[len(points) // 2][2], math.sqrt(3 * 200 * 50 / 8))  # parabola sag
        positions, uvs, triangles = fx.rope_mesh(points, width, scale)
        self.assertEqual(len(positions), 4 * len(points))  # two ribbons, two edges each
        self.assertLess(triangles.max(), len(positions))
        self.assertTrue(np.allclose(np.linalg.norm(positions[0] - positions[1]), 4))
        # A vertical rope hangs straight (la_ventruetower_1b): no sag folding it back, finite mesh.
        ents = [dict(classname='move_rope', targetname='a', nextkey='b', origin='0 0 0'), dict(classname='keyframe_rope', targetname='b', origin='0 0 150')]
        (_, points, width, scale), = fx.ropes(ents)
        self.assertTrue(np.allclose(points[:, :2], 0) and (np.diff(points[:, 2]) > 0).all())
        self.assertTrue(np.isfinite(fx.rope_mesh(points, width, scale)[0]).all())

    def test_decal_on_a_wall_not_on_the_far_side_of_a_thin_wall(self):
        # A wall facing +x at x = 0, its back 6 units behind (ch_zhaos_1's sign wall), a plane 2 units behind (within
        # DECAL_DISTANCE) and a far wall; a 32 x 16 decal 1 unit off the wall.
        wall = np.array([[0, -64, -64], [0, 64, -64], [0, 64, 64], [0, -64, 64]], float)
        back = wall[::-1] - [6, 0, 0]
        near = wall[::-1] - [2, 0, 0]
        far = wall + [100, 0, 0]
        pieces = fx.decal_pieces([(1, wall, np.array([1., 0, 0])), (2, back, np.array([-1., 0, 0])),
                                  (3, far, np.array([1., 0, 0])), (4, near, np.array([-1., 0, 0]))], np.array([1., 8, 0]), 32, 16)
        self.assertEqual([face for face, _, _ in pieces], [1, 4])
        _, piece, st = pieces[0]
        self.assertTrue(np.allclose(np.ptp(piece, axis=0), [0, 32, 16]))
        self.assertTrue(np.allclose(st.min(0), 0) and np.allclose(st.max(0), 1))
        # On walls t runs down (r_decal.cpp): the top edge is t = 0.
        self.assertAlmostEqual(st[np.argmax(piece[:, 2]), 1], 0)

    def test_overlay_projected_and_cut_to_its_face(self):
        floor = np.array([[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]], float)
        overlay = dict(normal=(0, 0, 1), origin=(50, 50, 8), u=(0, 1), v=(0, 1), faces=[7],
                       uv_points=np.array([[-80, -20, 1], [-80, 20, 0], [80, 20, 0], [80, -20, 0]], float))
        (face, piece, st), = fx.overlay_pieces(overlay, {7: (floor, np.array([0., 0, 1]))})
        self.assertEqual(face, 7)
        self.assertTrue(np.allclose(piece[:, 2], 0))  # on the floor, not at the overlay's height
        self.assertTrue(np.allclose(piece[:, 0].min(), 0) and np.allclose(piece[:, 0].max(), 100))  # cut to the face
        self.assertTrue(np.allclose(piece[:, 1].min(), 30) and np.allclose(piece[:, 1].max(), 70))
        self.assertTrue(np.allclose(st[np.argmin(piece[:, 0] + piece[:, 1])], [30 / 160, 0]))

    def test_sprites_and_turning_brushes(self):
        ents = [dict(classname='env_sprite', model='sprites/glow01.vmt', rendermode='9', scale='.5', rendercolor='255 128 0'),
                dict(classname='env_sprite', model='sprites/glow01.vmt', targetname='off'),
                dict(classname='env_lightglow', horizontalglowsize='20', verticalglowsize='10'),
                dict(classname='point_spotlight', spawnflags='0')]
        found = fx.sprites(ents)
        self.assertEqual(len(found), 2)
        material, _, width, scale, rgba, blend, glow, _ = found[0]
        self.assertEqual((material, width, scale, blend, glow), ('sprites/glow01', None, .5, 'add', True))
        self.assertTrue(np.allclose(rgba, [1, 128 / 255, 0, 1]))
        self.assertEqual(found[1][2:4], (40, .5))
        # Glow colour x renderamt, and x renderamt again with renderfx 14 (StandardGlowBlend); additive ones unscaled.
        glows = fx.sprites([dict(classname='env_sprite', model='sprites/glowa.vmt', rendermode=mode, renderfx=fxv, renderamt='51')
                            for mode, fxv in (('3', '14'), ('3', '0'), ('5', '14'))])
        self.assertTrue(np.allclose([g[4][0] for g in glows], [.04, .2, 1]))
        self.assertTrue(np.allclose([g[4][3] for g in glows], .2))
        yaw = fx.rotating(dict(classname='func_rotating', spawnflags='1', maxspeed='90'))
        self.assertTrue(np.allclose(yaw, [0, 0, 1, math.pi / 2]))
        self.assertTrue(np.allclose(fx.rotating(dict(classname='func_rotating', spawnflags='3', maxspeed='90')), [0, 0, -1, math.pi / 2]))
        self.assertIsNone(fx.rotating(dict(classname='func_rotating', spawnflags='0')))

    def test_particles(self):
        ents = [dict(classname='func_dustmotes', model='*1', spawnrate='10', lifetimemin='3', lifetimemax='5', sizemin='7', sizemax='12',
                     alpha='51', color='255 0 0', distmax='512', spritename='particle/motes'),
                dict(classname='func_dustmotes', model='*1', startdisabled='1'),
                dict(classname='func_dustmotes', model='*1', spawnrate='4', frozen='1'),
                dict(classname='env_steam', origin='1 2 3', angles='-90 0 0', initialstate='1', speed='30', jetlength='120',
                     startsize='10', endsize='12', rate='20', renderamt='100', rendercolor='255 255 255'),
                dict(classname='env_steam', initialstate='0'), dict(classname='env_steam', initialstate='1', type='1')]
        found = fx.particles(ents, lambda e: (np.array([0, 0, 0]), np.array([10, 20, 30])), lambda p: np.array([.5, .5, .5]))
        self.assertEqual([f[0] for f in found], ['particle/motes', 'particle/sparkles', 'particle/particle_smokegrenade'])
        (_, dust, de), (_, frozen, fe), (_, steam, se) = found
        self.assertEqual(de['origin'], [5, 10, 15])
        self.assertEqual(dust['origin'], [[-5, -10, -15], [5, 10, 15]])
        self.assertEqual(dust['size']['start'], [7e-4, 12e-4], 'a share of the depth (DUSTFLAGS_SCALEMOTES)')
        self.assertEqual(dust['alpha']['curve'], dict(kind='sine', parm=2))
        self.assertAlmostEqual(dust['alpha']['end'][0], .2)
        self.assertEqual((dust['fade'], de['period']), (512, .1))
        self.assertEqual((frozen['count'], frozen['life'][0], frozen['alpha']), ([4, 4], 1e6, dict(start=[1, 1])))
        self.assertEqual(steam['life'], [4, 4], 'JetLength / Speed')
        self.assertEqual(steam['size']['end'], [18, 18], 'grows by (end - start) a second, FLerp by seconds')
        self.assertTrue(np.allclose(se['forward'], [0, 0, 1]), 'pitch -90 points up')
        self.assertEqual(steam['velocity'], [[30, -15, -15], [30, 15, 15]])
        self.assertEqual(steam['rgb']['start'][0], [.5, .5, .5], 'rendercolor x the world light')
        material = lambda name: dict(texture='missing.png' if name == 'particle/motes' else name + '.png', settings={})
        data = fx.particle_data(found, material, 'missing.png', .5)
        self.assertEqual(sorted(data['batches']), ['particle/particle_smokegrenade', 'particle/sparkles'])
        self.assertEqual([e['template'] for e in data['emitters']], [0, 1])
        self.assertEqual(data['toWorld'], [0, 0, .5, .5, 0, 0, 0, .5, 0], 'page = map [y, z, x] x scale')


if __name__ == '__main__':
    unittest.main()

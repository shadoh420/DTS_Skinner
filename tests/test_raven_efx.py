import math
from pathlib import Path
import shutil
import subprocess
import unittest
from types import SimpleNamespace

from tools import raven_efx as efx, import_jk2_map as jk

FIRE = '''// A comment.
Particle
{
	spawnFlags			evenDistribution rgbComponentInterpolation
	count				2 3
	life				600 900
	cullrange			350
	origin				0 -8 -8 0 8 8
	velocity			10 0 0
	gravity				20 60
	rgb
	{
		start			1 0.5 0.5 1 0.25 0.25
	}
	alpha
	{
		end				0
		parm			50
		flags			linear nonlinear
	}
	shaders
	[
		gfx/effects/fire2
		gfx/effects/fire3.tga
	]
}
/* Block comment
   spanning lines. */
Light
{
	size { start 10 }
}
Line { }
'''


class RavenEfxTest(unittest.TestCase):
    def test_parse_ranges_groups_and_defaults(self):
        result = efx.parse(FIRE)
        self.assertEqual(result['skipped'], {'light': 1, 'line': 1})
        [p] = result['primitives']
        self.assertEqual(p['count'], [2, 3])
        self.assertEqual(p['life'], [600, 900])
        self.assertEqual(p['delay'], [0, 0], 'delay defaults to 0')
        self.assertEqual(p['origin'], [[0, -8, -8], [0, 8, 8]])
        self.assertEqual(p['velocity'], [[10, 0, 0], [10, 0, 0]], 'three values: min = max')
        self.assertEqual(p['cullRange'], 350)
        self.assertEqual(p['spawnFlags'], ['evendistribution', 'rgbcomponentinterpolation'])
        self.assertEqual(p['rgb']['start'], [[1, .5, .5], [1, .25, .25]])
        self.assertEqual(p['alpha'], dict(start=[1, 1], end=[0, 0], parm=50, flags=['linear', 'nonlinear']))
        self.assertEqual(p['size'], dict(start=[1, 1], end=[1, 1], parm=0, flags=[]), 'CPrimitiveTemplate defaults')
        self.assertEqual(p['shaders'], ['gfx/effects/fire2', 'gfx/effects/fire3.tga'])

    def test_effect_path(self):
        self.assertEqual(efx.effect_path('env/small_fire'), 'effects/env/small_fire.efx')
        self.assertEqual(efx.effect_path('Chunks\\DustFall.efx'), 'effects/chunks/dustfall.efx')
        self.assertEqual(efx.effect_path('effects/env/fire.efx'), 'effects/env/fire.efx')

    def test_curve_mask(self):
        self.assertEqual(jk.efx_curve(dict(flags=['linear', 'clamp'], parm=50)), dict(linear=True, kind='clamp', parm=50, random=False))
        self.assertIsNone(jk.efx_curve(dict(flags=['nonlinear', 'wave'], parm=1))['kind'], 'two parm kinds match neither mask')
        template = jk.efx_template(efx.parse(FIRE)['primitives'][0])
        self.assertEqual(template['life'], [.6, .9])
        self.assertTrue(template['even'] and template['rgbTogether'] and template['alphaFadesRgb'])

    def test_runner_axes(self):
        up, right = jk.runner_axes({'origin': '0 0 0'}, [], 'sp')
        self.assertEqual([round(v, 6) for v in up], [0, 0, 1], 'no angle: up')
        self.assertAlmostEqual(sum(a * b for a, b in zip(up, right)), 0)
        forward, _ = jk.runner_axes({'origin': '0 0 0', 'angle': '90'}, [], 'sp')
        self.assertEqual([round(v, 6) + 0 for v in forward], [0, 1, 0], 'SP angle is a yaw')
        forward, _ = jk.runner_axes({'origin': '0 0 0', 'angle': '0'}, [], 'sp')
        self.assertEqual([round(v, 6) + 0 for v in forward], [1, 0, 0], 'SP angle 0 is present: forward, not up')
        forward, _ = jk.runner_axes({'origin': '0 0 0', 'angle': '0'}, [], 'mp')
        self.assertEqual([round(v, 6) for v in forward], [0, 0, 1], 'MP all-zero angles: up')
        forward, _ = jk.runner_axes({'origin': '0 0 0', 'target': 't', 'angle': '90'}, [{'targetname': 't', 'origin': '0 0 -5'}], 'sp')
        self.assertEqual([round(v, 6) + 0 for v in forward], [0, 0, -1], 'the target wins')

    def test_particles_from_runners(self):
        game = SimpleNamespace(assets={'effects/env/fire.efx': {}}, read=lambda path: FIRE.encode(), shaders={}, effects={}, section='sp')
        textures = SimpleNamespace(resolve=lambda game, name: name.replace('/', '_') + '.png')
        things = [{'classname': 'worldspawn'}, {'classname': 'fx_runner', 'origin': '1 2 3', 'fxFile': 'env/fire', 'delay': '300', 'random': '100'},
                  {'classname': 'fx_runner', 'origin': '0 0 0', 'fxfile': 'env/fire', 'spawnflags': '1'},
                  {'classname': 'fx_runner', 'origin': '0 0 0', 'fxFile': 'env/missing'}]
        audit = [dict(entity=i, classname='fx_runner', status='omitted', reason='world/control/effect entity; no standalone visual') for i in (1, 2, 3)]
        data, missing, limits = jk.particles(game, things, audit, textures)
        self.assertEqual(missing, ['effects/env/missing.efx'])
        self.assertEqual([r['status'] for r in audit], ['drawn', 'omitted', 'omitted'])
        self.assertIn('STARTOFF', audit[1]['reason'])
        self.assertEqual(len(data['templates']), 1)
        self.assertEqual(sorted(data['batches']), ['gfx/effects/fire2', 'gfx/effects/fire3'], 'image extensions dropped')
        self.assertEqual(data['batches']['gfx/effects/fire2']['blend'], ['gl_src_alpha', 'gl_one_minus_src_alpha'], 'implicit 2D shader')
        first, second = data['emitters']
        self.assertTrue(math.isclose(first['period'], .35), 'delay + random / 2 ms')
        self.assertEqual((first['share'], second['share']), ([0, 2], [1, 2]))
        self.assertIn('light 1', limits[0])

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the particle system tests')
    def test_particle_system(self):
        result = subprocess.run(['node', str(Path(__file__).with_name('test_particles.js'))], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

import unittest
from types import SimpleNamespace

from tools import import_rtcw_map as rtcw


def describe(game, name, textures):
    return dict(name=name, stages=[dict(map=name + '.png', rgbGen=['vertex'], alphaGen=['vertex'])]), [], []


class SmokeTest(unittest.TestCase):
    def run_smoke(self, things, section='sp', rules=None):
        audit = [dict(entity=i, classname=e['classname'], status='omitted', reason='world/control/light entity; no standalone visual')
                 for i, e in enumerate(things)]
        game = SimpleNamespace(section=section)
        return (*rtcw.smoke_particles(game, things, audit, None, rules or SimpleNamespace(describe=describe)), audit)

    def test_smoke_on_puffs(self):
        things = [{'classname': 'target_smoke', 'origin': '0 0 0', 'spawnflags': '5', 'time': '1000', 'duration': '2000',
                   'start_size': '10', 'target': 't', 'delay': '300'},
                  {'classname': 'info_notnull', 'targetname': 't', 'origin': '20 0 0'},
                  {'classname': 'target_smoke', 'origin': '0 0 0', 'spawnflags': '1'},
                  {'classname': 'target_smoke', 'origin': '0 0 0', 'spawnflags': '6 '}]
        data, missing, _, audit = self.run_smoke(things)
        self.assertEqual([r['status'] for r in audit], ['drawn', 'omitted', 'omitted', 'drawn'])
        self.assertIn('SmokeON', audit[2]['reason'])
        black, white = data['templates']
        self.assertEqual(black['life'], [2.1, 2.1], 'idiot check: time below duration becomes duration + 100')
        self.assertEqual(black['velocity'], [[10, -10, 50], [30, 10, 50]], 'target offset plus start_size either way; rise wait 50')
        self.assertEqual(black['size']['end'], [96, 96])
        self.assertAlmostEqual(black['alpha']['curve']['parm'], 100 * 2000 / 2100)
        self.assertEqual(white['rgb']['start'], [[.5] * 3] * 2, 'GREY75 near the eye')
        self.assertEqual(sorted({e['batch'] for e in data['emitters'] if e['template'] == 0}), [f'smokepuffblack{i}' for i in range(1, 6)])
        self.assertEqual([e['batch'] for e in data['emitters'] if e['template'] == 1], ['smokepuff'])
        self.assertEqual(data['emitters'][0]['period'], .3)

    def test_rtcw_frees_smoke_in_mp_et_keeps_it(self):
        things = [{'classname': 'target_smoke', 'origin': '0 0 0', 'spawnflags': '4'}]
        data, _, _, audit = self.run_smoke(things, 'mp')
        self.assertIsNone(data)
        self.assertIn('single player', audit[0]['reason'])
        data, _, _, _ = self.run_smoke(things, 'mp', SimpleNamespace(describe=describe, SMOKE_IN_MP=True))
        self.assertEqual(len(data['emitters']), 5)

    def test_props_sparks_reason(self):
        _, _, _, audit = self.run_smoke([{'classname': 'props_sparks', 'origin': '0 0 0'}])
        self.assertIn('compiled out', audit[0]['reason'])


if __name__ == '__main__':
    unittest.main()

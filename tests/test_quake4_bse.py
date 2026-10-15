"""Quake 4 BSE effect decls and the browser BSE port; no retail install needed."""
from pathlib import Path
import shutil
import subprocess
import unittest

from tools.quake4_bse import parse_effects, extent

FX = '''effect effects/ambient/doorsteam
{
    size 321
    spawner "unnamed0"
    {
        count 5,5
        sprite
        {
            duration 3,5
            material "gfx/effects/smoke/cloud_alpha3"
            gravity 0,0.0125
            blend add
            start
            {
                position { line 0,-1,-80,0,-1,80 }
                velocity { box 0,-10,-10,0,10,10 surface }
                size { line 10,10,20,20 }
                fade { line 0.25,0.5 }
            }
            motion { size { envelope "linear" } fade { envelope fastinslowout count 2 } }
            end { size { line 150,150,180,180 } fade { point 1 relative } }
            impact { bounce 0.5 }
        }
    }
    emitter "unnamed1" { start 0.25,0.5 rate 20 locked constant line { duration 1,1 material "m" start { length { point 0,0,16 } } } }
    sound "hum" { soundShader "hum" }
    emitter "nomaterial" { count 2,2 sprite { duration 1,1 } }
}
'''


class Quake4BseTest(unittest.TestCase):
    def test_segments_domains_envelopes_and_ignored(self):
        e = parse_effects(FX)['effects/ambient/doorsteam']
        spawner, emitter = e['segments']
        self.assertEqual((spawner['kind'], spawner['count']), ('spawner', [5.0, 5.0]))
        p = spawner['particle']
        self.assertEqual((p['kind'], p['material'], p['duration'], p['gravity'], p['additive']),
                         ('sprite', 'gfx/effects/smoke/cloud_alpha3', [3.0, 5.0], [0.0, .0125], True))
        self.assertEqual(p['start']['position'], dict(type='line', values=[0, -1, -80, 0, -1, 80], flags=[]))
        self.assertEqual(p['start']['velocity']['flags'], ['surface'])
        self.assertEqual(p['end']['fade'], dict(type='point', values=[1.0], flags=['relative']))
        self.assertEqual(p['motion']['size']['table'], 'linear'); self.assertEqual(p['motion']['fade']['count'], [2.0])
        self.assertEqual((emitter['kind'], emitter['start'], emitter['count'], emitter['locked'], emitter['constant']),
                         ('emitter', [.25, .5], [20.0, 20.0], True, True))
        self.assertEqual(e['ignored'], {'sound': 1, 'sprite': 1})
        self.assertGreater(extent(e), 80 + 10 * 5 + 180)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for browser BSE tests')
    def test_browser_bse(self):
        result = subprocess.run(['node', str(Path(__file__).with_suffix('.js'))], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__': unittest.main()

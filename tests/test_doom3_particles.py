"""Doom 3 .prt decls and the browser particle port; no retail install needed."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from tools.doom3_particles import parse_particles, parse_fx, fx_timing, particle_reference, extent

PRT = '''particle smoke {
    depthHack 0.001
    {
        count 5
        material textures/particles/smoke
        time "2.000"
        distribution cylinder 8 8 "32.000" 0.5
        direction outward "1.000"
        orientation aimed 2 0.25
        speed "10.000" to "20.000"
        size flash_table
        gravity world -4
        color 1 0.5 0.25 1
        unknownKey 7
        fadeIn 0.9
    }
    { material broken
'''


class DoomParticleTest(unittest.TestCase):
    def test_stage_fields_line_parms_and_engine_error_handling(self):
        decl = parse_particles(PRT + '} }')['smoke']
        self.assertEqual(decl['depthHack'], .001)
        stage = decl['stages'][0]
        self.assertEqual((stage['count'], stage['time'], stage['cycleMsec']), (5, 2.0, 2000))
        self.assertEqual(stage['distribution'], 'cylinder'); self.assertEqual(stage['distributionParms'], [8, 8, 32, .5])
        self.assertEqual(stage['directionParms'], [1, 0, 0, 0], 'ParseParms stops at the end of the line')
        self.assertEqual(stage['orientationParms'], [2, .25, 0, 0])
        self.assertEqual(stage['speed'], [10, 20]); self.assertEqual(stage['size'], [0, 0, 'flash_table'])
        self.assertTrue(stage['worldGravity']); self.assertEqual(stage['gravity'], -4)
        self.assertEqual(stage['color'], [1, .5, .25, 1])
        self.assertEqual(stage['fadeIn'], .1, 'an unknown token ends the stage like idLexer::Error')
        self.assertEqual(decl['stages'][1]['material'], 'broken')

    def test_references_and_extent(self):
        class Defs:
            def model(self, name):
                if name == 'smoke_model': return {'mesh': 'Smoke.prt'}
                raise ValueError(name)
        self.assertEqual(particle_reference({'model': 'particles\\Fire.PRT'}, Defs()), 'fire')
        self.assertEqual(particle_reference({'model': 'smoke_model'}, Defs()), 'smoke')
        self.assertEqual(particle_reference({'classname': 'func_smoke', 'smoke': 'steam.prt'}, Defs()), 'steam')
        self.assertIsNone(particle_reference({'model': 'models/x.lwo'}, Defs()))
        decl = parse_particles(PRT + '} }')['smoke']
        self.assertGreaterEqual(extent(decl), 32 + 20 * 2 + 4 * 4)

    def test_fx_actions_and_timing(self):
        fx = parse_fx('''fx fx/sparks { { delay 0.5 duration 2 offset 1, 2, 3 particle "sparks.prt" }
            { duration 9 light "lights/x" , 1, 1, 1, 100 } { delay 1 duration 1 model "models/x.lwo" } }''')['fx/sparks']
        self.assertEqual(fx, [dict(delay=.5, duration=2.0, offset=[1.0, 2.0, 3.0], particle='sparks')])
        self.assertIsNone(fx_timing({'triggered': '1', 'start': '1'}, fx))
        self.assertIsNone(fx_timing({}, fx), 'neither start nor restart: waits for a trigger')
        self.assertEqual(fx_timing({'start': '1'}, fx), dict(duration=2.5, period=0.0))
        self.assertEqual(fx_timing({'restart': '3'}, fx), dict(duration=2.5, period=4.0), 'Duration() + restart * mean U(0,1)')

    def test_import_emitters_and_fx(self):
        from tests.test_doom3_maps import fixture
        from tools import import_doom3_map as maps
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); fixture(root / 'install')
            with zipfile.ZipFile(root / 'install/base/pak001.pk4', 'w') as z:
                z.writestr('particles/test.prt', PRT + '} }')
                z.writestr('fx/sparks.fx', 'fx fx/sparks { { delay 0.5 duration 2 offset 0, 0, 8 particle "smoke.prt" } }')
                z.writestr('materials/smoke.mtr', 'textures/particles/smoke { translucent { blend add map _white vertexColor } }')
                with zipfile.ZipFile(root / 'install/base/pak000.pk4') as original:
                    z.writestr('maps/game/test.map', original.read('maps/game/test.map') + b'''
                    { "classname" "func_emitter" "name" "steam" "model" "smoke.prt" "origin" "1 2 3" "shaderParm4" "-2" }
                    { "classname" "func_emitter" "name" "off" "model" "smoke.prt" "start_off" "1" }
                    { "classname" "func_smoke" "name" "chimney" "smoke" "smoke.prt" "origin" "0 0 0" }
                    { "classname" "func_fx" "name" "spark" "fx" "fx/sparks.fx" "origin" "4 0 0" "restart" "2" }
                    { "classname" "func_fx" "name" "waits" "fx" "fx/sparks.fx" }''')
            maps.import_maps(root / 'install', root / 'data', maps=['base:game/test'])
            s = json.loads((root / 'data/doom3-maps/maps/base_game_test/scene.json').read_text())
            emitters = {e['name']: e for e in s['emitters']}
            self.assertEqual(sorted(emitters), ['chimney', 'spark', 'steam'])
            self.assertEqual(emitters['steam']['parms'][4], -2); self.assertIsNone(emitters['steam']['fx'])
            self.assertEqual(emitters['spark']['origin'], [4, 0, 8]); self.assertEqual(emitters['spark']['parms'][:5], [1, 1, 1, 1, 0])
            self.assertEqual(emitters['spark']['fx'], dict(duration=2.5, period=3.5, delay=.5, length=2.0))
            self.assertEqual(emitters['steam']['materials'], ['textures/particles/smoke', 'broken'])
            self.assertIn('smoke', s['particles']); self.assertEqual(s['stats']['emitters'], 3)
            reasons = {e['name']: e['reason'] for e in s['skippedEntities']}
            self.assertEqual(reasons['waits'], 'triggered fx'); self.assertIn('start_off', reasons['off'])

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for browser particle tests')
    def test_browser_particles(self):
        result = subprocess.run(['node', str(Path(__file__).with_suffix('.js'))], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__': unittest.main()

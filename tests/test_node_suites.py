"""Runs every node check in tests/*.js and tests/*.cjs, so CI catches viewer regressions the Python tests can't see."""
import shutil, subprocess, unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
# Run by their own wrappers: test_dds.py, test_daikatana_maps.py, test_diabotical_maps.py, test_reflex_maps.py.
WIRED_ELSEWHERE = {'test_dds.js', 'daikatana_maps.test.cjs', 'diabotical_maps.test.cjs', 'reflex_maps.test.cjs'}


class NodeSuites(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the viewer checks')
    def test_node_suites(self):
        scripts = sorted(p for pattern in ('*.js', '*.cjs') for p in (ROOT / 'tests').glob(pattern) if p.name not in WIRED_ELSEWHERE)
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=script.name):
                result = subprocess.run(['node', str(script)], cwd=ROOT, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])


if __name__ == '__main__':
    unittest.main()

"""The T2 viewer's animated interior lights (tests/test_t2_interior_lights.mjs), run on Node's TypeScript stripping."""
import re, shutil, subprocess, unittest
from pathlib import Path


def node_strips_types():
    node = shutil.which('node')
    if not node:
        return False
    version = re.match(r'v(\d+)\.(\d+)', subprocess.run([node, '-v'], capture_output=True, text=True).stdout)
    return bool(version) and tuple(map(int, version.groups())) >= (22, 6)


class T2InteriorLightsTest(unittest.TestCase):
    @unittest.skipUnless(node_strips_types(), 'Node 22.6 or later is needed to run the TypeScript module')
    def test_interior_lights(self):
        root = Path(__file__).parents[1]
        result = subprocess.run(['node', '--experimental-strip-types', '--no-warnings', str(root / 'tests' / 'test_t2_interior_lights.mjs')],
                                cwd=root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

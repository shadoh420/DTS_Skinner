"""The shared DDS loader's node checks (tests/test_dds.js): formats, authored mips, WebGL S3TC sizes."""
import shutil, subprocess, unittest
from pathlib import Path


class DDSLoaderTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the loader checks')
    def test_loader(self):
        root = Path(__file__).parents[1]
        result = subprocess.run(['node', str(root / 'tests' / 'test_dds.js')], cwd=root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

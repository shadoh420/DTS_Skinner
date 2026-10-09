"""Release split (tools/split_release.py): data files go to the data ZIP, the rest to the app ZIP, reproducibly."""
from pathlib import Path
import tempfile
import time
import unittest
import zipfile

from tools.split_release import split


class SplitReleaseTest(unittest.TestCase):
    def test_split_and_reproducible(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'DTS-Skinner'
            for name in ('SkinnerApp.exe', '_internal/static/skinner.js', '_internal/static/textures/t2/a.png',
                         '_internal/static/t2/model_json/b.json', 'local-data/animations/t1/c.json.gz'):
                (app / name).parent.mkdir(parents=True, exist_ok=True)
                (app / name).write_bytes(name.encode())
            first = split(app, Path(folder) / 'one', 'v99')
            time.sleep(1.1)  # new file times must not change the bytes
            (app / 'SkinnerApp.exe').touch()
            second = split(app, Path(folder) / 'two', 'v99')
            self.assertEqual(first, second)
            with zipfile.ZipFile(Path(folder) / 'one' / 'DTS-Skinner-v99-Windows.zip') as archive:
                self.assertEqual(archive.namelist(),
                                 ['DTS-Skinner/SkinnerApp.exe', 'DTS-Skinner/_internal/static/skinner.js'])
            with zipfile.ZipFile(Path(folder) / 'one' / 'DTS-Skinner-data-Windows.zip') as archive:
                self.assertEqual(len(archive.namelist()), 3)
                self.assertTrue(all(n.startswith('DTS-Skinner/') for n in archive.namelist()))


if __name__ == '__main__':
    unittest.main()

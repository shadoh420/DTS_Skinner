"""The model browser's import boxes: every entry skinner.js wires up names elements index.html has.

A bad merge once turned ['ut', 'importUt', ...] into ['ut', 'dx', 'importUt', ...]; the missing element made the
page's start-up script throw, and no catalog loaded.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent


class ImportBoxesTest(unittest.TestCase):
    def test_every_import_button_exists(self):
        js = (ROOT / 'static' / 'skinner.js').read_text(encoding='utf-8')
        html = (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8')
        listing = re.search(r'for \(const \[gameId, button, input, status\] of \[(.*?)\]\) \{', js, re.S).group(1)
        entries = re.findall(r'\[([^\[\]]*)\]', listing)
        imports = re.findall(r"'(\w+)'", re.search(r'const IMPORTS = \[(.*?)\]', js).group(1))
        self.assertTrue(entries)
        for entry in entries:
            fields = re.findall(r"'(\w+)'", entry)
            self.assertEqual(len(fields), 4, entry)
            game, *ids = fields
            self.assertIn(game, imports, entry)
            for element in ids:
                self.assertIn(f'id="{element}"', html, entry)
        # Every game with an import box is wired up once.
        self.assertEqual(sorted(re.findall(r"\['(\w+)', 'import", listing)), sorted(set(imports)))


if __name__ == '__main__':
    unittest.main()

"""Source text must not carry UTF-8 that was read as Windows-1252 and saved again ("Â·", "â€¦")."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# A UTF-8 lead byte shown as Â..ï followed by a continuation byte shown in cp1252.
MOJIBAKE = re.compile('[Â-ï][\u0080-¿Œ-™]')


class TextEncodingTest(unittest.TestCase):
    def test_no_double_encoded_text(self):
        hits = []
        for folder, patterns in (('static', ('*.js', '*.html', '*.css')), ('templates', ('*.html',)),
                                 ('tools', ('*.py',)), ('docs', ('*.md',)), ('.', ('*.py', '*.md'))):
            for pattern in patterns:
                files = (ROOT / folder).glob(pattern) if folder == '.' else (ROOT / folder).rglob(pattern)
                for path in files:
                    if 'node_modules' in path.parts or 'vendor' in path.parts:
                        continue
                    for n, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
                        if MOJIBAKE.search(line):
                            hits.append(f'{path.relative_to(ROOT)}:{n}')
        self.assertEqual(hits, [], 'double-encoded text (fix with line.encode("cp1252").decode("utf-8"))')


if __name__ == '__main__':
    unittest.main()

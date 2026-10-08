"""Earthsiege import (tools/import_earthsiege.py): VOLN volumes, 3Space 2.0 shapes, texture banks and shade ramps."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app import app
from tools.import_earthsiege import import_catalog


def voln(members):
    """A VOLN volume holding `members` ({name: bytes}) stored (type 2) in one folder."""
    folders = b'dts\0'
    head = b'VOLN' + bytes(6) + struct.pack('<H', len(folders)) + folders + struct.pack('<HI', len(members), 0)
    at = len(head) + 18 * len(members)
    entries, body = b'', b''
    for name, data in members.items():
        entries += name.encode().ljust(13, b'\0') + struct.pack('<BI', 0, at + len(body))
        body += struct.pack('<BII', 2, len(data), 0) + data
    return head + entries + body


def tagged(kind, payload):
    return struct.pack('<HHI', kind, 0x14, len(payload)) + payload


def shape():
    """A group on no node: a textured quad (bank frame 0) and a shaded triangle (ramp 1) whose back is hidden. The
    corners wind against the stored normal (point 4), as every retail poly does."""
    points = [(0, 0, 0), (100, 0, 0), (100, 0, 100), (0, 0, 100), (0, 2048, 0)]
    hidden = 0x14 << 24 | 1
    surfaces = [0, 0, -1, -1, 1, 1, hidden, hidden]
    quad = tagged(0x0f, struct.pack('<5H', 4, 0, 4, 0, 0))
    triangle = tagged(0x03, struct.pack('<5H', 4, 0, 3, 0, 4))
    group = tagged(0x14, struct.pack('<hhh3h', -1, 0, 0, 0, 0, 0) + struct.pack('<4H', 4, 5, 8, 2) +
                   struct.pack('<4H', 0, 1, 2, 3) + b''.join(struct.pack('<3h', *p) for p in points) +
                   struct.pack('<8i', *surfaces) + quad + triangle)
    # Part 7, a visible hardpoint in BOX.GL: a placeholder the game swaps for the weapon, so not imported.
    placeholder = tagged(0x14, struct.pack('<hhh3h', -1, 7, 0, 0, 0, 0) + struct.pack('<4H', 3, 5, 4, 1) +
                         struct.pack('<3H', 0, 1, 2) + b''.join(struct.pack('<3h', *p) for p in points) +
                         struct.pack('<4i', 1, 1, 1, 1) + tagged(0x03, struct.pack('<5H', 4, 0, 3, 0, 0)))
    return tagged(0x08, struct.pack('<hhh3h', -1, 0, 0, 0, 0, 0) + struct.pack('<H', 2) + group + placeholder + struct.pack('<HH', 0, 0))


def palette():
    colours = bytearray(256 * 4)
    colours[7 * 4:7 * 4 + 3] = bytes((63, 0, 0))  # Red: the frame's texels.
    colours[8 * 4:8 * 4 + 3] = bytes((0, 63, 0))  # Green: ramp 1 at the import's light level.
    ramps = struct.pack('<i', 2) + struct.pack('<hh', 1, 0) + struct.pack('<5h', 4, 5, 6, 7, 8)
    body = struct.pack('<I', 256) + bytes(colours) + ramps
    return b'PL\0\0' + struct.pack('<I', len(body)) + body


def bank():
    frame = b'BM\0\0' + struct.pack('<IHHBBBIH', 13 + 64, 8, 8, 8, 0, 0, 64, 0) + bytes([7]) * 64
    return b'BA\0\0' + struct.pack('<II', len(frame) + 5, 1) + frame + b'\0'


class EarthsiegeImportTest(unittest.TestCase):
    def test_install_becomes_a_game(self):
        record = bytearray(0xd8)
        struct.pack_into('<h', record, 0x94, 0)  # Bank 0: LIGHT.
        with tempfile.TemporaryDirectory() as temp:
            install, output = Path(temp, 'Earthsiege'), Path(temp, 'local/es1')
            (install / 'vol').mkdir(parents=True)
            layout = struct.pack('<h', 1) + struct.pack('<hhhB', 7, -1, -1, 0).ljust(26, b'\0')  # Bone 7, on top.
            (install / 'vol/simvol0.vol').write_bytes(voln({'BOX.DTS': shape(), 'BOX.DAT': bytes(record), 'BOX.GL': layout,
                                                            'LIGHT.DBA': bank()}))
            (install / 'vol/simpatch.vol').write_bytes(voln({'WORLD0.DPL': palette()}))

            result = import_catalog(install, output, 'es1')
            self.assertEqual((result['entries'], result['ready'], result['skipped']), (1, 1, 0))
            entry = json.loads((output / 'catalog.json').read_text())[0]
            self.assertEqual((entry['model_name'], entry['category'], entry['game']), ('box', 'HERCs', 'es1'))
            model = json.loads((output / 'model_json/box.json').read_text())
            self.assertEqual(model['material_textures'], ['light_00.png', 'rgb_00ff00.png'])
            self.assertEqual(model['groups'], [dict(start=0, count=6, materialIndex=0), dict(start=6, count=3, materialIndex=1)])
            # The quad's first triangle starts at corner 3, (0, 0, 100) in the game's z-up frame: y up here, its
            # frame's bottom-left corner inset 3 texels (ES1), facing +z (the game's +y, forward).
            self.assertEqual(model['vertices'][:3], [0, 0.6, 0])
            self.assertEqual(model['uvs'][:2], [0.375, 0.625])
            self.assertEqual(model['normals'][:3], [0, 0, 1])
            a, b, c = (model['vertices'][i * 3:i * 3 + 3] for i in model['indices'][:3])
            cross = [(b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
                     (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
                     (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])]
            self.assertGreater(cross[2], 0)  # Anticlockwise seen from the side the normal faces.
            with Image.open(output / 'textures/light_00.png') as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
            with Image.open(output / 'textures/rgb_00ff00.png') as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 255, 0))

            with patch.dict('app.pack_dirs', es1=output):
                client = app.test_client()
                self.assertEqual(len(client.get('/list_models?game=es1').json), 1)
                self.assertEqual(client.get('/texture/light_00.png?game=es1').status_code, 200)
                self.assertEqual(client.get('/export_glb/box?game=es1').status_code, 200)

    def test_not_an_install(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, 'No Earthsiege volumes'):
            import_catalog(temp, Path(temp, 'out'), 'es2')


if __name__ == '__main__':
    unittest.main()

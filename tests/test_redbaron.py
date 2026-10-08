"""Red Baron 3D import (tools/import_redbaron.py): VOL volumes, 3Space 2.5 shapes, material lists and paint parts."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app import app
from tools.import_redbaron import import_catalog


def volume(members):
    """A Red Baron VOL holding `members` ({name: bytes}), every entry stored."""
    names = b''.join(name.encode() + b'\0' for name in members)
    chunk = lambda tag, size: tag + (size & 0xffffff).to_bytes(3, 'little') + b'\x80'
    head = chunk(b'VOL ', 0) + chunk(b'volh', 0) + chunk(b'vols', 4 + len(names)) + struct.pack('<I', len(names)) + names
    at = len(head) + 8 + 14 * len(members)
    index, blocks, name_at = b'', b'', 0
    for name, data in members.items():
        index += struct.pack('<IIIBB', name_at, at + len(blocks), len(data), 0, 1)
        blocks += chunk(b'VBLK', len(data)) + data
        name_at += len(name) + 1
    return head + chunk(b'voli', len(index)) + index + blocks


def tagged(kind, body):
    return struct.pack('<HHI', kind, 0x14, len(body)) + body + b'\0' * (len(body) & 1)


def part(nu=False):
    return (struct.pack('<I', 1) if nu else b'') + struct.pack('<2i4f', -1, 0, 1, 0, 0, 0)


def mesh(points, texture, faces):
    """points: (point, normal) pairs; faces: (corners[4], texture corners[4], normal point, material)."""
    return tagged(0x2b, part(True) + struct.pack('<3I', len(points), len(texture), len(faces)) +
                  b''.join(struct.pack('<6f', *p, *n) for p, n in points) + b''.join(struct.pack('<2f', *t) for t in texture) +
                  b''.join(struct.pack('<10ifi', *v, *t, n, m, 0, 0) for v, t, n, m in faces))


def shape():
    """A cell animation showing cell 0, which is its second child: a quad painted with the fuselage (material 1), a
    triangle in palette colour 5 (material 2) and a fire triangle (material 3, an effect, not drawn). Its first child,
    never shown, is a triangle of its own. Each face's corners wind against its normal, as the game's do."""
    up = (0, 1, 0)
    quad = [((0, 0, 0), up), ((10, 0, 0), up), ((10, 0, 10), up), ((0, 0, 10), up), ((0, 0, 0), up)]
    shown = mesh(quad, [(0, 0), (1, 0), (1, 1), (0, 1)], [((0, 1, 2, 3), (0, 1, 2, 3), 4, 1), ((0, 1, 2, 2), (0, 0, 0, 0), 4, 2),
                                                          ((0, 1, 2, 2), (0, 1, 2, 2), 4, 3)])
    hidden = mesh(quad, [], [((0, 1, 2, 2), (0, 0, 0, 0), 4, 2)])
    cells = tagged(0x51, part() + struct.pack('<I', 2) + hidden + shown + struct.pack('<4i', 0, 2, 1, 0))
    return tagged(0x65, part(True) + struct.pack('<I', 1) + cells) + struct.pack('<I', 0)


def material(kind, *fields, name=''):
    body = struct.pack('<I', kind) + struct.pack(f'<{len(fields)}i', *fields)
    if kind == 3:
        body += struct.pack('<I', len(name) + 1) + name.encode() + b'\0'
    return tagged(0x1f, body)


def materials():
    items = [material(2, 0, 0, 0, 0, 0xffffff, 0, 0), material(3, 0, 1, 0, 0, 0, 1, name='FUS.BMP'),  # Flags 1: keyed.
             material(1, 0, 1, 0, 5, 0, 0, 0), material(3, 0, 0, 0, 0, 0, 3, name='FIRE2.pab')]
    return struct.pack('<HHIII', 0x1e, 0x14, 0, len(items), 1) + b''.join(items)


def palette():
    colours = bytearray(1024)
    colours[5 * 4:5 * 4 + 3] = bytes((0, 255, 0))  # Green: palette colour 5.
    colours[7 * 4:7 * 4 + 3] = bytes((255, 0, 0))  # Red: the fuselage's texels.
    body = b'head' + struct.pack('<II', 4, 3) + b'data' + struct.pack('<I', 1024) + bytes(colours)
    return b'PPAL' + struct.pack('<I', len(body)) + body


def names():
    row = struct.pack('<i', 0) + b'Fokker E.III'.ljust(80, b'\0')
    return bytes(0x14) + struct.pack('<3H', 1, len(row), 2) + bytes(0x2a) + row


def bitmap():
    """Plane 00's fuselage (part 0) in squadron 0x10: a 4x2 PBMP of colour 7 but its first texel, colour 0."""
    head = b'head' + struct.pack('<I5i', 20, 2, 4, 2, 8, 8)
    data = b'data' + struct.pack('<I', 8) + bytes([0] + [7] * 7)
    return b'PBMP' + struct.pack('<I', len(head) + len(data)) + head + data


class RedBaronImportTest(unittest.TestCase):
    def test_install_becomes_a_game(self):
        with tempfile.TemporaryDirectory() as temp:
            install, output = Path(temp, 'Red Baron 3D'), Path(temp, 'local/rb3d')
            (install / 'Data/3dpatch').mkdir(parents=True)
            (install / 'Data/rb.vol').write_bytes(volume({'03008000.dts': b'not a shape', '03009000.dml': materials(),
                                                          'summer.pal': palette(), 'pnames.dat': names(),
                                                          '03000010.bmp': bitmap(), '03000011.bmp': b'later squadron',
                                                          'fire2.pab': b'BM effect'}))
            (install / 'Data/3dpatch/3dpatch.vol').write_bytes(volume({'03008000.dts': shape()}))  # Wins over rb.vol.

            result = import_catalog(install, output)
            self.assertEqual((result['entries'], result['ready']), (1, 1))
            entry = json.loads((output / 'catalog.json').read_text())[0]
            self.assertEqual((entry['model_name'], entry['display_name'], entry['category']), ('03008000', 'Fokker E.III', 'Aircraft'))
            model = json.loads((output / 'model_json/03008000.json').read_text())
            self.assertEqual(model['material_textures'], ['03000010.png', 'rgb_00ff00.png'])
            self.assertEqual(model['groups'], [dict(start=0, count=6, materialIndex=0), dict(start=6, count=3, materialIndex=1)])
            # Game x right, y forward, z up -> mirrored x, y up, z forward; 1.5 inches a unit.
            vertices = [model['vertices'][i:i + 3] for i in range(0, len(model['vertices']), 3)]
            self.assertIn([-0.381, 0.381, 0], vertices)
            # Corner 3, (0, 0, 10), takes texture vertex (0, 1): v 0 is the bitmap's top row, here too.
            at = vertices.index([0, 0.381, 0])
            self.assertEqual(model['uvs'][at * 2:at * 2 + 2], [0, 1])
            self.assertEqual(model['normals'][:3], [0, 0, 1])
            a, b, c = (vertices[i] for i in model['indices'][:3])
            cross = [(b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
                     (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
                     (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])]
            self.assertGreater(cross[2], 0)  # Anticlockwise seen from the side the normal faces.
            with Image.open(output / 'textures/03000010.png') as image:
                self.assertEqual((image.size, image.getpixel((0, 0)), image.getpixel((1, 0))), ((4, 2), (0, 0, 0, 0), (255, 0, 0, 255)))

            with patch.dict('app.pack_dirs', rb3d=output):
                client = app.test_client()
                self.assertEqual(len(client.get('/list_models?game=rb3d').json), 1)
                self.assertEqual(client.get('/texture/03000010.png?game=rb3d').status_code, 200)
                self.assertEqual(client.get('/export_glb/03008000?game=rb3d').status_code, 200)

    def test_older_shape(self):
        """An older 0x64 shape: integer part headers, and a 0x28 mesh of integer points, a 16.16 normal and 28-byte
        faces. Its material list is its name with the 8 made a 9 (variant 1: 0b0b9001.dml)."""
        corners = [(0, 0, 0), (10, 0, 0), (10, 0, 10), (0, 0, 0)]
        mesh = tagged(0x28, struct.pack('<6i', -1, 0, 1, 0, 0, 0) + struct.pack('<3I', 4, 0, 1) +
                      b''.join(struct.pack('<6i', *p, 0, 65536, 0) for p in corners) + struct.pack('<10H2i', 0, 1, 2, 2, 0, 0, 0, 0, 3, 0, 0, 0))
        old = tagged(0x64, struct.pack('<6i', -1, 0, 1, 0, 0, 0) + struct.pack('<I', 1) + mesh) + struct.pack('<I', 0)
        items = material(1, 0, 1, 0, 5, 0, 0, 0)
        dml = struct.pack('<HHIII', 0x1e, 0x14, 0, 1, 1) + items
        with tempfile.TemporaryDirectory() as temp:
            install, output = Path(temp, 'Red Baron 3D'), Path(temp, 'local/rb3d')
            (install / 'Data').mkdir(parents=True)
            (install / 'Data/rb.vol').write_bytes(volume({'0b0b8001.dts': old, '0b0b9001.dml': dml, 'summer.pal': palette()}))

            self.assertEqual(import_catalog(install, output)['ready'], 1)
            entry = json.loads((output / 'catalog.json').read_text())[0]
            self.assertEqual((entry['display_name'], entry['category']), ('Aircraft wreckage 1', 'Projectiles'))
            model = json.loads((output / 'model_json/0b0b8001.json').read_text())
            self.assertEqual((model['material_textures'], len(model['vertices']), model['normals'][:3]), (['rgb_00ff00.png'], 9, [0, 0, 1]))
            self.assertIn(-0.381, model['vertices'])

    def test_not_an_install(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, 'No Red Baron volumes'):
            import_catalog(temp, Path(temp, 'out'))


if __name__ == '__main__':
    unittest.main()

"""Quake PACK/MDL parsing, first pose/skin, seams, catalog accounting and static exports."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from app import app
from tools.import_quake import build_model, import_catalog, read_mdl, read_pak
from tools.model_data import load_model_data

INSTALL = Path('C:/Program Files (x86)/Steam/steamapps/common/Quake')
PALETTE = bytes(c for i in range(256) for c in (i, 255 - i, 7))
PIXELS = bytes([0, 1, 224, 255, 2, 3, 4, 5])


def pak(entries):
    body, directory = b'', b''
    for name, data in entries:
        directory += struct.pack('<56sii', name.encode(), 12 + len(body), len(data))
        body += data
    return struct.pack('<4sii', b'PACK', 12 + len(body), len(directory)) + body + directory


def mdl(grouped=False, pixels=PIXELS):
    # An asymmetric triangle at +x, clockwise seen from +x, and its reverse side.
    head = struct.pack('<4si3f3ff3f8if', b'IDPO', 6, 2, 3, 4, 10, -20, 30, 1, 0, 0, 0,
                       2, 4, 2, 3, 2, 2, 0, 0, 1)
    skin = struct.pack('<ii2f', 1, 2, .1, .2) + pixels + bytes([9] * 8) if grouped else struct.pack('<i', 0) + pixels
    skin += struct.pack('<ii2f', 1, 2, .1, .2) + bytes([8] * 16)  # Another skin must be skipped before ST.
    st = struct.pack('<9i', 32, 0, 0, 0, 1, 0, 0, 0, 1)
    triangles = struct.pack('<8i', 1, 0, 1, 2, 0, 0, 2, 1)

    def frame(name, x):
        return b'\0' * 8 + struct.pack('<16s12B', name.encode(), x, 1, 5, 0, x, 5, 1, 0, x, 1, 1, 0)

    frames = (struct.pack('<ii', 1, 2) + b'\0' * 8 + struct.pack('<2f', .1, .2) + frame('first', 2) + frame('later', 99)
              if grouped else struct.pack('<i', 0) + frame('first', 2))
    return head + skin + st + triangles + frames + struct.pack('<i', 0) + frame('last', 77)


def install_at(folder):
    folder.mkdir(parents=True)
    (folder / 'PAK0.PAK').write_bytes(pak([('gfx/palette.lmp', PALETTE), ('progs/player.mdl', mdl()),
                                        ('progs/broken.mdl', b'IDPO'), ('progs/s_bubble.spr', b'IDSP'),
                                        ('progs/b_shell.bsp', b'BSP')]))
    (folder / 'PAK1.PAK').write_bytes(pak([('progs/player.mdl', mdl(True))]))


class QuakeImportTest(unittest.TestCase):
    def test_first_pose_skin_groups_and_seam(self):
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                mesh = read_mdl(mdl(grouped))
                self.assertEqual(mesh['skin'], PIXELS)
                self.assertEqual(mesh['points'], [(14, -17, 50), (14, -5, 34), (14, -17, 34)])
                self.assertEqual((mesh['pose'], mesh['poses']), ('first', 3 if grouped else 2))
                data = build_model(mesh, 'player.png')
                self.assertEqual(len(data['vertices']), 12)  # Only the seam point needs duplication.
                self.assertEqual(data['vertices'][:3], [-17, 50, 14])
                self.assertEqual(data['uvs'][:2], [.125, .25])
                seam = data['indices'][3]
                self.assertEqual(data['uvs'][2 * seam:2 * seam + 2], [.625, .25])
                self.assertEqual(data['vertices'][3 * seam:3 * seam + 3], data['vertices'][:3])
                self.assertEqual(data['metadata']['fullbright_pixels'], 2)
                a, b, c = [data['vertices'][3 * i:3 * i + 3] for i in data['indices'][:3]]
                self.assertGreater((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]), 0)
                # Unequal axes preserve the left/right detail: native +y maps to viewer +x, +z to +y.
                self.assertEqual(b, [-17, 34, 14])
                self.assertEqual(c, [-5, 34, 14])

    def test_invalid_pak_and_mdl(self):
        good = pak([('progs/player.mdl', mdl())])
        self.assertEqual(read_pak(good), [('progs/player.mdl', mdl())])
        bad_entry = bytearray(good)
        struct.pack_into('<i', bad_entry, len(bad_entry) - 4, len(good))
        for data in (b'PACK', b'NOPE' + good[4:], good[:-1], good[:8] + struct.pack('<i', 1) + good[12:], bad_entry):
            with self.subTest(pak_length=len(data)), self.assertRaises(ValueError):
                read_pak(data)
        invalid = []
        for offset, value in ((4, 7), (48, 0), (60, -1), (84, 2), (88, -1)):
            data = bytearray(mdl(True))
            struct.pack_into('<i', data, offset, value)
            invalid.append(data)
        bad_triangle = bytearray(mdl())
        struct.pack_into('<i', bad_triangle, 168, 3)
        invalid.append(bad_triangle)
        for data in (b'', mdl()[:-1], *invalid):
            with self.subTest(mdl_length=len(data)), self.assertRaises(ValueError):
                read_mdl(data)

    def test_import_accounting_palette_preservation_and_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            install, output = Path(folder) / 'Quake', Path(folder) / 'out'
            install_at(install / 'id1')
            with patch.dict('app.pack_dirs', quake=output):
                client = app.test_client()
                response = client.post('/import_quake', json={'path': str(install)})
                self.assertEqual(response.status_code, 200, response.json)
                report = response.json
                self.assertEqual((report['entries'], report['ready'], report['mdl_entries'], report['mdl_skipped'],
                                  report['overridden']), (4, 1, 3, 1, 1))
                self.assertEqual(len(report['results']), 5)
                self.assertTrue(all(r.get('reason') for r in report['results'] if r['status'] != 'ready'))
                data = load_model_data(output / 'model_json' / 'player.json')
                self.assertEqual(data['metadata']['poses'], 3)  # PAK1, not PAK0.
                with Image.open(output / 'textures' / 'player.png') as image:
                    self.assertEqual(image.getpixel((0, 0)), (0, 255, 7))
                    self.assertEqual(image.getpixel((2, 0)), (224, 31, 7))
                    self.assertEqual(image.getpixel((3, 0)), (255, 0, 7))  # Fullbright, never transparent.
                self.assertEqual(len(client.get('/list_models?game=quake').json), 4)
                self.assertEqual(client.get('/model_json/player?game=quake').status_code, 200)
                with client.get('/texture/player.png?game=quake') as texture:
                    self.assertEqual(texture.status_code, 200)
                glb = client.get('/export_glb/player?game=quake')
                self.assertEqual(glb.status_code, 200)
                self.assertEqual(glb.headers['X-Skinner-Animation-Status'], 'static')
                self.assertEqual(glb.headers['X-Skinner-Animation-Clips'], '0')
                length = struct.unpack_from('<I', glb.data, 12)[0]
                gltf = json.loads(glb.data[20:20 + length])
                self.assertNotIn('animations', gltf)
                self.assertEqual(gltf['samplers'][0]['magFilter'], 9728)
                obj = client.get('/export_obj/player?game=quake')
                self.assertEqual(obj.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(obj.data)) as archive:
                    self.assertTrue(any(n.endswith('.obj') for n in archive.namelist()))
                Image.new('RGB', (4, 2), '#123456').save(output / 'textures' / 'player.png')
                edited = (output / 'textures' / 'player.png').read_bytes()
                import_catalog(install / 'id1', output)
                self.assertEqual((output / 'textures' / 'player.png').read_bytes(), edited)
                for body in ({}, {'path': ' '}):
                    self.assertEqual(client.post('/import_quake', json=body).status_code, 400)
                self.assertEqual(client.post('/import_quake', json={'path': str(install)},
                                             headers={'Origin': 'https://elsewhere.invalid'}).status_code, 403)


@unittest.skipUnless((INSTALL / 'id1' / 'PAK0.PAK').is_file() and (INSTALL / 'id1' / 'PAK1.PAK').is_file(),
                     'needs the classic registered Quake install')
class QuakeInstallTest(unittest.TestCase):
    def test_every_directory_model_is_accounted_for(self):
        expected = []
        for pak_name in ('PAK0.PAK', 'PAK1.PAK'):
            raw = (INSTALL / 'id1' / pak_name).read_bytes()
            offset, size = struct.unpack_from('<ii', raw, 4)
            for at in range(offset, offset + size, 64):
                name = raw[at:at + 56].split(b'\0')[0].decode('ascii').lower()
                if (name.startswith('progs/') and name.endswith(('.mdl', '.spr', '.bsp'))) or (name.startswith('maps/b_') and name.endswith('.bsp')):
                    expected.append((pak_name.lower(), name))
        self.assertEqual(sum(n.endswith('.mdl') for _, n in expected), 79)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            report = import_catalog(INSTALL, output)
            self.assertCountEqual([(r['pak'], r['source']) for r in report['results']], expected)
            self.assertEqual((report['ready'], report['mdl_entries'], report['mdl_skipped'], report['overridden']),
                             (92, 79, 0, 0))
            skipped = [r for r in report['results'] if r['status'] != 'ready']
            self.assertEqual(len(skipped), 3)
            self.assertTrue(all(r['source'].endswith('.spr') and r['reason'] for r in skipped))
            models = list((output / 'model_json').glob('*.json'))
            self.assertEqual(len(models), 92)
            self.assertEqual(report['bsp_items'], 13)
            for path in models:
                data = load_model_data(path)
                with Image.open(output / 'textures' / data['material_textures'][0]) as image:
                    image.verify()


if __name__ == '__main__':
    unittest.main()

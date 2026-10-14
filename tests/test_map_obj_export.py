import io
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from tools.map_obj_exporter import map_to_obj_zip, read_pack


def write_pack(root, colors):
    """A pack with three quads: a wall (index texture), water and sky; `colors` adds the RGBA block."""
    quad = lambda y: [(0, y, 0), (1, y, 0), (1, y, 1), (0, y, 1)]
    positions = np.array(quad(0) + quad(1) + quad(2), '<f4')
    uvs = np.tile([(0, 0), (1, 0), (1, 1), (0, 1)], (3, 1)).astype('<f4')
    indices = np.array([k + o for k in (0, 4, 8) for o in (0, 1, 2, 0, 2, 3)], '<u4')
    blob = positions.tobytes() + uvs.tobytes() + uvs.tobytes() + (bytes(48) if colors else b'') + indices.tobytes()
    groups = [dict(texture='aa.png', kind='normal', flags=0, start=0, count=6),
              dict(texture='bb.png', kind='turbulent', flags=0, start=6, count=6),
              dict(texture='cc.png', kind='sky', flags=0, start=12, count=6)]
    faces = [dict(texture='wall1', kind='normal'), dict(texture='*water1', kind='turbulent'), dict(texture='sky1', kind='sky')]
    scene = dict(name='test', vertices=12, indices=18, groups=groups, faces=faces, lightmap=[1, 1], **({'colors': True} if colors else {}))
    folder = root / 'maps' / 'test'
    folder.mkdir(parents=True)
    (folder / 'scene.json').write_text(json.dumps(scene), encoding='utf-8')
    (folder / 'geometry.bin').write_bytes(blob)
    textures = root / 'textures'
    textures.mkdir()
    Image.fromarray(np.arange(16, dtype=np.uint8).reshape(4, 4), 'L').save(textures / 'aa.png')
    Image.fromarray(np.arange(16, dtype=np.uint8).reshape(4, 4), 'L').save(textures / 'bb.png')
    colormap = np.zeros((64, 256, 3), np.uint8)
    colormap[32] = np.stack([np.arange(256), 255 - np.arange(256), np.full(256, 7)], axis=1)
    colormap[0] = 255  # The overbright row must not be the one used.
    Image.fromarray(colormap).save(textures / 'colormap.png')
    return folder


class MapObjExportTests(unittest.TestCase):
    def export(self, colors):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = write_pack(root, colors)
            output = io.BytesIO()
            summary = map_to_obj_zip(folder, [root / 'textures'], 'quake', output)
            with zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
        return summary, files

    def test_both_strides_export_named_materials_without_sky(self):
        for colors in (False, True):
            summary, files = self.export(colors)
            obj, mtl = files['test.obj'].decode(), files['test.mtl'].decode()
            self.assertEqual(summary, dict(materials=2, triangles=4, skipped=1, missing=[]))
            self.assertNotIn('sky', obj + mtl)
            self.assertIn('o wall1\nusemtl wall1\n', obj)
            self.assertIn('o water_water1\n', obj)
            self.assertIn('newmtl water_water1\nKa 1.000 1.000 1.000\nKd 1.000 1.000 1.000\nKs 0.000 0.000 0.000\nd 0.500\n', mtl)
            self.assertIn('map_Kd textures/wall1.png', mtl)
            self.assertIn('textures/water_water1.png', files)
            faces = [int(x.split('/')[0]) for line in obj.splitlines() if line.startswith('f ') for x in line.split()[1:]]
            self.assertEqual(len(faces), 12)
            self.assertTrue(all(1 <= f <= 12 for f in faces))
            self.assertEqual(obj.count('\nv '), 12)
            self.assertIn('vt 0.000000 1.000000', obj)  # v flipped for OBJ's bottom-left origin.
            with Image.open(io.BytesIO(files['textures/wall1.png'])) as image:
                self.assertEqual(image.mode, 'RGB')
                self.assertEqual(image.getpixel((1, 0)), (1, 254, 7))  # Colormap row 32, not the overbright row 0.

    def test_bad_geometry_size_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = write_pack(root, False)
            (folder / 'geometry.bin').write_bytes((folder / 'geometry.bin').read_bytes() + b'\0')
            with self.assertRaises(ValueError):
                read_pack(folder)


if __name__ == '__main__':
    unittest.main()

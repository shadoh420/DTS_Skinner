"""Terrain sets for the T1 Maps page (tools/t1_terrain_sets.py), on a small made-up install."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

from tools.import_t1_map import Install
from tools.t1_terrain_sets import blend_tile, build_sets, read_tile_table, turned

COLOURS = [(200, 0, 0), (0, 200, 0), (0, 0, 200), (200, 200, 0), (0, 200, 200), (200, 0, 200), (90, 90, 90), (250, 250, 250),
           (40, 40, 40)]


def tile_table(types, tiles):
    """A Terrain.dat: type names, then per tile a 256-byte name, corners (TL TR BR BL) and the editor's other fields."""
    data = struct.pack('<ii', len(types), len(tiles)) + b''.join(name.encode().ljust(32, b'\0') for name in types)
    return data + b''.join(name.encode().ljust(256, b'\0') + bytes(corners) + bytes(16) for name, corners in tiles)


def corner_png(corners, size=8, mark=0):
    """A tile whose quarters show its corner types' colours; mark tells worlds' tiles apart."""
    half = size // 2
    image = np.zeros((size, size, 3), 'uint8')
    for (top, left), kind in zip([(0, 0), (0, half), (half, half), (half, 0)], corners):
        image[top:top + half, left:left + half] = COLOURS[kind]
    image[0, 0] = mark
    out = io.BytesIO()
    Image.fromarray(image).save(out, 'PNG')
    return out.getvalue()


def quarters(png):
    image = np.asarray(Image.open(io.BytesIO(png)).convert('RGB'))
    half = image.shape[0] // 2
    return [tuple(image[top + 1, left + 1]) for top, left in [(0, 0), (0, half), (half, half), (half, 0)]]


def world(base, name, types, tiles):
    with zipfile.ZipFile(base / (name + 'Terrain.zip'), 'w') as archive:
        archive.writestr(name + '.Terrain.dat', tile_table(types, tiles))
    with zipfile.ZipFile(base / (name + 'DML.zip'), 'w') as archive:
        for tile, corners in tiles:
            archive.writestr(tile.split('.')[0] + '.png', corner_png(corners, mark=len(name)))


class TerrainSetTests(unittest.TestCase):
    def test_tile_table_reads_names_and_corners(self):
        types, tiles = read_tile_table(tile_table(['1 Dirt', '2 Grass'], [('lCCFF.BMP', (2, 2, 0, 0))]))
        self.assertEqual((types, tiles), (['1 Dirt', '2 Grass'], [('lCCFF.BMP', (2, 2, 0, 0))]))

    def test_each_turn_moves_the_corners_with_the_picture(self):
        corners = (0, 1, 2, 3)
        image = np.asarray(Image.open(io.BytesIO(corner_png(corners))).convert('RGB'))
        for k in range(8):
            moved, picture = turned(corners, image, k)
            out = io.BytesIO()
            Image.fromarray(np.ascontiguousarray(picture)).save(out, 'PNG')
            self.assertEqual(quarters(out.getvalue()), [COLOURS[kind] for kind in moved], k)

    def test_neighbouring_blended_tiles_meet_at_their_shared_edge(self):
        images = {name: np.full((32, 32, 3), value, 'float32') for name, value in (('grass', 50), ('rock', 200))}
        above = blend_tile((0, 0, 1, 1), ('grass', 'grass', 'rock', 'rock'), images, size=32)
        below = blend_tile((1, 1, 1, 0), ('rock', 'rock', 'rock', 'grass'), images, size=32)
        np.testing.assert_allclose(above[-1], below[0], atol=1e-3)  # Above's bottom edge (rock, rock) is below's top.

    def test_sets_move_tiles_between_worlds_by_role_and_keep_man_made_tiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'game/base'
            base.mkdir(parents=True)
            # Lush types: 0 dirt (ground3), 2 much grass (ground), 3 rock, 6 concrete pad; desert: 0 sand (ground), 2 rock,
            # 3 orange sand (ground3), 6 pad. Lush grass-and-rock lCRRR has only a turned desert match.
            lush = [('lCCCC.BMP', (2, 2, 2, 2)), ('lFFFF.BMP', (0, 0, 0, 0)), ('lCCRR.BMP', (2, 2, 3, 3)),
                    ('lLOGO1.BMP', (6, 6, 6, 6)), ('lCRRR.BMP', (2, 3, 3, 3)), ('lGGCC.BMP', (6, 6, 2, 2))]
            desert = [('dbbbb.bmp', (0, 0, 0, 0)), ('daaaa.bmp', (3, 3, 3, 3)), ('dbbrr.bmp', (0, 0, 2, 2)),
                      ('drrrb.bmp', (2, 2, 2, 0)), ('dyyyy.bmp', (6, 6, 6, 6))]
            world(base, 'lush', ['type'] * 9, lush)
            world(base, 'desert', ['type'] * 8, desert)
            t2 = root / 't2'
            t2.mkdir()
            for name in ('lushworld.grassmixed', 'lushworld.rocklight', 'lushworld.dirtmossy'):
                Image.new('RGB', (16, 16), (len(name) * 9 % 255, 80, 40)).save(t2 / (name + '.png'))
            pack = root / 'pack.zip'
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, 'w') as archive:
                archive.writestr('lcccc.png', corner_png((7, 7, 7, 7)))
            with zipfile.ZipFile(pack, 'w') as archive:
                archive.writestr('Config/base/lushDML.zip', inner.getvalue())
            output = root / 'pack-out'
            names = build_sets(Install(root / 'game'), output, t2, {'Uniform': pack})
            self.assertEqual(names, ['Tribes · Lush', 'Tribes · Desert', 'Tribes 2 · Lush', 'Packs · Uniform'])

            found = json.loads((output / 'terrain-sets.json').read_text())
            stock = {tuple(place): stored for stored, place in found['stock'].items()}
            self.assertEqual(len(stock), 11)
            sets = {entry['id']: entry for entry in found['sets']}
            self.assertNotIn('lush', sets['t1-lush']['tiles'])  # A world's own set leaves its maps alone.
            desert_on_lush = sets['t1-desert']['tiles']['lush']
            self.assertEqual(desert_on_lush[:2], [stock['desert', 0], stock['desert', 1]])  # Grass -> sand, dirt -> orange.
            self.assertEqual(desert_on_lush[2], stock['desert', 2])  # Grass above rock: sand above rock.
            self.assertIsNone(desert_on_lush[3])  # Pads are man-made: the map's own.
            turned_tile = (output / 'textures' / desert_on_lush[4]).read_bytes()  # Only drrrb fits, turned a quarter.
            self.assertEqual(quarters(turned_tile), [COLOURS[0], COLOURS[2], COLOURS[2], COLOURS[2]])
            # A pad's edge: the map's own pad above, the set's sand below.
            edge = quarters((output / 'textures' / desert_on_lush[5]).read_bytes())
            self.assertEqual((edge[0], edge[2]), (COLOURS[6], COLOURS[0]))

            t2_on_lush = sets['t2-lush']['tiles']['lush']
            self.assertTrue(all(t2_on_lush[i].endswith('.jpg') for i in (0, 1, 2, 4, 5)))
            self.assertIsNone(t2_on_lush[3])
            self.assertEqual(sets['pack-uniform']['tiles']['lush'][1:], [None] * 5)  # By name: only lcccc is replaced.
            self.assertEqual(quarters((output / 'textures' / sets['pack-uniform']['tiles']['lush'][0]).read_bytes())[0], COLOURS[7])

            build_sets(Install(root / 'game'), output, t2)  # A rebuild without the pack keeps the pack's set.
            self.assertIn('pack-uniform', [entry['id'] for entry in json.loads((output / 'terrain-sets.json').read_text())['sets']])


if __name__ == '__main__':
    unittest.main()

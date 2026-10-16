"""Synthetic RB3D maps. No retail data, external tools, browser or full install import.

Optional bounded install checks: RB3D_INSTALL_TESTS=1, optionally RB3D_INSTALL=...
"""
import json
import math
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import import_redbaron_map as rb


def chunk(tag, body):
    return tag + len(body).to_bytes(3, 'little') + b'\x80' + body


def volume(members, numeric=False):
    names = b'' if numeric else b''.join(name.encode() + b'\0' for name in members)
    head = chunk(b'VOL ', b'') + chunk(b'volh', b'') + chunk(b'vols', struct.pack('<I', len(names)) + names)
    start = len(head) + 8 + 14 * len(members)
    index, blocks, name_at = b'', b'', 0
    for name, data in members.items():
        index += struct.pack('<IIIBB', name if numeric else name_at, start + len(blocks), len(data), 0, not numeric)
        blocks += chunk(b'VBLK', data)
        name_at += 0 if numeric else len(name) + 1
    return head + chunk(b'voli', index) + blocks


def square(value=0, heights=(0, 1, 2, 3), material=0, flags=0):
    return struct.pack('<H6B', value, *heights, material, flags)


def ted_members(nested=False):
    # Four root squares, each 4 native units. First optionally points to a
    # child block sharing the root's height table.
    root = struct.pack('<9I', 100, 300, 0, 0, 1, 1, 0, 255, 0)
    root += struct.pack('<256i', *range(256)) + struct.pack('<2I', 1, 1)
    name = b'sbaron.dml\0'
    info = struct.pack('<2I12I', 100, 1, 0, 300, 0, 1, 1, 1, 1, 0, 255, 0, 1, 0)
    info += struct.pack('<I', len(name)) + name
    members = {100: struct.pack('<13I', 100, 501, 1, 0, 0, 0, 0, 8, 8, 0, 1, 0, 255),
               200: info, 300: root,
               300 | 0x40000: struct.pack('<7I', 100, 0, 1, 0, 0, 1, 32) +
               square(0x8000 | 500 if nested else 0) + square() * 3}
    if nested:
        members[500] = struct.pack('<11I', 100, 500, 0, 0, 0, 0, 0, 255, 1, 1, 1)
        members[500 | 0x40000] = struct.pack('<7I', 100, 0, 1, 0, 0, 1, 32) + square(0, (4, 5, 6, 7)) * 4
    return members


def table(rows, width):
    h = bytearray(100)
    struct.pack_into('<HH', h, 20, len(rows), width)
    return bytes(h) + b''.join(rows)


def palette():
    colours = bytes([10, 80, 20, 0]) * 256
    return b'PPAL' + struct.pack('<I', 1032) + b'data' + struct.pack('<I', 1024) + colours


def dml():
    return struct.pack('<HHIII', 0x1e, 0x14, 0, 1, 1) + struct.pack('<HHI8I', 0x1f, 0x14, 32, 1, 0, 0, 0, 0, 0, 0, 0)


def mission():
    h = bytearray(216)
    struct.pack_into('<8I', h, 0, 7, 3, 0, 0, 0, 13, 1000, 1)
    struct.pack_into('<I', h, 192, 2)
    struct.pack_into('<I', h, 204, 1)
    wp = lambda ident, x, time: struct.pack('<8i9BBi', ident, 0, 0, x, 200, 80, time, 300, *([0] * 9), 5, 99)
    return (struct.pack('<8sIBI', bytes(8), 7, 0, 1) + h + struct.pack('<i', 4) +
            wp(10, 100, 1000) + wp(11, 300, 3000) + struct.pack('<25i', 11, *([5] * 12), *([196] * 12)) +
            struct.pack('<i3B', 0, 1, 2, 3) + struct.pack('<11i', *([-1] * 11)))


def world():
    # Pilot has no history entries; total 172 bytes.
    pilot = bytes(79 + 8 + 39) + struct.pack('<I', 3) + bytes(5 + 8 + 29)
    return struct.pack('<BII', 1, 2421745, 2000) + pilot + struct.pack('<IB', 0, 1) + bytes(8)


class RedBaronMapsTest(unittest.TestCase):
    def test_shape_shading_modes_and_opt_in_vertex_normals(self):
        from tests.test_redbaron import material
        from unittest.mock import patch
        data = struct.pack('<HHIII', 0x1e, 0x14, 0, 3, 1) + b''.join(
            material(2, 0, mode, 0, 0, 0xffffff, 0, 0) for mode in range(3))
        self.assertEqual(rb.material_shading(data), [0, 1, 2])
        points = [(0,0,0, 1,0,0), (1,0,0, 0,1,0), (0,1,0, 0,0,1), (0,0,0, 0,0,-1)]
        face = (0,1,2,2, 0,0,0,0, 3,0)
        with patch('tools.import_redbaron.meshes', return_value=[(points, [], [face])]):
            original = rb.build_model(b'', lambda _: 'test.png')
            flat = rb.build_model(b'', lambda _: 'test.png', lambda _: False)
            smooth = rb.build_model(b'', lambda _: 'test.png', lambda _: True)
        self.assertEqual(original, flat)  # Model catalog output is unchanged.
        self.assertEqual(original['normals'], [0,-1,0]*3)
        self.assertEqual(smooth['normals'], [0,1,0, 0,0,1, -1,0,0])
        self.assertEqual(original['vertices'], smooth['vertices'])

    def test_height_normals_include_neighbours_and_preserve_flat_ground(self):
        plane = lambda x, y: 2*x + 3*y + 100
        def leaf(x, y, step):
            return (x, y, step, [plane(px, py) for px, py in
                    ((x,y), (x,y+step), (x+step,y+step), (x+step,y))], 'test.dml', 0, (x,y,step), 0)
        # Different leaf sizes meet at a chunk edge. The canonical sample
        # spacing and halo query must give exactly the same normal there.
        left = rb.terrain_normals([leaf(0, 0, 4)], plane, 2)
        right = rb.terrain_normals([leaf(4, 0, 2), leaf(4, 2, 2)], plane, 2)
        expected = (2/math.sqrt(14), 1/math.sqrt(14), -3/math.sqrt(14))
        for point in ((4, 0), (4, 4)):
            self.assertEqual(left[point], right[point])
            for a, b in zip(left[point], expected):
                self.assertAlmostEqual(a, b)
        flat = rb.terrain_normals([(0, 0, 2, [5]*4)])
        self.assertEqual(set(flat.values()), {(0, 1, 0)})
        # Only the true world edge uses this-height fallback.
        edge = rb.terrain_normals([leaf(0, 0, 2)], lambda x, y: plane(x,y) if x >= 0 else None, 2)
        self.assertAlmostEqual(edge[0,0][0] / edge[0,0][1], 1)

    def test_height_sampler_diagonal_and_bounded_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, 'tiny.ted')
            path.write_bytes(volume(ted_members(), True))
            field = rb.TerrainHeights(rb.Ted(path))
            self.assertIsNone(field.height(-1, 0))
            self.assertIsNone(field.height(0, 9))
            self.assertIsNotNone(field.height(8, 8))
            # Use a saddle to distinguish the two triangle interpolations.
            field.cell = lambda _: ([], {(0, 0, 4): [0, 8, 0, 8]})
            self.assertEqual(field.height(2, 2), 0)
            field.cell = lambda _: ([], {(0, 0, 4): [8, 0, 8, 0]})
            self.assertEqual(field.height(2, 2), 0)
            self.assertEqual(rb.terrain_triangles([8, 0, 8, 0]), ((0,1,3), (1,2,3)))

    def test_native_sun_clock_and_neutral_colour(self):
        world_settings = dict(day=2421745, time=39600000, region=3, weather=1)
        light = rb.native_lighting(world_settings, 39932920)
        self.assertAlmostEqual(sum(n*n for n in light['toward']), 1)
        self.assertAlmostEqual(light['sunElevation'], 63.304386, places=4)
        self.assertAlmostEqual(light['sunAzimuth'], 157.259346, places=4)
        def shade(normal):
            return min(1, (light['ambient']*65536 + max(0, sum(a*b for a,b in
                zip(normal, light['toward']))) * light['intensity']*65535) / 65280)
        self.assertEqual(shade((0, 1, 0)), 1)  # Accepted flat palette colour.
        self.assertEqual(shade(light['toward']), 1)
        self.assertLess(shade(tuple(-n for n in light['toward'])), .51)
        earlier = rb.native_lighting(world_settings, 6*3600000)
        later = rb.native_lighting(world_settings, 18*3600000)
        self.assertLess(earlier['toward'][0], 0)
        self.assertGreater(later['toward'][0], 0)
        night = rb.native_lighting(dict(world_settings, time=0))
        self.assertLess(night['intensity'], .3)
        self.assertAlmostEqual(night['toward'][1], math.sin(math.radians(11.25)))

    def test_native_non_drawable_layout_objects_stay_in_report(self):
        from unittest.mock import Mock
        rows = [struct.pack('<BBB4fB', 8, index, variant, 10, 20, 30, 0, 0)
                for index, variant in ((10, 0), (10, 1), (4, 0))]
        assets = Mock(files={'lo010000.dat': lambda: table(rows, 20)}, report={'warnings': set()})
        assets.model.return_value = '08048000.json'
        objects = rb.placed_objects(dict(name='Front', layout='lo010000.dat', position=[0, 0, 0], heading=0), assets)
        assets.model.assert_called_once_with('08048000.dts')
        self.assertEqual([o['model'] for o in objects], [None, None, '08048000.json'])
        self.assertEqual([o['nativeNoDrawable'] for o in objects], [True, True, False])
        self.assertEqual(objects[0]['sourceShape'], '080a8000.dts')
        self.assertTrue(any('080a8000.dts' in warning for warning in assets.report['warnings']))
        self.assertAlmostEqual(objects[0]['matrix'][14], 160 * rb.SCALE)

    def test_landmark_jump_frames_offset_layout_and_looks_at_it(self):
        place = dict(name='Test field', id=4, kind=0, position=[0, 0, 0], heading=0)
        objects = [dict(model='hangar.json', matrix=rb.matrix([x, y, 800]))
                   for x, y in ((10000, 20000), (30000, 40000))]
        # Non-drawable simulation objects must not pull the framing off target.
        objects.append(dict(model=None, matrix=rb.matrix([1e8, 1e8, 800])))
        for heading in (0, 45, 90, 180, 270):
            jump = rb.landmark_jump(dict(place, heading=heading), objects)
            self.assertEqual(jump['type'], 'Airfield')
            self.assertEqual(jump['target'], [-20000 * rb.SCALE, 800 * rb.SCALE, 30000 * rb.SCALE])
            m = jump['matrix']
            delta = [jump['target'][i] - m[12 + i] for i in range(3)]
            distance = math.sqrt(sum(d * d for d in delta))
            for i in range(3):
                self.assertAlmostEqual(-m[8 + i], delta[i] / distance)
            self.assertGreater(m[13], jump['target'][1] + 300)
            # Every placement centre fits even within a 60-degree square view.
            for obj in objects[:2]:
                offset = [obj['matrix'][12 + i] - m[12 + i] for i in range(3)]
                depth = -sum(offset[i] * m[8 + i] for i in range(3))
                for axis in (0, 4):
                    extent = abs(sum(offset[i] * m[axis + i] for i in range(3)))
                    self.assertLess(extent, depth * math.tan(math.pi / 6))
        empty = rb.landmark_jump(dict(place, kind=99), [])
        self.assertEqual(empty['type'], 'Landmark type 99')
        self.assertTrue(all(math.isfinite(x) for x in empty['matrix']))

    def test_sparse_finer_level_and_hole(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp, 'sparse.ted')
            members = ted_members()
            root = bytearray(members[300])
            struct.pack_into('<I', root, 16, 0)  # base zero, top one
            struct.pack_into('<I', root, len(root) - 4, 2)
            members[300] = root
            members[300 | 0x40000] = (struct.pack('<I12I', 100, 0, 1, 0, 0, 0, 32, 0, 1, 0, 0, 1, 32) +
                                     square(0, (4, 5, 6, 7)) * 3 + square(0xc000) +
                                     square(0x4000) + square() * 3)
            p.write_bytes(volume(members, True))
            ted = rb.Ted(p)
            leaves = list(ted.leaves(*next(ted.root_cells())))
            self.assertEqual([v[:3] for v in leaves], [(0, 0, 2), (2, 0, 2), (0, 2, 2)])
            self.assertEqual(leaves[0][3], [0, 5, 6, 7])

    def test_numeric_volume_and_child_heights(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp, 'tiny.ted')
            p.write_bytes(volume(ted_members(True), True))
            t = rb.Ted(p)
            cells = list(t.root_cells())
            self.assertEqual([(c[0], c[1], c[2]) for c in cells], [(0, 0, 4), (4, 0, 4), (0, 4, 4), (4, 4, 4)])
            leaves = list(t.leaves(*cells[0]))
            self.assertEqual(len(leaves), 4)
            self.assertEqual(leaves[0][:6], (0, 0, 2, [0, 5, 6, 7], 'sbaron.dml', 0))
            self.assertEqual(leaves[-1][:3], (2, 2, 2))

    def test_bad_references_and_truncated_volume(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp, 'bad.ted')
            members = ted_members()
            members[300 | 0x40000] = members[300 | 0x40000][:28] + square(0x8000 | 300) + square() * 3
            p.write_bytes(volume(members, True))
            t = rb.Ted(p)
            with self.assertRaisesRegex(ValueError, 'does not fit|Cyclic'):
                list(t.leaves(*next(t.root_cells())))
            p.write_bytes(volume(ted_members(), True)[:-2])
            with self.assertRaisesRegex(ValueError, 'Truncated'):
                rb.Ted(p)

    def test_mission_consumption_time_and_coordinates(self):
        m = rb.parse_mis(mission())
        self.assertEqual((m['player'], m['playerSlot']), (7, 0))
        f = m['flights'][0]
        self.assertEqual((f['link'], f['states'][0], f['states'][1]), (4, [1, 2, 3], None))
        self.assertEqual(f['waypoints'][0]['target'], 99)
        pos, heading = rb.flight_position(f, 2000)
        self.assertEqual(pos, [200, 200, 300])
        self.assertAlmostEqual(heading, rb.math.pi / 2)
        self.assertEqual(rb.matrix(pos)[12:15], [-200 * rb.SCALE, 300 * rb.SCALE, 200 * rb.SCALE])
        for size in (6, 9, 230):
            m = rb.parse_mis(mission() + bytes(size))
            self.assertEqual(m['parsedBytes'], len(mission()))
            self.assertEqual(m['trailingBytes'], size)
            self.assertEqual(len(m['flights']), 1)
            self.assertEqual(m['trailingDataSha256'], rb.hashlib.sha256(bytes(size)).hexdigest())
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            rb.parse_mis(mission()[:-1])
        self.assertEqual(rb.parse_dat(world())['stateBytes'], 8)
        self.assertEqual(rb.parse_dat(world(), {})['stateBytes'], 0)
        self.assertEqual(rb.parse_dat(world() + b'old data', {})['stateBytes'], 8)
        self.assertEqual(rb.parse_dat(b'\0' + world()[1:-8], {})['pilots'], [])
        self.assertEqual(f['aircraft'][0], 5)
        self.assertEqual(rb.geographic(*rb.ORIGINS[0], 0), (0, 0))

    def test_material_inheritance_uv_and_height_sentinels(self):
        with tempfile.TemporaryDirectory() as temp:
            members = ted_members()
            root = bytearray(members[300])
            struct.pack_into('<I', root, 16, 0)
            struct.pack_into('<I', root, len(root) - 4, 2)
            struct.pack_into('<4i', root, 36, -5, 2, 8, 12)
            struct.pack_into('<i', root, 36 + 255 * 4, 0x7fffffff)
            members[300] = root
            members[300 | 0x40000] = (struct.pack('<I12I', 100, 0, 1, 0, 0, 0, 32, 0, 1, 0, 0, 1, 32) +
                square(257, (0, 255, 255, 255), 255) + square(4030, (255, 255, 255, 3), 255) +
                square(0, (255, 1, 255, 255), 255) + square(0, (255, 255, 2, 255), 0) +
                square(0x4000, material=1) + square() * 3)
            path = Path(temp, 'inherit.ted')
            path.write_bytes(volume(members, True))
            t = rb.Ted(path)
            leaves = list(t.leaves(*next(t.root_cells())))
            self.assertEqual([v[5] for v in leaves], [1, 1, 1, 0])
            self.assertEqual(leaves[0][3], [-5, -1, 1, 3])
            self.assertEqual(leaves[2][3], [-1, 2, 5, 1])
            self.assertEqual(leaves[0][6], (0, 0, 4))
            self.assertEqual(leaves[3][6], (2, 2, 2))
            from unittest.mock import Mock
            assets = Mock(season='s', files={})
            assets.material.return_value = [(1, 0, 0), (1, 1, 0)]
            assets.slot.return_value = 'colour.png'
            target = Path(temp, 'chunk.bin')
            rb.terrain_chunk(leaves[:1], assets, target)
            raw = target.read_bytes()
            self.assertEqual(struct.unpack_from('<2f', raw, 12), (0, 1))
            self.assertEqual(struct.unpack_from('<2f', raw, 44), (0, .5))
            self.assertEqual(struct.unpack_from('<2f', raw, 76), (.5, .5))

    def test_multiplayer_bank_and_seasonal_names(self):
        self.assertEqual(rb.terrain_material('multi2.dml'), 'smulti2.dml')
        self.assertEqual(rb.terrain_material('WCOAST.DML'), 'scoast.dml')
        self.assertEqual(rb.terrain_material('baron.dml', 'w'), 'wbaron.dml')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'rbmmulti.vol').write_bytes(volume({'sbaron.dml': b'multiplayer', 'smulti2.dml': b'multi2'}))
            (root / 'rbmulti.vol').write_bytes(volume({'sbaron.dml': b'shell'}))
            (root / '3dpatch.vol').write_bytes(volume({'patch.bmp': b'patch'}))
            base = {'sbaron.dml': lambda: b'single', 'patch.bmp': lambda: b'old'}
            files = rb.multiplayer_files(root, base)
            self.assertEqual(files['sbaron.dml'](), b'multiplayer')
            self.assertEqual(files['smulti2.dml'](), b'multi2')
            self.assertEqual(files['patch.bmp'](), b'patch')
            self.assertEqual(base['sbaron.dml'](), b'single')

    def test_formation_parent_chain_and_dat_state(self):
        form = dict(offsets=[(1, 90, 0, 10), (2, 180, 0, 20)])
        self.assertEqual(rb.formation_offset(form, 0), [0, 0, 0])
        self.assertAlmostEqual(rb.formation_offset(form, 2)[0], 160)
        self.assertAlmostEqual(rb.formation_offset(form, 2)[1], -320)
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            rb.formation_offset(dict(offsets=[(2, 0, 0, 10)]), 1)
        tail = struct.pack('<IIBBBIB', 1, 55, 1, 0, 0, 0, 1)
        tail += struct.pack('<IIBI3sI', 0, 7, 1, 3, b'abc', 0)
        state = rb.parse_dat(world()[:-8] + tail, {55: 1})
        self.assertEqual(state['brigadeStates'][0]['children'][0]['data'], '616263')
        with self.assertRaisesRegex(ValueError, 'unknown brigade'):
            rb.parse_dat(world()[:-8] + tail, {})

    def test_resource_aliases_keep_real_override(self):
        files = {'03000001.bmp': lambda: b'actual', '03000003.bmp': lambda: b'patch'}
        data = table([struct.pack('<II', 0x03000002, 0x03000001), struct.pack('<II', 0x03000003, 0x03000001)], 8)
        self.assertEqual(rb.resource_aliases(files, data), 1)
        self.assertEqual(files['03000002.bmp'](), b'actual')
        self.assertEqual(files['03000003.bmp'](), b'patch')

    def test_shared_shape_reader_and_nested_layout_transform(self):
        from tests.test_redbaron import shape, materials, bitmap, palette as model_palette
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = {'summer.pal': model_palette(), '03008000.dts': shape(), '03009000.dml': materials(),
                    '03000010.bmp': bitmap(),
                    'lo020100.dat': table([struct.pack('<BBB4fB', 19, 0, 0, 10, 0, 0, 90, 0)], 20),
                    'lo130000.dat': table([struct.pack('<BBB4fB', 3, 0, 0, 10, 0, 5, 0, 0)], 20)}
            assets = rb.Assets({n: lambda value=b: value for n, b in data.items()}, root,
                               dict(missingShapes=set(), missingTextures=set(), warnings=set()))
            result = rb.placed_objects(dict(name='Field', layout='lo020100.dat', position=[100, 200, 300], heading=90), assets)
            self.assertEqual(len(result), 1)
            # Parent heading 90 rotates first offset south; child heading 180
            # rotates its offset west. z adds in native feet*8 units.
            self.assertAlmostEqual(result[0]['matrix'][12], -20 * rb.SCALE)
            self.assertAlmostEqual(result[0]['matrix'][14], 120 * rb.SCALE)
            self.assertAlmostEqual(result[0]['matrix'][13], 340 * rb.SCALE)
            model = json.loads((root / 'models' / result[0]['model']).read_text())
            self.assertEqual([g['count'] for g in model['groups']], [6, 3])
            self.assertEqual([g['shade'] for g in model['groups']], [1, 1])
            self.assertFalse(assets.report['missingShapes'])
            with Image.open(root / 'textures' / model['material_textures'][0]) as image:
                self.assertEqual(image.getpixel((0, 0))[3], 0)
                self.assertEqual(image.getpixel((1, 0)), (255, 0, 0, 255))

    def test_tiny_import_report_and_failed_refresh_preservation(self):
        with tempfile.TemporaryDirectory() as temp:
            install, output = Path(temp, 'install'), Path(temp, 'maps')
            (install / 'Data').mkdir(parents=True)
            (install / 'Single').mkdir()
            terrain = install / 'Data/BARON0.TED'
            terrain.write_bytes(volume(ted_members(), True))
            # Deliberately incomplete install: four absent TEDs, one valid MIS
            # with missing DAT and one corrupt MIS must each have a report row.
            (install / 'Single/VALID.MIS').write_bytes(mission())
            (install / 'Single/BAD.MIS').write_bytes(b'bad')
            (install / 'Data/FORMATNS.DAT').write_bytes(table([], 147))
            (install / 'Data/rb.vol').write_bytes(volume({'summer.pal': palette(), 'sbaron.dml': dml(),
                                                         'lndmrks0.dat': table([], 63)}))
            before = {p.relative_to(install): p.read_bytes() for p in install.rglob('*') if p.is_file()}
            result = rb.import_maps(install, output)
            self.assertEqual(result['imported'], ['western_front'])
            report = json.loads((output / 'report.json').read_text())
            self.assertEqual(len(report['teds']), 5)
            self.assertEqual(set(report['missions']), {'bad.mis', 'valid.mis'})
            self.assertIn('Missing companion', report['missions']['valid.mis']['reason'])
            self.assertEqual(report['teds']['baron0.ted']['squares'], 4)
            old_index = (output / 'index.json').read_bytes()
            pack = output / report['pack']
            desc = json.loads((pack / 'regions/0/0.json').read_text())
            raw = (pack / 'regions/0/0.bin').read_bytes()
            self.assertEqual(desc['stride'], 8)
            self.assertEqual(len(raw), desc['vertices'] * 32)
            self.assertEqual(struct.unpack_from('<3f', raw), (0, 0, 0))
            self.assertEqual(rb.import_maps(install, output)['skipped'], ['Western Front and single missions'])
            self.assertEqual(before, {p.relative_to(install): p.read_bytes() for p in install.rglob('*') if p.is_file()})
            terrain.write_bytes(b'bad')
            result = rb.import_maps(install, output, True)
            self.assertFalse(result['imported'])
            self.assertEqual((output / 'index.json').read_bytes(), old_index)
            self.assertTrue((pack / 'regions/0/0.bin').exists())
            # Global palette/volume failure still accounts for every source.
            (install / 'Data/rb.vol').write_bytes(volume({'irrelevant': b''}))
            result = rb.import_maps(install, output, True)
            report = json.loads((output / 'report.json').read_text())
            self.assertEqual(len(result['failed']), 7)
            self.assertIn('Shared asset setup failed', report['missions']['valid.mis']['reason'])
            self.assertEqual((output / 'index.json').read_bytes(), old_index)

    def test_season_from_the_mission_date_and_season_packs(self):
        import datetime
        day = lambda y, m, d: datetime.date(y, m, d).toordinal() + 1721425  # noqa: E731
        # 004EFB60: 20 Sep-20 Dec fall, 21 Dec-19 Mar winter, the rest summer.
        cases = {(1918, 5, 31): 'summer', (1918, 9, 19): 'summer', (1918, 9, 20): 'fall', (1916, 12, 20): 'fall',
                 (1917, 12, 21): 'winter', (1918, 1, 31): 'winter', (1918, 3, 19): 'winter', (1918, 3, 20): 'summer'}
        for date, season in cases.items():
            self.assertEqual(rb.date_season(day(*date)), season, date)
        self.assertEqual((rb.terrain_material('sbaron.dml', 'f'), rb.terrain_material('baron.dml', 'w')), ('fbaron.dml', 'wbaron.dml'))
        with tempfile.TemporaryDirectory() as temp:
            install, output = Path(temp, 'install'), Path(temp, 'maps')
            (install / 'Data/3dpatch').mkdir(parents=True)
            (install / 'Single').mkdir()
            (install / 'Data/BARON0.TED').write_bytes(volume(ted_members(), True))
            (install / 'Data/rb.vol').write_bytes(volume({'summer.pal': palette(), 'sbaron.dml': dml(), 'fbaron.dml': dml(),
                                                         'lndmrks0.dat': table([], 63)}))
            (install / 'Data/3dpatch/3dfall.vol').write_bytes(volume({'readme.bat': b'REM'}))
            result = rb.import_maps(install, output)
            # Fall has its volume, winter has none (a warning): two Western Front entries, each in its own pack.
            self.assertEqual(result['imported'], ['western_front', 'western_front_fall'])
            index = json.loads((output / 'index.json').read_text())
            self.assertEqual([entry['type'] for entry in index], ['summer preview', 'fall preview'])
            self.assertNotEqual(index[0]['pack'], index[1]['pack'])
            fall = json.loads((output / index[1]['pack'] / 'maps/western_front_fall/scene.json').read_text())
            self.assertEqual(fall['season'], 'fall')
            report = json.loads((output / 'report.json').read_text())
            self.assertIn('No Data/3dpatch/3dwinter.vol: winter not imported', report['warnings'])
            self.assertEqual(report['teds']['baron0.ted (fall)']['status'], 'imported')

    def test_mission_export_uses_birth_altitude_aircraft_and_briefing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mis, dat, dsc = (root / n for n in ('TEST.MIS', 'TEST.DAT', 'TEST.DSC'))
            mis.write_bytes(mission())
            dat.write_bytes(world())
            dsc.write_bytes(b'Protect the airfield.\0\x04\0binary tail')
            from unittest.mock import Mock
            assets = Mock(haze=[1, 2, 3], files={}, report={'missingShapes': set()}, season='s')
            assets.model.return_value = '03058000.json'
            result = rb.export_mission(mis, {'test.dat': dat, 'test.dsc': dsc}, {0: {}}, assets, table([], 147))
            assets.model.assert_called_once_with('03058000.dts', 3)
            self.assertEqual(result['startTime'], 1000)  # DAT time is 2000.
            self.assertEqual(result['briefing'], 'Protect the airfield.')
            self.assertEqual(result['viewpoints'][0][13], 300 * rb.SCALE)
            self.assertEqual(result['objects'][0]['matrix'][12], -100 * rb.SCALE)

    def test_sky_textures_carry_their_alpha_and_fall_back_to_the_clear_band(self):
        from PIL import Image

        def bmp(colour, mode='P'):
            image = Image.new(mode, (4, 2), colour)
            if mode == 'P':
                image.putpalette([0, 0, 0, 200, 210, 220] + [0] * 762)
            out = io.BytesIO()
            image.save(out, format='BMP')
            return out.getvalue()

        files = {'hz-cld0.bmp': bmp(1), 'hz-cld0.aph': bmp(128, 'L'), 'csheet1.bmp': bmp(1), 'csheet1.aph': bmp(255, 'L')}
        with tempfile.TemporaryDirectory() as temp:
            assets = rb.Assets.__new__(rb.Assets)
            assets.files, assets.output = {name: lambda data=data: data for name, data in files.items()}, Path(temp)
            assets.pngs, assets.report = {}, dict(missingTextures=set())
            sky = assets.sky()
            self.assertIsNone(sky['sun'])  # No Sun.bmp here: reported, not invented.
            self.assertEqual(assets.report['missingTextures'], {'sun.bmp'})
            # 004B41E0 falls back to hz-cld0; weather 0 has no cloud sheets (004BDA46), and missing ones are None.
            self.assertEqual(sky['ring'], [sky['ring'][0]] * 6)
            self.assertEqual((sky['clouds'][0], sky['clouds'][2]), (None, None))
            with Image.open(Path(temp) / 'textures' / sky['ring'][0]) as image:
                self.assertEqual(image.getpixel((0, 0)), (200, 210, 220, 128))
            self.assertIs(assets.sky(), sky)

    @unittest.skipUnless(shutil.which('node'), 'Node is not installed')
    def test_page_scripts(self):
        """static/rb3d-maps/stream.js: streaming, native light and haze, and the Glide sky."""
        for name in ('test_redbaron_maps_stream.cjs', 'test_redbaron_maps_sky.cjs'):
            run = subprocess.run(['node', str(Path(__file__).with_name(name))], capture_output=True, text=True, timeout=300)
            self.assertEqual(run.returncode, 0, name + run.stdout[-2000:] + run.stderr[-2000:])

    def test_output_cannot_touch_install(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'separate'):
                rb.import_maps(temp, Path(temp, 'out'))

    def test_routes_are_local_and_use_rb_pack(self):
        from app import app
        client = app.test_client()
        with client.get('/maps/rb3d/') as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(client.post('/import_rb3d_maps', json={'game': 'x'}, headers={'Origin': 'https://elsewhere.invalid'}).status_code, 403)
        self.assertEqual(client.post('/import_rb3d_maps', json={'game': 3}).status_code, 400)
        with patch('app.import_redbaron_maps', return_value={'imported': [], 'skipped': [], 'failed': {}}) as call:
            self.assertEqual(client.post('/import_rb3d_maps', json={'game': 'local game', 'replace': True}).status_code, 200)
            self.assertEqual(call.call_args.args[0], 'local game')
            self.assertEqual(call.call_args.args[1].name, 'rb3d-maps')
            self.assertIs(call.call_args.args[2], True)


@unittest.skipUnless(os.environ.get('RB3D_INSTALL_TESTS') == '1' and Path(os.environ.get('RB3D_INSTALL', rb.INSTALL)).is_dir(),
                     'Optional install probes disabled or install absent')
class RedBaronInstallProbeTest(unittest.TestCase):
    def test_one_ted_root_cell(self):
        install = Path(os.environ.get('RB3D_INSTALL', rb.INSTALL))
        ted = rb.Ted(install / 'Data/BARON0.TED')
        self.assertEqual(ted.bounds, (0, 0, 4194304, 4194304))
        self.assertTrue(list(ted.leaves(*next(ted.root_cells()))))

    def test_one_mission(self):
        install = Path(os.environ.get('RB3D_INSTALL', rb.INSTALL))
        self.assertEqual(len(rb.parse_mis((install / 'Single/ACE1.MIS').read_bytes())['flights']), 2)


if __name__ == '__main__':
    unittest.main()

"""Earthsiege Maps (tools/import_es_map.py): mission rows as the shell's generator reads them, zone terrain, placement,
the /maps/es1/ and /maps/es2/ routes, and the T1 page's sky list over other games' packs."""
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from app import app
from tools.import_es_map import (ES1_LAYOUT, SimRandom, Zone, es1_placements, import_maps, placement_matrix, point,
                                 read_mission, turn)


def words(*values):
    return struct.pack(f'<{len(values)}h', *values)


def record(size, **fields):
    """A record of `size` words, all -1 but the given word offsets (w<n>=value)."""
    out = [-1] * size
    for key, value in fields.items():
        out[int(key[1:])] = value
    return out


def mission(rows):
    """An Earthsiege 2 .MSN: revision 5, then rows 1-16 as {row: [records]} (row 5 is skipped, 64 bytes a record)."""
    data = words(5)
    for number in range(1, 17):
        records = rows.get(number, [])
        data += struct.pack('<H', len(records))
        for item in records:
            data += bytes(64) if number == 5 else words(*item[:5]) + words(*item[5]) if number == 8 else words(*item)
    return data


def bitmap(width, height, pixels):
    """A one-frame Dynamix bitmap array (.DBA) as tools/import_earthsiege.frames reads it."""
    frame = b'BM\0\0' + struct.pack('<IHHBBBIH', 13 + len(pixels), height, width, 8, 0, 0, len(pixels), 0) + bytes(pixels)
    return b'BA\0\0' + struct.pack('<II', len(frame) + 5, 1) + frame + b'\0'


class MissionTest(unittest.TestCase):
    def test_conditions_variants_and_repeated_guids(self):
        conditions = [record(7, w0=10, w1=-1, w2=3, w3=7, w5=2),  # A variant key 7 drawing below 2 (the draw is 0)...
                      record(7, w0=11, w1=10, w2=2, w3=0, w4=0),   # ...whose child for 0 survives
                      record(7, w0=12, w1=10, w2=2, w3=1, w4=1),   # and whose child for 1 does not;
                      record(7, w0=13, w1=-1, w2=0, w3=1, w4=0x119, w5=2)]  # flag 1 == 2.
        patches = [[13, 1, 504] + [0] * 8 + [-1] * 30, [-1, 1] + [0] * 9 + [-1] * 30]
        point_row = [record(11, w0=1, w4=0, w5=100, w6=0, w7=200, w8=0, w9=0, w10=0),
                     record(11, w0=1, w4=0, w5=300, w6=0, w7=400, w8=0, w9=0, w10=0),  # Same GUID: replaces.
                     record(11, w0=-1, w1=11, w4=0, w5=500, w6=0, w7=600, w8=0, w9=0, w10=0),  # The variant's payload.
                     record(11, w0=2, w2=7, w4=0, w5=9, w6=0, w7=9, w8=0, w9=0, w10=0)]  # Takes the variant's.
        group = record(82, w0=5, w3=0, w23=2, w24=0, w25=1)
        overlay = record(82, w0=5, w3=0, w23=-1, w24=3, w25=-1)  # Same GUID: set fields overlay, unset keep.
        data = mission({1: conditions, 2: patches, 6: point_row, 16: [group, overlay]})
        flags = [0] * 1000
        self.assertEqual(read_mission(data, flags)['header'][1], 0)
        flags[1] = 2
        loaded = read_mission(data, flags)
        self.assertEqual(loaded['header'][1], 504)
        self.assertEqual(point(loaded, 1), (300, 400, 0))
        self.assertEqual(point(loaded, 2), (500, 600, 0))
        self.assertEqual([(g[0], g[23], g[24], g[25]) for g in loaded['groups']], [(5, 2, 3, 1)])
        with self.assertRaises(ValueError):
            read_mission(b'\x04\x00', flags)

    def test_earthsiege_1_rows_variants_overlays_and_placement(self):
        data = words(5)
        rows = {1: [record(7, w0=10, w1=-1, w2=3, w3=7, w5=2),  # Variant key 7; its child 11 takes the draw (0).
                    record(7, w0=11, w1=10, w2=2, w3=0, w4=0),
                    record(7, w0=12, w1=-1, w2=0, w3=4, w4=0x117, w5=0)],  # ES1's "flag 4 > 0".
                2: [[12, 3] + [0] * 9, [-1, 1, 77] + [0] * 8],  # Theatre 3 only when flag 4 is set; zone 77.
                3: [record(11, w0=1, w4=0, w5=1000, w6=0, w7=2000, w8=0, w9=0, w10=0),
                    record(11, w0=2, w4=0, w5=5000, w6=0, w7=2000, w8=0, w9=0, w10=0),
                    record(11, w0=3, w4=0, w5=7000, w6=0, w7=0, w8=0, w9=0, w10=0)],
                4: [[20, -1, -1, -1, 90], [21, -1, -1, -1, 1]],  # Degrees as DBSIM reads them: 90 -> half a turn.
                5: [[30, -1, -1, -1, 2, [1, 2]]],
                8: [record(53, w0=-1, w1=11, w4=0, w24=6),  # The variant's payload: HERC type 6.
                    record(53, w0=40, w2=7),  # Keyed: takes the whole payload.
                    record(53, w0=41, w4=0, w24=2),
                    record(53, w0=42, w4=0, w24=3, w35=3)],  # Its own point.
                9: [record(32, w0=50, w4=0, w26=0)],
                11: [record(44, w0=60, w3=0, w23=0, w26=1, w29=-1),  # Group 0, the player's squad.
                     record(44, w0=61, w3=0, w23=0, w24=0, w26=1, w27=21, w29=40, w30=41, w31=42, w37=1),
                     record(44, w0=61, w3=0, w24=1),  # Same GUID: set fields overlay, -1 keeps.
                     record(44, w0=62, w3=0, w23=1, w24=0, w28=30, w29=50, w37=1)]}
        for number in range(1, 12):
            data += struct.pack('<H', len(rows.get(number, [])))
            for item in rows.get(number, []):
                data += words(*item[:5]) + words(*item[5]) if number == 5 else words(*item)
        flags = [0] * 1000
        self.assertEqual(read_mission(data, flags, ES1_LAYOUT)['header'][:2], [1, 77])
        flags[4] = 1
        mission = read_mission(data, flags, ES1_LAYOUT)
        self.assertEqual(mission['header'][:2], [3, 77])
        self.assertEqual(mission[8][40][24], 6)
        self.assertEqual(point(mission, 2, 3), (5000, 2000, 0))
        self.assertEqual([(g[0], g[23], g[24], g[29]) for g in mission['groups']], [(60, 0, -1, -1), (61, 0, 1, 40), (62, 1, 0, 50)])
        forms = {0: [[(0, 0)] * 7, [(100, -50)] + [(0, 0)] * 6], 1: [[(9, 9, 300)] * 3]}
        units, unplaced, player = es1_placements(mission, forms)
        self.assertEqual((unplaced, player), (0, (1000, 2000, 0)))
        dx, dy = turn(100, -50, 0x4000)  # Heading 1 = a quarter turn (the leader has none of its own).
        self.assertEqual([(u[0], u[1], u[2][0]) + tuple(round(v) for v in u[3:]) for u in units],
                         [(0, 1, 40, 1000, 2000, 0, 0x4000),
                          (0, 1, 41, round(1000 + dx), round(2000 + dy), 0, 0x4000),
                          (0, 1, 42, 7000, 0, 0, 0x4000),  # Its own point, no formation offset.
                          # Flyers at the route's start facing its first leg (east), climbed to 5000 from the ground.
                          (1, 1, 50, 1000, 2000, 5000, -0x4000)])

    def test_random_stream_is_the_retail_additive_table(self):
        random = SimRandom()
        self.assertEqual([random.next() for _ in range(2)], [-9683 + 4062, -8925 + 19854])
        for _ in range(1000):
            self.assertTrue(-0x8000 <= random.next() < 0x8000)


class ZoneTest(unittest.TestCase):
    def zone(self):
        image = [9, 9, 9, 9,  # Image row 0 is the north edge.
                 0, 4, 0, 0,
                 0, 0, 0, 0,
                 0, 0, 0, 0]
        files = {'DAT/ZONE7.DAT': struct.pack('<4i', 2, 2, 4, 2), 'DBA/ZONE7.DBA': bitmap(4, 4, image)}
        return Zone(files, 7)

    def test_heights_diagonals_and_levelling(self):
        zone = self.zone()
        self.assertEqual((zone.raw[3], zone.raw[0], zone.raw[2][1]), ([9, 9, 9, 9], [0, 0, 0, 0], 4))
        self.assertEqual(zone.selector(0, 1), 0)  # h00 + h11 - (h01 + h10) = 4: split (1,0)-(0,1).
        self.assertEqual(zone.selector(2, 0), 1)  # Flat: coplanar.
        self.assertAlmostEqual(zone.height(8, 24), 0)  # Cell (0, 1)'s near triangle.
        self.assertAlmostEqual(zone.height(14.4, 30.4), (4 - .1 * 4 - .1 * 4) * 2)  # Its far one, scale 2.
        zone.flatten({(1, 1)})  # One marked cell: its average forced east, north and north-east.
        self.assertEqual((zone.raw[2][1], zone.raw[2][2], zone.raw[1][2]), (0, 0, 0))
        self.assertEqual(zone.raw[3][1], 9)

    def test_placement_turns_the_shape_forward_to_its_heading(self):
        def forward(bam):  # A model's forward (+z of its JSON frame) in viewer space.
            m = placement_matrix(0, 0, 0, bam)
            return [round(m[8], 3), round(m[9], 3), round(m[10], 3)]
        self.assertEqual(forward(0), [-0.0, 0, -1.0])  # North is viewer -z.
        self.assertEqual(forward(0x4000), [-1.0, 0, -0.0])  # A quarter turn left faces west.
        self.assertEqual(placement_matrix(1000, 2000, 500, 0)[12:15], [6.0, 3.0, -12.0])


class RoutesTest(unittest.TestCase):
    def test_pages_data_and_import_validation(self):
        client = app.test_client()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'es2-maps').mkdir()
            (root / 'es2-maps/index.json').write_text('[]')
            with patch('app.local_data_dir', root):
                self.assertEqual(client.get('/maps/es2/').status_code, 200)
                self.assertEqual(client.get('/maps/es1/').status_code, 200)
                self.assertEqual(client.get('/maps/es3/').status_code, 404)
                self.assertEqual(client.get('/es2-map-data/index.json').json, [])
                self.assertEqual(client.get('/es2-map-data/../es2-maps/index.json').status_code, 404)
                self.assertEqual(client.post('/import_es_maps/es2', data='x').status_code, 400)
                self.assertEqual(client.post('/import_es_maps/es2', json={'game': ''}).status_code, 400)
                self.assertEqual(client.post('/import_es_maps/es2', json={'game': directory}).status_code, 422)

    def test_sky_list_takes_domes_q3_shaders_and_quake_ii_boxes_from_each_pack(self):
        dome = {'textures': ['a.png'], 'size': 600.0, 'feature': 0.0, 'top': [1, 2, 3], 'bottom': [4, 5, 6]}
        faces = {side: {'texture': f'sky_{side}.png'} for side in ('rt', 'bk', 'lf', 'ft', 'up', 'dn')}
        packs = {'t1-maps': {'raindance': {'sky': dome}}, 'es2-maps': {'c1_01': {'sky': dome, 'terrain': {'visibleDistance': 1376}}},
                 'quake2-maps': {'base1': {'skybox': {'name': 'unit1_', 'faces': faces}}, 'broken': {'skybox': {'faces': {'rt': {}}}}},
                 'et-maps': {'battery': {'shaders': [{'name': 'textures/skies/sd', 'stages': [], 'sky': {'cloudHeight': 512}}]}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pack, rows in packs.items():
                (root / pack).mkdir()
                (root / pack / 'index.json').write_text(json.dumps([{'id': key, 'name': key} for key in rows]))
                for key, scene in rows.items():
                    (root / pack / 'maps' / key).mkdir(parents=True)
                    (root / pack / 'maps' / key / 'scene.json').write_text(json.dumps(scene))
            with patch('app.local_data_dir', root):
                skies = app.test_client().get('/t1-map-skies').json
        self.assertEqual([(s['game'], s['group'], s['renderer'], s['base'], s['maps']) for s in skies],
                         [('t1', 'Tribes', 'dome', '/t1-map-data/', ['raindance']),
                          ('es2', 'Earthsiege 2', 'dome', '/es2-map-data/', ['c1_01']),
                          ('et', 'Wolfenstein: Enemy Territory', 'q3', '/et-map-data/', ['battery']),
                          ('quake2', 'Quake II', 'q3', '/quake2-map-data/', ['base1'])])
        self.assertEqual(skies[-1]['sky']['sky']['box'], [f'sky_{side}.png' for side in ('rt', 'bk', 'lf', 'ft', 'up', 'dn')])
        self.assertEqual(skies[0]['id'][:3], 't1-')  # T1 and Q3 ids keep their old form, so saved choices survive.


@unittest.skipUnless(os.environ.get('ES2_INSTALL'), 'set ES2_INSTALL to an Earthsiege 2 folder to import two missions')
class InstallTest(unittest.TestCase):
    def test_training_and_campaign_missions(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            report = import_maps(os.environ['ES2_INSTALL'], out, 'es2', ['TRAIN1', 'C1_01', 'C1_07'])
            self.assertEqual((sorted(report['imported']), report['failed']), (['C1_01', 'C1_07', 'TRAIN1'], {}))
            # C1_07's header sets the time-of-day bit: the urban theatre's night world (WORLD1), darker than the day's.
            day, night = (json.loads((out / f'maps/{stem}/scene.json').read_text()) for stem in ('c1_01', 'c1_07'))
            self.assertEqual((day['sky']['textures'][0][:6], night['sky']['textures'][0][:6]), ('es2w0-', 'es2w1-'))
            self.assertLess(sum(night['haze']), sum(day['haze']))
            scene = json.loads((out / 'maps/c1_01/scene.json').read_text())
            self.assertEqual((scene['zone'], scene['theatre'], scene['terrain']['squares']), (123, 'urban', 255))
            self.assertGreater(len(scene['objects']), 40)
            self.assertTrue(all(o['model'] for o in scene['objects']))
            self.assertEqual(len((out / 'maps/c1_01/pieces.bin').read_bytes()), 255 * 255)


@unittest.skipUnless(os.environ.get('ES1_INSTALL'), 'set ES1_INSTALL to an Earthsiege folder to import three missions')
class Es1InstallTest(unittest.TestCase):
    def test_campaign_and_simulator_units(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            report = import_maps(os.environ['ES1_INSTALL'], out, 'es1', ['B0_C1_00', 'B1_C0_1A', 'S2_ASS01'])
            self.assertEqual((sorted(report['imported']), report['failed']), (['B0_C1_00', 'B1_C0_1A', 'S2_ASS01'], {}))
            for stem, least in (('b0_c1_00', 30), ('b1_c0_1a', 20), ('s2_ass01', 25)):
                scene = json.loads((out / 'maps' / stem / 'scene.json').read_text())
                units = [o for o in scene['objects'] if 'feature' not in o['name']]
                self.assertGreaterEqual(len(units), least, stem)
                self.assertTrue(all(o['model'] for o in units), stem)
                self.assertTrue(any(o.get('npc') for o in units) and any(not o.get('npc') for o in units), stem)

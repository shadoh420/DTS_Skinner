"""Tiny generated DIS/DML/DIG/PBMP fixtures; no retail bytes or custom temp helpers.

Opt-in install check: T1_BUILDINGS_INSTALL_TESTS=1 (converts into a plain tempfile).
T1_BUILDINGS_BASE, T1_BUILDINGS_OPENCALL and T1_BUILDINGS_BOVIDI override local paths.
"""
import copy
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from tools.add_t1_buildings import (Textures, add_buildings, compare_geometry, encoded,
                                    export_building, handoff, team_pairs, write_catalog, write_new)
from tools.animate_t1 import load_animated_model
from tools.import_t1_map import Install, export_mounted_interior, mission_resources, parse_mission, walk
from tools.model_data import load_model_data, t1_building_names, t1_catalog_names


def png(colour, **options):
    output = io.BytesIO()
    Image.new('RGB', (2, 2), colour).save(output, 'PNG', **options)
    return output.getvalue()


def chunk(tag, data):
    return tag + struct.pack('<I', len(data)) + data


def interior(stem, texture, size=1):
    """One triangle, one material, one high LOD in the real reader's binary formats."""
    names = (stem + '.dml\0' + stem + '.dig\0').encode()
    dis = (b'ITRs' + struct.pack('<4I', 0, 1, 0, 1) + struct.pack('<4I', 100, len(stem) + 5, 0, 0)
           + struct.pack('<3I', 0, 0, len(names)) + names + struct.pack('<IB', 0, 0))
    dml = (b'PERS' + struct.pack('<IH', 0, 16) + b'TS::MaterialList' + struct.pack('<3I', 2, 1, 1)
           + struct.pack('<IfII32s', 1, 1., 0, 0, texture.encode()))
    dig = (b'PERS' + struct.pack('<IH', 0, 11) + b'ITRGeometry\0' + struct.pack('<I', 7)
           + struct.pack('<If6f9I', 1, 1., 0, 0, 0, size, size, 0, 1, 0, 0, 0, 0, 3, 3, 3, 0)
           + struct.pack('<6BHII2B2x', 0, 0, 1, 1, 0, 0, 0, 0, 0, 3, 3)
           + struct.pack('<6H', 0, 0, 1, 1, 2, 2)
           + struct.pack('<9f', 0, 0, 0, size, 0, 0, 0, size, 0)
           + struct.pack('<6f', 0, 0, 1, 0, 0, 1) + struct.pack('<2I', 0, 0))
    return {stem + '.dis': dis, stem + '.dml': dml, stem + '.dig': dig}


def palette(colour):
    return b'PL98' + struct.pack('<4i', 1, 0, 0, 0) + bytes(32) + bytes((*colour, 255)) * 256 + struct.pack('<2I', 7, 0)


def bitmap():
    return (b'PBMP' + bytes(4) + chunk(b'head', struct.pack('<5I', 1, 2, 2, 8, 0))
            + chunk(b'PiDX', struct.pack('<I', 7)) + chunk(b'data', bytes(8)))


def mission(folder, name, members, placed, colour=(12, 34, 56)):
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / (name + '.vol'), 'w') as archive:
        for key, data in dict(members, **{'world.ppl': palette(colour)}).items():
            archive.writestr(key, data)
    blocks = [('SimVolume', name + '.vol'), ('SimPalette', 'world.ppl')]
    blocks += [('InteriorShape', stem + '.0.dis') for stem in placed]
    path = folder / (name + '.mis')
    path.write_text(''.join(f'instant {kind} {{\nfileName = "{value}";\n}};\n' for kind, value in blocks))
    return path


class BuildingTests(unittest.TestCase):
    def test_real_readers_palette_collision_pairs_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, maps, static = root / 'base', root / 'maps', root / 'static'
            base.mkdir()
            (static / 'model_json').mkdir(parents=True)
            (static / 'model_json/stock.json').write_text('{}')
            (static / 'textures').mkdir()
            original = png('white')
            (static / 'textures/wall.png').write_bytes(original)
            members = {}
            for stem, tex, size in [('be_rig', 'wall.bmp', 1), ('ds_rig', 'blue.png', 1),
                                    ('hilde_be', 'wall.bmp', 1), ('hilde_ds', 'wall.bmp', 2), ('stock', 'wall.bmp', 1),
                                    ('unplaced', 'wall.bmp', 1)]:
                members.update(interior(stem, tex, size))
            members.update({'wall.bmp': bitmap(), 'blue.png': png('blue')})
            path = mission(maps, 'One', members, ['be_rig', 'ds_rig', 'hilde_be', 'hilde_ds', 'stock'])
            before = {p: p.read_bytes() for p in maps.iterdir()}
            result = add_buildings(base, [maps], static)
            self.assertEqual(set(result['models']), {'rig', 'hilde_be', 'hilde_ds'})
            self.assertEqual(set(result['unplaced']), {'unplaced'})
            self.assertEqual(result['skipped'], ['stock'])
            self.assertEqual([pair['equal'] for pair in result['pairs']], [True, False])
            self.assertEqual(result['pairs'][0]['slots'][0]['alternate_textures'], ['blue.png'])
            self.assertTrue(result['pairs'][0]['uv_arrays_equal'])
            self.assertEqual(result['sources']['be_rig']['mission'], str(path))
            summary = handoff(result).decode()
            self.assertIn('| 0 | be_rig_wall.png | [0] | blue.png | True |', summary)
            self.assertIn('unplaced', summary)
            self.assertIn('world.ppl', result['sources']['be_rig']['resources'])
            self.assertEqual((static / 'textures/wall.png').read_bytes(), original)
            with Image.open(static / 'textures/be_rig_wall.png') as image:
                self.assertEqual(image.getpixel((0, 0)), (12, 34, 56))
            self.assertTrue((static / 'textures/blue.png').is_file())
            self.assertEqual(set(t1_building_names(static / 'model_json')), set(result['models']))
            self.assertEqual(t1_catalog_names(static / 'model_json'), ['hilde_be', 'hilde_ds', 'rig', 'stock'])
            model = load_model_data(static / 'model_json/rig.json')
            self.assertEqual(model['indices'], [0, 1, 2])
            self.assertEqual(model['uvs'], [-.5, -.5, -1.5, -.5, -.5, -1.5])
            with patch('tools.animate_t1._source', side_effect=AssertionError('Static interior sought retail source')):
                self.assertEqual(load_animated_model('rig', None, model)['animation_clips'], [])
            snapshot = {p: p.read_bytes() for p in static.rglob('*') if p.is_file()}
            self.assertEqual(add_buildings(base, [maps], static)['models'], {})
            self.assertEqual(snapshot, {p: p.read_bytes() for p in static.rglob('*') if p.is_file()})
            self.assertEqual(before, {p: p.read_bytes() for p in maps.iterdir()})

    def test_shared_export_matches_direct_export_and_numbered_fallback(self):
        from tools.import_t1_map import export_interior
        import contextlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            members = interior('house', 'wall.png')
            members['house.0.dis'] = members.pop('house.dis')
            members['wall.png'] = png('red')
            path = mission(root, 'One', members, ['house'])
            nodes = list(walk(parse_mission(path.read_text())))
            model, textures, source = export_building(Install(root), path, nodes, 'house', 'house.0.dis')
            self.assertEqual(source['member'], 'house.0.dis')
            work = root / 'direct'
            work.mkdir()
            for name, data in members.items():
                (work / ('house.dis' if name == 'house.0.dis' else name)).write_bytes(data)
            with contextlib.redirect_stdout(io.StringIO()):
                export_interior.main(str(work / 'house.dis'), str(work), str(work), str(work))
            model.pop('metadata')
            self.assertEqual(model, json.loads((work / 'house.json').read_text()))
            self.assertEqual(textures['wall.png'], png('red'))

    def test_pixel_reuse_case_collision_and_exclusive_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Wall.PNG').write_bytes(png('red', compress_level=0))
            (root / 'house_wall.png').write_bytes(png('green'))
            store = Textures(root)
            self.assertEqual(store.add('house', 'wall.png', png('red', compress_level=9)), 'Wall.PNG')
            self.assertEqual(store.add('house', 'wall.bmp', png('blue')), 'house_2_wall.png')
            self.assertEqual(store.add('house', 'wall.bmp', png('blue')), 'house_2_wall.png')
            self.assertEqual(len(store.pending), 1)
            with self.assertRaisesRegex(ValueError, 'Refusing to overwrite'):
                write_new(root / 'Wall.PNG', b'changed')
            with self.assertRaises(ValueError):
                from tools.add_t1_buildings import filename
                filename('../escape.dis')

    def test_geometry_order_noise_winding_multiplicity_and_team_detection(self):
        a = dict(vertices=[0, 0, 0, 1, 0, 0, 0, 1, 0], indices=[0, 1, 2], uvs=[0, 0, 1, 0, 0, 1],
                 groups=[dict(start=0, count=3, materialIndex=0)], material_textures=['red.png'])
        b = copy.deepcopy(a)
        b['vertices'] = [0, 1.0000001, 0, 0, 0, 0, 1, 0, 0]
        b['indices'] = [1, 2, 0]
        self.assertTrue(compare_geometry(a, b)['equal'])
        b['indices'] = [1, 0, 2]
        self.assertFalse(compare_geometry(a, b)['equal'])
        b = copy.deepcopy(a)
        b['indices'] *= 2
        self.assertFalse(compare_geometry(a, b)['equal'])
        b = copy.deepcopy(a)
        b['vertices'][0] = .0001
        self.assertFalse(compare_geometry(a, b)['equal'])
        self.assertEqual(team_pairs(['hilde_be', 'hilde_ds', 'be_rig', 'ds_rig', 'ccbeaglelz', 'ccdswordlz', 'foo_be']),
                         [('be_rig', 'ds_rig', 'rig'), ('ccbeaglelz', 'ccdswordlz', 'cclz'), ('hilde_be', 'hilde_ds', 'hilde')])

    def test_local_game_catalog_names_each_building_by_its_mission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'base').mkdir()
            caves = mission(root / 'mod', 'Caves', dict(interior('house', 'wall.png'), **{'wall.png': png('red')}), ['house'])
            local = root / 'local' / 'ge'
            # What the shipped catalog already has is not copied into a local game.
            self.assertEqual(add_buildings(root / 'base', [caves], root / 'skipped', also_skip=['House'])['models'], {})
            add_buildings(root / 'base', [caves], local, game='ge')
            self.assertEqual(json.loads((local / 'model_json' / 'house.json').read_text(encoding='utf-8'))['game'], 'ge')
            write_catalog(local, 'ge')
            catalog = json.loads((local / 'catalog.json').read_text(encoding='utf-8'))
            self.assertEqual(catalog, [dict(model_name='house', display_name='house', game='ge', category='Caves',
                                            texture_name='wall.png', status='ready')])

    def test_failed_mission_tries_next_and_failed_batch_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'base'
            base.mkdir()
            members = interior('house', 'wall.png')
            bad = mission(root / 'bad', 'A', members, ['house'])
            good = mission(root / 'good', 'B', dict(members, **{'wall.png': png('red')}), ['house'])
            static = root / 'static'
            # A mission missing a texture loses to one that has it...
            result = add_buildings(base, [bad, good], static)
            self.assertEqual(result['sources']['house']['mission'], str(good))
            self.assertEqual(result['sources']['house']['earlier_candidates_failed'][0]['mission'], str(bad))
            # ...and alone it still converts, with that slot left empty and the texture recorded.
            alone = add_buildings(base, [bad], root / 'alone')
            self.assertEqual(len(alone['sources']['house']['missing_textures']), 1)
            # A building no mission can read at all still stops the batch before anything is written.
            broken = mission(root / 'broken', 'C', {}, ['house'])
            with self.assertRaisesRegex(ValueError, 'No usable placing mission'):
                add_buildings(base, [broken], root / 'none')
            self.assertFalse((root / 'none').exists())

    def test_mount_precedence_and_loose_base_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = mission(root, 'One', {'x.txt': b'first'}, [])
            with zipfile.ZipFile(root / 'two.zip', 'w') as archive:
                archive.writestr('x.txt', b'later')
            (root / 'loose.txt').write_bytes(b'loose')
            nodes = list(walk(parse_mission(path.read_text() + 'instant SimVolume {\nfilename = "two.vol";\n};\n')))
            _, _, volumes, warnings, read = mission_resources(Install(root), path, nodes)
            self.assertEqual(read('X.TXT'), b'later')
            self.assertEqual(read('LOOSE.TXT'), b'loose')
            self.assertEqual([v['volume'] for v in volumes], ['One.vol', 'two.zip'])
            self.assertEqual(warnings, [])
            del read, _  # Release the mounted ZIP readers before Windows removes the fixture.

    def test_map_import_keeps_mission_palette_after_catalog_addition(self):
        from tools.import_t1_map import import_mission
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'base'
            base.mkdir()
            members = interior('house', 'wall.bmp')
            members['wall.bmp'] = bitmap()
            members['land.dtf'] = (b'GFIL' + struct.pack('<iii', 0, 1, 9) + b'house.dml'
                                   + struct.pack('<iii', 1, 1, 0) + bytes(40)
                                   + struct.pack('<iii', 1, 1, 0) + struct.pack('<i', 0))
            members['land#0.dtb'] = (b'GBLK' + struct.pack('<Ii16siiffii', 0, 0, b'block-0', 1, 0, 1, 4, 1, 1)
                                     + struct.pack('<4f', 1, 2, 3, 4) + b'\x00\x00' + struct.pack('<4H', 255, 255, 255, 255))
            first = mission(root / 'a', 'A', members, ['house'], colour=(255, 0, 0))
            second = mission(root / 'b', 'B', members, ['house'], colour=(0, 0, 255))
            second.write_text(second.read_text().replace('fileName = "house.0.dis";',
                              'fileName = "house.0.dis";\nposition = "0 0 0";')
                              + 'instant SimTerrain {\ntedFileName = "land.dtf";\n};\n')
            add_buildings(base, [first], root / 'static')
            # Real map conversion, tiny terrain; no lights/sky/weather fixture needed.
            scene = import_mission(Install(base), second, root / 'pack', root / 'static/model_json')
            self.assertEqual(scene['objects'][0]['source'], 'pack')
            model = json.loads((root / 'pack/models' / scene['objects'][0]['model']).read_text())
            with Image.open(root / 'pack/textures' / model['material_textures'][0]) as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))

    def test_family_override_restore_static_exports_and_release_membership(self):
        from app import app
        from tools.split_release import DATA
        self.assertTrue('_internal/static/t1-buildings/a.json'.startswith(DATA))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = mission(root / 'maps', 'One', dict(interior('house', 'wall.png'), **{'wall.png': png('red')}), ['house'])
            add_buildings(root, [path], root / 'static')
            with patch('app.model_json_dir', root / 'static/model_json'), patch('app.textures_dir', root / 'static/textures'), \
                 patch('app.local_data_dir', root / 'local'):
                client = app.test_client()
                self.assertEqual(client.get('/list_models').json[0]['category'], 'Custom map buildings')
                client.post('/model_families', json=dict(models=['house'], family='My bases'))
                entry = client.get('/list_models').json[0]
                self.assertEqual((entry['category'], entry['import_category']), ('My bases', 'Custom map buildings'))
                client.post('/model_families', json=dict(models=['house'], family=''))
                self.assertEqual(client.get('/list_models').json[0]['category'], 'Custom map buildings')
                for route in ('/model_json/house', '/export_obj/house', '/export_glb/house'):
                    with client.get(route) as response:
                        self.assertEqual(response.status_code, 200, (route, response.get_data()[:200]))
                # Exercise the standalone packaged checker against synthetic Flask responses,
                # without a server/browser, including additions beyond the legacy stock list.
                import contextlib
                from urllib.parse import urlsplit
                from tools import check_packaged
                for name in ('house10', 'house2'):
                    (root / f'static/model_json/{name}.json').write_bytes((root / 'static/model_json/house.json').read_bytes())
                (root / 'model_catalog.txt').write_text('house\n')
                (root / 'build').mkdir()

                def get(url, **kwargs):
                    parts = urlsplit(url)
                    with client.get(parts.path + ('?' + parts.query if parts.query else '')) as response:
                        self.assertEqual(response.status_code, 200)
                        return io.BytesIO(response.data)

                with patch.object(check_packaged, '__file__', str(root / 'tools/check_packaged.py')), \
                     patch.object(check_packaged, 'urlopen', get), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(check_packaged.check('http://fixture', ('t1',))['t1']['catalog'], 3)


BASE = Path(os.environ.get('T1_BUILDINGS_BASE', 'C:/retro2tribes/base'))
OPENCALL = Path(os.environ.get('T1_BUILDINGS_OPENCALL', 'C:/retro2tribes/opencall2'))
BOVIDI = Path(os.environ.get('T1_BUILDINGS_BOVIDI', 'C:/Users/c/T1OpenGLMods/megaversion/T1OpenGLMods/bovidi/Maps'))


@unittest.skipUnless(os.environ.get('T1_BUILDINGS_INSTALL_TESTS') == '1' and all(p.is_dir() for p in (BASE, OPENCALL, BOVIDI)),
                     'opt in with T1_BUILDINGS_INSTALL_TESTS=1 and provide the base and both mission folders')
class BuildingInstallTests(unittest.TestCase):
    def test_custom_catalog_real_installs(self):
        # Preserve the stock exclusion without copying retail models into the fixture.
        with tempfile.TemporaryDirectory() as directory:
            static = Path(directory)
            (static / 'model_json').mkdir()
            shipped = Path(__file__).resolve().parents[1] / 'static/model_json'
            custom = t1_building_names(shipped)
            for p in shipped.glob('*.json'):
                if p.stem not in custom:
                    (static / 'model_json' / p.name).write_text('{}')
            report = add_buildings(BASE, [OPENCALL, BOVIDI], static)
            self.assertEqual(len(report['sources']), 63)
            self.assertIn('siam', report['unplaced'])
            self.assertEqual({(p['left'], p['right']) for p in report['pairs']}, {
                ('be_rig', 'ds_rig'), ('hilde_be', 'hilde_ds'), ('helibunker_be', 'helibunker_ds'), ('ccbeaglelz', 'ccdswordlz')})
            for name in report['models']:
                model = load_model_data(static / 'model_json' / (name + '.json'))
                for texture in model['material_textures']:
                    self.assertTrue(texture.startswith('[') or (static / 'textures' / texture).is_file())


if __name__ == '__main__':
    unittest.main()

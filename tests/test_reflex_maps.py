"""Reflex map import and serving boundaries, and the page's JavaScript tests (tests/reflex_maps.test.cjs) when Node
is installed. Set REFLEX_GAME_BASE to a Reflex Arena folder to also import that install."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import app
from tools.import_reflex_map import describe, import_maps, map_id, read_material

MAP = '\r\n'.join([
    'reflex map version 8',
    'prefab ledge',
    '\tentity',
    '\t\ttype WorldSpawn',
    '\t\tString256 title Not the title',
    '\tbrush',
    '\t\tvertices',
    '\t\t\t0.000000 0.000000 0.000000', '\t\t\t8.000000 0.000000 0.000000', '\t\t\t0.000000 8.000000 0.000000', '\t\t\t0.000000 0.000000 8.000000',
    '\t\tfaces',
    '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 2 1 0x00000000 common/materials/wood/bare',
    '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 1 3 0xff332805 common/materials/stone/concrete',
    '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 3 2 0x00000000 ',
    '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 0 3 2 0x00000000 structural/dev/dev_grey128',
    '\t\t\t0.000000 0.000000 1.000000 1.000000 0.000000 1 2 3 0x00000000 internal/editor/textures/editor_clip',
    'global',
    '\tentity',
    '\t\ttype WorldSpawn',
    '\t\tString256 title Test Walk',
    '\t\tString256 ownerString Someone + Someone Else',
    '',
])


def material(shader, *parameters):
    """A material file as the game writes one: (type, name, value bytes) per parameter."""
    pad = lambda data, size: data + b'\0' * (size - len(data))
    raw = b'\x14\x00\x0e\xd0' + pad(shader.encode(), 128) + struct.pack('<3I', 0x11b, len(parameters), 0)
    for kind, name, value in parameters:
        raw += struct.pack('<I', kind) + pad(name.encode(), 128) + pad(value, 128)
    return raw


CONCRETE = material('internal/shaders/deferredPbrStylized', (3, 'albedo', struct.pack('<4f', .37, .38, .35, 1)),
                    (0, 'metallic', struct.pack('<f', 0)), (0, 'roughness', struct.pack('<f', .8)))


class ReflexMapsTest(unittest.TestCase):
    def test_material_files_are_read_by_their_parameter_table(self):
        self.assertEqual(len(CONCRETE), 924)  # The size of the stock concrete.material.
        shader, parameters = read_material(CONCRETE)
        self.assertEqual(shader, 'internal/shaders/deferredPbrStylized')
        self.assertEqual([round(value, 4) for value in parameters['albedo']], [.37, .38, .35, 1])
        self.assertAlmostEqual(parameters['roughness'], .8, 6)
        _, lava = read_material(material('internal/shaders/fluid', (4, 'textureDiffuse', b'internal/effects/litspheres/water_c')))
        self.assertEqual(lava, {'textureDiffuse': 'internal/effects/litspheres/water_c'})
        # Types 1 and 2 are two and three floats: a dev material is a texture tinted by a colour.
        _, dev = read_material(material('internal/shaders/deferredPbr_TEXTUREALBEDOSPEC_TEXTUREMETA_TEXTURENORMALS_TINTED',
                                        (4, 'textureAlbedoSpec', b'dev_nogrid_albedospec'), (2, 'tintColor', struct.pack('<3f', 1, 0, 0)),
                                        (1, 'uvScale', struct.pack('<2f', .01, .01))))
        self.assertEqual((dev['tintColor'], [round(v, 4) for v in dev['uvScale']]), ([1, 0, 0], [.01, .01]))
        with self.assertRaisesRegex(ValueError, 'not a Reflex material'):
            read_material(b'PK\x03\x04' + bytes(200))
        with self.assertRaisesRegex(ValueError, 'cut short'):
            read_material(CONCRETE[:600])

    def test_describe_reads_the_global_title_author_and_face_materials(self):
        title, author, materials = describe(MAP)
        self.assertEqual((title, author), ('Test Walk', 'Someone + Someone Else'))
        # A face with no material names none; the prefab's title is not the map's.
        self.assertEqual(materials, {'common/materials/wood/bare', 'common/materials/stone/concrete', 'internal/editor/textures/editor_clip', 'structural/dev/dev_grey128'})

    def test_import_finds_maps_by_their_first_line_and_workshop_maps_beside_the_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / 'steamapps/common/Reflex Arena'
            (game / 'maps').mkdir(parents=True)
            (game / 'maps/Test Walk.map').write_text(MAP, newline='')
            (game / 'maps/readme.map').write_text('not a map')
            # Materials are in zip archives named .pak in base; a loose material file there comes first.
            (game / 'base/common/materials/wood').mkdir(parents=True)
            (game / 'base/common/materials/wood/bare.material').write_bytes(
                material('internal/shaders/deferredPbrStylized', (3, 'albedo', struct.pack('<4f', .5, .25, 0, 1)), (0, 'metallic', struct.pack('<f', 1))))
            with zipfile.ZipFile(game / 'base/common.pak', 'w') as pak:
                pak.writestr('common/materials/stone/', b'')
                pak.writestr('common/materials/stone/concrete.material', CONCRETE)
                pak.writestr('common/materials/wood/bare.material', material('x', (3, 'albedo', struct.pack('<4f', 1, 1, 1, 1))))
            with zipfile.ZipFile(game / 'base/structural.pak', 'w') as pak:
                pak.writestr('structural/dev/dev_grey128.material', material('tinted', (4, 'textureAlbedoSpec', b'dev_grid16_albedospec'),
                                                                             (2, 'tintColor', struct.pack('<3f', .5, .5, .5))))
            (game / 'base/broken.pak').write_bytes(b'not a zip')
            workshop = root / 'steamapps/workshop/content/328070/42'
            workshop.mkdir(parents=True)
            (workshop / 'other.map').write_text(MAP.replace('Test Walk', 'Other'), newline='')
            pack = root / 'pack'
            report = import_maps(game, pack)
            self.assertEqual((sorted(report['imported']), report['skipped'], report['failed']), (['Test Walk', 'other'], [], {}))
            index = json.loads((pack / 'index.json').read_text())
            self.assertEqual([(item['id'], item['group'], item['title']) for item in index],
                             [('test_walk', 'Reflex Arena', 'Test Walk'), ('workshop__42__other', 'Steam Workshop', 'Other')])
            # The map is copied as it is, CR LF and all, under a name that changes with its content.
            self.assertEqual((pack / 'maps' / index[0]['file']).read_bytes(), MAP.encode())
            colours = json.loads((pack / 'materials.json').read_text())
            self.assertEqual(colours['common/materials/stone/concrete'],
                             dict(colour=[.37, .38, .35], metallic=0.0, roughness=.8, shader='internal/shaders/deferredPbrStylized', source='common.pak'))
            self.assertEqual((colours['common/materials/wood/bare']['colour'], colours['common/materials/wood/bare']['metallic']), ([.5, .25, 0], 1))
            self.assertEqual(colours['structural/dev/dev_grey128'],
                             dict(colour=[.5, .5, .5], shader='tinted', source='structural.pak', tints='dev_grid16_albedospec'))
            self.assertEqual(report['materials'], 3)
            self.assertEqual(report['uncoloured'], ['internal/editor/textures/editor_clip'])
            self.assertEqual(import_maps(game, pack)['skipped'], ['Test Walk', 'other'])
            # A changed map replaces its old copy.
            (game / 'maps/Test Walk.map').write_text(MAP.replace('Someone Else', 'Nobody'), newline='')
            import_maps(game, pack, replace=True)
            self.assertEqual(len(list((pack / 'maps').glob('test_walk-*.map'))), 1)
            with self.assertRaisesRegex(ValueError, 'does not exist'):
                import_maps(root / 'absent', pack)
            with self.assertRaisesRegex(ValueError, 'No Reflex map files'):
                (root / 'empty').mkdir()
                import_maps(root / 'empty', root / 'other')

    def test_map_id(self):
        self.assertEqual(map_id('Abandoned Shelter'), 'abandoned_shelter')

    def test_routes_are_local_and_confined_to_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'reflex-maps/maps').mkdir(parents=True)
            (root / 'reflex-maps/index.json').write_text('[]')
            (root / 'reflex-maps/maps/test-0123456789ab.map').write_text(MAP)
            (root / 'secret.txt').write_text('outside pack')
            client = app.test_client()
            with patch('app.local_data_dir', root):
                with client.get('/maps/reflex/') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
                with client.get('/reflex-map-data/index.json') as response:
                    self.assertEqual(response.json, [])
                    self.assertNotIn('immutable', response.headers.get('Cache-Control', ''))
                with client.get('/reflex-map-data/maps/test-0123456789ab.map') as response:
                    self.assertIn('immutable', response.headers['Cache-Control'])
                with client.get('/reflex-map-data/../secret.txt') as response:
                    self.assertEqual(response.status_code, 404)
                self.assertEqual(client.post('/import_reflex_maps', json={'game': str(root)}, headers={'Origin': 'http://elsewhere.example'}).status_code, 403)
                self.assertEqual(client.post('/import_reflex_maps', json={'game': ''}).status_code, 400)
                with client.post('/import_reflex_maps', json={'game': str(root / 'absent')}) as response:
                    self.assertEqual((response.status_code, response.json['error']), (422, 'Reflex Arena folder does not exist'))

    @unittest.skipUnless(shutil.which('node'), 'Node is not installed')
    def test_page_scripts(self):
        """Brush CSG and the map file reader/writer (static/reflex-maps/brush.js, mapfile.js)."""
        test = Path(__file__).with_name('reflex_maps.test.cjs')
        run = subprocess.run(['node', '--test', str(test)], capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stdout[-4000:] + run.stderr[-2000:])

    @unittest.skipUnless(os.environ.get('REFLEX_GAME_BASE'), 'set REFLEX_GAME_BASE to a Reflex Arena folder to import its maps')
    def test_install_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            report = import_maps(os.environ['REFLEX_GAME_BASE'], Path(directory) / 'pack')
            self.assertEqual(report['failed'], {})
            self.assertTrue(report['imported'])
            if shutil.which('node'):
                env = dict(os.environ, REFLEX_MAPS=str(Path(directory) / 'pack/maps'))
                run = subprocess.run(['node', '--test', str(Path(__file__).with_name('reflex_maps.test.cjs'))], capture_output=True, text=True, timeout=600, env=env)
                self.assertEqual(run.returncode, 0, run.stdout[-4000:])


if __name__ == '__main__':
    unittest.main()

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
            (game / 'maps/Test Walk.light').write_bytes(b'baked light')
            (game / 'maps/readme.map').write_text('not a map')
            # Materials are in zip archives named .pak in base; a loose material file there comes first.
            (game / 'base/common/materials/wood').mkdir(parents=True)
            (game / 'base/common/materials/wood/bare.material').write_bytes(
                material('internal/shaders/deferredPbrStylized', (3, 'albedo', struct.pack('<4f', .5, .25, 0, 1)), (0, 'metallic', struct.pack('<f', 1))))
            with zipfile.ZipFile(game / 'base/common.pak', 'w') as pak:
                pak.writestr('common/materials/stone/', b'')
                pak.writestr('common/materials/stone/concrete.material', CONCRETE)
                pak.writestr('common/materials/wood/bare.material', material('x', (3, 'albedo', struct.pack('<4f', 1, 1, 1, 1))))
            with zipfile.ZipFile(game / 'base/internal.pak', 'w') as pak:
                pak.writestr('internal/editor/textures/editor_clip.material', material('internal/shaders/standard_TEXTUREDIFFUSE', (4, 'textureDiffuse', b'editor_clip_c')))
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
            # The map is copied as it is, CR LF and all, under a name that changes with its content; its baked light too.
            self.assertEqual((pack / 'maps' / index[0]['file']).read_bytes(), MAP.encode())
            self.assertEqual((pack / 'maps' / index[0]['light']).read_bytes(), b'baked light')
            self.assertNotIn('light', index[1])
            colours = json.loads((pack / 'materials.json').read_text())
            self.assertEqual(colours['common/materials/stone/concrete'],
                             dict(colour=[.37, .38, .35], metallic=0.0, roughness=.8, shader='internal/shaders/deferredPbrStylized', source='common.pak'))
            self.assertEqual((colours['common/materials/wood/bare']['colour'], colours['common/materials/wood/bare']['metallic']), ([.5, .25, 0], 1))
            self.assertEqual(colours['structural/dev/dev_grey128'],
                             dict(colour=[.5, .5, .5], shader='tinted', source='structural.pak', tints='dev_grid16_albedospec'))
            self.assertEqual(report['materials'], 3)
            # A material without a colour is still kept with its shader, which says whether it is see-through; its
            # texture is looked for too (models' glowing materials use theirs), and this made-up install has none.
            self.assertEqual(colours['internal/editor/textures/editor_clip'],
                             dict(shader='internal/shaders/standard_TEXTUREDIFFUSE', source='internal.pak', textureError="KeyError: 'editor_clip_c'"))
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


def textureset(name, images):
    """A .textureset file as the game writes one: {image name: (DXGI format, RGBA image)}, one copy of each."""
    import io
    from PIL import Image
    pad = lambda data, size: data + b'\0' * (size - len(data))
    table, blobs, offset = b'', b'', 2636
    for image_name, (fmt, image) in images.items():
        output = io.BytesIO()
        image.save(output, 'DDS', pixel_format='DXT1')
        data = output.getvalue()[128:]
        table += pad(image_name.encode(), 128) + struct.pack('<7I', offset, 0xffffffff, 0xffffffff, 1, 0, *image.size)
        blob = struct.pack('<11I', *image.size, 1, 1, fmt, 1, 0, 1, 8, 0, 0) + data
        blobs += blob
        offset += len(blob)
    head = b'\x20\x00\x0f\xd0' + pad(name.encode(), 128) + struct.pack('<2I', len(images), 0)
    return pad(head + table, 2636) + blobs


class ReflexTexturesTest(unittest.TestCase):
    def test_textureset_images_decode_from_their_best_copy(self):
        from PIL import Image
        from tools.reflex_textures import decode_textureset_image, textureset_images
        red = Image.new('RGBA', (8, 8), (255, 0, 0, 255))
        raw = textureset('structural/dev/test', {'structural/dev/test_albedoSpec': (72, red)})
        images = textureset_images(raw)
        self.assertEqual(list(images), ['structural/dev/test_albedoSpec'])
        # BC1 sRGB (72), which Pillow does not name, is read as plain BC1.
        self.assertEqual(decode_textureset_image(raw, images['structural/dev/test_albedoSpec']).getpixel((3, 3))[:3], (255, 0, 0))
        with self.assertRaisesRegex(ValueError, 'not a Reflex textureset'):
            textureset_images(b'DDS ' + bytes(200))

    def test_import_bakes_material_textures_and_thumbnails(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / 'Reflex Arena'
            (game / 'maps').mkdir(parents=True)
            (game / 'maps/Test Walk.map').write_text(MAP, newline='')
            # The albedo is a flat grey and the meta texture's blue channel is 0.6 along a line, as the dev grid's: the game
            # draws that line lighter.
            meta = Image.new('RGBA', (8, 8), (161, 0, 255, 255))
            for y in range(8): meta.putpixel((0, y), (255, 0, 153, 255))
            (game / 'base').mkdir()
            with zipfile.ZipFile(game / 'base/structural.pak', 'w') as pak:
                pak.writestr('structural/dev/dev_grey128.material', material('internal/shaders/deferredPbr_TEXTUREALBEDOSPEC_TEXTUREMETA_TEXTURENORMALS_TINTED',
                    (4, 'textureAlbedoSpec', b'dev_grid16_albedospec'), (4, 'textureMeta', b'dev_grid16_meta'), (2, 'tintColor', struct.pack('<3f', .5, .5, .5))))
                pak.writestr('structural/dev/dev_grid16.textureset', textureset('structural/dev/dev_grid16', {
                    'structural/dev/dev_grid16_albedoSpec': (72, Image.new('RGBA', (8, 8), (200, 200, 200, 255))), 'structural/dev/dev_grid16_meta': (71, meta)}))
                pak.writestr('structural/dev/dev_unused.material', CONCRETE)
            with zipfile.ZipFile(game / 'base/thumbs_material.pak', 'w') as pak:
                pak.writestr('thumbs_material/structural/dev/dev_grey128.textureset', textureset('thumbs_material/structural/dev/dev_grey128', {
                    'thumbs_material/structural/dev/dev_grey128': (72, Image.new('RGBA', (16, 16), (0, 0, 255, 255)))}))
            pack = Path(directory) / 'pack'
            report = import_maps(game, pack)
            self.assertEqual((report['textures'], report['thumbs']), (1, 1))
            colours = json.loads((pack / 'materials.json').read_text())
            entry = colours['structural/dev/dev_grey128']
            self.assertEqual((entry['texture'], entry['thumb']), ('dev_grid16_albedospec__dev_grid16_meta.png', 'structural~dev~dev_grey128.png'))
            baked = Image.open(pack / 'textures' / entry['texture'])
            # BC1 keeps colours to within a few levels.
            self.assertEqual(baked.mode, 'RGB')
            self.assertTrue(all(abs(channel - 200) < 10 for channel in baked.getpixel((4, 4))), baked.getpixel((4, 4)))
            self.assertGreater(baked.getpixel((0, 4))[0], 240)
            self.assertEqual(Image.open(pack / 'thumbs' / entry['thumb']).size, (128, 128))
            # Every material of the game is listed for the browser, also those no map names.
            self.assertIn('structural/dev/dev_unused', colours)

    def test_reflex_is_a_texture_library_for_models_and_maps(self):
        from PIL import Image
        from tools.model_data import material_texture_refs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'reflex-maps/textures').mkdir(parents=True)
            Image.new('RGB', (4, 4), (229, 229, 229)).save(root / 'reflex-maps/textures/dev_grid16_albedospec__dev_grid16_meta.png')
            client = app.test_client()
            with patch('app.local_data_dir', root):
                self.assertEqual(client.get('/list_textures?game=reflex').json, ['dev_grid16_albedospec__dev_grid16_meta.png'])
                with client.get('/texture/dev_grid16_albedospec__dev_grid16_meta.png?game=reflex') as response:
                    self.assertEqual(response.status_code, 200)
                self.assertEqual(client.get('/texture_metadata?game=reflex').json[0]['width'], 4)
                self.assertIn('dev_grid16_albedospec__dev_grid16_meta.png', client.get('/texture_versions?game=reflex').json)
                # Reflex has textures, not models.
                self.assertEqual(client.get('/list_models?game=reflex').status_code, 400)
        # A model's slot may take a Reflex texture.
        names, games, _ = material_texture_refs(dict(game='t1', material_textures=['a.png']), {'0': {'game': 'reflex', 'filename': 'dev_grid16_albedospec__dev_grid16_meta.png'}})
        self.assertEqual((names, games), (['dev_grid16_albedospec__dev_grid16_meta.png'], ['reflex']))


def mesh_file_bytes():
    """A one-triangle .mesh as the game writes one: one material, one level of detail, a bone b_light posed 2 below
    the origin with its x axis down."""
    head = b'\x23\x00\x0a\xd0' + struct.pack('<I6f3I', 1, 0, 0, 0, 8, 8, 0, 1, 0, 9) + b'MaterialA\0'
    vertex = lambda x, y, z: struct.pack('<3f4B3f2f4f', x, y, z, 255, 255, 255, 255, 0, 0, -1, 0, 0, 1, 0, 0, 1)
    block = struct.pack('<4I', 3, 3, 0x1d, 4) + vertex(0, 0, 0) + vertex(8, 0, 0) + vertex(0, 0, 8) + struct.pack('<3H', 0, 1, 2)
    shadow = struct.pack('<4I', 0, 0, 0, 4)
    identity = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    pose = (0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, -2, 0, 1)
    return head + block + shadow + struct.pack('<i', -1) + b'b_light\0' + struct.pack('<16f', *identity) + struct.pack('<16f', *pose)


def effect_file_bytes():
    """An .effect with a mesh record (its material and colour set, scale 2) and a spot light on b_light."""
    mesh = bytearray(2141)
    mesh[0] = 1
    mesh[1:10] = b'test/mesh'
    mesh[1025:1025 + 28] = b'common/materials/stone/stone'
    struct.pack_into('<4f', mesh, 897, .5, .5, .5, 1)
    struct.pack_into('<f', mesh, 2117, 2)
    spot = bytearray(185)
    spot[0] = 4
    struct.pack_into('<4f4f', spot, 1, 1, .9, .7, 1, 2.5, 16, 192, 0)
    struct.pack_into('<2f', spot, 37, 5, 40)
    spot[45:52] = b'b_light'
    records = bytes(mesh + spot)
    return b'\x33\x00\x0c\xd0' + struct.pack('<10I', 0x945893a8, 2, 2, 2, 44, 44 + len(records), 44 + 2 * len(records), 0, 0, 0) + records * 3


class ReflexModelsTest(unittest.TestCase):
    def test_effects_and_meshes_are_read_and_written_for_the_page(self):
        from tools.reflex_models import export_models, read_effect, read_mesh
        mesh = read_mesh(mesh_file_bytes())
        self.assertEqual((mesh['materials'], len(mesh['parts']), mesh['parts'][0]['indices']), (['MaterialA'], 1, [0, 1, 2]))
        self.assertEqual(mesh['bones']['b_light'], dict(position=[0, -2, 0], axis=[0, -1, 0]))
        effect = read_effect(effect_file_bytes())
        self.assertEqual(effect['meshes'][0]['materials'][0], 'common/materials/stone/stone')
        self.assertEqual((effect['meshes'][0]['colours'][0], effect['meshes'][0]['colours'][1], effect['meshes'][0]['scale']), ([.5, .5, .5, 1], None, 2))
        self.assertEqual({k: effect['lights'][0][k] for k in ('kind', 'intensity', 'near', 'far', 'inner', 'outer', 'bone')},
                         dict(kind='spot', intensity=2.5, near=16, far=192, inner=5, outer=40, bone='b_light'))
        files = {'test/effect.effect': effect_file_bytes(), 'test/mesh.mesh': mesh_file_bytes()}
        with tempfile.TemporaryDirectory() as directory:
            materials, failed = export_models(files.get, {'test/effect', 'test/missing'}, Path(directory))
            self.assertEqual((materials, failed), ({'common/materials/stone/stone'}, {'test/missing': 'no such effect'}))
            written = json.loads((Path(directory) / 'effects.json').read_text())['effects']['test/effect']
            # A spot light hangs from its bone and shines along the bone's x axis.
            self.assertEqual((written['lights'][0]['position'], written['lights'][0]['direction']), ([0, -2, 0], [0, -1, 0]))
            raw = (Path(directory) / 'models' / written['meshes'][0]['file']).read_bytes()
            self.assertEqual(struct.unpack_from('<4I', raw), (1, 0, 3, 3))
            self.assertEqual(len(raw), 16 + 3 * (12 + 12 + 8 + 4) + 3 * 4)

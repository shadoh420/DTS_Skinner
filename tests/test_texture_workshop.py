"""Transform pixels through preview and exports; source files must remain untouched."""
import contextlib
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from app import app
from tools.glb_exporter import model_to_glb
from tools.obj_exporter import json_to_obj_zip
from tools.texture_workshop import texture_metadata, _image_metadata, read_tags
from test_glb_export import read_glb


def model(game='t1'):
    return dict(game=game, vertices=[0, 0, 0, 1, 0, 0, 0, 1, 0],
                uvs=[0, 0, 1, 0, 0, 1], indices=[0, 1, 2], material_textures=['sample.png'])


def pixels(raw):
    with Image.open(io.BytesIO(raw)) as image:
        return image.size, list(image.convert('RGBA').getdata())


def embedded(document, binary, slot):
    material = document['materials'][slot]
    texture = document['textures'][material['pbrMetallicRoughness']['baseColorTexture']['index']]
    image = document['images'][texture['source']]
    view = document['bufferViews'][image['bufferView']]
    return binary[view['byteOffset']:view['byteOffset'] + view['byteLength']]


class TextureWorkshopTests(unittest.TestCase):
    def test_library_dimensions_do_not_decode_pixels_and_hue_stays_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGB', (110, 64), (255, 0, 0)).save(root / 'red.png')
            with patch('app.textures_dir', root), patch('app.local_data_dir', root / 'local'):
                client = app.test_client()
                with patch.object(Image.Image, 'convert', side_effect=AssertionError('Decoded pixels during library load')):
                    response = client.get('/texture_metadata?game=t1&details=dimensions')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json, [dict(filename='red.png', width=110, height=64, tags=[])])
                response = client.get('/texture_metadata?game=t1')
                self.assertEqual(response.json[0]['hue'], 0)
                # Header cache invalidates when an editor replaces the PNG.
                Image.new('RGB', (120, 90), (0, 255, 0)).save(root / 'red.png')
                response = client.get('/texture_metadata?game=t1&details=dimensions')
                self.assertEqual((response.json[0]['width'], response.json[0]['height']), (120, 90))

    def test_every_transform_preview_download_obj_glb_and_source_preservation(self):
        colors = [(255, 0, 0, 0), (0, 255, 0, 64), (0, 0, 255, 127),
                  (255, 255, 0, 128), (255, 0, 255, 200), (0, 255, 255, 255)]
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            source = root / 'sample.png'
            image = Image.new('RGBA', (3, 2))
            image.putdata(colors)
            image.save(source)
            original = source.read_bytes()
            source_stat = source.stat()
            path = root / 'sample.json'
            path.write_text(json.dumps(model()))
            stack.enter_context(patch('app.textures_dir', root))
            stack.enter_context(patch('app.model_path', return_value=path))
            stack.enter_context(patch('app.local_data_dir', root / 'local'))
            # Exercise an animation cache containing deliberately stale material transforms.
            cache = root / 'local/animations/t1/sample.json.gz'
            cache.parent.mkdir(parents=True)
            with gzip.open(cache, 'wt', encoding='utf-8') as stream:
                json.dump(dict(model(), material_texture_transforms=[{'rotation': 180}]), stream)
            client = app.test_client()
            for rotation in (0, 90, 180, 270):
                for flip_x in (False, True):
                    for flip_y in (False, True):
                        with self.subTest(rotation=rotation, flip_x=flip_x, flip_y=flip_y):
                            transform = dict(rotation=rotation, flip_x=flip_x, flip_y=flip_y)
                            # Independent coordinate mapping, not Pillow's transform implementation.
                            width, height = (2, 3) if rotation in (90, 270) else (3, 2)
                            expected = [None] * 6
                            for y in range(2):
                                for x in range(3):
                                    dx, dy = {0: (x, y), 90: (1-y, x), 180: (2-x, 1-y), 270: (y, 2-x)}[rotation]
                                    dx = width - dx - 1 if flip_x else dx
                                    dy = height - dy - 1 if flip_y else dy
                                    expected[dy * width + dx] = colors[y * 3 + x]
                            expected = ((width, height), expected)
                            query = dict(rotation=rotation, flip_x=int(flip_x), flip_y=int(flip_y))
                            with client.get('/texture/sample.png', query_string=query) as response:
                                self.assertEqual(response.status_code, 200)
                                self.assertEqual(pixels(response.data), expected)
                            response = client.get('/texture/sample.png', query_string=dict(query, download=1, opaque=1))
                            self.assertIn('attachment;', response.headers['Content-Disposition'])
                            self.assertNotIn('filename=sample.png', response.headers['Content-Disposition'])
                            self.assertEqual(pixels(response.data), expected)
                            response = client.get('/texture/sample.png', query_string=dict(query, opaque=1))
                            self.assertTrue(all(pixel[3] == 255 for pixel in pixels(response.data)[1]))
                            overrides = {'0': dict(game='t1', filename='sample.png', transform=transform)}
                            query = dict(materials=json.dumps(overrides))
                            preview = client.get('/model_json/sample', query_string=query).json
                            self.assertEqual(preview['material_texture_transforms'], [transform])
                            with contextlib.redirect_stdout(io.StringIO()):
                                response = client.get('/export_obj/sample', query_string=query)
                            self.assertEqual(response.status_code, 200)
                            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                                name = next(name for name in archive.namelist() if name.endswith('.png'))
                                self.assertEqual(pixels(archive.read(name)), expected)
                                self.assertIn(name, archive.read('sample.mtl').decode())
                            response = client.get('/export_glb/sample', query_string=query)
                            self.assertEqual(response.status_code, 200)
                            document, binary = read_glb(response.data)
                            self.assertEqual(pixels(embedded(document, binary, 0)), expected)
                            self.assertEqual(document['materials'][0]['extras']['textureTransform'], transform)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(source.stat().st_mtime_ns, source_stat.st_mtime_ns)
            self.assertEqual(list(root.glob('*.png')), [source])

    def test_duplicate_slots_and_filename_collisions_and_q3_opacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = Image.new('RGBA', (2, 1))
            image.putdata([(255, 0, 0, 20), (0, 255, 0, 200)])
            image.save(root / 'sample.png')
            Image.new('RGBA', (1, 1), 'blue').save(root / 'sample_r90.png')
            transforms = [{'rotation': 90}, {'rotation': 180}, {'rotation': 90}, {}]
            data = dict(model('q3'), material_textures=['sample.png'] * 3 + ['sample_r90.png'],
                        material_texture_games=['t2'] * 4, material_texture_transforms=transforms,
                        indices=[0, 1, 2] * 4,
                        material_settings=[{'alphaFunc': 'GE128'}] * 4,
                        groups=[dict(start=i*3, count=3, materialIndex=i) for i in range(4)])
            path = root / 'sample.json'
            path.write_text(json.dumps(data))
            output = io.BytesIO()
            with contextlib.redirect_stdout(io.StringIO()):
                json_to_obj_zip(path, root, output, 'sample', texture_dirs={'t2': root})
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
                metadata = json.loads(archive.read('metadata.json'))
                names = metadata['material_texture_paths']
                self.assertEqual(names[0], names[2])
                self.assertEqual(len(set(names)), 3)
                self.assertTrue(all(name.startswith('textures/t2/') for name in names))
                self.assertEqual(pixels(archive.read(names[0]))[0], (1, 2))
                with Image.open(io.BytesIO(archive.read(metadata['opacity_maps']['0']))) as opacity:
                    self.assertEqual(opacity.size, (1, 2))
                    self.assertEqual(list(opacity.getdata()), [0, 255])
            document, binary = read_glb(model_to_glb(data, root, texture_dirs={'t2': root}))
            self.assertEqual(len(document['images']), 3)
            self.assertEqual(pixels(embedded(document, binary, 0))[0], (1, 2))
            self.assertEqual(pixels(embedded(document, binary, 1))[1][0], (0, 255, 0, 200))

    def test_metadata_hue_wrap_grayscale_alpha_and_cache_invalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'test.png'
            image = Image.new('RGBA', (2, 1))
            image.putdata([(255, 0, 10, 255), (255, 10, 0, 255)])
            image.save(source)
            metadata = texture_metadata(source, 't1')
            self.assertLess(min(metadata['hue'], 360-metadata['hue']), 1)
            self.assertEqual((metadata['width'], metadata['height']), (2, 1))
            hits = _image_metadata.cache_info().hits
            with patch('tools.texture_workshop.Image.open', side_effect=AssertionError('must reuse cache')):
                self.assertEqual(texture_metadata(source, 't1'), metadata)
            self.assertEqual(_image_metadata.cache_info().hits, hits + 1)
            Image.new('RGB', (3, 4), (100, 100, 100)).save(source)
            self.assertEqual(texture_metadata(source, 't1'), dict(width=3, height=4, hue=None))
            Image.new('RGBA', (2, 1), (0, 255, 0, 0)).save(source)
            self.assertIsNone(texture_metadata(source, 't1')['hue'])
            self.assertAlmostEqual(texture_metadata(source, 't2')['hue'], 120)

    def test_tag_persistence_namespace_validation_and_origin(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            (root / 't2').mkdir()
            for path in (root, root / 't2'):
                Image.new('RGB', (2, 3), 'red').save(path / 'sample.png')
            stack.enter_context(patch('app.textures_dir', root))
            stack.enter_context(patch('app.local_data_dir', root / 'local'))
            client = app.test_client()
            payload = dict(filename='sample.png', tags=['Walls', ' metal ', 'walls'])
            self.assertEqual(client.post('/texture_tags', json=payload).json['tags'], ['walls', 'metal'])
            payload['tags'] = ['wood']
            self.assertEqual(client.post('/texture_tags?game=t2', json=payload).json['tags'], ['wood'])
            tag_file = root / 'local/texture-tags.json'
            self.assertEqual(read_tags(tag_file), {'t1': {'sample.png': ['walls', 'metal']}, 't2': {'sample.png': ['wood']}})
            self.assertEqual(client.get('/texture_metadata').json[0]['tags'], ['walls', 'metal'])
            self.assertEqual(client.get('/texture_metadata?game=t2').json[0]['tags'], ['wood'])
            original = tag_file.read_bytes()
            for bad in ([1], [''], ['a'*49], ['ß'*48], ['a\nb'], ['x']*33, {}, None):
                response = client.post('/texture_tags', json=dict(filename='sample.png', tags=bad))
                self.assertEqual(response.status_code, 422)
            for bad in ('../sample.png', '..\\sample.png', 'C:sample.png', 0, None):
                self.assertEqual(client.post('/texture_tags', json=dict(filename=bad, tags=[])).status_code, 422)
            self.assertEqual(client.post('/texture_tags', json=payload, headers={'Origin': 'https://elsewhere.invalid'}).status_code, 403)
            self.assertEqual(client.post('/texture_tags', json=payload, headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
            self.assertEqual(client.post('/texture_tags', data='filename=sample.png').status_code, 400)
            self.assertEqual(client.post('/texture_tags', json=dict(filename='missing.png', tags=[])).status_code, 404)
            self.assertEqual(tag_file.read_bytes(), original)
            with patch('tools.texture_workshop.os.replace', side_effect=OSError('disk unavailable')):
                self.assertEqual(client.post('/texture_tags', json=payload).status_code, 422)
            self.assertEqual(tag_file.read_bytes(), original)
            self.assertEqual(list(tag_file.parent.iterdir()), [tag_file])
            # Corrupt persisted metadata must be reported, never overwritten silently.
            tag_file.write_text('broken')
            self.assertEqual(client.post('/texture_tags', json=payload).status_code, 422)
            self.assertEqual(client.get('/texture_metadata').status_code, 422)
            self.assertEqual(tag_file.read_text(), 'broken')

    def test_invalid_transforms_rejected_by_preview_and_both_exports(self):
        invalid = [False, 90, [], 'rotate', {'rotation': True}, {'rotation': -90}, {'rotation': 360},
                   {'rotation': 90.0}, {'flip_x': 1}, {'flip_y': 'yes'}, {'path': 'elsewhere'}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.json'
            path.write_text(json.dumps(model()))
            with patch('app.model_path', return_value=path):
                client = app.test_client()
                for transform in invalid:
                    for route in ('model_json', 'export_obj', 'export_glb'):
                        with self.subTest(transform=transform, route=route):
                            query = dict(materials=json.dumps({'0': dict(game='t1', filename='sample.png', transform=transform)}))
                            self.assertEqual(client.get(f'/{route}/sample', query_string=query).status_code, 422)
                for query in ({'rotation': '-90'}, {'rotation': '90.0'}, {'flip_x': 'true'}, {'flip_y': '2'}):
                    self.assertEqual(client.get('/texture/sample.png', query_string=query).status_code, 422)


if __name__ == '__main__':
    unittest.main()

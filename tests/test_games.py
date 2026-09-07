"""Shared game/skin/export contract, without depending on a local game install."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import app, TextureWatcher
from tools.model_data import load_model_data


class GameTests(unittest.TestCase):
    def test_atomic_replacement_with_preserved_timestamp_is_detected(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, replacement = root/'skin.png', root/'replacement.tmp'
            target.write_bytes(b'old')
            stat = target.stat()
            with patch('app.textures_dir', root):
                before = app.test_client().get('/texture_versions').json['skin.png']
                replacement.write_bytes(b'new')
                os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                replacement.replace(target)
                after = app.test_client().get('/texture_versions').json['skin.png']
            self.assertEqual(before[:2], after[:2])
            self.assertNotEqual(before, after)

    def test_rgb_inspector_preserves_reflectivity_alpha_on_disk(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'t2').mkdir()
            path = root/'t2/reflect.png'
            Image.new('RGBA', (1,1), (123,45,67,0)).save(path)
            original = path.read_bytes()
            with patch('app.textures_dir', root):
                with app.test_client().get('/texture/reflect.png?game=t2&opaque=1') as response:
                    with Image.open(io.BytesIO(response.data)) as image:
                        self.assertEqual(image.getpixel((0,0)), (123,45,67))
                self.assertEqual(app.test_client().get('/texture/C:secret.png?opaque=1').status_code,400)
            self.assertEqual(path.read_bytes(), original)

    def test_complete_t2_inventory_and_generated_snapshots(self):
        from tools.model_data import model_sort_key
        root = Path(__file__).resolve().parents[1]
        inventory = json.loads((root/'static/t2/inventory.json').read_text())
        catalog = app.test_client().get('/list_models?game=t2').json
        expected = (root/'t2_catalog.txt').read_text().splitlines()
        self.assertEqual([entry['model_name'] for entry in catalog], expected)
        self.assertEqual(expected, sorted(expected, key=model_sort_key))
        self.assertEqual(len(catalog), inventory['source_model_count'] + inventory['unique_dif_count'])
        ready = {entry['model_name'] for entry in catalog if entry['status'] == 'ready'}
        self.assertEqual(ready, {path.stem for path in (root/'static/t2/model_json').glob('*.json')})
        for entry in catalog:
            with self.subTest(model=entry['model_name']):
                if entry['status'] != 'ready':
                    self.assertTrue(entry['warnings'])
                    continue
                data = load_model_data(root/'static/t2/model_json'/(entry['model_name']+'.json'))
                self.assertEqual(data['winding'], 'ccw')
                self.assertEqual(len(data['normals']), len(data['vertices']))
                self.assertEqual(len(data['material_flags']), len(data['material_textures']))
                self.assertIn('source', data['metadata'])

    def test_namespaced_skin_selection_preview_and_export(self):
        data = dict(game='t2', winding='ccw', vertices=[0,0,0, 1,0,0, 0,1,0],
                    normals=[0,0,1]*3, uvs=[0,0, 1,0, 0,1], indices=[0,1,2],
                    material_textures=['disc.png'], material_flags=[4],
                    metadata={'sequences': ['idle'], 'warnings': ['Static preview']})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'t2/model_json').mkdir(parents=True)
            (root/'textures/t2').mkdir(parents=True)
            (root/'t2/model_json/disc.json').write_text(json.dumps(data))
            (root/'t2/catalog.json').write_text(json.dumps([
                dict(model_name='disc', status='ready'),
                dict(model_name='unsupported', status='unsupported', warnings=['No visible detail'])]))
            (root/'textures/disc.png').write_bytes(b'T1')
            (root/'textures/t2/disc.png').write_bytes(b'T2')
            (root/'textures/t2/custom.png').write_bytes(b'custom T2')
            with patch('app.static_dir', root), patch('app.textures_dir', root/'textures'):
                client = app.test_client()
                with client.get('/texture/disc.png') as response:
                    self.assertEqual(response.data, b'T1')
                with client.get('/texture/disc.png?game=t2') as response:
                    self.assertEqual(response.data, b'T2')
                self.assertEqual(client.get('/list_textures?game=t2').json, ['custom.png','disc.png'])
                self.assertEqual(len(client.get('/list_models?game=t2').json), 2)
                self.assertEqual(client.get('/model_json/unsupported?game=t2').status_code, 404)
                query = dict(game='t2', materials=json.dumps({'0':'custom.png'}))
                preview = client.get('/model_json/disc', query_string=query).json
                self.assertEqual(preview['material_textures'], ['custom.png'])
                with contextlib.redirect_stdout(io.StringIO()):
                    response = client.get('/export_obj/disc', query_string=query)
                self.assertEqual(response.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                    self.assertEqual(archive.read('custom.png'), b'custom T2')
                    self.assertNotIn('disc.png', archive.namelist())
                    self.assertIn(b'f 1/1/1 2/2/2 3/3/3', archive.read('disc.obj'))
                    self.assertIn(b'vn 0.000000 0.000000 1.000000', archive.read('disc.obj'))
                    self.assertIn(b'map_d custom.png', archive.read('disc.mtl'))
                    self.assertEqual(json.loads(archive.read('metadata.json'))['sequences'], ['idle'])
                for override in ({'1':'disc.png'}, {'0':'../disc.png'}, {'0':None}, ['disc.png']):
                    for endpoint in ('model_json', 'export_obj'):
                        response = client.get(f'/{endpoint}/disc', query_string=dict(game='t2', materials=json.dumps(override)))
                        self.assertEqual(response.status_code, 422)
                self.assertEqual(client.get('/list_models?game=wrong').status_code, 400)
                with patch('app.socketio.emit') as emit:
                    TextureWatcher().notify_texture(root/'textures/t2/disc.png')
                emit.assert_called_once_with('texture_updated', {'filename':'disc.png','game':'t2'})

    def test_authored_normals_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'shape.json'
            path.write_text(json.dumps(dict(vertices=[0,0,0, 1,0,0, 0,1,0],
                uvs=[0,0]*3, indices=[0,1,2], normals=[float('nan')]*9)))
            with self.assertRaises(ValueError):
                load_model_data(path)


if __name__ == '__main__':
    unittest.main()

"""Run with: python -m unittest discover -s tests -v"""
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from watchdog.events import FileCreatedEvent, FileMovedEvent

from app import app, model_json_dir, textures_dir
from app import TextureWatcher
from tools.model_data import load_model_data
from tools.obj_exporter import compute_smooth_normals, generate_obj_content


class ModelTests(unittest.TestCase):
    def test_larmor_snapshot_matches_corrected_dts_exporter(self):
        from tools.export_model import main as export_model
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            export_model(str(model_json_dir.parents[1] / 'tools/dts_files/larmor.dts'), directory)
            generated = json.loads((pathlib.Path(directory) / 'larmor.json').read_text())
        committed = json.loads((model_json_dir / 'larmor.json').read_text())
        self.assertEqual(committed, generated)
        self.assertEqual(set(committed['material_textures']), {'base.larmor.png'})
        self.assertTrue((textures_dir / 'base.larmor.png').is_file())

    def test_disc_defaults_to_stock_without_removing_custom_skin(self):
        client = app.test_client()
        self.assertEqual(client.get('/model_json/disc').json['material_textures'], ['stock_disc.png'])
        self.assertEqual(client.get('/model_json/disc?texture=disc.png').json['material_textures'], ['disc.png'])
        self.assertTrue((textures_dir / 'stock_disc.png').is_file())
        self.assertTrue((textures_dir / 'disc.png').is_file())

    def test_atomic_texture_saves_notify_the_destination(self):
        watcher = TextureWatcher()
        with patch('app.socketio.emit') as emit:
            watcher.on_created(FileCreatedEvent(str(textures_dir / 'disc.png')))
            watcher.on_moved(FileMovedEvent(str(textures_dir / 'temp.tmp'), str(textures_dir / 'disc.png')))
        self.assertEqual(emit.call_count, 2)
        emit.assert_called_with('texture_updated', {'filename': 'disc.png'})

    def test_every_bundled_model_previews_and_exports(self):
        client = app.test_client()
        models = client.get('/list_models').get_json()
        self.assertEqual(len(models), 10)
        for model in models:
            name = model['model_name']
            with self.subTest(model=name), contextlib.redirect_stdout(io.StringIO()):
                response = client.get('/model_json/' + name)
                self.assertEqual(response.status_code, 200)
                data = response.get_json()
                original = json.loads((model_json_dir / (name + '.json')).read_text())
                self.assertEqual(data['vertices'], original.get('v', original.get('vertices')))
                response = client.get('/export_obj/' + name)
                self.assertEqual(response.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                    obj = archive.read(name + '.obj').decode()
                    self.assertEqual(sum(line.startswith('f ') for line in obj.splitlines()), len(data['indices']) // 3)
                    mtl = archive.read(name + '.mtl').decode()
                    for texture in data['material_textures']:
                        self.assertIn('map_Kd ' + texture, mtl)
                        if (textures_dir / texture).exists():
                            self.assertEqual(archive.read(texture), (textures_dir / texture).read_bytes())

    def test_fallback_skin_matches_preview_and_export(self):
        client = app.test_client()
        url = '/disc?texture=rainbow.png'
        self.assertEqual(client.get('/model_json' + url).json['material_textures'], ['rainbow.png'])
        with contextlib.redirect_stdout(io.StringIO()):
            response = client.get('/export_obj' + url)
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            self.assertIn('rainbow.png', archive.namelist())
            self.assertIn(b'map_Kd rainbow.png', archive.read('disc.mtl'))
        for prefix in ('/model_json', '/export_obj'):
            self.assertEqual(client.get(prefix + '/disc?texture=../secret.png').status_code, 422)
            self.assertEqual(client.get(prefix + '/not_a_model').status_code, 404)

    def test_modern_materials_and_invalid_geometry(self):
        data = dict(vertices=[0, 0, 0, 1, 0, 0, 0, 1, 0], uvs=[0, 0, 1, 0, 0, 1],
                    indices=[0, 1, 2], material_textures=['disc.png'],
                    groups=[dict(start=0, count=3, materialIndex=0)])
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'sample.json'
            path.write_text(json.dumps(data))
            self.assertEqual(load_model_data(path, 'rainbow.png'), data)
            for changes in ({'indices': [0, 1, 9]}, {'uvs': [0, 0]},
                            {'vertices': [float('nan')] * 9},
                            {'groups': [dict(start=0, count=6, materialIndex=0)]}):
                path.write_text(json.dumps(dict(data, **changes)))
                with self.assertRaises(ValueError):
                    load_model_data(path)

    def test_exported_normal_matches_exported_winding(self):
        vertices = [0, 0, 0, 1, 0, 0, 0, 1, 0]
        normals = compute_smooth_normals(vertices, [0, 1, 2])
        obj = generate_obj_content(vertices, [0, 0] * 3, [0, 1, 2], normals, ['disc.png'], [], 'test')
        self.assertIn('f 1/1/1 3/3/3 2/2/2', obj)
        self.assertIn('vn -0.000000 -0.000000 -1.000000', obj)


if __name__ == '__main__':
    unittest.main()

"""Cross-game preview/export must resolve source bytes and retain target semantics."""
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
from tools.model_data import load_model_data
from tools.obj_exporter import json_to_obj_zip
from test_glb_export import read_glb


GAMES = ('t1', 't2', 'q3')
COLORS = {'t1': (210, 20, 30, 64), 't2': (20, 210, 30, 64), 'q3': (20, 30, 210, 64)}


def model(game):
    return dict(game=game, vertices=[0, 0, 0, 1, 0, 0, 0, 1, 0],
                uvs=[0, 0, 1, 0, 0, 1], indices=[0, 1, 2],
                material_textures=['same.png'], material_flags=[4],
                material_settings=[{'alphaFunc': 'GE128', 'clamp': True}])


def embedded_pixel(document, binary, slot):
    material = document['materials'][slot]
    texture = document['textures'][material['pbrMetallicRoughness']['baseColorTexture']['index']]
    image = document['images'][texture['source']]
    view = document['bufferViews'][image['bufferView']]
    start = view['byteOffset']
    with Image.open(io.BytesIO(binary[start:start + view['byteLength']])) as png:
        return png.getpixel((0, 0))


class CrossGameTests(unittest.TestCase):
    def test_every_source_and_target_preview_obj_and_animated_glb(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            directories = {'t1': root / 'textures', 't2': root / 'textures/t2', 'q3': root / 'q3/textures'}
            for game, path in directories.items():
                path.mkdir(parents=True, exist_ok=True)
                Image.new('RGBA', (1, 1), COLORS[game]).save(path / 'same.png')
            path = root / 'sample.json'
            stack.enter_context(patch('app.model_path', return_value=path))
            stack.enter_context(patch('app.textures_dir', directories['t1']))
            stack.enter_context(patch('app.q3_dir', root / 'q3'))
            stack.enter_context(patch('app.local_data_dir', root))
            stack.enter_context(patch('tools.animate_t2.load_animated_model',
                                     side_effect=lambda name, source, preview: dict(preview, animation_clips=clips)))
            client = app.test_client()
            for target in GAMES:
                data = model(target)
                clips = [{'name': 'idle', 'fps': 1, 'frames': [{'vertices': data['vertices']}]}]
                path.write_text(json.dumps(data))
                # Cached animated geometry includes stale source slots on purpose.
                cache = root / 'animations' / target / 'sample.json.gz'
                cache.parent.mkdir(parents=True)
                with gzip.open(cache, 'wt', encoding='utf-8') as stream:
                    json.dump(dict(data, material_texture_games=[target], animation_clips=clips), stream)
                for source in GAMES:
                    with self.subTest(target=target, source=source):
                        query = dict(game=target, materials=json.dumps({'0': {'game': source, 'filename': 'same.png'}}))
                        preview = client.get('/model_json/sample', query_string=query)
                        self.assertEqual(preview.status_code, 200)
                        self.assertEqual(preview.json['material_textures'], ['same.png'])
                        self.assertEqual(preview.json['material_texture_games'], [source])
                        self.assertEqual(preview.json['material_flags'], data['material_flags'])
                        self.assertEqual(preview.json['material_settings'], data['material_settings'])
                        with client.get('/texture/same.png', query_string=dict(game=source)) as response:
                            self.assertEqual(response.data, (directories[source] / 'same.png').read_bytes())
                        with contextlib.redirect_stdout(io.StringIO()):
                            response = client.get('/export_obj/sample', query_string=query)
                        self.assertEqual(response.status_code, 200)
                        export_name = 'same.png' if source == target else f'textures/{source}/same.png'
                        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                            self.assertEqual(archive.read(export_name), (directories[source] / 'same.png').read_bytes())
                            self.assertIn(export_name, archive.read('sample.mtl').decode())
                            if source != target or target == 'q3':
                                metadata = json.loads(archive.read('metadata.json'))
                                self.assertEqual(metadata['material_texture_games'], [source])
                                self.assertEqual(metadata['material_texture_paths'], [export_name])
                            if target == 'q3':
                                opacity = metadata['opacity_maps']['0']
                                with Image.open(io.BytesIO(archive.read(opacity))) as png:
                                    self.assertEqual(png.getpixel((0, 0)), 0)
                        response = client.get('/export_glb/sample', query_string=query)
                        self.assertEqual(response.status_code, 200, response.get_data(as_text=True) if response.status_code != 200 else '')
                        document, binary = read_glb(response.data)
                        self.assertEqual(embedded_pixel(document, binary, 0), COLORS[source])
                        self.assertEqual(document['materials'][0]['extras']['sourceGame'], source)
                        self.assertEqual(document['animations'][0]['name'], 'idle')
                        expected_alpha = {'t1': 'OPAQUE', 't2': 'BLEND', 'q3': 'MASK'}[target]
                        self.assertEqual(document['materials'][0].get('alphaMode', 'OPAQUE'), expected_alpha)

    def test_same_filename_from_three_games_is_not_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            directories = {game: root / game for game in GAMES}
            for game, path in directories.items():
                path.mkdir()
                Image.new('RGBA', (1, 1), COLORS[game]).save(path / 'same.png')
            data = dict(model('t1'), material_textures=['same.png'] * 3, material_flags=[0] * 3,
                        material_texture_games=list(GAMES), indices=[0, 1, 2] * 3,
                        groups=[dict(start=slot * 3, count=3, materialIndex=slot) for slot in range(3)])
            path = root / 'sample.json'
            path.write_text(json.dumps(data))
            output = io.BytesIO()
            with contextlib.redirect_stdout(io.StringIO()):
                json_to_obj_zip(path, directories['t1'], output, 'sample', texture_dirs=directories)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
                for game in GAMES:
                    self.assertEqual(archive.read(f'textures/{game}/same.png'), (directories[game] / 'same.png').read_bytes())
            document, binary = read_glb(model_to_glb(data, directories['t1'], texture_dirs=directories))
            self.assertEqual(len(document['images']), 3)
            for slot, game in enumerate(GAMES):
                self.assertEqual(embedded_pixel(document, binary, slot), COLORS[game])
            # A direct exporter caller must supply cross-game roots, never silently use T1 bytes.
            with self.assertRaises(ValueError):
                model_to_glb(data, directories['t1'])

    def test_invalid_refs_rejected_in_preview_and_both_exports(self):
        invalid = [{'game': game, 'filename': 'same.png'} for game in ('wrong', '', None, [], {})]
        invalid += [{'game': 't2', 'filename': name} for name in
                    ('../secret.png', '..\\secret.png', 'C:secret.png', '/secret.png', '[Slot/secret]',
                     'a\0.png', 'a\n.png', 'a\t.png', '.', '..', 'a.png ', 'a.png.', '', None, [], {})]
        invalid += [{'game': 't2'}, {'filename': 'same.png'}, {'game': 't2', 'filename': 'same.png', 'path': 'secret'}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.json'
            path.write_text(json.dumps(model('t1')))
            with patch('app.model_path', return_value=path):
                client = app.test_client()
                for reference in invalid:
                    for endpoint in ('model_json', 'export_obj', 'export_glb'):
                        with self.subTest(reference=reference, endpoint=endpoint):
                            response = client.get(f'/{endpoint}/sample', query_string=dict(materials=json.dumps({'0': reference})))
                            self.assertEqual(response.status_code, 422)
                self.assertEqual(load_model_data(path, material_overrides={'0': 'legacy.png'})['material_texture_games'], ['t1'])
            for games in ([], ['t2', 'q3'], ['bad'], [None]):
                with self.subTest(games=games), self.assertRaises(ValueError):
                    model_to_glb(dict(model('t1'), material_texture_games=games), directory)


if __name__ == '__main__':
    unittest.main()

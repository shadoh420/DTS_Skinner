"""Read GLB independently and evaluate its exported vertex animation channels."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from PIL import Image

from tools.glb_exporter import model_to_glb
from tools.obj_exporter import json_to_obj_zip


def read_glb(raw):
    magic, version, length = struct.unpack_from('<4sII', raw)
    assert (magic, version, length) == (b'glTF', 2, len(raw))
    json_size, kind = struct.unpack_from('<I4s', raw, 12)
    assert kind == b'JSON'
    document = json.loads(raw[20:20 + json_size])
    binary_size, kind = struct.unpack_from('<I4s', raw, 20 + json_size)
    assert kind == b'BIN\0'
    binary = raw[28 + json_size:]
    assert len(binary) == binary_size
    assert document['buffers'][0]['byteLength'] <= len(binary)
    for view in document['bufferViews']:
        assert view.get('byteOffset', 0) % 4 == 0
        assert view.get('byteOffset', 0) + view['byteLength'] <= len(binary)
    return document, binary


def read_accessor(document, binary, index):
    entry = document['accessors'][index]
    width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3}[entry['type']]
    codes = {5126: 'f', 5125: 'I', 5123: 'H', 5121: 'B'}

    def read_view(view_index, component, count, offset=0):
        view = document['bufferViews'][view_index]
        return list(struct.unpack_from('<' + str(count) + codes[component], binary, view.get('byteOffset', 0) + offset))

    result = [0.0] * (entry['count'] * width)
    if 'bufferView' in entry:
        result = read_view(entry['bufferView'], entry['componentType'], len(result), entry.get('byteOffset', 0))
    if 'sparse' in entry:
        sparse = entry['sparse']
        indices = read_view(sparse['indices']['bufferView'], sparse['indices']['componentType'], sparse['count'])
        assert indices == sorted(set(indices))
        values = read_view(sparse['values']['bufferView'], entry['componentType'], sparse['count'] * width)
        for i, target in enumerate(indices):
            assert 0 <= target < entry['count']
            result[target * width:(target + 1) * width] = values[i * width:(i + 1) * width]
    return result


def animated_vertices(document, binary, animation_index, time):
    channel = document['animations'][animation_index]['channels'][0]
    mesh_index = document['nodes'][channel['target']['node']]['mesh']
    primitive = document['meshes'][mesh_index]['primitives'][0]
    result = read_accessor(document, binary, primitive['attributes']['POSITION'])
    sampler = document['animations'][animation_index]['samplers'][0]
    times = read_accessor(document, binary, sampler['input'])
    outputs = read_accessor(document, binary, sampler['output'])
    count = len(primitive['targets'])
    lower = max((i for i, value in enumerate(times) if value <= time), default=0)
    upper = min(lower + 1, len(times) - 1)
    fraction = 0 if lower == upper or sampler['interpolation'] == 'STEP' else (time - times[lower]) / (times[upper] - times[lower])
    weights = [(1 - fraction) * outputs[lower * count + i] + fraction * outputs[upper * count + i] for i in range(count)]
    for target, weight in zip(primitive['targets'], weights):
        delta = read_accessor(document, binary, target['POSITION'])
        result = [value + weight * change for value, change in zip(result, delta)]
    return result


class GlbExportTests(unittest.TestCase):
    def model(self):
        return {'vertices': [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0],
                'uvs': [0, 0, 1, 0, 1, 1, 0, 1], 'indices': [0, 1, 2, 0, 2, 3],
                'groups': [{'start': 0, 'count': 3, 'materialIndex': 0}, {'start': 3, 'count': 3, 'materialIndex': 1}],
                'material_textures': ['skin.png', 'missing.png'], 'winding': 'ccw'}

    def q3_model(self):
        model = self.model()
        model.update(game='q3', material_textures=['skin.png'] * 5,
                     material_settings=[{'shader': 'cutout', 'alphaFunc': 'GE128', 'cull': 'none', 'clamp': True},
                                        {'shader': 'inverse', 'alphaFunc': 'LT128', 'cull': 'back'},
                                        {'shader': 'nonzero', 'alphaFunc': 'GT0', 'cull': 'front'},
                                        {'shader': 'blend', 'blend': ['gl_src_alpha', 'gl_one_minus_src_alpha'], 'tcGen': 'environment', 'depthWrite': False},
                                        {'shader': 'opaque'}])
        return model

    def test_q3_alpha_test_blend_sampling_and_slot_local_inversion(self):
        data = self.q3_model()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'skin.png'
            texture = Image.new('RGBA', (5, 1))
            texture.putdata([(23, 45, 67, alpha) for alpha in [0, 1, 127, 128, 255]])
            texture.save(source)
            original = source.read_bytes()
            document, binary = read_glb(model_to_glb(data, directory))
            self.assertEqual(source.read_bytes(), original)
        materials = document['materials']
        self.assertEqual([materials[i]['alphaMode'] for i in range(4)], ['MASK', 'MASK', 'MASK', 'BLEND'])
        self.assertEqual(materials[0]['alphaCutoff'], .5)
        self.assertEqual(materials[1]['alphaCutoff'], .5)
        self.assertEqual(materials[2]['alphaCutoff'], .5 / 255)
        self.assertNotIn('alphaMode', materials[4])
        self.assertTrue(materials[0]['doubleSided'])
        self.assertFalse(materials[1]['doubleSided'])
        self.assertEqual(len(document['images']), 2)
        for slot, expected in [(0, [0, 1, 127, 128, 255]), (1, [255, 254, 128, 127, 0]), (4, [0, 1, 127, 128, 255])]:
            tex = document['textures'][materials[slot]['pbrMetallicRoughness']['baseColorTexture']['index']]
            image_view = document['bufferViews'][document['images'][tex['source']]['bufferView']]
            start = image_view['byteOffset']
            with Image.open(io.BytesIO(binary[start:start + image_view['byteLength']])) as image:
                self.assertEqual(list(image.getchannel('A').tobytes()), expected)
                self.assertEqual(image.getpixel((0, 0))[:3], (23, 45, 67))
            sampler = document['samplers'][tex['sampler']]
            self.assertEqual((sampler['magFilter'], sampler['minFilter']), (9729, 9987))
            self.assertEqual(sampler['wrapS'], 33071 if slot == 0 else 10497)
        self.assertTrue(any('front-face culling' in note for note in document['extras']['warnings']))
        self.assertTrue(any('environment' in note for note in document['extras']['warnings']))

    def test_q3_obj_opacity_is_grayscale_alpha_and_preserves_overrides(self):
        data = self.q3_model()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, override = directory / 'skin.png', directory / 'override.png'
            texture = Image.new('RGBA', (5, 1))
            texture.putdata([(23, 45, 67, alpha) for alpha in [0, 1, 127, 128, 255]])
            texture.save(source)
            texture.putalpha(Image.frombytes('L', (5, 1), bytes([255, 128, 127, 1, 0])))
            texture.save(override)
            original, overridden = source.read_bytes(), override.read_bytes()
            model_file = directory / 'sample.json'
            model_file.write_text(json.dumps(data), encoding='utf-8')
            output = io.BytesIO()
            json_to_obj_zip(model_file, directory, output, 'sample', material_overrides={'0': 'override.png'})
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(override.read_bytes(), overridden)
            with zipfile.ZipFile(output) as archive:
                metadata = json.loads(archive.read('metadata.json'))
                self.assertEqual(metadata['material_settings'], data['material_settings'])
                self.assertEqual(metadata['material_textures'][0], 'override.png')
                self.assertEqual(archive.read('skin.png'), original)
                self.assertEqual(archive.read('override.png'), overridden)
                mtl = archive.read('sample.mtl').decode()
                self.assertIn('map_Kd -clamp on override.png', mtl)
                expected = [[255, 255, 0, 0, 0], [255, 255, 255, 0, 0], [0, 255, 255, 255, 255], [0, 1, 127, 128, 255]]
                for slot, values in enumerate(expected):
                    name = metadata['opacity_maps'][str(slot)]
                    self.assertIn('map_d ' + ('-clamp on ' if slot == 0 else '') + name, mtl)
                    with Image.open(io.BytesIO(archive.read(name))) as opacity:
                        self.assertEqual(opacity.mode, 'L')
                        self.assertEqual(list(opacity.tobytes()), values)
                self.assertNotIn('4', metadata['opacity_maps'])

    def test_embedded_texture_groups_and_static_winding(self):
        data = self.model()
        with tempfile.TemporaryDirectory() as directory:
            texture = Image.new('RGBA', (2, 2))
            texture.putdata([(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)])
            texture.save(Path(directory) / 'skin.png')
            for winding in ('ccw', 'legacy'):
                data['winding'] = winding
                document, binary = read_glb(model_to_glb(data, directory))
                self.assertNotIn('animations', document)
                primitives = document['meshes'][0]['primitives']
                self.assertEqual(primitives[0]['attributes'], primitives[1]['attributes'])
                self.assertEqual([p['material'] for p in primitives], [0, 1])
                self.assertEqual(read_accessor(document, binary, primitives[0]['indices']), [0, 1, 2] if winding == 'ccw' else [0, 2, 1])
                self.assertEqual(read_accessor(document, binary, primitives[0]['attributes']['NORMAL'])[2], 1 if winding == 'ccw' else -1)
                self.assertEqual(read_accessor(document, binary, primitives[0]['attributes']['TEXCOORD_0']), data['uvs'])
                self.assertEqual(document['extras']['missingTextures'], ['missing.png'])
                image_view = document['bufferViews'][document['images'][0]['bufferView']]
                offset = image_view['byteOffset']
                with Image.open(io.BytesIO(binary[offset:offset + image_view['byteLength']])) as embedded:
                    self.assertEqual(embedded.tobytes(), texture.tobytes())

    def test_sparse_shared_targets_interpolate_and_loop_at_source_times(self):
        data = self.model()
        moved = [value + (2 if index % 3 == 2 else 0) for index, value in enumerate(data['vertices'])]
        frames = [{'vertices': data['vertices']}, {'vertices': moved}]
        data['animation_clips'] = [
            {'name': 'rise', 'fps': 20, 'times': [0, 0.4], 'duration': 0.8, 'frames': frames, 'loop': True},
            {'name': 'hold', 'fps': 2, 'frames': list(reversed(frames)), 'interpolation': 'STEP'}]
        with tempfile.TemporaryDirectory() as directory:
            document, binary = read_glb(model_to_glb(data, directory))
        primitives = document['meshes'][0]['primitives']
        self.assertEqual(len(primitives[0]['targets']), 1)
        self.assertEqual(primitives[0]['targets'], primitives[1]['targets'])
        self.assertEqual([a['name'] for a in document['animations']], ['rise', 'hold'])
        self.assertTrue(document['animations'][0]['extras']['loop'])
        halfway = animated_vertices(document, binary, 0, 0.2)
        for index, value in enumerate(halfway):
            self.assertAlmostEqual(value, data['vertices'][index] + (1 if index % 3 == 2 else 0), places=6)
        for actual, expected in zip(animated_vertices(document, binary, 0, 0.8), data['vertices']):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(animated_vertices(document, binary, 1, 0.25), moved)

    def test_constant_clip_and_torque_opaque_reflectivity_alpha(self):
        data = self.model()
        data.update(game='t2', material_flags=[0, 8], animation_clips=[{'name': 'still', 'fps': 1, 'frames': [{'vertices': data['vertices']}]}])
        with tempfile.TemporaryDirectory() as directory:
            document, binary = read_glb(model_to_glb(data, directory))
        self.assertNotIn('alphaMode', document['materials'][0])
        self.assertEqual(document['materials'][1]['alphaMode'], 'BLEND')
        self.assertIn('approximated', document['extras']['warnings'][0])
        self.assertEqual(animated_vertices(document, binary, 0, 0), data['vertices'])

    def test_large_animation_set_banks_clips_without_duplicating_geometry(self):
        data = self.model()
        data['animation_clips'] = []
        for clip_index in range(3):
            frames = []
            for frame_index in range(350):
                offset = 1 + clip_index * 350 + frame_index
                frames.append({'vertices': [x + (offset if index % 3 == 2 else 0) for index, x in enumerate(data['vertices'])]})
            data['animation_clips'].append({'name': str(clip_index), 'frames': frames, 'fps': 10})
        with tempfile.TemporaryDirectory() as directory:
            document, binary = read_glb(model_to_glb(data, directory))
        self.assertEqual(len(document['meshes']), 4)
        self.assertNotIn('targets', document['meshes'][0]['primitives'][0])
        self.assertEqual(document['nodes'][1]['scale'], [0, 0, 0])
        baseline = document['meshes'][0]['primitives'][0]
        for index in range(3):
            primitive = document['meshes'][index + 1]['primitives'][0]
            self.assertEqual(len(primitive['targets']), 350)
            self.assertEqual(primitive['attributes'], baseline['attributes'])
            self.assertEqual(document['accessors'][primitive['indices']], document['accessors'][baseline['indices']])
            self.assertEqual(len(document['animations'][index]['channels']), 3)
            actual = animated_vertices(document, binary, index, 0.05)
            self.assertAlmostEqual(actual[2], 1 + index * 350 + 0.5, places=5)

    def test_reject_invalid_geometry_animation_and_texture_paths(self):
        invalid = []
        for field, value in [('vertices', [float('nan')] * 12), ('indices', [0, 1, 50]),
                             ('uvs', [0, 0]), ('normals', [0, 0]), ('material_textures', ['../secret.png']),
                             ('groups', [{'start': 0, 'count': 3, 'materialIndex': 0}])]:
            model = self.model()
            model[field] = value
            invalid.append(model)
        for clip in [{'fps': 0, 'frames': [{'vertices': self.model()['vertices']}]},
                     {'fps': 1, 'frames': [{'vertices': [0, 1, 2]}]},
                     {'fps': 1, 'times': [0, 0], 'frames': [{'vertices': self.model()['vertices']}] * 2},
                     {'fps': 1, 'times': [1, 1 + 1e-12], 'frames': [{'vertices': self.model()['vertices']}] * 2},
                     {'fps': 1, 'frames': []}]:
            model = self.model()
            model['animation_clips'] = [clip]
            invalid.append(model)
        with tempfile.TemporaryDirectory() as directory:
            for model in invalid:
                with self.subTest(model=model), self.assertRaises(ValueError):
                    model_to_glb(model, directory)


if __name__ == '__main__':
    unittest.main()

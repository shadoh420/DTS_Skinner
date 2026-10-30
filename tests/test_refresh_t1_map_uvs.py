import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_add_t1_buildings import interior, png
from tools.import_t1_map import export_mounted_interior
from tools.refresh_t1_map_uvs import prepare, activate


class MapUVRefreshTests(unittest.TestCase):
    def test_source_matched_stage_preserves_metadata_and_refuses_stale_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache, stage, source = root / 'cache', root / 'stage', root / 'source'
            source.mkdir()
            members = interior('house', 'wall.png')
            for name, raw in members.items():
                (source / name).write_bytes(raw)
            (cache / 'textures').mkdir(parents=True)
            (cache / 'textures/wall.png').write_bytes(png('red'))
            model = export_mounted_interior('house', members['house.dis'], members.__getitem__, lambda _: png('red'))
            model.pop('interior_uv_version')
            model['uvs'] = [-.5, -.5, -1.5, -.5, -.5, -1.5]
            old = json.dumps(model).encode()
            name = 'house-' + hashlib.sha1(old).hexdigest()[:12] + '.json'
            (cache / 'models').mkdir()
            (cache / 'models' / name).write_bytes(old)
            path = cache / 'maps/example/scene.json'
            path.parent.mkdir(parents=True)
            scene = dict(objects=[dict(model=name, source='pack', classname='interiorshape', matrix=list(range(16)), light={'uv': 'existing.uv'})], zones=['edited'], section='RPG missions')
            original = json.dumps(scene, indent=3).encode()
            path.write_bytes(original)
            second = cache / 'maps/zsecond/scene.json'
            second.parent.mkdir(parents=True)
            second.write_bytes(original)
            (path.parent / 'collision.json').write_text('keep collision')
            report = prepare(cache, stage, [source])
            self.assertEqual(path.read_bytes(), original)
            row = report['models'][0]
            self.assertEqual(row['status'], 'refreshed')
            fixed = json.loads((stage / 'models' / row['replacement']).read_text())
            self.assertEqual(fixed['uvs'], [0., 0., 1., 0., 0., 1.])
            self.assertEqual({k:v for k,v in fixed.items() if k not in ('uvs', 'interior_uv_version')}, {k:v for k,v in model.items() if k != 'uvs'})
            path.write_bytes(original + b' ')
            with self.assertRaisesRegex(ValueError, 'Changed since staging'):
                activate(cache, stage)
            self.assertFalse((cache / 'models' / row['replacement']).exists())
            path.write_bytes(original)
            replace = Path.replace
            def fail_second_scene(temporary, target):
                # The Windows runner's TEMP may use an 8.3 alias; activation resolves it.
                if target == second.resolve():
                    raise OSError('Simulated scene write failure')
                return replace(temporary, target)
            with patch.object(Path, 'replace', fail_second_scene):
                with self.assertRaisesRegex(OSError, 'Simulated scene'):
                    activate(cache, stage)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(second.read_bytes(), original)
            self.assertEqual(activate(cache, stage), 2)
            after = json.loads(path.read_text())
            after['objects'][0]['model'] = name
            self.assertEqual(after, scene)
            self.assertEqual((path.parent / 'collision.json').read_text(), 'keep collision')
            self.assertEqual(activate(cache, stage, rollback=True), 2)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(second.read_bytes(), original)

    def test_existing_missing_texture_is_preserved_with_native_fallback_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache, stage, source = root / 'cache', root / 'stage', root / 'source'
            source.mkdir()
            members = interior('house', 'wall.png')
            for name, raw in members.items():
                (source / name).write_bytes(raw)
            model = export_mounted_interior('house', members['house.dis'], members.__getitem__, lambda _: None)
            model['material_textures'] = ['']  # The map cache records unavailable PNGs this way.
            model.pop('interior_uv_version')
            model['uvs'] = [-1/256, -1/256, -3/256, -1/256, -1/256, -3/256]
            (cache / 'models').mkdir(parents=True)
            (cache / 'models/house-legacy.json').write_text(json.dumps(model))
            path = cache / 'maps/example/scene.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(dict(objects=[dict(model='house-legacy.json', source='pack', classname='interiorshape')])))
            row = prepare(cache, stage, [source])['models'][0]
            self.assertEqual(row['status'], 'refreshed')
            fixed = json.loads((stage / 'models' / row['replacement']).read_text())
            self.assertEqual(fixed['material_textures'], [''])
            self.assertEqual(fixed['uvs'], [0., 0., 2/256, 0., 0., 2/256])

    def test_missing_interior_source_is_named_and_blocks_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache, stage = root / 'cache', root / 'stage'
            (cache / 'models').mkdir(parents=True)
            (cache / 'models/missing-123.json').write_text('{}')
            path = cache / 'maps/affected/scene.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(dict(objects=[dict(model='missing-123.json', source='pack', classname='interiorshape')])))
            report = prepare(cache, stage, [])
            self.assertEqual(report['models'][0]['maps'], ['affected'])
            self.assertEqual(report['models'][0]['status'], 'unresolved')
            with self.assertRaisesRegex(ValueError, 'Resolve referenced'):
                activate(cache, stage)

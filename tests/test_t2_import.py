"""Small numerical checks for the coordinate/primitive seam (no external kit)."""
import json
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from tools.import_t2 import Textures, geometry, node_matrices, posed_vertices, triangles


class T2ImportTests(unittest.TestCase):
    def test_authored_texture_path_wins_over_same_basename_skin(self):
        buffer = io.BytesIO()
        Image.new('RGBA', (1, 1), (40, 80, 120, 255)).save(buffer, format='PNG')
        raw = buffer.getvalue()
        assets = {key: {'source': key, 'alternatives': [], 'read': lambda: raw}
                  for key in ('textures/skins/wall.png', 'textures/interiors/wall.png', 'textures/other/wall.jpg')}
        with tempfile.TemporaryDirectory() as directory:
            resolver = Textures(assets, Path(directory))
            for requested in ('interiors/wall', 'textures/interiors/wall.png'):
                filename, evidence = resolver.resolve(requested)
                self.assertIsNotNone(filename)
                self.assertEqual(evidence['virtual_path'], 'textures/interiors/wall.png')
            _, evidence = resolver.resolve('textures/other/wall.jpg')
            self.assertEqual(evidence['virtual_path'], 'textures/other/wall.jpg')
            _, evidence = resolver.resolve('other/wall')
            self.assertEqual(evidence['virtual_path'], 'textures/other/wall.jpg')

    def test_torque_quaternion_and_non_topological_parent(self):
        # Stored conjugate: -90 degrees around Z encodes +90 in Cartesian space.
        shape = {'nodes': [(0, 1), (1, -1)],
                 'defaultRotations': [(0, 0, 0, 32767), (0, 0, -23170, 23170)],
                 'defaultTranslations': [(2, 0, 0), (10, 0, 0)]}
        worlds = node_matrices(shape)
        np.testing.assert_allclose(worlds[0][:3, 3], [10, 2, 0], atol=1e-6)
        source = {'type': 0, 'verts': [1, 0, 0], 'norms': [1, 0, 0]}
        points, normals = posed_vertices(source, 0, worlds)
        np.testing.assert_allclose(points[0], [10, 0, -3], atol=1e-6)
        np.testing.assert_allclose(normals[0], [0, 0, -1], atol=1e-6)

    def test_weighted_inverse_bind_does_not_double_transform(self):
        first, second = np.eye(4), np.eye(4)
        first[0, 3], second[0, 3] = 2, 6
        bind = np.eye(4)
        bind[0, 3] = -2
        source = {'type': 1, 'verts': [99, 99, 99], 'initialVerts': [1, 0, 0],
                  'norms': [1, 0, 0], 'initialNormals': [1, 0, 0],
                  'initialTransforms': list(bind.flat) * 2, 'nodeIndex': [0, 1],
                  'vertexIndex': [0, 0], 'boneIndex': [0, 1], 'weight': [.25, .75]}
        points, normals = posed_vertices(source, 1, [first, second])
        np.testing.assert_allclose(points[0], [4, 0, 0])
        np.testing.assert_allclose(normals[0], [1, 0, 0])

    def test_primitive_parity_and_authored_no_material(self):
        strip = {'indices': [0, 1, 2, 3], 'prims': [(0, 4, 0x60000005)]}
        self.assertEqual(list(triangles(strip)), [(0, 2, 1, 5), (2, 3, 1, 5)])
        fan = {'indices': [], 'prims': [(4, 4, 0x90000000)]}
        self.assertEqual(list(triangles(fan)), [(4, 6, 5, -1), (4, 7, 6, -1)])

    def test_zero_size_render_detail_is_not_collision(self):
        mesh = {'type': 0, 'verts': [0, 0, 0, 1, 0, 0, 0, 1, 0],
                'norms': [0, 0, -1] * 3, 'tverts': [0, 0, 1, 0, 0, 1],
                'prims': [(0, 3, 0x20000000)], 'indices': [0, 1, 2]}
        shape = {'nodes': [(0, -1)], 'names': ['root', 'Detail0', 'Collision-1'],
                 'defaultRotations': [(0, 0, 0, 32767)], 'defaultTranslations': [(0, 0, 0)],
                 'details': [{'name': 1, 'subShapeNum': 0, 'objectDetailNum': 0, 'size': 0},
                             {'name': 2, 'subShapeNum': 0, 'objectDetailNum': 1, 'size': -1}],
                 'objects': [(0, 2, 0, 0)], 'meshes': [mesh, mesh],
                 'subShapeFirstObject': [0], 'subShapeNumObjects': [1]}
        data, detail, _, _ = geometry(shape)
        self.assertEqual(detail, 0)
        self.assertEqual(len(data['indices']), 3)

    def test_generated_catalog_accounts_for_inventory(self):
        root = Path(__file__).resolve().parents[1]
        catalog = json.loads((root / 'static/t2/catalog.json').read_text())
        names = (root / 't2_catalog.txt').read_text().splitlines()
        self.assertEqual([r['model_name'] for r in catalog], names)
        from tools.model_data import model_sort_key
        self.assertEqual(names, sorted(names, key=model_sort_key))
        self.assertEqual(len(names), len(set(names)))
        report = json.loads((root / 'static/t2/inventory.json').read_text())
        self.assertEqual(len(catalog), report['source_model_count'] + report.get('source_dif_count', 0))
        for record in catalog:
            self.assertIn(record['status'], ('ready', 'unsupported', 'failed'))
            if record['status'] != 'ready':
                self.assertTrue(record['warnings'])
            else:
                path = root / 'static/t2/model_json' / (record['model_name'] + '.json')
                self.assertTrue(path.is_file(), record['model_name'])

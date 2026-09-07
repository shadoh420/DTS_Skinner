"""DIF strip topology, signed planes and texgen UVs, without the external kit."""
import types
import unittest

from tools.import_t2_dif import geometry


class DifGeometryTests(unittest.TestCase):
    def test_strip_keeps_plane_orientation_uvs_and_shared_vertices(self):
        detail = types.SimpleNamespace(
            detail_level=0, version=0, min_pixels=0, null_surface_count=1, lightmap_count=1,
            points=[(0, 0, 0), (0, 1, 0), (1, 0, 0), (1, 1, 0)],
            normals=[(0, 0, 1)], planes=[(0, 0)], materials=['brick'], windings=[0, 1, 2, 3],
            surfaces=[dict(textureIndex=0, planeIndex=0, planeFlipped=True,
                           windingStart=0, windingCount=4, texGenIndex=0)],
            surface_uv=lambda tg, p: (p[0] * 2 + .25, p[1] * 3 + .5))
        reader = types.SimpleNamespace(parse_dif=lambda raw: types.SimpleNamespace(
            details=[detail], file_version=44, tail_error=None, tail=b''))
        mesh, metadata = geometry(b'', reader)
        self.assertEqual(len(mesh['vertices']), 12)
        self.assertEqual(len(mesh['indices']), 6)
        self.assertEqual(metadata['null_surfaces_excluded'], 1)
        self.assertEqual(mesh['material_flags'], [3])
        points = [mesh['vertices'][i:i + 3] for i in range(0, 12, 3)]
        for i, point in enumerate(points):
            self.assertEqual(mesh['uvs'][2 * i:2 * i + 2], [point[0] * 2 + .25, -point[2] * 3 + .5])
        for i in range(0, 6, 3):
            p, q, r = [points[j] for j in mesh['indices'][i:i + 3]]
            # The authored flipped +Z plane becomes -Y after the proper rotation.
            cross_y = (q[2] - p[2]) * (r[0] - p[0]) - (q[0] - p[0]) * (r[2] - p[2])
            self.assertLess(cross_y, 0)
        self.assertEqual(mesh['normals'], [0, -1, 0] * 4)

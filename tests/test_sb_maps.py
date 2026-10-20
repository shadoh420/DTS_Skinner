"""Spellbreak map logic on invented data; one real-install smoke test when the client is extracted."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from tools import import_sb_map as sbmap
from tools.import_sb_map import import_maps, instance_matrices, landscape, tile_offset, to_viewer
from tools.tvg_map import viewer
from tools.ue4_package import Reader

INSTALL = Path(r'C:\Games\Spellbreak\client')


class FakePackage:
    def __init__(self, exports, header=b'', tile=0):
        self.exports = [dict(class_=c, name=n, outer=0, template=0, props=p) for c, n, p in exports]
        for e in self.exports:
            e['class'] = e.pop('class_')
        self.header, self.names, self.tile_info_offset = header, ['None'], tile

    def properties(self, ref):
        return self.exports[ref-1]['props'], None

    def qualified(self, ref):
        return '/Game/Map.Map:'+self.exports[ref-1]['name']


class FakeLibrary:
    def __init__(self, objects):
        self.objects = objects

    def object(self, source, classes=None):
        return self.objects[source]

    def state(self, source):
        return {'LayerName': source.split('.')[-1]}, 'LandscapeLayerInfoObject'


class FakeMaterials:
    def __init__(self, directory):
        self.directory = Path(directory)

    def chain(self, source):
        return []


class SpellbreakMaps(unittest.TestCase):
    def test_instance_data_follows_lod_data(self):
        rows = [np.eye(4), np.eye(4)]
        rows[1][3, :3] = (10, 20, 30)  # FMatrix rows: translation in the last row
        raw = struct.pack('<iiii', 0, 1, 64, 2)+b''.join(m.astype('<f4').tobytes() for m in rows)
        matrices = instance_matrices(Reader(raw))
        self.assertEqual(matrices.shape, (2, 4, 4))
        self.assertEqual(matrices[1][:3, 3].tolist(), [10, 20, 30])

    def test_tile_offset_from_summary(self):
        header = b'\0'*8+struct.pack('<3i', -47244, 23622, 0)
        self.assertEqual(tile_offset(FakePackage([], header, 8)).tolist(), [-47244, 23622, 0])
        self.assertEqual(tile_offset(FakePackage([])).tolist(), [0, 0, 0])

    def test_viewer_matrix_matches_point_basis(self):
        native = np.eye(4); native[:3, 3] = (100, 200, 300)
        m = np.asarray(to_viewer(native)).reshape(4, 4).T
        self.assertTrue(np.allclose(m[:3, 3], viewer(np.array((100., 200, 300)))))

    def test_landscape_stitches_components_and_maps_heights(self):
        # Two 2-quad components with two 1-quad subsections: 3 vertices from 4 texels (texel 1 and 2 repeat).
        def heightmap(base):
            image = np.zeros((4, 4, 4), np.uint8)
            for y in range(4):
                for x in range(4):
                    h = base+(min(x, 3)+10*y)
                    image[y, x] = (0, h & 255, h >> 8, 255)
            return image
        images = {'/Game/Map.H0': heightmap(32768), '/Game/Map.H1': heightmap(32768+128)}
        weight = np.zeros((4, 4, 4), np.uint8); weight[..., 2] = 255  # red channel = layer 0 fully painted
        images['/Game/Map.W'] = weight
        comps = [('LandscapeComponent', 'C0', dict(SectionBaseX=0, SectionBaseY=0, ComponentSizeQuads=2, SubsectionSizeQuads=1, NumSubsections=2,
                                                    HeightmapTexture='/Game/Map.H0', WeightmapTextures=['/Game/Map.W'],
                                                    WeightmapLayerAllocations=[dict(LayerInfo='/Game/L.Grass', WeightmapTextureIndex=0, WeightmapTextureChannel=0)])),
                 ('LandscapeComponent', 'C1', dict(SectionBaseX=2, ComponentSizeQuads=2, SubsectionSizeQuads=1, NumSubsections=2, HeightmapTexture='/Game/Map.H1')),
                 ('Landscape', 'Land', dict(RootComponent='/Game/Map.Root'))]
        p = FakePackage(comps)
        library = FakeLibrary({'/Game/Map.Root': (FakePackage([('SceneComponent', 'Root', dict(RelativeScale3D=[50, 50, 128], RelativeLocation=[1000, 0, 0]))]), 1),
                               **{k: (k, 0) for k in images}})
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(sbmap, 'texture_mips', lambda key, _: ({}, 'PF_B8G8R8A8', [(4, 4, images[key].tobytes())])):
            chunks = []
            row = landscape(p, library, FakeMaterials(tmp), chunks)
            self.assertEqual((row['width'], row['height']), (5, 3))
            heights = np.frombuffer(b''.join(chunks)[row['offset']:row['offset']+row['bytes']], '<u2').reshape(3, 5)
            # Shared subsection edges are stored twice; vertex 1 reads subsection 1's copy (texel 2), vertex 2 texel 3.
            # The second component starts at grid x 2 and owns the shared column.
            self.assertEqual(heights[0, :4].tolist(), [32768, 32770, 32768+128, 32768+128+2])
            self.assertEqual(heights[1, 0], 32768+20)
            c = np.asarray(row['to_world'])
            world = c[3:].reshape(3, 3)@(np.array((3, 0, heights[0, 3]))-c[:3])
            self.assertTrue(np.allclose(world, (1000+3*50, 0, 130)))
            self.assertTrue((Path(tmp)/row['textures']['MacroTexture']).is_file())

    @unittest.skipUnless((INSTALL/'g3/Content/Paks/g3-WindowsNoEditor.pak').is_file(), 'Spellbreak client not extracted')
    def test_real_practice_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = import_maps(INSTALL, Path(tmp), map_names=['Practice'])
            self.assertFalse(report['failed'])
            scene = json.loads((Path(tmp)/'sb-maps'/'maps'/'practice'/'scene.json').read_text())
            self.assertIn('g3/Content/Maps/Longshot/Alpha/1017/Lobby.umap', scene['streamed_packages'])
            self.assertGreater(scene['placements'], 10000)
            self.assertEqual(len(scene['terrain']), 1)


if __name__ == '__main__':
    unittest.main()

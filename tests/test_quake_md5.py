"""Quake enhanced static poses, indexed skins, catalog and installed pack coverage."""
import io
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from app import app
from tests.test_quake import INSTALL, PALETTE, pak, mdl
from tools.import_quake import import_catalog, read_install, read_pak, read_mdl
from tools.import_quake_md5 import read_mesh, read_anim, skin_points, resolve_skin, build_model
from tools.model_data import load_model_data


MESH = b'''MD5Version 10 // Scale: 10.0 is already baked
commandline "test"
numJoints 2
numMeshes 1
joints {
 "root" -1 ( 0 0 0 ) ( 0 0 0 )
 "child" 0 ( 2 0 0 ) ( 0 0 0 )
}
mesh {
 shader "test"
 numverts 3
 vert 0 ( 0 0 ) 0 1
 vert 1 ( 1 0 ) 1 2
 vert 2 ( 0 1 ) 3 1
 numtris 1
 tri 0 0 1 2
 numweights 4
 weight 0 1 1 ( 1 0 0 )
 weight 1 0 .5 ( 0 0 0 )
 weight 2 1 .5 ( 0 0 0 )
 weight 3 0 1 ( 0 0 1 )
}
'''
ANIM = b'''MD5Version 10
commandline ""
numFrames 2
numJoints 2
frameRate 10
numAnimatedComponents 1
hierarchy {
 "root" -1 1 0
 "child" 0 0 1
}
bounds { ( 0 0 0 ) ( 20 20 20 ) ( 0 0 0 ) ( 30 30 30 ) }
baseframe {
 ( 1 0 0 ) ( 0 0 -0.7071067811865476 )
 ( 2 0 0 ) ( 0 0 0 )
}
frame 0 { 10 }
frame 1 { 20 }
'''
SKIN = struct.pack('<II', 2, 2) + bytes([0, 1, 224, 255])


class MD5Test(unittest.TestCase):
    def test_first_frame_hierarchy_weights_and_orientation(self):
        mesh = read_mesh(MESH)
        self.assertEqual(skin_points(mesh['meshes'][0], mesh['joints']), [(3, 0, 0), (1, 0, 0), (0, 0, 1)])
        animation = read_anim(ANIM, mesh['joints'])
        for actual, expected in zip(skin_points(mesh['meshes'][0], animation['joints']), [(10, 3, 0), (10, 1, 0), (10, 0, 1)]):
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b)
        model = build_model(mesh, animation, ['test.png'])
        self.assertEqual(model['indices'], [0, 2, 1])
        self.assertEqual(model['uvs'], [0, 0, 1, 0, 0, 1])
        for a, b in zip(model['vertices'][:3], [3, 0, 10]):
            self.assertAlmostEqual(a, b)
        self.assertEqual(model['metadata']['frames'], 2)
        self.assertEqual(build_model(mesh, None, ['test.png'])['metadata']['pose'], 'bind pose (no animation)')

    def test_extra_unweighted_mesh_joints_and_animation_hierarchy(self):
        # mg3 ogre_rocket has an exporter-only parent after the animated prefix.
        blob = MESH.replace(b'numJoints 2', b'numJoints 3').replace(b'"root" -1', b'"root" 2')
        blob = blob.replace(b'\n}\nmesh', b'\n "exporter" -1 ( 1000 0 0 ) ( 0 0 0 )\n}\nmesh')
        mesh = read_mesh(blob)
        animation = read_anim(ANIM, mesh['joints'])
        self.assertTrue(animation['hierarchy_differs'])
        self.assertAlmostEqual(skin_points(mesh['meshes'][0], animation['joints'])[0][0], 10)
        mesh['meshes'][0]['weights'][0] = (2, 1, (0, 0, 0))
        with self.assertRaisesRegex(ValueError, 'lacks a weighted'):
            build_model(mesh, animation, ['test.png'])

    def test_multiple_materials_and_invalid_data(self):
        mesh = read_mesh(MESH.replace(b'numMeshes 1', b'numMeshes 2') + MESH[MESH.index(b'mesh {'):])
        result = build_model(mesh, None, ['a.png', 'b.png'])
        self.assertEqual(result['indices'], [0, 2, 1, 3, 5, 4])
        self.assertEqual(result['groups'][1], dict(start=3, count=3, materialIndex=1))
        for blob in [MESH[:-5], MESH.replace(b'Version 10', b'Version 6'),
                     MESH.replace(b'tri 0 0 1 2', b'tri 0 0 1 3'),
                     MESH.replace(b'weight 0 1 1', b'weight 0 1 .2'),
                     MESH.replace(b'weight 0 1 1', b'weight 0 2 1'),
                     MESH.replace(b'( 1 0 0 )', b'( nan 0 0 )')]:
            with self.subTest(blob=blob[-40:]), self.assertRaises(ValueError):
                read_mesh(blob)
        for blob in [ANIM[:-5], ANIM.replace(b'"child" 0', b'"child" 1'),
                     ANIM.replace(b'"child"', b'"other"'), ANIM.replace(b'-1 1 0', b'-1 63 0')]:
            with self.assertRaises(ValueError):
                read_anim(blob, read_mesh(MESH)['joints'])

    def test_skin_zero_palette_and_validation(self):
        files = {'progs/test_00_00.lmp': (SKIN, '', {}), 'gfx/palette.lmp': (PALETTE, '', {})}
        image, info = resolve_skin('test', files)
        self.assertEqual(image.mode, 'RGB')
        self.assertEqual(image.getpixel((1, 1)), (255, 0, 7))
        self.assertEqual(info['fullbright_pixels'], 2)
        self.assertEqual(resolve_skin('progs/test', files)[1]['source'], info['source'])
        with self.assertRaisesRegex(ValueError, 'Missing enhanced skin'):
            resolve_skin('missing', files)
        files['progs/test_00_00.lmp'] = (SKIN[:-1], '', {})
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            resolve_skin('test', files)

    def test_catalog_layering_edit_preservation_and_static_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            root, output = Path(folder) / 'install', Path(folder) / 'data'
            for game in ['id1', 'rerelease/id1', 'rerelease/mg3']:
                (root / game).mkdir(parents=True)
            (root / 'id1/pak0.pak').write_bytes(pak([('gfx/palette.lmp', bytes(768)), ('progs/test.mdl', mdl())]))
            (root / 'rerelease/id1/pak0.pak').write_bytes(pak([
                ('gfx/palette.lmp', PALETTE), ('progs/test.md5mesh', MESH), ('progs/test.md5anim', ANIM),
                ('progs/test_00_00.lmp', SKIN)]))
            (root / 'rerelease/mg3/pak0.pak').write_bytes(pak([('progs/variant.md5mesh', MESH)]))
            report = import_catalog(root, output)
            self.assertEqual((report['md5_entries'], report['md5_ready']), (2, 2))
            self.assertEqual(report['mdl_entries'], 1)
            data = load_model_data(output / 'model_json/test_enhanced.json')
            self.assertEqual(data['metadata']['pose'], 'animation frame 0')
            self.assertEqual(load_model_data(output / 'model_json/mg3_variant_enhanced.json')['metadata']['pose'], 'bind pose (no animation)')
            catalog = json.loads((output / 'catalog.json').read_text())
            self.assertEqual(catalog[1]['category'], 'Quake (enhanced): Items')
            with Image.open(output / 'textures/mg3_variant_enhanced.png') as image:
                self.assertEqual(image.getpixel((1, 1)), (255, 0, 7))
            with patch.dict('app.pack_dirs', quake=output), app.test_client() as client:
                glb = client.get('/export_glb/test_enhanced?game=quake')
                self.assertEqual(glb.status_code, 200)
                self.assertEqual(glb.headers['X-Skinner-Animation-Status'], 'static')
                size = struct.unpack_from('<I', glb.data, 12)[0]
                gltf = json.loads(glb.data[20:20+size])
                self.assertNotIn('animations', gltf)
                obj = client.get('/export_obj/test_enhanced?game=quake')
                self.assertEqual(obj.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(obj.data)) as archive:
                    self.assertTrue(any(n.endswith('.obj') for n in archive.namelist()))
            png = output / 'textures/test_enhanced.png'
            Image.new('RGB', (2, 2), '#123456').save(png)
            edited = png.read_bytes()
            import_catalog(root, output)
            self.assertEqual(png.read_bytes(), edited)


@unittest.skipUnless((INSTALL / 'rerelease/id1/pak0.pak').is_file(), 'needs Quake rerelease')
class MD5InstallTest(unittest.TestCase):
    def test_every_mesh_skin_pose_and_native_scale(self):
        for game, folder, count in [('qextras', 'id1', 59), ('mg3', 'mg3', 5)]:
            path = INSTALL / 'rerelease' / folder / 'pak0.pak'
            if not path.is_file():
                continue
            expected = [n for n, _ in read_pak(path.read_bytes()) if n.startswith('progs/') and n.endswith('.md5mesh')]
            self.assertEqual(len(expected), count)
            files, _, _ = read_install(INSTALL, game)
            for name in expected:
                with self.subTest(game=game, name=name):
                    mesh = read_mesh(files[name][0])
                    anim_name = name.replace('.md5mesh', '.md5anim')
                    animation = read_anim(files[anim_name][0], mesh['joints']) if anim_name in files else None
                    self.assertEqual(animation is None, name == 'progs/health100.md5mesh')
                    points = []
                    for part in mesh['meshes']:
                        image, info = resolve_skin(part['shader'], files)
                        self.assertTrue(image.width and info['source'] in files)
                        points.extend(skin_points(part, animation['joints'] if animation else mesh['joints']))
                    self.assertTrue(all(math.isfinite(v) for p in points for v in p))
                    mdl_name = name.replace('.md5mesh', '.mdl')
                    if mdl_name in files:
                        classic = read_mdl(files[mdl_name][0])
                        # No 10x/100x exporter-scale correction or per-model auto-fit.
                        span = max(max(p[k] for p in points)-min(p[k] for p in points) for k in range(3))
                        old_span = max(max(p[k] for p in classic['points'])-min(p[k] for p in classic['points']) for k in range(3))
                        self.assertTrue(.5 < span / old_span < 2)
                        if game == 'qextras':
                            self.assertEqual(animation['frames'], classic['frames'] if classic['frames'] > 1 else 2)
                    # Monster bounds were exported in native units; several item bounds
                    # were left at exporter scale or retain an old mesh's bounds.
                    if game == 'qextras' and animation and name in (
                            'progs/ogre.md5mesh', 'progs/player.md5mesh', 'progs/shambler.md5mesh'):
                        for edge, fn in zip(animation['bounds'], [min, max]):
                            for k in range(3):
                                self.assertAlmostEqual(fn(p[k] for p in points), edge[k], delta=.04)

    def test_catalog_counts_and_resolved_skins(self):
        with tempfile.TemporaryDirectory() as folder:
            report = import_catalog(INSTALL, folder)
            records = [r for r in report['results'] if r['source'].endswith('.md5mesh')]
            mg3_count = 5 if (INSTALL / 'rerelease/mg3/pak0.pak').is_file() else 0
            self.assertEqual((report['md5_entries'], report['md5_ready']), (59 + mg3_count, 59 + mg3_count))
            self.assertEqual(sum(r['game'] == 'qextras' for r in records), 59)
            self.assertEqual(sum(r['game'] == 'mg3' for r in records), mg3_count)
            self.assertEqual(report['pak_counts']['qextras/pak0.pak']['md5mesh'], 59)
            self.assertEqual(report['pak_counts']['qextras/pak0.pak']['md5anim'], 58)
            if mg3_count:
                self.assertEqual(report['pak_counts']['mg3/pak0.pak']['md5mesh'], 5)
                self.assertEqual(report['pak_counts']['mg3/pak0.pak']['md5anim'], 5)
            for record in records:
                self.assertEqual(record['status'], 'ready')
                model = load_model_data(Path(folder) / 'model_json' / (record['model'] + '.json'))
                self.assertTrue(record['skins'])
                for texture in model['material_textures']:
                    with Image.open(Path(folder) / 'textures' / texture) as image:
                        image.verify()


if __name__ == '__main__':
    unittest.main()

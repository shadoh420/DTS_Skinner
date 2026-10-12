"""Indexed geometry and shared GPU-compressed textures; synthetic only."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

from PIL import Image
from tools.model_data import load_model_data, write_model_data
from tools.texture_storage import dds_bytes, game_texture_dir, write_texture
from tools import vtmb
from tools.import_vtmb import prepare_texture
from tests.test_vtmb import vtf


class StorageTests(unittest.TestCase):
    def test_dx_shared_textures_routes_and_reimports(self):
        import app as server
        from test_dx import Fixture
        from test_dx_maps import make_map
        from test_unreal import index
        from tools.import_unreal import import_catalog
        from tools.import_unreal_map import import_maps
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); install = root/'install'; install.mkdir()
            def dxt_texture(f, colour):
                return (f.name('Format')+b'\x01\x03'+f.name('None')+b'\x01'+bytes(4)+index(8)+
                        struct.pack('<HHI', 0xf800, 0x07e0, 0x55aaff00)+struct.pack('<IIBB', 4, 4, 2, 2))
            with patch.object(Fixture, 'texture', dxt_texture):
                make_map(install)
            models, maps = root/'dx', root/'dx-maps'
            textures = game_texture_dir(root, 'dx')
            import_catalog(install, models, 'dx')
            blue = textures/'deusexcharacters.blue.dds'
            expected = blue.read_bytes()
            import_maps(install, maps, game='dx')
            self.assertEqual(blue.read_bytes(), expected)
            self.assertFalse((maps/'textures').exists())
            scene = json.loads((maps/'maps/01_test/scene.json').read_text())
            self.assertNotIn('positions', scene)  # Bulk map geometry was already binary.
            self.assertIsInstance(scene['vertices'], int)
            self.assertTrue((maps/'maps/01_test/geometry.bin').is_file())
            self.assertAlmostEqual(scene['display']['power'], 2/3)
            write_texture(textures, 'only-model.png', Image.new('RGBA', (1, 1)))
            write_texture(textures, 'only-map.png', Image.new('RGBA', (1, 1)))
            edited = dds_bytes(4, 4, 'DXT1', [struct.pack('<HHI', 31, 0, 0)])
            blue.write_bytes(edited)
            import_maps(install, maps, replace=True, game='dx')
            import_catalog(install, models, 'dx')
            self.assertEqual(blue.read_bytes(), edited)
            self.assertTrue((textures/'only-model.png').is_file())
            self.assertTrue((textures/'only-map.png').is_file())
            dds = dds_bytes(4, 4, 'DXT1', [struct.pack('<HHI', 0xf800, 0, 0)])
            write_texture(textures, 'sample.dds', dds)
            with patch.object(server, 'local_data_dir', root), patch.dict(server.pack_dirs, dx=models):
                client = server.app.test_client()
                for name in ('only-model.png', 'sample.dds', 'deusexcharacters.blue.dds'):
                    a = client.get('/texture/'+name+'?game=dx')
                    b = client.get('/dx-map-data/textures/'+name)
                    self.assertEqual(a.data, b.data)
                    if name.endswith('.dds'):
                        self.assertEqual(b.data, edited if name == blue.name else dds)
                        self.assertEqual(b.mimetype, 'image/vnd-ms.dds')
                    a.close(); b.close()
                self.assertIn(b'/static/dds.js', client.get('/maps/dx/').data)
            with self.assertRaisesRegex(ValueError, 'read-only'):
                import_maps(install, install/'output', game='dx')

    def test_unreal_dds_mips_palette_and_derived_textures(self):
        from test_dx import Fixture
        from test_unreal import index
        from tools.import_unreal import Library, Package, decode_texture, texture_asset, material_settings
        color = struct.pack('<HHI', 0xf800, 0x07e0, 0x55aaff00)
        blocks = {3: color, 6: bytes.fromhex('1032547698badcfe')+color,
                  7: b'\xff\0'+bytes.fromhex('123456789abc')+color}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/'Textures').mkdir()
            for fmt, block in blocks.items():
                f = Fixture()
                def body(props, payloads):
                    return (f.name('Format')+b'\x01'+bytes([fmt])+f.props(props)+bytes([len(payloads)])+
                            b''.join(bytes(4)+index(len(data))+data+struct.pack('<IIBB', w, h, 0, 0)
                                     for data, w, h in payloads))
                mips = [(block*2, 8, 4), (block, 4, 2), (block, 2, 1), (block, 1, 1)]
                imports = [('Core', 'Package', 0, 'Engine'), ('Core', 'Class', -1, 'Texture'),
                           ('Core', 'Class', -1, 'ScriptedTexture')]
                exports = [(-2, 'Still', body({}, mips)), (-2, 'Animated', body({'AnimNext': 1}, mips)),
                           (-3, 'Derived', body({}, mips)),
                           (-2, 'BadMips', body({}, [mips[0], (block, 2, 2)]))]
                if fmt == 3:
                    exports.append((-2, 'Cutout', body({}, [(struct.pack('<HHI', 0, 0xffff, 0xffffffff), 4, 4)])))
                path = root/'Textures/Test.utx'; path.write_bytes(f.package(imports, exports))
                lib = Library(root, game='dx'); package = Package(path, game='dx')
                old = decode_texture(lib, package, 1)
                with patch('tools.import_unreal.decode_texture', side_effect=AssertionError('DXT must not decode')):
                    name, data, w, h = texture_asset(lib, package, 1, masked=True, compressed=True)
                self.assertEqual((name, w, h), ('test.still.dds', 8, 4))
                self.assertEqual(data[128:], b''.join(m[0] for m in mips))
                self.assertEqual(Image.open(io.BytesIO(data)).convert('RGBA').tobytes(), old.tobytes())
                self.assertEqual(texture_asset(lib, package, 1, compressed=True)[:2], (name, data))
                with self.assertRaisesRegex(ValueError, 'mip dimensions'):
                    texture_asset(lib, package, 4, compressed=True)
                if fmt == 3:
                    cutout = texture_asset(lib, package, 5, masked=True, compressed=True)[1]
                    self.assertEqual(Image.open(io.BytesIO(cutout)).convert('RGBA').getchannel('A').getextrema(), (0, 0))
                for ref in (2, 3):
                    name, image, _, _ = texture_asset(lib, package, ref, compressed=True)
                    self.assertTrue(name.endswith('.png'))
                    self.assertEqual(image.tobytes(), old.tobytes())
                self.assertTrue(texture_asset(lib, package, 1)[0].endswith('.png'))  # opt-in only
            f = Fixture()
            palette = f.props({})+index(2)+bytes([20, 40, 60, 255, 80, 100, 120, 255])
            pixels = f.props({'Palette': 2})+b'\x01'+bytes(4)+index(4)+b'\0\1\1\0'+struct.pack('<IIBB', 2, 2, 1, 1)
            imports = [('Core', 'Package', 0, 'Engine'), ('Core', 'Class', -1, 'Texture'), ('Core', 'Class', -1, 'Palette')]
            path.write_bytes(f.package(imports, [(-2, 'P8', pixels), (-3, 'Pal', palette)]))
            lib = Library(root, game='dx'); package = lib.package('Test')
            name, image, _, _ = texture_asset(lib, package, 1, masked=True, compressed=True)
            opaque = decode_texture(lib, package, 1)
            self.assertEqual(name, 'test.p8_masked.png')
            self.assertEqual(image.convert('RGB').tobytes(), opaque.convert('RGB').tobytes())
            self.assertEqual(list(image.getchannel('A').getdata()), [0, 255, 255, 0])
            self.assertEqual(material_settings(2)['alphaFunc'], 'GE128')
            self.assertEqual(material_settings(4 | 2)['blend'], ['gl_one', 'gl_one_minus_src_color'])
            self.assertNotIn('alphaFunc', material_settings(4 | 2))
            self.assertEqual(material_settings(64)['blend'], ['gl_dst_color', 'gl_src_color'])

    def test_indexed_geometry_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = dict(game='vtmb', vertices=[0., 0., 0., 1/3, 0., 0., 0., 1., 0.],
                        normals=[0., 0., 1.]*3, uvs=[0., 0., 1., 0., 0., 1.], indices=[0, 1, 2],
                        colors=[.1, .2, .3]*3, material_textures=['x.dds'],
                        groups=[dict(start=0, count=3, materialIndex=0)])
            old, new = root/'old.json', root/'new.json'
            # Floats are stored as float32: the old JSON holding float32-rounded values must read identically.
            f32 = lambda xs: list(struct.unpack(f'<{len(xs)}f', struct.pack(f'<{len(xs)}f', *xs)))
            old.write_text(json.dumps(dict(data, **{k: f32(data[k]) for k in ('vertices', 'normals', 'uvs', 'colors')})))
            write_model_data(new, data)
            self.assertEqual(load_model_data(old), load_model_data(new))
            write_model_data(root/'skin.json', dict(data, material_textures=['y.png']))
            self.assertEqual(len(list(root.glob('*.bin'))), 1)
            description = json.loads(new.read_text())
            self.assertNotIn('vertices', description)
            binary = root/description['geometry_buffers']['file']; binary.write_bytes(binary.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, 'range'):
                load_model_data(new)

    def test_dds_shared_routes_listing_and_exports(self):
        import app as server
        from tools.glb_exporter import model_to_glb
        from tools.obj_exporter import json_to_obj_zip
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); textures = game_texture_dir(root, 'vtmb')
            from tools.import_vtmb_map import import_maps
            with self.assertRaisesRegex(ValueError, 'Shared textures'):
                import_maps(root/'vtmb', root/'vtmb-maps')
            block = struct.pack('<HHI', 0xf800, 0x07e0, 0)
            raw = dds_bytes(4, 4, 'DXT1', [block]*3)
            write_texture(textures, 'red.dds', raw)
            write_texture(textures, 'red.dds', b'never overwrite')
            with patch.object(server, 'local_data_dir', root), patch.dict(server.pack_dirs, vtmb=root/'vtmb'):
                client = server.app.test_client()
                self.assertEqual(server.game_textures('vtmb'), textures)
                self.assertIn('red.dds', client.get('/list_textures?game=vtmb').json)
                a = client.get('/texture/red.dds?game=vtmb'); b = client.get('/vtmb-map-data/textures/red.dds')
                self.assertEqual(a.data, raw); self.assertEqual(b.data, raw)
                self.assertEqual(a.mimetype, 'image/vnd-ms.dds'); self.assertEqual(b.mimetype, a.mimetype)
                a.close(); b.close()
                for query in ('preview=1', 'download=1', 'rotation=90'):
                    response = client.get('/texture/red.dds?game=vtmb&'+query)
                    self.assertEqual(response.mimetype, 'image/png')
                    self.assertEqual(Image.open(io.BytesIO(response.data)).getpixel((0, 0)), (255, 0, 0, 255))
            data = dict(game='vtmb', winding='ccw', vertices=[0, 0, 0, 1, 0, 0, 0, 1, 0],
                        normals=[0, 0, 1]*3, uvs=[0, 0, 1, 0, 0, 1], indices=[0, 1, 2], material_textures=['red.dds'])
            path = root/'model.json'; write_model_data(path, data)
            loaded = load_model_data(path)
            glb = model_to_glb(loaded, textures)
            self.assertIn(b'image/png', glb)
            output = root/'export.zip'
            json_to_obj_zip(path, textures, output, 'model')
            with zipfile.ZipFile(output) as archive:
                png = next(n for n in archive.namelist() if n.endswith('.png'))
                self.assertEqual(Image.open(io.BytesIO(archive.read(png))).getpixel((0, 0)), (255, 0, 0, 255))

    def test_vtf_dds_mips_alpha_and_orientation(self):
        # Asymmetric rows and columns: copying blocks must agree with the old decoder.
        color = struct.pack('<HHI', 0xf800, 0x07e0, 0x55aaff00)
        for fmt, block in ((13, color), (14, bytes.fromhex('1032547698badcfe')+color),
                           (15, b'\xff\0'+bytes.fromhex('123456789abc')+color),
                           (20, struct.pack('<HHI', 0, 0xffff, 0xffffffff))):
            raw = vtf(fmt, block*3, 4, 4, mips=3)
            old, _ = vtmb.read_vtf(raw); dds, info = vtmb.read_vtf(raw, compressed=True)
            self.assertEqual(dds[128:], block*3)
            self.assertEqual(Image.open(io.BytesIO(dds)).convert('RGBA').tobytes(), old.tobytes())
            self.assertEqual(info['mipmaps'], 3)
        with self.assertRaises(ValueError):
            vtmb.read_vtf(raw[:-1], compressed=True)
        red = struct.pack('<HHI', 0xf800, 0, 0)
        blue = struct.pack('<HHI', 31, 0, 0)
        green = struct.pack('<HHI', 0x07e0, 0, 0)
        raw = vtf(13, blue+green+red+green+color+green, 4, 4, mips=3, frames=2)
        payload, _ = vtmb.read_vtf(raw, compressed=True)
        self.assertEqual(payload[128:], color+red+blue)  # reverse mips; select frame 0 at each level
        from tools.import_vtmb_map import EmbeddedAsset
        files = {'materials/test.vtf': EmbeddedAsset(raw, 'synthetic')}
        vmt = dict(shader='UnlitGeneric', params={'$basetexture': '/materials/TEST.vtf'})
        first = prepare_texture(files, vmt, 'materials/model.vmt')
        vmt['params']['$basetexture'] = 'test'
        second = prepare_texture(files, vmt, 'materials/maps/world.vmt')
        self.assertEqual(first[:2], second[:2])  # model/world VMT paths share the base asset
        full = vtf(13, red, 4, 4)
        packed = zlib.compress(full[64:])
        head = struct.pack('<4sHBBI4I', b'TTH\0', 1, 1, 0, 64, 64, 0, len(full), len(packed))
        files = {'materials/test.tth': EmbeddedAsset(head+full[:64], 'synthetic'),
                 'materials/test.ttz': EmbeddedAsset(packed, 'synthetic')}
        decoded, _ = vtmb.read_texture(files, 'test')
        compressed, _ = vtmb.read_texture(files, 'test', compressed=True)
        self.assertEqual(decoded.tobytes(), Image.open(io.BytesIO(compressed)).convert('RGBA').tobytes())
        self.assertEqual(compressed[128:], red)

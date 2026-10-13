"""Small, invented UE2 packages. No retail assets or environment-specific temp paths."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from tools.tvg_package import Package, Library
from tools.import_tvg import import_catalog, texture_payload, Materials
from tools.model_data import load_model_data, MODEL_GAMES
from tools.texture_workshop import TEXTURE_GAMES
from tools.texture_storage import game_texture_dir
from tools.glb_exporter import model_to_glb
from tools.obj_exporter import json_to_obj_zip


def pack(fmt, *values):
    return struct.pack('<'+fmt, *values)


def compact(n):
    value = abs(n)
    out = bytearray([(128 if n < 0 else 0) | (value & 63) | (64 if value > 63 else 0)])
    value >>= 6
    while value:
        out.append((value & 127) | (128 if value > 127 else 0))
        value >>= 7
    return bytes(out)


def array(fmt, rows):
    return compact(len(rows)) + b''.join(pack(fmt, *r) for r in rows)


class Fixture:
    """Tables at fixed offsets; object payloads are generated with absolute lazy ends."""
    def __init__(self, version=130, licensee=27):
        self.version, self.licensee = version, licensee
        self.names, self.imports, self.exports = ['None'], [], []

    def name(self, name):
        if name not in self.names:
            self.names.append(name)
        return compact(self.names.index(name))

    def imp(self, name, cls='Class', outer=0):
        self.imports.append((name, cls, outer))
        return -len(self.imports)

    def external(self, package, name, cls):
        parent = self.imp(package, 'Package')
        return self.imp(name, cls, parent)

    def add(self, name, cls, payload=b'', outer=0, base=0):
        self.exports.append((name, cls, payload, outer, base))
        return len(self.exports)

    def hdr(self, since, sub=1):
        return pack('3i', 3, 0, sub) if self.licensee >= since else b''

    def prop(self, name, kind, raw, struct_name=None):
        sizes = (1, 2, 4, 12, 16)
        size = len(raw)
        sc = sizes.index(size) if size in sizes else 5 if size < 256 else 6
        return (self.name(name) + bytes([kind | sc << 4]) + (self.name(struct_name) if kind == 10 else b'') +
                (pack('B' if sc == 5 else 'H', size) if sc >= 5 else b'') + raw)

    def boolean(self, name, value):
        return self.name(name) + bytes([3 | (128 if value else 0)])

    def lazy(self, buffer, offset, fmt, rows):
        raw = array(fmt, rows)
        buffer.extend(pack('i', offset+len(buffer)+4+len(raw)) + raw)

    def texture(self, fmt=5, raw=b'\x10\x20\x30\xff', w=1, h=1):
        props = self.prop('Format', 1, bytes([fmt]))
        def payload(offset):
            b = bytearray(self.hdr(25)+props+b'\0'+b'\1'+self.hdr(26))
            if self.licensee >= 26:
                self.lazy(b, offset, 'B', [(99,)])  # exercise nonempty per-mip extension
            self.lazy(b, offset, 'B', [(x,) for x in raw])
            b.extend(pack('iiBB', w, h, 0, 0))
            return b
        return payload

    def static(self, texture):
        slots = b'\1'+self.prop('Material', 5, compact(texture))+b'\0'
        props = self.prop('Materials', 9, slots)
        def payload(offset):
            b = bytearray(self.hdr(25)+props+b'\0'+bytes(41)+self.hdr(3))
            b.extend(array('i5H', [(0, 0, 0, 2, 1, 1)])+bytes(25)+self.hdr(13))
            b.extend(array('6f', [(0, 0, 0, 0, 0, 1), (52.5, 0, 0, 0, 0, 1), (0, 52.5, 0, 0, 0, 1)])+bytes(4))
            if self.licensee >= 13:
                b.extend(b'\0')
            b.extend((b'\0'+bytes(4))*2)
            b.extend(b'\1'+array('2f', [(0, 0), (1, 0), (0, 1)])+bytes(8))
            b.extend(array('H', [(0,), (2,), (1,)])+bytes(4))  # native UE2 clockwise
            return b
        return payload

    def skeletal(self, texture, lod=False):
        root_name = self.name('root')
        def payload(offset):
            b = bytearray(self.hdr(25)+b'\0'+bytes(41)+pack('ii', 4, 3)+b'\0\1'+compact(texture))
            b.extend(pack('6f3i', 1, 1, 1, 0, 0, 0, 0, 0, 0)+b'\0'*5+bytes(24))
            b.extend(bytes(4)+b'\0'+bytes(52)+bytes(4)+self.hdr(4)+b'\0')
            b.extend(b'\1'+root_name+pack('I4f3f4fii', 0, 0, 0, 0, 1, 0, 0, 0, 1, 1, 1, 1, 0, 0))
            b.extend(b'\0'+bytes(4)+b'\0'*5)
            # One LOD stream including the Vengeance tangent extensions.
            b.extend(b'\1'+self.hdr(9)+b'\0\0'+bytes(4)+b'\0\0'+(b'\0'+bytes(4))*2)
            b.extend(self.hdr(17)+bytes(12)+b'\0')
            if self.licensee >= 17:
                b.extend(bytes(4)+b'\0')
            self.lazy(b, offset, 'fHH', [])
            self.lazy(b, offset, 'H2f', [(0, 0, 0), (1, 1, 0), (2, 0, 1)] if lod else [])
            self.lazy(b, offset, '4H', [(0, 2, 1, 0)] if lod else [])
            self.lazy(b, offset, '3f', [(0, 0, 0), (105, 0, 0), (0, 52.5, 0)] if lod else [])
            b.extend(bytes(24))
            if self.licensee >= 9:
                self.lazy(b, offset, '6f', [])
                b.extend(b'\0')
            b.extend(b'\0')  # compact object after LODs, not int32
            self.lazy(b, offset, '3f', [(0, 0, 0), (52.5, 0, 0), (0, 52.5, 0)])
            self.lazy(b, offset, 'H2f', [(0, 0, 0), (1, 1, 0), (2, 0, 1)])
            self.lazy(b, offset, '3HBBI', [(0, 2, 1, 0, 0, 1)])
            return b
        return payload

    def defaults(self, props=b''):
        return self.hdr(25) + bytes(5+12+22) + props+b'\0'

    def write(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        bodies, offset = [], 8192
        for _, _, payload, _, _ in self.exports:
            body = bytes(payload(offset) if callable(payload) else payload)
            bodies.append((offset, body))
            offset += len(body)
        imports = b''.join(self.name('Core')+self.name(c)+pack('i', o)+self.name(n) for n, c, o in self.imports)
        exports = b''
        for (n, c, _, o, base), (at, body) in zip(self.exports, bodies):
            exports += compact(c)+compact(base)+pack('i', o)+self.name(n)+bytes(4)+compact(len(body))
            if body:
                exports += compact(at)
        names = b''
        for name in self.names:
            if name.isascii():
                raw = name.encode()+b'\0'; names += compact(len(raw))+raw
            else:
                raw = (name+'\0').encode('utf-16le'); names += compact(-len(raw)//2)+raw
            names += bytes(4)
        self.assert_sizes(names, imports, exports)
        data = bytearray(offset)
        data[:36] = pack('IHHI6I', 0x9e2a83c1, self.version, self.licensee, 0,
                         len(self.names), 64, len(self.exports), 4096, len(self.imports), 2048)
        data[64:64+len(names)] = names
        data[2048:2048+len(imports)] = imports
        data[4096:4096+len(exports)] = exports
        for at, body in bodies:
            data[at:at+len(body)] = body
        path.write_bytes(data)
        return path

    @staticmethod
    def assert_sizes(names, imports, exports):
        assert len(names) < 1984 and len(imports) < 2048 and len(exports) < 4096


def install_fixture(root):
    (root/'Program/Bin').mkdir(parents=True)
    f = Fixture()
    texture, static, skeletal = [f.imp(c) for c in ('Texture', 'StaticMesh', 'SkeletalMesh')]
    red = f.add('Red', texture, f.texture())
    blue = f.add('Blue', texture, f.texture(raw=b'\xff\0\0\xff'))
    f.add('Prop', static, f.static(red))
    f.add('Hero', skeletal, f.skeletal(red))
    f.add('Hero', static, f.static(red))  # legal class-qualified name collision
    f.add('PropCollision', static)
    f.write(root/'Content/Art/Example.pkg')
    f = Fixture()
    root_class = f.add('SkinInfo', 0, f.defaults())
    f.add('applicableMesh', f.imp('ObjectProperty'), outer=root_class)
    f.add('meshSkins', f.imp('ArrayProperty'), outer=root_class)
    f.write(root/'Program/Bin/Gameplay.u')
    f = Fixture()
    base = f.external('Gameplay', 'SkinInfo', 'Class')
    hero = f.external('Example', 'Hero', 'SkeletalMesh')
    tex = f.external('Example', 'Blue', 'Texture')
    props = f.prop('applicableMesh', 5, compact(hero))+f.prop('meshSkins', 9, b'\1'+compact(tex))
    f.add('BlueSkin', 0, f.defaults(props), base=base)
    f.write(root/'Content/Skins/BlueSkin.pkg')


class PackageTests(unittest.TestCase):
    def test_skeletal_prefers_lod0_over_reference_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(); tex = f.add('Color', f.imp('Texture'), f.texture())
            ref = f.add('Hero', f.imp('SkeletalMesh'), f.skeletal(tex, lod=True))
            p = Package(f.write(Path(tmp)/'Lod.pkg'))
            try:
                data = p.mesh(ref)
                self.assertEqual(data['metadata']['geometry_source'], 'LOD0 raw mesh')
                self.assertEqual(data['vertices'][3:6], [0, 0, 2])
                self.assertEqual(data['winding'], 'ccw')
            finally:
                p.close()

    def test_texture_format_ids_in_package_mips(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture()
            cls = f.imp('Texture')
            for fmt, size in ((0, 16), (3, 8), (5, 64), (7, 16), (8, 16), (10, 32), (12, 32), (13, 16)):
                f.add('Format'+str(fmt), cls, f.texture(fmt, bytes(size), 4, 4))
            p = Package(f.write(Path(tmp)/'Formats.pkg'))
            try:
                for ref, expected in enumerate((0, 3, 5, 7, 8, 10, 12, 13), 1):
                    props, mips = p.texture(ref)
                    self.assertEqual(props['Format'], expected)
                    self.assertEqual(mips[0][:2], (4, 4))
            finally:
                p.close()

    def test_versions_headers_mips_and_qualified_unicode_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ver, lic in ((99, 0), (129, 24), (130, 25), (130, 26), (130, 27)):
                with self.subTest(version=ver, licensee=lic):
                    f = Fixture(ver, lic)
                    group = f.add('Grøup', f.imp('Package'))
                    ref = f.add('Image', f.imp('Texture'), f.texture(), outer=group)
                    p = Package(f.write(Path(tmp)/'Demo.pkg'))
                    try:
                        self.assertEqual(p.qualified(ref), 'Demo.Grøup.Image')
                        props, mips = p.texture(ref)
                        self.assertEqual(props['Format'], 5)
                        self.assertEqual(mips, [(1, 1, b'\x10\x20\x30\xff')])
                        self.assertEqual(p.at, p.limit)
                    finally:
                        p.close()

    def test_bad_object_header_and_lazy_bounds_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(); ref = f.add('Bad', f.imp('Texture'), bytes(12))
            p = Package(f.write(Path(tmp)/'Bad.pkg'))
            try:
                with self.assertRaisesRegex(ValueError, 'header'):
                    p.texture(ref)
            finally:
                p.close()
            f = Fixture(); body = f.hdr(25)+b'\0\1'+f.hdr(26, sub=0)+pack('i', 1)+b'\1x'+pack('iiBB', 1, 1, 0, 0)
            f.add('Bad', f.imp('Texture'), body)
            p = Package(f.write(Path(tmp)/'Bad.pkg'))
            try:
                with self.assertRaisesRegex(ValueError, 'end mismatch'):
                    p.texture(1)
            finally:
                p.close()

    def test_static_and_skeletal_geometry_and_normals(self):
        with tempfile.TemporaryDirectory() as tmp:
            for lic in (0, 24, 27):
                f = Fixture(126 if lic == 0 else 130, lic)
                tex = f.add('Texture', f.imp('Texture'), f.texture())
                a = f.add('Static', f.imp('StaticMesh'), f.static(tex))
                b = f.add('Skeletal', f.imp('SkeletalMesh'), f.skeletal(tex))
                p = Package(f.write(Path(tmp)/'Demo.pkg'))
                try:
                    for ref in (a, b):
                        mesh = p.mesh(ref)
                        self.assertEqual(mesh['indices'], [0, 2, 1])
                        self.assertEqual(mesh['vertices'], [0, 0, 0, 0, 0, 1, -1, 0, 0])
                        np.testing.assert_allclose(np.array(mesh['normals']).reshape(-1, 3), [[0, 1, 0]]*3)
                        self.assertEqual(mesh['material_refs'], [tex])
                    self.assertEqual(mesh['metadata']['bones'], [dict(name='root', parent=0)])
                finally:
                    p.close()


class TextureTests(unittest.TestCase):
    def test_all_supported_ids_and_dxt_payloads(self):
        palette = bytes([10, 20, 30, 0])*256
        for fmt, size in ((3, 8), (7, 16), (8, 16), (13, 16)):
            raw = bytes(range(size))
            ext, payload, meta = texture_payload(fmt, [(4, 4, raw)])
            self.assertEqual(ext, '.dds')
            self.assertEqual(payload[128:], raw)
            self.assertEqual(payload[84:88], {3: b'DXT1', 7: b'DXT3', 8: b'DXT5', 13: b'DXT5'}[fmt])
            self.assertEqual('normal_encoding' in meta, fmt == 13)
        _, image, _ = texture_payload(0, [(2, 1, b'\0\1')], palette)
        self.assertEqual([image.getpixel((x, 0)) for x in range(2)], [(10, 20, 30, 0), (10, 20, 30, 255)])
        _, image, _ = texture_payload(5, [(1, 1, b'\x03\x02\x01\xff')])
        self.assertEqual(image.getpixel((0, 0)), (1, 2, 3, 255))
        _, image, meta = texture_payload(12, [(1, 1, b'\0\0')])
        self.assertEqual(image.getpixel((0, 0)), (128, 128, 255))
        self.assertIn('CxV8U8', meta['normal_encoding'])
        _, image, _ = texture_payload(10, [(1, 1, pack('H', 12345))])
        self.assertEqual(image.getpixel((0, 0)), 12345)
        with self.assertRaises(ValueError):
            texture_payload(3, [(4, 4, bytes(7))])
        with self.assertRaises(ValueError):
            texture_payload(2, [(1, 1, b'\0')])

    def test_material_graph_alpha_modifiers_and_recorded_normals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'install'; (root/'Program/Bin').mkdir(parents=True)
            f = Fixture(); tex = f.imp('Texture')
            diffuse = f.add('Diffuse', tex, f.texture(raw=b'\x10\x20\x30\xff'))
            mask = f.add('Mask', tex, f.texture(raw=b'\xff\xff\xff\x40'))
            normal = f.add('Normal', tex, f.texture(13, bytes(range(16)), 4, 4))
            def material(name, cls, props):
                return f.add(name, f.imp(cls), f.hdr(25)+props+b'\0')
            shader = material('Surface', 'Shader', f.prop('Diffuse', 5, compact(diffuse))+
                              f.prop('Opacity', 5, compact(mask))+f.prop('NormalMap', 5, compact(normal))+
                              f.boolean('TwoSided', True))
            scale = material('Scale', 'TexScaler', f.prop('Material', 5, compact(shader))+
                             f.prop('UScale', 4, pack('f', 2))+f.prop('VScale', 4, pack('f', 4)))
            material('Final', 'FinalBlend', f.prop('Material', 5, compact(scale))+
                     f.boolean('AlphaTest', True)+f.prop('AlphaRef', 1, bytes([64]))+
                     f.prop('FrameBufferBlending', 1, bytes([6])))
            material('Combine', 'Combiner', f.prop('Material1', 5, compact(diffuse))+
                     f.prop('Material2', 5, compact(mask))+f.prop('Mask', 5, compact(mask)))
            material('Lum', 'Shader', f.prop('SelfIllumination', 5, compact(diffuse)))
            a = material('CycleA', 'FinalBlend', b'')
            b = material('CycleB', 'FinalBlend', f.prop('Material', 5, compact(a)))
            old = f.exports[a-1]
            f.exports[a-1] = (old[0], old[1], f.hdr(25)+f.prop('Material', 5, compact(b))+b'\0', 0, 0)
            f.write(root/'Content/Art/Materials.pkg')
            lib = Library(root)
            try:
                directory = game_texture_dir(Path(tmp)/'data', 'tvg')
                mats = Materials(lib, directory)
                result = mats.material('Materials.Final')
                self.assertEqual(result['status'], 'ready')
                self.assertEqual(result['settings']['uvScale'], [.5, .25])
                self.assertEqual(result['settings']['cull'], 'none')
                self.assertEqual(result['settings']['alphaTest'], 64/255)
                self.assertEqual(result['settings']['blend'], ['gl_one', 'gl_one'])
                self.assertEqual(mats.image(result).getpixel((0, 0)), (48, 32, 16, 64))
                model = dict(game='tvg', winding='ccw', vertices=[0, 0, 0, 0, 0, 1, -1, 0, 0],
                             uvs=[0, 0, 1, 0, 0, 1], indices=[0, 2, 1],
                             material_textures=[result['texture']], material_settings=[result['settings']])
                glb = model_to_glb(model, directory)
                doc = json.loads(glb[20:20+struct.unpack_from('<I', glb, 12)[0]])
                self.assertEqual(doc['materials'][0]['alphaMode'], 'MASK')
                self.assertAlmostEqual(doc['materials'][0]['alphaCutoff'], 64/255)
                self.assertTrue(doc['materials'][0]['doubleSided'])
                self.assertEqual(mats.image(mats.material('Materials.Combine')).getpixel((0, 0)), (48, 32, 16, 64))
                recorded = mats.material('Materials.Surface')['recorded_maps']['NormalMap']
                self.assertIn('DXT5N', recorded['normal_encoding'])
                self.assertEqual((directory/recorded['texture']).read_bytes()[128:], bytes(range(16)))
                self.assertTrue(mats.material('Materials.Lum')['settings']['unlit'])
                self.assertEqual(mats.material('Materials.CycleA')['status'], 'unresolved')
            finally:
                lib.close()


class ImportTests(unittest.TestCase):
    def test_shared_buffers_class_skins_collision_index_and_texture_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp)/'install', Path(tmp)/'data/tvg'
            install_fixture(root)
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            report = import_catalog(root, output)
            self.assertEqual(report['ready'], 3)
            self.assertEqual(report['entries'], 4)
            self.assertEqual(report['skipped'], 1)
            self.assertEqual(report['unresolved_materials'], 0)
            self.assertEqual(report['class_failures'], [])
            index = json.loads((output/'model-index.json').read_text())
            self.assertEqual(set(index['example.hero']['by_class']), {'StaticMesh', 'SkeletalMesh'})
            hero = index['example.hero']['by_class']['SkeletalMesh']
            self.assertEqual(len(hero['looks']), 2)
            self.assertEqual(len(index['example.hero']['by_class']['StaticMesh']['looks']), 1)
            metadata = [json.loads((output/'model_json'/(n+'.json')).read_text()) for n in hero['looks']]
            self.assertEqual(metadata[0]['geometry_buffers'], metadata[1]['geometry_buffers'])
            self.assertEqual(metadata[0]['geometry_buffers']['arrays']['vertices']['dtype'], '<f4')
            self.assertNotEqual(metadata[0]['material_textures'], metadata[1]['material_textures'])
            data = load_model_data(output/hero['model_json'])
            self.assertEqual(data['material_texture_games'], ['tvg'])
            directory = game_texture_dir(output.parent, 'tvg')
            (directory/'map-only.png').write_bytes(b'future map texture')
            self.assertEqual(model_to_glb(data, directory)[:4], b'glTF')
            archive = Path(tmp)/'hero.zip'
            json_to_obj_zip(output/hero['model_json'], directory, archive, 'hero')
            self.assertEqual(archive.read_bytes()[:2], b'PK')
            image_path = directory/metadata[1]['material_textures'][0]
            image_path.write_bytes(b'user edit')
            import_catalog(root, output)
            self.assertEqual(image_path.read_bytes(), b'user edit')
            self.assertEqual((directory/'map-only.png').read_bytes(), b'future map texture')
            self.assertEqual({p: p.read_bytes() for p in before}, before)
            self.assertFalse((output/'model_json/textures').exists())
            self.assertIn('tvg', MODEL_GAMES)
            self.assertIn('tvg', TEXTURE_GAMES)

    def test_bad_selection_and_install_output_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'install'; install_fixture(root)
            with self.assertRaisesRegex(ValueError, 'outside'):
                import_catalog(root, root/'tvg')
            with self.assertRaisesRegex(ValueError, 'Unknown model'):
                import_catalog(root, Path(tmp)/'tvg', ['no.such.model'])
            self.assertFalse((Path(tmp)/'tvg').exists())

    def test_app_model_texture_and_import_routes(self):
        import app
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp)/'install', Path(tmp)/'data/tvg'
            install_fixture(root)
            import_catalog(root, output)
            with patch.dict(app.pack_dirs, tvg=output), patch.object(app, 'local_data_dir', output.parent):
                client = app.app.test_client()
                catalog = client.get('/list_models?game=tvg').json
                self.assertEqual(len(catalog), 4)
                entry = catalog[0]
                data = client.get('/model_json/'+entry['model_name']+'?game=tvg').json
                self.assertEqual(data['game'], 'tvg')
                self.assertEqual(data['material_texture_games'], ['tvg'])
                self.assertIn(entry['texture_name'], client.get('/list_textures?game=tvg').json)
                response = client.get('/texture/'+entry['texture_name']+'?game=tvg')
                self.assertEqual(response.status_code, 200)
                response.close()
                with patch('app.import_tvg_catalog', return_value={'ready': 3}) as importer:
                    self.assertEqual(client.post('/import_tvg', json={'path': str(root)}).json, {'ready': 3})
                    importer.assert_called_once_with(str(root), output)
                    self.assertEqual(client.post('/import_tvg', json={'path': str(root)},
                                                 headers={'Origin': 'https://other.invalid'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()

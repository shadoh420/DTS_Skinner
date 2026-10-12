"""Tiny synthetic v68 packages, plus bounded optional reads of a local stock install. No retail bytes."""
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import app
from tools.import_dx import actor_texture, category, mesh_views, standing_pose
from tools.import_unreal import Library, Package, import_catalog, mesh_points
from test_unreal import index


class Fixture:
    """Name table and property encoding for small, authored packages only."""
    def __init__(self):
        self.names = ['None']

    def name(self, value):
        if value not in self.names:
            self.names.append(value)
        return index(self.names.index(value))

    def props(self, values):
        out = b''
        for key, value in values.items():
            base, _, element = key.partition('[')
            array = bool(element)
            if isinstance(value, float):
                kind, data = 4, struct.pack('<f', value)
            elif key == 'Style':
                kind, data = 1, bytes([value])
            else:
                kind, data = 5, index(value)
            sizes = {1: 0, 2: 1, 4: 2, 12: 3, 16: 4}
            size = sizes.get(len(data), 5)
            out += self.name(base) + bytes([kind | size << 4 | (128 if array else 0)])
            if size == 5:
                out += bytes([len(data)])
            if array:
                out += bytes([int(element[:-1])])
            out += data
        return out + self.name('None')

    def cls(self, title, parent, values, scripted=False):
        return (index(parent) + index(0) * 3 + self.name(title) + struct.pack('<III', 0, 0, int(scripted)) +
                (b'\x53' if scripted else b'') + bytes(42) + index(0) * 3 + self.name('None') + self.props(values))

    def mesh(self):
        # Two frames; Idle begins at frame 1. Values deliberately exceed the Unreal 11/11/10 range.
        bounds = bytes(41)
        lazy = lambda n, data: bytes(4) + index(n) + data
        points = [(2048, -2000, 700), (2048, -1800, 500), (2048, -1600, 700)]
        vertices = points + [(x + 100, y, z) for x, y, z in points]
        out = self.props({}) + bounds + lazy(6, b''.join(struct.pack('<4h', *v, 0) for v in vertices))
        out += lazy(0, b'') + index(1) + self.name('Idle') + self.name('None')
        out += struct.pack('<ii', 1, 1) + index(0) + struct.pack('<f', 5)
        out += lazy(0, b'') + bounds + lazy(0, b'') + index(2) + index(2) * 2
        out += index(0) * 2 + struct.pack('<iiII3f3f3iII', 3, 2, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0) + index(0)
        out += index(0) * 2 + index(2) + struct.pack('<8H', 0, 1, 2, 0, 0, 2, 1, 1)
        out += index(0) + index(3) + struct.pack('<HBBHBBHBB', 0, 0, 0, 1, 255, 0, 2, 255, 255)
        out += index(2) + struct.pack('<IiIi', 0, 0, 0x102, 1) + index(0) + struct.pack('<II', 3, 0)
        return out + bytes(24) + index(0) + bytes(4)

    def texture(self, colour):
        # Native BGRA, no palette dependency.
        return (self.name('Format') + b'\x01\x05' + self.name('None') + b'\x01' + bytes(4) +
                index(4) + bytes(colour) + struct.pack('<IIBB', 1, 1, 0, 0))

    def package(self, imports, exports, licensee=1):
        count = len(imports)
        imports = b''.join(self.name(pkg) + self.name(cls) + struct.pack('<i', outer) + self.name(n)
                           for pkg, cls, outer, n in imports)
        for cls, title, body in exports:
            self.name(title)
        names = b''.join(index(len(n) + 1) + n.encode() + b'\0' + bytes(4) for n in self.names)
        at = 40 + len(names) + len(imports)
        bodies, table = b'', b''
        for cls, title, body in exports:
            table += index(cls) + index(0) + bytes(4) + self.name(title) + struct.pack('<I', 0x70104)
            table += index(len(body)) + (index(at + len(bodies)) if body else b'')
            bodies += body
        head = struct.pack('<IHHI6I', 0x9e2a83c1, 68, licensee, 0, len(self.names), 40,
                           len(exports), at + len(bodies), count, 40 + len(names))
        return head + bytes(4) + names + imports + bodies + table


def fixture(install, missing=False):
    system = install / 'System'
    system.mkdir(parents=True)
    f = Fixture()
    imports = [('Core', 'Package', 0, 'Engine'), ('Core', 'Class', -1, 'LodMesh'),
               ('Core', 'Class', -1, 'Texture')]
    exports = [(-2, 'Body', f.mesh()), (-3, 'Own', f.texture((10, 20, 30, 255))),
               (-3, 'Red', f.texture((0, 0, 255, 255))), (-3, 'Blue', f.texture((255, 0, 0, 255))),
               (0, 'ScriptedPawn', f.cls('ScriptedPawn', 0, {})),
               (0, 'Parent', f.cls('Parent', 5, {'Mesh': 1, 'MultiSkins': 3, 'MultiSkins[1]': 4}))]
    (system / 'DeusExCharacters.u').write_bytes(f.package(imports, exports))
    f = Fixture()
    imports = [('Core', 'Package', 0, 'DeusExCharacters'), ('Core', 'Class', -1, 'Parent'),
               ('Engine', 'Texture', -1, 'Blue'), ('Engine', 'LodMesh', -1, 'Body'),
               ('Engine', 'Texture', -1, 'Missing')]
    exports = [(0, 'JC', f.cls('JC', -2, {})),
               (0, 'Paul', f.cls('Paul', -2, {'MultiSkins': -3}, scripted=True)),
               (0, 'Twin', f.cls('Twin', -2, {'MultiSkins': -3}))]
    if missing:
        exports.append((0, 'BrokenSkin', f.cls('BrokenSkin', -2, {'MultiSkins': -5})))
    (system / 'DeusEx.u').write_bytes(f.package(imports, exports))
    # Same geometry bytes under a second name must not create a duplicate raw-mesh entry.
    f = Fixture()
    imports = [('Core', 'Package', 0, 'Engine'), ('Core', 'Class', -1, 'LodMesh'),
               ('Core', 'Class', -1, 'Texture')]
    mesh = f.mesh()
    exports = [(-2, 'Unused', mesh), (-3, 'Own', f.texture((10, 20, 30, 255))), (-2, 'Duplicate', mesh)]
    (system / 'DeusExItems.u').write_bytes(f.package(imports, exports))
    return system


class DxSyntheticTests(unittest.TestCase):
    def test_headers_signed_vertices_lod_tail_pose_and_class_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            system = fixture(root)
            lib = Library(root, game='dx')
            package = lib.package('DeusExCharacters')
            self.assertEqual((package.version, package.licensee), (68, 1))
            mesh = package.mesh(1)
            self.assertEqual(mesh_points(mesh, 1)[0], (2148, -2000, 700))
            self.assertEqual(standing_pose(mesh)[:2], (1, 'Idle'))
            props, owners, chain = lib.class_info(('DeusEx', 'Paul'))
            self.assertEqual(chain, ['Paul', 'Parent', 'ScriptedPawn'])
            self.assertEqual(owners['MultiSkins'].path.stem, 'DeusEx')
            self.assertEqual(owners['MultiSkins[1]'].path.stem, 'DeusExCharacters')
            self.assertEqual(lib.resolve(*actor_texture(package, mesh, props, owners, 0))[1], 4)
            data = bytearray((system / 'DeusExCharacters.u').read_bytes())
            struct.pack_into('<H', data, 6, 0)
            (system / 'DeusExCharacters.u').write_bytes(data)
            self.assertEqual(Package(system / 'DeusExCharacters.u', game='dx').mesh(1), mesh)
            package.exports[0]['size'] -= 1
            with self.assertRaisesRegex(ValueError, 'bytes off its end'):
                package.mesh(1)

    def test_catalog_distinct_looks_dedup_exports_and_route(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            output = root / 'out'
            with patch.dict('app.pack_dirs', dx=output):
                client = app.test_client()
                response = client.post('/import_dx', json={'path': str(root)})
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json['ready'], 3)
                catalog = client.get('/list_models?game=dx').json
                self.assertEqual(len(catalog), 3)
                report = json.loads((output / 'import-report.json').read_text())
                self.assertTrue(any(a['source'] == 'DeusEx.Twin' for a in report['aliases']))
                self.assertTrue(any(a['source'] == 'DeusExItems.Duplicate' for a in report['aliases']))
                data = json.loads((output / 'model_json' / 'deusex.jc.json').read_text())
                self.assertEqual(data['metadata']['frame'], 1)
                self.assertEqual(data['material_textures'], ['deusexcharacters.blue_masked.png', 'deusexcharacters.red.png'])
                glb = client.get('/export_glb/deusex.jc?game=dx')
                self.assertEqual(glb.status_code, 200)
                length = struct.unpack_from('<I', glb.data, 12)[0]
                document = json.loads(glb.data[20:20 + length])
                self.assertFalse(document.get('animations'))
                accessor = document['accessors'][document['meshes'][0]['primitives'][0]['attributes']['POSITION']]
                view = document['bufferViews'][accessor['bufferView']]
                offset = 28 + length + view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
                positions = struct.unpack_from('<' + str(len(data['vertices'])) + 'f', glb.data, offset)
                for actual, expected in zip(positions, data['vertices']):
                    self.assertAlmostEqual(actual, expected, places=4)
                obj = client.get('/export_obj/deusex.jc?game=dx')
                self.assertEqual(obj.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(obj.data)) as archive:
                    text = archive.read(next(n for n in archive.namelist() if n.endswith('.obj'))).decode()
                    positions = [float(v) for line in text.splitlines() if line.startswith('v ') for v in line.split()[1:]]
                    self.assertEqual(positions, data['vertices'])
                png = output / 'textures' / 'deusexcharacters.red.png'
                png.write_bytes(b'user edit')
                import_catalog(root, output, 'dx')
                self.assertEqual(png.read_bytes(), b'user edit')

    def test_failures_reported_and_mods_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            system = fixture(root, missing=True)
            for filename in ('DXRando.u', 'FemJC.u', 'DeusExe.u'):
                (system / filename).write_bytes(b'not a package')
            (root / 'DXRando' / 'System').mkdir(parents=True)
            (root / 'DXRando' / 'System' / 'DeusEx.u').write_bytes(b'not a package')
            (system / 'DeusExDeco.u').write_bytes(b'truncated')
            output = root / 'out'
            import_catalog(root, output, 'dx')
            report = json.loads((output / 'import-report.json').read_text())
            self.assertTrue(any(i['kind'] == 'package' and i['source'] == 'deusexdeco' for i in report['issues']))
            self.assertTrue(any(i['kind'] == 'skin' and i['texture'].endswith('Missing') for i in report['issues']))
            self.assertFalse(any('dxrando' in p['name'].lower() for p in report['packages']))
            self.assertTrue(any('FemJC' in p for p in report['excluded']))

    def test_skin_precedence_categories_and_inventory_views(self):
        owner = object()
        mesh = dict(textures=[8, 9, 0])
        props = dict(Skin=1, Texture=2, MultiSkins=3)
        owners = dict.fromkeys(props, owner)
        self.assertEqual(actor_texture(owner, mesh, props, owners, 0), (owner, 3))
        self.assertEqual(actor_texture(owner, mesh, props, owners, 1), (owner, 9))
        self.assertEqual(actor_texture(owner, mesh, props, owners, 2), (owner, 1))
        self.assertEqual(category(['SecurityBot2', 'Robot', 'ScriptedPawn'], 'DeusExCharacters'), 'Robots')
        self.assertEqual(category(['Dog', 'Animal', 'ScriptedPawn'], 'DeusExCharacters'), 'Animals')
        self.assertEqual(category(['BlackHelicopter', 'Vehicles', 'DeusExDecoration'], 'DeusExDeco'), 'Vehicles')
        self.assertEqual(mesh_views(dict(Mesh=1, PlayerViewMesh=2, PickupViewMesh=3, ThirdPersonMesh=4),
                                    ['WeaponPistol', 'Weapon', 'Inventory']), ['PickupViewMesh', 'ThirdPersonMesh'])
        self.assertEqual(standing_pose(dict(seqs=[], frames=1))[:2], (0, None))

    def test_inventory_view_scale_style_and_unreadable_class(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            system = fixture(root)
            f = Fixture()
            imports = [('Core', 'Package', 0, 'DeusExCharacters'), ('Engine', 'LodMesh', -1, 'Body')]
            exports = [(0, 'Inventory', f.cls('Inventory', 0, {})),
                       (0, 'Weapon', f.cls('Weapon', 1, {})),
                       (0, 'Pistol', f.cls('Pistol', 2, dict(Mesh=-2, PickupViewMesh=-2, ThirdPersonMesh=-2,
                                                          PickupViewScale=2.0, ThirdPersonScale=1.0, Style=3))),
                       (0, 'Unreadable', b'\x00'),
                       (0, 'BadParent', f.cls('BadParent', 127, {'Mesh': -2}))]
            (system / 'DeusEx.u').write_bytes(f.package(imports, exports))
            output = root / 'out'
            import_catalog(root, output, 'dx')
            pickup = json.loads((output / 'model_json' / 'deusex.pistol_pickupviewmesh.json').read_text())
            third = json.loads((output / 'model_json' / 'deusex.pistol_thirdpersonmesh.json').read_text())
            for a, b in zip(pickup['vertices'], third['vertices']):
                self.assertAlmostEqual(a, b * 2, places=3)
            self.assertTrue(all(m['blend'] == ['gl_one', 'gl_one_minus_src_color'] for m in pickup['material_settings']))
            report = json.loads((output / 'import-report.json').read_text())
            self.assertTrue(any(i['kind'] == 'class' and i['source'] == 'DeusEx.Unreadable' for i in report['issues']))
            self.assertTrue(any(i['kind'] == 'class' and i['source'] == 'DeusEx.BadParent' for i in report['issues']))


INSTALL = Path(os.environ.get('DX_INSTALL', r'C:\Program Files (x86)\Steam\steamapps\common\Deus Ex'))


@unittest.skipUnless((INSTALL / 'System' / 'DeusExCharacters.u').is_file(), 'stock Deus Ex install absent')
class DxInstallTests(unittest.TestCase):
    def test_three_character_meshes_single_package(self):
        package = Package(INSTALL / 'System' / 'DeusExCharacters.u', game='dx')
        self.assertEqual(package.version, 68)
        self.assertIn(package.licensee, (0, 1))
        for name in ('GM_Trench', 'GFM_SuitSkirt', 'GM_DressShirt'):
            mesh = package.mesh(package.export_by_path(('DeusExCharacters', name), 'LodMesh'))
            self.assertEqual(standing_pose(mesh)[1], 'BreatheLight')
            self.assertTrue(mesh['faces'])


if __name__ == '__main__':
    unittest.main()

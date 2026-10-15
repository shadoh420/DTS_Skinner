"""Synthetic UE1 packages only: DX carcass views, BSP/lighting, placed actor policy and routes."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app import app
from test_dx import Fixture, fixture
from test_unreal import index
from tools.import_dx import display_settings, map_actor_kind, map_group, mesh_views
from tools.import_unreal import Library, Package, import_catalog
from tools.import_unreal_map import build_map, import_maps


class MapFixture(Fixture):
    def props(self, values):
        out = b''
        for key, value in values.items():
            if isinstance(value, bytes):
                out += self.name(key) + b'\x3a' + self.name('Vector') + value
            elif isinstance(value, bool):
                out += self.name(key) + bytes([0x53 | (128 if value else 0), 0])
            elif isinstance(value, str):
                data = self.name(value)
                out += self.name(key) + bytes([0x56, len(data)]) + data
            else:
                out += super().props({key: value})[:-1]
        return out + self.name('None')

    def model(self, texture, zone, light, polys=0):
        vec = lambda *v: struct.pack('<' + 'f' * len(v), *v)
        out = self.props({}) + bytes(41)
        out += index(3) + vec(0, 0, 1, 1, 0, 0, 0, 1, 0)  # normal/U/V
        out += index(4) + vec(-64, -64, 0, 64, -64, 0, 64, 64, 0, -64, 64, 0)
        out += index(1) + vec(0, 0, 1, 0) + bytes(9)
        out += b''.join(index(v) for v in (0, 0, -1, -1, -1, -1, -1, 1, 1)) + struct.pack('<Bii', 4, 0, 0)
        out += index(1) + index(texture) + struct.pack('<I', 0x200)  # one U-panning surface
        out += b''.join(index(v) for v in (0, 0, 1, 2, 0, 0)) + bytes(4) + index(0)
        out += index(4) + b''.join(index(v) + index(0) for v in range(4))
        out += struct.pack('<ii', 0, 2) + index(0) + bytes(16) + index(zone) + bytes(16)
        out += index(polys) + index(1) + struct.pack('<i3f', 0, 0, 0, 0) + index(2) * 2
        out += struct.pack('<ffi', 64, 64, 0) + index(2) + b'\x03\x01'
        out += index(0) * 3 + index(2) + index(light) + index(0) + struct.pack('<ii', 1, 0)
        return out

    def polys(self, texture):
        vec = lambda *v: struct.pack('<' + 'f' * len(v), *v)
        out = self.props({}) + struct.pack('<ii', 1, 1) + index(3)
        out += vec(0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 1, 0)
        out += vec(0, 0, 0, 16, 0, 0, 0, 16, 0) + bytes(4)
        return out + index(0) + index(texture) + index(0) * 2 + index(0) + bytes(4)


def make_map(root, water=False, legacy=False):
    system = fixture(root)
    # Real class ancestry without any retail class data or bytecode.
    f = MapFixture()
    exports = [(0, 'Actor', f.cls('Actor', 0, {'DrawType': 2, 'DrawScale': 1.0, 'ScaleGlow': 1.0})),
               (0, 'Decoration', f.cls('Decoration', 1, {})), (0, 'Carcass', f.cls('Carcass', 2, {})),
               (0, 'Pawn', f.cls('Pawn', 1, {})), (0, 'Inventory', f.cls('Inventory', 1, {})),
               (0, 'Mover', f.cls('Mover', 1, {})), (0, 'LevelInfo', f.cls('LevelInfo', 1, {'DrawType': 0})),
               (0, 'ZoneInfo', f.cls('ZoneInfo', 1, {'DrawType': 0, 'AmbientBrightness': 16, 'AmbientSaturation': 255})),
               (0, 'Light', f.cls('Light', 1, {'DrawType': 0, 'LightType': 1, 'LightBrightness': 160,
                                             'LightRadius': 8, 'LightSaturation': 255})),
               (0, 'PlayerStart', f.cls('PlayerStart', 1, {'DrawType': 0})),
               (0, 'Trigger', f.cls('Trigger', 1, {'DrawType': 0}))]
    (system / 'Engine.u').write_bytes(f.package([], exports).replace(struct.pack('<I', 0x70104), struct.pack('<I', 0x70004)))
    f = MapFixture()
    imports = [('Core', 'Package', 0, 'Engine'), ('Core', 'Package', 0, 'DeusExCharacters')]
    def imp(package, cls, name):
        imports.append((package, cls, -1 if package == 'Engine' else -2, name))
        return -len(imports)
    decoration, carcass, pawn, inventory, mover, actor = [imp('Engine', 'Class', n) for n in
                                                       ('Decoration', 'Carcass', 'Pawn', 'Inventory', 'Mover', 'Actor')]
    mesh, red = imp('DeusExCharacters', 'LodMesh', 'Body'), imp('DeusExCharacters', 'Texture', 'Red')
    imports.append(('Core', 'Package', 0, 'DeusExItems'))
    imports.append(('Engine', 'LodMesh', -len(imports), 'Unused'))
    floating = -len(imports)
    classes = [('Crate', decoration, {'Mesh': mesh, 'MultiSkins': red}),
               ('Dead', carcass, {'Mesh': mesh, 'Mesh2': mesh, 'Mesh3': floating, 'MultiSkins': red}),
               ('Guard', pawn, {'Mesh': mesh}), ('Pickup', inventory, {'Mesh': mesh, 'PickupViewMesh': mesh, 'PickupViewScale': .5}),
               ('Door', mover, {}), ('Effect', actor, {'Mesh': mesh})]
    (system / 'DeusEx.u').write_bytes(f.package(imports, [(0, n, f.cls(n, parent, props)) for n, parent, props in classes]).replace(struct.pack('<I', 0x70104), struct.pack('<I', 0x70004)))
    f = MapFixture()
    imports = [('Core', 'Package', 0, 'Engine'), ('Core', 'Package', 0, 'DeusEx'),
               ('Core', 'Package', 0, 'DeusExCharacters')]
    def imp(pkg, cls, name):
        imports.append((pkg, cls, -{'Engine': 1, 'DeusEx': 2, 'DeusExCharacters': 3}[pkg], name))
        return -len(imports)
    level_cls, model_cls, info_cls, zone_cls, light_cls, start_cls, polys_cls, trigger_cls = [imp('Engine', 'Class', n) for n in
        ('Level', 'Model', 'LevelInfo', 'ZoneInfo', 'Light', 'PlayerStart', 'Polys', 'Trigger')]
    crate_cls, dead_cls, guard_cls, pickup_cls, door_cls, effect_cls = [imp('DeusEx', 'Class', n) for n in
                                                                    ('Crate', 'Dead', 'Guard', 'Pickup', 'Door', 'Effect')]
    tex = imp('DeusExCharacters', 'Texture', 'Blue')
    body = imp('DeusExCharacters', 'LodMesh', 'Body')
    vec = lambda *v: struct.pack('<3f', *v)
    refs = [3, 4, 5, 6, 11, 14] if legacy else [3, 4, 5, 6, 7, 8, 9, 10, 11, 14, 15, 16]
    level = f.props({}) + struct.pack('<ii', len(refs), len(refs)) + b''.join(index(r) for r in refs)
    level += (index(1) + b'\0') * 4 + index(0) + bytes(8) + index(2)
    exports = [(level_cls, 'Level', level), (model_cls, 'World', f.model(tex, 4, 5)),
               (info_cls, 'LevelInfo0', f.props({})),
               (zone_cls, 'Zone0', f.props({'bWaterZone': water, 'TexUPanSpeed': 2.0, **({'ViewFog': vec(0, .05, .1)} if water else {})})),
               (light_cls, 'Light0', f.props({'Location': vec(0, 0, 80)})),
               (start_cls, 'PlayerStart0', f.props({'Location': vec(0, 0, 70)})),
               (crate_cls, 'Crate0', f.props({'Location': vec(20, 0, 0), 'DrawScale': .1, 'MultiSkins': tex,
                                           'Skin': tex, 'Rotation': struct.pack('<3i', 0, 16384, 0)})),
               (dead_cls, 'Dead0', f.props({'Location': vec(0, 30, 0), 'DrawScale': .1})),
               (guard_cls, 'Guard0', f.props({'Location': vec(9000, 0, 0)})),
               (pickup_cls, 'Pickup0', f.props({'Location': vec(0, 0, 20), 'DrawScale': .1})),
               (door_cls, 'Door0', f.props({'Brush': 12, 'BasePos': vec(100, 0, 0), 'KeyPos': vec(0, 0, 10),
                                          'KeyNum': 0, 'NumKeys': 2, 'Tag': 'OpenDoor', 'InitialState': 'TriggerToggle'})),
               (model_cls, 'Brush', f.model(tex, 4, 5, polys=13)), (polys_cls, 'Polys', f.polys(tex)),
               (trigger_cls, 'Trigger0', f.props({'Location': vec(0, 0, 70), 'Event': 'OpenDoor'})),
               (crate_cls, 'Hidden0', f.props({'Mesh': body, 'bHidden': True})),
               (effect_cls, 'Effect0', f.props({}))]
    maps = root / 'Maps'; maps.mkdir()
    path = maps / '01_Test.dx'
    path.write_bytes(f.package(imports, exports, licensee=0).replace(struct.pack('<I', 0x70104), struct.pack('<I', 0x70004)))
    if legacy:
        for package in system.glob('*.u'):
            package.write_bytes(package.read_bytes().replace(struct.pack('<I', 0x70104), struct.pack('<I', 0x70004)))
    return path


class DxMapTests(unittest.TestCase):
    def test_unreal_ut_output_matches_pre_dx_snapshot(self):
        # Digest captured from the unmodified 60271c2 importer on this tiny BSP/light/mover fixture.
        # Includes geometry, scene, atlas and textures; the arrival trigger must still open the legacy mover.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = make_map(root, legacy=True)
            for game in ('unreal', 'ut'):
                library = Library(root, game=game)
                if game == 'ut':
                    library.files['botpack'] = root / 'System' / 'DeusEx.u'
                scene, geometry, images, atlas = build_map(library, Package(path, game=game), None, game=game)
                data = json.dumps(scene, sort_keys=True).encode() + geometry + atlas.tobytes()
                for key, image in sorted(images.items()):
                    data += key.encode() + (image.tobytes() if image else b'none')
                self.assertEqual(hashlib.sha256(data).hexdigest(), 'c98fe1655aedb295241a9e2ae91f6747d46b024bcbb0cbe89e66e74c391c0c07')

    def test_carcass_alternates_get_class_looks_and_aliases(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'install'; fixture(root)
            f = Fixture()
            imports = [('Core', 'Package', 0, 'DeusExCharacters'), ('Engine', 'LodMesh', -1, 'Body'),
                       ('Engine', 'Texture', -1, 'Red')]
            exports = [(0, 'DeusExCarcass', f.cls('DeusExCarcass', 0, {})),
                       (0, 'Dead', f.cls('Dead', 1, {'Mesh': -2, 'Mesh2': -2, 'Mesh3': -2, 'MultiSkins': -3}))]
            (root / 'System' / 'DeusEx.u').write_bytes(f.package(imports, exports))
            import_catalog(root, Path(folder) / 'out', 'dx')
            report = json.loads((Path(folder) / 'out' / 'import-report.json').read_text())
            self.assertEqual(next(c['views'] for c in report['classes'] if c['source'] == 'DeusEx.Dead'), ['Mesh', 'Mesh2', 'Mesh3'])
            self.assertTrue({'Mesh2', 'Mesh3'} <= {a['view'] for a in report['aliases'] if a['source'] == 'DeusEx.Dead'})
            self.assertEqual(report['counts']['raw_default_texture_meshes'], 0)

    def test_bsp_lightmaps_mesh_overrides_and_start_mover(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = make_map(root)
            library = Library(root, game='dx'); level = Package(path, game='dx')
            scene, geometry, images, atlas = build_map(library, level, None, game='dx')
            self.assertEqual(scene['actors'], dict(meshes=4, movers=1, unread=0, failed=[], excluded_players=0, npcs=1,
                                                  excluded_other=6, carcasses=1, pickups=1, decorations=1))
            self.assertEqual(scene['polygons'], 1)
            self.assertEqual(scene['missing'], [])
            self.assertEqual(len(geometry), scene['vertices'] * 32 + scene['indices'] * 4)
            self.assertTrue(np.asarray(atlas).std() > 5)
            self.assertTrue(any(g.get('pan') == (128.0, 0) for g in scene['groups']))
            self.assertAlmostEqual(scene['viewpoints'][0]['origin'][1], 87.5 / 52.5, places=3)
            points = np.frombuffer(geometry, '<f4', count=scene['vertices'] * 3).reshape(-1, 3)
            self.assertGreater(points[:, 2].max(), 150)  # The admitted pawn was placed 9000 units away.
            self.assertTrue(any(np.allclose(p, (0, 10 / 52.5, 100 / 52.5)) for p in points))  # mover key 0
            # Crate overrides its class's red slot with map-owned blue, rotates yaw 90, and uses DrawScale .1.
            expected = (-214.8 / 52.5, 70 / 52.5, 220 / 52.5)
            self.assertTrue(any(np.allclose(p, expected, atol=1e-5) for p in points))
            self.assertIn('deusexcharacters.blue.png', images)

    def test_water_carcass_selects_mesh3(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = make_map(root, water=True)
            scene, _, images, _ = build_map(Library(root, game='dx'), Package(path, game='dx'), None, game='dx')
            self.assertEqual(scene['actors']['carcasses'], 1)
            self.assertEqual(scene['actors']['failed'], [])
            self.assertIn('deusexitems.own.png', images)
            # The water zone tints the view (PlayerPawn.ViewFlash: 0.05 and 0.1 kept, over Deus Ex's 0.019).
            self.assertEqual(scene['regions'], [None, {'flash': [1, 1, 1, 0, .05, .1]}])
            self.assertEqual(scene['region_tree'], {'root': -2, 'nodes': []})  # Its one node has zone 1 both sides.

    def test_sections_discovery_import_and_separate_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'install'; path = make_map(root)
            (root / 'Maps' / 'DXRando_Test.dx').write_bytes(b'ignored')
            (root / 'DXRando' / 'Maps').mkdir(parents=True)
            (root / 'DXRando' / 'Maps' / 'Other.dx').write_bytes(b'ignored')
            (root / 'System' / 'DeusEx.ini').write_text('[Engine.Engine]\nGameRenderDevice=GlideDrv.GlideRenderDevice\n[WinDrv.WindowsClient]\nBrightness=0.6\n')
            self.assertEqual(Library(root, game='dx').maps, [path])
            with patch('app.local_data_dir', Path(folder) / 'out'):
                client = app.test_client()
                page = client.get('/maps/dx/')
                self.assertIn(b'data-game="dx"', page.data)
                self.assertIn(b'/static/unreal-maps/viewer.js', page.data)
                self.assertNotIn(b'data-game="dx"', client.get('/maps/unreal/').data)
                self.assertEqual(client.post('/import_dx_maps', json={'path': str(root)}, headers={'Origin': 'https://other'}).status_code, 403)
                self.assertEqual(client.post('/import_dx_maps', json={'path': str(root), 'game': 'ut'}).status_code, 400)
                self.assertEqual(client.post('/import_unreal_maps', json={'path': str(root), 'game': 'dx'}).status_code, 400)
                result = client.post('/import_dx_maps', json={'path': str(root)})
                self.assertEqual(result.status_code, 200, result.json)
                self.assertEqual(result.json['failed'], {})
                self.assertEqual(len(result.json['imported']), 1)
                index_data = client.get('/dx-map-data/index.json').json
                self.assertEqual(index_data[0]['group'], 'Mission 01')
                scene = client.get('/dx-map-data/maps/01_test/scene.json').json
                self.assertAlmostEqual(scene['display']['power'], 2 / 3)
                self.assertEqual(scene['display']['renderer'], 'GlideDrv.GlideRenderDevice')
                self.assertEqual(client.get('/unreal-map-data/dx/index.json').status_code, 404)
            self.assertEqual([map_group(n) for n in ('00_Intro', '15_Area51', '99_Endgame1', 'DXMP_Test', 'Entry')],
                             ['Mission 00', 'Mission 15', 'Endings', 'Multiplayer', 'Other maps'])

    def test_ancestry_and_display_defaults(self):
        self.assertEqual(map_actor_kind(['Carcass', 'Decoration', 'Actor']), 'carcass')
        self.assertEqual(map_actor_kind(['ScriptedPawn', 'Pawn', 'Actor']), 'npc')
        self.assertEqual(map_actor_kind(['PlayerPawnExt', 'PlayerPawn', 'Pawn']), 'npc')
        self.assertEqual(mesh_views({'Mesh': 1, 'Mesh2': 2, 'Mesh3': 3}, ['DeusExCarcass']), ['Mesh', 'Mesh2', 'Mesh3'])
        with tempfile.TemporaryDirectory() as folder:
            self.assertAlmostEqual(display_settings(Path(folder))['power'], 2 / 3)


if __name__ == '__main__':
    unittest.main()

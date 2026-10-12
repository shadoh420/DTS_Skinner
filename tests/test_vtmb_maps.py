"""Tiny synthetic BSP17/MDL/material install. No retail assets or default install scan."""
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

from tools import import_vtmb_map as maps
from tools import vtmb
from tests.test_vtmb import vpk, vtf, mdl_fixture, quantized_fixture, compact_vtx


def bsp(entity_text='', displacement=False, static=True, embedded=None):
    lumps = [b''] * 64
    lumps[0] = ('{ "classname" "worldspawn" "skyname" "test" }\n' + entity_text).encode() + b'\0'
    lumps[1] = struct.pack('<4fi', 0, 0, 1, 0, 2)
    lumps[2] = struct.pack('<3f5i', .5, .5, .5, 0, 16, 16, 16, 16)
    corners = ((0, 0, 0), (0, 16, 0), (16, 16, 0), (16, 0, 0))
    lumps[3] = b''.join(struct.pack('<3f', *p) for p in corners)
    lumps[6] = struct.pack('<16f2i', 1, 0, 0, 0, 0, 1, 0, 0, .0625, 0, 0, 0, 0, .0625, 0, 0, 0, 0)
    face = bytearray(104)
    struct.pack_into('<HBBihhhh', face, 32, 0, 0, 1, 0, 4, 0, 0 if displacement else -1, 0)
    face[48:56] = bytes([0]+[255]*7)
    struct.pack_into('<if4i2I', face, 72, 0, 256, 0, 0, 1, 1, 0, 0)
    lumps[7] = bytes(face)
    lumps[8] = bytes([64, 32, 16, 0])*4
    lumps[12] = b''.join(struct.pack('<2H', i, (i+1)%4) for i in range(4))
    lumps[13] = struct.pack('<4i', 0, 1, 2, 3)
    lumps[14] = struct.pack('<9f3i', 0, 0, 0, 16, 16, 16, 0, 0, 0, 0, 0, 1)
    lumps[43] = b'test/floor\0'; lumps[44] = struct.pack('<i', 0)
    if displacement:
        d = bytearray(176); struct.pack_into('<3f3i', d, 0, 0, 0, 0, 0, 0, 2)
        lumps[26] = bytes(d)
        lumps[33] = b''.join(struct.pack('<5f', 0, 0, 1, 4 if i == 12 else 0, 0) for i in range(25))
        lumps[48] = struct.pack('<32H', *([6]*32))
    if embedded:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, data in embedded.items():
                archive.writestr(name, data)
        lumps[40] = stream.getvalue()
    out = bytearray(1036); out[:8] = b'VBSP\x11\0\0\0'
    for i, data in enumerate(lumps):
        if data:
            struct.pack_into('<4i', out, 8+i*16, len(out), len(data), 0, 0); out.extend(data)
    if static:
        payload = struct.pack('<i128siHi', 1, b'models/items/tiny.mdl', 1, 7, 1)
        payload += struct.pack('<6f3H2Bi5f', 32, 0, 0, 0, 90, 0, 0, 0, 1, 6, 0, 1, 100, 200, 0, 0, 0)
        game = struct.pack('<i4sHHii', 1, b'prps', 0, 4, len(out)+20, len(payload)) + payload
        struct.pack_into('<4i', out, 8+35*16, len(out), len(game), 0, 0); out.extend(game)
    return bytes(out)


def fixture(root):
    stock = root/'Vampire'; stock.mkdir(parents=True)
    mdl, vtx = mdl_fixture()
    assets = [('models/items/tiny.mdl', mdl), ('models/items/tiny.dx80.vtx', vtx)]
    for name, color in (('test/floor', b'\x80\x80\x80\xff'), ('test/red', b'\xff\0\0\xff'), ('test/blue', b'\0\0\xff\x80')):
        assets += [('materials/'+name+'.vtf', vtf(pixels=color)),
                   ('materials/'+name+'.vmt', ('LightmappedGeneric { "$basetexture" "'+name+'" }').encode())]
    for name in ('red', 'blue'):
        assets.append(('materials/models/test/'+name+'.vmt', ('VertexLitGeneric { "$basetexture" "test/'+name+'" }').encode()))
    for suffix in ('rt', 'lf', 'bk', 'ft', 'up', 'dn'):
        assets.append(('materials/skybox/test'+suffix+'.vmt', b'UnlitGeneric { "$basetexture" "test/blue" }'))
    assets.append(('maps/sm_tiny.bsp', bsp()))
    (stock/'pack000.vpk').write_bytes(vpk(assets))
    patch_dir = root/'Unofficial_Patch'/'vdata'/'items'; patch_dir.mkdir(parents=True)
    (patch_dir/'item_g_test.txt').write_text('WeaponData { "playermodel" "models/items/tiny.mdl" }')
    return stock


class BloodlinesMapsTests(unittest.TestCase):
    def test_quantized_prop_authored_companion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); fixture(root)
            files, _ = vtmb.read_install(root)
            raw, vtx = quantized_fixture(2); data = bytearray(raw)
            name = b'items/original.mdl\0'; data[12:12+len(name)] = name
            files['models/items/tiny.mdl'] = maps.EmbeddedAsset(bytes(data), 'synthetic')
            del files['models/items/tiny.dx80.vtx']
            files['models/items/original.dx80.vtx'] = maps.EmbeddedAsset(compact_vtx(vtx), 'synthetic')
            scene, blob, _, _ = maps.build_map(bsp(), 'test', files)
            self.assertFalse(scene['model_failures'])
            prop = scene['models'][0]
            self.assertEqual(prop['pose']['vtx'], 'models/items/original.dx80.vtx')
            self.assertEqual(prop['pose']['skin_family'], 1)
            self.assertEqual(prop['pose']['vertex_encodings'], [2])
            group = next(g for g in scene['groups'] if g['kind'] == 'model')
            indices = np.frombuffer(blob, '<u4', offset=scene['vertices']*28)[group['start']:group['start']+group['count']]
            points = np.frombuffer(blob, '<f4', count=scene['vertices']*3).reshape(-1, 3)[indices]
            # Reader pose X+2, prop yaw90/origin(32,0,0), then page axes and scale.
            self.assertTrue(any(np.allclose(p, np.array([12, 30, 12])/32) for p in points))

    def test_header_faces_and_light(self):
        parsed = maps.read_bsp(bsp(static=False))
        p, uv, lm, tri, flags, name = maps.face_geometry(parsed, 0)
        self.assertEqual(name, 'test/floor'); self.assertEqual(len(parsed['audit']), 64)
        self.assertEqual(len(tri), 2)
        self.assertTrue(np.all(np.cross(p[tri[:, 1]]-p[tri[:, 0]], p[tri[:, 2]]-p[tri[:, 0]])[:, 2] > 0))
        np.testing.assert_allclose(uv, lm)
        parsed['lumps'][8] = bytes([128, 64, 32, 255])*4  # signed exponent -1
        tile = maps.light_pixels(parsed, parsed['faces'][0], 0, gain=1)
        expected = np.rint((np.array([64, 32, 16])/255)**(1/2.2)*255)
        np.testing.assert_array_equal(tile[0, 0], expected)
        parsed['dark_styles'] = [0]
        self.assertFalse(maps.light_pixels(parsed, parsed['faces'][0], 0).any())
        with self.assertRaises(ValueError): maps.read_bsp(b'VBSP\x14\0\0\0')
        damaged = bytearray(bsp()); struct.pack_into('<i', damaged, 8+3*16, len(damaged))
        with self.assertRaises(ValueError): maps.read_bsp(damaged)
        parsed['faces'][0]['first'] = -1
        with self.assertRaises(ValueError): maps.face_geometry(parsed, 0)

    def test_displacement_grid_and_tags(self):
        parsed = maps.read_bsp(bsp(displacement=True, static=False))
        p, uv, lm, tri, _, _ = maps.face_geometry(parsed, 0)
        self.assertEqual((len(p), len(tri)), (25, 32))
        np.testing.assert_allclose(p[12], [8, 8, 4])
        np.testing.assert_allclose(uv[12], [.5, .5])
        parsed['displacements'][0]['vertex'] = 20
        with self.assertRaises(ValueError): maps.face_geometry(parsed, 0)

    def test_sprp_fields_and_rejection(self):
        parsed = maps.read_bsp(bsp()); props, audit = maps.static_props(parsed)
        self.assertEqual(props[0]['leaves'], [7]); self.assertEqual(props[0]['fade'], [100, 200])
        self.assertEqual(props[0]['skin'], 1); self.assertEqual(audit[0]['version'], 4)
        game = bytearray(parsed['lumps'][35]); struct.pack_into('<H', game, 10, 99)
        parsed['lumps'][35] = bytes(game)
        props, audit = maps.static_props(parsed)
        self.assertFalse(props); self.assertIn('Unsupported', audit[0]['reason'])

    def test_brush_start_state(self):
        row = (0, 0, 0, 16, 32, 64)
        r, p = maps.brush_transform(dict(classname='func_door', origin='10 0 0', angles='0 90 0', spawnflags='1', lip='2'), row)
        np.testing.assert_allclose(p, [10, 30, 0]); np.testing.assert_allclose(r, np.eye(3))
        r, p = maps.brush_transform(dict(classname='func_door_rotating', spawnflags='1', distance='90'), row)
        np.testing.assert_allclose(r @ [1, 0, 0], [0, 1, 0], atol=1e-7)
        self.assertTrue(maps.hidden({'starthidden': '1'}))

    def test_embedded_assets_and_precedence(self):
        parsed = maps.read_bsp(bsp(static=False, embedded={'materials/test/local.vmt': b'local', 'materials/test/floor.vmt': b'embedded'}))
        external = maps.EmbeddedAsset(b'patch', 'patch')
        files, audit = maps.map_files(parsed, {'materials/test/floor.vmt': external})
        self.assertEqual(files['materials/test/floor.vmt'].read(), b'patch')
        self.assertEqual(files['materials/test/local.vmt'].read(), b'local')
        self.assertEqual(sum(r['used'] for r in audit), 1)
        parsed = maps.read_bsp(bsp(static=False, embedded={'../escape': b'x'}))
        with self.assertRaises(ValueError): maps.map_files(parsed, {})

    def test_pipeline_patch_pickups_npc_omissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'install'; fixture(root)
            folder = root/'Unofficial_Patch'/'maps'; folder.mkdir()
            entity_text = '''
            { "classname" "info_player_start" "origin" "1 2 3" }
            { "classname" "npc_test" "model" "models/items/tiny.mdl" }
            { "classname" "prop_dynamic" "model" "models/character/test.mdl" }
            { "classname" "prop_dynamic" "model" "models/items/tiny.mdl" "StartHidden" "1" }
            { "classname" "item_g_test" "skin" "1" "origin" "64 0 0" }
            { "classname" "item_g_absent" }
            { "classname" "prop_physics" "model" "models/missing.mdl" }
            { "classname" "func_rotating" "model" "*0" "origin" "16 0 0" }
            '''
            source = bsp(entity_text, displacement=True)
            (folder/'sm_tiny.bsp').write_bytes(source)
            (folder/'ch_extra.bsp').write_bytes(bsp(static=False))
            output = Path(temp)/'out'
            report = maps.import_maps(root, output)
            self.assertFalse(report['failed'], report['failed']); self.assertEqual(report['effective_maps'], 2)
            self.assertEqual(report['inventory']['patch']['.bsp'], {'added': 1, 'replaced': 1})
            scene = json.loads((output/'maps'/maps.key('sm_tiny')/'scene.json').read_text())
            self.assertEqual(scene['skipped_by_reason']['NPC / character actor excluded'], 2)
            self.assertEqual(len(scene['model_failures']), 2)
            self.assertEqual(sum(m.get('status') == 'drawn' for m in scene['models']), 2)
            self.assertTrue(all(m['pose']['skin_family'] == 1 for m in scene['models'] if m.get('status') == 'drawn'))
            self.assertEqual(scene['models'][1]['source'], 'vdata/items/item_g_test.txt')
            blob = (output/'maps'/maps.key('sm_tiny')/'geometry.bin').read_bytes()
            self.assertEqual(len(blob), scene['vertices']*28 + scene['indices']*4)
            ix = np.frombuffer(blob, '<u4', offset=scene['vertices']*28)
            self.assertLess(ix.max(), scene['vertices'])
            self.assertFalse(scene['missing_reasons'], scene['missing_reasons'])
            self.assertEqual((folder/'sm_tiny.bsp').read_bytes(), source)
            self.assertEqual(len(maps.import_maps(root, output)['skipped']), 2)
            with self.assertRaises(ValueError): maps.import_maps(root, root/'bad')

    def test_cfg_and_sections(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); cfg = root/'Unofficial_Patch'/'cfg'; cfg.mkdir(parents=True)
            (cfg/'config.cfg').write_text('brightness "1.25"\nmat_overbright "2"\ncl_v_gamma "1.5"\n')
            profile = maps.lighting_profile(root)
            self.assertEqual(profile['brightness'], 1.25); self.assertAlmostEqual(profile['display_power'], 2/3)
        self.assertEqual(maps.section('sm_pier_1'), 'Santa Monica')
        self.assertEqual(maps.section('test'), 'Misc / test')

    def test_routes(self):
        from app import app
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); install = root/'install'; fixture(install)
            with patch('app.local_data_dir', root/'output'):
                client = app.test_client()
                response = client.post('/import_vtmb_maps', json={'path': str(install)})
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(client.get('/maps/vtmb/').status_code, 200)
                self.assertIn('/vtmb-maps/materials.js', client.get('/maps/vtmb/').text)
                self.assertIn('/maps/vtmb/', client.get('/').text)
                self.assertEqual(client.get('/vtmb-map-data/index.json').status_code, 200)
                self.assertEqual(client.post('/import_vtmb_maps', json={'path': str(install)}, headers={'Origin': 'https://foreign'}).status_code, 403)
                self.assertEqual(client.post('/import_vtmb_maps', json={'path': ''}).status_code, 400)


@unittest.skipUnless(os.environ.get('VTMB_MAP_TEST_FILE'), 'Set VTMB_MAP_TEST_FILE for a single BSP read-only probe')
class OptionalBloodlinesMapTest(unittest.TestCase):
    def test_single_bsp(self):
        parsed = maps.read_bsp(Path(os.environ['VTMB_MAP_TEST_FILE']).read_bytes())
        self.assertTrue(parsed['faces'])
        maps.static_props(parsed)


if __name__ == '__main__':
    unittest.main()

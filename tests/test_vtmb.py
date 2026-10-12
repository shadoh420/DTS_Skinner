"""Synthetic Bloodlines fixtures only; no retail bytes.

Optional single-file install smoke: set VTMB_TEST_INSTALL to the install root.
The default run never reads the installed game and never imports an entire install.
"""
import io
import gzip
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

from tools import vtmb
from tools.import_vtmb import import_catalog, resolve_material
from tools.model_data import load_model_data, MODEL_GAMES
from tools.texture_workshop import TEXTURE_GAMES
from tools.glb_exporter import model_to_glb
from tools.obj_exporter import json_to_obj_zip


def vpk(entries):
    payload, directory = bytearray(), bytearray()
    for name, data in entries:
        raw = name.encode('ascii')
        directory += struct.pack('<I', len(raw)) + raw + struct.pack('<II', len(payload), len(data))
        payload += data
    return bytes(payload + directory + struct.pack('<IIB', len(entries), len(payload), 0))


def vtf(fmt=0, pixels=b'\xff\0\0\xff', width=1, height=1, minor=1, frames=1, mips=1):
    header = bytearray(80 if minor >= 2 else 64)
    struct.pack_into('<4sIIIHHIHH', header, 0, b'VTF\0', 7, minor, len(header), width, height, 0, frames, 65535)
    struct.pack_into('<IBIBB', header, 52, fmt, mips, 0xffffffff, 0, 0)
    if minor >= 2: struct.pack_into('<H', header, 63, 1)
    return bytes(header) + pixels


def mdl_fixture():
    """One weighted bone, two skin families, one bodypart, one triangle.
    Authored idle frame translates X by 2. Embedded vertex positions are bind-space.
    """
    data = bytearray(424)

    def alloc(size):
        at = len(data); data.extend(bytes(size)); return at

    def put(at, fmt, *values): struct.pack_into('<' + fmt, data, at, *values)

    def text(value):
        at = len(data); data.extend(value.encode() + b'\0'); return at

    data[:4] = b'IDST'; put(4, 'iI', 2531, 1234)
    bone = alloc(160); put(240, 'ii', 1, bone)
    put(bone+4, 'i', -1); put(bone+32, '7f', 1, 0, 0, 0, 0, 0, 1)
    put(bone+60, '7f', 1, 1, 1, 1, 1, 1, 1)
    put(bone+88, '12f', 1, 0, 0, -1, 0, 1, 0, 0, 0, 0, 1, 0)
    anim = alloc(72); put(264, 'ii', 1, anim)
    put(anim+8, 'ii', 1, 1)
    sample = alloc(36); put(anim+48, 'i', sample-anim)
    put(sample, 'fi', 1, 32); put(sample+32, 'BBh', 1, 1, 2)
    seq = alloc(764); put(272, 'ii', 1, seq)
    put(seq+52, 'ii', 1, 0)
    textures = alloc(40); put(292, 'ii', 2, textures)
    dirs = alloc(4); put(300, 'ii', 1, dirs)
    skins = alloc(4); put(308, 'iii', 1, 2, skins); put(skins, '2H', 0, 1)
    bp = alloc(16); put(320, 'ii', 1, bp)
    model = alloc(224); put(bp+4, 'iii', 1, 1, model-bp)
    data[model:model+9] = b'tiny.smd\0'
    mesh = alloc(60); vertices = alloc(132)
    put(model+136, '4i', 1, mesh-model, 3, vertices-model)
    put(mesh, '4i', 0, model-mesh, 3, 0)
    for i, xyz in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
        at = vertices+i*44; data[at] = 255
        put(at+12, '8f', *xyz, 0, 0, 1, i/2, 0)
    for at, value in ((bone, 'root'), (anim, '@idle'), (seq, 'idle'), (textures, 'red'), (textures+20, 'blue'), (bp, 'Body')):
        put(at, 'i', text(value)-at)
    put(dirs, 'i', text('models/test/'))
    put(140, 'i', len(data))
    vtx = bytearray(174)
    def vp(at, fmt, *values): struct.pack_into('<'+fmt, vtx, at, *values)
    vp(0, 'iiHHiiiii', 107, 24, 16, 9, 3, 1234, 1, 0, 1)
    vp(32, 'i', 36); vp(36, 'ii', 1, 8); vp(44, 'ii', 1, 8)
    vp(52, 'iif', 1, 12, 0); vp(64, 'HHi', 1, 2, 8)
    vp(72, '4H3i', 3, 3, 1, 10, 20, 56, 62)
    for i in range(3): vp(92+i*12+10, 'H', i)
    vp(128, '3H', 0, 2, 1)
    vp(134, '4HBBHi', 3, 0, 3, 0, 1, 1, 0, 0)
    return bytes(data), bytes(vtx)


def install_fixture(root):
    stock = root/'Vampire'; stock.mkdir()
    raw, vtx = mdl_fixture()
    assets = [('models/items/tiny.mdl', raw), ('models/items/tiny.dx80.vtx', vtx),
              ('materials/models/test/red.vmt', b'VertexLitGeneric { "$basetexture" "test/red" "$alphatest" "1" }'),
              ('materials/models/test/blue.vmt', b'VertexLitGeneric { "$basetexture" "test/blue" "$translucent" "1" }'),
              ('materials/test/red.vtf', vtf()), ('materials/test/blue.vtf', vtf(pixels=b'\0\0\xff\x80')),
              ('models/objects/duplicate.mdl', raw), ('models/objects/broken.mdl', b'bad'),
              ('maps/same.bsp', b'stock map')]
    (stock/'pack000.vpk').write_bytes(vpk(assets))
    return stock


def quantized_fixture(encoding):
    """Three static vertices with independent quantization bias/scale and normals."""
    raw, vtx = mdl_fixture(); data = bytearray(raw)
    bp = vtmb.unpack(data, 'i', 324)[0]; model = bp + vtmb.unpack(data, 'i', bp+12)[0]
    maximum = 65535 if encoding == 1 else 255
    positions = ((0, 0, 0), (maximum, 0, 0), (0, maximum, 0))
    vertex_at = len(data)
    for pos, uv in zip(positions, ((0, 0), (65535, 0), (0, 65535))):
        data.extend(struct.pack('<3H2s2H', *pos, b'\xff\xff', *uv) if encoding == 1 else
                    struct.pack('<3BB2H', *pos, 0xff, *uv))
    normal_at = len(data)
    data.extend(struct.pack('<4f', 0, 0, 1, -1)*3)
    struct.pack_into('<3i', data, model+148, vertex_at-model, normal_at-model, encoding)
    struct.pack_into('<6f', data, model+160, 10, 20, 30,
                     *([2/65535, 3/65535, 4/65535] if encoding == 1 else [2, 3, 4]))
    struct.pack_into('<i', data, 140, len(data))
    return bytes(data), vtx


def compact_vtx(vtx):
    """Rigid strip-group indices: 2-byte originals, with nonzero padding byte."""
    data = bytearray(vtx[:92])
    struct.pack_into('<3H2B3i', data, 72, 3, 3, 1, 0x12, 0xed, 20, 26, 32)
    data.extend(struct.pack('<3H3H', 0, 1, 2, 0, 2, 1))
    data.extend(vtx[134:150])
    return bytes(data)


class BloodlinesTests(unittest.TestCase):
    def test_quantized_vertices_and_rigid_vtx(self):
        for encoding in (1, 2):
            with self.subTest(encoding=encoding):
                raw, vtx = quantized_fixture(encoding)
                result = vtmb.read_mdl(raw, compact_vtx(vtx), 1)
                expected = [20, 30, 12, 20, 30, 14, 23, 30, 12]
                for got, want in zip(result['vertices'], expected): self.assertAlmostEqual(got, want, places=5)
                self.assertEqual(result['uvs'], [0, 0, 1, 0, 0, 1])
                self.assertEqual(result['normals'], [0, 1, 0]*3)
                self.assertEqual(result['indices'], [0, 1, 2])
                self.assertEqual(result['groups'][0]['materialIndex'], 1)
                self.assertEqual(result['metadata']['vertex_encodings'], [encoding])
                self.assertEqual(result['metadata']['vtx_vertex_strides'], [2])
                bad = bytearray(raw); bp = vtmb.unpack(raw, 'i', 324)[0]; model = bp+vtmb.unpack(raw, 'i', bp+12)[0]
                struct.pack_into('<i', bad, model+148, len(raw)-model-1)
                with self.assertRaisesRegex(ValueError, 'binary range'): vtmb.read_mdl(bad, compact_vtx(vtx))
                bad = bytearray(raw); struct.pack_into('<i', bad, model+156, 99)
                with self.assertRaisesRegex(ValueError, 'encoding'): vtmb.read_mdl(bad, vtx)
        raw, vtx = mdl_fixture()
        self.assertEqual(vtmb.read_mdl(raw, compact_vtx(vtx))['vertices'], vtmb.read_mdl(raw, vtx)['vertices'])
        bad = bytearray(compact_vtx(vtx)); struct.pack_into('<H', bad, 92, 3)
        with self.assertRaisesRegex(ValueError, 'original mesh'): vtmb.read_mdl(raw, bad)
        bad = bytearray(compact_vtx(vtx)); bad[78] = 0x1a
        with self.assertRaisesRegex(ValueError, 'layout flags'): vtmb.read_mdl(raw, bad)

    def test_implicit_fourth_weight(self):
        raw, vtx = mdl_fixture(); data = bytearray(raw)
        # Four root bones, each a single independently translated idle track.
        old_bone = vtmb.unpack(data, 'i', 244)[0]; old_anim = vtmb.unpack(data, 'i', 268)[0]
        bone_at = len(data)
        for i in range(4):
            bone = bytearray(data[old_bone:old_bone+160])
            struct.pack_into('<7f', bone, 32, 0, 0, 0, 0, 0, 0, 1)
            struct.pack_into('<12f', bone, 88, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)
            data.extend(bone)
        struct.pack_into('<2i', data, 240, 4, bone_at)
        anim_at = len(data); data.extend(bytes(4*32))
        struct.pack_into('<i', data, old_anim+48, anim_at-old_anim)
        for i in range(4):
            sample = anim_at+i*32; struct.pack_into('<fi', data, sample, 1, len(data)-sample)
            data.extend(struct.pack('<BBh', 1, 1, i*10))
        bp = vtmb.unpack(data, 'i', 324)[0]; model = bp+vtmb.unpack(data, 'i', bp+12)[0]
        vertex = model+vtmb.unpack(data, 'i', model+148)[0]
        data[vertex:vertex+4] = bytes([64, 64, 64, 237])  # padding must not become weight4
        struct.pack_into('<4H', data, vertex+4, 0, 1, 2, 3)
        struct.pack_into('<i', data, 140, len(data))
        result = vtmb.read_mdl(data, vtx)
        self.assertAlmostEqual(result['vertices'][2], (64*10+64*20+63*30)/255)
        bad = bytearray(data); bad[vertex:vertex+3] = bytes([255, 1, 0])
        with self.assertRaisesRegex(ValueError, 'weights'): vtmb.read_mdl(bad, vtx)
        bad = bytearray(data); struct.pack_into('<H', bad, vertex+10, 4)
        with self.assertRaisesRegex(ValueError, 'vertex bone'): vtmb.read_mdl(bad, vtx)

    def test_declared_image_and_authored_companion(self):
        raw, vtx = mdl_fixture()
        self.assertEqual(vtmb.read_mdl(raw+b'\n', vtx)['vertices'], vtmb.read_mdl(raw, vtx)['vertices'])
        self.assertTrue(vtmb.read_mdl(raw+b'\n', vtx)['metadata']['trailing_lf'])
        for trailer in (b'X', b'\0', b'\n\n'):
            with self.assertRaisesRegex(ValueError, 'length mismatch'): vtmb.read_mdl(raw+trailer, vtx)
        empty = bytearray(raw); bp = vtmb.unpack(raw, 'i', 324)[0]; model = bp+vtmb.unpack(raw, 'i', bp+12)[0]
        bad_range = bytearray(raw+b'\n')
        struct.pack_into('<i', bad_range, model+148, len(raw)-model-3*44+1)
        with self.assertRaisesRegex(ValueError, 'binary range'): vtmb.read_mdl(bad_range, vtx)
        struct.pack_into('<i', empty, model+136, 0); struct.pack_into('<i', empty, model+144, 0)
        empty_vtx = bytearray(vtx); struct.pack_into('<i', empty_vtx, 52, 0)
        with self.assertRaisesRegex(ValueError, 'No triangles'): vtmb.read_mdl(empty, empty_vtx)
        data = bytearray(raw); authored = b'items/original.mdl\0'; data[12:12+len(authored)] = authored
        files = {'models/items/copy.dx80.vtx': None, 'models/items/original.dx80.vtx': None}
        self.assertEqual(vtmb.mdl_companions(data, 'models/items/copy.mdl', files), list(files))
        bad = bytearray(vtx); struct.pack_into('<I', bad, 16, 99)
        with self.assertRaisesRegex(ValueError, 'mismatched'): vtmb.read_mdl(data, bad)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); stock = root/'install'/'Vampire'; stock.mkdir(parents=True)
            # Named companion is bad; the authored candidate must still match all structures.
            (stock/'pack000.vpk').write_bytes(vpk([('models/items/copy.mdl', bytes(data)),
                ('models/items/copy.dx90.vtx', bytes(bad)), ('models/items/original.dx80.vtx', vtx)]))
            report = import_catalog(root/'install', root/'out')
            self.assertEqual(report['ready'], 2)
            self.assertEqual(report['results'][0]['vtx'], 'models/items/original.dx80.vtx')
            self.assertEqual(len(report['results'][0]['companion_failures']), 1)

    def test_archive_and_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); stock = install_fixture(root)
            (stock/'pack100.vpk').write_bytes(vpk([('materials/test/red.vtf', vtf(pixels=b'\0\xff\0\xff')), ('models/empty.mdl', b'')]))
            (stock/'pack011.vpk').write_bytes(vpk([('models', b'')])[:-1] + b'\x01')
            patchdir = root/'Unofficial_Patch'
            (patchdir/'materials/test').mkdir(parents=True)
            (patchdir/'materials/test/red.vtf').write_bytes(vtf(pixels=b'\xff\xff\0\xff'))
            (patchdir/'maps').mkdir(); (patchdir/'maps/same.bsp').write_bytes(b'patch map')
            (patchdir/'maps/new.bsp').write_bytes(b'new map')
            # Unrelated user data must never be indexed.
            (patchdir/'save').mkdir(); (patchdir/'save/secret.mdl').write_bytes(b'private')
            files, report = vtmb.read_install(root)
            self.assertEqual(files['maps/same.bsp'].read(), b'patch map')
            self.assertEqual(vtmb.read_texture(files, 'test/red')[0].getpixel((0, 0)), (255, 255, 0, 255))
            self.assertEqual(report['patch']['.bsp'], dict(added=1, replaced=1))
            self.assertEqual(report['archives']['pack011.vpk']['directories'], 1)
            self.assertIn('models/empty.mdl', files)
            self.assertEqual(files['models/empty.mdl'].read(), b'')
            self.assertFalse(any(n.startswith('save/') for n in files))
            bad = stock/'bad.vpk'; bad.write_bytes(vpk([('../escape', b'x')]))
            with self.assertRaisesRegex(ValueError, 'Unsafe'): vtmb.read_vpk(bad)
            bad.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError, 'footer'): vtmb.read_vpk(bad)
            blob = bytearray(vpk([('x', b'x')])); struct.pack_into('<I', blob, 6, 999)
            bad.write_bytes(blob)
            with self.assertRaises(ValueError): vtmb.read_vpk(bad)

    def test_vmt_and_patch(self):
        vmt = vtmb.read_vmt(b'// header\n"VertexLitGeneric" { "$basetexture" "a\\b" "$alphatest" "1" "$additive" "1" "$selfillum" "1" "$envmap" "env_cubemap" Proxies { Sine { resultVar "$alpha" } } }')
        settings, notes = vtmb.material_settings(vmt)
        self.assertEqual(vmt['params']['$basetexture'], 'a\\b')
        self.assertEqual(settings['alphaFunc'], 'GE128')
        self.assertEqual(settings['blend'], ['gl_src_alpha', 'gl_one'])
        self.assertTrue(settings['selfillum']); self.assertTrue(any('$envmap' in n for n in notes))
        with self.assertRaises(ValueError): vtmb.read_vmt(b'X { key }')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = root/'a'; a.write_bytes(b'VertexLitGeneric { "$basetexture" "old" }')
            b = root/'b'; b.write_bytes(b'patch { include "materials/a.vmt" replace { "$basetexture" "new" } insert { "$alphatest" "1" } }')
            files = {'materials/'+p.name+'.vmt': vtmb.Asset(p, 0, p.stat().st_size, 'test') for p in (a, b)}
            parsed, _ = resolve_material(files, 'b', [])
            self.assertEqual(parsed['params']['$basetexture'], 'new')
            self.assertEqual(parsed['params']['$alphatest'], '1')

    def test_vtf_pixels_and_mips(self):
        for fmt, raw, expected in [(0, b'\x11\x22\x33\x44', (17, 34, 51, 68)),
                                    (3, b'\x33\x22\x11', (17, 34, 51, 255)),
                                    (12, b'\x33\x22\x11\x44', (17, 34, 51, 68))]:
            image, info = vtmb.read_vtf(vtf(fmt, raw))
            self.assertEqual(image.getpixel((0, 0)), expected)
        color = struct.pack('<HHI', 0xf800, 0x07e0, 0)
        for fmt, block in ((13, color), (14, b'\xff'*8+color), (15, b'\xff\x00'+b'\0'*6+color)):
            image, _ = vtmb.read_vtf(vtf(fmt, block, 4, 4))
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
        pixels = b'\0\0\xff\xff'*2 + b'\xff\0\0\xff'*4 + b'\0\xff\0\xff'*4
        image, info = vtmb.read_vtf(vtf(0, pixels, 2, 2, frames=2, mips=2))
        self.assertEqual(image.getpixel((1, 1)), (255, 0, 0, 255))
        self.assertEqual(info['frames'], 2)
        with self.assertRaises(ValueError): vtmb.read_vtf(vtf()[:-1])
        with self.assertRaisesRegex(ValueError, 'format'): vtmb.read_vtf(vtf(999))
        resource = bytearray(vtf(minor=3)[:80]) + b'\x30\0\0\0' + struct.pack('<I', 88) + b'\xff\0\0\xff'
        struct.pack_into('<I', resource, 12, 88); struct.pack_into('<I', resource, 68, 1)
        self.assertEqual(vtmb.read_vtf(resource)[0].getpixel((0, 0)), (255, 0, 0, 255))

    def test_split_texture(self):
        # Two mip levels: one resident pixel, four streamed pixels.
        low = b'\0\0\xff\xff'; high = b'\xff\0\0\xff'*4
        full = vtf(0, low+high, 2, 2, mips=2)
        compressed = zlib.compress(high)
        header = struct.pack('<4sHBBI', b'TTH\0', 1, 2, 1, 68)
        header += struct.pack('<6I', 64, 0, 68, 0, 84, len(compressed))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); tth = root/'x.tth'; ttz = root/'x.ttz'
            tth.write_bytes(header+full[:68]); ttz.write_bytes(compressed)
            files = {'materials/x'+p.suffix: vtmb.Asset(p, 0, p.stat().st_size, 'test') for p in (tth, ttz)}
            image, info = vtmb.read_texture(files, 'x')
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
            self.assertEqual(info['container'], 'TTH/TTZ')
            del files['materials/x.ttz']
            with self.assertRaisesRegex(ValueError, 'Missing TTZ'): vtmb.read_texture(files, 'x')

    def test_mdl_pose_skins_and_rejections(self):
        raw, vtx = mdl_fixture()
        mesh = vtmb.read_mdl(raw, vtx)
        self.assertEqual(mesh['metadata']['pose'], 'idle')
        self.assertEqual(mesh['vertices'][:3], [0, 0, 2])
        self.assertEqual(mesh['indices'], [0, 1, 2])
        self.assertEqual(mesh['skin_table'], [[0], [1]])
        self.assertEqual(vtmb.read_mdl(raw, vtx, 1)['groups'][0]['materialIndex'], 1)
        bind = raw.replace(b'idle\0', b'walk\0')
        self.assertEqual(vtmb.read_mdl(bind, vtx)['metadata']['pose'], 'bind')
        self.assertEqual(vtmb.read_mdl(bind, vtx)['vertices'][:3], [0, 0, 0])
        broken = bytearray(vtx); struct.pack_into('<I', broken, 16, 987)
        with self.assertRaisesRegex(ValueError, 'mismatched'): vtmb.read_mdl(raw, broken)
        broken = bytearray(vtx); struct.pack_into('<H', broken, 102, 99)
        with self.assertRaisesRegex(ValueError, 'original mesh'): vtmb.read_mdl(raw, broken)
        broken = bytearray(raw); struct.pack_into('<i', broken, 4, 48)
        with self.assertRaisesRegex(ValueError, 'version'): vtmb.read_mdl(broken, vtx)
        with self.assertRaises(ValueError): vtmb.read_mdl(raw[:-1], vtx)

    def test_default_bodygroups_strips_and_nonfinite(self):
        raw, vtx = mdl_fixture()
        # The default remains model zero; an unused alternative is never substituted.
        extra = bytearray(raw); bp = vtmb.unpack(raw, 'i', 324)[0]
        struct.pack_into('<i', extra, bp+4, 2)
        alternate = bytearray(vtx); struct.pack_into('<i', alternate, 36, 2)
        self.assertEqual(vtmb.read_mdl(extra, alternate)['vertices'], vtmb.read_mdl(raw, vtx)['vertices'])
        strip = bytearray(vtx); strip[143] = 2
        self.assertEqual(vtmb.read_mdl(raw, strip)['indices'], [0, 1, 2])
        bad = bytearray(raw); bone = vtmb.unpack(raw, 'i', 244)[0]
        struct.pack_into('<f', bad, bone+88, float('nan'))
        with self.assertRaisesRegex(ValueError, 'Nonfinite'): vtmb.read_mdl(bad, vtx)
        bad = bytearray(raw); model = bp+vtmb.unpack(raw, 'i', bp+12)[0]
        struct.pack_into('<i', bad, model+128, 2)
        with self.assertRaisesRegex(ValueError, 'model type 2'): vtmb.read_mdl(bad, vtx)

    def test_catalog_and_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); install = root/'install'; install.mkdir(); install_fixture(install)
            output = root/'output'
            report = import_catalog(install, output)
            self.assertEqual((report['models'], report['ready'], report['skins'], report['duplicates'], report['skipped']), (3, 2, 2, 1, 1))
            self.assertEqual(report['unresolved_materials'], 0)
            duplicate = next(r for r in report['results'] if r['status'] == 'duplicate')
            self.assertTrue((output/'model_json'/(duplicate['model']+'.json')).is_file())
            self.assertIn('vtmb', MODEL_GAMES); self.assertIn('vtmb', TEXTURE_GAMES)
            catalog = json.loads((output/'catalog.json').read_text())
            for item in (c for c in catalog if c['status'] == 'ready'):
                path = output/'model_json'/(item['model_name']+'.json')
                data = load_model_data(path)
                self.assertEqual(data['vertices'][:3], [0, 0, 2])
                glb = model_to_glb(data, output/'textures')
                self.assertEqual(glb[:4], b'glTF')
                length = struct.unpack_from('<I', glb, 12)[0]
                doc = json.loads(glb[20:20+length])
                self.assertNotIn('animations', doc)
                self.assertEqual(doc['materials'][0]['alphaMode'], 'MASK')
                self.assertEqual(doc['materials'][1]['alphaMode'], 'BLEND')
                buf = io.BytesIO(); json_to_obj_zip(path, output/'textures', buf, item['model_name'])
                with zipfile.ZipFile(buf) as archive:
                    obj = next(n for n in archive.namelist() if n.endswith('.obj'))
                    self.assertIn('v 0.000000 0.000000 2.000000', archive.read(obj).decode())
            png = next((output/'textures').glob('materials*.png'), None) or next(p for p in (output/'textures').glob('*.png') if p.name != 'missing_vtmb.png')
            png.write_bytes(b'edited png sentinel')
            import_catalog(install, output)
            self.assertEqual(png.read_bytes(), b'edited png sentinel')
            with self.assertRaisesRegex(ValueError, 'outside'): import_catalog(install, install/'output')

    def test_missing_material_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); stock = root/'Vampire'; stock.mkdir()
            raw, vtx = mdl_fixture()
            (stock/'pack000.vpk').write_bytes(vpk([('models/hands/tiny.mdl', raw), ('models/hands/tiny.dx80.vtx', vtx)]))
            # Keep every test-created file inside its plain tempfile directory.
            with tempfile.TemporaryDirectory() as out:
                report = import_catalog(root, Path(out))
                self.assertEqual(report['ready'], 2)
                self.assertEqual(report['unresolved_materials'], 2)
                self.assertTrue(all('Missing VMT' in r['reason'] for r in report['material_results']))

    def test_routes(self):
        from app import app
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); install = root/'install'; install.mkdir(); install_fixture(install)
            output = root/'output'
            with patch('app.pack_dirs', {'vtmb': output}), patch('app.app_local_data_dir', root/'cache'):
                client = app.test_client()
                response = client.post('/import_vtmb', json={'path': str(install)})
                self.assertEqual(response.status_code, 200, response.json)
                catalog = client.get('/list_models?game=vtmb').json
                name = next(c['model_name'] for c in catalog if c['status'] == 'ready')
                self.assertEqual(client.get('/model_json/'+name+'?game=vtmb').json['game'], 'vtmb')
                self.assertTrue(client.get('/list_textures?game=vtmb').json)
                self.assertEqual(client.get('/export_obj/'+name+'?game=vtmb').status_code, 200)
                cache = root/'cache/animations/vtmb'; cache.mkdir(parents=True)
                # Bloodlines exports must always use the shown static geometry.
                (cache/(name+'.json.gz')).write_bytes(gzip.compress(b'not animation JSON'))
                glb = client.get('/export_glb/'+name+'?game=vtmb')
                self.assertEqual(glb.status_code, 200)
                self.assertEqual(glb.headers['X-Skinner-Animation-Clips'], '0')
                page = client.get('/').get_data(as_text=True)
                self.assertIn('id="vtmbImport"', page)
                self.assertIn('<option value="vtmb">Bloodlines</option>', page)
                self.assertEqual(client.post('/import_vtmb', json={'path': str(install)}, headers={'Origin': 'https://outside'}).status_code, 403)


@unittest.skipUnless(os.environ.get('VTMB_TEST_INSTALL'), 'Set VTMB_TEST_INSTALL for optional single-file install smoke')
class OptionalInstallTests(unittest.TestCase):
    def test_patch_projector(self):
        root = Path(os.environ['VTMB_TEST_INSTALL'])
        path = root/'Unofficial_Patch/models/objects/projector.mdl'
        if not path.is_file(): self.skipTest('Patch projector absent')
        mesh = vtmb.read_mdl(path.read_bytes(), path.with_suffix('.dx80.vtx').read_bytes())
        self.assertGreater(len(mesh['indices']), 0)
        self.assertEqual(mesh['metadata']['version'], 2531)


if __name__ == '__main__':
    unittest.main()

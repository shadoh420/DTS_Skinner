"""Synthetic formats/routes plus optional full retail-install accounting."""
from collections import Counter
import io
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app import app
from tests.test_quake2 import pcx
from tools.import_daikatana import (build_model, category, choose_skin, decompress, import_catalog,
                                   pak_directory, read_asset, read_dkm, read_install, read_pak, read_texture, read_wal)
from tools.model_data import load_model_data

INSTALL = Path('C:/Program Files (x86)/Steam/steamapps/common/Daikatana/data')
PALETTE = bytes(c for i in range(256) for c in (i, 255 - i, 17))


def literals(raw):
    return b''.join(bytes([len(raw[i:i + 64]) - 1]) + raw[i:i + 64] for i in range(0, len(raw), 64)) + b'\xff'


def pak(entries):
    payload, directory = bytearray(), bytearray()
    for name, raw, compressed in entries:
        encoded = literals(raw) if compressed else raw
        directory.extend(struct.pack('<56s4i', name.encode(), 12 + len(payload), len(raw), len(encoded), int(compressed)))
        payload.extend(encoded)
    return struct.pack('<4s2i', b'PACK', 12 + len(payload), len(directory)) + payload + directory


def dkm(version=1, old=False, x=2):
    skins = b''.join(struct.pack('<64s', name) for name in (b'skins/head.bmp', b'skins/body.bmp'))
    st = struct.pack('<8h', 0, 0, 1, 0, 0, 1, 3, 1)
    triangles = struct.pack('<16H', 0, 1, 0, 1, 2, 0, 1, 2, 1, 1, 0, 2, 1, 3, 2, 1)
    points = [(x, 1, 5), (x, 5, 1), (x, 1, 1)]

    def frame(name, scale, translation):
        vertices = b''.join(bytes((*p, 0)) if version == 1 else struct.pack('<IB', p[0] << 21 | p[1] << 11 | p[2], 0) for p in points)
        return struct.pack('<6f16s', *scale, *translation, name) + vertices + (b'\0' * 3 if version == 2 else b'')

    first = frame(b'first', (2, 3, 4), (10, -20, 30))
    frames = first + frame(b'later', (7, 8, 9), (99, 98, 97))
    surfaces = struct.pack('<32s5i32s5i', b'head', 0, 0, 4, 2, 1, b'body', 0, 1, 8, 4, 1)
    sequences = b'' if old else struct.pack('<16s2i', b'idle', 0, 1)
    skin_at = 80 if old else 88
    st_at = skin_at + len(skins)
    tri_at = st_at + len(st)
    frame_at = tri_at + len(triangles)
    cmd_at = frame_at + len(frames)
    sequence_at = cmd_at + len(surfaces)
    end = sequence_at + len(sequences)
    header = struct.pack('<4si3f15i', b'DKMD', version, 0, 0, -24, len(first), 2, 3, 4, 2, 0, 2, 2,
                         skin_at, st_at, tri_at, frame_at, cmd_at, cmd_at, end)
    return header + (b'' if old else struct.pack('<2i', 1, sequence_at)) + skins + st + triangles + frames + surfaces + sequences


def wal(version=3):
    header = bytearray(892 if version == 3 else 124 if version == 2 else 100)
    width, height = 8, 8
    sizes = [64, 16, 4, 4] if version else [64, 16, 4, 1]
    offset, offsets = len(header), []
    for size in sizes:
        offsets.append(offset)
        offset += size
    if version:
        header[0] = version
        header[1:5] = b'test'
        struct.pack_into('<2I9I', header, 36, width, height, *offsets, *([0] * 5))
        if version == 3:
            header[120:888] = PALETTE
            header[888:892] = b'tail'  # Outside the native palette, not its last colour.
    else:
        header[:4] = b'test'
        struct.pack_into('<6I', header, 32, width, height, *offsets)
    return bytes(header) + bytes([7] * sum(sizes))


def command_dkm(version=1, count=4, invalid_uv=False):
    raw = bytearray(dkm(version))
    cmd_at, surface_at = struct.unpack_from('<2i', raw, 68)
    corners = [(0, .125, .25), (1, .375, .25), (2, .125, .75), (0, .5, .5)]
    if invalid_uv:
        corners[0] = (0, float('inf'), float('inf'))
    commands = struct.pack('<3i', count, 1, 0) + b''.join(struct.pack('<iff', *c) for c in corners) + bytes(4)
    raw[cmd_at:cmd_at] = commands
    struct.pack_into('<i', raw, 40, len(commands) // 4)
    for offset in (72, 76, 84):
        struct.pack_into('<i', raw, offset, struct.unpack_from('<i', raw, offset)[0] + len(commands))
    # Stale table values must not override the valid drawing instructions.
    struct.pack_into('<3i', raw, surface_at + len(commands) + 36, 10, 0, 0)
    tri_at = struct.unpack_from('<i', raw, 60)[0]
    struct.pack_into('<H', raw, tri_at, 2)  # Kage's out-of-range surface.
    return raw


def bitmap(fmt='BMP'):
    im = Image.frombytes('P', (8, 8), bytes([7] * 64))
    im.putpalette(PALETTE)
    if fmt == 'TGA':
        im = im.convert('RGBA')
    stream = io.BytesIO()
    im.save(stream, format=fmt)
    return stream.getvalue()


class DaikatanaTest(unittest.TestCase):
    def test_pack_and_all_compression_opcodes(self):
        self.assertEqual(decompress(b'\x01AB\x40\x80Z\xc4\x02\xff', 12), b'AB\0\0ZZ\0\0ZZ\0\0')
        for code, expected in ((0, b'A'), (63, b'A' * 64), (64, bytes(2)), (127, bytes(65)), (128, b'A' * 2), (191, b'A' * 65)):
            raw = bytes([code]) + (expected if code < 64 else b'A' if code >= 128 else b'')
            self.assertEqual(decompress(raw, len(expected)), expected)
        self.assertEqual(decompress(b'\x01ab\xfe\x00\xff', 66), b'ab' * 33)
        self.assertEqual(read_pak(pak([('MODELS\\a.dkm', b'DKMD', True), ('skins/x.bmp', b'xyz', False)])),
                         [('models/a.dkm', b'DKMD'), ('skins/x.bmp', b'xyz')])
        for raw, length in ((b'\x01a', 2), (b'\x80', 2), (b'\xc0\x00', 2), (b'\xff', 1), (b'\x40', 1), (b'\xffx', 0)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decompress(raw, length)
        for raw in (b'', pak([('a', b'abc', False)])[:-1], b'NOPE' + bytes(8)):
            with self.assertRaises(ValueError):
                read_pak(raw)
        raw = bytearray(pak([('a', b'abc', False)]))
        struct.pack_into('<i', raw, len(raw) - 4, 2)
        with self.assertRaises(ValueError):
            read_pak(raw)

    def test_first_pose_surfaces_and_proper_rotation(self):
        for version in (1, 2):
            mesh = read_dkm(dkm(version))
            self.assertEqual(mesh['points'], [(14, -17, 50), (14, -5, 34), (14, -17, 34)])
            self.assertEqual(mesh['origin'], (0, 0, -24))  # Not added a second time.
            self.assertEqual(mesh['sequences'], [dict(name='idle', first=0, last=1)])
            model = build_model(mesh, ['head.png', 'body.png'])
            self.assertEqual(model['vertices'][:9], [-17, 50, 14, -17, 34, 14, -5, 34, 14])
            self.assertEqual(model['indices'], [0, 1, 2, 3, 4, 5])
            self.assertEqual(model['material_textures'], ['head.png', 'body.png'])
            self.assertEqual([g['materialIndex'] for g in model['groups']], [0, 1])
            self.assertEqual(model['uvs'][:6], [.125, .25, .125, .75, .375, .25])
            self.assertEqual(model['uvs'][6:8], [.4375, .375])  # Body has different atlas dimensions.
            a, b, c = [model['vertices'][i * 3:i * 3 + 3] for i in range(3)]
            self.assertGreater((b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]), 0)
        self.assertEqual(read_dkm(dkm(old=True))['sequences'], [])
        self.assertEqual(read_dkm(dkm(2, x=1500))['points'][0][0], 3010)  # All eleven X bits.

    def test_malformed_dkm(self):
        bad = [b'', dkm()[:-1]]
        for offset, value in ((4, 3), (20, 1), (24, -1), (28, 0), (52, 1), (64, 999999), (80, -1)):
            raw = bytearray(dkm())
            struct.pack_into('<i', raw, offset, value)
            bad.append(raw)
        raw = bytearray(dkm())
        tri_at = struct.unpack_from('<i', raw, 60)[0]
        for offset in (0, 4, 10):
            copy = bytearray(raw)
            struct.pack_into('<H', copy, tri_at + offset, 999)
            bad.append(copy)
        frame_at = struct.unpack_from('<i', raw, 64)[0]
        struct.pack_into('<f', raw, frame_at + 52, float('nan'))
        bad.append(raw)
        for raw in bad:
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                read_dkm(raw)

    def test_animated_uv_triangle_stride(self):
        raw = bytearray(dkm())
        tri_at = struct.unpack_from('<i', raw, 60)[0]
        struct.pack_into('<H', raw, tri_at + 2, 2)
        raw[tri_at + 16:tri_at + 16] = struct.pack('<3H', 3, 2, 1)
        for offset in (64, 68, 72, 76, 84):
            struct.pack_into('<i', raw, offset, struct.unpack_from('<i', raw, offset)[0] + 6)
        mesh = read_dkm(raw)
        self.assertEqual(mesh['triangle_uv_frames'], [1, 2])
        self.assertEqual(mesh['triangles'][0], (0, 2, 0, 1, 2, 0, 1, 2))
        self.assertEqual(mesh['triangles'][1][0], 1)
        self.assertEqual(build_model(mesh, ['a.png', 'b.png'])['material_textures'], ['a.png', 'b.png'])

    def test_native_gl_strips_fans_and_skin_assignment(self):
        for version in (1, 2):
            for count, indices in ((4, [0, 1, 2, 1, 3, 2]), (-4, [0, 1, 2, 0, 3, 1])):
                mesh = read_dkm(command_dkm(version, count))
                model = build_model(mesh, ['head.png', 'body.png'])
                self.assertEqual(mesh['surfaces'][0]['skin'], 10)
                self.assertEqual(model['material_textures'], ['body.png'])
                self.assertEqual(model['indices'], indices)
                self.assertEqual(model['vertices'][:9], [-17, 50, 14, -17, 34, 14, -5, 34, 14])
                self.assertEqual(model['uvs'][:6], [.125, .25, .125, .75, .375, .25])
                self.assertEqual(model['metadata']['geometry_source'], 'GL commands')
        raw = command_dkm()
        at = struct.unpack_from('<i', raw, 68)[0]
        for offset, value in ((0, 99999), (4, 10), (8, 2), (12, 3), (60, 1)):
            bad = bytearray(raw)
            struct.pack_into('<i', bad, at + offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                read_dkm(bad)

    def test_native_undefined_uvs_keep_geometry_with_warning(self):
        for version in (1, 2):
            model = build_model(read_dkm(command_dkm(version, invalid_uv=True)), ['a.png', 'b.png'])
            self.assertEqual(len(model['indices']), 6)
            self.assertTrue(all(math.isfinite(v) for v in model['uvs']))
            self.assertEqual(model['material_textures'], ['missing_skin.png'])
            self.assertEqual(model['metadata']['undefined_uvs'], 1)
            self.assertIn('undefined sampling', model['metadata']['warnings'][0])
            json.dumps(model, allow_nan=False)

    def test_wal_formats_palettes_and_ranges(self):
        for version in (0, 2, 3):
            image = read_wal(wal(version), PALETTE if version != 3 else None)
            self.assertEqual(image.size, (8, 8))
            self.assertEqual(image.getpixel((0, 0)), (7, 248, 17))
        for raw in (b'', wal()[:955], wal(0)[:-1]):
            with self.assertRaises(ValueError):
                read_wal(raw, PALETTE)
        with self.assertRaises(ValueError):
            read_wal(wal(0))

    def test_wal_non_power_of_two_clamped_mips_and_irregular_tail(self):
        # All six failed retail layouts, without any game bytes.
        for width, height, sizes in ((2279, 20, [45580, 1024, 256, 64, 16, 8, 4, 2, 2]),
                                     (1280, 12, [15360, 512, 128, 32, 16, 8, 4, 2, 2]),
                                     (489, 317, [155013, 16384, 4096, 1024, 256, 64, 16, 4, 3]),
                                     (704, 128, [90112, 8192, 2048, 512, 128, 32, 8, 2, 2])):
            raw = bytearray(wal()[:892])
            offsets, at = [], 892
            for size in sizes:
                offsets.append(at)
                at += size
            struct.pack_into('<2I9I', raw, 36, width, height, *offsets)
            raw += bytes([7]) * sum(sizes)
            image = read_wal(raw)
            self.assertEqual(image.size, (width, height))
            self.assertEqual(image.getpixel((width - 1, height - 1)), (7, 248, 17))
            # The renderer needs only mip0; it ignores lower mip offsets/data.
            self.assertEqual(read_wal(raw[:892 + width * height]).tobytes(), image.tobytes())
            with self.assertRaises(ValueError):
                read_wal(raw[:891 + width * height])

    def test_skin_lookup_order(self):
        files = dict.fromkeys(('skins/head.bmp', 'skins/head.wal', 'skins/head.tga', 'models/e1/body.pcx'))
        self.assertEqual(choose_skin('models/e1/a.dkm', 'SKINS\\head.bmp', files)[0], 'skins/head.wal')
        self.assertEqual(choose_skin('models/e1/a.dkm', 'skins/head.tga', files), ('skins/head.tga', ''))
        self.assertEqual(choose_skin('m.dkm', 'skins/head', files)[0], 'skins/head.wal')
        self.assertEqual(choose_skin('m.dkm', 'skins/head.psd', files)[0], 'skins/head.wal')
        del files['skins/head.wal']
        self.assertEqual(choose_skin('m.dkm', 'skins/head.bmp', files), ('skins/head.bmp', ''))
        del files['skins/head.bmp']
        self.assertEqual(choose_skin('m.dkm', 'skins/head.bmp', files)[0], '')
        self.assertEqual(choose_skin('m.dkm', 'models/e1/body.pcx', files)[0], 'models/e1/body.pcx')
        self.assertEqual(choose_skin('models/e1/a.dkm', 'lost/body.bmp', files)[0], '')
        self.assertEqual(choose_skin('models/e1/a.dkm', 'lost/head.bmp', files)[0], '')
        files.update({'models/kingg/cape.wal': None, 'skins/m_medusa.wal': None, 'skins/head.wal': None})
        self.assertEqual(choose_skin('m.dkm', 'models/kingg/cape.bmp', files)[0], 'models/kingg/cape.wal')
        self.assertEqual(choose_skin('m.dkm', 'skins/d2_medusa.bmp', files)[0], '')  # No guessed alias.
        self.assertEqual(choose_skin('m.dkm', 'skins/head.extra.bmp', files)[0], 'skins/head.wal')
        del files['skins/head.tga']
        self.assertEqual(choose_skin('m.dkm', 'skins/head.tga', files)[0], '')  # No WAL substitution for TGA.
        self.assertIn('checker', choose_skin('m.dkm', '', files)[1])

    def test_layering_library_dedupe_routes_and_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / 'data', Path(tmp) / 'out'
            root.mkdir()
            (root / 'pak1.pak').write_bytes(pak([
                ('models/e1/m_test.dkm', dkm(x=3), True), ('models/bad.dkm', b'DKMD', False),
                ('models/e1/sprite.sp2', b'IDS2', False), ('models/e1/descript.ion', b'notes', False),
                ('models/e1/we_glow.tga', bitmap('TGA'), False),
                ('skins/head.wal', wal(), True), ('skins/head.tga', bitmap('TGA'), False),
                ('skins/body.bmp', bitmap(), False), ('skins/body.wal', b'broken', False), ('skins/alt.pcx', pcx(), False),
                ('pics/colormap.bmp', bitmap(), False), ('skins/old.wal', wal(0), False)]))
            (root / 'pak3.pak').write_bytes(pak([('models/e1/m_test.dkm', dkm(2), False),
                                               ('skins/odd.dkm', dkm(old=True), False)]))
            (root / 'models/e1').mkdir(parents=True)
            (root / 'models/e1/m_test.dkm').write_bytes(dkm())
            (root / 'models/e1/alias.dkn').write_bytes(dkm())
            (root / 'models/e1/e1decoinfo.csv').write_text('test decoration,models/e1/m_test.dkm,none,bbox,300,0,20,-1,-2,-3,1,2,3\n')
            files, records, counts = read_install(root.parent)
            self.assertEqual(read_asset(files['models/e1/m_test.dkm']), dkm())
            self.assertEqual(counts['pak1.pak']['extensions']['.dkm'], 2)
            self.assertEqual(read_texture('skins/old.wal', files)[1], 'pics/colormap.bmp')
            self.assertIn('PCX palette', read_texture('skins/alt.pcx', files)[1])
            with patch.dict('app.pack_dirs', daikatana=output):
                client = app.test_client()
                page = client.get('/').get_data(as_text=True)
                self.assertIn('id="daikatanaImport"', page)
                self.assertIn('value="daikatana">Daikatana textures', page)
                response = client.post('/import_daikatana', json={'path': str(root)})
                self.assertEqual(response.status_code, 200, response.json)
                report = response.json
                self.assertEqual((report['ready'], report['duplicates'], report['overridden'], report['dkm_skipped']), (2, 1, 2, 1))
                self.assertTrue(all(r['status'] != 'pending' for r in report['results']))
                self.assertTrue(all(r.get('reason') for r in report['results'] if r['status'] != 'ready'))
                self.assertEqual(report['textures'], 3)
                skin_rows = next(r['skins'] for r in report['results'] if r['status'] == 'ready')
                self.assertEqual(skin_rows[1]['source'], 'skins/body.bmp')
                self.assertIn('Invalid skins/body.wal', skin_rows[1]['reason'])
                self.assertEqual(len(client.get('/list_textures?game=daikatana').json), 3)
                rows = client.get('/list_models?game=daikatana').json
                ready = [r for r in rows if r.get('status') == 'ready']
                self.assertEqual(len(ready), 2)
                model = ready[0]['model_name']
                self.assertEqual(client.get(f'/model_json/{model}?game=daikatana').status_code, 200)
                data = load_model_data(output / 'model_json' / (model + '.json'))
                self.assertEqual(len(data['material_textures']), 2)
                with client.get('/texture/' + data['material_textures'][0] + '?game=daikatana') as response:
                    self.assertEqual(response.status_code, 200)
                response = client.get(f'/export_glb/{model}?game=daikatana')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['X-Skinner-Animation-Status'], 'static')
                self.assertEqual(response.headers['X-Skinner-Animation-Clips'], '0')
                length = struct.unpack_from('<I', response.data, 12)[0]
                gltf = json.loads(response.data[20:20 + length])
                self.assertNotIn('animations', gltf)
                self.assertEqual(len(gltf['meshes'][0]['primitives']), 2)
                self.assertEqual(client.get(f'/export_obj/{model}?game=daikatana').status_code, 200)
                for body in ({}, {'path': ' '}, {'path': []}):
                    self.assertEqual(client.post('/import_daikatana', json=body).status_code, 400)
                self.assertEqual(client.post('/import_daikatana', json={'path': str(root)}, headers={'Origin': 'https://elsewhere.invalid'}).status_code, 403)
            png = output / 'textures' / data['material_textures'][0]
            Image.new('RGB', (2, 2), '#123456').save(png)
            before = png.read_bytes()
            import_catalog(root, output)
            self.assertEqual(png.read_bytes(), before)
            self.assertEqual(category('models/e2/w_test.dkm', {}), 'Episode 2/Weapons')
            self.assertEqual(category('models/e1/m_test.dkm', {'models/e1/m_test.dkm': {}}), 'Episode 1/Decorations')


@unittest.skipUnless((INSTALL / 'pak3.pak').is_file(), 'needs retail Daikatana data')
class DaikatanaInstallTest(unittest.TestCase):
    def test_full_wal_inventory(self):
        files, _, _ = read_install(INSTALL)
        versions = Counter()
        for name, entry in files.items():
            if name.endswith('.wal'):
                raw = read_asset(entry)
                version = raw[0] if raw[0] in (2, 3) else 0
                versions[version] += 1
                with self.subTest(texture=name):
                    # Native first-mip bounds and decoding, independent of palette selection.
                    read_wal(raw, PALETTE)
        self.assertEqual(versions, {3: 6283, 0: 139, 2: 1})

    def test_full_inventory_and_import(self):
        # Independent directory counts, including the two misnamed polygon files.
        expected, counts, versions = [], {}, Counter()
        for path in sorted(INSTALL.glob('pak[0-9].pak')):
            with path.open('rb') as stream:
                rows = pak_directory(stream)
                counts[path.name] = len(rows)
                for row in rows:
                    name = row['name']
                    if name.startswith('models/') or name.endswith(('.dkm', '.dkm2', '.dkn', '.sp2')):
                        expected.append((path.name, name))
                    if name.endswith('.dkm'):
                        stream.seek(row['offset'])
                        self.assertFalse(row['compressed'])
                        magic, version = struct.unpack('<4si', stream.read(8))
                        self.assertEqual(magic, b'DKMD')
                        versions[version] += 1
        self.assertEqual(counts, {'pak1.pak': 11455, 'pak2.pak': 83, 'pak3.pak': 782})
        self.assertEqual(versions, {1: 392, 2: 390})
        expected.extend(('loose', p.relative_to(INSTALL).as_posix().lower()) for p in (INSTALL / 'models').rglob('*') if p.is_file())
        with tempfile.TemporaryDirectory() as tmp:
            report = import_catalog(INSTALL, tmp)
            self.assertCountEqual([(r['pak'], r['source']) for r in report['results']], expected)
            self.assertEqual(report['dkm_entries'], 782)
            self.assertEqual(report['model_entries'], 784)
            self.assertEqual(report['dkm_skipped'], 0)
            self.assertEqual(report['overridden'], 0)
            self.assertEqual(report['ready'] + report['duplicates'], 784)
            self.assertEqual(report['ready'], 762)
            self.assertEqual(report['duplicates'], 22)  # Adds c_super_e3m4 == c_super_e3m5.
            self.assertEqual(report['textures'], 573)
            self.assertEqual(sum(s['texture'] == 'missing_skin.png' for r in report['results'] for s in r.get('skins', [])), 28)
            self.assertTrue(all(r['status'] in ('ready', 'duplicate') for r in report['results'] if r['source'].endswith(('.dkm', '.dkm2', '.dkn'))))
            sprites = [r for r in report['results'] if r['source'].endswith('.sp2')]
            self.assertEqual(len(sprites), 127)
            self.assertTrue(all(r['status'] == 'skipped' and r['reason'] for r in sprites))
            self.assertTrue(all(r['status'] != 'pending' for r in report['results']))
            models = list((Path(tmp) / 'model_json').glob('*.json'))
            self.assertEqual(len(models), report['ready'])
            for path in models:
                model = load_model_data(path)
                self.assertTrue(model['indices'])
                for png in model['material_textures']:
                    with Image.open(Path(tmp) / 'textures' / png) as image:
                        image.verify()
            for row in report['texture_results']:
                self.assertEqual(row['status'], 'ready', row)
                self.assertTrue(row['palette'])
            hiro = next(r for r in report['results'] if r['source'] == 'models/characters/hiro.dkm')
            data = load_model_data(Path(tmp) / 'model_json' / (hiro['model'] + '.json'))
            self.assertEqual(len(data['groups']), 4)
            self.assertEqual(len(set(data['material_textures'])), 2)
            self.assertTrue(all(s['source'] and s['reason'] for s in hiro['skins']))
            kage = next(r for r in report['results'] if r['source'] == 'models/cinematic/c_kage_e4m6c.dkm')
            self.assertEqual(kage['triangles'], 788)  # 110 invalid table faces aren't in the native draw stream.
            for source, undefined in (('models/e1/d1_swp12.dkm', 46), ('models/e3/m_lybits10.dkm', 98)):
                row = next(r for r in report['results'] if r['source'] == source)
                data = load_model_data(Path(tmp) / 'model_json' / (row['model'] + '.json'))
                self.assertEqual(data['metadata']['undefined_uvs'], undefined)
                self.assertTrue(row['warnings'])
                self.assertEqual(set(data['material_textures']), {'missing_skin.png'})


if __name__ == '__main__':
    unittest.main()

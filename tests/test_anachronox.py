"""Synthetic fixtures; explicitly select AnachronoxInstallTest for retail checks."""
from collections import Counter
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

from app import app
from tools.import_anachronox import (adat_directory, build_model, category, choose_mda,
                                    import_catalog, md2_layout, read_asset, read_entry,
                                    read_install, read_md2, read_mda, read_atd, read_texture, resolve_skins)
from tools.model_data import load_model_data

INSTALL = Path('C:/Program Files (x86)/Steam/steamapps/common/Anachronox/anoxdata')


def adat(entries):
    payload, directory = bytearray(), bytearray()
    for name, raw, compressed in entries:
        encoded = zlib.compress(raw) if compressed else raw
        directory.extend(struct.pack('<128s4i', name.encode(), 16 + len(payload), len(raw),
                                     len(encoded) if compressed else 0, 0))
        payload.extend(encoded)
    return struct.pack('<4s3i', b'ADAT', 16 + len(payload), len(directory), 9) + payload + directory


def md2(version=15, stride=5, x=2, short_end=False):
    skins = struct.pack('<64s64s', b'head.BMP', b'body.BMP')
    st = struct.pack('<8h', 0, 0, 1, 0, 0, 1, 1, 1)
    triangles = struct.pack('<18H', 0, 1, 2, 0, 1, 2, 2, 1, 3, 2, 1, 3, 0, 2, 1, 0, 2, 1)
    points = [(x, 1, 5), (x, 5, 1), (x, 1, 1), (x, 5, 5)]

    def frame(name, translation):
        packed = b''
        for p in points:
            packed += (bytes(p) + struct.pack('<H', 400) if stride == 5 else
                       struct.pack('<IH', p[0] | p[1] << 11 | p[2] << 21, 400) + bytes(stride - 6) if version & 0x10000 else
                       struct.pack('<4H', *p, 400))
        return struct.pack('<6f16s', 2, 3, 4, *translation, name) + packed

    first = frame(b'first', (10, -20, 30))
    frames = first + frame(b'second', (99, 98, 97))
    commands = struct.pack('<i', 4) + b''.join(struct.pack('<ffi', *c) for c in
                                             [(0, 0, 0), (1, 0, 1), (0, 1, 2), (1, 1, 3)])
    commands += struct.pack('<i', -3) + b''.join(struct.pack('<ffi', *c) for c in [(0, 0, 0), (0, 1, 2), (1, 0, 1)]) + bytes(4)
    surface = struct.pack('<2H', 1, 1)
    tags = b'' if short_end else struct.pack('<8sI', b'tag', 1)
    skin_at = 96
    st_at = skin_at + len(skins)
    tri_at = st_at + len(st)
    frame_at = tri_at + len(triangles)
    cmd_at = frame_at + len(frames)
    surface_at = cmd_at + len(commands)
    tag_at = surface_at + len(surface)
    end = tag_at + len(tags)
    header = struct.pack('<4s18i3f2i', b'IDP2', version, 8, 8, len(first), 2, 4, 4, 3, len(commands) // 4, 2,
                         skin_at, st_at, tri_at, frame_at, cmd_at, end - int(short_end), 2, surface_at,
                         1, 1, 1, int(bool(tags)), tag_at if tags else 0)
    return header + skins + st + triangles + frames + commands + surface + tags


def bitmap(fmt='TGA', color=(19, 47, 113, 128)):
    stream = io.BytesIO()
    image = Image.new('RGBA', (2, 2), color)
    image.putpixel((0, 0), (201, 4, 8, 0))
    image.save(stream, format=fmt, **({'compression': 'tga_rle'} if fmt == 'TGA' else {}))
    return stream.getvalue()


def mda(base='models/boots/hero.md2', skin='models/boots/chosen'):
    return (f'MDA1\nbasemodel={base}\n'
            'profile HURT { evaluate "hurt" skin { pass { map missing/hurt } } }\n'
            f'profile DFLT {{ skin {{ pass {{ map "{skin}" alphafunc ge128 cull disable }} '
            'pass { map graphics/shine uvgen sphere blendmode add } } skin { pass { map missing/body } } }\n'
            '# ABSOLUTELY do not modify the following!\n$TEST\n&\xff{profile').encode('latin1')


class AnachronoxTest(unittest.TestCase):
    def test_adat_stored_compressed_and_invalid_ranges(self):
        raw = adat([('BOOTS\\hero.md2', b'abc' * 100, True), ('x.tga', b'xyz', False)])
        stream = io.BytesIO(raw)
        entries = adat_directory(stream)
        self.assertEqual(entries[0]['name'], 'boots/hero.md2')
        self.assertEqual([read_entry(stream, e) for e in entries], [b'abc' * 100, b'xyz'])
        bad = [b'', raw[:-1], b'NOPE' + raw[4:], adat([('../escape', b'x', False)])]
        for offset, value in ((12, 8), (4, 2), (8, 143)):
            changed = bytearray(raw)
            struct.pack_into('<i', changed, offset, value)
            bad.append(changed)
        for changed in bad:
            with self.assertRaises(ValueError):
                adat_directory(io.BytesIO(changed))
        for payload, length in ((zlib.compress(b'abc')[:-1], 3), (zlib.compress(b'abc') + b'junk', 3),
                                (zlib.compress(b'abcd'), 3), (b'bad stream', 3)):
            with self.assertRaises(ValueError):
                read_entry(io.BytesIO(payload), dict(offset=0, stored=len(payload), length=length, compressed=True))

    def test_offsets_select_precision_first_frame_surfaces_and_uvs(self):
        for version, stride in ((15, 5), (14, 5), (0x1000e, 6), (0x1000f, 6), (0x2000f, 8), (0x1000c, 8)):
            with self.subTest(version=version):
                mesh = read_md2(md2(version, stride))
                self.assertEqual(mesh['vertex_stride'], stride)
                self.assertEqual(mesh['points'][0], (14, -17, 50))
                self.assertEqual(mesh['normal_indices'], [400] * 4)
                self.assertEqual(mesh['pose'], 'first')
                self.assertEqual([len(s['triangles']) for s in mesh['surfaces']], [2, 1])
                skins = [dict(texture=n, passes=[]) for n in ('a.png', 'b.png')]
                model = build_model(mesh, skins)
                self.assertEqual(model['vertices'][:9], [-17, 50, 14, -17, 34, 14, -5, 34, 14])
                self.assertEqual(model['uvs'][:6], [0, 0, 0, 1, 1, 0])
                self.assertEqual([g['count'] for g in model['groups']], [6, 3])
                self.assertEqual(model['material_textures'], ['a.png', 'b.png'])
        self.assertEqual(read_md2(md2(0x1000f, 6, x=1500))['points'][0][0], 3010)
        self.assertEqual(read_md2(md2(0x2000f, 8, x=60000))['points'][0][0], 120010)
        self.assertTrue(read_md2(md2(short_end=True))['warnings'])

    def test_bad_md2_boundaries_commands_and_values(self):
        bad = [b'', md2()[:-1], md2() + b'\0']
        for offset, value in ((4, 8), (16, 1), (24, 0), (44, 100), (60, 1), (64, 3), (68, 3), (88, -1)):
            changed = bytearray(md2())
            struct.pack_into('<i', changed, offset, value)
            bad.append(changed)
        for relative, value in ((0, 99999), (12, 999), (4, 0x7f800000)):
            changed = bytearray(md2())
            at = struct.unpack_from('<i', changed, 60)[0]
            struct.pack_into('<i', changed, at + relative, value)
            bad.append(changed)
        changed = bytearray(md2())
        at = struct.unpack_from('<i', changed, 72)[0]
        struct.pack_into('<H', changed, at, 0)
        bad.append(changed)
        for changed in bad:
            with self.assertRaises(ValueError):
                read_md2(changed)

    def test_mda_default_binary_boundary_resolution_and_categories(self):
        parsed = read_mda(mda())
        self.assertEqual(parsed['profile'], 'DFLT')
        self.assertEqual(len(parsed['skins']), 2)
        self.assertEqual(parsed['skins'][0][0]['map'], 'models/boots/chosen')
        self.assertTrue(parsed['warnings'])
        self.assertEqual(read_mda(b'MDA1\nbasemodel=models/hero.md2\n')['skins'], [])
        for raw in (b'MDA1', b'nope', b'MDA1 basemodel a.md2 profile {'):
            with self.assertRaises(ValueError):
                read_mda(raw)
        files = dict.fromkeys(('models/boots/chosen.png', 'models/boots/body.tga'))
        rows = resolve_skins('models/boots/hero.md2', ['head.BMP', 'body.BMP'], files, parsed)
        self.assertEqual([r['method'] for r in rows], ['mda', 'md2'])
        self.assertEqual(rows[1]['missing_references'], ['missing/body'])
        self.assertEqual(rows[1]['source'], 'models/boots/body.tga')
        self.assertEqual(resolve_skins('models/hero.md2', ['absent.bmp'], {})[0]['method'], 'missing')
        candidates = {'models/hero.md2': [dict(source='models/z.mda'), dict(source='models/hero.mda')]}
        self.assertEqual(choose_mda('models/hero.md2', candidates)['source'], 'models/hero.mda')
        for folder, expected in (('boots', 'Party'), ('newface/pal', 'Party'), ('newface/abbot', 'NPCs'),
                                 ('npcs', 'NPCs'), ('monsters', 'Monsters'), ('mystech', 'MysTech'),
                                 ('objects', 'Objects'), ('cine', 'Cinematic'), ('interface', 'Interface')):
            self.assertEqual(category(f'models/{folder}/x.md2'), expected)

    def test_atd_initial_animation_and_explicit_unsupported_procedural(self):
        raw = (b'ATD1\n# !frame fake comment\ntype=animation\nwidth=4\nheight=4\n'
               b'!bitmap\nfile=models/base.png\n!bitmap\nfile=models/eyes\n'
               b'!frame\nbitmap=0\nnext=1\nwait=-1\n'
               b'!frame\nbitmap=1\nx=2\ny=1\nwait=4\n')
        self.assertEqual(len(read_atd(raw)['draws']), 2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in [('skin.atd', raw), ('base.png', bitmap('PNG')), ('eyes.tga', bitmap())]:
                (root / name).write_bytes(content)
            files = {'models/' + p.name: dict(path=p) for p in root.iterdir()}
            image = read_texture('models/skin.atd', files)
            self.assertEqual(image.size, (4, 4))
            self.assertEqual(image.getpixel((0, 0)), (201, 4, 8, 0))
            self.assertEqual(image.getpixel((3, 2)), (19, 47, 113, 128))
        for bad in (b'ATD1\ntype=procedural', raw.replace(b'bitmap=0', b'bitmap=99'),
                    raw.replace(b'wait=4', b'wait=-1\nnext=0'), raw.replace(b'width=4', b'width=0')):
            with self.assertRaises(ValueError):
                read_atd(bad)

    def test_layering_import_texture_library_routes_exports_and_preserved_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / 'anoxdata', Path(tmp) / 'out'
            root.mkdir()
            (root / 'MODELS.dat').write_bytes(adat([
                ('boots/hero.md2', md2(x=3), True), ('boots/hero.mda', mda(skin='old'), True),
                ('boots/body.tga', bitmap(), False), ('boots/alt.png', bitmap('PNG'), True),
                ('objects/bad.md2', b'IDP2', False), ('objects/missing.md2', md2(x=9), True)]))
            for number, skin in ((1, 'patch1'), (2, 'patch2')):
                with zipfile.ZipFile(root / f'anox{number}.zip', 'w') as z:
                    z.writestr('models/boots/hero.mda', mda(skin=skin))
            patched = read_install(root)[0]
            self.assertEqual(read_asset(patched['models/boots/hero.mda']), mda(skin='patch2'))
            (root / 'models/boots').mkdir(parents=True)
            (root / 'models/boots/hero.mda').write_bytes(mda())
            (root / 'models/boots/hero.md2').write_bytes(md2())
            (root / 'models/boots/zz_duplicate.md2').write_bytes(md2())
            (root / 'models/boots/chosen.png').write_bytes(bitmap('PNG'))
            files, records, counts = read_install(root.parent)
            self.assertEqual(read_asset(files['models/boots/hero.md2']), md2())
            self.assertEqual(read_asset(files['models/boots/hero.mda']), mda())
            self.assertEqual(list(counts), ['models.dat', 'anox1.zip', 'anox2.zip', 'loose'])
            for name in ('models/boots/body.tga', 'models/boots/chosen.png'):
                image = read_texture(name, files)
                self.assertEqual(image.getpixel((0, 0)), (201, 4, 8, 0))
                self.assertEqual(image.getpixel((1, 1)), (19, 47, 113, 128))
            with patch.dict('app.pack_dirs', anachronox=out):
                client = app.test_client()
                page = client.get('/').get_data(as_text=True)
                self.assertIn('id="anachronoxImport"', page)
                self.assertIn('value="anachronox">Anachronox textures', page)
                response = client.post('/import_anachronox', json={'path': str(root)})
                self.assertEqual(response.status_code, 200, response.json)
                report = response.json
                self.assertEqual((report['ready'], report['duplicates'], report['overridden'], report['md2_skipped']), (2, 1, 1, 1))
                self.assertTrue(all(r['status'] != 'pending' for r in report['results']))
                rows = client.get('/list_models?game=anachronox').json
                hero = next(r for r in rows if r['display_name'] == 'hero')
                name = hero['model_name']
                data = load_model_data(out / 'model_json' / (name + '.json'))
                self.assertEqual(data['game'], 'anachronox')
                self.assertEqual([s['method'] for s in data['metadata']['skins']], ['mda', 'md2'])
                self.assertEqual(data['material_settings'][0], dict(alphaFunc='GE128', cull='none'))
                self.assertEqual(client.get(f'/model_json/{name}?game=anachronox').status_code, 200)
                self.assertGreaterEqual(len(client.get('/list_textures?game=anachronox').json), 3)
                with client.get('/texture/' + data['material_textures'][0] + '?game=anachronox') as response:
                    self.assertEqual(response.status_code, 200)
                response = client.get(f'/export_glb/{name}?game=anachronox')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['X-Skinner-Animation-Status'], 'static')
                length = struct.unpack_from('<I', response.data, 12)[0]
                gltf = json.loads(response.data[20:20 + length])
                self.assertEqual(len(gltf['meshes'][0]['primitives']), 2)
                self.assertNotIn('animations', gltf)
                self.assertEqual(client.get(f'/export_obj/{name}?game=anachronox').status_code, 200)
                for payload in ({}, {'path': []}, {'path': ' '}):
                    self.assertEqual(client.post('/import_anachronox', json=payload).status_code, 400)
                self.assertEqual(client.post('/import_anachronox', json={'path': str(root)}, headers={'Origin': 'https://elsewhere.invalid'}).status_code, 403)
            target = out / 'textures' / data['material_textures'][0]
            Image.new('RGBA', (2, 2), '#123456').save(target)
            before = target.read_bytes()
            import_catalog(root, out)
            self.assertEqual(target.read_bytes(), before)


@unittest.skipUnless((INSTALL / 'MODELS.dat').is_file(), 'needs retail Anachronox data')
class AnachronoxInstallTest(unittest.TestCase):
    def test_archive_inventory_and_all_model_layouts(self):
        with (INSTALL / 'MODELS.dat').open('rb') as stream:
            entries = adat_directory(stream)
            self.assertEqual(len(entries), 4658)
            self.assertEqual(Counter(Path(e['name']).suffix for e in entries)['.md2'], 1953)
            layouts = Counter()
            for entry in entries:
                if not entry['name'].endswith('.md2'):
                    continue
                with self.subTest(model=entry['name']):
                    mesh = read_md2(read_entry(stream, entry))
                    layouts[(mesh['version'], mesh['vertex_stride'])] += 1
            self.assertEqual(layouts, {(15, 5): 1332, (0x1000f, 6): 539, (0x2000f, 8): 72, (14, 5): 9, (0x1000c, 8): 1})

    def test_full_import_accounting_and_loadable_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = import_catalog(INSTALL, tmp)
            self.assertEqual(report['md2_skipped'], 0)
            self.assertEqual(report['ready'] + report['duplicates'] + report['overridden'], report['model_entries'])
            self.assertTrue(all(r['status'] != 'pending' for r in report['results']))
            for path in (Path(tmp) / 'model_json').glob('*.json'):
                with self.subTest(model=path.name):
                    load_model_data(path)


if __name__ == '__main__':
    unittest.main()

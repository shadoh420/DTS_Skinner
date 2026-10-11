"""Synthetic fixtures only by default; retail smoke checks require SIN_INSTALL_TESTS=1."""
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from tools.import_sin import (available_games, build_model, category, choose_animation,
                              import_catalog, read_asset, read_def, read_entry, read_install,
                              read_sam, read_sbm, read_swl, resolve_skins, spak_directory)
from tools.model_data import load_model_data

INSTALL = Path('C:/Program Files (x86)/Steam/steamapps/common/SiN 1')


def spak(entries):
    payload, directory = bytearray(), bytearray()
    for name, raw in entries:
        directory.extend(struct.pack('<120sii', name.encode(), 12 + len(payload), len(raw)))
        payload.extend(raw)
    return struct.pack('<4sii', b'SPAK', 12 + len(payload), len(directory)) + payload + directory


def swl():
    header = bytearray(1236)
    header[:4] = b'test'
    struct.pack_into('<2i', header, 64, 8, 8)
    for i in range(256):
        header[72 + i * 4:76 + i * 4] = bytes((i, 20, 30, 0))
    header[76:80] = bytes((255, 0, 255, 123))
    sizes = [64, 16, 4, 1]
    offsets, at = [], len(header)
    for size in sizes:
        offsets.append(at)
        at += size
    struct.pack_into('<4i', header, 1100, *offsets)
    return bytes(header) + bytes([0, 1, 2, 3] * 16) + bytes(sum(sizes[1:]))


def tga(color=(10, 40, 90, 255)):
    stream = io.BytesIO()
    Image.new('RGBA', (2, 2), color).save(stream, 'TGA')
    return stream.getvalue()


def sbm():
    st_at = 28 + 2 * 24
    payload = bytearray(struct.pack('<8f', 0, 0, 1, 0, 0, 1, 1, 1))
    surfaces = bytearray()
    for index, (ident, count, corners) in enumerate(((0, -3, [(0, 0, 0), (1, 0, 1), (0, 1, 2)]),
                                                   (3, 3, [(1, 0, 1), (1, 1, 3), (0, 1, 2)]))):
        at = 28 + 24 * index
        cmd_at = st_at + len(payload)
        commands = struct.pack('<i', count) + b''.join(struct.pack('<ffi', *c) for c in corners) + bytes(4)
        payload.extend(commands)
        tri_at = st_at + len(payload)
        ids = [c[2] for c in corners]
        payload.extend(struct.pack('<6HI', *ids, *ids, 0))
        surfaces.extend(struct.pack('<6i', ident, 1, len(commands) // 4, cmd_at - at, tri_at - at, st_at + len(payload) - at))
    return struct.pack('<4s6i', b'SBM ', 1, 4, 4, 2, st_at, st_at + len(payload)) + surfaces + payload


def sam(verts=4):
    header = struct.pack('<4si64s10f4i', b'SAM ', 1, b'idle', *([0.0] * 9), .2, verts, 2, 128, 128 + 88 + verts * 8)
    descriptors, payload = bytearray(), bytearray()
    for frame in range(2):
        at = 128 + 44 * frame
        descriptors.extend(struct.pack('<10fi', 0, 0, 0, frame * .1, -2, 3, 4, 10 + frame, -20, 30,
                                       128 + 88 + len(payload) - at))
        payload.extend(b''.join(struct.pack('<4B', i, i % 2, i // 2, 0) for i in range(verts)))
    return header + descriptors + payload


def definition(base='body.sbm'):
    return (f'SDEF\n/* comment */\npath "models/hero"\nscale 2\norigin 1 2 3\n{base}\n'
            'normal "a.tga"\na.tga\nb.tga\nready ready.sam\nidle1 idle.sam\n'
            'id 1 group body\nid 4 group head skin2 twosided masked\n'
            '!init:\nserver classname Citizen\n!main:\nserver idle1 first skin damage\n').encode()


def write_install(root):
    (root / 'base').mkdir()
    entries = [('models/hero.def', definition()), ('models/alias.def', definition()),
               ('models/hero/body.sbm', sbm()), ('models/hero/idle.sam', sam()),
               ('models/hero/a.tga', tga()), ('models/hero/b.tga', tga((90, 40, 10, 128))),
               ('models/unused.swl', swl()), ('models/missing.def', definition('absent.sbm')),
               ('models/broken.def', b'garbage'), ('sprites/test.spr', b'sprite')]
    (root / 'base' / 'pak0.sin').write_bytes(spak(entries))
    return root


class SinTest(unittest.TestCase):
    def test_lazy_archive_and_rejected_ranges(self):
        raw = spak([('MODELS/a.def', b'abc'), ('models/b.def', b'defg')])
        stream = io.BytesIO(raw)
        entries = spak_directory(stream)
        self.assertEqual(entries[0]['name'], 'models/a.def')
        self.assertEqual(read_entry(stream, entries[1]), b'defg')
        with self.assertRaisesRegex(ValueError, 'bounded'):
            read_entry(stream, entries[1], max_bytes=3)
        for bad in (b'', b'PACK' + raw[4:], raw[:-1], spak([('../x', b'')]), spak([('a/../x', b'')]), spak([('C:/x', b'')])):
            with self.subTest(bad=bad[:12]), self.assertRaises(ValueError):
                spak_directory(io.BytesIO(bad))
        for offset, value in ((4, 1), (8, 129), (12 + 7 + 120, 2), (12 + 7 + 124, -1)):
            bad = bytearray(raw)
            struct.pack_into('<i', bad, offset, value)
            with self.assertRaises(ValueError):
                spak_directory(io.BytesIO(bad))
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            read_entry(io.BytesIO(b'12'), dict(offset=1, length=2))

    def test_priority_campaign_isolation_and_first_directory_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for game in ('base', '2015', 'ctf'):
                (root / game).mkdir()
                (root / game / 'pak0.sin').write_bytes(spak([('models/a.tga', game.encode()), ('models/shared.tga', b'base')]))
            (root / 'base' / 'pak1.sin').write_bytes(spak([('models/a.tga', b'one'), ('models/a.tga', b'duplicate')]))
            (root / 'base' / 'pak10.sin').write_bytes(spak([('models/a.tga', b'ten')]))
            (root / 'base' / 'pak32.sin').write_bytes(spak([('models/a.tga', b'ignored')]))
            self.assertEqual(available_games(root), ['sin', '2015', 'ctf'])
            files, _, counts = read_install(root)
            self.assertEqual(read_asset(files['models/a.tga']), b'ten')
            self.assertEqual(list(counts), ['pak0.sin', 'pak1.sin', 'pak10.sin', 'loose'])
            (root / 'base' / 'models').mkdir()
            (root / 'base' / 'models' / 'a.tga').write_bytes(b'loose')
            files, _, _ = read_install(root / 'base')
            self.assertEqual(read_asset(files['models/a.tga']), b'loose')
            for game in ('2015', 'ctf'):
                expanded, _, _ = read_install(root, game=game, base=files)
                self.assertEqual(read_asset(expanded['models/a.tga']), game.encode())
                self.assertEqual(read_asset(files['models/a.tga']), b'loose')
            (root / 'base' / 'pak10.sin').unlink()
            (root / 'base' / 'models' / 'a.tga').unlink()
            self.assertEqual(read_asset(read_install(root)[0]['models/a.tga']), b'one')

    def test_swl_palette_alpha_mips_and_bounds(self):
        image = read_swl(swl())
        self.assertEqual(image.size, (8, 8))
        self.assertEqual(image.getpixel((0, 0)), (0, 20, 30, 255))
        self.assertEqual(image.getpixel((1, 0)), (255, 0, 255, 0))
        for offset, value in ((64, -1), (68, 100000), (1100, 12), (1104, 1237)):
            bad = bytearray(swl())
            struct.pack_into('<i', bad, offset, value)
            with self.assertRaises(ValueError):
                read_swl(bad)
        with self.assertRaises(ValueError):
            read_swl(swl()[:-1])

    def test_sbm_sam_relative_offsets_surface_ids_and_geometry(self):
        mesh = read_sbm(sbm())
        pose = read_sam(sam(), mesh['vertex_count'])
        self.assertEqual(pose['points'][0], (10, -20, 30))
        self.assertEqual(pose['points'][1], (8, -17, 30))
        self.assertEqual(pose['frames'], 2)
        info = read_def(definition())
        self.assertEqual(info['surfaces'][3]['skin'], 2)
        self.assertEqual(info['classname'], 'Citizen')
        self.assertEqual(info['init_commands'], [])
        files = {'models/hero/a.tga': {}, 'models/hero/b.tga': {}}
        skins = resolve_skins(info, mesh, files)
        self.assertEqual([s['source'] for s in skins], list(files))
        for skin in skins:
            skin['texture'] = Path(skin['source']).with_suffix('.png').name
        data = build_model(dict(mesh, **pose), info, skins)
        self.assertEqual(data['vertices'][:3], [-36, 66, 22])
        self.assertEqual(data['uvs'][:2], [0, 0])
        self.assertEqual(data['indices'][:3], [0, 1, 2])
        self.assertEqual(data['vertices'][3:6], [-36, 74, 14])  # Reversed clockwise corner order.
        self.assertEqual(len(data['groups']), 2)
        self.assertEqual(data['material_settings'][1], dict(cull='none', alphaFunc='GE128'))
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            read_sam(sam(), 5)
        for offset, value in ((20, 0), (24, 29), (28 + 12, -1), (28 + 16, 100000)):
            bad = bytearray(sbm())
            struct.pack_into('<i', bad, offset, value)
            with self.assertRaises(ValueError):
                read_sbm(bad)
        for offset, value in ((112, 0), (116, 100000), (120, 1), (168, -128)):
            bad = bytearray(sam())
            struct.pack_into('<i', bad, offset, value)
            with self.assertRaises(ValueError):
                read_sam(bad)
        bad = bytearray(sbm())
        struct.pack_into('<f', bad, 112, float('nan'))  # First command S.
        with self.assertRaisesRegex(ValueError, 'command'):
            read_sbm(bad)
        bad = bytearray(sam())
        struct.pack_into('<f', bad, 144, float('inf'))
        with self.assertRaisesRegex(ValueError, 'Non-finite'):
            read_sam(bad)

    def test_def_comments_invalid_data_and_skin_fallbacks(self):
        info = read_def(definition().replace(b'normal "a.tga"', b'normal "a.tga" // ignored\n/* .sbm fake */'))
        mesh = read_sbm(sbm())
        skins = resolve_skins(info, mesh, {'models/hero/a.tga': {}})
        self.assertIn('fallback', skins[1]['reason'])
        self.assertEqual(skins[1]['source'], 'models/hero/a.tga')
        self.assertTrue(all(not s['source'] for s in resolve_skins(info, mesh, {})))
        for data in (b'NOPE', b'SDEF\npath ../escape\na.sbm', b'SDEF\nscale nan\na.sbm', b'SDEF\norigin 1\na.sbm', b'SDEF\nid 0 group bad\na.sbm'):
            with self.assertRaises(ValueError):
                read_def(data)
        self.assertEqual(category('models/view_test.def', info), 'Weapons')
        self.assertEqual(category('models/test.def', dict(basemodel='models/items/chair.sbm')), 'Objects')
        relative = read_def(b'SDEF\norigin 0,0,0\na.sbm\na.tga\na.sam', 'models/nested/example.def')
        self.assertEqual(relative['basemodel'], 'models/nested/a.sbm')
        self.assertIn('Comma-separated', relative['warnings'][0])

    def test_synthetic_import_variants_accounting_api_exports_and_edits(self):
        from app import app
        with tempfile.TemporaryDirectory() as tmp:
            root = write_install(Path(tmp))
            out = root / 'out'
            (root / 'ctf').mkdir()
            (root / 'ctf' / 'pak0.sin').write_bytes(spak([('models/hero.def', definition()), ('models/hero/a.tga', tga((1, 2, 3, 255)))]))
            with patch('app.pack_dirs', {'sin': out}):
                client = app.test_client()
                response = client.post('/import_sin', json={'path': str(root)})
                self.assertEqual(response.status_code, 200, response.json)
                report = response.json
                self.assertEqual(report['ready'], 2)  # CTF texture override must retain its variant.
                self.assertEqual(report['duplicates'], 1)
                self.assertEqual(report['skipped'], 3)
                self.assertTrue(all(r['status'] != 'pending' for r in report['results']))
                catalog = client.get('/list_models?game=sin').json
                ready = [r for r in catalog if r['status'] == 'ready']
                self.assertEqual(len(ready), 2)
                name = ready[0]['model_name']
                data = load_model_data(out / 'model_json' / (name + '.json'))
                self.assertEqual(client.get('/model_json/' + name + '?game=sin').status_code, 200)
                self.assertGreaterEqual(len(client.get('/list_textures?game=sin').json), 4)
                self.assertEqual(client.get('/export_obj/' + name + '?game=sin').status_code, 200)
                glb = client.get('/export_glb/' + name + '?game=sin')
                self.assertEqual(glb.status_code, 200)
                self.assertEqual(glb.headers['X-Skinner-Animation-Status'], 'static')
                length = struct.unpack_from('<I', glb.data, 12)[0]
                gltf = json.loads(glb.data[20:20 + length])
                self.assertNotIn('animations', gltf)
                self.assertEqual(gltf['materials'][1]['alphaMode'], 'MASK')
                self.assertTrue(gltf['materials'][1]['doubleSided'])
                for payload in ({}, {'path': []}, {'path': ' '}):
                    self.assertEqual(client.post('/import_sin', json=payload).status_code, 400)
                self.assertEqual(client.post('/import_sin', json={'path': str(root)}, headers={'Origin': 'https://elsewhere.invalid'}).status_code, 403)
            target = out / 'textures' / data['material_textures'][0]
            Image.new('RGB', (2, 2), '#abcdef').save(target)
            before = target.read_bytes()
            import_catalog(root, out)
            self.assertEqual(target.read_bytes(), before)
            files, _, _ = read_install(root)
            pose, chosen, _ = choose_animation(read_def(definition()), read_sbm(sbm()), files)
            self.assertEqual(chosen['source'], 'models/hero/idle.sam')
            self.assertEqual(pose['pose'], 'idle:0')

    def test_unreadable_skin_animation_fallback_and_translucent_exports(self):
        from app import app
        from tools.obj_exporter import q3_material_settings
        with tempfile.TemporaryDirectory() as tmp:
            root = write_install(Path(tmp))
            loose = root / 'base/models/hero'
            loose.mkdir(parents=True)
            (loose / 'b.tga').write_bytes(b'invalid image')
            (loose / 'idle.sam').write_bytes(sam(5))
            (loose / 'ready.sam').write_bytes(sam())
            # A corrupt named skin can use default skin zero; retain its decode failure.
            altered = definition().replace(b'skin2 twosided masked', b'skin2 twosided translucent33')
            (root / 'base/models/hero.def').write_bytes(altered)
            out = root / 'out'
            with patch.dict('app.pack_dirs', sin=out):
                client = app.test_client()
                report = client.post('/import_sin', json={'path': str(root)}).json
                hero = next(r for r in report['results'] if r['source'] == 'models/hero.def' and r['status'] == 'ready')
                self.assertEqual(hero['animation']['source'], 'models/hero/ready.sam')
                self.assertIn('mismatch', hero['animation']['failures'][0]['reason'])
                self.assertEqual(hero['skins'][1]['source'], 'models/hero/a.tga')
                self.assertTrue(hero['skins'][1]['decode_failures'])
                self.assertEqual(hero['skin_declarations'][2]['status'], 'skipped')
                name = hero['model']
                glb = client.get('/export_glb/' + name + '?game=sin').data
                length = struct.unpack_from('<I', glb, 12)[0]
                gltf = json.loads(glb[20:20 + length])
                self.assertEqual(gltf['materials'][1]['alphaMode'], 'BLEND')
                self.assertEqual(gltf['materials'][1]['pbrMetallicRoughness']['baseColorFactor'][3], .33)
                obj = client.get('/export_obj/' + name + '?game=sin')
                self.assertEqual(obj.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(obj.data)) as archive:
                    opacity = next(n for n in archive.namelist() if '_skinner_opacity_1' in n)
                    with Image.open(io.BytesIO(archive.read(opacity))) as image:
                        self.assertEqual(image.getpixel((0, 0)), 84)
            # Existing unsupported-game behavior and existing Q3 defaults stay intact.
            self.assertEqual(q3_material_settings(dict(game='quake2', material_textures=['a'], material_settings=[{'opacity': .2}])), [{}])
            self.assertEqual(q3_material_settings(dict(game='q3', material_textures=['a'], material_settings=[{}])), [{}])


@unittest.skipUnless(os.environ.get('SIN_INSTALL_TESTS') == '1' and (INSTALL / 'base/pak0.sin').is_file(),
                     'optional retail smoke checks; needs install and SIN_INSTALL_TESTS=1')
class SinInstallTest(unittest.TestCase):
    def test_single_base_archive_member_smoke(self):
        # A handful of members only; no full import or install-wide validation.
        with (INSTALL / 'base/pak0.sin').open('rb') as stream:
            entries = {e['name']: e for e in spak_directory(stream)}
            info = read_def(read_entry(stream, entries['models/view_asrifle.def']))
            mesh = read_sbm(read_entry(stream, entries[info['basemodel']]))
            pose = read_sam(read_entry(stream, entries['models/asrifle/asrifle_idle1.sam']), mesh['vertex_count'])
            self.assertEqual(mesh['vertex_count'], 342)
            self.assertEqual(pose['frames'], 31)
            self.assertEqual(read_swl(read_entry(stream, entries['textures/aqueduct/cp_controls.swl'])).size, (192, 96))


if __name__ == '__main__':
    unittest.main()

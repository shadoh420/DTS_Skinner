"""Synthetic MD2/PCX/PACK tests and optional, fully counted baseq2 install coverage."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app import app
from tests.test_quake import pak
from tools.import_quake2 import build_model, import_catalog, read_install, read_md2, read_pcx
from tools.model_data import load_model_data

INSTALL = Path('C:/Program Files (x86)/Steam/steamapps/common/Quake 2/baseq2')


def pcx(color=7):
    image = Image.frombytes('P', (4, 2), bytes([0, 1, 192, 255, 2, 3, 4, 5]))
    image.putpalette(bytes(c for i in range(256) for c in (i, 255 - i, color)))
    output = io.BytesIO()
    image.save(output, format='PCX')
    return output.getvalue()


def md2(skins=('models/test/skin.pcx',), x=2):
    # Independent ST indices split point zero on the second face, with no onseam.
    skin_data = b''.join(struct.pack('<64s', s.encode()) for s in skins)
    st = struct.pack('<8h', 0, 0, 1, 0, 0, 1, 3, 1)
    triangles = struct.pack('<12H', 0, 1, 2, 0, 1, 2, 0, 2, 1, 3, 2, 1)

    def frame(name, scale, origin):
        return struct.pack('<6f16s12B', *scale, *origin, name.encode(),
                           x, 1, 5, 0, x, 5, 1, 0, x, 1, 1, 0)

    frames = frame('first', (2, 3, 4), (10, -20, 30)) + frame('later', (7, 8, 9), (99, 98, 97))
    skin_at = 68
    st_at = skin_at + len(skin_data)
    tri_at = st_at + len(st)
    frame_at = tri_at + len(triangles)
    cmd_at = frame_at + len(frames)
    return struct.pack('<4s16i', b'IDP2', 8, 4, 2, 52, len(skins), 3, 4, 2, 0, 2,
                       skin_at, st_at, tri_at, frame_at, cmd_at, cmd_at) + skin_data + st + triangles + frames


class Quake2Test(unittest.TestCase):
    def test_campaign_layering_skin_supply_and_version_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp), Path(tmp) / 'out'
            for game in ('baseq2', 'xatrix', 'rogue'):
                (root / game).mkdir()
            (root / 'baseq2/pak0.pak').write_bytes(pak([
                ('models/test/tris.md2', md2()), ('models/test/skin.pcx', pcx(7)),
                ('models/same/tris.md2', md2(x=9)),
                ('models/alias/tris.md2', md2(x=9)),
                ('players/male/w_extra.md2', md2(('models/extra/skin.pcx',)))]))
            (root / 'xatrix/pak0.pak').write_bytes(pak([
                ('models/test/tris.md2', md2(x=4)), ('models/test/skin.pcx', pcx(21)),
                ('models/same/tris.md2', md2(x=9)), ('models/extra/skin.pcx', pcx(31))]))
            (root / 'rogue/pak0.pak').write_bytes(pak([('models/rogue/tris.md2', md2(x=5)),
                                                     ('models/alias/tris.md2', md2(x=9))]))
            base, rows, _ = read_install(root)
            overlay, own, _ = read_install(root, game='xatrix', base=base)
            sibling, _, _ = read_install(root, game='rogue', base=base)
            self.assertEqual(overlay['models/test/skin.pcx'][0], pcx(21))
            self.assertEqual(sibling['models/test/skin.pcx'][0], pcx(7))
            self.assertNotIn('models/extra/skin.pcx', sibling)
            self.assertTrue(all(r['status'] == 'pending' for r in rows))  # Overlays cannot mutate base inventory.
            self.assertEqual(len(own), 2)
            report = import_catalog(root, output)
            rows = {(r['game'], r['source']): r for r in report['results']}
            self.assertEqual(rows['xatrix', 'models/same/tris.md2']['status'], 'duplicate')
            self.assertEqual(rows['rogue', 'models/alias/tris.md2']['status'], 'duplicate')
            extra = rows['quake2', 'players/male/w_extra.md2']
            self.assertEqual(extra['skin_game'], 'xatrix')
            self.assertIn('supplied', extra['skin_reason'])
            for game, color in (('quake2', 7), ('xatrix', 21), ('rogue', 7)):
                name = 'models/rogue/tris.md2' if game == 'rogue' else 'models/test/tris.md2'
                model = rows[game, name]['model']
                self.assertEqual(model.startswith(game + '_'), game != 'quake2')
                data = json.loads((output / 'model_json' / (model + '.json')).read_text())
                with Image.open(output / 'textures' / data['material_textures'][0]) as image:
                    self.assertEqual(image.getpixel((0, 0))[2], color)

    def test_first_frame_st_winding_and_palette(self):
        mesh = read_md2(md2())
        self.assertEqual(mesh['points'], [(14, -17, 50), (14, -5, 34), (14, -17, 34)])
        self.assertEqual((mesh['pose'], mesh['frames']), ('first', 2))
        model = build_model(mesh, 'test.png')
        self.assertEqual(model['vertices'], [-17, 50, 14, -17, 34, 14, -5, 34, 14, -17, 50, 14])
        self.assertEqual(model['indices'], [0, 1, 2, 3, 2, 1])
        self.assertEqual(model['uvs'], [.125, .25, .125, .75, .375, .25, .875, .75])
        a, b, c = [model['vertices'][i * 3:i * 3 + 3] for i in model['indices'][:3]]
        self.assertGreater((b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]), 0)
        image = read_pcx(pcx(19))
        self.assertEqual(image.size, (4, 2))
        self.assertEqual(list(image.getdata())[:4], [(0, 255, 19), (1, 254, 19), (192, 63, 19), (255, 0, 19)])

    def test_invalid_md2_pcx_and_pak(self):
        from tools.import_quake import read_pak
        invalid = [b'', md2()[:-1]]
        for offset, value in ((4, 7), (8, 0), (16, 12), (20, -1), (24, 0), (40, -1), (44, 0), (48, 1), (64, 999999)):
            data = bytearray(md2())
            struct.pack_into('<i', data, offset, value)
            invalid.append(data)
        for index in (0, 3):
            data = bytearray(md2())
            struct.pack_into('<H', data, struct.unpack_from('<i', data, 52)[0] + index * 2, 999)
            invalid.append(data)
        data = bytearray(md2())
        struct.pack_into('<f', data, struct.unpack_from('<i', data, 56)[0] + 52, float('nan'))
        invalid.append(data)  # Later frame ranges/transforms are validated too.
        for data in invalid:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                read_md2(data)
        for data in (b'', pcx()[:-1], b'NOPE' + pcx()[4:]):
            with self.assertRaises(ValueError):
                read_pcx(data)
        for data in (b'', pak([('model.md2', md2())])[:-1]):
            with self.assertRaises(ValueError):
                read_pak(data)

    def test_layering_dedupe_fallbacks_and_texture_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / 'baseq2', Path(tmp) / 'out'
            root.mkdir()
            (root / 'pak0.pak').write_bytes(pak([
                ('models/test/tris.md2', md2()), ('models/test/skin.pcx', pcx()),
                ('models/test/alternate.pcx', pcx(12)), ('sprites/test.sp2', b'IDS2'),
                ('models/broken/tris.md2', b'IDP2'),
                ('models/missing/tris.md2', md2(('absent.pcx',))),
                ('models/fallback/tris.md2', md2(('lost/skin.pcx',))),
                ('models/fallback/skin.pcx', pcx()),
                ('models/alias/tris.md2', md2(('models/test/../test/alternate.pcx',))),
            ]))
            (root / 'pak1.pak').write_bytes(pak([('models/test/tris.md2', md2(x=3))]))
            (root / 'pak2.pak').write_bytes(pak([('models/test/tris.md2', md2(x=4))]))
            (root / 'models/test').mkdir(parents=True)
            (root / 'models/test/tris.md2').write_bytes(md2(x=5))
            (root / 'models/test/skin.pcx').write_bytes(pcx(29))
            for player, x in (('male', 6), ('female', 7), ('cyborg', 6)):
                folder = root / 'players' / player
                folder.mkdir(parents=True)
                (folder / 'w_bfg.md2').write_bytes(md2(x=x))
            male = root / 'players/male'
            (male / 'tris.md2').write_bytes(md2(()))
            for name in ('grunt', 'other', 'grunt_i'):
                (male / (name + '.pcx')).write_bytes(pcx())
            files, records, counts = read_install(root.parent)
            self.assertEqual(files['models/test/tris.md2'][0], md2(x=5))
            self.assertEqual(files['models/test/skin.pcx'][0], pcx(29))
            self.assertEqual([counts[f'pak{i}.pak']['md2'] for i in range(3)], [5, 1, 1])
            report = import_catalog(root, output)
            self.assertEqual((report['ready'], report['duplicates'], report['overridden'], report['md2_skipped']), (7, 1, 3, 1))
            results = {r['source']: r for r in report['results'] if r['status'] == 'ready'}
            self.assertEqual(results['players/male/tris.md2']['skin'], 'players/male/grunt.pcx')
            self.assertIn('same-folder', results['models/fallback/tris.md2']['skin_reason'])
            self.assertIn('checker', results['models/missing/tris.md2']['skin_reason'])
            self.assertEqual(results['models/alias/tris.md2']['skin'], 'models/test/alternate.pcx')
            rows = json.loads((output / 'catalog.json').read_text())
            self.assertIn('players_female_w_bfg', {r['model_name'] for r in rows})
            self.assertIn('players_cyborg_w_bfg', {r['model_name'] for r in rows})
            self.assertNotIn('players_male_w_bfg', {r['model_name'] for r in rows})
            self.assertTrue(all(r['status'] != 'pending' for r in report['results']))
            self.assertTrue(all(r.get('reason') for r in report['results'] if r['status'] != 'ready'))
            textures = {r['source'] for r in report['texture_results']}
            self.assertTrue({'players/male/other.pcx', 'players/male/grunt_i.pcx', 'models/test/alternate.pcx'} <= textures)
            with Image.open(output / 'textures/models_test_skin.png') as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 255, 29))
            target = output / 'textures/models_test_skin.png'
            Image.new('RGB', (4, 2), '#123456').save(target)
            edited = target.read_bytes()
            import_catalog(root, output)
            self.assertEqual(target.read_bytes(), edited)

    def test_import_routes_and_static_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / 'baseq2', Path(tmp) / 'out'
            root.mkdir()
            (root / 'pak0.pak').write_bytes(pak([('models/test/tris.md2', md2()), ('models/test/skin.pcx', pcx())]))
            with patch.dict('app.pack_dirs', quake2=output):
                client = app.test_client()
                response = client.post('/import_quake2', json={'path': str(root)})
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json['ready'], 1)
                self.assertEqual(len(client.get('/list_models?game=quake2').json), 1)
                self.assertEqual(client.get('/model_json/models_test_tris?game=quake2').status_code, 200)
                with client.get('/texture/models_test_skin.png?game=quake2') as response:
                    self.assertEqual(response.status_code, 200)
                response = client.get('/export_glb/models_test_tris?game=quake2')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['X-Skinner-Animation-Status'], 'static')
                self.assertEqual(response.headers['X-Skinner-Animation-Clips'], '0')
                length = struct.unpack_from('<I', response.data, 12)[0]
                gltf = json.loads(response.data[20:20 + length])
                self.assertNotIn('animations', gltf)
                self.assertEqual(gltf['samplers'][0]['magFilter'], 9728)
                self.assertEqual(client.get('/export_obj/models_test_tris?game=quake2').status_code, 200)
                for body in ({}, {'path': ' '}, {'path': []}):
                    self.assertEqual(client.post('/import_quake2', json=body).status_code, 400)
                self.assertEqual(client.post('/import_quake2', json={'path': str(root)},
                                             headers={'Origin': 'https://elsewhere.invalid'}).status_code, 403)


@unittest.skipUnless((INSTALL / 'pak0.pak').is_file() and (INSTALL / 'players').is_dir(), 'needs classic Quake II baseq2')
class Quake2InstallTest(unittest.TestCase):
    def test_full_directory_and_loose_inventory(self):
        expected, counts = [], {}
        for path in sorted(INSTALL.glob('pak*.pak')):
            raw = path.read_bytes()
            offset, size = struct.unpack_from('<ii', raw, 4)
            names = [raw[i:i + 56].split(b'\0')[0].decode().lower() for i in range(offset, offset + size, 64)]
            counts[path.name] = len(names)
            expected.extend((path.name, n) for n in names if n.endswith(('.md2', '.sp2')))
        self.assertEqual(counts, {'pak0.pak': 3307, 'pak1.pak': 279, 'pak2.pak': 2})
        self.assertEqual(sum(n.endswith('.md2') for _, n in expected), 119)
        loose = [p.relative_to(INSTALL).as_posix().lower() for p in (INSTALL / 'players').rglob('*') if p.is_file()]
        # This install has 19 crakhor + 21 each cyborg/female/male, not the preliminary 85 estimate.
        self.assertEqual(sum(n.endswith('.md2') for n in loose), 82)
        self.assertEqual(sum(n.endswith('.pcx') for n in loose), 60)
        expected.extend(('loose', n) for n in loose if n.endswith(('.md2', '.sp2')))
        with tempfile.TemporaryDirectory() as tmp, patch('tools.import_quake2.available_games', return_value=['quake2']):
            report = import_catalog(INSTALL, tmp)
            self.assertCountEqual([(r['pak'], r['source']) for r in report['results']], expected)
            self.assertEqual((report['ready'], report['md2_entries'], report['duplicates'], report['md2_skipped'], report['overridden']),
                             (180, 201, 21, 0, 0))
            skipped = [r for r in report['results'] if r['status'] == 'skipped']
            self.assertEqual(len(skipped), 8)
            self.assertTrue(all(r['source'].endswith('.sp2') and r['reason'] for r in skipped))
            self.assertEqual(report['textures'], 214)
            self.assertTrue(all(r['status'] == 'ready' for r in report['texture_results']))
            self.assertEqual(sum(r['source'].startswith('players/') for r in report['texture_results']), 60)
            models = list((Path(tmp) / 'model_json').glob('*.json'))
            self.assertEqual(len(models), 180)
            for path in models:
                model = load_model_data(path)
                self.assertTrue(model['indices'])
                with Image.open(Path(tmp) / 'textures' / model['material_textures'][0]) as image:
                    image.verify()
            defaults = {r['source']: r['skin'] for r in report['results'] if r['source'].startswith('players/') and r['source'].endswith('/tris.md2')}
            self.assertEqual(defaults, {f'players/{p}/tris.md2': f'players/{p}/{s}.pcx' for p, s in
                                        (('male', 'grunt'), ('female', 'athena'), ('cyborg', 'oni911'))})

    def check_pack(self, game, directory, models):
        from tools.import_quake2 import read_pak
        entries = read_pak((INSTALL.parent / game / 'pak0.pak').read_bytes())
        expected = [n for n, _ in entries if n.endswith(('.md2', '.sp2'))]
        self.assertEqual(len(entries), directory)
        self.assertEqual(sum(n.endswith('.md2') for n in expected), models)
        with tempfile.TemporaryDirectory() as tmp:
            report = import_catalog(INSTALL, tmp)
            rows = [r for r in report['results'] if r['game'] == game]
            self.assertCountEqual([r['source'] for r in rows], expected)
            self.assertEqual(report['campaigns'][game]['ready'], models)
            self.assertTrue(all(r['skin'] for r in rows if r['status'] == 'ready'))
            self.assertTrue(all(r['status'] != 'pending' for r in rows))

    @unittest.skipUnless((INSTALL.parent / 'xatrix/pak0.pak').is_file(), 'needs The Reckoning')
    def test_reckoning_inventory(self):
        self.check_pack('xatrix', 832, 30)

    @unittest.skipUnless((INSTALL.parent / 'rogue/pak0.pak').is_file(), 'needs Ground Zero')
    def test_ground_zero_inventory(self):
        self.check_pack('rogue', 1983, 70)

    @unittest.skipUnless((INSTALL.parent / 'ctf/pak0.pak').is_file(), 'needs CTF')
    def test_ctf_inventory(self):
        self.check_pack('ctf', 169, 11)

    @unittest.skipUnless(all((INSTALL.parent / g / 'pak0.pak').is_file() for g in ('xatrix', 'rogue', 'ctf')), 'needs all packs')
    def test_all_22_missing_player_skins_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = import_catalog(INSTALL, tmp)
            weapons = ('w_chainfist', 'w_disrupt', 'w_etfrifle', 'w_phalanx', 'w_plasma', 'w_plauncher', 'w_ripper')
            rows = [r for r in report['results'] if r['game'] == 'quake2' and r['status'] == 'ready'
                    and r['source'].startswith('players/') and
                    (Path(r['source']).stem in weapons or r['source'] == 'players/crakhor/w_shotgun.md2')]
            self.assertEqual(len(rows), 22)
            for row in rows:
                self.assertTrue(row['skin'], row)
                self.assertEqual(row['skin_game'], 'quake2' if row['source'].endswith('w_shotgun.md2') else
                                 'xatrix' if Path(row['source']).stem in ('w_phalanx', 'w_ripper') else 'rogue')
                data = json.loads((Path(tmp) / 'model_json' / (row['model'] + '.json')).read_text())
                self.assertNotEqual(data['material_textures'][0], 'missing_skin.png')
                self.assertTrue((Path(tmp) / 'textures' / data['material_textures'][0]).is_file())


if __name__ == '__main__':
    unittest.main()

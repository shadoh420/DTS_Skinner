"""Synthetic fixtures; bounded retail smoke checks opt in with AZRAEL_INSTALL_TESTS=1."""
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

from tools.import_azrael import (CDReader, IDENTITY, build_model, decode_res,
                                 import_catalog, inventory, parse_ani, parse_xaf,
                                 read_install, read_rooms, res_records)
from tools.model_data import load_model_data
from tools.glb_exporter import model_to_glb

INSTALL = Path("C:/Program Files (x86)/GOG Galaxy/Games/Azrael's Tear")


def iso_record(name, lba, size, directory=False):
    raw = name if isinstance(name, bytes) else name.encode('ascii')
    row = bytearray(33 + len(raw) + (len(raw) % 2 == 0))
    row[0] = len(row)
    struct.pack_into('<I', row, 2, lba)
    struct.pack_into('>I', row, 6, lba)
    struct.pack_into('<I', row, 10, size)
    struct.pack_into('>I', row, 14, size)
    row[25] = 2 if directory else 0
    row[28:32] = b'\x01\0\0\x01'
    row[32] = len(raw)
    row[33:33 + len(raw)] = raw
    return row


def bin_image(payload=b'x' * 2100, name='SCENE.XAF;1'):
    image = bytearray(24 * 2352)
    def sector(lba, data):
        for start in range(0, len(data), 2048):
            at = (lba + start // 2048) * 2352 + 16
            image[at:at + min(2048, len(data) - start)] = data[start:start + 2048]
    pvd = bytearray(2048)
    pvd[:7] = b'\x01CD001\x01'
    struct.pack_into('<I', pvd, 80, 24)
    struct.pack_into('<H', pvd, 128, 2048)
    pvd[156:190] = iso_record(b'\0', 20, 2048, True)
    sector(16, pvd)
    sector(20, iso_record(b'\0', 20, 2048, True) + iso_record(b'\1', 20, 2048, True) + iso_record('TEXTURES', 21, 2048, True))
    sector(21, iso_record(b'\0', 21, 2048, True) + iso_record(b'\1', 20, 2048, True) + iso_record(name, 22, len(payload)))
    sector(22, payload)
    return image


def res():
    # Two leaves at internal node 256. LSB bit stream 0,1,1,0 => red,magenta,magenta,red.
    header = struct.pack('<15I', 2, 2, 1, 1, 8, 0, 2, 0, 0, 0, 0, 0, 0, 2, 0)
    tree = [0] * 1024
    tree[512:514] = [0, 1]
    palette = bytes((255, 0, 0, 99, 255, 0, 255, 88))
    payload = header + palette + struct.pack('<II1024HI', 4, 256, *tree, 6)
    return struct.pack('<I', len(payload)) + payload


def xaf(animated=True):
    # Face's second index selects one of FOUR normals, not the THREE UVs.
    return ('''// Formatting is intentionally unlike the retail files.
material "skin" {texture "test#.gif" colour {0,0,0,1} specular{0,15}}
material "hidden" {colour {1,1,1,0}}
object "root" { matrix{1,0,0,0, 0,1,0,0, 0,0,1,0, 10,20,30,1}
 object "child" {matrix{1,0,0,0, 0,1,0,0, 0,0,1,0, 1,2,3,1}
 mesh {vertices {0,0,0, 1,0,0, 0,1,0} normals {0,0,1, 0,0,1, 0,0,1, 0,0,1}
 texture_coordinates {0,0, 1,0, 0,1}
 faces{3,0,3,1,3,2,3, 3,0,0,1,0,2,0, 3,0,0,1,0,2,0, 0}
 face_materials {"skin", "hidden", ""} } } }
''' + ('''animation_set {animation "turn" {object_name "child"
rotate_keys{0,1,0,0,0, 10,0,1,0,0} position_keys{0,1,2,3} scale_keys{0,1,1,1}}}
''' if animated else '') + '\x1a').encode('ascii')


def ani(name='hero.xaf'):
    return f'NUM_FILES 1\nOFFSET 0,-7,0\nHEIGHT 33\nWIDTH 2\nNO_MOVE\nTRIGGER 12 sound.wav 100\nFILE {name}\nREST 2-4\nWALK 5-12\n\x1a'.encode()


def rooms(entries):
    def string(v):
        return v.encode() + b'\0' + b'\xcc' * (59 - len(v))
    return struct.pack('<H', len(entries)) + b''.join(string(name) + string(xaf) + struct.pack('<H', 1) + string('Entry' + name) + struct.pack('<H', 1) + string('door') + bytes(2) for name, xaf in entries)


def write_install(root):
    game = root / 'GAME' / 'AZRAEL'
    game.mkdir(parents=True)
    textures = root / 'TEXTURES'
    textures.mkdir()
    (game / 'ROOMS.DAT').write_bytes(rooms([('Start', 'room'), ('Boat', 'boat01'), ('Unused', '')]))
    (game / 'LURKA.ANI').write_bytes(ani())
    (game / 'ALIAS.ANI').write_bytes(ani())
    (game / 'SAILOR.ANI').write_bytes(ani('sailor_a.xaf'))
    (game / 'BAD.ANI').write_bytes(b'NUM_FILES 1')
    for name in ('hero', 'extra', 'copy', 'room', 'boat01', 'static', 'sailor_a'):
        (textures / (name + '.XAF')).write_bytes(xaf(name != 'static') + (b'\n' if name in ('extra', 'copy') else b''))
        (textures / (name + '.RES')).write_bytes(res())
    (textures / 'empty.xaf').write_bytes(b'')
    (textures / 'empty.res').write_bytes(b'')
    (textures / 'camera.xaf').write_bytes(b'object "dummy" {} animation_set {}')
    (textures / 'camera.res').write_bytes(b'')
    return game, textures


class AzraelTest(unittest.TestCase):
    def test_lazy_bin_cue_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'AZT.BIN'
            source.write_bytes(bin_image())
            (root / 'AZT.CUE').write_text('FILE "azt.bin" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n')
            cd = CDReader(root / 'AZT.CUE')
            self.assertEqual(cd.files, {'textures/scene.xaf': (22, 2100)})
            self.assertEqual(cd.read('TEXTURES/SCENE.XAF'), b'x' * 2100)
            with self.assertRaisesRegex(ValueError, 'bounded'):
                cd.read('textures/scene.xaf', 2099)
            with self.assertRaises(ValueError):
                cd.read('../outside')
            source.write_bytes(bin_image(name='../bad;1'))
            with self.assertRaises(ValueError):
                CDReader(source)
            source.write_bytes(bin_image()[:-1])
            with self.assertRaises(ValueError):
                CDReader(source)
            bad = bin_image()
            struct.pack_into('<I', bad, 16 * 2352 + 16 + 158, 10000)
            source.write_bytes(bad)
            with self.assertRaises(ValueError):
                CDReader(source)

    def test_folder_and_resource_cfg(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game, textures = write_install(root)
            actual, cd = read_install(root)
            self.assertEqual(actual, game)
            self.assertEqual(cd.read('textures/hero.xaf'), xaf())
            (game / 'RESOURCE.CFG').write_text(str(textures) + '\nD:\\SOUNDS\\\n')
            self.assertEqual(read_install(game)[1].read('textures/hero.xaf'), xaf())
            (game / 'RESOURCE.CFG').write_text('')
            self.assertIn('textures/hero.xaf', read_install(root)[1].files)

    def test_xaf_geometry_normals_uvs_materials_and_hierarchy(self):
        scene = parse_xaf(xaf())
        self.assertEqual(scene['textures'], ['test#.gif'])
        self.assertEqual(scene['materials']['skin']['specular'], [0, 15])
        self.assertEqual(scene['animations'][0]['rotate_keys'][1], [10, 0, 1, 0, 0])
        data = build_model(scene, {'skin': 'skin.png'})
        self.assertEqual(data['vertices'], [11, 23, -33, 12, 22, -33, 11, 22, -33])
        self.assertEqual(data['uvs'], [0, 1, 1, 0, 0, 0])
        self.assertEqual(data['indices'], [0, 1, 2])
        self.assertEqual(data['metadata']['omitted_faces'], {'alpha-zero': 1, 'material-less': 1})
        self.assertEqual(data['material_settings'], [{'unlit': True, 'alphaFunc': 'GE128'}])
        # A rotating parent must rotate the child's translation too.
        scene['objects'][0]['matrix'] = [0,1,0,0,-1,0,0,0,0,0,1,0,10,20,30,1]
        self.assertEqual(build_model(scene, {'skin':'skin.png'})['vertices'][-3:], [8,21,-33])

    def test_xaf_rejects_malformed_and_unbounded_input(self):
        for raw in (b'', b'object "a" {', b'}', b'object "a" { matrix{1} }',
                    xaf().replace(b'colour {0,0,0,1}', b'colour {nan,0,0,1}'),
                    xaf().replace(b'3,0,3,1,3,2,3', b'3,99,3,1,3,2,3'),
                    xaf().replace(b'3,0,3,1,3,2,3', b'3,0,9,1,3,2,3'),
                    xaf().replace(b'"skin", "hidden", ""', b'"skin"'),
                    b'object "a" {' * 66 + b'}' * 66):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                parse_xaf(raw)
        with patch('tools.import_azrael.MAX_BYTES', 10), self.assertRaises(ValueError):
            parse_xaf(xaf())

    def test_res_huffman_palette_key_stride_and_records(self):
        records = list(res_records(res() + res()))
        self.assertEqual(len(records), 2)
        image = decode_res(records[0], 'test#.gif')
        self.assertEqual([image.getpixel((x,y)) for y in range(2) for x in range(2)], [(255,0,0,255),(255,0,255,0),(255,0,255,0),(255,0,0,255)])
        self.assertEqual(decode_res(records[0], 'opaque.gif').getpixel((1,0)), (255,0,255,255))
        padded = bytearray(res())
        struct.pack_into('<I', padded, 4, 1)
        self.assertEqual(decode_res(next(res_records(padded))).size, (1,2))
        # A final partial word is legal only when enough real bits remain.
        partial = bytearray(res()[:-3])
        struct.pack_into('<I', partial, 0, len(partial)-4)
        self.assertEqual(decode_res(next(res_records(partial))).size, (2,2))
        leaf = bytearray(res()[:-4])
        struct.pack_into('<I', leaf, 0, len(leaf)-4)
        struct.pack_into('<I', leaf, 76, 1)
        self.assertEqual(decode_res(next(res_records(leaf))).getpixel((0,0)), (255,0,255,255))

    def test_res_rejects_truncation_cycles_bad_palette_and_dimensions(self):
        for raw in (res()[:3], res()[:-1]):
            with self.assertRaises(ValueError):
                list(res_records(raw))
        for offset, fmt, value in ((4,'I',9000),(56,'I',257),(72,'I',999),(76,'I',512),
                                    (80+512*2,'H',256),(80+513*2,'H',600),(80+513*2,'H',5)):
            raw=bytearray(res()); struct.pack_into('<'+fmt,raw,offset,value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                decode_res(next(res_records(raw)))
        raw = bytearray(res()[:-4]);struct.pack_into('<I',raw,0,len(raw)-4)
        with self.assertRaisesRegex(ValueError, 'bits'):
            decode_res(next(res_records(raw)))

    def test_ani_and_exact_rooms_records(self):
        data = parse_ani(ani())
        self.assertEqual(data['clips']['hero.xaf']['REST'], [2,4])
        self.assertEqual(data['properties']['OFFSET'], [0,-7,0])
        self.assertTrue(data['properties']['NO_MOVE'])
        self.assertEqual(data['triggers'], [['12','sound.wav','100']])
        for raw in (ani('../evil.xaf'),ani().replace(b'2-4',b'4-2'), b'REST 1-2', b'NUM_FILES 2\nFILE a.xaf'):
            with self.assertRaises(ValueError):parse_ani(raw)
        raw=rooms([('Start','room'),('Blank','')])
        result=read_rooms(raw)
        self.assertEqual(result['declared_count'],2)
        self.assertEqual(result['headers'][0]['objects'],['door'])
        self.assertEqual(result['headers'][1]['xaf'],'')
        for bad in (raw[:-1],raw+b'garbage',raw[:-2]+b'\x01\0'):
            with self.assertRaises(ValueError):read_rooms(bad)

    def test_catalog_aliases_room_exclusion_dedup_edits_and_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); game,textures=write_install(root); output=root/'output'
            report=import_catalog(root,output)
            self.assertEqual(report['categories'],{'Characters':1,'Other rigs':1})
            self.assertEqual(report['duplicates'],1)
            self.assertEqual(report['textures'],2)
            self.assertEqual(len(report['results']),9)
            catalog=json.loads((output/'catalog.json').read_text())
            self.assertEqual(catalog[0]['aliases'],['Alias','Lurka'])
            reasons={r['source']:r.get('reason','') for r in report['results']}
            self.assertIn('ROOMS.DAT',reasons['textures/boat01.xaf'])
            self.assertIn('placeholder',reasons['textures/sailor_a.xaf'])
            self.assertIn('camera',reasons['textures/camera.xaf'])
            self.assertEqual(report['ani_results'][1]['status'],'skipped')
            model=output/'model_json'/(catalog[0]['model_name']+'.json')
            data=load_model_data(model)
            self.assertEqual(data['material_texture_games'],['azrael'])
            glb=model_to_glb(data,output/'textures')
            self.assertEqual(glb[:4],b'glTF')
            size=struct.unpack_from('<I',glb,12)[0]
            document=json.loads(glb[20:20+size])
            self.assertEqual(document['materials'][0]['alphaMode'],'MASK')
            import app as server
            with patch.dict(server.pack_dirs, azrael=output):
                client=server.app.test_client()
                response=client.get('/export_glb/'+catalog[0]['model_name']+'?game=azrael')
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['X-Skinner-Animation-Status'],'static')
                response=client.get('/export_obj/'+catalog[0]['model_name']+'?game=azrael')
                self.assertEqual(response.status_code,200)
                with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                    opacity=next(n for n in archive.namelist() if '_skinner_opacity_0' in n)
                    with Image.open(io.BytesIO(archive.read(opacity))) as image:
                        self.assertEqual(image.getpixel((1,0)),0)
            png=output/'textures'/catalog[0]['texture_name']
            Image.new('RGBA',(2,2),(12,34,56,255)).save(png)
            before=png.read_bytes(); import_catalog(root,output)
            self.assertEqual(png.read_bytes(),before)
            report=import_catalog(root,output,'lurka')
            self.assertEqual(report['ready'],1)

    def test_app_import_dispatch_and_game_lists(self):
        import app as server
        from tools.model_data import MODEL_GAMES
        from tools.texture_workshop import TEXTURE_GAMES
        self.assertIn('azrael',MODEL_GAMES)
        self.assertIn('azrael',TEXTURE_GAMES)
        from tools.obj_exporter import q3_material_settings
        self.assertEqual(q3_material_settings(dict(game='quake2',material_textures=['a'],material_settings=[{'opacity':.2}])),[{}])
        self.assertEqual(q3_material_settings(dict(game='q3',material_textures=['a'],material_settings=[{}])),[{}])
        self.assertEqual(q3_material_settings(dict(game='sin',material_textures=['a'],material_settings=[{'opacity':.33}])),[{'opacity':.33}])
        with patch.object(server,'import_azrael_catalog',return_value={'ready':1}) as importer:
            response=server.app.test_client().post('/import_azrael',json={'path':'example'})
            self.assertEqual(response.status_code,200)
            importer.assert_called_once_with('example',server.pack_dirs['azrael'])
            self.assertEqual(server.app.test_client().post('/import_azrael',json={'path':''}).status_code,400)


@unittest.skipUnless(os.environ.get('AZRAEL_INSTALL_TESTS') == '1' and INSTALL.is_dir(),
                     'Optional local install smoke test (AZRAEL_INSTALL_TESTS=1)')
class AzraelInstallTest(unittest.TestCase):
    def test_one_character_no_import(self):
        game,cd=read_install(INSTALL)
        scene=parse_xaf(cd.read('textures/lurka.xaf'))
        self.assertEqual(len(scene['textures']),5)
        records=list(res_records(cd.read('textures/lurka.res')))
        self.assertEqual(len(records),5)
        self.assertTrue(decode_res(records[0],scene['textures'][0]).width)
        self.assertEqual(parse_ani((game/'LURKA.ANI').read_bytes())['clips']['lurka.xaf']['REST'],[334,344])


if __name__=='__main__':
    unittest.main()

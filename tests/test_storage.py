"""Indexed geometry and shared GPU-compressed Bloodlines textures; synthetic only."""
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
from tools.model_data import load_model_data, write_model_data, write_surface_data, read_geometry_buffers
from tools.texture_storage import dds_bytes, game_texture_dir, write_texture, SourceTextureStore, map_data_path
from tools import vtmb
from tools.import_vtmb import prepare_texture
from tests.test_vtmb import vtf


class StorageTests(unittest.TestCase):
    def test_ja_shared_originals_derivatives_and_edit_preservation(self):
        from types import SimpleNamespace
        from tools import import_ja as ja, import_ja_map as maps
        image = Image.new('RGBA', (4, 4), (20, 50, 90, 255))
        image.putpixel((0, 0), (200, 10, 40, 0))
        image.putpixel((3, 3), (5, 240, 30, 127))
        tga = io.BytesIO(); image.save(tga, format='TGA')
        jpg = io.BytesIO(); image.convert('RGB').save(jpg, format='JPEG'); raw = jpg.getvalue()
        values = {'z.jpg':raw, 'a.jpg':raw, 'cutout.tga':tga.getvalue()}
        assets = {name:dict(read=lambda raw=raw:raw,content_id=f'{zlib.crc32(raw):08x}:{len(raw)}') for name,raw in values.items()}
        game = SimpleNamespace(assets=assets,read=values.__getitem__,lighting=dict(picmip=0,gamma=1,intensity=1,hardwareGamma=False,cvars=dict(r_roundimagesdown=1)))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);out=root/'ja';directory=maps.texture_directory(root/'ja-maps')
            library=ja.TextureLibrary(out,[game]);model=library.convert(None,raw,'z.jpg')
            world=maps.Textures(directory,game);world.mipmap=False;world.picmip=False
            self.assertEqual(world.resolve(game,'a.jpg'),model)
            self.assertTrue(model.startswith('a__'));self.assertTrue(model.endswith('.jpg'))
            self.assertEqual((directory/model).read_bytes(),raw)
            png=world.resolve(game,'cutout.tga');self.assertTrue(png.endswith('.png'))
            self.assertEqual(Image.open(directory/png).convert('RGBA').tobytes(),image.tobytes())
            game.lighting['picmip']=1
            changed=maps.Textures(directory,game).resolve(game,'cutout.tga')
            self.assertTrue(changed.endswith('.png'))
            with Image.open(directory/changed) as converted:self.assertEqual(converted.size,(2,2))
            # The model import must not retire files created by the map importer.
            library.finish({model});self.assertTrue((directory/changed).exists())
            fresh=SourceTextureStore(directory,assets)
            fresh.finish(set());self.assertTrue((directory/model).exists());self.assertTrue((directory/png).exists())
            # Only files created by this import can be pruned, with a content check.
            unused=fresh.write('unused.png',image)
            edited_new=fresh.write('edited.png',image)
            (directory/edited_new).write_bytes(b'user edit')
            fresh.finish(set())
            self.assertFalse((directory/unused).exists());self.assertEqual((directory/edited_new).read_bytes(),b'user edit')
            # Older PNG assignments and user PNG edits take priority over JPG migration.
            old=directory/model.replace('.jpg','.png');Image.new('RGBA',(4,4),'orange').save(old)
            edited=old.read_bytes();self.assertEqual(fresh.convert(None,raw,'z.jpg'),old.name)
            fresh.finish(set());self.assertEqual(old.read_bytes(),edited)
            self.assertEqual(map_data_path(root,'ja','textures/'+model),directory/model)
            legacy=root/'ja-maps/textures/legacy.png';legacy.parent.mkdir(parents=True);legacy.write_bytes(edited)
            self.assertEqual(map_data_path(root,'ja','textures/legacy.png'),legacy)
            self.assertIsNone(map_data_path(root,'ja','../../outside'))

    def test_ja_jpg_routes_exports_and_binary_prop_geometry(self):
        import app as server
        from tools.glb_exporter import model_to_glb
        from tools.obj_exporter import json_to_obj_zip
        from tools.texture_workshop import TEXTURE_SUFFIXES
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);textures=game_texture_dir(root,'ja')
            stream=io.BytesIO();Image.new('RGB',(4,4),(22,77,131)).save(stream,format='JPEG');raw=stream.getvalue()
            write_texture(textures,'color.jpg',raw)
            self.assertIn('.jpg',TEXTURE_SUFFIXES)
            with patch.object(server,'local_data_dir',root),patch.dict(server.pack_dirs,ja=root/'ja'):
                client=server.app.test_client()
                self.assertIn('color.jpg',client.get('/list_textures?game=ja').json)
                tagged=client.post('/texture_tags?game=ja',json=dict(filename='color.jpg',tags=['blue']))
                self.assertEqual(tagged.status_code,200)
                self.assertEqual(tagged.json['tags'],['blue'])
                for route in ('/texture/color.jpg?game=ja','/ja-map-data/textures/color.jpg'):
                    response=client.get(route);self.assertEqual(response.data,raw);self.assertEqual(response.mimetype,'image/jpeg');response.close()
                for query in ('download=1','preview=1','rotation=90'):
                    response=client.get('/texture/color.jpg?game=ja&'+query)
                    self.assertEqual(response.mimetype,'image/png')
                self.assertIn('geometry-buffers.js',client.get('/maps/ja/').get_data(as_text=True))
            surface=dict(vertices=[0.,0.,0.,1/3,0.,0.,0.,1.,0.],normals=[0,0,1]*3,uvs=[0,0,1,0,0,1],indices=[0,1,2])
            path=root/'prop.json';write_surface_data(path,[surface,surface])
            description=json.loads(path.read_text());arrays=read_geometry_buffers(path,description['geometry_buffers'])
            self.assertNotIn('vertices',description);self.assertEqual(arrays['vertices'],surface['vertices']*2)
            self.assertEqual(arrays['indices'],[0,1,2,3,4,5]);self.assertEqual(description['groups'][1]['vertexStart'],3)
            data=dict(surface,game='ja',material_textures=['color.jpg'])
            write_model_data(root/'model.json',data)
            self.assertIn(b'image/png',model_to_glb(load_model_data(root/'model.json'),textures))
            json_to_obj_zip(root/'model.json',textures,root/'model.zip','model')
            with zipfile.ZipFile(root/'model.zip') as archive:
                self.assertTrue(any(n.endswith('.png') for n in archive.namelist()))

    def test_indexed_geometry_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = dict(game='vtmb', vertices=[0., 0., 0., 1/3, 0., 0., 0., 1., 0.],
                        normals=[0., 0., 1.]*3, uvs=[0., 0., 1., 0., 0., 1.], indices=[0, 1, 2],
                        colors=[.1, .2, .3]*3, material_textures=['x.dds'],
                        groups=[dict(start=0, count=3, materialIndex=0)])
            old, new = root/'old.json', root/'new.json'
            old.write_text(json.dumps(data)); write_model_data(new, data)
            self.assertEqual(load_model_data(old), load_model_data(new))
            write_model_data(root/'skin.json', dict(data, material_textures=['y.png']))
            self.assertEqual(len(list(root.glob('*.bin'))), 1)
            description = json.loads(new.read_text())
            self.assertNotIn('vertices', description)
            binary = root/description['geometry_buffers']['file']; binary.write_bytes(binary.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, 'range'):
                load_model_data(new)

    def test_dds_shared_routes_listing_and_exports(self):
        import app as server
        from tools.glb_exporter import model_to_glb
        from tools.obj_exporter import json_to_obj_zip
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); textures = game_texture_dir(root, 'vtmb')
            from tools.import_vtmb_map import import_maps
            with self.assertRaisesRegex(ValueError, 'Shared textures'):
                import_maps(root/'vtmb', root/'vtmb-maps')
            block = struct.pack('<HHI', 0xf800, 0x07e0, 0)
            raw = dds_bytes(4, 4, 'DXT1', [block]*3)
            write_texture(textures, 'red.dds', raw)
            write_texture(textures, 'red.dds', b'never overwrite')
            with patch.object(server, 'local_data_dir', root), patch.dict(server.pack_dirs, vtmb=root/'vtmb'):
                client = server.app.test_client()
                self.assertEqual(server.game_textures('vtmb'), textures)
                self.assertIn('red.dds', client.get('/list_textures?game=vtmb').json)
                a = client.get('/texture/red.dds?game=vtmb'); b = client.get('/vtmb-map-data/textures/red.dds')
                self.assertEqual(a.data, raw); self.assertEqual(b.data, raw)
                self.assertEqual(a.mimetype, 'image/vnd-ms.dds'); self.assertEqual(b.mimetype, a.mimetype)
                a.close(); b.close()
                for query in ('preview=1', 'download=1', 'rotation=90'):
                    response = client.get('/texture/red.dds?game=vtmb&'+query)
                    self.assertEqual(response.mimetype, 'image/png')
                    self.assertEqual(Image.open(io.BytesIO(response.data)).getpixel((0, 0)), (255, 0, 0, 255))
            data = dict(game='vtmb', winding='ccw', vertices=[0, 0, 0, 1, 0, 0, 0, 1, 0],
                        normals=[0, 0, 1]*3, uvs=[0, 0, 1, 0, 0, 1], indices=[0, 1, 2], material_textures=['red.dds'])
            path = root/'model.json'; write_model_data(path, data)
            loaded = load_model_data(path)
            glb = model_to_glb(loaded, textures)
            self.assertIn(b'image/png', glb)
            output = root/'export.zip'
            json_to_obj_zip(path, textures, output, 'model')
            with zipfile.ZipFile(output) as archive:
                png = next(n for n in archive.namelist() if n.endswith('.png'))
                self.assertEqual(Image.open(io.BytesIO(archive.read(png))).getpixel((0, 0)), (255, 0, 0, 255))

    def test_vtf_dds_mips_alpha_and_orientation(self):
        # Asymmetric rows and columns: copying blocks must agree with the old decoder.
        color = struct.pack('<HHI', 0xf800, 0x07e0, 0x55aaff00)
        for fmt, block in ((13, color), (14, bytes.fromhex('1032547698badcfe')+color),
                           (15, b'\xff\0'+bytes.fromhex('123456789abc')+color),
                           (20, struct.pack('<HHI', 0, 0xffff, 0xffffffff))):
            raw = vtf(fmt, block*3, 4, 4, mips=3)
            old, _ = vtmb.read_vtf(raw); dds, info = vtmb.read_vtf(raw, compressed=True)
            self.assertEqual(dds[128:], block*3)
            self.assertEqual(Image.open(io.BytesIO(dds)).convert('RGBA').tobytes(), old.tobytes())
            self.assertEqual(info['mipmaps'], 3)
        with self.assertRaises(ValueError):
            vtmb.read_vtf(raw[:-1], compressed=True)
        red = struct.pack('<HHI', 0xf800, 0, 0)
        blue = struct.pack('<HHI', 31, 0, 0)
        green = struct.pack('<HHI', 0x07e0, 0, 0)
        raw = vtf(13, blue+green+red+green+color+green, 4, 4, mips=3, frames=2)
        payload, _ = vtmb.read_vtf(raw, compressed=True)
        self.assertEqual(payload[128:], color+red+blue)  # reverse mips; select frame 0 at each level
        from tools.import_vtmb_map import EmbeddedAsset
        files = {'materials/test.vtf': EmbeddedAsset(raw, 'synthetic')}
        vmt = dict(shader='UnlitGeneric', params={'$basetexture': '/materials/TEST.vtf'})
        first = prepare_texture(files, vmt, 'materials/model.vmt')
        vmt['params']['$basetexture'] = 'test'
        second = prepare_texture(files, vmt, 'materials/maps/world.vmt')
        self.assertEqual(first[:2], second[:2])  # model/world VMT paths share the base asset
        full = vtf(13, red, 4, 4)
        packed = zlib.compress(full[64:])
        head = struct.pack('<4sHBBI4I', b'TTH\0', 1, 1, 0, 64, 64, 0, len(full), len(packed))
        files = {'materials/test.tth': EmbeddedAsset(head+full[:64], 'synthetic'),
                 'materials/test.ttz': EmbeddedAsset(packed, 'synthetic')}
        decoded, _ = vtmb.read_texture(files, 'test')
        compressed, _ = vtmb.read_texture(files, 'test', compressed=True)
        self.assertEqual(decoded.tobytes(), Image.open(io.BytesIO(compressed)).convert('RGBA').tobytes())
        self.assertEqual(compressed[128:], red)

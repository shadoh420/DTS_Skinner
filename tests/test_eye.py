"""Synthetic Source fixtures only; default tests never read the E.Y.E. install."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from tools import source_assets, source_mdl, vtmb
from tools.import_eye import drop_meshes, import_catalog
from tools.model_data import load_model_data, MODEL_GAMES
from tools.texture_workshop import TEXTURE_GAMES
from tools.texture_storage import game_texture_dir
from tools.glb_exporter import model_to_glb
from tools.obj_exporter import json_to_obj_zip


def model_fixture(version=48, vtx_version=7, fixups=False, pose=False):
    """Unused vertex before the model, two meshes, two skins, one default body.

    Exercises model-global VVD byte offsets AND mesh-relative VTX originals.
    The optional frame moves X by two, using Source's half-float raw position.
    """
    data = bytearray(408)
    def alloc(size):
        at = len(data); data.extend(bytes(size)); return at
    def put(at, fmt, *v): struct.pack_into('<'+fmt, data, at, *v)
    def string(text):
        at = len(data); data.extend(text.encode()+b'\0'); return at
    data[:4] = b'IDST'; put(4, 'iI', version, 123)
    bone = alloc(216); put(156, '2i', 1, bone)
    put(bone+4, 'i', -1); put(bone+44, '4f', 0, 0, 0, 1)
    put(bone+96, '12f', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)
    if pose:
        anim = alloc(100); put(180, '2i', 1, anim); put(anim+12, '2i', 0, 1)
        frame = alloc(10); put(anim+52, '2i', 0, frame-anim)
        put(frame, 'BBh3e', 0, 1, 0, 2, 0, 0)
        seq = alloc(212); put(188, '2i', 1, seq)
        put(seq+4, 'i', string('idle')-seq)
        ref = alloc(2); put(ref, 'h', 0); put(seq+56, '2i', 1, ref-seq)
    tex = alloc(128); put(204, '2i', 2, tex)
    for i, name in enumerate(('red', 'blue')):
        put(tex+i*64, 'i', string(name)-tex-i*64)
    dirs = alloc(4); put(212, '2i', 1, dirs); put(dirs, 'i', string('models/test/'))
    skins = alloc(8); put(220, '3i', 2, 2, skins); put(skins, '4H', 0, 1, 1, 0)
    bp = alloc(16); put(232, '2i', 1, bp)
    model = alloc(148); put(bp+4, '3i', 1, 1, model-bp)
    data[model:model+5] = b'tiny\0'
    meshes = alloc(232)
    dtype = source_mdl.VERTEX43 if version == 43 else source_mdl.VERTEX
    put(model+72, '4i', 2, meshes-model, 6, dtype.itemsize)
    for i in range(2): put(meshes+i*116, '4i', i, model-meshes-i*116, 3, i*3)
    put(76, 'i', len(data))

    rows = np.zeros(7, dtype=dtype)
    rows['weights'][:, 0] = 1; rows['count'][:] = 1; rows['normal'][:, 2] = 1
    rows['position'] = [(99, 99, 99), (0, 0, 0), (1, 0, 0), (0, 1, 0),
                        (2, 0, 0), (3, 0, 0), (2, 1, 0)]
    rows['uv'] = [(0, 0), (0, 0), (1, 0), (0, 1), (0, 0), (1, 0), (0, 1)]
    fix = b''
    if fixups:
        rows = rows[[3, 4, 5, 6, 0, 1, 2]]
        fix = struct.pack('<6i', 0, 4, 3, 0, 0, 4)
    vvd = bytearray(64)
    struct.pack_into('<4siIi8i4i', vvd, 0, b'IDSV', 4, 123, 1, 7, *([0]*7),
                     2 if fixups else 0, 64, 64+len(fix), 64+len(fix)+rows.nbytes)
    vvd += fix+rows.tobytes()+bytes(7*16)

    vtx = bytearray(64+2*9)
    def vp(at, fmt, *v): struct.pack_into('<'+fmt, vtx, at, *v)
    vp(0, 'iiHHiI4i', vtx_version, 32, 3, 3, 3, 123, 1, 0, 1, 36)
    vp(36, '2i', 1, 8); vp(44, '2i', 1, 8); vp(52, '2if', 2, 12, 0)
    stride, original = (15, 12) if vtx_version == 6 else (9, 4)
    for i in range(2):
        mesh = 64+i*9; sg = len(vtx)
        vtx.extend(bytes(25+3*stride+6+27)); vp(mesh, '2iB', 1, sg-mesh, 0)
        vp(sg, '6iB', 3, 25, 3, 25+3*stride, 1, 25+3*stride+6, 0)
        for j in range(3): vp(sg+25+j*stride+original, 'H', j)
        vp(sg+25+3*stride, '3H', 0, 2, 1)
        vp(sg+25+3*stride+6, '4ihB2i', 3, 0, 3, 0, 1, 1, 0, 0)
    return bytes(data), bytes(vvd), bytes(vtx)


def vtf_fixture(fmt=13, minor=2, pixels=None, mips=1):
    header = bytearray(88 if minor >= 3 else 80 if minor >= 2 else 64)
    struct.pack_into('<4sIIIHHIHH', header, 0, b'VTF\0', 7, minor, len(header), 4, 4, 0, 1, 65535)
    struct.pack_into('<IBIBB', header, 52, fmt, mips, 0xffffffff, 0, 0)
    if minor >= 2: struct.pack_into('<H', header, 63, 1)
    if minor >= 3:
        struct.pack_into('<I', header, 68, 1); header[80:84] = b'\x30\0\0\0'
        struct.pack_into('<I', header, 84, len(header)+7)
    if pixels is None: pixels = struct.pack('<HHI', 0xf800, 0x07e0, 0)*mips
    return bytes(header)+(bytes(7) if minor>=3 else b'')+pixels


def install_fixture(root):
    game = root/'EYE'; game.mkdir(parents=True)
    (game/'gameinfo.txt').write_text('GameInfo { FileSystem { SearchPaths { Game |gameinfo_path|. Game EYE } } }')
    mdl, vvd, vtx = model_fixture(pose=True, fixups=True)
    assets = {'models/props/tiny.mdl': mdl, 'models/props/tiny.vvd': vvd, 'models/props/tiny.dx90.vtx': vtx,
              'materials/models/test/red.vmt': b'VertexLitGeneric { "$basetexture" "/test/red" "$alphatest" "1" }',
              'materials/models/test/blue.vmt': b'Patch { include "models/test/red.vmt" replace { "$alphatest" "0" } insert { "$translucent" "1" } }',
              'materials/test/red.vtf': vtf_fixture(mips=3),
              'materials/models/props/tiny.mdl': mdl, 'models/vents/.mdl': mdl}
    for name, raw in assets.items():
        p = game/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
    return game


class EyeTests(unittest.TestCase):
    def test_versions_fixups_mesh_bases_and_skins(self):
        for version, vtx in ((43, 6), (44, 6), (44, 7), (48, 7)):
            for fixups in (False, True):
                with self.subTest(version=version, fixups=fixups):
                    result = source_mdl.read_model(*model_fixture(version, vtx, fixups))
                    self.assertEqual(result['vertices'][:9], [0,0,0, 0,0,1, 1,0,0])
                    self.assertEqual(result['indices'], [0,1,2,3,4,5])
                    self.assertEqual(result['skin_table'], [[0,1],[1,0]])
                    self.assertEqual(result['groups'][1]['vvd_first'], 4)
                    self.assertEqual(result['metadata']['pose']['pose'], 'bind')

    def test_local_pose_and_explicit_bind_fallback(self):
        mdl, vvd, vtx = model_fixture(pose=True)
        mesh = source_mdl.read_model(mdl, vvd, vtx)
        self.assertEqual(mesh['metadata']['pose']['pose'], 'idle')
        self.assertEqual(mesh['vertices'][:3], [0,0,2])
        data = bytearray(mdl); a = struct.unpack_from('<i', data, 184)[0]
        struct.pack_into('<i', data, a+52, 1)  # external .ani
        mesh = source_mdl.read_model(data, vvd, vtx)
        self.assertEqual(mesh['vertices'][:3], [0,0,0])
        self.assertIn('External', mesh['metadata']['pose']['fallbacks'][0]['reason'])
        data = bytearray(mdl); s = struct.unpack_from('<i', data, 192)[0]
        struct.pack_into('<i', data, s+12, 4)  # STUDIO_DELTA, distinct from animation-bone flag 0x10
        self.assertEqual(source_mdl.read_model(data,vvd,vtx)['metadata']['pose']['pose'], 'bind')

    def test_lod0_material_replacement_and_default_bodygroup(self):
        mdl,vvd,vtx = model_fixture()
        data=bytearray(vtx); offset=len(data)
        struct.pack_into('<i',data,24,offset)
        data.extend(struct.pack('<2ihi',1,8,0,6)+b'other/red\0')
        result=source_mdl.read_model(mdl,vvd,data)
        self.assertEqual(result['material_names'],['other/red','blue'])
        self.assertEqual(result['metadata']['material_replacements'][0]['slot'],0)
        # Add a second (empty) submodel to the bodypart. Default model stays model 0.
        m=bytearray(mdl); bp=struct.unpack_from('<i',m,236)[0]
        old=bp+struct.unpack_from('<i',m,bp+12)[0]; new=len(m)
        copied=bytearray(m[old:old+148]); relative=struct.unpack_from('<i',copied,76)[0]
        struct.pack_into('<i',copied,76,old+relative-new)
        m.extend(copied+bytes(148)); struct.pack_into('<i',m,76,len(m))
        struct.pack_into('<i',m,bp+4,2); struct.pack_into('<i',m,bp+12,new-bp)
        v=bytearray(vtx); new=len(v); v.extend(v[44:52]+bytes(8))
        struct.pack_into('<i',v,new+4,52-new); struct.pack_into('<2i',v,36,2,new-36)
        result=source_mdl.read_model(m,vvd,v)
        self.assertEqual(result['indices'],[0,1,2,3,4,5])
        self.assertEqual(result['metadata']['bodygroups'][0]['alternatives'],2)

    def test_animation_rle_and_packed_quaternions(self):
        mdl,vvd,vtx=model_fixture(pose=True)
        data=bytearray(mdl); a=struct.unpack_from('<i',data,184)[0]
        bone=struct.unpack_from('<i',data,160)[0]
        struct.pack_into('<3f',data,bone+72,1,1,1)
        at=len(data); struct.pack_into('<i',data,a+56,at-a)
        # Animated position: X pointer to first RLE value, other axes use defaults.
        data.extend(struct.pack('<BBh3hBBh',0,4,0,6,0,0,1,1,3))
        struct.pack_into('<i',data,76,len(data))
        self.assertEqual(source_mdl.read_model(data,vvd,vtx)['vertices'][:3],[0,0,3])
        for bits,flag,packed in ((48,2,32768+(32768<<16)+(16384<<32)),
                                 (64,32,1048576+(1048576<<21)+(1048576<<42))):
            data=bytearray(mdl); at=len(data); struct.pack_into('<i',data,a+56,at-a)
            data.extend(struct.pack('<BBh',0,flag|1,0)+packed.to_bytes(bits//8,'little')+struct.pack('<3e',4,0,0))
            struct.pack_into('<i',data,76,len(data))
            self.assertEqual(source_mdl.read_model(data,vvd,vtx)['vertices'][:3],[0,0,4])

    def test_strips_and_strict_checks(self):
        mdl, vvd, vtx = model_fixture(fixups=True)
        bad = bytearray(vvd); struct.pack_into('<I', bad, 8, 999)
        with self.assertRaisesRegex(ValueError, 'checksum'): source_mdl.read_model(mdl, bad, vtx)
        bad = bytearray(vtx); struct.pack_into('<I', bad, 16, 999)
        with self.assertRaisesRegex(ValueError, 'checksum'): source_mdl.read_model(mdl, vvd, bad)
        bad = bytearray(vvd); struct.pack_into('<i', bad, 80, 4)
        with self.assertRaisesRegex(ValueError, 'fixup'): source_mdl.read_model(mdl, bad, vtx)
        with self.assertRaises(ValueError): source_mdl.read_model(mdl, vvd[:-1], vtx)
        bad = bytearray(vtx); sg = 64+struct.unpack_from('<i', bad, 68)[0]
        struct.pack_into('<H', bad, sg+25+4, 3)
        with self.assertRaisesRegex(ValueError, 'original mesh'): source_mdl.read_model(mdl, vvd, bad)
        strips = bytearray(vtx)
        for mesh in (64, 73):
            group = mesh+struct.unpack_from('<i', strips, mesh+4)[0]
            strip = group+struct.unpack_from('<i', strips, group+20)[0]
            strips[strip+18] = 2
        self.assertEqual(source_mdl.read_model(mdl,vvd,strips)['indices'], [0,1,2,3,4,5])

    def test_vtf_versions_resource_mips_and_formats(self):
        for minor in range(5):
            for fmt, size in ((13,8), (14,16), (15,16)):
                raw = bytes([10])*size+bytes([20])*size+bytes([30])*size
                dds, info = vtmb.read_vtf(vtf_fixture(fmt,minor,raw,3), compressed=True)
                self.assertEqual(dds[:4], b'DDS ')
                self.assertEqual(dds[128:], bytes([30])*size+bytes([20])*size+bytes([10])*size)
                self.assertEqual(info['mipmaps'], 3)
                self.assertEqual(Image.open(io.BytesIO(dds)).size, (4,4))
        for fmt, pixel, expected in ((3,b'\x01\x02\x03',(3,2,1,255)),
                                     (12,b'\x01\x02\x03\x80',(3,2,1,128)),
                                     (22,b'\x80\x7f',(0,255,128,255)),
                                     (24,struct.pack('<4e',1,0,0,.5),(255,0,0,128))):
            image, _ = vtmb.read_vtf(vtf_fixture(fmt,4,pixel*16))
            self.assertEqual(image.getpixel((0,0)), expected)
        bad = bytearray(vtf_fixture(minor=4)); bad[83] = 2
        with self.assertRaisesRegex(ValueError,'inline'): vtmb.read_vtf(bad)

    def test_import_shared_geometry_textures_exports_and_routes(self):
        self.assertIn('eye', MODEL_GAMES); self.assertIn('eye', TEXTURE_GAMES)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); game = install_fixture(root/'install'); output = root/'data'/'eye'
            report = import_catalog(game, output)
            self.assertEqual((report['ready'],report['skipped'],report['duplicates']), (2,1,1))
            self.assertEqual(report['unresolved_used_materials'], 0)
            index = json.loads((output/'model-index.json').read_text())
            self.assertEqual(index['materials/models/props/tiny.mdl']['alias_of'], 'models/props/tiny.mdl')
            paths = sorted((output/'model_json').glob('*.json'))
            a,b = [json.loads(p.read_text()) for p in paths]
            self.assertEqual(a['geometry_buffers'], b['geometry_buffers'])
            data = load_model_data(paths[0]); other = load_model_data(paths[1])
            self.assertEqual(data['vertices'], other['vertices'])
            self.assertNotEqual(data['groups'][0]['materialIndex'], other['groups'][0]['materialIndex'])
            self.assertEqual(data['vertices'][:3], [0,0,2])
            textures = game_texture_dir(root/'data','eye')
            dds = next(textures.glob('*.dds')); before = dds.read_bytes()
            # The model importer preserves an unrelated future map texture and existing edits.
            (textures/'map-only.png').write_bytes(b'preserve')
            (game/'materials/test/red.vtf').write_bytes(vtf_fixture(pixels=bytes(8)))
            import_catalog(game,output)
            self.assertEqual(dds.read_bytes(), before)
            self.assertEqual((textures/'map-only.png').read_bytes(), b'preserve')
            self.assertEqual(model_to_glb(data,textures)[:4], b'glTF')
            archive = root/'model.zip'
            json_to_obj_zip(paths[0],textures,archive,'tiny')
            self.assertEqual(archive.read_bytes()[:2], b'PK')
            import app
            with patch.dict(app.pack_dirs,eye=output):
                self.assertEqual(app.game_textures('eye'), textures)
                client = app.app.test_client()
                self.assertEqual(len(client.get('/list_models?game=eye').json),3)
                self.assertEqual(client.get('/model_json/'+paths[0].stem+'?game=eye').json['vertices'],data['vertices'])
                self.assertIn(dds.name,client.get('/list_textures?game=eye').json)
                response = client.get('/texture/'+dds.name+'?game=eye')
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.mimetype,'image/vnd-ms.dds')
                response.close()
                with patch('app.import_eye_catalog',return_value={'ready':2}) as importer:
                    response = client.post('/import_eye',json={'path':str(game)})
                    self.assertEqual(response.json,{'ready':2})
                    importer.assert_called_once_with(str(game),output)

    def test_search_order_and_vtx_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); game=install_fixture(root)
            fallback=root/'platform'/'materials'/'test'; fallback.mkdir(parents=True)
            (fallback/'red.vtf').write_bytes(b'bad fallback')
            files, info=source_assets.read_install(game)
            self.assertEqual(files['materials/test/red.vtf'].path,(game/'materials/test/red.vtf').resolve())  # CI's temp folder is an 8.3 short name
            self.assertEqual(len(info['search_paths']),2)
            (game/'models/props/tiny.sw.vtx').write_bytes((game/'models/props/tiny.dx90.vtx').read_bytes())
            (game/'models/props/tiny.dx90.vtx').write_bytes(b'bad')
            files,_=source_assets.read_install(game)
            result=source_mdl.load_model(files,'models/props/tiny.mdl')
            self.assertTrue(result['metadata']['vtx'].endswith('.sw.vtx'))
            self.assertEqual(len(result['metadata']['companion_failures']),1)

    def test_scope_overlay_meshes_dropped_with_their_vertices(self):
        mesh=dict(vertices=[0,0,0, 1,0,0, 0,1,0, 9,9,99, 9,9,98, 9,8,99, 2,0,0, 2,1,0, 3,0,0],
                  normals=[0,0,1]*9, uvs=[0,0]*9, indices=[0,1,2, 3,4,5, 6,7,8],
                  groups=[dict(start=0,count=3,vertex_start=0,vertex_count=3,vvd_first=0),
                          dict(start=3,count=3,vertex_start=3,vertex_count=3,vvd_first=3),
                          dict(start=6,count=3,vertex_start=6,vertex_count=3,vvd_first=6)])
        self.assertEqual(drop_meshes(mesh,[10,11,12],[1]),[10,12])
        self.assertEqual(mesh['indices'],[0,1,2,3,4,5])
        self.assertEqual(max(mesh['vertices']),3)
        self.assertEqual(len(mesh['uvs']),12)
        self.assertEqual([(g['start'],g['vertex_start'],g['vvd_first']) for g in mesh['groups']],[(0,0,0),(3,3,6)])
        with self.assertRaises(ValueError):
            drop_meshes(dict(mesh),[10,12],[0,1])


if __name__ == '__main__': unittest.main()

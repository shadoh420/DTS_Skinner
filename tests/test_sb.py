"""Invented UE 4.22 pak and cooked packages only; no installed game required."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

from tools.import_sb import category, import_catalog
from tools.model_data import MODEL_GAMES
from tools.texture_workshop import TEXTURE_GAMES
from tools.ue4_package import PACKAGE_TAG, PAK_MAGIC, Library, mounted


def pack(fmt, *values):
    return struct.pack('<'+fmt, *values)


def fstring(text):
    raw = text.encode('latin1')+b'\0'
    return pack('i', len(raw))+raw


class Asset:
    """One cooked package: names, imports and exports, written as .uasset + .uexp."""
    def __init__(self, path):
        self.path = path; self.names = ['None']; self.imports = []; self.exports = []

    def name(self, value):
        if value not in self.names:
            self.names.append(value)
        return pack('ii', self.names.index(value), 0)

    def tag(self, name, kind, raw, extra=b''):
        return self.name(name)+self.name(kind)+pack('ii', len(raw), 0)+extra+b'\0'+raw

    def obj(self, name, ref):
        return self.tag(name, 'ObjectProperty', pack('i', ref))

    def struct_array(self, name, struct_name, elements):
        inner = self.name(name)+self.name('StructProperty')+pack('ii', sum(map(len, elements)), 0)+self.name(struct_name)+b'\0'*16+b'\0'
        raw = pack('i', len(elements))+inner+b''.join(elements)
        return self.tag(name, 'ArrayProperty', raw, self.name('StructProperty'))

    def add_import(self, class_package, class_name, outer, name):
        self.imports.append((self.name(class_package), self.name(class_name), outer, self.name(name)))
        return -len(self.imports)

    def add_export(self, cls, name, payload):
        self.exports.append((cls, self.name(name), payload))
        return len(self.exports)

    def build(self):
        for name in ('/Script/CoreUObject', 'Package'):
            self.name(name)
        names = b''.join(fstring(n)+b'\0'*4 for n in self.names)
        imports = b''.join(cp+cn+pack('i', outer)+n for cp, cn, outer, n in self.imports)

        def summary(header_size, name_offset, export_offset, import_offset):
            return (pack('Iiiii', PACKAGE_TAG, -7, 0, 0, 0)+pack('i', 0)+pack('i', header_size)+fstring('None')+pack('I', 0x80000000)
                    +pack('ii', len(self.names), name_offset)+pack('ii', 0, 0)
                    +pack('5i', len(self.exports), export_offset, len(self.imports), import_offset, 0)
                    +pack('ii', 0, 0)+pack('i', 0)+pack('i', 0)+b'\0'*16+pack('i', 0)
                    +(pack('HHHI', 4, 22, 0, 0)+fstring(''))*2+pack('Ii', 0, 0)+pack('I', 0)+pack('i', 0)
                    +pack('i', 0)+pack('q', header_size+sum(len(p) for _, _, p in self.exports))+pack('i', 0)+pack('i', 0)+pack('ii', 0, 0))
        size = len(summary(0, 0, 0, 0))
        name_offset, import_offset = size, size+len(names)
        export_offset = import_offset+len(imports)
        header_size = export_offset+104*len(self.exports)
        offset, table, body = header_size, b'', b''
        for cls, name, payload in self.exports:
            table += pack('4i', cls, 0, 0, 0)+name+pack('I', 0)+pack('qq', len(payload), offset)+b'\0'*(12+16+4+8+20)
            offset += len(payload); body += payload
        header = summary(header_size, name_offset, export_offset, import_offset)+names+imports+table
        assert len(header) == header_size
        return {self.path+'.uasset': header, self.path+'.uexp': body+pack('I', PACKAGE_TAG)}


def write_pak(path, files):
    data, index = b'', fstring('../../../')+pack('i', len(files))
    for name, raw in files.items():
        record = pack('qqqB', 0, len(raw), len(raw), 0)+b'\0'*20+pack('BI', 0, 0)
        index += fstring(name)+pack('qqqB', len(data), len(raw), len(raw), 0)+b'\0'*20+pack('BI', 0, 0)
        data += record+raw
    footer = b'\0'*16+b'\0'+pack('IIqq', PAK_MAGIC, 8, len(data), len(index))+b'\0'*20+b'\0'*128
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data+index+footer)


def bulk(size, raw):
    return pack('ii', size, len(raw)//size)+raw


def static_mesh(asset, material_ref):
    element = asset.obj('MaterialInterface', material_ref)+asset.name('None')
    props = asset.struct_array('StaticMaterials', 'StaticMaterial', [element])+asset.name('None')
    points = [(0, 0, 0), (100, 0, 0), (0, 100, 0), (0, 0, 100)]
    n = len(points)
    native = (b'\1\0'+pack('i', 1)+pack('ii', 0, 0)+b'\0'*16+pack('i', 0)+pack('i', 1)+b'\1\0'
              +pack('i', 1)+pack('5i', 0, 0, 4, 0, 3)+pack('ii', 1, 1)+pack('f', 0)
              +pack('ii', 12, n)+bulk(12, b''.join(pack('3f', *p) for p in points))
              +b'\1\0'+pack('ii', 1, n)+pack('ii', 1, 0)+bulk(8, bytes([0, 0, 0, 0, 0, 0, 127, 127])*n)
              +bulk(8, b''.join(pack('2f', i/4, 0) for i in range(n)))
              +b'\1\0'+pack('ii', 4, 0)+pack('i', 0)+bulk(1, pack('12H', 0, 1, 2, 0, 2, 3, 0, 3, 1, 1, 3, 2)))
    return props+pack('i', 0)+native


def texture(asset):
    top = b'\xff\xff\x00\x00'+b'\0'*4  # one DXT1 block
    mip = pack('i', 1)+pack('Iiiq', 0x40, len(top), len(top), 0)+top+pack('iii', 4, 4, 1)
    return (asset.name('None')+pack('i', 0)+b'\1\0\1\0'+pack('i', 1)+asset.name('PF_DXT1')+pack('ii', 0, 0)
            +pack('iii', 4, 4, 1)+fstring('PF_DXT1')+pack('i', 0)+pack('i', 1)+mip+asset.name('None'))


def material(asset, texture_ref):
    info = asset.tag('ParameterInfo', 'StructProperty', asset.tag('Name', 'NameProperty', asset.name('DiffuseTexture'))+asset.name('None'),
                     asset.name('MaterialParameterInfo')+b'\0'*16)
    element = info+asset.obj('ParameterValue', texture_ref)+asset.name('None')
    return asset.struct_array('TextureParameterValues', 'TextureParameterValue', [element])+asset.name('None')+pack('i', 0)


def fixture(root):
    files = {}
    tex = Asset('g3/Content/Env/T_Rock')
    tex.add_export(0, 'T_Rock', texture(tex)); tex.exports[0] = (-2,)+tex.exports[0][1:]
    tex.add_import('/Script/CoreUObject', 'Package', 0, '/Script/Engine'); tex.add_import('/Script/Engine', 'Class', -1, 'Texture2D')
    files.update(tex.build())
    mat = Asset('g3/Content/Env/MI_Rock')
    package = mat.add_import('/Script/CoreUObject', 'Package', 0, '/Game/Env/T_Rock')
    texture_ref = mat.add_import('/Script/Engine', 'Texture2D', package, 'T_Rock')
    mat.add_import('/Script/CoreUObject', 'Package', 0, '/Script/Engine'); cls = mat.add_import('/Script/Engine', 'Class', -3, 'MaterialInstanceConstant')
    mat.add_export(cls, 'MI_Rock', material(mat, texture_ref))
    files.update(mat.build())
    sm = Asset('g3/Content/Env/SM_Rock')
    package = sm.add_import('/Script/CoreUObject', 'Package', 0, '/Game/Env/MI_Rock')
    material_ref = sm.add_import('/Script/Engine', 'MaterialInstanceConstant', package, 'MI_Rock')
    sm.add_import('/Script/CoreUObject', 'Package', 0, '/Script/Engine'); cls = sm.add_import('/Script/Engine', 'Class', -3, 'StaticMesh')
    sm.add_export(cls, 'SM_Rock', static_mesh(sm, material_ref))
    files.update(sm.build())
    write_pak(root/Library.pak_path, files)


class SpellbreakModels(unittest.TestCase):
    def test_registered(self):
        self.assertIn('sb', MODEL_GAMES); self.assertIn('sb', TEXTURE_GAMES)

    def test_mount_points(self):
        self.assertEqual(mounted('g3/Content/Maps/X.umap'), '/Game/Maps/X')
        self.assertEqual(mounted('Engine/Content/BasicShapes/Cube.uasset'), '/Engine/BasicShapes/Cube')
        self.assertEqual(mounted('g3/Plugins/ImpostorBaker/Content/M.uasset'), '/ImpostorBaker/M')
        self.assertEqual(category('/Game/Characters/Human/Sam/H.H'), 'Characters / Human / Sam')
        self.assertEqual(category('/Game/Environment/Temperate/Deco/R.R'), 'Environment / Temperate')

    def test_pak_round_trip_imports_mesh_and_texture(self):
        with tempfile.TemporaryDirectory() as tmp:
            install, output = Path(tmp)/'client', Path(tmp)/'data'/'sb'
            fixture(install)
            report = import_catalog(install, output)
            self.assertEqual((report['ready'], report['skipped'], report['unresolved_materials']), (1, 0, 0), report['skipped_reasons'])
            catalog = json.loads((output/'catalog.json').read_text())
            self.assertEqual(catalog[0]['source'], '/Game/Env/SM_Rock.SM_Rock')
            self.assertTrue(catalog[0]['texture_name'].startswith('t_rock_') and catalog[0]['texture_name'].endswith('.dds'))
            self.assertTrue((output.parent/'sb'/'textures'/catalog[0]['texture_name']).is_file())
            row = report['results'][0]
            self.assertEqual((row['vertices'], row['triangles']), (4, 4))
            index = json.loads((output/'model-index.json').read_text())
            model = json.loads((output/index['/game/env/sm_rock.sm_rock']['model_json']).read_text())
            self.assertEqual(model['metadata']['materials'][0]['parameter'], 'instance')
            # Unreal (x, y, z) -> viewer (-y, z, x) / 52.5: the +Y vertex lands on viewer -X.
            raw = (output/'model_json'/model['geometry_buffers']['file']).read_bytes()
            vertices = struct.unpack('<12f', raw[:48])
            self.assertAlmostEqual(vertices[6], -100/52.5, 5)

    def test_output_must_be_named_for_the_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(Path(tmp)/'client')
            with self.assertRaises(ValueError):
                import_catalog(Path(tmp)/'client', Path(tmp)/'data'/'other')


if __name__ == '__main__':
    unittest.main()

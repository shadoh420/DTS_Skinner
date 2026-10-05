"""Reflex model import (tools/import_reflex_models.py) and its catalog in the model browser's routes."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

from app import app
from tests.test_reflex_maps import material, textureset
from tools.import_reflex_models import import_catalog
from tools.model_data import load_model_data


def mesh_bytes(materials, parts):
    """A .mesh as the game writes one, one level of detail: per material its vertices ((x, y, z), BGRA, normal, uv) and
    triangles; a shadow mesh on the first, no bones."""
    out = b'\x23\x00\x0a\xd0' + struct.pack('<I6f3I', len(materials), -8, -8, -8, 8, 8, 8, 0, 1, 9) + b''.join(m.encode() + b'\0' for m in materials)
    for number, (vertices, triangles) in enumerate(parts):
        out += struct.pack('<4I', len(vertices), 3 * len(triangles), 0x1d, 4)
        out += b''.join(struct.pack('<3f4B3f2f4f', *p, *c, *n, *uv, 1, 0, 0, 1) for p, c, n, uv in vertices)
        out += struct.pack(f'<{3 * len(triangles)}H', *(i for t in triangles for i in t))
        shadow = vertices if number == 0 else []
        out += struct.pack('<4I', len(shadow), 3 * len(triangles) if shadow else 0, 0, 4) + b''.join(struct.pack('<3f', *p) for p, *_ in shadow)
        out += struct.pack(f'<{3 * len(triangles)}H', *(i for t in triangles for i in t)) if shadow else b''
    return out


def effect_bytes(meshes):
    """An .effect of mesh records: (mesh, {slot: colour}, scale)."""
    records = b''
    for mesh, colours, scale in meshes:
        record = bytearray(2141)
        record[0] = 1
        record[1:1 + len(mesh)] = mesh.encode()
        for slot, colour in colours.items():
            struct.pack_into('<4f', record, 897 + 16 * slot, *colour, 1)
        struct.pack_into('<f', record, 2117, scale)
        records += bytes(record)
    return b'\x33\x00\x0c\xd0' + struct.pack('<10I', 0x945893a8, 0, len(meshes), 0, 44, 44, 44 + len(records), 0, 0, 0) + records


def game(root):
    # A crate: a quad in p_metal (a colour, named bare as meshes do) shaded by varying vertex colours, facing +z, and
    # a triangle in a textured material; its effect paints the quad red and doubles it. A tinted copy of it, a pickup
    # (a hologram, and a part in MaterialA, a material the game has no file for, painted blue), a light (no mesh) and a
    # weapon's muzzle flash (internal, not listed).
    quad = ([((0, 0, 0), (0, 0, 255, 255), (0, 0, 1), (0, 0)), ((8, 0, 0), (255, 255, 255, 255), (0, 0, 1), (1, 0)),
             ((8, 8, 0), (255, 255, 255, 255), (0, 0, 1), (1, 1)), ((0, 8, 0), (255, 255, 255, 255), (0, 0, 1), (0, 1))], [(0, 1, 2), (0, 2, 3)])
    triangle = ([((0, 0, 4), (255,) * 4, (0, 0, 1), (0, 0)), ((4, 0, 4), (255,) * 4, (0, 0, 1), (1, 0)), ((0, 4, 4), (255,) * 4, (0, 0, 1), (0, 1))], [(0, 1, 2)])
    (root / 'base').mkdir(parents=True)
    with zipfile.ZipFile(root / 'base/common.pak', 'w') as pak:
        pak.writestr('common/materials/p_metal.material', material('internal/shaders/deferredPbrStylized', (3, 'albedo', struct.pack('<4f', .5, .5, .5, 1))))
        pak.writestr('common/materials/dev_tex.material', material('internal/shaders/deferredPbr_TEXTUREALBEDOSPEC', (4, 'textureAlbedoSpec', b'dev_tex_albedospec')))
        pak.writestr('common/materials/dev_tex.textureset', textureset('common/materials/dev_tex', {'common/materials/dev_tex_albedoSpec': (72, Image.new('RGBA', (8, 8), (200, 200, 200, 255)))}))
        pak.writestr('common/meshes/test/crate.mesh', mesh_bytes(['p_metal', 'common/materials/dev_tex'], [quad, triangle]))
        pak.writestr('common/meshes/test/crate.effect', effect_bytes([('common/meshes/test/crate', {0: (1, 0, 0)}, 2)]))
        pak.writestr('common/meshes/test/crate_tinted.effect', effect_bytes([('common/meshes/test/crate', {1: (1, .5, .5)}, 1)]))
        pak.writestr('common/lights/lamp.effect', effect_bytes([]))
        pak.writestr('internal/items/holo.material', material('internal/shaders/hologram', (3, 'albedo', struct.pack('<4f', 0, 1, 0, 1))))
        pak.writestr('internal/items/health/health_mega.mesh', mesh_bytes(['internal/items/holo', 'MaterialA'], [quad, triangle]))
        pak.writestr('internal/items/health/health_mega.effect', effect_bytes([('internal/items/health/health_mega', {1: (0, 0, 1)}, 1)]))
        pak.writestr('internal/weapons/rail/rail_fire.effect', effect_bytes([('internal/items/health/health_mega', {}, 1)]))
    with zipfile.ZipFile(root / 'base/thumbs_mesh.pak', 'w') as pak:
        pak.writestr('thumbs_mesh/common/meshes/test/crate.textureset', textureset('thumbs_mesh/common/meshes/test/crate', {
            'thumbs_mesh/common/meshes/test/crate': (72, Image.new('RGBA', (16, 16), (0, 0, 255, 255)))}))


class ReflexModelsTest(unittest.TestCase):
    def test_props_and_pickups_become_models_with_their_colours(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game(root / 'reflexfps')
            output, library = root / 'local/reflex-models', root / 'local/reflex-maps'
            result = import_catalog(root / 'reflexfps', output, library)
            catalog = {item['model_name']: item for item in json.loads((output / 'catalog.json').read_text())}
            self.assertEqual(sorted(catalog), ['common.meshes.test.crate', 'common.meshes.test.crate_tinted', 'internal.items.health.health_mega'])
            self.assertEqual((result['entries'], result['ready'], result['thumbnails']), (3, 3, 1))
            self.assertEqual(catalog['common.meshes.test.crate']['category'], 'Common / meshes')
            self.assertEqual(catalog['internal.items.health.health_mega']['category'], 'Items')
            self.assertEqual(catalog['common.meshes.test.crate']['display_name'], 'common/meshes/test/crate')

            crate = load_model_data(output / 'model_json/common.meshes.test.crate.json')
            self.assertEqual(crate['material_textures'], ['colour_ff0000.png', 'dev_tex_albedospec.png'])  # The effect's red; the texture as it is.
            self.assertEqual(crate['material_names'], ['common/materials/p_metal #ff0000', 'common/materials/dev_tex'])
            v = np.reshape(crate['vertices'], (-1, 3))
            self.assertEqual((v[:, 0].max(), v[:, 2].min()), (16, -8))  # Doubled; z turned round (the triangle at z 4).
            # Each triangle still faces the way its normals say (z turned round with them).
            t, n = np.reshape(crate['indices'], (-1, 3)), np.reshape(crate['normals'], (-1, 3))
            self.assertTrue((np.einsum('ij,ij->i', np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]]), n[t[:, 0]]) > 0).all())
            self.assertEqual(crate['colors'][:3], [1, 0, 0])  # BGRA (0, 0, 255): red; the rest white.
            self.assertEqual(crate['uvs'][2:4], [1, 0])
            textures = library / 'textures'
            self.assertEqual(Image.open(textures / 'colour_ff0000.png').getpixel((0, 0)), (255, 0, 0))
            red, green, _ = Image.open(textures / 'dev_tex_albedospec__ff8080.png').getpixel((0, 0))[:3]
            self.assertTrue(abs(red - 200) < 10 and abs(green - 100) < 6, (red, green))
            self.assertEqual(load_model_data(output / 'model_json/common.meshes.test.crate_tinted.json')['material_textures'],
                             ['colour_808080.png', 'dev_tex_albedospec__ff8080.png'])
            mega = load_model_data(output / 'model_json/internal.items.health.health_mega.json')
            self.assertEqual(mega['material_textures'], ['colour_00ff00.png', 'colour_0000ff.png'])
            self.assertEqual(mega['material_settings'], [{'blend': ['gl_one', 'gl_one'], 'depthWrite': False, 'cull': 'none'}, {}])
            self.assertNotIn('colors', mega)  # Holograms take no vertex colours (the quad's red corner).
            self.assertEqual(result['missing'], ['MaterialA'])

            # Edited library textures survive a new import.
            (textures / 'colour_ff0000.png').write_bytes(b'edited')
            import_catalog(root / 'reflexfps', output, library)
            self.assertEqual((textures / 'colour_ff0000.png').read_bytes(), b'edited')
            Image.new('RGB', (4, 4), (255, 0, 0)).save(textures / 'colour_ff0000.png')

            with patch('app.local_data_dir', root / 'local'), patch('app.reflex_models_dir', output):
                client = app.test_client()
                self.assertEqual(len(client.get('/list_models?game=reflex').json), 3)
                self.assertEqual(client.get('/model_json/common.meshes.test.crate?game=reflex').json['game'], 'reflex')
                self.assertEqual(client.get('/model_thumbnail/reflex/common.meshes.test.crate.png').status_code, 200)
                self.assertEqual(client.get('/texture/colour_ff0000.png?game=reflex').status_code, 200)
                glb = client.get('/export_glb/common.meshes.test.crate?game=reflex')
                self.assertEqual((glb.status_code, glb.data[:4]), (200, b'glTF'))
                length = struct.unpack_from('<I', glb.data, 12)[0]
                gltf = json.loads(glb.data[20:20 + length])
                self.assertIn('COLOR_0', gltf['meshes'][0]['primitives'][0]['attributes'])
                obj = client.get('/export_obj/common.meshes.test.crate?game=reflex')
                self.assertEqual(obj.status_code, 200)
                self.assertIn('dev_tex_albedospec.png', zipfile.ZipFile(io.BytesIO(obj.data)).namelist())
                self.assertEqual(client.post('/import_reflex', json={'path': str(root / 'nowhere')},
                                             headers={'Origin': 'http://evil.example'}).status_code, 403)
                self.assertEqual(client.post('/import_reflex', json={'path': str(root / 'nowhere')}).status_code, 422)


if __name__ == '__main__':
    unittest.main()

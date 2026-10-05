"""Diabotical model import (tools/import_diabotical_models.py) and its catalog in the model browser's routes."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import app
from tests.test_diabotical_maps import dbp, dds, install
from tools.import_diabotical_models import import_catalog, marker
from tools.model_data import load_model_data

ASSETS = (b'asset props/sub/quad\n{\n  type prop\n  listed true\n}\n'
          b'asset strip\n{\n  dynamic true\n  dynamic_default_size 3 1 1\n  material props/red\n'
          b'  dynamic_rule\n  {\n    select props/sub/quad\n  }\n  dynamic_rule\n  {\n    if offset_right is 0\n    select props/sub/quad_flipx\n  }\n}\n'
          b'asset hidden_piece\n{\n  listed false\n  model props/sub/quad\n}\n'
          b'asset box\n{\n  listed true\n  model props/sub/quad\n  entity_property no_show true\n}\n'
          b'asset jump\n{\n  listed true\n  type entity\n  command multi /add jumppad; /resize !last 80 10 80\n}\n'
          b'asset arrow_decal\n{\n  type decal\n  material arrow\n}\n')


def game(root):
    install(root, {})
    # The fixture's model pack plus Editpad assets: a prop, a dynamic prop of three cells, a utility box (no_show in
    # maps), an entity with no model (a marker) and a decal; and a material tinted all over (its colour mask, map 4,
    # red: accent 1 replaces the colour).
    from tools.import_diabotical_map import Pack
    pack = Pack(root / 'packs/models_props.dbp')
    files = {name: pack.read(name) for name in pack.files} | {
        'models\\props\\sub\\editpad.assets': ASSETS,
        'models\\other\\tint.shader': (b'props/red\n{\n {\n\t\tmap models/props/quad_d.png\n\t\tmap n.png\n\t\tmap s.png\n\t\tmap id.png\n'
                                        b'\t\tmap models/other/mask_d.png\n\t\tpixel_shader tilemask.ps.cso\n\t\tpixel_shader_param accent1 FF0000\n }\n}\n'),
        'models\\other\\mask_d.png.dds': dds((255, 0, 0))}
    (root / 'packs/models_props.dbp').write_bytes(dbp(files))
    thumbs = root / 'ui/html/asset_thumbnails'
    thumbs.mkdir(parents=True)
    (thumbs / 'props_sub_quad.png.dds').write_bytes(dds((1, 2, 3)))


class DiaboticalModelsTest(unittest.TestCase):
    def test_editpad_assets_become_models_textures_and_thumbnails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game(root / 'game')
            output = root / 'out'
            result = import_catalog(root / 'game', output)
            catalog = {item['model_name']: item for item in json.loads((output / 'catalog.json').read_text())}
            self.assertEqual(sorted(catalog), ['dynprop.quad_strip', 'dynprop.quad_strip.props_sub_quad', 'dynprop.quad_strip.props_sub_quad_flipx', 'dynprop.strip', 'dynprop.strip.props_sub_quad', 'dynprop.strip.props_sub_quad_flipx',
                                               'entity.jump', 'prop.props_sub_quad', 'utility.box'])
            self.assertEqual((result['entries'], result['ready']), (9, 9))
            self.assertEqual(catalog['dynprop.strip.props_sub_quad_flipx']['display_name'], 'strip › props/sub/quad_flipx')
            self.assertEqual(catalog['prop.props_sub_quad']['category'], 'Props / other')
            self.assertTrue(catalog['prop.props_sub_quad']['thumbnail'])
            self.assertFalse(catalog['utility.box']['thumbnail'])

            prop = load_model_data(output / 'model_json/prop.props_sub_quad.json')
            self.assertEqual((len(prop['vertices']) // 3, len(prop['indices'])), (4, 6))  # Two triangles, corners shared.
            self.assertEqual(prop['material_names'], ['props/sub/quad_mat'])
            self.assertEqual(prop['material_textures'], ['props_sub_quad_mat.png'])
            self.assertEqual(prop['material_settings'], [{'alphaFunc': 'GE128', 'cull': 'none'}])
            self.assertEqual(sorted(prop['uvs'][1::2]), [0, 0, 1, 1])  # v down, as the browser's textures read.
            # The dynamic prop: three cells, the last mirrored, all in its material, tinted by its accent.
            strip = load_model_data(output / 'model_json/dynprop.strip.json')
            self.assertEqual(len(strip['indices']), 18)
            xs = strip['vertices'][0::3]
            self.assertAlmostEqual(max(xs) - min(xs), 70, places=3)  # Cells at 20, 60, 100; the quad 5..15 along x, the last mirrored.
            self.assertEqual(strip['material_textures'], ['props_red.png'])
            from PIL import Image
            with Image.open(output / 'textures/props_red.png') as tinted:
                red, green, _ = tinted.getpixel((0, 0))  # (10, 200, 10) toward red x its mean (73).
                self.assertEqual((abs(red - 73) < 4, green < 4), (True, True))
            jump = load_model_data(output / 'model_json/entity.jump.json')
            self.assertEqual(jump['material_textures'], ['marker_30d0f0.png'])
            self.assertTrue((output / 'textures/arrow.png').is_file())  # Decals join the texture library.

            # Edited library textures survive a new import.
            (output / 'textures/arrow.png').write_bytes(b'edited')
            import_catalog(root / 'game', output)
            self.assertEqual((output / 'textures/arrow.png').read_bytes(), b'edited')

            with patch('app.local_data_dir', root / 'local'), patch('app.diabotical_dir', output):
                client = app.test_client()
                listed = client.get('/list_models?game=diabotical').json
                self.assertEqual(len(listed), 9)
                model = client.get('/model_json/dynprop.strip?game=diabotical').json
                self.assertEqual(model['game'], 'diabotical')
                self.assertEqual(client.get('/model_json/nope?game=diabotical').status_code, 404)
                self.assertEqual(client.get('/diabotical_thumbnail/prop.props_sub_quad.png').status_code, 200)
                self.assertEqual(client.get('/diabotical_thumbnail/..%5Ccatalog.json').status_code, 404)
                self.assertEqual(client.get('/texture/props_red.png?game=diabotical').status_code, 200)
                glb = client.get('/export_glb/dynprop.strip?game=diabotical')
                self.assertEqual((glb.status_code, glb.data[:4]), (200, b'glTF'))
                obj = client.get('/export_obj/prop.props_sub_quad?game=diabotical')
                self.assertEqual(obj.status_code, 200)
                import zipfile
                self.assertIn('props_sub_quad_mat.png', zipfile.ZipFile(io.BytesIO(obj.data)).namelist())
                self.assertEqual(client.post('/import_diabotical', json={'path': str(root / 'nowhere')},
                                             headers={'Origin': 'http://evil.example'}).status_code, 403)

    def test_markers_face_out(self):
        import numpy as np
        for kind in ('spawn', 'jumppad', 'trigger'):
            triangles, _ = marker(kind, [40, 40, 40])
            middle = triangles[..., :3].reshape(-1, 3).mean(0)
            outward = np.einsum('ij,ij->i', triangles[:, 0, 3:6], triangles[..., :3].mean(1) - middle)
            self.assertTrue((outward >= 0).all(), kind)


if __name__ == '__main__':
    unittest.main()

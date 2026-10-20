"""Fab character import (tools/import_fab.py) from a made-up tools/ue5_export.py export."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from tests.test_diabotical_maps import quad_fbx
from tools.import_fab import import_catalog
from tools.model_data import load_model_data

colour = lambda name, default: dict(cls='MaterialExpressionVectorParameter', parameter_name=name, default_value=default, inputs=[])
mask = lambda pin: dict(cls='MaterialExpressionTextureSample', texture='/Game/Kit/T_Mask.T_Mask', pin=pin, inputs=[None, None, None])
lerp = lambda a, b, alpha: dict(cls='MaterialExpressionLinearInterpolate', const_a=0.0, const_b=1.0, const_alpha=0.5, inputs=[a, b, alpha])
wear = dict(cls='MaterialExpressionMaterialFunctionCall', material_function='/Engine/Functions/CheapContrast.CheapContrast', pin='Result', inputs=[mask('R')])


def export(folder):
    """One kit: a quad in slot material 'skin' (Type_A), a sibling Type_B, a mask whose left pixel is green."""
    kit = Path(folder) / 'Kit'
    (kit / 'Meshes').mkdir(parents=True)
    (kit / 'Meshes' / 'SK_Quad.fbx').write_bytes(quad_fbx())
    Image.fromarray(np.array([[[0, 255, 0], [0, 0, 0]]], np.uint8)).save(kit / 'T_Mask.png')
    graph = dict(MP_BASE_COLOR=lerp(lerp(colour('First', [0, 0, 0, 1]), colour('Second', [0, 0, 0, 1]), mask('G')), colour('Wear', [1, 1, 1, 1]), wear))
    instance = lambda folder, first: dict(name='skin', path=f'/Game/Kit/{folder}/skin.skin', cls='MaterialInstanceConstant',
                                         parent='/Game/Kit/M_Base.M_Base', textures={}, scalars={}, colours={'First': first, 'Second': [0, 0, 1, 1]})
    (kit / 'materials.json').write_text(json.dumps(dict(
        meshes=[dict(name='SK_Quad', fbx='Meshes/SK_Quad.fbx', slots=[dict(slot='Base', material='/Game/Kit/Type_A/skin.skin')])],
        animations=[dict(name='Idle', fbx='Animations/Idle.fbx', skeleton='')],
        textures=[dict(name='T_Mask', path='/Game/Kit/T_Mask.T_Mask', file='T_Mask.png', srgb=False)],
        materials=[dict(name='M_Base', path='/Game/Kit/M_Base.M_Base', cls='Material', graph=graph),
                   instance('Type_A', [0.5, 0, 0, 1]), dict(instance('Type_B', [0, 1, 0, 1]), name='skin_b', path='/Game/Kit/Type_B/skin_b.skin_b')])))


class FabImportTest(unittest.TestCase):
    def test_import_bakes_each_skin_and_turns_z_up_to_y_up(self):
        with tempfile.TemporaryDirectory() as folder:
            export(folder)
            out = Path(folder) / 'fab'
            report = import_catalog(Path(folder), out)
            catalog = json.loads((out / 'catalog.json').read_text())
            self.assertEqual([c['display_name'] for c in catalog], ['SK_Quad / Type_A', 'SK_Quad / Type_B'])
            self.assertEqual(report['unsupported'], ['M_Base MP_BASE_COLOR: MaterialFunctionCall CheapContrast'])
            # Left texel: the mask picks Second (blue); right: First, 0.5 linear red shown as sRGB 188.
            a = np.asarray(Image.open(out / 'textures' / catalog[0]['texture_name']))
            self.assertEqual(a[0].tolist(), [[0, 0, 255], [188, 0, 0]])
            b = np.asarray(Image.open(out / 'textures' / catalog[1]['texture_name']))
            self.assertEqual(b[0, 1].tolist(), [0, 255, 0])
            model = load_model_data(out / 'model_json' / (catalog[0]['model_name'] + '.json'))
            points = np.array(model['vertices']).reshape(-1, 3)
            self.assertEqual(points.min(0).tolist(), [5, 0, -10])  # FBX y 0..10 is page z 0..-10; the model moved 5 on x.
            self.assertEqual(points.max(0).tolist(), [15, 0, 0])
            uvs = np.array(model['uvs']).reshape(-1, 2)
            corner = np.flatnonzero((points == [5, 0, 0]).all(1))[0]
            self.assertEqual(uvs[corner].tolist(), [0, 1])  # FBX v 0 at that corner, flipped back to Unreal's.
            self.assertEqual(model['metadata']['animations'], ['Idle'])


if __name__ == '__main__':
    unittest.main()

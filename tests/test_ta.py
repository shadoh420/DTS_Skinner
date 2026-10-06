"""TA conversion pack import (tools/import_ta.py) and its catalog in the model browser's routes."""
import io
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from app import app
from tools.import_ta import import_catalog
from tools.model_data import load_model_data

DTS = Path(__file__).resolve().parents[1] / 'tools/dts_files'


class TaImportTest(unittest.TestCase):
    def test_pack_folders_become_models_with_their_skins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, stock, output = root / 'pack', root / 'stock', root / 'local/ta'
            for folder, colour in (('Gun A', 'red'), ('Gun B - Two', 'blue')):
                (pack / folder).mkdir(parents=True)
                shutil.copyfile(DTS / 'chaingun.DTS', pack / folder / 'chaingun.DTS')
                Image.new('RGB', (4, 4), colour).save(pack / folder / 'chaingun.png')
            Image.new('RGB', (4, 4), 'green').save(pack / 'Gun B - Two/chaingun_alt.png')
            (pack / 'Disc').mkdir()
            shutil.copyfile(DTS / 'disc.DTS', pack / 'Disc/disc.DTS')
            (pack / 'Readme only').mkdir()
            stock.mkdir()
            Image.new('RGB', (4, 4), 'white').save(stock / 'pulse.png')

            result = import_catalog(pack, output, stock)
            self.assertEqual(result, dict(entries=3, ready=3, missing=['disc.png']))
            catalog = json.loads((output / 'catalog.json').read_text())
            self.assertEqual([(e['model_name'], e['display_name']) for e in catalog],
                             [('disc', 'Disc'), ('gun_a', 'Gun A'), ('gun_b_-_two', 'Gun B - Two')])
            # Same-name skins with different pictures are kept apart; slots without one take the stock T1 texture.
            gun = load_model_data(output / 'model_json/gun_a.json')
            self.assertEqual(gun['material_textures'][:3], ['gun_a.chaingun.png', 'pulse.png', 'gun_a.chaingun.png'])
            self.assertEqual(gun['material_texture_games'][:3], ['ta', 't1', 'ta'])
            self.assertEqual(sorted(p.name for p in (output / 'textures').iterdir()),
                             ['chaingun_alt.png', 'gun_a.chaingun.png', 'gun_b_-_two.chaingun.png'])

            (output / 'textures/chaingun_alt.png').write_bytes(b'edited')
            import_catalog(pack, output, stock)
            self.assertEqual((output / 'textures/chaingun_alt.png').read_bytes(), b'edited')

            with patch('app.ta_dir', output):
                client = app.test_client()
                self.assertEqual(len(client.get('/list_models?game=ta').json), 3)
                self.assertEqual(client.get('/model_json/gun_b_-_two?game=ta').json['game'], 'ta')
                self.assertEqual(client.get('/texture/gun_a.chaingun.png?game=ta').status_code, 200)
                glb = client.get('/export_glb/gun_a?game=ta')
                self.assertEqual((glb.status_code, glb.data[:4]), (200, b'glTF'))
                gltf = json.loads(glb.data[20:20 + struct.unpack_from('<I', glb.data, 12)[0]])
                self.assertTrue(gltf.get('animations'))  # The chaingun's spin, baked from the copied DTS.
                obj = client.get('/export_obj/gun_b_-_two?game=ta')
                self.assertEqual(obj.status_code, 200)
                self.assertIn('textures/t1/pulse.png', zipfile.ZipFile(io.BytesIO(obj.data)).namelist())
                self.assertEqual(client.post('/import_ta', json={'path': str(root / 'nowhere')},
                                             headers={'Origin': 'http://evil.example'}).status_code, 403)
                self.assertEqual(client.post('/import_ta', json={'path': str(root / 'nowhere')}).status_code, 422)


class AtRestTest(unittest.TestCase):
    def test_objects_show_as_the_game_shows_a_shape_at_rest(self):
        from types import SimpleNamespace as N
        from tools.export_model import initially_visible
        # Sequence 1 "visibility" hides the hulk at position 0 (intact) and shows the muzzle flash.
        shape = N(names=[b'fire\0', b'visibility\0'], sequences=[N(name_index=0), N(name_index=1)],
                  sub_sequences=[N(sequence_idx=1, num_key_frames=2, first_key_frame=0),
                                 N(sequence_idx=1, num_key_frames=1, first_key_frame=2),
                                 N(sequence_idx=0, num_key_frames=1, first_key_frame=3)],
                  keyframes=[N(mat_index=0x4000), N(mat_index=0xc000), N(mat_index=0xc000), N(mat_index=0x4000)])
        obj = lambda flags, first, count: N(flags=flags, first_sub_seq=first, num_sub_seq=count)
        self.assertTrue(initially_visible(shape, obj(0, 0, 0)))
        self.assertFalse(initially_visible(shape, obj(1, 0, 0)))  # DefaultInvisible
        self.assertFalse(initially_visible(shape, obj(0, 0, 1)))  # Hulk
        self.assertTrue(initially_visible(shape, obj(1, 1, 1)))
        self.assertFalse(initially_visible(shape, obj(1, 2, 1)))  # Only "visibility" counts at rest.


if __name__ == '__main__':
    unittest.main()

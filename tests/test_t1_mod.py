"""Tribes 1 mod import (tools/import_t1_mod.py): the RPG, Star Wars and RedMoon mods as their own games."""
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
from tools.import_t1_map import SHARED_PALETTES
from tools.import_t1_mod import import_catalog
from tools.model_data import load_model_data

DTS = Path(__file__).resolve().parents[1] / 'tools/dts_files'


def bmp(palette_id):
    """A 4 x 4 8-bit Windows bitmap of palette index 1 naming game palette `palette_id` in bfReserved2."""
    image = Image.new('P', (4, 4), 1)
    image.putpalette([128] * 768)  # Its own palette is grey, as in the mods.
    data = io.BytesIO()
    image.save(data, 'BMP')
    data = data.getvalue()
    return data[:8] + struct.pack('<H', palette_id) + data[10:]


class T1ModImportTest(unittest.TestCase):
    def test_loose_and_volume_models_take_the_mods_skins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack, stock, output = root / 'RPG', root / 'stock', root / 'local/trpg'
            for folder in (pack / 'Skins', stock):
                folder.mkdir(parents=True)
            shutil.copyfile(DTS / 'disc.DTS', pack / 'Disc.DTS')
            shutil.copyfile(DTS / 'disc.DTS', pack / 'Skins/disc.dts')  # Same file deeper: one model.
            shutil.copyfile(DTS / 'chaingun.DTS', pack / 'Skins/DISC.dts')  # Same name, other shape: kept, renamed.
            Image.new('RGB', (4, 4), 'red').save(pack / 'disc.png')
            (pack / 'disc.bmp').write_bytes(bmp(1136))  # The PNG of the same name wins.
            Image.new('RGB', (4, 4), 'white').save(pack / 'intro.png')  # No model's: not stored.
            with zipfile.ZipFile(pack / 'shapes.zip', 'w') as volume:
                volume.write(DTS / 'chaingun.DTS', 'chaingun.dts')  # Only in a volume: still a model.
                volume.write(DTS / 'disc.DTS', 'disc.dts')  # Loose wins.
                volume.writestr('chaingun.bmp', bmp(1975))
            with zipfile.ZipFile(pack / 'interior.zip', 'w') as volume:
                volume.writestr('hall.dis', b'')
                volume.writestr('chaingun.png', b'not read')  # An interior's texture is no skin.
            Image.new('RGB', (4, 4), 'white').save(stock / 'pulse.png')

            result = import_catalog(pack, output, stock, 'trpg', 'T1 RPG mod')
            self.assertEqual((result['entries'], result['ready'], result['unreadable']), (3, 3, []))
            catalog = json.loads((output / 'catalog.json').read_text())
            self.assertEqual([(e['model_name'], e['game'], e['category']) for e in catalog],
                             [('chaingun', 'trpg', 'T1 RPG mod'), ('disc', 'trpg', 'T1 RPG mod'), ('skins_disc', 'trpg', 'T1 RPG mod')])
            disc = load_model_data(output / 'model_json/disc.json')
            self.assertEqual((disc['material_textures'][0], disc['material_texture_games'][0]), ('disc.png', 'trpg'))
            gun = load_model_data(output / 'model_json/chaingun.json')
            self.assertEqual(gun['material_texture_games'][:2], ['trpg', 't1'])
            self.assertEqual(sorted(p.name for p in (output / 'textures').iterdir()), ['chaingun.png', 'disc.png'])
            self.assertEqual(Image.open(output / 'textures/disc.png').getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(Image.open(output / 'textures/chaingun.png').getpixel((0, 0)), tuple(SHARED_PALETTES[1975][0][3:6]))

            with patch.dict('app.pack_dirs', trpg=output):
                client = app.test_client()
                self.assertEqual(len(client.get('/list_models?game=trpg').json), 3)
                self.assertEqual(client.get('/texture/disc.png?game=trpg').status_code, 200)
                glb = client.get('/export_glb/skins_disc?game=trpg')
                self.assertEqual((glb.status_code, glb.data[:4]), (200, b'glTF'))
                self.assertEqual(client.post('/import_rm', json={'path': str(root / 'nowhere')}).status_code, 422)


if __name__ == '__main__':
    unittest.main()

"""Starsiege import (tools/import_starsiege.py): its ' VOL' volumes, bitmap arrays, palettes and vehicle scripts."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app import app
from tests.test_t1_maps import chunk, pvol
from tools.import_starsiege import import_catalog
from tools.import_t1_map import open_volume

DTS = Path(__file__).resolve().parents[1] / 'tools/dts_files'


def starsiege_vol(members):
    """A Starsiege volume: a PVOL with the ' VOL' tag and an empty name list and index before the real ones."""
    data = pvol({name: (body, len(body), 0) for name, body in members.items()})
    names_at = struct.unpack_from('<I', data, 4)[0]
    return b' VOL' + data[4:names_at] + b'vols\0\0\0\0voli\0\0\0\0' + data[names_at:]


class StarsiegeImportTest(unittest.TestCase):
    def test_install_becomes_a_game(self):
        colours = bytes([10, 20, 30, 0]) * 256
        palette = b'PL98' + struct.pack('<iiii', 1, 0, 0, 0) + bytes(32) + colours + struct.pack('<II', 2, 0)
        frame = chunk(b'PBMP', chunk(b'head', struct.pack('<5i', 0, 2, 2, 8, 0)) + chunk(b'data', bytes(8)) + chunk(b'PiDX', struct.pack('<I', 2)))
        # As in Starsiege's darkscroll.pba: a head chunk, then the frames; the first is the texture.
        array = chunk(b'PBMA', chunk(b'head', struct.pack('<2I', 2, 0)) + frame + chunk(b'PBMP', bytes(4)))
        with tempfile.TemporaryDirectory() as temp:
            install, output = Path(temp, 'Starsiege'), Path(temp, 'local/ss')
            install.mkdir()
            volume = install / 'gameObjects.vol'
            volume.write_bytes(starsiege_vol({'disc.dts': (DTS / 'disc.DTS').read_bytes(), 'disc.pba': array}))
            (install / 'Temperate.Sim.vol').write_bytes(starsiege_vol({'temperate.d.ppl': palette}))
            (install / 'scripts.vol').write_bytes(starsiege_vol({'datherc_kn_disc.cs': b'hercBase(IDVEH_X, "DISC", "disc.dts", 13.0);'}))
            self.assertEqual(sorted(open_volume(volume)), ['disc.dts', 'disc.pba'])

            result = import_catalog(install, output)
            self.assertEqual((result['entries'], result['ready'], result['unreadable']), (1, 1, []))
            entry = json.loads((output / 'catalog.json').read_text())[0]
            self.assertEqual((entry['model_name'], entry['category'], entry['game']), ('disc', 'HERCs', 'ss'))
            model = json.loads((output / 'model_json/disc.json').read_text())
            self.assertEqual(model['material_textures'][0], 'disc.png')
            with Image.open(output / 'textures/disc.png') as image:
                self.assertEqual(image.getpixel((0, 0)), (10, 20, 30))
            # Flat-colour materials are swatches; any other untextured slot keeps its placeholder.
            for slot in model['material_textures'][1:]:
                self.assertRegex(slot, r'^(rgb_[0-9a-f]{6}\.png|\[Slot \d+: No Texture Specified\])$')
                if slot.startswith('rgb_'):
                    self.assertTrue((output / 'textures' / slot).is_file())

            with patch.dict('app.pack_dirs', ss=output):
                client = app.test_client()
                self.assertEqual(len(client.get('/list_models?game=ss').json), 1)
                self.assertEqual(client.get('/texture/disc.png?game=ss').status_code, 200)
                self.assertEqual(client.get('/export_glb/disc?game=ss').status_code, 200)

    def test_not_an_install(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, 'No Starsiege volumes'):
            import_catalog(temp, Path(temp, 'out'))


if __name__ == '__main__':
    unittest.main()

"""The T1 Maps Sky list (app.py /t1-map-skies): one group per game, each pack's sky kind turned into an entry the
T1 viewer's renderers draw (static/t1-maps/viewer.js showSky, skies.js), from synthetic packs."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import app

FACES = ('rt', 'bk', 'lf', 'ft', 'up', 'dn')


def pack(root, folder, rows):
    """A Maps pack: index.json and maps/<id>/scene.json for {id: (row extras, scene)}."""
    (root / folder).mkdir(parents=True)
    (root / folder / 'index.json').write_text(json.dumps([{'id': key, 'name': key, **extra} for key, (extra, _) in rows.items()]))
    for key, (_, scene) in rows.items():
        (root / folder / 'maps' / key).mkdir(parents=True)
        (root / folder / 'maps' / key / 'scene.json').write_text(json.dumps(scene))


def skybox(name):
    return {'name': name, 'faces': {side: {'texture': f'{name}_{side}.png'} for side in FACES}}


class SkyListTest(unittest.TestCase):
    def skies(self, build):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build(root)
            with patch('app.local_data_dir', root):
                return {entry['game']: entry for entry in app.test_client().get('/t1-map-skies').json} | \
                    {'_all': app.test_client().get('/t1-map-skies').json}

    def test_box_skies_sections_placeholders_and_display_power(self):
        def build(root):
            pack(root, 'sin-maps/2015', {'2015dm3': ({'group': 'SiN Gold'}, {'skybox': skybox('dam_')})})
            pack(root, 'vtmb-maps', {'sm_hub_1': ({'group': 'Santa Monica'}, {'skybox': skybox('pier'), 'lighting': {'display_power': .667}}),
                                     'ch_hub_1': ({'group': 'Chinatown'}, {'skybox': skybox('pier')})})
            plain = {'name': 'plain', 'faces': {side: {'texture': 'eye_sky_plain.png'} for side in FACES}}
            pack(root, 'eye-maps', {'cc_minos': ({}, {'skybox': plain})})
        skies = self.skies(build)
        self.assertEqual((skies['sin']['base'], skies['sin']['section'], skies['sin']['renderer']), ('/sin-map-data/2015/', 'SiN Gold', 'q3'))
        self.assertEqual(skies['sin']['sky']['sky']['box'], [f'dam__{side}.png' for side in FACES])
        self.assertEqual((skies['vtmb']['power'], skies['vtmb']['section'], skies['vtmb']['maps']), (.667, None, ['sm_hub_1', 'ch_hub_1']))
        self.assertNotIn('eye', skies)  # E.Y.E.'s flat stand-in for its HDR skies is not offered.
        self.assertEqual([entry['group'] for entry in skies['_all']], ['SiN', 'Vampire: The Masquerade – Bloodlines'])

    def test_quake_nosgoth_unreal_and_vengeance_entries(self):
        def build(root):
            pack(root, 'quake-maps/hipnotic', {'hip1m2': ({'group': 'Scourge of Armagon'}, {'groups': [
                {'kind': 'sky', 'name': 'sky_tele', 'texture': 'a.png'}, {'kind': 'sky', 'name': 'sky_tele', 'texture': 'a.png'}, {'name': 'wall'}]})})
            dome = {'source': 'Sky.Dome', 'sky': True, 'sky_order': -10001, 'store': 'models',
                    'geometry': {'file': 'f' * 64 + '.bin', 'arrays': {'vertices': {'offset': 0, 'count': 3, 'dtype': '<f4'}}},
                    'groups': [{'start': 0, 'count': 3, 'texture': 'sky.png', 'kind': 'sky', 'settings': {}, 'extra': 1}]}
            pack(root, 'nos-maps', {'docks': ({}, {'model_library': {'d': dome, 'w': {'source': 'Wall'}},
                                               'instances': [{'look': 'w', 'matrix': [1] * 16},
                                                             {'look': 'd', 'matrix': [2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 10, 20, 30, 1]}]})})
            zone = {'origin': [1, 2, 3], 'rotation': [1, 0, 0, 0, 1, 0, 0, 0, 1], 'rotator': [0, 0, 0], 'rate': [0, 256, 0]}
            pack(root, 'unreal-maps/ut', {'ctf-face': ({'title': 'Facing Worlds'}, {'sky': zone})})
            pack(root, 'dx-maps', {'01_nyc': ({}, {'sky': zone, 'display': {'power': .5}}), 'indoor': ({}, {'sky': None})})
            pack(root, 'tvg-maps', {'sp-x': ({}, {'sky': zone, 'terrain': [], 'lighting': {'display_power': 1}})})
        skies = self.skies(build)
        quake = skies['quake']
        self.assertEqual((quake['renderer'], quake['base'], quake['sky'], quake['section']),
                         ('quake1', '/quake-map-data/hipnotic/', {'name': 'sky_tele', 'texture': 'a.png'}, 'Scourge of Armagon'))
        model = skies['nos']['sky']['models'][0]
        self.assertEqual((skies['nos']['renderer'], model['matrix'][12:15], model['groups'][0].get('extra')), ('meshes', [0, 0, 0], None))
        ut = skies['ut']
        self.assertEqual((ut['group'], ut['renderer'], ut['base'], ut['power'], ut['sky']['map'], ut['sky']['rate']),
                         ('Unreal Tournament', 'unreal', '/unreal-map-data/ut/', .67, 'ctf-face', [0, 256, 0]))
        self.assertEqual((skies['dx']['power'], skies['dx']['maps']), (.5, ['01_nyc']))  # Its own display power; no sky, no entry.
        self.assertEqual((skies['tvg']['renderer'], skies['tvg']['power'], skies['tvg']['visible']), ('vengeance', 1, None))

    def test_tribes_2_missions_from_the_manifest(self):
        def build(root):
            base = root / 't2-maps' / 'base'
            for name, text in {'missions/whiteout.mis': 'new Sky(Sky) {\n\tcloudSpeed[1] = "0.000032 0";\n\tmaterialList = "sky_ice.dml";\n};',
                               'missions/tomb.mis': 'new Sky(Sky) {\n\tuseSkyTextures = "0";\n\tSkySolidColor = "0.5 1 0 0";\n};',
                               'textures/sky_ice.dml': 'ice/f\nice/r\nice/b\nice/l\nice/t.png\nice/d\nice/emap\nice/c1\nice/c2\n'}.items():
                (base / name).parent.mkdir(parents=True, exist_ok=True)
                (base / name).write_text(text)
            files = ['missions/whiteout.mis', 'missions/tomb.mis', 'textures/sky_ice.dml', 'textures/ice/c2.png',
                     *(f'textures/ice/{face}.png' for face in 'frbltd')]
            (root / 't2-maps' / 'manifest.json').write_text(json.dumps({
                'resources': {name: [name, ['']] for name in files},
                'missions': {'Whiteout': {'resourcePath': 'missions/whiteout.mis'}, 'Tomb': {'resourcePath': 'missions/tomb.mis'},
                             'Lost': {'resourcePath': 'missions/lost.mis'}}}))
        entries = [entry for entry in self.skies(build)['_all'] if entry['game'] == 't2']
        box, solid = sorted(entries, key=lambda entry: entry['renderer'] == 'dome')
        self.assertEqual((box['renderer'], box['base'], box['maps']), ('q3', '/t2-map-data/base/', ['Whiteout']))
        self.assertEqual(box['sky']['sky']['box'], ['ice/r.png', 'ice/f.png', 'ice/l.png', 'ice/b.png', 'ice/t.png', 'ice/d.png'])
        self.assertEqual(box['sky']['sky']['upTurn'], 1)
        self.assertEqual(box['sky']['stages'], [{'map': 'ice/c2.png', 'blend': ['gl_src_alpha', 'gl_one_minus_src_alpha'],
                                                  'tcMods': [['scroll', 0.001, 0.0]]}])  # Layer 1 only: c1 has no file.
        self.assertEqual((solid['renderer'], solid['sky']), ('dome', {'name': 'solid colour', 'color': [128, 255, 0]}))

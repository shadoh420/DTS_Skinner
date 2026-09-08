"""Native sample addressing plus optional installed-source movement check."""
import json
import unittest

import numpy as np

from tools.animate_t2 import DEFAULT_GAME_DATA, DEFAULT_KIT, ROOT, frame_worlds, load_animated_model


class T2AnimationTests(unittest.TestCase):
    def test_bound_translation_and_blend_use_native_node_order(self):
        shape = {'nodes': [(0, -1), (1, 0)],
                 'defaultRotations': [(0, 0, -23170, 23170), (0, 0, 0, 32767)],
                 'defaultTranslations': [(10, 0, 0), (2, 0, 0)]}
        sequence = {'numKeyframes': 2, 'flags': 8, 'translationMatters': [0], 'baseTranslation': 0}
        data = {'translations': [(0, 0, 0), (3, 0, 0)]}
        first = frame_worlds(shape, data, sequence, 0, mapping=[1])
        last = frame_worlds(shape, data, sequence, 1, mapping=[1])
        np.testing.assert_allclose(first[1][:3, 3], [10, 2, 0], atol=1e-6)
        np.testing.assert_allclose(last[1][:3, 3], [10, 5, 0], atol=1e-6)

    def test_scale_keys_are_applied(self):
        shape = {'nodes': [(0, -1)], 'defaultRotations': [(0, 0, 0, 32767)], 'defaultTranslations': [(0, 0, 0)]}
        sequence = {'numKeyframes': 2, 'flags': 2, 'scaleMatters': [0], 'baseScale': 0}
        matrix = frame_worlds(shape, {'alignedScales': [(1, 1, 1), (2, 3, 4)]}, sequence, 1)[0]
        np.testing.assert_allclose(matrix[:3, :3], np.diag([2, 3, 4]))

    def test_missing_source_is_explicit_and_dif_is_static(self):
        with self.assertRaisesRegex(ValueError, 'SKINNER_T2_GAME_DATA'):
            load_animated_model('missing', 'Z:/missing-t2-data', {'vertices': []}, use_cache=False)
        model = load_animated_model('interior_test', 'Z:/missing', {'vertices': [0, 0, 0]})
        self.assertEqual(model['animation_clips'], [])

    @unittest.skipUnless(DEFAULT_GAME_DATA.is_dir() and DEFAULT_KIT.is_dir(), 'Original T2 installation/kit unavailable')
    def test_native_hidden_disc_retains_slot_order_and_moves(self):
        preview = json.loads((ROOT / 'static/t2/model_json/weapon_disc.json').read_text())
        model = load_animated_model('weapon_disc', DEFAULT_GAME_DATA, preview, use_cache=False)
        self.assertGreater(len(model['vertices']), len(preview['vertices']))
        self.assertEqual(model['material_textures'], preview['material_textures'])
        self.assertEqual(len(model['indices']) * 3, len(model['vertices']))
        self.assertTrue(any(not np.allclose(clip['frames'][0]['vertices'], frame['vertices'])
                            for clip in model['animation_clips'] for frame in clip['frames'][1:]))

    @unittest.skipUnless(DEFAULT_GAME_DATA.is_dir() and DEFAULT_KIT.is_dir(), 'Original T2 installation/kit unavailable')
    def test_unknown_legacy_animation_still_exports_explicit_static_geometry(self):
        model = load_animated_model('borg3', DEFAULT_GAME_DATA, use_cache=False)
        self.assertEqual(model['animation_status'], 'static-fallback')
        self.assertEqual(model['animation_clips'], [])
        self.assertTrue(model['vertices'])
        self.assertTrue(any('v15/v16' in note for note in model['animation_notes']))

    @unittest.skipUnless(DEFAULT_GAME_DATA.is_dir() and DEFAULT_KIT.is_dir(), 'Original T2 installation/kit unavailable')
    def test_native_external_sequence_has_real_motion(self):
        # One native DSQ exercises name binding and moving armor geometry
        # without sampling all forty external player clips.
        name = 'light_male'
        preview = json.loads((ROOT / 'static/t2/model_json' / (name + '.json')).read_text())
        preview['metadata']['external_sequences'] = ['shapes/light_male_forward.dsq']
        preview['metadata']['script_sequence_bindings'] = []
        model = load_animated_model(name, DEFAULT_GAME_DATA, preview, use_cache=False)
        self.assertTrue(model['animation_clips'])
        self.assertTrue(any(not np.allclose(clip['frames'][0]['vertices'], frame['vertices'])
                            for clip in model['animation_clips'] for frame in clip['frames'][1:]))
        for clip in model['animation_clips']:
            self.assertGreater(clip['fps'], 0)
            self.assertTrue(all(len(frame['vertices']) == len(preview['vertices']) for frame in clip['frames']))


if __name__ == '__main__':
    unittest.main()

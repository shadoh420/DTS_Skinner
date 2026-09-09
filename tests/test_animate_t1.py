"""Source-backed T1 motion, static fallback and missing-source checks."""
from pathlib import Path
import contextlib
import io
import tempfile
import unittest
from unittest.mock import patch

from tools.animate_t1 import load_animated_model
from tools.model_data import load_model_data


ROOT = Path(__file__).resolve().parents[1]


class T1AnimationTests(unittest.TestCase):
    def load(self, name):
        preview = load_model_data(ROOT / 'static/model_json' / (name + '.json'))
        return load_animated_model(name, ROOT / 'tools/dts_files', preview)

    def test_real_armor_run_has_motion_and_source_duration(self):
        data = self.load('larmor')
        self.assertEqual(len(data['animation_clips']), 45)
        run = next(c for c in data['animation_clips'] if c['name'] == 'run')
        self.assertTrue(run['loop'])
        self.assertAlmostEqual(run['duration'], 2/3, places=5)
        self.assertEqual(run['times'][0], 0)
        self.assertAlmostEqual(run['times'][-1], run['duration'], places=6)
        self.assertTrue(all(a < b for a, b in zip(run['times'], run['times'][1:])))
        self.assertTrue(any(frame['vertices'] != run['frames'][0]['vertices'] for frame in run['frames']))
        self.assertTrue(all(len(frame['vertices']) == len(data['vertices']) for frame in run['frames']))
        preview = load_model_data(ROOT / 'static/model_json/larmor.json')
        self.assertEqual(data['indices'], preview['indices'])
        self.assertEqual(data['uvs'], preview['uvs'])
        self.assertLess(max(abs(a-b) for a,b in zip(data['vertices'], preview['vertices'])), 1e-5)

    def test_real_weapon_cel_frames_move(self):
        data = self.load('disc')
        self.assertEqual({c['name'] for c in data['animation_clips']}, {'activation', 'reload', 'spin', 'fire'})
        spin = next(c for c in data['animation_clips'] if c['name'] == 'spin')
        self.assertTrue(any(f['vertices'] != spin['frames'][0]['vertices'] for f in spin['frames']))

    def test_static_dts_is_not_given_fake_animation(self):
        data = self.load('ammo1')
        self.assertEqual(data['animation_clips'], [])
        self.assertIn('no sequences', data['metadata']['animation_status'])

    def test_malformed_source_sequence_is_reported_without_losing_valid_motion(self):
        from dts_module import dts
        source = ROOT / 'tools/dts_files/larmor.dts'
        shape = dts()
        with contextlib.redirect_stdout(io.StringIO()):
            shape.load_file(str(source))
        shape.sequences = shape.sequences[:3]
        first = next(s for s in shape.sub_sequences if s.sequence_idx == 1)
        shape.keyframes[first.first_key_frame].position = float('nan')
        shape.load_file = lambda _: True
        preview = load_model_data(ROOT / 'static/model_json/larmor.json')
        with patch('tools.animate_t1.dts', return_value=shape):
            result = load_animated_model('larmor', source, preview)
        self.assertEqual([c['name'] for c in result['animation_clips']], ['root', 'runback'])
        self.assertEqual(result['metadata']['unsupported_sequences'][0]['name'], 'run')
        self.assertIn('key time', result['metadata']['unsupported_sequences'][0]['reason'])

    def test_static_dis_and_missing_source_are_explicit(self):
        preview = load_model_data(ROOT / 'static/model_json/ammo1.json')
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(FileNotFoundError, 'animation source missing'):
                load_animated_model('missing', temporary, preview)
            path = Path(temporary) / 'Static.dis'
            path.write_bytes(b'')
            data = load_animated_model('static', path, preview)
            self.assertEqual(data['vertices'], preview['vertices'])
            self.assertEqual(data['animation_clips'], [])
            self.assertIn('Static DIS', data['metadata']['animation_status'])


if __name__ == '__main__':
    unittest.main()

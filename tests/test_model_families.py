"""The user's own model families: saved per game, laid over the imported categories, reversible."""
import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import app
from tools.model_data import read_families


class ModelFamiliesTests(unittest.TestCase):
    def test_move_back_validation_and_origin(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            (root / 'models').mkdir()
            for name in ('larmor', 'marmor', 'disc'):
                (root / 'models' / f'{name}.json').write_text('{}')
            stack.enter_context(patch('app.model_json_dir', root / 'models'))
            stack.enter_context(patch('app.local_data_dir', root / 'local'))
            client = app.test_client()
            families = lambda: {e['model_name']: e['category'] for e in client.get('/list_models').json}
            self.assertEqual(set(families().values()), {'Complete T1 catalog'})

            moved = client.post('/model_families', json=dict(models=['larmor', 'marmor'], family='  Armors  '))
            self.assertEqual(moved.json['family'], 'Armors')
            self.assertEqual(families(), {'disc': 'Complete T1 catalog', 'larmor': 'Armors', 'marmor': 'Armors'})
            entry = next(e for e in client.get('/list_models').json if e['model_name'] == 'larmor')
            self.assertEqual(entry['import_category'], 'Complete T1 catalog')
            # Families are per game: T2 keeps its own.
            self.assertEqual(client.post('/model_families?game=t2', json=dict(models=['larmor'], family='Other game')).status_code, 200)
            self.assertEqual(families()['larmor'], 'Armors')

            self.assertEqual(client.post('/model_families', json=dict(models=['marmor'], family='')).status_code, 200)
            self.assertEqual(families()['marmor'], 'Complete T1 catalog')
            family_file = root / 'local/model-families.json'
            self.assertEqual(read_families(family_file), {'t1': {'larmor': 'Armors'}, 't2': {'larmor': 'Other game'}})

            original = family_file.read_bytes()
            for bad in (dict(models=[], family='x'), dict(models=['a'], family=None), dict(models=[1], family='x'),
                        dict(models='larmor', family='x'), dict(models=['a'], family='x' * 81)):
                self.assertEqual(client.post('/model_families', json=bad).status_code, 422)
            self.assertEqual(client.post('/model_families', json=dict(models=['a'])).status_code, 400)
            self.assertEqual(client.post('/model_families?game=nope', json=dict(models=['a'], family='x')).status_code, 400)
            self.assertEqual(client.post('/model_families', json=dict(models=['a'], family='x'),
                                         headers={'Origin': 'https://elsewhere.invalid'}).status_code, 403)
            self.assertEqual(family_file.read_bytes(), original)

            # A broken file is reported and never overwritten; the catalog still loads with the imported families.
            family_file.write_text('broken')
            self.assertEqual(client.post('/model_families', json=dict(models=['a'], family='x')).status_code, 422)
            self.assertEqual(set(families().values()), {'Complete T1 catalog'})
            self.assertEqual(family_file.read_text(), 'broken')


if __name__ == '__main__':
    unittest.main()

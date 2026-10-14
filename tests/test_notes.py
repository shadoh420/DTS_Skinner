"""The user's notes on models and maps (local-data/notes.json) and the notes box on every map page."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import app
from tools.notes import read_notes


class NotesTests(unittest.TestCase):
    def test_save_read_delete_and_map_pages(self):
        with tempfile.TemporaryDirectory() as directory, patch('app.local_data_dir', Path(directory)):
            client = app.test_client()
            post = lambda **body: client.post('/notes', json=body)
            self.assertEqual(client.get('/notes').json, [])
            self.assertEqual(post(page='models/t1', subject='larmor', text='Seam on the helmet').status_code, 200)
            self.assertEqual(post(page='/maps/t1/', subject='Raindance', text='Tower floats').status_code, 200)
            self.assertEqual(post(page='models/t1', subject='larmor', text='Seam on the helmet; visor too').status_code, 200)
            one = client.get('/notes?page=models/t1&subject=larmor').json
            self.assertEqual([n['text'] for n in one], ['Seam on the helmet; visor too'])
            self.assertEqual([n['subject'] for n in read_notes(Path(directory) / 'notes.json')], ['Raindance', 'larmor'])

            self.assertEqual(post(page='models/t1', subject='larmor', text='  ').json['note'], None)
            self.assertEqual(client.get('/notes?page=models/t1&subject=larmor').json, [])
            self.assertEqual(post(page='', subject='x', text='y').status_code, 422)
            self.assertEqual(post(page='p', subject='x').status_code, 400)
            self.assertEqual(client.post('/notes', json=dict(page='p', subject='x', text='y'),
                                         headers={'Origin': 'https://elsewhere.invalid'}).status_code, 403)
            self.assertEqual(len(client.get('/notes').json), 1)

            with client.get('/maps/t1/') as page:
                self.assertIn(b'<script src="/static/notes.js"></script></body>', page.data)
                self.assertIn(b'<script src="/static/import-timer.js"></script>', page.data)
            self.assertIn(b'/static/notes.js', client.get('/').data)


if __name__ == '__main__':
    unittest.main()

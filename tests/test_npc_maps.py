"""Shared Maps script ordering; no retail assets or running server required."""
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from app import app


class NPCMapPageTests(unittest.TestCase):
    def test_every_maps_page_loads_helper_before_viewer(self):
        with tempfile.TemporaryDirectory() as directory, patch('app.local_data_dir', Path(directory)):
            client = app.test_client()
            routes = sorted(rule.rule for rule in app.url_map.iter_rules()
                            if rule.rule.startswith('/maps/') and '<' not in rule.rule)
            self.assertIn('/maps/', routes)
            self.assertIn('/maps/doom3/', routes)
            self.assertIn('/maps/quake4/', routes)
            for route in routes:
                with self.subTest(route=route), client.get(route) as response:
                    self.assertEqual(response.status_code, 200)
                    scripts = re.findall(rb'<script\b[^>]*>', response.data)
                    helper = b'<script src="/static/npc-toggle.js">'
                    self.assertEqual(scripts[0], helper)
                    self.assertEqual(scripts.count(helper), 1)
                    self.assertIn(b'/static/notes.js', response.data)
                    if route in ('/maps/doom3/', '/maps/quake4/'):
                        self.assertLess(response.data.index(b'/doom3-maps/npcs.js'),
                                        response.data.index(b'/doom3-maps/viewer.js'))
            with client.get('/maps/not-a-viewer/') as response:
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(b'/static/npc-toggle.js', response.data)


if __name__ == '__main__':
    unittest.main()

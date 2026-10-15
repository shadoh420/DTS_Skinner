"""Continuous movers (idRotater, idBobber, idPendulum) and what is bound to them; no retail install needed."""
import json
import math
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np

from tools.doom3_movers import angles_to_axis, axis_to_angles, mover_records, swept_bounds


class DoomMoverTest(unittest.TestCase):
    def test_angle_conventions_round_trip(self):
        for angles in ([0, 90, 0], [30, -45, 10], [-20, 170, 80]):
            np.testing.assert_allclose(axis_to_angles(angles_to_axis(angles)), angles, atol=1e-9)
        np.testing.assert_allclose(angles_to_axis([0, 90, 0]) @ [1, 0, 0], [0, 1, 0], atol=1e-12)

    def test_records_parameters_and_bind_chains(self):
        entities = [
            {'classname': 'func_rotating', 'name': 'fan', 'start_on': '1', 'speed': '45', 'x_axis': '1', 'origin': '1 2 3', 'bind': 'lift'},
            {'classname': 'func_rotating', 'name': 'idle'},
            {'classname': 'func_bobbing', 'name': 'lift', 'height': '8', 'speed': '2', 'phase': '.5', 'origin': '0 0 0'},
            {'classname': 'func_pendulum', 'name': 'swing', 'speed': '20', 'origin': '0 0 100'},
            {'classname': 'light', 'name': 'alarm', 'bind': 'fan'},
            {'classname': 'func_static', 'name': 'blade', 'bind': 'alarm'}]
        origin = lambda e: [float(v) for v in e.get('origin', '0 0 0').split()]
        movers, owner = mover_records(entities, origin, lambda e: np.eye(3), lambda e: -96.0)
        by = {m['name']: m for m in movers}
        self.assertNotIn('idle', owner, 'func_rotating without start_on waits for a trigger')
        self.assertEqual(by['fan']['delta'], [0, 0, 45.0], 'x_axis spins the roll angle')
        self.assertEqual(movers[by['fan']['parent']]['name'], 'lift')
        self.assertEqual((by['lift']['delta'], by['lift']['start'], by['lift']['duration']), ([0, 0, 16.0], 500.0, 1000.0))
        freq = 1 / (2 * math.pi) * math.sqrt(1066 / (3 * 96))
        self.assertAlmostEqual(by['swing']['duration'], 500 / freq); self.assertEqual(by['swing']['delta'], [0, 0, 40.0])
        self.assertEqual(owner['alarm'], owner['fan']); self.assertEqual(owner['blade'], owner['fan'], 'bind chains reach the mover')
        lo, hi = swept_bounds([[1, 2, 13], [1, 2, 13]], movers, owner['fan'])
        self.assertGreaterEqual(min(hi[2] - 3, 3 - lo[2]), 10, 'a full turn about the fan pivot')

    def test_import_tags_surfaces_and_lights(self):
        from tests.test_doom3_maps import fixture
        from tools import import_doom3_map as maps
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); fixture(root / 'install')
            with zipfile.ZipFile(root / 'install/base/pak001.pk4', 'w') as z:
                with zipfile.ZipFile(root / 'install/base/pak000.pk4') as original:
                    z.writestr('maps/game/test.map', original.read('maps/game/test.map').replace(b'"classname" "func_door"', b'"classname" "func_rotating" "start_on" "1"')
                               .replace(b'"name" "lamp"', b'"name" "lamp" "bind" "door"'))
            maps.import_maps(root / 'install', root / 'data', maps=['base:game/test'])
            s = json.loads((root / 'data/doom3-maps/maps/base_game_test/scene.json').read_text())
            self.assertEqual([m['kind'] for m in s['movers']], ['rotate'])
            door = next(x for x in s['surfaces'] if x['entity'] == 'door')
            self.assertEqual(door['mover'], 0); self.assertIn('moverBounds', door)
            lamp = next(l for l in s['lights'] if l['name'] == 'lamp'); self.assertEqual(lamp['mover'], 0)


if __name__ == '__main__': unittest.main()

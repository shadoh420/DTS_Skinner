"""Synthetic compiled-world events; no retail records or executable bytes."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from tools import import_azrael_map as maps
from tools.azrael_world import read_world, entry_spawns
from tests.test_azrael_maps import fixture, string


def words(*values):
    return struct.pack('<' + 'H' * len(values), *values)


def condition(op, index):
    return bytes([op]) + words(index) + bytes(34)


def spawn(name, x=30., y=2., z=40., heading=90):
    return bytes([30]) + string(name, 12) + struct.pack('<fffh', x, y, z, heading)


def world(first_conditions=None, first_action=None):
    # One scene, two zone events. The second is a puzzle interaction, not entry.
    zone = lambda name: words(1, 1) + string(name, 11) + words(1, 0)
    block = lambda conditions, actions: words(0, len(conditions)) + b''.join(conditions) + words(len(actions)) + b''.join(actions)
    blocks = [block(first_conditions if first_conditions is not None else [condition(1, 0), condition(10, 0)],
                    [first_action if first_action is not None else spawn('Hero')]),
              block([condition(1, 1)], [spawn('Later')])]
    return (bytes(30) + words(0) + words(2) + zone('zxentry') + zone('zxlever') +
            words(1) + string('room', 9) + words(0, 2, 0, 1) + words(0, 1, 0) +
            words(1, 0, 1, len(blocks)) + b''.join(blocks))


class AzraelNPCTest(unittest.TestCase):
    def test_unresolved_states_are_not_populations_and_names_are_unique(self):
        for change in ('state', 'disabled', 'prior-action'):
            parsed = read_world(world())
            script = parsed['scripts'][0]
            if change == 'state':
                script['groups'].append([]); script['initial'] = 1
            elif change == 'disabled':
                parsed['zones'][0]['enabled'] = 0
            else:
                script['groups'][0][0]['actions'].insert(0, dict(op=31, room=0))
            self.assertFalse(any(r['status']=='selected' for r in entry_spawns(parsed)))
        parsed = read_world(world())
        actions = parsed['scripts'][0]['groups'][0][0]['actions']
        actions.append(dict(actions[0], offset=999, position=[1,2,3]))
        rows = entry_spawns(parsed)
        self.assertEqual(rows[0]['reason'], 'superseded-initial-spawn')
        self.assertEqual([r['position'] for r in rows if r['status']=='selected'], [[1,2,3]])

    def test_entry_selected_later_event_excluded_and_bounded_reader(self):
        data = world()
        rows = entry_spawns(read_world(data))
        self.assertEqual([r['status'] for r in rows], ['selected', 'excluded'])
        self.assertEqual(rows[0]['rooms'], ['room'])
        self.assertEqual(rows[0]['position'], [30, 2, 40])
        self.assertEqual(rows[1]['reason'], 'later-or-non-entry-event')
        self.assertEqual(data[rows[0]['source_record']['offset']], 30)
        for invalid in (data[:-1], data + b'\0', bytes(29), world(first_action=spawn('Hero', x=float('nan'))),
                        world(first_conditions=[condition(1, 99)]), world(first_action=spawn('../bad'))):
            with self.assertRaises(ValueError): read_world(invalid)
        for conditions in ([condition(1, 0), condition(4, 0)], [condition(1, 0), condition(18, 0)]):
            self.assertEqual(entry_spawns(read_world(world(conditions)))[0]['status'], 'excluded')

    def test_export_tag_transform_textures_and_failure_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); game, _ = fixture(root)
            (game/'WORLD.DAT').write_bytes(world())
            # OFFSET is in parent space, independent of the heading rotation.
            with (game/'HERO.ANI').open('ab') as stream:
                stream.write(b'\nOFFSET 1,2,3\n')
            cd, entries, zones, _ = maps.plan_maps(root)
            entry = entries[0]
            before, base_blob, _ = maps.build_map(cd, entry, zones)
            selected = entry_spawns(read_world(world()))[:1]
            scene, blob, images = maps.build_map(cd, entry, zones, game=game, spawns=selected)
            actor = scene['characters'][0]
            self.assertEqual(actor['status'], 'drawn')
            self.assertEqual(actor['character_id'], 'hero')
            self.assertEqual(actor['model_source'], 'textures/hero.xaf')
            self.assertEqual(scene['npc_count'], 1)
            self.assertEqual(scene['viewpoints'], before['viewpoints'])
            self.assertEqual(scene['bounds'], before['bounds'])
            npc = [g for g in scene['groups'] if g.get('npc')]
            self.assertTrue(npc)
            self.assertEqual({g['entity'] for g in npc}, {actor['entity']})
            self.assertTrue(all(g['texture'] in images for g in npc))
            self.assertTrue(all(not g.get('npc') for g in scene['groups'][:len(before['groups'])]))
            self.assertEqual(len(blob), scene['vertices']*20 + scene['indices']*4)
            # At 90 degrees native (x,y,z) -> (z,y,-x), then reflect Z.
            from tools import import_azrael as az
            model_scene = az.parse_xaf(cd.read('textures/hero.xaf'))
            ani = az.parse_ani(az.read_file(game/'HERO.ANI'))
            frame = ani['clips']['hero.xaf']['REST'][0]
            local = az.build_model(model_scene, {n: 'x.png' for n in model_scene['materials']},
                                   az.evaluate_world_matrices(model_scene, frame))
            local = np.array(local['vertices']).reshape(-1,3) * [1,1,-1]
            expected = np.column_stack((local[:,2]+31, local[:,1]+4, local[:,0]-43))
            points = np.frombuffer(blob, '<f4', scene['vertices']*3).reshape(-1,3)
            np.testing.assert_allclose(points[before['vertices']:], expected, atol=1e-5)
            report = maps.import_maps(root, root/'out', map_filter='First')
            self.assertEqual(report['characters']['spawns'][1]['status'], 'excluded')
            saved = json.loads((root/'out/maps'/entry['id']/'scene.json').read_text())
            self.assertEqual(saved['npc_count'], 1)
            self.assertEqual(len(saved['characters']), 1)
            self.assertEqual(report['characters']['per_entry'][0]['drawn'], 1)
            (game/'HERO.ANI').unlink()
            failed, _, _ = maps.build_map(cd, entry, zones, game=game, spawns=selected)
            self.assertEqual(failed['npc_count'], 0)
            self.assertEqual(failed['characters'][0]['status'], 'failed')
            self.assertFalse(any(g.get('npc') for g in failed['groups']))


if __name__ == '__main__':
    unittest.main()

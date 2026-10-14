"""Synthetic compiled-world events; no retail records or executable bytes."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from tools import import_azrael_map as maps
from tools.azrael_world import read_world, entry_spawns, first_arrivals, run_event, fresh_state, condition_matches, Blocked
from tests.test_azrael_maps import fixture, string


def words(*values):
    return struct.pack('<' + 'H' * len(values), *values)


def condition(op, index):
    return bytes([op]) + words(index) + bytes(34)


def spawn(name, x=30., y=2., z=40., heading=90):
    return bytes([30]) + string(name, 12) + struct.pack('<fffh', x, y, z, heading)


def action(op, index=0, value=0):
    return bytes([op]) + words(index, value) + bytes(22)


def portal_world(arrival_actions=None):
    """Two rooms; entry sets a flag, exit carries it into a queued arrival."""
    block = lambda next_group, cs, acts: words(next_group, len(cs)) + b''.join(cs) + words(len(acts)) + b''.join(acts)
    groups = [
        [block(0, [condition(1, 0)], [action(4, 0, 1)]),
         block(1, [condition(1, 1)], [action(31, 1, 0)]),
         block(0, [condition(1, 2)], [action(4, 1, 1), spawn('Later')])],
        [block(0, [condition(4, 1)], [spawn('PuzzleGated')]),
         block(0, [condition(4, 0)], arrival_actions if arrival_actions is not None else [spawn('Visitor')])]]
    zone = lambda n: words(1, 0) + string(n, 11) + words(1, 0)
    obj = words(0) + bytes(10) + string('zyentry', 11) + bytes(30)
    return (bytes(30) + words(1) + obj + words(3) + zone('zxentry') + zone('zxexit') + zone('zxpuzzle') +
            words(2) + string('first', 9) + words(0, 2, 0, 1) + string('second', 9) + words(1, 0, 1, 2) +
            words(0, 2, 1, 1, 0, len(groups)) +
            b''.join(words(len(g)) + b''.join(g) for g in groups))


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
                script['groups'][0][0]['actions'].insert(0, dict(op=255, offset=123))
            self.assertFalse(any(r['status']=='selected' for r in entry_spawns(parsed)))
        parsed = read_world(world())
        actions = parsed['scripts'][0]['groups'][0][0]['actions']
        actions.append(dict(actions[0], offset=999, position=[1,2,3]))
        rows = entry_spawns(parsed)
        self.assertEqual(rows[0]['status'], 'excluded')
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
        for conditions in ([condition(1, 0), condition(4, 0)], [condition(1, 0), bytes([17])+string('Hero',12)+words(0)+bytes(22)]):
            self.assertEqual(entry_spawns(read_world(world(conditions)))[0]['status'], 'excluded')

    def test_portal_carries_flag_and_excludes_puzzle_trigger(self):
        parsed = read_world(portal_world())
        simulation = first_arrivals(parsed)
        self.assertNotIn('_dispatch', parsed)
        room = simulation['rooms'][1]
        self.assertEqual([a['character'] for a in room['actors']], ['Visitor'])
        self.assertEqual(room['actors'][0]['route'][0]['destination'], 1)
        rows = {r['character']: r for r in entry_spawns(parsed, simulation=simulation)}
        self.assertEqual(rows['Visitor']['rooms'], ['second'])
        self.assertEqual(rows['PuzzleGated']['reason'], 'state-gated')
        self.assertEqual(rows['PuzzleGated']['deciding_conditions'][0]['index'], 1)
        self.assertEqual(rows['Later']['reason'], 'later-or-non-entry-event')

    def test_terminal_actor_and_room_change_remove_live_preview(self):
        kill = bytes([21,12]) + string('Visitor',12) + bytes(13)
        parsed = read_world(portal_world([spawn('Visitor'), kill, spawn('Visitor', x=99)]))
        simulation = first_arrivals(parsed)
        self.assertEqual(simulation['rooms'][1]['actors'], [])
        self.assertEqual(simulation['rooms'][1]['alternatives'][0]['removed'][0]['reason'], 'terminal-animation-state')
        state = fresh_state(parsed, 0)
        state['actors']['old'] = dict(character='Old')
        state['flags'][0] = 1
        result = run_event(parsed, state, dict(op=1,index=1), [0])
        self.assertNotIn('old', result['actors'])
        self.assertIn(('visitor',1), result['dead'])

    def test_unhandled_action_rolls_back_arrival_and_names_blocked_spawn(self):
        for actions in ([action(255),spawn('Visitor')], [spawn('Visitor'),action(255)]):
            parsed = read_world(portal_world(actions))
            result = first_arrivals(parsed)
            self.assertEqual(result['rooms'][1]['status'], 'blocked')
            self.assertEqual(result['rooms'][1]['actors'], [])
            row = next(r for r in entry_spawns(parsed,simulation=result) if r['character']=='Visitor')
            self.assertEqual(row['reason'], 'blocked-operation')
            self.assertIn('255', row['blocker']['reason'])
            state = fresh_state(parsed, 0); state['flags'][0] = 1
            with self.assertRaises(Blocked): run_event(parsed,state,dict(op=1,index=1),[0])
            self.assertEqual(state['room'], 0)
            self.assertEqual(state['actors'], {})

    def test_route_conflicts_are_excluded(self):
        parsed = read_world(portal_world())
        script = parsed['scripts'][0]
        # A second portal to the same room skips its population continuation.
        parsed['zones'].append(dict(name='zxother',enabled=1,one_shot=0,scripts=[0]))
        parsed['scenes'][0]['zones'].append(3)
        script['groups'][0].append(dict(next_group=0,conditions=[dict(op=1,index=3,offset=999)],
            actions=[dict(op=31,room=1,arrival=0,offset=1000)]))
        result = first_arrivals(parsed)
        self.assertEqual(result['rooms'][1]['variants'], 2)
        self.assertEqual(result['rooms'][1]['actors'], [])
        row = next(r for r in entry_spawns(parsed,simulation=result) if r['character']=='Visitor')
        self.assertEqual(row['reason'], 'ambiguous-first-arrival')

    def test_variable_wrap_flags_and_contact_guard(self):
        parsed = read_world(portal_world())
        actions = parsed['scripts'][0]['groups'][0][0]['actions']
        actions.extend([dict(op=15,index=0,value=65535,offset=901), dict(op=14,index=0,value=2,offset=902)])
        state = run_event(parsed, fresh_state(parsed,0), dict(op=1,index=0), [0])
        self.assertTrue(condition_matches(dict(op=9,index=0,value=1),dict(op=13),state))
        self.assertFalse(condition_matches(dict(op=11,index=0,value=1),dict(op=13),state))
        self.assertTrue(condition_matches(dict(op=4,index=0),dict(op=13),state))
        self.assertFalse(condition_matches(dict(op=10,index=0),dict(op=13),state))
        with self.assertRaisesRegex(Blocked,'Contact condition op 14'):
            condition_matches(dict(op=14,index=0,offset=999),dict(op=13),state)

    def test_transit_join_carries_both_flag_histories(self):
        parsed = read_world(portal_world())
        # Two routes enter a transit room with opposite flags, then a common
        # portal reaches the target. Neither route's flag wins by queue order.
        parsed['scenes'].append(dict(name='third',objects=[0],zones=[]))
        parsed['zones'].append(dict(name='zxother',enabled=1,one_shot=0,scripts=[0]))
        parsed['zones'].append(dict(name='zxonward',enabled=1,one_shot=0,scripts=[0]))
        parsed['scenes'][0]['zones'].append(3)
        parsed['scenes'][1]['zones'].append(4)
        groups = parsed['scripts'][0]['groups']
        groups[0][1]['next_group'] = 0
        groups[0].extend([
            dict(next_group=0,conditions=[dict(op=1,index=3,offset=991)],actions=[dict(op=4,index=0,value=0,offset=992),dict(op=31,room=1,arrival=0,offset=993)]),
            dict(next_group=1,conditions=[dict(op=1,index=4,offset=994)],actions=[dict(op=31,room=2,arrival=0,offset=995)])])
        groups[1].append(dict(next_group=0,conditions=[dict(op=13,offset=996)],actions=[]))
        result = first_arrivals(parsed)
        self.assertEqual(result['rooms'][2]['variants'], 2)
        self.assertEqual(result['rooms'][2]['actors'], [])
        self.assertEqual(result['rooms'][2]['status'], 'reachable')

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
            # A spawn parked outside the room (scripts move it in later) is not drawn.
            staged, _, _ = maps.build_map(cd, entry, zones, game=game,
                                          spawns=[dict(selected[0], position=[1000, 1000, 1000])])
            self.assertEqual(staged['npc_count'], 0)
            self.assertEqual(staged['characters'][0]['status'], 'staged')
            self.assertFalse(any(g.get('npc') for g in staged['groups']))
            (game/'HERO.ANI').unlink()
            failed, _, _ = maps.build_map(cd, entry, zones, game=game, spawns=selected)
            self.assertEqual(failed['npc_count'], 0)
            self.assertEqual(failed['characters'][0]['status'], 'failed')
            self.assertFalse(any(g.get('npc') for g in failed['groups']))


if __name__ == '__main__':
    unittest.main()

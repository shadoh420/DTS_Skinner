"""NPC ownership survives export and same-material batching; synthetic assets only."""
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import test_sin_maps as sin_fixture, test_daikatana_maps as dk_fixture, test_anachronox_maps as anox_fixture
from test_dx_maps import make_map
from tools import import_t1_map as t1, import_unreal_map as unreal, import_sin_map as sin
from tools import import_daikatana_map as dk, import_anachronox_map as anox
from tools.import_unreal import Library, Package


def entities(*rows):
    return ('\n'.join('{' + ' '.join(f'"{k}" "{v}"' for k, v in row.items()) + '}' for row in rows) + '\0').encode()


class NPCStep2Tests(unittest.TestCase):
    def assert_groups(self, scene, expected):
        groups = [g for g in scene['groups'] if g.get('npc') and g['count']]
        self.assertEqual({g['entity'] for g in groups}, set(expected))
        self.assertTrue(all(g['classname'] for g in groups))
        ordinary = {g['texture'] for g in scene['groups'] if not g.get('npc')}
        self.assertTrue(ordinary.intersection(g['texture'] for g in groups), 'same-material scenery stays separate')
        self.assertEqual(sum(g['count'] for g in scene['groups']), scene['indices'])

    def test_unreal_ut_pawn_ancestry_and_dx_still_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = make_map(root)
            library, level = Library(root, game='dx'), Package(path, game='dx')
            layout = level.level(1)
            level.exports.append(dict(level.exports[8], name='Guard1'))
            other = len(level.exports)
            layout['actors'].append(other)
            level.level = lambda _: layout
            for game in ('unreal', 'ut'):
                with self.subTest(game=game):
                    scene, *_ = unreal.build_map(library, level, None, game=game)
                    self.assert_groups(scene, {9, other})
                    self.assertEqual({g['classname'] for g in scene['groups'] if g.get('npc')}, {'DeusEx.Guard'})
            scene, *_ = unreal.build_map(library, level, None, game='dx')
            self.assertFalse(any(g.get('npc') for g in scene['groups']))
            self.assertEqual(scene['actors']['excluded_npcs'], 2)

    def test_sin_models_keep_entity_identity_despite_shared_cached_model(self):
        raw = sin_fixture.bsp(entities=entities({'classname': 'worldspawn'},
            {'classname': 'func_scriptmodel', 'model': 'hero.def'},
            {'classname': 'Monster_Worker', 'model': 'hero.def'},
            {'classname': 'Animals_Rat', 'model': 'hero.def'},
            {'classname': 'Monster_DeadWorker', 'model': 'hero.def'}))
        with tempfile.TemporaryDirectory() as directory:
            files = sin_fixture.fixture(Path(directory), raw)
            for game in ('sin', '2015', 'ctf'):
                scene, *_ = sin.build_map(raw, 'test', files, game=game)
                self.assert_groups(scene, {2, 3})

    def test_sin_actor_vehicle_and_corpse_decisions(self):
        for cls, path, spawn, expected in [
            ('func_scriptmodel', 'worker.def', 'actor', True),
            ('monster_reconah', 'reconah.def', 'actor', True),
            ('Monster_HelicopterBoss', 'lamprey_man.def', 'vehicle', False),
            ('Monster_HelicopterBoss', 'lamprey/heli_base.sbm', '', False),
            ('vehicles_lamprey', 'lamprey.def', 'vehicle', False),
            ('Innocent_DeadGuy', 'deadguy.def', 'actor', False),
            ('func_scriptmodel', 'worker_mauled.def', 'object', False),
            ('gib_Head', 'gibhead.def', 'actor', False),
            ('trap_securityturret', 'turbase.def', '', True),
            ('ctf_turret', 'ctf_turret.def', 'CTFTurret', True),
            ('ctf_drivableturret', 'ctf_dt.def', 'CTFDrivableTurret', False),
            ('weapon_heligun', 'heligun.def', 'HeliGun', False),
        ]:
            with self.subTest(cls=cls):
                self.assertEqual(sin.model_is_npc(dict(classname=cls, path='models/' + path), dict(classname=spawn)), expected)

    def test_daikatana_ambient_animals_only(self):
        raw = dk_fixture.bsp(entity_lump=entities({'classname': 'worldspawn'},
            {'classname': 'deco_e1', 'model': 'test'},
            {'classname': 'e_seagull'}, {'classname': 'e_goldfish'},
            {'classname': 'monster_test', 'model': 'models/e1/test.dkm'},
            {'classname': 'info_player_start', 'origin': '0 0 64'}))
        table = dk_fixture.deco_table()
        for cls in dk.AMBIENT_ANIMALS:
            table[cls, ''] = dict(path='models/e1/test.dkm', scale='1', csv='aidata.csv')
        with patch.object(dk.dk, 'read_asset', side_effect=lambda entry: entry['data']):
            scene, *_ = dk.build_map(raw, 'test', dk_fixture.files(), table)
        self.assert_groups(scene, {2, 3})
        self.assertTrue(any(m['classname'] == 'monster_test' for m in scene['omitted_models']))

    def test_anachronox_character_types_and_hidden_characters(self):
        raw = anox_fixture.bsp(entity_lump=entities({'classname': 'worldspawn'},
            {'classname': 'prop'}, {'classname': 'npc_test'}, {'classname': 'fly'},
            {'classname': 'player'}, {'classname': 'npc_test', 'spawnflags': '2'}))
        with tempfile.TemporaryDirectory() as directory:
            files = anox_fixture.fixture(Path(directory), raw)
            table = anox.entity_table(files)
            for cls, kind in [('prop', 'object'), ('fly', 'charfly'), ('player', 'playerchar')]:
                table[cls] = dict(table['npc_test'], type=kind)
            scene, *_ = anox.build_map(raw, 'test', files, table)
        self.assert_groups(scene, {2, 3, 4})
        self.assertTrue(any(m['entity'] == 5 for m in scene['omitted_models']))

    def test_t1_townbot_and_placed_player_keep_class_and_datablock(self):
        mission = '''instant SimGroup "MissionGroup" {
instant SimTerrain { 
tedFileName = "test.dtf";
};
instant StaticShape "scenery" {
dataBlock = "Prop";
position = "0 0 0";
};
instant Player "placed player" {
dataBlock = "Armor";
position = "1 0 0";
};
instant SimGroup "TownBots" {
instant SimGroup "merchant" {
instant SimGroup "RACE Human";
instant Marker "spawn" {
position = "2 0 0";
};
};
};
};'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'test.mis'; source.write_text(mission)
            models = root / 'models'; models.mkdir(); (models / 'shared.json').write_text('{}')
            install = SimpleNamespace(find=lambda *a: None, shapes=lambda *a: {'prop': 'shared', 'armor': 'shared', 'humantownbot': 'shared'})
            index = dict(blockMap=[0], materialList='test.dml', unit=1, columns=1, rows=1)
            block = dict(size=1, heights=struct.pack('<4f', 0, 0, 0, 0), materials=b'', light=b'', lightWidth=2)
            dml = SimpleNamespace(load_binary=lambda _: None, materials=[])
            with patch.object(t1, 'mission_resources', return_value=([], {}, [], [], lambda _: b'')), \
                    patch.object(t1, 'read_terrain_index', return_value=index), \
                    patch.object(t1, 'read_terrain_block', return_value=block), \
                    patch.object(t1.interior_dml, 'dml', return_value=dml):
                scene = t1.import_mission(install, source, root / 'pack', models)
            actors = [o for o in scene['objects'] if o['npc']]
            self.assertEqual(len({o['entity'] for o in actors}), 2)
            self.assertEqual({(o['classname'], o['datablock']) for o in actors},
                             {('player', 'Armor'), ('staticshape', 'HumanTownBot')})
            self.assertEqual({o['model'] for o in scene['objects']}, {'shared'})
            self.assertEqual(len(scene['objects']), 3)


if __name__ == '__main__':
    unittest.main()

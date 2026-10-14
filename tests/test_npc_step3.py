"""Placed NPCs: synthetic formats, no game installs or real-library writes."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from tests import test_quake as qf, test_quake_maps as qmap
from tests import test_quake2 as q2f, test_quake2_maps as q2map
from tests import test_vtmb_maps as vf, test_daikatana_maps as df
from test_dx_maps import make_map
from test_npc_step2 import entities
from tools import import_quake as q, import_quake_map as qm
from tools import import_quake2 as q2, import_quake2_map as q2m
from tools import import_vtmb_map as vm, import_daikatana_map as dm
from tools import import_dx as dx, import_unreal_map as um
from tools.import_unreal import Library, Package


def ids(scene):
    return {g['entity'] for g in scene['groups'] if g.get('npc') and g['count']}


class NPCStep3Tests(unittest.TestCase):
    def test_dx_pawns_inherit_mesh_and_use_standing_pose(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            level = Package(make_map(root), game='dx')
            with patch.object(um, 'standing_pose', wraps=dx.standing_pose) as pose:
                scene, blob, images, _ = um.build_map(Library(root, game='dx'), level, None, game='dx')
            self.assertTrue(pose.called)
            self.assertEqual(ids(scene), {9})
            self.assertEqual(scene['actors']['npcs'], 1)
            self.assertFalse(scene['actors']['failed'])
            npc = [g for g in scene['groups'] if g.get('npc')]
            self.assertEqual({g['classname'] for g in npc}, {'DeusEx.Guard'})
            self.assertTrue({g['texture'] for g in npc} & {g['texture'] for g in scene['groups'] if not g.get('npc')})
            self.assertEqual(len(blob), scene['vertices'] * 32 + scene['indices'] * 4)
            library = Library(root, game='dx')
            original = library.class_info

            def player_chain(*args):
                defaults, sources, names = original(*args)
                return defaults, sources, [*names, 'DeusExPlayer'] if 'Guard' in names else names

            with patch.object(library, 'class_info', side_effect=player_chain):
                scene, *_ = um.build_map(library, level, None, game='dx')
            self.assertEqual(ids(scene), set())
            self.assertEqual(scene['actors']['excluded_players'], 1)
        for chain, kind in [(['DeusExPlayer', 'ScriptedPawn', 'Pawn'], 'player'),
                            (['Carcass', 'Pawn'], 'carcass'), (['Inventory'], 'pickup'),
                            (['Decoration'], 'decoration'), (['ScriptedPawn', 'Pawn'], 'npc')]:
            self.assertEqual(dx.map_actor_kind(chain), kind)

    def test_bloodlines_character_path_skin_and_batching(self):
        with tempfile.TemporaryDirectory() as folder:
            vf.fixture(Path(folder))
            files, _ = vm.vtmb.read_install(folder)
            for ext in ('.mdl', '.dx80.vtx'):
                files['models/character/test' + ext] = files['models/items/tiny' + ext]
            text = '\n'.join('{' + ' '.join(f'"{k}" "{v}"' for k, v in row.items()) + '}' for row in [
                dict(classname='prop_dynamic', model='models/character/test.mdl', skin=1),
                dict(classname='npc_test', model='models/character/test.mdl', skin=1, origin='64 0 0'),
                dict(classname='npc_test', model='models/character/test.mdl', skin=1, origin='128 0 0'),
                dict(classname='npc_vcamera', model='models/character/test.mdl'),
                dict(classname='npc_maker', model='models/character/test.mdl'),
                dict(classname='npc_maker'), dict(classname='npc_test'),
                dict(classname='npc_test', model='models/character/test.mdl', StartHidden=1),
            ])
            scene, blob, _, _ = vm.build_map(vf.bsp(text, static=False), 'test', files)
            self.assertEqual(ids(scene), {2, 3})
            self.assertFalse(scene['model_failures'])
            self.assertEqual(len(scene['omitted_models']), 5)
            self.assertTrue(all(m['pose']['skin_family'] == 1 and 'frame' in m['pose'] for m in scene['models']))
            ordinary = {g['texture'] for g in scene['groups'] if not g.get('npc')}
            self.assertTrue(ordinary & {g['texture'] for g in scene['groups'] if g.get('npc')})
            self.assertEqual(len(blob), scene['vertices'] * 28 + scene['indices'] * 4)

    def test_quake_skin_standing_frame_and_normal_sp_filter(self):
        raw_model = qf.mdl().replace(b'last\0', b'idle\0')
        mesh = q.read_mdl(raw_model, 1, standing=True)
        self.assertEqual((mesh['frame'], mesh['pose'], mesh['skin']), (1, 'idle', bytes([8] * 8)))
        self.assertEqual(q.read_mdl(raw_model)['pose'], 'first')  # Catalog default preserved.
        raw = bytearray(qmap.bsp())
        lump = entities({'classname': 'worldspawn'}, {'classname': 'info_player_start'},
                        {'classname': 'monster_army', 'skin': 1, 'origin': '320 0 0'},
                        {'classname': 'monster_army', 'spawnflags': 512},
                        {'classname': 'monster_army', 'spawnflags': 2048},
                        {'classname': 'monster_decoy'}, {'classname': 'spawn', 'monster': 'monster_army'},
                        {'classname': 'info_player_deathmatch'})
        struct.pack_into('<2i', raw, 4, len(raw), len(lump)); raw.extend(lump)
        files = {'progs/soldier.mdl': (raw_model, '', {})}
        scene, blob, _, _ = qm.build_map(raw, 'test', files=files)
        self.assertEqual(ids(scene), {2, 4})
        self.assertEqual(scene['models'][0]['parts'][0]['skin'], 1)
        self.assertEqual(scene['models'][0]['parts'][0]['frame'], 1)
        self.assertTrue(any(g['kind'] != 'model' and not g.get('npc') for g in scene['groups']))
        points = np.frombuffer(blob, '<f4', count=scene['vertices'] * 3).reshape(-1, 3)
        self.assertTrue(any(np.isclose(p[2], (320 + 77 * 2 + 10) / 32) for p in points))
        scene, *_ = qm.build_map(raw, 'test', game='qctf', files=files)
        self.assertEqual(ids(scene), set())

    def test_quake2_soldier_variant_skin_standing_and_filters(self):
        raw_model = q2f.md2(tuple(f'skin{i}.pcx' for i in range(6))).replace(b'later\0', b'stand\0')
        files = q2map.files()
        files.update({'models/monsters/soldier/tris.md2': (raw_model, '', {})})
        files.update({f'skin{i}.pcx': (q2f.pcx(i), '', {}) for i in range(6)})
        raw = q2map.bsp(entity_lump=entities({'classname': 'worldspawn'}, {'classname': 'info_player_start'},
            {'classname': 'monster_soldier'}, {'classname': 'monster_soldier_ss', 'spawnflags': 2},
            {'classname': 'monster_soldier', 'spawnflags': 512}, {'classname': 'monster_soldier', 'spawnflags': 2048},
            {'classname': 'monster_commander_body'}, {'classname': 'target_spawner', 'target': 'monster_soldier'},
            {'classname': 'info_player_deathmatch'}))
        scene, blob, _, _ = q2m.build_map(raw, 'test', files, qf.PALETTE)
        self.assertEqual(ids(scene), {2, 3, 5})
        self.assertEqual([(r['skin_number'], r['frame']) for r in scene['models'] if r['status'] == 'drawn'], [(2, 1), (4, 1), (2, 1)])
        self.assertTrue(scene['models'][1]['triggered'])
        self.assertEqual(len(blob), scene['vertices'] * 28 + scene['indices'] * 4)
        self.assertEqual(q2.read_md2(raw_model)['pose'], 'first')
        scene, *_ = q2m.build_map(raw, 'test', files, qf.PALETTE, game='ctf')
        self.assertEqual(ids(scene), set())

    def test_quake2_nonstandard_waiting_frame_names(self):
        # Native stand moves for medic, fixbot, flipper, carrier and Widow 2.
        for name in ('wait1', 'ambient_01', 'flphor01', 'search01', 'blackwidow3'):
            raw = q2f.md2().replace(b'later'.ljust(16, b'\0'), name.encode().ljust(16, b'\0'))
            self.assertEqual(q2.read_md2(raw, standing=True)['frame'], 1)
            self.assertEqual(q2.read_md2(raw)['frame'], 0)

    def test_alias_groups_never_merge_with_same_material_scenery(self):
        for module, classname, model, files in [
            (qm, 'monster_army', 'progs/soldier.mdl', {'progs/soldier.mdl': (qf.mdl(), '', {})}),
            (q2m, 'monster_soldier', 'models/monsters/soldier/tris.md2',
             {'models/monsters/soldier/tris.md2': (q2f.md2(), '', {}), 'models/test/skin.pcx': (q2f.pcx(), '', {})})]:
            data = dict(points=np.zeros((3, 3)), uvs=np.zeros((3, 2)), uv2=np.zeros((3, 2)),
                        groups=[dict(start=0, count=3)], indices=[0, 1, 2], textures={})
            bsp = dict(entities=[{}, dict(classname=classname), dict(classname=classname)])
            module.append_monsters(data, bsp, files, 'quake' if module is qm else 'quake2')
            # Rebuild with an existing scenery group using exactly the NPC material.
            texture = data['groups'][1]['texture']
            data = dict(points=np.zeros((3, 3)), uvs=np.zeros((3, 2)), uv2=np.zeros((3, 2)),
                        groups=[dict(texture=texture, start=0, count=3)], indices=[0, 1, 2], textures={})
            module.append_monsters(data, bsp, files, 'quake' if module is qm else 'quake2')
            self.assertEqual(ids(data), {1, 2})
            self.assertEqual(len(data['groups']), 3)
            self.assertFalse(data['groups'][0].get('npc'))

    def test_mission_pack_parts_and_variants(self):
        def empty():
            return dict(points=np.empty((0, 3)), uvs=np.empty((0, 2)), uv2=np.empty((0, 2)),
                        groups=[], indices=[], textures={})
        files = {f'progs/{name}.mdl': (qf.mdl(), '', {}) for name in ('armabody', 'armalegs', 'morph_az', 'knight')}
        data = empty()
        rows = qm.append_monsters(data, dict(entities=[dict(classname='monster_armagon')]), files, 'hipnotic')
        self.assertEqual(ids(data), {0})
        self.assertEqual(len(data['groups']), 2)
        half = len(data['points']) // 2
        np.testing.assert_allclose(data['points'][:half] - data['points'][half:], [[0, 0, 64]] * half)
        rows = qm.append_monsters(empty(), dict(entities=[dict(classname='monster_morph', spawnflags='2'),
                                                         dict(classname='monster_knight', spawnflags='2')]), files, 'rogue')
        self.assertEqual(rows[0]['parts'][0]['model'], 'progs/morph_az.mdl')
        self.assertEqual(rows[1]['parts'][0]['skin'], 1)
        files = {f'models/monsters/{name}/tris.md2': (q2f.md2(), '', {})
                 for name in ('boss3/jorg', 'boss3/rider', 'turret', 'turretbase')}
        files['models/test/skin.pcx'] = (q2f.pcx(), '', {})
        data = empty()
        rows, _ = q2m.append_monsters(data, dict(entities=[dict(classname='monster_jorg'),
            dict(classname='monster_turret', spawnflags='160', targetname='wake', angle='-1'),
            dict(classname='monster_turret', spawnflags='128')]), files, 'rogue')
        self.assertEqual(ids(data), {0, 1})
        self.assertEqual(len(data['groups']), 4)
        self.assertEqual(rows[1]['skin_number'], 2)
        self.assertEqual(rows[2]['status'], 'omitted')
        self.assertEqual(q2m.MONSTER_SKINS['soldier_hypergun'], 2)
        self.assertEqual(q2m.MONSTER_SKINS['soldier_lasergun'], 4)
        self.assertEqual(q2m.MONSTER_SKINS['chick_heat'], 3)

    def test_daikatana_aidata_monsters_companions_pose_and_spawner(self):
        files = df.files()
        csv = '\n'.join(','.join([cls, 'models/e1/test.dkm'] + ['0'] * 12 + ['1 1 1'])
                        for cls in ('monster_test', 'mikiko', 'superfly', 'cine_mikiko'))
        files['aidata.vsc'] = dict(data=b'CVSC' + bytes(c ^ 0x96 for c in csv.encode()))
        raw_model = bytearray(files['models/e1/test.dkm']['data'])
        struct.pack_into('<i', raw_model, len(raw_model) - 8, 1)  # idle sequence starts on frame one.
        files['models/e1/test.dkm']['data'] = bytes(raw_model)
        raw = df.bsp(entity_lump=entities({'classname': 'worldspawn'}, {'classname': 'info_player_start'},
            {'classname': 'deco_e1', 'model': 'test'}, {'classname': 'monster_test'}, {'classname': 'mikiko'},
            {'classname': 'monster_test', 'spawnflags': 8192}, {'classname': 'monster_spawner'},
            {'classname': 'func_monsterspawn', 'model': 'monster_test'}))
        with patch.object(dm.dk, 'read_asset', side_effect=lambda e: e['data']):
            table = dm.decoration_table(files); table.update(df.deco_table())
            scene, *_ = dm.build_map(raw, 'test', files, table)
        self.assertEqual(ids(scene), {3, 4})
        self.assertEqual({m['pose'] for m in scene['models'] if m['npc']}, {'later'})
        self.assertTrue(all(m['skins'] for m in scene['models'] if m['npc']))
        self.assertEqual({r['entity'] for r in scene['omitted_models']}, {5, 6, 7})
        self.assertFalse(scene['model_failures'])
        ordinary = {g['texture'] for g in scene['groups'] if not g.get('npc')}
        self.assertTrue(ordinary & {g['texture'] for g in scene['groups'] if g.get('npc')})


if __name__ == '__main__':
    unittest.main()

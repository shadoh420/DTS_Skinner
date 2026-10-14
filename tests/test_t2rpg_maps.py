"""Literal NPC extraction and mission-only overlays; all assets are synthetic."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.import_t2_map import ARCHIVES, import_maps
from tools.t2rpg_map import MISSION, npc_entries


PLAYER = 'datablock PlayerData(MaleHumanArmor) : Parent { shapeFile = "base_male.dts"; };'
TOWN = '''function DefineTownBots() {
    // spawnTownBot("9 9 9 0 0 1 0", 1, "Bank", "MaleHuman", 0, "Comment");
    spawnTownBot("1 2 3 0 0 1 1.5707963267948966", 1, "Bank", "MaleHuman", 0, "Banker");
    spawnquestbot("4 5 6 0 0 -1 0", 1, "MaleHuman", "Talk", "Old Man", "Quest");
}'''


class RPGMapsTests(unittest.TestCase):
    def test_literal_actors_and_town_calls_keep_identity_skin_and_transform(self):
        mission = '''new AIPlayer(Guard) { dataBlock = GuardArmor; position = "7 8 9";
                     rotation = "0 0 1 90"; skinName = "guard"; };
                     new Player(Unresolved) { dataBlock = %dynamic; position = "1 1 1"; };
                     new StaticShape(Prop) { dataBlock = GuardArmor; position = "0 0 0"; };'''
        scripts = {'scripts/player.cs': PLAYER + 'datablock PlayerData(GuardArmor : MaleHumanArmor) {};',
                   'missions/t2rpg_worldmap.cs': TOWN + '\ncreateAISpawn("0 0 0", "0 0 0", 1, 2, "1 2", 0, 0);'}
        npcs, notes = npc_entries(mission, scripts)
        self.assertEqual(len(npcs), 3)
        self.assertEqual(len({n['entity'] for n in npcs}), 3)
        self.assertTrue(all(n['npc'] and n['shape'] == 'base_male.dts' and n['pose'] == 'root' for n in npcs))
        self.assertEqual(npcs[0]['skin'], 'guard')
        self.assertEqual(npcs[0]['classname'], 'AIPlayer')
        self.assertEqual(npcs[1]['rotation'], [0, 0, 1, 90])
        self.assertEqual(npcs[1]['position'], [1, 2, 3])
        self.assertEqual(npcs[2]['name'], 'Old Man')
        self.assertEqual(len(notes), 2)

    def test_base_and_conditional_calls_do_not_invent_npcs(self):
        self.assertEqual(npc_entries('new StaticShape() { dataBlock = MaleHumanArmor; };', {}), ([], []))
        npcs, notes = npc_entries('', {'missions/t2rpg_worldmap.cs':
            'function DefineTownBots() { if ($quest) { spawnTownBot("0 0 0 0 0 1 0",1,"Bank","MaleHuman",0,"Hidden"); } }'})
        self.assertEqual(npcs, [])
        self.assertIn('nonliteral control flow', notes[0])
        npcs, notes = npc_entries('', {'missions/t2rpg_worldmap.cs':
            'function DefineTownBots() { if ($quest) spawnTownBot("0 0 0 0 0 1 0",1,"Bank","MaleHuman",0,"Hidden"); }'})
        self.assertEqual(npcs, [])
        self.assertIn('nonliteral control flow', notes[0])

    def test_mission_datablock_and_inherited_skin(self):
        mission = PLAYER + '''datablock PlayerData(Guard : MaleHumanArmor) { skinName = "green"; };
            new AIPlayer() { dataBlock = Guard; position = "1 2 3"; };'''
        npcs, notes = npc_entries(mission, {})
        self.assertEqual(notes, [])
        self.assertEqual((npcs[0]['shape'], npcs[0]['skin']), ('base_male.dts', 'green'))
        npcs, _ = npc_entries(mission + 'new Player() { dataBlock = Guard; position = "4 5 6"; };', {})
        self.assertEqual(len({npc['entity'] for npc in npcs}), 2)

    def test_overlay_does_not_change_stock_resources_or_missions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ARCHIVES:
                with zipfile.ZipFile(root / name, 'w') as archive:
                    if name == ARCHIVES[0]:
                        archive.writestr('scripts/server.cs', '// stock')
                        archive.writestr('missions/Base.mis', '// MissionTypes = CTF\nterrainFile = "base.ter";')
                        archive.writestr('terrains/base.ter', b'stock terrain')
                        archive.writestr('textures/base.png', b'stock skin')
                        archive.writestr('shapes/light_male_root.dsq', b'standing')
            loose = root / 'ironsphere'
            assets = {'scripts/player.cs': PLAYER, 'missions/t2rpg_worldmap.cs': TOWN,
                      'shapes/base_male.dts': 'mesh', 'textures/base.png': 'mod skin',
                      'scripts/server.cs': 'NEVER EXECUTE MOD SERVER',
                      'shapes/base_male.cs': 'sequence0 = "light_male_root.dsq root";'}
            for path, data in assets.items():
                target = loose / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(data)
            mod = root / 'ironsphere.vl2'
            with zipfile.ZipFile(mod, 'w') as archive:
                archive.writestr('missions/T2RPG_WorldMap.mis', '// MissionTypes = T2RPG\nnew SimGroup(MissionGroup) {};')
                archive.writestr('terrains/T2RPG_WorldMap.ter', b'mod terrain')
            import_maps(root, root / 'stock')
            report = import_maps(root, root / 'with-mod', ironsphere=mod, ironsphere_root=loose)
            stock = json.loads((root / 'stock/manifest.json').read_text())
            combined = json.loads((root / 'with-mod/manifest.json').read_text())
            self.assertEqual(stock['resources'], combined['resources'])
            self.assertEqual(stock['missions']['Base'], combined['missions']['Base'])
            for file in (root / 'stock/base').rglob('*'):
                if file.is_file():
                    self.assertEqual(file.read_bytes(), (root / 'with-mod/base' / file.relative_to(root / 'stock/base')).read_bytes())
            pack = combined['missionPacks'][MISSION]
            self.assertEqual(len(pack['npcs']), 2)
            self.assertNotIn('scripts/server.cs', pack['resources'])
            self.assertEqual(pack['resources']['shapes/base_male_root.dsq'], stock['resources']['shapes/light_male_root.dsq'])
            self.assertIn('mods/ironsphere/', pack['resources']['textures/base.png'][0])
            self.assertIn(MISSION, report['imported'])
            source = json.loads((root / 'with-mod/SOURCES.json').read_text())['missionPacks'][MISSION]
            self.assertEqual(len(source['sha256']), 64)
            self.assertEqual(len(source['files']), len(assets))
            import_maps(root, root / 'with-mod', replace=True, ironsphere=mod)
            archive_only = json.loads((root / 'with-mod/manifest.json').read_text())['missionPacks'][MISSION]
            self.assertEqual(archive_only['npcs'], [])
            self.assertTrue(archive_only['notes'])


if __name__ == '__main__':
    unittest.main()

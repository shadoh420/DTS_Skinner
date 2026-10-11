"""tools/sort_t1_families.py: the item scripts' folders, Dropships and InteriorShapes."""
from pathlib import Path
import tempfile
import unittest

from tools.sort_t1_families import sort_families


class SortT1FamiliesTests(unittest.TestCase):
    def test_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            items = Path(directory)
            scripts = {
                'armors/lightarmor.cs': 'PlayerData larmor\n{\n   shapeFile = "larmor";\n   shieldShapeName = "shield";\n};',
                'misc/grenade.cs': 'ItemData Grenade\n{\n   shapeFile = "grenade";\n};',
                'weapons/grenadelauncher.cs': 'GrenadeData GrenadeShell\n{\n   bulletShapeName = "grenade.dts";\n};',
                'misc/beacon.cs': 'ItemData Beacon\n{\n   shapeFile = "sensor_small";\n};',
                'sensors/motion.cs': 'SensorData MotionSensor\n{\n   shapeFile = "sensor_small";\n};',
                'turrets/indoor.cs': 'TurretData IndoorTurret\n{\n   shieldShapeName = "shield";\n};',
                'moveable.cs': 'MoveableData Door\n{\n   shapeFile = "newdoor5";\n};',
            }
            for name, text in scripts.items():
                (items / name).parent.mkdir(exist_ok=True)
                (items / name).write_text(text)
            names = ['larmor', 'grenade', 'sensor_small', 'shield', 'newdoor5', 'bedrop', 'Base1', 'hilde_be', 'staff']
            families, shared = sort_families(names, items, {'bedrop', 'base1', 'hilde_be'}, {'hilde_be'})
            self.assertEqual(families, {'larmor': 'Armors', 'grenade': 'Misc', 'sensor_small': 'Sensors', 'shield': 'Armors',
                                        'newdoor5': 'Moveables', 'bedrop': 'Dropships', 'Base1': 'InteriorShapes'})
            # A shapeFile reference beats a projectile's; shapes still shared are reported.
            self.assertEqual(shared, {'sensor_small': ['Misc', 'Sensors'], 'shield': ['Armors', 'Turrets']})


if __name__ == '__main__':
    unittest.main()

"""Static .gui rasters for guiSurf surfaces; no retail install needed."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from tools.doom3_gui import parse_gui, render_gui

GUI = '''windowDef Desktop
{
    rect 0, 0, 640, 480
    backcolor 0, 0, 1, 1
    onTime 0 { transition "Panel::matcolor" "1 1 1 0" "1 1 1 1" "500" ; }
    windowDef Clamp
    {
        rect 0, 0, 320, 240
        windowDef Panel
        {
            rect 160, 0, 320, 240
            background "gui/red"
            matcolor 1, 1, 1, 0.5
        }
        windowDef Hidden { rect 0, 0, 640, 480 visible 0 backcolor 1, 1, 1, 1 }
        windowDef Dynamic { rect 0, 120, 10, 10 visible "gui::show" backcolor 0, 1, 0, 1 }
    }
    windowDef Mirror { rect 640, 240, -320, 240 background "gui/half" }
    windowDef Title { rect 0, 400, 640, 80 text "#str_1" forecolor 1, 1, 0, 1 textalign 1 }
}
'''


class DoomGuiTest(unittest.TestCase):
    def test_tree_properties_and_event_blocks(self):
        root = parse_gui(GUI)
        self.assertEqual(root['name'], 'Desktop'); self.assertEqual(root['props']['rect'], ['0', '0', '640', '480'])
        self.assertEqual([c['name'] for c in root['children']], ['Clamp', 'Mirror', 'Title'])
        self.assertEqual([c['name'] for c in root['children'][0]['children']], ['Panel', 'Hidden', 'Dynamic'])
        self.assertNotIn('transition', str(root['props']), 'onTime blocks are skipped')

    def test_raster_clip_tint_flip_visibility_and_text(self):
        red = Image.new('RGBA', (4, 4), (255, 0, 0, 255))
        half = Image.new('RGBA', (2, 1), (255, 255, 255, 255)); half.putpixel((1, 0), (0, 0, 0, 255))
        image = render_gui(parse_gui(GUI), {'gui/red': red, 'gui/half': half}.get, {'#str_1': 'HELLO'})
        self.assertEqual(image.size, (640, 480))
        self.assertEqual(image.getpixel((5, 5)), (0, 0, 255, 255), 'Hidden (visible 0) draws nothing')
        r, g, b, _ = image.getpixel((200, 100)); self.assertGreater(r, 100); self.assertGreater(b, 100)
        self.assertEqual(image.getpixel((400, 100)), (0, 0, 255, 255), 'children clip to the parent rect')
        self.assertEqual(image.getpixel((5, 125))[:3], (0, 255, 0), 'gui:: visibility keeps the start state visible')
        self.assertEqual(image.getpixel((330, 300))[:3], (0, 0, 0), 'negative width mirrors the background')
        self.assertEqual(image.getpixel((630, 300))[:3], (255, 255, 255))
        self.assertTrue(any(image.getpixel((x, 410))[:2] == (255, 255) for x in range(250, 390)), 'centred yellow #str text')

    def test_import_guisurf_entity_material(self):
        from tests.test_doom3_maps import fixture, proc
        from tools import import_doom3_map as maps
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); fixture(root / 'install')
            with zipfile.ZipFile(root / 'install/base/pak001.pk4', 'w') as z:
                z.writestr('guis/test.gui', GUI)
                z.writestr('materials/gui.mtr', 'textures/common/entityGui { discrete guiSurf entity }')
                z.writestr('maps/game/test.proc', proc().replace(b'model { "Door" 1 { "textures/test"', b'model { "Door" 1 { "textures/common/entityGui"'))
                with zipfile.ZipFile(root / 'install/base/pak000.pk4') as original:
                    z.writestr('maps/game/test.map', original.read('maps/game/test.map').replace(b'"model" "door"', b'"model" "door" "gui" "guis/test.gui"'))
            maps.import_maps(root / 'install', root / 'data', maps=['base:game/test'])
            s = json.loads((root / 'data/doom3-maps/maps/base_game_test/scene.json').read_text())
            door = next(x for x in s['surfaces'] if x['entity'] == 'door')
            self.assertEqual(door['material'], 'gui:guis/test.gui')
            m = s['materials']['gui:guis/test.gui']
            self.assertEqual((m['sort'], m['translucent'], m['approximations']), (-2, True, ['gui start state only']))
            stage = m['stages'][-1]; self.assertEqual(stage['blend'], ['gl_one', 'gl_one_minus_src_alpha'], 'premultiplied raster')
            with Image.open(root / 'data/doom3/textures' / stage['image']['texture']) as raster: size = raster.size
            self.assertEqual(size, (640, 480))


if __name__ == '__main__': unittest.main()

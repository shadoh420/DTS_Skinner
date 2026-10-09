"""Unreal and UT import (tools/import_unreal.py): package tables, LodMesh, palette textures and class default skins."""
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from app import app
from tools.import_unreal import fire_pixels, import_catalog, slot_texture

NAMES = ['None', 'Core', 'Engine', 'Class', 'Package', 'Texture', 'Palette', 'LodMesh', 'Test', 'Skins', 'Pal', 'Own',
         'Given', 'Box', 'BoxDeco', 'Mesh', 'Skin', 'bMasked', 'System', 'DefaultSkinName', 'Body2', 'SkeletalMesh']


def index(value):
    """A compact index."""
    sign, value = (0x80 if value < 0 else 0), abs(value)
    out = bytearray([sign | (value & 0x3f) | (0x40 if value >= 0x40 else 0)])
    value >>= 6
    while value:
        out.append((value & 0x7f) | (0x80 if value >= 0x80 else 0))
        value >>= 7
    return bytes(out)


def name(text):
    return index(NAMES.index(text))


def props(*tags):
    """Tagged properties: (name, type, data) with an object, str or bool value; bools carry theirs in 0x80."""
    out = b''
    for prop, kind, data in tags:
        if kind == 13:  # str: size code 5 with a one-byte size; a compact length, the text and a NUL
            data = index(len(data) + 1) + data.encode() + b'\0'
            out += name(prop) + bytes([0x50 | kind, len(data)]) + data
        elif kind == 3:  # bool: size code 5 with a zero size byte, value in the array bit
            out += name(prop) + bytes([0x53 | (0x80 if data else 0), 0])
        else:  # object: size code 0, a one-byte compact reference
            out += name(prop) + bytes([kind]) + data
    return out + name('None')


def texture(palette, pixels, masked):
    body = props(('Palette', 5, index(palette)), *([('bMasked', 3, True)] if masked else []))
    return body + bytes([1]) + struct.pack('<I', 0) + index(len(pixels)) + pixels + struct.pack('<IIBB', 2, 2, 1, 1)


def lod_mesh(textures):
    """A two-face mesh: face 0 (slot 0, plain) winds clockwise seen from the front (+x) at x = 10; face 1 (slot 1,
    two-sided and masked) behind it. One frame, no rotation, scale 1."""
    pack = lambda x, y, z: (x & 0x7ff) | (y & 0x7ff) << 11 | (z & 0x3ff) << 22
    verts = [pack(10, 0, 10), pack(10, -10, 0), pack(10, 10, 0), pack(-10, 0, 10)]
    lazy = lambda count, data: struct.pack('<I', 0) + index(count) + data
    out = name('None') + struct.pack('<6fB4f', *[0] * 11)
    out += lazy(4, b''.join(struct.pack('<i', v - (1 << 32) * (v >> 31)) for v in verts)) + lazy(0, b'') + index(0)
    out += lazy(0, b'') + struct.pack('<6fB4f', *[0] * 11) + lazy(0, b'')
    out += index(len(textures)) + b''.join(index(t) for t in textures) + index(0) + index(0)
    out += struct.pack('<iiII3f3f3iII', 4, 1, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0) + index(0)
    out += index(0) + index(0) + index(2) + struct.pack('<8H', 0, 1, 2, 0, 3, 1, 2, 1)
    out += index(0) + index(4) + b''.join(struct.pack('<HBB', v, 255 * (v & 1), 0) for v in range(4))
    out += index(2) + struct.pack('<IiIi', 0, 0, 0x102, 1) + index(0) + struct.pack('<II', 4, 0) + b'\0' * 29
    return out


def class_defaults(mesh, skin, skin_name=None):
    """Class BoxDeco: no bytecode; defaults Mesh, Skin and (as UT's players have) DefaultSkinName."""
    out = index(0) * 4 + name('BoxDeco') + struct.pack('<III', 0, 0, 0) + b'\0' * 22 + b'\0' * 20
    out += index(0) + index(0) + index(0) + name('System')  # dependencies, imports, within, config
    return out + props(('Mesh', 5, index(mesh)), ('Skin', 5, index(skin)),
                       *([('DefaultSkinName', 13, skin_name)] if skin_name else []))


def package(skin_name=None, skeletal=False):
    """Test.u: Skins.Pal (palette), Skins.Own (masked) and Skins.Given (textures), Box (LodMesh with slot 1 empty) and
    BoxDeco (a class showing Box with Skin Given); with `skin_name`, BoxDeco's DefaultSkinName, and a texture Body2;
    `skeletal` makes Box a SkeletalMesh with the same corners as float reference points."""
    imports = [('Core', 'Package', 0, 'Engine'), ('Core', 'Class', -1, 'Texture'), ('Core', 'Class', -1, 'Palette'),
               ('Core', 'Class', -1, 'SkeletalMesh' if skeletal else 'LodMesh')]
    box = lod_mesh([3, 0])
    if skeletal:  # no float wedges, then the points (bones and weights are not read)
        box += index(0) + index(4) + struct.pack('<12f', 10, 0, 10, 10, -10, 0, 10, 10, 0, -10, 0, 10)
    palette = name('None') + index(256) + bytes(c for i in range(256) for c in (i, 255 - i, 7, 0))
    exports = [(0, 0, 'Skins', b''), (-3, 1, 'Pal', palette), (-2, 1, 'Own', texture(2, bytes([0, 1, 2, 3]), True)),
               (-2, 1, 'Given', texture(2, bytes([9, 9, 9, 9]), False)), (-4, 0, 'Box', box),
               (0, 0, 'BoxDeco', class_defaults(5, 4, skin_name))]
    if skin_name:
        exports.append((-2, 0, 'Body2', texture(2, bytes([5, 5, 5, 5]), False)))
    names = b''.join(index(len(n) + 1) + n.encode() + b'\0' + struct.pack('<I', 0) for n in NAMES)
    import_table = b''.join(name(a) + name(b) + struct.pack('<i', outer) + name(c) for a, b, outer, c in imports)
    at = 40 + len(names) + len(import_table)
    bodies, export_table = b'', b''
    for cls, outer, title, body in exports:
        export_table += index(cls) + index(0) + struct.pack('<i', outer) + name(title) + struct.pack('<i', 0x70004)
        export_table += index(len(body)) + (index(at + len(bodies)) if body else b'')
        bodies += body
    # Offsets as compact indexes change the table's length; bodies sit before it, so they are already placed.
    head = struct.pack('<IHHI6I', 0x9E2A83C1, 69, 0, 0, len(NAMES), 40, len(exports), at + len(bodies), len(imports),
                       40 + len(names))
    return head + b'\0' * 4 + names + import_table + bodies + export_table  # 4 bytes of the GUID pad to 40


class UnrealImportTest(unittest.TestCase):
    def test_mesh_textures_and_class_skin(self):
        with tempfile.TemporaryDirectory() as folder:
            install, output = Path(folder) / 'Unreal', Path(folder) / 'out'
            (install / 'System').mkdir(parents=True)
            (install / 'System' / 'UnrealShare.u').write_bytes(package())
            self.assertEqual(import_catalog(install, output), {'entries': 1, 'ready': 1})
            data = json.loads((output / 'model_json' / 'box.json').read_text())
            self.assertEqual(data['material_textures'], ['unrealshare.skins.given.png', 'unrealshare.skins.own.png'])
            self.assertEqual(data['material_settings'], [{'cull': 'none', 'alphaFunc': 'GE128'}, {}])
            # Face 0 (slot 0, Own) faces the front, the viewer's +z, anticlockwise.
            group = data['groups'][1]
            a, b, c = (data['vertices'][3 * i:3 * i + 3] for i in data['indices'][group['start']:group['start'] + 3])
            normal_z = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            self.assertGreater(normal_z, 0)
            self.assertTrue(all(abs(v[2] - 10 / 52.5) < 1e-3 for v in (a, b, c)))
            with Image.open(output / 'textures' / 'unrealshare.skins.own.png') as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 255, 7, 0))  # masked: index 0 clear
                self.assertEqual(image.getpixel((1, 0)), (1, 254, 7, 255))
            with patch.dict('app.pack_dirs', unreal=output):
                client = app.test_client()
                self.assertEqual(len(client.get('/list_models?game=unreal').json), 1)
                self.assertEqual(client.get('/texture/unrealshare.skins.own.png?game=unreal').status_code, 200)
                self.assertEqual(client.get('/export_glb/box?game=unreal').status_code, 200)

    def test_ut_player_skins_from_default_skin_name(self):
        # UT's players name their skins: slot 1 takes Body2 from DefaultSkinName "Botpack.Body" over the class Skin.
        with tempfile.TemporaryDirectory() as folder:
            install, output = Path(folder) / 'UnrealTournament', Path(folder) / 'out'
            (install / 'System').mkdir(parents=True)
            (install / 'System' / 'Botpack.u').write_bytes(package('Botpack.Body'))
            self.assertEqual(import_catalog(install, output, 'ut'), {'entries': 1, 'ready': 1})
            data = json.loads((output / 'model_json' / 'box.json').read_text())
            self.assertEqual(data['material_textures'], ['botpack.body2.png', 'botpack.skins.own.png'])
            self.assertEqual(json.loads((output / 'catalog.json').read_text())[0]['category'], 'Unreal Tournament')
            with patch.dict('app.pack_dirs', ut=output):
                client = app.test_client()
                self.assertEqual(len(client.get('/list_models?game=ut').json), 1)
                self.assertEqual(client.get('/export_glb/box?game=ut').status_code, 200)

    def test_skeletal_mesh_reference_pose(self):
        # UT's SkeletalChars: a SkeletalMesh draws its float reference points as a LodMesh draws its packed vertices.
        with tempfile.TemporaryDirectory() as folder:
            boxes = []
            for skeletal in (False, True):
                install, output = Path(folder) / f'ut{skeletal}', Path(folder) / f'out{skeletal}'
                (install / 'System').mkdir(parents=True)
                (install / 'System' / 'Botpack.u').write_bytes(package(skeletal=skeletal))
                self.assertEqual(import_catalog(install, output, 'ut'), {'entries': 1, 'ready': 1})
                boxes.append(json.loads((output / 'model_json' / 'box.json').read_text()))
            self.assertEqual(boxes[1]['vertices'], boxes[0]['vertices'])
            self.assertEqual(boxes[1]['material_textures'], boxes[0]['material_textures'])

    def test_empty_slot_takes_the_nearest_filled_slot_below(self):
        # UT's Bin2: its faces use empty slot 2 and the game draws slot 1's texture; slot 0 has nothing below.
        package = SimpleNamespace(ref_path=lambda ref: ('Botpack', 'bin2M'), exports=[{'name': 'bin2M'}])
        mesh = {'textures': [0, 7, 0]}
        self.assertEqual(slot_texture(None, {}, {}, package, 1, mesh, 2), (package, 7))
        self.assertIsNone(slot_texture(None, {}, {}, package, 1, mesh, 0))

    def test_fire_rises_from_its_spark(self):
        # One sparkle at the bottom middle of a 16 x 16 rising fire: heat above it, none below the bottom rows.
        pixels = fire_pixels({'RenderHeat': 240, 'bRising': True}, [(1, 255, 8, 14, 0, 0, 0, 0)], 16, 16)
        rows = [sum(pixels[y * 16:(y + 1) * 16]) for y in range(16)]
        self.assertGreater(rows[10], 0)
        self.assertGreater(rows[13], rows[4])
        self.assertEqual(len(pixels), 256)


if __name__ == '__main__':
    unittest.main()

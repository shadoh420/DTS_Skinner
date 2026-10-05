"""Diabotical map import (tools/import_diabotical_map.py) and serving boundaries, and the page's JavaScript tests
(tests/diabotical_maps.test.cjs) when Node is installed. Set DIABOTICAL_GAME_BASE to a Diabotical folder to also
import that install."""
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app import app
from tools.fbx_mesh import fbx_mesh
from tools.import_diabotical_map import OUT, import_maps, map_id, placements, read_billboards, read_map, read_pfx, visible_blocks

RECORD = {21: 46, 24: 46, 25: 52, 26: 53, 27: 53}


def text(value):
    return struct.pack('<I', len(value)) + value.encode()


def rbe(blocks, materials=('default', 'stone', 'stone:2'), version=27, author='Someone', entities=()):
    """A .rbe map as the game writes one: blocks are (x, y, z, shape, turn, six face materials), entities (name,
    position, rotation, scale, fields)."""
    size, turn_at = RECORD.get(version, 53), 44 if version <= 24 else 50
    body = bytes([len(materials) + 1]) + b''.join(text(name) for name in (*materials, ''))
    body += struct.pack('<I', len(blocks))
    for x, y, z, shape, turn, faces in blocks:
        record = bytearray(size)
        struct.pack_into('<3i', record, 0, x, y, z)
        record[12], record[turn_at], record[25:31] = shape, turn, bytes(faces)
        body += bytes(record)
    body += struct.pack('<I', 2) + bytes(32)  # A grid of 16-byte cells, not read.
    body += struct.pack('<I', len(entities))
    for name, position, rotation, scale, fields in entities:
        body += text(name) + struct.pack('<9fI', *position, *rotation, *scale, len(fields)) + b''.join(text(k) + text(v) for k, v in fields.items())
    body += b'\0' * 64  # The map's other parts, which the import does not read.
    head = b'REBM' + struct.pack('<III', version, 0x12345678, 0)
    if version > 21:  # Version 21 has no author.
        head += struct.pack('<I', len(author)) + author.encode() + bytes(8)
    return head + (gzip.compress(body) if version >= 24 else body)


def dbp(files):
    """A .dbp pack holding {backslash path: bytes}."""
    listing, data = b'', b''
    for name, raw in files.items():
        listing += struct.pack('<I', len(name)) + name.encode() + struct.pack('<II', len(data), len(raw))
        data += raw
    return b'DBP1' + struct.pack('<II', 0, len(files)) + listing + data


def dds(colour):
    from PIL import Image
    output = io.BytesIO()
    Image.new('RGB', (8, 8), colour).save(output, 'DDS', pixel_format='DXT1')
    return output.getvalue()


def fbx(nodes):
    """A binary FBX (version 7400) of nodes (name, [values], [children]); values are int, float, str or numpy arrays."""
    def node(at, name, values, children):
        props = b''
        for value in values:
            if isinstance(value, str):
                props += b'S' + text(value)
            elif isinstance(value, float):
                props += b'D' + struct.pack('<d', value)
            elif isinstance(value, int):
                props += b'L' + struct.pack('<q', value)
            else:
                raw = value.tobytes()
                props += {np.dtype('<f8'): b'd', np.dtype('<i4'): b'i'}[value.dtype] + struct.pack('<III', len(value), 0, len(raw)) + raw
        inner, start = b'', at + 13 + len(name) + len(props)
        for c in children:
            inner += node(start + len(inner), *c)
        if children:
            inner += bytes(13)
        return struct.pack('<IIIB', at + 13 + len(name) + len(props) + len(inner), len(values), len(props), len(name)) + name.encode() + props + inner
    out = b'Kaydara FBX Binary  \x00\x1a\x00' + struct.pack('<I', 7400)
    for n in nodes:
        out += node(len(out), *n)
    return out + bytes(13)


def quad_fbx():
    """One square of side 10 in material 'skin', its model moved 5 along x: two triangles."""
    p70 = lambda *entries: ('Properties70', [], [('P', [k, '', '', '', *v], []) for k, *v in entries])
    geometry = ('Geometry', [1, 'quad\x00\x01Geometry', 'Mesh'], [
        ('Vertices', [np.array([0, 0, 0, 10, 0, 0, 10, 10, 0, 0, 10, 0], '<f8')], []),
        ('PolygonVertexIndex', [np.array([0, 1, 2, -4], '<i4')], []),
        ('LayerElementUV', [0], [('MappingInformationType', ['ByPolygonVertex'], []), ('ReferenceInformationType', ['Direct'], []),
                                 ('UV', [np.array([0, 0, 1, 0, 1, 1, 0, 1], '<f8')], [])])])
    model = ('Model', [2, 'quad\x00\x01Model', 'Mesh'], [p70(('Lcl Translation', 5.0, 0.0, 0.0))])
    material = ('Material', [3, 'skin\x00\x01Material', ''], [])
    links = [('C', ['OO', 2, 0], []), ('C', ['OO', 1, 2], []), ('C', ['OO', 3, 2], [])]
    return fbx([('Objects', [], [geometry, model, material]), ('Connections', [], links)])


def install(root, maps):
    (root / 'packs').mkdir(parents=True)
    (root / 'packs/maps.dbp').write_bytes(dbp({f'maps\\{name}.rbe': raw for name, raw in maps.items()} | {'maps\\walk-b.png': b'png'}))
    (root / 'packs/scripts.dbp').write_bytes(dbp({
        'scripts\\walk.assets': b'asset stone\n{\n  type surface_material\n  material stone_floor\n}\n// asset gone { type surface_material material gone }\n',
        'scripts\\walk.shader': b'stone_floor\n{\n {\n\t\tmap textures/walk/not_in_packs_d.png\n\t\tuv_scale 0.5\n }\n}\n// default { { map x } }\n',
    }))
    # Found under its file name beside the shader file, not where the shader says.
    (root / 'packs/textures.dbp').write_bytes(dbp({
        'textures\\walk\\colours\\walk.shader': b'stone_floor\n{\n {\n\t\tmap textures/walk/stone_d.png\n\t\tmap textures/flat_normal.png\n\t\tuv_scale 0.125\n }\n}\n',
        'textures\\walk\\colours\\stone_d.png.dds': dds((200, 100, 50)),
        # A terrain material: maps 0, 3 and 5 are its ground, cliff and dirt.
        'textures\\ter.shader': b'core_ter\n{\n {\n\t\tmap textures/ter_d.png\n\t\tmap n.png\n\t\tmap black.png\n\t\tmap textures/cliff_d.png\n'
                                b'\t\tmap n.png\n\t\tmap textures/ter_d.png\n\t\tpixel_shader tileter.ps.cso\n }\n}\n',
        'textures\\ter_d.png.dds': dds((0, 120, 0)),
        'textures\\cliff_d.png.dds': dds((90, 90, 90)),
    }))
    (root / 'packs/zz_late.dbp').write_bytes(dbp({
        'zz\\walk.shader': b'stone_floor\n{\n {\n\t\tmap textures/late_d.png\n\t\tuv_scale 0.75\n }\n}\n',
        'textures\\late_d.png.dds': dds((0, 0, 255)),
    }))
    (root / 'packs/models_props.dbp').write_bytes(dbp({
        'models\\props\\sub\\quad.fbx': quad_fbx(),
        'models\\props\\sub\\quad.assets': (b'asset quad_prop\n{\n  model props/sub/quad\n  material stone_floor\n}\n'
                                             b'asset quad_strip\n{\n  dynamic true\n  dynamic_rule\n  {\n    select props/sub/quad\n  }\n'
                                             b'  dynamic_rule\n  {\n    if offset_right is 0\n    select props/sub/quad_flipx serial_rand\n  }\n}\n'),
        # The model's own material is the one named most like it in the nearest .shader at or above its folder.
        'models\\props\\props.shader': (b'props/other\n{\n {\n\t\tmap models/props/quad_d.png\n }\n}\n'
                                         b'props/sub/quad_mat\n{\n {\n\t\tmap models/props/quad_d.png\n\t\tculling off\n }\n}\n'
                                         # Tinted: map 4 is its colour mask, accent1 its own colour.
                                         b'props/tinted\n{\n {\n\t\tmap models/props/quad_d.png\n\t\tmap n.png\n\t\tmap s.png\n\t\tmap id.png\n'
                                         b'\t\tmap models/props/quad_d.png\n\t\tpixel_shader tilemask.ps.cso\n\t\tpixel_shader_param accent1 FF0000\n }\n}\n'
                                         # A decal's texture keeps its alpha.
                                         b'arrow\n{\n {\n\t\tmap models/props/quad_d.png\n\t\tpixel_shader tiledecal.ps.cso\n }\n}\n'),
        'models\\props\\quad_d.png.dds': dds((10, 200, 10)),
        # Its specular map (map 2: red gloss, green strength) is even, so a constant; its id map's red, the material id.
        'models\\props\\s.png.dds': dds((0, 255, 0)),
        'models\\props\\id.png.dds': dds((255, 0, 0)),
    }))
    (root / 'packs/audio.dbp').write_bytes(b'not a pack: audio packs are not read')


CUBE = (1, 1, 1, 1, 2, 0)


class DiaboticalMapsTest(unittest.TestCase):
    def test_map_records_by_version(self):
        for version in (21, 24, 25, 26, 27):
            parsed = read_map(rbe([(3, -2, 5, 3, 2, (1, 2, 1, 2, 0, 1))], version=version))
            self.assertEqual((parsed['version'], parsed['author'], parsed['materials']),
                             (version, '' if version == 21 else 'Someone', ['default', 'stone', 'stone:2', '']))
            blocks = parsed['blocks']
            self.assertEqual((blocks['xyz'].tolist(), blocks['shape'].tolist(), blocks['turn'].tolist(), blocks['faces'].tolist()),
                             ([[3, -2, 5]], [3], [2], [[1, 2, 1, 2, 0, 1]]))
        with self.assertRaisesRegex(ValueError, 'version 20 is not read yet'):
            read_map(rbe([], version=20))
        with self.assertRaisesRegex(ValueError, 'not a Diabotical map'):
            read_map(b'RIFF' + bytes(40))

    def test_faces_between_cubes_close_and_buried_cubes_are_left_out(self):
        solid = [(x, y, z, 1, 0, CUBE) for x in range(3) for y in range(3) for z in range(3)]
        blocks = visible_blocks(read_map(rbe(solid + [(5, 0, 0, 3, 1, CUBE), (7, 0, 0, 2, 0, CUBE)]))['blocks'])
        self.assertEqual(blocks.dtype, OUT)
        self.assertEqual(OUT.itemsize, 16)
        at = {(int(b['x']), int(b['y']), int(b['z'])): b for b in blocks}
        self.assertNotIn((1, 1, 1), at)  # Closed on every side.
        self.assertNotIn((7, 0, 0), at)  # Draws nothing.
        self.assertEqual(len(blocks), 26 + 1)
        # Faces +z, -x, -z, +x, top, bottom: the corner (0, 0, 0) is open toward -x, -z and below.
        self.assertEqual(at[(0, 0, 0)]['open'], 0b100110)
        self.assertEqual(at[(1, 2, 1)]['open'], 0b010000)
        self.assertEqual((at[(5, 0, 0)]['open'], at[(5, 0, 0)]['turn'], at[(5, 0, 0)]['shape']), (0x3f, 1, 3))

    def test_import_writes_blocks_index_and_material_textures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            props = [('prop_a', (0, 40, 0), (0, 0, 0), (1, 1, 1), {'model': 'quad_prop'}), ('prop_b', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'model': 'props/sub/quad'}),
                     ('prop_c', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'model': 'props/gone'}), ('hpt1', (5, 6, 7), (0, 0, 0), (1, 1, 1), {}),
                     ('prop_d', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'model': 'props/sub/quad', 'material': 'props/tinted', 'color2': '336699'}),
                     ('decal_arrow', (0, 0, 0), (0, 0, 0), (40, 40, 10), {'material': 'arrow'})]
            install(root / 'game', {'walk': rbe([(0, 0, 0, 1, 0, CUBE), (1, 0, 0, 1, 0, (2, 2, 2, 2, 0, 0))], entities=props), 'old menu': rbe([], version=20)})
            mine = root / 'Mine.rbe'
            mine.write_bytes(rbe([(0, 0, 0, 3, 0, CUBE)], materials=('default', 'gone'),
                                 entities=[('terrain', (500, 0, 0), (0, 0, 0), (1, 1, 1), {'offset_y': '-1070'})]))
            from PIL import Image
            heights = Image.new('RGBA', (4, 4), (0, 0, 0, 255))
            heights.putpixel((3, 1), (134, 0, 0, 255))
            heights.save(root / 'Mine-h.png')
            mask = Image.new('RGBA', (4, 4), (0, 0, 0, 255))
            mask.putpixel((2, 2), (255, 0, 0, 255))
            mask.save(root / 'Mine-b.png')
            result = import_maps(root / 'game', root / 'pack', extra=[mine])
            self.assertEqual((result['imported'], result['skipped']), (['walk', 'Mine'], []))
            self.assertEqual(result['failed'], {'old menu': 'map version 20 is not read yet'})
            self.assertEqual(result['untextured'], ['default', 'gone'])
            index = json.loads((root / 'pack/index.json').read_text())
            self.assertEqual([(item['id'], item['group'], item['blocks']) for item in index], [('walk', 'Diabotical', 2), ('user__mine', 'Your maps', 1)])
            blocks = np.frombuffer((root / 'pack/maps' / index[0]['file']).read_bytes(), OUT)
            self.assertEqual(blocks['open'].tolist(), [0b110111, 0b111101])
            materials = json.loads((root / 'pack/materials.json').read_text())
            # stone is stone_floor's asset; of its three definitions the first whose texture is found is used, as
            # the game does, and stone:2 is a variant of it.
            self.assertEqual(materials['stone']['scale'], .125)
            self.assertEqual(materials['stone:2'], materials['stone'])
            with Image.open(root / 'pack/textures' / materials['stone']['texture']) as image:
                self.assertLess(max(abs(a - b) for a, b in zip(image.getpixel((4, 4)), (200, 100, 50))), 8)  # DXT1 rounds to 5:6:5.
            # Props: the model converted once, its own material found in the .shader above it (two-sided, cut out).
            self.assertEqual(result['unconverted'], ['props/gone'])
            models = json.loads((root / 'pack/models.json').read_text())
            self.assertEqual(list(models), ['props/sub/quad'])
            self.assertEqual(models['props/sub/quad']['groups'], [['props/sub/quad_mat', 6]])
            self.assertEqual(len((root / 'pack/models' / models['props/sub/quad']['file']).read_bytes()), 6 * 8 * 4)
            self.assertEqual((materials['props/sub/quad_mat']['cutout'], materials['props/sub/quad_mat']['texture'][-6:]), (True, '-a.png'))
            raw = (root / 'pack/maps' / index[0]['entities']).read_bytes()
            length, = struct.unpack_from('<I', raw)
            head = json.loads(raw[4:4 + length])
            self.assertEqual(head['props'], [['props/sub/quad|stone_floor|', 1, 0], ['props/sub/quad||', 1, 0], ['props/gone||', 1, 0],
                                             ['props/sub/quad|props/tinted|', 1, 1]])
            self.assertEqual(np.frombuffer(raw[-12 - 108:-108], '<u4').tolist(), [0, 0x1336699, 0])  # The tinted key's tints.
            # Then the decals: their box matrices, then colour, flags and order each.
            self.assertEqual(head['decals'], [['arrow', 1]])
            self.assertEqual(np.frombuffer(raw[-108:-12], '<f4')[[0, 5, 10, 12, 17, 22]].tolist(), [40, 40, 10] * 2)
            self.assertEqual(np.frombuffer(raw[-12:], '<u4').tolist(), [0xffffffff, 0, 0])
            self.assertEqual(materials['arrow']['texture'][-6:], '-a.png')
            self.assertEqual(materials['props/tinted']['accents'], [0xff0000, None, None])
            self.assertIn('texture', materials['props/tinted#4'])
            self.assertEqual((materials['props/tinted']['spec'], materials['props/tinted']['id']), ([0, 1], 255))
            self.assertFalse({'spec', 'id'} & (set(materials['stone']) | set(materials['arrow']) | set(materials['props/tinted#4'])))
            self.assertIsNone(head['lights']['envmap'])  # No textures_cubemaps.dbp here.
            self.assertEqual(head['markers'], [['hpt', 5, 6, 7]])
            self.assertEqual(np.frombuffer(raw, '<f4', 12, 4 + length)[[3, 7, 11]].tolist(), [0, 40, 0])
            # Terrain: heights in red and dirt in green (a texel shared by the four vertices at its corners); its
            # material's cliff (#3) and dirt (#5) textures.
            terrain = index[1]['terrain']
            self.assertEqual({key: value for key, value in terrain.items() if key != 'file'},
                             dict(offset=[0, -1070, 0], cell=40, scale=1, material='core_ter', no_decals=False))
            with Image.open(root / 'pack/maps' / terrain['file']) as image:
                self.assertEqual((image.size, image.getpixel((3, 1))), ((4, 4), (134, 0, 0)))
                self.assertEqual([image.getpixel(at)[1] for at in ((2, 2), (3, 2), (2, 3), (3, 3), (1, 2), (2, 1))], [64, 64, 64, 64, 0, 0])
            self.assertIsNone(index[0]['terrain'])
            self.assertEqual(materials['core_ter#5']['texture'], materials['core_ter']['texture'])
            self.assertNotEqual(materials['core_ter#3']['texture'], materials['core_ter']['texture'])
            again = import_maps(root / 'game', root / 'pack', extra=[mine])
            self.assertEqual((again['imported'], again['skipped']), ([], ['walk', 'Mine']))
            mine.write_bytes(rbe([(0, 0, 0, 1, 0, CUBE)]))
            again = import_maps(root / 'game', root / 'pack', extra=[mine])
            self.assertEqual(again['imported'], ['Mine'])
            self.assertEqual(sorted(path.name.split('-')[0] for path in (root / 'pack/maps').iterdir()), ['user__mine', 'user__mine', 'walk', 'walk'])  # Blocks and entities, the old ones gone.
            import_maps(root / 'game', root / 'pack', extra=[])  # Deleted from the editor's folder.
            self.assertEqual([item['id'] for item in json.loads((root / 'pack/index.json').read_text())], ['walk'])
            self.assertEqual([path.name.split('-')[0] for path in sorted((root / 'pack/maps').iterdir())], ['walk', 'walk'])
            with self.assertRaisesRegex(ValueError, 'Enter the Diabotical folder'):
                import_maps(root, root / 'pack')

    def test_fbx_mesh_triangulates_and_moves_by_its_model(self):
        groups = fbx_mesh(quad_fbx())
        self.assertEqual(list(groups), ['skin'])
        positions, normals, uvs = groups['skin']
        self.assertEqual(positions.shape, (2, 3, 3))
        self.assertEqual((positions[..., 0].min(), positions[..., 0].max()), (5, 15))
        self.assertEqual(uvs[0].tolist(), [[0, 0], [1, 0], [1, 1]])
        with self.assertRaisesRegex(ValueError, 'not a binary FBX'):
            fbx_mesh(b'; FBX 7.4.0 project file')

    def test_entities_become_props_markers_and_liquids(self):
        entities = [
            ('prop_a', (40, 20, 80), (0, 0, 0), (2, 2, 2), {'model': 'quad_prop'}),
            ('prop_b', (0, 0, 0), (0, 0, 0), (3, 1, 1), {'model': 'quad_strip', 'unique': '1', 'color': '#FF8000', 'color3': 'accent2'}),
            ('prop_c', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'model': 'props/quad', 'no_show': '1'}),
            ('spawn_2', (1, 2, 3), (0, 1, 0), (1, 1, 1), {}),
            ('liquid_ocean', (0, -50, 0), (0, 0, 0), (1000, 100, 1000), {'material': 'core_ocean'}),
            ('global', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'accent2': '00ff7f'}),
            ('decal_arrow', (10, 20, 30), (0, 0, 0), (100, 50, 8), {'material': 'Arrow', 'color': '80402010', 'mirrored': 'true', 'v3': 'true', 'order': '01000'}),
            ('decal_arrow_1', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'material': 'arrow', 'color': 'accent2', 'v2': 'true', 'order': '-2'}),
            ('decal_none', (0, 0, 0), (0, 0, 0), (1, 1, 1), {}),
        ]
        parsed = read_map(rbe([], entities=entities))
        self.assertEqual([e[0] for e in parsed['entities']], [e[0] for e in entities])
        self.assertEqual(parsed['entities'][1][4], {'model': 'quad_strip', 'unique': '1', 'color': '#FF8000', 'color3': 'accent2'})
        assets = {'quad_prop': {'model': 'props/quad', 'material': 'stone_floor', 'rules': []},
                  'quad_strip': {'dynamic': 'true', 'rules': [(0, [], ['props/quad']), (0, ['offset_right is 0'], ['props/quad_flipx'])]}}
        props, tints, markers, liquids, decals = placements(parsed['entities'], assets)
        self.assertEqual({key: len(value) for key, value in props.items()}, {'props/quad|stone_floor|': 1, 'props/quad||': 2, 'props/quad||m': 1})
        # Tints: color, color2, color3 as 0x1RRGGBB, 0 unset; accentN is the global entity's. Only tinted keys are listed.
        self.assertEqual(sorted(tints), ['props/quad||', 'props/quad||m'])
        self.assertEqual(tints['props/quad||m'].tolist(), [[0x1ff8000, 0, 0x100ff7f]])
        # Page axes: the game's z negated. A static prop keeps its scale; a dynamic one is 40-unit cells from its corner.
        static = props['props/quad|stone_floor|'][0].reshape(3, 4)
        self.assertEqual(static[:, 3].tolist(), [40, 20, -80])
        self.assertAlmostEqual(np.linalg.det(static[:, :3]), 8, 4)
        self.assertEqual(sorted(m[3] for key in ('props/quad||', 'props/quad||m') for m in props[key]), [20, 60, 100])
        self.assertLess(np.linalg.det(props['props/quad||m'][0].reshape(3, 4)[:, :3]), 0)  # _flipx: mirrored.
        # A prop's material field X is the shader MODEL_X where there is one (bioplant's door frames), else X.
        framed = [('prop_d', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'model': 'props/quad', 'material': 'Frame_Red'}),
                  ('prop_e', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'model': 'props/quad', 'material': 'stone_floor'}),
                  ('prop_f', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'model': 'props/quad', 'no_decals': '1'})]
        self.assertEqual(sorted(placements(framed, {}, {'props/quad_frame_red'})[0]), ['props/quad|props/quad_frame_red|', 'props/quad|stone_floor|',
                                                                                     'props/quad||n'])
        # Billboards: a unit square scaled x by y, placed as a prop (page axes), colour, texture path with / , reflection.
        panes = [('billboard_1', (10, 20, 30), (0, 0, 0), (100, 50, 1), {'color': 'accent2', 'texture': 'Textures\Decals\Glow.png', 'reflection': 'on'}),
                 ('billboard_2', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'no_show': 'true'})]
        self.assertEqual(read_billboards(panes + [('global', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'accent2': '00ff7f'})]),
                         [[100, 0, 0, 10, 0, 50, 0, 20, 0, 0, 1, -30, 0x00ff7f, 'textures/decals/glow.png', 1]])
        # Particle emitters: their place and turn (page axes), system, colour, size.
        self.assertEqual(read_pfx([('pfx_1', (1, 2, 3), (0, 0, 0), (1, 1, 1), {'system': 'Fire', 'color': 'ff0000', 'size': '2'}),
                                   ('pfx_2', (0, 0, 0), (0, 0, 0), (1, 1, 1), {})]),
                         [[1, 0, 0, 1, 0, 1, 0, 2, 0, 0, 1, -3, 'fire', 0xff0000, 2.0]])
        self.assertEqual(markers, [['spawn', 1, 2, 3]])
        self.assertEqual(liquids, [[0, -50, 0, 1000, 100, 1000, 'core_ocean', None, None, 1]])  # Colour, alpha (AARRGGBB), ocean.
        # Decals: their boxes in page axes; colour 0xRRGGBBAA (from RRGGBB, or AARRGGBB: alpha first), flags (1 mirrored,
        # 2 v2, 4 v3), order.
        matrices, extras = decals['arrow']
        self.assertEqual(list(decals), ['arrow'])
        self.assertEqual(matrices[0].reshape(6, 4).tolist(), [[100, 0, 0, 10], [0, 50, 0, 20], [0, 0, 8, -30]] * 2)
        self.assertEqual(extras.view(np.int32).tolist(), [[0x40201080, 5, 1000], [0x00ff7fff, 2, -2]])

    def test_decal_boxes_turn_their_texture_as_the_game_does(self):
        from tools.import_diabotical_map import decal_matrix
        quarter = np.pi / 2
        columns = lambda m: [np.round(m[0][:3, i], 6).tolist() for i in range(3)]
        # A wall decal turned 90 degrees about y (run 13): texture right toward -z, top up, facing (local -z) -x.
        self.assertEqual(columns(decal_matrix((0, 10, -300), (0, quarter, 0), (1, 1, 1))), [[0, 0, -1], [0, 1, 0], [1, 0, 0]])
        # On a floor (rotated 90 degrees about x) the texture is turned round: right toward -x, top toward -z.
        self.assertEqual(columns(decal_matrix((0, 0, 0), (quarter, 0, 0), (1, 1, 1))), [[-1, 0, 0], [0, 0, -1], [0, -1, 0]])
        # Tilted 89 degrees it is not (run 14), nor a wall rolled 90 (its texture's right up); turned past 90 it is.
        self.assertEqual(columns(decal_matrix((0, 0, 0), (np.radians(89), 0, 0), (1, 1, 1)))[0], [1, 0, 0])
        self.assertEqual(columns(decal_matrix((0, 0, 0), (0, 2 * quarter, quarter), (1, 1, 1)))[:2], [[0, 1, 0], [1, 0, 0]])
        self.assertEqual(columns(decal_matrix((0, 0, 0), (2 * quarter, 0, 0), (1, 1, 1)))[:2], [[-1, 0, 0], [0, 1, 0]])
        # v3: |R| s, the rotation's entries as absolute values times the scale.
        flat = decal_matrix((0, 0, 0), (quarter, 0, 0), (120, 60, 40))
        self.assertEqual(np.round(np.linalg.norm(flat[0][:3, :3], axis=0), 6).tolist(), [120, 60, 40])
        turned = decal_matrix((0, 0, 0), (quarter, quarter, 0), (120, 60, 40), v3=True)
        self.assertEqual(np.round(np.linalg.norm(turned[0][:3, :3], axis=0), 6).tolist(), [60, 40, 120])
        rolled = decal_matrix((0, 0, 0), (0, 2 * quarter, quarter / 2), (40, 40, 30), v3=True)
        self.assertEqual(np.round(np.linalg.norm(rolled[0][:3, :3], axis=0), 2).tolist(), [56.57, 56.57, 30])
        # The box it is cut to: turned by the yaw the other way, not by the roll (run 15), same size and place.
        projected, cut = decal_matrix((1, 2, 3), (quarter, np.radians(30), np.radians(45)), (120, 60, 40))
        self.assertEqual(np.round(cut[:3, 0] / 120, 6).tolist(), np.round([np.cos(np.radians(30)), 0, np.sin(np.radians(30))], 6).tolist())
        self.assertEqual((cut[:3, 3].tolist(), np.round(np.linalg.norm(cut[:3, :3], axis=0), 6).tolist()), ([1, 2, 3], [120, 60, 40]))

    def test_lights_are_read_as_the_game_lit_them(self):
        from tools.import_diabotical_map import read_lights, POINT, SUN
        quarter = np.pi / 2
        out = read_lights([
            ('global', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'shadow_color': '404040', 'gloss': '0.5'}),
            ('light_ambient', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'type': 'ambient', 'color': '404040'}),
            ('light_sun', (0, 0, 0), (quarter, 0, 0), (1, 1, 1), {'type': 'sun', 'color': 'ff0000', 'intensity': '2'}),
            ('light_lamp', (10, 20, 30), (0, 0, 0), (1, 1, 1), {'color': '808080', 'radius': '200'}),  # No type, intensity, falloff.
            ('light_spot', (0, 0, 0), (quarter, 0, 0), (1, 1, 1), {'type': 'diffuse_spot', 'angle': '60', 'softness': '0.1', 'falloff': '.5', 'intensity': '1'}),
            ('light_tube', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'type': 'capsule', 'length': '200', 'radius': '100'}),
            ('light_node', (1, 2, 3), (0, 0, 0), (1, 1, 1), {'type': 'cubic_ambient_node', 'color': '000080', 'radius': '200', 'intensity': '20'}),
            ('light_fog', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'type': 'fog', 'color': 'ffffff'}),
            ('prop_lamp', (0, 0, 0), (0, 0, 0), (1, 1, 1), {'model': 'a'})])
        self.assertEqual(out['shadow_colour'], [.251, .251, .251])
        self.assertEqual(read_lights([])['shadow_colour'], [.325, .469, .519])  # The game's own, bluish (run 20).
        self.assertEqual(out['ambient'], out['shadow_ambient'])  # None of its own: the ambient.
        self.assertEqual(out['ambient'], [.251] * 3)  # Hex, linear.
        # The sun travels along its local +z (pitch 90: straight down), red x intensity x 0.262.
        self.assertEqual(np.round(out['sun'][:3], 6).tolist(), [0, -1, 0])
        self.assertEqual(out['sun'][3:], [round(2 * SUN, 4), 0, 0, round(1 / SUN, 4)])  # Then its specular scale.
        self.assertEqual((out['envmap'], out['gloss']), ('default_envmap', .5))
        lamp, spot, tube = out['lights']
        # Hex linear, intensity 4 by default, page z mirrored, falloff 0.33 by default.
        self.assertEqual(lamp[:4] + lamp[7:12], [0, 10, 20, -30, *[round(128 / 255 * 4 * POINT, 4)] * 3, 200, 66])
        self.assertEqual((spot[0], spot[12], spot[13], spot[11], np.round(spot[4:7], 6).tolist()), (1, round(np.cos(np.radians(30)), 4), .1, 100, [0, -1, 0]))
        self.assertEqual((tube[0], tube[14], np.round(tube[4:7], 6).tolist()), (2, 200, [0, 0, -1]))  # Local +z, mirrored.
        self.assertEqual(out['nodes'], [[1, 1, 2, -3, 200, 0, 0, round(128 / 255, 4)]])
        # A node above every block does nothing in game (run 23): the blocks' box drops it.
        node = [('light_node', (1, 450, 3), (0, 0, 0), (1, 1, 1), {'type': 'ambient_node'})]
        self.assertEqual([len(read_lights(node, box)['nodes']) for box in (None, ([0, 0, 0], [40, 400, 40]), ([0, 0, 0], [40, 600, 40]))], [1, 0, 1])
        # Diffuse lights (and those of no type) add no specular light; the others turn their colour back into
        # colour x intensity for it.
        self.assertEqual([lamp[15], spot[15], tube[15]], [0, 0, round(1 / POINT, 4)])

    def test_dynamic_rule_conditions(self):
        from tools.import_diabotical_map import rule_holds
        cell = dict(offset_left=2, offset_right=0, offset_bottom=4, size_x=3)
        self.assertTrue(all(rule_holds(c, cell) for c in ('offset_right is 0', 'offset_left == 2', 'offset_bottom % 2 0', 'size_x > 2',
                                                            'offset right % 3 0', 'offset_left 2', 'offset_bottom - offset_left 2')))
        self.assertFalse(any(rule_holds(c, cell) for c in ('offset_left is 0', 'offset_bottom % 3 0', 'size_x < 3', 'left empty',
                                                             'offset_front / offset_bottom 2', 'offset_top is 0')))

    def test_pickups_are_drawn_as_their_kinds_models(self):
        from tools.import_diabotical_map import pickup_kinds, placements
        assets = {'hpt1': {'model': 'Entities/Health/hpt1', 'pickup_size': 'large'}, 'weaponsw': {'pivot': '0 0 0', 'scale': '1.4', 'pickup_size': 'large'},
                  'weapongl': {'pickup_size': 'large'}, 'flag': {'pickup_size': 'large'}, 'chair': {'model': 'props/chair'}}
        kinds = pickup_kinds(assets, lambda model: model != 'weapongl')  # weapongl: an ASCII FBX.
        self.assertEqual(kinds, {'hpt1': ('entities/health/hpt1', 1.0, True), 'weaponsw': ('weaponsw', 0.56, False), 'flag': ('ctf_flag', 1.0, True),
                                 'coin': ('entities/coin/coin', 1.0, True)})
        entities = [('hpt1_2', (10, 20, 30), (0, 0, 0), (1, 1, 1), {}), ('weaponsw', (0, 0, 0), (0, 0, 0), (1, 1, 1), {}),
                    ('weapongl', (1, 2, 3), (0, 0, 0), (1, 1, 1), {})]
        props, _, markers, _, _ = placements(entities, assets, (), {kind: scale for kind, (_, scale, _) in kinds.items()})
        self.assertEqual(sorted(props), ['pickup/hpt1||', 'pickup/weaponsw||'])
        self.assertEqual(props['pickup/hpt1||'][0].tolist(), [1, 0, 0, 10, 0, 1, 0, 20, 0, 0, 1, -30])
        self.assertAlmostEqual(float(props['pickup/weaponsw||'][0][0]), 0.56, 5)
        self.assertEqual(markers, [['weapongl', 1, 2, 3]])  # No model it can read: still a marker.
        # Its asset's effect, offset in its own frame, joins the pfx emitters.
        from tools.import_diabotical_map import read_pfx
        glow = read_pfx([('armort2_1', (10, 20, 30), (0, 0, 0), (1, 1, 1), {}), ('hpt9', (0, 0, 0), (0, 0, 0), (1, 1, 1), {})],
                        {'armort2': {'pfx': 'Shield_01_green_vfx 0 7 0'}}, {'armort2': 1.0})
        self.assertEqual(glow, [[1, 0, 0, 10, 0, 1, 0, 27, 0, 0, 1, -30, 'shield_01_green_vfx', None, 1.0]])

    def test_dynamic_pieces_draw_with_their_channels_shader(self):
        from tools.import_diabotical_map import piece_shaders
        assets = {'bars': dict(dynamic='true', channels={}, rules=[(0, [], ['p/bars_mid']), (0, ['offset_top is 0'], ['p/bars_top_flipx']),
                                                                   (1, [], ['trim', 'p/trim_b'])]),
                  'pipe': dict(dynamic='true', channels={0: 'ofs_pipes_clean'}, rules=[(0, [], ['p/pipe_mid_1', 'p/pipe_mid_2'])]),
                  'scaffold': dict(dynamic='true', channels={}, rules=[(0, [], ['p/sc_bottom']), (0, [], ['p/sc_mid_flipz'])]),
                  'none': dict(dynamic='true', channels={}, rules=[(0, [], ['p/odd'])]),
                  'trim': dict(model='p/trim_a')}
        known = {'p/bars_mid', 'p/trim_a', 'ofs_pipes_clean', 'p/sc_mid_flipz'}
        self.assertEqual(piece_shaders(assets, known), {'p/bars_mid': 'p/bars_mid', 'p/bars_top': 'p/bars_mid', 'p/trim_a': 'p/trim_a',
                                                        'p/trim_b': 'p/trim_a', 'p/pipe_mid_1': 'ofs_pipes_clean', 'p/pipe_mid_2': 'ofs_pipes_clean',
                                                        'p/sc_bottom': 'p/sc_mid_flipz', 'p/sc_mid': 'p/sc_mid_flipz'})

    def test_map_id(self):
        self.assertEqual(map_id('duel_F1sks House'), 'duel_f1sks_house')

    def test_routes_are_local_and_confined_to_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'diabotical-maps/maps').mkdir(parents=True)
            (root / 'diabotical-maps/index.json').write_text('[]')
            (root / 'diabotical-maps/maps/walk-0123456789ab.bin').write_bytes(bytes(16))
            (root / 'secret.txt').write_text('outside pack')
            client = app.test_client()
            with patch('app.local_data_dir', root):
                with client.get('/maps/diabotical/') as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
                with client.get('/diabotical-map-data/index.json') as response:
                    self.assertEqual(response.json, [])
                    self.assertNotIn('immutable', response.headers.get('Cache-Control', ''))
                with client.get('/diabotical-map-data/maps/walk-0123456789ab.bin') as response:
                    self.assertIn('immutable', response.headers['Cache-Control'])
                with client.get('/diabotical-map-data/../secret.txt') as response:
                    self.assertEqual(response.status_code, 404)
                self.assertEqual(client.post('/import_diabotical_maps', json={'game': str(root)}, headers={'Origin': 'http://elsewhere.example'}).status_code, 403)
                self.assertEqual(client.post('/import_diabotical_maps', json={'game': ''}).status_code, 400)
                with client.post('/import_diabotical_maps', json={'game': str(root / 'absent')}) as response:
                    self.assertEqual(response.status_code, 422)
                    self.assertIn('Enter the Diabotical folder', response.json['error'])

    @unittest.skipUnless(shutil.which('node'), 'Node is not installed')
    def test_page_scripts(self):
        """Block meshing (static/diabotical-maps/blocks.js)."""
        test = Path(__file__).with_name('diabotical_maps.test.cjs')
        run = subprocess.run(['node', '--test', str(test)], capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stdout[-4000:] + run.stderr[-2000:])

    @unittest.skipUnless(os.environ.get('DIABOTICAL_GAME_BASE'), 'set DIABOTICAL_GAME_BASE to a Diabotical folder to import its maps')
    def test_install_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            report = import_maps(os.environ['DIABOTICAL_GAME_BASE'], Path(directory) / 'pack', extra=[])
            self.assertTrue(report['imported'])
            self.assertTrue(all('version 21' in reason for reason in report['failed'].values()), report['failed'])


if __name__ == '__main__':
    unittest.main()

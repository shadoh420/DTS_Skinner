import struct
import unittest
from collections import Counter

from tools import source_pcf as pcf, source_effects as fx


def dmx(elements):
    """A binary DMX v2 file: elements = [(type, name, [(attribute, type byte, packed value bytes)])]."""
    strings = []
    index = lambda s: strings.index(s) if s in strings else strings.append(s) or len(strings) - 1
    body = b''.join(struct.pack('<h', index(kind)) + name.encode() + b'\0' + bytes(16) for kind, name, _ in elements)
    for _, _, attributes in elements:
        body += struct.pack('<i', len(attributes))
        for name, kind, value in attributes:
            body += struct.pack('<hB', index(name), kind) + value
    table = struct.pack('<h', len(strings)) + b''.join(s.encode() + b'\0' for s in strings)
    return b'<!-- dmx encoding binary 2 format pcf 1 -->\n\0' + table + struct.pack('<i', len(elements)) + body


f = lambda v: struct.pack('<f', v)
i = lambda v: struct.pack('<i', v)
s = lambda v: v.encode() + b'\0'
array = lambda *refs: struct.pack('<i', len(refs)) + b''.join(i(r) for r in refs)


class PcfTest(unittest.TestCase):
    def fire(self):
        return dmx([
            ('DmElement', 'root', [('particleSystemDefinitions', 15, array(1, 6))]),
            ('DmeParticleSystemDefinition', 'env_fire_tiny', [('material', 5, s('particle\\fire.vmt')), ('max_particles', 2, i(20)),
                ('emitters', 15, array(2)), ('initializers', 15, array(3, 4)), ('operators', 15, array(5)), ('children', 15, array(7))]),
            ('DmeParticleOperator', 'emit_continuously', [('functionName', 5, s('emit_continuously')), ('emission_rate', 3, f(8))]),
            ('DmeParticleOperator', 'radius_random', [('functionName', 5, s('radius_random')), ('radius_min', 3, f(16)), ('radius_max', 3, f(20))]),
            ('DmeParticleOperator', 'color_random', [('functionName', 5, s('color_random')), ('color1', 8, bytes([255, 0, 0, 255])),
                                                     ('color2', 8, bytes([0, 0, 255, 255]))]),
            ('DmeParticleOperator', 'basic_movement', [('functionName', 5, s('basic_movement')), ('gravity', 10, struct.pack('<3f', 0, 0, 70))]),
            ('DmeParticleSystemDefinition', 'env_embers', [('material', 5, s('particle/glow')), ('emitters', 15, array(8))]),
            ('DmeParticleChild', 'child', [('child', 1, i(6)), ('delay', 3, f(0))]),
            ('DmeParticleOperator', 'emit_instantaneously', [('functionName', 5, s('emit_instantaneously'))]),
        ])

    def test_read_and_systems(self):
        found = pcf.systems(self.fire())
        self.assertEqual(sorted(found), ['env_embers', 'env_fire_tiny'])
        tiny = found['env_fire_tiny']
        self.assertEqual(tiny['attributes']['max_particles'], 20)
        self.assertEqual([o['function'] for o in tiny['operators']['initializers']], ['radius_random', 'color_random'])
        self.assertEqual(tiny['operators']['initializers'][1]['color1'], [255, 0, 0, 255])
        self.assertEqual(tiny['operators']['operators'][0]['gravity'], [0, 0, 70])
        self.assertEqual(tiny['children'], [dict(name='env_embers', delay=0)])

    def test_effect_mapping(self):
        notes = Counter()
        [only] = pcf.effect(pcf.systems(self.fire()), 'env_fire_tiny', notes)
        self.assertEqual(notes['systems without a continuous emitter'], 1, 'the burst-only child is left out')
        self.assertEqual((only['material'], only['rate'], only['max']), ('particle/fire', 8, 20))
        t = only['template']
        self.assertEqual(t['size']['start'], [16, 20])
        self.assertEqual(t['rgb']['start'], [[1, 0, 0], [0, 0, 1]])
        self.assertEqual(t['acceleration'], [[0, 0, 70], [0, 0, 70]])

    def test_sheet(self):
        frame = lambda *rect: f(1) + struct.pack('<4f', *rect) * 4
        resource = struct.pack('<ii', 1, 1) + struct.pack('<iii', 0, 1, 2) + f(2) + frame(0, 0, .5, .5) + frame(.5, 0, 1, .5)  # version 1: four rects a frame
        resource = struct.pack('<I', len(resource)) + resource
        header = bytearray(80 + 8)
        header[:16] = struct.pack('<4sIII', b'VTF\0', 7, 3, len(header))
        struct.pack_into('<I', header, 68, 1)
        struct.pack_into('<3sBI', header, 80, b'\x10\0\0', 0, len(header))
        self.assertEqual(pcf.sheet(bytes(header) + resource), [[(0, 0, .5, .5), (.5, 0, 1, .5)]])
        self.assertIsNone(pcf.sheet(b'VTF\0' + struct.pack('<II', 7, 2) + bytes(80)))

    def test_fires(self):
        systems = pcf.systems(self.fire())
        systems['env_fire_tiny_smoke'] = systems['env_fire_tiny']
        stage = lambda material, q: (material, dict(map=material + '.png'), 1, 1)
        drop = lambda origin: [origin[0], origin[1], origin[2] - 10]
        ents = [dict(classname='env_fire', origin='0 0 100', spawnflags='6', firesize='20'),        # smokeless, starts on: drops
                dict(classname='env_fire', origin='0 0 100', spawnflags='20', firesize='20'),       # don't drop
                dict(classname='env_fire', origin='0 0 100', spawnflags='0'),                       # waits for StartFire
                dict(classname='env_fire', origin='0 0 0', spawnflags='4', firesize='200')]         # large: not in this file
        notes = Counter()
        found = fx.fires(ents, systems, stage, drop, notes)
        self.assertEqual([e['fire'] for _, _, e, _ in found], ['env_fire_tiny', 'env_fire_tiny_smoke'])
        self.assertEqual([e['origin'][2] for _, _, e, _ in found], [90, 100])
        self.assertEqual(found[0][2]['period'], 1 / 8)
        self.assertEqual(notes['env_fire not started'], 1)
        data = fx.particle_data(found, lambda name: dict(texture='x.png', settings={}), 'missing.png', .5)
        self.assertEqual(len(data['templates']), 1, 'the two fires share one template')
        self.assertEqual(data['batches']['particle/fire'], dict(map='particle/fire.png'))


if __name__ == '__main__':
    unittest.main()

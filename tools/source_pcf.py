"""Valve particle files (.pcf: binary DMX, encoding 'binary' version 2) read into plain dicts.

Layout as Valve's public DMX documentation gives it: a text header, a string table (int16 count), the elements
(type and name, 16-byte id) and then each element's attributes (name, type byte, value). Only reading.
"""
import struct

SCALARS = {2: ('i', 4), 3: ('f', 4), 4: ('?', 1), 7: ('i', 4), 8: ('4B', 4), 9: ('2f', 8), 10: ('3f', 12),
           11: ('4f', 16), 12: ('3f', 12), 13: ('4f', 16), 14: ('16f', 64)}


def read(raw):
    """[{'type', 'name', 'attributes': {name: value}}]; element references are indices into the list (or None)."""
    end = raw.index(b'\0')
    header = raw[:end].decode('latin1')
    if 'binary 2' not in header:
        raise ValueError(f'unsupported DMX encoding: {header.strip()}')
    at = end + 1

    def take(fmt, size):
        nonlocal at
        value = struct.unpack_from('<' + fmt, raw, at)
        at += size
        return value if len(value) > 1 else value[0]

    def text():
        nonlocal at
        stop = raw.index(b'\0', at)
        value = raw[at:stop].decode('latin1')
        at = stop + 1
        return value

    strings = [text() for _ in range(take('h', 2))]
    elements = []
    for _ in range(take('i', 4)):
        kind = strings[take('h', 2)]
        name = text()
        at += 16  # element id
        elements.append(dict(type=kind, name=name, attributes={}))

    def value(kind):
        if kind == 1:
            index = take('i', 4)
            return None if index < 0 else index
        if kind == 5:
            return text()
        if kind == 6:
            size = take('i', 4)
            nonlocal_at = at
            return raw[nonlocal_at:nonlocal_at + size] if not skip(size) else None
        fmt, size = SCALARS[kind]
        result = take(fmt, size)
        return result / 10000 if kind == 7 else list(result) if isinstance(result, tuple) else result

    def skip(size):
        nonlocal at
        at += size
        return False

    for element in elements:
        for _ in range(take('i', 4)):
            name, kind = strings[take('h', 2)], take('B', 1)
            if kind >= 15:
                element['attributes'][name] = [value(kind - 14) for _ in range(take('i', 4))]
            else:
                element['attributes'][name] = value(kind)
    return elements


def sheet(raw):
    """A VTF 7.3+ sprite sheet resource (tag 0x10) as [[(u0, v0, u1, v1) per frame] per sequence], or None."""
    if raw[:4] != b'VTF\0' or struct.unpack_from('<I', raw, 8)[0] < 3:
        return None
    for i in range(struct.unpack_from('<I', raw, 68)[0]):
        tag, _, offset = struct.unpack_from('<3sBI', raw, 80 + i * 8)
        if tag != b'\x10\0\0':
            continue
        at = offset + 4
        version, count = struct.unpack_from('<ii', raw, at)
        at += 8
        sequences = []
        for _ in range(count):
            _, _, frames = struct.unpack_from('<iii', raw, at)
            at += 16
            rects = []
            for _ in range(frames):
                rects.append(struct.unpack_from('<4f', raw, at + 4))  # after its duration; version 1 holds 4 rects: the first
                at += 4 + 16 * (4 if version else 1)
            sequences.append(rects)
        return sequences
    return None


def systems(raw):
    """{system name: {attributes, 'operators': {group: [{'function', **attributes}]}, 'children': [names]}}."""
    elements = read(raw)
    groups = ('renderers', 'operators', 'initializers', 'emitters', 'forces', 'constraints')
    result = {}
    for e in elements:
        if e['type'] != 'DmeParticleSystemDefinition':
            continue
        a = e['attributes']
        entry = dict(attributes={k: v for k, v in a.items() if k not in groups + ('children',)}, operators={}, children=[])
        for group in groups:
            entry['operators'][group] = [dict(elements[i]['attributes'], function=elements[i]['attributes'].get('functionName', elements[i]['name']))
                                         for i in a.get(group) or [] if i is not None]
        for i in a.get('children') or []:
            child = elements[i]['attributes'] if i is not None else {}
            target = child.get('child')
            if target is not None:
                entry['children'].append(dict(name=elements[target]['name'], delay=child.get('delay', 0)))
        result[e['name']] = entry
    return result


def _attr(op, name, default=0.):
    value = op.get(name, default)
    return default if value is None else value


def effect(found, root, notes):
    """The particle systems of `root` and its children as particles.js templates: [{material, template, rate,
    sequences: (first, last)}]. A fixed subset of the SDK's operators is read; the rest are named in `notes` (a
    Counter). Operator rules as Valve's documentation describes them (developer.valvesoftware.com, particle operators):
      emit_continuously: emission_rate a second (systems with a set emission_duration, or other emitters, are left out);
      lifetime_random / lifetime from sequence (frames / Frames Per Second, the mean over the sequences picked);
      radius_random, radius_scale (start and end scale of the same radius), alpha_random, color_random (color1 to
      color2), color_fade (to color_fade), alpha fades (in, out, or both), rotation_random, rotation_spin;
      position_within_sphere (distance_max x distance_bias, as a box) plus position_offset_random (times the radius
      when proportional), Initial Velocity Noise and the sphere's local speeds as a velocity box;
      basic_movement's gravity as a world acceleration. max_particles caps rate x life.
    ponytail: drag, forces (random force, twist), noise pre-ageing, yaw flips and blended frames are not reproduced."""
    out, seen = [], set()

    def walk(name):
        if name in seen or name not in found:
            return
        seen.add(name)
        system = found[name]
        a, ops = system['attributes'], system['operators']
        emitters = ops['emitters']
        material = str(a.get('material') or '').replace(chr(92), '/').lower().removesuffix('.vmt')
        emit = next((o for o in emitters if o['function'] == 'emit_continuously'), None)
        if not emit or len(emitters) != 1 or _attr(emit, 'emission_duration') > 0 or not material:
            notes['systems without a continuous emitter'] += 1
        else:
            out.append(_template(a, ops, emit, material, notes))
        for child in system['children']:
            walk(child['name'])
    walk(root)
    return out


KNOWN = {'emit_continuously', 'lifetime_random', 'lifetime from sequence', 'radius_random', 'radius_scale', 'alpha_random',
         'color_random', 'color_fade', 'alpha_fade', 'alpha_fade_in_random', 'alpha_fade_out_random', 'lifespan_decay',
         'rotation_random', 'rotation_spin', 'position_within_sphere', 'position_offset_random', 'Initial Velocity Noise',
         'basic_movement', 'sequence_random', 'render_animated_sprites'}


def _template(a, ops, emit, material, notes):
    find = lambda function: next((o for group in ops.values() for o in group if o['function'] == function), None)
    for group in ops.values():
        for o in group:
            if o['function'] not in KNOWN:
                notes[o['function']] += 1
    seq = find('sequence_random')
    sequences = (int(_attr(seq, 'sequence_min')), int(_attr(seq, 'sequence_max'))) if seq else (0, 0)
    radius = find('radius_random')
    r0, r1 = (_attr(radius, 'radius_min', 5.), _attr(radius, 'radius_max', 5.)) if radius else (a.get('radius') or 5.,) * 2
    scale = find('radius_scale')
    s0, s1 = (_attr(scale, 'radius_start_scale', 1.), _attr(scale, 'radius_end_scale', 1.)) if scale else (1., 1.)
    life = find('lifetime_random')
    t = dict(count=[1, 1], life=[_attr(life, 'lifetime_min', 1.), _attr(life, 'lifetime_max', 1.)] if life else [1., 1.],
             size=dict(start=[r0 * s0, r1 * s0], end=[r0 * s1, r1 * s1], curve=dict(linear=True)), sizeTogether=True,
             worldOrigin=True, worldVelocity=True, worldAcceleration=True, rgbTogether=True)
    fps = find('lifetime from sequence')
    if fps:
        t['lifeFromSequence'] = _attr(fps, 'Frames Per Second', 30.)
    colour = find('color_random')
    c1, c2 = ([v / 255 for v in _attr(colour, k, [255] * 4)[:3]] for k in ('color1', 'color2')) if colour else ([1] * 3,) * 2
    fade = find('color_fade')
    t['rgb'] = dict(start=[c1, c2], end=[[v / 255 for v in _attr(fade, 'color_fade', [255] * 4)[:3]]] * 2, curve=dict(linear=True)) if fade else dict(start=[c1, c2])
    alpha = find('alpha_random')
    al = [_attr(alpha, 'alpha_min', 255) / 255, _attr(alpha, 'alpha_max', 255) / 255] if alpha else [1, 1]
    fade_in = find('alpha_fade_in_random') or find('alpha_fade')
    fade_out = find('alpha_fade_out_random') or find('alpha_fade')
    if fade_in and fade_out:
        t['alpha'] = dict(start=[0, 0], end=al, curve=dict(kind='sine', parm=.5))  # ponytail: in and out as one bump.
    elif fade_out:
        start = 1 - _attr(fade_out, 'fade out time max', .25)
        t['alpha'] = dict(start=al, end=[0, 0], curve=dict(kind='nonlinear', parm=100 * start))
    elif fade_in:
        t['alpha'] = dict(start=[0, 0], end=al, curve=dict(kind='clamp', parm=100 * _attr(fade_in, 'fade in time max', .25)))
    else:
        t['alpha'] = dict(start=al)
    rotation = find('rotation_random')
    if rotation:
        base = _attr(rotation, 'rotation_initial')
        t['rotation'] = [base + _attr(rotation, 'rotation_offset_min'), base + _attr(rotation, 'rotation_offset_max', 360.)]
    spin = find('rotation_spin')
    if spin:
        t['spin'] = [_attr(spin, 'spin_rate_min'), _attr(spin, 'spin_rate_degrees')]
    sphere, offset, noise = find('position_within_sphere'), find('position_offset_random'), find('Initial Velocity Noise')
    reach = [_attr(sphere, 'distance_max') * b for b in _attr(sphere, 'distance_bias', [1, 1, 1])] if sphere else [0, 0, 0]
    low, high = [-v for v in reach], list(reach)
    if offset:
        k = (r0 + r1) / 2 if _attr(offset, 'offset proportional to radius 0/1') else 1
        low = [v + w * k for v, w in zip(low, _attr(offset, 'offset min', [0, 0, 0]))]
        high = [v + w * k for v, w in zip(high, _attr(offset, 'offset max', [0, 0, 0]))]
    t['origin'] = [low, high]
    v0, v1 = [0, 0, 0], [0, 0, 0]
    if sphere:
        a0, a1 = _attr(sphere, 'speed_in_local_coordinate_system_min', [0, 0, 0]), _attr(sphere, 'speed_in_local_coordinate_system_max', [0, 0, 0])
        v0, v1 = [min(x, y) for x, y in zip(a0, a1)], [max(x, y) for x, y in zip(a0, a1)]
    if noise:
        v0 = [x + y for x, y in zip(v0, _attr(noise, 'output minimum', [0, 0, 0]))]
        v1 = [x + y for x, y in zip(v1, _attr(noise, 'output maximum', [0, 0, 0]))]
    t['velocity'] = [v0, v1]
    move = find('basic_movement')
    g = _attr(move, 'gravity', [0, 0, 0]) if move else [0, 0, 0]
    t['acceleration'] = [list(g), list(g)]
    rate = _attr(emit, 'emission_rate', 10.)
    return dict(material=material, template=t, rate=rate, max=a.get('max_particles') or 0, sequences=sequences)

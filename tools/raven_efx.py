"""Raven .efx effect files (Jedi Outcast, Jedi Academy) read into plain particle templates.

OpenJK's FxTemplate.cpp (GPL-2) is a rules-only reference: which keys exist, their defaults and range syntax.
Only Particle and OrientedParticle primitives are kept; every other primitive is counted by type.
"""
import re

KINDS = {'particle': 'particle', 'orientedparticle': 'oriented'}
GROUPS = ('rgb', 'alpha', 'size')


def tokens(text):
    """GenericParser2 words with their line numbers; // and /* */ comments dropped."""
    text = re.sub(r'/\*.*?\*/', lambda m: '\n' * m.group(0).count('\n'), text, flags=re.S)
    return [(word, row) for row, line in enumerate(text.splitlines())
            for word in re.findall(r'"[^"]*"|[{}\[\]]|[^\s{}\[\]"]+', re.sub(r'//.*', '', line))]


def tree(text):
    """[(name, value)]: a value is the rest of the key's line, a list (`[ ... ]`) or a nested group (`{ ... }`)."""
    words, at = tokens(text), 0
    def block():
        nonlocal at
        items = []
        while at < len(words):
            word, row = words[at]; at += 1
            if word == '}': return items
            if word in '{[]': continue
            name = word.strip('"').lower()
            if at < len(words) and words[at][0] == '{':
                at += 1; items.append((name, block()))
            elif at < len(words) and words[at][0] == '[':
                at += 1; values = []
                while at < len(words) and words[at][0] != ']': values.append(words[at][0].strip('"')); at += 1
                at += 1; items.append((name, values))
            else:
                values = []
                while at < len(words) and words[at][1] == row and words[at][0] not in '{}[]': values.append(words[at][0].strip('"')); at += 1
                items.append((name, ' '.join(values)))
        return items
    return block()


def floats(value, count):
    out = []
    for word in str(value).split():
        try: out.append(float(word))
        except ValueError: break
    return out[:count]


def rng(value, default):
    """ParseFloat: one value or a min max pair."""
    v = floats(value, 2)
    return [v[0], v[0]] if len(v) == 1 else v if len(v) == 2 else list(default)


def vec(value, default):
    """ParseVector: three values (min = max) or six (min then max); anything else keeps the default."""
    v = floats(value, 6)
    return [v[:3], v[:3]] if len(v) in (3, 4, 5) and len(v) == 3 else [v[:3], v[3:6]] if len(v) == 6 else [list(d) for d in default]


def group(items, vector=False):
    """rgb / alpha / size: start, end (ranges; rgb as vectors), parm, flags (linear nonlinear wave random clamp)."""
    one = [[1, 1, 1], [1, 1, 1]] if vector else [1, 1]
    out = dict(start=one, end=one, parm=0, flags=[])
    for name, value in items:
        if not isinstance(value, str): continue
        if name == 'start': out['start'] = vec(value, one) if vector else rng(value, one)
        elif name == 'end': out['end'] = vec(value, one) if vector else rng(value, one)
        elif name in ('parm', 'parms'): p = rng(value, [0, 0]); out['parm'] = (p[0] + p[1]) / 2  # ponytail: mean of a parm range.
        elif name in ('flag', 'flags'): out['flags'] = value.lower().split()
    return out


def primitive(kind, items):
    p = dict(type=KINDS[kind], count=[1, 1], life=[50, 50], delay=[0, 0], cullRange=0, spawnFlags=[], flags=[],
             origin=[[0, 0, 0], [0, 0, 0]], radius=[10, 10], height=[10, 10], rotation=[0, 0], rotationDelta=[0, 0],
             velocity=[[0, 0, 0], [0, 0, 0]], acceleration=[[0, 0, 0], [0, 0, 0]], gravity=[0, 0], shaders=[],
             angle=[[0, 0, 0], [0, 0, 0]])
    for name, value in items:
        if isinstance(value, list) and value and isinstance(value[0], tuple):
            if name in GROUPS: p[name] = group(value, vector=name == 'rgb')
            continue
        if isinstance(value, list):
            if name in ('shaders', 'shader'): p['shaders'] = [v for v in value if v]
            continue
        if name == 'count': p['count'] = rng(value, [1, 1])
        elif name == 'life': p['life'] = rng(value, [50, 50])
        elif name == 'delay': p['delay'] = rng(value, [0, 0])
        elif name == 'cullrange': p['cullRange'] = (floats(value, 1) or [0])[0]
        elif name in ('spawnflags', 'spawnflag'): p['spawnFlags'] = value.lower().split()
        elif name in ('flags', 'flag'): p['flags'] = value.lower().split()
        elif name == 'origin': p['origin'] = vec(value, p['origin'])
        elif name == 'radius': p['radius'] = rng(value, [10, 10])
        elif name == 'height': p['height'] = rng(value, [10, 10])
        elif name == 'rotation': p['rotation'] = rng(value, [0, 0])
        elif name == 'rotationdelta': p['rotationDelta'] = rng(value, [0, 0])
        elif name in ('velocity', 'vel'): p['velocity'] = vec(value, p['velocity'])
        elif name in ('acceleration', 'accel'): p['acceleration'] = vec(value, p['acceleration'])
        elif name == 'gravity': p['gravity'] = rng(value, [0, 0])
        elif name in ('angle', 'angles'): p['angle'] = vec(value, p['angle'])
        elif name in ('shaders', 'shader'): p['shaders'] = [value]
    for name in GROUPS: p.setdefault(name, group([], vector=name == 'rgb'))
    return p


def parse(text):
    """{primitives: [...], skipped: {type: count}} for one .efx file."""
    primitives, skipped = [], {}
    for name, value in tree(text):
        if not isinstance(value, list): continue
        kind = name.lower()
        if kind in KINDS: primitives.append(primitive(kind, value))
        elif kind: skipped[kind] = skipped.get(kind, 0) + 1
    return dict(primitives=primitives, skipped=skipped)


def effect_path(name):
    """fx_runner's fxFile as the game's G_EffectIndex/FX_RegisterEffect finds it: under effects/, with .efx."""
    name = name.replace('\\', '/').strip().lower().lstrip('/')
    if not name.startswith('effects/'): name = 'effects/' + name
    return name if name.endswith('.efx') else name + '.efx'

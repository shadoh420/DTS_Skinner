"""Quake 4 BSE effect decls (effects/**/*.fx) as plain data for the Maps page.

Covers the parts the page draws: spawner/emitter segments with sprite, line and oriented particles
(start/end domains, motion envelopes, gravity, blend add). Sound, light, decal, shake, model, debris,
electricity, trail and nested effect segments are kept as counts only. Semantics follow the GPL
"Quake 4 BSE code recreation effort" (rvSegmentTemplate/rvParticleTemplate parsers).
"""
import re

try:
    from tools.doom3_assets import lex
    from tools.doom3_particles import decls
except ImportError:
    from doom3_assets import lex
    from doom3_particles import decls

NUMBER = re.compile(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$')
SEGMENTS = ('spawner', 'emitter', 'trail', 'sound', 'decal', 'light', 'delay', 'effect', 'shake', 'doublevision', 'tunnel')
PARTICLES = ('sprite', 'line', 'oriented', 'decal', 'model', 'light', 'electricity', 'linked', 'debris')
DRAWN = ('sprite', 'line', 'oriented')
DOMAINS = ('point', 'line', 'box', 'sphere', 'cylinder', 'spiral', 'model')


def block(tokens, at):
    """(inner tokens, index after the closing brace) for the brace at tokens[at]."""
    depth = 0; start = at + 1
    while at < len(tokens):
        depth += (tokens[at] == '{') - (tokens[at] == '}'); at += 1
        if not depth: return tokens[start:at - 1], at
    return tokens[start:], at


def numbers(tokens, at, limit=None):
    """Comma-separated numbers from tokens[at] on (lex already split the commas)."""
    values = []
    while at < len(tokens) and (limit is None or len(values) < limit):
        if tokens[at] == ',': at += 1; continue
        if not NUMBER.match(tokens[at]): break
        values.append(float(tokens[at])); at += 1
    return values, at


def pair(tokens, at, default=(0.0, 0.0)):
    values, at = numbers(tokens, at, 2)
    values = (values + values[-1:] * 2)[:2] if values else list(default)
    return values, at


def domain(tokens):
    """position { box x,y,z, X,Y,Z [surface relative ...] } -> {type, values, flags}."""
    tokens = [t for t in tokens if t != '\n']
    if not tokens or tokens[0].lower() not in DOMAINS: return None
    kind = tokens[0].lower(); at = 1
    if kind == 'model': at += 2   # model name, then the vectors
    values, at = numbers(tokens, at)
    return dict(type=kind, values=values, flags=sorted({t.lower() for t in tokens[at:] if t != ','}))


def domains(tokens):
    result = {}; at = 0
    while at < len(tokens):
        if tokens[at] in ('\n', ','): at += 1; continue
        key = tokens[at].lower(); at += 1
        while at < len(tokens) and tokens[at] == '\n': at += 1
        if at < len(tokens) and tokens[at] == '{':
            inner, at = block(tokens, at); d = domain(inner)
            if d: result[key] = d
    return result


def envelopes(tokens):
    result = {}; at = 0
    while at < len(tokens):
        if tokens[at] in ('\n', ','): at += 1; continue
        key = tokens[at].lower(); at += 1
        while at < len(tokens) and tokens[at] == '\n': at += 1
        if at >= len(tokens) or tokens[at] != '{': continue
        inner, at = block(tokens, at); inner = [t for t in inner if t != '\n']; env = dict(table=None, rate=None, count=None, offset=None); i = 0
        while i < len(inner):
            word = inner[i].lower(); i += 1
            if word == 'envelope' and i < len(inner): env['table'] = inner[i].lower(); i += 1
            elif word in ('rate', 'count', 'offset'): env[word], i = numbers(inner, i)
        result[key] = env
    return result


def particle(kind, tokens):
    p = dict(kind=kind, material=None, duration=[1.0, 1.0], gravity=[0.0, 0.0], additive=False, start={}, end={}, motion={})
    at = 0
    while at < len(tokens):
        t = tokens[at]
        if t in ('\n', ','): at += 1; continue
        key = t.lower(); at += 1
        while at < len(tokens) and tokens[at] == '\n': at += 1
        if key in ('start', 'end', 'motion') and at < len(tokens) and tokens[at] == '{':
            inner, at = block(tokens, at); p[key] = envelopes(inner) if key == 'motion' else domains(inner)
        elif key == 'material' and at < len(tokens): p['material'] = tokens[at]; at += 1
        elif key == 'duration':
            p['duration'], at = pair(tokens, at, (1, 1)); p['duration'] = [min(60, max(.002, v)) for v in p['duration']]
        elif key == 'gravity': p['gravity'], at = pair(tokens, at)
        elif key == 'blend' and at < len(tokens): p['additive'] = tokens[at].lower() == 'add'; at += 1
        elif at < len(tokens) and tokens[at] == '{': _, at = block(tokens, at)   # impact/timeout/...
    return p


def segment(kind, tokens):
    s = dict(kind=kind, start=[0.0, 0.0], duration=[0.0, 0.0], count=[1.0, 1.0], locked=False, constant=False, particle=None)
    at = 0
    while at < len(tokens):
        t = tokens[at]
        if t in ('\n', ','): at += 1; continue
        key = t.lower(); at += 1
        while at < len(tokens) and tokens[at] == '\n': at += 1
        if key in ('count', 'rate'): s['count'], at = pair(tokens, at, (1, 1))
        elif key in ('start', 'duration', 'density', 'attenuation'): s[key], at = pair(tokens, at)
        elif key in ('locked', 'constant'): s[key] = True
        elif key in PARTICLES and at < len(tokens) and tokens[at] == '{':
            inner, at = block(tokens, at)
            if s['particle'] is None: s['particle'] = particle(key, inner)
        elif at < len(tokens) and tokens[at] == '{': _, at = block(tokens, at)
        elif at < len(tokens) and tokens[at] != '\n' and not tokens[at].lower() in ('locked', 'constant'):
            at += 1   # one-value settings: scale, detail, particleCap, soundShader ...
    return s


def parse_effects(text):
    """{name: {segments, ignored}} for every `effect` decl."""
    result = {}
    for head, body in decls(text):
        if len(head) < 2 or head[0].lower() != 'effect': continue
        segments = []; ignored = {}; at = 0
        while at < len(body):
            t = body[at].lower()
            if t in SEGMENTS:
                j = at + 1
                while j < len(body) and body[j] != '{': j += 1
                inner, at = block(body, j)
                seg = segment(t, inner)
                if t in ('spawner', 'emitter') and seg['particle'] and seg['particle']['kind'] in DRAWN and seg['particle']['material']:
                    segments.append(seg)
                else:
                    key = t if t not in ('spawner', 'emitter') else (seg['particle'] or {}).get('kind', t); ignored[key] = ignored.get(key, 0) + 1
                continue
            at += 1
        result[head[-1].replace('\\', '/').lower()] = dict(segments=segments, ignored=ignored)
    return result


def effect_decls(files):
    result = {}
    for name, asset in sorted(files.items()):
        if name.startswith('effects/') and name.endswith('.fx'):
            result.update(parse_effects(asset.read().decode('latin1')))
    return result


def extent(effect):
    """A generous effect-local radius for culling: spawn reach, travel over the life, gravity drop, size and length."""
    radius = 16.0
    for s in effect['segments']:
        p = s['particle']; life = p['duration'][1]
        reach = lambda key: max((abs(v) for part in ('start', 'end') for v in (p[part].get(key) or {}).get('values', [])), default=0.0)
        radius = max(radius, reach('position') + reach('velocity') * life + reach('acceleration') * life * life / 2
                     + 1066 * max(abs(g) for g in p['gravity']) * life * life / 2 + reach('size') + reach('length') + reach('offset'))
    return radius

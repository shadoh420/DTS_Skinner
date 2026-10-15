"""Doom 3 .prt particle decls as plain data for the Maps page (DeclParticle.cpp ParseParticleStage)."""
import re

try:
    from tools.doom3_assets import declarations, lex
except ImportError:
    from doom3_assets import declarations, lex

NUMBER = re.compile(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$')


def decls(text):
    """declarations(), but an unclosed last decl (one RoE .fx file) keeps the decls before it, as the decl manager does."""
    try: return list(declarations(lex(text)))
    except ValueError: return list(declarations(lex(text + '\n}' * 32)))


def default_stage():
    """idParticleStage::Default."""
    return dict(material='_default', count=100, bunching=1.0, time=1.5, cycles=0.0, timeOffset=0.0, deadTime=0.0,
                distribution='rect', distributionParms=[8.0, 8.0, 8.0, 0.0], direction='cone', directionParms=[90.0, 0, 0, 0],
                orientation='view', orientationParms=[0.0] * 4, customPath='standard', customPathParms=[0.0] * 8,
                speed=[150.0, 150.0], rotation=[0.0, 0.0], angle=0.0, size=[4.0, 4.0], aspect=[1.0, 1.0],
                gravity=1.0, worldGravity=False, offset=[0.0] * 3, animationFrames=0, animationRate=0.0,
                color=[1.0] * 4, fadeColor=[0.0] * 4, fadeIn=0.1, fadeOut=0.25, fadeIndex=0.0,
                randomDistribution=True, entityColor=False, boundsExpansion=0.0)


def parse_stage(tokens):
    """tokens keep '\\n' so on-line parameter lists (ParseParms) stop where the engine stops."""
    stage = default_stage(); at = 0
    def take():
        nonlocal at
        while at < len(tokens) and tokens[at] == '\n': at += 1
        if at >= len(tokens): raise ValueError('Truncated particle stage')
        at += 1; return tokens[at - 1]
    def number(): return float(take())
    def on_line(limit):
        nonlocal at
        values = []
        while at < len(tokens) and tokens[at] != '\n' and NUMBER.match(tokens[at]) and len(values) < limit:
            values.append(float(tokens[at])); at += 1
        return values + [0.0] * (limit - len(values))
    def parametric():
        # ParseParametric: number [to number], or a table name (tables are evaluated as `from` here).
        nonlocal at
        first = take()
        if not NUMBER.match(first): return [0.0, 0.0, first.lower()]
        value = float(first)
        save = at
        if take().lower() == 'to': return [value, number()]
        at = save; return [value, value]
    while at < len(tokens):
        if tokens[at] == '\n': at += 1; continue
        key = take().lower()
        if key == 'material': stage['material'] = take()
        elif key in ('count', 'animationframes'): stage['count' if key == 'count' else 'animationFrames'] = int(number())
        elif key in ('time', 'cycles', 'timeoffset', 'deadtime', 'bunching', 'angle', 'fadein', 'fadeout', 'fadeindex',
                     'animationrate', 'boundsexpansion'):
            stage[{'time': 'time', 'cycles': 'cycles', 'timeoffset': 'timeOffset', 'deadtime': 'deadTime', 'bunching': 'bunching',
                   'angle': 'angle', 'fadein': 'fadeIn', 'fadeout': 'fadeOut', 'fadeindex': 'fadeIndex',
                   'animationrate': 'animationRate', 'boundsexpansion': 'boundsExpansion'}[key]] = number()
        elif key in ('randomdistribution', 'entitycolor'):
            stage['randomDistribution' if key == 'randomdistribution' else 'entityColor'] = number() != 0
        elif key in ('distribution', 'direction', 'orientation', 'custompath'):
            kind = take().lower(); name = {'custompath': 'customPath'}.get(key, key)
            stage[name] = kind; stage[name + 'Parms'] = on_line(8 if key == 'custompath' else 4)
        elif key in ('speed', 'rotation', 'size', 'aspect'): stage[key] = parametric()
        elif key in ('color', 'fadecolor'): stage['color' if key == 'color' else 'fadeColor'] = [number() for _ in range(4)]
        elif key == 'offset': stage['offset'] = [number() for _ in range(3)]
        elif key == 'gravity':
            value = take()
            if value.lower() == 'world': stage['worldGravity'] = True; value = take()
            stage['gravity'] = float(value)
        elif key == 'softeningradius': pass
        else: break  # src.Error without fatal errors: the engine keeps the stage parsed so far
    stage['cycleMsec'] = int((stage['time'] + stage['deadTime']) * 1000)
    return stage


def parse_particles(text):
    """{name: {depthHack, stages}} for every `particle` decl in one .prt file."""
    result = {}
    for head, body in decls(text):
        if len(head) < 2 or head[0].lower() != 'particle': continue
        decl = dict(depthHack=0.0, stages=[]); at = 0
        while at < len(body):
            token = body[at]
            if token == '{':
                start = at + 1; depth = 1; at += 1
                while at < len(body) and depth: depth += (body[at] == '{') - (body[at] == '}'); at += 1
                try: decl['stages'].append(parse_stage(body[start:at - 1]))
                except ValueError: pass  # truncated stage, dropped
                continue
            if token.lower() == 'depthhack':
                decl['depthHack'] = float(body[at + 1]); at += 2; continue
            at += 1
        result[head[-1].lower()] = decl
    return result


def particle_decls(files):
    """Every particles/*.prt; RoE files override base ones by name like the decl manager."""
    result = {}
    for name, asset in sorted(files.items(), key=lambda item: (item[1].campaign == 'd3xp', item[0])):
        if name.startswith('particles/') and name.endswith('.prt'):
            result.update(parse_particles(asset.read().decode('latin1')))
    return result


def particle_reference(entity, defs):
    """The .prt decl an entity renders: model "x.prt", a model decl whose mesh is x.prt, or func_smoke "smoke"."""
    names = [entity.get('model', '')]
    if entity.get('classname', '').lower() == 'func_smoke': names.append(entity.get('smoke', ''))
    for name in names:
        name = name.replace('\\', '/').lower()
        if not name: continue
        if not name.endswith('.prt'):
            try: name = defs.model(name).get('mesh', '').replace('\\', '/').lower()
            except (ValueError, KeyError, AttributeError): continue
        if name.endswith('.prt'): return name.rsplit('/', 1)[-1][:-4]
    return None


def parse_fx(text):
    """{name: [particle actions]} from fx/*.fx (DeclFX.cpp ParseSingleFXAction: delay, duration, offset, particle/model .prt)."""
    result = {}
    for head, body in decls(text):
        if len(head) < 2 or head[0].lower() != 'fx': continue
        actions = []; at = 0
        while at < len(body):
            if body[at] != '{': at += 1; continue
            start = at + 1; depth = 1; at += 1
            while at < len(body) and depth: depth += (body[at] == '{') - (body[at] == '}'); at += 1
            t = [x for x in body[start:at - 1] if x != '\n']; action = dict(delay=0.0, duration=0.0, offset=[0.0] * 3, particle=None)
            for i, key in enumerate(x.lower() for x in t):
                try:
                    if key in ('delay', 'duration'): action[key] = float(t[i + 1])
                    elif key == 'offset': action['offset'] = [float(t[i + 1]), float(t[i + 3]), float(t[i + 5])]
                    elif key in ('particle', 'model') and t[i + 1].lower().endswith('.prt'):
                        action['particle'] = t[i + 1].replace('\\', '/').lower().rsplit('/', 1)[-1][:-4]
                except (IndexError, ValueError): pass
            if action['particle']: actions.append(action)
        result[head[-1].replace('\\', '/').lower()] = actions
    return result


def fx_decls(files):
    result = {}
    for name, asset in sorted(files.items(), key=lambda item: (item[1].campaign == 'd3xp', item[0])):
        if name.startswith('fx/') and name.endswith('.fx'):
            result.update(parse_fx(asset.read().decode('latin1')))
    return result


def fx_timing(entity, actions):
    """idEntityFx::Spawn/Event_ClearFx: untriggered fx with `start` or `restart` run at spawn and re-run after
    Duration() plus restart * U(0,1) (the mean is used); restart 0 plays once. None when it never starts."""
    flag = lambda key: entity.get(key, '0') not in ('0', '')
    restart = float(entity.get('restart', 0) or 0)
    if flag('triggered') or not (flag('start') or restart): return None
    duration = max((a['delay'] + a['duration'] for a in actions), default=0.0)
    return dict(duration=duration, period=duration + restart * .5 if restart else 0.0)


def extent(decl):
    """A generous emitter-local radius for culling (no tables, no custom-path detail)."""
    radius = 8.0
    for s in decl['stages']:
        speed = max(abs(v) for v in s['speed'][:2]); size = max(abs(v) for v in s['size'][:2]) * max(1, max(abs(v) for v in s['aspect'][:2]))
        reach = max(abs(v) for v in s['distributionParms'][:3]) + max(abs(v) for v in s['offset']) + speed * s['time']
        reach += abs(s['gravity']) * s['time'] ** 2 + size + max(abs(v) for v in s['customPathParms'][:3])
        radius = max(radius, reach)
    return radius

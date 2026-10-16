"""Bloodlines' own particle format: particles/<name>.txt KeyValues definitions (Troika, no public spec).

Rules read from the game's engine.dll (GOG 1.2 Bin/engine.dll; rules only, no code reproduced):
- A definition is a KeyValues file. fps (default 30, < 1 is invalid -> 30), frames (default fps: one second), min_frames
  and max_frames (default frames; min in [1, frames], max >= frames, else frames). A particle lives min..max frames at fps,
  chosen per particle; every track runs over its own life, so `frames` is only the time base of the (frame) marks.
- Flags: loop (replay the life forever), flat (quad in the parent's plane), movealign (stretched along the motion),
  sortfront, lighting (rgb x the light there), precipitation, no_z_test, depth_offset (moved toward the eye), collide
  (a block: world collisions). surface_color / use_surface_color (default 1) / ignore_surface_color (default 0).
- Tracks and their defaults: red green blue color mask 255, refract width height size 1, rotation 0 (an angle: wraps),
  radius_speed theta_speed phi_speed x_speed y_speed z_speed elevation_speed 0, parent_speed 1.
- A track is comma-separated keys "v", "a~b" (a random number in the range, drawn per particle per key), "v(f)" (at frame
  f; a negative f counts back from the end), "v!" (a step: jump at the key's time instead of ramping to it). Keys without
  a time spread evenly between timed ones; the last is at the end of the life, the first must be at 0. A particle ramps
  linearly from its current value to each key's value over [previous key time, key time].
- Spawn blocks: the first key named `spawn` and every key after it that is a block with a `particle` (a commented-out
  "// spawn" leaves an unnamed block that the KeyValues reader turns into plain keys, so it is skipped). A spawn names a
  definition and has its own tracks over the PARENT's life: red green blue color mask refract width height size rotation
  (multipliers for the child, defaults as above), radius theta phi (the child's start, spherical, degrees), x y z (the
  child's start in the parent's axes), elevation (world up), timescale (child life rate x, default 1), rate (children a
  second; `distance` "1": a unit of the parent's travel instead), burst (children at once at the key's time; always steps,
  may start after 0). Spawn ranges are kept as ranges and drawn per child.
- Colour: rgb = track x spawn multiplier x color x spawn color / 255^3, alpha = mask x spawn mask / 255, both clamped to
  0..255 per particle; drawn with the Sprite shader's render mode 8 (materials/engine/particlenormal.vmt), kRenderTransAlphaAdd:
  ONE, ONE_MINUS_SRC_ALPHA. So rgb glows and mask covers: fire is mask 0 (pure glow), smoke covers a little and glows
  a little, blood drops cover fully.
- Size: the sprite (particles/<sprite>.tga) keeps its aspect; its longer side is size x spawn size units, then width and
  height scale the two sides.
- Motion, each second: radius, theta, phi += their speeds (an orbit about the emitter); position += x/y/z speed along the
  parent's axes (A, B, A x B) and elevation speed along world up; the drawn point = position + radius x (cos phi (cos theta A
  + sin theta B) + sin phi (A x B)). Children follow their parent's motion x parent_speed.
"""
import math
import re

try:
    from tools.source_effects import _axes
except ImportError:
    from source_effects import _axes

TRACKS = dict(red=255., green=255., blue=255., color=255., mask=255., refract=1., width=1., height=1., size=1., rotation=0.,
              radius_speed=0., theta_speed=0., phi_speed=0., x_speed=0., y_speed=0., z_speed=0., elevation_speed=0., parent_speed=1.)
SPAWN_TRACKS = dict(red=255., green=255., blue=255., color=255., mask=255., refract=1., width=1., height=1., size=1., rotation=0.,
                    radius=0., theta=0., phi=0., x=0., y=0., z=0., elevation=0., timescale=1., rate=0., burst=0.)
FLAGS = ('loop', 'flat', 'sortfront', 'movealign', 'lighting', 'precipitation', 'no_z_test')


# --- KeyValues (Troika's reader in engine.dll: a name token, then a value token or a block) ----------------------------
_TOKEN = re.compile(r'"([^"]*)"?|([{}])|([^\s{}"]+)')


def _tokens(text):
    text = re.sub(r'//[^\n]*', '', text)
    for m in _TOKEN.finditer(text):
        yield m.group(1) if m.group(1) is not None else m.group(2) or m.group(3)


def keyvalues(text):
    """The first root block as [(name, value str | sub-list)], names lower-cased (lookups ignore case). As the game
    reads it: a name "}" ends a block, a value "{" opens one, and nothing else is special, so a "// spawn" block becomes
    plain pairs ("{" = "particle", ..., last = "}") and the keys after it still load."""
    tokens = _tokens(text)

    def block():
        out = []
        for name in tokens:
            if name == '}':
                break
            value = next(tokens, None)
            if value is None:
                break
            out.append((name.lower(), block() if value == '{' else value))
        return out

    for name in tokens:
        if next(tokens, None) == '{':
            return block()
    return []


def find(kv, key, default=None):
    return next((v for k, v in kv if k == key), default)


def _atof(text, default=0.):
    m = re.match(r'\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?', text) if isinstance(text, str) else None
    return float(m.group()) if m else default


def _int(kv, key, default):
    v = find(kv, key)
    return int(_atof(v, default)) if isinstance(v, str) and v.strip() else default


# --- Tracks ---------------------------------------------------------------------------------------------------------
_NUMBER = re.compile(r'\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)')


def track(text, frames, burst=False):
    """[(start, end, low, high, step)] over a life of 1, or None (invalid: the track keeps its default). Engine rules
    in the module docstring; `burst` keys are steps and may start after 0."""
    if not isinstance(text, str) or not text.strip():
        return None
    keys, at = [], 0
    while True:
        m = _NUMBER.match(text, at)
        if not m:
            return None  # "missing value"
        low = high = float(m.group(1)); at = m.end()
        if text[at:at + 1] == '~':
            m = _NUMBER.match(text, at + 1)
            if not m:
                return None
            high = float(m.group(1)); at = m.end()
        time = None
        if text[at:at + 1] == '(':
            m = _NUMBER.match(text, at + 1)
            if not m or text[m.end():m.end() + 1] != ')':
                return None
            time = float(m.group(1)) / frames
            time += 1 if time < 0 else 0
            if not 0 <= time <= 1 or not keys and time and not burst:
                return None
            at = m.end() + 1
        while text[at:at + 1].isspace():
            at += 1
        step = burst or text[at:at + 1] == '!'
        at += text[at:at + 1] == '!'
        while text[at:at + 1].isspace():
            at += 1
        keys.append([time, low, high, step])
        if text[at:at + 1] != ',':
            break
        at += 1
    if at < len(text.rstrip()):
        return None  # "syntax error"
    if len(keys) == 1:
        if not burst or keys[0][0] is None:
            keys[0][0] = 0.
        return [(keys[0][0], keys[0][0], keys[0][1], keys[0][2], True)]
    if keys[0][0] is None or not burst:
        keys[0][0] = 0.
    if keys[-1][0] is None:
        keys[-1][0] = 1.
    k = 1
    while k < len(keys):  # untimed keys spread evenly between the timed ones around them
        if keys[k][0] is not None:
            keys[k][0] = max(keys[k][0], keys[k - 1][0])  # "out of order": clamped to the previous time
            k += 1
            continue
        j = next(j for j in range(k, len(keys)) if keys[j][0] is not None)
        for n in range(k, j):
            keys[n][0] = keys[k - 1][0] + (n - k + 1) * (keys[j][0] - keys[k - 1][0]) / (j - k + 1)
        k = j
    out, prev = [], 0.
    for n, (time, low, high, step) in enumerate(keys):
        out.append((time if step or n == 0 else prev, time, low, high, step))
        prev = time
    return out


# --- Definitions ----------------------------------------------------------------------------------------------------
def definition_name(name):
    name = name.replace('\\', '/').strip().lower()
    name = name[len('particles/'):] if name.startswith('particles/') else name
    return name[:-4] if name.endswith('.txt') else name


def load(name, read, cache=None, depth=0):
    """The definition `name` (an entity's particle_definition or a spawn's particle) as a dict, or None when it is
    missing. read(asset name) gives bytes or None. Spawned definitions are loaded once (cache) and shared."""
    cache = {} if cache is None else cache
    name = definition_name(name)
    if name in cache:
        return cache[name]
    raw = read(f'particles/{name}.txt')
    if raw is None or depth > 16:
        cache[name] = None
        return None
    kv = keyvalues(raw.decode('cp1252', 'replace'))
    fps = _atof(find(kv, 'fps', '30'), 30.)
    fps = 30. if fps < 1 else fps
    frames = _int(kv, 'frames', -1)
    frames = frames if frames >= 1 else int(fps)
    low, high = _int(kv, 'min_frames', frames), _int(kv, 'max_frames', frames)
    low = low if 1 <= low <= frames else frames
    high = high if high >= frames else frames
    sprite = find(kv, 'sprite')
    d = dict(name=name, fps=fps, frames=frames, life=[low / fps, high / fps],
             sprite=sprite.lower() if isinstance(sprite, str) and sprite.strip() and not sprite.startswith('~') else None,
             normal=find(kv, 'normal') if isinstance(find(kv, 'normal'), str) else None,
             depth_offset=_atof(find(kv, 'depth_offset', '0')),
             surface=not (_int(kv, 'surface_color', 1) == 0 or _int(kv, 'use_surface_color', 1) == 0 or _int(kv, 'ignore_surface_color', 0)),
             collide=isinstance(find(kv, 'collide'), list),
             **{f: bool(_int(kv, f, 0)) for f in FLAGS})
    d['tracks'] = {t: track(find(kv, t), frames) or [(0., 0., v, v, True)] for t, v in TRACKS.items()}
    d['refracts'] = isinstance(find(kv, 'refract'), str)
    cache[name] = d
    d['spawns'] = []
    first = next((n for n, (k, _) in enumerate(kv) if k == 'spawn'), None)
    for key, value in kv[first:] if first is not None else []:
        if not isinstance(value, list) or not isinstance(find(value, 'particle'), str) or not find(value, 'particle').strip():
            continue
        child = load(find(value, 'particle'), read, cache, depth + 1)
        if child is None:
            continue
        d['spawns'].append(dict(child=child, distance=bool(_int(value, 'distance', 0)),
                                tracks={t: track(find(value, t), frames, t == 'burst') or [(0., 0., v, v, True)]
                                        for t, v in SPAWN_TRACKS.items()}))
    return d


def constant(keys):
    """(low, high) when a track never changes over the life, else None."""
    return (keys[0][2], keys[0][3]) if len(keys) == 1 else None


# --- Map emitters for static/particles.js (its 'troika' model) ----------------------------------------------------
ORDER = ('red', 'green', 'blue', 'color', 'mask', 'width', 'height', 'size', 'rotation', 'radius_speed', 'theta_speed',
         'phi_speed', 'x_speed', 'y_speed', 'z_speed', 'elevation_speed')
MOTION = ORDER[9:]
WORLD_ATTACH = ('', '0', '1', '-1')
FOREVER = 600.  # seconds: a particle living longer than this (city lights: 5000 frames at 1 fps) stays for the visit
# Fill cap (units^2): a spawn whose particles alive x their largest sprite area exceeds it is thinned to it, each kept
# particle brighter (rgb x gain) and more covering (1 - (1 - alpha)^gain) by the same factor. It mostly takes the
# burning mansions' firebrush (2,000 flames and 100 glows of 200-400 units a second: 6.2 ms in la_malkavian_5
# uncapped) and the ending sequences' fire; a few mists and smokes (hw_warrens, ch_temple_2, la_plaguebearer_sewer_1)
# are thinned up to 4.4x and still read as soft mist.
# ponytail: one cap for every view; a view-dependent budget would keep more of them from afar.
FILL = 250000.


def _span(keys):
    return [min(k[2] for k in keys), max(k[3] for k in keys)]


def _moves(d):
    return any(k[2] or k[3] for t in MOTION for k in d['tracks'][t])


def _stays(d):
    return d['loop'] or d['life'][0] >= FOREVER


def _offset(spawn, axes):
    """Where a spawn puts a child, mid-range (a collapsed emitter): x/y/z along the axes, elevation up, radius at theta/phi."""
    a, b, c = axes
    mid = {t: sum(_span(spawn['tracks'][t])) / 2 for t in ('x', 'y', 'z', 'elevation', 'radius', 'theta', 'phi')}
    th, ph = math.radians(mid['theta']), math.radians(mid['phi'])
    d = [math.cos(ph) * (math.cos(th) * a[i] + math.sin(th) * b[i]) + math.sin(ph) * c[i] for i in range(3)]
    return [mid['x'] * a[i] + mid['y'] * b[i] + mid['z'] * c[i] + mid['radius'] * d[i] + (mid['elevation'] if i == 2 else 0) for i in range(3)]


def _template(parent, spawn, aspect, scale, box=None, burst=None):
    """A particles.js troika template for one spawn of `parent`: its rate x scale (burst None) or one burst (time, count)."""
    child, tracks = spawn['child'], spawn['tracks']
    timescale = _span(tracks['timescale'])
    life = [child['life'][0] / max(timescale[1], 1e-3), child['life'][1] / max(timescale[0], 1e-3)]
    t = dict(model='troika', loop=_stays(child), movealign=child['movealign'], flat=child['flat'], collide=child['collide'],
             depthOffset=child['depth_offset'], aspect=aspect, lifeRate=[1 / child['life'][1], 1 / child['life'][0]],
             spawn={k: _span(v) for k, v in tracks.items() if k not in ('rate', 'burst')},
             tracks=[[list(k[:4]) for k in child['tracks'][n]] for n in ORDER], parentLife=sum(parent['life']) / 2,
             life=[1e6, 1e6] if _stays(child) else life, count=1, delay=0)
    if box is not None:
        t['box'] = box
    if burst is not None:
        t.update(count=max(1, round(burst[1] * scale)), delay=burst[0] * t['parentLife'])
    else:
        rate = [[k[0], k[1], k[2] * scale, k[3] * scale] for k in tracks['rate']]
        fixed = constant(rate) is not None and rate[0][2] == rate[0][3]
        t.update(rateMax=0 if fixed else max(k[3] for k in rate), tracks=t['tracks'] + [rate])
        if constant(rate) is not None:
            t['parentLife'] = 1e9  # a single-key rate is drawn once for the emitter's whole stay
    top = lambda n: max(k[3] for k in t['tracks'][ORDER.index(n)])
    side = top('size') * t['spawn']['size'][1]
    area = 4 * aspect[0] * aspect[1] * side * side * top('width') * t['spawn']['width'][1] * top('height') * t['spawn']['height'][1]
    alive = t['count'] if burst is not None else max(k[3] for k in t['tracks'][16]) * sum(life) / 2
    keep = min(1., FILL / max(alive * abs(area), 1e-9))
    if keep < 1:
        if burst is not None:
            t['gain'] = t['count'] / max(1, round(t['count'] * keep))
            t['count'] = max(1, round(t['count'] * keep))
        else:
            t.update(thin=keep, gain=1 / keep)
    return t


def emitters(entities, read, bounds, floor, sprite_aspect):
    """[(sprite, template, emitter)] for static/particles.js (troika model; map units and axes) and counts by outcome.
    read(asset) -> bytes | None; bounds(entity) -> brush (mins, maxs) | None; floor(origin) -> the world's height below
    or None; sprite_aspect(sprite) -> [half width, half height] (longer side 0.5) or None when the TGA is missing.

    Drawn: env_particle that starts active, not parented (parentname), at a place in the world (WORLD_ATTACH: the
    unparented attach types the maps give fires, stars and drips; 11 is rain around the player, others are bones);
    func_particle that starts active, its children spread over the brush's box (C_EnvParticle's box callback: inferred).
    Axes: the entity's forward, left and up (inferred). A looping definition spawns its children at its rate (x
    ramp_scale, vampire.dll's rate scale) or its multi-key bursts every life. A definition that plays once (timers) only
    keeps what outlives it: looping children that do not move and themselves spawn (WaterDrops_Timer ->
    WaterDrops_Emitter, collapsed to an emitter at the spawn's mid-range offset) and bursts of particles that loop or
    live >= FOREVER (stars, city lights). A single-key burst fires only at the parent's birth (single-key tracks are
    not replayed when it loops), so it is kept only when the child stays."""
    cache, found, counts, templates = {}, [], {}, {}

    def count(reason):
        counts[reason] = counts.get(reason, 0) + 1

    for e in entities:
        cls = e.get('classname', '').lower()
        if cls not in ('env_particle', 'func_particle'):
            continue
        if e.get('active', '1').strip() == '0' or e.get('starthidden', '0').strip() == '1':
            count(f'{cls}: starts inactive (waits for a trigger)')
            continue
        if e.get('parentname'):
            count(f'{cls}: follows a parent entity')
            continue
        if cls == 'env_particle' and e.get('attach_type', '0').strip() not in WORLD_ATTACH:
            count(f'env_particle: attach_type {e["attach_type"]} (' + ('follows the player' if e['attach_type'].strip() == '11' else 'attached') + ')')
            continue
        box = None
        if cls == 'func_particle':
            b = bounds(e)
            if b is None:
                count('func_particle: no brush model')
                continue
            box = [[float(v) for v in b[0]], [float(v) for v in b[1]]]
        ramp = _atof(e.get('ramp_scale', '1'), 1.)
        if ramp <= 0:
            count(f'{cls}: ramp_scale 0 (nothing spawns until an input)')
            continue
        root = load(e.get('particle_definition', ''), read, cache)
        if root is None:
            count(f'{cls}: definition missing')
            continue
        origin = [float(v) for v in (e.get('origin', '0 0 0').split() + ['0', '0', '0'])[:3]]
        if box is not None:
            middle = [(lo + hi) / 2 for lo, hi in zip(*box)]
            box, origin = [[lo - m for lo, m in zip(box[0], middle)], [hi - m for hi, m in zip(box[1], middle)]], middle
        forward, right = _axes([float(v) for v in (e.get('angles', '0 0 0').split() + ['0', '0', '0'])[:3]])
        a, b = [float(v) for v in forward], [-float(v) for v in right]
        axes = (a, b, [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]])
        plan, collapsed = [(root, origin)], []
        if not root['loop']:
            for s in root['spawns']:
                c = s['child']
                if c['spawns'] and c['loop'] and not c['sprite'] and not _moves(c) and any(k[3] > 0 for k in s['tracks']['burst']):
                    plan.append((c, [o + d for o, d in zip(origin, _offset(s, axes))]))
                    collapsed.append(c)
        drawn = 0
        for parent, at in plan:
            for n, s in enumerate(parent['spawns']):
                c = s['child']
                if not c['sprite']:
                    if not any(c is x for x in collapsed):
                        count('child emitter that moves or plays once (deferred)' if c['spawns'] else 'spawn without a sprite')
                    continue
                if c['normal']:
                    count('refraction sprite (deferred)')
                    continue
                aspect = sprite_aspect(c['sprite'])
                if aspect is None:
                    count('sprite TGA missing')
                    continue
                if c['spawns']:
                    count('children of a sprite particle (deferred)')
                if s['distance']:
                    count('spawns per distance travelled (the emitter does not move)')
                    continue
                stays, made = _stays(c), []
                rate, burst = s['tracks']['rate'], s['tracks']['burst']
                if any(k[3] > 0 for k in rate):
                    if not parent['loop']:
                        count('rate of an emitter that plays once (plays once)')
                    elif stays:
                        count('a particle that stays, spawned at a rate (accumulates in the game; skipped)')
                    else:
                        made.append(None)
                if any(k[3] > 0 for k in burst):
                    if constant(burst) is not None:
                        if stays:
                            made.append((0., sum(_span(burst)) / 2))
                        else:
                            count('one burst of a short-lived particle at the start (plays once)')
                    elif parent['loop'] and not stays:
                        made += [(k[1], (k[2] + k[3]) / 2) for k in burst if k[3] > 0]
                    else:
                        count('bursts every life of a particle that stays (accumulates; skipped)')
                for burst_key in made:
                    ident = (parent['name'], n, ramp, burst_key, None if box is None else id(e))
                    if ident not in templates:
                        templates[ident] = _template(parent, s, aspect, ramp, box, burst_key)
                    template = templates[ident]
                    if burst_key is None:
                        period = 1 / ((template['rateMax'] or max(template['tracks'][16][0][2], 1e-6)) * template.get('thin', 1))
                    else:
                        period = 1e6 if template['loop'] else template['parentLife']
                    emitter = dict(origin=at, forward=axes[0], right=axes[1], period=period)
                    if template['loop'] and burst_key is not None:
                        emitter['offset'] = -1000.  # there since the map started: already 1000 s old
                    if c['collide']:
                        below = floor(at)
                        if below is not None:
                            emitter['floor'] = below
                    found.append(('particles/' + c['sprite'], template, emitter))
                    drawn += 1
        count(f'{cls}: drawn' if drawn else f'{cls}: nothing that stays drawn')
    return found, counts

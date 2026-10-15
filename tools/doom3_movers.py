"""Continuous movers for the Maps page: idRotater (start_on), idBobber and idPendulum, plus what is bound to them.

Script-driven movers (func_mover, spline movers, doors) stay at their authored start pose. Axes follow the
importer's convention (world = axis @ local); idMat3 rows are its columns.
"""
import math

import numpy as np

MOVERS = ('func_rotating', 'func_bobbing', 'func_pendulum')
GRAVITY = 1066.0   # g_gravity default in Doom 3 and Quake 4


def angles_to_axis(angles):
    """idAngles::ToMat3 (pitch, yaw, roll in degrees), transposed to the importer's column axes."""
    sp, sy, sr = (math.sin(math.radians(a)) for a in angles)
    cp, cy, cr = (math.cos(math.radians(a)) for a in angles)
    rows = np.array([[cp * cy, cp * sy, -sp],
                     [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, sr * cp],
                     [cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp]])
    return rows.T


def axis_to_angles(axis):
    """idMat3::ToAngles of the idMat3 whose rows are the given axis columns."""
    m = np.asarray(axis).T; sp = max(-1.0, min(1.0, m[0][2])); theta = -math.asin(sp)
    if math.cos(theta) > 8192 * 1.1920929e-07:
        return [math.degrees(theta), math.degrees(math.atan2(m[0][1], m[0][0])), math.degrees(math.atan2(m[1][2], m[2][2]))]
    return [math.degrees(theta), math.degrees(-math.atan2(m[1][0], m[1][1])), 0.0]


def flag(entity, key, default='0'):
    return entity.get(key, default).strip() not in ('0', '', 'false')


def mover_records(entities, origin_of, axis_of, local_min_z):
    """[mover], {entity name: mover index} for movers and everything bound to them (through bind chains)."""
    named = {e.get('name'): e for e in entities if e.get('name')}; movers = []; index = {}
    def make(entity):
        kind = entity.get('classname'); origin = origin_of(entity); a0 = axis_to_angles(axis_of(entity))
        speed = float(entity.get('speed', {'func_rotating': 100, 'func_bobbing': 4, 'func_pendulum': 30}[kind]))
        if kind == 'func_rotating':
            if not flag(entity, 'start_on'): return None
            delta = [0.0, 0.0, 0.0]; delta[2 if flag(entity, 'x_axis') else 0 if flag(entity, 'y_axis') else 1] = speed
            return dict(kind='rotate', origin=origin, angles=a0, delta=delta)
        phase = float(entity.get('phase', 0))
        if kind == 'func_bobbing':
            height = float(entity.get('height', 32)); delta = [0.0, 0.0, 0.0]
            delta[0 if flag(entity, 'x_axis') else 1 if flag(entity, 'y_axis') else 2] = height * 2
            # EXTRAPOLATION_DECELSINE|NOSTOP from phase*1000 ms over speed*500 ms.
            return dict(kind='bob', origin=origin, delta=delta, start=phase * 1000, duration=speed * 500)
        freq = float(entity['freq']) if entity.get('freq') else 1 / (2 * math.pi) * math.sqrt(GRAVITY / (3 * max(8.0, abs(local_min_z(entity) or 0))))
        return dict(kind='pendulum', origin=origin, angles=a0, delta=[0.0, 0.0, speed * 2], start=phase * 1000, duration=500 / freq)
    def own(name, depth=0):
        if name in index: return index[name]
        entity = named.get(name)
        if entity is None or depth > 16: return None
        parent = own(entity['bind'], depth + 1) if entity.get('bind') in named else None
        record = make(entity) if entity.get('classname') in MOVERS else None
        if record:
            record.update(name=name, parent=parent); movers.append(record); index[name] = len(movers) - 1
        elif parent is not None: index[name] = parent
        return index.get(name)
    for name in named: own(name)
    return movers, index


def reach(movers, i):
    """How far a point can travel from its mover chain's pivots: bob amplitudes, summed up the chain."""
    total = 0.0
    while i is not None:
        m = movers[i]
        if m['kind'] == 'bob': total += np.linalg.norm(m['delta']) * m['duration'] * .001 * math.sqrt(.5)
        i = m['parent']
    return total


def swept_bounds(bounds, movers, i):
    """A box holding `bounds` under every pose of mover i's chain: spheres about each rotating pivot."""
    lo, hi = np.asarray(bounds[0], float), np.asarray(bounds[1], float)
    j = i
    while j is not None:
        m = movers[j]
        if m['kind'] != 'bob':
            corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            r = np.linalg.norm(corners - m['origin'], axis=1).max(); lo, hi = np.asarray(m['origin']) - r, np.asarray(m['origin']) + r
        j = m['parent']
    extra = reach(movers, i)
    return [(lo - extra).tolist(), (hi + extra).tolist()]

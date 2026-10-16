"""Light at the vertices of a model placed in a Bloodlines (Source BSP17) map: the ambient cube the engine builds from
the lightmaps around it plus the worldlights lump's lights that reach it, in the lightmaps' own linear units.

Source lights a model per vertex with an ambient cube (six colours, mixed by the normal's squared components) and the
local lights (intensity x falloff x max(0, N.L)). Maps without leaf ambient data (Bloodlines' 32-byte leaves hold none)
take the cube from the world around the model: rays spread over the sphere read the lightmap colour where they meet the
world, and each cube face takes their average weighted by the cosine to it (lightcache.cpp
ComputeAmbientFromSphericalSamples / R_LightVec; ponytail: 48 rays, the engine's 162 anorms). Worldlights (dworldlight_t, 88 bytes) are tested for line of sight
through the BSP's solid leaves; skylights reach a model whose ray toward them meets a sky surface.
"""
import math
import struct

import numpy as np

WORLDLIGHT = struct.Struct('<3f3f3f3i7f3i')
EMIT_SURFACE, EMIT_POINT, EMIT_SPOTLIGHT, EMIT_SKYLIGHT, EMIT_QUAKELIGHT, EMIT_SKYAMBIENT = range(6)
CONTENTS_SOLID, SURF_SKY2D, SURF_SKY = 1, 2, 4
AXES = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], float)
REACH = 16384  # ray length, map units
# Ambient sample directions: a Fibonacci sphere, and each one's cosine weight toward the cube faces (normalised per face).
_k = np.arange(48) + .5
RAYS = np.column_stack([np.cos(np.pi * (1 + 5 ** .5) * _k) * np.sqrt(1 - (1 - 2 * _k / 48) ** 2),
                        np.sin(np.pi * (1 + 5 ** .5) * _k) * np.sqrt(1 - (1 - 2 * _k / 48) ** 2), 1 - 2 * _k / 48])
WEIGHTS = np.maximum(AXES @ RAYS.T, 0)
WEIGHTS /= WEIGHTS.sum(1, keepdims=True)


def worldlights(raw):
    """The worldlights lump as dicts (origin, intensity, normal, type, style, stopdot, stopdot2, exponent, radius, attn)."""
    if len(raw) % WORLDLIGHT.size:
        raise ValueError('Incomplete worldlights lump')
    found = []
    for at in range(0, len(raw), WORLDLIGHT.size):
        v = WORLDLIGHT.unpack_from(raw, at)
        found.append(dict(origin=np.array(v[0:3]), intensity=np.array(v[3:6]), normal=np.array(v[6:9]), type=v[10], style=v[11],
                          stopdot=v[12], stopdot2=v[13], exponent=v[14], radius=v[15], attn=v[16:19]))
    return found


class Sampler:
    """Line-of-sight and lightmap lookups on a read_bsp() result (tools/import_vtmb_map.py), with `face_light(face)`
    giving a face's lightmap as linear (h, w, 3) colours (None without one)."""

    def __init__(self, bsp, face_light, leaf_stride=32):
        lumps = bsp['lumps']
        self.bsp, self.face_light, self.cache = bsp, face_light, {}
        self.nodes = [struct.unpack_from('<3i12x2H', lumps[5], at) for at in range(0, len(lumps[5]), 32)]
        self.contents = [struct.unpack_from('<i', lumps[10], at)[0] for at in range(0, len(lumps[10]), leaf_stride)]
        self.planes = [(np.array(p[:3]), p[3]) for p in bsp['planes']]
        self.lights = worldlights(lumps[15]) if len(lumps) > 15 else []
        self.dark = set(bsp.get('dark_styles', ()))
        self.ok = bool(self.nodes and self.contents)  # Synthetic maps without a tree: every line clear, no ambient.

    def trace(self, a, b):
        """(fraction, node) where the segment a -> b first enters solid; node is the plane it crossed there (None when it
        starts in solid); None when it stays clear."""
        if not self.ok:
            return None
        stack = [(self.bsp['models'][0][9], 0., 1., None)]
        d = b - a
        while stack:
            node, f1, f2, crossed = stack.pop()
            if node < 0:
                if self.contents[-1 - node] & CONTENTS_SOLID:
                    return f1, crossed
                continue
            plane, front, back = self.nodes[node][:3]
            n, dist = self.planes[plane]
            d1, d2 = n @ (a + d * f1) - dist, n @ (a + d * f2) - dist
            if d1 >= 0 and d2 >= 0:
                stack.append((front, f1, f2, crossed))
            elif d1 < 0 and d2 < 0:
                stack.append((back, f1, f2, crossed))
            else:
                fm = f1 + (f2 - f1) * d1 / (d1 - d2)
                near, far = (front, back) if d1 >= 0 else (back, front)
                stack.append((far, fm, f2, node))  # the far side after the near one (a stack: pushed first)
                stack.append((near, f1, fm, crossed))
        return None

    def surface(self, node, point, toward):
        """(face number, face) of `node` holding `point` and facing `toward` (the ray's start), or None."""
        first, count = self.nodes[node][3:5]
        for number in range(first, first + count):
            if number not in self.cache:
                self.cache[number] = self._polygon(number)
            polygon = self.cache[number]
            if polygon is None:
                continue
            corners, normal = polygon
            if normal @ (toward - point) < 0:
                continue
            edges = np.roll(corners, -1, 0) - corners
            if (np.einsum('ij,ij->i', np.cross(edges, point - corners), np.broadcast_to(normal, edges.shape)) >= -1e-3).all():
                return number, self.bsp['faces'][number]
        return None

    def _polygon(self, number):
        face, b = self.bsp['faces'][number], self.bsp
        if face['count'] < 3:
            return None
        corners = np.array([b['points'][b['edges'][abs(e)][0 if e >= 0 else 1]] for e in b['surfedges'][face['first']:face['first'] + face['count']]], float)
        normal = np.array(b['planes'][face['plane']][:3], float)  # BSP17: the plane index is already the face's side
        if np.cross(corners - corners[0], np.roll(corners, -1, 0) - corners[0]).sum(0) @ normal < 0:
            corners = corners[::-1].copy()  # counter-clockwise seen from the front, as the inside test needs
        return corners, normal

    def seen(self, start, direction):
        """(colour, sky) where a ray from `start` meets the world: the lightmap there (R_LightVec: no reflectivity)."""
        hit = self.trace(start, start + direction * REACH)
        if hit is None or hit[1] is None:  # out into the void, or starting in solid
            return np.zeros(3), False
        point = start + direction * REACH * hit[0]
        found = self.surface(hit[1], point, start)
        if found is None:
            return np.zeros(3), False
        number, face = found
        info = self.bsp['texinfo'][face['texinfo']]
        if info[16] & (SURF_SKY | SURF_SKY2D):
            return np.zeros(3), True
        tile = self.face_light(face)
        if tile is None:
            return np.zeros(3), False
        s, t = (point @ np.array(info[8:16]).reshape(2, 4)[:, :3].T + np.array(info[8:16]).reshape(2, 4)[:, 3]) - face['mins']
        h, w = tile.shape[:2]
        colour = tile[min(max(int(t + .5), 0), h - 1), min(max(int(s + .5), 0), w - 1)]
        return colour, False

    def light(self, origin, normals):
        """(n, 3) light at a model's vertex normals (n, 3; map axes), lit from `origin` (its lighting origin)."""
        samples = [self.seen(origin, ray) for ray in RAYS]
        ambient = sum((light['intensity'] for light in self.lights if light['type'] == EMIT_SKYAMBIENT), np.zeros(3))
        colours = np.array([ambient if sky else colour for colour, sky in samples])  # the sky gives its ambient light
        cube = WEIGHTS @ colours
        squared = normals ** 2
        result = sum(cube[2 * a + (normals[:, a] < 0)] * squared[:, a:a + 1] for a in range(3))
        for light in self.lights:
            if light['style'] in self.dark or light['type'] in (EMIT_SKYAMBIENT, EMIT_QUAKELIGHT):
                continue
            if light['type'] == EMIT_SKYLIGHT:
                toward = -light['normal']
                if self.seen(origin, toward)[1]:
                    result += light['intensity'] * np.maximum(normals @ toward, 0)[:, None]
                continue
            offset = light['origin'] - origin
            distance = max(np.linalg.norm(offset), 1.)
            toward = offset / distance
            if light['radius'] > 0 and distance > light['radius']:
                continue
            # The engine's WorldLightDistanceFalloff: surface lights 1 / r^2 (times the emitter's own cosine), point and
            # spot lights 1 / (constant + linear r + quadratic r^2).
            c, l, q = light['attn']
            falloff = 1 / distance ** 2 if light['type'] == EMIT_SURFACE else 1 / max(c + l * distance + q * distance * distance, 1e-6)
            if light['type'] == EMIT_SURFACE:
                falloff *= max(-(toward @ light['normal']), 0)
            elif light['type'] == EMIT_SPOTLIGHT:
                dot = -(toward @ light['normal'])
                if dot <= light['stopdot2']:
                    continue
                if dot < light['stopdot']:  # vrad's spot falloff between the inner and outer cones
                    falloff *= (dot - light['stopdot2']) / max(light['stopdot'] - light['stopdot2'], 1e-6)
                if light['exponent'] and light['exponent'] != 1:
                    falloff *= max(dot, 0) ** light['exponent']
            strength = light['intensity'] * falloff
            if strength.max() < .5:  # under half a lightmap step: not worth a line test
                continue
            blocked = self.trace(origin, light['origin'] - toward)
            if blocked is None:
                result += strength * np.maximum(normals @ toward, 0)[:, None]
        return result

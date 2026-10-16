"""Source-engine map effects shared by the Bloodlines and E.Y.E. importers: rope chains, infodecals and overlays clipped
onto world faces, sprite and glow entities, and brushes that turn on their own. Map units and axes throughout; each
importer turns the results into its own geometry, materials and page batches.
"""
import math

import numpy as np

try:
    from tools.import_quake_map import rotation
except ImportError:  # Run as a script from tools/.
    from import_quake_map import rotation

SURF_SKY2D, SURF_SKY, SURF_NODRAW, SURF_NODECALS = 0x2, 0x4, 0x80, 0x2000
# How near a node's plane must be to a decal for its faces to take it (GoldSrc lineage: Xash3D gl_decals.c R_DecalNode
# applies a decal to a node's surfaces only while -4 < dist < 4).
DECAL_DISTANCE = 4


def _number(entity, key, default=0.):
    try:
        value = float(str(entity.get(key, default)).split()[0])
    except (ValueError, IndexError):
        return default
    return value if math.isfinite(value) else default


def _vector(entity, key='origin'):
    try:
        values = [float(v) for v in str(entity.get(key, '0 0 0')).split()[:3]]
    except ValueError:
        values = []
    return np.array(values if len(values) == 3 and all(map(math.isfinite, values)) else [0., 0., 0.])


# --- Ropes ---------------------------------------------------------------------------------------------------------
def ropes(entities, segments=16):
    """[(material, points (n, 3), width, texture scale)] of the rope segments: each move_rope/keyframe_rope with a NextKey
    hangs a rope to that key (c_rope.cpp). The game simulates it settling; here it hangs as a parabola whose length is the
    span plus Slack (sag sqrt(3 x span x slack / 8), times the span's horizontal share). Defaults as the FGD's: Slack 25, Width 2, TextureScale 1,
    RopeMaterial cable/cable.vmt."""
    named = {}
    for e in entities:
        if e.get('classname', '').lower() in ('move_rope', 'keyframe_rope') and e.get('targetname'):
            named.setdefault(e['targetname'].lower(), e)
    found = []
    for e in entities:
        if e.get('classname', '').lower() not in ('move_rope', 'keyframe_rope') or not e.get('nextkey'):
            continue
        end = named.get(e['nextkey'].lower())
        if end is None or end is e:
            continue
        a, b = _vector(e), _vector(end)
        span = np.linalg.norm(b - a)
        if span < 1:
            continue
        # A steep rope sags less: by the span's horizontal share (a vertical one hangs straight, not folded on itself).
        sag = math.sqrt(3 * span * max(_number(e, 'slack', 25), 0) / 8) * np.linalg.norm((b - a)[:2]) / span
        t = np.linspace(0, 1, segments + 1)[:, None]
        points = a + (b - a) * t - np.array([0, 0, 1.]) * sag * 4 * t * (1 - t)
        material = str(e.get('ropematerial') or 'cable/cable.vmt')
        found.append((material[:-4] if material.lower().endswith('.vmt') else material, points,
                      max(_number(e, 'width', 2), .1), _number(e, 'texturescale', 1) or 1))
    return found


def rope_mesh(points, width, scale, aspect=1.):
    """A rope as two crossed ribbons along `points` (camera-facing in the game; ponytail: fixed, so it reads from any side):
    (positions (m, 3), uvs (m, 2), triangles (k, 3)). The texture runs along the rope, one repeat every
    width x aspect / scale units (aspect: the texture's height over its width)."""
    direction = np.gradient(points, axis=0)
    still = np.linalg.norm(direction, axis=1) < 1e-9
    direction[still] = points[-1] - points[0]  # a turning point: the chord's direction
    direction /= np.maximum(np.linalg.norm(direction, axis=1, keepdims=True), 1e-9)
    side = np.cross(direction, [0, 0, 1.])
    flat = np.linalg.norm(side, axis=1) < 1e-6
    side[flat] = np.cross(direction[flat], [1., 0, 0])
    side /= np.linalg.norm(side, axis=1, keepdims=True)
    up = np.cross(side, direction)
    v = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]) * scale / (width * aspect)
    positions, uvs, triangles = [], [], []
    for across in (side, up):
        base = len(positions)
        for k, p in enumerate(points):
            positions += [p - across[k] * width / 2, p + across[k] * width / 2]
            uvs += [(0, v[k]), (1, v[k])]
        for k in range(len(points) - 1):
            i = base + 2 * k
            triangles += [(i, i + 2, i + 1), (i + 1, i + 2, i + 3)]
    return np.array(positions), np.array(uvs, float), np.array(triangles)


# --- Decals and overlays -------------------------------------------------------------------------------------------
def clip(polygon, planes):
    """Convex polygon (n, 3) cut to where n . p <= d for every (n, d) in `planes` (Sutherland-Hodgman)."""
    for normal, limit in planes:
        if len(polygon) < 3:
            return polygon
        side = polygon @ normal - limit
        out = []
        for k in range(len(polygon)):
            p, q, sp, sq = polygon[k], polygon[(k + 1) % len(polygon)], side[k], side[(k + 1) % len(polygon)]
            if sp <= 0:
                out.append(p)
            if (sp <= 0) != (sq <= 0):
                out.append(p + (q - p) * (sp / (sp - sq)))
        polygon = np.array(out)
    return polygon


def decal_basis(normal):
    """R_DecalComputeBasis without an s axis (r_decal.cpp): on floors and ceilings s is world x; on walls t points down."""
    if abs(normal[2]) > math.sin(math.radians(45)):
        t = np.cross([1., 0, 0], normal)
        s = np.cross(normal, t)
    else:
        s = np.cross(normal, [0, 0, -1.])
        t = np.cross(s, normal)
    return s / np.linalg.norm(s), t / np.linalg.norm(t)


def decal_pieces(faces, origin, width, height):
    """An infodecal stamped as the engine stamps static decals (R_DecalShoot / R_DecalNode / R_DecalSurface): on every
    face whose plane lies within DECAL_DISTANCE of the decal, the face cut to a width x height square centred on the
    decal's projection, its texture s along the decal basis. `faces`: [(face id, corners (n, 3), normal)] (world faces
    that take decals). Returns [(face id, points (m, 3), st (m, 2))]. The far side of a thin wall takes none: Bloodlines'
    ch_zhaos_1 shows its street sign only outside (the inner face, 6 units off, stays bare; game run 2026-10-16)."""
    reach, found = max(width, height) / 2, []
    for face, corners, normal in faces:
        distance = (origin - corners[0]) @ normal
        if abs(distance) >= DECAL_DISTANCE:
            continue
        centre = origin - normal * distance
        if (corners.min(0) > centre + reach + 1).any() or (corners.max(0) < centre - reach - 1).any():
            continue
        s, t = decal_basis(normal)
        planes = [(s, s @ centre + width / 2), (-s, -(s @ centre) + width / 2),
                  (t, t @ centre + height / 2), (-t, -(t @ centre) + height / 2)]
        piece = clip(corners, planes)
        if len(piece) >= 3 and np.linalg.norm(np.cross(piece[1:] - piece[0], np.roll(piece[1:], -1, 0) - piece[0]).sum(0)) > 1e-3:
            found.append((face, piece, np.column_stack([(piece - centre) @ s / width + .5, (piece - centre) @ t / height + .5])))
    return found


def overlay_pieces(overlay, faces):
    """An info_overlay (doverlay_t) on its listed faces: its quad (uv points in its basis, the basis's u axis in their z
    components, overlay.cpp COverlay::Init) projected along the basis normal onto each face and cut to it. `faces`:
    {face id: (corners, normal)}. Returns [(face id, points, st)], st interpolated across the quad."""
    normal = np.asarray(overlay['normal'], float)
    u = np.array([p[2] for p in overlay['uv_points'][:3]], float)
    if np.linalg.norm(u) < 1e-6 or np.linalg.norm(normal) < 1e-6:
        return []
    normal, u = normal / np.linalg.norm(normal), u / np.linalg.norm(u)
    v = np.cross(normal, u) * (-1 if overlay['uv_points'][3][2] == 1 else 1)  # vbsp: uv3.z 1 marks a flipped BasisV
    corners2 = np.array([p[:2] for p in overlay['uv_points']], float)
    quad = np.asarray(overlay['origin'], float) + corners2[:, :1] * u + corners2[:, 1:] * v
    (u0, u1), (v0, v1) = overlay['u'], overlay['v']
    st = np.array([(u0, v0), (u0, v1), (u1, v1), (u1, v0)], float)
    # Affine map from the quad's plane (corners 0, 1, 3) to its texture coordinates; ponytail: exact for parallelograms.
    a, b = quad[1] - quad[0], quad[3] - quad[0]
    gram = np.linalg.inv(np.array([[a @ a, a @ b], [a @ b, b @ b]])) if abs(np.cross(a, b) @ normal) > 1e-6 else None
    found = []
    for face in overlay['faces']:
        if face not in faces or gram is None:
            continue
        corners, face_normal = faces[face]
        along = normal @ face_normal
        if abs(along) < 1e-3:
            continue
        # The quad dropped onto the face's plane along the overlay normal, then the face cut to it.
        dropped = quad - normal[None] * (((quad - corners[0]) @ face_normal) / along)[:, None]
        # Keep the side of each quad edge toward its inside (cross(normal, edge) points in when the quad turns
        # counter-clockwise about the face normal).
        spin = 1 if np.cross(dropped[1] - dropped[0], dropped[2] - dropped[1]) @ face_normal > 0 else -1
        planes = []
        for k in range(4):
            inward = np.cross(face_normal, dropped[(k + 1) % 4] - dropped[k]) * spin
            length = np.linalg.norm(inward)
            if length > 1e-9:
                planes.append((-inward / length, -(inward / length) @ dropped[k]))
        piece = clip(corners, planes)
        if len(piece) < 3:
            continue
        lifted = piece + normal[None] * (((quad[0] - piece) @ normal))[:, None]  # back on the quad's plane
        local = lifted - quad[0]
        coefficients = (np.column_stack([local @ a, local @ b])) @ gram
        found.append((face, piece, st[0] + coefficients[:, :1] * (st[1] - st[0]) + coefficients[:, 1:] * (st[3] - st[0])))
    return found


# --- Sprites -------------------------------------------------------------------------------------------------------
# Render modes (const.h): 0 normal, 1 transColor, 2 transTexture, 3 glow, 4 transAlpha, 5 transAdd, 9 worldGlow.
GLOW_MODES, ADDITIVE_MODES = {3, 9}, {3, 5, 9}


def sprites(entities):
    """[(material, origin, full width or None (the texture's width x scale), aspect or None, rgba, blend, glow, framerate)]
    for env_sprite (on at start: SF_SPRITE_STARTON or no targetname, sprite.cpp CSprite::Spawn), env_lightglow (its glow
    material, horizontal x vertical glow size, c_lightglow.cpp) and point_spotlight halos that start on (light_glow03 at
    60 units, point_spotlight.cpp; the beam is not drawn). Glow modes draw over the level and fade when their centre is
    hidden, as the engine's pixel-visibility test fades them."""
    found = []
    for e in entities:
        cls = e.get('classname', '').lower()
        hidden = _number(e, 'starthidden') or _number(e, 'renderamt', 255) <= 0
        colour = list(_vector(e, 'rendercolor') / 255) if 'rendercolor' in e else [1., 1., 1.]
        if cls == 'env_sprite' and not hidden and e.get('model', '').lower().endswith(('.vmt', '.spr')):
            if e.get('targetname') and not int(_number(e, 'spawnflags')) & 1:
                continue  # Waits for a TurnOn.
            mode, amount = int(_number(e, 'rendermode')), _number(e, 'renderamt', 255) / 255
            material = e['model'][:-4]
            if mode in GLOW_MODES:
                # C_SpriteRenderer::DrawSprite scales a glow's colour by the blend (renderamt) x GlowBlend, which is
                # renderamt again for kRenderFxNoDissipation (14), else the distance fade (1 within 1200 units)
                # (SDK 2013 c_sprite.cpp StandardGlowBlend). Bloodlines' sm_pier_1 fire glow (40, fx 14) leaves the arch
                # beside it unlit in the game. ponytail: no fade beyond 1200 and no fixed screen size (scale x dist / 200).
                colour = [c * amount * (amount if int(_number(e, 'renderfx')) == 14 else 1) for c in colour]
            found.append((material, _vector(e), None, _number(e, 'scale', 1) or 1, colour + [amount],
                          'add' if mode in ADDITIVE_MODES else 'alpha', mode in GLOW_MODES, _number(e, 'framerate', 10)))
        elif cls == 'env_lightglow' and not hidden:
            width, height = _number(e, 'horizontalglowsize', 30), _number(e, 'verticalglowsize', 30)
            found.append(('sprites/light_glow02_add_noz', _vector(e), 2 * width, height / max(width, 1e-6), colour + [1.], 'add', True, 0))
        elif cls == 'point_spotlight' and int(_number(e, 'spawnflags')) & 1 and not hidden:
            found.append(('sprites/light_glow03', _vector(e), 60., 1., colour + [_number(e, 'renderamt', 255) / 255], 'add', True, 0))
    return found


# --- Particles -----------------------------------------------------------------------------------------------------
def _axes(angles):
    """AngleVectors' forward and right (Source: pitch, yaw, roll in degrees)."""
    p, y, r = np.radians(angles)
    forward = [math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p)]
    right = [-math.sin(r) * math.sin(p) * math.cos(y) + math.cos(r) * math.sin(y),
             -math.sin(r) * math.sin(p) * math.sin(y) - math.cos(r) * math.cos(y), -math.sin(r) * math.cos(p)]
    return forward, right


def particles(entities, bounds, light=None):
    """[(material, template, emitter)] for static/particles.js, map units and axes; an emitter lacks only its template
    index and batch. bounds(entity) gives a brush entity's world (mins, maxs) or None; light(origin) the world light
    there as an rgb multiplier (WorldGetLightForPoint), or None for white. SDK 2013 client code as rules:
    func_dustmotes (c_func_dust.cpp): SpawnRate motes a second anywhere in the brush's box, LifetimeMin-Max seconds,
      fading in and out as sin^2 over the life, Color x Alpha, a constant size on screen (SizeMin-Max / 10000 of the
      depth), fainter with distance to 0 at DistMax; Frozen ones are SpawnRate motes that stay. Their x/y speed decays
      to the (absent) wind at 50 units/s/s within a frame or two, so only FallSpeed moves them. ponytail: the box, not
      the brush's own solid, is where they spawn. StartDisabled ones wait for a TurnOn.
    env_steam (c_steamjet.cpp), InitialState on, not the heat-wave type: Rate puffs a second along the forward at
      Speed, spread SpreadSpeed either way right and up, living JetLength / Speed seconds, StartSize growing by
      (EndSize - StartSize) a second (FLerp by the lifetime in seconds), alpha renderamt x sin(pi t), coloured
      rendercolor x the world light (one sample at the jet, not its ramp along the jet), rolling at up to RollSpeed."""
    found = []
    for e in entities:
        cls = e.get('classname', '').lower()
        if _number(e, 'starthidden'):
            continue
        if cls == 'func_dustmotes' and not _number(e, 'startdisabled'):
            box = bounds(e)
            if box is None:
                continue
            mins, maxs = (np.asarray(v, float) for v in box)
            middle = (mins + maxs) / 2
            rate = max(int(_number(e, 'spawnrate', 40)), 0)
            if not rate:
                continue
            colour = list(_vector(e, 'color') / 255) if 'color' in e else [1., 1., 1.]
            alpha, frozen, far = _number(e, 'alpha', 255) / 255, bool(_number(e, 'frozen')), _number(e, 'distmax', 1024)
            size = [_number(e, 'sizemin', 10) / 1e4, _number(e, 'sizemax', 20) / 1e4]
            template = dict(count=[rate, rate] if frozen else [1, 1], origin=[(mins - middle).tolist(), (maxs - middle).tolist()],
                            life=[1e6, 1e6] if frozen else [_number(e, 'lifetimemin', 5), _number(e, 'lifetimemax', 10)],
                            velocity=[[0, 0, -_number(e, 'fallspeed')]] * 2, worldOrigin=True, worldVelocity=True, screenSize=True,
                            size=dict(start=size, end=size), rgb=dict(start=[colour, colour]), cullRange=far, fade=far,
                            alpha=dict(start=[alpha] * 2) if frozen else dict(start=[0, 0], end=[alpha] * 2, curve=dict(kind='sine', parm=2)))
            found.append((e.get('spritename') or 'particle/sparkles', template,
                          dict(origin=middle.tolist(), forward=[1, 0, 0], right=[0, -1, 0], period=1e6 if frozen else 1 / rate)))
        elif cls == 'env_steam' and _number(e, 'initialstate') and not _number(e, 'type'):
            speed, spread = _number(e, 'speed', 120) or 120, _number(e, 'spreadspeed', 15)
            life, start, end = (_number(e, 'jetlength', 80) or 80) / speed, _number(e, 'startsize', 10), _number(e, 'endsize', 25)
            rate, amount, roll = _number(e, 'rate', 26), _number(e, 'renderamt', 255) / 255, _number(e, 'rollspeed', 8)
            if rate <= 0:
                continue
            colour = _vector(e, 'rendercolor') / 255 if 'rendercolor' in e else np.ones(3)
            if light:
                colour = colour * light(_vector(e))
                colour = colour / max(1., colour.max())
            forward, right = _axes(_vector(e, 'angles'))
            template = dict(count=[1, 1], life=[life, life], velocity=[[speed, -spread, -spread], [speed, spread, spread]],
                            rotation=[0, 360], spin=[-roll, roll], size=dict(start=[start] * 2, end=[start + (end - start) * life] * 2, curve=dict(linear=True)),
                            alpha=dict(start=[0, 0], end=[amount] * 2, curve=dict(kind='sine', parm=1)), rgb=dict(start=[colour.tolist()] * 2))
            found.append(('particle/particle_smokegrenade', template,
                          dict(origin=_vector(e).tolist(), forward=forward, right=right, period=1 / rate)))
    return found


def particle_data(found, material, missing_texture, scale):
    """map.particles from particles(): one template per emitter, one batch (a stage) per material that loads; the page
    axes are the map's [y, z, x] x scale, as the importers place everything else."""
    templates, batches, emitters = [], {}, []
    for name, template, emitter in found:
        if name not in batches:
            mat = material(name)
            batches[name] = None if mat['texture'] == missing_texture else dict(
                map=mat['texture'], blend=mat['settings'].get('blend') or ['gl_src_alpha', 'gl_one_minus_src_alpha'],
                rgbGen=['vertex'], alphaGen=['vertex'])
        if batches[name]:
            templates.append(template)
            emitters.append(dict(emitter, template=len(templates) - 1, batch=name))
    if not emitters:
        return None
    return dict(templates=templates, batches={k: v for k, v in batches.items() if v}, emitters=emitters,
                toWorld=[0, 0, scale, scale, 0, 0, 0, scale, 0], up=[0, 0, 1])


# --- Brushes that turn ---------------------------------------------------------------------------------------------
def rotating(entity, speed_key='maxspeed'):
    """A func_rotating that starts on (SF_BRUSH_ROTATE_START_ON 1) as a spin [x, y, z, radians a second] about its origin,
    map axes, or None. CFuncRotating::Spawn: angular velocity maxspeed (degrees a second) along roll (flag 4, 'Z axis'),
    pitch (flag 8, 'X axis') or yaw, reversed by flag 2; Rotation = angles + velocity x seconds. Quake II's SP_func_rotating
    reads the same flags with `speed` (speed_key)."""
    if entity.get('classname', '').lower() != 'func_rotating':
        return None
    flags = int(_number(entity, 'spawnflags'))
    speed = _number(entity, speed_key, 100) or 100
    if not flags & 1 or speed <= 0:
        return None
    rate = np.array([0., 0., 1.] if flags & 4 else [1., 0., 0.] if flags & 8 else [0., 1., 0.]) * speed * (-1 if flags & 2 else 1)
    start, dt = _vector(entity, 'angles'), 1e-3
    turn = rotation(start + rate * dt) @ rotation(start).T
    w = np.array([turn[2, 1] - turn[1, 2], turn[0, 2] - turn[2, 0], turn[1, 0] - turn[0, 1]]) / 2
    if np.linalg.norm(w) < 1e-12:
        return None
    return [*(w / np.linalg.norm(w)), math.asin(min(np.linalg.norm(w), 1)) / dt]

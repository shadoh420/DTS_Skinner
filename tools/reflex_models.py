"""Reflex Arena models for the Reflex Maps page: the game's .effect files (what an Effect entity or a pickup places)
and the .mesh files they name, read from the zip archives of its base folder and written for the page as
effects.json and one small binary per mesh (models/<mesh>.bin). Only what the page draws is kept: each mesh's
finest level of detail, and the lights and particle emitters an effect carries.

.mesh (version 0x23, magic 0xd00a; little-endian): u16 version, u16 magic, u32 material count, f32 bounds (min xyz,
max xyz), u32 bone count, u32 (0 or 1), u32 level-of-detail bits (9: one level, 11: two, 15: three: the count is the
number of bits set, less one), then the material names, each ended by a zero byte. Then per material one block per
level of detail and one more (positions only, on the first material: the shadow mesh; empty on the others). A
block: u32 vertex count, u32 index count, u32 vertex format, u32 4, the vertices, then u16 indices (triangles).
Formats: 0x1d, 52 bytes (position, colour as BGRA bytes, normal, texture coordinates, tangent and sign); 0x3d, 72
(the same, then four bone weights and four bone indices as bytes); 0x15, 44 (no texture coordinates); 0, 12 and 0x20,
32 (shadow meshes). Then the bones: every parent (i32), every name ended by a zero byte, every 4 x 4 matrix (b_light,
b_light2 …, which lights hang from), then every bone's pose as a 4 x 4 matrix (a spot light shines along its x).

.effect (version 0x33, magic 0xd00c): u16 version, u16 magic, u32 0x945893a8, three record counts and the offsets
of three lists of records (the same effect for three quality settings; the first is read). A record's first byte
is its type and fixes its length: 1 mesh (2141 bytes), 2 particle emitter (525), 3 point light (169), 4 spot light
(185); 0 and 5 to 9 are ribbons and the like. A particle emitter: its material at +1 (128 bytes), lifetime (least,
most) at +261, particles a second at +301, size at birth (least, most) at +309 and at death at +317, colour (four
floats) at birth at +333 and at death at +349, the bone it emits from at +389 (128 bytes). A mesh record: its mesh at +1 (128 bytes, without .mesh); a material for the whole mesh
at +385 (pickup holograms, pads' glow); per material of the mesh, eight of each: a colour at +769 (four floats, alpha
0 for none; a glow's), a colour at +897 (the albedo the game draws its material in, as a face's colour: p_metal is
a paint, green until given one) and the material at +1025 (128 bytes, empty to keep the mesh's: its names are often
placeholders, MaterialA); its scale at +2117. A light record: colour at +1 (four
floats), intensity +17, near +21 and far +25 attenuation; a point light's bone at +33, a spot light's inner and outer
angles (degrees) at +37 and +41 and its bone at +45 (128 bytes).
"""
import json
import re
import struct

MESH_MAGIC = b'\x23\x00\x0a\xd0'
EFFECT_MAGIC = b'\x33\x00\x0c\xd0'
STRIDE = {0x1d: 52, 0x3d: 72, 0x15: 44, 0x20: 32, 0: 12}
SHADOW_PART = 255  # The material slot the page's binary gives a mesh's shadow mesh.
RECORD = {0: 361, 1: 2141, 2: 525, 3: 169, 4: 185, 5: 285, 6: 321, 7: 433, 8: 165, 9: 2}

# The effect each pickup type draws (pickupType: the numbers the page names them by).
def pickup_pad(effect):
    """The pad a pickup's effect stands on: internal/items/health/health_25_pad, internal/weapons/shotgun/shotgun_pad."""
    return re.sub(r'_pickup$', '', effect) + '_pad'


# What the game draws for entities that are not Effects: in its editor only, a reflection probe. (A Teleporter shows
# nothing of its own, seen in the game: the stock maps place their portals as Effects.)
ENTITY_EFFECTS = {'ReflectionProbe': 'internal/misc/reflectionprobe'}
# What the game's editor shows for an entity (seen in its editor: a target is a red flag, a point light a blue diamond,
# an effect with no model a red "!"); a mesh named where an effect goes is drawn as it is. NavLink: its start, and
# with isStart 0 its end. Reflection probes show a mirror sphere, which the page makes.
EDITOR_EFFECTS = {'PlayerSpawn': 'internal/editor/playerspawn', 'Target': 'internal/editor/target', 'PointLight': 'internal/editor/light',
                  'Effect': 'internal/editor/mesh', 'WorkshopScreenshot': 'internal/editor/workshophcreenshot',
                  'NavLink': 'internal/editor/nav_offmesh_start', 'NavLinkEnd': 'internal/editor/nav_offmesh_target'}
# The game's sky: its cloud dome (a mesh; the page draws the sky's colours itself).
SKY_MESHES = {'clouds': 'internal/world/skies/sky_clouds1'}
# The material the game's editor draws a volume with (race starts and finishes it does not draw).
VOLUME_MATERIALS = {'Teleporter': 'internal/editor/textures/editor_teleport', 'JumpPad': 'internal/editor/textures/editor_jumppad',
                    'TriggerVolume': 'internal/editor/textures/editor_trigger'}
PICKUP_EFFECTS = {
    0: 'internal/weapons/burstgun/burstgun_pickup', 1: 'internal/weapons/shotgun/shotgun_pickup',
    2: 'internal/weapons/grenadelauncher/grenadelauncher_pickup', 3: 'internal/weapons/plasmarifle/plasmarifle_pickup',
    4: 'internal/weapons/rocketlauncher/rocketlauncher_pickup', 5: 'internal/weapons/ioncannon/ioncannon_pickup',
    6: 'internal/weapons/boltrifle/boltrifle_pickup', 7: 'internal/weapons/stakelauncher/stakelauncher_pickup',
    20: 'internal/items/ammo/ammo_burst', 21: 'internal/items/ammo/ammo_shells', 22: 'internal/items/ammo/ammo_grenades',
    23: 'internal/items/ammo/ammo_plasma', 24: 'internal/items/ammo/ammo_rockets', 25: 'internal/items/ammo/ammo_ic',
    26: 'internal/items/ammo/ammo_bolt', 27: 'internal/items/ammo/ammo_stake',
    40: 'internal/items/health/health_5', 41: 'internal/items/health/health_25', 42: 'internal/items/health/health_50',
    43: 'internal/items/health/health_mega', 50: 'internal/items/armor_shard/armor_shard', 51: 'internal/items/armor/armor_green',
    52: 'internal/items/armor/armor_yellow', 53: 'internal/items/armor/armor_red', 60: 'internal/items/powerup_quad/powerup_quad',
    61: 'internal/items/powerup_protect/powerup_protect', 70: 'internal/items/flags/flag_plain', 71: 'internal/items/flags/flag_plain',
    80: 'internal/items/training_token/training_token',
}


def _name(raw, at, length=128):
    return raw[at:at + length].split(b'\0', 1)[0].decode('latin-1')


def read_mesh(raw):
    """{materials, parts: [{material, positions, normals, colours, indices}], bones: {name: 4 x 4 rows}} of a .mesh,
    its finest level of detail only. Positions and normals are flat lists of floats, colours BGRA bytes (the editor's
    target, red in the game, is (0, 28, 180))."""
    if raw[:4] != MESH_MAGIC:
        raise ValueError('not a Reflex mesh')
    count = struct.unpack_from('<I', raw, 4)[0]
    bones, _, lod_bits = struct.unpack_from('<3I', raw, 32)
    levels = max(1, bin(lod_bits).count('1') - 1)
    at, materials = 44, []
    for _ in range(count):
        end = raw.index(b'\0', at)
        materials.append(raw[at:end].decode('latin-1'))
        at = end + 1
    parts = []
    for material in range(count):
        for level in range(levels + 1):
            vertices, indices, fmt = struct.unpack_from('<3I', raw, at)
            stride = STRIDE.get(fmt)
            if stride is None:
                raise ValueError(f'vertex format {fmt:#x} is not read')
            body = at + 16
            end = body + vertices * stride + indices * 2
            if end > len(raw):
                raise ValueError('mesh is cut short')
            if level == 0 and vertices and fmt & 1:
                positions, normals, colours, uvs = [], [], bytearray(), []
                # Texture coordinates follow the normal where the format has them (bit 8).
                layout = f'<3f4s3f2f{stride - 36}x' if fmt & 8 else f'<3f4s3f{stride - 28}x'
                for x, y, z, rgba, nx, ny, nz, *uv in struct.iter_unpack(layout, raw[body:body + vertices * stride]):
                    positions += (x, y, z)
                    uvs += uv or (0, 0)
                    colours += rgba
                    normals += (nx, ny, nz)
                index = list(struct.unpack_from(f'<{indices}H', raw, body + vertices * stride))
                part = dict(material=material, positions=positions, normals=normals, colours=bytes(colours), uvs=uvs, indices=index)
                if fmt & 0x20:  # Skinned: four bone weights and four bone numbers end each vertex.
                    part['skin'] = [struct.unpack_from('<4f4B', raw, body + v * stride + stride - 20) for v in range(vertices)]
                parts.append(part)
            if level == levels and material == 0:
                # The shadow mesh (positions, and bone weights where skinned): what the game casts sun shadows with.
                positions = [c for v in range(vertices) for c in struct.unpack_from('<3f', raw, body + v * stride)]
                part = dict(material=SHADOW_PART, positions=positions, normals=[0.0] * (3 * vertices), colours=bytes(4 * vertices),
                            uvs=[0.0] * (2 * vertices), indices=list(struct.unpack_from(f'<{indices}H', raw, body + vertices * stride)))
                if fmt & 0x20:
                    part['skin'] = [struct.unpack_from('<4f4B', raw, body + v * stride + stride - 20) for v in range(vertices)]
                parts.append(part)
            at = end
    # Bones: every parent (i32), then every name, then a 4 x 4 matrix each (bind), then a 4 x 4 matrix each (its
    # pose: rows, the place in the last; a light shines along the bone's x axis, its first row).
    at += 4 * bones
    names = []
    for _ in range(bones):
        end = raw.index(b'\0', at)
        names.append(raw[at:end].decode('latin-1'))
        at = end + 1
    binds = [struct.unpack_from('<16f', raw, at + 64 * k) for k in range(bones)]
    at += 64 * bones
    poses = [struct.unpack_from('<16f', raw, at + 64 * k) for k in range(bones)]
    named = {name: dict(position=list(pose[12:15]), axis=list(pose[0:3])) for name, pose in zip(names, poses)}
    # A skinned mesh is drawn in its pose: each vertex (a row) times its bones' bind and pose matrices, by weight.
    skins = [[sum(bind[r * 4 + k] * pose[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)] for bind, pose in zip(binds, poses)]
    for part in parts:
        for v, (*weights, b0, b1, b2, b3) in enumerate(part.pop('skin', [])):
            p, n = part['positions'][v * 3:v * 3 + 3], part['normals'][v * 3:v * 3 + 3]
            moved, turned = [0, 0, 0], [0, 0, 0]
            for weight, bone in zip(weights, (b0, b1, b2, b3)):
                if weight <= 0 or bone >= len(skins):
                    continue
                m = skins[bone]
                for c in range(3):
                    moved[c] += weight * (p[0] * m[c] + p[1] * m[4 + c] + p[2] * m[8 + c] + m[12 + c])
                    turned[c] += weight * (n[0] * m[c] + n[1] * m[4 + c] + n[2] * m[8 + c])
            if any(weights):
                length = sum(c * c for c in turned) ** .5 or 1
                part['positions'][v * 3:v * 3 + 3] = moved
                part['normals'][v * 3:v * 3 + 3] = [c / length for c in turned]
    return dict(materials=materials, parts=parts, bones=named)


def read_effect(raw):
    """{meshes: [{mesh, materials, colours, scale}], lights: [{kind, colour, intensity, near, far, inner, outer,
    bone}]} of an .effect, from its first list of records."""
    if raw[:4] != EFFECT_MAGIC:
        raise ValueError('not a Reflex effect')
    count = struct.unpack_from('<I', raw, 8)[0]
    at = struct.unpack_from('<I', raw, 20)[0]
    meshes, lights, particles = [], [], []
    for _ in range(count):
        kind = raw[at]
        if kind not in RECORD or at + RECORD[kind] > len(raw):
            break  # A record type not known here (10: cloth); what came before it is kept.
        record = raw[at:at + RECORD[kind]]
        if kind == 1:
            colour = lambda at: (lambda c: c if c[3] > 0 else None)(list(struct.unpack_from('<4f', record, at)))
            whole = _name(record, 385)
            meshes.append(dict(mesh=_name(record, 1), materials=[whole or _name(record, 1025 + 128 * k) for k in range(8)],
                               colours=[colour(897 + 16 * k) or colour(769 + 16 * k) for k in range(8)], scale=struct.unpack_from('<f', record, 2117)[0] or 1))
        elif kind == 2:
            # How many are alive at once: particles a second times their mean lifetime.
            particles.append(dict(material=_name(record, 1), size=sum(struct.unpack_from('<2f', record, 309)) / 2,
                                  alive=struct.unpack_from('<f', record, 301)[0] * sum(struct.unpack_from('<2f', record, 261)) / 2,
                                  colour=list(struct.unpack_from('<4f', record, 333)), bone=_name(record, 389)))
        elif kind in (3, 4):
            colour = list(struct.unpack_from('<3f', record, 1))
            intensity, near, far = struct.unpack_from('<3f', record, 17)
            light = dict(kind='point' if kind == 3 else 'spot', colour=colour, intensity=intensity, near=near, far=far)
            if kind == 4:
                light.update(inner=struct.unpack_from('<f', record, 37)[0], outer=struct.unpack_from('<f', record, 41)[0], bone=_name(record, 45))
            else:
                light['bone'] = _name(record, 33)
            lights.append(light)
        at += RECORD[kind]
    return dict(meshes=meshes, lights=lights, particles=particles)


def mesh_file(name):
    return re.sub(r'[^a-z0-9_.-]', '~', name.lower()) + '.bin'


def write_mesh(mesh, path):
    """The page's binary of a mesh: u32 part count, then per part u32 material slot, vertex count, index count and
    its float32 positions, float32 normals, float32 texture coordinates, BGRA bytes and u32 indices. The shadow mesh
    is the part of slot 255 (no normals, texture coordinates or colours to speak of; empty where it casts none)."""
    out = bytearray(struct.pack('<I', len(mesh['parts'])))
    for part in mesh['parts']:
        vertices = len(part['positions']) // 3
        out += struct.pack('<3I', part['material'], vertices, len(part['indices']))
        out += struct.pack(f'<{vertices * 3}f', *part['positions'])
        out += struct.pack(f'<{vertices * 3}f', *part['normals'])
        out += struct.pack(f'<{vertices * 2}f', *part['uvs'])
        out += part['colours']
        out += struct.pack(f'<{len(part["indices"])}I', *part['indices'])
    path.write_bytes(bytes(out))


def export_models(find, names, output, resolve=lambda name: name):
    """Reads the effects `names` (and the meshes they name) with `find(path)`, which gives a file's bytes or None,
    and writes output/effects.json and output/models/*.bin; `resolve` gives a material's full name for a bare one (a mesh
    may name ribbon_strokes_tele_orange). Returns the materials the models use and what failed."""
    (output / 'models').mkdir(parents=True, exist_ok=True)
    effects, meshes, failed, materials = {}, {}, {}, set()
    for name in sorted(names):
        raw = find(name + '.effect')
        if raw is None and find(name + '.mesh') is None:
            failed[name] = 'no such effect'
            continue
        try:
            # A map may name a mesh where an effect goes; the game draws the mesh.
            effect = read_effect(raw) if raw is not None else dict(meshes=[dict(mesh=name, materials=[''] * 8, colours=[None] * 8, scale=1)], lights=[], particles=[])
        except (ValueError, struct.error) as exc:
            failed[name] = str(exc)
            continue
        for record in effect['meshes']:
            mesh_name = record['mesh']
            if mesh_name not in meshes:
                raw_mesh = find(mesh_name + '.mesh')
                try:
                    mesh = read_mesh(raw_mesh) if raw_mesh is not None else None
                except (ValueError, struct.error) as exc:
                    failed[mesh_name] = str(exc)
                    mesh = None
                if mesh is not None:
                    write_mesh(mesh, output / 'models' / mesh_file(mesh_name))
                    meshes[mesh_name] = dict(file=mesh_file(mesh_name), materials=mesh['materials'],
                                             bones=mesh['bones'])
                else:
                    meshes[mesh_name] = None
                    failed.setdefault(mesh_name, 'no such mesh')
            known = meshes[mesh_name]
            if known:
                used = [resolve((record['materials'][k] if k < 8 else '') or m) for k, m in enumerate(known['materials'])]
                record['materials'] = used
                materials.update(used)
            # Lights hang from a bone of the effect's mesh: its place in the mesh is where the light is, and a spot
            # shines along its x axis.
            for light in effect['lights']:
                if known and light.get('bone') in known['bones']:
                    bone = known['bones'][light['bone']]
                    light['position'] = bone['position']
                    if light['kind'] == 'spot':
                        length = sum(c * c for c in bone['axis']) ** .5
                        light['direction'] = [c / length for c in bone['axis']] if length else [0, -1, 0]
            # Particle emitters too.
            for particle in effect['particles']:
                if known and particle.get('bone') in known['bones']:
                    particle['position'] = known['bones'][particle['bone']]['position']
        for particle in effect['particles']:
            particle['material'] = resolve(particle['material'])
            materials.add(particle['material'])
        effect['meshes'] = [record for record in effect['meshes'] if meshes.get(record['mesh'])]
        for record in effect['meshes']:
            record['file'] = meshes[record['mesh']]['file']
        effects[name] = effect
    (output / 'effects.json').write_text(json.dumps(dict(effects=effects, pickups=PICKUP_EFFECTS, entities=ENTITY_EFFECTS, editor=EDITOR_EFFECTS,
                                                    volumes=VOLUME_MATERIALS, sky=SKY_MESHES),
                                                    separators=(',', ':')), encoding='utf-8')
    return materials, failed

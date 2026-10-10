"""Static frame-zero MD5Version 10 models from the Quake rerelease.

Imported by import_quake.py --install <Quake folder> --output <data/quake>.
Mesh joints are absolute; animation baseframes are parent-relative. Exporter
scale comments describe values already baked into the file, not runtime scale.
"""
import math
import re
import struct

if __package__:
    from .import_quake import palette_image
else:
    from import_quake import palette_image


class Tokens:
    def __init__(self, data):
        try:
            source = data.decode('utf-8-sig')
        except UnicodeError as exc:
            raise ValueError('MD5 text is not UTF-8') from exc
        self.tokens = iter(re.findall(r'//[^\r\n]*|"[^"\r\n]*"|[^\s{}()]+|[{}()]', source))
        self.expect('MD5Version')
        if self.integer() != 10:
            raise ValueError('Expected MD5Version 10')
        self.expect('commandline')
        self.string()

    def take(self):
        for value in self.tokens:
            if not value.startswith('//'):
                return value
        raise ValueError('Truncated MD5')

    def expect(self, value):
        if self.take() != value:
            raise ValueError(f'Expected MD5 {value}')

    def integer(self):
        return int(self.take())

    def number(self):
        value = float(self.take())
        if not math.isfinite(value):
            raise ValueError('Non-finite MD5 number')
        return value

    def string(self):
        value = self.take()
        if not (value.startswith('"') and value.endswith('"')):
            raise ValueError('Expected quoted MD5 string')
        return value[1:-1]

    def count(self, name):
        self.expect(name)
        value = self.integer()
        if value < 0 or value > 1000000:
            raise ValueError(f'Invalid MD5 {name}')
        return value

    def vector(self, size=3):
        self.expect('(')
        result = tuple(self.number() for _ in range(size))
        self.expect(')')
        return result

    def end(self):
        if any(not value.startswith('//') for value in self.tokens):
            raise ValueError('Trailing MD5 data')


def quaternion(xyz):
    length = sum(v * v for v in xyz)
    if length > 1.001:
        raise ValueError('Invalid MD5 quaternion')
    q = (*xyz, -math.sqrt(max(0, 1 - length)))
    norm = math.sqrt(sum(v * v for v in q))
    return tuple(v / norm for v in q)


def multiply(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return (w*X + x*W + y*Z - z*Y, w*Y - x*Z + y*W + z*X,
            w*Z + x*Y - y*X + z*W, w*W - x*X - y*Y - z*Z)


def rotate(q, point):
    return multiply(multiply(q, (*point, 0)), (-q[0], -q[1], -q[2], q[3]))[:3]


def read_mesh(data):
    t = Tokens(data)
    joint_count, mesh_count = t.count('numJoints'), t.count('numMeshes')
    if not joint_count or not mesh_count:
        raise ValueError('Empty MD5 mesh')
    t.expect('joints')
    t.expect('{')
    joints = []
    for i in range(joint_count):
        name, parent, position, orientation = t.string(), t.integer(), t.vector(), quaternion(t.vector())
        if not -1 <= parent < joint_count or parent == i:
            raise ValueError('Invalid MD5 joint parent')
        joints.append(dict(name=name, parent=parent, position=position, orientation=orientation))
    t.expect('}')
    meshes = []
    for _ in range(mesh_count):
        t.expect('mesh')
        t.expect('{')
        t.expect('shader')
        shader = t.string()
        verts, triangles, weights = [], [], []
        for i in range(t.count('numverts')):
            t.expect('vert')
            if t.integer() != i:
                raise ValueError('Invalid MD5 vertex index')
            verts.append((t.vector(2), t.integer(), t.integer()))
        for i in range(t.count('numtris')):
            t.expect('tri')
            if t.integer() != i:
                raise ValueError('Invalid MD5 triangle index')
            tri = tuple(t.integer() for _ in range(3))
            if any(v < 0 or v >= len(verts) for v in tri):
                raise ValueError('Invalid MD5 triangle vertex')
            triangles.append(tri)
        for i in range(t.count('numweights')):
            t.expect('weight')
            if t.integer() != i:
                raise ValueError('Invalid MD5 weight index')
            joint, bias, position = t.integer(), t.number(), t.vector()
            if not 0 <= joint < joint_count or not 0 <= bias <= 1:
                raise ValueError('Invalid MD5 weight')
            weights.append((joint, bias, position))
        for uv, start, count in verts:
            if start < 0 or count < 1 or start + count > len(weights):
                raise ValueError('Invalid MD5 weight range')
            if abs(sum(w[1] for w in weights[start:start+count]) - 1) > .01:
                raise ValueError('MD5 vertex weights do not sum to one')
        t.expect('}')
        meshes.append(dict(shader=shader, verts=verts, triangles=triangles, weights=weights))
    t.end()
    return dict(joints=joints, meshes=meshes)


def read_anim(data, mesh_joints):
    """Return frame zero as absolute joints, validating matching joint indices.

    mg3's ogre_rocket has 55 mesh joints but only 39 animated joints. Its weights
    use that matching prefix; the animation supplies the hierarchy and units.
    skin_points rejects any weight outside the animation's joint array.
    """
    t = Tokens(data)
    frames, count = t.count('numFrames'), t.count('numJoints')
    rate, components = t.count('frameRate'), t.count('numAnimatedComponents')
    if not frames or not rate or not 0 < count <= len(mesh_joints):
        raise ValueError('Invalid MD5 animation counts')
    t.expect('hierarchy')
    t.expect('{')
    hierarchy = []
    for i in range(count):
        name, parent, flags, start = t.string(), t.integer(), t.integer(), t.integer()
        if (name != mesh_joints[i]['name'] or not -1 <= parent < i or
                not 0 <= flags <= 63 or start < 0 or start + flags.bit_count() > components):
            raise ValueError('MD5 animation hierarchy does not match mesh')
        hierarchy.append((parent, flags, start))
    t.expect('}')
    t.expect('bounds')
    t.expect('{')
    bounds = [(t.vector(), t.vector()) for _ in range(frames)]
    t.expect('}')
    t.expect('baseframe')
    t.expect('{')
    base = [(*t.vector(), *t.vector()) for _ in range(count)]
    t.expect('}')
    first = None
    for i in range(frames):
        t.expect('frame')
        if t.integer() != i:
            raise ValueError('Invalid MD5 animation frame index')
        t.expect('{')
        values = [t.number() for _ in range(components)]
        t.expect('}')
        if i == 0:
            first = values
    t.end()
    joints = []
    for i, (parent, flags, start) in enumerate(hierarchy):
        values = list(base[i])
        for bit in range(6):
            if flags & (1 << bit):
                values[bit] = first[start]
                start += 1
        position, orientation = values[:3], quaternion(values[3:])
        if parent >= 0:
            p = joints[parent]
            position = tuple(a + b for a, b in zip(p['position'], rotate(p['orientation'], position)))
            orientation = multiply(p['orientation'], orientation)
        joints.append(dict(mesh_joints[i], position=position, orientation=orientation))
    return dict(joints=joints, frames=frames, frame_rate=rate, bounds=bounds[0],
                hierarchy_differs=count != len(mesh_joints) or any(
                    h[0] != mesh_joints[i]['parent'] for i, h in enumerate(hierarchy)))


def skin_points(mesh, joints):
    points = []
    for uv, start, count in mesh['verts']:
        point = [0., 0., 0.]
        for joint, bias, offset in mesh['weights'][start:start+count]:
            if joint >= len(joints):
                raise ValueError('MD5 animation lacks a weighted mesh joint')
            transform = joints[joint]
            rotated = rotate(transform['orientation'], offset)
            for k in range(3):
                point[k] += bias * (transform['position'][k] + rotated[k])
        points.append(tuple(point))
    return points


def resolve_skin(shader, files):
    """KEX ParseShader names skin/frame zero {}_{:02}_{:02}.lmp in progs/."""
    stem = shader.lower().replace('\\', '/')
    if not stem.startswith('progs/'):
        stem = 'progs/' + stem
    name = stem + '_00_00.lmp'
    if name not in files:
        raise ValueError(f'Missing enhanced skin: {name}')
    data = files[name][0]
    if len(data) < 8:
        raise ValueError(f'Truncated enhanced skin: {name}')
    w, h = struct.unpack_from('<II', data)
    if not w or not h or w * h != len(data) - 8:
        raise ValueError(f'Invalid enhanced skin dimensions: {name}')
    image = palette_image(data[8:], (w, h), files['gfx/palette.lmp'][0])
    alternatives = [stem + '_00_00' + ext for ext in ('.png', '.tga') if stem + '_00_00' + ext in files]
    return image, dict(source=name, width=w, height=h, fullbright_pixels=sum(p >= 224 for p in data[8:]),
                       alternatives=alternatives)


def build_model(mesh, animation, textures):
    joints = animation['joints'] if animation else mesh['joints']
    vertices, uvs, indices, groups = [], [], [], []
    for part, texture in zip(mesh['meshes'], textures):
        offset, start = len(vertices) // 3, len(indices)
        for x, y, z in skin_points(part, joints):
            vertices.extend((y, z, x))  # Same native-unit rotation as the MDL importer.
        for uv, _, _ in part['verts']:
            uvs.extend(uv)
        for a, b, c in part['triangles']:
            indices.extend((offset+a, offset+c, offset+b))
        groups.append(dict(start=start, count=len(indices)-start, materialIndex=len(groups)))
    return dict(game='quake', winding='ccw', vertices=vertices, uvs=uvs, indices=indices,
                groups=groups, material_names=textures, material_textures=textures,
                material_settings=[{} for _ in textures],
                metadata=dict(format='md5', pose='animation frame 0' if animation else 'bind pose (no animation)',
                              frames=animation['frames'] if animation else 1, poses=1, skins=1,
                              frame_rate=animation['frame_rate'] if animation else None, scale=1,
                              mesh_joints=len(mesh['joints']), posed_joints=len(joints),
                              hierarchy_differs=animation['hierarchy_differs'] if animation else False))

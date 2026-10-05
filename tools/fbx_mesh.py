"""Binary FBX (version 7100 to 7700), or ASCII FBX (weapongl, the grenade launcher pickup), to triangles per
material, with node transforms applied as assimp does (the Diabotical import's models). Checked against the game's own compiled models (.dbm, assimp output): the bounding
boxes of 7681 of the 7927 models shipped as both match, the .dbm being the FBX with z negated; most of the rest are
old or backup files compiled from another version."""
import re
import struct
import zlib

import numpy as np

ARRAYS = {b'f': '<f4', b'd': '<f8', b'l': '<i8', b'i': '<i4', b'b': 'u1'}
ASCII_TOKEN = re.compile(r'"[^"]*"|[A-Za-z_][\w|]*:|[{}]|[^\s,{}"]+')


def read_ascii_nodes(text):
    """An ASCII FBX's node tree as read_nodes gives a binary one's: an array (`*N { a: ... }`) as one numpy value,
    an object's "Class::Name" as the binary's "Name\\x00\\x01Class"."""
    tokens, at = ASCII_TOKEN.findall(re.sub(r'(?m)^\s*;.*$', '', text)), 0

    def value(token):
        if token[0] == '"':
            word = token[1:-1]
            return '\x00\x01'.join(word.split('::', 1)[::-1]) if '::' in word else word
        for kind in (int, float):
            try:
                return kind(token)
            except ValueError:
                pass
        return token

    def nodes():
        nonlocal at
        out = []
        while at < len(tokens) and tokens[at] != '}':
            name, values, children = tokens[at][:-1], [], []
            at += 1
            while at < len(tokens) and tokens[at] not in ('{', '}') and not (tokens[at][-1] == ':' and tokens[at][0] != '"'):
                values.append(value(tokens[at]))
                at += 1
            if at < len(tokens) and tokens[at] == '{':
                at += 1
                children = nodes()
                at += 1
            if values and str(values[0]).startswith('*'):
                values, children = [np.array(children[0][1] if children else [])], []
            out.append((name, values, children))
        return out
    return nodes()


def read_nodes(data):
    """The FBX node tree: [(name, properties, children)]."""
    if data.lstrip()[:5] == b'; FBX':
        return read_ascii_nodes(data.decode('utf-8', 'replace'))
    if data[:21] != b'Kaydara FBX Binary  \x00':
        raise ValueError('not an FBX')
    version, = struct.unpack_from('<I', data, 23)
    head, head_size = ('<QQQB', 25) if version >= 7500 else ('<IIIB', 13)

    def node(at):
        end, count, _, length = struct.unpack_from(head, data, at)
        if end == 0:
            return None, at + head_size
        name = data[at + head_size:at + head_size + length].decode('latin-1')
        at += head_size + length
        values = []
        for _ in range(count):
            kind = data[at:at + 1]
            at += 1
            if kind in b'YCIFDL' and kind:
                code, size = {b'Y': ('<h', 2), b'C': ('<?', 1), b'I': ('<i', 4), b'F': ('<f', 4), b'D': ('<d', 8), b'L': ('<q', 8)}[kind]
                values.append(struct.unpack_from(code, data, at)[0])
                at += size
            elif kind in (b'S', b'R'):
                size, = struct.unpack_from('<I', data, at)
                raw = data[at + 4:at + 4 + size]
                values.append(raw.decode('utf-8', 'replace') if kind == b'S' else raw)
                at += 4 + size
            elif kind in ARRAYS:
                items, encoding, size = struct.unpack_from('<III', data, at)
                raw = data[at + 12:at + 12 + size]
                values.append(np.frombuffer(zlib.decompress(raw) if encoding else raw, ARRAYS[kind], items))
                at += 12 + size
            else:
                raise ValueError(f'FBX property type {kind!r}')
        children = []
        while at < end:
            child, at = node(at)
            if child is None:
                break
            children.append(child)
        return (name, values, children), end

    nodes, at = [], 27
    while at < len(data) - head_size:
        found, at = node(at)
        if found is None:
            break
        nodes.append(found)
    return nodes


def child(node, name):
    return next((c for c in node[2] if c[0] == name), None)


def properties(node):
    found = child(node, 'Properties70')
    return {c[1][0]: c[1][4:] for c in found[2]} if found else {}


def euler(degrees, order=0):
    """FBX rotation order 0 (XYZ: x applied first) to 5."""
    x, y, z = np.radians(degrees)
    rx = np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]])
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rz = np.array([[np.cos(z), -np.sin(z), 0], [np.sin(z), np.cos(z), 0], [0, 0, 1]])
    a, b, c = {0: (rz, ry, rx), 1: (ry, rz, rx), 2: (rz, rx, ry), 3: (rx, rz, ry), 4: (ry, rx, rz), 5: (rx, ry, rz)}[order]
    out = np.eye(4)
    out[:3, :3] = a @ b @ c
    return out


def move(v):
    out = np.eye(4)
    out[:3, 3] = v
    return out


def local_matrix(p):
    get = lambda key, default: np.array(p.get(key, default)[:3], float)
    pivot, scale_pivot = get('RotationPivot', (0, 0, 0)), get('ScalingPivot', (0, 0, 0))
    return (move(get('Lcl Translation', (0, 0, 0))) @ move(get('RotationOffset', (0, 0, 0))) @ move(pivot) @
            euler(get('PreRotation', (0, 0, 0))) @ euler(get('Lcl Rotation', (0, 0, 0)), int(p.get('RotationOrder', [0])[0])) @
            np.linalg.inv(euler(get('PostRotation', (0, 0, 0)))) @ move(-pivot) @ move(get('ScalingOffset', (0, 0, 0))) @
            move(scale_pivot) @ np.diag([*get('Lcl Scaling', (1, 1, 1)), 1]) @ move(-scale_pivot))


def geometric_matrix(p):
    get = lambda key, default: np.array(p.get(key, default)[:3], float)
    return move(get('GeometricTranslation', (0, 0, 0))) @ euler(get('GeometricRotation', (0, 0, 0))) @ np.diag([*get('GeometricScaling', (1, 1, 1)), 1])


def layer(geometry, kind, values, indices, width, corners):
    """A layer element's value per polygon corner (or None)."""
    element = child(geometry, kind)
    fields = {c[0]: c[1][0] for c in element[2] if c[1]} if element else {}
    if values not in fields:
        return None
    data = np.asarray(fields[values], float).reshape(-1, width)
    if fields.get('ReferenceInformationType') == 'IndexToDirect' and indices in fields:
        data = data[fields[indices]]
    mapping = fields.get('MappingInformationType')
    return data if mapping == 'ByPolygonVertex' else data[corners] if mapping in ('ByVertice', 'ByVertex') else None


def fbx_mesh(data):
    """{material name: (positions, normals, uvs)}, each (triangles, 3, n) float32, in the FBX's own axes."""
    nodes = read_nodes(data)
    if child(('', [], nodes), 'Objects') is None:
        raise ValueError('no objects in the FBX')
    objects = {o[1][0]: o for o in child(('', [], nodes), 'Objects')[2]}
    parent, children = {}, {}
    for link in child(('', [], nodes), 'Connections')[2]:
        if link[1][0] == 'OO':
            parent.setdefault(link[1][1], link[1][2])
            children.setdefault(link[1][2], []).append(link[1][1])
    worlds = {}

    def world(ident):
        if ident not in worlds:
            o = objects.get(ident)
            worlds[ident] = np.eye(4) if o is None or o[0] != 'Model' else world(parent.get(ident, 0)) @ local_matrix(properties(o))
        return worlds[ident]

    out = {}
    for ident, geometry in objects.items():
        model = objects.get(parent.get(ident))
        if geometry[0] != 'Geometry' or geometry[1][2] != 'Mesh' or model is None or child(geometry, 'Vertices') is None:
            continue
        materials = [objects[c][1][1].split('\x00')[0] for c in children.get(model[1][0], []) if objects.get(c, ('',))[0] == 'Material']
        matrix = world(model[1][0]) @ geometric_matrix(properties(model))
        points = child(geometry, 'Vertices')[1][0].reshape(-1, 3)
        index = child(geometry, 'PolygonVertexIndex')[1][0].astype(np.int64)
        last = index < 0
        corners = np.where(last, ~index, index)
        starts = np.r_[0, np.nonzero(last)[0][:-1] + 1]
        sizes = np.diff(np.r_[starts, len(index)])
        fans = [np.stack([np.full(n - 2, s), s + np.arange(1, n - 1), s + np.arange(2, n)], 1) for s, n in zip(starts, sizes) if n >= 3]
        if not fans:
            continue
        triangles = np.concatenate(fans)
        normals = layer(geometry, 'LayerElementNormal', 'Normals', 'NormalsIndex', 3, corners)
        uvs = layer(geometry, 'LayerElementUV', 'UV', 'UVIndex', 2, corners)
        element = child(geometry, 'LayerElementMaterial')
        per_polygon = np.zeros(len(starts), int)
        if element is not None:
            fields = {c[0]: c[1][0] for c in element[2] if c[1]}
            ids = np.asarray(fields.get('Materials', [0]))
            per_polygon[:] = ids[0] if fields.get('MappingInformationType') == 'AllSame' or len(ids) != len(starts) else ids
        position = np.c_[points[corners], np.ones(len(corners))] @ matrix.T
        if normals is not None:
            normals = normals @ np.linalg.inv(matrix[:3, :3])
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
        if np.linalg.det(matrix[:3, :3]) < 0:
            triangles = triangles[:, ::-1]
        which = per_polygon[np.repeat(np.arange(len(starts)), sizes)[triangles[:, 0]]]
        for k in np.unique(which):
            pick = triangles[which == k]
            group = out.setdefault(materials[k] if k < len(materials) else '', ([], [], []))
            group[0].append(position[pick, :3])
            group[1].append(np.zeros((len(pick), 3, 3)) if normals is None else normals[pick])
            group[2].append(np.zeros((len(pick), 3, 2)) if uvs is None else uvs[pick])
    return {name: tuple(np.concatenate(parts).astype(np.float32) for parts in group) for name, group in out.items()}

"""Ascend PC LOD0 streams. GPU vertices already contain the reference pose."""
import numpy as np

from tools.tvg_package import geometry


def normals(raw, count, stride, offset):
    return np.ndarray((count, 3), np.uint8, buffer=raw, offset=offset,
                      strides=(stride, 1)).astype(np.float32)/127.5-1


def vertices(raw, count, stride, offset, width, dtype):
    return np.ndarray((count, width), dtype, buffer=raw, offset=offset,
                      strides=(stride, np.dtype(dtype).itemsize)).astype(np.float32)


def sections_geometry(p, points, uv, normal, indices, sections, materials, **kwargs):
    groups, used = [], []
    for material, first, faces in sections:
        if min(material, first, faces) < 0 or material >= len(materials) or first+faces*3 > len(indices):
            raise ValueError('Mesh section outside index/material array')
        if faces:
            groups.append(dict(start=len(used), count=faces*3, materialIndex=material))
            used.extend(indices[first:first+faces*3])
    result = geometry(points, uv, used, groups, [p.qualified(x) for x in materials], normals=normal, **kwargs)
    result['metadata'].update(engine='UE3', geometry_source='PC LOD0 render vertex/index buffers',
                              animation_playback=False)
    return result


def static_mesh(p, ref):
    props, r = p.properties(ref)
    r.take(28+4)  # bounds, body setup
    r.take(24)  # compressed kDOP root bounds, version >=770
    r.bulk_array(); r.bulk_array()  # collision nodes and triangles
    internal_version = r.i32()
    lods = r.count(16, 32)
    if not lods:
        raise ValueError('StaticMesh has no render LOD')
    p.bulk(r, load=False)  # uncooked raw triangle source
    materials, sections = [], []
    for _ in range(r.count(40, 4096)):
        mat, collision, old_collision, shadow, first, faces, minimum, maximum, slot = r.unpack('9i')
        r.take(r.count(8)*8)  # fragment ranges
        if r.unpack('B')[0]:
            raise ValueError('PS3 static mesh section is unsupported by this PC reader')
        sections.append((len(materials), first, faces)); materials.append(mat)
    stride, expected = r.unpack('2i')
    count, raw = r.bulk_array(12)
    if stride != 12 or expected != count:
        raise ValueError('Static position buffer count mismatch')
    points = np.frombuffer(raw, '<f4').reshape(-1, 3)
    nuv, stride, n, full = r.unpack('4i')
    if not 1 <= nuv <= 8 or full not in (0, 1) or n != count or stride != 8+nuv*(8 if full else 4):
        raise ValueError('Unsupported static tangent/UV buffer layout')
    un, raw = r.bulk_array(stride)
    if un != count:
        raise ValueError('Static UV buffer count mismatch')
    normal = normals(raw, n, stride, 4)
    uv = vertices(raw, n, stride, 8, 2, '<f4' if full else '<f2')
    color_stride, colors = r.unpack('2i')
    if colors:
        cn, _ = r.bulk_array(4)
        if color_stride != 4 or cn != colors or cn != count:
            raise ValueError('Static color buffer count mismatch')
    if r.i32() != count:
        raise ValueError('Static LOD vertex count mismatch')
    _, raw = r.bulk_array(2); indices = np.frombuffer(raw, '<u2').astype(int).tolist()
    result = sections_geometry(p, points, uv, normal, indices, sections, materials)
    result['metadata'].update(mesh_version=internal_version, available_lods=lods,
                              vertex_colors='authored colors present; lighting deferred' if colors else None)
    return result


def skeletal_mesh(p, ref):
    props, r = p.properties(ref)
    r.take(28)  # bounds
    materials = [v[0] for v in r.array('i')]
    origin, rotation = r.unpack('3f'), r.unpack('3i')
    bones = []
    for _ in range(r.count(52, 65536)):
        name = r.name(); flags = r.i32(); orientation = r.unpack('4f'); position = r.unpack('3f')
        children, parent, color = r.unpack('3i')
        bones.append(dict(name=name, parent=parent, position=position, orientation=orientation))
    r.take(4)  # skeleton depth
    lods = r.count(16, 32)
    if not lods:
        raise ValueError('SkeletalMesh has no render LOD')
    sections = []
    for _ in range(r.count(11, 4096)):
        mat, chunk, first, faces, sorting = r.unpack('HHiHB')
        sections.append((mat, first, faces))
    _, raw = r.bulk_array(2); indices = np.frombuffer(raw, '<u2').astype(int).tolist()
    r.array('H')  # active bone indices
    chunks = r.count(24, 65536)
    for _ in range(chunks):
        r.take(4)  # base vertex
        # Four float UV channels, vertex color, and rigid/soft influences.
        r.take(r.count(61)*61); r.take(r.count(68)*68)
        r.array('H'); r.take(12)  # bone map, rigid/soft counts, maximum influences
    size, expected = r.unpack('2i')
    r.take(r.count()*1)  # required bones
    p.bulk(r, element_size=2, load=False)  # raw point indices
    lod_uvs, nuv, full, packed = r.unpack('4i')
    r.take(24)  # packed-position extent/origin (PC stream uses full positions)
    if not 1 <= nuv <= 8 or lod_uvs != nuv or full not in (0, 1) or packed not in (0, 1):
        raise ValueError('Unsupported skeletal GPU buffer layout')
    # UE3 PC serialization uses float positions even when the asset's console
    # packing option is set. The bulk stride below validates the actual layout.
    stride = 28+nuv*(8 if full else 4)
    n, raw = r.bulk_array(stride)
    if n != expected:
        raise ValueError('Skeletal GPU vertex count mismatch')
    normal = normals(raw, n, stride, 4)
    points = vertices(raw, n, stride, 16, 3, '<f4')
    uv = vertices(raw, n, stride, 28, 2, '<f4' if full else '<f2')
    result = sections_geometry(p, points, uv, normal, indices, sections, materials,
                               origin=origin, rotation=rotation)
    result['metadata'].update(pose='reference', bones=bones, available_lods=lods, serialized_packed_position_flag=packed,
                              skinning='bind-pose positions; no animation or runtime skinning')
    return result


def mesh(p, ref):
    cls = p.entry(ref)['class']
    if cls == 'StaticMesh':
        return static_mesh(p, ref)
    if cls == 'SkeletalMesh':
        return skeletal_mesh(p, ref)
    raise ValueError('SpeedTree native tree blob requires procedural branch/leaf reconstruction; no static UE3 mesh stream')
